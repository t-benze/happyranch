from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class AssistantState(StrEnum):
    UNINITIALIZED = "uninitialized"
    CONFIGURED = "configured"
    STALE_OR_BROKEN = "stale_or_broken"


@dataclass(frozen=True)
class SystemAssistantPaths:
    root: Path
    config_path: Path
    workspace: Path
    learnings_dir: Path
    logs_dir: Path


class AssistantConfig(BaseModel):
    selected_executor: str
    selected_command: str
    workspace_path: str
    latest_probe_results: list[dict[str, Any]] = Field(default_factory=list)


class AssistantStatus(BaseModel):
    state: AssistantState
    selected_executor: str | None = None
    workspace_path: str | None = None
    detail: str | None = None
    latest_probe_results: list[dict[str, Any]] = Field(default_factory=list)


def system_assistant_paths(runtime_root: Path) -> SystemAssistantPaths:
    root = runtime_root / "system" / "assistant"
    workspace = root / "workspace"
    return SystemAssistantPaths(
        root=root,
        config_path=root / "config.json",
        workspace=workspace,
        learnings_dir=workspace / "learnings",
        logs_dir=workspace / "logs",
    )


def load_assistant_config(runtime_root: Path) -> AssistantConfig | None:
    path = system_assistant_paths(runtime_root).config_path
    if not path.exists():
        return None
    return AssistantConfig.model_validate_json(path.read_text())


def save_assistant_config(runtime_root: Path, config: AssistantConfig) -> None:
    paths = system_assistant_paths(runtime_root)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_text(config.model_dump_json(indent=2) + "\n")


def classify_assistant_state(runtime_root: Path) -> AssistantStatus:
    paths = system_assistant_paths(runtime_root)
    config = load_assistant_config(runtime_root)
    if config is None:
        return AssistantStatus(state=AssistantState.UNINITIALIZED)
    if not paths.workspace.exists():
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            selected_executor=config.selected_executor,
            workspace_path=config.workspace_path,
            detail="assistant workspace is missing",
            latest_probe_results=config.latest_probe_results,
        )
    expected = "CLAUDE.md" if config.selected_executor == "claude" else "AGENTS.md"
    if not (paths.workspace / expected).exists():
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            selected_executor=config.selected_executor,
            workspace_path=config.workspace_path,
            detail=f"assistant bootstrap file {expected} is missing",
            latest_probe_results=config.latest_probe_results,
        )
    return AssistantStatus(
        state=AssistantState.CONFIGURED,
        selected_executor=config.selected_executor,
        workspace_path=config.workspace_path,
        latest_probe_results=config.latest_probe_results,
    )


def _assistant_prompt() -> str:
    return """# System Assistant

You are the HappyRanch system assistant. Help the founder operate HappyRanch itself:
setup, protocol explanation, runtime health, executor diagnosis, org discovery, and
guided next actions.

Authority boundary:
- Explain, inspect, and diagnose freely.
- Recommend next actions clearly.
- Run mutating HappyRanch commands only after explicit user confirmation.
- Do not silently edit runtime config, org definitions, agent files, or teams.
- Do not act as an org agent, team member, manager, or task worker.
"""


def bootstrap_assistant_workspace(runtime_root: Path, *, executor: str) -> None:
    paths = system_assistant_paths(runtime_root)
    paths.workspace.mkdir(parents=True, exist_ok=True)
    paths.learnings_dir.mkdir(parents=True, exist_ok=True)
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    (paths.workspace / "agent.yaml").write_text(
        f"name: system_assistant\nexecutor: {executor}\nrepos: {{}}\n"
    )
    if not (paths.learnings_dir / "_index.md").exists():
        (paths.learnings_dir / "_index.md").write_text("# Learnings: system_assistant\n\n")
    prompt = _assistant_prompt()
    claude_path = paths.workspace / "CLAUDE.md"
    agents_path = paths.workspace / "AGENTS.md"
    if executor == "claude":
        agents_path.unlink(missing_ok=True)
        claude_path.write_text(prompt)
    else:
        claude_path.unlink(missing_ok=True)
        agents_path.write_text(prompt)
