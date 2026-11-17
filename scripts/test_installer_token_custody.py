"""Temp-root installer token custody; no host commands or installed services.

Run: uv run --frozen pytest scripts/test_installer_token_custody.py -v --tb=short
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import pwd
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from runtime.remote_access import installer as module
from runtime.remote_access import release_verifier
from runtime.remote_access import cli as connector_cli
from runtime.remote_access import supervisor as supervisor_module
from runtime.remote_access.managed_provider import ManagedProviderConfig
from runtime.remote_access.service_manager import SystemdServiceManager
from runtime.remote_access.supervisor import ConnectorConfig, ConnectorConfigError, ConnectorSupervisor

QUARANTINE_PHASES = ["replacement-quarantine-connector", "replacement-quarantine-sidecar",
                     "replacement-quarantine-policy", "replacement-quarantined"]
QUARANTINE_CONFIGS = ["connector.json", "sidecar.json", "policy.json"]
REMOVALS = [
    ("replacement-remove-trust-state", "var/lib/happyranch-connector/trust-state.json"),
    ("replacement-remove-trust-anchor", "var/lib/happyranch-connector/trust-state.json.anchor"),
    ("replacement-remove-trust-lock", "var/lib/happyranch-connector/trust-state.json.lock"),
    ("replacement-remove-tsnet-state", "var/lib/happyranch-tsnet-sidecar/tailscaled.state"),
    ("replacement-remove-consumed", "var/lib/happyranch-tsnet-sidecar/credential.consumed"),
    ("replacement-remove-tsnet-log-config", "var/lib/happyranch-tsnet-sidecar/tailscaled.log.conf"),
    ("replacement-remove-tsnet-log1", "var/lib/happyranch-tsnet-sidecar/tailscaled.log1.txt"),
    ("replacement-remove-tsnet-log2", "var/lib/happyranch-tsnet-sidecar/tailscaled.log2.txt"),
    ("replacement-remove-enrollment", "etc/happyranch/enrollment.key"),
    ("replacement-remove-enrollment-link", "etc/credstore/happyranch-tsnet-enrollment"),
]
REMOVAL_PHASES = [phase for phase, _ in REMOVALS] + ["replacement-state-removed"]
REMOVAL_BOUNDARY = "^replacement paused after state removal; config/unit removal and fresh staging unavailable$"
CONFIG_REMOVALS = [
    ("replacement-remove-config-connector", "etc/happyranch/.replacement-connector.json"),
    ("replacement-remove-config-sidecar", "etc/happyranch/.replacement-sidecar.json"),
    ("replacement-remove-config-policy", "etc/happyranch/.replacement-policy.json"),
]
CONFIG_REMOVAL_PHASES = [phase for phase, _ in CONFIG_REMOVALS] + ["replacement-configs-removed"]
CONFIG_REMOVAL_BOUNDARY = "^replacement paused after config removal; unit removal and fresh staging unavailable$"
UNIT_REMOVALS = [
    ("replacement-remove-target-link", "etc/systemd/system/multi-user.target.wants/happyranch-managed.target", "link"),
    ("replacement-remove-verifier-link", "etc/systemd/system/happyranch-managed.target.wants/happyranch-release-verifier.socket", "link"),
    *[("replacement-remove-unit-" + label, "etc/systemd/system/" + name, "unit") for label, name in zip(
        ("connector", "sidecar", "target", "verifier-socket", "verifier-service"), module.UNITS)],
    *[("replacement-remove-dropins-" + label, "etc/systemd/system/" + name + ".d", "directory") for label, name in zip(
        ("connector", "sidecar", "target", "verifier-socket", "verifier-service"), module.UNITS)],
    ("replacement-remove-target-wants", "etc/systemd/system/happyranch-managed.target.wants", "directory"),
]
UNIT_REMOVAL_PHASES = [phase for phase, _, _ in UNIT_REMOVALS] + ["replacement-units-removed"]
UNIT_REMOVAL_BOUNDARY = "^replacement paused after unit removal; fresh staging and transaction closure unavailable$"
STAGING_FILES = [("replacement-stage-" + label, name) for label, name in (
    ("connector", "connector.json"), ("sidecar", "sidecar.json"), ("policy", "policy.json"),
    ("token", "daemon.token"), ("provision", "provision.json"))]
STAGING_PHASES = ["replacement-discard-token", "replacement-discard-provision",
                  *[phase for phase, _ in STAGING_FILES], "replacement-staged"]
STAGING_BOUNDARY = "^replacement paused after fresh staging; retry ReplaceFailed to close transaction$"
CLOSURE_PHASES = ["replacement-reload", "replacement-retire-journal"]


class FakeManager:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls: list[tuple] = []

    def _run(self, args: list[str], **kwargs: object) -> tuple[int, str]:
        raise AssertionError("external commands are forbidden")

    def daemon_reload(self) -> None:
        self.calls.append(("reload",))

    def enable(self, unit: str) -> None:
        self.calls.append(("enable", unit))

    def disable(self, unit: str) -> None:
        self.calls.append(("disable", unit))

    def start(self, unit: str, **kwargs: object) -> None:
        self.calls.append(("start", unit))

    def stop(self, unit: str, **kwargs: object) -> None:
        self.calls.append(("stop", unit))

    def show_properties(self, unit: str, properties: tuple[str, ...]) -> dict[str, str]:
        self.calls.append(("show", unit))
        return dict(FragmentPath=str(self.root / "etc/systemd/system" / unit),
                    DropInPaths="", NeedDaemonReload="no", ActiveState="active")


class Fixture:
    def __init__(self, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root, self.home, self.inputs = (folder / name for name in ("root", "release", "inputs"))
        for path in (self.root, self.home, self.inputs):
            path.mkdir(mode=0o700)
        user = pwd.getpwuid(os.getuid())
        self.user = user.pw_name
        owner = (os.getuid(), os.getgid())
        # Same retained test-side passwd-home isolation as test_release_verifier:
        # workspace scratch is beneath the real user's DEV home, not a release home.
        release = list(user)
        release[5] = "/home/installer-release-fixture"
        monkeypatch.setattr(release_verifier.pwd, "getpwuid", lambda uid: pwd.struct_passwd(release))
        service = SimpleNamespace(pw_uid=owner[0], pw_gid=owner[1],
                                  pw_dir="/nonexistent", pw_shell="/usr/sbin/nologin")
        self.manager = FakeManager(self.root)
        self.installer = module.ManagedInstaller(
            root=self.root, root_owner=owner, manager=self.manager,
            lookup_user=lambda name: service if name == "happyranch" else user,
            lookup_group=lambda name: SimpleNamespace(gr_gid=owner[1]),
        )
        config = ConnectorConfig(
            tenant_id="test-tenant", home_id="test-home", connector_id="test-connector", system=True,
            service_user="happyranch", service_group="happyranch",
            daemon_token_path="/etc/happyranch/daemon.token", policy_path="/etc/happyranch/policy.json",
            state_path="/var/lib/happyranch-connector/trust-state.json",
            managed=ManagedProviderConfig(bind_port=18443),
        )
        artifact = json.loads(Path("runtime/remote_access/route_policy.json").read_text())
        policy = dict(schema_version=1, artifact_version=int(artifact["version"]),
                      issued_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
                      max_age_seconds=3600, revision=1, state="active", artifact=artifact)
        sidecar = dict(StateDir="/var/lib/happyranch-tsnet-sidecar", ControlURL="https://fixture.invalid",
                       RoleIdentity="home-sidecar-fixture", ExpectedPeer="fixture-peer", DERPPolicy="private-only",
                       ListenAddr=":443", ConnectorAddr="127.0.0.1:18443")
        for name, value in (("connector", asdict(config)), ("sidecar", sidecar), ("policy", policy)):
            self.write(self.inputs / (name + ".json"), json.dumps(value).encode())
        self.token = self.home / "daemon.token"
        self.write(self.token, b"synthetic-release-token\n")
        self.key = self.inputs / "enrollment.key"
        self.write(self.key, b"synthetic-enrollment-key")

    @staticmethod
    def write(path: Path, data: bytes) -> None:
        path.write_bytes(data)
        path.chmod(0o600)

    def provision(self) -> None:
        self.installer.provision(
            connector_config=self.inputs / "connector.json", sidecar_config=self.inputs / "sidecar.json",
            policy_file=self.inputs / "policy.json", release_user=self.user, data_home=self.home,
        )

    def prepare(self, operation: str) -> None:
        if operation != "provision":
            self.provision()
        if operation == "drift":
            self.installer.install(enrollment_key_file=self.key)
        self.manager.calls.clear()

    def invoke(self, operation: str) -> None:
        if operation == "provision":
            self.provision()
        elif operation == "install":
            self.installer.install(enrollment_key_file=self.key)
        else:
            self.installer.check_drift()

    def inventory(self) -> dict[str, tuple[bytes, int]]:
        return {str(path.relative_to(self.root)): (path.read_bytes(), path.stat().st_mode)
                for path in self.root.rglob("*") if path.is_file()}


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Fixture:
    return Fixture(tmp_path, monkeypatch)


def watch_read(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, effect: Callable[[], None]) -> list[int]:
    """Wrap the real FD reader, mutating only after its actual bytes were read."""
    target = fixture.token.stat()
    fdopen, fstat = os.fdopen, os.fstat
    limits: list[int] = []

    class Reader:
        def __init__(self, stream: object) -> None:
            self.stream = stream

        def __enter__(self) -> Reader:
            return self

        def __exit__(self, *args: object) -> None:
            self.stream.close()

        def read(self, limit: int) -> bytes:
            limits.append(limit)
            data = self.stream.read(limit)
            effect()
            return data

    def open_stream(fd: int, mode: str, **kwargs: object) -> object:
        stream = fdopen(fd, mode, **kwargs)
        info = fstat(fd)
        if mode == "rb" and (info.st_dev, info.st_ino) == (target.st_dev, target.st_ino):
            return Reader(stream)
        return stream

    monkeypatch.setattr(os, "fdopen", open_stream)
    return limits


@pytest.mark.parametrize("operation", ["provision", "install", "drift"])
def test_foreign_owned_0600_token_refuses_before_read_or_effects(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    fixture.prepare(operation)
    before = fixture.inventory()
    target = fixture.token.stat()
    fstat, named_stat = os.fstat, os.stat
    reads = watch_read(fixture, monkeypatch, lambda: None)

    def foreign(info: os.stat_result) -> object:
        if (info.st_dev, info.st_ino) != (target.st_dev, target.st_ino):
            return info
        # No chown/sudo: model a foreign inode's kernel metadata at the existing
        # OS boundary; all bytes, FDs, mode, links and paths remain real.
        values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
        values["st_uid"] = target.st_uid + 1
        return SimpleNamespace(**values)

    monkeypatch.setattr(os, "fstat", lambda fd: foreign(fstat(fd)))
    monkeypatch.setattr(os, "stat", lambda *args, **kwargs: foreign(named_stat(*args, **kwargs)))
    with pytest.raises(module.InstallerError, match="unsafe provisioning file"):
        fixture.invoke(operation)
    assert reads == []
    assert fixture.inventory() == before
    assert fixture.manager.calls == []


@pytest.mark.parametrize("operation", ["provision", "install", "drift"])
@pytest.mark.parametrize("attack", ["parent-swap", "leaf-swap", "mode", "links", "size", "mtime"])
def test_changed_token_read_refuses_without_publication_or_service_calls(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, operation: str, attack: str,
) -> None:
    fixture.prepare(operation)
    before = fixture.inventory()

    def change() -> None:
        if attack == "parent-swap":
            fixture.home.rename(fixture.home.with_name("previous-release"))
            fixture.home.mkdir(mode=0o700)
        elif attack == "leaf-swap":
            fixture.token.rename(fixture.home / "previous-token")
            fixture.write(fixture.token, b"synthetic-substitution")
        elif attack == "mode":
            fixture.token.chmod(0o640)
        elif attack == "links":
            os.link(fixture.token, fixture.home / "extra-link")
        elif attack == "size":
            fixture.token.write_bytes(b"synthetic-grown-token" * 4096)
        else:
            info = fixture.token.stat()
            os.utime(fixture.token, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))

    reads = watch_read(fixture, monkeypatch, change)
    with pytest.raises(module.InstallerError):
        fixture.invoke(operation)
    assert len(reads) == 1  # The intended source FD was actually read.
    assert fixture.inventory() == before
    assert fixture.manager.calls == []


@pytest.mark.parametrize("operation", ["provision", "install", "drift"])
@pytest.mark.parametrize("attack", ["parent", "leaf"])
def test_named_opened_identity_swap_refuses(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, operation: str, attack: str,
) -> None:
    fixture.prepare(operation)
    before = fixture.inventory()
    parent_identity = fixture.home.stat()
    original_open = os.open
    visited = 0
    swapped = False

    def open_file(name: str, flags: int, *args: object, **kwargs: object) -> int:
        nonlocal visited, swapped
        if name == fixture.home.name:
            visited += 1
        # First home walk validates the release record; second opens token parents.
        if not swapped and attack == "parent" and name == fixture.home.name and visited == 2:
            content = fixture.token.read_bytes()
            fixture.home.rename(fixture.home.with_name("previous-release"))
            fixture.home.mkdir(mode=0o700)
            fixture.write(fixture.token, content)
            swapped = True
        elif not swapped and attack == "leaf" and name == fixture.token.name:
            info = os.fstat(kwargs["dir_fd"])
            if (info.st_dev, info.st_ino) == (parent_identity.st_dev, parent_identity.st_ino):
                fixture.token.rename(fixture.home / "previous-token")
                fixture.write(fixture.token, b"synthetic-substitution")
                swapped = True
        return original_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_file)
    with pytest.raises(module.InstallerError):
        fixture.invoke(operation)
    assert swapped
    assert fixture.inventory() == before
    assert fixture.manager.calls == []


@pytest.mark.parametrize("bad", ["empty", "blank", "utf8", "oversize", "mode", "hardlink", "symlink", "parent-symlink", "fifo"])
def test_unsafe_token_never_staged(fixture: Fixture, bad: str) -> None:
    if bad in {"empty", "blank", "utf8", "oversize"}:
        fixture.write(fixture.token, {"empty": b"", "blank": b" \n", "utf8": b"\xff",
                                      "oversize": b"s" * 65537}[bad])
    elif bad == "mode":
        fixture.token.chmod(0o640)
    elif bad == "hardlink":
        os.link(fixture.token, fixture.home / "extra-link")
    elif bad == "parent-symlink":
        moved = fixture.home.with_name("previous-release")
        fixture.home.rename(moved)
        fixture.home.symlink_to(moved, target_is_directory=True)
    else:
        fixture.token.unlink()
        if bad == "symlink":
            fixture.token.symlink_to(fixture.key)
        else:
            os.mkfifo(fixture.token, mode=0o600)
    with pytest.raises(module.InstallerError):
        fixture.provision()
    assert fixture.inventory() == {}
    assert fixture.manager.calls == []


@pytest.mark.parametrize("data", [b"x", b"s" * 65536], ids=["one-byte", "64-KiB"])
def test_valid_token_copied_from_validated_fd(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, data: bytes,
) -> None:
    fixture.write(fixture.token, data)
    reads = watch_read(fixture, monkeypatch, lambda: None)
    publish = fixture.installer._write_provision_file
    reads_at_copy: list[int] = []

    def observe(name: str, content: bytes, owner: tuple[int, int], mode: int) -> None:
        if name == "daemon.token":
            reads_at_copy.append(len(reads))
        publish(name, content, owner, mode)

    monkeypatch.setattr(fixture.installer, "_write_provision_file", observe)
    fixture.provision()
    assert reads_at_copy == [1]  # Copy does not reopen the source.
    assert reads == [65536, 65536]  # Initial read + separate post-provision drift validation.
    copied = fixture.root / "etc/happyranch/daemon.token"
    assert copied.read_bytes() == data
    assert copied.stat().st_mode & 0o7777 == 0o600
    record = json.loads((copied.parent / "provision.json").read_bytes())
    assert record["token_sha256"] == hashlib.sha256(data).hexdigest()
    fixture.installer.install(enrollment_key_file=fixture.key)
    assert ("start", "happyranch-managed.target") in fixture.manager.calls


def test_rotated_source_refuses_without_recopy_or_service_effects(fixture: Fixture) -> None:
    fixture.provision()
    before = fixture.inventory()
    fixture.write(fixture.token, b"synthetic-rotated-token")
    with pytest.raises(module.InstallerError, match="source rotation requires explicit reprovisioning"):
        fixture.installer.install(enrollment_key_file=fixture.key)
    assert fixture.inventory() == before
    assert fixture.manager.calls == []


def publication_parent(fixture: Fixture) -> Path:
    fixture.installer._service_owner = fixture.installer._account(create=False)
    with fixture.installer._directory("etc/happyranch", create=True):
        pass
    return fixture.root / "etc/happyranch"


def record_data(name: str, layout: str = "source") -> bytes:
    digests = {key: hashlib.sha256(b"synthetic-file").hexdigest() for key in (
        "connector.json", "sidecar.json", "policy.json", "release-daemon.json", "daemon.token")}
    if name == "install-transaction.json":
        digests["provision.json"] = hashlib.sha256(b"synthetic-provision").hexdigest()
    return json.dumps(dict(version=1, layout=layout, digests=digests,
                          phase="prepared" if name == "install-transaction.json" else "provisioned")).encode()


def publication_data(name: str) -> bytes:
    return record_data(name) if name in ("provision.json", "install-transaction.json") else b"synthetic-bytes!" * 4096


@pytest.mark.parametrize("name,mode", [
    ("connector.json", 0o640), ("sidecar.json", 0o640), ("policy.json", 0o640),
    ("release-daemon.json", 0o644), ("daemon.token", 0o600), ("provision.json", 0o600),
    ("install-transaction.json", 0o600),
])
def test_provision_publication_is_complete_and_durable(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, name: str, mode: int,
) -> None:
    parent = publication_parent(fixture)
    target = parent / name
    data = publication_data(name)  # Non-record files exercise the exact 64 KiB bound.
    owner = (os.getuid(), os.getgid())
    fsync = os.fsync
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    events: list[str] = []

    def sync(fd: int) -> None:
        if os.fstat(fd).st_mode & 0o170000 == 0o100000:
            assert not target.exists()  # No final name before durable complete bytes.
            events.append("file-fsync")
        else:
            assert target.read_bytes() == data
            events.append("parent-fsync")
        fsync(fd)

    def publish(source_fd: int, source: bytes, destination_fd: int, destination: bytes, flags: int) -> int:
        assert events == ["file-fsync"]
        assert destination == os.fsencode(name)
        assert source_fd == destination_fd
        assert flags == 1  # Linux RENAME_NOREPLACE, not clobbering POSIX rename.
        result = rename(source_fd, source, destination_fd, destination, flags)
        events.append("rename")
        return result

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(renameat2=publish))
    fixture.installer._write_provision_file(name, data, owner, mode)
    assert events == ["file-fsync", "rename", "parent-fsync"]
    assert target.read_bytes() == data
    info = target.stat()
    assert (info.st_uid, info.st_gid, info.st_mode & 0o7777, info.st_nlink) == (*owner, mode, 1)
    assert parent.stat().st_mode & 0o7777 == 0o755
    assert (parent.stat().st_uid, parent.stat().st_gid) == owner
    assert sorted(p.name for p in parent.iterdir()) == [name]
    assert fixture.manager.calls == []


@pytest.mark.parametrize("fault", ["file-fsync", "short-write", "rename"])
@pytest.mark.parametrize("name", ["connector.json", "provision.json", "install-transaction.json"])
def test_failed_provision_write_never_exposes_partial_final_file(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, fault: str, name: str,
) -> None:
    parent = publication_parent(fixture)
    fsync, fdopen = os.fsync, os.fdopen
    reached: list[str] = []

    def sync(fd: int) -> None:
        if fault == "file-fsync" and os.fstat(fd).st_mode & 0o170000 == 0o100000:
            reached.append(fault)
            raise OSError("synthetic write fault")
        fsync(fd)

    class ShortWriter:
        def __init__(self, stream: object) -> None:
            self.stream = stream

        def __enter__(self) -> ShortWriter:
            return self

        def __exit__(self, *args: object) -> None:
            self.stream.close()

        def flush(self) -> None:
            self.stream.flush()

        def write(self, data: bytes) -> int:
            reached.append(fault)
            return self.stream.write(data[:1])

    def open_stream(fd: int, mode: str, **kwargs: object) -> object:
        stream = fdopen(fd, mode, **kwargs)
        return ShortWriter(stream) if fault == "short-write" and mode == "wb" else stream

    def rename(*args: object) -> int:
        reached.append(fault)
        ctypes.set_errno(errno.EIO)
        return -1

    monkeypatch.setattr(os, "fsync", sync)
    monkeypatch.setattr(os, "fdopen", open_stream)
    if fault == "rename":
        monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(renameat2=rename))
    with pytest.raises((module.InstallerError, OSError)):
        fixture.installer._write_provision_file(name, publication_data(name), fixture.installer.root_owner,
                                              0o640 if name == "connector.json" else 0o600)
    assert reached == [fault]
    assert list(parent.iterdir()) == []
    assert fixture.manager.calls == []


@pytest.mark.parametrize("level", ["etc", "happyranch"])
@pytest.mark.parametrize("name", ["connector.json", "provision.json", "install-transaction.json"])
def test_swapped_publication_parent_refuses_before_rename(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, level: str, name: str,
) -> None:
    parent = publication_parent(fixture)
    selected = parent if level == "happyranch" else parent.parent
    moved = selected.with_name(selected.name + "-previous")
    fsync = os.fsync
    swapped = False

    def sync(fd: int) -> None:
        nonlocal swapped
        fsync(fd)
        if not swapped and os.fstat(fd).st_mode & 0o170000 == 0o100000:
            selected.rename(moved)
            selected.mkdir(mode=0o755)
            if level == "etc":
                parent.mkdir(mode=0o755)
            swapped = True

    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(module.InstallerError):
        fixture.installer._write_provision_file(name, publication_data(name), fixture.installer.root_owner,
                                              0o640 if name == "connector.json" else 0o600)
    assert swapped
    assert list(parent.iterdir()) == []
    old_parent = moved if level == "happyranch" else moved / "happyranch"
    assert list(old_parent.iterdir()) == []
    assert fixture.manager.calls == []


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "mode", "oversize", "foreign", "fifo", "safe"])
@pytest.mark.parametrize("entry", ["target", "pending"])
@pytest.mark.parametrize("name", ["policy.json", "provision.json", "install-transaction.json"])
def test_existing_provision_entry_refuses_without_any_publication(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, attack: str, entry: str, name: str,
) -> None:
    parent = publication_parent(fixture)
    target = parent / (name if entry == "target" else f".{name}.pending")
    if attack == "symlink":
        target.symlink_to(fixture.token)
    elif attack == "fifo":
        os.mkfifo(target, mode=0o600)
    else:
        fixture.write(target, b"s" * 65537 if attack == "oversize" else b"synthetic-existing")
        if attack == "hardlink":
            os.link(target, parent / "extra-link")
        elif attack == "mode":
            target.chmod(0o666)
    if attack == "foreign":
        named_stat = os.stat
        inode = target.stat().st_ino

        def foreign(*args: object, **kwargs: object) -> object:
            info = named_stat(*args, **kwargs)
            if info.st_ino != inode:
                return info
            fields = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            return SimpleNamespace(**{**fields, "st_uid": info.st_uid + 1})

        monkeypatch.setattr(os, "stat", foreign)
    before = {p.name: p.lstat() for p in parent.iterdir()}
    source = fixture.token.read_bytes()
    with pytest.raises(module.InstallerError):
        fixture.provision()
    assert {p.name: p.lstat() for p in parent.iterdir()} == before
    assert fixture.token.read_bytes() == source
    assert fixture.manager.calls == []


@pytest.mark.parametrize("attack", ["hardlink", "mode", "oversize", "pending-swap"])
@pytest.mark.parametrize("name", ["connector.json", "provision.json", "install-transaction.json"])
def test_changed_pending_file_refuses_without_overwriting_or_partial_publication(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, attack: str, name: str,
) -> None:
    parent = publication_parent(fixture)
    pending = parent / f".{name}.pending"
    target = parent / name
    fsync = os.fsync
    reached = False

    def sync(fd: int) -> None:
        nonlocal reached
        fsync(fd)
        if reached or os.fstat(fd).st_mode & 0o170000 != 0o100000:
            return
        reached = True
        if attack == "pending-swap":
            pending.rename(parent / "original-pending")
            fixture.write(pending, b"synthetic-substitution")
        elif attack == "hardlink":
            os.link(pending, parent / "extra-link")
        elif attack == "mode":
            pending.chmod(0o666)
        else:
            pending.write_bytes(b"s" * 65537)

    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(module.InstallerError):
        fixture.installer._write_provision_file(name, publication_data(name), fixture.installer.root_owner,
                                              0o640 if name == "connector.json" else 0o600)
    assert reached
    assert not target.exists()
    if attack == "pending-swap":
        assert pending.read_bytes() == b"synthetic-substitution"  # Never delete the substituted entry.
    assert fixture.manager.calls == []


@pytest.mark.parametrize("name,data", [
    ("connector.json", b"s" * 65537), ("../escaped.json", b"{}"),
    ("install-transaction.json", b"{}"), ("connector.json", b""),
], ids=["oversize", "path-escape", "transaction-wrong-mode", "empty"])
def test_unbounded_or_unselectable_publication_refuses(fixture: Fixture, name: str, data: bytes) -> None:
    parent = publication_parent(fixture)
    with pytest.raises(module.InstallerError):
        fixture.installer._write_provision_file(name, data, fixture.installer.root_owner, 0o640)
    assert list(parent.iterdir()) == []
    assert not (parent.parent / "escaped.json").exists()
    assert fixture.manager.calls == []


@pytest.mark.parametrize("name", ["connector.json", "provision.json", "install-transaction.json"])
def test_atomic_publication_never_replaces_racing_target(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, name: str,
) -> None:
    parent = publication_parent(fixture)
    target = parent / name
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    reached = False

    def race(source_fd: int, source: bytes, destination_fd: int, destination: bytes, flags: int) -> int:
        nonlocal reached
        # Arrive after the last lstat, immediately before the real kernel rename.
        fixture.write(target, b"synthetic-racing-target")
        reached = True
        return rename(source_fd, source, destination_fd, destination, flags)

    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: SimpleNamespace(renameat2=race))
    with pytest.raises(module.InstallerError, match="destination appeared"):
        fixture.installer._write_provision_file(name, publication_data(name), fixture.installer.root_owner,
                                              0o640 if name == "connector.json" else 0o600)
    assert reached
    assert target.read_bytes() == b"synthetic-racing-target"
    assert sorted(p.name for p in parent.iterdir()) == [target.name]
    assert fixture.manager.calls == []


@pytest.mark.parametrize("unavailable", ["symbol", "kernel"])
@pytest.mark.parametrize("name", ["connector.json", "provision.json", "install-transaction.json"])
def test_unavailable_atomic_rename_refuses_without_fallback(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, unavailable: str, name: str,
) -> None:
    parent = publication_parent(fixture)
    reached = False

    def rename(*args: object) -> int:
        nonlocal reached
        reached = True
        ctypes.set_errno(errno.ENOSYS)
        return -1

    library = SimpleNamespace() if unavailable == "symbol" else SimpleNamespace(renameat2=rename)
    monkeypatch.setattr(ctypes, "CDLL", lambda *args, **kwargs: library)
    with pytest.raises((module.InstallerError, OSError)):
        fixture.installer._write_provision_file(name, publication_data(name), fixture.installer.root_owner,
                                              0o640 if name == "connector.json" else 0o600)
    assert reached == (unavailable == "kernel")
    assert list(parent.iterdir()) == []
    assert fixture.manager.calls == []


def test_oversized_rendered_config_refuses_before_any_publication(fixture: Fixture) -> None:
    path = fixture.inputs / "sidecar.json"
    value = json.loads(path.read_bytes())
    # A bounded UTF8 input can expand beyond the output bound during JSON
    # normalization. The earlier connector must not be published first.
    value["RoleIdentity"] = "home-sidecar-" + "界" * 20000
    data = json.dumps(value, ensure_ascii=False).encode()
    assert len(data) < 65536 < len(json.dumps(value).encode())
    fixture.write(path, data)
    with pytest.raises(module.InstallerError):
        fixture.provision()
    assert fixture.inventory() == {}
    assert fixture.manager.calls == []


@pytest.mark.parametrize("name", ["provision.json", "install-transaction.json"])
@pytest.mark.parametrize("layout", ["source", "deb"])
def test_versioned_records_have_only_fixed_nonsecret_fields(fixture: Fixture, name: str, layout: str) -> None:
    fixture.installer.layout = layout
    parent = publication_parent(fixture)
    fixture.installer._write_provision_file(name, record_data(name, layout), fixture.installer.root_owner, 0o600)
    data = (parent / name).read_bytes()
    record = json.loads(data)
    assert set(record) == {"version", "layout", "digests", "phase"}
    assert type(record["version"]) is int and record["version"] == 1
    assert record["layout"] == layout
    assert record["phase"] == ("prepared" if name == "install-transaction.json" else "provisioned")
    assert set(record["digests"]) == {"connector.json", "sidecar.json", "policy.json", "release-daemon.json", "daemon.token"} | (
        {"provision.json"} if name == "install-transaction.json" else set())
    assert fixture.token.read_bytes().strip() not in data
    assert fixture.key.read_bytes() not in data
    assert str(fixture.home).encode() not in data
    assert fixture.manager.calls == []


@pytest.mark.parametrize("name", ["provision.json", "install-transaction.json"])
@pytest.mark.parametrize("bad", [
    "extra-secret", "missing", "version-bool", "version-future", "version-string", "layout",
    "phase", "arbitrary-path", "missing-digest", "secret-digest", "upper-digest", "digest-type",
    "digests-type", "duplicate", "non-json", "oversize",
])
def test_invalid_record_schema_refuses_before_publication(fixture: Fixture, name: str, bad: str) -> None:
    parent = publication_parent(fixture)
    record = json.loads(record_data(name))
    if bad == "extra-secret":
        record["token"] = fixture.token.read_text().strip()
    elif bad == "missing":
        del record["phase"]
    elif bad.startswith("version-"):
        record["version"] = {"version-bool": True, "version-future": 2, "version-string": "1"}[bad]
    elif bad in ("layout", "phase"):
        record[bad] = "unrecognized"
    elif bad == "arbitrary-path":
        record["digests"]["/etc/shadow"] = "a" * 64
    elif bad == "missing-digest":
        del record["digests"]["connector.json"]
    elif bad in ("secret-digest", "upper-digest", "digest-type"):
        record["digests"]["daemon.token"] = {
            "secret-digest": fixture.token.read_text().strip(), "upper-digest": "A" * 64, "digest-type": 1,
        }[bad]
    elif bad == "digests-type":
        record["digests"] = []
    data = json.dumps(record).encode()
    if bad == "duplicate":
        data = b'{"version":1,' + data[1:]
    elif bad == "non-json":
        data = b"not-json"
    elif bad == "oversize":
        data += b" " * 65536
    with pytest.raises(module.InstallerError):
        fixture.installer._write_provision_file(name, data, fixture.installer.root_owner, 0o600)
    assert list(parent.iterdir()) == []
    assert fixture.manager.calls == []


@pytest.mark.parametrize("name", ["install-transaction.json", ".install-transaction.json.pending"])
@pytest.mark.parametrize("operation", ["provision", "install", "uninstall", "drift"])
@pytest.mark.parametrize("phase", ["prepared", "replacement-stopped", *QUARANTINE_PHASES, *REMOVAL_PHASES, *CONFIG_REMOVAL_PHASES, *UNIT_REMOVAL_PHASES])
def test_transaction_inventory_never_authorizes_service_or_recovery(
    fixture: Fixture, name: str, operation: str, phase: str,
) -> None:
    fixture.prepare(operation)
    parent = publication_parent(fixture)
    record = json.loads(record_data("install-transaction.json"))
    record["phase"] = phase
    fixture.write(parent / name, json.dumps(record).encode())
    before = fixture.inventory()
    with pytest.raises(module.InstallerError):
        if operation == "uninstall":
            fixture.installer.uninstall(purge=True)
        else:
            fixture.invoke(operation)
    assert fixture.inventory() == before
    assert fixture.manager.calls == []


def generated_bundle(fixture: Fixture) -> Path:
    fixture.installer.provision(release_user=fixture.user, data_home=fixture.home)
    return fixture.root / "etc/happyranch"


def staged_inventory(fixture: Fixture) -> dict[str, tuple]:
    inventory = {}
    for path in (fixture.root, *fixture.root.rglob("*")):
        info = path.lstat()
        inventory[str(path.relative_to(fixture.root))] = (
            info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            path.read_bytes() if path.is_file() else None,
        )
    return inventory


class LifecycleManager(FakeManager):
    """Fixed-property fake; unknown commands still raise in the shared fixture."""

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.states: dict[str, str] = {}
        self.override: dict[str, str] = {}
        self.stop_error: str | None = None
        self.sticky = False

    def show_properties(self, unit: str, properties: tuple[str, ...]) -> dict[str, str]:
        if unit.endswith((".target", ".socket")):
            assert not {"MainPID", "ControlPID", "ControlGroup"}.intersection(properties)
        self.calls.append(("show", unit))
        path = self.root / "etc/systemd/system" / unit
        loaded = path.is_file()
        state = self.states.get(unit, "inactive")
        values = dict(FragmentPath=str(path) if loaded else "", DropInPaths="", NeedDaemonReload="no",
                      LoadState="loaded" if loaded else "not-found", ActiveState=state,
                      SubState="dead" if state == "inactive" else state,
                      MainPID="0", ControlPID="0", ControlGroup="", Job="")
        values.update(self.override)
        return {key: values[key] for key in properties}

    def stop(self, unit: str, **kwargs: object) -> None:
        from runtime.remote_access.service_manager import ServiceManagerError
        super().stop(unit, **kwargs)
        if unit == self.stop_error:
            raise ServiceManagerError("synthetic-manager-secret")
        if not self.sticky:
            self.states[unit] = "inactive"


def lifecycle_fixture(fixture: Fixture) -> LifecycleManager:
    manager = LifecycleManager(fixture.root)
    fixture.installer.manager = manager
    return manager


def prepared_replacement(fixture: Fixture, case: str = "failed") -> tuple[LifecycleManager, Path, bytes]:
    manager = lifecycle_fixture(fixture)
    modern = case == "modern-failed"
    if modern:
        generated_bundle(fixture)
    else:
        fixture.provision()
    inventory_units(fixture, modern=modern, missing=module.UNITS[1] if case == "partial" else None)
    if case != "partial":
        manager.states[module.UNITS[0]] = "failed"
    parent = fixture.root / "etc/happyranch"
    record = dict(version=1, layout=fixture.installer.layout, phase="prepared", digests={
        name: hashlib.sha256((parent / name).read_bytes()).hexdigest()
        for name in ("connector.json", "sidecar.json", "policy.json", "release-daemon.json", "daemon.token", "provision.json")
    })
    data = json.dumps(record, sort_keys=True).encode()
    fixture.installer._write_provision_file("install-transaction.json", data, fixture.installer.root_owner, 0o600)
    manager.calls.clear()
    return manager, parent, data


@pytest.mark.parametrize("case", ["failed", "partial", "modern-failed"])
def test_replacement_inventory_binds_exact_prepared_generation(fixture: Fixture, case: str) -> None:
    manager, parent, data = prepared_replacement(fixture, case)
    fixture.token.unlink()  # Classification must not reopen the release-home token.
    before = staged_inventory(fixture)
    result = fixture.installer.replacement_inventory()
    assert result.installation.state == "known-failed-or-partial"
    assert result.installation.detail == ("partial" if case == "partial" else "failed")
    assert result.journal_generation == hashlib.sha256(data).hexdigest()
    assert str(parent / "install-transaction.json") in result.installation.paths
    assert staged_inventory(fixture) == before
    assert all(call[0] == "show" and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("case", ["clean", "manual-active", "manual-stopped", "manual-journal", "staged", "recorded", "conflict"])
def test_replace_failed_refuses_non_candidates_without_effects(fixture: Fixture, case: str) -> None:
    manager = lifecycle_fixture(fixture)
    expected = "clean"
    if case == "manual-journal":
        manager, _, _ = prepared_replacement(fixture)
        manager.states.clear()
        expected = "healthy-manual"
    elif case in {"staged", "recorded"}:
        generated_bundle(fixture)
        expected = "modern"
        if case == "recorded":
            inventory_units(fixture, modern=True)
    elif case != "clean":
        fixture.provision()
        inventory_units(fixture)
        expected = "healthy-manual"
        if case == "manual-active":
            manager.states = dict.fromkeys(module.UNITS, "active")
        if case == "conflict":
            fixture.write(fixture.root / "etc/happyranch/custom.json", b"synthetic-secret")
            expected = "manual-install-conflict"
    before = staged_inventory(fixture)
    with pytest.raises(module.InstallerError, match=f"^replacement refused: {expected}:"):
        fixture.installer.replace_failed()
    assert staged_inventory(fixture) == before
    assert all(call[0] == "show" and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("bad", ["malformed", "unknown-phase", "extra-field", "layout", "digest", "missing-file",
                                 "pending", "mode", "symlink", "foreign-owner", "changed-journal"])
def test_replacement_inventory_refuses_unproven_journal(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, bad: str,
) -> None:
    manager, parent, data = prepared_replacement(fixture)
    path = parent / "install-transaction.json"
    record = json.loads(data)
    changed_inventory: list[dict[str, tuple]] = []
    if bad in {"unknown-phase", "extra-field", "layout", "digest"}:
        if bad == "unknown-phase":
            record["phase"] = "synthetic-secret"
        elif bad == "extra-field":
            record["token"] = "synthetic-secret"
        elif bad == "layout":
            record["layout"] = "deb"
        else:
            record["digests"]["provision.json"] = "f" * 64
        fixture.write(path, json.dumps(record).encode())
    elif bad == "malformed":
        fixture.write(path, b"synthetic-secret")
    elif bad == "missing-file":
        (parent / "daemon.token").unlink()
    elif bad == "pending":
        fixture.write(parent / ".install-transaction.json.pending", data)
    elif bad == "mode":
        path.chmod(0o644)
    elif bad == "symlink":
        path.unlink()
        path.symlink_to(fixture.inputs / "connector.json")
    elif bad == "foreign-owner":
        target, fstat = path.stat(), os.fstat

        def foreign(fd: int) -> object:
            info = fstat(fd)
            if (info.st_dev, info.st_ino) != (target.st_dev, target.st_ino):
                return info
            values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            return SimpleNamespace(**{**values, "st_uid": info.st_uid + 1})

        monkeypatch.setattr(os, "fstat", foreign)
    else:
        read = fixture.installer._read_file
        reads = 0

        def changed(parent_fd: int, name: str, **kwargs: object) -> bytes:
            nonlocal reads
            value = read(parent_fd, name, **kwargs)
            if name == "install-transaction.json":
                reads += 1
                if reads == 1:
                    # Replace the real final entry between the two real reads.
                    # A semantically identical record still has different bytes.
                    replacement = fixture.inputs / "replacement-journal.json"
                    fixture.write(replacement, value + b"\n")
                    os.replace(replacement, path)
                    changed_inventory.append(staged_inventory(fixture))
            return value

        monkeypatch.setattr(fixture.installer, "_read_file", changed)
    before = staged_inventory(fixture)
    result = fixture.installer.replacement_inventory()
    assert result.installation.state == "manual-install-conflict"
    assert result.journal_generation is None
    assert "synthetic-secret" not in repr(result)
    assert staged_inventory(fixture) == (changed_inventory[0] if changed_inventory else before)
    assert all(call[0] == "show" and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("journal", [False, True])
def test_replace_failed_stops_only_journaled_candidates(fixture: Fixture, journal: bool) -> None:
    manager, parent, _ = prepared_replacement(fixture)
    if not journal:
        (parent / "install-transaction.json").unlink()
    before = staged_inventory(fixture)
    with pytest.raises(module.InstallerError, match=("^replacement paused after stop intent;" if journal
                                                   else "^replacement mutation unavailable;")):
        fixture.installer.replace_failed()
    if journal:
        record = json.loads((parent / "install-transaction.json").read_bytes())
        assert record["phase"] == "replacement-stopped"
        assert [call[1] for call in manager.calls if call[0] == "stop"] == [module.UNITS[i] for i in (2, 0, 1, 3, 4)]
    else:
        assert staged_inventory(fixture) == before
        assert all(call[0] == "show" and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("case", ["failed", "partial", "modern-failed"])
def test_replacement_stop_intent_preserves_six_files_and_resumes(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    manager, parent, prepared = prepared_replacement(fixture, case)
    before = staged_inventory(fixture)
    fsync = os.fsync
    synced: list[tuple[int, int]] = []

    def sync(fd: int) -> None:
        info = os.fstat(fd)
        synced.append((info.st_dev, info.st_ino))
        fsync(fd)

    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(module.InstallerError, match="^replacement paused after stop intent;"):
        fixture.installer.replace_failed()
    journal = parent / "install-transaction.json"
    stopped = journal.read_bytes()
    expected = json.loads(prepared)
    expected["phase"] = "replacement-stopped"
    assert json.loads(stopped) == expected  # Same six digests; no path or secret fields.
    assert journal.stat().st_mode & 0o777 == 0o600
    assert (journal.stat().st_uid, journal.stat().st_gid) == fixture.installer.root_owner
    for path in (journal, parent):
        info = path.stat()
        assert (info.st_dev, info.st_ino) in synced
    after = staged_inventory(fixture)
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    assert changed == {"etc/happyranch", "etc/happyranch/install-transaction.json"}
    assert all(call[0] in {"show", "stop"} and call[1] in module.UNITS for call in manager.calls)
    assert all(state == "inactive" for state in manager.states.values())
    # A new installer instance represents loss of all in-memory observations.
    fixture.installer = module.ManagedInstaller(
        root=fixture.root, root_owner=fixture.installer.root_owner, manager=manager,
        lookup_user=fixture.installer._lookup_user, lookup_group=fixture.installer._lookup_group,
    )
    result = fixture.installer.replacement_inventory()
    assert result.installation.detail == "replacement-stopped"
    assert result.journal_generation == hashlib.sha256(stopped).hexdigest()
    manager.calls.clear()
    with pytest.raises(module.InstallerError, match="^replacement paused after config quarantine;"):
        fixture.installer.replace_failed()
    assert json.loads(journal.read_bytes()) == {**expected, "phase": "replacement-quarantined"}
    for name in expected["digests"]:
        path = parent / ((".replacement-" if name in QUARANTINE_CONFIGS else "") + name)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected["digests"][name]
    with pytest.raises(module.InstallerError, match=REMOVAL_BOUNDARY):
        fixture.installer.replace_failed()
    assert json.loads(journal.read_bytes()) == {**expected, "phase": "replacement-state-removed"}
    assert [call[1] for call in manager.calls if call[0] == "stop"] == 2 * [
        module.UNITS[i] for i in (2, 0, 1, 3, 4) if case != "partial" or i != 1]
    assert all(call[0] in {"show", "stop"} and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("failure", ["stop-error", "still-active", "pid", "control-pid", "cgroup", "job"])
def test_replacement_stop_failure_preserves_all_files(fixture: Fixture, failure: str) -> None:
    manager, _, _ = prepared_replacement(fixture)
    if failure == "stop-error":
        manager.stop_error = module.UNITS[1]
    elif failure == "still-active":
        manager.sticky = True
        manager.states[module.UNITS[0]] = "active"
        manager.states[module.UNITS[1]] = "failed"
    else:
        manager.override.update({"pid": {"MainPID": "42"}, "control-pid": {"ControlPID": "42"},
                                 "cgroup": {"ControlGroup": "/synthetic/cgroup"}, "job": {"Job": "42 start"}}[failure])
    before = staged_inventory(fixture)
    with pytest.raises(module.InstallerError) as error:
        fixture.installer.replace_failed()
    assert staged_inventory(fixture) == before
    assert any(call[0] == "stop" for call in manager.calls)
    assert str(error.value).startswith("quiescence unconfirmed:")
    assert all(call[0] in {"show", "stop"} and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("attack", ["foreign-owner", "parent-swap", "leaf-swap", "mode", "links", "size", "mtime"])
@pytest.mark.parametrize("candidate", ["prepared", "stopped", "quarantined", "removal", "config-removal", "unit-removal"])
def test_replacement_token_refuses_before_stop_or_publication(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, attack: str, candidate: str,
) -> None:
    manager, _, _ = (prepared_replacement(fixture) if candidate == "prepared" else stopped_replacement(fixture))
    if candidate in {"quarantined", "removal", "config-removal", "unit-removal"}:
        with pytest.raises(module.InstallerError, match="^replacement paused after config quarantine;"):
            fixture.installer.replace_failed()
    if candidate in {"config-removal", "unit-removal"}:
        with pytest.raises(module.InstallerError, match=REMOVAL_BOUNDARY):
            fixture.installer.replace_failed()
    if candidate == "unit-removal":
        with pytest.raises(module.InstallerError, match=CONFIG_REMOVAL_BOUNDARY):
            fixture.installer.replace_failed()
    if candidate in {"removal", "config-removal", "unit-removal"}:
        write = fixture.installer._write_provision_file
        def interrupt(name: str, data: bytes, *args: object, **kwargs: object) -> None:
            write(name, data, *args, **kwargs)
            raise SystemExit("synthetic-interruption")
        with monkeypatch.context() as patch:
            patch.setattr(fixture.installer, "_write_provision_file", interrupt)
            with pytest.raises(SystemExit):
                fixture.installer.replace_failed()
    manager.calls.clear()
    before = staged_inventory(fixture)
    target, fstat = fixture.token.stat(), os.fstat

    def change() -> None:
        if attack == "parent-swap":
            fixture.home.rename(fixture.home.with_name("previous-release"))
            fixture.home.mkdir(mode=0o700)
        elif attack == "leaf-swap":
            fixture.token.rename(fixture.home / "previous-token")
            fixture.write(fixture.token, b"synthetic-substitution")
        elif attack == "mode":
            fixture.token.chmod(0o640)
        elif attack == "links":
            os.link(fixture.token, fixture.home / "extra-link")
        elif attack == "size":
            fixture.token.write_bytes(b"synthetic-grown-token")
        elif attack == "mtime":
            os.utime(fixture.token, ns=(target.st_atime_ns, target.st_mtime_ns + 1_000_000_000))

    reads = watch_read(fixture, monkeypatch, change)
    if attack == "foreign-owner":
        def foreign(fd: int) -> object:
            info = fstat(fd)
            if (info.st_dev, info.st_ino) != (target.st_dev, target.st_ino):
                return info
            values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            return SimpleNamespace(**{**values, "st_uid": info.st_uid + 1})
        monkeypatch.setattr(os, "fstat", foreign)
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert staged_inventory(fixture) == before
    assert all(call[0] == "show" and call[1] in module.UNITS for call in manager.calls)
    assert len(reads) == (0 if attack == "foreign-owner" else 1)


@pytest.mark.parametrize("bad", ["malformed", "unknown-phase", "pending", "changed-bytes", "prepared-downgrade", *[
    f"{kind}:{name}" for kind in ("digest", "missing") for name in (
        "connector.json", "sidecar.json", "policy.json", "release-daemon.json", "daemon.token", "provision.json")]])
def test_replacement_stopped_intent_refuses_unproven_generation(fixture: Fixture, bad: str) -> None:
    manager, parent, _ = prepared_replacement(fixture)
    with pytest.raises(module.InstallerError, match="^replacement paused after stop intent;"):
        fixture.installer.replace_failed()
    journal = parent / "install-transaction.json"
    record = json.loads(journal.read_bytes())
    if ":" in bad:
        kind, name = bad.split(":")
        if kind == "digest":
            record["digests"][name] = "f" * 64
        else:
            (parent / name).unlink()
        fixture.write(journal, json.dumps(record, sort_keys=True).encode())
    elif bad == "malformed":
        fixture.write(journal, b"{invalid")
    elif bad == "unknown-phase":
        record["phase"] = "synthetic-secret"
        fixture.write(journal, json.dumps(record).encode())
    elif bad == "pending":
        fixture.write(parent / ".install-transaction.json.pending", journal.read_bytes())
    elif bad == "changed-bytes":
        fixture.write(journal, journal.read_bytes() + b"\n")
    else:
        # A valid prepared record cannot infer failure from the now-stopped host.
        record["phase"] = "prepared"
        fixture.write(journal, json.dumps(record).encode())
    manager.calls.clear()
    before = staged_inventory(fixture)
    observed = fixture.installer.replacement_inventory()
    assert observed.installation.state in {"manual-install-conflict", "healthy-manual"}
    assert observed.journal_generation is None
    with pytest.raises(module.InstallerError, match="^replacement refused:"):
        fixture.installer.replace_failed()
    assert staged_inventory(fixture) == before
    assert all(call[0] == "show" and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("point", ["pending-fsync", "parent-fsync", "after-intent"])
def test_replacement_intent_interruption_never_reopens_services(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, point: str,
) -> None:
    manager, parent, prepared = prepared_replacement(fixture)
    original_files = {name: (parent / name).read_bytes() for name in json.loads(prepared)["digests"]}
    fsync, write = os.fsync, fixture.installer._write_provision_file
    parent_info = parent.stat()
    interrupted = False

    def sync(fd: int) -> None:
        nonlocal interrupted
        info = os.fstat(fd)
        is_parent = (info.st_dev, info.st_ino) == (parent_info.st_dev, parent_info.st_ino)
        if not interrupted and ((point == "pending-fsync" and not is_parent) or (point == "parent-fsync" and is_parent)):
            interrupted = True
            raise OSError("synthetic-interruption")
        fsync(fd)

    def publish(*args: object, **kwargs: object) -> None:
        nonlocal interrupted
        write(*args, **kwargs)
        interrupted = True
        raise SystemExit("synthetic-process-death")

    with monkeypatch.context() as patch:
        if point == "after-intent":
            patch.setattr(fixture.installer, "_write_provision_file", publish)
        else:
            patch.setattr(os, "fsync", sync)
        with pytest.raises((module.InstallerError, SystemExit)):
            fixture.installer.replace_failed()
    assert interrupted
    assert all(state == "inactive" for state in manager.states.values())
    assert original_files == {name: (parent / name).read_bytes() for name in original_files}
    journal = parent / "install-transaction.json"
    assert json.loads(journal.read_bytes())["phase"] == ("prepared" if point == "pending-fsync" else "replacement-stopped")
    before = staged_inventory(fixture)
    manager.calls.clear()
    with pytest.raises(module.InstallerError, match=("^replacement refused: healthy-manual:" if point == "pending-fsync"
                                                   else "^replacement paused after config quarantine;")):
        fixture.installer.replace_failed()
    if point == "pending-fsync":
        assert staged_inventory(fixture) == before
    else:
        record = json.loads(journal.read_bytes())
        assert record == {**json.loads(prepared), "phase": "replacement-quarantined"}
        for name, data in original_files.items():
            entry = (".replacement-" if name in QUARANTINE_CONFIGS else "") + name
            assert (parent / entry).read_bytes() == data
    assert all(call[0] in ({"show"} if point == "pending-fsync" else {"show", "stop"})
               and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("attack", ["journal", "fixed-file", "parent-swap", "pending-substitution"])
def test_replacement_late_publication_change_refuses(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, attack: str,
) -> None:
    _, parent, prepared = prepared_replacement(fixture)
    fsync = os.fsync
    attacked = False
    moved = parent.with_name("previous-config")

    def sync(fd: int) -> None:
        nonlocal attacked
        fsync(fd)
        pending = parent / ".install-transaction.json.pending"
        if attacked or not pending.exists() or os.fstat(fd).st_ino != pending.stat().st_ino:
            return
        attacked = True
        if attack == "journal":
            fixture.write(parent / "install-transaction.json", prepared + b"\n")
        elif attack == "fixed-file":
            target = parent / "connector.json"
            target.write_bytes(target.read_bytes() + b"\n")
        elif attack == "parent-swap":
            parent.rename(moved)
            parent.mkdir(mode=0o755)
        else:
            pending.rename(parent / "unknown-pending")
            fixture.write(pending, b"synthetic-substitution")

    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert attacked
    actual_parent = moved if attack == "parent-swap" else parent
    assert (actual_parent / "install-transaction.json").read_bytes() == prepared + (b"\n" if attack == "journal" else b"")
    if attack == "parent-swap":
        assert list(parent.iterdir()) == []
    if attack == "pending-substitution":
        assert (parent / ".install-transaction.json.pending").read_bytes() == b"synthetic-substitution"
    else:
        assert not (actual_parent / ".install-transaction.json.pending").exists()


@pytest.mark.parametrize("operation", ["inventory", "stop"])
def test_replacement_observation_never_relaxes_other_transaction_guards(fixture: Fixture, operation: str) -> None:
    manager, _, _ = prepared_replacement(fixture)
    assert fixture.installer.replacement_inventory().journal_generation is not None
    manager.calls.clear()
    before = staged_inventory(fixture)
    if operation == "inventory":
        assert fixture.installer.inventory().state == "manual-install-conflict"
    else:
        with pytest.raises(module.InstallerError):
            fixture.installer.stop_and_confirm()
        assert all(call[0] == "show" for call in manager.calls)
    assert staged_inventory(fixture) == before


def stopped_replacement(fixture: Fixture, case: str = "failed") -> tuple[LifecycleManager, Path, bytes]:
    manager, parent, prepared = prepared_replacement(fixture, case)
    with pytest.raises(module.InstallerError, match="^replacement paused after stop intent;"):
        fixture.installer.replace_failed()
    manager.calls.clear()
    return manager, parent, prepared


@pytest.mark.parametrize("case", ["failed", "partial", "modern-failed"])
def test_replacement_quarantines_only_exact_configs(fixture: Fixture, case: str) -> None:
    manager, parent, prepared = stopped_replacement(fixture, case)
    for path in (fixture.root / "opt/happyranch/operator-data",
                 fixture.root / "etc/systemd/system/custom.service",
                 fixture.root / "etc/systemd/system/happyranch.service"):
        fixture.write(path, b"synthetic-unrelated-release-data")
    before = staged_inventory(fixture)
    original = {name: (parent / name).read_bytes() for name in QUARANTINE_CONFIGS}
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    record = json.loads((parent / "install-transaction.json").read_bytes())
    assert record == {**json.loads(prepared), "phase": "replacement-quarantined"}
    for name in QUARANTINE_CONFIGS:
        path = parent / (".replacement-" + name)
        assert not (parent / name).exists()
        assert path.read_bytes() == original[name]
        info = path.stat()
        assert (info.st_uid, info.st_gid) == fixture.installer.root_owner
        assert info.st_mode & 0o777 == 0o600
    after = staged_inventory(fixture)
    affected = {"etc/happyranch", "etc/happyranch/install-transaction.json", *[
        "etc/happyranch/" + prefix + name for prefix in ("", ".replacement-") for name in QUARANTINE_CONFIGS]}
    assert {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)} == affected
    assert all(call[0] in {"show", "stop"} and call[1] in module.UNITS for call in manager.calls)
    assert all(state == "inactive" for state in manager.states.values())
    manager.calls.clear()
    with pytest.raises(module.InstallerError, match=REMOVAL_BOUNDARY):
        fixture.installer.replace_failed()
    assert json.loads((parent / "install-transaction.json").read_bytes()) == {
        **json.loads(prepared), "phase": "replacement-state-removed"}
    assert all((parent / (".replacement-" + name)).read_bytes() == original[name] for name in QUARANTINE_CONFIGS)
    assert all(call[0] in {"show", "stop"} for call in manager.calls)


@pytest.mark.parametrize("phase,point", [(phase, point) for phase in QUARANTINE_PHASES[:3]
                                       for point in ("intent", "chmod", "chown", "tightened", "renamed")]
                         + [("replacement-quarantined", "intent")])
def test_replacement_quarantine_interruption_resumes_exact_generation(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, phase: str, point: str,
) -> None:
    manager, parent, prepared = stopped_replacement(fixture)
    write, fsync = fixture.installer._write_provision_file, os.fsync
    interrupted = False

    def publish(name: str, data: bytes, *args: object, **kwargs: object) -> None:
        nonlocal interrupted
        write(name, data, *args, **kwargs)
        if json.loads(data)["phase"] == phase and (point == "intent" or phase == QUARANTINE_PHASES[-1]):
            interrupted = True
            raise SystemExit("synthetic-process-death")

    def sync(fd: int) -> None:
        nonlocal interrupted
        fsync(fd)
        if json.loads((parent / "install-transaction.json").read_bytes())["phase"] != phase:
            return
        if phase == QUARANTINE_PHASES[-1]:
            return
        name = QUARANTINE_CONFIGS[QUARANTINE_PHASES.index(phase)]
        original, quarantine = parent / name, parent / (".replacement-" + name)
        info = os.fstat(fd)
        target = original if original.exists() else quarantine
        if ((point == "tightened" and original.exists() and info.st_ino == target.stat().st_ino
             and target.stat().st_mode & 0o777 == 0o600)
                or (point == "renamed" and quarantine.exists() and info.st_ino == parent.stat().st_ino)):
            interrupted = True
            raise SystemExit("synthetic-process-death")

    def metadata_change(method: Callable, boundary: str) -> Callable:
        def changed(fd: int, *args: object) -> None:
            nonlocal interrupted
            method(fd, *args)
            if point == boundary and json.loads((parent / "install-transaction.json").read_bytes())["phase"] == phase:
                name = QUARANTINE_CONFIGS[QUARANTINE_PHASES.index(phase)]
                if os.fstat(fd).st_ino == (parent / name).stat().st_ino:
                    interrupted = True
                    raise SystemExit("synthetic-process-death")
        return changed

    with monkeypatch.context() as patch:
        patch.setattr(fixture.installer, "_write_provision_file", publish)
        patch.setattr(os, "fsync", sync)
        patch.setattr(os, "fchmod", metadata_change(os.fchmod, "chmod"))
        patch.setattr(os, "fchown", metadata_change(os.fchown, "chown"))
        with pytest.raises((module.InstallerError, SystemExit)):
            fixture.installer.replace_failed()
    assert interrupted, "requested durable quarantine boundary was not reached"
    assert all(state == "inactive" for state in manager.states.values())
    # Lose all in-memory observations; only the closed on-disk generation resumes.
    fixture.installer = module.ManagedInstaller(
        root=fixture.root, root_owner=fixture.installer.root_owner, manager=manager,
        lookup_user=fixture.installer._lookup_user, lookup_group=fixture.installer._lookup_group,
    )
    observed = fixture.installer.replacement_inventory()
    assert observed.installation.detail == phase
    assert observed.journal_generation == hashlib.sha256((parent / "install-transaction.json").read_bytes()).hexdigest()
    consumes_quarantine = phase == "replacement-quarantined"
    boundary = REMOVAL_BOUNDARY if consumes_quarantine else "^replacement paused after config quarantine;"
    with pytest.raises(module.InstallerError, match=boundary):
        fixture.installer.replace_failed()
    assert json.loads((parent / "install-transaction.json").read_bytes()) == {
        **json.loads(prepared), "phase": "replacement-state-removed" if consumes_quarantine else "replacement-quarantined"}
    assert all(not (parent / name).exists() and (parent / (".replacement-" + name)).is_file()
               for name in QUARANTINE_CONFIGS)
    assert all(call[0] in {"show", "stop"} and call[1] in module.UNITS for call in manager.calls)
    assert all(state == "inactive" for state in manager.states.values())


@pytest.mark.parametrize("bad", ["unknown", "malformed", "digest", "downgraded", "prepared-downgrade", "missing", "duplicate",
                                 "future", "mode", "pending", "foreign-file", "custom-dropin", "symlink", "hardlink",
                                 "foreign-owner", "changed-bytes"])
def test_replacement_quarantine_refuses_unproven_placement(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, bad: str,
) -> None:
    manager, parent, _ = stopped_replacement(fixture)
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    journal = parent / "install-transaction.json"
    record = json.loads(journal.read_bytes())
    if bad in {"unknown", "digest", "downgraded", "prepared-downgrade", "future"}:
        if bad == "unknown":
            record["phase"] = "replacement-unrecognized"
        elif bad == "digest":
            record["digests"]["connector.json"] = "f" * 64
        elif bad == "prepared-downgrade":
            record["phase"] = "prepared"
        else:
            record["phase"] = "replacement-stopped" if bad == "downgraded" else QUARANTINE_PHASES[0]
        fixture.write(journal, json.dumps(record, sort_keys=True).encode())
    elif bad == "malformed":
        fixture.write(journal, b"{invalid")
    elif bad == "missing":
        (parent / ".replacement-sidecar.json").unlink()
    elif bad == "duplicate":
        fixture.write(parent / "connector.json", (parent / ".replacement-connector.json").read_bytes())
    elif bad == "mode":
        (parent / ".replacement-policy.json").chmod(0o640)
    elif bad == "pending":
        fixture.write(parent / ".install-transaction.json.pending", journal.read_bytes())
    elif bad == "changed-bytes":
        fixture.write(journal, journal.read_bytes() + b"\n")
    elif bad == "symlink":
        target = parent / ".replacement-policy.json"
        target.unlink()
        target.symlink_to(fixture.inputs / "policy.json")
    elif bad == "hardlink":
        os.link(parent / ".replacement-policy.json", fixture.inputs / "linked-policy")
    elif bad == "foreign-owner":
        target, fstat = (parent / ".replacement-policy.json").stat(), os.fstat

        def foreign(fd: int) -> object:
            info = fstat(fd)
            if (info.st_dev, info.st_ino) != (target.st_dev, target.st_ino):
                return info
            values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            return SimpleNamespace(**{**values, "st_uid": info.st_uid + 1})

        monkeypatch.setattr(os, "fstat", foreign)
    elif bad == "foreign-file":
        fixture.write(parent / "foreign.json", b"synthetic-foreign-data")
    else:
        dropin = fixture.root / "etc/systemd/system" / (module.UNITS[0] + ".d")
        dropin.mkdir(mode=0o755)
        fixture.write(dropin / "custom.conf", b"synthetic-foreign-data")
    manager.calls.clear()
    before = staged_inventory(fixture)
    result = fixture.installer.replacement_inventory()
    assert result.installation.state == "manual-install-conflict"
    assert result.journal_generation is None
    with pytest.raises(module.InstallerError, match="^replacement refused:"):
        fixture.installer.replace_failed()
    assert staged_inventory(fixture) == before
    assert all(call[0] == "show" and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("attack", ["journal", "source", "target", "parent"])
def test_replacement_quarantine_rechecks_published_intent(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, attack: str,
) -> None:
    manager, parent, _ = stopped_replacement(fixture)
    publish = fixture.installer._write_provision_file
    attacked: list[dict[str, tuple]] = []

    def changed(name: str, data: bytes, *args: object, **kwargs: object) -> None:
        publish(name, data, *args, **kwargs)
        if json.loads(data)["phase"] != QUARANTINE_PHASES[0]:
            return
        if attack == "journal":
            fixture.write(parent / "install-transaction.json", data + b"\n")
        elif attack == "source":
            path = parent / "connector.json"
            path.write_bytes(path.read_bytes() + b"\n")
        elif attack == "target":
            fixture.write(parent / ".replacement-connector.json", b"synthetic-foreign-data")
        else:
            parent.rename(parent.with_name("old-config"))
            parent.mkdir(mode=0o755)
        attacked.append(staged_inventory(fixture))

    monkeypatch.setattr(fixture.installer, "_write_provision_file", changed)
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert len(attacked) == 1
    assert staged_inventory(fixture) == attacked[0]
    assert all(call[0] in {"show", "stop"} and call[1] in module.UNITS for call in manager.calls)


def inventory_units(fixture: Fixture, *, modern: bool = False, missing: str | None = None) -> None:
    units = fixture.root / "etc/systemd/system"
    units.mkdir(parents=True, mode=0o755)
    for ancestor in units.parents:
        if ancestor == fixture.root:
            break
        ancestor.chmod(0o755)
    connector = {"managed": {"profile": "linux-managed-identity-v1"}} if modern else {}
    for name, content in module.managed_units(fixture.installer.layout, connector_config=connector).items():
        if name != missing:
            (units / name).write_text(content)
            (units / name).chmod(0o644)


@pytest.mark.parametrize("case, expected, delay", [(case, expected, 0) for case, expected in [
    ("clean", "clean"), ("staged", "modern"), ("staged-private-runtime", "modern"), ("recorded", "modern"),
    ("failed", "known-failed-or-partial"), ("partial-unit", "known-failed-or-partial"),
    ("partial-config", "known-failed-or-partial"),
    ("before-sidecar.json", "known-failed-or-partial"), ("before-policy.json", "known-failed-or-partial"),
    ("manual-active", "healthy-manual"), ("manual-0600", "healthy-manual"), ("manual-0644", "healthy-manual"),
    ("manual-stopped", "healthy-manual"), ("manual-recorded", "healthy-manual"),
    ("unit-only", "known-failed-or-partial"),
    ("before-release-daemon.json", "known-failed-or-partial"),
]] + [(case, expected, 3601) for case, expected in [
    ("before-release-daemon.json", "known-failed-or-partial"),
    ("manual-stopped", "healthy-manual"), ("staged", "modern"),
    ("partial-config", "known-failed-or-partial"),
]])
def test_fixed_inventory_classifies_without_effects(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, case: str, expected: str, delay: int,
) -> None:
    manager = lifecycle_fixture(fixture)
    interrupted = case.startswith("before-")
    if interrupted:
        writer = fixture.installer._write_provision_file
        writes: list[str] = []

        def fail_before(name: str, data: bytes, owner: tuple[int, int], mode: int) -> None:
            if name == case.removeprefix("before-"):
                raise OSError("synthetic publication interruption")
            writer(name, data, owner, mode)
            writes.append(name)

        with monkeypatch.context() as patch:
            patch.setattr(fixture.installer, "_write_provision_file", fail_before)
            with pytest.raises(module.InstallerError, match="provisioning failed"):
                generated_bundle(fixture)
        prefix = ["connector.json"] + (["sidecar.json"] if case in {"before-policy.json", "before-release-daemon.json"} else [])
        if case == "before-release-daemon.json":
            prefix.append("policy.json")
        assert writes == prefix
        assert {path.name for path in (fixture.root / "etc/happyranch").iterdir()} == set(prefix)
    elif case in {"staged", "staged-private-runtime", "recorded", "partial-config"}:
        parent = generated_bundle(fixture)
        if case == "partial-config":
            (parent / "sidecar.json").unlink()
        if case == "staged-private-runtime":
            (fixture.root / "run/happyranch-connector").chmod(0o700)
    elif case not in {"clean", "unit-only"}:
        fixture.provision()
        if case in {"manual-active", "manual-stopped", "manual-0600", "manual-0644"}:
            (fixture.root / "etc/happyranch/provision.json").unlink()
        if case in {"manual-0600", "manual-0644"}:
            for name in ("connector.json", "sidecar.json", "policy.json"):
                (fixture.root / "etc/happyranch" / name).chmod(int(case.removeprefix("manual-"), 8))
    if not interrupted and case not in {"clean", "staged", "staged-private-runtime", "partial-config"}:
        inventory_units(fixture, modern=case == "recorded",
                        missing=module.UNITS[1] if case == "partial-unit" else None)
    if case == "failed":
        manager.states[module.UNITS[0]] = "failed"
    if case == "manual-active":
        manager.states = dict.fromkeys(module.UNITS, "active")
    if delay:
        expire_staged_policy(fixture, monkeypatch)
        fresh_staging_instance(fixture)
    before = staged_inventory(fixture)
    result = fixture.installer.inventory()
    assert result.state == expected
    if interrupted:
        assert result.detail == "partial"
    if case.startswith("manual-"):
        assert result.detail == "migration-required"
    if case in {"staged", "recorded"}:
        assert result.detail == ("staged-inert" if case == "staged" else "recorded")
    if delay:
        assert fixture.installer.replacement_inventory().journal_generation is None
        if case == "staged":
            with pytest.raises(module.InstallerError, match="^replacement refused: modern:$"):
                fixture.installer.replace_failed()
        else:
            with pytest.raises(module.InstallerError, match="unjournaled replacement|replacement refused"):
                fixture.installer.replace_failed()
    assert staged_inventory(fixture) == before
    assert all(call[0] == "show" and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("case", ["unsafe-mode", "foreign-owner", "unreadable", "unknown-config", "unit-content", "dropin", "runtime-dropin",
                                 "vendor-fragment", "transaction", "pending-transaction", "digest", "unknown-state",
                                 "state-symlink", "dangling-link", "unit-symlink", "modern-0600", "modern-0644",
                                 "unrecorded-extra",
                                 "unrecorded-connector", "unrecorded-sidecar", "unrecorded-policy"])
def test_fixed_inventory_conflicts_name_only_safe_paths(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    manager = lifecycle_fixture(fixture)
    parent = generated_bundle(fixture)
    inventory_units(fixture, modern=True)
    path = parent / "connector.json"
    if case.startswith("unrecorded-"):
        (parent / "provision.json").unlink()
        if case == "unrecorded-extra":
            path = parent / "sidecar.json"
            value = json.loads(path.read_bytes())
            value["synthetic-secret"] = True
            path.write_text(json.dumps(value))
        else:
            path = parent / (case.removeprefix("unrecorded-") + ".json")
            path.write_bytes(b'{"synthetic-secret":true}')
    elif case in {"modern-0600", "modern-0644"}:
        path.chmod(int(case.removeprefix("modern-"), 8))
    elif case == "unsafe-mode":
        path.chmod(0o666)
    elif case == "foreign-owner":
        target, fstat = path.stat(), os.fstat

        def foreign(fd: int) -> object:
            info = fstat(fd)
            if (info.st_dev, info.st_ino) != (target.st_dev, target.st_ino):
                return info
            values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
            return SimpleNamespace(**{**values, "st_uid": info.st_uid + 1})

        monkeypatch.setattr(os, "fstat", foreign)
    elif case == "unreadable":
        read = fixture.installer._read_file

        def unreadable(parent: int, name: str, **kwargs: object) -> bytes:
            if name == "connector.json":
                raise OSError("synthetic-secret")
            return read(parent, name, **kwargs)

        monkeypatch.setattr(fixture.installer, "_read_file", unreadable)
    elif case == "unknown-config":
        path = parent / "custom.json"
        fixture.write(path, b"synthetic-secret")
    elif case == "unit-content":
        path = fixture.root / "etc/systemd/system" / module.UNITS[0]
        path.write_text("ExecStart=synthetic-secret\n")
    elif case in {"dropin", "runtime-dropin"}:
        base = "etc" if case == "dropin" else "run"
        path = fixture.root / base / "systemd/system" / (module.UNITS[0] + ".d") / "custom.conf"
        path.parent.mkdir(parents=True, mode=0o755)
        for ancestor in path.parents:
            if ancestor == fixture.root:
                break
            ancestor.chmod(0o755)
        fixture.write(path, b"synthetic-secret")
    elif case == "vendor-fragment":
        path = fixture.root / "usr/lib/systemd/system" / module.UNITS[0]
        path.parent.mkdir(parents=True, mode=0o755)
        for ancestor in path.parents:
            if ancestor == fixture.root:
                break
            ancestor.chmod(0o755)
        fixture.write(path, b"synthetic-secret")
    elif case in {"transaction", "pending-transaction"}:
        path = parent / ("install-transaction.json" if case == "transaction" else ".install-transaction.json.pending")
        fixture.write(path, record_data("install-transaction.json"))
    elif case == "digest":
        fixture.write(path, b'{"synthetic-secret":true}')
        path.chmod(0o640)
    elif case in {"unknown-state", "state-symlink"}:
        path = fixture.root / "var/lib/happyranch-tsnet-sidecar" / (
            "custom.state" if case == "unknown-state" else "tailscaled.state")
        if case == "state-symlink":
            path.symlink_to(fixture.token)
        else:
            fixture.write(path, b"synthetic-secret")
    elif case == "dangling-link":
        path = fixture.root / "etc/systemd/system/multi-user.target.wants" / module.UNITS[2]
        path.parent.mkdir(mode=0o755)
        path.symlink_to("/synthetic-missing-unit")
    else:
        path = fixture.root / "etc/systemd/system" / module.UNITS[0]
        path.unlink()
        path.symlink_to(fixture.inputs / "connector.json")
    # Snapshot no-follow metadata for the dangling link as well as content.
    before = {str(p): (p.lstat().st_ino, p.lstat().st_mode,
                       os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
              for p in fixture.root.rglob("*")}
    result = fixture.installer.inventory()
    assert result.state == "manual-install-conflict"
    assert result.paths == (str(parent if case.startswith("unrecorded-") else path),)
    assert "synthetic-secret" not in repr(result)
    assert {str(p): (p.lstat().st_ino, p.lstat().st_mode,
                     os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
            for p in fixture.root.rglob("*")} == before
    assert all(call[0] == "show" for call in manager.calls)


@pytest.mark.parametrize("property, value", [("FragmentPath", "/foreign/fragment"), ("DropInPaths", "/foreign/override"),
                                           ("LoadState", "error"), ("NeedDaemonReload", "yes"), ("ActiveState", "unknown")])
def test_fixed_inventory_loaded_uncertainty_refuses(fixture: Fixture, property: str, value: str) -> None:
    manager = lifecycle_fixture(fixture)
    generated_bundle(fixture)
    inventory_units(fixture, modern=True)
    manager.override[property] = value
    before = staged_inventory(fixture)
    result = fixture.installer.inventory()
    assert result.state == "manual-install-conflict"
    assert result.paths == (str(fixture.root / "etc/systemd/system" / module.UNITS[0]),)
    assert value not in repr(result)
    assert staged_inventory(fixture) == before


def test_fixed_inventory_never_reads_release_source_or_opaque_state(fixture: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    lifecycle_fixture(fixture)
    generated_bundle(fixture)
    lock = fixture.root / "var/lib/happyranch-connector/trust-state.json.lock"
    fixture.write(lock, b"")
    fixture.token.unlink()  # Inventory is not an authenticated token-rotation action.
    read = fixture.installer._read_file
    reads: list[str] = []

    def observe(parent: int, name: str, **kwargs: object) -> bytes:
        reads.append(name)
        return read(parent, name, **kwargs)

    monkeypatch.setattr(fixture.installer, "_read_file", observe)
    result = fixture.installer.inventory()
    assert result.state == "modern"
    assert lock.name not in reads


@pytest.mark.parametrize("case", ["success", "stop-error", "still-active", "pid", "control-pid", "cgroup", "job"])
def test_fixed_quiescence_proves_all_units_before_return(fixture: Fixture, case: str) -> None:
    manager = lifecycle_fixture(fixture)
    generated_bundle(fixture)
    inventory_units(fixture, modern=True)
    manager.states = dict.fromkeys(module.UNITS, "active")
    if case == "stop-error":
        manager.stop_error = module.UNITS[1]
    elif case == "still-active":
        manager.sticky = True
    else:
        manager.override.update({
            "pid": {"MainPID": "42"}, "control-pid": {"ControlPID": "42"},
            "cgroup": {"ControlGroup": "/synthetic/cgroup"}, "job": {"Job": "42 start"},
        }.get(case, {}))
    before = staged_inventory(fixture)
    if case == "success":
        assert fixture.installer.stop_and_confirm().state == "modern"
    else:
        with pytest.raises(module.InstallerError, match="^quiescence unconfirmed: ") as error:
            fixture.installer.stop_and_confirm()
        assert "synthetic-manager-secret" not in str(error.value)
    assert staged_inventory(fixture) == before
    stops = [call[1] for call in manager.calls if call[0] == "stop"]
    expected = [module.UNITS[index] for index in (2, 0, 1, 3, 4)]
    assert stops == (expected[:3] if case == "stop-error" else expected)
    assert all(call[0] in {"show", "stop"} and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("case", ["manual-stopped", "conflict", "post-stop-change"])
def test_fixed_quiescence_does_not_grant_migration_or_cached_authority(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    manager = lifecycle_fixture(fixture)
    if case == "post-stop-change":
        generated_bundle(fixture)
    else:
        fixture.provision()
    inventory_units(fixture, modern=case == "post-stop-change")
    if case == "manual-stopped":
        (fixture.root / "etc/happyranch/provision.json").unlink()
    elif case == "conflict":
        fixture.write(fixture.root / "etc/happyranch/unknown.json", b"synthetic-secret")
    else:
        stop = manager.stop

        def change(unit: str, **kwargs: object) -> None:
            stop(unit, **kwargs)
            if unit == module.UNITS[4]:
                fixture.write(fixture.root / "etc/happyranch/unknown.json", b"synthetic-secret")

        monkeypatch.setattr(manager, "stop", change)
    with pytest.raises(module.InstallerError, match="healthy-manual|manual-install-conflict"):
        fixture.installer.stop_and_confirm()
    if case != "post-stop-change":
        assert all(call[0] == "show" for call in manager.calls)



@pytest.mark.parametrize("layout", ["source", "deb"])
def test_generated_bundle_identity_policy_metadata_and_real_record(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, layout: str,
) -> None:
    fixture.installer.layout = layout
    if layout == "deb":
        payload = fixture.root / "opt/happyranch-desktop"
        for directory in (payload / "bin", payload / "python/bin"):
            directory.mkdir(parents=True, mode=0o755)
        for directory in (fixture.root / "opt", payload, payload / "python"):
            directory.chmod(0o755)
        for name in ("bin/happyranch-tsnet-sidecar", "python/bin/python3.12"):
            path = payload / name
            path.write_bytes(b"synthetic packaged executable")
            path.chmod(0o755)
        (payload / "python/bin/python3").symlink_to("python3.12")
        monkeypatch.setattr(os, "getgrouplist", lambda *args: [os.getgid()])
    publications: list[str] = []
    publish = fixture.installer._write_provision_file

    def observe(name: str, data: bytes, owner: tuple[int, int], mode: int) -> None:
        publications.append(name)
        publish(name, data, owner, mode)

    monkeypatch.setattr(fixture.installer, "_write_provision_file", observe)
    parent = generated_bundle(fixture)
    connector = json.loads((parent / "connector.json").read_bytes())
    sidecar = json.loads((parent / "sidecar.json").read_bytes())
    policy = json.loads((parent / "policy.json").read_bytes())
    ids = [connector[key] for key in ("tenant_id", "home_id", "connector_id")]
    ids.append(connector["managed"]["control_plane_id"])
    assert all(re.fullmatch(r"[0-9a-f]{32}", value) for value in ids)
    assert len(set(ids)) == 4
    assert connector["managed"]["profile"] == "linux-managed-identity-v1"
    assert connector["managed"]["bind_host"] == "127.0.0.1"
    assert connector["managed"]["bind_port"] == 8443  # Existing unused profile default, no TCP endpoint.
    assert "daemon_port" not in connector
    assert connector["excluded_daemon_ports"] == [8765]
    assert connector["daemon_token_path"] == "/etc/happyranch/daemon.token"
    assert connector["state_path"] == "/var/lib/happyranch-connector/trust-state.json"
    assert connector["policy_path"] == "/etc/happyranch/policy.json"
    assert sidecar == dict(Profile="linux-managed-identity-v1", StateDir="/var/lib/happyranch-tsnet-sidecar",
                          RoleIdentity=f"home-sidecar-{connector['home_id']}", DERPPolicy="private-only",
                          ListenAddr=":443", ExcludedDaemonPorts=[8765])
    assert {"ExpectedPeer", "ConnectorAddr", "ControlURL", "control_tls"}.isdisjoint(sidecar)
    artifact = json.loads(Path("runtime/remote_access/route_policy.json").read_bytes())
    assert policy["artifact"] == artifact
    assert policy["artifact"]["default_behavior"] == "deny_unclassified"
    issued = datetime.fromisoformat(policy["issued_at"])
    assert 0 <= (datetime.now(timezone.utc) - issued).total_seconds() < 60
    assert policy["max_age_seconds"] == 3600 and policy["state"] == "active"
    expected_modes = {"connector.json": 0o640, "sidecar.json": 0o640, "policy.json": 0o640,
                      "release-daemon.json": 0o644, "daemon.token": 0o600, "provision.json": 0o600}
    assert set(publications) == set(expected_modes) and len(publications) == 6
    assert {path.name for path in parent.iterdir()} == set(expected_modes)
    assert (parent.stat().st_uid, parent.stat().st_gid, parent.stat().st_mode & 0o7777) == (
        *fixture.installer.root_owner, 0o755)
    for name, mode in expected_modes.items():
        info = (parent / name).stat()
        # root:root, root:hr and hr:hr are simulated by the existing fixture's
        # same-user owner injection, never host chown/sudo or a foreign inode.
        assert (info.st_uid, info.st_gid, info.st_mode & 0o7777, info.st_nlink) == (os.getuid(), os.getgid(), mode, 1)
    record_bytes = (parent / "provision.json").read_bytes()
    record = json.loads(record_bytes)
    assert set(record) == {"version", "layout", "digests", "phase"}
    assert type(record["version"]) is int and record["version"] == 1
    assert record["layout"] == layout and record["phase"] == "provisioned"
    assert record["digests"] == {name: hashlib.sha256((parent / name).read_bytes()).hexdigest()
                                 for name in expected_modes if name != "provision.json"}
    assert fixture.token.read_bytes().strip() not in record_bytes
    assert fixture.key.read_bytes() not in record_bytes
    assert str(fixture.home).encode() not in record_bytes and b"/" not in record_bytes
    assert not (parent / "install-transaction.json").exists()
    assert fixture.manager.calls == []


@pytest.mark.parametrize("operation", ["install", "install-without-key", "drift", "write-units", "uninstall", "uninstall-purge"])
@pytest.mark.parametrize("marker", ["connector", "sidecar-only"])
def test_staged_identity_refuses_before_units_or_service_calls(fixture: Fixture, operation: str, marker: str) -> None:
    parent = generated_bundle(fixture)
    if marker == "sidecar-only":
        (parent / "connector.json").unlink()  # Even incomplete staged inventory cannot start the sidecar.
    if operation in {"drift", "uninstall", "uninstall-purge"}:
        units = fixture.root / "etc/systemd/system"
        units.mkdir(parents=True, mode=0o755)
        units.parent.chmod(0o755)
        for name, content in module.managed_units().items():
            (units / name).write_text(content)
            (units / name).chmod(0o644)
    before = staged_inventory(fixture)
    # A fresh instance must identify the staged profile from disk, not an
    # in-memory generation flag. Install cannot consume enrollment input first.
    fixture.installer = module.ManagedInstaller(
        root=fixture.root, root_owner=fixture.installer.root_owner, manager=fixture.manager,
        lookup_user=fixture.installer._lookup_user, lookup_group=fixture.installer._lookup_group)
    with pytest.raises(module.InstallerError, match="^home control binding not provisioned; requires slice 3b-2$"):
        if operation.startswith("install"):
            fixture.installer.install(enrollment_key_file=fixture.key if operation == "install" else None)
        elif operation == "drift":
            fixture.installer.check_drift()
        elif operation.startswith("uninstall"):
            fixture.installer.uninstall(purge=operation == "uninstall-purge")
        else:
            fixture.installer._write_units()
    assert staged_inventory(fixture) == before
    if operation not in {"drift", "uninstall", "uninstall-purge"}:
        assert not (fixture.root / "etc/systemd").exists()
    assert fixture.manager.calls == []


def lifecycle_cli_manager(fixture: Fixture, monkeypatch: pytest.MonkeyPatch) -> tuple[SystemdServiceManager, list]:
    """Real unit/backup lifecycle with only subprocess transport replaced."""
    units, backups = (fixture.root / name for name in ("lifecycle-units", "lifecycle-backups"))
    for directory in (units, backups):
        directory.mkdir(mode=0o700)
    fixture.write(units / "happyranch-connector.service", b"existing unit\n")
    fixture.write(backups / "happyranch-connector.service.1", b"previous unit\n")
    fixture.write(units / "sibling.service", b"unrelated unit\n")
    calls: list[tuple] = []

    def run(argv: list[str], **kwargs: object) -> tuple[int, str]:
        calls.append(tuple(argv))
        return 0, "active\n"

    manager = SystemdServiceManager(system=True, unit_dir=units, backup_dir=backups, run=run)
    monkeypatch.setattr(supervisor_module, "service_manager", lambda **kwargs: manager)
    # The alternate lifecycle now consults the same real fixed-path transaction
    # owner. Keep that read in the synthetic root, never the developer host.
    monkeypatch.setattr(module, "ManagedInstaller", lambda: fixture.installer)
    return manager, calls


@pytest.mark.parametrize("operation", ["upgrade", "rollback", "stop", "disable", "uninstall"])
def test_staged_identity_cli_preserves_lifecycle_inventory(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], operation: str,
) -> None:
    parent = generated_bundle(fixture)
    manager, service_calls = lifecycle_cli_manager(fixture, monkeypatch)
    manager_calls: list[str] = []
    method = getattr(manager, operation)

    def observe(*args: object, **kwargs: object) -> object:
        manager_calls.append(operation)
        return method(*args, **kwargs)

    monkeypatch.setattr(manager, operation, observe)
    before = staged_inventory(fixture)
    code = connector_cli.main([operation, "--config", str(parent / "connector.json")])
    output = capsys.readouterr()
    assert code == 1
    assert output.err == "error: home control binding not provisioned; requires slice 3b-2\n"
    assert output.out == ""
    assert staged_inventory(fixture) == before
    assert manager_calls == []
    assert service_calls == []


@pytest.mark.parametrize("operation", ["upgrade", "rollback"])
@pytest.mark.parametrize("profile", ["omitted", "empty"])
def test_legacy_cli_upgrade_rollback_keep_lifecycle(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    operation: str, profile: str,
) -> None:
    path = fixture.inputs / "connector.json"
    raw = json.loads(path.read_bytes())
    if profile == "omitted":
        raw["managed"].pop("profile", None)
    else:
        raw["managed"]["profile"] = ""
    fixture.write(path, json.dumps(raw).encode())
    manager, calls = lifecycle_cli_manager(fixture, monkeypatch)
    before = {path.name: path.read_bytes() for path in manager.backup_dir.iterdir()}
    assert connector_cli.main([operation, "--config", str(path)]) == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["ok"] is True
    expected_calls = [("systemctl", "daemon-reload"), ("systemctl", "restart", "happyranch-connector.service")]
    if operation == "upgrade":
        expected_calls.append(("systemctl", "is-active", "happyranch-connector.service"))
        installed = (manager.unit_dir / "happyranch-connector.service").read_text()
        assert "ExecStart=" in installed and "RuntimeDirectoryMode=0700" not in installed
        backups = {path.name: path.read_bytes() for path in manager.backup_dir.iterdir()}
        assert backups.items() >= before.items()
        assert len(backups) == 2 and b"existing unit\n" in backups.values()
    else:
        assert (manager.unit_dir / "happyranch-connector.service").read_bytes() == b"previous unit\n"
        assert {path.name: path.read_bytes() for path in manager.backup_dir.iterdir()} == before
    assert calls == expected_calls
    assert (manager.unit_dir / "sibling.service").read_bytes() == b"unrelated unit\n"


@pytest.mark.parametrize("operation", ["install", "start", "restart", "enable", "run"])
def test_staged_identity_cannot_bypass_via_supervisor(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    parent = generated_bundle(fixture)
    config = ConnectorConfig.from_file(parent / "connector.json")
    config.managed_dir_root = str(fixture.root / "alternate")
    config.state_path = str(fixture.root / "source-state/trust-state.json")
    config.policy_path = str(parent / "policy.json")
    supervisor = ConnectorSupervisor(config=config, manager=fixture.manager)
    # Isolate unrelated run-loop/provider and service-user ownership work so an
    # omitted guard reaches the observable admission assertions without host IO.
    monkeypatch.setattr(supervisor, "_run_loop", lambda **kwargs: 0)
    monkeypatch.setattr(supervisor, "_chown_tree", lambda *args: None)
    monkeypatch.setattr(fixture.manager, "restart", lambda unit: fixture.manager.calls.append(("restart", unit)), raising=False)
    monkeypatch.setattr(fixture.manager, "install", lambda *args, **kwargs: fixture.manager.calls.append(("install",)), raising=False)
    before = staged_inventory(fixture)
    with pytest.raises(ConnectorConfigError, match="^home control binding not provisioned; requires slice 3b-2$"):
        getattr(supervisor, operation)()
    assert staged_inventory(fixture) == before
    assert fixture.manager.calls == []


@pytest.mark.parametrize("operation", ["install", "uninstall", "start", "stop", "restart", "enable", "disable", "upgrade", "rollback", "run"])
@pytest.mark.parametrize("entry", ["install-transaction.json", ".install-transaction.json.pending"])
@pytest.mark.parametrize("phase", ["prepared", "replacement-stopped", *QUARANTINE_PHASES, *REMOVAL_PHASES, *CONFIG_REMOVAL_PHASES, *UNIT_REMOVAL_PHASES])
def test_replacement_transaction_blocks_legacy_supervisor_lifecycle(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, operation: str, entry: str, phase: str,
) -> None:
    fixture.provision()
    parent = fixture.root / "etc/happyranch"
    record = json.loads(record_data("install-transaction.json"))
    record["phase"] = phase
    fixture.write(parent / entry, json.dumps(record).encode())
    config = ConnectorConfig.from_file(parent / "connector.json")
    config.managed_dir_root = str(fixture.root / "alternate")
    config.state_path = str(fixture.root / "source-state/trust-state.json")
    config.policy_path = str(parent / "policy.json")
    manager, service_calls = lifecycle_cli_manager(fixture, monkeypatch)
    supervisor = ConnectorSupervisor(config=config, manager=manager)
    monkeypatch.setattr(supervisor, "_run_loop", lambda **kwargs: 0)
    monkeypatch.setattr(supervisor, "_chown_tree", lambda *args: None)
    before = staged_inventory(fixture)
    with pytest.raises(ConnectorConfigError, match="^installation transaction blocks remote lifecycle$"):
        getattr(supervisor, operation)()
    assert staged_inventory(fixture) == before
    assert service_calls == []


@pytest.mark.parametrize("entry", ["target", "pending"])
@pytest.mark.parametrize("name", ["release-daemon.json", "install-transaction.json"])
def test_generated_bundle_preserves_existing_inventory(fixture: Fixture, name: str, entry: str) -> None:
    parent = publication_parent(fixture)
    path = parent / (name if entry == "target" else f".{name}.pending")
    path.symlink_to(fixture.token)  # Refuse even an unsafe entry without following/repairing it.
    before = {p.name: p.lstat() for p in parent.iterdir()}
    with pytest.raises(module.InstallerError):
        generated_bundle(fixture)
    assert {p.name: p.lstat() for p in parent.iterdir()} == before
    assert path.is_symlink() and fixture.token.read_bytes() == b"synthetic-release-token\n"
    assert fixture.manager.calls == []


@pytest.mark.parametrize("bad", ["partial-config", "alternate-token"])
def test_generated_inputs_refuse_before_effects(fixture: Fixture, bad: str) -> None:
    inputs = {"connector_config": fixture.inputs / "connector.json"} if bad == "partial-config" else {
        "daemon_token_source": fixture.key}
    with pytest.raises(module.InstallerError, match="provide all three|fixed release-home token source"):
        fixture.installer.provision(release_user=fixture.user, data_home=fixture.home, **inputs)
    assert fixture.inventory() == {}
    assert list(fixture.root.iterdir()) == []
    assert fixture.manager.calls == []


# Exact main 9a68efc7 rendered bytes, recorded before this slice's edits.
_MAIN_UNIT_DIGESTS = {
    "source": ["2911b3eca89178f3d56f4b217382a04161d6fdd33969e2cf18cd4a546dbbba6f",
               "5cfef118da7b32c67a6c585885971660eef7ca2e5de3f4bd5050036bd34d551c",
               "af7c83b16489a907a8b58f7a56f5153b3203aab8791df8290af847344375982f",
               "8db88c53f4a00581009acc58e15cf70c00aabd07f707305242e2494c5d9b1351",
               "6c837e4f6282fec002645e0e79503721ef098685c3d64611e0fb4bbfedcb7f13"],
    "deb": ["c0d4c6c8188bc3caf846337f2c076203218b59405bed1353272a39e42f4cf2ea",
            "c66f28ab9a625ad0dd9b17fdd2fd00f5618f79bde8f4e22d6dddf175ffca48bc",
            "af7c83b16489a907a8b58f7a56f5153b3203aab8791df8290af847344375982f",
            "8db88c53f4a00581009acc58e15cf70c00aabd07f707305242e2494c5d9b1351",
            "3d4ac2db496b321baea22e019b07e1b7194113ed0a77f02594f962688252106a"],
}


@pytest.mark.parametrize("layout", ["source", "deb"])
def test_generated_profile_unit_bytes_and_legacy_main_parity(fixture: Fixture, layout: str) -> None:
    parent = generated_bundle(fixture)
    modern_config = json.loads((parent / "connector.json").read_bytes())
    legacy_config = json.loads((fixture.inputs / "connector.json").read_bytes())
    legacy_config["managed"].pop("profile")
    baseline = module.managed_units(layout)
    assert [hashlib.sha256(baseline[name].encode()).hexdigest() for name in module.UNITS] == _MAIN_UNIT_DIGESTS[layout]
    assert module.managed_units(layout, connector_config=legacy_config) == baseline
    legacy_config["managed"]["profile"] = ""
    assert module.managed_units(layout, connector_config=legacy_config) == baseline
    modern = module.managed_units(layout, connector_config=modern_config)
    assert modern[module.UNITS[0]].encode() == baseline[module.UNITS[0]].replace(
        "RuntimeDirectory=happyranch-connector\n",
        "RuntimeDirectory=happyranch-connector\nRuntimeDirectoryMode=0700\n", 1).encode()
    assert modern[module.UNITS[0]].count("RuntimeDirectoryMode=0700\n") == 1
    assert {name: modern[name].encode() for name in module.UNITS[1:]} == {
        name: baseline[name].encode() for name in module.UNITS[1:]}
    assert "SSL_CERT_" not in modern[module.UNITS[1]]  # Existing home_transport=False default.


@pytest.mark.parametrize("profile", ["omitted", "empty"])
def test_legacy_provenance_and_installed_units_keep_main_bytes(fixture: Fixture, profile: str) -> None:
    config_path = fixture.inputs / "connector.json"
    config = json.loads(config_path.read_bytes())
    if profile == "omitted":
        config["managed"].pop("profile")
    fixture.write(config_path, json.dumps(config).encode())
    inputs = {path.name: path.read_bytes() for path in fixture.inputs.iterdir()}
    fixture.provision()
    parent = fixture.root / "etc/happyranch"
    config.pop("daemon_port")
    config["excluded_daemon_ports"] = [8765]
    assert (parent / "connector.json").read_bytes() == json.dumps(config, sort_keys=True).encode()
    sidecar = json.loads(inputs["sidecar.json"])
    sidecar["ExcludedDaemonPorts"] = [8765]
    assert (parent / "sidecar.json").read_bytes() == json.dumps(sidecar, sort_keys=True).encode()
    assert (parent / "policy.json").read_bytes() == inputs["policy.json"]
    record = dict(layout="source", token_source=str(fixture.token),
                  token_sha256=hashlib.sha256(fixture.token.read_bytes()).hexdigest(),
                  configs={name: hashlib.sha256((parent / name).read_bytes()).hexdigest()
                           for name in ("connector.json", "sidecar.json", "policy.json")})
    assert (parent / "provision.json").read_bytes() == json.dumps(record, sort_keys=True).encode()
    fixture.installer.install(enrollment_key_file=fixture.key)
    assert [hashlib.sha256((fixture.root / "etc/systemd/system" / name).read_bytes()).hexdigest()
            for name in module.UNITS] == _MAIN_UNIT_DIGESTS["source"]
    assert {path.name: path.read_bytes() for path in fixture.inputs.iterdir()} == inputs


@pytest.mark.parametrize("mode", [0o600, 0o644])
def test_legacy_unrecorded_source_install_keeps_config_bytes_and_modes(fixture: Fixture, mode: int) -> None:
    fixture.provision()
    parent = fixture.root / "etc/happyranch"
    (parent / "provision.json").unlink()  # Existing independently provisioned source-install lane.
    for name in ("connector.json", "sidecar.json"):
        (parent / name).chmod(mode)
    configs = {name: ((parent / name).read_bytes(), (parent / name).stat().st_mode)
               for name in ("connector.json", "sidecar.json", "policy.json")}
    fixture.installer.install(enrollment_key_file=fixture.key)
    assert {name: ((parent / name).read_bytes(), (parent / name).stat().st_mode) for name in configs} == configs
    assert [hashlib.sha256((fixture.root / "etc/systemd/system" / name).read_bytes()).hexdigest()
            for name in module.UNITS] == _MAIN_UNIT_DIGESTS["source"]


def quarantined_with_state(fixture: Fixture, case: str = "failed") -> tuple[LifecycleManager, Path, bytes]:
    manager, parent, prepared = stopped_replacement(fixture, case)
    with pytest.raises(module.InstallerError, match="^replacement paused after config quarantine;"):
        fixture.installer.replace_failed()
    for _, relative in REMOVALS[:-1]:
        fixture.write(fixture.root / relative, b"synthetic-retired-state")
    (fixture.root / REMOVALS[-1][1]).symlink_to(fixture.root / REMOVALS[-2][1])
    manager.calls.clear()
    return manager, parent, prepared


@pytest.mark.parametrize("case", ["failed", "partial", "modern-failed"])
def test_replacement_removes_only_fixed_state_and_enrollment(fixture: Fixture, case: str) -> None:
    manager, parent, prepared = quarantined_with_state(fixture, case)
    for relative in ("opt/happyranch/operator-data", "etc/systemd/system/custom.service",
                     "etc/systemd/system/happyranch.service"):
        fixture.write(fixture.root / relative, b"synthetic-unrelated-data")
    before = staged_inventory(fixture)
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert json.loads((parent / "install-transaction.json").read_bytes()) == {
        **json.loads(prepared), "phase": "replacement-state-removed"}
    assert all(not (fixture.root / relative).exists() and not (fixture.root / relative).is_symlink()
               for _, relative in REMOVALS)
    after = staged_inventory(fixture)
    affected = {"etc/happyranch/install-transaction.json", "etc/happyranch", "etc/credstore",
                "var/lib/happyranch-connector", "var/lib/happyranch-tsnet-sidecar", *[r for _, r in REMOVALS]}
    assert {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)} == affected
    assert all(state == "inactive" for state in manager.states.values())
    assert all(call[0] in {"show", "stop"} and call[1] in module.UNITS for call in manager.calls)
    manager.calls.clear()
    with pytest.raises(module.InstallerError, match=CONFIG_REMOVAL_BOUNDARY):
        fixture.installer.replace_failed()
    assert all(not (parent / (".replacement-" + name)).exists() for name in QUARANTINE_CONFIGS)
    assert json.loads((parent / "install-transaction.json").read_bytes())["phase"] == "replacement-configs-removed"
    config_after = staged_inventory(fixture)
    assert {key for key in after.keys() | config_after.keys() if after.get(key) != config_after.get(key)} == {
        "etc/happyranch", "etc/happyranch/install-transaction.json", *[r for _, r in CONFIG_REMOVALS]}
    assert all(call[0] in {"show", "stop"} and call[1] in module.UNITS for call in manager.calls)
    assert fixture.installer.inventory().state == "manual-install-conflict"


@pytest.mark.parametrize("phase,relative,point", [
    (phase, relative, point) for phase, relative in REMOVALS for point in ("intent", "unlink", "fsync")
] + [("replacement-state-removed", "", "intent")])
def test_replacement_state_removal_interruption_resumes(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, phase: str, relative: str, point: str,
) -> None:
    manager, parent, prepared = quarantined_with_state(fixture)
    write, unlink, fsync = fixture.installer._write_provision_file, os.unlink, os.fsync
    interrupted = False

    def die() -> None:
        nonlocal interrupted
        interrupted = True
        raise SystemExit("synthetic-process-death")

    def publish(name: str, data: bytes, *args: object, **kwargs: object) -> None:
        write(name, data, *args, **kwargs)
        if json.loads(data)["phase"] == phase and point == "intent":
            die()

    def remove(name: str, *, dir_fd: int) -> None:
        if str(name) == Path(relative).name and point == "unlink":
            assert json.loads((parent / "install-transaction.json").read_bytes())["phase"] == phase
            unlink(name, dir_fd=dir_fd)
            die()
        unlink(name, dir_fd=dir_fd)

    def sync(fd: int) -> None:
        fsync(fd)
        if point == "fsync" and json.loads((parent / "install-transaction.json").read_bytes())["phase"] == phase:
            info = os.fstat(fd)
            target = fixture.root / relative
            if not target.exists() and not target.is_symlink() and info.st_ino == target.parent.stat().st_ino:
                die()

    with monkeypatch.context() as patch:
        patch.setattr(fixture.installer, "_write_provision_file", publish)
        patch.setattr(os, "unlink", remove)
        patch.setattr(os, "fsync", sync)
        with pytest.raises((module.InstallerError, SystemExit)):
            fixture.installer.replace_failed()
    assert interrupted, "requested removal boundary was not reached"
    journal = (parent / "install-transaction.json").read_bytes()
    assert json.loads(journal) == {**json.loads(prepared), "phase": phase}
    assert all(state == "inactive" for state in manager.states.values())
    previous = fixture.installer
    fixture.installer = module.ManagedInstaller(
        root=fixture.root, root_owner=previous.root_owner, manager=manager,
        lookup_user=previous._lookup_user, lookup_group=previous._lookup_group)
    observed = fixture.installer.replacement_inventory()
    assert observed.installation.detail == phase
    assert observed.journal_generation == hashlib.sha256(journal).hexdigest()
    consumes_state = phase == "replacement-state-removed"
    boundary = CONFIG_REMOVAL_BOUNDARY if consumes_state else REMOVAL_BOUNDARY
    with pytest.raises(module.InstallerError, match=boundary):
        fixture.installer.replace_failed()
    assert all(not (fixture.root / r).exists() and not (fixture.root / r).is_symlink() for _, r in REMOVALS)
    assert json.loads((parent / "install-transaction.json").read_bytes())["phase"] == (
        "replacement-configs-removed" if consumes_state else "replacement-state-removed")
    if consumes_state:
        assert all(not (fixture.root / r).exists() for _, r in CONFIG_REMOVALS)
    assert all(state == "inactive" for state in manager.states.values())
    assert all(call[0] in {"show", "stop"} and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("bad", ["unknown", "malformed", "digest", "downgraded", "pending", "foreign-file",
                                 "custom-dropin", "foreign-owner", "symlink", "hardlink", "reappeared"])
def test_replacement_state_removal_refuses_unproven_inventory(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, bad: str,
) -> None:
    manager, parent, _ = quarantined_with_state(fixture)
    journal = parent / "install-transaction.json"
    record = json.loads(journal.read_bytes())
    if bad in {"unknown", "digest", "downgraded"}:
        if bad == "digest":
            record["digests"]["sidecar.json"] = "f" * 64
        else:
            record["phase"] = "replacement-unknown" if bad == "unknown" else "replacement-stopped"
        fixture.write(journal, json.dumps(record, sort_keys=True).encode())
    elif bad == "malformed":
        fixture.write(journal, b"{invalid")
    elif bad == "pending":
        fixture.write(parent / ".install-transaction.json.pending", journal.read_bytes())
    elif bad == "foreign-file":
        fixture.write(fixture.root / "var/lib/happyranch-tsnet-sidecar/foreign", b"foreign")
    elif bad == "custom-dropin":
        dropin = fixture.root / "etc/systemd/system" / (module.UNITS[0] + ".d/custom.conf")
        dropin.parent.mkdir()
        fixture.write(dropin, b"foreign")
    elif bad in {"symlink", "hardlink"}:
        target = fixture.root / REMOVALS[0][1]
        if bad == "symlink":
            target.unlink()
            target.symlink_to(fixture.token)
        else:
            os.link(target, fixture.inputs / "state-alias")
    elif bad == "foreign-owner":
        target, fstat = (fixture.root / REMOVALS[0][1]).stat(), os.stat
        def foreign(*args: object, **kwargs: object) -> object:
            info = fstat(*args, **kwargs)
            if (info.st_dev, info.st_ino) == (target.st_dev, target.st_ino):
                values = {n: getattr(info, n) for n in dir(info) if n.startswith("st_")}
                return SimpleNamespace(**{**values, "st_uid": info.st_uid + 1})
            return info
        monkeypatch.setattr(os, "stat", foreign)
    else:
        with pytest.raises(module.InstallerError, match=REMOVAL_BOUNDARY):
            fixture.installer.replace_failed()
        fixture.write(fixture.root / REMOVALS[0][1], b"reappeared")
    before = staged_inventory(fixture)
    manager.calls.clear()
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert staged_inventory(fixture) == before
    assert all(call[0] == "show" for call in manager.calls)


@pytest.mark.parametrize("attack", ["journal", "fixed-file", "parent-swap", "leaf-swap", "foreign-entry"])
def test_replacement_state_removal_rechecks_before_unlink(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, attack: str,
) -> None:
    manager, parent, _ = quarantined_with_state(fixture)
    check = fixture.installer._check_replacement_transaction
    inside = False
    checks = 0
    mutated = False
    snapshot: dict = {}
    calls: list = []
    remove = fixture.installer._remove_replacement_entry

    def enter(transaction: bytes) -> None:
        nonlocal inside
        inside = True
        remove(transaction)

    def checked(fd: int, transaction: bytes) -> None:
        nonlocal mutated, checks
        check(fd, transaction)
        if not inside or mutated:
            return
        checks += 1
        if checks != 2:
            return
        mutated = True
        target = fixture.root / REMOVALS[0][1]
        if attack == "journal":
            journal = parent / "install-transaction.json"
            fixture.write(journal, journal.read_bytes() + b"\n")
        elif attack == "fixed-file":
            fixture.write(parent / ".replacement-policy.json", b"synthetic-substitution")
        elif attack == "parent-swap":
            target.parent.rename(target.parent.with_name("previous-state"))
            target.parent.mkdir(mode=0o755)
        elif attack == "leaf-swap":
            target.rename(target.with_name("previous-state"))
            target.symlink_to(fixture.token)
        else:
            fixture.write(target.parent / "foreign", b"synthetic-foreign")
        snapshot.update(staged_inventory(fixture))
        calls.extend(manager.calls)

    monkeypatch.setattr(fixture.installer, "_remove_replacement_entry", enter)
    monkeypatch.setattr(fixture.installer, "_check_replacement_transaction", checked)
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert mutated, "late removal boundary was not reached"
    assert staged_inventory(fixture) == snapshot
    assert all(call[0] == "show" for call in manager.calls[len(calls):])


def state_removed_with_configs(fixture: Fixture, case: str = "failed") -> tuple[LifecycleManager, Path, bytes]:
    manager, parent, prepared = quarantined_with_state(fixture, case)
    with pytest.raises(module.InstallerError, match=REMOVAL_BOUNDARY):
        fixture.installer.replace_failed()
    manager.calls.clear()
    return manager, parent, prepared


@pytest.mark.parametrize("case", ["failed", "partial", "modern-failed"])
def test_replacement_removes_only_quarantined_configs(fixture: Fixture, case: str) -> None:
    manager, parent, prepared = state_removed_with_configs(fixture, case)
    for relative in ("opt/happyranch/operator-data", "etc/systemd/system/custom.service",
                     "etc/systemd/system/happyranch.service"):
        fixture.write(fixture.root / relative, b"synthetic-unrelated-data")
    before = staged_inventory(fixture)
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert json.loads((parent / "install-transaction.json").read_bytes()) == {
        **json.loads(prepared), "phase": "replacement-configs-removed"}
    after = staged_inventory(fixture)
    assert {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)} == {
        "etc/happyranch", "etc/happyranch/install-transaction.json", *[r for _, r in CONFIG_REMOVALS]}
    assert all(not (fixture.root / r).exists() for _, r in CONFIG_REMOVALS)
    assert all(state == "inactive" for state in manager.states.values())
    assert all(call[0] in {"show", "stop"} and call[1] in module.UNITS for call in manager.calls)
    manager.calls.clear()
    with pytest.raises(module.InstallerError, match=UNIT_REMOVAL_BOUNDARY):
        fixture.installer.replace_failed()
    units_removed = staged_inventory(fixture)
    changed = {key for key in after.keys() | units_removed.keys() if after.get(key) != units_removed.get(key)}
    assert changed <= {"etc/happyranch", "etc/happyranch/install-transaction.json", "etc/systemd/system",
                       *[relative for _, relative, _ in UNIT_REMOVALS]}
    assert all(not (fixture.root / relative).exists() for _, relative, _ in UNIT_REMOVALS)
    assert fixture.installer.replacement_inventory().installation.detail == "replacement-units-removed"
    assert fixture.installer.inventory().state == "manual-install-conflict"


@pytest.mark.parametrize("phase,relative,point", [
    (phase, relative, point) for phase, relative in CONFIG_REMOVALS for point in ("intent", "unlink", "fsync")
] + [("replacement-configs-removed", "", "intent")])
def test_replacement_config_removal_interruption_resumes(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, phase: str, relative: str, point: str,
) -> None:
    manager, parent, prepared = state_removed_with_configs(fixture)
    write, unlink, fsync = fixture.installer._write_provision_file, os.unlink, os.fsync
    interrupted = False

    def die() -> None:
        nonlocal interrupted
        interrupted = True
        raise SystemExit("synthetic-process-death")

    def publish(name: str, data: bytes, *args: object, **kwargs: object) -> None:
        write(name, data, *args, **kwargs)
        if json.loads(data)["phase"] == phase and point == "intent":
            die()

    def remove(name: str, *, dir_fd: int) -> None:
        unlink(name, dir_fd=dir_fd)
        if str(name) == Path(relative).name and point == "unlink":
            assert json.loads((parent / "install-transaction.json").read_bytes())["phase"] == phase
            die()

    def sync(fd: int) -> None:
        fsync(fd)
        if point == "fsync" and json.loads((parent / "install-transaction.json").read_bytes())["phase"] == phase:
            target = fixture.root / relative
            if not target.exists() and os.fstat(fd).st_ino == parent.stat().st_ino:
                die()

    with monkeypatch.context() as patch:
        patch.setattr(fixture.installer, "_write_provision_file", publish)
        patch.setattr(os, "unlink", remove)
        patch.setattr(os, "fsync", sync)
        with pytest.raises((module.InstallerError, SystemExit)):
            fixture.installer.replace_failed()
    assert interrupted, "requested config removal boundary was not reached"
    journal = (parent / "install-transaction.json").read_bytes()
    assert json.loads(journal) == {**json.loads(prepared), "phase": phase}
    previous = fixture.installer
    fixture.installer = module.ManagedInstaller(
        root=fixture.root, root_owner=previous.root_owner, manager=manager,
        lookup_user=previous._lookup_user, lookup_group=previous._lookup_group)
    observed = fixture.installer.replacement_inventory()
    assert observed.installation.detail == phase
    assert observed.journal_generation == hashlib.sha256(journal).hexdigest()
    consumes_configs = phase == "replacement-configs-removed"
    with pytest.raises(module.InstallerError, match=UNIT_REMOVAL_BOUNDARY if consumes_configs else CONFIG_REMOVAL_BOUNDARY):
        fixture.installer.replace_failed()
    assert json.loads((parent / "install-transaction.json").read_bytes())["phase"] == (
        "replacement-units-removed" if consumes_configs else "replacement-configs-removed")
    assert all(not (fixture.root / r).exists() for _, r in CONFIG_REMOVALS)
    assert all(state == "inactive" for state in manager.states.values())
    assert all(call[0] in {"show", "stop"} and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("phase", CONFIG_REMOVAL_PHASES)
@pytest.mark.parametrize("point", ["pending-fsync", "rename", "parent-fsync"])
def test_replacement_config_removal_publication_interruption(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, phase: str, point: str,
) -> None:
    manager, parent, prepared = state_removed_with_configs(fixture)
    sync, rename = os.fsync, fixture.installer._rename_provision_entry
    interrupted = False

    def die() -> None:
        nonlocal interrupted
        interrupted = True
        raise SystemExit("synthetic-publication-death")

    def fsync(fd: int) -> None:
        sync(fd)
        pending = parent / ".install-transaction.json.pending"
        current = parent / "install-transaction.json"
        if point == "pending-fsync" and pending.exists() and json.loads(pending.read_bytes())["phase"] == phase:
            if os.fstat(fd).st_ino == pending.stat().st_ino:
                die()
        if point == "parent-fsync" and json.loads(current.read_bytes())["phase"] == phase:
            if os.fstat(fd).st_ino == parent.stat().st_ino:
                die()

    def replace(fd: int, source: str, destination: str, *, updating: bool = False) -> None:
        rename(fd, source, destination, updating=updating)
        if point == "rename" and json.loads((parent / destination).read_bytes())["phase"] == phase:
            die()

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fsync)
        patch.setattr(fixture.installer, "_rename_provision_entry", replace)
        with pytest.raises(SystemExit):
            fixture.installer.replace_failed()
    assert interrupted, "requested publication boundary was not reached"
    record = json.loads((parent / "install-transaction.json").read_bytes())
    phases = ["replacement-state-removed", *CONFIG_REMOVAL_PHASES]
    expected_phase = phases[phases.index(phase) - 1] if point == "pending-fsync" else phase
    assert record == {**json.loads(prepared), "phase": expected_phase}
    previous = fixture.installer
    fixture.installer = module.ManagedInstaller(
        root=fixture.root, root_owner=previous.root_owner, manager=manager,
        lookup_user=previous._lookup_user, lookup_group=previous._lookup_group)
    assert fixture.installer.replacement_inventory().installation.detail == expected_phase
    consumes_configs = expected_phase == "replacement-configs-removed"
    with pytest.raises(module.InstallerError, match=UNIT_REMOVAL_BOUNDARY if consumes_configs else CONFIG_REMOVAL_BOUNDARY):
        fixture.installer.replace_failed()
    assert json.loads((parent / "install-transaction.json").read_bytes())["phase"] == (
        "replacement-units-removed" if consumes_configs else "replacement-configs-removed")
    assert all(not (fixture.root / r).exists() for _, r in CONFIG_REMOVALS)
    assert all(state == "inactive" for state in manager.states.values())
    assert all(call[0] in {"show", "stop"} and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("bad", ["unknown", "malformed", "digest", "downgraded", "pending", "original",
                                 "foreign-file", "custom-dropin", "foreign-owner", "mode", "symlink", "hardlink",
                                 "reappeared-config", "reappeared-state", "unit-profile", "stop-error", "pid"])
def test_replacement_config_removal_refuses_unproven_inventory(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, bad: str,
) -> None:
    manager, parent, _ = state_removed_with_configs(fixture, "modern-failed")
    journal = parent / "install-transaction.json"
    target = parent / ".replacement-connector.json"
    record = json.loads(journal.read_bytes())
    if bad in {"unknown", "digest", "downgraded"}:
        if bad == "digest":
            record["digests"]["connector.json"] = "f" * 64
        else:
            record["phase"] = "replacement-unknown" if bad == "unknown" else "replacement-quarantined"
            if bad == "downgraded":
                with pytest.raises(module.InstallerError, match=CONFIG_REMOVAL_BOUNDARY):
                    fixture.installer.replace_failed()
        fixture.write(journal, json.dumps(record, sort_keys=True).encode())
    elif bad == "malformed":
        fixture.write(journal, b"{invalid")
    elif bad == "pending":
        fixture.write(parent / ".install-transaction.json.pending", journal.read_bytes())
    elif bad == "original":
        fixture.write(parent / "connector.json", target.read_bytes())
    elif bad == "foreign-file":
        fixture.write(parent / "foreign", b"foreign")
    elif bad == "custom-dropin":
        dropin = fixture.root / "etc/systemd/system" / (module.UNITS[0] + ".d/custom.conf")
        dropin.parent.mkdir()
        fixture.write(dropin, b"foreign")
    elif bad in {"mode", "symlink", "hardlink"}:
        if bad == "mode":
            target.chmod(0o640)
        elif bad == "symlink":
            target.unlink()
            target.symlink_to(fixture.token)
        else:
            os.link(target, fixture.inputs / "config-alias")
    elif bad == "foreign-owner":
        original, getstat = target.stat(), os.stat
        def foreign(*args: object, **kwargs: object) -> object:
            info = getstat(*args, **kwargs)
            if (info.st_dev, info.st_ino) == (original.st_dev, original.st_ino):
                values = {n: getattr(info, n) for n in dir(info) if n.startswith("st_")}
                return SimpleNamespace(**{**values, "st_uid": info.st_uid + 1})
            return info
        monkeypatch.setattr(os, "stat", foreign)
    elif bad.startswith("reappeared") or bad == "unit-profile":
        with pytest.raises(module.InstallerError, match=CONFIG_REMOVAL_BOUNDARY):
            fixture.installer.replace_failed()
        if bad == "unit-profile":
            path = fixture.root / "etc/systemd/system" / module.UNITS[0]
            path.write_text(module.managed_units("source")[module.UNITS[0]])
        else:
            fixture.write(target if bad.endswith("config") else fixture.root / REMOVALS[0][1], b"reappeared")
    elif bad == "stop-error":
        manager.stop_error = module.UNITS[1]
    else:
        manager.override["MainPID"] = "42"
    before = staged_inventory(fixture)
    manager.calls.clear()
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert staged_inventory(fixture) == before
    assert all(call[0] in ({"show", "stop"} if bad in {"stop-error", "pid"} else {"show"}) for call in manager.calls)


@pytest.mark.parametrize("attack", ["journal", "fixed-file", "parent-swap", "leaf-swap", "foreign-entry"])
def test_replacement_config_removal_rechecks_before_unlink(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, attack: str,
) -> None:
    manager, parent, _ = state_removed_with_configs(fixture)
    check, remove = fixture.installer._check_replacement_transaction, fixture.installer._remove_replacement_entry
    inside, checks, mutated = False, 0, False
    snapshot: dict = {}
    calls: list = []

    def enter(transaction: bytes) -> None:
        nonlocal inside
        inside = True
        remove(transaction)

    def checked(fd: int, transaction: bytes) -> None:
        nonlocal mutated, checks
        check(fd, transaction)
        if not inside or mutated:
            return
        checks += 1
        if checks != 2:
            return
        mutated = True
        target = parent / ".replacement-connector.json"
        if attack == "journal":
            fixture.write(parent / "install-transaction.json", transaction + b"\n")
        elif attack == "fixed-file":
            fixture.write(parent / ".replacement-policy.json", b"synthetic-substitution")
        elif attack == "parent-swap":
            parent.rename(parent.with_name("previous-configs"))
            parent.mkdir(mode=0o755)
        elif attack == "leaf-swap":
            target.rename(target.with_name("previous-config"))
            target.symlink_to(fixture.token)
        else:
            fixture.write(parent / "foreign", b"synthetic-foreign")
        snapshot.update(staged_inventory(fixture))
        calls.extend(manager.calls)

    monkeypatch.setattr(fixture.installer, "_remove_replacement_entry", enter)
    monkeypatch.setattr(fixture.installer, "_check_replacement_transaction", checked)
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert mutated, "late config removal boundary was not reached"
    assert staged_inventory(fixture) == snapshot
    assert all(call[0] == "show" for call in manager.calls[len(calls):])


def configs_removed_with_units(fixture: Fixture, case: str = "failed") -> tuple[LifecycleManager, Path, bytes]:
    manager, parent, prepared = state_removed_with_configs(fixture, case)
    with pytest.raises(module.InstallerError, match=CONFIG_REMOVAL_BOUNDARY):
        fixture.installer.replace_failed()
    unit_parent = fixture.root / "etc/systemd/system"
    for _, relative, kind in UNIT_REMOVALS:
        if kind == "directory":
            (fixture.root / relative).mkdir(mode=0o755, exist_ok=True)
    for folder, name in (("multi-user.target.wants", module.UNITS[2]),
                         (module.UNITS[2] + ".wants", module.UNITS[3])):
        directory = unit_parent / folder
        directory.mkdir(mode=0o755, exist_ok=True)
        (directory / name).symlink_to("../" + name)
    manager.calls.clear()
    return manager, parent, prepared


@pytest.mark.parametrize("case", ["failed", "partial", "modern-failed"])
@pytest.mark.parametrize("layout", ["source", "deb"])
def test_replacement_removes_only_owned_units(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, case: str, layout: str,
) -> None:
    fixture.installer.layout = layout
    fixture.installer.units = module.managed_units(layout)
    if layout == "deb":
        payload = fixture.root / "opt/happyranch-desktop"
        for directory in (payload / "bin", payload / "python/bin"):
            directory.mkdir(parents=True, mode=0o755)
        for directory in (fixture.root / "opt", payload, payload / "python"):
            directory.chmod(0o755)
        for name in ("bin/happyranch-tsnet-sidecar", "python/bin/python3.12"):
            path = payload / name
            path.write_bytes(b"synthetic packaged executable")
            path.chmod(0o755)
        (payload / "python/bin/python3").symlink_to("python3.12")
        monkeypatch.setattr(os, "getgrouplist", lambda *args: [os.getgid()])
    manager, parent, prepared = configs_removed_with_units(fixture, case)
    for relative in ("opt/happyranch/operator-data", "opt/happyranch-desktop/keep", "etc/systemd/system/custom.service",
                     "etc/systemd/system/happyranch.service", "etc/systemd/system/multi-user.target.wants/custom.service"):
        (fixture.root / relative).parent.mkdir(parents=True, exist_ok=True)
        fixture.write(fixture.root / relative, b"synthetic-unrelated-data")
    before = staged_inventory(fixture)
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert json.loads((parent / "install-transaction.json").read_bytes()) == {
        **json.loads(prepared), "phase": "replacement-units-removed"}
    after = staged_inventory(fixture)
    allowed = {relative for _, relative, _ in UNIT_REMOVALS} | {
        "etc/systemd/system", "etc/systemd/system/multi-user.target.wants",
        "etc/happyranch", "etc/happyranch/install-transaction.json"}
    changed = {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)}
    assert changed <= allowed
    assert all(not (fixture.root / relative).exists() for _, relative, _ in UNIT_REMOVALS)
    assert all(state == "inactive" for state in manager.states.values())
    assert all(call[0] in {"show", "stop"} and call[1] in module.UNITS for call in manager.calls)
    with pytest.raises(module.InstallerError, match=STAGING_BOUNDARY):
        fixture.installer.replace_failed()
    assert fixture.installer.inventory().state == "manual-install-conflict"
    assert fixture.installer.replacement_inventory().installation.detail == "replacement-staged"


@pytest.mark.parametrize("phase,relative,kind,point", [
    (phase, relative, kind, point) for phase, relative, kind in UNIT_REMOVALS for point in ("intent", "remove", "fsync")
] + [("replacement-units-removed", "", "", "intent")])
def test_replacement_unit_removal_interruption_resumes(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, phase: str, relative: str, kind: str, point: str,
) -> None:
    manager, parent, prepared = configs_removed_with_units(fixture, "modern-failed")
    write, unlink, rmdir, fsync = fixture.installer._write_provision_file, os.unlink, os.rmdir, os.fsync
    interrupted = False

    def die() -> None:
        nonlocal interrupted
        interrupted = True
        raise SystemExit("synthetic-unit-removal-death")

    def publish(name: str, data: bytes, *args: object, **kwargs: object) -> None:
        write(name, data, *args, **kwargs)
        if json.loads(data)["phase"] == phase and point == "intent":
            die()

    def remove(name: str, *, dir_fd: int) -> None:
        (rmdir if kind == "directory" else unlink)(name, dir_fd=dir_fd)
        if point == "remove" and str(name) == Path(relative).name:
            if json.loads((parent / "install-transaction.json").read_bytes())["phase"] == phase:
                die()

    def sync(fd: int) -> None:
        fsync(fd)
        if point == "fsync" and json.loads((parent / "install-transaction.json").read_bytes())["phase"] == phase:
            target = fixture.root / relative
            if not target.exists() and os.fstat(fd).st_ino == target.parent.stat().st_ino:
                die()

    with monkeypatch.context() as patch:
        patch.setattr(fixture.installer, "_write_provision_file", publish)
        patch.setattr(os, "unlink" if kind != "directory" else "rmdir", remove)
        patch.setattr(os, "fsync", sync)
        with pytest.raises((module.InstallerError, SystemExit)):
            fixture.installer.replace_failed()
    assert interrupted, "requested unit removal boundary was not reached"
    journal = (parent / "install-transaction.json").read_bytes()
    assert json.loads(journal) == {**json.loads(prepared), "phase": phase}
    previous = fixture.installer
    fixture.installer = module.ManagedInstaller(
        root=fixture.root, root_owner=previous.root_owner, layout=previous.layout, manager=manager,
        lookup_user=previous._lookup_user, lookup_group=previous._lookup_group)
    observed = fixture.installer.replacement_inventory()
    assert observed.installation.detail == phase
    assert observed.journal_generation == hashlib.sha256(journal).hexdigest()
    with pytest.raises(module.InstallerError, match=STAGING_BOUNDARY if phase == "replacement-units-removed" else UNIT_REMOVAL_BOUNDARY):
        fixture.installer.replace_failed()
    assert json.loads((parent / "install-transaction.json").read_bytes())["phase"] == (
        "replacement-staged" if phase == "replacement-units-removed" else "replacement-units-removed")
    assert all(not (fixture.root / relative).exists() for _, relative, _ in UNIT_REMOVALS)
    assert all(state == "inactive" for state in manager.states.values())


@pytest.mark.parametrize("phase", UNIT_REMOVAL_PHASES)
@pytest.mark.parametrize("point", ["pending-fsync", "rename", "parent-fsync"])
def test_replacement_unit_removal_publication_interruption(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, phase: str, point: str,
) -> None:
    manager, parent, prepared = configs_removed_with_units(fixture)
    sync, rename = os.fsync, fixture.installer._rename_provision_entry
    interrupted = False

    def die() -> None:
        nonlocal interrupted
        interrupted = True
        raise SystemExit("synthetic-unit-publication-death")

    def fsync(fd: int) -> None:
        sync(fd)
        pending, current = parent / ".install-transaction.json.pending", parent / "install-transaction.json"
        if point == "pending-fsync" and pending.exists() and json.loads(pending.read_bytes())["phase"] == phase:
            if os.fstat(fd).st_ino == pending.stat().st_ino:
                die()
        if point == "parent-fsync" and json.loads(current.read_bytes())["phase"] == phase:
            if os.fstat(fd).st_ino == parent.stat().st_ino:
                die()

    def replace(fd: int, source: str, destination: str, *, updating: bool = False) -> None:
        rename(fd, source, destination, updating=updating)
        if point == "rename" and json.loads((parent / destination).read_bytes())["phase"] == phase:
            die()

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", fsync)
        patch.setattr(fixture.installer, "_rename_provision_entry", replace)
        with pytest.raises((SystemExit, module.InstallerError)):
            fixture.installer.replace_failed()
    assert interrupted, "requested unit publication boundary was not reached"
    phases = ["replacement-configs-removed", *UNIT_REMOVAL_PHASES]
    expected = phases[phases.index(phase) - 1] if point == "pending-fsync" else phase
    assert json.loads((parent / "install-transaction.json").read_bytes()) == {**json.loads(prepared), "phase": expected}
    previous = fixture.installer
    fixture.installer = module.ManagedInstaller(
        root=fixture.root, root_owner=previous.root_owner, manager=manager,
        lookup_user=previous._lookup_user, lookup_group=previous._lookup_group)
    assert fixture.installer.replacement_inventory().installation.detail == expected
    with pytest.raises(module.InstallerError, match=STAGING_BOUNDARY if expected == "replacement-units-removed" else UNIT_REMOVAL_BOUNDARY):
        fixture.installer.replace_failed()


@pytest.mark.parametrize("bad", ["journal", "pending", "reappeared-config", "reappeared-state", "unit-bytes",
                                 "unit-mode", "unit-hardlink", "unit-symlink", "dropin", "unknown-wants", "wrong-link",
                                 "directory-symlink", "directory-mode", "token-mode", "token-owner", "stop-error", "pid"])
def test_replacement_unit_removal_refuses_unsafe_inventory(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, bad: str,
) -> None:
    manager, parent, _ = configs_removed_with_units(fixture)
    units = fixture.root / "etc/systemd/system"
    target = units / module.UNITS[0]
    if bad == "journal":
        journal = parent / "install-transaction.json"
        journal.write_bytes(journal.read_bytes() + b"\n")
    elif bad == "pending":
        fixture.write(parent / ".install-transaction.json.pending", b"{}")
    elif bad.startswith("reappeared"):
        fixture.write(parent / ".replacement-connector.json" if bad.endswith("config") else fixture.root / REMOVALS[0][1], b"reappeared")
    elif bad == "unit-bytes":
        target.write_bytes(b"synthetic-foreign-unit")
    elif bad == "unit-mode":
        target.chmod(0o666)
    elif bad == "unit-hardlink":
        os.link(target, fixture.inputs / "unit-alias")
    elif bad == "unit-symlink":
        target.unlink()
        target.symlink_to(fixture.token)
    elif bad in {"dropin", "unknown-wants"}:
        fixture.write(units / (module.UNITS[0] + ".d" if bad == "dropin" else module.UNITS[2] + ".wants") / "foreign", b"foreign")
    elif bad == "wrong-link":
        target = units / "multi-user.target.wants" / module.UNITS[2]
        target.unlink()
        target.symlink_to(fixture.token)
    elif bad.startswith("directory"):
        target = units / (module.UNITS[0] + ".d")
        if bad.endswith("symlink"):
            target.rmdir()
            target.symlink_to(fixture.inputs, target_is_directory=True)
        else:
            target.chmod(0o777)
    elif bad == "token-mode":
        fixture.token.chmod(0o640)
    elif bad == "token-owner":
        info, fstat = fixture.token.stat(), os.fstat
        def foreign(fd: int) -> object:
            current = fstat(fd)
            if (current.st_dev, current.st_ino) == (info.st_dev, info.st_ino):
                values = {key: getattr(current, key) for key in dir(current) if key.startswith("st_")}
                return SimpleNamespace(**{**values, "st_uid": current.st_uid + 1})
            return current
        monkeypatch.setattr(os, "fstat", foreign)
    elif bad == "stop-error":
        manager.stop_error = module.UNITS[1]
    else:
        manager.override["MainPID"] = "42"
    before = staged_inventory(fixture)
    manager.calls.clear()
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert staged_inventory(fixture) == before
    assert all(call[0] in ({"show", "stop"} if bad in {"stop-error", "pid"} else {"show"}) for call in manager.calls)


@pytest.mark.parametrize("attack", ["journal", "token", "config-parent", "unit-parent", "leaf", "metadata", "foreign-entry", "pid"])
@pytest.mark.parametrize("kind", ["unit", "directory", "link"])
def test_replacement_unit_removal_rechecks_before_effect(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, attack: str, kind: str,
) -> None:
    manager, parent, _ = configs_removed_with_units(fixture)
    phase, relative, _ = next(entry for entry in UNIT_REMOVALS if entry[2] == kind)
    # Reach this current intent using the real consumer, never fabricated phase authority.
    write = fixture.installer._write_provision_file
    def stop_at(name: str, data: bytes, *args: object, **kwargs: object) -> None:
        write(name, data, *args, **kwargs)
        if json.loads(data)["phase"] == phase:
            raise SystemExit("synthetic-current-intent")
    with monkeypatch.context() as patch:
        patch.setattr(fixture.installer, "_write_provision_file", stop_at)
        with pytest.raises((SystemExit, module.InstallerError)):
            fixture.installer.replace_failed()
    assert json.loads((parent / "install-transaction.json").read_bytes())["phase"] == phase
    check, remove = fixture.installer._check_replacement_transaction, fixture.installer._remove_replacement_unit
    inside, checks, mutated = False, 0, False
    snapshot: dict = {}

    def enter(transaction: bytes) -> None:
        nonlocal inside
        inside = True
        remove(transaction)

    def checked(fd: int, transaction: bytes) -> None:
        nonlocal mutated, checks
        check(fd, transaction)
        if not inside or mutated:
            return
        checks += 1
        if checks != 2:
            return
        mutated = True
        target = fixture.root / relative
        if attack == "journal":
            fixture.write(parent / "install-transaction.json", transaction + b"\n")
        elif attack == "token":
            fixture.token.chmod(0o640)
        elif attack in {"config-parent", "unit-parent"}:
            folder = parent if attack == "config-parent" else target.parent
            folder.rename(folder.with_name("previous-parent"))
            folder.mkdir(mode=0o755)
        elif attack == "leaf":
            target.rename(target.with_name("previous-leaf"))
            target.symlink_to(fixture.token)
        elif attack == "metadata":
            if kind == "link":
                target.unlink()
                target.symlink_to(fixture.token)
            else:
                target.chmod(0o777)
        elif attack == "foreign-entry":
            fixture.write((target if kind == "directory" else parent) / "foreign", b"synthetic-foreign")
        else:
            manager.override["MainPID"] = "42"
        snapshot.update(staged_inventory(fixture))

    monkeypatch.setattr(fixture.installer, "_remove_replacement_unit", enter)
    monkeypatch.setattr(fixture.installer, "_check_replacement_transaction", checked)
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert mutated, "late unit removal boundary was not reached"
    assert staged_inventory(fixture) == snapshot
    assert json.loads((parent if attack != "config-parent" else parent.with_name("previous-parent"))
                      .joinpath("install-transaction.json").read_bytes())["phase"] == phase


@pytest.mark.parametrize("bad", ["none", "foreign-fragment", "dropins", "active", "pid", "job", "cgroup"])
def test_replacement_unit_removal_accepts_only_quiescent_cached_fragments(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, bad: str,
) -> None:
    manager, parent, _ = configs_removed_with_units(fixture)
    show = manager.show_properties
    observed: dict = {}
    def cached(unit: str, properties: tuple[str, ...]) -> dict[str, str]:
        values = show(unit, properties)
        if not (fixture.root / "etc/systemd/system" / unit).exists():
            if not observed:
                observed.update(staged_inventory(fixture))
            for key, value in {"FragmentPath": str(fixture.root / "etc/systemd/system" / unit),
                               "LoadState": "loaded", "NeedDaemonReload": "yes"}.items():
                if key in properties:
                    values[key] = value
            overrides = {"foreign-fragment": ("FragmentPath", "/usr/lib/systemd/system/foreign.service"),
                         "dropins": ("DropInPaths", "/foreign.conf"), "active": ("ActiveState", "active"),
                         "pid": ("MainPID", "42"), "job": ("Job", "42/start"), "cgroup": ("ControlGroup", "/foreign")}
            if bad in overrides:
                key, value = overrides[bad]
                if key in properties:
                    values[key] = value
        return values
    monkeypatch.setattr(manager, "show_properties", cached)
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert observed, "cached fragment boundary was not reached"
    assert json.loads((parent / "install-transaction.json").read_bytes())["phase"] == (
        "replacement-units-removed" if bad == "none" else "replacement-remove-unit-connector")
    if bad != "none":
        assert staged_inventory(fixture) == observed


def units_removed_for_staging(fixture: Fixture, case: str = "failed") -> tuple[LifecycleManager, Path]:
    manager, parent, _ = configs_removed_with_units(fixture, case)
    with pytest.raises(module.InstallerError, match=UNIT_REMOVAL_BOUNDARY):
        fixture.installer.replace_failed()
    manager.calls.clear()
    return manager, parent


def fresh_staging_instance(fixture: Fixture) -> None:
    previous = fixture.installer
    fixture.installer = module.ManagedInstaller(
        root=fixture.root, root_owner=previous.root_owner, layout=previous.layout, manager=previous.manager,
        lookup_user=previous._lookup_user, lookup_group=previous._lookup_group)


def expire_staged_policy(fixture: Fixture, monkeypatch: pytest.MonkeyPatch) -> datetime:
    policy = json.loads((fixture.root / "etc/happyranch/policy.json").read_bytes())
    assert policy["max_age_seconds"] == 3600
    delayed = datetime.fromisoformat(policy["issued_at"]) + timedelta(seconds=3601)
    class DelayedClock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return delayed if tz is None else delayed.astimezone(tz)
    monkeypatch.setattr(module, "datetime", DelayedClock)
    return delayed


@pytest.mark.parametrize("case", ["failed", "partial", "modern-failed"])
def test_replacement_stages_fresh_inert_generation(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    manager, parent = units_removed_for_staging(fixture, case)
    old_journal = json.loads((parent / "install-transaction.json").read_bytes())
    # Use a rotated SOURCE token to distinguish copying it from reusing the old managed copy.
    fixture.write(fixture.token, b"synthetic-new-release-token\n")
    read = fixture.installer._read_file
    reads: list[str] = []
    def observe(fd: int, name: str, **kwargs: object) -> bytes:
        reads.append(name)
        return read(fd, name, **kwargs)
    monkeypatch.setattr(fixture.installer, "_read_file", observe)
    with pytest.raises(module.InstallerError, match=STAGING_BOUNDARY):
        fixture.installer.replace_failed()
    connector = json.loads((parent / "connector.json").read_bytes())
    sidecar = json.loads((parent / "sidecar.json").read_bytes())
    policy = json.loads((parent / "policy.json").read_bytes())
    import uuid
    ids = [connector[key] for key in ("tenant_id", "home_id", "connector_id")] + [connector["managed"]["control_plane_id"]]
    assert all(uuid.UUID(hex=value).version == 4 and uuid.UUID(hex=value).hex == value for value in ids)
    assert len(set(ids)) == 4
    assert connector["managed"]["profile"] == "linux-managed-identity-v1"
    assert module.SidecarUnitSpec().home_transport is False
    assert "daemon_port" not in connector
    assert sidecar["RoleIdentity"] == "home-sidecar-" + connector["home_id"]
    assert {"ControlURL", "control_tls", "ExpectedPeer", "ConnectorAddr"}.isdisjoint(sidecar)
    assert policy["artifact"] == json.loads(Path("runtime/remote_access/route_policy.json").read_bytes())
    assert policy["artifact"]["default_behavior"] == "deny_unclassified"
    assert not any(name.startswith(".replacement-") or name.startswith("trust-state") or name == "tailscaled.state"
                   or name == "enrollment.key" for name in reads)
    assert (parent / "daemon.token").read_bytes() == fixture.token.read_bytes()
    journal = json.loads((parent / "install-transaction.json").read_bytes())
    assert journal.keys() == old_journal.keys() == {"version", "layout", "phase", "digests"}
    assert journal["version"] == 1 and journal["phase"] == "replacement-staged"
    assert journal["digests"] == {name: hashlib.sha256((parent / name).read_bytes()).hexdigest() for name in old_journal["digests"]}
    provision = json.loads((parent / "provision.json").read_bytes())
    assert provision["phase"] == "provisioned" and provision["version"] == 1
    assert provision["digests"] == {name: digest for name, digest in journal["digests"].items() if name != "provision.json"}
    assert all(not (fixture.root / relative).exists() for _, relative in REMOVALS + CONFIG_REMOVALS)
    assert all(not (fixture.root / relative).exists() for _, relative, _ in UNIT_REMOVALS)
    assert all(call[0] in {"show", "stop"} for call in manager.calls)
    fresh_staging_instance(fixture)
    assert fixture.installer.replacement_inventory().installation.detail == "replacement-staged"
    assert fixture.installer.inventory().state == "manual-install-conflict"
    before = staged_inventory(fixture)
    # Recovery may retain expired bytes, but neither policy readiness nor any
    # activation route can use them. Inspect the actual current_policy gate so
    # an unrelated missing trust/credential gate cannot satisfy this assertion.
    from runtime.remote_access.readiness import ConnectorReadiness
    config = ConnectorConfig.from_file(parent / "connector.json")
    config.policy_path = str(parent / "policy.json")
    supervisor = ConnectorSupervisor(config=config, manager=manager)
    policy = supervisor.load_policy()
    readiness = ConnectorReadiness(
        daemon_port=None, credential_provider=SimpleNamespace(read_bearer=lambda: b"synthetic"),
        policy=policy, configured_identity=None, state_store=None)
    assert readiness.evaluate(datetime.now(timezone.utc)).gates["current_policy"].ok
    delayed = expire_staged_policy(fixture, monkeypatch)
    report = readiness.evaluate(delayed)
    assert not report.ready and not report.gates["current_policy"].ok
    assert report.gates["current_policy"].category == "policy_stale"
    with pytest.raises(module.InstallerError, match="current policy provisioning"):
        fixture.installer._check_provisioning()
    fresh_staging_instance(fixture)
    fixture.installer.replace_failed()
    assert_closed_replacement(fixture, before)
    with pytest.raises(module.InstallerError, match="current policy provisioning"):
        fixture.installer._check_provisioning()
    for operation in (fixture.installer.install, fixture.installer._write_units,
                      supervisor.install, supervisor.start, supervisor.restart, supervisor.enable, supervisor.run):
        with pytest.raises((module.InstallerError, ConnectorConfigError),
                           match="^home control binding not provisioned; requires slice 3b-2$"):
            operation()
    completed = staged_inventory(fixture)
    manager.calls.clear()
    fresh_staging_instance(fixture)
    with pytest.raises(module.InstallerError, match="^replacement refused: modern:$"):
        fixture.installer.replace_failed()
    assert staged_inventory(fixture) == completed
    assert all(call[0] == "show" and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("phase,point,delay", [(phase, point, 0) for phase in STAGING_PHASES for point in
    ("intent", "pending-fsync", "rename", "parent-fsync")] + [
    (phase, "intent", 3601) for phase in ("replacement-stage-token", "replacement-stage-provision", "replacement-staged")] + [
    (phase, point, 3601) for phase in CLOSURE_PHASES for point in ("intent", "pending-fsync", "rename", "parent-fsync")])
def test_replacement_staging_publication_resumes(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, phase: str, point: str, delay: int) -> None:
    manager, parent = units_removed_for_staging(fixture)
    if phase in CLOSURE_PHASES:
        with pytest.raises(module.InstallerError, match=STAGING_BOUNDARY):
            fixture.installer.replace_failed()
    write, rename, fsync = fixture.installer._write_provision_file, fixture.installer._rename_provision_entry, os.fsync
    interrupted = False
    def die() -> None:
        nonlocal interrupted
        interrupted = True
        raise SystemExit("synthetic-staging-death")
    def publish(name: str, data: bytes, *args: object, **kwargs: object) -> None:
        write(name, data, *args, **kwargs)
        if name == "install-transaction.json" and json.loads(data)["phase"] == phase and point == "intent":
            die()
    def replace(fd: int, source: str, target: str, *, updating: bool = False) -> None:
        rename(fd, source, target, updating=updating)
        if target == "install-transaction.json" and json.loads((parent / target).read_bytes())["phase"] == phase and point == "rename":
            die()
    def sync(fd: int) -> None:
        fsync(fd)
        pending = parent / ".install-transaction.json.pending"
        if point == "pending-fsync" and pending.exists() and os.fstat(fd).st_ino == pending.stat().st_ino:
            if json.loads(pending.read_bytes())["phase"] == phase:
                die()
        if point == "parent-fsync" and os.fstat(fd).st_ino == parent.stat().st_ino:
            if json.loads((parent / "install-transaction.json").read_bytes())["phase"] == phase:
                die()
    with monkeypatch.context() as patch:
        patch.setattr(fixture.installer, "_write_provision_file", publish)
        patch.setattr(fixture.installer, "_rename_provision_entry", replace)
        patch.setattr(os, "fsync", sync)
        with pytest.raises((module.InstallerError, SystemExit)):
            fixture.installer.replace_failed()
    assert interrupted, "requested staging publication boundary was not reached"
    if delay:
        expire_staged_policy(fixture, monkeypatch)
    before = staged_inventory(fixture)
    external = {path: path.read_bytes() for base in (fixture.home, fixture.inputs) for path in base.iterdir()}
    fresh_staging_instance(fixture)
    assert fixture.installer.replacement_inventory().journal_generation == hashlib.sha256((parent / "install-transaction.json").read_bytes()).hexdigest()
    if json.loads(before["etc/happyranch/install-transaction.json"][-1])["phase"] in ["replacement-staged", *CLOSURE_PHASES]:
        fixture.installer.replace_failed()
        assert_closed_replacement(fixture, before)
        completed = staged_inventory(fixture)
        manager.calls.clear()
        fresh_staging_instance(fixture)
        with pytest.raises(module.InstallerError, match="^replacement refused: modern:$"):
            fixture.installer.replace_failed()
        assert staged_inventory(fixture) == completed
        assert all(call[0] == "show" and call[1] in module.UNITS for call in manager.calls)
    else:
        with pytest.raises(module.InstallerError, match=STAGING_BOUNDARY):
            fixture.installer.replace_failed()
        assert json.loads((parent / "install-transaction.json").read_bytes())["phase"] == "replacement-staged"
        assert_staging_preserved(fixture, before)
    assert {path: path.read_bytes() for path in external} == external
    assert all(call[0] in {"show", "stop", "reload"} for call in manager.calls)


@pytest.mark.parametrize("phase,name,point,delay,damage", [(phase, name, point, 0, "intact") for phase, name in STAGING_FILES
    for point in ("pending-fsync", "rename", "parent-fsync")] + [
    ("replacement-discard-" + label, name, point, 0, "intact") for label, name in (("token", "daemon.token"), ("provision", "provision.json"))
    for point in ("remove", "parent-fsync")] + [
    ("replacement-stage-policy", "policy.json", "parent-fsync", 3601, damage)
    for damage in ("intact", "schema", "artifact", "digest", "unjournaled")])
def test_replacement_staging_file_effect_resumes(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, phase: str, name: str, point: str, delay: int, damage: str) -> None:
    manager, parent = units_removed_for_staging(fixture)
    rename, fsync, unlink = fixture.installer._rename_provision_entry, os.fsync, os.unlink
    interrupted = False
    def die() -> None:
        nonlocal interrupted
        interrupted = True
        raise SystemExit("synthetic-staging-file-death")
    def sync(fd: int) -> None:
        fsync(fd)
        if json.loads((parent / "install-transaction.json").read_bytes())["phase"] != phase:
            return
        target = parent / ("." + name + ".pending" if point == "pending-fsync" else name)
        if (point == "pending-fsync" and target.exists() and os.fstat(fd).st_ino == target.stat().st_ino
                or point == "parent-fsync" and target.exists() == (not phase.startswith("replacement-discard-"))
                and os.fstat(fd).st_ino == parent.stat().st_ino):
            die()
    def remove(target: str, *, dir_fd: int) -> None:
        unlink(target, dir_fd=dir_fd)
        if target == name and point == "remove" and json.loads((parent / "install-transaction.json").read_bytes())["phase"] == phase:
            die()
    def replace(fd: int, source: str, target: str, *, updating: bool = False) -> None:
        rename(fd, source, target, updating=updating)
        if target == name and point == "rename":
            die()
    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", sync)
        patch.setattr(fixture.installer, "_rename_provision_entry", replace)
        patch.setattr(os, "unlink", remove)
        with pytest.raises((module.InstallerError, SystemExit)):
            fixture.installer.replace_failed()
    assert interrupted, "requested fresh file boundary was not reached"
    if delay:
        expire_staged_policy(fixture, monkeypatch)
    if damage != "intact":
        journal_path = parent / "install-transaction.json"
        journal = json.loads(journal_path.read_bytes())
        policy_path = parent / "policy.json"
        policy = json.loads(policy_path.read_bytes())
        if damage == "unjournaled":
            journal_path.unlink()
        elif damage == "digest":
            policy_path.write_bytes(policy_path.read_bytes() + b"\n")
        else:
            if damage == "schema":
                policy["issued_at"] = "invalid"
            else:
                policy["artifact"]["default_behavior"] = "allow_unclassified"
            policy_path.write_bytes(json.dumps(policy).encode())
            # Bind the malformed bytes to isolate schema/artifact validation
            # from the independently tested exact-digest refusal.
            journal["digests"]["policy.json"] = hashlib.sha256(policy_path.read_bytes()).hexdigest()
            journal_path.write_bytes(json.dumps(journal, sort_keys=True).encode())
    before = staged_inventory(fixture)
    existing = (parent / name).read_bytes() if (parent / name).exists() else None
    external = {path: path.read_bytes() for base in (fixture.home, fixture.inputs) for path in base.iterdir()}
    fresh_staging_instance(fixture)
    with pytest.raises(module.InstallerError, match=STAGING_BOUNDARY if damage == "intact" else
                       "replacement refused|unjournaled replacement"):
        fixture.installer.replace_failed()
    if damage != "intact":
        assert fixture.installer.replacement_inventory().journal_generation is None
        assert staged_inventory(fixture) == before
        assert all(call[0] in {"show", "stop"} for call in manager.calls)
        return
    if existing is not None:
        assert (parent / name).read_bytes() == existing
    assert json.loads((parent / "install-transaction.json").read_bytes())["phase"] == "replacement-staged"
    assert_staging_preserved(fixture, before)
    assert {path: path.read_bytes() for path in external} == external
    assert all(call[0] in {"show", "stop"} for call in manager.calls)


def assert_staging_preserved(fixture: Fixture, before: dict[str, tuple]) -> None:
    after = staged_inventory(fixture)
    # Atomic publication changes parent times and the transaction itself. Every
    # already published payload, identity and byte outside that record is fixed.
    mutable = {"etc/happyranch", "etc/happyranch/install-transaction.json"}
    phase = json.loads(before["etc/happyranch/install-transaction.json"][-1])["phase"]
    current = -1 if phase == "replacement-units-removed" else STAGING_PHASES.index(phase)
    for phase, name in (("replacement-discard-token", "daemon.token"), ("replacement-discard-provision", "provision.json")):
        if current <= STAGING_PHASES.index(phase):
            mutable.add("etc/happyranch/" + name)
    for path, value in before.items():
        if path not in mutable:
            assert after[path] == value, path
    assert set(after) - set(before) <= {"etc/happyranch/" + name for _, name in STAGING_FILES}


@pytest.mark.parametrize("attack", ["journal", "config", "state", "enrollment", "pending", "parent", "token-mode",
                                  "pending-mode", "target-symlink", "target-hardlink", "pid", "job", "foreign-fragment"])
def test_replacement_staging_rechecks_before_publication(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, attack: str) -> None:
    manager, parent = units_removed_for_staging(fixture)
    fsync = os.fsync
    attacked: dict = {}
    def sync(fd: int) -> None:
        fsync(fd)
        pending = parent / ".connector.json.pending"
        if attacked or not pending.exists() or os.fstat(fd).st_ino != pending.stat().st_ino:
            return
        if attack == "journal":
            journal = parent / "install-transaction.json"
            fixture.write(journal, journal.read_bytes() + b"\n")
        elif attack == "config":
            fixture.write(parent / "unknown.json", b"synthetic-foreign")
        elif attack == "state":
            fixture.write(fixture.root / "var/lib/happyranch-connector/trust-state.json", b"synthetic-retired")
        elif attack == "enrollment":
            fixture.write(parent / "enrollment.key", b"synthetic-retired")
        elif attack == "pending":
            pending.rename(parent / "foreign-pending")
            fixture.write(pending, b"synthetic-substitution")
        elif attack == "parent":
            parent.rename(parent.with_name("moved-config"))
            parent.mkdir(mode=0o755)
        elif attack == "token-mode":
            fixture.token.chmod(0o644)
        elif attack == "pending-mode":
            pending.chmod(0o644)
        elif attack == "target-symlink":
            (parent / "connector.json").symlink_to(fixture.inputs / "connector.json")
        elif attack == "target-hardlink":
            os.link(fixture.inputs / "connector.json", parent / "connector.json")
        else:
            manager.override.update({"pid": {"MainPID": "42"}, "job": {"Job": "42 start"},
                                     "foreign-fragment": {"FragmentPath": "/foreign/unit"}}[attack])
        attacked.update(staged_inventory(fixture))
    monkeypatch.setattr(os, "fsync", sync)
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert attacked, "late fresh publication boundary was not reached"
    # Only the writer's exclusive pending inode and directory times can change
    # during its existing exception cleanup. Every foreign entry is retained.
    def retained(snapshot: dict) -> dict:
        return {key: value[:6] if value[2] & 0o170000 == 0o040000 else value
                for key, value in snapshot.items()
                if not (key.endswith("/.connector.json.pending") and attack != "pending")}
    assert retained(staged_inventory(fixture)) == retained(attacked)
    if attack == "pending":
        assert (parent / ".connector.json.pending").read_bytes() == b"synthetic-substitution"
    assert all(call[0] in {"show", "stop"} for call in manager.calls)


def staged_for_closure(fixture: Fixture) -> tuple[LifecycleManager, Path]:
    manager, parent = units_removed_for_staging(fixture)
    with pytest.raises(module.InstallerError, match=STAGING_BOUNDARY):
        fixture.installer.replace_failed()
    # This is the literal retained v1 terminal journal from prefix b: no new
    # key, migration, original-ID recovery or client-provided authority.
    record = json.loads((parent / "install-transaction.json").read_bytes())
    assert record.keys() == {"version", "layout", "phase", "digests"}
    assert record["version"] == 1 and record["phase"] == "replacement-staged"
    manager.calls.clear()
    return manager, parent


def assert_closed_replacement(fixture: Fixture, before: dict[str, tuple]) -> None:
    parent = fixture.root / "etc/happyranch"
    after = staged_inventory(fixture)
    journal = "etc/happyranch/install-transaction.json"
    assert journal not in after
    assert set(after) == set(before) - {journal}
    for path, value in before.items():
        if path not in {"etc/happyranch", journal}:
            assert after[path] == value, path
    expected_names = {"connector.json", "sidecar.json", "policy.json", "release-daemon.json", "daemon.token", "provision.json"}
    assert {path.name for path in parent.iterdir()} == expected_names
    # Independent clean-host modern provenance shape and actual fixed-file hashes.
    assert json.loads((parent / "provision.json").read_bytes()) == dict(
        version=1, layout=fixture.installer.layout, phase="provisioned",
        digests={name: hashlib.sha256((parent / name).read_bytes()).hexdigest()
                 for name in expected_names - {"provision.json"}})
    expected = module.InstallInventory("modern", "staged-inert", tuple(sorted(str(parent / name) for name in expected_names)))
    assert fixture.installer.inventory() == expected
    assert fixture.installer.replacement_inventory() == module.ReplacementInventory(expected)


@pytest.mark.parametrize("point", ["reload-before", "reload-after", "reload-error", "unlink-before", "unlink-after", "parent-fsync"])
def test_replacement_closure_effect_resumes(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, point: str) -> None:
    manager, parent = staged_for_closure(fixture)
    before = staged_inventory(fixture)
    journal = parent / "install-transaction.json"
    reload, unlink, fsync = manager.daemon_reload, os.unlink, os.fsync
    interrupted = False
    def die() -> None:
        nonlocal interrupted
        interrupted = True
        raise SystemExit("synthetic-closure-death")
    def reload_units() -> None:
        nonlocal interrupted
        assert json.loads(journal.read_bytes())["phase"] == "replacement-reload"
        if point == "reload-before":
            die()
        reload()
        if point == "reload-after":
            die()
        if point == "reload-error":
            interrupted = True
            raise module.ServiceManagerError("synthetic-manager-private-detail")
    def remove(name: str, *, dir_fd: int) -> None:
        if name == "install-transaction.json":
            assert json.loads(journal.read_bytes())["phase"] == "replacement-retire-journal"
            assert fixture.installer.inventory().state == "manual-install-conflict"
            if point == "unlink-before":
                die()
        unlink(name, dir_fd=dir_fd)
        if name == "install-transaction.json" and point == "unlink-after":
            die()
    def sync(fd: int) -> None:
        fsync(fd)
        if point == "parent-fsync" and not journal.exists() and os.fstat(fd).st_ino == parent.stat().st_ino:
            die()
    with monkeypatch.context() as patch:
        patch.setattr(manager, "daemon_reload", reload_units)
        patch.setattr(os, "unlink", remove)
        patch.setattr(os, "fsync", sync)
        with pytest.raises((module.InstallerError, SystemExit)):
            fixture.installer.replace_failed()
    assert interrupted, "requested closure effect was not reached"
    if point == "reload-error":
        assert journal.exists()
    # Only persisted exact-generation intent is carried into the new instance.
    expire_staged_policy(fixture, monkeypatch)
    fresh_staging_instance(fixture)
    manager.calls.clear()
    if journal.exists():
        fixture.installer.replace_failed()
        if point == "unlink-before":
            assert all(call[0] != "reload" for call in manager.calls)
    else:
        retired = staged_inventory(fixture)
        with pytest.raises(module.InstallerError, match="^replacement refused: modern:$"):
            fixture.installer.replace_failed()
        assert staged_inventory(fixture) == retired
        assert all(call[0] == "show" and call[1] in module.UNITS for call in manager.calls)
    assert_closed_replacement(fixture, before)
    completed = staged_inventory(fixture)
    manager.calls.clear()
    fresh_staging_instance(fixture)
    with pytest.raises(module.InstallerError, match="^replacement refused: modern:$"):
        fixture.installer.replace_failed()
    assert staged_inventory(fixture) == completed
    assert all(call[0] == "show" and call[1] in module.UNITS for call in manager.calls)


@pytest.mark.parametrize("bad", ["none", "cached", "reload-stuck", "unknown", "quarantine", "provision-digest", "policy-schema", "policy-artifact"])
def test_replacement_closure_validates_v1_generation(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    manager, parent = staged_for_closure(fixture)
    journal = parent / "install-transaction.json"
    if bad in {"cached", "reload-stuck"}:
        show, reload = manager.show_properties, manager.daemon_reload
        cached = True
        def properties(unit: str, keys: tuple[str, ...]) -> dict[str, str]:
            values = show(unit, keys)
            if cached:
                replacement = dict(FragmentPath=str(fixture.root / "etc/systemd/system" / unit), LoadState="loaded",
                                   NeedDaemonReload="yes", ActiveState="failed")
                values.update({key: value for key, value in replacement.items() if key in keys})
            return values
        def reload_units() -> None:
            nonlocal cached
            reload()
            if bad != "reload-stuck":
                cached = False
        monkeypatch.setattr(manager, "show_properties", properties)
        monkeypatch.setattr(manager, "daemon_reload", reload_units)
    elif bad == "unknown":
        fixture.write(parent / "unknown.json", b"synthetic-unknown")
    elif bad == "quarantine":
        fixture.write(parent / ".replacement-policy.json", b"synthetic-revoked")
    elif bad == "provision-digest":
        path = parent / "provision.json"
        path.write_bytes(path.read_bytes() + b"\n")
    elif bad.startswith("policy-"):
        path = parent / "policy.json"
        policy = json.loads(path.read_bytes())
        if bad == "policy-schema":
            policy["issued_at"] = "invalid"
        else:
            policy["artifact"]["default_behavior"] = "allow_unclassified"
        path.write_bytes(json.dumps(policy, sort_keys=True).encode())
        # Rebind both provenance and journal so schema/artifact validation,
        # rather than a mismatched digest, must be the refusing owner.
        provision_path = parent / "provision.json"
        provision = json.loads(provision_path.read_bytes())
        provision["digests"]["policy.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
        provision_path.write_bytes(json.dumps(provision, sort_keys=True).encode())
        record = json.loads(journal.read_bytes())
        record["digests"].update({name: hashlib.sha256((parent / name).read_bytes()).hexdigest()
                                  for name in ("policy.json", "provision.json")})
        journal.write_bytes(json.dumps(record, sort_keys=True).encode())
    before = staged_inventory(fixture)
    fresh_staging_instance(fixture)
    if bad in {"none", "cached"}:
        expire_staged_policy(fixture, monkeypatch)
        fixture.installer.replace_failed()
        assert_closed_replacement(fixture, before)
    else:
        with pytest.raises(module.InstallerError):
            fixture.installer.replace_failed()
        assert journal.exists()
        assert_staging_preserved(fixture, before)
        assert all(call[0] in {"show", "stop", "reload"} for call in manager.calls)
        if bad in {"unknown", "quarantine"}:
            name = "unknown.json" if bad == "unknown" else ".replacement-policy.json"
            assert fixture.installer.replacement_inventory().installation.paths == (str(parent / name),)


@pytest.mark.parametrize("attack", ["journal", "config", "state", "enrollment", "pending", "parent", "token-mode",
                                  "journal-mode", "journal-symlink", "journal-hardlink", "pid", "job", "foreign-fragment"])
def test_replacement_closure_rechecks_before_retirement(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, attack: str) -> None:
    manager, parent = staged_for_closure(fixture)
    check = fixture.installer._check_replacement_transaction
    attacked: dict = {}
    def checked(fd: int, transaction: bytes) -> None:
        check(fd, transaction)
        if attacked or json.loads(transaction)["phase"] != "replacement-retire-journal":
            return
        journal = parent / "install-transaction.json"
        if attack == "journal":
            fixture.write(journal, transaction + b"\n")
        elif attack == "config":
            fixture.write(parent / "unknown.json", b"synthetic-foreign")
        elif attack == "state":
            fixture.write(fixture.root / "var/lib/happyranch-connector/trust-state.json", b"synthetic-revoked")
        elif attack == "enrollment":
            fixture.write(parent / "enrollment.key", b"synthetic-revoked")
        elif attack == "pending":
            fixture.write(parent / ".install-transaction.json.pending", b"synthetic-foreign")
        elif attack == "parent":
            parent.rename(parent.with_name("moved-config"))
            parent.mkdir(mode=0o755)
        elif attack == "token-mode":
            fixture.token.chmod(0o644)
        elif attack == "journal-mode":
            journal.chmod(0o644)
        elif attack in {"journal-symlink", "journal-hardlink"}:
            journal.rename(parent / "foreign-journal")
            if attack == "journal-symlink":
                journal.symlink_to(parent / "foreign-journal")
            else:
                os.link(parent / "foreign-journal", journal)
        else:
            manager.override.update({"pid": {"MainPID": "42"}, "job": {"Job": "42 start"},
                                     "foreign-fragment": {"FragmentPath": "/foreign/unit"}}[attack])
        attacked.update(staged_inventory(fixture))
    monkeypatch.setattr(fixture.installer, "_check_replacement_transaction", checked)
    with pytest.raises(module.InstallerError):
        fixture.installer.replace_failed()
    assert attacked, "late closure retirement boundary was not reached"
    assert staged_inventory(fixture) == attacked
    assert all(call[0] in {"show", "stop", "reload"} for call in manager.calls)
