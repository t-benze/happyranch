from __future__ import annotations

from datetime import datetime, timezone

import pytest

from runtime.models import WorkHourMode, WorkHourRecord, WorkHourStatus


def _dt(hour: int) -> datetime:
    return datetime(2026, 6, 11, hour, 0, tzinfo=timezone.utc)


def _running_wh(org_state, *, agent: str = "dev_agent", wh_id: str = "WORKHOUR-001",
                slot: str = "09:00", routine_count: int = 2) -> WorkHourRecord:
    rec = WorkHourRecord(
        id=wh_id,
        agent_name=agent,
        local_date="2026-06-11",
        slot=slot,
        mode=WorkHourMode.WINDOWED,
        scheduled_for=_dt(9),
        status=WorkHourStatus.RUNNING,
        routine_count=routine_count,
    )
    org_state.db.work_hours.insert(rec)
    return rec


def _client(app):
    from fastapi.testclient import TestClient
    return TestClient(app)


def test_spawn_creates_targeted_root_tasks_and_completes(tmp_home, app, org_state, auth_headers):
    client = _client(app)
    _running_wh(org_state)

    resp = client.post(
        "/api/v1/orgs/alpha/work-hours/WORKHOUR-001/spawn",
        json={
            "summary": "Launched 2 routine tasks for the 09:00 wake.",
            "routines": [
                {"slug": "triage", "brief": "Triage open tickets since last wake."},
                {"slug": "followups", "brief": "Send overdue follow-ups."},
            ],
        },
        headers=auth_headers,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert len(body["spawned_task_ids"]) == 2

    # Executor targeting (Q2): each spawned root task is created on the waking
    # agent's own team with assigned_agent == the waking worker.
    for task_id in body["spawned_task_ids"]:
        task = org_state.db.get_task(task_id)
        assert task is not None
        assert task.assigned_agent == "dev_agent"
        assert task.team == "engineering"

    # Spawned tasks appear in the normal task list (task-producing contract).
    listed = {t.id for t in org_state.db.list_tasks(limit=50)}
    assert set(body["spawned_task_ids"]) <= listed

    # Work-hour marked completed with provenance recorded.
    wh = org_state.db.work_hours.get("WORKHOUR-001")
    assert wh.status == WorkHourStatus.COMPLETED
    assert wh.spawned_task_count == 2
    assert wh.spawned_task_ids == body["spawned_task_ids"]
    assert wh.summary.startswith("Launched 2")
    assert wh.transcript_path

    # Audit rows: spawned (with id list) + completed.
    spawned = org_state.db.get_audit_logs_by_action("work_hour_spawned")
    assert spawned[0]["task_id"] == "WORKHOUR-001"
    assert spawned[0]["payload"]["task_ids"] == body["spawned_task_ids"]
    assert org_state.db.get_audit_logs_by_action("work_hour_completed")[0]["task_id"] == "WORKHOUR-001"


def test_spawn_targets_manager_wake_to_the_manager(tmp_home, app, org_state, auth_headers):
    client = _client(app)
    _running_wh(org_state, agent="engineering_head", wh_id="WORKHOUR-009", routine_count=1)

    resp = client.post(
        "/api/v1/orgs/alpha/work-hours/WORKHOUR-009/spawn",
        json={"summary": "Manager wake.", "routines": [{"brief": "Plan the sprint."}]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    task = org_state.db.get_task(resp.json()["spawned_task_ids"][0])
    # A manager wake's task is assigned to the manager; it enters the manager's
    # own decision loop rather than being re-defaulted to the team manager.
    assert task.assigned_agent == "engineering_head"
    assert task.team == "engineering"


def test_spawn_rejects_non_running_workhour_single_use_guard(tmp_home, app, org_state, auth_headers):
    client = _client(app)
    org_state.db.work_hours.insert(WorkHourRecord(
        id="WORKHOUR-002",
        agent_name="dev_agent",
        local_date="2026-06-11",
        slot="11:00",
        mode=WorkHourMode.WINDOWED,
        scheduled_for=_dt(11),
        status=WorkHourStatus.COMPLETED,
    ))
    resp = client.post(
        "/api/v1/orgs/alpha/work-hours/WORKHOUR-002/spawn",
        json={"summary": "x", "routines": [{"brief": "y"}]},
        headers=auth_headers,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "work_hour_not_running"


def test_spawn_unknown_workhour_404(tmp_home, app, org_state, auth_headers):
    client = _client(app)
    resp = client.post(
        "/api/v1/orgs/alpha/work-hours/WORKHOUR-404/spawn",
        json={"summary": "x", "routines": [{"brief": "y"}]},
        headers=auth_headers,
    )
    assert resp.status_code == 404


def test_spawn_requires_at_least_one_routine(tmp_home, app, org_state, auth_headers):
    client = _client(app)
    _running_wh(org_state, wh_id="WORKHOUR-003")
    resp = client.post(
        "/api/v1/orgs/alpha/work-hours/WORKHOUR-003/spawn",
        json={"summary": "x", "routines": []},
        headers=auth_headers,
    )
    assert resp.status_code == 422


def test_spawn_partial_failure_records_created_ids_no_rollback(
    tmp_home, app, org_state, auth_headers, monkeypatch,
):
    client = _client(app)
    _running_wh(org_state, wh_id="WORKHOUR-004")

    real_insert = org_state.db.insert_task
    calls = {"n": 0}

    def flaky_insert(task):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real_insert(task)

    monkeypatch.setattr(org_state.db, "insert_task", flaky_insert)

    resp = client.post(
        "/api/v1/orgs/alpha/work-hours/WORKHOUR-004/spawn",
        json={
            "summary": "two routines",
            "routines": [{"brief": "first"}, {"brief": "second"}],
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "failed"
    # The first task is real work and is NOT rolled back.
    assert len(body["spawned_task_ids"]) == 1
    assert org_state.db.get_task(body["spawned_task_ids"][0]) is not None

    wh = org_state.db.work_hours.get("WORKHOUR-004")
    assert wh.status == WorkHourStatus.FAILED
    assert wh.spawned_task_count == 1
    assert "partial_spawn" in (wh.error or "")


def test_status_list_show_surfaces(tmp_home, app, org_state, auth_headers):
    client = _client(app)
    _running_wh(org_state, wh_id="WORKHOUR-005", slot="13:00")

    r_status = client.get("/api/v1/orgs/alpha/work-hours/status", headers=auth_headers)
    assert r_status.status_code == 200
    assert r_status.json()["recent"][0]["work_hour_id"] == "WORKHOUR-005"

    r_list = client.get("/api/v1/orgs/alpha/work-hours?agent=dev_agent", headers=auth_headers)
    assert r_list.status_code == 200
    assert r_list.json()["work_hours"][0]["agent_name"] == "dev_agent"

    r_show = client.get("/api/v1/orgs/alpha/work-hours/WORKHOUR-005", headers=auth_headers)
    assert r_show.status_code == 200
    body = r_show.json()
    assert body["work_hour_id"] == "WORKHOUR-005"
    assert body["slot"] == "13:00"
    assert body["mode"] == "windowed"

    assert client.get(
        "/api/v1/orgs/alpha/work-hours/WORKHOUR-404", headers=auth_headers,
    ).status_code == 404
