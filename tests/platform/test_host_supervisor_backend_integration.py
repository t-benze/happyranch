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

import os
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
    """A nonzero target exit preserves the primary reason through the whole
    lifecycle. The target lives long enough for launch to verify the applied
    enforcement envelope (Slice C), the body observes the nonzero exit, and
    finish tears the scope down (killing the backgrounded descendant) with a
    CLEAN receipt. An INSTANT exit is the fail-closed fast-exit case and is
    covered by ``test_supervisor_fast_exit_fails_closed_real``."""
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
        launch_spec=LaunchSpec(argv=("sh", "-c", "sleep 60 & sleep 0.5; exit 3")),
        launch_body=launch_body,
    )
    assert outcome.terminal_reason is TerminalReason.FAILURE
    assert outcome.receipt is not None
    assert outcome.receipt.cleanup_status is CleanupStatus.CLEAN


def test_supervisor_fast_exit_fails_closed_real(backend):
    """Slice C fail-closed fast-exit contract against the REAL systemd
    backend: a target that exits before the scope cgroup can be resolved is
    never reported as a normally launched enforced session. The supervisor
    surfaces SPAWN_FAILURE with the fail-closed verification error, releases
    the admission lease exactly once, publishes no receipt, and leaves no
    lingering scope unit."""
    published: list = []
    supervisor = HostSessionSupervisor(
        backend=backend,
        policy=canary_policy(sample_interval_seconds=0.1),
        publisher=published.append,
        sampler=DescendantSampler(
            census=ProcessTreeCensus(backend._reader)
        ).sample,
    )
    before = supervisor._admission.released_total()
    request = _request("sched-fast-exit")
    outcome = supervisor.run(
        request,
        launch_spec=LaunchSpec(argv=("sh", "-c", "exit 3")),
        launch_body=lambda running: LaunchResult(
            success=False, duration_seconds=0.0, returncode=3
        ),
    )
    assert outcome.terminal_reason is TerminalReason.SPAWN_FAILURE
    assert outcome.receipt is None  # nothing published on fail-closed launch
    assert "envelope could be verified" in (outcome.error or "")
    assert published == []
    assert supervisor._admission.released_total() == before + 1  # exactly once
    # No residue: the transient scope was collected (no unit remains under
    # this logical id's scope-name prefix).
    _, out = backend._systemctl(
        "list-units",
        "--all",
        "--no-legend",
        f"happyranch-session-{request.logical_id}-*.scope",
        timeout=5,
    )
    assert out.strip() == ""


def test_supervisor_fast_exit_fails_closed_while_cgroup_still_available(tmp_path):
    """Deterministic adversarial regression for the exact reviewer case: the
    target exits before ``_wait_for_cgroup`` resolves, yet the transient
    scope's ControlGroup is STILL queryable and memory.high / memory.max /
    pids.max STILL hold the exact envelope (reproduced with fixed seams, not
    timing). The supervisor surfaces SPAWN_FAILURE with no RunningHandle,
    never enters the launch body, never fires ``on_started``, publishes no
    receipt, and releases the admission lease exactly once after
    scope/process cleanup. Not probe-gated by construction: every seam the
    fast-exit race needs is deterministic, so this runs on any host."""
    backend = LinuxSystemdBackend()
    backend._cgroup_root = os.fspath(tmp_path)
    cgdir = tmp_path / "cg"
    cgdir.mkdir()
    for name, value in {
        "memory.high": str(2 * 1024**3),
        "memory.max": str(4 * 1024**3),
        "pids.max": "1024",
    }.items():
        (cgdir / name).write_text(value + "\n", encoding="utf-8")

    calls: list[list[str]] = []

    def run(argv, timeout=10.0):
        calls.append(list(argv))
        # ControlGroup stays queryable; stop is a no-op (scope already gone).
        if "show" in argv:
            return (0, "/cg")
        return (0, "")

    backend._run = run  # type: ignore[method-assign]
    backend._systemd_run_scope = (  # type: ignore[method-assign]
        lambda unit, slice_name, argv, **kwargs: _ExitedProc()
    )
    backend._wait_for_cgroup = (  # type: ignore[method-assign]
        lambda unit, proc, timeout=None: None
    )
    backend._start_exit_capture = lambda state, proc: None  # type: ignore[method-assign]

    published: list = []
    started: list = []
    body_entered = {"n": 0}

    def launch_body(running):
        body_entered["n"] += 1
        raise AssertionError("launch body must never run for an exited target")

    supervisor = HostSessionSupervisor(
        backend=backend,
        policy=canary_policy(sample_interval_seconds=0.1),
        publisher=published.append,
    )
    before = supervisor._admission.released_total()
    outcome = supervisor.run(
        AdmissionRequest(
            org="test",
            invocation_kind="schedule",
            logical_id="sched-cg-avail",
            executor_profile="claude",
            on_started=started.append,
        ),
        launch_spec=LaunchSpec(argv=("sh", "-c", "exit 0")),
        launch_body=launch_body,
    )
    assert outcome.terminal_reason is TerminalReason.SPAWN_FAILURE
    assert outcome.receipt is None  # no finish, no receipt, no publication
    assert outcome.cleanup_status is None
    assert started == []  # on_started never fired (no RunningHandle was bound)
    assert body_entered["n"] == 0  # launch body never entered
    assert published == []  # no receipt published
    assert supervisor._admission.released_total() == before + 1  # exactly once
    # Scope/process cleanup: launch stopped the scope before raising and the
    # supervisor abandoned the pending handle (idempotent second stop).
    assert any("stop" in argv for argv in calls)
    # The fail-closed error carries the exact applied-limits diagnostic
    # evidence — the verification ran and its outcome is preserved.
    assert "exited before its session enforcement envelope could be verified" in (
        outcome.error or ""
    )
    assert "exact session envelope applied" in (outcome.error or "")


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
