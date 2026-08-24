"""HostSessionSupervisor: daemon-wide admission + session lifecycle core.

Slice A (THR-207 rulings 1/2/3/4/5/6 as amended; governing spec
``docs/superpowers/specs/2026-08-24-host-resource-concurrency.md``) ships the
**pure, platform-neutral core** of the host-resource concurrency architecture:

* capability/report/sample/receipt/opaque-handle contracts — see
  ``runtime/platform/session_backend.py``;
* one daemon-wide **FIFO-with-aging admission** controller;
* the **HostSessionSupervisor lifecycle** that owns the load-bearing
  ordering: *admission acquire → prepare → launch → run → freeze terminal
  result → finish containment → residue accounting/reconciliation → publish
  bounded receipt → release lease (exactly once)*.

No production wiring ships in this slice: executors do not yet call this
module, no Linux/macOS backends exist, no routes/metrics/audit/config expose
it, and ``runtime/platform/isolation.py`` is untouched. Later serial slices
wire the executors, backends, and observability against these contracts.

Load-bearing ordering invariants (enforced here, honored by later wiring):

1. **Nothing launches before admission.** ``backend.prepare``/``backend.launch``
   and the launch body run only after an admission lease is granted.
2. **Queued cancellation creates no handle.** A request cancelled while queued
   is removed without prepare/launch — no lease, no handle, no launch.
3. **Every terminal path finishes containment before lease release**, success
   included, with the fixed ordering: freeze terminal result → collect
   receipt → backend finish (tree teardown + quiescence check) → capability-
   appropriate residue accounting/reconciliation → publish bounded receipt →
   release lease.
4. **Cleanup errors never replace the primary terminal reason.** Both travel
   in the outcome; the lease is still released exactly once.
5. **Residue consequences are capability-conditional.** Guaranteed-cleanup
   residue blocks admission until explicit reconciliation (survivor exit /
   successful re-probe / operator acknowledgement after verified cleanup).
   Best-effort verified survivors stay censused/charged/visible and block only
   on census/measurement failure or a conservative survivor threshold.
6. **Policy snapshots are immutable per invocation.** The snapshot passed to
   ``prepare`` is frozen; the supervisor never mutates or re-derives it, and
   never derives caps from host resources.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from runtime.platform.session_backend import (
    BackendError,
    Capability,
    CapabilityLevel,
    CapabilityReport,
    CleanupStatus,
    LaunchSpec,
    PendingHandle,
    Receipt,
    ResourceSample,
    RunningHandle,
    SessionBackend,
    SurvivorRecord,
    TerminalReason,
)

logger = logging.getLogger(__name__)


# ── Cancellation ─────────────────────────────────────────────────────


class CancellationToken:
    """Thread-safe cancellation signal for one logical invocation.

    A queued or running invocation may register at most one **opaque
    control** — the containment-handle binding that drives backend teardown.
    Cancellation goes through the control (and therefore the backend), never
    a bare PID signal. Registration is first-wins; later registrations are
    ignored (one opaque handle per attempt).
    """

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._control: "_OpaqueCancelControl | None" = None
        self._waker: Callable[[], None] | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def register(self, control: "_OpaqueCancelControl") -> bool:
        """Bind the opaque cancel control for the **current** attempt.

        Latest-wins: a 429 retry starts a fresh attempt with a fresh
        containment handle, so the control must be rebound per attempt; a
        previous attempt's control is stale by definition.

        The handshake is atomic with respect to launch: when cancellation
        already fired **before** registration, the control is invoked
        immediately (replaying the fired token) and ``True`` is returned so
        the caller abandons the pending handle without launching. Returns
        ``False`` when the token is not yet fired (the caller's launch fence
        then arbitrates any cancellation that lands after registration)."""
        with self._lock:
            self._control = control
            fired = self._cancelled.is_set()
        if fired:
            control.invoke()
        return fired

    def set_waker(self, waker: Callable[[], None]) -> None:
        """Wake the blocking admission wait when cancellation fires."""
        with self._lock:
            self._waker = waker

    def cancel(self) -> bool:
        """Request cancellation; returns True when this call wins the race.

        Invokes the registered opaque control synchronously so backend
        teardown (tree TERM/KILL + quiescence check) starts promptly, and
        wakes any blocked admission wait so a queued request is removed
        without launch. A later slice may decouple the invocation from the
        cancel route.
        """
        won = not self._cancelled.is_set()
        self._cancelled.set()
        with self._lock:
            control = self._control
            waker = self._waker
        if control is not None:
            control.invoke()
        if waker is not None:
            waker()
        return won

    def wait(self, timeout: float | None = None) -> bool:
        """Block until cancelled (True) or *timeout* elapses (False)."""
        return self._cancelled.wait(timeout)


# ── Admission request & policy ───────────────────────────────────────


@dataclass(frozen=True)
class AdmissionRequest:
    """Identity + producer metadata for one top-level agent invocation.

    ``enqueued_at`` is the **original** enqueue time (monotonic) and is
    preserved across 429-retry re-entry so aging survives re-admission.
    """

    org: str
    invocation_kind: str
    logical_id: str
    executor_profile: str
    enqueued_at: float | None = None
    retry_attempt: int = 0
    on_started: Callable[[int], None] | None = None
    cancellation: CancellationToken = field(default_factory=CancellationToken)

    def with_retry_attempt(self, attempt: int) -> "AdmissionRequest":
        """A fresh request for a retry: same identity, original enqueue age,
        fresh containment handle for the new attempt."""
        return AdmissionRequest(
            org=self.org,
            invocation_kind=self.invocation_kind,
            logical_id=self.logical_id,
            executor_profile=self.executor_profile,
            enqueued_at=self.enqueued_at,
            retry_attempt=attempt,
            on_started=self.on_started,
            cancellation=self.cancellation,
        )


@dataclass(frozen=True)
class CapPolicy:
    """A capability-conditional admission cap input.

    ``binding=True`` caps reduce the effective admission ceiling.
    ``binding=False`` (shadow) is recorded and instrumented but **never**
    reduces admission below the producer envelope.
    """

    value: int
    binding: bool


@dataclass(frozen=True)
class PolicySnapshot:
    """Immutable per-invocation policy snapshot.

    Explicit **canary inputs only** — never derived from host resources
    (CPU count, RAM) at runtime. The Linux ``<=11`` ceiling is a non-binding
    shadow input (initial Linux safety is containment plus pressure gates);
    the macOS cap of 4 is a binding input. The cleanup grace is a measured
    low-single-digit canary input, not a universal final constant. Final
    thresholds/grace remain receipt-driven and unapproved.
    """

    global_session_cap: int
    producer_envelope: int
    linux_shadow_cap: CapPolicy
    macos_binding_cap: CapPolicy
    cleanup_grace_seconds: float
    best_effort_survivor_threshold: int
    sample_interval_seconds: float = 0.0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __post_init__(self) -> None:
        if self.global_session_cap < 1:
            raise ValueError("global_session_cap must be >= 1")
        if self.producer_envelope < 1:
            raise ValueError("producer_envelope must be >= 1")
        if self.linux_shadow_cap.binding:
            raise ValueError(
                "the Linux cap is a non-binding shadow/canary input; "
                "binding=True is not allowed"
            )
        if self.linux_shadow_cap.value < 1 or self.linux_shadow_cap.value > 11:
            raise ValueError("the Linux shadow cap must be 1..11 (canary bound)")
        if not self.macos_binding_cap.binding or self.macos_binding_cap.value < 1:
            raise ValueError("the macOS cap is a binding input with value >= 1")
        if self.cleanup_grace_seconds <= 0:
            raise ValueError("cleanup_grace_seconds must be > 0")
        if self.best_effort_survivor_threshold < 0:
            raise ValueError("best_effort_survivor_threshold must be >= 0")
        if self.sample_interval_seconds < 0:
            raise ValueError("sample_interval_seconds must be >= 0")

    def effective_cap(self, *, enforcement_guaranteed: bool) -> int:
        """Configured cap lowered only by binding capability caps.

        Capability-derived, never OS-name-derived: when the active backend
        guarantees the enforcement family (memory/PID/CPU limits), the
        Linux ``<=11`` ceiling stays a non-binding shadow input over the
        producer envelope. When enforcement is missing (macOS-style), the
        binding cap applies — missing enforcement tightens admission."""
        caps = [self.global_session_cap]
        if enforcement_guaranteed:
            return min(caps)
        caps.extend(
            p.value
            for p in (self.linux_shadow_cap, self.macos_binding_cap)
            if p.binding
        )
        return min(caps)


def canary_policy(
    *,
    cleanup_grace_seconds: float = 5.0,
    global_session_cap: int = 11,
    best_effort_survivor_threshold: int = 3,
    sample_interval_seconds: float = 1.0,
) -> PolicySnapshot:
    """The founder-approved canary policy inputs.

    Linux cap 11 = non-binding shadow over the 11-slot producer envelope
    (4 task + 4 thread + 1 dream + 1 wake + 1 schedule); macOS cap 4 =
    intentionally binding. Cleanup grace is a measured low-single-digit
    canary input. All values are explicit inputs, never host-derived.
    """
    return PolicySnapshot(
        global_session_cap=global_session_cap,
        producer_envelope=11,
        linux_shadow_cap=CapPolicy(value=11, binding=False),
        macos_binding_cap=CapPolicy(value=4, binding=True),
        cleanup_grace_seconds=cleanup_grace_seconds,
        best_effort_survivor_threshold=best_effort_survivor_threshold,
        sample_interval_seconds=sample_interval_seconds,
    )


# ── pressure gates ───────────────────────────────────────────────────

# The enforcement family whose presence makes the Linux <=11 ceiling a
# non-binding shadow input; its absence (macOS-style) is what the binding
# cap responds to. Capability-derived, never OS-name-derived.
_ENFORCEMENT_FAMILY = (
    Capability.LIMITS_MEMORY,
    Capability.LIMITS_PIDS,
    Capability.LIMITS_CPU,
)


def enforcement_guaranteed(report: CapabilityReport) -> bool:
    """True when the backend guarantees the whole enforcement family."""
    return all(
        report.level(cap) == CapabilityLevel.GUARANTEED
        for cap in _ENFORCEMENT_FAMILY
    )


@dataclass(frozen=True)
class GateVerdict:
    """Admission-gate decision."""

    admit: bool
    reason: str | None = None


class PressureGate(Protocol):
    """A live pressure/census gate consulted at admission time.

    A denied request stalls in the FIFO queue (keeping its age) with the
    verdict reason as its stall reason.
    """

    def evaluate(self) -> GateVerdict: ...


# ── Admission controller ─────────────────────────────────────────────


class AdmissionTimeout(Exception):
    """A queued request timed out before admission."""


class AdmissionLease:
    """Held lease for one admitted invocation. Release exactly once.

    Double-release is a fail-closed error — the supervisor's retry loop
    releases in a ``finally`` so the lease is always released exactly once
    per attempt.
    """

    def __init__(self, controller: "AdmissionController") -> None:
        self._controller = controller
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                raise RuntimeError("admission lease released more than once")
            self._released = True
        self._controller._release(self)

    def __enter__(self) -> "AdmissionLease":
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


@dataclass
class _QueuedRequest:
    request: AdmissionRequest
    enqueued_at: float
    stall_reason: str | None = None


class AdmissionController:
    """Daemon-wide FIFO-with-aging admission controller.

    One controller covers every top-level agent invocation across orgs,
    producers, providers, and profiles (later slices wire every
    ``executor.run`` producer to it). Only the queue **head** may be admitted
    (strict FIFO); a head stalled by a pressure gate keeps its age and
    stall reason. Queued cancellation removes the request without launch.

    Aging: requests carry their original enqueue time (preserved across 429
    retries); the controller records queue depth and oldest wait so
    starvation is observable before any scheduler change is considered.
    """

    def __init__(
        self,
        *,
        cap: int,
        gates: Sequence[PressureGate] = (),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if cap < 1:
            raise ValueError("cap must be >= 1")
        self._cap = cap
        self._gates = list(gates)
        self._monotonic = monotonic
        self._cv = threading.Condition(threading.Lock())
        self._queue: deque[_QueuedRequest] = deque()
        self._active = 0
        self._shutdown = False
        self._admitted_total = 0
        self._released_total = 0
        self._cancelled_queued_total = 0

    # ── stats ─────────────────────────────────────────────────────

    def cap(self) -> int:
        return self._cap

    def queue_depth(self) -> int:
        with self._cv:
            return len(self._queue)

    def active_count(self) -> int:
        with self._cv:
            return self._active

    def admitted_total(self) -> int:
        with self._cv:
            return self._admitted_total

    def released_total(self) -> int:
        with self._cv:
            return self._released_total

    def cancelled_queued_total(self) -> int:
        with self._cv:
            return self._cancelled_queued_total

    def oldest_wait(self) -> float:
        """Age (seconds) of the oldest queued request, 0.0 when empty."""
        with self._cv:
            if not self._queue:
                return 0.0
            now = self._monotonic()
            return max(now - e.enqueued_at for e in self._queue)

    def head_stall_reason(self) -> str | None:
        with self._cv:
            return self._queue[0].stall_reason if self._queue else None

    def is_shutdown(self) -> bool:
        with self._cv:
            return self._shutdown

    # ── lifecycle ─────────────────────────────────────────────────

    def acquire(
        self, request: AdmissionRequest, timeout: float | None = None
    ) -> AdmissionLease | None:
        """Block until admitted, cancelled while queued (None), or timeout.

        Returns ``None`` when the request was cancelled while queued or the
        controller is shut down — in both cases nothing launched and no
        handle exists. Raises :class:`AdmissionTimeout` when *timeout*
        elapses while still queued.
        """
        if request.cancellation.cancelled:
            return None
        now = self._monotonic()
        entry = _QueuedRequest(
            request=request,
            enqueued_at=request.enqueued_at if request.enqueued_at is not None else now,
        )
        deadline = None if timeout is None else now + timeout
        # A cancellation fired while queued must wake this wait so the
        # request is removed without launch (no handle, no lease).
        request.cancellation.set_waker(self._notify_all)
        with self._cv:
            if self._shutdown:
                return None
            self._queue.append(entry)
            while True:
                if self._shutdown:
                    self._queue.remove(entry)
                    return None
                if entry is self._queue[0] and self._active < self._cap:
                    verdict = self._gates_verdict_locked()
                    if verdict.admit:
                        self._queue.popleft()
                        self._active += 1
                        self._admitted_total += 1
                        return AdmissionLease(self)
                    entry.stall_reason = verdict.reason
                if request.cancellation.cancelled:
                    self._queue.remove(entry)
                    self._cancelled_queued_total += 1
                    return None
                if deadline is not None:
                    remaining = deadline - self._monotonic()
                    if remaining <= 0:
                        self._queue.remove(entry)
                        raise AdmissionTimeout(
                            f"admission timed out after {timeout}s for "
                            f"{request.logical_id}"
                        )
                else:
                    remaining = None
                self._cv.wait(remaining)

    def _gates_verdict_locked(self) -> GateVerdict:
        for gate in self._gates:
            verdict = gate.evaluate()
            if not verdict.admit:
                return verdict
        return GateVerdict(True)

    def _release(self, lease: AdmissionLease) -> None:
        with self._cv:
            if self._active <= 0:
                raise RuntimeError("admission underflow: released with no active lease")
            self._active -= 1
            self._released_total += 1
            self._cv.notify_all()

    def wake(self) -> None:
        """Re-evaluate the queue head (e.g. after residue reconciliation)."""
        with self._cv:
            self._cv.notify_all()

    def _notify_all(self) -> None:
        with self._cv:
            self._cv.notify_all()

    def shutdown(self) -> None:
        """Stop admission and cancel every queued request.

        Active invocations keep their leases until their own finish; the
        supervisor's drain handles them. Queued waiters observe ``None`` on
        wake (nothing launched, no handle)."""
        with self._cv:
            self._shutdown = True
            self._cv.notify_all()


# ── Residue accounting / reconciliation ──────────────────────────────


@dataclass(frozen=True)
class ReconciliationResult:
    """Outcome of one residue-reconciliation input."""

    accepted: bool
    blocked: bool
    reason: str | None = None


class ResidueAccountant:
    """Capability-conditional residue census + admission pressure gate.

    * Guaranteed-cleanup residue is an anomaly: it marks containment
      unhealthy and blocks admission until explicit reconciliation.
    * Best-effort verified survivors stay censused/charged/visible; they
      block admission only when census/measurement fails or a conservative
      survivor-count threshold is crossed.

    Reconciliation inputs modeled: survivor exit, successful re-probe, and
    operator acknowledgement **after verified cleanup** (unverified
    acknowledgements are rejected fail-closed).
    """

    def __init__(
        self,
        *,
        policy: PolicySnapshot,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._policy = policy
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._survivors: dict[tuple[int, str], SurvivorRecord] = {}
        self._measurement_healthy = True
        self._gate_reason: str | None = None
        self._waker: Callable[[], None] | None = None

    def set_waker(self, waker: Callable[[], None]) -> None:
        self._waker = waker

    # ── census ────────────────────────────────────────────────────

    def census(self) -> tuple[SurvivorRecord, ...]:
        """Identity-verified survivors currently censused/charged/visible."""
        with self._lock:
            return tuple(self._survivors.values())

    def account(
        self, receipt: Receipt, capabilities: Sequence[Capability]
    ) -> None:
        """Record residue from a finished attempt per capability semantics."""
        with self._lock:
            if not receipt.survivors:
                return
            for sv in receipt.survivors:
                self._survivors[sv.key] = sv
            guaranteed = (
                Capability.KILLS_TREE_GUARANTEED in capabilities
            )
            if guaranteed:
                self._gate_reason = "guaranteed_cleanup_residue"
            else:
                self._recompute_best_effort_block_locked()
        self._wake()

    def note_measurement_failure(self, reason: str = "measurement_unhealthy") -> None:
        """Census/measurement is unhealthy — tighten admission (fail-closed)."""
        with self._lock:
            self._measurement_healthy = False
            self._gate_reason = reason
        self._wake()

    # ── pressure gate ─────────────────────────────────────────────

    def evaluate(self) -> GateVerdict:
        with self._lock:
            reason = self._gate_reason
        if reason is not None:
            return GateVerdict(False, reason)
        return GateVerdict(True)

    # ── reconciliation inputs ─────────────────────────────────────

    def handle_survivor_exit(self, pid: int, start_identity: str) -> ReconciliationResult:
        """A censused survivor exited; re-evaluate the block."""
        with self._lock:
            removed = self._survivors.pop((pid, start_identity), None)
            if removed is None:
                return ReconciliationResult(
                    accepted=False, blocked=self._is_blocked_locked(), reason=self._gate_reason
                )
            self._recompute_block_locked()
            blocked = self._is_blocked_locked()
            reason = self._gate_reason
        self._wake()
        return ReconciliationResult(accepted=True, blocked=blocked, reason=reason)

    def handle_successful_reprobe(
        self, report: CapabilityReport | None = None
    ) -> ReconciliationResult:
        """A re-probe verified cleanup; clear residue and unblock."""
        with self._lock:
            if report is not None and not report.healthy:
                return ReconciliationResult(
                    accepted=False, blocked=True, reason="reprobe_unhealthy"
                )
            self._survivors.clear()
            self._measurement_healthy = True
            self._gate_reason = None
            blocked = False
        self._wake()
        return ReconciliationResult(accepted=True, blocked=blocked)

    def handle_operator_acknowledgement(
        self,
        pid: int,
        start_identity: str,
        *,
        verified_cleanup_evidence: str,
    ) -> ReconciliationResult:
        """Operator acknowledges a survivor after **verified** cleanup.

        Unverified acknowledgements are rejected fail-closed — an unhealthy
        state must have a defined, evidence-backed way out."""
        if not verified_cleanup_evidence.strip():
            with self._lock:
                return ReconciliationResult(
                    accepted=False,
                    blocked=self._is_blocked_locked(),
                    reason="operator_ack_requires_verified_cleanup_evidence",
                )
        with self._lock:
            removed = self._survivors.pop((pid, start_identity), None)
            if removed is None:
                return ReconciliationResult(
                    accepted=False, blocked=self._is_blocked_locked(), reason="unknown_survivor"
                )
            self._recompute_block_locked()
            blocked = self._is_blocked_locked()
            reason = self._gate_reason
        self._wake()
        return ReconciliationResult(accepted=True, blocked=blocked, reason=reason)

    # ── internals ─────────────────────────────────────────────────

    def _recompute_block_locked(self) -> None:
        if self._gate_reason == "guaranteed_cleanup_residue":
            # Guaranteed residue clears only when no survivor remains.
            if not self._survivors:
                self._gate_reason = None
            return
        self._recompute_best_effort_block_locked()

    def _recompute_best_effort_block_locked(self) -> None:
        if not self._measurement_healthy:
            self._gate_reason = "measurement_unhealthy"
        elif len(self._survivors) > self._policy.best_effort_survivor_threshold:
            self._gate_reason = "survivor_threshold_exceeded"
        else:
            self._gate_reason = None

    def _is_blocked_locked(self) -> bool:
        return self._gate_reason is not None

    def _wake(self) -> None:
        waker = self._waker
        if waker is not None:
            waker()


# ── Launch result & outcome ──────────────────────────────────────────


@dataclass(frozen=True)
class LaunchResult:
    """Result of the executor launch body (communicate + parse).

    The supervisor maps this to a frozen :class:`TerminalReason`:
    ``timed_out`` -> TIMEOUT; ``rate_limited and not success`` ->
    RATE_LIMITED (retry-worthy); ``success`` -> SUCCESS; else FAILURE.
    """

    success: bool
    duration_seconds: float
    returncode: int | None = None
    error: str | None = None
    rate_limited: bool = False
    timed_out: bool = False
    payload: Any = None


@dataclass
class SessionOutcome:
    """Terminal outcome of one logical invocation (possibly multi-attempt)."""

    request: AdmissionRequest
    terminal_reason: TerminalReason
    attempt: int
    receipt: Receipt | None = None
    cleanup_status: CleanupStatus | None = None
    cleanup_error: str | None = None
    error: str | None = None
    retry_worthy: bool = False
    cancelled_while_queued: bool = False
    payload: Any = None


# ── Per-attempt state machine ────────────────────────────────────────


class _AttemptContext:
    """Serializes terminal-reason freezing and idempotent finish.

    Finish/cancel races are resolved by first-wins terminal freezing plus a
    once-guard on ``finish_once``: whichever thread arrives second blocks
    until the in-flight finish lands its receipt and returns the same
    receipt. Release of the admission lease is owned by the run loop, not
    this context.
    """

    def __init__(self, logical_id: str, monotonic: Callable[[], float]) -> None:
        self._logical_id = logical_id
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._terminal_reason: TerminalReason | None = None
        self._error: str | None = None
        self._cleanup_error: str | None = None
        self._launch_result: LaunchResult | None = None
        self._retry_worthy = False
        self._running: RunningHandle | None = None
        self._grace_seconds: float = 5.0
        self._finish_done = False
        self._finish_in_progress = False
        self._finalized = False
        self._receipt: Receipt | None = None
        self._samples: list[ResourceSample] = []
        self._sample_errors = 0
        self._started_at: float = monotonic()

    # ── terminal freeze ───────────────────────────────────────────

    def freeze_terminal(self, reason: TerminalReason) -> TerminalReason:
        """Freeze the primary terminal reason (first wins)."""
        with self._lock:
            if self._terminal_reason is None:
                self._terminal_reason = reason
            return self._terminal_reason

    def terminal_reason(self) -> TerminalReason | None:
        with self._lock:
            return self._terminal_reason

    def set_error(self, error: str) -> None:
        with self._lock:
            self._error = error

    def error(self) -> str | None:
        with self._lock:
            return self._error

    def cleanup_error(self) -> str | None:
        with self._lock:
            return self._cleanup_error

    # ── launch result ─────────────────────────────────────────────

    def set_launch_result(self, result: LaunchResult) -> None:
        with self._lock:
            self._launch_result = result
            if result.timed_out:
                reason = TerminalReason.TIMEOUT
            elif result.rate_limited and not result.success:
                reason = TerminalReason.RATE_LIMITED
            elif result.success:
                reason = TerminalReason.SUCCESS
            else:
                reason = TerminalReason.FAILURE
            if self._terminal_reason is None:
                self._terminal_reason = reason
            # Retry only when RATE_LIMITED actually won the terminal freeze —
            # a concurrent cancellation that froze first must not relaunch.
            self._retry_worthy = (
                result.rate_limited
                and not result.success
                and self._terminal_reason is TerminalReason.RATE_LIMITED
            )

    def launch_result(self) -> LaunchResult | None:
        with self._lock:
            return self._launch_result

    def retry_worthy(self) -> bool:
        with self._lock:
            return self._retry_worthy

    # ── running handle / grace ────────────────────────────────────

    def bind_running(self, running: RunningHandle, grace_seconds: float) -> None:
        with self._lock:
            self._running = running
            self._grace_seconds = grace_seconds

    def running(self) -> RunningHandle | None:
        with self._lock:
            return self._running

    def grace_seconds(self) -> float:
        with self._lock:
            return self._grace_seconds

    # ── sampling sink ─────────────────────────────────────────────

    def add_sample(self, sample: ResourceSample) -> None:
        with self._lock:
            self._samples.append(sample)

    def note_sample_error(self) -> None:
        with self._lock:
            self._sample_errors += 1

    def sample_failed(self) -> bool:
        with self._lock:
            return self._sample_errors > 0

    def samples_snapshot(self) -> tuple[ResourceSample, ...]:
        with self._lock:
            return tuple(self._samples)

    def wall_time_seconds(self) -> float:
        return self._monotonic() - self._started_at

    def receipt(self) -> Receipt | None:
        with self._lock:
            return self._receipt

    # ── idempotent finish ─────────────────────────────────────────

    def finish_once(
        self,
        backend: SessionBackend,
        grace_seconds: float | None = None,
    ) -> Receipt | None:
        """Run backend teardown exactly once per attempt; block stragglers.

        Returns the resulting receipt (or ``None`` when teardown raised —
        the primary terminal reason is preserved separately). Concurrent
        callers (finish/cancel race) wait for the in-flight finish and see
        the same receipt."""
        grace = self._grace_seconds if grace_seconds is None else grace_seconds
        with self._cond:
            if self._finish_done:
                while self._finish_in_progress:
                    self._cond.wait()
                return self._receipt
            self._finish_done = True
            self._finish_in_progress = True
            reason = self._terminal_reason or TerminalReason.FAILURE
        started = self._monotonic()
        try:
            receipt = backend.finish(
                self._running,  # type: ignore[arg-type]  # caller ensures running bound
                str(reason),
                grace,
                samples=self.samples_snapshot(),
            )
        except Exception as exc:
            receipt = None
            self._cleanup_error = str(exc)
        finally:
            with self._cond:
                self._finish_in_progress = False
                if receipt is not None:
                    self._receipt = receipt
                self._cond.notify_all()
        return receipt

    def finalize_once(
        self,
        accountant: "ResidueAccountant",
        capabilities: Sequence[Capability],
        publisher: Callable[[Receipt], None],
    ) -> None:
        """Residue-account + publish the receipt exactly once per attempt.

        Guarded separately from ``finish_once`` so whichever thread arrives
        last (run path or cancel path) publishes exactly once."""
        with self._cond:
            if self._finalized:
                return
            self._finalized = True
        receipt = self._receipt
        if receipt is None:
            return
        accountant.account(receipt, capabilities)
        if self.sample_failed():
            # Census/measurement genuinely failed: tighten admission (fail-closed).
            accountant.note_measurement_failure()
        elif (
            receipt.cleanup_status == CleanupStatus.INCOMPLETE
            and Capability.KILLS_TREE_GUARANTEED in capabilities
            and not receipt.survivors
        ):
            # Guaranteed teardown that could not be verified AND produced no
            # verified census is an anomaly. Best-effort INCOMPLETE is an
            # expected outcome for verified detached survivors: ``account``
            # keeps them censused/charged/visible and blocks only on actual
            # census/measurement failure or the conservative threshold.
            accountant.note_measurement_failure()
        publisher(receipt)


# ── sampler thread ───────────────────────────────────────────────────


class _Sampler:
    """Daemon thread sampling the live descendant tree at a bounded interval.

    Samples land in the attempt context; the backend merges them into the
    receipt at finish time. A raising sampler is recorded as a measurement
    failure (fail-closed: missing enforcement tightens admission)."""

    def __init__(
        self,
        *,
        backend: SessionBackend,
        running: RunningHandle,
        sampler: Callable[[RunningHandle], ResourceSample],
        interval_seconds: float,
        ctx: _AttemptContext,
        monotonic: Callable[[], float],
    ) -> None:
        self._backend = backend
        self._running = running
        self._sampler = sampler
        self._interval = interval_seconds
        self._ctx = ctx
        self._monotonic = monotonic
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop, name=f"hr-sampler-{running.request_id}", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                sample = self._sampler(self._running)
            except Exception:
                self._ctx.note_sample_error()
                # A broken sampler must not spin forever — stop sampling.
                return
            self._ctx.add_sample(sample)
            self._stop.wait(self._interval)


# ── opaque cancel control ────────────────────────────────────────────


class _OpaqueCancelControl:
    """Cancellation goes through the containment handle, never a bare PID.

    ``invoke`` freezes CANCELLED (first-wins) and, once the launch fence has
    been passed, drives the idempotent backend teardown — which is what
    terminates the descendant tree and unblocks the executor's communicate
    loop. Before the fence, a winning cancellation only freezes the terminal
    reason: the caller's ``fence_launch`` observes it and abandons the
    pending handle without launching, so cancellation/registration is atomic
    with respect to launch."""

    def __init__(self, supervisor: "HostSessionSupervisor", ctx: _AttemptContext) -> None:
        self._supervisor = supervisor
        self._ctx = ctx
        self._launch_lock = threading.Lock()
        self._launched = False

    def invoke(self) -> None:
        ctx = self._ctx
        ctx.freeze_terminal(TerminalReason.CANCELLED)
        with self._launch_lock:
            launched = self._launched
        if launched:
            self._supervisor._finish_and_reconcile(ctx)

    def fence_launch(self) -> bool:
        """Atomically gate the launch body against a winning cancellation.

        Returns ``True`` when launch may proceed (this call won the race);
        ``False`` when cancellation already won — the caller must abandon
        the pending handle and must not execute the launch body. The fence
        is one-shot: once passed, the handle is launched and a later
        cancellation drives idempotent teardown through ``invoke``."""
        with self._launch_lock:
            if self._ctx.terminal_reason() is TerminalReason.CANCELLED:
                return False
            self._launched = True
            return True


# ── the supervisor ───────────────────────────────────────────────────


class HostSessionSupervisor:
    """Owns admission + containment ordering for one logical invocation.

    Constructed once per daemon process in a later slice; this slice
    exercises it with dependency-injected backend/measurement/publisher
    fakes. The backend is injected (the daemon selects it by platform via
    the backend factory in a later slice); the publisher receives bounded
    receipts for later projection into health/metrics/audit payloads.
    """

    def __init__(
        self,
        *,
        backend: SessionBackend,
        policy: PolicySnapshot,
        publisher: Callable[[Receipt], None],
        admission: AdmissionController | None = None,
        residue: ResidueAccountant | None = None,
        sampler: Callable[[RunningHandle], ResourceSample] | None = None,
        sample_interval_seconds: float | None = None,
        max_retry_attempts: int = 0,
        backoff_seconds: Sequence[float] = (),
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_retry_attempts < 0:
            raise ValueError("max_retry_attempts must be >= 0")
        self._backend = backend
        self._policy = policy
        self._publisher = publisher
        self._monotonic = monotonic
        self._sleep = sleep
        self._max_retry_attempts = max_retry_attempts
        self._backoff_seconds = tuple(backoff_seconds)
        # Cached capability report (probe at startup/refresh, not per session)
        # must exist before the admission cap is resolved below.
        self._capability_report: CapabilityReport | None = None
        self._capability_lock = threading.Lock()
        self._residue = residue or ResidueAccountant(policy=policy, monotonic=monotonic)
        self._admission = admission or AdmissionController(
            cap=policy.effective_cap(
                enforcement_guaranteed=enforcement_guaranteed(self.probe())
            ),
            gates=(self._residue,),
            monotonic=monotonic,
        )
        self._residue.set_waker(self._admission.wake)
        if sampler is not None:
            self._sampler: Callable[[RunningHandle], ResourceSample] | None = sampler
            self._sample_interval_seconds = (
                policy.sample_interval_seconds
                if sample_interval_seconds is None
                else sample_interval_seconds
            )
        else:
            self._sampler = None
            self._sample_interval_seconds = 0.0
        # Active attempt contexts for bounded drain on shutdown.
        self._active: dict[str, _AttemptContext] = {}
        self._active_lock = threading.Lock()

    # ── capability probing ────────────────────────────────────────

    def probe(self) -> CapabilityReport:
        """Probe (or return the cached) backend capability report.

        A failed probe degrades to an empty capability set — missing
        enforcement tightens admission rather than silently widening it."""
        with self._capability_lock:
            if self._capability_report is not None:
                return self._capability_report
            try:
                report = self._backend.probe()
            except Exception as exc:
                report = CapabilityReport(
                    backend=getattr(self._backend, "name", "unknown"),
                    backend_version="unknown",
                    evidence=f"probe failed: {exc}",
                    healthy=False,
                )
            self._capability_report = report
            return report

    def _backend_capabilities(self) -> Sequence[Capability]:
        report = self.probe()
        return tuple(
            cap
            for cap, level in report.capabilities.items()
            if level == CapabilityLevel.GUARANTEED
        )

    # ── run ───────────────────────────────────────────────────────

    def run(
        self,
        request: AdmissionRequest,
        *,
        launch_spec: LaunchSpec,
        launch_body: Callable[[RunningHandle], LaunchResult],
        grace_seconds: float | None = None,
        timeout: float | None = None,
    ) -> SessionOutcome:
        """Run one logical invocation to a terminal outcome.

        Load-bearing ordering: admission acquire -> prepare -> launch ->
        launch body -> freeze terminal result -> finish containment ->
        residue accounting/reconciliation -> publish receipt -> release lease
        (exactly once, in ``finally``). A retry-worthy (429) result finishes
        the attempt fully, releases the lease, sleeps **without capacity**,
        then re-enters admission with the original enqueue age and a fresh
        containment handle."""

        grace = self._policy.cleanup_grace_seconds if grace_seconds is None else grace_seconds
        attempt = 0
        while True:
            lease = self._admission.acquire(request, timeout=timeout)
            if lease is None:
                return SessionOutcome(
                    request=request,
                    terminal_reason=TerminalReason.CANCELLED,
                    attempt=attempt,
                    cancelled_while_queued=True,
                )
            try:
                outcome = self._execute_attempt(
                    request,
                    launch_spec=launch_spec,
                    launch_body=launch_body,
                    grace_seconds=grace,
                    attempt=attempt,
                )
            finally:
                lease.release()
            if outcome.retry_worthy and attempt < self._max_retry_attempts:
                attempt += 1
                idx = attempt - 1
                backoff = self._backoff_seconds[idx] if idx < len(self._backoff_seconds) else 0.0
                if backoff > 0:
                    self._sleep(backoff)
                request = request.with_retry_attempt(attempt)
                continue
            return outcome

    # ── attempt execution ─────────────────────────────────────────

    def _execute_attempt(
        self,
        request: AdmissionRequest,
        *,
        launch_spec: LaunchSpec,
        launch_body: Callable[[RunningHandle], LaunchResult],
        grace_seconds: float,
        attempt: int,
    ) -> SessionOutcome:
        ctx = _AttemptContext(request.logical_id, self._monotonic)
        self._register_active(request.logical_id, ctx)
        try:
            # ── prepare (never before admission) ──────────────────
            try:
                pending = self._backend.prepare(request, self._policy)
            except BackendError as exc:
                ctx.freeze_terminal(TerminalReason.PREPARE_FAILURE)
                ctx.set_error(str(exc))
                return self._outcome(request, ctx, attempt)
            if request.cancellation.cancelled:
                self._safe_abandon(self._backend, pending)
                ctx.freeze_terminal(TerminalReason.CANCELLED)
                ctx.set_error("cancelled before launch")
                return self._outcome(request, ctx, attempt)
            control = _OpaqueCancelControl(self, ctx)
            fired = request.cancellation.register(control)
            # Launch fence: cancellation that wins before launch (already fired at
            # registration, or landed between registration and this fence) abandons
            # the pending handle and must not execute the launch body.
            if fired or not control.fence_launch():
                self._safe_abandon(self._backend, pending)
                ctx.freeze_terminal(TerminalReason.CANCELLED)
                ctx.set_error("cancelled before launch")
                return self._outcome(request, ctx, attempt)

            # ── launch (still post-admission, post-fence) ──
            try:
                running = self._backend.launch(pending, launch_spec)
            except BackendError as exc:
                self._safe_abandon(self._backend, pending)
                ctx.freeze_terminal(TerminalReason.SPAWN_FAILURE)
                ctx.set_error(str(exc))
                # Partial containment must still be torn down/verified.
                return self._outcome(request, ctx, attempt)
            ctx.bind_running(running, grace_seconds)
            if request.on_started is not None:
                request.on_started(running.root_pid)

            # ── sampling ──────────────────────────────────────────
            sampler = self._start_sampler(running, ctx)

            # ── launch body ───────────────────────────────────────
            try:
                result = launch_body(running)
            except Exception as exc:  # launch body never replaces terminal state
                ctx.set_error(f"launch body raised: {exc}")
                ctx.freeze_terminal(TerminalReason.FAILURE)
            else:
                ctx.set_launch_result(result)
            finally:
                self._stop_sampler(sampler, ctx)

            # ── finish + residue + publish (idempotent w/ cancel) ─
            self._finish_and_reconcile(ctx)
            return self._outcome(request, ctx, attempt)
        finally:
            self._unregister_active(request.logical_id)

    def _finish_and_reconcile(self, ctx: _AttemptContext) -> None:
        """Idempotent terminal teardown + reconciliation + publish.

        Safe to call from both the run path and the opaque cancel control:
        ``finish_once`` runs backend teardown exactly once (stragglers wait
        for the in-flight finish), and ``finalize_once`` accounts residue and
        publishes the bounded receipt exactly once."""
        if ctx.running() is None:
            # No live containment (e.g. spawn failure) — nothing to finish;
            # the backend already abandoned partial state.
            return
        ctx.finish_once(self._backend)
        ctx.finalize_once(
            self._residue, self._backend_capabilities(), self._publisher
        )
        if ctx.cleanup_error() is not None:
            # Teardown raised: the primary terminal reason is preserved in
            # the outcome; the lease is still released exactly once, and
            # missing verification tightens admission (fail-closed).
            self._residue.note_measurement_failure("cleanup_failed")

    def _outcome(
        self,
        request: AdmissionRequest,
        ctx: _AttemptContext,
        attempt: int,
    ) -> SessionOutcome:
        receipt = ctx.receipt()
        return SessionOutcome(
            request=request,
            terminal_reason=ctx.terminal_reason() or TerminalReason.FAILURE,
            attempt=attempt,
            receipt=receipt,
            cleanup_status=receipt.cleanup_status if receipt is not None else None,
            cleanup_error=ctx.cleanup_error(),
            error=ctx.error(),
            retry_worthy=ctx.retry_worthy(),
            payload=ctx.launch_result(),
        )

    @staticmethod
    def _safe_abandon(backend: SessionBackend, pending: PendingHandle) -> None:
        """Best-effort teardown of a prepared-but-not-launched handle."""
        try:
            backend.abandon(pending)
        except Exception as exc:
            logger.warning("backend.abandon(%r) raised: %s", pending.token, exc)

    # ── active registry / drain / shutdown ───────────────────────

    def _register_active(self, logical_id: str, ctx: _AttemptContext) -> None:
        with self._active_lock:
            self._active[logical_id] = ctx

    def _unregister_active(self, logical_id: str) -> None:
        with self._active_lock:
            self._active.pop(logical_id, None)

    def active_count(self) -> int:
        with self._active_lock:
            return len(self._active)

    def shutdown(self) -> None:
        """Stop admission, cancel queued, and finish active handles.

        Active attempts finish with SHUTDOWN as the (frozen, first-wins)
        terminal reason; their run loops release the lease in ``finally``."""
        self._admission.shutdown()
        with self._active_lock:
            contexts = list(self._active.values())
        for ctx in contexts:
            ctx.freeze_terminal(TerminalReason.SHUTDOWN)
            if ctx.running() is not None:
                ctx.finish_once(self._backend)

    # ── sampler helpers ───────────────────────────────────────────

    def _start_sampler(self, running: RunningHandle, ctx: _AttemptContext) -> _Sampler | None:
        if self._sampler is None or self._sample_interval_seconds <= 0:
            return None
        sampler = _Sampler(
            backend=self._backend,
            running=running,
            sampler=self._sampler,
            interval_seconds=self._sample_interval_seconds,
            ctx=ctx,
            monotonic=self._monotonic,
        )
        sampler.start()
        return sampler

    def _stop_sampler(self, sampler: _Sampler | None, ctx: _AttemptContext) -> None:
        if sampler is not None:
            sampler.stop()
