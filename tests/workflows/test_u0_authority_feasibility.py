from __future__ import annotations

import asyncio
import dataclasses
import json
import sqlite3
import threading
import traceback
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
    import hashlib

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
    def tree_contents(root):
        if not root.exists():
            return {}
        return {
            str(path.relative_to(root)): bytes_evidence(path)
            for path in sorted(root.rglob("*")) if path.is_file()
        }

    def bytes_evidence(path):
        if not path.exists():
            return None
        raw = path.read_bytes()
        return {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}

    canonical_paths = {name: paths.agents_dir / f"{name}.md" for name in identities}
    archived_paths = {name: paths.agents_dir / "_terminated" / f"{name}.md" for name in identities}
    return {
        "tasks": rows,
        "attachments": {task_id: [record(row) for row in db.list_task_attachments(task_id)]
                        for task_id in task_ids},
        "audits": {task_id: db.get_audit_logs(task_id) for task_id in task_ids},
        "results": {task_id: [record(row) for row in db.get_task_results(task_id)]
                    for task_id in task_ids},
        "sessions": {task_id: tracker.get_active(task_id, agents[task_id])
                     if agents[task_id] else None for task_id in task_ids},
        # PID is diagnostic/current-generation evidence only.  The schedules
        # prove it was present at launch and absent after their owned drain;
        # they do not claim historical or real-host process proof.
        "pids": {task_id: tracker.get_pid(task_id, agents[task_id])
                 if agents[task_id] else None for task_id in task_ids},
        "controls": {task_id: tracker.get_cancel_control(task_id, agents[task_id])
                     is not None if agents[task_id] else False for task_id in task_ids},
        # Convert the private deque observation to JSON-safe values.  It is a
        # snapshot only; queue mutation remains exclusively the shipping API.
        "queue": [
            {"org": slug, "task_id": task_id, "metadata": metadata}
            for slug, task_id, metadata in list(queue._queue._queue)
        ],
        "canonical_agents": {name: bytes_evidence(path) for name, path in canonical_paths.items()},
        "archived_agents": {name: bytes_evidence(path) for name, path in archived_paths.items()},
        "workspaces": {name: tree_contents(paths.workspaces_dir / name) for name in identities},
        "archived_workspaces": {name: tree_contents(paths.workspaces_dir / "_terminated" / name) for name in identities},
        # The test fixture's canonical team source is evidence too.  Keeping
        # its bytes lets each schedule compare membership input rather than
        # infer it from a surviving agent file.
        "teams_bytes": bytes_evidence(paths.teams_config_path),
        "proposed_workflow_relations": "NOT PRESENT IN SHIPPING SCHEMA",
        "active_chain": {task_id: rows[task_id]["active_chain"] if rows[task_id] else None
                         for task_id in task_ids},
        "active_fanout": {task_id: rows[task_id]["active_fanout"] if rows[task_id] else None
                          for task_id in task_ids},
    }


def _receipt_evidence(receipts) -> list[dict[str, object]]:
    """Serialize the receipt fields that actually exist at this seam.

    A ``Receipt`` deliberately has bounded invocation/profile attribution but
    no logical-task, agent, session, or retry-attempt field.  The contained
    fake backend used here emits its honest empty attribution, so callers must
    join those identities through the request, executor callback, and durable
    task-result surfaces instead of manufacturing a receipt identity.
    """
    return [
        {
            "invocation_kind": receipt.invocation_kind,
            "executor_profile": receipt.executor_profile,
            "terminal_reason": receipt.terminal_reason,
            "cleanup_status": receipt.cleanup_status.value,
            "quiescent": receipt.quiescent,
            "survivors": len(receipt.survivors),
        }
        for receipt in receipts
    ]


def test_r1_cancelled_pending_subtree_is_durable_before_queue_drain(tmp_path, monkeypatch) -> None:
    """Cancel an actually delegated child after original enqueue, before launch."""
    import sqlite3
    import threading
    from runtime.daemon.dispatcher import Dispatcher
    from runtime.daemon.routes.tasks import CancelBody, CompletionBody, cancel_task, submit_completion
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

    org = SimpleNamespace(root=paths.root, slug="test", db=db, teams=orch._teams, sessions=tracker,
                          settings=orch._settings, teams_lock=asyncio.Lock(), db_lock=asyncio.Lock(),
                          event_bus=EventSink(), orchestrator=orch)
    reached, release = threading.Event(), threading.Event()
    errors: list[BaseException] = []
    observed: dict[str, object] = {}

    class CallbackExecutor(_RecordingExecutor):
        def set_invocation_context(self, **kwargs):
            self.context = kwargs

        def run(self, **kwargs):
            body = CompletionBody(session_id=kwargs["session_id"], agent=self.context["agent"],
                status="completed", confidence=100, output_summary="delegate",
                decision=NextStep(action="delegate", agent="dev_agent", prompt="child").model_dump())
            assert asyncio.run(submit_completion(self.context["task_id"], body, org)) == {"ok": True}
            return super().run(**kwargs)

    executor = CallbackExecutor()
    monkeypatch.setattr(orch, "_build_executor", lambda _provider: executor)
    orch.attach_host_supervisor(HostSessionSupervisor(
        backend=backend, policy=canary_policy(sample_interval_seconds=0.0), publisher=lambda _receipt: None,
    ))
    parent = TaskRecord(id="TASK-U0-CANCEL-PARENT", team="engineering", brief="parent", assigned_agent="engineering_head", task_type="task")
    db.insert_task(parent)
    state = DaemonState.idle(orch._settings); state.orgs["test"] = SimpleNamespace(orchestrator=orch); orch.attach_queue(state.queue)
    original_enqueue = state.queue.enqueue

    def hold_child_after_original_enqueue(slug, task_id, *, metadata=None):
        original_enqueue(slug, task_id, metadata=metadata)
        child = db.get_task(task_id)
        if child is None or child.parent_task_id != parent.id:
            return
        with sqlite3.connect(paths.db_path) as reader:
            observed["durable_child"] = reader.execute("SELECT id, parent_task_id, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
        observed["child_id"] = task_id; reached.set()
        assert release.wait(2), "pending child boundary was not released"

    monkeypatch.setattr(state.queue, "enqueue", hold_child_after_original_enqueue)
    state.queue.enqueue("test", parent.id)

    def drain() -> None:
        try:
            asyncio.run(state.queue.drain_sync(Dispatcher(state)))
        except BaseException as exc:
            errors.append(exc)

    drain_thread = threading.Thread(target=drain, daemon=True)
    before = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent.id,), agent_names=("dev_agent",))
    drain_thread.start()
    try:
        assert reached.wait(2), "real delegation never reached child enqueue"
        child_id = observed["child_id"]
        assert observed["durable_child"] == (child_id, parent.id, TaskStatus.PENDING.value)
        result = asyncio.run(cancel_task(parent.id, CancelBody(rationale="fixture drain", cascade=True), org))
        with sqlite3.connect(paths.db_path) as reader:
            durable = reader.execute("SELECT id, status, cancelled_at, note, block_kind FROM tasks WHERE id IN (?, ?) ORDER BY id", (child_id, parent.id)).fetchall()
        cancelled = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent.id, child_id), agent_names=("dev_agent",))
        assert result["cancelled"] == [parent.id, child_id] and result["killed"] == []
        assert all(row[1] == TaskStatus.CANCELLED.value and row[2] and row[3] == "cancelled by founder: fixture drain" and row[4] is None for row in durable)
        assert backend.calls["launch"] == backend.calls["finish"] == 1
        assert cancelled["results"][child_id] == [] and cancelled["sessions"][child_id] is None and not cancelled["controls"][child_id]
        assert any(item["task_id"] == child_id for item in cancelled["queue"])
        assert len(cancelled["audits"][parent.id]) > len(before["audits"][parent.id])
        with pytest.raises(HTTPException) as repeated:
            asyncio.run(cancel_task(parent.id, CancelBody(cascade=True), org))
        assert repeated.value.status_code == 409 and repeated.value.detail["code"] == "task_already_terminal"
        unchanged = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent.id, child_id), agent_names=("dev_agent",))
        assert unchanged["audits"] == cancelled["audits"]
        assert unchanged["tasks"] == cancelled["tasks"]
    finally:
        release.set(); drain_thread.join(timeout=2)
        assert not drain_thread.is_alive(), "owned drain thread did not finish"
    assert not errors
    after = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent.id, child_id), agent_names=("dev_agent",))
    assert after["queue"] == [] and backend.calls["launch"] == backend.calls["finish"] == 1
    assert after["tasks"][parent.id]["status"] == after["tasks"][child_id]["status"] == TaskStatus.CANCELLED.value


