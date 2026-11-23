"""Retirement cutoff over real private IPC, anchored store and stream owner.

Run: uv run --frozen pytest scripts/test_home_retirement_cutoff.py -v --tb=short
External peer-root/NSS/proc facts are isolated; no host user/service is changed.
The fixture does not implement revoke, a cutoff decision or a success receipt.
"""
from __future__ import annotations

import hashlib
import json
import stat
import os
import socket
import struct
import threading
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import helper_control as control
from runtime.remote_access import home_retirement_cutoff as cutoff
from runtime.remote_access.authorization import PendingPairing, TrustState
from runtime.remote_access.home_retirement import RetirementUncertain
from runtime.remote_access.home_retirement_fence import retirement_fence
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.invite_control import InviteControl
from runtime.remote_access.pairing import PairingError, PairingManager, PinnedPairingManager
from runtime.remote_access.state_store import AtomicFileTrustStateStore, StateStoreError
from runtime.remote_access.streams import StreamClosed, StreamRegistry
from scripts.test_home_retirement import Fixture, STATE
from scripts.test_invite_control import Peer

SECRET = "synthetic-retirement-invite"
DIGEST = hashlib.sha256(SECRET.encode()).hexdigest()
BOOT = "11111111-2222-4333-8444-555555555555"
PIN = STATE + "/home-retirement-peer.json"


class CutoffFixture(Fixture):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.directory(STATE)
        self.directory(cutoff._CONTROL).chmod(0o700)
        self.path = self.directory(cutoff._STORE) / "trust-state.json"
        self.path.parent.chmod(0o700)
        self.identity = ConnectorIdentity(tenant_id="tenant", home_id="home", connector_id="connector")
        self.store = AtomicFileTrustStateStore(self.path, TrustState(self.identity, 0, 0))
        self.store.save(self.store.load())
        self.registry = StreamRegistry()
        self.manager = PinnedPairingManager(state_store=self.store, identity=self.identity, registry=self.registry)
        self.adapter = InviteControl(self.manager)
        self.pid = os.getpid()
        self.file(f"proc/{self.pid}/status", (
            f"Uid:\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\n"
            f"Gid:\t{os.getgid()}\t{os.getgid()}\t{os.getgid()}\t{os.getgid()}\n").encode())
        self.process_start(654)
        self.file("proc/sys/kernel/random/boot_id", (BOOT + "\n").encode(), 0o444)
        self.file(f"proc/{self.pid}/cgroup", b"0::/system.slice/happyranch-connector.service\n")
        (self.root / f"proc/{self.pid}/exe").symlink_to(
            root / "opt/happyranch-desktop/python/bin/python3.12")
        self.closed: list[str] = []
        self.frames: list[bytes] = []
        self.status_hook = lambda: None

    def lookup_user(self, name):
        assert name == "happyranch"
        return SimpleNamespace(pw_name=name, pw_uid=os.getuid(), pw_gid=os.getgid())

    def lookup_group(self, name):
        assert name == "happyranch"
        return SimpleNamespace(gr_name=name, gr_gid=os.getgid(), gr_mem=[])

    def process_start(self, start):
        self.file(f"proc/{self.pid}/stat", (str(self.pid) + " (connector worker) " + " ".join(
            ["S", *(["0"] * 18), str(start), "0", "0"]) + "\n").encode())

    def grant(self, node):
        now = datetime.now(timezone.utc)
        self.manager.issue_invite_record(DIGEST, now + timedelta(minutes=4))
        pending = self.manager.redeem(SECRET, node, "control", now)
        device = self.manager.approve_pending(pending.generation, ("control", node))
        credential = self.manager.complete(pending.completion_handle, node, "control", now)
        assert credential and device
        return device, credential

    def stream(self, device, node, *, close=None):
        fixture = self
        class Handle:
            def close(self):
                # Independent reopening observes the committed deny BEFORE I/O
                # cleanup; do not derive a receipt in the callback.
                state = AtomicFileTrustStateStore(fixture.path, fixture.store.load()).load()
                assert device not in state.devices or state.devices[device].revoked
                fixture.closed.append(node)
                if close:
                    close()
            def receive(self):
                return b"synthetic-live-frame"
            def send(self, payload):
                pass
        return self.registry.open(node, Handle(), device_id=device,
            epoch=self.store.load().devices[device].authorization_epoch)

    def reopened(self):
        return AtomicFileTrustStateStore(self.path, TrustState(self.identity, 0, 0)).snapshot()

    def operations(self):
        fixture = self
        class Operations:
            def mutation_lock(self):
                return fixture.manager.mutation_lock()
            def status(self):
                fixture.status_hook()
                _, generation = fixture.store.snapshot()
                return control.Snapshot(generation, os.getuid(), True, "ready", True, None, ())
            def reserve_invite(self, *args, **kwargs):
                return fixture.adapter.reserve_invite(*args, **kwargs)
            def invalidate_invite(self, *args, **kwargs):
                return fixture.adapter.invalidate_invite(*args, **kwargs)
            def revoke_all(self, *args, **kwargs):
                return fixture.adapter.revoke_all(*args, **kwargs)
        return Operations()

    @contextmanager
    def active(self, monkeypatch):
        monkeypatch.setattr(control, "CONTROL_SOCKET", str(self.root / cutoff._CONTROL / "control.sock"))
        with retirement_fence(self.io) as fence:
            listener = control.ControlListener(self.operations())
            original = listener.handler.handle
            monkeypatch.setattr(listener.handler, "handle", lambda connection: original(Peer(connection, self.frames)))
            try:
                yield cutoff.RetirementCutoff(self.io, fence, self.identity, os.getuid(),
                    original_peer=cutoff.OriginalPeer(self.pid, 654, BOOT))
            finally:
                listener.close()


