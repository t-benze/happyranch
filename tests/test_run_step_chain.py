from __future__ import annotations

from unittest.mock import MagicMock

from src.models import ChainLeg, NextStep
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
