"""THR-229 checkpoint C2 — real CLI/HTTP v2 admission loopback proof.

Drives the shipping ``happyranch report-completion --from-file`` command over a
real uvicorn server bound to literal ``127.0.0.1`` against a real v2 policy
launch, and asserts the durable result, the admitted ``authority_policy_v2_attempts``
row and the ``authority_policy_v2_result_stage`` admission audit.  The v2 launch
binding is produced by the ordinary ``Orchestrator._run_agent`` path, never
fabricated, after the shared fixture seeds the canonical instruction pair, and
the assessment reaches the route through the strict
``_completion_payload_from_file`` decoder.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from runtime.daemon.app import create_app
from runtime.models import TaskStatus
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore

from tests.daemon.test_completion_cli_loopback import (
    _CaptureCompletionTraffic,
    _LoopbackServer,
    _OMITTED,
    _captured_completion_json,
    _completion_file,
    _durable_decision,
    _install_engineering_team,
    _run_shipping_completion_cli,
    _seed_workspace,
)

MANAGER = "engineering_manager"


def _activate_v2(org) -> None:
    store = AuthorityPolicyStore(org.db)
    selector = store.ensure_authority_selector("engineering")
    store.create_and_activate_v2({
        "team": "engineering", "policy_id": "engineering-dual-text", "title": "Dual",
        "create_request_id": "c2-loop-create",
        "activation_request_id": "c2-loop-activate",
        "based_on_selector_id": selector.selector_id,
        "expected_selector_id": selector.selector_id,
        "action": "bootstrap",
        "what_to_escalate": "Escalate 产品 / external-contract change.",
        "what_not_to_escalate": "Continue 実装, debugging and review corrections.",
    })


def _valid_v2_evaluation(binding: dict, task_id: str) -> dict:
    return {
        "activation_epoch": binding["selector_epoch"],
        "activation_id": binding["activation_id"],
        "contract_digest": binding["contract_digest"],
        "contract_id": binding["contract_id"],
        "contract_version": binding["contract_version"],
        "executor_kind": binding["executor_kind"],
        "manager_session_id": binding["session_id"],
        "model_id": binding["model_id"],
        "policy_digest": binding["policy_digest"],
        "policy_version": binding["policy_version"],
        "provider_id": binding["provider_id"],
        "release_id": binding["release_id"],
        "root_task_id": task_id,
        "what_not_to_escalate": {
            "applicability": "applies", "confidence": 90, "uncertainty_codes": [],
        },
        "what_to_escalate": {
            "applicability": "does_not_apply", "confidence": 90,
            "uncertainty_codes": [],
        },
    }


def _launch_v2_manager_and_complete(
    org, monkeypatch, tmp_path, *, session_id: str, evaluation_factory,
) -> tuple[str, dict]:
    from runtime.orchestrator.authority import StrictFakeAuthorityEvaluator
    from runtime.orchestrator.executors import ExecutorResult

    _seed_workspace(org, MANAGER)
    _install_engineering_team(org, MANAGER)
    evaluator = StrictFakeAuthorityEvaluator()
    evaluator.provider_id = "strict-fake"
    evaluator._executor_kind = "test"
    org.orchestrator._authority_evaluator = evaluator
    org.orchestrator._host_supervisor = None
    _activate_v2(org)
    task_id = org.orchestrator.create_task("manager v2 policy handoff")
    org.db.update_task(
        task_id, assigned_agent=MANAGER, status=TaskStatus.IN_PROGRESS,
        orchestration_step_count=1,
    )
    monkeypatch.setattr(org.orchestrator, "_build_session_id", lambda: session_id)
    executor = MagicMock()
    captured: dict = {}

    def run(**kwargs):
        from runtime.orchestrator.active_authority_policy import load_session_policy_binding

        kwargs["on_started"](4242)
        binding = load_session_policy_binding(
            db=org.db, task_id=task_id, session_id=session_id, agent_name=MANAGER,
        )
        captured["binding"] = binding
        body = {
            "task_id": task_id, "session_id": session_id, "agent": MANAGER,
            "status": "completed", "confidence": 90, "summary": "escalate",
            "decision": {"action": "escalate", "reason": "routine"},
        }
        evaluation = evaluation_factory(binding, task_id)
        if evaluation is not _OMITTED:
            body["manager_self_evaluation"] = evaluation
        captured["file"] = _completion_file(tmp_path, body)
        _run_shipping_completion_cli(captured["file"])
        return ExecutorResult(success=True, duration_seconds=1, session_id="provider-session")

    executor.run.side_effect = run
    with patch.object(org.orchestrator, "_build_executor", return_value=executor):
        result, _report = org.orchestrator._run_agent(task_id, MANAGER, "decide")
    assert result.success
    return task_id, captured


def test_shipping_cli_loopback_admits_valid_v2_attempt(
    tmp_home, daemon_state, monkeypatch, tmp_path,
) -> None:
    org = daemon_state.orgs["alpha"]
    capture = _CaptureCompletionTraffic(create_app(daemon_state))
    with _LoopbackServer(capture):
        task_id, captured = _launch_v2_manager_and_complete(
            org, monkeypatch, tmp_path, session_id="sess-c2-valid",
            evaluation_factory=_valid_v2_evaluation,
        )
    binding = captured["binding"]
    assert binding and binding["mode"] == "v2"
    received = _captured_completion_json(capture)
    expected = _valid_v2_evaluation(binding, task_id)
    assert received["manager_self_evaluation"] == expected

    decision = _durable_decision(org, task_id, MANAGER, "sess-c2-valid")
    assert decision["_manager_self_evaluation"] == expected
    assert org.db.get_task(task_id).status is TaskStatus.IN_PROGRESS

    row = org.db.get_latest_task_result(task_id, MANAGER, "sess-c2-valid")
    assert row is not None
    attempt = org.db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert attempt is not None
    assert attempt.stage == "admitted"
    assert attempt.finalization_state == "unfinalized"
    assert attempt.binding_id == binding["binding_id"]
    assert attempt.release_id == binding["release_id"]
    assert attempt.activation_id == binding["activation_id"]
    audits = org.db.list_authority_policy_v2_result_stage_audits(
        root_task_id=task_id, manager_agent=MANAGER,
    )
    assert [a["payload"]["stage"] for a in audits] == ["admitted"]
    assert audits[0]["payload"]["result_id"] == row["id"]


def test_shipping_cli_exact_v2_transport_retry_is_read_only(
    tmp_home, daemon_state, monkeypatch, tmp_path,
) -> None:
    org = daemon_state.orgs["alpha"]
    capture = _CaptureCompletionTraffic(create_app(daemon_state))
    with _LoopbackServer(capture):
        task_id, captured = _launch_v2_manager_and_complete(
            org, monkeypatch, tmp_path, session_id="sess-c2-replay",
            evaluation_factory=_valid_v2_evaluation,
        )
        result_rows = org.db.get_task_results(task_id)
        audit_rows = org.db.list_authority_policy_v2_result_stage_audits(
            root_task_id=task_id, manager_agent=MANAGER,
        )
        # Exact transport retry after the tracker cleared: read-only success.
        _run_shipping_completion_cli(captured["file"])
    assert len(capture.records) == 2
    assert all(r["status"] == 200 for r in capture.records)
    assert len(org.db.get_task_results(task_id)) == len(result_rows) == 1
    assert len(org.db.list_authority_policy_v2_result_stage_audits(
        root_task_id=task_id, manager_agent=MANAGER,
    )) == len(audit_rows) == 1


def test_shipping_cli_changed_v2_payload_replay_fails(
    tmp_home, daemon_state, monkeypatch, tmp_path,
) -> None:
    org = daemon_state.orgs["alpha"]
    capture = _CaptureCompletionTraffic(create_app(daemon_state))
    with _LoopbackServer(capture):
        task_id, captured = _launch_v2_manager_and_complete(
            org, monkeypatch, tmp_path, session_id="sess-c2-changed",
            evaluation_factory=_valid_v2_evaluation,
        )
        result_rows = org.db.get_task_results(task_id)
        # Rewrite the payload with a changed assessment; the same session's
        # exact transport must not silently succeed after the tracker cleared.
        import json as _json
        from pathlib import Path

        payload = _json.loads(Path(captured["file"]).read_text())
        payload["manager_self_evaluation"]["what_to_escalate"]["confidence"] = 10
        Path(captured["file"]).write_text(_json.dumps(payload))
        with pytest.raises(SystemExit):
            _run_shipping_completion_cli(captured["file"])
    assert capture.records[-1]["status"] == 409
    assert len(org.db.get_task_results(task_id)) == len(result_rows) == 1
    assert len(org.db.list_authority_policy_v2_result_stage_audits(
        root_task_id=task_id, manager_agent=MANAGER,
    )) == 1


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update(summary="changed summary"),
        lambda p: p.update(decision={"action": "escalate", "reason": "changed"}),
        lambda p: p.update(confidence=10),
        lambda p: p.update(verdict="REQUEST_CHANGES"),
        lambda p: p.update(risks=["new"]),
        lambda p: p.update(output_dir="output/other"),
    ],
)
def test_shipping_cli_changed_completion_field_after_tracker_cleared_refuses(
    tmp_home, daemon_state, monkeypatch, tmp_path, mutate,
) -> None:
    """The tracker-cleared HTTP retry seam authenticates the complete result.

    The admission/assessment evidence is byte-identical, so only the complete
    normalized persisted-completion comparison can refuse these changed fields.
    """
    org = daemon_state.orgs["alpha"]
    capture = _CaptureCompletionTraffic(create_app(daemon_state))
    with _LoopbackServer(capture):
        task_id, captured = _launch_v2_manager_and_complete(
            org, monkeypatch, tmp_path, session_id=f"sess-c2-field-{abs(hash(mutate))}",
            evaluation_factory=_valid_v2_evaluation,
        )
        result_rows = org.db.get_task_results(task_id)
        audit_rows = org.db.list_authority_policy_v2_result_stage_audits(
            root_task_id=task_id, manager_agent=MANAGER,
        )
        import json as _json
        from pathlib import Path

        payload = _json.loads(Path(captured["file"]).read_text())
        mutate(payload)
        Path(captured["file"]).write_text(_json.dumps(payload))
        with pytest.raises(SystemExit):
            _run_shipping_completion_cli(captured["file"])
    assert capture.records[-1]["status"] == 409
    assert len(org.db.get_task_results(task_id)) == len(result_rows) == 1
    assert len(org.db.list_authority_policy_v2_result_stage_audits(
        root_task_id=task_id, manager_agent=MANAGER,
    )) == len(audit_rows) == 1
