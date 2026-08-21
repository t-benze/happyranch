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
from runtime.models import BlockKind, TaskRecord, TaskStatus
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
