"""Stream/session registry — revocation closes open streams fail closed
(contract §9; REV-002/REV-003/REV-004/REV-005/REV-006/REV-007/REV-008).

A closed stream refuses further frames; new streams are refused after a
revocation has closed the registry.

The registry owns the cleanup lifecycle as a one-shot state machine: the
first ``close_all`` seals and runs the transport cleanup exactly once, and
the terminal result is shared/persisted so that NO caller may return success
while an in-flight or completed cleanup failure relevant to the sealed
generation is unreported (TASK-5867).

ADMISSION AND EVERY OWNERSHIP/MEMBERSHIP MUTATION SHARE ONE ATOMIC LIFECYCLE
BOUNDARY (TASK-5874, extended by the THR-097 Unit-C fresh lifecycle
redesign): a single lifecycle lock guards BOTH the sealed flag (``_revoked``)
and registry membership (``_streams``). Every public membership mutation —
``open`` admission, duplicate-id replacement, ``close(stream_id)`` single
close, the retained-wrapper public ``close``, and ``close_all`` revocation —
performs its membership transition AND the seal of every affected public
wrapper in ONE critical section, so the affected wrapper(s) are sealed UNDER
that boundary before membership/ownership can escape (there is no
pop-without-seal window — TASK-5888). Physical transport close/open
callbacks execute OUTSIDE the lock but remain INSIDE the revocation
acknowledgement barrier: ``close_all`` may publish success only after every
pre-seal stream is unusable/closed, every outside-lock transport close that
linearized before the seal is terminal, and no post-seal admission can return
usable. Same-thread re-entrant ``close_all`` invoked from an unfinished
transport cleanup callback is REJECTED with fail-closed non-success (founder
ruling THR-097 seq140) — it never excludes its own in-flight cleanup and
publishes success, never marks the cleanup terminal with an incomplete
failed-id set, and never erases a callback failure that becomes terminal
after the re-entrant call; the caller retries from a normal context once its
callback's transport close is terminal. There is no lock-across-callback
deadlock.
"""
from __future__ import annotations

import threading
from typing import Protocol


class StreamClosed(Exception):
    """Raised when a stream is closed (revocation) and further bytes/frames
    are attempted."""

    def __init__(self, stream_id: str) -> None:
        super().__init__(f"stream closed: {stream_id}")
        self.stream_id = stream_id


class StreamCloseError(Exception):
    """Raised by ``StreamRegistry.close_all`` when one or more handles failed
    to close.

    The registry is still sealed (fail closed): every handle was dropped and
    no new stream can open, so a revoked device never retains a live
    registry-tracked stream. The failed ids are exposed for diagnostics only.

    The failure is PERSISTED on the registry: every subsequent ``close_all``
    — from a concurrent or a later revocation — re-raises the same failed ids
    rather than reporting a successful idempotent no-op, so the cleanup
    failure relevant to the sealed generation is never lost behind a
    higher-epoch race (TASK-5867).
    """

    def __init__(self, stream_ids: list[str] | tuple[str, ...]) -> None:
        self.stream_ids: tuple[str, ...] = tuple(stream_ids)
        super().__init__(
            f"stream close failed for {len(self.stream_ids)} stream(s)"
        )


class StreamHandle(Protocol):
    stream_id: str

    def receive(self) -> bytes | None: ...

    def close(self) -> None: ...

    @property
    def closed(self) -> bool: ...


