"""Per-agent workspace configuration (agent.yaml).

Each agent workspace ships an agent.yaml that declares the git repos the
agent should have cloned into `workspaces/<agent>/repos/<name>/`. The daemon
reads this file during `/agents/init` to decide what to clone.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def load_agent_config(workspace: Path) -> dict:
    """Parse workspace/agent.yaml, returning {} if missing."""
    path = workspace / "agent.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def write_default_agent_config(workspace: Path) -> None:
    """Create a default agent.yaml with an empty repos map if one is missing."""
    path = workspace / "agent.yaml"
    if path.exists():
        return
    workspace.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump({"repos": {}}, default_flow_style=False))
