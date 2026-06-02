from __future__ import annotations

from unittest.mock import MagicMock

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import BlockKind, ChainLeg, NextStep, TaskRecord, TaskStatus
from runtime.orchestrator.run_step import _validate_delegate


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
    from runtime.orchestrator.run_step import _chain_legs_off_team

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
    from runtime.orchestrator.run_step import _chain_legs_off_team
    teams = MagicMock()
    teams.team_for_manager.return_value = "engineering"
    teams.team_for_agent.return_value = "engineering"
    decision = NextStep(action="delegate", agent="dev", prompt="x", then=[
        ChainLeg(agent="sr", prompt="y"),
    ])
    assert _chain_legs_off_team(teams, manager="eh", decision=decision) == []


def test_cross_team_chain_guard_first_leg_off_team():
    """First leg's off-team membership is also caught by the new helper."""
    from runtime.orchestrator.run_step import _chain_legs_off_team
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
    from runtime.infrastructure.database import Database
    from runtime.orchestrator.chain import ChainState

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
    from runtime.infrastructure.database import Database
    db = Database(tmp_path / "x.db")
    rid = db.insert_audit_log(
        task_id="TASK-1", agent="orchestrator",
        action="orchestration_step",
        payload={"step_number": 1, "decision": {"action": "done"}},
    )
    assert isinstance(rid, int)
    assert rid > 0


def test_log_orchestration_step_returns_audit_row_id(tmp_path):
    from runtime.infrastructure.database import Database
    from runtime.infrastructure.audit_logger import AuditLogger
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
    from runtime.infrastructure.database import Database
    from runtime.orchestrator.chain import ChainState

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

    from runtime.orchestrator.run_step import _advance_chain_for_completed_child
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
    from runtime.infrastructure.database import Database
    from runtime.orchestrator.chain import ChainState

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

    from runtime.orchestrator.run_step import _advance_chain_for_completed_child
    outcome = _advance_chain_for_completed_child(
        orch=_orch_with_db(db),
        parent_task_id="TASK-001",
        child_task_id="TASK-002",
    )
    assert outcome == "wake"
    # chain must be cleared so the manager can decide next step
    assert db.get_task("TASK-001").active_chain is None


def test_chain_final_leg_wakes_manager_and_clears_chain(tmp_path):
    """When the LAST leg matches its expected verdict, parent wakes (not
    auto-done) and active_chain is cleared."""
    from runtime.infrastructure.database import Database
    from runtime.orchestrator.chain import ChainState

    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-P", team="engineering", brief="p"))
    db.update_task("TASK-P", status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED)
    chain = ChainState(
        step_index=1,  # final leg in flight (the only entry in `legs`)
        first_leg_expect_verdict=None,
        legs=[ChainLeg(agent="qa", prompt="q", expect_verdict="PASS")],
        step_audit_id=1,
    )
    db.update_task_active_chain("TASK-P", chain.serialize())

    db.insert_task(TaskRecord(
        id="TASK-C2", team="engineering", brief="qa",
        parent_task_id="TASK-P", assigned_agent="qa",
    ))
    db.update_task("TASK-C2", status=TaskStatus.COMPLETED)
    db.insert_task_result(
        task_id="TASK-C2", agent="qa", session_id="s",
        status="completed", confidence_score=90,
        output_summary="all green", verdict="PASS",
    )

    from runtime.orchestrator.run_step import _advance_chain_for_completed_child
    out = _advance_chain_for_completed_child(
        orch=_orch_with_db(db),
        parent_task_id="TASK-P",
        child_task_id="TASK-C2",
    )
    assert out == "wake"
    assert db.get_task("TASK-P").active_chain is None


