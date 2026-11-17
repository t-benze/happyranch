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
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

import pytest

from runtime.remote_access import installer as module
from runtime.remote_access import release_verifier
from runtime.remote_access.managed_provider import ManagedProviderConfig
from runtime.remote_access.supervisor import ConnectorConfig


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


@pytest.mark.parametrize("name,mode", [
    ("connector.json", 0o640), ("sidecar.json", 0o640), ("policy.json", 0o640),
    ("release-daemon.json", 0o644), ("daemon.token", 0o600), ("provision.json", 0o600),
])
def test_provision_publication_is_complete_and_durable(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, name: str, mode: int,
) -> None:
    parent = publication_parent(fixture)
    target = parent / name
    data = b"synthetic-bytes!" * 4096  # exactly 64 KiB
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
def test_failed_provision_write_never_exposes_partial_final_file(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, fault: str,
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
        fixture.installer._write_provision_file("connector.json", b"synthetic-complete", fixture.installer.root_owner, 0o640)
    assert reached == [fault]
    assert list(parent.iterdir()) == []
    assert fixture.manager.calls == []


@pytest.mark.parametrize("level", ["etc", "happyranch"])
def test_swapped_publication_parent_refuses_before_rename(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, level: str,
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
        fixture.installer._write_provision_file("connector.json", b"synthetic-complete", fixture.installer.root_owner, 0o640)
    assert swapped
    assert list(parent.iterdir()) == []
    old_parent = moved if level == "happyranch" else moved / "happyranch"
    assert list(old_parent.iterdir()) == []
    assert fixture.manager.calls == []


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "mode", "oversize", "foreign", "fifo", "safe"])
@pytest.mark.parametrize("entry", ["target", "pending"])
def test_existing_provision_entry_refuses_without_any_publication(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, attack: str, entry: str,
) -> None:
    parent = publication_parent(fixture)
    target = parent / ("policy.json" if entry == "target" else ".policy.json.pending")
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
def test_changed_pending_file_refuses_without_overwriting_or_partial_publication(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, attack: str,
) -> None:
    parent = publication_parent(fixture)
    pending = parent / ".connector.json.pending"
    target = parent / "connector.json"
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
        fixture.installer._write_provision_file("connector.json", b"synthetic-complete", fixture.installer.root_owner, 0o640)
    assert reached
    assert not target.exists()
    if attack == "pending-swap":
        assert pending.read_bytes() == b"synthetic-substitution"  # Never delete the substituted entry.
    assert fixture.manager.calls == []


@pytest.mark.parametrize("name,data", [
    ("connector.json", b"s" * 65537), ("../escaped.json", b"{}"),
    ("install-transaction.json", b"{}"), ("connector.json", b""),
], ids=["oversize", "path-escape", "future-transaction", "empty"])
def test_unbounded_or_unselectable_publication_refuses(fixture: Fixture, name: str, data: bytes) -> None:
    parent = publication_parent(fixture)
    with pytest.raises(module.InstallerError):
        fixture.installer._write_provision_file(name, data, fixture.installer.root_owner, 0o640)
    assert list(parent.iterdir()) == []
    assert not (parent.parent / "escaped.json").exists()
    assert fixture.manager.calls == []


def test_atomic_publication_never_replaces_racing_target(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = publication_parent(fixture)
    target = parent / "connector.json"
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
        fixture.installer._write_provision_file("connector.json", b"synthetic-new", fixture.installer.root_owner, 0o640)
    assert reached
    assert target.read_bytes() == b"synthetic-racing-target"
    assert sorted(p.name for p in parent.iterdir()) == [target.name]
    assert fixture.manager.calls == []


@pytest.mark.parametrize("unavailable", ["symbol", "kernel"])
def test_unavailable_atomic_rename_refuses_without_fallback(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, unavailable: str,
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
        fixture.installer._write_provision_file("connector.json", b"synthetic-new", fixture.installer.root_owner, 0o640)
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
