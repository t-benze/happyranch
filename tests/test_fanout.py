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
    NextStep,
    TaskRecord,
    TaskStatus,
    BlockKind,
)
from runtime.orchestrator.fanout import (
    MAX_FANOUT_WIDTH,
    FANOUT_REVIEW_THRESHOLD,
    FanoutState,
    build_fanout_join_context,
    collect_child_join_info,
    fanout_child_targets,
    fanout_needs_review,
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

    def test_accepts_width_cap_ack_none(self):
        """width_cap_ack=None is allowed (omit it)."""
        decision = NextStep(
            action="fanout",
            children=[FanoutChild(agent="a", prompt="1"), FanoutChild(agent="b", prompt="2")],
        )
        assert validate_fanout_decision(decision) is None

    def test_rejects_per_child_then_phase1(self):
        decision = NextStep(
            action="fanout",
            children=[
                FanoutChild(agent="a", prompt="1"),
                FanoutChild(agent="b", prompt="2", then=[ChainLeg(agent="c", prompt="review")]),
            ],
        )
        err = validate_fanout_decision(decision)
        assert err is not None
        assert "then" in err.lower()

    def test_rejects_per_child_expect_verdict_phase1(self):
        decision = NextStep(
            action="fanout",
            children=[
                FanoutChild(agent="a", prompt="1"),
                FanoutChild(agent="b", prompt="2", expect_verdict="APPROVE"),
            ],
        )
        err = validate_fanout_decision(decision)
        assert err is not None
        assert "expect_verdict" in err.lower()


# --- FanoutState tests ---


class TestFanoutState:
    def test_serialize_roundtrip(self):
        fs = FanoutState(
            children_ids=["TASK-100", "TASK-101"],
            width=2,
            manager_agent="eng_mgr",
            join_summary="Review both",
        )
        serialized = fs.serialize()
        fs2 = FanoutState.deserialize(serialized)
        assert fs2.children_ids == ["TASK-100", "TASK-101"]
        assert fs2.width == 2
        assert fs2.manager_agent == "eng_mgr"
        assert fs2.join_summary == "Review both"

    def test_serialize_omits_none_join_summary(self):
        fs = FanoutState(children_ids=["T-1"], width=1, manager_agent="m")
        s = fs.serialize()
        fs2 = FanoutState.deserialize(s)
        assert fs2.join_summary is None


# --- Join context tests ---


class TestFanoutJoinContext:
    def test_build_join_context_includes_all_children(self):
        fs = FanoutState(children_ids=["T-1", "T-2"], width=2, manager_agent="mgr")
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
            children_ids=["T-1"], width=1, manager_agent="mgr",
            join_summary="Combine results",
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

    def test_fanout_needs_review(self):
        assert fanout_needs_review(FANOUT_REVIEW_THRESHOLD) is False
        assert fanout_needs_review(FANOUT_REVIEW_THRESHOLD + 1) is True
        assert fanout_needs_review(2) is False
        assert fanout_needs_review(5) is True


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
        ok = db.try_delegate_many("TASK-FTP", children, parent_note="Fan-out 2 children")
        assert ok is True
        c1 = db.get_task("TASK-FTC1")
        assert c1 is not None and c1.parent_task_id == "TASK-FTP"
        c2 = db.get_task("TASK-FTC2")
        assert c2 is not None
        p = db.get_task("TASK-FTP")
        assert p.status == TaskStatus.IN_PROGRESS
        assert p.block_kind == BlockKind.DELEGATED

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
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-FANOUT-SCOPE")

        parent = db.get_task("T-FANOUT-SCOPE")
        # Off-team rejection → feedback step (task goes back to pending)
        assert parent.status == TaskStatus.PENDING

    def test_fanout_rejects_per_child_then(self, runtime, db, monkeypatch):
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)
        (runtime.workspaces_dir / "qa_engineer").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-FANOUT-THEN", brief="with then",
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
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-FANOUT-THEN")

        parent = db.get_task("T-FANOUT-THEN")
        assert parent.status == TaskStatus.FAILED
        assert "then" in (parent.note or "").lower()

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

    def test_fanout_review_threshold_fails(self, runtime, db, monkeypatch):
        """Width > FANOUT_REVIEW_THRESHOLD → task fails with review note."""
        import json
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.orchestrator.teams import TeamsRegistry

        for i in range(5):
            (runtime.workspaces_dir / f"agent{i}").mkdir(parents=True)

        db.insert_task(TaskRecord(
            id="T-FANOUT-REVIEW", brief="needs review",
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
                }),
            )
        monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

        orch.run_step("T-FANOUT-REVIEW")

        parent = db.get_task("T-FANOUT-REVIEW")
        assert parent.status == TaskStatus.FAILED
        assert "review threshold" in (parent.note or "").lower()

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
