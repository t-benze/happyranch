"""Atomic, corruption-detecting, owner-only local trust-state store
(THR-097 phase unit 3, contract §13).

The connector's durable trust state (pairing/revocation epochs, devices,
current device) must survive a service restart so revocation stays effective
across restarts. The store satisfies the already-approved ``TrustStateStore``
protocol with a schema-agnostic, versioned, non-normative envelope: atomic
replace + fsync, owner-only permissions, sha256 corruption detection, and
fail-closed rejection of any present-but-corrupt/loose/unreadable state. It
is NOT the founder-gated managed persistent schema (no database, no
migration machinery); it is the local recovery aid contract §13 permits,
explicitly replaceable by the future founder-gated store.

Fixed invariants exercised here:

- a present-but-corrupt state (digest flip, malformed JSON, unknown version,
  unknown payload keys, wrong types, naive datetimes, symlinked path) fails
  closed — never silently treated as a valid earlier epoch (corruption could
  erase a revocation);
- missing state is a first-run default (fresh deny-all), never corruption;
- revocation applied before a restart is still enforced after the store is
  re-loaded (revocation across restart);
- the envelope never carries the daemon bearer or any credential-shaped
  material (the store holds authorization state only).
"""
from __future__ import annotations

import hashlib
import json
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.remote_access.authorization import DeviceAuthorization, TrustState
from runtime.remote_access.identity import ConnectorIdentity
from runtime.remote_access.revocation import RevocationCoordinator
from runtime.remote_access.state_store import (
    AtomicFileTrustStateStore,
    CorruptTrustStateError,
    StateStoreError,
)
from runtime.remote_access.streams import StreamRegistry

from .conftest import NOW, default_identity


def _fresh_state() -> TrustState:
    state = TrustState(
        connector_identity=default_identity(),
        pairing_epoch=0,
        revocation_epoch=0,
    )
    return state


def _paired_state() -> TrustState:
    state = _fresh_state()
    state.apply_pairing(
        DeviceAuthorization(
            device_id="device-a",
            tenant_id="tenant-a",
            home_id="home-a",
            authorization_epoch=1,
            expires_at=NOW() + timedelta(days=30),
        )
    )
    return state


def _envelope_bytes(state: TrustState) -> bytes:
    """The exact envelope the store writes for *state* (used to corrupt it)."""
    store = AtomicFileTrustStateStore(Path("/unused"), _fresh_state())
    return store._render(state)