@pytest.fixture
def fixture(tmp_path):
    return CutoffFixture(tmp_path / "root")


def test_original_owner_cutoff_refuses_another_state_roots_live_fence(fixture, monkeypatch):
    other = Fixture(fixture.root.parent / "other-root")
    other.directory(STATE)
    with fixture.active(monkeypatch) as active, retirement_fence(other.io) as foreign_fence:
        before = fixture.snapshot(), other.snapshot()
        owner = cutoff.RetirementCutoff(fixture.io, foreign_fence, fixture.identity, os.getuid(),
            original_peer=active.peer)
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            owner.revoke()
        assert (fixture.snapshot(), other.snapshot()) == before
        assert fixture.frames == [] and fixture.calls == [] and other.calls == []


@pytest.mark.parametrize("ceremony", ["invite", "pending", "delivery"])
def test_cutoff_requires_burn_denies_and_original_owner_ack(fixture, monkeypatch, ceremony):
    grants = [fixture.grant(node) for node in ("http", "sse", "ws")]
    streams = [fixture.stream(device, node) for (device, _), node in zip(grants, ("http", "sse", "ws"))]
    fixture.manager.issue_invite_record(DIGEST, datetime.now(timezone.utc) + timedelta(minutes=4))
    if ceremony != "invite":
        pending = fixture.manager.redeem(SECRET, "pending", "control", datetime.now(timezone.utc))
        if ceremony == "delivery":
            fixture.manager.approve_pending(pending.generation, ("control", "pending"))
    with fixture.active(monkeypatch) as owner:
        before = fixture.snapshot()
        receipt = owner.revoke()
        receipt.require()
        state, generation = fixture.reopened()
        assert generation == receipt._generation
        assert state.invite_record is state.pending_approval is state.delivery is None
        assert all(device.revoked for device in state.devices.values())
        assert set(fixture.closed) == {"http", "sse", "ws"}
        for stream in streams:
            for operation in (stream.receive, lambda: stream.send(b"late")):
                with pytest.raises(StreamClosed):
                    operation()
        for node, (device, credential) in zip(("http", "sse", "ws"), grants):
            assert not fixture.manager.admit(credential, node, "control", datetime.now(timezone.utc)).ok
            with pytest.raises(StreamClosed):
                fixture.registry.open("late-" + node, SimpleNamespace(close=lambda: None), device_id=device,
                    epoch=state.devices[device].authorization_epoch)
        # The authoritative pair and protected original-peer intent change.
        # No account/service/local data or legacy private evidence is removed.
        after = fixture.snapshot()
        assert set(after) - set(before) == {PIN}
        assert after[STATE][:6] == before[STATE][:6]
        for name in before:
            if name not in (STATE, cutoff._STORE, cutoff._STORE + "/trust-state.json", cutoff._STORE + "/trust-state.json.anchor"):
                assert after[name] == before[name], name
        assert fixture.calls == []
    with pytest.raises(RetirementUncertain, match="^migration pending$"):
        receipt.require()


