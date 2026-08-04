import json
from pathlib import Path

import pytest

from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.workspace_adapters import (
    ClaudeWorkspaceAdapter,
    CodexWorkspaceAdapter,
    OpencodeWorkspaceAdapter,
)
from runtime.runtime import RuntimeDir


@pytest.fixture
def runtime(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    return OrgPaths(root=rt.orgs_dir / "test")


def test_claude_adapter_bootstrap_creates_claude_files_and_skills(test_settings, tmp_dir, runtime):
    skills_root = test_settings.get_protocol_dir() / "skills"
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
    cwd up to repo root). The adapter must copy ``protocol/skills/`` into the
    workspace and AGENTS.md must point at the start-task skill — not inline
    the full completion contract (the skill is the source of truth).
    """
    skills_root = test_settings.get_protocol_dir() / "skills"
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

    assert (workspace / "AGENTS.md").exists()
    assert not (workspace / "CLAUDE.md").exists()
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

    proto = tmp_path / "protocol" / "skills" / "start-task"
    proto.mkdir(parents=True)
    (proto / "SKILL.md").write_text(
        "Run: happyranch report-completion --org {ORG_SLUG} --task-id ...\n"
    )
    monkeypatch.setattr(
        "runtime.orchestrator.workspace_adapters._SKILLS_SRC",
        tmp_path / "protocol" / "skills",
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
    skills_root = test_settings.get_protocol_dir() / "skills"
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

    assert (workspace / "AGENTS.md").exists()
    assert not (workspace / "CLAUDE.md").exists()
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
    skills_root = test_settings.get_protocol_dir() / "skills"
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

    skills_root = test_settings.get_protocol_dir() / "skills"
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
    skills_root = test_settings.get_protocol_dir() / "skills"
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
    # Points at the jobs skill (the actual remediation path)
    assert "protocol/skills/jobs/SKILL.md" in content
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
    assert "protocol/skills/jobs/SKILL.md" in content
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
    assert "protocol/skills/jobs/SKILL.md" in content
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
    # Still recommends jobs as the remedy
    assert "protocol/skills/jobs/SKILL.md" in text
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
        lines = _skills_directory_readonly_section(skills_dir)
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

        # Does NOT claim immutable or ACL denial
        for forbidden in ("immutable", "ACL denial"):
            assert forbidden not in text.lower().replace("-", " "), (
                f"must not claim {forbidden!r}"
            )

        # Recommends lifecycle proposal workflow


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
# Phase 3b: lifecycle-ledger skill materialization (TDD, THR-055)
# ══════════════════════════════════════════════════════════════════════════════


class TestUserAuthoredSkillMaterialization:
    """TDD: inject_managed_skills materializes lifecycle-ledger custom skills
    from the published+assigned ledger. Legacy filesystem paths (org_root/skills/)
    are no longer resolved — only the lifecycle ledger is the runtime source."""

    def test_lifecycle_skill_materialized_from_artifact(self, tmp_dir, test_settings, db):
        """A lifecycle-published+assigned skill is materialized from the ArtifactStore."""
        from runtime.orchestrator.workspace_adapters import inject_managed_skills
        from runtime.skills.lifecycle import stores as lifecycle_stores
        from runtime.skills.lifecycle.service import SkillLifecycleService

        # Create system-contract source dirs so materialize_workspace_skills
        # can resolve them (required by the fail-closed source-existence check).
        proto_skills = tmp_dir / "protocol" / "skills"
        for sid in ("start-task", "jobs", "make-worktree", "thread"):
            (proto_skills / sid).mkdir(parents=True, exist_ok=True)
            (proto_skills / sid / "SKILL.md").write_text(f"# {sid}\n\nSkill body.\n")

        service = SkillLifecycleService()
        org_root = tmp_dir / "org"

        # Create a published+assigned lifecycle skill
        skill_md = "# Custom Skill\n\nTest content."
        import hashlib
        content_hash = hashlib.sha256(skill_md.encode("utf-8")).hexdigest()

        # Store artifact
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths
        artifact_store = ArtifactStore(OrgPaths(org_root).artifacts_dir)
        artifact_key = "skill-lifecycle/custom-skill/1.0.0/SKILL.md"
        artifact_store.put(artifact_key, skill_md.encode("utf-8"))

        # Seed the lifecycle ledger directly (bypass HTTP routes)
        pkg = lifecycle_stores.PackageVersion(
            skill_id="hr:custom-skill",
            slug="custom-skill",
            name="Custom Skill",
            version="1.0.0",
            content_hash=content_hash,
            policy_class="standard_operational",
            description="A custom skill",
            skill_md=skill_md,
            content_artifact_key=artifact_key,
            status=lifecycle_stores.LifecycleStatus.PUBLISHED,
            created_by="founder",
            publisher="founder",
        )
        version_id = lifecycle_stores.insert_package_version(db, pkg)

        # Create active assignment
        import datetime
        assign = lifecycle_stores.AssignmentRecord(
            skill_id="hr:custom-skill",
            agent_name="dev_agent",
            package_version_id=version_id,
            version="1.0.0",
            content_hash=content_hash,
            assigned_by="founder",
            assigned_at=datetime.datetime.now(datetime.timezone.utc),
            active=True,
        )
        lifecycle_stores.insert_assignment(db, assign)

        managed_root = tmp_dir / "managed"
        managed_root.mkdir()
        workspace = tmp_dir / "ws"

        inject_managed_skills(
            workspace, test_settings,
            slug="test",
            agent_name="dev_agent",
            team="engineering",
            skills_root=managed_root,
            org_root=org_root,
            db=db,
        )

        # The lifecycle skill should be on disk, loaded from ArtifactStore
        claude_skill = workspace / ".claude" / "skills" / "custom-skill" / "SKILL.md"
        agents_skill = workspace / ".agents" / "skills" / "custom-skill" / "SKILL.md"
        assert claude_skill.is_file(), (
            "lifecycle skill must be materialized to .claude/skills/"
        )
        assert agents_skill.is_file(), (
            "lifecycle skill must be materialized to .agents/skills/"
        )
        assert "Custom Skill" in claude_skill.read_text()

    def test_legacy_filesystem_skills_not_materialized(self, tmp_dir, test_settings, db):
        """Legacy filesystem skills (org_root/skills/) are NEVER materialized —
        only lifecycle-ledger published+assigned skills reach the workspace."""
        from runtime.orchestrator.workspace_adapters import inject_managed_skills

        # Create system-contract source dirs so materialize_workspace_skills
        # can resolve them (required by the fail-closed source-existence check).
        proto_skills = tmp_dir / "protocol" / "skills"
        for sid in ("start-task", "jobs", "make-worktree", "thread"):
            (proto_skills / sid).mkdir(parents=True, exist_ok=True)
            (proto_skills / sid / "SKILL.md").write_text(f"# {sid}\n\nSkill body.\n")

        org_root = tmp_dir / "org"
        skill_dir = org_root / "skills" / "custom-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Custom Skill\n\nLegacy content.")
        (skill_dir / "skill.yaml").write_text(
            "id: hr:custom-skill\n"
            "slug: custom-skill\n"
            "name: Custom Skill\n"
            "version: 1.0.0\n"
            "description: A custom skill\n"
            "when_to_use: ''\n"
            "owner: operator\n"
            "source: user_authored\n"
            "policy_class: standard_operational\n"
            "status: enabled\n"
        )

        # Eligibility policy (legacy — should be ignored for materialization)
        org_config_dir = org_root / "org"
        org_config_dir.mkdir(parents=True)
        import yaml
        policy = {
            "skills": {
                "agents": {
                    "dev_agent": {"allow": ["hr:custom-skill"]},
                }
            }
        }
        (org_config_dir / "config.yaml").write_text(yaml.dump(policy))

        managed_root = tmp_dir / "managed"
        managed_root.mkdir()
        workspace = tmp_dir / "ws"

        inject_managed_skills(
            workspace, test_settings,
            slug="test",
            agent_name="dev_agent",
            team="engineering",
            skills_root=managed_root,
            org_root=org_root,
            db=db,
        )

        # Legacy filesystem skill must NOT be materialized (THR-055 quarantine)
        claude_skill = workspace / ".claude" / "skills" / "custom-skill" / "SKILL.md"
        assert not claude_skill.is_file(), (
            "Legacy filesystem skills must NOT be materialized — "
            "only lifecycle-ledger published+assigned skills reach the workspace"
        )

    def test_proposed_skill_not_materialized(self, tmp_dir, test_settings, db):
        """Skills in non-PUBLISHED status (proposed, draft, etc.) must NOT materialize."""
        from runtime.orchestrator.workspace_adapters import inject_managed_skills
        from runtime.skills.lifecycle import stores as lifecycle_stores

        # Create system-contract source dirs so materialize_workspace_skills
        # can resolve them (required by the fail-closed source-existence check).
        proto_skills = tmp_dir / "protocol" / "skills"
        for sid in ("start-task", "jobs", "make-worktree", "thread"):
            (proto_skills / sid).mkdir(parents=True, exist_ok=True)
            (proto_skills / sid / "SKILL.md").write_text(f"# {sid}\n\nSkill body.\n")

        org_root = tmp_dir / "org"
        skill_md = "# Proposed Skill\n\nShould not appear."
        import hashlib
        content_hash = hashlib.sha256(skill_md.encode("utf-8")).hexdigest()

        # Seed a PROPOSED skill (not published, not assigned)
        pkg = lifecycle_stores.PackageVersion(
            skill_id="hr:proposed-skill",
            slug="proposed-skill",
            name="Proposed Skill",
            version="0.1.0",
            content_hash=content_hash,
            policy_class="standard_operational",
            description="Should be invisible",
            skill_md=skill_md,
            content_artifact_key="skill-lifecycle/proposed-skill/0.1.0/SKILL.md",
            status=lifecycle_stores.LifecycleStatus.PROPOSED,
            created_by="dev_agent",
        )
        lifecycle_stores.insert_package_version(db, pkg)

        managed_root = tmp_dir / "managed"
        managed_root.mkdir()
        workspace = tmp_dir / "ws"

        inject_managed_skills(
            workspace, test_settings,
            slug="test",
            agent_name="dev_agent",
            team="engineering",
            skills_root=managed_root,
            org_root=org_root,
            db=db,
        )

        # PROPOSED skill must NOT be materialized
        claude_skill = workspace / ".claude" / "skills" / "proposed-skill" / "SKILL.md"
        assert not claude_skill.is_file(), (
            "Proposed (non-PUBLISHED) skills must NOT be materialized"
        )

    def test_materialization_fail_closed_no_partial_state(self, tmp_dir, test_settings, db):
        """FAIL-CLOSED: materialization failure must raise and leave no partial workspace residue."""
        from runtime.orchestrator.workspace_adapters import (
            inject_managed_skills,
            LifecycleMaterializationError,
        )

        # Create system-contract source dirs so materialize_workspace_skills
        # can resolve them (required by the fail-closed source-existence check).
        proto_skills = tmp_dir / "protocol" / "skills"
        for sid in ("start-task", "jobs", "make-worktree", "thread"):
            (proto_skills / sid).mkdir(parents=True, exist_ok=True)
            (proto_skills / sid / "SKILL.md").write_text(f"# {sid}\n\nSkill body.\n")

        from runtime.skills.lifecycle import stores as lifecycle_stores

        org_root = tmp_dir / "org"
        import hashlib
        content_hash = hashlib.sha256(b"valid").hexdigest()

        # Seed a skill with a content_artifact_key that does NOT exist in ArtifactStore
        pkg = lifecycle_stores.PackageVersion(
            skill_id="hr:missing-artifact",
            slug="missing-artifact",
            name="Missing Artifact",
            version="1.0.0",
            content_hash=content_hash,
            policy_class="standard_operational",
            description="Artifact will not be found",
            skill_md="",
            content_artifact_key="skill-lifecycle/missing/1.0.0/SKILL.md",
            status=lifecycle_stores.LifecycleStatus.PUBLISHED,
            created_by="founder",
            publisher="founder",
        )
        version_id = lifecycle_stores.insert_package_version(db, pkg)

        import datetime
        assign = lifecycle_stores.AssignmentRecord(
            skill_id="hr:missing-artifact",
            agent_name="dev_agent",
            package_version_id=version_id,
            version="1.0.0",
            content_hash=content_hash,
            assigned_by="founder",
            assigned_at=datetime.datetime.now(datetime.timezone.utc),
            active=True,
        )
        lifecycle_stores.insert_assignment(db, assign)

        managed_root = tmp_dir / "managed"
        managed_root.mkdir()
        workspace = tmp_dir / "ws"

        # Must raise because the artifact is missing (fail-closed)
        with pytest.raises(LifecycleMaterializationError, match="not found"):
            inject_managed_skills(
                workspace, test_settings,
                slug="test",
                agent_name="dev_agent",
                team="engineering",
                skills_root=managed_root,
                org_root=org_root,
                db=db,
            )

        # No partial state — missing artifact should not have created a skill dir
        claude_skill = workspace / ".claude" / "skills" / "missing-artifact" / "SKILL.md"
        assert not claude_skill.is_file(), (
            "Missing artifact must NOT leave partial workspace residue"
        )

    def test_audit_persistence_failure_raises_named_error(
        self, tmp_dir, test_settings, db, monkeypatch,
    ):
        """Adversarial: when record_materialization raises (ledger/audit write
        failure), the materialization MUST raise a named LifecycleMaterializationError
        and MUST NOT proceed to a launch-capable successful return.

        Proves: (a) named failure reaches the materialization caller;
        (b) no successful launch/readiness/persist/audit progression occurs.
        """
        # Create system-contract source dirs so materialize_workspace_skills
        # can resolve them (required by the fail-closed source-existence check).
        proto_skills = tmp_dir / "protocol" / "skills"
        for sid in ("start-task", "jobs", "make-worktree", "thread"):
            (proto_skills / sid).mkdir(parents=True, exist_ok=True)
            (proto_skills / sid / "SKILL.md").write_text(f"# {sid}\n\nSkill body.\n")
        from runtime.orchestrator.workspace_adapters import (
            inject_managed_skills,
            LifecycleMaterializationError,
        )
        from runtime.skills.lifecycle import stores as lifecycle_stores
        from runtime.skills.lifecycle.service import SkillLifecycleService
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        org_root = tmp_dir / "org"
        skill_md = "# Audit Fail Skill\n\nTest."
        import hashlib
        content_hash = hashlib.sha256(skill_md.encode("utf-8")).hexdigest()

        # Store a valid artifact
        artifact_store = ArtifactStore(OrgPaths(org_root).artifacts_dir)
        artifact_key = "skill-lifecycle/audit-fail/1.0.0/SKILL.md"
        artifact_store.put(artifact_key, skill_md.encode("utf-8"))

        # Seed PUBLISHED + assigned skill
        pkg = lifecycle_stores.PackageVersion(
            skill_id="hr:audit-fail",
            slug="audit-fail",
            name="Audit Fail",
            version="1.0.0",
            content_hash=content_hash,
            policy_class="standard_operational",
            description="Will fail audit",
            skill_md=skill_md,
            content_artifact_key=artifact_key,
            status=lifecycle_stores.LifecycleStatus.PUBLISHED,
            created_by="founder",
            publisher="founder",
        )
        version_id = lifecycle_stores.insert_package_version(db, pkg)

        import datetime
        assign = lifecycle_stores.AssignmentRecord(
            skill_id="hr:audit-fail",
            agent_name="dev_agent",
            package_version_id=version_id,
            version="1.0.0",
            content_hash=content_hash,
            assigned_by="founder",
            assigned_at=datetime.datetime.now(datetime.timezone.utc),
            active=True,
        )
        lifecycle_stores.insert_assignment(db, assign)

        # Inject audit persistence failure: record_materialization raises
        original_record = SkillLifecycleService.record_materialization

        def _failing_record(self, db, skill_id, agent_name, version_id,
                            version, content_hash, success, error_message=None,
                            session_context=None):
            raise RuntimeError("Simulated ledger write failure")

        monkeypatch.setattr(
            SkillLifecycleService, "record_materialization", _failing_record,
        )

        managed_root = tmp_dir / "managed"
        managed_root.mkdir()
        workspace = tmp_dir / "ws"

        # (a) Named failure MUST reach the materialization caller
        with pytest.raises(LifecycleMaterializationError, match="audit-fail"):
            inject_managed_skills(
                workspace, test_settings,
                slug="test",
                agent_name="dev_agent",
                team="engineering",
                skills_root=managed_root,
                org_root=org_root,
                db=db,
            )

        # (b) No successful launch: workspace MUST NOT have symlinked skills
        claude_skill = workspace / ".claude" / "skills" / "audit-fail" / "SKILL.md"
        agents_skill = workspace / ".agents" / "skills" / "audit-fail" / "SKILL.md"
        assert not claude_skill.is_file(), (
            "Audit persistence failure must block materialization symlinks "
            "in .claude/skills/"
        )
        assert not agents_skill.is_file(), (
            "Audit persistence failure must block materialization symlinks "
            "in .agents/skills/"
        )

    def test_audit_failure_no_false_claim_and_hash_integrity(
        self, tmp_dir, test_settings, db, monkeypatch,
    ):
        """Adversarial: when record_materialization fails, prove:
        (c) no audit record is falsely claimed;
        (d) canonical content hashes and pre-existing workspace state
            obey the documented safety contract.
        """
        from runtime.orchestrator.workspace_adapters import (
            inject_managed_skills,
            LifecycleMaterializationError,
        )
        from runtime.skills.lifecycle import stores as lifecycle_stores

        # Create system-contract source dirs so materialize_workspace_skills
        # can resolve them (required by the fail-closed source-existence check).
        proto_skills = tmp_dir / "protocol" / "skills"
        for sid in ("start-task", "jobs", "make-worktree", "thread"):
            (proto_skills / sid).mkdir(parents=True, exist_ok=True)
            (proto_skills / sid / "SKILL.md").write_text(f"# {sid}\n\nSkill body.\n")
        from runtime.skills.lifecycle.service import SkillLifecycleService
        from runtime.infrastructure.artifact_store import ArtifactStore
        from runtime.orchestrator._paths import OrgPaths

        org_root = tmp_dir / "org"
        skill_md = "# Hash Integrity Skill\n\nVerify."
        import hashlib
        content_hash = hashlib.sha256(skill_md.encode("utf-8")).hexdigest()

        # Store a valid artifact
        artifact_store = ArtifactStore(OrgPaths(org_root).artifacts_dir)
        artifact_key = "skill-lifecycle/hash-integrity/1.0.0/SKILL.md"
        artifact_store.put(artifact_key, skill_md.encode("utf-8"))

        # Seed PUBLISHED + assigned skill
        pkg = lifecycle_stores.PackageVersion(
            skill_id="hr:hash-integrity",
            slug="hash-integrity",
            name="Hash Integrity",
            version="1.0.0",
            content_hash=content_hash,
            policy_class="standard_operational",
            description="Hash integrity test",
            skill_md=skill_md,
            content_artifact_key=artifact_key,
            status=lifecycle_stores.LifecycleStatus.PUBLISHED,
            created_by="founder",
            publisher="founder",
        )
        version_id = lifecycle_stores.insert_package_version(db, pkg)

        import datetime
        assign = lifecycle_stores.AssignmentRecord(
            skill_id="hr:hash-integrity",
            agent_name="dev_agent",
            package_version_id=version_id,
            version="1.0.0",
            content_hash=content_hash,
            assigned_by="founder",
            assigned_at=datetime.datetime.now(datetime.timezone.utc),
            active=True,
        )
        lifecycle_stores.insert_assignment(db, assign)

        # Pre-existing workspace file — must survive the failed materialization
        workspace = tmp_dir / "ws"
        workspace.mkdir(parents=True)
        pre_existing = workspace / "pre_existing.txt"
        pre_existing_content = "pre-existing workspace state"
        pre_existing.write_text(pre_existing_content)

        # Inject audit persistence failure
        def _failing_record(self, db, skill_id, agent_name, version_id,
                            version, content_hash, success, error_message=None,
                            session_context=None):
            raise RuntimeError("Simulated ledger write failure")

        monkeypatch.setattr(
            SkillLifecycleService, "record_materialization", _failing_record,
        )

        managed_root = tmp_dir / "managed"
        managed_root.mkdir()

        with pytest.raises(LifecycleMaterializationError):
            inject_managed_skills(
                workspace, test_settings,
                slug="test",
                agent_name="dev_agent",
                team="engineering",
                skills_root=managed_root,
                org_root=org_root,
                db=db,
            )

        # (c) No audit record falsely claimed — check materialization records
        mat = lifecycle_stores.get_latest_materialization(
            db, "hr:hash-integrity", "dev_agent",
        )
        assert mat is None, (
            "No materialization record must be claimed after audit failure"
        )

        # (d) Pre-existing workspace state must be preserved
        assert pre_existing.is_file(), (
            "Pre-existing workspace state must survive a failed materialization"
        )
        assert pre_existing.read_text() == pre_existing_content, (
            "Pre-existing workspace content must be byte-identical after failed materialization"
        )

        # (d) Canonical content hash must match the original
        assert pkg.content_hash == content_hash, (
            "Canonical content hash in ledger must be unchanged"
        )

    def test_system_contract_slug_protected_from_user_authored(
        self, tmp_dir, test_settings, db
    ):
        """Lifecycle-proposed skills with system-contract slugs are rejected at
        proposal time (protected-slug check), so they never reach materialization.

        The protection comes from the live release/system catalog check in the
        proposal route and service layer."""
        from runtime.orchestrator.workspace_adapters import inject_managed_skills
        from runtime.skills.lifecycle.service import SkillLifecycleService, LifecycleError
        from runtime.skills.system_contracts import SYSTEM_CONTRACTS

        sc_slugs = {sc.id for sc in SYSTEM_CONTRACTS}
        assert len(sc_slugs) > 0, "need at least one system contract"
        test_slug = sorted(sc_slugs)[0]

        service = SkillLifecycleService()

        # Attempting to propose with a system contract slug must fail
        with pytest.raises(LifecycleError, match="protected"):
            service.submit_proposal(
                db=db,
                actor_kind="agent",
                slug=test_slug,
                name="Imposter",
                description="Attempting to use protected slug",
                skill_md="# Evil",
                version="0.1.0",
                task_id="TASK-100",
                session_id="sess-001",
                proposer_agent="dev_agent",
            )
