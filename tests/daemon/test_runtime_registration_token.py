"""Tests for the runtime-level (org-agnostic) registration token methods.

Additive to RegistrationTokenStore — these methods operate on a parallel
runtime-scoped token space without touching the existing org-scoped methods.
"""
from __future__ import annotations

import threading
import time

import pytest

from runtime.daemon.registration_token import (
    DEFAULT_REGISTRATION_TOKEN_TTL_SECONDS,
    REGISTRATION_TOKEN_PREFIX,
    RegistrationTokenStore,
    _RUNTIME_ORG,
)


@pytest.fixture
def store():
    return RegistrationTokenStore()


# ── Runtime token mint ──────────────────────────────────────────────────


class TestRuntimeTokenMint:
    """Runtime mint: org-agnostic token creation."""

    def test_mint_runtime_returns_prefixed_token(self, store):
        token, _expires = store.mint_runtime("my-executor")
        assert token.startswith(REGISTRATION_TOKEN_PREFIX)
        assert len(token) > len(REGISTRATION_TOKEN_PREFIX) + 20

    def test_mint_runtime_returns_expires_at_in_future(self, store):
        now = 1_000_000.0
        _token, expires = store.mint_runtime("my-executor", now=now)
        assert expires == now + DEFAULT_REGISTRATION_TOKEN_TTL_SECONDS

    def test_mint_runtime_expires_prior_unconsumed_token_for_same_name(self, store):
        now = 1_000_000.0
        token1, _ = store.mint_runtime("my-executor", now=now)
        # Token1 is still unconsumed
        token2, _ = store.mint_runtime("my-executor", now=now + 1)
        # Token1 should now be consumed (expired by the newer mint)
        assert store.validate_runtime(token1, now=now + 1) is None

    def test_mint_runtime_does_not_affect_org_scoped_tokens(self, store):
        now = 1_000_000.0
        org_token, _ = store.mint("alpha", "my-executor", now=now)
        rt_token, _ = store.mint_runtime("my-executor", now=now)
        # Org token still valid for its org
        assert store.validate(org_token, "alpha", now=now) is not None
        # Runtime token valid via runtime validate
        assert store.validate_runtime(rt_token, now=now) is not None

    def test_mint_runtime_token_not_valid_for_org(self, store):
        now = 1_000_000.0
        rt_token, _ = store.mint_runtime("my-executor", now=now)
        # Runtime tokens should be rejected by org-scoped validate
        assert store.validate(rt_token, "alpha", now=now) is None

    def test_mint_runtime_and_org_independent_by_name(self, store):
        now = 1_000_000.0
        rt_token_a, _ = store.mint_runtime("executor-a", now=now)
        rt_token_b, _ = store.mint_runtime("executor-b", now=now)
        # Both should be valid
        assert store.validate_runtime(rt_token_a, now=now) is not None
        assert store.validate_runtime(rt_token_b, now=now) is not None


# ── Runtime token validate ──────────────────────────────────────────────


