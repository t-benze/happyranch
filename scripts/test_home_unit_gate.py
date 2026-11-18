"""Unmounted B5 checks on real temp-root fragments/links; no systemd calls.

Run: uv run --frozen pytest scripts/test_home_unit_gate.py -q
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from runtime.remote_access.home_activation import ACTIVATION_PATH, OWNED_LINK, OWNED_LINK_TARGET
from runtime.remote_access.home_unit_gate import HomeUnitGate, HomeUnitGateError
from runtime.remote_access.installer import ManagedInstaller, _HEADSCALE_DBUS_DIRS, _HEADSCALE_UNIT_DIRS
from runtime.remote_access.headscale_service import HeadscaleServiceError
from runtime.remote_access.systemd_unit import render_headscale_unit
from scripts.test_headscale_custody import EXPECTED_UNIT
from scripts.test_home_activation import encoded, receipt_record

HOME_UNIT = EXPECTED_UNIT.replace(
    "After=network.target\n", "After=network.target\nBefore=happyranch-tsnet-sidecar.service\n"
) + "\n[Install]\nWantedBy=happyranch-managed.target\n"
PHASES = ("prepared", "activating", "joined", "ready", "failed_uncertain")


def directories(root: Path, path: Path) -> None:
    for directory in reversed((path, *path.parents)):
        if directory.is_relative_to(root) and directory != root:
            directory.mkdir(exist_ok=True, mode=0o755)


def put(root: Path, absolute: str, data: bytes, mode: int = 0o644) -> Path:
    path = root / absolute.lstrip("/")
    directories(root, path.parent)
    path.write_bytes(data)
    path.chmod(mode)
    return path


def write_receipt(root: Path, phase: str = "ready", **changes: object) -> Path:
    record = receipt_record(phase)
    record["member_digests"][OWNED_LINK_TARGET] = hashlib.sha256(HOME_UNIT.encode()).hexdigest()
    record.update(changes)
    path = put(root, ACTIVATION_PATH, encoded(record), 0o600)
    path.parent.chmod(0o700)
    return path


def link(root: Path, target: str = OWNED_LINK_TARGET, absolute: str = OWNED_LINK) -> Path:
    path = root / absolute.lstrip("/")
    directories(root, path.parent)
    path.symlink_to(target)
    return path


@pytest.fixture
def gate(tmp_path: Path) -> HomeUnitGate:
    return HomeUnitGate(root=tmp_path, root_owner=(os.getuid(), os.getgid()),
                        release_uid=1000, home_id="home", control_plane_id="control",
                        connector_id="connector", expected_generation=7)


def test_rendered_bytes() -> None:
    assert render_headscale_unit() == EXPECTED_UNIT
    assert render_headscale_unit(home_transport=False) == EXPECTED_UNIT
    assert render_headscale_unit(home_transport=True) == HOME_UNIT


@pytest.mark.parametrize("phase", PHASES)
def test_every_closed_phase_admits_home_and_owned_link(gate: HomeUnitGate, tmp_path: Path, phase: str) -> None:
    put(tmp_path, OWNED_LINK_TARGET, HOME_UNIT.encode())
    write_receipt(tmp_path, phase)
    owned = link(tmp_path)
    assert gate.verify().phase == phase
    assert os.readlink(owned) == OWNED_LINK_TARGET


@pytest.mark.parametrize("case", ["absent", "old", "future", "release_uid", "home_id", "control_plane_id",
    "connector_id", "pending", "invalid", "duplicate", "digest-member", "ownership", "mode", "symlink",
    "hardlink", "directory", "fifo", "parent-mode", "parent-symlink", "unreadable"])
def test_invalid_receipt_falls_back_only_to_inert(gate: HomeUnitGate, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    path = write_receipt(tmp_path)
    if case == "absent": path.unlink()
    if case in ("old", "future"):
        write_receipt(tmp_path, activation_generation=6 if case == "old" else 8)
    if case in ("release_uid", "home_id", "control_plane_id", "connector_id"):
        write_receipt(tmp_path, **{case: 1001 if case == "release_uid" else "foreign"})
    if case == "pending": path.with_name(".home-activation.json.pending").write_bytes(b"pending")
    if case == "invalid": path.write_bytes(b"{bad}")
    if case == "duplicate": path.write_bytes(b'{"phase":"ready",' + path.read_bytes()[1:])
    if case in ("digest-member", "ownership"):
        record = receipt_record()
        if case == "digest-member": record["member_digests"].pop(OWNED_LINK_TARGET)
        else: record["ownership"]["links"] = {}
        path.write_bytes(encoded(record))
    if case == "mode": path.chmod(0o644)
    if case == "symlink":
        other = path.with_name("foreign")
        path.rename(other)
        path.symlink_to(other)
    if case == "hardlink": os.link(path, path.with_name("other"))
    if case in ("directory", "fifo"):
        path.unlink()
        if case == "directory": path.mkdir()
        else: os.mkfifo(path, 0o600)
    if case == "parent-mode": path.parent.chmod(0o755)
    if case == "parent-symlink":
        other = path.parent.with_name("foreign")
        path.parent.rename(other)
        path.parent.symlink_to(other, target_is_directory=True)
    if case == "unreadable":
        original = os.open
        def unreadable(name, flags, *args, **kwargs):
            if name == path.name and flags & os.O_NOFOLLOW:
                raise PermissionError("private diagnostic")
            return original(name, flags, *args, **kwargs)
        monkeypatch.setattr(os, "open", unreadable)
    put(tmp_path, OWNED_LINK_TARGET, EXPECTED_UNIT.encode())
    assert gate.verify() is None
    put(tmp_path, OWNED_LINK_TARGET, HOME_UNIT.encode())
    with pytest.raises(HomeUnitGateError, match="^foreign-unit$"):
        gate.verify()
    put(tmp_path, OWNED_LINK_TARGET, EXPECTED_UNIT.encode())
    manual = link(tmp_path)
    with pytest.raises(HomeUnitGateError, match="^foreign-unit$"):
        gate.verify()
    assert manual.is_symlink()


@pytest.mark.parametrize("case", ["digest", "foreign-bytes", "inert-reversion", "mode", "hardlink", "symlink"])
def test_receipt_cannot_excuse_fragment_drift(gate: HomeUnitGate, tmp_path: Path, case: str) -> None:
    path = put(tmp_path, OWNED_LINK_TARGET, HOME_UNIT.encode())
    write_receipt(tmp_path)
    if case == "digest":
        record = receipt_record()
        record["member_digests"][OWNED_LINK_TARGET] = "0" * 64
        write_receipt(tmp_path, member_digests=record["member_digests"])
    if case == "foreign-bytes":
        path.write_bytes(HOME_UNIT.encode() + b"#foreign\n")
        record = receipt_record()
        record["member_digests"][OWNED_LINK_TARGET] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_receipt(tmp_path, member_digests=record["member_digests"])
    if case == "inert-reversion": path.write_bytes(EXPECTED_UNIT.encode())
    if case == "mode": path.chmod(0o600)
    if case == "hardlink": os.link(path, path.with_name("copy"))
    if case == "symlink":
        path.rename(path.with_name("copy"))
        path.symlink_to("copy")
    with pytest.raises(HomeUnitGateError): gate.verify()


@pytest.mark.parametrize("case", ["wrong", "relative", "extra", "alias", "requires", "manual"])
def test_only_receipted_exact_link(gate: HomeUnitGate, tmp_path: Path, case: str) -> None:
    put(tmp_path, OWNED_LINK_TARGET, HOME_UNIT.encode() if case != "manual" else EXPECTED_UNIT.encode())
    if case != "manual": write_receipt(tmp_path)
    owned = link(tmp_path, "/foreign.service" if case == "wrong" else
                 "../happyranch-headscale.service" if case == "relative" else OWNED_LINK_TARGET)
    if case in ("extra", "alias", "requires"):
        link(tmp_path, absolute={"extra": "/run/systemd/system/multi-user.target.wants/happyranch-headscale.service",
             "alias": "/etc/systemd/system/operator-alias.service",
             "requires": "/etc/systemd/system/happyranch-managed.target.requires/happyranch-headscale.service"}[case])
    with pytest.raises(HomeUnitGateError): gate.verify()
    assert owned.is_symlink()


@pytest.mark.parametrize("load_path", (*_HEADSCALE_UNIT_DIRS, "lib/systemd/system"))
@pytest.mark.parametrize("case", ["dropin", "prefix", "type", "foreign-fragment", "literal", "reference-dropin"])
def test_foreign_inventory_stays_mandatory(gate: HomeUnitGate, tmp_path: Path, load_path: str, case: str) -> None:
    put(tmp_path, OWNED_LINK_TARGET, HOME_UNIT.encode())
    write_receipt(tmp_path)
    link(tmp_path)
    parent = tmp_path / load_path
    directories(tmp_path, parent)
    if case in ("dropin", "prefix", "type"):
        (parent / {"dropin": "happyranch-headscale.service.d", "prefix": "happyranch-.service.d",
                   "type": "service.d"}[case]).mkdir(mode=0o755)
    elif case == "foreign-fragment":
        if load_path == "etc/systemd/system":
            put(tmp_path, "/" + load_path + "/happyranch-headscale.timer", b"[Timer]\nOnBootSec=1s\n")
        else: put(tmp_path, "/" + load_path + "/happyranch-headscale.service", HOME_UNIT.encode())
    else:
        name = "operator.service.d/override.conf" if case == "reference-dropin" else "operator.target"
        put(tmp_path, "/" + load_path + "/" + name, b"[Unit]\nWants=happyranch-headscale.service\n")
    with pytest.raises(HomeUnitGateError): gate.verify()


@pytest.mark.parametrize("dbus_path", _HEADSCALE_DBUS_DIRS)
def test_dbus_literal_reference_refused(gate: HomeUnitGate, tmp_path: Path, dbus_path: str) -> None:
    write_receipt(tmp_path)
    put(tmp_path, "/" + dbus_path + "/operator", b"SystemdService=happyranch-headscale.service\n")
    with pytest.raises(HomeUnitGateError): gate.verify()


@pytest.mark.parametrize("case", ["parent-link", "deep", "oversized", "lib-link"])
def test_bounded_no_follow_inventory(gate: HomeUnitGate, tmp_path: Path, case: str) -> None:
    write_receipt(tmp_path)
    path = put(tmp_path, OWNED_LINK_TARGET, HOME_UNIT.encode())
    if case == "parent-link":
        path.parent.rename(path.parent.with_name("other"))
        path.parent.symlink_to("other", target_is_directory=True)
    if case == "deep": directories(tmp_path, path.parent / Path(*(["nested"] * 33)))
    if case == "oversized": put(tmp_path, "/etc/systemd/system/operator.service", b"#" * 65537)
    if case == "lib-link": (tmp_path / "lib").symlink_to("foreign")
    with pytest.raises(HomeUnitGateError): gate.verify()


@pytest.mark.parametrize("case", ["removed", "absent", "persists", "foreign", "receipt-change", "no-receipt"])
def test_cleanup_disables_then_reverifies(gate: HomeUnitGate, tmp_path: Path, case: str) -> None:
    put(tmp_path, OWNED_LINK_TARGET, EXPECTED_UNIT.encode() if case == "no-receipt" else HOME_UNIT.encode())
    if case != "no-receipt": write_receipt(tmp_path, "failed_uncertain")
    owned = link(tmp_path) if case != "absent" else tmp_path / OWNED_LINK.lstrip("/")
    if case == "foreign": link(tmp_path, absolute="/etc/systemd/system/operator-alias.service")
    calls = []
    def disable(absolute: str) -> None:
        calls.append(absolute)
        assert absolute == OWNED_LINK
        if case != "persists": owned.unlink(missing_ok=True)
        if case == "receipt-change": write_receipt(tmp_path, activation_generation=8)
    if case in ("removed", "absent"):
        gate.disable_owned_link(disable)
        assert not owned.is_symlink()
    else:
        with pytest.raises(HomeUnitGateError, match="^cleanup-uncertain$"):
            gate.disable_owned_link(disable)
    assert calls == ([] if case in ("foreign", "no-receipt") else [OWNED_LINK])
    if case in ("persists", "foreign", "no-receipt"): assert owned.is_symlink()


def test_existing_scanner_never_uses_receipt(gate: HomeUnitGate, tmp_path: Path) -> None:
    put(tmp_path, OWNED_LINK_TARGET, EXPECTED_UNIT.encode())
    write_receipt(tmp_path)
    io = ManagedInstaller(root=tmp_path, root_owner=(os.getuid(), os.getgid()))
    io._headscale_cleanup_links()
    owned = link(tmp_path)
    with pytest.raises(HeadscaleServiceError): io._headscale_cleanup_links()
    owned.unlink()
    put(tmp_path, OWNED_LINK_TARGET, HOME_UNIT.encode())
    assert gate.verify() is not None
    with pytest.raises(HeadscaleServiceError): io._headscale_cleanup_links()


def test_receipt_change_during_scan_refuses(gate: HomeUnitGate, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    put(tmp_path, OWNED_LINK_TARGET, HOME_UNIT.encode())
    path = write_receipt(tmp_path)
    original = ManagedInstaller._headscale_cleanup_links
    def scan(self, *args, **kwargs):
        original(self, *args, **kwargs)
        path.unlink()
    monkeypatch.setattr(ManagedInstaller, "_headscale_cleanup_links", scan)
    with pytest.raises(HomeUnitGateError): gate.verify()


@pytest.mark.parametrize("case", ["empty", "inert", "home"])
def test_no_link_inventory(gate: HomeUnitGate, tmp_path: Path, case: str) -> None:
    if case != "empty":
        put(tmp_path, OWNED_LINK_TARGET, HOME_UNIT.encode() if case == "home" else EXPECTED_UNIT.encode())
    if case == "home": write_receipt(tmp_path)
    value = gate.verify()
    assert (value is not None) == (case == "home")
