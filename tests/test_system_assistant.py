from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.system_assistant import (
    AssistantConfig,
    AssistantState,
    bootstrap_assistant_workspace,
    classify_assistant_state,
    load_assistant_config,
    save_assistant_config,
    system_assistant_paths,
)


def test_system_assistant_paths_are_runtime_global(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)

    assert paths.root == tmp_path / "system" / "assistant"
    assert paths.config_path == tmp_path / "system" / "assistant" / "config.json"
    assert paths.workspace == tmp_path / "system" / "assistant" / "workspace"
    assert "orgs" not in paths.root.parts


def test_classify_uninitialized_when_config_missing(tmp_path: Path) -> None:
    assert classify_assistant_state(tmp_path).state == AssistantState.UNINITIALIZED


def test_save_and_load_config_round_trips(tmp_path: Path) -> None:
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(tmp_path / "system" / "assistant" / "workspace"),
        latest_probe_results=[
            {
                "executor": "codex",
                "status": "passed",
                "command": "codex",
                "checked_at": "2026-06-08T00:00:00Z",
                "latency_ms": 12,
            }
        ],
    )

    save_assistant_config(tmp_path, cfg)

    assert load_assistant_config(tmp_path) == cfg
    assert classify_assistant_state(tmp_path).state == AssistantState.STALE_OR_BROKEN


def test_classify_stale_when_config_is_invalid(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.root.mkdir(parents=True)
    paths.config_path.write_text("{invalid json")

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant config is invalid"
    assert status.latest_probe_results == []


def test_classify_stale_when_workspace_path_does_not_match(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="codex")
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(tmp_path / "system" / "assistant" / "other-workspace"),
    )

    save_assistant_config(tmp_path, cfg)
    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant workspace path does not match runtime"


def test_classify_stale_when_executor_is_invalid(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.root.mkdir(parents=True)
    paths.config_path.write_text(
        json.dumps(
            {
                "selected_executor": "bogus",
                "selected_command": "bogus",
                "workspace_path": str(paths.workspace),
                "latest_probe_results": [],
            }
        )
        + "\n"
    )

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant config is invalid"


def test_classify_configured_when_workspace_matches_config(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="codex")
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(system_assistant_paths(tmp_path).workspace),
    )
    save_assistant_config(tmp_path, cfg)

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.CONFIGURED
    assert status.selected_executor == "codex"
    assert status.workspace_path == str(system_assistant_paths(tmp_path).workspace)


def test_bootstrap_codex_workspace_writes_agents_surface(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="codex")
    workspace = tmp_path / "system" / "assistant" / "workspace"

    assert (workspace / "agent.yaml").read_text().startswith("name: system_assistant\n")
    agents_md = (workspace / "AGENTS.md").read_text()
    assert "System Assistant" in agents_md
    assert "explicit user confirmation" in agents_md
    assert (workspace / "learnings" / "_index.md").exists()
    assert (workspace / "logs").is_dir()


def test_bootstrap_claude_workspace_writes_claude_surface(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="claude")

    workspace = tmp_path / "system" / "assistant" / "workspace"
    assert (workspace / "CLAUDE.md").exists()
    assert not (workspace / "AGENTS.md").exists()


def test_bootstrap_rejects_invalid_executor(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported assistant executor"):
        bootstrap_assistant_workspace(tmp_path, executor="bogus")