def test_r1_cancelled_launched_child_rejects_late_callback_and_drains(tmp_path, monkeypatch) -> None:
    """A real opaque control cancels the launched child before its callback."""
    import threading
    from runtime.daemon.dispatcher import Dispatcher
    from runtime.daemon.routes.tasks import CancelBody, CompletionBody, cancel_task, submit_completion
    from runtime.daemon.state import DaemonState
    from runtime.models import NextStep, TaskRecord, TaskStatus
    from runtime.orchestrator.host_supervisor import HostSessionSupervisor, canary_policy
    from tests.daemon.test_task_producer_containment import _FakeBackend, _RecordingExecutor, _make_orch

    backend = _FakeBackend(auto_exit_after=0)
    orch, _supervisor, tracker, db = _make_orch(tmp_path, backend, _RecordingExecutor(), monkeypatch)
    paths = orch._paths

    class EventSink:
        async def publish(self, task_id, event) -> None:
            return None

    org = SimpleNamespace(root=paths.root, slug="test", db=db, teams=orch._teams, sessions=tracker,
                          settings=orch._settings, teams_lock=asyncio.Lock(), db_lock=asyncio.Lock(),
                          event_bus=EventSink(), orchestrator=orch)
    reached, release_late = threading.Event(), threading.Event()
    control_arrived, release_control = threading.Event(), threading.Event()
    errors: list[BaseException] = []
    cancel_errors: list[BaseException] = []
    observed: dict[str, object] = {}

    class CallbackExecutor(_RecordingExecutor):
        def set_invocation_context(self, **kwargs):
            self.context = kwargs

        def run(self, **kwargs):
            task_id = self.context["task_id"]
            if task_id == "TASK-U0-CANCEL-LAUNCHED-PARENT":
                body = CompletionBody(session_id=kwargs["session_id"], agent=self.context["agent"], status="completed", confidence=100, output_summary="delegate", decision=NextStep(action="delegate", agent="dev_agent", prompt="child").model_dump())
                assert asyncio.run(submit_completion(task_id, body, org)) == {"ok": True}
                return self._results[0]
            observed.update(task_id=task_id, agent=self.context["agent"], session_id=kwargs["session_id"], request_id=kwargs["running"].request_id, attempt=0)
            reached.set()
            # The opaque shipping control has already been invoked by the
            # route when this release is permitted; no synthetic control is
            # substituted for the real contained running handle.
            assert release_late.wait(2), "late callback boundary was not released"
            result = super().run(**kwargs)
            late = CompletionBody(session_id=kwargs["session_id"], agent=self.context["agent"], status="completed", confidence=100, output_summary="late", decision=NextStep(action="done", summary="late").model_dump())
            with pytest.raises(HTTPException) as rejected:
                asyncio.run(submit_completion(task_id, late, org))
            observed["late_detail"] = rejected.value.detail
            return result

    executor, receipts = CallbackExecutor(), []
    monkeypatch.setattr(orch, "_build_executor", lambda _provider: executor)
    orch.attach_host_supervisor(HostSessionSupervisor(backend=backend, policy=canary_policy(sample_interval_seconds=0.0), publisher=receipts.append))
    parent = TaskRecord(id="TASK-U0-CANCEL-LAUNCHED-PARENT", team="engineering", brief="parent", assigned_agent="engineering_head", task_type="task")
    db.insert_task(parent)
    state = DaemonState.idle(orch._settings); state.orgs["test"] = SimpleNamespace(orchestrator=orch); orch.attach_queue(state.queue)
    state.queue.enqueue("test", parent.id)
    original_controls = tracker.iter_task_cancel_controls

    def held_original_control(task_id):
        """Hold the route immediately before its original opaque control."""
        controls = original_controls(task_id)
        if task_id != observed.get("task_id"):
            return controls

        def invoke_original(control):
            control_arrived.set()
            assert release_control.wait(2), "opaque cancellation control was not released"
            control()

        return [(agent, lambda control=control: invoke_original(control)) for agent, control in controls]

    monkeypatch.setattr(tracker, "iter_task_cancel_controls", held_original_control)

    def drain() -> None:
        try:
            asyncio.run(state.queue.drain_sync(Dispatcher(state)))
        except BaseException as exc:
            errors.append(exc)

    drain_thread = threading.Thread(target=drain, daemon=True)
    cancel_thread: threading.Thread | None = None
    drain_thread.start()
    try:
        assert reached.wait(2), "child did not reach launched pre-callback boundary"
        child_id = observed["task_id"]
        arrival = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent.id, child_id), agent_names=("dev_agent",))
        assert arrival["tasks"][child_id]["status"] == TaskStatus.IN_PROGRESS.value
        assert arrival["sessions"][child_id] == observed["session_id"] and arrival["controls"][child_id] is True
        assert observed["request_id"] == child_id and observed["agent"] == "dev_agent" and observed["attempt"] == 0
        def cancel() -> None:
            try:
                observed["cancelled"] = asyncio.run(cancel_task(
                    parent.id, CancelBody(rationale="late callback", cascade=True), org,
                ))
            except BaseException as exc:
                cancel_errors.append(exc)

        cancel_thread = threading.Thread(target=cancel, daemon=True)
        cancel_thread.start()
        assert control_arrived.wait(2), "route never reached the original opaque control"
        # The route has already written the terminal rows/audits under its
        # DB lock, but our wrapper has not yet called the original control.
        # This is an independent reader observation, not a writer-serialization
        # claim across SQLite connections.
        import sqlite3
        with sqlite3.connect(paths.db_path) as reader:
            durable = reader.execute(
                "SELECT id, status, cancelled_at, note FROM tasks WHERE id IN (?, ?) ORDER BY id",
                (child_id, parent.id),
            ).fetchall()
        before_control = _r1_snapshot(
            db=db, tracker=tracker, paths=paths, queue=state.queue,
            task_ids=(parent.id, child_id), agent_names=("dev_agent",),
        )
        assert all(
            row[1] == TaskStatus.CANCELLED.value and row[2]
            and row[3] == "cancelled by founder: late callback"
            for row in durable
        )
        assert before_control["sessions"][child_id] == observed["session_id"]
        assert before_control["controls"][child_id] is True
        assert any(row["action"] == "task_cancelled" for row in before_control["audits"][child_id])
        release_control.set()
        cancel_thread.join(timeout=2)
        assert not cancel_thread.is_alive(), "owned cancellation thread did not finish"
        assert not cancel_errors
        assert observed["cancelled"]["cancelled"] == [parent.id, child_id]
        assert observed["cancelled"]["killed"] == [{"task_id": child_id, "agent": "dev_agent"}]
    finally:
        release_control.set()
        release_late.set()
        for owned_thread in (cancel_thread, drain_thread):
            if owned_thread is not None and owned_thread.ident is not None:
                owned_thread.join(timeout=2)
        alive = [thread.name for thread in (cancel_thread, drain_thread)
                 if thread is not None and thread.is_alive()]
        assert not alive, f"owned test thread(s) did not finish: {alive}"
    assert not errors
    final = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent.id, child_id), agent_names=("dev_agent",))
    assert final["tasks"][parent.id]["status"] == final["tasks"][child_id]["status"] == TaskStatus.CANCELLED.value
    assert final["results"][child_id] == [] and final["sessions"][child_id] is None and final["controls"][child_id] is False and final["queue"] == []
    assert observed["late_detail"]["code"] == "task_not_active"
    assert backend.calls["launch"] == backend.calls["finish"] == 2 and len(receipts) == 2


def test_r1_cancel_after_persisted_callback_keeps_history_and_no_control(tmp_path, monkeypatch) -> None:
    """A callback-first child retains its row, then cancellation stays terminal."""
    import threading
    from runtime.daemon.dispatcher import Dispatcher
    from runtime.daemon.routes.tasks import CancelBody, CompletionBody, cancel_task, submit_completion
    from runtime.daemon.state import DaemonState
    from runtime.models import NextStep, TaskRecord, TaskStatus
    from runtime.orchestrator.host_supervisor import HostSessionSupervisor, canary_policy
    from tests.daemon.test_task_producer_containment import _FakeBackend, _RecordingExecutor, _make_orch

    backend = _FakeBackend(auto_exit_after=0)
    orch, _supervisor, tracker, db = _make_orch(tmp_path, backend, _RecordingExecutor(), monkeypatch)
    paths = orch._paths
    class EventSink:
        async def publish(self, task_id, event) -> None: return None
    org = SimpleNamespace(root=paths.root, slug="test", db=db, teams=orch._teams, sessions=tracker, settings=orch._settings, teams_lock=asyncio.Lock(), db_lock=asyncio.Lock(), event_bus=EventSink(), orchestrator=orch)
    reached, release = threading.Event(), threading.Event()
    errors: list[BaseException] = []
    observed: dict[str, object] = {}
    class CallbackExecutor(_RecordingExecutor):
        def set_invocation_context(self, **kwargs): self.context = kwargs
        def run(self, **kwargs):
            task_id = self.context["task_id"]
            if task_id == "TASK-U0-CANCEL-CALLBACK-PARENT":
                decision = NextStep(action="delegate", agent="dev_agent", prompt="child")
            else:
                decision = NextStep(action="done", summary="child completed")
            body = CompletionBody(session_id=kwargs["session_id"], agent=self.context["agent"], status="completed", confidence=100, output_summary="callback", decision=decision.model_dump())
            assert asyncio.run(submit_completion(task_id, body, org)) == {"ok": True}
            if task_id == "TASK-U0-CANCEL-CALLBACK-PARENT":
                return self._results[0]
            if task_id != "TASK-U0-CANCEL-CALLBACK-PARENT":
                observed.update(task_id=task_id, session_id=kwargs["session_id"], agent=self.context["agent"])
                reached.set(); assert release.wait(2), "callback-first boundary was not released"
            return self._results[0]
    executor = CallbackExecutor()
    monkeypatch.setattr(orch, "_build_executor", lambda _provider: executor)
    orch.attach_host_supervisor(HostSessionSupervisor(backend=backend, policy=canary_policy(sample_interval_seconds=0.0), publisher=lambda _receipt: None))
    parent = TaskRecord(id="TASK-U0-CANCEL-CALLBACK-PARENT", team="engineering", brief="parent", assigned_agent="engineering_head", task_type="task")
    db.insert_task(parent)
    state = DaemonState.idle(orch._settings); state.orgs["test"] = SimpleNamespace(orchestrator=orch); orch.attach_queue(state.queue); state.queue.enqueue("test", parent.id)
    def drain() -> None:
        try: asyncio.run(state.queue.drain_sync(Dispatcher(state)))
        except BaseException as exc: errors.append(exc)
    drain_thread = threading.Thread(target=drain, daemon=True); drain_thread.start()
    try:
        assert reached.wait(2), "callback-first child did not persist"
        child_id = observed["task_id"]
        before_cancel = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent.id, child_id), agent_names=("dev_agent",))
        assert before_cancel["tasks"][child_id]["status"] == TaskStatus.IN_PROGRESS.value
        assert len(before_cancel["results"][child_id]) == 1 and before_cancel["sessions"][child_id] is None and before_cancel["controls"][child_id] is False
        cancelled = asyncio.run(cancel_task(parent.id, CancelBody(rationale="callback already persisted", cascade=True), org))
        assert cancelled["cancelled"] == [parent.id, child_id] and cancelled["killed"] == []
        effects = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent.id, child_id), agent_names=("dev_agent",))
        with pytest.raises(HTTPException) as repeated:
            asyncio.run(cancel_task(parent.id, CancelBody(cascade=True), org))
        assert repeated.value.status_code == 409 and repeated.value.detail["code"] == "task_already_terminal"
        unchanged = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent.id, child_id), agent_names=("dev_agent",))
        assert unchanged["audits"] == effects["audits"] and unchanged["tasks"] == effects["tasks"]
    finally:
        release.set(); drain_thread.join(timeout=2)
        assert not drain_thread.is_alive(), "owned drain thread did not finish"
    assert not errors
    final = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent.id, child_id), agent_names=("dev_agent",))
    assert final["tasks"][parent.id]["status"] == final["tasks"][child_id]["status"] == TaskStatus.CANCELLED.value
    assert len(final["results"][child_id]) == 1 and final["sessions"][child_id] is None and final["controls"][child_id] is False and final["queue"] == []


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
    assert [(request.org, request.logical_id, request.retry_attempt)
            for request in backend.requests] == [("test", "TASK-U0-PARENT", 0)]
    assert _receipt_evidence(receipts) == [{
        "invocation_kind": "", "executor_profile": "", "terminal_reason": "success",
        "cleanup_status": "clean", "quiescent": True, "survivors": 0,
    }]


