"""Unmounted B4 forward composition and explicit B2 joined-to-ready publication.

Owners supply fixed service/admin/config operations and independent verification.
No CLI, helper, installer, supervisor or application calls this module.
"""
from __future__ import annotations

import hashlib
import os
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
from runtime.remote_access.home_observer import HomeObserver


class ActivationRefused(ValueError):
    def __init__(self, reason: str = "activation_refused") -> None:
        super().__init__(reason if reason in ("activation_refused", "token_source_invalid",
                                             "failed_uncertain", "pending_residue") else "activation_refused")


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

    def _pending(self) -> None:
        with self._receipts._parent("/var/lib/happyranch-remote-access/home-activation.json") as (parent, check):
            for name in ("home-activation.json", "home-activation-txn.json"):
                try:
                    os.stat("." + name + ".pending", dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                raise ActivationRefused("pending_residue")
            check()

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

    def publish_ready(self, *, expected_generation: int, observer: HomeObserver) -> HomeActivationReceipt:
        """Ruling 8: read-only observation then one A5 receipt write; no failure markers."""
        try:
            with self._transactions.locked():
                receipt = self._receipts.read(expected_generation=expected_generation)
                transaction = self._transactions.read_optional()
                if (receipt.phase != "joined" or receipt.local_node_stable_id is not None
                        or receipt.home_endpoint is not None or transaction is None
                        or transaction.phase != "joined" or not transaction.matches(receipt)
                        or tuple(e.name for e in transaction.effects) != EFFECTS
                        or any(e.state != "done" for e in transaction.effects)):
                    raise ActivationRefused()
                self._members(receipt, transaction)
                observed = observer.observe(receipt, expected_generation=expected_generation)
                # Recheck the original pair and its members before source-gated publication.
                if (self._transactions.read_optional() != transaction
                        or self._receipts.read(expected_generation=expected_generation) != receipt):
                    raise ActivationRefused()
                self._members(receipt, transaction)
                ready = replace(receipt, phase="ready", local_node_stable_id=observed.stable_id,
                                home_endpoint=observed.home_endpoint)
                self._publish_receipt(ready, receipt)
                return ready
        except Exception:
            raise ActivationRefused() from None

    def verify_ready(self, *, expected_generation: int, observer: HomeObserver) -> HomeActivationReceipt:
        """Fresh read-only restart admission evidence; never mint/rekey or publish."""
        try:
            with self._transactions.locked():
                receipt = self._receipts.read(expected_generation=expected_generation)
                if receipt.phase != "ready":
                    raise ActivationRefused()
                transaction = self._transactions.read_optional()
                if transaction is None or transaction.phase != "joined" or not transaction.matches(receipt):
                    raise ActivationRefused()
                self._members(receipt, transaction)
                observed = observer.observe(receipt, expected_generation=expected_generation)
                if (observed.stable_id != receipt.local_node_stable_id
                        or observed.home_endpoint != receipt.home_endpoint
                        or self._transactions.read_optional() != transaction
                        or self._receipts.read(expected_generation=expected_generation) != receipt):
                    raise ActivationRefused()
                self._members(receipt, transaction)
                return receipt
        except Exception:
            raise ActivationRefused() from None

    def run(self, prepared: HomeActivationReceipt) -> HomeActivationReceipt:
        """Externally selected prepared binding; never generate/reuse another ID.

        Ruling 7: an existing transaction can only terminate, even at a verified
        completed prefix or joined checkpoint. Failed terminal publication cannot
        be distinguished from a crash. G2-C2 owns explicit B7 recovery.
        """
        if type(prepared) is not HomeActivationReceipt or prepared.phase != "prepared":
            raise ActivationRefused()
        try:
            with self._transactions.locked():
                self._pending()
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
        older_failed = (transaction is not None and receipt.phase == "prepared"
                        and transaction.phase == "failed_uncertain"
                        and transaction.activation_generation < receipt.activation_generation)
        if transaction is not None and not older_failed:
            # Every valid txn contains the first durable intent. A new run has no
            # authority to resume it; run's failure path publishes txn then receipt.
            raise ActivationRefused("failed_uncertain")
        self._source(receipt)
        self._members(receipt, None if older_failed else transaction)
        self._verify("preflight", receipt)
        if receipt.phase != "prepared":
            raise ActivationRefused()
        previous = transaction
        transaction = HomeActivationTransaction.begin(receipt)
        if older_failed:
            # B7 rollover replaces only the older terminal journal at first intent.
            self._source(receipt)
            self._transactions._publish_record(
                "/var/lib/happyranch-remote-access/home-activation-txn.json", transaction.to_bytes(),
                expected=previous, parse=HomeActivationTransaction.parse)
        else:
            self._publish_transaction(transaction, receipt, None)
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
