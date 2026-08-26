"""Decline-by-default doctrine injection for thread REPLY invocations.

Spec: docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md §5
"""
from __future__ import annotations

from datetime import datetime, timezone

from runtime.daemon.thread_runner import build_thread_prompt
from runtime.models import (
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
)
from runtime.orchestrator.org_config import OrgConfig

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
        "org_config": OrgConfig(),
    }
    defaults.update(overrides)
    return build_thread_prompt(**defaults)


def test_doctrine_appears_for_reply_purpose():
    prompt = _build(purpose="reply")
    assert DOCTRINE_HEADER in prompt
    assert "decline" in prompt.lower()
    # Doctrine must precede the participation block — spec §5 says
    # "prepends to the existing 'You are participating in thread...' block".
    doctrine_pos = prompt.index(DOCTRINE_HEADER)
    participation_pos = prompt.index("You are participating in thread")
    assert doctrine_pos < participation_pos, (
        "doctrine must appear before the participation block "
        "(spec §5: top-of-prompt placement)"
    )


def test_doctrine_absent_for_bootstrap_purpose():
    prompt = _build(purpose="bootstrap")
    assert DOCTRINE_HEADER not in prompt


def test_doctrine_absent_for_close_out_purpose():
    prompt = _build(purpose="close_out")
    assert DOCTRINE_HEADER not in prompt


def test_doctrine_absent_for_task_followup_purpose():
    prompt = _build(purpose="task_followup")
    assert DOCTRINE_HEADER not in prompt


def _reply_doctrine(prompt: str) -> str:
    """The doctrine block is prepended before the participation block (spec
    §5: top-of-prompt placement). Slice it out and collapse line breaks so
    phrase assertions are robust to the prose's line wrapping."""
    block = prompt[: prompt.index("You are participating in thread")]
    return " ".join(block.split())


def test_doctrine_instructs_inspecting_full_history_beyond_delivery_range():
    """TASK-5735: the REPLY doctrine must tell the invoked agent to read the
    full supplied conversation — not just the newest delivery-range messages —
    before deciding."""
    doctrine = _reply_doctrine(_build(purpose="reply"))
    assert "full conversation" in doctrine
    assert "delivery range" in doctrine
    assert "not just the newest messages" in doctrine


def test_doctrine_instructs_silent_decline_when_same_agent_already_answered():
    """TASK-5735: the REPLY doctrine must instruct the invoked agent to
    silently decline when THAT SAME agent already substantively answered the
    delivered request in a later message of its own."""
    doctrine = _reply_doctrine(_build(purpose="reply"))
    assert "already substantively answered" in doctrine
    assert "later message of your own" in doctrine
    assert "decline silently" in doctrine


def test_doctrine_preserves_distinct_unanswered_request_exception():
    """TASK-5735: the already-answered rule must preserve the exception where
    the newest delivery-range message contains a distinct unanswered request
    (e.g. a genuine follow-up question)."""
    doctrine = _reply_doctrine(_build(purpose="reply"))
    assert "distinct request you have not yet answered" in doctrine
    assert "genuine follow-up question" in doctrine
    assert "suppress legitimate follow-up questions" in doctrine


def test_doctrine_never_infers_coverage_from_sequence_or_order():
    """TASK-5735: semantic coverage must not be inferred merely from later
    sequence/order — a later acknowledgment/restatement is not an answer."""
    doctrine = _reply_doctrine(_build(purpose="reply"))
    assert (
        "Coverage is a question of substance, never of sequence or position"
        in doctrine
    )
    assert "does not count as an answer" in doctrine


def test_doctrine_preserves_unique_role_direct_question_rule():
    """TASK-5735: the existing unique-role/direct-question reply condition is
    preserved alongside the new same-agent-already-answered rule."""
    doctrine = _reply_doctrine(_build(purpose="reply"))
    assert "uniquely answer based on your role" in doctrine


def test_doctrine_preserves_substantive_content_rule():
    """TASK-5735: the existing substantive-content reply condition is
    preserved."""
    doctrine = _reply_doctrine(_build(purpose="reply"))
    assert "substantive content to add" in doctrine
    assert '"I agree"' in doctrine


def test_doctrine_preserves_other_participant_coverage_rule():
    """TASK-5735: the existing other-participant-coverage reply condition is
    preserved."""
    doctrine = _reply_doctrine(_build(purpose="reply"))
    assert "No other participant has already covered the same ground" in doctrine
