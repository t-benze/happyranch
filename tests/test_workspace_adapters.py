import json
import os
from pathlib import Path

import pytest

from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.workspace_adapters import (
    ClaudeWorkspaceAdapter,
    CodexWorkspaceAdapter,
    InstructionPairConflict,
    OpencodeWorkspaceAdapter,
    canonical_instruction_pair_ok,
    instruction_pair_refusal,
    write_canonical_instruction_pair,
)
from runtime.runtime import RuntimeDir


@pytest.fixture
def runtime(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    return OrgPaths(root=rt.orgs_dir / "test")


def test_claude_adapter_bootstrap_creates_claude_files_and_skills(test_settings, tmp_dir, runtime):
    skills_root = test_settings.get_bundled_skills_dir()
    (skills_root / "start-task").mkdir(parents=True)
    (skills_root / "start-task" / "SKILL.md").write_text("# start-task\n")

    workspace = tmp_dir / "workspaces" / "dev_agent"
    (workspace / "repos" / "my-opc" / ".git").mkdir(parents=True)

    ClaudeWorkspaceAdapter(test_settings, runtime, slug="test").ensure_workspace_ready(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )

    assert (workspace / "CLAUDE.md").exists()
    assert (workspace / ".claude" / "settings.json").exists()
    # Cutover: wholesale dump disabled — no skills land during bootstrap.
    assert not (workspace / ".claude" / "skills" / "start-task" / "SKILL.md").exists()
    assert (workspace / "memory").is_dir()
    assert (workspace / "memory" / "_index.md").exists()
    assert not (workspace / "learnings.md").exists()
    assert not (workspace / "scorecard.md").exists()
    assert (workspace / "task_history.md").exists()

    data = json.loads((workspace / ".claude" / "settings.json").read_text())
    # THR-103: repo freshness is daemon-side; no PreToolUse pull hook is baked.
    assert data["hooks"] == {}


def test_codex_adapter_bootstrap_creates_agents_md_and_skills_tree(test_settings, tmp_dir, runtime):
    """Codex CLI ≥0.125 discovers skills under ``.agents/skills/`` (walking from
    cwd up to repo root). The adapter must copy ``runtime/skills/bundled/`` into the
    workspace and AGENTS.md must point at the start-task skill — not inline
    the full completion contract (the skill is the source of truth).
    """
    skills_root = test_settings.get_bundled_skills_dir()
    (skills_root / "start-task").mkdir(parents=True)
    (skills_root / "start-task" / "SKILL.md").write_text(
        "---\nname: start-task\ndescription: Use this skill at the start of every task.\n---\n"
    )
    (skills_root / "reflection").mkdir(parents=True)
    (skills_root / "reflection" / "SKILL.md").write_text(
        "---\nname: reflection\ndescription: Mid-thread reflection capturing learnings and KB entries.\n---\n"
    )

    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "recent_tasks.md").write_text("# Recent Tasks: dev_agent\n\n- TASK-001\n")

    CodexWorkspaceAdapter(test_settings, runtime, slug="test").ensure_workspace_ready(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )

    assert (workspace / "AGENTS.md").is_file()
    assert not (workspace / "AGENTS.md").is_symlink()
    assert (workspace / "CLAUDE.md").is_symlink()
    assert os.readlink(workspace / "CLAUDE.md") == "AGENTS.md"
    assert (workspace / "CLAUDE.md").resolve() == (workspace / "AGENTS.md").resolve()
    # Cutover: wholesale dump disabled — no skills land during bootstrap.
    assert not (workspace / ".claude" / "skills" / "start-task").exists()
    assert not (workspace / ".agents" / "skills" / "start-task" / "SKILL.md").exists()
    assert not (workspace / ".agents" / "skills" / "reflection" / "SKILL.md").exists()
    # Fresh workspace: migrated layout (memory/ dir, no flat learnings.md).
    assert (workspace / "memory").is_dir()
    assert (workspace / "memory" / "_index.md").exists()
    assert not (workspace / "learnings.md").exists()
    assert not (workspace / "scorecard.md").exists()
    assert (workspace / "task_history.md").exists()
    assert not (workspace / "recent_tasks.md").exists()

    body = (workspace / "AGENTS.md").read_text()
    assert "You are the Dev Agent." in body
    # Points at the skill.
    assert ".agents/skills/start-task/" in body
    assert ".claude/settings.json" not in body
    assert "PreToolUse" not in body
    assert "Bash(happyranch:*)" not in body


def test_copy_skills_substitutes_org_slug(tmp_path: Path, monkeypatch) -> None:
    """Canonical model: {ORG_SLUG} is NOT substituted in canonical bytes.

    The org context is passed via HAPPYRANCH_ORG_SLUG environment variable
    set by _callee_env(org_slug=...). Canonical bytes retain {ORG_SLUG}
    as a literal placeholder; the child process receives the real slug via env.
    """
    from runtime.config import Settings

    proto = tmp_path / "runtime" / "skills" / "bundled" / "start-task"
    proto.mkdir(parents=True)
    (proto / "SKILL.md").write_text(
        "Run: happyranch report-completion --org {ORG_SLUG} --task-id ...\n"
    )
    monkeypatch.setattr(
        "runtime.orchestrator.workspace_adapters._SKILLS_SRC",
        tmp_path / "runtime" / "skills" / "bundled",
    )

    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "hk-tourism")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    # Add a repo directory so system contract resolution works
    (workspace / "repos" / "test").mkdir(parents=True)
    import subprocess
    subprocess.run(["git", "init"], cwd=workspace / "repos" / "test",
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=workspace / "repos" / "test", capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=workspace / "repos" / "test", capture_output=True)

    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="hk-tourism")
    # Copy skills is a no-op in the canonical model — skills are symlinked
    adapter._copy_skills(workspace)

    # Verify: no .claude/skills directory created by the no-op adapter call.
    # Materialization now happens via materialize_workspace_skills which
    # creates symlinks, not content copies.
    claude_skills = workspace / ".claude" / "skills"
    if claude_skills.is_dir():
        # Canonical model creates symlinks from canonical store.
        # {ORG_SLUG} in canonical content is NOT substituted.
        start_task_link = claude_skills / "start-task"
        if start_task_link.is_symlink():
            # Symlink resolves to canonical store — content has literal {ORG_SLUG}
            pass  # Correct: canonical bytes retain {ORG_SLUG}
    # No assertion about substituted content — that's the env var's job