def test_chain_step_count_not_bumped_on_auto_advance(tmp_path):
    """The whole point of chains: auto-advancing legs must NOT consume the
    parent's orchestration_step_count budget."""
    from runtime.infrastructure.database import Database
    from runtime.orchestrator.chain import ChainState

    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-P", team="engineering", brief="p"))
    db.update_task("TASK-P", status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED)
    initial_count = db.get_task("TASK-P").orchestration_step_count

    chain = ChainState(
        step_index=0, first_leg_expect_verdict=None,
        legs=[
            ChainLeg(agent="sr", prompt="r", expect_verdict="APPROVE"),
            ChainLeg(agent="qa", prompt="q", expect_verdict="PASS"),
        ],
        step_audit_id=1,
    )
    db.update_task_active_chain("TASK-P", chain.serialize())

    # Walk through two auto-advances.
    for cid, verdict, agent in [
        ("TASK-C1", None, "dev"),         # ungated first leg
        ("TASK-C2", "APPROVE", "sr"),    # second leg expects APPROVE
    ]:
        db.insert_task(TaskRecord(
            id=cid, team="engineering", brief="x",
            parent_task_id="TASK-P", assigned_agent=agent,
        ))
        db.update_task(cid, status=TaskStatus.COMPLETED)
        db.insert_task_result(
            task_id=cid, agent=agent, session_id="s",
            status="completed", confidence_score=80, output_summary="ok",
            verdict=verdict,
        )
        from runtime.orchestrator.run_step import _advance_chain_for_completed_child
        _advance_chain_for_completed_child(
            orch=_orch_with_db(db),
            parent_task_id="TASK-P",
            child_task_id=cid,
        )

    final = db.get_task("TASK-P")
    assert final.orchestration_step_count == initial_count  # zero growth — the load-bearing invariant


def test_chain_summary_appended_to_prior_steps_when_chain_just_cleared(tmp_path):
    """After a chain clears (success or abort), the next time the manager
    wakes, its `prior_steps` history includes a synthetic summary entry so
    the manager can read what happened without re-deriving from raw child
    task records."""
    from runtime.infrastructure.database import Database
    from runtime.orchestrator.run_step import _build_prior_steps_from_db
    from runtime.models import TaskStatus

    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-P", team="engineering", brief="p"))
    # Three children: two completed, one most-recent reported APPROVE.
    for cid in ("TASK-C1", "TASK-C2", "TASK-C3"):
        db.insert_task(TaskRecord(id=cid, team="engineering", brief=cid, parent_task_id="TASK-P", assigned_agent="w"))
        db.update_task(cid, status=TaskStatus.COMPLETED, note="ok")
        db.insert_task_result(
            task_id=cid, agent="w", session_id="s",
            status="completed", confidence_score=80,
            output_summary=f"{cid} ok",
            verdict=("APPROVE" if cid == "TASK-C3" else None),
        )

    # Two chain_auto_advance audit rows (C1 → C2, C2 → C3) simulating a chain
    # that just ran and ended at C3.
    db.insert_audit_log(
        task_id="TASK-P", agent="orchestrator", action="chain_auto_advance",
        payload={
            "leg_index": 1, "spawned_child_id": "TASK-C2",
            "triggering_child_id": "TASK-C1", "triggering_verdict": None,
            "chain_origin_step_audit_id": 1,
        },
    )
    db.insert_audit_log(
        task_id="TASK-P", agent="orchestrator", action="chain_auto_advance",
        payload={
            "leg_index": 2, "spawned_child_id": "TASK-C3",
            "triggering_child_id": "TASK-C2", "triggering_verdict": "APPROVE",
            "chain_origin_step_audit_id": 1,
        },
    )

    from unittest.mock import MagicMock
    orch = MagicMock()
    orch._db = db
    steps = _build_prior_steps_from_db(orch, "TASK-P")
    # Last step should be a synthetic chain summary; assert the word "chain"
    # appears in either the action or result_summary field.
    assert any(
        "chain" in s.action.lower() or "chain" in s.result_summary.lower()
        for s in steps
    )


def test_chain_summary_not_appended_when_no_chain_ran(tmp_path):
    """When the parent has no chain_auto_advance audit rows, no synthetic
    chain summary is appended — the existing per-child steps stand alone."""
    from runtime.infrastructure.database import Database
    from runtime.orchestrator.run_step import _build_prior_steps_from_db
    from runtime.models import TaskStatus

    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-P", team="engineering", brief="p"))
    db.insert_task(TaskRecord(id="TASK-C1", team="engineering", brief="c", parent_task_id="TASK-P", assigned_agent="w"))
    db.update_task("TASK-C1", status=TaskStatus.COMPLETED, note="ok")

    from unittest.mock import MagicMock
    orch = MagicMock()
    orch._db = db
    steps = _build_prior_steps_from_db(orch, "TASK-P")
    assert all(
        "chain" not in s.action.lower() and "chain" not in s.result_summary.lower()
        for s in steps
    )


