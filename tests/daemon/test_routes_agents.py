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
    e = daemon_state.db.get_enrollment("content_writer")
    assert e is not None
    assert e["status"] == "pending"
    assert e["executor"] == "codex"


def test_manage_agent_enroll_duplicate_returns_409(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    _activate_eh_session(daemon_state)
    daemon_state.db.insert_enrollment("content_writer", "desc", "prompt")
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


def test_manage_agent_update_changes_prompt(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    _activate_eh_session(daemon_state)
    daemon_state.db.insert_enrollment("content_writer", "desc", "old prompt")
    daemon_state.db.update_enrollment_status("content_writer", "approved")
    workspace = daemon_state.runtime.workspaces_dir / "content_writer"
    workspace.mkdir(parents=True)

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.ensure_workspace_ready.return_value = None
        r = TestClient(app).post(
            "/api/v1/agents/manage",
            json={
                "action": "update",
                "name": "content_writer",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "system_prompt": "new prompt",
            "executor": "codex",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    enrollment = daemon_state.db.get_enrollment("content_writer")
    assert enrollment["system_prompt"] == "new prompt"
    assert enrollment["executor"] == "codex"


def test_manage_agent_update_persists_executor_to_workspace(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    _activate_eh_session(daemon_state)
    daemon_state.db.insert_enrollment("content_writer", "desc", "old prompt")
    daemon_state.db.update_enrollment_status("content_writer", "approved")
    workspace = daemon_state.runtime.workspaces_dir / "content_writer"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\n")

    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "update",
            "name": "content_writer",
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
    _activate_eh_session(daemon_state)
    daemon_state.db.insert_enrollment("content_writer", "desc", "prompt")
    daemon_state.db.update_enrollment_status("content_writer", "approved")
    workspace = daemon_state.runtime.workspaces_dir / "content_writer"
    workspace.mkdir(parents=True)
    (workspace / "CLAUDE.md").write_text("# test")

    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "terminate",
            "name": "content_writer",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert not workspace.exists()
    assert daemon_state.db.get_enrollment("content_writer")["status"] == "terminated"


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
    daemon_state.db.insert_enrollment("content_writer", "desc", "prompt", executor="codex")

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
    assert daemon_state.db.get_enrollment("content_writer")["status"] == "approved"
    workspace = daemon_state.runtime.workspaces_dir / "content_writer"
    assert workspace.exists()

    from src.daemon.agent_config import load_agent_config

    cfg = load_agent_config(workspace)
    assert cfg["executor"] == "codex"


def test_approve_non_pending_returns_409(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    daemon_state.db.insert_enrollment("content_writer", "desc", "prompt")
    daemon_state.db.update_enrollment_status("content_writer", "approved")
    r = TestClient(app).post(
        "/api/v1/agents/content_writer/approve",
        headers=auth_headers,
    )
    assert r.status_code == 409


def test_reject_agent(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    daemon_state.db.insert_enrollment("content_writer", "desc", "prompt")
    r = TestClient(app).post(
        "/api/v1/agents/content_writer/reject",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert daemon_state.db.get_enrollment("content_writer")["status"] == "rejected"


def test_list_enrollments(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    daemon_state.db.insert_enrollment("a", "desc a", "prompt a")
    daemon_state.db.insert_enrollment("b", "desc b", "prompt b")
    daemon_state.db.update_enrollment_status("a", "approved")

    r = TestClient(app).get(
        "/api/v1/agents/enrollments",
        params={"status": "pending"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    names = [e["name"] for e in r.json()["enrollments"]]
    assert names == ["b"]


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
    e = daemon_state.db.get_enrollment("content_writer")
    assert e is not None
    assert e["status"] == "pending"
    assert e["executor"] == "codex"


def test_manage_agent_talk_path_update_changes_prompt(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    talk_id = _seed_eh_talk(daemon_state, "TALK-701")
    daemon_state.db.insert_enrollment("content_writer", "desc", "old prompt")
    daemon_state.db.update_enrollment_status("content_writer", "approved")
    workspace = daemon_state.runtime.workspaces_dir / "content_writer"
    workspace.mkdir(parents=True)

    with patch("src.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.ensure_workspace_ready.return_value = None
        r = TestClient(app).post(
            "/api/v1/agents/manage",
            json={
                "action": "update",
                "name": "content_writer",
                "talk_id": talk_id,
                "system_prompt": "new prompt via talk",
            },
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert daemon_state.db.get_enrollment("content_writer")["system_prompt"] == "new prompt via talk"


def test_manage_agent_talk_path_terminate_removes_workspace(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    talk_id = _seed_eh_talk(daemon_state, "TALK-702")
    daemon_state.db.insert_enrollment("content_writer", "desc", "prompt")
    daemon_state.db.update_enrollment_status("content_writer", "approved")
    workspace = daemon_state.runtime.workspaces_dir / "content_writer"
    workspace.mkdir(parents=True)
    (workspace / "CLAUDE.md").write_text("# test")

    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "terminate",
            "name": "content_writer",
            "talk_id": talk_id,
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert not workspace.exists()
    assert daemon_state.db.get_enrollment("content_writer")["status"] == "terminated"


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
    daemon_state.db.insert_enrollment("content_writer", "desc", "prompt")
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
