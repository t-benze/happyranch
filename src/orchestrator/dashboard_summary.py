"""Aggregations powering GET /api/v1/orgs/{slug}/dashboard/summary.

Pure functions over Database + KbStore. Each function takes an explicit
`now` clock so tests are deterministic.

Spec: docs/superpowers/specs/2026-05-30-dashboard-overhaul-design.md
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel

from src.infrastructure.database import Database


class HeartbeatBucket(BaseModel):
    hour: int
    steps: int
    failed: int
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
            failed=failed_by_hour[h],
            tier=_tier_for_ratio(failed_by_hour[h], completion_by_hour[h]),
        )
        for h in range(24)
    ]


_RECENT_ACTIVITY_KINDS = (
    "session_start", "completion_report", "review_verdict",
    "escalation", "escalation_resolved", "task_dispatched",
    "talk_started", "talk_ended", "learning_promoted",
)


def _verdict_from_payload(
    action: str, payload_json: str | None
) -> Literal["ok", "fail", "warn"] | None:
    """Extract a normalized verdict from an audit row's payload."""
    payload = json.loads(payload_json or "{}")
    if action == "completion_report":
        status = payload.get("status")
        if status == "completed":
            return "ok"
        if status == "failed":
            return "fail"
        if status == "blocked":
            return "warn"
    if action == "review_verdict":
        v = payload.get("verdict")
        if v in ("approved", "accept", "ok"):
            return "ok"
        if v in ("request_changes", "rejected"):
            return "fail"
    return None


def compute_recent_activity(db: Database, *, n: int = 6) -> list[ActivityRow]:
    """Last n audit rows of meaningful kinds, DESC by timestamp."""
    placeholders = ",".join("?" * len(_RECENT_ACTIVITY_KINDS))
    rows = db.fetch_all_readonly(
        f"SELECT timestamp, task_id, agent, action, payload "
        f"FROM audit_log WHERE action IN ({placeholders}) "
        f"ORDER BY timestamp DESC LIMIT ?",
        (*_RECENT_ACTIVITY_KINDS, n),
    )
    return [
        ActivityRow(
            timestamp=datetime.fromisoformat(r["timestamp"]),
            who=r["agent"],
            event_kind=r["action"],
            task_id=r["task_id"],
            verdict=_verdict_from_payload(r["action"], r["payload"]),
        )
        for r in rows
    ]


def compute_updates_this_week(
    db: Database, *, now: datetime, kb_store, n: int = 12
) -> list[UpdateRow]:
    """Combined feed for the last 7 days: KB entries created + learnings promoted.

    Sources (both honest per the design audit):
      - KB store filesystem (entries with created_at within 7d)
      - audit_log rows with action='learning_promoted' within 7d

    No tier-transition row type — the daemon doesn't audit tier changes yet.
    """
    week_start = now - timedelta(days=7)
    items: list[UpdateRow] = []

    # KB entries created this week (from the KB store, not audit_log).
    # Exclude operational artifact types that agents write for task-state
    # persistence — they are not knowledge contributions.
    _OPERATIONAL_KB_TYPES = {"driver-state", "state"}
    for entry in kb_store.list_entries_created_since(week_start):
        if entry.get("type") in _OPERATIONAL_KB_TYPES:
            continue
        ts = datetime.fromisoformat(entry["created_at"])
        items.append(UpdateRow(
            marker="add",
            text="KB +1",
            meta=entry["slug"],
            timestamp=ts,
        ))

    # Learnings promoted to KB this week
    rows = db.fetch_all_readonly(
        "SELECT timestamp, payload FROM audit_log "
        "WHERE action = 'learning_promoted' AND timestamp >= ? "
        "ORDER BY timestamp DESC",
        (week_start.isoformat(),),
    )
    for r in rows:
        payload = json.loads(r["payload"] or "{}")
        items.append(UpdateRow(
            marker="info",
            text="Learning promoted to KB",
            meta=payload.get("kb_slug", ""),
            timestamp=datetime.fromisoformat(r["timestamp"]),
        ))

    items.sort(key=lambda x: x.timestamp, reverse=True)
    return items[:n]


