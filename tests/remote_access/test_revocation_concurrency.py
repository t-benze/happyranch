"""Concurrent revocation — barrier-controlled race matrix (TASK-5867).

TASK-5867 found that ``StreamRegistry.close_all`` published revoked/sealed
state before the first caller's transport cleanup reached a terminal result:
a racing higher-epoch ``RevocationCoordinator.revoke`` treated closure as a
successful idempotent no-op, applied its epoch, and returned success, while
the lower-epoch caller's blocked close failure was lost behind a rollback —
neither caller surfaced ``RevocationIncomplete`` or the failed stream id.

The corrected contract (governing spec §9):

- The cleanup terminal result is SHARED/PERSISTED across concurrent
  revocations: the first caller seals and runs the transport cleanup exactly
  once; concurrent callers serialize on the authoritative transaction and
  observe the same persisted outcome.
- NO caller may return success while an in-flight or completed cleanup
  failure relevant to the sealed generation is unreported: every revoke that
  would otherwise succeed re-surfaces the persisted failed ids as
  ``RevocationIncomplete``.
- Seal-first fail-closed byte safety, monotonic epochs, rollback-before-
  side-effects, idempotency, and deterministic outcomes are preserved.

These tests reproduce the reviewer's exact barrier-controlled blocked-close
race and the full adversarial matrix (lower-vs-higher orderings, same epoch,
multiple waiters, successful and failing cleanup, partial multi-handle
failure, repeated calls after terminal cleanup).
"""
from __future__ import annotations

import threading
from datetime import timedelta

import pytest

from runtime.remote_access.authorization import TrustState
from runtime.remote_access.revocation import RevocationCoordinator, RevocationIncomplete
from runtime.remote_access.streams import StreamCloseError, StreamClosed, StreamRegistry

from .conftest import NOW, default_authorization_state


class _BlockingClose:
    """A transport handle whose close() blocks on a test-controlled release
    and then raises — the exact barrier-controlled blocked-close shape."""

    def __init__(self, stream_id: str, release: threading.Event, entered: threading.Event, *, raise_on_close: bool = True) -> None:
        self.stream_id = stream_id
        self._release = release
        self._entered = entered
        self._raise_on_close = raise_on_close

    def receive(self) -> bytes | None:
        return b"still-live"

    def close(self) -> None:
        self._entered.set()
        assert self._release.wait(timeout=15), "test harness: close never released"
        if self._raise_on_close:
            raise OSError("transport close exploded after barrier")

    @property
    def closed(self) -> bool:
        return False


class _ExplodingClose:
    """A transport handle whose close() raises immediately."""

    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id

    def receive(self) -> bytes | None:
        return b"still-live"

    def close(self) -> None:
        raise OSError("transport close exploded")

    @property
    def closed(self) -> bool:
        return False


def _record(coordinator: RevocationCoordinator, epoch: int, results: dict, tag: str) -> None:
    """Run one revoke and record a stable outcome tuple."""
    try:
        results[tag] = ("ok", coordinator.revoke(epoch))
    except RevocationIncomplete as exc:
        results[tag] = ("incomplete", exc.applied_epoch, exc.stream_ids)
    except ValueError:
        results[tag] = ("rollback",)
    except RuntimeError:
        results[tag] = ("reentrant",)


def _run_race(epochs: list[tuple[str, int]], *, failing: bool = True) -> tuple[dict, TrustState, StreamRegistry, object, threading.Event]:
    """Drive a barrier-controlled race: the first worker's close blocks until
    every worker has been started, then the release fires.

    Returns (results, state, registry, tracked_wrapper, release).
    """
    state = default_authorization_state()
    registry = StreamRegistry()
    coordinator = RevocationCoordinator(state, registry)
    release = threading.Event()
    entered = threading.Event()
    tracked = registry.open("s-race", _BlockingClose("s-race", release, entered, raise_on_close=failing))
    results: dict = {}
    threads = []
    for tag, epoch in epochs:
        t = threading.Thread(target=_record, args=(coordinator, epoch, results, tag))
        threads.append(t)
    threads[0].start()
    assert entered.wait(timeout=15), "first revoke must reach the blocked close"
    for t in threads[1:]:
        t.start()
    release.set()
    for t in threads:
        t.join(timeout=15)
    for t in threads:
        assert not t.is_alive(), f"worker {t.name} did not resolve"
    return results, state, registry, tracked, release


