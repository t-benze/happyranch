from __future__ import annotations

from types import SimpleNamespace

import pytest
from datetime import datetime, timezone

from runtime.config import Settings
from runtime.daemon.thread_runner import (
    _render_message,
    build_thread_delta_prompt,
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


@pytest.fixture(autouse=True)
def _seed_active_agents_for_thread_runner(tmp_path):
    """Thread-runner launch is fail-closed: an active AgentDef is required.

    Legacy tests created only a workspace/agent.yaml. Seed active frontmatter
    for the agents used in this module so the launch guard admits them.
    """
    from runtime.orchestrator._paths import OrgPaths
    from tests.conftest import seed_test_agents
    seed_test_agents(OrgPaths(root=tmp_path), ("alice", "bob"))


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


@pytest.mark.parametrize("marker", [
    "## [RESERVED] Active Team Escalation Policy",
    "<!-- BEGIN HAPPYRANCH ACTIVE TEAM POLICY -->",
    "<!-- END HAPPYRANCH ACTIVE TEAM POLICY -->",
])
@pytest.mark.parametrize("resumed", [False, True])
def test_thread_shipping_builders_reject_reserved_fresh_and_resumed_inputs(marker, resumed):
    from runtime.orchestrator.active_authority_policy import ActiveAuthorityPolicyError
    thread = ThreadRecord(id="THR-X", subject="safe", started_at=datetime.now(timezone.utc))
    msg = ThreadMessage(
        thread_id="THR-X", seq=1, speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown=marker,
    )
    with pytest.raises(ActiveAuthorityPolicyError, match="server-reserved"):
        if resumed:
            build_thread_delta_prompt(
                thread=thread, new_messages=[msg], invocation_token="TOK",
                invoked_agent="alice", purpose="reply", triggering_seq=1,
                triggering_message=msg, org_config=OrgConfig(),
            )
        else:
            build_thread_prompt(
                thread=thread, participants=[], messages=[msg],
                invocation_token="TOK", invoked_agent="alice", purpose="reply",
                triggering_seq=1, org_config=OrgConfig(),
            )


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
        self.slug = "test"


def _seed_queued_reply(db, thread_id, agent_name, triggering_seq):
    """Mint a pending REPLY and seed its delivery-state queued slot so the
    runner's queued→running CAS succeeds (mirrors the durable stage a queued
    coalesced wake leaves behind, as produced by record_conversational_arrival)."""
    inv = db.mint_thread_invocation(
        thread_id=thread_id, agent_name=agent_name,
        triggering_seq=triggering_seq, purpose=ThreadInvocationPurpose.REPLY,
    )
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "queued_invocation_token, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (thread_id, agent_name, triggering_seq - 1, triggering_seq,
         inv.invocation_token, "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()
    return inv


CLAUDE_CREDIT_EXHAUSTED_RESULT = (
    '{"type":"result","subtype":"error_during_execution","is_error":true,'
    '"duration_ms":12543,"num_turns":1,"session_id":"sess-credit-limit",'
    '"total_cost_usd":0.0142,"usage":{"input_tokens":1643,"output_tokens":0,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0,'
    '"service_tier":"standard"},"modelUsage":{"claude-opus-4-1":'
    '{"inputTokens":1643,"outputTokens":0,"cacheReadInputTokens":0,'
    '"cacheCreationInputTokens":0}},"permission_denials":[],'
    '"server_tool_use":{"web_search_requests":0,"web_fetch_requests":0},'
    '"api_error_status":429,"terminal_reason":"credit_exhausted",'
    '"result":"Your account has insufficient credits to run this request. '
    'Please add credits or wait for the billing limit to reset before retrying."}'
)


def test_executor_error_detail_retains_credit_exhaustion_diagnostics() -> None:
    """A realistic Claude result envelope fits within the shared 2 KB cap."""
    from runtime.daemon.thread_runner import _executor_error_detail

    assert 300 < len(CLAUDE_CREDIT_EXHAUSTED_RESULT) <= 2000
    result = SimpleNamespace(
        error=f"Command exited with code 1: {CLAUDE_CREDIT_EXHAUSTED_RESULT}",
        stderr_tail="",
    )

    detail = _executor_error_detail(result, 1)

    assert detail == CLAUDE_CREDIT_EXHAUSTED_RESULT
    assert '"api_error_status":429' in detail
    assert '"terminal_reason":"credit_exhausted"' in detail
    assert '"result":"Your account has insufficient credits' in detail


def test_executor_error_detail_prefers_structured_failure_when_stderr_empty() -> None:
    from runtime.daemon.thread_runner import _executor_error_detail

    result = SimpleNamespace(
        error=(
            "Command exited with code 1: "
            '{"type":"result","subtype":"error_during_execution",'
            '"is_error":true,"result":"You\'ve hit your session limit"}'
        ),
        stderr_tail="",
        terminal_error="session_limit",
    )

    assert _executor_error_detail(result, 1) == "session_limit"


def test_executor_error_detail_prefers_structured_failure_over_known_trust_warning() -> None:
    from runtime.daemon.thread_runner import _executor_error_detail

    result = SimpleNamespace(
        error=("Command exited with code 1: Running as unit: "
               "happyranch-session-THR-220-67724507.scope; invocation ID: abc123"),
        stderr_tail=(
            "Running as unit: happyranch-session-THR-220-67724507.scope; "
            "invocation ID: abc123\n"
            "Ignoring 1 permissions.allow entry from .claude/settings.json: "
            "this workspace has not been trusted.\n"
        ),
        stdout_tail=(
            '{"type":"result","subtype":"error_during_execution",'
            '"is_error":true,"result":"You\'ve hit your session limit '
            '· resets 7:10pm (Asia/Shanghai)"}'
        ),
        terminal_error="session_limit",
    )

    assert _executor_error_detail(result, 1) == "session_limit"


@pytest.mark.parametrize("stderr", [
    "fatal: this workspace has not been trusted because launch failed",
    ("Running as unit: happyranch-session-THR-220-a.scope; invocation ID: abc123\n"
     "meaningful second line"),
    "prefix Ignoring 1 permissions.allow entry from .claude/settings.json: this workspace has not been trusted.",
])
def test_executor_error_detail_does_not_strip_trust_word_lookalikes(stderr: str) -> None:
    from runtime.daemon.thread_runner import _executor_error_detail

    result = SimpleNamespace(
        error=f"Command exited with code 1: {stderr}",
        stderr_tail=stderr,
        stdout_tail='{"result":"Session limit reached"}',
        terminal_error="session_limit",
    )

    assert _executor_error_detail(result, 1) != "session_limit"


@pytest.mark.asyncio
async def test_run_invocation_no_callback_silent_decline(tmp_path, monkeypatch):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)

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
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
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
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
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
async def test_no_callback_failure_preserves_claude_diagnostics_in_audit_reason(
    tmp_path, monkeypatch,
):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    class _FailExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            result = FakeExecutorResult(
                success=False,
                error=(
                    "Command exited with code 1: "
                    f"{CLAUDE_CREDIT_EXHAUSTED_RESULT}"
                ),
            )
            result.returncode = 1
            result.stdout_tail = "structured stdout"
            result.stderr_tail = "raw stderr warning"
            return result

    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _FailExec(),
    )

    await run_invocation(
        org_state=FakeOrgState(db=db, root=tmp_path),
        invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    inv_after = db.get_invocation_any_status(inv.invocation_token)
    assert inv_after.decline_reason is not None
    assert len(inv_after.decline_reason) > 300
    for diagnostic in (
        '"api_error_status":429',
        '"terminal_reason":"credit_exhausted"',
        '"result":"Your account has insufficient credits',
    ):
        assert diagnostic in inv_after.decline_reason

    audit_row = next(
        row for row in db.get_audit_logs("THR-001")
        if row["action"] == "thread_invocation_failed"
    )
    assert audit_row["payload"]["reason"] == inv_after.decline_reason
    assert audit_row["payload"]["stdout_tail"] == "structured stdout"
    assert audit_row["payload"]["stderr_tail"] == "raw stderr warning"


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
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
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
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
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
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=2)
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
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m2")
    db.update_thread_session("THR-001", "alice", agent_session_id="claude-evicted", last_resumed_seq=1)
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=2)
    ws = tmp_path / "workspaces" / "alice"; ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    evicted = FakeExecutorResult(success=False, error="Command exited with code 1: No conversation found for session claude-evicted")
    evicted.returncode = 1
    evicted.stderr_tail = "No conversation found with session ID: claude-evicted"
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


