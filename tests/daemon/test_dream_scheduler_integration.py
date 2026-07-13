from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from runtime.daemon.dream_scheduler import schedule_due_dreams, recover_running_dreams
from runtime.models import DreamRecord, DreamStatus


def _seed_org(org_state, *, catch_up: bool | None = None) -> None:
    (org_state.root / "org" / "agents").mkdir(parents=True, exist_ok=True)
    (org_state.root / "org" / "agents" / "dev_agent.md").write_text(
        "---\nname: dev_agent\nteam: engineering\nrole: worker\nexecutor: claude\n---\n\nYou are a developer agent.\n"
    )
    (org_state.root / "workspaces" / "dev_agent").mkdir(parents=True, exist_ok=True)
    # THR-095: DB-backed storage — write dreaming config to DB, not config.yaml.
    import json
    catch_up_val = True if catch_up is None else catch_up
    dreaming = {
        "enabled": True,
        "schedule": {"time": "02:00", "timezone": "Asia/Shanghai", "catch_up_on_startup": catch_up_val},
        "agents": {"mode": "all", "include": [], "exclude": []},
    }
    org_state.db.upsert_org_setting("dreaming", json.dumps(dreaming))


def _capture_enqueue(org_state, monkeypatch) -> list:
    enqueued: list = []
    monkeypatch.setattr(org_state.dream_queue, "put_nowait", enqueued.append)
    return enqueued


# Passed local time: 03:00 Shanghai when the dream is scheduled for 02:00.
_AFTER_TIME = datetime(2026, 6, 9, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_schedule_due_dreams_inserts_and_enqueues(org_state, monkeypatch):
    _seed_org(org_state)
    enqueued = _capture_enqueue(org_state, monkeypatch)

    count = schedule_due_dreams(org=org_state, now=_AFTER_TIME)

    assert count == 1
    dream = org_state.db.get_dream_for_agent_date("dev_agent", "2026-06-09")
    assert dream is not None
    assert dream.status == DreamStatus.PENDING
    assert enqueued[0].dream_id == dream.id


def test_loop_mode_enqueues_passed_dream(org_state, monkeypatch):
    """Steady-state loop scheduling (startup=False) always enqueues a due dream."""
    _seed_org(org_state, catch_up=False)
    enqueued = _capture_enqueue(org_state, monkeypatch)

    count = schedule_due_dreams(org=org_state, now=_AFTER_TIME, startup=False)

    assert count == 1
    assert len(enqueued) == 1


def test_startup_catch_up_false_skips_passed_dream(org_state, monkeypatch):
    """Startup with catch_up_on_startup=false must NOT enqueue today's already-passed
    dream; it records a SKIPPED row so the steady-state loop will not pick it up later."""
    _seed_org(org_state, catch_up=False)
    enqueued = _capture_enqueue(org_state, monkeypatch)

    count = schedule_due_dreams(org=org_state, now=_AFTER_TIME, startup=True)

    assert count == 0
    assert enqueued == []
    dream = org_state.db.get_dream_for_agent_date("dev_agent", "2026-06-09")
    assert dream is not None
    assert dream.status == DreamStatus.SKIPPED


def test_startup_catch_up_true_enqueues_passed_dream(org_state, monkeypatch):
    _seed_org(org_state, catch_up=True)
    enqueued = _capture_enqueue(org_state, monkeypatch)

    count = schedule_due_dreams(org=org_state, now=_AFTER_TIME, startup=True)

    assert count == 1
    assert len(enqueued) == 1
    dream = org_state.db.get_dream_for_agent_date("dev_agent", "2026-06-09")
    assert dream.status == DreamStatus.PENDING


def test_recover_running_dreams_marks_failed(org_state):
    org_state.db.insert_dream(DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=datetime(2026, 6, 9, 2, 0, tzinfo=ZoneInfo("UTC")),
        window_end=datetime(2026, 6, 9, 2, 0, tzinfo=ZoneInfo("UTC")),
        status=DreamStatus.RUNNING,
    ))

    changed = recover_running_dreams(org_state)

    assert changed == 1
    dream = org_state.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert dream.error == "daemon_restart"
