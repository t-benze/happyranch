from __future__ import annotations

import json
from pathlib import Path

from src.config import Settings


_SETTINGS_JSON = {
    "permissions": {
        "allow": ["Read(*)", "Write(*)", "Bash(*)", "Glob(*)", "Grep(*)"]
    },
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash|Read|Grep|Glob",
                "command": "cd repo && git pull --ff-only 2>/dev/null; true",
                "runOnce": True,
            }
        ]
    },
}


class ContextBuilder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def write_settings_json(self, workspace: Path) -> None:
        """Write .claude/settings.json to workspace."""
        claude_dir = workspace / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "settings.json").write_text(
            json.dumps(_SETTINGS_JSON, indent=2) + "\n"
        )

    def write_claude_md(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        task_brief: str | None = None,
    ) -> None:
        """Write CLAUDE.md to workspace with system prompt and context pointers."""
        sections = [
            f"# Agent: {agent_name}\n",
            "## System Prompt\n",
            system_prompt.strip() + "\n",
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
        ]

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

        self.write_claude_md(workspace, agent_name, system_prompt)
        self.write_settings_json(workspace)
