from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from runtime.config import Settings
from runtime.daemon.dream_runner import build_dream_prompt, run_dream
from runtime.models import DreamRecord, DreamStatus, TokenUsage
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.executors import ClaudeExecutor
from runtime.orchestrator.org_config import OrgConfig


def _dt(hour: int) -> datetime:
    return datetime(2026, 6, 9, hour, 0, tzinfo=timezone.utc)


def test_build_dream_prompt_contains_private_contract(tmp_path):
    prompt = build_dream_prompt(
        org_slug="test",
        dream=DreamRecord(
            id="DREAM-001",
            agent_name="dev_agent",
            local_date="2026-06-09",
            scheduled_for=_dt(2),
            window_start=_dt(1),
            window_end=_dt(2),
        ),
        workspace=tmp_path,
        recent_audit=[],
        task_history="TASK-001 completed\n",
        org_config=OrgConfig(),
    )

    assert "private reflection" in prompt
    assert "happyranch dreams complete" in prompt
    assert "DREAM-001" in prompt
    assert "TASK-001 completed" in prompt


class FakeResult:
    success = True
    error = None
    returncode = 0
    session_id = "executor-session"
    agent_session_id = "agent-session"
    token_usage = None


class FakeTimeoutResult:
    success = False
    error = "Session timed out after 1800 seconds"
    returncode = None
    session_id = "executor-session"
    agent_session_id = None
    token_usage = None


class FakeFailureResult:
    success = False
    error = "Command exited with code 1: boom"
    returncode = 1
    session_id = "executor-session"
    agent_session_id = None
    token_usage = None


class FakeTokenUsageResult(FakeResult):
    token_usage = TokenUsage(input_tokens=12, output_tokens=3, model="test-model")


class FakeExecutor:
    def __init__(self, result_cls=FakeResult):
        self.calls = []
        self._result_cls = result_cls

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self._result_cls()


def _insert_pending_dream(org_state, **overrides):
    fields = dict(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_start=_dt(1),
        window_end=_dt(2),
    )
    fields.update(overrides)
    org_state.db.insert_dream(DreamRecord(**fields))
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


async def test_run_dream_marks_running_and_waits_for_callback(org_state):
    workspace = _insert_pending_dream(org_state)
    fake = FakeExecutor()

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=lambda *_args, **_kwargs: fake)

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert "no_callback" in dream.error
    assert fake.calls[0]["workspace"] == Path(workspace)


async def test_run_dream_timeout_sets_timeout_status_and_audit(org_state):
    _insert_pending_dream(org_state)
    fake = FakeExecutor(FakeTimeoutResult)

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=lambda *_a, **_k: fake)

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.TIMEOUT
    assert "timed out" in dream.error
    actions = [r["action"] for r in org_state.db.get_audit_logs("DREAM-001")]
    assert "dream_timeout" in actions
    assert "dream_failed" not in actions
    # A timeout must NOT advance the successful-dream window.
    assert org_state.db.get_last_successful_dream("dev_agent") is None


async def test_run_dream_executor_failure_sets_failed_status(org_state):
    _insert_pending_dream(org_state)
    fake = FakeExecutor(FakeFailureResult)

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=lambda *_a, **_k: fake)

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    actions = [r["action"] for r in org_state.db.get_audit_logs("DREAM-001")]
    assert "dream_failed" in actions
    assert "dream_timeout" not in actions
    assert org_state.db.get_last_successful_dream("dev_agent") is None


async def test_run_dream_prompt_includes_agent_window_audit(org_state):
    _insert_pending_dream(org_state)
    # An audit row from the agent's recent (non-dream) work inside the window.
    org_state.db.insert_audit_log(
        task_id="TASK-900", agent="dev_agent", action="session_start",
        payload={"workspace": "ws"},
    )
    fake = FakeExecutor()

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=lambda *_a, **_k: fake)

    prompt = fake.calls[0]["prompt"]
    assert "TASK-900" in prompt
    assert "session_start" in prompt