def test_r1_termination_after_try_delegate_before_enqueue_refuses_quiescence(tmp_path, monkeypatch) -> None:
    """B: hold after the real committed admission, before original enqueue.

    This is intentionally distinct from Schedule C below.  The wrapper calls
    the original ``Database.try_delegate`` first, then blocks before
    ``run_step_impl`` can reach its real ``TaskQueue.put_nowait`` call.
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
    drain_errors: list[BaseException] = []
    owned_threads: list[threading.Thread] = []
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

    def pause_after_real_commit(parent_id, child, **kwargs):
        committed = real_try_delegate(parent_id, child, **kwargs)
        if not committed or parent_id != "TASK-U0-PARENT":
            return committed
        observed["boundary_child"] = child.id
        import sqlite3
        with sqlite3.connect(paths.db_path) as reader:
            row = reader.execute(
                "SELECT parent_task_id, assigned_agent, status FROM tasks WHERE id = ?",
                (child.id,),
            ).fetchone()
        assert row == (parent_id, "dev_agent", TaskStatus.PENDING.value)
        observed["independent_readback"] = row
        reached.set()
        assert release.wait(2), "post-commit pre-enqueue boundary was not released"
        return committed

    class CallbackExecutor(_RecordingExecutor):
        def set_invocation_context(self, **kwargs):
            self.context = kwargs
        def run(self, **kwargs):
            task_id = self.context["task_id"]
            invocation = (task_id, self.context["agent"], kwargs["session_id"])
            self.invocations = getattr(self, "invocations", []) + [invocation]
            self.callback_sessions = getattr(self, "callback_sessions", []) + [invocation]
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
    monkeypatch.setattr(db, "try_delegate", pause_after_real_commit)

    def drain_queue() -> None:
        try:
            asyncio.run(state.queue.drain_sync(Dispatcher(state)))
        except BaseException as exc:
            drain_errors.append(exc)

    drain_thread = threading.Thread(target=drain_queue, daemon=True)
    writer_thread = threading.Thread(target=authority_writer, daemon=True)
    owned_threads.extend((drain_thread, writer_thread))
    drain_thread.start()
    try:
        assert reached.wait(2), "child did not reach the post-commit boundary"
        writer_thread.start()
        assert writer_done.wait(2), "termination writer did not return"
        if writer_errors:
            raise writer_errors[0]
        child_id = observed["boundary_child"]
        observed["boundary"] = _r1_snapshot(
            db=db, tracker=tracker, paths=paths, queue=state.queue,
            task_ids=("TASK-U0-PARENT", child_id),
        )
        observed["backend_boundary"] = {
            "requests": [(request.org, request.invocation_kind, request.logical_id,
                          request.retry_attempt) for request in backend.requests],
            "launch": backend.calls["launch"], "finish": backend.calls["finish"],
            "receipts": [_receipt_evidence(receipts)],
        }
    finally:
        release.set()
        for thread in owned_threads:
            if thread.ident is not None:
                thread.join(timeout=2)
        alive = [thread.name for thread in owned_threads if thread.is_alive()]
        assert not alive, f"owned test thread(s) did not finish: {alive}"
    if writer_errors:
        raise writer_errors[0]
    if drain_errors:
        raise drain_errors[0]
    child_id = observed["boundary_child"]
    after = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue,
                         task_ids=("TASK-U0-PARENT", child_id))

    detail = observed["termination"]
    assert reached.is_set() and release.is_set()
    assert detail["name"] == "dev_agent"
    assert any(child_id in str(item) for item in detail["conflicts"])
    assert observed["independent_readback"] == ("TASK-U0-PARENT", "dev_agent", TaskStatus.PENDING.value)
    boundary = observed["boundary"]
    assert boundary["tasks"][child_id]["status"] == TaskStatus.PENDING.value
    assert boundary["tasks"][child_id]["parent_task_id"] == "TASK-U0-PARENT"
    assert boundary["tasks"][child_id]["assigned_agent"] == "dev_agent"
    assert boundary["queue"] == []
    assert boundary["results"][child_id] == []
    assert boundary["sessions"][child_id] is None and not boundary["controls"][child_id]
    assert observed["backend_boundary"] == {
        "requests": [("test", "task", "TASK-U0-PARENT", 0)], "launch": 1, "finish": 1,
        "receipts": [[{"invocation_kind": "", "executor_profile": "", "terminal_reason": "success",
                       "cleanup_status": "clean", "quiescent": True, "survivors": 0}]],
    }
    assert boundary["active_chain"]["TASK-U0-PARENT"] == before["active_chain"]["TASK-U0-PARENT"]
    assert boundary["active_fanout"]["TASK-U0-PARENT"] == before["active_fanout"]["TASK-U0-PARENT"]
    assert boundary["attachments"]["TASK-U0-PARENT"] == before["attachments"]["TASK-U0-PARENT"]
    assert boundary["canonical_agents"]["dev_agent"] and not boundary["archived_agents"].get("dev_agent", False)
    assert boundary["workspaces"]["dev_agent"] and not boundary["archived_workspaces"].get("dev_agent", False)
    assert after["queue"] == [] and after["proposed_workflow_relations"] == "NOT PRESENT IN SHIPPING SCHEMA"
    child_results, parent_results = after["results"][child_id], after["results"]["TASK-U0-PARENT"]
    assert len(child_results) == 1 and len(parent_results) == 2
    expected = [
        ("TASK-U0-PARENT", "engineering_head", parent_results[0]["session_id"]),
        (child_id, "dev_agent", child_results[0]["session_id"]),
        ("TASK-U0-PARENT", "engineering_head", parent_results[1]["session_id"]),
    ]
    assert executor.invocations == expected and executor.callback_sessions == expected
    assert [(request.org, request.logical_id, request.retry_attempt) for request in backend.requests] == [
        ("test", "TASK-U0-PARENT", 0), ("test", child_id, 0), ("test", "TASK-U0-PARENT", 0),
    ]
    assert after["sessions"][child_id] is None and not after["controls"][child_id]
    assert after["sessions"]["TASK-U0-PARENT"] is None and not after["controls"]["TASK-U0-PARENT"]
    assert _receipt_evidence(receipts) == [
        {"invocation_kind": "", "executor_profile": "", "terminal_reason": "success",
         "cleanup_status": "clean", "quiescent": True, "survivors": 0},
    ] * 3


def test_r1_termination_after_original_enqueue_refuses_quiescence(tmp_path, monkeypatch) -> None:
    """C: pause after original queue insertion and before dispatcher consumption.

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
    owned_threads: list[threading.Thread] = []
    drain_errors: list[BaseException] = []

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

    original_enqueue = None

    def pause_after_original_enqueue(slug, task_id, *, metadata=None):
        """Observe original queue insertion before holding the dispatcher.

        ``run_step_impl`` has already committed through ``try_delegate`` when
        it reaches ``put_nowait``.  Calling the original enqueue first makes
        the child admission visible through the actual queue API; holding this
        call prevents the current dispatcher from returning to drain the child.
        """
        assert original_enqueue is not None
        original_enqueue(slug, task_id, metadata=metadata)
        queued = db.get_task(task_id)
        if queued is None or queued.parent_task_id != "TASK-U0-PARENT":
            return
        try:
            observed["boundary_child"] = task_id
            # Independent SQLite readback proves the real try_delegate commit.
            # Original enqueue has already run; this is deliberately the
            # post-enqueue, pre-dispatch boundary (not before notification).
            import sqlite3
            with sqlite3.connect(paths.db_path) as reader:
                row = reader.execute(
                    "SELECT parent_task_id, assigned_agent, status FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
            assert row == ("TASK-U0-PARENT", "dev_agent", TaskStatus.PENDING.value)
            observed["independent_readback"] = row
            reached.set()
            assert release.wait(2), "post-writer queue boundary was not released"
        finally:
            # The outer owner always releases and joins the writer/drain
            # threads. This wrapper only retains the shipping queue boundary.
            pass

    class CallbackExecutor(_RecordingExecutor):
        def set_invocation_context(self, **kwargs):
            self.context = kwargs
        def run(self, **kwargs):
            task_id = self.context["task_id"]
            self.invocations = getattr(self, "invocations", []) + [
                (task_id, self.context["agent"], kwargs["session_id"]),
            ]
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
    original_enqueue = state.queue.enqueue
    before = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue,
                          task_ids=("TASK-U0-PARENT",))
    state.queue.enqueue("test", "TASK-U0-PARENT")
    monkeypatch.setattr(state.queue, "enqueue", pause_after_original_enqueue)

    def drain_queue() -> None:
        try:
            asyncio.run(state.queue.drain_sync(Dispatcher(state)))
        except BaseException as exc:
            drain_errors.append(exc)

    drain_thread = threading.Thread(target=drain_queue, daemon=True)
    writer_thread = threading.Thread(target=authority_writer, daemon=True)
    owned_threads.extend((drain_thread, writer_thread))
    drain_thread.start()
    try:
        assert reached.wait(2), "child did not reach the post-enqueue boundary"
        writer_thread.start()
        assert writer_done.wait(2), "termination writer did not reach post-commit boundary"
        if writer_errors:
            raise writer_errors[0]
        # The supported writer has returned its 409 while the held dispatcher
        # has not yet dequeued or dispatched the admitted child.
        child_at_boundary = observed["boundary_child"]
        observed["boundary"] = _r1_snapshot(
            db=db, tracker=tracker, paths=paths, queue=state.queue,
            task_ids=("TASK-U0-PARENT", child_at_boundary),
        )
        observed["backend_boundary"] = {
            "requests": [
                (request.org, request.invocation_kind, request.logical_id,
                 request.retry_attempt)
                for request in backend.requests
            ],
            "launch": backend.calls["launch"],
            "finish": backend.calls["finish"],
            "receipts": [
                _receipt_evidence(receipts),
            ],
        }
    finally:
        release.set()
        for owned_thread in owned_threads:
            if owned_thread.ident is not None:
                owned_thread.join(timeout=2)
        alive = [thread.name for thread in owned_threads if thread.is_alive()]
        assert not alive, f"owned test thread(s) did not finish: {alive}"
    if writer_errors:
        raise writer_errors[0]
    if drain_errors:
        raise drain_errors[0]
    after = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue,
                         task_ids=("TASK-U0-PARENT", observed["boundary_child"]))

    assert reached.is_set()
    assert release.is_set()
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
    assert boundary["queue"] == [{"org": "test", "task_id": child_id, "metadata": None}]
    assert boundary["sessions"][child_id] is None and not boundary["controls"][child_id]
    assert boundary["results"][child_id] == []
    assert len(boundary["results"]["TASK-U0-PARENT"]) == 1
    assert observed["backend_boundary"] == {
        "requests": [("test", "task", "TASK-U0-PARENT", 0)],
        "launch": 1,
        "finish": 1,
        # The fake backend returns the shipping Receipt's default empty
        # attribution; task/session identity is available only on request and
        # callback/result surfaces in this test.
        "receipts": [[{
            "invocation_kind": "", "executor_profile": "", "terminal_reason": "success",
            "cleanup_status": "clean", "quiescent": True, "survivors": 0,
        }]],
    }
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
    assert after["sessions"]["TASK-U0-PARENT"] is None
    assert not after["controls"]["TASK-U0-PARENT"]
    # Receipts do not carry task/agent/session/attempt identity.  The fake
    # backend's intentionally empty bounded attribution is asserted below;
    # each invocation's durable identity is instead joined through the real
    # request, executor context/callback, and persisted result row.
    assert [(request.org, request.logical_id, request.retry_attempt)
            for request in backend.requests] == [
        ("test", "TASK-U0-PARENT", 0), ("test", child_id, 0),
        ("test", "TASK-U0-PARENT", 0),
    ]
    expected_invocations = [
        ("TASK-U0-PARENT", "engineering_head", parent_results[0]["session_id"]),
        (child_id, "dev_agent", child_results[0]["session_id"]),
        ("TASK-U0-PARENT", "engineering_head", parent_results[1]["session_id"]),
    ]
    assert executor.invocations == expected_invocations
    assert executor.callback_sessions == expected_invocations
    assert _receipt_evidence(receipts) == [
        {"invocation_kind": "", "executor_profile": "", "terminal_reason": "success",
         "cleanup_status": "clean", "quiescent": True, "survivors": 0},
        {"invocation_kind": "", "executor_profile": "", "terminal_reason": "success",
         "cleanup_status": "clean", "quiescent": True, "survivors": 0},
        {"invocation_kind": "", "executor_profile": "", "terminal_reason": "success",
         "cleanup_status": "clean", "quiescent": True, "survivors": 0},
    ]
    assert not writer_errors


