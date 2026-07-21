"""THR-105 Phase 3: TDD tests for schedule spawn callback route —
acceptance gating (FIRING-only, record-scoped), task creation, terminal
state resolution, repeated-call rejection.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.models import ScheduleKind, ScheduleRecord, ScheduleStatus, TaskRecord
from runtime.orchestrator.schedule_rules import (
    MAX_ARMED_PER_AGENT,
    next_weekly_occurrence,
)


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


# ═══════════════════════════════════════════════════════════════════════
# Schedule CREATE route tests (THR-105 Phase 4)
# ═══════════════════════════════════════════════════════════════════════


def _create_schedule(client, auth_headers=None, **overrides) -> tuple[int, dict]:
    """Helper to POST to /schedules/create."""
    payload = {
        "task_id": "TASK-001",
        "session_id": "sess-test-create-001",
        "kind": "one_shot",
        "fire_at": "2026-08-22T12:00:00+00:00",
        "timezone": "UTC",
        "normalized_brief": "Test schedule brief",
        "source_instruction": "Founder said: create a test schedule",
    }
    payload.update(overrides)
    headers = auth_headers or {}
    resp = client.post(
        "/api/v1/orgs/alpha/schedules/create",
        json=payload,
        headers=headers,
    )
    if resp.status_code < 400:
        return resp.status_code, resp.json()
    return resp.status_code, resp.json().get("detail", {})


def _setup_task_and_session(org_state, agent="dev_agent"):
    """Insert a pending task with an active session."""
    task_id = org_state.db.next_task_id()
    org_state.db.insert_task(TaskRecord(
        id=task_id,
        brief="Dummy task for schedule creation",
        team="engineering",
        assigned_agent=agent,
    ))
    session_id = f"sess-{task_id}"
    org_state.sessions.set_active(task_id, agent, session_id)
    return task_id, session_id


def _enable_scheduling_for_agent(org_state, agent="dev_agent"):
    """Enable scheduling capability for the given agent in org config."""
    import yaml
    config_path = org_state.root / "org" / "config.yaml"
    raw = {}
    if config_path.exists():
        raw = yaml.safe_load(config_path.read_text()) or {}
    scheduling = raw.setdefault("scheduling", {})
    enabled = scheduling.setdefault("enabled_agents", [])
    if agent not in enabled:
        enabled.append(agent)
    config_path.write_text(yaml.safe_dump(raw))


# ── auth / session validation ────────────────────────────────────────

def test_create_rejects_missing_task_id(app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    # Pydantic rejects missing required fields as 422 before the route runs.
    status, detail = _create_schedule(client, auth_headers=auth_headers, task_id=None, session_id=None)
    assert status == 422


def test_create_rejects_invalid_task(app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    status, detail = _create_schedule(
        client, auth_headers=auth_headers, task_id="TASK-NOPE", session_id="sess-x")
    assert status == 409
    assert detail.get("code") == "session_mismatch"


def test_create_rejects_inactive_session(app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    task_id, _ = _setup_task_and_session(org_state)
    # Use a different session_id — won't match the active one.
    status, detail = _create_schedule(
        client, auth_headers=auth_headers, task_id=task_id, session_id="wrong-session")
    assert status == 409
    assert detail.get("code") == "session_mismatch"


def test_create_rejects_no_scheduling_capability(app, org_state, auth_headers):
    """When no scheduling config exists, creation must be rejected."""
    from fastapi.testclient import TestClient
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    # Do NOT call _enable_scheduling_for_agent — capability should default deny.
    status, detail = _create_schedule(
        client, auth_headers=auth_headers, task_id=task_id, session_id=session_id)
    assert status == 403
    assert "scheduling" in detail.get("code", "").lower()


def test_create_rejects_agent_not_in_enabled_list(app, org_state, auth_headers):
    """When scheduling is enabled for agent_B but caller is agent_A."""
    from fastapi.testclient import TestClient
    client = TestClient(app)
    _enable_scheduling_for_agent(org_state, "other_agent")
    task_id, session_id = _setup_task_and_session(org_state, agent="dev_agent")
    status, detail = _create_schedule(
        client, auth_headers=auth_headers, task_id=task_id, session_id=session_id)
    assert status == 403


# ── payload validation ───────────────────────────────────────────────

def test_create_rejects_extra_fields(app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    _enable_scheduling_for_agent(org_state)
    status, detail = _create_schedule(
        client,
        auth_headers=auth_headers,
        task_id=task_id,
        session_id=session_id,
        # Inject an extra field that the payload does not accept.
        target_agent="someone_else",
    )
    assert status == 422


def test_create_rejects_blank_normalized_brief(app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    _enable_scheduling_for_agent(org_state)
    status, detail = _create_schedule(
        client,
        auth_headers=auth_headers,
        task_id=task_id,
        session_id=session_id,
        normalized_brief="   ",
    )
    # The service layer rejects blank fields.
    assert status == 409


def test_create_rejects_blank_source_instruction(app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    _enable_scheduling_for_agent(org_state)
    status, detail = _create_schedule(
        client,
        auth_headers=auth_headers,
        task_id=task_id,
        session_id=session_id,
        source_instruction="",
    )
    assert status == 409


def test_create_rejects_one_shot_with_recurrence(app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    _enable_scheduling_for_agent(org_state)
    status, detail = _create_schedule(
        client,
        auth_headers=auth_headers,
        task_id=task_id,
        session_id=session_id,
        kind="one_shot",
        recurrence={"day": "mon", "time": "09:00", "tz": "Asia/Shanghai"},
    )
    assert status == 409


def test_create_rejects_unsupported_kind(app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    _enable_scheduling_for_agent(org_state)
    status, detail = _create_schedule(
        client,
        auth_headers=auth_headers,
        task_id=task_id,
        session_id=session_id,
        kind="cron",
    )
    assert status in (422, 409)


def test_create_rejects_weekly_timezone_mismatch(app, org_state, monkeypatch, auth_headers):
    from fastapi.testclient import TestClient
    from datetime import datetime, timezone, timedelta
    from runtime.orchestrator.schedule_rules import next_weekly_occurrence
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    _enable_scheduling_for_agent(org_state)

    # Compute the CORRECT next occurrence for the recurrence.
    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "runtime.orchestrator.schedule_service._now", lambda: now)

    recurrence = {"day": "mon", "time": "09:00", "tz": "Asia/Shanghai"}
    expected_fire = next_weekly_occurrence("mon", "09:00", "Asia/Shanghai", after=now)

    # fire_at matches the CORRECT tz but timezone field is WRONG
    status, detail = _create_schedule(
        client,
        auth_headers=auth_headers,
        task_id=task_id,
        session_id=session_id,
        kind="weekly",
        fire_at=expected_fire.isoformat(),
        recurrence=recurrence,
        timezone="America/New_York",  # mismatch!
    )
    assert status == 409


def test_create_rejects_fire_at_in_past(app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    _enable_scheduling_for_agent(org_state)
    status, detail = _create_schedule(
        client,
        auth_headers=auth_headers,
        task_id=task_id,
        session_id=session_id,
        fire_at="2020-01-01T00:00:00+00:00",
    )
    assert status == 409


def test_create_rejects_one_shot_beyond_horizon(app, org_state, auth_headers):
    from fastapi.testclient import TestClient
    from datetime import datetime, timedelta, timezone
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    _enable_scheduling_for_agent(org_state)
    far_future = datetime.now(timezone.utc) + timedelta(days=365)
    status, detail = _create_schedule(
        client,
        auth_headers=auth_headers,
        task_id=task_id,
        session_id=session_id,
        fire_at=far_future.isoformat(),
    )
    assert status == 409


def test_create_rejects_agent_cap_exceeded(app, org_state, auth_headers):
    """When the agent already has MAX_ARMED_PER_AGENT schedules."""
    from fastapi.testclient import TestClient
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    _enable_scheduling_for_agent(org_state)

    # Saturate the agent's capacity.
    from datetime import datetime, timezone
    for _ in range(MAX_ARMED_PER_AGENT):
        sid = org_state.db.schedules.next_id()
        org_state.db.schedules.insert(ScheduleRecord(
            id=sid,
            agent_name="dev_agent",
            team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
            timezone="UTC",
            normalized_brief="sat",
            source_instruction="sat",
            status=ScheduleStatus.ARMED,
        ))

    status, detail = _create_schedule(
        client, auth_headers=auth_headers, task_id=task_id, session_id=session_id)
    assert status == 409


# ── cross-agent / injection rejection ─────────────────────────────────

def test_create_derives_agent_from_session_not_payload(app, org_state, auth_headers):
    """The schedule owner must be the session's agent, not a caller-chosen value."""
    from fastapi.testclient import TestClient
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state, agent="dev_agent")
    _enable_scheduling_for_agent(org_state)
    # There is no 'agent_name' field in the payload — the route derives it.
    # This test verifies that the created schedule belongs to dev_agent.
    status, body = _create_schedule(
        client, auth_headers=auth_headers, task_id=task_id, session_id=session_id)
    assert status == 200
    record = org_state.db.schedules.get(body["schedule_id"])
    assert record.agent_name == "dev_agent"
    assert record.team == "engineering"