class _TrackedStream:
    """Registry-owned wrapper around a transport handle.

    The registry hands out THIS object (never the raw handle): the externally
    retained HTTP/SSE/WebSocket surface is the wrapper, whose public state is
    owned by the registry. Once the wrapper is sealed (revocation, single
    close, duplicate replacement, or the client's own ``close``), the surface
    goes fail-closed IRREVOCABLY: ``receive``/``send`` raise
    ``StreamClosed``, ``closed`` reports True, and ``close`` is an idempotent
    no-op — regardless of whether the underlying transport ``close()``
    succeeded or raised. A failing transport close is surfaced through
    ``StreamCloseError``/``RevocationIncomplete`` carrying the stream id, and
    ``cleanup_failed`` records the outcome evidence without claiming physical
    closure.

    The retained-wrapper public ``close`` is a REGISTRY MEMBERSHIP MUTATION:
    it routes through ``StreamRegistry.close`` so the membership removal and
    the seal are ONE atomic lifecycle transition (the registry owns the
    wrapper's lifecycle; the client-held wrapper can never escape the atomic
    seal). The wrapper is constructed OUTSIDE the lifecycle lock (it has no
    callbacks), which is the deterministic race-test seam: an admission can
    pause between construction and registration while a revocation completes,
    and must still fail closed.
    """

    __slots__ = ("stream_id", "_inner", "_sealed", "_cleanup_failed", "_registry")

    def __init__(self, stream_id: str, inner: StreamHandle, registry: "StreamRegistry") -> None:
        self.stream_id = stream_id
        self._inner = inner
        self._registry = registry
        self._sealed = False
        self._cleanup_failed = False

    # ── fail-closed public surface ───────────────────────────────────────

    @property
    def closed(self) -> bool:
        """True once sealed; before sealing, reflect the underlying handle."""
        if self._sealed:
            return True
        return bool(getattr(self._inner, "closed", False))

    @property
    def cleanup_failed(self) -> bool:
        """Outcome evidence for a failed underlying cleanup: True when the
        transport ``close()`` raised. The public surface is sealed regardless;
        this flag never claims physical closure."""
        return self._cleanup_failed

    def receive(self) -> bytes | None:
        if self._sealed:
            raise StreamClosed(self.stream_id)
        return self._inner.receive()

    def send(self, payload: bytes) -> None:
        if self._sealed:
            raise StreamClosed(self.stream_id)
        send = getattr(self._inner, "send", None)
        if send is None:
            raise AttributeError("stream does not support send")
        send(payload)

    # In-flight HTTP read surface passthrough (status/headers of the daemon
    # response); present only when the underlying handle carries them.
    @property
    def status(self):
        return getattr(self._inner, "status", None)

    @property
    def headers(self):
        return getattr(self._inner, "headers", None)

    # ── registry-owned transitions ───────────────────────────────────────

    def seal(self) -> None:
        """Seal the public surface BEFORE any transport close is attempted
        (fail-closed ordering): from this instant the retained handle rejects
        receive/send and reports closed, even if ``_close_inner`` raises.
        Only ever called under the lifecycle lock, atomically with the
        membership transition that owns this wrapper."""
        self._sealed = True

    def close(self) -> None:
        """Retained-wrapper public close — a REGISTRY MEMBERSHIP MUTATION.

        Routes through ``StreamRegistry.close`` so the membership removal and
        the wrapper seal are ONE atomic lifecycle transition under the
        lifecycle lock (the wrapper is the registry-owned surface; the
        registry runs the transport close outside the lock and acknowledges
        it on the revocation acknowledgement barrier). Once sealed this is an
        idempotent no-op (the revocation transaction owns cleanup after
        sealing).
        """
        if self._sealed:
            return
        self._registry.close(self.stream_id)

    def _close_inner(self) -> None:
        """Attempt the underlying transport close, recording the failure
        outcome evidence (without claiming physical closure)."""
        try:
            self._inner.close()
        except Exception:
            self._cleanup_failed = True
            raise


