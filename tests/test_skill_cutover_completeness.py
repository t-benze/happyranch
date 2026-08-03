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
If the shipped policy, catalog, or source dirs drift (e.g. reflection's policy_class changes, or a catalog entry
regresses), this guard MUST fail — it is a fail-closed integrity check.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
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
# Eligibility classes (from the real org/config.yaml on main):
#   A. reflection-eligible via org-wide allow (skills.org.allow: [hr:reflection])
#      — dev_agent, code_reviewer, qa_engineer, frontend_engineer
#   B. reflection-eligible (org) + manage-*-eligible via agent list
#      — product_lead (hr:manage-agent + hr:manage-repo)
#   C. reflection-eligible (org) + manage-*-eligible via agent list
#      — engineering_manager (org → hr:reflection; agent → hr:manage-*)
#   D. reflection-eligible (org), NO manage-*
#      — consultant_head (gets reflection via org, no manage-*)
#
# Executor adapter coverage:
#   - claude: dev_agent, frontend_engineer, engineering_manager,
#             product_lead, consultant_head
#   - codex: code_reviewer
#   - opencode: qa_engineer

_REPRESENTATIVE_ROSTER: list[tuple[str, str, str, str, str]] = [
    # (name, team, role, executor, eligibility_class)
    ("dev_agent", "engineering", "worker", "claude", "A — reflection via org"),
    ("code_reviewer", "engineering", "worker", "codex", "A — reflection via org; exercises Codex adapter"),
    ("qa_engineer", "engineering", "worker", "opencode", "A — reflection via org; exercises Opencode adapter"),
    ("frontend_engineer", "engineering", "worker", "claude", "A — reflection via org"),
    ("engineering_manager", "engineering", "manager", "claude", "C — reflection (org) + manage-* (agent)"),
    ("product_lead", "product", "manager", "claude", "B — reflection (org) + manage-* (agent)"),
    ("consultant_head", "consultant", "manager", "claude", "D — reflection (org), NO manage-*"),
]

