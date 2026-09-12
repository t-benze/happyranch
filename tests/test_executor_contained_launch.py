"""THR-207 contained-launch parity for BOTH executor Popen launch bodies.

Covers ``_run_command`` (the shared body behind Claude/Codex/Opencode/Pi)
and ``CustomAdapterExecutor.run`` (the second direct
Popen body):

* contained mode uses the backend-created ``RunningHandle.process`` — no
  self-Popen, no ``on_started`` (the supervisor binds the diagnostic PID),
  no pre-launch validator re-run;
* the throttle enters with NO internal 429 retry so the rate-limited result
  flows to the supervisor (finish/release/sleep/reacquire with a fresh
  backend handle);
* an honest passthrough handle (``process=None``) fails closed with an
  actionable error — never a fabricated PID;
* ``build_launch_spec`` produces the argv/stdio/env the backend launches.

These are pure/unit tests (real short-lived subprocesses, no daemon).
"""
from __future__ import annotations

import subprocess
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from runtime.orchestrator.executors import (
    ClaudeExecutor,
    CustomAdapterExecutor,
    ExecutorResult,
    _run_command,
    build_command_launch_spec,
)
from runtime.orchestrator.throttle import ProviderThrottle, get_throttle, set_throttle
from runtime.platform.session_backend import RunningHandle

_SID = "sess-contained-1"


def _running_with_process(argv=("sh", "-c", "cat")) -> RunningHandle:
    """A backend-style RunningHandle wrapping a REAL short-lived subprocess."""
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    return RunningHandle(
        backend="fake", token="tok-1", request_id="inv-1",
        root_pid=proc.pid, start_identity="start-1", process=proc,
    )


class _RecordingThrottle(ProviderThrottle):
    """Throttle spy capturing the backoff override the executor passes."""

    def __init__(self) -> None:
        super().__init__(ceiling_default=64, spacing_seconds=0.0, backoff_seconds=(5, 15, 45))
        self.calls: list[tuple[object, object, object, object]] = []

    def run(self, provider, launch, on_event=None, **kwargs):
        self.calls.append((provider, launch, on_event, kwargs))
        return launch()


def test_run_command_contained_uses_backend_process_and_parses():
    """The body communicates with the backend-created process and parses the
    executor envelope — no self-launch, no on_started, validators untouched."""
    from runtime.orchestrator.executors import _parse_claude_usage

    running = _running_with_process()
    on_started_calls: list[int] = []
    validator_calls: list[str] = []

    def validator():
        validator_calls.append("called")

    result = _run_command(
        ["ignored", "argv"],
        workspace=__import__("pathlib").Path("."),
        session_id=_SID,
        timeout_seconds=10,
        input_text="hello stdin",
        on_started=on_started_calls.append,
        usage_parser=_parse_claude_usage,
        provider="claude",
        pre_launch_validator=validator,
        org_slug="test",
        running=running,
    )
    # cat echoed our stdin back; the claude usage parser returns None (no
    # envelope) and the session succeeds with rc=0.
    assert result.success is True
    assert result.returncode == 0
    assert result.session_id == _SID
    # Contained mode: no on_started (supervisor owns the PID), no validator.
    assert on_started_calls == []
    assert validator_calls == []
    running.process.wait()


def test_run_command_contained_enters_throttle_without_internal_retry():
    """Contained mode passes backoff_seconds=() to the throttle so a 429
    surfaces to the supervisor instead of being retried internally."""
    from runtime.orchestrator import executors as exec_mod

    recording = _RecordingThrottle()
    old = get_throttle()
    set_throttle(recording)
    try:
        running = _running_with_process()
        result = _run_command(
            ["ignored"],
            workspace=__import__("pathlib").Path("."),
            session_id=_SID,
            timeout_seconds=10,
            input_text="x",
            running=running,
        )
    finally:
        set_throttle(old)
    assert result.success is True
    assert len(recording.calls) == 1
    _, _, _, kwargs = recording.calls[0]
    assert kwargs.get("backoff_seconds") == ()
    running.process.wait()


