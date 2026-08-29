"""Tests for the real Linux systemd/cgroup-v2 backend (THR-207 Slice B).

Two layers:

* **Deterministic unit tests** — inject fake systemd command responses
  through the single ``_run`` seam and fake cgroup file reads through
  ``_read_file``: probe degradation, limit verification, launch failure,
  finish ordering/status mapping, residue reporting, abandon, recover.
* **Real integration tests** (``-m integration``, gated on the actual probe)
  — create/launch/stop REAL transient scopes on the runner: the mandatory
  success-path descendant cleanup, escalation, emptiness verification,
  authoritative counters, and no-residue probes.

Hermetic: every real scope is torn down in the test; no live daemon is
touched.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time

import pytest

from runtime.platform.linux_systemd import (
    LinuxSystemdBackend,
    _KernelObservation,
    _LaunchState,
)
from runtime.platform.session_backend import (
    BackendLaunchError,
    Capability,
    CapabilityLevel,
    CleanupStatus,
    LaunchSpec,
    MeasurementProvenance,
    PendingHandle,
    RunningHandle,
    TerminalReason,
)

# ── deterministic fake helpers ───────────────────────────────────────


def _install_fake_run(backend: LinuxSystemdBackend, script):
    """Replace the single systemd shell-out seam with a deterministic fake."""

    def run(argv, timeout=10.0):
        return script(list(argv))

    backend._run = run  # type: ignore[method-assign]


def _install_fake_read_file(backend: LinuxSystemdBackend, files: dict[str, str]):
    """Fake cgroup file reads: cg-relative path -> content."""

    def read_file(cg, name):
        return files.get(name)

    backend._read_file = read_file  # type: ignore[method-assign]


_FAKE_PROCESSES: list[subprocess.Popen] = []


@pytest.fixture(autouse=True)
def _kill_fake_processes():
    """Tear down the disposable fake-launch processes after every test."""
    yield
    for proc in list(_FAKE_PROCESSES):
        try:
            proc.kill()
            proc.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
    _FAKE_PROCESSES.clear()


def _fake_launch_ok(
    backend: LinuxSystemdBackend,
    *,
    cg: str,
    limits_ok: bool = True,
) -> None:
    """Make ``launch`` succeed deterministically without a real scope.

    By default the Slice C applied-envelope verification is stubbed to pass
    (``limits_ok=True``); pass ``limits_ok=False`` to exercise the fail-
    closed mismatch path. The real verification is exercised by the real
    integration suites (probe-gated) and by ``_session_limits_applied``
    unit tests with fake cgroup file reads.
    """

    def systemd_run_scope(unit, slice_name, argv, **kwargs):
        proc = subprocess.Popen(
            ["sleep", "300"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _FAKE_PROCESSES.append(proc)
        return proc

    def wait_for_cgroup(unit, proc, timeout=None):
        return cg

    def start_identity(pid):
        return "boot-1"

    def session_limits_applied(cg_path, policy):
        return (limits_ok, "envelope-applied" if limits_ok else "stub-mismatch")

    backend._systemd_run_scope = systemd_run_scope  # type: ignore[method-assign]
    backend._wait_for_cgroup = wait_for_cgroup  # type: ignore[method-assign]
    backend._census.start_identity = start_identity  # type: ignore[method-assign]
    backend._session_limits_applied = session_limits_applied  # type: ignore[method-assign]


def _running(backend: LinuxSystemdBackend, token: str, pid: int = 9000) -> RunningHandle:
    return RunningHandle(
        backend=backend.name,
        token=token,
        request_id="req-1",
        root_pid=pid,
        start_identity="boot-1",
        process=None,
    )


def _policy():
    from runtime.orchestrator.host_supervisor import canary_policy

    return canary_policy()


def _request():
    from runtime.orchestrator.host_supervisor import AdmissionRequest

    return AdmissionRequest(
        org="test", invocation_kind="schedule", logical_id="sched-1",
        executor_profile="claude",
    )


# ── probe: honest degradation ────────────────────────────────────────


def test_probe_unhealthy_when_systemd_user_manager_unreachable():
    backend = LinuxSystemdBackend()

    def script(argv):
        if "--user" in argv and "is-system-running" in argv:
            return (2, "Failed to connect to bus")
        return (0, "")

    _install_fake_run(backend, script)
    report = backend.probe()
    assert report.healthy is False
    assert report.capabilities == {}
    assert report.reason is not None


def test_probe_unhealthy_when_cgroup_v2_controllers_missing(monkeypatch):
    backend = LinuxSystemdBackend()
    _install_fake_run(backend, lambda argv: (0, "running"))
    monkeypatch.setattr(backend, "_cgroup_v2_controllers", lambda: None)
    report = backend.probe()
    assert report.healthy is False
    assert "cgroup v2" in report.reason


def test_probe_degrades_when_scope_ops_fail(monkeypatch):
    backend = LinuxSystemdBackend()
    _install_fake_run(backend, lambda argv: (0, "running"))
    monkeypatch.setattr(backend, "_cgroup_v2_controllers", lambda: {"memory", "pids", "cpu"})
    monkeypatch.setattr(backend, "_probe_scope_operations", lambda: ({}, "boom"))
    report = backend.probe()
    assert report.healthy is False
    assert "boom" in report.reason


# ── probe internals: applied limits + counters ───────────────────────


def test_applied_limits_verifies_property_files():
    backend = LinuxSystemdBackend()
    _install_fake_read_file(
        backend,
        {
            "memory.max": "16777216",
            "pids.max": "4",
            "cpu.max": "10000 100000",
        },
    )
    applied = backend._applied_limits("/cg")
    assert applied == {
        "memory.max": True,
        "pids.max": True,
        "cpu.max": True,
    }


def test_applied_limits_false_when_files_absent():
    backend = LinuxSystemdBackend()
    _install_fake_read_file(backend, {})
    applied = backend._applied_limits("/cg")
    assert applied == {
        "memory.max": False,
        "pids.max": False,
        "cpu.max": False,
    }


def test_read_counters_parses_authoritative_files():
    backend = LinuxSystemdBackend()
    _install_fake_read_file(
        backend,
        {
            "memory.current": "1048576",
            "memory.peak": "2097152",
            "pids.current": "3",
            "pids.peak": "7",
            "cpu.stat": "usage_usec 1500000\nuser_usec 1000000\nsystem_usec 500000\n",
        },
    )
    counters = backend._read_counters("/cg")
    assert counters["memory.current"] == 1048576
    assert counters["memory.peak"] == 2097152
    assert counters["pids.current"] == 3
    assert counters["pids.peak"] == 7
    assert counters["cpu.stat"] == 1.5  # usage_usec -> seconds


def test_read_counters_empty_when_unreadable():
    backend = LinuxSystemdBackend()
    _install_fake_read_file(backend, {})
    assert backend._read_counters("/cg") == {}


def test_read_counters_rejects_negative_pid_counters():
    """Semantically invalid negative PID counts are dropped at the read
    boundary (the kernel never reports a negative membership/peak): a
    negative pids.peak or pids.current must never reach the provenance
    paths as authoritative evidence."""
    backend = LinuxSystemdBackend()
    _install_fake_read_file(
        backend,
        {
            "memory.current": "1048576",
            "memory.peak": "2097152",
            "pids.current": "-1",
            "pids.peak": "-1",
            "pids.max": "4",
            "cpu.stat": "usage_usec 1500000\n",
        },
    )
    counters = backend._read_counters("/cg")
    # Negative PID counters are treated as absent; valid non-negative
    # counters (including the stored-but-unconsumed pids.max) still parse.
    assert "pids.current" not in counters
    assert "pids.peak" not in counters
    assert counters["pids.max"] == 4
    assert counters["memory.current"] == 1048576
    assert counters["memory.peak"] == 2097152
    assert counters["cpu.stat"] == 1.5


def test_read_counters_keeps_non_negative_pid_counters():
    """Valid non-negative pids.current/pids.peak (including zero, which a
    live cgroup can legitimately read) are still parsed and exposed."""
    backend = LinuxSystemdBackend()
    _install_fake_read_file(
        backend,
        {
            "pids.current": "0",
            "pids.peak": "3",
        },
    )
    counters = backend._read_counters("/cg")
    assert counters["pids.current"] == 0
    assert counters["pids.peak"] == 3


# ── prepare / launch ─────────────────────────────────────────────────


def test_prepare_reserves_unique_scope_units():
    backend = LinuxSystemdBackend()
    p1 = backend.prepare(_request(), _policy())
    p2 = backend.prepare(_request(), _policy())
    assert p1.token.endswith(".scope")
    assert p1.token != p2.token
    assert p1.request_id == "sched-1"


def test_launch_failure_raises_backend_launch_error():
    backend = LinuxSystemdBackend()

    def systemd_run_scope(unit, slice_name, argv, **kwargs):
        raise OSError("no systemd-run")

    backend._systemd_run_scope = systemd_run_scope  # type: ignore[method-assign]
    pending = backend.prepare(_request(), _policy())
    with pytest.raises(BackendLaunchError):
        backend.launch(pending, LaunchSpec(argv=("sleep", "5")))


def test_launch_verifies_scope_membership():
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/user.slice/happyranch.slice/test.scope")
    _install_fake_run(backend, lambda argv: (0, ""))
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    assert running.root_pid > 0
    assert running.start_identity == "boot-1"
    with backend._launched_lock:
        state = backend._launched[pending.token]
    assert state.cgroup == "/user.slice/happyranch.slice/test.scope"


# ── Slice C: enforcement property emission + applied verification ────


def _capture_launch_properties(backend: LinuxSystemdBackend, request) -> list:
    """Launch with a fake systemd-run that records the requested properties."""
    captured: list = []

    def systemd_run_scope(unit, slice_name, argv, **kwargs):
        captured.append(kwargs.get("properties", ()))
        proc = subprocess.Popen(
            ["sleep", "300"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _FAKE_PROCESSES.append(proc)
        return proc

    def wait_for_cgroup(unit, proc, timeout=None):
        return "/user.slice/happyranch.slice/test.scope"

    def session_limits_applied(cg_path, policy):
        return (True, "envelope-applied")

    backend._systemd_run_scope = systemd_run_scope  # type: ignore[method-assign]
    backend._wait_for_cgroup = wait_for_cgroup  # type: ignore[method-assign]
    backend._census.start_identity = lambda pid: "boot-1"  # type: ignore[method-assign]
    backend._session_limits_applied = session_limits_applied  # type: ignore[method-assign]
    pending = backend.prepare(request, _policy())
    backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    return captured


def _task_request():
    from runtime.orchestrator.host_supervisor import AdmissionRequest

    return AdmissionRequest(
        org="test", invocation_kind="task", logical_id="task-1",
        executor_profile="claude",
    )


def test_launch_emits_exact_task_enforcement_properties():
    """Slice C: a real session scope for a task invocation emits the exact
    founder-approved envelope — MemoryHigh=14G, MemoryMax=24G, TasksMax=1024 —
    and deliberately NO CPUQuota property."""
    backend = LinuxSystemdBackend()
    captured = _capture_launch_properties(backend, _task_request())
    assert len(captured) == 1
    props = dict(captured[0])
    assert props == {
        "MemoryHigh": str(14 * 1024**3),
        "MemoryMax": str(24 * 1024**3),
        "TasksMax": "1024",
    }
    assert "CPUQuota" not in props
    assert all("CPU" not in k for k in props)


def test_launch_emits_exact_light_enforcement_properties():
    """Slice C: thread/dream/wake/schedule scopes emit the light envelope —
    MemoryHigh=2G, MemoryMax=4G (the founder ruling fixes MemoryMax at
    exactly 4G), TasksMax=1024 — and no CPUQuota."""
    for kind in ("thread", "dream", "wake", "schedule"):
        backend = LinuxSystemdBackend()
        from runtime.orchestrator.host_supervisor import AdmissionRequest

        request = AdmissionRequest(
            org="test", invocation_kind=kind, logical_id=f"{kind}-1",
            executor_profile="pi",
        )
        captured = _capture_launch_properties(backend, request)
        props = dict(captured[0])
        assert props == {
            "MemoryHigh": str(2 * 1024**3),
            "MemoryMax": str(4 * 1024**3),
            "TasksMax": "1024",
        }, kind
        assert "CPUQuota" not in props


def test_launch_unknown_kind_emits_light_envelope_never_task():
    """A future/unknown invocation kind is conservatively contained: it gets
    the light envelope — never the task-sized envelope."""
    backend = LinuxSystemdBackend()
    from runtime.orchestrator.host_supervisor import AdmissionRequest

    request = AdmissionRequest(
        org="test", invocation_kind="mystery-kind", logical_id="m-1",
        executor_profile="claude",
    )
    captured = _capture_launch_properties(backend, request)
    props = dict(captured[0])
    assert props == {
        "MemoryHigh": str(2 * 1024**3),
        "MemoryMax": str(4 * 1024**3),
        "TasksMax": "1024",
    }
    assert props["MemoryMax"] != str(24 * 1024**3)


def test_launch_applied_limits_mismatch_fails_closed():
    """A scope whose cgroup did not actually apply the envelope is a
    containment failure: launch raises BackendLaunchError (never a silent
    best-effort claim of guaranteed limits)."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg", limits_ok=False)
    _install_fake_run(backend, lambda argv: (0, ""))
    pending = backend.prepare(_request(), _policy())
    with pytest.raises(BackendLaunchError, match="did not apply the session enforcement envelope"):
        backend.launch(pending, LaunchSpec(argv=("sleep", "5")))


