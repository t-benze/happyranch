"""THR-105 Phase 3: TDD tests for schedule spawn callback route —
acceptance gating (FIRING-only, record-scoped), task creation, terminal
state resolution, repeated-call rejection.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.models import ScheduleKind, ScheduleRecord, ScheduleStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import load_org_config
from runtime.orchestrator.schedule_rules import next_recurring_occurrence, next_weekly_occurrence


_FROZEN_NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def _now() -> datetime:
    return _FROZEN_NOW


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """Freeze the service and store clocks so all date-dependent
    validations and DB timestamps in route tests are date-stable."""
    monkeypatch.setattr(
        "runtime.orchestrator.schedule_service._now",
        lambda: _FROZEN_NOW,
    )
    monkeypatch.setattr(
        "runtime.infrastructure.schedule_store._now",
        lambda: _FROZEN_NOW,
    )


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


def test_recurring_count_exhaustion_is_terminal_only_after_successful_spawn(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    sid = _insert_schedule(
        org_state, status=ScheduleStatus.FIRING, kind=ScheduleKind.RECURRING,
        recurrence={
            "freq": "DAILY", "interval": 1, "anchor_date": "2026-07-01",
            "time": "09:00", "tz": "UTC", "until": None, "count": 1,
        },
    )
    status, body = _spawn(client, sid, auth_headers)
    assert status == 200
    record = org_state.db.schedules.get(sid)
    assert record.status == ScheduleStatus.FIRED
    assert record.fire_count == 1
    assert record.end_reason == "count_exhausted"
    # The occurrence key cannot be claimed/dispatched twice after its FIRING
    # claim has resolved to a terminal row.
    status, detail = _spawn(client, sid, auth_headers)
    assert status == 409
    assert detail["code"] == "schedule_not_firing"


def test_recurring_spawn_until_exhaustion_is_date_ended_not_expired(
    tmp_home, app, org_state, auth_headers,
):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    sid = _insert_schedule(
        org_state,
        status=ScheduleStatus.FIRING,
        kind=ScheduleKind.RECURRING,
        expires_at=_FROZEN_NOW - timedelta(days=1),
        recurrence={
            "freq": "DAILY", "interval": 1, "anchor_date": "2026-07-01",
            "time": "09:00", "tz": "UTC", "until": "2026-07-22", "count": None,
        },
    )

    status, _ = _spawn(client, sid, auth_headers)

    assert status == 200
    record = org_state.db.schedules.get(sid)
    assert record.status == ScheduleStatus.FIRED
    assert record.end_reason == "date_ended"
    assert record.fire_count == 1


def test_recurring_spawn_expires_only_when_a_next_candidate_exists(
    tmp_home, app, org_state, auth_headers,
):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    sid = _insert_schedule(
        org_state,
        status=ScheduleStatus.FIRING,
        kind=ScheduleKind.RECURRING,
        expires_at=_FROZEN_NOW + timedelta(hours=1),
        recurrence={
            "freq": "DAILY", "interval": 1, "anchor_date": "2026-07-01",
            "time": "09:00", "tz": "UTC", "until": None, "count": None,
        },
    )

    status, body = _spawn(client, sid, auth_headers)

    assert status == 200
    assert body["status"] == "expired"
    record = org_state.db.schedules.get(sid)
    assert record.status == ScheduleStatus.EXPIRED
    assert record.end_reason is None
    assert record.fire_count == 1


def test_recurring_spawn_defensive_no_candidate_fails(
    tmp_home, app, org_state, auth_headers, monkeypatch,
):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    monkeypatch.setattr(
        "runtime.daemon.routes.schedules.next_recurring_occurrence", lambda *_args, **_kwargs: None,
    )
    sid = _insert_schedule(
        org_state,
        status=ScheduleStatus.FIRING,
        kind=ScheduleKind.RECURRING,
        recurrence={
            "freq": "DAILY", "interval": 1, "anchor_date": "2026-07-01",
            "time": "09:00", "tz": "UTC", "until": None, "count": None,
        },
    )

    status, body = _spawn(client, sid, auth_headers)

    assert status == 200
    assert body["status"] == "failed"
    record = org_state.db.schedules.get(sid)
    assert record.status == ScheduleStatus.FAILED
    assert record.error == "recurrence_no_candidate"
    assert record.fire_count == 1
    failed = org_state.db.get_audit_logs_by_action("schedule_failed")
    assert any(row["task_id"] == sid for row in failed)


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
        error="timed_out",
    )
    status, body = _spawn(client, sid, auth_headers)
    assert status == 200

    record = org_state.db.schedules.get(sid)
    assert record.status == ScheduleStatus.ARMED
    assert record.active == 1
    assert record.fire_count == 1
    assert record.last_fired_at is not None
    assert record.error is None


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


# ── Blocker 1 regression: weekly expiry enqueues + audits ──────────────

def test_weekly_expiry_enqueues_task_and_writes_audit(
    tmp_home, app, org_state, auth_headers, monkeypatch,
):
    """When a weekly schedule expires on its current fire (next occurrence
    past expires_at), the CURRENT fire's task MUST be enqueued, the spawned
    task id must be recorded, schedule_spawned + schedule_completed audit
    rows must exist, and the schedule must be EXPIRED."""
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
        indefinite=0,
    )

    status, body = _spawn(client, sid, auth_headers)
    assert status == 200
    assert body["status"] == "expired"
    assert len(body["spawned_task_ids"]) == 1

    # Schedule must be EXPIRED with the task recorded
    record = org_state.db.schedules.get(sid)
    assert record.status == ScheduleStatus.EXPIRED
    assert record.active == 0
    assert record.fire_count == 1
    assert len(record.spawned_task_ids) == 1
    assert body["spawned_task_ids"] == record.spawned_task_ids

    # Task must exist
    task_id = body["spawned_task_ids"][0]
    task = org_state.db.get_task(task_id)
    assert task is not None
    assert task.assigned_agent == "dev_agent"

    # Audit: schedule_spawned must exist for this fire
    spawned = org_state.db.get_audit_logs_by_action("schedule_spawned")
    spawned_for_schedule = [r for r in spawned if r["task_id"] == sid]
    assert len(spawned_for_schedule) >= 1

    # Audit: schedule_completed must exist for this fire
    completed = org_state.db.get_audit_logs_by_action("schedule_completed")
    completed_for_schedule = [r for r in completed if r["task_id"] == sid]
    assert len(completed_for_schedule) >= 1

    # Audit: schedule_expired must exist
    expired = org_state.db.get_audit_logs_by_action("schedule_expired")
    expired_for_schedule = [r for r in expired if r["task_id"] == sid]
    assert len(expired_for_schedule) >= 1


# ── THR-105 Phase 4: create route ──────────────────────────────────────

SESSION_TASK = "TASK-CREATE-001"
SESSION_ID = "sess-create-test"


def _write_legacy_scheduling_config(org_state, enabled_agents: list[str]) -> None:
    import yaml
    config_path = org_state.root / "org" / "config.yaml"
    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    else:
        raw = {}
    raw["scheduling"] = {"enabled_agents": enabled_agents}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _register_session(org_state, task_id: str = SESSION_TASK, agent: str = "dev_agent",
                      session_id: str = SESSION_ID) -> None:
    org_state.sessions.set_active(task_id, agent, session_id)


def _create_payload(**overrides) -> dict:
    base: dict = {
        "task_id": SESSION_TASK,
        "session_id": SESSION_ID,
        "agent": "dev_agent",
        "source_instruction": "Test instruction: follow up in 48 hours.",
        "normalized_brief": "Follow up with customer re: issue #42",
        "kind": "one_shot",
        "fire_at": (_now() + timedelta(days=10)).isoformat(),
        "timezone": "UTC",
    }
    base.update(overrides)
    return base


def _post_create(client, payload: dict, auth_headers: dict) -> tuple[int, dict]:
    resp = client.post(
        "/api/v1/orgs/alpha/schedules",
        json=payload,
        headers=auth_headers,
    )
    return resp.status_code, (resp.json() if resp.status_code < 400 else resp.json().get("detail", {}))


# ── acceptance gating ──────────────────────────────────────────────────

def test_create_succeeds_without_legacy_scheduling_config(tmp_home, app, org_state, auth_headers):
    """Missing legacy config cannot deny a session-bound in-org caller."""
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _register_session(org_state)
    status, body = _post_create(client, _create_payload(), auth_headers)
    assert status == 200
    assert body["agent_name"] == "dev_agent"


def test_legacy_enabled_agents_exclusion_is_a_noop_for_other_in_org_agent(
    tmp_home, app, org_state, auth_headers,
):
    """Existing config remains accepted but cannot authorize or deny Todos."""
    from fastapi.testclient import TestClient
    _write_legacy_scheduling_config(org_state, ["dev_agent"])
    assert load_org_config(OrgPaths(root=org_state.root)).session_timeout_seconds is None
    client = TestClient(app)
    _register_session(
        org_state,
        task_id="TASK-CREATE-002",
        agent="qa_engineer",
        session_id="sess-create-qa",
    )
    payload = _create_payload(
        task_id="TASK-CREATE-002", session_id="sess-create-qa", agent="qa_engineer",
    )
    status, body = _post_create(client, payload, auth_headers)
    assert status == 200
    assert body["agent_name"] == "qa_engineer"


def test_create_rejects_team_unresolved_caller(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _register_session(org_state, agent="outside_agent")
    status, detail = _post_create(client, _create_payload(agent="outside_agent"), auth_headers)
    assert status == 409
    assert detail.get("code") == "agent_team_unresolved"


def test_create_requires_authentication(tmp_home, app, org_state):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    response = TestClient(app).post(
        "/api/v1/orgs/alpha/schedules", json=_create_payload(),
    )
    assert response.status_code == 401


def test_create_rejects_missing_session(tmp_home, app, org_state, auth_headers):
    """Without a registered session, create is refused (409 unknown_session)."""
    from fastapi.testclient import TestClient
    client = TestClient(app)
    # No session registered
    status, detail = _post_create(client, _create_payload(), auth_headers)
    assert status == 409
    assert detail.get("code") == "unknown_session"


def test_create_rejects_session_mismatch(tmp_home, app, org_state, auth_headers):
    """A session_id that doesn't match the registered session is rejected."""
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(session_id="wrong-session")
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409
    assert detail.get("code") == "session_mismatch"


