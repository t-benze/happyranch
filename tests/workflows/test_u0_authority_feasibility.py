from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from runtime.models import CompletionReport
from runtime.orchestrator import prompt_loader
from runtime.orchestrator.chain import build_prior_leg_context


def _paths(org_state):
    from runtime.orchestrator._paths import OrgPaths

    return OrgPaths(root=org_state.root)


def _org_state(tmp_path):
    """Minimal real daemon state; no daemon lifecycle or queue is started."""
    from runtime.config import Settings
    from runtime.daemon.state import DaemonState
    from runtime.runtime import RuntimeDir

    runtime = RuntimeDir.init(tmp_path / "runtime")
    root = runtime.orgs_dir / "alpha"
    (root / "org").mkdir(parents=True)
    (root / "org" / "teams.yaml").write_text(
        "teams:\n  engineering:\n    manager: engineering_head\n    workers: [dev_agent]\n"
    )
    state = DaemonState.from_runtime(runtime, Settings())
    org_state = state.orgs["alpha"]
    org_state._u0_daemon_state = state
    return org_state


def _seed_agent(org_state, name: str, prompt: str) -> None:
    from datetime import datetime, timezone
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text

    paths = _paths(org_state)
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    agent = AgentDef(name=name, team="engineering", role="worker", executor="claude", allow_rules=(), repos={}, enrolled_by="engineering_head", enrolled_at_task="TASK-U0", enrolled_at=datetime.now(timezone.utc), system_prompt=prompt)
    (paths.agents_dir / f"{name}.md").write_text(render_agent_text(agent))


def _update(revision: str, prompt: str):
    from runtime.daemon.routes.agents import ManageAgentBody

    return ManageAgentBody(action="update", name="dev_agent", task_id="TASK-U0", session_id="sess-u0", expected_revision=revision, system_prompt=prompt)


def _r1_snapshot(*, db, tracker, paths, queue, task_ids: tuple[str, ...],
                 agent_names: tuple[str, ...] = ()) -> dict[str, object]:
    """Capture the shipping evidence surfaces; proposed workflow tables do not exist.

    This is deliberately a test helper, not a production evidence framework.
    It records empty attachment observations as such rather than treating them
    as evidence that an attachment-cleanup contract ran.
    """
    def record(value):
        if value is None:
            return None
        return dict(value) if isinstance(value, dict) else value.model_dump()

    rows = {task_id: record(db.get_task(task_id)) for task_id in task_ids}
    agents = {
        task_id: rows[task_id]["assigned_agent"] if rows[task_id] else None
        for task_id in task_ids
    }
    identities = set(agent_names) | {agent for agent in agents.values() if agent}
    return {
        "tasks": rows,
        "attachments": {task_id: [record(row) for row in db.list_task_attachments(task_id)]
                        for task_id in task_ids},
        "audits": {task_id: db.get_audit_logs(task_id) for task_id in task_ids},
        "results": {task_id: [record(row) for row in db.get_task_results(task_id)]
                    for task_id in task_ids},
        "sessions": {task_id: tracker.get_active(task_id, agents[task_id])
                     if agents[task_id] else None for task_id in task_ids},
        "controls": {task_id: tracker.get_cancel_control(task_id, agents[task_id])
                     is not None if agents[task_id] else False for task_id in task_ids},
        "queue": list(queue._queue._queue),
        "canonical_agents": {name: (paths.agents_dir / f"{name}.md").exists()
                             for name in identities},
        "archived_agents": {name: (paths.agents_dir / "_terminated" / f"{name}.md").exists()
                            for name in identities},
        "workspaces": {name: (paths.workspaces_dir / name).exists()
                       for name in identities},
        "archived_workspaces": {name: (paths.workspaces_dir / "_terminated" / name).exists()
                                for name in identities},
        "proposed_workflow_relations": "NOT PRESENT IN SHIPPING SCHEMA",
        "active_chain": {task_id: rows[task_id]["active_chain"] if rows[task_id] else None
                         for task_id in task_ids},
        "active_fanout": {task_id: rows[task_id]["active_fanout"] if rows[task_id] else None
                          for task_id in task_ids},
    }


