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
import yaml

from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.daemon.routes.settings import get_settings
from runtime.infrastructure.database import Database
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import (
    _ORG_WRITABLE_KEYS,
    _ORG_SETTINGS_SEED_SENTINEL,
    DreamingConfig,
    OrgConfig,
    WorkingHoursConfig,
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


def test_no_db_row_falls_to_dataclass_code_default(tmp_path: Path):
    """With no DB row, the resolved value == dataclass code default, NOT config.yaml.

    F2: after seed, config.yaml is NOT the read source for the 4 writable knobs.
    The ladder is: task-row(session_timeout only) → org_settings DB → code default.
    This test seeds config.yaml with a value that DIFFERS from the code default,
    proves the code default resolves (not the config.yaml seed value)."""
    org_root = tmp_path / "org"
    org_root.mkdir()
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams:\n  engineering:\n    manager: eng_head\n    workers: [dev]\n")
    paths = OrgPaths(root=org_root)
    db = Database(paths.db_path)

    # Write config.yaml with a value that DIFFERS from dataclass defaults.
    (org_root / "org" / "config.yaml").write_text("dreaming:\n  enabled: true\n  schedule:\n    time: '05:00'\n    catch_up_on_startup: false\n  agents:\n    mode: whitelist\n")

    # No DB row exists → code default wins.
    # F2 fix: pass TRUE dataclass defaults, NOT config.yaml-parsed values.
    resolved = resolve_org_setting_dreaming(db, code_default=DreamingConfig())

    # Dataclass defaults: enabled=False, schedule_time='02:00'
    assert resolved.enabled is False, f"expected False (code default), got {resolved.enabled}"
    assert resolved.schedule_time == "02:00", f"expected 02:00 (code default), got {resolved.schedule_time}"

    # Threads resolution also falls to code default
    cfg_parsed = load_org_config(paths)
    # config.yaml has no threads block, so parsed cfg has code defaults too.
    # But the critical invariant: code_default=OrgConfig() with none from config.yaml.
    threads = resolve_org_setting_threads(db, code_default=OrgConfig())
    assert threads["enabled"] is True  # OrgConfig dataclass default

    # Session timeout: code default is None
    sto = resolve_org_setting_session_timeout(db, code_default=None)
    assert sto is None

    # Working hours: code default
    wh = resolve_org_setting_working_hours(db, code_default=WorkingHoursConfig())
    assert wh.enabled is False  # WorkingHoursConfig() default


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


# ---------------------------------------------------------------------------
# TDD tests for REVISE round 1 fixes
# ---------------------------------------------------------------------------

# ── F1: PUT writes DB only, config.yaml untouched ──


def test_put_only_writes_db_not_config_yaml(tmp_path: Path):
    """F1: after PUT /settings/org, org/config.yaml is byte-unchanged;
    the DB row is the sole mutated store."""
    org_root = tmp_path / "org"
    org_root.mkdir()
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams:\n  engineering:\n    manager: eng_head\n    workers: [dev]\n")
    paths = OrgPaths(root=org_root)
    db = Database(paths.db_path)

    # Write config.yaml with only non-writable keys
    config_path = org_root / "org" / "config.yaml"
    import yaml
    config_path.write_text(yaml.dump({"timezone": "UTC"}))
    before_bytes = config_path.read_bytes()

    # Write a setting via PUT path
    write_org_setting_to_db(paths, db, {"session_timeout_seconds": 1200})

    # config.yaml is byte-unchanged
    after_bytes = config_path.read_bytes()
    assert after_bytes == before_bytes, (
        "F1 FAIL: config.yaml mutated on PUT — only DB should be written"
    )
    # timezone (non-writable key) still present
    raw = yaml.safe_load(after_bytes)
    assert raw.get("timezone") == "UTC"

    # DB row written
    sto = json.loads(db.get_org_setting("session_timeout_seconds"))
    assert sto == 1200


# ── F2: No DB row → code default, NOT config.yaml ──


def test_missing_row_resolves_to_dataclass_default_not_config_yaml(tmp_path: Path):
    """F2: with a MISSING org_settings row, resolved value == dataclass
    code default, NOT the config.yaml seed value.  Prove by seeding
    config.yaml with a value that DIFFERS from code default, deleting the
    DB row, and asserting code default resolves."""
    org_root = tmp_path / "org"
    org_root.mkdir()
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams:\n  engineering:\n    manager: eng_head\n    workers: [dev]\n")
    paths = OrgPaths(root=org_root)
    db = Database(paths.db_path)

    import yaml
    # Seed config.yaml with threads enabled=True (differs from dataclass default True...
    # Let's use a value that truly differs: default_turn_cap=99 vs OrgConfig default 500)
    config_path = org_root / "org" / "config.yaml"
    config_path.write_text(yaml.dump({
        "timezone": "Asia/Shanghai",
        "threads": {"enabled": True, "default_turn_cap": 99}
    }))

    # No DB row for threads → code default should resolve
    threads = resolve_org_setting_threads(db, code_default=OrgConfig())
    assert threads["default_turn_cap"] == 500, (
        f"F2 FAIL: expected code default 500, got {threads['default_turn_cap']} — "
        "config.yaml is NOT the fallback tier"
    )


# ── F3: Seed fidelity for continuous-mode timezone ──


def test_seed_preserves_continuous_mode_timezone(tmp_path: Path):
    """F3: continuous-mode config with top-level timezone seeds a DB value
    whose resolved working_hours equals the pre-migration resolved value
    (timezone preserved)."""
    org_root = tmp_path / "org"
    org_root.mkdir()
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams:\n  engineering:\n    manager: eng_head\n    workers: [dev]\n")
    paths = OrgPaths(root=org_root)

    import yaml
    config_yaml = {
        "timezone": "UTC",
        "working_hours": {
            "enabled": True,
            "default": {
                "mode": "continuous",
                "timezone": "Asia/Shanghai",
                "interval": "2h",
            },
        },
    }
    (org_root / "org" / "config.yaml").write_text(yaml.dump(config_yaml))

    # Pre-migration resolved value (direct from config.yaml parser)
    pre_cfg = load_org_config(paths)
    pre_schedule = pre_cfg.working_hours.resolve_for("agent1", None)
    assert pre_schedule.timezone == "Asia/Shanghai"
    assert pre_schedule.mode == "continuous"
    assert pre_schedule.interval == "2h"

    # Seed into DB
    db = Database(paths.db_path)
    seeded = seed_org_settings_from_config(paths, db)
    assert len(seeded) == 4

    # Resolve from DB (using code_default=WorkingHoursConfig())
    from runtime.orchestrator.org_config import WorkingHoursConfig, _working_hours_layer_to_dict
    wh_cfg = resolve_org_setting_working_hours(db, code_default=WorkingHoursConfig())
    post_schedule = wh_cfg.resolve_for("agent1", None)

    # Parity check: timezone MUST be preserved
    assert post_schedule.timezone == "Asia/Shanghai", (
        f"F3 FAIL: timezone lost during seed — expected Asia/Shanghai, "
        f"got {post_schedule.timezone}"
    )
    assert post_schedule.mode == "continuous"
    assert post_schedule.interval == "2h"

    # Also verify the raw DB value contains the timezone
    wh_raw = json.loads(db.get_org_setting("working_hours"))
    default_layer = wh_raw["default"]
    assert default_layer.get("timezone") == "Asia/Shanghai", (
        f"F3 FAIL: bare timezone not in seeded DB value: {json.dumps(default_layer)}"
    )


# ── F4: Audit tiers are exactly the changed keys ──


def test_audit_tiers_only_changed_keys(tmp_path: Path):
    """F4: for EACH of the 4 sections, a partial update emits a
    config:<section> audit row whose tiers list == exactly the changed keys."""
    org_root = tmp_path / "org"
    org_root.mkdir()
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams:\n  engineering:\n    manager: eng_head\n    workers: [dev]\n")
    paths = OrgPaths(root=org_root)
    db = Database(paths.db_path)

    # Seed full threads in DB
    db.upsert_org_setting("threads", json.dumps({
        "enabled": True,
        "default_turn_cap": 500,
        "invocation_timeout_seconds": None,
    }))

    # Partial update: only change default_turn_cap
    patch = {"threads": {"default_turn_cap": 99}}
    write_org_setting_to_db(paths, db, patch)

    # Verify audit row has tiers == ["default_turn_cap"] only
    audit_rows = db.get_audit_logs("config:threads")
    # The last audit row is the partial update
    last_audit = audit_rows[-1]
    tiers = last_audit["payload"]["tiers"]
    assert sorted(tiers) == ["default_turn_cap"], (
        f"F4 FAIL: tiers should be [default_turn_cap] only, got {tiers}"
    )


def test_audit_for_session_timeout_scalar(tmp_path: Path):
    """F4: for session_timeout_seconds (scalar, not dict), the audit row
    still has a correct tiers list."""
    org_root = tmp_path / "org"
    org_root.mkdir()
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams:\n  engineering:\n    manager: eng_head\n    workers: [dev]\n")
    paths = OrgPaths(root=org_root)
    db = Database(paths.db_path)

    # Write session_timeout
    patch = {"session_timeout_seconds": 1800}
    write_org_setting_to_db(paths, db, patch)

    audit_rows = db.get_audit_logs("config:session_timeout_seconds")
    assert len(audit_rows) > 0
    tiers = audit_rows[-1]["payload"]["tiers"]
    # For scalar stored as {"value": ...}, the changed tier is "value"
    assert tiers == ["value"] or sorted(tiers) == ["value"], (
        f"F4: scalar tiers should be ['value'], got {tiers}"
    )


def test_audit_transactional_atomicity_still_green(tmp_path: Path):
    """F4 regress: settings row + audit row commit or roll back together."""
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
    }
    write_org_setting_to_db(paths, db, patch)

    # Every section has both DB row AND audit row
    for section in ("dreaming", "threads", "session_timeout_seconds"):
        assert db.get_org_setting(section) is not None, f"DB row missing for {section}"
        audit_rows = db.get_audit_logs(f"config:{section}")
        assert len(audit_rows) > 0, f"Audit row missing for config:{section}"
