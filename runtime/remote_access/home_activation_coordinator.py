"""Unmounted G2-C1 forward composition; stops at joined, never publishes ready.

Owners supply fixed service/admin/config operations and independent verification.
No CLI, helper, installer, supervisor or application calls this module.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from runtime.remote_access import headscale_service, home_admin
from runtime.remote_access.home_activation import (
    BINDING_KEYS, HomeActivationReceipt, HomeActivationStore,
)
from runtime.remote_access.home_activation_txn import (
    CONFIG_MEMBERS, EFFECTS, Effect, HomeActivationTransaction, HomeActivationTransactionStore,
)
from runtime.remote_access.invite_issuer import MintedKey


class ActivationRefused(ValueError):
    def __init__(self, reason: str = "activation_refused") -> None:
        super().__init__(reason if reason in ("activation_refused", "token_source_invalid",
                                             "failed_uncertain") else "activation_refused")


class ActivationOwner(Protocol):
    """Protected owner contract, deliberately without a production implementation.

    verify is read-only, fresh and complete: it checks custody, current generation/
    policy and binding for preflight; actual service/user/node/credential state for
    later effects. Mint verification uses fixed-user key/node listings, not a cached
    success reply. credential_staging verifies transient key custody/consumption;
    no secret is written in the journal. perform accepts only the fixed effect names:
    headscale_start_verify, db_users, credential_staging, home_config, managed_services.
    db_users composes RootHeadscaleAdminOperations' fixed-name create/list operations.
    home_config alone may publish the two fixed live configs, using A5 and validated
    receipt binding. It never edits policy or prepared members. managed_services owns
    enroll/start. Every possible lost create/enroll/start reply raises; never retry.
    No public invite, approval, endpoint observation or readiness operation is exposed.
    """

    def verify(self, effect: str, receipt: HomeActivationReceipt) -> bool: ...

    def perform(self, effect: str, receipt: HomeActivationReceipt,
                credential: MintedKey | None) -> None: ...


class HomeActivationCoordinator:
    def __init__(self, *, owner: ActivationOwner, root: Path = Path("/"),
                 root_owner: tuple[int, int] = (0, 0)) -> None:
        self._owner = owner
        self._receipts = HomeActivationStore(root=root, root_owner=root_owner)
        self._transactions = HomeActivationTransactionStore(root=root, root_owner=root_owner)
        self._io = self._receipts._io

    def __repr__(self) -> str:
        return "HomeActivationCoordinator(<redacted>)"

    def _source(self, receipt: HomeActivationReceipt) -> None:
        try:
            release = self._io._check_release_record()
            if release["uid"] != receipt.release_uid:
                raise ValueError
            source = self._io._input(Path(release["data_home"]) / "daemon.token", token=True,
                                     source_uid=receipt.release_uid)
            # Bind rotation to the existing provisioned copy; never persist token
            # bytes or a new token digest in either B1/B4 record.
            with self._io._directory("etc/happyranch") as parent:
                staged = self._io._read_file(parent, "daemon.token", mode=0o600)
            if source != staged:
                raise ValueError
        except Exception:
            raise ActivationRefused("token_source_invalid") from None

    def _digests(self, paths: tuple[str, ...]) -> dict[str, str]:
        return {path: hashlib.sha256(self._io._input(self._io.root / path.lstrip("/"))).hexdigest()
                for path in paths}

    def _members(self, receipt: HomeActivationReceipt, transaction: HomeActivationTransaction | None) -> None:
        if self._digests(tuple(receipt.member_digests)) != receipt.member_digests:
            raise ActivationRefused()
        if transaction is not None:
            for effect in transaction.effects:
                if effect.member_digests and self._digests(tuple(effect.member_digests)) != effect.member_digests:
                    raise ActivationRefused()

    def _verify(self, effect: str, receipt: HomeActivationReceipt) -> None:
        if self._owner.verify(effect, receipt) is not True:
            raise ActivationRefused()

    def _publish_transaction(self, value: HomeActivationTransaction, receipt: HomeActivationReceipt,
                             previous: HomeActivationTransaction | None) -> None:
        self._source(receipt)
        self._transactions.publish(value, expected=previous)

    def _publish_receipt(self, value: HomeActivationReceipt, previous: HomeActivationReceipt) -> None:
        self._source(previous)
        self._receipts.publish(value, expected=previous)

    def _fail(self, prepared: HomeActivationReceipt) -> None:
        """Ruling 6: nonsecret terminal markers are exempt from source gating."""
        transaction = self._transactions.read_optional()
        if transaction is None:
            return
        receipt = self._receipts.read(expected_generation=prepared.activation_generation)
        if not transaction.matches(receipt) or not transaction.matches(prepared):
            raise ActivationRefused()
        if transaction.phase != "failed_uncertain":
            self._transactions.publish(replace(transaction, phase="failed_uncertain"), expected=transaction)
        if receipt.phase != "failed_uncertain":
            # G2-C1 cannot have observations or any live/token-derived fields.
            if receipt.local_node_stable_id is not None or receipt.home_endpoint is not None:
                raise ActivationRefused()
            self._receipts.publish(replace(receipt, phase="failed_uncertain"), expected=receipt)

    def run(self, prepared: HomeActivationReceipt) -> HomeActivationReceipt:
        """Externally selected prepared binding; never generate/reuse another ID.

        A fresh instance always reopens the literal records and verifies completed
        effects. An interrupted non-idempotent intent or lost private mint result
        can only terminate; G2-C2 owns explicit reconciliation/new generations.
        """
        if type(prepared) is not HomeActivationReceipt or prepared.phase != "prepared":
            raise ActivationRefused()
        try:
            with self._transactions.locked():
                try:
                    return self._run(prepared)
                except Exception as error:
                    try:
                        self._fail(prepared)
                    except Exception:
                        raise ActivationRefused() from None
                    reason = str(error) if type(error) is ActivationRefused else "activation_refused"
                    raise ActivationRefused(reason) from None
        except ActivationRefused:
            raise
        except Exception:
            raise ActivationRefused() from None

    def _run(self, prepared: HomeActivationReceipt) -> HomeActivationReceipt:
        receipt = self._receipts.read(expected_generation=prepared.activation_generation)
        if (any(getattr(receipt, key) != getattr(prepared, key) for key in (*BINDING_KEYS, "operation_id"))
                or receipt.member_digests != prepared.member_digests
                or receipt.phase not in ("prepared", "activating", "joined")):
            raise ActivationRefused()
        transaction = self._transactions.read_optional()
        if transaction is not None and (not transaction.matches(receipt) or transaction.phase == "failed_uncertain"):
            raise ActivationRefused("failed_uncertain")
        if transaction is not None and (
                (receipt.phase == "prepared" and transaction.effects != (Effect("preflight", "intent", {}),))
                or (receipt.phase == "joined" and transaction.phase != "joined")):
            raise ActivationRefused()
        self._source(receipt)
        self._members(receipt, transaction)
        self._verify("preflight", receipt)
        if transaction is None:
            if receipt.phase != "prepared":
                raise ActivationRefused()
            transaction = HomeActivationTransaction.begin(receipt)
            self._publish_transaction(transaction, receipt, None)
        else:
            # Never replay even an apparently successful start/mint/enroll intent.
            if (transaction.effects[-1].state == "intent"
                    and transaction.effects[-1].name not in ("preflight", "joined")):
                raise ActivationRefused("failed_uncertain")
            names = {effect.name for effect in transaction.effects if effect.state == "done"}
            if "local_node_mint" in names and "credential_staging" not in names:
                raise ActivationRefused("failed_uncertain")
            for effect in transaction.effects:
                if effect.state == "done":
                    self._source(receipt)
                    self._verify(effect.name, receipt)
        if receipt.phase == "prepared":
            updated = replace(receipt, phase="activating")
            self._publish_receipt(updated, receipt)
            receipt = updated
        credential: MintedKey | None = None
        for index, name in enumerate(EFFECTS):
            if index < len(transaction.effects) and transaction.effects[index].state == "done":
                continue
            if index == len(transaction.effects):
                updated_txn = replace(transaction, effects=(*transaction.effects, Effect(name, "intent", {})))
                self._publish_transaction(updated_txn, receipt, transaction)
                transaction = updated_txn
            self._source(receipt)
            self._members(receipt, transaction)
            self._verify("preflight", receipt)
            if name == "preflight":
                headscale_service.preflight_ports(receipt.dial_ip)
            elif name == "local_node_mint":
                credential = home_admin.mint_local_node(receipt.operation_id,
                                                        datetime.now(timezone.utc) + timedelta(seconds=120))
                if type(credential) is not MintedKey:
                    raise ActivationRefused("failed_uncertain")
            elif name not in ("preflight", "joined"):
                if name == "credential_staging" and credential is None:
                    raise ActivationRefused("failed_uncertain")
                self._owner.perform(name, receipt, credential if name == "credential_staging" else None)
            self._source(receipt)
            self._verify(name, receipt)
            digests = self._digests(CONFIG_MEMBERS) if name == "home_config" else {}
            updated_txn = replace(transaction, effects=(*transaction.effects[:-1], Effect(name, "done", digests)))
            self._publish_transaction(updated_txn, receipt, transaction)
            transaction = updated_txn
            if name == "credential_staging":
                credential = None
        if transaction.phase != "joined":
            updated_txn = replace(transaction, phase="joined")
            self._publish_transaction(updated_txn, receipt, transaction)
            transaction = updated_txn
        self._members(receipt, transaction)
        if receipt.phase != "joined":
            updated = replace(receipt, phase="joined")
            self._publish_receipt(updated, receipt)
            receipt = updated
        return receipt