# ── success: one-shot ────────────────────────────────────────────────

def test_create_one_shot_success(app, org_state, monkeypatch, auth_headers):
    from fastapi.testclient import TestClient
    from datetime import datetime, timezone
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    _enable_scheduling_for_agent(org_state)

    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "runtime.orchestrator.schedule_service._now", lambda: now)

    status, body = _create_schedule(
        client,
        auth_headers=auth_headers,
        task_id=task_id,
        session_id=session_id,
        kind="one_shot",
        fire_at="2026-08-22T12:00:00+00:00",
        normalized_brief="Run a one-shot task",
        source_instruction="Founder asked me to remind about the deadline",
    )
    assert status == 200
    assert body["schedule_id"].startswith("SCHEDULE-")
    assert body["agent_name"] == "dev_agent"
    assert body["team"] == "engineering"
    assert body["kind"] == "one_shot"
    assert body["status"] == "armed"
    assert body["normalized_brief"] == "Run a one-shot task"
    assert body["source_instruction"] == "Founder asked me to remind about the deadline"

    # Verify stored row
    record = org_state.db.schedules.get(body["schedule_id"])
    assert record.agent_name == "dev_agent"
    assert record.team == "engineering"
    assert record.kind == ScheduleKind.ONE_SHOT
    assert record.normalized_brief == "Run a one-shot task"
    assert record.source_instruction == "Founder asked me to remind about the deadline"
    assert record.status == ScheduleStatus.ARMED
    assert record.active == 1
    assert record.expires_at is None  # one-shot has no expiry

    # Verify audit
    audits = org_state.db.get_audit_logs_by_action("schedule_created")
    matching = [a for a in audits if a["task_id"] == body["schedule_id"]]
    assert len(matching) == 1


