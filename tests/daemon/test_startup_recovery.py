from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from runtime.config import Settings
from runtime.daemon.__main__ import _sweep_on_startup
from runtime.daemon.queue import TaskQueue
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, TaskRecord, TaskStatus, ThreadInvocationPurpose, ThreadRecord, ThreadStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir


def _seed_manager_recovery_result(tmp_path, *, self_evaluation="valid"):
    from runtime.orchestrator.active_authority_policy import (
        SELF_EVALUATION_CONTRACT_DIGEST,
        SELF_EVALUATION_CONTRACT_ID,
        SELF_EVALUATION_CONTRACT_VERSION,
        ActivePolicySnapshot,
        persist_session_policy_binding,
    )
    from runtime.orchestrator.authority_policy import CONTINUE_ROUTINE_PHRASE
    from runtime.orchestrator.teams import TeamManager
    from tests.authority_policy_test_factory import activate_test_policy

    db, orch, queue = _seed_org_with_orch(tmp_path)
    orch._teams._teams["engineering"] = TeamManager(
        name="engineering_manager", team="engineering", workers=("dev_agent",),
    )
    task_id = "TASK-ORPH-SELF-EVAL"
    session_id = "sess-orph-self-eval"
    manager = "engineering_manager"
    db.insert_thread(ThreadRecord(id="THR-RECOVERY", subject="recovery"))
    db.insert_task(TaskRecord(
        id=task_id, brief="x", team="engineering", assigned_agent=manager,
        status=TaskStatus.IN_PROGRESS, task_type="task",
        dispatched_from_thread_id="THR-RECOVERY",
    ))
    from runtime.infrastructure.audit_logger import AuditLogger
    AuditLogger(db).log_thread_dispatch(
        "THR-RECOVERY", task_id=task_id, dispatcher=manager,
        target_agent=manager, team="engineering",
    )
    db.update_task(
        task_id, current_session_id=session_id, orchestration_step_count=1,
    )
    release, activation = activate_test_policy(db)
    persist_session_policy_binding(
        db=db, task_id=task_id, session_id=session_id, agent_name=manager,
        snapshot=ActivePolicySnapshot(release, activation),
        provider_id="openai", executor_kind="codex", model_id="gpt-5",
    )
    evaluation = {
        "contract_id": SELF_EVALUATION_CONTRACT_ID,
        "contract_version": SELF_EVALUATION_CONTRACT_VERSION,
        "contract_digest": SELF_EVALUATION_CONTRACT_DIGEST,
        "root_task_id": task_id, "manager_session_id": session_id,
        "release_id": release.id, "policy_version": str(release.version),
        "policy_digest": release.policy_digest,
        "activation_id": activation.id, "activation_epoch": activation.epoch,
        "provider_id": "openai", "executor_kind": "codex", "model_id": "gpt-5",
        "disposition": "continue_same_root",
        "clause_id": "cont-routine-same-root", "action": "continue_same_root",
        "confidence": 1.0, "uncertainty_codes": [],
    }
    if self_evaluation == "mismatch":
        evaluation["manager_session_id"] = "sess-stale"
    decision = {"action": "escalate", "reason": CONTINUE_ROUTINE_PHRASE}
    if self_evaluation == "valid" or self_evaluation == "mismatch":
        decision["_manager_self_evaluation"] = evaluation
    elif self_evaluation == "malformed":
        decision["_manager_self_evaluation"] = {"unexpected": True}
    db.insert_task_result(
        task_id=task_id, agent=manager, session_id=session_id,
        status="completed", confidence_score=90, output_summary="escalate",
        decision_json=json.dumps(decision),
    )
    row = db.get_latest_task_result(task_id, manager, session_id)
    assert row is not None and row["task_id"] == task_id
    return db, orch, queue, task_id, row


def _assert_authority_denominator(db, task_id, *, continued):
    candidates = db.list_authority_candidates_for_root(task_id)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.causal_event_id == f"result:{db.get_latest_task_result(task_id, 'engineering_manager', 'sess-orph-self-eval')['id']}"
    evaluation = db.get_authority_evaluation(candidate.id)
    assert evaluation is not None
    assert evaluation.disposition.value == (
        "continue_same_root" if continued else "escalate"
    )
    outcomes = [
        row for row in db.get_audit_logs(task_id)
        if row["action"] == "authority_hook"
    ]
    assert len(outcomes) == 1
    assert outcomes[0]["payload"]["outcome"] == (
        "continued_same_root" if continued else "escalated"
    )
    if not continued:
        assert outcomes[0]["payload"]["error"]
    return candidate


def test_sweep_orphaned_result_runs_real_authority_path(tmp_path):
    db, orch, queue, task_id, row = _seed_manager_recovery_result(tmp_path)

    _sweep_on_startup(db, queue, "test", orch)

    assert db.get_task(task_id).status is TaskStatus.PENDING
    assert queue._queue.get_nowait() == ("test", task_id, None)
    candidate = _assert_authority_denominator(db, task_id, continued=True)
    assert candidate.lifecycle_state.value == "consumed"
    assert [event.event_type for event in db.list_authority_audit(candidate.id)] == [
        "candidate_claimed", "evaluation_recorded", "candidate_consumed",
    ]
    assert row["id"] == db.get_latest_task_result(
        task_id, "engineering_manager", "sess-orph-self-eval"
    )["id"]
    assert not db.list_thread_messages("THR-RECOVERY")


def test_accepted_recovery_continue_settles_exact_receipt_once_across_restart(tmp_path):
    """A real accepted root recovery continues once and settles its own row."""
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery

    db, orch, queue, task_id, original = _seed_manager_recovery_result(tmp_path)
    db.execute("DELETE FROM task_results WHERE id=?", (original["id"],))
    db._conn.commit()
    db.update_task(task_id, current_session_id="origin-manager")
    assert db.claim_task_completion_recovery(
        task_id=task_id, agent="engineering_manager", origin_session_id="origin-manager",
        recovery_session_id=original["session_id"], provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00",
    )
    db.update_task(task_id, current_session_id=original["session_id"])
    assert db.admit_task_completion_callback(
        task_id=task_id, agent="engineering_manager", session_id=original["session_id"],
        status="completed", output_summary="escalate", confidence_score=90,
        decision_json=original["decision_json"],
    )
    accepted = db.get_accepted_task_completion_recovery_result(
        task_id=task_id, agent="engineering_manager",
    )
    assert accepted is not None
    # An unrelated newer result never replaces the ledger-selected causal row.
    db.insert_task_result(task_id=task_id, agent="other", session_id="unrelated",
                          status="completed", output_summary="unrelated", confidence_score=1)
    report = completion_report_from_result_row(task_id, accepted, fallback_agent="engineering_manager")
    _consume_accepted_completion_recovery(
        orch, task_id, report, agent="engineering_manager",
        session_id=original["session_id"], result_row_id=accepted["id"],
    )
    assert db.get_task(task_id).status is TaskStatus.PENDING
    assert db.execute("SELECT state FROM task_completion_recoveries WHERE task_id=?", (task_id,)).fetchone()["state"] == "callback_consumed"
    path = db.path
    db.close()
    reopened = Database(path)
    orch._db, orch._audit = reopened, AuditLogger(reopened)
    _sweep_on_startup(reopened, queue, "test", orch)
    _sweep_on_startup(reopened, queue, "test", orch)
    assert reopened.get_task(task_id).status is TaskStatus.PENDING
    assert len(reopened.list_authority_candidates_for_root(task_id)) == 1
    assert [row["action"] for row in reopened.get_audit_logs(task_id)].count("completion_report") == 1
    assert reopened.execute("SELECT state FROM task_completion_recoveries WHERE task_id=?", (task_id,)).fetchone()["state"] == "callback_consumed"


def _accepted_root_escalation_recovery(tmp_path):
    """Admit a real root callback whose authority hook must escalate."""
    from runtime.orchestrator.authority import StrictFakeAuthorityEvaluator
    from runtime.orchestrator.authority_policy import ACTION_ESCALATE_TO_FOUNDER

    db, orch, queue, task_id, _ = _seed_manager_recovery_result(tmp_path)
    orch._authority_evaluator = StrictFakeAuthorityEvaluator(pinned={
        "needs founder": (
            "escalate", "esc-ambiguity-novelty", ACTION_ESCALATE_TO_FOUNDER,
        ),
    })
    db.update_task(task_id, current_session_id="origin-manager")
    assert db.claim_task_completion_recovery(
        task_id=task_id, agent="engineering_manager",
        origin_session_id="origin-manager", recovery_session_id="recovery-manager",
        provider_session_id="provider", claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2999-01-01T00:02:00+00:00",
    )
    db.update_task(task_id, current_session_id="recovery-manager")
    assert db.admit_task_completion_callback(
        task_id=task_id, agent="engineering_manager", session_id="recovery-manager",
        status="completed", output_summary="escalate", confidence_score=100,
        decision_json='{"action":"escalate","reason":"needs founder"}',
    )
    accepted = db.get_accepted_task_completion_recovery_result(
        task_id=task_id, agent="engineering_manager",
    )
    assert accepted is not None
    return db, orch, queue, task_id, accepted


@pytest.mark.parametrize("winner_kind", ["cancelled", "new_session", "new_agent"])
def test_accepted_recovery_root_escalation_final_owner_fence(tmp_path, winner_kind):
    """A stale root recovery reaches real authority but cannot commit its CAS."""
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator import run_step
    from runtime.daemon.event_bus import EventBus
    from runtime.daemon.routes.tasks import CancelBody, cancel_task
    from runtime.daemon.sessions import SessionTracker
    from runtime.infrastructure.audit_logger import AuditLogger

    db, orch, queue, task_id, accepted = _accepted_root_escalation_recovery(tmp_path)
    report = completion_report_from_result_row(
        task_id, accepted, fallback_agent="engineering_manager",
    )
    accepted_before = dict(db.execute(
        "SELECT * FROM task_results WHERE id=?", (accepted["id"],),
    ).fetchone())
    original = db.try_escalate
    entries = 0
    tracker = SessionTracker()
    tracker.register_recovery_session(task_id, "engineering_manager", "recovery-manager")

    def winner_before_shipping_cas(*args, **kwargs):
        nonlocal entries
        entries += 1
        if winner_kind == "cancelled":
            org = SimpleNamespace(
                db=db, db_lock=asyncio.Lock(), orchestrator=orch, sessions=tracker,
                event_bus=EventBus(lambda _task_id: []),
            )
            asyncio.run(cancel_task(task_id, CancelBody(cascade=False), org))
        elif winner_kind == "new_session":
            db.update_task(
                task_id, current_session_id="new-owner", executor_pid=os.getpid(),
            )
        else:
            db.update_task(task_id, assigned_agent="dev_agent", executor_pid=os.getpid())
        return original(*args, **kwargs)

    with mock.patch.object(db, "try_escalate", side_effect=winner_before_shipping_cas):
        run_step._consume_accepted_completion_recovery(
            orch, task_id, report, agent="engineering_manager",
            session_id="recovery-manager", result_row_id=accepted["id"],
        )

    assert entries == 1
    winning_task = db.get_task(task_id)
    assert winning_task is not None
    if winner_kind == "cancelled":
        assert winning_task.status is TaskStatus.CANCELLED
    else:
        assert winning_task.status is TaskStatus.IN_PROGRESS
        assert winning_task.current_session_id == (
            "new-owner" if winner_kind == "new_session" else "recovery-manager"
        )
        assert winning_task.assigned_agent == (
            "dev_agent" if winner_kind == "new_agent" else "engineering_manager"
        )
    assert db.execute(
        "SELECT state FROM task_completion_recoveries WHERE task_id=?", (task_id,),
    ).fetchone()["state"] == "callback_accepted"
    assert dict(db.execute("SELECT * FROM task_results WHERE id=?", (accepted["id"],)).fetchone()) == accepted_before
    assert not [
        row for row in db.get_audit_logs(task_id)
        if row["action"] in {"completion_report", "escalation"}
    ]
    assert not db.list_thread_messages("THR-RECOVERY")

    winner_before_restart = dict(db.execute(
        "SELECT * FROM tasks WHERE id=?", (task_id,),
    ).fetchone())
    path = db.path
    db.close()
    reopened = Database(path)
    orch._db, orch._audit = reopened, AuditLogger(reopened)
    _sweep_on_startup(reopened, queue, "test", orch)
    _sweep_on_startup(reopened, queue, "test", orch)
    assert dict(reopened.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()) == winner_before_restart
    assert dict(reopened.execute("SELECT * FROM task_results WHERE id=?", (accepted["id"],)).fetchone()) == accepted_before
    assert reopened.execute(
        "SELECT state FROM task_completion_recoveries WHERE task_id=?", (task_id,),
    ).fetchone()["state"] == "callback_accepted"
    assert not [
        row for row in reopened.get_audit_logs(task_id)
        if row["action"] in {"completion_report", "escalation"}
    ]
    assert not reopened.list_thread_messages("THR-RECOVERY")


def test_accepted_recovery_root_escalation_positive_control_uses_real_authority(tmp_path):
    """The unchanged owner takes the real authority, receipt, and thread path."""
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery

    db, orch, _queue, task_id, accepted = _accepted_root_escalation_recovery(tmp_path)
    _consume_accepted_completion_recovery(
        orch, task_id,
        completion_report_from_result_row(task_id, accepted, fallback_agent="engineering_manager"),
        agent="engineering_manager", session_id="recovery-manager",
        result_row_id=accepted["id"],
    )
    assert db.get_task(task_id).status is TaskStatus.ESCALATED
    ledger = db.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?", (task_id,),
    ).fetchone()
    assert ledger["state"] == "callback_consumed"
    assert ledger["accepted_result_id"] == accepted["id"]
    actions = [row["action"] for row in db.get_audit_logs(task_id)]
    assert actions.count("completion_report") == 1
    assert actions.count("escalation") == 1
    assert len(db.list_authority_candidates_for_root(task_id)) == 1
    assert db.list_thread_messages("THR-RECOVERY")


def test_nonroot_manager_recovery_escalation_is_parent_routed_and_consumed(tmp_path):
    """A parent-linked manager result has no root authority/escalation effects."""
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery
    from runtime.infrastructure.audit_logger import AuditLogger

    db, orch, queue, task_id, _ = _seed_manager_recovery_result(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-PARENT", brief="parent", status=TaskStatus.IN_PROGRESS,
        task_type="task",
    ))
    db.execute("UPDATE tasks SET parent_task_id=? WHERE id=?", ("TASK-PARENT", task_id))
    db.commit()
    db.update_task(task_id, current_session_id="origin-manager")
    assert db.claim_task_completion_recovery(
        task_id=task_id, agent="engineering_manager", origin_session_id="origin-manager",
        recovery_session_id="recovery-manager", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00",
    )
    db.update_task(task_id, current_session_id="recovery-manager")
    assert db.admit_task_completion_callback(
        task_id=task_id, agent="engineering_manager", session_id="recovery-manager",
        status="completed", output_summary="escalate", confidence_score=100,
        decision_json='{"action":"escalate","reason":"needs parent"}',
    )
    accepted = db.get_accepted_task_completion_recovery_result(
        task_id=task_id, agent="engineering_manager",
    )
    assert accepted is not None
    with mock.patch("runtime.orchestrator.authority.run_authority_hook") as authority:
        _consume_accepted_completion_recovery(
            orch, task_id,
            completion_report_from_result_row(task_id, accepted, fallback_agent="engineering_manager"),
            agent="engineering_manager", session_id="recovery-manager",
            result_row_id=accepted["id"],
        )
    assert db.get_task(task_id).status is TaskStatus.FAILED
    assert db.get_task("TASK-PARENT").status is TaskStatus.IN_PROGRESS
    assert authority.call_count == 0
    assert len(db.list_authority_candidates_for_root(task_id)) == 0
    actions = [row["action"] for row in db.get_audit_logs(task_id)]
    assert actions.count("completion_report") == 1
    assert "escalation" not in actions
    ledger = db.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?", (task_id,),
    ).fetchone()
    assert ledger["state"] == "callback_consumed"
    assert ledger["accepted_result_id"] == accepted["id"]
    assert queue._queue.empty()


def test_nonroot_manager_recovery_postcommit_cleanup_reenters_on_restart(tmp_path):
    """A crash after non-root commit replays only its owned cleanup and wake."""
    import threading

    from runtime.daemon import jobs_runner
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery

    db, orch, queue, task_id, _ = _seed_manager_recovery_result(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-PARENT", brief="parent", team="engineering",
        status=TaskStatus.IN_PROGRESS, task_type="task", block_kind=BlockKind.DELEGATED,
    ))
    db.execute("UPDATE tasks SET parent_task_id=? WHERE id=?", ("TASK-PARENT", task_id))
    db.commit()
    db.insert_task(TaskRecord(
        id="TASK-OTHER-PARENT", brief="other parent", team="engineering",
        status=TaskStatus.IN_PROGRESS, task_type="task", block_kind=BlockKind.DELEGATED,
    ))
    db.insert_task(TaskRecord(
        id="TASK-OTHER", brief="other", team="engineering", task_type="subtask",
        parent_task_id="TASK-OTHER-PARENT", status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.BLOCKED_ON_JOB,
    ))
    db.update_task(task_id, current_session_id="origin-manager")
    assert db.claim_task_completion_recovery(
        task_id=task_id, agent="engineering_manager",
        origin_session_id="origin-manager", recovery_session_id="recovery-manager",
        provider_session_id="provider", claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2999-01-01T00:02:00+00:00",
    )
    db.update_task(task_id, current_session_id="recovery-manager")
    _seed_job(db, "JOB-OWNED", task_id, status="running")
    _seed_job(db, "JOB-OTHER", "TASK-OTHER", status="running")
    db.update_task("TASK-OTHER", blocked_on_job_ids='["JOB-OTHER"]')
    assert db.admit_task_completion_callback(
        task_id=task_id, agent="engineering_manager",
        session_id="recovery-manager", status="completed", output_summary="escalate",
        confidence_score=100, decision_json='{"action":"escalate","reason":"parent"}',
    )
    accepted = db.get_accepted_task_completion_recovery_result(
        task_id=task_id, agent="engineering_manager",
    )
    assert accepted is not None
    report = completion_report_from_result_row(
        task_id, accepted, fallback_agent="engineering_manager",
    )
    # The consumed marker is durable before terminal effects.  Interrupt at
    # the actual capture-and-settle boundary, rather than at the async
    # dispatcher which is necessarily after the owned row was settled.
    with mock.patch.object(
        db, "settle_consumed_task_completion_recovery_jobs",
        side_effect=RuntimeError("before capture-and-settle"),
    ):
        with pytest.raises(RuntimeError, match="before capture-and-settle"):
            _consume_accepted_completion_recovery(
                orch, task_id, report, agent="engineering_manager",
                session_id="recovery-manager", result_row_id=accepted["id"],
            )
    durable = db.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?", (task_id,),
    ).fetchone()
    assert durable["state"] == "callback_consumed"
    assert durable["accepted_result_id"] == accepted["id"]
    assert db.get_job("JOB-OWNED").status.value == "running"
    db_path = db.path
    db.close()
    reopened = Database(db_path)
    orch._db = reopened
    orch._audit = AuditLogger(reopened)
    with mock.patch("runtime.orchestrator.authority.run_authority_hook") as authority:
        _sweep_on_startup(reopened, queue, "test", orch)
        # The recovery-owned durable backstop commits inside the startup sweep;
        # do not mistake the asynchronous terminator return for that boundary.
        assert reopened.get_job("JOB-OWNED").reason == "task_ended"
        assert reopened.get_job("JOB-OTHER").status.value == "running"
        # Startup cleanup precedes the lifespan orphan-job reconciliation.
        assert reopened.recover_orphaned_running_jobs(
            now_iso="2026-01-01T00:02:00+00:00",
        ) == ["JOB-OTHER"]
        _sweep_on_startup(reopened, queue, "test", orch)
    assert authority.call_count == 0
    assert reopened.get_task(task_id).status is TaskStatus.FAILED
    assert reopened.get_job("JOB-OWNED").reason == "task_ended"
    assert reopened.get_job("JOB-OTHER").reason == "daemon_crash"
    assert reopened.get_task("TASK-PARENT").status is TaskStatus.IN_PROGRESS
    assert reopened.get_task("TASK-OTHER-PARENT").status is TaskStatus.IN_PROGRESS
    assert len(reopened.list_authority_candidates_for_root(task_id)) == 0
    actions = [row["action"] for row in reopened.get_audit_logs(task_id)]
    assert actions.count("completion_report") == 1
    assert "escalation" not in actions
    ledger = reopened.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?", (task_id,),
    ).fetchone()
    assert ledger["state"] == "callback_consumed"
    assert ledger["accepted_result_id"] == accepted["id"]
    assert queue._queue.get_nowait() == ("test", "TASK-PARENT", None)
    assert queue._queue.get_nowait() == ("test", "TASK-OTHER", None)
    assert queue._queue.empty()


