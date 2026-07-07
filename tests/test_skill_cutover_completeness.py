"""Contract-completeness guard: every agent × session-context receives its
COMPLETE required system-contract + managed-catalog skill set WITHOUT the
wholesale protocol/skills dump.

This test is the GATE for THR-055 Phase 4 (the cutover). It must fail red
before the managed-skill injection path exists and pass green after.

Coverage:
  - 7 agents (dev_agent, code_reviewer, qa_engineer, frontend_engineer,
    engineering_manager, product_lead, consultant_head)
  - 4 session contexts (task, thread, wake, dream)
  - 2 repo states (with repos, without repos)
  - System contracts: start-task, jobs, make-worktree, thread, dream
  - Managed catalog: review (standard_operational), manage-agent + manage-repo
    (high_impact_policy, pending_review → FAIL CLOSED)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import Settings

# ── Agent roster as defined in the org ───────────────────────────────

AGENT_ROSTER: dict[str, str] = {
    "dev_agent": "engineering",
    "code_reviewer": "engineering",
    "qa_engineer": "engineering",
    "frontend_engineer": "engineering",
    "engineering_manager": "engineering",
    "product_lead": "product",
    "consultant_head": "consultant",
}

# Engineering team (workers + manager) + product_lead get review
# (product_lead has agent-scoped allow per the Phase 2 eligibility policy)
REVIEW_ELIGIBLE_AGENTS = {
    "dev_agent", "code_reviewer", "qa_engineer",
    "frontend_engineer", "engineering_manager",
    "product_lead",
}

# Manager-eligible agents (for manage-agent / manage-repo — but these
# are fail-closed as pending_review, so they should NOT appear)
MANAGER_AGENTS = {"engineering_manager", "product_lead"}

# ── Expected system contracts per (context, has_repos) ───────────────

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

# ── Helpers ──────────────────────────────────────────────────────────


def _setup_skills_fixtures(settings: Settings, tmp_path: Path) -> Path:
    """Build the protocol/skills/ tree, runtime/skills/ managed catalog,
    and org/config.yaml eligibility policy.

    Creates all 8 skill directories with SKILL.md files so both
    inject_system_contracts and the future inject_managed_skills can
    find their source content.

    Returns the managed skills_root path.
    """
    # ── org/config.yaml (eligibility policy) ─────────────────────────
    org_dir = settings.project_root / "org"
    org_dir.mkdir(parents=True, exist_ok=True)
    (org_dir / "config.yaml").write_text(
        "skills:\n"
        "  org:\n"
        "    allow: []\n"
        "    deny: []\n"
        "  teams:\n"
        "    engineering:\n"
        "      allow:\n"
        "        - hr:review\n"
        "      deny: []\n"
        "    product:\n"
        "      allow: []\n"
        "      deny: []\n"
        "    consultant:\n"
        "      allow: []\n"
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

    # ── protocol/skills/ (system contracts + legacy safety net) ──────
    proto_skills = settings.get_protocol_dir() / "skills"
    for name in (
        "start-task", "jobs", "make-worktree", "thread", "dream",
        "review", "manage-agent", "manage-repo",
    ):
        (proto_skills / name).mkdir(parents=True)
        (proto_skills / name / "SKILL.md").write_text(f"# {name}\n")

    # ── runtime/skills/ (managed catalog) ────────────────────────────
    # Use a temp dir that will be passed as the skills_root.
    managed_root = tmp_path / "runtime_skills"
    managed_root.mkdir(parents=True)

    # review: standard_operational, approved, engineering-scoped
    (managed_root / "review").mkdir(parents=True)
    (managed_root / "review" / "SKILL.md").write_text("# review\n")
    (managed_root / "review" / "skill.yaml").write_text(
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
        "status: enabled\n"
    )

    # manage-agent: high_impact_policy, pending_review (FAIL CLOSED)
    (managed_root / "manage-agent").mkdir(parents=True)
    (managed_root / "manage-agent" / "SKILL.md").write_text("# manage-agent\n")
    (managed_root / "manage-agent" / "skill.yaml").write_text(
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
        "status: enabled\n"
    )

    # manage-repo: high_impact_policy, pending_review (FAIL CLOSED)
    (managed_root / "manage-repo").mkdir(parents=True)
    (managed_root / "manage-repo" / "SKILL.md").write_text("# manage-repo\n")
    (managed_root / "manage-repo" / "skill.yaml").write_text(
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
        "status: enabled\n"
    )

    return managed_root


def _build_ws(tmp_path: Path, name: str, *, has_repos: bool) -> Path:
    """Create a workspace directory, optionally with repos/."""
    ws = tmp_path / name
    ws.mkdir(parents=True)
    if has_repos:
        (ws / "repos" / "happyranch" / ".git").mkdir(parents=True)
    return ws


def _collect_skill_ids(skills_dir: Path) -> set[str]:
    """List skill subdirectory names from .claude/skills/ (or any dir)."""
    if not skills_dir.is_dir():
        return set()
    return {
        child.name
        for child in skills_dir.iterdir()
        if child.is_dir() and (child / "SKILL.md").exists()
    }


# ── The contract-completeness test ───────────────────────────────────


class TestContractCompletenessPostCutover:
    """Prove EVERY agent × session-context receives its complete required
    skill set via explicit injection ONLY (NO wholesale dump)."""

    def test_all_agents_all_contexts_all_repo_states(
        self, test_settings: Settings, tmp_path: Path,
    ):
        """The gate: iterate every (agent, context, repo_state) and assert
        the complete required set is injected without refresh_session_skills."""
        from runtime.orchestrator.workspace_adapters import (
            inject_system_contracts,
            inject_managed_skills,  # WILL NOT EXIST YET — red
        )
        from runtime.skills.system_contracts import (
            resolve_system_contracts_for_session,
            SessionContext,
        )

        managed_root = _setup_skills_fixtures(test_settings, tmp_path)

        # Temporarily override _SKILLS_SRC for the managed injection
        import runtime.orchestrator.workspace_adapters as wa

        failures: list[str] = []

        for agent_name in sorted(AGENT_ROSTER):
            team_name = AGENT_ROSTER[agent_name]

            for context_str in ("task", "thread", "wake", "dream"):
                ctx = SessionContext(context_str)

                for has_repos in (True, False):
                    # Unique workspace per variant
                    ws_name = f"ws_{agent_name}_{context_str}_repos{has_repos}"
                    ws = _build_ws(tmp_path, ws_name, has_repos=has_repos)

                    # ── Inject system contracts ──────────────────────
                    inject_system_contracts(
                        ws, test_settings, slug="test", context=context_str,
                    )

                    # ── Inject managed-catalog skills ─────────────────
                    # This call is the NEW injection path that will be added
                    # in Phase 4. It does not exist yet → red test.
                    inject_managed_skills(
                        ws, test_settings,
                        slug="test",
                        agent_name=agent_name,
                        team=team_name,
                        skills_root=managed_root,
                    )

                    # ── Collect what was injected ────────────────────
                    sys_contracts = resolve_system_contracts_for_session(
                        ctx, workspace=ws,
                    )
                    injected = _collect_skill_ids(ws / ".claude" / "skills")
                    agents_injected = _collect_skill_ids(ws / ".agents" / "skills")

                    # ── Verify system contracts ──────────────────────
                    expected_sys = SYSTEM_CONTRACT_EXPECTATIONS[context_str][has_repos]
                    sys_ids = {sc.id for sc in sys_contracts}
                    assert sys_ids == expected_sys, (
                        f"resolve_system_contracts mismatch for "
                        f"({agent_name}, {context_str}, repos={has_repos}): "
                        f"got {sys_ids}, expected {expected_sys}"
                    )

                    for sc_id in expected_sys:
                        if sc_id not in injected:
                            failures.append(
                                f"MISSING system contract '{sc_id}' for "
                                f"({agent_name}, {context_str}, repos={has_repos})"
                            )
                        if sc_id not in agents_injected:
                            failures.append(
                                f"MISSING system contract '{sc_id}' in .agents/ for "
                                f"({agent_name}, {context_str}, repos={has_repos})"
                            )

                    # ── Verify managed-catalog skills ────────────────
                    if agent_name in REVIEW_ELIGIBLE_AGENTS:
                        # review should be injected
                        if "review" not in injected:
                            failures.append(
                                f"MISSING managed skill 'review' for "
                                f"({agent_name}, {context_str}, repos={has_repos})"
                            )
                    else:
                        # review should NOT be injected
                        if "review" in injected:
                            failures.append(
                                f"UNEXPECTED managed skill 'review' for "
                                f"({agent_name}, {context_str}, repos={has_repos})"
                            )

                    # manage-agent / manage-repo: fail-closed for ALL agents
                    # (pending_review — catalog gate blocked)
                    for hi_skill in ("manage-agent", "manage-repo"):
                        if hi_skill in injected:
                            failures.append(
                                f"UNEXPECTED high-impact skill '{hi_skill}' for "
                                f"({agent_name}, {context_str}, repos={has_repos}) "
                                f"— should be fail-closed (pending_review)"
                            )

                    # ── Verify dream/bloat exclusion ─────────────────
                    if context_str != "dream" and "dream" in injected:
                        failures.append(
                            f"UNEXPECTED 'dream' for ({agent_name}, {context_str}, "
                            f"repos={has_repos}) — dream is DREAM-only"
                        )

        # ── Report ───────────────────────────────────────────────────
        if failures:
            pytest.fail(
                f"{len(failures)} contract-completeness failure(s):\n"
                + "\n".join(f"  - {f}" for f in failures)
            )
