"""Unit tests for the platform-neutral session backend contracts (Slice A).

Covers the capability/report/sample/receipt/opaque-handle contract surface of
``runtime/platform/session_backend.py``: three-state capability values,
measurement provenance (never fabricated zeros), opaque-handle discipline,
and the explicit Slice-A promise that **no concrete backend implementation
ships yet** while the interface preserves the future Windows Job Object shape.
"""
from __future__ import annotations

import dataclasses

import pytest

from runtime.platform.session_backend import (
    BackendError,
    BackendLaunchError,
    BackendPrepareError,
    Capability,
    CapabilityLevel,
    CapabilityReport,
    CleanupStatus,
    LaunchSpec,
    MeasurementProvenance,
    PendingHandle,
    Receipt,
    RecoveryResult,
    ResourceSample,
    RunningHandle,
    SessionBackend,
    SurvivorRecord,
    TerminalReason,
)


def test_capability_vocabulary_covers_windows_job_object_shape():
    """The capability vocabulary is exactly what a future Windows Job
    Object backend needs (job-wide limits, kill-on-close, job accounting) —
    preserved as a first-class shape, not implemented here."""
    values = {c.value for c in Capability}
    assert values == {
        "limits_memory",
        "limits_pids",
        "limits_cpu",
        "kills_tree_guaranteed",
        "kills_tree_best_effort",
        "reports_memory_peak",
        "reports_cpu_total",
        "reports_process_peak",
        "survives_daemon_crash",
    }


def test_capability_levels_are_three_state():
    assert CapabilityLevel.GUARANTEED.value == "guaranteed"
    assert CapabilityLevel.BEST_EFFORT.value == "best_effort"
    assert CapabilityLevel.UNAVAILABLE.value == "unavailable"


def test_unreported_capability_defaults_to_unavailable():
    report = CapabilityReport(
        backend="fake", backend_version="1.0", capabilities={}
    )
    # Missing enforcement is never inferred as a guarantee (fail-closed).
    assert report.level(Capability.LIMITS_MEMORY) is CapabilityLevel.UNAVAILABLE
    assert report.level(Capability.KILLS_TREE_GUARANTEED) is CapabilityLevel.UNAVAILABLE
    assert report.healthy is True


def test_receipt_provenance_never_fabricates_zero():
    """``unavailable`` measured values stay ``None`` — never rendered as 0."""
    receipt = Receipt(
        backend="fake",
        terminal_reason="success",
        cleanup_status=CleanupStatus.CLEAN,
        cleanup_duration_seconds=0.1,
        quiescent=True,
        wall_time_seconds=1.0,
        memory_peak_provenance=MeasurementProvenance.UNAVAILABLE,
        cpu_total_provenance=MeasurementProvenance.UNAVAILABLE,
        process_peak_provenance=MeasurementProvenance.UNAVAILABLE,
    )
    assert receipt.memory_peak_bytes is None
    assert receipt.cpu_total_seconds is None
    assert receipt.process_peak is None


def test_receipt_cleanup_and_terminal_vocabulary():
    assert {c.value for c in CleanupStatus} == {"clean", "term", "kill", "incomplete"}
    assert {t.value for t in TerminalReason} == {
        "success", "failure", "timeout", "cancelled", "rate_limited",
        "prepare_failure", "spawn_failure", "shutdown",
    }


