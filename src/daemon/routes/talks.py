"""Talk endpoints — founder↔agent conversations."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.org_state import OrgState
from src.daemon.routes._doctrine import SELF_DISPATCH_HINT
from src.daemon.routes._org_dep import OrgDep
from src.daemon.routes.agents import _append_to_learnings_file
from src.daemon.runner import enqueue_task
from src.daemon.state import DaemonState
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.kb_store import KBStore
from src.infrastructure.talk_store import TalkStore
from src.models import TalkRecord, TalkStatus, TaskRecord
from src.orchestrator import prompt_loader
from src.orchestrator._paths import OrgPaths

router = APIRouter(dependencies=[require_token()])


def _store(org: OrgState) -> TalkStore:
    return TalkStore(org.root / "talks")


class StartTalkBody(BaseModel):
    agent_name: str


@router.post("/talks")
async def start_talk(slug: str, body: StartTalkBody, org: OrgDep) -> dict:
    async with org.db_lock:
        existing = org.db.list_open_talks_for_agent(body.agent_name)
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
        talk_id = org.db.next_talk_id()
        talk = TalkRecord(id=talk_id, agent_name=body.agent_name)
        org.db.insert_talk(talk)
    AuditLogger(org.db).log_talk_started(talk_id, body.agent_name, resumed_from=None)
    stored = org.db.get_talk(talk_id)
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
async def resume_talk(slug: str, talk_id: str, org: OrgDep) -> dict:
    async with org.db_lock:
        talk = org.db.get_talk(talk_id)
        if talk is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "talk_id": talk_id})
        if talk.status != TalkStatus.OPEN:
            raise HTTPException(
                status_code=400,
                detail={"code": "talk_not_open", "status": talk.status.value},
            )
    AuditLogger(org.db).log_talk_resumed(talk_id, talk.agent_name)
    return {
        "talk_id": talk_id,
        "started_at": talk.started_at.isoformat(),
    }


@router.post("/talks/{talk_id}/abandon")
async def abandon_talk(slug: str, talk_id: str, body: AbandonBody, org: OrgDep) -> dict:
    async with org.db_lock:
        talk = org.db.get_talk(talk_id)
        if talk is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "talk_id": talk_id})
        if talk.status != TalkStatus.OPEN:
            raise HTTPException(
                status_code=400,
                detail={"code": "talk_not_open", "status": talk.status.value},
            )
        org.db.update_talk(talk_id, status=TalkStatus.ABANDONED)
    AuditLogger(org.db).log_talk_abandoned(talk_id, talk.agent_name, reason=body.reason)
    return {"talk_id": talk_id, "status": "abandoned"}


@router.post("/talks/{talk_id}/end")
async def end_talk(slug: str, talk_id: str, body: EndTalkBody, org: OrgDep) -> dict:
    async with org.db_lock:
        talk = org.db.get_talk(talk_id)
        if talk is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "talk_id": talk_id})
        if talk.status != TalkStatus.OPEN:
            raise HTTPException(
                status_code=400,
                detail={"code": "talk_not_open", "status": talk.status.value},
            )
        # Validate kb_slugs — every claimed slug must exist in the KB.
        kb_store = KBStore(org.root / "kb")
        for kb_slug in body.kb_slugs:
            if not kb_store.path_for(kb_slug).exists():
                raise HTTPException(
                    status_code=400,
                    detail={"code": "unknown_kb_slug", "slug": kb_slug},
                )
        now_iso = datetime.now(timezone.utc).isoformat()
        transcript_path = _store(org).write_transcript(
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
        # Append learnings to the agent's workspace.
        # Migrated workspaces have a learnings/ directory; pre-migration ones
        # use the flat learnings.md file.
        workspace = org.root / "workspaces" / talk.agent_name
        learnings_dir = workspace / "learnings"
        flat_path = workspace / "learnings.md"
        if learnings_dir.exists():
            # Migrated workspace: write structured entries to the new store.
            from src.infrastructure.learnings_store import LearningsStore, LearningEntry
            store = LearningsStore(learnings_dir)
            for idx, entry in enumerate(body.learnings):
                lid = store.next_id()
                # Title must be single-line — derive from first non-empty line
                # so multiline learnings don't break frontmatter/index rendering.
                first_line = next(
                    (ln.strip() for ln in entry.text.splitlines() if ln.strip()),
                    "",
                )
                title = first_line[:80] or f"Talk {talk_id} learning #{idx + 1}"
                slug = f"talk-{talk_id.lower()}-{idx + 1}"
                le = LearningEntry(
                    id=lid,
                    slug=slug,
                    title=title,
                    topic="talk-residue",
                    body=entry.text if entry.text.endswith("\n") else entry.text + "\n",
                    source_task=talk_id,
                )
                store.write_entry(le, agent=talk.agent_name)
            if body.learnings:
                store.regenerate_index()
        else:
            # Pre-migration: keep legacy flat-file behavior.
            for entry in body.learnings:
                _append_to_learnings_file(flat_path, talk.agent_name, entry.text)
        org.db.update_talk(
            talk_id,
            status=TalkStatus.CLOSED,
            summary=body.summary,
            topic_list=body.topic_list,
            new_learnings_count=len(body.learnings),
            new_kb_slugs=body.kb_slugs,
            transcript_path=str(transcript_path),
            ended_at=now_iso,
        )
    AuditLogger(org.db).log_talk_ended(
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
    slug: str,
    org: OrgDep,
    agent: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict:
    rows = org.db.list_talks(agent=agent, status=status, limit=limit)
    return {"talks": [_talk_to_dict(t) for t in rows]}


@router.get("/talks/{talk_id}")
def get_talk(slug: str, talk_id: str, org: OrgDep) -> dict:
    talk = org.db.get_talk(talk_id)
    if talk is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "talk_id": talk_id})
    transcript = None
    if talk.status == TalkStatus.CLOSED and talk.transcript_path:
        try:
            content = _store(org).read_transcript(talk_id)
            if len(content.encode("utf-8")) <= _INLINE_TRANSCRIPT_MAX_BYTES:
                transcript = content
            # else: caller follows transcript_path directly — spec §11.
        except FileNotFoundError:
            transcript = None
    return _talk_to_dict(talk, include_transcript=transcript)


@router.post("/talks/{talk_id}/dispatch")
async def dispatch_task(
    slug: str, talk_id: str, body: DispatchBody, org: OrgDep, request: Request
) -> dict:
    state: DaemonState = request.app.state.daemon

    # 1. Talk exists + open.
    talk = org.db.get_talk(talk_id)
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
    if org.teams is None:
        raise HTTPException(status_code=403, detail={"code": "teams_registry_unavailable"})

    # 3. Resolve dispatcher's team. 4. Forbid cross-team. 5. Role-based assignment.
    # All three steps read the teams registry — hold teams_lock so a concurrent
    # manage-agent mutation cannot tear membership reads.
    dispatcher = talk.agent_name
    async with org.teams_lock:
        is_manager = org.teams.is_team_manager(dispatcher)
        dispatcher_team = (
            org.teams.team_for_manager(dispatcher) if is_manager
            else org.teams.team_for_agent(dispatcher)
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
                    "code": "talk_dispatch_team_override_forbidden",
                    "dispatcher_team": dispatcher_team,
                    "requested_team": effective_team,
                    "hint": SELF_DISPATCH_HINT,
                },
            )

        # 5. Self-only dispatch rule (managers and workers alike).
        effective_target = body.target_agent if body.target_agent is not None else dispatcher
        if effective_target != dispatcher:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "talk_dispatch_must_be_self",
                    "dispatcher": dispatcher,
                    "requested_target": effective_target,
                    "hint": SELF_DISPATCH_HINT,
                },
            )

    # 6. Target agent is active (file under <runtime>/org/agents/) AND has a workspace.
    agent_def = prompt_loader.load_agent(OrgPaths(root=org.root), effective_target)
    workspace_exists = (org.root / "workspaces" / effective_target).exists()
    if agent_def is None or not workspace_exists:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_agent", "agent": effective_target},
        )

    # 7. Insert + audit + enqueue.
    async with org.db_lock:
        task_id = org.db.next_task_id()
        org.db.insert_task(TaskRecord(
            id=task_id,
            brief=brief,
            team=effective_team,
            assigned_agent=effective_target,
            dispatched_from_talk_id=talk_id,
        ))
        AuditLogger(org.db).log_task_dispatched(
            task_id=task_id,
            talk_id=talk_id,
            dispatcher_agent=dispatcher,
            dispatcher_role="manager" if is_manager else "worker",
            effective_target=effective_target,
            team=effective_team,
        )

    enqueue_task(state, org.slug, task_id)

    return {
        "task_id": task_id,
        "team": effective_team,
        "assigned_agent": effective_target,
        "dispatched_from_talk_id": talk_id,
    }