async def test_run_dream_persists_token_usage_as_dream_scope(org_state):
    """Dream executor sessions that return TokenUsage must write a
    dream-scope session_token_usage row (issue #216)."""
    _insert_pending_dream(org_state)
    usage = TokenUsage(
        input_tokens=5000,
        output_tokens=2000,
        cache_read_tokens=8000,
        cache_creation_tokens=3000,
        reasoning_tokens=None,
        model="claude-sonnet-4-6",
    )
    result = FakeResult()
    result.token_usage = usage
    result.session_id = "dream-sess-001"

    def factory(*_a, **_k):
        ex = FakeExecutor()
        ex._result_cls = lambda: result
        return ex

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=factory)

    rows = org_state.db.list_session_token_usage(scope_type="dream", scope_id="DREAM-001")
    assert len(rows) == 1, f"Expected 1 dream-scope row, got {len(rows)}"
    row = rows[0]
    assert row["agent"] == "dev_agent"
    assert row["scope_type"] == "dream"
    assert row["scope_id"] == "DREAM-001"
    assert row["task_id"] is None
    assert row["executor"] == "claude"  # default for dev_agent workspace
    assert row["input_tokens"] == 5000
    assert row["output_tokens"] == 2000
    assert row["cache_read_tokens"] == 8000
    assert row["cache_creation_tokens"] == 3000
    assert row["total_tokens"] == 7000  # input + output + reasoning (cache excluded)


async def test_run_dream_passes_org_paths_to_executor_factory(org_state):
    _insert_pending_dream(org_state)
    captured = {}
    fake = FakeExecutor()

    def factory(executor_name, settings, paths):
        captured["paths"] = paths
        return fake

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=factory)

    assert isinstance(captured["paths"], OrgPaths)
    assert captured["paths"].root == org_state.root


# ── THR-116: terminal_error on executor failures ───────────────────────

class FakeSessionLimitResult:
    """Claude exited non-zero with a structured result envelope indicating
    session-limit, plus unrelated stderr noise (workspace-trust warning)."""
    success = False
    error = "Command exited with code 1: Workspace trust warning: ..."
    returncode = 1
    session_id = "executor-session"
    agent_session_id = None
    token_usage = None
    terminal_error = "session_limit"


class FakeCertificateErrorResult:
    """Claude exited non-zero with a structured result envelope indicating
    UNKNOWN_CERTIFICATE_VERIFICATION_ERROR, plus unrelated stderr noise."""
    success = False
    error = "Command exited with code 1: Workspace trust warning: ..."
    returncode = 1
    session_id = "executor-session"
    agent_session_id = None
    token_usage = None
    terminal_error = "transport_error: UNKNOWN_CERTIFICATE_VERIFICATION_ERROR"


class FakeNoStructuredResult:
    """Executor exited non-zero with no structured terminal result —
    terminal_error is None, so the fallback error should be used."""
    success = False
    error = "Command exited with code 2: fatal: something broke"
    returncode = 2
    session_id = "executor-session"
    agent_session_id = None
    token_usage = None
    terminal_error = None


class FakeStructuredResultExecutor:
    def __init__(self, result):
        self._result = result

    def run(self, **kwargs):
        return self._result


async def test_run_dream_session_limit_classified_terminal_error(org_state):
    """(a) Structured terminal session-limit result + unrelated stderr:
    persisted dream.error and dream_failed audit reason must be the
    classified 'session_limit', not the raw stderr-based error."""
    _insert_pending_dream(org_state)
    fake = FakeStructuredResultExecutor(FakeSessionLimitResult())

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=lambda *_a, **_k: fake)

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert dream.error == "session_limit"
    actions = [r for r in org_state.db.get_audit_logs("DREAM-001")]
    assert actions[-1]["action"] == "dream_failed"
    assert actions[-1]["payload"]["reason"] == "session_limit"
    # Must NOT contain the stderr noise.
    assert "Workspace trust warning" not in dream.error


async def test_run_dream_certificate_error_classified_terminal_error(org_state):
    """(b) Structured UNKNOWN_CERTIFICATE_VERIFICATION_ERROR result +
    unrelated stderr: persisted error and audit reason must be the
    classified transport_error reason."""
    _insert_pending_dream(org_state)
    fake = FakeStructuredResultExecutor(FakeCertificateErrorResult())

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=lambda *_a, **_k: fake)

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert dream.error == "transport_error: UNKNOWN_CERTIFICATE_VERIFICATION_ERROR"
    actions = [r for r in org_state.db.get_audit_logs("DREAM-001")]
    assert actions[-1]["action"] == "dream_failed"
    assert actions[-1]["payload"]["reason"] == "transport_error: UNKNOWN_CERTIFICATE_VERIFICATION_ERROR"
    # Must NOT contain the stderr noise.
    assert "Workspace trust warning" not in dream.error