def test_actual_prior_leg_context_is_immediate_report_only_not_authority_snapshot() -> None:
    report = CompletionReport(task_id="TASK-1", agent="maker_a", status="completed", confidence=90, output_summary="approved r1", verdict="APPROVE", output_dir="output/TASK-1")
    context = build_prior_leg_context(child_task_id="TASK-1", report=report)
    assert "maker_a" in context and "approved r1" in context
    assert "submission_digest" not in context
    assert "authority_envelope" not in context
    assert "current_revision" not in context


def test_actual_manage_agent_route_demonstrates_hash_aba_and_no_dispatch(tmp_path) -> None:
    """Supported route experiment: A→B→A re-admits stale A's content hash."""
    from runtime.daemon.routes import agents as agents_mod

    org_state = _org_state(tmp_path)
    org_state.sessions.set_active("TASK-U0", "engineering_head", "sess-u0")
    _seed_agent(org_state, "dev_agent", "A\n")
    paths = _paths(org_state)
    a = prompt_loader.agent_revision(paths, "dev_agent")
    assert a is not None

    async def exercise() -> None:
        assert await agents_mod.manage_agent("alpha", _update(a, "B\n"), org_state) == {"ok": True}
        b = prompt_loader.agent_revision(paths, "dev_agent")
        assert b is not None and b != a
        assert await agents_mod.manage_agent("alpha", _update(b, "A\n"), org_state) == {"ok": True}
        assert prompt_loader.agent_revision(paths, "dev_agent") == a
        assert await agents_mod.manage_agent("alpha", _update(a, "C\n"), org_state) == {"ok": True}

    asyncio.run(exercise())
    assert "C" in (paths.agents_dir / "dev_agent.md").read_text()
    assert not (paths.workspaces_dir / "dev_agent").exists()
    assert len([r for r in org_state.db.get_audit_logs("TASK-U0") if r["action"] == "agent_managed"]) == 3


def test_actual_manage_agent_route_serializes_same_hash_writers_at_lock(tmp_path) -> None:
    from runtime.daemon.routes import agents as agents_mod

    org_state = _org_state(tmp_path)
    org_state.sessions.set_active("TASK-U0", "engineering_head", "sess-u0")
    _seed_agent(org_state, "dev_agent", "initial\n")
    revision = prompt_loader.agent_revision(_paths(org_state), "dev_agent")
    assert revision is not None

    async def exercise() -> None:
        """Both writers arrive before either result is inspected.

        The route retains ownership of the real ``teams_lock`` and its
        synchronous replace/commit.  These events merely make contender
        arrival explicit; they do not manufacture lock acquisition or a
        winner ordering.
        """
        arrived = asyncio.Event()
        release = asyncio.Event()
        arrivals = 0

        async def contend(prompt: str):
            nonlocal arrivals
            arrivals += 1
            if arrivals == 2:
                arrived.set()
            await release.wait()
            return await agents_mod.manage_agent("alpha", _update(revision, prompt), org_state)

        winner = asyncio.create_task(contend("winner\n"))
        loser = asyncio.create_task(contend("loser\n"))
        await asyncio.wait_for(arrived.wait(), timeout=1)
        release.set()
        outcomes = await asyncio.gather(winner, loser, return_exceptions=True)
        successes = [result for result in outcomes if result == {"ok": True}]
        stale = [result for result in outcomes if isinstance(result, HTTPException)]
        assert successes == [{"ok": True}]
        assert len(stale) == 1
        assert stale[0].status_code == 409
        assert stale[0].detail["code"] == "stale_agent_revision"

    asyncio.run(exercise())
    current = (_paths(org_state).agents_dir / "dev_agent.md").read_text()
    assert ("winner" in current) ^ ("loser" in current)
    assert len([r for r in org_state.db.get_audit_logs("TASK-U0") if r["action"] == "agent_managed"]) == 1


