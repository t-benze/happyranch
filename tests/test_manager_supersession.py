"""THR-152 phase-1 regression tests for the closed manager-supersession core."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from runtime.infrastructure.database import Database
from runtime.models import NextStep, TaskRecord, TaskStatus


@pytest.mark.parametrize("payload", [
    {"action": "supersede", "successor_brief": "next", "rationale": "because", "task_id": "TASK-2"},
    {"action": "supersede", "successor_brief": "next", "rationale": "because", "agent": "other"},
    {"action": "supersede", "successor_brief": " ", "rationale": "because"},
    {"action": "supersede", "successor_brief": "next", "rationale": "", "force": True},
    {"action": "supersede", "successor_brief": "next", "rationale": "because", "team": "other"},
    {"action": "supersede", "successor_brief": "next", "rationale": "because", "assigned_agent": "other"},
    {"action": "supersede", "successor_brief": "next", "rationale": "because", "parent_task_id": "TASK-0"},
    {"action": "supersede", "successor_brief": "next", "rationale": "because", "revisit_of_task_id": "TASK-0"},
    {"action": "supersede", "successor_brief": "next", "rationale": "because", "resolves": "TASK-0"},
    {"action": "supersede", "successor_brief": "next", "rationale": "because", "status": "completed"},
])
def test_supersede_payload_is_closed_and_nonblank(payload: dict) -> None:
    with pytest.raises(ValidationError):
        NextStep(**payload)


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "state.db")
    db.insert_task(TaskRecord(
        id="TASK-001", status=TaskStatus.IN_PROGRESS, assigned_agent="engineering_manager",
        team="engineering", brief="original literal brief", current_session_id="session-1",
    ))
    return db


def _supersede(db: Database, task_id: str = "TASK-001") -> str | None:
    return db.try_manager_supersede(
        task_id, actor_agent="engineering_manager", actor_session_id="session-1",
        expected_team="engineering", successor_brief="successor", rationale="rationale",
    )


def test_manager_supersede_is_atomic_and_preserves_bidirectional_provenance(tmp_path: Path) -> None:
    db = _db(tmp_path)
    successor = db.try_manager_supersede(
        "TASK-001", actor_agent="engineering_manager", actor_session_id="session-1",
        expected_team="engineering", successor_brief="successor literal brief", rationale="fresh evidence invalidated plan",
    )

    assert successor == "TASK-002"
    assert db.get_task("TASK-001").status is TaskStatus.SUPERSEDED
    assert db.get_task(successor).status is TaskStatus.PENDING
    row = db.execute("SELECT * FROM manager_supersessions").fetchone()
    assert row["predecessor_task_id"] == "TASK-001"
    assert row["successor_task_id"] == successor
    assert row["predecessor_brief"] == "original literal brief"
    assert row["successor_brief"] == "successor literal brief"
    assert len(row["predecessor_brief_sha256"]) == 64
    assert len(row["successor_brief_sha256"]) == 64
    assert {r["task_id"] for r in db.execute(
        "SELECT task_id FROM audit_log WHERE action='manager_supersession'"
    ).fetchall()} == {"TASK-001", successor}
    audits = {
        row["task_id"]: row["payload"]
        for row in db.execute("SELECT task_id, payload FROM audit_log WHERE action='manager_supersession'")
    }
    assert successor in audits["TASK-001"]
    assert "TASK-001" in audits[successor]
    with pytest.raises(Exception, match="append-only"):
        db.execute("DELETE FROM manager_supersessions")


def test_manager_supersede_rejects_live_work_and_does_not_consume_allowance(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.insert_task(TaskRecord(
        id="TASK-CHILD", status=TaskStatus.PENDING, assigned_agent="dev_agent",
        team="engineering", brief="live", parent_task_id="TASK-001", task_type="subtask",
    ))
    assert _supersede(db) is None
    assert db.get_task("TASK-001").status is TaskStatus.IN_PROGRESS
    assert db.execute("SELECT COUNT(*) FROM manager_supersessions").fetchone()[0] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", TaskStatus.PENDING.value),
        ("status", TaskStatus.ESCALATED.value),
        ("status", TaskStatus.FAILED.value),
        ("status", TaskStatus.CANCELLED.value),
        ("status", TaskStatus.SUPERSEDED.value),
        ("block_kind", "delegated"),
        ("cancelled_at", "2026-08-07T00:00:00+00:00"),
        ("task_type", "subtask"),
        ("parent_task_id", "TASK-PARENT"),
        ("active_chain", "{}"),
        ("active_fanout", "{}"),
        ("blocked_on_job_ids", "[\"JOB-001\"]"),
        ("current_session_id", "stale-session"),
        ("assigned_agent", "another_manager"),
    ],
)
def test_manager_supersede_rejects_every_ineligible_root_state(
    tmp_path: Path, field: str, value: str,
) -> None:
    db = _db(tmp_path)
    db.execute(f"UPDATE tasks SET {field} = ? WHERE id = 'TASK-001'", (value,))
    db._conn.commit()

    assert _supersede(db) is None
    assert db.execute("SELECT COUNT(*) FROM manager_supersessions").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action='manager_supersession'").fetchone()[0] == 0


@pytest.mark.parametrize("job_task_id", ["TASK-001", "TASK-GRANDCHILD"])
def test_manager_supersede_rejects_live_direct_and_descendant_jobs(
    tmp_path: Path, job_task_id: str,
) -> None:
    db = _db(tmp_path)
    if job_task_id == "TASK-GRANDCHILD":
        db.insert_task(TaskRecord(
            id="TASK-CHILD", status=TaskStatus.COMPLETED, assigned_agent="dev_agent",
            team="engineering", brief="finished", parent_task_id="TASK-001", task_type="subtask",
        ))
        db.insert_task(TaskRecord(
            id=job_task_id, status=TaskStatus.COMPLETED, assigned_agent="dev_agent",
            team="engineering", brief="finished", parent_task_id="TASK-CHILD", task_type="subtask",
        ))
    db.execute(
        """INSERT INTO jobs (id, task_id, agent_name, title, script_text, interpreter, status, created_at)
           VALUES (?, ?, 'dev_agent', 'live', 'true', 'bash', 'running', '2026-08-07T00:00:00+00:00')""",
        ("JOB-001", job_task_id),
    )
    db._conn.commit()

    assert _supersede(db) is None
    assert db.get_task("TASK-001").status is TaskStatus.IN_PROGRESS
    assert db.execute("SELECT COUNT(*) FROM manager_supersessions").fetchone()[0] == 0


def test_manager_supersede_rechecks_the_expected_pilot_team(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.execute("UPDATE tasks SET team = 'other' WHERE id = 'TASK-001'")
    db._conn.commit()

    assert _supersede(db) is None
    assert db.get_task("TASK-001").status is TaskStatus.IN_PROGRESS
    assert db.execute("SELECT COUNT(*) FROM manager_supersessions").fetchone()[0] == 0


def test_manager_supersede_rejects_thread_origin_without_mutation(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.execute("UPDATE tasks SET dispatched_from_thread_id = 'THR-152' WHERE id = 'TASK-001'")
    db._conn.commit()

    assert _supersede(db) is None
    assert db.get_task("TASK-001").status is TaskStatus.IN_PROGRESS
    assert db.execute("SELECT COUNT(*) FROM tasks WHERE id = 'TASK-002'").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM manager_supersessions").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action='manager_supersession'").fetchone()[0] == 0


def test_manager_supersede_rolls_back_every_write_when_audit_fails(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.execute(
        """CREATE TRIGGER reject_manager_supersession_audit
           BEFORE INSERT ON audit_log WHEN NEW.action = 'manager_supersession'
           BEGIN SELECT RAISE(ABORT, 'injected audit failure'); END"""
    )
    db._conn.commit()

    with pytest.raises(Exception, match="injected audit failure"):
        _supersede(db)

    assert db.get_task("TASK-001").status is TaskStatus.IN_PROGRESS
    assert db.execute("SELECT COUNT(*) FROM tasks WHERE id = 'TASK-002'").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM manager_supersessions").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action='manager_supersession'").fetchone()[0] == 0


def test_manager_supersede_allows_only_one_original_lineage_reset(tmp_path: Path) -> None:
    db = _db(tmp_path)
    successor = _supersede(db)
    db.update_task(successor, status=TaskStatus.IN_PROGRESS, current_session_id="session-2")
    assert db.try_manager_supersede(
        successor, actor_agent="engineering_manager", actor_session_id="session-2",
        expected_team="engineering", successor_brief="third", rationale="second reset",
    ) is None
    assert db.get_task(successor).status is TaskStatus.IN_PROGRESS
