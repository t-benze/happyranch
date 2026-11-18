"""Standalone inert preparation/cleanup; temp root and fake commands, never host effects.

Run: uv run --frozen --python /usr/bin/python3 pytest scripts/test_headscale_custody.py -v --tb=short
"""
from __future__ import annotations

import errno
import hashlib
import json
import os
import socket
import ssl
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import headscale_service as home
from runtime.remote_access import installer as module
from runtime.remote_access import systemd_unit

PUBLICATION = "headscale-publication.json"
CONFIG_FILES = ("config.yaml", "derp.yaml")
LEAF_FILES = ("server.crt", "server.key")
MATERIALIZER = "/opt/happyranch-desktop/libexec/remote-access/happyranch-home-ca"
EMPTY_CA = "etc/happyranch/empty-ca-directory"
CONTROL_CA = "etc/happyranch/control-ca.pem"
PUBLISHED = (*CONFIG_FILES, *LEAF_FILES, "control-ca.pem", PUBLICATION)

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


class Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.present = False
        self.group_present = False
        self.calls: list[list[str]] = []
        self.dial_ip = "192.168.1.40"
        self.preflights: list[str] = []
        self.kernel_case = "single"
        self.uid, self.gid = os.getuid(), os.getgid()
        self.user = SimpleNamespace(pw_name=home.ACCOUNT, pw_uid=self.uid, pw_gid=self.gid,
                                    pw_dir="/nonexistent", pw_shell="/usr/sbin/nologin")
        self.group = SimpleNamespace(gr_name=home.ACCOUNT, gr_gid=self.gid, gr_mem=[])
        self.locked = True
        self.shared = False
        self.exit_useradd = 0
        self.exit_userdel = self.exit_groupdel = 0
        self.reused_uid = False
        self.busy = self.unit_busy = False
        self.fail_service = False
        self.ca_pem = ssl.DER_cert_to_PEM_cert(b"synthetic public CA").encode()
        self.leaf_pem = ssl.DER_cert_to_PEM_cert(b"synthetic server leaf").encode()
        self.leaf_key = b"synthetic leaf key"
        self.materializer_due = False
        self.materializer_result = None
        self.installer = module.ManagedInstaller(root=root, root_owner=(self.uid, self.gid), layout="deb",
                                                run=self.run, lookup_user=self.lookup_user,
                                                lookup_group=self.lookup_group)
        (root / "etc").mkdir(mode=0o755)
        # Synthetic ranges permit the actual test owner; no host NSS is queried.
        (root / "etc/login.defs").write_text(
            f"UID_MIN {self.uid + 1}\nGID_MIN {self.gid + 1}\nSYS_UID_MIN {self.uid}\nSYS_GID_MIN {self.gid}\n")
        (root / "etc/login.defs").chmod(0o644)
        binary = root / MATERIALIZER.lstrip("/")
        binary.parent.mkdir(parents=True)
        for parent in (binary.parent, *binary.parent.parents):
            if parent != root and parent.is_relative_to(root):
                parent.chmod(0o755)
        binary.write_bytes(b"synthetic inert materializer")
        binary.chmod(0o755)

    def lookup_user(self, name: str) -> SimpleNamespace:
        if name == home.ACCOUNT and self.present:
            return self.user
        raise KeyError(name)

    def lookup_group(self, name: str) -> SimpleNamespace:
        if name == "happyranch":
            return SimpleNamespace(gr_name=name, gr_gid=self.gid, gr_mem=[])
        if name == home.ACCOUNT and self.group_present:
            return self.group
        raise KeyError(name)

    def run(self, args: list[str], **kwargs: object) -> tuple[int, str]:
        self.calls.append(args)
        if args[0] == MATERIALIZER:
            assert len(args) == 2 and args[1] in {"ensure", "status", "renew-leaf"}
            if self.materializer_result is not None:
                return self.materializer_result
            staging = self.root / home.INSTALLER_STATE / "home-ca"
            if args[1] == "renew-leaf":
                self.leaf_pem = ssl.DER_cert_to_PEM_cert(b"synthetic renewed leaf").encode()
                self.leaf_key = b"synthetic renewed key"
                self.materializer_due = False
            if args[1] != "status":
                staging.mkdir(mode=0o700, exist_ok=True)
                for name, data in (("ca.pem", self.ca_pem), ("ca.key", b"synthetic CA private key"),
                                   ("leaf.pem", self.leaf_pem), ("leaf.key", self.leaf_key)):
                    path = staging / name
                    if not path.exists() or path.read_bytes() != data:
                        path.write_bytes(data)
                        path.chmod(0o600)
            return 0, json.dumps(dict(ca_fingerprint=hashlib.sha256(b"synthetic public CA").hexdigest(),
                                     ca_not_after="2030-01-01T00:00:00Z",
                                     leaf_not_after="2027-01-01T00:00:00Z",
                                     renewal_due=self.materializer_due)) + "\n"
        if args[0] == "useradd":
            if not self.exit_useradd:
                self.present = self.group_present = True
            return self.exit_useradd, "synthetic private diagnostic"
        if args[0] in {"userdel", "groupdel"}:
            code = self.exit_userdel if args[0] == "userdel" else self.exit_groupdel
            if not code:
                if args[0] == "userdel": self.present = False
                else: self.group_present = False
            return code, "synthetic private diagnostic"
        if args == ["ps", "-eo", "ruid=,euid=,suid=,fuid="]:
            return 0, (f"{self.uid} {self.uid} {self.uid} {self.uid}\n" if self.busy else "")
        if args[0] == "systemctl":
            if self.fail_service: return 1, "synthetic private diagnostic"
            if args[1] == "show":
                unit = self.root / "etc/systemd/system" / home.UNIT
                props = dict(LoadState="loaded" if unit.exists() else "not-found",
                             FragmentPath=str(unit) if unit.exists() else "", DropInPaths="",
                             ActiveState="active" if self.unit_busy else "inactive",
                             SubState="running" if self.unit_busy else "dead", Job="",
                             MainPID="12" if self.unit_busy else "0", ControlPID="0", ControlGroup="")
                return 0, "\n".join(f"{key}={props[key]}" for key in
                                    (arg.split("=", 1)[1] for arg in args if arg.startswith("--property=")))
            return 0, ""
        if args[:2] == ["getent", "passwd"] and len(args) == 3:
            if self.reused_uid:
                return 0, f"other:x:{self.uid}:{self.gid + 1}::/nonexistent:/usr/sbin/nologin\n"
            return ((0, f"{home.ACCOUNT}:x:{self.uid}:{self.gid}::/nonexistent:/usr/sbin/nologin\n")
                    if self.present else (2, ""))
        if args[:2] == ["getent", "group"] and len(args) == 3:
            return ((0, f"{home.ACCOUNT}:x:{self.group.gr_gid}:{','.join(self.group.gr_mem)}\n")
                    if self.group_present else (2, ""))
        if args == ["getent", "passwd"]:
            value = (f"{home.ACCOUNT}:x:{self.uid}:{self.gid}::/nonexistent:/usr/sbin/nologin\n"
                     if self.present else "")
            if self.reused_uid is True:
                value += f"other:x:{self.uid}:{self.gid + 1}::/nonexistent:/usr/sbin/nologin\n"
            if self.shared:
                value += f"other:x:{self.uid+1}:{self.gid}::/nonexistent:/usr/sbin/nologin\n"
            return 0, value
        if args == ["getent", "group"]:
            return 0, f"{home.ACCOUNT}:x:{self.group.gr_gid}:{','.join(self.group.gr_mem)}\n" if self.group_present else ""
        if args == ["passwd", "--status", home.ACCOUNT]:
            return 0, f"{home.ACCOUNT} {'L' if self.locked else 'P'} 2026-01-01 0 99999 7 -1\n"
        raise AssertionError(f"unexpected external command: {args[0]}")

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

    def receipt(self) -> Path:
        return self.root / "var/lib/happyranch-remote-access/headscale-account.json"


