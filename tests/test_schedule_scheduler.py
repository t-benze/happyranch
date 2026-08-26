"""THR-105 Phase 3: TDD tests for schedule scheduler — due selection, UTC
comparisons, duplicate-fire protection, startup recovery, no weekly replay.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading

import pytest

from runtime.infrastructure.database import Database
from runtime.models import ScheduleKind, ScheduleRecord, ScheduleStatus
from runtime.orchestrator.schedule_service import ScheduleService
from runtime.daemon.schedule_scheduler import schedule_due_schedules


# ── helpers ──────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def _dt(hour: int = 12, minute: int = 0, day: int = 22) -> datetime:
    return datetime(2026, 7, day, hour, minute, tzinfo=timezone.utc)


def _schedule(db: Database, id: str = "SCHEDULE-001", **overrides) -> None:
    base: dict = dict(
        id=id,
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_dt(day=22, hour=11),  # due (before now)
        timezone="UTC",
        normalized_brief="do the thing",
        source_instruction="please do the thing",
    )
    base.update(overrides)
    db.schedules.insert(ScheduleRecord(**base))


class _FakeOrg:
    """Minimal org stub for schedule_due_schedules."""
    def __init__(self, db: Database):
        self.db = db
        self.slug = "test-org"
        from runtime.daemon.schedule_queue import ScheduleQueue
        self.schedule_queue = ScheduleQueue()


# ── due selection ───────────────────────────────────────────────────────

def test_no_due_schedules(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    _schedule(db, fire_at=_dt(day=23, hour=12))  # future, not due
    org = _FakeOrg(db)
    assert schedule_due_schedules(org=org, now=_now()) == 0


def test_due_schedule_is_claimed_and_enqueued(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    _schedule(db, fire_at=_dt(day=22, hour=11))  # past, due
    org = _FakeOrg(db)

    assert schedule_due_schedules(org=org, now=_now()) == 1
    # Claimed: status should be FIRING
    record = db.schedules.get("SCHEDULE-001")
    assert record.status == ScheduleStatus.FIRING
    # Enqueued
    assert org.schedule_queue.size == 1
    assert any(log["action"] == "schedule_claimed" for log in db.get_audit_logs("SCHEDULE-001"))


def test_due_schedule_not_double_fired(tmp_path):
    """A schedule already claimed (FIRING) is not reselected by a subsequent
    scheduler pass — duplicate-fire protection."""
    db = Database(tmp_path / "db.sqlite")
    _schedule(db, fire_at=_dt(day=22, hour=11))  # past, due
    org = _FakeOrg(db)

    assert schedule_due_schedules(org=org, now=_now()) == 1
    # Second pass: already FIRING, not ARMED, so not listed as due
    assert schedule_due_schedules(org=org, now=_now()) == 0
    assert org.schedule_queue.size == 1  # only one enqueued job


def test_concurrent_scheduler_claims_enqueue_one_job_and_audit(tmp_path, monkeypatch):
    """Two passes that both select ARMED may enqueue only the winning claim.

    The claim barrier opens the real TOCTOU window: both scheduler threads have
    completed ``list_due`` before either writes. A blind ``UPDATE ... WHERE
    id = ?`` would let both passes enqueue and audit this occurrence.
    """
    db = Database(tmp_path / "db.sqlite")
    _schedule(db)
    org = _FakeOrg(db)
    claim_barrier = threading.Barrier(2, timeout=5)
    original_claim = db.schedules.claim_firing
    counts: list[int] = []
    errors: list[BaseException] = []

    def synchronized_claim(schedule_id: str) -> bool:
        claim_barrier.wait()
        return original_claim(schedule_id)

    monkeypatch.setattr(db.schedules, "claim_firing", synchronized_claim)

    def run_scheduler() -> None:
        try:
            counts.append(schedule_due_schedules(org=org, now=_now()))
        except BaseException as exc:
            errors.append(exc)

    workers = [threading.Thread(target=run_scheduler) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=5)

    assert not errors
    assert not any(worker.is_alive() for worker in workers)
    assert sorted(counts) == [0, 1]
    assert org.schedule_queue.size == 1
    claimed = [
        log for log in db.get_audit_logs("SCHEDULE-001")
        if log["action"] == "schedule_claimed"
        and log["payload"]["fire_at"] == _dt(day=22, hour=11).isoformat()
    ]
    assert len(claimed) == 1


def test_multiple_due_schedules_fire_independently(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    _schedule(db, id="SCHEDULE-001", fire_at=_dt(day=22, hour=11))
    _schedule(db, id="SCHEDULE-002", fire_at=_dt(day=22, hour=10))
    _schedule(db, id="SCHEDULE-003", fire_at=_dt(day=22, hour=9))
    org = _FakeOrg(db)

    assert schedule_due_schedules(org=org, now=_now()) == 3
    for sid in ("SCHEDULE-001", "SCHEDULE-002", "SCHEDULE-003"):
        assert db.schedules.get(sid).status == ScheduleStatus.FIRING
    assert org.schedule_queue.size == 3


def test_paused_schedule_not_due(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    _schedule(db, status=ScheduleStatus.PAUSED, active=0, fire_at=_dt(day=22, hour=11))
    org = _FakeOrg(db)
    assert schedule_due_schedules(org=org, now=_now()) == 0


def test_cancelled_schedule_not_due(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    _schedule(db, status=ScheduleStatus.CANCELLED, active=0, fire_at=_dt(day=22, hour=11))
    org = _FakeOrg(db)
    assert schedule_due_schedules(org=org, now=_now()) == 0


def test_future_fire_at_not_due(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    _schedule(db, fire_at=_dt(day=23, hour=14))  # tomorrow
    org = _FakeOrg(db)
    assert schedule_due_schedules(org=org, now=_now()) == 0


# ── UTC due comparisons ──────────────────────────────────────────────────

def test_utc_boundary_due(tmp_path):
    """A schedule whose UTC fire_at equals now is due (<=)."""
    db = Database(tmp_path / "db.sqlite")
    now = _now()
    _schedule(db, fire_at=now)
    org = _FakeOrg(db)
    assert schedule_due_schedules(org=org, now=now) == 1


def test_utc_seconds_granularity_due(tmp_path):
    """fire_at one second before now is due; one second after is not."""
    db = Database(tmp_path / "db.sqlite")
    now = _now()
    _schedule(db, id="SCHEDULE-001", fire_at=now - timedelta(seconds=1))
    _schedule(db, id="SCHEDULE-002", fire_at=now + timedelta(seconds=1))
    org = _FakeOrg(db)
    assert schedule_due_schedules(org=org, now=now) == 1
    assert db.schedules.get("SCHEDULE-001").status == ScheduleStatus.FIRING
    assert db.schedules.get("SCHEDULE-002").status == ScheduleStatus.ARMED


# ── weekly: no replay/backfill ──────────────────────────────────────────

def test_weekly_stale_slot_not_enqueued(tmp_path):
    """A weekly schedule whose fire_at is hours past (missed during daemon
    downtime) must NOT be enqueued. Instead, fire_at must be advanced to the
    next weekly occurrence. No job enqueued, no fire_count increment."""
    db = Database(tmp_path / "db.sqlite")
    now = _now()
    _schedule(
        db,
        kind=ScheduleKind.WEEKLY,
        recurrence={"day": "Mon", "time": "09:00", "tz": "UTC"},
        fire_at=now - timedelta(hours=5),
    )
    org = _FakeOrg(db)
    assert schedule_due_schedules(org=org, now=now) == 0
    # No job enqueued
    assert org.schedule_queue.size == 0
    # Schedule still ARMED, fire_at advanced to next occurrence
    record = db.schedules.get("SCHEDULE-001")
    assert record.status == ScheduleStatus.ARMED
    assert record.fire_at > now
    assert record.fire_count == 0


def test_weekly_stale_slot_expires_when_past_expiry(tmp_path):
    """A stale weekly whose next occurrence exceeds expires_at expires
    without enqueuing."""
    db = Database(tmp_path / "db.sqlite")
    now = _now()
    # Set fire_at 5 hours in the past, expires_at to 1 hour in the past —
    # the schedule should have expired already.
    _schedule(
        db,
        kind=ScheduleKind.WEEKLY,
        recurrence={"day": "Mon", "time": "09:00", "tz": "UTC"},
        fire_at=now - timedelta(hours=5),
        expires_at=now - timedelta(hours=1),
    )
    org = _FakeOrg(db)
    assert schedule_due_schedules(org=org, now=now) == 0
    assert org.schedule_queue.size == 0
    record = db.schedules.get("SCHEDULE-001")
    assert record.status == ScheduleStatus.EXPIRED
    assert record.active == 0
    # Audit: schedule_expired row emitted
    logs = db.get_audit_logs("SCHEDULE-001")
    assert any(
        entry["action"] == "schedule_expired"
        for entry in logs
    ), f"expected schedule_expired audit row, got {logs}"


def test_weekly_on_time_fires_normally(tmp_path):
    """A weekly schedule whose fire_at is within the tolerance window fires
    normally (claimed + enqueued)."""
    db = Database(tmp_path / "db.sqlite")
    now = _now()
    _schedule(
        db,
        kind=ScheduleKind.WEEKLY,
        recurrence={"day": "Wed", "time": "12:00", "tz": "UTC"},
        fire_at=now - timedelta(seconds=30),  # within tolerance
    )
    org = _FakeOrg(db)
    assert schedule_due_schedules(org=org, now=now) == 1
    assert org.schedule_queue.size == 1
    record = db.schedules.get("SCHEDULE-001")
    assert record.status == ScheduleStatus.FIRING


def test_weekly_missed_slot_not_replayed_on_restart(tmp_path):
    """Daemon restart after missing a weekly slot: no job enqueued, fire_at
    advanced to next occurrence."""
    db = Database(tmp_path / "db.sqlite")
    now = _now()
    _schedule(
        db,
        kind=ScheduleKind.WEEKLY,
        recurrence={"day": "Mon", "time": "09:00", "tz": "UTC"},
        fire_at=now - timedelta(days=2),  # missed by 2 days
    )
    org = _FakeOrg(db)
    assert schedule_due_schedules(org=org, now=now) == 0
    assert org.schedule_queue.size == 0
    record = db.schedules.get("SCHEDULE-001")
    assert record.status == ScheduleStatus.ARMED
    assert record.fire_at > now
    assert record.fire_count == 0


def test_recurring_stale_until_ends_date_not_expired(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    now = _now()
    _schedule(
        db, kind=ScheduleKind.RECURRING, fire_at=now - timedelta(hours=5),
        recurrence={
            "freq": "DAILY", "interval": 1, "anchor_date": "2026-07-01",
            "time": "09:00", "tz": "UTC", "until": "2026-07-21", "count": None,
        }, expires_at=now - timedelta(days=1),
    )
    org = _FakeOrg(db)
    assert schedule_due_schedules(org=org, now=now) == 0
    record = db.schedules.get("SCHEDULE-001")
    assert record.status == ScheduleStatus.FIRED
    assert record.end_reason == "date_ended"
    actions = [log["action"] for log in db.get_audit_logs(record.id)]
    assert "schedule_fired" in actions
    assert "occurrence_missed" not in actions


def test_recurring_stale_next_candidate_past_expiry_expires(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    now = _now()
    _schedule(
        db,
        kind=ScheduleKind.RECURRING,
        fire_at=now - timedelta(hours=5),
        expires_at=now + timedelta(hours=1),
        recurrence={
            "freq": "DAILY", "interval": 1, "anchor_date": "2026-07-01",
            "time": "09:00", "tz": "UTC", "until": None, "count": None,
        },
    )
    org = _FakeOrg(db)

    assert schedule_due_schedules(org=org, now=now) == 0
    record = db.schedules.get("SCHEDULE-001")
    assert record.status == ScheduleStatus.EXPIRED
    assert record.end_reason is None
    assert org.schedule_queue.size == 0


def test_ordinary_renew_revokes_indefinite_and_restores_expiry_guard(tmp_path, monkeypatch):
    """A normal renewal must restore the scheduler's finite review guard."""
    db = Database(tmp_path / "db.sqlite")
    renewal_now = _now()
    monkeypatch.setattr(
        "runtime.orchestrator.schedule_service._now",
        lambda: renewal_now,
    )
    _schedule(
        db,
        kind=ScheduleKind.RECURRING,
        fire_at=renewal_now - timedelta(hours=5),
        expires_at=renewal_now + timedelta(days=1),
        indefinite=1,
        recurrence={
            "freq": "DAILY", "interval": 1, "anchor_date": "2026-07-01",
            "time": "09:00", "tz": "UTC", "until": None, "count": None,
        },
    )

    renewed = ScheduleService(db).renew("SCHEDULE-001", "operator@test")

    assert renewed.indefinite == 0
    assert renewed.expires_at == renewal_now + timedelta(days=90)

    scheduler_now = renewed.expires_at + timedelta(days=1)
    org = _FakeOrg(db)
    assert schedule_due_schedules(org=org, now=scheduler_now) == 0
    assert db.schedules.get("SCHEDULE-001").status == ScheduleStatus.EXPIRED


