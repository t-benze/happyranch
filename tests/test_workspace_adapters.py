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
    assert "Bash(grassland:*)" not in body


def test_copy_skills_substitutes_org_slug(tmp_path: Path, monkeypatch) -> None:
    """`_copy_skills` must replace `{ORG_SLUG}` in every copied .md file with
    the adapter's own slug. Skills source is shared across orgs, but each
    workspace ends up with its own org's slug baked into the example `grassland`
    invocations so agent callbacks always carry `--org`.
    """
    from src.config import Settings

    proto = tmp_path / "protocol" / "skills" / "start-task"
    proto.mkdir(parents=True)
    (proto / "SKILL.md").write_text(
        "Run: grassland report-completion --org {ORG_SLUG} --task-id ...\n"
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
    only sanctioned prefixes. The baseline ``grassland *`` is always allowed; an
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
    assert bash["grassland *"] == "allow"
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
    assert bash["grassland *"] == "allow"
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


def test_claude_md_includes_shared_assets_section(tmp_path: Path) -> None:
    # Adjust adapter construction to match the existing test fixtures.
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import ClaudeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = ClaudeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_claude_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "CLAUDE.md").read_text()
    assert "## Shared Assets" in content
    assert "grassland assets put" in content
    assert "grassland assets list" in content
    assert "grassland assets get" in content


def test_codex_agents_md_includes_shared_assets_section(tmp_path: Path) -> None:
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = CodexWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    assert "## Shared Assets" in content
    assert "grassland assets put" in content


def test_opencode_agents_md_includes_shared_assets_section(tmp_path: Path) -> None:
    from src.config import Settings
    from src.orchestrator._paths import OrgPaths
    from src.orchestrator.workspace_adapters import OpencodeWorkspaceAdapter

    paths = OrgPaths(root=tmp_path)
    adapter = OpencodeWorkspaceAdapter(Settings(), paths, slug="demo")
    workspace = tmp_path / "workspaces" / "dev_agent"
    adapter.write_agents_md(workspace, "dev_agent", "You are dev_agent.")
    content = (workspace / "AGENTS.md").read_text()
    assert "## Shared Assets" in content
    assert "grassland assets put" in content
