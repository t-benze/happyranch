"""Existing real backend integration lane; unit cases retired by THR-291 seq40."""
from __future__ import annotations

import os
import signal
import time

import pytest

from runtime.platform.macos_process_group import MacOSProcessGroupBackend
from runtime.platform.session_backend import CleanupStatus, LaunchSpec, MeasurementProvenance


def _policy():
    from runtime.orchestrator.host_supervisor import canary_policy

    return canary_policy()


def _request():
    from runtime.orchestrator.host_supervisor import AdmissionRequest

    return AdmissionRequest(
        org="test", invocation_kind="schedule", logical_id="sched-1",
        executor_profile="claude",
    )


# ── real POSIX integration (gated on the operational probe) ──────────

real_integration = pytest.mark.integration


def _require_ops():
    backend = MacOSProcessGroupBackend()
    report = backend.probe()
    if not report.healthy:
        pytest.skip(f"process-group/census ops unusable on this runner: {report.reason}")
    return backend


@pytest.fixture(scope="module")
def real_backend():
    return _require_ops()


def _spawn_and_wait(backend, argv, timeout=5.0):
    pending = backend.prepare(_request(), _policy())
    running = backend.launch(pending, LaunchSpec(argv=argv))
    deadline = time.monotonic() + timeout
    while running.process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    return running


@real_integration
def test_probe_real_creates_signals_reaps_group_no_residue(real_backend):
    backend = _require_ops()
    report = backend.probe()
    assert report.healthy is True
    from runtime.platform.session_backend import Capability, CapabilityLevel

    assert report.level(Capability.KILLS_TREE_BEST_EFFORT) is CapabilityLevel.BEST_EFFORT


@real_integration
def test_launch_creates_new_process_group_real(real_backend):
    running = _spawn_and_wait(real_backend, ("sh", "-c", "sleep 30"))
    try:
        assert os.getpgid(running.root_pid) == running.root_pid
        assert running.start_identity
    finally:
        real_backend.finish(running, "success", grace_seconds=2.0)


@real_integration
def test_finish_clean_success_cleans_group_real(real_backend):
    running = _spawn_and_wait(real_backend, ("sh", "-c", "sleep 30"))
    receipt = real_backend.finish(running, "success", grace_seconds=2.0)
    assert receipt.cleanup_status is CleanupStatus.TERM  # live group -> TERM
    assert receipt.quiescent is True
    assert receipt.survivors == ()


@real_integration
def test_finish_clean_success_with_surviving_descendant_real(real_backend):
    """MANDATORY success-path descendant cleanup: the parent exits 0 while a
    child keeps running in the group — finish must TERM/KILL the whole group."""
    pending = real_backend.prepare(_request(), _policy())
    running = real_backend.launch(
        pending, LaunchSpec(argv=("sh", "-c", "sleep 60 & sleep 0.2"))
    )
    deadline = time.monotonic() + 5
    while running.process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert running.process.returncode == 0
    receipt = real_backend.finish(running, "success", grace_seconds=2.0)
    assert receipt.cleanup_status is CleanupStatus.TERM
    assert receipt.quiescent is True
    assert receipt.survivors == ()


@real_integration
def test_escaped_descendant_is_best_effort_survivor_real(real_backend):
    """SHIPPING finish seam: a descendant that ``setsid``s away after launch
    (no sampler ever ran — only the launch-time snapshot, taken before the
    child existed) is censused by finish's OWN fresh final identity-safe
    descendant census. No manual pre-sample refresh.

    Documented best-effort limitation: the census must run while the root
    still lives — a descendant that escapes AND is reparented before finish
    (root already exited) is unobservable by any process-table walk."""
    pending = real_backend.prepare(_request(), _policy())
    running = real_backend.launch(
        pending, LaunchSpec(argv=("sh", "-c", "setsid sleep 60 & sleep 5"))
    )
    survivors = ()
    try:
        # The escaped child is spawned within the first instant; finish runs
        # while the root still lives so the fresh census can see the child.
        time.sleep(0.3)
        assert running.process.poll() is None  # root still alive
        receipt = real_backend.finish(running, "success", grace_seconds=1.0)
        survivors = receipt.survivors
        # The escaped child survives (new session) — best-effort truth:
        # censused survivor, never a fabricated clean claim.
        assert receipt.survivors, "escaped descendant must be censused by finish's own census"
        assert receipt.quiescent is False
        assert receipt.cleanup_status is not CleanupStatus.INCOMPLETE
    finally:
        # Tear down the escaped child by its identity-verified survivor pid
        # (its cmdline is just ``sleep 60`` — setsid exec'd sleep).
        for sv in survivors:
            try:
                os.kill(sv.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        os.system("pkill -9 -f '^sleep 60$' 2>/dev/null || true")


@real_integration
def test_sampler_identity_safety_real(real_backend):
    running = _spawn_and_wait(real_backend, ("sh", "-c", "sleep 30"))
    try:
        sample = real_backend.sampler()(running)
        assert sample.provenance is MeasurementProvenance.SAMPLED
        assert sample.process_count is not None and sample.process_count >= 1
        assert sample.memory_peak_bytes is not None
    finally:
        real_backend.finish(running, "success", grace_seconds=2.0)


@real_integration
def test_finish_merges_real_sampled_receipt(real_backend):
    running = _spawn_and_wait(real_backend, ("sh", "-c", "sleep 30"))
    try:
        sample = real_backend.sampler()(running)
        receipt = real_backend.finish(running, "success", grace_seconds=2.0, samples=(sample,))
    except Exception:
        running.process.kill()
        raise
    assert receipt.memory_peak_provenance is MeasurementProvenance.SAMPLED
    assert receipt.process_peak is not None and receipt.process_peak >= 1