@pytest.mark.parametrize("callback_before_writer", (False, True), ids=("writer-before-callback", "callback-before-writer"))
def test_r1_termination_after_child_launch_distinguishes_callback_order(tmp_path, monkeypatch, callback_before_writer: bool) -> None:
    """Real post-launch child boundaries distinguish durable callback ordering."""
    import sqlite3
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

    org = SimpleNamespace(root=paths.root, slug="test", db=db, teams=orch._teams, sessions=tracker,
                          settings=orch._settings, teams_lock=asyncio.Lock(), db_lock=asyncio.Lock(), event_bus=EventSink())
    db.insert_task(TaskRecord(id="TASK-U0-AUTH", team="engineering", brief="writer", assigned_agent="engineering_head", task_type="task"))
    tracker.set_active("TASK-U0-AUTH", "engineering_head", "sess-authority")
    reached, writer_done, release = threading.Event(), threading.Event(), threading.Event()
    observed: dict[str, object] = {"event_order": []}
    errors: list[BaseException] = []

    def writer() -> None:
        try:
            async def terminate() -> None:
                body = ManageAgentBody(action="terminate", name="dev_agent", task_id="TASK-U0-AUTH", session_id="sess-authority")
                with pytest.raises(HTTPException) as raised:
                    await manage_agent("test", body, org)
                assert raised.value.status_code == 409
                observed["termination"] = raised.value.detail
            asyncio.run(terminate())
            observed["event_order"].append("writer_returned")
        except BaseException as exc:
            errors.append(exc)
        finally:
            writer_done.set()

    class CallbackExecutor(_RecordingExecutor):
        def set_invocation_context(self, **kwargs):
            self.context = kwargs
        def run(self, **kwargs):
            task_id = self.context["task_id"]
            prior = getattr(self, "runs", {}).get(task_id, 0)
            self.runs = {**getattr(self, "runs", {}), task_id: prior + 1}
            decision = NextStep(action="delegate", agent="dev_agent", prompt="child") if task_id == "TASK-U0-PARENT" and prior == 0 else NextStep(action="done", summary="child completed")
            body = CompletionBody(session_id=kwargs["session_id"], agent=self.context["agent"], status="completed", confidence=100, output_summary="contained callback", decision=decision.model_dump())
            if task_id != "TASK-U0-PARENT":
                observed["child_id"], observed["running"] = task_id, backend.last_running
                if callback_before_writer:
                    assert asyncio.run(submit_completion(task_id, body, org)) == {"ok": True}
                    observed["event_order"].append("callback_persisted")
                with sqlite3.connect(paths.db_path) as reader:
                    observed["arrival"] = reader.execute("SELECT id, assigned_agent, status FROM tasks WHERE id = ?", (task_id,)).fetchone()
                    observed["result_count"] = reader.execute("SELECT count(*) FROM task_results WHERE task_id = ?", (task_id,)).fetchone()[0]
                reached.set()
                assert release.wait(2), "post-launch executor boundary was not released"
                if not callback_before_writer:
                    assert asyncio.run(submit_completion(task_id, body, org)) == {"ok": True}
                    observed["event_order"].append("callback_persisted")
            else:
                assert asyncio.run(submit_completion(task_id, body, org)) == {"ok": True}
            return super().run(**kwargs)

    executor, receipts = CallbackExecutor(), []
    monkeypatch.setattr(orch, "_build_executor", lambda _provider: executor)
    orch.attach_host_supervisor(HostSessionSupervisor(backend=backend, policy=canary_policy(sample_interval_seconds=0.0), publisher=receipts.append))
    db.insert_task(TaskRecord(id="TASK-U0-PARENT", team="engineering", brief="parent", assigned_agent="engineering_head", task_type="task"))
    state = DaemonState.idle(orch._settings); state.orgs["test"] = SimpleNamespace(orchestrator=orch); orch.attach_queue(state.queue)
    state.queue.enqueue("test", "TASK-U0-PARENT")

    def drain() -> None:
        try:
            asyncio.run(state.queue.drain_sync(Dispatcher(state)))
        except BaseException as exc:
            errors.append(exc)

    drain_thread, writer_thread = threading.Thread(target=drain, daemon=True), threading.Thread(target=writer, daemon=True)
    drain_thread.start()
    try:
        assert reached.wait(2), "child did not reach post-launch boundary"
        child_id, running = observed["child_id"], observed["running"]
        assert running is not None and running.request_id == child_id
        assert observed["arrival"] == (child_id, "dev_agent", TaskStatus.IN_PROGRESS.value)
        assert observed["result_count"] == (1 if callback_before_writer else 0)
        arrival = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=("TASK-U0-PARENT", child_id), agent_names=("dev_agent",))
        assert backend.calls["launch"] == 2 and backend.calls["finish"] == 1
        assert (arrival["sessions"][child_id] is not None) == (not callback_before_writer)
        assert arrival["controls"][child_id] is (not callback_before_writer)
        assert len(arrival["results"][child_id]) == (1 if callback_before_writer else 0)
        writer_thread.start(); assert writer_done.wait(2), "writer did not return"
        if errors: raise errors[0]
        boundary = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=("TASK-U0-PARENT", child_id), agent_names=("dev_agent",))
        detail = observed["termination"]
        assert detail["code"] == "agent_not_quiescent" and detail["name"] == "dev_agent"
        assert {item["id"] for item in detail["conflicts"] if item["kind"] == "task"} == {child_id}
        assert boundary["tasks"][child_id]["status"] == TaskStatus.IN_PROGRESS.value
        assert (boundary["sessions"][child_id] is not None) == (not callback_before_writer)
        assert boundary["controls"][child_id] is (not callback_before_writer)
        assert boundary["canonical_agents"]["dev_agent"] and boundary["workspaces"]["dev_agent"]
        assert boundary["attachments"][child_id] == [] and boundary["proposed_workflow_relations"] == "NOT PRESENT IN SHIPPING SCHEMA"
        assert len(boundary["results"][child_id]) == (1 if callback_before_writer else 0)
        observed["event_order"].append("boundary_observed")
    finally:
        release.set()
        started = (writer_thread, drain_thread)
        for thread in started:
            if thread.ident is not None:
                thread.join(timeout=2)
        alive = [thread.name for thread in started if thread.is_alive()]
        assert not alive, f"owned test thread(s) did not finish: {alive}"
    if errors: raise errors[0]
    after = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=("TASK-U0-PARENT", child_id), agent_names=("dev_agent",))
    assert db.get_task(child_id).status is TaskStatus.COMPLETED and db.get_task("TASK-U0-PARENT").status is TaskStatus.COMPLETED
    assert len(after["results"][child_id]) == 1 and len(after["results"]["TASK-U0-PARENT"]) == 2
    assert after["sessions"][child_id] is None and not after["controls"][child_id]
    assert after["sessions"]["TASK-U0-PARENT"] is None and not after["controls"]["TASK-U0-PARENT"] and after["queue"] == []
    assert [(request.org, request.logical_id, request.retry_attempt) for request in backend.requests] == [("test", "TASK-U0-PARENT", 0), ("test", child_id, 0), ("test", "TASK-U0-PARENT", 0)]
    assert backend.calls["launch"] == backend.calls["finish"] == 3 and len(receipts) == 3
    assert observed["event_order"] == (["writer_returned", "boundary_observed", "callback_persisted"] if not callback_before_writer else ["callback_persisted", "writer_returned", "boundary_observed"])


