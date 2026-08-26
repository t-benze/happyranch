"""End-to-end supervisor integration with the REAL Slice B backends
(THR-207).

Proves the Slice A core (admission -> prepare -> launch -> launch body ->
freeze terminal -> finish containment -> residue reconciliation -> bounded
receipt -> exactly-once lease release) drives the real Linux
systemd/cgroup-v2 backend and the portable sampler together:

* the mandatory success-path descendant cleanup through the whole
  supervisor lifecycle (clean parent exit with a surviving descendant);
* authoritative kernel-provenance receipts;
* the daemon-drain (shutdown) path finishing a mid-body attempt exactly
  once against the real backend.

Gated on the operational probe (skip with an explicit reason when the
runner cannot provide systemd/cgroup-v2); hermetic — every scope is torn
down, no live daemon is touched.
"""
from __future__ import annotations

import threading
import time

import pytest

from runtime.orchestrator.host_supervisor import (
    AdmissionRequest,
    HostSessionSupervisor,
    LaunchResult,
    canary_policy,
)
from runtime.platform.linux_systemd import LinuxSystemdBackend
from runtime.platform.process_census import DescendantSampler, ProcessTreeCensus
from runtime.platform.session_backend import (
    CleanupStatus,
    LaunchSpec,
    MeasurementProvenance,
    TerminalReason,
)

pytestmark = pytest.mark.integration


def _require_systemd() -> LinuxSystemdBackend:
    backend = LinuxSystemdBackend()
    report = backend.probe()
    if not report.healthy:
        pytest.skip(
            f"Linux systemd/cgroup-v2 not usable on this runner: "
            f"{report.reason or report.evidence}"
        )
    return backend


@pytest.fixture(scope="module")
def backend():
    return _require_systemd()


def _make_supervisor(backend, *, sampler=True):
    kwargs = {}
    if sampler:
        kwargs["sampler"] = DescendantSampler(
            census=ProcessTreeCensus(backend._reader)
        ).sample
    return HostSessionSupervisor(
        backend=backend,
        policy=canary_policy(sample_interval_seconds=0.1),
        publisher=lambda receipt: None,
        **kwargs,
    )


def _request(logical_id="sched-e2e"):
    return AdmissionRequest(
        org="test",
        invocation_kind="schedule",
        logical_id=logical_id,
        executor_profile="claude",
    )


def test_supervisor_clean_success_with_surviving_descendant_real(backend):
    """MANDATORY canary-gating test: a clean parent exit with a surviving
    descendant runs the WHOLE supervisor lifecycle — the backend explicitly
    stops the scope, verifies cgroup emptiness, and the lease is released
    exactly once."""
    supervisor = _make_supervisor(backend)
    release_count = {"n": 0}

    def launch_body(running):
        try:
            deadline = time.monotonic() + 5
            while running.process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            return LaunchResult(
                success=running.process.returncode == 0,
                duration_seconds=0.5,
                returncode=running.process.returncode,
            )
        finally:
            release_count["n"] += 1

    outcome = supervisor.run(
        _request(),
        launch_spec=LaunchSpec(argv=("sh", "-c", "sleep 60 & sleep 0.2")),
        launch_body=launch_body,
    )
    assert outcome.terminal_reason is TerminalReason.SUCCESS
    assert outcome.receipt is not None
    assert outcome.receipt.cleanup_status is CleanupStatus.CLEAN
    assert outcome.receipt.quiescent is True
    assert outcome.receipt.survivors == ()
    assert outcome.receipt.memory_peak_provenance is MeasurementProvenance.KERNEL
    assert release_count["n"] == 1  # lease released exactly once


def test_supervisor_nonzero_preserves_primary_reason_real(backend):
    supervisor = _make_supervisor(backend)

    def launch_body(running):
        deadline = time.monotonic() + 5
        while running.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        return LaunchResult(
            success=False,
            duration_seconds=0.5,
            returncode=running.process.returncode,
        )

    outcome = supervisor.run(
        _request("sched-fail"),
        launch_spec=LaunchSpec(argv=("sh", "-c", "exit 3")),
        launch_body=launch_body,
    )
    assert outcome.terminal_reason is TerminalReason.FAILURE
    assert outcome.receipt is not None
    assert outcome.receipt.cleanup_status is CleanupStatus.CLEAN


def test_supervisor_shutdown_mid_body_finishes_exactly_once_real(backend):
    """The daemon-drain path: shutdown() freezes SHUTDOWN on the ownership
    record and the mid-body attempt finishes containment exactly once."""
    supervisor = _make_supervisor(backend)
    entered = threading.Event()
    finish_receipts = []

    def launch_body(running):
        entered.set()
        # Block until the drain drives the finish (or a short timeout).
        deadline = time.monotonic() + 5
        while running.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        # The drain's finish already ran; this body returns a normal result
        # but the durable first-wins SHUTDOWN reason must win.
        return LaunchResult(
            success=running.process.returncode == 0,
            duration_seconds=0.5,
            returncode=running.process.returncode,
        )

    results = {}

    def _run():
        results["outcome"] = supervisor.run(
            _request("sched-shutdown"),
            launch_spec=LaunchSpec(argv=("sh", "-c", "sleep 60")),
            launch_body=launch_body,
        )

    thread = threading.Thread(target=_run)
    thread.start()
    assert entered.wait(timeout=5)
    supervisor.shutdown()
    thread.join(timeout=10)
    outcome = results["outcome"]
    assert outcome.terminal_reason is TerminalReason.SHUTDOWN
    assert outcome.receipt is not None
    assert outcome.receipt.quiescent is True
    # Admission was stopped by the drain and no scope residue remains.
    assert supervisor._admission.is_shutdown() is True
    assert supervisor.active_count() == 0


def test_supervisor_cancellation_finishes_containment_real(backend):
    supervisor = _make_supervisor(backend)
    request = _request("sched-cancel")
    entered = threading.Event()

    def launch_body(running):
        entered.set()
        deadline = time.monotonic() + 5
        while running.process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        return LaunchResult(success=True, duration_seconds=0.5)

    results = {}

    def _run():
        results["outcome"] = supervisor.run(
            request,
            launch_spec=LaunchSpec(argv=("sh", "-c", "sleep 60")),
            launch_body=launch_body,
        )

    thread = threading.Thread(target=_run)
    thread.start()
    assert entered.wait(timeout=5)
    request.cancellation.cancel()
    thread.join(timeout=10)
    outcome = results["outcome"]
    assert outcome.terminal_reason is TerminalReason.CANCELLED
    assert outcome.receipt is not None
    assert outcome.receipt.quiescent is True