@pytest.mark.parametrize("point,trigger", [
    ("failed_task", "BEFORE UPDATE OF status ON tasks"),
    ("ledger_before", "BEFORE UPDATE OF state ON task_completion_recoveries"),
    ("ledger_after", "AFTER UPDATE OF state ON task_completion_recoveries"),
])
def test_nonroot_manager_recovery_transaction_abort_rolls_back_then_startup_settles(
    tmp_path, point, trigger,
):
    """Each real final-transaction write aborts without a partial receipt."""
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery

    db, orch, queue, task_id, _ = _seed_manager_recovery_result(tmp_path)
    db.insert_task(TaskRecord(id="TASK-PARENT", brief="parent", team="engineering",
        status=TaskStatus.IN_PROGRESS, task_type="task", block_kind=BlockKind.DELEGATED))
    db.execute("UPDATE tasks SET parent_task_id=? WHERE id=?", ("TASK-PARENT", task_id))
    db.commit()
    db.update_task(task_id, current_session_id="origin-manager")
    assert db.claim_task_completion_recovery(task_id=task_id, agent="engineering_manager",
        origin_session_id="origin-manager", recovery_session_id="recovery-manager",
        provider_session_id="provider", claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2999-01-01T00:02:00+00:00")
    db.update_task(task_id, current_session_id="recovery-manager")
    assert db.admit_task_completion_callback(task_id=task_id, agent="engineering_manager",
        session_id="recovery-manager", status="completed", output_summary="escalate",
        confidence_score=100, decision_json='{"action":"escalate","reason":"parent"}')
    accepted = db.get_accepted_task_completion_recovery_result(task_id=task_id, agent="engineering_manager")
    assert accepted is not None
    trigger_owner = "NEW.id" if point == "failed_task" else "NEW.task_id"
    db.execute(f"CREATE TRIGGER abort_nonroot_{point} {trigger} WHEN {trigger_owner}='{task_id}' BEGIN SELECT RAISE(ABORT, 'injected {point}'); END")
    with pytest.raises(Exception, match=f"injected {point}"):
        _consume_accepted_completion_recovery(orch, task_id,
            completion_report_from_result_row(task_id, accepted, fallback_agent="engineering_manager"),
            agent="engineering_manager", session_id="recovery-manager", result_row_id=accepted["id"])
    assert db.get_task(task_id).status is TaskStatus.IN_PROGRESS
    assert not [r for r in db.get_audit_logs(task_id) if r["action"] == "completion_report"]
    rolled_back = db.execute("SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?", (task_id,)).fetchone()
    assert rolled_back["state"] == "callback_accepted"
    assert rolled_back["accepted_result_id"] == accepted["id"]
    assert db.execute("SELECT id FROM task_results WHERE id=?", (accepted["id"],)).fetchone()["id"] == accepted["id"]
    db.execute(f"DROP TRIGGER abort_nonroot_{point}")
    path = db.path; db.close(); reopened = Database(path); orch._db = reopened; orch._audit = AuditLogger(reopened)
    with mock.patch("runtime.orchestrator.authority.run_authority_hook") as authority:
        _sweep_on_startup(reopened, queue, "test", orch)
        _sweep_on_startup(reopened, queue, "test", orch)
    assert authority.call_count == 0
    assert reopened.get_task(task_id).status is TaskStatus.FAILED
    ledger = reopened.execute("SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?", (task_id,)).fetchone()
    assert ledger["state"] == "callback_consumed" and ledger["accepted_result_id"] == accepted["id"]
    assert [r["action"] for r in reopened.get_audit_logs(task_id)].count("completion_report") == 1
    assert queue._queue.get_nowait() == ("test", "TASK-PARENT", None)
    assert queue._queue.empty()


@pytest.mark.parametrize("winner", ["cancelled", "new_session", "new_agent"])
def test_nonroot_manager_recovery_stale_winner_rejects_at_final_transaction(tmp_path, winner):
    """A winner installed at the real transaction entry defeats stale recovery."""
    import asyncio
    import os
    from types import SimpleNamespace

    from runtime.daemon.event_bus import EventBus
    from runtime.daemon.routes.tasks import CancelBody, cancel_task
    from runtime.daemon.sessions import SessionTracker
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator import run_step

    db, orch, queue, task_id, _ = _seed_manager_recovery_result(tmp_path)
    db.insert_task(TaskRecord(id="TASK-PARENT", brief="parent", status=TaskStatus.IN_PROGRESS,
        task_type="task", block_kind=BlockKind.DELEGATED))
    db.execute("UPDATE tasks SET parent_task_id=? WHERE id=?", ("TASK-PARENT", task_id)); db.commit()
    db.update_task(task_id, current_session_id="origin-manager")
    assert db.claim_task_completion_recovery(task_id=task_id, agent="engineering_manager",
        origin_session_id="origin-manager", recovery_session_id="recovery-manager", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00")
    db.update_task(task_id, current_session_id="recovery-manager")
    assert db.admit_task_completion_callback(task_id=task_id, agent="engineering_manager", session_id="recovery-manager",
        status="completed", output_summary="escalate", confidence_score=100, decision_json='{"action":"escalate","reason":"parent"}')
    accepted = db.get_accepted_task_completion_recovery_result(task_id=task_id, agent="engineering_manager")
    assert accepted is not None
    _seed_job(db, "JOB-OWNED", task_id, status="running")
    tracker = SessionTracker()
    tracker.register_recovery_session(task_id, "engineering_manager", "recovery-manager")
    cleanup_calls: list[str] = []
    original_consume = db.consume_accepted_nonroot_escalation_recovery
    transaction_entries = 0

    def install_winner(**kwargs):
        nonlocal transaction_entries
        transaction_entries += 1
        if winner == "cancelled":
            org = SimpleNamespace(db=db, db_lock=asyncio.Lock(), orchestrator=orch,
                                  sessions=tracker, event_bus=EventBus(lambda _task_id: []))
            asyncio.run(cancel_task(task_id, CancelBody(cascade=False), org))
        elif winner == "new_session":
            db.update_task(task_id, current_session_id="replacement-session", executor_pid=os.getpid())
        else:
            db.update_task(task_id, assigned_agent="dev_agent", executor_pid=os.getpid())
        return original_consume(**kwargs)

    with mock.patch("runtime.orchestrator.run_step._kill_jobs_for_terminating_task", side_effect=lambda _orch, tid: cleanup_calls.append(tid)), mock.patch.object(db, "consume_accepted_nonroot_escalation_recovery", side_effect=install_winner):
        run_step._consume_accepted_completion_recovery(orch, task_id,
            completion_report_from_result_row(task_id, accepted, fallback_agent="engineering_manager"),
            agent="engineering_manager", session_id="recovery-manager", result_row_id=accepted["id"])
    task = db.get_task(task_id)
    assert task is not None
    assert transaction_entries == 1
    assert db.execute("SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?", (task_id,)).fetchone()["state"] == "callback_accepted"
    assert not [r for r in db.get_audit_logs(task_id) if r["action"] == "completion_report"]
    assert db.get_job("JOB-OWNED").status.value == "running"
    if winner == "cancelled":
        assert task.status is TaskStatus.CANCELLED and task.cancelled_at is not None
        # This cleanup belongs to the shipping cancellation route, never recovery.
        assert cleanup_calls == [task_id]
    elif winner == "new_session":
        assert task.status is TaskStatus.IN_PROGRESS
        assert task.assigned_agent == "engineering_manager"
        assert task.current_session_id == "replacement-session"
        assert cleanup_calls == []
    else:
        assert task.status is TaskStatus.IN_PROGRESS
        assert task.assigned_agent == "dev_agent"
        assert task.current_session_id == "recovery-manager"
        assert cleanup_calls == []
    real_enqueue = run_step._enqueue_parent_if_waiting
    parent_calls: list[str] = []
    def observed_enqueue(*args, **kwargs):
        parent_calls.append(args[1])
        return real_enqueue(*args, **kwargs)
    with mock.patch("runtime.orchestrator.run_step._enqueue_parent_if_waiting", side_effect=observed_enqueue):
        _sweep_on_startup(db, queue, "test", orch); _sweep_on_startup(db, queue, "test", orch)
    assert db.get_task(task_id).status is task.status
    assert not [r for r in db.get_audit_logs(task_id) if r["action"] == "completion_report"]
    assert db.get_job("JOB-OWNED").status.value == "running"
    assert task_id not in parent_calls
    if winner == "cancelled":
        assert queue._queue.get_nowait() == ("test", "TASK-PARENT", None)
    # Any parent entry here is generic startup reconciliation, not a stale
    # recovery delivery (the named recovery task never reached that seam).


@pytest.mark.parametrize("winner", ["cancelled", "new_session", "new_agent"])
def test_consumed_nonroot_receipt_selector_excludes_changed_winner(tmp_path, winner):
    """A real consumed receipt selects once, then rejects exactly one changed owner predicate."""
    import threading

    from runtime.daemon import jobs_runner
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator import run_step

    db, orch, queue, task_id, _ = _seed_manager_recovery_result(tmp_path)
    db.insert_task(TaskRecord(id="TASK-PARENT", brief="parent", status=TaskStatus.IN_PROGRESS,
        task_type="task", block_kind=BlockKind.DELEGATED))
    db.execute("UPDATE tasks SET parent_task_id=? WHERE id=?", ("TASK-PARENT", task_id)); db.commit()
    db.update_task(task_id, current_session_id="origin-manager")
    assert db.claim_task_completion_recovery(task_id=task_id, agent="engineering_manager",
        origin_session_id="origin-manager", recovery_session_id="recovery-manager", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00")
    db.update_task(task_id, current_session_id="recovery-manager")
    _seed_job(db, "JOB-OWNED", task_id, status="running")
    assert db.admit_task_completion_callback(task_id=task_id, agent="engineering_manager", session_id="recovery-manager",
        status="completed", output_summary="escalate", confidence_score=100, decision_json='{"action":"escalate","reason":"parent"}')
    accepted = db.get_accepted_task_completion_recovery_result(task_id=task_id, agent="engineering_manager")
    assert accepted is not None
    cleanup_done = threading.Event()
    worker_errors: list[BaseException] = []
    workers = []
    real_terminate = jobs_runner.terminate_jobs_for_task
    real_thread = threading.Thread

    async def observed_terminate(*args, **kwargs):
        try:
            return await real_terminate(*args, **kwargs)
        finally:
            cleanup_done.set()

    def tracked_thread(*, target, daemon):
        worker = real_thread(
            target=lambda: _capture_worker_error(target, worker_errors), daemon=daemon,
        )
        workers.append(worker)
        return worker

    with mock.patch.object(jobs_runner, "terminate_jobs_for_task", observed_terminate), mock.patch.object(
        threading, "Thread", tracked_thread,
    ):
        run_step._consume_accepted_completion_recovery(orch, task_id,
            completion_report_from_result_row(task_id, accepted, fallback_agent="engineering_manager"),
            agent="engineering_manager", session_id="recovery-manager", result_row_id=accepted["id"])
        assert cleanup_done.wait(2), "positive consumed-receipt cleanup did not finish"
    for worker in workers:
        worker.join(2)
        assert not worker.is_alive()
    assert not worker_errors
    assert db.get_consumed_nonroot_escalation_recovery_task_ids() == [task_id]
    accepted_before = dict(accepted)
    # The positive consume legitimately wakes the waiting parent.  Empty it
    # before changing one selector predicate so later delivery is attributable.
    assert queue._queue.get_nowait() == ("test", "TASK-PARENT", None)
    assert queue._queue.empty()
    assert db.get_job("JOB-OWNED").reason == "task_ended"
    cleanup_calls: list[str] = []
    if winner == "cancelled":
        # It is already terminal: change only the cancellation metadata while
        # retaining FAILED/type/parent and every other selector predicate.
        db.update_task(task_id, cancelled_at="2026-01-01T00:03:00+00:00")
    elif winner == "new_session":
        db.update_task(task_id, current_session_id="new-session")
    else:
        db.update_task(task_id, assigned_agent="new-agent")
    _seed_job(db, "JOB-WINNER", task_id, status="running")
    assert db.get_consumed_nonroot_escalation_recovery_task_ids() == []
    real_enqueue = run_step._enqueue_parent_if_waiting
    parent_calls: list[str] = []
    def observed_enqueue(*args, **kwargs):
        parent_calls.append(args[1])
        return real_enqueue(*args, **kwargs)
    with mock.patch("runtime.orchestrator.run_step._kill_jobs_for_terminating_task", side_effect=lambda _orch, tid: cleanup_calls.append(tid)), mock.patch("runtime.orchestrator.run_step._enqueue_parent_if_waiting", side_effect=observed_enqueue):
        _sweep_on_startup(db, queue, "test", orch); _sweep_on_startup(db, queue, "test", orch)
    assert cleanup_calls == []
    assert task_id not in parent_calls
    assert db.get_job("JOB-OWNED").reason == "task_ended"
    assert db.get_job("JOB-WINNER").status.value == "running"
    assert dict(db.execute("SELECT * FROM task_results WHERE id=?", (accepted["id"],)).fetchone()) == accepted_before
@pytest.mark.parametrize("failure_point", ["before_effect", "after_effect_before_ledger"])
def test_root_recovery_escalation_transaction_rolls_back_then_restart_settles_once(
    tmp_path, failure_point,
):
    """The real root authority hook replays one accepted result after either crash.

    The unrelated result and older same-step audit make this a causal-result
    proof: startup must consume only the accepted immutable result, never a
    latest task result or a task+step-shaped audit receipt.
    """
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.orchestrator.authority import StrictFakeAuthorityEvaluator
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery

    db, orch, queue, task_id, _ = _seed_manager_recovery_result(tmp_path)
    orch._authority_evaluator = StrictFakeAuthorityEvaluator()
    db.update_task(task_id, current_session_id="origin-manager")
    assert db.claim_task_completion_recovery(
        task_id=task_id, agent="engineering_manager", origin_session_id="origin-manager",
        recovery_session_id="recovery-manager", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00",
    )
    db.update_task(task_id, current_session_id="recovery-manager")
    assert db.admit_task_completion_callback(
        task_id=task_id, agent="engineering_manager", session_id="recovery-manager",
        status="completed", output_summary="escalate", confidence_score=100,
        decision_json='{"action":"escalate","reason":"needs founder"}',
    )
    accepted = db.get_accepted_task_completion_recovery_result(
        task_id=task_id, agent="engineering_manager",
    )
    assert accepted is not None
    # A newer unrelated row is deliberately present but must never be selected.
    db.insert_task_result(
        task_id="TASK-UNRELATED", agent="engineering_manager", session_id="other",
        status="completed", confidence_score=1, output_summary="unrelated",
    )
    db.insert_audit_log(task_id, "engineering_manager", "orchestration_step", {
        "step": 0, "result_row_id": accepted["id"] - 1,
    })
    report = completion_report_from_result_row(
        task_id, accepted, fallback_agent="engineering_manager",
    )
    real_try_escalate = db.try_escalate

    def crash_at_transaction_boundary(*args, **kwargs):
        kwargs["recovery_fault_hook"] = lambda point: (
            (_ for _ in ()).throw(RuntimeError(f"injected {point}"))
            if point == failure_point else None
        )
        return real_try_escalate(*args, **kwargs)

    with mock.patch.object(db, "try_escalate", side_effect=crash_at_transaction_boundary):
        with pytest.raises(RuntimeError, match=failure_point):
            _consume_accepted_completion_recovery(
                orch, task_id, report, agent="engineering_manager",
                session_id="recovery-manager", result_row_id=accepted["id"],
            )

    # The newly introduced BEGIN IMMEDIATE transaction rolls back the final
    # effect and marker together, at both injection points.
    assert db.get_task(task_id).status is TaskStatus.IN_PROGRESS
    ledger = db.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?",
        (task_id,),
    ).fetchone()
    assert ledger["state"] == "callback_accepted"
    assert ledger["accepted_result_id"] == accepted["id"]
    assert not any(row["action"] == "escalation" for row in db.get_audit_logs(task_id))

    db_path = db.path
    db.close()
    reopened = Database(db_path)
    orch._db = reopened
    orch._audit = AuditLogger(reopened)
    _sweep_on_startup(reopened, queue, "test", orch)
    _sweep_on_startup(reopened, queue, "test", orch)

    settled = reopened.get_task(task_id)
    assert settled.status is TaskStatus.ESCALATED
    ledger = reopened.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?",
        (task_id,),
    ).fetchone()
    assert ledger["state"] == "callback_consumed"
    assert ledger["accepted_result_id"] == accepted["id"]
    actions = [row["action"] for row in reopened.get_audit_logs(task_id)]
    assert actions.count("completion_report") == 1
    assert actions.count("orchestration_step") == 2  # one older, one exact recovery
    assert actions.count("escalation") == 1
    assert len(reopened.list_authority_candidates_for_root(task_id)) == 1
    assert reopened.execute("SELECT COUNT(*) FROM authority_evaluations").fetchone()[0] == 1


@pytest.mark.parametrize("self_evaluation", ["absent", "malformed", "mismatch"])
def test_sweep_orphaned_result_invalid_evidence_fails_closed(
    tmp_path, self_evaluation,
):
    db, orch, queue, task_id, _ = _seed_manager_recovery_result(
        tmp_path, self_evaluation=self_evaluation,
    )

    _sweep_on_startup(db, queue, "test", orch)

    assert db.get_task(task_id).status is TaskStatus.ESCALATED
    assert queue._queue.empty()
    _assert_authority_denominator(db, task_id, continued=False)
    messages = db.list_thread_messages("THR-RECOVERY")
    assert len(messages) == 1
    assert messages[0].system_payload["kind_tag"] == "task_escalated"


def test_sweep_orphaned_result_replay_cannot_continue_twice(tmp_path):
    db, orch, queue, task_id, _ = _seed_manager_recovery_result(tmp_path)
    _sweep_on_startup(db, queue, "test", orch)
    assert queue._queue.get_nowait() == ("test", task_id, None)
    db.update_task(task_id, status=TaskStatus.IN_PROGRESS, block_kind=None)

    _sweep_on_startup(db, queue, "test", orch)

    assert db.get_task(task_id).status is TaskStatus.ESCALATED
    assert queue._queue.empty()
    assert len(db.list_authority_candidates_for_root(task_id)) == 1
    assert db.execute("SELECT COUNT(*) FROM authority_evaluations").fetchone()[0] == 1


def test_accepted_manager_done_recovery_reuses_its_step_audit_after_crash(tmp_path):
    """A crash after the manager step audit replays the exact admitted result once."""
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery

    db, orch, queue, task_id, _ = _seed_manager_recovery_result(tmp_path)
    db.update_task(task_id, current_session_id="origin-manager")
    assert db.claim_task_completion_recovery(
        task_id=task_id, agent="engineering_manager", origin_session_id="origin-manager",
        recovery_session_id="recovery-manager", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00",
    )
    db.update_task(task_id, current_session_id="recovery-manager")
    assert db.admit_task_completion_callback(
        task_id=task_id, agent="engineering_manager", session_id="recovery-manager",
        status="completed", output_summary="done", confidence_score=100,
        decision_json='{"action":"done","summary":"done"}',
    )
    accepted = db.get_accepted_task_completion_recovery_result(
        task_id=task_id, agent="engineering_manager",
    )
    assert accepted is not None
    report = completion_report_from_result_row(
        task_id, accepted, fallback_agent="engineering_manager",
    )
    with mock.patch("runtime.orchestrator.run_step._complete", side_effect=RuntimeError("after step audit")):
        with pytest.raises(RuntimeError, match="after step audit"):
            _consume_accepted_completion_recovery(
                orch, task_id, report, agent="engineering_manager",
                session_id="recovery-manager", result_row_id=accepted["id"],
            )
    assert len([r for r in db.get_audit_logs(task_id) if r["action"] == "orchestration_step"]) == 1
    db_path = db.path
    db.close()
    reopened = Database(db_path)
    orch._db = reopened
    from runtime.infrastructure.audit_logger import AuditLogger
    orch._audit = AuditLogger(reopened)
    _sweep_on_startup(reopened, queue, "test", orch)
    _sweep_on_startup(reopened, queue, "test", orch)
    assert reopened.get_task(task_id).status is TaskStatus.COMPLETED
    assert reopened.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?", (task_id,),
    ).fetchone()["state"] == "callback_consumed"
    audits = reopened.get_audit_logs(task_id)
    assert [r["action"] for r in audits].count("completion_report") == 1
    assert [r["action"] for r in audits].count("orchestration_step") == 1


def test_accepted_manager_done_recovery_preserves_replaced_owner_at_complete(tmp_path):
    """The final done effect, not only recovery entry, fences the owner."""
    import runtime.orchestrator.run_step as run_step
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    db, orch, _queue, task_id, _ = _seed_manager_recovery_result(tmp_path)
    db.update_task(task_id, current_session_id="origin-manager")
    assert db.claim_task_completion_recovery(task_id=task_id, agent="engineering_manager", origin_session_id="origin-manager", recovery_session_id="recovery-manager", provider_session_id="provider", claimed_at="2026-01-01T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00")
    db.update_task(task_id, current_session_id="recovery-manager")
    assert db.admit_task_completion_callback(task_id=task_id, agent="engineering_manager", session_id="recovery-manager", status="completed", output_summary="done", confidence_score=100, decision_json='{"action":"done","summary":"done"}')
    accepted = db.get_accepted_task_completion_recovery_result(task_id=task_id, agent="engineering_manager")
    assert accepted is not None
    report = completion_report_from_result_row(task_id, accepted, fallback_agent="engineering_manager")
    original = run_step._complete
    def replace_then_complete(*args, **kwargs):
        db.update_task(task_id, current_session_id="new-owner", session_pid=123456)
        return original(*args, **kwargs)
    with mock.patch.object(run_step, "_complete", side_effect=replace_then_complete):
        run_step._consume_accepted_completion_recovery(orch, task_id, report, agent="engineering_manager", session_id="recovery-manager", result_row_id=accepted["id"])
    assert db.get_task(task_id).status is TaskStatus.IN_PROGRESS
    assert db.get_task(task_id).current_session_id == "new-owner"
    assert db.execute("SELECT state FROM task_completion_recoveries WHERE task_id=?", (task_id,)).fetchone()["state"] == "callback_accepted"