@pytest.mark.parametrize("fails", [False, True])
def test_removed_owner_cleanup_is_not_replaced_by_empty_store(fixture, monkeypatch, fails):
    grants = [fixture.grant(node) for node in ("removed", "present")]
    def close():
        if fails:
            raise OSError("synthetic-private-close-failure")
    streams = [fixture.stream(device, node, close=close if node == "removed" else None)
               for (device, _), node in zip(grants, ("removed", "present"))]
    external = PairingManager(state_store=AtomicFileTrustStateStore(fixture.path, fixture.store.load()),
        identity=fixture.identity, registry=StreamRegistry())
    external.remove_device(grants[0][0])
    assert streams[0].receive() == b"synthetic-live-frame"
    with fixture.active(monkeypatch) as owner:
        if fails:
            for _ in range(2):
                owner = cutoff.RetirementCutoff(fixture.io, owner.fence, fixture.identity, os.getuid(),
                    original_peer=cutoff.OriginalPeer(fixture.pid, 654, BOOT))
                with pytest.raises(RetirementUncertain, match="^migration pending$"):
                    owner.revoke()
            assert owner._acknowledged is None
        else:
            receipt = owner.revoke()
            receipt.require()
        assert set(fixture.closed) == {"removed", "present"}
        assert all(stream.closed for stream in streams)
        assert all(device.revoked for device in fixture.reopened()[0].devices.values())
        with pytest.raises(StreamClosed):
            fixture.registry.open("late-removed", SimpleNamespace(close=lambda: None),
                device_id=grants[0][0], epoch=1)
        assert fixture.calls == []


def test_inflight_close_seals_wrappers_but_cannot_issue_receipt(fixture, monkeypatch):
    device, _ = fixture.grant("blocked")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    def close():
        entered.set()
        assert release.wait(2), "test close barrier not released"
    stream = fixture.stream(device, "blocked", close=close)
    results = []
    with fixture.active(monkeypatch) as owner:
        def retire():
            try:
                results.append(owner.revoke())
            except Exception as error:
                results.append(error)
            finally:
                finished.set()
        worker = threading.Thread(target=retire)
        worker.start()
        try:
            assert entered.wait(2)
            assert fixture.reopened()[0].devices[device].revoked
            assert not finished.is_set() and owner._acknowledged is None
            for operation in (stream.receive, lambda: stream.send(b"late")):
                with pytest.raises(StreamClosed):
                    operation()
            assert fixture.calls == []
        finally:
            release.set()
            worker.join(3)
        assert not worker.is_alive()
        assert len(results) == 1 and isinstance(results[0], cutoff.CutoffReceipt), results
        results[0].require()


def test_partial_commit_is_preserved_without_cutoff_permission(fixture, monkeypatch):
    grants = [fixture.grant(node) for node in ("first", "second")]
    streams = [fixture.stream(device, node) for (device, _), node in zip(grants, ("first", "second"))]
    original = fixture.manager.revoke_device
    calls = []
    def partial(device, **kwargs):
        calls.append(device)
        if len(calls) == 2:
            raise PairingError("authorization_expired")
        return original(device, **kwargs)
    monkeypatch.setattr(fixture.manager, "revoke_device", partial)
    with fixture.active(monkeypatch) as owner:
        first, second = tuple(fixture.reopened()[0].devices)
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            owner.revoke()
        state, generation = fixture.reopened()
        assert state.invite_record is state.pending_approval is state.delivery is None
        assert calls == [first, second]
        assert state.devices[first].revoked
        assert not state.devices[second].revoked
        for stream, (device, _) in zip(streams, grants):
            assert stream.closed is (device == first)
        assert owner._acknowledged is None and fixture.calls == []
        with pytest.raises(RetirementUncertain):
            cutoff.CutoffReceipt(owner, generation).require()


def test_new_cutoff_object_cannot_infer_ack_from_denied_state(fixture, monkeypatch):
    device, _ = fixture.grant("device")
    fixture.stream(device, "device")
    with fixture.active(monkeypatch) as owner:
        original = owner.revoke()
        fresh = cutoff.RetirementCutoff(fixture.io, owner.fence, fixture.identity, os.getuid(),
            original_peer=cutoff.OriginalPeer(fixture.pid, 654, BOOT))
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            cutoff.CutoffReceipt(fresh, original._generation).require()
        # Explicit recovery on the SAME original serving owner can re-observe
        # its terminal cleanup result; no new registry/reset or close replay.
        recovered = fresh.revoke()
        recovered.require()
        assert recovered._generation > original._generation
        assert fixture.closed == ["device"]
        assert all(device.revoked for device in fixture.reopened()[0].devices.values())
        assert fixture.calls == []


