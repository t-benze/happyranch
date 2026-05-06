"""GET /api/v1/orgs/{slug}/tokens — per-session token usage and rollups.

Single read endpoint for the ``session_token_usage`` table. With no
``group_by`` it returns one row per (task, agent, session); with
``group_by=agent`` or ``group_by=task`` it returns a rollup keyed by that
column. See spec: docs/superpowers/specs/2026-05-05-token-usage-tracking-design.md §3.2.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.daemon.auth import require_token
from src.daemon.routes._org_dep import OrgDep

router = APIRouter(dependencies=[require_token()])


@router.get("/tokens")
def list_tokens(
    slug: str,
    org: OrgDep,
    task_id: str | None = None,
    agent: str | None = None,
    since: str | None = None,
    limit: int | None = None,
    group_by: str | None = None,
) -> dict:
    """Return per-session rows or an aggregated rollup.

    Filters AND-compose. ``since`` is an ISO-8601 timestamp matched against
    ``created_at``. ``limit`` only applies to the per-session listing.
    ``group_by`` accepts ``"agent"`` or ``"task"``; any other non-null value
    yields 400.
    """
    if group_by is not None and group_by not in ("agent", "task"):
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_group_by", "value": group_by},
        )

    if group_by == "agent":
        rollup = org.db.aggregate_session_token_usage_by_agent(
            since=since, task_id=task_id,
        )
        return {"rollup": rollup}
    if group_by == "task":
        rollup = org.db.aggregate_session_token_usage_by_task(
            since=since, agent=agent,
        )
        return {"rollup": rollup}

    rows = org.db.list_session_token_usage(
        task_id=task_id, agent=agent, since=since, limit=limit,
    )
    return {"rows": rows}
