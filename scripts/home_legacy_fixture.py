"""Synthetic legacy native observations shared by retained custody fixtures.

This test-side adapter supplies filesystem/NSS/systemd/materializer inputs only.
It imports no activation, issuer, admin or transaction constructor and never
prepares an installation. Historical constants are independent fixture inputs.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import ssl
import struct
from pathlib import Path
from types import SimpleNamespace

from runtime.remote_access import installer as module

ACCOUNT = "happyranch-headscale"
UNIT = "happyranch-headscale.service"
STATE = "var/lib/happyranch-remote-access"
CUSTODY = ("etc/happyranch/headscale", "var/lib/happyranch-headscale", "run/happyranch-headscale")
MATERIALIZER = "/opt/happyranch-desktop/libexec/remote-access/happyranch-home-ca"


class LegacyNativeFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.present = False
        self.group_present = False
        self.calls: list[list[str]] = []
        self.dial_ip = "192.168.1.40"
        self.preflights: list[str] = []
        self.kernel_case = "single"
        self.uid, self.gid = os.getuid(), os.getgid()
        self.user = SimpleNamespace(pw_name=ACCOUNT, pw_uid=self.uid, pw_gid=self.gid,
                                    pw_dir="/nonexistent", pw_shell="/usr/sbin/nologin")
        self.group = SimpleNamespace(gr_name=ACCOUNT, gr_gid=self.gid, gr_mem=[])
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
        if name == ACCOUNT and self.present:
            return self.user
        raise KeyError(name)

    def lookup_group(self, name: str) -> SimpleNamespace:
        if name == "happyranch":
            return SimpleNamespace(gr_name=name, gr_gid=self.gid, gr_mem=[])
        if name == ACCOUNT and self.group_present:
            return self.group
        raise KeyError(name)

    def run(self, args: list[str], **kwargs: object) -> tuple[int, str]:
        self.calls.append(args)
        if args[0] == MATERIALIZER:
            assert len(args) == 2 and args[1] in {"ensure", "status", "renew-leaf"}
            if self.materializer_result is not None:
                return self.materializer_result
            staging = self.root / STATE / "home-ca"
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
                unit = self.root / "etc/systemd/system" / UNIT
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
            return ((0, f"{ACCOUNT}:x:{self.uid}:{self.gid}::/nonexistent:/usr/sbin/nologin\n")
                    if self.present else (2, ""))
        if args[:2] == ["getent", "group"] and len(args) == 3:
            return ((0, f"{ACCOUNT}:x:{self.group.gr_gid}:{','.join(self.group.gr_mem)}\n")
                    if self.group_present else (2, ""))
        if args == ["getent", "passwd"]:
            value = (f"{ACCOUNT}:x:{self.uid}:{self.gid}::/nonexistent:/usr/sbin/nologin\n"
                     if self.present else "")
            if self.reused_uid is True:
                value += f"other:x:{self.uid}:{self.gid + 1}::/nonexistent:/usr/sbin/nologin\n"
            if self.shared:
                value += f"other:x:{self.uid+1}:{self.gid}::/nonexistent:/usr/sbin/nologin\n"
            return 0, value
        if args == ["getent", "group"]:
            return 0, f"{ACCOUNT}:x:{self.group.gr_gid}:{','.join(self.group.gr_mem)}\n" if self.group_present else ""
        if args == ["passwd", "--status", ACCOUNT]:
            return 0, f"{ACCOUNT} {'L' if self.locked else 'P'} 2026-01-01 0 99999 7 -1\n"
        raise AssertionError(f"unexpected external command: {args[0]}")

    def service_directories(self) -> None:
        # Explicit live/cleanup prestate, modeling systemd's directory creation.
        for relative in CUSTODY[1:]:
            path = self.root / relative
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.parent.chmod(0o755)

    def receipt(self) -> Path:
        return self.root / "var/lib/happyranch-remote-access/headscale-account.json"


def nla(kind: int, data: bytes) -> bytes:
    length = len(data) + 4
    return struct.pack("=HH", length, kind) + data + bytes((-length) % 4)


class KernelDump:
    """Synthetic Linux UAPI frames, never source-selection/publication logic."""

    def __init__(self, fixture: LegacyNativeFixture, args: tuple) -> None:
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