@pytest.mark.asyncio
async def test_eviction_fallback_failure_invalidates_id_keeps_watermark(tmp_path, monkeypatch):
    """THR-200: when the eviction fallback ALSO fails, the stale provider id
    must remain NULL and the delivery watermark must NOT advance — the
    eviction audit and the invalidation are one transaction, applied BEFORE
    the fallback launch. The next wake re-attempts the same range from a full
    prompt instead of a doomed resume."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m1")
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m2")
    db.update_thread_session(
        "THR-001", "alice",
        agent_session_id="claude-stale", last_resumed_seq=1,
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=2)
    ws = tmp_path / "workspaces" / "alice"; ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    evicted = FakeExecutorResult(success=False, error="No conversation found for session claude-stale")
    evicted.returncode = 1
    evicted.stderr_tail = "No conversation found with session ID: claude-stale"
    evicted.agent_session_id = None
    fallback_failed = FakeExecutorResult(success=False, error="Command exited with code 1: boom")
    fallback_failed.returncode = 1
    fallback_failed.agent_session_id = None

    import runtime.daemon.thread_runner as runner_mod
    fake = _ResumeRecordingExec([evicted, fallback_failed])
    monkeypatch.setattr(runner_mod, "_build_executor_for_provider",
                        lambda provider, settings, paths: fake)
    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(org_state=org, invocation_token=inv.invocation_token, settings=Settings())

    # Exactly one eviction audit (transactional with the invalidation).
    audits = [r for r in db.get_audit_logs("THR-001")
              if r["action"] == "agent_session_evicted_fallback"]
    assert len(audits) == 1
    assert audits[0]["payload"]["stale_session_id"] == "claude-stale"
    # Id NULL (stale id durably invalidated), watermark preserved (1).
    sid, watermark = db.get_thread_session("THR-001", "alice")
    assert sid is None
    assert watermark == 1
    # Invocation settled FAILED exactly once (no nudge re-invoke after fallback).
    assert len(fake.calls) == 2
    after = db.get_invocation_any_status(inv.invocation_token)
    assert after.status == ThreadInvocationStatus.FAILED


@pytest.mark.asyncio
async def test_resume_watermark_above_running_from_uses_full_prompt(tmp_path, monkeypatch):
    """Range correctness `>`: when the stored watermark sits ABOVE the claim's
    running_from_seq, the runner must not trust the delta — it rebuilds the
    full prompt so no required sequence is ever omitted."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    for body in ("m1", "m2", "m3"):
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=body,
        )
    inv = _seed_queued_wake(db, "THR-001", "alice", ack=0, req=3)
    db.update_thread_session(
        "THR-001", "alice",
        agent_session_id="claude-prior", last_resumed_seq=3,
    )  # watermark 3 > running_from 1 → full prompt required
    ws = tmp_path / "workspaces" / "alice"; ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod
    prompts: list[str] = []
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: _RecordingExec(prompts=prompts),
    )
    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    first = prompts[0]
    assert "m1" in first and "m2" in first and "m3" in first  # full transcript
    assert "## Delivery range" in first
    assert "1 through 3" in first


