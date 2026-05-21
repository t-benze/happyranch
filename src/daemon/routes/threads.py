"""Thread endpoints — email-style multi-agent workchannel."""
from __future__ import annotations

import json as _json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.routes._org_dep import OrgDep
from src.daemon.runner import enqueue_task
from src.daemon.state import DaemonState
from src.daemon.thread_queue import ThreadJob
from src.infrastructure.audit_logger import AuditLogger
from src.models import (
    TalkStatus,
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

# Special routing literal for addressing the founder. NOT a real agent name —
# it never appears in `thread_participants`, never receives a ThreadInvocation,
# and is permitted only in `recipients` / `addressed_to` on agent-initiated
# composes. Routes via the Feishu notifier and the inbox UI instead.
FOUNDER_LITERAL = "@founder"


async def _publish_thread_event(
    org,
    slug: str,
    *,
    thread_id: str,
    seq: int | None,
    speaker: str,
    kind: str,
    preview: str = "",
    status: str = "open",
) -> None:
    from src.daemon.event_bus import thread_topic, thread_inbox_topic
    await org.event_bus.publish(
        thread_topic(thread_id),
        {
            "thread_id": thread_id,
            "seq": seq,
            "speaker": speaker,
            "kind": kind,
            "preview": (preview or "")[:160],
        },
    )
    await org.event_bus.publish(
        thread_inbox_topic(slug),
        {"thread_id": thread_id, "event_kind": kind, "status": status},
    )


class ComposeBody(BaseModel):
    subject: str
    recipients: list[str]
    body_markdown: str
    addressed_to: list[str] = ["@all"]
    forwarded_from_id: str | None = None
    forwarded_from_kind: str | None = None  # 'thread' | 'talk'


class ComposeAsAgentBody(BaseModel):
    composer: str
    subject: str
    recipients: list[str]
    body_markdown: str
    addressed_to: list[str] = ["@all"]
    task_id: str | None = None
    session_id: str | None = None
    talk_id: str | None = None


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

    if len(addressed_agents) > turn_cap:
        raise HTTPException(
            status_code=429,
            detail={"code": "turn_cap_exceeded",
                    "used": 0, "cap": turn_cap,
                    "requested": len(addressed_agents)},
        )

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

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=seq, speaker="founder",
        kind="message", preview=body_text, status="open",
    )

    return {
        "thread_id": thread_id,
        "started_at": org.db.get_thread(thread_id).started_at.isoformat(),
        "pending_replies": addressed_agents,
    }


# ---------------------------------------------------------------------------
# Task 6 — POST /threads/compose-as-agent (agent-initiated threads)
# NOTE: This route must be registered BEFORE /threads/{thread_id} routes so
# FastAPI does not match the literal "compose-as-agent" as a thread_id param.
# ---------------------------------------------------------------------------


async def _maybe_notify_founder_addressed(
    org, *, thread_id: str, subject: str, composer: str,
    body_text: str, addressed_to: list[str],
) -> bool:
    """Push a Feishu card if @founder addressed and org has Feishu configured.

    Returns True iff an attempt was made (delivery failures are swallowed and
    audited, but the caller still reports `founder_notified: true`).

    Task 11 will fill in the real notifier call.
    """
    return False


