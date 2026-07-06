"""Thread endpoints — email-style multi-agent workchannel."""
from __future__ import annotations

import json as _json
import mimetypes
import re
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from runtime.daemon.auth import require_token
from runtime.daemon.routes._doctrine import SELF_DISPATCH_HINT
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.runner import enqueue_task
from runtime.daemon.state import DaemonState
from runtime.daemon.thread_queue import ThreadJob
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.artifact_store import ArtifactStore, InvalidArtifactName
from runtime.infrastructure.thread_scoped_attachment_store import MAX_THREAD_ATTACHMENT_BYTES
from runtime.infrastructure.thread_store import render_transcript_body
from runtime.models import (
    ResponderStatusEntry,
    TaskRecord,
    ThreadAttachment,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessageKind,
    ThreadRecord,
    ThreadStatus,
)
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import load_org_config

router = APIRouter(dependencies=[require_token()])

# Special routing literal for addressing the founder. NOT a real agent name —
# it never appears in `thread_participants`, never receives a ThreadInvocation,
# and is permitted only in `recipients` on agent-initiated composes.
# Routes via the inbox UI instead.
FOUNDER_LITERAL = "@founder"
MAX_THREAD_ATTACHMENTS = 5


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
    from runtime.daemon.event_bus import thread_topic, thread_inbox_topic
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


def _create_agent_thread_locked(
    org,
    *,
    composer: str,
    subject: str,
    body_text: str | None,
    recipients: list[str],
    turn_cap: int,
    attachments: list[ThreadAttachment] | None = None,
    composed_from_task_id: str | None = None,
    composed_from_dream_id: str | None = None,
) -> tuple[str, int, list[str], list[str]]:
    """DB-write core of an agent-initiated compose. Caller MUST hold org.db_lock.

    Inserts the thread, adds the composer plus every non-@founder recipient as a
    participant, appends the opening message, increments turns, emits
    thread_started + thread_message_sent audit rows, and mints REPLY invocations
    for every addressed agent (recipients minus @founder minus the composer).

    Returns (thread_id, seq, tokens_to_enqueue, addressed_agents). The caller
    enqueues the tokens and publishes the thread event after releasing the lock.

    Extracted so non-HTTP callers (e.g. dream completion creating a founder-only
    thread) reuse the exact participant/turn/audit semantics without going
    through the authenticated compose route.
    """
    seen: set[str] = set()
    deduped: list[str] = []
    for name in recipients:
        if name in seen:
            continue
        seen.add(name)
        deduped.append(name)
    addressed_agents = [
        name for name in deduped if name != FOUNDER_LITERAL and name != composer
    ]

    thread_id = org.db.next_thread_id()
    org.db.insert_thread(ThreadRecord(
        id=thread_id, subject=subject, turn_cap=turn_cap,
        composed_by=composer,
        composed_from_task_id=composed_from_task_id,
        composed_from_dream_id=composed_from_dream_id,
    ))
    # Composer + every recipient become participants. @founder is NOT a row
    # (spec §3.3); skip it. The composer is added once explicitly; the loop
    # skips them if they're also in recipients to avoid a duplicate insert.
    org.db.add_thread_participant(thread_id, composer, added_by=composer)
    for name in deduped:
        if name == FOUNDER_LITERAL or name == composer:
            continue
        org.db.add_thread_participant(thread_id, name, added_by=composer)

    seq = org.db.append_thread_message(
        thread_id=thread_id, speaker=composer,
        kind=ThreadMessageKind.MESSAGE,
        body_markdown=body_text,
        attachments=attachments or [],
    )
    org.db.increment_thread_turns_used(thread_id, by=1)
    AuditLogger(org.db).log_thread_started(
        thread_id,
        subject=subject,
        initial_recipients=deduped,
        forwarded_from_id=None,
        composed_by=composer,
        composed_from_task_id=composed_from_task_id,
        composed_from_dream_id=composed_from_dream_id,
    )
    AuditLogger(org.db).log_thread_message_sent(
        thread_id, seq=seq, speaker=composer, kind="message",
        attachment_names=[a.artifact_name for a in (attachments or [])],
    )
    tokens_to_enqueue: list[str] = []
    for name in addressed_agents:
        inv = org.db.mint_thread_invocation(
            thread_id=thread_id, agent_name=name,
            triggering_seq=seq, purpose=ThreadInvocationPurpose.REPLY,
        )
        tokens_to_enqueue.append(inv.invocation_token)
    return thread_id, seq, tokens_to_enqueue, addressed_agents


class AttachmentRefBody(BaseModel):
    artifact_name: str = ""
    display_name: str | None = None
    content_type: str | None = None
    # Thread-scoped attachment id (TASK-1616). Mutually exclusive with artifact_name.
    attachment_id: str | None = None


class ComposeBody(BaseModel):
    subject: str
    recipients: list[str]
    body_markdown: str = ""
    attachments: list[AttachmentRefBody] = Field(default_factory=list)
    forwarded_from_id: str | None = None
    forwarded_from_kind: str | None = None  # 'thread'


class ComposeAsAgentBody(BaseModel):
    composer: str
    subject: str
    recipients: list[str]
    body_markdown: str = ""
    attachments: list[AttachmentRefBody] = Field(default_factory=list)
    task_id: str | None = None
    session_id: str | None = None


def _validate_display_name(name: str) -> None:
    if not name or len(name) > 200:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_attachment_display_name", "name": name},
        )
    if "/" in name or "\\" in name or any(ord(ch) < 32 for ch in name):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_attachment_display_name", "name": name},
        )


def _normalize_content_type(content_type: str | None, artifact_name: str) -> str | None:
    if content_type is None or not content_type.strip():
        return mimetypes.guess_type(artifact_name)[0]
    normalized = content_type.strip()
    if len(normalized) > 200 or any(ord(ch) < 32 for ch in normalized):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_attachment_content_type",
                "content_type": content_type,
            },
        )
    return normalized


def _attachments_preview(attachments: list[ThreadAttachment]) -> str:
    if not attachments:
        return ""
    names = ", ".join(a.display_name for a in attachments[:3])
    suffix = "" if len(attachments) <= 3 else f" +{len(attachments) - 3} more"
    file_word = "file" if len(attachments) == 1 else "files"
    return f"Attached {len(attachments)} {file_word}: {names}{suffix}"


def _normalize_attachments(
    org: object,
    refs: list[AttachmentRefBody] | None,
    *,
    uploaded_by: str,
) -> list[ThreadAttachment]:
    if not refs:
        return []
    if len(refs) > MAX_THREAD_ATTACHMENTS:
        raise HTTPException(
            status_code=422,
            detail={"code": "too_many_attachments", "max": MAX_THREAD_ATTACHMENTS},
        )
    seen: set[str] = set()
    store = ArtifactStore(OrgPaths(org.root).artifacts_dir)
    out: list[ThreadAttachment] = []
    for ref in refs:
        artifact_name = ref.artifact_name.strip()
        if artifact_name in seen:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "duplicate_attachment",
                    "artifact_name": artifact_name,
                },
            )
        seen.add(artifact_name)
        try:
            path = store.path_for(artifact_name)
        except InvalidArtifactName as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_artifact_name",
                    "name": artifact_name,
                    "message": str(exc),
                },
            ) from exc
        if ref.display_name is None:
            display_name = artifact_name
        else:
            display_name = ref.display_name.strip()
        _validate_display_name(display_name)
        content_type = _normalize_content_type(ref.content_type, artifact_name)
        if not path.exists():
            raise HTTPException(
                status_code=404,
                detail={"code": "artifact_not_found", "name": artifact_name},
            )
        stat = path.stat()
        out.append(
            ThreadAttachment(
                artifact_name=artifact_name,
                display_name=display_name,
                size_bytes=stat.st_size,
                content_type=content_type,
                uploaded_by=uploaded_by,
            )
        )
    return out