def test_create_weekly_success(app, org_state, monkeypatch, auth_headers):
    from fastapi.testclient import TestClient
    from datetime import datetime, timezone
    from runtime.orchestrator.schedule_rules import next_weekly_occurrence
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    _enable_scheduling_for_agent(org_state)

    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "runtime.orchestrator.schedule_service._now", lambda: now)

    recurrence = {"day": "mon", "time": "09:00", "tz": "Asia/Shanghai"}
    expected_fire = next_weekly_occurrence("mon", "09:00", "Asia/Shanghai", after=now)

    status, body = _create_schedule(
        client,
        auth_headers=auth_headers,
        task_id=task_id,
        session_id=session_id,
        kind="weekly",
        fire_at=expected_fire.isoformat(),
        recurrence=recurrence,
        timezone="Asia/Shanghai",
        normalized_brief="Weekly check-in",
        source_instruction="Founder wants weekly status update every Monday",
    )
    assert status == 200
    assert body["schedule_id"].startswith("SCHEDULE-")
    assert body["kind"] == "weekly"
    assert body["status"] == "armed"
    assert body["recurrence"] == recurrence
    assert body["timezone"] == "Asia/Shanghai"
    assert body["normalized_brief"] == "Weekly check-in"

    # Verify stored row
    record = org_state.db.schedules.get(body["schedule_id"])
    assert record.kind == ScheduleKind.WEEKLY
    assert record.recurrence == recurrence
    assert record.timezone == "Asia/Shanghai"
    assert record.expires_at is not None  # weekly has default expiry
    assert record.expires_at > now

    # Verify audit
    audits = org_state.db.get_audit_logs_by_action("schedule_created")
    matching = [a for a in audits if a["task_id"] == body["schedule_id"]]
    assert len(matching) == 1


def test_create_weekly_normalizes_timezone_from_recurrence(app, org_state, monkeypatch, auth_headers):
    """For weekly schedules, timezone is normalized from recurrence.tz."""
    from fastapi.testclient import TestClient
    from datetime import datetime, timezone
    from runtime.orchestrator.schedule_rules import next_weekly_occurrence
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    _enable_scheduling_for_agent(org_state)

    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "runtime.orchestrator.schedule_service._now", lambda: now)

    recurrence = {"day": "fri", "time": "17:00", "tz": "America/New_York"}
    expected_fire = next_weekly_occurrence("fri", "17:00", "America/New_York", after=now)

    status, body = _create_schedule(
        client,
        auth_headers=auth_headers,
        task_id=task_id,
        session_id=session_id,
        kind="weekly",
        fire_at=expected_fire.isoformat(),
        recurrence=recurrence,
        timezone="America/New_York",
        normalized_brief="Friday review",
        source_instruction="Weekly review on Fridays",
    )
    assert status == 200
    assert body["timezone"] == "America/New_York"


def test_create_no_schedule_row_on_failure(app, org_state, auth_headers):
    """When creation fails, no schedule row is inserted."""
    from fastapi.testclient import TestClient
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    _enable_scheduling_for_agent(org_state)

    schedules_before = org_state.db.schedules.list(limit=500)
    status, detail = _create_schedule(
        client,
        auth_headers=auth_headers,
        task_id=task_id,
        session_id=session_id,
        normalized_brief="",  # blank → rejection
    )
    assert status == 409
    schedules_after = org_state.db.schedules.list(limit=500)
    assert len(schedules_after) == len(schedules_before)


def test_create_no_audit_row_on_failure(app, org_state, auth_headers):
    """When creation fails, no audit row is written."""
    from fastapi.testclient import TestClient
    client = TestClient(app)
    task_id, session_id = _setup_task_and_session(org_state)
    _enable_scheduling_for_agent(org_state)

    audits_before = org_state.db.get_audit_logs_by_action("schedule_created")
    status, detail = _create_schedule(
        client,
        auth_headers=auth_headers,
        task_id=task_id,
        session_id=session_id,
        source_instruction="",  # blank → rejection
    )
    assert status == 409
    audits_after = org_state.db.get_audit_logs_by_action("schedule_created")
    assert len(audits_after) == len(audits_before)
