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

from runtime.remote_access import home_activation as activation
from runtime.remote_access.home_activation import HomeActivationError, HomeActivationReceipt, HomeActivationStore
from runtime.remote_access.invite_issuer import HomeInviteConfigRecord, IssuerError, read_home_invite_config

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
    return dict(format="home-invite-config@1", release_uid=1000, home_id="home", control_plane_id="control",
                connector_id="connector", activation_generation=7, ca_der_sha256="a" * 64,
                dial_ip="192.168.1.40", control_url="https://control.happyranch.invalid:8443",
                home_endpoint="100.64.0.10:443")


def receipt_record(phase: str = "prepared") -> dict:
    config = config_record()
    result = {key: value for key, value in config.items() if key not in ("control_url", "home_endpoint")}
    result.update(format="home-activation@1", operation_id="b" * 32,
                  member_digests={path: "c" * 64 for path in DIGEST_MEMBERS},
                  local_node_stable_id=None, home_endpoint=None, phase=phase, ownership=copy.deepcopy(OWNERSHIP))
    result["member_digests"]["/" + CONFIG] = hashlib.sha256(encoded(config)).hexdigest()
    if phase in ("joined", "ready"):
        result.update(local_node_stable_id="synthetic-local-node", home_endpoint=config["home_endpoint"])
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


def load_config(root: Path, generation: int = 7) -> HomeInviteConfigRecord:
    return read_home_invite_config(root=root, root_owner=(os.getuid(), os.getgid()),
                                   expected_generation=generation)


def put_pair(root: Path, *, phase: str = "ready") -> None:
    put(root, CONFIG, encoded(config_record()))
    put(root, RECEIPT, encoded(receipt_record(phase)))


def test_receipt_roundtrip_and_frozen_inventory() -> None:
    source = receipt_record()
    value = HomeActivationReceipt.parse(encoded(source))
    assert json.loads(value.to_bytes()) == source
    assert tuple(value.member_digests) == tuple(sorted(DIGEST_MEMBERS))
    assert value.ownership.to_dict() == OWNERSHIP
    assert activation.TRANSACTION_FORMAT == "home-activation-txn@1"
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
    error = HomeActivationError if kind == "receipt" else IssuerError
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


@pytest.mark.parametrize("before", ["prepared", "activating", "joined", "ready", "failed_uncertain"])
@pytest.mark.parametrize("after", ["prepared", "activating", "joined", "ready", "failed_uncertain"])
def test_phase_edges(before: str, after: str) -> None:
    previous = receipt(before)
    following = receipt(after)
    if after == "failed_uncertain" and before in ("joined", "ready"):
        following = replace(following, local_node_stable_id=previous.local_node_stable_id,
                            home_endpoint=previous.home_endpoint)
    allowed = (before, after) in {("prepared", "activating"), ("activating", "joined"),
                                 ("joined", "ready")} or (after == "failed_uncertain" and before != after)
    if allowed:
        activation.validate_transition(previous, following)
    else:
        with pytest.raises(HomeActivationError):
            activation.validate_transition(previous, following)


@pytest.mark.parametrize("field,value", [
    ("activation_generation", 6), ("activation_generation", 8), ("release_uid", 1001),
    ("home_id", "other"), ("control_plane_id", "other"), ("connector_id", "other"),
    ("operation_id", "d" * 32), ("ca_der_sha256", "d" * 64), ("dial_ip", "192.168.1.41"),
])
def test_transition_preserves_generation_and_binding(field: str, value: object) -> None:
    with pytest.raises(HomeActivationError):
        activation.validate_transition(receipt(), replace(receipt("activating"), **{field: value}))


def test_transition_preserves_observation_and_cannot_invent_it_on_failure() -> None:
    with pytest.raises(HomeActivationError):
        activation.validate_transition(receipt("joined"), replace(receipt("ready"), home_endpoint="100.64.0.11:443"))
    with pytest.raises(HomeActivationError):
        activation.validate_transition(receipt(), replace(receipt("failed_uncertain"),
                                      local_node_stable_id="synthetic-local-node", home_endpoint="100.64.0.10:443"))