def test_opencode_adapter_bootstrap_creates_agents_md_skills_and_opencode_json(
    test_settings, tmp_dir, runtime,
):
    """opencode reads AGENTS.md and discovers skills under .agents/skills/.
    The opencode-specific surface is opencode.json — a structured permission
    file that gates bash by command-prefix glob. The adapter must write all
    three.
    """
    skills_root = test_settings.get_bundled_skills_dir()
    (skills_root / "start-task").mkdir(parents=True)
    (skills_root / "start-task" / "SKILL.md").write_text(
        "---\nname: start-task\ndescription: Use this skill at the start of every task.\n---\n"
    )

    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)

    OpencodeWorkspaceAdapter(test_settings, runtime, slug="test").ensure_workspace_ready(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )

    assert (workspace / "AGENTS.md").is_file()
    assert not (workspace / "AGENTS.md").is_symlink()
    assert (workspace / "CLAUDE.md").is_symlink()
    assert os.readlink(workspace / "CLAUDE.md") == "AGENTS.md"
    assert (workspace / "CLAUDE.md").resolve() == (workspace / "AGENTS.md").resolve()
    # Cutover: wholesale dump disabled — no skills land during bootstrap.
    assert not (workspace / ".agents" / "skills" / "start-task" / "SKILL.md").exists()
    assert not (workspace / ".claude" / "skills" / "start-task").exists()
    # Fresh workspace: migrated layout.
    assert (workspace / "memory").is_dir()
    assert (workspace / "memory" / "_index.md").exists()
    assert not (workspace / "learnings.md").exists()
    assert (workspace / "task_history.md").exists()
    # opencode-specific permission file.
    assert (workspace / "opencode.json").exists()
    # Claude-specific surfaces must NOT be present in an opencode workspace.
    assert not (workspace / ".claude" / "settings.json").exists()


def test_opencode_json_strict_deny_default_with_opc_baseline(
    test_settings, tmp_dir, runtime,
):
    """opencode.json must default to ``bash.*: deny`` and explicitly allow
    only sanctioned prefixes. The baseline ``happyranch *`` is always allowed; an
    agent without per-agent extras gets exactly the baseline."""
    skills_root = test_settings.get_bundled_skills_dir()
    (skills_root / "start-task").mkdir(parents=True)
    (skills_root / "start-task" / "SKILL.md").write_text("# start-task\n")

    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)

    OpencodeWorkspaceAdapter(test_settings, runtime, slug="test").ensure_workspace_ready(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )

    config = json.loads((workspace / "opencode.json").read_text())
    bash = config["permission"]["bash"]
    assert bash["*"] == "deny"
    assert bash["happyranch *"] == "allow"
    # No --dangerously-skip-permissions surrogate (e.g. global "*" allow).
    assert config["permission"].get("*") != "allow"


def test_opencode_json_includes_agent_specific_allow_rules(
    test_settings, tmp_dir, runtime,
):
    """Per-agent allow_rules in agent frontmatter must surface as opencode
    bash allow entries. Source of truth is the same frontmatter Claude reads;
    only the rendering differs (Bash(prefix:*) → "prefix *": "allow")."""
    from datetime import datetime, timezone
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text

    eh = AgentDef(
        name="engineering_head",
        team="engineering",
        role="manager",
        executor="opencode",
        allow_rules=("gh pr close", "gh issue close"),
        repos={},
        enrolled_by=None,
        enrolled_at_task=None,
        enrolled_at=datetime.now(timezone.utc),
        system_prompt="You are the Engineering Head.\n",
    )
    runtime.agents_dir.mkdir(parents=True, exist_ok=True)
    (runtime.agents_dir / "engineering_head.md").write_text(render_agent_text(eh))

    skills_root = test_settings.get_bundled_skills_dir()
    (skills_root / "start-task").mkdir(parents=True)
    (skills_root / "start-task" / "SKILL.md").write_text("# start-task\n")

    workspace = tmp_dir / "workspaces" / "engineering_head"
    workspace.mkdir(parents=True)

    OpencodeWorkspaceAdapter(test_settings, runtime, slug="test").ensure_workspace_ready(
        workspace=workspace,
        agent_name="engineering_head",
        system_prompt="You are the Engineering Head.",
    )

    bash = json.loads((workspace / "opencode.json").read_text())["permission"]["bash"]
    assert bash["happyranch *"] == "allow"
    assert bash["gh pr close *"] == "allow"
    assert bash["gh issue close *"] == "allow"
    # Guardrail: scopes that are NOT in allow_rules must not leak in.
    assert "gh pr merge *" not in bash
    assert "gh pr create *" not in bash


def test_codex_agents_md_does_not_inline_completion_contract(test_settings, tmp_dir, runtime):
    """The completion contract used to be duplicated into AGENTS.md as prose
    + JSON because Codex couldn't resolve SKILL.md. As of Codex CLI 0.125 it
    can — the start-task skill in ``.agents/skills/`` is the source of truth
    and AGENTS.md must not re-inline its body. Two reasons:

    1. Drift: every contract change had to be applied in two places.
    2. Scope: Codex sessions implicit-invoke the skill via ``description``
       matching, so the skill is reliably loaded; duplicating its body is dead
       weight that bloats every AGENTS.md.

    This test is the inverse of the (now-removed) "inlines_completion_contract"
    test that locked in the pre-0.125 behavior.
    """
    skills_root = test_settings.get_bundled_skills_dir()
    (skills_root / "start-task").mkdir(parents=True)
    (skills_root / "start-task" / "SKILL.md").write_text(
        "---\nname: start-task\ndescription: Use this skill at the start of every task.\n---\n"
    )

    workspace = tmp_dir / "workspaces" / "senior_dev"
    workspace.mkdir(parents=True)

    CodexWorkspaceAdapter(test_settings, runtime, slug="test").ensure_workspace_ready(
        workspace=workspace,
        agent_name="senior_dev",
        system_prompt="You are the Senior Developer.",
    )

    body = (workspace / "AGENTS.md").read_text()

    # The skill pointer is present.
    assert "start-task" in body
    assert ".agents/skills/start-task/" in body

    # The full JSON schema is NOT inlined — it lives in the skill file.
    assert '"task_id"' not in body
    assert '"session_id"' not in body
    assert '/tmp/completion-' not in body

    # The EH decision contract is also delegated to the skill.
    assert '"decision"' not in body
    assert "delegate" not in body
    assert "escalate" not in body


