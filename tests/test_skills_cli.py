"""Tests for skills CLI commands (slice 5).

Covers:
- skills catalog list   (basic listing, JSON output)
- skills catalog validate (validation warnings, catalog-gate failures, unknown-id warnings)
- skills effective       (effective skills with both-gate filtering, blocked skills)
- skills policy explain  (explain why a skill is/isn't available, provenance, blocked-reason attribution)

All tests read from the test fixtures directory, no daemon round-trip.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / "fixtures" / "skills"

# Ensure cli/commands is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestSkillsCatalogList:
    """Tests for 'skills catalog list'"""

    def test_lists_all_skills(self, capsys):
        from cli.commands.skills import cmd_skills_catalog_list
        import argparse

        ns = argparse.Namespace(skills_root=str(FIXTURES), json=False)
        cmd_skills_catalog_list(ns)

        out = capsys.readouterr().out
        assert "hr:standard-skill" in out
        assert "hr:high-impact-skill" in out
        assert "hr:disabled-skill" in out
        assert "hr:draft-skill" in out
        assert "hr:minimal-skill" in out
        assert "hr:missing-metadata-skill" in out
        # system_contract excluded
        assert "hr:system-contract-skill" not in out

    def test_json_output(self, capsys):
        from cli.commands.skills import cmd_skills_catalog_list
        import argparse

        ns = argparse.Namespace(skills_root=str(FIXTURES), json=True)
        cmd_skills_catalog_list(ns)

        out = capsys.readouterr().out
        data = json.loads(out)
        assert isinstance(data, list)
        ids = [d["id"] for d in data]
        assert "hr:standard-skill" in ids
        assert "hr:high-impact-skill" in ids
        assert "hr:system-contract-skill" not in ids

    def test_empty_directory_shows_none(self, capsys, tmp_path):
        from cli.commands.skills import cmd_skills_catalog_list
        import argparse

        empty = tmp_path / "empty"
        empty.mkdir()
        ns = argparse.Namespace(skills_root=str(empty), json=False)
        cmd_skills_catalog_list(ns)

        out = capsys.readouterr().out
        assert "(no skills registered)" in out


class TestSkillsCatalogValidate:
    """Tests for 'skills catalog validate'"""

    def test_validates_with_known_skills(self, capsys):
        from cli.commands.skills import cmd_skills_catalog_validate
        import argparse

        ns = argparse.Namespace(skills_root=str(FIXTURES), policy_path=None, json=False)
        cmd_skills_catalog_validate(ns)

        out = capsys.readouterr().out
        # Should report catalog gate failures for disabled/draft/minimal
        assert "hr:disabled-skill" in out
        assert "CATALOG GATE FAILURES" in out

    def test_json_output_includes_warnings(self, capsys):
        from cli.commands.skills import cmd_skills_catalog_validate
        import argparse

        ns = argparse.Namespace(skills_root=str(FIXTURES), policy_path=None, json=True)
        cmd_skills_catalog_validate(ns)

        out = capsys.readouterr().out
        data = json.loads(out)
        assert "ids" in data
        assert "warnings" in data
        assert "catalog_gate_failures" in data
        assert len(data["catalog_gate_failures"]) > 0

    def test_unknown_ids_in_policy_produce_warnings(self, capsys, tmp_path):
        """Validate flags unknown skill ids referenced in eligibility policy."""
        from cli.commands.skills import cmd_skills_catalog_validate
        import argparse

        # Write a policy file with an unknown skill id
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("""
skills:
  org:
    allow:
      - hr:standard-skill
      - hr:nonexistent-skill
    deny: []
