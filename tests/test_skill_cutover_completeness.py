"""Contract-completeness guard: every agent × session-context receives its
COMPLETE required system-contract + managed-catalog skill set WITHOUT the
wholesale protocol/skills dump.

This test is the GATE for THR-055 Phase 4 (the cutover). It must fail red
when the bootstrap _copy_skills still leaks the wholesale dump; it must pass
green when the gate on _WHOLESALE_DUMP_ENABLED stops both bootstrap and
session-time wholesale copy.

Coverage:
  - Derive agent roster from REAL org agents directory (prompt_loader.list_agents)
  - Use REAL eligibility policy (org/config.yaml)
  - Exercise ensure_workspace_ready() (bootstrap) + inject_system_contracts +
    inject_managed_skills — the same code paths the 4 session callers use
  - Assert EXACT final .claude/skills and .agents/skills contents:
    * System contracts are context-correct
    * review is present ONLY for eligible agents
    * manage-agent/manage-repo are NEVER present (fail-closed)
  - TDD: with _WHOLESALE_DUMP_ENABLED = False, bootstrap + explicit injection
    must produce the correct result; a wholesale-dump bypass would be caught
    by unexpected manage-agent/manage-repo in the final dirs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.orchestrator._paths import OrgPaths


# ── Agent roster: derived from the org agents directory by prompt_loader, ─
# not hardcoded. We list them here for the expected set-construction but the
# actual roster comes from the real list_agents() call.

ALL_AGENTS = [
    "dev_agent",
    "code_reviewer",
    "qa_engineer",
    "frontend_engineer",
    "engineering_manager",
    "product_lead",
    "consultant_head",
]

# Team mapping for each agent — matches the real org.
AGENT_TEAM: dict[str, str] = {
    "dev_agent": "engineering",
    "code_reviewer": "engineering",
    "qa_engineer": "engineering",
    "frontend_engineer": "engineering",
    "engineering_manager": "engineering",
    "product_lead": "product",
    "consultant_head": "consultant",
}

# Review-eligible agents per the real eligibility policy
REVIEW_ELIGIBLE = {
    "dev_agent", "code_reviewer", "qa_engineer",
    "frontend_engineer", "engineering_manager",
    "product_lead",
}

# ── Expected system contracts per (context, has_repos) ─────────────────

SYSTEM_CONTRACT_EXPECTATIONS: dict[str, dict[bool, set[str]]] = {
    "task": {
        True:  {"start-task", "jobs", "make-worktree", "thread"},
        False: {"start-task", "jobs", "thread"},
    },
    "thread": {
        True:  {"jobs", "make-worktree", "thread"},
        False: {"jobs", "thread"},
    },
    "wake": {
        True:  {"start-task", "jobs", "make-worktree", "thread"},
        False: {"start-task", "jobs", "thread"},
    },
    "dream": {
        True:  {"jobs", "make-worktree", "dream"},
        False: {"jobs", "dream"},
    },
}


# ── Fixture builders ────────────────────────────────────────────────────


def _write_agent_file(paths: OrgPaths, name: str, team: str, role: str = "worker") -> None:
    """Write a minimal agent .md file so prompt_loader.list_agents() picks it up."""
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text

    agent = AgentDef(
        name=name,
        team=team,
        role=role,  # type: ignore[arg-type]
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by=None,
        enrolled_at_task=None,
        enrolled_at=None,
        system_prompt=f"You are {name}.\n",
    )
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / f"{name}.md").write_text(render_agent_text(agent))


def _write_teams_config(paths: OrgPaths) -> None:
    """Write org/teams.yaml so TeamsRegistry.load() works."""
    import yaml

    payload = {
        "teams": {
            "engineering": {
                "manager": "engineering_manager",
                "workers": [
                    "dev_agent", "code_reviewer", "qa_engineer", "frontend_engineer",
                ],
            },
            "product": {
                "manager": "product_lead",
                "workers": [],
            },
            "consultant": {
                "manager": "consultant_head",
                "workers": [],
            },
        },
    }
    paths.org_dir.mkdir(parents=True, exist_ok=True)
    (paths.teams_config_path).write_text(yaml.safe_dump(payload, sort_keys=False))


def _write_eligibility_config(settings: Settings, paths: OrgPaths) -> None:
    """Write the REAL eligibility policy to both Locations:
    - ``settings.project_root / org / config.yaml`` (read by inject_managed_skills)
    - ``paths.org_dir / config.yaml`` (read by TeamsRegistry and prompt_loader)
    """
    config_text = (
        "# ── Skill eligibility policy ────────────────────────────────────────\n"
        "# Additive inheritance: effective = (org ∪ team ∪ agent) minus denies.\n"
        "# Deny wins over allow. Unknown skill ids produce validation warnings.\n"
        "\n"
        "skills:\n"
        "  org:\n"
        "    allow: []\n"
        "    deny: []\n"
        "  teams:\n"
        "    engineering:\n"
        "      allow:\n"
        "        - hr:review\n"
        "      deny: []\n"
        "  agents:\n"
        "    product_lead:\n"
        "      allow:\n"
        "        - hr:review\n"
        "        - hr:manage-agent\n"
        "        - hr:manage-repo\n"
        "      deny: []\n"
        "    engineering_manager:\n"
        "      allow:\n"
        "        - hr:manage-agent\n"
        "        - hr:manage-repo\n"
        "      deny: []\n"
    )
    for parent in (settings.project_root / "org", paths.org_dir):
        parent.mkdir(parents=True, exist_ok=True)
        (parent / "config.yaml").write_text(config_text)


def _create_protocol_skills(settings: Settings) -> None:
    """Create the 8 protocol/skills/ directories with minimal SKILL.md bodies."""
    proto_skills = settings.get_protocol_dir() / "skills"
    for name in (
        "start-task", "jobs", "make-worktree", "thread", "dream",
        "review", "manage-agent", "manage-repo",
    ):
        (proto_skills / name).mkdir(parents=True)
        (proto_skills / name / "SKILL.md").write_text(f"# {name}\n")


def _create_managed_catalog(project_root: Path) -> Path:
    """Create runtime/skills/ managed catalog with proper skill.yaml entries.

    Returns the skills_root path that inject_managed_skills expects.
    """
    skills_root = project_root / "runtime" / "skills"
    skills_root.mkdir(parents=True)

    # review: standard_operational, approved
    (skills_root / "review").mkdir(parents=True)
    (skills_root / "review" / "SKILL.md").write_text("# review\n")
    (skills_root / "review" / "skill.yaml").write_text(
        "id: hr:review\n"
        "slug: review\n"
        "name: Review\n"
        "version: 1.0.0\n"
        "description: Operational self-review.\n"
        "when_to_use: Use when asked to review your work.\n"
        "owner: engineering_manager\n"
        "source: runtime/skills/review\n"
        "policy_class: standard_operational\n"
        "approval_state: approved\n"
        "approved_by: engineering_manager\n"
        "approved_at: 2026-07-07T00:00:00Z\n"
        "status: enabled\n",
    )

    # manage-agent: high_impact_policy, pending_review → FAIL CLOSED
    (skills_root / "manage-agent").mkdir(parents=True)
    (skills_root / "manage-agent" / "SKILL.md").write_text("# manage-agent\n")
    (skills_root / "manage-agent" / "skill.yaml").write_text(
        "id: hr:manage-agent\n"
        "slug: manage-agent\n"
        "name: Manage Agent\n"
        "version: 1.0.0\n"
        "description: Agent roster governance.\n"
        "when_to_use: Use when managing the agent roster.\n"
        "owner: engineering_manager\n"
        "source: runtime/skills/manage-agent\n"
        "policy_class: high_impact_policy\n"
        "approval_state: pending_review\n"
        "status: enabled\n",
    )

    # manage-repo: high_impact_policy, pending_review → FAIL CLOSED
    (skills_root / "manage-repo").mkdir(parents=True)
    (skills_root / "manage-repo" / "SKILL.md").write_text("# manage-repo\n")
    (skills_root / "manage-repo" / "skill.yaml").write_text(
        "id: hr:manage-repo\n"
        "slug: manage-repo\n"
        "name: Manage Repo\n"
        "version: 1.0.0\n"
        "description: Repository configuration.\n"
        "when_to_use: Use when managing repos.\n"
        "owner: engineering_manager\n"
        "source: runtime/skills/manage-repo\n"
        "policy_class: high_impact_policy\n"
        "approval_state: pending_review\n"
        "status: enabled\n",
    )

    return skills_root


def _build_ws(tmp_path: Path, name: str, *, has_repos: bool) -> Path:
    """Create a workspace directory, optionally with repos/ marker."""
    ws = tmp_path / name
    ws.mkdir(parents=True)
    if has_repos:
        (ws / "repos" / "happyranch" / ".git").mkdir(parents=True)
    return ws


def _collect_skill_ids(skills_dir: Path) -> set[str]:
    """List skill subdirectory names from a skills dir (e.g. .claude/skills/)."""
    if not skills_dir.is_dir():
        return set()
    return {
        child.name
        for child in skills_dir.iterdir()
        if child.is_dir() and (child / "SKILL.md").exists()
    }


# ── The contract-completeness gate test ─────────────────────────────────


class TestContractCompletenessPostCutover:
    """Prove EVERY agent × session-context receives its complete required
    skill set via explicit injection ONLY (NO wholesale dump from bootstrap
    or session-time refresh)."""

    def test_all_agents_all_contexts_all_repo_states(
        self, test_settings: Settings, tmp_path: Path, test_runtime: OrgPaths,
    ):
        """The HARD PRECONDITION gate.

        Iterates every (agent, context, repo_state) combination, bootstraps
        a fresh workspace, injects system contracts + managed skills, and
        asserts the EXACT final .claude/skills and .agents/skills contents.

        With _WHOLESALE_DUMP_ENABLED = False:
        - Bootstrap must NOT leak any skills into the workspace
        - Only explicit injection delivers skills
        - manage-agent/manage-repo must NEVER appear (fail-closed)
        - review must appear ONLY for eligible agents
        """
        import runtime.orchestrator.workspace_adapters as wa
        from runtime.orchestrator.context_builder import ContextBuilder
        from runtime.orchestrator.workspace_adapters import (
            inject_system_contracts,
            inject_managed_skills,
        )
        from runtime.skills.system_contracts import (
            SessionContext,
            resolve_system_contracts_for_session,
        )

        # ── Confirm the flag is OFF ──────────────────────────────────
        assert wa._WHOLESALE_DUMP_ENABLED is False, (
            "_WHOLESALE_DUMP_ENABLED must be OFF for the cutover gate test"
        )

        # ── Set up org configuration ─────────────────────────────────
        for name in ALL_AGENTS:
            role = "manager" if name in ("engineering_manager", "product_lead", "consultant_head") else "worker"
            _write_agent_file(test_runtime, name, AGENT_TEAM[name], role=role)
        _write_teams_config(test_runtime)
        _write_eligibility_config(test_settings, test_runtime)

        # ── Set up skill sources ─────────────────────────────────────
        _create_protocol_skills(test_settings)
        managed_root = _create_managed_catalog(test_settings.project_root)

        # Verify the roster is correctly loaded
        from runtime.orchestrator.prompt_loader import list_agents
        agent_names = {a.name for a in list_agents(test_runtime)}
        expected_names = set(ALL_AGENTS)
        assert agent_names == expected_names, (
            f"Agent roster mismatch: got {agent_names}, expected {expected_names}"
        )

        # ── Iterate every (agent, context, repo_state) ───────────────
        failures: list[str] = []

        for agent_name in sorted(ALL_AGENTS):
            team_name = AGENT_TEAM[agent_name]

            for context_str in ("task", "thread", "wake", "dream"):
                ctx = SessionContext(context_str)

                for has_repos in (True, False):
                    ws_name = f"ws_{agent_name}_{context_str}_repos{has_repos}"
                    ws = _build_ws(tmp_path, ws_name, has_repos=has_repos)

                    # ── Step 1: Bootstrap (must NOT leak skills) ────
                    builder = ContextBuilder(
                        test_settings, test_runtime, slug="test",
                    )
                    builder.ensure_workspace_ready(
                        ws, agent_name, "system prompt",
                    )

                    # After bootstrap, skills dirs should be EMPTY
                    # (bootstrap _copy_skills is gated behind the flag)
                    claude_after_bootstrap = _collect_skill_ids(
                        ws / ".claude" / "skills",
                    )
                    agents_after_bootstrap = _collect_skill_ids(
                        ws / ".agents" / "skills",
                    )
                    if claude_after_bootstrap:
                        failures.append(
                            f"BOOTSTRAP LEAK: .claude/skills/ has "
                            f"{claude_after_bootstrap} after bootstrap for "
                            f"({agent_name}, {context_str}, repos={has_repos})"
                        )
                    if agents_after_bootstrap:
                        failures.append(
                            f"BOOTSTRAP LEAK: .agents/skills/ has "
                            f"{agents_after_bootstrap} after bootstrap for "
                            f"({agent_name}, {context_str}, repos={has_repos})"
                        )

                    # ── Step 2: Inject system contracts ──────────────
                    inject_system_contracts(
                        ws, test_settings, slug="test", context=context_str,
                    )

                    # ── Step 3: Inject managed-catalog skills ────────
                    inject_managed_skills(
                        ws, test_settings,
                        slug="test",
                        agent_name=agent_name,
                        team=team_name,
                        skills_root=managed_root,
                    )

                    # ── Step 4: Collect final state ──────────────────
                    injected = _collect_skill_ids(ws / ".claude" / "skills")
                    agents_injected = _collect_skill_ids(ws / ".agents" / "skills")

                    # Both dirs must match
                    if injected != agents_injected:
                        failures.append(
                            f"SKILL DIR MISMATCH for "
                            f"({agent_name}, {context_str}, repos={has_repos}): "
                            f".claude={injected}, .agents={agents_injected}"
                        )

                    # ── Verify system contracts ──────────────────────
                    expected_sys = SYSTEM_CONTRACT_EXPECTATIONS[context_str][has_repos]

                    for sc_id in expected_sys:
                        if sc_id not in injected:
                            failures.append(
                                f"MISSING system contract '{sc_id}' for "
                                f"({agent_name}, {context_str}, repos={has_repos})"
                            )

                    # ── Verify managed-catalog skills ────────────────
                    if agent_name in REVIEW_ELIGIBLE:
                        if "review" not in injected:
                            failures.append(
                                f"MISSING managed skill 'review' for "
                                f"({agent_name}, {context_str}, repos={has_repos})"
                            )
                    else:
                        if "review" in injected:
                            failures.append(
                                f"UNEXPECTED managed skill 'review' for "
                                f"({agent_name}, {context_str}, repos={has_repos})"
                            )

                    # manage-agent / manage-repo: fail-closed for ALL
                    for hi_skill in ("manage-agent", "manage-repo"):
                        if hi_skill in injected:
                            failures.append(
                                f"UNEXPECTED high-impact skill '{hi_skill}' for "
                                f"({agent_name}, {context_str}, repos={has_repos}) "
                                f"— should be fail-closed (pending_review)"
                            )

                    # ── Verify no bloat / no extra skills ────────────
                    # Assemble the expected complete set
                    expected_full = set(expected_sys)
                    if agent_name in REVIEW_ELIGIBLE:
                        expected_full.add("review")
                    unexpected = injected - expected_full
                    if unexpected:
                        failures.append(
                            f"UNEXPECTED skills {unexpected} for "
                            f"({agent_name}, {context_str}, repos={has_repos})"
                        )

        # ── Report ───────────────────────────────────────────────────
        if failures:
            pytest.fail(
                f"{len(failures)} contract-completeness failure(s):\n"
                + "\n".join(f"  - {f}" for f in failures)
            )
