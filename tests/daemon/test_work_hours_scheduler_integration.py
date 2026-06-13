from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from runtime.daemon.work_hours_scheduler import schedule_due_wakes
from runtime.models import WorkHourMode, WorkHourRecord, WorkHourStatus

_SH = ZoneInfo("Asia/Shanghai")

_WINDOWED_AGENT = (
    "---\nname: dev_agent\nteam: engineering\nrole: worker\nexecutor: claude\n---\n\n"
    "You are a developer.\n\n"
    "## Routine Tasks\n\n- Triage tickets.\n- Send follow-ups.\n"
)
_CONTINUOUS_AGENT = (
    "---\nname: content_writer\nteam: content\nrole: worker\nexecutor: claude\n---\n\n"
    "You write content.\n\n"
    "## Routine Tasks\n\n- Resolve incoming requests.\n"
)
_NO_ROUTINE_AGENT = (
    "---\nname: qa_engineer\nteam: engineering\nrole: worker\nexecutor: claude\n---\n\n"
    "You are QA. No routine section here.\n"
)

# default: windowed 09:00-18:00 / 2h / Asia/Shanghai, weekdays.
# content_writer override: continuous hourly. qa_engineer: no routines.
_CONFIG = (
    "working_hours:\n"
    "  enabled: true\n"
    "  default:\n"
    "    mode: windowed\n"
    "    window:\n"
    '      start: "09:00"\n'
    '      end: "18:00"\n'
    '      timezone: "Asia/Shanghai"\n'
    '    interval: "2h"\n'
    "    days: [mon, tue, wed, thu, fri]\n"
    "  agents:\n"
    "    mode: all\n"
    "  overrides:\n"
    "    content_writer:\n"
    "      mode: continuous\n"
    '      interval: "1h"\n'
    '      timezone: "Asia/Shanghai"\n'
)


def _seed(org_state, *, catch_up: bool | None = None, agents=(_WINDOWED_AGENT, _CONTINUOUS_AGENT)) -> None:
    agents_dir = org_state.root / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    for text in agents:
        name = text.split("name: ", 1)[1].split("\n", 1)[0]
        (agents_dir / f"{name}.md").write_text(text)
        (org_state.root / "workspaces" / name).mkdir(parents=True, exist_ok=True)
    config = _CONFIG
    if catch_up is not None:
        config = config.replace(
            '    interval: "2h"\n',
            f'    interval: "2h"\n    catch_up_on_startup: {str(catch_up).lower()}\n',
        )
    (org_state.root / "org" / "config.yaml").write_text(config)


def _capture(org_state, monkeypatch) -> list:
    enqueued: list = []
    monkeypatch.setattr(org_state.wake_queue, "put_nowait", enqueued.append)
    return enqueued


def test_windowed_wake_enqueued_on_weekday(org_state, monkeypatch):
    _seed(org_state)
    enqueued = _capture(org_state, monkeypatch)
    # Thursday 09:30 Shanghai -> windowed due slot 09:00; continuous hourly -> 09:00.
    now = datetime(2026, 6, 11, 9, 30, tzinfo=_SH)

    schedule_due_wakes(org=org_state, now=now)

    dev = org_state.db.work_hours.get_for_agent_date_slot("dev_agent", "2026-06-11", "09:00")
    assert dev is not None and dev.status == WorkHourStatus.PENDING
    assert dev.mode == WorkHourMode.WINDOWED
    assert dev.routine_count == 2
    cw = org_state.db.work_hours.get_for_agent_date_slot("content_writer", "2026-06-11", "09:00")
    assert cw is not None and cw.mode == WorkHourMode.CONTINUOUS
    enqueued_ids = {j.work_hour_id for j in enqueued}
    assert dev.id in enqueued_ids and cw.id in enqueued_ids


def test_windowed_silent_on_weekend_but_continuous_fires(org_state, monkeypatch):
    _seed(org_state)
    _capture(org_state, monkeypatch)
    # Saturday 10:05 Shanghai: windowed dev_agent has NO valid slot (not a
    # configured day); continuous content_writer still fires (hourly, every day).
    now = datetime(2026, 6, 13, 10, 5, tzinfo=_SH)

    schedule_due_wakes(org=org_state, now=now)

    assert org_state.db.work_hours.get_for_agent_date_slot("dev_agent", "2026-06-13", "10:00") is None
    assert not org_state.db.work_hours.list(agent="dev_agent")
    cw = org_state.db.work_hours.get_for_agent_date_slot("content_writer", "2026-06-13", "10:00")
    assert cw is not None


def test_continuous_midnight_rollover_assigns_slot_to_new_date(org_state, monkeypatch):
    _seed(org_state)
    _capture(org_state, monkeypatch)
    # 00:05 Shanghai: the continuous 00:00 slot belongs to the NEW local_date,
    # so the grid restarts cleanly with no cross-date collision.
    now = datetime(2026, 6, 13, 0, 5, tzinfo=_SH)

    schedule_due_wakes(org=org_state, now=now)

    cw = org_state.db.work_hours.get_for_agent_date_slot("content_writer", "2026-06-13", "00:00")
    assert cw is not None
    assert cw.local_date == "2026-06-13"
    assert cw.slot == "00:00"


def test_uniqueness_guard_blocks_duplicate_slot(org_state, monkeypatch):
    _seed(org_state)
    _capture(org_state, monkeypatch)
    now = datetime(2026, 6, 11, 9, 30, tzinfo=_SH)

    first = schedule_due_wakes(org=org_state, now=now)
    second = schedule_due_wakes(org=org_state, now=now)

    assert first >= 1
    assert second == 0  # same (agent, local_date, slot) -> no new rows
    assert len(org_state.db.work_hours.list(agent="dev_agent")) == 1


def test_startup_catch_up_false_records_skipped_row(org_state, monkeypatch):
    _seed(org_state, catch_up=False)
    enqueued = _capture(org_state, monkeypatch)
    now = datetime(2026, 6, 11, 9, 30, tzinfo=_SH)

    schedule_due_wakes(org=org_state, now=now, startup=True)

    dev = org_state.db.work_hours.get_for_agent_date_slot("dev_agent", "2026-06-11", "09:00")
    assert dev is not None and dev.status == WorkHourStatus.SKIPPED
    assert all(j.work_hour_id != dev.id for j in enqueued)


def test_absent_routine_section_schedules_no_row(org_state, monkeypatch):
    # qa_engineer has NO '## Routine Tasks' section -> never accrues a row even
    # though it is otherwise selected.
    _seed(org_state, agents=(_NO_ROUTINE_AGENT,))
    _capture(org_state, monkeypatch)
    now = datetime(2026, 6, 11, 9, 30, tzinfo=_SH)

    count = schedule_due_wakes(org=org_state, now=now)

    assert count == 0
    assert org_state.db.work_hours.list(agent="qa_engineer") == []


def test_recover_running_marks_stale_failed(org_state):
    org_state.db.work_hours.insert(WorkHourRecord(
        id="WORKHOUR-077",
        agent_name="dev_agent",
        local_date="2026-06-11",
        slot="09:00",
        mode=WorkHourMode.WINDOWED,
        scheduled_for=datetime(2026, 6, 11, 1, 0, tzinfo=timezone.utc),
        status=WorkHourStatus.RUNNING,
    ))

    changed = org_state.db.work_hours.recover_running()

    assert changed == 1
    wh = org_state.db.work_hours.get("WORKHOUR-077")
    assert wh.status == WorkHourStatus.FAILED
    assert wh.error == "daemon_restart"