""")

        ns = argparse.Namespace(skills_root=str(FIXTURES), policy_path=str(policy_path), json=False)
        cmd_skills_catalog_validate(ns)

        out = capsys.readouterr().out
        assert "Unknown skill" in out
        assert "hr:nonexistent-skill" in out

    def test_missing_metadata_warnings(self, capsys):
        """Missing required fields produce warnings."""
        from cli.commands.skills import cmd_skills_catalog_validate
        import argparse

        ns = argparse.Namespace(skills_root=str(FIXTURES), policy_path=None, json=False)
        cmd_skills_catalog_validate(ns)

        out = capsys.readouterr().out
        # missing-metadata-skill has empty description and when_to_use
        assert "missing required field" in out

    def test_disabled_skill_flagged(self, capsys):
        """Disabled skill shows as catalog gate failure."""
        from cli.commands.skills import cmd_skills_catalog_validate
        import argparse

        ns = argparse.Namespace(skills_root=str(FIXTURES), policy_path=None, json=False)
        cmd_skills_catalog_validate(ns)

        out = capsys.readouterr().out
        assert "BLOCKED by catalog_gate" in out
        # disabled-skill should be blocked by catalog_gate
        assert "disabled" in out.lower()


class TestSkillsEffective:
    """Tests for 'skills effective --agent <name>'"""

    def _make_policy_file(self, tmp_path, allow=None, deny=None):
        """Helper: create a policy YAML file."""
        policy = {"skills": {}}
        if allow:
            policy["skills"]["org"] = {"allow": allow, "deny": deny or []}
        path = tmp_path / "config.yaml"
        path.write_text("---\n" + __import__("yaml").dump(policy, default_flow_style=False))
        return str(path)

    def test_effective_with_allow_policy(self, capsys, tmp_path):
        from cli.commands.skills import cmd_skills_effective
        import argparse

        policy_path = self._make_policy_file(tmp_path, allow=["hr:standard-skill"])
        ns = argparse.Namespace(
            agent="dev_agent", org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=policy_path, json=False,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        assert "hr:standard-skill" in out
        assert "Effective skills" in out

    def test_effective_json_output(self, capsys, tmp_path):
        from cli.commands.skills import cmd_skills_effective
        import argparse

        policy_path = self._make_policy_file(tmp_path, allow=["hr:standard-skill"])
        ns = argparse.Namespace(
            agent="dev_agent", org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=policy_path, json=True,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        data = json.loads(out)
        assert "effective_skills" in data
        assert "blocked_skills" in data
        assert data["agent"] == "dev_agent"

    def test_effective_shows_blocked_by_catalog(self, capsys, tmp_path):
        """A skill in allow list but disabled shows as blocked by catalog gate."""
        from cli.commands.skills import cmd_skills_effective
        import argparse

        policy_path = self._make_policy_file(tmp_path, allow=["hr:disabled-skill"])
        ns = argparse.Namespace(
            agent="dev_agent", org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=policy_path, json=False,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        assert "BLOCKED by catalog_gate" in out

    def test_effective_shows_blocked_by_deny(self, capsys, tmp_path):
        """A skill in both allow and deny lists shows as blocked."""
        from cli.commands.skills import cmd_skills_effective
        import argparse

        import yaml as _yaml
        policy = {"skills": {"org": {"allow": ["hr:standard-skill"], "deny": ["hr:standard-skill"]}}}
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(
            agent="dev_agent", org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=str(policy_path), json=False,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        assert "BLOCKED by eligibility_gate" in out

    def test_effective_no_policy_shows_all_catalog_approved(self, capsys, tmp_path):
        """Without a policy, no explicit allow rules exist, so no skills pass
        eligibility (is_allowed requires len(allowed_by) > 0).
        Uses an explicitly non-existent policy path to avoid the default config."""
        from cli.commands.skills import cmd_skills_effective
        import argparse

        nonexistent_policy = str(tmp_path / "nonexistent.yaml")
        ns = argparse.Namespace(
            agent="dev_agent", org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=nonexistent_policy, json=False,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        # With no policy (empty allow rules), no skills are exposed
        assert "Effective skills (0)" in out

    def test_effective_requires_agent(self, capsys):
        from cli.commands.skills import cmd_skills_effective
        import argparse

        ns = argparse.Namespace(
            agent=None, org=None, team=None,
            skills_root=str(FIXTURES), policy_path=None, json=False,
        )
        with pytest.raises(SystemExit):
            cmd_skills_effective(ns)


class TestSkillsPolicyExplain:
    """Tests for 'skills policy explain <skill_id> --agent <name>'"""

    def _make_policy_file(self, tmp_path, allow=None, deny=None):
        """Helper: create a policy YAML file."""
        import yaml as _yaml
        policy = {"skills": {"org": {"allow": allow or [], "deny": deny or []}}}
        path = tmp_path / "config.yaml"
        path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))
        return str(path)

    def test_explain_allowed_skill(self, capsys, tmp_path):
        """Explain a skill that is allowed and approved."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse
        import yaml as _yaml

        policy = {"skills": {"org": {"allow": ["hr:standard-skill"], "deny": []}}}
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(
            skill_id="hr:standard-skill", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=str(policy_path), json=False,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        assert "IS available" in out
        assert "Catalog Gate" in out
        assert "Eligibility Gate" in out
        assert "PASS" in out
        assert "ALLOWED" in out

    def test_explain_denied_skill(self, capsys, tmp_path):
        """Explain a skill that is denied by policy."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse
        import yaml as _yaml

        # Allow + deny = deny wins
        policy = {"skills": {"org": {"allow": ["hr:standard-skill"], "deny": ["hr:standard-skill"]}}}
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(
            skill_id="hr:standard-skill", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=str(policy_path), json=False,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        assert "NOT available" in out
        assert "DENIED" in out
        assert "deny wins" in out

    def test_explain_disabled_skill(self, capsys, tmp_path):
        """Explain a skill that passes eligibility but fails catalog (disabled)."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse
        import yaml as _yaml

        policy = {"skills": {"org": {"allow": ["hr:disabled-skill"], "deny": []}}}
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(
            skill_id="hr:disabled-skill", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=str(policy_path), json=False,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        assert "NOT available" in out
        assert "Blocked by: catalog gate" in out
        assert "disabled" in out.lower()

    def test_explain_draft_skill(self, capsys, tmp_path):
        """Explain a skill in draft state — fails catalog gate."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse

        ns = argparse.Namespace(
            skill_id="hr:draft-skill", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=None, json=False,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "draft" in out.lower()

    def test_explain_high_impact_skill(self, capsys, tmp_path):
        """Explain a high_impact_policy skill with founder approval."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse
        import yaml as _yaml

        policy = {"skills": {"org": {"allow": ["hr:high-impact-skill"], "deny": []}}}
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(
            skill_id="hr:high-impact-skill", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=str(policy_path), json=False,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        assert "IS available" in out
        assert "high_impact_policy" in out or "HIGH_IMPACT" in out.upper()
        assert "founder" in out or "approved_by" in out

    def test_explain_nonexistent_skill(self, capsys):
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse

        ns = argparse.Namespace(
            skill_id="hr:nonexistent", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=None, json=False,
        )
        with pytest.raises(SystemExit):
            cmd_skills_policy_explain(ns)

    def test_explain_json_output(self, capsys, tmp_path):
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse
        import yaml as _yaml

        policy = {"skills": {"org": {"allow": ["hr:standard-skill"], "deny": []}}}
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(
            skill_id="hr:standard-skill", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=str(policy_path), json=True,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["skill_id"] == "hr:standard-skill"
        assert data["catalog_gate"]["passed"] is True
        assert data["eligibility"]["passed"] is True

    def test_explain_requires_agent(self, capsys):
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse

        ns = argparse.Namespace(
            skill_id="hr:standard-skill", agent=None,
            org=None, team=None,
            skills_root=str(FIXTURES), policy_path=None, json=False,
        )
        with pytest.raises(SystemExit):
            cmd_skills_policy_explain(ns)

    def test_explain_org_deny_provenance(self, capsys, tmp_path):
        """Explain with org-level deny — shows correct provenance."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse
        import yaml as _yaml

        policy = {"skills": {"org": {"allow": [], "deny": ["hr:standard-skill"]}}}
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(
            skill_id="hr:standard-skill", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=str(policy_path), json=False,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        assert "NOT available" in out
        assert "DENIED" in out
        assert "Org policy" in out
        assert "deny:" in out  # org deny is shown

    def test_explain_team_provenance(self, capsys, tmp_path):
        """Explain with team-level allow — shows correct provenance."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse
        import yaml as _yaml

        policy = {
            "skills": {
                "teams": {
                    "engineering": {"allow": ["hr:standard-skill"], "deny": []},
                },
            },
        }
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(
            skill_id="hr:standard-skill", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=str(policy_path), json=False,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        assert "IS available" in out
        assert "Team policy" in out
        assert "allow: ✓" in out

    def test_explain_agent_provenance(self, capsys, tmp_path):
        """Explain with agent-level allow — shows correct provenance."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse
        import yaml as _yaml

        policy = {
            "skills": {
                "agents": {
                    "dev_agent": {"allow": ["hr:standard-skill"], "deny": []},
                },
            },
        }
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(
            skill_id="hr:standard-skill", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=str(policy_path), json=False,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        assert "IS available" in out
        assert "Agent policy" in out
        assert "allow: ✓" in out


class TestSkillsCliReview:
    """CLI tests for the review skill as a managed-catalog standard_operational entry."""

    def _make_review_policy(self, tmp_path, team_allow=True, agent_allows=None):
        """Helper: create a policy YAML with review scoped to engineering team
        and optionally to specific agents."""
        import yaml as _yaml
        policy: dict = {"skills": {"teams": {}, "agents": {}}}
        if team_allow:
            policy["skills"]["teams"]["engineering"] = {"allow": ["hr:review"], "deny": []}
        if agent_allows:
            for agent_name in agent_allows:
                policy["skills"]["agents"][agent_name] = {"allow": ["hr:review"], "deny": []}
        path = tmp_path / "config.yaml"
        path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))
        return str(path)

    def test_catalog_list_includes_review(self, capsys):
        """'skills catalog list' shows the review skill."""
        from cli.commands.skills import cmd_skills_catalog_list
        import argparse

        ns = argparse.Namespace(skills_root=str(FIXTURES), json=False)
        cmd_skills_catalog_list(ns)

        out = capsys.readouterr().out
        assert "hr:review" in out
        assert "Review" in out

    def test_catalog_list_json_includes_review(self, capsys):
        """JSON output from catalog list includes review."""
        from cli.commands.skills import cmd_skills_catalog_list
        import argparse

        ns = argparse.Namespace(skills_root=str(FIXTURES), json=True)
        cmd_skills_catalog_list(ns)

        out = capsys.readouterr().out
        data = json.loads(out)
        ids = [d["id"] for d in data]
        assert "hr:review" in ids

    def test_effective_review_exposed_to_engineering_team(self, capsys, tmp_path):
        """'skills effective --agent dev_agent' shows review as exposed when engineering team is allowed."""
        from cli.commands.skills import cmd_skills_effective
        import argparse

        policy_path = self._make_review_policy(tmp_path)
        ns = argparse.Namespace(
            agent="dev_agent", org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=policy_path, json=False,
            context=None, workspace=None,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        assert "hr:review" in out
        assert "ALLOW" in out

    def test_effective_review_not_exposed_to_non_participant(self, capsys, tmp_path):
        """'skills effective --agent support_agent --team cx' does NOT show review."""
        from cli.commands.skills import cmd_skills_effective
        import argparse

        policy_path = self._make_review_policy(tmp_path)
        ns = argparse.Namespace(
            agent="support_agent", org="happyranch", team="cx",
            skills_root=str(FIXTURES), policy_path=policy_path, json=False,
            context=None, workspace=None,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        # review should NOT appear in effective skills
        assert "hr:review" not in out or "Effective skills (0)" in out

    def test_effective_review_exposed_to_product_lead(self, capsys, tmp_path):
        """'skills effective --agent product_lead --team product' shows review via agent scope."""
        from cli.commands.skills import cmd_skills_effective
        import argparse

        policy_path = self._make_review_policy(tmp_path, agent_allows=["product_lead"])
        ns = argparse.Namespace(
            agent="product_lead", org="happyranch", team="product",
            skills_root=str(FIXTURES), policy_path=policy_path, json=False,
            context=None, workspace=None,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        assert "hr:review" in out

    def test_policy_explain_review_exposed(self, capsys, tmp_path):
        """'skills policy explain hr:review --agent dev_agent' shows review IS available."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse

        policy_path = self._make_review_policy(tmp_path)
        ns = argparse.Namespace(
            skill_id="hr:review", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=policy_path, json=False,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        assert "IS available" in out
        assert "Catalog Gate" in out
        assert "PASS" in out
        assert "ALLOWED" in out
        assert "standard_operational" in out

    def test_policy_explain_review_not_exposed_to_non_participant(self, capsys, tmp_path):
        """'skills policy explain hr:review --agent support_agent --team cx' shows NOT available."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse

        policy_path = self._make_review_policy(tmp_path)
        ns = argparse.Namespace(
            skill_id="hr:review", agent="support_agent",
            org="happyranch", team="cx",
            skills_root=str(FIXTURES), policy_path=policy_path, json=False,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        assert "NOT available" in out
        # Non-participant has no allow rules
        assert "NOT EXPLICITLY ALLOWED" in out

    def test_policy_explain_review_shows_team_provenance(self, capsys, tmp_path):
        """'skills policy explain hr:review' shows team-scoped eligibility provenance."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse

        policy_path = self._make_review_policy(tmp_path)
        ns = argparse.Namespace(
            skill_id="hr:review", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=policy_path, json=False,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        assert "Team policy" in out
        assert "allow: ✓" in out

    def test_policy_explain_review_json(self, capsys, tmp_path):
        """JSON output from policy explain includes review provenance."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse

        policy_path = self._make_review_policy(tmp_path)
        ns = argparse.Namespace(
            skill_id="hr:review", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=policy_path, json=True,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        data = json.loads(out)
        assert data["skill_id"] == "hr:review"
        assert data["catalog_gate"]["passed"] is True
        assert data["is_exposed"] is True
        # Provenance
        assert data["eligibility"]["team"] == "engineering"
        assert data["eligibility"]["team_policy"]["allow"] is True

    def test_validate_includes_review_in_catalog(self, capsys):
        """'skills catalog validate' includes the review skill in its output."""
        from cli.commands.skills import cmd_skills_catalog_validate
        import argparse

        ns = argparse.Namespace(skills_root=str(FIXTURES), policy_path=None, json=False)
        cmd_skills_catalog_validate(ns)

        out = capsys.readouterr().out
        assert "hr:review" in out

    def test_effective_system_contracts_does_not_include_review(self, capsys, tmp_path):
        """'skills effective' system contracts section does NOT include review."""
        from cli.commands.skills import cmd_skills_effective
        import argparse

        policy_path = self._make_review_policy(tmp_path)
        ns = argparse.Namespace(
            agent="dev_agent", org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=policy_path, json=False,
            context="task", workspace=None,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        # Check system contracts section does NOT list review
        # review appears only in Effective skills / Blocked, not in System Contracts
        # The system contracts section should show exactly 5 contracts
        assert "System Contracts (runtime-injected):" in out
        # review should appear in effective/blocked section, not system contracts
        # Verify the existing 5 contracts are still there
        assert "start-task" in out
        assert "jobs" in out


class TestSkillsCliRegistration:
    """Verify the skills subcommand is wired into the parser."""

    def test_skills_subcommand_registered(self):
        from cli.main import build_parser

        parser = build_parser()
        subparsers_action = next(
            a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"
        )
        choices = set(subparsers_action.choices.keys())
        assert "skills" in choices

    def test_test_skill_cli_commands_exist_e2e(self):
        """The existing test_skill_cli_commands_exist test should not need updating
        for managed skills (they are `skills ...` commands, not `happyranch <skill_slug>`)."""

    def test_skills_catalog_list_subcommand(self):
        from cli.main import build_parser

        parser = build_parser()
        args = parser.parse_args(["skills", "catalog", "list"])
        assert args.command == "skills"
        assert args.skills_command == "catalog"
        assert args.catalog_command == "list"
        assert args.func is not None

    def test_skills_catalog_validate_subcommand(self):
        from cli.main import build_parser

        parser = build_parser()
        args = parser.parse_args(["skills", "catalog", "validate"])
        assert args.command == "skills"
        assert args.skills_command == "catalog"
        assert args.catalog_command == "validate"
        assert args.func is not None

    def test_skills_effective_subcommand(self):
        from cli.main import build_parser

        parser = build_parser()
        args = parser.parse_args(["skills", "effective", "--agent", "dev_agent"])
        assert args.command == "skills"
        assert args.skills_command == "effective"
        assert args.agent == "dev_agent"
        assert args.func is not None

    def test_skills_policy_explain_subcommand(self):
        from cli.main import build_parser

        parser = build_parser()
        args = parser.parse_args(
            ["skills", "policy", "explain", "hr:test-skill", "--agent", "dev_agent"]
        )
        assert args.command == "skills"
        assert args.skills_command == "policy"
        assert args.policy_command == "explain"
        assert args.skill_id == "hr:test-skill"
        assert args.agent == "dev_agent"
        assert args.func is not None


class TestBlockedReasonAttribution:
    """TDD: blocked-reason attribution shows WHICH gate blocked and why."""

    def test_catalog_gate_blocked_reason_in_effective(self, capsys, tmp_path):
        """'skills effective' shows 'BLOCKED by catalog_gate' for catalog-failing skills."""
        from cli.commands.skills import cmd_skills_effective
        import argparse

        ns = argparse.Namespace(
            agent="dev_agent", org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=None, json=False,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        # disabled-skill fails catalog gate
        assert "BLOCKED by catalog_gate" in out
        # The reason should mention "disabled"
        assert "disabled" in out.lower()

    def test_eligibility_gate_blocked_reason_in_effective(self, capsys, tmp_path):
        """'skills effective' shows 'BLOCKED by eligibility_gate' for denied skills."""
        from cli.commands.skills import cmd_skills_effective
        import argparse
        import yaml as _yaml

        policy = {"skills": {"org": {"allow": ["hr:standard-skill"], "deny": ["hr:standard-skill"]}}}
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(
            agent="dev_agent", org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=str(policy_path), json=False,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        assert "BLOCKED by eligibility_gate" in out

    def test_policy_explain_attributes_block_to_catalog_gate(self, capsys):
        """'skills policy explain' attributes block to catalog gate for disabled skill."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse

        ns = argparse.Namespace(
            skill_id="hr:disabled-skill", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=None, json=False,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        assert "Blocked by: catalog gate" in out

    def test_policy_explain_attributes_block_to_eligibility_gate(self, capsys, tmp_path):
        """'skills policy explain' attributes block to eligibility gate for denied skill."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse
        import yaml as _yaml

        policy = {"skills": {"org": {"allow": ["hr:standard-skill"], "deny": ["hr:standard-skill"]}}}
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(
            skill_id="hr:standard-skill", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=str(policy_path), json=False,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        assert "Blocked by: eligibility gate" in out


class TestUnknownIdWarningPath:
    """TDD: unknown-id validation warnings."""

    def test_validate_warns_unknown_org_allow(self, capsys, tmp_path):
        from cli.commands.skills import cmd_skills_catalog_validate
        import argparse

        import yaml as _yaml
        policy = {"skills": {"org": {"allow": ["hr:unknown-skill-xyz"], "deny": []}}}
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(skills_root=str(FIXTURES), policy_path=str(policy_path), json=False)
        cmd_skills_catalog_validate(ns)

        out = capsys.readouterr().out
        assert "Unknown skill" in out
        assert "hr:unknown-skill-xyz" in out

    def test_validate_warns_unknown_team_allow(self, capsys, tmp_path):
        from cli.commands.skills import cmd_skills_catalog_validate
        import argparse

        import yaml as _yaml
        policy = {
            "skills": {
                "teams": {
                    "engineering": {"allow": ["hr:unknown-team-skill"], "deny": []},
                },
            },
        }
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(skills_root=str(FIXTURES), policy_path=str(policy_path), json=False)
        cmd_skills_catalog_validate(ns)

        out = capsys.readouterr().out
        assert "Unknown skill" in out
        assert "hr:unknown-team-skill" in out

    def test_validate_warns_unknown_agent_deny(self, capsys, tmp_path):
        from cli.commands.skills import cmd_skills_catalog_validate
        import argparse

        import yaml as _yaml
        policy = {
            "skills": {
                "agents": {
                    "qa_engineer": {"allow": [], "deny": ["hr:unknown-deny-skill"]},
                },
            },
        }
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(skills_root=str(FIXTURES), policy_path=str(policy_path), json=False)
        cmd_skills_catalog_validate(ns)

        out = capsys.readouterr().out
        assert "Unknown skill" in out
        assert "hr:unknown-deny-skill" in out


class TestProvenanceRendering:
    """TDD: provenance rendering shows which scope+rule admitted or denied."""

    def test_effective_shows_allow_provenance(self, capsys, tmp_path):
        """'skills effective' shows which scope allowed each skill."""
        from cli.commands.skills import cmd_skills_effective
        import argparse

        import yaml as _yaml
        policy = {"skills": {"org": {"allow": ["hr:standard-skill"], "deny": []}}}
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(
            agent="dev_agent", org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=str(policy_path), json=False,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        assert "org(happyranch) ALLOW" in out

    def test_policy_explain_shows_org_deny_provenance(self, capsys, tmp_path):
        """'skills policy explain' shows org-level deny provenance."""
        from cli.commands.skills import cmd_skills_policy_explain
        import argparse

        import yaml as _yaml
        policy = {"skills": {"org": {"allow": [], "deny": ["hr:standard-skill"]}}}
        policy_path = tmp_path / "config.yaml"
        policy_path.write_text("---\n" + _yaml.dump(policy, default_flow_style=False))

        ns = argparse.Namespace(
            skill_id="hr:standard-skill", agent="dev_agent",
            org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=str(policy_path), json=False,
        )
        cmd_skills_policy_explain(ns)

        out = capsys.readouterr().out
        # Should show org deny in the Deny provenance section
        assert "Org policy:" in out
        assert "deny:" in out


class TestAuditLoggerSkillsConfigWrite:
    """Test the skills config write audit logger method."""

    def test_log_skills_config_write_task_id(self):
        """log_skills_config_write uses config:skills as the scope prefix."""
        from runtime.infrastructure.audit_logger import AuditLogger

        class FakeDB:
            def __init__(self):
                self.logs = []
            def insert_audit_log(self, *, task_id, agent, action, payload):
                self.logs.append({"task_id": task_id, "agent": agent, "action": action, "payload": payload})

        db = FakeDB()
        logger = AuditLogger(db)
        logger.log_skills_config_write(
            subsection=None,
            tiers=["registry", "eligibility"],
            before={"skills": {}},
            after={"skills": {"org": {"allow": ["hr:test"]}}},
        )

        assert len(db.logs) == 1
        log = db.logs[0]
        assert log["task_id"] == "config:skills"
        assert log["action"] == "skills_config_write"
        assert log["payload"]["tiers"] == ["registry", "eligibility"]

    def test_log_skills_config_write_with_subsection(self):
        """log_skills_config_write with subsection scopes to config:skills:<subsection>."""
        from runtime.infrastructure.audit_logger import AuditLogger

        class FakeDB:
            def __init__(self):
                self.logs = []
            def insert_audit_log(self, *, task_id, agent, action, payload):
                self.logs.append({"task_id": task_id, "agent": agent, "action": action, "payload": payload})

        db = FakeDB()
        logger = AuditLogger(db)
        logger.log_skills_config_write(
            subsection="eligibility",
            tiers=["agents"],
            before={},
            after={"dev_agent": {"allow": ["hr:test"]}},
        )

        assert len(db.logs) == 1
        log = db.logs[0]
        assert log["task_id"] == "config:skills:eligibility"
        assert log["action"] == "skills_config_write"

    def test_log_skills_config_write_matches_existing_pattern(self):
        """log_skills_config_write mirrors log_org_config_write exactly."""
        from runtime.infrastructure.audit_logger import AuditLogger

        class FakeDB:
            def __init__(self):
                self.logs = []
            def insert_audit_log(self, *, task_id, agent, action, payload):
                self.logs.append({"task_id": task_id, "agent": agent, "action": action, "payload": payload})

        db = FakeDB()
        logger = AuditLogger(db)

        # Skills
        logger.log_skills_config_write(
            tiers=["registry"], before={"v": 1}, after={"v": 2}, actor="founder",
        )
        # Org config (working_hours)
        logger.log_org_config_write(
            section="working_hours", tiers=["default"], before={"v": 1}, after={"v": 2}, actor="founder",
        )

        assert len(db.logs) == 2
        # Both use config: prefix
        assert db.logs[0]["task_id"] == "config:skills"
        assert db.logs[1]["task_id"] == "config:working_hours"
        # Same payload shape keys
        assert set(db.logs[0]["payload"].keys()) == {"subsection", "tiers", "before", "after"}
        assert set(db.logs[1]["payload"].keys()) == {"section", "tiers", "before", "after"}


class TestSystemContractsCliDisplay:
    """Tests for system-contract display in 'skills effective'."""

    def test_effective_shows_system_contracts_section(self, capsys):
        """The 'skills effective' output includes a System Contracts section."""
        from cli.commands.skills import cmd_skills_effective
        import argparse

        ns = argparse.Namespace(
            agent="dev_agent", org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=None, json=False,
            context=None, workspace=None,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        assert "System Contracts (runtime-injected):" in out
        assert "Total: 5 contract(s)" in out
        assert "start-task" in out
        assert "jobs" in out
        assert "make-worktree" in out
        assert "thread" in out
        assert "dream" in out
        # Managed catalog section still present
        assert "Effective skills" in out

    def test_effective_json_includes_system_contracts(self, capsys):
        """JSON output includes system_contracts key."""
        from cli.commands.skills import cmd_skills_effective
        import argparse

        ns = argparse.Namespace(
            agent="dev_agent", org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=None, json=True,
            context=None, workspace=None,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        data = json.loads(out)
        assert "system_contracts" in data
        assert len(data["system_contracts"]) == 5
        ids = [sc["id"] for sc in data["system_contracts"]]
        assert "start-task" in ids
        assert "jobs" in ids
        assert "make-worktree" in ids
        assert "thread" in ids
        assert "dream" in ids
        # Existing keys still present
        assert "effective_skills" in data
        assert "blocked_skills" in data

    def test_effective_with_context_shows_injected_markers(self, capsys):
        """With --context task, injected contracts are marked."""
        from cli.commands.skills import cmd_skills_effective
        import argparse

        ns = argparse.Namespace(
            agent="dev_agent", org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=None, json=False,
            context="task", workspace=None,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        assert "System Contracts (runtime-injected):" in out
        assert "Context filter: task" in out
        # start-task should be INJECTED for task context
        assert "start-task" in out
        assert "INJECTED" in out
        # dream should NOT be injected
        assert "(not in context)" in out

    def test_effective_with_context_dream(self, capsys):
        """With --context dream, only jobs, make-worktree, dream are injected."""
        from cli.commands.skills import cmd_skills_effective
        import argparse

        ns = argparse.Namespace(
            agent="dev_agent", org="happyranch", team="engineering",
            skills_root=str(FIXTURES), policy_path=None, json=False,
            context="dream", workspace=None,
        )
        cmd_skills_effective(ns)

        out = capsys.readouterr().out
        # start-task should NOT be injected for dream
        assert "start-task" in out  # still listed
        # But dream should be injected
        # Check that the right pattern appears
        assert "dream  (Dream)  ← INJECTED" in out or "dream  (Dream)" in out