def test_session_limits_applied_verifies_cgroup_files_exactly():
    """The applied-envelope verification compares the cgroup files
    (memory.high / memory.max / pids.max) byte-for-byte with the policy; a
    missing, partial or wrong value fails (fail-closed)."""
    from runtime.platform.enforcement_policy import (
        LIGHT_ENFORCEMENT_POLICY,
        TASK_ENFORCEMENT_POLICY,
    )

    backend = LinuxSystemdBackend()
    _install_fake_read_file(
        backend,
        {
            "memory.high": str(2 * 1024**3),
            "memory.max": str(4 * 1024**3),
            "pids.max": "1024",
        },
    )
    ok, evidence = backend._session_limits_applied("/cg", LIGHT_ENFORCEMENT_POLICY)
    assert ok is True
    assert evidence == "envelope-applied"
    # Wrong memory.high (soft throttle mismatch) fails.
    _install_fake_read_file(
        backend, {"memory.high": str(1), "memory.max": str(4 * 1024**3), "pids.max": "1024"}
    )
    ok, evidence = backend._session_limits_applied("/cg", LIGHT_ENFORCEMENT_POLICY)
    assert ok is False
    assert "memory.high" in evidence
    # Missing files fail (never silently claimed).
    _install_fake_read_file(backend, {})
    ok, evidence = backend._session_limits_applied("/cg", LIGHT_ENFORCEMENT_POLICY)
    assert ok is False
    assert "memory.high" in evidence and "pids.max" in evidence
    # cpu.max is NOT part of the session verification (no CPUQuota emitted).
    assert "cpu.max" not in evidence
    # The task envelope verifies against its own exact values.
    _install_fake_read_file(
        backend,
        {
            "memory.high": str(14 * 1024**3),
            "memory.max": str(24 * 1024**3),
            "pids.max": "1024",
        },
    )
    ok, evidence = backend._session_limits_applied("/cg", TASK_ENFORCEMENT_POLICY)
    assert ok is True


def test_session_limits_applied_rejects_suffix_extra_tokens_and_mismatch(tmp_path):
    """The applied-envelope verification compares the ENTIRE normalized
    cgroup file value against the expected value (fail-closed). A suffix
    appended to an otherwise-correct value, an extra token after it, or a
    plain wrong value must all fail for memory.high, memory.max and
    pids.max — the old token-prefix comparison silently accepted the first
    two. The kernel's real file shape (value + trailing newline) still
    verifies: ``_read_file`` strips outer whitespace, matching cgroup v2
    file semantics."""
    from runtime.platform.enforcement_policy import LIGHT_ENFORCEMENT_POLICY

    backend = LinuxSystemdBackend()
    backend._cgroup_root = os.fspath(tmp_path)
    cgdir = tmp_path / "cg"
    cgdir.mkdir()
    base = {
        "memory.high": str(2 * 1024**3),
        "memory.max": str(4 * 1024**3),
        "pids.max": "1024",
    }

    def write(files):
        for name, value in files.items():
            (cgdir / name).write_text(value, encoding="utf-8")

    write(base)
    ok, evidence = backend._session_limits_applied("/cg", LIGHT_ENFORCEMENT_POLICY)
    assert ok is True
    assert evidence == "envelope-applied"
    for name in ("memory.high", "memory.max", "pids.max"):
        # Suffix appended to the exact value fails.
        write({name: base[name] + "junk"})
        ok, evidence = backend._session_limits_applied("/cg", LIGHT_ENFORCEMENT_POLICY)
        assert ok is False and name in evidence, f"suffix {name}: {evidence}"
        # Extra token after the exact value fails.
        write({name: base[name] + " 999"})
        ok, evidence = backend._session_limits_applied("/cg", LIGHT_ENFORCEMENT_POLICY)
        assert ok is False and name in evidence, f"extra-token {name}: {evidence}"
        # Plain value mismatch fails.
        write({name: str(int(base[name]) + 1)})
        ok, evidence = backend._session_limits_applied("/cg", LIGHT_ENFORCEMENT_POLICY)
        assert ok is False and name in evidence, f"mismatch {name}: {evidence}"
    # The kernel's real file shape (value + trailing newline) verifies.
    write({name: base[name] + "\n" for name in base})
    ok, evidence = backend._session_limits_applied("/cg", LIGHT_ENFORCEMENT_POLICY)
    assert ok is True


class _ExitedProc:
    """A fake launched process that has already exited (returncode set)."""

    returncode = 0
    pid = 424242

    def poll(self):
        return self.returncode

    def kill(self):
        # Real Popen.kill() no-ops once the process is reaped (returncode is
        # set), matching the fast-exit semantics this fake models.
        return None


def _install_fast_exit(backend, systemctl_script):
    """Fast-exit launch fakes: systemd-run returns an already-exited proc,
    ``_wait_for_cgroup`` resolves nothing (the process exited), and the
    systemctl seam answers ControlGroup / stop per *systemctl_script*.
    Returns the recorded systemctl call log."""
    calls: list[list[str]] = []

    def run(argv, timeout=10.0):
        calls.append(list(argv))
        return systemctl_script(list(argv))

    backend._run = run  # type: ignore[method-assign]
    backend._systemd_run_scope = (  # type: ignore[method-assign]
        lambda unit, slice_name, argv, **kwargs: _ExitedProc()
    )
    backend._wait_for_cgroup = lambda unit, proc, timeout=None: None  # type: ignore[method-assign]
    backend._start_exit_capture = lambda state, proc: None  # type: ignore[method-assign]
    return calls


def test_launch_fast_exit_fails_closed_when_scope_already_collected():
    """A target that exits before ``_wait_for_cgroup`` resolves the scope
    must NOT report a normal RunningHandle: an enforced session is never
    reported as launched without authoritative exact verification of
    MemoryHigh/MemoryMax/TasksMax. When the transient scope is already
    collected (no ControlGroup evidence), launch fails closed AFTER issuing
    the scope stop — the caller abandons the pending handle (admission
    released), freezes SPAWN_FAILURE, and no ``on_started`` callback or
    receipt was published (cleanup-before-release, no residue)."""
    backend = LinuxSystemdBackend()

    def systemctl(argv):
        # The scope is gone: no ControlGroup is queryable; stop is a no-op.
        return (1, "") if "show" in argv else (0, "")

    calls = _install_fast_exit(backend, systemctl)

    def must_not_verify(cg, policy):
        raise AssertionError("_session_limits_applied must not run without a cgroup")

    backend._session_limits_applied = must_not_verify  # type: ignore[method-assign]
    pending = backend.prepare(_request(), _policy())
    with pytest.raises(
        BackendLaunchError,
        match="exited before its session enforcement envelope could be verified",
    ):
        backend.launch(pending, LaunchSpec(argv=("sh", "-c", "exit 0")))
    # Failure cleanup contract: the scope stop was issued before the raise.
    assert any("stop" in argv for argv in calls)
    # Verification was not skipped by returning a handle — it could not be
    # obtained, so launch failed closed instead.
    assert all("envelope-applied" not in argv for argv in calls)


