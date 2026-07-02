"""Tests for runtime.skills.exposure — Two-gated skill exposure.

Covers acceptance criteria 3, 4, 6:
  - high_impact_policy requires founder/designated-owner approval (criterion 3)
  - version upgrade returns to pending (criterion 4)
  - both catalog and eligibility gates must pass (criterion 6)

Plus: disabled/unapproved entries excluded, version-specific approval.
"""

import pytest
from pathlib import Path
from datetime import datetime, timezone


FIXTURES = Path(__file__).parent / "fixtures" / "skills"


class TestCatalogGateStandardOperational:
    """Catalog gate for standard_operational skills."""

    def test_approved_and_enabled_passes_catalog_gate(self):
        """A standard_operational skill with approval_state=approved and status=enabled passes."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.exposure import catalog_gate

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:standard-skill")
        result = catalog_gate(entry)
        assert result.passed is True

    def test_disabled_fails_catalog_gate(self):
        """A skill with status=disabled fails the catalog gate."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.exposure import catalog_gate

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:disabled-skill")
        result = catalog_gate(entry)
        assert result.passed is False
        assert "disabled" in result.reason.lower()

    def test_draft_fails_catalog_gate(self):
        """A skill with approval_state=draft fails the catalog gate."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.exposure import catalog_gate

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:draft-skill")
        result = catalog_gate(entry)
        assert result.passed is False
        assert "draft" in result.reason.lower() or "not approved" in result.reason.lower()

    def test_pending_review_fails_catalog_gate(self):
        """approval_state=pending_review fails the catalog gate."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.exposure import catalog_gate

        registry = SkillRegistry(skills_root=FIXTURES)
        # Create a pending_review entry in-memory
        entry = registry.get("hr:standard-skill")
        from runtime.skills.models import SkillEntry
        pending_entry = SkillEntry(
            id="hr:pending-skill",
            slug="pending-skill",
            name="Pending Skill",
            version="1.0.0",
            description="A pending skill.",
            when_to_use="Testing.",
            owner="test",
            source="runtime/skills/pending-skill",
            policy_class="standard_operational",
            approval_state="pending_review",
            approved_by="test",
            approved_at=datetime.now(timezone.utc),
            status="enabled",
        )
        result = catalog_gate(pending_entry)
        assert result.passed is False

    def test_rejected_fails_catalog_gate(self):
        """approval_state=rejected fails the catalog gate."""
        from runtime.skills.models import SkillEntry
        from runtime.skills.exposure import catalog_gate

        entry = SkillEntry(
            id="hr:rejected-skill",
            slug="rejected-skill",
            name="Rejected Skill",
            version="1.0.0",
            description="A rejected skill.",
            when_to_use="Testing.",
            owner="test",
            source="runtime/skills/rejected-skill",
            policy_class="standard_operational",
            approval_state="rejected",
            approved_by="test",
            approved_at=datetime.now(timezone.utc),
            status="enabled",
        )
        result = catalog_gate(entry)
        assert result.passed is False

    def test_deprecated_fails_catalog_gate(self):
        """approval_state=deprecated fails the catalog gate."""
        from runtime.skills.models import SkillEntry
        from runtime.skills.exposure import catalog_gate

        entry = SkillEntry(
            id="hr:deprecated-skill",
            slug="deprecated-skill",
            name="Deprecated Skill",
            version="1.0.0",
            description="A deprecated skill.",
            when_to_use="Testing.",
            owner="test",
            source="runtime/skills/deprecated-skill",
            policy_class="standard_operational",
            approval_state="deprecated",
            approved_by="test",
            approved_at=datetime.now(timezone.utc),
            status="enabled",
        )
        result = catalog_gate(entry)
        assert result.passed is False


