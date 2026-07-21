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
from runtime.orchestrator.schedule_service import ScheduleService, ScheduleServiceError


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(h: int = 0, m: int = 0, day: int = 25, month: int = 7) -> datetime:
    return datetime(2026, month, day, h, m, tzinfo=timezone.utc)


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

def test_create_one_shot_success(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    record = svc.create(
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
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


def test_create_weekly_success(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"}
    fire_at = _utc(day=26, h=1)  # Sat in UTC ≈ 09:00 Asia/Shanghai
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


def test_create_rejects_invalid_one_shot_horizon(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    # 100 days in the future → beyond 90-day horizon
    far = _now() + timedelta(days=100)
    with pytest.raises(ScheduleServiceError, match="within 90 days"):
        svc.create(
            agent_name="dev_agent", team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=far, recurrence=None, timezone="UTC",
            normalized_brief="x", source_instruction="x",
            scheduling_enabled=True,
        )


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


def test_create_rejects_agent_cap_exceeded(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    # Pre-fill 20 armed schedules for dev_agent
    for i in range(20):
        db.schedules.insert(_record(
            id=f"SCHEDULE-{i + 1:03d}",
            agent_name="dev_agent",
            fire_at=_utc(day=2 + i, h=9),
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


def test_create_rejects_org_cap_exceeded(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    # Pre-fill 100 armed schedules across different agents
    for i in range(100):
        agent = f"agent_{i % 5}"
        db.schedules.insert(_record(
            id=f"SCHEDULE-{i + 1:03d}",
            agent_name=agent,
            fire_at=_utc(day=1 + (i % 28), h=9),
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

def test_get_returns_record(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    got = svc.get(r.id)
    assert got is not None
    assert got.id == r.id

    assert svc.get("SCHEDULE-999") is None


def test_list_all_and_filtered(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="a", source_instruction="a",
        scheduling_enabled=True,
    )
    svc.create(
        agent_name="qa_engineer", team="qa",
        kind=ScheduleKind.WEEKLY,
        fire_at=_utc(day=26, h=1),
        recurrence={"day": "Mon", "time": "09:00", "tz": "UTC"},
        timezone="UTC",
        normalized_brief="b", source_instruction="b",
        scheduling_enabled=True,
    )

    assert len(svc.list()) == 2
    assert len(svc.list(agent="dev_agent")) == 1
    assert svc.list(agent="dev_agent")[0].agent_name == "dev_agent"


# ── pause ────────────────────────────────────────────────────────────────

def test_pause_success(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
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

def test_cancel_success_from_armed(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
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


def test_cancel_success_from_paused(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
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

def test_edit_success_revalidates(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="old brief", source_instruction="old instruction",
        scheduling_enabled=True,
    )

    edited = svc.edit(r.id, "dev_agent",
                      fire_at=_utc(day=29, h=10),
                      timezone="Asia/Shanghai")
    assert edited.fire_at == _utc(day=29, h=10)
    assert edited.timezone == "Asia/Shanghai"
    # provenance fields unchanged
    assert edited.normalized_brief == "old brief"
    assert edited.source_instruction == "old instruction"
    # updated_at should have bumped
    assert edited.updated_at > r.updated_at

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


def test_edit_rejects_invalid_after_edit(tmp_path):
    """Editing a weekly schedule to an invalid recurrence must be rejected."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=_utc(day=26, h=1),
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

def test_edit_rejects_normalized_brief(tmp_path):
    """normalized_brief is immutable after insert in this phase."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="original brief", source_instruction="original instruction",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", normalized_brief="new")

    # Record unchanged
    got = svc.get(r.id)
    assert got.normalized_brief == "original brief"


def test_edit_rejects_source_instruction(tmp_path):
    """source_instruction is immutable after insert in this phase."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="original brief", source_instruction="original instruction",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", source_instruction="new")

    # Record unchanged
    got = svc.get(r.id)
    assert got.source_instruction == "original instruction"


def test_edit_rejects_blank_normalized_brief(tmp_path):
    """Blank normalized_brief edit is rejected and does not persist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="original brief", source_instruction="original instruction",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", normalized_brief="")

    # Record unchanged
    got = svc.get(r.id)
    assert got.normalized_brief == "original brief"


def test_edit_rejects_blank_source_instruction(tmp_path):
    """Blank source_instruction edit is rejected and does not persist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="original brief", source_instruction="original instruction",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", source_instruction="   ")

    # Record unchanged
    got = svc.get(r.id)
    assert got.source_instruction == "original instruction"


def test_edit_rejects_arbitrary_lifecycle_field_status(tmp_path):
    """Lifecycle field 'status' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", status=ScheduleStatus.FIRED)

    # Record unchanged
    got = svc.get(r.id)
    assert got.status == ScheduleStatus.ARMED


def test_edit_rejects_arbitrary_lifecycle_field_active(tmp_path):
    """Lifecycle field 'active' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", active=0)

    got = svc.get(r.id)
    assert got.active == 1


def test_edit_rejects_arbitrary_lifecycle_field_created_at(tmp_path):
    """Lifecycle field 'created_at' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    new_ts = _utc(day=1, h=0)
    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", created_at=new_ts)

    got = svc.get(r.id)
    assert got.created_at > new_ts


def test_edit_rejects_arbitrary_lifecycle_field_updated_at(tmp_path):
    """Lifecycle field 'updated_at' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", updated_at=_utc(day=1, h=0))


def test_edit_rejects_arbitrary_lifecycle_field_spawned_task_ids(tmp_path):
    """Lifecycle field 'spawned_task_ids' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", spawned_task_ids=["TASK-999"])


def test_edit_rejects_arbitrary_lifecycle_field_last_fired_at(tmp_path):
    """Lifecycle field 'last_fired_at' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", last_fired_at=_utc(day=28, h=9))


def test_edit_rejects_arbitrary_lifecycle_field_fire_count(tmp_path):
    """Lifecycle field 'fire_count' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", fire_count=5)


def test_edit_rejects_arbitrary_lifecycle_field_expires_at(tmp_path):
    """Lifecycle field 'expires_at' is rejected by edit allowlist."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )

    with pytest.raises(ScheduleServiceError, match="cannot edit these fields"):
        svc.edit(r.id, "dev_agent", expires_at=_utc(day=30, h=9))


# ── edit from paused ────────────────────────────────────────────────────

def test_edit_succeeds_from_paused(tmp_path):
    """Edit should work on PAUSED schedules."""
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    r = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )
    svc.pause(r.id, "dev_agent")

    edited = svc.edit(r.id, "dev_agent", fire_at=_utc(day=29, h=10))
    assert edited.fire_at == _utc(day=29, h=10)
    assert edited.status == ScheduleStatus.PAUSED  # status unchanged by edit


# ── expiry ───────────────────────────────────────────────────────────────

def test_create_weekly_indefinite_skips_expiry(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    rec = {"day": "Mon", "time": "09:00", "tz": "UTC"}
    record = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=_utc(day=26, h=9),
        recurrence=rec, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
        indefinite=True,
    )
    assert record.indefinite == 1
    assert record.expires_at is None


def test_create_one_shot_has_no_expiry(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    svc = ScheduleService(db)

    record = svc.create(
        agent_name="dev_agent", team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_utc(day=28, h=9),
        recurrence=None, timezone="UTC",
        normalized_brief="x", source_instruction="x",
        scheduling_enabled=True,
    )
    assert record.expires_at is None