@pytest.mark.parametrize("parent_linked", [False, True], ids=["root", "parent-linked"])
@pytest.mark.parametrize("interrupt_before_marker", [False, True], ids=["after-marker", "before-marker"])
def test_manager_done_postcommit_cleanup_pending_restarts_once(
    tmp_path, parent_linked, interrupt_before_marker,
):
    """Marker-edge manager-DONE crashes replay one owned settlement once.

    Both boundaries precede the receipt-bound handoff, so the owned durable
    job is still running when the old SQLite connection closes. Two shipping
    startup sweeps then consume and settle only that row; no old-process live
    cleanup, provider, or authority work is inherited.
    """
    from runtime.infrastructure.audit_logger import AuditLogger
    from runtime.orchestrator import run_step
    from runtime.orchestrator.orchestrator import completion_report_from_result_row

    db, orch, queue, task_id, _ = _seed_manager_recovery_result(tmp_path)
    if parent_linked:
        db.insert_task(TaskRecord(
            id="TASK-PARENT", brief="waiting parent", team="engineering",
            task_type="task", status=TaskStatus.IN_PROGRESS,
            block_kind=BlockKind.DELEGATED,
        ))
        db.execute("UPDATE tasks SET parent_task_id=? WHERE id=?", ("TASK-PARENT", task_id))
    db.insert_task(TaskRecord(
        id="TASK-UNRELATED", brief="unrelated", status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.BLOCKED_ON_JOB,
    ))
    db.commit()
    db.update_task(task_id, current_session_id="origin-manager")
    assert db.claim_task_completion_recovery(
        task_id=task_id, agent="engineering_manager",
        origin_session_id="origin-manager", recovery_session_id="recovery-manager",
        provider_session_id="provider", claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2999-01-01T00:02:00+00:00",
    )
    db.update_task(task_id, current_session_id="recovery-manager")
    _seed_job(db, "JOB-OWNED", task_id, status="running")
    _seed_job(db, "JOB-UNRELATED", "TASK-UNRELATED", status="running")
    db.update_task("TASK-UNRELATED", blocked_on_job_ids='["JOB-UNRELATED"]')
    assert db.admit_task_completion_callback(
        task_id=task_id, agent="engineering_manager", session_id="recovery-manager",
        status="completed", output_summary="manager done", confidence_score=100,
        decision_json='{"action":"done","summary":"done"}',
    )
    accepted = db.get_accepted_task_completion_recovery_result(
        task_id=task_id, agent="engineering_manager",
    )
    assert accepted is not None
    # This is deliberately later than, and unrelated to, the selected result.
    db.insert_task_result(
        task_id="TASK-UNRELATED", agent="dev_agent", session_id="other-session",
        status="completed", confidence_score=100, output_summary="unrelated later result",
    )
    db.commit()

    restarted_cleanup_finished = threading.Event()
    real_mark = db.mark_task_completion_recovery_callback_consumed

    def mark_then_interrupt(**kwargs):
        # The handoff begins only after this marker returns. Interrupting at
        # either edge is a finite pre-settlement crash seam: no old-process
        # cleanup worker can have written the owned job.
        if interrupt_before_marker:
            raise RuntimeError("interrupt immediately before callback_consumed commit")
        assert real_mark(**kwargs)
        raise RuntimeError("interrupt immediately after callback_consumed commit")

    with mock.patch.object(
        db, "mark_task_completion_recovery_callback_consumed",
        side_effect=mark_then_interrupt,
    ), mock.patch("runtime.orchestrator.authority.run_authority_hook") as authority, mock.patch.object(
        orch, "_run_agent",
    ) as provider_launch, mock.patch(
        "runtime.orchestrator.run_step._maybe_post_thread_followup",
        wraps=run_step._maybe_post_thread_followup,
    ) as followup:
        expected_boundary = (
            "immediately before callback_consumed"
            if interrupt_before_marker else "immediately after callback_consumed"
        )
        with pytest.raises(RuntimeError, match=expected_boundary):
            run_step._consume_accepted_completion_recovery(
                orch, task_id,
                completion_report_from_result_row(
                    task_id, accepted, fallback_agent="engineering_manager",
                ),
                agent="engineering_manager", session_id="recovery-manager",
                result_row_id=accepted["id"],
            )
        ledger = db.execute(
            "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?",
            (task_id,),
        ).fetchone()
        assert db.get_task(task_id).status is TaskStatus.COMPLETED
        assert ledger["state"] == (
            "callback_accepted" if interrupt_before_marker else "callback_consumed"
        )
        assert ledger["accepted_result_id"] == accepted["id"]
        assert db.get_job("JOB-OWNED").status.value == "running"

        accepted_row_before = dict(accepted)
        db_path = db.path
        assert db.get_job("JOB-OWNED").status.value == "running"
        db.close()
        reopened = Database(db_path)
        orch._db = reopened
        orch._audit = AuditLogger(reopened)
        assert reopened.get_job("JOB-OWNED").status.value == "running"
        reopened_backstop = reopened.settle_consumed_task_completion_recovery_jobs
        def observe_whole_restarted_cleanup(*args, **kwargs):
            try:
                return reopened_backstop(*args, **kwargs)
            finally:
                # Runner termination is only the first phase.  Observe the
                # real durable backstop return, including its SQLite commit.
                restarted_cleanup_finished.set()
        reopened.settle_consumed_task_completion_recovery_jobs = observe_whole_restarted_cleanup

        # Bounded negative control: a shipping sweep with its durable backstop
        # disabled leaves the owned job running, proving the assertion below
        # depends on the restart cleanup rather than old-process work.
        with mock.patch.object(
            reopened, "settle_consumed_task_completion_recovery_jobs", return_value=None,
        ), mock.patch(
            "runtime.orchestrator.run_step._kill_jobs_for_terminating_task",
        ), mock.patch("runtime.orchestrator.run_step._enqueue_parent_if_waiting"):
            _sweep_on_startup(reopened, TaskQueue(), "test", orch)
        with pytest.raises(AssertionError):
            assert reopened.get_job("JOB-OWNED").reason == "task_ended"

        _sweep_on_startup(reopened, queue, "test", orch)
        assert restarted_cleanup_finished.wait(2.0), "new-process durable backstop did not finish"
        _sweep_on_startup(reopened, queue, "test", orch)
        # This is before the later lifespan orphan reconciliation; startup
        # touched only the recovery owner's durable row.
        assert reopened.get_job("JOB-OWNED").reason == "task_ended"
        assert reopened.get_job("JOB-UNRELATED").status.value == "running"
        assert reopened.recover_orphaned_running_jobs(
            now_iso="2026-01-01T00:03:00+00:00",
        ) == ["JOB-UNRELATED"]
        assert authority.call_count == 0
        provider_launch.assert_not_called()
        assert followup.call_count == 0
        assert [row["action"] for row in reopened.get_audit_logs(task_id)].count(
            "completion_report",
        ) == 1
        assert [row["action"] for row in reopened.get_audit_logs(task_id)].count(
            "orchestration_step",
        ) == 1
        ledger = reopened.execute(
            "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?",
            (task_id,),
        ).fetchone()
        assert ledger["state"] == "callback_consumed"
        assert ledger["accepted_result_id"] == accepted["id"]
        accepted_row_after = dict(reopened.execute(
            "SELECT * FROM task_results WHERE id=?", (accepted["id"],),
        ).fetchone())
        assert accepted_row_after == accepted_row_before
        if parent_linked:
            assert queue._queue.get_nowait() == ("test", "TASK-PARENT", None)
            assert queue._queue.empty()
        else:
            assert queue._queue.empty()

@pytest.mark.parametrize(
    "winner", ["cancel", "new_session", "new_agent", "completed", "different_result", "not_accepted", "unchanged"],
)
def test_accepted_manager_done_recovery_final_owner_and_receipt_fences(tmp_path, winner):
    """The real final owner CAS rejects every stale accepted-manager result."""
    import asyncio
    import threading
    from types import SimpleNamespace

    import runtime.orchestrator.run_step as run_step
    from runtime.daemon.event_bus import EventBus
    from runtime.daemon import jobs_runner
    from runtime.daemon.routes.tasks import CancelBody, cancel_task
    from runtime.daemon.sessions import SessionTracker
    from runtime.orchestrator.orchestrator import completion_report_from_result_row

    db, orch, queue, task_id, _ = _seed_manager_recovery_result(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-PARENT", brief="parent", team="engineering", task_type="task",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))
    db.execute("UPDATE tasks SET parent_task_id=? WHERE id=?", ("TASK-PARENT", task_id))
    db.commit()
    db.update_task(task_id, current_session_id="origin-manager")
    assert db.claim_task_completion_recovery(task_id=task_id, agent="engineering_manager", origin_session_id="origin-manager", recovery_session_id="recovery-manager", provider_session_id="provider", claimed_at="2026-01-01T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00")
    db.update_task(task_id, current_session_id="recovery-manager")
    _seed_job(db, "JOB-OWNED", task_id, status="running")
    # This belongs to neither the recovery task nor its parent.  Keep it
    # observable across the completed winner's two startup entries: stale
    # recovery must not broaden its cleanup radius.
    db.insert_task(TaskRecord(
        id="TASK-UNRELATED", brief="unrelated", team="engineering",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))
    _seed_job(db, "JOB-UNRELATED", "TASK-UNRELATED", status="running")
    assert db.admit_task_completion_callback(task_id=task_id, agent="engineering_manager", session_id="recovery-manager", status="completed", output_summary="done", confidence_score=100, decision_json='{"action":"done","summary":"done"}')
    accepted = db.get_accepted_task_completion_recovery_result(task_id=task_id, agent="engineering_manager")
    assert accepted is not None
    accepted_result = dict(accepted)
    original_complete = db.complete_task_if_current_recovery_owner
    boundary_calls = 0
    cleanup_calls: list[str] = []
    parent_calls: list[str] = []
    delivery_calls: list[str] = []
    winner_effect_counts: tuple[int, int, int] | None = None

    def install_winner_at_final_cas(**kwargs):
        """Run exactly once immediately before the shipping owner CAS."""
        nonlocal boundary_calls, winner_effect_counts
        boundary_calls += 1
        if winner == "cancel":
            org = SimpleNamespace(
                db=db, db_lock=asyncio.Lock(), orchestrator=orch,
                sessions=SessionTracker(), event_bus=EventBus(lambda _task_id: []),
            )
            asyncio.run(cancel_task(task_id, CancelBody(cascade=False), org))
        elif winner == "new_session":
            db.update_task(task_id, current_session_id="replacement-session")
        elif winner == "new_agent":
            db.update_task(task_id, assigned_agent="dev_agent")
        elif winner == "completed":
            # The final CAS is the only point at which the old recovery can
            # publish effects.  A same-agent, newer-session terminal winner
            # must leave the accepted old result immutable and produce no
            # stale cleanup, parent wake, or thread follow-up on either
            # startup re-entry.
            db.update_task(
                task_id, current_session_id="replacement-session",
                status=TaskStatus.COMPLETED, note="new owner completed",
            )
        elif winner == "different_result":
            replacement = db.insert_task_result(task_id=task_id, agent="engineering_manager", session_id="replacement-session", status="completed", confidence_score=100, output_summary="replacement")
            db.execute("UPDATE task_completion_recoveries SET accepted_result_id=? WHERE task_id=?", (replacement, task_id))
            db._conn.commit()
        elif winner == "not_accepted":
            db.execute("UPDATE task_completion_recoveries SET state='claimed' WHERE task_id=?", (task_id,))
            db._conn.commit()
        winner_effect_counts = (len(cleanup_calls), len(parent_calls), len(delivery_calls))
        return original_complete(**kwargs)

    real_enqueue = run_step._enqueue_parent_if_waiting
    real_delivery = run_step._maybe_post_thread_followup

    def observe_enqueue(*args, **kwargs):
        parent_calls.append(args[1])
        return real_enqueue(*args, **kwargs)

    def observe_delivery(*args, **kwargs):
        delivery_calls.append(args[1])
        return real_delivery(*args, **kwargs)

    cleanup_finished = threading.Event()
    worker_threads: list[threading.Thread] = []
    worker_errors: list[BaseException] = []
    real_thread = threading.Thread
    real_terminate = jobs_runner.terminate_jobs_for_task

    async def observe_terminate(*args, **kwargs):
        result = await real_terminate(*args, **kwargs)
        cleanup_finished.set()
        return result

    real_kill = run_step._kill_jobs_for_terminating_task

    def observe_cleanup(*args, **kwargs):
        cleanup_calls.append(args[1])
        return real_kill(*args, **kwargs)

    def tracked_thread(*args, **kwargs):
        target = kwargs.get("target")
        target_args = kwargs.get("args", ())
        target_kwargs = kwargs.get("kwargs", {})
        if target is None:
            return real_thread(*args, **kwargs)

        def capture_worker_error():
            try:
                target(*target_args, **target_kwargs)
            except BaseException as exc:
                worker_errors.append(exc)

        thread_kwargs = dict(kwargs)
        thread_kwargs["target"] = capture_worker_error
        thread_kwargs.pop("args", None)
        thread_kwargs.pop("kwargs", None)
        worker = real_thread(*args, **thread_kwargs)
        worker_threads.append(worker)
        return worker

    with mock.patch.object(db, "complete_task_if_current_recovery_owner", side_effect=install_winner_at_final_cas), mock.patch("runtime.daemon.jobs_runner.terminate_jobs_for_task", side_effect=observe_terminate), mock.patch("runtime.orchestrator.run_step._kill_jobs_for_terminating_task", side_effect=observe_cleanup), mock.patch("runtime.orchestrator.run_step._enqueue_parent_if_waiting", side_effect=observe_enqueue), mock.patch("runtime.orchestrator.run_step._maybe_post_thread_followup", side_effect=observe_delivery), mock.patch("threading.Thread", side_effect=tracked_thread):
        run_step._consume_accepted_completion_recovery(
            orch, task_id,
            completion_report_from_result_row(task_id, accepted, fallback_agent="engineering_manager"),
            agent="engineering_manager", session_id="recovery-manager", result_row_id=accepted["id"],
        )
        for worker in worker_threads:
            worker.join(2.0)
            assert not worker.is_alive(), "recovery handoff thread did not join"
    assert not worker_errors
    task = db.get_task(task_id)
    assert task is not None
    assert boundary_calls == 1
    ledger = db.execute("SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?", (task_id,)).fetchone()
    preserved_result = db.execute(
        "SELECT id, task_id, agent, session_id, status, confidence_score, output_summary, decision_json "
        "FROM task_results WHERE id=?", (accepted["id"],),
    ).fetchone()
    assert dict(preserved_result) == {
        key: accepted_result[key]
        for key in ("id", "task_id", "agent", "session_id", "status", "confidence_score", "output_summary", "decision_json")
    }
    if winner == "unchanged":
        assert task.status is TaskStatus.COMPLETED
        assert ledger["state"] == "callback_consumed"
        assert ledger["accepted_result_id"] == accepted["id"]
        # Settlement captures the owned job once and the asynchronous handoff
        # performs the one fixed-identity runner cleanup before parent wake.
        assert cleanup_calls == [task_id]
        assert cleanup_finished.wait(2.0), "owned done cleanup did not finish"
        assert db.get_job("JOB-OWNED").reason == "task_ended"
        assert parent_calls == [task_id]
        assert delivery_calls == [task_id]
        assert queue._queue.get_nowait() == ("test", "TASK-PARENT", None)
        assert queue._queue.empty()
    else:
        assert ledger["state"] != "callback_consumed"
        assert task.status is (
            TaskStatus.CANCELLED if winner == "cancel"
            else TaskStatus.COMPLETED if winner == "completed"
            else TaskStatus.IN_PROGRESS
        )
        # A shipping cancellation has its own observed effects.  The stale
        # recovery contributes no cleanup, parent wake, or thread delivery.
        assert winner_effect_counts is not None
        assert (len(cleanup_calls), len(parent_calls), len(delivery_calls)) == winner_effect_counts
        assert queue._queue.empty()
        if winner == "cancel":
            assert task.cancelled_at is not None
            # The cancellation route has its own cleanup; recovery added none.
            assert cleanup_calls == [task_id]
            assert cleanup_finished.wait(2.0), "cancel cleanup did not finish"
            assert db.get_job("JOB-OWNED").reason == "task_ended"
        else:
            assert cleanup_calls == []
            assert db.get_job("JOB-OWNED").status.value == "running"
            if winner == "new_session":
                assert task.assigned_agent == "engineering_manager"
                assert task.current_session_id == "replacement-session"
            elif winner == "completed":
                assert task.assigned_agent == "engineering_manager"
                assert task.current_session_id == "replacement-session"
                winner_row = task.model_dump()
                # Keep the actual observers installed across both reentries:
                # injected stale recovery effects must not escape the test.
                with mock.patch("runtime.daemon.jobs_runner.terminate_jobs_for_task", side_effect=observe_terminate), mock.patch("runtime.orchestrator.run_step._kill_jobs_for_terminating_task", side_effect=observe_cleanup), mock.patch("runtime.orchestrator.run_step._enqueue_parent_if_waiting", side_effect=observe_enqueue), mock.patch("runtime.orchestrator.run_step._maybe_post_thread_followup", side_effect=observe_delivery):
                    _sweep_on_startup(db, queue, "test", orch)
                    _sweep_on_startup(db, queue, "test", orch)
                assert db.get_task(task_id).model_dump() == winner_row
                assert dict(db.execute(
                    "SELECT * FROM task_results WHERE id=?", (accepted["id"],),
                ).fetchone()) == accepted_result
                ledger_after_entries = db.execute(
                    "SELECT state, accepted_result_id FROM task_completion_recoveries "
                    "WHERE task_id=?", (task_id,),
                ).fetchone()
                assert dict(ledger_after_entries) == {
                    "state": "callback_accepted", "accepted_result_id": accepted["id"],
                }
                assert db.get_job("JOB-OWNED").status.value == "running"
                assert db.get_job("JOB-UNRELATED").status.value == "running"
                assert cleanup_calls == []
                assert parent_calls == []
                assert delivery_calls == []
            elif winner == "new_agent":
                assert task.assigned_agent == "dev_agent"
                assert task.current_session_id == "recovery-manager"
            elif winner == "different_result":
                assert ledger["accepted_result_id"] != accepted["id"]
            else:
                assert ledger["state"] == "claimed"


@pytest.mark.parametrize("agent", ["dev_agent", "engineering_manager"])
def test_sweep_orphaned_result_worker_and_legacy_compatibility(tmp_path, agent):
    db, orch, queue = _seed_org_with_orch(tmp_path)
    task_id = f"TASK-COMPAT-{agent}"
    session_id = f"sess-{agent}"
    db.insert_task(TaskRecord(
        id=task_id, brief="x", team="engineering", assigned_agent=agent,
        status=TaskStatus.IN_PROGRESS, task_type="subtask",
    ))
    db.update_task(task_id, current_session_id=session_id)
    db.insert_task_result(
        task_id=task_id, agent=agent, session_id=session_id,
        status="completed", confidence_score=90, output_summary="done",
    )

    _sweep_on_startup(db, queue, "test", orch)

    assert db.get_task(task_id).status is TaskStatus.COMPLETED
    assert db.list_authority_candidates_for_root(task_id) == []


def _seed_org(tmp_path: Path, slug: str = "test") -> Database:
    """Initialize a multi-org runtime with one seeded org and return its DB."""
    runtime = RuntimeDir.init(tmp_path / "rt")
    org_root = runtime.orgs_dir / slug
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    return Database(org_root / "happyranch.db")


def _seed_org_with_orch(
    tmp_path: Path, slug: str = "test",
) -> tuple[Database, Orchestrator, TaskQueue]:
    """Seed an org + construct a real Orchestrator wired to a real queue.

    Mirrors the sweep's production wiring closely enough that the
    bounded-wake and terminal-failure paths are exercisable end-to-end.
    Since TASK-3604, there is no auto-revisit path in production.
    """
    runtime = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=runtime.orgs_dir / slug)
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent]\n"
    )
    db = Database(paths.db_path)
    queue = TaskQueue()
    orch = Orchestrator(
        db=db, settings=Settings(), paths=paths, slug=slug,
        teams=TeamsRegistry.load(paths.root),
    )
    orch._queue = queue
    return db, orch, queue


def _claim_interrupted_recovery(db: Database, task_id: str, *, binding: str = "origin") -> None:
    db.insert_task(TaskRecord(
        id=task_id, brief="recovery", team="engineering",
        status=TaskStatus.IN_PROGRESS, assigned_agent="dev_agent",
        current_session_id="origin",
    ))
    assert db.claim_task_completion_recovery(
        task_id=task_id, agent="dev_agent", origin_session_id="origin",
        recovery_session_id=f"recovery-{task_id}", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00",
        expires_at="2999-01-01T00:02:00+00:00",
    )
    if binding != "origin":
        db.update_task(task_id, current_session_id=f"recovery-{task_id}")


