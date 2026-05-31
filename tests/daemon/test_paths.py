from __future__ import annotations

import stat
from pathlib import Path

import pytest

from src.daemon import paths as paths_mod


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    return tmp_path / ".happyranch"


def test_daemon_home_returns_env_override(tmp_home: Path) -> None:
    assert paths_mod.daemon_home() == tmp_home


def test_daemon_home_creates_directory_when_missing(tmp_home: Path) -> None:
    paths_mod.ensure_daemon_home()
    assert tmp_home.is_dir()


def test_pid_port_token_log_paths(tmp_home: Path) -> None:
    assert paths_mod.pid_file() == tmp_home / "daemon.pid"
    assert paths_mod.port_file() == tmp_home / "daemon.port"
    assert paths_mod.token_file() == tmp_home / "daemon.token"
    assert paths_mod.log_file() == tmp_home / "daemon.log"
    assert paths_mod.runtimes_file() == tmp_home / "runtimes.yaml"


def test_ensure_token_generates_and_returns(tmp_home: Path) -> None:
    paths_mod.ensure_daemon_home()
    token = paths_mod.ensure_token()
    assert isinstance(token, str)
    assert len(token) >= 40
    assert paths_mod.token_file().read_text() == token
    mode = stat.S_IMODE(paths_mod.token_file().stat().st_mode)
    assert mode == 0o600


def test_ensure_token_idempotent(tmp_home: Path) -> None:
    paths_mod.ensure_daemon_home()
    first = paths_mod.ensure_token()
    second = paths_mod.ensure_token()
    assert first == second


def test_read_token_returns_none_when_missing(tmp_home: Path) -> None:
    paths_mod.ensure_daemon_home()
    assert paths_mod.read_token() is None


def test_read_token_returns_existing(tmp_home: Path) -> None:
    paths_mod.ensure_daemon_home()
    token = paths_mod.ensure_token()
    assert paths_mod.read_token() == token


def test_ensure_token_creates_home_when_missing(tmp_home: Path) -> None:
    # Do NOT call ensure_daemon_home first — ensure_token must self-bootstrap.
    assert not tmp_home.exists()
    token = paths_mod.ensure_token()
    assert tmp_home.is_dir()
    assert paths_mod.token_file().read_text() == token
