"""Standalone E1a adapter checks; fake native transports, real temp-root custody.

The required E1b config adapter is a test double, not a live-config implementation.
No test proves a real join, privileged authentication or installed acceptance.
"""
from __future__ import annotations

import hashlib
import json
import os
import pwd
import ssl
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import home_activation_owner as h, installer
from runtime.remote_access.home_activation import ACTIVATION_PATH, MEMBER_PATHS
from runtime.remote_access.home_activation_coordinator import ActivationRefused, HomeActivationCoordinator
from runtime.remote_access.home_activation_txn import CONFIG_MEMBERS, EFFECTS, TRANSACTION_PATH, Effect, HomeActivationTransaction
from runtime.remote_access.home_admin import HeadscaleUser
from runtime.remote_access.home_minter import CreationWindow, HeadscaleJoinKeyMinter, PreAuthKey
from runtime.remote_access.home_observer import ObservationRefused, SidecarProcess
from runtime.remote_access.invite_issuer import MintedKey
from runtime.remote_access.service_manager import SystemdServiceManager
from runtime.remote_access.systemd_unit import render_headscale_unit
from scripts.test_home_activation_txn import Venue, put


class ConfigOwner:
    def __init__(self) -> None:
        self.calls = []
        self.valid = True

    def verify(self, effect, receipt):
        self.calls.append(("verify", effect))
        return self.valid

    def perform(self, effect, receipt, credential):
        assert effect == "home_config" and credential is None
        self.calls.append(("perform", effect))


class Admin:
    def __init__(self) -> None:
        self.users = ()
        self.calls = []
        self.lost = False
        now = datetime.now(timezone.utc)
        self.window = CreationWindow(now - timedelta(seconds=1), now + timedelta(seconds=90))
        self.key = PreAuthKey("happyranch-node", "1", "a" * 48, False, False, False,
                              self.window.expires_at, now, ())

    def list_users(self, **kwargs):
        self.calls.append("list-users")
        return self.users

    def create_user(self, *, name, **kwargs):
        self.calls.append(name)
        user = HeadscaleUser(len(self.users) + 1, name)
        self.users += (user,)
        if self.lost:
            raise OSError("synthetic private lost reply")
        return user

    def list_pre_auth_keys(self, **kwargs):
        assert kwargs == dict(user="happyranch-node", timeout_s=5)
        self.calls.append("list-keys")
        return (self.key,)


class Native:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = []
        self.override = {}
        self.lost = None
        self.on_start = lambda: None

    def run(self, args, **kwargs):
        self.calls.append(args)
        assert args[0] == "systemctl"
        if args[1] == "show":
            name = args[2]
            props = dict(Id=name, Names=name, FragmentPath=str(self.root / "etc/systemd/system" / name),
                         DropInPaths="", NeedDaemonReload="no", LoadState="loaded", ActiveState="active",
                         SubState="running", User="happyranch-headscale" if name == h._HEADSCALE else "happyranch",
                         Group="happyranch-headscale" if name == h._HEADSCALE else "happyranch",
                         DynamicUser="no", MainPID="321")
            props.update(self.override)
            requested = [arg.split("=", 1)[1] for arg in args if arg.startswith("--property=")]
            return 0, "\n".join(f"{key}={props[key]}" for key in requested)
        if args[1] == "start" and args[2] == h._TARGET:
            self.on_start()
        if args[1] == self.lost:
            raise OSError("synthetic private lost reply")
        return 0, ""