# ── TASK-5867 exact probe: racing higher epoch while a lower close is blocked ─

def test_barrier_blocked_close_race_higher_epoch_reports_failure() -> None:
    """Reproduce TASK-5867's exact probe: a lower-epoch revoke's transport
    close blocks (in-flight); a racing higher-epoch revoke must NOT return
    success and the failed stream id stays observable as RevocationIncomplete
    even though the higher epoch applies."""
    results, state, registry, tracked, _ = _run_race([("lower", 1), ("higher", 2)])

    # No caller returns success while the cleanup failure is in-flight or
    # completed-but-unreported.
    assert results["lower"][0] != "ok", f"lower-epoch caller returned success: {results}"
    assert results["higher"][0] != "ok", f"higher-epoch caller returned success: {results}"

    # The failed stream id / RevocationIncomplete remains observable even
    # though the higher epoch raced and applied.
    incomplete = [r for r in results.values() if r[0] == "incomplete"]
    assert incomplete, f"no caller surfaced RevocationIncomplete: {results}"
    assert any("s-race" in r[2] for r in incomplete), f"failed stream id lost: {results}"

    # Monotonic state, honest registry, sealed fail-closed retained surface.
    assert state.revocation_epoch == 2
    assert registry.is_open("s-race") is False
    assert tracked.closed is True
    assert tracked.cleanup_failed is True
    with pytest.raises(StreamClosed):
        tracked.receive()


def test_race_higher_epoch_first_lower_never_returns_success() -> None:
    """Reverse ordering: when the HIGHER epoch runs the blocked close first,
    the lower-epoch waiter must not return success either (it may only
    roll back) and the failed id stays observable via the higher caller."""
    results, state, registry, tracked, _ = _run_race([("higher", 2), ("lower", 1)])

    assert results["higher"][0] != "ok", f"higher-epoch caller returned success: {results}"
    assert results["lower"][0] != "ok", f"lower-epoch caller returned success: {results}"

    incomplete = [r for r in results.values() if r[0] == "incomplete"]
    assert incomplete and any("s-race" in r[2] for r in incomplete), f"failed id lost: {results}"
    assert state.revocation_epoch == 2
    assert tracked.closed is True and tracked.cleanup_failed is True
    with pytest.raises(StreamClosed):
        tracked.receive()


def test_race_same_epoch_one_applies_other_rolls_back() -> None:
    """Same epoch under a failing blocked close: exactly one caller applies
    the epoch and surfaces RevocationIncomplete; the other is a rollback
    rejection (at-most-once application) — never success."""
    results, state, registry, tracked, _ = _run_race([("a", 2), ("b", 2)])

    assert results["a"][0] == "incomplete", f"first same-epoch caller: {results}"
    assert results["a"][1] == 2 and "s-race" in results["a"][2]
    assert results["b"][0] == "rollback", f"second same-epoch caller: {results}"
    assert state.revocation_epoch == 2
    assert tracked.closed is True and tracked.cleanup_failed is True
    with pytest.raises(StreamClosed):
        tracked.receive()


def test_race_multiple_waiters_all_report_failure() -> None:
    """Three concurrent revocations with a failing blocked close: every waiter
    that applies its own epoch surfaces RevocationIncomplete with the failed
    id; state advances monotonically to the maximum epoch; none returns
    success."""
    results, state, registry, tracked, _ = _run_race([("a", 1), ("b", 2), ("c", 3)])

    assert all(r[0] != "ok" for r in results.values()), f"some caller returned success: {results}"
    incomplete = [r for r in results.values() if r[0] == "incomplete"]
    assert len(incomplete) == 3, f"expected 3 RevocationIncomplete, got {results}"
    assert all("s-race" in r[2] for r in incomplete)
    assert state.revocation_epoch == 3
    assert tracked.closed is True and tracked.cleanup_failed is True
    with pytest.raises(StreamClosed):
        tracked.receive()


