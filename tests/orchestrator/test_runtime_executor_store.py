"""Tests for the runtime-level executor profile store."""
from __future__ import annotations

import logging

import pytest
import yaml

from runtime.orchestrator.runtime_executor_store import (
    load_runtime_profiles,
    migrate_legacy_org_profiles,
    remove_runtime_profile,
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


# ── THR-107: one-shot migration of legacy per-org executor_profiles ─────


def _write_org_config(tmp_path, body: str):
    config_path = tmp_path / "org" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(body)
    return config_path


class TestMigrateLegacyOrgProfiles:
    """THR-107: a legacy per-org executor_profiles block is lifted into
    the machine-global runtime store exactly once, with a loud deprecation
    warning. Collisions are logged and skipped — never a crash, never a
    silent drop."""

    def test_lifts_nonempty_block_and_warns(self, tmp_path, monkeypatch, caplog):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
        config_path = _write_org_config(tmp_path, yaml.safe_dump({
            "timezone": "Asia/Shanghai",
            "executor_profiles": {
                "openclaw": {
                    "command": "echo",
                    "argv_template": ["echo", "{prompt}"],
                    "adapter": "pi",
                },
                "customcli": {
                    "command": "printf",
                    "argv_template": ["printf", "{prompt}"],
                    "adapter": "pi",
                },
            },
        }))

        with caplog.at_level(logging.WARNING):
            migrated = migrate_legacy_org_profiles(config_path, "alpha")

        assert sorted(migrated) == ["customcli", "openclaw"]
        store = load_runtime_profiles()
        assert store["openclaw"]["command"] == "echo"
        assert store["customcli"]["command"] == "printf"
        # Loud deprecation warning names the migrated entries and the org
        text = caplog.text
        assert "openclaw" in text
        assert "customcli" in text
        assert "alpha" in text
        assert "deprecat" in text.lower() or "removed" in text.lower()

    def test_collision_logs_and_skips_never_crashes(
        self, tmp_path, monkeypatch, caplog,
    ):
        """Machine-global collision edge: the runtime store already holds a
        DIFFERENT definition for the same name (e.g. lifted from another
        org). The conflicting entry is logged + skipped; the store keeps
        the existing definition; other entries still migrate."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
        save_runtime_profile("shared", {
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        })
        config_path = _write_org_config(tmp_path, yaml.safe_dump({
            "executor_profiles": {
                "shared": {  # conflicts with the store definition
                    "command": "printf",
                    "argv_template": ["printf", "{prompt}"],
                    "adapter": "pi",
                },
                "fresh": {
                    "command": "echo",
                    "argv_template": ["echo", "{prompt}"],
                    "adapter": "pi",
                },
            },
        }))

        with caplog.at_level(logging.WARNING):
            migrated = migrate_legacy_org_profiles(config_path, "beta")

        assert migrated == ["fresh"]
        store = load_runtime_profiles()
        # Existing store definition wins — NOT overwritten
        assert store["shared"]["command"] == "echo"
        assert store["fresh"]["command"] == "echo"
        assert "shared" in caplog.text
        assert "skip" in caplog.text.lower()

    def test_identical_entry_already_in_store_is_noop(
        self, tmp_path, monkeypatch, caplog,
    ):
        """Re-running the migration (block still in config.yaml) does not
        duplicate work: identical entries are left alone, and the block
        still produces a deprecation warning — never a silent drop."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
        entry = {
            "command": "echo",
            "argv_template": ["echo", "{prompt}"],
            "adapter": "pi",
        }
        save_runtime_profile("openclaw", entry)
        config_path = _write_org_config(tmp_path, yaml.safe_dump({
            "executor_profiles": {"openclaw": dict(entry)},
        }))

        with caplog.at_level(logging.WARNING):
            migrated = migrate_legacy_org_profiles(config_path, "alpha")

        assert migrated == []
        assert load_runtime_profiles()["openclaw"] == entry
        # Block present -> still warned as deprecated (loud, not silent)
        assert "executor_profiles" in caplog.text
        assert "deprecat" in caplog.text.lower() or "removed" in caplog.text.lower()

    def test_absent_config_or_block_is_silent_noop(
        self, tmp_path, monkeypatch, caplog,
    ):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
        # 1. Config file does not exist
        with caplog.at_level(logging.WARNING):
            assert migrate_legacy_org_profiles(
                tmp_path / "org" / "config.yaml", "alpha"
            ) == []
        # 2. Config exists without the block
        config_path = _write_org_config(tmp_path, "timezone: Asia/Shanghai\n")
        with caplog.at_level(logging.WARNING):
            assert migrate_legacy_org_profiles(config_path, "alpha") == []
        # 3. Empty block
        config_path.write_text("executor_profiles: {}\n")
        with caplog.at_level(logging.WARNING):
            assert migrate_legacy_org_profiles(config_path, "alpha") == []
        assert caplog.text == ""
        assert load_runtime_profiles() == {}

    def test_malformed_block_warns_and_skips(self, tmp_path, monkeypatch, caplog):
        """A malformed legacy block (non-mapping, bad keys/values) never
        crashes and never silently drops — it warns and skips."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
        config_path = _write_org_config(tmp_path, "executor_profiles: [1, 2, 3]\n")
        with caplog.at_level(logging.WARNING):
            assert migrate_legacy_org_profiles(config_path, "alpha") == []
        assert "alpha" in caplog.text
        assert load_runtime_profiles() == {}

        # Bad entry value inside an otherwise-valid mapping: skip only it
        caplog.clear()
        config_path.write_text(yaml.safe_dump({
            "executor_profiles": {
                "good": {
                    "command": "echo",
                    "argv_template": ["echo", "{prompt}"],
                    "adapter": "pi",
                },
                "bad": "not-a-mapping",
            },
        }))
        with caplog.at_level(logging.WARNING):
            migrated = migrate_legacy_org_profiles(config_path, "alpha")
        assert migrated == ["good"]
        assert "bad" in caplog.text
        store = load_runtime_profiles()
        assert "good" in store
        assert "bad" not in store

    def test_malformed_whole_config_never_crashes(
        self, tmp_path, monkeypatch, caplog,
    ):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
        config_path = _write_org_config(tmp_path, "this is not valid yaml: [")
        with caplog.at_level(logging.WARNING):
            assert migrate_legacy_org_profiles(config_path, "alpha") == []
        assert load_runtime_profiles() == {}


# ── THR-107 S4a: remove_runtime_profile ─────────────────────────────────


class TestRemoveRuntimeProfile:
    """remove_runtime_profile: atomic removal, no-op on absent name."""

    def test_remove_existing_profile_preserves_others(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        entry_a = {"command": "cli-a", "argv_template": ["{prompt}"], "adapter": "pi"}
        entry_b = {"command": "cli-b", "argv_template": ["{prompt}"], "adapter": "pi"}
        save_runtime_profile("exec-a", entry_a)
        save_runtime_profile("exec-b", entry_b)

        remove_runtime_profile("exec-a")

        profiles = load_runtime_profiles()
        assert "exec-a" not in profiles
        assert profiles["exec-b"] == entry_b

    def test_remove_last_profile_leaves_empty_store(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        save_runtime_profile(
            "only", {"command": "cli", "argv_template": ["{prompt}"], "adapter": "pi"}
        )
        remove_runtime_profile("only")
        assert load_runtime_profiles() == {}

    def test_remove_absent_name_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        entry = {"command": "cli", "argv_template": ["{prompt}"], "adapter": "pi"}
        save_runtime_profile("keeper", entry)

        remove_runtime_profile("no-such-profile")  # must not raise

        assert load_runtime_profiles() == {"keeper": entry}

    def test_remove_when_file_missing_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        remove_runtime_profile("anything")  # must not raise
        assert load_runtime_profiles() == {}
        # No-op must not create the store file either
        assert not (tmp_path / "executor_profiles.yaml").exists()

    def test_remove_writes_valid_yaml(self, tmp_path, monkeypatch):
        """The atomic rewrite leaves a parseable YAML mapping behind."""
        monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path))
        entry = {"command": "cli", "argv_template": ["{prompt}"], "adapter": "pi"}
        save_runtime_profile("exec-a", entry)
        save_runtime_profile("exec-b", entry)

        remove_runtime_profile("exec-a")

        raw = yaml.safe_load(
            (tmp_path / "executor_profiles.yaml").read_text(encoding="utf-8")
        )
        assert raw == {"exec-b": entry}
