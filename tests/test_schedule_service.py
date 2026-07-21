"""THR-105 Phase 2: TDD tests for ScheduleService lifecycle + audit.

Tests create/list/get/pause/cancel/edit with validation, state-transition
guards, and audit trail.  Audit rows are written directly via
Database.insert_audit_log.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.infrastructure.database import Database
from runtime.models import ScheduleKind, ScheduleRecord, ScheduleStatus
from runtime.orchestrator.schedule_rules import next_weekly_occurrence
from runtime.orchestrator.schedule_service import ScheduleService, ScheduleServiceError


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(h: int = 0, m: int = 0, day: int = 25, month: int = 7) -> datetime:
    return datetime(2026, month, day, h, m, tzinfo=timezone.utc)


# ── frozen-clock helpers (date-stable tests) ────────────────────────────

_FROZEN_NOW = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def frozen_clock(monkeypatch):
    """Freeze the service and store clocks so all date-dependent
    validations and DB timestamps are date-stable."""
    monkeypatch.setattr(
        "runtime.orchestrator.schedule_service._now",
        lambda: _FROZEN_NOW,
    )
    monkeypatch.setattr(
        "runtime.infrastructure.schedule_store._now",
        lambda: _FROZEN_NOW,
    )
    return _FROZEN_NOW


def _next_weekly_frozen(day: str, time_str: str, tz: str) -> datetime:
    """Return the next weekly occurrence after _FROZEN_NOW."""
    result = next_weekly_occurrence(day, time_str, tz, after=_FROZEN_NOW)
    assert result is not None, f"no next occurrence for {day} {time_str} {tz}"
    return result


# ── helpers ──────────────────────────────────────────────────────────────

def _record(**overrides) -> ScheduleRecord:
    """Build a synthetic ScheduleRecord with defaults that pass validation."""
    base: dict = dict(
        id="SCHEDULE-001",
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        timezone="Asia/Shanghai",
        normalized_brief="Send weekly report",
        source_instruction="Every Monday send the status report",
    )
    base.update(overrides)
    return ScheduleRecord(**base)


# ── create ───────────────────────────────────────────────────────────────

def test_create_one_shot_success(tmp_path, frozen_clock):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    record = svc.create(
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None,
        timezone="Asia/Shanghai",
        normalized_brief="Follow up with customer",
        source_instruction="Follow up in 48 hours",
        scheduling_enabled=True,
    )
    assert record.id.startswith("SCHEDULE-")
    assert record.agent_name == "dev_agent"
    assert record.kind == ScheduleKind.ONE_SHOT
    assert record.status == ScheduleStatus.ARMED
    assert record.active == 1
    assert record.normalized_brief == "Follow up with customer"
    assert record.source_instruction == "Follow up in 48 hours"

    # Audit row emitted
    audit_rows = db.get_audit_logs_by_action("schedule_created")
    assert len(audit_rows) == 1
    a = audit_rows[0]
    assert a["task_id"] == record.id
    assert a["agent"] == "dev_agent"
    assert a["payload"]["kind"] == "one_shot"
    assert a["payload"]["normalized_brief"] == "Follow up with customer"


def test_create_weekly_success(tmp_path, frozen_clock):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"}
    fire_at = _next_weekly_frozen("Sat", "09:00", "Asia/Shanghai")
    record = svc.create(
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=fire_at,
        recurrence=rec,
        timezone="Asia/Shanghai",
        normalized_brief="Send weekly market update",
        source_instruction="Every Saturday, send the weekly market update",
        scheduling_enabled=True,
    )
    assert record.kind == ScheduleKind.WEEKLY
    assert record.recurrence == rec
    # Weekly must have a default 90-day expiry
    assert record.expires_at is not None
    assert record.expires_at > record.created_at

    audit_rows = db.get_audit_logs_by_action("schedule_created")
    assert len(audit_rows) == 1
    assert audit_rows[0]["payload"]["kind"] == "weekly"


# ── capability gate ──────────────────────────────────────────────────────

def test_create_rejects_when_scheduling_disabled(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    with pytest.raises(ScheduleServiceError, match="scheduling is not enabled"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=_utc(day=28, h=9),
            recurrence=None, timezone="UTC",
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=False,
        )


def test_create_rejects_when_scheduling_capability_omitted(tmp_path):
    """Omitted scheduling capability must default-deny (reviewer finding #1)."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    with pytest.raises(ScheduleServiceError, match="scheduling is not enabled"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=_utc(day=28, h=9),
            recurrence=None, timezone="UTC",
            normalized_brief="x", source_instruction="x",
            # scheduling_enabled NOT passed
        )


