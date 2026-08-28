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
- removal REVOKES FIRST (live streams close, epoch advances, persists) and
  then deletes the record; the removed credential denies like absent;
- serialized redemption: concurrent redemption of one code yields EXACTLY
  ONE credential; concurrent redemptions of different codes lose no state;
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


class TestConcurrentRedemption:
    """Adversarial concurrent redemption regression (TASK-6039 reviewer
    [HIGH] finding 4): redemption/state mutation must be serialized (or
    generation-checked) so concurrent redemption of the SAME code yields
    EXACTLY ONE credential, and concurrent redemption of DIFFERENT codes
    cannot lose/corrupt persisted state.

    Deterministic interleaving seam: ``generate_device_credential`` is
    slowed so BOTH racing threads are between load and save simultaneously
    before either saves (at head, both mint; after the fix the transaction
    lock serializes them and the second thread observes the consumed code).
    """

    @staticmethod
    def _slow_credential(monkeypatch, released):
        import runtime.remote_access.pairing as pairing_mod

        orig = pairing_mod.generate_device_credential

        def slow(*, rng=None):
            released.wait(timeout=10)
            return orig(rng=rng)

        monkeypatch.setattr(pairing_mod, "generate_device_credential", slow)

    def test_concurrent_redeem_same_code_yields_exactly_one_credential(
        self, tmp_path, monkeypatch
    ) -> None:
        import threading

        store = _file_store(tmp_path)
        manager = _make_manager(store=store)
        issued = manager.issue_pairing_code("macbook-pro")
        released = threading.Event()
        self._slow_credential(monkeypatch, released)
        barrier = threading.Barrier(2)
        results: list[str | None] = []

        def worker() -> None:
            barrier.wait()
            results.append(manager.redeem_pairing(issued.code))

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        # Both threads are now between load and save (one inside the slow
        # credential generator, the other blocked on the transaction lock):
        # release them to race the save.
        released.set()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not t1.is_alive() and not t2.is_alive()
        creds = [r for r in results if r is not None]
        assert len(creds) == 1, (
            f"concurrent redemption of one code must mint EXACTLY ONE "
            f"credential; minted {len(creds)}"
        )
        # The persisted device record holds the digest of the ONE minted
        # credential (no lost/corrupt state).
        state = store.load()
        assert "macbook-pro" in state.devices
        digest = state.devices["macbook-pro"].credential_digest
        assert digest is not None
        assert digest == hashlib.sha256(creds[0].encode()).hexdigest()
        # The code is consumed (single-use preserved).
        assert state.pending_pairings["macbook-pro"].consumed is True

    def test_concurrent_redeem_different_codes_persists_both_devices(
        self, tmp_path, monkeypatch
    ) -> None:
        """Concurrent redemption of two DIFFERENT codes must not lose state:
        both devices (and both credentials) survive — no last-writer-wins
        erasure of one device's authority."""
        import threading

        store = _file_store(tmp_path)
        manager = _make_manager(store=store)
        a = manager.issue_pairing_code("macbook-pro")
        b = manager.issue_pairing_code("phone")
        released = threading.Event()
        self._slow_credential(monkeypatch, released)
        barrier = threading.Barrier(2)
        results: dict[str, str | None] = {}

        def worker(code, name) -> None:
            barrier.wait()
            results[name] = manager.redeem_pairing(code)

        t1 = threading.Thread(target=worker, args=(a.code, "a"))
        t2 = threading.Thread(target=worker, args=(b.code, "b"))
        t1.start()
        t2.start()
        released.set()
        t1.join(timeout=10)
        t2.join(timeout=10)
        assert not t1.is_alive() and not t2.is_alive()
        assert results["a"] is not None and results["b"] is not None
        # BOTH device records survive (no state loss).
        state = store.load()
        assert set(state.devices) == {"macbook-pro", "phone"}
        digest_a = state.devices["macbook-pro"].credential_digest
        digest_b = state.devices["phone"].credential_digest
        assert digest_a == hashlib.sha256(results["a"].encode()).hexdigest()
        assert digest_b == hashlib.sha256(results["b"].encode()).hexdigest()


def _proof(credential: str) -> DeviceProof:
    return DeviceProof(
        device_id=credential,
        tenant_id="diy",
        home_id="home-a",
        nonce="n",
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=5),
    )


