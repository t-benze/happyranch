from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.daemon.thread_archive_finalizer import finalize_thread
from src.infrastructure.database import Database
from src.infrastructure.thread_store import ThreadStore
from src.models import (
    ThreadInvocationPurpose,
    ThreadMessageKind,
    ThreadRecord,
    ThreadStatus,
)


async def test_finalize_thread_writes_transcript_and_archives(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "dev_agent", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    db.set_thread_status("THR-001", status=ThreadStatus.ARCHIVING, summary="done")
    store = ThreadStore(tmp_path / "threads")
    await finalize_thread(
        db=db, store=store, thread_id="THR-001",
        close_out_wait_seconds=2,
    )
    t = db.get_thread("THR-001")
    assert t.status is ThreadStatus.ARCHIVED
    assert t.transcript_path is not None


async def test_finalize_waits_for_close_outs_or_times_out(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "dev_agent", added_by="founder")
    db.set_thread_status("THR-001", status=ThreadStatus.ARCHIVING, summary="done")
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="dev_agent",
        triggering_seq=1, purpose=ThreadInvocationPurpose.CLOSE_OUT,
    )
    store = ThreadStore(tmp_path / "threads")
    start = asyncio.get_event_loop().time()
    await finalize_thread(
        db=db, store=store, thread_id="THR-001",
        close_out_wait_seconds=1,
    )
    elapsed = asyncio.get_event_loop().time() - start
    assert 0.9 <= elapsed <= 2.5
    assert db.get_thread("THR-001").status is ThreadStatus.ARCHIVED


async def test_finalize_thread_records_aggregate_learnings_total(tmp_path):
    """Close-out callbacks increment new_learnings_total; finalizer must
    surface that aggregate in both the transcript and the archived audit."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.add_thread_participant("THR-001", "bob", added_by="founder")
    db.set_thread_status("THR-001", status=ThreadStatus.ARCHIVING, summary="done")

    # Simulate two close-outs landing.
    db.add_thread_learnings_count("THR-001", count=3)  # alice
    db.add_thread_learnings_count("THR-001", count=2)  # bob

    store = ThreadStore(tmp_path / "threads")
    await finalize_thread(
        db=db, store=store, thread_id="THR-001",
        close_out_wait_seconds=1,
    )
    t = db.get_thread("THR-001")
    assert t.status is ThreadStatus.ARCHIVED
    assert t.new_learnings_total == 5
    transcript_text = Path(t.transcript_path).read_text(encoding="utf-8")
    assert "new_learnings_total: 5" in transcript_text
