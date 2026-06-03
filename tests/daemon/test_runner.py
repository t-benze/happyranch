from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon.runner import enqueue_task
from runtime.daemon.state import DaemonState
from runtime.runtime import RuntimeDir


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")
    (org_root / "workspaces").mkdir()
    (org_root / "kb").mkdir()
    (org_root / "talks").mkdir()


def test_enqueue_task_pushes_tuple(tmp_path: Path) -> None:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / "alpha")
    state = DaemonState.from_runtime(rt, Settings())
    enqueue_task(state, "alpha", "TASK-001")
    assert state.queue._queue.get_nowait() == ("alpha", "TASK-001", None)


def test_enqueue_task_idle_raises(tmp_path: Path) -> None:
    state = DaemonState.idle(Settings())
    with pytest.raises(RuntimeError, match="idle"):
        enqueue_task(state, "alpha", "TASK-001")