def test_accepted_recovery_reentry_after_effects_is_consumed_without_duplicate_audit(tmp_path):
    """A terminal post-effect crash re-enters through shipping startup twice."""
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery

    db, orch, _ = _seed_org_with_orch(tmp_path)
    _claim_interrupted_recovery(db, "TASK-REC", binding="recovery")
    assert db.admit_task_completion_callback(
        task_id="TASK-REC", agent="dev_agent", session_id="recovery-TASK-REC",
        status="completed", output_summary="done",
        decision_json='{"action":"done","summary":"done"}', confidence_score=100,
    )
    accepted = db.get_accepted_task_completion_recovery_result(task_id="TASK-REC", agent="dev_agent")
    assert accepted is not None
    report = completion_report_from_result_row("TASK-REC", accepted, fallback_agent="dev_agent")
    with mock.patch.object(
        db, "mark_task_completion_recovery_callback_consumed",
        side_effect=RuntimeError("injected post-effect/pre-marker crash"),
    ):
        with pytest.raises(RuntimeError, match="post-effect/pre-marker"):
            _consume_accepted_completion_recovery(
                orch, "TASK-REC", report, agent="dev_agent",
                session_id="recovery-TASK-REC", result_row_id=accepted["id"],
            )
    db_path = db.path
    db.close()
    reopened = Database(db_path)
    orch._db = reopened
    from runtime.infrastructure.audit_logger import AuditLogger
    orch._audit = AuditLogger(reopened)
    _sweep_on_startup(reopened, _, "test", orch)
    _sweep_on_startup(reopened, _, "test", orch)
    assert reopened.get_task("TASK-REC").status is TaskStatus.COMPLETED
    assert reopened.execute("SELECT state FROM task_completion_recoveries WHERE task_id='TASK-REC'").fetchone()["state"] == "callback_consumed"
    assert len([r for r in reopened.get_audit_logs("TASK-REC") if r["action"] == "completion_report"]) == 1


@pytest.mark.parametrize("failure_point", ["terminal", "before_ledger", "after_ledger"])
def test_accepted_leaf_completion_recovery_is_atomic_and_preserves_exact_verdict(
    tmp_path, failure_point,
):
    """A real admitted SUBTASK callback commits its two audit facts together.

    Each trigger crosses a distinct boundary: before the terminal update,
    after it but before ledger consumption, and after ledger consumption but
    before commit.  Every failure rolls the whole receipt back; actual startup
    then consumes the immutable accepted result exactly once.
    """
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery

    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-PARENT", brief="parent", team="engineering",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))
    db.insert_task(TaskRecord(
        id="TASK-LEAF", brief="leaf", team="engineering", task_type="subtask",
        parent_task_id="TASK-PARENT", status=TaskStatus.IN_PROGRESS,
        assigned_agent="dev_agent", current_session_id="origin",
    ))
    assert db.claim_task_completion_recovery(
        task_id="TASK-LEAF", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery-TASK-LEAF", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00",
    )
    db.update_task("TASK-LEAF", current_session_id="recovery-TASK-LEAF")
    assert db.admit_task_completion_callback(
        task_id="TASK-LEAF", agent="dev_agent", session_id="recovery-TASK-LEAF",
        status="completed", output_summary="leaf done", verdict="REQUEST_CHANGES",
        confidence_score=100,
    )
    accepted = db.get_accepted_task_completion_recovery_result(
        task_id="TASK-LEAF", agent="dev_agent",
    )
    assert accepted is not None
    # This unbound row must never supply the leaf's verdict.
    db.insert_task_result(
        task_id="TASK-LEAF", agent="other", session_id="unrelated", status="completed",
        confidence_score=1, output_summary="unrelated", verdict="APPROVE",
    )
    report = completion_report_from_result_row("TASK-LEAF", accepted, fallback_agent="dev_agent")
    trigger_sql = {
        "terminal": """CREATE TRIGGER abort_leaf_terminal BEFORE UPDATE OF status ON tasks
            WHEN NEW.id = 'TASK-LEAF' AND NEW.status = 'completed'
            BEGIN SELECT RAISE(ABORT, 'injected terminal update'); END""",
        "before_ledger": """CREATE TRIGGER abort_leaf_before_ledger BEFORE UPDATE OF state ON task_completion_recoveries
            WHEN NEW.task_id = 'TASK-LEAF' AND NEW.state = 'callback_consumed'
            BEGIN SELECT RAISE(ABORT, 'injected before ledger consumption'); END""",
        "after_ledger": """CREATE TRIGGER abort_leaf_after_ledger AFTER UPDATE OF state ON task_completion_recoveries
            WHEN NEW.task_id = 'TASK-LEAF' AND NEW.state = 'callback_consumed'
            BEGIN SELECT RAISE(ABORT, 'injected after ledger before commit'); END""",
    }[failure_point]
    db.execute(trigger_sql)
    with pytest.raises(Exception, match="injected"):
        _consume_accepted_completion_recovery(
            orch, "TASK-LEAF", report, agent="dev_agent",
            session_id="recovery-TASK-LEAF", result_row_id=accepted["id"],
        )
    # No durable row is reset to emulate a crash: SQLite's abort alone must
    # retain the pre-transaction task, audit/verdict, and accepted receipt.
    task_before_reopen = db.get_task("TASK-LEAF")
    ledger_before_reopen = db.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id='TASK-LEAF'"
    ).fetchone()
    assert task_before_reopen is not None and task_before_reopen.status is TaskStatus.IN_PROGRESS
    assert ledger_before_reopen["state"] == "callback_accepted"
    assert ledger_before_reopen["accepted_result_id"] == accepted["id"]
    assert not [row for row in db.get_audit_logs("TASK-LEAF") if row["action"] == "completion_report"]
    assert not [row for row in db.get_audit_logs("TASK-LEAF") if row["action"] == "review_verdict"]
    db_path = db.path
    db.close()

    reopened = Database(db_path)
    reopened.execute("DROP TRIGGER " + {
        "terminal": "abort_leaf_terminal",
        "before_ledger": "abort_leaf_before_ledger",
        "after_ledger": "abort_leaf_after_ledger",
    }[failure_point])
    orch._db = reopened
    from runtime.infrastructure.audit_logger import AuditLogger
    orch._audit = AuditLogger(reopened)
    _sweep_on_startup(reopened, queue, "test", orch)
    _sweep_on_startup(reopened, queue, "test", orch)

    assert reopened.get_task("TASK-LEAF").status is TaskStatus.COMPLETED
    ledger = reopened.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id='TASK-LEAF'"
    ).fetchone()
    assert ledger["state"] == "callback_consumed"
    assert ledger["accepted_result_id"] == accepted["id"]
    audits = reopened.get_audit_logs("TASK-LEAF")
    assert [row["action"] for row in audits].count("completion_report") == 1
    verdicts = [row for row in audits if row["action"] == "review_verdict"]
    assert len(verdicts) == 1
    assert verdicts[0]["payload"] == {
        "verdict": "REQUEST_CHANGES", "feedback": "leaf done", "reviewed_agent": "dev_agent",
    }
    assert queue._queue.get_nowait() == ("test", "TASK-PARENT", None)
    assert queue._queue.empty()


@pytest.mark.parametrize("winner", ["cancelled", "newer_binding", "reassigned_agent"])
def test_accepted_leaf_recovery_loses_to_winner_at_consume_transaction_boundary(tmp_path, winner):
    """A winner installed after selection cannot be overwritten by recovery."""
    import asyncio
    from types import SimpleNamespace

    from runtime.daemon.event_bus import EventBus
    from runtime.daemon.routes.tasks import CancelBody, cancel_task
    from runtime.daemon.sessions import SessionTracker
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery

    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(id="TASK-LEAF", brief="leaf", team="engineering", task_type="subtask",
                              status=TaskStatus.IN_PROGRESS, assigned_agent="dev_agent",
                              current_session_id="origin"))
    assert db.claim_task_completion_recovery(task_id="TASK-LEAF", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery-TASK-LEAF", provider_session_id="provider", claimed_at="2026-01-01T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00")
    db.update_task("TASK-LEAF", current_session_id="recovery-TASK-LEAF")
    _seed_job(db, "JOB-OWNED", "TASK-LEAF", status="running")
    assert db.admit_task_completion_callback(task_id="TASK-LEAF", agent="dev_agent", session_id="recovery-TASK-LEAF", status="completed", output_summary="accepted", verdict="PASS", confidence_score=100)
    accepted = db.get_accepted_task_completion_recovery_result(task_id="TASK-LEAF", agent="dev_agent")
    assert accepted is not None
    tracker = SessionTracker()
    tracker.register_recovery_session("TASK-LEAF", "dev_agent", "recovery-TASK-LEAF")
    winner_control_calls: list[str] = []
    cleanup_calls: list[str] = []
    original_consume = db.consume_accepted_completed_task_completion_recovery

    def install_winner(**kwargs):
        if winner == "cancelled":
            org = SimpleNamespace(db=db, db_lock=asyncio.Lock(), orchestrator=orch, sessions=tracker,
                                  event_bus=EventBus(lambda _task_id: []))
            asyncio.run(cancel_task("TASK-LEAF", CancelBody(cascade=False), org))
        elif winner == "newer_binding":
            db.update_task("TASK-LEAF", current_session_id="newer-session")
            tracker.set_active("TASK-LEAF", "dev_agent", "newer-session")
            tracker.set_pid("TASK-LEAF", "dev_agent", "newer-session", 424242)
            tracker.set_cancel_control("TASK-LEAF", "dev_agent", "newer-session", lambda: winner_control_calls.append("newer"))
        else:
            db.update_task("TASK-LEAF", assigned_agent="new-agent")
            tracker.set_active("TASK-LEAF", "new-agent", "recovery-TASK-LEAF")
            tracker.set_pid("TASK-LEAF", "new-agent", "recovery-TASK-LEAF", 424242)
            tracker.set_cancel_control("TASK-LEAF", "new-agent", "recovery-TASK-LEAF", lambda: winner_control_calls.append("newer"))
        return original_consume(**kwargs)

    with mock.patch("runtime.orchestrator.run_step._kill_jobs_for_terminating_task", side_effect=lambda _orch, task_id: cleanup_calls.append(task_id)), mock.patch.object(db, "consume_accepted_completed_task_completion_recovery", side_effect=install_winner):
        _consume_accepted_completion_recovery(orch, "TASK-LEAF", completion_report_from_result_row("TASK-LEAF", accepted, fallback_agent="dev_agent"), agent="dev_agent", session_id="recovery-TASK-LEAF", result_row_id=accepted["id"])

    task = db.get_task("TASK-LEAF")
    ledger = db.execute("SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id='TASK-LEAF'").fetchone()
    assert task is not None
    assert not [row for row in db.get_audit_logs("TASK-LEAF") if row["action"] in {"completion_report", "review_verdict"}]
    assert ledger["state"] == "callback_accepted" and ledger["accepted_result_id"] == accepted["id"]
    assert db.get_job("JOB-OWNED").status.value == "running"
    assert queue._queue.empty()
    if winner == "cancelled":
        assert task.status is TaskStatus.CANCELLED and task.cancelled_at is not None
        # This one cleanup is cancel_task's documented side effect, not recovery.
        assert cleanup_calls == ["TASK-LEAF"]
    else:
        assert task.status is TaskStatus.IN_PROGRESS
        if winner == "newer_binding":
            assert task.assigned_agent == "dev_agent" and task.current_session_id == "newer-session"
            assert tracker.get_pid("TASK-LEAF", "dev_agent") == 424242
            assert tracker.get_cancel_control("TASK-LEAF", "dev_agent") is not None
        else:
            assert task.assigned_agent == "new-agent" and task.current_session_id == "recovery-TASK-LEAF"
            assert tracker.get_pid("TASK-LEAF", "new-agent") == 424242
            assert tracker.get_cancel_control("TASK-LEAF", "new-agent") is not None
        assert cleanup_calls == [] and winner_control_calls == []
    db_path = db.path
    db.close()
    reopened = Database(db_path)
    orch._db = reopened
    _sweep_on_startup(reopened, queue, "test", orch)
    _sweep_on_startup(reopened, queue, "test", orch)
    assert reopened.execute("SELECT state FROM task_completion_recoveries WHERE task_id='TASK-LEAF'").fetchone()["state"] == "callback_accepted"
    assert reopened.get_job("JOB-OWNED").status.value == "running"
    assert queue._queue.empty()


def test_completed_leaf_postcommit_restart_cleans_only_owned_job_and_wakes_once(tmp_path):
    """A crash after the durable leaf receipt is repaired by startup, not PID reuse."""
    import threading

    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery
    from runtime.daemon import jobs_runner

    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(id="TASK-PARENT", brief="parent", team="engineering",
                              status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED))
    db.insert_task(TaskRecord(id="TASK-LEAF", brief="leaf", team="engineering", task_type="subtask",
                              parent_task_id="TASK-PARENT", status=TaskStatus.IN_PROGRESS,
                              assigned_agent="dev_agent", current_session_id="origin"))
    db.insert_task(TaskRecord(id="TASK-OTHER-PARENT", brief="other parent", team="engineering",
                              status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED))
    db.insert_task(TaskRecord(id="TASK-OTHER", brief="other", team="engineering", task_type="subtask",
                              parent_task_id="TASK-OTHER-PARENT", status=TaskStatus.IN_PROGRESS,
                              block_kind=BlockKind.BLOCKED_ON_JOB))
    assert db.claim_task_completion_recovery(task_id="TASK-LEAF", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery-TASK-LEAF", provider_session_id="provider", claimed_at="2026-01-01T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00")
    db.update_task("TASK-LEAF", current_session_id="recovery-TASK-LEAF")
    _seed_job(db, "JOB-OWNED", "TASK-LEAF", status="running")
    _seed_job(db, "JOB-OTHER", "TASK-OTHER", status="running")
    db.update_task("TASK-OTHER", blocked_on_job_ids='["JOB-OTHER"]')
    assert db.admit_task_completion_callback(task_id="TASK-LEAF", agent="dev_agent", session_id="recovery-TASK-LEAF", status="completed", output_summary="done", verdict="PASS", confidence_score=100)
    accepted = db.get_accepted_task_completion_recovery_result(task_id="TASK-LEAF", agent="dev_agent")
    assert accepted is not None
    with mock.patch("runtime.orchestrator.run_step._kill_jobs_for_terminating_task", side_effect=RuntimeError("after commit")):
        with pytest.raises(RuntimeError, match="after commit"):
            _consume_accepted_completion_recovery(orch, "TASK-LEAF", completion_report_from_result_row("TASK-LEAF", accepted, fallback_agent="dev_agent"), agent="dev_agent", session_id="recovery-TASK-LEAF", result_row_id=accepted["id"])
    path = db.path; db.close(); reopened = Database(path); orch._db = reopened
    from runtime.infrastructure.audit_logger import AuditLogger
    orch._audit = AuditLogger(reopened)
    _sweep_on_startup(reopened, queue, "test", orch)
    # The startup sweep settles the exact ledger-owned job durably before it
    # returns. The generic lifespan scan therefore sees only the unrelated
    # row; no process wait or persisted PID signal is involved at restart.
    assert reopened.get_job("JOB-OWNED").reason == "task_ended"
    assert reopened.recover_orphaned_running_jobs(
        now_iso="2026-01-01T00:02:00+00:00",
    ) == ["JOB-OTHER"]
    with mock.patch("runtime.daemon.jobs_runner.terminate_jobs_for_task"):
        _sweep_on_startup(reopened, queue, "test", orch)
    assert reopened.get_job("JOB-OWNED").reason == "task_ended"
    assert reopened.get_job("JOB-OTHER").reason == "daemon_crash"
    assert reopened.get_task("TASK-OTHER-PARENT").status is TaskStatus.IN_PROGRESS
    assert [r["action"] for r in reopened.get_audit_logs("TASK-LEAF")].count("completion_report") == 1
    assert reopened.execute("SELECT state FROM task_completion_recoveries WHERE task_id='TASK-LEAF'").fetchone()["state"] == "callback_consumed"
    assert queue._queue.get_nowait() == ("test", "TASK-PARENT", None)
    # The following row is the lifespan's ordinary orphan wake, deliberately
    # separated from recovery cleanup above.
    assert queue._queue.get_nowait() == ("test", "TASK-OTHER", None)
    assert queue._queue.empty()


@pytest.mark.parametrize("kind", ["leaf", "manager_done", "nonroot_escalate"])
def test_startup_consumed_recovery_backstops_owned_job_before_orphan_sweep(tmp_path, kind):
    """A real accepted callback consumed by startup settles before its async cleanup."""
    import threading
    from runtime.daemon import jobs_runner
    from runtime.orchestrator.orchestrator import completion_report_from_result_row

    if kind == "leaf":
        db, orch, queue = _seed_org_with_orch(tmp_path)
        task_id, agent, session_id, decision = "TASK-REC", "dev_agent", "recovery-TASK-REC", None
        _claim_interrupted_recovery(db, task_id, binding="recovery")
        db.execute("UPDATE tasks SET task_type='subtask' WHERE id=?", (task_id,)); db.commit()
    else:
        db, orch, queue, task_id, _ = _seed_manager_recovery_result(tmp_path)
        agent, session_id = "engineering_manager", "recovery-manager"
        db.update_task(task_id, current_session_id="origin")
        assert db.claim_task_completion_recovery(task_id=task_id, agent=agent, origin_session_id="origin", recovery_session_id=session_id, provider_session_id="provider", claimed_at="2026-01-01T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00")
        db.update_task(task_id, current_session_id=session_id)
        decision = '{"action":"done","summary":"done"}' if kind == "manager_done" else '{"action":"escalate","reason":"parent"}'
    db.insert_task(TaskRecord(id="TASK-PARENT", brief="parent", team="engineering", status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED))
    db.execute("UPDATE tasks SET parent_task_id='TASK-PARENT' WHERE id=?", (task_id,)); db.commit()
    db.insert_task(TaskRecord(id="TASK-OTHER", brief="other", team="engineering"))
    _seed_job(db, "JOB-OWNED", task_id, "running"); _seed_job(db, "JOB-OTHER", "TASK-OTHER", "running")
    assert db.admit_task_completion_callback(task_id=task_id, agent=agent, session_id=session_id, status="completed", output_summary="done", confidence_score=100, decision_json=decision)
    accepted = db.get_accepted_task_completion_recovery_result(task_id=task_id, agent=agent); assert accepted is not None
    accepted_snapshot = dict(accepted)
    entered, release, errors = threading.Event(), threading.Event(), []
    real_terminate, real_thread, workers = jobs_runner.terminate_jobs_for_task, threading.Thread, []
    async def paused(*args, **kwargs):
        entered.set(); assert release.wait(2); return await real_terminate(*args, **kwargs)
    def tracked_thread(*, target, daemon):
        worker = real_thread(target=lambda: _capture_worker_error(target, errors), daemon=daemon); workers.append(worker); return worker
    try:
        with mock.patch.object(jobs_runner, "terminate_jobs_for_task", paused), mock.patch.object(threading, "Thread", tracked_thread):
            _sweep_on_startup(db, queue, "test", orch)
            assert entered.wait(2)
            assert db.get_job("JOB-OWNED").reason == "task_ended"
            assert db.recover_orphaned_running_jobs(now_iso="2026-01-01T00:03:00Z") == ["JOB-OTHER"]
    finally:
        release.set()
        for worker in workers:
            worker.join(2)
            assert not worker.is_alive()
    # Cleanup errors arise only after the held real runner is released.
    assert not errors
    _sweep_on_startup(db, queue, "test", orch)
    _sweep_on_startup(db, queue, "test", orch)
    # These immutable receipt and owner assertions are deliberately after
    # both reentries: a later startup must not corrupt already-consumed state.
    accepted_after = db.execute(
        "SELECT * FROM task_results WHERE id=?", (accepted["id"],),
    ).fetchone()
    assert accepted_after is not None and dict(accepted_after) == accepted_snapshot
    ledger = db.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?",
        (task_id,),
    ).fetchone()
    assert ledger["state"] == "callback_consumed"
    assert ledger["accepted_result_id"] == accepted["id"]
    task_after = db.get_task(task_id)
    assert task_after is not None and task_after.current_session_id == session_id
    assert db.get_job("JOB-OWNED").reason == "task_ended"
    assert db.get_job("JOB-OTHER").reason == "daemon_crash"


