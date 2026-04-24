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


def test_codex_agents_md_inlines_completion_contract(test_settings, tmp_dir):
    """A Codex session never discovers Claude-style ``.claude/skills/*/SKILL.md``
    files — its workflow comes from AGENTS.md. The failure mode that motivated
    this (senior_dev / TASK-077, 2026-04-24) was: the inlined system prompt
    pointed at a `start-task skill` that didn't exist for Codex, and the
    adapter's own Workflow section reduced to two lines with no JSON schema.
    Codex completed its turn, wrote a verdict to stdout, and exited 0 — the
    orchestrator then auto-rejected the task with ``no completion callback``.

    AGENTS.md must therefore inline the full completion contract so the model
    can obey it without referencing external skill files.
    """
    workspace = tmp_dir / "workspaces" / "senior_dev"
    workspace.mkdir(parents=True)

    CodexWorkspaceAdapter(test_settings).ensure_workspace_ready(
        workspace=workspace,
        agent_name="senior_dev",
        system_prompt="You are the Senior Developer.",
    )

    body = (workspace / "AGENTS.md").read_text()

    # The callback is mandatory and must be called out explicitly — the fix
    # for TASK-077 is precisely to make "exiting without calling back" an
    # impossible-to-miss failure mode for the model.
    assert "opc report-completion" in body
    assert "mandatory" in body.lower()

    # The JSON payload schema (task_id + session_id + agent + status + summary)
    # must be inlined so Codex doesn't need to read a skill file.
    for field in ('"task_id"', '"session_id"', '"agent"', '"status"', '"summary"'):
        assert field in body, f"AGENTS.md missing payload field {field}"

    # --from-file form is mandatory across executors; it must be shown.
    assert "--from-file" in body
    assert "/tmp/completion-" in body

    # Blocker path must be documented.
    assert '"blocked"' in body

    # EH decision contract must be inlined (delegate/done/escalate) since
    # engineering_head can also be Codex-backed in future enrollments.
    assert "decision" in body
    assert "delegate" in body
    assert "escalate" in body

    # Still no mention of Claude-specific skill names (would be confusing
    # inside a Codex workspace and would conflict with the embedded system
    # prompt, which is exactly how TASK-077 was poisoned).
    assert "start-task" not in body
    assert "make-worktree" not in body
