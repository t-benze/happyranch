"""Audit-log inspection endpoint.

Exposes a filtered view of the ``audit_log`` table so CLI / UI consumers never
need to poke at ``happyranch.db`` directly. Schema-coupled access is a liability —
this route is the one read path we want to keep stable.
"""
from __future__ import annotations

from fastapi import APIRouter

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep

router = APIRouter(dependencies=[require_token()])


@router.get("/audit")
def list_audit(
    slug: str,
    org: OrgDep,
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
    entries = org.db.query_audit_logs(
        task_id=task_id,
        agent=agent,
        action=action,
        since=since,
        limit=limit,
    )
    return {"entries": entries}