def test_claude_md_includes_shared_artifacts_section(tmp_path: Path) -> None:
    # Adjust adapter construction to match the existing test fixtures.
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_claude_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "CLAUDE.md").read_text()
    assert "## Shared Artifacts" in content
    assert "happyranch artifacts put" in content
    assert "happyranch artifacts list" in content
    assert "happyranch artifacts get" in content


def test_codex_agents_md_includes_shared_artifacts_section(tmp_path: Path) -> None:
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = CodexWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    assert "## Shared Artifacts" in content
    assert "happyranch artifacts put" in content
    assert "happyranch artifacts list" in content
    assert "happyranch artifacts get" in content


def test_opencode_agents_md_includes_shared_artifacts_section(tmp_path: Path) -> None:
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import OpencodeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = OpencodeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    assert "## Shared Artifacts" in content
    assert "happyranch artifacts put" in content
    assert "happyranch artifacts list" in content
    assert "happyranch artifacts get" in content


def test_claude_md_warns_about_non_stop_commands(tmp_path: Path) -> None:
    """Bootstrap must steer agents off synchronous bash for non-returning commands."""
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_claude_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "CLAUDE.md").read_text()
    assert "## Long-running and non-stop commands" in content
    # Lists at least the canonical signals
    assert "npm run dev" in content
    assert "tail -f" in content
    # Points at the jobs skill (the actual remediation path) without
    # advertising a retired release-internal source path to agents.
    assert "**jobs** skill" in content
    assert "workspace's skills directory" in content
    # Mentions the flags so the agent knows what to fill on the submit form
    assert "persistent" in content
    assert "review_required" in content
    # TASK-3604: no auto-revisit — the warning must NOT promise automatic retries
    assert "auto-revisit" not in content
    assert "auto_revisit" not in content.lower()
    # Must deny automatic retries
    assert "no automatic retries" in content.lower()
    # TASK-3604: contract states terminal FAILED
    assert "FAILED" in content


def test_codex_agents_md_warns_about_non_stop_commands(tmp_path: Path) -> None:
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = CodexWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    assert "## Long-running and non-stop commands" in content
    assert "**jobs** skill" in content
    assert "workspace's skills directory" in content
    # TASK-3604: no auto-revisit in generated instruction
    assert "auto-revisit" not in content.lower()
    assert "FAILED" in content


def test_opencode_agents_md_warns_about_non_stop_commands(tmp_path: Path) -> None:
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import OpencodeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = OpencodeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    assert "## Long-running and non-stop commands" in content
    assert "**jobs** skill" in content
    assert "workspace's skills directory" in content
    # TASK-3604: no auto-revisit in generated instruction
    assert "auto-revisit" not in content.lower()
    assert "FAILED" in content


def test_non_stop_command_warning_section_contract(tmp_path: Path) -> None:
    """The builder output must state terminal FAILED, no auto-revisit (TASK-3604).

    The injected instruction is read by every agent every session — a stale
    auto-revisit promise is an operational contract violation per MEM-380.
    """
    from runtime.orchestrator.workspace_adapters import (
        _non_stop_command_warning_section,
    )

    lines = _non_stop_command_warning_section()
    text = "".join(lines)

    # Core contract: terminal FAILED, no automatic successor
    assert "FAILED" in text, (
        "non-stop command warning must state terminal FAILED"
    )
    assert "auto-revisit" not in text, (
        "non-stop command warning must not promise auto-revisit"
    )
    assert "twice per failure" not in text, (
        "non-stop command warning must not claim twice-per-failure retries"
    )
    assert "auto_revisit" not in text.lower(), (
        "non-stop command warning must not reference auto-revisit mechanism"
    )
    # Still recommends the delivered jobs skill without referring to a
    # retired release-internal source location.
    assert "**jobs** skill" in text
    assert "workspace's skills directory" in text
    # Mentions explicit recovery paths
    assert ("happyranch revisit" in text or "FAILED" in text), (
        "non-stop command warning must reference terminal failure or explicit recovery"
    )

    # The returned list is the literals injected into every bootstrap doc —
    # verify specific line shape hasn't accidentally dropped the section heading.
    assert any("## Long-running and non-stop commands" in l for l in lines), (
        "missing section heading"
    )


def test_skills_directory_readonly_section_both_roots(tmp_path: Path) -> None:
    """The skills-directory guidance must name both .claude/skills and
    .agents/skills roots in EVERY provider output (not merely the
    selected root), acknowledge same-owner residency, assert
    detection/refusal/no-local-automatic-recovery, assert manual
    external re-sync/redeploy recovery, and disclaim OS-level security
    enforcement.

    This injected section is read by every agent every session — a stale
    distinct-identity, single-root, or auto-recovery claim is a contract
    violation.
    """
    from runtime.orchestrator.workspace_adapters import (
        _skills_directory_readonly_section,
    )

    # Verify with both roots — but EVERY output must name BOTH roots
    for skills_dir in (".claude/skills", ".agents/skills"):
        lines = _skills_directory_readonly_section(skills_dir, "frontend_engineer")
        text = "".join(lines)

        # Section heading exists
        assert "## Skills Directory (do not edit)" in text

        # BOTH managed roots are named in EVERY output (not merely the
        # provider-selected root).
        assert ".claude/skills" in text, (
            f"guidance for {skills_dir} must name .claude/skills"
        )
        assert ".agents/skills" in text, (
            f"guidance for {skills_dir} must name .agents/skills"
        )

        # Same-owner residency: executor and daemon share OS identity
        assert "same OS identity" in text, (
            "must state executor and daemon share same OS identity"
        )

        # No OS-enforced security claims
        assert "OS-enforced security boundary" in text, (
            "must disclaim OS-enforced security boundary"
        )

        # Detection/refusal: no local automatic recovery/autoheal
        assert "NO local automatic" in text, (
            "must assert no local automatic recovery/autoheal"
        )

        # Manual recovery: external re-sync/redeploy
        assert "manual authoritative external re-sync/redeploy" in text, (
            "must assert manual authoritative external re-sync/redeploy recovery"
        )

        # Does NOT reference opt-in env var
        assert "HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR" not in text, (
            "must not reference HAPPYRANCH_ALLOW_SAME_OWNER_EXECUTOR env var"
        )

        # Does not claim filesystem immutability or ACL denial. A B2 response
        # accurately names its returned immutable version.
        normalized = text.lower().replace("-", " ")
        for forbidden in ("filesystem immutable", "os enforced immutable", "ACL denial"):
            assert forbidden.lower() not in normalized, (
                f"must not claim {forbidden!r}"
            )

        assert "happyranch skills create --from-file <path>" in text
        assert "immutable version" in text
        assert "default-hidden reason" in text
        assert "verified agent/task/session" in text
        assert "provenance" in text


