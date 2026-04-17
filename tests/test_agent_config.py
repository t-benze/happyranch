from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.daemon.agent_config import (
    add_repo,
    load_agent_config,
    remove_repo,
    update_repo_url,
    write_default_agent_config,
)


def test_add_repo_creates_entry(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    add_repo(tmp_path, "web-app", "https://github.com/t-benze/web-app.git")
    cfg = load_agent_config(tmp_path)
    assert cfg["repos"]["web-app"] == "https://github.com/t-benze/web-app.git"


def test_add_repo_duplicate_raises(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    add_repo(tmp_path, "web-app", "https://github.com/t-benze/web-app.git")
    with pytest.raises(ValueError, match="already exists"):
        add_repo(tmp_path, "web-app", "https://other.git")


def test_add_repo_initializes_repos_if_missing(tmp_path: Path) -> None:
    """agent.yaml exists but has no repos key."""
    (tmp_path / "agent.yaml").write_text(yaml.dump({"other": "val"}))
    add_repo(tmp_path, "docs", "https://github.com/t-benze/docs.git")
    cfg = load_agent_config(tmp_path)
    assert cfg["repos"]["docs"] == "https://github.com/t-benze/docs.git"


def test_remove_repo_deletes_entry(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    add_repo(tmp_path, "web-app", "https://github.com/t-benze/web-app.git")
    remove_repo(tmp_path, "web-app")
    cfg = load_agent_config(tmp_path)
    assert "web-app" not in cfg.get("repos", {})


def test_remove_repo_nonexistent_raises(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    with pytest.raises(KeyError, match="web-app"):
        remove_repo(tmp_path, "web-app")


def test_update_repo_url_changes_url(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    add_repo(tmp_path, "web-app", "https://old.git")
    update_repo_url(tmp_path, "web-app", "https://new.git")
    cfg = load_agent_config(tmp_path)
    assert cfg["repos"]["web-app"] == "https://new.git"


def test_update_repo_url_nonexistent_raises(tmp_path: Path) -> None:
    write_default_agent_config(tmp_path)
    with pytest.raises(KeyError, match="web-app"):
        update_repo_url(tmp_path, "web-app", "https://new.git")
