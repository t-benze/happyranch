"""E2a composed admission in the existing E1b temp-root/fake-systemd venue."""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from runtime.remote_access import home_admission as h, installer, supervisor
from runtime.remote_access.home_activation import ACTIVATION_PATH, OWNED_LINK, OWNED_LINK_TARGET
from runtime.remote_access.home_activation_txn import CONFIG_MEMBERS, EFFECTS, TRANSACTION_PATH, Effect
from runtime.remote_access.installer import InstallerError, UNITS
from runtime.remote_access.home_retirement import MESSAGE
from scripts.test_home_activation_owner import venue as owner_venue
from scripts.test_home_live_config import finish_config, venue as live_venue
from scripts.test_home_activation_txn import put


@pytest.fixture
def venue(live_venue, monkeypatch):
    v = live_venue
    monkeypatch.setattr(h.os, "geteuid", lambda: 0)
    receipt = finish_config(v)
    txn = v.transactions.read_optional()
    done = next(e for e in txn.effects if e.name == "home_config")
    v.txn = replace(txn, phase="joined", effects=tuple(done if name == "home_config" else
        Effect(name, "done", {}) for name in EFFECTS))
    v.ready = replace(receipt, phase="ready", local_node_stable_id="stable-node", home_endpoint="100.64.0.10:443")
    put(v.path(ACTIVATION_PATH), v.ready.to_bytes())
    put(v.path(TRANSACTION_PATH), v.txn.to_bytes())
    link = v.path(OWNED_LINK)
    link.parent.mkdir(mode=0o755, exist_ok=True)
    link.symlink_to(OWNED_LINK_TARGET)
    # Existing E1a fixture models certificate/account readers; fill their fixed
    # directory inventory here, without any host account or service operation.
    put(v.path("/etc/happyranch/headscale/server.crt"), b"synthetic public certificate")
    put(v.path("/etc/happyranch/headscale/server.key"), b"synthetic private fixture")
    v.path("/etc/happyranch/headscale").chmod(0o700)
    v.path("/etc/happyranch/empty-ca-directory").mkdir(mode=0o755)
    v.native.calls.clear()
    return v


def mutating(v):
    return [call[1:3] for call in v.native.calls if call[1] != "show"]


def config(v):
    raw = v.receipts._io._json(v.path(CONFIG_MEMBERS[0]).read_bytes())
    managed = raw.pop("managed")
    return supervisor.ConnectorConfig(**raw, managed=supervisor.ManagedProviderConfig(**managed))


def test_ready_home_admission_and_restart_are_retired(venue):
    v, io = venue, venue.receipts._io
    records = v.records()
    before = {p: v.path(p).read_bytes() for p in CONFIG_MEMBERS}
    with pytest.raises(InstallerError, match=MESSAGE.replace(".", r"\.")):
        h.HomeProfileAdmission(io).require()
    with pytest.raises(InstallerError):
        io._validate_configs({name: v.path("/etc/happyranch/" + name).read_bytes()
                              for name in ("connector.json", "sidecar.json", "policy.json")})
    assert io.inventory().state == "owned-activated"
    assert io.inventory().detail == "ready"
    with pytest.raises(InstallerError) as refused:
        io.install()
    assert str(refused.value) == MESSAGE
    assert mutating(v) == []
    assert v.records() == records and {p: v.path(p).read_bytes() for p in CONFIG_MEMBERS} == before
    assert not v.path("/etc/happyranch/enrollment.key").exists()
    assert not v.mints
    with pytest.raises(InstallerError): io.uninstall(purge=True)
    with pytest.raises(InstallerError): io._write_units()
    assert mutating(v) == []


@pytest.mark.parametrize("case", ["unprovisioned", "partial", "forged", "stale", "pending-receipt",
    "pending-txn", "pending-config", "caller-url-only", "failed_uncertain", "joined", "activating", "prepared",
    "token", "txn-digest", "txn-operation", "txn-missing", "txn-intent", "provision", "render-bytes",
    "link-absent", "link-foreign", "unit", "unit-reload", "nonroot", "policy", "ca", "enrollment"])
