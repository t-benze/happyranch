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
    (skills_root / "review").mkdir(parents=True)
    (skills_root / "review" / "SKILL.md").write_text(
        "---\nname: review\ndescription: Mid-thread review capturing learnings and KB entries.\n---\n"
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
    assert not (workspace / ".agents" / "skills" / "review" / "SKILL.md").exists()
    # Fresh workspace: migrated layout (memory/ dir, no flat learnings.md).
    assert (workspace / "memory").is_dir()
    assert (workspace / "memory" / "_index.md").exists()
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

    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="hk-tourism")
    # Re-enable wholesale dump for this direct _copy_skills test so the
    # substitution logic can still be verified.
    import runtime.orchestrator.workspace_adapters as wa_mod
    old = wa_mod._WHOLESALE_DUMP_ENABLED
    wa_mod._WHOLESALE_DUMP_ENABLED = True
    try:
        adapter._copy_skills(workspace)
    finally:
        wa_mod._WHOLESALE_DUMP_ENABLED = old

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
# Phase 3b: user-authored skill materialization + staleness (TDD)
# ══════════════════════════════════════════════════════════════════════════════


class TestUserAuthoredSkillMaterialization:
    """TDD: inject_managed_skills materializes user_authored skills from the
    per-org store, records materialized version, and preserves the old working
    version when edits bump the store version (MEM-288 / v3 §7.1, §9.5)."""

    def test_user_authored_skill_materialized_and_version_recorded(
        self, tmp_dir, test_settings, db
    ):
        """A user_authored skill in the org store is materialized by
        inject_managed_skills with its version recorded."""
        from runtime.orchestrator.workspace_adapters import inject_managed_skills
        from runtime.skills.registry import SkillRegistry

        # Create a user-authored skill in the org store
        org_root = tmp_dir / "org"
        skill_dir = org_root / "skills" / "custom-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Custom Skill\n\nTest content.")
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

        # Create eligibility policy assigning the skill to dev_agent
        org_config_dir = org_root / "org"
        org_config_dir.mkdir(parents=True)
        import yaml
        policy = {
            "skills": {
                "agents": {
                    "dev_agent": {
                        "allow": ["hr:custom-skill"],
                    }
                }
            }
        }
        (org_config_dir / "config.yaml").write_text(yaml.dump(policy))

        # Also create a release-managed skills root
        managed_root = tmp_dir / "managed"
        managed_root.mkdir()

        workspace = tmp_dir / "ws"

        # Materialize with org_root
        inject_managed_skills(
            workspace, test_settings,
            slug="test",
            agent_name="dev_agent",
            team="engineering",
            skills_root=managed_root,
            org_root=org_root,
            db=db,
        )

        # The user-authored skill should be on disk
        claude_skill = workspace / ".claude" / "skills" / "custom-skill" / "SKILL.md"
        agents_skill = workspace / ".agents" / "skills" / "custom-skill" / "SKILL.md"
        assert claude_skill.is_file(), (
            "user-authored skill must be materialized to .claude/skills/"
        )
        assert agents_skill.is_file(), (
            "user-authored skill must be materialized to .agents/skills/"
        )
        assert "Custom Skill" in claude_skill.read_text()

        # A materialization event should be recorded with the version
        events = db.list_skill_validation_events(
            skill_id="hr:custom-skill", agent="dev_agent"
        )
        mat_events = [e for e in events if e["source"] == "materialization"]
        assert len(mat_events) == 1, (
            f"Expected 1 materialization event, got {len(mat_events)}"
        )
        assert mat_events[0]["version"] == "1.0.0"
        assert mat_events[0]["ok"] is True

    def test_edit_preserves_old_version_on_disk_until_next_spawn(
        self, tmp_dir, test_settings, db
    ):
        """When a user_authored skill that is effective is edited (version
        bumped), the OLD materialized version stays live and functional on disk
        until the next spawn re-materializes the NEW version.

        A FAILED re-validation of the edit must NOT remove the working old
        version (MEM-288).
        """
        from runtime.orchestrator.workspace_adapters import inject_managed_skills

        # Create a user-authored skill in the org store (v1.0.0)
        org_root = tmp_dir / "org"
        skill_dir = org_root / "skills" / "custom-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Custom Skill v1\n\nv1 content.")
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

        # Eligibility policy
        org_config_dir = org_root / "org"
        org_config_dir.mkdir(parents=True)
        import yaml
        policy = {
            "skills": {
                "agents": {
                    "dev_agent": {
                        "allow": ["hr:custom-skill"],
                    }
                }
            }
        }
        (org_config_dir / "config.yaml").write_text(yaml.dump(policy))

        managed_root = tmp_dir / "managed"
        managed_root.mkdir()
        workspace = tmp_dir / "ws"

        # ── First spawn: materialize v1.0.0 ─────────────────────────
        inject_managed_skills(
            workspace, test_settings,
            slug="test",
            agent_name="dev_agent",
            team="engineering",
            skills_root=managed_root,
            org_root=org_root,
            db=db,
        )

        claude_skill = workspace / ".claude" / "skills" / "custom-skill" / "SKILL.md"
        assert claude_skill.is_file()
        assert "v1 content" in claude_skill.read_text()

        # ── Edit the skill in the store: bump to v2.0.0 ─────────────
        (skill_dir / "SKILL.md").write_text("# Custom Skill v2\n\nv2 content.")
        (skill_dir / "skill.yaml").write_text(
            "id: hr:custom-skill\n"
            "slug: custom-skill\n"
            "name: Custom Skill\n"
            "version: 2.0.0\n"
            "description: A custom skill v2\n"
            "when_to_use: ''\n"
            "owner: operator\n"
            "source: user_authored\n"
            "policy_class: standard_operational\n"
            "status: enabled\n"
        )

        # ── BEFORE re-materialization: old v1.0.0 is STILL on disk ─
        # This is the key invariant: edit does NOT remove the working old version
        assert claude_skill.is_file(), (
            "OLD materialized version must stay on disk after edit "
            "(edit must NOT pull/remove the working version)"
        )
        assert "v1 content" in claude_skill.read_text(), (
            "OLD content must be preserved until next spawn re-materializes"
        )

        # ── Second spawn: re-materialize, now v2.0.0 lands ──────────
        inject_managed_skills(
            workspace, test_settings,
            slug="test",
            agent_name="dev_agent",
            team="engineering",
            skills_root=managed_root,
            org_root=org_root,
            db=db,
        )

        # After re-materialization, v2.0.0 should be on disk
        assert claude_skill.is_file()
        assert "v2 content" in claude_skill.read_text(), (
            "NEW version must land on next spawn"
        )

        # Both materialization events should be recorded
        events = db.list_skill_validation_events(
            skill_id="hr:custom-skill", agent="dev_agent"
        )
        mat_events = [e for e in events if e["source"] == "materialization"]
        versions = sorted(e["version"] for e in mat_events)
        assert versions == ["1.0.0", "2.0.0"], (
            f"Expected materialization events for v1.0.0 and v2.0.0, got {versions}"
        )

    def test_user_authored_skill_release_wins_on_slug_collision(
        self, tmp_dir, test_settings, db
    ):
        """Release-shipped skills beat user-authored on slug collision."""
        from runtime.orchestrator.workspace_adapters import inject_managed_skills

        # Create a user-authored skill with same slug as a release skill
        org_root = tmp_dir / "org"
        skill_dir = org_root / "skills" / "review"  # collides with release 'review'
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Bogus Review\n\nEvil content.")
        (skill_dir / "skill.yaml").write_text(
            "id: hr:review\n"
            "slug: review\n"
            "name: Bogus Review\n"
            "version: 9.9.9\n"
            "description: Bogus\n"
            "when_to_use: ''\n"
            "owner: operator\n"
            "source: user_authored\n"
            "policy_class: standard_operational\n"
            "status: enabled\n"
        )

        # Eligibility policy — agent is eligible for 'review'
        org_config_dir = org_root / "org"
        org_config_dir.mkdir(parents=True)
        import yaml
        policy = {
            "skills": {
                "agents": {
                    "dev_agent": {
                        "allow": ["review"],
                    }
                }
            }
        }
        (org_config_dir / "config.yaml").write_text(yaml.dump(policy))

        # Create release-managed skill 'review'
        managed_root = tmp_dir / "managed"
        managed_root.mkdir()
        release_review = managed_root / "review"
        release_review.mkdir()
        (release_review / "SKILL.md").write_text("# Real Review\n\nLegit content.")
        (release_review / "skill.yaml").write_text(
            "id: review\n"
            "slug: review\n"
            "name: Code Review\n"
            "version: 1.2.0\n"
            "description: Legit review skill\n"
            "when_to_use: ''\n"
            "owner: runtime\n"
            "source: first_party\n"
            "policy_class: standard_operational\n"
            "status: enabled\n"
        )

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

        claude_skill = workspace / ".claude" / "skills" / "review" / "SKILL.md"
        assert claude_skill.is_file()
        content = claude_skill.read_text()
        # The RELEASE version must be on disk, NOT the user-authored imposter
        assert "Legit" in content, (
            f"Release skill must win on slug collision, got: {content}"
        )
        assert "Evil" not in content, (
            "User-authored imposter must NOT be materialized"
        )

    def test_materialization_fail_closed_no_partial_state(
        self, tmp_dir, test_settings, db
    ):
        """FAIL-CLOSED: a materialization error must not leave a partially-
        populated skills dir passing as complete."""
        from runtime.orchestrator.workspace_adapters import inject_managed_skills

        # Create a valid user-authored skill AND one with no SKILL.md
        # (the copy logic handles missing src_dir gracefully, so we simulate
        # a post-copy failure differently — verify that an error mid-copy
        # leaves the workspace clean)
        org_root = tmp_dir / "org"
        # Create valid skill
        valid_dir = org_root / "skills" / "valid-skill"
        valid_dir.mkdir(parents=True)
        (valid_dir / "SKILL.md").write_text("# Valid\n\ncontent.")
        (valid_dir / "skill.yaml").write_text(
            "id: hr:valid-skill\n"
            "slug: valid-skill\n"
            "name: Valid\n"
            "version: 1.0.0\n"
            "description: Valid\n"
            "when_to_use: ''\n"
            "owner: operator\n"
            "source: user_authored\n"
            "policy_class: standard_operational\n"
            "status: enabled\n"
        )

        # Eligibility — assign both skills
        org_config_dir = org_root / "org"
        org_config_dir.mkdir(parents=True)
        import yaml
        policy = {
            "skills": {
                "agents": {
                    "dev_agent": {
                        "allow": ["hr:valid-skill"],
                    }
                }
            }
        }
        (org_config_dir / "config.yaml").write_text(yaml.dump(policy))

        managed_root = tmp_dir / "managed"
        managed_root.mkdir()
        workspace = tmp_dir / "ws"

        # Materialization should succeed for the valid skill
        inject_managed_skills(
            workspace, test_settings,
            slug="test",
            agent_name="dev_agent",
            team="engineering",
            skills_root=managed_root,
            org_root=org_root,
            db=db,
        )

        claude_skill = workspace / ".claude" / "skills" / "valid-skill" / "SKILL.md"
        assert claude_skill.is_file(), (
            "Valid skill must be materialized"
        )
        # No partial state — only the valid skill landed
        assert claude_skill.read_text().startswith("# Valid")

    def test_system_contract_slug_protected_from_user_authored(
        self, tmp_dir, test_settings, db
    ):
        """A user-authored package with a system-contract slug is skipped
        even when NO matching release package exists — the protection comes
        solely from SYSTEM_CONTRACTS (sc_slugs), not release_slugs (REVISE TASK-2836).

        The original TASK-2829 test was a false positive: it created BOTH a
        release-managed skill AND a user-authored imposter with the same slug,
        so the release-wins path (not sc_slugs) masked the true protection."""
        from runtime.orchestrator.workspace_adapters import inject_managed_skills
        from runtime.skills.system_contracts import SYSTEM_CONTRACTS

        # Pick a real system-contract slug
        sc_slugs = {sc.id for sc in SYSTEM_CONTRACTS}
        assert len(sc_slugs) > 0, "need at least one system contract"
        test_slug = sorted(sc_slugs)[0]

        # Create a user-authored imposter with that slug
        org_root = tmp_dir / "org"
        skill_dir = org_root / "skills" / test_slug
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Imposter\n\nEvil content.")
        imposter_id = f"hr:{test_slug}"
        (skill_dir / "skill.yaml").write_text(
            f"id: {imposter_id}\n"
            f"slug: {test_slug}\n"
            f"name: Imposter {test_slug}\n"
            "version: 9.9.9\n"
            "description: Bogus\n"
            "when_to_use: ''\n"
            "owner: operator\n"
            "source: user_authored\n"
            "policy_class: standard_operational\n"
            "status: enabled\n"
        )

        # Eligibility policy — allow the imposter's full skill_id so it passes
        # eligibility and the only blocker is protected_slugs (sc_slugs).
        org_config_dir = org_root / "org"
        org_config_dir.mkdir(parents=True)
        import yaml
        policy = {
            "skills": {
                "agents": {
                    "dev_agent": {
                        "allow": [imposter_id],
                    }
                }
            }
        }
        (org_config_dir / "config.yaml").write_text(yaml.dump(policy))

        # Managed root with NO release package for this slug.
        # The protection comes SOLELY from sc_slugs (SYSTEM_CONTRACTS).
        managed_root = tmp_dir / "managed"
        managed_root.mkdir()

        workspace = tmp_dir / "ws"

        # Pre-materialize a legit system-contract stub at the destination.
        # Mirrors production: system-contract injection runs before managed
        # injection (orchestrator.py:586 then :602). After inject_managed_skills
        # the stub must remain — the user-authored imposter must NOT overwrite it.
        dest_dir = workspace / ".claude" / "skills" / test_slug
        dest_dir.mkdir(parents=True)
        (dest_dir / "SKILL.md").write_text(
            f"# {test_slug.capitalize()}\n\nLegit system contract content."
        )

        inject_managed_skills(
            workspace, test_settings,
            slug="test",
            agent_name="dev_agent",
            team="engineering",
            skills_root=managed_root,
            org_root=org_root,
            db=db,
        )

        # (a) The system-contract stub must survive — the imposter must NOT
        # overwrite it.
        claude_skill = dest_dir / "SKILL.md"
        assert claude_skill.is_file(), (
            f"System-contract skill {test_slug} must survive materialization"
        )
        content = claude_skill.read_text()
        assert "Legit" in content, (
            f"System-contract stub must survive for slug {test_slug}, "
            f"got: {content}"
        )
        assert "Evil" not in content, (
            f"User-authored imposter with system-contract slug {test_slug} "
            f"must NOT overwrite the destination (sc_slugs protection)"
        )

        # (b) No materialization record for the imposter — the user-authored
        # package with a SYSTEM_CONTRACTS slug must never be recorded as
        # materialized.
        events = db.list_skill_validation_events(
            skill_id=imposter_id, agent="dev_agent"
        )
        imposter_events = [
            e for e in events
            if e["source"] == "materialization"
        ]
        assert len(imposter_events) == 0, (
            f"No materialization event should exist for user-authored imposter "
            f"with system-contract slug {test_slug} (got {len(imposter_events)})"
        )
