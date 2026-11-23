"""Active retirement preparation and real original-owner cutoff composition.

The explicit caller must hold the original retirement fence. This owner joins
original evidence before revocation and can guard dedicated controller native
effects with the actual live cutoff receipt. Managed native effects additionally
require that live receipt or the existing independent original-death/quiescence
proof. Stopped retirement can selectively remove original private leaves under
fresh proofs and held operation exclusion. Account changes, package replacement
and final retired publication remain separate obligations.
Inert/pre-activation retirement and post-effect recovery have separate proofs.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

from runtime.remote_access.home_retirement import HomeRetirement, RetirementUncertain, STATE_DIRECTORY
from runtime.remote_access.home_retirement_cleanup import ExecutableCleanup, ManagedCleanup, PrivateCleanup, UnitCleanup
from runtime.remote_access.home_retirement_cutoff import CutoffReceipt, OriginalPeer, RetirementCutoff
from runtime.remote_access.home_retirement_fence import RetirementFence
from runtime.remote_access.home_retirement_inventory import LegacyInventory
from runtime.remote_access.home_retirement_journal import OriginalInventory, RetirementJournal, _NAME as _INVENTORY_RECORD
from runtime.remote_access.home_retirement_operations import AuxiliaryExclusion, held_auxiliary_operations, retire_auxiliary_domains
from runtime.remote_access.home_retirement_service import OwnedService, OwnedStopReceipt
from runtime.remote_access.home_retirement_recovery import OriginalDeathRecovery, RecoveredCutoffReceipt
from runtime.remote_access.home_retirement_transport import ManagedObservation, ManagedStopReceipt, ManagedTransport
from runtime.remote_access.home_retirement_transport_journal import ManagedTransportJournal, _NAME as _NATIVE_RECORD
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.state_store import StateStoreError

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

    @classmethod
    def from_saved(cls, io: ManagedInstaller, fence: RetirementFence) -> ActiveRetirement:
        """Explicit pre-revoke recovery without replacing saved original facts.

        Existing inventory is loaded, never recaptured. Before its first
        publication, both journals must be absent and intact historical lineage
        must still select the saved live peer. An orphan native record refuses.
        Finish an absent native journal only after original-member/native/peer
        readback. Committed trust changes after preparation use resume_revoke.
        """
        try:
            journal = RetirementJournal(io, fence)
            reader = HomeRetirement(io)
            cutoff = None
            if not reader._present(STATE_DIRECTORY + '/' + _INVENTORY_RECORD):
                if reader._present(STATE_DIRECTORY + '/' + _NATIVE_RECORD):
                    raise RetirementUncertain()
                inventory = LegacyInventory(io)
                ownership = inventory.observe(fence=fence)
                managed = inventory.managed_members(ownership, fence=fence)
                if ownership.activation is None or not managed.live_sidecar:
                    raise RetirementUncertain()
                current = ManagedTransport(io, fence, managed).observe()
                process = current.processes['happyranch-connector.service']
                if process is None:
                    raise RetirementUncertain()
                cutoff = RetirementCutoff.from_saved_peer(io, fence,
                    ConnectorIdentity(tenant_id=managed.identity[0], home_id=managed.identity[1],
                        connector_id=managed.identity[2]), managed.release_uid)
                if cutoff.peer != OriginalPeer(*process, current.boot_id):
                    raise RetirementUncertain()
                cutoff.capture_original()
                # Exclusive first publication only. No missing-record fallback
                # replaces a saved inventory or adopts a different live peer.
                if journal.capture(ownership).managed != managed:
                    raise RetirementUncertain()
            original = journal.load()
            managed = original.managed
            if original.ownership.activation is None or not managed.live_sidecar:
                raise RetirementUncertain()
            if cutoff is None:
                cutoff = RetirementCutoff.from_saved_peer(io, fence,
                    ConnectorIdentity(tenant_id=managed.identity[0], home_id=managed.identity[1],
                        connector_id=managed.identity[2]), managed.release_uid)
            journal.require_original_members(original)
            if OwnedService(io, original.ownership.service).observe() != original.ownership.native:
                raise RetirementUncertain()
            # This is status/readback from the saved original process, not a
            # second registry, revoke acknowledgement or new peer discovery.
            cutoff.capture_original()
            journal.require_original_members(original)
            native_journal = ManagedTransportJournal(ManagedTransport(io, fence, managed))
            with native_journal.transport.reader._entry(STATE_DIRECTORY + '/' + _NATIVE_RECORD) as (_, _, info):
                missing = info is None
            native = native_journal.capture() if missing else native_journal.load()
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
            self._require_records()
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
        return self._stop_controller(self.revoke())

    def _stop_controller(self, receipt: CutoffReceipt) -> OwnedStopReceipt:
        controller = OwnedService(self.journal.io, self.original.ownership.service)
        return controller.stop_and_disable(expected=self.original.ownership.native,
            before_effect=receipt.require)

    def _require_records(self) -> None:
        io, fence, managed = self.journal.io, self.journal.fence, self.original.managed
        fence.require(io)
        if ((self.cutoff.identity.tenant_id, self.cutoff.identity.home_id, self.cutoff.identity.connector_id)
                != managed.identity or self.cutoff.account != managed.account
                or self.cutoff.release_uid != managed.release_uid
                or self.cutoff.peer.boot_id != self.native.boot_id
                or (self.cutoff.peer.pid, self.cutoff.peer.started)
                    != self.native.processes['happyranch-connector.service']
                or self.cutoff.io is not io or self.native_journal.io is not io
                or self.cutoff.fence is not fence or self.native_journal.fence is not fence
                or self.native_journal.transport.managed != managed
                or self.native_journal.load() != self.native
                or self.journal.load() != self.original):
            raise RetirementUncertain()

    def revoke_and_stop_services(self) -> StoppedRetirement:
        """Join saved original evidence to all fixed native stop/disable effects.

        A live original acknowledgement can guard an effect. When that peer has
        gone, only the existing independent original-death plus all-managed
        quiescence and anchored denial proof can replace it. Partial native stop
        is not that proof and cannot authorize the next effect.
        """
        return self._stop_services(self.revoke())

    @classmethod
    def resume_revoke(cls, io: ManagedInstaller, fence: RetirementFence) -> StoppedRetirement:
        """Finish partial committed denial through the saved LIVE original owner.

        Current trust comes from the real anchored pair, never the pre-revoke
        inventory or a new registry. No original artifact/native owner can be
        replaced. Failed physical closure cannot become an acknowledgement by
        retrying; only the separate independent-death path can recover it.
        """
        try:
            owner = StoppedRetirement._saved_owner(io, fence)
            owner._require_records()
            owner.journal.require_revoke_members(owner.original)
            owner.cutoff.snapshot()
            controller = OwnedService(io, owner.original.ownership.service)
            if (controller.observe() != owner.original.ownership.native
                    or owner.native_journal.transport.observe() != owner.native):
                raise RetirementUncertain()
            owner.cutoff.capture_original()
            owner.journal.require_revoke_members(owner.original)
            receipt = owner.cutoff.revoke()
            return owner._stop_services(receipt)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, StateStoreError):
            raise RetirementUncertain() from None

    def _stop_services(self, receipt: CutoffReceipt) -> StoppedRetirement:
        controller_receipt = self._stop_controller(receipt)
        return self._stop_managed(receipt, controller_receipt)

    @classmethod
    def resume_controller(cls, io: ManagedInstaller, fence: RetirementFence) -> StoppedRetirement:
        """Finish controller/managed effects with the SAME live connector.

        Fresh native readback must prove the controller stopped, then skips
        completed disable. Before-stop recovery uses ``resume_revoke``. Managed
        boot/process/artifacts must still match, and actual original-registry
        acknowledgement is obtained again; no saved phase supplies authority.
        A previously stopped sidecar may have flushed its original runtime;
        current stopped/process-empty native readback is required each time.
        """
        try:
            owner = StoppedRetirement._saved_owner(io, fence)
            transport = owner.native_journal.transport
            current_managed = transport.observe()
            transport._same_members(owner.native, current_managed, set())
            controller = OwnedService(io, owner.original.ownership.service)
            current = controller.observe(stopped=True)
            controller._same_members(owner.original.ownership.native, current)
            sidecar = 'happyranch-tsnet-sidecar.service'
            owner.journal.require_controller_members(owner.original, sidecar_stopped=(
                current_managed.processes[sidecar] is None
                and dict(current_managed.properties[sidecar])['ActiveState'] in ('inactive', 'failed')))
            owner.cutoff.capture_original()
            receipt = owner.cutoff.revoke()

            def guard() -> None:
                owner._require_records()
                receipt.require()
                observed_managed = transport.observe()
                transport._same_members(owner.native, observed_managed, set())
                observed = controller.observe(stopped=True)
                controller._same_members(owner.original.ownership.native, observed)
                owner.journal.require_controller_members(owner.original, sidecar_stopped=(
                    observed_managed.processes[sidecar] is None
                    and dict(observed_managed.properties[sidecar])['ActiveState'] in ('inactive', 'failed')))

            guard()
            result = controller.stop_and_disable(expected=current, before_effect=guard)
            return owner._stop_managed(receipt, result)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, StateStoreError):
            raise RetirementUncertain() from None

    def _stop_managed(self, receipt: CutoffReceipt, controller_receipt: OwnedStopReceipt) -> StoppedRetirement:
        controller = OwnedService(self.journal.io, self.original.ownership.service)
        recovery = OriginalDeathRecovery(self.cutoff, self.native_journal)
        def guard() -> None:
            self._require_records()
            controller.confirm(controller_receipt)
            try:
                receipt.require()
            except RetirementUncertain:
                recovery.recover().require()
        guard()
        transport = self.native_journal.transport
        current = transport.observe()
        # Owned controller disable intentionally changes the shared wants
        # directory metadata. Its identity and every original managed artifact,
        # boot and process remain pinned; no replacement is adopted here.
        transport._same_members(self.native, current, set())
        managed_receipt = transport.stop_and_disable(current, before_effect=guard)
        proof = StoppedRetirement(self, controller_receipt, managed_receipt, recovery.recover())
        proof.require()
        return proof


class StoppedRetirement:
    """Fresh native quiescence/cutoff readback, never a final retirement record."""

    def __init__(self, owner: ActiveRetirement, controller: OwnedStopReceipt,
                 managed: ManagedStopReceipt, cutoff: RecoveredCutoffReceipt) -> None:
        self._owner, self.controller, self.managed, self.cutoff = owner, controller, managed, cutoff

    def __repr__(self) -> str:
        return 'StoppedRetirement(<redacted>)'

    @staticmethod
    def _saved_owner(io: ManagedInstaller, fence: RetirementFence) -> ActiveRetirement:
        fence.require(io)
        journal = RetirementJournal(io, fence)
        original = journal.load()
        managed = original.managed
        if original.ownership.activation is None or not managed.live_sidecar:
            raise RetirementUncertain()
        transport = ManagedTransport(io, fence, managed)
        native_journal = ManagedTransportJournal(transport)
        native = native_journal.load()
        cutoff = RetirementCutoff.from_saved_peer(io, fence,
            ConnectorIdentity(tenant_id=managed.identity[0], home_id=managed.identity[1],
                connector_id=managed.identity[2]), managed.release_uid)
        owner = ActiveRetirement(journal, original, native_journal, native, cutoff)
        owner._require_records()
        return owner

    @classmethod
    def from_saved(cls, io: ManagedInstaller, fence: RetirementFence) -> StoppedRetirement:
        """Explicit read-only recovery of the fully stopped/disabled phase.

        Load all original records without discovery or recapture. Only fresh
        independent original-death/denial/quiescence and exact original native
        artifacts can produce new in-process readbacks. Partial stop/disable,
        cleanup and final publication require their separate recovery owners.
        No native effect or registry replacement is attempted here.
        """
        try:
            owner = cls._saved_owner(io, fence)
            original, native, transport = owner.original, owner.native, owner.native_journal.transport
            denied = OriginalDeathRecovery(owner.cutoff, owner.native_journal).recover()
            controller = OwnedService(io, original.ownership.service)
            current_controller = controller.observe(stopped=True, disabled=True)
            controller._same_members(original.ownership.native, current_controller)
            current_managed = transport.stopped_original(native)
            if transport.observe(stopped=True, disabled=True) != current_managed:
                raise RetirementUncertain()
            result = cls(owner, OwnedStopReceipt(controller.identity.unit, current_controller),
                ManagedStopReceipt(current_managed), denied)
            result.require()
            return result
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    @classmethod
    def resume_native(cls, io: ManagedInstaller, fence: RetirementFence) -> StoppedRetirement:
        """Explicitly finish native effects after proven managed quiescence.

        Every effect needs original evidence, independent original death and
        anchored denial. Native readback skips completed effects. A live or
        partially stopped managed owner supplies no recovery authority here.
        """
        try:
            owner = cls._saved_owner(io, fence)
            transport = owner.native_journal.transport
            denied = OriginalDeathRecovery(owner.cutoff, owner.native_journal).recover()
            current_managed = transport.stopped_original(owner.native)
            boot = current_managed.boot_id
            controller = OwnedService(io, owner.original.ownership.service)
            current_controller = controller.observe()
            original_controller = owner.original.ownership.native
            controller._same_members(original_controller, current_controller)
            if current_controller.process is not None and (
                    current_controller.process != original_controller.process or boot != owner.native.boot_id):
                raise RetirementUncertain()

            def guard() -> None:
                owner._require_records()
                denied.require()
                if owner.cutoff._boot_id() != boot:
                    raise RetirementUncertain()

            controller_receipt = controller.stop_and_disable(expected=current_controller, before_effect=guard)

            def managed_guard() -> None:
                guard()
                controller.confirm(controller_receipt)

            managed_guard()
            current_managed = transport.stopped_original(owner.native)
            managed_receipt = transport.stop_and_disable(current_managed, before_effect=managed_guard)
            result = cls(owner, controller_receipt, managed_receipt, denied)
            result.require()
            return result
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    def require(self) -> None:
        owner = self._owner
        owner._require_records()
        OwnedService(owner.journal.io, owner.original.ownership.service).confirm(self.controller)
        owner.native_journal.transport.confirm(self.managed)
        self.cutoff.require()

    def remove_private(self) -> None:
        """Explicit selective private-leaf cleanup, including interrupted reuse.

        Native artifacts, original records, managed trust and operation locks
        remain available for independent recovery. No final receipt is emitted.
        """
        self.require()
        with held_auxiliary_operations(self._owner.journal, self._owner.original, cleanup=True) as exclusion:
            def guard() -> None:
                self.require()
                exclusion.require()
            PrivateCleanup(self._owner.journal, self._owner.original, before_effect=guard).remove()

    def remove_auxiliary(self) -> None:
        """Remove original issuer/minter leaves, retaining all lock domains.

        Fresh saved recovery can repeat this entry after partial removal.
        Neither absent leaves nor held locks substitute for native/cutoff
        proof, and this partial cleanup never publishes final retirement.
        """
        self.require()
        with held_auxiliary_operations(self._owner.journal, self._owner.original, cleanup=True) as exclusion:
            exclusion.remove_leaves(before_effect=self.require)
            self.require()

    def remove_private_directories(self) -> None:
        """Finish empty private roots under current cutoff/native exclusion.

        Leaf removal must already be complete. Fresh saved recovery can finish
        a partial rmdir or lost parent sync; no native/account effect is replayed.
        """
        self.require()
        with held_auxiliary_operations(self._owner.journal, self._owner.original, cleanup=True) as exclusion:
            def guard() -> None:
                self.require()
                exclusion.require()
            PrivateCleanup(self._owner.journal, self._owner.original, before_effect=guard).remove_directories()

    def remove_managed(self) -> None:
        """Remove original managed leaves while retaining cutoff evidence.

        This partial entry keeps trust/native artifacts and operation domains
        for fresh explicit recovery. Every removal needs current independent
        stopped/cutoff proof and held original issuer/minter exclusion.
        """
        self.require()
        with held_auxiliary_operations(self._owner.journal, self._owner.original, cleanup=True) as exclusion:
            def guard() -> None:
                self.require()
                exclusion.require()
            ManagedCleanup(self._owner.journal, self._owner.original, before_effect=guard).remove()

    @classmethod
    def remove_managed_directories(cls, io: ManagedInstaller, fence: RetirementFence) -> None:
        """Finish three original empty managed roots, retaining denied trust.

        Reopen saved originals after partial cleanup, including unit removal.
        Independent death/denial and current native quiescence still guard
        each rmdir; absent runtime directories cannot supply those proofs.
        """
        try:
            owner = cls._saved_owner(io, fence)
            controller = OwnedService(io, owner.original.ownership.service)
            recovery = OriginalDeathRecovery(owner.cutoff, owner.native_journal, native_cleanup=True)
            def require() -> None:
                owner._require_records()
                controller.observe_cleanup(owner.original.ownership.native)
                recovery.recover().require()
                controller.observe_cleanup(owner.original.ownership.native)
            require()
            with held_auxiliary_operations(owner.journal, owner.original, cleanup=True) as exclusion:
                def guard() -> None:
                    require()
                    exclusion.require()
                ManagedCleanup(owner.journal, owner.original, before_effect=guard).remove_directories()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    @classmethod
    def remove_operation_domains(cls, io: ManagedInstaller, fence: RetirementFence) -> None:
        """Finish original issuer/minter domains after their leaves are gone.

        Missing domains are admitted only under independent original-peer
        death, anchored denial and current complete native quiescence. Original
        journals and managed trust remain available; no final receipt is made.
        """
        try:
            owner = cls._saved_owner(io, fence)
            controller = OwnedService(io, owner.original.ownership.service)
            recovery = OriginalDeathRecovery(owner.cutoff, owner.native_journal, native_cleanup=True)
            def require() -> None:
                owner._require_records()
                controller.observe_cleanup(owner.original.ownership.native)
                recovery.recover().require()
                controller.observe_cleanup(owner.original.ownership.native)
            retire_auxiliary_domains(owner.journal, owner.original, before_effect=require)
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    @classmethod
    def remove_controller_executable(cls, io: ManagedInstaller, fence: RetirementFence) -> None:
        """Remove only the original dedicated binary, with partial-sync recovery.

        Original journals, account, trust and operation domains remain required.
        Cleanup-aware quiescence and independent death/denial guard each effect;
        neither missing binary nor held domains supply writer-death authority.
        """
        try:
            owner = cls._saved_owner(io, fence)
            controller = OwnedService(io, owner.original.ownership.service)
            recovery = OriginalDeathRecovery(owner.cutoff, owner.native_journal, native_cleanup=True)
            def require() -> None:
                owner._require_records()
                controller.observe_cleanup(owner.original.ownership.native)
                recovery.recover().require()
                controller.observe_cleanup(owner.original.ownership.native)
            require()
            with held_auxiliary_operations(owner.journal, owner.original, cleanup=True) as exclusion:
                def guard() -> None:
                    require()
                    exclusion.require()
                ExecutableCleanup(owner.journal, owner.original, before_effect=guard).remove()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    @classmethod
    def remove_unit_fragments(cls, io: ManagedInstaller, fence: RetirementFence) -> None:
        """Finish original fragment removal, including fresh partial recovery.

        This explicit entry loads the same original records and never resumes
        stop/disable. Every removal still needs all native owners independently
        quiescent/disabled, original-peer death, anchored denial and held
        original issuer/minter domains. Binaries, trust and accounts remain.
        """
        try:
            owner = cls._saved_owner(io, fence)
            controller = OwnedService(io, owner.original.ownership.service)
            recovery = OriginalDeathRecovery(owner.cutoff, owner.native_journal, native_cleanup=True)
            def require() -> None:
                owner._require_records()
                controller.observe_cleanup(owner.original.ownership.native)
                recovery.recover().require()
                controller.observe_cleanup(owner.original.ownership.native)
            require()
            with held_auxiliary_operations(owner.journal, owner.original, cleanup=True) as exclusion:
                def guard() -> None:
                    require()
                    exclusion.require()
                UnitCleanup(owner.journal, owner.original, owner.native, before_effect=guard).remove()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    @contextmanager
    def held_operations(self) -> Iterator[AuxiliaryExclusion]:
        """Join actual stopped/cutoff proof to held original operation locks.

        Absence is never a held lock. This unchanged-original context provides
        no removal or recovery of partial removal, and emits no final receipt.
        """
        self.require()
        with held_auxiliary_operations(self._owner.journal, self._owner.original) as exclusion:
            self.require()
            exclusion.require()
            yield exclusion
            self.require()
            exclusion.require()
