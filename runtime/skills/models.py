"""Skill registry data models for the HappyRanch runtime-managed skill policy v1.

These models encode the registry metadata, policy classifications, approval
states, and resolved-skill output shapes as defined in the THR-055 product spec.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class PolicyClass(str, Enum):
    """Governance classification for catalog admission and version approval.

    - standard_operational: workflow guidance, repo conventions, role playbooks.
    - high_impact_policy: pricing, legal, security, release policy, escalation.
    - system_contract: runtime protocol and mandatory operating-contract skills.
      These are OUTSIDE the toggleable catalog.
    """

    STANDARD_OPERATIONAL = "standard_operational"
    HIGH_IMPACT_POLICY = "high_impact_policy"
    SYSTEM_CONTRACT = "system_contract"


class ApprovalState(str, Enum):
    """Approval lifecycle state for a skill entry."""

    APPROVED = "approved"
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    REJECTED = "rejected"
    DEPRECATED = "deprecated"


class SkillStatus(str, Enum):
    """Runtime enablement status."""

    ENABLED = "enabled"
    DISABLED = "disabled"


def _coerce_enum(enum_cls, value) -> Any:
    """Coerce a value into the given enum class, passing through already-correct values."""
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value.lower())
        except ValueError:
            return value
    return value


@dataclass
class SkillEntry:
    """A single managed skill as read from the registry (skill.yaml).

    Required fields per spec: id, slug, name, version, description,
    when_to_use, owner, source, policy_class, approval_state, approved_by,
    approved_at, status.

    Optional: compatibility, tags, supersedes, reviewed_at, review_notes.
    """

    # Required fields
    id: str  # namespaced hr:<slug>
    slug: str
    name: str
    version: str
    description: str
    when_to_use: str
    owner: str
    source: str
    policy_class: PolicyClass
    approval_state: ApprovalState
    approved_by: Optional[str]
    approved_at: Optional[datetime]
    status: SkillStatus

    # Optional fields
    compatibility: Optional[dict[str, list[str]]] = None
    tags: Optional[list[str]] = None
    supersedes: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None
    approved_version: Optional[str] = None  # version for which this approval was granted

    # Internal — not from YAML
    skill_md_path: Optional[Path] = None

    def __post_init__(self):
        """Coerce enum fields from strings."""
        self.policy_class = _coerce_enum(PolicyClass, self.policy_class)
        self.approval_state = _coerce_enum(ApprovalState, self.approval_state)
        self.status = _coerce_enum(SkillStatus, self.status)

    def __repr__(self) -> str:
        pc = self.policy_class.value if isinstance(self.policy_class, PolicyClass) else str(self.policy_class)
        return f"SkillEntry(id={self.id!r}, version={self.version!r}, policy_class={pc!r})"


@dataclass
class EligibilityRule:
    """An allow or deny rule from the eligibility policy, with provenance."""

    scope: str  # "org", "team", "agent"
    id: str  # the org slug, team name, or agent name
    skill_id: str  # the skill id this rule references
    action: str  # "allow" or "deny"


@dataclass
class ResolvedSkill:
    """A skill that passed eligibility resolution, with provenance."""

    skill: SkillEntry
    allowed_by: list[EligibilityRule] = field(default_factory=list)
    denied_by: list[EligibilityRule] = field(default_factory=list)

    @property
    def is_allowed(self) -> bool:
        return len(self.allowed_by) > 0 and len(self.denied_by) == 0


@dataclass
class ExposedSkill:
    """A skill that passed BOTH the catalog gate AND the eligibility gate.

    This is what the session skill index receives.
    """

    skill: SkillEntry
    catalog_approved: bool
    allowed_by: list[EligibilityRule] = field(default_factory=list)
    denied_by: list[EligibilityRule] = field(default_factory=list)


@dataclass
class GateResult:
    """Result of a single gate check (catalog or eligibility)."""

    passed: bool
    reason: str


# The set of approval states that pass the catalog gate
CATALOG_APPROVED_STATES: set[ApprovalState] = {ApprovalState.APPROVED}

# The set of approval states that explicitly fail
CATALOG_BLOCKED_STATES: set[ApprovalState] = {
    ApprovalState.DRAFT,
    ApprovalState.PENDING_REVIEW,
    ApprovalState.REJECTED,
    ApprovalState.DEPRECATED,
}
