"""Shared knowledge-base endpoints."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

_log = logging.getLogger(__name__)

from src.daemon.auth import require_token
from src.daemon.org_state import OrgState
from src.daemon.routes._org_dep import OrgDep
from src.infrastructure.kb_store import (
    InvalidEntry,
    InvalidSlug,
    KBEntry,
    KBStore,
    NotFound,
    SlugExists,
)

router = APIRouter(dependencies=[require_token()])


def _store(org: OrgState) -> KBStore:
    return KBStore(org.root / "kb")


@router.get("/kb")
def list_kb(
    slug: str,
    org: OrgDep,
    topic: Optional[str] = None,
    type: Optional[str] = None,  # noqa: A002
) -> dict:
    summaries = _store(org).list_entries(topic=topic, type=type)
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
def search_kb(slug: str, org: OrgDep, q: str, limit: int = 20) -> dict:
    hits = _store(org).search(q, limit=limit)
    return {
        "hits": [
            {"slug": h.slug, "title": h.title, "snippet": h.snippet, "score": h.score}
            for h in hits
        ]
    }


@router.get("/kb/{entry_slug}")
def get_kb(slug: str, entry_slug: str, org: OrgDep) -> dict:
    try:
        entry = _store(org).read_entry(entry_slug)
    except NotFound:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "slug": entry_slug}
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


class KBAddBody(BaseModel):
    agent: str
    slug: str
    title: str
    type: str
    topic: str
    body: str
    tags: list[str] = []
    source_task: Optional[str] = None
    supersedes: Optional[str] = None
    force_new_sibling: bool = False


class KBUpdateBody(BaseModel):
    agent: str
    slug: str
    title: str
    type: str
    topic: str
    body: str
    tags: list[str] = []
    source_task: Optional[str] = None
    supersedes: Optional[str] = None


def _raise_invalid_entry(exc: InvalidEntry) -> None:
    code = exc.code
    status_code = 413 if code == "entry_too_large" else 400
    raise HTTPException(status_code=status_code, detail={"code": code, "message": str(exc)})


def _kb_write(
    org: OrgState, entry: KBEntry, agent: str, force_new_sibling: bool
) -> KBEntry:
    store = _store(org)
    try:
        store.validate_slug(entry.slug)
    except InvalidSlug as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_slug", "message": str(exc)})
    if store.path_for(entry.slug).exists():
        existing = store.read_entry(entry.slug)
        raise HTTPException(
            status_code=409,
            detail={"code": "slug_exists", "slug": entry.slug, "existing_title": existing.title},
        )
    if not force_new_sibling:
        dups = store.find_near_duplicates(title=entry.title, tags=entry.tags)
        if dups:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "near_duplicate",
                    "candidates": [
                        {"slug": d.slug, "title": d.title, "similarity": d.similarity}
                        for d in dups
                    ],
                    "suggestion": "update",
                },
            )
    try:
        written = store.write_entry(entry, agent=agent)
    except SlugExists as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "slug_exists", "slug": exc.slug, "existing_title": exc.existing_title},
        )
    except InvalidEntry as exc:
        _raise_invalid_entry(exc)
    try:
        store.regenerate_index()
    except Exception:  # noqa: BLE001 — regen is non-fatal per spec §6.3
        _log.warning("kb _index.md regeneration failed after write", exc_info=True)
    return written


@router.post("/kb")
async def add_kb(slug: str, body: KBAddBody, org: OrgDep) -> dict:
    entry = KBEntry(
        slug=body.slug,
        title=body.title,
        type=body.type,
        topic=body.topic,
        tags=body.tags,
        body=body.body,
        source_task=body.source_task,
        supersedes=body.supersedes,
    )
    async with org.kb_lock:
        written = _kb_write(org, entry, agent=body.agent, force_new_sibling=body.force_new_sibling)
    return {"slug": written.slug, "updated_at": written.updated_at}


@router.post("/kb/reindex")
async def reindex_kb(slug: str, org: OrgDep) -> dict:
    async with org.kb_lock:
        _store(org).regenerate_index()
    return {"ok": True}


@router.post("/kb/{entry_slug}")
async def update_kb(slug: str, entry_slug: str, body: KBUpdateBody, org: OrgDep) -> dict:
    if body.slug != entry_slug:
        raise HTTPException(status_code=400, detail={"code": "slug_mismatch"})
    store = _store(org)
    entry = KBEntry(
        slug=body.slug,
        title=body.title,
        type=body.type,
        topic=body.topic,
        tags=body.tags,
        body=body.body,
        source_task=body.source_task,
        supersedes=body.supersedes,
    )
    async with org.kb_lock:
        try:
            store.validate_slug(entry.slug)
        except InvalidSlug as exc:
            raise HTTPException(status_code=400, detail={"code": "invalid_slug", "message": str(exc)})
        try:
            updated = store.update_entry(entry, agent=body.agent)
        except NotFound:
            raise HTTPException(status_code=404, detail={"code": "not_found", "slug": entry_slug})
        except InvalidEntry as exc:
            _raise_invalid_entry(exc)
        try:
            store.regenerate_index()
        except Exception:  # noqa: BLE001 — regen is non-fatal per spec §6.3
            _log.warning("kb _index.md regeneration failed after write", exc_info=True)
    return {"slug": updated.slug, "updated_at": updated.updated_at, "updated_by": updated.updated_by}


@router.delete("/kb/{entry_slug}")
async def delete_kb(
    slug: str,
    entry_slug: str,
    org: OrgDep,
    agent: str,
    confirm: bool = False,
    as_founder: bool = False,
) -> dict:
    if not as_founder and (org.teams is None or not org.teams.is_team_manager(agent)):
        raise HTTPException(
            status_code=403,
            detail={"code": "delete_forbidden", "required": "team_manager"},
        )
    if not confirm:
        raise HTTPException(status_code=400, detail={"code": "confirm_required"})
    store = _store(org)
    async with org.kb_lock:
        try:
            store.delete_entry(entry_slug)
        except NotFound:
            raise HTTPException(status_code=404, detail={"code": "not_found", "slug": entry_slug})
        try:
            store.regenerate_index()
        except Exception:  # noqa: BLE001 — regen is non-fatal per spec §6.3
            _log.warning("kb _index.md regeneration failed after write", exc_info=True)
    return {"ok": True, "slug": entry_slug}