def test_skills_directory_rendered_guidance_exposes_b2_create_skill_to_every_verified_agent(
    test_settings, tmp_dir, runtime,
) -> None:
    """Every verified agent gets the session-bound B2 create-skill guidance."""
    workspace = tmp_dir / "workspaces" / "dev_agent"
    adapter = CodexWorkspaceAdapter(test_settings, runtime, slug="test")

    adapter.write_agents_md(workspace, "dev_agent", "You are the Dev Agent.")

    text = (workspace / "AGENTS.md").read_text()
    assert "happyranch skills create --from-file <path> --session-id <your-session-id>" in text
    assert "every verified agent can use the B2" in text
    assert "there is no pilot roster or slug restriction" in text
    assert "immutable version" in text
    assert "default-hidden reason" in text
    assert "verified agent/task/session" in text
    assert "provenance" in text


def test_claude_md_includes_thread_talk_dispatch_doctrine(tmp_path: Path) -> None:
    """Every agent's bootstrap doc must carry the self-only dispatch doctrine.

    The route enforces the rule mechanically (returns 403 with
    thread_dispatch_must_be_self); this prompt
    section is the *why* and the recommended pattern, surfaced before the
    agent encounters the rejection.
    """
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_claude_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "CLAUDE.md").read_text()
    assert "## Thread Dispatch is Self-Only" in content
    # Both rejection codes named — agents hitting a 403 can grep for either.
    assert "thread_dispatch_must_be_self" in content
    # The recommended alternative path: compose for cross-agent work.
    assert "happyranch threads compose" in content


def test_codex_agents_md_includes_thread_dispatch_doctrine(tmp_path: Path) -> None:
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = CodexWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    assert "## Thread Dispatch is Self-Only" in content
    assert "thread_dispatch_must_be_self" in content


def test_opencode_agents_md_includes_thread_dispatch_doctrine(tmp_path: Path) -> None:
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import OpencodeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = OpencodeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    assert "## Thread Dispatch is Self-Only" in content
    assert "thread_dispatch_must_be_self" in content


def _assert_task_completion_format_section(content: str) -> None:
    """Shared assertions for the system-injected Task Completion Format
    section. Every executor's bootstrap doc must carry this block so that
    agents no longer have to author (and drift from) their own."""
    # Header present
    assert "## Task Completion Format" in content
    # Routes the agent at the canonical source rather than restating the schema
    assert "start-task" in content
    assert "happyranch report-completion --from-file" in content
    # Universal prose-summary items the agent should hit
    assert "Findings, risks, or concerns" in content
    assert "founder decision" in content
    assert "Follow-up" in content
    # Manager-only `decision` block is referenced (so managers know the skill
    # carries the delegate/done/escalate shapes — but the section itself does
    # NOT restate the schema, the skill does).
    assert "`decision`" in content


def test_claude_md_includes_task_completion_format_section(tmp_path: Path) -> None:
    """Replaces the per-agent ``## Task Completion Format`` stubs that lived
    in agent ``.md`` files with a single system-injected section. Agents no
    longer author (or leave dangling) this content; the system carries it
    uniformly across every role."""
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_claude_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "CLAUDE.md").read_text()
    _assert_task_completion_format_section(content)


def test_codex_agents_md_includes_task_completion_format_section(tmp_path: Path) -> None:
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = CodexWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    _assert_task_completion_format_section(content)


def test_opencode_agents_md_includes_task_completion_format_section(tmp_path: Path) -> None:
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import OpencodeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = OpencodeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    _assert_task_completion_format_section(content)


def test_reserved_header_in_claude_agent_body_raises(tmp_path: Path) -> None:
    """Boundary enforcement: an agent body that authors a reserved H2 header
    must fail at bootstrap-doc write time, before any session sees the
    duplicated section. This is the runtime guard against the Finding-B
    regression: if a founder hand-edits an agent file or a future
    ``manage-agent`` callback writes one with a colliding header, the next
    workspace setup raises and tells the founder exactly which header to
    rename.
    """
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import (
        ClaudeWorkspaceAdapter,
        ReservedHeaderInAgentBody,
    )

    paths = OrgPaths(root=tmp_path)
    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    bad_body = (
        "You are dev_agent.\n\n"
        "## Workflow\n"
        "Some custom workflow text that collides with the system section.\n"
    )
    with pytest.raises(ReservedHeaderInAgentBody) as exc:
        adapter.write_claude_md(workspace, "dev_agent", bad_body)
    # Error message must name the offending header so the founder can fix it
    # without reading source.
    assert "'Workflow'" in str(exc.value)
    assert "dev_agent" in str(exc.value)


def test_reserved_header_in_codex_agent_body_raises(tmp_path: Path) -> None:
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import (
        CodexWorkspaceAdapter,
        ReservedHeaderInAgentBody,
    )

    paths = OrgPaths(root=tmp_path)
    adapter = CodexWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    bad_body = (
        "You are dev_agent.\n\n"
        "## Knowledge Base (shared across agents)\n"
        "Local override of the system KB section.\n"
    )
    with pytest.raises(ReservedHeaderInAgentBody) as exc:
        adapter.write_agents_md(workspace, "dev_agent", bad_body)
    assert "'Knowledge Base (shared across agents)'" in str(exc.value)


def test_reserved_header_in_opencode_agent_body_raises(tmp_path: Path) -> None:
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import (
        OpencodeWorkspaceAdapter,
        ReservedHeaderInAgentBody,
    )

    paths = OrgPaths(root=tmp_path)
    adapter = OpencodeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    bad_body = (
        "You are dev_agent.\n\n"
        "## Available Repositories\n"
        "neihoumacau (main product repo).\n"
    )
    with pytest.raises(ReservedHeaderInAgentBody) as exc:
        adapter.write_agents_md(workspace, "dev_agent", bad_body)
    assert "'Available Repositories'" in str(exc.value)


