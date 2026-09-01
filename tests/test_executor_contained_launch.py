"""THR-207 contained-launch parity for BOTH executor Popen launch bodies.

Covers ``_run_command`` (the shared body behind Claude/Codex/Opencode/Pi/
GenericCli executors) and ``CustomAdapterExecutor.run`` (the second direct
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

import pytest

from runtime.orchestrator.executors import (
    ClaudeExecutor,
    CustomAdapterExecutor,
    ExecutorResult,
    GenericCliExecutor,
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
    unconditionally — a non-GenericCli executor that rejects it fails every
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


def test_generic_cli_build_launch_spec_substitutes_timeout(tmp_path, monkeypatch):
    """GenericCliExecutor.build_launch_spec substitutes {timeout_seconds} into
    the argv template (the prompt travels via argv) — the contained seam must
    carry the session timeout the uncontained path bakes into argv."""
    from runtime.orchestrator import executors as exec_mod
    from runtime.orchestrator.executors import GenericCliExecutor

    monkeypatch.setattr(exec_mod, "_resolve_binary", lambda _n: "/bin/echo")
    executor = GenericCliExecutor(
        profile_name="g", argv_template=["echo", "{prompt}", "{timeout_seconds}"],
        provider="g",
    )
    spec = executor.build_launch_spec(
        workspace=tmp_path, prompt="p", timeout_seconds=42,
    )
    assert spec.argv[0] == "/bin/echo"
    assert spec.argv[-1] == "42"
    assert "p" in spec.argv[1]
    # Prompt via argv -> stdin is DEVNULL (no prompt transport via stdin).
    assert spec.stdin == subprocess.DEVNULL
