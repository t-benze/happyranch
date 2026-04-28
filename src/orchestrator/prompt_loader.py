"""File-based agent loader.

Reads agents from <runtime>/org/agents/<name>.md (active) and
<runtime>/org/agents/_pending/<name>.md (awaiting approval). Replaces
the previous protocol-markdown parser and the agent_enrollments table.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from src.orchestrator.agent_def import (
    AgentDef,
    AgentParseError,
    parse_agent_file,
    render_agent_text,
)
from src.runtime import RuntimeDir


__all__ = [
    "AgentDef",
    "AgentParseError",
    "load_agent",
    "list_agents",
    "list_pending",
    "load_pending_agent",
    "write_pending_agent",
    "approve_agent",
    "reject_agent",
    "allow_rules_for_agent",
]


def _agent_path(runtime: RuntimeDir, name: str, *, pending: bool) -> Path:
    parent = runtime.pending_agents_dir if pending else runtime.agents_dir
    return parent / f"{name}.md"


def load_agent(runtime: RuntimeDir, name: str) -> AgentDef | None:
    """Return the active agent, or None if missing.

    Pending agents are NOT returned by this function — use load_pending_agent.
    """
    path = _agent_path(runtime, name, pending=False)
    if not path.exists():
        return None
    return parse_agent_file(path)


def load_pending_agent(runtime: RuntimeDir, name: str) -> AgentDef | None:
    path = _agent_path(runtime, name, pending=True)
    if not path.exists():
        return None
    return parse_agent_file(path)


def _list_dir(directory: Path) -> list[AgentDef]:
    if not directory.exists():
        return []
    out: list[AgentDef] = []
    for entry in sorted(directory.iterdir()):
        if entry.is_file() and entry.suffix == ".md" and not entry.name.startswith("."):
            out.append(parse_agent_file(entry))
    return out


def list_agents(runtime: RuntimeDir) -> list[AgentDef]:
    """All active agents under <runtime>/org/agents/ (excluding _pending/)."""
    return _list_dir(runtime.agents_dir)


def list_pending(runtime: RuntimeDir) -> list[AgentDef]:
    return _list_dir(runtime.pending_agents_dir)


def write_pending_agent(runtime: RuntimeDir, agent: AgentDef) -> Path:
    """Atomically write a pending agent file. Overwrites if the slug is reused."""
    runtime.pending_agents_dir.mkdir(parents=True, exist_ok=True)
    target = _agent_path(runtime, agent.name, pending=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{agent.name}.", suffix=".md", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(render_agent_text(agent))
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    return target


def approve_agent(runtime: RuntimeDir, name: str) -> AgentDef:
    """Atomically move <name>.md from _pending/ to the active directory.

    Raises:
      FileNotFoundError: if no pending file exists.
      FileExistsError: if an active agent with the same name already exists
        (caller should resolve manually before retrying).
    """
    pending = _agent_path(runtime, name, pending=True)
    if not pending.exists():
        raise FileNotFoundError(f"no pending agent: {name}")
    active = _agent_path(runtime, name, pending=False)
    if active.exists():
        raise FileExistsError(f"active agent already exists: {name}")
    runtime.agents_dir.mkdir(parents=True, exist_ok=True)
    os.replace(pending, active)
    return parse_agent_file(active)


def reject_agent(runtime: RuntimeDir, name: str) -> None:
    pending = _agent_path(runtime, name, pending=True)
    if not pending.exists():
        raise FileNotFoundError(f"no pending agent: {name}")
    pending.unlink()


def allow_rules_for_agent(runtime: RuntimeDir, name: str) -> tuple[str, ...]:
    """Return the agent's declared Bash allow-rule prefixes (just the prefixes;
    Bash(...) wrapping is added by workspace_adapters._format_allow_rule).

    Returns () for an unknown agent.
    """
    agent = load_agent(runtime, name)
    if agent is None:
        return ()
    return agent.allow_rules