@pytest.mark.parametrize("first_verdict", ("PASS", None, "REVISE"), ids=("pass", "none", "revise"))
def test_r1_real_delegate_then_chain_publication_and_fail_closed_wake(
    tmp_path, monkeypatch, first_verdict: str | None,
) -> None:
    """Exercise real completion/consumption/chain/queue seams for all verdict gates."""
    from runtime.daemon.dispatcher import Dispatcher
    from runtime.daemon.routes.tasks import CompletionBody, submit_completion
    from runtime.daemon.state import DaemonState
    from runtime.models import ChainLeg, NextStep, TaskRecord, TaskStatus
    from runtime.orchestrator.host_supervisor import HostSessionSupervisor, canary_policy
    from runtime.orchestrator import chain
    from runtime.orchestrator import run_step as run_step_module
    from tests.daemon.test_task_producer_containment import _FakeBackend, _RecordingExecutor, _make_orch

    parent_id = "TASK-U0-CHAIN"
    backend = _FakeBackend(auto_exit_after=0)
    orch, _, tracker, db = _make_orch(tmp_path, backend, _RecordingExecutor(), monkeypatch)
    paths, receipts, launches, errors, publications, consumptions = orch._paths, [], [], [], [], []
    reached, release, done = threading.Event(), threading.Event(), threading.Event()
    compute_outcomes: list[object] = []

    class EventSink:
        async def publish(self, task_id, event):
            return None

    org = SimpleNamespace(db=db, sessions=tracker, db_lock=asyncio.Lock(), event_bus=EventSink())
    original_compute = chain.compute_advance_action
    def observed_compute(*args, **kwargs):
        outcome = original_compute(*args, **kwargs)
        compute_outcomes.append(outcome)
        return outcome
    monkeypatch.setattr(chain, "compute_advance_action", observed_compute)
    original_consume = run_step_module._consume_completion_report
    def observed_consume(consume_orch, task_id, report, result_row_id=None):
        """Observe the original consumption call without changing its result."""
        consumptions.append({
            "task_id": task_id,
            "agent": report.agent,
            "result_row_id": result_row_id,
            "current_session_id": db.get_task(task_id).current_session_id,
        })
        return original_consume(consume_orch, task_id, report, result_row_id=result_row_id)
    monkeypatch.setattr(run_step_module, "_consume_completion_report", observed_consume)

    class Executor(_RecordingExecutor):
        def set_invocation_context(self, **kwargs) -> None:
            assert not hasattr(self, "context"), "executor was reused across invocations"
            self.context = kwargs.copy()

        def run(self, **kwargs):
            try:
                context = self.context.copy()
                task_id, agent, session_id = context["task_id"], context["agent"], kwargs["session_id"]
                assert kwargs["running"].request_id == task_id
                assert tracker.get_active(task_id, agent) == session_id
                assert db.get_task(task_id).current_session_id == session_id
                launches.append((id(self), id(self.context), task_id, agent, session_id, kwargs["running"].request_id))
                children = db.get_children(parent_id)
                if task_id == parent_id and not children:
                    decision = NextStep(action="delegate", agent="dev_agent", prompt="first", expect_verdict="PASS", then=[ChainLeg(agent="dev_agent", prompt="second")])
                    verdict = None
                elif task_id == parent_id:
                    decision, verdict = NextStep(action="done", summary="parent revisit"), None
                else:
                    decision = NextStep(action="done", summary="child")
                    verdict = first_verdict if len(children) == 1 else "PASS"
                body = CompletionBody(session_id=session_id, agent=agent, status="completed", confidence=100, verdict=verdict, output_summary="chain callback", decision=decision.model_dump())
                assert asyncio.run(submit_completion(task_id, body, org)) == {"ok": True}
                kwargs["running"].process.terminate()
                return dataclasses.replace(super().run(**kwargs), session_id=session_id)
            except BaseException as exc:
                errors.append((exc, traceback.format_exc()))
                raise
            finally:
                kwargs["running"].process.terminate()

    executors = []
    def factory(_provider):
        executor = Executor()
        executors.append(executor)
        return executor
    monkeypatch.setattr(orch, "_build_executor", factory)
    supervisor = HostSessionSupervisor(backend=backend, policy=canary_policy(sample_interval_seconds=0), publisher=receipts.append)
    orch.attach_host_supervisor(supervisor)
    state = DaemonState.idle(orch._settings)
    state.orgs["test"] = SimpleNamespace(orchestrator=orch)
    orch.attach_queue(state.queue)
    original_put = state.queue.put_nowait
    def held_put(slug, task_id, *args, **kwargs):
        children = db.get_children(parent_id)
        expected_publication = task_id != parent_id and len(children) == 2 if first_verdict == "PASS" else task_id == parent_id and len(children) == 1
        if expected_publication:
            reached.set()
            assert release.wait(5), "real queue publication was not released"
        original_put(slug, task_id, *args, **kwargs)
        publications.append(task_id)
    monkeypatch.setattr(state.queue, "put_nowait", held_put)

    parent = TaskRecord(id=parent_id, team="engineering", brief="chain", assigned_agent="engineering_head", task_type="task")
    db.insert_task(parent)
    state.queue.enqueue("test", parent_id)
    dispatcher = Dispatcher(state)
    original_dispatch = dispatcher.run_step
    def dispatch(*args, **kwargs):
        try:
            return original_dispatch(*args, **kwargs)
        except BaseException as exc:
            errors.append((exc, traceback.format_exc()))
            raise
    monkeypatch.setattr(dispatcher, "run_step", dispatch)
    def worker() -> None:
        try:
            asyncio.run(state.queue.drain_sync(dispatcher))
        except BaseException as exc:
            errors.append((exc, traceback.format_exc()))
        finally:
            done.set()
    worker_thread = threading.Thread(target=worker, name="u0-chain-drain", daemon=False)
    worker_thread.start()
    assertion = None
    try:
        assert reached.wait(3), "real chain/wake queue boundary was not reached"
        children = db.get_children(parent_id)
        first = children[0]
        with sqlite3.connect(f"file:{paths.db_path}?mode=ro", uri=True, timeout=1) as connection:
            connection.row_factory = sqlite3.Row
            tasks = {row["id"]: dict(row) for row in connection.execute("SELECT * FROM tasks")}
            results = [dict(row) for row in connection.execute("SELECT * FROM task_results")]
            audits = [dict(row) for row in connection.execute("SELECT * FROM audit_log")]
            attachments = [dict(row) for row in connection.execute("SELECT * FROM task_attachments")]
        first_results = [row for row in results if row["task_id"] == first]
        assert len(first_results) == 1
        assert (first_results[0]["task_id"], first_results[0]["agent"], first_results[0]["session_id"], first_results[0]["verdict"]) == (first, "dev_agent", launches[1][4], first_verdict)
        assert tasks[first]["current_session_id"] == first_results[0]["session_id"]
        assert tasks[parent_id]["status"] == TaskStatus.IN_PROGRESS.value and tasks[parent_id]["block_kind"] == "delegated"
        assert tasks[parent_id]["orchestration_step_count"] == 1
        assert not state.queue._queue.qsize() and [entry[2] for entry in launches] == [parent_id, first]
        advance_audits = [row for row in audits if row["action"] == "chain_auto_advance"]
        if first_verdict == "PASS":
            assert len(children) == 2
            second = children[1]
            chain = json.loads(tasks[parent_id]["active_chain"])
            assert tasks[second]["status"] == TaskStatus.PENDING.value and chain["step_index"] == 1
            assert len(advance_audits) == 1
            payload = json.loads(advance_audits[0]["payload"])
            assert advance_audits[0]["task_id"] == parent_id
            assert payload == {"leg_index": 1, "spawned_child_id": second, "triggering_child_id": first, "triggering_verdict": "PASS", "chain_origin_step_audit_id": chain["step_audit_id"]}
            assert not attachments
        else:
            assert children == [first] and tasks[parent_id]["active_chain"] is None
            assert not advance_audits and not attachments
            assert compute_outcomes[-1].kind == "wake" and compute_outcomes[-1].reason == "verdict_mismatch"
            assert compute_outcomes[-1].actual == first_verdict
    except BaseException as exc:
        assertion = exc
    finally:
        release.set()
        worker_thread.join(8)
    assert not worker_thread.is_alive() and done.is_set(), "owned worker did not join"
    if errors:
        raise BaseExceptionGroup("worker failures (including queue-caught dispatcher errors)", [error for error, _trace in errors])
    if assertion:
        raise assertion
    children = db.get_children(parent_id)
    expected_launches = [parent_id, *children, parent_id]
    final = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent_id, *children), agent_names=("dev_agent", "engineering_head"))
    assert [entry[2] for entry in launches] == expected_launches
    assert len({entry[0] for entry in launches}) == len(expected_launches)
    assert len({entry[1] for entry in launches}) == len(expected_launches)
    assert len({entry[4] for entry in launches}) == len(expected_launches)
    assert [(request.org, request.logical_id, request.retry_attempt) for request in backend.requests] == [
        ("test", task_id, 0) for task_id in expected_launches
    ]
    assert [request.logical_id for request in backend.requests] == [entry[5] for entry in launches]
    assert publications == [*children, parent_id]
    assert backend.calls["launch"] == backend.calls["finish"] == len(receipts) == len(expected_launches)
    assert supervisor._admission.admitted_total() == supervisor._admission.released_total() == len(expected_launches)
    assert all(final["tasks"][task_id]["status"] == TaskStatus.COMPLETED.value for task_id in final["tasks"])
    assert db.get_task(parent_id).active_chain is None and db.get_task(parent_id).orchestration_step_count == 2
    expected_child_count = 2 if first_verdict == "PASS" else 1
    assert len(children) == expected_child_count
    child_step_counts = {child: db.get_task(child).orchestration_step_count for child in children}
    assert child_step_counts == {child: 1 for child in children}
    assert len(final["results"][parent_id]) == 2 and all(len(final["results"][child]) == 1 for child in children)
    result_rows = [row for task_rows in final["results"].values() for row in task_rows]
    assert len(consumptions) == len(expected_launches) == len(result_rows)
    observed_by_result_id = {observation["result_row_id"]: observation for observation in consumptions}
    assert None not in observed_by_result_id and len(observed_by_result_id) == len(consumptions)
    launch_identities = {(task_id, agent, session_id) for _, _, task_id, agent, session_id, _ in launches}
    for row in result_rows:
        observation = observed_by_result_id[row["id"]]
        identity = (row["task_id"], row["agent"], row["session_id"])
        assert identity in launch_identities
        assert (observation["task_id"], observation["agent"], observation["current_session_id"]) == identity
    receipt_rows = _receipt_evidence(receipts)
    assert all(row["cleanup_status"] == "clean" and row["quiescent"] and row["survivors"] == 0 for row in receipt_rows)
    assert final["queue"] == [] and state.queue._queue._unfinished_tasks == 0
    assert all(session is None for session in final["sessions"].values()) and not any(final["controls"].values())
    launched_bindings = {(task_id, agent) for _, _, task_id, agent, _, _ in launches}
    live_pids = {(task_id, agent): tracker.get_pid(task_id, agent) for task_id, agent in launched_bindings}
    assert all(pid is None for pid in live_pids.values())