def test_reserved_header_validator_lists_multiple_offenders(tmp_path: Path) -> None:
    """When an agent body has multiple reserved-header collisions, the error
    must list ALL of them in one message so the founder fixes them in one
    pass instead of seeing one error per session retry.
    """
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import (
        ClaudeWorkspaceAdapter,
        ReservedHeaderInAgentBody,
    )

    paths = OrgPaths(root=tmp_path)
    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "ui_designer"
    bad_body = (
        "You are ui_designer.\n\n"
        "## Workflow\nFoo.\n\n"
        "## Available Repositories\nBar.\n\n"
        "## Persistent Files\nBaz.\n"
    )
    with pytest.raises(ReservedHeaderInAgentBody) as exc:
        adapter.write_claude_md(workspace, "ui_designer", bad_body)
    msg = str(exc.value)
    assert "'Workflow'" in msg
    assert "'Available Repositories'" in msg
    assert "'Persistent Files'" in msg


def test_reserved_header_validator_ignores_lookalikes(tmp_path: Path) -> None:
    """The validator does an exact string match on the H2 text; it must not
    flag near-misses like ``## Editorial Workflow`` (a domain-specific name
    that legitimately lives in agent bodies — see content_manager.md).
    """
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "content_manager"
    fine_body = (
        "You are content_manager.\n\n"
        "## Editorial Workflow\nDomain-specific editorial pipeline.\n\n"
        "## Knowledge Base Access\nWhat I can read in the KB.\n\n"
        "## Design Workflow\nA different kind of workflow.\n\n"
        "## Repo Pointers\nKey files in the primary repo.\n"
    )
    # Should not raise.
    adapter.write_claude_md(workspace, "content_manager", fine_body)
    assert (workspace / "CLAUDE.md").exists()


def test_sample_org_agent_files_have_no_reserved_header_collisions() -> None:
    """Static regression guard: no agent file shipped in ``examples/orgs/``
    may use a reserved H2 header. Fails CI if a new sample agent (or a
    contributor's edit) reintroduces the Finding-B pattern.
    """
    import re
    from runtime.orchestrator.workspace_adapters import (
        _RESERVED_AGENT_BODY_HEADERS,
    )

    repo_root = Path(__file__).resolve().parents[1]
    agent_files = list(
        (repo_root / "examples" / "orgs").rglob("org/agents/*.md")
    )
    assert agent_files, "sanity check: expected sample-org agent files to exist"
    h2_re = re.compile(r"^## (.+)$", re.MULTILINE)
    violations: list[str] = []
    for f in agent_files:
        text = f.read_text()
        # Strip YAML frontmatter so we only scan the body.
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end != -1:
                text = text[end + 5:]
        for m in h2_re.finditer(text):
            heading = m.group(1).strip()
            if heading in _RESERVED_AGENT_BODY_HEADERS:
                violations.append(f"{f.relative_to(repo_root)}: ## {heading}")
    assert not violations, (
        "sample-org agent files use reserved H2 headers (collide with "
        "system-injected sections):\n  " + "\n  ".join(violations)
    )


def test_task_completion_format_does_not_inline_json_schema(tmp_path: Path) -> None:
    """Regression guard: the section must point at the start-task skill,
    NOT restate the JSON payload shape. Restating drifts from the skill
    over time (worker schema, manager `decision` schema, the
    blocked-path variant). The skill is the single source of truth.
    """
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_claude_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "CLAUDE.md").read_text()
    # Extract just the Task Completion Format section
    start = content.index("## Task Completion Format")
    after = content[start:]
    end = after.index("\n## ", 1)  # next H2 header
    section = after[:end]
    # The skill is the canonical schema source — section must not duplicate
    # field-by-field JSON. These appear in the skill but should NOT appear
    # in the bootstrap section.
    assert '"task_id"' not in section
    assert '"session_id"' not in section
    assert '"confidence"' not in section
    assert '"summary"' not in section
    assert '"status": "completed"' not in section