@pytest.fixture
def venue(tmp_path, monkeypatch):
    v = Venue(tmp_path, monkeypatch)
    io = v.receipts._io
    io.layout = "source"
    service = SimpleNamespace(pw_name="happyranch", pw_uid=os.getuid(), pw_gid=os.getgid(),
                              pw_dir="/nonexistent", pw_shell="/usr/sbin/nologin")
    io._lookup_user = lambda name: service if name == "happyranch" else pwd.getpwnam(name)
    io._lookup_group = lambda name: SimpleNamespace(gr_name=name, gr_gid=os.getgid())
    io._service_owner = v.owner_ids
    v.native = Native(v.root)
    io.manager = SystemdServiceManager(system=True, run=v.native.run)
    io._run = v.native.run
    v.config = ConfigOwner()
    v.production = h.HomeActivationOwner(config_owner=v.config, io=io,
        expected_generation=7, expected_operation_id=v.prepared.operation_id)
    v.admin = Admin()
    v.production._admin = v.admin
    monkeypatch.setattr(h.home_admin, "read_local_node_window", lambda *a, **kw: v.admin.window)
    def mint(operation_id, expires_at):
        now = datetime.now(timezone.utc)
        v.admin.window = CreationWindow(now, expires_at)
        v.admin.key = replace(v.admin.key, created_at=now, expiration=expires_at)
        return MintedKey(v.admin.key.key, expires_at)
    monkeypatch.setattr(h.home_admin, "mint_local_node", mint)
    # Existing headscale custody/NSS/materializer implementations have their own
    # standalone venue. These read-only owner results isolate the new composition.
    monkeypatch.setattr(io, "_headscale_shape", lambda: v.owner_ids)
    monkeypatch.setattr(io, "_headscale_publication", lambda: dict(directories=["empty-ca-directory"], dial_ip=v.prepared.dial_ip))
    monkeypatch.setattr(io, "_headscale_materializer", lambda verb: dict(
        ca_fingerprint=v.prepared.ca_der_sha256, renewal_due=False, leaf_not_after="2027-01-01T00:00:00Z"))
    ca = ssl.DER_cert_to_PEM_cert(b"synthetic public CA").encode()
    config = json.loads(v.path("/etc/happyranch/invite-issuer.json").read_bytes())
    config["ca_der_sha256"] = hashlib.sha256(b"synthetic public CA").hexdigest()
    put(v.path("/etc/happyranch/invite-issuer.json"), json.dumps(config).encode())
    put(v.path(MEMBER_PATHS[0]), render_headscale_unit(home_transport=True).encode(), 0o644)
    put(v.path("/etc/happyranch/control-ca.pem"), ca, 0o640)
    put(v.path("/etc/happyranch/headscale/policy.hujson"), h.headscale_service.render_policy())
    digests = {path: hashlib.sha256(v.path(path).read_bytes()).hexdigest() for path in MEMBER_PATHS}
    v.prepared = replace(v.prepared, member_digests=digests, ca_der_sha256=config["ca_der_sha256"])
    put(v.path(ACTIVATION_PATH), v.prepared.to_bytes())
    generated = io._generated_configs()
    raw = json.loads(generated["connector.json"])
    raw.update(home_id="home", connector_id="connector")
    raw["managed"]["control_plane_id"] = "control"
    generated["connector.json"] = json.dumps(raw).encode()
    for name, data in generated.items():
        put(v.path("/etc/happyranch/" + name), data, 0o640)
    names = ('connector.json', 'sidecar.json', 'policy.json', 'release-daemon.json', 'daemon.token')
    put(v.path('/etc/happyranch/provision.json'), json.dumps(dict(version=1, phase='provisioned', layout=io.layout,
        digests={name: hashlib.sha256(v.path('/etc/happyranch/' + name).read_bytes()).hexdigest()
                 for name in names}), sort_keys=True).encode())
    # Model the protected installed package source; the Git checkout is writable.
    package = v.root / 'package'
    package.mkdir(mode=0o755)
    put(package / 'route_policy.json', Path(installer.__file__).with_name('route_policy.json').read_bytes(), 0o644)
    monkeypatch.setattr(installer, '__file__', str(package / 'installer.py'))
    for name, data in installer.managed_units("source", connector_config=raw).items():
        put(v.path("/etc/systemd/system/" + name), data.encode(), 0o644)
    put(v.path("/etc/systemd/system/" + h._SIDECAR), v.production._sidecar_unit(), 0o644)
    (v.root / "etc/credstore").mkdir(mode=0o700)
    (v.root / "var/lib/happyranch-tsnet-sidecar").mkdir(mode=0o700)
    for path in v.root.rglob("*"):
        if path.is_dir(): path.chmod(0o755)
    for relative in ("etc/credstore", "var/lib/happyranch-remote-access", "var/lib/happyranch-tsnet-sidecar"):
        (v.root / relative).chmod(0o700)
    return v


def intent(v, effect):
    receipt = replace(v.prepared, phase="activating")
    put(v.path(ACTIVATION_PATH), receipt.to_bytes())
    effects = []
    for name in EFFECTS:
        state = "intent" if name == effect else "done"
        digests = ({path: hashlib.sha256(v.path(path).read_bytes()).hexdigest() for path in CONFIG_MEMBERS}
                   if name == "home_config" and state == "done" else {})
        effects.append(Effect(name, state, digests))
        if name == effect:
            break
    txn = replace(HomeActivationTransaction.begin(receipt), effects=tuple(effects))
    put(v.path(TRANSACTION_PATH), txn.to_bytes())
    return receipt