# ── Successful cleanup ───────────────────────────────────────────────────

def test_race_successful_cleanup_both_succeed_deterministically() -> None:
    """With a successful blocked close, concurrent revocations serialize and
    BOTH apply their monotonic epochs and return success — the lower epoch is
    never wrongly rolled back by the higher one."""
    results, state, registry, tracked, _ = _run_race([("a", 1), ("b", 2)], failing=False)

    assert results["a"] == ("ok", 1), f"lower-epoch caller: {results}"
    assert results["b"] == ("ok", 2), f"higher-epoch caller: {results}"
    assert state.revocation_epoch == 2
    assert tracked.closed is True and tracked.cleanup_failed is False
    with pytest.raises(StreamClosed):
        tracked.receive()


# ── Partial multi-handle failure ─────────────────────────────────────────

def test_race_partial_multi_handle_failure_all_report() -> None:
    """One good handle plus two failing handles (one blocked, one immediate)
    under concurrent revocation: every caller that applies its epoch reports
    BOTH failed ids; the good handle is physically closed; every retained
    wrapper is sealed."""
    state = default_authorization_state()
    registry = StreamRegistry()
    coordinator = RevocationCoordinator(state, registry)
    release = threading.Event()
    entered = threading.Event()

    class _Good:
        stream_id = "good"
        closed = False

        def receive(self) -> bytes | None:
            return None

        def close(self) -> None:
            self.closed = True

    good = _Good()
    good_tracked = registry.open("good", good)
    bad1_tracked = registry.open("bad1", _BlockingClose("bad1", release, entered, raise_on_close=True))
    bad2_tracked = registry.open("bad2", _ExplodingClose("bad2"))
    results: dict = {}
    t1 = threading.Thread(target=_record, args=(coordinator, 1, results, "a"))
    t2 = threading.Thread(target=_record, args=(coordinator, 2, results, "b"))
    t1.start()
    assert entered.wait(timeout=15), "blocked bad1 close must be reached"
    t2.start()
    release.set()
    t1.join(timeout=15)
    t2.join(timeout=15)
    assert not t1.is_alive() and not t2.is_alive()

    assert all(r[0] != "ok" for r in results.values()), f"some caller returned success: {results}"
    for tag in ("a", "b"):
        assert results[tag][0] == "incomplete", f"{tag}: {results[tag]}"
        assert set(results[tag][2]) == {"bad1", "bad2"}, f"{tag}: wrong failed ids {results[tag][2]}"
    assert state.revocation_epoch == 2
    assert good.closed is True, "good handle must be physically closed"
    for tracked in (good_tracked, bad1_tracked, bad2_tracked):
        assert tracked.closed is True
        with pytest.raises(StreamClosed):
            tracked.receive()


# ── Repeated calls after terminal cleanup ────────────────────────────────

def test_repeated_revoke_after_terminal_cleanup_failure_still_reports() -> None:
    """After a failed cleanup reaches terminal, a LATER higher-epoch revoke
    must not return success: the persisted cleanup failure relevant to the
    sealed generation is re-surfaced as RevocationIncomplete with the same
    failed ids."""
    state = default_authorization_state()
    registry = StreamRegistry()
    coordinator = RevocationCoordinator(state, registry)
    tracked = registry.open("s-race", _ExplodingClose("s-race"))

    with pytest.raises(RevocationIncomplete) as e1:
        coordinator.revoke(epoch=2)
    assert e1.value.applied_epoch == 2
    assert e1.value.stream_ids == ("s-race",)

    with pytest.raises(RevocationIncomplete) as e2:
        coordinator.revoke(epoch=3)
    assert e2.value.applied_epoch == 3
    assert e2.value.stream_ids == ("s-race",)

    assert state.revocation_epoch == 3
    assert tracked.closed is True and tracked.cleanup_failed is True
    with pytest.raises(StreamClosed):
        tracked.receive()