def test_r1_chain_harness_propagates_dispatcher_error(tmp_path, monkeypatch) -> None:
    """The local collector refuses a false green if drain_sync logs and swallows."""
    from runtime.daemon.dispatcher import Dispatcher
    original = Dispatcher.run_step
    calls = 0
    def injected(self, *args, **kwargs):
        nonlocal calls
        result = original(self, *args, **kwargs)
        calls += 1
        if calls == 1:
            raise RuntimeError("U0_DISPATCH_ERROR_CONTROL")
        return result
    monkeypatch.setattr(Dispatcher, "run_step", injected)
    with pytest.raises(BaseExceptionGroup, match="worker failures") as raised:
        test_r1_real_delegate_then_chain_publication_and_fail_closed_wake(tmp_path, monkeypatch, "PASS")
    assert any("U0_DISPATCH_ERROR_CONTROL" in str(error) for error in raised.value.exceptions)


def test_r1_chain_cancel_before_first_callback_rejects_late_result(tmp_path, monkeypatch) -> None:
    """A real parent cascade wins before the first chain callback is submitted.

    The executor is the only fake boundary.  In particular, cancellation uses
    the route's durable subtree walk and opaque control, and the delayed
    callback still enters the real ``submit_completion`` route.
    """
    from runtime.daemon.dispatcher import Dispatcher
    from runtime.daemon.routes.tasks import CancelBody, CompletionBody, cancel_task, submit_completion
    from runtime.daemon.state import DaemonState
    from runtime.models import ChainLeg, NextStep, TaskRecord, TaskStatus
    from runtime.orchestrator.host_supervisor import HostSessionSupervisor, canary_policy
    from tests.daemon.test_task_producer_containment import _FakeBackend, _RecordingExecutor, _make_orch

    parent_id = "TASK-U0-CHAIN-CANCEL-CALLBACK"
    backend = _FakeBackend(auto_exit_after=0)
    orch, _, tracker, db = _make_orch(tmp_path, backend, _RecordingExecutor(), monkeypatch)
    paths, receipts, launches, errors, late_statuses, control_entries = orch._paths, [], [], [], [], []
    child_held, release_child, done = threading.Event(), threading.Event(), threading.Event()

    class EventSink:
        async def publish(self, task_id, event):
            return None

    org = SimpleNamespace(db=db, sessions=tracker, db_lock=asyncio.Lock(), event_bus=EventSink(), orchestrator=orch)

    class Executor(_RecordingExecutor):
        def set_invocation_context(self, **kwargs) -> None:
            self.context = kwargs.copy()

        def run(self, **kwargs):
            try:
                task_id, agent = self.context["task_id"], self.context["agent"]
                session_id = kwargs["session_id"]
                launches.append((task_id, agent, session_id, kwargs["running"].request_id,
                                 tracker.get_pid(task_id, agent)))
                if task_id == parent_id:
                    decision, verdict = NextStep(action="delegate", agent="dev_agent", prompt="first", expect_verdict="PASS", then=[ChainLeg(agent="dev_agent", prompt="second")]), None
                else:
                    child_held.set()
                    assert release_child.wait(5), "late callback was not released"
                    decision, verdict = NextStep(action="done", summary="child"), "PASS"
                body = CompletionBody(session_id=session_id, agent=agent, status="completed", confidence=100, verdict=verdict, output_summary="chain callback", decision=decision.model_dump())
                try:
                    assert asyncio.run(submit_completion(task_id, body, org)) == {"ok": True}
                except HTTPException as exc:
                    late_statuses.append((exc.status_code, exc.detail))
                    assert task_id != parent_id and exc.status_code == 409
                kwargs["running"].process.terminate()
                return dataclasses.replace(super().run(**kwargs), session_id=session_id)
            except BaseException as exc:
                errors.append((exc, traceback.format_exc()))
                raise
            finally:
                kwargs["running"].process.terminate()

    monkeypatch.setattr(orch, "_build_executor", lambda _provider: Executor())
    supervisor = HostSessionSupervisor(backend=backend, policy=canary_policy(sample_interval_seconds=0), publisher=receipts.append)
    orch.attach_host_supervisor(supervisor)
    state = DaemonState.idle(orch._settings)
    state.orgs["test"] = SimpleNamespace(orchestrator=orch)
    orch.attach_queue(state.queue)
    parent = TaskRecord(id=parent_id, team="engineering", brief="chain", assigned_agent="engineering_head", task_type="task")
    db.insert_task(parent)
    state.queue.enqueue("test", parent_id)
    original_controls = tracker.iter_task_cancel_controls

    def observed_controls(task_id):
        """Observe durable route effects at the original opaque-control boundary."""
        controls = original_controls(task_id)
        wrapped = []
        for agent, original in controls:
            def invoke(agent=agent, original=original):
                with sqlite3.connect(f"file:{paths.db_path}?mode=ro", uri=True, timeout=1) as reader:
                    reader.row_factory = sqlite3.Row
                    rows = [dict(row) for row in reader.execute(
                        "SELECT id, status, cancelled_at FROM tasks WHERE id IN (?, ?) ORDER BY id",
                        (parent_id, first),
                    )]
                    audits = [dict(row) for row in reader.execute(
                        "SELECT task_id, action, payload FROM audit_log WHERE task_id IN (?, ?) AND action = 'task_cancelled' ORDER BY task_id, id",
                        (parent_id, first),
                    )]
                control_entries.append((task_id, agent, rows, audits))
                return original()
            wrapped.append((agent, invoke))
        return wrapped
    monkeypatch.setattr(tracker, "iter_task_cancel_controls", observed_controls)
    dispatcher = Dispatcher(state)
    original_dispatch = dispatcher.run_step
    def observed_dispatch(*args, **kwargs):
        try:
            return original_dispatch(*args, **kwargs)
        except BaseException as exc:
            errors.append((exc, traceback.format_exc()))
            raise
    monkeypatch.setattr(dispatcher, "run_step", observed_dispatch)
    def worker():
        try:
            asyncio.run(state.queue.drain_sync(dispatcher))
        except BaseException as exc:
            errors.append((exc, traceback.format_exc()))
        finally:
            done.set()
    thread = threading.Thread(target=worker, name="u0-chain-cancel-before-callback", daemon=False)
    thread.start()
    assertion = None
    try:
        assert child_held.wait(3), "first contained child was not admitted/launched"
        first = db.get_children(parent_id)[0]
        before = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent_id, first), agent_names=("engineering_head", "dev_agent"))
        assert [(task_id, agent, request_id) for task_id, agent, _session, request_id, _pid in launches] == [
            (parent_id, "engineering_head", parent_id), (first, "dev_agent", first)]
        assert all(session_id and request_id and pid
                   for _task, _agent, session_id, request_id, pid in launches)
        assert all(pid == 7000 + index for index, (_task, _agent, _session, _request, pid) in enumerate(launches, 1))
        assert [(request.org, request.logical_id, request.retry_attempt) for request in backend.requests] == [
            ("test", parent_id, 0), ("test", first, 0)]
        cancelled = asyncio.run(cancel_task(parent_id, CancelBody(rationale="chain callback race", cascade=True), org))
        with sqlite3.connect(f"file:{paths.db_path}?mode=ro", uri=True, timeout=1) as reader:
            reader.row_factory = sqlite3.Row
            durable = [dict(row) for row in reader.execute("SELECT id, status, cancelled_at FROM tasks WHERE id IN (?, ?) ORDER BY id", (first, parent_id))]
        after_cancel = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent_id, first), agent_names=("engineering_head", "dev_agent"))
        assert cancelled["cancelled"] == [parent_id, first] and cancelled["killed"] == [{"task_id": first, "agent": "dev_agent"}]
        assert all(row["status"] == TaskStatus.CANCELLED.value and row["cancelled_at"] for row in durable)
        assert len(control_entries) == 1
        _tid, _agent, entry_rows, entry_audits = control_entries[0]
        assert [row["id"] for row in entry_rows] == [first, parent_id]
        assert all(row["status"] == TaskStatus.CANCELLED.value and row["cancelled_at"] for row in entry_rows)
        assert [row["task_id"] for row in entry_audits] == [first, parent_id]
        assert all(row["action"] == "task_cancelled" for row in entry_audits)
        assert [json.loads(row["payload"]) for row in entry_audits] == [
            {"rationale": "chain callback race", "cascade": True},
            {"rationale": "chain callback race", "cascade": True},
        ]
        assert len(after_cancel["audits"][parent_id]) > len(before["audits"][parent_id])
        assert len(after_cancel["audits"][first]) > len(before["audits"][first])
        assert after_cancel["results"][first] == [] and after_cancel["active_chain"][parent_id] == before["active_chain"][parent_id]
    except BaseException as exc:
        assertion = exc
    finally:
        release_child.set()
        thread.join(8)
    assert not thread.is_alive() and done.is_set()
    if errors:
        raise BaseExceptionGroup("worker failures", [error for error, _trace in errors])
    if assertion:
        raise assertion
    final = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent_id, first), agent_names=("engineering_head", "dev_agent"))
    assert late_statuses == [(409, {"code": "task_not_active", "task_id": first,
                                   "status": TaskStatus.CANCELLED.value, "cancelled": True})]
    assert [task_id for task_id, *_rest in launches] == [parent_id, first]
    assert set(final["tasks"]) == {parent_id, first} and len(db.get_children(parent_id)) == 1
    assert all(final["tasks"][task_id]["status"] == TaskStatus.CANCELLED.value for task_id in (parent_id, first))
    assert final["results"][first] == [] and final["queue"] == [] and state.queue._queue._unfinished_tasks == 0
    # Every captured residue surface has a source-derived allowed delta: task
    # and cancellation-audit terminal fields change; no archive/team/workspace
    # input, attachment observation, fanout, result, or chain binding changes.
    for surface in ("attachments", "canonical_agents", "archived_agents", "workspaces",
                    "archived_workspaces", "teams_bytes", "active_fanout"):
        assert final[surface] == before[surface]
    assert final["results"][parent_id] == before["results"][parent_id]
    assert final["audits"][parent_id][:len(before["audits"][parent_id])] == before["audits"][parent_id]
    assert final["audits"][first][:len(before["audits"][first])] == before["audits"][first]
    assert final["active_chain"][parent_id] == before["active_chain"][parent_id]
    assert not [row for row in final["audits"][parent_id] if row["action"] == "chain_auto_advance"]
    assert [row["action"] for row in final["audits"][parent_id]].count("task_cancelled") == 1
    assert [row["action"] for row in final["audits"][first]].count("task_cancelled") == 1
    cancelled_audits = {
        task_id: [row for row in final["audits"][task_id] if row["action"] == "task_cancelled"]
        for task_id in (parent_id, first)
    }
    assert {
        (row["task_id"], row["action"], row["payload"]["cascade"])
        for rows in cancelled_audits.values() for row in rows
    } == {(parent_id, "task_cancelled", True), (first, "task_cancelled", True)}
    assert not any(final["controls"].values()) and all(session is None for session in final["sessions"].values())
    assert {task_id: final["pids"][task_id] for task_id, _agent in {(task_id, agent) for task_id, agent, *_rest in launches}} == {parent_id: None, first: None}
    assert db.get_task(parent_id).orchestration_step_count == db.get_task(first).orchestration_step_count == 1
    assert supervisor._admission.admitted_total() == supervisor._admission.released_total() == 2
    assert len(receipts) == len(launches) == 2
    receipt_rows = _receipt_evidence(receipts)
    # Receipt attribution is deliberately empty in this external fake; its
    # exact cardinality is joined to the nonempty request/running/session
    # identities above rather than inventing a receipt task identifier.
    assert [row["invocation_kind"] for row in receipt_rows] == ["", ""]
    assert all(row["cleanup_status"] == "clean" and row["quiescent"] and row["survivors"] == 0 for row in receipt_rows)


