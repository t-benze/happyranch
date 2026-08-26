"""Tests for the org-portability preflight + reconcile routes (THR-187 Slice A)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import paths
from runtime.daemon.routes.portability import router as portability_router
from runtime.daemon.state import DaemonState
from runtime.infrastructure.database import Database
from runtime.models import (
    BlockKind,
    DreamRecord,
    DreamStatus,
    JobInterpreter,
    JobRecord,
    JobStatus,
    ScheduleKind,
    ScheduleRecord,
    ScheduleStatus,
    TaskRecord,
    TaskStatus,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadRecord,
    WorkHourMode,
    WorkHourRecord,
    WorkHourStatus,
)
from runtime.portability.eligibility import STALE_HEARTBEAT_SECONDS
from runtime.runtime import RuntimeDir

DEAD_PID = 99999
TOKEN = "test-bearer-token"


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")


def _write_token() -> None:
    home = paths.daemon_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "daemon.token").write_text(TOKEN)


def _make_state(tmp_path: Path) -> DaemonState:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    _write_token()
    return DaemonState.from_runtime(rt, Settings())


def _make_app(state: DaemonState) -> FastAPI:
    app = FastAPI()
    app.state.daemon = state
    app.include_router(portability_router, prefix="/api/v1/orgs/{slug}")
    return app


def _client(state: DaemonState) -> TestClient:
    return TestClient(
        _make_app(state),
        headers={"Authorization": f"Bearer {TOKEN}"},
    )


def _insert_in_progress(db: Database, task_id: str, *, block_kind: str | None = None) -> None:
    db.insert_task(TaskRecord(
        id=task_id, brief="test", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
    ))
    db.update_task(task_id, block_kind=block_kind)


def _insert_true_zombie(db: Database, task_id: str, agent: str = "dev_agent") -> None:
    db.insert_task(TaskRecord(
        id=task_id, brief="zombie", team="engineering",
        assigned_agent=agent, status=TaskStatus.IN_PROGRESS,
    ))
    db.update_task(
        task_id,
        current_session_id="sess-dead",
        last_heartbeat=(datetime.now(timezone.utc)
                        - timedelta(seconds=STALE_HEARTBEAT_SECONDS + 10)).isoformat(),
        executor_pid=DEAD_PID,
    )


def _org_entries(state: DaemonState, slug: str) -> set[str]:
    root = state.orgs[slug].root
    return {p.name for p in root.iterdir()}


def test_preflight_eligible_on_empty_org(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    assert r.status_code == 200
    body = r.json()
    assert body["eligible"] is True
    assert body["eligibility"]["eligible"] is True
    assert body["classification"]["rejections"] == []


def test_preflight_refuses_in_progress_with_no_side_effect(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    _insert_in_progress(db, "T-1")
    before = _org_entries(state, "alpha")

    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    assert r.status_code == 200
    body = r.json()
    assert body["eligible"] is False
    assert body["eligibility"]["eligible"] is False
    assert body["eligibility"]["blockers"]["tasks"] == ["T-1"]

    # read-only: no archive/staging/fence side effect created
    assert _org_entries(state, "alpha") == before


@pytest.mark.parametrize(
    "status, block_kind",
    [
        (TaskStatus.PENDING, None),
        (TaskStatus.ESCALATED, None),
        (TaskStatus.IN_PROGRESS, None),
        (TaskStatus.IN_PROGRESS, BlockKind.DELEGATED),
        (TaskStatus.IN_PROGRESS, BlockKind.BLOCKED_ON_JOB),
    ],
    ids=["pending", "escalated", "in_progress",
         "in_progress_delegated", "in_progress_blocked_on_job"],
)
def test_preflight_refuses_every_nonterminal_form(
    tmp_path: Path, status: TaskStatus, block_kind: BlockKind | None,
) -> None:
    """Production-seam proof: seed each persisted nonterminal form through the
    real Database shape and assert the HTTP route returns ineligible with the
    task id in eligibility.blockers.tasks (Database.get_nonterminal_task_ids ->
    _gather_task_liveness -> compute_eligibility -> route)."""
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    db.insert_task(TaskRecord(
        id="T-1", brief="test", team="engineering",
        assigned_agent="dev_agent", status=status,
    ))
    if block_kind is not None:
        db.update_task("T-1", block_kind=block_kind.value)
    before = _org_entries(state, "alpha")

    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    assert r.status_code == 200
    body = r.json()
    assert body["eligible"] is False
    assert body["eligibility"]["eligible"] is False
    assert body["eligibility"]["blockers"]["tasks"] == ["T-1"]
    # read-only for every persisted nonterminal form
    assert _org_entries(state, "alpha") == before


def test_preflight_reports_possible_zombie(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    _insert_true_zombie(db, "T-Z")
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    assert body["eligible"] is False
    assert [z["task_id"] for z in body["eligibility"]["possible_zombies"]] == ["T-Z"]
    # reported, not resolved — task is still in_progress
    assert db.get_task("T-Z").status == TaskStatus.IN_PROGRESS


def test_preflight_requires_bearer(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    client = TestClient(_make_app(state))  # no bearer
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    assert r.status_code == 401


def test_reconcile_requires_human_bearer(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    client = TestClient(_make_app(state))  # no bearer
    r = client.post(
        "/api/v1/orgs/alpha/reconcile-portability",
        json={"candidate_task_id": "T-1", "disposition": "cancel"},
    )
    assert r.status_code == 401


def test_reconcile_refuses_delegated_task(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    _insert_in_progress(db, "T-DELEG", block_kind=BlockKind.DELEGATED.value)
    client = _client(state)
    r = client.post(
        "/api/v1/orgs/alpha/reconcile-portability",
        json={"candidate_task_id": "T-DELEG", "disposition": "cancel"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "not_a_zombie"
    assert "never zombies" in r.json()["detail"]["reason"]
    # no state change
    assert db.get_task("T-DELEG").status == TaskStatus.IN_PROGRESS


def test_reconcile_refuses_unknown_task(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    client = _client(state)
    r = client.post(
        "/api/v1/orgs/alpha/reconcile-portability",
        json={"candidate_task_id": "T-NOPE", "disposition": "cancel"},
    )
    assert r.status_code == 404


def test_reconcile_cancels_true_zombie_and_audits(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    _insert_true_zombie(db, "T-Z")
    client = _client(state)
    r = client.post(
        "/api/v1/orgs/alpha/reconcile-portability",
        json={
            "candidate_task_id": "T-Z",
            "disposition": "cancel",
            "evidence": {"reason": "dead pid + stale heartbeat"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "T-Z"
    assert body["disposition"] == "cancel"
    assert len(body["request_hash"]) == 64  # sha256 hex

    t = db.get_task("T-Z")
    assert t.status == TaskStatus.CANCELLED
    assert t.cancelled_at is not None

    # audit row: ordinary task_id scope + distinct action + hash + before/after
    audit_rows = db.get_audit_logs("T-Z")
    reconciled = [row for row in audit_rows if row["action"] == "portability_reconciled"]
    assert len(reconciled) == 1
    payload = reconciled[0]["payload"]
    assert payload["request_hash"] == body["request_hash"]
    assert payload["disposition"] == "cancel"
    assert payload["before"]["status"] == "in_progress"
    assert payload["after"]["status"] == "cancelled"


def test_reconcile_consume_result_without_fingerprint_refuses(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    _insert_true_zombie(db, "T-Z")
    client = _client(state)
    r = client.post(
        "/api/v1/orgs/alpha/reconcile-portability",
        json={"candidate_task_id": "T-Z", "disposition": "consume_result"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_result_to_consume"


def test_reconcile_consume_result_terminalizes(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    _insert_true_zombie(db, "T-Z")
    db.insert_task_result(
        task_id="T-Z", agent="dev_agent", session_id="sess-dead",
        status="completed", confidence_score=90, output_summary="done",
        decision_json='{"action": "done"}',
    )
    client = _client(state)
    r = client.post(
        "/api/v1/orgs/alpha/reconcile-portability",
        json={"candidate_task_id": "T-Z", "disposition": "consume_result"},
    )
    assert r.status_code == 200
    assert r.json()["disposition"] == "consume_result"
    t = db.get_task("T-Z")
    assert t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)


def test_reconcile_rejects_bad_disposition(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    client = _client(state)
    r = client.post(
        "/api/v1/orgs/alpha/reconcile-portability",
        json={"candidate_task_id": "T-1", "disposition": "export"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "bad_disposition"


# ---------------------------------------------------------------------------
# Exhaustive liveness regression (THR-187 Slice A, third fix-forward): the
# presentation list APIs (``list_dreams`` / ``work_hours.list`` /
# ``schedules.list``) are capped at 500 and ordered newest-first. Preflight
# must not collect active ids through them, or an old active row behind 500
# newer terminal rows is hidden and the org is wrongly reported eligible.
# Each test seeds 500 newer terminal rows plus one older active row through the
# real record/store shapes (ordering key: dream/work-hour ``scheduled_for``,
# schedule ``created_at``) and asserts the route still reports the old active
# id in the corresponding blockers field, with no preflight side effect.

_OLD = datetime(2020, 1, 1, tzinfo=timezone.utc)
_NEW_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _seed_500_terminal_dreams_plus_old_active(db: Database) -> str:
    active_id = "DREAM-ACTIVE"
    for i in range(500):
        newer = _NEW_BASE + timedelta(minutes=i)
        db.insert_dream(DreamRecord(
            id=f"DREAM-{i:03d}", agent_name=f"agent-{i}",
            local_date="2026-01-01", scheduled_for=newer, window_end=newer,
            status=DreamStatus.COMPLETED,
        ))
    db.insert_dream(DreamRecord(
        id=active_id, agent_name="dev_agent", local_date="2020-01-01",
        scheduled_for=_OLD, window_end=_OLD, status=DreamStatus.PENDING,
    ))
    return active_id


def _seed_500_terminal_work_hours_plus_old_active(db: Database) -> str:
    active_id = "WORKHOUR-ACTIVE"
    for i in range(500):
        newer = _NEW_BASE + timedelta(minutes=i)
        db.work_hours.insert(WorkHourRecord(
            id=f"WORKHOUR-{i:03d}", agent_name=f"agent-{i}",
            local_date="2026-01-01", slot=f"{i % 24:02d}:00",
            mode=WorkHourMode.WINDOWED, scheduled_for=newer,
            status=WorkHourStatus.COMPLETED,
        ))
    db.work_hours.insert(WorkHourRecord(
        id=active_id, agent_name="dev_agent", local_date="2020-01-01",
        slot="00:00", mode=WorkHourMode.WINDOWED, scheduled_for=_OLD,
        status=WorkHourStatus.PENDING,
    ))
    return active_id


def _seed_500_terminal_schedules_plus_old_active(db: Database) -> str:
    active_id = "SCHEDULE-ACTIVE"
    for i in range(500):
        newer = _NEW_BASE + timedelta(minutes=i)
        db.schedules.insert(ScheduleRecord(
            id=f"SCHEDULE-{i:03d}", agent_name=f"agent-{i}",
            kind=ScheduleKind.ONE_SHOT, fire_at=newer,
            normalized_brief="terminal", source_instruction="terminal",
            status=ScheduleStatus.FIRED, active=0, created_at=newer,
        ))
    db.schedules.insert(ScheduleRecord(
        id=active_id, agent_name="dev_agent", kind=ScheduleKind.ONE_SHOT,
        fire_at=_OLD, normalized_brief="active", source_instruction="active",
        status=ScheduleStatus.ARMED, created_at=_OLD,
    ))
    return active_id


def test_preflight_refuses_old_active_dream_after_500_terminal(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    active_id = _seed_500_terminal_dreams_plus_old_active(db)
    before = _org_entries(state, "alpha")

    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    assert r.status_code == 200
    body = r.json()
    assert body["eligible"] is False
    assert body["eligibility"]["eligible"] is False
    assert active_id in body["eligibility"]["blockers"]["active_dreams"]
    assert _org_entries(state, "alpha") == before  # read-only


def test_preflight_refuses_old_active_work_hour_after_500_terminal(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    active_id = _seed_500_terminal_work_hours_plus_old_active(db)
    before = _org_entries(state, "alpha")

    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    assert r.status_code == 200
    body = r.json()
    assert body["eligible"] is False
    assert body["eligibility"]["eligible"] is False
    assert active_id in body["eligibility"]["blockers"]["active_work_hours"]
    assert _org_entries(state, "alpha") == before  # read-only


def test_preflight_refuses_old_active_schedule_after_500_terminal(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    active_id = _seed_500_terminal_schedules_plus_old_active(db)
    before = _org_entries(state, "alpha")

    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    assert r.status_code == 200
    body = r.json()
    assert body["eligible"] is False
    assert body["eligibility"]["eligible"] is False
    assert active_id in body["eligibility"]["blockers"]["active_schedules"]
    assert _org_entries(state, "alpha") == before  # read-only


def test_presentation_list_cap_unchanged_after_501_rows(tmp_path: Path) -> None:
    """Narrow preservation: the generic presentation list cap (500, newest-first)
    is unchanged. The exhaustive fix is preflight-only and must not loosen the
    generic list APIs their existing callers rely on."""
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    _seed_500_terminal_dreams_plus_old_active(db)
    assert len(db.list_dreams(limit=500)) == 500
    assert all(d.status == DreamStatus.COMPLETED for d in db.list_dreams(limit=500))


# ---------------------------------------------------------------------------
# Session / queue / invocation / job liveness (TASK-5346 matrix, route proof)
# ---------------------------------------------------------------------------

def _insert_job(
    db: Database,
    job_id: str,
    status: JobStatus,
    *,
    created_at: str | None = None,
) -> None:
    db.insert_job(JobRecord(
        id=job_id, task_id="T-1", agent_name="dev_agent", title="t",
        rationale="r", script_text="echo hi", interpreter=JobInterpreter.BASH,
        status=status,
        created_at=created_at or datetime.now(timezone.utc).isoformat(),
    ))


def _insert_schedule(
    db: Database,
    schedule_id: str,
    status: ScheduleStatus,
    *,
    created_at: datetime | None = None,
) -> None:
    db.schedules.insert(ScheduleRecord(
        id=schedule_id, agent_name="dev_agent", kind=ScheduleKind.ONE_SHOT,
        fire_at=_NEW_BASE, normalized_brief="b", source_instruction="s",
        status=status, active=1 if status == ScheduleStatus.ARMED else 0,
        created_at=created_at or _NEW_BASE,
    ))


def test_preflight_refuses_active_session_binding(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    org = state.orgs["alpha"]
    org.sessions.set_active("T-1", "dev_agent", "sess-live")
    before = _org_entries(state, "alpha")

    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    assert r.status_code == 200
    assert body["eligible"] is False
    assert body["eligibility"]["blockers"]["active_sessions"] == 1
    assert _org_entries(state, "alpha") == before  # read-only


def test_preflight_refuses_queue_entry(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    state.queue.put_nowait("alpha", "T-1")
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    assert body["eligible"] is False
    assert body["eligibility"]["blockers"]["queued_items"] == 1


def test_preflight_ignores_other_org_queue_entry(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    state.queue.put_nowait("beta", "T-9")
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    assert body["eligible"] is True
    assert body["eligibility"]["blockers"]["queued_items"] == 0


@pytest.mark.parametrize(
    "purpose",
    [ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP,
     ThreadInvocationPurpose.TASK_FOLLOWUP],
    ids=["reply", "bootstrap", "task_followup"],
)
def test_preflight_refuses_pending_invocation(
    tmp_path: Path, purpose: ThreadInvocationPurpose,
) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    db.insert_thread(ThreadRecord(id="TH-1", subject="t"))
    db.mint_thread_invocation(
        thread_id="TH-1", agent_name="dev_agent",
        triggering_seq=1, purpose=purpose,
    )
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    assert body["eligible"] is False
    assert body["eligibility"]["blockers"]["pending_thread_invocations"] >= 1


@pytest.mark.parametrize(
    "terminalize", ["consume", "decline", "fail"],
    ids=["consumed", "declined", "failed"],
)
def test_preflight_ignores_terminal_invocations(
    tmp_path: Path, terminalize: str,
) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    db.insert_thread(ThreadRecord(id="TH-1", subject="t"))
    inv = db.mint_thread_invocation(
        thread_id="TH-1", agent_name="dev_agent",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    if terminalize == "consume":
        db.consume_invocation(inv.invocation_token)
    elif terminalize == "decline":
        db.mark_invocation_declined(inv.invocation_token, decline_reason="test")
    else:
        db.fail_invocation(
            inv.invocation_token,
            status=ThreadInvocationStatus.FAILED,
            decline_reason="test",
        )
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    assert body["eligible"] is True
    assert body["eligibility"]["blockers"]["pending_thread_invocations"] == 0


@pytest.mark.parametrize(
    "status", [JobStatus.PENDING, JobStatus.RUNNING],
    ids=["pending", "running"],
)
def test_preflight_refuses_active_job(tmp_path: Path, status: JobStatus) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    _insert_job(db, "JOB-1", status)
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    assert body["eligible"] is False
    assert body["eligibility"]["blockers"]["active_jobs"] == ["JOB-1"]


def test_preflight_refuses_501_active_jobs_uncapped(tmp_path: Path) -> None:
    """Anti-cap proof: all 501 active jobs are reported (the 501st cannot be
    hidden by the capped presentation list), a terminal job stays ineligible,
    and the presentation list cap itself is preserved."""
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    base = datetime(2020, 1, 1, tzinfo=timezone.utc)
    ids: list[str] = []
    for i in range(501):
        jid = f"JOB-{i:04d}"
        ids.append(jid)
        _insert_job(
            db, jid, JobStatus.PENDING,
            created_at=(base + timedelta(minutes=i)).isoformat(),
        )
    oldest = ids[0]
    _insert_job(db, "JOB-TERMINAL", JobStatus.COMPLETED)  # ineligible control

    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    assert body["eligible"] is False
    active = body["eligibility"]["blockers"]["active_jobs"]
    assert len(active) == 501
    assert active == ids  # deterministic ascending created_at order
    assert oldest in active
    assert "JOB-TERMINAL" not in active

    # the generic presentation list stays capped (501st active hidden)
    assert len(db.list_jobs_db(status=["pending", "running"], limit=500)) == 500


def test_preflight_refuses_running_dream(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    db.insert_dream(DreamRecord(
        id="DREAM-RUN", agent_name="dev_agent", local_date="2026-01-01",
        scheduled_for=_NEW_BASE, window_end=_NEW_BASE, status=DreamStatus.RUNNING,
    ))
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    assert body["eligible"] is False
    assert "DREAM-RUN" in body["eligibility"]["blockers"]["active_dreams"]


def test_preflight_refuses_running_work_hour(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    db.work_hours.insert(WorkHourRecord(
        id="WORKHOUR-RUN", agent_name="dev_agent", local_date="2026-01-01",
        slot="00:00", mode=WorkHourMode.WINDOWED, scheduled_for=_NEW_BASE,
        status=WorkHourStatus.RUNNING,
    ))
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    assert body["eligible"] is False
    assert "WORKHOUR-RUN" in body["eligibility"]["blockers"]["active_work_hours"]


@pytest.mark.parametrize(
    "status", [ScheduleStatus.ARMED, ScheduleStatus.FIRING],
    ids=["armed", "firing"],
)
def test_preflight_refuses_armed_and_firing_schedule(
    tmp_path: Path, status: ScheduleStatus,
) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    _insert_schedule(db, "SCHED-1", status)
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    assert body["eligible"] is False
    assert "SCHED-1" in body["eligibility"]["blockers"]["active_schedules"]


def test_preflight_armed_schedule_remedy_is_pause_or_cancel(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    _insert_schedule(db, "SCHED-A", ScheduleStatus.ARMED)
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    sched = [x for x in body["remedies"]
             if x["kind"] == "schedule" and x["target"] == "SCHED-A"]
    assert len(sched) == 1
    assert sched[0]["status"] == "armed"
    assert "happyranch todos pause --org alpha SCHED-A" in sched[0]["remedy"]
    assert "happyranch todos cancel --org alpha SCHED-A" in sched[0]["remedy"]


def test_preflight_firing_schedule_remedy_is_wait_not_disarm(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    _insert_schedule(db, "SCHED-F", ScheduleStatus.FIRING)
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    sched = [x for x in body["remedies"]
             if x["kind"] == "schedule" and x["target"] == "SCHED-F"]
    assert len(sched) == 1
    assert sched[0]["status"] == "firing"
    # firing cannot be paused/cancelled: no pause/cancel command, no export fence
    assert "happyranch todos pause" not in sched[0]["remedy"]
    assert "happyranch todos cancel" not in sched[0]["remedy"]
    assert "fence" not in sched[0]["remedy"]
    assert "export" not in sched[0]["remedy"]
    assert "terminal" in sched[0]["remedy"]


def test_preflight_task_remedy_is_existing_cancel(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    _insert_in_progress(db, "T-1")
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    t = [x for x in body["remedies"]
         if x["kind"] == "task" and x["target"] == "T-1"]
    assert len(t) == 1
    assert "happyranch cancel T-1 --org alpha" in t[0]["remedy"]


def test_preflight_zombie_remedy_is_reconcile_portability(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    _insert_true_zombie(db, "T-Z")
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    assert body["eligible"] is False
    z = [x for x in body["remedies"]
         if x["kind"] == "zombie" and x["target"] == "T-Z"]
    assert len(z) == 1
    assert ("happyranch orgs reconcile-portability alpha --from-file"
            in z[0]["remedy"])
    # reported, not resolved
    assert db.get_task("T-Z").status == TaskStatus.IN_PROGRESS


def test_preflight_eligible_has_no_remedies(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    assert body["eligible"] is True
    assert body["remedies"] == []


def test_preflight_parked_task_is_blocker_but_not_zombie(tmp_path: Path) -> None:
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    db.insert_task(TaskRecord(
        id="T-PARKED", brief="parked", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
    ))
    db.update_task(
        "T-PARKED",
        block_kind=BlockKind.BLOCKED_ON_JOB.value,
        last_heartbeat=(datetime.now(timezone.utc)
                        - timedelta(seconds=STALE_HEARTBEAT_SECONDS + 10)).isoformat(),
        executor_pid=DEAD_PID,
    )
    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    body = r.json()
    assert body["eligible"] is False
    assert body["eligibility"]["blockers"]["tasks"] == ["T-PARKED"]
    assert body["eligibility"]["possible_zombies"] == []


def test_preflight_is_read_only_across_all_blocker_surfaces(tmp_path: Path) -> None:
    """Production-seam read-only proof across every durable liveness surface.
    Seeds one of each blocker class — a persisted task, an armed schedule, a
    running job, a queue item, an active session binding, a pending thread
    invocation, a pending dream, and a pending work-hour — snapshots their
    identifying fields and statuses (not merely a count), asserts the route
    refuses with the intended blockers, then proves every snapshot is
    unchanged after GET (no dequeue, terminalization/cancel, disarm,
    reconciliation, archive/staging/fence side effect)."""
    state = _make_state(tmp_path)
    db = state.orgs["alpha"].db
    org = state.orgs["alpha"]
    _insert_in_progress(db, "T-1")
    _insert_schedule(db, "SCHED-A", ScheduleStatus.ARMED)
    _insert_job(db, "JOB-1", JobStatus.RUNNING)
    db.insert_thread(ThreadRecord(id="TH-1", subject="t"))
    inv = db.mint_thread_invocation(
        thread_id="TH-1", agent_name="dev_agent",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db.insert_dream(DreamRecord(
        id="DREAM-1", agent_name="dev_agent", local_date="2026-01-01",
        scheduled_for=_NEW_BASE, window_end=_NEW_BASE, status=DreamStatus.PENDING,
    ))
    db.work_hours.insert(WorkHourRecord(
        id="WORKHOUR-1", agent_name="dev_agent", local_date="2026-01-01",
        slot="00:00", mode=WorkHourMode.WINDOWED, scheduled_for=_NEW_BASE,
        status=WorkHourStatus.PENDING,
    ))
    state.queue.put_nowait("alpha", "T-9")
    org.sessions.set_active("T-1", "dev_agent", "sess-1")

    before_entries = _org_entries(state, "alpha")
    before_task = db.get_task("T-1").status
    before_sched = db.schedules.get("SCHED-A").status
    before_job = db.get_job("JOB-1").status
    before_sessions = org.sessions.count_active()
    before_queue = state.queue.pending_slugs()
    # identifying fields + status for the invocation/dream/work-hour surfaces
    before_inv = db.get_invocation_any_status(inv.invocation_token)
    before_inv_snapshot = (
        before_inv.invocation_token, before_inv.thread_id, before_inv.agent_name,
        before_inv.triggering_seq, before_inv.purpose.value, before_inv.status.value,
        before_inv.started_at, before_inv.consumed_at, before_inv.session_id,
        before_inv.dispatched_task_id, before_inv.decline_reason,
    )
    before_pending_tokens = [
        i.invocation_token for i in db.list_pending_thread_invocations()
    ]
    before_dream = db.get_dream("DREAM-1")
    before_dream_active_ids = db.list_dream_ids_by_status({"pending", "running"})
    before_work_hour = db.work_hours.get("WORKHOUR-1")
    before_work_hour_active_ids = db.work_hours.list_ids_by_status(
        {"pending", "running"},
    )

    client = _client(state)
    r = client.get("/api/v1/orgs/alpha/portability-preflight")
    assert r.status_code == 200
    body = r.json()
    assert body["eligible"] is False
    blockers = body["eligibility"]["blockers"]
    assert blockers["pending_thread_invocations"] >= 1
    assert "DREAM-1" in blockers["active_dreams"]
    assert "WORKHOUR-1" in blockers["active_work_hours"]

    assert _org_entries(state, "alpha") == before_entries
    assert db.get_task("T-1").status == before_task
    assert db.schedules.get("SCHED-A").status == before_sched
    assert db.get_job("JOB-1").status == before_job
    assert org.sessions.count_active() == before_sessions
    assert state.queue.pending_slugs() == before_queue

    # the seeded invocation/dream/work-hour are unchanged after GET
    after_inv = db.get_invocation_any_status(inv.invocation_token)
    assert (
        after_inv.invocation_token, after_inv.thread_id, after_inv.agent_name,
        after_inv.triggering_seq, after_inv.purpose.value, after_inv.status.value,
        after_inv.started_at, after_inv.consumed_at, after_inv.session_id,
        after_inv.dispatched_task_id, after_inv.decline_reason,
    ) == before_inv_snapshot
    assert [
        i.invocation_token for i in db.list_pending_thread_invocations()
    ] == before_pending_tokens
    assert db.get_dream("DREAM-1").status == before_dream.status
    assert db.list_dream_ids_by_status({"pending", "running"}) == before_dream_active_ids
    assert db.work_hours.get("WORKHOUR-1").status == before_work_hour.status
    assert db.work_hours.list_ids_by_status(
        {"pending", "running"},
    ) == before_work_hour_active_ids
