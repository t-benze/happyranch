import json
from pathlib import Path

from src.orchestrator.workspace_adapters import (
    ClaudeWorkspaceAdapter,
    CodexWorkspaceAdapter,
)


def test_claude_adapter_bootstrap_creates_claude_files_and_skills(test_settings, tmp_dir):
    skills_root = test_settings.get_protocol_dir() / "skills"
    (skills_root / "start-task").mkdir(parents=True)
    (skills_root / "start-task" / "SKILL.md").write_text("# start-task\n")

    workspace = tmp_dir / "workspaces" / "dev_agent"
    (workspace / "repos" / "my-opc" / ".git").mkdir(parents=True)

    ClaudeWorkspaceAdapter(test_settings).ensure_workspace_ready(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )

    assert (workspace / "CLAUDE.md").exists()
    assert (workspace / ".claude" / "settings.json").exists()
    assert (workspace / ".claude" / "skills" / "start-task" / "SKILL.md").exists()
    assert (workspace / "learnings.md").exists()
    assert (workspace / "scorecard.md").exists()
    assert (workspace / "task_history.md").exists()

    data = json.loads((workspace / ".claude" / "settings.json").read_text())
    hook_cmd = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "repos/my-opc" in hook_cmd


def test_codex_adapter_bootstrap_creates_agents_md_without_claude_tree(test_settings, tmp_dir):
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "recent_tasks.md").write_text("# Recent Tasks: dev_agent\n\n- TASK-001\n")

    CodexWorkspaceAdapter(test_settings).ensure_workspace_ready(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )

    assert (workspace / "AGENTS.md").exists()
    assert not (workspace / "CLAUDE.md").exists()
    assert not (workspace / ".claude" / "skills" / "start-task").exists()
    assert (workspace / "learnings.md").exists()
    assert (workspace / "scorecard.md").exists()
    assert (workspace / "task_history.md").exists()
    assert not (workspace / "recent_tasks.md").exists()

    body = (workspace / "AGENTS.md").read_text()
    assert "You are the Dev Agent." in body
    assert "start-task" not in body
    assert ".claude/settings.json" not in body
    assert "PreToolUse" not in body
    assert "Bash(opc:*)" not in body