class TestRoundTrip:
    def test_roundtrip_preserves_full_state(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        loaded = store.load()
        assert loaded.connector_identity == default_identity()
        assert loaded.pairing_epoch == 1
        assert loaded.revocation_epoch == 0
        assert loaded.current_device_id == "device-a"
        device = loaded.devices["device-a"]
        assert device.device_id == "device-a"
        assert device.tenant_id == "tenant-a"
        assert device.home_id == "home-a"
        assert device.authorization_epoch == 1
        assert device.expires_at == NOW() + timedelta(days=30)
        assert device.revoked is False

    def test_roundtrip_preserves_identity_none(self, tmp_path) -> None:
        state = TrustState(connector_identity=None, pairing_epoch=0, revocation_epoch=0)
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(state)
        assert store.load().connector_identity is None

    def test_overwrite_is_atomic_and_visible(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        second = _fresh_state()
        second.apply_pairing(
            DeviceAuthorization(
                device_id="device-b",
                tenant_id="tenant-a",
                home_id="home-a",
                authorization_epoch=2,
                expires_at=NOW() + timedelta(days=30),
            )
        )
        store.save(second)
        loaded = store.load()
        assert loaded.current_device_id == "device-b"
        assert loaded.pairing_epoch == 2


class TestMissingState:
    def test_missing_file_returns_default(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        loaded = store.load()
        assert loaded.connector_identity == default_identity()
        assert loaded.devices == {}
        assert loaded.revocation_epoch == 0

    def test_missing_state_never_counts_as_corruption(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        assert store.load().revocation_epoch == 0  # no raise


class TestFailClosedCorruption:
    def test_digest_flip_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["payload"]["revocation_epoch"] = 0  # corrupt: erase a revocation
        # re-sign with a WRONG digest (flip a hex char) — the envelope now
        # claims integrity but the digest does not match.
        digest = envelope["digest"]
        flipped = ("0" if digest[0] != "0" else "1") + digest[1:]
        envelope["digest"] = flipped
        path.write_text(json.dumps(envelope))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_malformed_json_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        path.write_text("{ not json !!!")
        path.chmod(0o600)
        store = AtomicFileTrustStateStore(path, _fresh_state())
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_non_object_envelope_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        path.write_text('["not", "an", "object"]')
        path.chmod(0o600)
        store = AtomicFileTrustStateStore(path, _fresh_state())
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_unsupported_version_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["version"] = 2
        path.write_text(json.dumps(envelope))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_kind_mismatch_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["kind"] = "something-else"
        path.write_text(json.dumps(envelope))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_unknown_payload_key_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["payload"]["privilege_escalation"] = True
        path.write_text(json.dumps(envelope))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_bool_epoch_fails_closed(self, tmp_path) -> None:
        """isinstance(True, int) is True — a bool epoch must be rejected."""
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["payload"]["revocation_epoch"] = True
        path.write_text(json.dumps(envelope))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_string_epoch_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["payload"]["pairing_epoch"] = "1"
        path.write_text(json.dumps(envelope))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_naive_datetime_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        envelope = json.loads(path.read_text(encoding="utf-8"))
        device = envelope["payload"]["devices"]["device-a"]
        device["expires_at"] = "2026-09-26T12:00:00"  # no timezone
        path.write_text(json.dumps(envelope))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_unparseable_datetime_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["payload"]["devices"]["device-a"]["expires_at"] = "not-a-date"
        path.write_text(json.dumps(envelope))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_missing_required_payload_key_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        envelope = json.loads(path.read_text(encoding="utf-8"))
        del envelope["payload"]["devices"]
        path.write_text(json.dumps(envelope))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_device_key_mismatch_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["payload"]["devices"]["device-b"] = envelope["payload"]["devices"].pop(
            "device-a"
        )
        path.write_text(json.dumps(envelope))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_current_device_not_in_devices_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["payload"]["current_device_id"] = "device-ghost"
        path.write_text(json.dumps(envelope))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_identity_extra_key_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["payload"]["connector_identity"]["admin"] = True
        path.write_text(json.dumps(envelope))
        with pytest.raises(CorruptTrustStateError):
            store.load()


class TestPermissionsAndFilesystem:
    def test_save_creates_owner_only_file(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & 0o077 == 0  # owner-only
        assert mode & 0o600 == 0o600  # readable+writable by owner

    def test_save_tightens_parent_directory(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        mode = stat.S_IMODE(tmp_path.stat().st_mode)
        assert mode & 0o077 == 0

    def test_loose_permissions_fail_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        path.chmod(0o644)  # world-readable
        with pytest.raises(StateStoreError):
            store.load()

    def test_symlinked_state_file_fails_closed(self, tmp_path) -> None:
        target = tmp_path / "target.json"
        target.write_text("{}")
        link = tmp_path / "state.json"
        link.symlink_to(target)
        store = AtomicFileTrustStateStore(link, _fresh_state())
        with pytest.raises(StateStoreError):
            store.load()

    def test_directory_at_state_path_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        path.mkdir()
        store = AtomicFileTrustStateStore(path, _fresh_state())
        with pytest.raises(StateStoreError):
            store.load()

    def test_save_leaves_no_temp_file(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_save_creates_missing_parent(self, tmp_path) -> None:
        path = tmp_path / "deep" / "nested" / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        assert path.is_file()
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & 0o077 == 0


class TestRevocationAcrossRestart:
    def test_revocation_survives_store_reload(self, tmp_path) -> None:
        """The deterministic revocation-across-restart test at the store seam:
        revoke via the authoritative coordinator, persist, then reload into a
        NEW store instance (a fresh process) and prove the device is still
        denied."""
        state = _paired_state()
        coordinator = RevocationCoordinator(state, StreamRegistry())
        coordinator.revoke(epoch=2)
        assert state.devices["device-a"].revoked is True
        assert state.revocation_epoch == 2

        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(state)

        # Simulate a restart: a brand-new store instance in a new process.
        restarted = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        loaded = restarted.load()
        assert loaded.revocation_epoch == 2
        assert loaded.devices["device-a"].revoked is True

    def test_no_bearer_or_credential_shape_in_envelope(self, tmp_path) -> None:
        """The envelope is authorization state only — it must never carry the
        daemon bearer or bearer-shaped material (fixed invariant #5)."""
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        raw = (tmp_path / "state.json").read_text(encoding="utf-8")
        assert "Bearer" not in raw
        assert "token" not in raw.lower() or "daemon" not in raw.lower()
        assert "hrpair_" not in raw

    def test_render_is_deterministic(self) -> None:
        store = AtomicFileTrustStateStore(Path("/unused"), _fresh_state())
        assert store._render(_paired_state()) == store._render(_paired_state())
        assert hashlib.sha256(store._render(_paired_state())).hexdigest()
