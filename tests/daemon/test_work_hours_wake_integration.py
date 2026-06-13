"""In-process wake integration: fake-executor wake -> work-hours spawn ->
spawned root tasks run through the run_step loop, with wake token usage under
the work_hour scope.

This exercises the real runner (``run_wake``), the real callback route
(``/work-hours/{id}/spawn``), real server-side task creation, and the real
run_step loop together. It is deterministic (no wall-clock scheduler, no
subprocess daemon) so it is reliable under the session-timeout budget that
sank earlier attempts.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon.queue import TaskQueue
from runtime.daemon.wake_runner import run_wake
from runtime.infrastructure.database import Database
from runtime.models import (
    TaskRecord,
    TaskStatus,
    TokenUsage,
    WorkHourMode,
    WorkHourRecord,
    WorkHourStatus,
)
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir

pytestmark = pytest.mark.integration

_AGENT_FILE = (
    "---\nname: dev_agent\nteam: engineering\nrole: worker\nexecutor: claude\n---\n\n"
    "You are a developer.\n\n"
    "## Routine Tasks\n\n- Triage open tickets.\n- Send overdue follow-ups.\n"
)


class _FakeResult:
    def __init__(self) -> None:
        self.success = True
        self.token_usage = TokenUsage(input_tokens=100, output_tokens=40, model="claude-opus")
        self.agent_session_id = "sess-wake-1"
        self.error = None


class _SpawningExecutor:
    """A fake wake executor: instead of an LLM, it performs the wake's one job
    — calling ``work-hours spawn`` once — then returns a successful result with
    token usage (which run_wake records under the work_hour scope)."""

    def __init__(self, client, slug: str, work_hour_id: str) -> None:
        self._client = client
        self._slug = slug
        self._wh = work_hour_id

    def run(self, *, workspace, prompt, session_id, timeout_seconds):
        # The wake reads the routine checklist (verbatim in the prompt) and
        # self-dispatches via the single-line callback.
        assert "## Routine Tasks" in prompt
        resp = self._client.post(
            f"/api/v1/orgs/{self._slug}/work-hours/{self._wh}/spawn",
            json={
                "summary": "Launched routine tasks for the wake.",
                "routines": [
                    {"slug": "triage", "brief": "Triage open tickets since last wake."},
                    {"slug": "followups", "brief": "Send overdue follow-ups."},
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        return _FakeResult()


def test_wake_spawns_and_records_work_hour_scope(tmp_home, app, org_state, auth_headers):
    from fastapi.testclient import TestClient

    (org_state.root / "org" / "agents").mkdir(parents=True, exist_ok=True)
    (org_state.root / "org" / "agents" / "dev_agent.md").write_text(_AGENT_FILE)
    (org_state.root / "workspaces" / "dev_agent").mkdir(parents=True, exist_ok=True)

    client = TestClient(app)
    client.headers.update(auth_headers)

    org_state.db.work_hours.insert(WorkHourRecord(
        id="WORKHOUR-001",
        agent_name="dev_agent",
        local_date="2026-06-11",
        slot="09:00",
        mode=WorkHourMode.WINDOWED,
        scheduled_for=datetime(2026, 6, 11, 1, 0, tzinfo=timezone.utc),
        status=WorkHourStatus.PENDING,
        routine_count=2,
    ))

    fake = _SpawningExecutor(client, "alpha", "WORKHOUR-001")
    asyncio.run(run_wake(
        org_state=org_state,
        work_hour_id="WORKHOUR-001",
        settings=Settings(),
        executor_factory=lambda name, settings, _extra: fake,
    ))

    # Wake completed by the spawn callback; provenance recorded.
    wh = org_state.db.work_hours.get("WORKHOUR-001")
    assert wh.status == WorkHourStatus.COMPLETED
    assert wh.spawned_task_count == 2

    # Spawned tasks are real pending root tasks targeted to the waking agent.
    for task_id in wh.spawned_task_ids:
        task = org_state.db.get_task(task_id)
        assert task is not None
        assert task.assigned_agent == "dev_agent"
        assert task.team == "engineering"
        assert task.parent_task_id is None  # root tasks, not children

    # Wake token usage is attributed to the work_hour scope, NOT task_id.
    rows = org_state.db.list_session_token_usage(scope_type="work_hour")
    assert len(rows) == 1
    assert rows[0]["scope_id"] == "WORKHOUR-001"
    assert rows[0]["task_id"] is None
    # The spawned root tasks carry no wake-scope usage of their own.
    scope_rollup = org_state.db.aggregate_session_token_usage_by_scope()
    assert any(r["scope_type"] == "work_hour" and r["scope_id"] == "WORKHOUR-001" for r in scope_rollup)


class _LocalDispatcher:
    def __init__(self, orch: Orchestrator) -> None:
        self._orch = orch

    def run_step(self, slug: str, task_id: str, metadata: dict | None = None) -> None:
        self._orch.run_step(task_id, metadata=metadata)

    def heartbeat(self, slug: str, task_id: str) -> None:
        pass


@pytest.mark.asyncio
async def test_spawned_worker_task_runs_directly_through_the_loop(tmp_path: Path, monkeypatch):
    """A wake-spawned root task assigned to a worker is executed by that worker
    directly (bounded work -> report), NOT re-routed to the team manager."""
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, payment_agent, qa_engineer]\n"
    )
    for agent in ("engineering_head", "dev_agent"):
        skill = paths.workspaces_dir / agent / ".claude" / "skills" / "start-task"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").touch()
    db = Database(paths.db_path)
    orch = Orchestrator(
        db=db, settings=Settings(max_orchestration_steps=10), paths=paths,
        slug="test", teams=TeamsRegistry.load(paths.root),
    )
    queue = TaskQueue()
    orch.attach_queue(queue)
    dispatcher = _LocalDispatcher(orch)

    ran_by: list[str] = []

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        from runtime.orchestrator.executors import ExecutorResult
        from runtime.models import CompletionReport
        ran_by.append(agent)
        return (
            ExecutorResult(success=True, session_id="s", duration_seconds=1),
            CompletionReport(task_id=task_id, agent=agent, status="completed",
                             confidence=90, output_summary=json.dumps({"action": "done", "summary": "routine done"})),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    # Mirror exactly what the spawn route creates: a root task pre-targeted to
    # the waking worker.
    db.insert_task(TaskRecord(id="TASK-001", brief="Triage open tickets.",
                              team="engineering", assigned_agent="dev_agent"))
    queue.enqueue("test", "TASK-001")

    for _ in range(4):
        await queue.drain_sync(dispatcher)
        if db.get_task("TASK-001").status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            break

    task = db.get_task("TASK-001")
    assert task.status == TaskStatus.COMPLETED
    # run_step honored the pre-set assigned_agent: dev_agent ran it directly,
    # the engineering_head manager was never invoked.
    assert ran_by == ["dev_agent"]
