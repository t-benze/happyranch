"""Pure visibility resolution for dark THR-055 B2 custom skills.

This module deliberately does not read persistence or participate in routing
or materialization.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class EligibilityRule:
    """A current custom-skill visibility rule supplied by a caller."""

    scope_type: str
    scope_target: str | None
    effect: str


@dataclass(frozen=True)
class SkillEligibilityState:
    """The skill state relevant to visibility."""

    retired: bool
    current_version_validation_state: str | None


@dataclass(frozen=True)
class EligibilityRecipient:
    """The agent and team memberships to evaluate."""

    agent_name: str
    teams: tuple[str, ...]


@dataclass(frozen=True)
class EligibilityResult:
    """A deterministic custom-skill visibility decision."""

    visible: bool
    reason: str
    winning_rule: EligibilityRule | None


def _matching_rule(
    rules: Sequence[EligibilityRule],
    recipient: EligibilityRecipient,
    *,
    scope_type: str,
    effect: str,
) -> EligibilityRule | None:
    for rule in rules:
        if rule.scope_type != scope_type or rule.effect != effect:
            continue
        if scope_type == "org":
            return rule
        if scope_type == "team" and rule.scope_target in recipient.teams:
            return rule
        if scope_type == "agent" and rule.scope_target == recipient.agent_name:
            return rule
    return None


def resolve_custom_skill_eligibility(
    skill: SkillEligibilityState,
    rules: Sequence[EligibilityRule],
    recipient: EligibilityRecipient,
) -> EligibilityResult:
    """Resolve whether a custom skill is visible to one recipient.

    A matching deny wins across every scope before any matching allow is
    considered.  Rules are assumed to be the caller's current rule set.
    """
    if skill.retired:
        return EligibilityResult(False, "retired", None)
    if skill.current_version_validation_state != "valid":
        return EligibilityResult(False, "current_version_invalid", None)
    if not rules:
        return EligibilityResult(False, "no_eligibility_policy", None)

    for scope_type in ("agent", "team", "org"):
        if rule := _matching_rule(rules, recipient, scope_type=scope_type, effect="deny"):
            return EligibilityResult(False, f"{scope_type}_deny", rule)

    for scope_type in ("agent", "team", "org"):
        if rule := _matching_rule(rules, recipient, scope_type=scope_type, effect="allow"):
            return EligibilityResult(True, f"{scope_type}_allow", rule)

    return EligibilityResult(False, "no_matching_rule", None)