def test_original_ack_cannot_replace_deny_readback(fixture, monkeypatch):
    fixture.grant("device")
    # Mixed legacy authority not handled by the pinned bulk operation must
    # remain pending. A successful real bulk reply alone cannot attest it away.
    with fixture.store.transaction() as tx:
        tx.state.pending_pairings[DIGEST] = PendingPairing(DIGEST,
            datetime.now(timezone.utc) + timedelta(minutes=4))
    with fixture.active(monkeypatch) as owner:
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            owner.revoke()
        state, _ = fixture.reopened()
        assert all(device.revoked for device in state.devices.values())
        assert not state.pending_pairings[DIGEST].consumed
        assert any(b'"result":"ok"' in frame for frame in fixture.frames)
        assert fixture.calls == []


@pytest.mark.parametrize("damage", ["anchor", "new-generation"])
def test_post_reply_store_change_refuses_cutoff(fixture, monkeypatch, damage):
    device, _ = fixture.grant("device")
    fixture.stream(device, "device")
    original = cutoff._OriginalClient._call
    with fixture.active(monkeypatch) as owner:
        def altered(client, request, *args, **kwargs):
            reply = original(client, request, *args, **kwargs)
            if request["method"] == "revoke-all@1":
                assert fixture.reopened()[0].devices[device].revoked
                if damage == "anchor":
                    Path(str(fixture.path) + ".anchor").write_bytes(b"{}")
                else:
                    fixture.store.save(fixture.store.load())
            return reply
        monkeypatch.setattr(cutoff._OriginalClient, "_call", altered)
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            owner.revoke()
        if damage == "anchor":
            assert Path(str(fixture.path) + ".anchor").read_bytes() == b"{}"
            with pytest.raises(StateStoreError):
                fixture.reopened()
        else:
            assert fixture.reopened()[0].devices[device].revoked
        assert fixture.closed == ["device"] and fixture.calls == []


def test_existing_writer_lock_refuses_before_ipc(fixture, monkeypatch):
    fixture.grant("device")
    with fixture.active(monkeypatch) as owner:
        before = fixture.snapshot()
        with fixture.store._lock_file():
            with pytest.raises(RetirementUncertain, match="^migration pending$"):
                owner.revoke()
        assert fixture.snapshot() == before
        assert fixture.frames == [] and fixture.calls == []


@pytest.mark.parametrize("committed", [False, True])
def test_expired_or_late_reply_never_grants_cutoff(fixture, monkeypatch, committed):
    device, _ = fixture.grant("device")
    stream = fixture.stream(device, "device")
    with fixture.active(monkeypatch) as owner:
        initial = fixture.reopened()[1]
        if committed:
            receive = cutoff._receive
            def late(connection):
                reply = receive(connection)
                if reply.get("result") == "ok":
                    owner.deadline = time.monotonic() - 1
                return reply
            monkeypatch.setattr(cutoff, "_receive", late)
        else:
            owner.deadline = time.monotonic() - 1
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            owner.revoke()
        state, generation = fixture.reopened()
        assert state.devices[device].revoked is committed
        assert stream.closed is committed
        assert (generation > initial) is committed
        assert owner._acknowledged is None and fixture.calls == []
        if not committed:
            assert fixture.frames == [], "expired cutoff must not open a control operation"


@pytest.mark.parametrize("case", ["missing-pair", "missing-anchor", "missing-lock", "pair-symlink",
    "anchor-hardlink", "lock-hardlink", "pair-fifo", "pair-mode", "parent-mode", "anchor-digest", "identity"])
