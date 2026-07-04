"""Operational metrics endpoint (THR-066).

GET /api/v1/metrics — bearer-authed JSON snapshot of daemon runtime health.
Pull-gauges (tasks, jobs, sessions, queue depth) are computed at request
time from live state, never stored in the registry.

GET /api/v1/metrics/history — bearer-authed query over persisted metrics
snapshot rows (metrics_snapshots table), newest-first.
"""
from __future__ import annotations

from fastapi import APIRouter, Query, Request

from runtime.daemon.auth import require_token
from runtime.daemon.metrics_store import compose_metrics_snapshot
from runtime.daemon.state import DaemonState

_MAX_LIMIT = 5000
_DEFAULT_LIMIT = 500

router = APIRouter(dependencies=[require_token()])


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