def test_diagnostic_parent_dispatches_child_through_real_queue(tmp_path, monkeypatch) -> None:
    """Diagnostic queue control only: replacing `_run_agent` bypasses launch proof."""
    from runtime.daemon.dispatcher import Dispatcher
    from runtime.models import CompletionReport, NextStep, TaskRecord, TaskStatus
    from runtime.orchestrator.executors import ExecutorResult

    org_state = _org_state(tmp_path)
    _seed_agent(org_state, "engineering_head", "manager\n")
    _seed_agent(org_state, "dev_agent", "worker\n")
    paths = _paths(org_state)
    paths.workspaces_dir.joinpath("engineering_head").mkdir(parents=True)
    paths.workspaces_dir.joinpath("dev_agent").mkdir(parents=True)
    parent = TaskRecord(
        id="TASK-U0-PARENT", team="engineering", brief="delegate once",
        assigned_agent="engineering_head", task_type="task",
    )
    org_state.db.insert_task(parent)
    launches: list[tuple[str, str]] = []

    def recording_executor(task_id, agent, prompt, on_session_started=None):
        launches.append((task_id, agent))
        decision = (
            NextStep(action="delegate", agent="dev_agent", prompt="bounded child")
            if task_id == "TASK-U0-PARENT" and launches.count((task_id, agent)) == 1
            else NextStep(action="done", summary="child completed")
        )
        return (
            ExecutorResult(success=True, session_id=f"sess-{task_id}", duration_seconds=0),
            CompletionReport(
                task_id=task_id, agent=agent, status="completed", confidence=100,
                output_summary=decision.summary or "delegate", decision=decision,
            ),
        )

    monkeypatch.setattr(org_state.orchestrator, "_run_agent", recording_executor)
    # This is the actual daemon queue/dispatcher path, but this deliberately
    # replaces `_run_agent`; it cannot establish session binding, scratch,
    # validator, contained supervisor launch, or DB completion readback.
    org_state._u0_daemon_state.queue.enqueue("alpha", parent.id)
    asyncio.run(org_state._u0_daemon_state.queue.drain_sync(Dispatcher(org_state._u0_daemon_state)))

    children = org_state.db.get_children(parent.id)
    assert len(children) == 1
    child = org_state.db.get_task(children[0])
    assert child is not None and child.assigned_agent == "dev_agent"
    assert launches == [
        (parent.id, "engineering_head"),
        (child.id, "dev_agent"),
        (parent.id, "engineering_head"),
    ]
    assert child.status is TaskStatus.COMPLETED


def test_contained_queue_dispatch_callback_readback_and_parent_resume(tmp_path, monkeypatch) -> None:
    """The positive control keeps every production seam through DB readback."""
    from runtime.daemon.dispatcher import Dispatcher
    from runtime.daemon.routes.tasks import CompletionBody, submit_completion
    from runtime.daemon.state import DaemonState
    from runtime.models import NextStep, TaskRecord, TaskStatus
    from runtime.orchestrator.host_supervisor import HostSessionSupervisor, canary_policy
    from tests.daemon.test_task_producer_containment import (
        _FakeBackend,
        _RecordingExecutor,
        _make_orch,
    )

    backend = _FakeBackend()
    executor = _RecordingExecutor()
    orch, _supervisor, tracker, db = _make_orch(tmp_path, backend, executor, monkeypatch)

    class EventSink:
        async def publish(self, task_id, event) -> None:
            return None

    org = SimpleNamespace(
        db=db, sessions=tracker, db_lock=asyncio.Lock(), event_bus=EventSink(),
    )

    class CallbackExecutor(_RecordingExecutor):
        def set_invocation_context(self, **kwargs):
            self.context = kwargs

        def run(self, **kwargs):
            task_id = self.context["task_id"]
            prior_runs = getattr(self, "runs", {}).get(task_id, 0)
            self.runs = {**getattr(self, "runs", {}), task_id: prior_runs + 1}
            decision = (
                NextStep(action="delegate", agent="dev_agent", prompt="complete child")
                if task_id == "TASK-U0-PARENT" and prior_runs == 0
                else NextStep(action="done", summary="child completed")
            )
            body = CompletionBody(
                session_id=kwargs["session_id"], agent=self.context["agent"],
                status="completed", confidence=100, output_summary="contained callback",
                decision=decision.model_dump(),
            )
            assert asyncio.run(submit_completion(task_id, body, org)) == {"ok": True}
            return super().run(**kwargs)

    callback_executor = CallbackExecutor()
    monkeypatch.setattr(orch, "_build_executor", lambda _provider: callback_executor)
    orch.attach_host_supervisor(HostSessionSupervisor(
        backend=backend, policy=canary_policy(sample_interval_seconds=0.0),
        publisher=lambda receipt: None,
    ))
    parent = TaskRecord(
        id="TASK-U0-PARENT", team="engineering", brief="delegate once",
        assigned_agent="engineering_head", task_type="task",
    )
    db.insert_task(parent)
    state = DaemonState.idle(orch._settings)
    state.orgs["test"] = SimpleNamespace(orchestrator=orch)
    orch.attach_queue(state.queue)
    state.queue.enqueue("test", parent.id)
    asyncio.run(state.queue.drain_sync(Dispatcher(state)))

    children = db.get_children(parent.id)
    assert len(children) == 1
    child = db.get_task(children[0])
    assert child is not None and child.status is TaskStatus.COMPLETED
    assert len(db.get_task_results(parent.id)) == 2
    assert len(db.get_task_results(child.id)) == 1
    assert db.get_task(parent.id).status is TaskStatus.COMPLETED
    assert backend.calls["launch"] == 3
    assert backend.calls["finish"] == 3


