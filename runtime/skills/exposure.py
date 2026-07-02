"""Two-gated skill exposure for the runtime-managed skill policy.

A skill is exposed to a session ONLY when BOTH gates pass:

1. CATALOG GATE:
   - approval_state == approved
   - status == enabled
   - For high_impact_policy: version-specific founder/designated-owner approval
     is recorded for THAT EXACT version.
   - draft, pending_review, rejected, deprecated, disabled, or missing
     approval => NOT exposed.

2. ELIGIBILITY GATE:
   - Passes the EligibilityResolver (additive inheritance, deny wins).

Version specificity for high_impact_policy:
   - Approval of 1.0.0 does NOT imply approval of 1.1.0.
   - A version upgrade returns the skill to pending until re-approved.
"""

from __future__ import annotations

from runtime.skills.models import (
    ApprovalState,
    ExposedSkill,
    GateResult,
    PolicyClass,
    SkillEntry,
    SkillStatus,
    CATALOG_APPROVED_STATES,
    CATALOG_BLOCKED_STATES,
)
from runtime.skills.registry import SkillRegistry
from runtime.skills.resolver import EligibilityResolver


def catalog_gate(entry: SkillEntry) -> GateResult:
    """Check whether a single skill entry passes the catalog gate.

    - approval_state must be 'approved'.
    - status must be 'enabled'.
    - For high_impact_policy: approved_by must name founder or designated owner
      AND the approval must be for THIS version.
    """
    # Status check
    if entry.status != SkillStatus.ENABLED:
        return GateResult(False, f"Skill {entry.id} is {entry.status.value}")

    # Approval state check
    if entry.approval_state in CATALOG_BLOCKED_STATES:
        return GateResult(
            False,
            f"Skill {entry.id} approval_state is {entry.approval_state.value}",
        )

    if entry.approval_state not in CATALOG_APPROVED_STATES:
        return GateResult(
            False,
            f"Skill {entry.id} has unknown approval_state: {entry.approval_state}",
        )

    # High-impact policy: requires version-specific founder/designated-owner approval
    if entry.policy_class == PolicyClass.HIGH_IMPACT_POLICY:
        # Gate 1: approved_by must be founder or the skill's designated owner
        if not entry.approved_by:
            return GateResult(
                False,
                f"High-impact skill {entry.id}@{entry.version} has no approved_by — "
                f"founder or designated-owner approval required",
            )
        valid_approvers = {"founder"}
        if entry.owner:
            valid_approvers.add(entry.owner)
        if entry.approved_by not in valid_approvers:
            return GateResult(
                False,
                f"High-impact skill {entry.id}@{entry.version} approved_by "
                f"'{entry.approved_by}' is not founder or designated owner "
                f"(owner={entry.owner})",
            )
        # Gate 2: approval must be version-specific — approved_version must
        # match the entry's current version. If missing (back-compat with
        # entries that predate approved_version), fail CLOSED.
        if not entry.approved_version or entry.approved_version != entry.version:
            return GateResult(
                False,
                f"High-impact skill {entry.id}@{entry.version} has not been "
                f"approved for this version (approved_version={entry.approved_version})",
            )

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
            catalog_approved=True,
            allowed_by=resolved.allowed_by,
            denied_by=resolved.denied_by,
        ))

    return exposed