def _normalize_all_attachments(
    org: object,
    refs: list[AttachmentRefBody] | None,
    *,
    uploaded_by: str,
    thread_id: str | None = None,
) -> list[ThreadAttachment]:
    """Dispatch attachment refs to shared-artifact or thread-scoped normalizer.

    Splits the ref list by whether `attachment_id` is set (thread-scoped) or
    `artifact_name` is non-empty (shared artifact). Both types can coexist in
    a single message payload.
    """
    if not refs:
        return []

    shared_refs: list[AttachmentRefBody] = []
    thread_refs: list[ThreadAttachmentRefBody] = []
    for ref in refs:
        if ref.attachment_id:
            if ref.artifact_name:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "ambiguous_attachment_ref",
                        "message": "Provide either artifact_name or attachment_id, not both.",
                    },
                )
            thread_refs.append(
                ThreadAttachmentRefBody(
                    attachment_id=ref.attachment_id,
                    display_name=ref.display_name,
                    content_type=ref.content_type,
                )
            )
        else:
            if not ref.artifact_name:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "empty_attachment_ref",
                        "message": "Provide artifact_name or attachment_id.",
                    },
                )
            shared_refs.append(ref)

    # Validate total count across both types.
    total = len(shared_refs) + len(thread_refs)
    if total > MAX_THREAD_ATTACHMENTS:
        raise HTTPException(
            status_code=422,
            detail={"code": "too_many_attachments", "max": MAX_THREAD_ATTACHMENTS},
        )

    # Dedupe across both types.
    all_ids: set[str] = set()
    for ref in shared_refs:
        if ref.artifact_name in all_ids:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "duplicate_attachment",
                    "artifact_name": ref.artifact_name,
                },
            )
        all_ids.add(ref.artifact_name)
    for ref in thread_refs:
        if ref.attachment_id in all_ids:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "duplicate_attachment",
                    "attachment_id": ref.attachment_id,
                },
            )
        all_ids.add(ref.attachment_id)

    out: list[ThreadAttachment] = []
    if shared_refs:
        out.extend(_normalize_attachments(org, shared_refs, uploaded_by=uploaded_by))
    if thread_refs and thread_id is not None:
        out.extend(
            _normalize_thread_attachments(
                org, thread_refs, thread_id=thread_id, uploaded_by=uploaded_by,
            )
        )
    return out


def _normalize_message_body(
    body_markdown: str | None,
    attachments: list[ThreadAttachment],
) -> str | None:
    body_text = (body_markdown or "").strip()
    if not body_text and not attachments:
        raise HTTPException(status_code=422, detail={"code": "empty_body"})
    return body_text or None


async def _compose_thread_multipart(
    slug: str, org: OrgDep, request: Request, *, uploaded_by: str,
) -> dict:
    """Handle compose as multipart/form-data for thread-scoped attachment uploads."""
    import json as _json
    from fastapi import UploadFile

    # Parse multipart form: expect a 'body' field with JSON, and optional 'files' fields.
    form = await request.form()
    body_raw = form.get("body")
    if body_raw is None:
        raise HTTPException(
            status_code=422, detail={"code": "missing_body_field"},
        )
    try:
        body_data = _json.loads(
            body_raw if isinstance(body_raw, str) else await body_raw.read()
        )
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_body_json"},
        )
    body = ComposeBody(**body_data)
    file_fields = [
        v for k, v in form.multi_items()
        if k == "files" and hasattr(v, "read")
    ]

    state: DaemonState = request.app.state.daemon
    subject = body.subject.strip()
    if not subject:
        raise HTTPException(status_code=422, detail={"code": "empty_subject"})
    if not body.recipients:
        raise HTTPException(status_code=422, detail={"code": "empty_recipients"})

    # Validate shared artifact refs (if any) via existing path.
    shared_attachments = _normalize_attachments(
        org, body.attachments, uploaded_by=uploaded_by,
    )

    # Validate recipients.
    org_paths = OrgPaths(root=org.root)
    for name in body.recipients:
        agent_def = prompt_loader.load_agent(org_paths, name)
        workspace_exists = (org.root / "workspaces" / name).exists()
        if agent_def is None or not workspace_exists:
            raise HTTPException(
                status_code=404,
                detail={"code": "unknown_agent", "agent": name},
            )

    if (body.forwarded_from_id is None) != (body.forwarded_from_kind is None):
        raise HTTPException(
            status_code=422, detail={"code": "forwarded_fields_must_pair"},
        )
    if body.forwarded_from_kind not in (None, "thread"):
        raise HTTPException(
            status_code=422, detail={"code": "forwarded_kind_invalid"},
        )
    if body.forwarded_from_id is not None:
        if body.forwarded_from_kind == "thread":
            src = org.db.get_thread(body.forwarded_from_id)
        else:
            src = None
        if src is None:
            raise HTTPException(
                status_code=404, detail={"code": "forwarded_source_not_found"},
            )

    org_cfg = load_org_config(org_paths)
    turn_cap = org_cfg.threads_default_turn_cap
    addressed_agents = list(body.recipients)

    total_file_count = len(file_fields) + len(shared_attachments)
    if total_file_count > MAX_THREAD_ATTACHMENTS:
        raise HTTPException(
            status_code=422,
            detail={"code": "too_many_attachments", "max": MAX_THREAD_ATTACHMENTS},
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

        # Store uploaded files in thread-scoped store.
        thread_attachments: list[ThreadAttachment] = []
        for file_field in file_fields:
            content = await file_field.read()
            if len(content) > MAX_THREAD_ATTACHMENT_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "code": "attachment_too_large",
                        "max_bytes": MAX_THREAD_ATTACHMENT_BYTES,
                    },
                )
            display_name = (
                file_field.filename if hasattr(file_field, "filename") else "attachment"
            ) or "attachment"
            _validate_display_name(display_name)
            content_type = (
                file_field.content_type
                if hasattr(file_field, "content_type")
                else None
            ) or mimetypes.guess_type(display_name)[0]
            attachment_id = org.db.next_thread_attachment_id()
            size_bytes = _attachment_store(org).put(
                thread_id, attachment_id, content,
            )
            org.db.insert_thread_scoped_attachment(
                attachment_id=attachment_id,
                thread_id=thread_id,
                display_name=display_name,
                size_bytes=size_bytes,
                content_type=content_type,
                uploaded_by=uploaded_by,
            )
            thread_attachments.append(
                ThreadAttachment(
                    artifact_name="",
                    display_name=display_name,
                    size_bytes=size_bytes,
                    content_type=content_type,
                    uploaded_by=uploaded_by,
                    thread_attachment_id=attachment_id,
                )
            )

        all_attachments = shared_attachments + thread_attachments
        body_text = _normalize_message_body(body.body_markdown, all_attachments)
        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.MESSAGE,
            body_markdown=body_text,
            attachments=all_attachments,
        )
        org.db.increment_thread_turns_used(thread_id, by=1)
        AuditLogger(org.db).log_thread_started(
            thread_id,
            subject=subject,
            initial_recipients=body.recipients,
            forwarded_from_id=body.forwarded_from_id,
        )
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker="founder", kind="message",
            attachment_names=[
                a.artifact_name or a.thread_attachment_id or ""
                for a in all_attachments
            ],
        )
        tokens_to_enqueue: list[str] = []
        for name in addressed_agents:
            inv = org.db.mint_thread_invocation(
                thread_id=thread_id, agent_name=name,
                triggering_seq=seq, purpose=ThreadInvocationPurpose.REPLY,
            )
            tokens_to_enqueue.append(inv.invocation_token)

    for token in tokens_to_enqueue:
        await org.thread_queue.put(
            ThreadJob(org_slug=slug, invocation_token=token),
        )

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=seq, speaker="founder",
        kind="message",
        preview=body_text or _attachments_preview(all_attachments),
        status="open",
    )

    return {
        "thread_id": thread_id,
        "started_at": org.db.get_thread(thread_id).started_at.isoformat(),
        "pending_replies": addressed_agents,
    }