def test_create_rejects_when_scheduling_capability_none(tmp_path):
    """Explicit None scheduling capability must be rejected."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    with pytest.raises(ScheduleServiceError, match="scheduling is not enabled"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=_utc(day=28, h=9),
            recurrence=None, timezone="UTC",
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=None,
        )


def test_create_rejects_blank_source_instruction(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    with pytest.raises(ScheduleServiceError, match="source_instruction"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=_utc(day=28, h=9),
            recurrence=None, timezone="UTC",
            normalized_brief="x", source_instruction="   ",
            scheduling_enabled=True,
        )


def test_create_rejects_blank_normalized_brief(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    with pytest.raises(ScheduleServiceError, match="normalized_brief"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=_utc(day=28, h=9),
            recurrence=None, timezone="UTC",
            normalized_brief="", source_instruction="x",
            scheduling_enabled=True,
        )


def test_create_rejects_invalid_one_shot_horizon(tmp_path, frozen_clock):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    # 100 days in the future → beyond 90-day horizon
    far = _FROZEN_NOW + timedelta(days=100)
    with pytest.raises(ScheduleServiceError, match="within 90 days"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=far, recurrence=None, timezone="UTC",
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=True,
        )


def test_create_one_shot_rejects_past_fire_at(tmp_path, frozen_clock):
    """One-shot create with a past fire_at must be rejected and no row
    is inserted."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    past = _FROZEN_NOW - timedelta(days=7)
    with pytest.raises(ScheduleServiceError, match="fire_at must be in the future"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=past, recurrence=None, timezone="UTC",
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=True,
        )

    # No row was inserted
    assert len(svc.list()) == 0


