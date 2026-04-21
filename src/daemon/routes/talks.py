"""Talk endpoints — founder↔agent conversations."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.state import DaemonState
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.talk_store import TalkStore
from src.models import TalkRecord, TalkStatus

router = APIRouter(dependencies=[require_token()])


def _require_active(state: DaemonState) -> DaemonState:
    if state.is_idle:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "no_active_runtime"},
        )
    return state


def _store(state: DaemonState) -> TalkStore:
    assert state.runtime is not None
    return TalkStore(state.runtime.root / "talks")


class StartTalkBody(BaseModel):
    agent_name: str


@router.post("/talks")
async def start_talk(body: StartTalkBody, request: Request) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)
    async with state.db_lock:
        existing = state.db.list_open_talks_for_agent(body.agent_name)
        if existing:
            prior = existing[0]
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "talk_already_open",
                    "prior_open_talk_id": prior.id,
                    "prior_started_at": prior.started_at.isoformat(),
                },
            )
        talk_id = state.db.next_talk_id()
        talk = TalkRecord(id=talk_id, agent_name=body.agent_name)
        state.db.insert_talk(talk)
    AuditLogger(state.db).log_talk_started(talk_id, body.agent_name, resumed_from=None)
    stored = state.db.get_talk(talk_id)
    return {
        "talk_id": talk_id,
        "started_at": stored.started_at.isoformat(),
    }


def _talk_to_dict(t: TalkRecord, include_transcript: str | None = None) -> dict:
    d = {
        "talk_id": t.id,
        "agent_name": t.agent_name,
        "status": t.status.value,
        "started_at": t.started_at.isoformat(),
        "ended_at": t.ended_at.isoformat() if t.ended_at else None,
        "summary": t.summary,
        "topic_list": t.topic_list,
        "new_learnings_count": t.new_learnings_count,
        "new_kb_slugs": t.new_kb_slugs,
        "transcript_path": t.transcript_path,
    }
    if include_transcript is not None:
        d["transcript"] = include_transcript
    return d


_INLINE_TRANSCRIPT_MAX_BYTES = 256 * 1024  # spec §11 default: inline unless >256 KiB.


class AbandonBody(BaseModel):
    reason: str


@router.post("/talks/{talk_id}/resume")
async def resume_talk(talk_id: str, request: Request) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)
    async with state.db_lock:
        talk = state.db.get_talk(talk_id)
        if talk is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "talk_id": talk_id})
        if talk.status != TalkStatus.OPEN:
            raise HTTPException(
                status_code=400,
                detail={"code": "talk_not_open", "status": talk.status.value},
            )
    AuditLogger(state.db).log_talk_resumed(talk_id, talk.agent_name)
    return {
        "talk_id": talk_id,
        "started_at": talk.started_at.isoformat(),
    }


@router.post("/talks/{talk_id}/abandon")
async def abandon_talk(talk_id: str, body: AbandonBody, request: Request) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)
    async with state.db_lock:
        talk = state.db.get_talk(talk_id)
        if talk is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "talk_id": talk_id})
        if talk.status != TalkStatus.OPEN:
            raise HTTPException(
                status_code=400,
                detail={"code": "talk_not_open", "status": talk.status.value},
            )
        state.db.update_talk(talk_id, status=TalkStatus.ABANDONED)
    AuditLogger(state.db).log_talk_abandoned(talk_id, talk.agent_name, reason=body.reason)
    return {"talk_id": talk_id, "status": "abandoned"}


@router.get("/talks/{talk_id}")
def get_talk(talk_id: str, request: Request) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)
    talk = state.db.get_talk(talk_id)
    if talk is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "talk_id": talk_id})
    transcript = None
    if talk.status == TalkStatus.CLOSED and talk.transcript_path:
        try:
            content = _store(state).read_transcript(talk_id)
            if len(content.encode("utf-8")) <= _INLINE_TRANSCRIPT_MAX_BYTES:
                transcript = content
            # else: caller follows transcript_path directly — spec §11.
        except FileNotFoundError:
            transcript = None
    return _talk_to_dict(talk, include_transcript=transcript)
