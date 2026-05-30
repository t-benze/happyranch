"""GET /api/v1/orgs/{slug}/dashboard/summary — founder dashboard rollup.

Single read endpoint aggregating heartbeat, narrative counts, escalations,
active-by-team, recent activity, updates-this-week, and org pulse.

Spec: docs/superpowers/specs/2026-05-30-dashboard-overhaul-design.md
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter

from src.daemon.auth import require_token
from src.daemon.org_state import OrgState
from src.daemon.routes._org_dep import OrgDep
from src.infrastructure.kb_store import KBStore
from src.orchestrator.dashboard_summary import (
    DashboardSummaryResponse,
    compose_dashboard_summary,
)

router = APIRouter(dependencies=[require_token()])


def _kb_store(org: OrgState) -> KBStore:
    return KBStore(org.root / "kb")


@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(slug: str, org: OrgDep) -> DashboardSummaryResponse:
    """Return the full dashboard rollup for the given org."""
    now = datetime.now(timezone.utc)
    return compose_dashboard_summary(
        db=org.db,
        kb_store=_kb_store(org),
        teams=org.teams,
        now=now,
    )
