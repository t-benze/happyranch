"""Operational metrics endpoint (THR-066).

GET /api/v1/metrics — bearer-authed JSON snapshot of daemon runtime health.
Pull-gauges (tasks, jobs, sessions, queue depth) are computed at request
time from live state, never stored in the registry.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from runtime.daemon.auth import require_token
from runtime.daemon.state import DaemonState

router = APIRouter(dependencies=[require_token()])


@router.get("/metrics")
def metrics(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon

    snap = state.metrics_registry.snapshot()

    # Pull-gauges: aggregated across all loaded orgs.
    task_count = 0
    job_count = 0
    session_count = 0
    for org in state.orgs.values():
        task_count += len(org.db.get_nonterminal_task_ids())
        job_count += len(org.db.list_jobs_db(status="running"))
        session_count += org.sessions.count_active()

    snap["tasks"] = {"pending_and_in_flight": task_count}
    snap["jobs_in_flight"] = job_count
    snap["executor_sessions_active"] = session_count
    snap["run_step_queue_depth"] = state.queue._queue.qsize()

    return snap
