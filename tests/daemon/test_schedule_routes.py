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


def _enable_scheduling(org_state, agent_name: str = "dev_agent") -> None:
    import yaml
    config_path = org_state.root / "org" / "config.yaml"
    if config_path.is_file():
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    else:
        raw = {}
    raw["scheduling"] = {"enabled_agents": [agent_name]}
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
        "fire_at": "2026-08-01T09:00:00+00:00",
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

def test_create_rejects_when_scheduling_not_enabled(tmp_home, app, org_state, auth_headers):
    """Default-deny: create is refused when scheduling.enabled_agents is absent."""
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _register_session(org_state)
    status, detail = _post_create(client, _create_payload(), auth_headers)
    assert status == 409
    assert detail.get("code") == "scheduling_disabled"


def test_create_rejects_agent_not_in_enabled_list(tmp_home, app, org_state, auth_headers):
    """An agent not in scheduling.enabled_agents is refused."""
    from fastapi.testclient import TestClient
    _enable_scheduling(org_state, "other_agent")
    client = TestClient(app)
    _register_session(org_state)
    status, detail = _post_create(client, _create_payload(), auth_headers)
    assert status == 409
    assert detail.get("code") == "scheduling_disabled"


def test_create_rejects_missing_session(tmp_home, app, org_state, auth_headers):
    """Without a registered session, create is refused (409 unknown_session)."""
    from fastapi.testclient import TestClient
    _enable_scheduling(org_state)
    client = TestClient(app)
    # No session registered
    status, detail = _post_create(client, _create_payload(), auth_headers)
    assert status == 409
    assert detail.get("code") == "unknown_session"


def test_create_rejects_session_mismatch(tmp_home, app, org_state, auth_headers):
    """A session_id that doesn't match the registered session is rejected."""
    from fastapi.testclient import TestClient
    _enable_scheduling(org_state)
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(session_id="wrong-session")
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409
    assert detail.get("code") == "session_mismatch"


def test_create_rejects_wrong_agent_for_session(tmp_home, app, org_state, auth_headers):
    """The agent in the payload must match the session's agent."""
    from fastapi.testclient import TestClient
    _enable_scheduling(org_state)
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
    _enable_scheduling(org_state)
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload()
    del payload["source_instruction"]
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 422


def test_create_rejects_blank_source_instruction(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _enable_scheduling(org_state)
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(source_instruction="   ")
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409
    assert "source_instruction" in detail.get("message", "")


def test_create_rejects_missing_normalized_brief(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _enable_scheduling(org_state)
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload()
    del payload["normalized_brief"]
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 422


def test_create_rejects_blank_normalized_brief(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _enable_scheduling(org_state)
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
    _enable_scheduling(org_state)
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload()
    payload["agent_name"] = "other_agent"  # not a recognized field
    status, _ = _post_create(client, payload, auth_headers)
    assert status == 422


# ── one-shot horizon ───────────────────────────────────────────────────

def test_create_rejects_one_shot_past_horizon(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _enable_scheduling(org_state)
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(fire_at="2027-01-01T00:00:00+00:00")
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409
    assert "90 days" in detail.get("message", "")


def test_create_rejects_one_shot_in_past(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _enable_scheduling(org_state)
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(fire_at="2020-01-01T00:00:00+00:00")
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409
    assert "must be in the future" in detail.get("message", "")


def test_create_rejects_one_shot_with_recurrence(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _enable_scheduling(org_state)
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
    _enable_scheduling(org_state)
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
    _enable_scheduling(org_state)
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
    _enable_scheduling(org_state)
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(
        kind="weekly",
        recurrence={"day": "Sat", "time": "09:00", "tz": "Asia/Shanghai", "days": ["Sat", "Sun"]},
        fire_at="2026-07-26T01:00:00+00:00",
    )
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409


# ── successful create ──────────────────────────────────────────────────

def test_create_one_shot_success(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _enable_scheduling(org_state)
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
    _enable_scheduling(org_state)
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
    _enable_scheduling(org_state)
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
    _enable_scheduling(org_state)
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
    _enable_scheduling(org_state)
    _register_session(org_state)
    client = TestClient(app)
    # Next Saturday 09:00 Asia/Shanghai after 2026-07-22T12:00Z
    # July 22 is Wednesday, so next Saturday is July 25
    # 09:00 Asia/Shanghai = 01:00 UTC
    payload = _create_payload(
        kind="weekly",
        fire_at="2026-07-25T01:00:00+00:00",
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
    _enable_scheduling(org_state)
    _register_session(org_state)
    client = TestClient(app)
    payload = _create_payload(fire_at="2026-08-01T09:00:00")
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 422
    assert detail.get("code") == "invalid_fire_at"


def test_create_respects_agent_cap(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    _enable_scheduling(org_state)
    _register_session(org_state)
    client = TestClient(app)

    # First create succeeds
    for _ in range(20):
        payload = _create_payload(
            fire_at=f"2026-08-{_+1:02d}T09:00:00+00:00",
            normalized_brief=f"Brief {_}",
        )
        status, body = _post_create(client, payload, auth_headers)
        assert status == 200, f"Create #{_} failed: {body}"

    # 21st create should fail (cap=20)
    payload = _create_payload(
        fire_at="2026-08-22T09:00:00+00:00",
        normalized_brief="Brief overflow",
    )
    status, detail = _post_create(client, payload, auth_headers)
    assert status == 409
    assert "20" in detail.get("message", "")
