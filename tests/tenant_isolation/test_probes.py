"""Unit tests for labs.tenant_isolation.harness.probes — recipe mapping,
outcome classification, and the fixture-driven assertion layer.

Merge unit B (THR-097, TASK-5792). The harness maps every normative threat
case to an executable probe recipe (cell-level isolation proofs) or an honest
deferral (connector-level cases owned by merge unit C). Expected deny/audit
categories are read from the fixtures at runtime — never duplicated here.
The assertion layer must fail closed on ANY hostile outcome, category
mismatch, or leaked detail.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from labs.tenant_isolation.harness.contract import Contract
from labs.tenant_isolation.harness.models import ObservedOutcome
from labs.tenant_isolation.harness.probes import (
    RECIPE_FOR_CATEGORY,
    DEFERRED_TO_UNIT_C,
    classify_enroll_outcome,
    evaluate_probe,
)

CONTRACT_DIR = Path(__file__).parents[2] / "tests" / "contract" / "managed_remote_access"


@pytest.fixture(scope="module")
def contract() -> Contract:
    return Contract(CONTRACT_DIR)


# ---------------------------------------------------------------------------
# Recipe mapping: every threat category accounted for (no silent drops)
# ---------------------------------------------------------------------------


def test_every_hostile_category_maps_to_probe_or_deferral(contract: Contract) -> None:
    hostile_cats = {c["category"] for c in contract.hostile_cases()}
    unmapped = hostile_cats - set(RECIPE_FOR_CATEGORY)
    assert not unmapped, f"unmapped hostile categories: {sorted(unmapped)}"


def test_every_positive_category_maps_to_a_probe(contract: Contract) -> None:
    positive_cats = {c["category"] for c in contract.positive_cases()}
    assert positive_cats <= set(RECIPE_FOR_CATEGORY)


def test_cell_isolation_categories_are_runtime_probes() -> None:
    for cat in (
        "wrong_cell_enrollment",
        "wrong_cell_redeem",
        "cross_cell_key_reuse",
        "peer_absent",
        "map_absent",
        "direct_path_denied",
        "forged_tags",
        "forged_routes",
        "policy_empty",
        "policy_malformed",
        "policy_compile_failed",
        "client_to_client",
        "non_connector_port",
        "positive_control_allowed_http",
        "positive_control_allowed_sse",
    ):
        assert RECIPE_FOR_CATEGORY[cat]["kind"] == "probe", cat


def test_relay_categories_are_not_executed_with_prerequisite() -> None:
    """DERP isolation must NOT be claimed while DERP is disabled.

    The pinned tailscale tarball ships no derper and headscale v0.25.1 embedded
    DERP requires TLS termination, so a deterministic real forced-relay path is
    not available on the authorized isolated runner without adding a pinned
    derper dependency or TLS infrastructure — the exact prerequisite is
    recorded, never weakened or fabricated.
    """
    for cat in ("forced_derp_denied", "derp_cannot_bypass_headscale_policy"):
        entry = RECIPE_FOR_CATEGORY[cat]
        assert entry["kind"] == "not-executed", cat
        assert "derper" in entry["reason"] and "not claimed" in entry["reason"].lower()


def test_policy_epoch_categories_are_deferred_to_unit_c() -> None:
    """Stale/future/rollback/apply-failed policy semantics are connector epoch
    logic (unit C); they must be explicitly deferred, never executed as fake
    cell probes and never silently dropped."""
    for cat in ("policy_stale", "policy_future", "policy_rollback", "policy_apply_failed"):
        entry = RECIPE_FOR_CATEGORY[cat]
        assert entry["kind"] == "deferred", cat
        assert "unit C" in entry["reason"]


def test_connector_level_categories_are_honestly_deferred() -> None:
    """Path/allowlist/upgrade/smuggling/bearer cases belong to unit C's connector.

    They must be marked deferred with a reason — never silently dropped and
    never falsely claimed as proven here.
    """
    for cat in (
        "encoded_path",
        "forbidden_route",
        "unsupported_upgrade",
        "smuggling_headers",
        "daemon_bearer_in_remote_input",
    ):
        entry = RECIPE_FOR_CATEGORY[cat]
        assert entry["kind"] == "deferred", cat
        assert entry["reason"]
    assert set(DEFERRED_TO_UNIT_C) <= set(RECIPE_FOR_CATEGORY)


# ---------------------------------------------------------------------------
# Outcome classification (pure parsing of backend outputs)
# ---------------------------------------------------------------------------


def test_classify_enroll_denied_maps_to_enrollment_category() -> None:
    outcome = classify_enroll_outcome(returncode=1, stderr="Failed to register", ok_marker="Successfully")
    assert outcome.outcome == "denied"
    assert outcome.deny_category == "enrollment"
    assert outcome.audit_category == "enrollment_denied"


def test_classify_enroll_allowed_maps_to_allowed_request() -> None:
    outcome = classify_enroll_outcome(returncode=0, stderr="Successfully logged in", ok_marker="Successfully")
    assert outcome.outcome == "allowed"
    assert outcome.audit_category == "allowed_request"
    assert outcome.deny_category is None


# ---------------------------------------------------------------------------
# evaluate_probe: fixture-driven assertion layer
# ---------------------------------------------------------------------------


def test_evaluate_matches_fixture_expectation(contract: Contract) -> None:
    case = contract.cases_by_category("wrong_cell_enrollment")[0]
    observed = ObservedOutcome(
        outcome="denied",
        deny_category="enrollment",
        audit_category="enrollment_denied",
        detail="Enrollment denied at the boundary; no confirmation of existence.",
    )
    result = evaluate_probe(case, observed, contract)
    assert result.passed is True


def test_evaluate_rejects_hostile_allowed(contract: Contract) -> None:
    """Mutation: a hostile case observed as allowed must never pass."""
    case = contract.cases_by_category("wrong_cell_enrollment")[0]
    observed = ObservedOutcome(
        outcome="allowed",
        deny_category=None,
        audit_category="allowed_request",
        detail="Request allowed.",
    )
    result = evaluate_probe(case, observed, contract)
    assert result.passed is False
    assert result.hostile_allowed_bug is False  # caller did not mark it passed


def test_evaluate_rejects_deny_category_mismatch(contract: Contract) -> None:
    case = contract.cases_by_category("wrong_cell_enrollment")[0]
    observed = ObservedOutcome(
        outcome="denied",
        deny_category="identity",  # fixture expects enrollment
        audit_category="enrollment_denied",
        detail="Denied.",
    )
    result = evaluate_probe(case, observed, contract)
    assert result.passed is False


def test_evaluate_rejects_audit_category_mismatch(contract: Contract) -> None:
    case = contract.cases_by_category("wrong_cell_enrollment")[0]
    observed = ObservedOutcome(
        outcome="denied",
        deny_category="enrollment",
        audit_category="topology_denied",  # fixture expects enrollment_denied
        detail="Denied.",
    )
    assert evaluate_probe(case, observed, contract).passed is False


def test_evaluate_rejects_leaking_detail(contract: Contract) -> None:
    """Mutation: a probe detail that leaks a credential/tenant must fail."""
    case = contract.cases_by_category("wrong_cell_enrollment")[0]
    observed = ObservedOutcome(
        outcome="denied",
        deny_category="enrollment",
        audit_category="enrollment_denied",
        detail="hrpair_LEAKED secret tenant a value",  # sentinel + tenant id
    )
    assert evaluate_probe(case, observed, contract).passed is False


def test_positive_control_matches(contract: Contract) -> None:
    case = contract.positive_cases()[0]
    observed = ObservedOutcome(
        outcome="allowed",
        deny_category=None,
        audit_category="allowed_request",
        detail="Request authenticated and forwarded to the loopback daemon.",
    )
    assert evaluate_probe(case, observed, contract).passed is True


def test_positive_control_denied_fails(contract: Contract) -> None:
    case = contract.positive_cases()[0]
    observed = ObservedOutcome(
        outcome="denied",
        deny_category="transport",
        audit_category="topology_denied",
        detail="Connection denied.",
    )
    assert evaluate_probe(case, observed, contract).passed is False


# ---------------------------------------------------------------------------
# Finding 1 (TASK-5796): GENUINE source-node-to-destination-node probes.
# Transport recipes must originate in a node's own context (SOCKS5 proxy) and
# target a destination node's tailnet listener — never a runner-host TCP
# connection to a Headscale control-plane port.
# ---------------------------------------------------------------------------


def _probe_env(tmp_path: Path, backend) -> "object":
    from types import SimpleNamespace

    from labs.tenant_isolation.harness.models import build_lab_spec
    from labs.tenant_isolation.harness.orchestrator import Bounds, ProbeEnv
    from labs.tenant_isolation.harness.policy import policy_states

    spec = build_lab_spec("run-probes-1", tmp_path, 38000, 990)
    policy_states(spec.cell("b").state_dir, "b")
    return ProbeEnv(
        spec=spec,
        backend=backend,
        contract=contract,
        bounds=Bounds(per_probe=5.0, total=120.0, port_min=38000, port_max=38999),
        runtime_kind="mock",
        orchestrator=SimpleNamespace(
            apply_policy_variant=lambda cell, variant: None,
            restore_policy=lambda cell: None,
            cell_healthy=lambda cell: True,
        ),
    )


def test_transport_probes_use_node_context_socks5_not_control_port(
    tmp_path: Path,
) -> None:
    """Mutation: the old recipes probed 127.0.0.1:<cell control port> from the
    runner host. Every transport recipe must now dial through a SOURCE node's
    SOCKS5 proxy to a DESTINATION node's tailnet listener (probe_node_http) —
    the runner-host control-port probe must never appear."""
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import _run_recipe

    fake = FakeBackend()
    env = _probe_env(tmp_path, fake)
    for category, recipe in (
        ("direct_path_denied", "direct_reach_denied"),
        ("client_to_client", "client_to_client_denied"),
        ("non_connector_port", "non_connector_port_denied"),
        ("positive_control_allowed_http", "positive_same_cell_http"),
    ):
        fake.calls.clear()
        case = {"category": category, "id": "X", "class": "hostile"}
        _run_recipe(recipe, case, env)
        joined = "\n".join(" ".join(c) for c in fake.calls)
        assert "probe_node_http" in joined, recipe
        assert "probe_tcp" not in joined, (
            f"{recipe} must not probe a runner-host control-plane port"
        )
        # every node-context probe originates at 127.0.0.1:<source socks5> and
        # carries a destination tailnet IP:port pair
        assert "probe_node_http 127.0.0.1" in joined, recipe
        assert "100.64.0." in joined, recipe


def test_direct_reach_denied_originates_in_tenant_a_and_targets_tenant_b_listener(
    tmp_path: Path,
) -> None:
    """The hostile direct probe must originate in tenant A node context and
    target tenant B's synthetic connector/data-plane listener: A1's SOCKS5
    proxy dials B-home's tailnet IP:connector port; a failed dial => denied
    with the fixture categories (direct/direct_path_denied)."""
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import _run_recipe

    fake = FakeBackend()
    env = _probe_env(tmp_path, fake)
    fake.node_probe_results["127.0.0.1 37001 100.64.0.6 48080"] = False
    observed = _run_recipe("direct_reach_denied", {"category": "direct_path_denied"}, env)
    assert observed.outcome == "denied"
    assert observed.deny_category == "direct"
    assert observed.audit_category == "direct_path_denied"
    assert observed.route_evidence == "direct"
    assert observed.target_kind == "node_to_node"
    # the probe actually targeted the tenant-B connector listener
    assert any("probe_node_http" in c[0] and "100.64.0.6" in c and "48080" in c for c in fake.calls)


def test_positive_control_is_same_cell_node_to_node(tmp_path: Path) -> None:
    """Positive controls must prove SAME-CELL node-to-node connector
    reachability (A1 -> A-home listener through A1's own context)."""
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import _run_recipe

    fake = FakeBackend()
    env = _probe_env(tmp_path, fake)
    # A1 (socks 37001) -> A-home (100.64.0.3):48080 succeeds
    fake.node_probe_results["127.0.0.1 37001 100.64.0.3 48080"] = True
    observed = _run_recipe("positive_same_cell_http", {"category": "positive_control_allowed_http"}, env)
    assert observed.outcome == "allowed"
    assert observed.audit_category == "allowed_request"
    assert observed.route_evidence == "direct"


def test_relay_cases_never_claim_proof(contract: Contract) -> None:
    """run_case for relay categories produces passed=False with disposition
    not-executed and the exact prerequisite — never a fabricated pass."""
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import run_case

    fake = FakeBackend()
    env = _probe_env(Path("/tmp/x"), fake)
    for cid in ("CROSS-011", "CROSS-012"):
        case = next(c for c in contract.threat_cases if c["id"] == cid)
        result = run_case(case, env)
        assert result.passed is False, cid
        assert result.disposition == "not-executed", cid
        assert "derper" in (result.limitation or ""), cid


def test_run_case_deferred_unit_c_never_counts_as_proof(contract: Contract) -> None:
    from labs.tenant_isolation.harness.backend import FakeBackend
    from labs.tenant_isolation.harness.probes import run_case

    env = _probe_env(Path("/tmp/x"), FakeBackend())
    for case in contract.threat_cases:
        result = run_case(case, env)
        assert result.disposition in ("probe", "deferred", "not-executed")
        if result.disposition in ("deferred", "not-executed"):
            assert result.passed is False, case["id"]
            assert result.limitation, case["id"]
