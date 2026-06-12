"""GET /api/v1/orgs/{slug}/tokens — scoped token usage and rollups.

Single read endpoint for the ``session_token_usage`` table. Task rows remain
task-shaped for compatibility. Direct thread invocations use
``scope_type=thread``; talk lifecycle routes do not run executors, so talk usage
currently appears only through talk-dispatched task rows with ``talk_id``.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep

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
    scope_type: str | None = None,
    scope_id: str | None = None,
    thread_id: str | None = None,
    talk_id: str | None = None,
    purpose: str | None = None,
) -> dict:
    """Return scoped per-session rows or an aggregated rollup.

    Filters AND-compose. ``since`` is an ISO-8601 timestamp matched against
    ``created_at``. ``limit`` only applies to the per-session listing.
    ``group_by`` accepts ``agent``, ``task``, ``failed_task``, ``scope``,
    ``thread``, or ``talk``. ``failed_task`` rolls up per-(task, agent) token
    burn for tasks in the terminal ``failed`` status only (read-only JOIN to
    the tasks table). Talk lifecycle APIs are executor-free; ``talk`` rollups
    show talk-attributed task rows today and future direct talk executor rows.
    """
    valid_groups = ("agent", "task", "failed_task", "scope", "thread", "talk")
    if group_by is not None and group_by not in valid_groups:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_group_by", "value": group_by},
        )
    filters = dict(
        since=since,
        task_id=task_id,
        agent=agent,
        scope_type=scope_type,
        scope_id=scope_id,
        thread_id=thread_id,
        talk_id=talk_id,
        purpose=purpose,
    )

    if group_by == "agent":
        rollup = org.db.aggregate_session_token_usage_by_agent(
            **filters,
        )
        return {"rollup": rollup}
    if group_by == "task":
        rollup = org.db.aggregate_session_token_usage_by_task(
            **filters,
        )
        return {"rollup": rollup}
    if group_by == "failed_task":
        rollup = org.db.aggregate_session_token_usage_by_failed_task(
            **filters,
        )
        return {"rollup": rollup}
    if group_by == "scope":
        rollup = org.db.aggregate_session_token_usage_by_scope(**filters)
        return {"rollup": rollup}
    if group_by == "thread":
        rollup = org.db.aggregate_session_token_usage_by_thread(**filters)
        return {"rollup": rollup}
    if group_by == "talk":
        rollup = org.db.aggregate_session_token_usage_by_talk(**filters)
        return {"rollup": rollup}

    rows = org.db.list_session_token_usage(
        limit=limit,
        **filters,
    )
    return {"rows": rows}