def test_recurring_stale_defensive_no_candidate_fails(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    now = _now()
    monkeypatch.setattr(
        "runtime.daemon.schedule_scheduler.next_schedule_occurrence",
        lambda *_args, **_kwargs: None,
    )
    _schedule(
        db,
        kind=ScheduleKind.RECURRING,
        fire_at=now - timedelta(hours=5),
        recurrence={
            "freq": "DAILY", "interval": 1, "anchor_date": "2026-07-01",
            "time": "09:00", "tz": "UTC", "until": None, "count": None,
        },
    )
    org = _FakeOrg(db)

    assert schedule_due_schedules(org=org, now=now) == 0
    record = db.schedules.get("SCHEDULE-001")
    assert record.status == ScheduleStatus.FAILED
    assert record.error == "recurrence_no_candidate"
    assert org.schedule_queue.size == 0


def test_recurring_stale_rearms_and_audits_missed_occurrence(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    now = _now()
    _schedule(
        db,
        kind=ScheduleKind.RECURRING,
        fire_at=now - timedelta(hours=5),
        recurrence={
            "freq": "DAILY", "interval": 1, "anchor_date": "2026-07-01",
            "time": "09:00", "tz": "UTC", "until": None, "count": None,
        },
    )
    org = _FakeOrg(db)

    assert schedule_due_schedules(org=org, now=now) == 0
    record = db.schedules.get("SCHEDULE-001")
    assert record.status == ScheduleStatus.ARMED
    assert record.fire_at > now
    assert record.fire_count == 0
    assert any(
        row["action"] == "occurrence_missed"
        for row in db.get_audit_logs(record.id)
    )


def test_recurring_occurrence_key_is_claimed_only_once(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    now = _now()
    _schedule(
        db,
        kind=ScheduleKind.RECURRING,
        fire_at=now - timedelta(seconds=30),
        recurrence={
            "freq": "DAILY", "interval": 1, "anchor_date": "2026-07-01",
            "time": "12:00", "tz": "UTC", "until": None, "count": None,
        },
    )
    org = _FakeOrg(db)

    assert schedule_due_schedules(org=org, now=now) == 1
    assert schedule_due_schedules(org=org, now=now) == 0
    assert org.schedule_queue.size == 1
    assert db.schedules.get("SCHEDULE-001").status == ScheduleStatus.FIRING


# ── startup recovery ────────────────────────────────────────────────────

def test_startup_recovery_clears_stale_firing(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    # Simulate a schedule left FIRING from a prior crashed daemon
    _schedule(db, status=ScheduleStatus.FIRING, fire_at=_dt(day=21, hour=10))
    org = _FakeOrg(db)

    # startup=True triggers recover_firing first
    assert schedule_due_schedules(org=org, now=_now(), startup=True) == 0
    record = db.schedules.get("SCHEDULE-001")
    assert record.status == ScheduleStatus.FAILED
    assert record.error == "daemon_restart"
    # Audit: schedule_failed row emitted
    logs = db.get_audit_logs("SCHEDULE-001")
    assert any(
        entry["action"] == "schedule_failed"
        and entry["payload"]["reason"] == "daemon_restart"
        for entry in logs
    ), f"expected schedule_failed audit row, got {logs}"


def test_startup_recovery_then_schedules_due(tmp_path):
    """Startup recovers stale FIRING, then schedules genuinely due rows."""
    db = Database(tmp_path / "db.sqlite")
    now = _now()
    # Stale FIRING
    _schedule(db, id="SCHEDULE-001", status=ScheduleStatus.FIRING, fire_at=_dt(day=21, hour=10))
    # Genuinely due
    _schedule(db, id="SCHEDULE-002", fire_at=_dt(day=22, hour=11))
    org = _FakeOrg(db)

    assert schedule_due_schedules(org=org, now=now, startup=True) == 1
    assert db.schedules.get("SCHEDULE-001").status == ScheduleStatus.FAILED
    assert db.schedules.get("SCHEDULE-002").status == ScheduleStatus.FIRING
