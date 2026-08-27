"""Stream/session registry — revocation closes open streams fail closed
(contract §9; REV-002/REV-003).

A closed stream refuses further frames; new streams are refused after a
revocation has closed the registry.

The registry owns the cleanup lifecycle as a one-shot state machine: the
first ``close_all`` seals and runs the transport cleanup exactly once, and
the terminal result is shared/persisted so that NO caller may return success
while an in-flight or completed cleanup failure relevant to the sealed
generation is unreported (TASK-5867). Transport-close callbacks never run
under the lifecycle lock, and same-thread re-entrant ``close_all`` returns
immediately — there is no lock-across-callback deadlock.
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
    owned by the registry. Once the wrapper is sealed (revocation or close),
    the surface goes fail-closed IRREVOCABLY: ``receive``/``send`` raise
    ``StreamClosed``, ``closed`` reports True, and ``close`` is an idempotent
    no-op — regardless of whether the underlying transport ``close()``
    succeeded or raised. A failing transport close is surfaced through
    ``StreamCloseError``/``RevocationIncomplete`` carrying the stream id, and
    ``cleanup_failed`` records the outcome evidence without claiming physical
    closure.
    """

    __slots__ = ("stream_id", "_inner", "_sealed", "_cleanup_failed")

    def __init__(self, stream_id: str, inner: StreamHandle) -> None:
        self.stream_id = stream_id
        self._inner = inner
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
        receive/send and reports closed, even if ``_close_inner`` raises."""
        self._sealed = True

    def close(self) -> None:
        """Close the stream. Once sealed this is an idempotent no-op (the
        revocation transaction owns cleanup after sealing); before sealing, the
        surface is sealed first and the inner close is attempted — a failure
        propagates but never unseals the retained handle."""
        if self._sealed:
            return
        self._sealed = True
        self._close_inner()

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

    Every open handle is wrapped in a ``_TrackedStream`` whose public state is
    owned by the registry: after revocation the externally retained wrapper
    irrevocably rejects receive/send even when the underlying transport close
    raised — a revoked device never retains a live stream and no handle is
    ever left readable, writable, or untracked.
    """

    def __init__(self) -> None:
        self._streams: dict[str, _TrackedStream] = {}
        self._revoked = False
        # ── cleanup lifecycle (one-shot; terminal result shared/persisted) ──
        self._lock = threading.Lock()  # guards lifecycle state, NOT callbacks
        self._cleanup_started = False
        self._cleanup_done = threading.Event()
        self._cleanup_failed_ids: tuple[str, ...] = ()
        self._in_cleanup = threading.local()  # same-thread re-entrancy guard

    def open(self, stream_id: str, handle: StreamHandle) -> _TrackedStream:
        """Track ``handle`` under ``stream_id`` and return the registry-owned
        wrapper — the raw handle is never handed out."""
        if self._revoked:
            raise StreamClosed(stream_id)
        previous = self._streams.get(stream_id)
        if previous is not None and previous._inner is not handle:
            previous.close()
        tracked = _TrackedStream(stream_id, handle)
        self._streams[stream_id] = tracked
        return tracked

    def close(self, stream_id: str) -> None:
        """Close one tracked stream: the wrapper is sealed and dropped, then
        the underlying close is attempted (its failure propagates but never
        unseals)."""
        tracked = self._streams.pop(stream_id, None)
        if tracked is not None:
            tracked.close()

    def close_all(self) -> None:
        """Seal the registry and close every open handle exactly once, sharing
        the terminal cleanup result across concurrent callers (contract §9;
        TASK-5867).

        Lifecycle:

        1. the FIRST caller seals the registry and every retained wrapper
           (fail-closed byte safety), snapshots the streams, and marks the
           cleanup started — all under the lifecycle lock;
        2. transport closes run OUTSIDE the lock (callbacks never run under
           it; a same-thread re-entrant ``close_all`` from inside a callback
           returns immediately via the thread-local guard);
        3. concurrent callers WAIT on ``_cleanup_done`` without holding any
           lock and then observe the SAME persisted terminal result — a
           racing revoke can never treat an in-flight cleanup as a
           successful idempotent no-op;
        4. every subsequent caller re-raises the persisted failures as
           ``StreamCloseError`` — a completed cleanup failure relevant to the
           sealed generation is never silently forgotten.

        Fail-closed ordering is preserved: every retained wrapper is sealed
        BEFORE any transport close is attempted, so a raising close can never
        leave the externally retained handle readable, writable, or
        untracked.
        """
        if getattr(self._in_cleanup, "active", False):
            # Same-thread re-entrancy from within a transport-close callback:
            # the outer cleanup run owns the terminal result; return now (no
            # event wait — waiting would deadlock on our own completion).
            return
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
                    self._cleanup_failed_ids = tuple(failed)
                    self._cleanup_done.set()
                self._in_cleanup.active = False
            if failed:
                raise StreamCloseError(failed)
            return
        if failures:
            raise StreamCloseError(failures)

    def is_open(self, stream_id: str) -> bool:
        return stream_id in self._streams

    def raise_if_open(self, stream_id: str) -> None:
        if not self.is_open(stream_id):
            raise StreamClosed(stream_id)
