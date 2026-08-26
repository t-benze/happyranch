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
        "forced_derp_denied",
        "derp_cannot_bypass_headscale_policy",
        "forged_tags",
        "forged_routes",
        "policy_empty",
        "policy_stale",
    ):
        assert RECIPE_FOR_CATEGORY[cat]["kind"] == "probe", cat


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
