from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.daemon import paths as paths_mod
from src.daemon.auth import require_token


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    paths_mod.ensure_daemon_home()
    paths_mod.ensure_token()
    return tmp_path / ".happyranch"


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()

    @app.get("/secured")
    def secured(_: None = require_token()) -> dict:
        return {"ok": True}

    return app


def test_request_without_token_rejected(tmp_home: Path, app: FastAPI) -> None:
    r = TestClient(app).get("/secured")
    assert r.status_code == 401


def test_request_with_wrong_token_rejected(tmp_home: Path, app: FastAPI) -> None:
    r = TestClient(app).get(
        "/secured", headers={"Authorization": "Bearer not-the-token"}
    )
    assert r.status_code == 401


def test_request_with_correct_token_allowed(tmp_home: Path, app: FastAPI) -> None:
    token = paths_mod.read_token()
    r = TestClient(app).get(
        "/secured", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
