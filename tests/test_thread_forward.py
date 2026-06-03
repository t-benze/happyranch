from __future__ import annotations

from datetime import datetime, timezone

from cli.thread_forward import (
    build_forward_body_from_talk, build_forward_body_from_thread,
)
from runtime.models import ThreadMessage, ThreadMessageKind


def test_build_forward_body_from_talk_truncates_at_4kib():
    talk_summary = "x" * 8000
    body = build_forward_body_from_talk(
        source_id="TALK-008", summary=talk_summary, agent_name="alice",
    )
    assert "TALK-008" in body
    assert "alice" in body
    assert len(body.encode("utf-8")) <= 4096 + 200


def test_build_forward_body_from_thread_quotes_messages():
    msgs = [
        ThreadMessage(thread_id="THR-1", seq=1, speaker="founder",
                      kind=ThreadMessageKind.MESSAGE, body_markdown="hello",
                      created_at=datetime(2026, 5, 13, tzinfo=timezone.utc)),
        ThreadMessage(thread_id="THR-1", seq=2, speaker="alice",
                      kind=ThreadMessageKind.MESSAGE, body_markdown="hi back",
                      created_at=datetime(2026, 5, 13, tzinfo=timezone.utc)),
    ]
    body = build_forward_body_from_thread(
        source_id="THR-001", messages=msgs, subject="Refund",
    )
    assert "THR-001" in body
    assert "Refund" in body
    assert "hello" in body
    assert "hi back" in body
    assert body.lstrip().startswith(">")