def consumed(v):
    v.admin.key = replace(v.admin.key, used=True)
    digest = hashlib.sha256(b"home-sidecar-home\x00https://control.happyranch.invalid:8443").hexdigest()
    put(v.path("/var/lib/happyranch-tsnet-sidecar/credential.consumed"), (digest + "\n").encode())


def test_source_verifier(venue):
    v = venue
    source = h.HomeTokenSourceVerifier(v.receipts._io)
    before = v.records()
    source.require(v.prepared)
    v.source.write_bytes(b"synthetic rotated token")
    with pytest.raises(ActivationRefused, match="^token_source_invalid$"):
        source.require(v.prepared)
    assert v.records() == before
    assert not v.native.calls


@pytest.mark.parametrize("case", ["mode", "symlink", "release-uid", "account"])
def test_source_custody(venue, monkeypatch, case):
    v = venue
    source = h.HomeTokenSourceVerifier(v.receipts._io)
    receipt = v.prepared
    if case == "mode": v.source.chmod(0o644)
    if case == "symlink":
        v.source.unlink()
        v.source.symlink_to(v.path("/etc/happyranch/daemon.token"))
    if case == "release-uid": receipt = replace(receipt, release_uid=receipt.release_uid + 1)
    if case == "account": monkeypatch.setattr(v.receipts._io, "_account", lambda **kw: None)
    with pytest.raises(ActivationRefused, match="^token_source_invalid$"):
        source.require(receipt)
    assert not v.native.calls


@pytest.mark.parametrize("key,value", [
    ("Id", "foreign.service"), ("Names", h._SIDECAR + " alias.service"),
    ("FragmentPath", "/run/systemd/system/foreign.service"), ("DropInPaths", "/etc/foreign.conf"),
    ("NeedDaemonReload", "yes"), ("LoadState", "not-found"), ("ActiveState", "inactive"),
    ("SubState", "dead"), ("User", "root"), ("Group", "root"), ("DynamicUser", "yes"),
    ("MainPID", "0"), ("MainPID", "0321"), ("MainPID", "2147483648"),
])
def test_process_property_refusals(venue, key, value):
    v = venue
    v.native.override[key] = value
    with pytest.raises(ObservationRefused, match="^observation_refused$"):
        v.production.sidecar_process(v.prepared)
    assert all(call[1] == "show" and call[2] == h._SIDECAR for call in v.native.calls)


def test_fresh_process_and_binding(venue):
    v = venue
    v.production._bound(v.prepared)
    v.production._unit(h._SIDECAR, v.production._sidecar_unit(), user="happyranch")
    assert v.production.sidecar_process(v.prepared) == SidecarProcess(os.getuid(), 321)
    v.native.override["MainPID"] = "654"
    assert v.production.sidecar_process(v.prepared).main_pid == 654
    for receipt in (replace(v.prepared, activation_generation=8), replace(v.prepared, operation_id="e" * 32)):
        with pytest.raises(ObservationRefused):
            v.production.sidecar_process(receipt)
    path = v.path("/etc/systemd/system/" + h._SIDECAR)
    path.write_bytes(path.read_bytes() + b"\n# changed")
    with pytest.raises(ObservationRefused):
        v.production.sidecar_process(v.prepared)
    assert all(call[1] == "show" for call in v.native.calls)


@pytest.mark.parametrize("case", ["valid", "config", "source", "policy", "member", "unit", "loaded-unit", "generation"])
def test_preflight_owner_boundaries(venue, case):
    v = venue
    before = v.records()
    if case == "config": v.config.valid = False
    if case == "source": v.source.write_bytes(b"synthetic rotated")
    if case == "policy": v.path("/etc/happyranch/policy.json").write_bytes(b"{}")
    if case == "member": v.path("/etc/happyranch/headscale/config.yaml").write_bytes(b"changed")
    if case == "unit": v.path("/etc/systemd/system/" + h._CONNECTOR).write_bytes(b"changed")
    if case == "loaded-unit": v.native.override["DropInPaths"] = "/etc/foreign.conf"
    receipt = replace(v.prepared, activation_generation=8) if case == "generation" else v.prepared
    if case == "valid": v.production._common(receipt)
    assert v.production.verify("preflight", receipt) is (case == "valid")
    assert v.records() == before
    assert all(call[1] == "show" for call in v.native.calls) and not v.admin.calls


