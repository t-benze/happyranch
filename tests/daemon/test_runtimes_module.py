from __future__ import annotations

from pathlib import Path

import pytest

from src.daemon import paths as paths_mod
from src.daemon import runtimes as reg
from src.runtime import RuntimeDir


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    paths_mod.ensure_daemon_home()
    return tmp_path / ".happyranch"


def _make_runtime(base: Path, name: str) -> Path:
    runtime_path = base / name
    RuntimeDir.init(runtime_path)
    return runtime_path.resolve()


def test_load_returns_empty_when_file_missing(tmp_home: Path) -> None:
    state = reg.load()
    assert state.active is None
    assert state.registered == []


def test_register_then_activate(tmp_home: Path, tmp_path: Path) -> None:
    rt = _make_runtime(tmp_path, "runtime-a")
    reg.register(rt)
    state = reg.load()
    assert rt in state.registered
    assert state.active == rt


def test_register_rejects_non_runtime_path(tmp_home: Path, tmp_path: Path) -> None:
    bogus = tmp_path / "not-a-runtime"
    bogus.mkdir()
    with pytest.raises(ValueError):
        reg.register(bogus)


def test_register_is_idempotent(tmp_home: Path, tmp_path: Path) -> None:
    rt = _make_runtime(tmp_path, "runtime-a")
    reg.register(rt)
    reg.register(rt)
    state = reg.load()
    assert state.registered.count(rt) == 1


def test_activate_unknown_path_raises(tmp_home: Path, tmp_path: Path) -> None:
    rt = _make_runtime(tmp_path, "runtime-a")
    with pytest.raises(ValueError):
        reg.activate(rt)


def test_activate_after_register_switches(tmp_home: Path, tmp_path: Path) -> None:
    a = _make_runtime(tmp_path, "runtime-a")
    b = _make_runtime(tmp_path, "runtime-b")
    reg.register(a)
    reg.register(b)
    assert reg.load().active == b
    reg.activate(a)
    assert reg.load().active == a


def test_load_returns_empty_when_yaml_is_non_mapping(tmp_home: Path) -> None:
    paths_mod.runtimes_file().write_text("- not_a_mapping\n")
    state = reg.load()
    assert state.active is None
    assert state.registered == []
