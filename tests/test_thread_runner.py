from __future__ import annotations

import pytest
from datetime import datetime, timezone

from runtime.config import Settings
from runtime.daemon.thread_runner import (
    _render_message,
    build_thread_prompt,
    run_invocation,
)
from runtime.infrastructure.database import Database
from runtime.models import (
    ThreadAttachment,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
    TokenUsage,
)
from runtime.orchestrator.org_config import OrgConfig


def test_render_message_includes_attachments() -> None:
    msg = ThreadMessage(
        thread_id="THR-001",
        seq=1,
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="see attached",
        attachments=[
            ThreadAttachment(
                artifact_name="THR-001-report.pdf",
                display_name="report.pdf",
                size_bytes=123,
                content_type=None,
                uploaded_by="founder",
            )
        ],
    )

    rendered = _render_message(msg)

    assert "Attachments:" in rendered
    assert "- report.pdf (`artifact:THR-001-report.pdf`, 123 bytes)" in rendered


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
        org_config=OrgConfig(),
    )
    assert "THR-001" in prompt
    assert "Refund policy" in prompt
    assert "TOK-ABC" in prompt
    assert "Message 1" in prompt
    assert "should we cap?" in prompt
    assert "posted to this thread" in prompt.lower()


class FakeExecutorResult:
    def __init__(
        self,
        success: bool,
        error: str = "",
        token_usage: TokenUsage | None = None,
    ):
        self.success = success
        self.error = error
        self.returncode = 0
        self.session_id = "sess-x"
        self.duration_seconds = 1
        self.agent_session_id = None
        self.stdout_tail = ""
        self.stderr_tail = ""
        self.token_usage = token_usage


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
    import runtime.daemon.thread_runner as runner_mod

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


@pytest.mark.asyncio
async def test_run_invocation_no_callback_writes_thread_token_usage(tmp_path, monkeypatch):
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
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    class _FakeExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            return FakeExecutorResult(
                success=True,
                token_usage=TokenUsage(
                    input_tokens=40,
                    output_tokens=6,
                    model="claude-sonnet",
                ),
            )

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

    rows = db.list_session_token_usage(scope_type="thread", thread_id="THR-001")
    assert len(rows) == 1
    assert rows[0]["task_id"] is None
    assert rows[0]["agent"] == "alice"
    assert rows[0]["session_id"] == "sess-x"
    assert rows[0]["executor"] == "claude"
    assert rows[0]["scope_id"] == "THR-001"
    assert rows[0]["invocation_purpose"] == "reply"
    assert rows[0]["input_tokens"] == 40
    assert rows[0]["output_tokens"] == 6
    assert rows[0]["model"] == "claude-sonnet"


@pytest.mark.asyncio
async def test_no_callback_failure_surfaces_executor_error(tmp_path, monkeypatch):
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
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    class _FailExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            r = FakeExecutorResult(
                success=False,
                error="Command exited with code 1: API Error: 529 Overloaded. "
                "This is a server-side issue, usually temporary.",
            )
            r.returncode = 1
            return r

    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _FailExec(),
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    inv_after = db.get_invocation_any_status(inv.invocation_token)
    assert inv_after.status.value == "failed"
    # The opaque rc code is retained, but the underlying cause is now visible
    # instead of being silently dropped (the 529 was previously only findable
    # by digging into the claude session JSONL).
    assert inv_after.decline_reason.startswith("no_callback: rc=1")
    assert "529 Overloaded" in inv_after.decline_reason
    # The executor's redundant "Command exited with code N" envelope is stripped.
    assert "Command exited with code" not in inv_after.decline_reason


@pytest.mark.asyncio
async def test_failed_thread_invocation_writes_usage_when_executor_returns_it(
    tmp_path, monkeypatch,
):
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
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    class _FailExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            r = FakeExecutorResult(
                success=False,
                error="Command exited with code 1: no callback",
                token_usage=TokenUsage(usage_raw_json='{"usage":"partial"}'),
            )
            r.returncode = 1
            return r

    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _FailExec(),
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    rows = db.list_session_token_usage(scope_type="thread", thread_id="THR-001")
    assert len(rows) == 1
    assert rows[0]["task_id"] is None
    assert rows[0]["agent"] == "alice"
    assert rows[0]["session_id"] == "sess-x"
    assert rows[0]["executor"] == "claude"
    assert rows[0]["scope_id"] == "THR-001"
    assert rows[0]["invocation_purpose"] == "reply"
    assert rows[0]["input_tokens"] is None
    assert rows[0]["output_tokens"] is None
    assert rows[0]["usage_raw_json"] == '{"usage":"partial"}'


