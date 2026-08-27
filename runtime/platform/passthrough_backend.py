"""Honest no-enforcement :class:`SessionBackend` (fallback + test default).

THR-207 real-caller wiring: ``HostSessionSupervisor`` runs the daemon's
production producers through a real backend (Linux systemd/cgroup-v2 or
macOS process-group/census selected by the capability factory); this
passthrough is the truthful selection when the operational probe is
unsupported/unhealthy AND the deterministic default of
``build_default_host_supervisor()`` with no arguments (schedule-fire
integration suites and unit fakes):

* ``probe`` declares **no** capability — every value is ``unavailable``.
  This is the truthful capability contract, never a fabricated guarantee:
  missing enforcement tightens admission (the binding macOS canary cap of 4
  applies) exactly as the governing spec requires.
* ``prepare``/``launch``/``finish`` perform no containment. Subprocess
  control stays entirely inside the executor launch body (the uncontained
  path, which still routes through the per-provider throttle and
  ``runtime/platform/isolation.py`` exactly as before).
* ``finish`` reports ``CLEAN``/``quiescent`` **about the absence of
  containment state** (there is no tree this backend manages and nothing it
  can verify) — it never claims a descendant-tree quiescence it did not
  check, and it never fabricates measured values (provenance is
  ``unavailable``).

The passthrough is not a Linux or macOS backend implementation and performs
no launch-path, provider, pool, spacing, backoff, or capacity change.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

from runtime.platform.session_backend import (
    CapabilityReport,
    CleanupStatus,
    MeasurementProvenance,
    PendingHandle,
    Receipt,
    RecoveryResult,
    ResourceSample,
    RunningHandle,
    SessionBackend,
)

_BACKEND_NAME = "passthrough"
_BACKEND_VERSION = "0.1"


class PassthroughBackend:
    """Platform-neutral ``SessionBackend`` with no containment capabilities.

    Implements the full ``SessionBackend`` Protocol but declares every
    capability ``unavailable`` and never touches a subprocess. It exists so
    the real-caller wiring has a truthful backend in this slice; later
    backend slices (Linux systemd/cgroup v2, macOS process group) replace it
    without caller changes.
    """

    # ── capability probe ──────────────────────────────────────────

    def probe(self) -> CapabilityReport:
        return CapabilityReport(
            backend=_BACKEND_NAME,
            backend_version=_BACKEND_VERSION,
            capabilities={},
            evidence=(
                "passthrough: no containment backend ships in slice A; "
                "all capabilities unavailable (missing enforcement tightens "
                "admission)"
            ),
            probed_at=time.monotonic(),
            healthy=True,
        )

    # ── lifecycle ─────────────────────────────────────────────────

    def prepare(self, request, policy) -> PendingHandle:
        """Reserve an opaque handle; no containment state is created."""
        return PendingHandle(
            backend=_BACKEND_NAME,
            token=f"passthrough-{request.logical_id}",
            request_id=request.logical_id,
        )

    def launch(self, pending: PendingHandle, spec) -> RunningHandle:
        """No containment launch — the executor launch body performs the real
        launch (and reports the real PID through its own ``on_started``
        hook); the supervisor's ``request.on_started`` is therefore unused by
        the wired producer. ``root_pid`` is 0 because no subprocess exists
        here; callers must not synthesize backend operations from it."""
        return RunningHandle(
            backend=_BACKEND_NAME,
            token=pending.token,
            request_id=pending.request_id,
            root_pid=0,
            start_identity="",
            process=None,
        )

    def sample(self, running: RunningHandle) -> ResourceSample:
        """No measurement surface: ``unavailable`` is never a fabricated 0."""
        return ResourceSample(
            sampled_at=time.monotonic(),
            memory_peak_bytes=None,
            cpu_total_seconds=None,
            process_count=None,
            provenance=MeasurementProvenance.UNAVAILABLE,
        )

    def finish(
        self,
        running: RunningHandle,
        terminal_reason: str,
        grace_seconds: float,
        samples: Sequence[ResourceSample] | None = None,
        sample_prefix_gap: float = 0.0,
    ) -> Receipt:
        """Nothing to tear down or verify — this backend manages no tree.

        ``CLEAN``/``quiescent`` describe the **absence of containment
        state**, never a verified descendant-tree quiescence; all measured
        values are ``unavailable``."""
        return Receipt(
            backend=_BACKEND_NAME,
            terminal_reason=terminal_reason,
            cleanup_status=CleanupStatus.CLEAN,
            cleanup_duration_seconds=0.0,
            quiescent=True,
            wall_time_seconds=0.0,
            memory_peak_bytes=None,
            memory_peak_provenance=MeasurementProvenance.UNAVAILABLE,
            cpu_total_seconds=None,
            cpu_total_provenance=MeasurementProvenance.UNAVAILABLE,
            process_peak=None,
            process_peak_provenance=MeasurementProvenance.UNAVAILABLE,
            sample_gaps=(),
            enforcement_events=(),
            survivors=(),
        )

    def abandon(self, pending: PendingHandle) -> None:
        """No partial containment state exists to close."""

    def recover(self, handle_token: str) -> RecoveryResult:
        """No durable residue exists on this backend."""
        return RecoveryResult(
            recovered=False,
            evidence="passthrough backend has no durable containment residue",
        )


# Keep the Protocol check honest at import time (no OS branching anywhere).
def _protocol_check() -> None:
    assert isinstance(PassthroughBackend(), SessionBackend)


_protocol_check()