def test_cascade_fail_clears_active_chain(tmp_path):
    """When a chain leg's child fails, the cascade-fail path must clear the
    parent's active_chain so the FAILED parent doesn't carry a stale chain
    pointer (would render confusingly in CLI/Web UI)."""
    from runtime.infrastructure.database import Database
    from runtime.orchestrator.chain import ChainState
    from runtime.orchestrator._paths import OrgPaths
    from runtime.models import TaskStatus, BlockKind
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-P", team="engineering", brief="p", parent_task_id=None))
    db.update_task("TASK-P", status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED)
    chain = ChainState(
        step_index=0, first_leg_expect_verdict=None,
        legs=[ChainLeg(agent="sr", prompt="r", expect_verdict="APPROVE")],
        step_audit_id=1,
    )
    db.update_task_active_chain("TASK-P", chain.serialize())

    # Child that ended in FAILED (not COMPLETED) — triggers cascade-fail path.
    db.insert_task(TaskRecord(
        id="TASK-C1", team="engineering", brief="b",
        parent_task_id="TASK-P", assigned_agent="dev",
    ))
    db.update_task("TASK-C1", status=TaskStatus.FAILED, note="executor crashed")

    orch = MagicMock()
    orch._db = db
    orch._audit = AuditLogger(db)
    orch._queue = None
    orch._slug = "test-org"
    # Use a real OrgPaths pointing at tmp_path so _notify_failure_if_eligible →
    # load_org_config finds no config.yaml and returns early (a MagicMock path
    # causes yaml.safe_load to hang indefinitely reading MagicMock.read() calls).
    orch._paths = OrgPaths(tmp_path)

    _enqueue_parent_if_waiting(orch, "TASK-C1")

    parent_after = db.get_task("TASK-P")
    assert parent_after.status == TaskStatus.FAILED
    assert parent_after.active_chain is None, "cascade-fail must clear active_chain"


def test_chain_cleared_on_cascade_fail(tmp_path):
    """When a chain leg fails (not completes), the parent's active_chain
    must be cleared so the UI doesn't render a stale chain strip."""
    from runtime.infrastructure.database import Database
    from runtime.orchestrator.chain import ChainState
    from runtime.orchestrator.run_step import _fail
    from runtime.models import TaskStatus

    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-P", team="engineering", brief="p", parent_task_id=None))
    db.update_task("TASK-P", status=TaskStatus.IN_PROGRESS)
    chain = ChainState(
        step_index=0, first_leg_expect_verdict=None,
        legs=[ChainLeg(agent="sr", prompt="r", expect_verdict="APPROVE")],
        step_audit_id=1,
    )
    db.update_task_active_chain("TASK-P", chain.serialize())
    assert db.get_task("TASK-P").active_chain is not None

    orch = MagicMock()
    orch._db = db
    _fail(orch, "TASK-P", note="cascade")

    task = db.get_task("TASK-P")
    assert task.status == TaskStatus.FAILED
    assert task.active_chain is None  # the fix


