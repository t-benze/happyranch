from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_agents_returns_tiers(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).get("/api/v1/agents", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "agents" in body
    names = [a["name"] for a in body["agents"]]
    assert "engineering_head" in names


def test_learnings_requires_session_id(tmp_home, app, daemon_state, auth_headers) -> None:
    daemon_state.sessions.set_active("TASK-001", "dev_agent", "sess-1")
    r = TestClient(app).post(
        "/api/v1/agents/dev_agent/learnings",
        json={"text": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 422  # session_id missing


def test_learnings_appends_to_file(
    tmp_home, app, daemon_state, auth_headers, tmp_path,
) -> None:
    daemon_state.sessions.set_active("TASK-001", "dev_agent", "sess-1")
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "learnings.md").write_text("# Learnings: dev_agent\n\n")

    r = TestClient(app).post(
        "/api/v1/agents/dev_agent/learnings",
        json={"session_id": "sess-1", "task_id": "TASK-001", "text": "use uv not pip"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert "use uv not pip" in (workspace / "learnings.md").read_text()


def test_learnings_session_mismatch_409(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    daemon_state.sessions.set_active("TASK-001", "dev_agent", "sess-real")
    r = TestClient(app).post(
        "/api/v1/agents/dev_agent/learnings",
        json={"session_id": "sess-stale", "task_id": "TASK-001", "text": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_learnings_unknown_session_409(
    tmp_home, app, daemon_state, auth_headers, tmp_path,
) -> None:
    """Unregistered (task, agent) pair — reject and do not create/append."""
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    learnings = workspace / "learnings.md"
    learnings.write_text("# Learnings: dev_agent\n\n")

    r = TestClient(app).post(
        "/api/v1/agents/dev_agent/learnings",
        json={"session_id": "fabricated", "task_id": "TASK-NOPE", "text": "should not land"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "unknown_session"
    assert "should not land" not in learnings.read_text()


def test_init_writes_default_agent_yaml_and_creates_dirs(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """init-agent must leave the workspace bootstrapped: agent.yaml present,
    agent-specific folders created (e.g. specs/ for product_manager)."""
    client = TestClient(app)
    with client.stream(
        "POST", "/api/v1/agents/init",
        json={"agent": "product_manager"},
        headers=auth_headers,
    ) as r:
        assert r.status_code == 200
        # Drain the SSE stream so the background generator completes.
        for _ in r.iter_lines():
            pass

    ws = daemon_state.runtime.workspaces_dir / "product_manager"
    assert (ws / "agent.yaml").exists(), "agent.yaml was not created"
    assert (ws / "specs").is_dir(), "product_manager specs/ dir missing"


def test_init_unknown_agent_returns_422(tmp_home, app, auth_headers) -> None:
    """Unrecognized agent name must surface as 422, not crash to 500."""
    r = TestClient(app).post(
        "/api/v1/agents/init",
        json={"agent": "ghost_writer"},
        headers=auth_headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "unknown_agent"