def test_unsafe_store_refuses_without_repair_or_ipc(fixture, monkeypatch, case):
    with fixture.active(monkeypatch) as owner:
        path = fixture.path
        if case.startswith("missing-"):
            suffix = {"pair": "", "anchor": ".anchor", "lock": ".lock"}[case.removeprefix("missing-")]
            Path(str(path) + suffix).unlink()
        elif case == "pair-symlink":
            path.unlink()
            path.symlink_to(fixture.root / "external-keeper")
        elif case.endswith("hardlink"):
            os.link(str(path) + (".anchor" if case.startswith("anchor") else ".lock"), fixture.root / "external-keeper")
        elif case == "pair-fifo":
            path.unlink()
            os.mkfifo(path, 0o600)
        elif case == "pair-mode":
            path.chmod(0o644)
        elif case == "parent-mode":
            path.parent.chmod(0o755)
        elif case == "anchor-digest":
            Path(str(path) + ".anchor").write_bytes(b"{}")
        else:
            owner.identity = ConnectorIdentity(tenant_id="foreign", home_id="home", connector_id="connector")
        # FIFO/symlink inventory must not open them; compare metadata and exact
        # regular bytes only, with external sentinel retention.
        def inventory():
            return {str(p.relative_to(fixture.root)): (cutoff._signature(p.lstat()), p.read_bytes() if p.is_file() and not p.is_symlink() else None)
                    for p in fixture.root.rglob("*")}
        before = inventory()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            owner.revoke()
        assert inventory() == before
        assert fixture.frames == [] and fixture.calls == []


@pytest.mark.parametrize("case", ["socket-mode", "proc-start", "proc-replacement", "proc-exe", "proc-cgroup", "proc-uid", "proc-symlink"])
def test_unsafe_original_peer_never_reaches_commit(fixture, monkeypatch, case):
    device, _ = fixture.grant("device")
    with fixture.active(monkeypatch) as owner:
        if case == "socket-mode":
            (fixture.root / cutoff._CONTROL / "control.sock").chmod(0o666)
        elif case == "proc-start":
            fixture.process_start(0)
        elif case == "proc-replacement":
            fixture.process_start(999)
        elif case == "proc-exe":
            path = fixture.root / f"proc/{fixture.pid}/exe"
            path.unlink()
            path.symlink_to("/foreign/python3.12")
        elif case == "proc-cgroup":
            fixture.file(f"proc/{fixture.pid}/cgroup", b"0::/foreign\n")
        elif case == "proc-uid":
            fixture.file(f"proc/{fixture.pid}/status", b"Uid:\t1\t1\t1\t1\nGid:\t1\t1\t1\t1\n")
        else:
            (fixture.root / f"proc/{fixture.pid}").rename(fixture.root / "proc/saved")
            (fixture.root / f"proc/{fixture.pid}").symlink_to("saved")
        before = fixture.path.read_bytes()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            owner.revoke()
        assert fixture.path.read_bytes() == before
        assert not fixture.reopened()[0].devices[device].revoked
        assert b'{"commit":true}\n' not in fixture.frames
        assert owner._acknowledged is None and fixture.calls == []


@pytest.mark.parametrize("field", ["uid", "gid"])
def test_kernel_peer_identity_refuses_before_request(fixture, monkeypatch, field):
    fixture.grant("device")
    real_socket = socket.socket
    class WrongPeer(real_socket):
        def getsockopt(self, level, option, *args):
            if (level, option) == (socket.SOL_SOCKET, socket.SO_PEERCRED):
                return struct.pack("3i", fixture.pid,
                    os.getuid() + (field == "uid"), os.getgid() + (field == "gid"))
            return super().getsockopt(level, option, *args)
    with fixture.active(monkeypatch) as owner:
        before = fixture.path.read_bytes()
        monkeypatch.setattr(cutoff.socket, "socket", WrongPeer)
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            owner.revoke()
        assert fixture.path.read_bytes() == before
        assert not any(b'"method"' in frame for frame in fixture.frames), "foreign peer received a protocol request"
        assert fixture.calls == []


@pytest.mark.parametrize("change", ["generation", "peer-start", "fence", "deadline"])
def test_cutoff_receipt_requires_fresh_facts(fixture, monkeypatch, change):
    fixture.grant("device")
    # The changed fence also refuses the context's final readback. Both denials
    # are required; do not restore or silently remove the changed evidence.
    outer = pytest.raises(RetirementUncertain, match="^migration pending$") if change == "fence" else nullcontext()
    with outer, fixture.active(monkeypatch) as owner:
        receipt = owner.revoke()
        receipt.require()
        if change == "generation":
            fixture.store.save(fixture.store.load())
        elif change == "peer-start":
            fixture.process_start(999)
        elif change == "fence":
            (fixture.root / STATE / ".home-activation.json.pending").write_bytes(b"foreign")
        else:
            owner.deadline = time.monotonic() - 1
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            receipt.require()
        assert fixture.calls == []


