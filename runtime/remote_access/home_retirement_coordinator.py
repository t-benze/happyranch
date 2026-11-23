"""Active retirement preparation and real original-owner cutoff composition.

The explicit caller must hold the original retirement fence. This owner joins
original evidence before revocation and can guard dedicated controller native
effects with the actual live cutoff receipt. It does not stop the managed
connector, remove artifacts, change accounts, admit package replacement or
publish a retired receipt.
Inert/pre-activation retirement and post-effect recovery have separate proofs.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from runtime.remote_access.home_retirement import RetirementUncertain
from runtime.remote_access.home_retirement_cutoff import CutoffReceipt, OriginalPeer, RetirementCutoff
from runtime.remote_access.home_retirement_fence import RetirementFence
from runtime.remote_access.home_retirement_inventory import LegacyInventory
from runtime.remote_access.home_retirement_journal import OriginalInventory, RetirementJournal
from runtime.remote_access.home_retirement_service import OwnedService, OwnedStopReceipt
from runtime.remote_access.home_retirement_transport import ManagedObservation, ManagedTransport
from runtime.remote_access.home_retirement_transport_journal import ManagedTransportJournal
from runtime.remote_access.identity import ConnectorIdentity

if TYPE_CHECKING:
    from runtime.remote_access.installer import ManagedInstaller


class ActiveRetirement:
    """Same-root original evidence and live serving-owner acknowledgement.

    Construction performs no discovery. ``capture`` is the explicit pre-effect
    entry; recovery must load the saved owners instead of recapturing a changed
    layout. No Boolean or durable phase flag supplies cutoff authority.
    """

    def __init__(self, journal: RetirementJournal, original: OriginalInventory,
                 native_journal: ManagedTransportJournal, native: ManagedObservation,
                 cutoff: RetirementCutoff) -> None:
        self.journal, self.original = journal, original
        self.native_journal, self.native, self.cutoff = native_journal, native, cutoff

    @classmethod
    def capture(cls, io: ManagedInstaller, fence: RetirementFence) -> ActiveRetirement:
        try:
            fence.require(io)
            inventory = LegacyInventory(io)
            ownership = inventory.observe(fence=fence)
            managed = inventory.managed_members(ownership, fence=fence)
            if ownership.activation is None or not managed.live_sidecar:
                raise RetirementUncertain()
            transport = ManagedTransport(io, fence, managed)
            native = transport.observe()
            process = native.processes['happyranch-connector.service']
            if process is None:
                raise RetirementUncertain()
            cutoff = RetirementCutoff(io, fence, ConnectorIdentity(tenant_id=managed.identity[0],
                home_id=managed.identity[1], connector_id=managed.identity[2]), managed.release_uid,
                original_peer=OriginalPeer(*process, native.boot_id))
            # The actual serving socket/status pins this process before the
            # remaining original inventory. A current native name alone cannot.
            cutoff.capture_original()
            journal = RetirementJournal(io, fence)
            original = journal.capture(ownership)
            if original.managed != managed:
                raise RetirementUncertain()
            native_journal = ManagedTransportJournal(transport)
            if native_journal.capture() != native:
                raise RetirementUncertain()
            owner = cls(journal, original, native_journal, native, cutoff)
            owner.require_original()
            return owner
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    def require_original(self) -> None:
        """Fresh pre-revocation boundary; unsuitable after trust-pair mutation."""
        try:
            io, fence = self.journal.io, self.journal.fence
            fence.require(io)
            managed = self.original.managed
            if ((self.cutoff.identity.tenant_id, self.cutoff.identity.home_id, self.cutoff.identity.connector_id)
                    != managed.identity or self.cutoff.account != managed.account
                    or self.cutoff.release_uid != managed.release_uid
                    or self.cutoff.peer.boot_id != self.native.boot_id
                    or (self.cutoff.peer.pid, self.cutoff.peer.started)
                        != self.native.processes['happyranch-connector.service']
                    or self.cutoff.io is not io or self.native_journal.io is not io
                    or self.cutoff.fence is not fence or self.native_journal.fence is not fence
                    or self.native_journal.transport.managed != self.original.managed
                    or self.native_journal.load() != self.native
                    or self.journal.load() != self.original):
                raise RetirementUncertain()
            self.journal.require_original_members(self.original)
            if (OwnedService(io, self.original.ownership.service).observe() != self.original.ownership.native
                    or self.native_journal.transport.observe() != self.native):
                raise RetirementUncertain()
            self.journal.require_original_members(self.original)
            self.cutoff._require_original_peer()
            fence.require(io)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    def revoke(self) -> CutoffReceipt:
        """Commit denial through the real original registry and read it back.

        Any failure preserves committed denial and all original evidence. No
        guessed rollback, second registry or follow-on native effect is supplied.
        """
        self.require_original()
        receipt = self.cutoff.revoke()
        receipt.require()
        return receipt


    def revoke_and_stop_controller(self) -> OwnedStopReceipt:
        """Join actual cutoff to each owned controller native effect.

        The original managed connector must remain alive to recheck its receipt
        before both controller stop and disable. This does not stop that
        connector, authorize cleanup, or turn native partial progress into final
        retirement. Explicit recovery must use the durable original evidence.
        """
        receipt = self.revoke()
        controller = OwnedService(self.journal.io, self.original.ownership.service)
        return controller.stop_and_disable(expected=self.original.ownership.native,
            before_effect=receipt.require)