class TestRuntimeTokenValidate:
    """Runtime validate: no org check, but checks existence, expiry, consumption."""

    def test_validate_runtime_valid_token(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        record = store.validate_runtime(token, now=now)
        assert record is not None
        assert record.org == _RUNTIME_ORG
        assert record.name == "my-executor"
        assert not record.consumed

    def test_validate_runtime_expired_token_rejected(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        past_ttl = now + DEFAULT_REGISTRATION_TOKEN_TTL_SECONDS + 1
        assert store.validate_runtime(token, now=past_ttl) is None

    def test_validate_runtime_unknown_token_rejected(self, store):
        assert store.validate_runtime("hrreg_fake_token_value") is None

    def test_validate_runtime_does_not_consume(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        store.validate_runtime(token, now=now)
        record = store.validate_runtime(token, now=now)
        assert record is not None
        assert not record.consumed

    def test_validate_runtime_accepts_token_regardless_of_org(self, store):
        """Runtime validate is org-agnostic — any valid runtime token passes."""
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        assert store.validate_runtime(token, now=now) is not None


# ── Runtime token consume ───────────────────────────────────────────────


class TestRuntimeTokenConsume:
    """Runtime consume: single-use, no org check."""

    def test_consume_runtime_valid_token(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        record = store.consume_runtime(token, now=now)
        assert record is not None
        assert record.consumed

    def test_consume_runtime_second_use_rejected(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        assert store.consume_runtime(token, now=now) is not None
        assert store.consume_runtime(token, now=now) is None

    def test_consume_runtime_expired_token_rejected(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        past_ttl = now + DEFAULT_REGISTRATION_TOKEN_TTL_SECONDS + 1
        assert store.consume_runtime(token, now=past_ttl) is None

    def test_consume_runtime_org_token_rejected(self, store):
        """Org-scoped tokens should NOT be consumable via consume_runtime."""
        now = 1_000_000.0
        org_token, _ = store.mint("alpha", "my-executor", now=now)
        assert store.consume_runtime(org_token, now=now) is None


# ── Runtime token reserve / commit / release ────────────────────────────


class TestRuntimeTokenReserve:
    """Runtime reserve/commit/release lifecycle."""

    def test_reserve_runtime_valid_token(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        record = store.reserve_runtime(token, now=now)
        assert record is not None
        # Token is reserved but not consumed
        assert not record.consumed
        assert record.reserved

    def test_reserve_runtime_second_reserve_rejected(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        assert store.reserve_runtime(token, now=now) is not None
        assert store.reserve_runtime(token, now=now) is None

    def test_commit_runtime_succeeds_after_reserve(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        assert store.reserve_runtime(token, now=now) is not None
        assert store.commit_runtime(token, now=now)

    def test_commit_runtime_fails_without_reserve(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        assert not store.commit_runtime(token, now=now)

    def test_release_runtime_succeeds_after_reserve(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        assert store.reserve_runtime(token, now=now) is not None
        assert store.release_runtime(token, now=now)
        # Token is now valid again
        assert store.validate_runtime(token, now=now) is not None

    def test_release_runtime_fails_without_reserve(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        assert not store.release_runtime(token, now=now)

    def test_runtime_org_tokens_isolated(self, store):
        """Runtime reserve/commit/release should not affect org-scoped tokens."""
        now = 1_000_000.0
        org_token, _ = store.mint("alpha", "my-executor", now=now)
        # Runtime reserve should reject org token
        assert store.reserve_runtime(org_token, now=now) is None


# ── Runtime conformance state machine ───────────────────────────────────


class TestRuntimeConformanceStateMachine:
    """Runtime conformance: same challenge model, no org check."""

    def test_mint_runtime_opens_challenge_with_required_steps(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        challenge = store.get_challenge_runtime(token)
        assert challenge is not None
        assert len(challenge.steps) == len(RegistrationTokenStore.DEFAULT_CONFORMANCE_STEPS)
        step_ids = [s.step_id for s in challenge.steps]
        assert "workspace_access" in step_ids
        assert "loopback_reachable" in step_ids
        assert "cli_callback" in step_ids

    def test_all_steps_incomplete_initially(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        assert not store.is_challenge_complete_runtime(token, now=now)

    def test_record_step_arrival_runtime(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        assert store.record_step_arrival_runtime(token, "workspace_access", now=now)
        challenge = store.get_challenge_runtime(token)
        ws_step = next(s for s in challenge.steps if s.step_id == "workspace_access")
        assert ws_step.arrived
        assert ws_step.arrived_at == now

    def test_record_step_runtime_requires_valid_token(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        past_ttl = now + DEFAULT_REGISTRATION_TOKEN_TTL_SECONDS + 1
        assert not store.record_step_arrival_runtime(
            token, "workspace_access", now=past_ttl
        )

    def test_record_step_runtime_requires_unconsumed_token(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        store.consume_runtime(token, now=now)
        assert not store.record_step_arrival_runtime(
            token, "workspace_access", now=now
        )

    def test_all_complete_flips_only_when_every_step_arrived(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        assert not store.is_challenge_complete_runtime(token, now=now)
        store.record_step_arrival_runtime(token, "workspace_access", now=now)
        assert not store.is_challenge_complete_runtime(token, now=now)
        store.record_step_arrival_runtime(token, "loopback_reachable", now=now + 1)
        assert not store.is_challenge_complete_runtime(token, now=now + 1)
        store.record_step_arrival_runtime(token, "cli_callback", now=now + 2)
        assert store.is_challenge_complete_runtime(token, now=now + 2)

    def test_expired_token_cannot_complete_runtime(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        for step_id in ["workspace_access", "loopback_reachable", "cli_callback"]:
            store.record_step_arrival_runtime(token, step_id, now=now)
        past_ttl = now + DEFAULT_REGISTRATION_TOKEN_TTL_SECONDS + 1
        assert not store.is_challenge_complete_runtime(token, now=past_ttl)

    def test_pending_steps_runtime_returns_unarrived_step_ids(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        store.record_step_arrival_runtime(token, "workspace_access", now=now)
        pending = store.get_pending_steps_runtime(token, now=now)
        assert pending is not None
        assert "workspace_access" not in pending
        assert "loopback_reachable" in pending
        assert "cli_callback" in pending

    def test_pending_steps_runtime_none_for_invalid_token(self, store):
        assert store.get_pending_steps_runtime("hrreg_fake") is None

    def test_duplicate_step_arrival_runtime_is_noop(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        assert store.record_step_arrival_runtime(token, "workspace_access", now=now)
        assert not store.record_step_arrival_runtime(token, "workspace_access", now=now + 1)

    def test_runtime_challenge_isolated_from_org_challenge(self, store):
        """Runtime challenges are independent of org-scoped challenges."""
        now = 1_000_000.0
        rt_token, _ = store.mint_runtime("my-executor", now=now)
        org_token, _ = store.mint("alpha", "my-executor", now=now)

        # Record a step on runtime token
        store.record_step_arrival_runtime(rt_token, "workspace_access", now=now)
        # Org token should still be incomplete
        assert not store.is_challenge_complete(org_token, "alpha", now=now)
        # Record same step on org token
        store.record_step_arrival(org_token, "alpha", "workspace_access", now=now)
        # Still not complete for either
        assert not store.is_challenge_complete_runtime(rt_token, now=now)
        assert not store.is_challenge_complete(org_token, "alpha", now=now)


# ── _validate_raw compatibility ─────────────────────────────────────────


class TestValidateRawCompatibility:
    """_validate_raw() is org-agnostic — it must find both org and runtime tokens."""

    def test_validate_raw_finds_org_token(self, store):
        now = 1_000_000.0
        token, _ = store.mint("alpha", "my-executor", now=now)
        record = store._validate_raw(token, now=now)
        assert record is not None
        assert record.org == "alpha"

    def test_validate_raw_finds_runtime_token(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        record = store._validate_raw(token, now=now)
        assert record is not None
        assert record.org == _RUNTIME_ORG

    def test_validate_raw_rejects_consumed_runtime_token(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        store.consume_runtime(token, now=now)
        assert store._validate_raw(token, now=now) is None

    def test_validate_raw_rejects_expired_runtime_token(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)
        past_ttl = now + DEFAULT_REGISTRATION_TOKEN_TTL_SECONDS + 1
        assert store._validate_raw(token, now=past_ttl) is None


# ── Concurrency regression tests for runtime tokens ─────────────────────


class TestRuntimeTokenConcurrency:
    """Proof that consume_runtime enforces single-use atomicity under concurrency."""

    NUM_THREADS = 8

    def test_consume_runtime_with_lock_exactly_one_wins(self, store):
        now = 1_000_000.0
        token, _ = store.mint_runtime("my-executor", now=now)

        success_count = 0
        success_lock = threading.Lock()
        barrier = threading.Barrier(self.NUM_THREADS)

        def try_consume():
            nonlocal success_count
            barrier.wait()
            record = store.consume_runtime(token, now=now)
            if record is not None:
                with success_lock:
                    success_count += 1

        threads = [
            threading.Thread(target=try_consume, daemon=True)
            for _ in range(self.NUM_THREADS)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert success_count == 1, (
            f"Expected exactly 1 successful consume_runtime under concurrency, "
            f"got {success_count}."
        )

    def test_concurrent_mint_runtime_vs_consume_runtime_consistency(self, store):
        now = 1_000_000.0
        old_token, _ = store.mint_runtime("my-executor", now=now)

        consume_successes = []
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        def mint_new():
            barrier.wait()
            store.mint_runtime("my-executor", now=now + 1)

        def consume_old():
            barrier.wait()
            record = store.consume_runtime(old_token, now=now + 1)
            with lock:
                consume_successes.append(record is not None)

        t1 = threading.Thread(target=mint_new, daemon=True)
        t2 = threading.Thread(target=consume_old, daemon=True)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert store.validate_runtime(old_token, now=now + 1) is None