def nla(kind: int, data: bytes) -> bytes:
    length = len(data) + 4
    return struct.pack("=HH", length, kind) + data + bytes((-length) % 4)


class KernelDump:
    """Synthetic Linux UAPI frames, never source-selection/publication logic."""

    def __init__(self, fixture: Fixture, args: tuple) -> None:
        assert args == (socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE)
        self.fixture = fixture

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def bind(self, address): assert address == (0, 0)
    def settimeout(self, timeout): assert 0 < timeout <= 6

    def sendto(self, packet, address):
        assert address == (0, 0)
        _, request, _, self.sequence, _ = struct.unpack_from("=IHHII", packet)
        self.response = {18: 16, 22: 20, 26: 24}[request]
        return len(packet)

    def recvmsg(self, limit):
        value, case = self.fixture, self.fixture.kernel_case
        address = socket.inet_pton(socket.AF_INET, value.dial_ip)
        link = struct.pack("=BBHiII", 0, 0, 1, 2, 0 if case == "down" else 9 if case == "loopback" else 1, 0)
        addr = struct.pack("=BBBBI", socket.AF_INET, 24, 0, 0, 2) + nla(2, address)
        route = struct.pack("=BBBBBBBBI", socket.AF_INET, 0, 0, 0, 254, 4, 0, 1, 0)
        source = b"" if case == "unresolved" else nla(7, address)
        route += source
        if case == "ecmp":
            route += nla(9, struct.pack("=HBBi", 8, 0, 0, 2) * 2)
        else:
            route += nla(4, struct.pack("=I", 99 if case == "unknown-interface" else 2))
        bodies = {16: [link], 20: [addr], 24: [] if case == "no-route" else [route]}
        if case == "duplicate-interface": bodies[16].append(link)
        if case == "unresolved-second":
            bodies[24].append(struct.pack("=BBBBBBBBI", socket.AF_INET, 0, 0, 0, 254, 4, 0, 1, 0)
                              + nla(4, struct.pack("=I", 2)))
        if case == "ipv6":
            ipv6 = socket.inet_pton(socket.AF_INET6, "2001:db8::2")
            bodies[20].append(struct.pack("=BBBBI", socket.AF_INET6, 64, 0, 0, 2) + nla(1, ipv6))
            bodies[24].append(struct.pack("=BBBBBBBBI", socket.AF_INET6, 0, 0, 0, 254, 4, 0, 1, 0)
                              + nla(4, struct.pack("=I", 2)) + nla(7, ipv6))
        def message(kind, body, flags=2):
            return struct.pack("=IHHII", 16 + len(body), kind, flags, self.sequence, 0) + body
        frames = b"".join(message(self.response, body) for body in bodies[self.response])
        frames += message(3, struct.pack("=i", 0), 0x10 if case == "interrupted" else 2)
        if case == "error": frames = message(2, struct.pack("=i", -1))
        return frames, [], socket.MSG_TRUNC if case == "truncated" else 0, (0, 0)


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch) -> Fixture:
    value = Fixture(tmp_path)
    monkeypatch.setattr(home.socket, "socket", lambda *args: KernelDump(value, args))
    monkeypatch.setattr(home, "preflight_ports", value.preflights.append)
    return value


def test_created_receipt_custody_and_inert_unit(fixture, monkeypatch, capsys) -> None:
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 0, output
    assert json.loads(output) == {"state": "inert", "account": "created", "preservation": "receipt-recorded"}
    assert json.loads(fixture.receipt().read_bytes()) == {
        "version": 1, "account": "happyranch-headscale", "uid": fixture.uid, "gid": fixture.gid,
        "group_created_with_user": True}
    assert fixture.receipt().stat().st_mode & 0o777 == 0o600
    assert fixture.receipt().parent.stat().st_mode & 0o777 == 0o700
    for name in ("etc/happyranch/headscale", "var/lib/happyranch-headscale", "run/happyranch-headscale"):
        info = (fixture.root / name).stat()
        assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (fixture.uid, fixture.gid, 0o700)
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
    assert not list((fixture.root / "var/lib/happyranch-headscale").iterdir())
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
    if entry == "state": (fixture.root / "var/lib/happyranch-headscale/db.sqlite").write_bytes(b"foreign")
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
