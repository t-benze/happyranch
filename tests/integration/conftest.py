from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from src.daemon import paths as paths_mod
from src.runtime import RuntimeDir


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("OPC_DAEMON_HOME", str(tmp_path / ".opc"))
    return tmp_path / ".opc"


@pytest.fixture
def runtime(tmp_path: Path) -> Path:
    rt = RuntimeDir.init(tmp_path / "runtime")
    return rt.root


def seed_workspace(runtime_root: Path, agent: str) -> Path:
    """Create the minimum workspace layout needed for `_run_agent`.

    The orchestrator's WorkspaceNotInitialized guard only checks the
    start-task SKILL.md marker — we don't need a real CLAUDE.md,
    settings.json, or task_history.md for the fake Claude binary to
    succeed, because `fake_claude.sh` parses task_id/session_id out of the
    prompt instead of running the skill."""
    ws = runtime_root / "workspaces" / agent
    skill_dir = ws / ".claude" / "skills" / "start-task"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# start-task (test stub)\n")
    return ws


@pytest.fixture
def fake_claude(tmp_path: Path) -> Path:
    src = Path(__file__).parent / "fake_claude.sh"
    dst = tmp_path / "fake_claude.sh"
    dst.write_bytes(src.read_bytes())
    dst.chmod(0o755)
    return dst


@pytest.fixture
def fake_plan_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pre-declare FAKE_CLAUDE_PLAN so the daemon inherits it at launch time.

    The test body writes the plan script at this path AFTER the daemon is up,
    but BEFORE the daemon spawns fake_claude (i.e. before submitting a task).
    Setting the env var in the daemon's parent process is a no-op once the
    daemon is running, so this must happen during fixture setup.
    """
    plan_path = tmp_path / "plan.sh"
    monkeypatch.setenv("FAKE_CLAUDE_PLAN", str(plan_path))
    return plan_path


@pytest.fixture
def live_daemon(tmp_home, fake_claude, fake_plan_env, monkeypatch):
    """Start the daemon via scripts/daemon.sh and stop it after the test."""
    monkeypatch.setenv("OPC_CLAUDE_CLI_PATH", str(fake_claude))
    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "daemon.sh"
    subprocess.run([str(script), "start"], check=True)
    # Wait for /health to respond
    deadline = time.time() + 5
    while time.time() < deadline:
        if paths_mod.port_file().exists():
            port = paths_mod.port_file().read_text().strip()
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/api/v1/health", timeout=1.0)
                if r.status_code == 200:
                    yield port
                    break
            except httpx.HTTPError:
                pass
        time.sleep(0.2)
    else:
        raise RuntimeError("daemon failed to start")
    subprocess.run([str(script), "stop"], check=False)