def test_create_rejects_invalid_weekly_recurrence(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    # Missing 'tz' key
    bad_rec = {"day": "Sat", "time": "09:00"}
    with pytest.raises(ScheduleServiceError, match="recurrence"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.WEEKLY,
            fire_at=_utc(day=26, h=1),
            recurrence=bad_rec, timezone="UTC",
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=True,
        )


def test_create_rejects_unsupported_recurrence(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    # A dict with an unknown shape → rejected, not approximated
    bad_rec = {"cron": "0 9 * * 1"}
    with pytest.raises(ScheduleServiceError, match="recurrence"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.WEEKLY,
            fire_at=_utc(day=26, h=1),
            recurrence=bad_rec, timezone="UTC",
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=True,
        )


def test_create_rejects_agent_cap_exceeded(tmp_path, frozen_clock):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    # Pre-fill 20 armed schedules for dev_agent
    for i in range(20):
        db.schedules.insert(_record(
            id=f"SCHEDULE-{i + 1:03d}",
            agent_name="dev_agent",
            fire_at=_FROZEN_NOW + timedelta(days=2 + i, hours=9),
        ))
    assert db.schedules.active_count_for_agent("dev_agent") == 20

    with pytest.raises(ScheduleServiceError, match="max 20"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=_utc(day=28, h=9),
            recurrence=None, timezone="UTC",
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=True,
        )


def test_create_rejects_org_cap_exceeded(tmp_path, frozen_clock):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    # Pre-fill 100 armed schedules across different agents
    for i in range(100):
        agent = f"agent_{i % 5}"
        db.schedules.insert(_record(
            id=f"SCHEDULE-{i + 1:03d}",
            agent_name=agent,
            fire_at=_FROZEN_NOW + timedelta(days=1 + (i % 28), hours=9),
        ))
    assert db.schedules.active_count_org() == 100

    with pytest.raises(ScheduleServiceError, match="max 100"):
        svc.create(
            agent_name="new_agent", team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=_utc(day=28, h=9),
            recurrence=None, timezone="UTC",
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=True,
        )


# ── get / list ───────────────────────────────────────────────────────────

def test_get_returns_record(tmp_path, frozen_clock):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    got = svc.get(r.id)
    assert got is not None
    assert got.id == r.id

    assert svc.get("SCHEDULE-999") is None


def test_list_all_and_filtered(tmp_path, frozen_clock):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="a", source_instruction="a",
        scheduling_enabled=True,
    )
    mon_fire = _next_weekly_frozen("Mon", "09:00", "UTC")
    svc.create(
        agent_name="qa_engineer", team="qa",
        kind=ScheduleKind.WEEKLY,
        fire_at=mon_fire,
        recurrence={"day": "Mon", "time": "09:00", "tz": "UTC"},
        timezone="UTC",
        normalized_brief="b", source_instruction="b",
        scheduling_enabled=True,
    )

    assert len(svc.list()) == 2
    assert len(svc.list(agent="dev_agent")) == 1
    assert svc.list(agent="dev_agent")[0].agent_name == "dev_agent"


# ── pause ────────────────────────────────────────────────────────────────

def test_pause_success(tmp_path, frozen_clock):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    paused = svc.pause(r.id, "dev_agent")
    assert paused.status == ScheduleStatus.PAUSED
    assert paused.active == 0

    # Audit row
    audit_rows = db.get_audit_logs_by_action("schedule_paused")
    assert len(audit_rows) == 1
    assert audit_rows[0]["task_id"] == r.id
    assert audit_rows[0]["agent"] == "dev_agent"


def test_pause_rejects_terminal_state(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    # Insert a schedule already in terminal state
    db.schedules.insert(_record(
        id="SCHEDULE-001",
        status=ScheduleStatus.FIRED,
    ))

    with pytest.raises(ScheduleServiceError, match="only pause armed"):
        svc.pause("SCHEDULE-001", "dev_agent")


def test_pause_rejects_firing(tmp_path):
    """pause must reject FIRING state (reviewer finding)."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    db.schedules.insert(_record(
        id="SCHEDULE-001",
        status=ScheduleStatus.FIRING,
    ))

    with pytest.raises(ScheduleServiceError, match="only pause armed"):
        svc.pause("SCHEDULE-001", "dev_agent")


def test_pause_rejects_missing(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    with pytest.raises(ScheduleServiceError, match="not found"):
        svc.pause("SCHEDULE-999", "dev_agent")


# ── cancel ───────────────────────────────────────────────────────────────

def test_cancel_success_from_armed(tmp_path, frozen_clock):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    cancelled = svc.cancel(r.id, "dev_agent")
    assert cancelled.status == ScheduleStatus.CANCELLED
    assert cancelled.active == 0

    audit_rows = db.get_audit_logs_by_action("schedule_cancelled")
    assert len(audit_rows) == 1
    assert audit_rows[0]["task_id"] == r.id


def test_cancel_success_from_paused(tmp_path, frozen_clock):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )
    svc.pause(r.id, "dev_agent")

    # Cancel from paused should work
    cancelled = svc.cancel(r.id, "dev_agent")
    assert cancelled.status == ScheduleStatus.CANCELLED


def test_cancel_rejects_firing(tmp_path):
    """cancel must reject FIRING state (reviewer finding #2)."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    db.schedules.insert(_record(
        id="SCHEDULE-001",
        status=ScheduleStatus.FIRING,
    ))

    with pytest.raises(ScheduleServiceError, match="cannot cancel"):
        svc.cancel("SCHEDULE-001", "dev_agent")


def test_cancel_rejects_terminal_state_fired(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    db.schedules.insert(_record(
        id="SCHEDULE-001",
        status=ScheduleStatus.FIRED,
    ))

    with pytest.raises(ScheduleServiceError, match="cannot cancel"):
        svc.cancel("SCHEDULE-001", "dev_agent")


def test_cancel_rejects_terminal_state_cancelled(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    db.schedules.insert(_record(
        id="SCHEDULE-001",
        status=ScheduleStatus.CANCELLED,
    ))

    with pytest.raises(ScheduleServiceError, match="cannot cancel"):
        svc.cancel("SCHEDULE-001", "dev_agent")


def test_cancel_rejects_missing(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    with pytest.raises(ScheduleServiceError, match="not found"):
        svc.cancel("SCHEDULE-999", "dev_agent")


# ── edit ─────────────────────────────────────────────────────────────────

def test_edit_success_revalidates(tmp_path, frozen_clock):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="old brief", source_instruction="old instruction",
        scheduling_enabled=True,
    )

    edited = svc.edit(r.id, "dev_agent",
                      fire_at=_FROZEN_NOW + timedelta(days=7, hours=10),
                      timezone="Asia/Shanghai")
    assert edited.fire_at == _FROZEN_NOW + timedelta(days=7, hours=10)
    assert edited.timezone == "Asia/Shanghai"
    # provenance fields unchanged
    assert edited.normalized_brief == "old brief"
    assert edited.source_instruction == "old instruction"
    # updated_at should have bumped (may be equal under frozen clock)
    assert edited.updated_at >= r.updated_at

    # Audit row
    audit_rows = db.get_audit_logs_by_action("schedule_edited")
    assert len(audit_rows) == 1
    assert audit_rows[0]["task_id"] == r.id
    assert sorted(audit_rows[0]["payload"]["fields"]) == ["fire_at", "timezone"]


def test_edit_rejects_terminal_state(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    db.schedules.insert(_record(
        id="SCHEDULE-001",
        status=ScheduleStatus.FIRED,
    ))

    with pytest.raises(ScheduleServiceError, match="cannot edit"):
        svc.edit("SCHEDULE-001", "dev_agent",
                 fire_at=_utc(day=29, h=10))


def test_edit_rejects_firing(tmp_path):
    """edit must reject FIRING state (reviewer finding #3)."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    db.schedules.insert(_record(
        id="SCHEDULE-001",
        status=ScheduleStatus.FIRING,
    ))

    with pytest.raises(ScheduleServiceError, match="cannot edit"):
        svc.edit("SCHEDULE-001", "dev_agent",
                 fire_at=_utc(day=29, h=10))


def test_edit_rejects_invalid_after_edit(tmp_path, frozen_clock):
    """Editing a weekly schedule to an invalid recurrence must be rejected."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    mon_fire = _next_weekly_frozen("Mon", "09:00", "UTC")
    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=mon_fire,
        recurrence={"day": "Mon", "time": "09:00", "tz": "UTC"},
        timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    # Try to set an invalid recurrence
    bad_rec = {"day": "Mondayz", "time": "09:00", "tz": "UTC"}
    with pytest.raises(ScheduleServiceError, match="recurrence"):
        svc.edit(r.id, "dev_agent", recurrence=bad_rec)

    # The original record should be unchanged
    got = svc.get(r.id)
    assert got.recurrence == {"day": "Mon", "time": "09:00", "tz": "UTC"}


def test_edit_rejects_missing(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    with pytest.raises(ScheduleServiceError, match="not found"):
        svc.edit("SCHEDULE-999", "dev_agent", fire_at=_utc(day=29, h=10))


# ── edit field allowlist (reviewer findings #4, #5) ───────────────────────

def test_edit_rejects_normalized_brief(tmp_path, frozen_clock):
    """normalized_brief is a provenance field — immutable after creation."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="original brief", source_instruction="original instruction",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", normalized_brief="updated brief")

    # Row remains unchanged
    got = svc.get(r.id)
    assert got.normalized_brief == "original brief"


def test_edit_rejects_source_instruction(tmp_path, frozen_clock):
    """source_instruction is a provenance field — immutable after creation."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="original brief", source_instruction="original instruction",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", source_instruction="updated instruction")

    # Row remains unchanged
    got = svc.get(r.id)
    assert got.source_instruction == "original instruction"


def test_edit_rejects_arbitrary_lifecycle_field_status(tmp_path, frozen_clock):
    """Lifecycle field 'status' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", status=ScheduleStatus.FIRED)

    # Record unchanged
    got = svc.get(r.id)
    assert got.status == ScheduleStatus.ARMED


def test_edit_rejects_arbitrary_lifecycle_field_active(tmp_path, frozen_clock):
    """Lifecycle field 'active' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", active=0)

    got = svc.get(r.id)
    assert got.active == 1


def test_edit_rejects_arbitrary_lifecycle_field_created_at(tmp_path, frozen_clock):
    """Lifecycle field 'created_at' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    new_ts = _FROZEN_NOW - timedelta(days=21)
    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", created_at=new_ts)

    got = svc.get(r.id)
    assert got.created_at > new_ts


def test_edit_rejects_arbitrary_lifecycle_field_updated_at(tmp_path, frozen_clock):
    """Lifecycle field 'updated_at' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", updated_at=_FROZEN_NOW - timedelta(days=21))


def test_edit_rejects_arbitrary_lifecycle_field_spawned_task_ids(tmp_path, frozen_clock):
    """Lifecycle field 'spawned_task_ids' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", spawned_task_ids=["TASK-999"])


def test_edit_rejects_arbitrary_lifecycle_field_last_fired_at(tmp_path, frozen_clock):
    """Lifecycle field 'last_fired_at' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", last_fired_at=_FROZEN_NOW + timedelta(days=6, hours=9))


def test_edit_rejects_arbitrary_lifecycle_field_fire_count(tmp_path, frozen_clock):
    """Lifecycle field 'fire_count' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", fire_count=5)


def test_edit_rejects_arbitrary_lifecycle_field_expires_at(tmp_path, frozen_clock):
    """Lifecycle field 'expires_at' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", expires_at=_FROZEN_NOW + timedelta(days=8, hours=9))


# ── edit from paused ────────────────────────────────────────────────────

def test_edit_succeeds_from_paused(tmp_path, frozen_clock):
    """Edit should work on PAUSED schedules."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )
    svc.pause(r.id, "dev_agent")

    edited = svc.edit(r.id, "dev_agent", fire_at=_FROZEN_NOW + timedelta(days=7, hours=10))
    assert edited.fire_at == _FROZEN_NOW + timedelta(days=7, hours=10)
    assert edited.status == ScheduleStatus.PAUSED  # status unchanged by edit


# ── one-shot recurrence rejection (reviewer HIGH finding) ────────────

def test_create_one_shot_rejects_non_null_recurrence_weekly_shape(tmp_path):
    """One-shot with weekly-shaped recurrence must be rejected at create time."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    with pytest.raises(ScheduleServiceError, match="one-shot"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=_utc(day=28, h=9),
            recurrence={"day": "Mon", "time": "09:00", "tz": "UTC"},
            timezone="UTC",
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=True,
        )


def test_create_one_shot_rejects_cron_recurrence(tmp_path):
    """One-shot with cron/arbitrary recurrence must be rejected at create time."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    with pytest.raises(ScheduleServiceError, match="one-shot"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=_utc(day=28, h=9),
            recurrence={"cron": "* * * * *"},
            timezone="UTC",
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=True,
        )


def test_edit_one_shot_rejects_adding_recurrence(tmp_path, frozen_clock):
    """Edit must reject attaching recurrence to an existing one-shot schedule."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="one-shot"):
        svc.edit(r.id, "dev_agent", recurrence={"cron": "* * * * *"})


def test_edit_one_shot_row_unchanged_after_rejected_recurrence(tmp_path, frozen_clock):
    """After a rejected recurrence edit, the one-shot row remains unchanged."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    try:
        svc.edit(r.id, "dev_agent", recurrence={"day": "Mon", "time": "09:00", "tz": "UTC"})
    except ScheduleServiceError:
        pass

    got = svc.get(r.id)
    assert got is not None
    assert got.recurrence is None
    assert got.kind == ScheduleKind.ONE_SHOT
    assert got.fire_at == _FROZEN_NOW + timedelta(days=6, hours=9)


def test_edit_one_shot_accepts_null_recurrence(tmp_path, frozen_clock):
    """Setting recurrence=None on a one-shot is idempotent and accepted."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    edited = svc.edit(r.id, "dev_agent", recurrence=None)
    assert edited.recurrence is None
    assert edited.kind == ScheduleKind.ONE_SHOT


# ── expiry ───────────────────────────────────────────────────────────────

def test_create_weekly_indefinite_skips_expiry(tmp_path, frozen_clock):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "UTC"}
    mon_fire = _next_weekly_frozen("Mon", "09:00", "UTC")
    record = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=mon_fire,
        recurrence=rec, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
        indefinite=True,
    )
    assert record.indefinite == 1
    assert record.expires_at is None


def test_create_one_shot_has_no_expiry(tmp_path, frozen_clock):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    record = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )
    assert record.expires_at is None