async def test_run_dream_timeout_unchanged_by_terminal_error(org_state):
    """(c) Existing timeout path remains distinct — timeout status and
    dream_timeout audit action are unchanged."""
    _insert_pending_dream(org_state)
    fake = FakeExecutor(FakeTimeoutResult)

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=lambda *_a, **_k: fake)

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.TIMEOUT
    assert "timed out" in dream.error
    actions = [r["action"] for r in org_state.db.get_audit_logs("DREAM-001")]
    assert "dream_timeout" in actions
    assert "dream_failed" not in actions


async def test_run_dream_no_callback_unchanged_by_terminal_error(org_state):
    """(c) Existing successful/no-callback path remains distinct —
    FAILED status with 'no_callback' error."""
    _insert_pending_dream(org_state)
    fake = FakeExecutor(FakeResult)

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=lambda *_a, **_k: fake)

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert "no_callback" in dream.error
    audit_rows = list(org_state.db.get_audit_logs("DREAM-001"))
    actions = [r["action"] for r in audit_rows]
    assert "dream_failed" in actions


async def test_run_dream_no_structured_result_falls_back_to_error(org_state):
    """(d) When terminal_error is None (no structured result), dream.error
    and audit reason fall back to the pre-existing raw error string."""
    _insert_pending_dream(org_state)
    fake = FakeStructuredResultExecutor(FakeNoStructuredResult())

    await run_dream(org_state=org_state, dream_id="DREAM-001", executor_factory=lambda *_a, **_k: fake)

    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert dream.error == "Command exited with code 2: fatal: something broke"
    actions = [r for r in org_state.db.get_audit_logs("DREAM-001")]
    assert actions[-1]["action"] == "dream_failed"
    assert actions[-1]["payload"]["reason"] == "Command exited with code 2: fatal: something broke"


# ── THR-116 repair: real shipping-chain dream-runner tests ─────────────
# These go through the real chain:
#   mocked subprocess → ClaudeExecutor → production _run_command +
#   _parse_claude_terminal_error → run_dream → dream.error + audit
# The executors fixture provides a real ClaudeExecutor, not a lookalike
# FakeStructuredResultExecutor with prepopulated terminal_error.


def _popen_mock(returncode=0, stdout="", stderr="", pid=4242):
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, stderr)
    return proc


@patch("runtime.orchestrator.executors._resolve_binary", return_value="/usr/bin/env")
@patch("runtime.orchestrator.executors.subprocess")
async def test_run_dream_real_chain_session_limit(
    mock_subprocess, _mock_resolve_binary, org_state,
):
    """Real-chain (session-limit): mocked subprocess produces
    {type:result, subtype:error_during_execution} + workspace-trust stderr →
    ClaudeExecutor.run() with production parser → run_dream →
    dream.error == dream_failed.payload.reason == 'session_limit'."""
    _insert_pending_dream(org_state)

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout='{"type":"result","subtype":"error_during_execution","is_error":true,'
               '"result":"Session limit reached"}',
        stderr="Workspace trust warning: untrusted directory\n",
    )

    def _factory(_name, _settings, _paths):
        return ClaudeExecutor(
            claude_cli_path="claude", permission_mode="auto",
            settings=Settings(), paths=_paths,
        )

    await run_dream(
        org_state=org_state, dream_id="DREAM-001",
        executor_factory=_factory,
    )

    mock_subprocess.Popen.assert_called_once()
    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert dream.error == "session_limit"
    actions = [r for r in org_state.db.get_audit_logs("DREAM-001")]
    assert actions[-1]["action"] == "dream_failed"
    assert actions[-1]["payload"]["reason"] == "session_limit"
    # Must NOT contain the stderr noise.
    assert "Workspace trust warning" not in dream.error


@patch("runtime.orchestrator.executors._resolve_binary", return_value="/usr/bin/env")
@patch("runtime.orchestrator.executors.subprocess")
async def test_run_dream_real_chain_certificate_error(
    mock_subprocess, _mock_resolve_binary, org_state,
):
    """Real-chain (certificate): mocked subprocess produces
    {type:result, subtype:error_during_execution, result: certificate...}
    + workspace-trust stderr → ClaudeExecutor.run() with production parser
    → run_dream → dream.error == dream_failed.payload.reason ==
    'transport_error: UNKNOWN_CERTIFICATE_VERIFICATION_ERROR'."""
    _insert_pending_dream(org_state)

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout='{"type":"result","subtype":"error_during_execution","is_error":true,'
               '"result":"UNKNOWN_CERTIFICATE_VERIFICATION_ERROR: unable to verify"}',
        stderr="Workspace trust warning: untrusted directory\n",
    )

    def _factory(_name, _settings, _paths):
        return ClaudeExecutor(
            claude_cli_path="claude", permission_mode="auto",
            settings=Settings(), paths=_paths,
        )

    await run_dream(
        org_state=org_state, dream_id="DREAM-001",
        executor_factory=_factory,
    )

    mock_subprocess.Popen.assert_called_once()
    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert dream.error == "transport_error: UNKNOWN_CERTIFICATE_VERIFICATION_ERROR"
    actions = [r for r in org_state.db.get_audit_logs("DREAM-001")]
    assert actions[-1]["action"] == "dream_failed"
    assert actions[-1]["payload"]["reason"] == "transport_error: UNKNOWN_CERTIFICATE_VERIFICATION_ERROR"
    assert "Workspace trust warning" not in dream.error


