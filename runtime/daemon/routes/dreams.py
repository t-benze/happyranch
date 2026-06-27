"""Dream endpoints: private scheduled agent reflection."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.routes.threads import (
    FOUNDER_LITERAL,
    _create_agent_thread_locked,
    _publish_thread_event,
)
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.dream_store import DreamStore
from runtime.infrastructure.learnings_store import MemoryItem, MemoryStore
from runtime.infrastructure.memory_migration import migrate_workspace
from runtime.models import DreamKbCandidate, DreamStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import load_org_config

router = APIRouter(dependencies=[require_token()])


def _store(org) -> DreamStore:
    return DreamStore(org.root / "dreams")


class DreamLearningBody(BaseModel):
    slug: str
    title: str
    topic: str
    body: str


class DreamKbCandidateBody(BaseModel):
    slug: str
    title: str
    topic: str
    rationale: str
    body_markdown: str


class FounderThreadBody(BaseModel):
    needed: bool = False
    subject: str | None = None
    body_markdown: str | None = None


class DreamCompleteBody(BaseModel):
    summary: str = Field(min_length=1)
    learnings: list[DreamLearningBody] = []
    kb_candidates: list[DreamKbCandidateBody] = []
    founder_thread: FounderThreadBody = Field(default_factory=FounderThreadBody)


def _dream_to_dict(dream, *, candidates=None, transcript: str | None = None) -> dict:
    data = {
        "dream_id": dream.id,
        "agent_name": dream.agent_name,
        "local_date": dream.local_date,
        "scheduled_for": dream.scheduled_for.isoformat(),
        "window_start": dream.window_start.isoformat() if dream.window_start else None,
        "window_end": dream.window_end.isoformat(),
        "started_at": dream.started_at.isoformat() if dream.started_at else None,
        "ended_at": dream.ended_at.isoformat() if dream.ended_at else None,
        "status": dream.status.value,
        "summary": dream.summary,
        "transcript_path": dream.transcript_path,
        "new_learnings_count": dream.new_learnings_count,
        "kb_candidate_count": dream.kb_candidate_count,
        "founder_thread_id": dream.founder_thread_id,
        "error": dream.error,
    }
    if candidates is not None:
        data["kb_candidates"] = [
            {
                "id": c.id,
                "dream_id": c.dream_id,
                "agent_name": c.agent_name,
                "slug": c.slug,
                "title": c.title,
                "topic": c.topic,
                "rationale": c.rationale,
                "status": c.status,
                "promoted_kb_slug": c.promoted_kb_slug,
            }
            for c in candidates
        ]
    if transcript is not None:
        data["transcript"] = transcript
    return data


@router.get("/dreams/status")
def dream_status(slug: str, org: OrgDep, agent: str | None = None) -> dict:
    dreams = org.db.list_dreams(agent=agent, limit=20)
    return {"recent": [_dream_to_dict(d) for d in dreams]}


@router.get("/dreams")
def list_dreams(slug: str, org: OrgDep, agent: str | None = None, limit: int = 50) -> dict:
    return {"dreams": [_dream_to_dict(d) for d in org.db.list_dreams(agent=agent, limit=limit)]}


@router.get("/dreams/{dream_id}")
def show_dream(slug: str, dream_id: str, org: OrgDep) -> dict:
    dream = org.db.get_dream(dream_id)
    if dream is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "dream_id": dream_id})
    candidates = org.db.list_dream_kb_candidates(dream_id=dream_id)
    transcript = None
    if dream.transcript_path:
        try:
            transcript = _store(org).read_transcript(dream_id)
        except FileNotFoundError:
            transcript = None
    return _dream_to_dict(dream, candidates=candidates, transcript=transcript)


@router.post("/dreams/{dream_id}/complete")
async def complete_dream(slug: str, dream_id: str, body: DreamCompleteBody, org: OrgDep, request: Request) -> dict:
    if body.founder_thread.needed:
        if not (body.founder_thread.subject or "").strip():
            raise HTTPException(status_code=422, detail={"code": "empty_thread_subject"})
        if not (body.founder_thread.body_markdown or "").strip():
            raise HTTPException(status_code=422, detail={"code": "empty_thread_body"})

    async with org.db_lock:
        dream = org.db.get_dream(dream_id)
        if dream is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "dream_id": dream_id})
        if dream.status != DreamStatus.RUNNING:
            raise HTTPException(status_code=400, detail={"code": "dream_not_running", "status": dream.status.value})

        founder_thread_id = None
        founder_thread_seq: int | None = None
        founder_thread_preview = ""
        if body.founder_thread.needed:
            # Route founder-thread creation through the shared compose helper so
            # the founder thread gets the same participant semantics, turn
            # accounting, and thread_started/thread_message_sent audit rows as an
            # agent-initiated compose. Recipients are @founder only: the dream
            # agent is the sole participant; no other agent is looped in.
            turn_cap = load_org_config(OrgPaths(root=org.root)).threads_default_turn_cap
            founder_thread_preview = body.founder_thread.body_markdown.strip()
            founder_thread_id, founder_thread_seq, _tokens, _addressed = _create_agent_thread_locked(
                org,
                composer=dream.agent_name,
                subject=body.founder_thread.subject.strip(),
                body_text=founder_thread_preview,
                recipients=[FOUNDER_LITERAL],
                turn_cap=turn_cap,
                composed_from_dream_id=dream_id,
            )

        # THR-032 Phase R: dream reflections are memory items. Migrate a legacy
        # learnings/ workspace forward first (idempotent), then write to memory/.
        workspace = org.root / "workspaces" / dream.agent_name
        migrate_workspace(workspace)
        memory_dir = workspace / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        store = MemoryStore(memory_dir)
        for learning in body.learnings:
            store.write_entry(MemoryItem(
                id=store.next_id(),
                slug=learning.slug,
                title=learning.title,
                topic=learning.topic,
                body=learning.body if learning.body.endswith("\n") else learning.body + "\n",
                source_task=dream_id,
            ), agent=dream.agent_name)
        if body.learnings:
            store.regenerate_index()

        for candidate in body.kb_candidates:
            org.db.insert_dream_kb_candidate(DreamKbCandidate(
                dream_id=dream_id,
                agent_name=dream.agent_name,
                slug=candidate.slug,
                title=candidate.title,
                topic=candidate.topic,
                rationale=candidate.rationale,
                body_markdown=candidate.body_markdown,
            ))

        now = datetime.now(timezone.utc)
        transcript_path = _store(org).write_transcript(
            dream_id=dream_id,
            agent_name=dream.agent_name,
            local_date=dream.local_date,
            window_start=dream.window_start.isoformat() if dream.window_start else None,
            window_end=dream.window_end.isoformat(),
            summary=body.summary,
            transcript_markdown=body.summary,
            new_learnings_count=len(body.learnings),
            kb_candidate_count=len(body.kb_candidates),
            founder_thread_id=founder_thread_id,
        )
        org.db.update_dream(
            dream_id,
            status=DreamStatus.COMPLETED,
            ended_at=now,
            summary=body.summary,
            transcript_path=str(transcript_path),
            new_learnings_count=len(body.learnings),
            kb_candidate_count=len(body.kb_candidates),
            founder_thread_id=founder_thread_id,
        )

    if founder_thread_id is not None:
        AuditLogger(org.db).log_dream_founder_thread_created(
            dream_id, dream.agent_name, founder_thread_id=founder_thread_id,
        )
        await _publish_thread_event(
            org, slug,
            thread_id=founder_thread_id, seq=founder_thread_seq,
            speaker=dream.agent_name, kind="message",
            preview=founder_thread_preview, status="open",
        )

    AuditLogger(org.db).log_dream_completed(
        dream_id,
        dream.agent_name,
        new_learnings_count=len(body.learnings),
        kb_candidate_count=len(body.kb_candidates),
        founder_thread_id=founder_thread_id,
    )
    return {"dream_id": dream_id, "status": "completed", "founder_thread_id": founder_thread_id}


def _candidate_to_dict(candidate: DreamKbCandidate) -> dict:
    return {
        "id": candidate.id,
        "dream_id": candidate.dream_id,
        "agent_name": candidate.agent_name,
        "slug": candidate.slug,
        "title": candidate.title,
        "topic": candidate.topic,
        "rationale": candidate.rationale,
        "body_markdown": candidate.body_markdown,
        "status": candidate.status,
        "promoted_kb_slug": candidate.promoted_kb_slug,
        "created_at": candidate.created_at.isoformat(),
        "updated_at": candidate.updated_at.isoformat(),
    }


@router.post("/dreams/candidates/{candidate_id}/accept")
async def accept_candidate(slug: str, candidate_id: int, org: OrgDep) -> dict:
    from runtime.daemon.routes.kb import _kb_write
    from runtime.infrastructure.kb_store import KBEntry

    rows = org.db.list_dream_kb_candidates(candidate_id=candidate_id)
    if not rows:
        raise HTTPException(status_code=404, detail={"code": "candidate_not_found", "candidate_id": candidate_id})
    candidate = rows[0]

    if candidate.status == "promoted":
        return _candidate_to_dict(candidate)
    if candidate.status != "pending":
        raise HTTPException(
            status_code=400,
            detail={"code": "candidate_already_decided", "status": candidate.status},
        )

    entry = KBEntry(
        slug=candidate.slug,
        title=candidate.title,
        type="precedent",
        topic=candidate.topic,
        body=candidate.body_markdown,
        source_task=candidate.dream_id,
    )

    async with org.kb_lock:
        written = _kb_write(org, entry, agent=candidate.agent_name, force_new_sibling=False)

    org.db.update_dream_kb_candidate(
        candidate_id, status="promoted", promoted_kb_slug=written.slug,
    )
    updated = org.db.list_dream_kb_candidates(candidate_id=candidate_id)[0]
    return _candidate_to_dict(updated)


@router.post("/dreams/candidates/{candidate_id}/dismiss")
async def dismiss_candidate(slug: str, candidate_id: int, org: OrgDep) -> dict:
    rows = org.db.list_dream_kb_candidates(candidate_id=candidate_id)
    if not rows:
        raise HTTPException(status_code=404, detail={"code": "candidate_not_found", "candidate_id": candidate_id})
    candidate = rows[0]

    if candidate.status == "rejected":
        return _candidate_to_dict(candidate)
    if candidate.status == "promoted":
        raise HTTPException(
            status_code=400,
            detail={"code": "candidate_already_promoted", "status": candidate.status},
        )
    if candidate.status != "pending":
        raise HTTPException(
            status_code=400,
            detail={"code": "candidate_already_decided", "status": candidate.status},
        )

    org.db.update_dream_kb_candidate(candidate_id, status="rejected")
    updated = org.db.list_dream_kb_candidates(candidate_id=candidate_id)[0]
    return _candidate_to_dict(updated)
