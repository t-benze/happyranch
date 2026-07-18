from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon.org_state import OrgState
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.org_validation import OrgConsistencyError


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    (org_root / "org" / "agents").mkdir()
    (org_root / "workspaces").mkdir()
    (org_root / "kb").mkdir()


def test_org_state_load_opens_db_and_teams(tmp_path: Path) -> None:
    org_root = tmp_path / "rt" / "orgs" / "alpha"
    _seed_org(org_root)
    settings = Settings()
    org = OrgState.load(slug="alpha", root=org_root, settings=settings)
    assert org.slug == "alpha"
    assert org.root == org_root
    assert org.db is not None
    assert org.teams is not None
    org.close()


def test_org_state_two_orgs_independent_dbs(tmp_path: Path) -> None:
    """Two OrgStates point at distinct DB files — writes don't cross over."""
    rt = tmp_path / "rt"
    a_root = rt / "orgs" / "alpha"
    b_root = rt / "orgs" / "beta"
    _seed_org(a_root)
    _seed_org(b_root)
    settings = Settings()
    org_a = OrgState.load(slug="alpha", root=a_root, settings=settings)
    org_b = OrgState.load(slug="beta", root=b_root, settings=settings)
    a_id = org_a.db.next_task_id()
    b_id = org_b.db.next_task_id()
    assert a_id == "TASK-001"
    assert b_id == "TASK-001"  # independent counters per org
    assert org_a.db.path != org_b.db.path
    org_a.close()
    org_b.close()


def test_org_state_close_releases_db(tmp_path: Path) -> None:
    org_root = tmp_path / "rt" / "orgs" / "alpha"
    _seed_org(org_root)
    settings = Settings()
    org = OrgState.load(slug="alpha", root=org_root, settings=settings)
    org.close()
    with pytest.raises(Exception):
        org.db.next_task_id()


def test_org_state_load_refuses_on_team_drift(tmp_path: Path) -> None:
    """OrgState.load must raise when an active agent declares an unknown team."""
    org_root = tmp_path / "rt" / "orgs" / "family"
    _seed_org(org_root)
    paths = OrgPaths(root=org_root)
    manager = AgentDef(
        name="family_manager",
        team="family_operations",
        role="manager",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by="founder",
        enrolled_at_task=None,
        enrolled_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        system_prompt="You are the Family Manager.\n",
        description="Manages family ops",
    )
    (paths.agents_dir / "family_manager.md").write_text(render_agent_text(manager))

    with pytest.raises(OrgConsistencyError) as exc_info:
        OrgState.load(slug="family", root=org_root, settings=Settings())
    assert "family_operations" in str(exc_info.value)


# ── THR-107: legacy per-org executor_profiles block no longer registers ──

def _make_org_config(org_root: Path, body: str) -> None:
    (org_root / "org" / "config.yaml").parent.mkdir(parents=True, exist_ok=True)
    (org_root / "org" / "config.yaml").write_text(body)


def test_org_state_load_does_not_register_legacy_executor_profiles(
    tmp_path: Path, monkeypatch,
) -> None:
    """THR-107: the per-org executor_profiles config surface is removed.
    A legacy block in org/config.yaml must NOT register anything into the
    process-wide registry via the org_state startup path. (The one-shot
    startup migration lifts it into the machine-global runtime store
    instead — see DaemonState.from_runtime tests.)"""
    from runtime.orchestrator.executor_registry import get_registry, reset_registry
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    reset_registry()

    org_root = tmp_path / "rt" / "orgs" / "testorg"
    _seed_org(org_root)

    _make_org_config(org_root, """
executor_profiles:
  openclaw:
    command: echo
    adapter: pi
    argv_template:
      - echo
      - "{prompt}"
""")

    org = OrgState.load(slug="testorg", root=org_root, settings=Settings())
    assert org.slug == "testorg"

    # The legacy block must NOT reach the process registry.
    assert not get_registry().is_registered("openclaw")
    org.close()
    reset_registry()


def test_org_state_load_fails_when_custom_profile_unregistered_and_agent_declares_it(
    tmp_path: Path, monkeypatch,
) -> None:
    """An active agent declaring a custom executor that is NOT registered
    (THR-107: e.g. a legacy config block that is no longer parsed, here a
    malformed one that cannot even be lifted) must fail validation — the
    agent depends on an unregistered profile."""
    from runtime.orchestrator.executor_registry import reset_registry
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    reset_registry()

    org_root = tmp_path / "rt" / "orgs" / "badorg"
    _seed_org(org_root)
    paths = OrgPaths(root=org_root)

    # Write a MALFORMED org/config.yaml so profiles are NOT registered.
    _make_org_config(org_root, "executor_profiles: [1, 2, 3]\n")

    # Write an active agent file that declares an unregistered custom executor.
    agent = AgentDef(
        name="dev_agent",
        team="engineering",
        role="worker",
        executor="openclaw",
        allow_rules=(),
        repos={},
        enrolled_by="founder",
        enrolled_at_task=None,
        enrolled_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
        system_prompt="You are the dev agent.\n",
        description="Dev agent",
    )
    (org_root / "org" / "teams.yaml").write_text(
        "teams:\n  engineering:\n    manager: engineering_manager\n"
    )
    (paths.agents_dir / "dev_agent.md").write_text(render_agent_text(agent))

    # Must fail validation because openclaw is not a registered profile.
    with pytest.raises(ValueError, match="registered profile"):
        OrgState.load(slug="badorg", root=org_root, settings=Settings())
