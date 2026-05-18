"""Async end-to-end: EH delegates → child runs → parent resumes → parent completes."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.config import Settings
from src.daemon.queue import TaskQueue
from src.daemon.agent_config import load_agent_config
from src.infrastructure.database import Database
from src.models import TaskRecord, TaskStatus
from src.orchestrator._paths import OrgPaths
from src.orchestrator.orchestrator import Orchestrator
from src.orchestrator.teams import TeamsRegistry
from src.runtime import RuntimeDir


def _seed_teams(paths: OrgPaths) -> None:
    """Seed a minimal teams.yaml so team manager lookups work and the org is
    discoverable by ``RuntimeDir.iter_org_roots`` (which gates on the
    presence of ``org/teams.yaml``)."""
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, payment_agent, qa_engineer]\n"
        "  content:\n"
        "    manager: content_manager\n"
        "    workers: [content_writer, content_qa, seo_agent]\n"
    )


def _make_org_paths(tmp_path: Path) -> tuple[RuntimeDir, OrgPaths]:
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    paths.root.mkdir(parents=True, exist_ok=True)
    return rt, paths


class _LocalDispatcher:
    """Minimal dispatcher that ignores the slug and dispatches to a single
    Orchestrator. ``TaskQueue.drain_sync`` requires a dispatcher with
    ``run_step(slug, task_id)`` + ``heartbeat(slug, task_id)``; for tests
    that only exercise a single org we just forward to the orchestrator."""

    def __init__(self, orch: Orchestrator) -> None:
        self._orch = orch

    def run_step(self, slug: str, task_id: str) -> None:
        self._orch.run_step(task_id)

    def heartbeat(self, slug: str, task_id: str) -> None:
        # No-op — heartbeat liveness isn't exercised by these tests.
        pass


@pytest.mark.asyncio
async def test_full_delegation_roundtrip(tmp_path: Path, monkeypatch):
    rt, paths = _make_org_paths(tmp_path)
    _seed_teams(paths)
    (paths.workspaces_dir / "engineering_head" / ".claude" / "skills" / "start-task").mkdir(parents=True)
    (paths.workspaces_dir / "engineering_head" / ".claude" / "skills" / "start-task" / "SKILL.md").touch()
    (paths.workspaces_dir / "dev_agent" / ".claude" / "skills" / "start-task").mkdir(parents=True)
    (paths.workspaces_dir / "dev_agent" / ".claude" / "skills" / "start-task" / "SKILL.md").touch()
    db = Database(paths.db_path)

    orch = Orchestrator(
        db=db,
        settings=Settings(max_orchestration_steps=10),
        paths=paths,
        slug="test",
        teams=TeamsRegistry.load(paths.root),
    )
    queue = TaskQueue()
    orch.attach_queue(queue)
    dispatcher = _LocalDispatcher(orch)

    # Fake `_run_agent`: EH first returns delegate, second call returns done;
    # dev_agent returns done.
    call_log: list[tuple[str, str]] = []
    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        call_log.append((task_id, agent))
        from src.orchestrator.executors import ExecutorResult
        from src.models import CompletionReport
        if agent == "engineering_head":
            # First EH pass delegates; second is `done`.
            past_eh_calls = sum(1 for (_t, a) in call_log if a == "engineering_head")
            if past_eh_calls == 1:
                summary = json.dumps({
                    "action": "delegate",
                    "agent": "dev_agent",
                    "prompt": "Write feature",
                })
            else:
                summary = json.dumps({"action": "done", "summary": "Root done"})
        else:
            summary = json.dumps({"action": "done", "summary": "Child done"})
        return (
            ExecutorResult(success=True, session_id="s", duration_seconds=1),
            CompletionReport(task_id=task_id, agent=agent, status="completed",
                             confidence=80, output_summary=summary),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    # Seed the root
    db.insert_task(TaskRecord(id="TASK-001", brief="build"))
    queue.enqueue("test", "TASK-001")

    # Drain in two passes — delegate creates a child and enqueues it, which
    # drain_sync will pick up on the same pass. But run_step is synchronous
    # inside drain, so one drain_sync call may not suffice; iterate until
    # queue is empty AND the root is terminal.
    for _ in range(6):
        await queue.drain_sync(dispatcher)
        root = db.get_task("TASK-001")
        if root.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            break

    root = db.get_task("TASK-001")
    assert root.status == TaskStatus.COMPLETED
    assert root.note == "Root done"
    # Exactly one child, completed, with brief from the delegate prompt
    children = db.get_children("TASK-001")
    assert len(children) == 1
    child = db.get_task(children[0])
    assert child.status == TaskStatus.COMPLETED
    assert child.assigned_agent == "dev_agent"


@pytest.mark.asyncio
async def test_escalation_roundtrip(tmp_path: Path, monkeypatch):
    rt, paths = _make_org_paths(tmp_path)
    _seed_teams(paths)
    (paths.workspaces_dir / "engineering_head" / ".claude" / "skills" / "start-task").mkdir(parents=True)
    (paths.workspaces_dir / "engineering_head" / ".claude" / "skills" / "start-task" / "SKILL.md").touch()
    db = Database(paths.db_path)

    orch = Orchestrator(
        db=db,
        settings=Settings(),
        paths=paths,
        slug="test",
        teams=TeamsRegistry.load(paths.root),
    )
    queue = TaskQueue()
    orch.attach_queue(queue)
    dispatcher = _LocalDispatcher(orch)

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        from src.orchestrator.executors import ExecutorResult
        from src.models import CompletionReport
        # First EH call: escalate. Second EH call (after founder resolves):
        # done.
        past = sum(1 for _ in db.get_audit_logs(task_id)
                   if _["action"] == "orchestration_step")
        if past == 0:
            summary = json.dumps({"action": "escalate", "reason": "needs founder"})
        else:
            summary = json.dumps({"action": "done", "summary": "resumed ok"})
        return (
            ExecutorResult(success=True, session_id="s", duration_seconds=1),
            CompletionReport(task_id=task_id, agent=agent, status="completed",
                             confidence=80, output_summary=summary),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    db.insert_task(TaskRecord(id="TASK-001", brief="x"))
    queue.enqueue("test", "TASK-001")
    await queue.drain_sync(dispatcher)

    # Task should now be blocked(escalated)
    t = db.get_task("TASK-001")
    assert t.status == TaskStatus.BLOCKED
    from src.models import BlockKind
    assert t.block_kind == BlockKind.ESCALATED
    assert t.note == "needs founder"

    # Founder resolves directly via update_task (no HTTP here)
    # — mimic what the route does.
    # Directly call DB update as the route would.
    db.update_task("TASK-001", status=TaskStatus.COMPLETED, block_kind=None)
    # No parent, nothing to enqueue. Test asserts the status-transition path.

    assert db.get_task("TASK-001").status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_init_agents_uses_enrollment_executor_for_workspace_bootstrap(
    tmp_path: Path,
    monkeypatch,
):
    from fastapi.testclient import TestClient
    from src.daemon.app import create_app
    from src.daemon.state import DaemonState
    from src.daemon.paths import ensure_token
    from src.daemon import runtimes as runtimes_mod
    from src.orchestrator.agent_def import AgentDef, render_agent_text
    from datetime import datetime, timezone

    rt, paths = _make_org_paths(tmp_path)
    _seed_teams(paths)
    # Seed an active agent file with executor=codex.
    agent = AgentDef(
        name="content_writer", team="content", role="worker", executor="codex",
        allow_rules=(), repos={}, enrolled_by=None, enrolled_at_task=None,
        enrolled_at=datetime.now(timezone.utc), system_prompt="prompt\n",
    )
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / "content_writer.md").write_text(render_agent_text(agent))
    settings = Settings(project_root=Path("/Users/tangbz/projects/my-opc/.worktrees/codex-executor"))

    daemon_home = tmp_path / "home"
    monkeypatch.setenv("GRASSLAND_DAEMON_HOME", str(daemon_home))
    token = ensure_token()
    runtimes_mod.register(rt.root)

    state = DaemonState.from_runtime(rt, settings)
    app = create_app(state)

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None
        mock_ctx.create_agent_dirs.return_value = None

        response = TestClient(app).post(
            "/api/v1/orgs/test/agents/init",
            json={"agent": "content_writer"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    workspace = paths.workspaces_dir / "content_writer"
    cfg = load_agent_config(workspace)
    assert cfg["executor"] == "codex"
    assert mock_ctx.ensure_workspace_ready.call_args.kwargs["provider"] == "codex"


@pytest.mark.asyncio
async def test_approve_agent_uses_provider_specific_workspace_bootstrap(
    tmp_path: Path,
    monkeypatch,
):
    from fastapi.testclient import TestClient
    from src.daemon.app import create_app
    from src.daemon.state import DaemonState
    from src.daemon.paths import ensure_token
    from src.daemon import runtimes as runtimes_mod
    from src.orchestrator import prompt_loader
    from src.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone

    rt, paths = _make_org_paths(tmp_path)
    _seed_teams(paths)
    # Seed a pending agent file with executor=codex.
    agent = AgentDef(
        name="content_writer", team="content", role="worker", executor="codex",
        allow_rules=(), repos={}, enrolled_by=None, enrolled_at_task=None,
        enrolled_at=datetime.now(timezone.utc), system_prompt="prompt\n",
    )
    prompt_loader.write_pending_agent(paths, agent)
    settings = Settings(project_root=Path("/Users/tangbz/projects/my-opc/.worktrees/codex-executor"))

    daemon_home = tmp_path / "home"
    monkeypatch.setenv("GRASSLAND_DAEMON_HOME", str(daemon_home))
    token = ensure_token()
    runtimes_mod.register(rt.root)

    state = DaemonState.from_runtime(rt, settings)
    app = create_app(state)

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None
        mock_ctx.create_agent_dirs.return_value = None

        response = TestClient(app).post(
            "/api/v1/orgs/test/agents/content_writer/approve",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    workspace = paths.workspaces_dir / "content_writer"
    cfg = load_agent_config(workspace)
    assert cfg["executor"] == "codex"
    assert mock_ctx.ensure_workspace_ready.call_args.kwargs["provider"] == "codex"
