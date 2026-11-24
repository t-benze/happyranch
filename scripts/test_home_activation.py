"""Standalone B1 temp-root checks; synthetic records never prove a real join.

Run: uv run --frozen --python /usr/bin/python3 pytest scripts/test_home_activation.py -v --tb=short
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access.installer import ManagedInstaller

from runtime.remote_access import legacy_home_readback
from runtime.remote_access import legacy_home_readback as activation
from runtime.remote_access.legacy_home_receipt import HomeActivationError, HomeActivationReceipt, TRANSACTION_FORMAT
from runtime.remote_access.legacy_home_readback import LegacyHomeReadback as HomeActivationStore
from runtime.remote_access.legacy_home_receipt import HomeInviteConfigRecord

RECEIPT = "var/lib/happyranch-remote-access/home-activation.json"
CONFIG = "etc/happyranch/invite-issuer.json"
DIGEST_MEMBERS = (
    "/etc/systemd/system/happyranch-headscale.service",
    "/etc/happyranch/headscale/config.yaml",
    "/etc/happyranch/headscale/derp.yaml",
    "/etc/happyranch/headscale/policy.hujson",
    "/etc/happyranch/control-ca.pem",
    "/etc/happyranch/invite-issuer.json",
)
OWNERSHIP = {
    "members": [
        "/var/lib/happyranch-headscale/db.sqlite", "/var/lib/happyranch-headscale/db.sqlite-wal",
        "/var/lib/happyranch-headscale/db.sqlite-shm", "/var/lib/happyranch-headscale/noise.key",
        "/var/lib/happyranch-headscale/derp.key", "/run/happyranch-headscale/headscale.sock",
        "/run/happyranch-tsnet-sidecar/observe.sock", "/var/lib/happyranch-headscale/acme-unused",
    ],
    "links": {"/etc/systemd/system/happyranch-managed.target.wants/happyranch-headscale.service":
              "/etc/systemd/system/happyranch-headscale.service"},
}


def encoded(record: dict) -> bytes:
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode()


def config_record() -> dict:
    return dict(format="home-invite-config@2", release_uid=1000, home_id="home", control_plane_id="control",
                connector_id="connector", ca_der_sha256="a" * 64,
                dial_ip="192.168.1.40", control_url="https://control.happyranch.invalid:8443")


def receipt_record(phase: str = "prepared") -> dict:
    config = config_record()
    result = {key: value for key, value in config.items() if key not in ("control_url", "home_endpoint")}
    result.update(format="home-activation@1", activation_generation=7, operation_id="b" * 32,
                  member_digests={path: "c" * 64 for path in DIGEST_MEMBERS},
                  local_node_stable_id=None, home_endpoint=None, phase=phase, ownership=copy.deepcopy(OWNERSHIP))
    result["member_digests"]["/" + CONFIG] = hashlib.sha256(encoded(config)).hexdigest()
    if phase in ("joined", "ready"):
        result.update(local_node_stable_id="synthetic-local-node", home_endpoint="100.64.0.10:443")
    return result


def receipt(phase: str = "prepared") -> HomeActivationReceipt:
    return HomeActivationReceipt.parse(encoded(receipt_record(phase)))


def put(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.write_bytes(data)
    path.chmod(0o600)
    return path


@pytest.fixture
def store(tmp_path: Path) -> HomeActivationStore:
    for relative in ("etc", "etc/happyranch", "var", "var/lib", "var/lib/happyranch-remote-access"):
        (tmp_path / relative).mkdir(mode=0o700 if relative.endswith("happyranch-remote-access") else 0o755)
    return HomeActivationStore(root=tmp_path, root_owner=(os.getuid(), os.getgid()))




def put_pair(root: Path, *, phase: str = "ready") -> None:
    put(root, CONFIG, encoded(config_record()))
    put(root, RECEIPT, encoded(receipt_record(phase)))


def test_receipt_roundtrip_and_frozen_inventory() -> None:
    source = receipt_record()
    value = HomeActivationReceipt.parse(encoded(source))
    assert json.loads(value.to_bytes()) == source
    assert tuple(value.member_digests) == tuple(sorted(DIGEST_MEMBERS))
    assert value.ownership.to_dict() == OWNERSHIP
    assert TRANSACTION_FORMAT == "home-activation-txn@1"
    with pytest.raises(FrozenInstanceError):
        value.phase = "ready"
    with pytest.raises(TypeError):
        value.member_digests[DIGEST_MEMBERS[0]] = "d" * 64
    source["member_digests"][DIGEST_MEMBERS[0]] = "d" * 64
    assert value.member_digests[DIGEST_MEMBERS[0]] == "c" * 64
    inventory = value.ownership.to_dict()
    inventory["members"].append("/arbitrary")
    assert value.ownership.to_dict() == OWNERSHIP


@pytest.mark.parametrize("kind", ["receipt", "config"])
@pytest.mark.parametrize("case", [
    "extra", "missing", "duplicate", "format", "float", "boolean", "negative", "nan",
    "trailing", "utf8", "uid", "uid-bool", "identity", "fingerprint", "dial-ip", "oversized",
])
def test_closed_record_parsers(kind: str, case: str) -> None:
    value = receipt_record() if kind == "receipt" else config_record()
    parser = HomeActivationReceipt.parse if kind == "receipt" else HomeInviteConfigRecord.parse
    error = HomeActivationError
    if case == "extra": value["secret"] = "synthetic-private-canary"
    if case == "missing": value.pop("connector_id")
    if case == "format": value["format"] = "foreign@1"
    if case == "float": value["activation_generation"] = 7.0
    if case == "boolean": value["activation_generation"] = True
    if case == "negative": value["activation_generation"] = -1
    if case == "nan": value["activation_generation"] = float("nan")
    if case == "uid": value["release_uid"] = 0
    if case == "uid-bool": value["release_uid"] = True
    if case == "identity": value["home_id"] = "home/foreign"
    if case == "fingerprint": value["ca_der_sha256"] = "A" * 64
    if case == "dial-ip": value["dial_ip"] = "192.168.1.040"
    data = encoded(value)
    if case == "duplicate": data = b'{"home_id":"home",' + data[1:]
    if case == "trailing": data += b" {}"
    if case == "utf8": data += b"\xff"
    if case == "oversized": data += b" " * (16385 - len(data))
    with pytest.raises(error):
        parser(data)


@pytest.mark.parametrize("phase", ["prepared", "activating", "joined", "ready", "failed_uncertain"])
@pytest.mark.parametrize("case", ["missing", "extra", "uppercase", "short", "duplicate", "secret"])
def test_six_digest_members_in_every_phase(phase: str, case: str) -> None:
    value = receipt_record(phase)
    key = DIGEST_MEMBERS[0]
    if case == "missing": value["member_digests"].pop(key)
    if case == "extra": value["member_digests"]["/arbitrary"] = "d" * 64
    if case == "uppercase": value["member_digests"][key] = "D" * 64
    if case == "short": value["member_digests"][key] = "d" * 63
    if case == "secret":
        value["member_digests"]["/etc/happyranch/headscale/server.key"] = "d" * 64
    data = encoded(value)
    if case == "duplicate":
        data = data.replace(b'"member_digests":{', b'"member_digests":{' + encoded({key: "c" * 64})[1:-1] + b",")
    with pytest.raises(HomeActivationError):
        HomeActivationReceipt.parse(data)


@pytest.mark.parametrize("case", ["extra-key", "extra-member", "missing-member", "duplicate-member",
                                 "link-target", "extra-link", "duplicate-link", "member-type"])
def test_closed_ownership_inventory(case: str) -> None:
    value = receipt_record()
    owner = value["ownership"]
    if case == "extra-key": owner["secret"] = "synthetic-private-canary"
    if case == "extra-member": owner["members"].append("/arbitrary")
    if case == "missing-member": owner["members"].pop()
    if case == "duplicate-member": owner["members"][-1] = owner["members"][0]
    if case == "link-target": owner["links"][next(iter(owner["links"]))] = "/arbitrary"
    if case == "extra-link": owner["links"]["/arbitrary"] = "/elsewhere"
    if case == "member-type": owner["members"][0] = {}
    data = encoded(value)
    if case == "duplicate-link":
        data = data.replace(b'"links":{', b'"links":{' + encoded(owner["links"])[1:-1] + b",")
    with pytest.raises(HomeActivationError):
        HomeActivationReceipt.parse(data)


@pytest.mark.parametrize("phase", ["prepared", "activating", "joined", "ready", "failed_uncertain"])
@pytest.mark.parametrize("observed", [False, True, "partial"])
def test_observation_fields_by_phase(phase: str, observed: object) -> None:
    value = receipt_record(phase)
    value.update(local_node_stable_id="synthetic-local-node" if observed else None,
                 home_endpoint="100.64.0.10:443" if observed is True else None)
    invalid = observed == "partial" or (phase in ("prepared", "activating") and observed) or (
        phase == "ready" and not observed)
    if invalid:
        with pytest.raises(HomeActivationError):
            HomeActivationReceipt.parse(encoded(value))
    else:
        assert HomeActivationReceipt.parse(encoded(value)).phase == phase










@pytest.mark.parametrize("path", [RECEIPT])
@pytest.mark.parametrize("pending", [False, True])
def test_missing_final_and_pending_are_never_adopted(store: HomeActivationStore, tmp_path: Path,
                                                    path: str, pending: bool) -> None:
    put_pair(tmp_path)
    final = tmp_path / path
    original = final.read_bytes()
    final.unlink()
    partial = final.with_name("." + final.name + ".pending")
    if pending:
        partial.write_bytes(original)
        partial.chmod(0o600)
    call = lambda: store.read(expected_generation=7)
    with pytest.raises(HomeActivationError):
        call()
    assert not final.exists()
    assert partial.exists() == pending
    if pending: assert partial.read_bytes() == original


def spoof_metadata(monkeypatch: pytest.MonkeyPatch, path: Path, field: str) -> None:
    target = path.stat()
    original_stat, original_fstat = os.stat, os.fstat
    def changed(info: os.stat_result) -> object:
        if (info.st_dev, info.st_ino) != (target.st_dev, target.st_ino): return info
        fields = {key: getattr(info, key) for key in dir(info) if key.startswith("st_")}
        fields[field] += 1
        return SimpleNamespace(**fields)
    monkeypatch.setattr(activation.os, "stat", lambda *args, **kwargs: changed(original_stat(*args, **kwargs)))
    monkeypatch.setattr(activation.os, "fstat", lambda *args, **kwargs: changed(original_fstat(*args, **kwargs)))


@pytest.mark.parametrize("path", [RECEIPT])
@pytest.mark.parametrize("case", ["mode", "owner", "group", "symlink", "hardlink", "directory", "fifo",
                                 "oversized", "pending-file", "pending-link", "pending-directory", "parent-mode",
                                 "parent-owner", "parent-link"])
def test_protected_reader_custody(store: HomeActivationStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                 path: str, case: str) -> None:
    put_pair(tmp_path)
    assert store.read(expected_generation=7).home_id == "home"
    final = tmp_path / path
    original = final.read_bytes()
    if case == "mode": final.chmod(0o644)
    if case in ("owner", "group"): spoof_metadata(monkeypatch, final, "st_uid" if case == "owner" else "st_gid")
    if case in ("symlink", "hardlink"):
        alternate = final.with_name("foreign")
        final.rename(alternate)
        if case == "symlink": final.symlink_to(alternate)
        else: os.link(alternate, final)
    if case in ("directory", "fifo"):
        final.unlink()
        if case == "directory": final.mkdir()
        else: os.mkfifo(final)
    if case == "oversized": final.write_bytes(b" " * 16385)
    pending = final.with_name("." + final.name + ".pending")
    if case == "pending-file":
        pending.write_bytes(original)
        pending.chmod(0o600)
    if case == "pending-link": pending.symlink_to(final)
    if case == "pending-directory": pending.mkdir()
    if case == "parent-mode": final.parent.chmod(0o755 if path == RECEIPT else 0o777)
    if case == "parent-owner": spoof_metadata(monkeypatch, final.parent, "st_uid")
    if case == "parent-link":
        moved = final.parent.with_name("moved-parent")
        final.parent.rename(moved)
        final.parent.symlink_to(moved, target_is_directory=True)
    call = lambda: store.read(expected_generation=7)
    with pytest.raises(HomeActivationError):
        call()
    assert final.lstat()
    if case.startswith("pending-"): assert pending.lstat()


@pytest.mark.parametrize("path", [RECEIPT])
@pytest.mark.parametrize("swap", ["leaf", "parent", "ancestor"])
def test_reader_revalidates_swapped_paths(store: HomeActivationStore, tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch, path: str, swap: str) -> None:
    put_pair(tmp_path)
    assert store.read(expected_generation=7).home_id == "home"
    final = tmp_path / path
    original_read = ManagedInstaller._read_file
    swapped = False
    def read(parent: int, name: str, **kwargs: object) -> bytes:
        nonlocal swapped
        data = original_read(parent, name, **kwargs)
        if not swapped and name == final.name:
            swapped = True
            if swap == "leaf":
                final.rename(final.with_name("retained-original"))
                put(tmp_path, path, data)
            else:
                directory = final.parent if swap == "parent" else final.parent.parent
                directory.rename(directory.with_name("retained-parent"))
                directory.mkdir(mode=0o700 if path == RECEIPT and swap == "parent" else 0o755)
        return data
    monkeypatch.setattr(ManagedInstaller, "_read_file", staticmethod(read))
    call = lambda: store.read(expected_generation=7)
    with pytest.raises(HomeActivationError):
        call()
    assert swapped










@pytest.mark.parametrize("kind", ["receipt", "config"])
def test_record_size_maximum_is_inclusive(kind: str) -> None:
    data = encoded(receipt_record() if kind == "receipt" else config_record())
    parser = HomeActivationReceipt.parse if kind == "receipt" else HomeInviteConfigRecord.parse
    assert parser(data + b" " * (16384 - len(data)))
    with pytest.raises(HomeActivationError):
        parser(data + b" " * (16385 - len(data)))


@pytest.mark.parametrize('case', ['absent', 'receipt', 'pending', 'symlink', 'malformed', 'parent'])
def test_current_receipt_read_only(store: HomeActivationStore, tmp_path: Path, case: str) -> None:
    path = tmp_path / RECEIPT
    if case == 'receipt':
        put(tmp_path, RECEIPT, receipt().to_bytes())
    elif case == 'pending':
        put(tmp_path, str(Path(RECEIPT).with_name('.home-activation.json.pending')), b'evidence')
    elif case == 'symlink':
        path.symlink_to('missing')
    elif case == 'malformed':
        put(tmp_path, RECEIPT, b'{}')
    elif case == 'parent':
        path.parent.chmod(0o755)
    before = {str(p): (p.lstat().st_mode, None if p.is_symlink() or p.is_dir() else p.read_bytes())
              for p in tmp_path.rglob('*')}
    if case == 'absent':
        assert store.current() is None
    elif case == 'receipt':
        observed = store.current()
        assert observed is not None
        assert observed.activation_generation == 7 and observed.operation_id == 'b' * 32
        assert observed == store.read(expected_generation=7)
    else:
        with pytest.raises(HomeActivationError):
            store.current()
    after = {str(p): (p.lstat().st_mode, None if p.is_symlink() or p.is_dir() else p.read_bytes())
             for p in tmp_path.rglob('*')}
    assert after == before


def test_current_absence_revalidates_before_return(store: HomeActivationStore, tmp_path: Path) -> None:
    with pytest.raises(HomeActivationError):
        with store._record(activation.ACTIVATION_PATH, allow_absent=True) as value:
            assert value is None
            put(tmp_path, RECEIPT, receipt().to_bytes())
    assert store.current() == receipt()


def test_helper_snapshot_is_closed_and_read_only(store: HomeActivationStore, tmp_path: Path,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(legacy_home_readback, 'LegacyHomeReadback', lambda: store)
    assert legacy_home_readback.helper_snapshot() == b'null\n'
    path = put(tmp_path, RECEIPT, receipt().to_bytes())
    original = path.read_bytes()
    assert json.loads(legacy_home_readback.helper_snapshot()) == {
        'generation': 7, 'operation': 'b' * 32, 'release_uid': 1000, 'phase': 'prepared'}
    assert path.read_bytes() == original
