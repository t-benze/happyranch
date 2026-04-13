from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from src.config import Settings

logger = logging.getLogger(__name__)


def _build_settings_json(repo_names: list[str]) -> dict:
    """Build .claude/settings.json with a git pull hook for all repos."""
    if repo_names:
        # Pull all repos on first tool use
        pull_cmds = " && ".join(
            f"(cd repos/{name} && git pull --ff-only 2>/dev/null; true)"
            for name in repo_names
        )
        hooks = {
            "PreToolUse": [
                {
                    "matcher": "Bash|Read|Grep|Glob",
                    "command": pull_cmds,
                    "runOnce": True,
                }
            ]
        }
    else:
        hooks = {}

    return {
        "permissions": {
            "allow": ["Read(*)", "Write(*)", "Bash(*)", "Glob(*)", "Grep(*)"]
        },
        "hooks": hooks,
    }


class ContextBuilder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def write_settings_json(self, workspace: Path, repo_names: list[str] | None = None) -> None:
        """Write .claude/settings.json to workspace."""
        claude_dir = workspace / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_data = _build_settings_json(repo_names or [])
        (claude_dir / "settings.json").write_text(
            json.dumps(settings_data, indent=2) + "\n"
        )

    def write_claude_md(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        task_brief: str | None = None,
        repo_names: list[str] | None = None,
    ) -> None:
        """Write CLAUDE.md to workspace with system prompt and context pointers."""
        sections = [
            f"# Agent: {agent_name}\n",
            "## System Prompt\n",
            system_prompt.strip() + "\n",
        ]

        # List available repos
        if repo_names:
            sections.append("## Available Repositories\n")
            for name in repo_names:
                sections.append(f"- `repos/{name}/` — git clone, kept fresh via PreToolUse hook")
            sections.append("")

        sections.extend([
            "## Persistent Files\n",
            "- `learnings.md` -- your accumulated operational learnings (append new insights here)",
            "- `scorecard.md` -- your current performance scorecard (read-only, updated by orchestrator)",
            "- `recent_tasks.md` -- summary of your recent tasks (read-only, updated by orchestrator)\n",
            "## Completion Report\n",
            "At the end of every task, write `completion_report.json` to this workspace root:",
            "```json",
            '{',
            '  "task_id": "<from your task brief>",',
            '  "agent": "' + agent_name + '",',
            '  "status": "completed",',
            '  "confidence": 85,',
            '  "output_summary": "<what you did>",',
            '  "risks_flagged": ["<any concerns>"],',
            '  "dependencies": ["<what you assumed or relied on>"],',
            '  "suggested_reviewer_focus": ["<where to look hardest>"]',
            '}',
            "```\n",
            "If you learn something reusable for future tasks, append it to `learnings.md` in this workspace.\n",
        ])

        if task_brief:
            sections.extend([
                "## Current Task\n",
                task_brief.strip() + "\n",
            ])

        (workspace / "CLAUDE.md").write_text("\n".join(sections))

    def initialize_workspace(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
    ) -> None:
        """Set up an agent workspace with all required files.

        Creates persistent files only if they don't already exist.
        Always regenerates CLAUDE.md and settings.json.
        """
        workspace.mkdir(parents=True, exist_ok=True)

        for filename, default_content in [
            ("learnings.md", f"# Learnings: {agent_name}\n\n"),
            ("scorecard.md", "# Scorecard\n\nNo performance data yet. Tier: green (default)\n"),
            ("recent_tasks.md", f"# Recent Tasks: {agent_name}\n\n"),
        ]:
            path = workspace / filename
            if not path.exists():
                path.write_text(default_content)

        # Detect cloned repos
        repos_dir = workspace / "repos"
        repo_names = []
        if repos_dir.exists():
            repo_names = sorted(
                d.name for d in repos_dir.iterdir()
                if d.is_dir() and (d / ".git").exists()
            )

        self.write_claude_md(workspace, agent_name, system_prompt, repo_names=repo_names)
        self.write_settings_json(workspace, repo_names=repo_names)

    def clone_repo(self, workspace: Path, name: str, url: str) -> bool:
        """Clone a repo into workspace/repos/<name>/. Returns True on success."""
        repo_dir = workspace / "repos" / name
        if repo_dir.exists() and (repo_dir / ".git").exists():
            logger.info("Repo already cloned at %s, pulling latest", repo_dir)
            try:
                subprocess.run(
                    ["git", "pull", "--ff-only"],
                    cwd=str(repo_dir),
                    capture_output=True,
                    timeout=60,
                )
                return True
            except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
                logger.warning("git pull failed: %s", e)
                return False

        repo_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Cloning %s into %s", url, repo_dir)
        try:
            subprocess.run(
                ["git", "clone", url, str(repo_dir)],
                capture_output=True,
                check=True,
                timeout=120,
            )
            return True
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            logger.error("git clone failed: %s", e)
            return False

    def clone_repos(self, workspace: Path, repos: dict[str, str]) -> dict[str, bool]:
        """Clone multiple repos. Returns {name: success}."""
        results = {}
        for name, url in repos.items():
            results[name] = self.clone_repo(workspace, name, url)
        return results

    def create_agent_dirs(self, workspace: Path, agent_name: str) -> None:
        """Create agent-specific subdirectories per the spec."""
        agent_dirs: dict[str, list[str]] = {
            "product_manager": ["specs"],
            "payment_agent": ["proposals"],
        }
        for dirname in agent_dirs.get(agent_name, []):
            (workspace / dirname).mkdir(parents=True, exist_ok=True)