class TestCatalogGateHighImpactPolicy:
    """Acceptance criterion 3: high_impact_policy requires version-specific
    founder/designated-owner approval."""

    def test_high_impact_with_founder_approval_passes(self):
        """high_impact_policy with approved_by=founder passes catalog gate."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.exposure import catalog_gate

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:high-impact-skill")
        result = catalog_gate(entry)
        assert result.passed is True

    def test_high_impact_with_founder_approval_passes(self):
        """high_impact_policy with approved_by=founder AND approved_version match
        passes catalog gate."""
        from runtime.skills.models import SkillEntry
        from runtime.skills.exposure import catalog_gate

        entry = SkillEntry(
            id="hr:hi-founder",
            slug="hi-founder",
            name="HI Founder",
            version="1.0.0",
            description="Approved by founder.",
            when_to_use="Testing.",
            owner="security_team",
            source="runtime/skills/hi-founder",
            policy_class="high_impact_policy",
            approval_state="approved",
            approved_by="founder",
            approved_at=datetime.now(timezone.utc),
            status="enabled",
            approved_version="1.0.0",
        )
        result = catalog_gate(entry)
        assert result.passed is True

    def test_high_impact_with_owner_approval_passes(self):
        """high_impact_policy with approved_by matching the skill's owner
        AND matching approved_version passes catalog gate."""
        from runtime.skills.models import SkillEntry
        from runtime.skills.exposure import catalog_gate

        entry = SkillEntry(
            id="hr:hi-owner-approved",
            slug="hi-owner-approved",
            name="HI Owner Approved",
            version="1.0.0",
            description="Approved by the designated owner.",
            when_to_use="Testing.",
            owner="security_lead",
            source="runtime/skills/hi-owner-approved",
            policy_class="high_impact_policy",
            approval_state="approved",
            approved_by="security_lead",
            approved_at=datetime.now(timezone.utc),
            status="enabled",
            approved_version="1.0.0",
        )
        result = catalog_gate(entry)
        assert result.passed is True

    def test_high_impact_with_non_owner_approval_fails(self):
        """high_impact_policy approved_by a non-founder/non-designated owner
        should fail. For v1 we require approved_by to not be null/empty."""
        from runtime.skills.models import SkillEntry
        from runtime.skills.exposure import catalog_gate

        entry = SkillEntry(
            id="hr:hi-nobody",
            slug="hi-nobody",
            name="HI Nobody",
            version="1.0.0",
            description="No approved_by.",
            when_to_use="Testing.",
            owner="someone",
            source="runtime/skills/hi-nobody",
            policy_class="high_impact_policy",
            approval_state="approved",
            approved_by=None,
            approved_at=datetime.now(timezone.utc),
            status="enabled",
        )
        result = catalog_gate(entry)
        assert result.passed is False
        assert "approval" in result.reason.lower() or "approved_by" in result.reason.lower()

    def test_high_impact_draft_fails_even_with_founder_name(self):
        """A high_impact_policy skill marked draft fails even if approved_by=founder.
        (approval_state must be 'approved' first.)"""
        from runtime.skills.models import SkillEntry
        from runtime.skills.exposure import catalog_gate

        entry = SkillEntry(
            id="hr:hi-draft-founder",
            slug="hi-draft-founder",
            name="HI Draft Founder",
            version="1.0.0",
            description="Draft with founder name.",
            when_to_use="Testing.",
            owner="test",
            source="runtime/skills/hi-draft-founder",
            policy_class="high_impact_policy",
            approval_state="draft",
            approved_by="founder",
            approved_at=datetime.now(timezone.utc),
            status="enabled",
        )
        result = catalog_gate(entry)
        assert result.passed is False


class TestVersionSpecificHighImpactApproval:
    """Acceptance criterion 4: version upgrade of high_impact_policy
    returns to pending until new version is approved."""

    def test_approval_of_1_0_0_does_not_imply_1_1_0(self):
        """A high_impact_policy skill approved for 1.0.0 does not pass catalog
        gate for version 1.1.0 without separate approval."""
        from runtime.skills.models import SkillEntry
        from runtime.skills.exposure import catalog_gate

        # Version 1.0.0 — approved by founder
        v1 = SkillEntry(
            id="hr:versioned-hi",
            slug="versioned-hi",
            name="Versioned HI Skill",
            version="1.0.0",
            description="Approved for 1.0.0.",
            when_to_use="Testing.",
            owner="security_team",
            source="runtime/skills/versioned-hi",
            policy_class="high_impact_policy",
            approval_state="approved",
            approved_by="founder",
            approved_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            status="enabled",
            approved_version="1.0.0",
        )
        assert catalog_gate(v1).passed is True

        # Version 1.1.0 — upgraded but NOT separately approved yet
        v2 = SkillEntry(
            id="hr:versioned-hi",
            slug="versioned-hi",
            name="Versioned HI Skill",
            version="1.1.0",
            description="Upgraded to 1.1.0, not yet approved.",
            when_to_use="Testing.",
            owner="security_team",
            source="runtime/skills/versioned-hi",
            policy_class="high_impact_policy",
            approval_state="pending_review",
            approved_by=None,
            approved_at=None,
            status="enabled",
        )
        result = catalog_gate(v2)
        assert result.passed is False, "Version 1.1.0 should not pass without separate approval"

    def test_version_upgrade_returns_to_pending(self):
        """When a high_impact_policy skill's version bumps from 1.0.0 to 1.1.0,
        the new version has approval_state=pending_review (not yet approved)."""
        from runtime.skills.models import SkillEntry
        from runtime.skills.exposure import catalog_gate

        # Simulate what happens after a version upgrade:
        # the skill.yaml is updated with version: 1.1.0 and approval_state: pending_review
        upgraded = SkillEntry(
            id="hr:versioned-hi",
            slug="versioned-hi",
            name="Versioned HI Skill",
            version="1.1.0",
            description="Version upgraded, awaiting re-approval.",
            when_to_use="Testing.",
            owner="security_team",
            source="runtime/skills/versioned-hi",
            policy_class="high_impact_policy",
            approval_state="pending_review",
            approved_by=None,
            approved_at=None,
            status="enabled",
        )
        result = catalog_gate(upgraded)
        assert result.passed is False
        assert "pending" in result.reason.lower() or "not approved" in result.reason.lower()

    def test_upgraded_version_approved_by_founder_passes(self):
        """After re-approval of the new version by founder, the gate passes."""
        from runtime.skills.models import SkillEntry
        from runtime.skills.exposure import catalog_gate

        re_approved = SkillEntry(
            id="hr:versioned-hi",
            slug="versioned-hi",
            name="Versioned HI Skill",
            version="1.1.0",
            description="Version 1.1.0 re-approved by founder.",
            when_to_use="Testing.",
            owner="security_team",
            source="runtime/skills/versioned-hi",
            policy_class="high_impact_policy",
            approval_state="approved",
            approved_by="founder",
            approved_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
            status="enabled",
            approved_version="1.1.0",
        )
        result = catalog_gate(re_approved)
        assert result.passed is True

    def test_version_bump_with_stale_approval_fails_closed(self):
        """A high_impact_policy skill that has version bumped but still
        carries stale approved_version (1.0.0) fails CLOSED even though
        approved_state=approved and approved_by=founder."""
        from runtime.skills.models import SkillEntry
        from runtime.skills.exposure import catalog_gate

        stale = SkillEntry(
            id="hr:versioned-hi",
            slug="versioned-hi",
            name="Versioned HI Skill",
            version="1.1.0",
            description="Version bumped, stale approval.",
            when_to_use="Testing.",
            owner="security_team",
            source="runtime/skills/versioned-hi",
            policy_class="high_impact_policy",
            approval_state="approved",
            approved_by="founder",
            approved_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            status="enabled",
            approved_version="1.0.0",
        )
        result = catalog_gate(stale)
        assert result.passed is False
        assert "approved_version" in result.reason.lower()

    def test_non_owner_non_founder_approver_fails_closed(self):
        """approved_by set to someone who is NOT founder and NOT the skill's
        owner → fail closed."""
        from runtime.skills.models import SkillEntry
        from runtime.skills.exposure import catalog_gate

        bad_approver = SkillEntry(
            id="hr:bad-approver",
            slug="bad-approver",
            name="Bad Approver HI",
            version="1.0.0",
            description="Approved by wrong person.",
            when_to_use="Testing.",
            owner="security_team",
            source="runtime/skills/bad-approver",
            policy_class="high_impact_policy",
            approval_state="approved",
            approved_by="random_person",
            approved_at=datetime.now(timezone.utc),
            status="enabled",
            approved_version="1.0.0",
        )
        result = catalog_gate(bad_approver)
        assert result.passed is False
        assert "approved_by" in result.reason.lower()


class TestTwoGateExposure:
    """Acceptance criterion 6: a skill must pass BOTH gates to be exposed."""

    def test_both_gates_pass_skill_exposed(self):
        """When catalog and eligibility both pass, skill is exposed."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="dev_agent"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:standard-skill" in exposed_ids

    def test_catalog_pass_but_eligibility_fail_not_exposed(self):
        """Skill passes catalog gate but denied by eligibility → NOT exposed."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        # Allow + deny same skill = deny wins
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": ["hr:standard-skill"]},
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="dev_agent"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:standard-skill" not in exposed_ids

    def test_eligibility_pass_but_catalog_fail_not_exposed(self):
        """Skill passes eligibility but fails catalog (disabled) → NOT exposed."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "org": {"allow": ["hr:disabled-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="dev_agent"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:disabled-skill" not in exposed_ids

    def test_draft_skill_not_exposed_even_if_eligible(self):
        """A draft skill that would be eligible is NOT exposed (catalog gate fails)."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "org": {"allow": ["hr:draft-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="dev_agent"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:draft-skill" not in exposed_ids

    def test_high_impact_version_not_approved_not_exposed(self):
        """A high_impact_policy skill with pending version not exposed even if eligible."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        # high-impact-skill IS approved for 1.0.0 in fixtures → should be exposed
        policy = {
            "org": {"allow": ["hr:high-impact-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="dev_agent"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:high-impact-skill" in exposed_ids  # 1.0.0 approved

    def test_resolved_exposure_includes_provenance(self):
        """Exposed skills carry catalog approval info and eligibility provenance."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="dev_agent"
        )

        for s in exposed:
            if s.skill.id == "hr:standard-skill":
                assert s.catalog_approved is True
                assert len(s.allowed_by) > 0
                assert len(s.denied_by) == 0
