from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Settings
from src.daemon.routes._org_dep import OrgDep
from src.daemon.state import DaemonState
from src.runtime import RuntimeDir


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")


def _make_app(state: DaemonState) -> FastAPI:
    app = FastAPI()
    app.state.daemon = state

    @app.get("/api/v1/orgs/{slug}/echo")
    def echo(slug: str, org: OrgDep) -> dict:
        return {"slug": org.slug}

    return app


def test_org_dep_resolves(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(_make_app(state))
    r = client.get("/api/v1/orgs/alpha/echo")
    assert r.status_code == 200
    assert r.json() == {"slug": "alpha"}


def test_org_dep_unknown_404(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    client = TestClient(_make_app(state))
    r = client.get("/api/v1/orgs/nope/echo")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_org"
    assert r.json()["detail"]["available"] == ["alpha"]


def test_org_dep_idle_409(tmp_path: Path) -> None:
    state = DaemonState.idle(Settings())
    client = TestClient(_make_app(state))
    r = client.get("/api/v1/orgs/alpha/echo")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "no_active_runtime"