def test_launch_fast_exit_verifies_via_control_group_seam(tmp_path):
    """When the target exits before ``_wait_for_cgroup`` resolves but the
    transient scope is still queryable, launch resolves the cgroup through
    the authoritative ControlGroup seam and STILL verifies the applied
    envelope exactly (via the real ``_read_file`` path) before returning
    the RunningHandle — verification is never skipped on the fast-exit
    path."""
    backend = LinuxSystemdBackend()
    backend._cgroup_root = os.fspath(tmp_path)
    cgdir = tmp_path / "cg"
    cgdir.mkdir()
    light = {
        "memory.high": str(2 * 1024**3),
        "memory.max": str(4 * 1024**3),
        "pids.max": "1024",
    }
    for name, value in light.items():
        (cgdir / name).write_text(value + "\n", encoding="utf-8")

    def systemctl(argv):
        # The scope still exists: ControlGroup resolves /cg; stop is a no-op.
        if "show" in argv:
            return (0, "/cg")
        return (0, "")

    _install_fast_exit(backend, systemctl)
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "exit 0")))
    assert isinstance(running, RunningHandle)
    assert running.token == pending.token
    assert running.invocation_kind == "schedule"


def test_launch_fast_exit_fails_closed_on_envelope_mismatch(tmp_path):
    """A fast-exit target whose resolvable scope did NOT apply the exact
    envelope is a containment failure: launch raises BackendLaunchError
    after issuing the scope stop (never a silent unverified handle)."""
    backend = LinuxSystemdBackend()
    backend._cgroup_root = os.fspath(tmp_path)
    cgdir = tmp_path / "cg"
    cgdir.mkdir()
    (cgdir / "memory.high").write_text("1", encoding="utf-8")  # wrong soft limit
    (cgdir / "memory.max").write_text(str(4 * 1024**3), encoding="utf-8")
    (cgdir / "pids.max").write_text("1024", encoding="utf-8")

    def systemctl(argv):
        if "show" in argv:
            return (0, "/cg")
        return (0, "")

    calls = _install_fast_exit(backend, systemctl)
    pending = backend.prepare(_request(), _policy())
    with pytest.raises(
        BackendLaunchError, match="did not apply the session enforcement envelope"
    ):
        backend.launch(pending, LaunchSpec(argv=("sh", "-c", "exit 0")))
    assert any("stop" in argv for argv in calls)

def test_prepare_and_launch_carry_receipt_attribution():
    """The bounded attribution (invocation_kind + executor_profile) sourced
    from the AdmissionRequest rides the pending/running handles so the
    finish-time receipt is attributed honestly."""
    backend = LinuxSystemdBackend()
    pending = backend.prepare(_request(), _policy())
    assert pending.invocation_kind == "schedule"
    assert pending.executor_profile == "claude"
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, ""))
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    assert running.invocation_kind == "schedule"
    assert running.executor_profile == "claude"


def test_finish_attributes_receipt_from_running_handle():
    """The Linux finish receipt carries the bounded attribution so operator
    surfaces can attribute receipts to a producer kind + executor profile."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, ""))
    _install_fake_read_file(
        backend, {"memory.current": "0", "pids.current": "0", "cpu.stat": "usage_usec 0\n"}
    )
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    _install_fake_run(backend, lambda argv: (0, "inactive" if "is-active" in argv else ""))
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt is not None
    assert receipt.invocation_kind == "schedule"
    assert receipt.executor_profile == "claude"


# ── finish: ordering and status mapping (deterministic) ──────────────


def _active_until_killed():
    """Fake systemctl that reports the unit active until the KILL signal."""

    killed = {"flag": False}

    def script(argv):
        if any("KILL" in a for a in argv):
            killed["flag"] = True
            return (0, "")
        if "is-active" in argv:
            return (0, "active") if not killed["flag"] else (0, "inactive")
        if "stop" in argv:
            return (0, "") if killed["flag"] else (3, "inactive")
        if "show" in argv:
            return (0, "/user.slice/happyranch.slice/test.scope")
        return (0, "")

    return script, killed


def test_finish_clean_success_stops_scope_explicitly():
    """The mandatory clean-success path still performs the explicit stop."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/user.slice/happyranch.slice/test.scope")
    calls = []

    def script(argv):
        calls.append(argv[2] if len(argv) > 2 else "")
        if "is-active" in argv:
            return (0, "inactive")  # scope already auto-removed (no residue)
        if "show" in argv:
            return (0, "/user.slice/happyranch.slice/test.scope")
        return (0, "")

    _install_fake_run(backend, script)
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    receipt = backend.finish(running, "success", grace_seconds=1.0)
    assert "stop" in calls  # explicit stop ran on the clean-success path
    assert receipt.cleanup_status is CleanupStatus.CLEAN
    assert receipt.quiescent is True
    assert receipt.survivors == ()


def test_finish_escalates_to_kill_when_members_survive_stop():
    """A TERM-resistant member keeps the cgroup non-empty after stop; the
    backend escalates to KILL and reaches quiescence."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/user.slice/happyranch.slice/test.scope")
    killed = {"flag": False}

    def members(cg):
        return {} if killed["flag"] else {7001: "ident-1"}

    def script(argv):
        if any("KILL" in a for a in argv):
            killed["flag"] = True
            return (0, "")
        if "is-active" in argv:
            return (0, "inactive")
        if "show" in argv:
            return (0, "/user.slice/happyranch.slice/test.scope")
        return (0, "")

    _install_fake_run(backend, script)
    backend._cgroup_members = members  # type: ignore[method-assign]
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    receipt = backend.finish(running, "failure", grace_seconds=0.2)
    assert killed["flag"] is True
    assert receipt.cleanup_status is CleanupStatus.KILL
    assert receipt.quiescent is True
    assert receipt.survivors == ()


def test_finish_reports_guaranteed_residue_as_survivors():
    """Residue surviving a guaranteed cleanup is reported (admission-blocking)."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/user.slice/happyranch.slice/test.scope")
    _install_fake_run(backend, lambda argv: (0, ""))
    backend._cgroup_members = lambda cg: {7001: "ident-1"}  # type: ignore[method-assign]
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    receipt = backend.finish(running, "success", grace_seconds=0.2)
    assert receipt.quiescent is False
    assert len(receipt.survivors) == 1
    assert receipt.survivors[0].pid == 7001
    assert receipt.survivors[0].start_identity == "ident-1"


def test_finish_incomplete_when_members_survive_escalation():
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, ""))
    backend._cgroup_members = lambda cg: {7001: "ident-1"}  # type: ignore[method-assign]
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    receipt = backend.finish(running, "timeout", grace_seconds=0.2)
    assert receipt.cleanup_status is CleanupStatus.INCOMPLETE
    assert receipt.quiescent is False
    assert len(receipt.survivors) == 1


def test_finish_preserves_primary_terminal_reason():
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, ""))
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    for reason in ("success", "failure", "timeout", "cancelled", "shutdown"):
        receipt = backend.finish(running, reason, grace_seconds=0.1)
        assert receipt.terminal_reason == reason


def test_finish_merges_authoritative_counters():
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, ""))
    _install_fake_read_file(
        backend,
        {
            "memory.peak": "3000000",
            "pids.peak": "9",
            "pids.current": "5",
            "cpu.stat": "usage_usec 2500000\n",
        },
    )
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.memory_peak_bytes == 3000000
    assert receipt.memory_peak_provenance is MeasurementProvenance.KERNEL
    assert receipt.cpu_total_seconds == 2.5
    assert receipt.cpu_total_provenance is MeasurementProvenance.KERNEL
    # The kernel pids.peak is the authoritative process peak; the live
    # pids.current never replaces it.
    assert receipt.process_peak == 9
    assert receipt.process_peak_provenance is MeasurementProvenance.KERNEL


def test_finish_merges_sampled_values_when_no_kernel_counters():
    from runtime.platform.session_backend import ResourceSample

    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, ""))
    _install_fake_read_file(backend, {})
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    samples = (
        ResourceSample(sampled_at=1.0, memory_peak_bytes=100, cpu_total_seconds=0.5, process_count=2),
        ResourceSample(sampled_at=2.0, memory_peak_bytes=300, cpu_total_seconds=1.0, process_count=4),
    )
    receipt = backend.finish(running, "success", grace_seconds=0.1, samples=samples)
    assert receipt.memory_peak_bytes == 300
    assert receipt.memory_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt.cpu_total_seconds == 1.0
    assert receipt.sample_gaps == (1.0,)


# ── finish: fail-closed quiescence (review TASK-5656 findings 1 & 4) ──


def test_unit_active_fails_closed_on_interrogation_error():
    """Only POSITIVE terminal evidence (inactive/failed/dead/not-found) is
    quiescent; an interrogation error (bad rc, timeout, empty output) leaves
    the unit state UNKNOWN and is treated as still-active (fail-closed)."""
    backend = LinuxSystemdBackend()
    cases = {
        # (rc, out) -> expected _unit_active (True = not quiescent)
        (0, "active"): True,
        (0, "activating"): True,
        (0, "deactivating"): True,
        (0, "inactive"): False,
        (0, "failed"): False,
        (3, "inactive"): False,  # LSB: program is not running
        (1, "Failed to connect to bus"): True,  # interrogation ERROR
        (-1, "timeout after 5s: systemctl --user is-active"): True,
        (0, ""): True,  # empty state -> unknown
        (5, ""): True,  # unknown rc -> unknown
    }
    for (rc, out), expected in cases.items():
        _install_fake_run(backend, lambda argv, rc=rc, out=out: (rc, out))
        got = backend._unit_active("happyranch-session-x.scope")
        assert got is expected, f"rc={rc} out={out!r}: expected active={expected}, got {got}"


def test_finish_fails_closed_when_cgroup_membership_unreadable():
    """cgroup.procs unreadable must never yield CLEAN/quiescent: the unit is
    stopped and KILL-escalated best-effort, then the receipt is INCOMPLETE
    with explicit unreadable-membership evidence (admission-blocking)."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(backend, {})  # cgroup.procs unreadable
    backend._cgroup_dir_exists = lambda cg: True  # type: ignore[method-assign]
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    receipt = backend.finish(running, "success", grace_seconds=0.2)
    assert receipt.cleanup_status is not CleanupStatus.CLEAN
    assert receipt.cleanup_status is CleanupStatus.INCOMPLETE
    assert receipt.quiescent is False
    assert receipt.survivors == ()  # unenumerable residue is never fabricated
    assert "cgroup_procs_unreadable" in receipt.enforcement_events


def test_finish_fails_closed_when_unit_state_interrogation_fails():
    """A systemctl is-active ERROR (unknown unit state) must never become
    CLEAN even when the cgroup is empty/removed."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")

    def script(argv):
        if "is-active" in argv:
            return (1, "Failed to connect to bus")
        return (0, "")

    _install_fake_run(backend, script)
    _install_fake_read_file(backend, {})  # cgroup gone -> genuinely empty
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    receipt = backend.finish(running, "success", grace_seconds=0.2)
    assert receipt.cleanup_status is not CleanupStatus.CLEAN
    assert receipt.cleanup_status is CleanupStatus.INCOMPLETE
    assert receipt.quiescent is False


