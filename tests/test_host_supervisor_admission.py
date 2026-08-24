"""Unit tests for the daemon-wide FIFO-with-aging admission core (Slice A).

Covers ``AdmissionController``, ``AdmissionRequest``/``CancellationToken``,
and the ``PolicySnapshot`` cap model: strict FIFO admission, queued
cancellation without launch, pressure-gate stalls, aging preserved across
retry, shutdown semantics, exactly-once lease release, and the capability-
derived effective cap (Linux <=11 shadow non-binding / macOS 4 binding).
"""
from __future__ import annotations

import threading
import time

import pytest

from runtime.orchestrator.host_supervisor import (
    AdmissionController,
    AdmissionLease,
    AdmissionRequest,
    AdmissionTimeout,
    CancellationToken,
    CapPolicy,
    GateVerdict,
    PolicySnapshot,
    canary_policy,
)


def make_request(logical_id: str = "r1", **kw) -> AdmissionRequest:
    return AdmissionRequest(
        org="happyranch",
        invocation_kind="task",
        logical_id=logical_id,
        executor_profile="claude",
        **kw,
    )


def make_policy(**kw) -> PolicySnapshot:
    base = dict(
        global_session_cap=2,
        producer_envelope=11,
        linux_shadow_cap=CapPolicy(value=11, binding=False),
        macos_binding_cap=CapPolicy(value=4, binding=True),
        cleanup_grace_seconds=5.0,
        best_effort_survivor_threshold=3,
    )
    base.update(kw)
    return PolicySnapshot(**base)


class FlagGate:
    """A mutable pressure gate for deterministic stall tests."""

    def __init__(self, admit: bool = True, reason: str | None = None) -> None:
        self.admit = admit
        self.reason = reason

    def evaluate(self) -> GateVerdict:
        return GateVerdict(self.admit, self.reason)


def _run_acquire(controller, request, results, timeout=5.0):
    try:
        lease = controller.acquire(request, timeout=timeout)
    except AdmissionTimeout as exc:
        results.append(("timeout", str(exc)))
        return
    results.append(("lease", lease))
    if lease is not None:
        lease.release()


# ── FIFO ordering ────────────────────────────────────────────────────


def test_fifo_only_head_may_be_admitted():
    ctl = AdmissionController(cap=1, monotonic=time.monotonic)
    first = ctl.acquire(make_request("a"))
    assert first is not None

    results: list = []
    t = threading.Thread(target=_run_acquire, args=(ctl, make_request("b"), results))
    t.start()
    # B is queued behind A; only the head may be admitted.
    deadline = time.monotonic() + 5
    while ctl.queue_depth() != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert ctl.queue_depth() == 1
    assert ctl.active_count() == 1
    assert results == []

    first.release()
    t.join(timeout=5)
    assert not t.is_alive()
    assert results[0][0] == "lease"
    assert ctl.active_count() == 0
    assert ctl.released_total() == 2


def test_admission_order_is_enqueue_order():
    ctl = AdmissionController(cap=3, monotonic=time.monotonic)
    leases = [ctl.acquire(make_request(f"r{i}")) for i in range(3)]
    try:
        assert ctl.admitted_total() == 3
        assert ctl.queue_depth() == 0
    finally:
        for lease in leases:
            lease.release()
    assert ctl.released_total() == 3


# ── queued cancellation ──────────────────────────────────────────────


def test_queued_cancellation_removes_without_admission():
    ctl = AdmissionController(cap=1, monotonic=time.monotonic)
    holder = ctl.acquire(make_request("a"))
    assert holder is not None

    token = CancellationToken()
    results: list = []
    t = threading.Thread(
        target=_run_acquire, args=(ctl, make_request("b", cancellation=token), results)
    )
    t.start()
    deadline = time.monotonic() + 5
    while ctl.queue_depth() != 1 and time.monotonic() < deadline:
        time.sleep(0.005)

    token.cancel()
    t.join(timeout=5)
    assert not t.is_alive()
    # Cancelled while queued: no lease, nothing admitted, no launch path.
    assert results == [("lease", None)]
    assert ctl.queue_depth() == 0
    assert ctl.active_count() == 1  # holder unaffected
    assert ctl.cancelled_queued_total() == 1
    holder.release()


def test_cancel_after_admission_does_not_release_lease():
    """Cancelling an already-admitted request must not touch the lease —
    lease lifecycle is owned by the run loop."""
    ctl = AdmissionController(cap=1, monotonic=time.monotonic)
    token = CancellationToken()
    lease = ctl.acquire(make_request("a", cancellation=token))
    assert lease is not None
    token.cancel()
    assert ctl.active_count() == 1  # lease still held
    lease.release()
    assert ctl.active_count() == 0


# ── pressure gates / aging ───────────────────────────────────────────


def test_pressure_gate_stall_records_reason_and_releases_on_recovery():
    gate = FlagGate(admit=False, reason="guaranteed_cleanup_residue")
    ctl = AdmissionController(cap=1, gates=(gate,), monotonic=time.monotonic)

    results: list = []
    t = threading.Thread(target=_run_acquire, args=(ctl, make_request("a"), results))
    t.start()
    deadline = time.monotonic() + 5
    while ctl.head_stall_reason() is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert ctl.head_stall_reason() == "guaranteed_cleanup_residue"
    assert results == []  # still queued (aged, not failed)

    gate.admit = True
    ctl.wake()
    t.join(timeout=5)
    assert not t.is_alive()
    assert results[0][0] == "lease"
    assert ctl.active_count() == 0  # helper releases immediately