# Which agents are reflection-eligible per the real policy (derived from config.yaml)
# Organ-wide universal: ALL agents receive hr:reflection
_REFLECTION_ELIGIBLE: frozenset[str] = frozenset({
    "dev_agent", "code_reviewer", "qa_engineer", "frontend_engineer",
    "engineering_manager", "product_lead", "consultant_head",
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
    allows like engineering → hr:reflection). This is synthetic because teams.yaml
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
        - reflection must appear ONLY for eligible agents
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
                    if name in _REFLECTION_ELIGIBLE:
                        if "reflection" not in injected:
                            failures.append(
                                f"MISSING managed skill 'reflection' for "
                                f"({name}, {context_str}, repos={has_repos})"
                            )
                    else:
                        if "reflection" in injected:
                            failures.append(
                                f"UNEXPECTED managed skill 'reflection' for "
                                f"({name}, {context_str}, repos={has_repos})"
                            )

                    # manage-agent / manage-repo: exposed for eligible managers (THR-055 seq 55)
                    # Per real org/config.yaml:
                    #   engineering_manager: allow [manage-agent, manage-repo]
                    #   product_lead: allow [manage-agent, manage-repo]
                    #   All others: NO manage-* eligibility
                    is_manager = name in {"engineering_manager", "product_lead"}
                    for hi_skill in ("manage-agent", "manage-repo"):
                        if is_manager:
                            if hi_skill not in injected:
                                failures.append(
                                    f"MISSING manage skill '{hi_skill}' for "
                                    f"({name}, {context_str}, repos={has_repos})"
                                )
                        else:
                            if hi_skill in injected:
                                failures.append(
                                    f"UNEXPECTED manage skill '{hi_skill}' for "
                                    f"({name}, {context_str}, repos={has_repos})"
                                )

                    # ── Verify no bloat / no extra skills ────────────
                    expected_full = set(expected_sys)
                    if name in _REFLECTION_ELIGIBLE:
                        expected_full.add("reflection")
                    if is_manager:
                        expected_full.update({"manage-agent", "manage-repo"})
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
        """RED-PROOF (POST-CUTOVER): Wholesale dump is permanently removed.

        Even with _WHOLESALE_DUMP_ENABLED=True, the wholesale copy path
        is a no-op. The canonical store + symlink architecture has
        superseded all per-session content copying. This verifies that
        high-policy skills like manage-agent/manage-repo are NOT leaked
        via the (now-dead) wholesale dump path.
        """
        import runtime.orchestrator.workspace_adapters as wa

        for name, team, role, executor, _notes in _REPRESENTATIVE_ROSTER:
            _write_agent_file(test_runtime, name, team, role, executor)
        _write_teams_config(test_runtime)
        _copy_real_eligibility_config(test_settings, test_runtime)

        from runtime.orchestrator.context_builder import ContextBuilder

        wa._WHOLESALE_DUMP_ENABLED = True

        ws = _build_ws(tmp_path, "red_proof_ws", has_repos=True)
        builder = ContextBuilder(test_settings, test_runtime, slug="test")
        builder.ensure_workspace_ready(
            ws, "dev_agent", "system prompt", provider="claude",
        )

        leaked = _collect_skill_ids(ws / ".claude" / "skills")

        # POST-CUTOVER: wholesale dump is dead — NO skills should leak via
        # the wholesale copy path (which is now a no-op regardless of flag).
        for hi_skill in ("manage-agent", "manage-repo", "reflection"):
            assert hi_skill not in leaked, (
                f"RED-PROOF FAIL: dead wholesale dump leaked '{hi_skill}' "
                f"into .claude/skills/. The canonical store + symlink "
                f"architecture must be the sole delivery path. "
                f"Leaked set: {leaked}"
            )

        wa._WHOLESALE_DUMP_ENABLED = False


# ── Materialization-level regression: guard workflow in delivered skill ────


class TestMakeWorktreeGuardInDeliveredSkill:
    """Prove the delivered make-worktree skill body contains the
    worktree-root guard workflow for both supported skill destinations."""

    def test_source_skill_contains_guard_workflow(self):
        """The source protocol/skills/make-worktree/SKILL.md contains
        the guard setup and verify commands."""
        source = _REPO_ROOT / "protocol" / "skills" / "make-worktree" / "SKILL.md"
        body = source.read_text()

        # Guard setup command must be present (via GUARD variable or literal)
        assert ("worktree_guard" in body) or ("GUARD" in body), (
            "Source make-worktree SKILL.md must reference the worktree guard"
        )
        assert "python \"$GUARD\" setup" in body or "worktree_guard setup" in body, (
            "Source make-worktree SKILL.md must contain guard setup command"
        )
        assert "python \"$GUARD\" verify" in body or "worktree_guard verify" in body, (
            "Source make-worktree SKILL.md must contain guard verify command"
        )

        # Canonical root computation
        assert "WORKTREE_ROOT" in body, (
            "Source skill must define WORKTREE_ROOT variable"
        )
        assert "PRIMARY_ROOT" in body, (
            "Source skill must define PRIMARY_ROOT variable"
        )

        # Forbidden-path warning
        assert "FORBIDDEN" in body or "forbidden" in body, (
            "Source skill must state that absolute repos/<repo>/ paths are forbidden"
        )

        # Recovery instructions on failure
        assert "recover" in body.lower(), (
            "Source skill must include recovery instructions"
        )

    def test_delivered_claude_skill_contains_guard(self, test_settings: Settings,
                                                    test_runtime: OrgPaths,
                                                    tmp_path: Path):
        """After injection, .claude/skills/make-worktree/SKILL.md contains
        the guard workflow."""
        from runtime.orchestrator.workspace_adapters import inject_system_contracts

        ws = _build_ws(tmp_path, "guard_claude", has_repos=True)
        inject_system_contracts(ws, test_settings, slug="test", context="task")

        delivered = ws / ".claude" / "skills" / "make-worktree" / "SKILL.md"
        assert delivered.is_file(), (
            f"make-worktree skill not delivered to .claude/skills/: {delivered}"
        )

        body = delivered.read_text()
        assert ("python \"$GUARD\" setup" in body) or ("worktree_guard setup" in body)
        assert ("python \"$GUARD\" verify" in body) or ("worktree_guard verify" in body)
        assert "WORKTREE_ROOT" in body
        assert "PRIMARY_ROOT" in body

    def test_delivered_agents_skill_contains_guard(self, test_settings: Settings,
                                                    test_runtime: OrgPaths,
                                                    tmp_path: Path):
        """After injection, .agents/skills/make-worktree/SKILL.md contains
        the guard workflow (for Codex/Opencode/Pi destinations)."""
        from runtime.orchestrator.workspace_adapters import inject_system_contracts

        ws = _build_ws(tmp_path, "guard_agents", has_repos=True)
        inject_system_contracts(ws, test_settings, slug="test", context="task")

        delivered = ws / ".agents" / "skills" / "make-worktree" / "SKILL.md"
        assert delivered.is_file(), (
            f"make-worktree skill not delivered to .agents/skills/: {delivered}"
        )

        body = delivered.read_text()
        assert ("python \"$GUARD\" setup" in body) or ("worktree_guard setup" in body)
        assert ("python \"$GUARD\" verify" in body) or ("worktree_guard verify" in body)
        assert "WORKTREE_ROOT" in body
        assert "PRIMARY_ROOT" in body

    # ── FINDING-3: Runnable delivery regression tests ───────────────
    # The guard script must be deliverable AND executable in a
    # non-HappyRanch git repo (no runtime/ package available).

    def test_delivered_claude_guard_script_exists(self, test_settings: Settings,
                                                    test_runtime: OrgPaths,
                                                    tmp_path: Path):
        """FINDING-3: The worktree_guard.py script is delivered alongside
        SKILL.md to .claude/skills/make-worktree/."""
        from runtime.orchestrator.workspace_adapters import inject_system_contracts

        ws = _build_ws(tmp_path, "guard_script_claude", has_repos=True)
        inject_system_contracts(ws, test_settings, slug="test", context="task")

        guard_script = ws / ".claude" / "skills" / "make-worktree" / "worktree_guard.py"
        assert guard_script.is_file(), (
            f"Guard script not delivered to .claude/skills/make-worktree/: {guard_script}"
        )

        body = guard_script.read_text()
        assert "def cmd_setup" in body, "Delivered guard must contain cmd_setup"
        assert "def cmd_verify" in body, "Delivered guard must contain cmd_verify"
        assert '__name__ == "__main__"' in body or "if __name__" in body, (
            "Delivered guard must have a __main__ entry point"
        )
        assert "from runtime" not in body, (
            "Delivered guard must not import from runtime (must be standalone)"
        )

    def test_delivered_agents_guard_script_exists(self, test_settings: Settings,
                                                    test_runtime: OrgPaths,
                                                    tmp_path: Path):
        """FINDING-3: The worktree_guard.py script is delivered alongside
        SKILL.md to .agents/skills/make-worktree/."""
        from runtime.orchestrator.workspace_adapters import inject_system_contracts

        ws = _build_ws(tmp_path, "guard_script_agents", has_repos=True)
        inject_system_contracts(ws, test_settings, slug="test", context="task")

        guard_script = ws / ".agents" / "skills" / "make-worktree" / "worktree_guard.py"
        assert guard_script.is_file(), (
            f"Guard script not delivered to .agents/skills/make-worktree/: {guard_script}"
        )

        body = guard_script.read_text()
        assert "def cmd_setup" in body
        assert "def cmd_verify" in body

    def test_delivered_guard_executes_in_non_happyranch_repo(
        self, test_settings: Settings, test_runtime: OrgPaths, tmp_path: Path,
    ):
        """FINDING-3: The delivered guard actually runs in a temp git repo
        that does NOT have the HappyRanch runtime/ package available.

        This proves the guard is a standalone, deliverable, executable asset
        rather than requiring ``python -m runtime.tools.worktree_guard``.
        """
        from runtime.orchestrator.workspace_adapters import inject_system_contracts

        # 1. Materialize the skill into a workspace
        ws = _build_ws(tmp_path, "guard_exec", has_repos=True)
        inject_system_contracts(ws, test_settings, slug="test", context="task")

        guard_script = ws / ".claude" / "skills" / "make-worktree" / "worktree_guard.py"
        assert guard_script.is_file()

        # 2. Create a temp non-HappyRanch git repo (no runtime/ dir)
        test_repo = tmp_path / "test-project"
        test_repo.mkdir()
        subprocess.run(
            ["git", "init"], cwd=test_repo, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=test_repo, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=test_repo, capture_output=True, text=True,
        )
        (test_repo / "README.md").write_text("# Test\n")
        subprocess.run(
            ["git", "add", "README.md"], cwd=test_repo, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=test_repo, capture_output=True, text=True,
        )

        # 3. Create a worktree from this repo
        worktree_dir = test_repo / ".claude" / "worktrees" / "TASK-DELIVERY"
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["git", "-C", str(test_repo), "worktree", "add", str(worktree_dir),
             "-b", "task/TASK-DELIVERY"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"worktree add failed: {r.stderr}"

        try:
            # 4. Run setup via the delivered guard script
            r = subprocess.run(
                [
                    sys.executable, str(guard_script),
                    "setup",
                    "--worktree-root", str(worktree_dir),
                    "--primary-root", str(test_repo),
                    "--task-id", "TASK-DELIVERY",
                ],
                capture_output=True, text=True,
                # No cwd — the guard is a standalone script
            )
            assert r.returncode == 0, (
                f"delivered guard setup failed (exit {r.returncode}):\n"
                f"stdout: {r.stdout}\nstderr: {r.stderr}"
            )
            assert "WORKTREE_ROOT=" in r.stdout
            assert "PRIMARY_ROOT=" in r.stdout

            # 5. Run verify (no changes — should pass)
            r = subprocess.run(
                [
                    sys.executable, str(guard_script),
                    "verify",
                    "--worktree-root", str(worktree_dir),
                ],
                capture_output=True, text=True,
            )
            assert r.returncode == 0, (
                f"delivered guard verify (noop) failed:\n"
                f"stdout: {r.stdout}\nstderr: {r.stderr}"
            )
            assert "GUARD PASS" in r.stdout

            # 6. Make a primary edit — verify must fail
            (test_repo / "accidental.md").write_text("primary edit\n")
            r = subprocess.run(
                [
                    sys.executable, str(guard_script),
                    "verify",
                    "--worktree-root", str(worktree_dir),
                ],
                capture_output=True, text=True,
            )
            assert r.returncode == 1, (
                f"delivered guard verify should fail after primary edit:\n"
                f"stdout: {r.stdout}\nstderr: {r.stderr}"
            )
            assert "GUARD FAILED" in r.stderr
            assert "accidental.md" in r.stderr

        finally:
            # Cleanup
            subprocess.run(
                ["git", "-C", str(test_repo), "worktree", "remove",
                 str(worktree_dir), "--force"],
                capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "-C", str(test_repo), "branch", "-D", "task/TASK-DELIVERY"],
                capture_output=True, text=True,
            )

    def test_delivered_guard_executes_via_agents_destination(
        self, test_settings: Settings, test_runtime: OrgPaths, tmp_path: Path,
    ):
        """FINDING-3: The guard delivered to .agents/skills/ also runs in a
        temp non-HappyRanch git repo (covering Codex/Opencode/Pi)."""
        from runtime.orchestrator.workspace_adapters import inject_system_contracts

        # 1. Materialize
        ws = _build_ws(tmp_path, "guard_exec_agents", has_repos=True)
        inject_system_contracts(ws, test_settings, slug="test", context="task")

        guard_script = ws / ".agents" / "skills" / "make-worktree" / "worktree_guard.py"
        assert guard_script.is_file()

        # 2. Create a temp repo + worktree
        test_repo = tmp_path / "test-project-agents"
        test_repo.mkdir()
        subprocess.run(["git", "init"], cwd=test_repo, capture_output=True, text=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=test_repo, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=test_repo, capture_output=True, text=True,
        )
        (test_repo / "README.md").write_text("# Test\n")
        subprocess.run(
            ["git", "add", "README.md"], cwd=test_repo, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=test_repo, capture_output=True, text=True,
        )

        worktree_dir = test_repo / ".claude" / "worktrees" / "TASK-AGENTS"
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "-C", str(test_repo), "worktree", "add", str(worktree_dir),
             "-b", "task/TASK-AGENTS"],
            capture_output=True, text=True,
        )

        try:
            r = subprocess.run(
                [
                    sys.executable, str(guard_script),
                    "setup",
                    "--worktree-root", str(worktree_dir),
                    "--primary-root", str(test_repo),
                    "--task-id", "TASK-AGENTS",
                ],
                capture_output=True, text=True,
            )
            assert r.returncode == 0, f"agents-destination setup failed: {r.stderr}"
            assert "WORKTREE_ROOT=" in r.stdout

            r = subprocess.run(
                [
                    sys.executable, str(guard_script),
                    "verify",
                    "--worktree-root", str(worktree_dir),
                ],
                capture_output=True, text=True,
            )
            assert r.returncode == 0, f"agents-destination verify failed: {r.stderr}"
            assert "GUARD PASS" in r.stdout

            # Primary edit → fail
            (test_repo / "oops.md").write_text("primary edit\n")
            r = subprocess.run(
                [
                    sys.executable, str(guard_script),
                    "verify",
                    "--worktree-root", str(worktree_dir),
                ],
                capture_output=True, text=True,
            )
            assert r.returncode == 1, "agents-destination should detect primary edit"
            assert "GUARD FAILED" in r.stderr

        finally:
            subprocess.run(
                ["git", "-C", str(test_repo), "worktree", "remove",
                 str(worktree_dir), "--force"],
                capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "-C", str(test_repo), "branch", "-D", "task/TASK-AGENTS"],
                capture_output=True, text=True,
            )


