"""Standalone connector trust-ceremony checks; synthetic owner-only state only."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.remote_access.authorization import DeviceAuthorization, TrustState
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.policy import canonical_json
from runtime.remote_access.state_store import AtomicFileTrustStateStore, CorruptTrustStateError

NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)
IDENTITY = ConnectorIdentity(tenant_id="tenant", home_id="home", connector_id="connector")


def store_at(path: Path) -> AtomicFileTrustStateStore:
    return AtomicFileTrustStateStore(path, TrustState(IDENTITY, 0, 0))


@pytest.mark.parametrize("mode", [0o755, 0o775])
def test_owned_legacy_parent_is_tightened(tmp_path: Path, mode: int) -> None:
    from runtime.remote_access.state_store import StateStoreError
    parent = tmp_path / "state"
    parent.mkdir(mode=mode)
    parent.chmod(mode)
    path = parent / "trust.json"
    try:
        state = store_at(path).load()
    except StateStoreError:
        state = None
    assert state is not None, "owned legacy parent must load on first run"
    assert parent.stat().st_mode & 0o7777 == 0o700
    store_at(path).save(state)
    parent.chmod(mode)
    assert store_at(path).load().connector_identity == IDENTITY
    assert parent.stat().st_mode & 0o7777 == 0o700

    # Compatibility tightening cannot repair an alias or an unsafe leaf file.
    alias = tmp_path / "alias"
    alias.symlink_to(parent, target_is_directory=True)
    parent.chmod(mode)
    with pytest.raises(StateStoreError):
        store_at(alias / "trust.json").load()
    assert parent.stat().st_mode & 0o7777 == mode
    parent.chmod(0o700)
    path.chmod(0o644)
    with pytest.raises(StateStoreError):
        store_at(path).load()
    assert path.stat().st_mode & 0o7777 == 0o644
    non_directory = tmp_path / "file-parent"
    non_directory.write_text("synthetic")
    with pytest.raises(StateStoreError):
        store_at(non_directory / "trust.json").load()
    assert non_directory.read_text() == "synthetic"


def rewrite_payload(path: Path, change) -> None:
    envelope = json.loads(path.read_bytes())
    change(envelope)
    envelope["digest"] = hashlib.sha256(canonical_json(envelope["payload"])).hexdigest()
    raw = canonical_json(envelope)
    path.write_bytes(raw)
    anchor_path = Path(str(path) + ".anchor")
    anchor = json.loads(anchor_path.read_bytes())
    anchor["snapshot_digest"] = hashlib.sha256(raw).hexdigest()
    del anchor["digest"]
    anchor["digest"] = hashlib.sha256(canonical_json(anchor)).hexdigest()
    anchor_path.write_bytes(canonical_json(anchor))


@pytest.mark.parametrize("case", ["missing", "unknown", "mistyped", "duplicate", "torn", "rollback"])
def test_strict_anchored_state(tmp_path: Path, case: str) -> None:
    path = tmp_path / "trust.json"
    store = store_at(path)
    store.save(store.load())
    old = path.read_bytes()
    if case == "missing":
        rewrite_payload(path, lambda e: e["payload"].pop("pending_approval"))
    elif case == "unknown":
        rewrite_payload(path, lambda e: e["payload"].update(surprise=True))
    elif case == "mistyped":
        rewrite_payload(path, lambda e: e["payload"].update(invite_record={
            "secret_digest": "a" * 64, "generation": True, "expires_at": NOW.isoformat()}))
    elif case == "duplicate":
        path.write_bytes(old.replace(b'"version":3', b'"version":3,"version":3'))
    elif case == "torn":
        path.write_bytes(old[:len(old)//2])
    else:
        store.save(store.load())
        path.write_bytes(old)
    with pytest.raises(CorruptTrustStateError):
        store_at(path).load()


def test_legacy_unpinned_loads_for_repair(tmp_path: Path) -> None:
    path = tmp_path / "trust.json"
    store = store_at(path)
    state = store.load()
    state.apply_pairing(DeviceAuthorization("old", "tenant", "home", 1, NOW + timedelta(days=1)))
    store.save(state)
    def legacy(envelope):
        envelope["version"] = 2
        for key in ("invite_record", "pending_approval", "delivery"):
            del envelope["payload"][key]
        for device in envelope["payload"]["devices"].values():
            device.pop("control_plane_id", None)
            device.pop("stable_node_id", None)
    rewrite_payload(path, legacy)
    device = store_at(path).load().devices["old"]
    assert device.re_pair_required
    assert device.stable_node_id is None


def test_base_written_33_diy_devices_remain_usable(tmp_path: Path) -> None:
    import io
    import subprocess
    import sys
    import tarfile
    from runtime.remote_access.identity import DeviceProof
    from runtime.remote_access.pairing import PairingManager
    from runtime.remote_access.state_store import StateStoreError

    # Use the actual pre-slice production writer, not a JSON migration imitation.
    base = "768163ad32e1442f79589ebb7f0dd494d64501c9"
    source = tmp_path / "base-source"
    source.mkdir()
    archive = subprocess.run(
        ["git", "archive", base, "runtime/__init__.py", "runtime/remote_access"],
        check=True, capture_output=True,
    )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
        bundle.extractall(source, filter="data")
    path = tmp_path / "state" / "trust.json"
    writer = '''
from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch
from runtime.remote_access.authorization import TrustState
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.pairing import PairingManager
from runtime.remote_access.state_store import AtomicFileTrustStateStore
now = datetime(2026, 11, 16, tzinfo=timezone.utc)
identity = ConnectorIdentity(tenant_id="tenant", home_id="home", connector_id="connector")
store = AtomicFileTrustStateStore(Path(sys.argv[1]), TrustState(identity, 0, 0))
manager = PairingManager(state_store=store, identity=identity, now_fn=lambda: now)
with patch("runtime.remote_access.pairing.generate_device_credential",
           side_effect=[f"hrpair_{i:032x}" for i in range(33)]):
    for i in range(33):
        issued = manager.issue_pairing_code(f"legacy-{i}")
        assert manager.redeem_pairing(issued.code) is not None
assert len(store.load().devices) == 33
'''
    written = subprocess.run([sys.executable, "-c", writer, str(path)], cwd=source, capture_output=True)
    assert written.returncode == 0, "actual base PairingManager fixture writer failed"
    assert json.loads(path.read_bytes())["version"] == 2
    try:
        state = store_at(path).load()
    except StateStoreError:
        state = None
    assert state is not None, "33 base-written DIY devices must remain loadable"
    assert len(state.devices) == 33
    diy = PairingManager(state_store=store_at(path), identity=IDENTITY, now_fn=lambda: NOW)
    managed = manager_at(path)
    assert managed.status()["re_pair_required_count"] == 33
    for index in range(33):
        credential = f"hrpair_{index:032x}"
        device = diy.find_device_by_credential(credential)
        assert device is not None and device.device_id == f"legacy-{index}"
        proof = DeviceProof(device_id=device.device_id, tenant_id="tenant", home_id="home",
                            nonce="synthetic", issued_at=NOW, expires_at=NOW + timedelta(minutes=1))
        assert diy.credential_verifier().verify(proof, IDENTITY, NOW).ok
        assert not managed.admit(credential, f"node-{index}", "control", NOW).ok
    # Historical unpinned records do not spend the managed grant budget.
    device_id, credential = grant_for(managed)
    assert managed.admit(credential, "node-a", "control", NOW).ok
    assert len(store_at(path).load().devices) == 34
    assert diy.revoke("legacy-0").complete
    assert diy.find_device_by_credential(f"hrpair_{0:032x}") is None
    diy.remove_device("legacy-1")
    state = store_at(path).load()
    assert state.devices["legacy-0"].revoked
    assert "legacy-1" not in state.devices
    assert diy.find_device_by_credential(f"hrpair_{2:032x}") is not None
    assert managed.status()["re_pair_required_count"] == 32


def manager_at(path: Path, registry=None, now_fn=None):
    from runtime.remote_access.pairing import PinnedPairingManager
    from runtime.remote_access.streams import StreamRegistry
    return PinnedPairingManager(state_store=store_at(path), identity=IDENTITY,
                                registry=registry or StreamRegistry(), now_fn=now_fn or (lambda: NOW))


def pending_for(manager, node="node-a", secret="synthetic-invite"):
    manager.issue_invite_record(hashlib.sha256(secret.encode()).hexdigest(), NOW + timedelta(minutes=5))
    pending = manager.redeem(secret, node, "control", NOW)
    assert pending is not None
    return pending


def grant_for(manager, node="node-a", secret="synthetic-invite"):
    pending = pending_for(manager, node, secret)
    device_id = manager.approve_pending(pending.generation, ("control", node))
    assert device_id is not None
    credential = manager.complete(pending.completion_handle, node, "control", NOW)
    assert credential is not None
    return device_id, credential


def test_concurrent_redeemers_one_durable_pending(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    path = tmp_path / "trust.json"
    # Two connector-core owners initialized before issuance; only the file lock
    # serializes them. No shared manager lock can manufacture the winner.
    managers = [manager_at(path), manager_at(path)]
    managers[0].issue_invite_record(hashlib.sha256(b"synthetic-invite").hexdigest(), NOW + timedelta(minutes=5))
    barrier = Barrier(2)
    def redeem(index):
        barrier.wait(timeout=5)
        return managers[index].redeem("synthetic-invite", f"node-{index}", "control", NOW)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(redeem, range(2)))
    assert sum(result is not None for result in results) == 1
    state = store_at(path).load()
    assert state.invite_record is None
    assert state.pending_approval is not None
    winner = next(i for i, result in enumerate(results) if result is not None)
    assert state.pending_approval.stable_node_id == f"node-{winner}"
    assert state.devices == {}


def test_concurrent_approvals_one_durable_grant(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    path = tmp_path / "trust.json"
    managers = [manager_at(path), manager_at(path)]
    pending = pending_for(managers[0])
    barrier = Barrier(2)
    def approve(index):
        barrier.wait(timeout=5)
        return managers[index].approve_pending(pending.generation, ("control", "node-a"))
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(approve, range(2)))
    assert sum(result is not None for result in results) == 1
    state = store_at(path).load()
    assert len(state.devices) == 1
    assert state.pending_approval is None
    assert state.delivery.device_id == next(result for result in results if result is not None)
    winner = next(i for i, result in enumerate(results) if result is not None)
    credential = managers[winner].complete(pending.completion_handle, "node-a", "control", NOW)
    assert credential is not None
    assert store_at(path).load().devices[state.delivery.device_id].credential_digest == hashlib.sha256(credential.encode()).hexdigest()


@pytest.mark.parametrize("case", ["wrong-node", "wrong-control", "epoch", "expired", "revoked", "legacy"])
def test_admission_refusals(tmp_path: Path, case: str) -> None:
    from dataclasses import replace
    path = tmp_path / "trust.json"
    manager = manager_at(path)
    device_id, credential = grant_for(manager)
    assert manager.admit(credential, "node-a", "control", NOW).ok
    node, control, now = "node-a", "control", NOW
    if case == "wrong-node":
        node = "node-b"
    elif case == "wrong-control":
        control = "other"
    elif case == "epoch":
        with store_at(path).transaction() as tx:
            device = tx.state.devices[device_id]
            tx.state.pairing_epoch += 1
            tx.state.devices[device_id] = replace(device, authorization_epoch=tx.state.pairing_epoch)
    elif case == "expired":
        now = NOW + timedelta(days=365)
    elif case == "revoked":
        manager.revoke_device(device_id)
    else:
        with store_at(path).transaction() as tx:
            tx.state.devices[device_id] = replace(tx.state.devices[device_id], control_plane_id=None, stable_node_id=None)
        assert manager.status()["re_pair_required_count"] == 1
    assert not manager.admit(credential, node, control, now).ok


@pytest.mark.parametrize("case", ["generation", "tuple", "rejected", "expired", "second-pending", "replacement"])
def test_captured_approval_and_invite_refusals(tmp_path: Path, case: str) -> None:
    path = tmp_path / "trust.json"
    clock = [NOW]
    manager = manager_at(path, now_fn=lambda: clock[0])
    pending = pending_for(manager)
    generation, pin = pending.generation, ("control", "node-a")
    if case == "generation":
        generation += 1
    elif case == "tuple":
        pin = ("control", "node-b")
    elif case == "rejected":
        assert not manager.reject_pending(generation + 1, pin)
        assert not manager.reject_pending(generation, ("control", "node-b"))
        assert manager.reject_pending(generation, pin)
    elif case == "expired":
        clock[0] += timedelta(minutes=5)
    elif case == "second-pending":
        assert manager.redeem("synthetic-invite", "node-b", "control", NOW) is None
        assert store_at(path).load().pending_approval.stable_node_id == "node-a"
        return
    else:
        manager.issue_invite_record(hashlib.sha256(b"replacement").hexdigest(), NOW + timedelta(minutes=5))
    assert manager.approve_pending(generation, pin) is None
    assert store_at(path).load().devices == {}
    assert manager.redeem("synthetic-invite", "node-a", "control", NOW) is None


@pytest.mark.parametrize("phase", ["invite", "pending", "delivery"])
def test_restart_burns_unfinished_authority(tmp_path: Path, phase: str) -> None:
    path = tmp_path / "trust.json"
    manager = manager_at(path)
    if phase == "invite":
        manager.issue_invite_record(hashlib.sha256(b"synthetic-invite").hexdigest(), NOW + timedelta(minutes=5))
        pending = None
    else:
        pending = pending_for(manager)
        if phase == "delivery":
            assert manager.approve_pending(pending.generation, ("control", "node-a")) is not None
    restarted = manager_at(path)
    state = store_at(path).load()
    assert state.invite_record is state.pending_approval is state.delivery is None
    assert restarted.redeem("synthetic-invite", "node-a", "control", NOW) is None
    if pending is not None:
        assert manager.complete(pending.completion_handle, "node-a", "control", NOW) is None
    assert all(device.revoked for device in state.devices.values())


def test_completion_binding_commit_and_secret_free_status(tmp_path: Path) -> None:
    path = tmp_path / "trust.json"
    manager = manager_at(path)
    pending = pending_for(manager)
    summary = manager.status()
    assert summary["pending"]["comparison_phrase"] == pending.comparison_phrase
    assert summary["pending"]["tuple"] == ("control", "node-a")
    assert pending.completion_handle not in repr(pending)
    assert manager.complete(pending.completion_handle, "node-a", "control", NOW) is None
    device_id = manager.approve_pending(pending.generation, ("control", "node-a"))
    assert manager.complete("b" * 64, "node-a", "control", NOW) is None
    assert manager.complete(pending.completion_handle, "node-b", "control", NOW) is None
    assert manager.complete(pending.completion_handle, "node-a", "other", NOW) is None
    state = store_at(path).load()
    assert state.devices[device_id].credential_digest is not None
    credential = manager.complete(pending.completion_handle, "node-a", "control", NOW)
    assert credential is not None
    assert manager.complete(pending.completion_handle, "node-a", "control", NOW) is None
    assert manager.admit(credential, "node-a", "control", NOW).ok
    raw = path.read_text() + json.dumps(manager.status())
    for secret in ("synthetic-invite", pending.completion_handle, credential, state.devices[device_id].credential_digest):
        # The credential DIGEST belongs in the store but never status.
        if secret == state.devices[device_id].credential_digest:
            assert secret not in json.dumps(manager.status())
        else:
            assert secret not in raw


@pytest.mark.parametrize("case", ["expired", "bad-identity", "corrupt", "capacity"])
def test_redemption_denial_never_restores_invite(tmp_path: Path, case: str) -> None:
    path = tmp_path / "trust.json"
    manager = manager_at(path)
    if case == "capacity":
        for index in range(32):
            grant_for(manager, f"node-{index}", f"synthetic-{index}")
    manager.issue_invite_record(hashlib.sha256(b"synthetic-invite").hexdigest(), NOW + timedelta(minutes=5))
    now = NOW + timedelta(minutes=5) if case == "expired" else NOW
    node = "" if case == "bad-identity" else "node-a"
    if case == "corrupt":
        path.write_bytes(b'{"torn":')
    if case == "capacity":
        pending = manager.redeem("synthetic-invite", node, "control", now)
        assert pending is not None, "capacity is enforced at managed approval"
        assert manager.approve_pending(pending.generation, ("control", node)) is None
    else:
        assert manager.redeem("synthetic-invite", node, "control", now) is None
    assert manager.redeem("synthetic-invite", "node-a", "control", now) is None
    if case == "corrupt":
        with pytest.raises(CorruptTrustStateError):
            store_at(path).load()
        assert path.read_bytes() == b'{"torn":'
    else:
        state = store_at(path).load()
        assert state.invite_record is None
        assert state.pending_approval is None
        assert len(state.devices) == (32 if case == "capacity" else 0)


class Handle:
    def __init__(self, stream_id, close_fn=None):
        self.stream_id, self.closed, self.close_fn = stream_id, False, close_fn
    def receive(self):
        return b"working"
    def close(self):
        if self.close_fn:
            self.close_fn()
        self.closed = True


@pytest.mark.parametrize("fails", [False, True])
def test_revoke_closes_only_device_and_persists_first(tmp_path: Path, fails: bool) -> None:
    from runtime.remote_access.revocation import RevocationIncomplete
    from runtime.remote_access.streams import StreamClosed, StreamRegistry
    path = tmp_path / "trust.json"
    registry = StreamRegistry()
    manager = manager_at(path, registry)
    device_a, bearer_a = grant_for(manager)
    device_b, bearer_b = grant_for(manager, "node-b", "synthetic-b")
    epoch_a = manager.admit(bearer_a, "node-a", "control", NOW).authorization_epoch
    epoch_b = manager.admit(bearer_b, "node-b", "control", NOW).authorization_epoch
    def close_a():
        assert store_at(path).load().devices[device_a].revoked  # real current anchored state
        if fails:
            raise OSError("synthetic cleanup failure")
    stream_a = registry.open("a", Handle("a", close_a), device_id=device_a, epoch=epoch_a)
    stream_b = registry.open("b", Handle("b"), device_id=device_b, epoch=epoch_b)
    assert stream_a.device_id == device_a and stream_a.authorization_epoch == epoch_a
    if fails:
        with pytest.raises(RevocationIncomplete):
            manager.revoke_device(device_a)
        with pytest.raises(RevocationIncomplete):
            manager.revoke_device(device_a)
    else:
        assert manager.revoke_device(device_a).complete
    assert store_at(path).load().devices[device_a].revoked
    assert not manager.admit(bearer_a, "node-a", "control", NOW).ok
    assert manager.admit(bearer_b, "node-b", "control", NOW).ok
    with pytest.raises(StreamClosed):
        stream_a.receive()
    assert stream_b.receive() == b"working"
    with pytest.raises(StreamClosed):
        registry.open("stale", Handle("stale"), device_id=device_a, epoch=epoch_a)


@pytest.mark.parametrize("case", ["pending-missing", "pending-unknown", "pin-missing", "pin-half", "device-limit", "generation-rollback", "pending-list-null", "identity-invalid", "pinned-digest-null"])
def test_nested_state_shape_and_grant_capacity(tmp_path: Path, case: str) -> None:
    path = tmp_path / "trust.json"
    manager = manager_at(path)
    pending = pending_for(manager)
    if case == "pending-list-null":
        def change(envelope):
            envelope["payload"]["pending_pairings"] = None
    elif case == "identity-invalid":
        def change(envelope):
            envelope["payload"]["connector_identity"]["home_id"] = ""
    elif case.startswith("pending"):
        def change(envelope):
            record = envelope["payload"]["pending_approval"]
            if case == "pending-missing":
                record.pop("handle_digest")
            else:
                record["caller_path"] = "/synthetic"
    elif case == "generation-rollback":
        def change(envelope):
            envelope["payload"]["pending_approval"]["generation"] = envelope["generation"] + 1
    else:
        device_id = manager.approve_pending(pending.generation, ("control", "node-a"))
        assert manager.complete(pending.completion_handle, "node-a", "control", NOW) is not None
        def change(envelope):
            devices = envelope["payload"]["devices"]
            if case == "pinned-digest-null":
                devices[device_id]["credential_digest"] = None
            elif case == "pin-missing":
                devices[device_id].pop("stable_node_id")
            elif case == "pin-half":
                devices[device_id]["stable_node_id"] = None
            else:
                for index in range(32):
                    copied = dict(devices[device_id])
                    copied["device_id"] = f"synthetic-{index}"
                    devices[copied["device_id"]] = copied
    rewrite_payload(path, change)
    if case == "device-limit":
        from runtime.remote_access.state_store import StateStoreError
        try:
            state = store_at(path).load()
        except StateStoreError:
            state = None
        assert state is not None, "record count must not make a snapshot corrupt"
        assert len(state.devices) == 33
        pending = pending_for(manager, "overflow", "synthetic-overflow")
        assert manager.approve_pending(pending.generation, ("control", "overflow")) is None
        assert len(store_at(path).load().devices) == 33
        return
    with pytest.raises(CorruptTrustStateError):
        store_at(path).load()


def test_completed_grants_survive_restart_and_delivery_expiry_burns(tmp_path: Path) -> None:
    path = tmp_path / "trust.json"
    manager = manager_at(path)
    _, bearer = grant_for(manager)
    restarted = manager_at(path)
    assert restarted.admit(bearer, "node-a", "control", NOW).ok
    pending = pending_for(restarted, "node-b", "synthetic-b")
    device_id = restarted.approve_pending(pending.generation, ("control", "node-b"))
    assert restarted.complete(pending.completion_handle, "node-b", "control", NOW + timedelta(minutes=5)) is None
    state = store_at(path).load()
    assert state.delivery is None and state.devices[device_id].revoked
    assert restarted.admit(bearer, "node-a", "control", NOW).ok


def test_device_revoke_waits_for_close_and_fences_racing_admission(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from runtime.remote_access.streams import StreamClosed, StreamRegistry
    path = tmp_path / "trust.json"
    registry = StreamRegistry()
    manager = manager_at(path, registry)
    device_id, bearer = grant_for(manager)
    epoch = manager.admit(bearer, "node-a", "control", NOW).authorization_epoch
    entered, release = Event(), Event()
    def close():
        entered.set()
        assert release.wait(5)
    stream = registry.open("live", Handle("live", close), device_id=device_id, epoch=epoch)
    with ThreadPoolExecutor(1) as pool:
        revoked = pool.submit(manager.revoke_device, device_id)
        try:
            assert entered.wait(5)
            assert stream.closed
            assert not revoked.done()
            with pytest.raises(StreamClosed):
                registry.open("racer", Handle("racer"), device_id=device_id, epoch=epoch)
        finally:
            release.set()
        assert revoked.result(timeout=5).complete
    assert store_at(path).load().devices[device_id].revoked
