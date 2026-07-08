"""Tests for runtime.skills.exposure — Simplified skill exposure (THR-055 seq 55).

Catalog gate now only checks status==enabled (no approval gate).
Eligibility gate unchanged — policy_class still scopes eligibility.

New acceptance criteria (THR-055 seq 55):
  - manage-agent AND manage-repo resolve EXPOSED for an eligible manager/operator
  - manage-agent AND manage-repo NOT exposed for a non-eligible agent
  - review still resolves for eligible agents
  - disabled skills remain blocked
  - system contracts unaffected
"""

import pytest
from pathlib import Path
from datetime import datetime, timezone


FIXTURES = Path(__file__).parent / "fixtures" / "skills"


# ══════════════════════════════════════════════════════════════════════════
# Catalog gate — status-only
# ══════════════════════════════════════════════════════════════════════════


class TestCatalogGateStatusOnly:
    """Catalog gate only checks status==enabled (no approval gate)."""

    def test_enabled_passes_catalog_gate(self):
        """An enabled skill passes the catalog gate regardless of policy_class."""
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

    def test_high_impact_policy_passes_catalog_gate(self):
        """high_impact_policy skill passes catalog gate (status=enabled, no approval check)."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.exposure import catalog_gate

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:high-impact-skill")
        result = catalog_gate(entry)
        assert result.passed is True

    def test_manage_agent_passes_catalog_gate(self):
        """manage-agent (high_impact_policy, status=enabled) passes catalog gate."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.exposure import catalog_gate

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:manage-agent")
        result = catalog_gate(entry)
        assert result.passed is True

    def test_manage_repo_passes_catalog_gate(self):
        """manage-repo (high_impact_policy, status=enabled) passes catalog gate."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.exposure import catalog_gate

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:manage-repo")
        result = catalog_gate(entry)
        assert result.passed is True

    def test_draft_skill_passes_catalog_gate(self):
        """Formerly-draft skill (now just status=enabled) passes catalog gate."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.exposure import catalog_gate

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:draft-skill")
        # draft-skill fixture has status=enabled, no approval_state
        result = catalog_gate(entry)
        assert result.passed is True

    def test_minimal_skill_passes_catalog_gate(self):
        """Minimal skill with status=enabled passes catalog gate."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.exposure import catalog_gate

        registry = SkillRegistry(skills_root=FIXTURES)
        entry = registry.get("hr:minimal-skill")
        result = catalog_gate(entry)
        assert result.passed is True


# ══════════════════════════════════════════════════════════════════════════
# Two-gate exposure — review skill (eligibility-scoped)
# ══════════════════════════════════════════════════════════════════════════


class TestTwoGateExposureReview:
    """Two-gate exposure tests for the review skill with team-scoped eligibility."""

    def test_review_exposed_to_engineering_team_member(self):
        """A dev_agent in the engineering team resolves review as exposed."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "teams": {
                "engineering": {"allow": ["hr:review"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="dev_agent"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:review" in exposed_ids

    def test_review_not_exposed_to_non_participant_agent(self):
        """An agent NOT in the engineering team does NOT resolve review."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "teams": {
                "engineering": {"allow": ["hr:review"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="cx", agent="support_agent"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:review" not in exposed_ids

    def test_review_exposed_to_product_lead_via_agent_scope(self):
        """product_lead (a team manager but not in engineering) resolves review via agent scope."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "teams": {
                "engineering": {"allow": ["hr:review"], "deny": []},
            },
            "agents": {
                "product_lead": {"allow": ["hr:review"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="product", agent="product_lead"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:review" in exposed_ids

    def test_review_not_exposed_to_org_wide_non_participant(self):
        """An org-wide agent with no explicit allow does NOT resolve review."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "teams": {
                "engineering": {"allow": ["hr:review"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="marketing", agent="content_creator"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:review" not in exposed_ids

    def test_review_not_exposed_with_no_policy_at_all(self):
        """Without any eligibility policy, review is NOT exposed (empty allow union)."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        resolver = EligibilityResolver({})
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="dev_agent"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:review" not in exposed_ids

    def test_review_exposed_with_provenance(self):
        """Exposed review carries correct eligibility provenance."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "teams": {
                "engineering": {"allow": ["hr:review"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="dev_agent"
        )

        for s in exposed:
            if s.skill.id == "hr:review":
                assert len(s.allowed_by) > 0
                assert any(r.scope == "team" and r.id == "engineering" for r in s.allowed_by)
                assert len(s.denied_by) == 0


# ══════════════════════════════════════════════════════════════════════════
# System contracts — review NOT a system contract
# ══════════════════════════════════════════════════════════════════════════


class TestReviewSkillNotInSystemContracts:
    """Verify review is NOT a system contract (it's standard_operational, managed catalog)."""

    def test_review_not_in_system_contracts_list(self):
        """review does not appear in the system_contracts tuple."""
        from runtime.skills.system_contracts import list_system_contracts

        contracts = list_system_contracts()
        ids = {sc.id for sc in contracts}
        assert "review" not in ids
        # The 5 system contracts remain intact
        assert "start-task" in ids
        assert "jobs" in ids
        assert "make-worktree" in ids
        assert "thread" in ids
        assert "dream" in ids


# ══════════════════════════════════════════════════════════════════════════
# Two-gate exposure — general
# ══════════════════════════════════════════════════════════════════════════


class TestTwoGateExposure:
    """A skill must pass BOTH gates (catalog = present+enabled, eligibility = allowed) to be exposed."""

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

    def test_high_impact_exposed_when_eligible(self):
        """A high_impact_policy skill that is enabled AND eligible IS exposed
        (no version-specific approval gate — THR-055 seq 55)."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        # high-impact-skill IS enabled in fixtures → should be exposed when eligible
        policy = {
            "org": {"allow": ["hr:high-impact-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="dev_agent"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:high-impact-skill" in exposed_ids

    def test_resolved_exposure_includes_provenance(self):
        """Exposed skills carry eligibility provenance."""
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
                assert len(s.allowed_by) > 0
                assert len(s.denied_by) == 0


# ══════════════════════════════════════════════════════════════════════════
# manage-agent / manage-repo — EXPOSED to eligible managers, NOT to others
# ══════════════════════════════════════════════════════════════════════════


class TestManageAgentManageRepoExposure:
    """THR-055 seq 55: manage-agent and manage-repo are now EXPOSED to
    eligible managers/operators (no approval gate blocks them). Eligibility
    still gates — non-eligible agents do NOT see them."""

    def test_manage_agent_exposed_to_engineering_manager(self):
        """manage-agent IS exposed to engineering_manager (eligible via agent scope)."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "agents": {
                "engineering_manager": {"allow": ["hr:manage-agent"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="engineering_manager"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:manage-agent" in exposed_ids, (
            "manage-agent should be EXPOSED to engineering_manager (eligible, catalog passes)"
        )

    def test_manage_repo_exposed_to_engineering_manager(self):
        """manage-repo IS exposed to engineering_manager (eligible via agent scope)."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "agents": {
                "engineering_manager": {"allow": ["hr:manage-repo"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="engineering_manager"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:manage-repo" in exposed_ids, (
            "manage-repo should be EXPOSED to engineering_manager (eligible, catalog passes)"
        )

    def test_manage_agent_not_exposed_to_non_manager_engineer(self):
        """dev_agent (engineering team worker, not a manager) does NOT resolve manage-agent."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "agents": {
                "engineering_manager": {"allow": ["hr:manage-agent"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="dev_agent"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:manage-agent" not in exposed_ids, (
            "dev_agent (non-manager) should NOT resolve manage-agent — eligibility still gates"
        )

    def test_manage_repo_not_exposed_to_non_manager_engineer(self):
        """dev_agent does NOT resolve manage-repo (eligibility still gates)."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "agents": {
                "engineering_manager": {"allow": ["hr:manage-repo"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="dev_agent"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:manage-repo" not in exposed_ids, (
            "dev_agent should NOT resolve manage-repo — eligibility still gates"
        )

    def test_manage_agent_eligible_to_product_lead(self):
        """product_lead (team manager) has eligibility for manage-agent."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "agents": {
                "product_lead": {"allow": ["hr:manage-agent"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        catalog = registry.list_all()
        resolved = resolver.resolve(catalog, org="happyranch", team="product", agent="product_lead")

        resolved_ids = {r.skill.id for r in resolved}
        assert "hr:manage-agent" in resolved_ids, (
            "product_lead should be eligible for manage-agent"
        )

    def test_manage_agent_not_org_wide(self):
        """manage-agent is NOT org-wide — non-participant agent does not resolve it."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "agents": {
                "engineering_manager": {"allow": ["hr:manage-agent"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="cx", agent="support_agent"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:manage-agent" not in exposed_ids

    def test_manage_agent_not_exposed_with_no_policy(self):
        """Without any eligibility policy, manage-agent is NOT exposed."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        resolver = EligibilityResolver({})
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="engineering_manager"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:manage-agent" not in exposed_ids

    def test_manage_agent_exposed_to_product_lead(self):
        """product_lead IS exposed to manage-agent (eligible + catalog passes)."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        policy = {
            "agents": {
                "product_lead": {"allow": ["hr:manage-agent", "hr:manage-repo"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="product", agent="product_lead"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:manage-agent" in exposed_ids
        assert "hr:manage-repo" in exposed_ids

    def test_manage_agent_disabled_not_exposed_even_when_eligible(self):
        """A disabled manage-agent is NOT exposed even when eligible."""
        from runtime.skills.models import SkillEntry
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        # Build a catalog with a disabled manage-agent
        registry = SkillRegistry(skills_root=FIXTURES)
        disabled_entry = SkillEntry(
            id="hr:manage-agent-disabled",
            slug="manage-agent-disabled",
            name="Manage Agent Disabled",
            version="1.0.0",
            description="Disabled manage-agent.",
            when_to_use="Testing.",
            owner="engineering_manager",
            source="runtime/skills/manage-agent",
            policy_class="high_impact_policy",
            status="disabled",
        )
        # Replace manage-agent in catalog
        catalog = [e for e in registry.list_all() if e.id != "hr:manage-agent"] + [disabled_entry]
        policy = {
            "agents": {
                "engineering_manager": {"allow": ["hr:manage-agent-disabled"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="engineering_manager"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:manage-agent-disabled" not in exposed_ids, (
            "Disabled manage-agent should NOT be exposed even when eligible"
        )


# ══════════════════════════════════════════════════════════════════════════
# Negative invariants — eligibility is the SOLE exposure gate (after status)
# ══════════════════════════════════════════════════════════════════════════


class TestNegativeInvariants:
    """Security: eligibility must remain intact as the sole gate. No skill
    escapes to an agent who lacks explicit allow rules."""

    def test_no_skill_leaks_with_empty_policy(self):
        """Empty eligibility policy → zero exposed skills."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        resolver = EligibilityResolver({})
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="engineering", agent="dev_agent"
        )

        assert len(exposed) == 0, (
            "No skills should be exposed with empty eligibility policy"
        )

    def test_manage_agent_not_exposed_to_consultant_head(self):
        """consultant_head (NO eligibility for manage-*) does NOT see manage-agent."""
        from runtime.skills.registry import SkillRegistry
        from runtime.skills.resolver import EligibilityResolver
        from runtime.skills.exposure import resolve_exposed_skills

        registry = SkillRegistry(skills_root=FIXTURES)
        # Real config policy: consultant_head has no skill eligibility
        policy = {
            "agents": {
                "engineering_manager": {"allow": ["hr:manage-agent", "hr:manage-repo"], "deny": []},
                "product_lead": {"allow": ["hr:review", "hr:manage-agent", "hr:manage-repo"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        exposed = resolve_exposed_skills(
            registry, resolver, org="happyranch", team="consultant", agent="consultant_head"
        )

        exposed_ids = {s.skill.id for s in exposed}
        assert "hr:manage-agent" not in exposed_ids
        assert "hr:manage-repo" not in exposed_ids
        assert "hr:review" not in exposed_ids