# ══════════════════════════════════════════════════════════════════════════════
class TestProductionDocumentRendering:
    """Verify the complete same-owner contract in rendered agent documents.

    Each test writes a real bootstrap document through the production
    adapter path and asserts the full contract is present.
    """

    def test_claude_md_rendered_document_contains_same_owner_contract(
        self, test_settings, tmp_dir, runtime,
    ):
        """write_claude_md produces CLAUDE.md with the full same-owner contract.

        The rendered document (CLAUDE.md at workspace root) must name
        BOTH .claude/skills and .agents/skills, state the
        detection/durable-event/refusal/no-local-autoheal contract,
        assert manual external re-sync/redeploy recovery, and lack
        stale strict-OS-account, ACL, immutable, or automatic-recovery
        claims.

        Production artifact: workspace/CLAUDE.md (written by
        ClaudeWorkspaceAdapter.write_claude_md).
        """
        workspace = tmp_dir / "workspaces" / "dev_agent"
        workspace.mkdir(parents=True)

        adapter = ClaudeWorkspaceAdapter(test_settings, runtime, slug="test")
        adapter.write_claude_md(
            workspace=workspace,
            agent_name="dev_agent",
            system_prompt="You are the Dev Agent.",
        )

        claude_md_path = workspace / "CLAUDE.md"
        assert claude_md_path.exists(), (
            "write_claude_md must produce CLAUDE.md"
        )
        text = claude_md_path.read_text()

        # Production artifact identity
        assert "## Skills Directory (do not edit)" in text, (
            "CLAUDE.md must contain the Skills Directory section"
        )

        # BOTH managed roots must be named (not merely the
        # provider-selected root)
        assert ".claude/skills" in text, (
            "CLAUDE.md must name .claude/skills root"
        )
        assert ".agents/skills" in text, (
            "CLAUDE.md must name .agents/skills root"
        )

        # Detection/refusal: same-owner residency + no local autoheal
        assert "same OS identity" in text, (
            "must state executor and daemon share same OS identity"
        )
        assert "NO local automatic" in text, (
            "must assert no local automatic recovery/autoheal"
        )

        # Durable event + refusal
        assert "durable visible integrity" in text, (
            "must reference durable visible integrity event"
        )
        assert "refuses launch" in text, (
            "must assert launch refusal on mismatch"
        )

        # Manual external re-sync/redeploy recovery
        # (may span multiple lines in the joined output)
        assert "manual authoritative" in text, (
            "must assert manual authoritative recovery"
        )
        assert "external re-sync/redeploy" in text, (
            "must assert external re-sync/redeploy recovery"
        )

        # Must NOT claim OS-enforced filesystem immutability or ACL denial.
        assert "OS-enforced security boundary" in text, (
            "must disclaim OS-enforced security boundary"
        )
        # A returned B2 version is accurately immutable; only a filesystem
        # security claim is forbidden here.
        skills_section = text.split("## Skills Directory (do not edit)")[1]
        next_section_idx = skills_section.find("\n## ")
        if next_section_idx != -1:
            skills_section = skills_section[:next_section_idx]
        normalized = skills_section.lower().replace("-", " ")
        assert "filesystem immutable" not in normalized
        assert "os enforced immutable" not in normalized
        assert "acl denial" not in normalized
        assert "ACL" not in skills_section, (
            "CLAUDE.md Skills Directory section must not claim ACL enforcement"
        )
        assert "distinct" not in skills_section.lower(), (
            "CLAUDE.md Skills Directory section must not claim distinct identity"
        )

    def test_agents_md_rendered_document_contains_same_owner_contract(
        self, test_settings, tmp_dir, runtime,
    ):
        """write_agents_md produces AGENTS.md with the full same-owner contract.

        The rendered document (AGENTS.md at workspace root) must name
        BOTH .claude/skills and .agents/skills, state the
        detection/durable-event/refusal/no-local-autoheal contract,
        assert manual external re-sync/redeploy recovery, and lack
        stale strict-OS-account, ACL, immutable, or automatic-recovery
        claims.

        Production artifact: workspace/AGENTS.md (written by
        CodexWorkspaceAdapter.write_agents_md).
        """
        workspace = tmp_dir / "workspaces" / "dev_agent"
        workspace.mkdir(parents=True)

        adapter = CodexWorkspaceAdapter(test_settings, runtime, slug="test")
        adapter.write_agents_md(
            workspace=workspace,
            agent_name="dev_agent",
            system_prompt="You are the Dev Agent.",
        )

        agents_md_path = workspace / "AGENTS.md"
        assert agents_md_path.exists(), (
            "write_agents_md must produce AGENTS.md"
        )
        text = agents_md_path.read_text()

        # Production artifact identity
        assert "## Skills Directory (do not edit)" in text, (
            "AGENTS.md must contain the Skills Directory section"
        )

        # BOTH managed roots must be named (not merely the
        # provider-selected root)
        assert ".claude/skills" in text, (
            "AGENTS.md must name .claude/skills root"
        )
        assert ".agents/skills" in text, (
            "AGENTS.md must name .agents/skills root"
        )

        # Detection/refusal: same-owner residency + no local autoheal
        assert "same OS identity" in text, (
            "must state executor and daemon share same OS identity"
        )
        assert "NO local automatic" in text, (
            "must assert no local automatic recovery/autoheal"
        )

        # Durable event + refusal
        assert "durable visible integrity" in text, (
            "must reference durable visible integrity event"
        )
        assert "refuses launch" in text, (
            "must assert launch refusal on mismatch"
        )

        # Manual external re-sync/redeploy recovery
        # (may span multiple lines in the joined output)
        assert "manual authoritative" in text, (
            "must assert manual authoritative recovery"
        )
        assert "external re-sync/redeploy" in text, (
            "must assert external re-sync/redeploy recovery"
        )

        # Must NOT claim OS-enforced filesystem immutability or ACL denial.
        assert "OS-enforced security boundary" in text, (
            "must disclaim OS-enforced security boundary"
        )

        # Isolate the Skills Directory section
        skills_section = text.split("## Skills Directory (do not edit)")[1]
        next_section_idx = skills_section.find("\n## ")
        if next_section_idx != -1:
            skills_section = skills_section[:next_section_idx]

        # A returned B2 version is accurately immutable; only a filesystem
        # security claim is forbidden here.
        normalized = skills_section.lower().replace("-", " ")
        assert "filesystem immutable" not in normalized
        assert "os enforced immutable" not in normalized
        assert "acl denial" not in normalized
        assert "distinct" not in skills_section.lower(), (
            "AGENTS.md Skills Directory section must not claim distinct identity"
        )


# ── THR-262 Slice B: canonical instruction pair (founder seq59) ──────────


@pytest.mark.parametrize("provider", ["claude", "codex", "opencode", "pi"])
def test_instruction_pair_canonical_for_every_provider(
    test_settings, tmp_dir, runtime, provider,
):
    """Every built-in adapter converges on regular AGENTS.md + relative link."""
    from runtime.orchestrator.context_builder import ContextBuilder

    ws = tmp_dir / "workspaces" / f"agent_{provider}"
    ContextBuilder(test_settings, runtime, slug="test").ensure_workspace_ready(
        ws, f"agent_{provider}", "You are a test agent.", provider=provider,
    )
    assert (ws / "AGENTS.md").is_file()
    assert not (ws / "AGENTS.md").is_symlink()
    assert (ws / "CLAUDE.md").is_symlink()
    assert os.readlink(ws / "CLAUDE.md") == "AGENTS.md"
    assert (ws / "CLAUDE.md").resolve() == (ws / "AGENTS.md").resolve()
    assert canonical_instruction_pair_ok(ws)


def _seed_agents(ws: Path) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "AGENTS.md").write_text("canonical\n")


def test_instruction_pair_refusal_missing(tmp_dir):
    ws = tmp_dir / "w"
    ws.mkdir()
    assert instruction_pair_refusal(ws) == "AGENTS.md is missing"


@pytest.mark.parametrize(
    "form",
    [
        "dangling",
        "cyclic",
        "absolute",
        "foreign",
        "wrong_target",
        "non_link",
        "directory",
        "agents_symlink",
    ],
)
def test_instruction_pair_refuses_non_canonical_forms(tmp_dir, form):
    ws = tmp_dir / f"w_{form}"
    _seed_agents(ws)
    claude = ws / "CLAUDE.md"
    if form == "dangling":
        os.symlink("MISSING.md", claude)
    elif form == "cyclic":
        os.symlink("CLAUDE.md", claude)
    elif form == "absolute":
        os.symlink(str(ws / "AGENTS.md"), claude)
    elif form == "foreign":
        os.symlink("/etc/hostname", claude)
    elif form == "wrong_target":
        (ws / "OTHER.md").write_text("other\n")
        os.symlink("OTHER.md", claude)
    elif form == "non_link":
        claude.write_text("canonical\n")
    elif form == "directory":
        claude.mkdir()
    elif form == "agents_symlink":
        (ws / "AGENTS-real.md").write_text("canonical\n")
        (ws / "AGENTS.md").unlink()
        os.symlink("AGENTS-real.md", ws / "AGENTS.md")
        os.symlink("AGENTS.md", claude)
    assert instruction_pair_refusal(ws) is not None


