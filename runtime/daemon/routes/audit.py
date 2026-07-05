"""Audit-log inspection endpoint.

Exposes a filtered view of the ``audit_log`` table so CLI / UI consumers never
need to poke at ``happyranch.db`` directly. Schema-coupled access is a liability —
this route is the one read path we want to keep stable.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.infrastructure.database import _decode_cursor

router = APIRouter(dependencies=[require_token()])

# Scope prefixes carried in audit_log.task_id (load-bearing invariant — do NOT
# reinterpret). Used by the enrichment path to cross-reference the threads table
# for dream-origin markers (A4, §4.12 PRD final).
_THREAD_SCOPE_PREFIX = "THR-"


def _enrich_thread_dream_origin(entries: list[dict], db) -> list[dict]:
    """Batch-resolve ``composed_from_dream_id`` for audit entries whose
    ``task_id`` is a thread ID (THR-*).

    This is a DERIVE read enrichment — zero schema change, zero new store.
    Entries with no thread-scope task_id pass through unchanged.
    """
    thread_ids = {
        e["task_id"]
        for e in entries
        if e.get("task_id") and str(e["task_id"]).startswith(_THREAD_SCOPE_PREFIX)
    }
    if not thread_ids:
        return entries

    placeholders = ",".join("?" for _ in thread_ids)
    rows = db._conn.execute(
        f"SELECT id, composed_from_dream_id FROM threads WHERE id IN ({placeholders})",
        tuple(thread_ids),
    ).fetchall()
    dream_map: dict[str, str | None] = {}
    for row in rows:
        dream_map[row["id"]] = (
            row["composed_from_dream_id"]
            if "composed_from_dream_id" in row.keys()
            else None
        )

    for e in entries:
        tid = e.get("task_id")
        if tid and str(tid) in dream_map and dream_map[str(tid)]:
            e["_thread_dream_id"] = dream_map[str(tid)]

    return entries


@router.get("/audit")
def list_audit(
    slug: str,
    org: OrgDep,
    task_id: str | None = None,
    agent: str | None = None,
    action: str | None = None,
    since: str | None = None,
    limit: int | None = None,
    cursor: str | None = None,
    include_thread_origin: bool = Query(False),
) -> dict:
    """Return filtered audit entries with optional keyset cursor pagination.

    All filters AND-compose. ``since`` is an ISO-8601 timestamp. ``limit``
    caps to the most recent N entries (chronological order preserved).

    ``cursor`` is an opaque string from a prior response's ``next_cursor``.
    Pass it to fetch the next older page (keyset pagination — stable under
    concurrent inserts). ``next_cursor`` is ``null`` when the result set is
    exhausted.

    When ``include_thread_origin`` is true, Thread-scoped entries (task_id
    starting with ``THR-``) are enriched with ``_thread_dream_id`` from the
    threads table. This is a DERIVE read enrichment — no schema change.
    """
    # Validate cursor at the HTTP layer so malformed cursors get 422, not 500.
    if cursor is not None:
        try:
            _decode_cursor(cursor)
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid cursor")

    entries, next_cursor = org.db.query_audit_logs(
        task_id=task_id,
        agent=agent,
        action=action,
        since=since,
        limit=limit,
        cursor=cursor,
    )
    if include_thread_origin:
        entries = _enrich_thread_dream_origin(entries, org.db)
    return {"entries": entries, "next_cursor": next_cursor}