@pytest.mark.parametrize("winner", ["cancelled", "replacement"])
def test_startup_preterminal_selection_revalidates_recovery_owner_before_effects(tmp_path, winner):
    """A winner after owner selection receives no stale recovery effects."""
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery

    db, orch, queue = _seed_org_with_orch(tmp_path)
    _claim_interrupted_recovery(db, "TASK-REC", binding="recovery")
    db.execute("UPDATE tasks SET task_type='subtask' WHERE id='TASK-REC'"); db.commit()
    _seed_job(db, "JOB-OWNED", "TASK-REC", "running")
    assert db.admit_task_completion_callback(
        task_id="TASK-REC", agent="dev_agent", session_id="recovery-TASK-REC",
        status="completed", output_summary="done", confidence_score=100,
    )
    accepted = db.get_accepted_task_completion_recovery_result(
        task_id="TASK-REC", agent="dev_agent",
    )
    assert accepted is not None
    accepted_snapshot = dict(accepted)

    def interrupt_before_settlement(**kwargs):
        raise RuntimeError("finite post-consumption/pre-settlement interruption")

    with mock.patch.object(
        db, "settle_consumed_task_completion_recovery_jobs",
        side_effect=interrupt_before_settlement,
    ), pytest.raises(RuntimeError, match="post-consumption/pre-settlement"):
        _consume_accepted_completion_recovery(
            orch, "TASK-REC", completion_report_from_result_row(
                "TASK-REC", accepted, fallback_agent="dev_agent",
            ), agent="dev_agent", session_id="recovery-TASK-REC",
            result_row_id=accepted["id"],
        )
    receipt_before_restart = db.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?",
        ("TASK-REC",),
    ).fetchone()
    assert receipt_before_restart["state"] == "callback_consumed"
    assert receipt_before_restart["accepted_result_id"] == accepted["id"]
    assert db.get_task("TASK-REC").status is TaskStatus.COMPLETED
    assert db.get_job("JOB-OWNED").status.value == "running"
    assert dict(db.execute(
        "SELECT * FROM task_results WHERE id=?", (accepted["id"],),
    ).fetchone()) == accepted_snapshot

    select = db.get_consumed_task_completion_recovery_owners
    selection_calls = 0

    def select_then_replace():
        nonlocal selection_calls
        selection_calls += 1
        selected = select()
        if selection_calls > 1:
            return selected
        assert selected and selected[0]["accepted_result_id"] == accepted["id"]
        if winner == "cancelled":
            db.update_task("TASK-REC", cancelled_at="2026-01-01T00:03:00Z")
        else:
            db.update_task("TASK-REC", current_session_id="newer-session")
        return selected
    with mock.patch.object(db, "get_consumed_task_completion_recovery_owners", side_effect=select_then_replace), mock.patch(
        "runtime.orchestrator.run_step._kill_jobs_for_terminating_task",
    ) as cleanup:
        _sweep_on_startup(db, queue, "test", orch)
        _sweep_on_startup(db, queue, "test", orch)
    assert db.get_job("JOB-OWNED").status.value == "running"
    cleanup.assert_not_called()
    task = db.get_task("TASK-REC")
    assert task.cancelled_at is not None if winner == "cancelled" else task.current_session_id == "newer-session"
    assert dict(db.execute(
        "SELECT * FROM task_results WHERE id=?", (accepted["id"],),
    ).fetchone()) == accepted_snapshot
    receipt_after_reentries = db.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id=?",
        ("TASK-REC",),
    ).fetchone()
    assert dict(receipt_after_reentries) == dict(receipt_before_restart)
    assert selection_calls == 2
    assert queue._queue.empty()


def _capture_worker_error(target, errors):
    try:
        target()
    except BaseException as exc:
        errors.append(exc)


@pytest.mark.asyncio
async def test_completed_leaf_live_cleanup_waits_for_real_runner_and_is_idempotent(tmp_path, monkeypatch):
    """The live recovery consumer terminates only its owned fake process before returning."""
    import asyncio
    import threading
    from types import SimpleNamespace
    from runtime.daemon import jobs_runner
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery

    db, orch, queue = _seed_org_with_orch(tmp_path)
    _claim_interrupted_recovery(db, "TASK-REC", binding="recovery")
    db.insert_task(TaskRecord(id="TASK-PARENT", brief="parent", team="engineering",
                              status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED))
    db.execute("UPDATE tasks SET task_type='subtask', parent_task_id='TASK-PARENT' WHERE id='TASK-REC'")
    _seed_job(db, "JOB-OWNED", "TASK-REC", status="running")
    db.insert_task(TaskRecord(id="TASK-OTHER", brief="other")); _seed_job(db, "JOB-OTHER", "TASK-OTHER", status="running")
    assert db.admit_task_completion_callback(task_id="TASK-REC", agent="dev_agent", session_id="recovery-TASK-REC", status="completed", output_summary="done", confidence_score=100)
    accepted = db.get_accepted_task_completion_recovery_result(task_id="TASK-REC", agent="dev_agent"); assert accepted is not None
    owned, other = SimpleNamespace(pid=111, returncode=None), SimpleNamespace(pid=222, returncode=None)
    jobs_runner._INFLIGHT.update({"JOB-OWNED": owned, "JOB-OTHER": other})
    done = threading.Event(); calls = []
    def killpg(pid, _sig):
        calls.append(pid)
        if pid == owned.pid: owned.returncode = -15
    async def no_wait(_): return None
    real = jobs_runner.terminate_jobs_for_task
    async def observed(*args, **kwargs):
        result = await real(*args, **kwargs); done.set(); return result
    monkeypatch.setattr(jobs_runner.os, "killpg", killpg); monkeypatch.setattr(jobs_runner.asyncio, "sleep", no_wait)
    try:
        with mock.patch("runtime.daemon.jobs_runner.terminate_jobs_for_task", side_effect=observed):
            _consume_accepted_completion_recovery(orch, "TASK-REC", completion_report_from_result_row("TASK-REC", accepted, fallback_agent="dev_agent"), agent="dev_agent", session_id="recovery-TASK-REC", result_row_id=accepted["id"])
            await asyncio.wait_for(asyncio.to_thread(done.wait, 2.0), timeout=2.5)
            _consume_accepted_completion_recovery(orch, "TASK-REC", completion_report_from_result_row("TASK-REC", accepted, fallback_agent="dev_agent"), agent="dev_agent", session_id="recovery-TASK-REC", result_row_id=accepted["id"])
            await asyncio.sleep(0)
        assert calls == [111]
        assert db.get_job("JOB-OWNED").reason == "task_ended"
        assert db.get_job("JOB-OTHER").status.value == "running"
        assert queue._queue.get_nowait() == ("test", "TASK-PARENT", None)
        assert queue._queue.empty()
    finally:
        jobs_runner._INFLIGHT.pop("JOB-OWNED", None); jobs_runner._INFLIGHT.pop("JOB-OTHER", None)


@pytest.mark.parametrize("stale_owner", ["session", "agent", "cancelled"])
def test_consumed_leaf_recovery_startup_ignores_stale_current_owner(tmp_path, stale_owner):
    """A consumed historical receipt never cleans up a newer winner's job."""
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery

    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-LEAF", brief="leaf", team="engineering", task_type="subtask",
        status=TaskStatus.IN_PROGRESS, assigned_agent="dev_agent",
        current_session_id="origin",
    ))
    assert db.claim_task_completion_recovery(
        task_id="TASK-LEAF", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery-TASK-LEAF", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00",
    )
    db.update_task("TASK-LEAF", current_session_id="recovery-TASK-LEAF")
    assert db.admit_task_completion_callback(
        task_id="TASK-LEAF", agent="dev_agent", session_id="recovery-TASK-LEAF",
        status="completed", output_summary="leaf done", confidence_score=100,
    )
    accepted = db.get_accepted_task_completion_recovery_result(
        task_id="TASK-LEAF", agent="dev_agent",
    )
    assert accepted is not None
    _consume_accepted_completion_recovery(
        orch, "TASK-LEAF",
        completion_report_from_result_row("TASK-LEAF", accepted, fallback_agent="dev_agent"),
        agent="dev_agent", session_id="recovery-TASK-LEAF", result_row_id=accepted["id"],
    )
    _seed_job(db, "JOB-WINNER", "TASK-LEAF", status="running")
    if stale_owner == "session":
        db.update_task("TASK-LEAF", current_session_id="newer-session")
    elif stale_owner == "agent":
        db.update_task("TASK-LEAF", assigned_agent="other_agent")
    else:
        db.update_task("TASK-LEAF", cancelled_at="2026-01-01T00:03:00+00:00")

    assert db.get_consumed_completed_task_completion_recovery_task_ids() == []
    _sweep_on_startup(db, queue, "test", orch)
    winner = db.get_job("JOB-WINNER")
    assert winner is not None and winner.status.value == "running"
    assert queue._queue.empty()


@pytest.mark.parametrize(
    "crash_boundary",
    [
        "after_completion_audit_before_parking",
        "after_parking_before_blocked_audit",
        "after_blocked_audit_before_immediate_resume",
    ],
)
@pytest.mark.parametrize("terminal_exit_code", [None, 0, 7])
def test_accepted_blocked_recovery_restart_consumes_once_then_resumes_owned_job(
    tmp_path, crash_boundary, terminal_exit_code,
):
    """Accepted blocked recovery is atomic, then startup reconstructs its wake.

    The first two injected failures happen inside the database transaction, so
    reopening must see no partial audit/carrier state.  The final failure is
    after its durable commit; the ordinary startup sweep reconstructs a single
    terminal-job wake.  A running owned job always remains parked.
    """
    from runtime.models import JobStatus
    from runtime.orchestrator.orchestrator import completion_report_from_result_row
    from runtime.orchestrator.run_step import _consume_accepted_completion_recovery

    db, orch, queue = _seed_org_with_orch(tmp_path)
    _claim_interrupted_recovery(db, "TASK-REC", binding="recovery")
    _seed_job(db, "JOB-REC", "TASK-REC", status="running")
    if terminal_exit_code is not None:
        db.transition_job_to_terminal(
            "JOB-REC",
            status=JobStatus.COMPLETED if terminal_exit_code == 0 else JobStatus.FAILED,
            exit_code=terminal_exit_code,
            finished_at="2026-01-01T00:02:00+00:00", duration_ms=1,
            stdout_head="", stderr_head="",
        )
    assert db.admit_task_completion_callback(
        task_id="TASK-REC", agent="dev_agent", session_id="recovery-TASK-REC",
        status="blocked", output_summary="waiting for owned job",
        waiting_on_job_ids=["JOB-REC"], confidence_score=100,
    )
    accepted = db.get_accepted_task_completion_recovery_result(
        task_id="TASK-REC", agent="dev_agent",
    )
    assert accepted is not None
    report = completion_report_from_result_row("TASK-REC", accepted, fallback_agent="dev_agent")
    if crash_boundary == "after_blocked_audit_before_immediate_resume":
        patch_target = "runtime.orchestrator.run_step._maybe_resume_blocked_task"
        patch = mock.patch(patch_target, side_effect=RuntimeError(f"injected {crash_boundary} crash"))
    else:
        original_insert = db.insert_audit_log_uncommitted
        calls = 0

        def interrupted_insert(*args, **kwargs):
            nonlocal calls
            calls += 1
            if crash_boundary == "after_completion_audit_before_parking" and calls == 1:
                original_insert(*args, **kwargs)
                raise RuntimeError(f"injected {crash_boundary} crash")
            if crash_boundary == "after_parking_before_blocked_audit" and calls == 2:
                raise RuntimeError(f"injected {crash_boundary} crash")
            return original_insert(*args, **kwargs)

        patch = mock.patch.object(db, "insert_audit_log_uncommitted", side_effect=interrupted_insert)
    with patch:
        with pytest.raises(RuntimeError, match=crash_boundary):
            _consume_accepted_completion_recovery(
                orch, "TASK-REC", report, agent="dev_agent",
                session_id="recovery-TASK-REC", result_row_id=accepted["id"],
            )

    db_path = db.path
    db.close()
    reopened = Database(db_path)
    orch._db = reopened
    from runtime.infrastructure.audit_logger import AuditLogger
    orch._audit = AuditLogger(reopened)
    _sweep_on_startup(reopened, queue, "test", orch)
    _sweep_on_startup(reopened, queue, "test", orch)

    task = reopened.get_task("TASK-REC")
    job = reopened.get_job("JOB-REC")
    ledger = reopened.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id='TASK-REC'"
    ).fetchone()
    assert task is not None and task.status is TaskStatus.IN_PROGRESS
    assert task.block_kind is BlockKind.BLOCKED_ON_JOB
    assert job is not None
    expected_status = JobStatus.RUNNING if terminal_exit_code is None else (
        JobStatus.COMPLETED if terminal_exit_code == 0 else JobStatus.FAILED
    )
    assert job.status is expected_status
    assert ledger["state"] == "callback_consumed"
    assert ledger["accepted_result_id"] == accepted["id"]
    actions = [row["action"] for row in reopened.get_audit_logs("TASK-REC")]
    assert actions.count("completion_report") == 1
    assert actions.count("task_blocked_on_jobs") == 1
    if terminal_exit_code is None:
        assert queue._queue.empty()
        reopened.transition_job_to_terminal(
            "JOB-REC", status=JobStatus.COMPLETED, exit_code=0,
            finished_at="2026-01-01T00:03:00+00:00", duration_ms=1,
            stdout_head="", stderr_head="",
        )
        _sweep_on_startup(reopened, queue, "test", orch)
        _sweep_on_startup(reopened, queue, "test", orch)
    queued = queue._queue.get_nowait()
    assert queued[:2] == ("test", "TASK-REC")
    assert queued[2] is None or queued[2] == {
        "trigger": "block_submit", "triggering_job_id": None,
    }
    assert queue._queue.empty()
    assert [row["action"] for row in reopened.get_audit_logs("TASK-REC")].count("completion_report") == 1


@pytest.mark.parametrize("binding", ["origin", "recovery"])
@pytest.mark.parametrize("executor_pid", [None, 424242])
def test_sweep_restart_settles_unaccepted_recovery_before_pid_liveness_and_reconciles_owned_job(
    tmp_path, binding, executor_pid,
):
    db, orch, queue = _seed_org_with_orch(tmp_path)
    _claim_interrupted_recovery(db, "TASK-REC", binding=binding)
    _seed_job(db, "JOB-REC", "TASK-REC", status="running")
    db.insert_task(TaskRecord(id="TASK-OTHER", brief="other"))
    _seed_job(db, "JOB-OTHER", "TASK-OTHER", status="running")
    db.update_task("TASK-REC", executor_pid=executor_pid)
    db_path = db.path
    db.close()
    reopened = Database(db_path)
    orch._db = reopened

    from runtime.orchestrator.run_step import _kill_jobs_for_terminating_task
    with mock.patch("os.kill") as kill, mock.patch(
        "runtime.orchestrator.run_step._kill_jobs_for_terminating_task",
        wraps=_kill_jobs_for_terminating_task,
    ) as cleanup:
        _sweep_on_startup(reopened, queue, "test", orch)

    task = reopened.get_task("TASK-REC")
    ledger = reopened.execute("SELECT state FROM task_completion_recoveries").fetchone()
    assert task is not None and task.status is TaskStatus.FAILED
    assert ledger["state"] == "restart_settled"
    kill.assert_not_called()
    cleanup.assert_called_once_with(orch, "TASK-REC")
    owned = reopened.get_job("JOB-REC")
    unrelated = reopened.get_job("JOB-OTHER")
    assert owned is not None and owned.status.value == "failed"
    assert owned.reason == "task_ended"
    assert unrelated is not None and unrelated.status.value == "running"
    _sweep_on_startup(reopened, queue, "test", orch)
    assert reopened.execute("SELECT COUNT(*) FROM task_completion_recoveries").fetchone()[0] == 1
    assert reopened.get_job("JOB-REC").reason == "task_ended"


def test_sweep_restart_does_not_replace_accepted_cancelled_or_newer_recovery_owner(tmp_path):
    db, orch, queue = _seed_org_with_orch(tmp_path)
    _claim_interrupted_recovery(db, "TASK-ACCEPT", binding="recovery")
    assert db.admit_task_completion_callback(
        task_id="TASK-ACCEPT", agent="dev_agent", session_id="recovery-TASK-ACCEPT",
        output_summary="accepted", confidence_score=90,
    )
    _claim_interrupted_recovery(db, "TASK-CANCEL")
    db.update_task("TASK-CANCEL", cancelled_at="2026-01-01T00:01:00+00:00")
    _claim_interrupted_recovery(db, "TASK-NEWER")
    db.update_task("TASK-NEWER", current_session_id="newer")

    _sweep_on_startup(db, queue, "test", orch)

    # The accepted ledger row is consumed before PID liveness; restart
    # settlement itself must never convert it to failure.
    assert db.get_task("TASK-ACCEPT").status is not TaskStatus.FAILED
    assert db.get_task("TASK-CANCEL").status is TaskStatus.IN_PROGRESS
    assert db.get_task("TASK-NEWER").current_session_id == "newer"
    assert db.execute(
        "SELECT state FROM task_completion_recoveries WHERE task_id='TASK-ACCEPT'"
    ).fetchone()["state"] == "callback_consumed"
    assert db.execute(
        "SELECT state FROM task_completion_recoveries WHERE task_id='TASK-NEWER'"
    ).fetchone()["state"] == "claimed"


def test_sweep_restart_settlement_reenters_after_cleanup_interrupt_and_lifespan_reopens(tmp_path):
    """The durable settlement survives interruption before live-control cleanup.

    A restarted process has no inherited ``_INFLIGHT`` controls.  The first
    sweep therefore only proves durable-row reconciliation; the next shipping
    sweep wakes the parked parent, and the lifespan orphan scan handles only
    the unrelated running row.
    """
    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-PARENT", brief="parent", team="engineering",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))
    db.insert_task(TaskRecord(
        id="TASK-REC", brief="recovery", team="engineering",
        status=TaskStatus.IN_PROGRESS, assigned_agent="dev_agent",
        current_session_id="origin", parent_task_id="TASK-PARENT",
    ))
    assert db.claim_task_completion_recovery(
        task_id="TASK-REC", agent="dev_agent", origin_session_id="origin",
        recovery_session_id="recovery-TASK-REC", provider_session_id="provider",
        claimed_at="2026-01-01T00:00:00+00:00", expires_at="2999-01-01T00:02:00+00:00",
    )
    _seed_job(db, "JOB-REC", "TASK-REC", status="running")
    db.insert_task(TaskRecord(id="TASK-OTHER", brief="other"))
    _seed_job(db, "JOB-OTHER", "TASK-OTHER", status="running")
    # A prior-session blocked report must never become a recovery completion.
    db.insert_task_result(
        task_id="TASK-REC", agent="dev_agent", session_id="prior-session",
        status="blocked", confidence_score=1, output_summary="stale",
        waiting_on_job_ids=["JOB-REC"],
    )
    db_path = db.path
    db.close()
    reopened = Database(db_path)
    orch._db = reopened

    with mock.patch(
        "runtime.orchestrator.run_step._kill_jobs_for_terminating_task",
        side_effect=RuntimeError("stop after durable settlement"),
    ):
        with pytest.raises(RuntimeError, match="durable settlement"):
            _sweep_on_startup(reopened, queue, "test", orch)

    assert reopened.get_task("TASK-REC").status is TaskStatus.FAILED
    assert reopened.get_job("JOB-REC").reason == "task_ended"
    assert queue._queue.empty()
    reopened.close()

    reopened = Database(db_path)
    orch._db = reopened
    with mock.patch("os.kill") as kill:
        _sweep_on_startup(reopened, queue, "test", orch)
    # This is the lifespan seam, after the startup sweep in production.
    assert reopened.recover_orphaned_running_jobs(now_iso="2026-01-01T00:02:00+00:00") == ["JOB-OTHER"]

    settled = reopened.execute(
        "SELECT state FROM task_completion_recoveries WHERE task_id='TASK-REC'"
    ).fetchone()
    assert settled["state"] == "restart_settled"
    assert reopened.get_task_results("TASK-REC")[0]["session_id"] == "prior-session"
    assert reopened.get_job("JOB-REC").reason == "task_ended"
    assert reopened.get_job("JOB-OTHER").reason == "daemon_crash"
    # Repeat the shipping sweep before workers consume its recovered wake:
    # the parent stays queued exactly once.
    _sweep_on_startup(reopened, queue, "test", orch)
    assert queue._queue.get_nowait() == ("test", "TASK-PARENT", None)
    # The unrelated pending task follows its ordinary startup queue contract.
    assert queue._queue.get_nowait() == ("test", "TASK-OTHER", None)
    assert queue._queue.get_nowait() == ("test", "TASK-OTHER", None)
    assert queue._queue.empty()
    kill.assert_not_called()


def test_restart_settlement_rolls_back_task_ledger_and_job_together(tmp_path):
    db, orch, queue = _seed_org_with_orch(tmp_path)
    _claim_interrupted_recovery(db, "TASK-REC", binding="recovery")
    _seed_job(db, "JOB-REC", "TASK-REC", status="running")
    # The job UPDATE is after the task and ledger writes inside the same
    # BEGIN IMMEDIATE transaction.  Abort exactly there, then prove reopen
    # retains every pre-transaction row and a later real sweep settles all.
    db.execute(
        """CREATE TRIGGER abort_restart_job_update BEFORE UPDATE OF status ON jobs
           WHEN NEW.reason = 'task_ended'
           BEGIN SELECT RAISE(ABORT, 'injected job update failure'); END"""
    )
    with pytest.raises(Exception, match="injected job update failure"):
        _sweep_on_startup(db, queue, "test", orch)
    db.close()

    reopened = Database(db.path)
    orch._db = reopened
    assert reopened.get_task("TASK-REC").status is TaskStatus.IN_PROGRESS
    assert reopened.get_job("JOB-REC").status.value == "running"
    assert reopened.execute(
        "SELECT state FROM task_completion_recoveries WHERE task_id='TASK-REC'"
    ).fetchone()["state"] == "claimed"
    reopened.execute("DROP TRIGGER abort_restart_job_update")
    _sweep_on_startup(reopened, queue, "test", orch)
    assert reopened.get_task("TASK-REC").status is TaskStatus.FAILED
    assert reopened.get_job("JOB-REC").reason == "task_ended"
    assert reopened.execute(
        "SELECT state FROM task_completion_recoveries WHERE task_id='TASK-REC'"
    ).fetchone()["state"] == "restart_settled"


