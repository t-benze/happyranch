"""Standalone E1b temp-root/fake-native venue; no installed service or caller.

Run: uv run --frozen pytest scripts/test_home_live_config.py -q
Four authoring answers and attributable mutation receipts live in the PR body.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import replace
from typing import Any

import pytest

from runtime.remote_access import home_activation_owner as adapters
from runtime.remote_access import home_live_config as h
from runtime.remote_access.home_activation import ACTIVATION_PATH, HomeActivationReceipt
from runtime.remote_access.home_activation_coordinator import ActivationRefused, HomeActivationCoordinator
from runtime.remote_access.home_activation_txn import CONFIG_MEMBERS, EFFECTS, TRANSACTION_PATH, Effect
from runtime.remote_access.installer import InstallerError
from scripts.test_home_activation_owner import consumed, intent, venue as owner_venue
from scripts.test_home_activation_txn import Venue, put


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def configs(v: Venue) -> dict[str, bytes]:
    return {path: v.path(path).read_bytes() for path in CONFIG_MEMBERS}


def provenance(v: Venue) -> dict:
    names = ("connector.json", "sidecar.json", "policy.json", "release-daemon.json", "daemon.token")
    raw = dict(version=1, phase="provisioned", layout=v.receipts._io.layout,
               digests={name: digest(v.path("/etc/happyranch/" + name).read_bytes()) for name in names})
    put(v.path("/etc/happyranch/provision.json"), json.dumps(raw).encode())
    return raw


def new_owner(v: Venue, *, generation: int = 7, operation: str | None = None) -> h.HomeLiveConfigOwner:
    return h.HomeLiveConfigOwner(io=v.receipts._io, expected_generation=generation,
                                expected_operation_id=operation or v.prepared.operation_id)


@pytest.fixture
def venue(owner_venue: Venue) -> Venue:
    v = owner_venue
    path = v.path(CONFIG_MEMBERS[1])
    sidecar = json.loads(path.read_bytes())
    sidecar["RoleIdentity"] = "home-sidecar-home"
    # Noncanonical whitespace must survive the renderer and publication exactly.
    path.write_bytes((json.dumps(sidecar, indent=2) + "\n \t").encode())
    provenance(v)
    v.config = new_owner(v)
    v.production._config = v.config
    return v


def expected_live(v: Venue, prepared: dict[str, bytes]) -> dict[str, bytes]:
    r = v.prepared
    # Independent literal wire expectation, not a production renderer/helper.
    addition = (',"ControlURL":"https://control.happyranch.invalid:28443",'
        '"control_tls":{"dial_ip":"' + r.dial_ip + '","ca_sha256":"' + r.ca_der_sha256 + '"},'
        '"home_binding":{"home_id":"home","control_plane_id":"control",'
        '"connector_id":"connector","activation_generation":7}').encode()
    sidecar = prepared[CONFIG_MEMBERS[1]]
    end = sidecar.rindex(b"}")
    return {CONFIG_MEMBERS[0]: prepared[CONFIG_MEMBERS[0]],
            CONFIG_MEMBERS[1]: sidecar[:end] + addition + sidecar[end:]}


def finish_config(v: Venue) -> HomeActivationReceipt:
    receipt = intent(v, "home_config")
    v.config.perform("home_config", receipt, None)
    assert v.config.verify("home_config", receipt)
    txn = v.transactions.read_optional()
    updated = replace(txn, effects=(*txn.effects[:-1], Effect("home_config", "done",
        {path: digest(data) for path, data in configs(v).items()})))
    v.transactions.publish(updated, expected=txn)
    return receipt


def test_renderer_exact_bytes(venue: Venue) -> None:
    v = venue
    before = configs(v)
    rendered = h.render_home_live_configs(*before.values(), v.prepared)
    assert rendered == expected_live(v, before)
    assert h.render_home_live_configs(*before.values(), v.prepared) == rendered
    assert configs(v) == before
    raw = json.loads(rendered[CONFIG_MEMBERS[1]])
    assert set(raw) - set(json.loads(before[CONFIG_MEMBERS[1]])) == {"ControlURL", "control_tls", "home_binding"}
    assert raw["home_binding"] == dict(home_id="home", control_plane_id="control", connector_id="connector",
                                       activation_generation=7)
    assert type(raw["home_binding"]["activation_generation"]) is int


@pytest.mark.parametrize("case", ["extra", "url", "tls", "binding", "duplicate", "connector-extra"])
def test_renderer_extra_fields_refuse(venue: Venue, case: str) -> None:
    v = venue
    connector, sidecar = configs(v).values()
    if case == "connector-extra":
        raw = json.loads(connector)
        raw["unparsed"] = "foreign"
        connector = json.dumps(raw).encode()
    elif case == "duplicate":
        sidecar = b'{"Profile":"linux-managed-identity-v1",' + sidecar[1:]
    else:
        raw = json.loads(sidecar)
        raw[{"extra": "unparsed", "url": "ControlURL", "tls": "control_tls", "binding": "home_binding"}[case]] = None
        sidecar = json.dumps(raw).encode()
    with pytest.raises(ActivationRefused):
        h.render_home_live_configs(connector, sidecar, v.prepared)


@pytest.mark.parametrize("case", ["legacy", "digest", "connector-id", "home-id", "control-id", "uid"])
def test_prepared_binding_refusals(venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = venue
    record_path = v.path("/etc/happyranch/provision.json")
    if case == "legacy":
        raw = json.loads(record_path.read_bytes())
        record_path.write_bytes(json.dumps(dict(layout="source", token_source=str(v.source),
            token_sha256=digest(v.source.read_bytes()), configs={key: raw["digests"][key]
                for key in ("connector.json", "sidecar.json", "policy.json")})).encode())
    elif case == "digest":
        path = v.path(CONFIG_MEMBERS[1])
        path.write_bytes(path.read_bytes() + b" ")
    elif case in ("connector-id", "home-id", "control-id"):
        path = v.path(CONFIG_MEMBERS[0])
        raw = json.loads(path.read_bytes())
        if case == "control-id": raw["managed"]["control_plane_id"] = "foreign"
        else: raw[{"connector-id": "connector_id", "home-id": "home_id"}[case]] = "foreign"
        path.write_bytes(json.dumps(raw).encode())
        if case == "home-id":
            # Keep staged cross-config identity valid; isolate binding to B1.
            path = v.path(CONFIG_MEMBERS[1])
            raw = json.loads(path.read_bytes())
            raw["RoleIdentity"] = "home-sidecar-foreign"
            path.write_bytes(json.dumps(raw).encode())
        provenance(v)
    else:
        # The independent source verifier also rejects this mismatch. Isolate
        # Ruling 13a's digest-checked release-record cross-binding here.
        monkeypatch.setattr(v.config._source, "require", lambda receipt: None)
        path = v.path("/etc/happyranch/release-daemon.json")
        raw = json.loads(path.read_bytes())
        raw["uid"] += 1
        path.write_bytes(json.dumps(raw).encode())
        provenance(v)
    before, records = configs(v), v.records()
    assert not v.config.verify("preflight", v.prepared), case
    assert configs(v) == before and v.records() == records
    assert not list(v.path("/etc/happyranch").glob(".*.pending"))
    assert not v.native.calls


@pytest.mark.parametrize("case", ["version-bool", "version", "phase", "layout", "extra", "digest-keys", "release-digest", "profile"])
def test_versioned_record_and_profile_refusals(venue: Venue, case: str) -> None:
    v = venue
    path = v.path("/etc/happyranch/provision.json")
    raw = json.loads(path.read_bytes())
    if case == "version-bool": raw["version"] = True
    if case == "version": raw["version"] = 2
    if case == "phase": raw["phase"] = "prepared"
    if case == "layout": raw["layout"] = "deb"
    if case == "extra": raw["extra"] = None
    if case == "digest-keys": raw["digests"]["extra"] = "a" * 64
    if case == "release-digest": raw["digests"]["release-daemon.json"] = "a" * 64
    if case == "profile":
        sidecar = v.path(CONFIG_MEMBERS[1])
        data = json.loads(sidecar.read_bytes())
        data["ListenAddr"] = ":444"
        sidecar.write_bytes(json.dumps(data).encode())
        raw["digests"]["sidecar.json"] = digest(sidecar.read_bytes())
    path.write_bytes(json.dumps(raw).encode())
    before = configs(v), v.records()
    assert not v.config.verify("preflight", v.prepared)
    assert (configs(v), v.records()) == before


@pytest.mark.parametrize("name", ["provision.json", "connector.json", "sidecar.json", "release-daemon.json"])
@pytest.mark.parametrize("case", ["symlink", "hardlink", "mode"])
def test_prepared_custody_refusals(venue: Venue, name: str, case: str) -> None:
    v = venue
    path = v.path("/etc/happyranch/" + name)
    if case == "mode": path.chmod(0o666)
    else:
        saved = path.with_name("saved-" + name)
        path.rename(saved)
        if case == "symlink": path.symlink_to(saved)
        else: os.link(saved, path)
    before = configs(v), v.records()
    assert not v.config.verify("preflight", v.prepared)
    assert (configs(v), v.records()) == before


@pytest.mark.parametrize("case", ["generation", "operation", "receipt", "no-intent", "wrong-intent", "credential"])
def test_expected_binding_and_intent(venue: Venue, case: str) -> None:
    v = venue
    receipt = intent(v, "db_users" if case == "wrong-intent" else "home_config") if case != "no-intent" else v.prepared
    if case == "generation": v.config = new_owner(v, generation=8)
    if case == "operation": v.config = new_owner(v, operation="e" * 32)
    if case == "receipt": receipt = replace(receipt, connector_id="foreign")
    before = configs(v), v.records()
    with pytest.raises(ActivationRefused):
        v.config.perform("home_config", receipt, object() if case == "credential" else None)
    assert (configs(v), v.records()) == before


@pytest.mark.parametrize("when", ["before-render", "pending-written"])
def test_changed_bytes_between_read_and_publish(venue: Venue, monkeypatch: pytest.MonkeyPatch, when: str) -> None:
    v = venue
    assert v.config.verify("preflight", v.prepared)
    receipt = intent(v, "home_config")
    path = v.path(CONFIG_MEMBERS[1])
    changed = path.read_bytes() + b" "
    if when == "before-render": path.write_bytes(changed)
    else:
        original = v.config._source.require
        def rotate(receipt: HomeActivationReceipt) -> None:
            original(receipt)
            if v.path("/etc/happyranch/.connector.json.pending").exists():
                path.write_bytes(changed)
        monkeypatch.setattr(v.config._source, "require", rotate)
    with pytest.raises(ActivationRefused):
        v.config.perform("home_config", receipt, None)
    assert path.read_bytes() == changed
    assert b"ControlURL" not in path.read_bytes()
    assert v.path(CONFIG_MEMBERS[0]).read_bytes() == v.config._inputs["connector.json"].data
    assert v.path("/etc/happyranch/.connector.json.pending").exists() is (when == "pending-written")


@pytest.mark.parametrize("name", ["connector.json", "sidecar.json"])
def test_pending_is_not_adopted(venue: Venue, name: str) -> None:
    v = venue
    assert v.config.verify("preflight", v.prepared)
    receipt = intent(v, "home_config")
    pending = v.path("/etc/happyranch/." + name + ".pending")
    put(pending, b"foreign pending", 0o640)
    before = configs(v), v.records(), pending.read_bytes()
    with pytest.raises(ActivationRefused):
        v.config.perform("home_config", receipt, None)
    assert (configs(v), v.records(), pending.read_bytes()) == before


@pytest.mark.parametrize("case", ["missing", "alias"])
def test_no_mkdir_or_parent_alias(venue: Venue, case: str) -> None:
    v = venue
    assert v.config.verify("preflight", v.prepared)
    receipt = intent(v, "home_config")
    parent = v.path("/etc/happyranch")
    saved = parent.with_name("saved-happyranch")
    parent.rename(saved)
    if case == "alias": parent.symlink_to(saved, target_is_directory=True)
    before = {p.name: p.read_bytes() for p in saved.iterdir() if p.is_file()}
    with pytest.raises(ActivationRefused):
        v.config.perform("home_config", receipt, None)
    assert {p.name: p.read_bytes() for p in saved.iterdir() if p.is_file()} == before
    assert parent.is_symlink() if case == "alias" else not parent.exists()


def test_publication_and_independent_verification(venue: Venue, monkeypatch: pytest.MonkeyPatch) -> None:
    v = venue
    prepared = configs(v)
    record = v.path("/etc/happyranch/provision.json").read_bytes()
    reads, syncs, renames = [], [], []
    original_read, original_sync = v.receipts._io._read_file, os.fsync
    original_rename = v.receipts._io._rename_provision_entry
    def read(parent: int, name: str, **kwargs: Any) -> bytes:
        if name in ("connector.json", "sidecar.json"): reads.append(name)
        return original_read(parent, name, **kwargs)
    def sync(fd: int) -> None:
        syncs.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        return original_sync(fd)
    def rename(parent: int, source: str, target: str, **kwargs: Any) -> None:
        renames.append((source, target, tuple(syncs)))
        return original_rename(parent, source, target, **kwargs)
    monkeypatch.setattr(v.receipts._io, "_read_file", read)
    monkeypatch.setattr(v.receipts._io, "_rename_provision_entry", rename)
    monkeypatch.setattr(h.os, "fsync", sync)
    assert v.config.verify("preflight", v.prepared)
    receipt = intent(v, "home_config")
    v.config.perform("home_config", receipt, None)
    assert reads == ["connector.json", "sidecar.json"]
    assert configs(v) == expected_live(v, prepared)
    assert syncs == ["file", "dir", "file", "dir"]
    assert [(s, t) for s, t, _ in renames] == [(".connector.json.pending", "connector.json"),
                                                            (".sidecar.json.pending", "sidecar.json")]
    assert all(history[-1] == "file" for _, _, history in renames)
    for path in CONFIG_MEMBERS:
        info = v.path(path).stat()
        assert (info.st_uid, info.st_gid) == v.owner_ids and stat.S_IMODE(info.st_mode) == 0o640
    assert v.config.verify("home_config", receipt)
    assert v.path("/etc/happyranch/provision.json").read_bytes() == record
    # No repeated perform/adoption, even in the same original intent.
    with pytest.raises(ActivationRefused): v.config.perform("home_config", receipt, None)
    v.path(CONFIG_MEMBERS[1]).write_bytes(v.path(CONFIG_MEMBERS[1]).read_bytes() + b" ")
    assert not v.config.verify("home_config", receipt)


@pytest.mark.parametrize("case", ["before-intent", "after-intent", "second-publication"])
def test_source_failures_at_publication(venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = venue
    before = configs(v), v.records()
    if case == "before-intent":
        v.source.write_bytes(b"synthetic rotated token")
        with pytest.raises(ActivationRefused, match="token_source_invalid"):
            HomeActivationCoordinator(owner=v.production, root=v.root, root_owner=v.owner_ids).run(v.prepared)
        assert (configs(v), v.records()) == before
        return
    original_read = v.receipts._io._read_file
    def read(parent: int, name: str, **kwargs: Any) -> bytes:
        data = original_read(parent, name, **kwargs)
        if case == "after-intent" and name == ".connector.json.pending":
            v.source.write_bytes(b"synthetic rotated token")
        return data
    # Rotate after first rename for the second-publication case.
    original_rename = v.receipts._io._rename_provision_entry
    def rename(parent: int, source: str, target: str, **kwargs: Any) -> None:
        original_rename(parent, source, target, **kwargs)
        if case == "second-publication" and target == "connector.json":
            v.source.write_bytes(b"synthetic rotated token")
    monkeypatch.setattr(v.receipts._io, "_read_file", read)
    monkeypatch.setattr(v.receipts._io, "_rename_provision_entry", rename)
    with pytest.raises(ActivationRefused):
        HomeActivationCoordinator(owner=v.production, root=v.root, root_owner=v.owner_ids).run(v.prepared)
    v.terminal()
    assert v.transactions.read_optional().effects[-1] == Effect("home_config", "intent", {})
    assert configs(v) == before[0]


def test_coordinator_composition_and_b7_digest_verification(venue: Venue, monkeypatch: pytest.MonkeyPatch) -> None:
    v = venue
    before = configs(v)
    monkeypatch.setattr(adapters.VerifierClient, "resolve", lambda self: 12345)
    monkeypatch.setattr(adapters.HomeObserver, "observe", lambda *args, **kwargs: object())
    v.native.on_start = lambda: consumed(v)
    joined = HomeActivationCoordinator(owner=v.production, root=v.root, root_owner=v.owner_ids).run(v.prepared)
    txn = v.transactions.read_optional()
    assert joined.phase == "joined" and txn.phase == "joined"
    assert tuple(e.name for e in txn.effects) == EFFECTS and all(e.state == "done" for e in txn.effects)
    assert txn.effects[5].member_digests == {path: digest(data) for path, data in configs(v).items()}
    assert configs(v) == expected_live(v, before)
    # Terminal B7 evidence is verified by a fresh owner WITHOUT provenance.
    terminal = replace(joined, phase="failed_uncertain")
    put(v.path(ACTIVATION_PATH), terminal.to_bytes())
    put(v.path(TRANSACTION_PATH), replace(txn, phase="failed_uncertain").to_bytes())
    v.path("/etc/happyranch/provision.json").unlink()
    fresh = new_owner(v)
    assert fresh.verify("preflight", terminal) and fresh.verify("home_config", terminal)
    # Matching live semantics with different whitespace must fail the txn digest.
    path = v.path(CONFIG_MEMBERS[1])
    path.write_bytes(path.read_bytes() + b" ")
    assert not fresh.verify("home_config", terminal)


def test_installer_refuses_live_profile_and_rollover_preflight_is_read_only(venue: Venue) -> None:
    v = venue
    receipt = finish_config(v)
    io = v.receipts._io
    before = configs(v), v.records()
    with pytest.raises(InstallerError, match="home control binding not provisioned"):
        io.install()
    with pytest.raises(InstallerError):
        io._validate_configs({path.rsplit("/", 1)[1]: data for path, data in configs(v).items()},
                             staged=True, partial=True)
    with pytest.raises(InstallerError): io._check_provisioning()
    assert (configs(v), v.records()) == before and not v.native.calls
    next_receipt = replace(receipt, phase="prepared", activation_generation=8, operation_id="d" * 32)
    put(v.path(ACTIVATION_PATH), next_receipt.to_bytes())
    old = v.transactions.read_optional()
    put(v.path(TRANSACTION_PATH), replace(old, phase="failed_uncertain").to_bytes())
    next_owner = new_owner(v, generation=8, operation="d" * 32)
    before = configs(v), v.records()
    assert next_owner.verify("preflight", next_receipt)
    assert not next_owner.verify("home_config", next_receipt)
    assert (configs(v), v.records()) == before


@pytest.mark.parametrize("case", ["pending-swap", "fsync", "second-rename"])
def test_failed_publication_is_terminal_and_never_retried(venue: Venue, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    v = venue
    before = configs(v)
    original_read, original_sync = v.receipts._io._read_file, os.fsync
    original_rename = v.receipts._io._rename_provision_entry
    def read(parent: int, name: str, **kwargs: Any) -> bytes:
        data = original_read(parent, name, **kwargs)
        if case == "pending-swap" and name == ".connector.json.pending":
            path = v.path("/etc/happyranch/" + name)
            path.unlink()
            put(path, b"foreign pending", 0o640)
        return data
    def sync(fd: int) -> None:
        if case == "fsync" and stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("synthetic fsync failure")
        return original_sync(fd)
    def rename(parent: int, source: str, target: str, **kwargs: Any) -> None:
        if case == "second-rename" and target == "sidecar.json":
            raise OSError("synthetic rename failure")
        return original_rename(parent, source, target, **kwargs)
    monkeypatch.setattr(v.receipts._io, "_read_file", read)
    monkeypatch.setattr(v.receipts._io, "_rename_provision_entry", rename)
    # Limit this injected fsync failure to config pending writes, leaving the
    # existing receipt/transaction writers free to publish nonsecret markers.
    def config_sync(fd: int) -> None:
        current = os.fstat(fd)
        for name in (".connector.json.pending", ".sidecar.json.pending"):
            path = v.path("/etc/happyranch/" + name)
            if path.exists():
                pending = path.stat()
                if (pending.st_dev, pending.st_ino) == (current.st_dev, current.st_ino):
                    return sync(fd)
        return original_sync(fd)
    monkeypatch.setattr(h.os, "fsync", config_sync)
    coordinator = HomeActivationCoordinator(owner=v.production, root=v.root, root_owner=v.owner_ids)
    with pytest.raises(ActivationRefused): coordinator.run(v.prepared)
    v.terminal()
    assert configs(v) == before
    assert v.transactions.read_optional().effects[-1] == Effect("home_config", "intent", {})
    assert any(v.path("/etc/happyranch/." + name + ".pending").exists()
               for name in ("connector.json", "sidecar.json"))
    records = v.records()
    with pytest.raises(ActivationRefused): coordinator.run(v.prepared)
    assert v.records() == records and configs(v) == before


@pytest.mark.parametrize("case", ["extra-field", "binding", "url", "txn-digest", "pending"])
def test_fresh_verifier_checks_renderer_and_digest(venue: Venue, case: str) -> None:
    v = venue
    receipt = finish_config(v)
    path = v.path(CONFIG_MEMBERS[1])
    data = path.read_bytes()
    if case == "extra-field": data = b'{"extra":true,' + data[1:]
    if case == "binding": data = data.replace(b'"activation_generation":7', b'"activation_generation":8')
    if case == "url": data = data.replace(b'control.happyranch.invalid', b'foreign.happyranch.invalid')
    if case == "txn-digest": data += b" "
    path.write_bytes(data)
    if case != "txn-digest":
        txn = v.transactions.read_optional()
        effect = Effect("home_config", "done", {p: digest(b) for p, b in configs(v).items()})
        put(v.path(TRANSACTION_PATH), replace(txn, effects=(*txn.effects[:-1], effect)).to_bytes())
    if case == "pending": put(v.path("/etc/happyranch/.sidecar.json.pending"), b"foreign")
    before = configs(v), v.records()
    v.path("/etc/happyranch/provision.json").unlink()
    fresh = new_owner(v)
    assert not fresh.verify("home_config", receipt)
    assert (configs(v), v.records()) == before