@router.post("/threads")
async def compose_thread(
    slug: str, org: OrgDep, request: Request
) -> dict:
    state: DaemonState = request.app.state.daemon

    # Support both JSON and multipart/form-data for thread-scoped attachments (TASK-1616).
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        return await _compose_thread_multipart(
            slug, org, request, uploaded_by="founder",
        )
    # JSON path (backward compatible).
    body_data = await request.json()
    body = ComposeBody(**body_data)

    subject = body.subject.strip()
    if not subject:
        raise HTTPException(status_code=422, detail={"code": "empty_subject"})
    if not body.recipients:
        raise HTTPException(status_code=422, detail={"code": "empty_recipients"})
    attachments = _normalize_attachments(
        org, body.attachments, uploaded_by="founder",
    )
    body_text = _normalize_message_body(body.body_markdown, attachments)

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

    # Validate forwarded source if set.
    if (body.forwarded_from_id is None) != (body.forwarded_from_kind is None):
        raise HTTPException(
            status_code=422,
            detail={"code": "forwarded_fields_must_pair"},
        )
    if body.forwarded_from_kind not in (None, "thread"):
        raise HTTPException(
            status_code=422,
            detail={"code": "forwarded_kind_invalid"},
        )
    if body.forwarded_from_id is not None:
        if body.forwarded_from_kind == "thread":
            src = org.db.get_thread(body.forwarded_from_id)
        else:
            # Non-thread forwarded_from_kind is no longer supported.
            src = None
        if src is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "forwarded_source_not_found"},
            )

    # Turn cap from org config (default 500).
    org_cfg = load_org_config(org_paths)
    turn_cap = org_cfg.threads_default_turn_cap

    # Broadcast model: every recipient (== future participant) gets a REPLY
    # invocation. The founder is not a participant; no founder mint.
    # Self-exclusion is moot at compose time (founder is the speaker and is
    # not in recipients).
    addressed_agents = list(body.recipients)

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
            body_markdown=body_text,
            attachments=attachments,
        )
        org.db.increment_thread_turns_used(thread_id, by=1)
        AuditLogger(org.db).log_thread_started(
            thread_id,
            subject=subject,
            initial_recipients=body.recipients,
            forwarded_from_id=body.forwarded_from_id,
        )
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker="founder", kind="message",
            attachment_names=[a.artifact_name for a in attachments],
        )
        # Broadcast: mint REPLY for every recipient (participant). The founder
        # is the speaker and is not in recipients, so she is never minted.
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
        kind="message",
        preview=body_text or _attachments_preview(attachments),
        status="open",
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


async def _compose_agent_thread_multipart(
    slug: str, org: OrgDep, request: Request,
) -> dict:
    """Handle compose-as-agent as multipart/form-data for thread-scoped attachment uploads."""
    import json as _json
    from fastapi import UploadFile

    form = await request.form()
    body_raw = form.get("body")
    if body_raw is None:
        raise HTTPException(
            status_code=422, detail={"code": "missing_body_field"},
        )
    try:
        body_data = _json.loads(
            body_raw if isinstance(body_raw, str) else await body_raw.read()
        )
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=422, detail={"code": "invalid_body_json"},
        )
    body = ComposeAsAgentBody(**body_data)
    file_fields = [
        v for k, v in form.multi_items()
        if k == "files" and hasattr(v, "read")
    ]

    subject = body.subject.strip()
    if not subject:
        raise HTTPException(status_code=422, detail={"code": "empty_subject"})
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

    # Task binding.
    if not body.task_id or not body.session_id:
        raise HTTPException(status_code=422, detail={"code": "binding_required"})
    task = org.db.get_task(body.task_id)
    if task is None:
        raise HTTPException(
            status_code=404, detail={"code": "unknown_task", "task_id": body.task_id}
        )
    if task.assigned_agent != body.composer:
        raise HTTPException(
            status_code=403,
            detail={"code": "composer_not_task_owner",
                    "composer": body.composer, "assigned_agent": task.assigned_agent},
        )
    if task.status.value not in ("pending", "in_progress"):
        raise HTTPException(
            status_code=400,
            detail={"code": "task_not_active", "status": task.status.value},
        )
    active_sid = org.sessions.get_active(body.task_id, body.composer)
    if active_sid is None or active_sid != body.session_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "session_mismatch", "active": active_sid, "got": body.session_id},
        )

    # Dedupe recipients.
    seen_rcpt: set[str] = set()
    recipients: list[str] = []
    for name in body.recipients:
        if name in seen_rcpt:
            continue
        seen_rcpt.add(name)
        recipients.append(name)

    # Validate each non-@founder recipient.
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

    external = [r for r in recipients if r != body.composer]
    founder_in_recipients = FOUNDER_LITERAL in recipients
    if not external and not founder_in_recipients:
        raise HTTPException(status_code=422, detail={"code": "empty_external_recipients"})

    org_cfg = load_org_config(org_paths)
    turn_cap = org_cfg.threads_default_turn_cap

    # Validate shared artifact refs (if any).
    shared_attachments = _normalize_attachments(
        org, body.attachments, uploaded_by=body.composer,
    )

    total_file_count = len(file_fields) + len(shared_attachments)
    if total_file_count > MAX_THREAD_ATTACHMENTS:
        raise HTTPException(
            status_code=422,
            detail={"code": "too_many_attachments", "max": MAX_THREAD_ATTACHMENTS},
        )

    # Inline thread creation (same as _create_agent_thread_locked but we store
    # thread-scoped files before appending the message).
    addressed_agents = [
        name for name in recipients if name != FOUNDER_LITERAL and name != body.composer
    ]

    async with org.db_lock:
        thread_id = org.db.next_thread_id()
        org.db.insert_thread(ThreadRecord(
            id=thread_id, subject=subject, turn_cap=turn_cap,
            composed_by=body.composer,
            composed_from_task_id=body.task_id,
        ))
        org.db.add_thread_participant(thread_id, body.composer, added_by=body.composer)
        for name in recipients:
            if name == FOUNDER_LITERAL or name == body.composer:
                continue
            org.db.add_thread_participant(thread_id, name, added_by=body.composer)

        # Store uploaded files in thread-scoped store.
        thread_attachments: list[ThreadAttachment] = []
        for file_field in file_fields:
            content = await file_field.read()
            if len(content) > MAX_THREAD_ATTACHMENT_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "code": "attachment_too_large",
                        "max_bytes": MAX_THREAD_ATTACHMENT_BYTES,
                    },
                )
            display_name = (
                file_field.filename if hasattr(file_field, "filename") else "attachment"
            ) or "attachment"
            _validate_display_name(display_name)
            content_type = (
                file_field.content_type
                if hasattr(file_field, "content_type")
                else None
            ) or mimetypes.guess_type(display_name)[0]
            attachment_id = org.db.next_thread_attachment_id()
            size_bytes = _attachment_store(org).put(
                thread_id, attachment_id, content,
            )
            org.db.insert_thread_scoped_attachment(
                attachment_id=attachment_id,
                thread_id=thread_id,
                display_name=display_name,
                size_bytes=size_bytes,
                content_type=content_type,
                uploaded_by=body.composer,
            )
            thread_attachments.append(
                ThreadAttachment(
                    artifact_name="",
                    display_name=display_name,
                    size_bytes=size_bytes,
                    content_type=content_type,
                    uploaded_by=body.composer,
                    thread_attachment_id=attachment_id,
                )
            )

        all_attachments = shared_attachments + thread_attachments
        body_text = _normalize_message_body(body.body_markdown, all_attachments)

        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker=body.composer,
            kind=ThreadMessageKind.MESSAGE,
            body_markdown=body_text,
            attachments=all_attachments,
        )
        org.db.increment_thread_turns_used(thread_id, by=1)
        AuditLogger(org.db).log_thread_started(
            thread_id,
            subject=subject,
            initial_recipients=recipients,
            forwarded_from_id=None,
            composed_by=body.composer,
            composed_from_task_id=body.task_id,
        )
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker=body.composer, kind="message",
            attachment_names=[
                a.artifact_name or a.thread_attachment_id or ""
                for a in all_attachments
            ],
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

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=seq, speaker=body.composer,
        kind="message",
        preview=body_text or _attachments_preview(all_attachments),
        status="open",
    )

    return {
        "thread_id": thread_id,
        "started_at": org.db.get_thread(thread_id).started_at.isoformat(),
        "composed_by": body.composer,
        "composed_from_task_id": body.task_id,
        "pending_replies": addressed_agents,
    }


