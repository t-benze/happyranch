"""Temp-root installer token custody; no host commands or installed services.

Run: uv run --frozen pytest scripts/test_installer_token_custody.py -v --tb=short
"""
from __future__ import annotations

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
