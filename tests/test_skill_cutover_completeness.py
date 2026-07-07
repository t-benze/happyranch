"""Contract-completeness guard: every agent × session-context receives its
COMPLETE required system-contract + managed-catalog skill set WITHOUT the
wholesale protocol/skills dump.

This test is the GATE for THR-055 Phase 4 (the cutover). It must fail red
when the bootstrap _copy_skills still leaks the wholesale dump; it must pass
green when the gate on _WHOLESALE_DUMP_ENABLED stops both bootstrap and
session-time wholesale copy.

REAL-SOURCE GUARD: This test reads the REAL in-repo artifacts —
  - ``org/config.yaml`` (eligibility policy, shipped in Phase 2-3)
  - ``runtime/skills/`` (managed catalog with real approval states)
  - ``protocol/skills/`` (injection + bootstrap source skill bodies)
If the shipped policy, catalog, or source dirs drift (e.g. manage-agent gets
flipped to approved, review's policy_class changes, or a catalog entry
regresses), this guard MUST fail — it is a fail-closed integrity check.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.orchestrator._paths import OrgPaths


# ── Resolve the REAL in-repo root from this test file's location. ───────
# This is what makes the guard "real": the policy, catalog, and source
# dirs are read from the actual repository, not fabricated.
_REPO_ROOT = Path(__file__).resolve().parents[1]


# ── Representative roster ─────────────────────────────────────────────────
#
# The LIVE happyranch org roster (org/agents/, org/teams.yaml) is RUNTIME-ONLY
# state — NOT in-repo — so it cannot be enumerated hermetically in CI.
#
# This EXPLICIT, DOCUMENTED representative roster replaces the prior fabricated
# list. It spans every eligibility class against the REAL in-repo
# org/config.yaml and every executor adapter:
#
# Eligibility classes (from the real org/config.yaml shipped in Phase 2-3):
#   A. review-eligible via team membership (engineering → hr:review)
#      — dev_agent, code_reviewer, qa_engineer, frontend_engineer
#   B. review-eligible + manage-*-eligible via agent list
#      — product_lead (hr:review + hr:manage-agent + hr:manage-repo)
#   C. review-eligible via team + manage-*-eligible via agent list
#      — engineering_manager (engineering team → hr:review; agent → hr:manage-*)
#   D. NON-eligible — no team allow, no agent allow
#      — consultant_head (gets NEITHER review NOR manage-*)
#
# Executor adapter coverage:
#   - claude: dev_agent, frontend_engineer, engineering_manager,
#             product_lead, consultant_head
#   - codex: code_reviewer
#   - opencode: qa_engineer

_REPRESENTATIVE_ROSTER: list[tuple[str, str, str, str, str]] = [
    # (name, team, role, executor, eligibility_class)
    ("dev_agent", "engineering", "worker", "claude", "A — review via team"),
    ("code_reviewer", "engineering", "worker", "codex", "A — review via team; exercises Codex adapter"),
    ("qa_engineer", "engineering", "worker", "opencode", "A — review via team; exercises Opencode adapter"),
    ("frontend_engineer", "engineering", "worker", "claude", "A — review via team"),
    ("engineering_manager", "engineering", "manager", "claude", "C — review (team) + manage-* (agent)"),
    ("product_lead", "product", "manager", "claude", "B — review + manage-* (agent)"),
    ("consultant_head", "consultant", "manager", "claude", "D — NON-eligible"),
]

# Which agents are review-eligible per the real policy (derived from config.yaml)
_REVIEW_ELIGIBLE: frozenset[str] = frozenset({
    "dev_agent", "code_reviewer", "qa_engineer",
    "frontend_engineer", "engineering_manager", "product_lead",
})


# ── Expected system contracts per (context, has_repos) ─────────────────────

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


# ── Helper: assert the real in-repo sources exist at test collection time ──

def _assert_real_sources_present() -> None:
    """Fail-fast at import time if the real in-repo sources are missing.

    This is a canary: if CI suddenly loses these files (e.g. a bad checkout,
    a restructure that moves org/config.yaml), the guard fails loudly rather
    than silently degrading to synthetic fallbacks.
    """
    missing: list[str] = []
    config_path = _REPO_ROOT / "org" / "config.yaml"
    if not config_path.is_file():
        missing.append(str(config_path))
    catalog_path = _REPO_ROOT / "runtime" / "skills"
    if not catalog_path.is_dir():
        missing.append(str(catalog_path))
    proto_path = _REPO_ROOT / "protocol" / "skills"
    if not proto_path.is_dir():
        missing.append(str(proto_path))
    if missing:
        raise RuntimeError(
            "Cutover guard requires real in-repo sources but these are missing:\n"
            + "\n".join(f"  - {m}" for m in missing)
        )

_assert_real_sources_present()


# ── Fixture helpers ────────────────────────────────────────────────────────


def _write_agent_file(paths: OrgPaths, name: str, team: str, role: str,
                      executor: str) -> None:
    """Write a minimal agent .md file so prompt_loader.list_agents() picks it up."""
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text

    agent = AgentDef(
        name=name,
        team=team,
        role=role,  # type: ignore[arg-type]
        executor=executor,
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
    """Write org/teams.yaml so TeamsRegistry.load() works.

    Teams membership is required for eligibility resolution (team-level
    allows like engineering → hr:review). This is synthetic because teams.yaml
    is runtime-only state — NOT in-repo.
    """
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


def _copy_real_eligibility_config(settings: Settings, paths: OrgPaths) -> None:
    """Copy the REAL in-repo org/config.yaml to the test's project_root and
    org_dir so the eligibility resolver reads the actual shipped policy.

    We READ the real file from the repo rather than synthesizing the string
    so the guard fails if the shipped policy drifts.
    """
    real_config = _REPO_ROOT / "org" / "config.yaml"
    content = real_config.read_text()
    for parent in (settings.project_root / "org", paths.org_dir):
        parent.mkdir(parents=True, exist_ok=True)
        (parent / "config.yaml").write_text(content)


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


def _skills_after_bootstrap(workspace: Path, provider: str) -> set[str]:
    """Return skills present after bootstrap for a given provider."""
    if provider == "claude":
        return _collect_skill_ids(workspace / ".claude" / "skills")
    else:
        # codex / opencode write to .agents/skills/
        return _collect_skill_ids(workspace / ".agents" / "skills")


# ── Module-level fixture: isolate _SKILLS_SRC override to this module ───────


@pytest.fixture(autouse=True)
def _isolate_skills_src_override():
    """Set _SKILLS_SRC to the real protocol/skills/ for this module's tests
    and restore it afterward so other test modules aren't affected.
    """
    import runtime.orchestrator.workspace_adapters as wa
    original = wa._SKILLS_SRC
    wa._SKILLS_SRC = _REPO_ROOT / "protocol" / "skills"
    yield
    wa._SKILLS_SRC = original


# ── Also ensure _WHOLESALE_DUMP_ENABLED is False at test start ─────────────


@pytest.fixture(autouse=True)
def _ensure_flag_false():
    """Ensure _WHOLESALE_DUMP_ENABLED starts False for every test.
    The red-proof test explicitly sets it True and restores False.
    """
    import runtime.orchestrator.workspace_adapters as wa
    wa._WHOLESALE_DUMP_ENABLED = False


# ── Bootstrap-no-leak tests: each adapter independently ────────────────────


class TestBootstrapNoLeakAllAdapters:
    """Prove bootstrap leaks NO skills for each executor adapter with the
    wholesale dump disabled (_WHOLESALE_DUMP_ENABLED = False).

    Each adapter is tested separately so an adapter-specific regression
    (e.g. Codex _copy_skills bypassing the gate) fails independently.
    """

    def _setup_and_bootstrap(self, test_settings: Settings, test_runtime: OrgPaths,
                             tmp_path: Path, provider: str) -> Path:
        """Set up org config and bootstrap a workspace via the given provider."""
        import runtime.orchestrator.workspace_adapters as wa

        assert wa._WHOLESALE_DUMP_ENABLED is False, (
            f"_WHOLESALE_DUMP_ENABLED must be OFF for bootstrap-no-leak test"
        )

        # _SKILLS_SRC is already set by the _isolate_skills_src_override fixture

        # Write minimal agent + teams so agents can be resolved
        for name, team, role, executor, _notes in _REPRESENTATIVE_ROSTER:
            _write_agent_file(test_runtime, name, team, role, executor)
        _write_teams_config(test_runtime)
        _copy_real_eligibility_config(test_settings, test_runtime)

        ws = _build_ws(tmp_path, f"bootstrap_{provider}", has_repos=True)

        from runtime.orchestrator.context_builder import ContextBuilder
        builder = ContextBuilder(test_settings, test_runtime, slug="test")
        builder.ensure_workspace_ready(ws, "dev_agent", "system prompt",
                                       provider=provider)
        return ws

    def test_bootstrap_no_leak_claude(self, test_settings: Settings,
                                      test_runtime: OrgPaths, tmp_path: Path):
        """Claude adapter bootstrap must not leak skills into .claude/skills/."""
        ws = self._setup_and_bootstrap(test_settings, test_runtime, tmp_path,
                                       provider="claude")
        leaked = _skills_after_bootstrap(ws, "claude")
        assert not leaked, (
            f"Claude bootstrap leaked skills: {leaked}"
        )

    def test_bootstrap_no_leak_codex(self, test_settings: Settings,
                                     test_runtime: OrgPaths, tmp_path: Path):
        """Codex adapter bootstrap must not leak skills into .agents/skills/."""
        ws = self._setup_and_bootstrap(test_settings, test_runtime, tmp_path,
                                       provider="codex")
        leaked = _skills_after_bootstrap(ws, "codex")
        assert not leaked, (
            f"Codex bootstrap leaked skills: {leaked}"
        )

    def test_bootstrap_no_leak_opencode(self, test_settings: Settings,
                                        test_runtime: OrgPaths, tmp_path: Path):
        """Opencode adapter bootstrap must not leak skills into .agents/skills/."""
        ws = self._setup_and_bootstrap(test_settings, test_runtime, tmp_path,
                                       provider="opencode")
        leaked = _skills_after_bootstrap(ws, "opencode")
        assert not leaked, (
            f"Opencode bootstrap leaked skills: {leaked}"
        )


# ── The contract-completeness gate test ────────────────────────────────────


class TestContractCompletenessPostCutover:
    """Prove EVERY representative agent × session-context receives its complete
    required skill set via explicit injection ONLY — the real in-repo sources
    are used for the eligibility policy, managed catalog, and source skills.

    NO wholesale dump from bootstrap or session-time refresh.
    """

    def test_completeness_gate(
        self, test_settings: Settings, tmp_path: Path, test_runtime: OrgPaths,
    ):
        """The HARD PRECONDITION gate.

        Iterates every representative (agent, context, repo_state), bootstraps
        a fresh workspace via the agent's executor adapter, injects system
        contracts + managed skills, and asserts the EXACT final
        .claude/skills and .agents/skills contents.

        With _WHOLESALE_DUMP_ENABLED = False:
        - Bootstrap must NOT leak any skills into the workspace
        - Only explicit injection delivers skills
        - manage-agent/manage-repo must NEVER appear (fail-closed)
        - review must appear ONLY for eligible agents
        - System contracts must be context-correct
        """
        import runtime.orchestrator.workspace_adapters as wa
        from runtime.orchestrator.context_builder import ContextBuilder
        from runtime.orchestrator.workspace_adapters import (
            inject_system_contracts,
            inject_managed_skills,
        )

        # ── Confirm the flag is OFF ──────────────────────────────────
        assert wa._WHOLESALE_DUMP_ENABLED is False, (
            "_WHOLESALE_DUMP_ENABLED must be OFF for the cutover gate test"
        )

        # _SKILLS_SRC is already set by the _isolate_skills_src_override fixture
        # Use the REAL runtime/skills/ as the managed catalog
        managed_root = _REPO_ROOT / "runtime" / "skills"

        # ── Set up org configuration ─────────────────────────────────
        for name, team, role, executor, _notes in _REPRESENTATIVE_ROSTER:
            _write_agent_file(test_runtime, name, team, role, executor)
        _write_teams_config(test_runtime)
        _copy_real_eligibility_config(test_settings, test_runtime)

        # ── Verify roster is correctly loaded ────────────────────────
        from runtime.orchestrator.prompt_loader import list_agents
        agent_names = {a.name for a in list_agents(test_runtime)}
        expected_names = {name for name, _, _, _, _ in _REPRESENTATIVE_ROSTER}
        assert agent_names == expected_names, (
            f"Agent roster mismatch: got {agent_names}, expected {expected_names}"
        )

        # ── Iterate every (agent, context, repo_state) ───────────────
        failures: list[str] = []

        for name, team, role, executor, _notes in _REPRESENTATIVE_ROSTER:

            for context_str in ("task", "thread", "wake", "dream"):
                ctx_str = context_str  # used below

                for has_repos in (True, False):
                    ws_name = f"ws_{name}_{context_str}_repos{has_repos}"
                    ws = _build_ws(tmp_path, ws_name, has_repos=has_repos)

                    # ── Step 1: Bootstrap (must NOT leak skills) ────
                    builder = ContextBuilder(
                        test_settings, test_runtime, slug="test",
                    )
                    builder.ensure_workspace_ready(
                        ws, name, "system prompt",
                        provider=executor,
                    )

                    # After bootstrap the provider-specific skills dir
                    # should be empty (bootstrap _copy_skills gated)
                    leaked = _skills_after_bootstrap(ws, executor)
                    if leaked:
                        failures.append(
                            f"BOOTSTRAP LEAK ({executor}): .claude/ or .agents/ "
                            f"skills has {leaked} after bootstrap for "
                            f"({name}, {context_str}, repos={has_repos})"
                        )

                    # ── Step 2: Inject system contracts ──────────────
                    inject_system_contracts(
                        ws, test_settings, slug="test",
                        context=context_str,
                    )

                    # ── Step 3: Inject managed-catalog skills ────────
                    inject_managed_skills(
                        ws, test_settings,
                        slug="test",
                        agent_name=name,
                        team=team,
                        skills_root=managed_root,
                    )

                    # ── Step 4: Collect final state ──────────────────
                    injected = _collect_skill_ids(ws / ".claude" / "skills")
                    agents_injected = _collect_skill_ids(ws / ".agents" / "skills")

                    # Both dirs must match
                    if injected != agents_injected:
                        failures.append(
                            f"SKILL DIR MISMATCH for "
                            f"({name}, {context_str}, repos={has_repos}): "
                            f".claude={injected}, .agents={agents_injected}"
                        )

                    # ── Verify system contracts ──────────────────────
                    expected_sys = SYSTEM_CONTRACT_EXPECTATIONS[context_str][has_repos]

                    for sc_id in expected_sys:
                        if sc_id not in injected:
                            failures.append(
                                f"MISSING system contract '{sc_id}' for "
                                f"({name}, {context_str}, repos={has_repos})"
                            )

                    # ── Verify managed-catalog skills ────────────────
                    if name in _REVIEW_ELIGIBLE:
                        if "review" not in injected:
                            failures.append(
                                f"MISSING managed skill 'review' for "
                                f"({name}, {context_str}, repos={has_repos})"
                            )
                    else:
                        if "review" in injected:
                            failures.append(
                                f"UNEXPECTED managed skill 'review' for "
                                f"({name}, {context_str}, repos={has_repos})"
                            )

                    # manage-agent / manage-repo: fail-closed for ALL
                    for hi_skill in ("manage-agent", "manage-repo"):
                        if hi_skill in injected:
                            failures.append(
                                f"UNEXPECTED high-impact skill '{hi_skill}' for "
                                f"({name}, {context_str}, repos={has_repos}) "
                                f"— should be fail-closed (pending_review)"
                            )

                    # ── Verify no bloat / no extra skills ────────────
                    expected_full = set(expected_sys)
                    if name in _REVIEW_ELIGIBLE:
                        expected_full.add("review")
                    unexpected = injected - expected_full
                    if unexpected:
                        failures.append(
                            f"UNEXPECTED skills {unexpected} for "
                            f"({name}, {context_str}, repos={has_repos})"
                        )

        # ── Report ───────────────────────────────────────────────────
        if failures:
            pytest.fail(
                f"{len(failures)} contract-completeness failure(s):\n"
                + "\n".join(f"  - {f}" for f in failures)
            )

    def test_red_proof_wholesale_dump_leaks_skills(
        self, test_settings: Settings, tmp_path: Path, test_runtime: OrgPaths,
    ):
        """RED-PROOF: The guard MUST fail when _WHOLESALE_DUMP_ENABLED is True.

        With the wholesale dump re-enabled, bootstrap copies the ENTIRE
        protocol/skills/ tree (including manage-agent and manage-repo) into
        the workspace skill dirs. This proves the guard would catch a real
        cutover regression — if someone flips manage-agent to approved or
        the flag back to True, this test would fail.

        After the red-proof, the flag is restored to False.
        """
        import runtime.orchestrator.workspace_adapters as wa

        # ── Set up org configuration ─────────────────────────────────
        for name, team, role, executor, _notes in _REPRESENTATIVE_ROSTER:
            _write_agent_file(test_runtime, name, team, role, executor)
        _write_teams_config(test_runtime)
        _copy_real_eligibility_config(test_settings, test_runtime)

        # _SKILLS_SRC is already set by the _isolate_skills_src_override fixture

        from runtime.orchestrator.context_builder import ContextBuilder

        # ── Red-proof: enable the wholesale dump ─────────────────────
        try:
            wa._WHOLESALE_DUMP_ENABLED = True

            ws = _build_ws(tmp_path, "red_proof_ws", has_repos=True)
            builder = ContextBuilder(test_settings, test_runtime, slug="test")
            builder.ensure_workspace_ready(
                ws, "dev_agent", "system prompt", provider="claude",
            )

            # With the wholesale dump ENABLED, the real protocol/skills/
            # tree is copied — including manage-agent and manage-repo.
            # The guard must catch this fail-closed violation.
            leaked = _collect_skill_ids(ws / ".claude" / "skills")

            # At minimum, manage-agent and manage-repo should be present
            # (they exist in the real protocol/skills/ dir)
            for hi_skill in ("manage-agent", "manage-repo"):
                assert hi_skill in leaked, (
                    f"RED-PROOF FAIL: wholesale dump did NOT leak '{hi_skill}' "
                    f"into .claude/skills/. Expected it to be present when "
                    f"_WHOLESALE_DUMP_ENABLED=True. "
                    f"Available skills: {leaked}"
                )

            # Also verify review leaks (it's in protocol/skills/ too)
            assert "review" in leaked, (
                f"RED-PROOF FAIL: wholesale dump did NOT leak 'review'. "
                f"Available: {leaked}"
            )

        finally:
            # ── Restore the flag ─────────────────────────────────────
            wa._WHOLESALE_DUMP_ENABLED = False