def test_teardown_and_verify_fails_closed_when_membership_unreadable():
    """The probe's own emptiness verification must fail when cgroup.procs
    cannot be read (unknown membership never proves cgroup emptiness)."""
    backend = LinuxSystemdBackend()
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(backend, {})
    backend._cgroup_dir_exists = lambda cg: True  # type: ignore[method-assign]
    ok, reason = backend._teardown_and_verify("unit.scope", "/cg", 0.2)
    assert ok is False
    assert "unreadable" in reason


# ── finish: absent counters are unavailable, never sampled ───────────


def test_finish_absent_counters_are_unavailable_not_sampled():
    """Wholly absent counters AND no samples: values are None with
    UNAVAILABLE provenance — never labeled SAMPLED with no sample behind
    them."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(backend, {})
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.memory_peak_bytes is None
    assert receipt.memory_peak_provenance is MeasurementProvenance.UNAVAILABLE
    assert receipt.cpu_total_seconds is None
    assert receipt.cpu_total_provenance is MeasurementProvenance.UNAVAILABLE
    assert receipt.process_peak is None
    assert receipt.process_peak_provenance is MeasurementProvenance.UNAVAILABLE


def test_finish_partial_absent_counters_unavailable():
    """Partial absence: memory.peak present (KERNEL) while cpu.stat and
    pids.current are absent with no samples — cpu/process are None +
    UNAVAILABLE, memory stays KERNEL."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(backend, {"memory.peak": "3000000"})
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "5")))
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.memory_peak_bytes == 3000000
    assert receipt.memory_peak_provenance is MeasurementProvenance.KERNEL
    assert receipt.cpu_total_seconds is None
    assert receipt.cpu_total_provenance is MeasurementProvenance.UNAVAILABLE
    assert receipt.process_peak is None
    assert receipt.process_peak_provenance is MeasurementProvenance.UNAVAILABLE


# ── process-peak provenance corrective (TASK-5910/TASK-5911) ──────────


def test_finish_clean_success_empty_tree_teardown_uses_kernel_pids_peak():
    """The shipped regression: a clean-success empty-tree teardown reads
    pids.current == 0 (the tree already exited) while the kernel's
    pids.peak still holds the true lifetime high-water mark. The receipt
    MUST report the pids.peak value with KERNEL provenance — a fabricated
    authoritative zero must never recur."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(
        backend,
        {
            "pids.peak": "5",  # lifetime high-water mark, read pre-teardown
            "pids.current": "0",  # empty tree at finish time
        },
    )
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "exit 0")))
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.process_peak == 5
    assert receipt.process_peak_provenance is MeasurementProvenance.KERNEL


def test_finish_kernel_pids_peak_is_authoritative_over_live_count():
    """A nonzero pids.peak is the authoritative process peak; the live
    pids.current at finish time never replaces or relabels it."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(
        backend,
        {
            "pids.peak": "7",
            "pids.current": "3",
        },
    )
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "60")))
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.process_peak == 7
    assert receipt.process_peak_provenance is MeasurementProvenance.KERNEL


def test_finish_missing_pids_peak_falls_back_to_sampled_merged_with_current():
    """Kernels without pids.peak: the sampled peak is the honest cap, merged
    with the best-effort live count (max); provenance is SAMPLED — never
    KERNEL. An empty-tree teardown pids.current == 0 must not drag the peak
    down."""
    from runtime.platform.session_backend import ResourceSample

    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(
        backend,
        {
            "pids.current": "0",
        },
    )
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "60")))
    samples = (
        ResourceSample(sampled_at=1.0, process_count=2),
        ResourceSample(sampled_at=2.0, process_count=4),
    )
    receipt = backend.finish(running, "success", grace_seconds=0.1, samples=samples)
    assert receipt.process_peak == 4  # sampled peak, not the teardown 0
    assert receipt.process_peak_provenance is MeasurementProvenance.SAMPLED


def test_finish_missing_pids_peak_with_only_live_count_is_best_effort_sampled():
    """Absent/invalid pids.peak with ONLY pids.current: the live count is an
    explicit best-effort fallback labeled SAMPLED — it must never be labeled
    KERNEL (a teardown live count can undercount the true peak)."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(
        backend,
        {
            "pids.current": "5",
        },
    )
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "60")))
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.process_peak == 5
    assert receipt.process_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt.process_peak_provenance is not MeasurementProvenance.KERNEL


def test_finish_invalid_pids_peak_falls_back_honestly():
    """An unparsable pids.peak file is treated as absent (never a fabricated
    value); the fallback path applies with SAMPLED provenance."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(
        backend,
        {
            "pids.peak": "not-an-int",
            "pids.current": "2",
        },
    )
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "60")))
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.process_peak == 2
    assert receipt.process_peak_provenance is MeasurementProvenance.SAMPLED


def test_finish_negative_pids_peak_falls_back_to_live_count_sampled():
    """A parseable but semantically invalid negative pids.peak is rejected
    (treated as absent), so finish() falls back to the valid non-negative
    pids.current with SAMPLED provenance — it can never earn KERNEL."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(
        backend,
        {
            "pids.peak": "-1",
            "pids.current": "2",
        },
    )
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "60")))
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.process_peak == 2
    assert receipt.process_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt.process_peak_provenance is not MeasurementProvenance.KERNEL


def test_finish_negative_pids_peak_with_samples_uses_sampled_merged():
    """A negative pids.peak cannot suppress the honest sampled-evidence
    fallback: the sampled peak merged with the valid live count is reported
    with SAMPLED provenance, never KERNEL."""
    from runtime.platform.session_backend import ResourceSample

    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(
        backend,
        {
            "pids.peak": "-1",
            "pids.current": "2",
        },
    )
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "60")))
    samples = (
        ResourceSample(sampled_at=1.0, process_count=4),
        ResourceSample(sampled_at=2.0, process_count=3),
    )
    receipt = backend.finish(running, "success", grace_seconds=0.1, samples=samples)
    assert receipt.process_peak == 4  # sampled peak; the negative peak is inert
    assert receipt.process_peak_provenance is MeasurementProvenance.SAMPLED


def test_finish_negative_pids_current_cannot_contaminate_fallback():
    """A negative pids.current is rejected too, so it cannot contaminate the
    fallback: with samples it is simply skipped (the sampled peak stands)
    and with no other evidence the receipt is truthful UNAVAILABLE — a
    fabricated negative live count is never reported."""
    from runtime.platform.session_backend import ResourceSample

    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(
        backend,
        {
            "pids.current": "-1",
        },
    )
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "60")))
    samples = (
        ResourceSample(sampled_at=1.0, process_count=4),
        ResourceSample(sampled_at=2.0, process_count=3),
    )
    receipt = backend.finish(running, "success", grace_seconds=0.1, samples=samples)
    assert receipt.process_peak == 4  # only the samples are usable evidence
    assert receipt.process_peak_provenance is MeasurementProvenance.SAMPLED
    # With NO other evidence the negative live count is absent: UNAVAILABLE,
    # never a reported negative value.
    receipt_unavailable = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt_unavailable.process_peak is None
    assert (
        receipt_unavailable.process_peak_provenance
        is MeasurementProvenance.UNAVAILABLE
    )


def test_finish_empty_tree_teardown_zero_is_never_kernel_provenance():
    """The shipped defect's exact shape: a clean-success empty-tree teardown
    with no pids.peak and no samples must NOT yield process_peak == 0 with
    KERNEL provenance. The best-effort live count stays SAMPLED (an explicit
    fallback); truthful UNAVAILABLE applies only when no evidence exists at
    all."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(
        backend,
        {
            "pids.current": "0",
        },
    )
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "exit 0")))
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.process_peak == 0
    assert receipt.process_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt.process_peak_provenance is not MeasurementProvenance.KERNEL


def _set_exit_observation(
    backend: LinuxSystemdBackend,
    token: str,
    *,
    memory: int | None = None,
    cpu: float | None = None,
    proc: int | None = None,
    final_read_ok_memory: bool = True,
    final_read_ok_cpu: bool = True,
    final_read_ok_pids: bool = True,
) -> None:
    """Deterministic seam: record the exit-watcher's immutable observation
    on the launch state (what the watcher thread stores after capturing the
    kernel counters while the scope was alive). Final-read validity is
    tracked **per counter**: a flag set False models that counter's exit-
    instant read losing the collection race, so its retained value came
    from the last live read while the scope was alive."""
    with backend._launched_lock:
        state = backend._launched[token]
        state.observation = _KernelObservation(
            captured_at=1.0,
            memory_peak_bytes=memory,
            cpu_total_seconds=cpu,
            process_peak=proc,
            final_read_ok_memory=final_read_ok_memory,
            final_read_ok_cpu=final_read_ok_cpu,
            final_read_ok_pids=final_read_ok_pids,
        )
        state.capture_done.set()


# ── exit-watcher capture: the deployed clean-success receipt defect ────


