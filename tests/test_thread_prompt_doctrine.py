"""Decline-by-default doctrine injection for thread REPLY invocations.

Spec: docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md §5
"""
from __future__ import annotations

from datetime import datetime, timezone

from src.daemon.thread_runner import build_thread_prompt
from src.models import (
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
)

DOCTRINE_HEADER = "Decline-by-Default in Threads"

_NOW = datetime(2026, 5, 30, tzinfo=timezone.utc)


def _fake_thread() -> ThreadRecord:
    return ThreadRecord(
        id="THR-001",
        subject="Budget review",
        started_at=_NOW,
    )


def _fake_participant(name: str) -> ThreadParticipant:
    return ThreadParticipant(thread_id="THR-001", agent_name=name)


def _fake_message(seq: int, speaker: str, body: str) -> ThreadMessage:
    return ThreadMessage(
        thread_id="THR-001",
        seq=seq,
        speaker=speaker,
        kind=ThreadMessageKind.MESSAGE,
        body_markdown=body,
        addressed_to=["@all"],
    )


def _build(purpose: str, **overrides):
    defaults = {
        "thread": _fake_thread(),
        "participants": [_fake_participant("alpha"), _fake_participant("bravo")],
        "messages": [_fake_message(seq=1, speaker="founder", body="kickoff")],
        "invocation_token": "tok-x",
        "invoked_agent": "alpha",
        "purpose": purpose,
        "triggering_seq": 1,
    }
    defaults.update(overrides)
    return build_thread_prompt(**defaults)


def test_doctrine_appears_for_reply_purpose():
    prompt = _build(purpose="reply")
    assert DOCTRINE_HEADER in prompt
    assert "decline" in prompt.lower()


def test_doctrine_absent_for_bootstrap_purpose():
    prompt = _build(purpose="bootstrap")
    assert DOCTRINE_HEADER not in prompt


def test_doctrine_absent_for_close_out_purpose():
    prompt = _build(purpose="close_out")
    assert DOCTRINE_HEADER not in prompt


def test_doctrine_absent_for_task_followup_purpose():
    prompt = _build(purpose="task_followup")
    assert DOCTRINE_HEADER not in prompt
