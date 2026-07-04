"""Tests for native fan-out Phase 1 — models, validation, state, and orchestration.
"""
from __future__ import annotations

import json

from runtime.config import Settings

import pytest
from pathlib import Path

from runtime.infrastructure.database import Database
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir


@pytest.fixture
def runtime(tmp_path: Path) -> OrgPaths:
    """Shared runtime fixture for fanout orchestration tests — same shape as
    test_run_step.py's fixture."""
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent, qa_engineer, agent0, agent1, agent2, agent3, agent4]\n"
    )
    return paths


@pytest.fixture
def db(runtime: OrgPaths) -> Database:
    return Database(runtime.db_path)

from runtime.models import (
    ChainLeg,
    FanoutChild,
    JobStatus,
    NextStep,
    TaskRecord,
    TaskStatus,
    BlockKind,
)
from runtime.orchestrator.fanout import (
    MAX_FANOUT_WIDTH,
    FanoutState,
    build_fanout_join_context,
    collect_child_join_info,
    fanout_child_targets,
    validate_fanout_decision,
)


# --- Model validation tests ---


class TestFanoutValidation:
    def test_accepts_valid_fanout(self):
        decision = NextStep(
            action="fanout",
            children=[
                FanoutChild(agent="dev_agent", prompt="task 1"),
                FanoutChild(agent="qa_engineer", prompt="task 2"),
            ],
            width_cap_ack=2,
        )
        assert validate_fanout_decision(decision) is None

    def test_accepts_fanout_min_width_2(self):
        decision = NextStep(
            action="fanout",
            children=[
                FanoutChild(agent="a", prompt="1"),
                FanoutChild(agent="b", prompt="2"),
            ],
            width_cap_ack=2,
        )
        assert validate_fanout_decision(decision) is None

    def test_accepts_max_width(self):
        children = [FanoutChild(agent=f"agent{i}", prompt=f"task {i}") for i in range(MAX_FANOUT_WIDTH)]
        decision = NextStep(action="fanout", children=children, width_cap_ack=MAX_FANOUT_WIDTH)
        assert validate_fanout_decision(decision) is None

    def test_rejects_empty_children(self):
        decision = NextStep(action="fanout", children=[])
        err = validate_fanout_decision(decision)
        assert err is not None
        assert "at least one child" in err

    def test_rejects_single_child(self):
        decision = NextStep(action="fanout", children=[FanoutChild(agent="a", prompt="x")])
        err = validate_fanout_decision(decision)
        assert err is not None
        assert "single child" in err.lower() or "use delegate" in err.lower()

    def test_rejects_over_cap(self):
        children = [FanoutChild(agent=f"a{i}", prompt="x") for i in range(MAX_FANOUT_WIDTH + 1)]
        decision = NextStep(action="fanout", children=children)
        err = validate_fanout_decision(decision)
        assert err is not None
        assert "exceeds max" in err

    def test_rejects_width_cap_ack_mismatch(self):
        decision = NextStep(
            action="fanout",
            children=[FanoutChild(agent="a", prompt="1"), FanoutChild(agent="b", prompt="2")],
            width_cap_ack=3,
        )
        err = validate_fanout_decision(decision)
        assert err is not None
        assert "does not match" in err

    def test_rejects_missing_width_cap_ack(self):
        """width_cap_ack is mandatory for fan-out decisions."""
        decision = NextStep(
            action="fanout",
            children=[FanoutChild(agent="a", prompt="1"), FanoutChild(agent="b", prompt="2")],
        )
        err = validate_fanout_decision(decision)
        assert err is not None
        assert "width_cap_ack" in err.lower() or "match" in err.lower()

    def test_accepts_parallel_action_alias(self):
        """action='parallel' is accepted and normalized to 'fanout'."""
        decision = NextStep(
            action="parallel",
            children=[
                FanoutChild(agent="a", prompt="1"),
                FanoutChild(agent="b", prompt="2"),
            ],
            width_cap_ack=2,
        )
        # The field_validator normalizes 'parallel' → 'fanout' after parsing.
        assert decision.action == "fanout"
        assert validate_fanout_decision(decision) is None

    def test_accepts_per_child_expect_verdict_phase2(self):
        """Phase 2: expect_verdict on a fan-out child is now accepted (pipeline)."""
        decision = NextStep(
            action="fanout",
            children=[
                FanoutChild(agent="a", prompt="1"),
                FanoutChild(agent="b", prompt="2", expect_verdict="APPROVE"),
            ],
            width_cap_ack=2,
        )
        assert validate_fanout_decision(decision) is None

    def test_accepts_per_child_then_phase2(self):
        """Phase 2: per-child then with valid legs is now accepted (pipeline)."""
        decision = NextStep(
            action="fanout",
            children=[
                FanoutChild(agent="a", prompt="1"),
                FanoutChild(agent="b", prompt="2", then=[ChainLeg(agent="c", prompt="review")]),
            ],
            width_cap_ack=2,
        )
        assert validate_fanout_decision(decision) is None


# --- FanoutState tests ---


class TestFanoutState:
    def test_serialize_roundtrip(self):
        fs = FanoutState(
            children_ids=["TASK-100", "TASK-101"],
            children_details=[],
            width=2,
            manager_agent="eng_mgr",
            join_summary="Review both",
            status="spawned",
        )
        serialized = fs.serialize()
        fs2 = FanoutState.deserialize(serialized)
        assert fs2.children_ids == ["TASK-100", "TASK-101"]
        assert fs2.width == 2
        assert fs2.manager_agent == "eng_mgr"
        assert fs2.join_summary == "Review both"

    def test_serialize_omits_none_join_summary(self):
        fs = FanoutState(children_ids=["T-1"], children_details=[], width=1, manager_agent="m", status="spawned")
        s = fs.serialize()
        fs2 = FanoutState.deserialize(s)
        assert fs2.join_summary is None


# --- Join context tests ---


