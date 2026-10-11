"""Existing real backend integration lane; unit cases retired by THR-291 seq40."""
from __future__ import annotations

import time

import pytest

from runtime.platform.linux_systemd import LinuxSystemdBackend
from runtime.platform.session_backend import (
    Capability, CapabilityLevel, CleanupStatus, LaunchSpec,
    MeasurementProvenance, RunningHandle,
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