@router.post("/threads/compose-as-agent")
async def compose_thread_as_agent(
    slug: str, body: ComposeAsAgentBody, org: OrgDep, request: Request
) -> dict:
    state: DaemonState = request.app.state.daemon

    subject = body.subject.strip()
    if not subject:
        raise HTTPException(status_code=422, detail={"code": "empty_subject"})
    body_text = body.body_markdown.strip()
    if not body_text:
        raise HTTPException(status_code=422, detail={"code": "empty_body"})
    if not body.recipients:
        raise HTTPException(status_code=422, detail={"code": "empty_recipients"})

    # Composer must be an approved agent with a workspace.
    org_paths = OrgPaths(root=org.root)
    composer_def = prompt_loader.load_agent(org_paths, body.composer)
    composer_workspace = (org.root / "workspaces" / body.composer).exists()
    if composer_def is None or not composer_workspace:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_composer", "agent": body.composer},
        )

    # Exactly one binding (task XOR talk).
    has_task = body.task_id is not None
    has_talk = body.talk_id is not None
    if not has_task and not has_talk:
        raise HTTPException(status_code=422, detail={"code": "binding_required"})
    if has_task and has_talk:
        raise HTTPException(status_code=422, detail={"code": "binding_ambiguous"})
    if has_task and not body.session_id:
        raise HTTPException(status_code=422, detail={"code": "binding_required", "missing": "session_id"})

    # Task binding: task exists, composer == assigned_agent, active session matches,
    # task in {pending, in_progress}.
    if has_task:
        task = org.db.get_task(body.task_id)
        if task is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_task", "task_id": body.task_id})
        if task.assigned_agent != body.composer:
            raise HTTPException(
                status_code=403,
                detail={"code": "composer_not_task_owner",
                        "composer": body.composer, "assigned_agent": task.assigned_agent},
            )
        active_sid = org.sessions.get_active(body.task_id, body.composer)
        if active_sid is None or active_sid != body.session_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "session_mismatch", "active": active_sid, "got": body.session_id},
            )
        if task.status.value not in ("pending", "in_progress"):
            raise HTTPException(
                status_code=400,
                detail={"code": "task_not_active", "status": task.status.value},
            )

    # Talk binding: talk exists, OPEN, owned by composer.
    if has_talk:
        talk = org.db.get_talk(body.talk_id)
        if talk is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_talk", "talk_id": body.talk_id})
        if talk.status != TalkStatus.OPEN:
            raise HTTPException(
                status_code=400,
                detail={"code": "talk_not_open", "status": talk.status.value},
            )
        if talk.agent_name != body.composer:
            raise HTTPException(
                status_code=403,
                detail={"code": "composer_not_talk_owner",
                        "composer": body.composer, "talk_agent": talk.agent_name},
            )

    # Dedupe recipients (preserve order).
    seen: set[str] = set()
    recipients: list[str] = []
    for name in body.recipients:
        if name in seen:
            continue
        seen.add(name)
        recipients.append(name)

    # Validate each non-@founder recipient is approved with a workspace.
    for name in recipients:
        if name == FOUNDER_LITERAL:
            continue
        agent_def = prompt_loader.load_agent(org_paths, name)
        workspace_exists = (org.root / "workspaces" / name).exists()
        if agent_def is None or not workspace_exists:
            raise HTTPException(
                status_code=404,
                detail={"code": "unknown_agent", "agent": name},
            )

    # External-recipients rule: recipients minus composer must be non-empty OR
    # @founder must appear in addressed_to (resolved if @all).
    # NOTE: `external` includes @founder by design — used only for the empty-
    # check below. Do NOT reuse this list to mint ThreadInvocations in Task 10;
    # @founder is not a subprocess and must be excluded from the invocation set.
    external = [r for r in recipients if r != body.composer]
    addressed_includes_founder = (
        FOUNDER_LITERAL in body.addressed_to
        or (body.addressed_to == ["@all"] and FOUNDER_LITERAL in recipients)
    )
    if not external and not addressed_includes_founder:
        raise HTTPException(status_code=422, detail={"code": "empty_external_recipients"})

    # addressed_to: either ["@all"] or non-empty subset of recipients.
    _validate_addressed_to(body.addressed_to, recipients)

    org_cfg = load_org_config(org_paths)
    turn_cap = org_cfg.threads_default_turn_cap

    # Resolve the addressee set:
    # - @all → every recipient (including @founder if present, including composer);
    # - otherwise the explicit list.
    if body.addressed_to == ["@all"]:
        resolved = list(recipients)
    else:
        resolved = list(body.addressed_to)
    # Concrete agent invocations exclude both @founder and the composer
    # (composer is already running; @founder isn't a subprocess).
    addressed_agents = [
        a for a in resolved
        if a != FOUNDER_LITERAL and a != body.composer
    ]
    founder_in_addressed = FOUNDER_LITERAL in resolved

    if len(addressed_agents) > turn_cap:
        raise HTTPException(
            status_code=429,
            detail={"code": "turn_cap_exceeded",
                    "used": 0, "cap": turn_cap,
                    "requested": len(addressed_agents)},
        )

    composed_from_task_id = body.task_id if has_task else None
    composed_from_talk_id = body.talk_id if has_talk else None

    async with org.db_lock:
        thread_id = org.db.next_thread_id()
        org.db.insert_thread(ThreadRecord(
            id=thread_id, subject=subject, turn_cap=turn_cap,
            composed_by=body.composer,
            composed_from_task_id=composed_from_task_id,
            composed_from_talk_id=composed_from_talk_id,
        ))
        # Composer + every recipient become participants. @founder is NOT a
        # row (spec §3.3); skip it when iterating recipients. The composer
        # is added once explicitly; the loop skips them if they're also in
        # recipients to avoid a duplicate insert (which would silently no-op
        # but is wasteful).
        org.db.add_thread_participant(thread_id, body.composer, added_by=body.composer)
        for name in recipients:
            if name == FOUNDER_LITERAL or name == body.composer:
                continue
            org.db.add_thread_participant(thread_id, name, added_by=body.composer)

        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker=body.composer,
            kind=ThreadMessageKind.MESSAGE,
            body_markdown=body_text, addressed_to=body.addressed_to,
        )
        AuditLogger(org.db).log_thread_started(
            thread_id,
            subject=subject,
            initial_recipients=recipients,
            forwarded_from_id=None,
            composed_by=body.composer,
            composed_from_task_id=composed_from_task_id,
            composed_from_talk_id=composed_from_talk_id,
        )
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker=body.composer,
            addressed_to=body.addressed_to, kind="message",
        )
        if founder_in_addressed:
            AuditLogger(org.db).log_thread_founder_addressed(
                thread_id, seq=seq, speaker=body.composer, notify_channel="feishu",
            )
        tokens_to_enqueue: list[str] = []
        for name in addressed_agents:
            inv = org.db.mint_thread_invocation(
                thread_id=thread_id, agent_name=name,
                triggering_seq=seq, purpose=ThreadInvocationPurpose.REPLY,
            )
            tokens_to_enqueue.append(inv.invocation_token)

    for tok in tokens_to_enqueue:
        await org.thread_queue.put(ThreadJob(org_slug=slug, invocation_token=tok))

    if founder_in_addressed:
        await _maybe_notify_founder_addressed(
            org, thread_id=thread_id, subject=subject, composer=body.composer,
            body_text=body_text, addressed_to=body.addressed_to,
        )
    founder_notified = founder_in_addressed

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=seq, speaker=body.composer,
        kind="message", preview=body_text, status="open",
    )

    return {
        "thread_id": thread_id,
        "started_at": org.db.get_thread(thread_id).started_at.isoformat(),
        "composed_by": body.composer,
        "composed_from_task_id": composed_from_task_id,
        "composed_from_talk_id": composed_from_talk_id,
        "pending_replies": addressed_agents,
        "founder_notified": founder_notified,
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
        "composed_by": t.composed_by,
        "composed_from_task_id": t.composed_from_task_id,
        "composed_from_talk_id": t.composed_from_talk_id,
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


# ---------------------------------------------------------------------------
# Task 31 — SSE endpoints (/threads/events + /threads/{id}/tail)
# NOTE: /threads/events must be registered BEFORE /threads/{thread_id} so
# FastAPI does not match the literal "events" as a thread_id path param.
# ---------------------------------------------------------------------------


@router.get("/threads/events")
async def threads_inbox_events_endpoint(
    slug: str,
    org: OrgDep,
    request: Request,
) -> StreamingResponse:
    async def gen():
        from src.daemon.event_bus import thread_inbox_topic
        async for event in org.event_bus.subscribe(thread_inbox_topic(slug)):
            yield f"data: {_json.dumps(event)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/threads/{thread_id}/tail")
