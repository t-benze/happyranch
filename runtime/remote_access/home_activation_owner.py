"""Unmounted E1a adapters; E1b must supply the protected live-config owner.

No factory, caller, authentication entry or installer admission is added here.
The coordinator owns serialization/intents; these adapters never retry effects.
"""
from __future__ import annotations

import hashlib
import os
import re
import ssl
from datetime import datetime, timezone
from pathlib import Path

from runtime.remote_access import headscale_service, home_admin, installer
from runtime.remote_access.home_activation import (
    BINDING_KEYS, INVITE_CONFIG_PATH, MEMBER_PATHS, HomeActivationReceipt, HomeActivationStore,
)
from runtime.remote_access.home_activation_coordinator import ActivationOwner, ActivationRefused
from runtime.remote_access.home_activation_txn import EFFECTS, HomeActivationTransactionStore
from runtime.remote_access.home_minter import HeadscaleJoinKeyMinter, PreAuthKey, _MAX_INVENTORY_KEYS
from runtime.remote_access.home_observer import HomeObserver, ObservationRefused, SidecarProcess
from runtime.remote_access.home_unit_gate import HomeUnitGate
from runtime.remote_access.invite_issuer import HomeInviteConfigRecord, MintedKey
from runtime.remote_access.release_verifier import VerifierClient
from runtime.remote_access.systemd_unit import SidecarUnitSpec, render_headscale_unit, render_sidecar_unit

_SIDECAR = "happyranch-tsnet-sidecar.service"
_CONNECTOR = "happyranch-connector.service"
_HEADSCALE = "happyranch-headscale.service"
_TARGET = "happyranch-managed.target"
_PROPERTIES = ("Id", "Names", "FragmentPath", "DropInPaths", "NeedDaemonReload",
               "LoadState", "ActiveState", "SubState", "User", "Group", "DynamicUser", "MainPID")


class HomeTokenSourceVerifier:
    """Compose the installer's same-FD/UID source rules; retain no token/digest."""

    def __init__(self, io: installer.ManagedInstaller) -> None:
        self._io = io

    def require(self, receipt: HomeActivationReceipt) -> None:
        try:
            release = self._io._check_release_record()
            if release["uid"] != receipt.release_uid:
                raise ValueError
            source = self._io._input(Path(release["data_home"]) / "daemon.token",
                                     token=True, source_uid=receipt.release_uid)
            service = self._io._account(create=False)
            if service is None:
                raise ValueError
            with self._io._publication_parent() as (parent, check):
                staged = self._io._read_file(parent, "daemon.token", owner=service, mode=0o600)
                if staged != source:
                    raise ValueError
                check()
        except Exception:
            raise ActivationRefused("token_source_invalid") from None


class HomeSystemdProcessOwner:
    """Fresh exact-unit systemd reads; UID resolves the declared fixed service user.

    SO_PEERCRED in HomeObserver independently verifies the actual process UID/PID.
    This provider neither starts units nor infers identity from a process name.
    """

    def __init__(self, *, io: installer.ManagedInstaller, expected_generation: int,
                 expected_operation_id: str) -> None:
        if (type(expected_generation) is not int or expected_generation < 0
                or type(expected_operation_id) is not str
                or re.fullmatch("[0-9a-f]{32}", expected_operation_id) is None):
            raise ActivationRefused()
        self._io = io
        self._generation, self._operation = expected_generation, expected_operation_id
        self._receipts = HomeActivationStore(root=io.root, root_owner=io.root_owner)

    def _bound(self, receipt: HomeActivationReceipt) -> None:
        if (type(receipt) is not HomeActivationReceipt
                or receipt.activation_generation != self._generation
                or receipt.operation_id != self._operation
                or self._receipts.read(expected_generation=self._generation) != receipt):
            raise ActivationRefused()

    def _unit(self, name: str, expected: bytes, *, user: str) -> SidecarProcess:
        with self._io._publication_parent("etc/systemd/system") as (parent, check):
            self._io._headscale_no_pending(parent, name)
            if self._io._read_unit(parent, name) != expected:
                raise ActivationRefused()
            props = self._io.manager.show_properties(name, _PROPERTIES)
            if any(props.get(key) != value for key, value in {
                "Id": name, "Names": name,
                "FragmentPath": str(self._io.root / "etc/systemd/system" / name),
                "DropInPaths": "", "NeedDaemonReload": "no", "LoadState": "loaded",
                "ActiveState": "active", "SubState": "running", "User": user,
                "Group": user, "DynamicUser": "no",
            }.items()) or props.keys() != set(_PROPERTIES):
                raise ActivationRefused()
            pid = props["MainPID"]
            if re.fullmatch("[1-9][0-9]{0,9}", pid) is None or int(pid) >= 2**31:
                raise ActivationRefused()
            account = (self._io._account(create=False) if user == "happyranch"
                       else self._io._headscale_shape())
            if account is None or self._io._read_unit(parent, name) != expected:
                raise ActivationRefused()
            check()
            return SidecarProcess(account[0], int(pid))

    def _sidecar_unit(self) -> bytes:
        # Rendering for comparison only. Installer selection/publication stays E2.
        prefix = "happyranch-desktop" if self._io.layout == "deb" else "happyranch"
        return render_sidecar_unit(SidecarUnitSpec(
            binary_path=f"/opt/{prefix}/bin/happyranch-tsnet-sidecar", home_transport=True)).encode()

    def sidecar_process(self, receipt: HomeActivationReceipt) -> SidecarProcess:
        try:
            self._bound(receipt)
            process = self._unit(_SIDECAR, self._sidecar_unit(), user="happyranch")
            self._bound(receipt)
            return process
        except Exception:
            raise ObservationRefused() from None


