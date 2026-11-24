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
import select
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterator

import pytest

from runtime.remote_access.home_retirement import MESSAGE

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
        self.service_directories()

    def service_directories(self) -> None:
        # Legacy lifecycle fixtures model directories supplied at service start.
        # Clean I5d tests call the production provision method directly.
        for path in self.installer._layout():
            with self.installer._directory(path, create=True):
                pass

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
    """Neutral historical identity input; no generated provisioning owner."""
    io = fixture.installer
    io._service_owner = io.root_owner
    fixture.service_directories()
    parent = fixture.root / "etc/happyranch"
    connector = json.loads((fixture.inputs / "connector.json").read_bytes())
    connector.update(tenant_id="a" * 32, home_id="b" * 32, connector_id="c" * 32,
                     excluded_daemon_ports=[8765])
    connector.pop("daemon_port", None)
    connector["managed"].update(profile="linux-managed-identity-v1", control_plane_id="d" * 32,
                                bind_port=8443)
    sidecar = dict(Profile="linux-managed-identity-v1", StateDir="/var/lib/happyranch-tsnet-sidecar",
        RoleIdentity="home-sidecar-" + connector["home_id"], DERPPolicy="private-only",
        ListenAddr=":443", ExcludedDaemonPorts=[8765])
    release = dict(user=fixture.user, uid=os.getuid(), data_home=str(fixture.home),
        unit="happyranch-daemon.service", fragment_path="/usr/lib/systemd/user/happyranch-daemon.service")
    for name, value, mode in (("connector.json", connector, 0o640), ("sidecar.json", sidecar, 0o640),
                              ("release-daemon.json", release, 0o644)):
        (parent / name).write_bytes(json.dumps(value, sort_keys=True).encode())
        (parent / name).chmod(mode)
    for name, data, mode in (("policy.json", (fixture.inputs / "policy.json").read_bytes(), 0o640),
                             ("daemon.token", fixture.token.read_bytes(), 0o600)):
        (parent / name).write_bytes(data)
        (parent / name).chmod(mode)
    names = ("connector.json", "sidecar.json", "policy.json", "release-daemon.json", "daemon.token")
    record = dict(version=1, layout=io.layout, phase="provisioned", digests={
        name: hashlib.sha256((parent / name).read_bytes()).hexdigest() for name in names})
    fixture.write(parent / "provision.json", json.dumps(record, sort_keys=True).encode())
    return parent


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
    fixture.write(parent / "install-transaction.json", data)
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
        parent = generated_bundle(fixture)
        prefix = ["connector.json"] + (["sidecar.json"] if case in {"before-policy.json", "before-release-daemon.json"} else [])
        if case == "before-release-daemon.json":
            prefix.append("policy.json")
        for path in parent.iterdir():
            if path.name not in prefix:
                path.unlink()
        assert {path.name for path in parent.iterdir()} == set(prefix)
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


@pytest.mark.parametrize("bad", ["symlink", "hardlink", "uid", "gid", "mode-0666", "mode-0700",
                                 "oversize", "nine", "non-numeric", "unknown", "directory", "fifo"])
def test_pre_stop_unsafe_diagnostics_refuse_without_effects(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, bad: str,
) -> None:
    manager = lifecycle_fixture(fixture)
    generated_bundle(fixture)
    inventory_units(fixture, modern=True)
    runtime = fixture.root / "run/happyranch-tsnet-sidecar"
    healthy = runtime / "health.json"
    healthy.write_bytes(b"synthetic diagnostic")
    healthy.chmod(0o644)
    path = runtime / ".health-123"
    fixture.write(path, b"synthetic interrupted diagnostic")
    if bad == "symlink":
        path.unlink()
        path.symlink_to(healthy.name)
    if bad == "hardlink": os.link(path, fixture.root / "held-diagnostic")
    if bad.startswith("mode-"): path.chmod(int(bad.removeprefix("mode-"), 8))
    if bad == "oversize": path.write_bytes(b"x" * 65537)
    if bad == "nine":
        for index in range(7):
            fixture.write(runtime / f".health-{index}", b"synthetic diagnostic")
    if bad == "non-numeric": path = path.rename(runtime / ".health-x")
    if bad == "unknown": path = path.rename(runtime / "unknown.json")
    if bad in {"directory", "fifo"}:
        path.unlink()
        if bad == "directory": path.mkdir(mode=0o600)
        else: os.mkfifo(path, 0o600)
    if bad in {"uid", "gid"}:
        original = os.stat
        identity = path.lstat().st_ino
        def metadata(*args: object, **kwargs: object) -> object:
            info = original(*args, **kwargs)
            if info.st_ino != identity: return info
            value = SimpleNamespace(**{key: getattr(info, key) for key in dir(info) if key.startswith("st_")})
            field = "st_" + bad
            setattr(value, field, getattr(value, field) + 1)
            return value
        monkeypatch.setattr(os, "stat", metadata)
    before = staged_inventory(fixture)
    result = fixture.installer.inventory()
    assert (result.state, result.detail) == ("manual-install-conflict", "unsafe-or-unrecognized")
    if bad == "nine":
        # scandir ordering is unspecified; whichever entry crosses the bound is named.
        assert len(result.paths) == 1 and result.paths[0] in {str(child) for child in runtime.iterdir()}
    else:
        assert result.paths == (str(path),)
    with pytest.raises(module.InstallerError, match="^manual-install-conflict: "):
        fixture.installer.stop_and_confirm()
    assert staged_inventory(fixture) == before
    assert all(call[0] == "show" for call in manager.calls)


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
    with pytest.raises(module.InstallerError, match="^" + re.escape(MESSAGE) + "$"):
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
    assert output.err == "error: " + MESSAGE + "\n"
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
    with pytest.raises(ConnectorConfigError, match="^" + re.escape(MESSAGE) + "$"):
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