def test_finish_uses_exit_capture_when_cgroup_vanished():
    """THE shipped clean-success defect: the contained process exits and the
    transient scope's cgroup is collected by systemd before ``finish`` runs,
    so the finish-time counter read is empty. The exit-watcher's immutable
    observation (captured while the scope was alive) must still publish
    non-null KERNEL memory/CPU/process peaks — never null with unavailable
    provenance despite guaranteed capabilities."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(backend, {})  # cgroup gone: no finish-time counters
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "exit 0")))
    _set_exit_observation(backend, running.token, memory=2_097_152, cpu=1.25, proc=5)
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.memory_peak_bytes == 2_097_152
    assert receipt.memory_peak_provenance is MeasurementProvenance.KERNEL
    assert receipt.cpu_total_seconds == 1.25
    assert receipt.cpu_total_provenance is MeasurementProvenance.KERNEL
    assert receipt.process_peak == 5
    assert receipt.process_peak_provenance is MeasurementProvenance.KERNEL


def test_finish_merges_exit_capture_with_finish_read_as_max():
    """The monotonic kernel counters merge the exit-watcher's capture and
    the finish-time pre-stop read as the max; both are genuine KERNEL reads
    and neither ``pids.current`` nor a sampled value is relabeled."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(
        backend,
        {
            "memory.peak": "4194304",  # finish read higher for memory
            "pids.peak": "3",  # finish read lower for pids
            "cpu.stat": "usage_usec 500000\n",  # finish read lower for cpu
            "pids.current": "3",
        },
    )
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "exit 0")))
    _set_exit_observation(backend, running.token, memory=2_097_152, cpu=2.0, proc=7)
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.memory_peak_bytes == 4_194_304  # max of both KERNEL reads
    assert receipt.cpu_total_seconds == 2.0  # exit capture cpu higher
    assert receipt.process_peak == 7  # exit capture pids.peak higher
    assert receipt.memory_peak_provenance is MeasurementProvenance.KERNEL
    assert receipt.cpu_total_provenance is MeasurementProvenance.KERNEL
    assert receipt.process_peak_provenance is MeasurementProvenance.KERNEL


def test_finish_no_exit_capture_and_vanished_cgroup_is_unavailable():
    """A vanished cgroup with NO exit capture and NO samples is never
    fabricated: memory/CPU/process stay UNAVAILABLE (None) — the missing
    cgroup cannot short-circuit to a fake kernel value."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "not-found"))
    _install_fake_read_file(backend, {})  # cgroup gone; no capture either
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "exit 0")))
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.memory_peak_bytes is None
    assert receipt.memory_peak_provenance is MeasurementProvenance.UNAVAILABLE
    assert receipt.cpu_total_seconds is None
    assert receipt.cpu_total_provenance is MeasurementProvenance.UNAVAILABLE
    assert receipt.process_peak is None
    assert receipt.process_peak_provenance is MeasurementProvenance.UNAVAILABLE


def test_finish_vanished_cgroup_clean_with_explicit_evidence():
    """A vanished cgroup corroborated by a positively-terminal unit state is
    genuine emptiness (the scope was collected after the tree exited) — it
    yields CLEAN/quiescent but ONLY with explicit ``cgroup_vanished``
    evidence, never a silently-verified clean."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "not-found"))
    _install_fake_read_file(backend, {})
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "exit 0")))
    _set_exit_observation(backend, running.token, memory=1000, cpu=0.1, proc=2)
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.cleanup_status is CleanupStatus.CLEAN
    assert receipt.quiescent is True
    assert "cgroup_vanished" in receipt.enforcement_events
    assert "cgroup_procs_unreadable" not in receipt.enforcement_events
    assert receipt.memory_peak_provenance is MeasurementProvenance.KERNEL


def test_finish_vanished_cgroup_unknown_unit_state_fails_closed():
    """A vanished cgroup with an UNKNOWN unit state never short-circuits to
    falsely clean/quiescent evidence: the receipt stays INCOMPLETE with the
    vanish recorded (fail-closed)."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")

    def script(argv):
        if "is-active" in argv:
            return (1, "Failed to connect to bus")  # UNKNOWN unit state
        return (0, "")

    _install_fake_run(backend, script)
    _install_fake_read_file(backend, {})
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "exit 0")))
    _set_exit_observation(backend, running.token, memory=1000, cpu=0.1, proc=2)
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.cleanup_status is CleanupStatus.INCOMPLETE
    assert receipt.quiescent is False
    assert "cgroup_vanished" in receipt.enforcement_events


def test_exit_watcher_captures_live_and_final_values():
    """Deterministic watcher proof: a short-lived process exits; the
    watcher's live ``pread`` polls carry the kernel counters while the
    cgroup is valid and the immutable observation lands on the launch state
    (non-null) after exit — the exact capture-before-teardown seam."""
    backend = LinuxSystemdBackend()

    def systemd_run_scope(unit, slice_name, argv, **kwargs):
        proc = subprocess.Popen(
            ["sh", "-c", "sleep 0.2"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _FAKE_PROCESSES.append(proc)
        return proc

    backend._systemd_run_scope = systemd_run_scope  # type: ignore[method-assign]
    backend._wait_for_cgroup = lambda unit, proc, timeout=None: "/cg"  # type: ignore[method-assign]
    backend._session_limits_applied = lambda cg_path, policy: (True, "envelope-applied")  # type: ignore[method-assign]
    reads = []
    # Real fds (from /dev/null) so the watcher's close of the "opened"
    # counter files never touches pytest's stdio descriptors.
    _devnull_fds = [os.open(os.devnull, os.O_RDONLY) for _ in range(3)]

    def fake_open(cg):
        return {
            "memory.peak": _devnull_fds[0],
            "cpu.stat": _devnull_fds[1],
            "pids.peak": _devnull_fds[2],
        }

    def fake_pread(fds):
        # deterministic GC simulation: counters vanish once the process exits
        alive = any(p.poll() is None for p in _FAKE_PROCESSES)
        if not alive:
            return {"memory.peak": None, "cpu.stat": None, "pids.peak": None}
        reads.append(1)
        return {"memory.peak": 2_097_152, "cpu.stat": 0.25, "pids.peak": 4}

    backend._open_observation_fds = fake_open  # type: ignore[method-assign]
    backend._pread_observation = fake_pread  # type: ignore[method-assign]
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "sleep 0.2")))
    state = backend._launched[running.token]
    assert state.capture_done.wait(timeout=5)
    assert state.observation is not None
    assert state.observation.memory_peak_bytes == 2_097_152
    assert state.observation.cpu_total_seconds == 0.25
    assert state.observation.process_peak == 4
    assert len(reads) >= 2  # live polls happened while the process ran


def test_await_exit_capture_waits_bounded_for_observation():
    """``finish`` gives the exit-watcher a bounded moment to record its
    observation when the process has already exited, then uses it."""
    backend = LinuxSystemdBackend()
    proc = subprocess.Popen(
        ["true"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    _FAKE_PROCESSES.append(proc)
    assert proc.wait(timeout=5) == 0  # ensure the process has exited
    state = _LaunchState(unit="u", cgroup="/cg", root_pid=proc.pid, started_at=0.0)

    def set_later():
        time.sleep(0.05)
        with backend._launched_lock:
            state.observation = _KernelObservation(
                captured_at=1.0, memory_peak_bytes=5, cpu_total_seconds=0.1, process_peak=2,
                final_read_ok_memory=True, final_read_ok_cpu=True, final_read_ok_pids=True,
            )
            state.capture_done.set()

    threading.Thread(target=set_later).start()
    running = RunningHandle(
        backend="x", token="u", request_id="r", root_pid=proc.pid,
        start_identity="i", process=proc,
    )
    obs = backend._await_exit_capture(state, running)
    assert obs is not None
    assert obs.memory_peak_bytes == 5
    assert obs.process_peak == 2


def test_sample_reads_kernel_peak_counter():
    """The live sampler reads the kernel ``memory.peak`` high-water counter
    (not the instantaneous ``memory.current``) while the scope is valid."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_read_file(
        backend,
        {
            "memory.current": "1048576",
            "memory.peak": "2097152",
            "pids.current": "3",
            "pids.peak": "7",
        },
    )
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "60")))
    sample = backend.sample(running)
    assert sample.memory_peak_bytes == 2_097_152  # the kernel peak, not current
    assert sample.provenance is MeasurementProvenance.KERNEL


def test_watcher_capture_failure_is_contained():
    """A watcher failure records no observation and never breaks ``finish``:
    the receipt degrades to the honest fallback chain."""
    backend = LinuxSystemdBackend()

    def systemd_run_scope(unit, slice_name, argv, **kwargs):
        proc = subprocess.Popen(
            ["sleep", "1"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        _FAKE_PROCESSES.append(proc)
        return proc

    backend._systemd_run_scope = systemd_run_scope  # type: ignore[method-assign]
    backend._wait_for_cgroup = lambda unit, proc, timeout=None: "/cg"  # type: ignore[method-assign]
    backend._session_limits_applied = lambda cg_path, policy: (True, "envelope-applied")  # type: ignore[method-assign]

    def boom(cg):
        raise RuntimeError("watcher boom")

    backend._open_observation_fds = boom  # type: ignore[method-assign]
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "1")))
    state = backend._launched[running.token]
    time.sleep(0.2)
    assert state.observation is None  # contained: nothing recorded
    _install_fake_run(backend, lambda argv: (0, "not-found"))
    _install_fake_read_file(backend, {})
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt is not None
    assert receipt.memory_peak_provenance is MeasurementProvenance.UNAVAILABLE


def test_open_observation_fds_absent_pids_peak_keeps_memory_cpu(tmp_path):
    """Finding-1 fix: the three kernel counter files open **independently**.
    An absent old-kernel ``pids.peak`` disables only that counter — the
    guaranteed ``memory.peak`` / ``cpu.stat`` fds must stay open — and a
    fully-gone cgroup yields an empty map (honest no-capture), never an
    all-or-nothing discard of the valid descriptors."""
    root = tmp_path / "cgroot"
    (root / "cg").mkdir(parents=True)
    (root / "cg" / "memory.peak").write_text("0")
    (root / "cg" / "cpu.stat").write_text("usage_usec 0")
    backend = LinuxSystemdBackend(cgroup_root=root)
    fds = backend._open_observation_fds("/cg")
    assert set(fds) == {"memory.peak", "cpu.stat"}  # pids.peak absent: only it
    for fd in fds.values():
        os.close(fd)
    # cgroup entirely gone: empty map -> the watcher records nothing.
    empty = LinuxSystemdBackend(cgroup_root=tmp_path / "empty")
    assert empty._open_observation_fds("/cg") == {}