def test_effect_requires_current_intent(venue):
    v = venue
    with pytest.raises(ActivationRefused):
        v.production.perform("headscale_start_verify", v.prepared, None)
    receipt = intent(v, "headscale_start_verify")
    for effect, credential in (("db_users", None), ("unknown", None),
                               ("headscale_start_verify", MintedKey("a" * 48, v.admin.key.expiration))):
        with pytest.raises(ActivationRefused):
            v.production.perform(effect, receipt, credential)
    assert all(call[1] == "show" for call in v.native.calls) and not v.admin.calls


@pytest.mark.parametrize("effect", ["headscale_start_verify", "db_users", "credential_staging", "home_config"])
def test_fixed_effect_and_independent_read(venue, effect):
    v = venue
    receipt = intent(v, effect)
    credential = MintedKey(v.admin.key.key, v.admin.key.expiration) if effect == "credential_staging" else None
    v.production.perform(effect, receipt, credential)
    calls = len(v.admin.calls) + len(v.native.calls)
    assert v.production.verify(effect, receipt)
    if effect == "home_config":
        assert ("perform", effect) in v.config.calls and ("verify", effect) in v.config.calls
    else:
        assert len(v.admin.calls) + len(v.native.calls) > calls
    if effect == "headscale_start_verify":
        assert [call[1:] for call in v.native.calls if call[1] != "show"] == [
            ["daemon-reload"], ["enable", h._HEADSCALE], ["start", h._HEADSCALE]]
        v.native.override["ActiveState"] = "inactive"
        assert not v.production.verify(effect, receipt)
    if effect == "db_users":
        assert [u.name for u in v.admin.users] == ["happyranch-node", "happyranch-invite"]
        v.admin.users += (HeadscaleUser(3, "foreign"),)
        assert not v.production.verify(effect, receipt)
    if effect == "credential_staging":
        path = v.path("/etc/happyranch/enrollment.key")
        assert path.read_bytes() == b"a" * 48 and path.stat().st_mode & 0o777 == 0o600
        path.write_bytes(b"synthetic wrong key")
        assert not v.production.verify(effect, receipt)


@pytest.mark.parametrize("case", ["used", "reusable", "expiration", "window", "duplicate"])
def test_mint_inventory_refusals(venue, monkeypatch, case):
    v = venue
    key = v.admin.key
    if case == "used":
        # Used is permitted for recovery, but requires consumed custody when staging.
        v.admin.key = replace(key, used=True)
        assert not v.production.verify("credential_staging", v.prepared)
        return
    if case == "reusable": v.admin.key = replace(key, reusable=True)
    if case == "expiration": v.admin.key = replace(key, expiration=key.expiration + timedelta(seconds=1))
    if case == "window": v.admin.key = replace(key, created_at=v.admin.window.started_at - timedelta(seconds=1))
    if case == "duplicate": monkeypatch.setattr(v.admin, "list_pre_auth_keys", lambda **kw: (key, key))
    assert not v.production.verify("local_node_mint", v.prepared)


def history_minter(v, monkeypatch, *, used=False, corrupt=None):
    """Real fenced minter; synthetic admin transport/reservation like the review repro."""
    class HistoryAdmin(Admin):
        def __init__(self):
            super().__init__()
            now = datetime.now(timezone.utc)
            self.keys = (replace(self.key, id="old", key="b" * 48, used=used,
                created_at=now - timedelta(minutes=3),
                expiration=now + timedelta(minutes=1) if used else now - timedelta(minutes=1)),)
            self.reservations = []

        def list_pre_auth_keys(self, **kwargs):
            assert kwargs == dict(user="happyranch-node", timeout_s=5)
            self.calls.append("list-keys")
            return self.keys

        def create_pre_auth_key(self, *, user, reusable, ephemeral, expiration, timeout_s):
            assert (user, reusable, ephemeral, timeout_s) == ("happyranch-node", False, False, 5)
            self.calls.append("create-key")
            fresh = replace(self.key, id="new", created_at=datetime.now(timezone.utc), expiration=expiration)
            self.keys += (fresh,)
            if corrupt is not None:
                self.keys = corrupt(self.keys, self.window)
            return fresh

        def expire_pre_auth_key(self, **kwargs):
            raise AssertionError("inert history must not require expiration")

    v.admin = HistoryAdmin()
    v.production._admin = v.admin

    class Records:
        def reserve(self, operation_id, window):
            assert operation_id == v.prepared.operation_id and not v.admin.reservations
            v.admin.reservations.append((operation_id, window))
            v.admin.window = window
            return True

    def mint(operation_id, expires_at):
        return HeadscaleJoinKeyMinter(admin=v.admin, invite_user="happyranch-node",
            now_fn=lambda: datetime.now(timezone.utc), records=Records()).mint(operation_id, expires_at)

    monkeypatch.setattr(h.home_admin, "mint_local_node", mint)