@router.post("/threads/compose-as-agent")
async def compose_thread_as_agent(
    slug: str, org: OrgDep, request: Request,
) -> dict:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" in content_type:
        return await _compose_agent_thread_multipart(slug, org, request)
    # JSON path.
    body_data = await request.json()
    body = ComposeAsAgentBody(**body_data)
    subject = body.subject.strip()
    if not subject:
        raise HTTPException(status_code=422, detail={"code": "empty_subject"})
    attachments = _normalize_attachments(
        org, body.attachments, uploaded_by=body.composer,
    )
    body_text = _normalize_message_body(body.body_markdown, attachments)
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

    # Task binding: task exists, composer owns it, task is active, session matches.
    # Status gate runs BEFORE session gate so a finished task surfaces
    # `task_not_active` rather than `session_mismatch` (a completed task has
    # no active session in normal operation; reporting "session mismatch"
    # would mislead the caller).
    if not body.task_id or not body.session_id:
        raise HTTPException(status_code=422, detail={"code": "binding_required"})
    task = org.db.get_task(body.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_task", "task_id": body.task_id})
    if task.assigned_agent != body.composer:
        raise HTTPException(
            status_code=403,
            detail={"code": "composer_not_task_owner",
                    "composer": body.composer, "assigned_agent": task.assigned_agent},
        )
    if task.status.value not in ("pending", "in_progress"):
        raise HTTPException(
            status_code=400,
            detail={"code": "task_not_active", "status": task.status.value},
        )
    active_sid = org.sessions.get_active(body.task_id, body.composer)
    if active_sid is None or active_sid != body.session_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "session_mismatch", "active": active_sid, "got": body.session_id},
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
    # @founder must appear in recipients.
    external = [r for r in recipients if r != body.composer]
    founder_in_recipients = FOUNDER_LITERAL in recipients
    if not external and not founder_in_recipients:
        raise HTTPException(status_code=422, detail={"code": "empty_external_recipients"})

    org_cfg = load_org_config(org_paths)
    turn_cap = org_cfg.threads_default_turn_cap

    composed_from_task_id = body.task_id

    async with org.db_lock:
        thread_id, seq, tokens_to_enqueue, addressed_agents = _create_agent_thread_locked(
            org,
            composer=body.composer,
            subject=subject,
            body_text=body_text,
            recipients=recipients,
            turn_cap=turn_cap,
            attachments=attachments,
            composed_from_task_id=composed_from_task_id,
        )

    for tok in tokens_to_enqueue:
        await org.thread_queue.put(ThreadJob(org_slug=slug, invocation_token=tok))

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=seq, speaker=body.composer,
        kind="message",
        preview=body_text or _attachments_preview(attachments),
        status="open",
    )

    return {
        "thread_id": thread_id,
        "started_at": org.db.get_thread(thread_id).started_at.isoformat(),
        "composed_by": body.composer,
        "composed_from_task_id": composed_from_task_id,
        "pending_replies": addressed_agents,
    }


# ---------------------------------------------------------------------------
# Shared serializers
# ---------------------------------------------------------------------------


def _wire_status(db_status: str) -> str:
    """Rename DB status values to the spec's wire enum.

    DB: pending | consumed | declined | failed | timeout
    Wire: pending | replied | declined | failed

    consumed → replied (semantic: agent wrote a reply)
    timeout → failed (semantic: agent did not engage successfully; UI
                       doesn't distinguish crash from timeout at this level)
    """
    if db_status == "consumed":
        return "replied"
    if db_status == "timeout":
        return "failed"
    return db_status


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
        "transcript_path": t.transcript_path,
        "composed_by": t.composed_by,
        "composed_from_task_id": t.composed_from_task_id,
        "composed_from_dream_id": t.composed_from_dream_id,
        "last_speaker": t.last_speaker,
    }


def _has_infra_signature(reason: str) -> bool:
    """Return True when *reason* carries an infrastructure-failure marker
    even if it is wrapped in a no_callback/no_callback_after_reprompt prefix."""
    # Parse rc=N from the reason (case-insensitive).
    m = re.search(r'rc=(\d+)', reason, re.IGNORECASE)
    if m and int(m.group(1)) != 0:
        return True
    lower = reason.lower()
    if "529" in lower or "overloaded" in lower:
        return True
    if "quota" in lower or "usage limit" in lower:
        return True
    if "unknown_session" in lower:
        return True
    return False


def _responder_category(db_status: str, decline_reason: str | None) -> str | None:
    """Derive a failure/decline category from the invocation's terminal state.

    Four buckets per THR-071:
    - ``declined`` — agent chose to decline (explicit decline route)
    - ``no_callback`` — agent forgot the terminal callback (clean exit, rc==0,
      no infra markers)
    - ``no_callback_after_reprompt`` — agent forgot even after the nudge
      (clean exit, rc==0, no infra markers)
    - ``infra_fail`` — infrastructure failure (timeout, 529, runner_crash,
      rc!=0, quota, unknown_session, etc.)

    Returns None when no terminal failure category applies (queued/working/replied).
    """
    if db_status == "declined":
        return "declined"
    if db_status in ("failed", "timeout"):
        reason = (decline_reason or "").lower()
        # Check for infra signatures FIRST — a no_callback: rc=1 is still infra.
        if reason.startswith("no_callback_after_reprompt:"):
            return "infra_fail" if _has_infra_signature(reason) else "no_callback_after_reprompt"
        if reason.startswith("no_callback:"):
            return "infra_fail" if _has_infra_signature(reason) else "no_callback"
        # All other failures: timeout, runner_crash, 529, rc=N, etc.
        return "infra_fail"
    return None


def _responder_entry(e: dict) -> ResponderStatusEntry:
    """Build one responder-status wire entry from a grouped invocation dict.

    Splits the DB `pending` state into `queued` (no subprocess yet) vs
    `working` (subprocess started — `started_at` set). Terminal states go
    through `_wire_status` (consumed→replied, timeout→failed).
    """
    db_status = e["status"]
    if db_status == "pending":
        wire = "working" if e.get("started_at") else "queued"
    else:
        wire = _wire_status(db_status)
    return ResponderStatusEntry(
        agent_name=e["agent_name"],
        status=wire,
        responded_at=e["consumed_at"],
        started_at=e.get("started_at"),
        decline_reason=e.get("decline_reason"),
        category=_responder_category(
            db_status,
            e.get("decline_reason"),
        ),
    )