def test_ordinary_refusals_have_no_effect(venue, monkeypatch, case):
    v, io = venue, venue.receipts._io
    if case == "unprovisioned": v.path("/etc/happyranch/provision.json").unlink()
    if case == "partial": v.path(CONFIG_MEMBERS[0]).unlink()
    if case == "forged": v.path(ACTIVATION_PATH).write_bytes(b"{}")
    if case == "stale": put(v.path(ACTIVATION_PATH), replace(v.ready, activation_generation=8).to_bytes())
    if case in {"failed_uncertain", "joined", "activating", "prepared"}:
        receipt = replace(v.ready, phase=case, **({"local_node_stable_id": None, "home_endpoint": None}
                                                if case in {"activating", "prepared"} else {}))
        put(v.path(ACTIVATION_PATH), receipt.to_bytes())
    if case.startswith("pending-"):
        path = {"pending-receipt": ACTIVATION_PATH, "pending-txn": TRANSACTION_PATH,
                "pending-config": CONFIG_MEMBERS[1]}[case]
        target = v.path(path)
        put(target.with_name("." + target.name + ".pending"), b"foreign pending")
    if case == "caller-url-only":
        sc = json.loads(v.path(CONFIG_MEMBERS[1]).read_bytes()); del sc["home_binding"]
        v.path(CONFIG_MEMBERS[1]).write_bytes(json.dumps(sc).encode())
    if case == "token": v.source.write_bytes(b"synthetic rotated token")
    if case == "txn-operation": put(v.path(TRANSACTION_PATH), replace(v.txn, operation_id="a" * 32).to_bytes())
    if case == "txn-missing": v.path(TRANSACTION_PATH).unlink()
    if case == "txn-intent":
        effects = v.txn.effects[:5] + (Effect("home_config", "intent", {}),)
        put(v.path(TRANSACTION_PATH), replace(v.txn, phase="activating", effects=effects).to_bytes())
    if case == "txn-digest":
        effects = tuple(Effect("home_config", "done", {p: "a" * 64 for p in CONFIG_MEMBERS})
                        if e.name == "home_config" else e for e in v.txn.effects)
        put(v.path(TRANSACTION_PATH), replace(v.txn, effects=effects).to_bytes())
    if case == "provision":
        path = v.path("/etc/happyranch/provision.json"); raw = json.loads(path.read_bytes())
        raw["digests"]["sidecar.json"] = "a" * 64; path.write_bytes(json.dumps(raw).encode())
    if case == "render-bytes":
        path = v.path(CONFIG_MEMBERS[1]); path.write_bytes(path.read_bytes() + b" ")
        from scripts.test_home_live_config import digest
        effects = tuple(Effect("home_config", "done", {p: digest(v.path(p).read_bytes()) for p in CONFIG_MEMBERS})
                        if e.name == "home_config" else e for e in v.txn.effects)
        put(v.path(TRANSACTION_PATH), replace(v.txn, effects=effects).to_bytes())
    if case == "link-absent": v.path(OWNED_LINK).unlink()
    if case == "link-foreign":
        v.path(OWNED_LINK).unlink(); v.path(OWNED_LINK).symlink_to("../happyranch-headscale.service")
    if case == "unit": v.path("/etc/systemd/system/" + UNITS[1]).write_bytes(b"foreign unit")
    if case == "unit-reload": v.native.override["NeedDaemonReload"] = "yes"
    if case == "nonroot": monkeypatch.setattr(h.os, "geteuid", lambda: 1000)
    if case == "policy": v.path("/etc/happyranch/policy.json").write_bytes(b"{}")
    if case == "ca": v.path("/etc/happyranch/control-ca.pem").write_bytes(b"foreign CA")
    if case == "enrollment": put(v.path("/etc/happyranch/enrollment.key"), b"synthetic enrollment")
    before = v.records()
    with pytest.raises(InstallerError): io.install()
    assert v.records() == before and mutating(v) == [] and not v.mints


@pytest.mark.parametrize("phase", ["activating", "joined", "failed_uncertain"])
@pytest.mark.parametrize("legacy_sidecar", [False, True])
def test_nonready_inventory_is_owned_and_never_adoptable(venue, phase, legacy_sidecar):
    v, io = venue, venue.receipts._io
    receipt = replace(v.ready, phase=phase, **({"local_node_stable_id": None, "home_endpoint": None}
                                             if phase == "activating" else {}))
    put(v.path(ACTIVATION_PATH), receipt.to_bytes())
    txn = replace(v.txn, phase="failed_uncertain" if phase == "failed_uncertain" else "activating" if phase == "activating" else "joined")
    if legacy_sidecar:
        txn = replace(txn, phase="failed_uncertain" if phase == "failed_uncertain" else "activating", effects=txn.effects[:6])
        text = installer.managed_units("source", connector_config=json.loads(v.path(CONFIG_MEMBERS[0]).read_bytes()))[UNITS[1]]
        put(v.path("/etc/systemd/system/" + UNITS[1]), text.encode(), 0o644)
    put(v.path(TRANSACTION_PATH), txn.to_bytes())
    result = io.inventory()
    assert (result.state, result.detail) == ("owned", phase)
    assert io.replacement_inventory().installation.state == "owned"
    with pytest.raises(InstallerError): io.install()
    assert not mutating(v)


@pytest.mark.parametrize("case", ["unknown-config", "unknown-headscale", "extra-link", "unit", "prepared-no-receipt", "empty-ca-absent", "sidecar-absent"])
def test_inventory_keeps_other_drift(venue, case):
    v, io = venue, venue.receipts._io
    if case == "unknown-config": put(v.path("/etc/happyranch/foreign"), b"foreign")
    if case == "unknown-headscale": put(v.path("/etc/happyranch/headscale/foreign"), b"foreign")
    if case == "extra-link": v.path("/etc/systemd/system/extra.service").symlink_to(OWNED_LINK_TARGET)
    if case == "unit": v.path("/etc/systemd/system/" + UNITS[1]).write_bytes(b"foreign")
    if case == "prepared-no-receipt": v.path(ACTIVATION_PATH).unlink()
    if case == "empty-ca-absent": v.path("/etc/happyranch/empty-ca-directory").rmdir()
    if case == "sidecar-absent": v.path(CONFIG_MEMBERS[1]).unlink()
    assert io.inventory().state == "manual-install-conflict"
    assert not mutating(v)