def test_thread_runner_builds_pi_executor():
    import runtime.daemon.thread_runner as runner_mod

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

    import runtime.daemon.thread_runner as runner_mod
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

    import runtime.daemon.thread_runner as runner_mod
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

    import runtime.daemon.thread_runner as runner_mod
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
    from runtime.daemon.thread_runner import build_thread_delta_prompt
    from runtime.models import ThreadRecord, ThreadMessage, ThreadMessageKind

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
        org_config=OrgConfig(),
    )
    assert "brand new point" in prompt
    assert "TOK-XYZ" in prompt
    assert "Decline-by-Default" in prompt
    # It must NOT re-ship the full transcript header / participant roster.
    assert "Full message history follows" not in prompt
    assert "Participants:" not in prompt


@pytest.mark.asyncio
async def test_run_invocation_publishes_started_and_settled(tmp_path, monkeypatch):
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
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    published: list[tuple[str, dict]] = []

    class _Bus:
        async def publish(self, topic, event):
            published.append((topic, event))

    import runtime.daemon.thread_runner as runner_mod

    class _FakeExec:
        def __init__(self, **kwargs):
            pass
        def run(self, **kwargs):
            return FakeExecutorResult(success=True)   # no callback → auto-decline

    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: _FakeExec(),
    )

    class OrgWithBus(FakeOrgState):
        def __init__(self, db, root):
            super().__init__(db=db, root=root)
            self.event_bus = _Bus()

    org = OrgWithBus(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token, settings=Settings(),
    )

    kinds = [ev["kind"] for _, ev in published]
    assert "invocation_started" in kinds
    assert "invocation_settled" in kinds
    started = next(ev for _, ev in published if ev["kind"] == "invocation_started")
    assert started["thread_id"] == "THR-001"
    assert started["agent_name"] == "alice"
    assert started["seq"] == 1
    assert started["status"] == "working"


@pytest.mark.asyncio
async def test_same_participant_invocations_serialize(tmp_path, monkeypatch):
    """Two pending invocations for the same Claude participant must NOT run
    their subprocesses concurrently — the per-(thread, agent) invocation lock
    serializes all providers, and the Claude read→run→update path can't race."""
    import asyncio
    import threading
    import time

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m1")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m2")
    inv1 = db.mint_thread_invocation(thread_id="THR-001", agent_name="alice",
                                     triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY)
    inv2 = db.mint_thread_invocation(thread_id="THR-001", agent_name="alice",
                                     triggering_seq=2, purpose=ThreadInvocationPurpose.REPLY)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    counter = {"now": 0, "max": 0}
    clock = threading.Lock()

    class _SlowExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            with clock:
                counter["now"] += 1
                counter["max"] = max(counter["max"], counter["now"])
            time.sleep(0.1)
            with clock:
                counter["now"] -= 1
            r = FakeExecutorResult(success=True)
            r.agent_session_id = "sess-x"
            return r

    import runtime.daemon.thread_runner as runner_mod
    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: _SlowExec())

    org = FakeOrgState(db=db, root=tmp_path)
    await asyncio.gather(
        run_invocation(org_state=org, invocation_token=inv1.invocation_token, settings=Settings()),
        run_invocation(org_state=org, invocation_token=inv2.invocation_token, settings=Settings()),
    )
    # Serialized: at most one subprocess in flight for (THR-001, alice) at a time.
    assert counter["max"] == 1


class _RecordingBus:
    """Captures events published to any thread topic."""
    def __init__(self):
        self.events = []

    async def publish(self, topic, event):
        self.events.append(event)


class _OrgWithBus(FakeOrgState):
    def __init__(self, db, root, bus):
        super().__init__(db=db, root=root)
        self.event_bus = bus


