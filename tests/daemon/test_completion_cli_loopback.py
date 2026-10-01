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