def test_r1_termination_before_validate_denies_real_contained_delegation(tmp_path, monkeypatch) -> None:
    """A supported terminate before validation removes the delegation target.

    The executor and host backend are faked, but the decision, route, archive,
    team mutation, validator, queue drain, callback persistence and parent
    result handling are the production seams.  This deliberately has no child
    launch claim: the target is withdrawn before admission.
    """
    from runtime.daemon.dispatcher import Dispatcher
    from runtime.daemon.routes.agents import ManageAgentBody, manage_agent
    from runtime.daemon.routes.tasks import CompletionBody, submit_completion
    from runtime.daemon.state import DaemonState
    from runtime.models import NextStep, TaskRecord, TaskStatus
    from runtime.orchestrator import run_step as run_step_mod
    from runtime.orchestrator.host_supervisor import HostSessionSupervisor, canary_policy
    from tests.daemon.test_task_producer_containment import _FakeBackend, _RecordingExecutor, _make_orch

    backend = _FakeBackend()
    orch, _supervisor, tracker, db = _make_orch(tmp_path, backend, _RecordingExecutor(), monkeypatch)
    paths = orch._paths

    class EventSink:
        async def publish(self, task_id, event) -> None:
            return None

    # This is the route's complete OrgState field surface, all sharing the
    # exact paths, Database, TeamsRegistry, tracker and orchestrator harness.
    org = SimpleNamespace(root=paths.root, slug="test", db=db, teams=orch._teams,
                          sessions=tracker, settings=orch._settings,
                          teams_lock=asyncio.Lock(), db_lock=asyncio.Lock(),
                          event_bus=EventSink())
    # A separate active manager session owns the authority writer; it cannot
    # be mistaken for the parent invocation whose decision is being consumed.
    db.insert_task(TaskRecord(id="TASK-U0-AUTH", team="engineering", brief="authority writer",
                              assigned_agent="engineering_head", task_type="task"))
    tracker.set_active("TASK-U0-AUTH", "engineering_head", "sess-authority")
    terminated: dict[str, object] = {}
    original_validate = run_step_mod._validate_delegate

    def terminate_at_boundary(current_orch, decision):
        if not terminated:
            terminated["before"] = {
                "team": org.teams.team_for_agent("dev_agent"),
                "active": (paths.agents_dir / "dev_agent.md").exists(),
            }
            body = ManageAgentBody(action="terminate", name="dev_agent",
                                    task_id="TASK-U0-AUTH", session_id="sess-authority")
            terminated["result"] = asyncio.run(manage_agent("test", body, org))
            terminated["boundary"] = _r1_snapshot(
                db=db, tracker=tracker, paths=paths, queue=state.queue,
                task_ids=("TASK-U0-PARENT",), agent_names=("dev_agent",),
            )
        result = original_validate(current_orch, decision)
        terminated["validator_result"] = result
        return result

    monkeypatch.setattr(run_step_mod, "_validate_delegate", terminate_at_boundary)

    class CallbackExecutor(_RecordingExecutor):
        def set_invocation_context(self, **kwargs):
            self.context = kwargs
        def run(self, **kwargs):
            task_id = self.context["task_id"]
            self.callback_sessions = getattr(self, "callback_sessions", []) + [
                (task_id, self.context["agent"], kwargs["session_id"])
            ]
            body = CompletionBody(session_id=kwargs["session_id"], agent=self.context["agent"],
                status="completed", confidence=100, output_summary="delegate after withdrawal",
                decision=NextStep(action="delegate", agent="dev_agent", prompt="must deny").model_dump())
            assert asyncio.run(submit_completion(task_id, body, org)) == {"ok": True}
            return super().run(**kwargs)

    executor = CallbackExecutor()
    monkeypatch.setattr(orch, "_build_executor", lambda _provider: executor)
    receipts = []
    orch.attach_host_supervisor(HostSessionSupervisor(backend=backend, policy=canary_policy(sample_interval_seconds=0.0), publisher=receipts.append))
    db.insert_task(TaskRecord(id="TASK-U0-PARENT", team="engineering", brief="withdraw", assigned_agent="engineering_head", task_type="task"))
    state = DaemonState.idle(orch._settings)
    state.orgs["test"] = SimpleNamespace(orchestrator=orch)
    orch.attach_queue(state.queue)
    before = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue,
                          task_ids=("TASK-U0-PARENT",), agent_names=("dev_agent",))
    state.queue.enqueue("test", "TASK-U0-PARENT")
    asyncio.run(state.queue.drain_sync(Dispatcher(state)))
    after = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue,
                         task_ids=("TASK-U0-PARENT",), agent_names=("dev_agent",))

    assert terminated["result"] == {"ok": True, "status": "terminated"}
    assert not (paths.agents_dir / "dev_agent.md").exists()
    assert (paths.agents_dir / "_terminated" / "dev_agent.md").exists()
    assert not (paths.workspaces_dir / "dev_agent").exists()
    assert (paths.workspaces_dir / "_terminated" / "dev_agent").exists()
    assert org.teams.team_for_agent("dev_agent") is None
    assert db.get_children("TASK-U0-PARENT") == []
    assert db.get_task("TASK-U0-PARENT").status is TaskStatus.FAILED
    assert backend.calls["launch"] == 1
    assert before["tasks"]["TASK-U0-PARENT"]["status"] == TaskStatus.PENDING.value
    assert terminated["boundary"]["tasks"]["TASK-U0-PARENT"]["status"] == TaskStatus.IN_PROGRESS.value
    assert after["queue"] == []
    assert terminated["boundary"]["attachments"]["TASK-U0-PARENT"] == []
    assert terminated["boundary"]["proposed_workflow_relations"] == "NOT PRESENT IN SHIPPING SCHEMA"
    parent_results = after["results"]["TASK-U0-PARENT"]
    assert len(parent_results) == 1
    assert parent_results[0]["task_id"] == "TASK-U0-PARENT"
    assert parent_results[0]["agent"] == "engineering_head"
    assert parent_results[0]["session_id"] != "sess-authority"
    assert executor.callback_sessions == [
        ("TASK-U0-PARENT", "engineering_head", parent_results[0]["session_id"]),
    ]
    assert parent_results[0]["output_summary"] == "delegate after withdrawal"
    assert after["tasks"]["TASK-U0-PARENT"]["note"] == (
        "invalid delegate: no workspace for agent 'dev_agent'"
    )
    assert terminated["validator_result"] == "no workspace for agent 'dev_agent'"
    assert terminated["boundary"]["canonical_agents"]["engineering_head"]
    assert terminated["boundary"]["archived_agents"]["dev_agent"]
    assert terminated["boundary"]["archived_workspaces"]["dev_agent"]
    assert any(row["action"] == "agent_managed" for row in db.get_audit_logs("TASK-U0-AUTH"))
    assert len(receipts) == 1
    assert [request.logical_id for request in backend.requests] == ["TASK-U0-PARENT"]


