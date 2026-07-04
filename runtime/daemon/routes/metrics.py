"""Operational metrics endpoint (THR-066).

GET /api/v1/metrics — bearer-authed JSON snapshot of daemon runtime health.
Pull-gauges (tasks, jobs, sessions, queue depth) are computed at request
time from live state, never stored in the registry.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from runtime.daemon.auth import require_token
from runtime.daemon.metrics_store import compose_metrics_snapshot
from runtime.daemon.state import DaemonState

router = APIRouter(dependencies=[require_token()])


@router.get("/metrics")
def metrics(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    return compose_metrics_snapshot(state)
