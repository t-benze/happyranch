"""Closed B4 journal and A5 store. No entry point or recovery authority."""
from __future__ import annotations

from runtime.remote_access.home_activation import (
    ACTIVATION_PATH, HomeActivationError, HomeActivationStore,
)
from runtime.remote_access.legacy_home_readback import LegacyHomeTransactions, TRANSACTION_PATH
from runtime.remote_access.legacy_home_receipt import (
    CONFIG_MEMBERS, EFFECTS, Effect, HomeActivationTransaction,
)


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


class HomeActivationTransactionStore(LegacyHomeTransactions, HomeActivationStore):
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
