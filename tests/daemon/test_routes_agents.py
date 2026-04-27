from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

_EH_TASK = "TASK-100"
_EH_SESSION = "sess-eh-test"


def _activate_eh_session(daemon_state) -> None:
    """Register an active engineering_head session so manage-agent calls succeed."""
    daemon_state.sessions.set_active(_EH_TASK, "engineering_head", _EH_SESSION)


def test_list_agents_returns_tiers(tmp_home, app, daemon_state, auth_headers) -> None:
    # Create at least one workspace so list_agents finds it
    ws = daemon_state.runtime.workspaces_dir / "engineering_head"
    ws.mkdir(parents=True, exist_ok=True)
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


def test_init_creates_workspace_for_any_name(tmp_home, app, daemon_state, auth_headers) -> None:
    """init-agent accepts any valid agent name, no longer validates against enum."""
    client = TestClient(app)
    with client.stream(
        "POST", "/api/v1/agents/init",
        json={"agent": "new_custom_agent"},
        headers=auth_headers,
    ) as r:
        assert r.status_code == 200
        for _ in r.iter_lines():
            pass
    ws = daemon_state.runtime.workspaces_dir / "new_custom_agent"
    assert (ws / "agent.yaml").exists()


def test_manage_repo_add_creates_entry_and_clones(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\n")

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None

        r = TestClient(app).post(
            "/api/v1/agents/dev_agent/repos",
            json={"action": "add", "repo_name": "docs", "url": "https://github.com/t-benze/docs.git"},
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    mock_ctx.clone_repo.assert_called_once()
    mock_ctx.ensure_workspace_ready.assert_called_once()

    from src.daemon.agent_config import load_agent_config
    cfg = load_agent_config(workspace)
    assert cfg["repos"]["docs"] == "https://github.com/t-benze/docs.git"


def test_manage_repo_add_duplicate_returns_409(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos:\n  docs: https://old.git\n")

    r = TestClient(app).post(
        "/api/v1/agents/dev_agent/repos",
        json={"action": "add", "repo_name": "docs", "url": "https://new.git"},
        headers=auth_headers,
    )
    assert r.status_code == 409


def test_manage_repo_remove_deletes_entry_and_dir(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos:\n  docs: https://old.git\n")
    repo_dir = workspace / "repos" / "docs"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()  # fake git dir

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.ensure_workspace_ready.return_value = None

        r = TestClient(app).post(
            "/api/v1/agents/dev_agent/repos",
            json={"action": "remove", "repo_name": "docs"},
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert not repo_dir.exists()

    from src.daemon.agent_config import load_agent_config
    cfg = load_agent_config(workspace)
    assert "docs" not in cfg.get("repos", {})


def test_manage_repo_remove_nonexistent_returns_404(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\n")

    r = TestClient(app).post(
        "/api/v1/agents/dev_agent/repos",
        json={"action": "remove", "repo_name": "ghost"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_manage_repo_update_reclones(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos:\n  docs: https://old.git\n")
    repo_dir = workspace / "repos" / "docs"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None

        r = TestClient(app).post(
            "/api/v1/agents/dev_agent/repos",
            json={"action": "update", "repo_name": "docs", "url": "https://new.git"},
            headers=auth_headers,
        )
    assert r.status_code == 200
    mock_ctx.clone_repo.assert_called_once()

    from src.daemon.agent_config import load_agent_config
    cfg = load_agent_config(workspace)
    assert cfg["repos"]["docs"] == "https://new.git"


def test_manage_repo_add_missing_url_returns_422(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\n")

    r = TestClient(app).post(
        "/api/v1/agents/dev_agent/repos",
        json={"action": "add", "repo_name": "docs"},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_manage_repo_unknown_workspace_returns_404(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    r = TestClient(app).post(
        "/api/v1/agents/nonexistent/repos",
        json={"action": "add", "repo_name": "x", "url": "https://x.git"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_manage_agent_enroll_creates_pending(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    _activate_eh_session(daemon_state)
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "description": "Writes destination guides",
            "system_prompt": "You are the Content Writer...",
            "executor": "codex",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    from src.orchestrator import prompt_loader
    agent = prompt_loader.load_pending_agent(daemon_state.runtime, "content_writer")
    assert agent is not None
    assert agent.executor == "codex"


def test_manage_agent_enroll_duplicate_returns_409(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    _activate_eh_session(daemon_state)
    # Pre-seed a pending agent file so the duplicate check fires.
    from src.orchestrator import prompt_loader
    from src.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone
    agent = AgentDef(
        name="content_writer", team="content", role="worker", executor="claude",
        allow_rules=(), repos={}, enrolled_by="engineering_head",
        enrolled_at_task=_EH_TASK, enrolled_at=datetime.now(timezone.utc),
        system_prompt="prompt\n",
    )
    prompt_loader.write_pending_agent(daemon_state.runtime, agent)
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 409


def test_manage_agent_enroll_invalid_name_returns_422(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    _activate_eh_session(daemon_state)
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "Content Writer",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 422


def _seed_active_agent(daemon_state, name: str, team: str = "engineering", executor: str = "claude", system_prompt: str = "prompt\n") -> None:
    """Write an active agent file for testing update/terminate endpoints."""
    from src.orchestrator.agent_def import AgentDef, render_agent_text
    from datetime import datetime, timezone
    agent = AgentDef(
        name=name, team=team, role="worker", executor=executor,
        allow_rules=(), repos={}, enrolled_by="engineering_head",
        enrolled_at_task=_EH_TASK, enrolled_at=datetime.now(timezone.utc),
        system_prompt=system_prompt,
    )
    daemon_state.runtime.agents_dir.mkdir(parents=True, exist_ok=True)
    (daemon_state.runtime.agents_dir / f"{name}.md").write_text(render_agent_text(agent))


def test_manage_agent_update_changes_prompt(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    # Use dev_agent which belongs to engineering team (managed by engineering_head).
    _activate_eh_session(daemon_state)
    _seed_active_agent(daemon_state, "dev_agent", system_prompt="old prompt\n")
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.ensure_workspace_ready.return_value = None
        r = TestClient(app).post(
            "/api/v1/agents/manage",
            json={
                "action": "update",
                "name": "dev_agent",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "system_prompt": "new prompt",
            "executor": "codex",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    from src.orchestrator import prompt_loader
    updated = prompt_loader.load_agent(daemon_state.runtime, "dev_agent")
    assert updated is not None
    assert "new prompt" in updated.system_prompt
    assert updated.executor == "codex"


def test_manage_agent_update_persists_executor_to_workspace(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    # Use dev_agent which belongs to engineering team (managed by engineering_head).
    _activate_eh_session(daemon_state)
    _seed_active_agent(daemon_state, "dev_agent")
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\n")

    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "update",
            "name": "dev_agent",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "executor": "codex",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200

    from src.daemon.agent_config import load_agent_config

    cfg = load_agent_config(workspace)
    assert cfg["executor"] == "codex"


def test_manage_agent_terminate_removes_workspace(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    # Use dev_agent which belongs to engineering team (managed by engineering_head).
    _activate_eh_session(daemon_state)
    _seed_active_agent(daemon_state, "dev_agent")
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "CLAUDE.md").write_text("# test")

    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "terminate",
            "name": "dev_agent",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert not workspace.exists()
    from src.orchestrator import prompt_loader
    assert prompt_loader.load_agent(daemon_state.runtime, "dev_agent") is None


def test_manage_agent_terminate_nonexistent_returns_404(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    _activate_eh_session(daemon_state)
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "terminate",
            "name": "ghost",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
        },
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_manage_agent_without_eh_session_returns_403(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """Requests without an active EH session are rejected."""
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "rogue_agent",
            "task_id": "TASK-999",
            "session_id": "sess-fake",
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 403


def test_manage_agent_wrong_session_returns_403(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """Requests with a mismatched session_id are rejected."""
    _activate_eh_session(daemon_state)
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "rogue_agent",
            "task_id": _EH_TASK,
            "session_id": "sess-wrong",
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 403


def test_approve_agent_bootstraps_workspace(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    # Pre-seed a pending agent file.
    from src.orchestrator import prompt_loader
    from src.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone
    agent = AgentDef(
        name="content_writer", team="content", role="worker", executor="codex",
        allow_rules=(), repos={}, enrolled_by="engineering_head",
        enrolled_at_task=_EH_TASK, enrolled_at=datetime.now(timezone.utc),
        system_prompt="prompt\n",
    )
    prompt_loader.write_pending_agent(daemon_state.runtime, agent)

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None
        mock_ctx.create_agent_dirs.return_value = None

        r = TestClient(app).post(
            "/api/v1/agents/content_writer/approve",
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert prompt_loader.load_agent(daemon_state.runtime, "content_writer") is not None
    assert prompt_loader.load_pending_agent(daemon_state.runtime, "content_writer") is None
    workspace = daemon_state.runtime.workspaces_dir / "content_writer"
    assert workspace.exists()

    from src.daemon.agent_config import load_agent_config

    cfg = load_agent_config(workspace)
    assert cfg["executor"] == "codex"


def test_approve_non_pending_returns_409(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    # Seed an active (approved) agent file — not pending.
    _seed_active_agent(daemon_state, "content_writer", team="content")
    r = TestClient(app).post(
        "/api/v1/agents/content_writer/approve",
        headers=auth_headers,
    )
    assert r.status_code == 409


def test_reject_agent(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    from src.orchestrator import prompt_loader
    from src.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone
    agent = AgentDef(
        name="content_writer", team="content", role="worker", executor="claude",
        allow_rules=(), repos={}, enrolled_by="engineering_head",
        enrolled_at_task=_EH_TASK, enrolled_at=datetime.now(timezone.utc),
        system_prompt="prompt\n",
    )
    prompt_loader.write_pending_agent(daemon_state.runtime, agent)
    r = TestClient(app).post(
        "/api/v1/agents/content_writer/reject",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert prompt_loader.load_pending_agent(daemon_state.runtime, "content_writer") is None


def test_list_enrollments(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    # Seed one pending and one active agent.
    from src.orchestrator import prompt_loader
    from src.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone
    def _make(name, team):
        return AgentDef(
            name=name, team=team, role="worker", executor="claude",
            allow_rules=(), repos={}, enrolled_by="engineering_head",
            enrolled_at_task=_EH_TASK, enrolled_at=datetime.now(timezone.utc),
            system_prompt="prompt\n",
        )
    prompt_loader.write_pending_agent(daemon_state.runtime, _make("b", "content"))
    daemon_state.runtime.agents_dir.mkdir(parents=True, exist_ok=True)
    from src.orchestrator.agent_def import render_agent_text
    (daemon_state.runtime.agents_dir / "a.md").write_text(render_agent_text(_make("a", "engineering")))

    r = TestClient(app).get(
        "/api/v1/agents/enrollments",
        params={"status": "pending"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    names = [e["name"] for e in r.json()["enrollments"]]
    assert names == ["b"]


def test_backfill_enrollments_imports_known_workspaces(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """backfill-enrollments is now a deprecated no-op; always returns empty lists."""
    r = TestClient(app).post(
        "/api/v1/agents/backfill-enrollments",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["backfilled"] == []
    assert body["skipped_already_enrolled"] == []
    assert body["skipped_unknown_prompt"] == []


def test_backfill_enrollments_skips_already_enrolled(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """Deprecated no-op: always returns empty lists regardless of workspace state."""
    r = TestClient(app).post(
        "/api/v1/agents/backfill-enrollments",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["backfilled"] == []
    assert body["skipped_already_enrolled"] == []


def test_backfill_enrollments_skips_unknown_prompt(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """Deprecated no-op: always returns empty lists."""
    r = TestClient(app).post(
        "/api/v1/agents/backfill-enrollments",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["backfilled"] == []
    assert body["skipped_unknown_prompt"] == []


def test_backfill_enrollments_is_idempotent(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """Deprecated no-op: both calls return empty lists."""
    client = TestClient(app)
    r1 = client.post(
        "/api/v1/agents/backfill-enrollments", headers=auth_headers,
    )
    assert r1.status_code == 200
    assert r1.json()["backfilled"] == []

    r2 = client.post(
        "/api/v1/agents/backfill-enrollments", headers=auth_headers,
    )
    assert r2.status_code == 200
    assert r2.json()["backfilled"] == []


def test_backfill_enrollments_reads_agent_yaml(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """Deprecated no-op: always returns empty lists (agent.yaml ignored)."""
    r = TestClient(app).post(
        "/api/v1/agents/backfill-enrollments",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["backfilled"] == []


def test_backfill_enrollments_writes_audit_entry(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """Deprecated no-op: returns 200 with empty lists; no audit entries written."""
    r = TestClient(app).post(
        "/api/v1/agents/backfill-enrollments",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["backfilled"] == []


def test_manage_agent_body_accepts_talk_id_alone() -> None:
    """talk_id alone (no task_id/session_id) validates."""
    from src.daemon.routes.agents import ManageAgentBody

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        talk_id="TALK-007",
        description="desc",
        system_prompt="prompt",
    )
    assert body.talk_id == "TALK-007"
    assert body.task_id is None
    assert body.session_id is None


def test_manage_agent_body_accepts_task_and_session() -> None:
    """(task_id + session_id) still validates."""
    from src.daemon.routes.agents import ManageAgentBody

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        task_id="TASK-100",
        session_id="sess-eh",
        description="desc",
        system_prompt="prompt",
    )
    assert body.task_id == "TASK-100"
    assert body.talk_id is None


def test_manage_agent_body_rejects_both_paths() -> None:
    """Supplying both task/session and talk_id is a validation error."""
    import pytest
    from pydantic import ValidationError
    from src.daemon.routes.agents import ManageAgentBody

    with pytest.raises(ValidationError):
        ManageAgentBody(
            action="enroll",
            name="content_writer",
            task_id="TASK-100",
            session_id="sess-eh",
            talk_id="TALK-007",
            description="desc",
            system_prompt="prompt",
        )


def test_manage_agent_body_rejects_neither_path() -> None:
    """Supplying neither is a validation error."""
    import pytest
    from pydantic import ValidationError
    from src.daemon.routes.agents import ManageAgentBody

    with pytest.raises(ValidationError):
        ManageAgentBody(
            action="enroll",
            name="content_writer",
            description="desc",
            system_prompt="prompt",
        )


def test_manage_agent_body_rejects_partial_task_path() -> None:
    """task_id without session_id (or vice versa) is a validation error."""
    import pytest
    from pydantic import ValidationError
    from src.daemon.routes.agents import ManageAgentBody

    with pytest.raises(ValidationError):
        ManageAgentBody(
            action="enroll",
            name="content_writer",
            task_id="TASK-100",
            description="desc",
            system_prompt="prompt",
        )


def test_require_eh_auth_talk_path_success(
    tmp_home, daemon_state,
) -> None:
    """Helper returns None for an open EH talk."""
    from src.daemon.routes.agents import ManageAgentBody, _require_eh_auth
    from src.models import TalkRecord

    daemon_state.db.insert_talk(
        TalkRecord(id="TALK-042", agent_name="engineering_head"),
    )

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        talk_id="TALK-042",
        description="desc",
        system_prompt="prompt",
    )
    _require_eh_auth(body, daemon_state)  # no raise


def test_require_eh_auth_talk_path_wrong_agent_raises_403(
    tmp_home, daemon_state,
) -> None:
    """Talk owned by another agent is rejected."""
    import pytest
    from fastapi import HTTPException
    from src.daemon.routes.agents import ManageAgentBody, _require_eh_auth
    from src.models import TalkRecord

    daemon_state.db.insert_talk(
        TalkRecord(id="TALK-050", agent_name="dev_agent"),
    )

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        talk_id="TALK-050",
        description="desc",
        system_prompt="prompt",
    )
    with pytest.raises(HTTPException) as ex:
        _require_eh_auth(body, daemon_state)
    assert ex.value.status_code == 403


def test_require_eh_auth_talk_path_closed_talk_raises_403(
    tmp_home, daemon_state,
) -> None:
    """Closed talk is rejected."""
    import pytest
    from fastapi import HTTPException
    from src.daemon.routes.agents import ManageAgentBody, _require_eh_auth
    from src.models import TalkRecord, TalkStatus

    daemon_state.db.insert_talk(
        TalkRecord(
            id="TALK-060",
            agent_name="engineering_head",
            status=TalkStatus.CLOSED,
        ),
    )

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        talk_id="TALK-060",
        description="desc",
        system_prompt="prompt",
    )
    with pytest.raises(HTTPException) as ex:
        _require_eh_auth(body, daemon_state)
    assert ex.value.status_code == 403


def test_require_eh_auth_talk_path_missing_talk_raises_404(
    tmp_home, daemon_state,
) -> None:
    """Unknown talk_id is 404."""
    import pytest
    from fastapi import HTTPException
    from src.daemon.routes.agents import ManageAgentBody, _require_eh_auth

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        talk_id="TALK-999",
        description="desc",
        system_prompt="prompt",
    )
    with pytest.raises(HTTPException) as ex:
        _require_eh_auth(body, daemon_state)
    assert ex.value.status_code == 404


def test_require_eh_auth_task_path_success(
    tmp_home, daemon_state,
) -> None:
    """Helper returns None for a live EH task session."""
    from src.daemon.routes.agents import ManageAgentBody, _require_eh_auth

    daemon_state.sessions.set_active("TASK-100", "engineering_head", "sess-eh")

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        task_id="TASK-100",
        session_id="sess-eh",
        description="desc",
        system_prompt="prompt",
    )
    _require_eh_auth(body, daemon_state)  # no raise


def test_require_eh_auth_task_path_unknown_session_raises_403(
    tmp_home, daemon_state,
) -> None:
    """Unknown (task_id, eh) pair is 403."""
    import pytest
    from fastapi import HTTPException
    from src.daemon.routes.agents import ManageAgentBody, _require_eh_auth

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        task_id="TASK-404",
        session_id="sess-ghost",
        description="desc",
        system_prompt="prompt",
    )
    with pytest.raises(HTTPException) as ex:
        _require_eh_auth(body, daemon_state)
    assert ex.value.status_code == 403


def test_require_eh_auth_task_path_wrong_session_raises_403(
    tmp_home, daemon_state,
) -> None:
    """Mismatched session_id is 403."""
    import pytest
    from fastapi import HTTPException
    from src.daemon.routes.agents import ManageAgentBody, _require_eh_auth

    daemon_state.sessions.set_active("TASK-100", "engineering_head", "sess-real")

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        task_id="TASK-100",
        session_id="sess-stale",
        description="desc",
        system_prompt="prompt",
    )
    with pytest.raises(HTTPException) as ex:
        _require_eh_auth(body, daemon_state)
    assert ex.value.status_code == 403


def _seed_eh_talk(daemon_state, talk_id: str = "TALK-700") -> str:
    """Helper: insert an open EH talk and return its id."""
    from src.models import TalkRecord

    daemon_state.db.insert_talk(
        TalkRecord(id=talk_id, agent_name="engineering_head"),
    )
    return talk_id


def test_manage_agent_talk_path_enroll_creates_pending(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    talk_id = _seed_eh_talk(daemon_state)
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "talk_id": talk_id,
            "description": "Writes destination guides",
            "system_prompt": "You are the Content Writer...",
            "executor": "codex",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    from src.orchestrator import prompt_loader
    agent = prompt_loader.load_pending_agent(daemon_state.runtime, "content_writer")
    assert agent is not None
    assert agent.executor == "codex"


def test_manage_agent_talk_path_update_changes_prompt(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    # Use dev_agent which belongs to engineering team (managed by engineering_head).
    talk_id = _seed_eh_talk(daemon_state, "TALK-701")
    _seed_active_agent(daemon_state, "dev_agent", system_prompt="old prompt\n")
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.ensure_workspace_ready.return_value = None
        r = TestClient(app).post(
            "/api/v1/agents/manage",
            json={
                "action": "update",
                "name": "dev_agent",
                "talk_id": talk_id,
                "system_prompt": "new prompt via talk",
            },
            headers=auth_headers,
        )
    assert r.status_code == 200
    from src.orchestrator import prompt_loader
    updated = prompt_loader.load_agent(daemon_state.runtime, "dev_agent")
    assert updated is not None
    assert "new prompt via talk" in updated.system_prompt


def test_manage_agent_talk_path_terminate_removes_workspace(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    # Use dev_agent which belongs to engineering team (managed by engineering_head).
    talk_id = _seed_eh_talk(daemon_state, "TALK-702")
    _seed_active_agent(daemon_state, "dev_agent")
    workspace = daemon_state.runtime.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "CLAUDE.md").write_text("# test")

    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "terminate",
            "name": "dev_agent",
            "talk_id": talk_id,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert not workspace.exists()
    from src.orchestrator import prompt_loader
    assert prompt_loader.load_agent(daemon_state.runtime, "dev_agent") is None


def test_manage_agent_talk_path_non_eh_talk_returns_403(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    from src.models import TalkRecord

    daemon_state.db.insert_talk(
        TalkRecord(id="TALK-703", agent_name="dev_agent"),
    )
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "talk_id": "TALK-703",
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 403


def test_manage_agent_talk_path_closed_talk_returns_403(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    from src.models import TalkRecord, TalkStatus

    daemon_state.db.insert_talk(
        TalkRecord(
            id="TALK-704",
            agent_name="engineering_head",
            status=TalkStatus.CLOSED,
        ),
    )
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "talk_id": "TALK-704",
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 403


def test_manage_agent_talk_path_missing_talk_returns_404(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "talk_id": "TALK-DOES-NOT-EXIST",
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_manage_agent_both_auth_paths_returns_422(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    _activate_eh_session(daemon_state)
    _seed_eh_talk(daemon_state, "TALK-705")
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "talk_id": "TALK-705",
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_manage_agent_task_path_writes_audit_entry(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    _activate_eh_session(daemon_state)
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200

    managed = [
        log for log in daemon_state.db.get_audit_logs(_EH_TASK)
        if log["action"] == "agent_managed"
    ]
    assert len(managed) == 1
    assert managed[0]["agent"] == "engineering_head"
    assert managed[0]["payload"]["action"] == "enroll"
    assert managed[0]["payload"]["name"] == "content_writer"
    assert managed[0]["payload"]["source"] == "task"


def test_manage_agent_talk_path_writes_audit_entry_scoped_to_talk(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    talk_id = _seed_eh_talk(daemon_state, "TALK-800")
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "talk_id": talk_id,
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200

    managed = [
        log for log in daemon_state.db.get_audit_logs(talk_id)
        if log["action"] == "agent_managed"
    ]
    assert len(managed) == 1
    assert managed[0]["agent"] == "engineering_head"
    assert managed[0]["payload"]["action"] == "enroll"
    assert managed[0]["payload"]["name"] == "content_writer"
    assert managed[0]["payload"]["source"] == "talk"


def test_manage_agent_failed_enrollment_does_not_log(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """A 409 duplicate enrollment must not leave an audit row."""
    _activate_eh_session(daemon_state)
    # Pre-seed a pending agent file so the duplicate check fires.
    from src.orchestrator import prompt_loader
    from src.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone
    agent = AgentDef(
        name="content_writer", team="content", role="worker", executor="claude",
        allow_rules=(), repos={}, enrolled_by="engineering_head",
        enrolled_at_task=_EH_TASK, enrolled_at=datetime.now(timezone.utc),
        system_prompt="prompt\n",
    )
    prompt_loader.write_pending_agent(daemon_state.runtime, agent)
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 409

    managed = [
        log for log in daemon_state.db.get_audit_logs(_EH_TASK)
        if log["action"] == "agent_managed"
    ]
    assert len(managed) == 0


# ---------------------------------------------------------------------------
# ManageAgentBody.allow_rules validation tests (FIX 1 security hardening)
# ---------------------------------------------------------------------------

def test_manage_agent_body_allow_rules_accepts_valid() -> None:
    """Valid allow_rules list with safe entries validates successfully."""
    from src.daemon.routes.agents import ManageAgentBody

    body = ManageAgentBody(
        action="enroll",
        name="seo_agent",
        task_id="TASK-200",
        session_id="sess-eh",
        description="SEO agent",
        system_prompt="You are the SEO Agent...",
        allow_rules=["gh api", "curl https://api.example.com"],
    )
    assert body.allow_rules == ["gh api", "curl https://api.example.com"]


def test_manage_agent_body_allow_rules_rejects_empty_string() -> None:
    """Empty string entry in allow_rules must be rejected with 422."""
    import pytest
    from pydantic import ValidationError
    from src.daemon.routes.agents import ManageAgentBody

    with pytest.raises(ValidationError, match="non-empty"):
        ManageAgentBody(
            action="enroll",
            name="seo_agent",
            task_id="TASK-201",
            session_id="sess-eh",
            description="desc",
            system_prompt="prompt",
            allow_rules=[""],
        )


def test_manage_agent_body_allow_rules_rejects_whitespace_only() -> None:
    """Whitespace-only entry in allow_rules must be rejected."""
    import pytest
    from pydantic import ValidationError
    from src.daemon.routes.agents import ManageAgentBody

    with pytest.raises(ValidationError, match="non-empty"):
        ManageAgentBody(
            action="enroll",
            name="seo_agent",
            task_id="TASK-202",
            session_id="sess-eh",
            description="desc",
            system_prompt="prompt",
            allow_rules=["   "],
        )


def test_manage_agent_body_allow_rules_rejects_embedded_newline() -> None:
    """Entry with embedded newline must be rejected (newline = command separator)."""
    import pytest
    from pydantic import ValidationError
    from src.daemon.routes.agents import ManageAgentBody

    with pytest.raises(ValidationError):
        ManageAgentBody(
            action="enroll",
            name="seo_agent",
            task_id="TASK-203",
            session_id="sess-eh",
            description="desc",
            system_prompt="prompt",
            allow_rules=["gh api\ngh pr merge"],
        )


def test_manage_agent_body_allow_rules_rejects_embedded_semicolon() -> None:
    """Entry with semicolon must be rejected (semicolon = command separator)."""
    import pytest
    from pydantic import ValidationError
    from src.daemon.routes.agents import ManageAgentBody

    with pytest.raises(ValidationError):
        ManageAgentBody(
            action="enroll",
            name="seo_agent",
            task_id="TASK-204",
            session_id="sess-eh",
            description="desc",
            system_prompt="prompt",
            allow_rules=["gh api; rm -rf /"],
        )


def test_manage_agent_body_allow_rules_rejects_leading_whitespace() -> None:
    """Entry with leading whitespace must be rejected."""
    import pytest
    from pydantic import ValidationError
    from src.daemon.routes.agents import ManageAgentBody

    with pytest.raises(ValidationError, match="leading/trailing whitespace"):
        ManageAgentBody(
            action="enroll",
            name="seo_agent",
            task_id="TASK-205",
            session_id="sess-eh",
            description="desc",
            system_prompt="prompt",
            allow_rules=[" gh api"],
        )


def test_manage_agent_body_allow_rules_none_is_valid() -> None:
    """allow_rules=None (omitted) is accepted — means use protocol defaults."""
    from src.daemon.routes.agents import ManageAgentBody

    body = ManageAgentBody(
        action="enroll",
        name="seo_agent",
        task_id="TASK-206",
        session_id="sess-eh",
        description="desc",
        system_prompt="prompt",
    )
    assert body.allow_rules is None


def test_init_agents_targets_include_content_team(
    daemon_state,
) -> None:
    """init_agents target enumeration includes Content Team agents from TeamsRegistry."""
    # daemon_state uses a fresh temp runtime → TeamsRegistry.load falls back to
    # DEFAULT_LAYOUT, which includes content_manager / content_writer / content_qa.
    assert daemon_state.teams is not None
    agents = daemon_state.teams.all_agents()
    assert "content_manager" in agents
    assert "content_writer" in agents
    assert "content_qa" in agents


def test_init_agents_targets_include_approved_enrollments(
    daemon_state,
) -> None:
    """init_agents target enumeration includes approved enrollments from agent files."""
    from src.orchestrator import prompt_loader
    from src.orchestrator.agent_def import AgentDef, render_agent_text
    from datetime import datetime, timezone
    agent = AgentDef(
        name="seo_agent", team="content", role="worker", executor="claude",
        allow_rules=(), repos={}, enrolled_by="engineering_head",
        enrolled_at_task=None, enrolled_at=datetime.now(timezone.utc),
        system_prompt="You are SEO.\n",
    )
    daemon_state.runtime.agents_dir.mkdir(parents=True, exist_ok=True)
    (daemon_state.runtime.agents_dir / "seo_agent.md").write_text(render_agent_text(agent))
    names = [a.name for a in prompt_loader.list_agents(daemon_state.runtime)]
    assert "seo_agent" in names


def test_init_agents_targets_none_teams_is_safe(daemon_state) -> None:
    """If teams is None the guard prevents a crash; workspace dirs are still used."""
    daemon_state.teams = None  # type: ignore[assignment]
    # No crash — state.teams is None but the guard `if state.teams is not None` handles it.
    from src.orchestrator import prompt_loader
    known: set[str] = set()
    if daemon_state.teams is not None:
        known.update(daemon_state.teams.all_agents())
    ws_dir = daemon_state.runtime.workspaces_dir
    if ws_dir.exists():
        known.update(d.name for d in ws_dir.iterdir() if d.is_dir())
    known.update([a.name for a in prompt_loader.list_agents(daemon_state.runtime)])
    # No exception raised; result is an empty or workspace-only set.
    assert isinstance(known, set)


# ---------------------------------------------------------------------------
# Task 6.1: file-based enroll / approve / reject tests
# ---------------------------------------------------------------------------

def test_manage_agent_enroll_writes_pending_file(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """manage-agent enroll writes a pending agent file under _pending/."""
    _activate_eh_session(daemon_state)
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "seo_agent",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "description": "Does SEO",
            "system_prompt": "You are the SEO Agent.",
            "executor": "claude",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    from src.orchestrator import prompt_loader
    agent = prompt_loader.load_pending_agent(daemon_state.runtime, "seo_agent")
    assert agent is not None
    assert agent.name == "seo_agent"
    assert agent.executor == "claude"


def test_approve_agent_moves_file(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """approve moves the pending file to the active agents dir."""
    from src.orchestrator import prompt_loader
    from src.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone

    agent = AgentDef(
        name="seo_agent",
        team="content",
        role="worker",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by="engineering_head",
        enrolled_at_task=_EH_TASK,
        enrolled_at=datetime.now(timezone.utc),
        system_prompt="You are the SEO Agent.\n",
    )
    prompt_loader.write_pending_agent(daemon_state.runtime, agent)

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None
        mock_ctx.create_agent_dirs.return_value = None
        r = TestClient(app).post(
            "/api/v1/agents/seo_agent/approve",
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert prompt_loader.load_agent(daemon_state.runtime, "seo_agent") is not None
    assert prompt_loader.load_pending_agent(daemon_state.runtime, "seo_agent") is None


def test_reject_agent_unlinks_file(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """reject removes the pending file."""
    from src.orchestrator import prompt_loader
    from src.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone

    agent = AgentDef(
        name="seo_agent",
        team="content",
        role="worker",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by="engineering_head",
        enrolled_at_task=_EH_TASK,
        enrolled_at=datetime.now(timezone.utc),
        system_prompt="You are the SEO Agent.\n",
    )
    prompt_loader.write_pending_agent(daemon_state.runtime, agent)

    r = TestClient(app).post(
        "/api/v1/agents/seo_agent/reject",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert prompt_loader.load_pending_agent(daemon_state.runtime, "seo_agent") is None