# ═══════════════════════════════════════════════════════════════════════════
# Weekly fire_at normalization — reviewer findings #1, #2 regression tests
# ═══════════════════════════════════════════════════════════════════════════

# ── create: weekly fire_at must match next_weekly_occurrence ──────────

def test_create_weekly_rejects_past_fire_at(tmp_path, frozen_clock):
    """Reviewer finding #1: weekly create with a past fire_at must fail
    and the row must not appear in due/listed armed work."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Wed", "time": "09:00", "tz": "UTC"}
    # A past date relative to the frozen clock
    past = _FROZEN_NOW - timedelta(days=7)

    with pytest.raises(ScheduleServiceError, match="fire_at must match"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.WEEKLY,
            fire_at=past,
            recurrence=rec, timezone="UTC",
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=True,
        )

    # No row was inserted
    assert len(svc.list()) == 0
    assert len(db.schedules.list_due(_FROZEN_NOW)) == 0


def test_create_weekly_rejects_mismatched_weekday(tmp_path, frozen_clock):
    """Reviewer finding #1: weekly create rejects fire_at for the wrong weekday."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Sat", "time": "09:00", "tz": "UTC"}
    # A Tuesday (wrong weekday for Sat recurrence)
    tue = _FROZEN_NOW + timedelta(days=6, hours=9)  # Jul 28 is Tuesday

    with pytest.raises(ScheduleServiceError, match="fire_at must match"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.WEEKLY,
            fire_at=tue,
            recurrence=rec, timezone="UTC",
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=True,
        )

    assert len(svc.list()) == 0


