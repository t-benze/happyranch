"""Audit-log inspection endpoint.

Exposes a filtered view of the ``audit_log`` table so CLI / UI consumers never
need to poke at ``opc.db`` directly. Schema-coupled access is a liability —
this route is the one read path we want to keep stable.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from src.daemon.auth import require_token
from src.daemon.state import DaemonState

router = APIRouter(dependencies=[require_token()])


def _require_active(state: DaemonState) -> None:
    if state.is_idle:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_active_runtime"},
        )


@router.get("/audit")
def list_audit(
    request: Request,
    task_id: str | None = None,
    agent: str | None = None,
    action: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> dict:
    """Return filtered audit entries.

    All filters AND-compose. ``since`` is an ISO-8601 timestamp. ``limit``
    caps to the most recent N entries (chronological order preserved).
    """
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    entries = state.db.query_audit_logs(
        task_id=task_id,
        agent=agent,
        action=action,
        since=since,
        limit=limit,
    )
    return {"entries": entries}
