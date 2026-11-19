"""Unmounted Ruling 16 first-activation preparation, before immutable B1 digests."""
from __future__ import annotations

import hashlib
import os
import secrets
import ssl
from dataclasses import replace
from pathlib import Path

from runtime.remote_access import headscale_service
from runtime.remote_access.home_activation import (
    ACTIVATION_PATH, BINDING_KEYS, INVITE_CONFIG_PATH, MEMBER_PATHS, OWNED_LINK,
    HomeActivationReceipt, HomeActivationStore, _generation,
)
from runtime.remote_access.home_activation_coordinator import ActivationRefused
from runtime.remote_access.home_activation_owner import HomeTokenSourceVerifier
from runtime.remote_access.home_activation_txn import TRANSACTION_PATH, HomeActivationTransactionStore
from runtime.remote_access.home_live_config import HomeLiveConfigOwner
from runtime.remote_access.home_unit_publication import publish_home_unit
from runtime.remote_access.installer import ManagedInstaller
from runtime.remote_access.invite_issuer import HomeInviteConfigRecord
from runtime.remote_access.systemd_unit import render_headscale_unit


class HomeActivationPrepare:
    def __init__(self, *, io: ManagedInstaller) -> None:
        self._io = io
        self._receipts = HomeActivationStore(root=io.root, root_owner=io.root_owner)
        self._transactions = HomeActivationTransactionStore(root=io.root, root_owner=io.root_owner)
        self._source = HomeTokenSourceVerifier(io)

    def _absent(self) -> None:
        with self._receipts._parent(ACTIVATION_PATH) as (parent, check):
            for path in (ACTIVATION_PATH, TRANSACTION_PATH):
                self._io._refuse_provision_entry(parent, Path(path).name)
            check()
        relative = Path(OWNED_LINK.lstrip("/"))
        try:
            with self._io._publication_parent(str(relative.parent)) as (parent, check):
                self._io._refuse_provision_entry(parent, relative.name)
                check()
        except FileNotFoundError:
            # Only an absent wants directory is absence, not a disappearing leaf.
            with self._io._publication_parent(str(relative.parent.parent)) as (parent, check):
                self._io._refuse_provision_entry(parent, relative.parent.name)
                check()

    def _require(self, receipt: HomeActivationReceipt) -> None:
        if os.geteuid() != 0:
            raise ActivationRefused()
        self._absent()
        self._source.require(receipt)
        self._io._headscale_stopped()

    def _members(self, receipt: HomeActivationReceipt) -> dict[str, str]:
        publication = self._io._headscale_publication()
        status = self._io._headscale_materializer("status")
        if (publication is None or "directories" not in publication
                or publication["dial_ip"] != receipt.dial_ip
                or status["ca_fingerprint"] != receipt.ca_der_sha256
                or status["renewal_due"] or not status["leaf_not_after"]):
            raise ActivationRefused()
        members = self._io._headscale_members()
        digests = {}
        for path in MEMBER_PATHS:
            relative = Path(path.lstrip("/"))
            with self._io._publication_parent(str(relative.parent)) as (parent, check):
                self._io._headscale_no_pending(parent, relative.name)
                if path == MEMBER_PATHS[0]:
                    data = self._io._read_unit(parent, relative.name)
                    if data != render_headscale_unit(home_transport=True).encode():
                        raise ActivationRefused()
                else:
                    owner, mode = members.get(path, (self._io.root_owner, 0o600))
                    data = self._io._read_file(parent, relative.name, owner=owner, mode=mode)
                if path == INVITE_CONFIG_PATH:
                    config = HomeInviteConfigRecord.parse(data)
                    if any(getattr(config, key) != getattr(receipt, key) for key in BINDING_KEYS):
                        raise ActivationRefused()
                if path.endswith("control-ca.pem"):
                    der = ssl.PEM_cert_to_DER_cert(data.decode("ascii"))
                    if hashlib.sha256(der).hexdigest() != receipt.ca_der_sha256:
                        raise ActivationRefused()
                if path.endswith("policy.hujson") and data != headscale_service.render_policy():
                    raise ActivationRefused()
                digests[path] = hashlib.sha256(data).hexdigest()
                check()
        return digests

    def prepare(self, *, expected_generation: int, authenticated: bool) -> tuple[int, str]:
        """Authentication and expected G are supplied by a future trusted caller.

        First activation only. No record/txn/pending entry may be adopted. A crash
        after unit rename leaves home bytes without a receipt: B5 reports drift.
        Only this explicit retry (exact home) or B6 may deal with that residual.
        """
        try:
            _generation(expected_generation)
            if authenticated is not True or os.geteuid() != 0:
                raise ActivationRefused()
            with self._transactions.locked():
                self._absent()
                with self._receipts._record(INVITE_CONFIG_PATH) as data:
                    config = HomeInviteConfigRecord.parse(data)
                if config.activation_generation != expected_generation:
                    raise ActivationRefused()
                receipt = HomeActivationReceipt(
                    **{key: getattr(config, key) for key in BINDING_KEYS},
                    operation_id="0" * 32, phase="prepared",
                    member_digests={path: "0" * 64 for path in MEMBER_PATHS},
                    local_node_stable_id=None, home_endpoint=None)
                self._io._service_owner = self._io._account(create=False)
                self._io._headscale_owner = self._io._headscale_shape()
                if self._io._service_owner is None or self._io._headscale_owner is None:
                    raise ActivationRefused()
                self._io._headscale_preparing = True
                self._require(receipt)
                publish_home_unit(self._io, "happyranch-headscale.service",
                    legacy=render_headscale_unit().encode(),
                    home=render_headscale_unit(home_transport=True).encode(),
                    require=lambda: self._require(receipt))
                self._require(receipt)
                self._io.manager.daemon_reload()
                digests = self._members(receipt)
                receipt = replace(receipt, member_digests=digests, operation_id=secrets.token_hex(16))
                # Reuse E1b's protected provision/RELEASE binding without writing
                # either config or the sidecar unit during Prepare.
                configs = HomeLiveConfigOwner(io=self._io, expected_generation=expected_generation,
                                             expected_operation_id=receipt.operation_id)
                configs._prepare(receipt)
                self._require(receipt)
                if self._members(receipt) != digests:
                    raise ActivationRefused()
                configs._stable()
                self._require(receipt)
                self._receipts.publish(receipt)
                return expected_generation, receipt.operation_id
        except Exception:
            raise ActivationRefused() from None