def _seed_thread_with_invocation(tmp_path):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="hi")
    inv = db.mint_thread_invocation(thread_id="THR-001", agent_name="alice",
                                    triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")
    return db, inv


@pytest.mark.asyncio
async def test_decline_publishes_settled_event(tmp_path, monkeypatch):
    """A silent decline must publish a seq-bearing invocation_settled event so
    the live 'working' indicator clears (decline_status carries seq=null)."""
    db, inv = _seed_thread_with_invocation(tmp_path)
    bus = _RecordingBus()

    import runtime.daemon.thread_runner as runner_mod

    class _DeclineExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            # Mimic the agent calling `happyranch threads decline` mid-session.
            db.mark_invocation_declined(inv.invocation_token, decline_reason="nothing to add")
            return FakeExecutorResult(success=True)

    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: _DeclineExec())
    org = _OrgWithBus(db=db, root=tmp_path, bus=bus)
    await run_invocation(org_state=org, invocation_token=inv.invocation_token, settings=Settings())

    settled = [e for e in bus.events if e["kind"] == "invocation_settled"]
    assert settled, "decline must publish invocation_settled"
    assert settled[0]["seq"] == 1
    assert settled[0]["status"] == "declined"


@pytest.mark.asyncio
async def test_runner_crash_publishes_settled_event(tmp_path, monkeypatch):
    """If the executor raises after invocation_started fired, the crash handler
    must publish invocation_settled so the indicator doesn't stick on 'working'."""
    db, inv = _seed_thread_with_invocation(tmp_path)
    bus = _RecordingBus()

    import runtime.daemon.thread_runner as runner_mod

    class _BoomExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: _BoomExec())
    org = _OrgWithBus(db=db, root=tmp_path, bus=bus)
    await run_invocation(org_state=org, invocation_token=inv.invocation_token, settings=Settings())

    kinds = [e["kind"] for e in bus.events]
    assert "invocation_started" in kinds
    assert "invocation_settled" in kinds
    # And the invocation is recorded failed.
    after = db.get_invocation_any_status(inv.invocation_token)
    assert after.status.value == "failed"


@pytest.mark.asyncio
async def test_codex_invocations_serialize(tmp_path, monkeypatch):
    """Two pending invocations for the same Codex participant must NOT run
    concurrently — the provider-agnostic per-(thread, agent) lock serializes
    all executors, not just Claude."""
    import asyncio
    import threading
    import time

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m1")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m2")
    inv1 = db.mint_thread_invocation(thread_id="THR-001", agent_name="alice",
                                     triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY)
    inv2 = db.mint_thread_invocation(thread_id="THR-001", agent_name="alice",
                                     triggering_seq=2, purpose=ThreadInvocationPurpose.REPLY)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: codex\n")

    counter = {"now": 0, "max": 0}
    clock = threading.Lock()

    class _SlowExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            with clock:
                counter["now"] += 1
                counter["max"] = max(counter["max"], counter["now"])
            time.sleep(0.1)
            with clock:
                counter["now"] -= 1
            return FakeExecutorResult(success=True)

    import runtime.daemon.thread_runner as runner_mod
    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: _SlowExec())

    org = FakeOrgState(db=db, root=tmp_path)
    await asyncio.gather(
        run_invocation(org_state=org, invocation_token=inv1.invocation_token, settings=Settings()),
        run_invocation(org_state=org, invocation_token=inv2.invocation_token, settings=Settings()),
    )
    # Serialized: at most one subprocess in flight for (THR-001, alice) at a time.
    assert counter["max"] == 1