class TestFanoutJoinContext:
    def test_build_join_context_includes_all_children(self):
        fs = FanoutState(children_ids=["T-1", "T-2"], children_details=[], width=2, manager_agent="mgr", status="spawned")
        # Create mock child join info
        infos = [
            type("_CJI", (), {
                "id": "T-1", "agent": "dev", "status": "completed",
                "verdict": None, "confidence": 80,
                "summary_excerpt": "Built feature", "output_dir": "output/T-1",
                "failure_note": None,
            })(),
            type("_CJI", (), {
                "id": "T-2", "agent": "qa", "status": "failed",
                "verdict": None, "confidence": 80,
                "summary_excerpt": "Test failed", "output_dir": None,
                "failure_note": "Test failure",
            })(),
        ]
        ctx = build_fanout_join_context(
            parent_task_id="TASK-PARENT",
            fanout=fs,
            child_results=infos,
        )
        assert "All 2 fan-out children" in ctx
        assert "T-1" in ctx
        assert "T-2" in ctx
        assert "completed" in ctx
        assert "failed" in ctx
        assert "Built feature" in ctx
        assert "Test failure" in ctx

    def test_join_context_includes_join_summary(self):
        fs = FanoutState(
            children_ids=["T-1"], children_details=[], width=1, manager_agent="mgr",
            join_summary="Combine results",
            status="spawned",
        )
        infos = [type("_CJI", (), {
            "id": "T-1", "agent": "dev", "status": "completed",
            "verdict": None, "confidence": 95,
            "summary_excerpt": "Done", "output_dir": None, "failure_note": None,
        })()]
        ctx = build_fanout_join_context(
            parent_task_id="PARENT", fanout=fs, child_results=infos,
        )
        assert "Combine results" in ctx

    def test_collect_child_join_info_from_task_records(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        t1 = TaskRecord(
            id="T-001", brief="x", status=TaskStatus.COMPLETED,
            assigned_agent="dev", note="Built feature X",
            final_output_dir="output/T-001",
            created_at=now, updated_at=now,
        )
        t2 = TaskRecord(
            id="T-002", brief="y", status=TaskStatus.FAILED,
            assigned_agent="qa", note="Tests failed",
            created_at=now, updated_at=now,
        )
        infos = collect_child_join_info([t1, t2])
        assert len(infos) == 2
        assert infos[0].id == "T-001"
        assert infos[0].status == "completed"
        assert infos[0].summary_excerpt == "Built feature X"
        assert infos[1].id == "T-002"
        assert infos[1].status == "failed"
        assert infos[1].failure_note == "Tests failed"


# --- Fanout helpers ---


class TestFanoutHelpers:
    def test_fanout_child_targets(self):
        decision = NextStep(
            action="fanout",
            children=[
                FanoutChild(agent="dev_agent", prompt="t1"),
                FanoutChild(agent="qa_engineer", prompt="t2"),
            ],
        )
        targets = fanout_child_targets(decision)
        assert targets == ["dev_agent", "qa_engineer"]



# --- Database tests ---


class TestFanoutDatabase:
    def test_active_fanout_migration_adds_column(self, db):
        """active_fanout column exists after DB init."""
        cols = db._conn.execute("PRAGMA table_info(tasks)").fetchall()
        col_names = {row["name"] for row in cols}
        assert "active_fanout" in col_names

    def test_task_record_active_fanout_null_by_default(self, db):
        db.insert_task(TaskRecord(id="TASK-FT001", brief="fanout test"))
        t = db.get_task("TASK-FT001")
        assert t is not None
        assert t.active_fanout is None

    def test_update_task_active_fanout_set_and_clear(self, db):
        db.insert_task(TaskRecord(id="TASK-FT002", brief="fanout test"))
        db.update_task_active_fanout("TASK-FT002", '{"v":1}')
        t = db.get_task("TASK-FT002")
        assert t.active_fanout == '{"v":1}'
        db.update_task_active_fanout("TASK-FT002", None)
        t = db.get_task("TASK-FT002")
        assert t.active_fanout is None

    def test_try_delegate_many_inserts_all_children(self, db):
        parent = TaskRecord(id="TASK-FTP", brief="fanout parent")
        db.insert_task(parent)
        children = [
            TaskRecord(id="TASK-FTC1", brief="child 1", parent_task_id="TASK-FTP", assigned_agent="dev", task_type="subtask"),
            TaskRecord(id="TASK-FTC2", brief="child 2", parent_task_id="TASK-FTP", assigned_agent="qa", task_type="subtask"),
        ]
        ok = db.try_delegate_many(
            "TASK-FTP", children, parent_note="Fan-out 2 children",
            active_fanout_json='{"v":1}',
        )
        assert ok is True
        c1 = db.get_task("TASK-FTC1")
        assert c1 is not None and c1.parent_task_id == "TASK-FTP"
        c2 = db.get_task("TASK-FTC2")
        assert c2 is not None
        p = db.get_task("TASK-FTP")
        assert p.status == TaskStatus.IN_PROGRESS
        assert p.block_kind == BlockKind.DELEGATED
        assert p.active_fanout == '{"v":1}'

    def test_try_delegate_many_rejects_cancelled_parent(self, db):
        from datetime import datetime, timezone
        parent = TaskRecord(
            id="TASK-FTP2", brief="x", status=TaskStatus.FAILED,
            cancelled_at=datetime.now(timezone.utc).isoformat(),
        )
        db.insert_task(parent)
        children = [TaskRecord(id="TASK-FTC3", brief="c", parent_task_id="TASK-FTP2", task_type="subtask")]
        ok = db.try_delegate_many("TASK-FTP2", children, parent_note="nope")
        assert ok is False
        assert db.get_task("TASK-FTC3") is None

    def test_try_delegate_many_rejects_already_terminal(self, db):
        parent = TaskRecord(id="TASK-FTP3", brief="x", status=TaskStatus.COMPLETED)
        db.insert_task(parent)
        children = [TaskRecord(id="TASK-FTC4", brief="c", parent_task_id="TASK-FTP3", task_type="subtask")]
        ok = db.try_delegate_many("TASK-FTP3", children, parent_note="nope")
        assert ok is False
        assert db.get_task("TASK-FTC4") is None

    def test_try_delegate_many_get_children(self, db):
        parent = TaskRecord(id="TASK-FTP4", brief="x")
        db.insert_task(parent)
        children = [
            TaskRecord(id="TASK-FTC5", brief="c1", parent_task_id="TASK-FTP4", assigned_agent="dev", task_type="subtask"),
            TaskRecord(id="TASK-FTC6", brief="c2", parent_task_id="TASK-FTP4", assigned_agent="qa", task_type="subtask"),
            TaskRecord(id="TASK-FTC7", brief="c3", parent_task_id="TASK-FTP4", assigned_agent="sr", task_type="subtask"),
        ]
        ok = db.try_delegate_many("TASK-FTP4", children, parent_note="fanout 3")
        assert ok is True
        child_ids = db.get_children("TASK-FTP4")
        assert set(child_ids) == {"TASK-FTC5", "TASK-FTC6", "TASK-FTC7"}

    def test_try_delegate_many_non_existent_parent(self, db):
        children = [TaskRecord(id="TASK-FTC8", brief="c", parent_task_id="NONEXISTENT", task_type="subtask")]
        ok = db.try_delegate_many("NONEXISTENT", children, parent_note="nope")
        assert ok is False
        assert db.get_task("TASK-FTC8") is None

    def test_try_delegate_many_atomic_rollback_on_conflict(self, db):
        """Reviewer probe: a later child insert failure (UNIQUE conflict)
        must leave no new children committed and the parent unchanged.

        Reproduces: two children with the same id (duplicate) causes the
        second insert to fail; the transaction must roll back so child 1
        is NOT committed and the parent is NOT parked.
        """
        parent = TaskRecord(id="TASK-FTP-DUP", brief="duplicate child ids")
        db.insert_task(parent)
        parent_before = db.get_task("TASK-FTP-DUP")
        assert parent_before.status == TaskStatus.PENDING

        children = [
            TaskRecord(id="TASK-FTC-DUP", brief="child 1", parent_task_id="TASK-FTP-DUP", task_type="subtask"),
            TaskRecord(id="TASK-FTC-DUP", brief="child 2", parent_task_id="TASK-FTP-DUP", task_type="subtask"),
        ]
        with pytest.raises(Exception):
            db.try_delegate_many("TASK-FTP-DUP", children, parent_note="should roll back")

        # Neither child was committed.
        assert db.get_task("TASK-FTC-DUP") is None
        # Parent unchanged.
        parent_after = db.get_task("TASK-FTP-DUP")
        assert parent_after.status == TaskStatus.PENDING
        assert parent_after.block_kind is None

    def test_try_delegate_many_atomic_sets_active_fanout(self, db):
        """active_fanout_json is written in the same transaction as children
        and parent park, so there is no crash gap."""
        parent = TaskRecord(id="TASK-FTP-AF", brief="af test")
        db.insert_task(parent)
        children = [
            TaskRecord(id="TASK-FTC-AF1", brief="c1", parent_task_id="TASK-FTP-AF", assigned_agent="dev", task_type="subtask"),
            TaskRecord(id="TASK-FTC-AF2", brief="c2", parent_task_id="TASK-FTP-AF", assigned_agent="qa", task_type="subtask"),
        ]
        ok = db.try_delegate_many(
            "TASK-FTP-AF", children, parent_note="fanout",
            active_fanout_json='{"children_ids":["TASK-FTC-AF1","TASK-FTC-AF2"],"width":2}',
        )
        assert ok is True
        p = db.get_task("TASK-FTP-AF")
        assert p.active_fanout is not None
        assert '"width":2' in p.active_fanout


    def test_active_fanout_migration_compat_v0_upgrade(self, tmp_path):
        """v0 enrollment upgrade: a pre-existing DB (no active_fanout column)
        tolerates the additive migration and existing rows get NULL default."""
        import sqlite3
        from runtime.infrastructure.database import Database

        db_path = tmp_path / "v0_upgrade.db"
        conn = sqlite3.connect(db_path)
        # Pre-existing schema — same as what v0 enrollment created before
        # the active_fanout migration shipped.
        conn.execute(
            """CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                assigned_agent TEXT,
                team TEXT NOT NULL DEFAULT 'engineering',
                brief TEXT NOT NULL,
                revision_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                parent_task_id TEXT,
                final_output_dir TEXT,
                block_kind TEXT,
                blocked_on_job_ids TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO tasks (id, status, team, brief, created_at, updated_at) "
            "VALUES ('V0-T1', 'pending', 'engineering', 'v0 legacy', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        conn.close()

        db = Database(db_path)
        cols = db._conn.execute("PRAGMA table_info(tasks)").fetchall()
        col_names = {row["name"] for row in cols}
        assert "active_fanout" in col_names  # migration added it
        t = db.get_task("V0-T1")
        assert t.active_fanout is None  # existing row gets NULL default
        db.close()

    def test_active_fanout_migration_compat_v1_flat_single_org(self, tmp_path):
        """v1 flat single-org upgrade: a legacy single-org DB (no active_fanout)
        tolerates the additive migration."""
        import sqlite3
        from runtime.infrastructure.database import Database

        db_path = tmp_path / "v1_upgrade.db"
        conn = sqlite3.connect(db_path)
        # v1-style flat single-org schema (before fan-out migration).
        conn.execute(
            """CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                assigned_agent TEXT,
                team TEXT NOT NULL DEFAULT 'engineering',
                brief TEXT NOT NULL,
                revision_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                parent_task_id TEXT,
                final_output_dir TEXT,
                note TEXT,
                block_kind TEXT,
                blocked_on_job_ids TEXT,
                task_type TEXT NOT NULL DEFAULT 'task'
            )"""
        )
        conn.execute(
            "INSERT INTO tasks (id, status, team, brief, created_at, updated_at) "
            "VALUES ('V1-T1', 'pending', 'engineering', 'v1 legacy', '2026-01-01', '2026-01-01')"
        )
        conn.commit()
        conn.close()

        db = Database(db_path)
        cols = db._conn.execute("PRAGMA table_info(tasks)").fetchall()
        col_names = {row["name"] for row in cols}
        assert "active_fanout" in col_names  # migration added it
        t = db.get_task("V1-T1")
        assert t.active_fanout is None  # existing row gets NULL default
        db.close()


# --- Join context verdict/confidence tests ---


class TestJoinContextVerdict:
    """Tests that collect_child_join_info properly propagates persisted
    verdict and confidence from child completion reports."""

    def test_default_verdict_and_confidence_without_reports(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        t = TaskRecord(
            id="T-001", brief="x", status=TaskStatus.COMPLETED,
            assigned_agent="dev", note="did the work",
            created_at=now, updated_at=now,
        )
        infos = collect_child_join_info([t])
        assert infos[0].verdict is None
        assert infos[0].confidence == 80

    def test_verdict_and_confidence_from_report(self):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        t = TaskRecord(
            id="T-002", brief="y", status=TaskStatus.COMPLETED,
            assigned_agent="qa", note="verified",
            created_at=now, updated_at=now,
        )
        reports = {"T-002": {"verdict": "PASS", "confidence_score": 92}}
        infos = collect_child_join_info([t], child_reports=reports)
        assert infos[0].verdict == "PASS"
        assert infos[0].confidence == 92

    def test_mixed_report_availability(self):
        """One child has a report, one does not — each gets correct values."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        t1 = TaskRecord(
            id="T-WITH", brief="with", status=TaskStatus.COMPLETED,
            assigned_agent="dev", created_at=now, updated_at=now,
        )
        t2 = TaskRecord(
            id="T-WITHOUT", brief="without", status=TaskStatus.FAILED,
            assigned_agent="qa", created_at=now, updated_at=now,
        )
        reports = {"T-WITH": {"verdict": "APPROVE", "confidence_score": 88}}
        infos = collect_child_join_info([t1, t2], child_reports=reports)
        assert infos[0].verdict == "APPROVE"
        assert infos[0].confidence == 88
        assert infos[1].verdict is None
        assert infos[1].confidence == 80