async def tail_thread_endpoint(
    slug: str,
    thread_id: str,
    org: OrgDep,
    request: Request,
    since_seq: int = 0,
) -> StreamingResponse:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})

    async def gen():
        # Replay missed messages first (not via the bus — directly from DB).
        for m in org.db.list_thread_messages(thread_id, since_seq=since_seq, limit=1000):
            yield f"data: {_json.dumps(_msg_to_dict(m))}\n\n"
        # Live updates via the event bus.
        from src.daemon.event_bus import thread_topic
        async for event in org.event_bus.subscribe(thread_topic(thread_id)):
            yield f"data: {_json.dumps(event)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


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


def _pending_reply_load(org, thread_id: str) -> int:
    """Count pending REPLY and BOOTSTRAP invocations on a thread.

    These obligations will increment turns_used when they land (or auto-
    decline). CLOSE_OUT invocations are excluded — they don't count toward
    turns_used per spec §5.10.1.
    """
    from src.models import ThreadInvocationStatus as _TIS
    pending = org.db.list_thread_invocations(thread_id, status=_TIS.PENDING)
    return sum(
        1 for inv in pending
        if inv.purpose in {ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP}
    )


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
    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=seq, speaker=body.speaker,
        kind="message", preview=body_text, status="open",
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
    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=seq, speaker=body.speaker,
        kind="decline", preview=reason, status="open",
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

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=sys_seq, speaker=dispatcher,
        kind="system", preview=brief[:160], status="open",
    )

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

    pending_load = _pending_reply_load(org, thread_id)
    projected = t.turns_used + pending_load + len(addressed)
    if projected > t.turn_cap:
        raise HTTPException(
            status_code=429,
            detail={"code": "turn_cap_exceeded",
                    "used": t.turns_used, "pending": pending_load,
                    "cap": t.turn_cap, "requested": len(addressed)},
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

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=seq, speaker="founder",
        kind="message", preview=body_text, status="open",
    )

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

    pending_load = _pending_reply_load(org, thread_id)
    projected = t.turns_used + pending_load + 1
    if projected > t.turn_cap:
        raise HTTPException(
            status_code=429,
            detail={"code": "turn_cap_exceeded",
                    "used": t.turns_used, "pending": pending_load,
                    "cap": t.turn_cap, "requested": 1},
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

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=sys_seq, speaker="founder",
        kind="system", preview=f"added {body.agent_name}", status="open",
    )

    return {"thread_id": thread_id, "agent_name": body.agent_name, "system_message_seq": sys_seq}


