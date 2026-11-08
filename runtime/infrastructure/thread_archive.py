"""Thread archive evaluation, post-commit notifications and publication."""
from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable

from runtime.infrastructure.thread_store import render_transcript_body
from runtime.models import TaskStatus, ThreadRecord

logger = logging.getLogger(__name__)


async def publish_thread_event(
    org: Any, slug: str, *, thread_id: str, seq: int | None, speaker: str,
    kind: str, preview: str = "", status: str = "open",
) -> None:
    from runtime.daemon.event_bus import thread_topic, thread_inbox_topic

    await org.event_bus.publish(thread_topic(thread_id), {
        "thread_id": thread_id, "seq": seq, "speaker": speaker,
        "kind": kind, "preview": (preview or "")[:160],
    })
    await org.event_bus.publish(thread_inbox_topic(slug), {
        "thread_id": thread_id, "event_kind": kind, "status": status,
    })


async def finish_thread_archive(
    org: Any, thread: ThreadRecord, *, summary: str, archived_at: datetime,
    participants: list[str], seq: int, speaker: str = "founder",
    audit_committed: bool = False,
) -> str:
    """Publish the existing transcript/path/audit/SSE after native COMMIT."""
    from runtime.infrastructure.audit_logger import AuditLogger

    msgs = org.db.list_thread_messages(thread.id, limit=10000)
    transcript_path = org.thread_store.write_transcript(
        thread_id=thread.id, subject=thread.subject, started_at=thread.started_at,
        archived_at=archived_at, participants=participants,
        turns_used=thread.turns_used, forwarded_from_id=thread.forwarded_from_id,
        summary=summary, rendered_transcript=render_transcript_body(msgs),
    )
    async with org.db_lock:
        org.db.set_thread_transcript_path(thread.id, str(transcript_path))
        if not audit_committed:
            AuditLogger(org.db).log_thread_archived(thread.id, turns_used=thread.turns_used)
    await publish_thread_event(
        org, org.slug, thread_id=thread.id, seq=seq, speaker=speaker,
        kind="system", preview="archived", status="archived",
    )
    return str(transcript_path)


async def maybe_auto_archive_thread(org: Any, thread_id: str, trigger: str) -> bool:
    """Best-effort cleanup: the native archive owner rechecks ALL admission."""
    try:
        archived_at = datetime.now(timezone.utc)
        async with org.db_lock:
            seq = org.db.archive_thread_and_reset_sessions(
                thread_id, summary="", audit_scope_id=thread_id,
                audit_agent="orchestrator", append_archive_message=True,
                archive_message_speaker="orchestrator", auto_archive_trigger=trigger,
            )
            if seq is None:
                return False
            thread = org.db.get_thread(thread_id)
            participants = [p.agent_name for p in org.db.list_thread_participants(thread_id)]
        await finish_thread_archive(
            org, thread, summary="", archived_at=archived_at,
            participants=participants, seq=seq, speaker="orchestrator",
            audit_committed=True,
        )
        return True
    except Exception:
        logger.exception("Auto-archive failed for %s/%s (trigger=%s)", org.slug, thread_id, trigger)
        return False


def bind_thread_archive_notifications(org: Any) -> None:
    """Bind live org callbacks; Database alone has no daemon dependency."""
    pending: set[asyncio.Task] = set()

    def notify(thread_id: str, trigger: str) -> None:
        try:
            loop = getattr(org.orchestrator, "_main_loop", None)
            if loop is None:
                loop = asyncio.get_running_loop()
            def start() -> None:
                task = loop.create_task(maybe_auto_archive_thread(org, thread_id, trigger))
                pending.add(task)
                task.add_done_callback(pending.discard)
            loop.call_soon_threadsafe(start)
        except Exception:
            logger.exception("Auto-archive notification unavailable for %s/%s", org.slug, thread_id)

    org.db._thread_archive_notify = notify
    if org.orchestrator is not None:
        org.orchestrator._thread_archive_notify = notify


def notify_composed_threads_task_terminal(orch: Any, task_id: str) -> None:
    """Task termination evaluates composition provenance, never resolution."""
    try:
        notify = getattr(orch, "_thread_archive_notify", None)
        if notify is None:
            return
        task = orch._db.get_task(task_id)
        if task is None or task.status not in {
            TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SUPERSEDED,
        }:
            return
        for thread in orch._db.list_threads_by_composed_from_task_id(task_id):
            notify(thread.id, "composed_task_terminal")
    except Exception:
        logger.exception("Composed-thread terminal cleanup failed for %s", task_id)


