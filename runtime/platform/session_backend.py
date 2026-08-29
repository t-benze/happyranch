"""Platform-neutral session backend contracts for the HostSessionSupervisor.

This module is the **capability-based** contract surface for one daemon-wide
``HostSessionSupervisor`` (see ``runtime/orchestrator/host_supervisor.py``).
It intentionally contains **no backend implementation** — Linux systemd/cgroup
v2, macOS process-group/census, and a future Windows Job Object backend all
implement this Protocol in later slices.

Design rules that are load-bearing:

1. **Callers never branch on OS names.** A backend reports its capabilities
   through :class:`CapabilityReport`; callers act only on declared
   capability levels. A capability that is not reported as ``guaranteed``
   is never treated as guaranteed.
2. **Three-state capability values.** ``guaranteed`` / ``best_effort`` /
   ``unavailable`` exist because a bare boolean is misleading: a sampled
   peak can undercount between samples while a cgroup/job counter is
   authoritative. ``unavailable`` is never rendered as a fabricated zero.
3. **Opaque handles.** :class:`RunningHandle` carries a backend token plus
   root PID and start identity for diagnostics only. Callers must never
   synthesize backend operations (kill, limits, accounting) from the PID —
   the handle is the only authority for teardown.
4. **Provenance.** Every measured value in :class:`Receipt` is tagged with
   :class:`MeasurementProvenance` (``kernel`` / ``sampled`` / ``unavailable``)
   so health/UI can distinguish authoritative counters from sampled estimates.
5. **Windows Job Object shape is preserved, not implemented.** The capability
   vocabulary below is exactly what a future Windows backend needs (job-wide
   memory/active-process/CPU limits, ``KILL_ON_JOB_CLOSE`` tree teardown, job
   accounting). No Windows support is implemented or advertised in this slice;
   a Windows backend is a separately ruled supported-platform release.

Capability truth table (initial platforms):

====================  ============================  ============================  ==================================
Capability            Linux systemd/cgroup v2       macOS initial backend         Future Windows Job Object
====================  ============================  ============================  ==================================
limits_memory         guaranteed (after verify)     unavailable                   guaranteed (job-wide)
limits_pids           guaranteed                    unavailable                   guaranteed (active-process)
limits_cpu            guaranteed (quota/weight)     unavailable                   guaranteed (CPU rate control)
kills_tree_guaranteed guaranteed (stop + empty      unavailable                   guaranteed (KILL_ON_JOB_CLOSE)
                      cgroup check)
kills_tree_best_effort unavailable                  guaranteed (process group +   unavailable
                                                    verified descendant census)
reports_memory_peak   authoritative cgroup counter  sampled resident sum          job accounting / sampled
reports_cpu_total     authoritative                sampled cumulative            job accounting
reports_process_peak  authoritative/sample         sampled                       job accounting / sample
survives_daemon_crash not guaranteed               not guaranteed                credible, must be proven
====================  ============================  ============================  ==================================

``isolation.py`` (canonical-skill-store integrity + same-owner executor
launch) is deliberately **not** consulted or modified by this module or by
``host_supervisor.py``; the supervisor wraps executor launch with admission
and containment and is layered above, never inside, that module.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from runtime.orchestrator.host_supervisor import AdmissionRequest, PolicySnapshot


# ── Capability vocabulary ────────────────────────────────────────────


class Capability(StrEnum):
    """Backend-declared containment/measurement capabilities.

    The value vocabulary deliberately matches the future Windows Job Object
    shape (job-wide memory/process/CPU limits, kill-on-close teardown, job
    accounting) so a Windows backend slots in as a first-class backend
    without a caller-side rewrite.
    """

    LIMITS_MEMORY = "limits_memory"
    LIMITS_PIDS = "limits_pids"
    LIMITS_CPU = "limits_cpu"
    KILLS_TREE_GUARANTEED = "kills_tree_guaranteed"
    KILLS_TREE_BEST_EFFORT = "kills_tree_best_effort"
    REPORTS_MEMORY_PEAK = "reports_memory_peak"
    REPORTS_CPU_TOTAL = "reports_cpu_total"
    REPORTS_PROCESS_PEAK = "reports_process_peak"
    SURVIVES_DAEMON_CRASH = "survives_daemon_crash"


class CapabilityLevel(StrEnum):
    """Three-state capability value.

    ``guaranteed`` is a real, verified enforcement/reporting guarantee.
    ``best_effort`` is a documented, testable best effort (e.g. macOS tree
    cleanup can miss a descendant that daemonizes into a new session).
    ``unavailable`` means the backend does not provide it — callers must
    never infer an unreported guarantee.
    """

    GUARANTEED = "guaranteed"
    BEST_EFFORT = "best_effort"
    UNAVAILABLE = "unavailable"


class MeasurementProvenance(StrEnum):
    """Where a measured value came from.

    ``kernel`` = authoritative counter (cgroup/job accounting).
    ``sampled`` = portable sampler estimate; may undercount between samples.
    ``unavailable`` = not measurable on this backend; never rendered as 0.
    """

    KERNEL = "kernel"
    SAMPLED = "sampled"
    UNAVAILABLE = "unavailable"


class CleanupStatus(StrEnum):
    """Outcome of the terminal containment teardown.

    ``clean`` = tree quiescent without signal escalation.
    ``term`` = graceful TERM used, tree quiescent after.
    ``kill`` = escalated to KILL, tree quiescent after.
    ``incomplete`` = teardown could not be verified/complete within bounds —
    admission must tighten (fail-closed) until reconciled.
    """

    CLEAN = "clean"
    TERM = "term"
    KILL = "kill"
    INCOMPLETE = "incomplete"


class TerminalReason(StrEnum):
    """Frozen primary terminal reason for one invocation attempt.

    This is the *first* terminal condition to win in a finish/cancel race;
    cleanup failures never overwrite it (both travel in the outcome).
    """

    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    RATE_LIMITED = "rate_limited"
    PREPARE_FAILURE = "prepare_failure"
    SPAWN_FAILURE = "spawn_failure"
    SHUTDOWN = "shutdown"


# ── Capability report ────────────────────────────────────────────────


@dataclass(frozen=True)
class CapabilityReport:
    """Result of a backend capability probe.

    Probes run at startup, on explicit health refresh, and after backend
    operation failure — not per session. They are bounded, leave no
    residue, and exercise real operations rather than OS/version strings.
    """

    backend: str
    backend_version: str
    capabilities: Mapping[Capability, CapabilityLevel] = field(default_factory=dict)
    evidence: str = ""
    reason: str | None = None
    probed_at: float = 0.0
    healthy: bool = True

    def level(self, capability: Capability) -> CapabilityLevel:
        """Declared level for *capability*, defaulting to ``unavailable``.

        An unreported capability is never inferred as guaranteed — missing
        enforcement tightens admission, never silently widens limits.
        """
        return self.capabilities.get(capability, CapabilityLevel.UNAVAILABLE)


# ── Handles ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PendingHandle:
    """A prepared-but-not-yet-launched containment handle.

    Returned by ``SessionBackend.prepare``. If the invocation is cancelled
    before launch, or launch never happens, callers must ``abandon`` it so
    partial scope state (if any) is torn down.

    ``invocation_kind`` / ``executor_profile`` carry the bounded receipt
    attribution sourced from the ``AdmissionRequest`` (THR-207 Slice C) so
    the eventual ``Receipt`` can be attributed honestly on every backend.
    Empty values mean the producer did not attribute the request.
    """

    backend: str
    token: str
    request_id: str
    invocation_kind: str = ""
    executor_profile: str = ""


@dataclass(frozen=True)
class RunningHandle:
    """Opaque backend identity for one live (or recently live) invocation.

    ``root_pid`` and ``start_identity`` are **diagnostics only** — callers
    must never synthesize backend operations (kill, limits, accounting) from
    the PID; the opaque ``token`` is the only authority for teardown and
    measurement. ``process`` is the underlying subprocess so the executor
    launch body can ``communicate()``/read ``returncode``.

    ``invocation_kind`` / ``executor_profile`` carry the bounded receipt
    attribution sourced from the ``AdmissionRequest`` (THR-207 Slice C) so
    the eventual ``Receipt`` can be attributed honestly on every backend.
    Empty values mean the producer did not attribute the request.
    """

    backend: str
    token: str
    request_id: str
    root_pid: int
    start_identity: str
    process: subprocess.Popen | None = None
    invocation_kind: str = ""
    executor_profile: str = ""


@dataclass(frozen=True)
class LaunchSpec:
    """argv/stdio/environment for one contained subprocess launch.

    Supplied by the executor launch body (later slices); the backend decides
    how to launch it inside containment.
    """

    argv: tuple[str, ...]
    cwd: str = "."
    env: Mapping[str, str] | None = None
    stdin: Any = subprocess.PIPE
    stdout: Any = subprocess.PIPE
    stderr: Any = subprocess.PIPE
    text: bool = True


# ── Measurement ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResourceSample:
    """One portable sample of the descendant tree's resource footprint.

    ``sampled_at`` is the monotonic timestamp of the sample. Sample cadence
    and inter-sample gaps are recorded so sampled values are never presented
    as continuous truth.
    """

    sampled_at: float
    memory_peak_bytes: int | None = None
    cpu_total_seconds: float | None = None
    process_count: int | None = None
    provenance: MeasurementProvenance = MeasurementProvenance.SAMPLED


@dataclass(frozen=True)
class SurvivorRecord:
    """An identity-verified descendant surviving terminal cleanup.

    Surviving a best-effort cleanup is an *expected* outcome and keeps the
    survivor censused, charged against host pressure/admission, and visible
    in receipts. Surviving a guaranteed cleanup is an anomaly that blocks
    admission until reconciliation.
    """

    pid: int
    start_identity: str
    backend: str
    discovered_at: float
    last_seen_at: float

    @property
    def key(self) -> tuple[int, str]:
        """(pid, start_identity) identity key safe against PID reuse."""
        return (self.pid, self.start_identity)


# ── Receipt ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Receipt:
    """Bounded, cross-platform resource/capability receipt for one attempt.

    One shape on every platform. Measured values carry explicit provenance;
    ``unavailable`` values are ``None``, never fabricated zeros. ``survivors``
    is bounded by the backend's census; ``enforcement_events`` is a bounded
    event log (e.g. ``oom``, ``throttle``, ``job_limit``).

    ``invocation_kind`` and ``executor_profile`` are the bounded receipt
    attribution sourced only from existing ``AdmissionRequest`` data and
    populated honestly by every backend at ``finish`` (THR-207 Slice C).
    Empty strings mean the producer did not attribute the request; the
    operator surfaces redact/bucket them (``bounded_executor_profile`` /
    ``bounded_invocation_kind``) so externally-influenced values stay
    bounded and never create dynamic aggregate-map cardinality.
    """

    backend: str
    terminal_reason: str
    cleanup_status: CleanupStatus
    cleanup_duration_seconds: float
    quiescent: bool
    wall_time_seconds: float
    memory_peak_bytes: int | None = None
    memory_peak_provenance: MeasurementProvenance = MeasurementProvenance.UNAVAILABLE
    cpu_total_seconds: float | None = None
    cpu_total_provenance: MeasurementProvenance = MeasurementProvenance.UNAVAILABLE
    process_peak: int | None = None
    process_peak_provenance: MeasurementProvenance = MeasurementProvenance.UNAVAILABLE
    sample_gaps: tuple[float, ...] = ()
    enforcement_events: tuple[str, ...] = ()
    survivors: tuple[SurvivorRecord, ...] = ()
    invocation_kind: str = ""
    executor_profile: str = ""


@dataclass(frozen=True)
class RecoveryResult:
    """Result of a backend recovery attempt (daemon crash/restart path).

    ``recovered`` is True only when the backend reaped identity-safe owned
    residue. ``residue_remaining`` lists verified survivors that could not
    be reaped and must stay censused/charged.
    """

    recovered: bool
    evidence: str
    residue_remaining: tuple[SurvivorRecord, ...] = ()


# ── Backend errors ───────────────────────────────────────────────────


class BackendError(Exception):
    """Base class for backend operation failures."""


class BackendPrepareError(BackendError):
    """``prepare`` failed; no containment state may be assumed."""


class BackendLaunchError(BackendError):
    """``launch`` failed; partial containment state must be torn down."""


class BackendFinishError(BackendError):
    """``finish`` failed; the caller keeps the primary terminal reason and
    the admission lease is still released exactly once."""


# ── Backend protocol ─────────────────────────────────────────────────


@runtime_checkable
class SessionBackend(Protocol):
    """One containment/measurement backend for agent sessions.

    Implementations live in later slices: Linux systemd/cgroup v2,
    macOS process-group + descendant census, future Windows Job Objects.
    No implementation ships in this slice.
    """

    def probe(self) -> CapabilityReport:
        """Probe the daemon's actual environment; bounded, no residue."""
        ...

    def prepare(
        self, request: "AdmissionRequest", policy: "PolicySnapshot"
    ) -> PendingHandle:
        """Reserve containment state for *request* under the immutable
        *policy* snapshot. Raises :class:`BackendPrepareError` on failure."""
        ...

    def launch(self, pending: PendingHandle, spec: LaunchSpec) -> RunningHandle:
        """Launch the subprocess inside containment per *spec*.

        Raises :class:`BackendLaunchError` on failure; the caller then
        finishes/abandons partial containment."""
        ...

    def sample(self, running: RunningHandle) -> ResourceSample:
        """One portable resource sample of the live descendant tree."""
        ...

    def finish(
        self,
        running: RunningHandle,
        terminal_reason: str,
        grace_seconds: float,
        samples: Sequence[ResourceSample] | None = None,
        sample_prefix_gap: float = 0.0,
    ) -> Receipt:
        """Terminate the tree, verify quiescence, and produce the receipt.

        Runs on **every** terminal path, success included. ``samples`` is the
        supervisor-collected portable sampler data, used by backends whose
        provenance is ``sampled``/``unavailable``. ``sample_prefix_gap`` is
        the truthful elapsed time of the sampling prefix truncated by the
        supervisor's retention bound (0 when nothing was dropped); backends
        prepend it to the serialized gap series so cadence is never presented
        as continuous truth. Raises on unrecoverable teardown failure (the
        caller keeps the primary terminal reason)."""
        ...

    def abandon(self, pending: PendingHandle) -> None:
        """Close a prepared-but-never-launched handle (partial setup)."""
        ...

    def recover(self, handle_token: str) -> RecoveryResult:
        """Recover identity-safe owned residue after daemon crash/restart."""
        ...
