from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from src.daemon import paths as paths_mod
from src.daemon.app import create_app
from src.daemon.state import DaemonState
from src.runtime import RuntimeDir


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPC_DAEMON_HOME", str(tmp_path / ".opc"))
    paths_mod.ensure_daemon_home()
    paths_mod.ensure_token()
    return tmp_path / ".opc"


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeDir:
    return RuntimeDir.init(tmp_path / "runtime")


@pytest.fixture
def daemon_state(runtime: RuntimeDir) -> DaemonState:
    return DaemonState.from_runtime(runtime, Settings())


@pytest.fixture
def daemon_state_idle() -> DaemonState:
    return DaemonState.idle(Settings())


@pytest.fixture
def app(daemon_state: DaemonState):
    return create_app(daemon_state)


@pytest.fixture
def app_idle(daemon_state_idle: DaemonState):
    return create_app(daemon_state_idle)


@pytest.fixture
def auth_headers() -> dict:
    return {"Authorization": f"Bearer {paths_mod.read_token()}"}
