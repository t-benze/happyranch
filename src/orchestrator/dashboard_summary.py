"""Aggregations powering GET /api/v1/orgs/{slug}/dashboard/summary.

Pure functions over Database + KbStore. Each function takes an explicit
`now` clock so tests are deterministic.

Spec: docs/superpowers/specs/2026-05-30-dashboard-overhaul-design.md
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel


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
