from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class AssistantState(StrEnum):
    UNINITIALIZED = "uninitialized"
    CONFIGURED = "configured"
    STALE_OR_BROKEN = "stale_or_broken"


class AssistantExecutor(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"
    PI = "pi"


@dataclass(frozen=True)
class SystemAssistantPaths:
    root: Path
    config_path: Path
    workspace: Path
    learnings_dir: Path
    logs_dir: Path


class AssistantConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_executor: AssistantExecutor
    selected_command: str
    workspace_path: str
    latest_probe_results: list[dict[str, Any]] = Field(default_factory=list)


class AssistantStatus(BaseModel):
    state: AssistantState
    selected_executor: AssistantExecutor | None = None
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


def _managed_dir_symlink_detail(paths: SystemAssistantPaths) -> str | None:
    managed_dirs = [
        (paths.root, "assistant root must not be a symlink"),
        (paths.workspace, "assistant workspace must not be a symlink"),
        (paths.learnings_dir, "assistant learnings directory must not be a symlink"),
        (paths.logs_dir, "assistant logs directory must not be a symlink"),
    ]
    for path, detail in managed_dirs:
        if path.is_symlink():
            return detail
    return None


def _managed_dir_invalid_detail(paths: SystemAssistantPaths) -> str | None:
    managed_dirs = [
        (paths.root, "assistant root is missing"),
        (paths.workspace, "assistant workspace is missing"),
        (paths.learnings_dir, "assistant learnings directory is missing"),
        (paths.logs_dir, "assistant logs directory is missing"),
    ]
    for path, detail in managed_dirs:
        if not path.is_dir():
            return detail
    return None


def _ensure_managed_dir(path: Path, symlink_detail: str) -> None:
    if path.is_symlink():
        raise ValueError(symlink_detail)
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(symlink_detail)
    if not path.is_dir():
        raise ValueError(f"{path} is not a directory")


def save_assistant_config(runtime_root: Path, config: AssistantConfig) -> None:
    paths = system_assistant_paths(runtime_root)
    if paths.config_path.is_symlink():
        raise ValueError("assistant config must not be a symlink")
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_text(config.model_dump_json(indent=2) + "\n")


def classify_assistant_state(runtime_root: Path) -> AssistantStatus:
    paths = system_assistant_paths(runtime_root)
    managed_symlink_detail = _managed_dir_symlink_detail(paths)
    if managed_symlink_detail is not None:
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            detail=managed_symlink_detail,
        )
    try:
        config = load_assistant_config(runtime_root)
    except (UnicodeDecodeError, ValidationError):
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            detail="assistant config is invalid",
        )
    if config is None:
        return AssistantStatus(state=AssistantState.UNINITIALIZED)
    configured_workspace = Path(config.workspace_path).expanduser().resolve(strict=False)
    expected_workspace = paths.workspace.resolve(strict=False)
    if configured_workspace != expected_workspace:
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            selected_executor=config.selected_executor,
            workspace_path=config.workspace_path,
            detail="assistant workspace path does not match runtime",
            latest_probe_results=config.latest_probe_results,
        )
    managed_invalid_detail = _managed_dir_invalid_detail(paths)
    if managed_invalid_detail is not None:
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            selected_executor=config.selected_executor,
            workspace_path=config.workspace_path,
            detail=managed_invalid_detail,
            latest_probe_results=config.latest_probe_results,
        )
    agent_path = paths.workspace / "agent.yaml"
    if agent_path.is_symlink():
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            selected_executor=config.selected_executor,
            workspace_path=config.workspace_path,
            detail="assistant bootstrap file agent.yaml must not be a symlink",
            latest_probe_results=config.latest_probe_results,
        )
    if not agent_path.exists():
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            selected_executor=config.selected_executor,
            workspace_path=config.workspace_path,
            detail="assistant agent.yaml is missing",
            latest_probe_results=config.latest_probe_results,
        )
    expected = (
        "CLAUDE.md"
        if config.selected_executor == AssistantExecutor.CLAUDE
        else "AGENTS.md"
    )
    prompt_path = paths.workspace / expected
    if prompt_path.is_symlink():
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            selected_executor=config.selected_executor,
            workspace_path=config.workspace_path,
            detail=f"assistant bootstrap file {expected} must not be a symlink",
            latest_probe_results=config.latest_probe_results,
        )
    if not prompt_path.exists():
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            selected_executor=config.selected_executor,
            workspace_path=config.workspace_path,
            detail=f"assistant bootstrap file {expected} is missing",
            latest_probe_results=config.latest_probe_results,
        )
    learnings_index = paths.learnings_dir / "_index.md"
    if learnings_index.is_symlink():
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            selected_executor=config.selected_executor,
            workspace_path=config.workspace_path,
            detail="assistant learnings index must not be a symlink",
            latest_probe_results=config.latest_probe_results,
        )
    return AssistantStatus(
        state=AssistantState.CONFIGURED,
        selected_executor=config.selected_executor,
        workspace_path=config.workspace_path,
        latest_probe_results=config.latest_probe_results,
    )


def _validate_executor(executor: str | AssistantExecutor) -> AssistantExecutor:
    try:
        return AssistantExecutor(executor)
    except ValueError as exc:
        raise ValueError(f"unsupported assistant executor: {executor}") from exc


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


def _reject_symlink(path: Path, detail: str) -> None:
    if path.is_symlink():
        raise ValueError(detail)


def bootstrap_assistant_workspace(runtime_root: Path, *, executor: str) -> None:
    selected_executor = _validate_executor(executor)
    paths = system_assistant_paths(runtime_root)
    _reject_symlink(paths.root, "assistant root must not be a symlink")
    _reject_symlink(paths.workspace, "assistant workspace must not be a symlink")
    _reject_symlink(
        paths.learnings_dir,
        "assistant learnings directory must not be a symlink",
    )
    _reject_symlink(paths.logs_dir, "assistant logs directory must not be a symlink")
    _reject_symlink(
        paths.workspace / "agent.yaml",
        "assistant bootstrap file agent.yaml must not be a symlink",
    )
    _reject_symlink(
        paths.workspace / "AGENTS.md",
        "assistant bootstrap file AGENTS.md must not be a symlink",
    )
    _reject_symlink(
        paths.workspace / "CLAUDE.md",
        "assistant bootstrap file CLAUDE.md must not be a symlink",
    )
    _reject_symlink(
        paths.learnings_dir / "_index.md",
        "assistant learnings index must not be a symlink",
    )
    _ensure_managed_dir(paths.root, "assistant root must not be a symlink")
    _ensure_managed_dir(paths.workspace, "assistant workspace must not be a symlink")
    _ensure_managed_dir(
        paths.learnings_dir,
        "assistant learnings directory must not be a symlink",
    )
    _ensure_managed_dir(paths.logs_dir, "assistant logs directory must not be a symlink")
    (paths.workspace / "agent.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "system_assistant",
                "executor": selected_executor.value,
                "repos": {},
            },
            sort_keys=False,
        )
    )
    if not (paths.learnings_dir / "_index.md").exists():
        (paths.learnings_dir / "_index.md").write_text("# Learnings: system_assistant\n\n")
    prompt = _assistant_prompt()
    claude_path = paths.workspace / "CLAUDE.md"
    agents_path = paths.workspace / "AGENTS.md"
    if selected_executor == AssistantExecutor.CLAUDE:
        agents_path.unlink(missing_ok=True)
        claude_path.write_text(prompt)
    else:
        claude_path.unlink(missing_ok=True)
        agents_path.write_text(prompt)