def test_create_rejects_wrong_agent_for_session(tmp_home, app, org_state, auth_headers):
    """The agent in the payload must match the session's agent."""
    from fastapi.testclient import TestClient
    _register_session(org_state, agent="dev_agent")
    client = TestClient(app)
    payload = _create_payload(agent="other_agent", session_id=SESSION_ID)
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409
    # The session lookup is by (task_id, agent from payload), so if agent doesn't
    # match, it returns unknown_session.
    assert detail.get("code") == "unknown_session"


# ── mandatory fields ───────────────────────────────────────────────────

def test_create_rejects_missing_source_instruction(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload()
    del payload["source_instruction"]
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 422


def test_create_rejects_blank_source_instruction(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(source_instruction="   ")
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409
    assert "source_instruction" in detail.get("message", "")


def test_create_rejects_missing_normalized_brief(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload()
    del payload["normalized_brief"]
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 422


def test_create_rejects_blank_normalized_brief(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(normalized_brief="")
    status, detail = _post_create(client, payload, auth_headers)
    # Pydantic min_length=1 catches empty string at 422, not service-level 409.
    assert status == 422


# ── payload shape rejection ────────────────────────────────────────────

def test_create_rejects_extra_forbidden_fields(tmp_home, app, org_state, auth_headers):
    """The create payload uses extra='forbid' — agent_name/target cannot be in the payload."""
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload()
    payload["agent_name"] = "other_agent"  # not a recognized field
    status, _ = _post_create(client, payload, auth_headers)
    assert status == 422


# ── one-shot horizon ───────────────────────────────────────────────────

def test_create_rejects_one_shot_past_horizon(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(fire_at="2027-01-01T00:00:00+00:00")
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409
    assert "90 days" in detail.get("message", "")


def test_create_rejects_one_shot_in_past(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(fire_at="2020-01-01T00:00:00+00:00")
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409
    assert "must be in the future" in detail.get("message", "")


def test_create_rejects_one_shot_with_recurrence(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(
        recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"},
    )
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409
    assert "must not have recurrence" in detail.get("message", "")


# ── weekly shape ───────────────────────────────────────────────────────

def test_create_weekly_requires_recurrence(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    # Weekly with null recurrence
    payload = _create_payload(kind="weekly", recurrence=None,
                              fire_at="2026-07-26T01:00:00+00:00")
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409
    assert "must not be null" in detail.get("message", "")


def test_create_rejects_weekly_cron_extras(tmp_home, app, org_state, auth_headers):
    """Cron-style or multi-weekday recurrence extras are rejected."""
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(
        kind="weekly",
        recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai", "cron": "0 9 * * 6"},
        fire_at="2026-07-26T01:00:00+00:00",
    )
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409


def test_create_rejects_weekly_multi_day(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(
        kind="weekly",
        recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai", "days": ["Sat", "Sun"]},
        fire_at="2026-07-26T01:00:00+00:00",
    )
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409


# ── recurring named validation errors ──────────────────────────────────

def test_create_recurring_returns_named_validation_code(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(
        kind="recurring",
        recurrence={"freq": "DAILY", "interval": 0, "time": "09:00", "tz": "UTC"},
        fire_at="2026-07-23T09:00:00+00:00",
    )

    status, detail = _post_create(client, payload, auth_headers)

    assert status == 422
    assert detail["code"] == "invalid_interval"


def test_create_recurring_preserves_agent_rule_at_the_service_create_seam(
    tmp_home, app, org_state, auth_headers,
):
    """A documented recurring callback reaches the shipped create seam intact."""
    from fastapi.testclient import TestClient

    _register_session(org_state)
    client = TestClient(app)
    recurrence = {
        "freq": "WEEKLY", "interval": 2, "byday": ["TU", "TH"],
        "time": "09:00", "tz": "Asia/Shanghai", "count": 6,
    }
    local_tz = timezone(timedelta(hours=8))
    expected = next_recurring_occurrence(
        {**recurrence, "anchor_date": _FROZEN_NOW.astimezone(local_tz).date().isoformat()},
        _FROZEN_NOW,
    )
    assert expected is not None
    payload = _create_payload(
        kind="recurring", recurrence=recurrence, timezone="Asia/Shanghai",
        fire_at=expected.isoformat(),
    )

    status, body = _post_create(client, payload, auth_headers)

    assert status == 200
    record = org_state.db.schedules.get(body["schedule_id"])
    assert record is not None
    assert record.kind == ScheduleKind.RECURRING
    assert record.recurrence == {
        **recurrence,
        "anchor_date": expected.astimezone(local_tz).date().isoformat(),
    }


def test_patch_recurring_without_fire_at_derives_server_occurrence(
    tmp_home, app, org_state, auth_headers,
):
    from fastapi.testclient import TestClient

    rule = {
        "freq": "WEEKLY", "interval": 1, "byday": ["TU"], "time": "09:00",
        "tz": "UTC", "until": None, "count": None, "anchor_date": "2026-07-22",
    }
    fire_at = next_recurring_occurrence(rule, _FROZEN_NOW)
    sid = _insert_schedule(
        org_state, kind=ScheduleKind.RECURRING, recurrence=rule, timezone="UTC", fire_at=fire_at,
    )

    response = TestClient(app).patch(
        f"/api/v1/orgs/alpha/schedules/{sid}",
        json={"recurrence": {"byday": ["TH"]}, "timezone": "UTC"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["fire_at"] == next_recurring_occurrence(
        body["recurrence"], _FROZEN_NOW,
    ).isoformat()


@pytest.mark.parametrize(
    ("stored_rule", "editor_rule", "cleared_selectors"),
    [
        (
            {"freq": "MONTHLY", "interval": 1, "bymonthday": 15, "time": "09:00", "tz": "UTC", "until": None, "count": None},
            {"freq": "MONTHLY", "interval": 1, "byday": ["MO"], "bymonthday": None, "ordinal": "second", "time": "09:00", "tz": "UTC", "until": None, "count": None},
            {"bymonthday"},
        ),
        (
            {"freq": "MONTHLY", "interval": 1, "byday": ["MO"], "ordinal": "second", "time": "09:00", "tz": "UTC", "until": None, "count": None},
            {"freq": "MONTHLY", "interval": 1, "byday": None, "bymonthday": 15, "ordinal": None, "time": "09:00", "tz": "UTC", "until": None, "count": None},
            {"byday", "ordinal"},
        ),
        (
            {"freq": "WEEKLY", "interval": 1, "byday": ["TU"], "time": "09:00", "tz": "UTC", "until": None, "count": None},
            {"freq": "DAILY", "interval": 1, "byday": None, "bymonthday": None, "ordinal": None, "time": "09:00", "tz": "UTC", "until": None, "count": None},
            {"byday", "bymonthday", "ordinal"},
        ),
        (
            {"freq": "MONTHLY", "interval": 1, "bymonthday": 15, "time": "09:00", "tz": "UTC", "until": None, "count": None},
            {"freq": "YEARLY", "interval": 1, "byday": None, "bymonthday": None, "ordinal": None, "time": "09:00", "tz": "UTC", "until": None, "count": None},
            {"byday", "bymonthday", "ordinal"},
        ),
    ],
    ids=["monthly-date-to-ordinal", "monthly-ordinal-to-date", "weekly-to-daily", "monthly-to-yearly"],
)
def test_patch_recurring_editor_selector_clears_are_persisted_canonically(
    tmp_home, app, org_state, auth_headers, stored_rule, editor_rule, cleared_selectors,
):
    """Bearer PATCH reaches the merge seam and removes inactive selectors."""
    from fastapi.testclient import TestClient

    stored = {**stored_rule, "anchor_date": "2026-07-22"}
    fire_at = next_recurring_occurrence(stored, _FROZEN_NOW)
    sid = _insert_schedule(
        org_state, kind=ScheduleKind.RECURRING, recurrence=stored, timezone="UTC", fire_at=fire_at,
    )

    response = TestClient(app).patch(
        f"/api/v1/orgs/alpha/schedules/{sid}",
        json={"recurrence": editor_rule, "timezone": "UTC"}, headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    persisted = org_state.db.schedules.get(sid)
    assert body["timezone"] == body["recurrence"]["tz"] == "UTC"
    assert persisted.timezone == persisted.recurrence["tz"] == "UTC"
    assert all(selector not in body["recurrence"] for selector in cleared_selectors)
    assert persisted.recurrence == body["recurrence"]
    assert body["fire_at"] == next_recurring_occurrence(body["recurrence"], _FROZEN_NOW).isoformat()


def test_patch_recurring_timezone_without_fire_at_derives_server_occurrence(
    tmp_home, app, org_state, auth_headers,
):
    from fastapi.testclient import TestClient

    rule = {
        "freq": "DAILY", "interval": 1, "time": "09:00", "tz": "UTC",
        "until": None, "count": None, "anchor_date": "2026-07-22",
    }
    fire_at = next_recurring_occurrence(rule, _FROZEN_NOW)
    sid = _insert_schedule(
        org_state, kind=ScheduleKind.RECURRING, recurrence=rule, timezone="UTC", fire_at=fire_at,
    )

    response = TestClient(app).patch(
        f"/api/v1/orgs/alpha/schedules/{sid}",
        json={"timezone": "Asia/Shanghai"},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["timezone"] == body["recurrence"]["tz"] == "Asia/Shanghai"
    assert body["recurrence"]["anchor_date"] == rule["anchor_date"]
    assert body["fire_at"] == next_recurring_occurrence(
        body["recurrence"], _FROZEN_NOW,
    ).isoformat()


@pytest.mark.parametrize("include_timezone", [False, True])
def test_patch_recurring_rule_timezone_without_fire_at_persists_authoritative_timezone(
    tmp_home, app, org_state, auth_headers, include_timezone,
):
    """Bearer edits keep returned and persisted recurring timezones identical."""
    from fastapi.testclient import TestClient

    rule = {
        "freq": "DAILY", "interval": 1, "time": "09:00", "tz": "UTC",
        "until": None, "count": None, "anchor_date": "2026-07-22",
    }
    fire_at = next_recurring_occurrence(rule, _FROZEN_NOW)
    sid = _insert_schedule(
        org_state, kind=ScheduleKind.RECURRING, recurrence=rule, timezone="UTC", fire_at=fire_at,
    )

    payload = {"recurrence": {"tz": "Asia/Shanghai"}}
    if include_timezone:
        payload["timezone"] = "Asia/Shanghai"
    response = TestClient(app).patch(
        f"/api/v1/orgs/alpha/schedules/{sid}", json=payload, headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    persisted = org_state.db.schedules.get(sid)
    assert body["timezone"] == body["recurrence"]["tz"] == "Asia/Shanghai"
    assert persisted.timezone == persisted.recurrence["tz"] == "Asia/Shanghai"
    assert body["fire_at"] == next_recurring_occurrence(body["recurrence"], _FROZEN_NOW).isoformat()
    assert body["recurrence"]["anchor_date"] == rule["anchor_date"]


def test_patch_recurring_supplied_mismatching_fire_at_still_rejects(
    tmp_home, app, org_state, auth_headers,
):
    from fastapi.testclient import TestClient

    rule = {
        "freq": "DAILY", "interval": 1, "time": "09:00", "tz": "UTC",
        "until": None, "count": None, "anchor_date": "2026-07-22",
    }
    fire_at = next_recurring_occurrence(rule, _FROZEN_NOW)
    sid = _insert_schedule(
        org_state, kind=ScheduleKind.RECURRING, recurrence=rule, timezone="UTC", fire_at=fire_at,
    )

    response = TestClient(app).patch(
        f"/api/v1/orgs/alpha/schedules/{sid}",
        json={"recurrence": {"time": "10:00"}, "fire_at": fire_at.isoformat()},
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "state_conflict"


@pytest.mark.parametrize("anchor_date", ["2026-07-22", "2026-07-23"])
def test_patch_recurring_rejects_caller_supplied_anchor_key(
    tmp_home, app, org_state, auth_headers, anchor_date,
):
    from fastapi.testclient import TestClient

    rule = {
        "freq": "DAILY", "interval": 1, "time": "09:00", "tz": "UTC",
        "until": None, "count": None, "anchor_date": "2026-07-22",
    }
    fire_at = next_recurring_occurrence(rule, _FROZEN_NOW)
    sid = _insert_schedule(
        org_state, kind=ScheduleKind.RECURRING, recurrence=rule, timezone="UTC", fire_at=fire_at,
    )

    response = TestClient(app).patch(
        f"/api/v1/orgs/alpha/schedules/{sid}",
        json={"recurrence": {"anchor_date": anchor_date}},
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "state_conflict"


# ── successful create ──────────────────────────────────────────────────

def test_create_one_shot_success(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload()
    status, body = _post_create(client, payload, auth_headers)
    assert status == 200
    assert body["status"] == "armed"
    assert body["agent_name"] == "dev_agent"
    assert body["team"] == "engineering"
    assert body["kind"] == "one_shot"
    assert body["normalized_brief"] == "Follow up with customer re: issue #42"
    assert body["source_instruction"] == "Test instruction: follow up in 48 hours."
    assert body["active"] == 1
    assert body["spawned_task_ids"] == []

    # Verify in DB
    record = org_state.db.schedules.get(body["schedule_id"])
    assert record is not None
    assert record.status.value == "armed"
    assert record.agent_name == "dev_agent"


def test_create_writes_schedule_created_audit(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    status, body = _post_create(client, _create_payload(), auth_headers)
    assert status == 200
    rows = org_state.db.get_audit_logs_by_action("schedule_created")
    created_for_id = [r for r in rows if r["task_id"] == body["schedule_id"]]
    assert len(created_for_id) == 1
    payload = created_for_id[0]["payload"]
    assert isinstance(payload, dict)
    assert payload.get("kind") == "one_shot"
    assert payload.get("normalized_brief") == "Follow up with customer re: issue #42"


def test_created_schedule_visible_in_list(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    status, body = _post_create(client, _create_payload(), auth_headers)
    assert status == 200
    sid = body["schedule_id"]

    # Verify visible in list
    list_resp = client.get("/api/v1/orgs/alpha/schedules", headers=auth_headers)
    assert list_resp.status_code == 200
    schedules = list_resp.json()["schedules"]
    ids = [s["schedule_id"] for s in schedules]
    assert sid in ids

    # Verify visible in show
    show_resp = client.get(f"/api/v1/orgs/alpha/schedules/{sid}", headers=auth_headers)
    assert show_resp.status_code == 200
    assert show_resp.json()["schedule_id"] == sid


def test_created_schedule_respects_self_target(tmp_home, app, org_state, auth_headers):
    """The created schedule's agent_name is the session-verified agent, not any
    field in the payload."""
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    # The payload agent field is verified against session — the server resolves
    # the agent from the session context. The payload cannot pick another agent.
    payload = _create_payload()
    status, body = _post_create(client, payload, auth_headers)
    assert status == 200
    assert body["agent_name"] == "dev_agent"
    # Payload's agent field matches the session, so it succeeds.


def test_create_weekly_success(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    # Compute the next Saturday 09:00 Asia/Shanghai after _FROZEN_NOW.
    # July 22 is Wednesday, so next Saturday is July 25;
    # 09:00 Asia/Shanghai = 01:00 UTC.
    now = _now()
    next_fire = next_weekly_occurrence("Sat", "09:00", "Asia/Shanghai", after=now)
    assert next_fire is not None
    payload = _create_payload(
        kind="weekly",
        fire_at=next_fire.isoformat(),
        recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"},
        timezone="Asia/Shanghai",
    )
    status, body = _post_create(client, payload, auth_headers)
    assert status == 200
    assert body["kind"] == "weekly"
    assert body["recurrence"] == {"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai"}
    assert body["timezone"] == "Asia/Shanghai"

    # Verify expires_at is set (90-day default for weekly)
    assert body["expires_at"] is not None


def test_create_rejects_naive_fire_at(tmp_home, app, org_state, auth_headers):
    """An offset-less ISO string like '2026-08-01T09:00:00' produces a naive
    datetime that causes TypeError in the service layer.  The route must
    reject it with a controlled 422 before it reaches ScheduleService."""
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(fire_at="2026-08-01T09:00:00")
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 422
    assert detail.get("code") == "invalid_fire_at"


def test_create_respects_agent_cap(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _register_session(org_state)
    client = TestClient(app)
    now = _now()

    # First 20 creates succeed (each with a unique fire_at within the horizon)
    for i in range(20):
        payload = _create_payload(
            fire_at=(now + timedelta(days=1, hours=i)).isoformat(),
            normalized_brief=f"Brief {i}",
        )
        status, body = _post_create(client, payload, auth_headers)
        assert status == 200, f"Create #{i} failed: {body}"

    # 21st create should fail (cap=20)
    payload = _create_payload(
        fire_at=(now + timedelta(days=2)).isoformat(),
        normalized_brief="Brief overflow",
    )
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409
    assert "20" in detail.get("message", "")