# ---------------------------------------------------------------------------
# Task 26 — POST /threads/{id}/extend
# ---------------------------------------------------------------------------


class ExtendBody(BaseModel):
    new_cap: int


@router.post("/threads/{thread_id}/extend")
async def extend_thread_endpoint(
    slug: str, thread_id: str, body: ExtendBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})
    if body.new_cap <= t.turn_cap:
        raise HTTPException(
            status_code=422,
            detail={"code": "new_cap_must_be_greater",
                    "current": t.turn_cap, "requested": body.new_cap},
        )
    async with org.db_lock:
        prior_cap = t.turn_cap
        org.db.set_thread_turn_cap(thread_id, new_cap=body.new_cap)
        sys_seq = org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.SYSTEM,
            system_payload={"kind_tag": "turn_cap_extended",
                            "prior_cap": prior_cap, "new_cap": body.new_cap},
        )

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=sys_seq, speaker="founder",
        kind="system", preview="turn cap extended", status="open",
    )

    return {"thread_id": thread_id, "turn_cap": body.new_cap}


# ---------------------------------------------------------------------------
# Task 28 — POST /threads/{id}/archive (Phase A) + Task 29 Phase B finalizer
# ---------------------------------------------------------------------------


class ArchiveBody(BaseModel):
    summary: str
    request_close_outs: bool = True


@router.post("/threads/{thread_id}/archive", status_code=202)
async def archive_thread_endpoint(
    slug: str, thread_id: str, body: ArchiveBody, org: OrgDep, request: Request,
) -> dict:
    state: DaemonState = request.app.state.daemon
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is ThreadStatus.ARCHIVED:
        return {"thread_id": thread_id, "status": "archived",
                "transcript_path": t.transcript_path, "idempotent": True}
    if t.status is ThreadStatus.ABANDONED:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})
    if t.status is ThreadStatus.ARCHIVING:
        raise HTTPException(
            status_code=409,
            detail={"code": "archive_in_progress",
                    "archive_requested_at": t.archive_requested_at.isoformat() if t.archive_requested_at else None},
        )
    summary = body.summary.strip()

    close_out_tokens: list[str] = []
    async with org.db_lock:
        org.db.reap_pending_invocations(
            thread_id,
            purposes=[ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP],
            decline_reason="archive_started",
        )
        org.db.set_thread_status(
            thread_id, status=ThreadStatus.ARCHIVING, summary=summary,
        )
        participants = [p.agent_name for p in org.db.list_thread_participants(thread_id)]
        sys_seq = org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.SYSTEM,
            system_payload={"kind_tag": "archive_requested", "summary": summary},
        )
        AuditLogger(org.db).log_thread_archive_requested(
            thread_id, close_out_count=len(participants) if body.request_close_outs else 0,
        )
        if body.request_close_outs:
            for name in participants:
                inv = org.db.mint_thread_invocation(
                    thread_id=thread_id, agent_name=name,
                    triggering_seq=sys_seq, purpose=ThreadInvocationPurpose.CLOSE_OUT,
                )
                close_out_tokens.append(inv.invocation_token)

    for token in close_out_tokens:
        await org.thread_queue.put(ThreadJob(org_slug=slug, invocation_token=token))

    # Phase B: spawn the background finalizer (wired in Task 29).
    cfg = load_org_config(OrgPaths(root=org.root))
    state.thread_finalizers.spawn_finalizer(
        slug, thread_id,
        org_state=org,
        close_out_wait_seconds=cfg.threads_close_out_wait_seconds,
    )

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=sys_seq, speaker="founder",
        kind="system", preview="archiving", status="archiving",
    )

    return {
        "thread_id": thread_id,
        "status": "archiving",
        "close_out_count": len(close_out_tokens),
        "transcript_path": None,
    }


