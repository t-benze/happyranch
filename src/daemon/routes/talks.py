"""Talk endpoints — founder↔agent conversations."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.routes.agents import _append_to_learnings_file
from src.daemon.runner import enqueue_task
from src.daemon.state import DaemonState
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.kb_store import KBStore
from src.infrastructure.talk_store import TalkStore
from src.models import TalkRecord, TalkStatus, TaskRecord

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


class EndTalkLearning(BaseModel):
    text: str


class EndTalkBody(BaseModel):
    summary: str
    topic_list: list[str] = []
    transcript_markdown: str
    learnings: list[EndTalkLearning] = []
    kb_slugs: list[str] = []


class DispatchBody(BaseModel):
    brief: str
    target_agent: str | None = None
    team: str | None = None


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


@router.post("/talks/{talk_id}/end")
async def end_talk(talk_id: str, body: EndTalkBody, request: Request) -> dict:
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
        # Validate kb_slugs — every claimed slug must exist in the KB.
        kb_store = KBStore(state.runtime.root / "kb")
        for slug in body.kb_slugs:
            if not kb_store.path_for(slug).exists():
                raise HTTPException(
                    status_code=400,
                    detail={"code": "unknown_kb_slug", "slug": slug},
                )
        now_iso = datetime.now(timezone.utc).isoformat()
        transcript_path = _store(state).write_transcript(
            talk_id=talk_id,
            agent_name=talk.agent_name,
            started_at=talk.started_at.isoformat(),
            ended_at=now_iso,
            topic_list=body.topic_list,
            new_learnings_count=len(body.learnings),
            new_kb_slugs=body.kb_slugs,
            summary=body.summary,
            transcript_markdown=body.transcript_markdown,
        )
        # Append learnings to the agent's learnings.md — same helper the
        # /agents/{name}/learnings route uses, minus the session guard.
        learnings_path = state.runtime.workspaces_dir / talk.agent_name / "learnings.md"
        for entry in body.learnings:
            _append_to_learnings_file(learnings_path, talk.agent_name, entry.text)
        state.db.update_talk(
            talk_id,
            status=TalkStatus.CLOSED,
            summary=body.summary,
            topic_list=body.topic_list,
            new_learnings_count=len(body.learnings),
            new_kb_slugs=body.kb_slugs,
            transcript_path=str(transcript_path),
            ended_at=now_iso,
        )
    AuditLogger(state.db).log_talk_ended(
        talk_id,
        talk.agent_name,
        new_learnings_count=len(body.learnings),
        new_kb_slugs=body.kb_slugs,
    )
    return {
        "talk_id": talk_id,
        "status": "closed",
        "transcript_path": str(transcript_path),
        "new_learnings_count": len(body.learnings),
    }


@router.get("/talks")
def list_talks(
    request: Request,
    agent: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)
    rows = state.db.list_talks(agent=agent, status=status, limit=limit)
    return {"talks": [_talk_to_dict(t) for t in rows]}


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


@router.post("/talks/{talk_id}/dispatch")
async def dispatch_task(talk_id: str, body: DispatchBody, request: Request) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)

    # 1. Talk exists + open.
    talk = state.db.get_talk(talk_id)
    if talk is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "talk_id": talk_id})
    if talk.status != TalkStatus.OPEN:
        raise HTTPException(
            status_code=400,
            detail={"code": "talk_not_open", "status": talk.status.value},
        )

    # 2. Brief non-empty after strip.
    brief = body.brief.strip()
    if not brief:
        raise HTTPException(status_code=422, detail={"code": "empty_brief"})

    # 2b. Reject empty strings on optional body fields (None means "unset"; "" is malformed).
    if body.team is not None and not body.team.strip():
        raise HTTPException(status_code=422, detail={"code": "empty_team"})
    if body.target_agent is not None and not body.target_agent.strip():
        raise HTTPException(status_code=422, detail={"code": "empty_target_agent"})

    # 2c. Teams registry must be available for role/membership checks.
    if state.teams is None:
        raise HTTPException(status_code=403, detail={"code": "teams_registry_unavailable"})

    # 3. Resolve dispatcher's team. 4. Forbid cross-team. 5. Role-based assignment.
    # All three steps read the teams registry — hold teams_lock so a concurrent
    # manage-agent mutation cannot tear membership reads.
    dispatcher = talk.agent_name
    async with state.teams_lock:
        is_manager = state.teams.is_team_manager(dispatcher)
        dispatcher_team = (
            state.teams.team_for_manager(dispatcher) if is_manager
            else state.teams.team_for_agent(dispatcher)
        )
        if dispatcher_team is None:
            raise HTTPException(
                status_code=403,
                detail={"code": "dispatcher_team_unknown", "agent": dispatcher},
            )

        # 4. Resolve effective_team and forbid cross-team.
        effective_team = body.team if body.team is not None else dispatcher_team
        if effective_team != dispatcher_team:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "cross_team_dispatch_forbidden",
                    "dispatcher_team": dispatcher_team,
                    "requested_team": effective_team,
                },
            )

        # 5. Resolve effective_target + role-based assignment rule.
        effective_target = body.target_agent if body.target_agent is not None else dispatcher
        if not is_manager and effective_target != dispatcher:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "worker_must_self_dispatch",
                    "dispatcher": dispatcher,
                    "requested_target": effective_target,
                },
            )
        if is_manager:
            team_meta = state.teams.manager_for_team(dispatcher_team)
            in_team = (
                effective_target == team_meta.name
                or effective_target in team_meta.workers
            )
            if not in_team:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "target_not_in_team",
                        "team": dispatcher_team,
                        "requested_target": effective_target,
                    },
                )

    # 6. Target agent is registered AND has a workspace.
    enrollment = state.db.get_enrollment(effective_target)
    workspace_exists = (state.runtime.workspaces_dir / effective_target).exists()
    if enrollment is None or enrollment.get("status") != "approved" or not workspace_exists:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_agent", "agent": effective_target},
        )

    # 7. Insert + audit + enqueue.
    async with state.db_lock:
        task_id = state.db.next_task_id()
        state.db.insert_task(TaskRecord(
            id=task_id,
            brief=brief,
            team=effective_team,
            assigned_agent=effective_target,
            dispatched_from_talk_id=talk_id,
        ))
        AuditLogger(state.db).log_task_dispatched(
            task_id=task_id,
            talk_id=talk_id,
            dispatcher_agent=dispatcher,
            dispatcher_role="manager" if is_manager else "worker",
            effective_target=effective_target,
            team=effective_team,
        )

    enqueue_task(state, task_id)

    return {
        "task_id": task_id,
        "team": effective_team,
        "assigned_agent": effective_target,
        "dispatched_from_talk_id": talk_id,
    }
