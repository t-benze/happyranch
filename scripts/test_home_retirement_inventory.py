"""Legacy retirement lineage through real readers; no provisioning fixture.

Run: uv run --frozen pytest scripts/test_home_retirement_inventory.py -v --tb=short
The native/NSS adapter and synthetic binary stand in for installed observations.
Historical service fixtures are frozen bytes, independent of retiring renderers.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import home_retirement_inventory as inventory
from runtime.remote_access.home_retirement import RetirementUncertain
from runtime.remote_access.home_retirement_fence import retirement_fence
from scripts.test_home_retirement import Fixture, STATE
from scripts.test_home_retirement_service import BINARY, BINARY_BYTES, FRAGMENT, LINK, ServiceFixture

ACCOUNT = "happyranch-headscale"
ACCOUNT_RECORD = STATE + "/headscale-account.json"
PUBLICATION = STATE + "/headscale-publication.json"
ACTIVATION = STATE + "/home-activation.json"
FIXTURES = Path(__file__).parent / "fixtures/home-retirement"
# Closed historical path lists are fixture input, not migration output.
MEMBERS = ["/etc/systemd/system/happyranch-headscale.service",
    "/etc/happyranch/headscale/config.yaml", "/etc/happyranch/headscale/derp.yaml",
    "/etc/happyranch/headscale/policy.hujson", "/etc/happyranch/control-ca.pem",
    "/etc/happyranch/invite-issuer.json"]
LIVE = ["/var/lib/happyranch-headscale/db.sqlite", "/var/lib/happyranch-headscale/db.sqlite-wal",
    "/var/lib/happyranch-headscale/db.sqlite-shm", "/var/lib/happyranch-headscale/noise.key",
    "/var/lib/happyranch-headscale/derp.key", "/run/happyranch-headscale/headscale.sock",
    "/run/happyranch-tsnet-sidecar/observe.sock", "/var/lib/happyranch-headscale/acme-unused"]


class InventoryFixture(ServiceFixture):
    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        super().__init__(root)
        # Only the external package artifact is synthetic. Production still
        # streams the candidate, verifies custody and compares a trusted pin.
        monkeypatch.setattr(inventory, "_BINARIES", {inventory.platform.machine():
            hashlib.sha256(BINARY_BYTES).hexdigest()})
        self.record = dict(version=1, account=ACCOUNT, uid=os.getuid(), gid=os.getgid(),
            group_created_with_user=True)
        self.publication = dict(version=1, uid=os.getuid(), gid=os.getgid(), dial_ip="192.168.1.40",
            files={path: "c" * 64 for path in ("/etc/happyranch/headscale/config.yaml",
                "/etc/happyranch/headscale/derp.yaml", "/etc/happyranch/headscale/server.crt",
                "/etc/happyranch/headscale/server.key", "/etc/happyranch/control-ca.pem")},
            directories=["/etc/happyranch/empty-ca-directory"])
        self.activation = dict(format="home-activation@1", release_uid=1234, home_id="home",
            control_plane_id="control", connector_id="connector", activation_generation=7,
            operation_id="b" * 32, ca_der_sha256="a" * 64, dial_ip="192.168.1.40",
            member_digests={path: "c" * 64 for path in MEMBERS}, local_node_stable_id="node",
            home_endpoint="100.64.0.10:443", phase="ready", ownership=dict(
                members=LIVE, links={"/" + LINK: "/" + FRAGMENT}))
        self.put_json(ACCOUNT_RECORD, self.record)
        self.put_json(PUBLICATION, self.publication)
        self.file("etc/login.defs", (f"UID_MIN {os.getuid() + 1}\nGID_MIN {os.getgid() + 1}\n"
            f"SYS_UID_MIN 1\nSYS_GID_MIN 1\n").encode(), 0o644)
        self.set_unit(home=True)
        self.inventory = inventory.LegacyInventory(self.io)
        self.nss = None
        self.lock_status = "L"
        self.on_getent = lambda: None

    def put_json(self, relative: str, value: dict) -> None:
        self.file(relative, json.dumps(value, sort_keys=True, separators=(",", ":")).encode())

    def set_unit(self, *, home: bool) -> None:
        data = (FIXTURES / ("headscale-home.service" if home else "headscale-inert.service")).read_bytes()
        self.file(FRAGMENT, data, 0o644)
        self.activation["member_digests"]["/" + FRAGMENT] = hashlib.sha256(data).hexdigest()
        if home:
            self.put_json(ACTIVATION, self.activation)

    def lookup_user(self, name: str) -> SimpleNamespace:
        assert name == ACCOUNT
        return SimpleNamespace(pw_name=name, pw_uid=os.getuid(), pw_gid=os.getgid(),
            pw_shell="/usr/sbin/nologin", pw_dir="/nonexistent")

    def run(self, argv: list[str], **kwargs: object) -> tuple[int, str]:
        if argv[:1] == ["getent"]:
            self.calls.append(argv)
            self.on_getent()
            if self.nss is not None:
                return self.nss
            if argv[1] == "passwd":
                return 0, f"{ACCOUNT}:x:{os.getuid()}:{os.getgid()}::/nonexistent:/usr/sbin/nologin\n"
            return 0, f"{ACCOUNT}:x:{os.getgid()}:\n"
        if argv == ["passwd", "--status", ACCOUNT]:
            self.calls.append(argv)
            return 0, f"{ACCOUNT} {self.lock_status} 2026-01-01 0 99999 7 -1\n"
        return super().run(argv, **kwargs)

    def observe(self):
        before = self.snapshot()
        result = self.inventory.observe()
        assert self.snapshot() == before
        assert self.effects() == []
        assert result.activation is not None
        assert result.activation.connector_id == "connector"
        assert result.service.fragment_sha256 == hashlib.sha256((self.root / FRAGMENT).read_bytes()).hexdigest()
        assert result.receipt_digests["headscale-account.json"] == hashlib.sha256((self.root / ACCOUNT_RECORD).read_bytes()).hexdigest()
        return result

    def refuse(self):
        before = self.snapshot()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            self.inventory.observe()
        assert self.snapshot() == before
        assert self.effects() == []


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> InventoryFixture:
    return InventoryFixture(tmp_path / "root", monkeypatch)


def test_protected_historical_lineage_and_native_identity_are_read_only(fixture: InventoryFixture) -> None:
    first = fixture.observe()
    assert first.native.process == (123, 321)
    assert inventory.LegacyInventory(fixture.io).observe() == first
    fixture.inventory.require(first)
    assert repr(first) == "LegacyOwnership(<redacted>)"
    with pytest.raises(TypeError):
        first.receipt_digests["headscale-account.json"] = "foreign"


def test_inert_legacy_requires_receipts_but_not_an_activation(fixture: InventoryFixture) -> None:
    fixture.set_unit(home=False)
    (fixture.root / ACTIVATION).unlink()
    (fixture.root / LINK).unlink()
    fixture.stopped()
    fixture.properties["UnitFileState"] = "disabled"
    before = fixture.snapshot()
    result = fixture.inventory.observe()
    assert result.activation is None and result.native.process is None and result.native.link is None
    assert set(result.receipt_digests) == {"headscale-account.json", "headscale-publication.json"}
    assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize("name", [ACCOUNT_RECORD, PUBLICATION, ACTIVATION])
@pytest.mark.parametrize("change", ["missing", "symlink", "hardlink", "mode", "duplicate", "extra", "pending"])
def test_receipt_names_and_candidate_bytes_never_establish_ownership(
        fixture: InventoryFixture, name: str, change: str) -> None:
    path = fixture.root / name
    if change in ("missing", "symlink"):
        data = path.read_bytes()
        path.unlink()
        if change == "symlink":
            keeper = fixture.file("outside-receipt", data)
            path.symlink_to(keeper)
    elif change == "hardlink":
        os.link(path, fixture.root / "outside-receipt")
    elif change == "mode":
        path.chmod(0o644)
    elif change == "duplicate":
        data = path.read_bytes()
        key, value = next(iter(json.loads(data).items()))
        path.write_bytes(data[:-1] + b"," + json.dumps(key).encode() + b":" + json.dumps(value).encode() + b"}")
    elif change == "extra":
        data = json.loads(path.read_bytes())
        data["private"] = "synthetic-private-retirement-sentinel"
        fixture.put_json(name, data)
    else:
        fixture.file(str(path.relative_to(fixture.root).parent / ("." + path.name + ".pending")))
    fixture.refuse()


@pytest.mark.parametrize("case", ["unlocked", "alias-user", "alias-group", "shared-gid", "native-error",
    "malformed-nss", "oversized-nss", "shell", "home-link", "home-missing", "uid-range", "limits-duplicate",
    "receipt-uid", "receipt-version-bool", "receipt-group-created"])
def test_foreign_reused_or_shared_accounts_are_never_adopted(
        fixture: InventoryFixture, case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if case == "unlocked":
        fixture.lock_status = "P"
    elif case == "alias-user":
        fixture.nss = (0, f"{ACCOUNT}:x:{os.getuid()}:{os.getgid()}::/nonexistent:/usr/sbin/nologin\n"
            f"foreign:x:{os.getuid()}:{os.getgid()+1}::/nonexistent:/bin/false\n")
    elif case == "alias-group":
        old = fixture.run
        monkeypatch.setattr(fixture, "run", lambda argv, **kwargs: (0,
            f"{ACCOUNT}:x:{os.getgid()}:\nforeign:x:{os.getgid()}:\n") if argv == ["getent", "group"] else old(argv, **kwargs))
        fixture.io._run = fixture.run
    elif case == "shared-gid":
        fixture.nss = (0, f"{ACCOUNT}:x:{os.getuid()}:{os.getgid()}::/nonexistent:/usr/sbin/nologin\n"
            f"foreign:x:{os.getuid()+1}:{os.getgid()}::/nonexistent:/bin/false\n")
    elif case in ("native-error", "malformed-nss", "oversized-nss"):
        fixture.nss = {"native-error": (1, "synthetic-private-native-error"),
            "malformed-nss": (0, "foreign:bad"), "oversized-nss": (0, "x" * 65537)}[case]
    elif case in ("shell", "home-missing"):
        user = fixture.lookup_user(ACCOUNT)
        if case == "shell":
            user.pw_shell = "/bin/bash"
        else:
            user.pw_dir = "/missing-home"
        fixture.io._lookup_user = lambda _: user
    elif case == "home-link":
        (fixture.root / "nonexistent").symlink_to(fixture.root)
    elif case in ("uid-range", "limits-duplicate"):
        fixture.file("etc/login.defs", b"UID_MIN 1\n" if case == "uid-range" else b"UID_MIN 1000\nUID_MIN 1001\n", 0o644)
    else:
        record = copy.deepcopy(fixture.record)
        key, value = {"receipt-uid": ("uid", os.getuid()+1), "receipt-version-bool": ("version", True),
            "receipt-group-created": ("group_created_with_user", False)}[case]
        record[key] = value
        fixture.put_json(ACCOUNT_RECORD, record)
    fixture.refuse()


@pytest.mark.parametrize("case", ["unit", "self-hashed-unit", "binary", "activation-unit", "publication-config", "publication-ca",
    "dial-ip", "arbitrary-member", "foreign-live-member", "foreign-link", "native-fragment", "native-process"])
def test_package_receipt_and_native_lineage_must_agree(
        fixture: InventoryFixture, case: str) -> None:
    if case in ("unit", "self-hashed-unit", "binary"):
        data = b"foreign-self-hashed-candidate"
        fixture.file(BINARY if case == "binary" else FRAGMENT, data,
            0o755 if case == "binary" else 0o644)
        if case == "self-hashed-unit":
            # The inert pre-activation layout has no second activation digest
            # or active-unit gate to mask the released-unit pin under test.
            (fixture.root / ACTIVATION).unlink()
            (fixture.root / LINK).unlink()
            fixture.stopped()
            fixture.properties["UnitFileState"] = "disabled"
    elif case in ("activation-unit", "foreign-live-member", "foreign-link"):
        data = copy.deepcopy(fixture.activation)
        if case == "activation-unit":
            data["member_digests"]["/" + FRAGMENT] = "e" * 64
        elif case == "foreign-live-member":
            data["ownership"]["members"][0] = "/home/operator/task.db"
        else:
            data["ownership"]["links"] = {"/etc/foreign.service": "/" + FRAGMENT}
        fixture.put_json(ACTIVATION, data)
    elif case in ("publication-config", "publication-ca", "dial-ip", "arbitrary-member"):
        data = copy.deepcopy(fixture.publication)
        if case == "dial-ip":
            data["dial_ip"] = "192.168.1.41"
        else:
            path = {"publication-config": "/etc/happyranch/headscale/config.yaml",
                "publication-ca": "/etc/happyranch/control-ca.pem", "arbitrary-member": "/home/operator/task.db"}[case]
            data["files"][path] = "e" * 64
        fixture.put_json(PUBLICATION, data)
    elif case == "native-fragment":
        fixture.properties["FragmentPath"] = "/foreign.service"
    else:
        fixture.ps_output = "999 " + " ".join([str(os.getuid())] * 4) + "\n"
    fixture.refuse()


@pytest.mark.parametrize("change", ["receipt-bytes", "receipt-inode", "fragment-inode", "process-start"])
def test_changed_identity_between_observations_cannot_authorize_effects(
        fixture: InventoryFixture, change: str) -> None:
    first = fixture.observe()
    if change == "receipt-bytes":
        data = copy.deepcopy(fixture.publication)
        data["files"]["/etc/happyranch/headscale/server.key"] = "d" * 64
        fixture.put_json(PUBLICATION, data)
    elif change == "receipt-inode":
        data = (fixture.root / ACCOUNT_RECORD).read_bytes()
        (fixture.root / ACCOUNT_RECORD).unlink()
        fixture.file(ACCOUNT_RECORD, data)
    elif change == "fragment-inode":
        data = (fixture.root / FRAGMENT).read_bytes()
        (fixture.root / FRAGMENT).unlink()
        fixture.file(FRAGMENT, data, 0o644)
    else:
        fixture.process_start(322)
    before = fixture.snapshot()
    with pytest.raises(RetirementUncertain, match="^migration pending$"):
        fixture.inventory.require(first)
    assert fixture.snapshot() == before and fixture.effects() == []


def test_receipt_swap_during_native_observation_is_not_hidden_by_equal_bytes(
        fixture: InventoryFixture) -> None:
    swapped = []
    def replace(count: int) -> None:
        if count == 1:
            data = (fixture.root / ACCOUNT_RECORD).read_bytes()
            (fixture.root / ACCOUNT_RECORD).rename(fixture.root / "original-account-receipt")
            fixture.file(ACCOUNT_RECORD, data)
            swapped.append(True)
    fixture.on_show = replace
    with pytest.raises(RetirementUncertain, match="^migration pending$"):
        fixture.inventory.observe()
    assert swapped == [True] and fixture.effects() == []
    assert (fixture.root / ACCOUNT_RECORD).read_bytes() == (fixture.root / "original-account-receipt").read_bytes()


def test_only_live_exact_retirement_fence_may_explain_activation_pending(
        fixture: InventoryFixture) -> None:
    first = fixture.observe()
    with retirement_fence(fixture.io) as fence:
        fixture.refuse()
        assert fixture.inventory.observe(fence=fence) == first
        fixture.inventory.require(first, fence=fence)
    with pytest.raises(RetirementUncertain, match="^migration pending$"):
        fixture.inventory.observe(fence=fence)
    fixture.refuse()


def test_another_state_roots_live_fence_cannot_explain_this_roots_pending_file(
        fixture: InventoryFixture) -> None:
    other = Fixture(fixture.root.parent / "other-root")
    other.directory(STATE)
    with retirement_fence(fixture.io), retirement_fence(other.io) as foreign_fence:
        before = fixture.snapshot(), other.snapshot()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            fixture.inventory.observe(fence=foreign_fence)
        assert (fixture.snapshot(), other.snapshot()) == before
        assert fixture.effects() == [] and other.calls == []


PRIVATE = STATE + "/home-ca"
OPAQUE = "var/lib/happyranch-headscale/db.sqlite"
PRIVATE_KEY = PRIVATE + "/ca.key"
CONFIG = "etc/happyranch/headscale/config.yaml"


def private_setup(fixture: InventoryFixture) -> None:
    """Frozen historical input only; no activation/preparation implementation."""
    for path in fixture.publication["files"]:
        data = ("synthetic-legacy-" + Path(path).name).encode()
        fixture.file(path.lstrip("/"), data, 0o640 if path.endswith("/control-ca.pem") else 0o600)
        fixture.publication["files"][path] = hashlib.sha256(data).hexdigest()
        if path in fixture.activation["member_digests"]:
            fixture.activation["member_digests"][path] = hashlib.sha256(data).hexdigest()
    for path in ("/etc/happyranch/headscale/policy.hujson", "/etc/happyranch/invite-issuer.json"):
        data = b"synthetic-legacy-bound-input"
        fixture.file(path.lstrip("/"), data)
        fixture.activation["member_digests"][path] = hashlib.sha256(data).hexdigest()
    fixture.put_json(PUBLICATION, fixture.publication)
    fixture.put_json(ACTIVATION, fixture.activation)
    for name in ("db.sqlite", "db.sqlite-wal", "db.sqlite-shm", "noise.key", "derp.key"):
        fixture.file("var/lib/happyranch-headscale/" + name, b"synthetic-opaque-never-open")
    fixture.directory("var/lib/happyranch-headscale/acme-unused").chmod(0o700)
    fixture.file(PRIVATE + "/ca.pem", (fixture.root / "etc/happyranch/control-ca.pem").read_bytes())
    fixture.file(PRIVATE_KEY, b"synthetic-private-CA-never-open")
    fixture.file(PRIVATE + "/leaf.pem", (fixture.root / "etc/happyranch/headscale/server.crt").read_bytes())
    fixture.file(PRIVATE + "/leaf.key", b"synthetic-private-leaf-never-open")
    fixture.directory("etc/happyranch/empty-ca-directory")
    fixture.directory("run/happyranch-headscale")
    for path in ("etc/happyranch/headscale", "var/lib/happyranch-headscale", "run/happyranch-headscale", PRIVATE):
        (fixture.root / path).chmod(0o700)
    fixture.io._lookup_group = lambda name: SimpleNamespace(gr_name=name, gr_gid=os.getgid(), gr_mem=[])


def private_refusal(fixture: InventoryFixture, ownership, fence) -> None:
    before = fixture.snapshot()
    with pytest.raises(RetirementUncertain, match="^migration pending$"):
        fixture.inventory.private_members(ownership, fence=fence)
    assert fixture.snapshot() == before
    assert fixture.effects() == []


def test_private_inventory_pins_legacy_custody_without_opening_opaque_or_CA_key(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    private_setup(fixture)
    fixture.file("retained-work/task.db", b"synthetic-retained-work")
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        before = fixture.snapshot()
        real_open = os.open
        forbidden = {"ca.key", "leaf.key", "db.sqlite", "db.sqlite-wal", "db.sqlite-shm", "noise.key", "derp.key"}
        opened = []
        def observe_open(path, *args, **kwargs):
            opened.append(str(path))
            assert Path(path).name not in forbidden, "opaque/private key content was opened"
            return real_open(path, *args, **kwargs)
        with monkeypatch.context() as scoped:
            scoped.setattr(inventory.os, "open", observe_open)
            captured = fixture.inventory.private_members(ownership, fence=fence)
            assert inventory.LegacyInventory(fixture.io).private_members(ownership, fence=fence) == captured
            fixture.inventory.require_private(ownership, captured, fence=fence)
        assert CONFIG in captured.members and OPAQUE in captured.members and PRIVATE_KEY in captured.members
        assert captured.members["run/happyranch-headscale/headscale.sock"] is None
        assert str(fixture.root / "retained-work/task.db") not in opened
        assert repr(captured) == "LegacyPrivateMembers(<redacted>)"
        with pytest.raises(TypeError):
            captured.members[OPAQUE] = None
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize("path", [CONFIG, "etc/happyranch/headscale/server.key", OPAQUE, PRIVATE_KEY])
@pytest.mark.parametrize("change", ["mode", "symlink", "hardlink", "fifo"])
def test_private_unsafe_leaves_are_preserved_without_open_or_repair(
        fixture: InventoryFixture, path: str, change: str) -> None:
    private_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        target = fixture.root / path
        if change == "mode":
            target.chmod(0o644)
        elif change == "hardlink":
            os.link(target, fixture.root / "external-keeper")
        else:
            target.unlink()
            if change == "symlink":
                target.symlink_to(fixture.root / "external-keeper")
            else:
                os.mkfifo(target, 0o600)
        private_refusal(fixture, ownership, fence)


@pytest.mark.parametrize("path", [CONFIG, "etc/happyranch/headscale/server.crt", "etc/happyranch/headscale/server.key",
    "etc/happyranch/headscale/policy.hujson", "etc/happyranch/control-ca.pem", "etc/happyranch/invite-issuer.json",
    PRIVATE + "/ca.pem", PRIVATE + "/leaf.pem"])
def test_private_digest_comes_from_protected_lineage_not_the_candidate(
        fixture: InventoryFixture, path: str) -> None:
    private_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        # Preserve mode/owner; the independently recorded digest is the only
        # varied condition. A metadata-only implementation would accept it.
        (fixture.root / path).write_bytes(b"foreign-candidate-self-hash")
        private_refusal(fixture, ownership, fence)


@pytest.mark.parametrize("path", [CONFIG, "etc/happyranch/control-ca.pem", "etc/happyranch/invite-issuer.json",
    "etc/happyranch/empty-ca-directory"])
def test_initial_private_snapshot_cannot_adopt_missing_immutable_members(
        fixture: InventoryFixture, path: str) -> None:
    private_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        target = fixture.root / path
        (target.rmdir if target.is_dir() else target.unlink)()
        private_refusal(fixture, ownership, fence)


@pytest.mark.parametrize("directory", ["etc/happyranch/headscale", "var/lib/happyranch-headscale",
    "run/happyranch-headscale", PRIVATE])
@pytest.mark.parametrize("change", ["mode", "symlink", "unknown"])
def test_private_parent_and_closed_shallow_inventory_refuse_foreign_evidence(
        fixture: InventoryFixture, directory: str, change: str) -> None:
    private_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        target = fixture.root / directory
        if change == "mode":
            target.chmod(0o755)
        elif change == "symlink":
            target.rename(fixture.root / "outside-private-directory")
            target.symlink_to(fixture.root / "outside-private-directory")
        else:
            # An unknown nested directory must be refused without traversing
            # its contents; the sentinel is retained with exact metadata.
            (target / "unknown").mkdir(mode=0o700)
            (target / "unknown/retained.db").write_bytes(b"synthetic-private-foreign-work")
        private_refusal(fixture, ownership, fence)


@pytest.mark.parametrize("case", ["partial-ca", "empty-ca-key", "large-ca-key", "nonempty-acme", "nonempty-empty-ca"])
def test_private_pair_and_empty_directory_contracts(fixture: InventoryFixture, case: str) -> None:
    private_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        if case == "partial-ca":
            (fixture.root / PRIVATE_KEY).unlink()
        elif case in ("empty-ca-key", "large-ca-key"):
            (fixture.root / PRIVATE_KEY).write_bytes(b"" if case == "empty-ca-key" else b"x" * 4097)
        else:
            directory = "var/lib/happyranch-headscale/acme-unused" if case == "nonempty-acme" else "etc/happyranch/empty-ca-directory"
            (fixture.root / directory / "foreign").write_bytes(b"synthetic-private-foreign-work")
        private_refusal(fixture, ownership, fence)


@pytest.mark.parametrize("path", [CONFIG, OPAQUE, PRIVATE_KEY, "var/lib/happyranch-headscale"])
def test_private_inventory_requires_original_inode_after_blocking_observation(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    private_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        original = fixture.inventory.require
        calls, snapshots = [], []
        def replaced(observed, **kwargs):
            original(observed, **kwargs)
            calls.append(True)
            if len(calls) == 2:
                target = fixture.root / path
                before = target.lstat()
                target.rename(fixture.root / "preserved-original-private")
                if stat.S_ISDIR(before.st_mode):
                    target.mkdir(mode=stat.S_IMODE(before.st_mode))
                    for child in (fixture.root / "preserved-original-private").iterdir():
                        if child.is_dir():
                            (target / child.name).mkdir(mode=stat.S_IMODE(child.lstat().st_mode))
                        else:
                            (target / child.name).write_bytes(child.read_bytes())
                            (target / child.name).chmod(stat.S_IMODE(child.lstat().st_mode))
                else:
                    target.write_bytes((fixture.root / "preserved-original-private").read_bytes())
                    target.chmod(stat.S_IMODE(before.st_mode))
                snapshots.append(fixture.snapshot())
        monkeypatch.setattr(fixture.inventory, "require", replaced)
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            fixture.inventory.private_members(ownership, fence=fence)
        assert snapshots and fixture.snapshot() == snapshots[0] and fixture.effects() == []


def test_private_socket_is_observed_without_connection(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    private_setup(fixture)
    directory = os.open(fixture.root / "run/happyranch-headscale", os.O_DIRECTORY)
    try:
        with socket.socket(socket.AF_UNIX) as listener:
            listener.bind(f"/proc/self/fd/{directory}/headscale.sock")
            (fixture.root / "run/happyranch-headscale/headscale.sock").chmod(0o600)
            monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail(
                "private inventory must not connect to the controller socket"))
            with retirement_fence(fixture.io) as fence:
                ownership = fixture.inventory.observe(fence=fence)
                before = fixture.snapshot()
                captured = fixture.inventory.private_members(ownership, fence=fence)
                assert stat.S_ISSOCK(captured.members["run/happyranch-headscale/headscale.sock"][4])
                assert fixture.snapshot() == before and fixture.effects() == []
    finally:
        os.close(directory)


@pytest.mark.parametrize("path", ["etc/happyranch/control-ca.pem", "etc/happyranch/invite-issuer.json",
    "etc/happyranch/empty-ca-directory"])
def test_private_shared_parent_pending_members_cannot_be_ignored(
        fixture: InventoryFixture, path: str) -> None:
    private_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        target = fixture.root / path
        (target.parent / ("." + target.name + ".pending")).write_bytes(b"synthetic-unfinished-publication")
        private_refusal(fixture, ownership, fence)


@pytest.mark.parametrize("path", [OPAQUE, PRIVATE_KEY, "var/lib/happyranch-headscale"])
@pytest.mark.parametrize("field", ["uid", "gid"])
def test_private_numeric_owners_must_match_historical_custody(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, path: str, field: str) -> None:
    private_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        real_stat, real_fstat = os.stat, os.fstat
        before = fixture.snapshot()
        def owner(info, candidate):
            if candidate == fixture.root / path:
                value = SimpleNamespace(**{key: getattr(info, key) for key in dir(info) if key.startswith("st_")})
                key = "st_" + field
                setattr(value, key, getattr(value, key) + 1)
                return value
            return info
        def changed_owner(name, *args, dir_fd=None, **kwargs):
            info = real_stat(name, *args, dir_fd=dir_fd, **kwargs)
            candidate = Path(os.readlink(f"/proc/self/fd/{dir_fd}")) / name if dir_fd is not None else Path(name)
            return owner(info, candidate)
        with monkeypatch.context() as scoped:
            scoped.setattr(inventory.os, "stat", changed_owner)
            scoped.setattr(inventory.os, "fstat", lambda fd: owner(real_fstat(fd), Path(os.readlink(f"/proc/self/fd/{fd}"))))
            with pytest.raises(RetirementUncertain, match="^migration pending$"):
                fixture.inventory.private_members(ownership, fence=fence)
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize("layout", ["ca-pair", "no-staging", "no-mutable"])
def test_optional_private_absence_is_recorded_without_claiming_an_effect(
        fixture: InventoryFixture, layout: str) -> None:
    private_setup(fixture)
    if layout in ("ca-pair", "no-staging"):
        for name in ("leaf.pem", "leaf.key"):
            (fixture.root / PRIVATE / name).unlink()
        if layout == "no-staging":
            for name in ("ca.pem", "ca.key"):
                (fixture.root / PRIVATE / name).unlink()
            (fixture.root / PRIVATE).rmdir()
    else:
        for child in (fixture.root / "var/lib/happyranch-headscale").iterdir():
            (child.rmdir if child.is_dir() else child.unlink)()
        (fixture.root / "var/lib/happyranch-headscale").rmdir()
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        before = fixture.snapshot()
        captured = fixture.inventory.private_members(ownership, fence=fence)
        missing = OPAQUE if layout == "no-mutable" else PRIVATE + "/leaf.key"
        assert captured.members[missing] is None
        assert fixture.snapshot() == before and fixture.effects() == []


def test_private_snapshot_requires_its_live_root_bound_fence(fixture: InventoryFixture) -> None:
    private_setup(fixture)
    other = Fixture(fixture.root.parent / "other-root")
    other.directory(STATE)
    with retirement_fence(fixture.io) as fence, retirement_fence(other.io) as other_fence:
        ownership = fixture.inventory.observe(fence=fence)
        before = fixture.snapshot(), other.snapshot()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            fixture.inventory.private_members(ownership, fence=other_fence)
        assert (fixture.snapshot(), other.snapshot()) == before
    before = fixture.snapshot()
    with pytest.raises(RetirementUncertain, match="^migration pending$"):
        fixture.inventory.private_members(ownership, fence=fence)
    assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize("change", ["immutable-inode", "opaque-inode", "opaque-absent"])
def test_private_saved_snapshot_never_adopts_replaced_or_disappearing_members(
        fixture: InventoryFixture, change: str) -> None:
    private_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        captured = fixture.inventory.private_members(ownership, fence=fence)
        path = fixture.root / (CONFIG if change == "immutable-inode" else OPAQUE)
        data, mode = path.read_bytes(), stat.S_IMODE(path.lstat().st_mode)
        path.rename(fixture.root / "preserved-original-private")
        if change != "opaque-absent":
            path.write_bytes(data)
            path.chmod(mode)
        before = fixture.snapshot()
        # A fresh candidate snapshot is structurally valid. It cannot replace
        # the original inventory just because its bytes/modes still match.
        assert fixture.inventory.private_members(ownership, fence=fence) != captured
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            fixture.inventory.require_private(ownership, captured, fence=fence)
        assert fixture.snapshot() == before and fixture.effects() == []


MANAGED_CONFIGS = ("connector.json", "sidecar.json", "policy.json", "release-daemon.json",
                   "daemon.token", "provision.json", "invite-issuer.json")
MANAGED_DIRS = ("var/lib/happyranch-connector", "var/lib/happyranch-tsnet-sidecar",
                "run/happyranch-connector", "run/happyranch-tsnet-sidecar")
MANAGED_OPAQUE = ("var/lib/happyranch-connector/trust-state.json",
    "var/lib/happyranch-connector/trust-state.json.anchor", "var/lib/happyranch-connector/trust-state.json.lock",
    "var/lib/happyranch-tsnet-sidecar/tailscaled.state", "var/lib/happyranch-tsnet-sidecar/credential.consumed",
    "var/lib/happyranch-tsnet-sidecar/tailscaled.log.conf", "var/lib/happyranch-tsnet-sidecar/tailscaled.log1.txt",
    "var/lib/happyranch-tsnet-sidecar/tailscaled.log2.txt", "run/happyranch-tsnet-sidecar/health.json")


def managed_setup(fixture: InventoryFixture, *, live: bool = True) -> None:
    """Synthetic historical data, independent of retired provisioning owners."""
    private_setup(fixture)
    original_lookup = fixture.lookup_user
    def lookup_user(name: str) -> SimpleNamespace:
        if name in ("happyranch", "release"):
            return SimpleNamespace(pw_name=name, pw_uid=os.getuid(), pw_gid=os.getgid())
        if name == ACCOUNT:
            return original_lookup(name)
        raise KeyError(name)
    fixture.io._lookup_user = lookup_user
    fixture.activation["release_uid"] = os.getuid()
    connector = dict(tenant_id="tenant", home_id="home", connector_id="connector", system=True,
        service_user="happyranch", service_group="happyranch", daemon_token_path="/etc/happyranch/daemon.token",
        policy_path="/etc/happyranch/policy.json", state_path="/var/lib/happyranch-connector/trust-state.json",
        excluded_daemon_ports=[8765], managed=dict(profile="linux-managed-identity-v1", control_plane_id="control"))
    inert = dict(Profile="linux-managed-identity-v1", StateDir="/var/lib/happyranch-tsnet-sidecar",
        RoleIdentity="home-sidecar-home", DERPPolicy="private-only", ListenAddr=":443", ExcludedDaemonPorts=[8765])
    release = dict(user="release", uid=os.getuid(), data_home="/home/release/.local/share/happyranch",
        unit="happyranch-daemon.service", fragment_path="/usr/lib/systemd/user/happyranch-daemon.service")
    policy = dict(schema_version=1, artifact_version=1,
        artifact=json.loads((Path(inventory.__file__).parent / "route_policy.json").read_bytes()),
        issued_at="2000-01-01T00:00:00+00:00", max_age_seconds=3600, revision=1, state="active")
    for name, data in (("connector.json", connector), ("sidecar.json", inert), ("policy.json", policy),
                       ("release-daemon.json", release)):
        fixture.file("etc/happyranch/" + name, json.dumps(data, sort_keys=True).encode(),
            0o644 if name == "release-daemon.json" else 0o640)
    fixture.file("etc/happyranch/daemon.token", b"synthetic-copied-daemon-token")
    digests = {name: hashlib.sha256((fixture.root / "etc/happyranch" / name).read_bytes()).hexdigest()
        for name in MANAGED_CONFIGS[:5]}
    fixture.put_json("etc/happyranch/provision.json", dict(version=1, layout="deb", phase="provisioned", digests=digests))
    if live:
        # Independent historical bytes: fixed order and compact insertion into
        # the original inert file, never the retiring live-config renderer.
        addition = b',"ControlURL":"https://control.happyranch.invalid:8443","control_tls":' + json.dumps(dict(
            dial_ip="192.168.1.40", ca_sha256="a" * 64), separators=(",", ":")).encode() + b',"home_binding":' + json.dumps(dict(
            home_id="home", control_plane_id="control", connector_id="connector", activation_generation=7),
            separators=(",", ":")).encode()
        path = fixture.root / "etc/happyranch/sidecar.json"
        data = path.read_bytes()
        path.write_bytes(data[:-1] + addition + b"}")
    invite = dict(format="home-invite-config@2", release_uid=os.getuid(), home_id="home", control_plane_id="control",
        connector_id="connector", ca_der_sha256="a" * 64, dial_ip="192.168.1.40",
        control_url="https://control.happyranch.invalid:8443")
    fixture.put_json("etc/happyranch/invite-issuer.json", invite)
    fixture.activation["member_digests"]["/etc/happyranch/invite-issuer.json"] = hashlib.sha256(
        (fixture.root / "etc/happyranch/invite-issuer.json").read_bytes()).hexdigest()
    fixture.put_json(ACTIVATION, fixture.activation)
    for path in MANAGED_OPAQUE:
        fixture.file(path, b"synthetic-opaque-state-must-not-open", 0o644 if path.endswith("/health.json") else 0o600)
    # Local work/native sentinels must not even be opened by production inventory.
    fixture.file("home/release/.local/share/happyranch/daemon.token", b"synthetic-release-home-token")
    fixture.file("retained-native/saved-home", b"synthetic-P25-saved-home")
    for path in MANAGED_DIRS:
        fixture.directory(path).chmod(0o700)
    for path in ("etc/happyranch/headscale", "var/lib/happyranch-headscale", "run/happyranch-headscale", PRIVATE):
        (fixture.root / path).chmod(0o700)


def managed_refusal(fixture: InventoryFixture, ownership, fence) -> None:
    before = fixture.snapshot()
    with pytest.raises(RetirementUncertain, match="^migration pending$"):
        fixture.inventory.managed_members(ownership, fence=fence)
    assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize("live", [False, True])
def test_managed_lineage_preserves_expired_provenance_and_never_reads_opaque_state_or_source_token(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, live: bool) -> None:
    managed_setup(fixture, live=live)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        before = fixture.snapshot()
        forbidden = {str(fixture.root / path) for path in (*MANAGED_OPAQUE,
            "home/release/.local/share/happyranch/daemon.token", "retained-native/saved-home")}
        original_open = os.open
        opened = []
        def open_only_fixed(path, *args, **kwargs):
            absolute = str(Path(os.readlink(f"/proc/self/fd/{kwargs['dir_fd']}")) / path) if kwargs.get("dir_fd") is not None else str(path)
            opened.append(absolute)
            assert absolute not in forbidden, "opaque state or release-home secret was opened"
            return original_open(path, *args, **kwargs)
        with monkeypatch.context() as scoped:
            scoped.setattr(inventory.os, "open", open_only_fixed)
            captured = fixture.inventory.managed_members(ownership, fence=fence)
            assert inventory.LegacyInventory(fixture.io).managed_members(ownership, fence=fence) == captured
            fixture.inventory.require_managed(ownership, captured, fence=fence)
        assert captured.identity == ("tenant", "home", "connector") and captured.control_plane_id == "control"
        assert captured.release_uid == os.getuid() and captured.account == (os.getuid(), os.getgid())
        assert captured.live_sidecar is live
        assert captured.members[MANAGED_OPAQUE[0]] is not None
        assert captured.members["run/happyranch-connector/control.sock"] is None
        assert captured.digests["etc/happyranch/sidecar.json"] == hashlib.sha256((fixture.root / "etc/happyranch/sidecar.json").read_bytes()).hexdigest()
        assert repr(captured) == "LegacyManagedMembers(<redacted>)"
        with pytest.raises(TypeError):
            captured.members[MANAGED_OPAQUE[0]] = None
        assert not forbidden.intersection(opened)
        assert fixture.snapshot() == before and fixture.effects() == []


def test_managed_inert_pre_activation_has_no_invite_or_trust_fallback(fixture: InventoryFixture) -> None:
    managed_setup(fixture, live=False)
    fixture.set_unit(home=False)
    for path in (ACTIVATION, LINK, "etc/happyranch/invite-issuer.json", *MANAGED_OPAQUE[:3]):
        (fixture.root / path).unlink()
    fixture.stopped()
    fixture.properties["UnitFileState"] = "disabled"
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        before = fixture.snapshot()
        captured = fixture.inventory.managed_members(ownership, fence=fence)
        assert ownership.activation is None and captured.live_sidecar is False
        assert captured.members["etc/happyranch/invite-issuer.json"] is None
        assert all(captured.members[path] is None for path in MANAGED_OPAQUE[:3])
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize("name", MANAGED_CONFIGS)
@pytest.mark.parametrize("change", ["missing", "mode", "symlink", "hardlink", "fifo", "pending"])
def test_managed_config_custody_never_adopts_or_repairs_a_foreign_leaf(
        fixture: InventoryFixture, name: str, change: str) -> None:
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        path = fixture.root / "etc/happyranch" / name
        if change in ("missing", "symlink", "fifo"):
            path.unlink()
            if change == "symlink":
                path.symlink_to(fixture.root / "retained-native/saved-home")
            elif change == "fifo":
                os.mkfifo(path, 0o600)
        elif change == "mode":
            path.chmod(0o666)
        elif change == "hardlink":
            os.link(path, fixture.root / "external-keeper")
        else:
            path.with_name("." + name + ".pending").write_bytes(b"synthetic-interrupted-publication")
        managed_refusal(fixture, ownership, fence)


@pytest.mark.parametrize("name", MANAGED_CONFIGS[:5])
def test_managed_provenance_digests_are_independent_of_current_candidate(
        fixture: InventoryFixture, name: str) -> None:
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        path = fixture.root / "etc/happyranch/provision.json"
        record = json.loads(path.read_bytes())
        record["digests"][name] = "0" * 64
        path.write_bytes(json.dumps(record).encode())
        managed_refusal(fixture, ownership, fence)


@pytest.mark.parametrize("case", ["generation", "ca", "dial", "url", "home", "extra", "insertion", "no-activation",
    "invite-ca", "invite-release", "release-user", "release-uid", "source-layout", "legacy-provenance", "transaction"])
def test_managed_binding_and_historical_generation_must_agree(
        fixture: InventoryFixture, case: str) -> None:
    managed_setup(fixture)
    if case == "no-activation":
        fixture.set_unit(home=False)
        for path in (ACTIVATION, LINK):
            (fixture.root / path).unlink()
        fixture.stopped()
        fixture.properties["UnitFileState"] = "disabled"
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        folder = fixture.root / "etc/happyranch"
        if case in ("generation", "ca", "dial", "url", "home", "extra", "insertion"):
            sidecar = json.loads((folder / "sidecar.json").read_bytes())
            if case == "generation":
                sidecar["home_binding"]["activation_generation"] = True
            elif case == "ca":
                sidecar["control_tls"]["ca_sha256"] = "f" * 64
            elif case == "dial":
                sidecar["control_tls"]["dial_ip"] = "192.168.1.41"
            elif case == "url":
                sidecar["ControlURL"] = "https://foreign.invalid"
            elif case == "home":
                sidecar["home_binding"]["home_id"] = "foreign"
            elif case == "extra":
                sidecar["private"] = "synthetic-private-error-sentinel"
            (folder / "sidecar.json").write_bytes(json.dumps(sidecar, sort_keys=True).encode())
        elif case.startswith("invite-"):
            path = folder / "invite-issuer.json"
            record = json.loads(path.read_bytes())
            record["ca_der_sha256" if case == "invite-ca" else "release_uid"] = "b" * 64 if case == "invite-ca" else os.getuid() + 1
            path.write_bytes(json.dumps(record).encode())
        elif case.startswith("release-"):
            path = folder / "release-daemon.json"
            record = json.loads(path.read_bytes())
            record["user" if case == "release-user" else "uid"] = "foreign" if case == "release-user" else os.getuid() + 1
            path.write_bytes(json.dumps(record).encode())
            # Keep provenance consistent, exposing the NSS/release binding guard.
            prov = json.loads((folder / "provision.json").read_bytes())
            prov["digests"]["release-daemon.json"] = hashlib.sha256(path.read_bytes()).hexdigest()
            (folder / "provision.json").write_bytes(json.dumps(prov).encode())
        elif case in ("source-layout", "legacy-provenance"):
            record = json.loads((folder / "provision.json").read_bytes())
            if case == "source-layout":
                record["layout"] = "source"
            else:
                record = dict(layout="deb", token_source="legacy", token_sha256="a" * 64, configs=[])
            (folder / "provision.json").write_bytes(json.dumps(record).encode())
        elif case == "transaction":
            (folder / "install-transaction.json").write_bytes(b"synthetic-interrupted-renewal")
        managed_refusal(fixture, ownership, fence)


@pytest.mark.parametrize("directory", MANAGED_DIRS)
@pytest.mark.parametrize("change", ["unknown", "parent-mode", "parent-symlink"])
def test_managed_runtime_requires_fixed_shallow_private_directories(
        fixture: InventoryFixture, directory: str, change: str) -> None:
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        path = fixture.root / directory
        if change == "unknown":
            (path / "foreign-data").write_bytes(b"synthetic-local-work")
        elif change == "parent-mode":
            path.chmod(0o755)
        else:
            original = path.with_name(path.name + "-original")
            path.rename(original)
            path.symlink_to(original)
        managed_refusal(fixture, ownership, fence)


@pytest.mark.parametrize("path", [MANAGED_OPAQUE[0], MANAGED_OPAQUE[3], MANAGED_OPAQUE[-1]])
@pytest.mark.parametrize("change", ["mode", "symlink", "hardlink", "fifo"])
def test_managed_opaque_runtime_is_metadata_only_and_keeps_unsafe_evidence(
        fixture: InventoryFixture, path: str, change: str) -> None:
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        target = fixture.root / path
        if change == "mode":
            target.chmod(0o666)
        elif change == "hardlink":
            os.link(target, fixture.root / "external-keeper")
        else:
            target.unlink()
            if change == "symlink":
                target.symlink_to(fixture.root / "retained-native/saved-home")
            else:
                os.mkfifo(target, 0o600)
        managed_refusal(fixture, ownership, fence)


@pytest.mark.parametrize("mask", range(1, 7))
def test_managed_partial_trust_pair_is_preserved_without_initialization(
        fixture: InventoryFixture, mask: int) -> None:
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        for index, path in enumerate(MANAGED_OPAQUE[:3]):
            if mask & (1 << index):
                (fixture.root / path).unlink()
        managed_refusal(fixture, ownership, fence)


@pytest.mark.parametrize("path", ["etc/happyranch/connector.json", MANAGED_OPAQUE[0], "var/lib/happyranch-tsnet-sidecar"])
def test_managed_saved_inventory_refuses_same_bytes_new_identity_or_disappearance(
        fixture: InventoryFixture, path: str) -> None:
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        captured = fixture.inventory.managed_members(ownership, fence=fence)
        target = fixture.root / path
        original = fixture.root / "retained-original-managed-member"
        target.rename(original)
        if not original.is_dir():
            target.write_bytes(original.read_bytes())
            target.chmod(stat.S_IMODE(original.stat().st_mode))
        before = fixture.snapshot()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            fixture.inventory.require_managed(ownership, captured, fence=fence)
        assert fixture.snapshot() == before and fixture.effects() == []


def test_managed_config_replaced_during_validation_cannot_be_recaptured_as_original(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        validate = fixture.io._validate_configs
        replaced = []
        def swap(*args, **kwargs):
            validate(*args, **kwargs)
            if not replaced:
                target = fixture.root / "etc/happyranch/connector.json"
                original = target.with_name("original-connector")
                target.rename(original)
                target.write_bytes(original.read_bytes())
                target.chmod(0o640)
                replaced.append(True)
        monkeypatch.setattr(fixture.io, "_validate_configs", swap)
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            fixture.inventory.managed_members(ownership, fence=fence)
        assert replaced == [True] and fixture.effects() == []


def test_managed_reader_requires_its_own_live_fence(fixture: InventoryFixture) -> None:
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        first = fixture.inventory.managed_members(ownership, fence=fence)
    before = fixture.snapshot()
    with pytest.raises(RetirementUncertain, match="^migration pending$"):
        fixture.inventory.require_managed(ownership, first, fence=fence)
    assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize("case", ["managed-user", "managed-gid", "managed-group", "invite-malformed"])
def test_managed_native_account_and_invite_errors_stay_closed(
        fixture: InventoryFixture, case: str) -> None:
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        if case == "invite-malformed":
            (fixture.root / "etc/happyranch/invite-issuer.json").write_bytes(b"{}")
        elif case == "managed-user":
            lookup = fixture.io._lookup_user
            def missing(name: str):
                if name == "happyranch":
                    raise KeyError(name)
                return lookup(name)
            fixture.io._lookup_user = missing
        else:
            lookup = fixture.io._lookup_group
            fixture.io._lookup_group = lambda name: (SimpleNamespace(gr_name="foreign" if case == "managed-group" else name,
                gr_gid=os.getgid() + 1 if case == "managed-gid" else os.getgid(), gr_mem=[])
                if name == "happyranch" else lookup(name))
        managed_refusal(fixture, ownership, fence)


def test_managed_socket_inventory_never_connects_to_or_opens_observer_or_ingress(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    managed_setup(fixture)
    sockets = []
    try:
        for directory, name in (("run/happyranch-tsnet-sidecar", "observe.sock"),
                ("run/happyranch-connector", "control.sock"), ("run/happyranch-connector", "ingress.sock")):
            descriptor = os.open(fixture.root / directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                child = socket.socket(socket.AF_UNIX)
                child.bind(f"/proc/self/fd/{descriptor}/{name}")
                sockets.append(child)
                (fixture.root / directory / name).chmod(0o600)
            finally:
                os.close(descriptor)
        with retirement_fence(fixture.io) as fence:
            ownership = fixture.inventory.observe(fence=fence)
            before = fixture.snapshot()
            def forbidden(*args, **kwargs):
                pytest.fail("read-only managed inventory connected to a socket")
            monkeypatch.setattr(socket.socket, "connect", forbidden)
            captured = fixture.inventory.managed_members(ownership, fence=fence)
            assert all(captured.members[relative] is not None for relative in (
                "run/happyranch-connector/control.sock", "run/happyranch-connector/ingress.sock",
                "run/happyranch-tsnet-sidecar/observe.sock"))
            assert fixture.snapshot() == before and fixture.effects() == []
    finally:
        for child in sockets:
            child.close()


@pytest.mark.parametrize("case", ["valid-diagnostic", "diagnostic-count", "diagnostic-size", "unknown-diagnostic"])
def test_managed_runtime_diagnostics_have_bounded_metadata_only_rules(
        fixture: InventoryFixture, case: str) -> None:
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        folder = fixture.root / "run/happyranch-tsnet-sidecar"
        for index in range(8 if case == "diagnostic-count" else 1):
            path = folder / ("foreign-health" if case == "unknown-diagnostic" else ".health-" + str(index))
            path.write_bytes(b"x" * (65537 if case == "diagnostic-size" else 16))
            path.chmod(0o600)
        if case == "valid-diagnostic":
            before = fixture.snapshot()
            observed = fixture.inventory.managed_members(ownership, fence=fence)
            assert observed.members["run/happyranch-tsnet-sidecar/.health-0"] is not None
            assert fixture.snapshot() == before and fixture.effects() == []
        else:
            managed_refusal(fixture, ownership, fence)


@pytest.mark.parametrize("path", ["etc/happyranch/connector.json", MANAGED_OPAQUE[0], MANAGED_OPAQUE[-1]])
@pytest.mark.parametrize("field", [4, 5])
def test_managed_numeric_leaf_owner_is_observed_before_any_content_open(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, path: str, field: int) -> None:
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        original_stat, original_open = os.stat, os.open
        target = fixture.root / path
        seen, opened = [], []
        def different_owner(name, *args, **kwargs):
            result = original_stat(name, *args, **kwargs)
            actual = Path(os.readlink(f"/proc/self/fd/{kwargs['dir_fd']}")) / name if kwargs.get("dir_fd") is not None else Path(name)
            if actual == target:
                parts = list(result)
                parts[field] += 1
                result = os.stat_result(parts)
                seen.append(True)
            return result
        def observe_open(name, *args, **kwargs):
            actual = Path(os.readlink(f"/proc/self/fd/{kwargs['dir_fd']}")) / name if kwargs.get("dir_fd") is not None else Path(name)
            opened.append(actual)
            return original_open(name, *args, **kwargs)
        before = fixture.snapshot()
        with monkeypatch.context() as scoped:
            scoped.setattr(inventory.os, "stat", different_owner)
            scoped.setattr(inventory.os, "open", observe_open)
            with pytest.raises(RetirementUncertain, match="^migration pending$"):
                fixture.inventory.managed_members(ownership, fence=fence)
        assert seen and target not in opened and fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize("path", ["etc/happyranch/connector.json", MANAGED_OPAQUE[0]])
def test_managed_original_snapshot_is_rechecked_after_blocking_native_observations(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        validated, replaced = [], []
        original_validate = fixture.io._validate_configs
        def validated_original(*args, **kwargs):
            original_validate(*args, **kwargs)
            validated.append(True)
        def native_barrier():
            if validated and not replaced:
                target = fixture.root / path
                original = fixture.root / "retained-original-during-native-observation"
                target.rename(original)
                target.write_bytes(original.read_bytes())
                target.chmod(stat.S_IMODE(original.stat().st_mode))
                replaced.append(True)
        monkeypatch.setattr(fixture.io, "_validate_configs", validated_original)
        fixture.on_getent = native_barrier
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            fixture.inventory.managed_members(ownership, fence=fence)
        assert validated and replaced == [True] and fixture.effects() == []


JOURNAL = STATE + "/home-retirement-inventory.json"


@pytest.mark.parametrize("live", [False, True])
def test_original_inventory_is_durable_closed_and_reopens_without_adopting_current_members(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, live: bool) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    managed_setup(fixture, live=live)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        before = fixture.snapshot()
        syncs, real_sync = [], os.fsync
        def sync(fd):
            syncs.append(os.readlink(f"/proc/self/fd/{fd}"))
            real_sync(fd)
        monkeypatch.setattr(os, "fsync", sync)
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(ownership)
        target = fixture.root / JOURNAL
        assert str(target) in syncs and str(target.parent) in syncs
        assert syncs.index(str(target)) < syncs.index(str(target.parent))
        record = json.loads(target.read_bytes())
        assert record.keys() == {"format", "directory", "original"}
        assert record["format"] == "home-retirement-inventory@1"
        assert record["directory"] == [getattr(os.stat(target.parent), key)
            for key in ("st_dev", "st_ino", "st_uid", "st_gid", "st_mode")]
        assert record["original"]["service"]["unit"] == "happyranch-headscale.service"
        assert record["original"]["managed"]["identity"] == ["tenant", "home", "connector"]
        assert record["original"]["managed"]["live_sidecar"] is live
        assert set(record["original"]["managed"]["members"]) >= set(MANAGED_OPAQUE)
        assert set(record["original"]["private"]["members"]) >= {CONFIG, OPAQUE, PRIVATE_KEY}
        assert (target.stat().st_uid, target.stat().st_gid, stat.S_IMODE(target.stat().st_mode), target.stat().st_nlink
            ) == (os.getuid(), os.getgid(), 0o600, 1)
        assert all(secret not in target.read_bytes() for secret in (b"synthetic-copied-daemon-token",
            b"synthetic-opaque-state", b"synthetic-release-home-token", b"synthetic-P25-saved-home"))
        after = fixture.snapshot()
        assert {path for path in after if before.get(path) != after[path]} == {JOURNAL, STATE}
        assert fixture.effects() == [] and repr(original) == "OriginalInventory(<redacted>)"
        journal.require_original_members(original)
        # Recovery reads the saved original even after interrupted cleanup or a
        # same-byte replacement. It never recaptures these current candidates.
        member = fixture.root / OPAQUE
        signature = original.private.members[OPAQUE]
        member.rename(fixture.root / "preserved-original-db")
        member.write_bytes(b"foreign-replacement")
        member.chmod(0o600)
        (fixture.root / PRIVATE_KEY).unlink()
        for name in ("observe", "private_members", "managed_members"):
            monkeypatch.setattr(inventory.LegacyInventory, name, lambda *args, **kwargs: pytest.fail("load recaptured current state"))
        before = fixture.snapshot()
        reopened = RetirementJournal(fixture.io, fence).load()
        assert reopened.private.members[OPAQUE] == signature
        assert reopened.private.members[PRIVATE_KEY] is not None
        assert reopened == original and fixture.snapshot() == before and fixture.effects() == []
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            journal.require_original_members(reopened)
        with pytest.raises(TypeError):
            reopened.managed.members[MANAGED_OPAQUE[0]] = None


@pytest.mark.parametrize("activity", ["outer-sibling", "inner-sibling", "outer-mode", "outer-replacement"])
@pytest.mark.parametrize("boundary", ["inventory-walk", "ancestor-open"])
def test_original_inventory_distinguishes_outer_activity_from_owned_directory_drift(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, activity: str, boundary: str) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        real_open, changed = os.open, []
        outer_mode = stat.S_IMODE(fixture.root.parent.stat().st_mode)
        sibling = (fixture.root.parent if activity.startswith("outer-") else fixture.root) / "unrelated-sibling"
        trigger = (fixture.root.parent if boundary == "ancestor-open" and activity.startswith("outer-")
                   else fixture.root / "var/lib/happyranch-headscale")
        def open_during_activity(path, flags, *args, **kwargs):
            nonlocal sibling
            descriptor = real_open(path, flags, *args, **kwargs)
            if (not changed and flags == inventory._DIRECTORY_FLAGS
                    and Path(os.readlink(f"/proc/self/fd/{descriptor}")) == trigger):
                # A deterministic external writer during a real inventory walk.
                # It supplies no decision/receipt and never changes production.
                sibling.write_bytes(b"synthetic-independent-sibling")
                if activity == "outer-mode":
                    fixture.root.parent.chmod(0o755)
                elif activity == "outer-replacement":
                    parent = fixture.root.parent
                    retained = parent.with_name(parent.name + "-retained-original")
                    parent.rename(retained)
                    parent.mkdir(mode=0o700)
                    (retained / fixture.root.name).rename(fixture.root)
                    sibling = retained / sibling.name
                changed.append(True)
            return descriptor
        with monkeypatch.context() as scoped:
            scoped.setattr(inventory.os, "open", open_during_activity)
            if activity == "outer-sibling":
                saved = RetirementJournal(fixture.io, fence).capture(ownership)
                assert saved.private.members[OPAQUE] is not None
                assert RetirementJournal(fixture.io, fence).load() == saved
            else:
                try:
                    with pytest.raises(RetirementUncertain, match="^migration pending$"):
                        RetirementJournal(fixture.io, fence).capture(ownership)
                finally:
                    # Restore only the injected fixture ancestor before the
                    # independent lifetime-lock revalidation on context exit.
                    if activity == "outer-mode":
                        fixture.root.parent.chmod(outer_mode)
                    elif activity == "outer-replacement":
                        parent = fixture.root.parent
                        retained = parent.with_name(parent.name + "-retained-original")
                        fixture.root.rename(retained / fixture.root.name)
                        parent.rmdir()
                        retained.rename(parent)
                        sibling = parent / sibling.name
        assert changed == [True] and sibling.read_bytes() == b"synthetic-independent-sibling"
        assert fixture.effects() == []
        assert (fixture.root / "retained-native/saved-home").read_bytes() == b"synthetic-P25-saved-home"


@pytest.mark.parametrize("damage", ["mode", "hardlink", "symlink", "fifo", "empty", "oversized", "torn",
    "duplicate", "format", "directory", "extra", "path", "parent", "missing-managed", "missing-private",
    "digest", "signature", "signature-bool", "account", "release", "identity", "activation", "native", "receipts"])
def test_unknown_original_inventory_is_preserved_without_effects_or_recapture(
        fixture: InventoryFixture, damage: str) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        try:
            RetirementJournal(fixture.io, fence).capture(fixture.inventory.observe(fence=fence))
        except RetirementUncertain as error:
            # Preserve closed diagnostics while making an unexpected initial
            # capture failure attributable, including suppressed inner frames.
            import traceback
            frames = []
            current = error
            while current is not None:
                frames.extend((Path(frame.filename).name, frame.name, frame.lineno)
                    for frame in traceback.extract_tb(current.__traceback__))
                current = current.__context__
            pytest.fail(f"valid initial inventory capture refused: {frames}")
        target = fixture.root / JOURNAL
        record = json.loads(target.read_bytes())
        original = record["original"]
        if damage == "mode":
            target.chmod(0o644)
        elif damage == "hardlink":
            os.link(target, fixture.root / "external-journal")
        elif damage in ("symlink", "fifo"):
            target.rename(fixture.root / "external-journal")
            if damage == "symlink":
                target.symlink_to(fixture.root / "external-journal")
            else:
                os.mkfifo(target, 0o600)
        elif damage in ("empty", "oversized", "torn", "duplicate"):
            target.write_bytes({"empty": b"", "oversized": b"x" * (1024 * 1024 + 1), "torn": b'{"format":',
                "duplicate": target.read_bytes().replace(b'"format":', b'"format":"home-retirement-inventory@1","format":', 1)}[damage])
        else:
            if damage == "format": record["format"] = "home-retirement-inventory@2"
            elif damage == "directory": record["directory"][1] += 1
            elif damage == "extra": record["completion"] = True
            elif damage == "path": original["private"]["members"]["home/release/task.db"] = original["private"]["members"][OPAQUE]
            elif damage == "parent": original["managed"]["directories"]["home/release"] = original["managed"]["directories"]["etc"]
            elif damage == "missing-managed": del original["managed"]["members"][MANAGED_OPAQUE[0]]
            elif damage == "missing-private": del original["private"]["members"][PRIVATE_KEY]
            elif damage == "digest": original["managed"]["digests"]["etc/happyranch/connector.json"] = "unknown"
            elif damage == "signature": original["private"]["members"][OPAQUE] = [1, 2]
            elif damage == "signature-bool": original["private"]["members"][OPAQUE][1] = True
            elif damage == "account": original["managed"]["account"][0] = False
            elif damage == "release": original["managed"]["release_uid"] += 1
            elif damage == "identity": original["managed"]["identity"][2] = "foreign"
            elif damage == "activation": original["activation"]["operation_id"] = "unknown"
            elif damage == "native": original["native"]["properties"].append(["alias", "foreign"])
            elif damage == "receipts": del original["receipt_identities"]["headscale-account.json"]
            target.write_bytes(json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        before = fixture.snapshot()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            RetirementJournal(fixture.io, fence).load()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            RetirementJournal(fixture.io, fence).capture(fixture.inventory.observe(fence=fence))
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize("failure", ["write", "file-sync", "parent-sync"])
def test_interrupted_original_inventory_publication_retains_evidence_and_requires_explicit_sync(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        before = fixture.snapshot()
        write, sync = os.write, os.fsync
        def interrupted_write(fd, data):
            if os.readlink(f"/proc/self/fd/{fd}") == str(fixture.root / JOURNAL) and failure == "write":
                return write(fd, data[:17])
            return write(fd, data)
        def interrupted_sync(fd):
            target = os.readlink(f"/proc/self/fd/{fd}")
            if ((failure == "file-sync" and target == str(fixture.root / JOURNAL))
                    or failure == "parent-sync" and target == str(fixture.root / STATE)):
                raise OSError("synthetic fault containing private input")
            sync(fd)
        with monkeypatch.context() as scoped:
            scoped.setattr(os, "write", interrupted_write)
            scoped.setattr(os, "fsync", interrupted_sync)
            with pytest.raises(RetirementUncertain, match="^migration pending$"):
                RetirementJournal(fixture.io, fence).capture(ownership)
        after = fixture.snapshot()
        assert {path for path in after if before.get(path) != after[path]} == {JOURNAL, STATE}
        assert fixture.effects() == [] and not (fixture.root / STATE / "home-retirement.json").exists()
        if failure == "write":
            with pytest.raises(RetirementUncertain, match="^migration pending$"):
                RetirementJournal(fixture.io, fence).load()
        else:
            syncs = []
            def resync(fd):
                syncs.append(os.readlink(f"/proc/self/fd/{fd}"))
                sync(fd)
            monkeypatch.setattr(os, "fsync", resync)
            restored = RetirementJournal(fixture.io, fence).load()
            assert syncs == [str(fixture.root / JOURNAL), str(fixture.root / STATE)]
            assert restored.managed.identity == ("tenant", "home", "connector")
        assert fixture.snapshot() == after and fixture.effects() == []


@pytest.mark.parametrize("when", ["publish-file", "publish-parent", "reuse-file", "reuse-parent"])
def test_original_journal_inode_stays_pinned_across_sync_and_reuse(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, when: str) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        journal = RetirementJournal(fixture.io, fence)
        if when.startswith("reuse"):
            journal.capture(ownership)
        replaced, sync = [], os.fsync
        def swap(fd):
            sync(fd)
            path = os.readlink(f"/proc/self/fd/{fd}")
            selected = JOURNAL if when.endswith("file") else STATE
            if path == str(fixture.root / selected) and not replaced:
                target = fixture.root / JOURNAL
                target.rename(fixture.root / "preserved-original-journal")
                target.write_bytes((fixture.root / "preserved-original-journal").read_bytes())
                target.chmod(0o600)
                replaced.append(True)
        monkeypatch.setattr(os, "fsync", swap)
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            (journal.load if when.startswith("reuse") else lambda: journal.capture(ownership))()
        assert replaced == [True] and fixture.effects() == []


@pytest.mark.parametrize("path", [OPAQUE, PRIVATE_KEY, "etc/happyranch/connector.json", MANAGED_OPAQUE[0], ACCOUNT_RECORD])
def test_original_journal_publication_never_relabels_drift_as_its_own_directory_write(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        changed, sync = [], os.fsync
        def drift(fd):
            sync(fd)
            if os.readlink(f"/proc/self/fd/{fd}") == str(fixture.root / STATE) and not changed:
                target = fixture.root / path
                target.write_bytes(target.read_bytes() + b"foreign-drift")
                changed.append(True)
        monkeypatch.setattr(os, "fsync", drift)
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            RetirementJournal(fixture.io, fence).capture(ownership)
        assert changed == [True] and fixture.effects() == []


def test_original_journal_requires_same_live_serialization_domain(fixture: InventoryFixture) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    managed_setup(fixture)
    other = Fixture(fixture.root.parent / "foreign-root")
    other.directory(STATE)
    with retirement_fence(fixture.io) as fence, retirement_fence(other.io) as foreign:
        ownership = fixture.inventory.observe(fence=fence)
        before = fixture.snapshot(), other.snapshot()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            RetirementJournal(fixture.io, foreign).capture(ownership)
        assert (fixture.snapshot(), other.snapshot()) == before
        journal = RetirementJournal(fixture.io, fence)
        journal.capture(ownership)
    before = fixture.snapshot()
    with pytest.raises(RetirementUncertain, match="^migration pending$"):
        journal.load()
    assert fixture.snapshot() == before and fixture.effects() == []


ISSUER = STATE + "/home-issuer"
ISSUER_RECEIPT = STATE + "/home-issuer-publication.json"
MINTER = STATE + "/home-minter"
OPERATION = MINTER + "/happyranch-invite/operation-" + "1" * 64
TRANSACTION = STATE + "/home-activation-txn.json"


def auxiliary_setup(fixture: InventoryFixture) -> None:
    managed_setup(fixture)
    auxiliary_journal_setup(fixture)


def auxiliary_journal_setup(fixture: InventoryFixture) -> None:
    folder = fixture.directory(ISSUER)
    fixture.put_json(ISSUER_RECEIPT, dict(version=1, directory="/" + ISSUER,
        uid=os.getuid(), gid=os.getgid(), device=folder.stat().st_dev, inode=folder.stat().st_ino))
    fixture.put_json(ISSUER + "/invite-issuer.json", dict(operation_id="b" * 32, binding_digest="a" * 64,
        not_before=100.0, expires_at=399.0, outcome="uncertain"))
    fixture.file(ISSUER + "/invite-issuer.lock", b"")
    for user in ("happyranch-invite", "happyranch-node"):
        fixture.file(MINTER + "/" + user + "/operation-" + "1" * 64,
            b"2026-01-01T00:00:00+00:00\n2026-01-01T00:04:00+00:00\n")
    fixture.put_json(TRANSACTION, dict(format="home-activation-txn@1",
        **{key: fixture.activation[key] for key in ("release_uid", "home_id", "control_plane_id", "connector_id",
            "activation_generation", "operation_id", "ca_der_sha256", "dial_ip")},
        phase="failed_uncertain", effects=[dict(name="preflight", state="intent", member_digests={})]))
    for relative in (ISSUER, MINTER, MINTER + "/happyranch-invite", MINTER + "/happyranch-node"):
        (fixture.root / relative).chmod(0o700)


def test_auxiliary_journals_are_observed_without_minting_recovery_or_private_data_access(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    auxiliary_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        before = fixture.snapshot()
        real_open, opened = os.open, []
        forbidden = {str(fixture.root / path) for path in (OPAQUE, PRIVATE_KEY, *MANAGED_OPAQUE,
            "home/release/.local/share/happyranch/daemon.token")}
        def bounded_open(name, *args, **kwargs):
            candidate = str(Path(os.readlink(f"/proc/self/fd/{kwargs['dir_fd']}")) / name) if kwargs.get("dir_fd") is not None else str(name)
            opened.append(candidate)
            assert candidate not in forbidden
            return real_open(name, *args, **kwargs)
        with monkeypatch.context() as scoped:
            scoped.setattr(os, "open", bounded_open)
            observed = fixture.inventory.auxiliary_members(ownership, fence=fence)
            fixture.inventory.require_auxiliary(ownership, observed, fence=fence)
        assert {ISSUER, ISSUER_RECEIPT, OPERATION, TRANSACTION} <= observed.members.keys()
        assert observed.members[OPERATION][1] == (fixture.root / OPERATION).stat().st_ino
        assert repr(observed) == "LegacyAuxiliaryMembers(<redacted>)"
        assert fixture.snapshot() == before and fixture.effects() == []
        saved = RetirementJournal(fixture.io, fence).capture(ownership)
        assert saved.auxiliary == observed
        (fixture.root / OPERATION).unlink()
        before = fixture.snapshot()
        assert RetirementJournal(fixture.io, fence).load().auxiliary == observed
        assert fixture.snapshot() == before and fixture.effects() == []


def test_absent_auxiliary_state_never_initializes_or_claims_cleanup(fixture: InventoryFixture) -> None:
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        before = fixture.snapshot()
        observed = fixture.inventory.auxiliary_members(ownership, fence=fence)
        assert all(value is None for value in observed.members.values())
        assert len(observed.members) == 8
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize("damage", ["receipt-missing", "receipt-inode", "receipt-mode", "issuer-unknown",
    "issuer-pending", "issuer-partial", "issuer-malformed", "issuer-lock-mode", "minter-name", "minter-mode",
    "operation-symlink", "operation-hardlink", "operation-fifo", "operation-mode", "operation-empty",
    "operation-large", "operation-malformed", "operation-timezone", "operation-window", "operation-name", "issuer-unreceipted-empty", "transaction-binding",
    "transaction-malformed", "transaction-pending", "transaction-mode"])
def test_unknown_auxiliary_evidence_is_preserved_without_effects(
        fixture: InventoryFixture, damage: str) -> None:
    auxiliary_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        if damage.startswith("receipt-"):
            target = fixture.root / ISSUER_RECEIPT
            if damage == "receipt-missing": target.unlink()
            elif damage == "receipt-mode": target.chmod(0o644)
            else:
                record = json.loads(target.read_bytes())
                record["inode"] += 1
                target.write_bytes(json.dumps(record).encode())
        elif damage.startswith("issuer-"):
            target = fixture.root / ISSUER
            if damage == "issuer-unreceipted-empty":
                for name in ("invite-issuer.json", "invite-issuer.lock"):
                    (target / name).unlink()
                (fixture.root / ISSUER_RECEIPT).unlink()
            elif damage in ("issuer-unknown", "issuer-pending"):
                (target / ("foreign" if damage == "issuer-unknown" else "invite-issuer.pending")).write_bytes(b"synthetic-private-state")
            elif damage == "issuer-partial": (target / "invite-issuer.lock").unlink()
            elif damage == "issuer-lock-mode": (target / "invite-issuer.lock").chmod(0o644)
            else: (target / "invite-issuer.json").write_bytes(b"{unknown}")
        elif damage.startswith("minter-"):
            if damage == "minter-name": (fixture.root / MINTER / "foreign-user").mkdir(mode=0o700)
            else: (fixture.root / MINTER).chmod(0o755)
        elif damage.startswith("transaction-"):
            target = fixture.root / TRANSACTION
            if damage == "transaction-binding":
                record = json.loads(target.read_bytes())
                record["operation_id"] = "f" * 32
                target.write_bytes(json.dumps(record).encode())
            elif damage == "transaction-mode": target.chmod(0o644)
            elif damage == "transaction-pending": target.with_name("." + target.name + ".pending").write_bytes(b"synthetic-pending")
            else: target.write_bytes(b"{unknown}")
        else:
            target = fixture.root / OPERATION
            if damage in ("operation-symlink", "operation-fifo"):
                target.unlink()
                if damage == "operation-symlink": target.symlink_to(fixture.root / "retained-native/saved-home")
                else: os.mkfifo(target, 0o600)
            elif damage == "operation-name": target.rename(target.with_name("operation-unknown"))
            elif damage == "operation-hardlink": os.link(target, fixture.root / "external-operation")
            elif damage == "operation-mode": target.chmod(0o644)
            else:
                target.write_bytes({"operation-empty": b"", "operation-large": b"x" * 161,
                    "operation-malformed": b"unknown\nunknown\n",
                    "operation-timezone": b"2026-01-01T00:00:00\n2026-01-01T00:04:00\n",
                    "operation-window": b"2026-01-01T00:00:00+00:00\n2026-01-01T00:06:00+00:00\n"}[damage])
        before = fixture.snapshot()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            fixture.inventory.auxiliary_members(ownership, fence=fence)
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize("relative", [ISSUER + "/invite-issuer.lock", MINTER + "/happyranch-invite", MINTER + "/happyranch-node"])
def test_auxiliary_live_operation_lock_refuses_without_cleanup_or_repair(
        fixture: InventoryFixture, relative: str) -> None:
    import fcntl
    auxiliary_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        descriptor = os.open(fixture.root / relative, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            before = fixture.snapshot()
            with pytest.raises(RetirementUncertain, match="^migration pending$"):
                fixture.inventory.auxiliary_members(ownership, fence=fence)
            assert fixture.snapshot() == before and fixture.effects() == []
        finally:
            os.close(descriptor)
        observed = fixture.inventory.auxiliary_members(ownership, fence=fence)
        assert observed.members[OPERATION] is not None


@pytest.mark.parametrize("relative", [OPERATION, ISSUER_RECEIPT, TRANSACTION])
def test_auxiliary_saved_original_inode_is_never_recaptured_as_current(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, relative: str) -> None:
    auxiliary_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        observed = fixture.inventory.auxiliary_members(ownership, fence=fence)
        target = fixture.root / relative
        target.rename(fixture.root / "preserved-original-auxiliary")
        target.write_bytes((fixture.root / "preserved-original-auxiliary").read_bytes())
        target.chmod(0o600)
        before = fixture.snapshot()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            fixture.inventory.require_auxiliary(ownership, observed, fence=fence)
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize("relative", [OPERATION, ISSUER_RECEIPT, TRANSACTION])
def test_auxiliary_original_snapshot_is_rechecked_after_blocking_native_observations(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, relative: str) -> None:
    auxiliary_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        read, replaced = [], []
        operation = inventory._AuxiliaryMembers._operation
        def original_operation(*args, **kwargs):
            result = operation(*args, **kwargs)
            read.append(True)
            return result
        def native_barrier():
            if read and not replaced:
                target = fixture.root / relative
                saved = fixture.root.parent / "retained-original-auxiliary"
                target.rename(saved)
                target.write_bytes(saved.read_bytes())
                target.chmod(0o600)
                replaced.append(True)
        monkeypatch.setattr(inventory._AuxiliaryMembers, "_operation", original_operation)
        fixture.on_getent = native_barrier
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            fixture.inventory.auxiliary_members(ownership, fence=fence)
        assert read and replaced == [True] and fixture.effects() == []


def test_auxiliary_operation_budget_refuses_without_partial_effects(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    auxiliary_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        before = fixture.snapshot()
        monkeypatch.setattr(inventory._AuxiliaryMembers, "_MAX_OPERATIONS", 1)
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            fixture.inventory.auxiliary_members(ownership, fence=fence)
        assert fixture.snapshot() == before and fixture.effects() == []


def test_auxiliary_journal_preserves_a_populated_original_operation_inventory(fixture: InventoryFixture) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    auxiliary_setup(fixture)
    for index in range(130):
        fixture.file(MINTER + "/happyranch-invite/operation-" + f"{index + 2:064x}",
            b"2026-01-01T00:00:00+00:00\n2026-01-01T00:04:00+00:00\n")
    for relative in (MINTER, MINTER + "/happyranch-invite", MINTER + "/happyranch-node"):
        (fixture.root / relative).chmod(0o700)
    with retirement_fence(fixture.io) as fence:
        ownership = fixture.inventory.observe(fence=fence)
        try:
            original = RetirementJournal(fixture.io, fence).capture(ownership)
        except RetirementUncertain:
            pytest.fail("valid populated original operation inventory was refused")
        assert len([path for path in original.auxiliary.members if "/operation-" in path]) == 132
        assert 32768 < (fixture.root / JOURNAL).stat().st_size <= 1024 * 1024
        assert RetirementJournal(fixture.io, fence).load().auxiliary == original.auxiliary
        assert fixture.effects() == []


OPERATION_LOCKS = (ISSUER + '/invite-issuer.lock', MINTER + '/happyranch-invite', MINTER + '/happyranch-node')


def writer_lock(root: Path, relative: str) -> int:
    """A fresh process opens the same shipping serialization inode."""
    import subprocess
    import sys
    return subprocess.run([sys.executable, '-c', '''
import fcntl, os, sys
descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW)
try:
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(73)
finally:
    os.close(descriptor)
''', str(root / relative)], capture_output=True).returncode


@pytest.mark.parametrize('relative', OPERATION_LOCKS)
def test_held_auxiliary_exclusion_blocks_real_writer_until_context_exit(
        fixture: InventoryFixture, relative: str) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    from runtime.remote_access.home_retirement_operations import held_auxiliary_operations
    auxiliary_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        before = fixture.snapshot()
        assert writer_lock(fixture.root, relative) == 0
        with held_auxiliary_operations(journal, original) as exclusion:
            assert repr(exclusion) == 'AuxiliaryExclusion(<redacted>)'
            exclusion.require()
            assert writer_lock(fixture.root, relative) == 73
            # Revalidation must neither reopen/release the actual writer lock
            # nor lose it while another named domain is being checked.
            exclusion.require()
            assert writer_lock(fixture.root, relative) == 73
        assert writer_lock(fixture.root, relative) == 0
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            exclusion.require()
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize('relative', OPERATION_LOCKS)
def test_held_auxiliary_contention_releases_earlier_locks_without_effects(
        fixture: InventoryFixture, relative: str) -> None:
    import fcntl
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    from runtime.remote_access.home_retirement_operations import held_auxiliary_operations
    auxiliary_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        before = fixture.snapshot()
        descriptor = os.open(fixture.root / relative, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                with held_auxiliary_operations(journal, original):
                    pytest.fail('contended original must not enter caller operation')
            for candidate in OPERATION_LOCKS:
                assert writer_lock(fixture.root, candidate) == (73 if candidate == relative else 0)
        finally:
            os.close(descriptor)
        for candidate in OPERATION_LOCKS:
            assert writer_lock(fixture.root, candidate) == 0
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize('relative', [OPERATION, ISSUER_RECEIPT, TRANSACTION, *OPERATION_LOCKS])
@pytest.mark.parametrize('phase', ['before-acquire', 'held'])
def test_held_auxiliary_never_adopts_replaced_original_evidence(
        fixture: InventoryFixture, relative: str, phase: str) -> None:
    import shutil
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    from runtime.remote_access.home_retirement_operations import held_auxiliary_operations
    auxiliary_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        def replace_original() -> dict:
            target = fixture.root / relative
            saved = fixture.root.parent / 'original-auxiliary-member'
            target.rename(saved)
            if saved.is_dir():
                shutil.copytree(saved, target)
            else:
                shutil.copy2(saved, target)
            return fixture.snapshot()
        if phase == 'before-acquire':
            before = replace_original()
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                with held_auxiliary_operations(journal, original):
                    pytest.fail('replacement must not acquire original exclusion')
        else:
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                with held_auxiliary_operations(journal, original):
                    before = replace_original()
                    # Context exit itself must check even without caller require.
        assert fixture.snapshot() == before and fixture.effects() == []


def test_held_auxiliary_refuses_replacement_during_lock_acquisition(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    from runtime.remote_access.home_retirement_operations import held_auxiliary_operations
    auxiliary_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        real_flock, replaced = fcntl.flock, []
        def flock(descriptor, operation):
            result = real_flock(descriptor, operation)
            if not replaced and Path(os.readlink(f'/proc/self/fd/{descriptor}')) == fixture.root / OPERATION_LOCKS[0]:
                target = fixture.root / OPERATION_LOCKS[0]
                target.rename(fixture.root.parent / 'original-held-issuer-lock')
                fixture.file(OPERATION_LOCKS[0], b'')
                replaced.append(fixture.snapshot())
            return result
        with monkeypatch.context() as scoped:
            scoped.setattr(fcntl, 'flock', flock)
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                with held_auxiliary_operations(journal, original):
                    pytest.fail('a lock on an unlinked original is not named exclusion')
        assert replaced and fixture.snapshot() == replaced[0] and fixture.effects() == []
        assert all(writer_lock(fixture.root, path) == 0 for path in OPERATION_LOCKS)


@pytest.mark.parametrize('body_failure', [False, True])
def test_held_auxiliary_context_releases_all_descriptions_on_caller_failure(
        fixture: InventoryFixture, body_failure: bool) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    from runtime.remote_access.home_retirement_operations import held_auxiliary_operations
    auxiliary_setup(fixture)
    class CallerFailure(Exception):
        pass
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        before = fixture.snapshot()
        def operation():
            with held_auxiliary_operations(journal, original) as exclusion:
                assert all(writer_lock(fixture.root, path) == 73 for path in OPERATION_LOCKS)
                if body_failure:
                    raise CallerFailure()
                return exclusion
        if body_failure:
            with pytest.raises(CallerFailure):
                operation()
        else:
            operation()
        assert all(writer_lock(fixture.root, path) == 0 for path in OPERATION_LOCKS)
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize('issuer_empty', [False, True])
def test_held_auxiliary_never_initializes_an_absent_lock_domain(
        fixture: InventoryFixture, issuer_empty: bool) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    from runtime.remote_access.home_retirement_operations import held_auxiliary_operations
    if issuer_empty:
        auxiliary_setup(fixture)
        for name in ('invite-issuer.json', 'invite-issuer.lock'):
            (fixture.root / ISSUER / name).unlink()
    else:
        managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        before = fixture.snapshot()
        if issuer_empty:
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                with held_auxiliary_operations(journal, original):
                    pytest.fail('empty issuer has no original lock to exclude an old writer')
        else:
            with held_auxiliary_operations(journal, original) as exclusion:
                exclusion.require()
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize('damage', ['record', 'expired-fence'])
def test_held_auxiliary_requires_unchanged_saved_record_and_live_fence(
        fixture: InventoryFixture, damage: str) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    from runtime.remote_access.home_retirement_operations import held_auxiliary_operations
    auxiliary_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        if damage == 'record':
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                with held_auxiliary_operations(journal, original) as exclusion:
                    record = fixture.root / STATE / 'home-retirement-inventory.json'
                    data = record.read_bytes()
                    record.rename(fixture.root.parent / 'original-inventory-record')
                    fixture.file(STATE + '/home-retirement-inventory.json', data)
                    before = fixture.snapshot()
                    with pytest.raises(RetirementUncertain, match='^migration pending$'):
                        exclusion.require()
                    with pytest.raises(RetirementUncertain, match='^migration pending$'):
                        with held_auxiliary_operations(journal, original):
                            pytest.fail('replacement saved record must not enter')
            assert fixture.snapshot() == before and fixture.effects() == []
            assert all(writer_lock(fixture.root, path) == 0 for path in OPERATION_LOCKS)
    if damage == 'expired-fence':
        before = fixture.snapshot()
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            with held_auxiliary_operations(journal, original):
                pytest.fail('closed fence must not acquire exclusion')
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize('state', ['complete', 'absent-leaf', 'interrupted'])
def test_auxiliary_cleanup_removes_original_nonlock_leaves_and_reopens(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    from runtime.remote_access.home_retirement_operations import held_auxiliary_operations
    auxiliary_setup(fixture)
    fixture.file('local-work/keep', b'synthetic-local-work')
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        leaves = {path for path, signature in original.auxiliary.members.items()
                  if signature is not None and stat.S_ISREG(signature[4]) and path not in OPERATION_LOCKS}
        if state == 'absent-leaf':
            (fixture.root / OPERATION).unlink()
        saved = (fixture.root / STATE / 'home-retirement-inventory.json').read_bytes()
        real_unlink, real_fsync, real_open = os.unlink, os.fsync, os.open
        removed, synced, proof_calls, pending_sync = [], [], [], []
        def guarded():
            assert all(writer_lock(fixture.root, path) == 73 for path in OPERATION_LOCKS)
            proof_calls.append(len(removed))
        def unlink(name, *, dir_fd=None):
            path = str((Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name).relative_to(fixture.root))
            assert path in leaves and path not in OPERATION_LOCKS
            real_unlink(name, dir_fd=dir_fd)
            removed.append(path)
            pending_sync.append(str(Path(path).parent))
        def sync(fd):
            real_fsync(fd)
            if pending_sync:
                relative = str(Path(os.readlink(f'/proc/self/fd/{fd}')).relative_to(fixture.root))
                assert relative == pending_sync.pop(0)
                synced.append(relative)
                if state == 'interrupted' and len(removed) == 1:
                    raise OSError('synthetic interrupted parent sync')
        def opened(name, flags, *args, dir_fd=None, **kwargs):
            path = Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name if dir_fd is not None else Path(name)
            assert path not in {fixture.root / leaf for leaf in leaves}, 'opaque auxiliary leaf opened'
            return real_open(name, flags, *args, dir_fd=dir_fd, **kwargs)
        with monkeypatch.context() as scoped:
            scoped.setattr(os, 'unlink', unlink)
            scoped.setattr(os, 'fsync', sync)
            scoped.setattr(os, 'open', opened)
            if state == 'interrupted':
                with pytest.raises(RetirementUncertain, match='^migration pending$'):
                    with held_auxiliary_operations(journal, original, cleanup=True) as exclusion:
                        exclusion.remove_leaves(before_effect=guarded)
                assert len(removed) == 1
            with held_auxiliary_operations(journal, original, cleanup=True) as exclusion:
                exclusion.remove_leaves(before_effect=guarded)
            count = len(removed)
            with held_auxiliary_operations(journal, original, cleanup=True) as exclusion:
                exclusion.remove_leaves(before_effect=guarded)
            assert len(removed) == count and len(removed) == len(set(removed))
        assert set(removed) == leaves - ({OPERATION} if state == 'absent-leaf' else set())
        assert len(synced) == len(removed) and proof_calls
        assert all(not (fixture.root / path).exists() for path in leaves)
        assert all(writer_lock(fixture.root, path) == 0 for path in OPERATION_LOCKS)
        assert (fixture.root / STATE / 'home-retirement-inventory.json').read_bytes() == saved
        assert journal.load() == original
        assert (fixture.root / 'local-work/keep').read_bytes() == b'synthetic-local-work'
        assert not (fixture.root / STATE / 'home-retirement.json').exists() and fixture.effects() == []


@pytest.mark.parametrize('damage', ['leaf', 'lock', 'parent', 'unknown', 'missing-lock', 'missing-domain', 'originally-absent'])
def test_auxiliary_cleanup_refuses_unowned_or_missing_serialization_domains(
        fixture: InventoryFixture, damage: str) -> None:
    import shutil
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    from runtime.remote_access.home_retirement_operations import held_auxiliary_operations
    auxiliary_setup(fixture)
    data = (fixture.root / TRANSACTION).read_bytes()
    if damage == 'originally-absent':
        (fixture.root / TRANSACTION).unlink()
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        if damage in ('leaf', 'lock', 'parent'):
            target = fixture.root / (OPERATION if damage == 'leaf' else OPERATION_LOCKS[0 if damage == 'lock' else 1])
            saved = fixture.root.parent / 'preserved-original'
            target.rename(saved)
            shutil.copytree(saved, target) if saved.is_dir() else shutil.copy2(saved, target)
        elif damage == 'unknown':
            fixture.file(ISSUER + '/unrecognized', b'synthetic-foreign')
        elif damage == 'missing-lock':
            (fixture.root / OPERATION_LOCKS[0]).unlink()
        elif damage == 'missing-domain':
            (fixture.root / OPERATION_LOCKS[1]).rename(fixture.root.parent / 'preserved-original-domain')
        else:
            fixture.file(TRANSACTION, data)
        before = fixture.snapshot()
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            with held_auxiliary_operations(journal, original, cleanup=True) as exclusion:
                exclusion.remove_leaves(before_effect=lambda: None)
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize('failure', ['proof-loss', 'target-replacement'])
def test_auxiliary_cleanup_checks_each_next_effect(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    from runtime.remote_access.home_retirement_operations import held_auxiliary_operations
    auxiliary_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        real_unlink, removed, after = os.unlink, [], []
        def guard():
            if removed and failure == 'proof-loss':
                raise RetirementUncertain()
        def unlink(name, *, dir_fd=None):
            real_unlink(name, dir_fd=dir_fd)
            removed.append(name)
            if len(removed) == 1 and failure == 'target-replacement':
                target = fixture.root / ISSUER / 'invite-issuer.json'
                data = target.read_bytes()
                target.rename(fixture.root.parent / 'preserved-original-leaf')
                fixture.file(ISSUER + '/invite-issuer.json', data)
            after.append(fixture.snapshot())
        with monkeypatch.context() as scoped:
            scoped.setattr(os, 'unlink', unlink)
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                with held_auxiliary_operations(journal, original, cleanup=True) as exclusion:
                    exclusion.remove_leaves(before_effect=guard)
        assert len(removed) == 1 and fixture.snapshot() == after[0] and fixture.effects() == []
        assert all(writer_lock(fixture.root, path) == 0 for path in OPERATION_LOCKS)


@pytest.mark.parametrize('state', ['unchanged', 'runtime-flush', 'missing-original'])
def test_private_cleanup_removes_only_original_leaves_and_reuses_absence(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    from runtime.remote_access.home_retirement_cleanup import PrivateCleanup
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    managed_setup(fixture)
    fixture.file('retained-work/task.db', b'synthetic-local-work')
    sentinel = fixture.root / 'retained-work/task.db'
    retained = sentinel.read_bytes(), sentinel.stat().st_ino
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        if state == 'runtime-flush':
            (fixture.root / OPAQUE).write_bytes(b'synthetic-stop-flush')
        elif state == 'missing-original':
            (fixture.root / PRIVATE_KEY).unlink()
        guards, removed, synced = [], [], []
        real_unlink, real_rmdir, real_sync, real_open = os.unlink, os.rmdir, os.fsync, os.open
        def unlink(name, *, dir_fd=None):
            removed.append(str(Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name))
            real_unlink(name, dir_fd=dir_fd)
        def rmdir(name, *, dir_fd=None):
            removed.append(str(Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name))
            real_rmdir(name, dir_fd=dir_fd)
        def sync(fd):
            synced.append(os.readlink(f'/proc/self/fd/{fd}'))
            real_sync(fd)
        def opaque_open(name, *args, **kwargs):
            assert Path(name).name not in {'ca.key', 'leaf.key', 'db.sqlite', 'noise.key', 'derp.key'}
            return real_open(name, *args, **kwargs)
        with monkeypatch.context() as scoped:
            scoped.setattr(os, 'unlink', unlink)
            scoped.setattr(os, 'rmdir', rmdir)
            scoped.setattr(os, 'fsync', sync)
            scoped.setattr(os, 'open', opaque_open)
            PrivateCleanup(journal, original, before_effect=lambda: guards.append(True)).remove()
            count = len(removed)
            # Fresh records and objects retain original evidence and do not
            # repeat proven absent removals or adopt a new layout.
            fresh = RetirementJournal(fixture.io, fence)
            PrivateCleanup(fresh, fresh.load(), before_effect=lambda: guards.append(True)).remove()
        assert len(removed) == count and count > 10
        assert len(guards) > count
        assert all(not (fixture.root / path).exists() for path in original.private.members)
        assert all(str(Path(path).parent) in synced for path in removed)
        assert journal.load() == original
        assert (sentinel.read_bytes(), sentinel.stat().st_ino) == retained
        assert (fixture.root / BINARY).is_file() and (fixture.root / ACCOUNT_RECORD).is_file()
        assert not (fixture.root / STATE / 'home-retirement.json').exists()
        assert fixture.effects() == []


@pytest.mark.parametrize('drift', ['immutable-write', 'runtime-replacement', 'private-symlink',
    'private-hardlink', 'parent-replacement', 'parent-mode', 'unknown-private',
    'nonempty-directory', 'originally-absent'])
def test_private_cleanup_refuses_drift_before_any_removal(
        fixture: InventoryFixture, drift: str) -> None:
    from runtime.remote_access.home_retirement_cleanup import PrivateCleanup
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    import shutil
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        if drift == 'immutable-write':
            (fixture.root / CONFIG).write_bytes(b'foreign-config')
        elif drift == 'runtime-replacement':
            target = fixture.root / OPAQUE
            target.rename(fixture.root / 'kept-original-runtime')
            target.write_bytes(b'foreign-replacement')
            target.chmod(0o600)
        elif drift == 'private-symlink':
            target = fixture.root / PRIVATE_KEY
            target.unlink()
            target.symlink_to(fixture.root / OPAQUE)
        elif drift == 'private-hardlink':
            os.link(fixture.root / PRIVATE_KEY, fixture.root / 'kept-key-link')
        elif drift == 'parent-replacement':
            target = fixture.root / PRIVATE
            target.rename(target.with_name('kept-original-ca'))
            shutil.copytree(target.with_name('kept-original-ca'), target)
        elif drift == 'parent-mode':
            (fixture.root / PRIVATE).chmod(0o750)
        elif drift == 'unknown-private':
            (fixture.root / PRIVATE / 'unknown.key').write_bytes(b'foreign-keeper')
        elif drift == 'nonempty-directory':
            (fixture.root / 'var/lib/happyranch-headscale/acme-unused/unknown').write_bytes(b'foreign-keeper')
        else:
            (fixture.root / 'run/happyranch-headscale/headscale.sock').write_bytes(b'foreign-new-leaf')
        before = fixture.snapshot()
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            PrivateCleanup(journal, original, before_effect=lambda: None).remove()
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize('boundary', ['first-guard', 'next-guard', 'next-target', 'sync'])
def test_private_cleanup_interruption_keeps_original_evidence_and_checks_next_effect(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, boundary: str) -> None:
    from runtime.remote_access.home_retirement_cleanup import PrivateCleanup
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        present = sorted(path for path, value in original.private.members.items() if value is not None)
        first = present[0]
        second = next(path for path in present[1:]
                      if not stat.S_ISDIR(original.private.members[path][4]))
        guards, removed = [], []
        real_unlink, real_sync = os.unlink, os.fsync
        def guard():
            guards.append(True)
            if (boundary == 'first-guard' and len(guards) == 2
                    or boundary == 'next-guard' and removed):
                raise RetirementUncertain()
        def unlink(name, *, dir_fd=None):
            real_unlink(name, dir_fd=dir_fd)
            removed.append(str(Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name))
            if boundary == 'next-target':
                (fixture.root / second).write_bytes(b'foreign-between-effects')
        def sync(fd):
            if boundary == 'sync' and removed and os.readlink(f'/proc/self/fd/{fd}') == str((fixture.root / first).parent):
                raise OSError('synthetic-sync-failure')
            real_sync(fd)
        with monkeypatch.context() as scoped:
            scoped.setattr(os, 'unlink', unlink)
            scoped.setattr(os, 'fsync', sync)
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                PrivateCleanup(journal, original, before_effect=guard).remove()
        assert len(removed) == (0 if boundary == 'first-guard' else 1)
        assert (fixture.root / second).exists()
        assert RetirementJournal(fixture.io, fence).load() == original
        assert not (fixture.root / STATE / 'home-retirement.json').exists()
        if boundary in ('first-guard', 'next-guard', 'sync'):
            fresh = RetirementJournal(fixture.io, fence)
            PrivateCleanup(fresh, fresh.load(), before_effect=lambda: None).remove()
            assert all(not (fixture.root / path).exists() for path in original.private.members)
        assert fixture.effects() == []


@pytest.mark.parametrize('family', ['private', 'auxiliary', 'managed'])
@pytest.mark.parametrize('boundary', ['complete', 'sync-failure', 'reappearance'])
def test_cleanup_recovery_synchronizes_previously_removed_originals(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, family: str, boundary: str) -> None:
    from runtime.remote_access.home_retirement_cleanup import ManagedCleanup, PrivateCleanup
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    from runtime.remote_access.home_retirement_operations import held_auxiliary_operations
    auxiliary_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        members = getattr(original, family).members
        leaves = {path: signature for path, signature in members.items()
                  if signature is not None and (family == 'private'
                      or stat.S_ISREG(signature[4]) and path not in (*OPERATION_LOCKS, *MANAGED_OPAQUE[:3]))}
        # Model the persisted unlink with its parent sync interrupted, without
        # claiming the cleanup owner caused or acknowledged these absences.
        for path, signature in leaves.items():
            target = fixture.root / path
            target.rmdir() if stat.S_ISDIR(signature[4]) else target.unlink()
        target = fixture.root / (PRIVATE_KEY if family == 'private' else
            MANAGED_OPAQUE[3] if family == 'managed' else OPERATION)
        required = {str((fixture.root / path).parent) for path in leaves}
        synced, injected, removals = [], [], []
        real_sync, real_unlink = os.fsync, os.unlink
        def sync(fd):
            parent = os.readlink(f'/proc/self/fd/{fd}')
            if parent == str(target.parent) and boundary == 'sync-failure':
                raise OSError('synthetic interrupted recovery sync')
            real_sync(fd)
            synced.append(parent)
            if parent == str(target.parent) and boundary == 'reappearance' and not injected:
                target.write_bytes(b'synthetic-foreign-reappearance')
                injected.append(fixture.snapshot())
        def unlink(name, *, dir_fd=None):
            removals.append(name)
            return real_unlink(name, dir_fd=dir_fd)
        def recover():
            fresh = RetirementJournal(fixture.io, fence)
            saved = fresh.load()
            if family in ('private', 'managed'):
                cleanup = PrivateCleanup if family == 'private' else ManagedCleanup
                cleanup(fresh, saved, before_effect=lambda: None).remove()
            else:
                with held_auxiliary_operations(fresh, saved, cleanup=True) as exclusion:
                    exclusion.remove_leaves(before_effect=lambda: None)
        before = fixture.snapshot()
        with monkeypatch.context() as scoped:
            scoped.setattr(os, 'fsync', sync)
            scoped.setattr(os, 'unlink', unlink)
            if boundary == 'complete':
                recover()
                assert required <= set(synced), 'absent original parent was not synchronized'
            else:
                with pytest.raises(RetirementUncertain, match='^migration pending$'):
                    recover()
        assert not removals and fixture.effects() == []
        assert fixture.snapshot() == (injected[0] if injected else before)
        assert RetirementJournal(fixture.io, fence).load() == original
        assert not (fixture.root / STATE / 'home-retirement.json').exists()


@pytest.mark.parametrize('state', ['unchanged', 'runtime-flush', 'missing-original'])
def test_managed_cleanup_removes_original_nontrust_leaves_and_reuses_absence(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    from runtime.remote_access.home_retirement_cleanup import ManagedCleanup
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        retained = {path: ((fixture.root / path).read_bytes(), (fixture.root / path).stat().st_ino)
                    for path in (*MANAGED_OPAQUE[:3], BINARY, ACCOUNT_RECORD,
                        'retained-native/saved-home', 'home/release/.local/share/happyranch/daemon.token')}
        if state == 'runtime-flush':
            (fixture.root / MANAGED_OPAQUE[3]).write_bytes(b'synthetic-stop-flush')
        elif state == 'missing-original':
            (fixture.root / 'etc/happyranch/connector.json').unlink()
        guards, removed, synced = [], [], []
        real_unlink, real_sync, real_open = os.unlink, os.fsync, os.open
        def unlink(name, *, dir_fd=None):
            removed.append(str(Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name))
            real_unlink(name, dir_fd=dir_fd)
        def sync(fd):
            synced.append(os.readlink(f'/proc/self/fd/{fd}'))
            real_sync(fd)
        def no_opaque_open(name, *args, **kwargs):
            assert Path(name).name not in {Path(path).name for path in MANAGED_OPAQUE}
            return real_open(name, *args, **kwargs)
        with monkeypatch.context() as scoped:
            scoped.setattr(os, 'unlink', unlink)
            scoped.setattr(os, 'fsync', sync)
            scoped.setattr(os, 'open', no_opaque_open)
            ManagedCleanup(journal, original, before_effect=lambda: guards.append(True)).remove()
            count = len(removed)
            fresh = RetirementJournal(fixture.io, fence)
            ManagedCleanup(fresh, fresh.load(), before_effect=lambda: guards.append(True)).remove()
        leaves = set(original.managed.members) - set(MANAGED_OPAQUE[:3])
        assert len(removed) == count and count >= 12
        assert all(not (fixture.root / path).exists() for path in leaves)
        assert len(guards) > count and all(str(Path(path).parent) in synced for path in removed)
        assert {path: ((fixture.root / path).read_bytes(), (fixture.root / path).stat().st_ino)
                for path in retained} == retained
        assert journal.load() == original and fixture.effects() == []
        assert not (fixture.root / STATE / 'home-retirement.json').exists()


@pytest.mark.parametrize('drift', ['config-write', 'runtime-replacement', 'parent-replacement',
    'parent-mode', 'symlink', 'hardlink', 'unknown-state', 'unknown-trust', 'originally-absent', 'account'])
def test_managed_cleanup_refuses_drift_before_any_removal(fixture: InventoryFixture, drift: str) -> None:
    from runtime.remote_access.home_retirement_cleanup import ManagedCleanup
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        target = fixture.root / MANAGED_OPAQUE[3]
        if drift == 'config-write':
            (fixture.root / 'etc/happyranch/connector.json').write_bytes(b'synthetic-foreign-config')
        elif drift == 'runtime-replacement':
            target.rename(fixture.root / 'kept-original-runtime')
            target.write_bytes(b'synthetic-replaced-runtime')
            target.chmod(0o600)
        elif drift == 'parent-replacement':
            parent = target.parent
            saved = fixture.root / 'kept-original-parent'
            parent.rename(saved)
            parent.mkdir(mode=0o700)
            # Keep every runtime leaf inode/custody unchanged so only the
            # replaced parent guard owns this refusal.
            for child in saved.iterdir():
                child.rename(parent / child.name)
        elif drift == 'parent-mode':
            target.parent.chmod(0o750)
        elif drift == 'symlink':
            target.unlink()
            target.symlink_to(fixture.root / MANAGED_OPAQUE[0])
        elif drift == 'hardlink':
            os.link(target, fixture.root / 'kept-runtime-link')
        elif drift in ('unknown-state', 'unknown-trust'):
            fixture.file(('var/lib/happyranch-tsnet-sidecar' if drift == 'unknown-state'
                else 'var/lib/happyranch-connector') + '/foreign', b'synthetic-foreign')
        elif drift == 'originally-absent':
            fixture.file('run/happyranch-connector/control.sock', b'synthetic-foreign-new-leaf')
        else:
            lookup = fixture.io._lookup_user
            fixture.io._lookup_user = lambda name: (SimpleNamespace(pw_name=name,
                pw_uid=os.getuid() + 1, pw_gid=os.getgid()) if name == 'happyranch' else lookup(name))
        before = fixture.snapshot()
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            ManagedCleanup(journal, original, before_effect=lambda: None).remove()
        assert fixture.snapshot() == before and fixture.effects() == []


@pytest.mark.parametrize('boundary', ['first-guard', 'next-guard', 'next-target', 'sync'])
def test_managed_cleanup_interruption_preserves_originals_and_checks_next_effect(
        fixture: InventoryFixture, monkeypatch: pytest.MonkeyPatch, boundary: str) -> None:
    from runtime.remote_access.home_retirement_cleanup import ManagedCleanup
    from runtime.remote_access.home_retirement_journal import RetirementJournal
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        leaves = sorted(path for path, value in original.managed.members.items()
                        if value is not None and path not in MANAGED_OPAQUE[:3])
        guards, removed = [], []
        real_unlink, real_sync = os.unlink, os.fsync
        def guard():
            guards.append(True)
            if (boundary == 'first-guard' and len(guards) == 2
                    or boundary == 'next-guard' and removed):
                raise RetirementUncertain()
        def unlink(name, *, dir_fd=None):
            real_unlink(name, dir_fd=dir_fd)
            removed.append(name)
            if boundary == 'next-target':
                (fixture.root / leaves[1]).write_bytes(b'synthetic-foreign-between-effects')
        def sync(fd):
            if boundary == 'sync' and removed and os.readlink(f'/proc/self/fd/{fd}') == str((fixture.root / leaves[0]).parent):
                raise OSError('synthetic-sync-failure')
            real_sync(fd)
        with monkeypatch.context() as scoped:
            scoped.setattr(os, 'unlink', unlink)
            scoped.setattr(os, 'fsync', sync)
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                ManagedCleanup(journal, original, before_effect=guard).remove()
        assert len(removed) == (0 if boundary == 'first-guard' else 1)
        assert (fixture.root / leaves[1]).is_file()
        assert RetirementJournal(fixture.io, fence).load() == original
        assert not (fixture.root / STATE / 'home-retirement.json').exists()
        if boundary != 'next-target':
            fresh = RetirementJournal(fixture.io, fence)
            ManagedCleanup(fresh, fresh.load(), before_effect=lambda: None).remove()
            assert all(not (fixture.root / path).exists() for path in leaves)
        assert all((fixture.root / path).is_file() for path in MANAGED_OPAQUE[:3])
        assert fixture.effects() == []
