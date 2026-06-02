import json
from pathlib import Path

import pytest

from src.orchestrator._paths import OrgPaths
from src.orchestrator.workspace_adapters import (
    ClaudeWorkspaceAdapter,
    CodexWorkspaceAdapter,
    OpencodeWorkspaceAdapter,
)
from src.runtime import RuntimeDir


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
    assert (workspace / ".claude" / "skills" / "start-task" / "SKILL.md").exists()
    assert (workspace / "learnings").is_dir()
    assert (workspace / "learnings" / "_index.md").exists()
    assert not (workspace / "learnings.md").exists()
    assert not (workspace / "scorecard.md").exists()
    assert (workspace / "task_history.md").exists()

    data = json.loads((workspace / ".claude" / "settings.json").read_text())
    hook_cmd = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "repos/my-opc" in hook_cmd


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
    (skills_root / "talk").mkdir(parents=True)
    (skills_root / "talk" / "SKILL.md").write_text(
        "---\nname: talk\ndescription: Use when the founder runs /talk start.\n---\n"
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
    # Codex skills land under .agents/skills/, not .claude/skills/
    assert not (workspace / ".claude" / "skills" / "start-task").exists()
    assert (workspace / ".agents" / "skills" / "start-task" / "SKILL.md").exists()
    assert (workspace / ".agents" / "skills" / "talk" / "SKILL.md").exists()
    # Fresh workspace: migrated layout (learnings/ dir, no flat learnings.md).
    assert (workspace / "learnings").is_dir()
    assert (workspace / "learnings" / "_index.md").exists()
    assert not (workspace / "learnings.md").exists()
    assert not (workspace / "scorecard.md").exists()
    assert (workspace / "task_history.md").exists()
    assert not (workspace / "recent_tasks.md").exists()

    body = (workspace / "AGENTS.md").read_text()
    assert "You are the Dev Agent." in body
    # Points at the skill, not at Claude-specific paths.
    assert ".agents/skills/start-task/" in body
    assert ".claude/skills" not in body
    assert ".claude/settings.json" not in body
    assert "PreToolUse" not in body
    assert "Bash(happyranch:*)" not in body


def test_copy_skills_substitutes_org_slug(tmp_path: Path, monkeypatch) -> None:
    """`_copy_skills` must replace `{ORG_SLUG}` in every copied .md file with
    the adapter's own slug. Skills source is shared across orgs, but each
    workspace ends up with its own org's slug baked into the example `happyranch`
    invocations so agent callbacks always carry `--org`.
    """
    from src.config import Settings

    proto = tmp_path / "protocol" / "skills" / "start-task"
    proto.mkdir(parents=True)
    (proto / "SKILL.md").write_text(
        "Run: happyranch report-completion --org {ORG_SLUG} --task-id ...\n"
    )
    monkeypatch.setattr(
        "src.orchestrator.workspace_adapters._SKILLS_SRC",
        tmp_path / "protocol" / "skills",
    )

    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "hk-tourism")
    workspace = tmp_path / "ws"
    workspace.mkdir()

    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="hk-tourism")
    adapter._copy_skills(workspace)

    out = (workspace / ".claude" / "skills" / "start-task" / "SKILL.md").read_text()
    assert "{ORG_SLUG}" not in out
    assert "--org hk-tourism" in out


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
    # Skills under .agents/skills/ — same layout as Codex.
    assert (workspace / ".agents" / "skills" / "start-task" / "SKILL.md").exists()
    assert not (workspace / ".claude" / "skills" / "start-task").exists()
    # Fresh workspace: migrated layout.
    assert (workspace / "learnings").is_dir()
    assert (workspace / "learnings" / "_index.md").exists()
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
    from src.orchestrator.agent_def import AgentDef, render_agent_text

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
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

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
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import CodexWorkspaceAdapter

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
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import OpencodeWorkspaceAdapter

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
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

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


def test_codex_agents_md_warns_about_non_stop_commands(tmp_path: Path) -> None:
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = CodexWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    assert "## Long-running and non-stop commands" in content
    assert "protocol/skills/jobs/SKILL.md" in content


def test_opencode_agents_md_warns_about_non_stop_commands(tmp_path: Path) -> None:
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import OpencodeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = OpencodeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    assert "## Long-running and non-stop commands" in content
    assert "protocol/skills/jobs/SKILL.md" in content


def test_claude_md_includes_thread_talk_dispatch_doctrine(tmp_path: Path) -> None:
    """Every agent's bootstrap doc must carry the self-only dispatch doctrine.

    The route enforces the rule mechanically (returns 403 with
    thread_dispatch_must_be_self / talk_dispatch_must_be_self); this prompt
    section is the *why* and the recommended pattern, surfaced before the
    agent encounters the rejection.
    """
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_claude_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "CLAUDE.md").read_text()
    assert "## Thread and Talk Dispatch are Self-Only" in content
    # Both rejection codes named — agents hitting a 403 can grep for either.
    assert "thread_dispatch_must_be_self" in content
    assert "talk_dispatch_must_be_self" in content
    # The recommended alternative path: compose for cross-agent work.
    assert "happyranch threads compose" in content


def test_codex_agents_md_includes_thread_talk_dispatch_doctrine(tmp_path: Path) -> None:
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = CodexWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    assert "## Thread and Talk Dispatch are Self-Only" in content
    assert "thread_dispatch_must_be_self" in content
    assert "talk_dispatch_must_be_self" in content


def test_opencode_agents_md_includes_thread_talk_dispatch_doctrine(tmp_path: Path) -> None:
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import OpencodeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = OpencodeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    assert "## Thread and Talk Dispatch are Self-Only" in content
    assert "thread_dispatch_must_be_self" in content
    assert "talk_dispatch_must_be_self" in content


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
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_claude_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "CLAUDE.md").read_text()
    _assert_task_completion_format_section(content)


def test_codex_agents_md_includes_task_completion_format_section(tmp_path: Path) -> None:
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = CodexWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    _assert_task_completion_format_section(content)


def test_opencode_agents_md_includes_task_completion_format_section(tmp_path: Path) -> None:
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import OpencodeWorkspaceAdapter

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
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import (
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
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import (
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
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import (
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
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import (
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
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

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
    from src.orchestrator.workspace_adapters import (
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
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

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
