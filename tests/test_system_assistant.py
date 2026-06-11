from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from runtime import system_assistant as system_assistant_module
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
    assert paths.knowledge_dir == tmp_path / "system" / "assistant" / "workspace" / "happyranch"
    assert "orgs" not in paths.root.parts


def _write_knowledge_sources(root: Path, *, marker: str = "packaged") -> None:
    for source_rel, _dest_rel in system_assistant_module._KNOWLEDGE_SOURCES:
        source = root / source_rel
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"{marker}: {source_rel}\n")


def test_bootstrap_prefers_packaged_knowledge_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package_root = tmp_path / "package-resources"
    source_root = tmp_path / "source-root"
    _write_knowledge_sources(package_root, marker="packaged")
    _write_knowledge_sources(source_root, marker="source")
    monkeypatch.setattr(
        system_assistant_module,
        "_packaged_knowledge_root",
        lambda: package_root,
    )
    monkeypatch.setattr(
        system_assistant_module,
        "_source_knowledge_root",
        lambda: source_root,
    )

    bootstrap_assistant_workspace(tmp_path / "runtime", executor="codex")

    copied_guide = (
        system_assistant_paths(tmp_path / "runtime").knowledge_dir
        / "docs"
        / "agent-guides"
        / "runtime-and-configuration.md"
    ).read_text()
    assert "packaged: docs/agent-guides/runtime-and-configuration.md" in copied_guide
    assert "source: docs/agent-guides/runtime-and-configuration.md" not in copied_guide


def test_bootstrap_fails_when_knowledge_sources_are_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_site_packages = tmp_path / "site-packages"
    fake_site_packages.mkdir()
    (fake_site_packages / "README.md").write_text("unrelated package readme\n")
    monkeypatch.setattr(system_assistant_module, "_packaged_knowledge_root", lambda: None)
    monkeypatch.setattr(
        system_assistant_module,
        "__file__",
        str(fake_site_packages / "runtime" / "system_assistant.py"),
    )

    with pytest.raises(ValueError, match="assistant knowledge sources are unavailable"):
        bootstrap_assistant_workspace(tmp_path / "runtime", executor="codex")

    assert not system_assistant_paths(tmp_path / "runtime").config_path.exists()


def test_classify_uninitialized_when_config_missing(tmp_path: Path) -> None:
    assert classify_assistant_state(tmp_path).state == AssistantState.UNINITIALIZED


def test_save_and_load_config_round_trips(tmp_path: Path) -> None:
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(tmp_path / "system" / "assistant" / "workspace"),
    )

    save_assistant_config(tmp_path, cfg)

    assert load_assistant_config(tmp_path) == cfg
    assert classify_assistant_state(tmp_path).state == AssistantState.STALE_OR_BROKEN


def test_save_config_rejects_symlink_without_writing_target(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.root.mkdir(parents=True)
    target = tmp_path / "external-config.json"
    target.write_text("keep me\n")
    paths.config_path.symlink_to(target)
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )

    with pytest.raises(ValueError, match="assistant config must not be a symlink"):
        save_assistant_config(tmp_path, cfg)

    assert target.read_text() == "keep me\n"


def test_save_config_rejects_directory_config_path(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.config_path.mkdir(parents=True)
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )

    with pytest.raises(ValueError, match="assistant config must be a regular file"):
        save_assistant_config(tmp_path, cfg)


@pytest.mark.parametrize(
    ("directory_name", "detail"),
    [
        ("system", "assistant system directory is not a directory"),
        ("root", "assistant root is not a directory"),
        ("workspace", "assistant workspace is not a directory"),
        ("learnings", "assistant learnings directory is not a directory"),
        ("logs", "assistant logs directory is not a directory"),
    ],
)
def test_save_config_rejects_regular_file_managed_path(
    tmp_path: Path, directory_name: str, detail: str
) -> None:
    paths = system_assistant_paths(tmp_path)
    if directory_name == "system":
        managed_path = paths.root.parent
    elif directory_name == "root":
        paths.root.parent.mkdir()
        managed_path = paths.root
    elif directory_name == "workspace":
        paths.root.mkdir(parents=True)
        managed_path = paths.workspace
    elif directory_name == "learnings":
        paths.workspace.mkdir(parents=True)
        managed_path = paths.learnings_dir
    else:
        paths.workspace.mkdir(parents=True)
        managed_path = paths.logs_dir
    managed_path.write_text("not a directory\n")
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )

    with pytest.raises(ValueError, match=detail):
        save_assistant_config(tmp_path, cfg)


