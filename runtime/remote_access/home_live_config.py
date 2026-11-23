"""Unmounted B4 live-config owner. No installer admission or recovery effects."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, replace

from runtime.remote_access.home_activation import (
    HomeActivationReceipt, HomeActivationStore, _signature,
)
from runtime.remote_access.home_activation_coordinator import ActivationRefused
from runtime.remote_access.legacy_home_readback import HomeTokenSourceVerifier, _addition, render_home_live_configs
from runtime.remote_access.home_activation_txn import (
    CONFIG_MEMBERS, HomeActivationTransaction, HomeActivationTransactionStore,
)
from runtime.remote_access.installer import ManagedInstaller
from runtime.remote_access.invite_issuer import MintedKey
from runtime.remote_access.release_verifier import validate_record

_CONNECTOR, _SIDECAR = CONFIG_MEMBERS


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True, repr=False)
class _Input:
    data: bytes
    signature: tuple[int, ...]
    digest: str


class HomeLiveConfigOwner:
    """Required config_owner for HomeActivationOwner, under the coordinator lock.

    Prepared inputs are read once, then retained with custody signatures. Each
    publication validates source and original inputs again; a partial pair is
    never resumed. Current txn verification uses done digests. Post-B7 prepared
    admission proves staged provenance under an earlier render, or the retained
    post-Disable insertion when the older txn never reached home_config.
    """

    def __init__(self, *, io: ManagedInstaller, expected_generation: int,
                 expected_operation_id: str) -> None:
        if (type(expected_generation) is not int or expected_generation < 0
                or type(expected_operation_id) is not str
                or re.fullmatch("[0-9a-f]{32}", expected_operation_id) is None):
            raise ActivationRefused()
        self._io = io
        self._generation, self._operation = expected_generation, expected_operation_id
        self._receipts = HomeActivationStore(root=io.root, root_owner=io.root_owner)
        self._transactions = HomeActivationTransactionStore(root=io.root, root_owner=io.root_owner)
        self._source = HomeTokenSourceVerifier(io)
        self._inputs: dict[str, _Input] = {}
        self._live: dict[str, bytes] | None = None
        self._attempted = False

    def __repr__(self) -> str:
        return "HomeLiveConfigOwner(<redacted>)"

    def _context(self, receipt: HomeActivationReceipt) -> HomeActivationTransaction | None:
        if (type(receipt) is not HomeActivationReceipt
                or receipt.activation_generation != self._generation
                or receipt.operation_id != self._operation
                or self._receipts.read(expected_generation=self._generation) != receipt):
            raise ActivationRefused()
        txn = self._transactions.read_optional()
        if txn is not None and not txn.matches(receipt):
            if not (receipt.phase == "prepared" and txn.phase == "failed_uncertain"
                    and txn.activation_generation < self._generation):
                raise ActivationRefused()
        return txn

    def _read(self, parent: int, name: str, *, owner: tuple[int, int], mode: int) -> _Input:
        self._io._headscale_no_pending(parent, name)
        before = _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
        data = self._io._read_file(parent, name, owner=owner, mode=mode)
        if _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != before:
            raise ActivationRefused()
        return _Input(data, before, _digest(data))

    def _prepare(self, receipt: HomeActivationReceipt) -> dict[str, bytes]:
        if not self._inputs:
            txn = self._transactions.read_optional()
            rollover = (receipt.phase == "prepared" and txn is not None
                        and txn.phase == "failed_uncertain"
                        and txn.activation_generation < receipt.activation_generation)
            post_disable = receipt.phase == "prepared" and receipt.activation_generation == 0 and txn is None
            if rollover and self._context(receipt) != txn:
                raise ActivationRefused()
            old_effect = next((e for e in txn.effects if e.name == "home_config"), None) if rollover else None
            if old_effect is not None and old_effect.state != "done":
                raise ActivationRefused()
            service = self._io._account(create=False)
            if service is None:
                raise ActivationRefused()
            with self._io._publication_parent() as (parent, check):
                record = self._read(parent, "provision.json", owner=self._io.root_owner, mode=0o600)
                raw = self._io._json(record.data)
                if "version" not in raw:
                    raise ActivationRefused()  # Ruling 13a: no legacy provenance.
                self._io._validate_install_record("provision.json", record.data)
                inputs = {"provision.json": record}
                for name in ("connector.json", "sidecar.json", "release-daemon.json"):
                    owner, mode = ((self._io.root_owner, 0o644) if name == "release-daemon.json"
                                   else ((self._io.root_owner[0], service[1]), 0o640))
                    item = self._read(parent, name, owner=owner, mode=mode)
                    inputs[name] = item
                if inputs["connector.json"].digest != raw["digests"]["connector.json"]:
                    raise ActivationRefused()
                sidecar = inputs["sidecar.json"]
                if sidecar.digest != raw["digests"]["sidecar.json"]:
                    if post_disable:
                        # PD3+PD4 use the existing exact-tail/staged-byte proof.
                        # Old transport authority is discarded, never adopted;
                        # the normal home_config effect renders from this receipt.
                        proven = self._io._home_unit_provisioning(parent, raw, reuse=True)["sidecar.json"]
                        inputs["sidecar.json"] = _Input(proven, sidecar.signature, _digest(proven))
                    elif not rollover:
                        raise ActivationRefused()
                    else:
                        g = self._io._json(sidecar.data).get("home_binding", {}).get("activation_generation")
                        staged = None
                        # L1(b) keeps its exact earlier-render and old-done proof.
                        if type(g) is int and 0 <= g < receipt.activation_generation:
                            end = sidecar.data.rindex(b"}")
                            addition = _addition(replace(receipt, activation_generation=g))
                            if sidecar.data[end-len(addition):end] == addition:
                                candidate = sidecar.data[:end-len(addition)] + sidecar.data[end:]
                                if (_digest(candidate) == raw["digests"]["sidecar.json"]
                                        and (old_effect is None or (
                                            g == txn.activation_generation and old_effect.member_digests == {
                                                _CONNECTOR: inputs["connector.json"].digest, _SIDECAR: sidecar.digest}))):
                                    staged = candidate
                        if staged is None:
                            if old_effect is not None:
                                raise ActivationRefused()
                            # L1(c): no older home_config record. Reuse P3's PD4
                            # proof; retired transport fields/g are not authority
                            # for this receipt. Admission remains read-only.
                            staged = self._io._home_unit_provisioning(parent, raw, reuse=True)["sidecar.json"]
                        inputs["sidecar.json"] = _Input(staged, sidecar.signature, _digest(staged))
                if inputs["release-daemon.json"].digest != raw["digests"]["release-daemon.json"]:
                    raise ActivationRefused()
                release = self._io._json(inputs["release-daemon.json"].data)
                validate_record(release)
                if release["uid"] != receipt.release_uid:
                    raise ActivationRefused()
                check()
                self._inputs = inputs
        self._stable()
        # Re-hash exact captured inputs immediately before the pure render.
        return self._render(receipt)

    def _render(self, receipt: HomeActivationReceipt) -> dict[str, bytes]:
        for item in self._inputs.values():
            if _digest(item.data) != item.digest:
                raise ActivationRefused()
        return render_home_live_configs(self._inputs["connector.json"].data,
                                        self._inputs["sidecar.json"].data, receipt)

    def _stable(self) -> None:
        with self._io._publication_parent() as (parent, check):
            for name, item in self._inputs.items():
                if _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != item.signature:
                    raise ActivationRefused()
            check()

    def _verify_live(self, receipt: HomeActivationReceipt,
                     txn: HomeActivationTransaction) -> None:
        effect = next((e for e in txn.effects if e.name == "home_config"), None)
        if effect is None or not txn.matches(receipt):
            raise ActivationRefused()
        with self._io._publication_parent() as (parent, check):
            service = self._io._account(create=False)
            if service is None:
                raise ActivationRefused()
            observed = {path: self._read(parent, path.rsplit("/", 1)[1],
                        owner=(self._io.root_owner[0], service[1]), mode=0o640)
                        for path in CONFIG_MEMBERS}
            live = {path: item.data for path, item in observed.items()}
            if effect.state == "done":
                if {path: _digest(data) for path, data in live.items()} != effect.member_digests:
                    raise ActivationRefused()
            elif (txn.phase != "activating" or self._live is None or live != self._live
                  or txn.effects[-1] != effect):
                raise ActivationRefused()
            if self._inputs:
                expected = self._render(receipt)
            else:
                # Exact inverse of our insertion, ONLY after txn digest verification.
                sidecar = live[_SIDECAR]
                end, addition = sidecar.rindex(b"}"), _addition(receipt)
                if sidecar[end-len(addition):end] != addition:
                    raise ActivationRefused()
                prepared = sidecar[:end-len(addition)] + sidecar[end:]
                expected = render_home_live_configs(live[_CONNECTOR], prepared, receipt)
            if live != expected or self._context(receipt) != txn:
                raise ActivationRefused()
            for path, item in observed.items():
                name = path.rsplit("/", 1)[1]
                self._io._headscale_no_pending(parent, name)
                if _signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != item.signature:
                    raise ActivationRefused()
            check()

    def verify(self, effect: str, receipt: HomeActivationReceipt) -> bool:
        try:
            if effect not in ("preflight", "home_config"):
                return False
            txn = self._context(receipt)
            self._source.require(receipt)
            config_effect = next((e for e in txn.effects if e.name == "home_config"), None) if txn else None
            rollover = (receipt.phase == "prepared" and txn is not None
                        and txn.phase == "failed_uncertain"
                        and txn.activation_generation < receipt.activation_generation)
            if config_effect and not rollover and (config_effect.state == "done" or self._live is not None):
                self._verify_live(receipt, txn)
            elif effect == "preflight" and not self._attempted and receipt.phase in ("prepared", "activating"):
                self._prepare(receipt)
                if self._context(receipt) != txn:
                    raise ActivationRefused()
            else:
                return False
            return True
        except Exception:
            return False

    def perform(self, effect: str, receipt: HomeActivationReceipt,
                credential: MintedKey | None) -> None:
        try:
            if effect != "home_config" or credential is not None or self._attempted:
                raise ActivationRefused()
            txn = self._context(receipt)
            if (txn is None or not txn.matches(receipt) or txn.phase != "activating"
                    or receipt.phase != "activating" or txn.effects[-1].name != "home_config"
                    or txn.effects[-1].state != "intent"):
                raise ActivationRefused()
            self._source.require(receipt)
            live = self._prepare(receipt)
            self._attempted = True
            # Both pending names must be absent before either file is touched.
            with self._io._publication_parent() as (parent, check):
                for path in CONFIG_MEMBERS:
                    self._io._headscale_no_pending(parent, path.rsplit("/", 1)[1])
                check()
            for path, data in live.items():
                self._publish(path.rsplit("/", 1)[1], data, receipt, txn)
            self._live = live
        except Exception:
            raise ActivationRefused() from None

    def _publish(self, name: str, data: bytes, receipt: HomeActivationReceipt,
                 txn: HomeActivationTransaction) -> None:
        self._stable()
        self._source.require(receipt)
        if self._context(receipt) != txn:
            raise ActivationRefused()
        service = self._io._account(create=False)
        if service is None:
            raise ActivationRefused()
        owner = (self._io.root_owner[0], service[1])
        with self._io._publication_parent() as (parent, check):
            self._io._headscale_no_pending(parent, name)
            pending = "." + name + ".pending"
            descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                                 | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=parent)
            try:
                os.fchown(descriptor, *owner)
                os.fchmod(descriptor, 0o640)
                with os.fdopen(descriptor, "wb", closefd=False) as stream:
                    if stream.write(data) != len(data):
                        raise ActivationRefused()
                    stream.flush()
                    os.fsync(descriptor)
                created = _signature(os.fstat(descriptor))
                if (self._io._read_file(parent, pending, owner=owner, mode=0o640) != data
                        or _signature(os.stat(pending, dir_fd=parent, follow_symlinks=False)) != created):
                    raise ActivationRefused()
                self._source.require(receipt)
                if self._context(receipt) != txn:
                    raise ActivationRefused()
                self._stable()
                check()
                if (_signature(os.stat(pending, dir_fd=parent, follow_symlinks=False)) != created
                        or _signature(os.fstat(descriptor)) != created):
                    raise ActivationRefused()
                self._io._rename_provision_entry(parent, pending, name, updating=True)
                os.fsync(parent)
                check()
                after = _signature(os.stat(name, dir_fd=parent, follow_symlinks=False))
                if after != _signature(os.fstat(descriptor)):
                    raise ActivationRefused()
                # Our own rename changes the signature, not the captured input.
                item = self._inputs[name]
                self._inputs[name] = _Input(item.data, after, item.digest)
            finally:
                os.close(descriptor)  # Preserve pending residue on every refusal.
