"""Explicit native retirement of a proven prepared, never activated layout.

Absence alone supplies no cutoff proof for an active installation. This owner
requires historical inert provenance, original and current absent authority,
and fresh complete native quiescence. It neither initializes trust nor emits a
final receipt. Cleanup, account disposition and package replacement are separate.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from runtime.remote_access.home_retirement import HomeRetirement, RetirementUncertain, STATE_DIRECTORY
from runtime.remote_access.home_retirement_cutoff import _PEER_RECORD
from runtime.remote_access.home_retirement_fence import RetirementFence
from runtime.remote_access.home_retirement_inventory import LegacyInventory, LegacyManagedMembers, LegacyOwnership
from runtime.remote_access.home_retirement_journal import OriginalInventory, RetirementJournal
from runtime.remote_access.home_retirement_service import OwnedService, OwnedStopReceipt
from runtime.remote_access.home_retirement_transport import ManagedObservation, ManagedStopReceipt, ManagedTransport
from runtime.remote_access.home_retirement_transport_journal import ManagedTransportJournal, _NAME as _NATIVE_RECORD

if TYPE_CHECKING:
    from runtime.remote_access.installer import ManagedInstaller

_AUTHORITY = tuple('var/lib/happyranch-connector/trust-state.json' + suffix
                   for suffix in ('', '.anchor', '.lock')) + tuple(
    'run/happyranch-connector/' + name for name in ('control.sock', 'ingress.sock'))
_ACTIVE_RECORDS = (_PEER_RECORD, 'home-activation.json')


class InertRetirement:
    def __init__(self, journal: RetirementJournal, original: OriginalInventory,
                 native_journal: ManagedTransportJournal, native: ManagedObservation) -> None:
        self.journal, self.original = journal, original
        self.native_journal, self.native = native_journal, native

    @staticmethod
    def _layout(ownership: LegacyOwnership, managed: LegacyManagedMembers) -> None:
        if (ownership.activation is not None or ownership.native.process is not None
                or ownership.native.link is not None or managed.live_sidecar
                or any(managed.members[path] is not None for path in _AUTHORITY)):
            raise RetirementUncertain()

    @classmethod
    def capture(cls, io: ManagedInstaller, fence: RetirementFence) -> InertRetirement:
        """First preparation only; no active fallback or native effects."""
        try:
            inventory = LegacyInventory(io)
            ownership = inventory.observe(fence=fence)
            managed = inventory.managed_members(ownership, fence=fence)
            cls._layout(ownership, managed)
            if any(HomeRetirement(io)._present(STATE_DIRECTORY + '/' + name) for name in _ACTIVE_RECORDS):
                raise RetirementUncertain()
            controller = OwnedService(io, ownership.service)
            if controller.observe(stopped=True, disabled=True) != ownership.native:
                raise RetirementUncertain()
            transport = ManagedTransport(io, fence, managed)
            native = transport.observe(stopped=True)
            journal = RetirementJournal(io, fence)
            original = journal.capture(ownership)
            if original.managed != managed:
                raise RetirementUncertain()
            native_journal = ManagedTransportJournal(transport)
            if native_journal.capture() != native:
                raise RetirementUncertain()
            owner = cls(journal, original, native_journal, native)
            owner.require()
            return owner
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    @classmethod
    def from_saved(cls, io: ManagedInstaller, fence: RetirementFence) -> InertRetirement:
        """Reopen complete originals; finish only an absent native publication.

        Missing/torn original evidence refuses without recapture. Completing
        the first native publication needs unchanged original members and fresh
        inert ownership/quiescence, before any native effect is attempted.
        """
        try:
            journal = RetirementJournal(io, fence)
            original = journal.load()
            cls._layout(original.ownership, original.managed)
            journal.require_original_members(original)
            native_journal = ManagedTransportJournal(ManagedTransport(io, fence, original.managed))
            reader = HomeRetirement(io)
            if any(reader._present(STATE_DIRECTORY + '/' + name) for name in _ACTIVE_RECORDS):
                raise RetirementUncertain()
            OwnedService(io, original.ownership.service).observe(stopped=True, disabled=True)
            native_journal.transport.observe(stopped=True)
            native = (native_journal.load() if reader._present(STATE_DIRECTORY + '/' + _NATIVE_RECORD)
                      else native_journal.capture())
            owner = cls(journal, original, native_journal, native)
            owner.require()
            return owner
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    def require(self, *, disabled: bool = False) -> None:
        """Read back original inert lineage and current quiescence every time."""
        try:
            io, fence = self.journal.io, self.journal.fence
            fence.require(io)
            self._layout(self.original.ownership, self.original.managed)
            if (self.journal.load() != self.original or self.native_journal.load() != self.native
                    or self.native_journal.io is not io or self.native_journal.fence is not fence
                    or self.native_journal.transport.managed != self.original.managed
                    or any(process is not None for process in self.native.processes.values())
                    or any(dict(props)['ActiveState'] not in ('inactive', 'failed')
                           for props in self.native.properties.values())
                    or any(HomeRetirement(io)._present(STATE_DIRECTORY + '/' + name) for name in _ACTIVE_RECORDS)):
                raise RetirementUncertain()
            self.journal.require_original_members(self.original)
            controller = OwnedService(io, self.original.ownership.service)
            controller._same_members(self.original.ownership.native, controller.observe(stopped=True, disabled=True))
            transport = self.native_journal.transport
            current = transport.stopped_original(self.native)
            if disabled and transport.observe(stopped=True, disabled=True) != current:
                raise RetirementUncertain()
            self.journal.require_original_members(self.original)
            fence.require(io)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    def stop_and_disable(self) -> tuple[OwnedStopReceipt, ManagedStopReceipt]:
        """Disable only proven inert native owners; skip confirmed effects."""
        self.require()
        controller = OwnedService(self.journal.io, self.original.ownership.service)
        dedicated = controller.stop_and_disable(expected=controller.observe(stopped=True, disabled=True),
            before_effect=self.require)
        transport = self.native_journal.transport
        managed = transport.stop_and_disable(transport.stopped_original(self.native), before_effect=self.require)
        controller.confirm(dedicated)
        transport.confirm(managed)
        self.require(disabled=True)
        return dedicated, managed
