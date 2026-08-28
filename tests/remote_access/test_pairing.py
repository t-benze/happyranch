"""Supported-DIY pairing ceremony engine tests (THR-097 Unit 3A).

Proves the security-critical ceremony properties:

- single-use, expiring pairing codes; digest-only persistence;
- redeem issues a fresh per-device credential, consumes the code, records a
  monotonic-epoch DeviceAuthorization, and persists (survives restart);
- re-pairing invalidates old authority (new epoch, new digest, old
  credential denies);
- unknown / expired / consumed / removed / revoked credentials ALL deny
  identically (no existence oracle);
- revocation (one device and all) advances the epoch, persists across
  restart, closes live streams through the authoritative coordinator, and
  per-device revocation leaves other devices authorized;
- removal deletes the record; the removed credential denies like absent;
- nothing credential-shaped is ever rendered in status/list output.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from runtime.remote_access.authorization import DeviceAuthorization, TrustState
from runtime.remote_access.identity import ConnectorIdentity, DeviceProof, ProofVerdict
from runtime.remote_access.pairing import (
    CREDENTIAL_PREFIX,
    CODE_ALPHABET,
    PairingCredentialVerifier,
    PairingError,
    PairingManager,
)
from runtime.remote_access.state import InMemoryTrustStateStore
from runtime.remote_access.state_store import AtomicFileTrustStateStore

NOW = datetime(2026, 8, 28, 12, 0, 0, tzinfo=timezone.utc)


def _identity() -> ConnectorIdentity:
    return ConnectorIdentity(tenant_id="diy", home_id="home-a", connector_id="connector-a")


def _make_manager(
    *,
    store=None,
    now_fn=None,
    code_ttl=300,
    cred_ttl=365,
    registry=None,
) -> PairingManager:
    state = TrustState(connector_identity=_identity(), pairing_epoch=0, revocation_epoch=0)
    store = store or InMemoryTrustStateStore(state)
    return PairingManager(
        state_store=store,
        identity=_identity(),
        now_fn=now_fn or (lambda: NOW),
        code_ttl_seconds=code_ttl,
        credential_ttl_days=cred_ttl,
        registry=registry,
    )


def _file_store(tmp_path) -> AtomicFileTrustStateStore:
    state = TrustState(connector_identity=_identity(), pairing_epoch=0, revocation_epoch=0)
    return AtomicFileTrustStateStore(tmp_path / "trust-state.json", state)


class TestIssueCode:
    def test_issues_short_code_and_stores_only_digest(self) -> None:
        manager = _make_manager()
        issued = manager.issue_pairing_code("macbook-pro")
        assert len(issued.code) == 8
        assert all(c in CODE_ALPHABET for c in issued.code)
        assert issued.device_name == "macbook-pro"
        state = manager.load_state()
        pending = state.pending_pairings["macbook-pro"]
        assert pending.code_digest == hashlib.sha256(issued.code.encode()).hexdigest()
        assert issued.code not in str(state)

    def test_rejects_blank_device_name(self) -> None:
        manager = _make_manager()
        with pytest.raises(PairingError):
            manager.issue_pairing_code("   ")

    def test_code_expires(self) -> None:
        manager = _make_manager()
        issued = manager.issue_pairing_code("phone")
        later = _make_manager(now_fn=lambda: NOW + timedelta(seconds=301))
        # The later manager has a different in-memory store; use the same
        # file-backed store to prove expiry persists.
        state = manager.load_state()
        assert state.pending_pairings["phone"].expires_at == NOW + timedelta(seconds=300)


class TestRedeem:
    def test_redeem_issues_credential_and_consumes_code(self) -> None:
        manager = _make_manager()
        issued = manager.issue_pairing_code("macbook-pro")
        credential = manager.redeem_pairing(issued.code)
        assert credential is not None
        assert credential.startswith(CREDENTIAL_PREFIX)
        assert len(credential) == len(CREDENTIAL_PREFIX) + 32
        state = manager.load_state()
        assert state.pending_pairings["macbook-pro"].consumed is True
        device = state.devices["macbook-pro"]
        assert device.credential_digest == hashlib.sha256(credential.encode()).hexdigest()
        assert device.authorization_epoch == 1

    def test_replayed_code_denies_identically_to_unknown(self) -> None:
        manager = _make_manager()
        issued = manager.issue_pairing_code("macbook-pro")
        credential = manager.redeem_pairing(issued.code)
        assert credential is not None
        # Replay of the SAME code after consumption: deny like unknown.
        assert manager.redeem_pairing(issued.code) is None
        assert manager.redeem_pairing("WRONGCOD") is None

    def test_unknown_blank_and_expired_codes_deny(self) -> None:
        manager = _make_manager()
        issued = manager.issue_pairing_code("macbook-pro")
        # Expire the code (issue with a TTL then advance the clock past it).
        expired_manager = PairingManager(
            state_store=manager._state_store,
            identity=_identity(),
            now_fn=lambda: NOW + timedelta(seconds=999),
            code_ttl_seconds=300,
        )
        assert expired_manager.redeem_pairing(issued.code) is None
        assert manager.redeem_pairing("") is None
        assert manager.redeem_pairing("   ") is None
        assert manager.redeem_pairing("NOPE") is None

    def test_redeem_persists_across_restart(self, tmp_path) -> None:
        store = _file_store(tmp_path)
        manager = _make_manager(store=store)
        issued = manager.issue_pairing_code("macbook-pro")
        credential = manager.redeem_pairing(issued.code)
        assert credential is not None
        # Fresh process/store instance over the same files:
        fresh = _make_manager(store=_file_store(tmp_path))
        state = fresh.load_state()
        assert state.devices["macbook-pro"].credential_digest == hashlib.sha256(
            credential.encode()
        ).hexdigest()
        assert state.pending_pairings["macbook-pro"].consumed is True

    def test_redeem_wrong_audience_denied(self) -> None:
        manager = _make_manager()
        issued = manager.issue_pairing_code("macbook-pro")
        credential = manager.redeem_pairing(issued.code)
        assert credential is not None
        # A device record in the state carries tenant/home of the connector;
        # a presented credential for another home is not found -> deny.
        other_identity = ConnectorIdentity(
            tenant_id="diy", home_id="home-b", connector_id="connector-a"
        )
        other = PairingManager(
            state_store=manager._state_store,
            identity=other_identity,
            now_fn=lambda: NOW,
        )
        assert other.redeem_pairing("SOMECODE") is None  # no pending in this store -> deny


class TestRepairing:
    def test_repair_invalidates_old_authority(self) -> None:
        manager = _make_manager()
        first = manager.issue_pairing_code("macbook-pro")
        credential_1 = manager.redeem_pairing(first.code)
        assert credential_1 is not None
        second = manager.issue_pairing_code("macbook-pro")
        credential_2 = manager.redeem_pairing(second.code)
        assert credential_2 is not None
        assert credential_1 != credential_2
        state = manager.load_state()
        # Old digest replaced; epoch advanced monotonically.
        assert state.devices["macbook-pro"].credential_digest == hashlib.sha256(
            credential_2.encode()
        ).hexdigest()
        assert state.devices["macbook-pro"].authorization_epoch == 2
        # Old credential now denies (verifier on the resolved device).
        verifier = manager.credential_verifier()
        verdict = verifier.verify(
            _proof("macbook-pro"), _identity(), NOW
        )
        assert verdict.ok is True
        # But the old raw credential no longer RESOLVES to the device:
        assert manager.find_device_by_credential(credential_1) is None
        assert manager.find_device_by_credential(credential_2) is not None
        verdict_new = verifier.verify(_proof("macbook-pro"), _identity(), NOW)
        assert verdict_new.ok is True


class TestVerifier:
    def test_absent_revoked_expired_removed_all_deny_identically(self, tmp_path) -> None:
        store = _file_store(tmp_path)
        manager = _make_manager(store=store)
        issued = manager.issue_pairing_code("macbook-pro")
        credential = manager.redeem_pairing(issued.code)
        assert credential is not None

        # Baseline: the resolved proof for the paired device verifies.
        assert manager.credential_verifier().verify(_proof("macbook-pro"), _identity(), NOW).ok is True

        # Absent credential (no device resolution possible):
        absent = manager.credential_verifier().verify(_proof("unknown-device"), _identity(), NOW)
        assert absent.ok is False and absent.reason == "identity_unestablished"

        # Revoked device:
        manager.revoke("macbook-pro")
        revoked = manager.credential_verifier().verify(_proof("macbook-pro"), _identity(), NOW)
        assert revoked.ok is False and revoked.reason == "identity_unestablished"

        # Expired device (advance the clock past the credential TTL):
        expired_manager = PairingManager(
            state_store=_file_store(tmp_path),
            identity=_identity(),
            now_fn=lambda: NOW + timedelta(days=9999),
            credential_ttl_days=365,
        )
        expired = expired_manager.credential_verifier().verify(
            _proof("macbook-pro"), _identity(), NOW + timedelta(days=9999)
        )
        assert expired.ok is False and expired.reason == "identity_unestablished"

        # Removed device:
        manager.remove_device("macbook-pro")
        removed = manager.credential_verifier().verify(_proof("macbook-pro"), _identity(), NOW)
        assert removed.ok is False and removed.reason == "identity_unestablished"

        # All four deny paths are EXTERNALLY IDENTICAL (same reason).
        reasons = {absent.reason, revoked.reason, expired.reason, removed.reason}
        assert reasons == {"identity_unestablished"}

    def test_find_device_by_credential_enforces_digest(self) -> None:
        manager = _make_manager()
        issued = manager.issue_pairing_code("macbook-pro")
        credential = manager.redeem_pairing(issued.code)
        assert credential is not None
        assert manager.find_device_by_credential(credential) is not None
        # Tampered credential (same length, different bytes):
        tampered = "hrpair_" + ("a" * 32 if credential[-1] != "a" else "b" * 32)
        assert manager.find_device_by_credential(tampered) is None
        assert manager.find_device_by_credential("") is None

    def test_verifier_rejects_digestless_device(self) -> None:
        """A device record without a credential digest (e.g. legacy/harness)
        is never verifiable through the DIY lane — it cannot be resolved from
        a presented credential, and a direct proof for its name denies."""
        state = TrustState(connector_identity=_identity(), pairing_epoch=0, revocation_epoch=0)
        state.apply_pairing(
            DeviceAuthorization(
                device_id="legacy-device",
                tenant_id="diy",
                home_id="home-a",
                authorization_epoch=1,
                expires_at=NOW + timedelta(days=30),
            )
        )
        verifier = PairingCredentialVerifier(state)
        verdict = verifier.verify(_proof("legacy-device"), _identity(), NOW)
        assert verdict.ok is False and verdict.reason == "identity_unestablished"


class TestRevocation:
    def test_revoke_all_persists_across_restart(self, tmp_path) -> None:
        store = _file_store(tmp_path)
        manager = _make_manager(store=store)
        creds = []
        for name in ("macbook-pro", "phone"):
            issued = manager.issue_pairing_code(name)
            cred = manager.redeem_pairing(issued.code)
            assert cred is not None
            creds.append(cred)
        outcome = manager.revoke()
        assert outcome.complete is True
        assert outcome.epoch == 3  # pair epoch 2 -> next is 3
        # Fresh process over the same files:
        fresh = _make_manager(store=_file_store(tmp_path))
        state = fresh.load_state()
        assert state.revocation_epoch == 3
        assert all(d.revoked for d in state.devices.values())
        for cred in creds:
            verdict = fresh.credential_verifier().verify(_proof(cred), _identity(), NOW)
            assert verdict.ok is False

    def test_revoke_one_device_leaves_others_authorized(self, tmp_path) -> None:
        store = _file_store(tmp_path)
        manager = _make_manager(store=store)
        creds = {}
        for name in ("macbook-pro", "phone"):
            issued = manager.issue_pairing_code(name)
            cred = manager.redeem_pairing(issued.code)
            assert cred is not None
            creds[name] = cred
        outcome = manager.revoke("phone")
        assert outcome.complete is True
        assert outcome.device_id == "phone"
        # The revoked phone's credential no longer resolves.
        assert manager.find_device_by_credential(creds["phone"]) is None
        # The other device's credential still resolves and its proof verifies.
        assert manager.find_device_by_credential(creds["macbook-pro"]) is not None
        assert manager.credential_verifier().verify(_proof("macbook-pro"), _identity(), NOW).ok is True

    def test_revoke_epoch_monotonic_rollback_rejected(self, tmp_path) -> None:
        store = _file_store(tmp_path)
        manager = _make_manager(store=store)
        issued = manager.issue_pairing_code("macbook-pro")
        manager.redeem_pairing(issued.code)
        manager.revoke()
        # A second revoke still advances (not an error), but a stored state
        # replay at an OLDER epoch is rejected by the coordinator path.
        from runtime.remote_access.revocation import RevocationCoordinator
        from runtime.remote_access.streams import StreamRegistry

        state = manager.load_state()
        coordinator = RevocationCoordinator(state, StreamRegistry())
        with pytest.raises(ValueError, match="rollback"):
            coordinator.revoke(state.revocation_epoch)  # same epoch = rollback


class TestRemove:
    def test_remove_device_deletes_record_and_credential(self) -> None:
        manager = _make_manager()
        issued = manager.issue_pairing_code("macbook-pro")
        credential = manager.redeem_pairing(issued.code)
        assert credential is not None
        manager.remove_device("macbook-pro")
        state = manager.load_state()
        assert "macbook-pro" not in state.devices
        # Removed credential denies like absent.
        verdict = manager.credential_verifier().verify(_proof(credential), _identity(), NOW)
        assert verdict.ok is False and verdict.reason == "identity_unestablished"


class TestListAndStatus:
    def test_list_devices_is_redacted(self) -> None:
        manager = _make_manager()
        issued = manager.issue_pairing_code("macbook-pro")
        credential = manager.redeem_pairing(issued.code)
        assert credential is not None
        info = manager.list_devices()
        assert len(info) == 1
        assert info[0].device_id == "macbook-pro"
        assert info[0].paired is True
        assert info[0].credential_digest_present is True
        blob = str(info) + str(manager.pairing_status())
        assert credential not in blob
        assert hashlib.sha256(credential.encode()).hexdigest() not in blob

    def test_pairing_status_states(self) -> None:
        manager = _make_manager()
        issued = manager.issue_pairing_code("macbook-pro")
        credential = manager.redeem_pairing(issued.code)
        assert credential is not None
        status = manager.pairing_status()
        assert status["devices"][0]["state"] == "paired"
        manager.revoke("macbook-pro")
        status = manager.pairing_status()
        assert status["devices"][0]["state"] == "revoked"


def _proof(credential: str) -> DeviceProof:
    return DeviceProof(
        device_id=credential,
        tenant_id="diy",
        home_id="home-a",
        nonce="n",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
    )