def test_publication_fsync_atomic_replace_and_stale_cas(store: HomeActivationStore, tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    synced: set[tuple[int, int]] = set()
    sync = os.fsync
    rename = activation.ManagedInstaller._rename_provision_entry
    observed: list[str] = []
    def fsync(fd: int) -> None:
        info = os.fstat(fd)
        synced.add((info.st_dev, info.st_ino))
        sync(fd)
    def publish(parent: int, source: str, target: str, *, updating: bool = False) -> None:
        pending = os.stat(source, dir_fd=parent, follow_symlinks=False)
        assert (pending.st_dev, pending.st_ino) in synced, "pending bytes must be fsynced before publication"
        assert stat.S_IMODE(pending.st_mode) == 0o600 and pending.st_nlink == 1
        assert (pending.st_uid, pending.st_gid) == (os.getuid(), os.getgid())
        assert source == ".home-activation.json.pending" and target == "home-activation.json"
        if updating:
            old = os.stat(target, dir_fd=parent, follow_symlinks=False)
            assert (old.st_dev, old.st_ino) != (pending.st_dev, pending.st_ino)
        rename(parent, source, target, updating=updating)
        observed.append("replace" if updating else "create")
    monkeypatch.setattr(activation.os, "fsync", fsync)
    monkeypatch.setattr(activation.ManagedInstaller, "_rename_provision_entry", staticmethod(publish))
    prepared = receipt()
    store.publish(prepared)
    first = (tmp_path / RECEIPT).stat().st_ino
    assert store.read(expected_generation=7) == prepared
    assert not (tmp_path / RECEIPT).with_name(".home-activation.json.pending").exists()
    previous = prepared
    for phase in ("activating", "joined", "ready"):
        updated = receipt(phase)
        store.publish(updated, expected=previous)
        assert store.read(expected_generation=7) == updated
        current = (tmp_path / RECEIPT).stat().st_ino
        assert current != first
        first = current
        previous = updated
    assert observed == ["create", "replace", "replace", "replace"]
    assert (tmp_path / RECEIPT).read_bytes() == receipt("ready").to_bytes()
    assert ((tmp_path / RECEIPT).parent.stat().st_dev, (tmp_path / RECEIPT).parent.stat().st_ino) in synced
    with pytest.raises(HomeActivationError):
        store.publish(receipt("activating"), expected=prepared)
    with pytest.raises(HomeActivationError):
        store.publish(prepared)
    with pytest.raises(HomeActivationError):
        store.read(expected_generation=6)


@pytest.mark.parametrize("path", [RECEIPT, CONFIG])
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
    call = (lambda: store.read(expected_generation=7)) if path == RECEIPT else (lambda: load_config(tmp_path))
    with pytest.raises((HomeActivationError, IssuerError)):
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


@pytest.mark.parametrize("path", [RECEIPT, CONFIG])
@pytest.mark.parametrize("case", ["mode", "owner", "group", "symlink", "hardlink", "directory", "fifo",
                                 "oversized", "pending-file", "pending-link", "pending-directory", "parent-mode",
                                 "parent-owner", "parent-link"])
def test_protected_reader_custody(store: HomeActivationStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                 path: str, case: str) -> None:
    put_pair(tmp_path)
    assert load_config(tmp_path).activation_generation == 7
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
    call = (lambda: store.read(expected_generation=7)) if path == RECEIPT else (lambda: load_config(tmp_path))
    with pytest.raises((HomeActivationError, IssuerError)):
        call()
    assert final.lstat()
    if case.startswith("pending-"): assert pending.lstat()


@pytest.mark.parametrize("path", [RECEIPT, CONFIG])
@pytest.mark.parametrize("swap", ["leaf", "parent", "ancestor"])
def test_reader_revalidates_swapped_paths(store: HomeActivationStore, tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch, path: str, swap: str) -> None:
    put_pair(tmp_path)
    assert load_config(tmp_path).activation_generation == 7
    final = tmp_path / path
    original_read = activation.ManagedInstaller._read_file
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
    monkeypatch.setattr(activation.ManagedInstaller, "_read_file", staticmethod(read))
    call = (lambda: store.read(expected_generation=7)) if path == RECEIPT else (lambda: load_config(tmp_path))
    with pytest.raises((HomeActivationError, IssuerError)):
        call()
    assert swapped


@pytest.mark.parametrize("case", ["pending", "final", "missing-parent", "parent-link",
                                 "parent-swap", "pending-swap", "late-final", "stale-final", "fsync"])
def test_publication_refuses_and_retains_uncertainty(store: HomeActivationStore, tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    final = tmp_path / RECEIPT
    pending = final.with_name(".home-activation.json.pending")
    if case == "pending": pending.write_bytes(b"foreign")
    if case == "final": put(tmp_path, RECEIPT, b"foreign")
    if case == "missing-parent": final.parent.rmdir()
    if case == "parent-link":
        final.parent.rename(final.parent.with_name("retained-parent"))
        final.parent.symlink_to(final.parent.with_name("retained-parent"), target_is_directory=True)
    expected = None
    if case == "stale-final":
        expected = receipt()
        store.publish(expected)
    sync = os.fsync
    fired = False
    def fsync(fd: int) -> None:
        nonlocal fired
        sync(fd)
        if fired or not stat.S_ISREG(os.fstat(fd).st_mode): return
        fired = True
        if case == "parent-swap":
            final.parent.rename(final.parent.with_name("retained-parent"))
            final.parent.mkdir(mode=0o700)
        if case == "pending-swap":
            pending.rename(pending.with_name("retained-pending"))
            pending.symlink_to("retained-pending")
        if case in ("late-final", "stale-final"): put(tmp_path, RECEIPT, b"foreign")
        if case == "fsync": raise OSError("synthetic-private-canary")
    monkeypatch.setattr(activation.os, "fsync", fsync)
    with pytest.raises(HomeActivationError) as caught:
        store.publish(receipt("activating") if expected else receipt(), expected=expected)
    assert str(caught.value) in ("state", "transition")
    if case in ("final", "late-final", "stale-final"): assert final.read_bytes() == b"foreign"
    if case == "pending": assert pending.read_bytes() == b"foreign"
    if case == "parent-swap": assert not final.exists() and final.parent.with_name("retained-parent").is_dir()
    if case in ("fsync", "pending-swap", "late-final", "stale-final"):
        assert pending.lstat()
        with pytest.raises(HomeActivationError):
            store.read(expected_generation=7)


@pytest.mark.parametrize("field,value", [
    ("release_uid", 1001), ("home_id", "other"), ("control_plane_id", "other"), ("connector_id", "other"),
    ("activation_generation", 6), ("ca_der_sha256", "d" * 64), ("dial_ip", "192.168.1.41"),
    ("home_endpoint", "100.64.0.11:443"),
])
def test_reader_cross_checks_each_binding(store: HomeActivationStore, tmp_path: Path,
                                        field: str, value: object) -> None:
    config = config_record()
    config[field] = value
    data = encoded(config)
    current = receipt_record("ready")
    # Keep the full-file digest matching so this observes the field cross-check.
    current["member_digests"]["/" + CONFIG] = hashlib.sha256(data).hexdigest()
    put(tmp_path, CONFIG, data)
    put(tmp_path, RECEIPT, encoded(current))
    with pytest.raises(IssuerError):
        load_config(tmp_path)


@pytest.mark.parametrize("case", ["stale-pair", "partial", "missing-receipt", "config-digest",
                                 "control-url", "endpoint", "invalid-receipt"])
def test_config_requires_current_ready_complete_pair(store: HomeActivationStore, tmp_path: Path, case: str) -> None:
    put_pair(tmp_path)
    if case == "partial": put(tmp_path, RECEIPT, encoded(receipt_record("joined")))
    if case == "missing-receipt": (tmp_path / RECEIPT).unlink()
    if case == "config-digest": (tmp_path / CONFIG).write_bytes(encoded(config_record()) + b" ")
    if case == "invalid-receipt": (tmp_path / RECEIPT).write_bytes(b"{}")
    if case in ("control-url", "endpoint"):
        config = config_record()
        config["control_url" if case == "control-url" else "home_endpoint"] = "https://arbitrary.invalid"
        put(tmp_path, CONFIG, encoded(config))
    with pytest.raises(IssuerError):
        load_config(tmp_path, 8 if case == "stale-pair" else 7)


def test_config_read_is_frozen_effect_free_and_redacted(store: HomeActivationStore, tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str]) -> None:
    put_pair(tmp_path)
    journal = tmp_path / "var/lib/happyranch-remote-access/home-issuer"
    journal.mkdir(mode=0o700)
    (journal / "invite-issuer.json").write_bytes(b"synthetic-journal-canary")
    before = sorted((str(path.relative_to(tmp_path)), path.read_bytes()) for path in tmp_path.rglob("*") if path.is_file())
    def no_effect(*args: object, **kwargs: object) -> None:
        pytest.fail("reader attempted a write, fsync, directory creation or service command")
    for name in ("write", "fsync", "mkdir", "rename", "unlink", "fchown", "fchmod"):
        monkeypatch.setattr(activation.os, name, no_effect)
    monkeypatch.setattr(activation.ManagedInstaller, "_rename_provision_entry", no_effect)
    result = load_config(tmp_path)
    assert result.home_endpoint == "100.64.0.10:443"
    assert result.activation_generation == 7
    with pytest.raises(FrozenInstanceError): result.home_endpoint = "100.64.0.11:443"
    after = sorted((str(path.relative_to(tmp_path)), path.read_bytes()) for path in tmp_path.rglob("*") if path.is_file())
    assert after == before
    assert repr(result) == "HomeInviteConfigRecord(<redacted>)"
    assert repr(receipt("ready")) == "HomeActivationReceipt(<redacted>)"
    assert repr(store) == "HomeActivationStore(<redacted>)"
    assert str(IssuerError("synthetic-private-canary")) == "state"
    assert str(HomeActivationError("synthetic-private-canary")) == "record"
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("kind", ["receipt", "config"])
def test_record_size_maximum_is_inclusive(kind: str) -> None:
    data = encoded(receipt_record() if kind == "receipt" else config_record())
    parser = HomeActivationReceipt.parse if kind == "receipt" else HomeInviteConfigRecord.parse
    assert parser(data + b" " * (16384 - len(data)))
    with pytest.raises((HomeActivationError, IssuerError)):
        parser(data + b" " * (16385 - len(data)))
