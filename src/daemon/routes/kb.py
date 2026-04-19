"""Shared knowledge-base endpoints."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.state import DaemonState
from src.infrastructure.kb_store import (
    InvalidEntry,
    InvalidSlug,
    KBEntry,
    KBStore,
    NotFound,
    SlugExists,
)
from src.models import TaskType

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
    state: DaemonState, entry: KBEntry, agent: str, force_new_sibling: bool
) -> KBEntry:
    store = _store(state)
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
        pass
    return written


@router.post("/kb")
async def add_kb(body: KBAddBody, request: Request) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)
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
    async with state.kb_lock:
        written = _kb_write(state, entry, agent=body.agent, force_new_sibling=body.force_new_sibling)
    return {"slug": written.slug, "updated_at": written.updated_at}


@router.post("/kb/reindex")
async def reindex_kb(request: Request) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)
    async with state.kb_lock:
        _store(state).regenerate_index()
    return {"ok": True}


_TOPIC_FOR_TASK_TYPE: dict[TaskType, str] = {
    TaskType.PAYMENT_CHANGE: "payment",
    TaskType.BUG_FIX: "engineering",
    TaskType.IMPLEMENT_FEATURE: "engineering",
}


class KBPrecedentBody(BaseModel):
    task_id: str
    decision: str
    rationale: str
    slug: Optional[str] = None


@router.post("/kb/precedent")
async def precedent_kb(body: KBPrecedentBody, request: Request) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)
    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail={"code": "invalid_decision"})
    if not body.rationale.strip():
        raise HTTPException(status_code=400, detail={"code": "rationale_required"})
    task = state.db.get_task(body.task_id)
    if task is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "task_not_found", "task_id": body.task_id},
        )

    escalation_rows = [
        r for r in state.db.get_audit_logs(body.task_id) if r["action"] == "escalation"
    ]
    if not escalation_rows:
        raise HTTPException(
            status_code=400, detail={"code": "no_escalation_record", "task_id": body.task_id},
        )
    escalation_reason = escalation_rows[-1]["payload"].get("reason", "")

    default_slug = f"precedent-{body.task_id.lower().replace('_', '-')}-{body.decision}"
    slug = body.slug or default_slug
    topic = _TOPIC_FOR_TASK_TYPE.get(task.type, "general")
    title = f"{task.brief} — {body.decision}"
    entry_body = (
        f"# Precedent: {task.brief}\n\n"
        f"## Context\n\n"
        f"- Task: `{body.task_id}`\n"
        f"- Brief: {task.brief}\n\n"
        f"## Escalation reason\n\n{escalation_reason}\n\n"
        f"## Decision\n\n{body.decision}\n\n"
        f"## Rationale\n\n{body.rationale}\n"
    )
    entry = KBEntry(
        slug=slug,
        title=title,
        type="precedent",
        topic=topic,
        tags=["precedent", body.decision],
        body=entry_body,
        source_task=body.task_id,
        escalation_reason=escalation_reason,
        founder_decision=body.decision,
        founder_rationale=body.rationale,
    )
    async with state.kb_lock:
        written = _kb_write(state, entry, agent="founder", force_new_sibling=True)
    return {"slug": written.slug, "task_id": body.task_id}


@router.post("/kb/{slug}")
async def update_kb(slug: str, body: KBUpdateBody, request: Request) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)
    if body.slug != slug:
        raise HTTPException(status_code=400, detail={"code": "slug_mismatch"})
    store = _store(state)
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
    async with state.kb_lock:
        try:
            store.validate_slug(entry.slug)
        except InvalidSlug as exc:
            raise HTTPException(status_code=400, detail={"code": "invalid_slug", "message": str(exc)})
        try:
            updated = store.update_entry(entry, agent=body.agent)
        except NotFound:
            raise HTTPException(status_code=404, detail={"code": "not_found", "slug": slug})
        except InvalidEntry as exc:
            _raise_invalid_entry(exc)
        try:
            store.regenerate_index()
        except Exception:  # noqa: BLE001
            pass
    return {"slug": updated.slug, "updated_at": updated.updated_at, "updated_by": updated.updated_by}


@router.delete("/kb/{slug}")
async def delete_kb(
    slug: str,
    request: Request,
    agent: str,
    confirm: bool = False,
    as_founder: bool = False,
) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)
    if not as_founder and agent != "engineering_head":
        raise HTTPException(
            status_code=403,
            detail={"code": "delete_forbidden", "required": "engineering_head"},
        )
    if not confirm:
        raise HTTPException(status_code=400, detail={"code": "confirm_required"})
    store = _store(state)
    async with state.kb_lock:
        try:
            store.delete_entry(slug)
        except NotFound:
            raise HTTPException(status_code=404, detail={"code": "not_found", "slug": slug})
        try:
            store.regenerate_index()
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "slug": slug}
