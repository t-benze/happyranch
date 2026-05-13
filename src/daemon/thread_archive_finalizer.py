"""Background finalizer that moves a thread from 'archiving' to 'archived'.

Waits up to `close_out_wait_seconds` for all pending close-out invocations to
land (consume/timeout/fail), then writes the transcript and flips status.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.thread_store import ThreadStore, render_transcript_body
from src.models import ThreadInvocationStatus, ThreadMessageKind

logger = logging.getLogger(__name__)


async def finalize_thread(
    *,
    db,
    store: ThreadStore,
    thread_id: str,
    close_out_wait_seconds: int,
) -> None:
    deadline = asyncio.get_event_loop().time() + close_out_wait_seconds
    while True:
        pending = db.list_thread_invocations(
            thread_id, status=ThreadInvocationStatus.PENDING,
        )
        if not pending:
            break
        if asyncio.get_event_loop().time() >= deadline:
            db.reap_pending_invocations(
                thread_id, purposes=None, decline_reason="close_out_timeout",
            )
            break
        await asyncio.sleep(0.25)

    thread = db.get_thread(thread_id)
    if thread is None or thread.status.value != "archiving":
        logger.warning("finalize_thread: thread %s not in archiving state", thread_id)
        return

    participants = [p.agent_name for p in db.list_thread_participants(thread_id)]
    msgs = db.list_thread_messages(thread_id, limit=10000)
    rendered = render_transcript_body(msgs)
    summary = thread.summary or ""
    archived_at = datetime.now(timezone.utc)
    transcript_path = store.write_transcript(
        thread_id=thread_id,
        subject=thread.subject,
        started_at=thread.started_at,
        archived_at=archived_at,
        participants=participants,
        turns_used=thread.turns_used,
        new_learnings_total=0,
        new_kb_slugs=thread.new_kb_slugs,
        forwarded_from_id=thread.forwarded_from_id,
        summary=summary,
        rendered_transcript=rendered,
    )
    db.append_thread_message(
        thread_id=thread_id, speaker="founder",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={
            "kind_tag": "archived",
            "new_kb_slugs": thread.new_kb_slugs,
        },
    )
    db.finalize_thread_archived(
        thread_id, transcript_path=str(transcript_path),
        new_kb_slugs=thread.new_kb_slugs,
    )
    AuditLogger(db).log_thread_archived(
        thread_id,
        new_learnings_total=0,
        new_kb_slugs=thread.new_kb_slugs,
        turns_used=thread.turns_used,
    )
