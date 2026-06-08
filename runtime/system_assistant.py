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


def _managed_dir_entries(paths: SystemAssistantPaths) -> list[tuple[Path, str]]:
    return [
        (paths.root.parent, "assistant system directory"),
        (paths.root, "assistant root"),
        (paths.workspace, "assistant workspace"),
        (paths.learnings_dir, "assistant learnings directory"),
        (paths.logs_dir, "assistant logs directory"),
    ]


def _managed_dir_detail(
    paths: SystemAssistantPaths,
    *,
    require_exists: bool,
) -> str | None:
    for path, label in _managed_dir_entries(paths):
        if path.is_symlink():
            return f"{label} must not be a symlink"
        if not path.exists():
            if require_exists:
                return f"{label} is missing"
            continue
        if not path.is_dir():
            return f"{label} is not a directory"
    return None


def _managed_config_ancestor_detail(paths: SystemAssistantPaths) -> str | None:
    for path, label in _managed_dir_entries(paths)[:2]:
        if path.is_symlink():
            return f"{label} must not be a symlink"
        if path.exists() and not path.is_dir():
            return f"{label} is not a directory"
    return None


def load_assistant_config(runtime_root: Path) -> AssistantConfig | None:
    paths = system_assistant_paths(runtime_root)
    managed_detail = _managed_config_ancestor_detail(paths)
    if managed_detail is not None:
        raise ValueError(managed_detail)
    path = paths.config_path
    if path.is_symlink():
        raise ValueError("assistant config must not be a symlink")
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError("assistant config must be a regular file")
    return AssistantConfig.model_validate_json(path.read_text())


def _managed_dir_existing_invalid_detail(paths: SystemAssistantPaths) -> str | None:
    return _managed_dir_detail(paths, require_exists=False)


def _managed_dir_invalid_detail(paths: SystemAssistantPaths) -> str | None:
    return _managed_dir_detail(paths, require_exists=True)


def _bootstrap_file_invalid_detail(path: Path, filename: str) -> str | None:
    if path.is_symlink():
        return f"assistant bootstrap file {filename} must not be a symlink"
    if not path.exists():
        return f"assistant bootstrap file {filename} is missing"
    if not path.is_file():
        return f"assistant bootstrap file {filename} is not a regular file"
    return None


def _learnings_index_invalid_detail(path: Path) -> str | None:
    if not path.exists():
        return None
    if path.is_symlink():
        return "assistant learnings index must not be a symlink"
    if not path.is_file():
        return "assistant learnings index is not a regular file"
    return None


def _ensure_managed_dir(path: Path, symlink_detail: str, non_dir_detail: str) -> None:
    if path.is_symlink():
        raise ValueError(symlink_detail)
    if path.exists() and not path.is_dir():
        raise ValueError(non_dir_detail)
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(symlink_detail)
    if not path.is_dir():
        raise ValueError(non_dir_detail)


def save_assistant_config(runtime_root: Path, config: AssistantConfig) -> None:
    paths = system_assistant_paths(runtime_root)
    managed_detail = _managed_config_ancestor_detail(paths)
    if managed_detail is not None:
        raise ValueError(managed_detail)
    if paths.config_path.is_symlink():
        raise ValueError("assistant config must not be a symlink")
    _ensure_managed_dir(
        paths.root.parent,
        "assistant system directory must not be a symlink",
        "assistant system directory is not a directory",
    )
    _ensure_managed_dir(
        paths.root,
        "assistant root must not be a symlink",
        "assistant root is not a directory",
    )
    paths.config_path.write_text(config.model_dump_json(indent=2) + "\n")


def classify_assistant_state(runtime_root: Path) -> AssistantStatus:
    paths = system_assistant_paths(runtime_root)
    managed_existing_invalid_detail = _managed_dir_existing_invalid_detail(paths)
    if managed_existing_invalid_detail is not None:
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            detail=managed_existing_invalid_detail,
        )
    try:
        config = load_assistant_config(runtime_root)
    except (OSError, UnicodeDecodeError, ValueError, ValidationError):
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
    agent_invalid_detail = _bootstrap_file_invalid_detail(
        paths.workspace / "agent.yaml",
        "agent.yaml",
    )
    if agent_invalid_detail is not None:
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            selected_executor=config.selected_executor,
            workspace_path=config.workspace_path,
            detail=(
                "assistant agent.yaml is missing"
                if agent_invalid_detail == "assistant bootstrap file agent.yaml is missing"
                else agent_invalid_detail
            ),
            latest_probe_results=config.latest_probe_results,
        )
    expected = (
        "CLAUDE.md"
        if config.selected_executor == AssistantExecutor.CLAUDE
        else "AGENTS.md"
    )
    prompt_invalid_detail = _bootstrap_file_invalid_detail(
        paths.workspace / expected,
        expected,
    )
    if prompt_invalid_detail is not None:
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            selected_executor=config.selected_executor,
            workspace_path=config.workspace_path,
            detail=prompt_invalid_detail,
            latest_probe_results=config.latest_probe_results,
        )
    learnings_index_invalid_detail = _learnings_index_invalid_detail(
        paths.learnings_dir / "_index.md",
    )
    if learnings_index_invalid_detail is not None:
        return AssistantStatus(
            state=AssistantState.STALE_OR_BROKEN,
            selected_executor=config.selected_executor,
            workspace_path=config.workspace_path,
            detail=learnings_index_invalid_detail,
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


def _reject_existing_invalid_bootstrap_file(path: Path, filename: str) -> None:
    invalid_detail = _bootstrap_file_invalid_detail(path, filename)
    if invalid_detail is None or invalid_detail.endswith(" is missing"):
        return
    raise ValueError(invalid_detail)


def bootstrap_assistant_workspace(runtime_root: Path, *, executor: str) -> None:
    selected_executor = _validate_executor(executor)
    paths = system_assistant_paths(runtime_root)
    _reject_symlink(
        paths.root.parent,
        "assistant system directory must not be a symlink",
    )
    _reject_symlink(paths.root, "assistant root must not be a symlink")
    _reject_symlink(paths.workspace, "assistant workspace must not be a symlink")
    _reject_symlink(
        paths.learnings_dir,
        "assistant learnings directory must not be a symlink",
    )
    _reject_symlink(paths.logs_dir, "assistant logs directory must not be a symlink")
    _reject_existing_invalid_bootstrap_file(
        paths.workspace / "agent.yaml",
        "agent.yaml",
    )
    _reject_existing_invalid_bootstrap_file(
        paths.workspace / "AGENTS.md",
        "AGENTS.md",
    )
    _reject_existing_invalid_bootstrap_file(
        paths.workspace / "CLAUDE.md",
        "CLAUDE.md",
    )
    learnings_index_invalid_detail = _learnings_index_invalid_detail(
        paths.learnings_dir / "_index.md",
    )
    if learnings_index_invalid_detail is not None:
        raise ValueError(learnings_index_invalid_detail)
    _ensure_managed_dir(
        paths.root.parent,
        "assistant system directory must not be a symlink",
        "assistant system directory is not a directory",
    )
    _ensure_managed_dir(
        paths.root,
        "assistant root must not be a symlink",
        "assistant root is not a directory",
    )
    _ensure_managed_dir(
        paths.workspace,
        "assistant workspace must not be a symlink",
        "assistant workspace is not a directory",
    )
    _ensure_managed_dir(
        paths.learnings_dir,
        "assistant learnings directory must not be a symlink",
        "assistant learnings directory is not a directory",
    )
    _ensure_managed_dir(
        paths.logs_dir,
        "assistant logs directory must not be a symlink",
        "assistant logs directory is not a directory",
    )
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