async def sweep_resolved_threads(org: Any) -> None:
    """One startup catch-up, after recovery; each decision is independently fresh."""
    try:
        thread_ids = org.db.list_open_resolved_thread_ids()
    except Exception:
        logger.exception("Auto-archive startup scan failed for %s", org.slug)
        return
    for thread_id in thread_ids:
        await maybe_auto_archive_thread(org, thread_id, "startup")


def after_thread_invocations_commit(scope: str) -> Callable:
    """Observe terminal writers without changing their transactions or results.

    Snapshot only pending receipts in resolved/open threads. After the writer
    returns (including a post-COMMIT error), inspect those actual receipts.
    Never notify from an enclosing uncommitted transaction. Recovery/raw startup
    writers that precede org loop wiring are covered by the startup sweep.
    Global exchange commit owners also observe the last held-row release, which
    can settle without a new invocation terminal (the existing grace reaper).
    """
    def decorate(method: Callable) -> Callable:
        signature = inspect.signature(method)

        @wraps(method)
        def observed(db: Any, *args: Any, **kwargs: Any) -> Any:
            before: dict[str, str] = {}
            held_before: set[str] = set()
            notify = getattr(db, "_thread_archive_notify", None)
            if notify is not None:
                try:
                    bound = signature.bind(db, *args, **kwargs).arguments
                    where, params = "", ()
                    if scope in {"token", "thread_id", "agent_name"}:
                        column = "invocation_token" if scope == "token" else scope
                        where, params = f" AND i.{column} = ?", (bound[scope],)
                    with db._lock:
                        if scope == "all":
                            held_before = {r["thread_id"] for r in db._conn.execute(
                                "SELECT DISTINCT d.thread_id FROM thread_exchange_deferrals d "
                                "JOIN thread_reply_exchange e ON e.thread_id=d.thread_id AND e.exchange_id=d.exchange_id "
                                "JOIN threads t ON t.id=d.thread_id WHERE d.state='held' AND e.state='open' "
                                "AND t.status='open' AND t.resolved_seq IS NOT NULL",
                            )}
                        before = {r["invocation_token"]: r["thread_id"] for r in db._conn.execute(
                            "SELECT i.invocation_token, i.thread_id FROM thread_invocations i "
                            "JOIN threads t ON t.id=i.thread_id WHERE i.status='pending' "
                            "AND t.status='open' AND t.resolved_seq IS NOT NULL" + where, params,
                        )}
                except Exception:
                    logger.exception("Auto-archive terminal observation failed before %s", method.__name__)
            try:
                return method(db, *args, **kwargs)
            finally:
                if before or held_before:
                    try:
                        terminal_threads: set[str] = set()
                        released_threads: set[str] = set()
                        with db._lock:
                            if not db._conn.in_transaction:
                                for thread_id in held_before:
                                    if db._conn.execute(
                                        "SELECT 1 FROM thread_exchange_deferrals d JOIN thread_reply_exchange e "
                                        "ON e.thread_id=d.thread_id AND e.exchange_id=d.exchange_id "
                                        "WHERE d.thread_id=? AND d.state='held' AND e.state='open' LIMIT 1",
                                        (thread_id,),
                                    ).fetchone() is None:
                                        released_threads.add(thread_id)
                                tokens = list(before)
                                for start in range(0, len(tokens), 500):
                                    batch = tokens[start:start + 500]
                                    marks = ",".join("?" for _ in batch)
                                    terminal_threads.update(r["thread_id"] for r in db._conn.execute(
                                        "SELECT thread_id FROM thread_invocations "
                                        f"WHERE invocation_token IN ({marks}) AND status!='pending'", batch,
                                    ))
                        for thread_id in terminal_threads:
                            notify(thread_id, "invocation_terminal")
                        for thread_id in released_threads - terminal_threads:
                            notify(thread_id, "exchange_settled")
                    except Exception:
                        logger.exception("Auto-archive terminal observation failed after %s", method.__name__)
        return observed
    return decorate
