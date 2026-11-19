"""Guarded activation composition, privately mounted by the fixed root helper.

Public lifecycle calls remain gated until I5. Recovery executes only the
existing Ruling 12/12a verify/publication path, never activation effects.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import threading
import time
from contextlib import contextmanager
from typing import Iterator

from runtime.remote_access.home_activation import (
    ACTIVATION_PATH, INVITE_CONFIG_PATH, HomeActivationReceipt, HomeActivationStore, _generation,
)
from runtime.remote_access.home_activation_coordinator import ActivationRefused, HomeActivationCoordinator
from runtime.remote_access.home_activation_owner import HomeActivationOwner
from runtime.remote_access.home_activation_prepare import HomeActivationPrepare
from runtime.remote_access.home_activation_recovery import HomeActivationRecovery
from runtime.remote_access.home_activation_txn import HomeActivationTransactionStore
from runtime.remote_access.home_live_config import HomeLiveConfigOwner
from runtime.remote_access.home_observer import HomeObserver
from runtime.remote_access.installer import ManagedInstaller, UNITS


class _OperationTransactions(HomeActivationTransactionStore):
    """Borrow the SAME held flock for nested calls by this operation's thread.

    Other threads refuse nonblocking; independent objects/processes still hit the
    original directory flock. The unchanged coordinator keeps its lock contexts.
    """

    def __init__(self, *, io: ManagedInstaller) -> None:
        super().__init__(root=io.root, root_owner=io.root_owner)
        self._mutex = threading.RLock()
        self._depth = 0

    @contextmanager
    def locked(self) -> Iterator[None]:
        if not self._mutex.acquire(blocking=False):
            raise ActivationRefused()
        try:
            if self._depth:
                yield
            else:
                with super().locked():
                    self._depth = 1
                    try:
                        yield
                    finally:
                        self._depth = 0
        finally:
            self._mutex.release()


class HomeActivationBackend:
    def __init__(self, *, io: ManagedInstaller | None = None) -> None:
        self._io = io or ManagedInstaller()
        self._transactions = _OperationTransactions(io=self._io)
        self._prepare = HomeActivationPrepare(io=self._io)
        self._prepare._transactions = self._transactions

    def _compose(self, receipt: HomeActivationReceipt) -> tuple[
            HomeActivationCoordinator, HomeActivationRecovery, HomeObserver]:
        binding = dict(expected_generation=receipt.activation_generation,
                       expected_operation_id=receipt.operation_id)
        config = HomeLiveConfigOwner(io=self._io, **binding)
        owner = HomeActivationOwner(io=self._io, config_owner=config, **binding)
        coordinator = HomeActivationCoordinator(owner=owner, root=self._io.root,
                                                root_owner=self._io.root_owner)
        recovery = HomeActivationRecovery(owner=owner, admin=owner._admin,
            root=self._io.root, root_owner=self._io.root_owner)
        coordinator._transactions = recovery._transactions = self._transactions
        observer = HomeObserver(owner=owner, admin=owner._admin, root=self._io.root)
        return coordinator, recovery, observer

    def enable(self, *, expected_generation: int, authenticated: bool) -> HomeActivationReceipt:
        try:
            _generation(expected_generation)
            if authenticated is not True or os.geteuid() != 0:
                raise ActivationRefused()
            with self._transactions.locked():
                receipts = self._prepare._receipts
                with receipts._parent(ACTIVATION_PATH) as (parent, check):
                    self._io._headscale_no_pending(parent, "home-activation.json")
                    try:
                        os.stat("home-activation.json", dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        exists = False
                    else:
                        exists = True
                    check()
                transaction = self._transactions.read_optional()
                if not exists:
                    if transaction is not None:
                        raise ActivationRefused()
                    self._prepare.prepare(expected_generation=expected_generation, authenticated=authenticated)
                receipt = receipts.read(expected_generation=expected_generation)
                if (receipt.phase not in ("prepared", "activating", "joined")
                        or receipt.local_node_stable_id is not None or receipt.home_endpoint is not None):
                    raise ActivationRefused()
                if transaction is not None:
                    if not transaction.matches(receipt):
                        raise ActivationRefused()
                    coordinator, _, _ = self._compose(receipt)
                    # Ruling 17: use the actual protected binding, never a
                    # fabricated prepared receipt or the forward run entry.
                    coordinator._pending()
                    coordinator._fail(receipt)
                    raise ActivationRefused("failed_uncertain")
                if receipt.phase != "prepared":
                    raise ActivationRefused()
                coordinator, _, observer = self._compose(receipt)
                coordinator.run(receipt)
                return coordinator.publish_ready(expected_generation=expected_generation, observer=observer)
        except Exception:
            raise ActivationRefused() from None

    def recover(self, *, expected_generation: int, expected_operation_id: str,
                fresh_authenticated: bool) -> HomeActivationReceipt:
        try:
            if fresh_authenticated is not True or os.geteuid() != 0:
                raise ActivationRefused()
            with self._transactions.locked():
                receipt = self._prepare._receipts.read(expected_generation=expected_generation)
                if receipt.operation_id != expected_operation_id:
                    raise ActivationRefused()
                _, recovery, observer = self._compose(receipt)
                return recovery.recover(expected_generation=expected_generation,
                    expected_operation_id=expected_operation_id, fresh_authenticated=fresh_authenticated,
                    observation_owner=observer._owner)
        except Exception:
            raise ActivationRefused() from None


class HelperLifecycleBackend:
    """Existing bundled root child, selected only by the fixed helper.

    No paths, identities or effect flags come from a D-Bus caller. The helper
    sends its own authenticated UID and previously captured protected snapshot.
    Each child revalidates the snapshot; B6 also checks operation under its flock.
    """

    def __init__(self, io: ManagedInstaller) -> None:
        self.io = io
        self.store = HomeActivationStore(root=io.root, root_owner=io.root_owner)

    def capture(self, method: str, uid: int) -> str:
        if method in ('IssueInvite', 'CancelInvite', 'RecoverInvite'):
            from runtime.remote_access.helper_invite_backend import HelperInviteBackend
            return HelperInviteBackend(self.io).capture(uid)
        receipt = self.store.current()
        activation = None
        generation = None
        replacement = None
        configuration = None
        if receipt is not None:
            if receipt.release_uid != uid:
                raise ActivationRefused()
            activation = dict(generation=receipt.activation_generation, operation=receipt.operation_id,
                              home_id=receipt.home_id, control_plane_id=receipt.control_plane_id,
                              connector_id=receipt.connector_id)
            generation = receipt.activation_generation
        elif method == 'Enable':
            from runtime.remote_access.invite_issuer import HomeInviteConfigRecord
            with self.store._record(INVITE_CONFIG_PATH) as data:
                config = HomeInviteConfigRecord.parse(data)
                configuration = hashlib.sha256(data).hexdigest()
            if config.release_uid != uid:
                raise ActivationRefused()
            generation = config.activation_generation
        if method == 'ReplaceFailed':
            candidate = self.io.replacement_inventory()
            replacement = candidate.journal_generation or candidate.inventory_generation
            if candidate.installation.state != 'known-failed-or-partial' or replacement is None:
                raise ActivationRefused()
        if method == 'Disable' and receipt is None:
            raise ActivationRefused()
        return json.dumps(dict(uid=uid, activation=activation, generation=generation,
                               replacement=replacement, configuration=configuration), sort_keys=True, separators=(',', ':'))

    def dispatch(self, method: str, uid: int, step: str, expected: str | None, *,
                 revoke_succeeded: bool = False) -> dict:
        if method not in ('Enable', 'ReplaceFailed', 'Disable') or type(uid) is not int or uid < 1000:
            raise ActivationRefused()
        if type(revoke_succeeded) is not bool or (revoke_succeeded and (method, step) != ('Disable', 'cleanup')):
            raise ActivationRefused()
        if step == 'capture':
            if expected is not None:
                raise ActivationRefused()
            return {'snapshot': self.capture(method, uid)}
        if type(expected) is not str or self.capture(method, uid) != expected:
            raise ActivationRefused()
        snapshot = json.loads(expected)
        if method == 'Disable' and step == 'cleanup':
            receipt = self.store.current()
            if receipt is None:
                raise ActivationRefused()
            result = self.io.cleanup_headscale(expected_release_uid=uid,
                expected_home_id=snapshot['activation']['home_id'],
                expected_control_plane_id=snapshot['activation']['control_plane_id'],
                expected_connector_id=snapshot['activation']['connector_id'], expected_generation=snapshot['generation'],
                expected_operation_id=snapshot['activation']['operation'], revoke_succeeded=revoke_succeeded)
            if result.get('state') != 'removed':
                raise ActivationRefused()
            return {'outcome': 'off', 'residue': []}
        backend = HomeActivationBackend(io=self.io)
        with backend._transactions.locked():
            if self.capture(method, uid) != expected:
                raise ActivationRefused()
            if method == 'Enable' and step == 'enable':
                current = self.store.current()
                if current is not None and current.phase == 'ready':
                    # Already-ready is inspect-only, never replays activation.
                    coordinator, _, _ = backend._compose(current)
                    if coordinator._owner.verify('joined', current) is not True:
                        raise ActivationRefused()
                elif current is not None and current.phase == 'failed_uncertain':
                    transaction = backend._transactions.read_optional()
                    if (transaction is None or not transaction.matches(current)
                            or not (transaction.phase == 'failed_uncertain'
                                    or (transaction.phase == 'joined'
                                        and transaction.recovery_observation is not None))):
                        raise ActivationRefused()
                    current = backend.recover(expected_generation=snapshot['generation'],
                        expected_operation_id=snapshot['activation']['operation'], fresh_authenticated=True)
                else:
                    current = backend.enable(expected_generation=snapshot['generation'], authenticated=True)
                return {'outcome': current.phase, 'residue': []}
            if method == 'ReplaceFailed' and step == 'replace':
                self.io.begin_replacement(expected_release_uid=uid,
                                          expected_generation=snapshot['replacement'])
                return {'outcome': 'off', 'residue': []}
            if method == 'Disable' and step == 'stop':
                self.io.stop_and_confirm()
                from runtime.remote_access import headscale_service
                from runtime.remote_access.home_unit_gate import HomeUnitGate
                receipt = self.store.current()
                gate = HomeUnitGate(expected_release_uid=uid, expected_home_id=receipt.home_id,
                    expected_control_plane_id=receipt.control_plane_id, expected_connector_id=receipt.connector_id,
                    expected_generation=receipt.activation_generation, root=self.io.root, root_owner=self.io.root_owner)
                gate.inspect()
                account = self.io._headscale_receipt()
                self.io._headscale_cleanup_identity(account)
                if self.io._headscale_owner is None:
                    raise ActivationRefused()
                name = headscale_service.UNIT
                props = self.io.manager.show_properties(name, ('LoadState', 'FragmentPath', 'DropInPaths'))
                loaded = props.get('LoadState') == 'loaded'
                if (props.get('LoadState') not in ('loaded', 'not-found') or props.get('DropInPaths') != ''
                        or props.get('FragmentPath') != (str(self.io.root / 'etc/systemd/system' / name) if loaded else '')):
                    raise ActivationRefused()
                if loaded:
                    self.io.manager.stop(name, timeout=50.0)
                self.io._headscale_stopped()
                # B6 owns its receipted Headscale link. Disable the other exact
                # recognized remote units here, only after native stop proof.
                for name in (UNITS[2], UNITS[0], UNITS[1], UNITS[3]):
                    if self.io.inventory().state not in ('owned', 'owned-activated'):
                        raise ActivationRefused()
                    self.io._confirm_quiescent()
                    if self.io.manager.show_properties(name, ('LoadState',)).get('LoadState') != 'not-found':
                        self.io.manager.disable(name)
                self.io._confirm_quiescent()
                self.io._headscale_stopped()
                return {'outcome': 'in_progress', 'residue': []}
        raise ActivationRefused()


def helper_main() -> int:
    """Closed private input from the fixed helper; never an operator CLI."""
    try:
        if len(sys.argv) != 1 or os.geteuid() != 0:
            raise ActivationRefused()
        raw = sys.stdin.buffer.readline(4097)
        if len(raw) > 4096:
            raise ActivationRefused()
        request = json.loads(raw)
        if type(request) is not dict:
            raise ActivationRefused()
        fields = {'method', 'uid', 'step', 'expected', 'deadline'}
        if (request.get('method'), request.get('step')) == ('Disable', 'cleanup'):
            fields.add('revoke_succeeded')
        if set(request) != fields:
            raise ActivationRefused()
        deadline = request.pop('deadline')
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= time.monotonic():
            raise ActivationRefused()
        # Existing command runner; every root/systemd step gets the ORIGINAL remainder.
        from runtime.remote_access.service_manager import SystemdServiceManager, _default_run
        def run(argv: list[str], timeout: float = 10.0, **kwargs: object) -> tuple[int, str]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ActivationRefused()
            return _default_run(argv, timeout=min(timeout, remaining), **kwargs)
        manager = SystemdServiceManager(system=True, run=run)
        io = ManagedInstaller(layout='deb', manager=manager, run=run)
        if request['method'] in ('IssueInvite', 'CancelInvite', 'RecoverInvite'):
            from runtime.remote_access.helper_invite import confirm
            from runtime.remote_access.helper_invite_backend import HelperInviteBackend
            result = HelperInviteBackend(io).dispatch(**request, deadline=deadline,
                                                      before_commit=lambda: confirm(deadline))
        else:
            result = HelperLifecycleBackend(io).dispatch(**request)
        if time.monotonic() >= deadline:
            raise ActivationRefused()
        print(json.dumps(result, sort_keys=True, separators=(',', ':')))
        return 0
    except Exception:
        return 1  # No raw paths, keys, credentials or diagnostics leave this child.


if __name__ == '__main__':
    raise SystemExit(helper_main())