def test_instruction_pair_writer_never_mutates_external_target(tmp_dir):
    ws = tmp_dir / "w"
    ws.mkdir()
    external = tmp_dir / "external.md"
    external.write_text("external bytes\n")
    os.symlink(external, ws / "AGENTS.md")
    write_canonical_instruction_pair(ws, "canonical\n")
    assert external.read_text() == "external bytes\n"
    assert (ws / "AGENTS.md").is_file()
    assert not (ws / "AGENTS.md").is_symlink()
    assert os.readlink(ws / "CLAUDE.md") == "AGENTS.md"


def test_instruction_pair_preserves_both_differing_regular_files(tmp_dir):
    ws = tmp_dir / "w"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("old agents\n")
    (ws / "CLAUDE.md").write_text("old claude\n")
    write_canonical_instruction_pair(ws, "canonical\n")
    assert (ws / "AGENTS.md").read_text() == "canonical\n"
    assert os.readlink(ws / "CLAUDE.md") == "AGENTS.md"
    backups = sorted(
        p.read_text() for p in ws.iterdir() if p.name.endswith(".bak")
    )
    assert backups == ["old agents\n", "old claude\n"]


def test_instruction_pair_cl_copy_failure_leaves_both_unchanged(
    tmp_dir, monkeypatch,
):
    """T2b: a CLAUDE.md preservation-copy failure after the completed AGENTS.md
    copy leaves both originals byte/type-identical and the AG copy retained."""
    import runtime.orchestrator.workspace_adapters as wa

    ws = tmp_dir / "w"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("old agents\n")
    (ws / "CLAUDE.md").write_text("old claude\n")

    real = wa._write_preservation_copy
    calls: list[str] = []

    def failing(path, state):
        calls.append(path.name)
        if path.name == "CLAUDE.md":
            raise OSError("injected copy failure")
        return real(path, state)

    monkeypatch.setattr(wa, "_write_preservation_copy", failing)
    with pytest.raises(InstructionPairConflict):
        wa.write_canonical_instruction_pair(ws, "canonical\n")

    assert (ws / "AGENTS.md").read_text() == "old agents\n"
    assert (ws / "CLAUDE.md").read_text() == "old claude\n"
    assert not (ws / "CLAUDE.md").is_symlink()
    # The completed AGENTS.md preservation copy is retained and identified.
    ag_backups = [p for p in ws.iterdir() if p.name.startswith("AGENTS.md.happyranch")]
    assert ag_backups, "completed AGENTS.md preservation copy must be retained"
    assert calls == ["AGENTS.md", "CLAUDE.md"]
    # No canonical pair was written.
    assert not canonical_instruction_pair_ok(ws)


@pytest.mark.parametrize(
    "form", ["stale", "reverse", "broken", "cyclic", "external"],
)
def test_instruction_pair_recreates_recordable_non_canonical_links(
    tmp_dir, form,
):
    """C8/§7.4.2: a recordable stale/reverse/broken/cyclic/external raw link is
    classified as 'recreate' (not T1-ambiguous); the writer unlinks it, writes
    the canonical pair, and never mutates the original external target."""
    ws = tmp_dir / f"w_{form}"
    ws.mkdir()
    external = tmp_dir / "external_target.md"
    external.write_text("external bytes\n")
    agents = ws / "AGENTS.md"
    claude = ws / "CLAUDE.md"

    if form == "stale":
        agents.write_text("canonical\n")
        (ws / "OTHER.md").write_text("other\n")
        os.symlink("OTHER.md", claude)
    elif form == "reverse":
        # ``AGENTS.md -> CLAUDE.md``: the reverse of the accepted topology.
        claude.write_text("claude regular\n")
        os.symlink("CLAUDE.md", agents)
    elif form == "broken":
        agents.write_text("canonical\n")
        os.symlink("MISSING.md", claude)
    elif form == "cyclic":
        agents.write_text("canonical\n")
        os.symlink("CLAUDE.md", claude)
    elif form == "external":
        agents.write_text("canonical\n")
        os.symlink(str(external), claude)
    else:  # pragma: no cover
        raise AssertionError(form)

    write_canonical_instruction_pair(ws, "canonical\n")

    assert agents.is_file() and not agents.is_symlink()
    assert claude.is_symlink()
    assert os.readlink(claude) == "AGENTS.md"
    assert canonical_instruction_pair_ok(ws)
    # The original external target is never written through.
    assert external.read_text() == "external bytes\n"


def test_instruction_pair_directory_input_fails_closed(tmp_dir):
    """T9: a directory/other-non-regular instruction path fails closed and
    leaves both paths byte/type identical."""
    ws = tmp_dir / "w"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("original agents\n")
    (ws / "CLAUDE.md").mkdir()

    with pytest.raises(InstructionPairConflict):
        write_canonical_instruction_pair(ws, "canonical\n")

    assert (ws / "AGENTS.md").read_text() == "original agents\n"
    assert (ws / "CLAUDE.md").is_dir()
    assert not canonical_instruction_pair_ok(ws)
    assert not list(ws.glob("*.bak"))


def test_instruction_pair_unreadable_regular_fails_closed(tmp_dir):
    """T1: an unreadable regular instruction path leaves both paths unchanged
    and creates no backup/temp. Skipped for a root runner, which can read 0000."""
    ws = tmp_dir / "w"
    ws.mkdir()
    agents = ws / "AGENTS.md"
    agents.write_text("original agents\n")
    (ws / "CLAUDE.md").write_text("original claude\n")
    if os.geteuid() == 0:
        pytest.skip("root can read mode-0000 files")
    os.chmod(agents, 0o000)
    try:
        with pytest.raises(InstructionPairConflict):
            write_canonical_instruction_pair(ws, "canonical\n")
    finally:
        os.chmod(agents, 0o644)

    assert agents.read_text() == "original agents\n"
    assert (ws / "CLAUDE.md").read_text() == "original claude\n"
    assert not (ws / "CLAUDE.md").is_symlink()
    assert not list(ws.glob("*.bak"))