def compute_escalations_open(db: Database, *, now: datetime) -> list[EscalationRow]:
    """Currently-escalated tasks with the question text from audit payload."""
    rows = db.fetch_all_readonly(
        "SELECT t.id, t.assigned_agent, t.team, t.updated_at, "
        "       a.payload AS escalation_payload, a.timestamp AS escalation_ts "
        "FROM tasks t "
        "LEFT JOIN audit_log a ON a.task_id = t.id AND a.action = 'escalation' "
        "WHERE t.status = 'blocked' AND t.block_kind = 'escalated' "
        "ORDER BY t.updated_at DESC"
    )
    result: list[EscalationRow] = []
    for r in rows:
        payload = json.loads(r["escalation_payload"] or "{}")
        question = payload.get("reason") or payload.get("question") or ""
        raised = datetime.fromisoformat(r["escalation_ts"] or r["updated_at"])
        if raised.tzinfo is None:
            raised = raised.replace(tzinfo=timezone.utc)
        moment = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        age = int((moment - raised).total_seconds())
        result.append(EscalationRow(
            task_id=r["id"],
            agent=r["assigned_agent"],
            team=r["team"],
            question=question,
            raised_at=raised,
            age_seconds=max(0, age),
        ))
    return result


def compute_active_by_team(db: Database) -> list[ActiveByTeam]:
    """tasks with status='in_progress' grouped by team."""
    rows = db.fetch_all_readonly(
        "SELECT team, id FROM tasks WHERE status = 'in_progress' "
        "ORDER BY team, updated_at DESC"
    )
    groups: dict[str, list[str]] = {}
    for r in rows:
        groups.setdefault(r["team"], []).append(r["id"])
    return [
        ActiveByTeam(team=team, count=len(ids), task_ids=ids[:10])
        for team, ids in sorted(groups.items())
    ]


def _acceptance_pct_for_window(
    db: Database, *, team: str, start: datetime, end: datetime,
) -> tuple[int, int]:
    """Return (approved_count, total_count) of review_verdict rows in window
    where the reviewed task belonged to `team`."""
    rows = db.fetch_all_readonly(
        "SELECT a.payload FROM audit_log a "
        "JOIN tasks t ON t.id = a.task_id "
        "WHERE a.action = 'review_verdict' "
        "  AND t.team = ? "
        "  AND a.timestamp >= ? AND a.timestamp < ?",
        (team, start.isoformat(), end.isoformat()),
    )
    approved = 0
    total = len(rows)
    for r in rows:
        payload = json.loads(r["payload"] or "{}")
        v = payload.get("verdict")
        if v in ("approved", "accept", "ok"):
            approved += 1
    return approved, total


def compute_org_pulse_7d(db: Database, *, now: datetime, teams) -> list[TeamPulse]:
    """Per-team 7d acceptance + trend + 12-week sparkline.

    `teams` duck-types TeamsRegistry: needs `.teams() -> list[str]` and
    `.manager_for_team(team) -> TeamManager(name, team, workers)`.
    """
    week_now_start = now - timedelta(days=7)
    week_prior_start = now - timedelta(days=14)

    result: list[TeamPulse] = []
    for team_name in teams.teams():
        mgr = teams.manager_for_team(team_name)
        # Current 7d
        approved_now, total_now = _acceptance_pct_for_window(
            db, team=team_name, start=week_now_start, end=now,
        )
        # Prior 7d (for trend delta)
        approved_prior, total_prior = _acceptance_pct_for_window(
            db, team=team_name, start=week_prior_start, end=week_now_start,
        )
        acceptance = int(round(100 * approved_now / total_now)) if total_now else 0
        prior_acc = int(round(100 * approved_prior / total_prior)) if total_prior else 0
        trend_delta = acceptance - prior_acc

        # 12-week sparkline (oldest first), each value in [0, 1]
        sparkline: list[float] = []
        for i in range(12, 0, -1):
            w_start = now - timedelta(days=i * 7)
            w_end = now - timedelta(days=(i - 1) * 7)
            approved, total = _acceptance_pct_for_window(
                db, team=team_name, start=w_start, end=w_end,
            )
            sparkline.append(approved / total if total else 0.0)

        result.append(TeamPulse(
            team=team_name,
            acceptance_pct=acceptance,
            trend_delta=trend_delta,
            sparkline=sparkline,
            members=len(mgr.workers),
            lead=mgr.name,
        ))
    return result


def compose_dashboard_summary(
    *, db: Database, kb_store, teams, now: datetime,
) -> DashboardSummaryResponse:
    """Top-level: run every aggregation, return the wire-shape response."""
    return DashboardSummaryResponse(
        heartbeat=compute_heartbeat_24h(db, now=now),
        narrative_counts=compute_narrative_counts_today(db, now=now, kb_store=kb_store),
        escalations=compute_escalations_open(db, now=now),
        active_by_team=compute_active_by_team(db),
        recent_activity=compute_recent_activity(db, n=6),
        updates_this_week=compute_updates_this_week(db, now=now, kb_store=kb_store, n=12),
        org_pulse=compute_org_pulse_7d(db, now=now, teams=teams),
        org_age_days=compute_org_age_days(db, now=now),
        server_now=now,
    )
