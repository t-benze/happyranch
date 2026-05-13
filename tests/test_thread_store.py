from __future__ import annotations

from datetime import datetime, timezone

from src.infrastructure.thread_store import ThreadStore


def test_write_transcript_creates_file(tmp_path):
    store = ThreadStore(tmp_path / "threads")
    path = store.write_transcript(
        thread_id="THR-001",
        subject="Refund policy",
        started_at=datetime(2026, 5, 13, 10, 42, tzinfo=timezone.utc),
        archived_at=datetime(2026, 5, 13, 14, 10, tzinfo=timezone.utc),
        participants=["alice", "bob"],
        turns_used=4,
        new_learnings_total=3,
        new_kb_slugs=["refund-policy"],
        forwarded_from_id=None,
        summary="Settled at 45 days.",
        rendered_transcript="# Transcript\n\nMessage 1 …\n",
    )
    text = path.read_text(encoding="utf-8")
    assert "thread_id: THR-001" in text
    assert "Refund policy" in text
    assert "Settled at 45 days." in text
    assert "Message 1" in text


def test_write_transcript_is_atomic(tmp_path):
    store = ThreadStore(tmp_path / "threads")
    store.write_transcript(
        thread_id="THR-001",
        subject="x",
        started_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
        archived_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
        participants=[],
        turns_used=0,
        new_learnings_total=0,
        new_kb_slugs=[],
        forwarded_from_id=None,
        summary="",
        rendered_transcript="",
    )
    tmps = list((tmp_path / "threads").glob(".THR-001.*.md.tmp"))
    assert tmps == []
