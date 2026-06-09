"""Dream endpoints: private scheduled agent reflection."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.dream_store import DreamStore
from runtime.infrastructure.learnings_store import LearningEntry, LearningsStore
from runtime.models import DreamKbCandidate, DreamStatus, ThreadMessageKind, ThreadRecord

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
        if body.founder_thread.needed:
            founder_thread_id = org.db.next_thread_id()
            org.db.insert_thread(ThreadRecord(
                id=founder_thread_id,
                subject=body.founder_thread.subject.strip(),
                composed_by=dream.agent_name,
            ))
            org.db.append_thread_message(
                thread_id=founder_thread_id,
                speaker=dream.agent_name,
                kind=ThreadMessageKind.MESSAGE,
                body_markdown=body.founder_thread.body_markdown.strip(),
            )

        learnings_dir = org.root / "workspaces" / dream.agent_name / "learnings"
        learnings_dir.mkdir(parents=True, exist_ok=True)
        store = LearningsStore(learnings_dir)
        for learning in body.learnings:
            store.write_entry(LearningEntry(
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

    AuditLogger(org.db).log_dream_completed(
        dream_id,
        dream.agent_name,
        new_learnings_count=len(body.learnings),
        kb_candidate_count=len(body.kb_candidates),
        founder_thread_id=founder_thread_id,
    )
    return {"dream_id": dream_id, "status": "completed", "founder_thread_id": founder_thread_id}