# --- Orchestration tests (run_step fanout decision branch) ---


class _SlugQueue:
    """Minimal in-memory queue for unit-testing run_step."""
    def __init__(self):
        from collections import deque
        self._items = deque()

    def put_nowait(self, slug, task_id):
        self._items.append((slug, task_id))

    def get_nowait(self):
        return self._items.popleft()


def _make_result(success=True, error=None, returncode=0):
    from unittest.mock import MagicMock
    r = MagicMock()
    r.success = success
    r.error = error
    r.returncode = returncode
    r.rate_limited = False
    r.token_usage = None
    r.session_id = "sess-1"
    r.agent_session_id = None
    r.stdout_tail = ""
    r.stderr_tail = ""
    return r


def _make_report(output_summary="done", verdict=None, decision=None):
    from runtime.models import CompletionReport
    return CompletionReport(
        task_id="T-TEST",
        agent="test_agent",
        status="completed",
        confidence=80,
        output_summary=output_summary,
        verdict=verdict,
    )


class TestFanoutRunStep:
    def test_fanout_spawns_all_children_and_parks_parent(self, runtime, db, monkeypatch):
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry


        # Create workspaces for child agents
        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
        (runtime.workspaces_dir / "qa_engineer").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-FANOUT-1", brief="fanout root",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        q = _SlugQueue()
        orch._queue = q

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": [
                        {"agent": "dev_agent", "prompt": "task 1"},
                        {"agent": "qa_engineer", "prompt": "task 2"},
                    ],
                    "width_cap_ack": 2,
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-FANOUT-1")

        # Parent is now IN_PROGRESS(DELEGATED)
        parent = db.get_task("T-FANOUT-1")
        assert parent.status == TaskStatus.IN_PROGRESS
        assert parent.block_kind == BlockKind.DELEGATED
        assert parent.active_fanout is not None

        # Two children spawned and enqueued
        children = db.get_children("T-FANOUT-1")
        assert len(children) == 2
        for cid in children:
            c = db.get_task(cid)
            assert c is not None
            assert c.status == TaskStatus.PENDING
            assert c.task_type == "subtask"
            assert c.parent_task_id == "T-FANOUT-1"

        # Both children enqueued
        enqueued = [q.get_nowait() for _ in range(2)]
        assert len(enqueued) == 2
        assert all(slug == "test" for slug, _ in enqueued)

    def test_fanout_rejects_invalid_children_validation(self, runtime, db, monkeypatch):
        """Structural validation errors (e.g. single child) cause task failure."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-FANOUT-BAD", brief="fails validation",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        q = _SlugQueue()
        orch._queue = q

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": [
                        {"agent": "dev_agent", "prompt": "single child"},
                    ],
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-FANOUT-BAD")

        parent = db.get_task("T-FANOUT-BAD")
        assert parent.status == TaskStatus.FAILED
        assert "single child" in (parent.note or "").lower()

    def test_fanout_rejects_over_cap(self, runtime, db, monkeypatch):
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        for i in range(9):
            (runtime.workspaces_dir / f"agent{i}").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-FANOUT-CAP", brief="over cap",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        orch._queue = _SlugQueue()

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            children = [{"agent": f"agent{i}", "prompt": f"task {i}"} for i in range(9)]
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": children,
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-FANOUT-CAP")

        parent = db.get_task("T-FANOUT-CAP")
        assert parent.status == TaskStatus.FAILED
        assert "exceeds max" in (parent.note or "").lower()

    def test_fanout_rejects_off_team_agents(self, runtime, db, monkeypatch):
        """Manager cannot fan-out to agents on other teams."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
        # 'other_agent' is NOT in the engineering team but has a workspace
        (runtime.workspaces_dir / "other_agent").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-FANOUT-SCOPE", brief="off team",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        orch._queue = _SlugQueue()

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": [
                        {"agent": "dev_agent", "prompt": "ok"},
                        {"agent": "other_agent", "prompt": "off-team"},
                    ],
                    "width_cap_ack": 2,
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-FANOUT-SCOPE")

        parent = db.get_task("T-FANOUT-SCOPE")
        # Off-team rejection → feedback step (task goes back to pending)
        assert parent.status == TaskStatus.PENDING

    def test_fanout_rejects_invalid_then_leg_workspace(self, runtime, db, monkeypatch):
        """Fan-out with a then leg targeting a non-existent workspace still fails."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
        (runtime.workspaces_dir / "qa_engineer").mkdir(parents=True)
        # 'qa' workspace does NOT exist

        db.insert_task(TaskRecord(
            id="T-FANOUT-THEN", brief="invalid then leg",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        orch._queue = _SlugQueue()

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": [
                        {"agent": "dev_agent", "prompt": "t1", "then": [{"agent": "qa", "prompt": "review"}]},
                        {"agent": "qa_engineer", "prompt": "t2"},
                    ],
                    "width_cap_ack": 2,
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-FANOUT-THEN")

        parent = db.get_task("T-FANOUT-THEN")
        assert parent.status == TaskStatus.FAILED
        # Should fail because then leg agent 'qa' has no workspace
        assert "qa" in (parent.note or "").lower()

    def test_fanout_clears_active_fanout_on_failure(self, runtime, db, monkeypatch):
        """When a fan-out decision fails validation, active_fanout stays None."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-FANOUT-CLEAR", brief="clear test",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        orch._queue = _SlugQueue()

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": [],  # empty → validation fail
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-FANOUT-CLEAR")

        parent = db.get_task("T-FANOUT-CLEAR")
        assert parent.status == TaskStatus.FAILED
        assert parent.active_fanout is None  # never set

    def test_fanout_active_fanout_set_after_spawn(self, runtime, db, monkeypatch):
        """active_fanout is set with correct FanoutState after spawn."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry
        from runtime.orchestrator.fanout import FanoutState

        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
        (runtime.workspaces_dir / "qa_engineer").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-FANOUT-STATE", brief="state test",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        orch._queue = _SlugQueue()

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": [
                        {"agent": "dev_agent", "prompt": "t1"},
                        {"agent": "qa_engineer", "prompt": "t2"},
                    ],
                    "width_cap_ack": 2,
                    "join_summary": "Review both",
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-FANOUT-STATE")

        parent = db.get_task("T-FANOUT-STATE")
        assert parent.active_fanout is not None
        fs = FanoutState.deserialize(parent.active_fanout)
        assert fs.width == 2
        assert fs.manager_agent == "engineering_head"
        assert fs.join_summary == "Review both"
        assert len(fs.children_ids) == 2

    def test_fanout_over_cap_still_hard_fails(self, runtime, db, monkeypatch):
        """Width > MAX_FANOUT_WIDTH still hard-fails (parse rejection before review gate)."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        for i in range(10):
            (runtime.workspaces_dir / f"agent{i}").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-FANOUT-OVERCAP", brief="over cap",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        orch._queue = _SlugQueue()

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            children = [{"agent": f"agent{i}", "prompt": f"task {i}"} for i in range(9)]
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": children,
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-FANOUT-OVERCAP")

        parent = db.get_task("T-FANOUT-OVERCAP")
        # Over-cap still hard-fails (parse rejection).
        assert parent.status == TaskStatus.FAILED
        assert "exceeds max" in (parent.note or "").lower()

    def test_fanout_failed_child_join_context_preserved(self, runtime, db, monkeypatch):
        """Mixed completed + failed fan-out children within bound: parent
        re-enqueues once, active_fanout survives for join injection, fanout_join
        audit occurs, then active_fanout clears."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry
        from runtime.orchestrator.fanout import FanoutState

        (runtime.workspaces_dir / "eng_mgr").mkdir(parents=True)
        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
        (runtime.workspaces_dir / "qa_engineer").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-FANOUT-MIXED", brief="mixed test",
            assigned_agent="eng_mgr",
        ))

        # Simulate: parent already spawned 2 children. Child 1 completed,
        # Child 2 failed. Parent is in_progress(delegated) with active_fanout.
        fanout = FanoutState(
            children_ids=["T-FANOUT-MIXED-C1", "T-FANOUT-MIXED-C2"],
            children_details=[
                {"agent": "dev_agent", "prompt": "task 1"},
                {"agent": "qa_engineer", "prompt": "task 2"},
            ],
            width=2, manager_agent="eng_mgr",
            status="spawned",
        )
        db.update_task(
            "T-FANOUT-MIXED",
            status=TaskStatus.IN_PROGRESS,
            block_kind=BlockKind.DELEGATED,
            note="fan-out in flight",
        )
        db.update_task_active_fanout("T-FANOUT-MIXED", fanout.serialize())

        # Child 1: completed.
        db.insert_task(TaskRecord(
            id="T-FANOUT-MIXED-C1", brief="task 1",
            assigned_agent="dev_agent", parent_task_id="T-FANOUT-MIXED",
            status=TaskStatus.COMPLETED, task_type="subtask",
        ))
        # Child 2: failed (within bound).
        db.insert_task(TaskRecord(
            id="T-FANOUT-MIXED-C2", brief="task 2",
            assigned_agent="qa_engineer", parent_task_id="T-FANOUT-MIXED",
            status=TaskStatus.FAILED, task_type="subtask",
            note="test failure",
        ))
        # Add completion report for child 1.
        db.insert_task_result(
            task_id="T-FANOUT-MIXED-C1", agent="dev_agent", session_id="",
            status="completed", confidence_score=90, output_summary="Done",
            risks_flagged=[],
        )

        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        orch._queue = _SlugQueue()

        # Run step — should detect all children terminal, within bound,
        # inject join context (not clear active_fanout prematurely).
        orch.run_step("T-FANOUT-MIXED")

        parent = db.get_task("T-FANOUT-MIXED")
        # Parent should have been re-enqueued (still in_progress(delegated) for
        # fresh manager decision). active_fanout should be cleared by the
        # CAS-winner after join injection.
        assert parent.active_fanout is None
        # The fanout_join audit row should exist.
        logs = db.get_audit_logs("T-FANOUT-MIXED")
        join_actions = [e for e in logs if e["action"] == "fanout_join"]
        assert len(join_actions) == 1
        # Join context should include both children.
        payload = join_actions[0].get("payload", {})
        ctx = payload.get("context_markdown", "")
        assert "T-FANOUT-MIXED-C1" in ctx
        assert "T-FANOUT-MIXED-C2" in ctx
        assert "completed" in ctx
        assert "failed" in ctx
        assert "test failure" in ctx


# --- Phase 2 pipeline tests (§3.6 TDD list) ---


class TestFanoutPipeline:
    """§3.6 TDD tests for Phase 2 pipeline (carrier child with inline chain).
    These must FAIL pre-impl (capture the red output).
    """

    # ── §3.6 test 8: parse-accept ──

    def test_parse_accepts_per_child_then_and_expect_verdict(self):
        """then/expect_verdict on a fan-out child now passes validation
        instead of being rejected (inverse of the Phase 1 parse-reject)."""
        decision = NextStep(
            action="fanout",
            children=[
                FanoutChild(
                    agent="dev_agent", prompt="task 1",
                    then=[ChainLeg(agent="senior_dev", prompt="review", expect_verdict="APPROVE")],
                ),
                FanoutChild(
                    agent="qa_engineer", prompt="task 2",
                    expect_verdict="PASS",
                ),
            ],
            width_cap_ack=2,
        )
        assert validate_fanout_decision(decision) is None

    def test_parse_rejects_then_leg_missing_agent(self):
        """A then leg with a missing agent still fails validation."""
        decision = NextStep(
            action="fanout",
            children=[
                FanoutChild(
                    agent="dev_agent", prompt="task 1",
                    then=[ChainLeg(agent="", prompt="review")],
                ),
                FanoutChild(agent="qa_engineer", prompt="task 2"),
            ],
            width_cap_ack=2,
        )
        err = validate_fanout_decision(decision)
        assert err is not None
        assert "agent" in err.lower()

    def test_parse_rejects_then_leg_missing_prompt(self):
        """A then leg with a missing prompt still fails validation."""
        decision = NextStep(
            action="fanout",
            children=[
                FanoutChild(
                    agent="dev_agent", prompt="task 1",
                    then=[ChainLeg(agent="senior_dev", prompt="")],
                ),
                FanoutChild(agent="qa_engineer", prompt="task 2"),
            ],
            width_cap_ack=2,
        )
        err = validate_fanout_decision(decision)
        assert err is not None
        assert "prompt" in err.lower()

    # ── §3.6 test 6: width + review gate unchanged ──

    def test_width_cap_ack_mismatch_still_rejects(self):
        """width_cap_ack mismatch still parse-rejects."""
        decision = NextStep(
            action="fanout",
            children=[
                FanoutChild(agent="a", prompt="1"),
                FanoutChild(agent="b", prompt="2"),
            ],
            width_cap_ack=99,
        )
        err = validate_fanout_decision(decision)
        assert err is not None
        assert "width_cap_ack" in err.lower()

    # ── §3.6 test 5: plain-child regression ──

    def test_empty_then_child_behaves_like_phase1(self, runtime, db, monkeypatch):
        """A fan-out child with empty `then` is dispatched as a plain PENDING
        subtask, byte-identical to Phase 1 — no active_chain, no carrier behavior."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
        (runtime.workspaces_dir / "qa_engineer").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-PLAIN1", brief="plain child regression",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        q = _SlugQueue()
        orch._queue = q

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": [
                        {"agent": "dev_agent", "prompt": "plain child"},
                        {"agent": "qa_engineer", "prompt": "also plain"},
                    ],
                    "width_cap_ack": 2,
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-PLAIN1")

        # Parent parked with active_fanout
        parent = db.get_task("T-PLAIN1")
        assert parent.active_fanout is not None

        children = db.get_children("T-PLAIN1")
        assert len(children) == 2
        for cid in children:
            c = db.get_task(cid)
            assert c is not None
            # Plain children are PENDING — no active_chain set
            assert c.status == TaskStatus.PENDING
            assert c.active_chain is None
            assert c.task_type == "subtask"
            assert c.parent_task_id == "T-PLAIN1"

    # ── §3.6 test 1: no-clobber ──

    def test_pipeline_no_clobber_two_column_two_row(self, runtime, db, monkeypatch):
        """Spawn a 2-wide pipeline; assert P.active_fanout on P's row,
        Cᵢ.active_chain on each Cᵢ row, neither ever written to the other row."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
        (runtime.workspaces_dir / "qa_engineer").mkdir(parents=True)
        (runtime.workspaces_dir / "senior_dev").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-NOCLOBBER", brief="pipeline no-clobber",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        q = _SlugQueue()
        orch._queue = q

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": [
                        {
                            "agent": "dev_agent", "prompt": "pipeline child 1",
                            "then": [{"agent": "senior_dev", "prompt": "review", "expect_verdict": "APPROVE"}],
                        },
                        {
                            "agent": "qa_engineer", "prompt": "pipeline child 2",
                            "expect_verdict": "PASS",
                        },
                    ],
                    "width_cap_ack": 2,
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-NOCLOBBER")

        # P has active_fanout but NOT active_chain
        parent = db.get_task("T-NOCLOBBER")
        assert parent.active_fanout is not None, "P must have active_fanout"
        assert parent.active_chain is None, "P must NOT have active_chain"

        children = db.get_children("T-NOCLOBBER")
        assert len(children) == 2
        for cid in children:
            c = db.get_task(cid)
            assert c is not None
            # Each Cᵢ has active_chain but NOT active_fanout
            assert c.active_chain is not None, f"{cid} must have active_chain"
            assert c.active_fanout is None, f"{cid} must NOT have active_fanout"

    # ── §3.6 test 2: carrier defers terminal ──

    def test_carrier_defers_terminal_until_chain_completes(self, runtime, db, monkeypatch):
        """Cᵢ does NOT count toward P's barrier until its final leg
        matches expect_verdict.  When the chain completes, the carrier
        auto-completes DIRECTLY — NO orch._run_agent call, NO manager-wake."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry
        from runtime.orchestrator.run_step import (
            _spawn_fanout_children, _enqueue_parent_if_waiting,
        )

        for a in ("dev_agent", "qa_engineer", "senior_dev"):
            (runtime.workspaces_dir / a).mkdir(parents=True)

        # Insert parent task
        db.insert_task(TaskRecord(
            id="T-PIPE-DEFER", brief="carrier deferral",
            assigned_agent="engineering_head",
            task_type="task",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        q = _SlugQueue()
        orch._queue = q
        # Spy on _run_agent: the carrier must NEVER call _run_agent.
        _run_agent_calls = []
        monkeypatch.setattr(orch, "_run_agent", lambda tid, ag, pr, **kw: (
            _run_agent_calls.append(tid), _make_result(), _make_report("done"),
        )[-2:])

        # Spawn fan-out: 1 pipeline child + 1 plain child
        children_payload = [
            {
                "agent": "dev_agent", "prompt": "build",
                "expect_verdict": "APPROVE",
                "then": [{"agent": "senior_dev", "prompt": "review", "expect_verdict": "APPROVE"}],
            },
            {"agent": "qa_engineer", "prompt": "test"},
        ]
        _spawn_fanout_children(
            orch, db.get_task("T-PIPE-DEFER"), "T-PIPE-DEFER", 1,
            children=children_payload, width=2,
            manager_agent="engineering_head", step_audit_id=1,
        )
        parent = db.get_task("T-PIPE-DEFER")
        assert parent.status == TaskStatus.IN_PROGRESS
        assert parent.block_kind == BlockKind.DELEGATED

        children = db.get_children("T-PIPE-DEFER")
        assert len(children) == 2

        # Identify carrier
        carrier_id = None
        plain_id = None
        for cid in children:
            c = db.get_task(cid)
            if c.active_chain is not None:
                carrier_id = cid
            else:
                plain_id = cid
        assert carrier_id is not None
        assert plain_id is not None

        carrier = db.get_task(carrier_id)
        assert carrier.status == TaskStatus.IN_PROGRESS
        assert carrier.block_kind == BlockKind.DELEGATED

        # Find first leg
        carrier_children = db.get_children(carrier_id)
        assert len(carrier_children) == 1
        first_leg_id = carrier_children[0]

        # Complete first leg with matching verdict
        db.update_task(first_leg_id, status=TaskStatus.COMPLETED, block_kind=None)
        db.insert_task_result(
            task_id=first_leg_id, agent="dev_agent", session_id="s",
            status="completed", confidence_score=80,
            output_summary="built", verdict="APPROVE",
        )
        # Trigger parent-wake on carrier (chain should advance)
        _enqueue_parent_if_waiting(orch, first_leg_id)

        # Chain advanced: second leg spawned, carrier STILL delegated
        carrier = db.get_task(carrier_id)
        assert carrier.status == TaskStatus.IN_PROGRESS, (
            "carrier should DEFER terminal until chain completes"
        )
        carrier_children = db.get_children(carrier_id)
        assert len(carrier_children) == 2, "second leg should have been spawned"

        # Complete plain fan-out child
        db.update_task(plain_id, status=TaskStatus.COMPLETED, block_kind=None)
        db.insert_task_result(
            task_id=plain_id, agent="qa_engineer", session_id="s",
            status="completed", confidence_score=80,
            output_summary="tested",
        )
        _enqueue_parent_if_waiting(orch, plain_id)
        # Parent still parked — carrier hasn't completed its chain yet
        parent = db.get_task("T-PIPE-DEFER")
        assert parent.block_kind == BlockKind.DELEGATED, (
            "P should still be parked while carrier chain is incomplete"
        )

        # Complete second leg (senior_dev) with expected verdict
        second_leg_id = [cid for cid in carrier_children if cid != first_leg_id][0]
        db.update_task(second_leg_id, status=TaskStatus.COMPLETED, block_kind=None)
        db.insert_task_result(
            task_id=second_leg_id, agent="senior_dev", session_id="s",
            status="completed", confidence_score=80,
            output_summary="reviewed", verdict="APPROVE",
        )
        # Trigger carrier wake → chain complete → carrier auto-completes.
        # The carrier must auto-complete DIRECTLY, with NO _run_agent call.
        _enqueue_parent_if_waiting(orch, second_leg_id)

        # Carrier should now be terminal — auto-completed by the chain-complete handler.
        carrier = db.get_task(carrier_id)
        assert carrier.status == TaskStatus.COMPLETED, (
            f"carrier should auto-complete after chain completes, got {carrier.status}"
        )
        # Verify _run_agent was NEVER called for the carrier.
        assert carrier_id not in _run_agent_calls, (
            f"carrier {carrier_id} must NOT call _run_agent — it has no session of its own"
        )

    # ── §3.6 test 3: barrier fires once ──

    def test_barrier_fires_once_after_all_carriers_complete(self, runtime, db, monkeypatch):
        """P wakes exactly once, only after ALL carriers' chains complete.
        Carriers auto-complete DIRECTLY — NO _run_agent call for any carrier."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry
        from runtime.orchestrator.run_step import (
            _spawn_fanout_children, _enqueue_parent_if_waiting,
        )

        for a in ("agent0", "agent1", "agent2", "agent3"):
            (runtime.workspaces_dir / a).mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-BARRIER1", brief="barrier once",
            assigned_agent="engineering_head",
            task_type="task",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )

        # Count how many times P is enqueued
        enqueued_parent_count = [0]

        class CountQueue:
            def __init__(self):
                from collections import deque
                self._items = deque()
            def put_nowait(self, slug, task_id):
                if task_id == "T-BARRIER1":
                    enqueued_parent_count[0] += 1
                self._items.append((slug, task_id))
            def get_nowait(self):
                return self._items.popleft()
            def enqueue(self, slug, task_id):
                self.put_nowait(slug, task_id)
            def __len__(self):
                return len(self._items)
            def __bool__(self):
                return bool(self._items)
        orch._queue = CountQueue()
        # Spy on _run_agent: carriers must NEVER call _run_agent.
        _run_agent_calls = []
        monkeypatch.setattr(orch, "_run_agent", lambda tid, ag, pr, **kw: (
            _run_agent_calls.append(tid), _make_result(), _make_report("done"),
        )[-2:])

        # Spawn 2 pipeline carriers
        children_payload = [
            {
                "agent": "agent0", "prompt": "c1",
                "then": [{"agent": "agent2", "prompt": "review1", "expect_verdict": "APPROVE"}],
            },
            {
                "agent": "agent1", "prompt": "c2",
                "then": [{"agent": "agent3", "prompt": "review2", "expect_verdict": "PASS"}],
            },
        ]
        _spawn_fanout_children(
            orch, db.get_task("T-BARRIER1"), "T-BARRIER1", 1,
            children=children_payload, width=2,
            manager_agent="engineering_head", step_audit_id=1,
        )
        parent = db.get_task("T-BARRIER1")
        assert parent.active_fanout is not None

        children = db.get_children("T-BARRIER1")
        assert len(children) == 2

        # For each carrier, complete its chain fully
        for cid in children:
            carrier = db.get_task(cid)
            assert carrier.active_chain is not None

            # Complete first leg
            carrier_children = db.get_children(cid)
            assert len(carrier_children) == 1
            first_leg_id = carrier_children[0]
            db.update_task(first_leg_id, status=TaskStatus.COMPLETED, block_kind=None)
            db.insert_task_result(
                task_id=first_leg_id, agent=carrier.assigned_agent, session_id="s",
                status="completed", confidence_score=80,
                output_summary=f"first leg of {cid}",
            )
            _enqueue_parent_if_waiting(orch, first_leg_id)

            # Now complete second leg (spawned by chain advance)
            carrier_children = db.get_children(cid)
            second_legs = [l for l in carrier_children if l != first_leg_id]
            for sl_id in second_legs:
                sl = db.get_task(sl_id)
                if sl.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                    db.update_task(sl_id, status=TaskStatus.COMPLETED, block_kind=None)
                    verdict = "APPROVE" if cid == children[0] else "PASS"
                    db.insert_task_result(
                        task_id=sl_id, agent=sl.assigned_agent, session_id="s",
                        status="completed", confidence_score=80,
                        output_summary=f"review of {cid}", verdict=verdict,
                    )
                    _enqueue_parent_if_waiting(orch, sl_id)

        # Carriers should auto-complete DIRECTLY after the last leg's
        # _enqueue_parent_if_waiting — NO separate orch.run_step needed.
        for cid in children:
            carrier = db.get_task(cid)
            assert carrier.status == TaskStatus.COMPLETED, (
                f"carrier {cid} should auto-complete after chain completes, got {carrier.status}"
            )
        # Verify _run_agent was NEVER called for any carrier.
        for cid in children:
            assert cid not in _run_agent_calls, (
                f"carrier {cid} must NOT call _run_agent — it has no session of its own"
            )

        # P should have been enqueued exactly once
        assert enqueued_parent_count[0] == 1, (
            f"P should wake exactly once, got {enqueued_parent_count[0]}"
        )

    # ── §3.6 test 4: fail-closed on leg mismatch ──

    def test_fail_closed_on_leg_verdict_mismatch(self, runtime, db, monkeypatch):
        """A leg verdict mismatch terminates the carrier failed and cascades to P."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry
        from runtime.orchestrator.run_step import (
            _spawn_fanout_children, _enqueue_parent_if_waiting,
        )

        for a in ("dev_agent", "qa_engineer"):
            (runtime.workspaces_dir / a).mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-FAILCLSD", brief="fail-closed test",
            assigned_agent="engineering_head",
            task_type="task",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        q = _SlugQueue()
        orch._queue = q
        # Mock _run_agent for safety.
        monkeypatch.setattr(orch, "_run_agent", lambda tid, ag, pr, **kw: (_make_result(), _make_report("done")))

        # Spawn: 1 carrier (expect_verdict=APPROVE) + 1 plain child
        children_payload = [
            {
                "agent": "dev_agent", "prompt": "build",
                "expect_verdict": "APPROVE",
            },
            {"agent": "qa_engineer", "prompt": "test"},
        ]
        _spawn_fanout_children(
            orch, db.get_task("T-FAILCLSD"), "T-FAILCLSD", 1,
            children=children_payload, width=2,
            manager_agent="engineering_head", step_audit_id=1,
        )

        children = db.get_children("T-FAILCLSD")
        assert len(children) == 2

        # Find the carrier
        carrier_id = None
        for cid in children:
            c = db.get_task(cid)
            if c.active_chain is not None:
                carrier_id = cid
                break
        assert carrier_id is not None

        # Complete first leg with WRONG verdict
        carrier_children = db.get_children(carrier_id)
        assert len(carrier_children) == 1
        first_leg_id = carrier_children[0]
        db.update_task(first_leg_id, status=TaskStatus.COMPLETED, block_kind=None)
        db.insert_task_result(
            task_id=first_leg_id, agent="dev_agent", session_id="s",
            status="completed", confidence_score=80,
            output_summary="built", verdict="REVISE",  # wrong verdict!
        )
        # Trigger parent-wake on carrier → verdict mismatch → carrier fails
        _enqueue_parent_if_waiting(orch, first_leg_id)

        # Carrier should be FAILED (verdict mismatch)
        carrier = db.get_task(carrier_id)
        assert carrier.status == TaskStatus.FAILED, (
            f"carrier should FAIL on verdict mismatch, got {carrier.status}"
        )

    # ── §3.6 test 7: restart recovery ──

    def test_all_carriers_terminal_sweep_reenqueues_parent(self, runtime, db, monkeypatch):
        """Kill mid-flight with all carriers terminal → _sweep_on_startup
        re-enqueues P (reuses existing DELEGATED sweep)."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry
        from runtime.orchestrator.fanout import FanoutState

        for a in ("agent0", "agent1", "agent2", "agent3"):
            (runtime.workspaces_dir / a).mkdir(parents=True)

        # Simulate: parent is parked BLOCKED(DELEGATED) with active_fanout.
        # All 2 carriers are already terminal.
        fanout = FanoutState(
            children_ids=["T-SWEEP-C1", "T-SWEEP-C2"],
            children_details=[
                {"agent": "agent0", "prompt": "c1"},
                {"agent": "agent1", "prompt": "c2"},
            ],
            width=2, manager_agent="engineering_head",
            status="spawned",
        )
        db.insert_task(TaskRecord(
            id="T-SWEEP-P", brief="restart recovery",
            assigned_agent="engineering_head",
            status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
            active_fanout=fanout.serialize(),
        ))
        # All carriers terminal
        for cid in ("T-SWEEP-C1", "T-SWEEP-C2"):
            db.insert_task(TaskRecord(
                id=cid, brief="carrier",
                assigned_agent="agent0",
                parent_task_id="T-SWEEP-P",
                status=TaskStatus.COMPLETED, task_type="subtask",
            ))

        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )

        # Simulate sweep: _sweep_on_startup detects all-terminal BLOCKED(DELEGATED)
        from runtime.daemon.__main__ import _sweep_on_startup
        enqueued = []

        class SweepQueue:
            def put_nowait(self, slug, tid):
                enqueued.append((slug, tid))
            def enqueue(self, slug, tid):
                enqueued.append((slug, tid))
        orch._queue = SweepQueue()

        _sweep_on_startup(db, orch._queue, slug="test", orchestrator=orch)

        # P should have been re-enqueued
        assert ("test", "T-SWEEP-P") in enqueued, (
            f"sweep should re-enqueue P, enqueued: {enqueued}"
        )


class TestMutatingFanout:
    """THR-056 option 3: mutating fan-out — child fans-out to managers whose
    delegate-chain decisions must be parsed and spawn implementation children."""

    # ── Test 1: task_type assignment ──

    def test_manager_child_is_task_type_task(self, runtime, db, monkeypatch):
        """A fan-out child targeted at a team manager (engineering_head) gets
        task_type='task' so its delegate decisions are parsed."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        (runtime.workspaces_dir / "engineering_head").mkdir(parents=True)
        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
        (runtime.workspaces_dir / "qa_engineer").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-MUT-TYPE", brief="mutating fanout type test",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        q = _SlugQueue()
        orch._queue = q

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": [
                        {"agent": "engineering_head", "prompt": "manager child"},
                        {"agent": "dev_agent", "prompt": "worker child"},
                    ],
                    "width_cap_ack": 2,
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-MUT-TYPE")

        children = db.get_children("T-MUT-TYPE")
        assert len(children) == 2

        # Manager child → task_type="task"
        mgr_child = None
        worker_child = None
        for cid in children:
            c = db.get_task(cid)
            assert c is not None
            if c.assigned_agent == "engineering_head":
                mgr_child = c
            elif c.assigned_agent == "dev_agent":
                worker_child = c

        assert mgr_child is not None, "manager child not found"
        assert worker_child is not None, "worker child not found"
        assert mgr_child.task_type == "task", (
            f"manager child should be task_type='task', got {mgr_child.task_type!r}"
        )
        assert worker_child.task_type == "subtask", (
            f"worker child should be task_type='subtask', got {worker_child.task_type!r}"
        )

    def test_non_manager_child_still_subtask(self, runtime, db, monkeypatch):
        """A fan-out child targeted at a regular worker still gets
        task_type='subtask' (read-only, Phase 1 behavior preserved)."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        (runtime.workspaces_dir / "engineering_head").mkdir(parents=True)
        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
        (runtime.workspaces_dir / "qa_engineer").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-READONLY", brief="readonly fanout",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        q = _SlugQueue()
        orch._queue = q

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": [
                        {"agent": "dev_agent", "prompt": "worker child 1"},
                        {"agent": "qa_engineer", "prompt": "worker child 2"},
                    ],
                    "width_cap_ack": 2,
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-READONLY")

        children = db.get_children("T-READONLY")
        assert len(children) == 2
        worker = db.get_task(children[0])
        assert worker.task_type == "subtask", (
            f"worker child should be task_type='subtask', got {worker.task_type!r}"
        )
        assert worker.assigned_agent == "dev_agent"

    # ── Test 2: mutating child can spawn delegate chain ──

    def test_manager_child_delegate_decision_spawns_children(self, runtime, db, monkeypatch):
        """A mutating fan-out child (task_type='task') that returns a delegate
        decision with an inline chain spawns implementation children."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        (runtime.workspaces_dir / "engineering_head").mkdir(parents=True)
        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
        (runtime.workspaces_dir / "code_reviewer").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-MUT-SPAWN", brief="mutating spawn test",
            assigned_agent="engineering_head",
        ))
        teams = TeamsRegistry.load(runtime.root)
        teams.add_worker("engineering", "code_reviewer")
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=teams,
        )
        q = _SlugQueue()
        orch._queue = q

        call_count = 0

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Fan-out: spawn a manager child (mutating) + a worker child
                return _make_result(), _make_report(
                    output_summary=json.dumps({
                        "action": "fanout",
                        "children": [
                            {"agent": "engineering_head", "prompt": "mutating child"},
                            {"agent": "dev_agent", "prompt": "worker child"},
                        ],
                        "width_cap_ack": 2,
                    }),
                )
            elif call_count == 2:
                # The mutating child's session: return a delegate with a chain
                return _make_result(), _make_report(
                    output_summary=json.dumps({
                        "action": "delegate",
                        "agent": "dev_agent",
                        "prompt": "implement feature",
                        "then": [
                            {"agent": "code_reviewer", "prompt": "review",
                             "expect_verdict": "APPROVE"},
                        ],
                    }),
                )
            return _make_result(), _make_report(
                output_summary=json.dumps({"action": "done"}),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        # Step 1: parent runs fan-out
        orch.run_step("T-MUT-SPAWN")
        parent = db.get_task("T-MUT-SPAWN")
        assert parent.active_fanout is not None, "parent should have active_fanout"
        assert parent.block_kind == BlockKind.DELEGATED

        children = db.get_children("T-MUT-SPAWN")
        assert len(children) == 2
        mutating_child_id = None
        for cid in children:
            c = db.get_task(cid)
            if c.assigned_agent == "engineering_head":
                mutating_child_id = cid
        assert mutating_child_id is not None
        mutating_child = db.get_task(mutating_child_id)
        assert mutating_child.task_type == "task", (
            f"mutating child should be task_type='task', got {mutating_child.task_type!r}"
        )
        assert mutating_child.status == TaskStatus.PENDING

        # Step 2: run the mutating child — it returns a delegate decision
        orch.run_step(mutating_child_id)
        mutating_child = db.get_task(mutating_child_id)
        assert mutating_child.status == TaskStatus.IN_PROGRESS
        assert mutating_child.block_kind == BlockKind.DELEGATED
        assert mutating_child.active_chain is not None, (
            "mutating child should have active_chain after delegate+then"
        )

        # The mutating child should have spawned a subtask (first leg of chain)
        impl_children = db.get_children(mutating_child_id)
        assert len(impl_children) == 1, (
            f"mutating child should have 1 implementation child, got {len(impl_children)}"
        )
        first_leg_id = impl_children[0]
        impl_child = db.get_task(first_leg_id)
        assert impl_child.assigned_agent == "dev_agent"

        # Step 3: simulate dev_agent completing (with callback that writes task_result)
        from runtime.orchestrator.run_step import _enqueue_parent_if_waiting
        db.update_task(first_leg_id, status=TaskStatus.COMPLETED, block_kind=None)
        db.insert_task_result(
            task_id=first_leg_id, agent="dev_agent", session_id="s1",
            status="completed", confidence_score=80,
            output_summary="implemented", verdict="APPROVE",
        )
        _enqueue_parent_if_waiting(orch, first_leg_id)

        # Chain should auto-advance to code_reviewer
        mutating_child = db.get_task(mutating_child_id)
        assert mutating_child.status == TaskStatus.IN_PROGRESS, (
            f"mutating child should stay IN_PROGRESS after chain advance, got {mutating_child.status}"
        )
        assert mutating_child.active_chain is not None, "chain should still be active after advance"
        impl_children_after = db.get_children(mutating_child_id)
        assert len(impl_children_after) == 2, (
            f"should have 2 children (dev_agent + code_reviewer), got {len(impl_children_after)}"
        )

        # Step 4: simulate code_reviewer completing with matching verdict
        second_leg_id = [cid for cid in impl_children_after if cid != first_leg_id][0]
        db.update_task(second_leg_id, status=TaskStatus.COMPLETED, block_kind=None)
        db.insert_task_result(
            task_id=second_leg_id, agent="code_reviewer", session_id="s2",
            status="completed", confidence_score=80,
            output_summary="reviewed", verdict="APPROVE",
        )
        _enqueue_parent_if_waiting(orch, second_leg_id)

        # Chain should complete → mutating child completes (via carrier-complete)
        mutating_child = db.get_task(mutating_child_id)
        assert mutating_child.status == TaskStatus.COMPLETED, (
            f"mutating child should be COMPLETED after chain finishes, got {mutating_child.status}"
        )

    # ── Test 3: fan-out parent waits for mutating child ──

    def test_fanout_parent_waits_for_mutating_child(self, runtime, db, monkeypatch):
        """The original fan-out parent does not join until the mutating child
        is terminal. A non-mutating worker child that completes first does NOT
        trigger the parent join."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        (runtime.workspaces_dir / "engineering_head").mkdir(parents=True)
        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
        (runtime.workspaces_dir / "qa_engineer").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-BARRIER", brief="barrier test",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        q = _SlugQueue()
        orch._queue = q

        call_count = 0

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Fan-out: one mutating (manager) + one worker child
                return _make_result(), _make_report(
                    output_summary=json.dumps({
                        "action": "fanout",
                        "children": [
                            {"agent": "engineering_head", "prompt": "mutating child"},
                            {"agent": "dev_agent", "prompt": "worker child"},
                        ],
                        "width_cap_ack": 2,
                    }),
                )
            elif call_count == 2:
                # Mutating child: returns done
                return _make_result(), _make_report(
                    output_summary=json.dumps({"action": "done", "summary": "mutating done"}),
                )
            else:
                # Worker child: returns done
                return _make_result(), _make_report(
                    output_summary=json.dumps({"action": "done", "summary": "worker done"}),
                )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        # Step 1: parent fans out
        orch.run_step("T-BARRIER")
        parent = db.get_task("T-BARRIER")
        assert parent.block_kind == BlockKind.DELEGATED

        children = db.get_children("T-BARRIER")
        assert len(children) == 2
        worker_id = None
        mutating_id = None
        for cid in children:
            c = db.get_task(cid)
            if c.assigned_agent == "dev_agent":
                worker_id = cid
            else:
                mutating_id = cid
        assert worker_id is not None
        assert mutating_id is not None

        # Step 2: run and complete the worker child first
        orch.run_step(worker_id)
        worker = db.get_task(worker_id)
        assert worker.status == TaskStatus.COMPLETED

        # Parent should NOT have been woken — mutating child still PENDING
        parent = db.get_task("T-BARRIER")
        assert parent.status == TaskStatus.IN_PROGRESS, (
            f"parent should be IN_PROGRESS until mutating child is terminal, got {parent.status}"
        )
        assert parent.block_kind == BlockKind.DELEGATED

        # Step 3: run and complete the mutating child
        orch.run_step(mutating_id)
        mutating = db.get_task(mutating_id)
        assert mutating.status == TaskStatus.COMPLETED

        # Parent should be woken now (all children terminal)
        parent = db.get_task("T-BARRIER")
        # The parent was enqueued when the last child completed.
        # active_fanout is still set until the CAS winner runs run_step.
        assert parent.status == TaskStatus.IN_PROGRESS
        assert parent.active_fanout is not None

        # Step 4: run the parent's join step
        orch.run_step("T-BARRIER")
        parent = db.get_task("T-BARRIER")
        # After join context injection and agent re-run, the parent should
        # be pending (ready for next agent session)
        assert parent.active_fanout is None, "active_fanout should be cleared after join"

    # ── Test 4: pipeline carrier unaffected ──

    def test_pipeline_carrier_stays_subtask(self, runtime, db, monkeypatch):
        """A pipeline carrier (child with `then` pre-declared) stays
        task_type='subtask' even when targeted at a team manager — it
        never runs an agent session of its own."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        (runtime.workspaces_dir / "engineering_head").mkdir(parents=True)
        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
        (runtime.workspaces_dir / "code_reviewer").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-PIPE-TYPE", brief="pipeline type test",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        q = _SlugQueue()
        orch._queue = q

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": [
                        {
                            "agent": "engineering_head",
                            "prompt": "pipeline to manager",
                            "then": [{"agent": "code_reviewer", "prompt": "review",
                                      "expect_verdict": "APPROVE"}],
                        },
                        {
                            "agent": "dev_agent",
                            "prompt": "plain worker child",
                        },
                    ],
                    "width_cap_ack": 2,
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-PIPE-TYPE")

        children = db.get_children("T-PIPE-TYPE")
        assert len(children) == 2
        # Find the pipeline carrier (has active_chain)
        carrier = None
        for cid in children:
            c = db.get_task(cid)
            if c.active_chain is not None:
                carrier = c
                break
        assert carrier is not None, "pipeline carrier not found"
        assert carrier.assigned_agent == "engineering_head"
        # Pipeline carrier: IN_PROGRESS+DELEGATED, task_type stays subtask
        assert carrier.task_type == "subtask", (
            f"pipeline carrier should stay task_type='subtask', got {carrier.task_type!r}"
        )
        assert carrier.status == TaskStatus.IN_PROGRESS
        assert carrier.block_kind == BlockKind.DELEGATED
        assert carrier.active_chain is not None

        # The carrier's first leg is spawned separately as a subtask of the carrier
        carrier_children = db.get_children(carrier.id)
        assert len(carrier_children) == 1

    # ── THR-012 Phase-2(c): review gate REMOVED ──

    def test_width_5_spawns_immediately_no_review_gate(self, runtime, db, monkeypatch):
        """Width 5 (previously > FANOUT_REVIEW_THRESHOLD=4) spawns immediately
        with NO review job — the review gate is REMOVED."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry
        from runtime.orchestrator.fanout import FanoutState

        for i in range(5):
            (runtime.workspaces_dir / f"agent{i}").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-NO-GATE", brief="no review gate",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        orch._queue = _SlugQueue()

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            children = [{"agent": f"agent{i}", "prompt": f"task {i}"} for i in range(5)]
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": children,
                    "width_cap_ack": 5,
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-NO-GATE")

        parent = db.get_task("T-NO-GATE")
        # Should spawn children immediately — NOT parked on BLOCKED_ON_JOB.
        assert parent.block_kind == BlockKind.DELEGATED, (
            f"expected DELEGATED (children spawned), got {parent.block_kind}"
        )
        # active_fanout should be 'spawned', NOT 'pending_review'
        assert parent.active_fanout is not None
        fs = FanoutState.deserialize(parent.active_fanout)
        assert fs.status == "spawned", (
            f"expected status='spawned' (no review gate), got {fs.status!r}"
        )
        assert fs.width == 5
        # Children should exist
        children = db.get_children("T-NO-GATE")
        assert len(children) == 5
        # No fanout_review_required audit entry should exist
        logs = db.get_audit_logs("T-NO-GATE")
        review_actions = [
            e for e in logs
            if e.get("action") == "fanout_review_required"
        ]
        assert len(review_actions) == 0, (
            "no fanout_review_required audit entry should be created"
        )
        # A fanout_spawned audit entry should exist instead
        spawned = [e for e in logs if e.get("action") == "fanout_spawned"]
        assert len(spawned) == 1, "fanout_spawned should be logged"

    def test_width_7_also_spawns_immediately(self, runtime, db, monkeypatch):
        """Width 7 (well above the old threshold) also spawns immediately.
        Uses agents on the test team (agent0-4 + dev_agent + qa_engineer = 7)."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        agents = ["dev_agent", "qa_engineer", "agent0", "agent1", "agent2", "agent3", "agent4"]
        for a in agents:
            (runtime.workspaces_dir / a).mkdir(parents=True, exist_ok=True)

        db.insert_task(TaskRecord(
            id="T-WIDE7", brief="wide fanout 7",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        orch._queue = _SlugQueue()

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            children = [{"agent": a, "prompt": f"task {a}"} for a in agents]
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": children,
                    "width_cap_ack": 7,
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-WIDE7")

        parent = db.get_task("T-WIDE7")
        assert parent.block_kind == BlockKind.DELEGATED
        children = db.get_children("T-WIDE7")
        assert len(children) == 7

    def test_no_fanout_ever_enters_pending_review(self, runtime, db, monkeypatch):
        """No fan-out of ANY width enters pending_review.
        Width 4 (at the old threshold) spawns immediately."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry
        from runtime.orchestrator.fanout import FanoutState

        agents = ["dev_agent", "qa_engineer", "agent0", "agent1"]
        for a in agents:
            (runtime.workspaces_dir / a).mkdir(parents=True, exist_ok=True)

        db.insert_task(TaskRecord(
            id="T-WIDE4", brief="wide fanout 4",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        orch._queue = _SlugQueue()

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            children = [{"agent": a, "prompt": f"task {a}"} for a in agents]
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": children,
                    "width_cap_ack": 4,
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-WIDE4")

        parent = db.get_task("T-WIDE4")
        assert parent.active_fanout is not None
        fs = FanoutState.deserialize(parent.active_fanout)
        assert fs.status == "spawned", (
            f"width 4 should spawn immediately, got status={fs.status!r}"
        )
        assert fs.width == 4
        children = db.get_children("T-WIDE4")
        assert len(children) == 4

    def test_width_9_still_parse_rejects(self):
        """Width > MAX_FANOUT_WIDTH (8) still parse-rejects — cap intact."""
        children = [FanoutChild(agent=f"agent{i}", prompt=f"task {i}") for i in range(9)]
        decision = NextStep(action="fanout", children=children, width_cap_ack=9)
        err = validate_fanout_decision(decision)
        assert err is not None
        assert "exceeds max" in err.lower()

    def test_mutating_width_3_no_review_job(self, runtime, db, monkeypatch):
        """A MUTATING fan-out (manager-targeted child) at width 3 spawns
        immediately with NO review job."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        (runtime.workspaces_dir / "engineering_head").mkdir(parents=True)
        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
        (runtime.workspaces_dir / "qa_engineer").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-MUT-NOGATE", brief="mutating no gate",
            assigned_agent="engineering_head",
        ))
        orch = Orchestrator(
            db=db, settings=Settings(),
            paths=runtime, slug="test",
            teams=TeamsRegistry.load(runtime.root),
        )
        orch._queue = _SlugQueue()

        def fake_run_agent(task_id, agent, prompt, on_session_started=None):
            return _make_result(), _make_report(
                output_summary=json.dumps({
                    "action": "fanout",
                    "children": [
                        {"agent": "engineering_head", "prompt": "mutating child"},
                        {"agent": "dev_agent", "prompt": "worker child"},
                        {"agent": "qa_engineer", "prompt": "worker child 2"},
                    ],
                    "width_cap_ack": 3,
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-MUT-NOGATE")

        parent = db.get_task("T-MUT-NOGATE")
        assert parent.block_kind == BlockKind.DELEGATED
        # Mutating child should have task_type='task'
        children = db.get_children("T-MUT-NOGATE")
        assert len(children) == 3
        mgr_children = [
            cid for cid in children
            if db.get_task(cid).task_type == "task"
        ]
        assert len(mgr_children) == 1, "exactly one mutating (manager-targeted) child"
