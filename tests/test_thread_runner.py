from __future__ import annotations

from datetime import datetime, timezone

from src.daemon.thread_runner import build_thread_prompt
from src.models import (
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
)


def test_build_prompt_includes_token_and_history():
    thread = ThreadRecord(
        id="THR-001", subject="Refund policy",
        started_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    participants = [
        ThreadParticipant(thread_id="THR-001", agent_name="alice"),
        ThreadParticipant(thread_id="THR-001", agent_name="bob"),
    ]
    msgs = [
        ThreadMessage(
            thread_id="THR-001", seq=1, speaker="founder",
            kind=ThreadMessageKind.MESSAGE,
            body_markdown="should we cap?",
            addressed_to=["@all"],
        ),
    ]
    prompt = build_thread_prompt(
        thread=thread, participants=participants, messages=msgs,
        invocation_token="TOK-ABC",
        invoked_agent="alice", purpose="reply", triggering_seq=1,
    )
    assert "THR-001" in prompt
    assert "Refund policy" in prompt
    assert "TOK-ABC" in prompt
    assert "Message 1" in prompt
    assert "should we cap?" in prompt
    assert "@all" in prompt.lower() or "addressed @all" in prompt.lower()
