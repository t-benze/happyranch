"""Real Linux integration: the actual executor Popen body (``_run_command``)
in contained mode against the REAL Linux systemd/cgroup-v2 backend.

Proves the THR-207 task-producer wiring end to end on a capable host: the
supervisor launches the command into a per-session transient scope via
``backend.launch``, ``_run_command`` communicates with the backend-created
process (no self-launch), and on a clean parent exit with a surviving
descendant the supervisor's terminal finish explicitly stops the whole
scope and verifies cgroup emptiness before releasing the lease.

Gated on the operational probe with an explicit skip reason — an unavailable
real capability is NOT silently counted as PASS (skip, not pass). Hermetic:
every scope is torn down; no live daemon is touched.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from runtime.orchestrator.executors import _run_command
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


def test_run_command_contained_success_path_descendant_cleanup_real(
    backend, tmp_path
):
    """MANDATORY canary-gating test through the REAL executor Popen body: a
    clean parent exit with a surviving descendant runs the whole lifecycle —
    the backend explicitly stops the scope, verifies cgroup emptiness, and
    the lease is released exactly once."""
    supervisor = HostSessionSupervisor(
        backend=backend,
        policy=canary_policy(sample_interval_seconds=0.1),
        publisher=lambda receipt: None,
        sampler=DescendantSampler(
            census=ProcessTreeCensus(backend._reader)
        ).sample,
    )
    ws = tmp_path / "ws"
    ws.mkdir(parents=True, exist_ok=True)

    def launch_body(running):
        # The ACTUAL executor Popen body in contained mode (THR-207): the
        # subprocess is already launched into the scope; communicate+parse.
        result = _run_command(
            ["sh", "-c", "sleep 60 >/dev/null 2>&1 & sleep 0.2"],
            workspace=ws,
            session_id="sess-real-1",
            timeout_seconds=10,
            provider="claude",
            running=running,
        )
        return LaunchResult(
            success=result.success,
            duration_seconds=float(getattr(result, "duration_seconds", 0) or 0),
            returncode=getattr(result, "returncode", None),
            error=getattr(result, "error", None),
            rate_limited=bool(getattr(result, "rate_limited", False)),
            timed_out=False,
            payload=result,
        )

    outcome = supervisor.run(
        AdmissionRequest(
            org="test", invocation_kind="task", logical_id="task-real-1",
            executor_profile="claude",
        ),
        launch_spec=LaunchSpec(
            argv=("sh", "-c", "sleep 60 >/dev/null 2>&1 & sleep 0.2"),
            cwd=str(ws),
        ),
        launch_body=launch_body,
    )
    assert outcome.terminal_reason is TerminalReason.SUCCESS
    assert outcome.receipt is not None
    # The surviving `sleep 60` descendant was explicitly stopped and the
    # cgroup verified empty (kernel-provenance counters).
    assert outcome.receipt.cleanup_status is CleanupStatus.CLEAN
    assert outcome.receipt.quiescent is True
    assert outcome.receipt.survivors == ()
    assert outcome.receipt.memory_peak_provenance is MeasurementProvenance.KERNEL
    # Lease released exactly once and no ownership residue.
    assert supervisor.active_count() == 0
    assert supervisor._admission.released_total() == 1


def test_run_command_contained_clean_exit_receipt_kernel_values_real(backend, tmp_path):
    """THE shipping seam on the real host: a contained command that exits
    cleanly (the executor's ``communicate`` reaps it; systemd collects the
    transient scope before the supervisor's terminal finish) must produce a
    receipt with non-null KERNEL memory/CPU/process peaks — the deployed
    defect was 3-for-3 null on exactly this path."""
    supervisor = HostSessionSupervisor(
        backend=backend,
        policy=canary_policy(sample_interval_seconds=0.1),
        publisher=lambda receipt: None,
    )
    ws = tmp_path / "ws-clean"
    ws.mkdir(parents=True, exist_ok=True)

    def launch_body(running):
        result = _run_command(
            ["sh", "-c", "sleep 0.3"],
            workspace=ws,
            session_id="sess-real-clean-1",
            timeout_seconds=10,
            provider="claude",
            running=running,
        )
        return LaunchResult(
            success=result.success,
            duration_seconds=float(getattr(result, "duration_seconds", 0) or 0),
            returncode=getattr(result, "returncode", None),
            error=getattr(result, "error", None),
            rate_limited=bool(getattr(result, "rate_limited", False)),
            timed_out=False,
            payload=result,
        )

    outcome = supervisor.run(
        AdmissionRequest(
            org="test", invocation_kind="task", logical_id="task-real-clean-1",
            executor_profile="claude",
        ),
        launch_spec=LaunchSpec(
            argv=("sh", "-c", "sleep 0.3"),
            cwd=str(ws),
        ),
        launch_body=launch_body,
    )
    assert outcome.terminal_reason is TerminalReason.SUCCESS
    assert outcome.receipt is not None
    assert outcome.receipt.memory_peak_bytes is not None
    assert outcome.receipt.memory_peak_provenance is MeasurementProvenance.KERNEL
    assert outcome.receipt.cpu_total_seconds is not None
    assert outcome.receipt.cpu_total_provenance is MeasurementProvenance.KERNEL
    assert outcome.receipt.process_peak is not None
    assert outcome.receipt.process_peak_provenance is MeasurementProvenance.KERNEL
    assert outcome.receipt.cleanup_status is CleanupStatus.CLEAN
    assert outcome.receipt.quiescent is True
    assert supervisor.active_count() == 0


def test_run_command_contained_timeout_tree_cleanup_real(backend, tmp_path):
    """A contained session that times out in the executor body kills the main
    process; the supervisor's terminal finish still stops the whole scope and
    verifies cgroup emptiness (timeout remains the primary reason)."""
    supervisor = HostSessionSupervisor(
        backend=backend,
        policy=canary_policy(),
        publisher=lambda receipt: None,
    )
    ws = tmp_path / "ws2"
    ws.mkdir(parents=True, exist_ok=True)

    def launch_body(running):
        result = _run_command(
            ["sh", "-c", "sleep 60 & sleep 60"],
            workspace=ws,
            session_id="sess-real-2",
            timeout_seconds=1,  # the body's own timeout fires
            provider="claude",
            running=running,
        )
        return LaunchResult(
            success=result.success,
            duration_seconds=float(getattr(result, "duration_seconds", 0) or 0),
            returncode=getattr(result, "returncode", None),
            error=getattr(result, "error", None),
            rate_limited=bool(getattr(result, "rate_limited", False)),
            timed_out="timed out" in (getattr(result, "error", "") or "").lower(),
            payload=result,
        )

    outcome = supervisor.run(
        AdmissionRequest(
            org="test", invocation_kind="task", logical_id="task-real-2",
            executor_profile="claude",
        ),
        launch_spec=LaunchSpec(
            argv=("sh", "-c", "sleep 60 & sleep 60"),
            cwd=str(ws),
        ),
        launch_body=launch_body,
    )
    assert outcome.terminal_reason is TerminalReason.TIMEOUT
    assert outcome.receipt is not None
    assert outcome.receipt.quiescent is True
    assert outcome.receipt.survivors == ()
    assert supervisor.active_count() == 0


def test_task_producer_cancel_route_finishes_real_scope(backend, tmp_path):
    """Cancellation through the opaque control (what the /cancel route invokes)
    drives REAL backend finish: the scope is stopped and cgroup-verified empty
    while the body is still blocked in communicate."""
    import threading

    supervisor = HostSessionSupervisor(
        backend=backend,
        policy=canary_policy(),
        publisher=lambda receipt: None,
    )
    ws = tmp_path / "ws3"
    ws.mkdir(parents=True, exist_ok=True)
    request = AdmissionRequest(
        org="test", invocation_kind="task", logical_id="task-real-3",
        executor_profile="claude",
    )
    entered = threading.Event()
    results = {}

    def launch_body(running):
        entered.set()
        result = _run_command(
            ["sh", "-c", "sleep 60"],
            workspace=ws,
            session_id="sess-real-3",
            timeout_seconds=30,
            provider="claude",
            running=running,
        )
        return LaunchResult(
            success=result.success,
            duration_seconds=0.5,
            returncode=getattr(result, "returncode", None),
            error=getattr(result, "error", None),
            rate_limited=False,
            timed_out=False,
            payload=result,
        )

    def _run():
        results["outcome"] = supervisor.run(
            request,
            launch_spec=LaunchSpec(argv=("sh", "-c", "sleep 60"), cwd=str(ws)),
            launch_body=launch_body,
        )

    thread = threading.Thread(target=_run)
    thread.start()
    assert entered.wait(timeout=5)
    # Exactly what the /tasks/{id}/cancel route invokes for a wired session:
    request.cancellation.cancel()
    thread.join(timeout=15)
    outcome = results["outcome"]
    assert outcome.terminal_reason is TerminalReason.CANCELLED
    assert outcome.receipt is not None
    assert outcome.receipt.quiescent is True
    assert outcome.receipt.survivors == ()
    assert supervisor.active_count() == 0
