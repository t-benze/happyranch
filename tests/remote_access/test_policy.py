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


# ── locked security schema: decision order / nested values / state ────────
#
# The consumer must trust the complete Unit-A security contract: the locked
# nine-step decision_order, every nested security-relevant value, and the
# operational state. Reversed/missing/duplicated order, nested-field
# mutations, and unknown states must all fail closed (policy_malformed).


LOCKED_ORDER = (
    "authenticate",
    "bind",
    "proof",
    "policy",
    "normalize",
    "allowlist",
    "strip",
    "bearer",
    "redact",
)


def _mutated(route_policy_fixture, **changes) -> dict:
    artifact = dict(route_policy_fixture)
    for key, value in changes.items():
        artifact[key] = value
    return artifact


@pytest.mark.parametrize(
    "order",
    [
        tuple(reversed(LOCKED_ORDER)),  # reversed
        LOCKED_ORDER[:-1],  # missing step (redact)
        LOCKED_ORDER[1:],  # missing step (authenticate)
        (LOCKED_ORDER[0],) * 2 + LOCKED_ORDER[1:],  # duplicated step
        LOCKED_ORDER + ("sneak",),  # unknown step
        ("normalize",) + LOCKED_ORDER[:-1],  # first step moved to front
    ],
)
def test_decision_order_mutations_fail_closed(route_policy_fixture, order) -> None:
    with pytest.raises(PolicyError) as excinfo:
        build_consumer(_mutated(route_policy_fixture, decision_order=list(order)))
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


def test_default_behavior_mutation_fails_closed(route_policy_fixture) -> None:
    with pytest.raises(PolicyError) as excinfo:
        build_consumer(_mutated(route_policy_fixture, default_behavior="allow_unclassified"))
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


def test_status_mutation_fails_closed(route_policy_fixture) -> None:
    with pytest.raises(PolicyError) as excinfo:
        build_consumer(_mutated(route_policy_fixture, status="draft"))
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


@pytest.mark.parametrize(
    "mutation",
    [
        ("normalization", {"normalize_once": False}),
        ("normalization", {"ambiguity_denied": False}),
        ("normalization", {"percent_encoding": ""}),
        ("normalization", {"extra_flag": True}),
        ("header_stripping", {"strip_authorization": False}),
        ("header_stripping", {"strip_hop_by_hop": False}),
        ("header_stripping", {"reject_smuggling": False}),
        ("header_stripping", {"reject_duplicate_critical_headers": False}),
        ("header_stripping", {"inject_only_on_loopback": False}),
        ("header_stripping", {"daemon_bearer_injection_hop": "anywhere"}),
        ("header_stripping", {"strip_cookies": False}),
        ("header_stripping", {"strip_host": False}),
        ("upgrade_semantics", {"unsupported_upgrades_denied": False}),
        ("upgrade_semantics", {"unsupported_bodies_denied": False}),
        ("upgrade_semantics", {"allowed": []}),
    ],
)
def test_nested_security_value_mutations_fail_closed(route_policy_fixture, mutation) -> None:
    section, change = mutation
    artifact = _mutated(route_policy_fixture)
    artifact[section] = {**artifact[section], **change}
    with pytest.raises(PolicyError) as excinfo:
        build_consumer(artifact)
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


def test_normalization_missing_key_fails_closed(route_policy_fixture) -> None:
    """A missing nested key is structural drift and fails closed."""
    artifact = _mutated(route_policy_fixture)
    artifact["normalization"] = {
        k: v for k, v in artifact["normalization"].items() if k != "ambiguity_denied"
    }
    with pytest.raises(PolicyError) as excinfo:
        build_consumer(artifact)
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


@pytest.mark.parametrize(
    "forbidden",
    [
        [{"id": "not_a_locked_class", "examples": []}],
        [{"id": "agent_callbacks", "examples": []}, {"id": "agent_callbacks", "examples": []}],
        [],
        [{"id": "auth_bootstrap_registration", "examples": []}],
    ],
)
def test_forbidden_classes_mutations_fail_closed(route_policy_fixture, forbidden) -> None:
    with pytest.raises(PolicyError) as excinfo:
        build_consumer(_mutated(route_policy_fixture, forbidden_classes=forbidden))
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