def test_aging_is_preserved_across_retry_reentry():
    """A 429 retry re-enters admission with its ORIGINAL enqueue age."""
    original_age = 100.0
    base = make_request("a", enqueued_at=original_age)
    retried = base.with_retry_attempt(1)
    assert retried.enqueued_at == original_age
    assert retried.retry_attempt == 1
    assert retried.logical_id == base.logical_id
    assert retried.cancellation is base.cancellation

    gate = FlagGate(admit=False, reason="stall")
    ctl = AdmissionController(cap=1, gates=(gate,), monotonic=time.monotonic)
    results: list = []
    t = threading.Thread(
        target=_run_acquire, args=(ctl, retried, results, 0.5)
    )
    t.start()
    # While stalled, the queue keeps the ORIGINAL enqueue age (~100s).
    deadline = time.monotonic() + 5
    while ctl.queue_depth() != 1 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert ctl.queue_depth() == 1
    assert ctl.oldest_wait() >= 100.0
    t.join(timeout=5)
    # The stall is a backpressure wait, not a failure: it timed out.
    assert results[0][0] == "timeout"


def test_admission_timeout_removes_queued_request():
    gate = FlagGate(admit=False, reason="stall")
    ctl = AdmissionController(cap=1, gates=(gate,), monotonic=time.monotonic)
    with pytest.raises(AdmissionTimeout):
        ctl.acquire(make_request("a"), timeout=0.05)
    assert ctl.queue_depth() == 0
    assert ctl.active_count() == 0


# ── shutdown ─────────────────────────────────────────────────────────


def test_shutdown_cancels_queued_and_preserves_active_leases():
    ctl = AdmissionController(cap=1, monotonic=time.monotonic)
    holder = ctl.acquire(make_request("a"))
    assert holder is not None

    results: list = []
    t = threading.Thread(target=_run_acquire, args=(ctl, make_request("b"), results))
    t.start()
    deadline = time.monotonic() + 5
    while ctl.queue_depth() != 1 and time.monotonic() < deadline:
        time.sleep(0.005)

    ctl.shutdown()
    t.join(timeout=5)
    assert not t.is_alive()
    assert results == [("lease", None)]  # queued cancelled, nothing launched
    assert ctl.is_shutdown()
    assert ctl.active_count() == 1  # active lease unaffected
    holder.release()
    assert ctl.active_count() == 0


def test_acquire_after_shutdown_returns_none():
    ctl = AdmissionController(cap=1, monotonic=time.monotonic)
    ctl.shutdown()
    assert ctl.acquire(make_request("a")) is None


# ── exactly-once release ─────────────────────────────────────────────


def test_double_release_fails_closed():
    ctl = AdmissionController(cap=1, monotonic=time.monotonic)
    lease = ctl.acquire(make_request("a"))
    assert lease is not None
    lease.release()
    with pytest.raises(RuntimeError):
        lease.release()


def test_release_underflow_fails_closed():
    ctl = AdmissionController(cap=1, monotonic=time.monotonic)
    with pytest.raises(RuntimeError):
        ctl._release(AdmissionLease(ctl))  # no active lease to release


# ── policy cap model ─────────────────────────────────────────────────


def test_linux_shadow_cap_is_non_binding():
    """Linux <=11 is a non-binding shadow input: with enforcement guaranteed
    the effective cap is the producer envelope, never reduced by the shadow."""
    p = canary_policy(global_session_cap=11)
    assert p.linux_shadow_cap == CapPolicy(value=11, binding=False)
    assert p.effective_cap(enforcement_guaranteed=True) == 11
    # Even a global cap above the envelope must not be reduced by the shadow.
    p2 = canary_policy(global_session_cap=16)
    assert p2.effective_cap(enforcement_guaranteed=True) == 16


def test_macos_binding_cap_applies_only_when_enforcement_missing():
    p = canary_policy(global_session_cap=11)
    assert p.macos_binding_cap == CapPolicy(value=4, binding=True)
    assert p.effective_cap(enforcement_guaranteed=False) == 4
    # With enforcement present the binding cap does not apply.
    assert p.effective_cap(enforcement_guaranteed=True) == 11


def test_policy_snapshot_is_frozen_and_validated():
    p = make_policy()
    with pytest.raises(AttributeError):
        p.global_session_cap = 99  # type: ignore[misc]

    # Linux shadow must be non-binding and <= 11.
    with pytest.raises(ValueError):
        make_policy(linux_shadow_cap=CapPolicy(value=11, binding=True))
    with pytest.raises(ValueError):
        make_policy(linux_shadow_cap=CapPolicy(value=12, binding=False))
    # macOS cap must be a binding input.
    with pytest.raises(ValueError):
        make_policy(macos_binding_cap=CapPolicy(value=4, binding=False))
    # Grace must be positive; canary inputs are never derived from the host.
    with pytest.raises(ValueError):
        make_policy(cleanup_grace_seconds=0.0)