def test_exit_watcher_old_kernel_shape_captures_memory_cpu_natural_exit():
    """Finding-1 end-to-end through a natural clean exit: on the old-kernel
    shape (``pids.peak`` absent, memory/cpu guaranteed) the watcher must
    still capture non-null memory/CPU at the exit instant — the missing
    optional counter never discards the guaranteed capture — while
    ``process_peak`` stays honestly None (capability-aware provenance)."""
    backend = LinuxSystemdBackend()

    def systemd_run_scope(unit, slice_name, argv, **kwargs):
        proc = subprocess.Popen(
            ["sh", "-c", "sleep 0.2"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _FAKE_PROCESSES.append(proc)
        return proc

    backend._systemd_run_scope = systemd_run_scope  # type: ignore[method-assign]
    backend._wait_for_cgroup = lambda unit, proc, timeout=None: "/cg"  # type: ignore[method-assign]
    backend._session_limits_applied = lambda cg_path, policy: (True, "envelope-applied")  # type: ignore[method-assign]
    _devnull_fds = [os.open(os.devnull, os.O_RDONLY) for _ in range(2)]

    def fake_open(cg):
        # Old-kernel shape: memory.peak + cpu.stat only (no pids.peak).
        return {"memory.peak": _devnull_fds[0], "cpu.stat": _devnull_fds[1]}

    def fake_pread(fds):
        # The exit-instant read succeeds (the cgroup files are still valid
        # for it) and returns the authoritative values.
        return {"memory.peak": 2_097_152, "cpu.stat": 0.25}

    backend._open_observation_fds = fake_open  # type: ignore[method-assign]
    backend._pread_observation = fake_pread  # type: ignore[method-assign]
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "sleep 0.2")))
    state = backend._launched[running.token]
    assert state.capture_done.wait(timeout=5)
    assert state.observation is not None
    assert state.observation.memory_peak_bytes == 2_097_152
    assert state.observation.cpu_total_seconds == 0.25
    assert state.observation.process_peak is None  # no pids.peak: honest
    assert state.observation.final_read_ok_memory is True
    assert state.observation.final_read_ok_cpu is True
    assert state.observation.final_read_ok_pids is False  # absent counter


def test_exit_watcher_final_read_captures_terminal_window_growth():
    """Finding-2 deterministic proof on the watcher seam: terminal-window
    CPU/peak growth — forced to start strictly AFTER a recorded live read
    and end at the process exit, so no live read can observe it — must be
    captured by the exit-instant read (the deterministic pidfd exit
    notification) and carried into the observation. The receipt-includes-it
    end-to-end proof is the real-Linux integration test."""
    backend = LinuxSystemdBackend()
    burning = threading.Event()
    live_reads: list[float] = []

    def systemd_run_scope(unit, slice_name, argv, **kwargs):
        proc = subprocess.Popen(
            ["sh", "-c", "sleep 30"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _FAKE_PROCESSES.append(proc)
        return proc

    backend._systemd_run_scope = systemd_run_scope  # type: ignore[method-assign]
    backend._wait_for_cgroup = lambda unit, proc, timeout=None: "/cg"  # type: ignore[method-assign]
    backend._session_limits_applied = lambda cg_path, policy: (True, "envelope-applied")  # type: ignore[method-assign]
    _devnull_fds = [os.open(os.devnull, os.O_RDONLY) for _ in range(3)]

    def fake_open(cg):
        return {
            "memory.peak": _devnull_fds[0],
            "cpu.stat": _devnull_fds[1],
            "pids.peak": _devnull_fds[2],
        }

    def fake_pread(fds):
        if not burning.is_set():
            live_reads.append(time.monotonic())
            return {"memory.peak": 1_048_576, "cpu.stat": 0.01, "pids.peak": 2}
        # Terminal-window growth: counter values jump right before exit.
        return {"memory.peak": 4_194_304, "cpu.stat": 0.31, "pids.peak": 5}

    backend._open_observation_fds = fake_open  # type: ignore[method-assign]
    backend._pread_observation = fake_pread  # type: ignore[method-assign]
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "sleep 30")))
    state = backend._launched[running.token]
    # Wait for at least two live reads, then force the terminal-window
    # growth (burn) and immediately terminate the process: the burn is
    # strictly after the last live read and before exit, so only the
    # exit-instant read can capture it.
    deadline = time.monotonic() + 5
    while len(live_reads) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(live_reads) >= 2
    last_live = live_reads[-1]
    burning.set()
    time.sleep(0.01)
    _FAKE_PROCESSES[-1].terminate()
    assert state.capture_done.wait(timeout=5)
    assert state.observation is not None
    assert state.observation.final_read_ok_memory is True
    assert state.observation.final_read_ok_cpu is True
    assert state.observation.final_read_ok_pids is True
    # The terminal-window growth is in the observation (memory/CPU/pids).
    assert state.observation.memory_peak_bytes == 4_194_304
    assert state.observation.cpu_total_seconds == 0.31
    assert state.observation.process_peak == 5


def test_exit_watcher_falls_back_to_waitid_when_pidfd_unavailable(monkeypatch):
    """The deterministic exit notification falls back to ``waitid(WNOWAIT)``
    when ``pidfd_open`` is unavailable — still a wake at the exit instant
    (no polling cadence), and the capture still lands on natural exit."""
    backend = LinuxSystemdBackend()

    def systemd_run_scope(unit, slice_name, argv, **kwargs):
        proc = subprocess.Popen(
            ["sh", "-c", "sleep 0.2"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _FAKE_PROCESSES.append(proc)
        return proc

    backend._systemd_run_scope = systemd_run_scope  # type: ignore[method-assign]
    backend._wait_for_cgroup = lambda unit, proc, timeout=None: "/cg"  # type: ignore[method-assign]
    backend._session_limits_applied = lambda cg_path, policy: (True, "envelope-applied")  # type: ignore[method-assign]
    _devnull_fds = [os.open(os.devnull, os.O_RDONLY) for _ in range(3)]

    def fake_open(cg):
        return {
            "memory.peak": _devnull_fds[0],
            "cpu.stat": _devnull_fds[1],
            "pids.peak": _devnull_fds[2],
        }

    def fake_pread(fds):
        return {"memory.peak": 2_097_152, "cpu.stat": 0.25, "pids.peak": 4}

    def no_pidfd(pid):
        raise OSError(38, "function not implemented")

    monkeypatch.setattr(os, "pidfd_open", no_pidfd)
    backend._open_observation_fds = fake_open  # type: ignore[method-assign]
    backend._pread_observation = fake_pread  # type: ignore[method-assign]
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "sleep 0.2")))
    state = backend._launched[running.token]
    assert state.capture_done.wait(timeout=5)
    assert state.observation is not None
    assert state.observation.memory_peak_bytes == 2_097_152
    assert state.observation.cpu_total_seconds == 0.25
    assert state.observation.final_read_ok_memory is True
    assert state.observation.final_read_ok_cpu is True
    assert state.observation.final_read_ok_pids is True


def test_finish_records_capture_final_read_lost_when_last_live_fallback():
    """When every exit-instant final read lost the collection race, each
    retained last-live value is downgraded to honest non-KERNEL provenance
    and the receipt records a precise per-counter ``capture_final_read_lost``
    event naming every affected counter — never silently labeling the
    possibly-stale last-live reads as the authoritative final totals/peaks."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(backend, {})  # cgroup gone: no finish-time read
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "exit 0")))
    _set_exit_observation(
        backend, running.token, memory=1000, cpu=0.1, proc=2,
        final_read_ok_memory=False, final_read_ok_cpu=False, final_read_ok_pids=False,
    )
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert "capture_final_read_lost:memory.peak,cpu.stat,pids.peak" in receipt.enforcement_events
    # The retained last-live reads are genuine KERNEL-sourced but are never
    # the authoritative final total/peak: they are conservatively downgraded.
    assert receipt.memory_peak_bytes == 1000
    assert receipt.memory_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt.cpu_total_seconds == 0.1
    assert receipt.cpu_total_provenance is MeasurementProvenance.SAMPLED
    assert receipt.process_peak == 2
    assert receipt.process_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt.memory_peak_provenance is not MeasurementProvenance.KERNEL
    assert receipt.cpu_total_provenance is not MeasurementProvenance.KERNEL
    assert receipt.process_peak_provenance is not MeasurementProvenance.KERNEL


def test_finish_partial_final_read_memory_ok_pids_lost_never_publishes_stale_pids_kernel():
    """TASK-5953's exact adversarial shape: the exit-instant final read
    succeeds for memory/CPU but FAILS for ``pids.peak`` (systemd's
    collection raced the third pread). The retained last-live process peak
    must NOT be silently published as the authoritative KERNEL final merely
    because memory/CPU succeeded — it is downgraded to SAMPLED provenance
    with a precise per-counter ``capture_final_read_lost:pids.peak`` event,
    while the valid memory/CPU counters stay authoritative KERNEL."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(backend, {})  # cgroup gone: no finish-time read
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "exit 0")))
    _set_exit_observation(
        backend, running.token, memory=2_097_152, cpu=0.25, proc=2,
        final_read_ok_memory=True, final_read_ok_cpu=True, final_read_ok_pids=False,
    )
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    # Valid counters remain usable with authoritative KERNEL provenance.
    assert receipt.memory_peak_bytes == 2_097_152
    assert receipt.memory_peak_provenance is MeasurementProvenance.KERNEL
    assert receipt.cpu_total_seconds == 0.25
    assert receipt.cpu_total_provenance is MeasurementProvenance.KERNEL
    # The stale last-live pids.peak is never overstated as KERNEL: the value
    # is preserved under honest SAMPLED provenance and the loss is named
    # precisely per counter (and only for the affected counter).
    assert receipt.process_peak == 2
    assert receipt.process_peak_provenance is MeasurementProvenance.SAMPLED
    assert set(receipt.enforcement_events) == {
        "cgroup_vanished",
        "capture_final_read_lost:pids.peak",
    }