def _msg_to_dict(m, responders: list[dict] | None = None) -> dict:
    d = {
        "seq": m.seq,
        "speaker": m.speaker,
        "kind": m.kind.value,
        "body_markdown": m.body_markdown,
        "decline_reason": m.decline_reason,
        "system_payload": m.system_payload,
        "attachments": [
            attachment.model_dump(mode="json") for attachment in m.attachments
        ],
        "created_at": m.created_at.isoformat(),
    }
    if responders is not None:
        d["responder_status"] = [
            _responder_entry(e).model_dump(mode="json") for e in responders
        ]
    else:
        d["responder_status"] = []
    return d


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
        from runtime.daemon.event_bus import thread_inbox_topic
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
        from runtime.daemon.event_bus import thread_topic
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
    responders_by_seq = org.db.list_invocations_for_thread_grouped_by_seq(thread_id)
    d = _thread_row_to_dict(t)
    d["participants"] = participants
    # Pass responders unconditionally: the grouped query returns reply
    # invocations (which hang off MESSAGE rows) and task_followup invocations
    # (which hang off the SYSTEM row that wakes a dispatched agent). The two are
    # disjoint by triggering-row kind, so a blanket lookup surfaces the followup
    # in-flight strip on its system row without contaminating message rows.
    d["messages"] = [
        _msg_to_dict(m, responders=responders_by_seq.get(m.seq))
        for m in msgs
    ]
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
    responders_by_seq = org.db.list_invocations_for_thread_grouped_by_seq(thread_id)
    # Unconditional responders lookup — see get_thread_endpoint: reply and
    # task_followup invocations are disjoint by triggering-row kind, so the
    # blanket lookup surfaces the followup in-flight strip on its system row.
    return {
        "messages": [
            _msg_to_dict(m, responders=responders_by_seq.get(m.seq))
            for m in msgs
        ]
    }


# ---------------------------------------------------------------------------
# THR-061 PR-1 — GET /threads/{thread_id}/tasks
# ---------------------------------------------------------------------------


@router.get("/threads/{thread_id}/tasks")
async def list_thread_tasks_endpoint(
    slug: str,
    thread_id: str,
    org: OrgDep,
) -> list[dict]:
    """Return the tasks dispatched from a thread, newest-first."""
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    return org.db.list_tasks_by_thread(thread_id)


# ---------------------------------------------------------------------------
# Task 21 — POST /threads/{id}/reply
# ---------------------------------------------------------------------------


class ReplyBody(BaseModel):
    thread_id: str
    invocation_token: str
    speaker: str
    body_markdown: str = ""
    attachments: list[AttachmentRefBody] = Field(default_factory=list)
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


@router.post("/threads/{thread_id}/reply")
async def reply_thread_endpoint(
    slug: str, thread_id: str, body: ReplyBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})

    attachments = _normalize_all_attachments(
        org, body.attachments, uploaded_by=body.speaker, thread_id=thread_id,
    )
    body_text = _normalize_message_body(body.body_markdown, attachments)

    _validate_invocation_token(
        org, token=body.invocation_token,
        expected_agent=body.speaker, expected_thread_id=thread_id,
        require_purposes=[
            ThreadInvocationPurpose.REPLY,
            ThreadInvocationPurpose.BOOTSTRAP,
            ThreadInvocationPurpose.TASK_FOLLOWUP,
        ],
    )
    if not org.db.is_thread_participant(thread_id, body.speaker):
        raise HTTPException(status_code=403, detail={"code": "not_participant"})
    # _verify_addressed removed: broadcast model; any participant can reply to
    # any message as long as they hold a valid invocation token.

    tokens_to_enqueue: list[str] = []
    async with org.db_lock:
        inv = org.db.get_pending_invocation(body.invocation_token)
        if inv is None:
            raise HTTPException(status_code=409, detail={"code": "invocation_token_consumed"})
        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker=body.speaker,
            kind=ThreadMessageKind.MESSAGE, body_markdown=body_text,
            attachments=attachments,
        )
        org.db.consume_invocation(body.invocation_token)
        org.db.increment_thread_turns_used(thread_id, by=1)
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker=body.speaker, kind="message",
            attachment_names=[a.artifact_name for a in attachments],
        )
        # Broadcast: mint REPLY for every participant except the speaker.
        for p in org.db.list_thread_participants(thread_id):
            if p.agent_name == body.speaker:
                continue
            new_inv = org.db.mint_thread_invocation(
                thread_id=thread_id, agent_name=p.agent_name,
                triggering_seq=seq, purpose=ThreadInvocationPurpose.REPLY,
            )
            tokens_to_enqueue.append(new_inv.invocation_token)

    for token in tokens_to_enqueue:
        await org.thread_queue.put(ThreadJob(org_slug=slug, invocation_token=token))

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=seq, speaker=body.speaker,
        kind="message",
        preview=body_text or _attachments_preview(attachments),
        status="open",
    )

    return {"thread_id": thread_id, "seq": seq, "kind": "message"}


# ---------------------------------------------------------------------------
# Task 22 — POST /threads/{id}/decline
# ---------------------------------------------------------------------------