def expected_peer_record(fixture):
    return {"format": "home-retirement-peer@1",
        "peer": {"pid": fixture.pid, "started": 654, "boot_id": BOOT},
        "account": {"uid": os.getuid(), "gid": os.getgid()},
        "identity": {"tenant_id": "tenant", "home_id": "home", "connector_id": "connector"},
        "release_uid": os.getuid()}


def test_original_peer_is_durable_before_first_request_and_survives_reopen(fixture, monkeypatch):
    device, _ = fixture.grant("device")
    stream = fixture.stream(device, "device")
    synced, requests = [], []
    real_sync, real_send = os.fsync, cutoff._send
    expected = expected_peer_record(fixture)
    def sync(fd):
        real_sync(fd)
        info = os.fstat(fd)
        synced.append((info.st_dev, info.st_ino))
    def send(connection, request):
        path = fixture.root / PIN
        info, parent = path.stat(), path.parent.stat()
        requests.append((request, json.loads(path.read_bytes()), info, parent, tuple(synced)))
        return real_send(connection, request)
    with fixture.active(monkeypatch) as owner:
        monkeypatch.setattr(cutoff.os, "fsync", sync)
        monkeypatch.setattr(cutoff, "_send", send)
        owner.capture_original()
        assert owner._acknowledged is None
        assert not fixture.reopened()[0].devices[device].revoked
        assert stream.receive() == b"synthetic-live-frame"
        receipt = owner.revoke()
        receipt.require()
        assert requests[0][0] == {"method": "status"}
        before = cutoff._signature((fixture.root / PIN).stat()), (fixture.root / PIN).read_bytes()
        synced.clear()
        fresh = cutoff.RetirementCutoff.from_saved_peer(fixture.io, owner.fence, fixture.identity, os.getuid())
        recovered = fresh.revoke()
        recovered.require()
        assert (cutoff._signature((fixture.root / PIN).stat()), (fixture.root / PIN).read_bytes()) == before
        assert fixture.closed == ["device"] and fixture.calls == []
        assert SECRET.encode() not in before[1]
        assert repr(owner.peer) == "OriginalPeer(<redacted>)"
        for _, value, info, parent, syncs in requests:
            assert value == expected
            assert stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
            assert (info.st_uid, info.st_gid) == fixture.io.root_owner
            assert (info.st_dev, info.st_ino) in syncs, "original peer file was not synchronized before request"
            assert (parent.st_dev, parent.st_ino) in syncs, "original peer parent was not synchronized before request"


@pytest.mark.parametrize("damage", ["missing", "malformed", "no-newline", "mode", "hardlink", "symlink", "parent-symlink", "different-boot"])
def test_boot_identity_is_required_before_any_request(fixture, monkeypatch, damage):
    device, _ = fixture.grant("device")
    path = fixture.root / "proc/sys/kernel/random/boot_id"
    with fixture.active(monkeypatch) as owner:
        if damage == "missing":
            path.unlink()
        elif damage in ("malformed", "no-newline", "different-boot"):
            path.chmod(0o600)
            path.write_bytes({"malformed": b"private-invalid-boot\n", "no-newline": BOOT.encode(),
                "different-boot": b"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\n"}[damage])
            path.chmod(0o444)
        elif damage == "mode":
            path.chmod(0o644)
        elif damage == "hardlink":
            os.link(path, fixture.root / "external-boot")
        elif damage == "symlink":
            path.rename(fixture.root / "external-boot")
            path.symlink_to(fixture.root / "external-boot")
        else:
            path.parent.rename(path.parent.with_name("saved-random"))
            path.parent.symlink_to("saved-random")
        before = fixture.snapshot()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            owner.revoke()
        assert fixture.snapshot() == before
        assert not fixture.reopened()[0].devices[device].revoked
        assert not any(b'"method"' in frame for frame in fixture.frames)
        assert fixture.calls == [] and not (fixture.root / PIN).exists()


