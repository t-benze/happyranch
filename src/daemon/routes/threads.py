"""Thread endpoints — email-style multi-agent workchannel."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.routes._org_dep import OrgDep
from src.daemon.runner import enqueue_task
from src.daemon.state import DaemonState
from src.daemon.thread_queue import ThreadJob
from src.infrastructure.audit_logger import AuditLogger
from src.models import (
    TaskRecord,
    ThreadInvocationPurpose,
    ThreadMessageKind,
    ThreadRecord,
    ThreadStatus,
)
from src.orchestrator import prompt_loader
from src.orchestrator._paths import OrgPaths
from src.orchestrator.org_config import load_org_config

router = APIRouter(dependencies=[require_token()])


class ComposeBody(BaseModel):
    subject: str
    recipients: list[str]
    body_markdown: str
    addressed_to: list[str] = ["@all"]
    forwarded_from_id: str | None = None
    forwarded_from_kind: str | None = None  # 'thread' | 'talk'


def _validate_addressed_to(addressed_to: list[str], recipients: list[str]) -> None:
    if addressed_to == ["@all"]:
        return
    for name in addressed_to:
        if name == "@all":
            raise HTTPException(
                status_code=422,
                detail={"code": "addressed_to_mixed_at_all"},
            )
        if name not in recipients:
            raise HTTPException(
                status_code=422,
                detail={"code": "addressed_to_not_subset", "name": name},
            )


def _resolve_addressed_agents(addressed_to: list[str], recipients: list[str]) -> list[str]:
    if addressed_to == ["@all"]:
        return list(recipients)
    return list(addressed_to)


@router.post("/threads")
async def compose_thread(
    slug: str, body: ComposeBody, org: OrgDep, request: Request
) -> dict:
    state: DaemonState = request.app.state.daemon

    subject = body.subject.strip()
    if not subject:
        raise HTTPException(status_code=422, detail={"code": "empty_subject"})
    if not body.recipients:
        raise HTTPException(status_code=422, detail={"code": "empty_recipients"})
    body_text = body.body_markdown.strip()
    if not body_text:
        raise HTTPException(status_code=422, detail={"code": "empty_body"})

    # Validate each recipient is an approved agent with a workspace.
    org_paths = OrgPaths(root=org.root)
    for name in body.recipients:
        agent_def = prompt_loader.load_agent(org_paths, name)
        workspace_exists = (org.root / "workspaces" / name).exists()
        if agent_def is None or not workspace_exists:
            raise HTTPException(
                status_code=404,
                detail={"code": "unknown_agent", "agent": name},
            )

    _validate_addressed_to(body.addressed_to, body.recipients)

    # Validate forwarded source if set.
    if (body.forwarded_from_id is None) != (body.forwarded_from_kind is None):
        raise HTTPException(
            status_code=422,
            detail={"code": "forwarded_fields_must_pair"},
        )
    if body.forwarded_from_kind not in (None, "thread", "talk"):
        raise HTTPException(
            status_code=422,
            detail={"code": "forwarded_kind_invalid"},
        )
    if body.forwarded_from_id is not None:
        if body.forwarded_from_kind == "thread":
            src = org.db.get_thread(body.forwarded_from_id)
        else:
            src = org.db.get_talk(body.forwarded_from_id)
        if src is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "forwarded_source_not_found"},
            )

    # Turn cap from org config (default 500).
    org_cfg = load_org_config(org_paths)
    turn_cap = org_cfg.threads_default_turn_cap

    addressed_agents = _resolve_addressed_agents(body.addressed_to, body.recipients)

    async with org.db_lock:
        thread_id = org.db.next_thread_id()
        org.db.insert_thread(ThreadRecord(
            id=thread_id, subject=subject, turn_cap=turn_cap,
            forwarded_from_id=body.forwarded_from_id,
            forwarded_from_kind=body.forwarded_from_kind,
        ))
        for name in body.recipients:
            org.db.add_thread_participant(thread_id, name, added_by="founder")
        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.MESSAGE,
            body_markdown=body_text, addressed_to=body.addressed_to,
        )
        AuditLogger(org.db).log_thread_started(
            thread_id,
            subject=subject,
            initial_recipients=body.recipients,
            forwarded_from_id=body.forwarded_from_id,
        )
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker="founder",
            addressed_to=body.addressed_to, kind="message",
        )
        # Mint pending invocations for each addressed agent.
        tokens_to_enqueue: list[str] = []
        for name in addressed_agents:
            inv = org.db.mint_thread_invocation(
                thread_id=thread_id, agent_name=name,
                triggering_seq=seq, purpose=ThreadInvocationPurpose.REPLY,
            )
            tokens_to_enqueue.append(inv.invocation_token)

    for token in tokens_to_enqueue:
        await org.thread_queue.put(ThreadJob(org_slug=slug, invocation_token=token))

    return {
        "thread_id": thread_id,
        "started_at": org.db.get_thread(thread_id).started_at.isoformat(),
        "pending_replies": addressed_agents,
    }


# ---------------------------------------------------------------------------
# Shared serializers
# ---------------------------------------------------------------------------


def _thread_row_to_dict(t: ThreadRecord) -> dict:
    return {
        "thread_id": t.id,
        "subject": t.subject,
        "status": t.status.value,
        "started_at": t.started_at.isoformat(),
        "archived_at": t.archived_at.isoformat() if t.archived_at else None,
        "forwarded_from_id": t.forwarded_from_id,
        "forwarded_from_kind": t.forwarded_from_kind,
        "turn_cap": t.turn_cap,
        "turns_used": t.turns_used,
        "summary": t.summary,
        "new_kb_slugs": t.new_kb_slugs,
        "transcript_path": t.transcript_path,
    }


def _msg_to_dict(m) -> dict:
    return {
        "seq": m.seq,
        "speaker": m.speaker,
        "kind": m.kind.value,
        "body_markdown": m.body_markdown,
        "addressed_to": m.addressed_to,
        "decline_reason": m.decline_reason,
        "system_payload": m.system_payload,
        "created_at": m.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Task 20 — GET endpoints
# ---------------------------------------------------------------------------


@router.get("/threads")
async def list_threads_endpoint(
    slug: str,
    org: OrgDep,
    status: str | None = None,
    limit: int = 50,
) -> dict:
    rows = org.db.list_threads(status=status, limit=min(limit, 500))
    return {"threads": [_thread_row_to_dict(t) for t in rows]}


@router.get("/threads/{thread_id}")
async def get_thread_endpoint(
    slug: str,
    thread_id: str,
    org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    participants = [p.agent_name for p in org.db.list_thread_participants(thread_id)]
    msgs = org.db.list_thread_messages(thread_id, limit=200)
    d = _thread_row_to_dict(t)
    d["participants"] = participants
    d["messages"] = [_msg_to_dict(m) for m in msgs]
    return d


@router.get("/threads/{thread_id}/messages")
async def list_thread_messages_endpoint(
    slug: str,
    thread_id: str,
    org: OrgDep,
    since_seq: int = 0,
    limit: int = 200,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    msgs = org.db.list_thread_messages(thread_id, since_seq=since_seq, limit=min(limit, 1000))
    return {"messages": [_msg_to_dict(m) for m in msgs]}


# ---------------------------------------------------------------------------
# Task 21 — POST /threads/{id}/reply
# ---------------------------------------------------------------------------


class ReplyBody(BaseModel):
    thread_id: str
    invocation_token: str
    speaker: str
    body_markdown: str
    in_response_to_seq: int


def _validate_invocation_token(
    org,
    *,
    token: str,
    expected_agent: str,
    expected_thread_id: str,
    require_purposes: list[ThreadInvocationPurpose],
):
    inv = org.db.get_pending_invocation(token)
    if inv is None:
        any_inv = org.db.get_invocation_any_status(token)
        if any_inv is None:
            raise HTTPException(
                status_code=401,
                detail={"code": "invocation_token_invalid"},
            )
        raise HTTPException(
            status_code=409,
            detail={"code": "invocation_token_consumed", "status": any_inv.status.value},
        )
    if inv.thread_id != expected_thread_id or inv.agent_name != expected_agent:
        raise HTTPException(
            status_code=401,
            detail={"code": "invocation_token_invalid", "reason": "mismatch"},
        )
    if inv.purpose not in require_purposes:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "wrong_invocation_purpose",
                "actual": inv.purpose.value,
                "required": [p.value for p in require_purposes],
            },
        )
    return inv


def _verify_addressed(org, *, thread_id: str, seq: int, speaker: str) -> None:
    m = org.db.get_thread_message_by_seq(thread_id, seq)
    if m is None:
        raise HTTPException(status_code=400, detail={"code": "not_addressed", "reason": "seq missing"})
    addr = m.addressed_to or []
    if addr == ["@all"]:
        return
    if speaker not in addr:
        if m.kind.value == "system" and (m.system_payload or {}).get("agent_name") == speaker:
            return
        raise HTTPException(status_code=400, detail={"code": "not_addressed"})


@router.post("/threads/{thread_id}/reply")
async def reply_thread_endpoint(
    slug: str, thread_id: str, body: ReplyBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})

    body_text = body.body_markdown.strip()
    if not body_text:
        raise HTTPException(status_code=422, detail={"code": "empty_body"})

    _validate_invocation_token(
        org, token=body.invocation_token,
        expected_agent=body.speaker, expected_thread_id=thread_id,
        require_purposes=[ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP],
    )
    if not org.db.is_thread_participant(thread_id, body.speaker):
        raise HTTPException(status_code=403, detail={"code": "not_participant"})
    _verify_addressed(org, thread_id=thread_id, seq=body.in_response_to_seq, speaker=body.speaker)

    async with org.db_lock:
        inv = org.db.get_pending_invocation(body.invocation_token)
        if inv is None:
            raise HTTPException(status_code=409, detail={"code": "invocation_token_consumed"})
        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker=body.speaker,
            kind=ThreadMessageKind.MESSAGE, body_markdown=body_text,
        )
        org.db.consume_invocation(body.invocation_token)
        org.db.increment_thread_turns_used(thread_id, by=1)
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker=body.speaker,
            addressed_to=None, kind="message",
        )
    return {"thread_id": thread_id, "seq": seq, "kind": "message"}


# ---------------------------------------------------------------------------
# Task 22 — POST /threads/{id}/decline
# ---------------------------------------------------------------------------


class DeclineBody(BaseModel):
    thread_id: str
    invocation_token: str
    speaker: str
    reason: str
    in_response_to_seq: int


@router.post("/threads/{thread_id}/decline")
async def decline_thread_endpoint(
    slug: str, thread_id: str, body: DeclineBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail={"code": "empty_reason"})
    _validate_invocation_token(
        org, token=body.invocation_token,
        expected_agent=body.speaker, expected_thread_id=thread_id,
        require_purposes=[ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP],
    )
    if not org.db.is_thread_participant(thread_id, body.speaker):
        raise HTTPException(status_code=403, detail={"code": "not_participant"})
    _verify_addressed(org, thread_id=thread_id, seq=body.in_response_to_seq, speaker=body.speaker)

    async with org.db_lock:
        if org.db.get_pending_invocation(body.invocation_token) is None:
            raise HTTPException(status_code=409, detail={"code": "invocation_token_consumed"})
        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker=body.speaker,
            kind=ThreadMessageKind.DECLINE, decline_reason=reason,
        )
        org.db.consume_invocation(body.invocation_token)
        org.db.increment_thread_turns_used(thread_id, by=1)
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker=body.speaker,
            addressed_to=None, kind="decline",
        )
    return {"thread_id": thread_id, "seq": seq, "kind": "decline"}


# ---------------------------------------------------------------------------
# Task 23 — POST /threads/{id}/dispatch
# ---------------------------------------------------------------------------


class DispatchBody(BaseModel):
    thread_id: str
    invocation_token: str
    dispatcher: str
    brief: str
    target_agent: str | None = None
    team: str | None = None


@router.post("/threads/{thread_id}/dispatch")
async def dispatch_from_thread_endpoint(
    slug: str, thread_id: str, body: DispatchBody, org: OrgDep, request: Request,
) -> dict:
    state: DaemonState = request.app.state.daemon
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})
    brief = body.brief.strip()
    if not brief:
        raise HTTPException(status_code=422, detail={"code": "empty_brief"})
    if body.team is not None and not body.team.strip():
        raise HTTPException(status_code=422, detail={"code": "empty_team"})
    if body.target_agent is not None and not body.target_agent.strip():
        raise HTTPException(status_code=422, detail={"code": "empty_target_agent"})

    inv = _validate_invocation_token(
        org, token=body.invocation_token,
        expected_agent=body.dispatcher, expected_thread_id=thread_id,
        require_purposes=[ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP],
    )
    if inv.dispatched_task_id is not None:
        raise HTTPException(status_code=409, detail={"code": "dispatch_already_used"})

    if not org.db.is_thread_participant(thread_id, body.dispatcher):
        raise HTTPException(status_code=403, detail={"code": "not_participant"})
    if org.teams is None:
        raise HTTPException(status_code=403, detail={"code": "teams_registry_unavailable"})

    dispatcher = body.dispatcher
    async with org.teams_lock:
        is_manager = org.teams.is_team_manager(dispatcher)
        dispatcher_team = (
            org.teams.team_for_manager(dispatcher) if is_manager
            else org.teams.team_for_agent(dispatcher)
        )
        if dispatcher_team is None:
            raise HTTPException(status_code=403, detail={"code": "dispatcher_team_unknown"})
        effective_team = body.team if body.team is not None else dispatcher_team
        if effective_team != dispatcher_team:
            raise HTTPException(
                status_code=403,
                detail={"code": "cross_team_dispatch_forbidden",
                        "dispatcher_team": dispatcher_team,
                        "requested_team": effective_team},
            )
        effective_target = body.target_agent if body.target_agent is not None else dispatcher
        if not is_manager and effective_target != dispatcher:
            raise HTTPException(
                status_code=403,
                detail={"code": "worker_must_self_dispatch",
                        "dispatcher": dispatcher,
                        "requested_target": effective_target},
            )
        if is_manager:
            team_meta = org.teams.manager_for_team(dispatcher_team)
            in_team = (
                effective_target == team_meta.name
                or effective_target in team_meta.workers
            )
            if not in_team:
                raise HTTPException(
                    status_code=403,
                    detail={"code": "target_not_in_team",
                            "team": dispatcher_team,
                            "requested_target": effective_target},
                )

    org_paths = OrgPaths(root=org.root)
    agent_def = prompt_loader.load_agent(org_paths, effective_target)
    workspace_exists = (org.root / "workspaces" / effective_target).exists()
    if agent_def is None or not workspace_exists:
        raise HTTPException(status_code=404, detail={"code": "unknown_agent", "agent": effective_target})

    async with org.db_lock:
        cur_inv = org.db.get_pending_invocation(body.invocation_token)
        if cur_inv is None or cur_inv.dispatched_task_id is not None:
            raise HTTPException(status_code=409, detail={"code": "dispatch_already_used"})
        task_id = org.db.next_task_id()
        org.db.insert_task(TaskRecord(
            id=task_id, brief=brief, team=effective_team,
            assigned_agent=effective_target,
            dispatched_from_thread_id=thread_id,
        ))
        sys_seq = org.db.append_thread_message(
            thread_id=thread_id, speaker=dispatcher,
            kind=ThreadMessageKind.SYSTEM,
            system_payload={
                "kind_tag": "task_dispatched",
                "task_id": task_id,
                "dispatcher": dispatcher,
                "target_agent": effective_target,
                "team": effective_team,
                "brief_preview": brief[:160],
            },
        )
        org.db.record_dispatch_on_invocation(body.invocation_token, task_id=task_id)
        AuditLogger(org.db).log_thread_dispatch(
            thread_id, task_id=task_id, dispatcher=dispatcher,
            target_agent=effective_target, team=effective_team,
        )

    enqueue_task(state, slug, task_id)

    return {
        "task_id": task_id,
        "team": effective_team,
        "assigned_agent": effective_target,
        "dispatched_from_thread_id": thread_id,
        "system_message_seq": sys_seq,
    }


# ---------------------------------------------------------------------------
# Task 24 — POST /threads/{id}/send (founder follow-up)
# ---------------------------------------------------------------------------


class SendBody(BaseModel):
    body_markdown: str
    addressed_to: list[str]


@router.post("/threads/{thread_id}/send")
async def send_thread_endpoint(
    slug: str, thread_id: str, body: SendBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})
    body_text = body.body_markdown.strip()
    if not body_text:
        raise HTTPException(status_code=422, detail={"code": "empty_body"})

    participants = [p.agent_name for p in org.db.list_thread_participants(thread_id)]
    _validate_addressed_to(body.addressed_to, participants)
    addressed = _resolve_addressed_agents(body.addressed_to, participants)

    if t.turns_used + len(addressed) > t.turn_cap:
        raise HTTPException(
            status_code=429,
            detail={"code": "turn_cap_exceeded",
                    "used": t.turns_used, "cap": t.turn_cap,
                    "requested": len(addressed)},
        )

    tokens_to_enqueue: list[str] = []
    async with org.db_lock:
        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.MESSAGE,
            body_markdown=body_text, addressed_to=body.addressed_to,
        )
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker="founder",
            addressed_to=body.addressed_to, kind="message",
        )
        for name in addressed:
            inv = org.db.mint_thread_invocation(
                thread_id=thread_id, agent_name=name,
                triggering_seq=seq, purpose=ThreadInvocationPurpose.REPLY,
            )
            tokens_to_enqueue.append(inv.invocation_token)

    for token in tokens_to_enqueue:
        await org.thread_queue.put(ThreadJob(org_slug=slug, invocation_token=token))

    return {"thread_id": thread_id, "seq": seq, "pending_replies": addressed}


# ---------------------------------------------------------------------------
# Task 25 — POST /threads/{id}/invite
# ---------------------------------------------------------------------------


class InviteBody(BaseModel):
    agent_name: str


@router.post("/threads/{thread_id}/invite")
async def invite_thread_endpoint(
    slug: str, thread_id: str, body: InviteBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})

    org_paths = OrgPaths(root=org.root)
    agent_def = prompt_loader.load_agent(org_paths, body.agent_name)
    workspace_exists = (org.root / "workspaces" / body.agent_name).exists()
    if agent_def is None or not workspace_exists:
        raise HTTPException(status_code=404, detail={"code": "unknown_agent"})

    if t.turns_used + 1 > t.turn_cap:
        raise HTTPException(
            status_code=429,
            detail={"code": "turn_cap_exceeded",
                    "used": t.turns_used, "cap": t.turn_cap, "requested": 1},
        )

    token_to_enqueue: str | None = None
    async with org.db_lock:
        inserted = org.db.add_thread_participant(thread_id, body.agent_name, added_by="founder")
        if not inserted:
            raise HTTPException(status_code=409, detail={"code": "already_participant"})
        sys_seq = org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.SYSTEM,
            system_payload={
                "kind_tag": "participant_added",
                "agent_name": body.agent_name,
                "added_by": "founder",
            },
        )
        AuditLogger(org.db).log_thread_participant_added(
            thread_id, agent_name=body.agent_name, added_by="founder",
        )
        inv = org.db.mint_thread_invocation(
            thread_id=thread_id, agent_name=body.agent_name,
            triggering_seq=sys_seq, purpose=ThreadInvocationPurpose.BOOTSTRAP,
        )
        token_to_enqueue = inv.invocation_token

    await org.thread_queue.put(ThreadJob(org_slug=slug, invocation_token=token_to_enqueue))
    return {"thread_id": thread_id, "agent_name": body.agent_name, "system_message_seq": sys_seq}
