from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from src.config import Settings
from src.orchestrator._paths import OrgPaths
from src.orchestrator.workspace_adapters import (
    ClaudeWorkspaceAdapter,
    CodexWorkspaceAdapter,
    OpencodeWorkspaceAdapter,
)

logger = logging.getLogger(__name__)


class ContextBuilder:
    def __init__(self, settings: Settings, paths: OrgPaths, *, slug: str) -> None:
        self._settings = settings
        self._paths = paths
        self._slug = slug
        self._claude = ClaudeWorkspaceAdapter(settings, paths, slug=slug)
        self._codex = CodexWorkspaceAdapter(settings, paths, slug=slug)
        self._opencode = OpencodeWorkspaceAdapter(settings, paths, slug=slug)

    def _adapter(self, provider: str = "claude"):
        if provider == "claude":
            return self._claude
        if provider == "codex":
            return self._codex
        if provider == "opencode":
            return self._opencode
        raise ValueError(f"unknown workspace provider: {provider}")

    def write_settings_json(self, workspace: Path, repo_names: list[str] | None = None) -> None:
        self._claude.write_settings_json(workspace, repo_names=repo_names)

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
        self._claude.write_claude_md(workspace, agent_name, system_prompt, repo_names=repo_names)

    def write_agents_md(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        repo_names: list[str] | None = None,
    ) -> None:
        self._codex.write_agents_md(workspace, agent_name, system_prompt, repo_names=repo_names)

    def ensure_workspace_ready(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        provider: str = "claude",
    ) -> None:
        """Make sure an agent workspace has every file the orchestrator requires."""
        self._adapter(provider).ensure_workspace_ready(workspace, agent_name, system_prompt)

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