@pytest.mark.asyncio
async def test_equality_state_recovers_after_settled_full_prompt_turn(tmp_path, monkeypatch):
    """THR-200 binding correction: the equality state (watermark ==
    running_from_seq) is NOT a permanent wedge. After ONE safely transported,
    successfully terminal-settled full-prompt turn, both watermarks converge
    and resume eligibility returns — no standalone watermark comparison
    change is implemented (THR-198 recovery = transport, not a code fix)."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    for body in ("m1", "m2", "m3"):
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=body,
        )
    ws = tmp_path / "workspaces" / "alice"; ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    # ── Phase 1: equality state — watermark 1 == running_from 1 ──────────
    inv1 = _seed_queued_wake(db, "THR-001", "alice", ack=0, req=3)
    db.update_thread_session(
        "THR-001", "alice",
        agent_session_id="claude-prior", last_resumed_seq=1,
    )

    import runtime.daemon.thread_runner as runner_mod
    calls: list[dict] = []

    class _Phase1Exec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            calls.append(kwargs)
            # The agent posts its reply mid-run (terminal settle).
            db.consume_invocation(inv1.invocation_token)
            db.settle_conversational_reply(
                token=inv1.invocation_token, outcome="reply",
            )
            r = FakeExecutorResult(success=True)
            r.agent_session_id = "claude-fresh"
            return r

    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: _Phase1Exec(),
    )
    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv1.invocation_token,
        settings=Settings(),
    )

    # Equality turn ran a FULL prompt (no resume), settled, and advanced the
    # stored watermark to the same frontier as the acknowledgement.
    assert calls[0].get("resume_session_id") is None
    assert "m1" in calls[0]["prompt"] and "m3" in calls[0]["prompt"]
    sid, watermark = db.get_thread_session("THR-001", "alice")
    assert sid == "claude-fresh"
    assert watermark == 3
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st.acknowledged_through_seq == 3

    # ── Phase 2: a NEW message arrives; resume must be eligible again ────
    db.append_thread_message(thread_id="THR-001", speaker="founder",
                             kind=ThreadMessageKind.MESSAGE, body_markdown="m4")
    inv2 = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=4, purpose=ThreadInvocationPurpose.REPLY,
    )
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "queued_invocation_token, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(thread_id, agent_name) DO UPDATE SET "
        "acknowledged_through_seq = excluded.acknowledged_through_seq, "
        "required_through_seq = excluded.required_through_seq, "
        "queued_invocation_token = excluded.queued_invocation_token, "
        "running_invocation_token = NULL, running_from_seq = NULL, "
        "running_through_seq = NULL, last_terminal_reason = NULL, "
        "last_terminal_at = NULL, updated_at = excluded.updated_at",
        ("THR-001", "alice", 3, 4, inv2.invocation_token,
         "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()

    prompts2: list[str] = []
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: _RecordingExec(prompts=prompts2),
    )
    await run_invocation(
        org_state=org, invocation_token=inv2.invocation_token,
        settings=Settings(),
    )

    # Resume eligible again: delta ships ONLY the new message; no required
    # sequence is omitted (m1..m3 are already durably delivered/acknowledged).
    assert "m4" in prompts2[0]
    assert "m1" not in prompts2[0] and "m2" not in prompts2[0] and "m3" not in prompts2[0]
    assert "## Delivery range" in prompts2[0]
    assert "4 through 4" in prompts2[0]


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
    # TASK-5735: the resumed/delta REPLY builder must inject the same
    # behavioral doctrine clauses as the full builder — inspect the full
    # supplied conversation beyond the delivery range, and silently decline
    # when the invoked agent already substantively answered in a later own
    # message (with the distinct-unanswered-request exception preserved).
    assert "already substantively answered" in prompt
    assert "full conversation" in prompt
    assert "delivery range" in prompt
    assert "distinct request you have not yet answered" in prompt
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
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
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
    """A conversational REPLY and a BOOTSTRAP for the same participant must
    NOT run their subprocesses concurrently — the per-(thread, agent)
    invocation lock serializes all providers, and the Claude read→run→update
    path can't race. (Two coalesced REPLYs can't coexist: the delivery-state
    table holds a single queued slot per pair.)"""
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
    inv1 = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    inv2 = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
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
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
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
    """A conversational REPLY and a BOOTSTRAP for the same Codex participant
    must NOT run concurrently — the provider-agnostic per-(thread, agent) lock
    serializes all executors, not just Claude."""
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
    inv1 = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    inv2 = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
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
    inv1 = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    inv2 = _seed_queued_reply(db, "THR-001", "bob", triggering_seq=1)
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
    inv1 = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    inv2 = _seed_queued_reply(db, "THR-002", "alice", triggering_seq=1)
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
async def test_externally_failed_invocation_preserves_abort_reason(tmp_path, monkeypatch):
    """When a subprocess exits after the invocation was externally failed
    (e.g. founder abort), the runner must NOT overwrite the abort reason."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)

    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    class _AbortDuringExec:
        """Fake executor that simulates a founder abort during execution."""
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            # Simulate external abort: mark the invocation as failed with
            # founder_aborted reason while the "subprocess" is running.
            db.fail_invocation(
                inv.invocation_token,
                status=ThreadInvocationStatus.FAILED,
                decline_reason="founder_aborted",
            )
            return FakeExecutorResult(success=True)

    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _AbortDuringExec(),
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    inv_after = db.get_invocation_any_status(inv.invocation_token)
    assert inv_after.status == ThreadInvocationStatus.FAILED
    assert inv_after.decline_reason == "founder_aborted"


