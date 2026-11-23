"""Retirement detector through real installer I/O, isolated from host services.

Run: uv run --frozen pytest scripts/test_home_retirement.py -v --tb=short
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import home_retirement as retirement
from runtime.remote_access import installer

COPY = "Home mode has been retired. Sign in to use your account's private network. Your local work is kept."
STATE = "var/lib/happyranch-remote-access"
FINAL = STATE + "/home-retirement.json"
SERVICE = "happyranch-headscale.service"
SECRET = b"synthetic-private-retirement-sentinel"


class Fixture:
    def __init__(self, root: Path) -> None:
        root.mkdir(mode=0o700)
        self.root = root
        self.calls: list[list[str]] = []
        self.accounts: set[str] = set()
        self.properties = dict(FragmentPath="", DropInPaths="", LoadState="not-found",
            ActiveState="inactive", SubState="dead", UnitFileState="", MainPID="0", ControlPID="0",
            ControlGroup="", Job="", NeedDaemonReload="no")
        self.io = installer.ManagedInstaller(root=root, root_owner=(os.getuid(), os.getgid()),
            layout="deb", run=self.run, lookup_user=self.lookup_user, lookup_group=self.lookup_group)

    def run(self, argv: list[str], **kwargs: object) -> tuple[int, str]:
        self.calls.append(argv)
        assert argv[:3] == ["systemctl", "show", SERVICE], "no service/account effect is allowed"
        assert all(arg.startswith("--property=") for arg in argv[3:])
        keys = [arg.removeprefix("--property=") for arg in argv[3:]]
        return 0, "\n".join(key + "=" + self.properties[key] for key in keys)

    def lookup_user(self, name: str) -> SimpleNamespace:
        assert name == "happyranch-headscale"
        if "user" in self.accounts:
            return SimpleNamespace(pw_name=name, pw_uid=os.getuid(), pw_gid=os.getgid())
        raise KeyError(name)

    def lookup_group(self, name: str) -> SimpleNamespace:
        assert name == "happyranch-headscale"
        if "group" in self.accounts:
            return SimpleNamespace(gr_name=name, gr_gid=os.getgid(), gr_mem=[])
        raise KeyError(name)

    def directory(self, relative: str) -> Path:
        path = self.root / relative
        path.mkdir(parents=True, exist_ok=True)
        for parent in (path, *path.parents):
            if parent == self.root:
                break
            parent.chmod(0o700 if parent == self.root / STATE else 0o755)
        return path

    def file(self, relative: str, data: bytes = SECRET, mode: int = 0o600) -> Path:
        path = self.root / relative
        self.directory(str(Path(relative).parent))
        path.write_bytes(data)
        path.chmod(mode)
        return path

    def snapshot(self) -> dict[str, tuple]:
        result = {}
        for path in self.root.rglob("*"):
            info = path.lstat()
            content = (hashlib.sha256(path.read_bytes()).hexdigest() if stat.S_ISREG(info.st_mode) else
                       os.readlink(path) if stat.S_ISLNK(info.st_mode) else None)
            result[str(path.relative_to(self.root))] = (info.st_dev, info.st_ino, info.st_uid,
                info.st_gid, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns, content)
        return result

    def detect(self, state: str, detail: str | None = None) -> dict[str, str]:
        before = self.snapshot()
        result = self.io.home_retirement_status()
        assert result["state"] == state
        assert result["message"] == COPY
        assert set(result) == {"state", "detail", "message"}
        if detail is not None:
            assert result["detail"] == detail
        assert SECRET.decode() not in json.dumps(result)
        assert self.snapshot() == before, "read-only detection changed persisted evidence or local data"
        assert all(call[:3] == ["systemctl", "show", SERVICE] for call in self.calls)
        return result


@pytest.fixture
def fixture(tmp_path: Path) -> Fixture:
    return Fixture(tmp_path / "root")


@pytest.mark.parametrize("existing_state", [False, True])
def test_fresh_detection_keeps_local_data(fixture: Fixture, existing_state: bool) -> None:
    # Local daemon data, tokens, backups and native/client state are outside the
    # bounded retirement inventory; their presence must not imply home authority.
    for relative in ("home/operator/org/task.db", "home/operator/repos/work.txt",
                     "home/operator/daemon.token", "backups/snapshot", "var/lib/other/native-credential"):
        fixture.file(relative)
    if existing_state:
        fixture.directory(STATE)
    first = fixture.detect("fresh", "no legacy evidence")
    assert fixture.detect("fresh") == first
    assert len(fixture.calls) == 2


@pytest.mark.parametrize("relative", [
    "etc/happyranch/headscale", "var/lib/happyranch-headscale", "run/happyranch-headscale",
    "etc/happyranch/control-ca.pem", "etc/happyranch/empty-ca-directory", "etc/happyranch/invite-issuer.json",
    "etc/happyranch/.invite-issuer.json.pending", "etc/happyranch/.control-ca.pem.pending",
    STATE + "/headscale-account.json", STATE + "/headscale-publication.json",
    STATE + "/home-issuer-publication.json", STATE + "/home-activation.json",
    STATE + "/home-activation-txn.json", STATE + "/home-ca", STATE + "/home-issuer",
    STATE + "/home-minter", STATE + "/.home-retirement.json.pending", STATE + "/home-retirement-intent.json",
])
def test_legacy_evidence_is_never_adopted_or_resumed(fixture: Fixture, relative: str,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    path = fixture.file(relative)
    original = os.open

    def no_private_read(name: str, flags: int, *args: object, **kwargs: object) -> int:
        if name == path.name:
            pytest.fail("detection opened private legacy evidence")
        return original(name, flags, *args, **kwargs)

    monkeypatch.setattr(retirement.os, "open", no_private_read)
    fixture.detect("migration pending", "legacy evidence retained")
    assert not fixture.calls


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "socket", "mode", "oversized", "malformed",
    "extra-secret", "partial", "duplicate", "invalid-utf8", "wrong-owner"])
def test_untrusted_final_receipt_never_means_retired(fixture: Fixture, tmp_path: Path, kind: str,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    path = fixture.file(FINAL, b'{"format":"home-retirement@1","state":"retired","account":"absent"}')
    channel = None
    if kind in ("symlink", "fifo", "socket"):
        path.unlink()
        if kind == "symlink":
            outside = tmp_path / "outside"
            outside.write_bytes(SECRET)
            path.symlink_to(outside)
        elif kind == "fifo":
            os.mkfifo(path, 0o600)
        else:
            channel = socket.socket(socket.AF_UNIX)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                channel.bind(f"/proc/self/fd/{directory}/{path.name}")
            finally:
                os.close(directory)
    elif kind == "hardlink":
        os.link(path, tmp_path / "external-link")
    elif kind == "mode":
        path.chmod(0o644)
    elif kind == "oversized":
        path.write_bytes(b" " * 1025)
    elif kind == "malformed":
        path.write_bytes(SECRET)
    elif kind == "extra-secret":
        path.write_bytes(json.dumps(dict(format="home-retirement@1", state="retired", account="absent",
            private_key=SECRET.decode())).encode())
    elif kind == "partial":
        path.write_bytes(b'{"format":"home-retirement@1","state":"stopping","account":"absent"}')
    elif kind == "duplicate":
        path.write_bytes(b'{"format":"home-retirement@1","state":"pending","state":"retired","account":"absent"}')
    elif kind == "invalid-utf8":
        path.write_bytes(b"\xff")
    elif kind == "wrong-owner":
        original = os.stat

        def foreign(name: object, *args: object, **kwargs: object) -> os.stat_result:
            info = original(name, *args, **kwargs)
            if name == "home-retirement.json" and kwargs.get("dir_fd") is not None:
                values = list(info)
                values[4] = info.st_uid + 1
                return os.stat_result(values)
            return info

        monkeypatch.setattr(retirement.os, "stat", foreign)
    try:
        fixture.detect("migration pending", "unsafe or changing evidence")
        assert not fixture.calls
        if kind == "symlink":
            assert (tmp_path / "outside").read_bytes() == SECRET
    finally:
        if channel is not None:
            channel.close()


@pytest.mark.parametrize("relative", ["var", "var/lib", STATE, "etc", "etc/happyranch", "etc/systemd"])
def test_symlinked_parent_stays_pending(fixture: Fixture, tmp_path: Path, relative: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_bytes(SECRET)
    fixture.directory(str(Path(relative).parent))
    (fixture.root / relative).symlink_to(outside, target_is_directory=True)
    fixture.detect("migration pending", "unsafe or changing evidence")
    assert (outside / "sentinel").read_bytes() == SECRET
    assert not fixture.calls


@pytest.mark.parametrize("relative,data", [
    ("etc/systemd/system/" + SERVICE, SECRET),
    ("run/systemd/generator/" + SERVICE, SECRET),
    ("etc/systemd/system/foreign.service", b"[Service]\nExecStart=/foreign/happyranch-headscale\n"),
    ("etc/systemd/system/service.d/foreign.conf", b"Environment=happyranch-headscale\n"),
    ("usr/share/dbus-1/system-services/foreign.service", b"Exec=/foreign/happyranch-headscale\n"),
])
def test_foreign_service_evidence_has_zero_effects(fixture: Fixture, relative: str, data: bytes) -> None:
    fixture.file(relative, data, 0o644)
    fixture.detect("migration pending", "legacy evidence retained")
    assert not fixture.calls


def test_alias_is_observed_without_following(fixture: Fixture) -> None:
    directory = fixture.directory("etc/systemd/system/other.target.wants")
    (directory / "foreign.service").symlink_to("/external/" + SERVICE)
    fixture.detect("migration pending", "legacy evidence retained")
    assert not fixture.calls


@pytest.mark.parametrize("key,value", [("FragmentPath", "/foreign/unit"), ("DropInPaths", "/foreign/dropin"),
    ("LoadState", "loaded"), ("ActiveState", "active"), ("SubState", "running"), ("UnitFileState", "enabled"),
    ("MainPID", "42"), ("ControlPID", "43"), ("ControlGroup", "/foreign"), ("Job", "1"),
    ("NeedDaemonReload", "yes")])
def test_native_uncertainty_stays_pending(fixture: Fixture, key: str, value: str) -> None:
    fixture.properties[key] = value
    fixture.detect("migration pending", "service retirement unconfirmed")
    assert len(fixture.calls) == 1


@pytest.mark.parametrize("kind", ["user", "group"])
def test_account_name_alone_is_not_ownership(fixture: Fixture, kind: str) -> None:
    fixture.accounts.add(kind)
    fixture.detect("migration pending", "account retirement unconfirmed")
    assert len(fixture.calls) == 1


@pytest.mark.parametrize("mode", [0o755, 0o777])
def test_unsafe_private_parent_stays_pending(fixture: Fixture, mode: int) -> None:
    fixture.directory(STATE).chmod(mode)
    fixture.detect("migration pending", "unsafe or changing evidence")
    assert not fixture.calls


@pytest.mark.parametrize("code,data", [(1, SECRET.decode()), (0, "Job=" + SECRET.decode()),
    (0, "Job=\nJob=" + SECRET.decode())])
def test_service_observation_failure_has_bounded_output(fixture: Fixture, code: int, data: str,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    def failed_observation(argv: list[str], **kwargs: object) -> tuple[int, str]:
        fixture.calls.append(argv)
        return code, data

    monkeypatch.setattr(fixture.io.manager, "_run", failed_observation)
    fixture.detect("migration pending", "unsafe or changing evidence")
    assert len(fixture.calls) == 1


def test_retained_nonhome_config_and_exact_helper_unit(fixture: Fixture) -> None:
    fixture.file("etc/happyranch/sidecar.json", b'{"Profile":"linux-managed-identity-v1"}', 0o640)
    fixture.file(installer.PACKAGED_HELPER_UNIT, installer.PACKAGED_HELPER_UNIT_BYTES, 0o644)
    fixture.detect("fresh", "no legacy evidence")
    fixture.file("etc/happyranch/sidecar.json", b'{"home_binding":{}}', 0o640)
    fixture.detect("migration pending", "legacy evidence retained")


@pytest.mark.parametrize("boundary", ["missing-receipt", "missing-parent"])
def test_absence_race_never_reports_fresh(fixture: Fixture, boundary: str,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    if boundary == "missing-receipt":
        fixture.directory(STATE)
    original = os.stat
    injected: dict = {}

    def appeared(name: object, *args: object, **kwargs: object) -> os.stat_result:
        try:
            return original(name, *args, **kwargs)
        except FileNotFoundError:
            # Inject only at the final absence readback after the otherwise
            # valid service/account observations, never an earlier refusal.
            target = "home-retirement.json" if boundary == "missing-receipt" else "var"
            if not injected and fixture.calls and name == target and kwargs.get("dir_fd") is not None:
                injected["started"] = True
                fixture.file(FINAL if boundary == "missing-receipt" else STATE + "/home-activation.json")
                injected["snapshot"] = fixture.snapshot()
            raise

    monkeypatch.setattr(retirement.os, "stat", appeared)
    result = fixture.io.home_retirement_status()
    assert injected.get("started"), "the named final-absence race was not reached"
    assert result == dict(state="migration pending", detail="unsafe or changing evidence", message=COPY)
    assert fixture.snapshot() == injected["snapshot"]
    assert len(fixture.calls) == 1


@pytest.mark.parametrize("pending", [False, True])
def test_installer_status_cli_is_read_only(fixture: Fixture, monkeypatch: pytest.MonkeyPatch,
                                         capsys: pytest.CaptureFixture[str], pending: bool) -> None:
    if pending:
        fixture.file(STATE + "/home-activation-txn.json")
    monkeypatch.setattr(installer, "ManagedInstaller", lambda **kwargs: fixture.io)
    before = fixture.snapshot()
    assert installer.main(["home-retirement-status", "--layout", "deb"]) == int(pending)
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == dict(state="migration pending" if pending else "fresh",
        detail="legacy evidence retained" if pending else "no legacy evidence", message=COPY)
    assert fixture.snapshot() == before
