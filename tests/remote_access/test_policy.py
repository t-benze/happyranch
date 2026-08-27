"""Versioned route-policy consumer — schema/digest/version/staleness drift
fail-closed behavior (contract §6.1 step 4, §14).

The policy artifact is the Unit-A route-policy fixture wrapped in a signed
envelope. Missing/malformed/stale/future/rollback/compiler-failed/apply-failed
state and digest/version drift all fail closed with the normative policy
deny/audit categories.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import pytest

from runtime.remote_access.policy import (
    PolicyEnvelope,
    PolicyError,
    RoutePolicyConsumer,
    canonical_json,
)

from .conftest import NOW, build_consumer, make_policy_envelope


def assert_policy_denied(exc: PolicyError, deny: str, audit: str) -> None:
    assert exc.outcome.deny_category == deny
    assert exc.outcome.audit_category == audit


# ── positive: the normative fixture loads ────────────────────────────────


def test_consumer_loads_normative_fixture(route_policy_fixture) -> None:
    consumer = build_consumer(route_policy_fixture)
    consumer.require_current(now=NOW())  # must not raise
    assert len(consumer.allowlist_entries) == 134
    assert consumer.default_behavior == "deny_unclassified"
    assert consumer.decision_order[0] == "authenticate"
    assert consumer.decision_order[-1] == "redact"


def test_canonical_json_is_deterministic() -> None:
    payload = {"b": 2, "a": {"z": 1, "y": [3, 1]}}
    assert canonical_json(payload) == canonical_json(json.loads(canonical_json(payload)))


def test_digest_is_sha256_hex() -> None:
    digest = hashlib.sha256(canonical_json({"version": 1})).hexdigest()
    assert len(digest) == 64
    assert int(digest, 16) >= 0  # valid hex


# ── schema / version drift ───────────────────────────────────────────────


def test_unknown_schema_version_fails_closed() -> None:
    with pytest.raises(PolicyError) as excinfo:
        RoutePolicyConsumer.from_envelope(
            make_policy_envelope({"version": 1}, schema_version=0),
            now=NOW(),
        )
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


def test_artifact_version_drift_fails_closed(route_policy_fixture) -> None:
    artifact = dict(route_policy_fixture)
    artifact["version"] = 99
    with pytest.raises(PolicyError) as excinfo:
        RoutePolicyConsumer.from_envelope(make_policy_envelope(artifact), now=NOW())
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


def test_missing_required_artifact_fields_fail_closed() -> None:
    with pytest.raises(PolicyError) as excinfo:
        RoutePolicyConsumer.from_envelope(
            make_policy_envelope({"version": 1, "name": "x"}),
            now=NOW(),
        )
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


def test_unknown_artifact_top_level_keys_fail_closed(route_policy_fixture) -> None:
    artifact = dict(route_policy_fixture)
    artifact["surprise"] = "not-allowed"
    with pytest.raises(PolicyError) as excinfo:
        RoutePolicyConsumer.from_envelope(make_policy_envelope(artifact), now=NOW())
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


def test_non_dict_artifact_fails_closed() -> None:
    with pytest.raises(PolicyError) as excinfo:
        RoutePolicyConsumer.from_envelope(
            make_policy_envelope([1, 2, 3], schema_version=1),
            now=NOW(),
        )
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


# ── digest drift ─────────────────────────────────────────────────────────


def test_pinned_digest_mismatch_fails_closed(route_policy_fixture) -> None:
    wrong_digest = "0" * 64
    with pytest.raises(PolicyError) as excinfo:
        build_consumer(route_policy_fixture, pinned_digest=wrong_digest)
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


def test_pinned_digest_match_accepts(route_policy_fixture) -> None:
    digest = hashlib.sha256(canonical_json(route_policy_fixture)).hexdigest()
    consumer = build_consumer(route_policy_fixture, pinned_digest=digest)
    consumer.require_current(now=NOW())  # must not raise


def test_single_byte_drift_changes_digest(route_policy_fixture) -> None:
    """A one-byte mutation of the artifact is detected via digest drift."""
    artifact = dict(route_policy_fixture)
    allow = [dict(e) for e in artifact["allow"]]
    allow[0]["method"] = "GET" if allow[0]["method"] != "GET" else "POST"
    artifact["allow"] = allow
    digest = hashlib.sha256(canonical_json(route_policy_fixture)).hexdigest()
    with pytest.raises(PolicyError) as excinfo:
        build_consumer(artifact, pinned_digest=digest)
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


# ── staleness / future / rollback ────────────────────────────────────────


def test_stale_policy_fails_closed(route_policy_fixture) -> None:
    consumer = build_consumer(route_policy_fixture, max_age_seconds=60)
    with pytest.raises(PolicyError) as excinfo:
        consumer.require_current(now=NOW() + timedelta(seconds=120))
    assert_policy_denied(excinfo.value, "policy", "policy_stale")


def test_future_policy_fails_closed(route_policy_fixture) -> None:
    consumer = build_consumer(route_policy_fixture, issued_at=NOW() + timedelta(seconds=301))
    with pytest.raises(PolicyError) as excinfo:
        consumer.require_current(now=NOW())
    assert_policy_denied(excinfo.value, "policy", "policy_future")


def test_rollback_revision_fails_closed(route_policy_fixture) -> None:
    consumer = build_consumer(route_policy_fixture, revision=2, last_revision=3)
    with pytest.raises(PolicyError) as excinfo:
        consumer.require_current(now=NOW())
    assert_policy_denied(excinfo.value, "policy", "policy_rollback")


def test_same_revision_accepted(route_policy_fixture) -> None:
    consumer = build_consumer(route_policy_fixture, revision=3, last_revision=3)
    consumer.require_current(now=NOW())


# ── policy states: empty / malformed / compiler / apply ─────────────────


@pytest.mark.parametrize(
    ("state", "audit"),
    [
        ("empty", "policy_missing"),
        ("malformed", "policy_malformed"),
        ("compiler_failed", "policy_compile_failed"),
        ("apply_failed", "policy_apply_failed"),
    ],
)
def test_non_active_policy_states_fail_closed(route_policy_fixture, state, audit) -> None:
    consumer = build_consumer(route_policy_fixture, state=state)
    with pytest.raises(PolicyError) as excinfo:
        consumer.require_current(now=NOW())
    assert_policy_denied(excinfo.value, "policy", audit)


def test_empty_allow_denies_every_request(route_policy_fixture) -> None:
    """An artifact with an empty allow-list denies everything (policy missing)."""
    artifact = dict(route_policy_fixture)
    artifact["allow"] = []
    consumer = build_consumer(artifact)
    with pytest.raises(PolicyError) as excinfo:
        consumer.require_current(now=NOW())
    assert_policy_denied(excinfo.value, "policy", "policy_missing")


# ── decision order is preserved from the fixture ─────────────────────────


def test_decision_order_locked_to_fixture(route_policy_fixture) -> None:
    consumer = build_consumer(route_policy_fixture)
    assert consumer.decision_order == tuple(route_policy_fixture["decision_order"])


def test_default_behavior_deny_unclassified(route_policy_fixture) -> None:
    consumer = build_consumer(route_policy_fixture)
    assert consumer.default_behavior == route_policy_fixture["default_behavior"]


def test_upgrade_semantics_websocket_empty(route_policy_fixture) -> None:
    consumer = build_consumer(route_policy_fixture)
    assert consumer.websocket_allowed_templates == ()
    assert consumer.sse_allowed_templates == (
        "/api/v1/orgs/{slug}/threads/{thread_id}/tail",
        "/api/v1/orgs/{slug}/jobs/{job_id}/tail",
    )
