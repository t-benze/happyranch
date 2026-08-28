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
from runtime.remote_access.policy import canonical_json
from runtime.remote_access.revocation import RevocationCoordinator
from runtime.remote_access.state_store import (
    ANCHOR_KIND,
    ANCHOR_VERSION,
    ENVELOPE_KIND,
    ENVELOPE_VERSION,
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


def _resign(path: Path, mutate) -> None:
    """Mutate the snapshot envelope, then re-sign BOTH the envelope and the
    companion anchor consistently (simulating a full-pair restore). The load()
    anchor bind checks therefore pass and only the payload/envelope validation
    layer is the gate under test."""
    env = json.loads(path.read_text(encoding="utf-8"))
    mutate(env)
    payload = env["payload"]
    env["digest"] = hashlib.sha256(canonical_json(payload)).hexdigest()
    env_bytes = json.dumps(
        env, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    path.write_bytes(env_bytes)
    anchor_payload = {
        "version": ANCHOR_VERSION,
        "kind": ANCHOR_KIND,
        "generation": env["generation"],
        "snapshot_digest": hashlib.sha256(env_bytes).hexdigest(),
    }
    anchor = dict(anchor_payload)
    anchor["digest"] = hashlib.sha256(canonical_json(anchor_payload)).hexdigest()
    Path(str(path) + ".anchor").write_bytes(
        json.dumps(
            anchor, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    )


def _anchor_path(state_path: Path) -> Path:
    return Path(str(state_path) + ".anchor")


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
        _resign(path, lambda env: env.__setitem__("version", ENVELOPE_VERSION + 1))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_kind_mismatch_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        _resign(path, lambda env: env.__setitem__("kind", "something-else"))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_unknown_payload_key_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        _resign(path, lambda env: env["payload"].__setitem__("privilege_escalation", True))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_bool_epoch_fails_closed(self, tmp_path) -> None:
        """isinstance(True, int) is True — a bool epoch must be rejected."""
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        _resign(path, lambda env: env["payload"].__setitem__("revocation_epoch", True))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_string_epoch_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        _resign(path, lambda env: env["payload"].__setitem__("pairing_epoch", "1"))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_naive_datetime_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        _resign(
            path,
            lambda env: env["payload"]["devices"]["device-a"].__setitem__(
                "expires_at", "2026-09-26T12:00:00"  # no timezone
            ),
        )
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_unparseable_datetime_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        _resign(
            path,
            lambda env: env["payload"]["devices"]["device-a"].__setitem__(
                "expires_at", "not-a-date"
            ),
        )
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_missing_required_payload_key_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        _resign(path, lambda env: env["payload"].__delitem__("devices"))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_device_key_mismatch_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        _resign(
            path,
            lambda env: env["payload"]["devices"].__setitem__(
                "device-b", env["payload"]["devices"].pop("device-a")
            ),
        )
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_current_device_not_in_devices_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        _resign(
            path, lambda env: env["payload"].__setitem__("current_device_id", "device-ghost")
        )
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_identity_extra_key_fails_closed(self, tmp_path) -> None:
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        path = tmp_path / "state.json"
        _resign(path, lambda env: env["payload"]["connector_identity"].__setitem__("admin", True))
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
        anchor_mode = stat.S_IMODE(_anchor_path(path).stat().st_mode)
        assert anchor_mode & 0o077 == 0  # the companion anchor is owner-only too

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
        """The envelope AND its companion anchor are authorization state only —
        they must never carry the daemon bearer or bearer-shaped material
        (fixed invariant #5)."""
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        store.save(_paired_state())
        raw = (tmp_path / "state.json").read_text(encoding="utf-8")
        raw_anchor = _anchor_path(tmp_path / "state.json").read_text(encoding="utf-8")
        for blob in (raw, raw_anchor):
            assert "Bearer" not in blob
            assert "token" not in blob.lower() or "daemon" not in blob.lower()
            assert "hrpair_" not in blob

    def test_render_is_deterministic(self) -> None:
        store = AtomicFileTrustStateStore(Path("/unused"), _fresh_state())
        assert store._render(_paired_state(), generation=7) == store._render(
            _paired_state(), generation=7
        )
        assert hashlib.sha256(store._render(_paired_state(), generation=7)).hexdigest()


class TestCompanionMonotonicAnchor:
    """The founder-approved (THR-097 seq163) non-database companion monotonic
    generation/digest anchor.

    The anchor lives OUTSIDE the replaceable snapshot (``<state>.anchor``,
    owner-only, atomic) and binds the snapshot by (a) an envelope generation
    that must equal the anchor's generation and (b) an anchor ``snapshot_digest``
    over the exact snapshot bytes. A previously valid OLDER snapshot replayed
    after a newer revocation/generation — including across a new store/process
    instance — is deterministically rejected. Missing/mismatched/corrupt/stale
    anchors, partial snapshot/anchor states, symlinks, and loose permissions all
    fail closed.

    Honest contract (consultant seq162 / manager seq165): a local companion
    anchor detects accidental or partial rollback (backup restore, half-copied
    state directory, botched upgrade) and naive replay across restart. It does
    NOT resist a determined actor able to replace BOTH local files; managed
    authoritative rollback protection is a Units 4-5 control-plane
    responsibility and is out of scope here.
    """

    def _revoked_then_replay(self, tmp_path):
        """Revoke to epoch 2, capture the old valid bytes, replay them, load."""
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        base = _paired_state()
        store.save(base)
        old_bytes = path.read_bytes()  # the OLD valid snapshot (generation 1)
        state = store.load()
        RevocationCoordinator(state, StreamRegistry()).revoke(epoch=2)
        store.save(state)  # generation 2
        assert store.load().revocation_epoch == 2
        path.write_bytes(old_bytes)  # replay the old snapshot only
        return store, path

    def test_old_snapshot_replay_after_revocation_is_rejected(self, tmp_path) -> None:
        """The reviewer's exact repro, now deterministic: replaying a valid
        older snapshot after a newer revocation must fail closed — never accept
        epoch 0 as current."""
        store, _path = self._revoked_then_replay(tmp_path)
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_old_snapshot_replay_across_new_store_instance_is_rejected(
        self, tmp_path
    ) -> None:
        """Replay is rejected even by a BRAND-NEW store instance (a fresh
        process): the anchor on disk is the monotonic authority."""
        store, path = self._revoked_then_replay(tmp_path)
        del store
        restarted = AtomicFileTrustStateStore(path, _fresh_state())
        with pytest.raises(CorruptTrustStateError):
            restarted.load()

    def test_anchor_survives_normal_reload_across_instances(self, tmp_path) -> None:
        """A NORMAL reload (no replay) still works across store instances."""
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        restarted = AtomicFileTrustStateStore(path, _fresh_state())
        assert restarted.load().current_device_id == "device-a"

    def test_missing_anchor_with_present_snapshot_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        _anchor_path(path).unlink()
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_missing_snapshot_with_present_anchor_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        path.unlink()
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_both_missing_is_first_run_default(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        assert store.load().revocation_epoch == 0
        assert store.load().devices == {}

    def test_corrupt_anchor_json_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        _anchor_path(path).write_text("{ not json !!!")
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_anchor_digest_flip_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        anchor_path = _anchor_path(path)
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        digest = anchor["digest"]
        anchor["digest"] = ("0" if digest[0] != "0" else "1") + digest[1:]
        anchor_path.write_text(json.dumps(anchor))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_anchor_version_unsupported_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        anchor_path = _anchor_path(path)
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        anchor["version"] = ANCHOR_VERSION + 1
        anchor_path.write_text(json.dumps(anchor))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_anchor_kind_mismatch_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        anchor_path = _anchor_path(path)
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        anchor["kind"] = "something-else"
        anchor_path.write_text(json.dumps(anchor))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_anchor_unknown_key_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        anchor_path = _anchor_path(path)
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        anchor["escalation"] = True
        anchor_path.write_text(json.dumps(anchor))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_anchor_bool_generation_fails_closed(self, tmp_path) -> None:
        """bool is an int subclass — a bool generation must be rejected."""
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        anchor_path = _anchor_path(path)
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        anchor["generation"] = True
        anchor_path.write_text(json.dumps(anchor))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_anchor_zero_generation_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        anchor_path = _anchor_path(path)
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        anchor["generation"] = 0
        anchor_path.write_text(json.dumps(anchor))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_anchor_snapshot_digest_wrong_type_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        anchor_path = _anchor_path(path)
        anchor = json.loads(anchor_path.read_text(encoding="utf-8"))
        anchor["snapshot_digest"] = 12345
        anchor_path.write_text(json.dumps(anchor))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_envelope_generation_mismatch_anchor_fails_closed(self, tmp_path) -> None:
        """A snapshot carrying a different generation than its anchor is a
        cross-generation pair (stale anchor / newer snapshot) — rejected."""
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        anchor = json.loads(_anchor_path(path).read_text(encoding="utf-8"))
        anchor["generation"] = 99
        anchor_payload = {
            "version": ANCHOR_VERSION,
            "kind": ANCHOR_KIND,
            "generation": 99,
            "snapshot_digest": anchor["snapshot_digest"],
        }
        anchor["digest"] = hashlib.sha256(canonical_json(anchor_payload)).hexdigest()
        _anchor_path(path).write_text(json.dumps(anchor))
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_anchor_snapshot_digest_mismatch_fails_closed(self, tmp_path) -> None:
        """A stale anchor whose snapshot_digest does not cover the present
        snapshot bytes is rejected (the reviewer's snapshot-only replay)."""
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        other = _fresh_state()  # different payload → different bytes
        store2 = AtomicFileTrustStateStore(tmp_path / "other.json", _fresh_state())
        store2.save(other)
        other_bytes = (tmp_path / "other.json").read_bytes()
        path.write_bytes(other_bytes)  # snapshot replaced, anchor untouched
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_partial_snapshot_only_update_fails_closed(self, tmp_path) -> None:
        """Crash between snapshot write and anchor write: the newer snapshot is
        paired with the older anchor — the pair must be rejected, never
        silently accepted as a stale state."""
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        new_envelope = store._render(_fresh_state(), generation=2)
        path.write_bytes(new_envelope)  # snapshot is newer; anchor stays gen 1
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_partial_anchor_only_update_fails_closed(self, tmp_path) -> None:
        """A restored newer anchor over an older snapshot is a cross-generation
        pair — rejected."""
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        anchor_payload = {
            "version": ANCHOR_VERSION,
            "kind": ANCHOR_KIND,
            "generation": 2,
            "snapshot_digest": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        anchor = dict(anchor_payload)
        anchor["digest"] = hashlib.sha256(canonical_json(anchor_payload)).hexdigest()
        _anchor_path(path).write_text(
            json.dumps(anchor, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        )
        with pytest.raises(CorruptTrustStateError):
            store.load()

    def test_symlinked_anchor_fails_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        target = tmp_path / "anchor-target.json"
        target.write_text("{}")
        _anchor_path(path).unlink()
        _anchor_path(path).symlink_to(target)
        with pytest.raises(StateStoreError):
            store.load()

    def test_loose_anchor_permissions_fail_closed(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        _anchor_path(path).chmod(0o644)
        with pytest.raises(StateStoreError):
            store.load()

    def test_dangling_symlink_state_is_not_first_run(self, tmp_path) -> None:
        """A dangling symlink at the state path must fail closed, never be
        treated as 'missing' (first-run default) — Path.exists() is False for
        dangling symlinks, so the store checks is_symlink() first."""
        path = tmp_path / "state.json"
        path.symlink_to(tmp_path / "does-not-exist")
        store = AtomicFileTrustStateStore(path, _fresh_state())
        with pytest.raises(StateStoreError):
            store.load()

    def test_save_bumps_generation_monotonically(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        seen: list[int] = []
        for _ in range(3):
            store.save(_paired_state())
            anchor = json.loads(_anchor_path(path).read_text(encoding="utf-8"))
            env = json.loads(path.read_text(encoding="utf-8"))
            seen.append(anchor["generation"])
            assert env["generation"] == anchor["generation"]
        assert seen == [1, 2, 3]

    def test_save_never_regresses_generation(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        store.save(_paired_state())
        anchor = json.loads(_anchor_path(path).read_text(encoding="utf-8"))
        assert anchor["generation"] == 2
        # The store refuses to write below the anchored generation even if an
        # older snapshot is presented for saving: next save is always +1.
        store.save(_fresh_state())
        anchor = json.loads(_anchor_path(path).read_text(encoding="utf-8"))
        assert anchor["generation"] == 3

    def test_save_refuses_corrupt_existing_anchor(self, tmp_path) -> None:
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        _anchor_path(path).write_text("{ corrupt")
        with pytest.raises(StateStoreError):
            store.save(_paired_state())

    def test_save_refuses_snapshot_without_anchor(self, tmp_path) -> None:
        """A present snapshot with a missing anchor is partial state: save()
        refuses to silently resurrect it at a fresh generation."""
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        _anchor_path(path).unlink()
        with pytest.raises(StateStoreError):
            store.save(_paired_state())



    def test_anchored_generation_none_when_no_pair(self, tmp_path) -> None:
        """No snapshot and no anchor = no generation (first run)."""
        store = AtomicFileTrustStateStore(tmp_path / "state.json", _fresh_state())
        assert store.anchored_generation() is None

    def test_anchored_generation_returns_saved_generation(self, tmp_path) -> None:
        """A saved pair reports its anchored generation; a re-save bumps it."""
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        assert store.anchored_generation() is None
        store.save(_fresh_state())
        assert store.anchored_generation() == 1
        store.save(_paired_state())
        assert store.anchored_generation() == 2
        # A NEW store instance (restart) reads the same anchored generation.
        reloaded = AtomicFileTrustStateStore(path, _fresh_state())
        assert reloaded.anchored_generation() == 2

    def test_anchored_generation_partial_pair_fails_closed(self, tmp_path) -> None:
        """A snapshot without its anchor is partial state: the generation
        cannot be trusted and fails closed."""
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        _anchor_path(path).unlink()
        with pytest.raises(StateStoreError):
            store.anchored_generation()

    def test_revocation_replay_digest_binds_snapshot_bytes(self, tmp_path) -> None:
        """Changing ANY byte of the snapshot (even re-serializing identical
        JSON differently) breaks the anchor digest — the bind is over the exact
        snapshot bytes, not a canonical re-parse."""
        path = tmp_path / "state.json"
        store = AtomicFileTrustStateStore(path, _fresh_state())
        store.save(_paired_state())
        raw = path.read_text(encoding="utf-8")
        # pretty-printed (non-canonical) re-serialization of the SAME JSON
        path.write_text(json.dumps(json.loads(raw), indent=4))
        with pytest.raises(CorruptTrustStateError):
            store.load()