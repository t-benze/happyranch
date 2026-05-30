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
