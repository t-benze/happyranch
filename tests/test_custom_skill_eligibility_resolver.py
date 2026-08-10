"""Unit tests for the dark THR-055 B2 custom-skill visibility resolver."""

from __future__ import annotations

from itertools import product

import pytest

from runtime.skills.eligibility import (
    EligibilityRecipient,
    EligibilityRule,
    SkillEligibilityState,
    resolve_custom_skill_eligibility,
)


RECIPIENT = EligibilityRecipient(agent_name="dev_agent", teams=("engineering",))
LIVE_SKILL = SkillEligibilityState(
    retired=False,
    current_version_validation_state="valid",
)


def _rule(scope: str, effect: str) -> EligibilityRule:
    targets = {"org": None, "team": "engineering", "agent": "dev_agent"}
    return EligibilityRule(scope_type=scope, scope_target=targets[scope], effect=effect)


def _expected_reason(org: str, team: str, agent: str) -> str:
    if (org, team, agent) == ("none", "none", "none"):
        return "no_eligibility_policy"
    for scope, value in (("agent", agent), ("team", team), ("org", org)):
        if value == "deny":
            return f"{scope}_deny"
    for scope, value in (("agent", agent), ("team", team), ("org", org)):
        if value == "allow":
            return f"{scope}_allow"
    return "no_matching_rule"


def _rules(org: str, team: str, agent: str) -> tuple[EligibilityRule, ...]:
    return tuple(
        _rule(scope, effect)
        for scope, effect in (("org", org), ("team", team), ("agent", agent))
        if effect != "none"
    )


PERMUTATIONS = list(product(("none", "allow", "deny"), repeat=3))


@pytest.mark.parametrize(
    ("org", "team", "agent"),
    PERMUTATIONS,
    ids=lambda value: value,
)
def test_resolves_every_org_team_agent_rule_permutation(org: str, team: str, agent: str) -> None:
    """Every live-rule combination honors deny-first, then specificity."""
    rules = _rules(org, team, agent)

    result = resolve_custom_skill_eligibility(LIVE_SKILL, rules, RECIPIENT)

    expected_reason = _expected_reason(org, team, agent)
    expected_scope = expected_reason.split("_")[0] if expected_reason != "no_matching_rule" else None
    expected_rule = next((rule for rule in rules if rule.scope_type == expected_scope), None)
    assert result.reason == expected_reason
    assert result.visible is expected_reason.endswith("_allow")
    assert result.winning_rule is expected_rule


@pytest.mark.parametrize(
    ("rules", "expected_reason"),
    [
        pytest.param(
            (_rule("agent", "allow"), _rule("org", "deny")),
            "org_deny",
            id="agent_allow_plus_org_deny_is_hidden_by_org_deny",
        ),
        pytest.param(
            (_rule("team", "allow"), _rule("org", "deny")),
            "org_deny",
            id="team_allow_plus_org_deny_is_hidden_by_org_deny",
        ),
    ],
)
def test_deny_wins_across_scope_depth(
    rules: tuple[EligibilityRule, ...], expected_reason: str
) -> None:
    result = resolve_custom_skill_eligibility(LIVE_SKILL, rules, RECIPIENT)

    org_deny = next(rule for rule in rules if rule.scope_type == "org")
    assert result.visible is False
    assert result.reason == expected_reason
    assert result.winning_rule is org_deny


def test_retired_skill_is_hidden_before_rule_evaluation() -> None:
    result = resolve_custom_skill_eligibility(
        SkillEligibilityState(retired=True, current_version_validation_state="valid"),
        (_rule("agent", "allow"),),
        RECIPIENT,
    )

    assert result.visible is False
    assert result.reason == "retired"
    assert result.winning_rule is None


@pytest.mark.parametrize("validation_state", (None, "invalid", "validation_required"))
def test_non_valid_current_version_is_hidden(validation_state: str | None) -> None:
    result = resolve_custom_skill_eligibility(
        SkillEligibilityState(retired=False, current_version_validation_state=validation_state),
        (_rule("agent", "allow"),),
        RECIPIENT,
    )

    assert result.visible is False
    assert result.reason == "current_version_invalid"
    assert result.winning_rule is None


def test_empty_rule_list_is_hidden() -> None:
    result = resolve_custom_skill_eligibility(LIVE_SKILL, (), RECIPIENT)

    assert result.visible is False
    assert result.reason == "no_eligibility_policy"
    assert result.winning_rule is None


def test_nonmatching_rules_are_hidden() -> None:
    other_agent = EligibilityRule("agent", "other_agent", "allow")
    other_team = EligibilityRule("team", "other_team", "deny")

    result = resolve_custom_skill_eligibility(LIVE_SKILL, (other_agent, other_team), RECIPIENT)

    assert result.visible is False
    assert result.reason == "no_matching_rule"
    assert result.winning_rule is None