@pytest.mark.parametrize("history", ["expired-unused", "used"])
def test_coordinator_stages_after_historical_key(venue, monkeypatch, history):
    v = venue
    history_minter(v, monkeypatch, used=history == "used")
    def stop_after_staging(*args):
        raise ActivationRefused("failed_uncertain")
    monkeypatch.setattr(v.config, "perform", stop_after_staging)
    with pytest.raises(ActivationRefused, match="^failed_uncertain$"):
        HomeActivationCoordinator(owner=v.production, root=v.root, root_owner=v.owner_ids).run(v.prepared)
    txn = v.transactions.read_optional()
    assert txn.phase == "failed_uncertain"
    assert txn.effects == tuple(Effect(name, "done", {}) for name in EFFECTS[:5]) + (
        Effect("home_config", "intent", {}),)
    assert v.admin.calls.count("create-key") == 1 and len(v.admin.reservations) == 1
    assert len(v.admin.keys) == 2
    assert v.path("/etc/happyranch/enrollment.key").read_bytes() == b"a" * 48
    assert v.receipts._io._check_enrollment()
    assert [call for call in v.native.calls if call[1] == "start"] == [["systemctl", "start", h._HEADSCALE]]


@pytest.mark.parametrize("case", ["in-window", "after-window", "usable-history", "reusable-history",
                               "duplicate-id", "oversize"])
def test_coordinator_refuses_ambiguous_key_inventory(venue, monkeypatch, case):
    v = venue
    def corrupt(keys, window):
        old, fresh = keys
        if case == "in-window":
            return old, fresh, replace(fresh, id="extra", key="c" * 48)
        if case == "after-window":
            return old, fresh, replace(fresh, id="extra", key="c" * 48,
                created_at=window.expires_at + timedelta(seconds=1))
        if case == "usable-history":
            return replace(old, expiration=window.expires_at), fresh
        if case == "reusable-history":
            return replace(old, reusable=True, used=True, expiration=window.expires_at), fresh
        if case == "duplicate-id":
            return replace(old, id=fresh.id), fresh
        assert case == "oversize"
        return old, fresh, replace(old, id="extra", key="c" * 48)
    if case == "oversize":
        # Exercise the real size guard with a small test-side bound and unique IDs.
        monkeypatch.setattr(h, "_MAX_INVENTORY_KEYS", 2)
    history_minter(v, monkeypatch, corrupt=corrupt)
    with pytest.raises(ActivationRefused):
        HomeActivationCoordinator(owner=v.production, root=v.root, root_owner=v.owner_ids).run(v.prepared)
    txn = v.transactions.read_optional()
    assert txn.phase == "failed_uncertain"
    assert txn.effects == tuple(Effect(name, "done", {}) for name in EFFECTS[:3]) + (
        Effect("local_node_mint", "intent", {}),)
    assert v.admin.calls.count("create-key") == 1 and len(v.admin.reservations) == 1
    assert not v.path("/etc/happyranch/enrollment.key").exists()
    assert ("perform", "home_config") not in v.config.calls


def test_managed_services_consumption_and_observation(venue, monkeypatch):
    v = venue
    receipt = intent(v, "credential_staging")
    v.production.perform("credential_staging", receipt, MintedKey(v.admin.key.key, v.admin.key.expiration))
    receipt = intent(v, "managed_services")
    observations = []
    monkeypatch.setattr(h.HomeObserver, "observe", lambda self, r, **kw: observations.append(r))
    monkeypatch.setattr(h.VerifierClient, "resolve", lambda self: 12345)
    v.native.on_start = lambda: consumed(v)
    old = json.loads(v.path('/etc/happyranch/policy.json').read_bytes())
    v.production.perform("managed_services", receipt, None)
    policy = json.loads(v.path('/etc/happyranch/policy.json').read_bytes())
    assert policy['revision'] == old['revision'] + 1
    record = json.loads(v.path('/etc/happyranch/provision.json').read_bytes())
    assert record['digests']['policy.json'] == hashlib.sha256(v.path('/etc/happyranch/policy.json').read_bytes()).hexdigest()
    actions = [call[1:] for call in v.native.calls if call[1] != 'show']
    assert actions.index(['enable', 'happyranch-remote-access-policy-renew.timer']) < actions.index(['start', h._TARGET])
    assert not v.receipts._io._check_enrollment()
    assert len(observations) == 1
    v.admin.users = (HeadscaleUser(1, "happyranch-node"), HeadscaleUser(2, "happyranch-invite"))
    assert v.production.verify("managed_services", receipt)
    assert v.production.verify("joined", receipt)
    assert len(observations) == 3
    v.path("/var/lib/happyranch-tsnet-sidecar/credential.consumed").write_bytes(b"foreign\n")
    assert not v.production.verify("joined", receipt)