def test_running_handle_is_opaque_and_frozen():
    handle = RunningHandle(
        backend="fake",
        token="opaque-token",
        request_id="req-1",
        root_pid=4242,
        start_identity="start-1",
        process=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        handle.token = "tampered"  # type: ignore[misc]
    # The opaque token, not the PID, is the teardown authority.
    assert handle.token == "opaque-token"
    assert handle.root_pid == 4242
    assert handle.start_identity == "start-1"


def test_survivor_identity_key_is_pid_reuse_safe():
    sv = SurvivorRecord(
        pid=7, start_identity="boot-9", backend="macos",
        discovered_at=1.0, last_seen_at=2.0,
    )
    assert sv.key == (7, "boot-9")


def test_launch_spec_defaults_are_blocking_stdio():
    spec = LaunchSpec(argv=("python", "-c", "pass"))
    assert spec.cwd == "."
    assert spec.text is True
    # stdio defaults to PIPE constants (the daemon needs the pid + streams).
    assert spec.stdin == spec.stdout == spec.stderr


def test_backend_error_hierarchy():
    assert issubclass(BackendPrepareError, BackendError)
    assert issubclass(BackendLaunchError, BackendError)


def test_no_concrete_backend_ships_in_slice_a():
    """Slice A ships the Protocol + contract types only. No Linux/macOS
    backend, and Windows support is explicitly not implemented/advertised."""
    import runtime.platform.session_backend as sb

    assert isinstance(SessionBackend, type)
    # No concrete backend classes exist anywhere in the module surface.
    for name in (
        "LinuxSystemdBackend",
        "LinuxCgroupBackend",
        "MacOSProcessGroupBackend",
        "MacOSBackend",
        "WindowsJobBackend",
        "WindowsJobObjectBackend",
    ):
        assert not hasattr(sb, name), f"Slice A must not ship {name}"
    # The documented future Windows shape lives in the module docstring.
    assert "Windows Job Object" in sb.__doc__


class _ShapeCompliantBackend:
    """A minimal structurally-compliant backend (what later slices must look
    like); used only to prove the Protocol is satisfied."""

    def probe(self) -> CapabilityReport:
        return CapabilityReport(backend="shape", backend_version="1.0")

    def prepare(self, request, policy) -> PendingHandle:
        return PendingHandle(backend="shape", token="t", request_id=request.logical_id)

    def launch(self, pending, spec) -> RunningHandle:
        return RunningHandle(
            backend="shape", token=pending.token, request_id=pending.request_id,
            root_pid=1, start_identity="s",
        )

    def sample(self, running) -> ResourceSample:
        return ResourceSample(sampled_at=0.0)

    def finish(self, running, terminal_reason, grace_seconds, samples=None) -> Receipt:
        return Receipt(
            backend="shape", terminal_reason=terminal_reason,
            cleanup_status=CleanupStatus.CLEAN, cleanup_duration_seconds=0.0,
            quiescent=True, wall_time_seconds=0.0,
        )

    def abandon(self, pending) -> None:
        return None

    def recover(self, handle_token) -> RecoveryResult:
        return RecoveryResult(recovered=False, evidence="shape")


def test_protocol_is_runtime_checkable_and_satisfiable():
    assert isinstance(_ShapeCompliantBackend(), SessionBackend)
    assert isinstance(CapabilityReport, type)


# ── honest no-enforcement passthrough (THR-207 real-caller wiring) ─────


def test_passthrough_backend_declares_no_capabilities():
    """The passthrough used by the wired schedule producer is truthful: every
    capability is unavailable, never a fabricated guarantee."""
    from runtime.platform.passthrough_backend import PassthroughBackend

    backend = PassthroughBackend()
    report = backend.probe()
    assert report.healthy is True
    assert report.capabilities == {}
    for cap in Capability:
        assert report.level(cap) == CapabilityLevel.UNAVAILABLE


def test_passthrough_backend_lifecycle_is_residue_free():
    """prepare/launch/finish/abandon leave no containment residue and never
    fabricate measured values (provenance unavailable, no survivors)."""
    from runtime.platform.passthrough_backend import PassthroughBackend
    from runtime.orchestrator.host_supervisor import (
        AdmissionRequest, PolicySnapshot, canary_policy,
    )
    from runtime.platform.session_backend import LaunchSpec

    backend = PassthroughBackend()
    policy = canary_policy()
    request = AdmissionRequest(
        org="happyranch", invocation_kind="schedule",
        logical_id="SCHEDULE-X", executor_profile="claude",
    )
    pending = backend.prepare(request, policy)
    assert pending.request_id == "SCHEDULE-X"
    running = backend.launch(pending, LaunchSpec(argv=("a",)))
    assert running.root_pid == 0  # no subprocess exists here (diagnostic)
    receipt = backend.finish(running, "success", 5.0)
    assert receipt.backend == "passthrough"
    assert receipt.cleanup_status == CleanupStatus.CLEAN
    assert receipt.quiescent is True  # absence of containment state, not a claim
    assert receipt.survivors == ()
    assert receipt.memory_peak_bytes is None
    assert receipt.memory_peak_provenance == MeasurementProvenance.UNAVAILABLE
    backend.abandon(pending)  # no-op, no residue
    recovered = backend.recover("tok")
    assert recovered.recovered is False
