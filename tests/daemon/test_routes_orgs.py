from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.config import Settings
from src.daemon.app import create_app
from src.daemon.state import DaemonState
from src.runtime import RuntimeDir


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")


@pytest.fixture
def auth(monkeypatch, tmp_path):
    home = tmp_path / "opc-home"
    home.mkdir()
    monkeypatch.setenv("OPC_DAEMON_HOME", str(home))
    from src.daemon import paths
    token = paths.ensure_token()
    return {"Authorization": f"Bearer {token}"}


def test_list_orgs_returns_loaded(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    _seed_org(rt.orgs_dir / "beta")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.get("/api/v1/orgs", headers=auth)
    assert r.status_code == 200
    assert sorted(o["slug"] for o in r.json()["orgs"]) == ["alpha", "beta"]


def test_init_org_creates_skeleton_and_loads(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "alpha"})
    assert r.status_code == 200
    assert (rt.orgs_dir / "alpha" / "org" / "teams.yaml").is_file()
    assert "alpha" in state.orgs


def test_init_org_invalid_slug_400(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "Bad Slug"})
    assert r.status_code == 400


def test_init_org_idempotent_returns_409(tmp_path: Path, auth) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(create_app(state))
    r = client.post("/api/v1/orgs", headers=auth, json={"slug": "alpha"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "org_exists"