def test_run_command_uncontained_honors_throttle_backoff_override():
    """The uncontained path honors ``throttle_backoff_seconds`` so the
    honest-passthrough fallback ALSO defers 429 to the supervisor."""
    from runtime.orchestrator import executors as exec_mod

    recording = _RecordingThrottle()
    old = get_throttle()
    set_throttle(recording)
    try:
        result = _run_command(
            ["sh", "-c", "echo hi"],
            workspace=__import__("pathlib").Path("."),
            session_id=_SID,
            timeout_seconds=10,
            throttle_backoff_seconds=(),
        )
    finally:
        set_throttle(old)
    assert result.success is True
    _, _, _, kwargs = recording.calls[0]
    assert kwargs.get("backoff_seconds") == ()


@pytest.mark.parametrize("backoff, expected_launches, expected_sleeps", [
    ((), 1, []),
    (None, 2, [9.0]),
])
def test_run_command_uncontained_429_override_controls_real_popen_boundary(
    tmp_path, backoff, expected_launches, expected_sleeps,
):
    """The recovery override reaches the real executor launch loop, not a spy.

    Fake only the OS process and clock: ``_run_command`` still classifies the
    429 and asks the configured throttle whether a second Popen is allowed.
    """
    from runtime.orchestrator import executors as exec_mod

    launches: list[object] = []
    sleeps: list[float] = []
    throttle = ProviderThrottle(
        ceiling_default=1, spacing_seconds=0.0, backoff_seconds=(9.0,),
        sleep=sleeps.append,
    )

    def launch_executor(*_args, **_kwargs):
        launches.append(object())
        return SimpleNamespace(
            pid=100 + len(launches), returncode=1,
            communicate=lambda **_kw: ("", "HTTP 429 rate limit"),
        )

    old = get_throttle()
    set_throttle(throttle)
    try:
        with patch.object(exec_mod, "detect_platform_isolation", return_value=SimpleNamespace(
            launch_executor=launch_executor,
        )):
            result = _run_command(
                ["fake-codex"], workspace=tmp_path, session_id=_SID,
                timeout_seconds=10, input_text="prompt", provider="codex",
                throttle_backoff_seconds=backoff,
            )
    finally:
        set_throttle(old)

    assert result.success is False
    assert result.rate_limited is True
    assert len(launches) == expected_launches
    assert sleeps == expected_sleeps


def test_run_command_recovery_deadline_refuses_after_setup_before_launch(tmp_path):
    """The absolute recovery budget is checked at the real Popen boundary."""
    from runtime.orchestrator import executors as exec_mod

    clock = [0.0]
    launches: list[object] = []

    def validator():
        clock[0] = 1.0

    # Replace this module's clock object, rather than its shared stdlib time
    # module: supervisor/event infrastructure elsewhere must retain real time.
    with patch.object(exec_mod, "time", SimpleNamespace(monotonic=lambda: clock[0])), patch.object(
        exec_mod, "detect_platform_isolation",
        return_value=SimpleNamespace(launch_executor=lambda *_a, **_kw: launches.append(object())),
    ):
        result = _run_command(
            ["fake-codex"], workspace=tmp_path, session_id=_SID,
            timeout_seconds=30, input_text="prompt", provider="codex",
            pre_launch_validator=validator, recovery_deadline_monotonic=1.0,
        )

    assert result.success is False
    assert result.failure_category == "pre_launch"
    assert launches == []


