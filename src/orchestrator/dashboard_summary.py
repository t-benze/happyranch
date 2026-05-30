"""Aggregations powering GET /api/v1/orgs/{slug}/dashboard/summary.

Pure functions over Database + KbStore. Each function takes an explicit
`now` clock so tests are deterministic.

Spec: docs/superpowers/specs/2026-05-30-dashboard-overhaul-design.md
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel

from src.infrastructure.database import Database


class HeartbeatBucket(BaseModel):
    hour: int
    steps: int
    tier: Literal["ok", "warn", "bad"]


class NarrativeCounts(BaseModel):
    completed_today: int
    failed_today: int
    escalated_open: int
    kb_added_today: int
    agents_active_now: int
    spend_today_usd: float


class EscalationRow(BaseModel):
    task_id: str
    agent: str
    team: str
    question: str
    raised_at: datetime
    age_seconds: int


class ActiveByTeam(BaseModel):
    team: str
    count: int
    task_ids: list[str]


class ActivityRow(BaseModel):
    timestamp: datetime
    who: str
    event_kind: str
    task_id: str | None
    verdict: Literal["ok", "fail", "warn"] | None


class UpdateRow(BaseModel):
    marker: Literal["add", "warn", "info"]
    text: str
    meta: str
    timestamp: datetime


class TeamPulse(BaseModel):
    team: str
    acceptance_pct: int
    trend_delta: int
    sparkline: list[float]
    members: int
    lead: str


class DashboardSummaryResponse(BaseModel):
    heartbeat: list[HeartbeatBucket]
    narrative_counts: NarrativeCounts
    escalations: list[EscalationRow]
    active_by_team: list[ActiveByTeam]
    recent_activity: list[ActivityRow]
    updates_this_week: list[UpdateRow]
    org_pulse: list[TeamPulse]
    org_age_days: int
    server_now: datetime


def compute_org_age_days(db: Database, *, now: datetime | None = None) -> int:
    """Days between the earliest audit_log row and `now`. Empty DB → 0."""
    row = db.fetch_one_readonly("SELECT MIN(timestamp) AS first_ts FROM audit_log")
    if not row or not row["first_ts"]:
        return 0
    first = datetime.fromisoformat(row["first_ts"])
    moment = now or datetime.now(timezone.utc)
    if first.tzinfo is None:
        first = first.replace(tzinfo=timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0, (moment - first).days)


def _local_midnight(now: datetime) -> datetime:
    """Truncate `now` to local midnight (UTC for our deterministic clock)."""
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def compute_spend_today(db: Database, *, now: datetime) -> float:
    """Sum estimated USD spend recorded since local midnight.

    The real schema stores per-session cost on ``task_results.estimated_cost``
    (``session_token_usage`` records token counts only). The spec's pseudocode
    references a ``token_usage.cost_usd`` column that does not exist; this
    function implements the intent against the actual table.
    """
    midnight = _local_midnight(now).isoformat()
    row = db.fetch_one_readonly(
        "SELECT COALESCE(SUM(estimated_cost), 0) AS total "
        "FROM task_results WHERE created_at >= ?",
        (midnight,),
    )
    return float(row["total"]) if row else 0.0


def compute_narrative_counts_today(
    db: Database, *, now: datetime, kb_store
) -> NarrativeCounts:
    """Today = local midnight to now. Aggregates over tasks + audit_log +
    task_results + the KB store."""
    midnight = _local_midnight(now).isoformat()

    completed_row = db.fetch_one_readonly(
        "SELECT COUNT(*) AS n FROM tasks "
        "WHERE status = 'completed' AND updated_at >= ?",
        (midnight,),
    )
    completed = int(completed_row["n"]) if completed_row else 0

    failed_row = db.fetch_one_readonly(
        "SELECT COUNT(*) AS n FROM tasks "
        "WHERE status = 'failed' AND updated_at >= ?",
        (midnight,),
    )
    failed = int(failed_row["n"]) if failed_row else 0

    escalated_row = db.fetch_one_readonly(
        "SELECT COUNT(*) AS n FROM tasks "
        "WHERE status = 'blocked' AND block_kind = 'escalated'",
    )
    escalated = int(escalated_row["n"]) if escalated_row else 0

    # Distinct agents with an unmatched session_start
    active_rows = db.fetch_all_readonly(
        "SELECT DISTINCT a.agent FROM audit_log a "
        "WHERE a.action = 'session_start' "
        "AND NOT EXISTS ("
        "  SELECT 1 FROM audit_log b "
        "  WHERE b.task_id = a.task_id AND b.agent = a.agent "
        "  AND b.action = 'session_end' AND b.timestamp > a.timestamp"
        ")"
    )
    active_now = len(active_rows)

    return NarrativeCounts(
        completed_today=completed,
        failed_today=failed,
        escalated_open=escalated,
        kb_added_today=kb_store.count_entries_created_since(_local_midnight(now)),
        agents_active_now=active_now,
        spend_today_usd=compute_spend_today(db, now=now),
    )


def _tier_for_ratio(failed: int, total: int) -> Literal["ok", "warn", "bad"]:
    """Map failed:total ratio to a tier. < 10% = ok, < 30% = warn, else = bad."""
    if total == 0 or failed == 0:
        return "ok"
    ratio = failed / total
    if ratio < 0.10:
        return "ok"
    if ratio < 0.30:
        return "warn"
    return "bad"


def compute_heartbeat_24h(db: Database, *, now: datetime) -> list[HeartbeatBucket]:
    """24 hourly buckets ending at `now`. Steps = session_start +
    completion_report rows. Tier = ok/warn/bad by failed:total ratio."""
    window_start = (now - timedelta(hours=24)).isoformat()
    rows = db.fetch_all_readonly(
        "SELECT timestamp, action, payload FROM audit_log "
        "WHERE timestamp >= ? AND action IN ('session_start', 'completion_report') "
        "LIMIT 50000",
        (window_start,),
    )
    import json
    steps_by_hour: dict[int, int] = {h: 0 for h in range(24)}
    failed_by_hour: dict[int, int] = {h: 0 for h in range(24)}
    completion_by_hour: dict[int, int] = {h: 0 for h in range(24)}
    for r in rows:
        ts = datetime.fromisoformat(r["timestamp"])
        h = ts.hour
        steps_by_hour[h] += 1
        if r["action"] == "completion_report":
            completion_by_hour[h] += 1
            payload = json.loads(r["payload"] or "{}")
            if payload.get("status") == "failed":
                failed_by_hour[h] += 1
    return [
        HeartbeatBucket(
            hour=h,
            steps=steps_by_hour[h],
            tier=_tier_for_ratio(failed_by_hour[h], completion_by_hour[h]),
        )
        for h in range(24)
    ]
