"""current_time injection into the wake / thread / dream session prompts.

TASK-999 (THR-039 REVISE): the three executor-backed session prompt builders
that bypass ``Orchestrator._build_agent_prompt`` (wake, thread full + delta,
dream) must each carry the same local wall-clock + zone line, fresh on every
spawn/wake/turn, rendered by the shared ``render_current_time_line`` with an
injectable clock for deterministic tests. The task/subtask path is covered by
``tests/test_orchestrator_current_time.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from runtime.daemon.dream_runner import build_dream_prompt
from runtime.daemon.thread_runner import (
    build_thread_delta_prompt,
    build_thread_prompt,
)
from runtime.daemon.wake_runner import build_wake_prompt
from runtime.models import (
    DreamRecord,
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
)
from runtime.orchestrator.org_config import OrgConfig

# 04:47Z == 12:47 in Asia/Shanghai (+08:00).
_FROZEN = datetime(2026, 6, 27, 4, 47, tzinfo=timezone.utc)
_FROZEN_LATER = datetime(2026, 6, 27, 6, 47, tzinfo=timezone.utc)  # +2h -> 14:47
_TZ_ORG = OrgConfig(timezone="Asia/Shanghai")
_EXPECTED = "current_time: 2026-06-27T12:47+08:00 (Asia/Shanghai)"


def _wake_prompt(now=None, org_config=_TZ_ORG) -> str:
    return build_wake_prompt(
        org_slug="happyranch",
        work_hour_id="WORKHOUR-1",
        agent_name="dev_agent",
        role="worker",
        team="engineering",
        local_date="2026-06-27",
        slot="09:00",
        mode="windowed",
        preamble="",
        routines=["- do a thing"],
        org_config=org_config,
        now=now,
    )


def _thread() -> ThreadRecord:
    return ThreadRecord(
        id="THR-001", subject="Refund policy",
        started_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )


def _msg(seq: int) -> ThreadMessage:
    return ThreadMessage(
        thread_id="THR-001", seq=seq, speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )


def _thread_prompt(now=None, org_config=_TZ_ORG) -> str:
    return build_thread_prompt(
        thread=_thread(),
        participants=[ThreadParticipant(thread_id="THR-001", agent_name="dev_agent")],
        messages=[_msg(1)],
        invocation_token="TOK", invoked_agent="dev_agent",
        purpose="reply", triggering_seq=1,
        org_config=org_config, now=now,
    )


def _delta_prompt(now=None, org_config=_TZ_ORG) -> str:
    return build_thread_delta_prompt(
        thread=_thread(), new_messages=[_msg(2)],
        invocation_token="TOK", invoked_agent="dev_agent",
        purpose="reply", triggering_seq=2, triggering_message=_msg(2),
        org_config=org_config, now=now,
    )


def _dream_prompt(now=None, org_config=_TZ_ORG) -> str:
    return build_dream_prompt(
        org_slug="happyranch",
        dream=DreamRecord(
            id="DREAM-1", agent_name="dev_agent", local_date="2026-06-27",
            scheduled_for=_FROZEN, window_start=_FROZEN, window_end=_FROZEN,
        ),
        workspace=Path("/tmp"),
        recent_audit=[], task_history="",
        org_config=org_config, now=now,
    )


# --- exact-format assertions (frozen clock + configured org tz) ---

def test_wake_prompt_has_current_time_line() -> None:
    assert _EXPECTED in _wake_prompt(now=lambda: _FROZEN)


def test_thread_full_prompt_has_current_time_line() -> None:
    assert _EXPECTED in _thread_prompt(now=lambda: _FROZEN)


def test_thread_delta_prompt_has_current_time_line() -> None:
    assert _EXPECTED in _delta_prompt(now=lambda: _FROZEN)


def test_dream_prompt_has_current_time_line() -> None:
    # Dreaming inherits the org timezone (dreaming.timezone -> org.timezone).
    assert _EXPECTED in _dream_prompt(now=lambda: _FROZEN)


# --- freshness: recomputed per build, not cached ---

def test_current_time_is_fresh_per_build() -> None:
    for builder in (_wake_prompt, _thread_prompt, _delta_prompt, _dream_prompt):
        early = builder(now=lambda: _FROZEN)
        late = builder(now=lambda: _FROZEN_LATER)
        assert "2026-06-27T12:47+08:00 (Asia/Shanghai)" in early
        assert "2026-06-27T14:47+08:00 (Asia/Shanghai)" in late
        assert early != late
