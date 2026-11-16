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


@pytest.mark.parametrize("case", ["pending-missing", "pending-unknown", "pin-missing", "pin-half", "device-limit", "generation-rollback"])
def test_nested_state_shape_refuses(tmp_path: Path, case: str) -> None:
    path = tmp_path / "trust.json"
    manager = manager_at(path)
    pending = pending_for(manager)
    if case.startswith("pending"):
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
            if case == "pin-missing":
                devices[device_id].pop("stable_node_id")
            elif case == "pin-half":
                devices[device_id]["stable_node_id"] = None
            else:
                for index in range(32):
                    copied = dict(devices[device_id])
                    copied["device_id"] = f"synthetic-{index}"
                    devices[copied["device_id"]] = copied
    rewrite_payload(path, change)
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