@pytest.mark.parametrize("case", ["lost-start", "unused-key", "missing-marker", "observation"])
def test_managed_start_refusal_retains_credential(venue, monkeypatch, case):
    v = venue
    receipt = intent(v, "credential_staging")
    v.production.perform("credential_staging", receipt, MintedKey(v.admin.key.key, v.admin.key.expiration))
    receipt = intent(v, "managed_services")
    if case != "unused-key": v.native.on_start = lambda: consumed(v)
    if case == "lost-start": v.native.lost = "start"
    if case == "missing-marker":
        def no_marker():
            v.admin.key = replace(v.admin.key, used=True)
        v.native.on_start = no_marker
    if case == "observation":
        def refuse(*args, **kwargs): raise ObservationRefused()
        monkeypatch.setattr(h.HomeObserver, "observe", refuse)
    with pytest.raises(ActivationRefused, match="^failed_uncertain$"):
        v.production.perform("managed_services", receipt, None)
    assert v.path("/etc/happyranch/enrollment.key").is_file()
    assert v.path("/etc/happyranch/enrollment.key").read_bytes() == b"a" * 48
    assert [call for call in v.native.calls if call[1] == "start"] == [["systemctl", "start", h._TARGET]]
    assert not any(call[1] in ("stop", "restart") for call in v.native.calls)


@pytest.mark.parametrize("effect", ["headscale_start_verify", "db_users", "credential_staging"])
def test_lost_effect_is_terminal_without_retry(venue, monkeypatch, effect):
    v = venue
    if effect == "headscale_start_verify": v.native.lost = "start"
    if effect == "db_users": v.admin.lost = True
    if effect == "credential_staging":
        original = v.receipts._io._write_enrollment
        def lost(data):
            original(data)
            raise OSError("synthetic private lost staging reply")
        monkeypatch.setattr(v.receipts._io, "_write_enrollment", lost)
    coordinator = HomeActivationCoordinator(owner=v.production, root=v.root, root_owner=v.owner_ids)
    with pytest.raises(ActivationRefused, match="^failed_uncertain$"):
        coordinator.run(v.prepared)
    v.terminal()
    assert v.transactions.read_optional().effects[-1] == Effect(effect, "intent", {})
    if effect == "headscale_start_verify":
        assert ["systemctl", "start", h._HEADSCALE] in v.native.calls
    if effect == "db_users": assert v.admin.calls.count("happyranch-node") == 1
    if effect == "credential_staging": assert v.path("/etc/happyranch/enrollment.key").read_bytes() == b"a" * 48
    calls = len(v.native.calls) + len(v.admin.calls)
    with pytest.raises(ActivationRefused):
        coordinator.run(v.prepared)
    assert len(v.native.calls) + len(v.admin.calls) == calls


def test_installer_stays_inert_for_live_profile(venue):
    v = venue
    path = v.path("/etc/happyranch/sidecar.json")
    raw = json.loads(path.read_bytes())
    raw.update(ControlURL="https://control.happyranch.invalid:8443",
               control_tls=dict(dial_ip=v.prepared.dial_ip, ca_sha256=v.prepared.ca_der_sha256),
               home_binding={key: getattr(v.prepared, key) for key in
                   ("home_id", "control_plane_id", "connector_id", "activation_generation")})
    path.write_bytes(json.dumps(raw).encode())
    with pytest.raises(installer.InstallerError, match="home control binding not provisioned"):
        v.receipts._io.install()
    with pytest.raises(installer.InstallerError):
        v.receipts._io._validate_configs({name: v.path("/etc/happyranch/" + name).read_bytes()
            for name in ("connector.json", "sidecar.json", "policy.json")}, staged=True)
    assert not v.native.calls