def test_create_weekly_rejects_mismatched_time(tmp_path, frozen_clock):
    """Reviewer finding #1: weekly create rejects fire_at with the right
    weekday but wrong time."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "UTC"}
    # Monday 10:00 UTC is wrong time (should be 09:00)
    mon_10 = _next_weekly_frozen("Mon", "09:00", "UTC") + timedelta(hours=1)

    with pytest.raises(ScheduleServiceError, match="fire_at must match"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.WEEKLY,
            fire_at=mon_10,
            recurrence=rec, timezone="UTC",
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=True,
        )

    assert len(svc.list()) == 0


def test_create_weekly_accepts_normalized_fire_at(tmp_path, frozen_clock):
    """Weekly create succeeds when fire_at matches next_weekly_occurrence."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "UTC"}
    correct = _next_weekly_frozen("Mon", "09:00", "UTC")

    record = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=correct,
        recurrence=rec, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )
    assert record.fire_at == correct
    assert record.status == ScheduleStatus.ARMED

    # Must appear in due/listed armed work
    assert len(svc.list()) == 1
    assert len(db.schedules.list_due(_FROZEN_NOW)) == 0  # fire_at is still future


# ── edit: weekly fire_at re-validated ─────────────────────────────────

def test_edit_weekly_rejects_mismatched_fire_at(tmp_path, frozen_clock):
    """Reviewer finding #2: weekly edit with a mismatched fire_at must
    fail and leave the persisted row unchanged."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "UTC"}
    mon_fire = _next_weekly_frozen("Mon", "09:00", "UTC")
    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=mon_fire,
        recurrence=rec, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    # Try to set fire_at to Tuesday (wrong weekday)
    tue = _FROZEN_NOW + timedelta(days=6, hours=9)  # Jul 28 is Tuesday
    with pytest.raises(ScheduleServiceError, match="fire_at must match"):
        svc.edit(r.id, "dev_agent", fire_at=tue)

    # Row unchanged
    got = svc.get(r.id)
    assert got.fire_at == mon_fire
    assert got.recurrence == rec


def test_edit_weekly_accepts_valid_fire_at(tmp_path, frozen_clock):
    """Weekly edit succeeds when fire_at matches the next occurrence."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "UTC"}
    mon_fire = _next_weekly_frozen("Mon", "09:00", "UTC")
    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=mon_fire,
        recurrence=rec, timezone="UTC",
        normalized_brief="old", source_instruction="old",
        scheduling_enabled=True,
    )

    # Edit timezone only — fire_at stays the same.
    # Provenance fields are immutable and unchanged.
    edited = svc.edit(r.id, "dev_agent", timezone="UTC")
    assert edited.timezone == "UTC"
    assert edited.fire_at == mon_fire
    assert edited.normalized_brief == "old"
    assert edited.source_instruction == "old"


