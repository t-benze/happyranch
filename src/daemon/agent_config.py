"""Per-agent workspace configuration (agent.yaml).

Each agent workspace ships an agent.yaml that declares the git repos the
agent should have cloned into `workspaces/<agent>/repos/<name>/`. The daemon
reads this file during `/agents/init` to decide what to clone.
"""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_EXECUTOR = "claude"


def load_agent_config(workspace: Path) -> dict:
    """Parse workspace/agent.yaml, returning {} if missing.

    Existing configs that omit `executor` still behave as if they selected
    Claude Code.
    """
    path = workspace / "agent.yaml"
    if not path.exists():
        return {}
    config = yaml.safe_load(path.read_text()) or {}
    config.setdefault("repos", {})
    config.setdefault("executor", DEFAULT_EXECUTOR)
    return config


def write_default_agent_config(workspace: Path) -> None:
    """Create a default agent.yaml with an empty repos map if one is missing."""
    path = workspace / "agent.yaml"
    if path.exists():
        return
    workspace.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump({"repos": {}, "executor": DEFAULT_EXECUTOR}, default_flow_style=False),
    )


def set_executor(workspace: Path, executor: str | None) -> None:
    """Set the workspace executor in agent.yaml."""
    config = load_agent_config(workspace)
    config["executor"] = executor or DEFAULT_EXECUTOR
    (workspace / "agent.yaml").write_text(yaml.dump(config, default_flow_style=False))


def add_repo(workspace: Path, name: str, url: str) -> None:
    """Add a repo entry to agent.yaml. Raises ValueError if name exists."""
    config = load_agent_config(workspace)
    repos = config.setdefault("repos", {})
    if name in repos:
        raise ValueError(f"repo {name!r} already exists")
    repos[name] = url
    (workspace / "agent.yaml").write_text(yaml.dump(config, default_flow_style=False))


def remove_repo(workspace: Path, name: str) -> None:
    """Remove a repo entry from agent.yaml. Raises KeyError if not found."""
    config = load_agent_config(workspace)
    repos = config.get("repos", {})
    if name not in repos:
        raise KeyError(name)
    del repos[name]
    (workspace / "agent.yaml").write_text(yaml.dump(config, default_flow_style=False))


def update_repo_url(workspace: Path, name: str, url: str) -> None:
    """Change the URL for an existing repo. Raises KeyError if not found."""
    config = load_agent_config(workspace)
    repos = config.get("repos", {})
    if name not in repos:
        raise KeyError(name)
    repos[name] = url
    (workspace / "agent.yaml").write_text(yaml.dump(config, default_flow_style=False))