@pytest.mark.parametrize(
    ("directory_name", "detail"),
    [
        ("workspace", "assistant workspace must not be a symlink"),
        ("learnings", "assistant learnings directory must not be a symlink"),
        ("logs", "assistant logs directory must not be a symlink"),
    ],
)
def test_save_config_rejects_symlinked_existing_workspace_path(
    tmp_path: Path, directory_name: str, detail: str
) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.workspace.mkdir(parents=True)
    if directory_name == "workspace":
        shutil.rmtree(paths.workspace)
        managed_path = paths.workspace
    elif directory_name == "learnings":
        managed_path = paths.learnings_dir
    else:
        managed_path = paths.logs_dir
    external_path = tmp_path / f"external-{directory_name}"
    external_path.mkdir()
    managed_path.symlink_to(external_path, target_is_directory=True)
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )

    with pytest.raises(ValueError, match=detail):
        save_assistant_config(tmp_path, cfg)


def test_save_config_rejects_root_symlink_without_writing_target(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    external_root = tmp_path / "external-root"
    external_root.mkdir()
    paths.root.parent.mkdir(parents=True)
    paths.root.symlink_to(external_root, target_is_directory=True)
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )

    with pytest.raises(ValueError, match="assistant root must not be a symlink"):
        save_assistant_config(tmp_path, cfg)

    assert not (external_root / "config.json").exists()


def test_save_config_rejects_system_symlink_without_writing_target(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    external_system = tmp_path / "external-system"
    external_system.mkdir()
    paths.root.parent.symlink_to(external_system, target_is_directory=True)
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )

    with pytest.raises(
        ValueError,
        match="assistant system directory must not be a symlink",
    ):
        save_assistant_config(tmp_path, cfg)

    assert not (external_system / "assistant" / "config.json").exists()


def test_load_config_rejects_system_symlink(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    external_system = tmp_path / "external-system"
    external_root = external_system / "assistant"
    external_root.mkdir(parents=True)
    paths.root.parent.symlink_to(external_system, target_is_directory=True)
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )
    (external_root / "config.json").write_text(cfg.model_dump_json(indent=2) + "\n")

    with pytest.raises(
        ValueError,
        match="assistant system directory must not be a symlink",
    ):
        load_assistant_config(tmp_path)


def test_load_config_rejects_root_symlink(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    external_root = tmp_path / "external-root"
    external_root.mkdir()
    paths.root.parent.mkdir()
    paths.root.symlink_to(external_root, target_is_directory=True)
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )
    (external_root / "config.json").write_text(cfg.model_dump_json(indent=2) + "\n")

    with pytest.raises(ValueError, match="assistant root must not be a symlink"):
        load_assistant_config(tmp_path)


def test_load_config_rejects_config_symlink(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.root.mkdir(parents=True)
    target = tmp_path / "external-config.json"
    target.write_text(
        json.dumps(
            {
                "selected_executor": "codex",
                "selected_command": "codex",
                "workspace_path": str(paths.workspace),
            }
        )
        + "\n"
    )
    paths.config_path.symlink_to(target)

    with pytest.raises(ValueError, match="assistant config must not be a symlink"):
        load_assistant_config(tmp_path)


@pytest.mark.parametrize(
    ("directory_name", "detail"),
    [
        ("workspace", "assistant workspace is not a directory"),
        ("learnings", "assistant learnings directory is not a directory"),
        ("logs", "assistant logs directory is not a directory"),
    ],
)
def test_load_config_rejects_regular_file_existing_workspace_path(
    tmp_path: Path, directory_name: str, detail: str
) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.config_path.parent.mkdir(parents=True)
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )
    paths.config_path.write_text(cfg.model_dump_json(indent=2) + "\n")
    if directory_name == "workspace":
        paths.workspace.write_text("not a directory\n")
    elif directory_name == "learnings":
        paths.workspace.mkdir()
        paths.learnings_dir.write_text("not a directory\n")
    else:
        paths.workspace.mkdir()
        paths.logs_dir.write_text("not a directory\n")

    with pytest.raises(ValueError, match=detail):
        load_assistant_config(tmp_path)