def test_edit_weekly_rejects_recurrence_change_without_matching_fire_at(tmp_path, frozen_clock):
    """When only recurrence changes (no explicit fire_at), the merged
    fire_at from the stored record will not match the new recurrence's
    next occurrence — this must be rejected and the row left unchanged."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "UTC"}
    mon_fire = _next_weekly_frozen("Mon", "09:00", "UTC")
    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=mon_fire,
        recurrence=rec, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    # Change day from Mon → Sat — stored fire_at (next Mon) won't match.
    new_rec = {"day": "Sat", "time": "09:00", "tz": "UTC"}
    with pytest.raises(ScheduleServiceError, match="fire_at must match"):
        svc.edit(r.id, "dev_agent", recurrence=new_rec)

    # Row unchanged
    got = svc.get(r.id)
    assert got.recurrence == rec
    assert got.fire_at == mon_fire
    assert got.timezone == "UTC"


def test_edit_weekly_rejects_recurrence_tz_change_without_timezone(tmp_path, frozen_clock):
    """When recurrence.tz changes without an explicit matching timezone,
    the merged timezone from the stored record will diverge from the new
    recurrence.tz — this must be rejected."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "UTC"}
    mon_fire = _next_weekly_frozen("Mon", "09:00", "UTC")
    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=mon_fire,
        recurrence=rec, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    # Change recurrence.tz → diverges from stored timezone.
    new_rec = {"day": "Mon", "time": "09:00", "tz": "America/New_York"}
    with pytest.raises(ScheduleServiceError, match="timezone.*must match"):
        svc.edit(r.id, "dev_agent", recurrence=new_rec)

    # Row unchanged
    got = svc.get(r.id)
    assert got.recurrence == rec
    assert got.timezone == "UTC"