def test_r1_chain_cancel_after_advance_before_next_publication(tmp_path, monkeypatch) -> None:
    """A committed second leg is cancelled at the real held queue-publication seam."""
    from runtime.daemon.dispatcher import Dispatcher
    from runtime.daemon.routes.tasks import CancelBody, CompletionBody, cancel_task, submit_completion
    from runtime.daemon.state import DaemonState
    from runtime.models import ChainLeg, NextStep, TaskRecord, TaskStatus
    from runtime.orchestrator.host_supervisor import HostSessionSupervisor, canary_policy
    from tests.daemon.test_task_producer_containment import _FakeBackend, _RecordingExecutor, _make_orch

    parent_id = "TASK-U0-CHAIN-CANCEL-PUBLICATION"
    backend = _FakeBackend(auto_exit_after=0)
    orch, _, tracker, db = _make_orch(tmp_path, backend, _RecordingExecutor(), monkeypatch)
    paths, receipts, launches, errors, published, dispatches = orch._paths, [], [], [], [], []
    publication_held, release_publication, done = threading.Event(), threading.Event(), threading.Event()

    class EventSink:
        async def publish(self, task_id, event):
            return None

    org = SimpleNamespace(db=db, sessions=tracker, db_lock=asyncio.Lock(), event_bus=EventSink(), orchestrator=orch)

    class Executor(_RecordingExecutor):
        def set_invocation_context(self, **kwargs) -> None:
            self.context = kwargs.copy()

        def run(self, **kwargs):
            try:
                task_id, agent, session_id = self.context["task_id"], self.context["agent"], kwargs["session_id"]
                launches.append((task_id, agent, session_id, kwargs["running"].request_id,
                                 tracker.get_pid(task_id, agent)))
                if task_id == parent_id and not db.get_children(parent_id):
                    decision, verdict = NextStep(action="delegate", agent="dev_agent", prompt="first", expect_verdict="PASS", then=[ChainLeg(agent="dev_agent", prompt="second")]), None
                elif task_id == parent_id:
                    decision, verdict = NextStep(action="done", summary="unexpected parent revisit"), None
                else:
                    decision, verdict = NextStep(action="done", summary="first"), "PASS"
                body = CompletionBody(session_id=session_id, agent=agent, status="completed", confidence=100, verdict=verdict, output_summary="chain callback", decision=decision.model_dump())
                assert asyncio.run(submit_completion(task_id, body, org)) == {"ok": True}
                kwargs["running"].process.terminate()
                return dataclasses.replace(super().run(**kwargs), session_id=session_id)
            except BaseException as exc:
                errors.append((exc, traceback.format_exc()))
                raise
            finally:
                kwargs["running"].process.terminate()

    monkeypatch.setattr(orch, "_build_executor", lambda _provider: Executor())
    supervisor = HostSessionSupervisor(backend=backend, policy=canary_policy(sample_interval_seconds=0), publisher=receipts.append)
    orch.attach_host_supervisor(supervisor)
    state = DaemonState.idle(orch._settings)
    state.orgs["test"] = SimpleNamespace(orchestrator=orch)
    orch.attach_queue(state.queue)
    original_put = state.queue.put_nowait
    def held_put(slug, task_id, *args, **kwargs):
        published.append((slug, task_id))
        if task_id != parent_id and len(db.get_children(parent_id)) == 2:
            publication_held.set()
            assert release_publication.wait(5), "held original queue publication was not released"
        return original_put(slug, task_id, *args, **kwargs)
    monkeypatch.setattr(state.queue, "put_nowait", held_put)
    parent = TaskRecord(id=parent_id, team="engineering", brief="chain", assigned_agent="engineering_head", task_type="task")
    db.insert_task(parent)
    state.queue.enqueue("test", parent_id)
    dispatcher = Dispatcher(state)
    original_dispatch = dispatcher.run_step
    def observed_dispatch(*args, **kwargs):
        # `drain_sync` has dequeued this real shipping item before it calls the
        # dispatcher. Capture the durable row at that boundary, so the
        # released second publication proves the cancelled queue path itself
        # reached its normal skip gate rather than merely inferring it from no
        # executor launch.
        _slug, dispatched_task_id = args[:2]
        dispatched = db.get_task(dispatched_task_id)
        dispatches.append((dispatched_task_id, dispatched.status.value if dispatched else None))
        try:
            return original_dispatch(*args, **kwargs)
        except BaseException as exc:
            errors.append((exc, traceback.format_exc()))
            raise
    monkeypatch.setattr(dispatcher, "run_step", observed_dispatch)
    def worker():
        try:
            asyncio.run(state.queue.drain_sync(dispatcher))
        except BaseException as exc:
            errors.append((exc, traceback.format_exc()))
        finally:
            done.set()
    thread = threading.Thread(target=worker, name="u0-chain-cancel-before-publication", daemon=False)
    thread.start()
    assertion = None
    try:
        assert publication_held.wait(3), "PASS chain did not reach original next-child publication"
        first, second = db.get_children(parent_id)
        before = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent_id, first, second), agent_names=("engineering_head", "dev_agent"))
        assert before["tasks"][first]["status"] == TaskStatus.COMPLETED.value
        assert before["tasks"][second]["status"] == TaskStatus.PENDING.value
        assert len(before["results"][first]) == 1 and before["results"][second] == []
        first_result = before["results"][first][0]
        assert (first_result["task_id"], first_result["agent"], first_result["session_id"], first_result["verdict"]) == (
            first, "dev_agent", before["tasks"][first]["current_session_id"], "PASS")
        chain = json.loads(before["tasks"][parent_id]["active_chain"])
        assert chain["step_index"] == 1
        # Shipping active_chain stores the index/origin audit, while child
        # bindings live in the tasks relation; assert both authoritative
        # surfaces rather than inventing an ID field in the serialized chain.
        assert db.get_children(parent_id) == [first, second]
        assert [(before["tasks"][task_id]["id"], before["tasks"][task_id]["assigned_agent"])
                for task_id in (first, second)] == [(first, "dev_agent"), (second, "dev_agent")]
        advance = [row for row in before["audits"][parent_id] if row["action"] == "chain_auto_advance"]
        assert len(advance) == 1 and advance[0]["task_id"] == parent_id
        assert advance[0]["payload"] == {"leg_index": 1, "spawned_child_id": second,
                                          "triggering_child_id": first, "triggering_verdict": "PASS",
                                          "chain_origin_step_audit_id": chain["step_audit_id"]}
        cancelled = asyncio.run(cancel_task(parent_id, CancelBody(rationale="chain publication race", cascade=True), org))
        with sqlite3.connect(f"file:{paths.db_path}?mode=ro", uri=True, timeout=1) as reader:
            reader.row_factory = sqlite3.Row
            durable = {row["id"]: dict(row) for row in reader.execute("SELECT id, status, cancelled_at FROM tasks WHERE id IN (?, ?, ?)", (parent_id, first, second))}
        assert cancelled["cancelled"] == [parent_id, second] and cancelled["killed"] == []
        assert durable[parent_id]["status"] == durable[second]["status"] == TaskStatus.CANCELLED.value
        assert durable[parent_id]["cancelled_at"] and durable[second]["cancelled_at"]
        assert durable[first]["status"] == TaskStatus.COMPLETED.value
    except BaseException as exc:
        assertion = exc
    finally:
        release_publication.set()
        thread.join(8)
    assert not thread.is_alive() and done.is_set()
    if errors:
        raise BaseExceptionGroup("worker failures", [error for error, _trace in errors])
    if assertion:
        raise assertion
    final = _r1_snapshot(db=db, tracker=tracker, paths=paths, queue=state.queue, task_ids=(parent_id, first, second), agent_names=("engineering_head", "dev_agent"))
    assert [task_id for task_id, *_rest in launches] == [parent_id, first]
    assert [(task_id, agent, request_id) for task_id, agent, _session, request_id, _pid in launches] == [
        (parent_id, "engineering_head", parent_id), (first, "dev_agent", first)]
    assert all(session_id and request_id and pid
               for _task, _agent, session_id, request_id, pid in launches)
    assert all(pid == 7000 + index for index, (_task, _agent, _session, _request, pid) in enumerate(launches, 1))
    assert [(request.org, request.logical_id, request.retry_attempt) for request in backend.requests] == [
        ("test", parent_id, 0), ("test", first, 0)]
    # The original shipping `put_nowait` is called only for the two chain
    # publications; the root uses the explicit test setup enqueue.
    assert published == [("test", first), ("test", second)]
    assert dispatches == [
        (parent_id, TaskStatus.PENDING.value),
        (first, TaskStatus.PENDING.value),
        # The released original publication reaches Dispatcher only after the
        # cancellation route's committed terminal transition; `run_step` then
        # takes its shipping cancelled-row skip with no third launch.
        (second, TaskStatus.CANCELLED.value),
    ]
    assert final["tasks"][parent_id]["status"] == final["tasks"][second]["status"] == TaskStatus.CANCELLED.value
    assert final["tasks"][first]["status"] == TaskStatus.COMPLETED.value and len(final["results"][first]) == 1
    assert final["results"][first] == before["results"][first]
    assert (first_result["task_id"], first_result["agent"], first_result["session_id"]) in {
        (task_id, agent, session_id) for task_id, agent, session_id, _request_id, _pid in launches
    }
    for surface in ("attachments", "canonical_agents", "archived_agents", "workspaces",
                    "archived_workspaces", "teams_bytes", "active_fanout"):
        assert final[surface] == before[surface]
    assert final["results"][parent_id] == before["results"][parent_id]
    assert final["audits"][first] == before["audits"][first]
    assert [row for row in final["audits"][parent_id] if row["action"] == "chain_auto_advance"] == advance
    assert [row for row in final["audits"][first] if row["action"] == "task_cancelled"] == []
    assert [(row["task_id"], row["payload"]["cascade"])
            for task_id in (parent_id, second)
            for row in final["audits"][task_id] if row["action"] == "task_cancelled"] == [
        (parent_id, True), (second, True)]
    assert final["results"][second] == [] and final["queue"] == [] and state.queue._queue._unfinished_tasks == 0
    assert not any(final["controls"].values()) and all(session is None for session in final["sessions"].values())
    assert {task_id: final["pids"][task_id] for task_id, _agent in {(task_id, agent) for task_id, agent, *_rest in launches}} == {parent_id: None, first: None}
    assert db.get_task(parent_id).orchestration_step_count == db.get_task(first).orchestration_step_count == 1
    assert db.get_task(second).orchestration_step_count == 0
    assert supervisor._admission.admitted_total() == supervisor._admission.released_total() == 2
    assert len(receipts) == len(launches) == 2
    receipt_rows = _receipt_evidence(receipts)
    assert [row["invocation_kind"] for row in receipt_rows] == ["", ""]
    assert all(row["cleanup_status"] == "clean" and row["quiescent"] and row["survivors"] == 0 for row in receipt_rows)


