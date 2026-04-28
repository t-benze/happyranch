import json
from pathlib import Path

import pytest

from src.config import Settings
from src.orchestrator._paths import OrgPaths
from src.orchestrator.context_builder import ContextBuilder
from src.runtime import RuntimeDir


@pytest.fixture
def runtime(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    return OrgPaths(root=rt.orgs_dir / "test")


def _write_agent(rt: OrgPaths, name: str, allow_rules: list[str]) -> None:
    rt.agents_dir.mkdir(parents=True, exist_ok=True)
    rules_block = (
        "allow_rules: []\n" if not allow_rules
        else "allow_rules:\n" + "\n".join(f"  - {r!r}" for r in allow_rules) + "\n"
    )
    (rt.agents_dir / f"{name}.md").write_text(
        "---\n"
        f"name: {name}\nteam: engineering\nrole: worker\nexecutor: claude\n"
        f"{rules_block}"
        "repos: {}\nenrolled_by: null\nenrolled_at_task: null\nenrolled_at: null\n"
        "---\n\nbody\n"
    )


def test_build_settings_json_no_repos(test_settings, tmp_dir, runtime):
    builder = ContextBuilder(test_settings, runtime)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    builder.write_settings_json(workspace)
    settings_path = workspace / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "permissions" in data
    # Only the orchestrator CLI is pinned open for non-EH agents; everything
    # else inherits Claude Code's default auto-mode behavior.
    assert data["permissions"]["allow"] == ["Bash(opc:*)"]
    # No repos → no hooks
    assert data["hooks"] == {}


def test_build_settings_json_with_repos(test_settings, tmp_dir, runtime):
    builder = ContextBuilder(test_settings, runtime)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    builder.write_settings_json(workspace, repo_names=["my-opc", "web-app"])
    data = json.loads((workspace / ".claude" / "settings.json").read_text())
    hook_cmd = data["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert "repos/my-opc" in hook_cmd
    assert "repos/web-app" in hook_cmd


def test_ensure_workspace_ready_grants_engineering_head_gh_resolve_rules(
    test_settings, tmp_dir, runtime,
):
    """EH needs to close stale/superseded PRs and close resolved issues during
    revisit cleanup. Those `gh` calls are otherwise blocked by Claude Code's
    headless risk heuristic (see TASK-067 post-mortem). Scope the extra grants
    tightly to close+comment on PRs and issues — no merge, no create, no delete.
    Allow rules now come from the agent's frontmatter in <runtime>/org/agents/.
    """
    # Seed the agent file so allow_rules_for_agent can read the EH allow_rules.
    _write_agent(runtime, "engineering_head", [
        "gh pr close", "gh pr comment", "gh issue close", "gh issue comment",
    ])
    builder = ContextBuilder(test_settings, runtime)
    workspace = tmp_dir / "workspaces" / "engineering_head"
    builder.ensure_workspace_ready(
        workspace=workspace,
        agent_name="engineering_head",
        system_prompt="You are the Engineering Head.",
    )
    data = json.loads((workspace / ".claude" / "settings.json").read_text())
    allow = data["permissions"]["allow"]
    assert "Bash(opc:*)" in allow
    assert "Bash(gh pr close:*)" in allow
    assert "Bash(gh pr comment:*)" in allow
    assert "Bash(gh issue close:*)" in allow
    assert "Bash(gh issue comment:*)" in allow
    # Guardrail: do NOT grant merge/create/delete — those can change shared
    # state in ways the close+comment cleanup flow doesn't require.
    assert not any("gh pr merge" in r for r in allow)
    assert not any("gh pr create" in r for r in allow)
    assert not any("gh issue delete" in r for r in allow)


def test_ensure_workspace_ready_does_not_grant_gh_to_non_eh_agents(
    test_settings, tmp_dir, runtime,
):
    """Only EH resolves PRs/issues; workers stay on the narrow opc allowlist."""
    builder = ContextBuilder(test_settings, runtime)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    builder.ensure_workspace_ready(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )
    data = json.loads((workspace / ".claude" / "settings.json").read_text())
    assert data["permissions"]["allow"] == ["Bash(opc:*)"]


def test_build_claude_md_contains_system_prompt(test_settings, tmp_dir, runtime):
    builder = ContextBuilder(test_settings, runtime)
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


def test_build_claude_md_contains_persistent_file_pointers(test_settings, tmp_dir, runtime):
    builder = ContextBuilder(test_settings, runtime)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    builder.write_claude_md(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )
    content = (workspace / "CLAUDE.md").read_text()
    assert "learnings.md" in content
    assert "scorecard.md" not in content
    assert "task_history.md" in content
    assert "recent_tasks.md" not in content


def test_ensure_workspace_ready_creates_persistent_files(test_settings, tmp_dir, runtime):
    builder = ContextBuilder(test_settings, runtime)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    builder.ensure_workspace_ready(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )
    assert (workspace / "learnings.md").exists()
    assert not (workspace / "scorecard.md").exists()
    assert (workspace / "task_history.md").exists()
    assert not (workspace / "recent_tasks.md").exists()
    assert (workspace / "CLAUDE.md").exists()
    assert (workspace / ".claude" / "settings.json").exists()


def test_ensure_workspace_ready_migrates_recent_tasks_to_task_history(test_settings, tmp_dir, runtime):
    """Workspaces carried over from before the rename should have their
    recent_tasks.md renamed in place so no history is lost."""
    builder = ContextBuilder(test_settings, runtime)
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


def test_build_claude_md_points_at_agent_yaml_for_repos(test_settings, tmp_dir, runtime):
    """CLAUDE.md should redirect readers to agent.yaml for the repo list,
    not duplicate it inline — agent.yaml is the source of truth."""
    builder = ContextBuilder(test_settings, runtime)
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


def test_ensure_workspace_ready_detects_cloned_repos(test_settings, tmp_dir, runtime):
    """Cloned repos drive the PreToolUse git-pull hook (so `git pull` fires
    per repo) but do not get enumerated in CLAUDE.md — agent.yaml is the
    single source of truth for that list."""
    builder = ContextBuilder(test_settings, runtime)
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


def test_ensure_workspace_ready_does_not_overwrite_existing_learnings(test_settings, tmp_dir, runtime):
    builder = ContextBuilder(test_settings, runtime)
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


def test_ensure_workspace_ready_copies_skills(test_settings, tmp_path, runtime):
    # Set up a fake protocol/skills/ tree
    skills_root = test_settings.get_protocol_dir() / "skills"
    (skills_root / "start-task").mkdir(parents=True)
    (skills_root / "start-task" / "SKILL.md").write_text("# start-task\n")
    (skills_root / "make-worktree").mkdir(parents=True)
    (skills_root / "make-worktree" / "SKILL.md").write_text("# make-worktree\n")

    workspace = tmp_path / "workspace"
    ContextBuilder(test_settings, runtime).ensure_workspace_ready(workspace, "dev_agent", "system prompt")

    assert (workspace / ".claude" / "skills" / "start-task" / "SKILL.md").read_text() == "# start-task\n"
    assert (workspace / ".claude" / "skills" / "make-worktree" / "SKILL.md").read_text() == "# make-worktree\n"


def test_ensure_workspace_ready_without_skills_dir_is_noop(test_settings, tmp_path, runtime):
    skills_root = test_settings.get_protocol_dir() / "skills"
    assert not skills_root.exists()
    workspace = tmp_path / "workspace"
    ContextBuilder(test_settings, runtime).ensure_workspace_ready(workspace, "dev_agent", "system prompt")
    assert not (workspace / ".claude" / "skills").exists()


def test_ensure_workspace_ready_can_bootstrap_codex_workspace(test_settings, tmp_path, runtime):
    workspace = tmp_path / "workspace"
    ContextBuilder(test_settings, runtime).ensure_workspace_ready(
        workspace,
        "dev_agent",
        "system prompt",
        provider="codex",
    )
    assert (workspace / "AGENTS.md").exists()
    assert not (workspace / "CLAUDE.md").exists()
    assert not (workspace / ".claude").exists()
    body = (workspace / "AGENTS.md").read_text()
    assert ".claude/settings.json" not in body
    assert "PreToolUse" not in body


def test_claude_md_drops_task_brief_and_completion_report(test_settings, tmp_path, runtime):
    workspace = tmp_path / "workspace"
    ContextBuilder(test_settings, runtime).write_claude_md(workspace, "dev_agent", "system prompt")
    text = (workspace / "CLAUDE.md").read_text()
    assert "Current Task" not in text
    assert "completion_report.json" not in text


def test_generated_claude_md_contains_kb_section(tmp_path):
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    builder = ContextBuilder(Settings(), paths)
    workspace = tmp_path / "ws"
    builder.write_claude_md(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are dev_agent.",
    )
    body = (workspace / "CLAUDE.md").read_text()
    assert "## Knowledge Base" in body
    assert "opc kb list" in body
    assert "opc kb search" in body
    assert "opc kb get" in body
    assert "opc kb add --agent" in body
    assert "opc kb update" in body
    assert "--from-file" in body
    assert "Consult" in body
    assert "Contribute" in body


def test_generated_claude_md_contains_task_recall_section(tmp_path):
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    builder = ContextBuilder(Settings(), paths)
    workspace = tmp_path / "ws"
    builder.write_claude_md(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are dev_agent.",
    )
    body = (workspace / "CLAUDE.md").read_text()
    assert "## Task Recall" in body
    assert "opc recall" in body
    assert "--tree" in body
    assert "--fetch-artifact" in body
    assert "task_history.md" in body
