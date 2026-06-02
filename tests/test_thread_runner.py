from __future__ import annotations

import pytest
from datetime import datetime, timezone

from src.config import Settings
from src.daemon.thread_runner import build_thread_prompt, run_invocation
from src.infrastructure.database import Database
from src.models import (
    ThreadInvocationPurpose,
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
    assert "posted to this thread" in prompt.lower()


class FakeExecutorResult:
    def __init__(self, success: bool, error: str = ""):
        self.success = success
        self.error = error
        self.returncode = 0
        self.session_id = "sess-x"
        self.duration_seconds = 1
        self.agent_session_id = None
        self.stdout_tail = ""
        self.stderr_tail = ""


class FakeOrgState:
    def __init__(self, db: Database, root):
        self.db = db
        self.root = root


@pytest.mark.asyncio
async def test_run_invocation_no_callback_silent_decline(tmp_path, monkeypatch):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )

    # Workspace stub so the runner can find agent.yaml.
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    # Replace the executor builder so .run() returns immediately without callback.
    import src.daemon.thread_runner as runner_mod

    class _FakeExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            return FakeExecutorResult(success=True)

    original_build = runner_mod._build_executor_for_provider
    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _FakeExec(),
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    # Broadcast model: silent decline — no transcript row is inserted.
    msgs = db.list_thread_messages("THR-001")
    assert not any(m.kind.value == "decline" for m in msgs), "silent decline: no transcript row"
    # The invocation row itself transitions to a terminal failed/timeout status.
    inv_after = db.get_invocation_any_status(inv.invocation_token)
    assert inv_after.status.value in {"failed", "timeout"}


def test_thread_runner_builds_pi_executor():
    import src.daemon.thread_runner as runner_mod

    executor = runner_mod._build_executor_for_provider(
        "pi",
        Settings(pi_cli_path="pi-test"),
        paths=None,
    )

    assert executor.__class__.__name__ == "PiExecutor"


class _ResumeRecordingExec:
    """Fake executor that records run() kwargs and returns scripted results."""
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []
    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)


def _ok_result(agent_session_id="claude-new"):
    r = FakeExecutorResult(success=True)
    r.agent_session_id = agent_session_id
    return r


@pytest.mark.asyncio
async def test_turn1_full_prompt_captures_session_id(tmp_path, monkeypatch):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="hello")
    inv = db.mint_thread_invocation(thread_id="THR-001", agent_name="alice",
                                    triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY)
    ws = tmp_path / "workspaces" / "alice"; ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import src.daemon.thread_runner as runner_mod
    fake = _ResumeRecordingExec([_ok_result("claude-sess-001")])
    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: fake)
    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(org_state=org, invocation_token=inv.invocation_token, settings=Settings())

    assert "resume_session_id" not in fake.calls[0]
    sid, seq = db.get_thread_session("THR-001", "alice")
    assert sid == "claude-sess-001"
    assert seq == 1


@pytest.mark.asyncio
async def test_turn2_resumes_with_delta(tmp_path, monkeypatch):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m1")
    db.append_thread_message(thread_id="THR-001", speaker="bob",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m2 newest")
    db.update_thread_session("THR-001", "alice", agent_session_id="claude-prior", last_resumed_seq=1)
    inv = db.mint_thread_invocation(thread_id="THR-001", agent_name="alice",
                                    triggering_seq=2, purpose=ThreadInvocationPurpose.REPLY)
    ws = tmp_path / "workspaces" / "alice"; ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import src.daemon.thread_runner as runner_mod
    fake = _ResumeRecordingExec([_ok_result("claude-prior")])
    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: fake)
    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(org_state=org, invocation_token=inv.invocation_token, settings=Settings())

    assert fake.calls[0].get("resume_session_id") == "claude-prior"
    delta_prompt = fake.calls[0]["prompt"]
    assert "m2 newest" in delta_prompt
    assert "m1" not in delta_prompt
    _, seq = db.get_thread_session("THR-001", "alice")
    assert seq == 2
    actions = {r["action"] for r in db.get_audit_logs("THR-001")}
    assert "agent_session_reused" in actions


@pytest.mark.asyncio
async def test_resume_not_found_falls_back_to_full(tmp_path, monkeypatch):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m1")
    db.update_thread_session("THR-001", "alice", agent_session_id="claude-evicted", last_resumed_seq=0)
    inv = db.mint_thread_invocation(thread_id="THR-001", agent_name="alice",
                                    triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY)
    ws = tmp_path / "workspaces" / "alice"; ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    evicted = FakeExecutorResult(success=False, error="No conversation found for session claude-evicted")
    evicted.returncode = 1
    evicted.stderr_tail = "No conversation found"
    evicted.agent_session_id = None

    import src.daemon.thread_runner as runner_mod
    fake = _ResumeRecordingExec([evicted, _ok_result("claude-fresh")])
    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: fake)
    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(org_state=org, invocation_token=inv.invocation_token, settings=Settings())

    assert len(fake.calls) == 2
    assert fake.calls[0].get("resume_session_id") == "claude-evicted"
    assert "resume_session_id" not in fake.calls[1]
    assert "Full message history follows" in fake.calls[1]["prompt"]
    sid, _ = db.get_thread_session("THR-001", "alice")
    assert sid == "claude-fresh"
    actions = {r["action"] for r in db.get_audit_logs("THR-001")}
    assert "agent_session_evicted_fallback" in actions


def test_build_delta_prompt_excludes_old_history_includes_new():
    from datetime import datetime, timezone
    from src.daemon.thread_runner import build_thread_delta_prompt
    from src.models import ThreadRecord, ThreadMessage, ThreadMessageKind

    thread = ThreadRecord(
        id="THR-001", subject="Refund policy",
        started_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    new_msgs = [
        ThreadMessage(
            thread_id="THR-001", seq=12, speaker="bob",
            kind=ThreadMessageKind.MESSAGE, body_markdown="brand new point",
        ),
    ]
    triggering = new_msgs[0]
    prompt = build_thread_delta_prompt(
        thread=thread, new_messages=new_msgs,
        invocation_token="TOK-XYZ", invoked_agent="alice",
        purpose="reply", triggering_seq=12, triggering_message=triggering,
    )
    assert "brand new point" in prompt
    assert "TOK-XYZ" in prompt
    assert "Decline-by-Default" in prompt
    # It must NOT re-ship the full transcript header / participant roster.
    assert "Full message history follows" not in prompt
    assert "Participants:" not in prompt