def test_r1_termination_after_try_delegate_refuses_quiescence(tmp_path, monkeypatch) -> None:
    """B: pause the real committed admission before its real queue notification.

    The authority writer runs in its own live event-loop thread while the
    queue worker is stopped at the observed post-commit boundary.  Thus the
    409 is the shipping quiescence decision for the admitted child, rather
    than a hand-built database precondition.
    """
    import threading

    from runtime.daemon.dispatcher import Dispatcher
    from runtime.daemon.routes.agents import ManageAgentBody, manage_agent
    from runtime.daemon.routes.tasks import CompletionBody, submit_completion
    from runtime.daemon.state import DaemonState
    from runtime.models import NextStep, TaskRecord, TaskStatus
    from runtime.orchestrator.host_supervisor import HostSessionSupervisor, canary_policy
    from tests.daemon.test_task_producer_containment import _FakeBackend, _RecordingExecutor, _make_orch

    backend = _FakeBackend()
    orch, _supervisor, tracker, db = _make_orch(tmp_path, backend, _RecordingExecutor(), monkeypatch)
    paths = orch._paths

    class EventSink:
        async def publish(self, task_id, event) -> None:
            return None

    org = SimpleNamespace(root=paths.root, slug="test", db=db, teams=orch._teams,
                          sessions=tracker, settings=orch._settings,
                          teams_lock=asyncio.Lock(), db_lock=asyncio.Lock(),
                          event_bus=EventSink())
    db.insert_task(TaskRecord(id="TASK-U0-AUTH", team="engineering", brief="authority writer",
                              assigned_agent="engineering_head", task_type="task"))
    tracker.set_active("TASK-U0-AUTH", "engineering_head", "sess-authority")
    reached, release, writer_done = threading.Event(), threading.Event(), threading.Event()
    observed: dict[str, object] = {}
    writer_errors: list[BaseException] = []
    real_try_delegate = db.try_delegate

    def authority_writer() -> None:
        async def terminate() -> None:
            body = ManageAgentBody(action="terminate", name="dev_agent",
                                   task_id="TASK-U0-AUTH", session_id="sess-authority")
            with pytest.raises(HTTPException) as raised:
                await manage_agent("test", body, org)
            observed["termination"] = raised.value.detail
            assert raised.value.status_code == 409
            assert raised.value.detail["code"] == "agent_not_quiescent"
        try:
            asyncio.run(terminate())
        except BaseException as exc:
            writer_errors.append(exc)
        finally:
            writer_done.set()
            release.set()

    def pause_after_real_commit(parent_id, child, **kwargs):
        committed = real_try_delegate(parent_id, child, **kwargs)
        if committed and parent_id == "TASK-U0-PARENT":
            observed["boundary_child"] = child.id
            observed["boundary_parent"] = db.get_task(parent_id)
            observed["boundary_child_row"] = db.get_task(child.id)
            # Independent SQLite readback proves the real try_delegate commit
            # is visible before queue notification, not merely in db's cache.
            import sqlite3
            with sqlite3.connect(paths.db_path) as reader:
                row = reader.execute(
                    "SELECT parent_task_id, assigned_agent, status FROM tasks WHERE id = ?",
                    (child.id,),
                ).fetchone()
            assert row == (parent_id, "dev_agent", TaskStatus.PENDING.value)
            observed["independent_readback"] = row
            reached.set()
            thread = threading.Thread(target=authority_writer, daemon=True)
            thread.start()
            assert writer_done.wait(2), "termination writer did not reach post-commit boundary"
            assert not writer_errors, writer_errors
            assert release.wait(1), "post-commit boundary was not released"
            thread.join(timeout=1)
            assert not thread.is_alive(), "termination writer did not finish"
            if writer_errors:
                raise writer_errors[0]
            # This is after the real writer returns its 409 but still before
            # try_delegate returns to queue notification/launch.
            observed["boundary"] = _r1_snapshot(
                db=db, tracker=tracker, paths=paths, queue=state.queue,
                task_ids=(parent_id, child.id),
            )
        return committed

    monkeypatch.setattr(db, "try_delegate", pause_after_real_commit)

    class CallbackExecutor(_RecordingExecutor):
        def set_invocation_context(self, **kwargs):
            self.context = kwargs
        def run(self, **kwargs):
            task_id = self.context["task_id"]
            self.callback_sessions = getattr(self, "callback_sessions", []) + [
                (task_id, self.context["agent"], kwargs["session_id"])
            ]
            prior = getattr(self, "runs", {}).get(task_id, 0)
            self.runs = {**getattr(self, "runs", {}), task_id: prior + 1}
            decision = (NextStep(action="delegate", agent="dev_agent", prompt="admitted child")
                        if task_id == "TASK-U0-PARENT" and prior == 0
                        else NextStep(action="done", summary="contained child completed"))
            body = CompletionBody(session_id=kwargs["session_id"], agent=self.context["agent"],
                status="completed", confidence=100, output_summary="contained callback",
                decision=decision.model_dump())
            assert asyncio.run(submit_completion(task_id, body, org)) == {"ok": True}
            return super().run(**kwargs)

    executor = CallbackExecutor()
    monkeypatch.setattr(orch, "_build_executor", lambda _provider: executor)
    receipts = []
    orch.attach_host_supervisor(HostSessionSupervisor(
        backend=backend, policy=canary_policy(sample_interval_seconds=0.0), publisher=receipts.append,
    ))
    db.insert_task(TaskRecord(id="TASK-U0-PARENT", team="engineering", brief="parent",
                              assigned_agent="engineering_head", task_type="task"))
    state = DaemonState.idle(orch._settings)
    state.orgs["test"] = SimpleNamespace(orchestrator=orch)
    orch.attach_queue(state.queue)
    before = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue,
                          task_ids=("TASK-U0-PARENT",))
    state.queue.enqueue("test", "TASK-U0-PARENT")
    asyncio.run(state.queue.drain_sync(Dispatcher(state)))
    after = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue,
                         task_ids=("TASK-U0-PARENT", observed["boundary_child"]))

    assert reached.is_set()
    assert observed["boundary_child"] == db.get_children("TASK-U0-PARENT")[0]
    detail = observed["termination"]
    assert detail["name"] == "dev_agent"
    assert any(observed["boundary_child"] in str(item) for item in detail["conflicts"])
    assert (paths.agents_dir / "dev_agent.md").exists()
    assert not (paths.agents_dir / "_terminated" / "dev_agent.md").exists()
    assert org.teams.team_for_agent("dev_agent") == "engineering"
    child_id = observed["boundary_child"]
    child = db.get_task(child_id)
    assert child is not None and child.status is TaskStatus.COMPLETED
    assert db.get_task("TASK-U0-PARENT").status is TaskStatus.COMPLETED
    assert len(db.get_task_results("TASK-U0-PARENT")) == 2
    assert len(db.get_task_results(child_id)) == 1
    assert backend.calls["launch"] == backend.calls["finish"] == 3
    assert observed["independent_readback"] == ("TASK-U0-PARENT", "dev_agent", TaskStatus.PENDING.value)
    assert before["tasks"]["TASK-U0-PARENT"]["status"] == TaskStatus.PENDING.value
    boundary = observed["boundary"]
    assert boundary["tasks"][child_id]["status"] == TaskStatus.PENDING.value
    assert boundary["tasks"][child_id]["parent_task_id"] == "TASK-U0-PARENT"
    assert boundary["tasks"][child_id]["assigned_agent"] == "dev_agent"
    assert boundary["queue"] == []
    assert boundary["sessions"][child_id] is None and not boundary["controls"][child_id]
    assert boundary["active_chain"]["TASK-U0-PARENT"] == before["active_chain"]["TASK-U0-PARENT"]
    assert boundary["active_fanout"]["TASK-U0-PARENT"] == before["active_fanout"]["TASK-U0-PARENT"]
    assert boundary["attachments"]["TASK-U0-PARENT"] == before["attachments"]["TASK-U0-PARENT"]
    assert boundary["canonical_agents"]["dev_agent"]
    assert not boundary["archived_agents"].get("dev_agent", False)
    assert boundary["workspaces"]["dev_agent"]
    assert not boundary["archived_workspaces"].get("dev_agent", False)
    assert after["queue"] == []
    assert after["tasks"][child_id]["parent_task_id"] == "TASK-U0-PARENT"
    assert after["tasks"][child_id]["assigned_agent"] == "dev_agent"
    assert after["proposed_workflow_relations"] == "NOT PRESENT IN SHIPPING SCHEMA"
    assert after["attachments"][child_id] == []
    child_results = after["results"][child_id]
    assert len(child_results) == 1
    assert child_results[0]["task_id"] == child_id
    assert child_results[0]["agent"] == "dev_agent"
    assert child_results[0]["session_id"] != "sess-authority"
    parent_results = after["results"]["TASK-U0-PARENT"]
    assert executor.callback_sessions == [
        ("TASK-U0-PARENT", "engineering_head", parent_results[0]["session_id"]),
        (child_id, "dev_agent", child_results[0]["session_id"]),
        ("TASK-U0-PARENT", "engineering_head", parent_results[1]["session_id"]),
    ]
    assert after["sessions"][child_id] is None and not after["controls"][child_id]
    assert len(receipts) == 3
    assert [request.logical_id for request in backend.requests] == [
        "TASK-U0-PARENT", child_id, "TASK-U0-PARENT",
    ]
    assert not writer_errors