@pytest.mark.parametrize(
    ("directory_name", "detail"),
    [
        ("workspace", "assistant workspace must not be a symlink"),
        ("learnings", "assistant learnings directory must not be a symlink"),
        ("logs", "assistant logs directory must not be a symlink"),
    ],
)
def test_load_config_rejects_symlinked_existing_workspace_path(
    tmp_path: Path, directory_name: str, detail: str
) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.config_path.parent.mkdir(parents=True)
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )
    paths.config_path.write_text(cfg.model_dump_json(indent=2) + "\n")
    paths.workspace.mkdir()
    if directory_name == "workspace":
        shutil.rmtree(paths.workspace)
        managed_path = paths.workspace
    elif directory_name == "learnings":
        managed_path = paths.learnings_dir
    else:
        managed_path = paths.logs_dir
    external_path = tmp_path / f"external-{directory_name}"
    external_path.mkdir()
    managed_path.symlink_to(external_path, target_is_directory=True)

    with pytest.raises(ValueError, match=detail):
        load_assistant_config(tmp_path)


def test_classify_stale_when_config_is_invalid(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.root.mkdir(parents=True)
    paths.config_path.write_text("{invalid json")

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant config is invalid"


def test_classify_stale_when_config_is_directory(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.config_path.mkdir(parents=True)

    with pytest.raises(ValueError, match="assistant config must be a regular file"):
        load_assistant_config(tmp_path)

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant config is invalid"


def test_classify_stale_when_config_is_symlink_to_valid_json(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="codex")
    paths = system_assistant_paths(tmp_path)
    target = tmp_path / "external-config.json"
    target.write_text(
        json.dumps(
            {
                "selected_executor": "codex",
                "selected_command": "codex",
                "workspace_path": str(paths.workspace),
            }
        )
        + "\n"
    )
    paths.config_path.symlink_to(target)

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant config is invalid"


def test_classify_stale_when_config_is_dangling_symlink(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.root.mkdir(parents=True)
    paths.config_path.symlink_to(tmp_path / "missing-config.json")

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant config is invalid"


def test_classify_stale_when_config_is_invalid_utf8(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.root.mkdir(parents=True)
    paths.config_path.write_bytes(b"\xff\xfe\x00")

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant config is invalid"


def test_classify_stale_when_config_has_extra_field(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.root.mkdir(parents=True)
    paths.config_path.write_text(
        json.dumps(
            {
                "selected_executor": "codex",
                "selected_command": "codex",
                "workspace_path": str(paths.workspace),
                "unexpected": True,
            }
        )
        + "\n"
    )

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant config is invalid"


def test_classify_stale_when_workspace_path_is_malformed(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.root.mkdir(parents=True)
    paths.config_path.write_text(
        json.dumps(
            {
                "selected_executor": "codex",
                "selected_command": "codex",
                "workspace_path": "bad\u0000path",
            }
        )
        + "\n"
    )

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant config is invalid"


def test_classify_stale_when_workspace_path_has_unknown_user(
    tmp_path: Path,
) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.root.mkdir(parents=True)
    paths.config_path.write_text(
        json.dumps(
            {
                "selected_executor": "codex",
                "selected_command": "codex",
                "workspace_path": "~definitely_missing_user/workspace",
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
    paths.config_path.write_text(cfg.model_dump_json(indent=2) + "\n")

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant workspace must not be a symlink"


def test_classify_stale_when_system_directory_is_symlink(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    external_system = tmp_path / "external-system"
    external_system.mkdir()
    paths.root.parent.symlink_to(external_system, target_is_directory=True)
    external_root = external_system / "assistant"
    external_workspace = external_root / "workspace"
    external_workspace.mkdir(parents=True)
    (external_workspace / "agent.yaml").write_text("name: system_assistant\n")
    (external_workspace / "AGENTS.md").write_text("# System Assistant\n")
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )
    (external_root / "config.json").write_text(cfg.model_dump_json(indent=2) + "\n")

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant system directory must not be a symlink"


@pytest.mark.parametrize(
    ("directory_name", "detail"),
    [
        ("system", "assistant system directory is not a directory"),
        ("root", "assistant root is not a directory"),
        ("workspace", "assistant workspace is not a directory"),
        ("learnings", "assistant learnings directory is not a directory"),
        ("logs", "assistant logs directory is not a directory"),
    ],
)
def test_classify_stale_when_managed_path_is_regular_file(
    tmp_path: Path, directory_name: str, detail: str
) -> None:
    paths = system_assistant_paths(tmp_path)
    if directory_name == "system":
        managed_path = paths.root.parent
    elif directory_name == "root":
        paths.root.parent.mkdir()
        managed_path = paths.root
    elif directory_name == "workspace":
        paths.root.mkdir(parents=True)
        managed_path = paths.workspace
    elif directory_name == "learnings":
        paths.workspace.mkdir(parents=True)
        managed_path = paths.learnings_dir
    else:
        paths.workspace.mkdir(parents=True)
        managed_path = paths.logs_dir
    managed_path.write_text("not a directory\n")

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == detail


@pytest.mark.parametrize(
    ("directory_name", "detail"),
    [
        ("root", "assistant root must not be a symlink"),
        ("learnings_dir", "assistant learnings directory must not be a symlink"),
        ("logs_dir", "assistant logs directory must not be a symlink"),
    ],
)
def test_classify_stale_when_managed_directory_is_symlink(
    tmp_path: Path, directory_name: str, detail: str
) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="codex")
    paths = system_assistant_paths(tmp_path)
    directory = getattr(paths, directory_name)
    external_directory = tmp_path / f"external-{directory_name}"
    external_directory.mkdir()
    if directory_name == "root":
        root_backup = tmp_path / "root-backup"
        paths.root.rename(root_backup)
        directory.symlink_to(external_directory, target_is_directory=True)
        config_root = external_directory
        workspace = external_directory / "workspace"
        workspace.mkdir()
        (workspace / "agent.yaml").write_text("name: system_assistant\n")
        (workspace / "AGENTS.md").write_text("# System Assistant\n")
    else:
        shutil.rmtree(directory)
        directory.symlink_to(external_directory, target_is_directory=True)
        config_root = paths.root
        workspace = paths.workspace
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(workspace),
    )
    config_root.mkdir(parents=True, exist_ok=True)
    (config_root / "config.json").write_text(cfg.model_dump_json(indent=2) + "\n")

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == detail


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


@pytest.mark.parametrize("filename", ["agent.yaml", "AGENTS.md", "CLAUDE.md"])
def test_classify_stale_when_required_bootstrap_file_is_symlink(
    tmp_path: Path, filename: str
) -> None:
    executor = "claude" if filename == "CLAUDE.md" else "codex"
    bootstrap_assistant_workspace(tmp_path, executor=executor)
    workspace = system_assistant_paths(tmp_path).workspace
    target = tmp_path / f"{filename}.target"
    target.write_text("external target\n")
    (workspace / filename).unlink()
    (workspace / filename).symlink_to(target)
    cfg = AssistantConfig(
        selected_executor=executor,
        selected_command=executor,
        workspace_path=str(workspace),
    )

    save_assistant_config(tmp_path, cfg)
    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == f"assistant bootstrap file {filename} must not be a symlink"


@pytest.mark.parametrize("filename", ["agent.yaml", "AGENTS.md", "CLAUDE.md"])
def test_classify_stale_when_required_bootstrap_file_is_directory(
    tmp_path: Path, filename: str
) -> None:
    executor = "claude" if filename == "CLAUDE.md" else "codex"
    bootstrap_assistant_workspace(tmp_path, executor=executor)
    workspace = system_assistant_paths(tmp_path).workspace
    (workspace / filename).unlink()
    (workspace / filename).mkdir()
    cfg = AssistantConfig(
        selected_executor=executor,
        selected_command=executor,
        workspace_path=str(workspace),
    )

    save_assistant_config(tmp_path, cfg)
    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == f"assistant bootstrap file {filename} is not a regular file"


def test_classify_stale_when_learnings_index_is_symlink(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="codex")
    paths = system_assistant_paths(tmp_path)
    target = tmp_path / "index-target.md"
    target.write_text("external target\n")
    (paths.learnings_dir / "_index.md").unlink()
    (paths.learnings_dir / "_index.md").symlink_to(target)
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )

    save_assistant_config(tmp_path, cfg)
    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant learnings index must not be a symlink"


def test_classify_stale_when_learnings_index_is_dangling_symlink(
    tmp_path: Path,
) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="codex")
    paths = system_assistant_paths(tmp_path)
    target = tmp_path / "missing-index-target.md"
    (paths.learnings_dir / "_index.md").unlink()
    (paths.learnings_dir / "_index.md").symlink_to(target)
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )

    save_assistant_config(tmp_path, cfg)
    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant learnings index must not be a symlink"


def test_classify_stale_when_learnings_index_is_directory(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="codex")
    paths = system_assistant_paths(tmp_path)
    (paths.learnings_dir / "_index.md").unlink()
    (paths.learnings_dir / "_index.md").mkdir()
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )

    save_assistant_config(tmp_path, cfg)
    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant learnings index is not a regular file"


def test_classify_stale_when_knowledge_index_is_missing(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="codex")
    paths = system_assistant_paths(tmp_path)
    (paths.knowledge_dir / "README.md").unlink()
    cfg = AssistantConfig(
        selected_executor="codex",
        selected_command="codex",
        workspace_path=str(paths.workspace),
    )

    save_assistant_config(tmp_path, cfg)
    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant knowledge index is missing"


def test_bootstrap_rejects_nested_knowledge_symlink_without_writing_target(
    tmp_path: Path,
) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.knowledge_dir.mkdir(parents=True)
    external_docs = tmp_path / "external-docs"
    external_docs.mkdir()
    (paths.knowledge_dir / "docs").symlink_to(
        external_docs,
        target_is_directory=True,
    )

    with pytest.raises(
        ValueError,
        match="assistant knowledge directory must not be a symlink",
    ):
        bootstrap_assistant_workspace(tmp_path, executor="codex")

    assert not any(external_docs.iterdir())


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


def test_classify_stale_when_selected_command_not_found(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.root.mkdir(parents=True)
    paths.config_path.write_text(
        json.dumps(
            {
                "selected_executor": "bogus",
                "selected_command": "bogus-cli-does-not-exist",
                "workspace_path": str(paths.workspace),
            }
        )
        + "\n"
    )

    status = classify_assistant_state(tmp_path)

    assert status.state == AssistantState.STALE_OR_BROKEN
    assert status.detail == "assistant selected command not found: bogus-cli-does-not-exist"


@pytest.mark.parametrize("executor", ["claude", "codex", "opencode", "pi"])
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

    agent_yaml = yaml.safe_load((workspace / "agent.yaml").read_text())
    assert agent_yaml["name"] == "system_assistant"
    assert agent_yaml["executor"] == executor
    assert agent_yaml["repos"] == {}
    agents_md = (workspace / "AGENTS.md").read_text()
    assert "System Assistant" in agents_md
    assert "explicit user confirmation" in agents_md
    assert "happyranch/README.md" in agents_md
    knowledge_index = workspace / "happyranch" / "README.md"
    assert knowledge_index.is_file()
    knowledge = knowledge_index.read_text()
    assert "HappyRanch System Assistant Knowledge" in knowledge
    assert "runtime-and-configuration.md" in knowledge
    assert (workspace / "happyranch" / "docs" / "agent-guides" / "web-and-cli.md").is_file()
    assert (workspace / "happyranch" / "skills" / "happyranch" / "SKILL.md").is_file()
    assert (workspace / "learnings" / "_index.md").exists()
    assert (workspace / "logs").is_dir()


def test_bootstrap_claude_workspace_writes_claude_surface(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="claude")

    workspace = tmp_path / "system" / "assistant" / "workspace"
    assert (workspace / "CLAUDE.md").exists()
    assert not (workspace / "AGENTS.md").exists()


def test_bootstrap_switches_prompt_surface_from_claude_to_codex(
    tmp_path: Path,
) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="claude")
    workspace = tmp_path / "system" / "assistant" / "workspace"

    assert (workspace / "CLAUDE.md").exists()
    assert not (workspace / "AGENTS.md").exists()

    bootstrap_assistant_workspace(tmp_path, executor="codex")

    assert not (workspace / "CLAUDE.md").exists()
    assert (workspace / "AGENTS.md").exists()


def test_bootstrap_accepts_arbitrary_executor_string(tmp_path: Path) -> None:
    bootstrap_assistant_workspace(tmp_path, executor="my-custom-cli")

    workspace = system_assistant_paths(tmp_path).workspace
    agent_yaml = yaml.safe_load((workspace / "agent.yaml").read_text())
    assert agent_yaml["executor"] == "my-custom-cli"
    # Non-claude executors get the AGENTS.md prompt surface.
    assert (workspace / "AGENTS.md").is_file()
    assert not (workspace / "CLAUDE.md").exists()


def test_bootstrap_rejects_empty_executor(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="assistant executor must be a non-empty string"):
        bootstrap_assistant_workspace(tmp_path, executor="  ")


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


def test_bootstrap_rejects_system_symlink_without_writing_target(
    tmp_path: Path,
) -> None:
    paths = system_assistant_paths(tmp_path)
    external_system = tmp_path / "external-system"
    external_system.mkdir()
    paths.root.parent.symlink_to(external_system, target_is_directory=True)

    with pytest.raises(
        ValueError,
        match="assistant system directory must not be a symlink",
    ):
        bootstrap_assistant_workspace(tmp_path, executor="codex")

    assert not (external_system / "assistant" / "workspace" / "agent.yaml").exists()
    assert not (external_system / "assistant" / "workspace" / "AGENTS.md").exists()


@pytest.mark.parametrize(
    ("directory_name", "detail"),
    [
        ("system", "assistant system directory is not a directory"),
        ("root", "assistant root is not a directory"),
        ("workspace", "assistant workspace is not a directory"),
        ("learnings", "assistant learnings directory is not a directory"),
        ("logs", "assistant logs directory is not a directory"),
    ],
)
def test_bootstrap_rejects_managed_path_regular_file(
    tmp_path: Path, directory_name: str, detail: str
) -> None:
    paths = system_assistant_paths(tmp_path)
    if directory_name == "system":
        managed_path = paths.root.parent
    elif directory_name == "root":
        paths.root.parent.mkdir()
        managed_path = paths.root
    elif directory_name == "workspace":
        paths.root.mkdir(parents=True)
        managed_path = paths.workspace
    elif directory_name == "learnings":
        paths.workspace.mkdir(parents=True)
        managed_path = paths.learnings_dir
    else:
        paths.workspace.mkdir(parents=True)
        managed_path = paths.logs_dir
    managed_path.write_text("not a directory\n")

    with pytest.raises(ValueError, match=detail):
        bootstrap_assistant_workspace(tmp_path, executor="codex")


@pytest.mark.parametrize(
    ("directory_name", "detail"),
    [
        ("root", "assistant root must not be a symlink"),
        ("learnings_dir", "assistant learnings directory must not be a symlink"),
        ("logs_dir", "assistant logs directory must not be a symlink"),
    ],
)
def test_bootstrap_rejects_managed_directory_symlink_without_writing_target(
    tmp_path: Path, directory_name: str, detail: str
) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.workspace.mkdir(parents=True)
    paths.learnings_dir.mkdir(parents=True)
    paths.logs_dir.mkdir(parents=True)
    directory = getattr(paths, directory_name)
    external_directory = tmp_path / f"external-{directory_name}"
    external_directory.mkdir()
    if directory_name == "root":
        paths.root.rename(tmp_path / "root-backup")
    else:
        directory.rmdir()
    directory.symlink_to(external_directory, target_is_directory=True)

    with pytest.raises(ValueError, match=detail):
        bootstrap_assistant_workspace(tmp_path, executor="codex")

    assert list(external_directory.iterdir()) == []


@pytest.mark.parametrize(
    ("filename", "executor"),
    [
        ("agent.yaml", "codex"),
        ("AGENTS.md", "codex"),
        ("CLAUDE.md", "claude"),
        ("learnings/_index.md", "codex"),
    ],
)
def test_bootstrap_rejects_child_symlink_without_writing_target(
    tmp_path: Path, filename: str, executor: str
) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.workspace.mkdir(parents=True)
    paths.learnings_dir.mkdir(parents=True)
    target = tmp_path / "external-target"
    target.write_text("keep me\n")
    symlink = paths.workspace / filename
    symlink.parent.mkdir(parents=True, exist_ok=True)
    symlink.symlink_to(target)

    with pytest.raises(ValueError, match="must not be a symlink"):
        bootstrap_assistant_workspace(tmp_path, executor=executor)

    assert target.read_text() == "keep me\n"


@pytest.mark.parametrize(
    ("filename", "executor"),
    [
        ("agent.yaml", "codex"),
        ("AGENTS.md", "codex"),
        ("CLAUDE.md", "claude"),
    ],
)
def test_bootstrap_rejects_existing_directory_bootstrap_file(
    tmp_path: Path, filename: str, executor: str
) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.workspace.mkdir(parents=True)
    (paths.workspace / filename).mkdir()

    with pytest.raises(
        ValueError,
        match=f"assistant bootstrap file {filename} is not a regular file",
    ):
        bootstrap_assistant_workspace(tmp_path, executor=executor)


def test_bootstrap_rejects_learnings_index_directory(tmp_path: Path) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.workspace.mkdir(parents=True)
    paths.learnings_dir.mkdir(parents=True)
    (paths.learnings_dir / "_index.md").mkdir()

    with pytest.raises(
        ValueError,
        match="assistant learnings index is not a regular file",
    ):
        bootstrap_assistant_workspace(tmp_path, executor="codex")


def test_bootstrap_rejects_dangling_learnings_index_symlink_without_writing_target(
    tmp_path: Path,
) -> None:
    paths = system_assistant_paths(tmp_path)
    paths.learnings_dir.mkdir(parents=True)
    target = tmp_path / "external-index-target.md"
    (paths.learnings_dir / "_index.md").symlink_to(target)

    with pytest.raises(
        ValueError,
        match="assistant learnings index must not be a symlink",
    ):
        bootstrap_assistant_workspace(tmp_path, executor="codex")

    assert not target.exists()


def test_assistant_config_accepts_arbitrary_executor_string(tmp_path: Path) -> None:
    config = AssistantConfig(
        selected_executor="my-custom-cli",
        selected_command="my-custom-cli",
        selected_argv=["my-custom-cli"],
        workspace_path=str(tmp_path / "ws"),
    )
    assert config.selected_executor == "my-custom-cli"


def test_assistant_config_rejects_empty_executor(tmp_path: Path) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AssistantConfig(
            selected_executor="",
            selected_command="claude",
            selected_argv=["claude"],
            workspace_path=str(tmp_path / "ws"),
        )


def test_assistant_config_has_no_probe_results_field() -> None:
    assert "latest_probe_results" not in AssistantConfig.model_fields


def test_prepare_registration_workspace_writes_both_prompt_files(tmp_path: Path) -> None:
    from runtime.system_assistant import prepare_assistant_registration_workspace

    prepare_assistant_registration_workspace(tmp_path)

    paths = system_assistant_paths(tmp_path)
    claude = (paths.workspace / "CLAUDE.md").read_text()
    agents = (paths.workspace / "AGENTS.md").read_text()
    assert "happyranch assistant register --from-file" in claude
    assert claude == agents


def test_clear_assistant_config_removes_config_file(tmp_path: Path) -> None:
    from runtime.system_assistant import clear_assistant_config

    paths = system_assistant_paths(tmp_path)
    save_assistant_config(
        tmp_path,
        AssistantConfig(
            selected_executor="claude",
            selected_command="claude",
            selected_argv=["claude"],
            workspace_path=str(paths.workspace),
        ),
    )
    assert paths.config_path.exists()

    clear_assistant_config(tmp_path)
    assert not paths.config_path.exists()
    assert load_assistant_config(tmp_path) is None
