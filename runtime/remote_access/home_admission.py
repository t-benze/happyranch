"""E2 ordinary lifecycle and foreground admission; no activation or unit writer."""
from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
from dataclasses import dataclass

from runtime.remote_access import invite_v2
from runtime.remote_access.home_activation import (
    ACTIVATION_PATH, IDENTITY_KEYS, CONTROL_URL, INVITE_CONFIG_PATH, MEMBER_PATHS,
    HomeActivationReceipt, HomeActivationStore,
)
from runtime.remote_access.home_activation_owner import HomeTokenSourceVerifier
from runtime.remote_access.home_activation_txn import CONFIG_MEMBERS, HomeActivationTransactionStore
from runtime.remote_access.home_live_config import _addition, render_home_live_configs
from runtime.remote_access.home_unit_gate import HomeUnitGate
from runtime.remote_access.invite_issuer import HomeInviteConfigRecord
from runtime.remote_access.installer import InstallerError, ManagedInstaller, UNITS, managed_units
from runtime.remote_access.systemd_unit import (
    SidecarUnitSpec, render_headscale_unit, render_sidecar_unit,
)

_INERT = {"Profile", "StateDir", "RoleIdentity", "DERPPolicy", "ListenAddr", "ExcludedDaemonPorts"}
_BINDING = {"home_id", "control_plane_id", "connector_id", "activation_generation"}


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_live_pair(io: ManagedInstaller, files: dict[str, bytes]) -> dict:
    """Closed public config grammar only; this supplies no protected admission."""
    connector = io._json(files["connector.json"])
    sidecar = io._json(files["sidecar.json"])
    binding, tls = sidecar.get("home_binding"), sidecar.get("control_tls")
    if (sidecar.keys() != _INERT | {"ControlURL", "control_tls", "home_binding"}
            or type(binding) is not dict or binding.keys() != _BINDING
            or type(binding["activation_generation"]) is not int or binding["activation_generation"] < 0
            or any(type(binding[k]) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", binding[k]) is None
                   for k in _BINDING - {"activation_generation"})
            or type(tls) is not dict or tls.keys() != {"dial_ip", "ca_sha256"}
            or type(tls["dial_ip"]) is not str or not invite_v2._canonical_dial_ip(tls["dial_ip"])
            or type(tls["ca_sha256"]) is not str or re.fullmatch("[0-9a-f]{64}", tls["ca_sha256"]) is None
            or sidecar["ControlURL"] != CONTROL_URL
            or connector.get("home_id") != binding["home_id"]
            or connector.get("connector_id") != binding["connector_id"]
            or connector.get("managed", {}).get("control_plane_id") != binding["control_plane_id"]):
        raise InstallerError("home control binding not provisioned")
    inert = {k: sidecar[k] for k in _INERT}
    io._validate_configs({"connector.json": files["connector.json"],
                          "sidecar.json": json.dumps(inert).encode()}, staged=True, partial=True)
    return sidecar


@dataclass(frozen=True, repr=False)
class HomeAdmission:
    receipt: HomeActivationReceipt
    files: dict[str, bytes]

    def __repr__(self) -> str:
        return "HomeAdmission(<redacted>)"


