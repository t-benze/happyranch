"""Read-only original-death cutoff proof for explicit retirement recovery.

No native effect, registry replacement or trust mutation is supplied here.
The full coordinator still owns selective cleanup and final publication.
"""
from __future__ import annotations

import os
import stat

from runtime.remote_access.home_retirement import RetirementUncertain, _DIRECTORY_FLAGS, _signature
from runtime.remote_access.home_retirement_cutoff import RetirementCutoff
from runtime.remote_access.home_retirement_service import OwnedService, boot_identity
from runtime.remote_access.home_retirement_transport_journal import ManagedTransportJournal
from runtime.remote_access.state_store import StateStoreError


class RecoveredCutoffReceipt:
    """Live independent death/quiescence/deny proof, not a durable phase flag."""

    def __init__(self, owner: OriginalDeathRecovery, generation: int) -> None:
        self._owner, self._generation = owner, generation

    def require(self) -> None:
        self._owner.confirm(self._generation)


class OriginalDeathRecovery:
    def __init__(self, cutoff: RetirementCutoff, journal: ManagedTransportJournal, *, native_cleanup: bool = False) -> None:
        self.cutoff, self.journal, self.transport = cutoff, journal, journal.transport
        self.native_cleanup = native_cleanup
        if ((cutoff.io.root, cutoff.io.root_owner) != (journal.io.root, journal.io.root_owner)
                or cutoff.account != self.transport.managed.account
                or cutoff.release_uid != self.transport.managed.release_uid
                or (cutoff.identity.tenant_id, cutoff.identity.home_id, cutoff.identity.connector_id)
                    != self.transport.managed.identity):
            raise RetirementUncertain()

    def _original_gone(self, boot: str) -> None:
        owner, io = self.cutoff, self.cutoff.io
        if boot_identity(io) != boot:
            raise RetirementUncertain()
        if boot != owner.peer.boot_id:
            # The independently observed current kernel boot cannot contain a
            # process from the saved original boot. Native quiescence remains
            # separately mandatory, including on this branch.
            return
        with io._publication_parent('proc') as (parent, check):
            name = str(owner.peer.pid)
            try:
                before = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                check()
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    return
                raise RetirementUncertain()
            if not stat.S_ISDIR(before.st_mode):
                raise RetirementUncertain()
            descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent)
            try:
                if _signature(os.fstat(descriptor)) != _signature(before):
                    raise RetirementUncertain()
                raw = OwnedService._proc_read(descriptor, 'stat')
                fields = raw.rsplit(') ', 1)
                if (len(fields) != 2 or fields[0].split(' (', 1)[0] != name
                        or len(fields[1].split()) < 20 or not fields[1].split()[19].isascii()
                        or not fields[1].split()[19].isdigit() or int(fields[1].split()[19]) <= 0
                        or int(fields[1].split()[19]) == owner.peer.started):
                    raise RetirementUncertain()
                check()
                if (OwnedService._proc_read(descriptor, 'stat') != raw
                        or _signature(os.fstat(descriptor)) != _signature(before)
                        or _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != _signature(before)):
                    raise RetirementUncertain()
            finally:
                os.close(descriptor)

    def _observe(self) -> int:
        owner = self.cutoff
        try:
            owner.remaining()
            owner._require_original_peer()
            self.journal.fence.require(owner.io)
            original = self.journal.load()
            if (original.boot_id != owner.peer.boot_id
                    or original.processes['happyranch-connector.service'] != (owner.peer.pid, owner.peer.started)):
                raise RetirementUncertain()
            state, generation = owner.snapshot()
            owner._denied(state)
            observe = self.transport.observe_cleanup if self.native_cleanup else self.transport.stopped_original
            current = observe(original)
            self._original_gone(current.boot_id)
            owner._require_original_peer()
            state, after = owner.snapshot()
            owner._denied(state)
            if after != generation or observe(original) != current:
                raise RetirementUncertain()
            self._original_gone(current.boot_id)
            owner.remaining()
            return generation
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, StateStoreError):
            raise RetirementUncertain() from None

    def recover(self) -> RecoveredCutoffReceipt:
        return RecoveredCutoffReceipt(self, self._observe())

    def confirm(self, generation: int) -> None:
        if self._observe() != generation:
            raise RetirementUncertain()