class DeclineBody(BaseModel):
    thread_id: str
    invocation_token: str
    speaker: str
    reason: str | None = None
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
    _validate_invocation_token(
        org, token=body.invocation_token,
        expected_agent=body.speaker, expected_thread_id=thread_id,
        require_purposes=[
            ThreadInvocationPurpose.REPLY,
            ThreadInvocationPurpose.BOOTSTRAP,
            ThreadInvocationPurpose.TASK_FOLLOWUP,
        ],
    )
    if not org.db.is_thread_participant(thread_id, body.speaker):
        raise HTTPException(status_code=403, detail={"code": "not_participant"})
    # _verify_addressed removed: broadcast model; any participant can decline
    # any message as long as they hold a valid invocation token.

    reason = body.reason.strip() if body.reason else None
    async with org.db_lock:
        if org.db.get_pending_invocation(body.invocation_token) is None:
            raise HTTPException(status_code=409, detail={"code": "invocation_token_consumed"})
        ok = org.db.mark_invocation_declined(
            body.invocation_token, decline_reason=reason,
        )
        if not ok:
            raise HTTPException(status_code=409, detail={"code": "invocation_token_consumed"})
        AuditLogger(org.db).log_thread_decline_consumed(
            thread_id, agent_name=body.speaker, reason=reason,
        )
        # No thread_messages row, no turns_used increment (spec §6: silent decline).
    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=None, speaker=body.speaker,
        kind="decline_status", preview=None, status="open",
    )

    return {"thread_id": thread_id, "status": "declined"}


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
    # THR-018 §3a: optionally name an escalated or in_progress(delegated) predecessor
    # that this continuation supersedes. Honored ONLY for a manager-authorized
    # dispatch (maker-checker); a worker self-dispatch must NEVER auto-close it.
    resolves: str | None = None


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
                detail={"code": "thread_dispatch_team_override_forbidden",
                        "dispatcher_team": dispatcher_team,
                        "requested_team": effective_team,
                        "hint": SELF_DISPATCH_HINT},
            )
        effective_target = body.target_agent if body.target_agent is not None else dispatcher
        if effective_target != dispatcher:
            raise HTTPException(
                status_code=403,
                detail={"code": "thread_dispatch_must_be_self",
                        "dispatcher": dispatcher,
                        "requested_target": effective_target,
                        "hint": SELF_DISPATCH_HINT},
            )

    org_paths = OrgPaths(root=org.root)
    agent_def = prompt_loader.load_agent(org_paths, effective_target)
    workspace_exists = (org.root / "workspaces" / effective_target).exists()
    if agent_def is None or not workspace_exists:
        raise HTTPException(status_code=404, detail={"code": "unknown_agent", "agent": effective_target})

    # THR-018 §3a forcing function: an optional `resolves` names a blocked
    # predecessor that this thread-dispatched continuation supersedes. The
    # maker-checker boundary: ONLY a founder/manager-authorized dispatch may
    # auto-close a predecessor — a worker self-dispatch must NEVER. (The founder
    # closes via `revisit`; managers close in-lineage via this thread path.)
    # Validated before the task is created so an ineligible/unauthorized supersede
    # rejects cleanly without orphaning a new root.
    resolves = body.resolves.strip() if body.resolves else None
    predecessor = None
    pred_block_kind = None
    if resolves:
        if not is_manager:
            raise HTTPException(
                status_code=403,
                detail={"code": "thread_supersede_not_authorized",
                        "dispatcher": dispatcher, "resolves": resolves},
            )
        from runtime.daemon.routes.tasks import _eligible_supersede_block_kind
        predecessor = org.db.get_task(resolves)
        if predecessor is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "predecessor_not_found", "resolves": resolves},
            )
        pred_block_kind = _eligible_supersede_block_kind(org, predecessor)
        if pred_block_kind is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "predecessor_not_supersedable",
                        "resolves": resolves,
                        "predecessor_status": predecessor.status.value},
            )

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
        audit = AuditLogger(org.db)
        audit.log_thread_dispatch(
            thread_id, task_id=task_id, dispatcher=dispatcher,
            target_agent=effective_target, team=effective_team,
        )
        # Reuse the proven revisit-path supersede logic (Gap-A no-reenqueue +
        # successor citation). Parent-wake + followup run after the lock.
        family_closed: list[str] = []
        if resolves and predecessor is not None:
            from runtime.daemon.routes.tasks import (
                _collect_eligible_revisit_family,
                _eligible_supersede_block_kind,
                _supersede_predecessor_locked,
            )
            _supersede_predecessor_locked(
                org, audit,
                predecessor_id=predecessor.id,
                successor_root=task_id,
                prior_block_kind=pred_block_kind,
                actor="thread-dispatch",
                note_suffix=f"thread {thread_id} dispatch by {dispatcher}",
                thread_id=thread_id,
            )
            # THR-046 msg127: broader revisit-family closure — also supersede
            # eligible sibling/ancestor revisits in the same revisit family.
            for family_task in _collect_eligible_revisit_family(
                org,
                explicit_predecessor_id=predecessor.id,
                successor_root=task_id,
            ):
                family_block_kind = _eligible_supersede_block_kind(org, family_task)
                _supersede_predecessor_locked(
                    org, audit,
                    predecessor_id=family_task.id,
                    successor_root=task_id,
                    prior_block_kind=family_block_kind,
                    actor="thread-dispatch",
                    note_suffix=f"thread {thread_id} dispatch by {dispatcher}",
                    thread_id=thread_id,
                )
                family_closed.append(family_task.id)

    enqueue_task(state, slug, task_id)

    # Supersede tail (outside the lock, mirroring revisit_from_notification):
    # the superseded predecessor reached a terminal, so a delegated parent must
    # learn its branch is done, and a thread-originated predecessor must emit its
    # task-followup. The predecessor itself is NEVER re-enqueued (Gap-A).
    if resolves and predecessor is not None:
        from runtime.models import TaskStatus
        from runtime.orchestrator.run_step import (
            _enqueue_parent_if_waiting,
            _maybe_post_thread_followup,
        )
        _enqueue_parent_if_waiting(org.orchestrator, predecessor.id)
        _maybe_post_thread_followup(
            org.orchestrator, predecessor.id,
            status=TaskStatus.RESOLVED_SUPERSEDED, auto_revisit_spawned=False,
        )
        # Same tail for each family sibling closed.
        for family_task_id in family_closed:
            _enqueue_parent_if_waiting(org.orchestrator, family_task_id)
            _maybe_post_thread_followup(
                org.orchestrator, family_task_id,
                status=TaskStatus.RESOLVED_SUPERSEDED, auto_revisit_spawned=False,
            )

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
        "superseded_task_id": resolves if (resolves and predecessor is not None) else None,
    }


# ---------------------------------------------------------------------------
# Task 24 — POST /threads/{id}/send (founder follow-up)
# ---------------------------------------------------------------------------


class SendBody(BaseModel):
    body_markdown: str = ""
    attachments: list[AttachmentRefBody] = Field(default_factory=list)
    # Optional agent binding fields for agent-attributed send (THR-069).
    composer: str | None = None
    task_id: str | None = None
    session_id: str | None = None


class _SendThreadError(Exception):
    """In-process send failures — translated to HTTPException by the route."""

    def __init__(self, status_code: int, code: str, **details: object) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.details = details


def _validate_task_session_binding(
    org: object,
    composer: str,
    task_id: str,
    session_id: str,
) -> None:
    """Validate a task+session binding for agent-initiated thread actions.

    Shared by /send (with agent binding) and /post-as-agent.
    Raises _SendThreadError on validation failures.
    """
    task = org.db.get_task(task_id)
    if task is None:
        raise _SendThreadError(404, "unknown_task", task_id=task_id)
    if task.assigned_agent != composer:
        raise _SendThreadError(
            403, "composer_not_task_owner",
            composer=composer, assigned_agent=task.assigned_agent,
        )
    active_sid = org.sessions.get_active(task_id, composer)
    if active_sid is None or active_sid != session_id:
        raise _SendThreadError(
            409, "session_mismatch", active=active_sid, got=session_id,
        )


async def _send_thread_message_inprocess(
    org: object,
    slug: str,
    thread_id: str,
    *,
    body_markdown: str,
    attachments: list[AttachmentRefBody] | None = None,
    # Agent binding for agent-attributed send (THR-069).
    composer: str | None = None,
    task_id: str | None = None,
    session_id: str | None = None,
) -> dict:
    """Append a message to an open thread + mint reply invocations.

    When agent binding (composer/task_id/session_id) is present, the message is
    attributed to the agent and REPLY invocations are minted to OTHER participants
    only (mirrors /post-as-agent). When absent, the message is attributed to
    'founder' and REPLY is minted to ALL participants (current behavior).

    Returns the same response shape as POST /threads/{id}/send.
    Raises _SendThreadError on validation failures (route translates to HTTP).
    """
    t = org.db.get_thread(thread_id)
    if t is None:
        raise _SendThreadError(404, "not_found")
    if t.status is not ThreadStatus.OPEN:
        raise _SendThreadError(400, "thread_not_open")

    # Derive speaker and addressed list from binding presence (THR-069).
    if composer is not None and task_id is not None and session_id is not None:
        _validate_task_session_binding(org, composer, task_id, session_id)
        # Participate-and-audit guard: the speaker must be a participant.
        participants = [p.agent_name for p in org.db.list_thread_participants(thread_id)]
        if composer not in participants:
            raise _SendThreadError(
                403, "not_a_participant", composer=composer,
            )
        speaker = composer
        uploaded_by = composer
        addressed = [name for name in participants if name != composer]
        sent_from_task_id = task_id
    else:
        speaker = "founder"
        uploaded_by = "founder"
        participants = [p.agent_name for p in org.db.list_thread_participants(thread_id)]
        # Broadcast model: founder /send mints REPLY for every participant.
        addressed = list(participants)
        sent_from_task_id = None

    normalized_attachments = _normalize_all_attachments(
        org, attachments, uploaded_by=uploaded_by, thread_id=thread_id,
    )
    body_text = _normalize_message_body(body_markdown, normalized_attachments)

    tokens_to_enqueue: list[str] = []
    async with org.db_lock:
        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker=speaker,
            kind=ThreadMessageKind.MESSAGE,
            body_markdown=body_text,
            attachments=normalized_attachments,
            sent_from_task_id=sent_from_task_id,
        )
        org.db.increment_thread_turns_used(thread_id, by=1)
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker=speaker, kind="message",
            attachment_names=[a.artifact_name for a in normalized_attachments],
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
        thread_id=thread_id, seq=seq, speaker=speaker,
        kind="message",
        preview=body_text or _attachments_preview(normalized_attachments),
        status="open",
    )

    return {"thread_id": thread_id, "seq": seq, "pending_replies": addressed}