@pytest.mark.asyncio
async def test_distinct_agents_same_thread_can_overlap(tmp_path, monkeypatch):
    """Two different agents on the same thread can run concurrently —
    the lock key includes agent_name, so distinct agents do not block each other."""
    import asyncio
    import threading
    import time

    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.add_thread_participant("THR-001", "bob", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m1")
    inv1 = db.mint_thread_invocation(thread_id="THR-001", agent_name="alice",
                                     triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY)
    inv2 = db.mint_thread_invocation(thread_id="THR-001", agent_name="bob",
                                     triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY)
    for agent in ("alice", "bob"):
        ws = tmp_path / "workspaces" / agent
        ws.mkdir(parents=True)
        (ws / "agent.yaml").write_text("executor: claude\n")

    counter = {"now": 0, "max": 0}
    clock = threading.Lock()
    barrier = threading.Barrier(2, timeout=2)

    class _SlowExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            with clock:
                counter["now"] += 1
                counter["max"] = max(counter["max"], counter["now"])
            # Wait for both to enter so we confirm overlap.
            barrier.wait()
            time.sleep(0.1)
            with clock:
                counter["now"] -= 1
            r = FakeExecutorResult(success=True)
            r.agent_session_id = "sess-x"
            return r

    import runtime.daemon.thread_runner as runner_mod
    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: _SlowExec())

    org = FakeOrgState(db=db, root=tmp_path)
    await asyncio.gather(
        run_invocation(org_state=org, invocation_token=inv1.invocation_token, settings=Settings()),
        run_invocation(org_state=org, invocation_token=inv2.invocation_token, settings=Settings()),
    )
    # Distinct agents on the same thread can overlap.
    assert counter["max"] == 2


@pytest.mark.asyncio
async def test_same_agent_distinct_threads_can_overlap(tmp_path, monkeypatch):
    """The same agent on two different threads can run concurrently —
    the lock key includes thread_id, so distinct threads do not block each other."""
    import asyncio
    import threading
    import time

    db = Database(tmp_path / "happyranch.db")
    for thread_id in ("THR-001", "THR-002"):
        db.insert_thread(ThreadRecord(id=thread_id, subject="x"))
        db.add_thread_participant(thread_id, "alice", added_by="founder")
        db.append_thread_message(thread_id=thread_id, speaker="founder",
                                 kind=ThreadMessageKind.MESSAGE, body_markdown="hi")
    inv1 = db.mint_thread_invocation(thread_id="THR-001", agent_name="alice",
                                     triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY)
    inv2 = db.mint_thread_invocation(thread_id="THR-002", agent_name="alice",
                                     triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    counter = {"now": 0, "max": 0}
    clock = threading.Lock()
    barrier = threading.Barrier(2, timeout=2)

    class _SlowExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            with clock:
                counter["now"] += 1
                counter["max"] = max(counter["max"], counter["now"])
            # Wait for both to enter so we confirm overlap.
            barrier.wait()
            time.sleep(0.1)
            with clock:
                counter["now"] -= 1
            r = FakeExecutorResult(success=True)
            r.agent_session_id = "sess-x"
            return r

    import runtime.daemon.thread_runner as runner_mod
    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: _SlowExec())

    org = FakeOrgState(db=db, root=tmp_path)
    await asyncio.gather(
        run_invocation(org_state=org, invocation_token=inv1.invocation_token, settings=Settings()),
        run_invocation(org_state=org, invocation_token=inv2.invocation_token, settings=Settings()),
    )
    # Same agent on distinct threads can overlap.
    assert counter["max"] == 2


@pytest.mark.asyncio
async def test_run_invocation_preserves_abort_reason_when_externally_failed(
    tmp_path, monkeypatch,
):
    """When an invocation is externally failed (founder_aborted) during
    subprocess execution, the runner must not overwrite the abort reason
    with no_callback or emit a misleading extra failure audit."""
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

    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    class _FakeExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            return FakeExecutorResult(success=True)

    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _FakeExec(),
    )

    # Simulate external abort during execution: fail the invocation before
    # the runner inspects post-subprocess state.
    db.fail_invocation(
        inv.invocation_token,
        status=ThreadInvocationStatus.FAILED,
        decline_reason="founder_aborted",
    )

    org = FakeOrgState(db=db, root=tmp_path)

    # Run invocation — should detect externally-failed token and return
    # without overwriting the abort reason.
    await run_invocation(
        org_state=org,
        invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    # The invocation should still be failed with founder_aborted reason.
    after = db.get_invocation_any_status(inv.invocation_token)
    assert after is not None
    assert after.status is ThreadInvocationStatus.FAILED
    assert after.decline_reason == "founder_aborted"

    # No extra failure audit row should be emitted for this invocation
    # (the audit row count for thread invocation failures should be 0).
    # We verify the decline_reason wasn't overwritten to "no_callback".
    assert "no_callback" not in (after.decline_reason or "")
