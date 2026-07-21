"""THR-105 Phase 3: Route tests for the agent Todos management surface.

Covers list/show/pause/cancel/edit for GET /schedules, GET /schedules/{id},
POST /schedules/{id}/pause, POST /schedules/{id}/cancel, and
PATCH /schedules/{id}.  Uses the ScheduleService (real) with the in-memory
per-test database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.models import ScheduleKind, ScheduleStatus
from runtime.orchestrator.schedule_rules import next_weekly_occurrence
from runtime.orchestrator.schedule_service import ScheduleService

_FROZEN_NOW = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)


def _seed_one_shot(
    org_state, agent: str = "dev_agent", brief: str = "Test one-shot todo",
    instruction: str = "Test one-shot instruction",
) -> str:
    svc = ScheduleService(org_state.db)
    record = svc.create(
        agent_name=agent,
        team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=_FROZEN_NOW + timedelta(days=6, hours=9),
        recurrence=None,
        timezone="Asia/Shanghai",
        normalized_brief=brief,
        source_instruction=instruction,
        scheduling_enabled=True,
    )
    return record.id


def _seed_weekly(
    org_state, agent: str = "dev_agent",
) -> str:
    fire_at = next_weekly_occurrence("mon", "09:00", "Asia/Shanghai", after=_FROZEN_NOW)
    assert fire_at is not None
    svc = ScheduleService(org_state.db)
    record = svc.create(
        agent_name=agent,
        team="engineering",
        kind=ScheduleKind.WEEKLY,
        fire_at=fire_at,
        recurrence={"day": "mon", "time": "09:00", "tz": "Asia/Shanghai"},
        timezone="Asia/Shanghai",
        normalized_brief="Test weekly todo",
        source_instruction="Test weekly instruction",
        scheduling_enabled=True,
    )
    return record.id


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(
        "runtime.orchestrator.schedule_service._now",
        lambda: _FROZEN_NOW,
    )
    monkeypatch.setattr(
        "runtime.infrastructure.schedule_store._now",
        lambda: _FROZEN_NOW,
    )
    return _FROZEN_NOW


# ═══════════════════════════════════════════════════════════════════════
# list
# ═══════════════════════════════════════════════════════════════════════

def test_list_empty(client, org_state) -> None:
    r = client.get(f"/api/v1/orgs/{org_state.slug}/schedules")
    assert r.status_code == 200
    assert r.json() == {"schedules": []}


def test_list_with_schedules(client, org_state, frozen_clock) -> None:
    sid1 = _seed_one_shot(org_state, agent="dev_agent")
    sid2 = _seed_weekly(org_state, agent="qa_engineer")

    r = client.get(f"/api/v1/orgs/{org_state.slug}/schedules")
    assert r.status_code == 200
    body = r.json()
    assert len(body["schedules"]) == 2
    ids = {s["schedule_id"] for s in body["schedules"]}
    assert ids == {sid1, sid2}


def test_list_filter_by_agent(client, org_state, frozen_clock) -> None:
    _seed_one_shot(org_state, agent="dev_agent")
    _seed_weekly(org_state, agent="qa_engineer")

    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/schedules",
        params={"agent": "dev_agent"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["schedules"]) == 1
    assert body["schedules"][0]["agent_name"] == "dev_agent"


def test_list_filter_by_status(client, org_state, frozen_clock) -> None:
    sid = _seed_one_shot(org_state, agent="dev_agent")
    _seed_weekly(org_state, agent="qa_engineer")
    # Pause one
    svc = ScheduleService(org_state.db)
    svc.pause(sid, "operator@test")

    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/schedules",
        params={"status": "paused"},
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["schedules"]) == 1
    assert body["schedules"][0]["status"] == "paused"


def test_list_invalid_status(client, org_state) -> None:
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/schedules",
        params={"status": "bogus"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_status"


def test_list_limit(client, org_state, frozen_clock) -> None:
    for i in range(5):
        _seed_one_shot(
            org_state, agent="dev_agent",
            brief=f"test brief {i}", instruction=f"test {i}",
        )
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/schedules",
        params={"limit": 2},
    )
    assert r.status_code == 200
    assert len(r.json()["schedules"]) == 2


# ═══════════════════════════════════════════════════════════════════════
# show
# ═══════════════════════════════════════════════════════════════════════

def test_show_found(client, org_state, frozen_clock) -> None:
    sid = _seed_one_shot(org_state, agent="dev_agent")

    r = client.get(f"/api/v1/orgs/{org_state.slug}/schedules/{sid}")
    assert r.status_code == 200
    body = r.json()
    assert body["schedule_id"] == sid
    assert body["agent_name"] == "dev_agent"
    assert body["kind"] == "one_shot"
    assert body["status"] == "armed"
    assert body["normalized_brief"] == "Test one-shot todo"


def test_show_not_found(client, org_state) -> None:
    r = client.get(f"/api/v1/orgs/{org_state.slug}/schedules/SCHEDULE-999")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "not_found"


# ═══════════════════════════════════════════════════════════════════════
# pause
# ═══════════════════════════════════════════════════════════════════════

def test_pause_armed(client, org_state, frozen_clock) -> None:
    sid = _seed_one_shot(org_state, agent="dev_agent")

    r = client.post(f"/api/v1/orgs/{org_state.slug}/schedules/{sid}/pause")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "paused"
    assert body["active"] == 0

    # Verify in DB
    record = org_state.db.schedules.get(sid)
    assert record.status == ScheduleStatus.PAUSED


def test_pause_idempotent(client, org_state, frozen_clock) -> None:
    sid = _seed_one_shot(org_state, agent="dev_agent")
    svc = ScheduleService(org_state.db)
    svc.pause(sid, "operator@test")

    r = client.post(f"/api/v1/orgs/{org_state.slug}/schedules/{sid}/pause")
    assert r.status_code == 200
    assert r.json()["status"] == "paused"


def test_pause_not_found(client, org_state) -> None:
    r = client.post(f"/api/v1/orgs/{org_state.slug}/schedules/SCHEDULE-999/pause")
    assert r.status_code == 409
    assert "not found" in r.json()["detail"]["message"]


def test_pause_cancelled_rejected(client, org_state, frozen_clock) -> None:
    sid = _seed_one_shot(org_state, agent="dev_agent")
    svc = ScheduleService(org_state.db)
    svc.cancel(sid, "operator@test")

    r = client.post(f"/api/v1/orgs/{org_state.slug}/schedules/{sid}/pause")
    assert r.status_code == 409
    assert "armed" in r.json()["detail"]["message"]


# ═══════════════════════════════════════════════════════════════════════
# cancel
# ═══════════════════════════════════════════════════════════════════════

def test_cancel_armed(client, org_state, frozen_clock) -> None:
    sid = _seed_one_shot(org_state, agent="dev_agent")

    r = client.post(f"/api/v1/orgs/{org_state.slug}/schedules/{sid}/cancel")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "cancelled"
    assert body["active"] == 0

    record = org_state.db.schedules.get(sid)
    assert record.status == ScheduleStatus.CANCELLED


def test_cancel_paused(client, org_state, frozen_clock) -> None:
    sid = _seed_one_shot(org_state, agent="dev_agent")
    svc = ScheduleService(org_state.db)
    svc.pause(sid, "operator@test")

    r = client.post(f"/api/v1/orgs/{org_state.slug}/schedules/{sid}/cancel")
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_cancel_already_cancelled(client, org_state, frozen_clock) -> None:
    sid = _seed_one_shot(org_state, agent="dev_agent")
    svc = ScheduleService(org_state.db)
    svc.cancel(sid, "operator@test")

    r = client.post(f"/api/v1/orgs/{org_state.slug}/schedules/{sid}/cancel")
    assert r.status_code == 409
    assert "cannot cancel" in r.json()["detail"]["message"]


def test_cancel_not_found(client, org_state) -> None:
    r = client.post(f"/api/v1/orgs/{org_state.slug}/schedules/SCHEDULE-999/cancel")
    assert r.status_code == 409
    assert "not found" in r.json()["detail"]["message"]


# ═══════════════════════════════════════════════════════════════════════
# edit
# ═══════════════════════════════════════════════════════════════════════

def test_edit_fire_at(client, org_state, frozen_clock) -> None:
    sid = _seed_one_shot(org_state, agent="dev_agent")
    new_fire = (_FROZEN_NOW + timedelta(days=30)).isoformat()

    r = client.patch(
        f"/api/v1/orgs/{org_state.slug}/schedules/{sid}",
        json={"fire_at": new_fire},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "armed"
    assert body["fire_at"] == new_fire

    record = org_state.db.schedules.get(sid)
    assert record.fire_at.isoformat() == new_fire


def test_edit_recurrence_weekly(client, org_state, frozen_clock) -> None:
    sid = _seed_weekly(org_state, agent="dev_agent")

    new_recurrence = {"day": "wed", "time": "14:00", "tz": "UTC"}
    new_fire = next_weekly_occurrence("wed", "14:00", "UTC", after=_FROZEN_NOW)
    assert new_fire is not None
    r = client.patch(
        f"/api/v1/orgs/{org_state.slug}/schedules/{sid}",
        json={
            "recurrence": new_recurrence,
            "timezone": "UTC",
            "fire_at": new_fire.isoformat(),
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["recurrence"]["day"] == "wed"
    assert body["recurrence"]["time"] == "14:00"


def test_edit_noop(client, org_state, frozen_clock) -> None:
    sid = _seed_one_shot(org_state, agent="dev_agent")

    r = client.patch(
        f"/api/v1/orgs/{org_state.slug}/schedules/{sid}",
        json={},
    )
    assert r.status_code == 200


def test_edit_not_found(client, org_state) -> None:
    r = client.patch(
        f"/api/v1/orgs/{org_state.slug}/schedules/SCHEDULE-999",
        json={"fire_at": "2026-08-01T00:00:00+00:00"},
    )
    assert r.status_code == 409
    assert "not found" in r.json()["detail"]["message"]


def test_edit_cancelled_rejected(client, org_state, frozen_clock) -> None:
    sid = _seed_one_shot(org_state, agent="dev_agent")
    svc = ScheduleService(org_state.db)
    svc.cancel(sid, "operator@test")

    r = client.patch(
        f"/api/v1/orgs/{org_state.slug}/schedules/{sid}",
        json={"fire_at": "2026-08-01T00:00:00+00:00"},
    )
    assert r.status_code == 409
    assert "cannot edit" in r.json()["detail"]["message"]


def test_edit_invalid_fire_at_format(client, org_state, frozen_clock) -> None:
    sid = _seed_one_shot(org_state, agent="dev_agent")

    r = client.patch(
        f"/api/v1/orgs/{org_state.slug}/schedules/{sid}",
        json={"fire_at": "not-a-date"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_fire_at"


def test_edit_one_shot_with_recurrence_rejected(client, org_state, frozen_clock) -> None:
    sid = _seed_one_shot(org_state, agent="dev_agent")

    r = client.patch(
        f"/api/v1/orgs/{org_state.slug}/schedules/{sid}",
        json={"recurrence": {"day": "mon", "time": "09:00", "tz": "UTC"}},
    )
    assert r.status_code == 409
    assert "one-shot" in r.json()["detail"]["message"]