@router.post("/threads/{thread_id}/send")
async def send_thread_endpoint(
    slug: str, thread_id: str, body: SendBody, org: OrgDep,
) -> dict:
    try:
        return await _send_thread_message_inprocess(
            org, slug, thread_id,
            body_markdown=body.body_markdown,
            attachments=body.attachments,
            composer=body.composer,
            task_id=body.task_id,
            session_id=body.session_id,
        )
    except _SendThreadError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, **exc.details},
        ) from exc


# ---------------------------------------------------------------------------
# POST /threads/{id}/post-as-agent — a live task session posts a MESSAGE into
# an EXISTING thread it participates in (THR-027). The task-session analogue of
# /threads/{id}/send: binding checks mirror compose-as-agent; the append +
# broadcast effect mirrors _send_thread_message_inprocess, except the message
# is attributed to the agent and REPLY tokens are minted to OTHER participants
# only (the composer is excluded). Authz is participant-only (founder ruling
# THR-027 seq=18): contrast compose-as-agent, which opens a NEW thread.
# ---------------------------------------------------------------------------


class PostAsAgentBody(BaseModel):
    composer: str
    task_id: str | None = None
    session_id: str | None = None
    body_markdown: str = ""
    attachments: list[AttachmentRefBody] = Field(default_factory=list)


@router.post("/threads/{thread_id}/post-as-agent")
async def post_thread_as_agent(
    slug: str, thread_id: str, body: PostAsAgentBody, org: OrgDep,
) -> dict:
    # Task binding — mirrors compose_thread_as_agent: task_id+session_id
    # required, task exists, composer owns it, active session matches.
    if not body.task_id or not body.session_id:
        raise HTTPException(status_code=422, detail={"code": "binding_required"})
    try:
        _validate_task_session_binding(
            org, body.composer, body.task_id, body.session_id,
        )
    except _SendThreadError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, **exc.details},
        ) from exc

    # Thread must exist before participation can be evaluated.
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})

    # Authz (participant-only, founder ruling THR-027 seq=18): the composer must
    # already be a current participant of the thread.
    participants = [p.agent_name for p in org.db.list_thread_participants(thread_id)]
    if body.composer not in participants:
        raise HTTPException(
            status_code=403,
            detail={"code": "not_a_participant", "composer": body.composer},
        )

    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})

    attachments = _normalize_all_attachments(
        org, body.attachments, uploaded_by=body.composer, thread_id=thread_id,
    )
    body_text = _normalize_message_body(body.body_markdown, attachments)

    # Broadcast model: mint REPLY for every OTHER participant (exclude the
    # composer — they are the speaker).
    addressed = [name for name in participants if name != body.composer]

    tokens_to_enqueue: list[str] = []
    async with org.db_lock:
        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker=body.composer,
            kind=ThreadMessageKind.MESSAGE,
            body_markdown=body_text,
            attachments=attachments,
            sent_from_task_id=body.task_id,
        )
        org.db.increment_thread_turns_used(thread_id, by=1)
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker=body.composer, kind="message",
            attachment_names=[a.artifact_name for a in attachments],
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
        thread_id=thread_id, seq=seq, speaker=body.composer,
        kind="message",
        preview=body_text or _attachments_preview(attachments),
        status="open",
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

    # Spec §7: "invite is free" — no turn-cap projection here.
    # turns_used is still incremented at message-send time for display,
    # but cap enforcement was removed per THR-046 msg86.

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
    summary: str = ""


@router.post("/threads/{thread_id}/archive")
async def archive_thread_endpoint(
    slug: str, thread_id: str, body: ArchiveBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is ThreadStatus.ARCHIVED:
        return {
            "thread_id": thread_id, "status": "archived",
            "transcript_path": t.transcript_path, "idempotent": True,
        }
    summary = body.summary.strip()

    archived_at = datetime.now(timezone.utc)
    async with org.db_lock:
        org.db.reap_pending_invocations(
            thread_id,
            purposes=[ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP],
            decline_reason="archive_started",
        )
        org.db.set_thread_status(
            thread_id, status=ThreadStatus.ARCHIVED, summary=summary,
        )
        participants = [p.agent_name for p in org.db.list_thread_participants(thread_id)]
        sys_seq = org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.SYSTEM,
            system_payload={"kind_tag": "archived", "summary": summary},
        )

    # Write transcript file synchronously (was the finalizer's job).
    msgs = org.db.list_thread_messages(thread_id, limit=10000)
    rendered = render_transcript_body(msgs)
    transcript_path = org.thread_store.write_transcript(
        thread_id=thread_id,
        subject=t.subject,
        started_at=t.started_at,
        archived_at=archived_at,
        participants=participants,
        turns_used=t.turns_used,
        forwarded_from_id=t.forwarded_from_id,
        summary=summary,
        rendered_transcript=rendered,
    )
    async with org.db_lock:
        org.db.set_thread_transcript_path(thread_id, str(transcript_path))
        AuditLogger(org.db).log_thread_archived(
            thread_id, turns_used=t.turns_used,
        )

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=sys_seq, speaker="founder",
        kind="system", preview="archived", status="archived",
    )

    return {
        "thread_id": thread_id, "status": "archived",
        "transcript_path": str(transcript_path),
    }


# ---------------------------------------------------------------------------
# POST /threads/{id}/resume — founder reopens an archived thread
# ---------------------------------------------------------------------------


@router.post("/threads/{thread_id}/resume")
async def resume_thread_endpoint(
    slug: str, thread_id: str, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is ThreadStatus.OPEN:
        return {"thread_id": thread_id, "status": "open", "idempotent": True}
    if t.status is not ThreadStatus.ARCHIVED:
        raise HTTPException(
            status_code=400,
            detail={"code": "thread_not_archived", "status": t.status.value},
        )

    prior_archived_at = (
        t.archived_at.isoformat() if t.archived_at else None
    )
    async with org.db_lock:
        org.db.set_thread_status(thread_id, status=ThreadStatus.OPEN)
        sys_seq = org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.SYSTEM,
            system_payload={"kind_tag": "resumed"},
        )
        AuditLogger(org.db).log_thread_resumed(
            thread_id, prior_archived_at=prior_archived_at,
        )

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=sys_seq, speaker="founder",
        kind="system", preview="resumed", status="open",
    )

    return {"thread_id": thread_id, "status": "open"}


# ---------------------------------------------------------------------------
# POST /threads/{id}/abort-replies — founder aborts pending reply invocations
# ---------------------------------------------------------------------------