class StreamRegistry:
    """Tracks open streams; ``close_all`` (revocation) closes every handle and
    permanently refuses new streams.

    ONE ATOMIC LIFECYCLE BOUNDARY (TASK-5874, fresh lifecycle redesign): a
    single lifecycle lock guards BOTH the sealed flag (``_revoked``) and
    registry membership. EVERY public membership mutation — ``open``
    admission, duplicate-id replacement, ``close(stream_id)``, the
    retained-wrapper public ``close``, and ``close_all`` — performs its
    membership transition AND the seal of every affected public wrapper in
    ONE critical section (the seal never lags the membership transition — no
    pop-without-seal window, TASK-5888), and runs physical transport
    close/open callbacks OUTSIDE the lock but INSIDE the revocation
    acknowledgement barrier.

    Linearization points:

    - ``open``: the sealed-flag check + registration insert in ONE critical
      section. An admission is successful ONLY if it is fully registered
      before the revocation seal; every admission that linearizes after the
      seal fails closed and NEVER returns a usable wrapper (the allocated
      transport is closed by the registry itself, outside the lock).
    - ``close(stream_id)`` / retained-wrapper ``close``: the membership pop +
      wrapper seal in ONE critical section; the transport close runs outside
      the lock on the acknowledgement barrier.
    - duplicate-id replacement: the old wrapper's pop + seal and the new
      wrapper's insert in ONE critical section; the replaced transport close
      runs outside the lock on the acknowledgement barrier (a same-handle
      re-registration never closes the shared inner).
    - ``close_all``: the seal (the **linearization point**) + wrapper seals +
      snapshot + membership clear in ONE critical section, seal before
      snapshot; then the runner waits for every outside-lock transport close
      that linearized before the seal (acknowledgement barrier), runs the
      snapshot transport closes outside the lock, and publishes the shared
      terminal result.

    Revocation success is permitted only after the registry is sealed, every
    pre-seal registered wrapper is irrevocably unusable, every required
    physical-cleanup outcome is terminal and acknowledged, and no post-seal
    admission can return usable.
    """

    def __init__(self) -> None:
        self._streams: dict[str, _TrackedStream] = {}
        self._revoked = False
        # ── cleanup lifecycle (one-shot; terminal result shared/persisted) ──
        # ONE lock = the atomic lifecycle boundary: guards the sealed flag AND
        # registry membership. Callbacks (transport open/close, duplicate
        # replacement) never run under it.
        self._lock = threading.Lock()
        self._cleanup_started = False
        self._cleanup_done = threading.Event()
        self._cleanup_failed_ids: tuple[str, ...] = ()
        self._in_cleanup = threading.local()  # same-thread re-entrancy guard
        # ── revocation acknowledgement barrier ────────────────────────────
        # Outside-lock transport closes that linearized before the seal are
        # registered here and must become terminal before close_all success.
        # A thread-local counter tracks the calling thread's OWN in-flight
        # closes so a same-thread re-entrant close_all from an unfinished
        # transport cleanup callback is REJECTED (fail-closed non-success)
        # rather than publishing an incomplete success or self-deadlocking.
        self._cleanup_in_flight = 0
        self._cleanup_ack = threading.Condition()
        self._self_inflight = threading.local()  # same-thread in-flight closes
        # Barrier-tracked explicit-close failures (pre-seal, terminal): folded
        # into the persisted _cleanup_failed_ids at publish so a callback
        # failure that becomes terminal after a re-entrant rejection is never
        # erased (REV-008).
        self._inflight_failed_ids: set[str] = set()

    # ── revocation acknowledgement barrier (lifecycle steps) ─────────────

    def _begin_inflight_close(self) -> None:
        """Register an outside-lock transport close on the revocation
        acknowledgement barrier. Called UNDER the lifecycle lock, atomically
        with the membership transition that owns the close (so the revocation
        seal, in the same lock, can never miss it). Also counts the calling
        thread's own in-flight closes so a same-thread re-entrant close_all
        from an unfinished transport cleanup callback is rejected (fail-closed
        non-success) instead of being excluded into an incomplete success."""
        self._cleanup_in_flight += 1
        self._self_inflight.count = getattr(self._self_inflight, "count", 0) + 1

    def _end_inflight_close(self, failed_id: str | None = None) -> None:
        """Acknowledge that an outside-lock transport close is TERMINAL.
        Called AFTER the close terminates (success or recorded failure),
        never under the lifecycle lock. When the close terminated with
        FAILURE, the stream id is persisted on the registry so the close_all
        runner folds it into the terminal failed-id set (a callback failure
        that becomes terminal after a re-entrant rejection is never erased).
        Wakes the close_all acknowledgement waiters."""
        with self._lock:
            self._cleanup_in_flight -= 1
            if failed_id is not None:
                self._inflight_failed_ids.add(failed_id)
        self._self_inflight.count = getattr(self._self_inflight, "count", 1) - 1
        with self._cleanup_ack:
            self._cleanup_ack.notify_all()

    def _wait_for_inflight_acknowledgement(self) -> None:
        """The revocation acknowledgement barrier: ``close_all``'s runner
        waits (with NO lock held) for every outside-lock transport close that
        linearized before the seal to become terminal, before running its own
        snapshot cleanup — revocation never publishes success while a pre-seal
        transport close is in flight, and no outside-lock callback can ever
        deadlock against the wait (callbacks run on their own threads, which
        acknowledge via ``_end_inflight_close``). A same-thread re-entrant
        close_all from an unfinished transport cleanup callback is rejected at
        ``close_all`` entry BEFORE this wait is reached, so the runner never
        holds an in-flight close of its own — the wait covers EVERY registered
        in-flight close (no self-inflight exclusion; an excluded own in-flight
        close was the false-success acknowledgement defect)."""
        with self._cleanup_ack:
            while self._cleanup_in_flight > 0:
                self._cleanup_ack.wait()

    @staticmethod
    def _close_unexposed(handle: StreamHandle) -> None:
        """Close a transport handle that was never registered or exposed. The
        fail-closed admission swallows the close failure: the handle was never
        handed to any caller, and the admission denial (``StreamClosed``) is
        already determined — a close error on an unexposed handle must not
        surface a different error or secret-bearing text."""
        try:
            handle.close()
        except Exception:
            pass

    def open(self, stream_id: str, handle: StreamHandle) -> _TrackedStream:
        """Admit ``handle`` under ``stream_id`` and return the registry-owned
        wrapper — the raw handle is never handed out.

        One atomic lifecycle boundary: the sealed-flag check and the
        registration are ONE critical section on the lifecycle lock. An
        admission is successful ONLY if it fully registers before the seal:

        - if the seal already linearized, the admission fails closed: the
          allocated transport is closed OUTSIDE the lock (registered on the
          acknowledgement barrier — never leaked, never returned) and
          ``StreamClosed`` is raised — even when transport allocation or
          callbacks raced;
        - a duplicate-id replacement seals the previous wrapper and closes its
          transport OUTSIDE the lock on the acknowledgement barrier (callbacks
          never run under the lock; the replaced transport close is terminal
          before ``close_all`` success);
        - a pre-seal registration is included in ``close_all``'s snapshot and
          revoked with it.
        """
        tracked = _TrackedStream(stream_id, handle, self)  # construction: no callbacks
        replace: _TrackedStream | None = None
        doomed: StreamHandle | None = None
        with self._lock:
            if self._revoked:
                # The seal already linearized: this admission is NOT fully
                # registered before the seal — fail closed below, outside the
                # lock. The unexposed handle's close is registered on the
                # acknowledgement barrier so it too is terminal before any
                # in-flight revocation publishes success.
                doomed = handle
                self._begin_inflight_close()
            else:
                previous = self._streams.get(stream_id)
                if previous is not None:
                    del self._streams[stream_id]
                    previous.seal()  # retained old wrapper fail-closes now
                    if previous._inner is not handle:
                        replace = previous  # different transport: close outside
                        self._begin_inflight_close()
                self._streams[stream_id] = tracked  # registration linearizes
        if doomed is not None:
            try:
                self._close_unexposed(doomed)
            finally:
                self._end_inflight_close()
            raise StreamClosed(stream_id)
        if replace is not None:
            # Duplicate-replacement transport close runs OUTSIDE the lock on
            # the acknowledgement barrier (callbacks never run under it); a
            # failure is recorded on the replaced wrapper (``cleanup_failed``)
            # and never blocks the new admission. The barrier ack fires when
            # the close is terminal.
            try:
                replace._close_inner()
            except Exception:
                pass
            finally:
                self._end_inflight_close()
        return tracked

    def close(self, stream_id: str) -> None:
        """Close one tracked stream: the membership removal AND the wrapper
        seal are ONE atomic transition under the lifecycle lock — there is no
        pop-without-seal window in which a concurrent revocation could publish
        success while the retained wrapper is still usable (TASK-5888). The
        transport close runs OUTSIDE the lock on the revocation
        acknowledgement barrier: ``close_all`` waits for it to be terminal
        before publishing success. The transport-close failure propagates to
        the caller but never unseals the wrapper; a missing/unknown stream id
        is an idempotent no-op."""
        tracked: _TrackedStream | None = None
        with self._lock:
            tracked = self._streams.pop(stream_id, None)
            if tracked is not None:
                tracked.seal()  # atomic with membership removal (TASK-5888)
                self._begin_inflight_close()
        if tracked is None:
            return
        try:
            tracked._close_inner()
        except Exception:
            # Persist the failed id on the registry: a pre-seal explicit-close
            # failure (including one that becomes terminal AFTER a re-entrant
            # close_all rejection) is folded into the terminal failed-id set
            # and re-surfaced by every later close_all/revoke — never erased.
            self._end_inflight_close(tracked.stream_id)
            raise
        self._end_inflight_close()

    def close_all(self) -> None:
        """Seal the registry and close every open handle exactly once, sharing
        the terminal cleanup result across concurrent callers (contract §9;
        TASK-5867).

        Lifecycle:

        1. the FIRST caller seals the registry and every retained wrapper
           (fail-closed byte safety), snapshots the streams, and marks the
           cleanup started — all under the lifecycle lock, in the SAME
           critical section every mutation uses for its seal+membership
           transition (one atomic lifecycle boundary: the seal is the
           linearization point and occurs BEFORE the snapshot, and no
           post-seal admission can register);
        2. the runner then enters the REVOCATION ACKNOWLEDGEMENT BARRIER:
           it waits (holding no lock) for every outside-lock transport close
           that linearized before the seal — single-close, retained-wrapper
           close, duplicate replacement, fail-closed admission — to become
           terminal, so revocation never publishes success while a pre-seal
           transport is still open;
        3. snapshot transport closes run OUTSIDE the lock (callbacks never
           run under it; a same-thread re-entrant ``close_all`` from inside a
           callback is REJECTED with fail-closed non-success by the same-thread
           guard — it never publishes or acknowledges);
        4. concurrent callers WAIT on ``_cleanup_done`` without holding any
           lock and then observe the SAME persisted terminal result — a
           racing revoke can never treat an in-flight cleanup as a
           successful idempotent no-op;
        5. every subsequent caller re-raises the persisted failures as
           ``StreamCloseError`` — a completed cleanup failure relevant to the
           sealed generation is never silently forgotten.

        Fail-closed ordering is preserved: every retained wrapper is sealed
        BEFORE any transport close is attempted, so a raising close can never
        leave the externally retained handle readable, writable, or
        untracked. Revocation success is permitted only after the registry is
        sealed, every pre-seal registered wrapper is irrevocably unusable,
        every required physical-cleanup outcome is terminal and acknowledged,
        and no post-seal admission can return usable.
        """
        if getattr(self._self_inflight, "count", 0) > 0 or getattr(
            self._in_cleanup, "active", False
        ):
            # Same-thread re-entrancy from within an UNFINISHED transport
            # cleanup callback (founder ruling THR-097 seq140): this thread is
            # inside an outside-lock transport close whose outcome is not yet
            # terminal. Proceeding would either exclude the calling thread's
            # own in-flight close and publish an incomplete success (erasing a
            # callback failure that becomes terminal later) or wait on our own
            # completion. FAIL CLOSED: reject with the public API's existing
            # non-success representation (RuntimeError, mirroring the
            # coordinator's re-entrant-revoke rejection) BEFORE any seal,
            # membership transition, or acknowledgement publish. The caller
            # must retry close_all from a normal context once its callback's
            # transport close is terminal.
            raise RuntimeError(
                "re-entrant close_all from a transport cleanup callback rejected"
            )
        pending: list[tuple[str, _TrackedStream]] = []
        role = "observe"
        failures: tuple[str, ...] = ()
        with self._lock:
            if self._cleanup_done.is_set():
                # Terminal result already published: observe it.
                failures = self._cleanup_failed_ids
                role = "observe"
            elif self._cleanup_started:
                # Cleanup in flight by another caller: wait outside the lock.
                role = "wait"
            else:
                # First caller: seal first, snapshot, mark started.
                self._revoked = True
                for tracked in list(self._streams.values()):
                    tracked.seal()
                pending = list(self._streams.items())
                self._streams.clear()
                self._cleanup_started = True
                role = "run"
        if role == "wait":
            # No lock is held while waiting: the cleanup runner can publish
            # the terminal result and set the event without deadlocking.
            self._cleanup_done.wait()
            with self._lock:
                failures = self._cleanup_failed_ids
        elif role == "run":
            # Revocation acknowledgement barrier: every outside-lock transport
            # close that linearized before the seal must be terminal before
            # success (single close / replacement / fail-closed admission).
            self._wait_for_inflight_acknowledgement()
            failed: list[str] = []
            self._in_cleanup.active = True
            try:
                for stream_id, tracked in pending:
                    try:
                        tracked._close_inner()
                    except Exception:
                        failed.append(stream_id)
            finally:
                with self._lock:
                    # Fold every barrier-tracked pre-seal explicit-close
                    # failure (terminal before publish, guaranteed by the
                    # acknowledgement barrier) into the PERSISTED terminal
                    # result — never publish an incomplete failed-id set, and
                    # never erase a callback failure that became terminal
                    # after a re-entrant rejection (REV-008).
                    self._cleanup_failed_ids = tuple(failed) + tuple(
                        sorted(self._inflight_failed_ids)
                    )
                    self._inflight_failed_ids.clear()
                    self._cleanup_done.set()
                self._in_cleanup.active = False
            # Raise on the PERSISTED terminal result (snapshot failures folded
            # together with barrier-tracked explicit-close failures) — a
            # pre-seal close failure folded in from the acknowledgement barrier
            # must never slip past as a silent success.
            if self._cleanup_failed_ids:
                raise StreamCloseError(self._cleanup_failed_ids)
            return
        if failures:
            raise StreamCloseError(failures)

    def is_open(self, stream_id: str) -> bool:
        """Derived membership read — synchronized on the lifecycle lock so the
        reported truth is a real linearization point of the atomic boundary."""
        with self._lock:
            return stream_id in self._streams

    def open_count(self) -> int:
        """Number of live registered streams — a real linearization point on
        the lifecycle lock. Advisory read used by the supervisor's revocation
        reconciliation to close live streams after a cross-process revoke
        WITHOUT sealing an empty registry (sealing an empty registry would
        refuse future streams for legitimately re-paired devices)."""
        with self._lock:
            return len(self._streams)

    @property
    def sealed(self) -> bool:
        """True once ``close_all`` has sealed this one-shot registry (the
        linearization point of the lifecycle lock). The supervisor rotates
        the authoritative runtime after a reconciled revocation EXACTLY when
        the registry is sealed — a sealed registry would otherwise refuse
        every future stream for the remaining process lifetime (TASK-6044
        finding 2)."""
        with self._lock:
            return self._revoked

    def raise_if_open(self, stream_id: str) -> None:
        """Fail closed when the stream is NOT a member of the registry (a
        closed/never-registered stream must not be used). Synchronized on the
        lifecycle lock like ``is_open``."""
        with self._lock:
            if stream_id not in self._streams:
                raise StreamClosed(stream_id)
