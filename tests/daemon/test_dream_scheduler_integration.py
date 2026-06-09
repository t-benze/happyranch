from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from runtime.daemon.dream_scheduler import schedule_due_dreams, recover_running_dreams
from runtime.models import DreamRecord, DreamStatus


def test_schedule_due_dreams_inserts_and_enqueues(org_state, monkeypatch):
    (org_state.root / "org" / "agents").mkdir(parents=True, exist_ok=True)
    (org_state.root / "org" / "agents" / "dev_agent.md").write_text("---\nname: dev_agent\nteam: engineering\nrole: worker\nexecutor: claude\n---\n\nYou are a developer agent.\n")
    (org_state.root / "workspaces" / "dev_agent").mkdir(parents=True, exist_ok=True)
    (org_state.root / "org" / "config.yaml").write_text("""
dreaming:
  enabled: true
  schedule:
    time: "02:00"
    timezone: "Asia/Shanghai"
  agents:
    mode: all
""")
    enqueued = []

    async def put(job):
        enqueued.append(job)

    monkeypatch.setattr(org_state.dream_queue, "put", put)

    count = schedule_due_dreams(
        org=org_state,
        now=datetime(2026, 6, 9, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert count == 1
    dream = org_state.db.get_dream_for_agent_date("dev_agent", "2026-06-09")
    assert dream is not None
    assert enqueued[0].dream_id == dream.id


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