def test_run_command_recovery_communicate_uses_fractional_live_remainder(tmp_path):
    """No integer round-up extends a recovery provider opportunity."""
    from runtime.orchestrator import executors as exec_mod

    clock = [10.0]
    observed_timeouts: list[float] = []

    class Process:
        pid = 701
        returncode = 0

        def communicate(self, **kwargs):
            observed_timeouts.append(kwargs["timeout"])
            return "", ""

    with patch.object(exec_mod, "time", SimpleNamespace(monotonic=lambda: clock[0])), patch.object(
        exec_mod, "detect_platform_isolation",
        return_value=SimpleNamespace(launch_executor=lambda *_a, **_kw: Process()),
    ):
        result = _run_command(
            ["fake-codex"], workspace=tmp_path, session_id=_SID,
            timeout_seconds=30, input_text="prompt", provider="codex",
            recovery_deadline_monotonic=10.75,
        )

    assert result.success is True
    assert observed_timeouts == [pytest.approx(0.75)]


def test_run_command_recovery_deadline_after_launch_kills_owned_process(tmp_path):
    """Expiry during launch retains launch provenance and reaps the process."""
    from runtime.orchestrator import executors as exec_mod

    clock = [0.0]
    events: list[object] = []

    class Process:
        pid = 702
        returncode = None

        def kill(self):
            events.append("kill")

        def communicate(self, **kwargs):
            events.append(("communicate", kwargs["timeout"]))
            return "", ""

    def launch(*_args, **_kwargs):
        clock[0] = 1.0
        events.append("launch")
        return Process()

    with patch.object(exec_mod, "time", SimpleNamespace(monotonic=lambda: clock[0])), patch.object(
        exec_mod, "detect_platform_isolation",
        return_value=SimpleNamespace(launch_executor=launch),
    ):
        result = _run_command(
            ["fake-codex"], workspace=tmp_path, session_id=_SID,
            timeout_seconds=30, input_text="prompt", provider="codex",
            recovery_deadline_monotonic=1.0,
        )

    assert result.success is False
    assert result.provider_launched is True
    assert result.failure_category == "provider_timeout"
    assert events == ["launch", "kill", ("communicate", 5)]


def test_run_command_contained_recovery_expiry_reaps_backend_process(tmp_path):
    """A backend launch that used the budget is still finished by its owner."""
    from runtime.orchestrator import executors as exec_mod

    events: list[object] = []

    class Process:
        pid = 703
        returncode = None

        def kill(self):
            events.append("kill")

        def communicate(self, **kwargs):
            events.append(("communicate", kwargs["timeout"]))
            return "", ""

    running = RunningHandle(
        backend="fake", token="tok", request_id="inv", root_pid=703,
        start_identity="start", process=Process(),
    )
    with patch.object(exec_mod, "time", SimpleNamespace(monotonic=lambda: 2.0)):
        result = _run_command(
            ["ignored"], workspace=tmp_path, session_id=_SID,
            timeout_seconds=30, input_text="prompt", running=running,
            recovery_deadline_monotonic=1.0,
        )

    assert result.success is False
    assert result.provider_launched is True
    assert events == ["kill", ("communicate", 5)]


def test_run_command_contained_passthrough_handle_fails_closed():
    """A RunningHandle with no process (honest passthrough) fails closed with
    an actionable error — never a fabricated PID or silent success."""
    running = RunningHandle(
        backend="passthrough", token="tok", request_id="inv",
        root_pid=0, start_identity="", process=None,
    )
    result = _run_command(
        ["ignored"],
        workspace=__import__("pathlib").Path("."),
        session_id=_SID,
        timeout_seconds=10,
        running=running,
    )
    assert result.success is False
    assert "backend-created process" in (result.error or "")


def test_run_command_contained_timeout_kills_and_marks_timeout():
    """A contained session that exceeds the timeout kills the main process,
    drains pipes, and returns the timeout failure the supervisor maps to
    TIMEOUT (whole-scope teardown happens in backend.finish)."""
    running = _running_with_process(argv=("sh", "-c", "sleep 30"))
    started = time.monotonic()
    result = _run_command(
        ["ignored"],
        workspace=__import__("pathlib").Path("."),
        session_id=_SID,
        timeout_seconds=1,
        running=running,
    )
    assert result.success is False
    assert "timed out" in (result.error or "").lower()
    assert result.returncode is None  # killed before an exit code was observed
    assert time.monotonic() - started < 10
    running.process.wait()