@pytest.mark.parametrize("winner", ["callback", "cancelled", "newer_binding"])
def test_sweep_restart_settlement_loses_to_real_winner_selected_after_claim_read(tmp_path, winner):
    """The winner commits at the actual get-claim → settle boundary, not setup."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    _claim_interrupted_recovery(db, "TASK-REC", binding="recovery")
    _seed_job(db, "JOB-REC", "TASK-REC", status="running")
    original_get_claim = db.get_claimed_task_completion_recovery
    accepted_id = None

    def select_then_commit_winner(*, task_id, agent):
        nonlocal accepted_id
        claim = original_get_claim(task_id=task_id, agent=agent)
        assert claim is not None
        if winner == "callback":
            assert db.admit_task_completion_callback(
                task_id=task_id, agent=agent, session_id="recovery-TASK-REC",
                output_summary="winner", confidence_score=90,
            )
            accepted_id = db.get_task_results(task_id)[0]["id"]
        elif winner == "cancelled":
            db.update_task(task_id, cancelled_at="2026-01-01T00:01:00+00:00")
        else:
            db.update_task(task_id, current_session_id="newer-binding")
        return claim

    with mock.patch.object(db, "get_claimed_task_completion_recovery", side_effect=select_then_commit_winner):
        _sweep_on_startup(db, queue, "test", orch)

    task = db.get_task("TASK-REC")
    ledger = db.execute(
        "SELECT state, accepted_result_id FROM task_completion_recoveries WHERE task_id='TASK-REC'"
    ).fetchone()
    assert task.status is TaskStatus.IN_PROGRESS
    assert db.get_job("JOB-REC").status.value == "running"
    if winner == "callback":
        assert ledger["state"] == "callback_accepted"
        assert ledger["accepted_result_id"] == accepted_id
        assert db.get_task_results("TASK-REC")[0]["id"] == accepted_id
    elif winner == "cancelled":
        assert task.cancelled_at is not None
        assert ledger["state"] == "claimed"
    else:
        assert task.current_session_id == "newer-binding"
        assert ledger["state"] == "claimed"


def test_late_callback_after_restart_settlement_is_rejected_without_revival(tmp_path):
    db, orch, queue = _seed_org_with_orch(tmp_path)
    _claim_interrupted_recovery(db, "TASK-REC", binding="recovery")
    _sweep_on_startup(db, queue, "test", orch)

    assert not db.admit_task_completion_callback(
        task_id="TASK-REC", agent="dev_agent", session_id="recovery-TASK-REC",
        output_summary="late", confidence_score=90,
    )
    assert db.get_task("TASK-REC").status is TaskStatus.FAILED
    assert db.get_task_results("TASK-REC") == []


def test_sweep_in_progress_to_failed(tmp_path: Path) -> None:
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    db.update_task("T-1", status=TaskStatus.IN_PROGRESS)

    _sweep_on_startup(db, TaskQueue(), "test")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    # THR-079: null executor_pid → fail-closed with undeterminable liveness note.
    assert t.note and "liveness undeterminable" in t.note


def test_sweep_parked_delegated_with_all_children_terminal_reenqueues(tmp_path):
    """Path B Branch 2 (the landmine): a parent parked on its children is stored
    in_progress(delegated) — NOT blocked. The sweep MUST re-enqueue it when all
    children are terminal, and MUST NOT force-fail it as a 'running' task."""
    db = _seed_org(tmp_path)
    # Parent in_progress(DELEGATED), child completed — lost the wake-up signal
    # to the daemon crash.
    db.insert_task(TaskRecord(id="T-PAR", brief="p"))
    db.update_task("T-PAR", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(id="T-CHD", brief="c", parent_task_id="T-PAR"))
    db.update_task("T-CHD", status=TaskStatus.COMPLETED, note="done")

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    # Parent survives (not failed) AND is re-enqueued for its next decision step.
    assert db.get_task("T-PAR").status == TaskStatus.IN_PROGRESS
    assert db.get_task("T-PAR").block_kind == BlockKind.DELEGATED
    assert queue._queue.get_nowait() == ("test", "T-PAR", None)


def _seed_job(db: Database, job_id: str, task_id: str, status: str) -> None:
    """Insert a job row in the given status (bypasses the runner)."""
    from datetime import datetime, timezone

    from runtime.models import JobInterpreter, JobRecord
    db.insert_job(JobRecord(
        id=job_id, task_id=task_id, agent_name="dev_agent",
        title="t", rationale="r", script_text="echo x",
        interpreter=JobInterpreter.BASH,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    ))
    db._conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
    db._conn.commit()


def _seed_consumed_parent_handoff(db: Database) -> tuple[dict, dict]:
    db.insert_task(TaskRecord(
        id="TASK-PARENT-HANDOFF", brief="parent", team="engineering",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
        task_type="task",
    ))
    db.insert_task(TaskRecord(
        id="TASK-CHILD-HANDOFF", brief="child", team="engineering",
        assigned_agent="dev_agent", parent_task_id="TASK-PARENT-HANDOFF",
        status=TaskStatus.IN_PROGRESS, current_session_id="origin", task_type="subtask",
    ))
    assert db.claim_task_completion_recovery(task_id="TASK-CHILD-HANDOFF", agent="dev_agent", origin_session_id="origin", recovery_session_id="recovery", provider_session_id="provider", claimed_at="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:02:00Z")
    db.update_task("TASK-CHILD-HANDOFF", current_session_id="recovery")
    assert db.admit_task_completion_callback(task_id="TASK-CHILD-HANDOFF", agent="dev_agent", session_id="recovery", status="completed", output_summary="done", confidence_score=100, decision_json='{"action":"done","summary":"done"}')
    accepted = db.get_accepted_task_completion_recovery_result(task_id="TASK-CHILD-HANDOFF", agent="dev_agent")
    assert accepted is not None
    db.update_task("TASK-CHILD-HANDOFF", status=TaskStatus.COMPLETED)
    assert db.mark_task_completion_recovery_callback_consumed(task_id="TASK-CHILD-HANDOFF", agent="dev_agent", session_id="recovery", result_row_id=accepted["id"], settled_at="2026-01-01T00:01:00Z")
    return accepted, dict(accepted)


@pytest.mark.parametrize("captured_jobs,replace_before_handoff", [(0, False), (1, False), (0, True), (1, True)], ids=["zero-job", "one-job", "zero-job-winner", "one-job-winner"])
def test_consumed_recovery_parent_handoff_is_receipt_owned_through_shipping_caller(tmp_path, monkeypatch, captured_jobs, replace_before_handoff):
    """The actual shipping handoff owns only its captured receipt and parent wake."""
    from types import SimpleNamespace
    from runtime.daemon import jobs_runner
    from runtime.orchestrator import run_step

    db, orch, queue = _seed_org_with_orch(tmp_path)
    accepted, accepted_snapshot = _seed_consumed_parent_handoff(db)
    workers: list[threading.Thread] = []
    errors: list[BaseException] = []
    cleanup_done = threading.Event()
    parent_effects: list[str] = []
    real_thread = threading.Thread
    if captured_jobs:
        _seed_job(db, "JOB-OWNED", "TASK-CHILD-HANDOFF", "running")
        owned = SimpleNamespace(pid=111, returncode=None)
        jobs_runner._INFLIGHT["JOB-OWNED"] = owned
        monkeypatch.setattr(jobs_runner.os, "killpg", lambda pid, _sig: setattr(owned, "returncode", -15) if pid == 111 else None)
        async def no_wait(_seconds):
            return None
        monkeypatch.setattr(jobs_runner.asyncio, "sleep", no_wait)
    else:
        owned = None
    _seed_job(db, "JOB-UNRELATED", "TASK-UNRELATED", "running")
    unrelated = SimpleNamespace(pid=222, returncode=None)
    jobs_runner._INFLIGHT["JOB-UNRELATED"] = unrelated

    def tracked_thread(*, target, daemon):
        worker = real_thread(target=lambda: _capture_worker_error(target, errors), daemon=daemon)
        workers.append(worker)
        return worker
    monkeypatch.setattr(threading, "Thread", tracked_thread)

    if replace_before_handoff:
        original_handoff = db.handoff_consumed_task_completion_recovery_parent_effect
        def winner_then_original(**kwargs):
            db.update_task("TASK-CHILD-HANDOFF", current_session_id="winner")
            db.insert_task_result(task_id="TASK-CHILD-HANDOFF", agent="dev_agent", session_id="winner", status="completed", confidence_score=99, output_summary="winner")
            _seed_job(db, "JOB-WINNER", "TASK-CHILD-HANDOFF", "running")
            return original_handoff(**kwargs)
        monkeypatch.setattr(db, "handoff_consumed_task_completion_recovery_parent_effect", winner_then_original)

    try:
        run_step._handoff_consumed_recovery_terminal_effects(
            orch, "TASK-CHILD-HANDOFF", "dev_agent", "recovery", accepted["id"],
            TaskStatus.COMPLETED.value,
            after_recovery_cleanup=lambda: run_step._enqueue_parent_if_waiting(orch, "TASK-CHILD-HANDOFF"),
            after_recovery_parent_effect=lambda: parent_effects.append("receipt-owned"),
        )
        if workers:
            cleanup_done.set()
        for worker in workers:
            worker.join(2)
            assert not worker.is_alive(), "owned async cleanup thread leaked"
        assert not errors
        assert unrelated.returncode is None
        assert db.get_job("JOB-UNRELATED").status.value == "running"
        if replace_before_handoff:
            # The wrapped final caller just installed a same-agent winner;
            # the real handoff must not deliver the old receipt's parent tail.
            assert queue._queue.empty()
            assert parent_effects == []
        else:
            assert parent_effects == ["receipt-owned"]
        immutable_before_reentry = {
            "accepted": dict(db.execute("SELECT * FROM task_results WHERE id=?", (accepted["id"],)).fetchone()),
            "ledger": dict(db.execute("SELECT * FROM task_completion_recoveries WHERE task_id='TASK-CHILD-HANDOFF'").fetchone()),
            "binding": dict(db.execute("SELECT id, current_session_id, status, assigned_agent FROM tasks WHERE id='TASK-CHILD-HANDOFF'").fetchone()),
            "results": [dict(row) for row in db.execute("SELECT * FROM task_results WHERE task_id='TASK-CHILD-HANDOFF' ORDER BY id").fetchall()],
            "jobs": [dict(row) for row in db.execute("SELECT * FROM jobs WHERE task_id='TASK-CHILD-HANDOFF' ORDER BY id").fetchall()],
        }
        # Keep the real queue observer intact through both startup reentries;
        # deduplication, not queue draining, establishes at-most-once delivery.
        _sweep_on_startup(db, queue, "test", orch)
        _sweep_on_startup(db, queue, "test", orch)
        for worker in workers:
            worker.join(2)
            assert not worker.is_alive(), "startup reentry worker leaked"
        assert not errors
        if replace_before_handoff:
            # A later generic startup scan may legitimately reconstruct the
            # terminal child's ordinary parent wake. It is distinct from the
            # rejected recovery handoff and remains deduplicated across both
            # reentries without draining the queue between them.
            assert queue._queue.get_nowait() == ("test", "TASK-PARENT-HANDOFF", None)
            assert queue._queue.empty()
            assert db.get_task("TASK-CHILD-HANDOFF").current_session_id == "winner"
            assert db.get_job("JOB-WINNER").status.value == "running"
        else:
            assert queue._queue.get_nowait() == ("test", "TASK-PARENT-HANDOFF", None)
            assert queue._queue.empty()
            if captured_jobs:
                assert db.get_job("JOB-OWNED").reason == "task_ended"
        assert immutable_before_reentry["accepted"] == accepted_snapshot
        assert dict(db.execute("SELECT * FROM task_results WHERE id=?", (accepted["id"],)).fetchone()) == immutable_before_reentry["accepted"]
        assert dict(db.execute("SELECT * FROM task_completion_recoveries WHERE task_id='TASK-CHILD-HANDOFF'").fetchone()) == immutable_before_reentry["ledger"]
        assert dict(db.execute("SELECT id, current_session_id, status, assigned_agent FROM tasks WHERE id='TASK-CHILD-HANDOFF'").fetchone()) == immutable_before_reentry["binding"]
        assert [dict(row) for row in db.execute("SELECT * FROM task_results WHERE task_id='TASK-CHILD-HANDOFF' ORDER BY id").fetchall()] == immutable_before_reentry["results"]
        assert [dict(row) for row in db.execute("SELECT * FROM jobs WHERE task_id='TASK-CHILD-HANDOFF' ORDER BY id").fetchall()] == immutable_before_reentry["jobs"]
    finally:
        if owned is not None:
            jobs_runner._INFLIGHT.pop("JOB-OWNED", None)
        jobs_runner._INFLIGHT.pop("JOB-UNRELATED", None)


def test_consumed_recovery_parent_handoff_contender_attempts_same_rlock(tmp_path):
    """A real parent effect excludes a writer at the same database RLock acquisition."""
    from runtime.orchestrator import run_step

    db, orch, queue = _seed_org_with_orch(tmp_path)
    accepted, _ = _seed_consumed_parent_handoff(db)
    release, contender_attempt = threading.Event(), threading.Event()
    errors: list[BaseException] = []
    original_lock = db._lock

    class ObservedLock:
        def acquire(self, *args, **kwargs):
            if threading.current_thread().name == "owner-contender":
                contender_attempt.set()
            return original_lock.acquire(*args, **kwargs)
        def release(self):
            return original_lock.release()
        def __enter__(self):
            self.acquire()
            return self
        def __exit__(self, *exc):
            self.release()

    db._lock = ObservedLock()
    def contender_work() -> None:
        try:
            db.update_task("TASK-CHILD-HANDOFF", current_session_id="winner")
        except BaseException as exc:
            errors.append(exc)
    contender = threading.Thread(target=contender_work, name="owner-contender")
    def actual_parent_effect() -> None:
        run_step._enqueue_parent_if_waiting(orch, "TASK-CHILD-HANDOFF")
        contender.start()
        assert contender_attempt.wait(2), "contender never attempted the database RLock"
        assert contender.is_alive(), "contender acquired the lock during parent effect"
        assert release.wait(2), "test did not release parent effect"
    def handoff_work() -> None:
        try:
            run_step._handoff_consumed_recovery_terminal_effects(orch, "TASK-CHILD-HANDOFF", "dev_agent", "recovery", accepted["id"], TaskStatus.COMPLETED.value, after_recovery_cleanup=actual_parent_effect)
        except BaseException as exc:
            errors.append(exc)
    handoff = threading.Thread(target=handoff_work, name="handoff")
    try:
        handoff.start()
        assert contender_attempt.wait(2)
        release.set()
        handoff.join(2); contender.join(2)
        assert not handoff.is_alive() and not contender.is_alive(), "worker thread leaked"
    finally:
        release.set()
        handoff.join(2)
        if contender.ident is not None:
            contender.join(2)
        db._lock = original_lock
    assert not errors
    assert db.get_task("TASK-CHILD-HANDOFF").current_session_id == "winner"
    assert queue._queue.get_nowait() == ("test", "TASK-PARENT-HANDOFF", None)


def test_sweep_blocked_on_job_with_live_job_survives_restart(tmp_path):
    """Path B Branch 3 (THE LANDMINE, #1 reviewer focus): a task parked on a
    still-in-flight job is stored in_progress(blocked_on_job) with NO live
    subprocess. The pre-Path-B sweep had no branch for it (it was status=blocked
    and simply skipped); Path B makes it in_progress, so without the explicit
    Branch-3 exclusion it would fall into Branch 1 and be WRONGLY FAILED on
    every restart. Assert it SURVIVES untouched."""
    db = _seed_org(tmp_path)
    _seed_job(db, "JOB-1", "T-JOB", status="running")  # still in-flight
    db.insert_task(TaskRecord(id="T-JOB", brief="j"))
    db.update_task("T-JOB", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.BLOCKED_ON_JOB,
                   blocked_on_job_ids='["JOB-1"]', note="waiting on jobs")

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    # SURVIVES: not failed, still parked, NOT re-enqueued (job still in flight).
    t = db.get_task("T-JOB")
    assert t.status == TaskStatus.IN_PROGRESS
    assert t.block_kind == BlockKind.BLOCKED_ON_JOB
    assert queue._queue.empty()


def test_sweep_blocked_on_job_with_terminal_job_reenqueues(tmp_path):
    """Path B Branch 3: when every blocking job is terminal at restart (the job
    finished while the daemon was down), the parked task is re-enqueued — the
    orphaned wake-up the live jobs_runner hook missed."""
    db = _seed_org(tmp_path)
    _seed_job(db, "JOB-1", "T-JOB", status="completed")  # finished while down
    db.insert_task(TaskRecord(id="T-JOB", brief="j"))
    db.update_task("T-JOB", status=TaskStatus.IN_PROGRESS,
                   block_kind=BlockKind.BLOCKED_ON_JOB,
                   blocked_on_job_ids='["JOB-1"]', note="waiting on jobs")

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    # Not failed; re-enqueued for its resume step.
    assert db.get_task("T-JOB").status == TaskStatus.IN_PROGRESS
    assert queue._queue.get_nowait() == ("test", "T-JOB", None)


def test_sweep_leaves_escalated_alone(tmp_path):
    """Path B Branch 5: an escalated task (top-level status, founder-owned) is
    visited by the sweep — get_nonterminal_task_ids now yields it — and left
    untouched, mirroring the pre-Path-B blocked(escalated) fall-through."""
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    db.update_task("T-1", status=TaskStatus.ESCALATED, block_kind=None,
                   note="needs founder")

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.ESCALATED
    assert queue._queue.empty()


def test_sweep_blocked_delegated_with_live_child_bounded_wake(tmp_path):
    """THR-064 / TASK-573: when sweep force-fails an in-progress child of an
    in_progress(delegated) parent, the parent gets a bounded-wake decision step
    (enqueued, NOT cascade-failed). Since TASK-3604 there is no auto-revisit —
    the parked non-terminal ancestor means the bounded-wake recovers the work
    directly without spawning any successor."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="T-PAR", brief="p", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
        note="waiting",
        task_type="task",
    ))
    db.insert_task(TaskRecord(
        id="T-CHD", brief="c", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-PAR",
        status=TaskStatus.IN_PROGRESS,
        task_type="subtask",
    ))

    # Suppress feishu side effects from the real orch.
    orch.notify_failed = lambda **kw: None  # type: ignore[assignment]

    _sweep_on_startup(db, queue, "test", orch)

    # Child force-failed.
    assert db.get_task("T-CHD").status == TaskStatus.FAILED
    # TASK-573: parent stays in_progress(delegated) for bounded-wake, not FAILED.
    assert db.get_task("T-PAR").status == TaskStatus.IN_PROGRESS
    assert db.get_task("T-PAR").block_kind == BlockKind.DELEGATED
    # NO auto-revisit twin — parked non-terminal ancestor means skip.
    revisits = [
        t for t in (db.get_task(tid)
                    for tid in db.get_nonterminal_task_ids())
        if t is not None and t.revisit_of_task_id == "T-PAR"
    ]
    assert len(revisits) == 0, (
        f"expected 0 auto-revisit twins (parked ancestor skips it); "
        f"got {len(revisits)}"
    )
    # Queue gets ONLY the parent bounded-wake enqueue (no twin root).
    enqueued = []
    while not queue._queue.empty():
        enqueued.append(queue._queue.get_nowait())
    enqueued_ids = [tid for (_slug, tid, _md) in enqueued]
    assert "T-PAR" in enqueued_ids  # parent bounded-wake


def test_sweep_leaves_blocked_escalated_alone(tmp_path):
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-1", brief="x"))
    db.update_task("T-1", status=TaskStatus.ESCALATED, block_kind=None, note="halt")

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.ESCALATED
    assert t.block_kind is None
    assert queue._queue.empty()


def test_sweep_pending_stays_pending_but_gets_enqueued(tmp_path):
    """Pending rows from before the crash need a nudge — their original
    POST /tasks enqueue was lost when the daemon died."""
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-1", brief="x"))

    queue = TaskQueue()
    _sweep_on_startup(db, queue, "test")

    assert db.get_task("T-1").status == TaskStatus.PENDING
    assert queue._queue.get_nowait() == ("test", "T-1", None)


def test_sweep_works_without_orchestrator_arg(tmp_path):
    """Degraded mode: with no orchestrator (test convenience only — production
    always passes one), the IN_PROGRESS branch marks-failed-and-audits and
    does not enqueue or notify (no orchestrator wiring)."""
    db = _seed_org(tmp_path)
    db.insert_task(TaskRecord(id="T-BC", brief="x"))
    db.update_task("T-BC", status=TaskStatus.IN_PROGRESS)
    _sweep_on_startup(db, TaskQueue(), "test")
    assert db.get_task("T-BC").status == TaskStatus.FAILED
    actions = [r["action"] for r in db.get_audit_logs("T-BC")]
    assert "daemon_restart_failure" in actions
    # No auto-revisit in degraded mode.
    assert "auto_revisit_of" not in actions


