"""Tests for the schedule capability resolver (THR-105 Phase 4)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from runtime.orchestrator.schedule_capability import is_scheduling_enabled


def _write_config(org_root: Path, scheduling: dict | None) -> None:
    org_dir = org_root / "org"
    org_dir.mkdir(parents=True, exist_ok=True)
    config: dict = {}
    if scheduling is not None:
        config["scheduling"] = scheduling
    (org_dir / "config.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")


def test_enabled_agent_returns_true(tmp_path: Path):
    _write_config(tmp_path, {"enabled_agents": ["dev_agent", "other"]})
    assert is_scheduling_enabled(tmp_path, "dev_agent") is True


def test_agent_not_in_list_returns_false(tmp_path: Path):
    _write_config(tmp_path, {"enabled_agents": ["other_agent"]})
    assert is_scheduling_enabled(tmp_path, "dev_agent") is False


def test_empty_enabled_agents_returns_false(tmp_path: Path):
    _write_config(tmp_path, {"enabled_agents": []})
    assert is_scheduling_enabled(tmp_path, "dev_agent") is False


def test_missing_scheduling_key_returns_false(tmp_path: Path):
    _write_config(tmp_path, None)  # no scheduling key
    assert is_scheduling_enabled(tmp_path, "dev_agent") is False


def test_missing_config_file_returns_false(tmp_path: Path):
    # No config.yaml at all
    assert is_scheduling_enabled(tmp_path, "dev_agent") is False


def test_malformed_yaml_returns_false(tmp_path: Path):
    org_dir = tmp_path / "org"
    org_dir.mkdir(parents=True)
    (org_dir / "config.yaml").write_text(": bad yaml", encoding="utf-8")
    assert is_scheduling_enabled(tmp_path, "dev_agent") is False


def test_scheduling_not_dict_returns_false(tmp_path: Path):
    _write_config(tmp_path, None)
    # Overwrite with scheduling as a list, not a dict
    import yaml as _yaml
    config = {"scheduling": ["not", "a", "dict"]}
    (tmp_path / "org" / "config.yaml").write_text(
        _yaml.safe_dump(config), encoding="utf-8",
    )
    assert is_scheduling_enabled(tmp_path, "dev_agent") is False


def test_enabled_agents_not_list_returns_false(tmp_path: Path):
    _write_config(tmp_path, None)
    import yaml as _yaml
    config = {"scheduling": {"enabled_agents": "not-a-list"}}
    (tmp_path / "org" / "config.yaml").write_text(
        _yaml.safe_dump(config), encoding="utf-8",
    )
    assert is_scheduling_enabled(tmp_path, "dev_agent") is False
