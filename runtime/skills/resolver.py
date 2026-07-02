"""Eligibility resolver for the runtime-managed skill policy.

Implements additive inheritance with explicit deny (deny wins) per the
THR-055 product spec:

    effective = approved_catalog
      intersect (org.allow UNION team.allow UNION agent.allow)
      minus (org.deny UNION team.deny UNION agent.deny)

Preserves provenance per resolved skill: the catalog-approval record PLUS
the eligibility rule(s) that allowed or denied it.

Unknown skill ids in eligibility config → validation WARNING, excluded from results.
"""

from __future__ import annotations

from typing import Any

from runtime.skills.models import EligibilityRule, ResolvedSkill, SkillEntry


class EligibilityResolver:
    """Resolves org/team/agent eligibility for a catalog of skills.

    The policy dict shape (from YAML):
        {
            "org": {"allow": [...], "deny": [...]},
            "teams": {
                "team_name": {"allow": [...], "deny": [...]},
                ...
            },
            "agents": {
                "agent_name": {"allow": [...], "deny": [...]},
                ...
            },
        }
    """

    def __init__(self, policy: dict[str, Any]):
        self._policy = policy

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(
        self,
        catalog: list[SkillEntry],
        org: str,
        team: str,
        agent: str,
    ) -> list[ResolvedSkill]:
        """Resolve eligibility for an org/team/agent tuple against the catalog.

        Returns only skills that pass the eligibility formula (allow - deny).
        Unknown skill ids are excluded with a warning.
        """
        catalog_by_id = {e.id: e for e in catalog}

        # Collect all eligibility rules
        all_allows: list[EligibilityRule] = []
        all_denies: list[EligibilityRule] = []

        # Org scope
        org_policy = self._policy.get("org", {})
        all_allows.extend(self._rules_for_scope(org_policy, "org", org, "allow"))
        all_denies.extend(self._rules_for_scope(org_policy, "org", org, "deny"))

        # Team scope
        teams_policy = self._policy.get("teams", {})
        team_policy = teams_policy.get(team, {})
        all_allows.extend(self._rules_for_scope(team_policy, "team", team, "allow"))
        all_denies.extend(self._rules_for_scope(team_policy, "team", team, "deny"))

        # Agent scope
        agents_policy = self._policy.get("agents", {})
        agent_policy = agents_policy.get(agent, {})
        all_allows.extend(self._rules_for_scope(agent_policy, "agent", agent, "allow"))
        all_denies.extend(self._rules_for_scope(agent_policy, "agent", agent, "deny"))

        # Build set of allowed and denied skill ids (deny wins)
        allowed_ids: set[str] = {r.skill_id for r in all_allows}
        denied_ids: set[str] = {r.skill_id for r in all_denies}

        # Effective: allowed minus denied
        effective_ids = allowed_ids - denied_ids

        # Build results with provenance
        results: list[ResolvedSkill] = []
        for skill_id in sorted(effective_ids):
            entry = catalog_by_id.get(skill_id)
            if entry is None:
                continue  # unknown — excluded with warning (handled in validate)
            allowed_rules = [r for r in all_allows if r.skill_id == skill_id]
            denied_rules = [r for r in all_denies if r.skill_id == skill_id]
            results.append(ResolvedSkill(
                skill=entry,
                allowed_by=allowed_rules,
                denied_by=denied_rules,
            ))

        return results

    def get_blocked(
        self,
        catalog: list[SkillEntry],
        org: str,
        team: str,
        agent: str,
    ) -> dict[str, list[EligibilityRule]]:
        """Return a map of skill_id -> deny rules for diagnostic purposes.

        Only skills denied by at least one rule are included.
        """
        _, all_denies = self._collect_rules(catalog, org, team, agent)
        blocked: dict[str, list[EligibilityRule]] = {}
        for rule in all_denies:
            blocked.setdefault(rule.skill_id, []).append(rule)
        return blocked

    def validate(self, catalog: list[SkillEntry]) -> list[str]:
        """Validate the eligibility policy against the catalog.

        Returns a list of warning strings for:
        - Unknown skill ids referenced in allow/deny rules
        """
        warnings: list[str] = []
        catalog_ids = {e.id for e in catalog}

        def check_scope(policy: dict, scope_name: str, scope_id: str):
            for action in ("allow", "deny"):
                for skill_id in policy.get(action, []):
                    if skill_id not in catalog_ids:
                        warnings.append(
                            f"Unknown skill '{skill_id}' in {scope_name} "
                            f"({scope_id}) {action} list"
                        )

        org_policy = self._policy.get("org", {})
        check_scope(org_policy, "org", "org")

        teams_policy = self._policy.get("teams", {})
        for team_name, team_policy in teams_policy.items():
            check_scope(team_policy, f"team '{team_name}'", team_name)

        agents_policy = self._policy.get("agents", {})
        for agent_name, agent_policy in agents_policy.items():
            check_scope(agent_policy, f"agent '{agent_name}'", agent_name)

        return warnings

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rules_for_scope(
        self,
        policy: dict,
        scope: str,
        scope_id: str,
        action: str,
    ) -> list[EligibilityRule]:
        """Extract EligibilityRules from a single scope's allow/deny list."""
        skill_ids: list[str] = policy.get(action, [])
        return [
            EligibilityRule(scope=scope, id=scope_id, skill_id=sid, action=action)
            for sid in skill_ids
        ]

    def _collect_rules(
        self,
        catalog: list[SkillEntry],
        org: str,
        team: str,
        agent: str,
    ) -> tuple[list[EligibilityRule], list[EligibilityRule]]:
        """Internal: collect all allow and deny rules for a given resolution context."""
        all_allows: list[EligibilityRule] = []
        all_denies: list[EligibilityRule] = []

        org_policy = self._policy.get("org", {})
        all_allows.extend(self._rules_for_scope(org_policy, "org", org, "allow"))
        all_denies.extend(self._rules_for_scope(org_policy, "org", org, "deny"))

        teams_policy = self._policy.get("teams", {})
        team_policy = teams_policy.get(team, {})
        all_allows.extend(self._rules_for_scope(team_policy, "team", team, "allow"))
        all_denies.extend(self._rules_for_scope(team_policy, "team", team, "deny"))

        agents_policy = self._policy.get("agents", {})
        agent_policy = agents_policy.get(agent, {})
        all_allows.extend(self._rules_for_scope(agent_policy, "agent", agent, "allow"))
        all_denies.extend(self._rules_for_scope(agent_policy, "agent", agent, "deny"))

        return all_allows, all_denies