@patch("runtime.orchestrator.executors._resolve_binary", return_value="/usr/bin/env")
@patch("runtime.orchestrator.executors.subprocess")
async def test_run_dream_real_chain_no_structured_result_fallback(
    mock_subprocess, _mock_resolve_binary, org_state,
):
    """Real-chain (fallback): mocked subprocess produces non-JSON stdout
    → ClaudeExecutor with production parser returns None → raw error wins."""
    _insert_pending_dream(org_state)

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=2,
        stdout="not json",
        stderr="fatal: something broke\n",
    )

    def _factory(_name, _settings, _paths):
        return ClaudeExecutor(
            claude_cli_path="claude", permission_mode="auto",
            settings=Settings(), paths=_paths,
        )

    await run_dream(
        org_state=org_state, dream_id="DREAM-001",
        executor_factory=_factory,
    )

    mock_subprocess.Popen.assert_called_once()
    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert "Command exited with code 2" in dream.error
    assert "fatal: something broke" in dream.error
    actions = [r for r in org_state.db.get_audit_logs("DREAM-001")]
    assert actions[-1]["action"] == "dream_failed"
    assert "Command exited with code 2" in actions[-1]["payload"]["reason"]


@patch("runtime.orchestrator.executors._resolve_binary", return_value="/usr/bin/env")
@patch("runtime.orchestrator.executors.subprocess")
async def test_run_dream_real_chain_subtype_success_no_terminal_error(
    mock_subprocess, _mock_resolve_binary, org_state,
):
    """Real-chain (false-precedence): mocked subprocess produces
    {type:result, subtype:success, result: certificate...} —
    must NOT produce a terminal_error; raw error wins."""
    _insert_pending_dream(org_state)

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout='{"type":"result","subtype":"success",'
               '"result":"certificate verification failed"}',
        stderr="Workspace trust warning\n",
    )

    def _factory(_name, _settings, _paths):
        return ClaudeExecutor(
            claude_cli_path="claude", permission_mode="auto",
            settings=Settings(), paths=_paths,
        )

    await run_dream(
        org_state=org_state, dream_id="DREAM-001",
        executor_factory=_factory,
    )

    mock_subprocess.Popen.assert_called_once()
    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    # Raw error must contain the stderr noise — no classified reason available.
    assert "Workspace trust warning" in dream.error
    assert "certificate" not in dream.error.lower() or "session" not in dream.error.lower()
    actions = [r for r in org_state.db.get_audit_logs("DREAM-001")]
    assert actions[-1]["action"] == "dream_failed"


@patch("runtime.orchestrator.executors._resolve_binary", return_value="/usr/bin/env")
@patch("runtime.orchestrator.executors.subprocess")
async def test_run_dream_real_chain_error_lookalike_falls_back_to_raw(
    mock_subprocess, _mock_resolve_binary, org_state,
):
    """Real-chain (error_lookalike): mocked subprocess produces
    {type:result, subtype:error_lookalike, is_error:true, result: irrelevant}
    + unrelated workspace-trust stderr → ClaudeExecutor → run_dream →
    dream.error == dream_failed.payload.reason == raw stderr-first fallback
    (NOT a synthetic terminal reason)."""
    _insert_pending_dream(org_state)

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout='{"type":"result","subtype":"error_lookalike","is_error":true,'
               '"result":"some irrelevant message"}',
        stderr="Workspace trust warning: untrusted directory\n",
    )

    def _factory(_name, _settings, _paths):
        return ClaudeExecutor(
            claude_cli_path="claude", permission_mode="auto",
            settings=Settings(), paths=_paths,
        )

    await run_dream(
        org_state=org_state, dream_id="DREAM-001",
        executor_factory=_factory,
    )

    mock_subprocess.Popen.assert_called_once()
    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    # error_lookalike is unsupported → raw stderr-first fallback, no
    # synthetic terminal reason.
    assert "Command exited with code 1" in dream.error
    assert "Workspace trust warning" in dream.error
    assert dream.error != "claude_lookalike"
    assert "session_limit" not in dream.error
    actions = [r for r in org_state.db.get_audit_logs("DREAM-001")]
    assert actions[-1]["action"] == "dream_failed"
    assert actions[-1]["payload"]["reason"] == dream.error