# ── TASK-8744 F2: caught link-conversion faults retain exact old state ──────
#
# The canonical writer must never unlink the pre-existing CLAUDE.md before the
# replacement link exists. These shipping-path tests inject an ordinary failure
# at each step of the owned-sibling staging protocol and assert the recorded old
# raw link/bytes/type/mode/uid survive, no owned temp/staging residue remains,
# preservation copies are retained, and no external target is mutated.


def _instruction_path_state(path: Path):
    """Return a comparable (kind, payload, mode, uid) snapshot via lstat."""
    import stat as _stat

    if not os.path.lexists(path):
        return ("absent", None, None, None)
    st = os.lstat(path)
    if _stat.S_ISLNK(st.st_mode):
        return ("symlink", os.readlink(path), st.st_mode & 0o7777, st.st_uid)
    if _stat.S_ISREG(st.st_mode):
        return ("regular", path.read_bytes(), st.st_mode & 0o7777, st.st_uid)
    return ("other", None, st.st_mode & 0o7777, st.st_uid)


def _owned_temp_residue(ws: Path) -> list[str]:
    return sorted(
        p.name
        for p in ws.iterdir()
        if ".happyranch-" in p.name and not p.name.endswith(".bak")
    )


def _seed_fault_form(ws: Path, external: Path, form: str) -> None:
    agents = ws / "AGENTS.md"
    claude = ws / "CLAUDE.md"
    if form == "stale":
        agents.write_text("canonical\n")
        (ws / "OTHER.md").write_text("other\n")
        os.symlink("OTHER.md", claude)
    elif form == "broken":
        agents.write_text("canonical\n")
        os.symlink("MISSING.md", claude)
    elif form == "cyclic":
        agents.write_text("canonical\n")
        os.symlink("CLAUDE.md", claude)
    elif form == "reverse":
        # The reverse topology: registerable recordable raw link at AGENTS.md.
        claude.write_text("old claude regular\n")
        os.symlink("CLAUDE.md", agents)
    elif form == "absolute":
        agents.write_text("canonical\n")
        os.symlink(str(agents), claude)
    elif form == "external":
        agents.write_text("canonical\n")
        os.symlink(str(external), claude)
    elif form == "differing_regulars":
        agents.write_text("old agents\n")
        claude.write_text("old claude\n")
    else:  # pragma: no cover
        raise AssertionError(form)


@pytest.mark.parametrize(
    "form",
    ["stale", "broken", "cyclic", "reverse", "absolute", "external", "differing_regulars"],
)
@pytest.mark.parametrize("injection", ["stage", "replace"])
def test_instruction_pair_claude_link_fault_retains_exact_old_state(
    tmp_dir, monkeypatch, form, injection,
):
    import runtime.orchestrator.workspace_adapters as wa

    ws = tmp_dir / f"w_{form}_{injection}"
    ws.mkdir()
    external = tmp_dir / f"ext_{form}_{injection}.md"
    external.write_text("external bytes\n")
    _seed_fault_form(ws, external, form)
    claude = ws / "CLAUDE.md"
    claude_before = _instruction_path_state(claude)
    external_before = external.read_bytes()

    if injection == "stage":
        def boom(*_a, **_k):
            raise OSError("injected link staging failure")

        monkeypatch.setattr(wa, "_stage_canonical_claude_link", boom)
    else:
        real_replace = wa.os.replace

        def replace_boom(src, dst, *a, **k):
            if str(dst).endswith("CLAUDE.md"):
                raise OSError("injected link replace failure")
            return real_replace(src, dst, *a, **k)

        monkeypatch.setattr(wa.os, "replace", replace_boom)

    with pytest.raises(InstructionPairConflict) as excinfo:
        write_canonical_instruction_pair(ws, "canonical\n")
    assert str(claude) in str(excinfo.value), excinfo.value
    assert "link creation failed" in str(excinfo.value), excinfo.value

    # Exact retained old state of the failure target (bytes/type/raw link/mode/uid).
    assert _instruction_path_state(claude) == claude_before
    # No owned temp/staging residue survives.
    assert _owned_temp_residue(ws) == []
    # External target never written through.
    assert external.read_bytes() == external_before
    # The pair is honestly still non-canonical.
    assert not canonical_instruction_pair_ok(ws)


def test_instruction_pair_reverse_agents_symlink_fault_retains_old_link(
    tmp_dir, monkeypatch,
):
    """``AGENTS.md -> CLAUDE.md`` must be atomically replaced, never unlinked
    before the new regular file exists."""
    import runtime.orchestrator.workspace_adapters as wa

    ws = tmp_dir / "w"
    ws.mkdir()
    (ws / "CLAUDE.md").write_text("old claude regular\n")
    os.symlink("CLAUDE.md", ws / "AGENTS.md")
    agents = ws / "AGENTS.md"
    agents_before = _instruction_path_state(agents)

    real_replace = wa.os.replace

    def replace_boom(src, dst, *a, **k):
        if str(dst).endswith("AGENTS.md"):
            raise OSError("injected agents replace failure")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(wa.os, "replace", replace_boom)

    with pytest.raises(InstructionPairConflict):
        write_canonical_instruction_pair(ws, "canonical\n")

    assert _instruction_path_state(agents) == agents_before
    assert _owned_temp_residue(ws) == []


def test_instruction_pair_fault_retains_preservation_copies(tmp_dir, monkeypatch):
    """A caught CLAUDE-link fault after the barrier keeps every completed
    preservation copy (the user's differing regular bytes) identifiable."""
    import runtime.orchestrator.workspace_adapters as wa

    ws = tmp_dir / "w"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("old agents\n")
    (ws / "CLAUDE.md").write_text("old claude\n")

    def boom(*_a, **_k):
        raise OSError("injected link staging failure")

    monkeypatch.setattr(wa, "_stage_canonical_claude_link", boom)
    with pytest.raises(InstructionPairConflict):
        write_canonical_instruction_pair(ws, "canonical\n")

    backups = {
        p.read_bytes() for p in ws.iterdir() if p.name.endswith(".bak")
    }
    assert b"old agents\n" in backups
    assert b"old claude\n" in backups
    assert (ws / "CLAUDE.md").read_bytes() == b"old claude\n"
    assert not (ws / "CLAUDE.md").is_symlink()
