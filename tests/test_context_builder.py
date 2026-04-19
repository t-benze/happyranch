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
    # Only the orchestrator CLI is pinned open; everything else inherits
    # Claude Code's default auto-mode behavior.
    assert data["permissions"]["allow"] == ["Bash(opc:*)"]
    # No repos → no hooks
    assert data["hooks"] == {}


def test_build_settings_json_with_repos(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    builder.write_settings_json(workspace, repo_names=["my-opc", "web-app"])
    data = json.loads((workspace / ".claude" / "settings.json").read_text())
    hook_cmd = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
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
    assert "task_history.md" in content
    assert "recent_tasks.md" not in content



def test_ensure_workspace_ready_creates_persistent_files(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    builder.ensure_workspace_ready(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )
    assert (workspace / "learnings.md").exists()
    assert (workspace / "scorecard.md").exists()
    assert (workspace / "task_history.md").exists()
    assert not (workspace / "recent_tasks.md").exists()
    assert (workspace / "CLAUDE.md").exists()
    assert (workspace / ".claude" / "settings.json").exists()


def test_ensure_workspace_ready_migrates_recent_tasks_to_task_history(test_settings, tmp_dir):
    """Workspaces carried over from before the rename should have their
    recent_tasks.md renamed in place so no history is lost."""
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "recent_tasks.md").write_text(
        "# Recent Tasks: dev_agent\n\n- TASK-001 old entry\n"
    )
    builder.ensure_workspace_ready(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )
    assert not (workspace / "recent_tasks.md").exists()
    migrated = (workspace / "task_history.md").read_text()
    assert "TASK-001 old entry" in migrated


def test_build_claude_md_points_at_agent_yaml_for_repos(test_settings, tmp_dir):
    """CLAUDE.md should redirect readers to agent.yaml for the repo list,
    not duplicate it inline — agent.yaml is the source of truth."""
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
    assert "Available Repositories" in content
    assert "agent.yaml" in content
    # The repo names themselves must not be inlined — that would drift.
    assert "repos/my-opc/" not in content
    assert "repos/web-app/" not in content


def test_ensure_workspace_ready_detects_cloned_repos(test_settings, tmp_dir):
    """Cloned repos drive the PreToolUse git-pull hook (so `git pull` fires
    per repo) but do not get enumerated in CLAUDE.md — agent.yaml is the
    single source of truth for that list."""
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    # Simulate pre-existing cloned repos
    for name in ["my-opc", "web-app"]:
        repo_dir = workspace / "repos" / name / ".git"
        repo_dir.mkdir(parents=True)
    builder.ensure_workspace_ready(workspace, "dev_agent", "You are the Dev Agent.")
    settings_data = json.loads((workspace / ".claude" / "settings.json").read_text())
    hook_cmd = settings_data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "repos/my-opc" in hook_cmd
    assert "repos/web-app" in hook_cmd


def test_ensure_workspace_ready_does_not_overwrite_existing_learnings(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "learnings.md").write_text("# Learnings\n\n- Important lesson\n")
    builder.ensure_workspace_ready(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )
    content = (workspace / "learnings.md").read_text()
    assert "Important lesson" in content


def test_ensure_workspace_ready_copies_skills(test_settings, tmp_path):
    # Set up a fake protocol/skills/ tree
    skills_root = test_settings.get_protocol_dir() / "skills"
    (skills_root / "start-task").mkdir(parents=True)
    (skills_root / "start-task" / "SKILL.md").write_text("# start-task\n")
    (skills_root / "make-worktree").mkdir(parents=True)
    (skills_root / "make-worktree" / "SKILL.md").write_text("# make-worktree\n")

    workspace = tmp_path / "workspace"
    ContextBuilder(test_settings).ensure_workspace_ready(workspace, "dev_agent", "system prompt")

    assert (workspace / ".claude" / "skills" / "start-task" / "SKILL.md").read_text() == "# start-task\n"
    assert (workspace / ".claude" / "skills" / "make-worktree" / "SKILL.md").read_text() == "# make-worktree\n"


def test_ensure_workspace_ready_without_skills_dir_is_noop(test_settings, tmp_path):
    skills_root = test_settings.get_protocol_dir() / "skills"
    assert not skills_root.exists()
    workspace = tmp_path / "workspace"
    ContextBuilder(test_settings).ensure_workspace_ready(workspace, "dev_agent", "system prompt")
    assert not (workspace / ".claude" / "skills").exists()


def test_claude_md_drops_task_brief_and_completion_report(test_settings, tmp_path):
    workspace = tmp_path / "workspace"
    ContextBuilder(test_settings).write_claude_md(workspace, "dev_agent", "system prompt")
    text = (workspace / "CLAUDE.md").read_text()
    assert "Current Task" not in text
    assert "completion_report.json" not in text


def test_generated_claude_md_contains_kb_section(tmp_path):
    from src.config import Settings

    builder = ContextBuilder(Settings())
    workspace = tmp_path / "ws"
    builder.write_claude_md(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are dev_agent.",
    )
    body = (workspace / "CLAUDE.md").read_text()
    assert "## Knowledge Base" in body
    assert "opc kb" in body
    assert "Consult" in body
    assert "Contribute" in body
