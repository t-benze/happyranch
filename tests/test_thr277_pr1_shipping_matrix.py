"""THR-277 PR1 shipping-seam regressions.

These tests drive the real authority-v2 writer through the run-step completion
consumer or the daemon startup sweep.  They intentionally avoid helper-only
lookalikes; broad ``tests/integration`` remains skipped under THR-243 seq42.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.daemon.__main__ import _sweep_on_startup
from runtime.daemon.queue import TaskQueue
from runtime.daemon.sessions import SessionTracker
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import (
    BlockKind,
    JobInterpreter,
    JobRecord,
    JobStatus,
    TaskRecord,
    TaskStatus,
)
from runtime.orchestrator import run_step
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from runtime.orchestrator.chain import ChainState
from runtime.orchestrator.fanout import FanoutState
from runtime.orchestrator.orchestrator import (
    Orchestrator,
    completion_report_from_result_row,
)
from tests.test_authority_v2_hook import (
    _admitted,
    _carrier_with,
    _orch,
    _seed_bound_task,
    _store,
)
from tests.test_authority_v2_refusal_housekeeping import _seed_receipt
from tests.test_authority_v2_decision_dispatch import _spent_ready_b
from tests.test_authority_v2_envelope_spend import RESERVED
from tests.test_authority_v2_attempt_admission import MANAGER, TEAM
from tests.test_run_step import _admit_terminal_worktree, _git


class _ObservableTeams:
    """Small registry that keeps the production tail/prompt readers real."""

    manager_mode = False

    def is_team_manager(self, agent):
        return self.manager_mode and agent == MANAGER

    def all_agents(self):
        return [MANAGER]

    def manager_for_team(self, team):
        assert team == TEAM
        return types.SimpleNamespace(name="reviewer", workers=())

    def team_for_manager(self, agent):
        return TEAM if self.manager_mode and agent == MANAGER else None


def _configure_observable_tail(
    store, orch, tmp_path: Path, monkeypatch, task_id: str,
) -> dict[str, object]:
    """Seed durable job/history/worktree state for the real terminal tail."""
    paths = OrgPaths(root=tmp_path / "observable-org")
    workspace = paths.workspaces_dir / MANAGER
    workspace.mkdir(parents=True, exist_ok=True)
    primary = workspace / "repos" / "happyranch"
    primary.mkdir(parents=True)
    _git(primary, "init", "-b", "main")
    _git(primary, "config", "user.email", "tests@example.invalid")
    _git(primary, "config", "user.name", "HappyRanch tests")
    (primary / "tracked.txt").write_text("base\n")
    _git(primary, "add", "tracked.txt")
    _git(primary, "commit", "-m", "test base")
    _git(primary, "update-ref", "refs/remotes/origin/main", "HEAD")
    candidate = primary / ".claude" / "worktrees" / task_id
    candidate.parent.mkdir(parents=True)
    _git(primary, "worktree", "add", "-b", f"task/{task_id}", str(candidate))

    orch._paths = paths
    orch._sessions = SessionTracker()
    orch.teams = _ObservableTeams()
    orch._HISTORY_CAP = Orchestrator._HISTORY_CAP
    orch._HISTORY_HEADER = Orchestrator._HISTORY_HEADER
    orch._update_task_history = types.MethodType(
        Orchestrator._update_task_history, orch,
    )
    _admit_terminal_worktree(monkeypatch)

    async def no_os_signal(_task_id, *, inflight_to_task=None):
        # The subprocess/signal boundary is outside this test.  The production
        # tail still owns the real durable task_ended backstop transaction.
        assert inflight_to_task is not None

    monkeypatch.setattr(
        "runtime.daemon.jobs_runner.terminate_jobs_for_task", no_os_signal,
    )
    job_id = f"JOB-{task_id}"
    store._db.insert_job(JobRecord(
        id=job_id,
        task_id=task_id,
        agent_name=MANAGER,
        title="observable terminal tail",
        rationale="proposal cases 1 and 2",
        script_text="true",
        interpreter=JobInterpreter.BASH,
        status=JobStatus.RUNNING,
        created_at=datetime.now(timezone.utc).isoformat(),
    ))
    return {
        "candidate": candidate,
        "history": workspace / "task_history.md",
        "job_id": job_id,
    }


def _wait_for_job(store, job_id: str, reason: str) -> None:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        job = store._db.get_job(job_id)
        if job is not None and job.status is JobStatus.FAILED and job.reason == reason:
            return
        time.sleep(0.01)
    job = store._db.get_job(job_id)
    pytest.fail(f"job did not settle as failed/{reason}: {job}")


def _refusing_run_step_fixture(tmp_path, monkeypatch):
    store = _store(tmp_path)
    binding = _seed_bound_task(store)
    carrier, admission = _carrier_with(
        binding,
        escalate="applies",
        continue_="does_not_apply",
    )
    store, _, _, row, attempt = _admitted(
        tmp_path,
        carrier=carrier,
        admission=admission,
        prebound=(store, binding),
    )
    store._db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    report = completion_report_from_result_row(
        attempt.root_task_id,
        row,
        fallback_agent=attempt.manager_agent,
    )
    orch = _orch(store)
    orch._audit = AuditLogger(store._db)
    orch._parse_next_step = lambda completion: completion.decision
    orch._update_task_history = lambda _task_id: None
    effects: dict[str, list] = {
        "founder": [],
        "thread_escalation": [],
        "terminal_tail": [],
        "parent_wake": [],
        "thread_followup": [],
    }
    orch.notify_escalated = lambda **kwargs: effects["founder"].append(kwargs)
    monkeypatch.setattr(
        run_step,
        "_maybe_post_thread_escalation",
        lambda *_args, **kwargs: effects["thread_escalation"].append(kwargs),
    )
    monkeypatch.setattr(
        run_step,
        "_fail_terminal_tail",
        lambda *_args, **kwargs: effects["terminal_tail"].append(kwargs),
    )
    monkeypatch.setattr(
        run_step,
        "_enqueue_parent_if_waiting",
        lambda *_args, **kwargs: effects["parent_wake"].append(kwargs),
    )
    monkeypatch.setattr(
        run_step,
        "_maybe_post_thread_followup",
        lambda *_args, **kwargs: effects["thread_followup"].append(kwargs),
    )
    return store, row, attempt, report, orch, effects


def _ordinary_tail_fixture(tmp_path, monkeypatch, *, observable_tail=False):
    store = _store(tmp_path)
    binding = _seed_bound_task(store)
    carrier, admission = _carrier_with(
        binding,
        escalate="applies",
        continue_="does_not_apply",
    )
    store, _, _, row, attempt = _admitted(
        tmp_path,
        carrier=carrier,
        admission=admission,
        prebound=(store, binding),
    )
    store._db.bind_authority_policy_v2_process_boot_id(attempt.origin_boot_id)
    report = completion_report_from_result_row(
        attempt.root_task_id,
        row,
        fallback_agent=attempt.manager_agent,
    )
    orch = _orch(store)
    orch._audit = AuditLogger(store._db)
    orch._parse_next_step = lambda completion: completion.decision
    effects: dict[str, object] = {
        "founder": [],
        "thread_escalation": [],
    }
    orch._update_task_history = lambda _task_id: None
    orch.notify_escalated = lambda **kwargs: effects["founder"].append(kwargs)
    monkeypatch.setattr(
        run_step,
        "_maybe_post_thread_escalation",
        lambda *_args, **kwargs: effects["thread_escalation"].append(kwargs),
    )
    if observable_tail:
        effects.update(_configure_observable_tail(
            store, orch, tmp_path, monkeypatch, attempt.root_task_id,
        ))
    return store, row, attempt, report, orch, effects


def _insert_parked_task(store, task_id: str, *, parent_task_id=None, task_type="task"):
    store._db.insert_task(TaskRecord(
        id=task_id,
        brief=task_id,
        assigned_agent="engineering_manager",
        parent_task_id=parent_task_id,
        task_type=task_type,
        status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.DELEGATED,
    ))


def _fanout_parent(store, child_id: str) -> tuple[str, str | None]:
    parent_id = "TASK-THR277-FANOUT"
    sibling_id = "TASK-THR277-FANOUT-SIBLING"
    _insert_parked_task(store, parent_id)
    store._db.insert_task(TaskRecord(
        id=sibling_id,
        brief="completed sibling B",
        assigned_agent="dev_agent",
        parent_task_id=parent_id,
        task_type="subtask",
        status=TaskStatus.COMPLETED,
        note="sibling B completed",
        completed_at=datetime.now(timezone.utc).isoformat(),
    ))
    fanout = FanoutState(
        children_ids=[child_id, sibling_id],
        children_details=[
            {"agent": "engineering_manager", "prompt": "slice A"},
            {"agent": "dev_agent", "prompt": "slice B"},
        ],
        width=2,
        manager_agent="engineering_manager",
    )
    store._db.update_task_active_fanout(parent_id, fanout.serialize())
    store._db._conn.execute(
        "UPDATE tasks SET parent_task_id=? WHERE id=?", (parent_id, child_id),
    )
    store._db._conn.commit()
    return parent_id, None


def _serial_parent(store, child_id: str) -> tuple[str, str | None]:
    parent_id = "TASK-THR277-SERIAL"
    _insert_parked_task(store, parent_id)
    chain = ChainState(
        step_index=0,
        first_leg_expect_verdict="APPROVE",
        legs=[],
        step_audit_id=1,
    )
    store._db.update_task_active_chain(parent_id, chain.serialize())
    store._db._conn.execute(
        "UPDATE tasks SET parent_task_id=? WHERE id=?", (parent_id, child_id),
    )
    store._db._conn.commit()
    return parent_id, None


def _passive_carrier_parent(store, child_id: str) -> tuple[str, str | None]:
    outer_id = "TASK-THR277-OUTER"
    carrier_id = "TASK-THR277-CARRIER"
    _insert_parked_task(store, outer_id)
    _insert_parked_task(
        store,
        carrier_id,
        parent_task_id=outer_id,
        task_type="subtask",
    )
    chain = ChainState(
        step_index=0,
        first_leg_expect_verdict="APPROVE",
        legs=[],
        step_audit_id=1,
    )
    fanout = FanoutState(
        children_ids=[carrier_id],
        children_details=[{"agent": "engineering_manager", "prompt": "pipeline"}],
        width=1,
        manager_agent="engineering_manager",
    )
    store._db.update_task_active_chain(carrier_id, chain.serialize())
    store._db.update_task_active_fanout(outer_id, fanout.serialize())
    store._db._conn.execute(
        "UPDATE tasks SET parent_task_id=? WHERE id=?", (carrier_id, child_id),
    )
    store._db._conn.commit()
    return outer_id, carrier_id


def _assert_ordinary_child_tail(store, effects, child_id: str) -> None:
    _wait_for_job(store, effects["job_id"], "task_ended")
    verdicts = [
        row for row in store._db.get_audit_logs(child_id)
        if row["action"] == "review_verdict"
    ]
    assert len(verdicts) == 1
    assert verdicts[0]["payload"]["verdict"] == "rejected"
    history = effects["history"]
    assert history.exists()
    assert child_id in history.read_text()
    assert not effects["candidate"].exists()
    assert effects["founder"] == []
    assert effects["thread_escalation"] == []


def _consume_parent_wake_context(orch, parent_id: str) -> str:
    """Consume the recorded wake and drive the production prompt builders."""
    assert [item[1] for item in orch._queue.puts] == [parent_id]
    _slug, queued_id, _metadata = orch._queue.puts.pop(0)
    assert queued_id == parent_id
    parent = orch._db.get_task(parent_id)
    assert orch._db.try_claim_for_step(
        parent_id,
        expected_status=parent.status,
        expected_block_kind=parent.block_kind,
        new_count=parent.orchestration_step_count + 1,
    )
    if parent.active_fanout is not None:
        run_step._inject_fanout_join_context(orch, parent_id, parent.active_fanout)
        orch._db.update_task_active_fanout(parent_id, None)
    orch.teams.manager_mode = True
    claimed = orch._db.get_task(parent_id)
    return run_step._build_agent_prompt(orch, claimed, claimed.assigned_agent)


def _drive_fresh_run_step_child_refusal(
    tmp_path, monkeypatch, parent_builder,
):
    store, row, attempt, report, orch, effects = _ordinary_tail_fixture(
        tmp_path, monkeypatch, observable_tail=True,
    )
    real_finalize = AuthorityPolicyStore.finalize_v2_attempt_refusal
    routed: dict[str, str | None] = {}

    def make_child_then_finalize(self, **kwargs):
        wake_id, carrier_id = parent_builder(store, attempt.root_task_id)
        routed.update(wake_id=wake_id, carrier_id=carrier_id)
        return real_finalize(self, **kwargs)

    monkeypatch.setattr(
        AuthorityPolicyStore,
        "finalize_v2_attempt_refusal",
        make_child_then_finalize,
    )
    _consume_at_run_step(orch, attempt, row, report)
    return store, attempt, orch, effects, routed


def _drive_startup_child_refusal(tmp_path, monkeypatch, parent_builder):
    store, row, attempt, _report, orch, effects = _ordinary_tail_fixture(
        tmp_path, monkeypatch, observable_tail=True,
    )
    wake_id, carrier_id = parent_builder(store, attempt.root_task_id)
    store._db.bind_authority_policy_v2_process_boot_id("boot-after-restart")
    store._db._v2_live_attempt_owners.clear()
    _sweep_on_startup(store._db, TaskQueue(), "test", orchestrator=orch)
    return store, attempt, orch, effects, {
        "wake_id": wake_id,
        "carrier_id": carrier_id,
    }


def _make_child(store, task_id: str) -> None:
    store._db.insert_task(TaskRecord(id="TASK-THR277-PARENT", brief="parent"))
    store._db._conn.execute(
        "UPDATE tasks SET parent_task_id=? WHERE id=?",
        ("TASK-THR277-PARENT", task_id),
    )
    store._db._conn.commit()


class _StructuredRefusal(str):
    """Boundary-compatible refusal result for caller-only race injections."""

    def __new__(cls, *, status: str, task_disposition: str | None = None):
        value = super().__new__(cls, "v2_refused")
        value.status = status
        value.task_disposition = task_disposition
        return value


def _consume_at_run_step(orch, attempt, row, report) -> None:
    run_step._consume_completion_report_body(
        orch,
        attempt.root_task_id,
        report,
        result_row_id=row["id"],
    )


def test_case_6_run_step_owner_lost_child_has_no_root_or_child_projection(
    tmp_path, monkeypatch,
):
    """Proposal case 6/M1: a replaced child owner wins before D1 refusal."""
    store, row, attempt, report, orch, effects = _refusing_run_step_fixture(
        tmp_path, monkeypatch,
    )
    def replace_owner_then_refuse(*_args, **_kwargs):
        _make_child(store, attempt.root_task_id)
        store._db.update_task(
            attempt.root_task_id, current_session_id="sess-winner",
        )
        return _StructuredRefusal(status="owner_lost")

    monkeypatch.setattr(
        "runtime.orchestrator.authority.run_authority_hook",
        replace_owner_then_refuse,
    )

    _consume_at_run_step(orch, attempt, row, report)

    assert store._db.get_task(attempt.root_task_id).current_session_id == "sess-winner"
    assert effects == {name: [] for name in effects}


def test_case_6_run_step_cancelled_child_has_no_root_or_child_projection(
    tmp_path, monkeypatch,
):
    """Proposal case 6/M1: cancellation wins before D1 refusal."""
    store, row, attempt, report, orch, effects = _refusing_run_step_fixture(
        tmp_path, monkeypatch,
    )
    def cancel_then_refuse(*_args, **_kwargs):
        _make_child(store, attempt.root_task_id)
        store._db.update_task(
            attempt.root_task_id,
            status=TaskStatus.CANCELLED,
            cancelled_at="2026-01-01T00:00:00+00:00",
        )
        return _StructuredRefusal(status="owner_lost")

    monkeypatch.setattr(
        "runtime.orchestrator.authority.run_authority_hook",
        cancel_then_refuse,
    )

    _consume_at_run_step(orch, attempt, row, report)

    assert store._db.get_task(attempt.root_task_id).status is TaskStatus.CANCELLED
    assert effects == {name: [] for name in effects}


def test_case_4_run_step_fresh_root_refusal_keeps_escalation_projection(
    tmp_path, monkeypatch,
):
    """Proposal case 4: the freshly refused root still surfaces exactly once."""
    store, row, attempt, report, orch, effects = _refusing_run_step_fixture(
        tmp_path, monkeypatch,
    )

    _consume_at_run_step(orch, attempt, row, report)

    root = store._db.get_task(attempt.root_task_id)
    assert root.status is TaskStatus.ESCALATED
    assert root.parent_task_id is None
    assert len(effects["founder"]) == 1
    assert len(effects["thread_escalation"]) == 1
    assert effects["terminal_tail"] == []
    assert effects["parent_wake"] == []
    assert effects["thread_followup"] == []


def test_case_5_run_step_root_refusal_replay_has_no_second_projection(
    tmp_path, monkeypatch,
):
    """Proposal case 5: an exact replay authenticates but owns no live tail."""
    _store_obj, row, attempt, report, orch, effects = _refusing_run_step_fixture(
        tmp_path, monkeypatch,
    )
    _consume_at_run_step(orch, attempt, row, report)
    assert len(effects["founder"]) == 1
    assert len(effects["thread_escalation"]) == 1
    effects["founder"].clear()
    effects["thread_escalation"].clear()

    _consume_at_run_step(orch, attempt, row, report)

    assert effects["founder"] == []
    assert effects["thread_escalation"] == []
    assert effects["terminal_tail"] == []
    assert effects["parent_wake"] == []


def test_case_1_run_step_fanout_child_runs_terminal_tail_and_wakes_parent(
    tmp_path, monkeypatch,
):
    store, attempt, orch, effects, routed = _drive_fresh_run_step_child_refusal(
        tmp_path, monkeypatch, _fanout_parent,
    )
    _assert_ordinary_child_tail(store, effects, attempt.root_task_id)
    child = store._db.get_task(attempt.root_task_id)
    assert child.status is TaskStatus.FAILED
    assert child.note.startswith("authority_v2_refusal:")
    prompt = _consume_parent_wake_context(orch, routed["wake_id"])
    assert attempt.root_task_id in prompt
    assert "status=failed" in prompt
    assert child.note in prompt
    assert "TASK-THR277-FANOUT-SIBLING" in prompt
    assert "status=completed" in prompt


def test_case_2_run_step_serial_child_runs_terminal_tail_and_wakes_parent(
    tmp_path, monkeypatch,
):
    store, attempt, orch, effects, routed = _drive_fresh_run_step_child_refusal(
        tmp_path, monkeypatch, _serial_parent,
    )
    _assert_ordinary_child_tail(store, effects, attempt.root_task_id)
    parent = store._db.get_task(routed["wake_id"])
    assert parent.active_chain is None
    child = store._db.get_task(attempt.root_task_id)
    assert child.note.startswith("authority_v2_refusal:")
    prompt = _consume_parent_wake_context(orch, routed["wake_id"])
    assert attempt.root_task_id in prompt
    assert "status=failed" in prompt
    assert child.note in prompt


def test_case_3_run_step_passive_carrier_fails_to_outer_barrier(
    tmp_path, monkeypatch,
):
    store, attempt, orch, effects, routed = _drive_fresh_run_step_child_refusal(
        tmp_path, monkeypatch, _passive_carrier_parent,
    )
    _assert_ordinary_child_tail(store, effects, attempt.root_task_id)
    carrier = store._db.get_task(routed["carrier_id"])
    assert carrier.status is TaskStatus.FAILED
    assert f"causal_leaf_id={attempt.root_task_id}" in (carrier.note or "")
    assert [item[1] for item in orch._queue.puts] == [routed["wake_id"]]


def test_case_1_startup_fanout_child_runs_terminal_tail_and_wakes_parent(
    tmp_path, monkeypatch,
):
    store, attempt, orch, effects, routed = _drive_startup_child_refusal(
        tmp_path, monkeypatch, _fanout_parent,
    )
    _assert_ordinary_child_tail(store, effects, attempt.root_task_id)
    child = store._db.get_task(attempt.root_task_id)
    assert child.status is TaskStatus.FAILED
    assert child.note.startswith("authority_v2_refusal:")
    prompt = _consume_parent_wake_context(orch, routed["wake_id"])
    assert attempt.root_task_id in prompt
    assert "status=failed" in prompt
    assert child.note in prompt
    assert "TASK-THR277-FANOUT-SIBLING" in prompt
    assert "status=completed" in prompt


def test_case_2_startup_serial_child_runs_terminal_tail_and_wakes_parent(
    tmp_path, monkeypatch,
):
    store, attempt, orch, effects, routed = _drive_startup_child_refusal(
        tmp_path, monkeypatch, _serial_parent,
    )
    _assert_ordinary_child_tail(store, effects, attempt.root_task_id)
    assert store._db.get_task(routed["wake_id"]).active_chain is None
    child = store._db.get_task(attempt.root_task_id)
    assert child.note.startswith("authority_v2_refusal:")
    prompt = _consume_parent_wake_context(orch, routed["wake_id"])
    assert attempt.root_task_id in prompt
    assert "status=failed" in prompt
    assert child.note in prompt


def test_case_3_startup_passive_carrier_fails_to_outer_barrier(
    tmp_path, monkeypatch,
):
    store, attempt, orch, effects, routed = _drive_startup_child_refusal(
        tmp_path, monkeypatch, _passive_carrier_parent,
    )
    _assert_ordinary_child_tail(store, effects, attempt.root_task_id)
    carrier = store._db.get_task(routed["carrier_id"])
    assert carrier.status is TaskStatus.FAILED
    assert f"causal_leaf_id={attempt.root_task_id}" in (carrier.note or "")
    assert [item[1] for item in orch._queue.puts] == [routed["wake_id"]]


def test_case_3_keeper_existing_failed_leaf_fails_passive_carrier(
    tmp_path, monkeypatch,
):
    """Proposal case 3 keeper: the pre-existing passive-carrier route remains."""
    store = _store(tmp_path)
    child_id = "TASK-THR277-CARRIER-LEAF"
    store._db.insert_task(TaskRecord(
        id=child_id,
        brief="failed leaf",
        assigned_agent="dev_agent",
        task_type="subtask",
        status=TaskStatus.FAILED,
        note="existing failure",
        completed_at=datetime.now(timezone.utc).isoformat(),
    ))
    outer_id, carrier_id = _passive_carrier_parent(store, child_id)
    orch = _orch(store)
    orch._audit = AuditLogger(store._db)
    orch._update_task_history = lambda _task_id: None
    monkeypatch.setattr(run_step, "_fail_terminal_tail", lambda *_a, **_kw: None)

    run_step._enqueue_parent_if_waiting(orch, child_id)

    carrier = store._db.get_task(carrier_id)
    assert carrier.status is TaskStatus.FAILED
    assert f"causal_leaf_id={child_id}" in (carrier.note or "")
    assert [item[1] for item in orch._queue.puts] == [outer_id]


def _recovery_owned_startup_fixture(tmp_path, monkeypatch, *, with_job: bool):
    store, row, attempt, _report, orch, effects = _ordinary_tail_fixture(
        tmp_path, monkeypatch,
    )
    parent_id, _ = _fanout_parent(store, attempt.root_task_id)
    _seed_receipt(store, row["id"])
    if with_job:
        store._db.insert_job(JobRecord(
            id="JOB-THR277-RECOVERY",
            task_id=attempt.root_task_id,
            agent_name=attempt.manager_agent,
            title="recovery-owned",
            rationale="proposal case 7",
            script_text="true",
            interpreter=JobInterpreter.BASH,
            status=JobStatus.RUNNING,
            created_at=datetime.now(timezone.utc).isoformat(),
        ))
    store._db.bind_authority_policy_v2_process_boot_id("boot-after-restart")
    store._db._v2_live_attempt_owners.clear()
    return store, row, attempt, orch, effects, parent_id


def test_case_7_recovery_owned_zero_job_wakes_via_after_recovery_cleanup(
    tmp_path, monkeypatch,
):
    store, row, attempt, orch, effects, parent_id = _recovery_owned_startup_fixture(
        tmp_path, monkeypatch, with_job=False,
    )

    handoffs: list[bool] = []
    original_handoff = (
        store._db.handoff_consumed_task_completion_recovery_parent_effect
    )

    def observed_handoff(**kwargs):
        result = original_handoff(**kwargs)
        handoffs.append(result)
        return result

    monkeypatch.setattr(
        store._db,
        "handoff_consumed_task_completion_recovery_parent_effect",
        observed_handoff,
    )

    _sweep_on_startup(store._db, TaskQueue(), "test", orchestrator=orch)

    receipt = store._db._conn.execute(
        "SELECT state FROM task_completion_recoveries WHERE task_id=?",
        (attempt.root_task_id,),
    ).fetchone()
    assert receipt["state"] == "callback_consumed"
    assert handoffs == [True]
    assert [item[1] for item in orch._queue.puts] == [parent_id]
    assert effects["founder"] == []


def test_case_7_recovery_owned_nonzero_job_wakes_via_after_recovery_cleanup(
    tmp_path, monkeypatch,
):
    store, row, attempt, orch, effects, parent_id = _recovery_owned_startup_fixture(
        tmp_path, monkeypatch, with_job=True,
    )
    handoffs: list[bool] = []

    def finish_owned_jobs(
        _orch,
        task_id,
        *,
        recovery_owner,
        recovery_job_ids,
        after_recovery_cleanup,
        after_recovery_parent_effect,
    ):
        assert task_id == attempt.root_task_id
        assert recovery_job_ids == ("JOB-THR277-RECOVERY",)
        assert after_recovery_cleanup is not None
        assert after_recovery_parent_effect is None
        agent, session_id, result_id, terminal_status = recovery_owner
        handoffs.append(store._db.handoff_consumed_task_completion_recovery_parent_effect(
            task_id=task_id,
            agent=agent,
            recovery_session_id=session_id,
            result_row_id=result_id,
            terminal_status=terminal_status,
            effect=after_recovery_cleanup,
        ))

    monkeypatch.setattr(run_step, "_kill_jobs_for_terminating_task", finish_owned_jobs)

    _sweep_on_startup(store._db, TaskQueue(), "test", orchestrator=orch)

    assert handoffs == [True]
    assert [item[1] for item in orch._queue.puts] == [parent_id]
    job = store._db.get_job("JOB-THR277-RECOVERY")
    assert job.status is JobStatus.FAILED
    assert job.reason == "task_ended"
    assert effects["founder"] == []


@pytest.mark.parametrize("with_job", [False, True], ids=["zero_job", "nonzero_job"])
def test_case_7_recovery_owned_consumed_ledger_restart_wakes_parent_once(
    tmp_path, monkeypatch, with_job,
):
    """Proposal case 7: a crash after callback consumption retains wake ownership."""
    store, row, attempt, orch, effects, parent_id = _recovery_owned_startup_fixture(
        tmp_path, monkeypatch, with_job=with_job,
    )
    crash_seen = threading.Event()
    crash_enabled = True
    original_handoff = (
        store._db.handoff_consumed_task_completion_recovery_parent_effect
    )

    def crash_before_parent_wake(**kwargs):
        nonlocal crash_enabled
        if crash_enabled:
            crash_seen.set()
            return False
        return original_handoff(**kwargs)

    monkeypatch.setattr(
        store._db,
        "handoff_consumed_task_completion_recovery_parent_effect",
        crash_before_parent_wake,
    )
    if with_job:
        async def no_os_signal(_task_id, *, inflight_to_task=None):
            assert inflight_to_task is not None

        monkeypatch.setattr(
            "runtime.daemon.jobs_runner.terminate_jobs_for_task", no_os_signal,
        )
        _sweep_on_startup(store._db, TaskQueue(), "test", orchestrator=orch)
        assert crash_seen.wait(3)
    else:
        _sweep_on_startup(store._db, TaskQueue(), "test", orchestrator=orch)
        assert crash_seen.is_set()

    receipt = store._db._conn.execute(
        "SELECT state FROM task_completion_recoveries WHERE task_id=?",
        (attempt.root_task_id,),
    ).fetchone()
    assert receipt["state"] in {"callback_consumed", "jobs_settled"}
    assert orch._queue.puts == []

    crash_enabled = False
    fresh_queue = TaskQueue()
    fresh_orch = _orch(store, fresh_queue)
    fresh_orch._audit = AuditLogger(store._db)
    fresh_orch._update_task_history = lambda _task_id: None
    _sweep_on_startup(store._db, fresh_queue, "test-org", orchestrator=fresh_orch)
    _sweep_on_startup(store._db, fresh_queue, "test-org", orchestrator=fresh_orch)

    assert fresh_queue._queue.qsize() == 1
    assert fresh_queue._queue.get_nowait()[1] == parent_id
    assert effects["founder"] == []


@pytest.mark.parametrize(
    "boundary",
    ["after_commit", "after_job_scheduled", "after_worktree_reclaim", "after_parent_enqueue"],
)
def test_case_8_d1_and_ordinary_fail_crash_boundaries_converge_identically(
    tmp_path, monkeypatch, boundary,
):
    """Proposal case 8: every D1 post-commit crash matches ordinary `_fail`."""
    store, row, attempt, _report, orch, effects = _ordinary_tail_fixture(
        tmp_path, monkeypatch, observable_tail=True,
    )
    d1_parent, _ = _fanout_parent(store, attempt.root_task_id)
    now = datetime.now(timezone.utc).isoformat()

    outcome = AuthorityPolicyStore(store._db).finalize_v2_attempt_refusal(
        root_task_id=attempt.root_task_id,
        manager_agent=attempt.manager_agent,
        manager_session_id=attempt.manager_session_id,
        result_id=row["id"],
        refusal_code="interrupted_pre_final",
        owner_attempt_id=attempt.owner_attempt_id,
    )
    assert outcome.status == "refused"
    assert outcome.task_disposition == "failed"

    ordinary_parent = "TASK-THR277-ORDINARY-PARENT"
    ordinary_child = "TASK-THR277-ORDINARY-CHILD"
    _insert_parked_task(store, ordinary_parent)
    store._db.insert_task(TaskRecord(
        id=ordinary_child,
        brief="ordinary fail control",
        assigned_agent=MANAGER,
        parent_task_id=ordinary_parent,
        task_type="subtask",
        status=TaskStatus.IN_PROGRESS,
        team=TEAM,
    ))
    primary = effects["candidate"].parents[2]
    ordinary_candidate = primary / ".claude" / "worktrees" / ordinary_child
    _git(
        primary, "worktree", "add", "-b", f"task/{ordinary_child}",
        str(ordinary_candidate),
    )
    store._db.insert_job(JobRecord(
        id="JOB-THR277-ORDINARY-CRASH",
        task_id=ordinary_child,
        agent_name=MANAGER,
        title="ordinary crash",
        rationale="proposal case 8 control",
        script_text="true",
        interpreter=JobInterpreter.BASH,
        status=JobStatus.RUNNING,
        created_at=now,
    ))

    scheduled = threading.Event()
    release = threading.Event()

    async def blocked_os_signal(_task_id, *, inflight_to_task=None):
        assert inflight_to_task is not None
        scheduled.set()
        while not release.is_set():
            await asyncio.sleep(0.005)

    monkeypatch.setattr(
        "runtime.daemon.jobs_runner.terminate_jobs_for_task", blocked_os_signal,
    )

    class BoundaryCrash(RuntimeError):
        pass

    real_reclaim = run_step._reclaim_terminal_task_worktree
    if boundary == "after_commit":
        monkeypatch.setattr(
            run_step, "_fail_terminal_tail", lambda *_args, **_kwargs: None,
        )
        run_step._fail(orch, ordinary_child, note="ordinary fail control")
    else:
        if boundary == "after_job_scheduled":
            def crash_reclaim(_orch, _task_id):
                raise BoundaryCrash("after job kill scheduling")
        elif boundary == "after_worktree_reclaim":
            def crash_reclaim(_orch, task_id):
                result = real_reclaim(_orch, task_id)
                raise BoundaryCrash(f"after worktree reclaim: {result}")
        else:
            crash_reclaim = real_reclaim
        monkeypatch.setattr(run_step, "_reclaim_terminal_task_worktree", crash_reclaim)

        if boundary == "after_parent_enqueue":
            run_step._fail_terminal_tail(
                orch,
                attempt.root_task_id,
                expected_note="authority_v2_refusal:interrupted_pre_final",
            )
            run_step._enqueue_parent_if_waiting(orch, attempt.root_task_id)
            run_step._fail(orch, ordinary_child, note="ordinary fail control")
            run_step._enqueue_parent_if_waiting(orch, ordinary_child)
        else:
            with pytest.raises(BoundaryCrash):
                run_step._fail_terminal_tail(
                    orch,
                    attempt.root_task_id,
                    expected_note="authority_v2_refusal:interrupted_pre_final",
                )
            with pytest.raises(BoundaryCrash):
                run_step._fail(orch, ordinary_child, note="ordinary fail control")
        assert scheduled.wait(3)

    recovered_jobs = store._db.recover_orphaned_running_jobs(now_iso=now)
    release.set()
    queue = TaskQueue()
    fresh_orch = _orch(store, queue)
    fresh_orch._audit = AuditLogger(store._db)
    fresh_orch._update_task_history = lambda _task_id: None
    _sweep_on_startup(store._db, queue, "test", orchestrator=fresh_orch)
    _sweep_on_startup(store._db, queue, "test", orchestrator=fresh_orch)

    assert set(recovered_jobs) == {
        effects["job_id"],
        "JOB-THR277-ORDINARY-CRASH",
    }
    assert queue._queue.qsize() == 2
    assert {
        queue._queue.get_nowait()[1],
        queue._queue.get_nowait()[1],
    } == {d1_parent, ordinary_parent}
    for job_id in recovered_jobs:
        job = store._db.get_job(job_id)
        assert job.status is JobStatus.FAILED
        assert job.reason == "daemon_crash"
    child = store._db.get_task(attempt.root_task_id)
    control = store._db.get_task(ordinary_child)
    assert child.status is control.status is TaskStatus.FAILED
    assert child.completed_at is not None and control.completed_at is not None
    actions = [a["action"] for a in store._db.get_audit_logs(attempt.root_task_id)]
    assert actions.count("authority_v2_refusal_task_failed") == 1
    assert "escalation" not in actions
    final_attempt = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert final_attempt.finalization_state == "refused"
    reclaimed = boundary in {"after_worktree_reclaim", "after_parent_enqueue"}
    assert effects["candidate"].exists() is not reclaimed
    assert ordinary_candidate.exists() is not reclaimed
    if boundary == "after_parent_enqueue":
        assert sorted(item[1] for item in orch._queue.puts) == sorted(
            [d1_parent, ordinary_parent],
        )


def test_case_8_keeper_ordinary_fail_commit_crash_recovers_job_and_parent_once(
    tmp_path, monkeypatch,
):
    """Proposal case 8 keeper: the existing ordinary-_fail crash owner stays."""
    store = _store(tmp_path)
    parent_id = "TASK-THR277-FAIL-PARENT"
    child_id = "TASK-THR277-FAIL-CHILD"
    _insert_parked_task(store, parent_id)
    store._db.insert_task(TaskRecord(
        id=child_id,
        brief="ordinary fail",
        assigned_agent="dev_agent",
        parent_task_id=parent_id,
        task_type="subtask",
        status=TaskStatus.IN_PROGRESS,
    ))
    now = datetime.now(timezone.utc).isoformat()
    store._db.insert_job(JobRecord(
        id="JOB-THR277-FAIL-KEEPER",
        task_id=child_id,
        agent_name="dev_agent",
        title="ordinary crash",
        rationale="case 8 keeper",
        script_text="true",
        interpreter=JobInterpreter.BASH,
        status=JobStatus.RUNNING,
        created_at=now,
    ))
    orch = _orch(store)
    orch._audit = AuditLogger(store._db)
    orch._update_task_history = lambda _task_id: None
    monkeypatch.setattr(run_step, "_fail_terminal_tail", lambda *_a, **_kw: None)
    run_step._fail(orch, child_id, note="ordinary fail")
    store._db.recover_orphaned_running_jobs(now_iso=now)
    queue = TaskQueue()

    _sweep_on_startup(store._db, queue, "test", orchestrator=orch)
    _sweep_on_startup(store._db, queue, "test", orchestrator=orch)

    assert queue._queue.qsize() == 1
    assert queue._queue.get_nowait()[1] == parent_id
    job = store._db.get_job("JOB-THR277-FAIL-KEEPER")
    assert job.status is JobStatus.FAILED
    assert job.reason == "daemon_crash"


@pytest.mark.parametrize(
    ("case_name", "status", "decision_json", "output_summary", "refused"),
    [
        ("case10_delegate", "completed", {"action": "delegate", "agent": "dev_agent", "prompt": "x"}, "delegate", False),
        ("case10_done", "completed", {"action": "done", "summary": "done"}, "done", False),
        ("case11_legacy_delegate", "completed", None, json.dumps({"action": "delegate", "agent": "dev_agent", "prompt": "legacy"}), False),
        ("case11_prose", "completed", None, "plain prose", True),
        ("case11_non_object", "completed", None, "[]", True),
        ("case11_malformed", "completed", {"action": "bogus"}, "malformed", True),
        ("case11_empty", "completed", None, "", True),
        ("case14_missing_result", "completed", {"action": "done", "summary": "x"}, "done", True),
        ("case14_unreadable_result", "completed", {"action": "done", "summary": "x"}, "done", True),
        ("case14_parser_exception", "completed", {"action": "done", "summary": "x"}, "done", True),
    ],
    ids=lambda value: value if isinstance(value, str) and value.startswith("case") else None,
)
def test_cases_10_11_14_d2_classification_table_runs_at_real_startup_seam(
    tmp_path,
    monkeypatch,
    case_name,
    status,
    decision_json,
    output_summary,
    refused,
):
    """Proposal cases 10/11/14: only admitted classified results may skip."""
    store, _, _, row, attempt = _admitted(tmp_path)
    store._db._conn.execute(
        "UPDATE task_results SET status=?, decision_json=?, output_summary=? WHERE id=?",
        (
            status,
            json.dumps(decision_json) if decision_json is not None else None,
            output_summary,
            row["id"],
        ),
    )
    store._db.update_task(
        attempt.root_task_id,
        status=TaskStatus.IN_PROGRESS,
        executor_pid=os.getpid(),
    )
    store._db._conn.commit()
    store._db.bind_authority_policy_v2_process_boot_id("boot-after-restart")
    store._db._v2_live_attempt_owners.clear()
    orch = _orch(store)
    orch._audit = AuditLogger(store._db)
    orch._update_task_history = lambda _task_id: None
    orch._parse_next_step = types.MethodType(Orchestrator._parse_next_step, orch)
    if case_name == "case14_missing_result":
        monkeypatch.setattr(store._db, "get_task_results", lambda _task_id: [])
    elif case_name == "case14_unreadable_result":
        monkeypatch.setattr(
            "runtime.orchestrator.orchestrator.completion_report_from_result_row",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("unreadable")),
        )
    elif case_name == "case14_parser_exception":
        orch._parse_next_step = lambda _report: (_ for _ in ()).throw(
            RuntimeError("parser unavailable")
        )
    before_task = store._db.get_task(attempt.root_task_id)
    before_attempt = dict(store._db._conn.execute(
        "SELECT * FROM authority_policy_v2_attempts WHERE result_id=?",
        (row["id"],),
    ).fetchone())
    before_audits = list(store._db.get_audit_logs(attempt.root_task_id))
    queue = TaskQueue()

    _sweep_on_startup(store._db, queue, "test", orchestrator=orch)

    after_task = store._db.get_task(attempt.root_task_id)
    after_attempt = dict(store._db._conn.execute(
        "SELECT * FROM authority_policy_v2_attempts WHERE result_id=?",
        (row["id"],),
    ).fetchone())
    if refused:
        assert after_task.status is TaskStatus.ESCALATED
        assert after_attempt["finalization_state"] == "refused"
        assert queue._queue.empty()
    else:
        assert after_task.status is before_task.status is TaskStatus.IN_PROGRESS
        assert after_attempt == before_attempt
        assert store._db.get_audit_logs(attempt.root_task_id) == before_audits
        assert queue._queue.empty()


def test_case_13_d1_refusal_does_not_leak_into_reserved_r2_dispatch_receipts(
    tmp_path,
):
    """Proposal case 13 keeper: D1 touches B, never A's spent R2 receipt."""
    store, _row_a, generation_a, r2_row, attempt_b = _spent_ready_b(tmp_path)
    store._db.insert_task(TaskRecord(id="TASK-THR277-R2-PARENT", brief="parent"))
    store._db._conn.execute(
        "UPDATE tasks SET parent_task_id=? WHERE id=?",
        ("TASK-THR277-R2-PARENT", attempt_b.root_task_id),
    )
    store._db._conn.commit()
    notification = store.get_v2_recovery_notification(generation_a.notification_id)
    envelope_before = store.get_v2_continue_envelope(notification.envelope_id)
    r2_before = dict(store._db._conn.execute(
        "SELECT * FROM task_results WHERE id=?", (r2_row["id"],),
    ).fetchone())
    dispatch_before = [
        audit for audit in store._db.get_audit_logs(attempt_b.root_task_id)
        if audit["action"] in {
            "authority_v2_decision_claimed",
            "authority_v2_decision_applied",
            "authority_v2_decision_dispatch_interrupted",
        }
    ]

    result = store.finalize_v2_attempt_refusal(
        root_task_id=attempt_b.root_task_id,
        manager_agent=attempt_b.manager_agent,
        manager_session_id=RESERVED,
        result_id=r2_row["id"],
        refusal_code="interrupted_pre_final",
        owner_attempt_id=attempt_b.owner_attempt_id,
    )

    assert result.status == "refused"
    assert dict(store._db._conn.execute(
        "SELECT * FROM task_results WHERE id=?", (r2_row["id"],),
    ).fetchone()) == r2_before
    assert store.get_v2_continue_envelope(notification.envelope_id) == envelope_before
    dispatch_after = [
        audit for audit in store._db.get_audit_logs(attempt_b.root_task_id)
        if audit["action"] in {
            "authority_v2_decision_claimed",
            "authority_v2_decision_applied",
            "authority_v2_decision_dispatch_interrupted",
        }
    ]
    assert dispatch_after == dispatch_before


