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

    def seed_prepared_history(self) -> None:
        """Write synthetic historical inputs, never invoke a provisioning owner.

        These frozen bytes are legacy input only. Actual cleanup, custody,
        revocation and retirement decisions always remain production behavior.
        """
        self.present = self.group_present = True
        fixtures = Path(__file__).parent / "fixtures/home-retirement"

        def directory(relative: str, mode: int) -> Path:
            path = self.root / relative
            path.mkdir(parents=True, exist_ok=True)
            for parent in path.parents:
                if parent == self.root:
                    break
                parent.chmod(0o755)
            path.chmod(mode)
            return path

        def put(relative: str, data: bytes, mode: int = 0o600) -> None:
            path = self.root / relative
            path.write_bytes(data)
            path.chmod(mode)

        directory("etc/happyranch/headscale", 0o700)
        directory("etc/happyranch/empty-ca-directory", 0o755)
        directory("etc/systemd/system", 0o755)
        directory(STATE + "/home-ca", 0o700)
        issuer = directory(STATE + "/home-issuer", 0o700)
        (self.root / STATE).chmod(0o700)
        files = {"etc/happyranch/headscale/" + name:
            (fixtures / ("legacy-" + name)).read_bytes()
            for name in ("config.yaml", "derp.yaml")}
        files.update({"etc/happyranch/headscale/server.crt": self.leaf_pem,
            "etc/happyranch/headscale/server.key": self.leaf_key,
            "etc/happyranch/control-ca.pem": self.ca_pem})
        for name, data in files.items():
            put(name, data, 0o640 if name.endswith("control-ca.pem") else 0o600)
        put("etc/happyranch/headscale/policy.hujson", (fixtures / "legacy-policy.hujson").read_bytes())
        put("etc/systemd/system/" + UNIT, (fixtures / "headscale-inert.service").read_bytes(), 0o644)
        for name, data in (("ca.pem", self.ca_pem), ("ca.key", b"synthetic CA private key"),
                           ("leaf.pem", self.leaf_pem), ("leaf.key", self.leaf_key)):
            put(STATE + "/home-ca/" + name, data)
        records = {"headscale-account.json": dict(version=1, account=ACCOUNT,
                uid=self.uid, gid=self.gid, group_created_with_user=True),
            "headscale-publication.json": dict(version=1, uid=self.uid, gid=self.gid,
                dial_ip="192.168.1.40", files={"/" + name: hashlib.sha256(data).hexdigest()
                    for name, data in files.items()}, directories=["/etc/happyranch/empty-ca-directory"]),
            "home-issuer-publication.json": dict(version=1, directory="/" + STATE + "/home-issuer",
                uid=self.uid, gid=self.gid, device=issuer.stat().st_dev, inode=issuer.stat().st_ino)}
        for name, record in records.items():
            put(STATE + "/" + name, json.dumps(record, sort_keys=True).encode())

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


def legacy_activation_record(phase: str) -> dict:
    """Historical activation input for retained cleanup, not an activation result."""
    members = ("/etc/systemd/system/happyranch-headscale.service",
        "/etc/happyranch/headscale/config.yaml", "/etc/happyranch/headscale/derp.yaml",
        "/etc/happyranch/headscale/policy.hujson", "/etc/happyranch/control-ca.pem",
        "/etc/happyranch/invite-issuer.json")
    return dict(format="home-activation@1", release_uid=1000, home_id="home",
        control_plane_id="control", connector_id="connector", ca_der_sha256="a" * 64,
        dial_ip="192.168.1.40", activation_generation=7, operation_id="b" * 32,
        member_digests={path: "c" * 64 for path in members}, phase=phase,
        local_node_stable_id="synthetic-local-node" if phase in ("joined", "ready") else None,
        home_endpoint="100.64.0.10:443" if phase in ("joined", "ready") else None,
        ownership=dict(members=["/var/lib/happyranch-headscale/" + name for name in
            ("db.sqlite", "db.sqlite-wal", "db.sqlite-shm", "noise.key", "derp.key")]
            + ["/run/happyranch-headscale/headscale.sock", "/run/happyranch-tsnet-sidecar/observe.sock",
               "/var/lib/happyranch-headscale/acme-unused"],
            links={"/etc/systemd/system/happyranch-managed.target.wants/" + UNIT:
                   "/etc/systemd/system/" + UNIT}))


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


