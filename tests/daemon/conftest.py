from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.daemon import paths as paths_mod
from runtime.daemon.app import create_app
from runtime.daemon.org_state import OrgState
from runtime.daemon.state import DaemonState
from runtime.runtime import RuntimeDir


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HAPPYRANCH_DAEMON_HOME", str(tmp_path / ".happyranch"))
    paths_mod.ensure_daemon_home()
    paths_mod.ensure_token()
    return tmp_path / ".happyranch"


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeDir:
    rt = RuntimeDir.init(tmp_path / "runtime")
    # Seed an "alpha" org with a minimal teams.yaml so engineering_head and
    # content_manager are recognized as team managers by
    # _require_team_manager_auth.
    org_root = rt.orgs_dir / "alpha"
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir()
    (org_root / "org" / "teams.yaml").write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [product_manager, dev_agent, payment_agent, qa_engineer]\n"
        "  content:\n"
        "    manager: content_manager\n"
        "    workers: [content_writer, content_qa, seo_agent]\n"
    )
    return rt


@pytest.fixture
def daemon_state(runtime: RuntimeDir) -> DaemonState:
    return DaemonState.from_runtime(runtime, Settings())


@pytest.fixture
def daemon_state_idle() -> DaemonState:
    return DaemonState.idle(Settings())


@pytest.fixture
def org_state(daemon_state: DaemonState) -> OrgState:
    return daemon_state.orgs["alpha"]


@pytest.fixture
def app(daemon_state: DaemonState):
    return create_app(daemon_state)


@pytest.fixture
def app_idle(daemon_state_idle: DaemonState):
    return create_app(daemon_state_idle)


@pytest.fixture
def auth_headers() -> dict:
    return {"Authorization": f"Bearer {paths_mod.read_token()}"}


@pytest.fixture
def client_with_runtime(tmp_home, daemon_state: DaemonState):
    """TestClient bound to a runtime-backed app, without triggering lifespan.

    Yields (TestClient, OrgState) where OrgState is the seeded "alpha" org.
    Tests use the client to issue HTTP requests and the OrgState to read/write
    the per-org DB and SessionTracker directly. The DaemonState is reachable
    via ``client.app.state.daemon`` if a test needs the global queue.

    Lifespan is NOT triggered because the TestClient is used without `with` —
    this keeps the worker pool dormant so tests don't race against background
    task execution.
    """
    from fastapi.testclient import TestClient
    app = create_app(daemon_state)
    client = TestClient(app)
    # Attach auth token to every request automatically.
    client.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
    yield client, daemon_state.orgs["alpha"]


@pytest.fixture
def client(tmp_home, daemon_state: DaemonState):
    """TestClient bound to a runtime-backed app (no lifespan, auth pre-attached).

    Returns the TestClient directly (not a tuple). Suitable for tests that
    only need HTTP access and not direct DB/state manipulation.
    """
    from fastapi.testclient import TestClient
    app = create_app(daemon_state)
    tc = TestClient(app)
    tc.headers.update({"Authorization": f"Bearer {paths_mod.read_token()}"})
    return tc


def open_talk_for(client, agent_name: str, slug: str = "alpha") -> str:
    """POST /talks for *agent_name* under *slug* and return the talk_id.

    Convenience helper for tests that need an open talk without caring about
    the full talk lifecycle. Raises AssertionError on non-200 responses.
    """
    from fastapi.testclient import TestClient as _TC  # noqa: F401 (type-check only)
    resp = client.post(f"/api/v1/orgs/{slug}/talks", json={"agent_name": agent_name})
    assert resp.status_code == 200, f"open_talk_for({agent_name!r}) failed: {resp.text}"
    return resp.json()["talk_id"]