@pytest.mark.asyncio
async def test_externally_aborted_invocation_skips_session_update(tmp_path, monkeypatch):
    """When an invocation is externally aborted during subprocess execution,
    the runner must NOT persist the stale agent_session_id or advance
    last_resumed_seq — an aborted invocation must not become the resumable
    Claude session for a later reply."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)

    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    class _AbortDuringExecWithSession:
        """Fake executor that simulates founder abort during execution and
        returns a stale agent_session_id — the runner must NOT store it for
        future resume."""
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            db.fail_invocation(
                inv.invocation_token,
                status=ThreadInvocationStatus.FAILED,
                decline_reason="founder_aborted",
            )
            result = FakeExecutorResult(success=True)
            result.agent_session_id = "stale-aborted-session"
            return result

    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _AbortDuringExecWithSession(),
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    # Invocation row must still reflect the abort reason.
    inv_after = db.get_invocation_any_status(inv.invocation_token)
    assert inv_after.status == ThreadInvocationStatus.FAILED
    assert inv_after.decline_reason == "founder_aborted"

    # Thread session must NOT be polluted with the stale aborted session.
    sid, watermark = db.get_thread_session("THR-001", "alice")
    assert sid != "stale-aborted-session", (
        "aborted invocation must not store its agent_session_id for future resume"
    )
    assert watermark == 0, (
        "aborted invocation must not advance last_resumed_seq"
    )


# -----------------------------------------------------------------
# THR-071 slice (3) — bounded terminal-callback enforcement
# -----------------------------------------------------------------


def _clean_exit_result(agent_session_id="claude-sess-x"):
    r = FakeExecutorResult(success=True)
    r.agent_session_id = agent_session_id
    return r


class _ScriptedExec:
    """Fake executor that returns scripted results in order."""
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)


@pytest.mark.asyncio
async def test_clean_exit_no_callback_reinvokes_once_and_recovers(tmp_path, monkeypatch):
    """rc==0 clean exit + unconsumed → exactly ONE re-invoke with nudge →
    second pass consumes (CONSUMED) → session persisted, no auto-decline.

    Issue #568 regression: BOTH executor.run attempts (initial invocation +
    nudge/reinvoke) receive the authoritative AgentDef.model value."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")
    # Authoritative AgentDef frontmatter with gpt-5.6-terra model.
    agent_dir = tmp_path / "org" / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "alice.md").write_text(
        "---\nname: alice\nteam: engineering\nrole: worker\n"
        "executor: claude\nmodel: gpt-5.6-terra\n---\n\n"
        "You are a test agent.\n"
    )

    import runtime.daemon.thread_runner as runner_mod

    # First invocation: clean exit, no callback → still pending.
    # Second invocation (re-invoke with nudge): agent calls reply mid-session.
    fake = _ScriptedExec([
        _clean_exit_result("claude-first"),
        _clean_exit_result("claude-nudged"),
    ])

    # On the second call (the nudge re-invoke), simulate the agent calling
    # `happyranch threads reply` by consuming the invocation mid-exec.
    original_run = fake.run
    def _run_with_reply(**kwargs):
        if len(fake.calls) == 1:  # second call (first is already consumed)
            db.consume_invocation(inv.invocation_token)
        return original_run(**kwargs)
    fake.run = _run_with_reply

    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: fake,
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    # Exactly TWO invocations happened (first + one re-invoke).
    assert len(fake.calls) == 2, f"expected 2 invocations, got {len(fake.calls)}"

    # ── Issue #568: BOTH calls must receive the authoritative model ─────
    assert fake.calls[0].get("model") == "gpt-5.6-terra", (
        f"initial invocation: expected model='gpt-5.6-terra', "
        f"got {fake.calls[0].get('model')!r}"
    )
    assert fake.calls[1].get("model") == "gpt-5.6-terra", (
        f"nudge re-invoke: expected model='gpt-5.6-terra', "
        f"got {fake.calls[1].get('model')!r}"
    )

    # Second prompt must be a nudge.
    nudge_prompt = fake.calls[1]["prompt"]
    assert "ended without" in nudge_prompt.lower() or "forgot" in nudge_prompt.lower(), \
        f"nudge prompt missing corrective directive: {nudge_prompt[:200]}"

    # First call: no resume.
    assert "resume_session_id" not in fake.calls[0]
    # Second call: resumes the first call's agent_session_id.
    assert fake.calls[1].get("resume_session_id") == "claude-first"

    # Invocation was consumed (by the nudge's simulated reply).
    after = db.get_invocation_any_status(inv.invocation_token)
    assert after.status == ThreadInvocationStatus.CONSUMED, \
        f"expected consumed, got {after.status}"

    # Session persisted.
    sid, watermark = db.get_thread_session("THR-001", "alice")
    assert sid == "claude-nudged"
    assert watermark == 1


@pytest.mark.asyncio
async def test_clean_exit_no_callback_reinvoke_still_pending_auto_decline(tmp_path, monkeypatch):
    """rc==0 clean exit + unconsumed → ONE re-invoke → STILL pending →
    auto-decline with reason tagged no_callback_after_reprompt."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    # Both invocations: clean exit, no callback → never consumed.
    fake = _ScriptedExec([
        _clean_exit_result("claude-first"),
        _clean_exit_result("claude-second"),
    ])
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: fake,
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    # Exactly TWO invocations happened (first + one re-invoke).
    assert len(fake.calls) == 2, f"expected 2 invocations, got {len(fake.calls)}"

    # Invocation is FAILED with no_callback_after_reprompt reason.
    after = db.get_invocation_any_status(inv.invocation_token)
    assert after.status == ThreadInvocationStatus.FAILED
    assert after.decline_reason is not None
    assert after.decline_reason.startswith("no_callback_after_reprompt: rc=0"), \
        f"unexpected reason: {after.decline_reason}"

    # No transcript row (silent decline).
    msgs = db.list_thread_messages("THR-001")
    assert not any(m.kind.value == "decline" for m in msgs)

    # Session was persisted from the first invocation; nudge re-invoke
    # also updates the session for consistency.
    sid, watermark = db.get_thread_session("THR-001", "alice")
    assert sid == "claude-second"
    assert watermark == 1


@pytest.mark.asyncio
async def test_nonzero_rc_no_callback_not_reinvoked(tmp_path, monkeypatch):
    """rc!=0 → NOT re-invoked. Existing no_callback path unchanged."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    fail_result = FakeExecutorResult(success=False)
    fail_result.returncode = 1
    fake = _ScriptedExec([fail_result])
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: fake,
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    # Exactly ONE invocation — no re-invoke for rc!=0.
    assert len(fake.calls) == 1, (
        f"rc!=0 must NOT trigger re-invoke, got {len(fake.calls)} calls"
    )

    # Existing no_callback path: FAILED with no_callback: rc=1.
    after = db.get_invocation_any_status(inv.invocation_token)
    assert after.status == ThreadInvocationStatus.FAILED
    assert after.decline_reason is not None
    assert after.decline_reason.startswith("no_callback: rc=1"), \
        f"unexpected reason: {after.decline_reason}"