def test_build_launch_spec_matches_uncontained_launch_environment(tmp_path):
    """build_command_launch_spec mirrors the uncontained _launch environment:
    argv/cwd/env/stdio/text; stdin is PIPE when the prompt travels via stdin
    and DEVNULL when it travels via argv."""
    from runtime.platform.session_backend import LaunchSpec

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    spec = build_command_launch_spec(
        cmd=["echo", "hello"], workspace=workspace, input_text="prompt",
        org_slug="test",
    )
    assert spec.argv == ("echo", "hello")
    assert spec.cwd == str(workspace)
    assert spec.stdin == subprocess.PIPE
    assert spec.stdout == subprocess.PIPE
    assert spec.text is True
    assert spec.env.get("HAPPYRANCH_ORG_SLUG") == "test"

    spec_no_stdin = build_command_launch_spec(
        cmd=["echo", "hi"], workspace=workspace, input_text=None,
    )
    assert spec_no_stdin.stdin == subprocess.DEVNULL


def test_build_launch_spec_argv_too_large_raises_actionable():
    """The argv-too-large gate fires during spec assembly (before the backend
    launches) with the same actionable category as the uncontained path."""
    from pathlib import Path

    from runtime.orchestrator.executors import PromptTransportTooLargeError

    huge = "x" * 200_000
    with pytest.raises(PromptTransportTooLargeError) as exc:
        build_command_launch_spec(
            cmd=["tool", huge], workspace=Path("/ws"), input_text=None,
        )
    assert "prompt_transport_too_large" in str(exc.value)


def _make_adapter_executor(tmp_path):
    """A minimal CustomAdapterExecutor whose executable is a real shell script
    that echoes a valid AdapterOutput JSON for any input."""
    from runtime.orchestrator.adapter_store import compute_sha256

    exe = tmp_path / "adapter.sh"
    exe.write_text(
        "#!/bin/sh\n"
        'cat >/dev/null\n'
        'echo \'{"contract_version":1,"session_id":"sess-adapter-1",'
        '"success":true,"returncode":0,"duration_seconds":1,'
        '"stdout_tail":"ok","stderr_tail":"",'
        '"adapter_metadata":'
        '{"contract_version":1,"adapter":"adapter-1","adapter_version":"v1"},'
        '"token_usage":null}\'\n'
    )
    exe.chmod(0o755)
    adapter = CustomAdapterExecutor(
        profile_name="adapter-1",
        adapter_entry_id="adapter-1",
        adapter_executable=str(exe),
        adapter_hash=compute_sha256(str(exe)),
        adapter_version="v1",
        adapter_contract_version=1,
        provider="adapter",
    )
    adapter.set_invocation_context(
        agent="dev_agent", org="test", invocation_kind="task", task_id="T-1",
    )
    return adapter


