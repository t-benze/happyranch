"""Exact ownership before native home retirement effects, without host services.

Run: uv run --frozen pytest scripts/test_home_retirement_service.py -v --tb=short
This owner-only venue does not claim migration revoke/ack or installed proof.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from runtime.remote_access.home_retirement import RetirementUncertain
from runtime.remote_access.home_retirement_service import OwnedService, ServiceIdentity
from scripts.test_home_retirement import Fixture, SERVICE

FRAGMENT = "etc/systemd/system/" + SERVICE
LINK = "etc/systemd/system/happyranch-managed.target.wants/" + SERVICE
BINARY = "opt/happyranch-desktop/libexec/headscale/headscale"
# Synthetic historical receipt inputs, independent of the production reader.
UNIT_BYTES = b"[Service]\nUser=happyranch-headscale\nExecStart=/opt/happyranch-desktop/libexec/headscale/headscale serve\n"
BINARY_BYTES = b"synthetic-historical-package-executable"


class ServiceFixture(Fixture):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.effect: Callable[[list[str]], None] = lambda argv: None
        self.on_show: Callable[[int], None] = lambda count: None
        self.show_count = 0
        self.fail: str | None = None
        self.lie: str | None = None
        self.pid = 123
        self.ps_output: str | None = None
        self.identity = ServiceIdentity(unit=SERVICE, account="happyranch-headscale",
            uid=os.getuid(), gid=os.getgid(), fragment_sha256=hashlib.sha256(UNIT_BYTES).hexdigest(),
            executable=BINARY, executable_sha256=hashlib.sha256(BINARY_BYTES).hexdigest(), link=LINK)
        self.file(FRAGMENT, UNIT_BYTES, 0o644)
        self.file(BINARY, BINARY_BYTES, 0o755)
        self.directory(str(Path(LINK).parent))
        (root / LINK).symlink_to("/" + FRAGMENT)
        self.properties = dict(Id=SERVICE, Names=SERVICE, FragmentPath=str(root / FRAGMENT),
            DropInPaths="", NeedDaemonReload="no", LoadState="loaded", ActiveState="active",
            SubState="running", UnitFileState="enabled", MainPID=str(self.pid), ControlPID="0",
            ControlGroup="/system.slice/" + SERVICE, Job="", User="happyranch-headscale",
            Group="happyranch-headscale", DynamicUser="no", Transient="no")
        self.file("proc/123/status", (f"Uid:\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\n"
            f"Gid:\t{os.getgid()}\t{os.getgid()}\t{os.getgid()}\t{os.getgid()}\n").encode())
        self.process_start(321)
        self.file("proc/123/cgroup", ("0::/system.slice/" + SERVICE + "\n").encode())
        (root / "proc/123/exe").symlink_to(str(root / BINARY))
        self.owner = OwnedService(self.io, self.identity)

    def process_start(self, value: int) -> None:
        self.file("proc/123/stat", ("123 (headscale worker) " + " ".join(
            ["S", *(["0"] * 18), str(value), "0", "0"]) + "\n").encode())

    def lookup_user(self, name: str) -> SimpleNamespace:
        assert name == "happyranch-headscale"
        return SimpleNamespace(pw_name=name, pw_uid=os.getuid(), pw_gid=os.getgid())

    def lookup_group(self, name: str) -> SimpleNamespace:
        assert name == "happyranch-headscale"
        return SimpleNamespace(gr_name=name, gr_gid=os.getgid(), gr_mem=[])

    def stopped(self) -> None:
        self.properties.update(ActiveState="inactive", SubState="dead", MainPID="0", ControlGroup="")

    def run(self, argv: list[str], **kwargs: object) -> tuple[int, str]:
        self.calls.append(argv)
        if argv[:3] == ["systemctl", "show", SERVICE]:
            self.show_count += 1
            self.on_show(self.show_count)
            keys = [arg.removeprefix("--property=") for arg in argv[3:]]
            return 0, "\n".join(key + "=" + self.properties[key] for key in keys)
        if argv == ["ps", "-eo", "pid=,ruid=,euid=,suid=,fuid="]:
            if self.ps_output is not None:
                return 0, self.ps_output
            return 0, (" ".join([str(self.pid), *([str(os.getuid())] * 4)]) + "\n"
                       if self.properties["MainPID"] != "0" else "")
        assert argv in (["systemctl", "stop", SERVICE], ["systemctl", "disable", SERVICE])
        self.effect(argv)
        if argv[1] == self.fail:
            return 1, "synthetic-private-native-error"
        if argv[1] != self.lie:
            if argv[1] == "stop":
                self.stopped()
            else:
                (self.root / LINK).unlink()
                self.properties["UnitFileState"] = "disabled"
        return 0, ""

    def effects(self) -> list[list[str]]:
        return [argv for argv in self.calls if argv[:2] in (["systemctl", "stop"], ["systemctl", "disable"])]


@pytest.fixture
def service(tmp_path: Path) -> ServiceFixture:
    return ServiceFixture(tmp_path / "root")


def test_owned_stop_receipt_requires_native_and_process_readback(service: ServiceFixture) -> None:
    before = service.snapshot()
    receipt = service.owner.stop_and_disable()
    assert receipt.unit == SERVICE
    assert service.effects() == [["systemctl", "stop", SERVICE], ["systemctl", "disable", SERVICE]]
    service.owner.confirm(receipt)
    after = service.snapshot()
    assert set(before) - set(after) == {LINK}
    for name in set(after) - {str(Path(LINK).parent)}:
        assert after[name] == before[name], name
    service.calls.clear()
    assert OwnedService(service.io, service.identity).stop_and_disable() == receipt
    assert service.effects() == [], "fresh explicit recovery must not replay proven stopped/disabled effects"


@pytest.mark.parametrize("key,value", [
    ("Id", "foreign.service"), ("Names", SERVICE + " alias.service"),
    ("FragmentPath", "/usr/lib/systemd/system/foreign.service"), ("DropInPaths", "/etc/foreign.conf"),
    ("NeedDaemonReload", "yes"), ("LoadState", "not-found"), ("ControlPID", "9"),
    ("Job", "9"), ("User", "root"), ("Group", "root"), ("DynamicUser", "yes"),
    ("Transient", "yes"), ("MainPID", "01bad"), ("ControlGroup", "/foreign"),
    ("UnitFileState", "enabled-runtime"), ("SubState", "start-post"), ("ActiveState", "activating"),
])
def test_foreign_native_identity_has_zero_effects(service: ServiceFixture, key: str, value: str) -> None:
    service.properties[key] = value
    before = service.snapshot()
    with pytest.raises(RetirementUncertain, match="^migration pending$"):
        service.owner.stop_and_disable()
    assert service.effects() == []
    assert service.snapshot() == before


@pytest.mark.parametrize("change", ["fragment-bytes", "fragment-inode", "fragment-link", "fragment-hardlink",
    "binary-bytes", "binary-inode", "binary-mode", "binary-link", "extra-fragment", "socket", "alias", "drop-in",
    "dbus", "link-target", "account-uid", "account-group", "proc-executable", "proc-cgroup", "proc-uid",
    "extra-process", "mixed-process", "malformed-process", "proc-start", "proc-parent-link"])
def test_foreign_files_aliases_accounts_and_processes_are_preserved(
        service: ServiceFixture, change: str, monkeypatch: pytest.MonkeyPatch) -> None:
    root = service.root
    if change == "fragment-bytes":
        service.file(FRAGMENT, b"foreign", 0o644)
    elif change == "fragment-inode":
        # An inode change between independent fresh objects is not foreign by
        # itself. A non-receipted digest is required in this initial-input row.
        (root / FRAGMENT).unlink()
        service.file(FRAGMENT, b"foreign-replacement", 0o644)
    elif change == "fragment-link":
        (root / FRAGMENT).unlink()
        (root / FRAGMENT).symlink_to(root / BINARY)
    elif change == "fragment-hardlink":
        os.link(root / FRAGMENT, root / "outside-keeper")
    elif change == "binary-bytes":
        service.file(BINARY, b"foreign", 0o755)
    elif change == "binary-inode":
        (root / BINARY).unlink()
        service.file(BINARY, b"foreign-replacement", 0o755)
    elif change == "binary-mode":
        (root / BINARY).chmod(0o777)
    elif change == "binary-link":
        (root / BINARY).unlink()
        (root / BINARY).symlink_to(root / FRAGMENT)
    elif change == "extra-fragment":
        service.file("run/systemd/generator/" + SERVICE, UNIT_BYTES, 0o644)
    elif change == "socket":
        service.file("etc/systemd/system/happyranch-headscale.socket", b"[Socket]\nListenStream=9999\n", 0o644)
    elif change == "alias":
        service.file("etc/systemd/system/alias.service", b"placeholder", 0o644).unlink()
        (root / "etc/systemd/system/alias.service").symlink_to("/" + FRAGMENT)
    elif change == "drop-in":
        service.file("etc/systemd/system/" + SERVICE + ".d/override.conf", b"[Service]\n", 0o644)
    elif change == "dbus":
        service.file("usr/share/dbus-1/system-services/foreign.service", SERVICE.encode(), 0o644)
    elif change == "link-target":
        (root / LINK).unlink()
        (root / LINK).symlink_to("/foreign/" + SERVICE)
    elif change == "account-uid":
        monkeypatch.setattr(service.io, "_lookup_user", lambda name: SimpleNamespace(
            pw_name=name, pw_uid=os.getuid() + 1, pw_gid=os.getgid()))
    elif change == "account-group":
        monkeypatch.setattr(service.io, "_lookup_group", lambda name: SimpleNamespace(
            gr_name="foreign", gr_gid=os.getgid()))
    elif change == "proc-executable":
        (root / "proc/123/exe").unlink()
        (root / "proc/123/exe").symlink_to("/foreign/headscale")
    elif change == "proc-cgroup":
        service.file("proc/123/cgroup", b"0::/foreign\n")
    elif change == "proc-uid":
        service.file("proc/123/status", b"Uid:\t0 0 0 0\n")
    elif change == "extra-process":
        service.ps_output = "\n".join(" ".join([str(pid), *([str(os.getuid())] * 4)]) for pid in (123, 999))
    elif change == "mixed-process":
        service.ps_output = " ".join(["123", str(os.getuid()), "0", "0", "0"])
    elif change == "malformed-process":
        service.ps_output = "untrusted process output"
    elif change == "proc-start":
        service.process_start(0)
    else:
        (root / "proc/123").rename(root / "proc/outside")
        (root / "proc/123").symlink_to(root / "proc/outside")
    before = service.snapshot()
    with pytest.raises(RetirementUncertain, match="^migration pending$"):
        service.owner.stop_and_disable()
    assert service.effects() == []
    assert service.snapshot() == before


@pytest.mark.parametrize("boundary,change", [
    (4, "fragment"), (4, "alias"), (4, "process"), (4, "binary"),
    (8, "fragment"), (8, "alias"), (8, "binary"),
])
def test_replacement_immediately_before_stop_or_disable_gets_no_effect(
        service: ServiceFixture, boundary: int, change: str) -> None:
    snapshots = []

    def replace_target(count: int) -> None:
        if count != boundary:
            return
        if change in ("fragment", "binary"):
            name = FRAGMENT if change == "fragment" else BINARY
            (service.root / name).rename(service.root / ("original-" + change))
            service.file(name, UNIT_BYTES if change == "fragment" else BINARY_BYTES,
                         0o644 if change == "fragment" else 0o755)
        elif change == "alias":
            (service.root / "etc/systemd/system/foreign.service").symlink_to("/" + FRAGMENT)
        else:
            service.process_start(999)
        snapshots.append(service.snapshot())

    service.on_show = replace_target
    with pytest.raises(RetirementUncertain, match="^migration pending$"):
        service.owner.stop_and_disable()
    assert snapshots, "the intended last-observation boundary was not reached"
    assert service.effects() == ([] if boundary == 4 else [["systemctl", "stop", SERVICE]])
    assert service.snapshot() == snapshots[0], "replacement received a stop/disable/delete effect"


@pytest.mark.parametrize("operation,kind", [("stop", "error"), ("stop", "lie"),
                                          ("disable", "error"), ("disable", "lie")])
def test_owned_errors_or_misleading_zero_exit_never_produce_receipt(
        service: ServiceFixture, operation: str, kind: str) -> None:
    setattr(service, "fail" if kind == "error" else "lie", operation)
    before = service.snapshot()
    with pytest.raises(RetirementUncertain, match="^migration pending$"):
        service.owner.stop_and_disable()
    expected = [["systemctl", "stop", SERVICE]]
    if operation == "disable":
        expected.append(["systemctl", "disable", SERVICE])
    assert service.effects() == expected
    assert service.snapshot() == before


@pytest.mark.parametrize("change", ["fragment", "alias", "process", "native"])
def test_stop_receipt_is_not_reusable_after_drift(service: ServiceFixture, change: str) -> None:
    receipt = service.owner.stop_and_disable()
    service.calls.clear()
    if change == "fragment":
        (service.root / FRAGMENT).rename(service.root / "original-fragment")
        service.file(FRAGMENT, UNIT_BYTES, 0o644)
    elif change == "alias":
        (service.root / "etc/systemd/system/foreign.service").symlink_to("/" + FRAGMENT)
    elif change == "process":
        service.ps_output = " ".join(["123", *([str(os.getuid())] * 4)])
    else:
        service.properties["NeedDaemonReload"] = "yes"
    before = service.snapshot()
    with pytest.raises(RetirementUncertain, match="^migration pending$"):
        service.owner.confirm(receipt)
    assert service.effects() == []
    assert service.snapshot() == before


def test_service_identity_has_no_arbitrary_target_selector(service: ServiceFixture) -> None:
    with pytest.raises(RetirementUncertain):
        replace(service.identity, unit="foreign.service")
    with pytest.raises(RetirementUncertain):
        replace(service.identity, executable="usr/bin/foreign")
    with pytest.raises(RetirementUncertain):
        replace(service.identity, link="etc/systemd/system/foreign.service")
