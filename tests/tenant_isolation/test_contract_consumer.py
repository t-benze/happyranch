"""Unit tests for labs.tenant_isolation.harness.contract — the semantic consumer
of the merged unit-A normative fixtures.

Merge unit B (THR-097, TASK-5792). The harness must consume the contract
*semantically*: it reads expected deny/audit categories, threat cases, and
the existence-guard rule from the read-only fixtures at runtime — it never
duplicates expected answers and never mutates the fixtures. This module locks
that behavior.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from labs.tenant_isolation.harness.contract import Contract, ContractLoadError

CONTRACT_DIR = Path(__file__).parents[2] / "tests" / "contract" / "managed_remote_access"


@pytest.fixture(scope="module")
def contract() -> Contract:
    return Contract(CONTRACT_DIR)


# ---------------------------------------------------------------------------
# Loads the merged normative fixtures read-only
# ---------------------------------------------------------------------------


def test_contract_loads_all_four_fixtures(contract: Contract) -> None:
    assert contract.version == 1
    assert contract.status == "normative-contract"
    assert len(contract.deny_categories) >= 20
    assert len(contract.audit_categories) >= 40
    assert len(contract.threat_cases) == 57


def test_contract_reports_fixture_digests(contract: Contract) -> None:
    digests = contract.fixture_digests()
    assert set(digests) == {
        "route-policy.json",
        "credential-taxonomy.json",
        "failure-categories.json",
        "threat-cases.json",
    }
    for name, digest in digests.items():
        assert digest.startswith("sha256:")


def test_contract_digest_detects_mutation(tmp_path: Path, contract: Contract) -> None:
    """A mutated fixture must be detectable via digest drift (fail-closed)."""
    import shutil

    copied = tmp_path / "managed_remote_access"
    shutil.copytree(CONTRACT_DIR, copied)
    threat = copied / "threat-cases.json"
    data = json.loads(threat.read_text(encoding="utf-8"))
    data["cases"][0]["expected"]["outcome"] = "allowed"  # hostile mutation
    threat.write_text(json.dumps(data), encoding="utf-8")
    mutated = Contract(copied)
    original = contract.fixture_digests()
    assert mutated.fixture_digests()["threat-cases.json"] != original["threat-cases.json"]


def test_contract_refuses_missing_fixture(tmp_path: Path) -> None:
    with pytest.raises(ContractLoadError):
        Contract(tmp_path)


# ---------------------------------------------------------------------------
# Threat-case semantics
# ---------------------------------------------------------------------------


def test_hostile_cases_all_denied_with_deny_category(contract: Contract) -> None:
    for case in contract.hostile_cases():
        assert case["expected"]["outcome"] == "denied", case["id"]
        assert "deny_category" in case["expected"], case["id"]
        assert case["expected"]["deny_category"] in contract.deny_categories


def test_positive_controls_allowed(contract: Contract) -> None:
    for case in contract.positive_cases():
        assert case["expected"]["outcome"] == "allowed", case["id"]
        assert case["expected"]["audit_category"] == "allowed_request"


def test_audit_categories_are_fixed_enumeration(contract: Contract) -> None:
    for case in contract.threat_cases:
        assert case["expected"]["audit_category"] in contract.audit_categories
    # No category id may embed a concrete tenant/cell/home/device identifier
    # (mirrors the unit-A validator's no-oracle rule; the bare words "home",
    # "cell", "tenant" in category ids are permitted — identifiers are not).
    import re

    id_embed = re.compile(r"(?:tenant|cell|home|device)[-_]?[a-z0-9](?=[_-]|$)")
    for cat in contract.audit_categories:
        assert id_embed.search(cat) is None, f"audit category {cat!r} embeds an identifier"


def test_existence_pairs_share_identical_categories(contract: Contract) -> None:
    pairs = contract.existence_pairs()
    assert pairs, "at least one existence-guard pair required"
    for pair_id, members in pairs.items():
        assert len(members) == 2, pair_id
        a, b = members
        assert a["expected"].get("deny_category") == b["expected"].get("deny_category")
        assert a["expected"]["audit_category"] == b["expected"]["audit_category"]


def test_categories_by_category_lookup(contract: Contract) -> None:
    """Every hostile category maps to at least one case (coverage basis)."""
    hostile_cats = {c["category"] for c in contract.hostile_cases()}
    assert "wrong_cell_enrollment" in hostile_cats
    assert "peer_absent" in hostile_cats
    assert "direct_path_denied" in hostile_cats
    assert "forced_derp_denied" in hostile_cats
    assert "derp_cannot_bypass_headscale_policy" in hostile_cats
    assert "policy_empty" in hostile_cats
    assert "forged_routes" in hostile_cats
    assert "forged_tags" in hostile_cats
