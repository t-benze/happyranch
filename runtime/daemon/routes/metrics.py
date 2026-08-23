"""Operational metrics endpoint (THR-066).

GET /api/v1/metrics — bearer-authed JSON snapshot of daemon runtime health.
Pull-gauges (tasks, jobs, sessions, queue depth) are computed at request
time from live state, never stored in the registry.

GET /api/v1/metrics/history — bearer-authed query over persisted metrics
snapshot rows (metrics_snapshots table), newest-first.

POST /api/v1/metrics/maintenance — bearer-authed, founder-explicit daemon
maintenance operation (prune + WAL checkpoint + integrity check + VACUUM).
Requires explicit confirmation, then atomically enters a maintenance
admission/drain/exclusivity gate: new normal traffic is rejected, in-flight
requests drain, quiescence is re-checked, and only then does the blocking
operation run.  A second concurrent call is deterministically rejected.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, StrictBool

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

# Fixed path of the maintenance endpoint.  The HTTP admission middleware uses
# this to exempt the controlling operation from rejection.
MAINTENANCE_ROUTE_PATH = "/api/v1/metrics/maintenance"

# Bound on how long the maintenance gate waits for already-admitted requests
# to drain before it fails with a structured ``drain_timeout`` (never wedge).
_MAINTENANCE_DRAIN_TIMEOUT_SECONDS = 60.0

router = APIRouter(dependencies=[require_token()])


class MetricsMaintenanceRequest(BaseModel):
    """Request body for the explicit quiescent metrics maintenance operation.

    ``confirm_quiescent`` must be exactly the JSON literal ``true`` (strict
    bool).  Absent/false is refused; coercible values (strings such as
    ``"yes"``/``"true"``, numeric ``1``/``0``, ``"on"``, …) are rejected with
    HTTP 422 before any admission or maintenance work.
    """

    confirm_quiescent: StrictBool = False


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

    Refuses (HTTP 409) without explicit confirmation.  Then atomically enters
    the maintenance gate (a concurrent call is deterministically rejected with
    HTTP 409 ``maintenance_in_progress``), drains already-admitted requests
    (HTTP 503 ``drain_timeout`` on a bounded timeout), and re-checks quiescence
    (HTTP 409 ``not_quiescent`` while any nonterminal task / running job /
    active executor session exists).  Only then — and only through
    ``MetricsStore`` — it prunes at the unchanged 30-day cutoff, WAL
    checkpoints, runs ``PRAGMA integrity_check`` (fail-closed), and ``VACUUM``s,
    returning a deterministic before/after report.

    The gate is released on every success/failure path.  A failure returns
    HTTP 500 ``maintenance_failed`` with a stable bounded ``code``/``reason``
    and history still queryable; the original exception is logged
    server-side only and never echoed to the client (no raw SQLite/integrity
    text, paths, IDs, or snapshot content).  A fresh explicit invocation is
    required (no automatic retry, no false physical-reclaim claim).
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

    gate = state.maintenance_gate

    # Atomic admission: only one maintenance call wins.  A second call while
    # pending/active is deterministically rejected here.
    if not gate.try_enter_pending():
        raise HTTPException(
            status_code=409,
            detail={
                "code": "maintenance_in_progress",
                "detail": (
                    "another metrics maintenance operation is already in "
                    "progress; retry after it completes."
                ),
            },
        )

    try:
        # Drain already-admitted normal requests (bounded).
        if not gate.drain(timeout=_MAINTENANCE_DRAIN_TIMEOUT_SECONDS):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "drain_timeout",
                    "detail": (
                        "timed out waiting for in-flight requests to drain; "
                        "no maintenance was performed. Retry once the daemon "
                        "is quieter."
                    ),
                },
            )

        # Re-check quiescence immediately before the operation.
        quiescence = daemon_is_quiescent(state)
        if not quiescence["quiescent"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "not_quiescent",
                    "detail": (
                        "metrics maintenance requires a quiescent daemon "
                        "(no nonterminal tasks, running jobs, or active "
                        "executor sessions)."
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

        gate.mark_active()

        cutoff = (datetime.now(timezone.utc) - timedelta(days=_RETENTION_DAYS)).isoformat()
        return state.metrics_store.maintenance(cutoff)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — logged server-side, never leaked
        logger.exception(
            "metrics maintenance failed; pre-existing history remains queryable — "
            "recovery requires a fresh explicit invocation (no automatic retry)."
        )
        raise HTTPException(
            status_code=500,
            detail={
                "code": "maintenance_failed",
                "reason": "maintenance_did_not_complete",
                "detail": (
                    "Metrics maintenance did not complete. Pre-existing history "
                    "remains queryable. Recovery requires a fresh explicit "
                    "invocation (no automatic retry)."
                ),
                "recovery": (
                    "Maintenance did not complete. History remains queryable; "
                    "re-run the maintenance operation with a fresh explicit invocation."
                ),
            },
        ) from exc
    finally:
        gate.release()