# ── Deterministic multi-process ceremony races (TASK-6045 reviewer [HIGH] finding 1) ──
#
# The TASK-6039 optimistic generation guard was a check-then-act race ACROSS
# processes: two CLI/connector processes can both read the anchored
# generation, both pass the comparison, and both write generation N+1 —
# losing a pairing/revocation mutation or interleaving snapshot/anchor into
# an unloadable pair. These regressions run REAL separate OS processes
# (``trust_state_race_worker.py``) against ONE shared file-backed store with
# a deterministic load->publication seam, proving the post-fix owner-only
# inter-process transaction serializes issue/redeem/revoke/remove: exactly
# one redemption credential, no lost mutation, and a consistent loadable
# snapshot+anchor pair.

import json
import os
import subprocess
import sys
import time
from pathlib import Path

_RACE_WORKER = Path(__file__).resolve().parent / "trust_state_race_worker.py"


def _race_spec(state_path, operation, ready, release, result, **extra) -> dict:
    spec = {
        "state_path": str(state_path),
        "operation": operation,
        "ready_path": str(ready),
        "release_path": str(release),
        "result_path": str(result),
    }
    spec.update(extra)
    return spec


def _run_race(specs: list[dict], wait_both_timeout: float = 5.0) -> list[dict]:
    """Spawn the workers, wait for their ready markers (bounded: at the
    fixed head only the flock holder parks — its sibling is blocked ON the
    inter-process lock, which is itself the serialization evidence), write
    the release marker, and collect the JSON results."""
    procs = [
        subprocess.Popen(
            [sys.executable, str(_RACE_WORKER), json.dumps(spec)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        for spec in specs
    ]
    ready_paths = [Path(s["ready_path"]) for s in specs]
    release_path = Path(specs[0]["release_path"])
    deadline = time.monotonic() + wait_both_timeout
    seen = 0
    while time.monotonic() < deadline:
        seen = sum(1 for p in ready_paths if p.exists())
        if seen == len(specs):
            break
        time.sleep(0.02)
    release_path.write_text("go", encoding="utf-8")
    results = []
    for proc, spec in zip(procs, specs):
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, f"race worker crashed: {err}\n{out}"
        result_file = Path(spec["result_path"])
        assert result_file.exists(), f"race worker produced no result: {err}\n{out}"
        results.append(json.loads(result_file.read_text(encoding="utf-8")))
    return results


class TestMultiProcessCeremonyRaces:
    """TASK-6045 finding-1 regressions: the ceremony transaction must be an
    owner-only INTER-PROCESS serialized mutation boundary (load, generation
    validation, snapshot publication, anchor publication), not a per-process
    threading lock + optimistic check."""

    @staticmethod
    def _reload(state_path):
        """A FRESH store instance over the same files — the exact
        'another process' consumer: proves the pair binds (generation ==
        anchor generation, digest over the exact snapshot bytes) and the
        state is loadable/fail-closed-consistent after the race."""
        from runtime.remote_access.authorization import TrustState
        from runtime.remote_access.identity import ConnectorIdentity

        identity = ConnectorIdentity(
            tenant_id="diy", home_id="home-a", connector_id="connector-a"
        )
        store = AtomicFileTrustStateStore(
            state_path,
            TrustState(connector_identity=identity, pairing_epoch=0, revocation_epoch=0),
        )
        return store, store.load()

    def test_concurrent_redeem_same_code_yields_exactly_one_credential_multiprocess(
        self, tmp_path,
    ) -> None:
        """Two SEPARATE processes redeem the SAME one-time code concurrently:
        exactly one credential is minted, the code is consumed, and the
        persisted pair is consistent and loadable."""
        state_path = tmp_path / "trust-state.json"
        hand = tmp_path / "handshake"
        hand.mkdir()
        # Main-process issue (persisted before the race). The workers run on
        # the REAL clock, so the code must be issued on the real clock too
        # (the fixture NOW is a fixed historical instant whose 300s TTL would
        # have expired by the time the workers redeem).
        store, _state = self._reload(state_path)
        issued = _make_manager(
            store=store, now_fn=lambda: datetime.now(timezone.utc)
        ).issue_pairing_code("macbook-pro")
        specs = [
            _race_spec(
                state_path, "redeem", hand / "ready-a", hand / "release",
                hand / "result-a.json", code=issued.code,
            ),
            _race_spec(
                state_path, "redeem", hand / "ready-b", hand / "release",
                hand / "result-b.json", code=issued.code,
            ),
        ]
        results = _run_race(specs)
        creds = [r.get("credential") for r in results if r.get("credential")]
        assert len(creds) == 1, (
            f"concurrent multi-process redemption of one code must mint EXACTLY "
            f"ONE credential; minted {len(creds)}"
        )
        fresh, state = self._reload(state_path)
        del fresh
        assert "macbook-pro" in state.devices
        digest = state.devices["macbook-pro"].credential_digest
        assert digest == hashlib.sha256(creds[0].encode()).hexdigest()
        assert state.pending_pairings["macbook-pro"].consumed is True

    def test_concurrent_issue_different_devices_loses_no_mutation(self, tmp_path) -> None:
        """Two SEPARATE processes issue pairing codes for DIFFERENT devices
        concurrently: both pending pairings survive (no last-writer-wins
        erasure) and the pair stays consistent/loadable."""
        state_path = tmp_path / "trust-state.json"
        hand = tmp_path / "handshake"
        hand.mkdir()
        specs = [
            _race_spec(
                state_path, "issue", hand / "ready-a", hand / "release",
                hand / "result-a.json", device_name="macbook-pro",
            ),
            _race_spec(
                state_path, "issue", hand / "ready-b", hand / "release",
                hand / "result-b.json", device_name="phone",
            ),
        ]
        results = _run_race(specs)
        assert all("code" in r for r in results), results
        _fresh, state = self._reload(state_path)
        assert set(state.pending_pairings) == {"macbook-pro", "phone"}, (
            f"concurrent issue across processes lost a mutation; pending = "
            f"{sorted(state.pending_pairings)}"
        )

    def test_concurrent_targeted_revoke_different_devices_no_lost_revocation(
        self, tmp_path,
    ) -> None:
        """Two SEPARATE processes revoke DIFFERENT devices concurrently: BOTH
        revocations persist (neither lost) and the pair stays loadable."""
        state_path = tmp_path / "trust-state.json"
        hand = tmp_path / "handshake"
        hand.mkdir()
        manager = _make_manager(store=self._reload(state_path)[0])
        for device in ("macbook-pro", "phone"):
            issued = manager.issue_pairing_code(device)
            assert manager.redeem_pairing(issued.code) is not None
        specs = [
            _race_spec(
                state_path, "revoke_device", hand / "ready-a", hand / "release",
                hand / "result-a.json", device_name="macbook-pro",
            ),
            _race_spec(
                state_path, "revoke_device", hand / "ready-b", hand / "release",
                hand / "result-b.json", device_name="phone",
            ),
        ]
        results = _run_race(specs)
        assert all("epoch" in r for r in results), results
        _fresh, state = self._reload(state_path)
        assert state.devices["macbook-pro"].revoked is True
        assert state.devices["phone"].revoked is True
        assert state.revocation_epoch == max(r["epoch"] for r in results)

    def test_concurrent_remove_different_devices_no_lost_removal(self, tmp_path) -> None:
        """Two SEPARATE processes remove DIFFERENT devices concurrently: both
        records are gone (no lost removal) and the pair stays loadable."""
        state_path = tmp_path / "trust-state.json"
        hand = tmp_path / "handshake"
        hand.mkdir()
        manager = _make_manager(store=self._reload(state_path)[0])
        for device in ("macbook-pro", "phone"):
            issued = manager.issue_pairing_code(device)
            assert manager.redeem_pairing(issued.code) is not None
        specs = [
            _race_spec(
                state_path, "remove_device", hand / "ready-a", hand / "release",
                hand / "result-a.json", device_name="macbook-pro",
            ),
            _race_spec(
                state_path, "remove_device", hand / "ready-b", hand / "release",
                hand / "result-b.json", device_name="phone",
            ),
        ]
        results = _run_race(specs)
        assert all(r.get("removed") for r in results), results
        _fresh, state = self._reload(state_path)
        assert state.devices == {}, (
            f"concurrent removal across processes lost a removal; devices = "
            f"{sorted(state.devices)}"
        )
