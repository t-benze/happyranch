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
    TokenUsage,
    ThreadInvocationPurpose,
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
    # Must NOT contain a comma-joined resolves value (route contract is singular).
    assert '"resolves": "TASK-900, TASK-901"' not in note
    # Each task gets its own valid per-task example.
    assert 'TASK-900 → {"resolves": "TASK-900"}' in note
    assert 'TASK-901 → {"resolves": "TASK-901"}' in note


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


# ---------------------------------------------------------------------------
# run_invocation boundary tests — prove the executor receives the guardrail
# ---------------------------------------------------------------------------


class _CapturingExecutor:
    """Executor stub that captures the prompt passed to run()."""

    def __init__(self, **kwargs):
        self._prompt: str | None = None

    def run(self, prompt, **kwargs):
        self._prompt = prompt
        return _FakeExecutorResult(success=True)


class _FakeExecutorResult:
    def __init__(self, success: bool, error: str = "",
                 token_usage: "TokenUsage | None" = None):
        self.success = success
        self.error = error
        self.returncode = 0
        self.session_id = "sess-x"
        self.duration_seconds = 1
        self.agent_session_id = None
        self.stdout_tail = ""
        self.stderr_tail = ""
        self.token_usage = token_usage


def _make_org_state_with_teams(db, root, manager_name: str = "engineering_head"):
    """Return an org_state-like object with a .teams registry."""
    class _OS:
        pass
    os = _OS()
    os.db = db
    os.root = root
    os.teams = FakeTeams({manager_name})
    return os


def _insert_escalated_task(db, task_id: str = "TASK-900", team: str = "engineering",
                           agent: str = "engineering_head") -> None:
    db.insert_task(TaskRecord(
        id=task_id, brief="some escalated work", team=team,
        assigned_agent=agent, status=TaskStatus.ESCALATED,
    ))


def _insert_delegated_task_with_live_child(db, task_id: str = "TASK-900",
                                            team: str = "engineering",
                                            agent: str = "engineering_head") -> None:
    """Insert a delegated task with a non-terminal child so it is NOT
    supersedable (Gap-B safety gate: _eligible_supersede_block_kind
    requires _delegated_children_all_terminal)."""
    db.insert_task(TaskRecord(
        id=task_id, brief="delegated work", team=team,
        assigned_agent=agent, status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.DELEGATED,
    ))
    # Live child — ensures _delegated_children_all_terminal returns False.
    db.insert_task(TaskRecord(
        id=f"{task_id}-child", brief="child work", team=team,
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
        parent_task_id=task_id,
    ))


@pytest.mark.asyncio
async def test_run_invocation_injects_guardrail_for_supersedable_escalation(
    tmp_path, monkeypatch
):
    """The executor prompt receives the guardrail note when the thread has an
    unresolved escalated task whose live row is supersedable."""
    from runtime.infrastructure.database import Database
    from runtime.config import Settings
    from runtime.daemon import thread_runner as runner_mod

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="Escalation follow-up"))
    db.add_thread_participant("THR-001", "engineering_head", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="system",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "task_escalated",
                         "task_id": "TASK-900",
                         "status": "escalated"},
    )
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="please continue",
    )
    _insert_escalated_task(db, "TASK-900")
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="engineering_head",
        triggering_seq=2, purpose=ThreadInvocationPurpose.REPLY,
    )

    ws = tmp_path / "workspaces" / "engineering_head"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    cap = _CapturingExecutor()
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: cap,
    )

    org = _make_org_state_with_teams(db, tmp_path)
    await runner_mod.run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    assert cap._prompt is not None, "executor was invoked"
    assert "Unresolved Escalation" in cap._prompt
    assert "TASK-900" in cap._prompt
    assert '"resolves"' in cap._prompt


@pytest.mark.asyncio
async def test_run_invocation_skips_guardrail_for_non_supersedable_predecessor(
    tmp_path, monkeypatch
):
    """When the escalated message's task row is delegated with a live child
    (not supersedable via _eligible_supersede_block_kind), the executor
    prompt does NOT receive the guardrail."""
    from runtime.infrastructure.database import Database
    from runtime.config import Settings
    from runtime.daemon import thread_runner as runner_mod

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="Delegated follow-up"))
    db.add_thread_participant("THR-001", "engineering_head", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="system",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "task_escalated",
                         "task_id": "TASK-900",
                         "status": "escalated"},
    )
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="what's happening?",
    )
    _insert_delegated_task_with_live_child(db, "TASK-900")
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="engineering_head",
        triggering_seq=2, purpose=ThreadInvocationPurpose.REPLY,
    )

    ws = tmp_path / "workspaces" / "engineering_head"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    cap = _CapturingExecutor()
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: cap,
    )

    org = _make_org_state_with_teams(db, tmp_path)
    await runner_mod.run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    assert cap._prompt is not None, "executor was invoked"
    assert "Unresolved Escalation" not in cap._prompt
