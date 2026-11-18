"""Standalone B5 temp-root checks; no host systemd or activation caller.

Run: uv run --frozen pytest scripts/test_home_unit_gate.py -q
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from runtime.remote_access import home_activation as activation
from runtime.remote_access.home_unit_gate import HomeUnitGate, HomeUnitGateError
from runtime.remote_access.systemd_unit import SidecarUnitSpec, render_headscale_unit, render_sidecar_unit
from scripts.test_headscale_custody import EXPECTED_UNIT
from scripts.test_home_activation import receipt_record

UNIT = "/etc/systemd/system/happyranch-headscale.service"
LINK = "/etc/systemd/system/happyranch-managed.target.wants/happyranch-headscale.service"
RECEIPT = "/var/lib/happyranch-remote-access/home-activation.json"
PHASES = ("prepared", "activating", "joined", "ready", "failed_uncertain")
SIDECAR_DIGESTS = {
    False: "5cfef118da7b32c67a6c585885971660eef7ca2e5de3f4bd5050036bd34d551c",
    True: "13d9875cd6d98a3c4d45e565e878d5f8ae48dab677496d365f2330dbd4a81c5d",
}
LOAD_ROOTS = (
    "etc/systemd/system.control", "run/systemd/system.control", "run/systemd/transient",
    "run/systemd/generator.early", "etc/systemd/system", "etc/systemd/system.attached",
    "run/systemd/system", "run/systemd/system.attached", "run/systemd/generator",
    "usr/local/lib/systemd/system", "usr/lib/systemd/system", "run/systemd/generator.late",
    "lib/systemd/system",
)


def directory(root: Path, path: Path) -> None:
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        current.mkdir(exist_ok=True, mode=0o755)


def put(root: Path, path: str, data: bytes, mode: int = 0o644) -> Path:
    target = root / path.lstrip("/")
    directory(root, target.parent)
    target.write_bytes(data)
    target.chmod(mode)
    return target


def link(root: Path, path: str = LINK, target: str = UNIT) -> Path:
    result = root / path.lstrip("/")
    directory(root, result.parent)
    result.symlink_to(target)
    return result


def record(root: Path, phase: str = "prepared", **updates: object) -> Path:
    value = receipt_record(phase)
    value["member_digests"][UNIT] = hashlib.sha256(render_headscale_unit(home_transport=True).encode()).hexdigest()
    value.update(updates)
    path = put(root, RECEIPT, json.dumps(value).encode(), 0o600)
    path.parent.chmod(0o700)
    return path


@pytest.fixture
def gate(tmp_path: Path) -> HomeUnitGate:
    return HomeUnitGate(root=tmp_path, root_owner=(os.getuid(), os.getgid()), expected_release_uid=1000,
                        expected_home_id="home", expected_control_plane_id="control", expected_connector_id="connector",
                        expected_generation=7)


def test_exact_rendered_bytes() -> None:
    assert render_headscale_unit() == render_headscale_unit(home_transport=False) == EXPECTED_UNIT
    expected = EXPECTED_UNIT.replace("After=network.target\n", "After=network.target\nBefore=happyranch-tsnet-sidecar.service\n")
    expected += "\n[Install]\nWantedBy=happyranch-managed.target\n"
    assert render_headscale_unit(home_transport=True) == expected
    # Golden hashes of both sidecar renderings at base e50ef0ac; A4 stays exact.
    for home, digest in SIDECAR_DIGESTS.items():
        assert hashlib.sha256(render_sidecar_unit(SidecarUnitSpec(home_transport=home)).encode()).hexdigest() == digest


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("enabled", [False, True])
def test_current_receipt_admits_every_phase(gate: HomeUnitGate, tmp_path: Path, phase: str, enabled: bool) -> None:
    put(tmp_path, UNIT, render_headscale_unit(home_transport=True).encode())
    record(tmp_path, phase)
    if enabled:
        link(tmp_path)
    gate.inspect()
    if enabled:
        assert os.readlink(tmp_path / LINK.lstrip("/")) == UNIT


@pytest.mark.parametrize("case", [
    "absent", "old", "future", "uid", "home", "control", "connector", "parse", "duplicate", "extra",
    "missing-digests", "missing-ownership", "unrecorded-link", "wrong-recorded-target", "pending", "symlink",
    "hardlink", "mode", "fifo", "oversized", "parent-alias",
])
def test_without_current_receipt_only_inert_no_link(gate: HomeUnitGate, tmp_path: Path, case: str) -> None:
    path = record(tmp_path)
    value = json.loads(path.read_bytes())
    if case == "old": value["activation_generation"] = 6
    if case == "future": value["activation_generation"] = 8
    if case == "uid": value["release_uid"] = 1001
    if case in {"home", "control", "connector"}:
        value[{"home": "home_id", "control": "control_plane_id", "connector": "connector_id"}[case]] = "other"
    if case == "extra": value["extra"] = "synthetic-private-canary"
    if case == "missing-digests": value.pop("member_digests")
    if case == "missing-ownership": value.pop("ownership")
    if case == "unrecorded-link": value["ownership"]["links"] = {}
    if case == "wrong-recorded-target": value["ownership"]["links"][LINK] = "../happyranch-headscale.service"
    path.write_bytes(json.dumps(value).encode())
    if case == "absent": path.unlink()
    if case == "parse": path.write_bytes(b"invalid synthetic-private-canary")
    if case == "duplicate": path.write_bytes(b'{"phase":"prepared",' + path.read_bytes()[1:])
    if case == "pending": path.with_name(".home-activation.json.pending").write_bytes(b"pending")
    if case == "symlink":
        path.rename(path.with_name("retained"))
        path.symlink_to("retained")
    if case == "hardlink": os.link(path, path.with_name("retained"))
    if case == "mode": path.chmod(0o644)
    if case == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    if case == "oversized": path.write_bytes(b" " * 16385)
    if case == "parent-alias":
        path.parent.rename(path.parent.with_name("retained"))
        path.parent.symlink_to("retained", target_is_directory=True)
    unit = put(tmp_path, UNIT, EXPECTED_UNIT.encode())
    gate.inspect()
    unit.write_bytes(render_headscale_unit(home_transport=True).encode())
    with pytest.raises(HomeUnitGateError, match="^home-unit-inventory$"):
        gate.inspect()
    unit.write_bytes(EXPECTED_UNIT.encode())
    link(tmp_path)
    calls = []
    with pytest.raises(HomeUnitGateError):
        gate.disable_owned_link(lambda: calls.append("disabled"))
    assert calls == []


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("case", ["digest", "foreign-bytes"])
def test_receipt_digest_and_exact_variant(gate: HomeUnitGate, tmp_path: Path, phase: str, case: str) -> None:
    data = render_headscale_unit(home_transport=True).encode() if case == "digest" else b"[Service]\nExecStart=/foreign\n"
    put(tmp_path, UNIT, data)
    value = receipt_record(phase)
    value["member_digests"][UNIT] = "0" * 64 if case == "digest" else hashlib.sha256(data).hexdigest()
    record(tmp_path, phase, member_digests=value["member_digests"])
    with pytest.raises(HomeUnitGateError):
        gate.inspect()


@pytest.mark.parametrize("base", LOAD_ROOTS)
@pytest.mark.parametrize("case", ["fragment", "dropin", "prefix-dropin", "type-dropin", "target", "reference",
                                  "dependency-dropin", "alias", "extra-link", "same-stem", "oversized"])
def test_foreign_policy_still_refuses(gate: HomeUnitGate, tmp_path: Path, base: str, case: str) -> None:
    record(tmp_path)
    put(tmp_path, UNIT, render_headscale_unit(home_transport=True).encode())
    link(tmp_path)
    gate.inspect()
    prefix = "/" + base + "/"
    if case == "fragment":
        # Even exact home bytes at another load root have no exclusion.
        put(tmp_path, prefix + "happyranch-headscale.service", render_headscale_unit(home_transport=True).encode())
        if base == "etc/systemd/system":
            # The owned fragment is legal; use its pending peer for this root.
            put(tmp_path, prefix + ".happyranch-headscale.service.pending", b"pending")
    elif case in {"dropin", "prefix-dropin", "type-dropin"}:
        name = {"dropin": "happyranch-headscale.service.d", "prefix-dropin": "happyranch-.service.d",
                "type-dropin": "service.d"}[case]
        directory(tmp_path, tmp_path / base / name)
    elif case in {"alias", "extra-link"}:
        link(tmp_path, prefix + ("alias.service" if case == "alias" else "multi-user.target.wants/happyranch-headscale.service"))
    elif case == "same-stem":
        put(tmp_path, prefix + "happyranch-headscale.timer", b"[Timer]\nOnBootSec=1s\n")
    else:
        name = "operator.service.d/override.conf" if case == "dependency-dropin" else "operator.target"
        data = b"[Unit]\nWants=happyranch-headscale.service\n"
        if case == "reference":
            name, data = "operator.service", b"[Unit]\nDescription=happyranch-headscale diagnostic\n"
        if case == "oversized": data = b"#" * 65537
        put(tmp_path, prefix + name, data)
    with pytest.raises(HomeUnitGateError):
        gate.inspect()


@pytest.mark.parametrize("base", ["usr/share/dbus-1/system-services", "usr/local/share/dbus-1/system-services",
                                  "etc/dbus-1/system-services"])
def test_dbus_literal_reference_refuses(gate: HomeUnitGate, tmp_path: Path, base: str) -> None:
    record(tmp_path)
    put(tmp_path, UNIT, render_headscale_unit(home_transport=True).encode())
    gate.inspect()
    put(tmp_path, "/" + base + "/operator-activation", b"[D-BUS Service]\nSystemdService=happyranch-headscale.service\n")
    with pytest.raises(HomeUnitGateError):
        gate.inspect()


@pytest.mark.parametrize("case", ["relative", "wrong", "manual", "file", "directory", "fifo", "parent-alias"])
def test_only_exact_owned_link(gate: HomeUnitGate, tmp_path: Path, case: str) -> None:
    put(tmp_path, UNIT, EXPECTED_UNIT.encode())
    if case != "manual": record(tmp_path)
    path = tmp_path / LINK.lstrip("/")
    if case in {"file", "directory", "fifo"}:
        directory(tmp_path, path.parent)
        if case == "file": put(tmp_path, LINK, b"unrelated")
        if case == "directory": path.mkdir(mode=0o755)
        if case == "fifo": os.mkfifo(path, 0o600)
    else:
        link(tmp_path, target="../happyranch-headscale.service" if case == "relative" else "/foreign.service" if case == "wrong" else UNIT)
        if case == "parent-alias":
            retained = tmp_path / "retained-links"
            path.parent.rename(retained)
            path.parent.symlink_to(retained, target_is_directory=True)
    with pytest.raises(HomeUnitGateError):
        gate.inspect()


@pytest.mark.parametrize("case", ["symlink", "hardlink", "mode", "fifo", "unsafe-directory"])
def test_fragment_custody_refuses(gate: HomeUnitGate, tmp_path: Path, case: str) -> None:
    record(tmp_path)
    path = put(tmp_path, UNIT, render_headscale_unit(home_transport=True).encode())
    if case == "symlink":
        path.rename(path.with_name("retained"))
        path.symlink_to("retained")
    if case == "hardlink": os.link(path, path.with_name("retained"))
    if case == "mode": path.chmod(0o666)
    if case == "fifo":
        path.unlink()
        os.mkfifo(path, 0o644)
    if case == "unsafe-directory": path.parent.chmod(0o777)
    with pytest.raises(HomeUnitGateError):
        gate.inspect()


@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("effect", ["remove", "persist", "wrong-target", "file", "extra-link", "receipt", "raise"])
def test_cleanup_reverifies_absence(gate: HomeUnitGate, tmp_path: Path, phase: str, effect: str) -> None:
    record(tmp_path, phase)
    put(tmp_path, UNIT, render_headscale_unit(home_transport=True).encode())
    path = link(tmp_path)
    calls = []
    def disable() -> None:
        calls.append("disable")
        if effect == "raise": raise RuntimeError("synthetic-private-canary")
        if effect == "persist": return
        path.unlink()
        if effect == "wrong-target": path.symlink_to("/foreign.service")
        if effect == "file": path.write_bytes(b"unrelated")
        if effect == "extra-link": link(tmp_path, "/run/systemd/system/other.target.wants/happyranch-headscale.service")
        if effect == "receipt": record(tmp_path, phase, operation_id="d" * 32)
    if effect == "remove":
        gate.disable_owned_link(disable)
        assert not path.is_symlink() and not path.exists()
        assert (tmp_path / RECEIPT.lstrip("/")).is_file()
    else:
        with pytest.raises(HomeUnitGateError, match="^home-unit-inventory$"):
            gate.disable_owned_link(disable)
    assert calls == ["disable"]


@pytest.mark.parametrize("receipted", [False, True])
def test_absent_link_does_not_disable(gate: HomeUnitGate, tmp_path: Path, receipted: bool) -> None:
    put(tmp_path, UNIT, EXPECTED_UNIT.encode())
    if receipted: record(tmp_path)
    calls = []
    gate.disable_owned_link(lambda: calls.append("disable"))
    assert calls == []


def test_changed_receipt_during_inventory_refuses(gate: HomeUnitGate, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    record(tmp_path)
    put(tmp_path, UNIT, render_headscale_unit(home_transport=True).encode())
    put(tmp_path, "/etc/systemd/system/other.service", b"[Unit]\nDescription=unrelated\n")
    original = gate._foreign_file
    def changed(parent: int, name: str, named: os.stat_result) -> None:
        original(parent, name, named)
        record(tmp_path, activation_generation=8)
    monkeypatch.setattr(gate, "_foreign_file", changed)
    with pytest.raises(HomeUnitGateError):
        gate.inspect()


@pytest.mark.parametrize("case", ["depth", "entries"])
def test_inventory_bounds(gate: HomeUnitGate, tmp_path: Path, case: str) -> None:
    base = tmp_path / "etc/systemd/system"
    directory(tmp_path, base)
    if case == "depth":
        parent = base
        for _ in range(32):
            parent /= "d"
            parent.mkdir(mode=0o755)
        gate.inspect()
        (parent / "d").mkdir(mode=0o755)
    else:
        for index in range(65536):
            (base / str(index)).touch()
        gate.inspect()
        (base / "overflow").touch()
    with pytest.raises(HomeUnitGateError):
        gate.inspect()


@pytest.mark.parametrize("alias", ["usr/lib", "/usr/lib", "foreign-lib"])
def test_merged_usr_alias(gate: HomeUnitGate, tmp_path: Path, alias: str) -> None:
    (tmp_path / "lib").symlink_to(alias)
    put(tmp_path, "/usr/lib/systemd/system/unrelated.service", b"[Unit]\nDescription=unrelated\n")
    if alias == "foreign-lib":
        with pytest.raises(HomeUnitGateError): gate.inspect()
    else:
        gate.inspect()