@pytest.mark.asyncio
async def test_timeout_no_callback_not_reinvoked(tmp_path, monkeypatch):
    """timeout → NOT re-invoked. Existing timeout path unchanged."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    timeout_result = FakeExecutorResult(success=False, error="timeout")
    timeout_result.returncode = 143
    fake = _ScriptedExec([timeout_result])
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: fake,
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    # Exactly ONE invocation — no re-invoke for timeout.
    assert len(fake.calls) == 1, (
        f"timeout must NOT trigger re-invoke, got {len(fake.calls)} calls"
    )

    # Existing timeout path: TIMEOUT with invocation_timeout.
    after = db.get_invocation_any_status(inv.invocation_token)
    assert after.status == ThreadInvocationStatus.TIMEOUT
    assert after.decline_reason == "invocation_timeout"


# --- THR-071 HIGH-2 REVISE: nudge failure must mirror first-pass classification ---


class _ScriptedExecWithCrash:
    """Fake executor: first call clean-exits, second call raises."""
    def __init__(self, *, agent_session_id="claude-sess-x"):
        self._first = True
        self._sid = agent_session_id
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        if self._first:
            self._first = False
            r = FakeExecutorResult(success=True)
            r.agent_session_id = self._sid
            return r
        raise RuntimeError("nudge crash simulation")


@pytest.mark.asyncio
async def test_nudge_reinvoke_exception_persists_runner_crash(tmp_path, monkeypatch):
    """Nudge re-invoke raises exception → persisted reason is runner_crash,
    NOT no_callback_after_reprompt."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    fake = _ScriptedExecWithCrash()
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: fake,
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    # Exactly TWO invocations happened (first + attempted re-invoke).
    assert len(fake.calls) == 2, f"expected 2 calls, got {len(fake.calls)}"

    after = db.get_invocation_any_status(inv.invocation_token)
    assert after.status == ThreadInvocationStatus.FAILED
    assert after.decline_reason is not None
    assert after.decline_reason.startswith("runner_crash:"), \
        f"expected runner_crash, got: {after.decline_reason}"
    assert "no_callback_after_reprompt" not in (after.decline_reason or ""), \
        "must NOT tag nudge crash as no_callback_after_reprompt"


@pytest.mark.asyncio
async def test_nudge_reinvoke_timeout_preserves_timeout_classification(tmp_path, monkeypatch):
    """Nudge re-invoke exits with timeout → invocation_timeout, not no_callback_after_reprompt."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    clean = _clean_exit_result("claude-first")
    timeout = FakeExecutorResult(success=False, error="timeout")
    timeout.returncode = 143
    fake = _ScriptedExec([clean, timeout])
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: fake,
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    assert len(fake.calls) == 2, f"expected 2 calls, got {len(fake.calls)}"

    after = db.get_invocation_any_status(inv.invocation_token)
    assert after.status == ThreadInvocationStatus.TIMEOUT
    assert after.decline_reason == "invocation_timeout", \
        f"expected invocation_timeout, got: {after.decline_reason}"


@pytest.mark.asyncio
async def test_nudge_reinvoke_nonzero_rc_preserves_no_callback_classification(tmp_path, monkeypatch):
    """Nudge re-invoke exits with rc!=0 → no_callback: rc=N, not no_callback_after_reprompt."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    clean = _clean_exit_result("claude-first")
    fail = FakeExecutorResult(success=False)
    fail.returncode = 1
    fail.error = "Command exited with code 1: API Error: 529 Overloaded"
    fake = _ScriptedExec([clean, fail])
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: fake,
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    assert len(fake.calls) == 2, f"expected 2 calls, got {len(fake.calls)}"

    after = db.get_invocation_any_status(inv.invocation_token)
    assert after.status == ThreadInvocationStatus.FAILED
    assert after.decline_reason is not None
    assert after.decline_reason.startswith("no_callback: rc=1"), \
        f"expected no_callback: rc=1, got: {after.decline_reason}"
    assert "no_callback_after_reprompt" not in (after.decline_reason or ""), \
        "must NOT tag nudge nonzero rc as no_callback_after_reprompt"


# ── Issue #568: AgentDef.model forwarding to executor.run ──────────────

class _CapturingFakeExec:
    """Fake executor that captures run kwargs for assertion."""
    def __init__(self, **kwargs):
        pass

    def run(self, **kwargs):
        self.last_kwargs = kwargs
        return FakeExecutorResult(success=True)


@pytest.mark.asyncio
async def test_thread_invocation_forwards_agent_model_to_executor_run(
    tmp_path, monkeypatch,
):
    """When AgentDef.model is set, thread runner passes it to executor.run(model=...)."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")
    # Create AgentDef with a model in org/agents/<name>.md
    agent_dir = tmp_path / "org" / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "alice.md").write_text(
        "---\nname: alice\nteam: engineering\nrole: worker\n"
        "executor: claude\nmodel: gpt-5.6-terra\n---\n\n"
        "You are a test agent.\n"
    )

    import runtime.daemon.thread_runner as runner_mod
    fake_exec = _CapturingFakeExec()
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: fake_exec,
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    assert fake_exec.last_kwargs.get("model") == "gpt-5.6-terra", (
        f"expected model='gpt-5.6-terra', got {fake_exec.last_kwargs.get('model')!r}"
    )


@pytest.mark.asyncio
async def test_thread_invocation_refreshes_repos_before_executor_run(tmp_path, monkeypatch):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-REFRESH", subject="x"))
    db.add_thread_participant("THR-REFRESH", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-REFRESH", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_reply(db, "THR-REFRESH", "alice", triggering_seq=1)
    workspace = tmp_path / "workspaces" / "alice"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("executor: claude\n")
    agent_dir = tmp_path / "org" / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "alice.md").write_text(
        "---\nname: alice\nteam: engineering\nrole: worker\nexecutor: claude\n---\n\n"
        "You are a test agent.\n"
    )

    import runtime.daemon.thread_runner as runner_mod
    events: list[str] = []
    monkeypatch.setattr(
        runner_mod, "refresh_workspace_repos",
        lambda ws: (events.append("refresh_workspace_repos"), {"happyranch": True})[1],
    )

    class _Executor:
        def run(self, **_kwargs):
            events.append("executor.run")
            db.mark_invocation_declined(inv.invocation_token, decline_reason="done")
            return FakeExecutorResult(success=True)

    monkeypatch.setattr(runner_mod, "_build_executor_for_provider", lambda *_args: _Executor())

    await run_invocation(
        org_state=FakeOrgState(db=db, root=tmp_path),
        invocation_token=inv.invocation_token, settings=Settings(),
    )

    assert events == ["refresh_workspace_repos", "executor.run"]


@pytest.mark.asyncio
async def test_thread_invocation_no_model_preserves_default_behavior(
    tmp_path, monkeypatch,
):
    """When AgentDef.model is absent, executor.run(model=...) is None (default)."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")
    # AgentDef with NO model
    agent_dir = tmp_path / "org" / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "alice.md").write_text(
        "---\nname: alice\nteam: engineering\nrole: worker\n"
        "executor: claude\n---\n\n"
        "You are a test agent.\n"
    )

    import runtime.daemon.thread_runner as runner_mod
    fake_exec = _CapturingFakeExec()
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: fake_exec,
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    # model is either absent or None — existing default behavior
    assert fake_exec.last_kwargs.get("model") in (None, ), (
        f"model should be None when AgentDef has no model, "
        f"got {fake_exec.last_kwargs.get('model')!r}"
    )


