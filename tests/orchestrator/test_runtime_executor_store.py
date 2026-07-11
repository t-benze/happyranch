"""Tests for the runtime-level executor profile store."""
from __future__ import annotations

import pytest

from runtime.orchestrator.runtime_executor_store import (
    load_runtime_profiles,
    save_runtime_profile,
)


class TestRuntimeExecutorStore:
    """Runtime executor profile store: read, write, atomicity."""

    def test_load_empty_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        profiles = load_runtime_profiles()
        assert profiles == {}

    def test_save_and_load_single_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        entry = {
            "command": "my-custom-cli",
            "argv_template": ["--prompt", "{prompt}", "--timeout", "{timeout_seconds}"],
            "adapter": "pi",
        }
        save_runtime_profile("my-executor", entry)
        profiles = load_runtime_profiles()
        assert "my-executor" in profiles
        assert profiles["my-executor"] == entry

    def test_save_and_load_multiple_profiles(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        entry_a = {
            "command": "cli-a",
            "argv_template": ["{prompt}"],
            "adapter": "claude",
        }
        entry_b = {
            "command": "cli-b",
            "argv_template": ["--prompt", "{prompt}"],
            "adapter": "pi",
        }
        save_runtime_profile("exec-a", entry_a)
        save_runtime_profile("exec-b", entry_b)
        profiles = load_runtime_profiles()
        assert profiles["exec-a"] == entry_a
        assert profiles["exec-b"] == entry_b

    def test_save_overwrites_existing_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        entry_old = {
            "command": "old-cli",
            "argv_template": ["{prompt}"],
            "adapter": "pi",
        }
        entry_new = {
            "command": "new-cli",
            "argv_template": ["--prompt", "{prompt}"],
            "adapter": "claude",
        }
        save_runtime_profile("my-executor", entry_old)
        save_runtime_profile("my-executor", entry_new)
        profiles = load_runtime_profiles()
        assert profiles["my-executor"] == entry_new

    def test_load_preserves_existing_entries_on_save(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        entry_a = {"command": "cli-a", "argv_template": ["{prompt}"], "adapter": "pi"}
        entry_b = {"command": "cli-b", "argv_template": ["{prompt}"], "adapter": "pi"}
        save_runtime_profile("exec-a", entry_a)
        save_runtime_profile("exec-b", entry_b)
        profiles = load_runtime_profiles()
        assert len(profiles) == 2
        assert profiles["exec-a"] == entry_a
        assert profiles["exec-b"] == entry_b

    def test_load_handles_corrupt_yaml_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        store_path = tmp_path / "executor_profiles.yaml"
        store_path.write_text("this is not valid yaml: [")
        profiles = load_runtime_profiles()
        assert profiles == {}

    def test_load_handles_non_dict_yaml_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        store_path = tmp_path / "executor_profiles.yaml"
        store_path.write_text("- list_item\n- another")
        profiles = load_runtime_profiles()
        assert profiles == {}
