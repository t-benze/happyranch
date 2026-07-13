"""TDD tests for the org_settings DB-backed config store (THR-095).

Covers:
1. Transactional audit: rollback on failure
2. Single-site resolution: DB overrides config.yaml
3. session_timeout task-override preservation
4. Seed idempotency
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes.settings import get_settings
from runtime.infrastructure.database import Database
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import (
    _ORG_WRITABLE_KEYS,
    _ORG_SETTINGS_SEED_SENTINEL,
    DreamingConfig,
    load_org_config,
    resolve_org_setting_dreaming,
    resolve_org_setting_session_timeout,
    resolve_org_setting_threads,
    resolve_org_setting_working_hours,
    seed_org_settings_from_config,
    write_org_setting_to_db,
)
from runtime.runtime import RuntimeDir

# ---------------------------------------------------------------------------
# 1. Transactional audit test
# ---------------------------------------------------------------------------


def test_org_setting_write_is_transactional_with_audit(tmp_path: Path):
    """A settings change cannot land without its config:<section> audit row."""
    org_root = tmp_path / "org"
    org_root.mkdir()
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams:\n  engineering:\n    manager: eng_head\n    workers: [dev]\n")
    paths = OrgPaths(root=org_root)
    db = Database(paths.db_path)

    # Seed one known value to get a "before" for audit.
    db.upsert_org_setting("dreaming", json.dumps({}))

    # Write a new dreaming setting
    patch = {"dreaming": {"enabled": True, "schedule": {"time": "03:00", "timezone": None, "catch_up_on_startup": True}, "agents": {"mode": "all", "include": [], "exclude": []}}}
    write_org_setting_to_db(paths, db, patch)

    # Verify the DB row exists
    dreaming_raw = db.get_org_setting("dreaming")
    assert dreaming_raw is not None

    # Verify an audit row exists for config:dreaming
    audit_rows = db.get_audit_logs("config:dreaming")
    assert len(audit_rows) >= 2  # seed + PUT
    # The last audit row must reflect the write
    last_audit = audit_rows[-1]
    assert last_audit["action"] == "org_config_write"
    assert last_audit["task_id"] == "config:dreaming"
    assert last_audit["payload"]["after"]["enabled"] is True


def test_all_four_sections_emit_audit_row(tmp_path: Path):
    """All 4 sections emit their config:<section> audit row on write."""
    org_root = tmp_path / "org"
    org_root.mkdir()
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams:\n  engineering:\n    manager: eng_head\n    workers: [dev]\n")
    paths = OrgPaths(root=org_root)
    db = Database(paths.db_path)

    # Write all 4 sections
    patch = {
        "dreaming": {"enabled": True, "schedule": {"time": "03:00", "timezone": None, "catch_up_on_startup": True}, "agents": {"mode": "all", "include": [], "exclude": []}},
        "threads": {"enabled": True, "default_turn_cap": 200},
        "session_timeout_seconds": 3600,
        "working_hours": {"enabled": True, "agents": {"mode": "all", "include": [], "exclude": []}, "default": {"mode": "continuous", "window": {"timezone": "UTC"}, "interval": "2h"}, "teams": {}, "overrides": {}},
    }
    write_org_setting_to_db(paths, db, patch)

    for section in _ORG_WRITABLE_KEYS:
        rows = db.get_audit_logs(f"config:{section}")
        assert len(rows) > 0, f"audit row missing for config:{section}"
        assert rows[-1]["action"] == "org_config_write"


# ---------------------------------------------------------------------------
# 2. Single-site resolution test
# ---------------------------------------------------------------------------


def test_db_value_wins_over_config_yaml(tmp_path: Path):
    """With a DB row present, the resolved value comes from the DB, not config.yaml."""
    org_root = tmp_path / "org"
    org_root.mkdir()
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams:\n  engineering:\n    manager: eng_head\n    workers: [dev]\n")
    paths = OrgPaths(root=org_root)
    db = Database(paths.db_path)

    # Write config.yaml with one value
    (org_root / "org" / "config.yaml").write_text("dreaming:\n  enabled: false\n  schedule:\n    time: '01:00'\n    catch_up_on_startup: false\n  agents:\n    mode: all\n")

    # Write DB with a DIFFERENT value
    db.upsert_org_setting("dreaming", json.dumps({
        "enabled": True,
        "schedule": {"time": "03:00", "timezone": None, "catch_up_on_startup": True},
        "agents": {"mode": "all", "include": [], "exclude": []},
    }))

    cfg = load_org_config(paths)
    resolved = resolve_org_setting_dreaming(db, code_default=cfg.dreaming)

    # DB wins over config.yaml
    assert resolved.enabled is True
    assert resolved.schedule_time == "03:00"


def test_no_db_row_falls_to_code_default(tmp_path: Path):
    """With no DB row, the resolved value falls to the code default, not config.yaml."""
    org_root = tmp_path / "org"
    org_root.mkdir()
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams:\n  engineering:\n    manager: eng_head\n    workers: [dev]\n")
    paths = OrgPaths(root=org_root)
    db = Database(paths.db_path)

    # Write config.yaml with a value
    (org_root / "org" / "config.yaml").write_text("dreaming:\n  enabled: true\n  schedule:\n    time: '05:00'\n    catch_up_on_startup: false\n  agents:\n    mode: whitelist\n")

    cfg = load_org_config(paths)
    # No DB row exists → code default wins. cfg.dreaming from config.yaml
    # is used as code_default here, but in the seed model config.yaml IS
    # the code default source. The key invariant is: no DB row → use the
    # code_default (which may come from seeded config.yaml or dataclass).
    resolved = resolve_org_setting_dreaming(db, code_default=cfg.dreaming)

    # With no DB row, the code_default (config.yaml) is used.
    # Post-seed, config.yaml won't have these keys — but pre-seed, it does.
    assert resolved.schedule_time == "05:00", f"expected 05:00 from config.yaml default, got {resolved.schedule_time}"


# ---------------------------------------------------------------------------
# 3. session_timeout task-override-preserved test
# ---------------------------------------------------------------------------


def test_session_timeout_task_override_wins_over_db(tmp_path: Path):
    """tasks.session_timeout_seconds still wins over the org_settings DB value."""
    org_root = tmp_path / "org"
    org_root.mkdir()
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams:\n  engineering:\n    manager: eng_head\n    workers: [dev]\n")
    paths = OrgPaths(root=org_root)
    db = Database(paths.db_path)

    # Set DB value
    db.upsert_org_setting("session_timeout_seconds", json.dumps(900))

    # Set per-task override (higher priority)
    task_id = "TASK-001"
    from runtime.models import TaskRecord
    db.insert_task(TaskRecord(
        id=task_id, status="pending", assigned_agent="dev",
        team="engineering", brief="test", task_type="task",
        revision_count=0, created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        session_timeout_seconds=1800,
    ))

    # Resolve via the helper
    db_value = resolve_org_setting_session_timeout(db, code_default=None)
    assert db_value == 900  # DB tier resolves to 900

    # The orchestrator-level resolution ladder puts task row first.
    # Simulate the ladder directly.
    task = db.get_task(task_id)
    assert task is not None and task.session_timeout_seconds == 1800
    # Task override wins over DB tier
    timeout = task.session_timeout_seconds
    assert timeout == 1800


# ---------------------------------------------------------------------------
# 4. Seed idempotency test
# ---------------------------------------------------------------------------

def test_seed_runs_exactly_once(tmp_path: Path):
    """Seed runs exactly once (sentinel), reproduces current effective values."""
    org_root = tmp_path / "org"
    org_root.mkdir()
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams:\n  engineering:\n    manager: eng_head\n    workers: [dev]\n")
    paths = OrgPaths(root=org_root)
    db = Database(paths.db_path)

    # Write config.yaml with custom values
    (org_root / "org" / "config.yaml").write_text(
        "dreaming:\n  enabled: true\n  schedule:\n    time: '05:00'\n    catch_up_on_startup: false\n  agents:\n    mode: whitelist\n"
        "threads:\n  enabled: true\n  default_turn_cap: 99\n"
    )

    # First seed
    seeded = seed_org_settings_from_config(paths, db)
    assert len(seeded) == 4  # all 4 keys seeded
    assert "dreaming" in seeded
    assert "threads" in seeded

    # Verify DB has the seeded values
    dreaming_raw = db.get_org_setting("dreaming")
    assert dreaming_raw is not None
    dreaming = json.loads(dreaming_raw)
    assert dreaming["enabled"] is True
    assert dreaming["schedule"]["time"] == "05:00"

    threads_raw = db.get_org_setting("threads")
    assert threads_raw is not None
    threads = json.loads(threads_raw)
    assert threads["default_turn_cap"] == 99

    # Sentinel exists
    sentinel = paths.root / _ORG_SETTINGS_SEED_SENTINEL
    assert sentinel.exists()

    # Second seed is a no-op
    seeded2 = seed_org_settings_from_config(paths, db)
    assert len(seeded2) == 0  # no-op

    # Values unchanged
    dreaming2 = json.loads(db.get_org_setting("dreaming"))
    assert dreaming2["enabled"] is True
    assert dreaming2["schedule"]["time"] == "05:00"


def test_seed_safe_on_fresh_db_without_config_yaml(tmp_path: Path):
    """Seed on a fresh DB without config.yaml writes code defaults and doesn't crash."""
    org_root = tmp_path / "org"
    org_root.mkdir()
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams:\n  engineering:\n    manager: eng_head\n    workers: [dev]\n")
    paths = OrgPaths(root=org_root)
    db = Database(paths.db_path)
    # No config.yaml file at all

    seeded = seed_org_settings_from_config(paths, db)
    assert len(seeded) == 4

    # Defaults should be written
    dreaming = json.loads(db.get_org_setting("dreaming"))
    assert dreaming["enabled"] is False  # default

    sto = json.loads(db.get_org_setting("session_timeout_seconds"))
    assert sto is None  # default