@router.post("/threads/{thread_id}/abort-replies")
async def abort_replies_endpoint(
    slug: str, thread_id: str, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})

    # Collect triggering seqs BEFORE reaping so we know which seqs to publish.
    from runtime.daemon.thread_runner import _publish_invocation_event
    pending = org.db.list_thread_invocations(
        thread_id, status=ThreadInvocationStatus.PENDING,
    )
    affected_seqs: set[int] = {
        inv.triggering_seq for inv in pending
        if inv.purpose in (
            ThreadInvocationPurpose.REPLY,
            ThreadInvocationPurpose.BOOTSTRAP,
            ThreadInvocationPurpose.TASK_FOLLOWUP,
        )
    }

    aborted_count = 0
    async with org.db_lock:
        aborted_count = org.db.reap_pending_invocations(
            thread_id,
            purposes=[
                ThreadInvocationPurpose.REPLY,
                ThreadInvocationPurpose.BOOTSTRAP,
                ThreadInvocationPurpose.TASK_FOLLOWUP,
            ],
            decline_reason="founder_aborted",
        )

    # Publish seq-bearing events for each affected triggering seq so the web
    # client invalidates its responder_status and clears in-flight indicators.
    for seq in sorted(affected_seqs):
        await _publish_invocation_event(
            org, thread_id=thread_id, agent_name="founder",
            seq=seq, kind="invocation_settled", status="failed",
        )

    return {"thread_id": thread_id, "aborted_count": aborted_count}


# ---------------------------------------------------------------------------
# Thread-scoped attachment routes (TASK-1616)
# ---------------------------------------------------------------------------


def _attachment_store(org: object) -> "ThreadScopedAttachmentStore":
    from runtime.infrastructure.thread_scoped_attachment_store import (
        ThreadScopedAttachmentStore,
    )
    return ThreadScopedAttachmentStore(OrgPaths(org.root).threads_dir)


class ThreadAttachmentRefBody(BaseModel):
    """Reference to a thread-scoped attachment already uploaded."""
    attachment_id: str
    display_name: str | None = None
    content_type: str | None = None


@router.get("/threads/{thread_id}/attachments")
async def list_thread_attachments(
    slug: str, thread_id: str, org: OrgDep, request: Request,
    agent: str | None = Query(None),
    invocation_token: str | None = Query(None),
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    # Agent-facing access must carry proof. No-agent requests are rejected
    # so a caller cannot bypass by omitting params. Founder bearer path
    # (agent='founder') remains unrestricted.
    if not agent:
        raise HTTPException(
            status_code=401, detail={"code": "agent_required"},
        )
    if agent != "founder":
        if not invocation_token:
            raise HTTPException(
                status_code=401,
                detail={"code": "invocation_token_required"},
            )
        _validate_invocation_token(
            org, token=invocation_token,
            expected_agent=agent, expected_thread_id=thread_id,
            require_purposes=[
                ThreadInvocationPurpose.REPLY,
                ThreadInvocationPurpose.BOOTSTRAP,
                ThreadInvocationPurpose.TASK_FOLLOWUP,
            ],
        )
        if not org.db.is_thread_participant(thread_id, agent):
            raise HTTPException(status_code=403, detail={"code": "not_participant"})
    rows = org.db.list_thread_scoped_attachments(thread_id)
    return {
        "attachments": [
            {
                "attachment_id": r.attachment_id,
                "thread_id": r.thread_id,
                "display_name": r.display_name,
                "size_bytes": r.size_bytes,
                "content_type": r.content_type,
                "uploaded_by": r.uploaded_by,
                "created_at": r.created_at,
            }
            for r in rows
        ]
    }


@router.get("/threads/{thread_id}/attachments/{attachment_id}")
async def get_thread_attachment(
    slug: str, thread_id: str, attachment_id: str, org: OrgDep, request: Request,
    agent: str | None = Query(None),
    invocation_token: str | None = Query(None),
):
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    # Agent-facing access must carry proof. No-agent requests are rejected
    # so a caller cannot bypass by omitting params. Founder bearer path
    # (agent='founder') remains unrestricted.
    if not agent:
        raise HTTPException(
            status_code=401, detail={"code": "agent_required"},
        )
    if agent != "founder":
        if not invocation_token:
            raise HTTPException(
                status_code=401,
                detail={"code": "invocation_token_required"},
            )
        _validate_invocation_token(
            org, token=invocation_token,
            expected_agent=agent, expected_thread_id=thread_id,
            require_purposes=[
                ThreadInvocationPurpose.REPLY,
                ThreadInvocationPurpose.BOOTSTRAP,
                ThreadInvocationPurpose.TASK_FOLLOWUP,
            ],
        )
        if not org.db.is_thread_participant(thread_id, agent):
            raise HTTPException(status_code=403, detail={"code": "not_participant"})
    row = org.db.get_thread_scoped_attachment(thread_id, attachment_id)
    if row is None:
        raise HTTPException(
            status_code=404, detail={"code": "attachment_not_found"}
        )
    try:
        content = _attachment_store(org).read(thread_id, attachment_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail={"code": "attachment_not_found"}
        )
    media_type = row.content_type or "application/octet-stream"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{row.display_name}"'
        },
    )


@router.post("/threads/{thread_id}/attachments")
async def upload_thread_attachment(
    slug: str,
    thread_id: str,
    org: OrgDep,
    request: Request,
    file: UploadFile = File(...),
    agent: str = Query(...),
) -> dict:
    from fastapi import UploadFile
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})
    # Non-founder agents must be thread participants.
    if agent != "founder":
        if not org.db.is_thread_participant(thread_id, agent):
            raise HTTPException(status_code=403, detail={"code": "not_participant"})

    content = await file.read(MAX_THREAD_ATTACHMENT_BYTES + 1)
    if len(content) > MAX_THREAD_ATTACHMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "attachment_too_large",
                "max_bytes": MAX_THREAD_ATTACHMENT_BYTES,
                "size_bytes": len(content),
            },
        )

    display_name = file.filename or "attachment"
    _validate_display_name(display_name)
    content_type = file.content_type or mimetypes.guess_type(display_name)[0]

    attachment_id = org.db.next_thread_attachment_id()
    size_bytes = _attachment_store(org).put(thread_id, attachment_id, content)
    org.db.insert_thread_scoped_attachment(
        attachment_id=attachment_id,
        thread_id=thread_id,
        display_name=display_name,
        size_bytes=size_bytes,
        content_type=content_type,
        uploaded_by=agent,
    )

    return {
        "attachment_id": attachment_id,
        "thread_id": thread_id,
        "display_name": display_name,
        "size_bytes": size_bytes,
        "content_type": content_type,
        "uploaded_by": agent,
    }


def _normalize_thread_attachments(
    org: object,
    refs: list[ThreadAttachmentRefBody] | None,
    *,
    thread_id: str,
    uploaded_by: str,
) -> list[ThreadAttachment]:
    """Validate thread-scoped attachment refs and return ThreadAttachment list."""
    if not refs:
        return []
    seen: set[str] = set()
    out: list[ThreadAttachment] = []
    for ref in refs:
        attachment_id = ref.attachment_id.strip()
        if attachment_id in seen:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "duplicate_attachment",
                    "attachment_id": attachment_id,
                },
            )
        seen.add(attachment_id)
        row = org.db.get_thread_scoped_attachment(thread_id, attachment_id)
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "thread_attachment_not_found", "attachment_id": attachment_id},
            )
        display_name = (
            ref.display_name.strip() if ref.display_name else row.display_name
        )
        _validate_display_name(display_name)
        content_type = _normalize_content_type(ref.content_type, display_name)
        if content_type is None:
            content_type = row.content_type
        out.append(
            ThreadAttachment(
                artifact_name="",
                display_name=display_name,
                size_bytes=row.size_bytes,
                content_type=content_type,
                uploaded_by=uploaded_by,
                thread_attachment_id=attachment_id,
            )
        )
    return out
