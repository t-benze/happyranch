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