@pytest.mark.asyncio
async def test_thread_invocation_session_not_found_fallback_forwards_model(
    tmp_path, monkeypatch,
):
    """Session-not-found eviction fallback also passes model."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="follow-up",
    )
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=2)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")
    agent_dir = tmp_path / "org" / "agents"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "alice.md").write_text(
        "---\nname: alice\nteam: engineering\nrole: worker\n"
        "executor: claude\nmodel: gpt-5.6-terra\n---\n\n"
        "You are a test agent.\n"
    )

    import runtime.daemon.thread_runner as runner_mod

    # First call: simulate session-not-found so the fallback triggers.
    call_count = [0]
    all_model_kwargs = []

    class _SessionNotFoundExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            call_count[0] += 1
            all_model_kwargs.append(kwargs.get("model"))
            if call_count[0] == 1:
                # First invocation: resume attempt fails with session-not-found
                # (production shape: the exact anchored marker lands on stderr
                # with the ATTEMPTED id, rc=1 — the classifier reads
                # stderr_tail only, never the error envelope).
                result = FakeExecutorResult(success=False, error="No session found")
                result.returncode = 1
                result.stderr_tail = "No conversation found with session ID: stale-sid"
                return result
            else:
                return FakeExecutorResult(success=True)

    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider",
        lambda provider, settings, paths: _SessionNotFoundExec(),
    )

    # Stub thread_session so resume is attempted.
    db.update_thread_session("THR-001", "alice", agent_session_id="stale-sid", last_resumed_seq=1)

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )

    assert call_count[0] == 2, f"expected 2 executor.run calls, got {call_count[0]}"
    assert all_model_kwargs[0] == "gpt-5.6-terra", (
        f"first call (resume): expected model='gpt-5.6-terra', got {all_model_kwargs[0]!r}"
    )
    assert all_model_kwargs[1] == "gpt-5.6-terra", (
        f"second call (fallback): expected model='gpt-5.6-terra', got {all_model_kwargs[1]!r}"
    )


# ── GitHub #688 Phase 1 Slice B: runner claim/settle/prompt-range wiring ──


def _seed_queued_wake(db, thread_id, agent_name, *, ack, req):
    """Mint a pending REPLY and seed its delivery-state queued slot covering
    ack+1 .. req (explicit range — unlike _seed_queued_reply which covers a
    single triggering seq)."""
    inv = db.mint_thread_invocation(
        thread_id=thread_id, agent_name=agent_name,
        triggering_seq=ack + 1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id, agent_name, acknowledged_through_seq, required_through_seq, "
        "queued_invocation_token, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (thread_id, agent_name, ack, req, inv.invocation_token,
         "2026-01-01T00:00:00+00:00"),
    )
    db._conn.commit()
    return inv


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["first", "eviction_fallback", "nudge"])
async def test_post_launch_exception_counts_once_at_shipping_runner_seam(
    tmp_path, monkeypatch, path,
):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="breaker"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="one",
    )
    if path == "eviction_fallback":
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown="two",
        )
        triggering_seq = 2
        db.update_thread_session(
            "THR-001", "alice", agent_session_id="stale-sid", last_resumed_seq=1,
        )
    else:
        triggering_seq = 1
    inv = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=triggering_seq)
    for index in range(2):
        db.record_thread_reply_breaker_failure(
            thread_id="THR-001", agent_name="alice",
            executor_key="claude:default:3:900",
            invocation_token=f"prior-failure-{index}",
            failure_category="provider_nonzero", threshold=3,
            cooldown_seconds=900,
        )
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod
    calls = 0

    class _Executor:
        def run(self, **kwargs):
            nonlocal calls
            calls += 1
            if path == "eviction_fallback" and calls == 1:
                result = FakeExecutorResult(False, "evicted")
                result.returncode = 1
                result.stderr_tail = "No conversation found with session ID: stale-sid"
                return result
            if path == "nudge" and calls == 1:
                return FakeExecutorResult(True)
            kwargs["on_started"](9000 + calls)
            raise OSError(f"post-launch-{path}")

    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider", lambda *_args: _Executor(),
    )
    await run_invocation(
        org_state=FakeOrgState(db, tmp_path), invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    breaker = db._conn.execute(
        "SELECT state,consecutive_failures FROM thread_reply_breaker_episodes"
    ).fetchone()
    assert tuple(breaker) == ("open", 3)
    assert db._conn.execute(
        "SELECT COUNT(*) FROM thread_reply_breaker_receipts"
    ).fetchone()[0] == 3


@pytest.mark.asyncio
async def test_shipping_runner_restart_open_probe_failure_rearms_once(
    tmp_path, monkeypatch,
):
    """Exercise the persisted OPEN -> daemon recovery -> one PROBE -> OPEN loop.

    Reopening the Database models a daemon restart.  The retained delivery gap
    remains authoritative independently of the breaker row; the timer mints
    exactly one lease, and shipping run_invocation settles its terminal failure
    once and rearms the cooldown.
    """
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="breaker"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="retained gap",
    )
    queued = _seed_queued_reply(db, "THR-001", "alice", triggering_seq=1)
    breaker_key = "claude:default:3:900"
    for index in range(3):
        db.record_thread_reply_breaker_failure(
            thread_id="THR-001", agent_name="alice",
            executor_key=breaker_key, invocation_token=f"prior-{index}",
            failure_category="provider_nonzero", threshold=3,
            cooldown_seconds=900,
            now=datetime(2026, 9, 1, 8, index, tzinfo=timezone.utc),
        )
    opened = db.get_thread_reply_breaker("THR-001", "alice", breaker_key)
    db.close()

    restarted = Database(tmp_path / "happyranch.db")
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    launches = 0

    class _FailingProbe:
        def run(self, **kwargs):
            nonlocal launches
            launches += 1
            kwargs["on_started"](9100)
            result = FakeExecutorResult(False, "probe failed")
            result.returncode = 1
            result.failure_category = "provider_nonzero"
            result.provider_launched = True
            return result

    import runtime.daemon.thread_runner as runner_mod
    monkeypatch.setattr(
        runner_mod, "_build_executor_for_provider", lambda *_args: _FailingProbe(),
    )
    org = FakeOrgState(restarted, tmp_path)

    # A stale queued notification after restart cannot bypass OPEN.
    await run_invocation(
        org_state=org, invocation_token=queued.invocation_token,
        settings=Settings(),
    )
    assert launches == 0
    delivery = restarted.get_reply_delivery_state("THR-001", "alice")
    assert delivery.required_through_seq == 1
    assert delivery.acknowledged_through_seq == 0

    probes = restarted.mint_due_thread_reply_breaker_probes(
        now=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
    )
    assert len(probes) == 1
    repeated = restarted.mint_due_thread_reply_breaker_probes(
        now=datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc),
    )
    assert [entry.invocation_token for entry in repeated] == [
        probes[0].invocation_token
    ]
    await run_invocation(
        org_state=org, invocation_token=probes[0].invocation_token,
        settings=Settings(),
    )
    assert launches == 1
    rearmed = restarted.get_thread_reply_breaker(
        "THR-001", "alice", breaker_key
    )
    assert rearmed.state == "open"
    assert rearmed.probe_lease_id is None
    assert rearmed.cooldown_until > opened.cooldown_until
    assert restarted._conn.execute(
        "SELECT COUNT(*) FROM thread_reply_breaker_receipts "
        "WHERE invocation_token=?", (probes[0].invocation_token,),
    ).fetchone()[0] == 1

class _RecordingExec:
    """Fake executor that records every prompt it is handed."""

    def __init__(self, *, prompts, result=None):
        self._prompts = prompts
        self._result = result or FakeExecutorResult(success=True)

    def run(self, **kwargs):
        self._prompts.append(kwargs.get("prompt", ""))
        return self._result


@pytest.mark.asyncio
async def test_stale_claim_noop_before_provider(tmp_path, monkeypatch):
    """A REPLY whose token owns no delivery-state queued slot (stale/legacy
    notification) no-ops before prompt materialization or any provider call."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    # Legacy direct mint with NO delivery-state row: run_invocation must
    # refuse to claim it and return before any provider work.
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod
    calls: list[str] = []
    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _RecordingExec(prompts=calls),
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    assert calls == []  # no prompt materialized, no provider call
    # The token stays pending (no claim happened, nothing settled).
    assert (
        db.get_invocation_any_status(inv.invocation_token).status
        is ThreadInvocationStatus.PENDING
    )
    assert db.list_reply_delivery_states() == []


