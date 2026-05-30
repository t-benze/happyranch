from __future__ import annotations

from unittest.mock import MagicMock

from src.infrastructure.audit_logger import AuditLogger
from src.models import BlockKind, ChainLeg, NextStep, TaskRecord, TaskStatus
from src.orchestrator.run_step import _validate_delegate


def _orch_with_workspaces_present():
    """Build a minimal mock Orchestrator-like object where ALL workspace.exists() returns True."""
    orch = MagicMock()
    workspace_root = MagicMock()
    workspace_root.exists.return_value = True
    orch._paths = MagicMock()
    orch._paths.workspaces_dir = MagicMock()
    orch._paths.workspaces_dir.__truediv__.return_value = workspace_root
    return orch


def test_validate_delegate_rejects_first_leg_with_no_agent():
    orch = _orch_with_workspaces_present()
    err = _validate_delegate(orch, NextStep(action="delegate", agent=None, prompt="x"))
    assert err is not None
    assert "agent" in err.lower()


def test_validate_delegate_passes_when_all_legs_have_agents_and_workspaces():
    orch = _orch_with_workspaces_present()
    decision = NextStep(
        action="delegate", agent="dev", prompt="build",
        then=[
            ChainLeg(agent="sr", prompt="review", expect_verdict="APPROVE"),
            ChainLeg(agent="qa", prompt="qa", expect_verdict="PASS"),
        ],
    )
    err = _validate_delegate(orch, decision)
    assert err is None


def test_validate_delegate_rejects_chain_leg_with_missing_workspace():
    orch = MagicMock()
    # First call (for first leg agent) returns workspace_root with exists()=True
    # Second call (for chain leg agent "ghost_agent") returns workspace_root with exists()=False
    workspace_first = MagicMock()
    workspace_first.exists.return_value = True
    workspace_ghost = MagicMock()
    workspace_ghost.exists.return_value = False
    orch._paths = MagicMock()
    orch._paths.workspaces_dir = MagicMock()

    def truediv(agent_name):
        if agent_name == "ghost_agent":
            return workspace_ghost
        return workspace_first

    orch._paths.workspaces_dir.__truediv__.side_effect = truediv

    decision = NextStep(
        action="delegate", agent="dev", prompt="build",
        then=[ChainLeg(agent="ghost_agent", prompt="x")],
    )
    err = _validate_delegate(orch, decision)
    assert err is not None
    assert "ghost_agent" in err


def test_cross_team_chain_guard_rejects_off_team_leg():
    """If any leg targets an agent not on the manager's team, the whole
    chain is rejected via the existing feedback mechanism."""
    from src.orchestrator.run_step import _chain_legs_off_team

    teams = MagicMock()
    teams.team_for_manager.return_value = "engineering"
    teams.team_for_agent.side_effect = lambda name: {
        "dev": "engineering",
        "sr": "engineering",
        "outsider": "content",
    }.get(name)

    decision = NextStep(
        action="delegate", agent="dev", prompt="build",
        then=[
            ChainLeg(agent="sr", prompt="review"),
            ChainLeg(agent="outsider", prompt="other"),
        ],
    )
    off = _chain_legs_off_team(teams, manager="eh", decision=decision)
    assert off == [("outsider", "content")]


def test_cross_team_chain_guard_passes_when_all_legs_on_team():
    from src.orchestrator.run_step import _chain_legs_off_team
    teams = MagicMock()
    teams.team_for_manager.return_value = "engineering"
    teams.team_for_agent.return_value = "engineering"
    decision = NextStep(action="delegate", agent="dev", prompt="x", then=[
        ChainLeg(agent="sr", prompt="y"),
    ])
    assert _chain_legs_off_team(teams, manager="eh", decision=decision) == []


def test_cross_team_chain_guard_first_leg_off_team():
    """First leg's off-team membership is also caught by the new helper."""
    from src.orchestrator.run_step import _chain_legs_off_team
    teams = MagicMock()
    teams.team_for_manager.return_value = "engineering"
    teams.team_for_agent.side_effect = lambda name: {
        "outsider": "content",
        "sr": "engineering",
    }.get(name)
    decision = NextStep(action="delegate", agent="outsider", prompt="x", then=[
        ChainLeg(agent="sr", prompt="y"),
    ])
    off = _chain_legs_off_team(teams, manager="eh", decision=decision)
    assert off == [("outsider", "content")]


def test_chain_persistence_writes_active_chain_with_step_audit_id(tmp_path):
    """When a manager declares a delegate with `then` or `expect_verdict`,
    the orchestrator persists ChainState on the parent before/with the first
    leg spawn. This test verifies the persistence API shape; the orchestrator
    wire-up is tested in integration tests (Task 14)."""
    from src.infrastructure.database import Database
    from src.orchestrator.chain import ChainState

    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-1", brief="parent"))
    chain = ChainState(
        step_index=0,
        first_leg_expect_verdict=None,
        legs=[ChainLeg(agent="sr", prompt="r", expect_verdict="APPROVE")],
        step_audit_id=42,
    )
    db.update_task_active_chain("TASK-1", chain.serialize())

    task = db.get_task("TASK-1")
    parsed = ChainState.deserialize(task.active_chain)
    assert parsed.step_index == 0
    assert parsed.step_audit_id == 42
    assert len(parsed.legs) == 1