def test_case_18_thr090_current_session_result_is_consumed_once_by_branch_1(
    tmp_path, monkeypatch,
):
    """Proposal case 18 keeper: D1/D2 fencing never captures ordinary THR-090."""
    store = _store(tmp_path)
    task_id = "TASK-THR277-THR090"
    session_id = "sess-thr090"
    store._db.insert_task(TaskRecord(
        id=task_id,
        brief="ordinary current-session result",
        assigned_agent="engineering_manager",
        task_type="task",
        status=TaskStatus.IN_PROGRESS,
        current_session_id=session_id,
        executor_pid=99999999,
    ))
    store._db._conn.execute(
        """INSERT INTO task_results
           (task_id, agent, session_id, status, output_summary, decision_json,
            confidence_score, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            task_id,
            "engineering_manager",
            session_id,
            "completed",
            "done",
            json.dumps({"action": "done", "summary": "finished"}),
            90,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    store._db._conn.commit()
    orch = _orch(store)
    orch._audit = AuditLogger(store._db)
    orch._parse_next_step = types.MethodType(Orchestrator._parse_next_step, orch)
    orch._update_task_history = lambda _task_id: None
    monkeypatch.setattr(run_step, "_kill_jobs_for_terminating_task", lambda *_a, **_k: None)
    monkeypatch.setattr(run_step, "_reclaim_terminal_task_worktree", lambda *_a, **_k: None)
    queue = TaskQueue()

    _sweep_on_startup(store._db, queue, "test", orchestrator=orch)
    _sweep_on_startup(store._db, queue, "test", orchestrator=orch)

    assert store._db.get_task(task_id).status is TaskStatus.COMPLETED
    actions = [audit["action"] for audit in store._db.get_audit_logs(task_id)]
    assert actions.count("completion_report") == 1
    assert "escalation" not in actions
    assert "authority_v2_refusal_task_failed" not in actions