def test_prepared_inventory_and_unreceipted_crash(live_venue, monkeypatch):
    v, io = live_venue, live_venue.receipts._io
    monkeypatch.setattr(h.os, "geteuid", lambda: 0)
    v.path("/etc/happyranch/headscale").chmod(0o700)
    v.path("/etc/happyranch/empty-ca-directory").mkdir(mode=0o755)
    assert (io.inventory().state, io.inventory().detail) == ("owned", "prepared")
    v.path(ACTIVATION_PATH).unlink()
    assert io.inventory().state == "manual-install-conflict"
    # The fragment itself must cause drift, independent of other unknown files.
    for path in ("/etc/happyranch/control-ca.pem", "/etc/happyranch/invite-issuer.json",
                 "/etc/happyranch/headscale/config.yaml", "/etc/happyranch/headscale/derp.yaml",
                 "/etc/happyranch/headscale/policy.hujson"):
        v.path(path).unlink()
    v.path("/etc/happyranch/headscale").rmdir()
    v.path("/etc/happyranch/empty-ca-directory").rmdir()
    text = installer.managed_units("source", connector_config=json.loads(v.path(CONFIG_MEMBERS[0]).read_bytes()))[UNITS[1]]
    put(v.path("/etc/systemd/system/" + UNITS[1]), text.encode(), 0o644)
    assert io.inventory().state == "manual-install-conflict"
    assert not mutating(v)


@pytest.mark.parametrize("method", ["start", "restart", "enable"])
def test_supervisor_root_lifecycle_is_bound(venue, monkeypatch, method):
    v, io = venue, venue.receipts._io
    monkeypatch.setattr(installer, "ManagedInstaller", lambda: io)
    svc = supervisor.ConnectorSupervisor(config=config(v), config_path=Path("/etc/happyranch/connector.json"), manager=io.manager)
    with pytest.raises(supervisor.ConnectorConfigError) as refused:
        getattr(svc, method)()
    assert str(refused.value) == MESSAGE
    assert mutating(v) == []
    put(v.path(ACTIVATION_PATH), replace(v.ready, phase="failed_uncertain").to_bytes())
    with pytest.raises(supervisor.ConnectorConfigError): getattr(svc, method)()
    assert mutating(v) == []


@pytest.mark.parametrize("case", ["valid", "failed-receipt", "wrong-user", "config-drift", "symlink", "mode", "mismatch"])
def test_foreground_uses_custody_without_protected_reads(venue, monkeypatch, case):
    v, io = venue, venue.receipts._io
    cfg = config(v)
    monkeypatch.setattr(h.os, "geteuid", lambda: io._service_owner[0] if case != "wrong-user" else 0)
    if case == "failed-receipt": put(v.path(ACTIVATION_PATH), replace(v.ready, phase="failed_uncertain").to_bytes())
    if case == "config-drift":
        raw = json.loads(v.path(CONFIG_MEMBERS[1]).read_bytes()); raw["home_binding"]["connector_id"] = "other"
        v.path(CONFIG_MEMBERS[1]).write_bytes(json.dumps(raw).encode())
    if case == "symlink":
        path = v.path(CONFIG_MEMBERS[1]); path.rename(path.with_name("retained")); path.symlink_to("retained")
    if case == "mode": v.path(CONFIG_MEMBERS[1]).chmod(0o666)
    if case == "mismatch": cfg.home_id = "other"
    def protected(*args, **kwargs): raise AssertionError("foreground read protected records")
    monkeypatch.setattr(h.HomeActivationStore, "read", protected)
    monkeypatch.setattr(h.HomeActivationTransactionStore, "read_optional", protected)
    with pytest.raises(InstallerError) as refused:
        h.HomeProfileAdmission(io).foreground(cfg)
    assert str(refused.value) == MESSAGE
    assert not mutating(v) and not v.mints


def test_supervisor_foreground_checks_before_run_loop(venue, monkeypatch):
    v, io = venue, venue.receipts._io
    monkeypatch.setattr(installer, "ManagedInstaller", lambda: io)
    monkeypatch.setattr(h.os, "geteuid", lambda: io._service_owner[0])
    svc = supervisor.ConnectorSupervisor(config=config(v), config_path=Path("/etc/happyranch/connector.json"))
    ran = []
    monkeypatch.setattr(svc, "_run_loop", lambda **kwargs: ran.append(True))
    with pytest.raises(supervisor.ConnectorConfigError) as refused:
        svc.run()
    assert str(refused.value) == MESSAGE and ran == []
    svc.config.home_id = "foreign"
    with pytest.raises(supervisor.ConnectorConfigError): svc.run()
    assert not mutating(v)