def test_repeated_revoke_after_terminal_successful_cleanup_is_idempotent() -> None:
    """After a SUCCESSFUL cleanup reaches terminal, later higher-epoch revokes
    return success normally (nothing to report) and repeated close_all never
    raises."""
    state = default_authorization_state()
    registry = StreamRegistry()
    coordinator = RevocationCoordinator(state, registry)

    class _GoodClose:
        stream_id = "s-ok"

        def __init__(self) -> None:
            self._closed = False

        def receive(self) -> bytes | None:
            return None

        def close(self) -> None:
            self._closed = True

        @property
        def closed(self) -> bool:
            return self._closed

    handle = _GoodClose()
    registry.open("s-ok", handle)

    assert coordinator.revoke(epoch=2) == 2
    assert coordinator.revoke(epoch=3) == 3
    assert coordinator.revoke(epoch=4) == 4
    assert state.revocation_epoch == 4
    registry.close_all()  # idempotent, never raises


# ── Re-entrancy: callbacks cannot re-enter the transaction ───────────────

def test_reentrant_close_all_from_close_callback_fails_closed_no_deadlock() -> None:
    """A transport-close callback that re-enters close_all on the SAME thread
    is REJECTED with fail-closed non-success (RuntimeError) — never a silent
    success, never an incomplete publish; the outer cleanup run owns and
    publishes the real terminal result and still surfaces the failed id."""
    registry = StreamRegistry()
    events: list[str] = []

    class _Reentrant:
        stream_id = "reentrant"

        def receive(self) -> bytes | None:
            return None

        def close(self) -> None:
            events.append("inner-close_all")
            try:
                registry.close_all()  # re-entrant: must fail closed, not deadlock
            except RuntimeError:
                events.append("inner-close_all-rejected")
            else:
                events.append("inner-close_all-returned")  # false success — forbidden
            raise OSError("boom")

        @property
        def closed(self) -> bool:
            return False

    tracked = registry.open("reentrant", _Reentrant())
    with pytest.raises(StreamCloseError) as excinfo:
        registry.close_all()
    assert excinfo.value.stream_ids == ("reentrant",)
    assert events == ["inner-close_all", "inner-close_all-rejected"]
    assert tracked.closed is True
    with pytest.raises(StreamClosed):
        tracked.receive()


def test_reentrant_revoke_from_close_callback_fails_closed() -> None:
    """A transport-close callback that calls revoke() re-entrantly on the same
    thread is rejected with RuntimeError (fail closed — the inner call never
    returns success and never deadlocks on the transaction lock); the outer
    transaction still applies its epoch and surfaces RevocationIncomplete with
    the failed id."""
    state = default_authorization_state()
    registry = StreamRegistry()
    coordinator = RevocationCoordinator(state, registry)
    events: list[str] = []

    class _Reentrant:
        stream_id = "reentrant"

        def receive(self) -> bytes | None:
            return None

        def close(self) -> None:
            try:
                coordinator.revoke(epoch=5)
                events.append("inner-revoke-returned")
            except RuntimeError:
                events.append("inner-revoke-rejected")
            raise OSError("boom")

        @property
        def closed(self) -> bool:
            return False

    registry.open("reentrant", _Reentrant())
    with pytest.raises(RevocationIncomplete) as excinfo:
        coordinator.revoke(epoch=2)
    assert excinfo.value.applied_epoch == 2
    assert excinfo.value.stream_ids == ("reentrant",)
    assert events == ["inner-revoke-rejected"]
    assert state.revocation_epoch == 2
    with pytest.raises(StreamClosed):
        registry.open("late", _Reentrant())
