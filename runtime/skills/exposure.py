"""Skill exposure for the runtime-managed skill policy.

A skill is exposed to a session when:

1. CATALOG GATE (presence + enabled):
   - The skill is present in the catalog.
   - status == enabled.
   - Disabled => NOT exposed.

2. ELIGIBILITY GATE:
   - Passes the EligibilityResolver (additive inheritance, deny wins).
   - policy_class still scopes eligibility (manage-agent/manage-repo ->
     managers/operators).

Approval-gate fields removed per THR-055 seq 55 (founder-authorized).
For first-party skills, runtime approval duplicates the release pipeline.
"""

from __future__ import annotations

from runtime.skills.models import (
    ExposedSkill,
    GateResult,
    SkillEntry,
    SkillStatus,
)
from runtime.skills.registry import SkillRegistry
from runtime.skills.resolver import EligibilityResolver


def catalog_gate(entry: SkillEntry) -> GateResult:
    """Check whether a single skill entry passes the catalog gate.

    - The skill is present in the catalog (registry loaded it).
    - status must be 'enabled'.
    - Disabled skills fail.
    """
    if entry.status != SkillStatus.ENABLED:
        return GateResult(False, f"Skill {entry.id} is {entry.status.value}")

    return GateResult(True, f"Skill {entry.id}@{entry.version} passes catalog gate")


def resolve_exposed_skills(
    registry: SkillRegistry,
    resolver: EligibilityResolver,
    org: str,
    team: str,
    agent: str,
) -> list[ExposedSkill]:
    """Resolve which skills are exposed to a session.

    Both gates must pass: catalog gate + eligibility gate.
    Returns ExposedSkill objects with full provenance.
    """
    catalog = registry.list_all()
    eligible = resolver.resolve(catalog, org=org, team=team, agent=agent)

    # Build a map of eligible skill id -> ResolvedSkill
    eligible_by_id = {r.skill.id: r for r in eligible}

    exposed: list[ExposedSkill] = []
    for entry in catalog:
        gate = catalog_gate(entry)
        if not gate.passed:
            continue

        resolved = eligible_by_id.get(entry.id)
        if resolved is None or not resolved.is_allowed:
            continue

        exposed.append(ExposedSkill(
            skill=entry,
            allowed_by=resolved.allowed_by,
            denied_by=resolved.denied_by,
        ))

    return exposed
