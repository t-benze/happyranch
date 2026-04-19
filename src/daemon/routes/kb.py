"""Shared knowledge-base endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status

from src.daemon.auth import require_token
from src.daemon.state import DaemonState
from src.infrastructure.kb_store import KBStore, NotFound

router = APIRouter(dependencies=[require_token()])


def _require_active(state: DaemonState) -> DaemonState:
    if state.is_idle:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_active_runtime"},
        )
    return state


def _store(state: DaemonState) -> KBStore:
    assert state.runtime is not None
    return KBStore(state.runtime.root / "kb")


@router.get("/kb")
def list_kb(
    request: Request,
    topic: Optional[str] = None,
    type: Optional[str] = None,  # noqa: A002
) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)
    summaries = _store(state).list_entries(topic=topic, type=type)
    return {
        "entries": [
            {
                "slug": s.slug,
                "title": s.title,
                "type": s.type,
                "topic": s.topic,
                "tags": s.tags,
                "updated_at": s.updated_at,
            }
            for s in summaries
        ]
    }


@router.get("/kb/search")
def search_kb(request: Request, q: str, limit: int = 20) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)
    hits = _store(state).search(q, limit=limit)
    return {
        "hits": [
            {"slug": h.slug, "title": h.title, "snippet": h.snippet, "score": h.score}
            for h in hits
        ]
    }


@router.get("/kb/{slug}")
def get_kb(slug: str, request: Request) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)
    try:
        entry = _store(state).read_entry(slug)
    except NotFound:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "slug": slug}
        )
    return {
        "slug": entry.slug,
        "title": entry.title,
        "type": entry.type,
        "topic": entry.topic,
        "tags": entry.tags,
        "authored_by": entry.authored_by,
        "authored_at": entry.authored_at,
        "updated_by": entry.updated_by,
        "updated_at": entry.updated_at,
        "source_task": entry.source_task,
        "supersedes": entry.supersedes,
        "body": entry.body,
    }
