import json
from pathlib import Path

from src.orchestrator.context_builder import ContextBuilder


def test_build_settings_json_no_repos(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    builder.write_settings_json(workspace)
    settings_path = workspace / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "permissions" in data
    assert "Read(*)" in data["permissions"]["allow"]
    # No repos → no hooks
    assert data["hooks"] == {}


def test_build_settings_json_with_repos(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    builder.write_settings_json(workspace, repo_names=["my-opc", "web-app"])
    data = json.loads((workspace / ".claude" / "settings.json").read_text())
    hook_cmd = data["hooks"]["PreToolUse"][0]["command"]
    assert "repos/my-opc" in hook_cmd
    assert "repos/web-app" in hook_cmd


def test_build_claude_md_contains_system_prompt(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    system_prompt = "You are the Dev Agent for a tourism services company."
    builder.write_claude_md(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt=system_prompt,
    )
    claude_md = workspace / "CLAUDE.md"
    assert claude_md.exists()
    content = claude_md.read_text()
    assert "Dev Agent" in content
    assert "tourism services" in content


def test_build_claude_md_contains_persistent_file_pointers(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    builder.write_claude_md(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )
    content = (workspace / "CLAUDE.md").read_text()
    assert "learnings.md" in content
    assert "scorecard.md" in content
    assert "recent_tasks.md" in content


def test_build_claude_md_with_task_brief(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    builder.write_claude_md(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
        task_brief="Implement Alipay integration for international cards",
    )
    content = (workspace / "CLAUDE.md").read_text()
    assert "Alipay integration" in content


def test_initialize_workspace_creates_persistent_files(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    builder.initialize_workspace(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )
    assert (workspace / "learnings.md").exists()
    assert (workspace / "scorecard.md").exists()
    assert (workspace / "recent_tasks.md").exists()
    assert (workspace / "CLAUDE.md").exists()
    assert (workspace / ".claude" / "settings.json").exists()


def test_build_claude_md_lists_repos(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    builder.write_claude_md(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
        repo_names=["my-opc", "web-app"],
    )
    content = (workspace / "CLAUDE.md").read_text()
    assert "repos/my-opc/" in content
    assert "repos/web-app/" in content
    assert "Available Repositories" in content


def test_initialize_workspace_detects_cloned_repos(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    # Simulate pre-existing cloned repos
    for name in ["my-opc", "web-app"]:
        repo_dir = workspace / "repos" / name / ".git"
        repo_dir.mkdir(parents=True)
    builder.initialize_workspace(workspace, "dev_agent", "You are the Dev Agent.")
    content = (workspace / "CLAUDE.md").read_text()
    assert "repos/my-opc/" in content
    assert "repos/web-app/" in content
    settings_data = json.loads((workspace / ".claude" / "settings.json").read_text())
    hook_cmd = settings_data["hooks"]["PreToolUse"][0]["command"]
    assert "repos/my-opc" in hook_cmd


def test_initialize_workspace_does_not_overwrite_existing_learnings(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "learnings.md").write_text("# Learnings\n\n- Important lesson\n")
    builder.initialize_workspace(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )
    content = (workspace / "learnings.md").read_text()
    assert "Important lesson" in content
