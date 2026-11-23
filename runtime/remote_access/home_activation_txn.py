"""Closed B4 journal and A5 store. No entry point or recovery authority."""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from runtime.remote_access.home_activation import (
    ACTIVATION_PATH, HomeActivationError, HomeActivationStore,
)
from runtime.remote_access.headscale_service import HeadscaleServiceError
from runtime.remote_access.installer import InstallerError
from runtime.remote_access.installer_lock import installer_state_lock
from runtime.remote_access.legacy_home_receipt import (
    CONFIG_MEMBERS, EFFECTS, Effect, HomeActivationTransaction,
)

TRANSACTION_PATH = "/var/lib/happyranch-remote-access/home-activation-txn.json"


def validate_transaction_transition(previous: HomeActivationTransaction,
                                    updated: HomeActivationTransaction) -> None:
    if (previous.binding() != updated.binding() or previous.phase == "failed_uncertain"
            or previous.recovery_observation != updated.recovery_observation):
        raise HomeActivationError("transition")
    if updated.phase == "failed_uncertain" and updated.effects == previous.effects:
        return
    if previous.phase != "activating":
        raise HomeActivationError("transition")
    before, after = previous.effects, updated.effects
    append = (before[-1].state == "done" and len(after) == len(before) + 1
              and after[:-1] == before and after[-1].state == "intent")
    finish = (before[-1].state == "intent" and len(after) == len(before)
              and after[:-1] == before[:-1] and after[-1].name == before[-1].name
              and after[-1].state == "done")
    close = (before == after and len(before) == len(EFFECTS) and before[-1].state == "done"
             and updated.phase == "joined")
    if not (close or (updated.phase == "activating" and (append or finish))):
        raise HomeActivationError("transition")


class HomeActivationTransactionStore(HomeActivationStore):
    @contextmanager
    def _parent(self, path: str) -> Iterator[tuple[int, Callable[[], None]]]:
        if path != TRANSACTION_PATH:
            raise HomeActivationError("state")
        with super()._parent(ACTIVATION_PATH) as parent:
            yield parent

    @contextmanager
    def locked(self) -> Iterator[int]:
        """One nonblocking directory lock spans both records and every effect."""
        try:
            with installer_state_lock(self._io) as parent:
                yield parent
        except (OSError, InstallerError, HeadscaleServiceError):
            raise HomeActivationError("state") from None

    def read_optional(self) -> HomeActivationTransaction | None:
        try:
            with self._parent(TRANSACTION_PATH) as (parent, revalidate):
                self._io._headscale_no_pending(parent, Path(TRANSACTION_PATH).name)
                try:
                    os.stat(Path(TRANSACTION_PATH).name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    revalidate()
                    return None
            with self._record(TRANSACTION_PATH) as data:
                return HomeActivationTransaction.parse(data)
        except (OSError, InstallerError, HeadscaleServiceError):
            raise HomeActivationError("state") from None

    def publish(self, transaction: HomeActivationTransaction, *,
                expected: HomeActivationTransaction | None = None) -> None:
        if type(transaction) is not HomeActivationTransaction:
            raise HomeActivationError()
        if expected is None:
            if transaction.phase != "activating" or transaction.effects != (Effect("preflight", "intent", {}),):
                raise HomeActivationError("transition")
        else:
            validate_transaction_transition(expected, transaction)
        self._publish_record(TRANSACTION_PATH, transaction.to_bytes(), expected=expected,
                             parse=HomeActivationTransaction.parse)