@pytest.mark.parametrize("bad", ["omitted", "partial-config", "alternate-token"])
def test_generated_inputs_refuse_before_effects(fixture: Fixture, bad: str) -> None:
    inputs = ({"connector_config": fixture.inputs / "connector.json"} if bad == "partial-config" else
              {"daemon_token_source": fixture.key} if bad == "alternate-token" else {})
    message = "provide all three" if bad == "partial-config" else re.escape(MESSAGE)
    with pytest.raises(module.InstallerError, match=message):
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
    from runtime.remote_access import installer_validation
    monkeypatch.setattr(installer_validation, "datetime", DelayedClock)
    return delayed
























# B14 exercises the existing protected writer and directory flock on real temp files.
def renewal_setup(fixture: Fixture, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, datetime]:
    generated_bundle(fixture)
    # Installed package source is root-owned/non-writable; the Git clone is
    # group-writable. Copy the exact shipped bytes into protected fixture custody.
    source = fixture.inputs / 'route_policy.json'
    source.write_bytes(Path(module.__file__).with_name('route_policy.json').read_bytes())
    source.chmod(0o644)
    monkeypatch.setattr(module, '__file__', str(fixture.inputs / 'installer.py'))
    state = fixture.root / 'var/lib/happyranch-remote-access'
    state.mkdir(parents=True, mode=0o700)
    for parent in (state.parent, state.parent.parent):
        parent.chmod(0o755)
    folder = fixture.root / 'etc/happyranch'
    issued = datetime.fromisoformat(json.loads((folder / 'policy.json').read_bytes())['issued_at'])
    now = issued + timedelta(seconds=1800)
    class Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return now
    monkeypatch.setattr(module, 'datetime', Clock)
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    fixture.manager.calls.clear()
    return folder, now


def renewal_intent(fixture: Fixture, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, bytes]:
    folder, _ = renewal_setup(fixture, monkeypatch)
    with monkeypatch.context() as patcher:
        patcher.setattr(fixture.installer, '_recover_policy_renewal',
                        lambda transaction: (_ for _ in ()).throw(RuntimeError('power loss')))
        with pytest.raises(RuntimeError, match='power loss'):
            fixture.installer.renew_policy()
    return folder, (folder / 'install-transaction.json').read_bytes()