def _adapter_running_handle(exe: str) -> RunningHandle:
    """A backend-style RunningHandle whose process IS the adapter executable
    (as a real backend would launch it)."""
    proc = subprocess.Popen(
        [exe], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    return RunningHandle(
        backend="fake", token="tok-a", request_id="inv-a",
        root_pid=proc.pid, start_identity="start-a", process=proc,
    )


def test_custom_adapter_contained_uses_backend_process(tmp_path):
    """CustomAdapterExecutor contained mode communicates with the backend
    process and parses the AdapterOutput — no raw Popen, no on_started."""
    adapter = _make_adapter_executor(tmp_path)
    running = _adapter_running_handle(adapter._adapter_executable)
    on_started_calls: list[int] = []
    result = adapter.run(
        workspace=tmp_path,
        prompt="do the thing",
        session_id="sess-adapter-1",
        timeout_seconds=10,
        on_started=on_started_calls.append,
        running=running,
    )
    # The adapter.sh script echoes a valid AdapterOutput envelope.
    assert result.success is True
    assert result.session_id == "sess-adapter-1"
    assert on_started_calls == []
    running.process.wait()


@pytest.mark.parametrize("contained", [False, True], ids=["fallback", "contained"])
def test_ordinary_task_producers_run_each_concrete_executor_body(tmp_path, monkeypatch, contained):
    """The two shipping producers reach all ordinary executor bodies.

    This deliberately exercises ``_launch_agent_with_scratch``, rather than
    calling ``executor.run`` directly: both fallback and contained launch
    forms must preserve the ordinary validator/retry contract while the real
    Claude, OpenCode, Pi, and adapter bodies communicate with their process.
    """
    import json
    from types import MethodType

    from runtime.config import Settings
    from runtime.orchestrator import executors as ex
    from runtime.orchestrator import workspace_adapters
    from runtime.orchestrator.orchestrator import Orchestrator

    observed: list[str | None] = []

    class Process:
        pid, returncode = 70001, 0

        def communicate(self, input=None, timeout=None):
            observed.append(input)
            if input and input.startswith('{"contract_version":'):
                return json.dumps({
                    "contract_version": 1, "session_id": "sess-adapter-1",
                    "success": True, "returncode": 0, "duration_seconds": 1,
                    "stdout_tail": "ok", "stderr_tail": "",
                    "adapter_metadata": {"contract_version": 1, "adapter": "adapter-1", "adapter_version": "v1"},
                    "token_usage": None,
                }), ""
            return "", ""

    class Supervisor:
        def run(self, request, **kwargs):
            assert kwargs["allow_retries"] is True
            assert kwargs["final_prelaunch_validator"] is None
            kwargs["pre_launch_validator"]()
            running = RunningHandle(
                backend="fake", token="token", request_id="ordinary",
                root_pid=70001, start_identity="fake", process=Process(),
            )
            result = kwargs["launch_body"](running)
            kwargs["on_terminal"](None)
            return SimpleNamespace(payload=result)

    monkeypatch.setattr(ex, "_resolve_binary", lambda *_a, **_k: "/bin/true")
    monkeypatch.setattr(ex, "_callee_env", lambda *_a, **_k: {})
    monkeypatch.setattr(workspace_adapters, "allow_rules_for_agent", lambda *_a, **_k: ())
    monkeypatch.setattr(ex.subprocess, "Popen", lambda *_a, **_k: Process())
    monkeypatch.setattr(ex, "detect_platform_isolation", lambda: SimpleNamespace(launch_executor=lambda *_a, **_k: Process()))

    executors = (
        ex.ClaudeExecutor("claude", "default", Settings()),
        ex.OpencodeExecutor("opencode"),
        ex.PiExecutor("pi"),
        _make_adapter_executor(tmp_path),
    )
    orch = SimpleNamespace(
        _host_supervisor=Supervisor() if contained else None,
        _sessions=None, _slug="test",
    )
    orch._run_agent_launch_contained = MethodType(
        Orchestrator._run_agent_launch_contained, orch,
    )
    for executor in executors:
        before = len(observed)
        result = Orchestrator._launch_agent_with_scratch(
            orch, task_id="TASK-PRODUCER", agent_name="dev_agent",
            workspace=tmp_path, provider="ordinary", model_name=None,
            executor=executor, session_id="sess-adapter-1",
            full_prompt="ordinary task", timeout_seconds=10,
            on_started=lambda _pid: None, on_throttle_event=None,
            pre_launch_integrity_validator=lambda: None,
            recovery_launch_validator=lambda: None,
        )
        assert result.success, result.error
        assert len(observed) == before + 1


def test_custom_adapter_contained_verifies_launch_ready(tmp_path):
    """The adapter artifact is re-verified inside the contained body too
    (defense in depth): a tampered executable fails closed."""
    from runtime.orchestrator.adapter_store import compute_sha256

    exe = tmp_path / "adapter.sh"
    exe.write_text("#!/bin/sh\necho ok\n")
    exe.chmod(0o755)
    adapter = CustomAdapterExecutor(
        profile_name="adapter-1",
        adapter_entry_id="adapter-1",
        adapter_executable=str(exe),
        adapter_hash=compute_sha256(str(exe)),
        adapter_version="v1",
        adapter_contract_version=1,
        provider="adapter",
    )
    adapter.set_invocation_context(
        agent="dev_agent", org="test", invocation_kind="task", task_id="T-1",
    )
    # Tamper AFTER construction: the hash no longer matches.
    exe.write_text("#!/bin/sh\necho tampered\n")
    running = _running_with_process(argv=("sh", "-c", "sleep 5"))
    result = adapter.run(
        workspace=tmp_path,
        prompt="do it",
        session_id="sess-adapter-1",
        timeout_seconds=10,
        running=running,
    )
    assert result.success is False
    assert "hash mismatch" in (result.error or "")
    running.process.kill()
    running.process.wait()


def test_custom_adapter_contained_passthrough_handle_fails_closed(tmp_path):
    adapter = _make_adapter_executor(tmp_path)
    running = RunningHandle(
        backend="passthrough", token="tok", request_id="inv",
        root_pid=0, start_identity="", process=None,
    )
    result = adapter.run(
        workspace=tmp_path,
        prompt="do it",
        session_id="sess-adapter-1",
        timeout_seconds=10,
        running=running,
    )
    assert result.success is False
    assert "backend-created process" in (result.error or "")


def test_custom_adapter_build_launch_spec(tmp_path):
    """The adapter's LaunchSpec launches the absolute hash-pinned executable
    with the inherited normalized env and PIPE stdio."""
    adapter = _make_adapter_executor(tmp_path)
    spec = adapter.build_launch_spec(
        workspace=tmp_path, prompt="do it", org_slug="test",
        timeout_seconds=1800,
    )
    assert spec.argv == (adapter._adapter_executable,)
    assert spec.cwd == str(tmp_path)
    assert spec.stdin == subprocess.PIPE
    assert spec.text is True
    assert spec.env.get("HAPPYRANCH_ORG_SLUG") == "test"


def test_builtin_executors_build_launch_spec(tmp_path, monkeypatch):
    """Every built-in executor exposes the contained-launch spec seam with the
    same signature (prompt via stdin where the transport uses stdin) and
    accepts the ``timeout_seconds`` seam kwarg the task/schedule producers pass
    unconditionally — an executor that rejects it fails every
    wired launch closed (TASK-5821 regression fix)."""
    from runtime.config import Settings
    from runtime.orchestrator.executor_registry import build_executor
    from runtime.orchestrator import executors as exec_mod
    from runtime.orchestrator._paths import OrgPaths

    # THR-107 hard no-PATH cutover: the binary registry must resolve the CLI.
    fake_bin = tmp_path / "fake-cli"
    fake_bin.write_text("#!/bin/sh\necho hi\n")
    fake_bin.chmod(0o755)
    monkeypatch.setattr(exec_mod, "_resolve_binary", lambda _name: str(fake_bin))

    paths = OrgPaths(root=tmp_path / "orgs" / "test")
    settings = Settings(project_root=tmp_path)
    for name in ("claude", "codex", "pi", "opencode"):
        executor = build_executor(name, settings, paths=paths)
        assert hasattr(executor, "build_launch_spec")
        spec = executor.build_launch_spec(
            workspace=tmp_path, prompt="prompt", org_slug="test",
            timeout_seconds=1800,
        )
        assert isinstance(spec.argv, tuple) and spec.argv
        assert spec.cwd == str(tmp_path)
