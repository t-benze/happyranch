"""Consumes the other normative Unit-A fixtures (route-policy, credential
taxonomy, failure categories) as the connector core's governing contracts.

Every deny/audit category the core can emit must be a member of the fixture
taxonomies; the daemon bearer class keeps its absolute network prohibition;
the allow-list is the explicit remote surface (never the web-coverage set).
"""
from __future__ import annotations

from runtime.remote_access.audit import DENY_DETAILS, detail_for
from runtime.remote_access.forwarding import LOOPBACK_HOST
from runtime.remote_access.gateway import ConnectorGateway

from .conftest import build_consumer


def test_emitted_deny_categories_are_taxonomy_members(failure_categories_fixture) -> None:
    taxonomy = {c["id"] for c in failure_categories_fixture["deny_categories"]}
    for key in DENY_DETAILS:
        deny_category, audit_category, _ = key
        assert deny_category in taxonomy, f"deny category {deny_category} not in taxonomy"
    # Also check audit categories.
    audit_taxonomy = {c["id"] for c in failure_categories_fixture["audit_categories"]}
    for key in DENY_DETAILS:
        deny_category, audit_category, _ = key
        assert audit_category in audit_taxonomy, f"audit category {audit_category} not in taxonomy"


def test_deny_details_are_tenant_neutral() -> None:
    for (deny, audit, reason), detail in DENY_DETAILS.items():
        assert detail.strip(), f"detail for {deny}/{audit}/{reason} is empty"
        assert "PLACEHOLDER_" not in detail
        assert "Bearer " not in detail
        assert "hrpair_" not in detail and "hrreg_" not in detail


def test_route_policy_fixture_allowlist_is_explicit_allow(route_policy_fixture) -> None:
    """The connector's policy is explicit allow-by-method+template, never the
    web-coverage 'included' set and never a deny-list."""
    consumer = build_consumer(route_policy_fixture)
    assert consumer.default_behavior == "deny_unclassified"
    # The auth-bootstrap surface is never remotely allowed.
    assert consumer.allowlist.match("GET", "/api/v1/auth/bootstrap") is None
    assert consumer.allowlist.match("POST", "/api/v1/auth/bootstrap") is None
    # Agent callbacks are never remotely allowed.
    assert (
        consumer.allowlist.match("POST", "/api/v1/orgs/acme/tasks/T-1/completion") is None
    )


def test_forbidden_classes_never_overlap_allowlist(route_policy_fixture) -> None:
    """Validator-side invariant mirrored in the core: forbidden-class examples
    never match the allow-list."""
    consumer = build_consumer(route_policy_fixture)
    for cls in route_policy_fixture["forbidden_classes"]:
        for example in cls.get("examples", []):
            if not isinstance(example, str):
                continue
            if " " in example:
                method, _, path = example.partition(" ")
            else:
                method, path = "ANY", example
            if method != "ANY":
                assert consumer.allowlist.match(method, path) is None, (
                    f"forbidden class {cls['id']} overlaps allow-list at {method} {path}"
                )


def test_local_daemon_bearer_never_network_exposed(credential_taxonomy_fixture) -> None:
    bearer_class = next(
        c for c in credential_taxonomy_fixture["classes"] if c["id"] == "local_daemon_bearer"
    )
    forbidden = " ".join(bearer_class["forbidden_exposure"]).lower()
    assert "network" in forbidden
    # The bearer's presentation rule is the absolute network prohibition.
    presentation = bearer_class["network_presentation"].lower()
    assert "never transmitted over any network" in presentation


def test_remote_classes_permit_encrypted_transport_to_audience(credential_taxonomy_fixture) -> None:
    """Unit-A fix-forward rule: remotely presented credentials travel only over
    encrypted transport to their exact authenticated audience; the connector
    core never transmits them at all (it only injects the local bearer)."""
    for cls in credential_taxonomy_fixture["classes"]:
        if cls["id"] == "local_daemon_bearer":
            continue
        presentation = cls.get("network_presentation", "")
        assert "encrypted" in presentation.lower() or "tls" in presentation.lower() or "wireguard" in presentation.lower() or "https" in presentation.lower(), (
            f"{cls['id']} missing encrypted-transport rule"
        )


def test_all_deny_categories_have_canonical_detail() -> None:
    """Every deny category the core emits has a canonical tenant-neutral detail."""
    emitted = {
        "expiry", "replay", "identity", "current_device", "pairing",
        "policy", "normalization", "route", "method", "revocation",
        "local_daemon", "internal",
    }
    for deny in emitted:
        detail = detail_for(deny, None)
        assert detail is not None and detail.strip()
        assert "Bearer" not in detail
        assert "hrpair_" not in detail and "hrreg_" not in detail


def test_decision_order_in_gateway_matches_fixture(route_policy_fixture) -> None:
    consumer = build_consumer(route_policy_fixture)
    fixture_order = tuple(consumer.decision_order)
    # The gateway's executable steps are the fixture's first eight stages
    # (redact is the final wrap) in the same relative order.
    gateway_steps = ConnectorGateway._STEP_ORDER
    assert gateway_steps == ("authenticate", "bind", "proof", "policy", "normalize", "allowlist", "strip", "bearer")
    assert fixture_order[:8] == gateway_steps


def test_loopback_forwarder_host_constant_matches_contract() -> None:
    # The only network target the connector core may use.
    assert LOOPBACK_HOST == "127.0.0.1"
