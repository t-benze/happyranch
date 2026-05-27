from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from src.config import Settings
from src.daemon.state import DaemonState
from src.orchestrator._paths import OrgPaths
from src.orchestrator.agent_def import AgentDef, render_agent_text
from src.orchestrator.org_validation import OrgConsistencyError
from src.runtime import RuntimeDir


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    (org_root / "org" / "agents").mkdir()
    (org_root / "workspaces").mkdir()
    (org_root / "kb").mkdir()
    (org_root / "talks").mkdir()


def test_from_runtime_loads_all_orgs(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    _seed_org(rt.orgs_dir / "beta")
    _seed_org(rt.orgs_dir / "_pending")  # reserved, must be skipped
    state = DaemonState.from_runtime(rt, Settings())
    assert sorted(state.orgs.keys()) == ["alpha", "beta"]


def test_get_org_unknown_raises(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    with pytest.raises(KeyError):
        state.get_org("does-not-exist")


@pytest.mark.asyncio
async def test_add_org_idempotent(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    org_a1 = await state.add_org("alpha")
    org_a2 = await state.add_org("alpha")
    assert org_a1 is org_a2  # same instance, not reloaded


def _seed_drifted_org(org_root: Path) -> None:
    """Org with an active manager that teams.yaml doesn't know — the family-org bug."""
    _seed_org(org_root)
    paths = OrgPaths(root=org_root)
    manager = AgentDef(
        name="solo_manager",
        team="missing_team",
        role="manager",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by="founder",
        enrolled_at_task=None,
        enrolled_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        system_prompt="You are solo.\n",
        description="Solo",
    )
    (paths.agents_dir / "solo_manager.md").write_text(render_agent_text(manager))


def test_from_runtime_skips_broken_org(tmp_path: Path) -> None:
    """One broken org must not crash daemon startup. Others stay loaded."""
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    _seed_drifted_org(rt.orgs_dir / "broken")
    state = DaemonState.from_runtime(rt, Settings())
    assert "alpha" in state.orgs
    assert "broken" not in state.orgs
    assert "broken" in state.broken_orgs
    assert "missing_team" in state.broken_orgs["broken"]


@pytest.mark.asyncio
async def test_add_org_propagates_consistency_error(tmp_path: Path) -> None:
    """Explicit add (e.g. founder action) must surface the error, not swallow."""
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_drifted_org(rt.orgs_dir / "broken")
    state = DaemonState.from_runtime(rt, Settings())
    # The from_runtime path skipped it — broken_orgs holds the error.
    assert "broken" in state.broken_orgs
    # Direct add_org must surface it.
    with pytest.raises(OrgConsistencyError):
        await state.add_org("broken")


@pytest.mark.asyncio
async def test_add_org_clears_broken_on_success(tmp_path: Path) -> None:
    """After fixing teams.yaml on disk, a successful add_org clears the broken entry."""
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_drifted_org(rt.orgs_dir / "broken")
    state = DaemonState.from_runtime(rt, Settings())
    assert "broken" in state.broken_orgs
    # Founder fixes teams.yaml
    (rt.orgs_dir / "broken" / "org" / "teams.yaml").write_text(
        "teams:\n"
        "  missing_team:\n"
        "    manager: solo_manager\n"
        "    workers: []\n"
    )
    org = await state.add_org("broken")
    assert org.slug == "broken"
    assert "broken" not in state.broken_orgs
    assert "broken" in state.orgs