# ---------------------------------------------------------------------------
# Task 27 — POST /threads/{id}/abandon
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Task 30 — POST /threads/{id}/close-out
# ---------------------------------------------------------------------------


class CloseOutLearning(BaseModel):
    text: str


class CloseOutBody(BaseModel):
    thread_id: str
    invocation_token: str
    agent: str
    learnings: list[CloseOutLearning] = []
    kb_slugs: list[str] = []


@router.post("/threads/{thread_id}/close-out")
async def close_out_thread_endpoint(
    slug: str, thread_id: str, body: CloseOutBody, org: OrgDep,
) -> dict:
    from src.infrastructure.kb_store import KBStore, NotFound as KBNotFound
    from src.daemon.routes.agents import _append_to_learnings_file

    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status not in {ThreadStatus.OPEN, ThreadStatus.ARCHIVING}:
        raise HTTPException(status_code=400, detail={"code": "thread_already_finalized"})
    _validate_invocation_token(
        org, token=body.invocation_token,
        expected_agent=body.agent, expected_thread_id=thread_id,
        require_purposes=[ThreadInvocationPurpose.CLOSE_OUT],
    )
    if not org.db.is_thread_participant(thread_id, body.agent):
        raise HTTPException(status_code=403, detail={"code": "not_participant"})

    # Validate KB slugs exist (read-only, before acquiring any lock).
    kb = KBStore(org.root / "kb")
    for kb_slug in body.kb_slugs:
        try:
            kb.read_entry(kb_slug)
        except KBNotFound:
            raise HTTPException(
                status_code=400,
                detail={"code": "kb_slug_not_found", "slug": kb_slug},
            )

    # Atomic token consume + DB updates under the lock. File writes happen
    # AFTER the lock to keep the critical section short, but ONLY if consume
    # succeeded — which guarantees this request is the unique winner for
    # this token.
    async with org.db_lock:
        if not org.db.consume_invocation(body.invocation_token):
            raise HTTPException(status_code=409, detail={"code": "invocation_token_consumed"})
        for kb_slug in body.kb_slugs:
            org.db.add_thread_kb_slug(thread_id, kb_slug)
        org.db.add_thread_learnings_count(thread_id, count=len(body.learnings))
        AuditLogger(org.db).log_thread_close_out_received(
            thread_id, agent=body.agent,
            new_learnings_count=len(body.learnings),
            new_kb_slugs=body.kb_slugs,
        )

    # Now safe: token is consumed, no other request can reach this point with
    # the same token.
    workspace = org.root / "workspaces" / body.agent
    learnings_path = workspace / "learnings.md"
    for entry in body.learnings:
        _append_to_learnings_file(learnings_path, body.agent, entry.text)

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=None, speaker=body.agent,
        kind="system", preview="close-out received", status=t.status.value,
    )

    return {
        "thread_id": thread_id, "agent": body.agent,
        "new_learnings_count": len(body.learnings),
        "new_kb_slugs": body.kb_slugs,
    }


class AbandonBody(BaseModel):
    reason: str


@router.post("/threads/{thread_id}/abandon")
async def abandon_thread_endpoint(
    slug: str, thread_id: str, body: AbandonBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status in {ThreadStatus.ARCHIVED, ThreadStatus.ABANDONED}:
        return {"thread_id": thread_id, "status": t.status.value, "idempotent": True}
    reason = body.reason.strip() or "abandoned"
    async with org.db_lock:
        org.db.set_thread_status(thread_id, status=ThreadStatus.ABANDONED)
        org.db.reap_pending_invocations(
            thread_id, purposes=None, decline_reason="thread_abandoned",
        )
        AuditLogger(org.db).log_thread_abandoned(thread_id, reason=reason)

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=None, speaker="founder",
        kind="system", preview="abandoned", status="abandoned",
    )

    return {"thread_id": thread_id, "status": "abandoned"}
