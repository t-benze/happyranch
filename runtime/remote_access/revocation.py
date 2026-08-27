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
"""
from __future__ import annotations

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

    def revoke(self, epoch: int) -> int:
        """Revoke at a monotonic epoch.

        - Rejects rollback epochs before any side effect.
        - Closes every open stream (the registry seals fail closed even if an
          individual handle close raises).
        - Applies trust-state revocation atomically.
        - Notifies signal subscribers after application.
        - Raises ``RevocationIncomplete`` (with the applied epoch) when a
          handle failed to close — state is still revoked (deny side).
        """
        if epoch <= self.state.revocation_epoch:
            raise ValueError("revocation epoch rollback rejected")
        failures: list[str] = []
        try:
            self.registry.close_all()
        except StreamCloseError as exc:
            failures = list(exc.stream_ids)
        # Private by contract: trust-state revocation is applied only through
        # this transaction, after stream closure (contract §9 ordering).
        self.state._apply_revocation(epoch)
        if self.signal is not None:
            self.signal.fire(epoch)
        if failures:
            raise RevocationIncomplete(epoch, failures)
        return epoch
