"""Tests for the TASK-1201 thread escalation guardrail.

When a manager receives a REPLY/BOOTSTRAP invocation in a thread that carries
unresolved ``task_escalated`` system messages whose live task rows are still
supersedable, the prompt MUST name the concrete task ids and instruct the agent
to include ``resolves`` in any continuation dispatch payload.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from runtime.daemon.thread_runner import (
    _maybe_unresolved_escalations_note,
    build_thread_prompt,
    build_thread_delta_prompt,
)
from runtime.models import (
    BlockKind,
    TaskRecord,
    TaskStatus,
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
)
from runtime.orchestrator.org_config import OrgConfig

_NOW = datetime(2026, 6, 29, tzinfo=timezone.utc)


def _fake_thread() -> ThreadRecord:
    return ThreadRecord(
        id="THR-001",
        subject="Escalation follow-up",
        started_at=_NOW,
    )


def _fake_participant(name: str) -> ThreadParticipant:
    return ThreadParticipant(thread_id="THR-001", agent_name=name)


def _system_msg(seq: int, payload: dict) -> ThreadMessage:
    return ThreadMessage(
        thread_id="THR-001",
        seq=seq,
        speaker="system",
        kind=ThreadMessageKind.SYSTEM,
        system_payload=payload,
        created_at=_NOW,
    )


def _message(seq: int, speaker: str, body: str) -> ThreadMessage:
    return ThreadMessage(
        thread_id="THR-001",
        seq=seq,
        speaker=speaker,
        kind=ThreadMessageKind.MESSAGE,
        body_markdown=body,
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# _maybe_unresolved_escalations_note unit tests
# ---------------------------------------------------------------------------


class FakeOrgState:
    """Minimal org_state for _maybe_unresolved_escalations_note."""

    def __init__(self, db, teams=None):
        self.db = db
        self.teams = teams


class FakeTeams:
    """Minimal teams registry — is_team_manager returns True for names in
    ``managers``."""

    def __init__(self, managers: set[str]):
        self._managers = managers

    def is_team_manager(self, name: str) -> bool:
        return name in self._managers


class FakeDB:
    """Minimal DB with a task lookup dict."""

    def __init__(self, tasks: dict[str, TaskRecord]):
        self._tasks = tasks

    def get_task(self, task_id: str):
        return self._tasks.get(task_id)


def _escalated_task(task_id: str) -> TaskRecord:
    return TaskRecord(
        id=task_id,
        brief="some escalated work",
        team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.ESCALATED,
    )


def _completed_task(task_id: str) -> TaskRecord:
    return TaskRecord(
        id=task_id,
        brief="done",
        team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.COMPLETED,
    )


def test_returns_note_for_manager_with_single_unresolved_escalation():
    db = FakeDB({"TASK-900": _escalated_task("TASK-900")})
    org = FakeOrgState(db=db, teams=FakeTeams({"engineering_head"}))
    msgs = [
        _message(1, "founder", "review the escalation"),
        _system_msg(2, {"kind_tag": "task_escalated", "task_id": "TASK-900", "status": "escalated"}),
    ]
    note = _maybe_unresolved_escalations_note(
        messages=msgs,
        org_state=org,
        purpose="reply",
        invoked_agent="engineering_head",
    )
    assert note
    assert "TASK-900" in note
    assert "resolves" in note
    assert "is still awaiting a founder" in note  # singular


def test_returns_note_for_multiple_unresolved_escalations():
    db = FakeDB({
        "TASK-900": _escalated_task("TASK-900"),
        "TASK-901": _escalated_task("TASK-901"),
    })
    org = FakeOrgState(db=db, teams=FakeTeams({"engineering_head"}))
    msgs = [
        _system_msg(1, {"kind_tag": "task_escalated", "task_id": "TASK-900", "status": "escalated"}),
        _message(2, "founder", "ok proceed"),
        _system_msg(3, {"kind_tag": "task_escalated", "task_id": "TASK-901", "status": "escalated"}),
    ]
    note = _maybe_unresolved_escalations_note(
        messages=msgs,
        org_state=org,
        purpose="reply",
        invoked_agent="engineering_head",
    )
    assert note
    assert "TASK-900" in note
    assert "TASK-901" in note
    assert "and are still awaiting" in note  # plural


def test_no_note_for_non_manager():
    db = FakeDB({"TASK-900": _escalated_task("TASK-900")})
    org = FakeOrgState(db=db, teams=FakeTeams(set()))  # no managers — or non-matched
    msgs = [
        _system_msg(1, {"kind_tag": "task_escalated", "task_id": "TASK-900", "status": "escalated"}),
    ]
    note = _maybe_unresolved_escalations_note(
        messages=msgs,
        org_state=org,
        purpose="reply",
        invoked_agent="dev_agent",
    )
    assert note == ""


def test_no_note_when_task_already_resolved():
    db = FakeDB({"TASK-900": _completed_task("TASK-900")})
    org = FakeOrgState(db=db, teams=FakeTeams({"engineering_head"}))
    msgs = [
        _system_msg(1, {"kind_tag": "task_escalated", "task_id": "TASK-900", "status": "escalated"}),
    ]
    note = _maybe_unresolved_escalations_note(
        messages=msgs,
        org_state=org,
        purpose="reply",
        invoked_agent="engineering_head",
    )
    assert note == ""


def test_no_note_when_task_not_found():
    db = FakeDB({})
    org = FakeOrgState(db=db, teams=FakeTeams({"engineering_head"}))
    msgs = [
        _system_msg(1, {"kind_tag": "task_escalated", "task_id": "TASK-999", "status": "escalated"}),
    ]
    note = _maybe_unresolved_escalations_note(
        messages=msgs,
        org_state=org,
        purpose="reply",
        invoked_agent="engineering_head",
    )
    assert note == ""


def test_no_note_for_task_followup_purpose():
    db = FakeDB({"TASK-900": _escalated_task("TASK-900")})
    org = FakeOrgState(db=db, teams=FakeTeams({"engineering_head"}))
    msgs = [
        _system_msg(1, {"kind_tag": "task_escalated", "task_id": "TASK-900", "status": "escalated"}),
    ]
    note = _maybe_unresolved_escalations_note(
        messages=msgs,
        org_state=org,
        purpose="task_followup",
        invoked_agent="engineering_head",
    )
    assert note == ""


def test_no_note_when_no_teams_on_org_state():
    db = FakeDB({"TASK-900": _escalated_task("TASK-900")})
    org = FakeOrgState(db=db)  # no teams attr
    msgs = [
        _system_msg(1, {"kind_tag": "task_escalated", "task_id": "TASK-900", "status": "escalated"}),
    ]
    note = _maybe_unresolved_escalations_note(
        messages=msgs,
        org_state=org,
        purpose="reply",
        invoked_agent="engineering_head",
    )
    assert note == ""


def test_no_note_when_no_escalation_messages():
    db = FakeDB({"TASK-900": _escalated_task("TASK-900")})
    org = FakeOrgState(db=db, teams=FakeTeams({"engineering_head"}))
    msgs = [
        _message(1, "founder", "hi"),
        _message(2, "alice", "hello"),
    ]
    note = _maybe_unresolved_escalations_note(
        messages=msgs,
        org_state=org,
        purpose="reply",
        invoked_agent="engineering_head",
    )
    assert note == ""


def test_does_not_dup_same_task_id():
    """Same TASK-900 escalated twice (e.g. revisit chain) → note names it once."""
    db = FakeDB({"TASK-900": _escalated_task("TASK-900")})
    org = FakeOrgState(db=db, teams=FakeTeams({"engineering_head"}))
    msgs = [
        _system_msg(1, {"kind_tag": "task_escalated", "task_id": "TASK-900", "status": "escalated"}),
        _message(2, "founder", "ok"),
        _system_msg(3, {"kind_tag": "task_escalated", "task_id": "TASK-900", "status": "escalated"}),
    ]
    note = _maybe_unresolved_escalations_note(
        messages=msgs,
        org_state=org,
        purpose="reply",
        invoked_agent="engineering_head",
    )
    assert note.count("TASK-900") == 2  # one in intro, one in JSON field name


# ---------------------------------------------------------------------------
# build_thread_prompt — integrated guardrail test
# ---------------------------------------------------------------------------


def _build_reply_prompt(messages, **overrides):
    defaults = {
        "thread": _fake_thread(),
        "participants": [_fake_participant("engineering_head"), _fake_participant("dev_agent")],
        "messages": messages,
        "invocation_token": "tok-x",
        "invoked_agent": "engineering_head",
        "purpose": "reply",
        "triggering_seq": messages[-1].seq if messages else 1,
        "org_config": OrgConfig(),
    }
    defaults.update(overrides)
    return build_thread_prompt(**defaults)


def test_reply_prompt_without_escalations_has_no_guardrail():
    """Baseline: a normal reply prompt does NOT contain the guardrail note."""
    msgs = [
        _message(1, "founder", "hello"),
        _message(2, "alice", "hi"),
    ]
    prompt = _build_reply_prompt(msgs)
    assert "Unresolved Escalation" not in prompt
    assert "resolves" not in prompt.split("---")[-1]  # purpose-note section, not guardrail


def test_build_delta_prompt_without_guardrail():
    """build_thread_delta_prompt does not inject guardrail itself (injection
    happens in run_invocation)."""
    msgs = [
        _system_msg(5, {"kind_tag": "task_escalated", "task_id": "TASK-900", "status": "escalated"}),
    ]
    prompt = build_thread_delta_prompt(
        thread=_fake_thread(),
        new_messages=msgs,
        invocation_token="tok-x",
        invoked_agent="engineering_head",
        purpose="reply",
        triggering_seq=5,
        triggering_message=msgs[0],
        org_config=OrgConfig(),
    )
    assert "Unresolved Escalation" not in prompt
