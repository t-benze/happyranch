from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef
from runtime.orchestrator.org_validation import (
    OrgConsistencyError,
    validate_team_membership,
)
from runtime.orchestrator.teams import TeamsRegistry


def _seed_empty_org(org_root: Path) -> Path:
    (org_root / "org" / "agents").mkdir(parents=True)
    (org_root / "org" / "agents" / "_pending").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    return org_root


def _write_active(paths: OrgPaths, agent: AgentDef) -> None:
    target = paths.agents_dir / f"{agent.name}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    from runtime.orchestrator.agent_def import render_agent_text
    target.write_text(render_agent_text(agent))


def _make_agent(*, name: str, team: str, role: str) -> AgentDef:
    return AgentDef(
        name=name,
        team=team,
        role=role,
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by="founder",
        enrolled_at_task=None,
        enrolled_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        system_prompt=f"You are {name}.\n",
        description="Test agent",
    )


def test_validate_passes_when_empty(tmp_path: Path) -> None:
    org_root = _seed_empty_org(tmp_path / "orgs" / "alpha")
    teams = TeamsRegistry.load(org_root)
    paths = OrgPaths(root=org_root)
    validate_team_membership(paths, teams)  # no raise


def test_validate_passes_when_consistent(tmp_path: Path) -> None:
    org_root = _seed_empty_org(tmp_path / "orgs" / "alpha")
    (org_root / "org" / "teams.yaml").write_text(
        "teams:\n"
        "  ops:\n"
        "    manager: ops_manager\n"
        "    workers: [worker_a]\n"
    )
    paths = OrgPaths(root=org_root)
    _write_active(paths, _make_agent(name="ops_manager", team="ops", role="manager"))
    _write_active(paths, _make_agent(name="worker_a", team="ops", role="worker"))
    teams = TeamsRegistry.load(org_root)
    validate_team_membership(paths, teams)  # no raise


def test_validate_raises_when_agent_team_missing(tmp_path: Path) -> None:
    """The family-org bug shape: manager declares a team that teams.yaml doesn't know."""
    org_root = _seed_empty_org(tmp_path / "orgs" / "family")
    paths = OrgPaths(root=org_root)
    _write_active(paths, _make_agent(
        name="family_manager", team="family_operations", role="manager",
    ))
    teams = TeamsRegistry.load(org_root)  # teams.yaml is "teams: {}"
    with pytest.raises(OrgConsistencyError) as exc_info:
        validate_team_membership(paths, teams)
    msg = str(exc_info.value)
    assert "family_manager" in msg
    assert "family_operations" in msg
    assert "teams.yaml" in msg


def test_validate_raises_when_manager_mismatch(tmp_path: Path) -> None:
    """An agent file says role=manager but teams.yaml names someone else."""
    org_root = _seed_empty_org(tmp_path / "orgs" / "alpha")
    (org_root / "org" / "teams.yaml").write_text(
        "teams:\n"
        "  ops:\n"
        "    manager: real_manager\n"
        "    workers: []\n"
    )
    paths = OrgPaths(root=org_root)
    _write_active(paths, _make_agent(name="impostor", team="ops", role="manager"))
    teams = TeamsRegistry.load(org_root)
    with pytest.raises(OrgConsistencyError) as exc_info:
        validate_team_membership(paths, teams)
    msg = str(exc_info.value)
    assert "impostor" in msg
    assert "real_manager" in msg


def test_validate_collects_all_drift(tmp_path: Path) -> None:
    """Founder should see every drift in one read, not whack-a-mole."""
    org_root = _seed_empty_org(tmp_path / "orgs" / "alpha")
    paths = OrgPaths(root=org_root)
    _write_active(paths, _make_agent(name="a1", team="missing_team_1", role="manager"))
    _write_active(paths, _make_agent(name="a2", team="missing_team_2", role="worker"))
    teams = TeamsRegistry.load(org_root)
    with pytest.raises(OrgConsistencyError) as exc_info:
        validate_team_membership(paths, teams)
    msg = str(exc_info.value)
    assert "a1" in msg
    assert "a2" in msg


def test_validate_ignores_pending_agents(tmp_path: Path) -> None:
    """Pending agents are validated at approve time, not load time."""
    org_root = _seed_empty_org(tmp_path / "orgs" / "alpha")
    paths = OrgPaths(root=org_root)
    prompt_loader.write_pending_agent(
        paths,
        _make_agent(name="pending_one", team="not_yet_wired", role="worker"),
    )
    teams = TeamsRegistry.load(org_root)
    validate_team_membership(paths, teams)  # no raise