@pytest.mark.parametrize("damage", ["malformed", "extra", "pid", "start", "boot", "release", "identity", "uid", "gid", "mode", "hardlink", "symlink", "fifo", "directory"])
def test_unknown_original_peer_record_is_preserved_without_request(fixture, monkeypatch, damage):
    device, _ = fixture.grant("device")
    with fixture.active(monkeypatch) as owner:
        # Synthetic interrupted intent only; the fixture supplies no success
        # receipt, acknowledgement or retirement transition.
        value = expected_peer_record(fixture)
        if damage in ("pid", "start", "boot"):
            key = {"pid": "pid", "start": "started", "boot": "boot_id"}[damage]
            value["peer"][key] = "foreign" if damage == "boot" else value["peer"][key] + 1
        elif damage == "release":
            value["release_uid"] += 1
        elif damage == "identity":
            value["identity"]["connector_id"] = "foreign"
        elif damage in ("uid", "gid"):
            value["account"][damage] += 1
        elif damage == "extra":
            value["acknowledged"] = True
        data = b"private-unknown-record" if damage == "malformed" else json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
        path = fixture.file(PIN, data)
        if damage == "mode":
            path.chmod(0o644)
        elif damage == "hardlink":
            os.link(path, fixture.root / "foreign-peer-record")
        elif damage == "symlink":
            path.rename(fixture.root / "foreign-peer-record")
            path.symlink_to(fixture.root / "foreign-peer-record")
        elif damage in ("fifo", "directory"):
            path.unlink()
            os.mkfifo(path, 0o600) if damage == "fifo" else path.mkdir(mode=0o700)
        before = fixture.snapshot()
        if damage in ("pid", "start"):
            # Loading preserves a well-formed saved original without current
            # process discovery. It is not a live-peer or cutoff observation.
            saved = cutoff.RetirementCutoff.from_saved_peer(fixture.io, owner.fence, fixture.identity, os.getuid())
            assert saved.peer == cutoff.OriginalPeer(
                value["peer"]["pid"], value["peer"]["started"], BOOT)
            assert saved._acknowledged is None
        else:
            with pytest.raises(RetirementUncertain, match="^migration pending$"):
                cutoff.RetirementCutoff.from_saved_peer(fixture.io, owner.fence, fixture.identity, os.getuid())
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            owner.revoke()
        assert fixture.snapshot() == before
        assert not fixture.reopened()[0].devices[device].revoked
        assert not any(b'"method"' in frame for frame in fixture.frames)
        assert fixture.calls == []


@pytest.mark.parametrize("failure", ["short-write", "file-sync", "parent-sync"])
def test_interrupted_peer_publication_cannot_send_request(fixture, monkeypatch, failure):
    device, _ = fixture.grant("device")
    real_write, real_sync = os.write, os.fsync
    with fixture.active(monkeypatch) as owner:
        def write(fd, data):
            return real_write(fd, data[:4]) if failure == "short-write" else real_write(fd, data)
        def sync(fd):
            mode = os.fstat(fd).st_mode
            if (failure == "file-sync" and stat.S_ISREG(mode)
                    or failure == "parent-sync" and stat.S_ISDIR(mode)):
                raise OSError("synthetic-private-sync-failure")
            return real_sync(fd)
        monkeypatch.setattr(cutoff.os, "write", write)
        monkeypatch.setattr(cutoff.os, "fsync", sync)
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            owner.revoke()
        assert not any(b'"method"' in frame for frame in fixture.frames), "publication failure sent a control request"
        assert owner._acknowledged is None
        assert not fixture.reopened()[0].devices[device].revoked
        path = fixture.root / PIN
        original = cutoff._signature(path.stat()), path.read_bytes()
        monkeypatch.setattr(cutoff.os, "write", real_write)
        monkeypatch.setattr(cutoff.os, "fsync", real_sync)
        fresh = cutoff.RetirementCutoff(fixture.io, owner.fence, fixture.identity, os.getuid(),
            original_peer=cutoff.OriginalPeer(fixture.pid, 654, BOOT))
        if failure == "short-write":
            with pytest.raises(RetirementUncertain, match="^migration pending$"):
                fresh.revoke()
            assert (cutoff._signature(path.stat()), path.read_bytes()) == original
            assert not fixture.reopened()[0].devices[device].revoked
        else:
            # A complete protected pin is revalidated and synchronized, then
            # actual original-owner revocation may complete. Intent is no ack.
            fresh.revoke().require()
            assert fixture.reopened()[0].devices[device].revoked
            assert (cutoff._signature(path.stat()), path.read_bytes()) == original
        assert fixture.calls == []


