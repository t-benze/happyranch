"""Standalone inert preparation/cleanup; temp root and fake commands, never host effects.

Run: uv run --frozen --python /usr/bin/python3 pytest scripts/test_headscale_custody.py -v --tb=short
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import json
import os
import socket
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import headscale_service as home
from runtime.remote_access import installer as module
from runtime.remote_access import systemd_unit
from scripts.home_legacy_fixture import KernelDump, LegacyNativeFixture
from scripts.test_home_activation_backend import composition

PUBLICATION = "headscale-publication.json"
CONFIG_FILES = ("config.yaml", "derp.yaml")
LEAF_FILES = ("server.crt", "server.key")
MATERIALIZER = "/opt/happyranch-desktop/libexec/remote-access/happyranch-home-ca"
EMPTY_CA = "etc/happyranch/empty-ca-directory"
CONTROL_CA = "etc/happyranch/control-ca.pem"
PUBLISHED = (*CONFIG_FILES, *LEAF_FILES, "control-ca.pem", PUBLICATION)
ISSUER = "var/lib/happyranch-remote-access/home-issuer"
ISSUER_RECEIPT = "home-issuer-publication.json"

EXPECTED_POLICY = b'''{
  "acls": [
    {
      "action": "accept",
      "dst": [
        "happyranch-node:443"
      ],
      "proto": "tcp",
      "src": [
        "happyranch-invite"
      ]
    }
  ]
}
'''
EXPECTED_UNIT = '''[Unit]
Description=HappyRanch home Headscale
After=network.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Service]
User=happyranch-headscale
Group=happyranch-headscale
ExecStart=/opt/happyranch-desktop/libexec/headscale/headscale --config /etc/happyranch/headscale/config.yaml serve
ExecStartPost=/opt/happyranch-desktop/python/bin/python3 -I -m runtime.remote_access.headscale_service wait-ready
Type=exec
Restart=on-failure
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
SystemCallArchitectures=native
UMask=0077
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
PrivateDevices=yes
ProtectKernelModules=yes
CapabilityBoundingSet=
AmbientCapabilities=
RestartSec=1
TimeoutStartSec=40
TimeoutStopSec=30
KillMode=control-group
RuntimeDirectory=happyranch-headscale
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=yes
StateDirectory=happyranch-headscale
StateDirectoryMode=0700
ReadWritePaths=/var/lib/happyranch-headscale /run/happyranch-headscale
StandardOutput=null
StandardError=null
'''


class Fixture(LegacyNativeFixture):
    def prepare(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> tuple[int, str]:
        with monkeypatch.context() as patch:
            patch.setattr(module, "ManagedInstaller", lambda **kw: self.installer)
            try:
                code = module.main(["prepare-headscale", "--layout", "deb"])
            except SystemExit as error:
                code = error.code
        output = capsys.readouterr()
        return code, output.out + output.err

    def cleanup(self, monkeypatch, capsys, command="disable-headscale"):
        with monkeypatch.context() as patch:
            patch.setattr(module, "ManagedInstaller", lambda **kw: self.installer)
            try:
                code = module.main([command, "--layout", "deb"])
            except SystemExit as error:
                code = error.code
        output = capsys.readouterr()
        return code, output.out + output.err


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch) -> Fixture:
    value = Fixture(tmp_path)
    monkeypatch.setattr(home.socket, "socket", lambda *args: KernelDump(value, args))
    monkeypatch.setattr(home, "preflight_ports", value.preflights.append)
    return value


def foreign_metadata(monkeypatch, path: Path) -> None:
    original = os.fstat
    identity = path.stat().st_dev, path.stat().st_ino
    def observe(fd):
        info = original(fd)
        if (info.st_dev, info.st_ino) != identity: return info
        value = SimpleNamespace(**{k: getattr(info, k) for k in dir(info) if k.startswith("st_")})
        value.st_uid += 1
        return value
    monkeypatch.setattr(module.os, "fstat", observe)


def issuer_journal(path: Path, outcome: str = "issued") -> None:
    (path / "invite-issuer.lock").touch(mode=0o600)
    (path / "invite-issuer.json").write_text(json.dumps(dict(
        operation_id="a" * 32, binding_digest="b" * 64, not_before=123,
        expires_at=456, outcome=outcome), sort_keys=True, separators=(",", ":")))
    (path / "invite-issuer.json").chmod(0o600)


@pytest.mark.parametrize("existing", [False, True])
def test_issuer_prepare_receipts_exact_directory(fixture, monkeypatch, capsys, existing) -> None:
    path = fixture.root / ISSUER
    if existing:
        path.parent.mkdir(parents=True, mode=0o700)
        path.mkdir(mode=0o700)
        (fixture.root / "var").chmod(0o755)
        (fixture.root / "var/lib").chmod(0o755)
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 0, output
    assert path.is_dir(), "prepare must publish the protected issuer directory"
    info = path.stat()
    assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (fixture.uid, fixture.gid, 0o700)
    receipt = path.parent / ISSUER_RECEIPT
    assert json.loads(receipt.read_bytes()) == dict(version=1, directory="/" + ISSUER,
        uid=fixture.uid, gid=fixture.gid, device=info.st_dev, inode=info.st_ino)
    assert receipt.stat().st_mode & 0o777 == 0o600
    before = receipt.read_bytes()
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    assert receipt.read_bytes() == before
    assert not any(call[:2] in (["systemctl", "start"], ["systemctl", "enable"],
                                ["systemctl", "daemon-reload"]) for call in fixture.calls)


@pytest.mark.parametrize("entry", ["symlink", "fifo", "file", "mode", "owner", "parent-link", "pending", "foreign"])
def test_issuer_prepare_refuses_unsafe_without_repair(fixture, monkeypatch, capsys, entry) -> None:
    path = fixture.root / ISSUER
    path.parent.mkdir(parents=True, mode=0o700)
    (fixture.root / "var").chmod(0o755)
    (fixture.root / "var/lib").chmod(0o755)
    if entry == "symlink":
        target = fixture.root / "foreign-issuer"
        target.mkdir(mode=0o700)
        path.symlink_to(target)
    elif entry == "fifo": os.mkfifo(path, 0o600)
    elif entry == "file": path.write_bytes(b"foreign")
    else:
        path.mkdir(mode=0o755 if entry == "mode" else 0o700)
        if entry == "owner":
            foreign_metadata(monkeypatch, path)
        if entry == "parent-link":
            target = path.parent.with_name("redirected-state")
            path.parent.rename(target)
            path.parent.symlink_to(target)
        if entry == "pending": (path.parent / ".home-issuer.pending").mkdir(mode=0o700)
        if entry == "foreign": (path / "foreign").write_bytes(b"keep")
    mode = path.lstat().st_mode
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and "issuer-state-uncertain" in output
    assert path.lstat().st_mode == mode
    assert not (path.parent / ISSUER_RECEIPT).exists()
    assert fixture.calls == []


@pytest.mark.parametrize("name", [".home-issuer.pending", ".home-issuer-publication.json.pending",
    "home-issuer/invite-issuer.pending", "home-issuer/invite-issuer.cleanup.pending"])
@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "hardlink", "fifo"])
def test_issuer_pending_residue_is_never_repaired(fixture, monkeypatch, capsys, name, kind) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    path = fixture.root / home.INSTALLER_STATE / name
    if kind == "directory": path.mkdir(mode=0o700)
    elif kind == "symlink": path.symlink_to(path.with_name("absent-target"))
    elif kind == "fifo": os.mkfifo(path, 0o600)
    elif kind == "hardlink": path.hardlink_to(fixture.receipt())
    else: path.write_bytes(b"synthetic pending")
    before = path.lstat()
    fixture.calls.clear()
    assert fixture.prepare(monkeypatch, capsys)[0] == 1
    assert fixture.cleanup(monkeypatch, capsys)[0] == 1
    assert path.lstat() == before and fixture.calls == []


@pytest.mark.parametrize("outcome", [None, "issued", "refused", "expired", "encoding", "pairing", "recovered"])
def test_issuer_owned_cleanup_preserves_siblings_and_receipts(fixture, monkeypatch, capsys, outcome) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    path = fixture.root / ISSUER
    if outcome: issuer_journal(path, outcome)
    minter = path.parent / "home-minter"
    minter.mkdir(mode=0o700)
    (minter / "keep").write_bytes(b"operation fence")
    receipt = path.parent / ISSUER_RECEIPT
    before = receipt.read_bytes()
    code, output = fixture.cleanup(monkeypatch, capsys)
    assert code == 0, output
    assert not path.exists()
    assert receipt.read_bytes() == before
    assert (minter / "keep").read_bytes() == b"operation fence"
    assert (path.parent / "home-ca/ca.key").read_bytes() == b"synthetic CA private key"
    fixture.calls.clear()
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and "issuer-state-uncertain" in output
    assert not path.exists() and fixture.calls == []


@pytest.mark.parametrize("tamper", ["minting", "uncertain", "pending", "foreign", "edited", "mode", "owner",
    "hardlink", "symlink", "fifo", "lock-content", "lock-missing", "journal-missing", "busy", "receipt", "receipt-pending",
    "receipt-hardlink", "receipt-symlink", "receipt-mode", "receipt-fifo", "receipt-owner", "receipt-missing",
    "lock-symlink", "lock-hardlink", "lock-mode", "lock-fifo", "lock-owner", "directory-swap",
    "oversized-not-before", "oversized-expires-at", "negative-oversized-not-before", "negative-oversized-expires-at"])
@pytest.mark.parametrize("command", ["prepare-headscale", "disable-headscale"])
def test_issuer_cleanup_preserves_whole_uncertain_directory(fixture, monkeypatch, capsys, tamper, command) -> None:
    import fcntl
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    path = fixture.root / ISSUER
    issuer_journal(path, tamper if tamper in {"minting", "uncertain"} else "issued")
    journal = path / "invite-issuer.json"
    receipt = path.parent / ISSUER_RECEIPT
    if tamper in {"pending", "foreign"}: (path / ("invite-issuer.pending" if tamper == "pending" else "foreign")).write_bytes(b"keep")
    if "oversized" in tamper:
        record = json.loads(journal.read_bytes())
        field = "not_before" if tamper.endswith("not-before") else "expires_at"
        record[field] = -(10 ** 400) if tamper.startswith("negative-") else 10 ** 400
        journal.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")))
    if tamper == "edited": journal.write_bytes(b'{"outcome":"issued"}')
    if tamper == "mode": journal.chmod(0o644)
    if tamper in {"owner", "receipt-owner", "lock-owner"}:
        foreign_metadata(monkeypatch, journal if tamper == "owner" else receipt if tamper == "receipt-owner"
                         else path / "invite-issuer.lock")
    if tamper == "hardlink": (fixture.root / "journal-alias").hardlink_to(journal)
    if tamper in {"symlink", "fifo"}:
        journal.unlink()
        if tamper == "symlink": journal.symlink_to(receipt)
        else: os.mkfifo(journal, 0o600)
    if tamper == "lock-content": (path / "invite-issuer.lock").write_bytes(b"foreign")
    if tamper == "lock-missing": (path / "invite-issuer.lock").unlink()
    if tamper == "journal-missing": journal.unlink()
    lock_path = path / "invite-issuer.lock"
    if tamper == "lock-hardlink": (fixture.root / "lock-alias").hardlink_to(lock_path)
    if tamper == "lock-mode": lock_path.chmod(0o644)
    if tamper in {"lock-symlink", "lock-fifo"}:
        lock_path.unlink()
        if tamper == "lock-symlink": lock_path.symlink_to(receipt)
        else: os.mkfifo(lock_path, 0o600)
    if tamper == "receipt-missing": receipt.unlink()
    if tamper == "receipt": receipt.write_bytes(b'{}')
    if tamper == "receipt-pending": (receipt.parent / ("." + receipt.name + ".pending")).write_bytes(b"keep")
    if tamper == "receipt-hardlink": (fixture.root / "receipt-alias").hardlink_to(receipt)
    if tamper == "receipt-mode": receipt.chmod(0o644)
    if tamper in {"receipt-symlink", "receipt-fifo"}:
        receipt.unlink()
        if tamper == "receipt-symlink": receipt.symlink_to(journal)
        else: os.mkfifo(receipt, 0o600)
    if tamper == "directory-swap":
        path.rename(path.with_name("kept-issuer"))
        path.mkdir(mode=0o700)
        issuer_journal(path)
    lock = os.open(path / "invite-issuer.lock", os.O_RDONLY) if tamper == "busy" else None
    if lock is not None: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    def snapshot():
        return {p.name: (p.lstat().st_ino, p.lstat().st_mode, p.lstat().st_size,
                         p.lstat().st_mtime_ns, p.lstat().st_ctime_ns) for p in path.iterdir()}
    before = snapshot()
    contents = {p.name: p.read_bytes() for p in path.iterdir()} if "oversized" in tamper else None
    fixture.calls.clear()
    try:
        code, output = fixture.cleanup(monkeypatch, capsys, command)
        category = "issuer-state-uncertain" if command == "prepare-headscale" else "issuer-cleanup-uncertain"
        assert code == 1 and category in output
        assert "Traceback" not in output and "OverflowError" not in output
        assert str(10 ** 400) not in output
        with pytest.raises(home.HeadscaleServiceError) as caught:
            if command == "prepare-headscale": fixture.installer.prepare_headscale()
            else: fixture.installer.cleanup_headscale()
        assert str(caught.value) == category
        assert caught.value.__cause__ is None and caught.value.__context__ is None
    finally:
        if lock is not None: os.close(lock)
    assert snapshot() == before
    if contents is not None: assert {p.name: p.read_bytes() for p in path.iterdir()} == contents
    assert fixture.calls == []


@pytest.mark.parametrize("boundary", ["directory-pending", "directory-published", "receipt-pending", "receipt-published"])
def test_issuer_publication_crash_and_retry_preserves_residue(fixture, monkeypatch, capsys, boundary) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    path = fixture.root / ISSUER
    path.rmdir()
    (path.parent / ISSUER_RECEIPT).unlink()
    original = fixture.installer._rename_provision_entry
    pid = os.fork()
    if pid == 0:
        def crash(parent, source, target, **kwargs):
            selected = target == ("home-issuer" if boundary.startswith("directory") else ISSUER_RECEIPT)
            if selected and boundary.endswith("pending"): os._exit(73)
            original(parent, source, target, **kwargs)
            if selected: os._exit(73)
        fixture.installer._rename_provision_entry = crash
        fixture.installer.prepare_headscale()
        os._exit(74)
    assert os.waitpid(pid, 0)[1] == 73 << 8
    if boundary.endswith("pending"):
        pending = ".home-issuer.pending" if boundary.startswith("directory") else "." + ISSUER_RECEIPT + ".pending"
        before = (path.parent / pending).lstat()
        assert fixture.prepare(monkeypatch, capsys)[0] == 1
        assert (path.parent / pending).lstat().st_ino == before.st_ino
    else:
        assert path.is_dir()
        assert fixture.prepare(monkeypatch, capsys)[0] == 0


def test_issuer_partial_cleanup_preserves_pending_on_retry(fixture, monkeypatch, capsys) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    path = fixture.root / ISSUER
    issuer_journal(path)
    original = os.unlink
    def fail(name, **kwargs):
        if name == "invite-issuer.lock": raise OSError("synthetic removal interruption")
        return original(name, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "unlink", fail)
        code, output = fixture.cleanup(monkeypatch, capsys)
    assert code == 1 and "issuer-partial-cleanup-uncertain" in output
    assert (path / "invite-issuer.cleanup.pending").exists()
    assert fixture.cleanup(monkeypatch, capsys)[0] == 1


def test_created_receipt_custody_and_inert_unit(fixture, monkeypatch, capsys) -> None:
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 0, output
    assert json.loads(output) == {"state": "inert", "account": "created", "preservation": "receipt-recorded"}
    assert json.loads(fixture.receipt().read_bytes()) == {
        "version": 1, "account": "happyranch-headscale", "uid": fixture.uid, "gid": fixture.gid,
        "group_created_with_user": True}
    assert fixture.receipt().stat().st_mode & 0o777 == 0o600
    assert fixture.receipt().parent.stat().st_mode & 0o777 == 0o700
    info = (fixture.root / "etc/happyranch/headscale").stat()
    assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (fixture.uid, fixture.gid, 0o700)
    assert not (fixture.root / "var/lib/happyranch-headscale").exists()
    assert not (fixture.root / "run/happyranch-headscale").exists()
    policy = fixture.root / "etc/happyranch/headscale/policy.hujson"
    assert policy.read_bytes() == EXPECTED_POLICY
    assert policy.stat().st_mode & 0o777 == 0o600
    unit = fixture.root / "etc/systemd/system/happyranch-headscale.service"
    assert unit.read_bytes() == EXPECTED_UNIT.encode()
    assert unit.stat().st_mode & 0o777 == 0o644
    assert [args[0] for args in fixture.calls].count("useradd") == 1
    assert set(args[0] for args in fixture.calls) == {"useradd", "getent", "passwd", "systemctl", "ps", MATERIALIZER}
    assert all(args[0] != "systemctl" or args[1] == "show" for args in fixture.calls)
    assert sorted(p.name for p in policy.parent.iterdir()) == ["config.yaml", "derp.yaml", "policy.hujson", "server.crt", "server.key"]
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    assert [args[0] for args in fixture.calls].count("useradd") == 1


def test_expected_account_without_receipt_is_unowned(fixture, monkeypatch, capsys) -> None:
    fixture.present = fixture.group_present = True
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 0, output
    assert json.loads(output)["account"] == "unowned"
    assert json.loads(output)["preservation"] == "preserved: not created by HappyRanch"
    assert not fixture.receipt().exists()
    assert all(args[0] in {"getent", "passwd", "systemctl", "ps", MATERIALIZER} for args in fixture.calls)
    assert all(args[0] != "systemctl" or args[1] == "show" for args in fixture.calls)
    code, output = fixture.cleanup(monkeypatch, capsys)
    assert code == 0 and json.loads(output) == {
        "state": "preserved", "account": "preserved: not created by HappyRanch", "home_ca": "preserved"}
    assert fixture.present and fixture.group_present and not fixture.receipt().exists()
    assert not any(args[0] in {"userdel", "groupdel", "useradd"} for args in fixture.calls)


@pytest.mark.parametrize("shape", ["uid", "gid", "shell", "home", "members", "primary", "password", "group-only"])
def test_account_shape_refuses_before_effects(fixture, monkeypatch, capsys, shape) -> None:
    fixture.present = fixture.group_present = True
    if shape == "uid": fixture.user.pw_uid = fixture.uid + 10
    if shape == "gid": fixture.group.gr_gid = fixture.gid + 10
    if shape == "shell": fixture.user.pw_shell = "/bin/bash"
    if shape == "home": fixture.user.pw_dir = "/missing-home"
    if shape == "members": fixture.group.gr_mem = ["other"]
    if shape == "primary": fixture.shared = True
    if shape == "password": fixture.locked = False
    if shape == "group-only": fixture.present = False
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and "account-shape" in output
    assert not fixture.receipt().exists()
    assert not (fixture.root / "etc/happyranch").exists()
    assert all(args[0] in {"getent", "passwd"} for args in fixture.calls)


@pytest.mark.parametrize("tamper", ["uid", "gid", "account", "bool-id", "extra", "malformed", "mode", "symlink", "pending"])
def test_receipt_tamper_preserves_account_and_receipt(fixture, monkeypatch, capsys, tamper) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    receipt = fixture.receipt()
    value = json.loads(receipt.read_bytes())
    if tamper in {"uid", "gid"}: value[tamper] += 1
    if tamper == "account": value["account"] = "foreign"
    if tamper == "bool-id": value["uid"] = True
    if tamper == "extra": value["extra"] = "synthetic private diagnostic"
    receipt.write_text("{" if tamper == "malformed" else json.dumps(value))
    if tamper == "mode": receipt.chmod(0o644)
    if tamper == "symlink":
        receipt.rename(receipt.with_suffix(".foreign"))
        receipt.symlink_to(receipt.with_suffix(".foreign").name)
    if tamper == "pending": receipt.with_name(".headscale-account.json.pending").write_text("residue")
    before = receipt.read_bytes()
    fixture.calls.clear()
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and ("account-receipt-uncertain" in output or "custody-uncertain" in output)
    assert "synthetic private diagnostic" not in output
    assert fixture.present and fixture.group_present
    assert receipt.read_bytes() == before
    assert all(args[0] in {"getent", "passwd"} for args in fixture.calls)
    code, output = fixture.cleanup(monkeypatch, capsys)
    assert code == 1 and "cleanup-uncertain" in output
    assert "synthetic private diagnostic" not in output
    assert fixture.present and fixture.group_present and receipt.read_bytes() == before
    assert not any(args[0] in {"userdel", "groupdel", "useradd"} for args in fixture.calls)


def test_crash_after_useradd_becomes_unowned(fixture, monkeypatch, capsys) -> None:
    with monkeypatch.context() as patch:
        def crash(*args, **kwargs):
            raise OSError("synthetic private diagnostic")
        patch.setattr(fixture.installer, "_write_provision_file", crash)
        code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and "custody-uncertain" in output
    assert fixture.present and not fixture.receipt().exists()
    fixture.calls.clear()
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 0 and json.loads(output)["account"] == "unowned"
    assert not fixture.receipt().exists()
    assert all(args[0] in {"getent", "passwd", "systemctl", "ps", MATERIALIZER} for args in fixture.calls)
    assert all(args[0] != "systemctl" or args[1] == "show" for args in fixture.calls)
    code, output = fixture.cleanup(monkeypatch, capsys)
    assert code == 0 and json.loads(output)["account"] == "preserved: not created by HappyRanch"
    assert fixture.present and fixture.group_present and not fixture.receipt().exists()
    assert not any(args[0] in {"userdel", "groupdel", "useradd"} for args in fixture.calls)


@pytest.mark.parametrize("entry", ["unknown", "policy", "link", "mode", "state", "unit", "dropin",
    *[f"load:{path}:{kind}:{account}"
      for path in ("etc/systemd/system.control", "run/systemd/system.control",
                   "run/systemd/transient", "run/systemd/generator.early",
                   "etc/systemd/system.attached", "run/systemd/system",
                   "run/systemd/system.attached", "run/systemd/generator",
                   "usr/local/lib/systemd/system", "usr/lib/systemd/system",
                   "run/systemd/generator.late", "lib/systemd/system")
      for kind in ("fragment", "fragment-link", "dropin", "dropin-link")
      for account in ("absent", "unowned", "owned")],
    "prefix-dropin", "type-dropin", "lib-foreign-link",
    *[f"prelink:{path}:{kind}:{account}"
      for path in ("etc/systemd/system.control", "run/systemd/system.control",
                   "run/systemd/transient", "run/systemd/generator.early", "etc/systemd/system",
                   "etc/systemd/system.attached", "run/systemd/system",
                   "run/systemd/system.attached", "run/systemd/generator",
                   "usr/local/lib/systemd/system", "usr/lib/systemd/system",
                   "run/systemd/generator.late", "lib/systemd/system")
      for kind in ("wants", "requires", "wants-foreign", "alias", "alias-dropin", "alias-cross-dropin")
      for account in ("absent", "unowned")
      if kind != "alias-cross-dropin" or path != "etc/systemd/system"],
    *[f"activation:{path}:{kind}:{account}"
      for path in ("etc/systemd/system.control", "run/systemd/system.control",
                   "run/systemd/transient", "run/systemd/generator.early", "etc/systemd/system",
                   "etc/systemd/system.attached", "run/systemd/system",
                   "run/systemd/system.attached", "run/systemd/generator",
                   "usr/local/lib/systemd/system", "usr/lib/systemd/system",
                   "run/systemd/generator.late", "lib/systemd/system")
      for kind in ("target", "dependency-dropin", "timer", "socket", "path", "automount", "mount",
                   "mask", "stem-directory", "stem-fifo", "oversized", "unreadable", "unrelated")
      for account in ("absent", "unowned", "owned")],
    *[f"activation:{path}:{kind}:{account}"
      for path in ("usr/share/dbus-1/system-services", "usr/local/share/dbus-1/system-services",
                   "etc/dbus-1/system-services")
      for kind in ("dbus", "dbus-no-suffix", "oversized", "unreadable", "unrelated")
      for account in ("absent", "unowned", "owned")]])
def test_foreign_custody_preserved(fixture, monkeypatch, capsys, entry) -> None:
    if entry.startswith("activation:"):
        _, path, kind, account = entry.split(":")
        if account == "owned":
            assert fixture.prepare(monkeypatch, capsys)[0] == 0
        elif account == "unowned":
            fixture.present = fixture.group_present = True
        parent = fixture.root / path
        for directory in reversed((parent, *parent.parents)):
            if directory.is_relative_to(fixture.root) and directory != fixture.root:
                directory.mkdir(exist_ok=True, mode=0o755)
        foreign = parent / "operator.target"
        data = b"[Unit]\nWants=happyranch-headscale.service\n"
        if kind == "dependency-dropin":
            foreign = parent / "operator.service.d" / "override.conf"
            foreign.parent.mkdir(mode=0o755)
            data = b"[Unit]\nRequires=happyranch-headscale.service\n"
        if kind in {"timer", "socket", "path", "automount", "mount"}:
            foreign = parent / ("happyranch-headscale." + kind)
            # Same-stem triggers need no explicit service name in their bytes.
            data = {"timer": b"[Timer]\nOnBootSec=1s\n",
                    "socket": b"[Socket]\nListenStream=12345\n",
                    "path": b"[Path]\nPathExists=/synthetic-trigger\n",
                    "automount": b"[Automount]\nWhere=/synthetic-trigger\n",
                    "mount": b"[Mount]\nWhat=tmpfs\nWhere=/synthetic-trigger\nType=tmpfs\n"}[kind]
        if kind in {"dbus", "dbus-no-suffix"}:
            foreign = parent / ("operator.service" if kind == "dbus" else "operator-activation")
            data = b"[D-BUS Service]\nName=org.example.Synthetic\nSystemdService=happyranch-headscale.service\n"
        if kind == "oversized": data = b"#" * 65536 + data
        if kind == "unrelated": data = b"[Unit]\nWants=operator-other.service\n"
        if kind == "mask":
            foreign = parent / "happyranch-headscale.timer"
            foreign.symlink_to("/dev/null")
        elif kind == "stem-directory":
            (parent / "happyranch-headscale.socket.d").mkdir(mode=0o755)
        elif kind == "stem-fifo":
            os.mkfifo(parent / "happyranch-headscale.path", mode=0o600)
        else:
            foreign.write_bytes(data)
            foreign.chmod(0o644)
        if kind == "unreadable":
            # Inject the actual no-follow open failure, independently of euid.
            original_open = module.os.open
            def unreadable(name, flags, *args, **kwargs):
                if name == foreign.name and flags & os.O_NOFOLLOW and not flags & os.O_DIRECTORY:
                    raise PermissionError("synthetic private diagnostic")
                return original_open(name, flags, *args, **kwargs)
            monkeypatch.setattr(module.os, "open", unreadable)
    elif entry.startswith("prelink:"):
        _, path, kind, account = entry.split(":")
        if account == "unowned":
            fixture.present = fixture.group_present = True
        parent = fixture.root / path
        for directory in reversed((parent, *parent.parents)):
            if directory.is_relative_to(fixture.root) and directory != fixture.root:
                directory.mkdir(exist_ok=True, mode=0o755)
        if kind.startswith("alias"):
            alias_parent = fixture.root / "etc/systemd/system" if kind == "alias-cross-dropin" else parent
            for directory in reversed((alias_parent, *alias_parent.parents)):
                if directory.is_relative_to(fixture.root) and directory != fixture.root:
                    directory.mkdir(exist_ok=True, mode=0o755)
            (alias_parent / "operator-alias.service").symlink_to(home.UNIT)
            if kind != "alias":
                dropin = parent / "operator-alias.service.d"
                dropin.mkdir(mode=0o755)
                (dropin / "override.conf").write_bytes(
                    b"[Service]\nExecStart=\nExecStart=/foreign/operator-alias-override\n")
        else:
            dependency = parent / ("multi-user.target.requires" if kind == "requires"
                                   else "multi-user.target.wants")
            dependency.mkdir(mode=0o755)
            (dependency / home.UNIT).symlink_to("../foreign.service" if kind == "wants-foreign"
                                               else "../" + home.UNIT)
    elif entry.startswith("load:"):
        _, path, kind, account = entry.split(":")
        if account == "owned":
            assert fixture.prepare(monkeypatch, capsys)[0] == 0
        elif account == "unowned":
            fixture.present = fixture.group_present = True
        foreign = fixture.root / path / (home.UNIT + (".d" if kind.startswith("dropin") else ""))
        for parent in reversed(foreign.parents):
            if parent.is_relative_to(fixture.root) and parent != fixture.root:
                parent.mkdir(exist_ok=True, mode=0o755)
        if kind.endswith("-link"):
            foreign.symlink_to(fixture.root / "missing-foreign-object")
        elif kind == "dropin":
            foreign.mkdir(mode=0o755)
            (foreign / "override.conf").write_bytes(b"synthetic private diagnostic")
        else:
            # Even identical bytes at an alternate location confer no ownership.
            foreign.write_bytes(EXPECTED_UNIT.encode())
    else:
        assert fixture.prepare(monkeypatch, capsys)[0] == 0
    folder = fixture.root / "etc/happyranch/headscale"
    if entry == "unknown": (folder / "unknown").write_bytes(b"foreign")
    if entry == "policy": (folder / "policy.hujson").write_bytes(b"foreign")
    if entry == "link":
        (folder / "policy.hujson").unlink()
        target = fixture.root / "foreign-policy"
        target.write_bytes(EXPECTED_POLICY)
        target.chmod(0o600)
        (folder / "policy.hujson").symlink_to(target)
    if entry == "mode": folder.chmod(0o755)
    if entry == "state":
        fixture.service_directories()
        (fixture.root / "var/lib/happyranch-headscale/db.sqlite").write_bytes(b"foreign")
    if entry == "unit": (fixture.root / "etc/systemd/system/happyranch-headscale.service").write_bytes(b"foreign")
    if entry == "dropin": (fixture.root / "etc/systemd/system/happyranch-headscale.service.d").mkdir()
    if entry in {"prefix-dropin", "type-dropin"}:
        (fixture.root / "etc/systemd/system" / ("happyranch-.service.d" if entry == "prefix-dropin"
                                                 else "service.d")).mkdir()
    if entry == "lib-foreign-link": (fixture.root / "lib").symlink_to("foreign-lib")
    identities = fixture.present, fixture.group_present, fixture.receipt().exists()
    def snapshot():
        return {str(p.relative_to(fixture.root)): (p.lstat().st_mode, p.lstat().st_uid,
                    p.lstat().st_gid, p.lstat().st_ino, p.lstat().st_nlink,
                    os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
                for p in fixture.root.rglob("*")}
    contents = snapshot()
    before = sorted(str(p.relative_to(fixture.root)) for p in fixture.root.rglob("*"))
    fixture.calls.clear()
    code, output = fixture.prepare(monkeypatch, capsys)
    if entry.startswith("activation:") and kind == "unrelated":
        assert code == 0, output
        assert json.loads(output)["state"] == "inert"
        after = snapshot()
        # Successful preparation may add child directories (changing st_nlink).
        assert all((after[name][:4], after[name][-1]) == (value[:4], value[-1])
                   for name, value in contents.items())
        assert after[str(foreign.relative_to(fixture.root))] == contents[str(foreign.relative_to(fixture.root))]
        assert foreign.read_bytes() == data
        assert fixture.present and fixture.group_present
        assert fixture.receipt().exists() == (account != "unowned")
        assert (fixture.root / "etc/systemd/system" / home.UNIT).read_bytes() == EXPECTED_UNIT.encode()
        assert all(args[0] != "systemctl" or args[1] == "show" for args in fixture.calls)
        return
    assert code == 1 and ("foreign-" in output or "custody-uncertain" in output)
    if entry.startswith(("load:", "prelink:")):
        assert "foreign-unit" in output
    if entry.startswith("activation:"):
        assert ("custody-uncertain" if kind in {"oversized", "unreadable"} else "foreign-unit") in output
    assert "synthetic private diagnostic" not in output
    assert snapshot() == contents
    assert sorted(str(p.relative_to(fixture.root)) for p in fixture.root.rglob("*")) == before
    assert all(args[0] in {"getent", "passwd"} for args in fixture.calls)
    assert (fixture.present, fixture.group_present, fixture.receipt().exists()) == identities
    code, output = fixture.cleanup(monkeypatch, capsys)
    assert code == 1 and "cleanup-uncertain" in output
    assert sorted(str(p.relative_to(fixture.root)) for p in fixture.root.rglob("*")) == before
    assert (fixture.present, fixture.group_present, fixture.receipt().exists()) == identities
    assert snapshot() == contents
    assert not any(args[0] in {"userdel", "groupdel", "useradd", "systemctl"} for args in fixture.calls)


def test_policy_and_unit_exact_bytes() -> None:
    assert home.render_policy() == EXPECTED_POLICY
    assert systemd_unit.render_headscale_unit() == EXPECTED_UNIT


@pytest.mark.parametrize("occupied", [None, 0, 1, 2, 3, "uncertain"])
def test_fixed_port_bindings_and_typed_refusal(monkeypatch, occupied) -> None:
    calls, closed = [], []
    class Probe:
        def __init__(self, family, kind):
            assert family == socket.AF_INET
            self.kind = kind
        def __enter__(self): return self
        def __exit__(self, *args): closed.append(self.kind)
        def bind(self, address):
            index = len(calls)
            calls.append((address, self.kind))
            if index == occupied or occupied == "uncertain":
                raise OSError(errno.EADDRINUSE if occupied != "uncertain" else errno.EACCES,
                              "synthetic private diagnostic")
    monkeypatch.setattr(home.socket, "socket", Probe)
    if occupied is None:
        home.preflight_ports("192.168.1.40")
    else:
        with pytest.raises(home.HeadscaleServiceError) as caught:
            home.preflight_ports("192.168.1.40")
        assert str(caught.value) == ("port-check-uncertain" if occupied == "uncertain" else "occupied-port")
        assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert calls == [(("192.168.1.40", 8443), socket.SOCK_STREAM),
                     (("192.168.1.40", 3478), socket.SOCK_DGRAM),
                     (("127.0.0.1", 19090), socket.SOCK_STREAM),
                     (("127.0.0.1", 50443), socket.SOCK_STREAM)][:len(calls)]
    assert len(calls) == (4 if occupied is None else 1 if occupied == "uncertain" else occupied + 1)
    assert len(closed) == len(calls)


@pytest.mark.parametrize("address", ["0.0.0.0", "127.0.0.1", "::1", "8.8.8.8", "192.168.1.040", "bad"])
def test_invalid_dial_ip_never_binds(monkeypatch, address) -> None:
    def forbidden(*args): raise AssertionError("must not inspect host sockets")
    monkeypatch.setattr(home.socket, "socket", forbidden)
    with pytest.raises(home.HeadscaleServiceError, match="^dial-ip$"):
        home.preflight_ports(address)


@pytest.mark.parametrize("ready", [True, False])
def test_readiness_is_fixed_local_and_bounded(monkeypatch, ready) -> None:
    clock, closed = [0.0], []
    monkeypatch.setattr(home.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(home.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port) == ("127.0.0.1", 19090)
            assert 0 < timeout <= 1.0
        def request(self, method, path): assert (method, path) == ("GET", "/metrics")
        def getresponse(self): return SimpleNamespace(status=200 if ready else 503)
        def close(self): closed.append(True)
    monkeypatch.setattr(home.http.client, "HTTPConnection", Connection)
    if ready:
        home.wait_ready()
        assert clock[0] == 0.0 and len(closed) == 1
    else:
        with pytest.raises(home.HeadscaleServiceError, match="^readiness-timeout$"):
            home.wait_ready()
        assert 30.0 <= clock[0] < 30.1 and closed


@pytest.mark.parametrize("command", ["disable-headscale", "uninstall"])
@pytest.mark.parametrize("case", ["created", "userdel-fail", "groupdel-fail-retry", "uid-reused",
                                  "both-absent", "process", "unit", "group-member", "gid-changed",
                                  "shape-changed", "service-fail", "objects-absent", "unit-absent",
                                  "retry-group-member", "retry-gid-changed", "retry-primary-shared", "uid-numeric-reused",
                                  "foreign-alias", "foreign-wants", "foreign-requires", "manual-managed-link",
                                  "runtime-alias", "runtime-wants", "runtime-requires", "dangling-alias",
                                  "unrelated-links", "merged-usr-relative", "merged-usr-absolute"])
def test_cleanup_receipted_transitions(fixture, monkeypatch, capsys, command, case) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    fixture.service_directories()
    receipt = fixture.receipt().read_bytes()
    payload = fixture.root / "opt/happyranch-desktop/libexec/headscale/headscale"
    notices = fixture.root / "opt/happyranch-desktop/licenses/headscale.txt"
    for path in (payload, notices):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic inert package")
    if case == "userdel-fail": fixture.exit_userdel = 1
    if case == "groupdel-fail-retry": fixture.exit_groupdel = 1
    if case in {"uid-reused", "both-absent", "objects-absent", "retry-group-member",
                "retry-gid-changed", "retry-primary-shared", "uid-numeric-reused"}: fixture.present = False
    if case == "uid-reused": fixture.reused_uid = True
    if case == "uid-numeric-reused": fixture.reused_uid = "numeric-only"
    if case in {"both-absent", "objects-absent"}: fixture.group_present = False
    if case in {"objects-absent", "unit-absent"}:
        (fixture.root / "etc/systemd/system" / home.UNIT).unlink()
    if case == "objects-absent":
        for name in ("policy.hujson", *CONFIG_FILES, *LEAF_FILES):
            (fixture.root / home.CUSTODY[0] / name).unlink()
        (fixture.receipt().parent / PUBLICATION).unlink()
        (fixture.root / CONTROL_CA).unlink()
        (fixture.root / EMPTY_CA).rmdir()
        for path in home.CUSTODY: (fixture.root / path).rmdir()
    if case == "retry-group-member": fixture.group.gr_mem = ["other"]
    if case == "retry-gid-changed": fixture.group.gr_gid += 1
    if case == "retry-primary-shared": fixture.shared = True
    if case == "process": fixture.busy = True
    if case == "unit": fixture.unit_busy = True
    if case == "group-member": fixture.group.gr_mem = ["other"]
    if case == "gid-changed": fixture.group.gr_gid += 1
    if case == "shape-changed": fixture.user.pw_shell = "/bin/bash"
    if case == "service-fail": fixture.fail_service = True
    if case in {"merged-usr-relative", "merged-usr-absolute"}:
        (fixture.root / "usr").mkdir(mode=0o755)
        (fixture.root / "usr/lib").mkdir(mode=0o755)
        (fixture.root / "lib").symlink_to("usr/lib" if case.endswith("relative") else "/usr/lib")
        assert fixture.prepare(monkeypatch, capsys)[0] == 0
    foreign_links = []
    if case in {"foreign-alias", "foreign-wants", "foreign-requires", "manual-managed-link",
                "runtime-alias", "runtime-wants", "runtime-requires", "dangling-alias",
                "unrelated-links"}:
        config = fixture.root / ("run/systemd/system" if case.startswith("runtime-") else "etc/systemd/system")
        for parent in (*reversed(config.parents), config):
            if parent.is_relative_to(fixture.root) and parent != fixture.root:
                parent.mkdir(exist_ok=True, mode=0o755)
        target = fixture.root / "opt/foreign" / home.UNIT
        if case != "dangling-alias":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"synthetic private diagnostic")
        if case in {"foreign-wants", "runtime-wants", "foreign-requires", "runtime-requires"}:
            link = config / ("operator.target.requires" if case.endswith("requires") else "operator.target.wants") / home.UNIT
            link.parent.mkdir(mode=0o755)
        else:
            link = config / "operator-alias.service"
        if case == "manual-managed-link": target = fixture.root / "etc/systemd/system" / home.UNIT
        if case == "unrelated-links": target = fixture.root / "opt/foreign/other.service"
        link.symlink_to(target)
        foreign_links.append((link, os.readlink(link)))
    def snapshot():
        return {str(p.relative_to(fixture.root)): (p.lstat().st_mode,
                    os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
                for p in fixture.root.rglob("*")}
    before = snapshot() if foreign_links else None
    fixture.calls.clear()
    code, output = fixture.cleanup(monkeypatch, capsys, command)
    assert "synthetic private diagnostic" not in output
    deletion = [args[0] for args in fixture.calls if args[0] in {"userdel", "groupdel", "useradd"}]
    if case in {"created", "both-absent", "objects-absent", "unit-absent", "unrelated-links",
                "merged-usr-relative", "merged-usr-absolute"}:
        assert code == 0 and output.strip(), output
        assert json.loads(output) == {"state": "removed", "account": "removed",
                                      **({"home_ca": "preserved"} if case != "objects-absent" else {})}
        assert deletion == ([] if case in {"both-absent", "objects-absent"} else ["userdel", "groupdel"])
    else:
        assert code == 1 and "cleanup-uncertain" in output
        assert fixture.receipt().read_bytes() == receipt
        assert (fixture.root / "etc/happyranch/headscale/policy.hujson").read_bytes() == EXPECTED_POLICY
        assert (fixture.root / "etc/systemd/system" / home.UNIT).read_bytes() == EXPECTED_UNIT.encode()
        if foreign_links:
            assert all(link.is_symlink() and os.readlink(link) == target for link, target in foreign_links)
            assert snapshot() == before
            assert not any(args[0] == "systemctl" and args[1] in {"stop", "disable"} for args in fixture.calls)
        if case == "groupdel-fail-retry":
            assert "partial-cleanup-uncertain" in output
            assert not fixture.present and fixture.group_present
            assert deletion == ["userdel", "groupdel"]
            fixture.exit_groupdel = 0
            fixture.calls.clear()
            code, output = fixture.cleanup(monkeypatch, capsys, command)
            assert code == 0 and json.loads(output)["state"] == "removed"
            assert [args[0] for args in fixture.calls if args[0] in {"userdel", "groupdel", "useradd"}] == ["groupdel"]
        else:
            assert deletion == (["userdel"] if case == "userdel-fail" else [])
            assert fixture.present == (case not in {"uid-reused", "retry-group-member", "retry-gid-changed",
                                                    "retry-primary-shared", "uid-numeric-reused"}) and fixture.group_present
            return
    assert not fixture.present and not fixture.group_present and not fixture.receipt().exists()
    assert not any((fixture.root / path).exists() for path in home.CUSTODY)
    assert not (fixture.root / "etc/systemd/system" / home.UNIT).exists()
    assert payload.read_bytes() == notices.read_bytes() == b"synthetic inert package"
    assert all(link.is_symlink() and os.readlink(link) == target for link, target in foreign_links)
    if case in {"merged-usr-relative", "merged-usr-absolute"}:
        assert os.readlink(fixture.root / "lib") == ("usr/lib" if case.endswith("relative") else "/usr/lib")
    assert not any(args[0] == "systemctl" and args[1] in {"start", "enable", "restart"} for args in fixture.calls)


@pytest.mark.parametrize("case,category", [
    ("ecmp", "multiple_addresses"), ("ipv6", "dial_ip"),
    ("unresolved", "inventory"), ("unresolved-second", "inventory"), ("unknown-interface", "inventory"),
    ("down", "no_address"), ("loopback", "no_address"),
    ("duplicate-interface", "inventory"), ("interrupted", "inventory"),
    ("error", "inventory"), ("truncated", "inventory"), ("no-route", "no_address"),
])
def test_kernel_inventory_refuses_before_publication(fixture, monkeypatch, capsys, case, category) -> None:
    fixture.kernel_case = case
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and category in output
    assert not fixture.preflights
    assert not fixture.receipt().exists()
    assert not (fixture.root / "etc/happyranch").exists()
    assert fixture.calls == []


def test_config_publication_idempotency_and_address_change(fixture, monkeypatch, capsys) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    root = fixture.root / home.CUSTODY[0]
    config, derp = root / "config.yaml", root / "derp.yaml"
    assert config.is_file() and derp.is_file()
    assert b"listen_addr: 192.168.1.40:8443\n" in config.read_bytes()
    assert b"ipv4: 192.168.1.40\n" in derp.read_bytes()
    receipt = fixture.receipt().parent / PUBLICATION
    record = json.loads(receipt.read_bytes())
    assert record == {"version": 1, "uid": fixture.uid, "gid": fixture.gid, "dial_ip": "192.168.1.40",
                      "files": {f"/{home.CUSTODY[0]}/{p.name}": hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (config, derp, root / "server.crt", root / "server.key")} |
                               {"/" + CONTROL_CA: hashlib.sha256((fixture.root / CONTROL_CA).read_bytes()).hexdigest()},
                      "directories": ["/" + EMPTY_CA]}
    leaf, key, ca = root / "server.crt", root / "server.key", fixture.root / CONTROL_CA
    assert leaf.read_bytes() == fixture.leaf_pem
    assert key.read_bytes() == fixture.leaf_key
    assert ca.read_bytes() == fixture.ca_pem
    paths = (config, derp, leaf, key, receipt)
    before = [(p.read_bytes(), p.stat()) for p in paths]
    assert all((p.stat().st_uid, p.stat().st_gid, p.stat().st_mode & 0o7777, p.stat().st_nlink)
               == (fixture.uid, fixture.gid, 0o600, 1) for p in paths)
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    assert [(p.read_bytes(), p.stat()) for p in paths] == before
    unchanged = [ca, fixture.root / EMPTY_CA,
                 *(fixture.receipt().parent / "home-ca" / name for name in ("ca.pem", "ca.key", "leaf.pem", "leaf.key"))]
    def metadata(path):
        info = path.stat()
        return info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns
    stable = [metadata(path) for path in unchanged]
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    assert [metadata(path) for path in unchanged] == stable
    fixture.dial_ip = "192.168.1.41"
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and "address-changed" in output
    assert [(p.read_bytes(), p.stat()) for p in paths] == before
    assert fixture.preflights == ["192.168.1.40"] * 3
    assert all(args[0] != "systemctl" or args[1] == "show" for args in fixture.calls)
    assert (fixture.root / "etc/systemd/system" / home.UNIT).read_bytes() == EXPECTED_UNIT.encode()
    assert (ca.stat().st_uid, ca.stat().st_gid, ca.stat().st_mode & 0o7777, ca.stat().st_nlink) == (fixture.uid, fixture.gid, 0o640, 1)
    empty = fixture.root / EMPTY_CA
    assert not list(empty.iterdir())
    assert (empty.stat().st_uid, empty.stat().st_gid, empty.stat().st_mode & 0o7777, empty.stat().st_nlink) == (fixture.uid, fixture.gid, 0o755, 2)
    assert [args[1] for args in fixture.calls if args[0] == MATERIALIZER] == ["ensure", "status", *(["status", "status"] * 2)]


@pytest.mark.parametrize("name", PUBLISHED)
@pytest.mark.parametrize("tamper", ["bytes", "mode", "link", "hardlink", "pending", "missing",
                                  "directory", "fifo", "oversized"])
def test_publication_tamper_is_preserved(fixture, monkeypatch, capsys, name, tamper) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    parent = (fixture.receipt().parent if name == PUBLICATION else fixture.root / "etc/happyranch"
              if name == "control-ca.pem" else fixture.root / home.CUSTODY[0])
    path = parent / name
    assert path.is_file()
    if tamper == "bytes": path.write_bytes(b"synthetic foreign bytes")
    if tamper == "mode": path.chmod(0o644)
    if tamper == "link":
        foreign = fixture.root / ("foreign-" + name)
        path.rename(foreign)
        path.symlink_to(foreign)
    if tamper == "hardlink": os.link(path, fixture.root / ("foreign-" + name))
    if tamper == "pending": (parent / ("." + name + ".pending")).write_bytes(b"synthetic pending")
    if tamper == "missing": path.unlink()
    if tamper in {"directory", "fifo"}:
        path.unlink()
        if tamper == "directory": path.mkdir()
        else: os.mkfifo(path)
    if tamper == "oversized": path.write_bytes(b"x" * 65537)
    def snapshot():
        return {str(p.relative_to(fixture.root)): (p.lstat().st_mode, p.lstat().st_ino,
                os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
                for p in fixture.root.rglob("*")}
    before = snapshot()
    fixture.calls.clear()
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and "synthetic" not in output
    assert snapshot() == before
    code, output = fixture.cleanup(monkeypatch, capsys)
    assert code == 1 and "cleanup-uncertain" in output
    assert snapshot() == before
    assert not any(args[0] in {"useradd", "userdel", "groupdel", "systemctl"} for args in fixture.calls)


@pytest.mark.parametrize("crash", PUBLISHED)
def test_interrupted_publication_is_never_adopted(fixture, monkeypatch, capsys, crash) -> None:
    original = fixture.installer._write_provision_file
    def interrupted(name, *args, **kwargs):
        original(name, *args, **kwargs)
        if name == crash: raise OSError("synthetic crash")
    with monkeypatch.context() as patch:
        patch.setattr(fixture.installer, "_write_provision_file", interrupted)
        assert fixture.prepare(monkeypatch, capsys)[0] == 1
    # A durable final receipt is the commit point; lost success is idempotent.
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == (0 if crash == PUBLICATION else 1)
    if crash != PUBLICATION:
        fixture.calls.clear()
        code, output = fixture.cleanup(monkeypatch, capsys)
        assert code == 1 and "cleanup-uncertain" in output
        assert not any(args[0] in {"userdel", "groupdel", "systemctl"} for args in fixture.calls)


def test_publication_cleanup_preserves_ca_staging(fixture, monkeypatch, capsys) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    staging = fixture.receipt().parent / "home-ca"
    assert staging.is_dir()
    key = staging / "ca.key"
    key.write_bytes(b"synthetic private staging")
    key.chmod(0o600)
    before = key.read_bytes(), key.stat()
    code, output = fixture.cleanup(monkeypatch, capsys)
    assert code == 0 and json.loads(output)["home_ca"] == "preserved"
    assert (key.read_bytes(), key.stat()) == before
    assert not (fixture.root / home.CUSTODY[0]).exists()
    assert not (fixture.receipt().parent / PUBLICATION).exists()


def test_port_preflight_precedes_all_publication(fixture, monkeypatch, capsys) -> None:
    def occupied(address):
        assert address == "192.168.1.40"
        assert not fixture.receipt().exists()
        assert not (fixture.root / "etc/happyranch").exists()
        raise home.HeadscaleServiceError("occupied-port")
    monkeypatch.setattr(home, "preflight_ports", occupied)
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and "occupied-port" in output
    assert not fixture.receipt().exists()
    assert fixture.calls == []


@pytest.mark.parametrize("name", [*PUBLISHED, "empty-ca-directory"])
def test_publication_never_replaces_late_foreign_inode(fixture, monkeypatch, capsys, name) -> None:
    rename = fixture.installer._rename_provision_entry
    def arrive(parent, source, target, **kwargs):
        if target == name:
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
            with os.fdopen(fd, "wb") as stream: stream.write(b"synthetic late foreign")
        rename(parent, source, target, **kwargs)
    monkeypatch.setattr(fixture.installer, "_rename_provision_entry", arrive)
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and "synthetic" not in output
    parent = (fixture.receipt().parent if name == PUBLICATION else fixture.root / "etc/happyranch"
              if name in {"control-ca.pem", "empty-ca-directory"} else fixture.root / home.CUSTODY[0])
    assert (parent / name).read_bytes() == b"synthetic late foreign"


@pytest.mark.parametrize("parent", [home.CUSTODY[0], home.INSTALLER_STATE, "etc/happyranch", EMPTY_CA])
@pytest.mark.parametrize("tamper", ["mode", "link"])
def test_publication_parent_custody_is_preserved(fixture, monkeypatch, capsys, parent, tamper) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    path = fixture.root / parent
    if tamper == "mode": path.chmod(0o777)
    else:
        path.rename(path.with_name(path.name + "-foreign"))
        path.symlink_to(path.name + "-foreign", target_is_directory=True)
    before = path.lstat()
    fixture.calls.clear()
    assert fixture.prepare(monkeypatch, capsys)[0] == 1
    code, output = fixture.cleanup(monkeypatch, capsys)
    assert code == 1 and "cleanup-uncertain" in output
    after = path.lstat()
    assert (before.st_ino, before.st_mode) == (after.st_ino, after.st_mode)
    assert not any(args[0] in {"userdel", "groupdel", "systemctl"} for args in fixture.calls)


@pytest.mark.parametrize("name", [*CONFIG_FILES, *LEAF_FILES, "control-ca.pem", "empty-ca-directory"])
def test_cleanup_interruption_keeps_receipt_and_refuses_retry(fixture, monkeypatch, capsys, name) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    receipt = fixture.receipt().parent / PUBLICATION
    assert receipt.is_file()
    original = receipt.read_bytes()
    remove = os.rmdir if name == "empty-ca-directory" else os.unlink
    def interrupted(path, *args, **kwargs):
        remove(path, *args, **kwargs)
        if path == name: raise OSError("synthetic lost cleanup")
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "rmdir" if name == "empty-ca-directory" else "unlink", interrupted)
        code, output = fixture.cleanup(monkeypatch, capsys)
    assert code == 1 and "partial-cleanup-uncertain" in output
    assert receipt.read_bytes() == original
    assert fixture.present and fixture.group_present
    fixture.calls.clear()
    code, output = fixture.cleanup(monkeypatch, capsys)
    assert code == 1 and "cleanup-uncertain" in output
    assert receipt.read_bytes() == original
    assert not any(args[0] in {"userdel", "groupdel", "systemctl"} for args in fixture.calls)


@pytest.mark.parametrize("busy", ["uid", "unit"])
def test_preparation_never_adopts_running_service(fixture, monkeypatch, capsys, busy) -> None:
    fixture.present = fixture.group_present = True
    fixture.busy, fixture.unit_busy = busy == "uid", busy == "unit"
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and "cleanup-uncertain" in output
    assert not fixture.receipt().exists()
    assert not (fixture.root / "etc/happyranch").exists()
    assert all(args[0] != "systemctl" or args[1] == "show" for args in fixture.calls)


@pytest.mark.parametrize("tamper", ["uid", "gid", "bool-id", "version", "path", "digest", "address", "extra"])
def test_publication_receipt_binds_exact_identity_and_files(fixture, monkeypatch, capsys, tamper) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    receipt = fixture.receipt().parent / PUBLICATION
    assert receipt.is_file()
    record = json.loads(receipt.read_bytes())
    if tamper in {"uid", "gid"}: record[tamper] += 1
    if tamper == "bool-id": record["uid"] = True
    if tamper == "version": record["version"] = True
    if tamper == "path": record["files"]["/foreign/config.yaml"] = record["files"].pop("/etc/happyranch/headscale/config.yaml")
    if tamper == "digest": record["files"]["/etc/happyranch/headscale/config.yaml"] = "0" * 64
    if tamper == "address": record["dial_ip"] = "192.168.1.41"
    if tamper == "extra": record["extra"] = "synthetic private diagnostic"
    receipt.write_text(json.dumps(record))
    before = receipt.read_bytes(), receipt.stat()
    fixture.calls.clear()
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and "synthetic" not in output
    code, output = fixture.cleanup(monkeypatch, capsys)
    assert code == 1 and "cleanup-uncertain" in output
    assert (receipt.read_bytes(), receipt.stat()) == before
    assert fixture.present and fixture.group_present
    assert not any(args[0] in {"userdel", "groupdel", "systemctl"} for args in fixture.calls)


@pytest.mark.parametrize("entry", ["control-ca.pem", ".control-ca.pem.pending",
                                  "empty-ca-directory", ".empty-ca-directory.pending",
                                  "server.crt", "server.key", ".server.crt.pending", ".server.key.pending"])
@pytest.mark.parametrize("account", ["absent", "unowned", "owned"])
def test_unreceipted_handoff_is_foreign(fixture, monkeypatch, capsys, entry, account) -> None:
    fixture.present = fixture.group_present = account != "absent"
    if account == "owned":
        assert fixture.prepare(monkeypatch, capsys)[0] == 0
        (fixture.receipt().parent / PUBLICATION).unlink()
    parent = fixture.root / ("etc/happyranch" if "ca." in entry or "directory" in entry else home.CUSTODY[0])
    parent.mkdir(parents=True, exist_ok=True, mode=0o700 if parent.name == "headscale" else 0o755)
    path = parent / entry
    if "directory" in entry:
        path.mkdir(exist_ok=True, mode=0o755)
    else:
        path.write_bytes(b"synthetic foreign")
        path.chmod(0o600)
    before = path.lstat()
    fixture.calls.clear()
    assert fixture.prepare(monkeypatch, capsys)[0] == 1
    assert fixture.cleanup(monkeypatch, capsys)[0] == 1
    assert path.lstat() == before
    assert not any(args[0] in {"useradd", "userdel", "groupdel", MATERIALIZER} for args in fixture.calls)
    assert not any(args[0] == "systemctl" and args[1] != "show" for args in fixture.calls)


@pytest.mark.parametrize("tamper", ["nonempty", "subdirectory", "mode", "link", "pending", "missing"])
def test_empty_ca_directory_tamper_preserved(fixture, monkeypatch, capsys, tamper) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    path = fixture.root / EMPTY_CA
    if tamper == "nonempty": (path / "foreign").write_bytes(b"synthetic")
    if tamper == "subdirectory": (path / "foreign").mkdir()
    if tamper == "mode": path.chmod(0o700)
    if tamper == "link":
        path.rename(path.with_name("foreign"))
        path.symlink_to("foreign", target_is_directory=True)
    if tamper == "pending": path.with_name(".empty-ca-directory.pending").mkdir()
    if tamper == "missing": path.rmdir()
    if path.is_dir():
        # Prime read access before the snapshot; admission may read this directory.
        list(path.iterdir())
    before = path.lstat() if path.exists() or path.is_symlink() else None
    fixture.calls.clear()
    assert fixture.prepare(monkeypatch, capsys)[0] == 1
    assert fixture.cleanup(monkeypatch, capsys)[0] == 1
    assert (path.lstat() if path.exists() or path.is_symlink() else None) == before
    assert not any(args[0] in {"userdel", "groupdel", MATERIALIZER} for args in fixture.calls)


@pytest.mark.parametrize("result", [
    (1, "synthetic private diagnostic"), (0, "{}"), (0, "[]"), (0, "{}\n{}\n"),
    (0, '{"ca_fingerprint":"x","ca_fingerprint":"y"}'), (0, "x" * 4097),
    *[(0, json.dumps(dict(ca_fingerprint=fp, ca_not_after=ca, leaf_not_after=leaf, renewal_due=due)))
      for fp, ca, leaf, due in [
          ("0"*64, "2030-01-01T00:00:00Z", "2027-01-01T00:00:00Z", "false"),
          ("0"*64, "yesterday", "2027-01-01T00:00:00Z", False),
          ("0"*64, "2030-01-01T00:00:00Z", "", False),
          ("0"*64, "2030-01-01T00:00:00Z", "2027-01-01T00:00:00Z", False),
          ("A"*64, "2030-01-01T00:00:00Z", "2027-01-01T00:00:00Z", False)]]])
def test_materializer_status_refuses_without_handoff(fixture, monkeypatch, capsys, result) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    receipt = fixture.receipt().parent / PUBLICATION
    before = receipt.read_bytes(), receipt.stat()
    fixture.calls.clear()
    fixture.materializer_result = result
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and "materializer-uncertain" in output and "synthetic" not in output
    assert (receipt.read_bytes(), receipt.stat()) == before
    assert [args[1] for args in fixture.calls if args[0] == MATERIALIZER] == ["status"]


@pytest.mark.parametrize("member,tamper", [
    *[(member, tamper) for member in ("leaf.pem", "leaf.key", "ca.pem", "ca.key", "binary")
      for tamper in ("mode", "link", "hardlink", "oversized", "fifo")
      if (member, tamper) != ("binary", "oversized")],
    *[("home-ca", tamper) for tamper in ("mode", "link", "unknown", "pending", "partial")]])
def test_materializer_source_custody_refuses(fixture, monkeypatch, capsys, member, tamper) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    path = (fixture.root / MATERIALIZER.lstrip("/") if member == "binary" else
            fixture.receipt().parent / "home-ca" if member == "home-ca" else
            fixture.receipt().parent / "home-ca" / member)
    if member == "home-ca" and tamper in {"unknown", "pending"}:
        (path / ("foreign" if tamper == "unknown" else ".leaf.key.pending")).write_bytes(b"synthetic")
    elif member == "home-ca" and tamper == "partial": (path / "leaf.pem").unlink()
    elif tamper == "mode": path.chmod(0o777)
    elif tamper == "link":
        foreign = fixture.root / ("foreign-" + member)
        path.rename(foreign)
        path.symlink_to(foreign, target_is_directory=member == "home-ca")
    elif tamper == "hardlink": os.link(path, fixture.root / ("foreign-" + member))
    elif tamper == "fifo":
        path.unlink()
        os.mkfifo(path)
    else: path.write_bytes(b"x" * 4097 if member != "binary" else b"synthetic")
    if path.is_dir():
        # Retain the full stat assertion without confusing read-atime with a write.
        list(path.iterdir())
    before = path.lstat()
    publication = (fixture.receipt().parent / PUBLICATION).read_bytes()
    fixture.calls.clear()
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and "synthetic" not in output
    assert path.lstat() == before
    assert (fixture.receipt().parent / PUBLICATION).read_bytes() == publication
    assert not any(args[0] == MATERIALIZER for args in fixture.calls)


def test_due_leaf_renewal_uses_same_ca_and_receipt(fixture, monkeypatch, capsys) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    ca_key = fixture.receipt().parent / "home-ca/ca.key"
    ca = fixture.root / CONTROL_CA
    config = fixture.root / home.CUSTODY[0] / "config.yaml"
    before = [(p.read_bytes(), p.stat()) for p in (ca_key, ca, config)]
    key = fixture.root / home.CUSTODY[0] / "server.key"
    old_key = key.read_bytes()
    fixture.calls.clear()
    fixture.materializer_due = True
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    assert [args[1] for args in fixture.calls if args[0] == MATERIALIZER] == ["status", "renew-leaf", "status"]
    assert key.read_bytes() != old_key and key.read_bytes() == fixture.leaf_key
    assert [(p.read_bytes(), p.stat()) for p in (ca_key, ca, config)] == before
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    assert fixture.cleanup(monkeypatch, capsys)[0] == 0
    assert ca_key.read_bytes() == before[0][0]


def test_ca_private_key_is_never_read_by_installer(fixture, monkeypatch, capsys) -> None:
    read = fixture.installer._read_file
    def public_or_leaf(parent, name, **kwargs):
        assert name != "ca.key"
        return read(parent, name, **kwargs)
    monkeypatch.setattr(fixture.installer, "_read_file", public_or_leaf)
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    assert fixture.cleanup(monkeypatch, capsys)[0] == 0
    assert (fixture.receipt().parent / "home-ca/ca.key").is_file()


def test_merged_config_only_receipt_extends_without_rewriting_config(fixture, monkeypatch, capsys) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    receipt = fixture.receipt().parent / PUBLICATION
    record = json.loads(receipt.read_bytes())
    for path in list(record["files"]):
        if Path(path).name not in CONFIG_FILES:
            (fixture.root / path.lstrip("/")).unlink()
            del record["files"][path]
    del record["directories"]
    (fixture.root / EMPTY_CA).rmdir()
    receipt.write_text(json.dumps(record))
    paths = [fixture.root / home.CUSTODY[0] / name for name in CONFIG_FILES]
    before = [(p.read_bytes(), p.stat()) for p in paths]
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    assert [(p.read_bytes(), p.stat()) for p in paths] == before
    assert json.loads(receipt.read_bytes())["directories"] == ["/" + EMPTY_CA]
    assert fixture.cleanup(monkeypatch, capsys)[0] == 0


@pytest.mark.parametrize("name", ["server.crt", "server.key", "control-ca.pem", "empty-ca-directory"])
@pytest.mark.parametrize("field", ["uid", "gid"])
def test_handoff_owner_mismatch_preserves_all(fixture, monkeypatch, capsys, name, field) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    path = fixture.root / (CONTROL_CA if name == "control-ca.pem" else EMPTY_CA if name == "empty-ca-directory"
                           else home.CUSTODY[0] + "/" + name)
    before = (path.read_bytes() if path.is_file() else None), path.stat()
    inode = path.stat().st_dev, path.stat().st_ino
    original_stat, original_fstat = os.stat, os.fstat
    def mismatched(info):
        if (info.st_dev, info.st_ino) == inode:
            fields = list(info)
            fields[4 if field == "uid" else 5] += 1
            return os.stat_result(fields)
        return info
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "stat", lambda *a, **kw: mismatched(original_stat(*a, **kw)))
        patch.setattr(module.os, "fstat", lambda *a, **kw: mismatched(original_fstat(*a, **kw)))
        fixture.calls.clear()
        assert fixture.prepare(monkeypatch, capsys)[0] == 1
        assert fixture.cleanup(monkeypatch, capsys)[0] == 1
    assert ((path.read_bytes() if path.is_file() else None), path.stat()) == before
    assert not any(args[0] in {"userdel", "groupdel", MATERIALIZER} for args in fixture.calls)


@pytest.mark.parametrize("crash", ["server.key", "server.crt", PUBLICATION, "empty-ca-directory"])
def test_renewal_or_extension_crash_preserves_uncertainty(fixture, monkeypatch, capsys, crash) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    if crash == "empty-ca-directory":
        receipt = fixture.receipt().parent / PUBLICATION
        record = json.loads(receipt.read_bytes())
        for path in list(record["files"]):
            if Path(path).name not in CONFIG_FILES:
                (fixture.root / path.lstrip("/")).unlink()
                del record["files"][path]
        del record["directories"]
        (fixture.root / EMPTY_CA).rmdir()
        receipt.write_text(json.dumps(record))
    else:
        fixture.materializer_due = True
    rename = fixture.installer._rename_provision_entry
    def interrupted(parent, source, target, **kwargs):
        rename(parent, source, target, **kwargs)
        if target == crash: raise OSError("synthetic lost publication")
    with monkeypatch.context() as patch:
        patch.setattr(fixture.installer, "_rename_provision_entry", interrupted)
        assert fixture.prepare(monkeypatch, capsys)[0] == 1
    assert fixture.prepare(monkeypatch, capsys)[0] == (0 if crash == PUBLICATION else 1)
    if crash != PUBLICATION:
        assert fixture.cleanup(monkeypatch, capsys)[0] == 1


@pytest.mark.parametrize("name", ["ca.pem", "leaf.pem", "leaf.key"])
def test_changed_staging_bytes_refuse_before_materializer(fixture, monkeypatch, capsys, name) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    path = fixture.receipt().parent / "home-ca" / name
    path.write_bytes(b"synthetic changed staging")
    before = path.read_bytes(), path.stat()
    fixture.materializer_due = True
    fixture.calls.clear()
    assert fixture.prepare(monkeypatch, capsys)[0] == 1
    assert not any(args[0] == MATERIALIZER for args in fixture.calls)
    assert (path.read_bytes(), path.stat()) == before


@pytest.mark.parametrize("name", ["server.key", "server.crt", PUBLICATION])
def test_interrupted_renewal_removal_is_never_repaired(fixture, monkeypatch, capsys, name) -> None:
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    fixture.materializer_due = True
    unlink = os.unlink
    def interrupted(path, *args, **kwargs):
        unlink(path, *args, **kwargs)
        if path == name: raise OSError("synthetic interrupted renewal")
    with monkeypatch.context() as patch:
        patch.setattr(module.os, "unlink", interrupted)
        assert fixture.prepare(monkeypatch, capsys)[0] == 1
    assert fixture.prepare(monkeypatch, capsys)[0] == 1
    assert fixture.cleanup(monkeypatch, capsys)[0] == 1


# B6 extends this retained installer venue; no host account/service/socket is used.
_NATIVE_SOCKET = socket.socket
_HOME_EXPECTED = dict(expected_release_uid=1000, expected_home_id="home",
                      expected_control_plane_id="control", expected_connector_id="connector",
                      expected_generation=7)
_B6_LIVE = (
    "var/lib/happyranch-headscale/db.sqlite", "var/lib/happyranch-headscale/db.sqlite-wal",
    "var/lib/happyranch-headscale/db.sqlite-shm", "var/lib/happyranch-headscale/noise.key",
    "var/lib/happyranch-headscale/derp.key", "run/happyranch-headscale/headscale.sock",
    "run/happyranch-tsnet-sidecar/observe.sock", "var/lib/happyranch-headscale/acme-unused",
)


def b6_setup(fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *,
             phase: str = "ready", live: bool = True, staging: bool = False) -> tuple[Path, Path]:
    from scripts.test_home_activation import receipt_record
    assert fixture.prepare(monkeypatch, capsys)[0] == 0
    fixture.service_directories()
    root = fixture.root
    if not staging:
        # Older B6 cases isolate live members; I3c below covers full staging.
        for name in ("ca.pem", "leaf.pem", "leaf.key"):
            (root / home.HOME_CA / name).unlink()
    lookup, run = fixture.lookup_user, fixture.run
    def user(name: str) -> SimpleNamespace:
        if name == "happyranch":
            return SimpleNamespace(pw_uid=fixture.uid, pw_gid=fixture.gid,
                                   pw_shell="/usr/sbin/nologin", pw_dir="/nonexistent")
        return lookup(name)
    fixture.installer._lookup_user = user
    unit = root / "etc/systemd/system" / home.UNIT
    unit.write_text(systemd_unit.render_headscale_unit(home_transport=True))
    invite = root / "etc/happyranch/invite-issuer.json"
    invite.write_bytes(b"synthetic protected invite configuration")
    invite.chmod(0o600)
    record = receipt_record(phase)
    record["ca_der_sha256"] = hashlib.sha256(b"synthetic public CA").hexdigest()
    record["member_digests"] = {path: hashlib.sha256((root / path.lstrip("/")).read_bytes()).hexdigest()
                                for path in record["member_digests"]}
    activation = root / home.INSTALLER_STATE / "home-activation.json"
    activation.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")))
    activation.chmod(0o600)
    runtime = root / "run/happyranch-tsnet-sidecar"
    runtime.mkdir(mode=0o755)
    link = root / "etc/systemd/system/happyranch-managed.target.wants" / home.UNIT
    link.parent.mkdir(mode=0o755)
    link.symlink_to("/etc/systemd/system/" + home.UNIT)
    def command(args: list[str], **kwargs: object) -> tuple[int, str]:
        if args[0] == "systemctl" and args[1] == "disable":
            assert args[-1] == home.UNIT
            link.unlink(missing_ok=True)
        if args[0] == "systemctl" and args[1] == "show" and args[2] != home.UNIT:
            fixture.calls.append(args)
            values = dict(LoadState="not-found", FragmentPath="", DropInPaths="", ActiveState="inactive",
                          SubState="dead", MainPID="0", ControlPID="0", ControlGroup="", Job="")
            return 0, "\n".join(f"{key}={values[key]}" for key in
                                (arg.split("=", 1)[1] for arg in args if arg.startswith("--property=")))
        return run(args, **kwargs)
    fixture.installer._run = command
    fixture.installer.manager._run = command
    if live:
        for relative in _B6_LIVE:
            path = root / relative
            if path.name == "acme-unused":
                path.mkdir(mode=0o700)
            elif path.suffix == ".sock":
                # Relative bind avoids Unix sockaddr's 108-byte path bound.
                previous = os.open(".", module._DIR_FLAGS)
                try:
                    os.chdir(path.parent)
                    with _NATIVE_SOCKET(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                        sock.bind(path.name)
                    path.chmod(0o600)
                finally:
                    os.fchdir(previous)
                    os.close(previous)
            else:
                path.write_bytes(b"synthetic live state")
                path.chmod(0o600)
    fixture.calls.clear()
    return activation, link


def b6_snapshot(root: Path) -> dict[str, tuple[int, ...]]:
    return {str(path.relative_to(root)): (path.lstat().st_ino, path.lstat().st_mode,
            path.lstat().st_uid, path.lstat().st_gid, path.lstat().st_nlink, path.lstat().st_size)
            for path in root.rglob("*")}


@pytest.mark.parametrize("mode", [0o600, 0o644])
@pytest.mark.parametrize("count", [2, 8])
def test_pre_stop_diagnostics_disable_off_reenable(
    composition: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, mode: int, count: int,
) -> None:
    v = composition
    expected = v.lifecycle.capture("Enable", os.getuid())
    assert v.lifecycle.dispatch("Enable", os.getuid(), "enable", expected,
                                deadline=time.monotonic() + 480) == {"outcome": "ready", "residue": []}
    v.observers.close()
    runtime = v.path("/run/happyranch-tsnet-sidecar")
    names = ["health.json", *(f".health-{index}" for index in range(count - 1))]
    for name in names:
        path = runtime / name
        path.write_bytes(b"\xff" * 65536)  # Diagnostics are opaque, including at the size ceiling.
        path.chmod(0o644 if name == "health.json" else mode)
    original = os.open
    def no_diagnostic_read(name: str, flags: int, *args: object, **kwargs: object) -> int:
        assert Path(name).name not in names
        return original(name, flags, *args, **kwargs)
    assert v.io.inventory().state == "owned-activated"
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", no_diagnostic_read)
        assert v.io.inventory().state == "owned-activated"
        expected = v.lifecycle.capture("Disable", os.getuid())
        assert v.lifecycle.dispatch("Disable", os.getuid(), "stop", expected) == {
            "outcome": "in_progress", "residue": []}
        assert v.lifecycle.dispatch("Disable", os.getuid(), "cleanup", expected,
                                    revoke_succeeded=True) == {"outcome": "off", "residue": []}
    assert not runtime.exists()  # The real B6 pass retired the admitted residue.
    assert v.receipts.current() is None and v.transactions.read_optional() is None
    v.admin.users, v.admin.nodes = (), ()
    expected = v.lifecycle.capture("Enable", os.getuid())
    assert v.lifecycle.dispatch("Enable", os.getuid(), "enable", expected,
                                deadline=time.monotonic() + 480) == {"outcome": "ready", "residue": []}
    assert v.receipts.current().phase == "ready" and len(v.mints) == 2


@pytest.mark.parametrize("phase", ["prepared", "activating", "joined", "ready", "failed_uncertain"])
@pytest.mark.parametrize("live", [True, False])
def test_b6_fixed_members_and_every_phase(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], phase: str, live: bool,
) -> None:
    activation, link = b6_setup(fixture, monkeypatch, capsys, phase=phase, live=live)
    ca_key = fixture.root / home.HOME_CA / "ca.key"
    original = os.open
    def no_socket_or_ca_read(name: str, flags: int, *args: object, **kwargs: object) -> int:
        assert name not in {"headscale.sock", "observe.sock", "ca.key"}
        return original(name, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", no_socket_or_ca_read)
    result = fixture.installer.cleanup_headscale(**_HOME_EXPECTED)
    assert result == {"state": "removed", "account": "removed", "home_ca": "removed"}
    assert all(not (fixture.root / path).exists() for path in _B6_LIVE)
    assert not ca_key.exists() and not activation.exists() and not link.is_symlink()
    assert not fixture.receipt().exists() and not fixture.present and not fixture.group_present
    assert [args[0] for args in fixture.calls if args[0] in {"userdel", "groupdel"}] == ["userdel", "groupdel"]
    assert not any(args[0] == "useradd" or args[:2] == ["systemctl", "stop"] for args in fixture.calls)


@pytest.mark.parametrize("revoked", [True, False])
def test_b6_retires_runtime_diagnostics_without_reading(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], revoked: bool,
) -> None:
    b6_setup(fixture, monkeypatch, capsys)
    runtime = fixture.root / "run/happyranch-tsnet-sidecar"
    names = ["health.json", *(f".health-{index}" for index in range(7))]
    for index, name in enumerate(names):
        path = runtime / name
        path.write_bytes(b"x" * 65536)
        path.chmod(0o644 if index % 2 == 0 else 0o600)
    original = os.open
    def no_diagnostic_read(name: str, flags: int, *args: object, **kwargs: object) -> int:
        assert Path(name).name not in names
        return original(name, flags, *args, **kwargs)
    monkeypatch.setattr(os, "open", no_diagnostic_read)
    assert fixture.installer.cleanup_headscale(**_HOME_EXPECTED, revoke_succeeded=revoked)["state"] == "removed"
    assert not runtime.exists()


@pytest.mark.parametrize("bad", ["symlink", "hardlink", "uid", "gid", "mode", "fifo",
                                 "unknown", "nine", "oversize"])
def test_b6_unsafe_runtime_diagnostics_retains_whole_directory(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad: str,
) -> None:
    activation, _ = b6_setup(fixture, monkeypatch, capsys)
    runtime = fixture.root / "run/happyranch-tsnet-sidecar"
    # An admissible entry precedes the bad entry: preflight must retain both.
    healthy = runtime / "health.json"
    healthy.write_bytes(b"synthetic diagnostic")
    healthy.chmod(0o644)
    path = runtime / ".health-123"
    path.write_bytes(b"synthetic interrupted diagnostic")
    path.chmod(0o600)
    if bad == "symlink":
        path.unlink()
        path.symlink_to(healthy.name)
    if bad == "hardlink": os.link(path, fixture.root / "held-diagnostic")
    if bad == "mode": path.chmod(0o640)
    if bad == "fifo":
        path.unlink()
        os.mkfifo(path, 0o600)
    if bad == "unknown": path.rename(runtime / ".health-not-numeric")
    if bad == "nine":
        for index in range(7):
            extra = runtime / f".health-{index}"
            extra.write_bytes(b"synthetic diagnostic")
            extra.chmod(0o644)
    if bad == "oversize": path.write_bytes(b"x" * 65537)
    if bad in {"uid", "gid"}:
        original = os.stat
        identity = path.lstat().st_ino
        def metadata(*args: object, **kwargs: object) -> os.stat_result | SimpleNamespace:
            info = original(*args, **kwargs)
            if info.st_ino != identity: return info
            value = SimpleNamespace(**{key: getattr(info, key) for key in dir(info) if key.startswith("st_")})
            field = "st_" + bad
            setattr(value, field, getattr(value, field) + 1)
            return value
        monkeypatch.setattr(os, "stat", metadata)
    before = b6_snapshot(runtime)
    with pytest.raises(home.HeadscaleServiceError, match="^partial-cleanup-uncertain$"):
        fixture.installer.cleanup_headscale(**_HOME_EXPECTED, revoke_succeeded=True)
    assert b6_snapshot(runtime) == before
    assert activation.exists() and fixture.receipt().exists()


@pytest.mark.parametrize("bad", ["absent", "generation", "release", "home", "control", "connector",
                                 "pending", "parse", "mode", "hardlink", "symlink", "parent-symlink", "headscale-unreceipted"])
def test_b6_invalid_receipt_has_no_effect(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad: str,
) -> None:
    activation, _ = b6_setup(fixture, monkeypatch, capsys)
    if bad == "absent": activation.unlink()
    if bad == "headscale-unreceipted":
        fixture.receipt().unlink()
        (fixture.root / home.INSTALLER_STATE / home.PUBLICATION).unlink()
    if bad in {"generation", "release", "home", "control", "connector"}:
        record = json.loads(activation.read_bytes())
        key = {"generation": "activation_generation", "release": "release_uid", "home": "home_id",
               "control": "control_plane_id", "connector": "connector_id"}[bad]
        record[key] = 8 if bad == "generation" else 1001 if bad == "release" else "foreign"
        activation.write_text(json.dumps(record))
    if bad == "pending": activation.with_name(".home-activation.json.pending").write_bytes(b"synthetic pending")
    if bad == "parse": activation.write_bytes(b"{}")
    if bad == "mode": activation.chmod(0o644)
    if bad == "hardlink": (fixture.root / "held-receipt").hardlink_to(activation)
    if bad == "symlink":
        activation.rename(fixture.root / "held-receipt")
        activation.symlink_to(fixture.root / "held-receipt")
    if bad == "parent-symlink":
        activation.parent.rename(fixture.root / "held-parent")
        activation.parent.symlink_to(fixture.root / "held-parent")
    before = b6_snapshot(fixture.root)
    with pytest.raises(home.HeadscaleServiceError, match="^cleanup-uncertain$"):
        fixture.installer.cleanup_headscale(**_HOME_EXPECTED)
    assert b6_snapshot(fixture.root) == before
    assert not any(args[0] in {"userdel", "groupdel"} or args[:2] == ["systemctl", "disable"] for args in fixture.calls)


@pytest.mark.parametrize("relative", _B6_LIVE)
@pytest.mark.parametrize("bad", ["type", "owner", "mode", "nlink", "symlink", "parent-symlink"])
def test_b6_unsafe_live_member_retained(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], relative: str, bad: str,
) -> None:
    b6_setup(fixture, monkeypatch, capsys)
    path = fixture.root / relative
    if bad == "type":
        if path.is_dir(): path.rmdir()
        else: path.unlink()
        path.write_bytes(b"synthetic wrong type") if relative.endswith((".sock", "acme-unused")) else os.mkfifo(path, 0o600)
    if bad == "mode": path.chmod(0o755 if path.is_dir() else 0o644)
    if bad == "symlink":
        path.rename(fixture.root / "held-member")
        path.symlink_to(fixture.root / "held-member")
    if bad == "parent-symlink":
        path.parent.rename(fixture.root / "held-parent")
        path.parent.symlink_to(fixture.root / "held-parent")
    if bad in {"owner", "nlink"}:
        original = os.stat
        identity = path.lstat().st_ino
        def metadata(*args: object, **kwargs: object) -> os.stat_result | SimpleNamespace:
            info = original(*args, **kwargs)
            if info.st_ino != identity: return info
            value = SimpleNamespace(**{key: getattr(info, key) for key in dir(info) if key.startswith("st_")})
            if bad == "owner": value.st_uid += 1
            else: value.st_nlink += 1
            return value
        monkeypatch.setattr(os, "stat", metadata)
    before = b6_snapshot(fixture.root)
    with pytest.raises(home.HeadscaleServiceError, match="^cleanup-uncertain$"):
        fixture.installer.cleanup_headscale(**_HOME_EXPECTED)
    assert b6_snapshot(fixture.root) == before
    assert fixture.receipt().exists()


@pytest.mark.parametrize("bad", ["acme-child", "extra", "headscale-active", "sidecar-job", "connector-pid",
                                 "verifier-cgroup", "uid-process", "ps-malformed", "ps-oversize", "ps-error"])
def test_b6_inventory_and_quiescence_precede_deletion(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad: str,
) -> None:
    b6_setup(fixture, monkeypatch, capsys)
    if bad in {"acme-child", "extra"}:
        path = fixture.root / "var/lib/happyranch-headscale" / ("acme-unused/keep" if bad == "acme-child" else "unknown")
        path.write_bytes(b"synthetic retained state")
    if bad == "headscale-active": fixture.unit_busy = True
    if bad == "uid-process": fixture.busy = True
    command = fixture.installer._run
    def native(args: list[str], **kwargs: object) -> tuple[int, str]:
        if args[0] == "ps":
            if bad == "ps-error": return 1, "synthetic private diagnostic"
            if bad == "ps-malformed": return 0, "invalid snapshot"
            if bad == "ps-oversize": return 0, " " * 65537
        code, output = command(args, **kwargs)
        if args[0] == "systemctl" and args[1] == "show":
            unit = {"sidecar-job": "happyranch-tsnet-sidecar.service",
                    "connector-pid": "happyranch-connector.service",
                    "verifier-cgroup": "happyranch-release-verifier.service"}.get(bad)
            if args[2] == unit:
                old, new = {"sidecar-job": ("Job=", "Job=5 start"),
                            "connector-pid": ("MainPID=0", "MainPID=42"),
                            "verifier-cgroup": ("ControlGroup=", "ControlGroup=/synthetic")}[bad]
                output = output.replace(old, new)
        return code, output
    fixture.installer._run = native
    fixture.installer.manager._run = native
    before = b6_snapshot(fixture.root)
    with pytest.raises(home.HeadscaleServiceError, match="^cleanup-uncertain$"):
        fixture.installer.cleanup_headscale(**_HOME_EXPECTED)
    assert b6_snapshot(fixture.root) == before


@pytest.mark.parametrize("case", ["unowned", "user-absent", "both-absent", "shared", "reuse", "group-failure",
                                 "unlink-failure", "member-failure", "disable-leftover", "issuer", "fence", "extra-runtime"])
def test_b6_accounts_partial_and_retained_evidence(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str,
) -> None:
    activation, _ = b6_setup(fixture, monkeypatch, capsys)
    if case == "unowned": fixture.receipt().unlink()
    if case in {"user-absent", "both-absent"}: fixture.present = False
    if case == "both-absent": fixture.group_present = False
    if case == "shared": fixture.shared = True
    if case == "reuse": fixture.reused_uid = True
    if case == "group-failure": fixture.exit_groupdel = 1
    if case == "issuer": issuer_journal(fixture.root / ISSUER, "uncertain")
    if case == "fence":
        fence = fixture.root / home.INSTALLER_STATE / "home-minter"
        fence.mkdir(mode=0o700)
        (fence / "operation-synthetic").write_bytes(b"synthetic uncertainty fence")
    if case == "extra-runtime":
        (fixture.root / "run/happyranch-tsnet-sidecar/unknown").write_bytes(b"synthetic retained state")
    if case == "disable-leftover":
        native = fixture.installer.manager._run
        def disable(args: list[str], **kwargs: object) -> tuple[int, str]:
            if args[:2] == ["systemctl", "disable"]: return 0, ""
            return native(args, **kwargs)
        fixture.installer.manager._run = disable
    original = os.unlink
    def remove(name: str, **kwargs: object) -> None:
        if case == "unlink-failure" and name == "db.sqlite-wal": raise OSError("synthetic unlink failure")
        if case == "member-failure" and name == "config.yaml": raise OSError("synthetic member failure")
        original(name, **kwargs)
    monkeypatch.setattr(os, "unlink", remove)
    if case in {"shared", "reuse", "group-failure", "unlink-failure", "member-failure", "disable-leftover", "issuer", "fence", "extra-runtime"}:
        with pytest.raises(home.HeadscaleServiceError) as error:
            fixture.installer.cleanup_headscale(**_HOME_EXPECTED)
        assert str(error.value) == ("cleanup-uncertain" if case in {"shared", "reuse"} else "partial-cleanup-uncertain")
        assert activation.exists() and fixture.receipt().exists()
        if case == "group-failure": assert not fixture.present and fixture.group_present
        if case == "issuer": assert (fixture.root / ISSUER / "invite-issuer.json").exists()
        if case == "fence": assert (fence / "operation-synthetic").exists()
        if case == "extra-runtime": assert (fixture.root / "run/happyranch-tsnet-sidecar/unknown").exists()
        if case in {"unlink-failure", "member-failure"}:
            monkeypatch.setattr(os, "unlink", original)
            assert fixture.installer.cleanup_headscale(**_HOME_EXPECTED)["state"] == "removed"
            assert not activation.exists() and not fixture.receipt().exists()
        return
    result = fixture.installer.cleanup_headscale(**_HOME_EXPECTED)
    assert result["account"] == ("preserved: not created by HappyRanch" if case == "unowned" else "removed")
    assert fixture.present == (case == "unowned")
    assert not activation.exists()
    if case == "unowned": assert not any(args[0] in {"userdel", "groupdel"} for args in fixture.calls)


def test_b6_ca_staging_is_retired(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    activation, _ = b6_setup(fixture, monkeypatch, capsys, staging=True)
    staging = fixture.root / home.HOME_CA
    assert fixture.installer.cleanup_headscale(**_HOME_EXPECTED)["state"] == "removed"
    assert not staging.exists()
    assert all(not (fixture.root / path).exists() for path in _B6_LIVE)
    assert not activation.exists() and not fixture.receipt().exists()
    assert not fixture.present and not fixture.group_present


def b6_minters(root: Path) -> Path:
    base = root / home.INSTALLER_STATE / "home-minter"
    base.mkdir(mode=0o700)
    for user in ("happyranch-node", "happyranch-invite"):
        directory = base / user
        directory.mkdir(mode=0o700)
        for name in ("operation-" + "a" * 64, "user-create", "fence", "fence.pending", "rpc.lock"):
            (directory / name).write_bytes(b"synthetic opaque evidence")
            (directory / name).chmod(0o600)
    return base


def test_b6_retires_minters_after_db_with_counts_audit(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    activation, _ = b6_setup(fixture, monkeypatch, capsys, staging=True)
    base = b6_minters(fixture.root)
    journal = fixture.root / home.INSTALLER_STATE / "home-audit.jsonl"
    original_unlink, original_open = os.unlink, os.open
    removed = []
    def remove(name: str, **kwargs: object) -> None:
        if name in {"operation-" + "a" * 64, "user-create", "fence", "fence.pending", "rpc.lock"}:
            assert all(not (fixture.root / path).exists() for path in _B6_LIVE[:5])
            assert json.loads(journal.read_text().splitlines()[-1])["stage"] == "intent"
            removed.append(name)
        original_unlink(name, **kwargs)
    def metadata_only(name: str, flags: int, *args: object, **kwargs: object) -> int:
        assert name not in {"operation-" + "a" * 64, "user-create", "fence", "fence.pending"}
        return original_open(name, flags, *args, **kwargs)
    monkeypatch.setattr(os, "unlink", remove)
    monkeypatch.setattr(os, "open", metadata_only)
    assert fixture.installer.cleanup_headscale(**_HOME_EXPECTED, revoke_succeeded=True)["state"] == "removed"
    assert not base.exists() and not activation.exists()
    assert removed == ["operation-" + "a" * 64, "user-create", "fence", "fence.pending", "rpc.lock"] * 2
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    for user in ("node", "invite"):
        pair = [r for r in rows if r["subject_id"] == "subject-home-minter-" + user]
        assert [r["stage"] for r in pair] == ["intent", "outcome"]
        assert pair[-1]["outcome"] == "succeeded"
        assert all(r["counts"] == dict(reservations=1, markers=1, fences=1, pending_fences=1) for r in pair)
    assert "a" * 64 not in journal.read_text() and "synthetic opaque evidence" not in journal.read_text()


@pytest.mark.parametrize("failure", ["db-unlink", "noise-unlink", "db-returned"])
def test_b6_no_db_proof_retains_whole_minter_set(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: str,
) -> None:
    activation, _ = b6_setup(fixture, monkeypatch, capsys)
    base = b6_minters(fixture.root)
    before = b6_snapshot(base)
    native = os.unlink
    def remove(name: str, **kwargs: object) -> None:
        if name == ("db.sqlite" if failure == "db-unlink" else "noise.key") and failure != "db-returned":
            raise OSError("synthetic failed DB retirement")
        native(name, **kwargs)
        if name == "derp.key" and failure == "db-returned":
            path = fixture.root / _B6_LIVE[0]
            path.write_bytes(b"returned DB")
            path.chmod(0o600)
    monkeypatch.setattr(os, "unlink", remove)
    with pytest.raises(home.HeadscaleServiceError, match="partial-cleanup-uncertain"):
        fixture.installer.cleanup_headscale(**_HOME_EXPECTED, revoke_succeeded=True)
    assert b6_snapshot(base) == before and activation.exists() and fixture.receipt().exists()


@pytest.mark.parametrize("bad", ["unknown-child", "unknown-name", "type", "symlink", "fifo", "mode", "owner",
                                 "hardlink", "directory-mode", "directory-link", "busy", "rpc-busy", "bound"])
def test_b6_bad_minter_refuses_whole_set(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], bad: str,
) -> None:
    activation, _ = b6_setup(fixture, monkeypatch, capsys)
    base = b6_minters(fixture.root)
    directory = base / "happyranch-invite"  # Later user: earlier user's records must also remain.
    marker = directory / "user-create"
    if bad == "unknown-child": (base / "foreign").mkdir(mode=0o700)
    if bad == "unknown-name": (directory / "operation-not-a-hash").touch(mode=0o600)
    if bad in {"type", "symlink", "fifo"}:
        marker.unlink()
        if bad == "type": marker.mkdir(mode=0o700)
        elif bad == "symlink": marker.symlink_to("foreign")
        else: os.mkfifo(marker, 0o600)
    if bad == "mode": marker.chmod(0o644)
    if bad == "owner":
        native = os.stat
        def observe(name: str, *args: object, **kwargs: object) -> os.stat_result:
            info = native(name, *args, **kwargs)
            if name == "user-create":
                fields = list(info)
                fields[4] += 1
                return os.stat_result(fields)
            return info
        monkeypatch.setattr(os, "stat", observe)
    if bad == "hardlink": (fixture.root / "extra-link").hardlink_to(marker)
    if bad == "directory-mode": directory.chmod(0o755)
    if bad == "directory-link":
        directory.rename(directory.with_name("foreign"))
        directory.symlink_to("foreign", target_is_directory=True)
    if bad == "bound":
        assert module._HOME_MINTER_LIMIT == 600_000
        monkeypatch.setattr(module, "_HOME_MINTER_LIMIT", 4)  # Real streamed scan, scaled boundary.
    lock = None
    try:
        if bad in {"busy", "rpc-busy"}:
            lock = os.open(directory if bad == "busy" else directory / "rpc.lock", os.O_RDONLY)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = b6_snapshot(base)
        with pytest.raises(home.HeadscaleServiceError, match="partial-cleanup-uncertain"):
            fixture.installer.cleanup_headscale(**_HOME_EXPECTED, revoke_succeeded=True)
        assert b6_snapshot(base) == before and activation.exists() and fixture.receipt().exists()
    finally:
        if lock is not None: os.close(lock)


@pytest.mark.parametrize("revoked", [True, False])
def test_b6_trust_and_sidecar_require_successful_revoke(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], revoked: bool,
) -> None:
    activation, _ = b6_setup(fixture, monkeypatch, capsys)
    for path in module._HOME_STATE_MEMBERS:
        target = fixture.root / path.lstrip("/")
        target.parent.mkdir(mode=0o700, exist_ok=True)
        target.write_bytes(b"synthetic opaque state")
        target.chmod(0o600)
    before = {p: (fixture.root / p.lstrip("/")).read_bytes() for p in module._HOME_STATE_MEMBERS}
    if revoked:
        assert fixture.installer.cleanup_headscale(**_HOME_EXPECTED, revoke_succeeded=True)["state"] == "removed"
        assert all(not (fixture.root / p.lstrip("/")).exists() for p in before)
        assert not activation.exists()
    else:
        with pytest.raises(home.HeadscaleServiceError, match="partial-cleanup-uncertain"):
            fixture.installer.cleanup_headscale(**_HOME_EXPECTED, revoke_succeeded=False)
        assert {p: (fixture.root / p.lstrip("/")).read_bytes() for p in before} == before
        assert activation.exists() and fixture.receipt().exists()


@pytest.mark.parametrize("outcome", ["minting", "uncertain", "issued", "recovered"])
@pytest.mark.parametrize("revoked", [True, False])
def test_b6_issuer_and_transaction_retirement(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], outcome: str, revoked: bool,
) -> None:
    from runtime.remote_access.home_activation import HomeActivationStore
    from runtime.remote_access.home_activation_txn import HomeActivationTransaction, HomeActivationTransactionStore
    activation, _ = b6_setup(fixture, monkeypatch, capsys)
    receipts = HomeActivationStore(root=fixture.root, root_owner=(fixture.uid, fixture.gid))
    transactions = HomeActivationTransactionStore(root=fixture.root, root_owner=(fixture.uid, fixture.gid))
    transactions.publish(HomeActivationTransaction.begin(receipts.current()))
    issuer_journal(fixture.root / ISSUER, outcome)
    txn = activation.with_name("home-activation-txn.json")
    if not revoked:
        with pytest.raises(home.HeadscaleServiceError, match="partial-cleanup-uncertain"):
            fixture.installer.cleanup_headscale(**_HOME_EXPECTED)
        assert (fixture.root / ISSUER / "invite-issuer.json").exists() and activation.exists()
        return
    assert fixture.installer.cleanup_headscale(**_HOME_EXPECTED, revoke_succeeded=True)["state"] == "removed"
    assert not txn.exists() and not (fixture.root / ISSUER).exists()
    assert not activation.with_name(ISSUER_RECEIPT).exists()
    rows = [json.loads(line) for line in activation.with_name("home-audit.jsonl").read_text().splitlines()]
    for subject in ("subject-activation-evidence", "subject-issuer-evidence"):
        assert [(r["stage"], r["outcome"]) for r in rows if r["subject_id"] == subject] == [
            ("intent", None), ("outcome", "succeeded")]


@pytest.mark.parametrize("db_safe", [True, False])
def test_b6_minter_interruption_retry_reestablishes_db_proof(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], db_safe: bool,
) -> None:
    activation, _ = b6_setup(fixture, monkeypatch, capsys)
    base = b6_minters(fixture.root)
    native = os.unlink
    def remove(name: str, **kwargs: object) -> None:
        if name == "fence": raise OSError("synthetic interrupted removal")
        native(name, **kwargs)
    monkeypatch.setattr(os, "unlink", remove)
    with pytest.raises(home.HeadscaleServiceError, match="partial-cleanup-uncertain"):
        fixture.installer.cleanup_headscale(**_HOME_EXPECTED, revoke_succeeded=True)
    assert not (base / "happyranch-node/user-create").exists()
    assert (base / "happyranch-invite/user-create").exists() and activation.exists()
    monkeypatch.setattr(os, "unlink", native)
    if db_safe:
        assert fixture.installer.cleanup_headscale(**_HOME_EXPECTED, revoke_succeeded=True)["state"] == "removed"
        assert not base.exists() and not activation.exists()
    else:
        db = fixture.root / _B6_LIVE[0]
        db.write_bytes(b"unsafe returned DB")
        db.chmod(0o644)
        before = b6_snapshot(base)
        with pytest.raises(home.HeadscaleServiceError, match="cleanup-uncertain"):
            fixture.installer.cleanup_headscale(**_HOME_EXPECTED, revoke_succeeded=True)
        assert b6_snapshot(base) == before and activation.exists()


@pytest.mark.parametrize("boundary", ["intent", "outcome"])
def test_b6_retirement_audit_failure_retains_evidence(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], boundary: str,
) -> None:
    from runtime.remote_access import home_audit
    activation, _ = b6_setup(fixture, monkeypatch, capsys)
    base = b6_minters(fixture.root)
    before = b6_snapshot(base)
    def fail(*args: object, **kwargs: object) -> None:
        raise home_audit.AuditRefused("unavailable")
    monkeypatch.setattr(home_audit.HomeAudit, boundary, fail)
    with pytest.raises(home.HeadscaleServiceError, match="partial-cleanup-uncertain"):
        fixture.installer.cleanup_headscale(**_HOME_EXPECTED, revoke_succeeded=True)
    assert activation.exists() and fixture.receipt().exists()
    if boundary == "intent":
        assert b6_snapshot(base) == before
    else:
        assert not (base / "happyranch-node").exists()
        assert (base / "happyranch-invite/user-create").exists()
        rows = [json.loads(line) for line in activation.with_name("home-audit.jsonl").read_text().splitlines()]
        assert [(r["stage"], r["outcome"]) for r in rows] == [("intent", None), ("uncertain", "uncertain")]


@pytest.mark.parametrize("relative", _B6_LIVE)
def test_b6_no_expectations_keeps_ruling_d(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], relative: str,
) -> None:
    activation, link = b6_setup(fixture, monkeypatch, capsys)
    activation.unlink()
    link.unlink()
    (fixture.root / "etc/systemd/system" / home.UNIT).write_text(EXPECTED_UNIT)
    # One fixed live entry at a time reaches D's existing custody refusal.
    for other in _B6_LIVE:
        if other == relative: continue
        path = fixture.root / other
        path.rmdir() if path.is_dir() else path.unlink()
    before = b6_snapshot(fixture.root)
    if relative.endswith("/observe.sock"):
        # D never owned this later sidecar socket; only Headscale custody is
        # cleaned. The new optional API must not widen its no-argument scope.
        result = fixture.installer.cleanup_headscale()
        assert result["home_ca"] == "preserved"
        assert b6_snapshot(fixture.root)[relative] == before[relative]
        return
    with pytest.raises(home.HeadscaleServiceError, match="^cleanup-uncertain$"):
        fixture.installer.cleanup_headscale()
    assert b6_snapshot(fixture.root) == before
    assert not any(args[0] in {"userdel", "groupdel"} or args[:2] == ["systemctl", "disable"] for args in fixture.calls)


@pytest.mark.parametrize('operation', ['b' * 32, 'e' * 32])
def test_b6_helper_operation_cas_under_receipt_lock(
    fixture: Fixture, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], operation: str,
) -> None:
    b6_setup(fixture, monkeypatch, capsys)
    before = b6_snapshot(fixture.root)
    if operation == 'b' * 32:
        assert fixture.installer.cleanup_headscale(**_HOME_EXPECTED, expected_operation_id=operation)['state'] == 'removed'
    else:
        with pytest.raises(home.HeadscaleServiceError, match='cleanup-uncertain'):
            fixture.installer.cleanup_headscale(**_HOME_EXPECTED, expected_operation_id=operation)
        assert b6_snapshot(fixture.root) == before
        assert not any(args[0] in {'userdel', 'groupdel'} or args[:2] == ['systemctl', 'disable'] for args in fixture.calls)
