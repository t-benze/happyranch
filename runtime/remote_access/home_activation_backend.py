"""Uncalled production composition. Authentication is a trusted caller precondition.

No CLI/helper/D-Bus/app/web entry mounts this class. Recovery executes only the
existing Ruling 12/12a verify/publication path, never activation effects.
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Iterator

from runtime.remote_access.home_activation import ACTIVATION_PATH, HomeActivationReceipt, _generation
from runtime.remote_access.home_activation_coordinator import ActivationRefused, HomeActivationCoordinator
from runtime.remote_access.home_activation_owner import HomeActivationOwner
from runtime.remote_access.home_activation_prepare import HomeActivationPrepare
from runtime.remote_access.home_activation_recovery import HomeActivationRecovery
from runtime.remote_access.home_activation_txn import HomeActivationTransactionStore
from runtime.remote_access.home_live_config import HomeLiveConfigOwner
from runtime.remote_access.home_observer import HomeObserver
from runtime.remote_access.installer import ManagedInstaller


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
                if not exists:
                    self._prepare.prepare(expected_generation=expected_generation, authenticated=authenticated)
                # An already-prepared binding (including B7 rollover) goes only
                # through unchanged coordinator admission, never through Prepare.
                prepared = receipts.read(expected_generation=expected_generation)
                coordinator, _, observer = self._compose(prepared)
                coordinator.run(prepared)
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