def test_edit_weekly_rejects_tz_change_without_matching_fire_at(tmp_path, frozen_clock):
    """When recurrence.tz changes with a matching timezone but the caller
    does not pass a matching fire_at, the merged fire_at (from stored)
    will not match the next occurrence — must reject."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "UTC"}
    mon_fire = _next_weekly_frozen("Mon", "09:00", "UTC")
    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=mon_fire,
        recurrence=rec, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    new_rec = {"day": "Mon", "time": "09:00", "tz": "America/New_York"}
    with pytest.raises(ScheduleServiceError, match="fire_at must match"):
        svc.edit(r.id, "dev_agent",
                 recurrence=new_rec,
                 timezone="America/New_York")

    # Row unchanged
    got = svc.get(r.id)
    assert got.recurrence == rec
    assert got.fire_at == mon_fire


def test_edit_weekly_accepts_atomically_consistent_change(tmp_path, frozen_clock):
    """Positive case: caller passes recurrence, timezone, and fire_at
    that are all mutually consistent — the edit is accepted."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "UTC"}
    mon_fire = _next_weekly_frozen("Mon", "09:00", "UTC")
    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=mon_fire,
        recurrence=rec, timezone="UTC",
        normalized_brief="old", source_instruction="old",
        scheduling_enabled=True,
    )

    # Change to Saturday — pass all three fields consistently.
    new_rec = {"day": "Sat", "time": "09:00", "tz": "UTC"}
    new_fire = _next_weekly_frozen("Sat", "09:00", "UTC")
    edited = svc.edit(r.id, "dev_agent",
                      recurrence=new_rec,
                      timezone="UTC",
                      fire_at=new_fire)

    assert edited.recurrence == new_rec
    assert edited.timezone == "UTC"
    assert edited.fire_at == new_fire
    # provenance fields unchanged
    assert edited.normalized_brief == "old"
    assert edited.source_instruction == "old"