def test_sweep_in_progress_bounded_parent_wake(tmp_path):
    """THR-064: in-progress child of a parked DELEGATED root at restart is
    terminal FAILED with no successor. Parent gets bounded-wake instead;
    no twin root since TASK-3604 removed daemon auto-revisit. notify_failed
    is suppressed because the work is being retried via parent re-enqueue."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    # Root parent task is in_progress+delegated waiting on its in-flight child.
    db.insert_task(TaskRecord(
        id="T-ROOT", brief="root work", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
        task_type="task",
    ))
    db.insert_task(TaskRecord(
        id="T-CHD", brief="child work", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-ROOT",
        status=TaskStatus.IN_PROGRESS,
        task_type="subtask",
    ))

    notify_calls: list[dict] = []
    orch.notify_failed = lambda **kw: notify_calls.append(kw)  # type: ignore[assignment]

    _sweep_on_startup(db, queue, "test", orch)

    # Child force-failed.
    assert db.get_task("T-CHD").status == TaskStatus.FAILED
    # TASK-573: bounded-wake, not cascade-fail.
    assert db.get_task("T-ROOT").status == TaskStatus.IN_PROGRESS
    assert db.get_task("T-ROOT").block_kind == BlockKind.DELEGATED
    # NO auto-revisit twin — parked non-terminal ancestor skips it.
    revisits = [
        t for t in (db.get_task(tid)
                    for tid in db.get_nonterminal_task_ids())
        if t is not None and t.revisit_of_task_id == "T-ROOT"
    ]
    assert len(revisits) == 0, (
        f"expected 0 auto-revisit twins (parked ancestor); got {len(revisits)}"
    )
    # notify_failed is suppressed because the work is being retried via parent wake.
    assert notify_calls == []


def test_sweep_per_root_dedup(tmp_path):
    """THR-064: two in-flight children of the same parked root at restart
    correctly skip auto-revisit (parked ancestor). No twin roots at all."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="T-ROOT", brief="root", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
    ))
    db.insert_task(TaskRecord(
        id="T-CHD-A", brief="a", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-ROOT",
        status=TaskStatus.IN_PROGRESS,
    ))
    db.insert_task(TaskRecord(
        id="T-CHD-B", brief="b", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-ROOT",
        status=TaskStatus.IN_PROGRESS,
    ))

    notify_calls: list[dict] = []
    orch.notify_failed = lambda **kw: notify_calls.append(kw)  # type: ignore[assignment]

    _sweep_on_startup(db, queue, "test", orch)

    revisits = [
        t for t in (db.get_task(tid)
                    for tid in db.get_nonterminal_task_ids())
        if t is not None and t.revisit_of_task_id == "T-ROOT"
    ]
    assert len(revisits) == 0, (
        f"expected 0 auto-revisit twins (parked ancestor); got {len(revisits)}"
    )
    # Both siblings still cascade-suppressed: zero founder pings.
    assert notify_calls == []


def test_lifespan_recovers_orphaned_running_jobs(tmp_home, daemon_state):
    """Job rows left in 'running' state on daemon startup are force-failed."""
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient

    from runtime.daemon.app import create_app
    from runtime.models import JobInterpreter, JobRecord, JobStatus

    org = daemon_state.orgs["alpha"]
    # Seed: insert a pending job then mark it running manually.
    job = JobRecord(
        id="JOB-001",
        task_id="TASK-001",
        agent_name="engineering_head",
        title="t",
        rationale="r",
        script_text="echo x",
        interpreter=JobInterpreter.BASH,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    org.db.insert_job(job)
    org.db._conn.execute(
        "UPDATE jobs SET status='running', started_at='2026-05-23T00:00:00Z' WHERE id='JOB-001'"
    )
    org.db._conn.commit()

    # Boot lifespan via TestClient context manager — startup hook fires.
    app = create_app(daemon_state)
    with TestClient(app):
        # Query inside the context so the DB is still open (lifespan teardown
        # calls close_all() on __exit__, after which the connection is gone).
        fetched = org.db.get_job("JOB-001")

    assert fetched is not None
    assert fetched.status == JobStatus.FAILED
    assert fetched.finished_at is not None
    # Recovery must distinguish a crash-orphan from a normal failure so the
    # founder UX and audit story preserve the cause.
    assert fetched.reason == "daemon_crash"


def test_terminate_all_inflight_awaits_runner_tasks(tmp_home, daemon_state):
    """Regression: clean shutdown must let in-flight runner tasks persist
    terminal state BEFORE the per-org DB is closed. Without this, a job sits
    in `running` until the next startup recovery scan."""
    import asyncio
    from datetime import datetime, timezone

    from runtime.daemon import jobs_runner
    from runtime.models import JobInterpreter, JobRecord, JobStatus

    org = daemon_state.orgs["alpha"]
    job = JobRecord(
        id="JOB-100",
        task_id="TASK-100",
        agent_name="engineering_head",
        title="t",
        rationale="r",
        script_text="x",
        interpreter=JobInterpreter.BASH,
        created_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    org.db.insert_job(job)
    org.db._conn.execute(
        "UPDATE jobs SET status='running' WHERE id='JOB-100'"
    )
    org.db._conn.commit()

    # Simulate a runner task that's still mid-flight: it sleeps briefly, then
    # transitions the row to FAILED. terminate_all_inflight must await this.
    async def fake_runner() -> None:
        await asyncio.sleep(0.05)
        org.db.transition_job_to_terminal(
            "JOB-100",
            status=JobStatus.FAILED,
            exit_code=-15,
            finished_at="2026-05-23T00:00:01Z",
            duration_ms=50,
            stdout_head="",
            stderr_head="killed by shutdown",
        )

    async def run_test() -> None:
        task = asyncio.create_task(fake_runner())
        jobs_runner.register_runner_task("JOB-100", task)
        # No subprocesses to kill — just await the runner task.
        await jobs_runner.terminate_all_inflight(
            grace_seconds=0, persist_timeout_seconds=2.0,
        )

    asyncio.run(run_test())

    fetched = org.db.get_job("JOB-100")
    assert fetched.status == JobStatus.FAILED, (
        "shutdown returned before the runner task persisted terminal state — "
        "row would have stayed `running` until next startup"
    )


# ── Thread invocation sweep (THR-046 message-112) ────────────────────────

def test_sweep_reconciles_pending_invocation_to_failed(tmp_path):
    """Branch 6: orphaned pending thread invocations are reaped to failed on
    daemon restart so the UI reply box (queued/working render) clears."""
    from runtime.daemon.routes.threads import _responder_entry

    db = _seed_org(tmp_path)
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alpha", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    assert inv.status.value == "pending"
    # Verify wire render is 'queued' (no started_at)
    wire_entry = _responder_entry({
        "agent_name": "alpha", "purpose": "reply", "status": "pending",
        "consumed_at": None, "started_at": None,
    })
    assert wire_entry.status == "queued"

    _sweep_on_startup(db, TaskQueue(), "test")

    # DB row is now terminal.
    reel = db.get_invocation_any_status(inv.invocation_token)
    assert reel is not None
    assert reel.status.value == "failed"
    assert reel.decline_reason == "daemon_restart"
    assert reel.consumed_at is not None

    # Wire render is now 'failed' (box clears).
    wire_after = _responder_entry({
        "agent_name": "alpha", "purpose": "reply", "status": reel.status.value,
        "consumed_at": reel.consumed_at.isoformat() if reel.consumed_at else None,
        "started_at": reel.started_at.isoformat() if reel.started_at else None,
    })
    assert wire_after.status == "failed", (
        f"expected wire status 'failed' after sweep; got '{wire_after.status}'"
    )


def test_sweep_reconciles_working_invocation_to_failed(tmp_path):
    """Branch 6: a started (working) pending invocation is also reaped to
    failed. The wire render flips from 'working' to 'failed'."""
    from datetime import datetime, timezone

    from runtime.daemon.routes.threads import _responder_entry

    db = _seed_org(tmp_path)
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alpha", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    # Simulate a subprocess that started before the daemon was killed.
    started_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    db._conn.execute(
        "UPDATE thread_invocations SET started_at = ? WHERE invocation_token = ?",
        (started_ts, inv.invocation_token),
    )
    db._conn.commit()

    # Wire renders 'working' because started_at is set.
    wire_entry = _responder_entry({
        "agent_name": "alpha", "purpose": "reply", "status": "pending",
        "consumed_at": None, "started_at": started_ts,
    })
    assert wire_entry.status == "working"

    _sweep_on_startup(db, TaskQueue(), "test")

    reel = db.get_invocation_any_status(inv.invocation_token)
    assert reel is not None
    assert reel.status.value == "failed"
    assert reel.decline_reason == "daemon_restart"

    wire_after = _responder_entry({
        "agent_name": "alpha", "purpose": "reply", "status": reel.status.value,
        "consumed_at": reel.consumed_at.isoformat() if reel.consumed_at else None,
        "started_at": reel.started_at.isoformat() if reel.started_at else None,
    })
    assert wire_after.status == "failed"


def test_sweep_reconciles_all_threads_pending_invocations(tmp_path):
    """Branch 6: reaps pending invocations across ALL threads, not just open
    ones. A pending invocation is orphaned regardless of thread status."""
    from runtime.daemon.routes.threads import _responder_entry

    db = _seed_org(tmp_path)
    # Thread 1 — has pending invocation
    db.insert_thread(ThreadRecord(id="THR-001", subject="open"))
    inv1 = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alpha", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    # Thread 2 — archived thread (past conversation), also has pending
    db.insert_thread(ThreadRecord(id="THR-002", subject="archived"))
    db.set_thread_status("THR-002", status=ThreadStatus.ARCHIVED)
    inv2 = db.mint_thread_invocation(
        thread_id="THR-002", agent_name="bravo", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )

    _sweep_on_startup(db, TaskQueue(), "test")

    for token in (inv1.invocation_token, inv2.invocation_token):
        reel = db.get_invocation_any_status(token)
        assert reel is not None
        assert reel.status.value == "failed", (
            f"invocation {token} should be failed after sweep, got "
            f"{reel.status.value}"
        )
        assert reel.decline_reason == "daemon_restart"


def test_sweep_leaves_already_terminal_invocations_alone(tmp_path):
    """Branch 6: terminal invocations (consumed, declined, timeout) are NOT
    touched — only genuinely pending rows are reaped."""
    from runtime.daemon.routes.threads import _responder_entry

    db = _seed_org(tmp_path)
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))

    # Already-consumed invocation.
    consumed = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alpha", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    db.consume_invocation(consumed.invocation_token)

    # Already-declined.
    declined = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="bravo", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    db.mark_invocation_declined(declined.invocation_token, decline_reason="agent_declined")

    _sweep_on_startup(db, TaskQueue(), "test")

    # Consumed stays consumed.
    c = db.get_invocation_any_status(consumed.invocation_token)
    assert c is not None and c.status.value == "consumed", \
        f"consumed invocation was altered to {c.status.value if c else 'None'}"

    # Declined stays declined.
    d = db.get_invocation_any_status(declined.invocation_token)
    assert d is not None and d.status.value == "declined", \
        f"declined invocation was altered to {d.status.value if d else 'None'}"


# ── THR-064: daemon-restart double-recovery (TASK-1855) ──────────────────

def test_thr064_parked_ancestor_bounded_wake(tmp_path):
    """THR-064: A parked manager root (in_progress/DELEGATED) with ONE running
    child at restart -> EXACTLY ONE woken root (parent bounded-wake), NO twin
    successor (auto-revisit removed in TASK-3604), killed child FAILED with
    the restart marker note."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="T-ROOT", brief="root", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
        task_type="task",
    ))
    db.insert_task(TaskRecord(
        id="T-CHD", brief="child", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-ROOT",
        status=TaskStatus.IN_PROGRESS,
        task_type="subtask",
    ))

    orch.notify_failed = lambda **kw: None  # type: ignore[assignment]

    _sweep_on_startup(db, queue, "test", orch)

    # Child force-failed (null executor_pid → fail-closed).
    chd = db.get_task("T-CHD")
    assert chd.status == TaskStatus.FAILED
    assert "liveness undeterminable" in (chd.note or "")

    # Parent stays parked (bounded-wake), NOT cascade-failed.
    par = db.get_task("T-ROOT")
    assert par.status == TaskStatus.IN_PROGRESS
    assert par.block_kind == BlockKind.DELEGATED

    # NO auto-revisit twin — parked ancestor recovery via bounded-wake instead.
    revisits = [
        t for t in (db.get_task(tid)
                    for tid in db.get_nonterminal_task_ids())
        if t is not None and t.revisit_of_task_id == "T-ROOT"
    ]
    assert len(revisits) == 0, (
        f"expected 0 auto-revisit twins; got {len(revisits)}"
    )

    # Parent is enqueued (bounded-wake recovery).
    enqueued = []
    while not queue._queue.empty():
        enqueued.append(queue._queue.get_nowait())
    enqueued_ids = [tid for (_slug, tid, _md) in enqueued]
    assert "T-ROOT" in enqueued_ids, (
        f"expected parent T-ROOT to be enqueued for bounded-wake; "
        f"got {enqueued_ids}"
    )


def test_thr064_guardrail1_worker_root_terminal_failed(tmp_path):
    """THR-079 / TASK-3604: a genuine parentless worker root subprocess death
    (no parked non-terminal ancestor) is terminal FAILED with no daemon
    successor. The THR-079 pid-liveness probe plus TASK-3604's removal of
    auto-revisit means only a daemon_restart_failure audit row is written;
    recovery is explicit founder action."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    # A root task with NO parent and NO block_kind — a genuine worker root.
    db.insert_task(TaskRecord(
        id="T-WORKER", brief="worker root", team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.IN_PROGRESS,
        task_type="task",
    ))

    orch.notify_failed = lambda **kw: None  # type: ignore[assignment]

    _sweep_on_startup(db, queue, "test", orch)

    # Killed task is failed (null executor_pid → fail-closed).
    t = db.get_task("T-WORKER")
    assert t.status == TaskStatus.FAILED

    # THR-079: NO auto-revisit twin — pid-liveness probe supersedes auto-revisit.
    revisits = [
        t for t in (db.get_task(tid)
                    for tid in db.get_nonterminal_task_ids())
        if t is not None and t.revisit_of_task_id == "T-WORKER"
    ]
    assert len(revisits) == 0, (
        f"THR-079: expected 0 auto-revisit twins (pid-liveness probe supersedes auto-revisit); "
        f"got {len(revisits)}"
    )