def test_finish_partial_final_read_pids_ok_cpu_lost_downgrades_cpu_only():
    """The adversarial mirror: the exit-instant final read succeeds for
    memory/pids but FAILS for ``cpu.stat``. Only the affected CPU counter
    is downgraded (SAMPLED + ``capture_final_read_lost:cpu.stat``); the
    valid memory/pids counters stay authoritative KERNEL — a failed final
    read for one counter never drags down (or masks) the others."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(backend, {})  # cgroup gone: no finish-time read
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "exit 0")))
    _set_exit_observation(
        backend, running.token, memory=2_097_152, cpu=0.25, proc=5,
        final_read_ok_memory=True, final_read_ok_cpu=False, final_read_ok_pids=True,
    )
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert receipt.memory_peak_bytes == 2_097_152
    assert receipt.memory_peak_provenance is MeasurementProvenance.KERNEL
    assert receipt.process_peak == 5
    assert receipt.process_peak_provenance is MeasurementProvenance.KERNEL
    assert receipt.cpu_total_seconds == 0.25
    assert receipt.cpu_total_provenance is MeasurementProvenance.SAMPLED
    assert receipt.cpu_total_provenance is not MeasurementProvenance.KERNEL
    assert set(receipt.enforcement_events) == {
        "cgroup_vanished",
        "capture_final_read_lost:cpu.stat",
    }


def test_finish_partial_final_read_no_successful_final_read_downgrades_all():
    """No successful final read at all: every retained last-live value is
    conservatively downgraded (SAMPLED, never KERNEL) and the single event
    names all three affected counters — the receipt never fabricates an
    authoritative final total/peak out of possibly-stale last-live reads."""
    backend = LinuxSystemdBackend()
    _fake_launch_ok(backend, cg="/cg")
    _install_fake_run(backend, lambda argv: (0, "inactive"))
    _install_fake_read_file(backend, {})  # cgroup gone: no finish-time read
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "exit 0")))
    _set_exit_observation(
        backend, running.token, memory=1000, cpu=0.1, proc=2,
        final_read_ok_memory=False, final_read_ok_cpu=False, final_read_ok_pids=False,
    )
    receipt = backend.finish(running, "success", grace_seconds=0.1)
    assert set(receipt.enforcement_events) == {
        "cgroup_vanished",
        "capture_final_read_lost:memory.peak,cpu.stat,pids.peak",
    }
    assert receipt.memory_peak_bytes == 1000
    assert receipt.memory_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt.cpu_total_seconds == 0.1
    assert receipt.cpu_total_provenance is MeasurementProvenance.SAMPLED
    assert receipt.process_peak == 2
    assert receipt.process_peak_provenance is MeasurementProvenance.SAMPLED
    assert (
        receipt.memory_peak_provenance
        is receipt.cpu_total_provenance
        is receipt.process_peak_provenance
        is not MeasurementProvenance.KERNEL
    )


def test_exit_watcher_partial_final_read_records_per_counter_validity():
    """Watcher-seam proof: the exit-instant read loses the collection race
    for ONLY ``pids.peak`` (memory/CPU finals succeed). The observation
    must record per-counter validity — memory/CPU ``final_read_ok_*`` True,
    pids False with the last-live ``process_peak`` retained — so ``finish``
    downgrades exactly the affected counter."""
    backend = LinuxSystemdBackend()

    def systemd_run_scope(unit, slice_name, argv, **kwargs):
        proc = subprocess.Popen(
            ["sh", "-c", "sleep 0.2"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _FAKE_PROCESSES.append(proc)
        return proc

    backend._systemd_run_scope = systemd_run_scope  # type: ignore[method-assign]
    backend._wait_for_cgroup = lambda unit, proc, timeout=None: "/cg"  # type: ignore[method-assign]
    backend._session_limits_applied = lambda cg_path, policy: (True, "envelope-applied")  # type: ignore[method-assign]
    _devnull_fds = [os.open(os.devnull, os.O_RDONLY) for _ in range(3)]

    def fake_open(cg):
        return {
            "memory.peak": _devnull_fds[0],
            "cpu.stat": _devnull_fds[1],
            "pids.peak": _devnull_fds[2],
        }

    def fake_pread(fds):
        alive = any(p.poll() is None for p in _FAKE_PROCESSES)
        if not alive:
            # The cgroup collection races ONLY the pids.peak pread.
            return {"memory.peak": 2_097_152, "cpu.stat": 0.25, "pids.peak": None}
        return {"memory.peak": 2_097_152, "cpu.stat": 0.25, "pids.peak": 2}

    backend._open_observation_fds = fake_open  # type: ignore[method-assign]
    backend._pread_observation = fake_pread  # type: ignore[method-assign]
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sh", "-c", "sleep 0.2")))
    state = backend._launched[running.token]
    assert state.capture_done.wait(timeout=5)
    assert state.observation is not None
    assert state.observation.memory_peak_bytes == 2_097_152
    assert state.observation.final_read_ok_memory is True
    assert state.observation.cpu_total_seconds == 0.25
    assert state.observation.final_read_ok_cpu is True
    # pids.peak final read lost the race: the last-live value is retained
    # but marked NOT final-read-ok so finish never labels it authoritative.
    assert state.observation.process_peak == 2
    assert state.observation.final_read_ok_pids is False


def test_probe_process_peak_capability_tracks_pids_peak():
    """The probe declares REPORTS_PROCESS_PEAK guaranteed only when the
    kernel exposes pids.peak; an old kernel (live-count fallback only) keeps
    the capability best_effort."""
    backend = LinuxSystemdBackend()

    class _FakeProc:
        pid = 9999

        def terminate(self) -> None:
            pass

        def wait(self, timeout=None):
            return 0

        def poll(self):
            return None

    backend._systemd_run_scope = lambda *a, **k: _FakeProc()
    backend._wait_for_cgroup = lambda unit, proc, timeout=None: "/cg"
    backend._session_limits_applied = lambda cg_path, policy: (True, "envelope-applied")  # type: ignore[method-assign]
    backend._applied_limits = lambda cg: {
        "memory.max": True, "pids.max": True, "cpu.max": True,
    }
    backend._cgroup_members = lambda cg: {1: "x", 2: "y"}
    backend._teardown_and_verify = lambda unit, cg, grace: (True, "cgroup-empty")
    backend._cleanup_probe_slice = lambda slice_name: None
    backend._systemctl = lambda *a, **k: (0, "running")
    backend._cgroup_v2_controllers = lambda: {"memory", "pids", "cpu"}

    # Kernel exposes pids.peak -> authoritative process peak capability.
    backend._read_counters = lambda cg: {
        "pids.peak": 2, "pids.current": 1, "memory.peak": 1, "cpu.stat": 0.1,
    }
    report = backend.probe()
    assert (
        report.level(Capability.REPORTS_PROCESS_PEAK) is CapabilityLevel.GUARANTEED
    )

    # Old kernel without pids.peak -> best-effort live-count fallback.
    backend._read_counters = lambda cg: {
        "pids.current": 1, "memory.peak": 1, "cpu.stat": 0.1,
    }
    report = backend.probe()
    assert (
        report.level(Capability.REPORTS_PROCESS_PEAK) is CapabilityLevel.BEST_EFFORT
    )


# ── abandon / recover ────────────────────────────────────────────────


def test_abandon_stops_partial_scope():
    backend = LinuxSystemdBackend()
    stopped = []

    def script(argv):
        if "stop" in argv:
            stopped.append(argv)
        return (0, "")

    _install_fake_run(backend, script)
    pending = backend.prepare(_request(), _policy())
    backend.abandon(pending)
    assert any("stop" in a for a in stopped)


def test_recover_is_observational_only():
    backend = LinuxSystemdBackend()

    def script(argv):
        if "list-units" in argv:
            return (0, "happyranch-session-sched-1-abc.scope  loaded active running\n")
        return (0, "")

    _install_fake_run(backend, script)
    result = backend.recover("token")
    assert result.recovered is False
    assert "happyranch-session-sched-1-abc.scope" in result.evidence


def test_backend_is_session_backend():
    assert isinstance(LinuxSystemdBackend(), object)
    from runtime.platform.session_backend import SessionBackend

    assert isinstance(LinuxSystemdBackend(), SessionBackend)


# ── real integration (gated on the operational probe) ────────────────

real_integration = pytest.mark.integration


def _require_systemd():
    backend = LinuxSystemdBackend()
    report = backend.probe()
    if not report.healthy:
        pytest.skip(
            f"Linux systemd/cgroup-v2 not usable on this runner: "
            f"{report.reason or report.evidence}"
        )
    return backend, report


@pytest.fixture(scope="module")
def real_backend():
    backend, report = _require_systemd()
    return backend


@real_integration
def test_probe_real_creates_and_removes_scope_no_residue(real_backend):
    backend, report = _require_systemd()
    assert report.healthy is True
    assert report.level(Capability.LIMITS_MEMORY) is CapabilityLevel.GUARANTEED
    assert report.level(Capability.LIMITS_PIDS) is CapabilityLevel.GUARANTEED
    assert report.level(Capability.LIMITS_CPU) is CapabilityLevel.GUARANTEED
    assert report.level(Capability.KILLS_TREE_GUARANTEED) is CapabilityLevel.GUARANTEED
    assert report.level(Capability.REPORTS_MEMORY_PEAK) is CapabilityLevel.GUARANTEED
    assert report.level(Capability.REPORTS_CPU_TOTAL) is CapabilityLevel.GUARANTEED
    # Probe left no residue: no probe scope units remain.
    code, out = backend._systemctl(
        "list-units", "--all", "--type=scope", "--plain", "--no-legend"
    )
    assert "happyranch-probe-" not in out


@real_integration
def _launch_sleep(backend, argv=("sleep", "60")) -> RunningHandle:
    pending = backend.prepare(_request(), _policy())
    spec = LaunchSpec(argv=argv)
    return backend.launch(pending, spec)


@real_integration
def test_launch_applies_task_enforcement_envelope_real(real_backend):
    """Slice C real enforcement: a task session scope applies the exact
    founder-approved envelope — MemoryHigh=14G / MemoryMax=24G /
    TasksMax=1024 in the cgroup files — and the probe-only CPUQuota value
    is never applied to a real session."""
    from runtime.orchestrator.host_supervisor import AdmissionRequest

    backend = real_backend
    request = AdmissionRequest(
        org="test", invocation_kind="task", logical_id="task-real-1",
        executor_profile="claude",
    )
    pending = backend.prepare(request, _policy())
    running = backend.launch(pending, LaunchSpec(argv=("sleep", "60")))
    try:
        assert running.invocation_kind == "task"
        assert running.executor_profile == "claude"
        cg = backend._proc_cgroup(running.root_pid)
        assert cg is not None
        assert backend._read_file(cg, "memory.high") == str(14 * 1024**3)
        assert backend._read_file(cg, "memory.max") == str(24 * 1024**3)
        assert backend._read_file(cg, "pids.max") == "1024"
        # The probe-only CPUQuota (10% -> "10000 100000") must never land on
        # a real session scope: cpu.max stays inherited ("max"/slice value)
        # or is absent when the cpu controller is not enabled on this
        # subtree — either way it is never the probe quota value.
        cpu_max = backend._read_file(cg, "cpu.max")
        if cpu_max is not None:
            assert cpu_max.split()[0] != "10000"
    finally:
        receipt = backend.finish(running, "success", grace_seconds=3.0)
        assert receipt.invocation_kind == "task"
        assert receipt.executor_profile == "claude"
        assert receipt.cleanup_status is CleanupStatus.CLEAN


@real_integration
def test_launch_applies_light_enforcement_envelope_real(real_backend):
    """Slice C real enforcement: thread/dream/wake/schedule sessions apply
    the light envelope — MemoryHigh=2G / MemoryMax=4G (exactly) /
    TasksMax=1024 — verified on a real scope."""
    from runtime.orchestrator.host_supervisor import AdmissionRequest

    backend = real_backend
    for kind in ("thread", "dream", "wake", "schedule"):
        request = AdmissionRequest(
            org="test", invocation_kind=kind, logical_id=f"{kind}-real-1",
            executor_profile="pi",
        )
        pending = backend.prepare(request, _policy())
        running = backend.launch(pending, LaunchSpec(argv=("sleep", "60")))
        try:
            assert running.invocation_kind == kind
            cg = backend._proc_cgroup(running.root_pid)
            assert cg is not None
            assert backend._read_file(cg, "memory.high") == str(2 * 1024**3), kind
            assert backend._read_file(cg, "memory.max") == str(4 * 1024**3), kind
            assert backend._read_file(cg, "pids.max") == "1024", kind
        finally:
            receipt = backend.finish(running, "success", grace_seconds=3.0)
            assert receipt.invocation_kind == kind
            assert receipt.executor_profile == "pi"
            assert receipt.cleanup_status is CleanupStatus.CLEAN


@real_integration
def test_launch_into_scope_real(real_backend):
    running = _launch_sleep(real_backend)
    try:
        assert running.root_pid > 0
        assert running.start_identity  # identity-safe root
        cg = real_backend._proc_cgroup(running.root_pid)
        assert cg is not None and running.token in cg
        assert real_backend._unit_active(running.token)
    finally:
        real_backend.finish(running, "success", grace_seconds=2.0)


@real_integration
def test_finish_clean_success_explicit_stop_real(real_backend):
    running = _launch_sleep(real_backend)
    receipt = real_backend.finish(running, "success", grace_seconds=2.0)
    assert receipt.cleanup_status is CleanupStatus.CLEAN
    assert receipt.quiescent is True
    assert receipt.survivors == ()
    assert not real_backend._unit_active(running.token)


@real_integration
def test_finish_clean_success_with_surviving_descendant_real(real_backend):
    """MANDATORY success-path descendant cleanup: the parent exits 0 while a
    descendant sleeps inside the scope — finish must explicitly stop the
    whole scope, kill the descendant, and verify cgroup emptiness."""
    pending = real_backend.prepare(_request(), _policy())
    running = real_backend.launch(
        pending, LaunchSpec(argv=("sh", "-c", "sleep 60 & sleep 0.2"))
    )
    # Wait for the parent to exit 0 while the descendant stays in the scope.
    deadline = time.monotonic() + 5
    while running.process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert running.process.returncode == 0  # clean success
    receipt = real_backend.finish(running, "success", grace_seconds=3.0)
    assert receipt.cleanup_status is CleanupStatus.CLEAN
    assert receipt.quiescent is True
    assert receipt.survivors == ()
    # The scope is inactive and its cgroup is gone/empty.
    assert not real_backend._unit_active(running.token)


@real_integration
def test_finish_nonzero_preserves_primary_reason_real(real_backend):
    """A nonzero target exit preserves the primary reason through finish.
    The target lives long enough for launch to verify the applied
    enforcement envelope (Slice C — an instant exit fails closed at launch
    and is covered by the deterministic fast-exit unit tests); the finish
    receipt carries the primary reason with a CLEAN teardown."""
    pending = real_backend.prepare(_request(), _policy())
    running = real_backend.launch(
        pending, LaunchSpec(argv=("sh", "-c", "sleep 60 & sleep 0.5; exit 3"))
    )
    deadline = time.monotonic() + 5
    while running.process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert running.process.returncode == 3
    receipt = real_backend.finish(running, "failure", grace_seconds=2.0)
    assert receipt.terminal_reason == "failure"  # primary reason preserved
    assert receipt.cleanup_status is CleanupStatus.CLEAN


@real_integration
def test_finish_escalates_to_kill_real(real_backend):
    """A TERM-resistant descendant forces the KILL escalation."""
    pending = real_backend.prepare(_request(), _policy())
    term_ignore = (
        "import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
    )
    running = real_backend.launch(
        pending,
        LaunchSpec(argv=("sh", "-c", f"python3 -c '{term_ignore}' & sleep 0.2")),
    )
    deadline = time.monotonic() + 5
    while running.process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    receipt = real_backend.finish(running, "success", grace_seconds=1.5)
    # Either KILL escalation reached quiescence or the daemon's stop policy
    # already killed the TERM-resistant member — both are quiescent.
    assert receipt.quiescent is True
    assert not real_backend._unit_active(running.token)


@real_integration
def test_finish_reads_authoritative_counters_real(real_backend):
    running = _launch_sleep(real_backend)
    try:
        time.sleep(0.5)  # let the scope accumulate some state
        cg = real_backend._proc_cgroup(running.root_pid)
        counters = real_backend._read_counters(cg) if cg else {}
        assert counters.get("memory.current") is not None
        assert counters.get("pids.current") == 1
        assert counters.get("cpu.stat") is not None
    finally:
        receipt = real_backend.finish(running, "success", grace_seconds=2.0)
    assert receipt.memory_peak_bytes is not None
    assert receipt.memory_peak_provenance is MeasurementProvenance.KERNEL
    assert receipt.cpu_total_seconds is not None
    assert receipt.cpu_total_provenance is MeasurementProvenance.KERNEL


@real_integration
def test_finish_clean_exit_receipt_non_null_kernel_real(real_backend):
    """THE deployed clean-success defect on the real host: the contained
    process exits naturally and systemd collects the transient scope before
    ``finish`` runs (live evidence: the cgroup directory can vanish within
    microseconds of the process exiting). The exit-watcher's capture while
    the scope was alive must still publish non-null KERNEL memory, CPU, and
    process peaks on the receipt — never null with unavailable provenance
    despite guaranteed capabilities."""
    running = _launch_sleep(real_backend, argv=("sh", "-c", "sleep 0.5"))
    out, err = running.process.communicate(timeout=10)  # wait/reap like the executor
    receipt = real_backend.finish(running, "success", grace_seconds=2.0)
    assert receipt.memory_peak_bytes is not None
    assert receipt.memory_peak_provenance is MeasurementProvenance.KERNEL
    assert receipt.cpu_total_seconds is not None
    assert receipt.cpu_total_provenance is MeasurementProvenance.KERNEL
    assert receipt.process_peak is not None
    assert receipt.process_peak_provenance is MeasurementProvenance.KERNEL
    assert receipt.cleanup_status is CleanupStatus.CLEAN
    assert receipt.quiescent is True


@real_integration
def test_exit_capture_cross_checked_against_systemd_accounting_real(real_backend):
    """Deterministic journal/kernel cross-check for the SAME scope: while
    the contained process runs, systemd's manager accounting (``systemctl
    show`` MemoryPeak/CPUUsageNSec — the systemd 'journal' view) and the raw
    kernel cgroup counters agree; after a natural exit the carried KERNEL
    receipt values are non-null and never below the live systemd accounting
    (the observation is complete, not a stale lower bound)."""
    running = _launch_sleep(
        real_backend,
        argv=(
            "python3",
            "-c",
            "import time\n"
            "x=[bytearray(1024*1024)]*48\n"
            "t=time.monotonic()\n"
            "while time.monotonic()-t < 2.0:\n"
            "    pass",
        ),
    )
    sysd_mem = sysd_cpu = None
    try:
        time.sleep(0.8)  # let the workload allocate memory and burn CPU
        code, out = real_backend._systemctl(
            "show", "-p", "MemoryPeak", "-p", "CPUUsageNSec", running.token
        )
        assert code == 0
        props = dict(
            line.split("=", 1) for line in out.splitlines() if "=" in line
        )
        assert "MemoryPeak" in props and "CPUUsageNSec" in props
        sysd_mem = int(props["MemoryPeak"])
        sysd_cpu = int(props["CPUUsageNSec"]) / 1_000_000_000.0
        cg = real_backend._proc_cgroup(running.root_pid)
        assert cg is not None
        counters = real_backend._read_counters(cg)
        kernel_mem = counters.get("memory.peak")
        kernel_cpu = counters.get("cpu.stat")
        assert kernel_mem is not None and kernel_cpu is not None
        # Two independent views of the same scope agree: systemd mirrors the
        # kernel accounting for scope units (at most a small lag).
        assert kernel_mem >= sysd_mem - max(1, int(kernel_mem * 0.05))
        assert kernel_cpu >= sysd_cpu - max(0.01, kernel_cpu * 0.1)
    finally:
        out, err = running.process.communicate(timeout=10)  # natural exit
        receipt = real_backend.finish(running, "success", grace_seconds=2.0)
    # The carried KERNEL observation is complete — never below the live
    # systemd accounting captured above.
    assert receipt.memory_peak_bytes is not None
    assert receipt.memory_peak_provenance is MeasurementProvenance.KERNEL
    assert receipt.cpu_total_seconds is not None
    assert receipt.cpu_total_provenance is MeasurementProvenance.KERNEL
    assert receipt.process_peak is not None
    assert receipt.process_peak_provenance is MeasurementProvenance.KERNEL
    assert sysd_mem is not None and sysd_cpu is not None
    assert receipt.memory_peak_bytes >= sysd_mem - max(1, int(sysd_mem * 0.05))
    assert receipt.cpu_total_seconds >= sysd_cpu - max(0.01, sysd_cpu * 0.1)


@real_integration
def test_abandon_prepared_never_launched_no_residue_real(real_backend):
    pending = real_backend.prepare(_request(), _policy())
    real_backend.abandon(pending)
    assert not real_backend._unit_active(pending.token)
    code, out = real_backend._systemctl(
        "list-units", "--all", "--type=scope", "--plain", "--no-legend"
    )
    assert pending.token not in out