def test_edit_weekly_past_fire_at_rejected_and_row_unchanged(tmp_path, frozen_clock):
    """Reviewer finding #2: weekly edit with a past fire_at must fail
    and the row must remain unchanged."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "UTC"}
    mon_fire = _next_weekly_frozen("Mon", "09:00", "UTC")
    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=mon_fire,
        recurrence=rec, timezone="UTC",
        normalized_brief="old", source_instruction="old",
        scheduling_enabled=True,
    )

    # Try to set fire_at to a past date
    past = _FROZEN_NOW - timedelta(days=7)
    with pytest.raises(ScheduleServiceError, match="fire_at must match"):
        svc.edit(r.id, "dev_agent", fire_at=past)

    # Row unchanged
    got = svc.get(r.id)
    assert got.fire_at == mon_fire
    assert got.normalized_brief == "old"


# ═══════════════════════════════════════════════════════════════════════════
# Weekly timezone normalization — must not allow top-level timezone
# to diverge from recurrence.tz for weekly schedules.
# ═══════════════════════════════════════════════════════════════════════════

def test_create_weekly_rejects_divergent_timezone(tmp_path, frozen_clock):
    """Top-level timezone must match recurrence.tz for weekly schedules.
    Divergent payloads are rejected at create time."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "Asia/Shanghai"}
    fire_at = _next_weekly_frozen("Mon", "09:00", "Asia/Shanghai")

    with pytest.raises(ScheduleServiceError, match="timezone.*must match"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.WEEKLY,
            fire_at=fire_at,
            recurrence=rec,
            timezone="UTC",  # diverges from recurrence["tz"]=Asia/Shanghai
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=True,
        )

    # No row was inserted
    assert len(svc.list()) == 0


def test_create_weekly_derives_timezone_from_recurrence(tmp_path, frozen_clock):
    """For weekly schedules the stored timezone is derived from recurrence.tz,
    giving the founder one clear timezone to review."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "Asia/Shanghai"}
    fire_at = _next_weekly_frozen("Mon", "09:00", "Asia/Shanghai")
    record = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=fire_at,
        recurrence=rec,
        timezone="Asia/Shanghai",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )
    assert record.timezone == "Asia/Shanghai"
    assert record.recurrence["tz"] == "Asia/Shanghai"


def test_create_weekly_derives_timezone_when_omitted(tmp_path, frozen_clock):
    """When top-level timezone is empty or omitted, it is still derived
    from recurrence.tz for weekly schedules."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "America/New_York"}
    fire_at = _next_weekly_frozen("Mon", "09:00", "America/New_York")
    record = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=fire_at,
        recurrence=rec,
        timezone="",  # empty
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )
    assert record.timezone == "America/New_York"


def test_edit_weekly_rejects_divergent_timezone(tmp_path, frozen_clock):
    """Edit must reject a timezone that diverges from recurrence.tz
    for weekly schedules; the row is left unchanged."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "Asia/Shanghai"}
    mon_fire = _next_weekly_frozen("Mon", "09:00", "Asia/Shanghai")
    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=mon_fire,
        recurrence=rec, timezone="Asia/Shanghai",
        normalized_brief="old", source_instruction="old",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="timezone.*must match"):
        svc.edit(r.id, "dev_agent", timezone="UTC")

    # Row unchanged
    got = svc.get(r.id)
    assert got.timezone == "Asia/Shanghai"
    assert got.normalized_brief == "old"


def test_edit_weekly_rejects_recurrence_tz_and_timezone_mismatch(tmp_path, frozen_clock):
    """When recurrence.tz and top-level timezone are both passed but
    disagree, the edit must be rejected."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "UTC"}
    mon_fire = _next_weekly_frozen("Mon", "09:00", "UTC")
    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=mon_fire,
        recurrence=rec, timezone="UTC",
        normalized_brief="old", source_instruction="old",
        scheduling_enabled=True,
    )

    # recurrence.tz != timezone
    new_rec = {"day": "Mon", "time": "09:00", "tz": "America/New_York"}
    with pytest.raises(ScheduleServiceError, match="timezone.*must match"):
        svc.edit(r.id, "dev_agent",
                 recurrence=new_rec,
                 timezone="UTC")  # disagrees with new_rec.tz

    # Row unchanged
    got = svc.get(r.id)
    assert got.recurrence == rec
    assert got.timezone == "UTC"
    assert got.normalized_brief == "old"