@patch("runtime.orchestrator.executors._resolve_binary", return_value="/usr/bin/env")
@patch("runtime.orchestrator.executors.subprocess")
async def test_run_dream_real_chain_error_unknown_falls_back_to_raw(
    mock_subprocess, _mock_resolve_binary, org_state,
):
    """Real-chain (error_unknown): mocked subprocess produces
    {type:result, subtype:error_unknown, is_error:true, result: ...}
    + unrelated workspace-trust stderr → ClaudeExecutor → run_dream →
    dream.error == dream_failed.payload.reason == raw stderr-first fallback
    (NOT a synthetic terminal reason)."""
    _insert_pending_dream(org_state)

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=2,
        stdout='{"type":"result","subtype":"error_unknown","is_error":true,'
               '"result":"something went wrong"}',
        stderr="fatal: something broke\n",
    )

    def _factory(_name, _settings, _paths):
        return ClaudeExecutor(
            claude_cli_path="claude", permission_mode="auto",
            settings=Settings(), paths=_paths,
        )

    await run_dream(
        org_state=org_state, dream_id="DREAM-001",
        executor_factory=_factory,
    )

    mock_subprocess.Popen.assert_called_once()
    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    # error_unknown is unsupported → raw stderr-first fallback, no
    # synthetic terminal reason.
    assert "Command exited with code 2" in dream.error
    assert "fatal: something broke" in dream.error
    assert dream.error != "claude_unknown"
    assert "session_limit" not in dream.error
    actions = [r for r in org_state.db.get_audit_logs("DREAM-001")]
    assert actions[-1]["action"] == "dream_failed"
    assert actions[-1]["payload"]["reason"] == dream.error


# ── THR-116 adversarial real-chain dream tests (formerly faulty) ──────
# These tests prove that unsupported error_* subtypes with recognised signal
# text do NOT produce a structured terminal reason through the full
# ClaudeExecutor → _run_command → production parser → run_dream chain.
# Both dream.error and dream_failed.payload.reason must contain the raw
# stderr-first fallback, not a classified reason.


@patch("runtime.orchestrator.executors._resolve_binary", return_value="/usr/bin/env")
@patch("runtime.orchestrator.executors.subprocess")
async def test_run_dream_real_chain_error_lookalike_session_limit_raw_fallback(
    mock_subprocess, _mock_resolve_binary, org_state,
):
    """Real-chain adversarial (error_lookalike + session-limit text):
    {type:result, subtype:error_lookalike, is_error:true,
    result: "Session limit reached"} + unrelated workspace-trust stderr →
    dream.error == dream_failed.payload.reason == raw stderr-first fallback.

    This enters the formerly faulty classification condition: under the
    earlier startswith("error_") check, the parser would have classified
    this as session_limit.  With the strict subtype == error_during_execution
    check, the parser returns None and the raw fallback wins.
    (MEM-380 coverage)."""
    _insert_pending_dream(org_state)

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout='{"type":"result","subtype":"error_lookalike","is_error":true,'
               '"result":"Session limit reached"}',
        stderr="Workspace trust warning: untrusted directory\n",
    )

    def _factory(_name, _settings, _paths):
        return ClaudeExecutor(
            claude_cli_path="claude", permission_mode="auto",
            settings=Settings(), paths=_paths,
        )

    await run_dream(
        org_state=org_state, dream_id="DREAM-001",
        executor_factory=_factory,
    )

    mock_subprocess.Popen.assert_called_once()
    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    # error_lookalike is unsupported → raw stderr-first fallback.
    assert "Command exited with code 1" in dream.error
    assert "Workspace trust warning" in dream.error
    assert "session_limit" not in dream.error, (
        f"error_lookalike must not produce session_limit in dream.error; got {dream.error!r}"
    )
    actions = [r for r in org_state.db.get_audit_logs("DREAM-001")]
    assert actions[-1]["action"] == "dream_failed"
    assert actions[-1]["payload"]["reason"] == dream.error
    assert "session_limit" not in actions[-1]["payload"]["reason"], (
        f"dream_failed reason must not be session_limit; got {actions[-1]['payload']['reason']!r}"
    )


