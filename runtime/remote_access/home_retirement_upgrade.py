"""Canonical explicit retirement sequence shared by upgrade and recovery.

Read-only observations select the earliest remaining operation. They confer no
authority: each existing owner rechecks all its proofs before every effect.
A failed effect ends the invocation; no alternate effect path is attempted.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

from runtime.remote_access.home_retirement import HomeRetirement, RetirementStatus, RetirementUncertain, STATE_DIRECTORY
from runtime.remote_access.home_retirement_cleanup import ExecutableCleanup, InertConnectorCleanup, ManagedCleanup, PrivateCleanup, UnitCleanup
from runtime.remote_access.home_retirement_coordinator import ActiveRetirement, StoppedRetirement
from runtime.remote_access.home_retirement_cutoff import _PEER_RECORD
from runtime.remote_access.home_retirement_fence import RetirementFence, retirement_fence
from runtime.remote_access.home_retirement_inert import InertRetirement
from runtime.remote_access.home_retirement_inventory import LegacyInventory
from runtime.remote_access.home_retirement_journal import OriginalInventory, RetirementJournal, _NAME as _INVENTORY
from runtime.remote_access.home_retirement_operations import AuxiliaryExclusion
from runtime.remote_access.home_retirement_service import OwnedService
from runtime.remote_access.home_retirement_transport import ManagedTransport
from runtime.remote_access.home_retirement_transport_journal import ManagedTransportJournal, _NAME as _NATIVE

if TYPE_CHECKING:
    from runtime.remote_access.installer import ManagedInstaller


def _observed(require: Callable[[], object]) -> bool:
    """Only read-only candidates may be used here, never effect attempts."""
    try:
        require()
        return True
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
        return False


def _cleanup_prefix(journal: RetirementJournal, original: OriginalInventory,
                    native: ManagedTransportJournal, *, fragments: bool) -> None:
    # These existing observers enforce saved-original custody and reject new
    # members. Absence selects an entry; that entry still needs native/cutoff
    # proof and original operation exclusion before it can remove anything.
    private = PrivateCleanup(journal, original, before_effect=journal.fence.require)
    managed = ManagedCleanup(journal, original, before_effect=journal.fence.require)
    if (any(value is not None for value in private._empty_directories().values())
            or any(value is not None for value in managed._observe().values())):
        raise RetirementUncertain()
    if fragments:
        if any(value is not None for value in managed._empty_directories().values()):
            raise RetirementUncertain()
        for cleanup in (ExecutableCleanup(journal, original, before_effect=journal.fence.require),
                UnitCleanup(journal, original, native.load(), before_effect=journal.fence.require)):
            if any(value is not None for value in cleanup._observe().values()):
                raise RetirementUncertain()


def _finish(io: ManagedInstaller, fence: RetirementFence) -> None:
    reader = HomeRetirement(io)
    journal = RetirementJournal(io, fence)
    saved = reader._present(STATE_DIRECTORY + '/' + _INVENTORY)
    native_saved = reader._present(STATE_DIRECTORY + '/' + _NATIVE)
    if not saved:
        if native_saved:
            raise RetirementUncertain()
        if reader._present(STATE_DIRECTORY + '/' + _PEER_RECORD):
            owner = ActiveRetirement.from_saved(io, fence)
        else:
            ownership = LegacyInventory(io).observe(fence=fence)
            owner = (InertRetirement.capture(io, fence) if ownership.activation is None
                     else ActiveRetirement.capture(io, fence))
    else:
        original = journal.load()
        inert = original.ownership.activation is None
        cleanup = InertRetirement if inert else StoppedRetirement
        if not native_saved:
            owner = (InertRetirement.from_saved(io, fence) if inert else ActiveRetirement.from_saved(io, fence))
        else:
            native = ManagedTransportJournal(ManagedTransport(io, fence, original.managed))
            # Account proof permits partial account/receipt recovery only after
            # independently observing every earlier cleanup and native fact.
            disposition = (InertRetirement._account_disposition(io, fence, receipt_cleanup=True)
                           if inert else StoppedRetirement._account_disposition(io, fence))
            if _observed(disposition.before_effect):
                if not _observed(disposition.require_retired):
                    cleanup.retire_account(io, fence)
                cleanup.remove_original_receipts(io, fence)
                cleanup.publish_final(io, fence)
                return
            if _observed(lambda: _cleanup_prefix(journal, original, native, fragments=True)):
                _finish_domains(io, fence, inert=inert)
                return
            if _observed(lambda: _cleanup_prefix(journal, original, native, fragments=False)):
                _finish_native_files(io, fence, inert=inert)
                return
            if inert:
                owner = InertRetirement(journal, original, native, native.load())
                if not _observed(lambda: owner.require(disabled=True, cleanup=True)):
                    owner.stop_and_disable()
            elif not _observed(lambda: StoppedRetirement.from_saved(io, fence)):
                current = native.transport.observe()
                native.transport._same_members(native.load(), current, set())
                if current.processes['happyranch-connector.service'] is None:
                    StoppedRetirement.resume_native(io, fence)
                elif OwnedService(io, original.ownership.service).observe().process is None:
                    ActiveRetirement.resume_controller(io, fence)
                else:
                    ActiveRetirement.resume_revoke(io, fence)
            _finish_leaves(io, fence, inert=inert)
            return
    inert = isinstance(owner, InertRetirement)
    if inert:
        owner.stop_and_disable()
    else:
        owner.revoke_and_stop_services()
    _finish_leaves(io, fence, inert=inert)


def _finish_leaves(io: ManagedInstaller, fence: RetirementFence, *, inert: bool) -> None:
    # Each distinct explicit operation reopens saved originals. An in-flight
    # operation's cutoff deadline is never reset or extended by this sequence.
    if inert:
        InertRetirement.resume_cleanup(io, fence).remove_leaves()
        InertRetirement.resume_cleanup(io, fence).remove_private_directories()
    else:
        for method in ('remove_auxiliary', 'remove_private', 'remove_managed', 'remove_private_directories'):
            getattr(StoppedRetirement.from_saved(io, fence), method)()
    _finish_native_files(io, fence, inert=inert)


def _finish_native_files(io: ManagedInstaller, fence: RetirementFence, *, inert: bool) -> None:
    cleanup = InertRetirement if inert else StoppedRetirement
    for method in ('remove_unit_fragments', 'remove_controller_executable', 'remove_managed_directories'):
        getattr(cleanup, method)(io, fence)
    _finish_domains(io, fence, inert=inert)


def _finish_domains(io: ManagedInstaller, fence: RetirementFence, *, inert: bool) -> None:
    cleanup = InertRetirement if inert else StoppedRetirement
    journal = RetirementJournal(io, fence)
    original = journal.load()
    if inert:
        connector = InertConnectorCleanup(journal, original, before_effect=fence.require)
        if any(value is not None for value in connector._empty_directories().values()):
            cleanup.remove_connector_directory(io, fence)
    # After partial trust removal the old domains must already be absent. Do
    # not ask an earlier operation to re-establish a now-removed trust pair.
    domains = AuxiliaryExclusion(journal, original, cleanup=True, retirement_proof=fence.require)
    if any(value is not None for value in domains._original_members().values()):
        cleanup.remove_operation_domains(io, fence)
    if not inert:
        cleanup.remove_trust(io, fence)
    cleanup.retire_account(io, fence)
    cleanup.remove_original_receipts(io, fence)
    cleanup.publish_final(io, fence)


def retire(io: ManagedInstaller) -> RetirementStatus:
    """Explicit retirement; fresh/completed layouts have no mutation or replay."""
    if io.layout != 'deb':
        raise RetirementUncertain()
    reader = HomeRetirement(io)
    status = reader.detect()
    if status.state in ('fresh', 'retired'):
        return status
    # Existing final evidence with drift is never a new retirement/adoption.
    if reader._receipt() is not None:
        raise RetirementUncertain()
    with retirement_fence(io) as fence:
        _finish(io, fence)
        status = reader.detect()
        if status.state != 'retired':
            raise RetirementUncertain()
    return status
