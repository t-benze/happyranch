"""THR-105 Phase 3: TDD tests for schedule spawn callback route —
acceptance gating (FIRING-only, record-scoped), task creation, terminal
state resolution, repeated-call rejection.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.models import ScheduleKind, ScheduleRecord, ScheduleStatus
from runtime.orchestrator.schedule_rules import next_weekly_occurrence


def _now() -> datetime:
    return datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def _insert_schedule(org_state, **overrides) -> str:
    now = _now()
    base: dict = dict(
        id=org_state.db.schedules.next_id(),
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=now + timedelta(hours=1),
        timezone="UTC",
        normalized_brief="Test task brief",
        source_instruction="Test source instruction",
    )
    base.update(overrides)
    record = ScheduleRecord(**base)
    org_state.db.schedules.insert(record)
    return record.id


def _spawn(client, schedule_id: str, auth_headers: dict) -> tuple[int, dict]:
    resp = client.post(
        f"/api/v1/orgs/alpha/schedules/{schedule_id}/spawn",
        json={"summary": "Dispatched the scheduled task."},
        headers=auth_headers,
    )
    return resp.status_code, (resp.json() if resp.status_code < 400 else resp.json().get("detail", {}))


# ── acceptance gating ──────────────────────────────────────────────────

def test_rejects_non_firing_schedule(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    sid = _insert_schedule(org_state, status=ScheduleStatus.ARMED)
    status, detail = _spawn(client, sid, auth_headers)
    assert status == 409
    assert detail.get("code") == "schedule_not_firing"


def test_rejects_missing_schedule(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    status, _ = _spawn(client, "SCHEDULE-999", auth_headers)
    assert status == 404


def test_rejects_paused_schedule(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    sid = _insert_schedule(org_state, status=ScheduleStatus.PAUSED, active=0)
    status, detail = _spawn(client, sid, auth_headers)
    assert status == 409


def test_rejects_cancelled_schedule(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    sid = _insert_schedule(org_state, status=ScheduleStatus.CANCELLED, active=0)
    status, detail = _spawn(client, sid, auth_headers)
    assert status == 409


def test_rejects_already_fired_schedule(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    sid = _insert_schedule(org_state, status=ScheduleStatus.FIRED, active=0)
    status, detail = _spawn(client, sid, auth_headers)
    assert status == 409


# ── successful spawn: one-shot ──────────────────────────────────────────

def test_one_shot_spawn_creates_task_and_transitions_to_fired(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    sid = _insert_schedule(org_state, status=ScheduleStatus.FIRING, kind=ScheduleKind.ONE_SHOT)
    status, body = _spawn(client, sid, auth_headers)
    assert status == 200
    assert body["schedule_id"] == sid
    assert body["status"] == "completed"
    assert len(body["spawned_task_ids"]) == 1

    task_id = body["spawned_task_ids"][0]
    task = org_state.db.get_task(task_id)
    assert task is not None
    assert task.assigned_agent == "dev_agent"
    assert task.team == "engineering"
    assert task.brief == "Test task brief"

    record = org_state.db.schedules.get(sid)
    assert record.status == ScheduleStatus.FIRED
    assert record.active == 0
    assert record.spawned_task_ids == [task_id]
    assert record.fire_count == 1
    assert record.last_fired_at is not None


def test_one_shot_spawn_writes_audit(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    sid = _insert_schedule(org_state, status=ScheduleStatus.FIRING, kind=ScheduleKind.ONE_SHOT)

    _spawn(client, sid, auth_headers)

    # Check audit rows
    spawned = org_state.db.get_audit_logs_by_action("schedule_spawned")
    assert len(spawned) >= 1
    assert spawned[0]["task_id"] == sid

    completed = org_state.db.get_audit_logs_by_action("schedule_completed")
    assert len(completed) >= 1
    assert completed[0]["task_id"] == sid


# ── repeated call rejection ─────────────────────────────────────────────

def test_repeated_spawn_rejected(tmp_home, app, org_state, auth_headers):
    """Once a one-shot schedule is FIRED, a second spawn call is rejected."""
    from fastapi.testclient import TestClient
    client = TestClient(app)
    sid = _insert_schedule(org_state, status=ScheduleStatus.FIRING, kind=ScheduleKind.ONE_SHOT)

    # First spawn succeeds
    status1, body1 = _spawn(client, sid, auth_headers)
    assert status1 == 200

    # Second spawn rejects (status is now FIRED, not FIRING)
    status2, _ = _spawn(client, sid, auth_headers)
    assert status2 == 409


# ── payload isolation: cannot choose agent/team/brief ──────────────────

def test_spawn_does_not_accept_agent_or_team_from_payload(tmp_home, app, org_state, auth_headers):
    """The spawn payload is summary-only; the agent, team, and brief come from
    the stored Schedule row."""
    from fastapi.testclient import TestClient
    client = TestClient(app)
    sid = _insert_schedule(org_state, status=ScheduleStatus.FIRING, kind=ScheduleKind.ONE_SHOT)

    # Extra fields in the payload are NOT part of the Pydantic model
    # (ScheduleSpawnBody only has summary), so they are ignored by FastAPI.
    resp = client.post(
        f"/api/v1/orgs/alpha/schedules/{sid}/spawn",
        json={
            "summary": "Dispatched.",
            "agent": "other_agent",  # ignored
            "team": "other_team",    # ignored
            "brief": "overridden",   # ignored
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200
    task_id = resp.json()["spawned_task_ids"][0]
    task = org_state.db.get_task(task_id)
    assert task.assigned_agent == "dev_agent"
    assert task.team == "engineering"
    assert task.brief == "Test task brief"  # from stored record, not payload


# ── weekly: re-arm after fire ───────────────────────────────────────────

def test_weekly_spawn_rearms_with_next_occurrence(tmp_home, app, org_state, auth_headers, monkeypatch):
    """A weekly schedule transitions back to ARMED with next fire_at after
    a successful spawn."""
    from fastapi.testclient import TestClient
    client = TestClient(app)

    now = _now()
    # Freeze the clock for deterministic next_weekly_occurrence.
    monkeypatch.setattr(
        "runtime.daemon.routes.schedules.datetime",
        type("FakeDatetime", (object,), {
            "now": staticmethod(lambda tz=None: now),
            "timezone": timezone,
            "timedelta": timedelta,
        }),
    )

    recurrence = {"day": "Sat", "time": "09:00", "tz": "UTC"}

    sid = _insert_schedule(
        org_state,
        status=ScheduleStatus.FIRING,
        kind=ScheduleKind.WEEKLY,
        recurrence=recurrence,
        fire_at=now - timedelta(hours=1),
        expires_at=None,
        indefinite=1,
    )
    status, body = _spawn(client, sid, auth_headers)
    assert status == 200

    record = org_state.db.schedules.get(sid)
    assert record.status == ScheduleStatus.ARMED
    assert record.active == 1
    assert record.fire_count == 1
    assert record.last_fired_at is not None


def test_weekly_spawn_expires_when_past_expires_at(tmp_home, app, org_state, auth_headers, monkeypatch):
    """When next occurrence exceeds expires_at and indefinite is 0, the schedule
    transitions to EXPIRED."""
    from fastapi.testclient import TestClient
    client = TestClient(app)

    now = _now()
    # Freeze the clock so the route's datetime.now() returns our controlled time.
    monkeypatch.setattr(
        "runtime.daemon.routes.schedules.datetime",
        type("FakeDatetime", (object,), {
            "now": staticmethod(lambda tz=None: now),
            "timezone": timezone,
            "timedelta": timedelta,
        }),
    )

    recurrence = {"day": "Wed", "time": "09:00", "tz": "UTC"}
    next_fire = next_weekly_occurrence("Wed", "09:00", "UTC", after=now)

    # Set expires_at to just before the next fire so it becomes expired.
    sid = _insert_schedule(
        org_state,
        status=ScheduleStatus.FIRING,
        kind=ScheduleKind.WEEKLY,
        recurrence=recurrence,
        fire_at=now - timedelta(hours=1),
        expires_at=next_fire - timedelta(seconds=1),  # just before next fire
        indefinite=0,
    )
    status, body = _spawn(client, sid, auth_headers)
    assert status == 200
    assert body["status"] == "expired"

    record = org_state.db.schedules.get(sid)
    assert record.status == ScheduleStatus.EXPIRED
    assert record.active == 0

    # Check audit
    expired = org_state.db.get_audit_logs_by_action("schedule_expired")
    assert len(expired) >= 1
    assert expired[0]["task_id"] == sid


def test_weekly_indefinite_skips_expiry(tmp_home, app, org_state, auth_headers, monkeypatch):
    """When indefinite=1, next occurrence past expires_at does NOT expire."""
    from fastapi.testclient import TestClient
    client = TestClient(app)

    now = _now()
    monkeypatch.setattr(
        "runtime.daemon.routes.schedules.datetime",
        type("FakeDatetime", (object,), {
            "now": staticmethod(lambda tz=None: now),
            "timezone": timezone,
            "timedelta": timedelta,
        }),
    )

    recurrence = {"day": "Wed", "time": "09:00", "tz": "UTC"}
    next_fire = next_weekly_occurrence("Wed", "09:00", "UTC", after=now)

    sid = _insert_schedule(
        org_state,
        status=ScheduleStatus.FIRING,
        kind=ScheduleKind.WEEKLY,
        recurrence=recurrence,
        fire_at=now - timedelta(hours=1),
        expires_at=next_fire - timedelta(seconds=1),
        indefinite=1,  # indefinite → no expiry check
    )
    status, body = _spawn(client, sid, auth_headers)
    assert status == 200
    assert body["status"] == "completed"

    record = org_state.db.schedules.get(sid)
    assert record.status == ScheduleStatus.ARMED
    assert record.active == 1
