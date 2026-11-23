"""Explicit native retirement of a proven prepared, never activated layout.

Absence alone supplies no cutoff proof for an active installation. This owner
requires historical inert provenance, original and current absent authority,
and fresh complete native quiescence. Selective cleanup retains shared
executables, accounts, operation locks and original journals. It neither
initializes trust nor emits a final receipt or package replacement gate.
"""
from __future__ import annotations

import os
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from runtime.remote_access.home_retirement import HomeRetirement, RetirementUncertain, STATE_DIRECTORY
from runtime.remote_access.home_retirement_cleanup import ExecutableCleanup, InertConnectorCleanup, ManagedCleanup, PrivateCleanup, UnitCleanup
from runtime.remote_access.home_retirement_cutoff import _PEER_RECORD
from runtime.remote_access.home_retirement_fence import RetirementFence
from runtime.remote_access.home_retirement_inventory import LegacyInventory, LegacyManagedMembers, LegacyOwnership
from runtime.remote_access.home_retirement_journal import OriginalInventory, RetirementJournal
from runtime.remote_access.home_retirement_operations import held_auxiliary_operations, retire_auxiliary_domains
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

    @classmethod
    def resume_cleanup(cls, io: ManagedInstaller, fence: RetirementFence) -> InertRetirement:
        """Load complete originals after interrupted explicit leaf removal.

        No first publication or native effect is attempted. Missing originals,
        native uncertainty and new authority refuse before any removal.
        """
        try:
            journal = RetirementJournal(io, fence)
            original = journal.load()
            native_journal = ManagedTransportJournal(ManagedTransport(io, fence, original.managed))
            owner = cls(journal, original, native_journal, native_journal.load())
            owner.require(disabled=True, cleanup=True)
            with held_auxiliary_operations(journal, original, cleanup=True):
                owner.require(disabled=True, cleanup=True)
            return owner
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    def require(self, *, disabled: bool = False, cleanup: bool = False, native_cleanup: bool = False) -> None:
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
                    or cleanup and not disabled
                    or native_cleanup and not cleanup
                    or any(HomeRetirement(io)._present(STATE_DIRECTORY + '/' + name) for name in _ACTIVE_RECORDS)):
                raise RetirementUncertain()
            managed = ManagedCleanup(self.journal, self.original, before_effect=lambda: None)
            def empty_authority() -> None:
                # These parents belong to the managed service account; the
                # root-only status reader cannot walk them on an installed host.
                for path in _AUTHORITY:
                    relative = str(PurePosixPath(path).parent)
                    walker = managed._walker()
                    with walker._directory(relative, contents_may_change=cleanup) as parent:
                        managed._parents(walker)
                        if parent is None:
                            if (relative in self.original.managed.directories
                                    and not (cleanup and (relative in managed._removable_directories
                                        or native_cleanup and relative in InertConnectorCleanup._removable_directories)
                                        and str(PurePosixPath(relative).parent) in walker.directory_identities)):
                                raise RetirementUncertain()
                            continue
                        try:
                            os.stat(PurePosixPath(path).name, dir_fd=parent, follow_symlinks=False)
                        except FileNotFoundError:
                            continue
                        raise RetirementUncertain()
            def members() -> None:
                if not cleanup:
                    self.journal.require_original_members(self.original)
                    return
                # Cleanup admits only absence of original leaves, never their
                # replacement or loss of the historical ownership receipts.
                for name, expected in self.original.ownership.receipt_identities.items():
                    if LegacyInventory(io)._receipt(name, fence)[1] != expected:
                        raise RetirementUncertain()
                for owner in (PrivateCleanup, ManagedCleanup):
                    owner(self.journal, self.original, before_effect=lambda: None)._observe()
            empty_authority()
            members()
            controller = OwnedService(io, self.original.ownership.service)
            if native_cleanup:
                controller.observe_cleanup(self.original.ownership.native)
            else:
                controller._same_members(self.original.ownership.native, controller.observe(stopped=True, disabled=True))
            transport = self.native_journal.transport
            current = transport.observe_cleanup(self.native) if native_cleanup else transport.stopped_original(self.native)
            if disabled and not native_cleanup and transport.observe(stopped=True, disabled=True) != current:
                raise RetirementUncertain()
            empty_authority()
            members()
            fence.require(io)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    @classmethod
    def remove_managed_directories(cls, io: ManagedInstaller, fence: RetirementFence) -> None:
        """Finish original empty runtime/state roots under fresh inert proof.

        The connector trust directory remains required. Each effect rechecks
        original/current empty authority, native quiescence and held domains,
        including explicit recovery after unit or partial directory removal.
        """
        try:
            journal = RetirementJournal(io, fence)
            original = journal.load()
            native_journal = ManagedTransportJournal(ManagedTransport(io, fence, original.managed))
            owner = cls(journal, original, native_journal, native_journal.load())
            def require() -> None:
                owner.require(disabled=True, cleanup=True, native_cleanup=True)
            require()
            with held_auxiliary_operations(journal, original, cleanup=True) as exclusion:
                def guard() -> None:
                    require()
                    exclusion.require()
                ManagedCleanup(journal, original, before_effect=guard).remove_directories()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    @classmethod
    def remove_connector_directory(cls, io: ManagedInstaller, fence: RetirementFence) -> None:
        """Finish the original empty inert connector root, including lost sync.

        Saved empty authority is mandatory; active layouts cannot use this
        entry. Surviving original operation domains remain held throughout.
        No trust pair is initialized, removed or replaced, and no final receipt
        is published. Native cleanup proof rechecks current empty authority.
        """
        try:
            journal = RetirementJournal(io, fence)
            original = journal.load()
            native_journal = ManagedTransportJournal(ManagedTransport(io, fence, original.managed))
            owner = cls(journal, original, native_journal, native_journal.load())
            def require() -> None:
                owner.require(disabled=True, cleanup=True, native_cleanup=True)
            require()
            with held_auxiliary_operations(journal, original, cleanup=True) as exclusion:
                def guard() -> None:
                    require()
                    exclusion.require()
                InertConnectorCleanup(journal, original, before_effect=guard).remove_directories()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    @classmethod
    def remove_operation_domains(cls, io: ManagedInstaller, fence: RetirementFence) -> None:
        """Finish original issuer/minter domains under fresh inert proof.

        Original/current empty authority and complete native quiescence remain
        mandatory even after interrupted domain removal. Trust and original
        journals remain; this operation supplies no final retirement receipt.
        """
        try:
            journal = RetirementJournal(io, fence)
            original = journal.load()
            native_journal = ManagedTransportJournal(ManagedTransport(io, fence, original.managed))
            owner = cls(journal, original, native_journal, native_journal.load())
            def require() -> None:
                owner.require(disabled=True, cleanup=True, native_cleanup=True)
            retire_auxiliary_domains(journal, original, before_effect=require)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    @classmethod
    def remove_controller_executable(cls, io: ManagedInstaller, fence: RetirementFence) -> None:
        """Remove the dedicated original binary under fresh inert quiescence.

        Original/current empty authority and held original operation domains
        remain mandatory after unit removal or interrupted binary parent sync.
        """
        try:
            journal = RetirementJournal(io, fence)
            original = journal.load()
            native_journal = ManagedTransportJournal(ManagedTransport(io, fence, original.managed))
            owner = cls(journal, original, native_journal, native_journal.load())
            def require() -> None:
                owner.require(disabled=True, cleanup=True, native_cleanup=True)
            require()
            with held_auxiliary_operations(journal, original, cleanup=True) as exclusion:
                def guard() -> None:
                    require()
                    exclusion.require()
                ExecutableCleanup(journal, original, before_effect=guard).remove()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    @classmethod
    def remove_unit_fragments(cls, io: ManagedInstaller, fence: RetirementFence) -> None:
        """Explicit original-fragment cleanup with inert partial-removal recovery.

        Original and current absent authority, complete native quiescence and
        held operation domains remain mandatory before each removal. No active
        layout fallback, native replay, binary/account removal or final receipt.
        """
        try:
            journal = RetirementJournal(io, fence)
            original = journal.load()
            native_journal = ManagedTransportJournal(ManagedTransport(io, fence, original.managed))
            owner = cls(journal, original, native_journal, native_journal.load())
            def require() -> None:
                owner.require(disabled=True, cleanup=True, native_cleanup=True)
            require()
            with held_auxiliary_operations(journal, original, cleanup=True) as exclusion:
                def guard() -> None:
                    require()
                    exclusion.require()
                UnitCleanup(journal, original, owner.native, before_effect=guard).remove()
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

    def remove_leaves(self) -> None:
        """Remove only original non-lock leaves under fresh inert proof.

        Explicit recovery reconciles lost parent syncs without unlink replay.
        Current absence is never a substitute for native stopped/disabled proof.
        """
        self.require(disabled=True, cleanup=True)
        with held_auxiliary_operations(self.journal, self.original, cleanup=True) as exclusion:
            def guard() -> None:
                self.require(disabled=True, cleanup=True)
                exclusion.require()
            guard()
            PrivateCleanup(self.journal, self.original, before_effect=guard).remove()
            exclusion.remove_leaves(before_effect=guard)
            ManagedCleanup(self.journal, self.original, before_effect=guard).remove()
            guard()

    def remove_private_directories(self) -> None:
        """Finish only empty original private roots after selective leaves.

        Current empty authority and stopped/disabled native ownership remain
        mandatory, including after an interrupted rmdir and fresh recovery.
        """
        self.require(disabled=True, cleanup=True)
        with held_auxiliary_operations(self.journal, self.original, cleanup=True) as exclusion:
            def guard() -> None:
                self.require(disabled=True, cleanup=True)
                exclusion.require()
            PrivateCleanup(self.journal, self.original, before_effect=guard).remove_directories()
