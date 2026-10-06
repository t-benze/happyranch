"""Real loopback transport proof for the shipping completion CLI (THR-229 A).

The mock-based ``tests/test_cli.py`` cases prove only that the shipping CLI
*constructs* a request carrying ``manager_self_evaluation``.  These tests drive
the real shipping ``happyranch report-completion --from-file`` command over a
real uvicorn server bound to literal ``127.0.0.1`` and assert both the raw
bytes the server received and the durable ``task_results`` row the route
persisted.  They deliberately do **not** claim the later staged v2
CLI -> durable result -> hook -> Pending/enqueue lifecycle.

The v1 launch/session binding is produced by the ordinary launch path
(``Orchestrator._run_agent``), never fabricated.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import uvicorn

from runtime.daemon.app import create_app
from runtime.models import TaskStatus


_OMITTED = object()


# --------------------------------------------------------------------------
# Real loopback server with independent raw-request capture
# --------------------------------------------------------------------------


class _CaptureCompletionTraffic:
    """Pure ASGI wrapper recording raw body + status for completion POSTs."""

    def __init__(self, app) -> None:
        self.app = app
        self.records: list[dict] = []

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).endswith("/completion"):
            await self.app(scope, receive, send)
            return
        record: dict = {"body": b"", "status": None, "headers": dict(scope.get("headers") or [])}
        self.records.append(record)

        async def observed_receive():
            message = await receive()
            if message["type"] == "http.request":
                record["body"] += message.get("body", b"")
            return message

        async def observed_send(message):
            if message["type"] == "http.response.start":
                record["status"] = message["status"]
            await send(message)

        await self.app(scope, observed_receive, observed_send)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _LoopbackServer:
    def __init__(self, asgi_app) -> None:
        self.port = _free_port()
        config = uvicorn.Config(
            asgi_app, host="127.0.0.1", port=self.port,
            log_level="warning", lifespan="off",
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def __enter__(self) -> "_LoopbackServer":
        self._thread.start()
        deadline = time.monotonic() + 15.0
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        if not self._server.started:  # pragma: no cover - environment failure
            raise AssertionError("loopback uvicorn server did not start")
        port_file = Path(os.environ["HAPPYRANCH_DAEMON_HOME"]) / "daemon.port"
        port_file.write_text(str(self.port))
        return self

    def __exit__(self, *exc) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=15.0)


# --------------------------------------------------------------------------
# Ordinary launch helpers (real binding, never fabricated)
# --------------------------------------------------------------------------


def _seed_workspace(org, agent: str) -> None:
    from tests.conftest import seed_test_agents

    paths = org.orchestrator._paths
    seed_test_agents(paths, (agent,))
    workspace = paths.workspaces_dir / agent
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "task_history.md").write_text(f"# Task History: {agent}\n")
    # THR-262 Slice B: canonical instruction pair required before launch.
    (workspace / "AGENTS.md").write_text(f"# Agent: {agent}\n")
    (workspace / "CLAUDE.md").symlink_to("AGENTS.md")


def _install_engineering_team(org, manager: str) -> None:
    from runtime.orchestrator.teams import TeamManager

    org.orchestrator._teams._teams["engineering"] = TeamManager(
        name=manager, team="engineering", workers=("dev_agent",),
    )


def _completion_file(tmp_path: Path, body: dict) -> str:
    path = tmp_path / "completion.json"
    path.write_text(json.dumps(body))
    return str(path)


def _run_shipping_completion_cli(from_file: str) -> None:
    from cli.main import cmd_report_completion

    cmd_report_completion(SimpleNamespace(org="alpha", from_file=from_file))


def _launch_manager_and_complete(
    org, monkeypatch, tmp_path: Path, *, session_id: str, evaluation_factory,
) -> tuple[str, dict]:
    """Real launch -> binding -> shipping CLI completion over loopback HTTP."""
    from runtime.orchestrator.authority import StrictFakeAuthorityEvaluator
    from runtime.orchestrator.executors import ExecutorResult
    from tests.authority_policy_test_factory import activate_test_policy

    manager = "engineering_manager"
    _seed_workspace(org, manager)
    _install_engineering_team(org, manager)
    evaluator = StrictFakeAuthorityEvaluator()
    evaluator.provider_id = "strict-fake"
    evaluator._executor_kind = "test"
    org.orchestrator._authority_evaluator = evaluator
    org.orchestrator._host_supervisor = None
    release, _activation = activate_test_policy(org.db)
    task_id = org.orchestrator.create_task("manager policy handoff")
    org.db.update_task(
        task_id, assigned_agent=manager, status=TaskStatus.IN_PROGRESS,
        orchestration_step_count=1,
    )
    monkeypatch.setattr(org.orchestrator, "_build_session_id", lambda: session_id)
    executor = MagicMock()
    captured: dict = {}

    def run(**kwargs):
        from runtime.orchestrator.active_authority_policy import load_session_policy_binding

        kwargs["on_started"](4242)
        binding = load_session_policy_binding(
            db=org.db, task_id=task_id, session_id=session_id, agent_name=manager,
        )
        captured["binding"] = binding
        body = {
            "task_id": task_id, "session_id": session_id, "agent": manager,
            "status": "completed", "confidence": 90, "summary": "escalate",
            "decision": {
                "action": "escalate",
                "reason": "routine same-root follow-through of the already-completed slice",
            },
        }
        evaluation = evaluation_factory(binding, task_id, release)
        if evaluation is not _OMITTED:
            body["manager_self_evaluation"] = evaluation
        _run_shipping_completion_cli(_completion_file(tmp_path, body))
        return ExecutorResult(success=True, duration_seconds=1, session_id="provider-session")

    executor.run.side_effect = run
    with patch.object(org.orchestrator, "_build_executor", return_value=executor):
        result, _report = org.orchestrator._run_agent(task_id, manager, "decide")
    assert result.success
    return task_id, captured, release


def _launch_worker_and_complete(
    org, monkeypatch, tmp_path: Path, *, session_id: str,
) -> tuple[str, dict]:
    from runtime.orchestrator.authority import StrictFakeAuthorityEvaluator
    from runtime.orchestrator.executors import ExecutorResult

    worker = "dev_agent"
    _seed_workspace(org, worker)
    _install_engineering_team(org, "engineering_manager")
    evaluator = StrictFakeAuthorityEvaluator()
    evaluator.provider_id = "strict-fake"
    evaluator._executor_kind = "test"
    org.orchestrator._authority_evaluator = evaluator
    org.orchestrator._host_supervisor = None
    task_id = org.orchestrator.create_task("worker handoff")
    org.db.update_task(
        task_id, assigned_agent=worker, status=TaskStatus.IN_PROGRESS,
        orchestration_step_count=1,
    )
    monkeypatch.setattr(org.orchestrator, "_build_session_id", lambda: session_id)
    executor = MagicMock()

    def run(**kwargs):
        kwargs["on_started"](4243)
        body = {
            "task_id": task_id, "session_id": session_id, "agent": worker,
            "status": "completed", "confidence": 90, "summary": "done",
        }
        _run_shipping_completion_cli(_completion_file(tmp_path, body))
        return ExecutorResult(success=True, duration_seconds=1, session_id="provider-session")

    executor.run.side_effect = run
    with patch.object(org.orchestrator, "_build_executor", return_value=executor):
        result, _report = org.orchestrator._run_agent(task_id, worker, "work")
    assert result.success
    return task_id, {}


def _captured_completion_json(capture: _CaptureCompletionTraffic) -> dict:
    assert len(capture.records) == 1, capture.records
    record = capture.records[0]
    assert record["status"] == 200, record
    return json.loads(record["body"])


def _durable_decision(org, task_id: str, agent: str, session_id: str) -> dict:
    row = org.db.get_latest_task_result(task_id, agent, session_id)
    assert row is not None
    return json.loads(row["decision_json"]) if row["decision_json"] else {}


def _valid_v1_evaluation(binding: dict, task_id: str, release) -> dict:
    from runtime.orchestrator.active_authority_policy import (
        SELF_EVALUATION_CONTRACT_DIGEST, SELF_EVALUATION_CONTRACT_ID,
        SELF_EVALUATION_CONTRACT_VERSION,
    )

    return {
        "contract_id": SELF_EVALUATION_CONTRACT_ID,
        "contract_version": SELF_EVALUATION_CONTRACT_VERSION,
        "contract_digest": SELF_EVALUATION_CONTRACT_DIGEST,
        "root_task_id": task_id,
        "manager_session_id": binding["session_id"],
        "release_id": binding["release_id"],
        "policy_version": str(release.version),
        "policy_digest": binding["policy_digest"],
        "activation_id": binding["activation_id"],
        "activation_epoch": binding["activation_epoch"],
        "provider_id": binding["provider_id"],
        "executor_kind": binding["executor_kind"],
        "model_id": binding["model_id"],
        "disposition": "continue_same_root",
        "clause_id": "cont-routine-same-root",
        "action": "continue_same_root",
        "confidence": 1.0,
        "uncertainty_codes": [],
    }


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


def test_shipping_cli_loopback_persists_valid_launch_bound_v1_evaluation(
    tmp_home, daemon_state, monkeypatch, tmp_path,
) -> None:
    org = daemon_state.orgs["alpha"]
    capture = _CaptureCompletionTraffic(create_app(daemon_state))
    with _LoopbackServer(capture):
        task_id, captured, release = _launch_manager_and_complete(
            org, monkeypatch, tmp_path, session_id="sess-loopback-valid",
            evaluation_factory=_valid_v1_evaluation,
        )
    binding = captured["binding"]
    assert binding and binding["mode"] == "db_release"
    received = _captured_completion_json(capture)
    expected = _valid_v1_evaluation(binding, task_id, release)
    assert received["manager_self_evaluation"] == expected
    decision = _durable_decision(org, task_id, "engineering_manager", "sess-loopback-valid")
    persisted = decision["_manager_self_evaluation"]
    assert persisted == expected
    assert persisted["release_id"] == binding["release_id"]
    assert persisted["activation_id"] == binding["activation_id"]
    assert persisted["activation_epoch"] == binding["activation_epoch"]
    assert persisted["manager_session_id"] == "sess-loopback-valid"
    assert persisted["root_task_id"] == task_id
    assert persisted["provider_id"] == binding["provider_id"]
    assert persisted["executor_kind"] == binding["executor_kind"]
    assert persisted["model_id"] == binding["model_id"]


def test_shipping_cli_loopback_worker_omitted_value_stays_omitted(
    tmp_home, daemon_state, monkeypatch, tmp_path,
) -> None:
    org = daemon_state.orgs["alpha"]
    capture = _CaptureCompletionTraffic(create_app(daemon_state))
    with _LoopbackServer(capture):
        task_id, _ = _launch_worker_and_complete(
            org, monkeypatch, tmp_path, session_id="sess-loopback-worker",
        )
    received = _captured_completion_json(capture)
    assert "manager_self_evaluation" not in received
    decision = _durable_decision(org, task_id, "dev_agent", "sess-loopback-worker")
    assert "_manager_self_evaluation" not in decision


def test_shipping_cli_loopback_present_null_is_indistinguishable_from_omission(
    tmp_home, daemon_state, monkeypatch, tmp_path,
) -> None:
    """Documents the exact current v1 route limitation for the later unit.

    ``CompletionBody.manager_self_evaluation: object | None = None`` collapses a
    present JSON ``null`` into the same ``None`` as an omitted member, so the
    route skips validation entirely.  The member *does* reach the route (proved
    by the captured raw body) but v1 cannot distinguish the two cases.
    """
    org = daemon_state.orgs["alpha"]
    capture = _CaptureCompletionTraffic(create_app(daemon_state))
    with _LoopbackServer(capture):
        task_id, _, _ = _launch_manager_and_complete(
            org, monkeypatch, tmp_path, session_id="sess-loopback-null",
            evaluation_factory=lambda binding, tid, rel: None,
        )
    received = _captured_completion_json(capture)
    assert "manager_self_evaluation" in received
    assert received["manager_self_evaluation"] is None
    decision = _durable_decision(
        org, task_id, "engineering_manager", "sess-loopback-null",
    )
    assert "_manager_self_evaluation" not in decision


@pytest.mark.parametrize(
    "invalid",
    [False, "", [], "not-an-object", {"malformed": True}],
)
def test_shipping_cli_loopback_present_invalid_values_reach_v1_validator(
    tmp_home, daemon_state, monkeypatch, tmp_path, invalid,
) -> None:
    org = daemon_state.orgs["alpha"]
    capture = _CaptureCompletionTraffic(create_app(daemon_state))
    with _LoopbackServer(capture):
        task_id, _, _ = _launch_manager_and_complete(
            org, monkeypatch, tmp_path, session_id="sess-loopback-invalid",
            evaluation_factory=lambda binding, tid, rel: invalid,
        )
    received = _captured_completion_json(capture)
    assert "manager_self_evaluation" in received
    assert received["manager_self_evaluation"] == invalid
    decision = _durable_decision(
        org, task_id, "engineering_manager", "sess-loopback-invalid",
    )
    persisted = decision["_manager_self_evaluation"]
    assert persisted["_error_code"] == "malformed_output"
    assert len(persisted["payload_digest"]) == 64


# THR-259 seq418: reporting input is synthetic, never proof of real removal.
_CLEANUP_REPORT_INPUTS = [
    pytest.param("completed", '{"action":"done","summary":"Removed 0; skipped 2 (live use, uncertain owner); allocated 0 B; apparent 0 B; unique reclaimed bytes unknown; report-only."}',
                 "Independent audit not performed", "completed", id="report_only"),
    pytest.param("completed", '{"action":"done","summary":"Removed 2; skipped 1 (protected); allocated reclaimed 8192 B; apparent removed 12288 B; filesystem delta 4096 B unattributed; unique reclaimed bytes unknown."}',
                 "Concurrent writes prevent causal free-space attribution", "completed", id="recorded_actions"),
    pytest.param("failed", '{"action":"done","summary":"Removed 1; skipped 2; failed 1; partial batch stopped; allocated reclaimed 4096 B; apparent removed 8192 B; residual allocated 2048 B; unique reclaimed bytes unknown."}',
                 "Protected-path postcheck unavailable; independent audit pending", "completed", id="partial_failure"),
    pytest.param("blocked", "Removed 0; skipped all; sizing unavailable; allocated/apparent/unique reclaimed bytes unknown; independent verification incomplete.",
                 "Independent verification incomplete", "failed", id="unavailable"),
    pytest.param("completed", "Removed 0; skipped 2 (live use, uncertain owner); allocated 0 B; apparent 0 B; unique reclaimed bytes unknown; report-only.",
                 "Independent audit not performed", "escalated", id="plain_prose_control"),
]


def _prepare_cleanup_worker(org, monkeypatch):
    from runtime.orchestrator.authority import StrictFakeAuthorityEvaluator
    _seed_workspace(org, "dev_agent")
    _install_engineering_team(org, "engineering_head")
    evaluator = StrictFakeAuthorityEvaluator()
    evaluator.provider_id = "strict-fake"
    evaluator._executor_kind = "test"
    org.orchestrator._authority_evaluator = evaluator
    org.orchestrator._host_supervisor = None
    # Ordinary _run_agent creates this session's actual durable/tracker binding.
    monkeypatch.setattr(org.orchestrator, "_build_session_id", lambda: "sess-cleanup-report")


@pytest.mark.parametrize("source", ["scheduled", "manual_alone", "manual_lf", "manual_crlf"])
@pytest.mark.parametrize("callback_status,summary,risk,terminal_status", _CLEANUP_REPORT_INPUTS)
def test_cleanup_shipping_callback_to_activity_and_terminal(
    tmp_home, daemon_state, monkeypatch, tmp_path, cleanup_thread_queue, source, callback_status, summary, risk, terminal_status,
):
    import asyncio
    from datetime import datetime, timedelta, timezone
    from cli.client.client import OpcClient
    from runtime.daemon import workspace_cleanup_scheduler as wcs
    from runtime.daemon import task_scratch_reclamation
    from runtime.models import TaskRecord, ThreadRecord
    from runtime.orchestrator.executors import ExecutorResult
    from tests.test_workspace_cleanup_scheduler import _reporting_thread_state

    org = daemon_state.orgs["alpha"]
    _prepare_cleanup_worker(org, monkeypatch)
    db = org.db
    admissions = []
    if source == "scheduled":
        monkeypatch.setattr(wcs, "measure_workspace_context", lambda *a, **kw: wcs.WorkspaceContextSnapshot(
            available=True, workspaces_bytes=1024 ** 3, workspaces_count=1,
            largest=[("dev_agent", 1024 ** 3)],
        ))
        tid = asyncio.run(wcs.trigger_cleanup(org, agent="dev_agent", enqueue=lambda slug, task: admissions.append((slug, task))))
        assert admissions == [("alpha", tid)]
    else:
        # Two old daemon occurrences do not grant a manual owner action authority.
        for i in range(2):
            db.insert_task(TaskRecord(id=f"TASK-{i+1}", brief=wcs._CLEANUP_BRIEF_MARKER+"\nprior",
                team="engineering", assigned_agent="dev_agent", status=TaskStatus.COMPLETED,
                created_at=datetime.now(timezone.utc)-timedelta(days=30-i)))
            db.insert_audit_log(f"TASK-{i+1}", "dev_agent", "workspace_cleanup_triggered", {
                "report_thread_id": None, "measurement_available": True, "measurement_reason": None,
                "measurement_truncated": False, "run_number": i+1, "brief_kind": "report_only",
            })
        ending = {"manual_alone": "", "manual_lf": "\nbody", "manual_crlf": "\r\nbody"}[source]
        tid = db.next_task_id()
        db.insert_task(TaskRecord(id=tid, brief="HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)"+ending,
                                  team="engineering", assigned_agent="dev_agent"))
        org.orchestrator._paths.org_config_path.write_text("workspace_cleanup:\n  reclamation_actions_enabled: true\n")
    before_history = db.summarize_workspace_cleanup_marker_history(wcs._CLEANUP_BRIEF_MARKER, assigned_agent="dev_agent")
    # Historical composed-from association is never a dispatched-from identity.
    db.insert_thread(ThreadRecord(id="THR-HISTORY", subject="Preserved historical report",
                                 composed_by="dev_agent", composed_from_task_id=tid))
    db.add_thread_participant("THR-HISTORY", "dev_agent", added_by="founder")
    thread_before = _reporting_thread_state(db)
    attempted_actions = []
    def forbidden_action(*args, **kwargs):
        attempted_actions.append((args, kwargs))
        raise AssertionError("reporting must not execute cleanup")
    monkeypatch.setattr(task_scratch_reclamation, "collect_revalidate_seal_consume_disposable", forbidden_action)
    monkeypatch.setattr(task_scratch_reclamation, "execute_ledger", forbidden_action)
    assert db.get_task(tid).task_type == "task"
    assert db.get_task(tid).parent_task_id is None
    assert db.get_task(tid).dispatched_from_thread_id is None
    capture = _CaptureCompletionTraffic(create_app(daemon_state))
    executor = MagicMock()
    observed = []
    output_dir = f"output/{tid}"
    def activity(client):
        r = client.get("/api/v1/orgs/alpha/agents/dev_agent/cleanup-activity")
        assert r.status_code == 200
        return next(row for row in r.json()["activities"] if row["task_id"] == tid)
    def run(**kwargs):
        kwargs["on_started"](4243)
        body = {"task_id": tid, "session_id": "sess-cleanup-report", "agent": "dev_agent",
                "status": callback_status, "confidence": 80, "summary": summary,
                "risks": [risk], "output_dir": output_dir}
        payload_path = _completion_file(tmp_path, body)
        _run_shipping_completion_cli(payload_path)
        client = OpcClient.from_env()
        first = activity(client)
        assert first["status"] == "in_progress"
        assert first["result_status"] == callback_status
        assert first["output_summary"] == summary
        observed.append(first)
        row = db.get_latest_task_result(tid, "dev_agent", "sess-cleanup-report")
        assert row["output_summary"] == summary and row["output_dir"] == output_dir
        assert row["risks_flagged"] == [risk]
        result_count = db.execute("SELECT COUNT(*) FROM task_results WHERE task_id=?", (tid,)).fetchone()[0]
        # Exact shipping retry after tracker clearing remains idempotent.
        _run_shipping_completion_cli(payload_path)
        assert db.execute("SELECT COUNT(*) FROM task_results WHERE task_id=?", (tid,)).fetchone()[0] == result_count == 1
        foreign = client.post(f"/api/v1/orgs/alpha/tasks/{tid}/completion", json={
            "agent": "dev_agent", "session_id": "foreign-session", "status": "completed", "confidence": 80, "output_summary": "foreign"})
        assert foreign.status_code == 409
        assert activity(client) == first
        assert _reporting_thread_state(db) == thread_before
        return ExecutorResult(success=True, duration_seconds=1, session_id="provider-session")
    executor.run.side_effect = run
    with _LoopbackServer(capture):
        with patch.object(org.orchestrator, "_build_executor", return_value=executor):
            org.orchestrator.run_step(tid)
        assert executor.run.call_count == 1
        final = activity(OpcClient.from_env())
    assert len(observed) == 1
    assert final["status"] == terminal_status, db.get_task(tid).note
    assert final["result_status"] == callback_status
    assert final["output_summary"] == summary
    assert [r["status"] for r in capture.records] == [200, 200, 409]
    received = json.loads(capture.records[0]["body"])
    assert received["output_summary"] == summary and received["risks_flagged"] == [risk]
    assert received["output_dir"] == output_dir and "decision" not in received
    assert _reporting_thread_state(db) == thread_before
    assert attempted_actions == []
    assert cleanup_thread_queue[0].size == 0
    assert not any(a["action"] == "workspace_cleanup_reclamation_attempt" for a in db.get_audit_logs(tid))
    after_history = db.summarize_workspace_cleanup_marker_history(wcs._CLEANUP_BRIEF_MARKER, assigned_agent="dev_agent")
    assert after_history.count == before_history.count == (1 if source == "scheduled" else 2)
    if source != "scheduled":
        assert not any(a["action"] == "workspace_cleanup_triggered" for a in db.get_audit_logs(tid))
    if callback_status == "blocked":
        assert "self-blocked" in db.get_task(tid).note
    if terminal_status == "escalated":
        assert "decision" in db.get_task(tid).note and "JSON" in db.get_task(tid).note


def test_explicit_cleanup_coordination_callback_terminal_followup(tmp_home, daemon_state, monkeypatch, tmp_path, cleanup_thread_queue):
    import asyncio
    from runtime.models import ThreadRecord, ThreadInvocationPurpose, ThreadMessageKind
    from runtime.orchestrator.executors import ExecutorResult
    org = daemon_state.orgs["alpha"]
    _prepare_cleanup_worker(org, monkeypatch)
    db = org.db
    db.insert_thread(ThreadRecord(id="THR-COORD", subject="Founder-requested cleanup coordination"))
    db.add_thread_participant("THR-COORD", "engineering_head", added_by="founder")
    tid = db.next_task_id()
    from runtime.models import TaskRecord
    db.insert_task(TaskRecord(id=tid, brief="Explicit founder coordination", team="engineering",
                              assigned_agent="dev_agent", dispatched_from_thread_id="THR-COORD"))
    org.orchestrator._audit.log_thread_dispatch("THR-COORD", task_id=tid,
        dispatcher="engineering_head", target_agent="dev_agent", team="engineering")
    queue, loop = cleanup_thread_queue
    capture = _CaptureCompletionTraffic(create_app(daemon_state))
    executor = MagicMock()
    def run(**kwargs):
        kwargs["on_started"](4243)
        _run_shipping_completion_cli(_completion_file(tmp_path, {
            "task_id": tid, "session_id": "sess-cleanup-report", "agent": "dev_agent",
            "status": "completed", "summary": '{"action":"done","summary":"Coordination recorded; no removal performed."}',
        }))
        assert db.get_task(tid).status == TaskStatus.IN_PROGRESS
        assert not [i for i in db.list_thread_invocations("THR-COORD") if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
        return ExecutorResult(success=True, duration_seconds=1, session_id="provider-session")
    executor.run.side_effect = run
    with _LoopbackServer(capture):
        with patch.object(org.orchestrator, "_build_executor", return_value=executor):
            org.orchestrator.run_step(tid)
    assert executor.run.call_count == 1
    assert db.get_task(tid).status == TaskStatus.COMPLETED
    followups = [i for i in db.list_thread_invocations("THR-COORD") if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert len(followups) == 1 and followups[0].agent_name == "engineering_head"
    job = asyncio.run_coroutine_threadsafe(queue.get(), loop).result(timeout=2)
    assert job.org_slug == "alpha" and job.invocation_token == followups[0].invocation_token
    messages = [m for m in db.list_thread_messages("THR-COORD") if m.kind == ThreadMessageKind.SYSTEM]
    assert len(messages) == 1
    assert messages[0].system_payload["kind_tag"] == "task_completed"
    assert messages[0].system_payload["task_id"] == tid
    assert _captured_completion_json(capture)["output_summary"] == '{"action":"done","summary":"Coordination recorded; no removal performed."}'


@pytest.fixture
def cleanup_thread_queue(daemon_state):
    """Real isolated queue/loop, mirroring the existing follow-up owner fixture."""
    import asyncio
    from runtime.daemon.thread_queue import ThreadQueue
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    queue = ThreadQueue()
    daemon_state.orgs["alpha"].orchestrator.attach_thread_queue(queue, loop)
    try:
        yield queue, loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        assert not thread.is_alive()
        loop.close()
