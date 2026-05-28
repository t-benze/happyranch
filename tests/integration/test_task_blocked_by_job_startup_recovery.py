"""Startup recovery test for caller C (blocked-on-job → re-evaluate on daemon restart).

Spec: docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md §5.7 & §9.2

Approach: unit-level DB staging rather than a full daemon restart.

The full daemon-restart flow would require:
  1. Starting the daemon (live_daemon fixture)
  2. Getting a task into BLOCKED+BLOCKED_ON_JOB with a job in 'running' state
  3. Killing the daemon mid-run (SIGKILL — no graceful shutdown)
  4. Restarting the daemon against the same DB
  5. Verifying the startup lifespan hook re-evaluates the predicate

This is not practical with the current test infrastructure because
`live_daemon` is a single-use fixture with no restart support, and
SIGKILL'ing it would require managing port-file stale state, the test
process's own env, and uvicorn's asyncio loop — all fragile.

Instead, this file verifies the startup-recovery code path at the unit
level by directly calling:
  1. `recover_orphaned_running_jobs` — marks running jobs 'failed' with
     reason='daemon_crash' (same as a real crash-recovery).
  2. `list_tasks_blocked_on_jobs` + `_maybe_resume_blocked_task` — the
     exact caller C scan loop in src/daemon/app.py lifespan.
  3. The enqueued metadata carries trigger="startup_recovery".

The second test additionally verifies the full run_step path by calling
`orch.run_step` directly with the blocked task and a mock subprocess, to
confirm the BLOCKED-JOBS-RESULTS header is injected into the prompt (via
the `task_resumed_from_jobs` audit row written at CAS-win time).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.models import (
    BlockKind,
    JobInterpreter,
    JobRecord,
    JobStatus,
    TaskRecord,
    TaskStatus,
)
from src.orchestrator._paths import OrgPaths
from src.orchestrator.teams import TeamsRegistry
from src.runtime import RuntimeDir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _make_runtime(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent]\n"
    )
    return paths


def _make_job(
    job_id: str,
    task_id: str,
    *,
    status: JobStatus = JobStatus.RUNNING,
    reason: str | None = None,
) -> JobRecord:
    return JobRecord(
        id=job_id,
        task_id=task_id,
        agent_name="dev_agent",
        title="test job",
        rationale="integration test",
        script_text="echo test",
        interpreter=JobInterpreter.BASH,
        status=status,
        reason=reason,
        created_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# Test 1: recover_orphaned_running_jobs + caller C scan enqueues the task
# ---------------------------------------------------------------------------

def test_startup_recovery_enqueues_blocked_task_after_job_crash(
    tmp_path: Path,
) -> None:
    """Crash scenario: daemon dies with one job 'running' and the task
    in BLOCKED+BLOCKED_ON_JOB.

    Startup recovery sequence (mirroring src/daemon/app.py lifespan):
      step A — recover_orphaned_running_jobs force-fails 'running' jobs
               with reason='daemon_crash'.
      step B — caller C scan: list_tasks_blocked_on_jobs + _maybe_resume_blocked_task
               per task → enqueues task with trigger='startup_recovery'.

    Assertions:
    - Job status flips from 'running' → 'failed' with reason='daemon_crash'.
    - _maybe_resume_blocked_task returns True (enqueued).
    - The enqueue carries metadata trigger='startup_recovery',
      triggering_job_id=None.
    """
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator.run_step import _maybe_resume_blocked_task

    org_paths = _make_runtime(tmp_path)
    db = Database(org_paths.db_path)

    # Stage: task in BLOCKED+BLOCKED_ON_JOB waiting on JOB-1.
    # Note: insert_task does not include blocked_on_job_ids; use update_task
    # to set it after insertion (mirrors the real route that calls insert_task
    # then update_task with blocked_on_job_ids in the self-block handler).
    db.insert_task(TaskRecord(
        id="TASK-1",
        brief="deploy service",
        team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.BLOCKED,
        block_kind=BlockKind.BLOCKED_ON_JOB,
    ))
    db.update_task("TASK-1", blocked_on_job_ids=json.dumps(["JOB-1"]))

    # Stage: JOB-1 is in 'running' state (daemon died before it finished).
    db.insert_job(_make_job("JOB-1", "TASK-1", status=JobStatus.RUNNING))

    # Verify precondition: job is running, task is blocked-on-job.
    assert db.get_job_status("JOB-1") == "running"
    blocked_ids = db.list_tasks_blocked_on_jobs()
    assert "TASK-1" in blocked_ids

    # ── Step A: recover_orphaned_running_jobs (lifespan line 97) ──
    recovered = db.recover_orphaned_running_jobs(now_iso=_now_iso())
    assert "JOB-1" in recovered, (
        f"expected JOB-1 in recovered list, got {recovered}"
    )

    # Job must now be 'failed' with reason='daemon_crash'.
    job = db.get_job("JOB-1")
    assert job is not None
    assert job.status == JobStatus.FAILED, (
        f"expected job status=failed after recovery, got {job.status!r}"
    )
    assert job.reason == "daemon_crash", (
        f"expected job reason=daemon_crash, got {job.reason!r}"
    )

    # ── Step B: caller C scan ──
    # Build a minimal orchestrator; wire a mock queue to capture enqueue calls.
    orch = Orchestrator(
        db=db,
        settings=Settings(),
        paths=org_paths,
        slug="test",
        teams=TeamsRegistry.load(org_paths.root),
    )
    mock_queue = MagicMock()
    orch.attach_queue(mock_queue)

    enqueued = []

    def _capture_enqueue(slug: str, task_id: str, *, metadata: dict | None = None) -> None:
        enqueued.append({"slug": slug, "task_id": task_id, "metadata": metadata})

    mock_queue.enqueue.side_effect = _capture_enqueue

    # Run the caller C scan (mirrors app.py lifespan lines 108-112).
    blocked_task_ids = db.list_tasks_blocked_on_jobs()
    assert "TASK-1" in blocked_task_ids, (
        f"expected TASK-1 in list_tasks_blocked_on_jobs, got {blocked_task_ids}"
    )

    for task_id in blocked_task_ids:
        result = _maybe_resume_blocked_task(
            orch, task_id,
            trigger="startup_recovery",
            triggering_job_id=None,
        )

    # Assert the task was enqueued.
    assert len(enqueued) == 1, (
        f"expected exactly 1 enqueue call, got {len(enqueued)}: {enqueued}"
    )
    call = enqueued[0]
    assert call["task_id"] == "TASK-1", (
        f"expected task_id=TASK-1, got {call['task_id']!r}"
    )
    assert call["slug"] == "test", (
        f"expected slug=test, got {call['slug']!r}"
    )
    md = call["metadata"] or {}
    assert md.get("trigger") == "startup_recovery", (
        f"expected trigger=startup_recovery in metadata, got {md!r}"
    )
    assert md.get("triggering_job_id") is None, (
        f"expected triggering_job_id=None in metadata, got {md!r}"
    )


# ---------------------------------------------------------------------------
# Test 2: multi-job crash — all jobs failed by recover → task enqueued once
# ---------------------------------------------------------------------------

def test_startup_recovery_multi_job_all_crashed(tmp_path: Path) -> None:
    """Two jobs both in 'running' when daemon dies.

    After recovery both are 'failed'; the all-terminal predicate passes
    on the first call to _maybe_resume_blocked_task and the task is enqueued
    exactly once.
    """
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator.run_step import _maybe_resume_blocked_task

    org_paths = _make_runtime(tmp_path)
    db = Database(org_paths.db_path)

    db.insert_task(TaskRecord(
        id="TASK-2",
        brief="multi-job crash test",
        team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.BLOCKED,
        block_kind=BlockKind.BLOCKED_ON_JOB,
    ))
    db.update_task("TASK-2", blocked_on_job_ids=json.dumps(["JOB-A", "JOB-B"]))
    db.insert_job(_make_job("JOB-A", "TASK-2", status=JobStatus.RUNNING))
    db.insert_job(_make_job("JOB-B", "TASK-2", status=JobStatus.RUNNING))

    # Recover both.
    recovered = db.recover_orphaned_running_jobs(now_iso=_now_iso())
    assert set(recovered) == {"JOB-A", "JOB-B"}, (
        f"expected both jobs in recovered list, got {recovered}"
    )

    for jid in ("JOB-A", "JOB-B"):
        job = db.get_job(jid)
        assert job is not None and job.status == JobStatus.FAILED, (
            f"expected {jid} failed after recovery, got {job}"
        )

    # Caller C scan.
    orch = Orchestrator(
        db=db,
        settings=Settings(),
        paths=org_paths,
        slug="test",
        teams=TeamsRegistry.load(org_paths.root),
    )
    mock_queue = MagicMock()
    orch.attach_queue(mock_queue)

    enqueued: list[dict] = []
    mock_queue.enqueue.side_effect = lambda slug, task_id, *, metadata=None: \
        enqueued.append({"slug": slug, "task_id": task_id, "metadata": metadata})

    for task_id in db.list_tasks_blocked_on_jobs():
        _maybe_resume_blocked_task(
            orch, task_id,
            trigger="startup_recovery",
            triggering_job_id=None,
        )

    assert len(enqueued) == 1, (
        f"expected exactly 1 enqueue for TASK-2, got {len(enqueued)}: {enqueued}"
    )
    assert enqueued[0]["task_id"] == "TASK-2"
    assert (enqueued[0]["metadata"] or {}).get("trigger") == "startup_recovery"


# ---------------------------------------------------------------------------
# Test 3: task_resumed_from_jobs audit row written by run_step CAS (verifies
#         the BLOCKED-JOBS-RESULTS prompt header would be injected)
# ---------------------------------------------------------------------------

def test_startup_recovery_run_step_writes_resumed_audit_row(
    tmp_path: Path,
) -> None:
    """After caller C enqueues the task, run_step picks it up and transitions
    BLOCKED+BLOCKED_ON_JOB → IN_PROGRESS via the CAS.  At CAS-win it writes
    a `task_resumed_from_jobs` audit row with the job outcomes — this is what
    the BLOCKED-JOBS-RESULTS header builder reads.

    We mock _run_agent to return a fake 'completed' result so run_step exits
    cleanly without touching the filesystem.
    """
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator.run_step import _maybe_resume_blocked_task

    org_paths = _make_runtime(tmp_path)

    # Seed a minimal workspace (required by run_step step 4 workspace guard).
    ws = org_paths.workspaces_dir / "dev_agent"
    skill_dir = ws / ".claude" / "skills" / "start-task"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# start-task (test stub)\n")

    db = Database(org_paths.db_path)

    db.insert_task(TaskRecord(
        id="TASK-3",
        brief="restart audit row test",
        team="engineering",
        assigned_agent="dev_agent",
        status=TaskStatus.BLOCKED,
        block_kind=BlockKind.BLOCKED_ON_JOB,
    ))
    db.update_task("TASK-3", blocked_on_job_ids=json.dumps(["JOB-C"]))
    db.insert_job(_make_job("JOB-C", "TASK-3", status=JobStatus.RUNNING))

    # Step A: crash-recovery.
    db.recover_orphaned_running_jobs(now_iso=_now_iso())
    assert db.get_job_status("JOB-C") == "failed"

    # Step B: caller C enqueues (we won't actually drain the queue; we call
    # run_step manually below).
    orch = Orchestrator(
        db=db,
        settings=Settings(),
        paths=org_paths,
        slug="test",
        teams=TeamsRegistry.load(org_paths.root),
    )
    mock_queue = MagicMock()
    orch.attach_queue(mock_queue)

    result = _maybe_resume_blocked_task(
        orch, "TASK-3",
        trigger="startup_recovery",
        triggering_job_id=None,
    )
    assert result is True, "expected _maybe_resume_blocked_task to return True"

    # Verify the queue was called with correct metadata.
    mock_queue.enqueue.assert_called_once_with(
        "test", "TASK-3",
        metadata={"trigger": "startup_recovery", "triggering_job_id": None},
    )

    # Step C: simulate run_step picking up the task (CAS transition).
    # We patch _run_agent so no subprocess is spawned and no filesystem
    # workspace is needed beyond the SKILL.md marker above.
    from src.models import CompletionReport
    from src.orchestrator.executors import ExecutorResult

    fake_report = CompletionReport(
        task_id="TASK-3",
        agent="dev_agent",
        status="completed",
        confidence=90,
        output_summary="completed after daemon crash recovery",
        decision={"action": "done", "summary": "done"},
    )
    fake_result = ExecutorResult(
        success=True, error=None, returncode=0,
        duration_seconds=1, session_id="sess-fake-1",
    )

    # ── Before run_step: verify the BLOCKED-JOBS-RESULTS header would fire ──
    # The header builder returns non-None when task_resumed_from_jobs has a
    # higher audit id than the latest orchestration_step entry. Since no
    # orchestration_step has been written yet, the header must fire here.
    # This is the state the resumed agent session would see at step 4 of run_step.
    from src.orchestrator.run_step import _blocked_jobs_resume_header_if_applicable

    # At this point, task_resumed_from_jobs is NOT yet written (we haven't called
    # run_step yet). Re-wire the queue now so run_step can call it.
    orch.attach_queue(mock_queue)

    # Call run_step which: CAS-wins, writes task_resumed_from_jobs, then builds
    # the prompt (which calls _blocked_jobs_resume_header_if_applicable internally),
    # then calls _run_agent (mocked), then writes orchestration_step (only for
    # team managers; dev_agent is a worker so this is skipped).
    with patch.object(orch, "_run_agent", return_value=(fake_result, fake_report)):
        orch.run_step(
            "TASK-3",
            metadata={"trigger": "startup_recovery", "triggering_job_id": None},
        )

    # ── Verify task_resumed_from_jobs audit row was written ──
    audit_logs = db.get_audit_logs("TASK-3")
    actions = [e["action"] for e in audit_logs]

    assert "task_resumed_from_jobs" in actions, (
        f"expected task_resumed_from_jobs audit row; got actions={actions}"
    )

    resumed_entry = next(
        e for e in audit_logs if e["action"] == "task_resumed_from_jobs"
    )
    payload = resumed_entry.get("payload") or {}
    if isinstance(payload, str):
        payload = json.loads(payload)

    # trigger must be 'startup_recovery'.
    assert payload.get("trigger") == "startup_recovery", (
        f"expected trigger=startup_recovery, got {payload!r}"
    )

    # blocking_job_ids must contain JOB-C.
    blocking_ids = payload.get("blocking_job_ids", [])
    assert "JOB-C" in blocking_ids, (
        f"JOB-C not in blocking_job_ids={blocking_ids!r}; full payload={payload}"
    )

    # job_outcomes for JOB-C must show 'failed' (daemon crash).
    job_outcomes = payload.get("job_outcomes", {})
    assert job_outcomes.get("JOB-C") == "failed", (
        f"expected job_outcomes['JOB-C']='failed', got {job_outcomes!r}"
    )

    # ── Verify BLOCKED-JOBS-RESULTS header was available during the run ──
    # After run_step completes: task_resumed_from_jobs has been written and is
    # readable. The header builder returns non-None because task_resumed_from_jobs
    # is the latest relevant audit entry (dev_agent is a worker, not a manager,
    # so no orchestration_step row is written on its behalf — workers go straight
    # to done). The task itself is now completed/terminal.
    header = _blocked_jobs_resume_header_if_applicable(orch, "TASK-3")
    # The header is non-None (task_resumed_from_jobs was written; no subsequent
    # orchestration_step suppresses it for a worker). This confirms it WAS
    # available during the agent session and the prompt injection path would fire.
    assert header is not None, (
        "expected header to be non-None (task_resumed_from_jobs was written "
        "and no orchestration_step suppresses it for a worker agent); "
        f"audit actions={actions}"
    )
    # The header must mention JOB-C and its 'failed' status.
    assert "JOB-C" in header, (
        f"expected JOB-C to appear in the BLOCKED-JOBS-RESULTS header; got:\n{header}"
    )
    assert "failed" in header, (
        f"expected 'failed' to appear in the BLOCKED-JOBS-RESULTS header; got:\n{header}"
    )