@patch("runtime.orchestrator.executors._resolve_binary", return_value="/usr/bin/env")
@patch("runtime.orchestrator.executors.subprocess")
async def test_run_dream_real_chain_error_unknown_certificate_raw_fallback(
    mock_subprocess, _mock_resolve_binary, org_state,
):
    """Real-chain adversarial (error_unknown + certificate text):
    {type:result, subtype:error_unknown, is_error:true,
    result: "UNKNOWN_CERTIFICATE_VERIFICATION_ERROR: unable to verify"}
    + unrelated workspace-trust stderr →
    dream.error == dream_failed.payload.reason == raw stderr-first fallback.

    This enters the formerly faulty classification condition: under the
    earlier startswith("error_") check, the parser would have classified
    this as transport_error.  With the strict subtype == error_during_execution
    check, the parser returns None and the raw fallback wins.
    (MEM-377 coverage)."""
    _insert_pending_dream(org_state)

    mock_subprocess.Popen.return_value = _popen_mock(
        returncode=1,
        stdout='{"type":"result","subtype":"error_unknown","is_error":true,'
               '"result":"UNKNOWN_CERTIFICATE_VERIFICATION_ERROR: unable to verify"}',
        stderr="Workspace trust warning: untrusted directory\n",
    )

    def _factory(_name, _settings, _paths):
        return ClaudeExecutor(
            claude_cli_path="claude", permission_mode="auto",
            settings=Settings(), paths=_paths,
        )

    await run_dream(
        org_state=org_state, dream_id="DREAM-001",
        executor_factory=_factory,
    )

    mock_subprocess.Popen.assert_called_once()
    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    # error_unknown is unsupported → raw stderr-first fallback.
    assert "Command exited with code 1" in dream.error
    assert "Workspace trust warning" in dream.error
    assert "transport_error" not in dream.error, (
        f"error_unknown must not produce transport_error in dream.error; got {dream.error!r}"
    )
    actions = [r for r in org_state.db.get_audit_logs("DREAM-001")]
    assert actions[-1]["action"] == "dream_failed"
    assert actions[-1]["payload"]["reason"] == dream.error
    assert "transport_error" not in actions[-1]["payload"]["reason"], (
        f"dream_failed reason must not contain transport_error; got {actions[-1]['payload']['reason']!r}"
    )


# ── Issue #568: AgentDef.model forwarding to executor.run ──────────────

async def test_run_dream_forwards_model_to_executor_run(org_state):
    """When AgentDef.model is set, dream runner passes it to executor.run(model=...)."""
    workspace = _insert_pending_dream(org_state)
    # Write AgentDef with model into org/agents/
    agents_dir = org_state.root / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "dev_agent.md").write_text(
        "---\nname: dev_agent\nteam: engineering\nrole: worker\n"
        "executor: claude\nmodel: gpt-5.6-terra\n---\n\n"
        "You are a test agent.\n"
    )
    captured_model = {}

    class _CapturingFakeExec:
        def run(self, **kwargs):
            captured_model["model"] = kwargs.get("model")
            return FakeResult()

    def factory(*_a, **_k):
        return _CapturingFakeExec()

    await run_dream(
        org_state=org_state, dream_id="DREAM-001",
        executor_factory=factory,
    )

    assert captured_model.get("model") == "gpt-5.6-terra", (
        f"expected model='gpt-5.6-terra', got {captured_model.get('model')!r}"
    )


async def test_run_dream_no_model_preserves_default_behavior(org_state):
    """When AgentDef.model is absent, dream runner passes model=None."""
    workspace = _insert_pending_dream(org_state)
    agents_dir = org_state.root / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / "dev_agent.md").write_text(
        "---\nname: dev_agent\nteam: engineering\nrole: worker\n"
        "executor: claude\n---\n\n"
        "You are a test agent.\n"
    )
    captured_model = {}

    class _CapturingFakeExec:
        def run(self, **kwargs):
            captured_model["model"] = kwargs.get("model")
            return FakeResult()

    def factory(*_a, **_k):
        return _CapturingFakeExec()

    await run_dream(
        org_state=org_state, dream_id="DREAM-001",
        executor_factory=factory,
    )

    assert captured_model.get("model") is None, (
        f"model should be None when AgentDef has no model, "
        f"got {captured_model.get('model')!r}"
    )
