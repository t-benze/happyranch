"""GET /api/v1/orgs/{slug}/dashboard/summary — founder dashboard rollup.

Cache-only: reads the per-org durable last-known-good projection refreshed
every 10s by a coalesced asyncio scheduler. Never calls compose_dashboard_summary
synchronously. Cold-start / no-projection-yet returns 503.

Spec: protocol/05e-dashboard.md
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from runtime.daemon.auth import require_token
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes._org_dep import OrgDep
from runtime.orchestrator.dashboard_summary import DashboardSummaryResponse

router = APIRouter(dependencies=[require_token()])


@router.get("/dashboard/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(slug: str, org: OrgDep) -> DashboardSummaryResponse:
    """Return the cached dashboard rollup for the given org.

    Cache-only — reads the per-org projection, never composes synchronously.
    Returns 503 when no projection exists yet (cold-start before first refresh).
    """
    projection = org.dashboard_projection.get_projection()
    if projection is None:
        raise HTTPException(
            status_code=503,
            detail="Dashboard summary is not yet available. "
                   "Please retry in a few seconds.",
        )
    now = datetime.now(timezone.utc)
    payload = projection.payload.copy()
    payload["server_now"] = now.isoformat()
    payload["generated_at"] = projection.generated_at.isoformat()
    return DashboardSummaryResponse.model_validate(payload)