@pytest.mark.asyncio
async def test_duplicate_notification_noop_after_claim(tmp_path, monkeypatch):
    """A duplicate queue notification for an already-claimed token no-ops
    before prompt materialization or provider work (claim CAS is durable)."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_wake(db, "THR-001", "alice", ack=0, req=1)
    # Simulate the real claim having already happened (running slot).
    assert db.claim_conversational_reply(inv.invocation_token) is not None
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod
    calls: list[str] = []
    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _RecordingExec(prompts=calls),
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    assert calls == []
    # The original claim is untouched: still the running token.
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None
    assert st.running_invocation_token == inv.invocation_token
    assert st.queued_invocation_token is None


@pytest.mark.asyncio
async def test_claimed_reply_prompt_explicitly_covers_range_in_order(tmp_path, monkeypatch):
    """A claimed conversational REPLY prompt explicitly states its inclusive
    delivery range and renders every required message in order."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    for body in ("m1", "m2", "m3"):
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=body,
        )
    inv = _seed_queued_wake(db, "THR-001", "alice", ack=0, req=3)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod
    prompts: list[str] = []
    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _RecordingExec(prompts=prompts),
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    assert prompts, "executor must have been invoked for a valid claimed wake"
    first = prompts[0]
    assert "## Delivery range" in first
    assert "1 through 3" in first
    # Required messages appear in order.
    idx = [first.index(body) for body in ("m1", "m2", "m3")]
    assert idx == sorted(idx)
    # The clean-exit-without-callback run settled through the store: the claim
    # was taken (range was stated), then the terminal path left retry_required
    # with no acknowledgement advance and no hot-loop retry.
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None
    assert st.acknowledged_through_seq == 0
    assert st.queued_invocation_token is None
    assert st.running_invocation_token is None


@pytest.mark.asyncio
async def test_resume_delta_cannot_omit_required_messages(tmp_path, monkeypatch):
    """Session resume is allowed only as an optimization: when the stored
    watermark is below the claim's running_from_seq the delta includes every
    required message and states the range explicitly."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    for body in ("m1", "m2", "m3", "m4", "m5", "m6"):
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=body,
        )
    # Prior turns acked through 2; the coalesced wake covers 3..6.
    inv = _seed_queued_wake(db, "THR-001", "alice", ack=2, req=6)
    db.update_thread_session(
        "THR-001", "alice",
        agent_session_id="claude-prior", last_resumed_seq=2,
    )
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod
    prompts: list[str] = []
    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _RecordingExec(prompts=prompts),
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    first = prompts[0]
    assert "## Delivery range" in first
    assert "3 through 6" in first
    # Delta covers the whole required range and omits already-seen messages.
    for body in ("m3", "m4", "m5", "m6"):
        assert body in first
    assert "m1" not in first and "m2" not in first


@pytest.mark.asyncio
async def test_resume_forbidden_when_watermark_at_or_above_running_from(tmp_path, monkeypatch):
    """If the stored session watermark is at or above the claim's
    running_from_seq, the runner must NOT trust the delta — it falls back to
    the full prompt so no required message is ever omitted."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    for body in ("m1", "m2", "m3"):
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=body,
        )
    inv = _seed_queued_wake(db, "THR-001", "alice", ack=0, req=3)
    db.update_thread_session(
        "THR-001", "alice",
        agent_session_id="claude-prior", last_resumed_seq=1,
    )  # watermark == running_from (1) → full prompt required
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod
    prompts: list[str] = []
    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _RecordingExec(prompts=prompts),
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    first = prompts[0]
    assert "m1" in first and "m2" in first and "m3" in first  # full transcript
    assert "## Delivery range" in first
    assert "1 through 3" in first


