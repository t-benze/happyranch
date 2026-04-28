from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from src.daemon.org_state import OrgState


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    (org_root / "org" / "agents").mkdir()
    (org_root / "workspaces").mkdir()
    (org_root / "kb").mkdir()
    (org_root / "talks").mkdir()


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
