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
import time

import pytest

from runtime.platform.linux_systemd import LinuxSystemdBackend
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


def _fake_launch_ok(backend: LinuxSystemdBackend, *, cg: str) -> None:
    """Make ``launch`` succeed deterministically without a real scope."""

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

    backend._systemd_run_scope = systemd_run_scope  # type: ignore[method-assign]
    backend._wait_for_cgroup = wait_for_cgroup  # type: ignore[method-assign]
    backend._census.start_identity = start_identity  # type: ignore[method-assign]


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
    pending = real_backend.prepare(_request(), _policy())
    running = real_backend.launch(
        pending, LaunchSpec(argv=("sh", "-c", "exit 3"))
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
def test_abandon_prepared_never_launched_no_residue_real(real_backend):
    pending = real_backend.prepare(_request(), _policy())
    real_backend.abandon(pending)
    assert not real_backend._unit_active(pending.token)
    code, out = real_backend._systemctl(
        "list-units", "--all", "--type=scope", "--plain", "--no-legend"
    )
    assert pending.token not in out