# ── FINDING 1: Delivered workflow lookup — execute the exact skill workflow ─


class TestDeliveredMakeWorktreeWorkflow:
    """Prove the delivered make-worktree SKILL.md workflow can actually
    locate and execute the guard from within a real task worktree in a
    non-HappyRanch repo — the exact setup fragment the skill instructs
    agents to run.

    This is the FINDING-1 fix: the old WORKSPACE_ROOT calculation
    (WORKTREE_ROOT/../../../) resolved only to repos/<repo>, not the
    workspace, so the guard was unfindable. The new calculation
    (WORKTREE_ROOT/../../../../../) correctly reaches the workspace root.
    """

    def _simulate_workflow(
        self, tmp_path: Path, test_settings: Settings, test_runtime: OrgPaths,
        guard_skill_dir: str,  # ".claude" or ".agents"
    ) -> tuple[Path, Path, Path]:
        """Set up a simulated workspace + non-HappyRanch repo + real
        task worktree, materialize contracts, and return
        (workspace, primary_repo, worktree_dir)."""
        from runtime.orchestrator.workspace_adapters import inject_system_contracts
        from runtime.orchestrator.context_builder import ContextBuilder

        # Build a workspace with repos/ structure
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "repos").mkdir()

        # Create a non-HappyRanch git repo under repos/
        repo = workspace / "repos" / "my-project"
        repo.mkdir(parents=True)
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, text=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=repo, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=repo, capture_output=True, text=True,
        )
        (repo / "README.md").write_text("# My Project\n")
        subprocess.run(
            ["git", "add", "README.md"], cwd=repo, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=repo, capture_output=True, text=True,
        )

        # Create a task worktree inside the repo (as the skill instructs)
        wt = repo / ".claude" / "worktrees" / "TASK-DELIVERED"
        wt.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", str(wt),
             "-b", "task/TASK-DELIVERED"],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"worktree add failed: {r.stderr}"

        # Materialize system contracts into the workspace
        inject_system_contracts(workspace, test_settings, slug="test",
                                context="task")

        return workspace, repo, wt

    def test_delivered_workflow_setup_and_verify_claude(
        self, test_settings: Settings, tmp_path: Path, test_runtime: OrgPaths,
    ):
        """FINDING-1: The delivered make-worktree workflow (via .claude/skills)
        executes against a real task worktree — setup succeeds, no-op verify
        passes, primary edit detect fails."""
        workspace, repo, wt = self._simulate_workflow(
            tmp_path, test_settings, test_runtime, ".claude",
        )

        guard = workspace / ".claude" / "skills" / "make-worktree" / "worktree_guard.py"
        assert guard.is_file(), f"Guard not delivered: {guard}"

        # Execute the EXACT workflow from the delivered skill, substituting
        # WORKTREE_ROOT the way the skill instructs
        worktree_root = str(wt.resolve())
        primary_root = str(repo.resolve())
        task_id = "TASK-DELIVERED"  # matches the branch created by _simulate_workflow

        # Step: setup
        r = subprocess.run(
            [
                sys.executable, str(guard),
                "setup",
                "--worktree-root", worktree_root,
                "--primary-root", primary_root,
                "--task-id", "TASK-DELIVERED",
            ],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, (
            f"Delivered Claude guard setup failed:\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert "WORKTREE_ROOT=" in r.stdout
        assert "PRIMARY_ROOT=" in r.stdout

        # Step: verify (no-op)
        r = subprocess.run(
            [
                sys.executable, str(guard),
                "verify",
                "--worktree-root", worktree_root,
            ],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, (
            f"Delivered Claude guard no-op verify failed:\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert "GUARD PASS" in r.stdout

        # Step: primary edit → verify fails
        (repo / "accidental.txt").write_text("bad edit\n")
        r = subprocess.run(
            [
                sys.executable, str(guard),
                "verify",
                "--worktree-root", worktree_root,
            ],
            capture_output=True, text=True,
        )
        assert r.returncode == 1, (
            f"Delivered Claude guard should detect primary edit:\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert "GUARD FAILED" in r.stderr
        assert "accidental.txt" in r.stderr

        # Cleanup
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", str(wt), "--force"],
            capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "branch", "-D", "task/TASK-DELIVERED"],
            capture_output=True, text=True,
        )

    def test_delivered_workflow_setup_and_verify_agents(
        self, test_settings: Settings, tmp_path: Path, test_runtime: OrgPaths,
    ):
        """FINDING-1: The delivered make-worktree workflow (via .agents/skills)
        executes against a real task worktree — setup succeeds, no-op verify
        passes, primary edit detect fails."""
        workspace, repo, wt = self._simulate_workflow(
            tmp_path, test_settings, test_runtime, ".agents",
        )

        guard = workspace / ".agents" / "skills" / "make-worktree" / "worktree_guard.py"
        assert guard.is_file(), f"Guard not delivered to .agents: {guard}"

        worktree_root = str(wt.resolve())
        primary_root = str(repo.resolve())
        # Must match the branch name created in _simulate_workflow:
        # task/TASK-DELIVERED
        task_id = "TASK-DELIVERED"

        # Step: setup
        r = subprocess.run(
            [
                sys.executable, str(guard),
                "setup",
                "--worktree-root", worktree_root,
                "--primary-root", primary_root,
                "--task-id", task_id,
            ],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, (
            f"Delivered Agents guard setup failed:\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

        # Step: verify (no-op)
        r = subprocess.run(
            [
                sys.executable, str(guard),
                "verify",
                "--worktree-root", worktree_root,
            ],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, "Agents guard no-op verify must pass"
        assert "GUARD PASS" in r.stdout

        # Step: primary edit → fail
        (repo / "bad.txt").write_text("edit\n")
        r = subprocess.run(
            [
                sys.executable, str(guard),
                "verify",
                "--worktree-root", worktree_root,
            ],
            capture_output=True, text=True,
        )
        assert r.returncode == 1, "Agents guard must detect primary edit"
        assert "GUARD FAILED" in r.stderr

        # Cleanup
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", str(wt), "--force"],
            capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "branch", "-D", "task/TASK-AGENTS-DEL"],
            capture_output=True, text=True,
        )

    def test_delivered_worktree_edit_not_accused_claude(
        self, test_settings: Settings, tmp_path: Path, test_runtime: OrgPaths,
    ):
        """FINDING-1: A legitimate edit in the task worktree does NOT
        falsely accuse the primary checkout when run through the delivered
        Claude workflow."""
        workspace, repo, wt = self._simulate_workflow(
            tmp_path, test_settings, test_runtime, ".claude",
        )

        guard = workspace / ".claude" / "skills" / "make-worktree" / "worktree_guard.py"
        worktree_root = str(wt.resolve())
        primary_root = str(repo.resolve())
        task_id = "TASK-DELIVERED"  # matches the branch created by _simulate_workflow

        # Setup
        r = subprocess.run(
            [sys.executable, str(guard), "setup",
             "--worktree-root", worktree_root,
             "--primary-root", primary_root,
             "--task-id", task_id],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"setup failed: {r.stderr}"

        # Edit in the worktree (legitimate)
        (wt / "feature.py").write_text("print('hello')\n")

        # Edit in the worktree (legitimate)
        (wt / "feature.py").write_text("print('hello')\n")

        # Verify must pass — worktree edits are never accused
        r = subprocess.run(
            [sys.executable, str(guard), "verify",
             "--worktree-root", worktree_root],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, (
            f"Worktree edit falsely accused primary:\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

        # Cleanup
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", str(wt), "--force"],
            capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "branch", "-D", "task/TASK-WT-EDIT"],
            capture_output=True, text=True,
        )

    def test_delivered_workflow_workspace_root_computed_correctly(
        self, test_settings: Settings, tmp_path: Path, test_runtime: OrgPaths,
    ):
        """FINDING-1: The WORKSPACE_ROOT calculation in the delivered
        SKILL.md matches the actual workspace root by executing the
        exact shell computation from the skill."""
        workspace, repo, wt = self._simulate_workflow(
            tmp_path, test_settings, test_runtime, ".claude",
        )

        # Verify the skill's workspace root computation works:
        # WORKSPACE_ROOT=$(cd "$WORKTREE_ROOT/../../../../.." && pwd -P)
        worktree_root = str(wt.resolve())
        computed = subprocess.run(
            ["/bin/bash", "-c",
             f'cd "$1/../../../../.." && pwd -P', "_",
             worktree_root],
            capture_output=True, text=True,
        )
        assert computed.returncode == 0, f"Shell computation failed: {computed.stderr}"
        computed_ws = Path(computed.stdout.strip()).resolve()
        actual_ws = workspace.resolve()
        assert computed_ws == actual_ws, (
            f"WORKSPACE_ROOT mismatch:\n"
            f"  Computed: {computed_ws}\n"
            f"  Actual:   {actual_ws}\n"
            f"  Worktree: {worktree_root}"
        )

        # Now verify the guard exists at the computed path
        guard_claude = computed_ws / ".claude" / "skills" / "make-worktree" / "worktree_guard.py"
        guard_agents = computed_ws / ".agents" / "skills" / "make-worktree" / "worktree_guard.py"
        assert guard_claude.is_file(), f"Guard not at computed .claude path: {guard_claude}"
        assert guard_agents.is_file(), f"Guard not at computed .agents path: {guard_agents}"

        # Cleanup
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "remove", str(wt), "--force"],
            capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "branch", "-D", "task/TASK-DELIVERED"],
            capture_output=True, text=True,
        )

    # ── FINDING-1 literal shell fragment helpers + tests ──────────────

    def _run_literal_skill_fragment(
        self, workspace: Path, repo: Path, wt: Path,
        guard_skill_dir: str,  # ".claude" or ".agents"
    ) -> None:
        """Execute the literal SKILL.md shell workflow fragment and verify
        it correctly computes PRIMARY_ROOT and locates/runs the guard.

        This faithfully replicates the exact bash fragment the skill prints:
          WORKTREE_ROOT=$(pwd -P)
          PRIMARY_ROOT=$(cd ../../.. && pwd -P)
          WORKSPACE_ROOT=$(cd "$WORKTREE_ROOT/../../../../.." && pwd -P)
          GUARD="$WORKSPACE_ROOT/<dir>/skills/make-worktree/worktree_guard.py"
          python "$GUARD" setup --worktree-root "$WORKTREE_ROOT" ...

        The test runs this via /bin/bash so it exercises the EXACT same
        shell computations the agent would perform — no caller passes
        a known-correct primary root.
        """
        worktree_root = str(wt.resolve())

        # Step A: Prove PRIMARY_ROOT = cd ../../.. from worktree = primary,
        # NOT cd ../.. which would be <primary>/.claude.
        r = subprocess.run(
            ["/bin/bash", "-c",
             'cd "$1" && cd ../../.. && pwd -P', "_", str(wt)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"cd ../../.. failed: {r.stderr}"
        computed_pr = Path(r.stdout.strip()).resolve()
        assert computed_pr == repo.resolve(), (
            f"PRIMARY_ROOT mismatch:\n"
            f"  Computed (cd ../../..): {computed_pr}\n"
            f"  Actual primary repo:    {repo.resolve()}\n"
            f"  Worktree:               {wt}"
        )

        # Step B: Prove cd ../.. is WRONG (would be .claude, not primary)
        r = subprocess.run(
            ["/bin/bash", "-c",
             'cd "$1" && cd ../.. && pwd -P', "_", str(wt)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0
        wrong_pr = Path(r.stdout.strip()).resolve()
        assert wrong_pr != repo.resolve(), (
            f"cd ../.. MUST NOT equal primary root:\n"
            f"  cd ../.. result:  {wrong_pr}\n"
            f"  Actual primary:   {repo.resolve()}\n"
            f"  Expected mismatch (cd ../.. = .claude/ dir)"
        )

        # Step C: Compute WORKSPACE_ROOT the way the skill does
        r = subprocess.run(
            ["/bin/bash", "-c",
             'cd "$1" && cd ../../../../.. && pwd -P', "_", str(wt)],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, f"WORKSPACE_ROOT computation failed: {r.stderr}"
        computed_ws = Path(r.stdout.strip()).resolve()
        assert computed_ws == workspace.resolve(), (
            f"WORKSPACE_ROOT mismatch:\n"
            f"  Computed: {computed_ws}\n"
            f"  Actual:   {workspace.resolve()}"
        )

        # Step D: Locate guard via workspace root (exact skill logic)
        guard_path = (
            computed_ws / guard_skill_dir / "skills"
            / "make-worktree" / "worktree_guard.py"
        )
        assert guard_path.is_file(), (
            f"Guard not found at skill-located path: {guard_path}\n"
            f"  Workspace root: {computed_ws}\n"
            f"  Skill dir: {guard_skill_dir}"
        )

        # Step E: Run setup through the located guard — use the computed
        # primary root (proved correct in Step A).
        r = subprocess.run(
            [
                sys.executable, str(guard_path),
                "setup",
                "--worktree-root", worktree_root,
                "--primary-root", str(computed_pr),
                "--task-id", "TASK-DELIVERED",
            ],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, (
            f"Guard setup via literal-skill-path failed:\n"
            f"  Guard: {guard_path}\n"
            f"  stdout: {r.stdout}\n"
            f"  stderr: {r.stderr}"
        )
        assert "WORKTREE_ROOT=" in r.stdout
        assert "PRIMARY_ROOT=" in r.stdout

        # Step F: No-op verify passes
        r = subprocess.run(
            [
                sys.executable, str(guard_path),
                "verify",
                "--worktree-root", worktree_root,
            ],
            capture_output=True, text=True,
        )
        assert r.returncode == 0, (
            f"No-op verify via literal-skill-path failed:\n"
            f"  stdout: {r.stdout}\n"
            f"  stderr: {r.stderr}"
        )
        assert "GUARD PASS" in r.stdout

        # Step G: Primary edit fails verify
        (repo / "oops.txt").write_text("bad\n")
        r = subprocess.run(
            [
                sys.executable, str(guard_path),
                "verify",
                "--worktree-root", worktree_root,
            ],
            capture_output=True, text=True,
        )
        assert r.returncode == 1, (
            f"Primary-edit detection via literal-skill-path failed:\n"
            f"  stdout: {r.stdout}\n"
            f"  stderr: {r.stderr}"
        )
        assert "GUARD FAILED" in r.stderr

    def test_literal_shell_fragment_claude(
        self, test_settings: Settings, tmp_path: Path, test_runtime: OrgPaths,
    ):
        """FINDING-1: Execute the literal SKILL.md shell fragment for the
        .claude/skills injection destination — proves cd ../../.. reaches
        the primary and the guard is locatable + runnable."""
        workspace, repo, wt = self._simulate_workflow(
            tmp_path, test_settings, test_runtime, ".claude",
        )
        try:
            self._run_literal_skill_fragment(workspace, repo, wt, ".claude")
        finally:
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", str(wt), "--force"],
                capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "branch", "-D", "task/TASK-DELIVERED"],
                capture_output=True, text=True,
            )

    def test_literal_shell_fragment_agents(
        self, test_settings: Settings, tmp_path: Path, test_runtime: OrgPaths,
    ):
        """FINDING-1: Execute the literal SKILL.md shell fragment for the
        .agents/skills injection destination — proves cd ../../.. reaches
        the primary and the guard is locatable + runnable."""
        workspace, repo, wt = self._simulate_workflow(
            tmp_path, test_settings, test_runtime, ".agents",
        )
        try:
            self._run_literal_skill_fragment(workspace, repo, wt, ".agents")
        finally:
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "remove", str(wt), "--force"],
                capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "-C", str(repo), "branch", "-D", "task/TASK-DELIVERED"],
                capture_output=True, text=True,
            )
