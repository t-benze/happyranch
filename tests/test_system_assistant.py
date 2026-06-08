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


def test_classify_stale_when_config_is_invalid_utf8(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.root.mkdir(parents=True)
    paths.config_path.write_bytes(b"\xff\xfe\x00")

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant config is invalid"
    assert status.latest_probe_results == []


def test_classify_stale_when_config_has_extra_field(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.root.mkdir(parents=True)
    paths.config_path.write_text(
        json.dumps(
            {
                "selected_executor": "codex",
                "selected_command": "codex",
                "workspace_path": str(paths.workspace),
                "latest_probe_results": [],
                "unexpected": True,
            }
        )
        + "\n"
    )

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant config is invalid"


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


def test_classify_stale_when_workspace_is_symlink(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    external_workspace = tmp_path / "external-workspace"
    external_workspace.mkdir()
    (external_workspace / "agent.yaml").write_text("name: system_assistant\n")
    (external_workspace / "AGENTS.md").write_text("# System Assistant\n")
    paths.root.mkdir(parents=True)
    paths.workspace.symlink_to(external_workspace, target_is_directory=True)
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(external_workspace),
    )

    save_assistant_config(tmp_path, cfg)
    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant workspace must not be a symlink"


def test_classify_accepts_equivalent_workspace_path(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="codex")
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(
            tmp_path / "system" / "assistant" / ".." / "assistant" / "workspace"
        ),
    )

    save_assistant_config(tmp_path, cfg)
    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.CONFIGURED


def test_classify_stale_when_agent_yaml_is_missing(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="codex")
    (system_assistant_paths(tmp_path).workspace / "agent.yaml").unlink()
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(system_assistant_paths(tmp_path).workspace),
    )

    save_assistant_config(tmp_path, cfg)
    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant agent.yaml is missing"


def test_classify_stale_when_claude_prompt_file_is_missing(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="claude")
    workspace = system_assistant_paths(tmp_path).workspace
    (workspace / "CLAUDE.md").unlink()
    cfg = AssistantConfig(
        selected_executor="claude",
        selected_command="claude",
        workspace_path=str(workspace),
    )

    save_assistant_config(tmp_path, cfg)
    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant bootstrap file CLAUDE.md is missing"


@pytest.mark.parametrize("executor", ["codex", "opencode", "pi"])
def test_classify_stale_when_agents_prompt_file_is_missing(
    tmp_path: Path, executor: str
) -> None:
    bootstrap_assistant_workspace(tmp_path, executor=executor)
    workspace = system_assistant_paths(tmp_path).workspace
    (workspace / "AGENTS.md").unlink()
    cfg = AssistantConfig(
        selected_executor=executor,
        selected_command=executor,
        workspace_path=str(workspace),
    )

    save_assistant_config(tmp_path, cfg)
    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant bootstrap file AGENTS.md is missing"


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


@pytest.mark.parametrize("executor", ["codex", "opencode", "pi"])
def test_classify_configured_when_workspace_matches_config(
    tmp_path: Path, executor: str
) -> None:
    bootstrap_assistant_workspace(tmp_path, executor=executor)
    cfg = AssistantConfig(
        selected_executor=executor,
        selected_command=executor,
        workspace_path=str(system_assistant_paths(tmp_path).workspace),
    )
    save_assistant_config(tmp_path, cfg)

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.CONFIGURED
    assert status.selected_executor == executor
    assert status.workspace_path == str(system_assistant_paths(tmp_path).workspace)


@pytest.mark.parametrize("executor", ["codex", "opencode", "pi"])
def test_bootstrap_agents_backed_workspace_writes_agents_surface(
    tmp_path: Path, executor: str
) -> None:
    bootstrap_assistant_workspace(tmp_path, executor=executor)
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


def test_bootstrap_rejects_workspace_symlink_without_writing_target(
    tmp_path: Path,
) -> None:
    paths = system_assistant_paths(tmp_path)
    external_workspace = tmp_path / "external-workspace"
    external_workspace.mkdir()
    paths.root.mkdir(parents=True)
    paths.workspace.symlink_to(external_workspace, target_is_directory=True)

    with pytest.raises(ValueError, match="assistant workspace must not be a symlink"):
        bootstrap_assistant_workspace(tmp_path, executor="codex")

    assert list(external_workspace.iterdir()) == []