def test_thr064_fanout_killed_child_does_not_wake_parent_early(tmp_path):
    """THR-064 RED-FIRST (fails on merge-base b96e105, passes on branch head).

    Fan-out barrier: a restart-killed child among still-live siblings MUST
    NOT wake the parked root early. Only when all children terminal does the
    parent wake."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="T-ROOT", brief="root", team="engineering",
        assigned_agent="engineering_head",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
        task_type="task",
    ))
    # Two children — one killed by restart, one blocked_on_job (survives sweep).
    _seed_job(db, "JOB-SIB", "T-CHD-B", status="running")
    db.insert_task(TaskRecord(
        id="T-CHD-A", brief="killed child", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-ROOT",
        status=TaskStatus.IN_PROGRESS,
        task_type="subtask",
    ))
    db.insert_task(TaskRecord(
        id="T-CHD-B", brief="job-blocked sibling", team="engineering",
        assigned_agent="dev_agent", parent_task_id="T-ROOT",
        status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.BLOCKED_ON_JOB,
        blocked_on_job_ids='["JOB-SIB"]',
        task_type="subtask",
    ))

    orch.notify_failed = lambda **kw: None  # type: ignore[assignment]

    _sweep_on_startup(db, queue, "test", orch)

    # Killed child is failed.
    assert db.get_task("T-CHD-A").status == TaskStatus.FAILED
    # Job-blocked sibling survives the sweep (BLOCKED_ON_JOB, Branch 3 guard).
    assert db.get_task("T-CHD-B").status == TaskStatus.IN_PROGRESS, (
        f"job-blocked sibling should survive; got {db.get_task('T-CHD-B').status}"
    )

    # Parent is NOT enqueued early — sibling still in_progress blocks the wake.
    assert queue._queue.empty(), (
        "fan-out barrier violated: parent was woken before all siblings terminal"
    )

    # NO auto-revisit twin (parked ancestor exists → skip).
    revisits = [
        t for t in (db.get_task(tid)
                    for tid in db.get_nonterminal_task_ids())
        if t is not None and t.revisit_of_task_id == "T-ROOT"
    ]
    assert len(revisits) == 0, (
        f"expected 0 auto-revisit twins; got {len(revisits)}"
    )


def test_thread_queue_wiring_before_task_workers_prevents_enqueue_unavailable(
    tmp_home, daemon_state,
):
    """Regression THR-109: daemon lifespan MUST wire thread queues + main loop
    BEFORE starting task workers.

    This test exercises two complementary verification surfaces:

    **A. Source-order guard (deterministic).** Uses ``inspect.getsource``
    to verify the two statements inside ``_wire_then_start_workers``
    appear in the correct order: ``_attach_thread_queue_wiring`` before
    ``ensure_workers_started``.  This is a pure code-level check that
    fails immediately if the helper's internal ordering is ever reversed —
    no runtime race, no sleep, no non-determinism.

    **B. Behavioural regression (runtime, deterministic sync).** Calls the SAME
    production helper that ``_lifespan`` invokes via a background event loop.
    A test-local wrapper around the real ``ThreadQueue.put`` records every
    ``ThreadJob`` and signals a ``threading.Event`` after its ``await``
    completes, so the test waits on explicit delivery proof — never DB polling
    or arbitrary sleep.  Real queue workers pick up a pre-seeded terminal task,
    exercise the exact ``_maybe_post_thread_escalation`` →
    ``_append_followup_system_and_reinvoke`` code path, and the test asserts
    the post-fix outcomes.

    With the post-fix helper ordering (wiring before workers):
    - (1) a TASK_FOLLOWUP invocation is minted and its ``ThreadJob`` is
      delivered through the real ``ThreadQueue.put`` (deterministic signal)
    - (2) no thread_followup_skipped(enqueue_unavailable) audit is written
    - (3) the task reaches its normal terminal state after the follow-up

    The unchanged test must fail if ONLY ``_wire_then_start_workers`` call
    order in app.py is temporarily reversed (red-side proof, not committed).
    """
    import asyncio
    import inspect
    import threading

    from runtime.daemon.app import _wire_then_start_workers
    from runtime.models import (
        TaskRecord, TaskStatus, ThreadInvocationPurpose, ThreadRecord,
    )

    # ── A. Source-order guard (deterministic) ──
    src = inspect.getsource(_wire_then_start_workers)
    wire_idx = src.index("_attach_thread_queue_wiring")
    workers_idx = src.index("ensure_workers_started")
    assert wire_idx < workers_idx, (
        f"_wire_then_start_workers: _attach_thread_queue_wiring "
        f"(idx {wire_idx}) must precede ensure_workers_started "
        f"(idx {workers_idx}); ordering is reversed — TASK_FOLLOWUP "
        f"invocations will strand with enqueue_unavailable"
    )

    # ── B. Behavioural regression (runtime, deterministic sync) ──

    org = daemon_state.orgs["alpha"]
    db = org.db
    orch = org.orchestrator
    audit = orch._audit
    thread_queue = org.thread_queue

    # ── Deterministic delivery signal: wrap ThreadQueue.put ──
    # A test-local async wrapper around the real ThreadQueue.put that records
    # the ThreadJob and signals a threading.Event *after* the await completes.
    # This guarantees the job is actually enqueued in the asyncio.Queue before
    # the test proceeds — no DB polling, no sleep, no race window.
    delivery_event = threading.Event()
    delivered_job = None
    _original_put = thread_queue.put

    async def _wrapped_put(job):
        nonlocal delivered_job
        await _original_put(job)
        delivered_job = job
        delivery_event.set()

    thread_queue.put = _wrapped_put  # type: ignore[method-assign]

    # Seed an OPEN thread + dispatched PENDING root task.
    db.insert_thread(ThreadRecord(id="THR-STRT", subject="startup ordering"))
    db.add_thread_participant("THR-STRT", "engineering_head", added_by="founder")
    db.insert_task(TaskRecord(
        id="TASK-STRT", brief="startup test", team="engineering",
        assigned_agent="engineering_head",
        dispatched_from_thread_id="THR-STRT",
    ))
    audit.log_thread_dispatch(
        "THR-STRT", task_id="TASK-STRT", dispatcher="engineering_head",
        target_agent="engineering_head", team="engineering",
    )

    # Enqueue the PENDING task so workers pick it up immediately on start.
    daemon_state.queue.put_nowait(org.slug, "TASK-STRT")

    # Start a real event loop in a background daemon thread.
    # Mirrors the daemon lifespan where FastAPI/uvicorn runs the loop.
    loop = asyncio.new_event_loop()
    bg_thread = threading.Thread(target=loop.run_forever, daemon=True)
    bg_thread.start()

    try:
        # Call the same production helper that _lifespan uses.
        async def _start():
            _wire_then_start_workers(daemon_state, loop)
        asyncio.run_coroutine_threadsafe(_start(), loop).result(timeout=5.0)

        # Wait for deterministic delivery proof — the wrapped put signals
        # delivery_event AFTER the real asyncio.Queue.put await completes.
        # This replaces the flaky DB-polling loop.
        assert delivery_event.wait(timeout=10.0), (
            "ThreadJob was never delivered via ThreadQueue.put within 10s; "
            "thread queue wiring is not in place before workers started — "
            "TASK_FOLLOWUP invocations will strand with enqueue_unavailable"
        )

        # (1) TASK_FOLLOWUP invocation is minted and the delivered ThreadJob
        #     matches the minted invocation token.
        invs = db.list_thread_invocations("THR-STRT")
        followups = [
            i for i in invs
            if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP
        ]
        assert len(followups) >= 1, (
            f"expected \u22651 TASK_FOLLOWUP invocation, got {len(followups)}"
        )
        assert delivered_job.org_slug == org.slug
        assert delivered_job.invocation_token == followups[0].invocation_token, (
            f"delivered job token {delivered_job.invocation_token} "
            f"!= minted token {followups[0].invocation_token}"
        )

        # (2) No enqueue_unavailable audit row was written.
        audit_rows = db.get_audit_logs("TASK-STRT")
        skipped = [
            r for r in audit_rows
            if r.get("action") == "thread_followup_skipped"
            and "enqueue_unavailable" in str(r.get("payload", {}))
        ]
        assert not skipped, (
            f"found enqueue_unavailable audit -- thread queue wiring was "
            f"not in place before escalation fired: {skipped}"
        )

        # (3) Normal nearby lifecycle: the task ran through the follow-up path
        # and reached its ordinary terminal outcome.  The test fixture has no
        # runnable executor, so that outcome is FAILED; it must not rely on a
        # retired total-step denial to reach it.
        t = db.get_task("TASK-STRT")
        assert t.status == TaskStatus.FAILED
        assert "agent invocation failed" in (t.note or "")

        # Consume the delivered job from the real queue (do not start the
        # daemon ThreadInvocationRunner — consuming the observed job is
        # sufficient and prevents an unrelated agent execution).
        fut = asyncio.run_coroutine_threadsafe(thread_queue.get(), loop)
        job = fut.result(timeout=2.0)
        assert job.invocation_token == delivered_job.invocation_token
    finally:
        # Restore the original put before cleanup.
        thread_queue.put = _original_put  # type: ignore[method-assign]
        # Stop workers gracefully before tearing down the loop.
        async def _stop():
            await daemon_state.queue.stop(timeout=2.0)
        try:
            asyncio.run_coroutine_threadsafe(_stop(), loop).result(timeout=3.0)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        bg_thread.join(timeout=2.0)


# ── Orphaned result local_ci reconstruction ──────────────────────────────

def test_sweep_orphaned_result_preserves_local_ci_in_audit_and_consumption(tmp_path):
    """An orphaned task_result with valid local_ci must be carried into
    the CompletionReport consumed by the sweep. The audit event must contain
    the exact local_ci object, and the task must reach COMPLETED."""
    from runtime.models import LocalCiEvidence

    db, orch, queue = _seed_org_with_orch(tmp_path)

    db.insert_task(TaskRecord(
        id="TASK-ORPH-LC", brief="x", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
        task_type="task",
    ))
    db.update_task("TASK-ORPH-LC", current_session_id="sess-orph-lc")

    db.insert_task_result(
        task_id="TASK-ORPH-LC",
        agent="dev_agent",
        session_id="sess-orph-lc",
        status="completed",
        output_summary="Done with CI evidence",
        confidence_score=95,
        decision_json='{"action":"done","summary":"Done with CI evidence"}',
        local_ci_json='{"command":"scripts/local_ci.sh all","exit_code":0}',
    )

    _sweep_on_startup(db, queue, "test", orch)

    t = db.get_task("TASK-ORPH-LC")
    assert t.status == TaskStatus.COMPLETED, (
        f"expected COMPLETED, got {t.status}"
    )

    # Verify the audit payload contains the exact local_ci.
    logs = db.get_audit_logs("TASK-ORPH-LC")
    completion_audits = [r for r in logs if r["action"] == "completion_report"]
    assert len(completion_audits) >= 1, (
        f"expected at least one completion_report audit; got {logs}"
    )
    payload = completion_audits[0]["payload"]
    assert payload.get("local_ci") == {
        "command": "scripts/local_ci.sh all",
        "exit_code": 0,
    }, f"audit payload missing local_ci: {payload}"


def test_sweep_orphaned_thread_originated_supersede_escalates_once(tmp_path, monkeypatch):
    """A rejected thread-origin decision is durable and cannot replay."""
    import json

    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="T-STARTUP-SUP", brief="original", team="engineering",
        assigned_agent="engineering_head", status=TaskStatus.IN_PROGRESS,
        current_session_id="sess-startup",
    ))
    db.execute(
        "UPDATE tasks SET dispatched_from_thread_id = 'THR-152' WHERE id = 'T-STARTUP-SUP'"
    )
    db._conn.commit()
    db.insert_task_result(
        task_id="T-STARTUP-SUP", agent="engineering_head", session_id="sess-startup",
        status="completed", confidence_score=90, output_summary="replace",
        decision_json=json.dumps({
            "action": "supersede", "successor_brief": "replacement",
            "rationale": "new evidence",
            "attestation": {
                "recovery_reason": "Evidence invalidated the old plan.",
                "policy_product_intent_unchanged": True,
                "no_budget_or_external_commitment": True,
                "no_permission_or_cross_team_change": True,
                "no_schema_auth_security_privacy_or_data_access_change": True,
                "no_unresolved_founder_gate": True,
            },
        }),
    )
    monkeypatch.setenv("HAPPYRANCH_MANAGER_SUPERSESSION_ENABLED", "1")
    monkeypatch.setenv("HAPPYRANCH_MANAGER_SUPERSESSION_PILOT_TEAM", "engineering")

    _sweep_on_startup(db, queue, "test", orch)

    task = db.get_task("T-STARTUP-SUP")
    assert task.status is TaskStatus.ESCALATED
    assert task.note == (
        "manager supersession rejected: thread-origin roots are not eligible "
        "for supersession; founder action required"
    )
    assert db.execute("SELECT COUNT(*) FROM manager_supersessions").fetchone()[0] == 0
    assert queue._queue.empty()
    logs = db.get_audit_logs("T-STARTUP-SUP")
    assert [row["action"] for row in logs].count("orchestration_step") == 1
    escalation = next(row for row in logs if row["action"] == "escalation")
    assert escalation["payload"] == {"reason": task.note}
    authority = next(row for row in logs if row["action"] == "authority_hook")
    assert authority["payload"] == {
        "outcome": "not_applicable",
        "reason_code": "runtime_manager_supersession_thread_origin_ineligible",
        "reason": "runtime-raised escalation is not an authority decision",
        "causal_escalation_audit_id": escalation["id"],
    }

    # Escalated rows are outside both startup and zombie recovery allowlists.
    # A replay pass therefore cannot consume the same decision a second time.
    _sweep_on_startup(db, queue, "test", orch)
    replay_logs = db.get_audit_logs("T-STARTUP-SUP")
    assert [row["action"] for row in replay_logs].count("orchestration_step") == 1
    assert [row["action"] for row in replay_logs].count("escalation") == 1


def test_sweep_orphaned_non_thread_supersede_still_commits_atomically(tmp_path, monkeypatch):
    """The rejection correction does not narrow ordinary eligible supersession."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_task(TaskRecord(
        id="T-STARTUP-ELIGIBLE", brief="original", team="engineering",
        assigned_agent="engineering_head", status=TaskStatus.IN_PROGRESS,
        current_session_id="sess-eligible",
    ))
    db.insert_task_result(
        task_id="T-STARTUP-ELIGIBLE", agent="engineering_head",
        session_id="sess-eligible", status="completed", confidence_score=90,
        output_summary="replace", decision_json=json.dumps({
            "action": "supersede", "successor_brief": "replacement",
            "rationale": "new evidence", "attestation": {
                "recovery_reason": "Evidence invalidated the old plan.",
                "policy_product_intent_unchanged": True,
                "no_budget_or_external_commitment": True,
                "no_permission_or_cross_team_change": True,
                "no_schema_auth_security_privacy_or_data_access_change": True,
                "no_unresolved_founder_gate": True,
            },
        }),
    )
    monkeypatch.setenv("HAPPYRANCH_MANAGER_SUPERSESSION_ENABLED", "1")
    monkeypatch.setenv("HAPPYRANCH_MANAGER_SUPERSESSION_PILOT_TEAM", "engineering")

    _sweep_on_startup(db, queue, "test", orch)

    predecessor = db.get_task("T-STARTUP-ELIGIBLE")
    assert predecessor.status is TaskStatus.SUPERSEDED
    relation = db.execute("SELECT * FROM manager_supersessions").fetchone()
    successor = db.get_task(relation["successor_task_id"])
    assert successor.status is TaskStatus.PENDING
    assert queue._queue.get_nowait() == ("test", successor.id, None)


def test_sweep_supersede_competing_cancellation_remains_silent_race(tmp_path, monkeypatch):
    """A cancellation winning the supersession CAS retains its terminal state."""
    db, orch, queue = _seed_org_with_orch(tmp_path)
    task_id = "T-STARTUP-RACE"
    db.insert_task(TaskRecord(
        id=task_id, brief="original", team="engineering",
        assigned_agent="engineering_head", status=TaskStatus.IN_PROGRESS,
        current_session_id="sess-race",
    ))
    db.insert_task_result(
        task_id=task_id, agent="engineering_head", session_id="sess-race",
        status="completed", confidence_score=90, output_summary="replace",
        decision_json=json.dumps({
            "action": "supersede", "successor_brief": "replacement",
            "rationale": "new evidence", "attestation": {
                "recovery_reason": "Evidence invalidated the old plan.",
                "policy_product_intent_unchanged": True,
                "no_budget_or_external_commitment": True,
                "no_permission_or_cross_team_change": True,
                "no_schema_auth_security_privacy_or_data_access_change": True,
                "no_unresolved_founder_gate": True,
            },
        }),
    )

    def cancellation_wins(*args, **kwargs):
        db.update_task(
            task_id, status=TaskStatus.CANCELLED,
            cancelled_at="2026-09-06T00:00:00+00:00",
            completed_at="2026-09-06T00:00:00+00:00",
            note="cancelled by founder",
        )
        return None

    monkeypatch.setattr(db, "try_manager_supersede", cancellation_wins)
    monkeypatch.setenv("HAPPYRANCH_MANAGER_SUPERSESSION_ENABLED", "1")
    monkeypatch.setenv("HAPPYRANCH_MANAGER_SUPERSESSION_PILOT_TEAM", "engineering")

    _sweep_on_startup(db, queue, "test", orch)

    assert db.get_task(task_id).status is TaskStatus.CANCELLED
    assert db.execute("SELECT COUNT(*) FROM manager_supersessions").fetchone()[0] == 0
    assert not any(
        row["action"] == "escalation" for row in db.get_audit_logs(task_id)
    )
    assert queue._queue.empty()


def test_sweep_orphaned_result_malformed_local_ci_does_not_crash(tmp_path):
    """An orphaned task_result with malformed local_ci JSON must not crash
    the sweep or alter its preexisting restart behavior. The task is still
    consumed; local_ci is None."""
    import json as _json

    db, orch, queue = _seed_org_with_orch(tmp_path)

    db.insert_task(TaskRecord(
        id="TASK-ORPH-MAL", brief="x", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
        task_type="task",
    ))
    db.update_task("TASK-ORPH-MAL", current_session_id="sess-orph-mal")

    # Use insert_task_result without local_ci_json, then directly set
    # malformed JSON via the connection to bypass normal serialization.
    db.insert_task_result(
        task_id="TASK-ORPH-MAL",
        agent="dev_agent",
        session_id="sess-orph-mal",
        status="completed",
        output_summary="Done with bad CI evidence",
        confidence_score=95,
        decision_json='{"action":"done","summary":"Done with bad CI evidence"}',
    )
    db._conn.execute(
        "UPDATE task_results SET local_ci = ? "
        "WHERE task_id = ? AND session_id = ?",
        ("NOT VALID JSON", "TASK-ORPH-MAL", "sess-orph-mal"),
    )
    db._conn.commit()

    # Must not crash.
    _sweep_on_startup(db, queue, "test", orch)

    t = db.get_task("TASK-ORPH-MAL")
    assert t.status == TaskStatus.COMPLETED, (
        f"expected COMPLETED (malformed local_ci should not block consumption), "
        f"got {t.status}"
    )

    # The audit payload must still exist but with local_ci as null/absent.
    logs = db.get_audit_logs("TASK-ORPH-MAL")
    completion_audits = [r for r in logs if r["action"] == "completion_report"]
    assert len(completion_audits) >= 1
    payload = completion_audits[0]["payload"]
    assert payload.get("local_ci") is None, (
        f"malformed local_ci should degrade to None; got {payload.get('local_ci')}"
    )


def test_terminal_job_backstop_serializes_cleanup_before_shipping_startup_update_commit(
    tmp_path, monkeypatch, request,
):
    """The terminal cleanup helper owns its UPDATE/commit before startup.

    This is intentionally a real shared SQLite connection test.  The proxy
    stops the owned-RUNNING UPDATE immediately before its actual commit; a
    concurrent shipping startup sweep then demonstrably attempts the same database
    lock and cannot acquire it until the cleanup commit is released.
    """
    from runtime.daemon import jobs_runner
    from runtime.models import JobStatus
    from runtime.orchestrator import run_step

    db, orch, _queue = _seed_org_with_orch(tmp_path)
    _seed_job(db, "JOB-OWNED", "TASK-TERMINAL", "running")
    _seed_job(db, "JOB-TERMINAL", "TASK-TERMINAL", "completed")
    _seed_job(db, "JOB-OTHER", "TASK-OTHER", "running")

    update_at_boundary = threading.Event()
    release_cleanup_commit = threading.Event()
    startup_attempted_lock = threading.Event()
    cleanup_thread: list[threading.Thread] = []
    errors: list[BaseException] = []
    original_connection = db._conn
    original_lock = db._lock
    original_thread = threading.Thread

    def release_and_join() -> None:
        # Never strand the paused production cleanup if an assertion fails.
        release_cleanup_commit.set()
        for thread in cleanup_thread:
            thread.join(2)
            assert not thread.is_alive(), "cleanup thread leaked during failure cleanup"

    request.addfinalizer(release_and_join)

    class PausingConnection:
        def execute(self, sql, *args, **kwargs):
            cursor = original_connection.execute(sql, *args, **kwargs)
            if sql.startswith("UPDATE jobs SET status='failed'"):
                update_at_boundary.set()
                assert release_cleanup_commit.wait(2), "test did not release commit"
            return cursor

        def __getattr__(self, name):
            return getattr(original_connection, name)

    class ObservableLock:
        def acquire(self, *args, **kwargs):
            if update_at_boundary.is_set():
                startup_attempted_lock.set()
            return original_lock.acquire(*args, **kwargs)

        def release(self):
            return original_lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.release()
            return False

    def tracked_thread(*args, **kwargs):
        target = kwargs.get("target")
        if target is not None:
            def capture_cleanup_error(*target_args, **target_kwargs):
                try:
                    return target(*target_args, **target_kwargs)
                except BaseException as exc:
                    errors.append(exc)
            kwargs["target"] = capture_cleanup_error
        thread = original_thread(*args, **kwargs)
        cleanup_thread.append(thread)
        return thread

    async def no_processes(*args, **kwargs):
        return []

    db._conn = PausingConnection()
    db._lock = ObservableLock()
    monkeypatch.setattr(jobs_runner, "terminate_jobs_for_task", no_processes)
    monkeypatch.setattr(threading, "Thread", tracked_thread)

    # The prior raw shared-connection seam is a deterministic red control:
    # BEGIN IMMEDIATE sees the uncommitted UPDATE and fails nested-transaction.
    original_connection.execute("UPDATE jobs SET status='running' WHERE id='JOB-OWNED'")
    try:
        with pytest.raises(Exception, match="transaction"):
            original_connection.execute("BEGIN IMMEDIATE")
    finally:
        original_connection.rollback()

    run_step._kill_jobs_for_terminating_task(orch, "TASK-TERMINAL")
    assert update_at_boundary.wait(2), "cleanup never reached UPDATE-to-commit"

    def startup_sweep() -> None:
        try:
            # Use the actual startup entrypoint.  Its final raw orphan-reap
            # UPDATE-to-commit is the previously unprotected shared-connection
            # window; it must wait behind the owned-job terminal backstop.
            _sweep_on_startup(db, TaskQueue(), "test", orch)
        except BaseException as exc:  # worker errors must reach the test
            errors.append(exc)

    contender = original_thread(target=startup_sweep)
    contender.start()
    assert startup_attempted_lock.wait(2), "startup did not attempt DB lock"
    assert contender.is_alive(), "startup acquired the lock before cleanup commit"
    release_cleanup_commit.set()
    contender.join(2)
    assert not contender.is_alive(), "startup thread leaked"
    assert not errors
    assert len(cleanup_thread) == 1
    cleanup_thread[0].join(2)
    assert not cleanup_thread[0].is_alive(), "cleanup thread leaked"

    owned = db.get_job("JOB-OWNED")
    terminal = db.get_job("JOB-TERMINAL")
    unrelated = db.get_job("JOB-OTHER")
    assert owned is not None and owned.status is JobStatus.FAILED
    assert owned.reason == "task_ended"
    assert terminal is not None and terminal.status is JobStatus.COMPLETED
    assert unrelated is not None and unrelated.status is JobStatus.RUNNING


def test_shipping_startup_invocation_commit_serializes_owned_job_backstop(
    tmp_path, request,
):
    """The opposite shared-SQLite direction is finite and durable.

    Pause the shipping startup ``thread_invocations`` UPDATE just before its
    commit while it owns ``Database._lock``.  The real owned-job backstop on
    the same connection must wait, then persist only its owned terminal row
    after startup releases; unrelated rows retain their ordinary state.
    """
    from runtime.models import JobStatus

    db, orch, queue = _seed_org_with_orch(tmp_path)
    db.insert_thread(ThreadRecord(id="THR-LOCK", subject="lock"))
    governed = db.mint_thread_invocation(
        thread_id="THR-LOCK", agent_name="dev_agent", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    db.insert_thread(ThreadRecord(id="THR-UNRELATED", subject="unrelated"))
    unrelated = db.mint_thread_invocation(
        thread_id="THR-UNRELATED", agent_name="other", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    assert db.consume_invocation(unrelated.invocation_token)
    _seed_job(db, "JOB-OWNED", "TASK-TERMINAL", "running")
    _seed_job(db, "JOB-OTHER", "TASK-OTHER", "running")

    update_reached = threading.Event()
    cleanup_attempted_lock = threading.Event()
    release_startup = threading.Event()
    startup_errors: list[BaseException] = []
    cleanup_errors: list[BaseException] = []
    original_connection, original_lock = db._conn, db._lock

    class PausingConnection:
        def execute(self, sql, *args, **kwargs):
            cursor = original_connection.execute(sql, *args, **kwargs)
            if sql.startswith("UPDATE thread_invocations SET status = 'failed'"):
                update_reached.set()
                assert release_startup.wait(2), "test did not release startup commit"
            return cursor

        def __getattr__(self, name):
            return getattr(original_connection, name)

    class ObservableLock:
        def acquire(self, *args, **kwargs):
            if threading.current_thread() is cleanup_thread:
                cleanup_attempted_lock.set()
            return original_lock.acquire(*args, **kwargs)

        def release(self):
            return original_lock.release()

        def __enter__(self):
            self.acquire()
            return self

        def __exit__(self, exc_type, exc, traceback):
            self.release()
            return False

    db._conn, db._lock = PausingConnection(), ObservableLock()

    def startup() -> None:
        try:
            _sweep_on_startup(db, queue, "test", orch)
        except BaseException as exc:
            startup_errors.append(exc)

    def cleanup() -> None:
        try:
            db.backstop_terminated_task_jobs(
                "TASK-TERMINAL", finished_at="2026-01-01T00:00:00Z",
            )
        except BaseException as exc:
            cleanup_errors.append(exc)

    startup_thread = threading.Thread(target=startup)
    cleanup_thread = threading.Thread(target=cleanup)

    def release_and_join() -> None:
        release_startup.set()
        startup_thread.join(2)
        cleanup_thread.join(2)

    request.addfinalizer(release_and_join)
    startup_thread.start()
    assert update_reached.wait(2), "shipping startup never reached UPDATE-to-commit"
    cleanup_thread.start()
    assert cleanup_attempted_lock.wait(2), "owned cleanup never attempted startup's DB lock"
    assert cleanup_thread.is_alive(), "owned cleanup bypassed startup's DB lock"
    release_startup.set()
    startup_thread.join(2)
    cleanup_thread.join(2)
    assert not startup_thread.is_alive() and not cleanup_thread.is_alive()
    assert not startup_errors and not cleanup_errors
    # Prove committed state, not merely visibility on the connection whose
    # lock was contended.  The two invocation controls distinguish the
    # startup-governed pending receipt from an unrelated terminal receipt.
    db_path = db.path
    db.close()
    reopened = Database(db_path)
    owned = reopened.get_job("JOB-OWNED")
    other_job = reopened.get_job("JOB-OTHER")
    assert owned is not None and owned.reason == "task_ended"
    assert other_job is not None and other_job.status is JobStatus.RUNNING
    invocation = reopened.get_invocation_any_status(governed.invocation_token)
    assert invocation is not None and invocation.status.value == "failed"
    assert invocation.decline_reason == "daemon_restart"
    assert invocation.consumed_at is not None
    unrelated_after = reopened.get_invocation_any_status(unrelated.invocation_token)
    assert unrelated_after is not None and unrelated_after.status.value == "consumed"
