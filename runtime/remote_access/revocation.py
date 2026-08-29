"""Authoritative revocation transaction (contract §9).

The connector revokes through exactly one public API — the
``RevocationCoordinator`` — which composes live-stream closure with
trust-state application in the mandated order:

1. validate the epoch is a real advance (rollback rejected before any side
   effect);
2. close every live HTTP/SSE/WebSocket handle through the stream registry,
   fail closed (the registry seals even when an individual handle close
   raises — a revoked device never retains a live registry-tracked stream);
3. apply trust-state revocation atomically (``TrustState._apply_revocation``
   is private and reachable only through this transaction);
4. notify signal subscribers after application.

If a handle failed to close, ``revoke`` still applies the revocation (the
deny side is the safe side) and raises ``RevocationIncomplete`` carrying the
applied epoch — state is never left ambiguously advanced and no revoked
device retains a live stream.

The transaction is SERIALIZED across concurrent callers (one authoritative
transaction; TASK-5867): the stream registry persists the complete cleanup
terminal result, so a racing higher-epoch revoke can never treat an
in-flight cleanup as a successful idempotent no-op and return false success
— every caller that would otherwise succeed re-surfaces the persisted
failed ids as ``RevocationIncomplete``. Same-thread re-entrant ``revoke``
(from within a transport-close callback) fails closed with ``RuntimeError``
rather than deadlocking on the transaction lock; callbacks cannot re-enter
the transaction.
"""
from __future__ import annotations

import threading

from runtime.remote_access.authorization import RevocationSignal, TrustState
from runtime.remote_access.streams import StreamCloseError, StreamRegistry


class RevocationIncomplete(Exception):
    """Raised when stream closure was imperfect.

    The registry is still sealed and trust-state revocation WAS applied (fail
    closed): a revoked device never retains a live registry-tracked stream
    and state is not ambiguously advanced. ``applied_epoch`` and the failed
    stream ids are exposed for diagnostics.
    """

    def __init__(self, applied_epoch: int, stream_ids: list[str] | tuple[str, ...]) -> None:
        self.applied_epoch = applied_epoch
        self.stream_ids: tuple[str, ...] = tuple(stream_ids)
        super().__init__(
            f"revocation epoch {applied_epoch} applied; "
            f"{len(self.stream_ids)} stream(s) failed to close"
        )


class RevocationCoordinator:
    """The one authoritative revocation transaction."""

    def __init__(
        self,
        state: TrustState,
        registry: StreamRegistry,
        signal: RevocationSignal | None = None,
    ) -> None:
        self.state = state
        self.registry = registry
        self.signal = signal
        self._tx_lock = threading.Lock()  # serializes the whole transaction
        self._in_revoke = threading.local()  # same-thread re-entrancy guard

    def revoke(self, epoch: int) -> int:
        """Revoke at a monotonic epoch.

        - Rejects rollback epochs before any side effect (fast-fail, and
          again under the transaction lock for at-most-once application).
        - SERIALIZES concurrent revocations: exactly one caller runs the
          stream-registry cleanup; every other caller waits for the shared
          terminal result (no lock is held while waiting inside the
          registry). A racing higher epoch can never observe the closure as
          a successful no-op and return success while a cleanup failure is
          in flight or completed-but-unreported.
        - Closes every open stream (the registry seals fail closed even if an
          individual handle close raises).
        - Applies trust-state revocation atomically.
        - Notifies signal subscribers after application.
        - Raises ``RevocationIncomplete`` (with the applied epoch) when a
          handle failed to close — state is still revoked (deny side). The
          same persisted failed ids are re-surfaced by every later revoke
          that would otherwise succeed.
        - Rejects same-thread re-entrant calls with ``RuntimeError`` (a
          transport-close callback cannot re-enter the transaction; failing
          closed avoids deadlock on ``_tx_lock``).
        """
        if getattr(self._in_revoke, "active", False):
            raise RuntimeError("re-entrant revocation transaction rejected")
        if epoch <= self.state.revocation_epoch:
            raise ValueError("revocation epoch rollback rejected")
        self._in_revoke.active = True
        try:
            with self._tx_lock:
                # Authoritative epoch check: a waiter may arrive after a
                # higher epoch already applied (at-most-once application).
                if epoch <= self.state.revocation_epoch:
                    raise ValueError("revocation epoch rollback rejected")
                failures: tuple[str, ...] = ()
                try:
                    self.registry.close_all()
                except StreamCloseError as exc:
                    failures = exc.stream_ids
                # Private by contract: trust-state revocation is applied only
                # through this transaction, after stream closure (§9 order).
                self.state._apply_revocation(epoch)
                if self.signal is not None:
                    self.signal.fire(epoch)
        finally:
            self._in_revoke.active = False
        if failures:
            raise RevocationIncomplete(epoch, failures)
        return epoch

    def revoke_device(self, device_id: str, epoch: int) -> int:
        """Revoke ONE paired device at a monotonic epoch (Supported-DIY
        lost-device flow). Runs the SAME authoritative transaction shape as
        ``revoke`` — re-entrancy guarded, serialized, fail-closed stream
        closure FIRST (conservatively ALL live streams are closed: stream
        attribution to a device is a later refinement; closing more than
        needed is the safe side), then the targeted per-device trust-state
        application, then signal subscribers. Other devices remain
        authorized. Raises ``RevocationIncomplete`` exactly like ``revoke``
        when a stream failed to close (the deny side is still applied).
        """
        if getattr(self._in_revoke, "active", False):
            raise RuntimeError("re-entrant revocation transaction rejected")
        if epoch <= self.state.revocation_epoch:
            raise ValueError("revocation epoch rollback rejected")
        self._in_revoke.active = True
        try:
            with self._tx_lock:
                if epoch <= self.state.revocation_epoch:
                    raise ValueError("revocation epoch rollback rejected")
                failures: tuple[str, ...] = ()
                try:
                    self.registry.close_all()
                except StreamCloseError as exc:
                    failures = exc.stream_ids
                # Private by contract: per-device revocation is applied only
                # through this transaction, after stream closure (§9 order).
                self.state._revoke_device(device_id, epoch)
                if self.signal is not None:
                    self.signal.fire(epoch)
        finally:
            self._in_revoke.active = False
        if failures:
            raise RevocationIncomplete(epoch, failures)
        return epoch