def put_legacy(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Synthetic historical input only; no production publication transition."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


class LegacyAdmissionNative:
    """Native observations for retained inventory; no activation simulation."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = []
        self.override = {}

    def run(self, args: list[str], **kwargs: object) -> tuple[int, str]:
        self.calls.append(args)
        assert args[:2] == ["systemctl", "show"], "inventory attempted a native effect"
        name = args[2]
        values = dict(Id=name, Names=name, FragmentPath=str(self.root / "etc/systemd/system" / name),
            DropInPaths="", NeedDaemonReload="no", LoadState="loaded", ActiveState="active",
            SubState="running", User="happyranch-headscale" if name == UNIT else "happyranch",
            Group="happyranch-headscale" if name == UNIT else "happyranch", DynamicUser="no", MainPID="321")
        values.update(self.override)
        return 0, "\n".join(f"{key}={values[key]}" for key in
            (arg.split("=", 1)[1] for arg in args if arg.startswith("--property=")))


def legacy_admission_fixture(tmp: Path, monkeypatch: object, *, ready: bool) -> object:
    """Seed legacy data for real readback without activation/provisioning owners."""
    from runtime.remote_access.legacy_home_receipt import HomeActivationReceipt, HomeActivationTransaction
    from runtime.remote_access.legacy_home_readback import LegacyHomeReadback, LegacyHomeTransactions
    from runtime.remote_access.service_manager import SystemdServiceManager
    from scripts.test_installer_token_custody import Fixture

    fixture = Fixture(tmp, monkeypatch)
    native = LegacyAdmissionNative(fixture.root)
    head = LegacyNativeFixture(fixture.root)
    head.seed_prepared_history()
    # This historical reader fixture predates the later installer publication
    # records; leave only the activation pair used by these existing cases.
    for name in ("headscale-account.json", "headscale-publication.json", "home-issuer-publication.json"):
        (fixture.root / STATE / name).unlink()
    for name in ("ca.pem", "ca.key", "leaf.pem", "leaf.key"):
        (fixture.root / STATE / "home-ca" / name).unlink()
    for name in ("home-ca", "home-issuer"):
        (fixture.root / STATE / name).rmdir()
    io = fixture.installer
    io.layout = "source"
    lookup = io._lookup_user
    io._lookup_user = lambda name: head.lookup_user(name) if name == ACCOUNT else lookup(name)
    io._lookup_group = head.lookup_group
    io._service_owner = io.root_owner
    io._run = head.run
    io.manager = SystemdServiceManager(system=True, run=native.run)
    fixtures = Path(__file__).parent / "fixtures/home-retirement"
    path = lambda name: fixture.root / name.lstrip("/")
    for directory in ("etc/credstore", "var/lib/happyranch-tsnet-sidecar", "var/lib/happyranch-connector"):
        path(directory).mkdir(mode=0o700, parents=True, exist_ok=True)
    connector = json.loads((fixture.inputs / "connector.json").read_bytes())
    connector.update(home_id="home", connector_id="connector", excluded_daemon_ports=[8765])
    connector.pop("daemon_port", None)
    connector["managed"].update(profile="linux-managed-identity-v1", control_plane_id="control", bind_port=8443)
    sidecar = dict(Profile="linux-managed-identity-v1", StateDir="/var/lib/happyranch-tsnet-sidecar",
        RoleIdentity="home-sidecar-home", DERPPolicy="private-only", ListenAddr=":443", ExcludedDaemonPorts=[8765])
    # Noncanonical whitespace is independently stored historical input.
    prepared = {"connector.json": json.dumps(connector, sort_keys=True).encode(),
        "sidecar.json": (json.dumps(sidecar, indent=2) + "\n \t").encode(),
        "policy.json": (fixture.inputs / "policy.json").read_bytes()}
    for name, data in prepared.items():
        put_legacy(path("etc/happyranch/" + name), data, 0o640)
    put_legacy(path("etc/happyranch/daemon.token"), fixture.token.read_bytes())
    release = dict(user=fixture.user, uid=os.getuid(), data_home=str(fixture.home),
        unit="happyranch-daemon.service", fragment_path="/usr/lib/systemd/user/happyranch-daemon.service")
    put_legacy(path("etc/happyranch/release-daemon.json"), json.dumps(release).encode(), 0o644)
    names = (*prepared, "release-daemon.json", "daemon.token")
    provision = dict(version=1, phase="provisioned", layout="source",
        digests={name: hashlib.sha256(path("etc/happyranch/" + name).read_bytes()).hexdigest() for name in names})
    put_legacy(path("etc/happyranch/provision.json"), json.dumps(provision).encode())
    invite = dict(format="home-invite-config@2", release_uid=os.getuid(), home_id="home",
        control_plane_id="control", connector_id="connector", ca_der_sha256=hashlib.sha256(b"synthetic public CA").hexdigest(),
        dial_ip="192.168.1.40", control_url="https://control.happyranch.invalid:8443")
    put_legacy(path("etc/happyranch/invite-issuer.json"), json.dumps(invite).encode())
    for name in module.UNITS:
        source = "managed-home-sidecar.service" if ready and name == module.UNITS[1] else "managed-" + name
        put_legacy(path("etc/systemd/system/" + name), (fixtures / source).read_bytes().replace(b"/opt/happyranch-desktop/", b"/opt/happyranch/").replace(
            b"/opt/happyranch/python/bin/python3 -I", b"/opt/happyranch/venv/bin/python"), 0o644)
    put_legacy(path("etc/systemd/system/" + UNIT), (fixtures / "headscale-home.service").read_bytes(), 0o644)
    record = legacy_activation_record("ready" if ready else "prepared")
    record.update(release_uid=os.getuid(), ca_der_sha256=invite["ca_der_sha256"],
        local_node_stable_id="stable-node" if ready else None)
    record["member_digests"] = {name: hashlib.sha256(path(name).read_bytes()).hexdigest() for name in record["member_digests"]}
    put_legacy(path(STATE + "/home-activation.json"), json.dumps(record).encode())
    receipt = HomeActivationReceipt.parse(json.dumps(record).encode())
    if not ready:
        for name in ("server.crt", "server.key"):
            path("etc/happyranch/headscale/" + name).unlink()
    transaction = None
    if ready:
        addition = (',"ControlURL":"https://control.happyranch.invalid:8443",'
            '"control_tls":{"dial_ip":"192.168.1.40","ca_sha256":"' + invite["ca_der_sha256"] + '"},'
            '"home_binding":{"home_id":"home","control_plane_id":"control",'
            '"connector_id":"connector","activation_generation":7}').encode()
        data = prepared["sidecar.json"]
        end = data.rindex(b"}")
        put_legacy(path("etc/happyranch/sidecar.json"), data[:end] + addition + data[end:], 0o640)
        effects = [dict(name=name, state="done", member_digests=(
            {"/etc/happyranch/" + item: hashlib.sha256(path("etc/happyranch/" + item).read_bytes()).hexdigest()
                for item in ("connector.json", "sidecar.json")} if name == "home_config" else {}))
            for name in ("preflight", "headscale_start_verify", "db_users", "local_node_mint", "credential_staging", "home_config", "managed_services", "joined")]
        transaction = dict(format="home-activation-txn@1", release_uid=os.getuid(), home_id="home",
            control_plane_id="control", connector_id="connector", activation_generation=7,
            operation_id="b" * 32, ca_der_sha256=invite["ca_der_sha256"], dial_ip="192.168.1.40",
            phase="joined", effects=effects)
        put_legacy(path(STATE + "/home-activation-txn.json"), json.dumps(transaction).encode())
        transaction = HomeActivationTransaction.parse(json.dumps(transaction).encode())
        link = path("etc/systemd/system/happyranch-managed.target.wants/" + UNIT)
        link.parent.mkdir(mode=0o755)
        link.symlink_to("/etc/systemd/system/" + UNIT)
    receipts = LegacyHomeReadback(root=fixture.root, root_owner=io.root_owner)
    receipts._io = io
    transactions = LegacyHomeTransactions(root=fixture.root, root_owner=io.root_owner)
    transactions._io = io
    native.calls.clear()
    return SimpleNamespace(root=fixture.root, receipts=receipts, transactions=transactions,
        source=fixture.token, path=path, native=native, prepared=receipt, ready=receipt,
        txn=transaction, mints=[], records=lambda: (path(STATE + "/home-activation.json").read_bytes(),
            path(STATE + "/home-activation-txn.json").read_bytes() if path(STATE + "/home-activation-txn.json").exists() else None))