@pytest.mark.parametrize("change", ["missing", "same-bytes", "new-boot"])
def test_live_cutoff_receipt_cannot_recreate_or_adopt_changed_peer_pin(fixture, monkeypatch, change):
    fixture.grant("device")
    with fixture.active(monkeypatch) as owner:
        receipt = owner.revoke()
        path = fixture.root / PIN
        if change == "missing":
            path.unlink()
        elif change == "same-bytes":
            replacement = path.with_name("replacement")
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(path)
        else:
            boot = fixture.root / "proc/sys/kernel/random/boot_id"
            boot.chmod(0o600)
            boot.write_bytes(b"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\n")
            boot.chmod(0o444)
        before = fixture.snapshot()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            receipt.require()
        assert fixture.snapshot() == before and fixture.calls == []


def test_new_object_cannot_adopt_reused_pid_start_on_another_boot(fixture, monkeypatch):
    fixture.grant("device")
    with fixture.active(monkeypatch) as owner:
        owner.revoke().require()
        fixture.frames.clear()
        new_boot = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
        boot = fixture.root / "proc/sys/kernel/random/boot_id"
        boot.chmod(0o600)
        boot.write_bytes((new_boot + "\n").encode())
        boot.chmod(0o444)
        replacement = cutoff.RetirementCutoff(fixture.io, owner.fence, fixture.identity, os.getuid(),
            original_peer=cutoff.OriginalPeer(fixture.pid, 654, new_boot))
        before = fixture.snapshot()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            replacement.revoke()
        assert fixture.snapshot() == before
        assert not any(b'"method"' in frame for frame in fixture.frames)
        assert replacement._acknowledged is None and fixture.calls == []


def test_pin_replacement_after_sync_refuses_before_first_request(fixture, monkeypatch):
    fixture.grant("device")
    real_sync = os.fsync
    swapped = []
    with fixture.active(monkeypatch) as owner:
        def sync(fd):
            real_sync(fd)
            info = os.fstat(fd)
            directory = (fixture.root / STATE).stat()
            if (info.st_dev, info.st_ino) == (directory.st_dev, directory.st_ino) and not swapped:
                path = fixture.root / PIN
                path.rename(path.with_name("original-peer"))
                path.write_bytes(path.with_name("original-peer").read_bytes())
                path.chmod(0o600)
                swapped.append(path.stat().st_ino)
        monkeypatch.setattr(cutoff.os, "fsync", sync)
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            owner.revoke()
        assert len(swapped) == 1
        assert not any(b'"method"' in frame for frame in fixture.frames)
        assert all(not device.revoked for device in fixture.reopened()[0].devices.values())
        assert fixture.calls == []


def test_saved_peer_load_keeps_original_identity_without_current_socket_discovery(fixture, monkeypatch):
    fixture.grant("device")
    with fixture.active(monkeypatch) as owner:
        before = fixture.snapshot()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            cutoff.RetirementCutoff.from_saved_peer(fixture.io, owner.fence, fixture.identity, os.getuid())
        assert fixture.snapshot() == before and fixture.frames == []
        owner.revoke().require()
        boot = fixture.root / "proc/sys/kernel/random/boot_id"
        boot.chmod(0o600)
        boot.write_bytes(b"aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee\n")
        boot.chmod(0o444)
        before = fixture.snapshot()
        def no_socket(*args, **kwargs):
            raise AssertionError("saved-peer loading must not discover a replacement socket")
        monkeypatch.setattr(cutoff.socket, "socket", no_socket)
        fresh = cutoff.RetirementCutoff.from_saved_peer(fixture.io, owner.fence, fixture.identity, os.getuid())
        assert fresh.peer == cutoff.OriginalPeer(fixture.pid, 654, BOOT)
        assert fresh._acknowledged is None
        assert fixture.snapshot() == before and fixture.calls == []


def test_saved_peer_load_pins_inode_across_later_native_connection(fixture, monkeypatch):
    fixture.grant("device")
    with fixture.active(monkeypatch) as owner:
        owner.revoke().require()
        fresh = cutoff.RetirementCutoff.from_saved_peer(fixture.io, owner.fence, fixture.identity, os.getuid())
        path = fixture.root / PIN
        replacement = path.with_name("replacement")
        replacement.write_bytes(path.read_bytes())
        replacement.chmod(0o600)
        replacement.replace(path)
        fixture.frames.clear()
        before = fixture.snapshot()
        with pytest.raises(RetirementUncertain, match="^migration pending$"):
            fresh.revoke()
        assert fixture.snapshot() == before
        assert not any(b'"method"' in frame for frame in fixture.frames)
        assert fresh._acknowledged is None and fixture.calls == []
