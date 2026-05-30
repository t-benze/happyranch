from __future__ import annotations

from datetime import datetime, timezone

from src.infrastructure.thread_store import ThreadStore, render_transcript_body
from src.models import ThreadMessage, ThreadMessageKind


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


def test_render_transcript_renders_message_decline_system():
    msgs = [
        ThreadMessage(
            thread_id="THR-001", seq=1, speaker="founder",
            kind=ThreadMessageKind.MESSAGE,
            body_markdown="should we cap refunds at 30 days?",
        ),
        ThreadMessage(
            thread_id="THR-001", seq=2, speaker="alice",
            kind=ThreadMessageKind.MESSAGE,
            body_markdown="Alipay 60d, Stripe 120d.",
        ),
        ThreadMessage(
            thread_id="THR-001", seq=3, speaker="bob",
            kind=ThreadMessageKind.DECLINE,
            decline_reason="alice covered it",
        ),
        ThreadMessage(
            thread_id="THR-001", seq=4, speaker="alice",
            kind=ThreadMessageKind.SYSTEM,
            system_payload={
                "kind_tag": "task_dispatched",
                "task_id": "TASK-091",
                "target_agent": "dev",
                "brief_preview": "Cap at 45 days",
            },
        ),
    ]
    out = render_transcript_body(msgs)
    assert "## Message 1 — founder" in out
    assert "To: @all" in out
    assert "## Message 3 — bob" in out
    assert "declined" in out and "alice covered it" in out
    assert "system: dispatched TASK-091 to dev" in out