class HomeActivationOwner(HomeSystemdProcessOwner):
    """Fixed B4 effects and independent B7/B2 observations over existing owners.

    config_owner is REQUIRED, with no permissive default. E1b owns its preflight
    (Ruling 13a prepared binding, or txn-bound live verification), home_config
    publication and verification. No production config owner is delivered in E1a.
    Call only under the coordinator's original lock and current durable intent.
    """

    def __init__(self, *, config_owner: ActivationOwner, expected_generation: int,
                 expected_operation_id: str, io: installer.ManagedInstaller | None = None) -> None:
        io = io or installer.ManagedInstaller()
        super().__init__(io=io, expected_generation=expected_generation,
                         expected_operation_id=expected_operation_id)
        self._config = config_owner
        self._admin = home_admin.RootHeadscaleAdminOperations()
        self._source = HomeTokenSourceVerifier(io)
        self._transactions = HomeActivationTransactionStore(root=io.root, root_owner=io.root_owner)

    def __repr__(self) -> str:
        return "HomeActivationOwner(<redacted>)"

    def _common(self, receipt: HomeActivationReceipt) -> None:
        self._bound(receipt)
        self._source.require(receipt)
        self._io._service_owner = self._io._account(create=False)
        self._io._headscale_owner = self._io._headscale_shape()
        if self._io._service_owner is None or self._io._headscale_owner is None:
            raise ActivationRefused()
        HomeUnitGate(
            expected_release_uid=receipt.release_uid, expected_home_id=receipt.home_id,
            expected_control_plane_id=receipt.control_plane_id, expected_connector_id=receipt.connector_id,
            expected_generation=self._generation, root=self._io.root,
            root_owner=self._io.root_owner).inspect()
        publication = self._io._headscale_publication()
        if (publication is None or "directories" not in publication
                or publication["dial_ip"] != receipt.dial_ip):
            raise ActivationRefused()
        # Existing certificate owner is read ONLY through status, never ensure/renew.
        status = self._io._headscale_materializer("status")
        if (status["ca_fingerprint"] != receipt.ca_der_sha256 or status["renewal_due"]
                or not status["leaf_not_after"]):
            raise ActivationRefused()
        members = self._io._headscale_members()
        for path in MEMBER_PATHS:
            relative = Path(path.lstrip("/"))
            with self._io._publication_parent(str(relative.parent)) as (parent, check):
                self._io._headscale_no_pending(parent, relative.name)
                if path.endswith(".service"):
                    data = self._io._read_unit(parent, relative.name)
                    if data != render_headscale_unit(home_transport=True).encode():
                        raise ActivationRefused()
                else:
                    owner, mode = members.get(path, (self._io.root_owner, 0o600))
                    data = self._io._read_file(parent, relative.name, owner=owner, mode=mode)
                if hashlib.sha256(data).hexdigest() != receipt.member_digests[path]:
                    raise ActivationRefused()
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
                check()
        with self._io._publication_parent() as (parent, check):
            policy = self._io._read_file(parent, "policy.json",
                owner=(self._io.root_owner[0], self._io._service_owner[1]), mode=0o640)
            self._io._validate_configs({"policy.json": policy}, partial=True)
            check()
        if self._config.verify("preflight", receipt) is not True:
            raise ActivationRefused()
        self._managed_fragments()
        self._bound(receipt)

    def _managed_fragments(self) -> dict[str, str]:
        with self._io._publication_parent() as (parent, check):
            raw = self._io._json(self._io._read_file(parent, "connector.json",
                owner=(self._io.root_owner[0], self._io._service_owner[1]), mode=0o640))
            check()
        units = installer.managed_units(self._io.layout, connector_config=raw)
        units[_SIDECAR] = self._sidecar_unit().decode("utf-8")
        with self._io._publication_parent("etc/systemd/system") as (parent, check):
            for name, expected in units.items():
                self._io._headscale_no_pending(parent, name)
                if self._io._read_unit(parent, name) != expected.encode():
                    raise ActivationRefused()
                props = self._io.manager.show_properties(name, (
                    "FragmentPath", "DropInPaths", "NeedDaemonReload", "LoadState"))
                if props != dict(FragmentPath=str(self._io.root / "etc/systemd/system" / name),
                                 DropInPaths="", NeedDaemonReload="no", LoadState="loaded"):
                    raise ActivationRefused()
            check()
        return units

    def _key(self, receipt: HomeActivationReceipt) -> PreAuthKey:
        window = home_admin.read_local_node_window(
            receipt.operation_id, root=self._io.root, root_owner=self._io.root_owner)
        keys = self._admin.list_pre_auth_keys(user="happyranch-node", timeout_s=5)
        if type(keys) is not tuple or len(keys) > _MAX_INVENTORY_KEYS:
            raise ActivationRefused()
        validator = HeadscaleJoinKeyMinter(admin=self._admin, invite_user="happyranch-node",
            now_fn=lambda: datetime.now(timezone.utc), records=None)
        ids = set()
        for key in keys:
            validator._record(key)
            if key.id in ids:
                raise ActivationRefused()
            ids.add(key.id)
        current = [key for key in keys
                   if window.started_at <= key.created_at <= window.expires_at]
        if len(current) != 1:
            raise ActivationRefused()
        key = current[0]
        now = datetime.now(timezone.utc)
        if (key.reusable or key.ephemeral or key.acl_tags
                or key.expiration != window.expires_at
                or (not key.used and key.expiration <= now)):
            raise ActivationRefused()
        for historical in keys:
            if historical is key:
                continue
            # B7 retains inert rows. Used reusable keys can still enroll, so
            # history is conservatively restricted to non-reusable keys too.
            if (historical.created_at >= window.started_at or historical.reusable
                    or (not historical.used and historical.expiration > now)):
                raise ActivationRefused()
        return key

    def _users(self) -> None:
        users = self._admin.list_users(timeout_s=5)
        if (type(users) is not tuple or len(users) != 2
                or any(type(user) is not home_admin.HeadscaleUser for user in users)
                or {user.name for user in users} != {"happyranch-node", "happyranch-invite"}
                or len({user.id for user in users}) != 2):
            raise ActivationRefused()

    def _credential(self, receipt: HomeActivationReceipt) -> None:
        key = self._key(receipt)
        if self._io._check_enrollment():
            if key.used:
                raise ActivationRefused()
            with self._io._publication_parent(installer._CREDSTORE) as (parent, check):
                # _check_enrollment validates the intentional link when present;
                # require its presence as well as the source, not either alone.
                os.stat(installer._CREDENTIAL_NAME, dir_fd=parent, follow_symlinks=False)
                check()
            with self._io._publication_parent() as (parent, check):
                data = self._io._read_file(parent, "enrollment.key", owner=self._io.root_owner, mode=0o600)
                if data != key.key.encode("ascii"):
                    raise ActivationRefused()
                check()
        elif not key.used:
            raise ActivationRefused()
        else:
            self._consumed(receipt)

    def _consumed(self, receipt: HomeActivationReceipt) -> None:
        with self._io._publication_parent("var/lib/happyranch-tsnet-sidecar") as (parent, check):
            marker = self._io._read_file(parent, "credential.consumed",
                                         owner=self._io._service_owner, mode=0o600)
            role = "home-sidecar-" + receipt.home_id
            expected = hashlib.sha256((role + "\x00" + "https://control.happyranch.invalid:8443").encode()).hexdigest()
            if marker != (expected + "\n").encode("ascii"):
                raise ActivationRefused()
            check()

    def _services(self, receipt: HomeActivationReceipt) -> None:
        units = self._managed_fragments()
        self._unit(_CONNECTOR, units[_CONNECTOR].encode(), user="happyranch")
        self.sidecar_process(receipt)
        VerifierClient().resolve()  # Existing root verifier proves the RELEASE owner.
        if not self._key(receipt).used or self._io._check_enrollment():
            raise ActivationRefused()
        self._consumed(receipt)
        HomeObserver(owner=self, admin=self._admin, root=self._io.root).observe(
            receipt, expected_generation=self._generation)

    def verify(self, effect: str, receipt: HomeActivationReceipt) -> bool:
        try:
            if effect not in EFFECTS:
                return False
            self._common(receipt)
            if effect == "headscale_start_verify":
                with self._io._publication_parent("etc/systemd/system") as (parent, check):
                    unit = self._io._read_unit(parent, _HEADSCALE)
                    check()
                self._unit(_HEADSCALE, unit, user="happyranch-headscale")
                self._admin.list_users(timeout_s=5)  # Fresh socket/RPC observation after start.
            elif effect == "db_users":
                self._users()
            elif effect == "local_node_mint":
                self._key(receipt)
            elif effect == "credential_staging":
                self._credential(receipt)
            elif effect == "home_config":
                return self._config.verify(effect, receipt) is True
            elif effect in ("managed_services", "joined"):
                self._users()
                if self._config.verify("home_config", receipt) is not True:
                    raise ActivationRefused()
                self._services(receipt)
            self._bound(receipt)
            return True
        except Exception:
            return False

    def perform(self, effect: str, receipt: HomeActivationReceipt,
                credential: MintedKey | None) -> None:
        try:
            self._common(receipt)
            transaction = self._transactions.read_optional()
            if (transaction is None or not transaction.matches(receipt) or transaction.phase != "activating"
                    or receipt.phase != "activating" or transaction.effects[-1].name != effect
                    or transaction.effects[-1].state != "intent"):
                raise ActivationRefused()
            if effect != "credential_staging" and credential is not None:
                raise ActivationRefused()
            if effect == "headscale_start_verify":
                self._source.require(receipt)
                self._io.manager.daemon_reload()
                self._source.require(receipt)
                self._io.manager.enable(_HEADSCALE)
                self._source.require(receipt)
                self._io.manager.start(_HEADSCALE, timeout=45)
            elif effect == "db_users":
                users = self._admin.list_users(timeout_s=5)
                if type(users) is not tuple or len(users) > 2:
                    raise ActivationRefused()
                names = {user.name for user in users}
                if not names <= {"happyranch-node", "happyranch-invite"}:
                    raise ActivationRefused()
                for name in ("happyranch-node", "happyranch-invite"):
                    if name not in names:
                        self._source.require(receipt)
                        self._admin.create_user(name=name, timeout_s=5)
            elif effect == "credential_staging":
                key = self._key(receipt)
                if (type(credential) is not MintedKey or key.used or credential.key != key.key
                        or credential.expires_at != key.expiration or self._io._check_enrollment()):
                    raise ActivationRefused()
                data = bytearray(credential.key.encode("ascii"))
                try:
                    self._source.require(receipt)
                    self._io._write_enrollment(data)
                finally:
                    data[:] = b"\x00" * len(data)
            elif effect == "home_config":
                self._config.perform(effect, receipt, None)
            elif effect == "managed_services":
                if self._config.verify("home_config", receipt) is not True:
                    raise ActivationRefused()
                self._credential(receipt)
                self._source.require(receipt)
                self._io.manager.daemon_reload()
                for name in ("happyranch-release-verifier.socket", _TARGET):
                    self._source.require(receipt)
                    self._io.manager.enable(name)
                self._source.require(receipt)
                self._io.manager.start(_TARGET, timeout=250)
                # Like install(), discard the private enrollment input after start.
                # No stop/retry/rollback on loss: coordinator records uncertainty.
                if not self._key(receipt).used:
                    raise ActivationRefused()
                self._consumed(receipt)
                HomeObserver(owner=self, admin=self._admin, root=self._io.root).observe(
                    receipt, expected_generation=self._generation)
                self._source.require(receipt)
                self._io._remove_enrollment()
            else:
                # preflight/mint/joined are coordinator-owned, never replayed here.
                raise ActivationRefused()
        except Exception:
            raise ActivationRefused("failed_uncertain") from None
