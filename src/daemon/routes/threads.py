"""Thread endpoints — email-style multi-agent workchannel."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.routes._org_dep import OrgDep
from src.daemon.state import DaemonState
from src.daemon.thread_queue import ThreadJob
from src.infrastructure.audit_logger import AuditLogger
from src.models import (
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