@pytest.mark.parametrize(
    "mutation",
    [
        ("normalization", {"percent_encoding": "ALLOW invalid encoding"}),
        ("normalization", {"dot_segments": "ALLOW dot-dot traversal to escape the daemon root"}),
        ("normalization", {"duplicate_slashes": "ALLOW collapsing to change the template match outcome"}),
        ("normalization", {"query_separation": "ALLOW query strings to participate in route identity"}),
        ("normalization", {"unicode_control_bytes": "ALLOW CRLF injection and NUL in method/path/headers"}),
        ("normalization", {"absolute_form_authority": "ALLOW absolute-form request targets"}),
        ("header_stripping", {"daemon_bearer_injection_hop": "connector_to_any_hop"}),
        ("upgrade_semantics", {"allowed": ["sse"]}),
        ("upgrade_semantics", {"allowed": ["websocket", "http"]}),
        ("upgrade_semantics", {"sse": {"allowed_templates": ["GET /admin/*"]}}),
        ("upgrade_semantics", {"websocket": {"allowed_templates": ["GET /api/v1/health"]}}),
        ("upgrade_semantics", {"sse": {"allowed_templates": ["DELETE /api/v1/orgs/{slug}/threads/{thread_id}/tail"]}}),
        ("normalization", {"percent_encoding": 123}),
        ("normalization", {"percent_encoding": True}),
        ("normalization", {"dot_segments": []}),
        ("upgrade_semantics", {"sse": {"allowed_templates": "GET /api/v1/orgs/{slug}/threads/{thread_id}/tail"}}),
        ("upgrade_semantics", {"websocket": {"allowed_templates": [123]}}),
    ],
)
def test_nested_canonical_semantic_mutations_fail_closed(route_policy_fixture, mutation) -> None:
    """Contradictory NON-EMPTY mutations of every peer nested semantic value
    fail closed at load: canonical equality, not mere non-empty prose, is the
    binding check for the Unit-A security semantics."""
    section, change = mutation
    artifact = _mutated(route_policy_fixture)
    current = artifact[section]
    if isinstance(current, dict) and isinstance(change, dict) and section == "upgrade_semantics":
        artifact[section] = {**current, **change}
    else:
        artifact[section] = {**current, **change} if isinstance(current, dict) else change
    with pytest.raises(PolicyError) as excinfo:
        build_consumer(artifact)
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


def test_canonical_locked_semantics_match_fixture(route_policy_fixture) -> None:
    """No-fork guard: the locked canonical semantics embedded in the consumer
    are exactly the Unit-A fixture's values — the consumer never maintains a
    second, drifting copy of the security contract."""
    from runtime.remote_access import policy as policy_mod

    norm = route_policy_fixture["normalization"]
    for key, canonical in policy_mod.LOCKED_NORMALIZATION_SEMANTICS.items():
        assert norm[key] == canonical, f"canonical normalization.{key} drifted from fixture"
    stripping = route_policy_fixture["header_stripping"]
    assert stripping["daemon_bearer_injection_hop"] == policy_mod.LOCKED_BEARER_INJECTION_HOP
    upgrade = route_policy_fixture["upgrade_semantics"]
    assert tuple(upgrade["sse"]["allowed_templates"]) == policy_mod.LOCKED_SSE_ALLOWED_TEMPLATES
    assert tuple(upgrade["websocket"]["allowed_templates"]) == policy_mod.LOCKED_WEBSOCKET_ALLOWED_TEMPLATES


@pytest.mark.parametrize("state", ["active-ish", "suspended", "revoked", "expired", "", "ACTIVE"])
def test_unknown_policy_states_fail_closed_at_load(route_policy_fixture, state) -> None:
    with pytest.raises(PolicyError) as excinfo:
        build_consumer(route_policy_fixture, state=state)
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


def test_unknown_state_fails_closed_when_constructed_directly(route_policy_fixture) -> None:
    """Even a directly constructed consumer with an unknown state denies at
    require_current — unknown states are never treated as active."""
    from runtime.remote_access.allowlist import AllowEntry

    consumer = RoutePolicyConsumer(
        artifact=dict(route_policy_fixture),
        decision_order=LOCKED_ORDER,
        default_behavior="deny_unclassified",
        normalization=dict(route_policy_fixture["normalization"]),
        header_stripping=dict(route_policy_fixture["header_stripping"]),
        upgrade_semantics=dict(route_policy_fixture["upgrade_semantics"]),
        forbidden_classes=list(route_policy_fixture["forbidden_classes"]),
        allow=tuple(
            AllowEntry(str(e["method"]), str(e["path_template"]))
            for e in route_policy_fixture["allow"]
        ),
        issued_at=NOW() - timedelta(seconds=60),
        max_age_seconds=3600,
        revision=1,
        last_revision=None,
        state="mystery",
        digest="x",
    )
    with pytest.raises(PolicyError) as excinfo:
        consumer.require_current(now=NOW())
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


def test_mutation_reversed_order_changes_digest_and_is_rejected(route_policy_fixture) -> None:
    """Checked-in mutation: reversing the order also changes the pinned digest
    — both the locked-schema check and the digest drift reject it."""
    artifact = _mutated(route_policy_fixture, decision_order=list(reversed(LOCKED_ORDER)))
    digest = hashlib.sha256(canonical_json(route_policy_fixture)).hexdigest()
    with pytest.raises(PolicyError) as excinfo:
        build_consumer(artifact, pinned_digest=digest)
    assert_policy_denied(excinfo.value, "policy", "policy_malformed")


def test_decision_order_locked_tuple_exposed(route_policy_fixture) -> None:
    from runtime.remote_access.policy import LOCKED_DECISION_ORDER

    assert LOCKED_DECISION_ORDER == LOCKED_ORDER
    assert len(LOCKED_DECISION_ORDER) == 9
    assert len(set(LOCKED_DECISION_ORDER)) == 9
