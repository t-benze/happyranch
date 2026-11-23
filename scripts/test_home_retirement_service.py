"""Exact ownership before native home retirement effects, without host services.

Run: uv run --frozen pytest scripts/test_home_retirement_service.py -v --tb=short
This owner-only venue does not claim migration revoke/ack or installed proof.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator

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


def test_original_controller_link_replaced_during_stop_never_receives_disable(service: ServiceFixture) -> None:
    snapshots = []
    def replace_during_stop(argv: list[str]) -> None:
        if argv == ['systemctl', 'stop', SERVICE]:
            target = service.root / LINK
            target.rename(service.root.parent / 'original-controller-link')
            target.symlink_to('/' + FRAGMENT)
            snapshots.append(service.snapshot())
    service.effect = replace_during_stop
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        service.owner.stop_and_disable(expected=service.owner.observe())
    assert snapshots and service.snapshot() == snapshots[0]
    assert service.effects() == [['systemctl', 'stop', SERVICE]]


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


MANAGED_UNITS = ('happyranch-connector.service', 'happyranch-tsnet-sidecar.service',
    'happyranch-managed.target', 'happyranch-release-verifier.socket', 'happyranch-release-verifier.service')
MANAGED_LINKS = {
    'happyranch-managed.target': 'etc/systemd/system/multi-user.target.wants/happyranch-managed.target',
    'happyranch-release-verifier.socket': 'etc/systemd/system/happyranch-managed.target.wants/happyranch-release-verifier.socket',
}
PYTHON = 'opt/happyranch-desktop/python/bin/python3.12'
SIDECAR = 'opt/happyranch-desktop/bin/happyranch-tsnet-sidecar'
PACKAGE_REGISTRY = 'var/lib/dpkg/info/happyranch.md5sums'


class ManagedFixture(Fixture):
    """Native owner venue; managed-config lineage is an independent prerequisite.

    Fake NSS/systemd/proc observations and a protected synthetic package registry
    supply inputs. Frozen historical unit bytes are independent of the native
    owner. No fixture computes retirement, revoke, stop receipts or cleanup.
    """
    def __init__(self, root: Path, *, home: bool = True) -> None:
        from runtime.remote_access.home_retirement_inventory import LegacyManagedMembers
        from runtime.remote_access.home_retirement_fence import retirement_fence
        super().__init__(root)
        self.home = home
        self.on_show = lambda count: None
        self.show_count = 0
        self.fail, self.lie, self.ps_extra = None, None, ''
        self.native = {}
        self.managed = LegacyManagedMembers(('tenant', 'home', 'connector'), 'control', 1234,
            (os.getuid(), os.getgid()), home, {}, {}, {})
        frozen = Path(__file__).parent / 'fixtures/home-retirement'
        for name in MANAGED_UNITS:
            source = frozen / ('managed-home-sidecar.service' if home and name == MANAGED_UNITS[1] else 'managed-' + name)
            self.file('etc/systemd/system/' + name, source.read_bytes(), 0o644)
            props = dict(Id=name, Names=name, FragmentPath=str(root / 'etc/systemd/system' / name),
                DropInPaths='', LoadState='loaded', ActiveState='active', SubState='running' if name.endswith('.service') else
                    'listening' if name.endswith('.socket') else 'active',
                UnitFileState='enabled' if name in MANAGED_LINKS else 'disabled' if name != MANAGED_UNITS[4] else 'static',
                Job='', NeedDaemonReload='no')
            if name.endswith('.service'):
                pid = {MANAGED_UNITS[0]: 111, MANAGED_UNITS[1]: 222, MANAGED_UNITS[4]: 333}[name]
                props.update(MainPID=str(pid), ControlPID='0', ControlGroup='/system.slice/' + name,
                    User='root' if name == MANAGED_UNITS[4] else 'happyranch', Group='happyranch', DynamicUser='no', Transient='no')
                self.file(f'proc/{pid}/status', (f'Uid:\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\n'
                    f'Gid:\t{os.getgid()}\t{os.getgid()}\t{os.getgid()}\t{os.getgid()}\n').encode())
                self.file(f'proc/{pid}/stat', (str(pid) + ' (managed worker) ' + ' '.join(['S', *(['0'] * 18), '456', '0']) + '\n').encode())
                self.file(f'proc/{pid}/cgroup', ('0::/system.slice/' + name + '\n').encode())
                binary = SIDECAR if name == MANAGED_UNITS[1] else PYTHON
                (root / f'proc/{pid}/exe').symlink_to(str(root / binary))
                executable = '/opt/happyranch-desktop/python/bin/python3'
                argv = ([executable, '-I', '-m', 'runtime.remote_access.cli', 'run', '--managed', '--config', '/etc/happyranch/connector.json']
                    if name == MANAGED_UNITS[0] else [executable, '-I', '-m', 'runtime.remote_access.release_verifier']
                    if name == MANAGED_UNITS[4] else ['/opt/happyranch-desktop/bin/happyranch-tsnet-sidecar', '--config',
                        '/etc/happyranch/sidecar.json', '--credential-file',
                        '/run/credentials/happyranch-tsnet-sidecar.service/enrollment-key'])
                self.file(f'proc/{pid}/cmdline', ('\0'.join(argv) + '\0').encode())
            self.native[name] = props
        for name, relative in MANAGED_LINKS.items():
            self.directory(str(Path(relative).parent))
            (root / relative).symlink_to('/etc/systemd/system/' + name)
        self.file(PYTHON, b'synthetic-installed-python', 0o755)
        self.file(SIDECAR, b'synthetic-installed-sidecar', 0o755)
        self.file(PACKAGE_REGISTRY, ''.join(hashlib.md5((root / path).read_bytes()).hexdigest() + '  ' + path + '\n'
            for path in (PYTHON, SIDECAR)).encode(), 0o644)
        self.file('proc/sys/kernel/random/boot_id', b'11111111-1111-4111-8111-111111111111\n', 0o444)
        self.directory('var/lib/happyranch-remote-access')
        self.fence_context = retirement_fence(self.io)
        self.fence = self.fence_context.__enter__()

    def close(self) -> None:
        self.fence_context.__exit__(None, None, None)

    def lookup_user(self, name: str) -> SimpleNamespace:
        assert name in ('happyranch', 'root')
        return SimpleNamespace(pw_name=name, pw_uid=os.getuid(), pw_gid=os.getgid())

    def lookup_group(self, name: str) -> SimpleNamespace:
        assert name == 'happyranch'
        return SimpleNamespace(gr_name=name, gr_gid=os.getgid(), gr_mem=[])

    def run(self, argv: list[str], **kwargs: object) -> tuple[int, str]:
        self.calls.append(argv)
        if argv[:2] == ['systemctl', 'show']:
            self.show_count += 1
            self.on_show(self.show_count)
            return 0, '\n'.join(key + '=' + self.native[argv[2]][key] for key in
                (arg.removeprefix('--property=') for arg in argv[3:]))
        if argv[:2] == ['ps', '-eo']:
            return 0, ''.join(' '.join([props['MainPID'], *([str(os.getuid())] * 4)]) + '\n' for props in self.native.values()
                if props.get('MainPID', '0') != '0') + self.ps_extra
        assert argv[:2] in (['systemctl', 'stop'], ['systemctl', 'disable']) and argv[2] in MANAGED_UNITS
        if self.fail == tuple(argv[1:]): return 1, 'synthetic-private-native-failure'
        if self.lie != tuple(argv[1:]):
            props = self.native[argv[2]]
            if argv[1] == 'stop':
                props.update(ActiveState='inactive', SubState='dead')
                if 'MainPID' in props: props.update(MainPID='0', ControlGroup='')
            else:
                (self.root / MANAGED_LINKS[argv[2]]).unlink()
                props['UnitFileState'] = 'disabled'
        return 0, ''

    def effects(self) -> list[list[str]]:
        return [argv for argv in self.calls if argv[:2] in (['systemctl', 'stop'], ['systemctl', 'disable'])]


@pytest.fixture
def managed_service(tmp_path: Path) -> Iterator[ManagedFixture]:
    fixture = ManagedFixture(tmp_path / 'root')
    try:
        yield fixture
    finally:
        fixture.close()


@pytest.mark.parametrize('home', [False, True])
def test_managed_stop_disable_requires_exact_package_process_and_native_readback(managed_service: ManagedFixture, home: bool) -> None:
    from runtime.remote_access.home_retirement_transport import ManagedTransport
    v = managed_service
    if not home:
        v.managed = replace(v.managed, live_sidecar=False)
        (v.root / 'etc/systemd/system' / MANAGED_UNITS[1]).write_bytes((
            Path(__file__).parent / 'fixtures/home-retirement' / ('managed-' + MANAGED_UNITS[1])).read_bytes())
    owner = ManagedTransport(v.io, v.fence, v.managed)
    private_root = v.root / 'var/lib/happyranch-headscale'
    private_root.mkdir(mode=0o700)
    before = v.snapshot()
    observed = owner.observe()
    assert v.snapshot() == before and v.effects() == []
    assert observed.processes == {MANAGED_UNITS[0]: (111, 456), MANAGED_UNITS[1]: (222, 456), MANAGED_UNITS[4]: (333, 456)}
    assert repr(observed) == 'ManagedObservation(<redacted>)'
    receipt = owner.stop_and_disable(observed)
    assert receipt.observation.processes == {MANAGED_UNITS[0]: None, MANAGED_UNITS[1]: None, MANAGED_UNITS[4]: None}
    assert all(dict(props)['ActiveState'] == 'inactive' for props in receipt.observation.properties.values())
    assert all(receipt.observation.artifacts[path] is None for path in MANAGED_LINKS.values())
    assert v.effects() == [['systemctl', 'stop', name] for name in (MANAGED_UNITS[2], MANAGED_UNITS[1], MANAGED_UNITS[0],
        MANAGED_UNITS[3], MANAGED_UNITS[4])] + [['systemctl', 'disable', name] for name in MANAGED_LINKS]
    owner.confirm(receipt)
    count = len(v.effects())
    # Selective private cleanup changes the still-original shared parent's
    # link count/timestamps, not its identity or the managed native members.
    private_root.rmdir()
    assert owner.observe(stopped=True, disabled=True) != receipt.observation
    owner.confirm(receipt)
    assert len(v.effects()) == count
    again = owner.stop_and_disable(owner.observe())
    owner.confirm(again)
    assert len(v.effects()) == count
    assert all((v.root / path).read_bytes() for path in (PYTHON, SIDECAR, PACKAGE_REGISTRY))
    assert all((v.root / 'etc/systemd/system' / name).exists() for name in MANAGED_UNITS)


@pytest.mark.parametrize('damage', ['binary', 'registry-digest', 'registry-missing', 'registry-duplicate', 'registry-mode',
    'unit', 'alias', 'dropin', 'same-stem', 'shared-consumer', 'pid-reuse', 'executable', 'argv', 'cgroup',
    'extra-process', 'native-name', 'native-path', 'native-reload', 'native-control-pid', 'nss-user', 'nss-group',
    'missing-unit', 'unit-mode', 'registry-symlink', 'binary-hardlink', 'binary-mode', 'missing-boot', 'boot-mode',
    'boot-change', 'parent-reuse', 'link-reuse'])
def test_foreign_managed_ownership_has_zero_native_or_file_effects(managed_service: ManagedFixture, damage: str) -> None:
    from runtime.remote_access.home_retirement_transport import ManagedTransport
    v = managed_service
    owner = ManagedTransport(v.io, v.fence, v.managed)
    expected = owner.observe()
    if damage == 'binary': (v.root / SIDECAR).write_bytes(b'foreign-candidate-self-hash')
    elif damage.startswith('registry-'):
        target = v.root / PACKAGE_REGISTRY
        if damage == 'registry-missing': target.unlink()
        elif damage == 'registry-symlink':
            target.rename(v.root.parent / 'retained-registry')
            target.symlink_to(v.root.parent / 'retained-registry')
        elif damage == 'registry-mode': target.chmod(0o666)
        elif damage == 'registry-duplicate': target.write_bytes(target.read_bytes() * 2)
        else: target.write_bytes(target.read_bytes().replace(hashlib.md5((v.root / SIDECAR).read_bytes()).hexdigest().encode(), b'0' * 32))
    elif damage == 'unit': (v.root / 'etc/systemd/system' / MANAGED_UNITS[0]).write_bytes(b'[Service]\nUser=foreign\n')
    elif damage == 'missing-unit': (v.root / 'etc/systemd/system' / MANAGED_UNITS[0]).unlink()
    elif damage == 'unit-mode': (v.root / 'etc/systemd/system' / MANAGED_UNITS[0]).chmod(0o666)
    elif damage == 'binary-hardlink': os.link(v.root / SIDECAR, v.root / 'external-binary')
    elif damage == 'binary-mode': (v.root / SIDECAR).chmod(0o777)
    elif damage in ('missing-boot', 'boot-mode', 'boot-change'):
        path = v.root / 'proc/sys/kernel/random/boot_id'
        if damage == 'missing-boot': path.unlink()
        elif damage == 'boot-mode': path.chmod(0o644)
        else:
            path.chmod(0o600)
            path.write_bytes(b'22222222-2222-4222-8222-222222222222\n')
            path.chmod(0o444)
    elif damage == 'parent-reuse':
        (v.root / 'etc/systemd').rename(v.root.parent / 'retained-systemd')
        (v.root / 'etc/systemd').mkdir(mode=0o755)
        (v.root.parent / 'retained-systemd/system').rename(v.root / 'etc/systemd/system')
    elif damage == 'link-reuse':
        path = v.root / MANAGED_LINKS[MANAGED_UNITS[2]]
        path.rename(v.root.parent / 'retained-link')
        path.symlink_to('/etc/systemd/system/' + MANAGED_UNITS[2])
    elif damage == 'nss-user':
        v.io._lookup_user = lambda name: SimpleNamespace(pw_name=name, pw_uid=os.getuid() + 1, pw_gid=os.getgid())
    elif damage == 'nss-group':
        v.io._lookup_group = lambda name: SimpleNamespace(gr_name=name, gr_gid=os.getgid() + 1, gr_mem=[])
    elif damage == 'alias': (v.root / 'etc/systemd/system/foreign.service').symlink_to('/etc/systemd/system/' + MANAGED_UNITS[0])
    elif damage == 'dropin': v.file('run/systemd/system/service.d/foreign.conf', b'[Service]\nEnvironment=FOREIGN=1\n', 0o644)
    elif damage == 'same-stem': v.file('etc/systemd/system/happyranch-connector.timer', b'[Timer]\nOnBootSec=1\n', 0o644)
    elif damage == 'shared-consumer': v.file('etc/systemd/system/foreign.service', b'[Service]\nUser=happyranch\n', 0o644)
    elif damage == 'pid-reuse':
        target = v.root / 'proc/111/stat'
        target.write_bytes(target.read_bytes().replace(b'456', b'457'))
    elif damage == 'executable':
        target = v.root / 'proc/111/exe'
        target.unlink()
        target.symlink_to(str(v.root / SIDECAR))
    elif damage == 'argv': (v.root / 'proc/111/cmdline').write_bytes(b'foreign-program\0')
    elif damage == 'cgroup': (v.root / 'proc/222/cgroup').write_bytes(b'0::/foreign.scope\n')
    elif damage == 'extra-process': v.ps_extra = f'444 {os.getuid()} {os.getuid()} {os.getuid()} {os.getuid()}\n'
    else:
        key = {'native-name': 'Names', 'native-path': 'FragmentPath', 'native-reload': 'NeedDaemonReload',
               'native-control-pid': 'ControlPID'}[damage]
        v.native[MANAGED_UNITS[0]][key] = 'foreign' if key != 'ControlPID' else '444'
    before = v.snapshot()
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        owner.stop_and_disable(expected)
    assert v.snapshot() == before and v.effects() == []


@pytest.mark.parametrize('effect', [('stop', MANAGED_UNITS[2]), ('disable', MANAGED_UNITS[2])])
@pytest.mark.parametrize('fault', ['error', 'zero-without-effect'])
def test_managed_native_uncertainty_never_produces_stop_receipt(
        managed_service: ManagedFixture, effect: tuple[str, str], fault: str) -> None:
    from runtime.remote_access.home_retirement_transport import ManagedTransport
    v = managed_service
    if fault == 'error': v.fail = effect
    else: v.lie = effect
    owner = ManagedTransport(v.io, v.fence, v.managed)
    expected = owner.observe()
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        owner.stop_and_disable(expected)
    assert v.effects()[-1] == ['systemctl', *effect]
    assert all((v.root / 'etc/systemd/system' / name).exists() for name in MANAGED_UNITS)
    assert (v.root / PACKAGE_REGISTRY).exists()


@pytest.mark.parametrize('path', [SIDECAR, PACKAGE_REGISTRY, 'etc/systemd/system/' + MANAGED_UNITS[0], MANAGED_LINKS[MANAGED_UNITS[2]]])
def test_managed_original_artifacts_are_rechecked_after_blocking_native_observation(
        managed_service: ManagedFixture, path: str) -> None:
    from runtime.remote_access.home_retirement_transport import ManagedTransport
    v = managed_service
    def swap(count: int) -> None:
        if count == 10:
            target = v.root / path
            saved = v.root.parent / 'preserved-original-native-member'
            target.rename(saved)
            if saved.is_symlink(): target.symlink_to(os.readlink(saved))
            else:
                target.write_bytes(saved.read_bytes())
                target.chmod(0o755 if path == SIDECAR else 0o644)
    v.on_show = swap
    before = len(v.effects())
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        ManagedTransport(v.io, v.fence, v.managed).observe()
    assert len(v.effects()) == before and (v.root.parent / 'preserved-original-native-member').lstat().st_ino > 0


@pytest.mark.parametrize('phase', ['stop', 'disable'])
@pytest.mark.parametrize('path', [SIDECAR, PACKAGE_REGISTRY, 'etc/systemd/system/' + MANAGED_UNITS[0], MANAGED_LINKS[MANAGED_UNITS[2]]])
def test_replacement_before_managed_stop_or_disable_has_zero_effects(
        managed_service: ManagedFixture, monkeypatch: pytest.MonkeyPatch, phase: str, path: str) -> None:
    from runtime.remote_access.home_retirement_transport import ManagedTransport
    v = managed_service
    if phase == 'disable':
        for props in v.native.values():
            props.update(ActiveState='inactive', SubState='dead')
            if 'MainPID' in props: props.update(MainPID='0', ControlGroup='')
    owner = ManagedTransport(v.io, v.fence, v.managed)
    expected = owner.observe()
    observe, changed, observations = owner.observe, [], []
    def replace_after_observation(*args: Any, **kwargs: Any) -> Any:
        result = observe(*args, **kwargs)
        observations.append(True)
        if not changed and (phase == 'stop' and len(observations) == 2 or phase == 'disable' and kwargs.get('stopped')):
            target = v.root / path
            saved = v.root.parent / 'retained-pre-effect-original'
            target.rename(saved)
            if saved.is_symlink(): target.symlink_to(os.readlink(saved))
            else:
                target.write_bytes(saved.read_bytes())
                target.chmod(0o755 if path == SIDECAR else 0o644)
            changed.append(True)
        return result
    monkeypatch.setattr(owner, 'observe', replace_after_observation)
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        owner.stop_and_disable(expected)
    assert changed == [True] and v.effects() == []
    assert (v.root.parent / 'retained-pre-effect-original').lstat().st_ino > 0


@pytest.mark.parametrize('damage', ['fragment', 'registry', 'process', 'boot', 'parent-reuse', 'parent-mode'])
def test_managed_stop_receipt_cannot_authorize_later_changed_state(managed_service: ManagedFixture, damage: str) -> None:
    from runtime.remote_access.home_retirement_transport import ManagedTransport
    v = managed_service
    owner = ManagedTransport(v.io, v.fence, v.managed)
    receipt = owner.stop_and_disable(owner.observe())
    effects = list(v.effects())
    if damage == 'fragment':
        path = v.root / 'etc/systemd/system' / MANAGED_UNITS[0]
        path.rename(v.root.parent / 'retained-fragment')
        path.write_bytes((v.root.parent / 'retained-fragment').read_bytes())
        path.chmod(0o644)
    elif damage == 'registry': (v.root / PACKAGE_REGISTRY).write_bytes(b'unknown-registry')
    elif damage == 'parent-reuse':
        (v.root / 'etc/systemd').rename(v.root.parent / 'retained-systemd')
        (v.root / 'etc/systemd').mkdir(mode=0o755)
        (v.root.parent / 'retained-systemd/system').rename(v.root / 'etc/systemd/system')
    elif damage == 'parent-mode': (v.root / 'etc/systemd').chmod(0o700)
    elif damage == 'boot':
        path = v.root / 'proc/sys/kernel/random/boot_id'
        path.chmod(0o600)
        path.write_bytes(b'22222222-2222-4222-8222-222222222222\n')
        path.chmod(0o444)
    else: v.native[MANAGED_UNITS[0]].update(ActiveState='active', SubState='running', MainPID='111', ControlGroup='/system.slice/' + MANAGED_UNITS[0])
    before = v.snapshot()
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        owner.confirm(receipt)
    assert v.snapshot() == before and v.effects() == effects


@pytest.mark.parametrize('drift', ['pid-start', 'boot', 'resurrection'])
def test_managed_original_process_identity_survives_each_native_effect(
        managed_service: ManagedFixture, monkeypatch: pytest.MonkeyPatch, drift: str) -> None:
    from runtime.remote_access.home_retirement_transport import ManagedTransport
    v = managed_service
    connector = v.native[MANAGED_UNITS[0]]
    if drift == 'resurrection':
        connector.update(ActiveState='inactive', SubState='dead', MainPID='0', ControlGroup='')
    owner = ManagedTransport(v.io, v.fence, v.managed)
    original = owner.observe()
    changed, observe = [], owner.observe
    def after_first_owned_stop(*args, **kwargs):
        result = observe(*args, **kwargs)
        if v.effects() and not changed:
            assert v.effects() == [['systemctl', 'stop', MANAGED_UNITS[2]]]
            if drift == 'pid-start':
                target = v.root / 'proc/111/stat'
                target.write_bytes(target.read_bytes().replace(b'456', b'457'))
            elif drift == 'boot':
                target = v.root / 'proc/sys/kernel/random/boot_id'
                target.chmod(0o644)
                target.write_bytes(b'22222222-2222-4222-8222-222222222222\n')
                target.chmod(0o444)
            else:
                connector.update(ActiveState='active', SubState='running', MainPID='111',
                    ControlGroup='/system.slice/' + MANAGED_UNITS[0])
            changed.append(True)
        return result
    monkeypatch.setattr(owner, 'observe', after_first_owned_stop)
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        owner.stop_and_disable(original)
    assert changed == [True]
    assert v.effects() == [['systemctl', 'stop', MANAGED_UNITS[2]]]
    assert (v.root / PACKAGE_REGISTRY).is_file() and (v.root / PYTHON).is_file()


TRANSPORT_JOURNAL = 'var/lib/happyranch-remote-access/home-retirement-transport.json'


def test_managed_original_native_journal_is_durable_and_never_recaptures_replacements(
        managed_service: ManagedFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.remote_access.home_retirement_transport import ManagedTransport
    from runtime.remote_access.home_retirement_transport_journal import ManagedTransportJournal
    v = managed_service
    owner = ManagedTransport(v.io, v.fence, v.managed)
    syncs, sync = [], os.fsync
    def synchronized(fd):
        syncs.append(os.readlink(f'/proc/self/fd/{fd}'))
        sync(fd)
    with monkeypatch.context() as scoped:
        scoped.setattr(os, 'fsync', synchronized)
        original = ManagedTransportJournal(owner).capture()
    target = v.root / TRANSPORT_JOURNAL
    assert syncs == [str(target), str(target.parent)]
    assert target.stat().st_mode & 0o777 == 0o600 and target.stat().st_nlink == 1
    record = json.loads(target.read_bytes())
    assert record['format'] == 'home-retirement-transport@1'
    assert record['managed']['identity'] == ['tenant', 'home', 'connector']
    assert record['observation']['processes']['happyranch-connector.service'] == [111, 456]
    assert set(record) == {'format', 'directory', 'managed', 'observation'}
    path = v.root / PYTHON
    retained = v.root.parent / 'retained-original-python'
    path.rename(retained)
    path.write_bytes(retained.read_bytes())
    path.chmod(0o755)
    before = v.snapshot()
    with monkeypatch.context() as scoped:
        scoped.setattr(owner, 'observe', lambda **kwargs: pytest.fail('loader recaptured current native state'))
        assert ManagedTransportJournal(owner).load() == original
    assert v.snapshot() == before and v.effects() == []
    assert owner.observe() != original
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        owner.stop_and_disable(original)
    assert v.effects() == [] and v.snapshot() == before


@pytest.mark.parametrize('damage', ['mode', 'hardlink', 'symlink', 'fifo', 'empty', 'oversized', 'torn',
    'duplicate', 'format', 'extra', 'directory', 'identity', 'account-bool', 'path', 'signature-bool',
    'native', 'process', 'boot'])
def test_unknown_managed_native_journal_is_preserved_without_effects(
        managed_service: ManagedFixture, damage: str) -> None:
    from runtime.remote_access.home_retirement_transport import ManagedTransport
    from runtime.remote_access.home_retirement_transport_journal import ManagedTransportJournal
    v = managed_service
    owner = ManagedTransport(v.io, v.fence, v.managed)
    ManagedTransportJournal(owner).capture()
    target = v.root / TRANSPORT_JOURNAL
    record = json.loads(target.read_bytes())
    if damage == 'mode': target.chmod(0o644)
    elif damage == 'hardlink': os.link(target, v.root.parent / 'retained-journal-link')
    elif damage in ('symlink', 'fifo'):
        retained = v.root.parent / 'retained-original-journal'
        target.rename(retained)
        if damage == 'symlink': target.symlink_to(retained)
        else: os.mkfifo(target, 0o600)
    elif damage in ('empty', 'oversized', 'torn', 'duplicate'):
        target.write_bytes({'empty': b'', 'oversized': b'x' * 65537, 'torn': b'{"format":',
            'duplicate': target.read_bytes().replace(b'"format":', b'"format":"foreign","format":', 1)}[damage])
    else:
        if damage == 'format': record['format'] = 'home-retirement-transport@2'
        elif damage == 'extra': record['complete'] = True
        elif damage == 'directory': record['directory'][1] += 1
        elif damage == 'identity': record['managed']['identity'][2] = 'foreign'
        elif damage == 'account-bool': record['managed']['account'][0] = True
        elif damage == 'path': record['observation']['artifacts']['home/release/daemon.token'] = record['observation']['artifacts'][PYTHON]
        elif damage == 'signature-bool': record['observation']['artifacts'][PYTHON][1] = True
        elif damage == 'native': record['observation']['properties'][MANAGED_UNITS[0]].append(['Names', 'foreign'])
        elif damage == 'process': record['observation']['processes'][MANAGED_UNITS[0]][0] += 1
        elif damage == 'boot': record['observation']['boot_id'] = 'unknown'
        target.write_bytes(json.dumps(record, sort_keys=True, separators=(',', ':')).encode() + b'\n')
    before = v.snapshot()
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        ManagedTransportJournal(owner).load()
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        ManagedTransportJournal(owner).capture()
    assert v.snapshot() == before and v.effects() == []


@pytest.mark.parametrize('failure', ['write', 'file-sync', 'parent-sync'])
def test_interrupted_managed_native_publication_keeps_evidence_for_explicit_sync(
        managed_service: ManagedFixture, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    from runtime.remote_access.home_retirement_transport import ManagedTransport
    from runtime.remote_access.home_retirement_transport_journal import ManagedTransportJournal
    v = managed_service
    owner = ManagedTransport(v.io, v.fence, v.managed)
    target = v.root / TRANSPORT_JOURNAL
    sync, write = os.fsync, os.write
    def interrupted(fd):
        if os.readlink(f'/proc/self/fd/{fd}') == str(target if failure == 'file-sync' else target.parent):
            raise OSError('synthetic sync interruption')
        sync(fd)
    with monkeypatch.context() as scoped:
        if failure == 'write': scoped.setattr(os, 'write', lambda fd, data: write(fd, data[:len(data) // 2]))
        else: scoped.setattr(os, 'fsync', interrupted)
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            ManagedTransportJournal(owner).capture()
    assert target.is_file() and v.effects() == []
    before = v.snapshot()
    if failure == 'write':
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            ManagedTransportJournal(owner).load()
    else:
        assert ManagedTransportJournal(owner).load() == owner.observe()
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        ManagedTransportJournal(owner).capture()
    assert v.snapshot() == before and v.effects() == []


@pytest.mark.parametrize('when', ['capture-file', 'capture-parent', 'load-file', 'load-parent'])
def test_managed_native_journal_pins_original_inode_through_both_syncs(
        managed_service: ManagedFixture, monkeypatch: pytest.MonkeyPatch, when: str) -> None:
    from runtime.remote_access.home_retirement_transport import ManagedTransport
    from runtime.remote_access.home_retirement_transport_journal import ManagedTransportJournal
    v = managed_service
    journal = ManagedTransportJournal(ManagedTransport(v.io, v.fence, v.managed))
    target = v.root / TRANSPORT_JOURNAL
    if when.startswith('load'): journal.capture()
    changed, sync = [], os.fsync
    def replace_during_sync(fd):
        sync(fd)
        if not changed and os.readlink(f'/proc/self/fd/{fd}') == str(target if when.endswith('file') else target.parent):
            retained = v.root.parent / 'retained-original-journal'
            target.rename(retained)
            target.write_bytes(retained.read_bytes())
            target.chmod(0o600)
            changed.append(True)
    with monkeypatch.context() as scoped:
        scoped.setattr(os, 'fsync', replace_during_sync)
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            (journal.load if when.startswith('load') else journal.capture)()
    assert changed == [True] and v.effects() == []
    assert target.read_bytes() == (v.root.parent / 'retained-original-journal').read_bytes()


def test_managed_native_journal_rejects_live_record_replacement_and_wrong_binding(
        managed_service: ManagedFixture) -> None:
    from runtime.remote_access.home_retirement_transport import ManagedTransport
    from runtime.remote_access.home_retirement_transport_journal import ManagedTransportJournal
    v = managed_service
    owner = ManagedTransport(v.io, v.fence, v.managed)
    journal = ManagedTransportJournal(owner)
    journal.capture()
    target = v.root / TRANSPORT_JOURNAL
    retained = v.root.parent / 'retained-original-journal'
    target.rename(retained)
    target.write_bytes(retained.read_bytes())
    target.chmod(0o600)
    before = v.snapshot()
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        journal.load()
    foreign = ManagedTransport(v.io, v.fence, replace(v.managed, control_plane_id='foreign'))
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        ManagedTransportJournal(foreign).load()
    assert v.snapshot() == before and v.effects() == []


@pytest.mark.parametrize('drift', ['process', 'executable'])
def test_managed_native_journal_rechecks_original_after_publication(
        managed_service: ManagedFixture, drift: str) -> None:
    from runtime.remote_access.home_retirement_transport import ManagedTransport
    from runtime.remote_access.home_retirement_transport_journal import ManagedTransportJournal
    v = managed_service
    owner = ManagedTransport(v.io, v.fence, v.managed)
    original = owner.observe()
    changed = []
    def after_publication(count):
        if (v.root / TRANSPORT_JOURNAL).exists() and not changed:
            if drift == 'process':
                target = v.root / 'proc/111/stat'
                target.write_bytes(target.read_bytes().replace(b'456', b'457'))
            else:
                target = v.root / PYTHON
                retained = v.root.parent / 'retained-original-python'
                target.rename(retained)
                target.write_bytes(retained.read_bytes())
                target.chmod(0o755)
            changed.append(True)
    v.on_show = after_publication
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        ManagedTransportJournal(owner).capture()
    assert changed == [True] and v.effects() == []
    assert ManagedTransportJournal(owner).load() == original
    assert owner.observe() != original


def test_managed_native_journal_requires_its_live_serialization_domain(
        managed_service: ManagedFixture) -> None:
    from runtime.remote_access.home_retirement_transport import ManagedTransport
    from runtime.remote_access.home_retirement_transport_journal import ManagedTransportJournal
    v = managed_service
    journal = ManagedTransportJournal(ManagedTransport(v.io, v.fence, v.managed))
    journal.capture()
    before = v.snapshot()
    other = ManagedFixture(v.root.parent / 'other-root')
    try:
        journal.fence = other.fence
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            journal.load()
    finally:
        other.close()
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        journal.load()
    assert v.snapshot() == before and v.effects() == [] and other.effects() == []
