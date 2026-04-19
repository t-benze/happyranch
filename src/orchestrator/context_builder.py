from __future__ import annotations

import json
import logging
import shutil
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
                    "hooks": [
                        {
                            "type": "command",
                            "command": pull_cmds,
                            "once": True,
                        }
                    ],
                }
            ]
        }
    else:
        hooks = {}

    return {
        "permissions": {
            # The orchestrator's CLI (`opc …`) is the agent's only sanctioned
            # side-effect channel — report-completion, learning, etc. Pin it
            # open so callbacks can't be silently blocked by auto-mode
            # prompting. Everything else falls under Claude Code's default
            # auto-mode behavior (read-only tools run, writes/arbitrary Bash
            # prompt).
            "allow": ["Bash(opc:*)"]
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
        repo_names: list[str] | None = None,
    ) -> None:
        """Write CLAUDE.md to workspace with system prompt and context pointers.

        ``repo_names`` is accepted for API compatibility but is not listed
        inline — CLAUDE.md just points at ``agent.yaml`` as the source of
        truth so the repo list doesn't drift between the two files.
        """
        workspace.mkdir(parents=True, exist_ok=True)
        sections = [
            f"# Agent: {agent_name}\n",
            "## System Prompt\n",
            system_prompt.strip() + "\n",
            "## Available Repositories\n",
            "See `agent.yaml` in this workspace for the authoritative list of",
            "repositories cloned under `repos/`. Each is kept fresh via the",
            "PreToolUse hook in `.claude/settings.json`.\n",
            "## Persistent Files\n",
            "- `learnings.md` -- your accumulated operational learnings",
            "- `scorecard.md` -- read-only, updated by orchestrator",
            "- `recent_tasks.md` -- read-only, updated by orchestrator\n",
            "## Workflow\n",
            "Every task arrives via the orchestrator's prompt. Use the **start-task** skill",
            "(in `.claude/skills/start-task/`) to parse parameters and report completion via",
            "`opc report-completion`. Mid-task learnings go through `opc learning`.\n",
        ]
        (workspace / "CLAUDE.md").write_text("\n".join(sections))

    def _copy_skills(self, workspace: Path) -> None:
        """Copy protocol/skills/ tree into workspace/.claude/skills/."""
        src = self._settings.get_protocol_dir() / "skills"
        if not src.exists():
            return
        dst = workspace / ".claude" / "skills"
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            target = dst / child.name
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(child, target)

    def ensure_workspace_ready(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
    ) -> None:
        """Make sure an agent workspace has every file the orchestrator requires.

        Persistent files (learnings, scorecard, recent tasks) are created only if
        missing. CLAUDE.md, settings.json, and the skills tree are always
        regenerated so workspaces carried over from older code self-heal.
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
        self._copy_skills(workspace)
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
