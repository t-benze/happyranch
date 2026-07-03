"""Tests for runtime.skills.resolver — EligibilityResolver.

Covers acceptance criteria 5: additive inheritance with explicit deny, deny wins.
And criterion 6: skill must pass both catalog approval AND eligibility.
"""

import pytest
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures" / "skills"


def _make_approved_catalog():
    """Helper: load the registry and return only approved+enabled skills
    (mimicking the catalog gate which is tested separately in exposure tests)."""
    from runtime.skills.registry import SkillRegistry

    registry = SkillRegistry(skills_root=FIXTURES)
    return [
        e for e in registry.list_all()
        if e.approval_state == "approved" and e.status == "enabled"
    ]


class TestEligibilityResolverBasic:
    """Basic eligibility resolution."""

    def test_no_policy_allows_nothing(self):
        """With no eligibility policy set, empty allow union admits NOTHING
        per the spec formula: approved_catalog ∩ (org ∪ team ∪ agent) MINUS denies."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        resolver = EligibilityResolver({})
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="dev_agent")

        assert len(results) == 0, "Empty allow union must admit nothing"

    def test_org_allow_grants_access(self):
        """Org-level allow list grants eligibility."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="dev_agent")

        resolved_ids = {r.skill.id for r in results}
        assert "hr:standard-skill" in resolved_ids
        # Not in allow list → excluded
        assert "hr:high-impact-skill" not in resolved_ids

    def test_team_allow_grants_access(self):
        """Team-level allow list grants eligibility."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "teams": {
                "engineering": {"allow": ["hr:high-impact-skill"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="dev_agent")

        resolved_ids = {r.skill.id for r in results}
        assert "hr:high-impact-skill" in resolved_ids
        assert "hr:standard-skill" not in resolved_ids

    def test_agent_allow_grants_access(self):
        """Agent-level allow list grants eligibility."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "agents": {
                "dev_agent": {"allow": ["hr:standard-skill"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="dev_agent")

        resolved_ids = {r.skill.id for r in results}
        assert "hr:standard-skill" in resolved_ids


class TestDenyWinsPrecedence:
    """Acceptance criterion 5: deny wins over allow at any scope."""

    def test_org_deny_overrides_org_allow(self):
        """When the same scope both allows and denies, deny wins."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": ["hr:standard-skill"]},
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="dev_agent")

        resolved_ids = {r.skill.id for r in results}
        assert "hr:standard-skill" not in resolved_ids

    def test_team_deny_overrides_org_allow(self):
        """Team deny wins over org allow."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": []},
            "teams": {
                "engineering": {"allow": [], "deny": ["hr:standard-skill"]},
            },
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="dev_agent")

        resolved_ids = {r.skill.id for r in results}
        assert "hr:standard-skill" not in resolved_ids, "Team deny should override org allow"

    def test_agent_deny_overrides_org_and_team_allow(self):
        """Agent deny wins over org+team allow."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": []},
            "teams": {
                "engineering": {"allow": ["hr:standard-skill"], "deny": []},
            },
            "agents": {
                "dev_agent": {"allow": [], "deny": ["hr:standard-skill"]},
            },
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="dev_agent")

        resolved_ids = {r.skill.id for r in results}
        assert "hr:standard-skill" not in resolved_ids, "Agent deny should override all allows"

    def test_org_deny_overrides_team_and_agent_allow(self):
        """Org deny wins over all lower-level allows."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "org": {"allow": [], "deny": ["hr:high-impact-skill"]},
            "teams": {
                "engineering": {"allow": ["hr:high-impact-skill"], "deny": []},
            },
            "agents": {
                "dev_agent": {"allow": ["hr:high-impact-skill"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="dev_agent")

        resolved_ids = {r.skill.id for r in results}
        assert "hr:high-impact-skill" not in resolved_ids, "Org deny should override all allows"


class TestAdditiveInheritance:
    """Acceptance criterion 5: additive inheritance (union of allows)."""

    def test_org_plus_team_allow_union(self):
        """Org and team allows are additive (union)."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": []},
            "teams": {
                "engineering": {"allow": ["hr:high-impact-skill"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="dev_agent")

        resolved_ids = {r.skill.id for r in results}
        assert "hr:standard-skill" in resolved_ids
        assert "hr:high-impact-skill" in resolved_ids

    def test_org_plus_agent_allow_union(self):
        """Org and agent allows are additive."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": []},
            "agents": {
                "dev_agent": {"allow": ["hr:high-impact-skill"], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="dev_agent")

        resolved_ids = {r.skill.id for r in results}
        assert "hr:standard-skill" in resolved_ids
        assert "hr:high-impact-skill" in resolved_ids

    def test_union_allows_cascade_properly_with_partial_deny(self):
        """Complex scenario: org allows A+B, team denies A, agent allows C."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "org": {"allow": ["hr:standard-skill", "hr:minimal-skill"], "deny": []},
            "teams": {
                "engineering": {"allow": ["hr:high-impact-skill"], "deny": ["hr:standard-skill"]},
            },
            "agents": {
                "dev_agent": {"allow": [], "deny": []},
            },
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="dev_agent")

        resolved_ids = {r.skill.id for r in results}
        # standard-skill: org allows but team denies → excluded
        assert "hr:standard-skill" not in resolved_ids
        # minimal-skill: org allows → included
        assert "hr:minimal-skill" in resolved_ids
        # high-impact-skill: team allows → included
        assert "hr:high-impact-skill" in resolved_ids


class TestEligibilityProvenance:
    """Provenance tracking per resolved skill."""

    def test_resolved_skill_records_allow_provenance(self):
        """Each resolved skill records which rule(s) allowed it."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="dev_agent")

        for r in results:
            if r.skill.id == "hr:standard-skill":
                assert len(r.allowed_by) > 0
                assert any(rule.scope == "org" and rule.id == "happyranch" for rule in r.allowed_by)
                assert len(r.denied_by) == 0

    def test_resolved_skill_records_deny_provenance_for_blocked(self):
        """Blocked skills should NOT appear, but the resolver's full report
        should capture the deny provenance for diagnostic purposes.
        (Blocked skills are excluded from results, but we test the internal
        deny tracking via a scenario where deny is recorded.)"""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": ["hr:standard-skill"]},
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="dev_agent")

        # The skill should be excluded
        resolved_ids = {r.skill.id for r in results}
        assert "hr:standard-skill" not in resolved_ids

        # But the blocked report should show it
        blocked = resolver.get_blocked(catalog, org="happyranch", team="engineering", agent="dev_agent")
        assert "hr:standard-skill" in blocked
        assert any(rule.scope == "org" for rule in blocked["hr:standard-skill"])


class TestUnknownSkillIds:
    """Unknown skill ids in eligibility config produce warnings and are excluded."""

    def test_unknown_skill_id_in_allow_does_not_crash(self):
        """Unknown skills in allow list are silently ignored (no crash), excluded from results."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "org": {"allow": ["hr:nonexistent-skill", "hr:standard-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="dev_agent")

        resolved_ids = {r.skill.id for r in results}
        assert "hr:standard-skill" in resolved_ids
        assert "hr:nonexistent-skill" not in resolved_ids

    def test_unknown_skill_id_produces_warning(self):
        """Unknown skills produce validation warnings."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "org": {"allow": ["hr:nonexistent-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        warnings = resolver.validate(catalog)
        assert len(warnings) > 0
        assert any("nonexistent-skill" in w for w in warnings)


class TestEligibilityYamlShape:
    """Tests for the eligibility YAML parsing (skills: org/teams/agents)."""

    def test_parse_full_yaml_shape(self):
        """The resolver accepts the canonical YAML shape from the spec."""
        from runtime.skills.resolver import EligibilityResolver

        policy_yaml = {
            "org": {"allow": ["hr:debugging"], "deny": []},
            "teams": {
                "engineering": {
                    "allow": ["hr:repo-review"],
                    "deny": ["hr:customer-comms-policy"],
                },
            },
            "agents": {
                "dev_agent": {
                    "allow": ["hr:runtime-skill-authoring"],
                    "deny": [],
                },
            },
        }
        # Should not raise
        resolver = EligibilityResolver(policy_yaml)
        assert resolver is not None

    def test_missing_team_in_policy_no_error(self):
        """When the requested team has no policy entry, it behaves as no-ops."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": []},
            # No teams section
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="nonexistent_team", agent="dev_agent")

        resolved_ids = {r.skill.id for r in results}
        assert "hr:standard-skill" in resolved_ids

    def test_missing_agent_in_policy_no_error(self):
        """When the requested agent has no policy entry, it behaves as no-ops."""
        from runtime.skills.resolver import EligibilityResolver

        catalog = _make_approved_catalog()
        policy = {
            "org": {"allow": ["hr:standard-skill"], "deny": []},
        }
        resolver = EligibilityResolver(policy)
        results = resolver.resolve(catalog, org="happyranch", team="engineering", agent="nonexistent_agent")

        resolved_ids = {r.skill.id for r in results}
        assert "hr:standard-skill" in resolved_ids