class HomeProfileAdmission:
    def __init__(self, io: ManagedInstaller) -> None:
        self.io = io

    def _files(self, names: tuple[str, ...]) -> dict[str, bytes]:
        service = self.io._account(create=False)
        if service is None:
            raise InstallerError("home service account unavailable")
        self.io._service_owner = service
        with self.io._publication_parent() as (parent, check):
            result = {}
            for name in names:
                self.io._headscale_no_pending(parent, name)
                owner, mode = ((self.io.root_owner, 0o600) if name == "provision.json" else
                               (self.io.root_owner, 0o644) if name == "release-daemon.json" else
                               (service, 0o600) if name == "daemon.token" else
                               ((self.io.root_owner[0], service[1]), 0o640))
                result[name] = self.io._read_file(parent, name, owner=owner, mode=mode)
            check()
            return result

    def require(self, *, ordinary: bool = True) -> HomeAdmission:
        """Full protected admission; inventory may observe any parsed current phase."""
        preparing = self.io._headscale_preparing
        try:
            if os.geteuid() != 0:
                raise InstallerError("home lifecycle requires root")
            self.io._refuse_install_transaction()
            if ordinary and self.io._check_enrollment():
                raise InstallerError("home restart requires no enrollment input")
            files = self._files(("connector.json", "sidecar.json", "policy.json", "provision.json",
                                 "release-daemon.json", "daemon.token"))
            record = self.io._json(files["provision.json"])
            if record.get("layout") not in ("source", "deb"):
                raise InstallerError("home layout invalid")
            self.io.layout = record["layout"]
            if not ordinary and "home_binding" not in self.io._json(files["sidecar.json"]):
                return self._inert_inventory(files)
            sidecar = parse_live_pair(self.io, files)
            generation = sidecar["home_binding"]["activation_generation"]
            store = HomeActivationStore(root=self.io.root, root_owner=self.io.root_owner)
            txns = HomeActivationTransactionStore(root=self.io.root, root_owner=self.io.root_owner)
            receipt = store.read(expected_generation=generation)
            txn = txns.read_optional()
            if (ordinary and receipt.phase != "ready") or txn is None or not txn.matches(receipt):
                raise InstallerError("home activation not admitted")
            if ordinary and txn.phase != "joined":
                raise InstallerError("home activation not admitted")
            effect = next((e for e in txn.effects if e.name == "home_config" and e.state == "done"), None)
            live = {p: files[p.rsplit("/", 1)[1]] for p in CONFIG_MEMBERS}
            if effect is None or {p: _hash(data) for p, data in live.items()} != effect.member_digests:
                raise InstallerError("home config transaction drift")
            # Reverse only the shared renderer's exact insertion, then authenticate
            # its original inputs against the protected Ruling-13a provenance.
            data = files["sidecar.json"]
            end, addition = data.rindex(b"}"), _addition(receipt)
            if data[end-len(addition):end] != addition:
                raise InstallerError("home renderer drift")
            prepared = {"connector.json": files["connector.json"],
                        "sidecar.json": data[:end-len(addition)] + data[end:]}
            self.io._validate_install_record("provision.json", files["provision.json"])
            record = self.io._json(files["provision.json"])
            if "version" not in record:
                raise InstallerError("home requires versioned provisioning")
            expected = {**files, **prepared}
            if any(_hash(expected[name]) != digest for name, digest in record["digests"].items()):
                raise InstallerError("home provision drift")
            if self.io._json(files["release-daemon.json"])["uid"] != receipt.release_uid:
                raise InstallerError("home release drift")
            if render_home_live_configs(prepared["connector.json"], prepared["sidecar.json"], receipt) != live:
                raise InstallerError("home renderer drift")
            self.io._validate_configs({**prepared, "policy.json": files["policy.json"]}, staged=True)
            HomeTokenSourceVerifier(self.io).require(receipt)
            gate = HomeUnitGate(expected_release_uid=receipt.release_uid, expected_home_id=receipt.home_id,
                expected_control_plane_id=receipt.control_plane_id, expected_connector_id=receipt.connector_id,
                expected_generation=generation, root=self.io.root, root_owner=self.io.root_owner)
            gate.inspect()
            self._members(receipt)
            if ordinary:
                units = self.observed_units(HomeAdmission(receipt, files))
                present, _ = self.io._inventory_units(self.io._json(files["connector.json"]), observed_units=units)
                if len(present) != len(UNITS):
                    raise InstallerError("home unit absent")
            # Gate inventory permits absence for pre-activation/cleanup. Ordinary
            # ready admission additionally requires the one literal owned link.
            if ordinary:
                from runtime.remote_access.home_activation import OWNED_LINK, OWNED_LINK_TARGET
                with self.io._publication_parent(OWNED_LINK.rsplit("/", 1)[0].lstrip("/")) as (parent, check):
                    if os.readlink(OWNED_LINK.rsplit("/", 1)[1], dir_fd=parent) != OWNED_LINK_TARGET:
                        raise InstallerError("home link absent")
                    check()
            if (store.read(expected_generation=generation) != receipt or txns.read_optional() != txn
                    or self._files(tuple(files)) != files):
                raise InstallerError("home admission changed")
            return HomeAdmission(receipt, files)
        except Exception:
            raise InstallerError("home control binding not provisioned or activation drift") from None
        finally:
            self.io._headscale_preparing = preparing

    def _inert_inventory(self, files: dict[str, bytes]) -> HomeAdmission:
        # Observation only: protected current receipt supplies its phase/G; IDs
        # come independently from provision-bound prepared files, never adoption.
        store = HomeActivationStore(root=self.io.root, root_owner=self.io.root_owner)
        with store._record(ACTIVATION_PATH) as data:
            receipt = HomeActivationReceipt.parse(data)
        if receipt.phase == "ready":
            raise InstallerError("ready home configs missing")
        txn = HomeActivationTransactionStore(root=self.io.root, root_owner=self.io.root_owner).read_optional()
        if txn is None:
            if receipt.phase != "prepared":
                raise InstallerError("home transaction absent")
        elif (not txn.matches(receipt) or any(e.name == "home_config" and e.state == "done" for e in txn.effects)):
            raise InstallerError("home transaction mismatch")
        self.io._validate_install_record("provision.json", files["provision.json"])
        record = self.io._json(files["provision.json"])
        if "version" not in record or any(_hash(files[name]) != digest for name, digest in record["digests"].items()):
            raise InstallerError("home provision drift")
        self.io._validate_configs({k: files[k] for k in ("connector.json", "sidecar.json", "policy.json")}, staged=True)
        render_home_live_configs(files["connector.json"], files["sidecar.json"], receipt)
        HomeTokenSourceVerifier(self.io).require(receipt)
        self._members(receipt)
        HomeUnitGate(expected_release_uid=receipt.release_uid, expected_home_id=receipt.home_id,
            expected_control_plane_id=receipt.control_plane_id, expected_connector_id=receipt.connector_id,
            expected_generation=receipt.activation_generation, root=self.io.root, root_owner=self.io.root_owner).inspect()
        if (store.read(expected_generation=receipt.activation_generation) != receipt
                or HomeActivationTransactionStore(root=self.io.root, root_owner=self.io.root_owner).read_optional() != txn
                or self._files(tuple(files)) != files):
            raise InstallerError("home inventory changed")
        return HomeAdmission(receipt, files)

    def _members(self, receipt: HomeActivationReceipt) -> None:
        self.io._headscale_owner = self.io._headscale_shape()
        if self.io._headscale_owner is None:
            raise InstallerError("home Headscale account unavailable")
        self.io._headscale_preparing = True  # Reuse the fixed custody layout; no prepare effect.
        self.io._headscale_empty_ca()
        members = self.io._headscale_members()
        for path in MEMBER_PATHS:
            relative, name = path.lstrip("/").rsplit("/", 1)
            with self.io._publication_parent(relative) as (parent, check):
                self.io._headscale_no_pending(parent, name)
                owner, mode = members.get(path, (self.io.root_owner, 0o600))
                data = (self.io._read_unit(parent, name) if path.endswith(".service") else
                        self.io._read_file(parent, name, owner=owner, mode=mode))
                if (_hash(data) != receipt.member_digests[path]
                        or (path.endswith(".service") and data != render_headscale_unit(home_transport=True).encode())):
                    raise InstallerError("home member drift")
                if path == INVITE_CONFIG_PATH:
                    config = HomeInviteConfigRecord.parse(data)
                    if any(getattr(config, key) != getattr(receipt, key) for key in IDENTITY_KEYS):
                        raise InstallerError("home invite binding drift")
                if path.endswith("control-ca.pem") and _hash(ssl.PEM_cert_to_DER_cert(data.decode("ascii"))) != receipt.ca_der_sha256:
                    raise InstallerError("home CA drift")
                check()

    def foreground(self, config: object) -> None:
        """Ruling 15: fixed service UID and root-custodied configs, no protected reads."""
        try:
            service = self.io._account(create=False)
            if service is None or service[0] == 0 or os.geteuid() != service[0]:
                raise InstallerError("home foreground service account required")
            files = self._files(("connector.json", "sidecar.json"))
            parse_live_pair(self.io, files)
            from runtime.remote_access.managed_provider import ManagedProviderConfig
            from runtime.remote_access.supervisor import ConnectorConfig
            raw = self.io._json(files["connector.json"])
            managed = raw.pop("managed")
            if ConnectorConfig(**raw, managed=ManagedProviderConfig(**managed)) != config:
                raise InstallerError("home foreground config mismatch")
        except Exception:
            raise InstallerError("home foreground custody invalid") from None

    def observed_units(self, admitted: HomeAdmission) -> dict[str, str]:
        """Comparison only; E2b owns first publication/selection under intent."""
        units = managed_units(self.io.layout, connector_config=self.io._json(admitted.files["connector.json"]))
        binary = f"/opt/{'happyranch-desktop' if self.io.layout == 'deb' else 'happyranch'}/bin/happyranch-tsnet-sidecar"
        legacy = units[UNITS[1]]
        units[UNITS[1]] = render_sidecar_unit(SidecarUnitSpec(binary_path=binary, home_transport=True))
        if admitted.receipt.phase != "ready":
            txn = HomeActivationTransactionStore(root=self.io.root, root_owner=self.io.root_owner).read_optional()
            if txn is not None and not txn.matches(admitted.receipt):
                raise InstallerError("home transaction mismatch")
            done = txn is not None and any(e.name == "managed_services" and e.state == "done" for e in txn.effects)
            if not done:
                with self.io._publication_parent("etc/systemd/system") as (parent, check):
                    if self.io._read_unit(parent, UNITS[1]) == legacy.encode():
                        units[UNITS[1]] = legacy
                    check()
        return units