def test_chain_summary_filters_to_most_recent_chain_only(tmp_path):
    """When a parent has audit rows from multiple SEQUENTIAL chains, the
    summary covers ONLY the most-recent chain (identified by
    chain_origin_step_audit_id), not a conflated union."""
    from runtime.infrastructure.database import Database
    from runtime.orchestrator.run_step import _summarize_recent_chain
    from runtime.models import TaskStatus

    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-P", team="engineering", brief="p"))
    # Chain A (origin step audit id = 10): A1 → A2
    for cid in ("TASK-A1", "TASK-A2"):
        db.insert_task(TaskRecord(id=cid, team="engineering", brief=cid, parent_task_id="TASK-P", assigned_agent="w"))
        db.update_task(cid, status=TaskStatus.COMPLETED, note="ok")
        db.insert_task_result(task_id=cid, agent="w", session_id="s", status="completed", confidence_score=80, output_summary=f"{cid} ok", verdict=None)
    db.insert_audit_log(
        task_id="TASK-P", agent="orchestrator", action="chain_auto_advance",
        payload={"leg_index": 1, "spawned_child_id": "TASK-A2", "triggering_child_id": "TASK-A1", "triggering_verdict": None, "chain_origin_step_audit_id": 10},
    )

    # Chain B (origin step audit id = 20): B1 → B2
    for cid in ("TASK-B1", "TASK-B2"):
        db.insert_task(TaskRecord(id=cid, team="engineering", brief=cid, parent_task_id="TASK-P", assigned_agent="w"))
        db.update_task(cid, status=TaskStatus.COMPLETED, note="ok")
        db.insert_task_result(task_id=cid, agent="w", session_id="s", status="completed", confidence_score=80, output_summary=f"{cid} ok", verdict="APPROVE" if cid == "TASK-B2" else None)
    db.insert_audit_log(
        task_id="TASK-P", agent="orchestrator", action="chain_auto_advance",
        payload={"leg_index": 1, "spawned_child_id": "TASK-B2", "triggering_child_id": "TASK-B1", "triggering_verdict": None, "chain_origin_step_audit_id": 20},
    )

    from unittest.mock import MagicMock
    orch = MagicMock()
    orch._db = db
    summary = _summarize_recent_chain(orch, "TASK-P")
    # Should mention TASK-B1 and TASK-B2 only — NOT TASK-A1 or TASK-A2.
    assert summary is not None
    assert "TASK-B1" in summary
    assert "TASK-B2" in summary
    assert "TASK-A1" not in summary
    assert "TASK-A2" not in summary


def test_chain_summary_suppressed_when_manager_decision_landed_after_chain(tmp_path):
    """After the manager has already seen the chain summary (in the wake where
    the chain ended) and made a subsequent non-chain decision, later wakes
    must NOT re-append the stale chain summary. The marker is: an
    `orchestration_step` audit row with id > max chain_auto_advance id means
    the manager has moved on."""
    from runtime.infrastructure.database import Database
    from runtime.orchestrator.run_step import _summarize_recent_chain
    from runtime.models import TaskStatus

    db = Database(tmp_path / "x.db")
    db.insert_task(TaskRecord(id="TASK-P", team="engineering", brief="p"))
    # Chain ran: 1 child, then 1 auto-advance.
    for cid in ("TASK-C1", "TASK-C2"):
        db.insert_task(TaskRecord(id=cid, team="engineering", brief=cid, parent_task_id="TASK-P", assigned_agent="w"))
        db.update_task(cid, status=TaskStatus.COMPLETED, note="ok")
        db.insert_task_result(
            task_id=cid, agent="w", session_id="s", status="completed",
            confidence_score=80, output_summary=f"{cid} ok",
            verdict="APPROVE" if cid == "TASK-C2" else None,
        )
    db.insert_audit_log(
        task_id="TASK-P", agent="orchestrator", action="chain_auto_advance",
        payload={"leg_index": 1, "spawned_child_id": "TASK-C2",
                 "triggering_child_id": "TASK-C1", "triggering_verdict": None,
                 "chain_origin_step_audit_id": 1},
    )

    orch = MagicMock()
    orch._db = db

    # Before any post-chain manager decision: summary SHOULD show.
    summary = _summarize_recent_chain(orch, "TASK-P")
    assert summary is not None
    assert "TASK-C1" in summary

    # Manager wakes (final-leg wake), makes a non-chain delegate decision.
    # That decision logs an orchestration_step audit row.
    db.insert_audit_log(
        task_id="TASK-P", agent="orchestrator", action="orchestration_step",
        payload={"step_number": 2, "decision": {"action": "delegate", "agent": "cleanup", "prompt": "..."}},
    )

    # Later wake (triggered by cleanup child completing): summary must NOT show,
    # because the manager already saw it at the prior wake.
    summary = _summarize_recent_chain(orch, "TASK-P")
    assert summary is None
