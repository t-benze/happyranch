"""Operational metrics endpoint (THR-066).

GET /api/v1/metrics — bearer-authed JSON snapshot of daemon runtime health.
Pull-gauges (tasks, jobs, sessions, queue depth) are computed at request
time from live state, never stored in the registry.

GET /api/v1/metrics/history — bearer-authed query over persisted metrics
snapshot rows (metrics_snapshots table), newest-first.

POST /api/v1/metrics/maintenance — bearer-authed, founder-explicit daemon
maintenance operation (prune + WAL checkpoint + integrity check + VACUUM).
Refuses absent confirmation and daemon-observed non-quiescence.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from runtime.daemon.auth import require_token
from runtime.daemon.metrics_store import (
    _RETENTION_DAYS,
    compose_metrics_snapshot,
    daemon_is_quiescent,
)
from runtime.daemon.state import DaemonState

logger = logging.getLogger(__name__)

_MAX_LIMIT = 5000
_DEFAULT_LIMIT = 500

router = APIRouter(dependencies=[require_token()])


class MetricsMaintenanceRequest(BaseModel):
    """Request body for the explicit quiescent metrics maintenance operation.

    ``confirm_quiescent`` must be explicitly ``true``; absent/false is refused.
    """

    confirm_quiescent: bool = False


@router.get("/metrics")
def metrics(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    return compose_metrics_snapshot(state)


@router.get("/metrics/history")
def metrics_history(
    request: Request,
    since: str | None = Query(None, description="ISO-8601 lower bound (inclusive)"),
    until: str | None = Query(None, description="ISO-8601 upper bound (inclusive)"),
    limit: int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT, description="Max rows to return"),
) -> dict:
    state: DaemonState = request.app.state.daemon
    if state.metrics_store is None:
        return {"snapshots": []}
    rows = state.metrics_store.query(since=since, until=until, limit=limit)
    return {"snapshots": rows}


@router.post("/metrics/maintenance")
def metrics_maintenance(request: Request, body: MetricsMaintenanceRequest) -> dict:
    """Run the daemon-owned metrics maintenance sequence (explicit + quiescent).

    Refuses (HTTP 409) without explicit confirmation, and refuses while any
    nonterminal task, running job, or active executor session exists.  On
    confirmed quiescence, executes only through ``MetricsStore`` (prune,
    WAL checkpoint, integrity check, VACUUM) and returns the deterministic
    before/after report.  A failure is surfaced (HTTP 500) with recovery
    guidance; history remains queryable and a fresh explicit invocation is
    required.
    """
    state: DaemonState = request.app.state.daemon

    if not body.confirm_quiescent:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "confirmation_required",
                "detail": (
                    "metrics maintenance is destructive and requires explicit "
                    "confirmation: send confirm_quiescent=true (CLI: "
                    "`happyranch metrics maintenance --confirm-quiescent`)."
                ),
            },
        )

    quiescence = daemon_is_quiescent(state)
    if not quiescence["quiescent"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "not_quiescent",
                "detail": (
                    "metrics maintenance requires a quiescent daemon "
                    "(no nonterminal tasks, running jobs, or active executor sessions)."
                ),
                "nonterminal_tasks": quiescence["nonterminal_tasks"],
                "running_jobs": quiescence["running_jobs"],
                "active_executor_sessions": quiescence["active_executor_sessions"],
            },
        )

    if state.metrics_store is None:
        raise HTTPException(
            status_code=409,
            detail={"code": "no_metrics_store", "detail": "no metrics store is available."},
        )

    cutoff = (datetime.now(timezone.utc) - timedelta(days=_RETENTION_DAYS)).isoformat()
    try:
        return state.metrics_store.maintenance(cutoff)
    except Exception as exc:  # noqa: BLE001 — surfaced, never concealed
        logger.exception(
            "metrics maintenance failed; pre-existing history remains queryable — "
            "recovery requires a fresh explicit invocation (no automatic retry)."
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "maintenance_failed",
                "detail": str(exc),
                "recovery": (
                    "Maintenance did not complete. History remains queryable; "
                    "re-run the maintenance operation with a fresh explicit invocation."
                ),
            },
        )