def test_insert_audit_log_returns_row_id(tmp_path):
    """Database.insert_audit_log should return the inserted row's id (lastrowid).
    This is the audit_row_id the chain-persistence code uses for step_audit_id."""
    from src.infrastructure.database import Database
    db = Database(tmp_path / "x.db")
    rid = db.insert_audit_log(
        task_id="TASK-1", agent="orchestrator",
        action="orchestration_step",
        payload={"step_number": 1, "decision": {"action": "done"}},
    )
    assert isinstance(rid, int)
    assert rid > 0


def test_log_orchestration_step_returns_audit_row_id(tmp_path):
    from src.infrastructure.database import Database
    from src.infrastructure.audit_logger import AuditLogger
    db = Database(tmp_path / "x.db")
    rid = AuditLogger(db).log_orchestration_step("TASK-1", 1, {"action": "done"})
    assert isinstance(rid, int)
    assert rid > 0


# --- Task 8: chain-advance helper tests ---


def _orch_with_db(db):
    """Test-double Orchestrator that satisfies _advance_chain_for_completed_child."""
    orch = MagicMock()
    orch._db = db
    orch._audit = AuditLogger(db)
    orch._queue = None
    orch._slug = "test-org"
    return orch


def test_chain_branch_auto_advances_on_verdict_match(tmp_path):
    """When a chain leg's worker reports a matching verdict and a next leg
    exists, the orchestrator spawns the next leg instead of waking the parent.
    Parent's orchestration_step_count is NOT bumped."""
    from src.infrastructure.database import Database
    from src.orchestrator.chain import ChainState

    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-001", team="engineering", brief="parent"))
    db.update_task("TASK-001", status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED)
    chain = ChainState(
        step_index=0,
        first_leg_expect_verdict=None,
        legs=[
            ChainLeg(agent="sr", prompt="review brief", expect_verdict="APPROVE"),
            ChainLeg(agent="qa", prompt="qa brief", expect_verdict="PASS"),
        ],
        step_audit_id=1,
    )
    db.update_task_active_chain("TASK-001", chain.serialize())

    db.insert_task(TaskRecord(
        id="TASK-002", team="engineering", brief="build",
        parent_task_id="TASK-001", assigned_agent="dev",
    ))
    db.update_task("TASK-002", status=TaskStatus.COMPLETED)
    db.insert_task_result(
        task_id="TASK-002", agent="dev", session_id="s",
        status="completed", confidence_score=80,
        output_summary="built PR #1", verdict=None,  # first leg ungated
    )

    from src.orchestrator.run_step import _advance_chain_for_completed_child
    outcome = _advance_chain_for_completed_child(
        orch=_orch_with_db(db),
        parent_task_id="TASK-001",
        child_task_id="TASK-002",
    )
    assert outcome == "advance"
    children = db.get_children("TASK-001")
    assert len(children) == 2  # original + new leg
    new_child_id = children[1]
    new_child = db.get_task(new_child_id)
    assert new_child.assigned_agent == "sr"
    assert "review brief" in new_child.brief
    assert "Prior leg context" in new_child.brief
    cs2 = ChainState.deserialize(db.get_task("TASK-001").active_chain)
    assert cs2.step_index == 1
    # chain auto-advance does NOT bump orchestration_step_count
    assert db.get_task("TASK-001").orchestration_step_count == 0


def test_chain_branch_wakes_parent_on_verdict_mismatch(tmp_path):
    """Mismatched verdict aborts the chain; helper returns 'wake' and clears active_chain."""
    from src.infrastructure.database import Database
    from src.orchestrator.chain import ChainState

    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-001", team="engineering", brief="parent"))
    db.update_task("TASK-001", status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED)
    chain = ChainState(
        step_index=0,
        first_leg_expect_verdict="APPROVE",
        legs=[ChainLeg(agent="qa", prompt="qa brief", expect_verdict="PASS")],
        step_audit_id=1,
    )
    db.update_task_active_chain("TASK-001", chain.serialize())

    db.insert_task(TaskRecord(
        id="TASK-002", team="engineering", brief="review",
        parent_task_id="TASK-001", assigned_agent="sr",
    ))
    db.update_task("TASK-002", status=TaskStatus.COMPLETED)
    db.insert_task_result(
        task_id="TASK-002", agent="sr", session_id="s",
        status="completed", confidence_score=80,
        output_summary="needs changes", verdict="REQUEST_CHANGES",
    )

    from src.orchestrator.run_step import _advance_chain_for_completed_child
    outcome = _advance_chain_for_completed_child(
        orch=_orch_with_db(db),
        parent_task_id="TASK-001",
        child_task_id="TASK-002",
    )
    assert outcome == "wake"
    # chain must be cleared so the manager can decide next step
    assert db.get_task("TASK-001").active_chain is None