def test_r1_chain_cancel_harness_propagates_dispatcher_error(tmp_path, monkeypatch) -> None:
    """The new cancellation harness exposes a queue-swallowed dispatcher error."""
    from runtime.daemon.dispatcher import Dispatcher

    original = Dispatcher.run_step
    calls = 0

    def injected(self, *args, **kwargs):
        nonlocal calls
        result = original(self, *args, **kwargs)
        calls += 1
        if calls == 1:
            raise RuntimeError("U0_CANCEL_DISPATCH_ERROR_CONTROL")
        return result

    monkeypatch.setattr(Dispatcher, "run_step", injected)
    with pytest.raises(BaseExceptionGroup, match="worker failures") as raised:
        test_r1_chain_cancel_before_first_callback_rejects_late_result(tmp_path, monkeypatch)
    assert any("U0_CANCEL_DISPATCH_ERROR_CONTROL" in str(error)
               for error in raised.value.exceptions)


def test_r1_chain_cancel_publication_harness_propagates_dispatcher_error(tmp_path, monkeypatch) -> None:
    """The distinct publication wrapper also rethrows after release and join."""
    from runtime.daemon.dispatcher import Dispatcher

    original = Dispatcher.run_step
    calls = 0

    def injected(self, *args, **kwargs):
        nonlocal calls
        result = original(self, *args, **kwargs)
        calls += 1
        if calls == 1:
            raise RuntimeError("U0_CANCEL_PUBLICATION_DISPATCH_ERROR_CONTROL")
        return result

    monkeypatch.setattr(Dispatcher, "run_step", injected)
    with pytest.raises(BaseExceptionGroup, match="worker failures") as raised:
        test_r1_chain_cancel_after_advance_before_next_publication(tmp_path, monkeypatch)
    assert any("U0_CANCEL_PUBLICATION_DISPATCH_ERROR_CONTROL" in str(error)
               for error in raised.value.exceptions)
