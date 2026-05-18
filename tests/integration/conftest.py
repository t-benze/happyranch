from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from src.daemon import paths as paths_mod
from src.daemon import runtimes as runtimes_mod
from src.runtime import RuntimeDir


@pytest.fixture(autouse=True)
def _reset_lark_token_cache():
    """Reset the lark-oapi token cache between tests.

    The lark SDK assigns ``TokenManager.cache = LocalCache.instance()`` at
    class-definition time — a process-wide singleton dict. Without clearing
    it, a test that sends a Feishu message (caching a token against
    fake-server-port-A) contaminates the next Feishu test (which uses
    fake-server-port-B) — the second test reuses the stale cached token,
    skips the token-fetch, and its fake server records zero token_calls even
    though the message send succeeds.
    """
    from lark_oapi.core.token.manager import TokenManager
    TokenManager.cache.cache.clear()
    yield
    TokenManager.cache.cache.clear()


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GRASSLAND_DAEMON_HOME", str(tmp_path / ".grassland"))
    return tmp_path / ".grassland"


DEFAULT_TEST_SLUG = "test"


@pytest.fixture
def runtime_container(tmp_path: Path) -> Path:
    """Create a fresh multi-org runtime container."""
    return RuntimeDir.init(tmp_path / "runtime").root


@pytest.fixture
def runtime(runtime_container: Path) -> Path:
    """Materialize a default org under <container>/orgs/test/.

    Returns the ORG ROOT so existing tests that reference
    ``runtime/org/agents`` and ``runtime/workspaces/<agent>`` keep working
    against the multi-org layout. The daemon ``register`` call uses the
    container path (see ``live_daemon``), not this org root.
    """
    org_root = runtime_container / "orgs" / DEFAULT_TEST_SLUG
    (org_root / "org" / "agents").mkdir(parents=True, exist_ok=True)
    (org_root / "workspaces").mkdir(parents=True, exist_ok=True)
    (org_root / "kb").mkdir(parents=True, exist_ok=True)
    (org_root / "talks").mkdir(parents=True, exist_ok=True)
    # Seed engineering + content teams so /tasks (which defaults to team=engineering)
    # and the content-team end-to-end flows have valid managers to dispatch to.
    (org_root / "org" / "teams.yaml").write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, payment_agent, qa_engineer]\n"
        "  content:\n"
        "    manager: content_manager\n"
        "    workers: [content_writer, content_qa, seo_agent]\n"
    )
    return org_root


def seed_workspace(org_root: Path, agent: str) -> Path:
    """Create the minimum workspace layout needed for `_run_agent`.

    The orchestrator's WorkspaceNotInitialized guard only checks the
    start-task SKILL.md marker — we don't need a real CLAUDE.md,
    settings.json, or task_history.md for the fake Claude binary to
    succeed, because `fake_claude.sh` parses task_id/session_id out of the
    prompt instead of running the skill."""
    ws = org_root / "workspaces" / agent
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
def fake_codex(tmp_path: Path) -> Path:
    src = Path(__file__).parent / "fake_codex.sh"
    dst = tmp_path / "fake_codex.sh"
    dst.write_bytes(src.read_bytes())
    dst.chmod(0o755)
    return dst


@pytest.fixture
def fake_claude_plan_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
def fake_codex_plan_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pre-declare FAKE_CODEX_PLAN so the daemon inherits it at launch time."""
    plan_path = tmp_path / "plan_codex.sh"
    monkeypatch.setenv("FAKE_CODEX_PLAN", str(plan_path))
    return plan_path


@pytest.fixture
def fake_claude_thread_plan_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pre-declare FAKE_CLAUDE_THREAD_PLAN so the daemon inherits it.

    Same shape as fake_claude_plan_env but routes to a separate script when
    the prompt is a thread invocation (detected by `Your invocation_token:`).
    """
    plan_path = tmp_path / "thread_plan.sh"
    monkeypatch.setenv("FAKE_CLAUDE_THREAD_PLAN", str(plan_path))
    return plan_path


@pytest.fixture
def fake_plan_env(fake_claude_plan_env: Path) -> Path:
    """Backward-compatible alias for the Claude plan env fixture."""
    return fake_claude_plan_env


@pytest.fixture
def live_daemon(
    tmp_home,
    runtime_container,
    runtime,
    fake_claude,
    fake_codex,
    fake_claude_plan_env,
    fake_codex_plan_env,
    fake_claude_thread_plan_env,
    monkeypatch,
):
    """Start the daemon via scripts/daemon.sh and stop it after the test."""
    monkeypatch.setenv("GRASSLAND_CLAUDE_CLI_PATH", str(fake_claude))
    monkeypatch.setenv("GRASSLAND_CODEX_CLI_PATH", str(fake_codex))
    from src.daemon import runtimes as runtimes_mod

    runtimes_mod.register(runtime_container)
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


@pytest.fixture
def live_daemon_idle(
    tmp_home,
    fake_claude,
    fake_codex,
    fake_claude_plan_env,
    fake_codex_plan_env,
    monkeypatch,
):
    """Start the daemon with no active runtime registered yet."""
    monkeypatch.setenv("GRASSLAND_CLAUDE_CLI_PATH", str(fake_claude))
    monkeypatch.setenv("GRASSLAND_CODEX_CLI_PATH", str(fake_codex))
    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "daemon.sh"
    subprocess.run([str(script), "start"], check=True)
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