@pytest.mark.asyncio
async def test_runner_failure_settles_retry_required_no_hot_loop(tmp_path, monkeypatch):
    """Provider failure through the runner settles the claimed range via the
    store and the real downstream projection exposes only its bounded category.

    A later arrival and claim clear that category while the raw terminal detail
    remains available only as historical detail. Successful settlement then
    removes the live obligation entirely.
    """
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    for body in ("m1", "m2", "m3"):
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown=body,
        )
    inv = _seed_queued_wake(db, "THR-001", "alice", ack=0, req=3)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    raw = (
        "Running as unit: happyranch-session-TASK-6331-496f9d86.scope; "
        "invocation ID: 62fd4173e2434aabaf1d1caddcda070e; stderr tail: "
        + "provider startup failed before callback; " * 12
    )
    assert len(raw) >= 448

    class _FailExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            r = FakeExecutorResult(
                success=False,
                error=f"Command exited with code 1: {raw}",
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
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None
    assert st.acknowledged_through_seq == 0  # untouched by failure
    assert st.required_through_seq == 3
    assert st.queued_invocation_token is None  # no immediate retry / hot loop
    assert st.running_invocation_token is None
    assert st.last_terminal_reason and "no_callback" in st.last_terminal_reason
    inv_after = db.get_invocation_any_status(inv.invocation_token)
    assert inv_after.status is ThreadInvocationStatus.FAILED
    # Projection reports retry_required (diagnostic, not a live subprocess).
    proj = db.list_reply_delivery_projections("THR-001")
    assert len(proj) == 1 and proj[0].state == "retry_required"
    assert proj[0].started_at is None
    assert proj[0].current_failure_category == "infra_fail"
    assert raw not in (proj[0].current_failure_category or "")
    assert "happyranch-session-TASK-6331-496f9d86.scope" in (
        proj[0].last_terminal_reason or ""
    )
    # No pending REPLY was minted by the failure (no hot loop).
    from runtime.models import ThreadInvocation
    pending = [
        i for i in db.list_thread_invocations("THR-001")
        if i.status is ThreadInvocationStatus.PENDING
    ]
    assert pending == []

    _, arrivals = db.record_conversational_arrival(
        thread_id="THR-001",
        speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="m4",
        recipients=["alice"],
    )
    replacement = next(a.invocation_token for a in arrivals if a.agent_name == "alice")
    assert replacement is not None
    queued = db.list_reply_delivery_projections("THR-001")[0]
    assert queued.state == "queued"
    assert queued.current_failure_category is None
    assert queued.last_terminal_reason is None

    claim = db.claim_conversational_reply(replacement)
    assert claim is not None
    running = db.list_reply_delivery_projections("THR-001")[0]
    assert running.state == "running"
    assert running.current_failure_category is None
    assert running.last_terminal_reason is None

    settled = db.settle_conversational_reply(token=replacement, outcome="reply")
    assert settled is not None
    assert db.list_reply_delivery_projections("THR-001") == []


@pytest.mark.asyncio
async def test_runner_timeout_settles_via_store(tmp_path, monkeypatch):
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_wake(db, "THR-001", "alice", ack=0, req=1)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    class _TimeoutExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            return FakeExecutorResult(success=False, error="timeout")

    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _TimeoutExec(),
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    assert (
        db.get_invocation_any_status(inv.invocation_token).status
        is ThreadInvocationStatus.TIMEOUT
    )
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None and st.acknowledged_through_seq == 0
    proj = db.list_reply_delivery_projections("THR-001")
    assert proj and proj[0].state == "retry_required"


@pytest.mark.asyncio
async def test_runner_materialization_failure_settles_via_store(tmp_path, monkeypatch):
    """A materialization failure terminalizes the claimed REPLY through the
    store (failed, retry_required) BEFORE any provider spawn."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = _seed_queued_wake(db, "THR-001", "alice", ack=0, req=1)
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod
    from runtime.orchestrator.workspace_adapters import (
        SystemContractMaterializationError,
    )

    def _boom(*args, **kwargs):
        raise SystemContractMaterializationError("skills corrupted")

    monkeypatch.setattr(runner_mod, "materialize_workspace_skills", _boom)
    calls: list[str] = []
    monkeypatch.setattr(
        runner_mod,
        "_build_executor_for_provider",
        lambda provider, settings, paths: _RecordingExec(prompts=calls),
    )

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    assert calls == []  # provider never spawned
    inv_after = db.get_invocation_any_status(inv.invocation_token)
    assert inv_after.status is ThreadInvocationStatus.FAILED
    assert "materialization_failed" in (inv_after.decline_reason or "")
    st = db.get_reply_delivery_state("THR-001", "alice")
    assert st is not None and st.acknowledged_through_seq == 0
    assert st.queued_invocation_token is None  # no hot-loop retry


@pytest.mark.asyncio
async def test_task_followup_runner_never_touches_delivery_state(tmp_path, monkeypatch):
    """A TASK_FOLLOWUP invocation runs on the legacy path: no claim, no
    delivery-state settlement, no reply-delivery row created — terminal
    transitions go through fail_invocation."""
    db = Database(tmp_path / "happyranch.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="dispatch",
    )
    inv, _ = db.mint_followup_invocation_with_cap_extend(
        "THR-001", agent_name="alice", triggering_seq=1,
    )
    assert inv.purpose is ThreadInvocationPurpose.TASK_FOLLOWUP
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    import runtime.daemon.thread_runner as runner_mod

    class _FailExec:
        def __init__(self, **kwargs):
            pass

        def run(self, **kwargs):
            r = FakeExecutorResult(success=False, error="Command exited with code 1: boom")
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
    # No reply-delivery-state row was ever created, claimed, or settled.
    assert db.list_reply_delivery_states() == []
    assert db.list_reply_delivery_projections("THR-001") == []
    # The followup terminalized through the legacy fail path.
    assert (
        db.get_invocation_any_status(inv.invocation_token).status
        is ThreadInvocationStatus.FAILED
    )
