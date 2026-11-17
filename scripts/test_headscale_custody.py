"""Standalone inert preparation/cleanup; temp root and fake commands, never host effects.

Run: uv run --frozen --python /usr/bin/python3 pytest scripts/test_headscale_custody.py -v --tb=short
"""
from __future__ import annotations

import errno
import json
import os
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import headscale_service as home
from runtime.remote_access import installer as module
from runtime.remote_access import systemd_unit

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
        self.installer = module.ManagedInstaller(root=root, root_owner=(self.uid, self.gid), layout="deb",
                                                run=self.run, lookup_user=self.lookup_user,
                                                lookup_group=self.lookup_group)
        (root / "etc").mkdir(mode=0o755)
        # Synthetic ranges permit the actual test owner; no host NSS is queried.
        (root / "etc/login.defs").write_text(
            f"UID_MIN {self.uid + 1}\nGID_MIN {self.gid + 1}\nSYS_UID_MIN {self.uid}\nSYS_GID_MIN {self.gid}\n")
        (root / "etc/login.defs").chmod(0o644)

    def lookup_user(self, name: str) -> SimpleNamespace:
        if name == home.ACCOUNT and self.present:
            return self.user
        raise KeyError(name)

    def lookup_group(self, name: str) -> SimpleNamespace:
        if name == home.ACCOUNT and self.group_present:
            return self.group
        raise KeyError(name)

    def run(self, args: list[str], **kwargs: object) -> tuple[int, str]:
        self.calls.append(args)
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


@pytest.fixture
def fixture(tmp_path: Path) -> Fixture:
    return Fixture(tmp_path)


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
    assert set(args[0] for args in fixture.calls) == {"useradd", "getent", "passwd"}
    assert sorted(p.name for p in policy.parent.iterdir()) == ["policy.hujson"]
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
    assert all(args[0] in {"getent", "passwd"} for args in fixture.calls)
    code, output = fixture.cleanup(monkeypatch, capsys)
    assert code == 0 and json.loads(output) == {
        "state": "preserved", "account": "preserved: not created by HappyRanch"}
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
    assert all(args[0] in {"getent", "passwd"} for args in fixture.calls)
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
      if kind != "alias-cross-dropin" or path != "etc/systemd/system"]])
def test_foreign_custody_preserved(fixture, monkeypatch, capsys, entry) -> None:
    if entry.startswith("prelink:"):
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
        return {str(p.relative_to(fixture.root)): (p.lstat().st_mode,
                    os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
                for p in fixture.root.rglob("*")}
    contents = snapshot()
    before = sorted(str(p.relative_to(fixture.root)) for p in fixture.root.rglob("*"))
    fixture.calls.clear()
    code, output = fixture.prepare(monkeypatch, capsys)
    assert code == 1 and ("foreign-" in output or "custody-uncertain" in output)
    if entry.startswith(("load:", "prelink:")):
        assert "foreign-unit" in output
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
        (fixture.root / home.CUSTODY[0] / "policy.hujson").unlink()
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
        assert json.loads(output) == {"state": "removed", "account": "removed"}
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