def test_fixed_policy_launcher_publishes_real_renewal(fixture: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy
    from runtime.remote_access import policy_renew

    folder, now = renewal_setup(fixture, monkeypatch)
    before = fixture.inventory()
    def installed(**kwargs: object) -> module.ManagedInstaller:
        assert kwargs == {'layout': 'deb'}
        return fixture.installer
    monkeypatch.setattr(policy_renew, 'ManagedInstaller', installed)
    monkeypatch.setattr(sys, 'argv', ['happyranch-remote-access-policy-renew'])
    executed = []

    class Executed(BaseException):
        pass

    def execute(path: str, argv: tuple[str, ...], env: dict[str, str]) -> None:
        assert path == '/opt/happyranch-desktop/python/bin/python3'
        assert argv == (path, '-I', '-m', 'runtime.remote_access.policy_renew') and env == {}
        executed.append(argv)
        monkeypatch.setattr(sys, 'argv', [str(policy_renew.__file__)])
        assert policy_renew.main() == 0
        raise Executed()

    monkeypatch.setattr(os, 'execve', execute)
    launcher = Path(__file__).resolve().parents[1] / 'packaging/linux/bin/happyranch-remote-access-policy-renew'
    with pytest.raises(Executed):
        runpy.run_path(str(launcher), run_name='__main__')
    assert len(executed) == 1
    policy = json.loads((folder / 'policy.json').read_bytes())
    assert (policy['revision'], policy['issued_at']) == (2, now.isoformat())
    assert json.loads((folder / 'provision.json').read_bytes())['digests']['policy.json'] == hashlib.sha256(
        (folder / 'policy.json').read_bytes()).hexdigest()
    after = fixture.inventory()
    assert all(after[name][1] == value[1] for name, value in before.items())
    assert {name: value for name, value in after.items()
            if name not in ('etc/happyranch/policy.json', 'etc/happyranch/provision.json')} == {
        name: value for name, value in before.items()
        if name not in ('etc/happyranch/policy.json', 'etc/happyranch/provision.json')}
    assert not fixture.manager.calls and not (folder / 'install-transaction.json').exists()


@pytest.mark.parametrize('case', ['complete', 'missing-live-auth', 'stale', 'expired', 'uid',
    'root', 'selector', 'method', 'duplicate', 'oversized', 'policy-mode', 'home-binding',
    'legacy', 'legacy-pending', 'legacy-symlink', 'state-symlink'])
def test_retained_policy_private_child_custody_and_auth(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str,
) -> None:
    from runtime.remote_access import policy_renew_backend as backend
    import time

    folder, now = renewal_setup(fixture, monkeypatch)
    child = backend.PolicyRenewalBackend(fixture.installer)
    snapshot = child.capture(1000)
    request = dict(method='RetryPolicyRenewal', uid=1000, step='renew', expected=snapshot,
                   deadline=time.monotonic() + 30)
    state = fixture.root / 'var/lib/happyranch-remote-access'
    if case == 'stale': request['expected'] += ' '
    if case == 'expired': request['deadline'] = time.monotonic() - 1
    if case == 'uid': request['uid'] = 1001
    if case == 'method': request['method'] = 'Enable'
    if case == 'policy-mode': (folder / 'policy.json').chmod(0o666)
    if case == 'home-binding':
        data = json.loads((folder / 'sidecar.json').read_bytes())
        data['home_binding'] = {'synthetic-old-key': 'never-account-authority'}
        (folder / 'sidecar.json').write_bytes(json.dumps(data).encode())
    if case in ('legacy', 'legacy-pending'):
        name = 'home-activation.json' if case == 'legacy' else '.home-activation.json.pending'
        (state / name).write_bytes(b'synthetic private legacy evidence')
        (state / name).chmod(0o600)
    if case == 'legacy-symlink':
        (state / 'home-activation.json').symlink_to(fixture.token)
    if case == 'state-symlink':
        moved = state.with_name('protected-state-sentinel')
        state.rename(moved)
        state.symlink_to(moved, target_is_directory=True)
    if case in ('home-binding', 'legacy', 'legacy-pending', 'legacy-symlink', 'state-symlink'):
        # Observe the shipping capture boundary itself, before later renewal
        # validation could mask a missing legacy-admission guard.
        request.update(step='capture', expected=None)
    raw = json.dumps(request).encode()
    if case == 'duplicate': raw = raw.replace(b'"uid": 1000', b'"uid": 1000, "uid": 1001')
    if case == 'oversized': raw += b' ' * 4097
    wire = fixture.inputs / 'private-renewal-input'
    wire.write_bytes(raw + b'\n' + (b'' if case == 'missing-live-auth' else b'{"commit":true}\n' * 3))
    wire.chmod(0o600)
    monkeypatch.setattr(sys, 'argv', ['policy_renew_backend.py', *(['--synthetic-selector'] if case == 'selector' else [])])
    monkeypatch.setattr(os, 'geteuid', lambda: 1000 if case == 'root' else 0)

    def installed(*, layout: str, manager: object, run: object) -> module.ManagedInstaller:
        assert layout == 'deb'
        return fixture.installer

    monkeypatch.setattr(backend, 'ManagedInstaller', installed)
    before = fixture.inventory()
    def metadata(path: Path) -> tuple[int, ...]:
        info = path.lstat()
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    token_before = fixture.token.read_bytes(), metadata(fixture.token)
    link_before = metadata(state)
    with wire.open('rb', buffering=0) as stream:
        monkeypatch.setattr(sys, 'stdin', SimpleNamespace(buffer=stream, fileno=stream.fileno))
        assert backend.helper_main() == (0 if case == 'complete' else 1)
    output = capsys.readouterr()
    frames = [json.loads(line) for line in output.out.splitlines()]
    assert output.err == ''
    if case == 'complete':
        assert frames == [{'result': 'ready'}] * 3 + [{'outcome': 'complete'}]
        policy = json.loads((folder / 'policy.json').read_bytes())
        assert (policy['revision'], policy['issued_at']) == (2, now.isoformat())
        assert json.loads((folder / 'provision.json').read_bytes())['digests']['policy.json'] == hashlib.sha256(
            (folder / 'policy.json').read_bytes()).hexdigest()
        after = fixture.inventory()
        assert all(after[name][1] == value[1] for name, value in before.items())
        assert {name: value for name, value in after.items()
                if name not in ('etc/happyranch/policy.json', 'etc/happyranch/provision.json')} == {
            name: value for name, value in before.items()
            if name not in ('etc/happyranch/policy.json', 'etc/happyranch/provision.json')}
    else:
        assert all(frame == {'result': 'ready'} for frame in frames)
        assert fixture.inventory() == before
    assert (fixture.token.read_bytes(), metadata(fixture.token)) == token_before
    assert metadata(state) == link_before
    assert not fixture.manager.calls and not (folder / 'install-transaction.json').exists()


def test_renewal_order_metadata_and_provision_digest(fixture: Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    folder, now = renewal_setup(fixture, monkeypatch)
    before = fixture.inventory()
    old = json.loads((folder / 'provision.json').read_bytes())
    metadata = {name: ((folder / name).stat().st_uid, (folder / name).stat().st_gid,
                       (folder / name).stat().st_mode) for name in ('policy.json', 'provision.json')}
    events = []
    rename = fixture.installer._rename_provision_entry
    def observe(parent: int, source: str, target: str, **kwargs: object) -> None:
        if target != 'install-transaction.json':
            assert (folder / 'install-transaction.json').is_file()
        events.append(target)
        rename(parent, source, target, **kwargs)
    monkeypatch.setattr(fixture.installer, '_rename_provision_entry', observe)
    fixture.installer.renew_policy()
    assert events == ['install-transaction.json', 'policy.json', 'provision.json']
    assert metadata == {name: ((folder / name).stat().st_uid, (folder / name).stat().st_gid,
                              (folder / name).stat().st_mode) for name in metadata}
    policy = json.loads((folder / 'policy.json').read_bytes())
    assert (policy['revision'], datetime.fromisoformat(policy['issued_at']), policy['max_age_seconds']) == (2, now, 3600)
    old['digests']['policy.json'] = hashlib.sha256((folder / 'policy.json').read_bytes()).hexdigest()
    assert (folder / 'provision.json').read_bytes() == json.dumps(old, sort_keys=True).encode()
    after = fixture.inventory()
    for name, (data, mode) in before.items():
        assert after[name][1] == mode
        if name not in ('etc/happyranch/policy.json', 'etc/happyranch/provision.json'):
            assert after[name][0] == data
    assert not (folder / 'install-transaction.json').exists()
    assert fixture.manager.calls == []


@pytest.mark.parametrize('fresh_auth,accepted', [(False, False), (True, True)])
def test_renewal_expiry_modes(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, fresh_auth: bool, accepted: bool) -> None:
    folder, _ = renewal_setup(fixture, monkeypatch)
    data = json.loads((folder / 'policy.json').read_bytes())
    data['issued_at'] = (datetime.fromisoformat(data['issued_at']) - timedelta(hours=2)).isoformat()
    (folder / 'policy.json').write_bytes(json.dumps(data, sort_keys=True).encode())
    record = json.loads((folder / 'provision.json').read_bytes())
    record['digests']['policy.json'] = hashlib.sha256((folder / 'policy.json').read_bytes()).hexdigest()
    (folder / 'provision.json').write_bytes(json.dumps(record, sort_keys=True).encode())
    before = fixture.inventory()
    if accepted:
        fixture.installer.renew_policy(fresh_auth=fresh_auth)
        assert json.loads((folder / 'policy.json').read_bytes())['revision'] == 2
    else:
        with pytest.raises(module.InstallerError):
            fixture.installer.renew_policy(fresh_auth=fresh_auth)
        assert fixture.inventory() == before
    assert not (folder / 'install-transaction.json').exists()
    assert fixture.manager.calls == []


@pytest.mark.parametrize('policy_new,provision_new', [(False, False), (True, False), (False, True), (True, True)])
def test_renewal_recovery_old_or_new_rolls_forward(fixture: Fixture, monkeypatch: pytest.MonkeyPatch,
                                                  policy_new: bool, provision_new: bool) -> None:
    import base64
    folder, transaction = renewal_intent(fixture, monkeypatch)
    record = json.loads(transaction)
    for name, use_new in [('policy.json', policy_new), ('provision.json', provision_new)]:
        if use_new:
            (folder / name).write_bytes(base64.b64decode(record['files'][name]['new_base64']))
    # Replaying durable bytes is allowed after their envelope expires.
    class Later(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return datetime(2099, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(module, 'datetime', Later)
    fixture.installer.renew_policy()
    for name in ('policy.json', 'provision.json'):
        assert (folder / name).read_bytes() == base64.b64decode(record['files'][name]['new_base64'])
    assert not (folder / 'install-transaction.json').exists()
    assert fixture.manager.calls == []


def renewal_pending(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, boundary: str) -> Path:
    """Kill the real writer after its pending fsync; finally must not run."""
    folder, _ = renewal_setup(fixture, monkeypatch)
    rename = fixture.installer._rename_provision_entry
    with monkeypatch.context() as patcher:
        def interrupt(parent: int, source: str, target: str, *, updating: bool = False) -> None:
            if target == boundary:
                os._exit(91)
            rename(parent, source, target, updating=updating)
        patcher.setattr(fixture.installer, '_rename_provision_entry', interrupt)
        child = os.fork()
        if child == 0:
            try:
                fixture.installer.renew_policy()
            except BaseException:
                os._exit(93)
            os._exit(92)
        waited, status = os.waitpid(child, 0)
    assert waited == child and os.waitstatus_to_exitcode(status) == 91
    return folder


@pytest.mark.parametrize('boundary', ['install-transaction.json', 'policy.json', 'provision.json',
                                     'policy.json.pending', 'provision.json.pending'])
def test_renewal_interruption_recovers(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, boundary: str) -> None:
    if boundary.endswith('.pending'):
        import base64
        destination = boundary.removesuffix('.pending')
        folder = renewal_pending(fixture, monkeypatch, destination)
        journal = folder / 'install-transaction.json'
        record = json.loads(journal.read_bytes())
        pending = folder / ('.' + boundary)
        assert pending.read_bytes() == base64.b64decode(record['files'][destination]['new_base64'])
        metadata = {name: ((folder / name).stat().st_uid, (folder / name).stat().st_gid,
                           (folder / name).stat().st_mode) for name in record['files']}
        for name, entry in record['files'].items():
            expected = 'new_sha256' if destination == 'provision.json' and name == 'policy.json' else 'old_sha256'
            assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == entry[expected]
        error = None
        try:
            fixture.installer.renew_policy()
        except Exception as exc:
            error = exc
        assert error is None, f'journal-bound pending must recover: {error!r}'
        for name, entry in record['files'].items():
            assert (folder / name).read_bytes() == base64.b64decode(entry['new_base64'])
            assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == entry['new_sha256']
            info = (folder / name).stat()
            assert (info.st_uid, info.st_gid, info.st_mode) == metadata[name]
        assert not journal.exists()
        assert not any(path.name.endswith('.pending') for path in folder.iterdir())
        assert fixture.manager.calls == []
        return
    folder, _ = renewal_setup(fixture, monkeypatch)
    write = fixture.installer._write_provision_file
    def interrupt(name: str, *args: object, **kwargs: object) -> None:
        write(name, *args, **kwargs)
        if name == boundary:
            raise RuntimeError('power loss')
    with monkeypatch.context() as patcher:
        patcher.setattr(fixture.installer, '_write_provision_file', interrupt)
        with pytest.raises(RuntimeError, match='power loss'):
            fixture.installer.renew_policy()
    assert (folder / 'install-transaction.json').exists()
    fixture.installer.renew_policy()
    assert json.loads((folder / 'policy.json').read_bytes())['revision'] == 2
    assert not (folder / 'install-transaction.json').exists()
    assert fixture.manager.calls == []


_RENEWAL_PENDING_REFUSALS = [f'{destination}:{attack}'
    for destination in ('policy.json', 'provision.json')
    for attack in ('bytes', 'mode', 'owner', 'group', 'hardlink', 'symlink', 'foreign', 'unjournaled')]


@pytest.mark.parametrize('case', ['policy_drift', 'provision_drift', 'malformed', 'extra', 'oversize',
                                   'digest', 'symlink', 'hardlink', 'mode', 'pending', 'other_transaction',
                                   'unjournaled-intent', *_RENEWAL_PENDING_REFUSALS])
def test_renewal_recovery_refuses_and_retains(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    if ':' in case or case == 'unjournaled-intent':
        destination, attack = case.split(':') if ':' in case else ('install-transaction.json', 'unjournaled')
        folder = renewal_pending(fixture, monkeypatch, destination)
        journal = folder / 'install-transaction.json'
        pending = folder / ('.' + destination + '.pending')
        if attack == 'bytes':
            pending.write_bytes(b'substituted')
        elif attack == 'mode':
            pending.chmod(0o644)
        elif attack in ('owner', 'group'):
            target = pending.stat()
            fstat, named_stat = os.fstat, os.stat
            def foreign(info: os.stat_result) -> object:
                if (info.st_dev, info.st_ino) != (target.st_dev, target.st_ino):
                    return info
                values = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
                field = 'st_uid' if attack == 'owner' else 'st_gid'
                values[field] += 1
                return SimpleNamespace(**values)
            monkeypatch.setattr(os, 'fstat', lambda fd: foreign(fstat(fd)))
            monkeypatch.setattr(os, 'stat', lambda *args, **kwargs: foreign(named_stat(*args, **kwargs)))
        elif attack in ('hardlink', 'symlink'):
            foreign_path = folder / 'foreign'
            if attack == 'hardlink':
                os.link(pending, foreign_path)
            else:
                pending.rename(foreign_path)
                pending.symlink_to('foreign')
        elif attack == 'foreign':
            # Retain the valid own pending too: unknown evidence must refuse
            # before even that otherwise recoverable inode is discarded.
            (folder / '.foreign.pending').write_bytes(pending.read_bytes())
        elif journal.exists():
            journal.unlink()
        before = staged_inventory(fixture)
        with pytest.raises(module.InstallerError):
            fixture.installer.renew_policy()
        assert staged_inventory(fixture) == before
        assert fixture.manager.calls == []
        return
    folder, transaction = renewal_intent(fixture, monkeypatch)
    journal = folder / 'install-transaction.json'
    if case.endswith('_drift'):
        (folder / ('policy.json' if case == 'policy_drift' else 'provision.json')).write_bytes(b'{}')
    elif case in ('malformed', 'oversize', 'other_transaction'):
        journal.write_bytes({'malformed': b'{', 'oversize': b'x' * 65537,
                             'other_transaction': b'{"version":1}'}[case])
    elif case in ('extra', 'digest'):
        record = json.loads(transaction)
        if case == 'extra':
            record['selector'] = '/untrusted'
        else:
            record['files']['policy.json']['new_sha256'] = '0' * 64
        journal.write_bytes(json.dumps(record, sort_keys=True).encode())
    elif case == 'symlink':
        journal.rename(folder / 'foreign')
        journal.symlink_to('foreign')
    elif case == 'hardlink':
        os.link(journal, folder / 'foreign')
    elif case == 'mode':
        journal.chmod(0o644)
    else:
        (folder / '.policy.json.pending').write_bytes(b'unknown')
    before = fixture.inventory()
    with pytest.raises(module.InstallerError):
        fixture.installer.renew_policy()
    assert fixture.inventory() == before
    assert journal.exists()
    assert fixture.manager.calls == []


@contextmanager
def renewal_lock_holder(directory: Path) -> Iterator[None]:
    """A separate process holds the actual directory inode, with bounded teardown."""
    script = '''
import fcntl, os, sys
descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
info = os.fstat(descriptor)
print(info.st_dev, info.st_ino, flush=True)
sys.stdin.readline()
os.close(descriptor)
'''
    child = subprocess.Popen([sys.executable, '-I', '-c', script, str(directory)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([child.stdout], [], [], 5)[0], 'lock holder did not acknowledge acquisition'
        info = directory.stat()
        assert child.stdout.readline(128).decode().strip() == f'{info.st_dev} {info.st_ino}'
        yield
        child.communicate(b'release\n', timeout=5)
        assert child.returncode == 0, 'lock holder failed to release'
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


@pytest.mark.parametrize('case', ['provision_drift', 'artifact_mismatch', 'unsafe_source', 'oversize_intent', 'locked'])
def test_renewal_initial_refusals_have_no_effect(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    folder, _ = renewal_setup(fixture, monkeypatch)
    if case == 'provision_drift':
        (folder / 'policy.json').write_bytes(b'{}')
    elif case == 'oversize_intent':
        policy = json.loads((folder / 'policy.json').read_bytes())
        policy['padding'] = 'x' * 31000
        (folder / 'policy.json').write_bytes(json.dumps(policy, sort_keys=True).encode())
        provision = json.loads((folder / 'provision.json').read_bytes())
        provision['digests']['policy.json'] = hashlib.sha256((folder / 'policy.json').read_bytes()).hexdigest()
        (folder / 'provision.json').write_bytes(json.dumps(provision, sort_keys=True).encode())
        assert (folder / 'policy.json').stat().st_size < 65536
        write = fixture.installer._write_provision_file
        def oversized(name: str, data: bytes, *args: object, **kwargs: object) -> None:
            assert name == 'install-transaction.json' and len(data) > 65536
            write(name, data, *args, **kwargs)
        monkeypatch.setattr(fixture.installer, '_write_provision_file', oversized)
    elif case in ('artifact_mismatch', 'unsafe_source'):
        source = fixture.inputs / 'route_policy.json'
        source.write_bytes(b'{}')
        if case == 'unsafe_source':
            source.chmod(0o666)
        monkeypatch.setattr(module, '__file__', str(fixture.inputs / 'installer.py'))
    if case == 'locked':
        metadata = {name: (folder / name).stat() for name in ('policy.json', 'provision.json')}
        before = fixture.inventory()
        with renewal_lock_holder(fixture.root / 'var/lib/happyranch-remote-access'):
            with pytest.raises(module.InstallerError):
                fixture.installer.renew_policy()
            assert fixture.inventory() == before
            assert fixture.manager.calls == []
        # An acknowledged release, rather than a sleep, permits the SAME valid
        # operation. Observe real policy/provenance publication and preserved custody.
        fixture.installer.renew_policy()
        policy = (folder / 'policy.json').read_bytes()
        assert json.loads(policy)['revision'] == 2
        assert json.loads((folder / 'provision.json').read_bytes())['digests']['policy.json'] == hashlib.sha256(policy).hexdigest()
        for name, previous in metadata.items():
            current = (folder / name).stat()
            assert (current.st_uid, current.st_gid, current.st_mode) == (previous.st_uid, previous.st_gid, previous.st_mode)
        after = fixture.inventory()
        for name, value in before.items():
            if name not in ('etc/happyranch/policy.json', 'etc/happyranch/provision.json'):
                assert after[name] == value
        assert not (folder / 'install-transaction.json').exists()
        assert fixture.manager.calls == []
    else:
        before = fixture.inventory()
        with pytest.raises(module.InstallerError):
            fixture.installer.renew_policy()
        assert fixture.inventory() == before
        assert fixture.manager.calls == []


_RENEWAL_READERS = ['inventory', 'replacement_inventory', 'prepared_inventory', 'home_admission', 'home_inert_inventory', 'ordinary_record', 'replacement_record', 'provision', 'install', 'uninstall', 'check_drift', 'stop_and_confirm', '_refuse_install_transaction', '_replacement_release', '_replacement_pending', '_staging_inventory', '_replacement_custody']

@pytest.mark.parametrize('reader', _RENEWAL_READERS)
def test_every_other_transaction_reader_refuses_renewal(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, reader: str) -> None:
    from runtime.remote_access.home_admission import HomeProfileAdmission
    folder, transaction = renewal_intent(fixture, monkeypatch)
    if reader == 'provision':
        for child in folder.iterdir():
            if child.name != 'install-transaction.json':
                child.unlink()
    before = fixture.inventory()
    io = fixture.installer
    with io._publication_parent() as (parent, check):
        calls = dict(
            inventory=io.inventory, replacement_inventory=io.replacement_inventory,
            prepared_inventory=lambda: io._inventory(prepared_transaction=transaction),
            home_admission=lambda: HomeProfileAdmission(io).require(),
            home_inert_inventory=lambda: HomeProfileAdmission(io).require(ordinary=False),
            ordinary_record=lambda: io._validate_install_record('install-transaction.json', transaction),
            replacement_record=lambda: io._validate_install_record('install-transaction.json', transaction, replacement=True),
            provision=fixture.provision, install=lambda: io.install(enrollment_key_file=fixture.key),
            uninstall=io.uninstall, check_drift=io.check_drift,
            stop_and_confirm=lambda: io.stop_and_confirm(_replacement_transaction=transaction),
            _replacement_pending=lambda: io._replacement_pending(parent, transaction),
        )
        if reader in ('inventory', 'prepared_inventory'):
            assert calls[reader]().state == 'manual-install-conflict'
        elif reader == 'replacement_inventory':
            result = calls[reader]()
            assert result.installation.state == 'manual-install-conflict'
            assert result.journal_generation is None
        else:
            call = calls.get(reader)
            if call is None:
                call = (getattr(io, reader) if reader == '_refuse_install_transaction' else
                        lambda: getattr(io, reader)(transaction))
            with pytest.raises(module.InstallerError):
                call()
        check()
    assert fixture.inventory() == before
    assert (folder / 'install-transaction.json').read_bytes() == transaction
    assert fixture.manager.calls == []




# Retained Disable/inventory uses historical inputs, never initial home publication.
@pytest.mark.parametrize("old_generation", [0, 7])
@pytest.mark.parametrize("case", ["classification", "double", "tail", "ids", "connector", "state",
    "pending", "txn", "minter", "unknown", "audit", "generation", "tls"])
def test_home_disable_preserves_post_disable_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old_generation: int, case: str,
) -> None:
    from runtime.remote_access.home_activation_backend import HelperLifecycleBackend
    from scripts.home_legacy_fixture import legacy_admission_fixture

    history = legacy_admission_fixture(tmp_path, monkeypatch, ready=True,
                                       layout="deb", retain_headscale=True)
    io = history.receipts._io
    io.manager = LifecycleManager(history.root)
    show = io.manager.show_properties
    io.manager.show_properties = lambda name, properties: {key: value for key, value in {
        **show(name, tuple(key for key in properties if key != "Names")), "Names": name,
    }.items() if key in properties}
    state = history.root / "var/lib/happyranch-remote-access"
    (state / "home-activation-txn.json").unlink()
    (history.root / "etc/systemd/system/happyranch-managed.target.wants" / module.headscale.UNIT).unlink()
    activation = state / "home-activation.json"
    record = json.loads(activation.read_bytes())
    record["activation_generation"] = old_generation
    activation.write_bytes(json.dumps(record).encode())
    sidecar = history.root / "etc/happyranch/sidecar.json"
    sidecar.write_bytes(sidecar.read_bytes().replace(b'"activation_generation":7',
        b'"activation_generation":' + str(old_generation).encode()))
    connector = json.loads((history.root / "etc/happyranch/connector.json").read_bytes())
    unit = history.root / "etc/systemd/system" / module.headscale.UNIT
    before = {name: ((history.root / "etc/systemd/system" / name).stat().st_ino,
                    (history.root / "etc/systemd/system" / name).read_bytes()) for name in module.UNITS}
    retained = sidecar.read_bytes()
    backend = HelperLifecycleBackend(io=io)
    snapshot = backend.capture("Disable", os.getuid())
    # Native policy units are already quiescent in this retained B6 venue.
    monkeypatch.setattr(backend, "_policy_quiescence", lambda: [])
    assert backend.dispatch("Disable", os.getuid(), "cleanup", snapshot,
                            revoke_succeeded=True) == {"outcome": "off", "residue": []}
    assert not activation.exists() and not unit.exists()
    assert {name: ((history.root / "etc/systemd/system" / name).stat().st_ino,
                   (history.root / "etc/systemd/system" / name).read_bytes()) for name in module.UNITS} == before
    assert sidecar.read_bytes() == retained
    if case == "classification":
        untouched = staged_inventory(history)
        inventory = io.inventory()
        assert (inventory.state, inventory.detail) == ("modern", "post-disable")
        assert staged_inventory(history) == untouched
        return
    if case == "double":
        tail = retained[retained.index(b',"ControlURL"'):retained.rindex(b'}')]
        sidecar.write_bytes(retained[:retained.rindex(b'}')] + tail + retained[retained.rindex(b'}'):])
    if case == "tail": sidecar.write_bytes(retained[:retained.rindex(b'}')] + b',"foreign":true}')
    if case == "ids":
        sidecar.write_bytes(retained.replace(b'"home_id":"' + connector["home_id"].encode() + b'"',
                                            b'"home_id":"foreign"', 1))
    if case == "connector":
        path = history.root / "etc/happyranch/connector.json"
        path.write_bytes(path.read_bytes() + b" ")
    if case == "state":
        path = history.root / "var/lib/happyranch-connector"
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        (path / "trust-state.json").write_bytes(b"synthetic retained trust")
    if case == "pending": (sidecar.parent / ".sidecar.json.pending").touch(mode=0o600)
    if case == "txn": (state / "home-activation-txn.json").write_bytes(b"{}")
    if case == "minter": (state / "home-minter").mkdir(mode=0o700)
    if case == "unknown": (sidecar.parent / "unknown").touch(mode=0o600)
    if case == "audit": (state / "home-audit.jsonl").chmod(0o644)
    if case == "generation":
        sidecar.write_bytes(retained.replace(b'"activation_generation":' + str(old_generation).encode(),
                                            b'"activation_generation":true'))
    if case == "tls":
        sidecar.write_bytes(retained.replace(b'},"home_binding":', b',"foreign":true},"home_binding":'))
    untouched = staged_inventory(history)
    io.manager.calls.clear()
    assert io.inventory().state == "manual-install-conflict"
    assert staged_inventory(history) == untouched
    assert all(call[0] == "show" for call in io.manager.calls)
