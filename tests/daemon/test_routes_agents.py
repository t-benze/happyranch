from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from runtime.orchestrator._paths import OrgPaths

_EH_TASK = "TASK-100"
_EH_SESSION = "sess-eh-test"


def _activate_eh_session(org_state) -> None:
    """Register an active engineering_head session so manage-agent calls succeed."""
    org_state.sessions.set_active(_EH_TASK, "engineering_head", _EH_SESSION)


def _paths(org_state) -> OrgPaths:
    return OrgPaths(root=org_state.root)


def test_list_agents_returns_names(tmp_home, app, org_state, auth_headers) -> None:
    # Create at least one workspace so list_agents finds it
    ws = org_state.root / "workspaces" / "engineering_head"
    ws.mkdir(parents=True, exist_ok=True)
    r = TestClient(app).get("/api/v1/orgs/alpha/agents", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "agents" in body
    names = [a["name"] for a in body["agents"]]
    assert "engineering_head" in names


def test_list_agents_returns_full_shape(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Each agent row carries the founder-UI fields: team/role/executor/
    description. The performance-tier feature was removed; the audit log is
    the canonical record of agent outcomes."""
    from datetime import datetime, timezone
    from runtime.orchestrator.agent_def import AgentDef

    ws = org_state.root / "workspaces" / "engineering_head"
    ws.mkdir(parents=True, exist_ok=True)

    paths = _paths(org_state)
    agent = AgentDef(
        name="engineering_head",
        team="engineering",
        role="manager",
        executor="claude",
        allow_rules=tuple(),
        repos={},
        enrolled_by=None,
        enrolled_at_task=None,
        enrolled_at=datetime.now(timezone.utc),
        system_prompt="manage the engineering team",
        description="Owns the engineering team.",
    )
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / "engineering_head.md").write_text(
        # use the canonical render helper so the file round-trips cleanly
        __import__("runtime.orchestrator.agent_def", fromlist=["render_agent_text"])
            .render_agent_text(agent),
    )

    r = TestClient(app).get("/api/v1/orgs/alpha/agents", headers=auth_headers)
    assert r.status_code == 200
    rows = {a["name"]: a for a in r.json()["agents"]}
    eh = rows["engineering_head"]
    assert eh["team"] == "engineering"
    assert eh["role"] == "manager"
    assert eh["executor"] == "claude"
    assert eh["description"] == "Owns the engineering team."
    # model is returned by GET /agents (resolved from agent.yaml or None)
    assert "model" in eh
    assert eh["model"] is None  # no agent.yaml → null
    # No tier / scorecard / avg_confidence fields — tier feature removed.
    assert "tier" not in eh
    assert "scorecard" not in eh
    assert "avg_confidence" not in eh


def test_list_agents_returns_model(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """GET /agents returns model — both set and null — resolved from agent.yaml."""
    from datetime import datetime, timezone
    from runtime.orchestrator.agent_def import AgentDef
    from runtime.daemon.agent_config import set_model

    ws = org_state.root / "workspaces" / "engineering_head"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")

    paths = _paths(org_state)
    agent = AgentDef(
        name="engineering_head",
        team="engineering",
        role="manager",
        executor="claude",
        allow_rules=tuple(),
        repos={},
        enrolled_by=None,
        enrolled_at_task=None,
        enrolled_at=datetime.now(timezone.utc),
        system_prompt="manage the engineering team",
        description="Owns the engineering team.",
    )
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / "engineering_head.md").write_text(
        __import__("runtime.orchestrator.agent_def", fromlist=["render_agent_text"])
            .render_agent_text(agent),
    )

    # No model set → null
    r = TestClient(app).get("/api/v1/orgs/alpha/agents", headers=auth_headers)
    assert r.status_code == 200
    rows = {a["name"]: a for a in r.json()["agents"]}
    eh = rows["engineering_head"]
    assert eh["model"] is None

    # Set a model → returned
    set_model(ws, "claude-sonnet-4-20250514")
    r = TestClient(app).get("/api/v1/orgs/alpha/agents", headers=auth_headers)
    assert r.status_code == 200
    rows = {a["name"]: a for a in r.json()["agents"]}
    eh = rows["engineering_head"]
    assert eh["model"] == "claude-sonnet-4-20250514"

    # Clear the model → null
    set_model(ws, None)
    r = TestClient(app).get("/api/v1/orgs/alpha/agents", headers=auth_headers)
    assert r.status_code == 200
    rows = {a["name"]: a for a in r.json()["agents"]}
    eh = rows["engineering_head"]
    assert eh["model"] is None


def test_model_survives_agent_yaml_regeneration(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """GET /agents returns the durable frontmatter model when agent.yaml
    is regenerated without a model key.

    Reproduces THR-069: per-agent model showed EMPTY on the web Agents page
    after a daemon restart / workspace re-bootstrap.  _resolve_agent_model
    read ONLY from agent.yaml (a regenerable cache) and never fell back to
    the durable AgentDef.model in org/agents/<name>.md, so any path that
    regenerated agent.yaml silently dropped the model.

    This test verifies the fix: when agent.yaml lacks model, the READ
    falls back to the frontmatter.
    """
    from runtime.daemon.agent_config import write_default_agent_config
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    from datetime import datetime, timezone

    paths = _paths(org_state)
    ws = paths.workspaces_dir / "consultant_head"

    # Seed the durable frontmatter WITH model: fable.
    agent = AgentDef(
        name="consultant_head",
        team="engineering",
        role="worker",
        executor="claude",
        allow_rules=tuple(),
        repos={},
        enrolled_by=None,
        enrolled_at_task=None,
        enrolled_at=datetime.now(timezone.utc),
        system_prompt="consultant",
        description="Consultant head.",
        model="fable",
    )
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / "consultant_head.md").write_text(render_agent_text(agent))

    # Simulate a daemon restart / workspace re-bootstrap: agent.yaml is missing
    # and gets regenerated by write_default_agent_config WITHOUT a model key.
    ws.mkdir(parents=True, exist_ok=True)
    write_default_agent_config(ws)
    # Verify agent.yaml was created and has NO model key.
    assert ws.joinpath("agent.yaml").exists()
    cfg_text = ws.joinpath("agent.yaml").read_text()
    assert "model" not in cfg_text

    # GET /agents MUST still return the model from the durable frontmatter.
    r = TestClient(app).get("/api/v1/orgs/alpha/agents", headers=auth_headers)
    assert r.status_code == 200
    rows = {a["name"]: a for a in r.json()["agents"]}
    ch = rows["consultant_head"]
    assert ch["model"] == "fable", (
        f"Expected model='fable' from frontmatter fallback, got {ch['model']!r}"
    )


def test_list_enrollments_returns_team_and_role(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Enrollment rows carry team/role/executor so the Pending tab can render
    without a second roundtrip."""
    from datetime import datetime, timezone
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text

    paths = _paths(org_state)
    paths.pending_agents_dir.mkdir(parents=True, exist_ok=True)
    agent = AgentDef(
        name="new_writer",
        team="content",
        role="worker",
        executor="codex",
        allow_rules=tuple(),
        repos={},
        enrolled_by="content_manager",
        enrolled_at_task="TASK-050",
        enrolled_at=datetime.now(timezone.utc),
        system_prompt="write things",
        description="Drafts blog posts.",
    )
    (paths.pending_agents_dir / "new_writer.md").write_text(render_agent_text(agent))

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/agents/enrollments?status=pending",
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = r.json()["enrollments"]
    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "new_writer"
    assert row["team"] == "content"
    assert row["role"] == "worker"
    assert row["executor"] == "codex"
    assert row["enrolled_by"] == "content_manager"
    assert row["status"] == "pending"


def test_learnings_requires_session_id(tmp_home, app, org_state, auth_headers) -> None:
    org_state.sessions.set_active("TASK-001", "dev_agent", "sess-1")
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/dev_agent/learnings",
        json={"text": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 422  # session_id missing


def test_learnings_appends_to_file(
    tmp_home, app, org_state, auth_headers, tmp_path,
) -> None:
    org_state.sessions.set_active("TASK-001", "dev_agent", "sess-1")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "learnings.md").write_text("# Learnings: dev_agent\n\n")

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/dev_agent/learnings",
        json={"session_id": "sess-1", "task_id": "TASK-001", "text": "use uv not pip"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert "use uv not pip" in (workspace / "learnings.md").read_text()


def test_learnings_session_mismatch_409(
    tmp_home, app, org_state, auth_headers,
) -> None:
    org_state.sessions.set_active("TASK-001", "dev_agent", "sess-real")
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/dev_agent/learnings",
        json={"session_id": "sess-stale", "task_id": "TASK-001", "text": "x"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_learnings_unknown_session_409(
    tmp_home, app, org_state, auth_headers, tmp_path,
) -> None:
    """Unregistered (task, agent) pair — reject and do not create/append."""
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    learnings = workspace / "learnings.md"
    learnings.write_text("# Learnings: dev_agent\n\n")

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/dev_agent/learnings",
        json={"session_id": "fabricated", "task_id": "TASK-NOPE", "text": "should not land"},
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "unknown_session"
    assert "should not land" not in learnings.read_text()


def test_init_writes_default_agent_yaml_and_creates_dirs(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """init-agent must leave the workspace bootstrapped: agent.yaml present,
    agent-specific folders created (e.g. specs/ for product_manager)."""
    client = TestClient(app)
    with client.stream(
        "POST", "/api/v1/orgs/alpha/agents/init",
        json={"agent": "product_manager"},
        headers=auth_headers,
    ) as r:
        assert r.status_code == 200
        # Drain the SSE stream so the background generator completes.
        for _ in r.iter_lines():
            pass

    ws = org_state.root / "workspaces" / "product_manager"
    assert (ws / "agent.yaml").exists(), "agent.yaml was not created"
    assert (ws / "specs").is_dir(), "product_manager specs/ dir missing"


def test_init_creates_workspace_for_any_name(tmp_home, app, org_state, auth_headers) -> None:
    """init-agent accepts any valid agent name, no longer validates against enum."""
    client = TestClient(app)
    with client.stream(
        "POST", "/api/v1/orgs/alpha/agents/init",
        json={"agent": "new_custom_agent"},
        headers=auth_headers,
    ) as r:
        assert r.status_code == 200
        for _ in r.iter_lines():
            pass
    ws = org_state.root / "workspaces" / "new_custom_agent"
    assert (ws / "agent.yaml").exists()


def test_manage_repo_add_creates_entry_and_clones(
    tmp_home, app, org_state, auth_headers,
) -> None:
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\n")

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None

        r = TestClient(app).post(
            "/api/v1/orgs/alpha/agents/dev_agent/repos",
            json={"action": "add", "repo_name": "docs", "url": "https://github.com/t-benze/docs.git"},
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    mock_ctx.clone_repo.assert_called_once()
    mock_ctx.ensure_workspace_ready.assert_called_once()

    from runtime.daemon.agent_config import load_agent_config
    cfg = load_agent_config(workspace)
    assert cfg["repos"]["docs"] == "https://github.com/t-benze/docs.git"


def test_manage_repo_add_duplicate_returns_409(
    tmp_home, app, org_state, auth_headers,
) -> None:
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos:\n  docs: https://old.git\n")

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/dev_agent/repos",
        json={"action": "add", "repo_name": "docs", "url": "https://new.git"},
        headers=auth_headers,
    )
    assert r.status_code == 409


def test_manage_repo_remove_deletes_entry_and_dir(
    tmp_home, app, org_state, auth_headers,
) -> None:
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos:\n  docs: https://old.git\n")
    repo_dir = workspace / "repos" / "docs"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()  # fake git dir

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.ensure_workspace_ready.return_value = None

        r = TestClient(app).post(
            "/api/v1/orgs/alpha/agents/dev_agent/repos",
            json={"action": "remove", "repo_name": "docs"},
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert not repo_dir.exists()

    from runtime.daemon.agent_config import load_agent_config
    cfg = load_agent_config(workspace)
    assert "docs" not in cfg.get("repos", {})


def test_manage_repo_remove_nonexistent_returns_404(
    tmp_home, app, org_state, auth_headers,
) -> None:
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\n")

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/dev_agent/repos",
        json={"action": "remove", "repo_name": "ghost"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_manage_repo_update_reclones(
    tmp_home, app, org_state, auth_headers,
) -> None:
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos:\n  docs: https://old.git\n")
    repo_dir = workspace / "repos" / "docs"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None

        r = TestClient(app).post(
            "/api/v1/orgs/alpha/agents/dev_agent/repos",
            json={"action": "update", "repo_name": "docs", "url": "https://new.git"},
            headers=auth_headers,
        )
    assert r.status_code == 200
    mock_ctx.clone_repo.assert_called_once()

    from runtime.daemon.agent_config import load_agent_config
    cfg = load_agent_config(workspace)
    assert cfg["repos"]["docs"] == "https://new.git"


def test_manage_repo_add_missing_url_returns_422(
    tmp_home, app, org_state, auth_headers,
) -> None:
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\n")

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/dev_agent/repos",
        json={"action": "add", "repo_name": "docs"},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_manage_repo_unknown_workspace_returns_404(
    tmp_home, app, auth_headers,
) -> None:
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/nonexistent/repos",
        json={"action": "add", "repo_name": "x", "url": "https://x.git"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_repo_round_trip_add_remove_reflected_in_get_agents(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Add a repo -> GET /agents shows it. Remove a repo -> GET /agents omits it.

    The GET /agents read model must reflect the same agent.yaml repo store
    that POST /agents/{agent}/repos mutates.  This test guards against the
    pre-fix drift where GET read from AgentDef frontmatter while the repo
    route wrote to workspace agent.yaml.
    """
    from datetime import datetime, timezone
    from runtime.orchestrator.agent_def import AgentDef

    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\n")

    paths = _paths(org_state)
    agent = AgentDef(
        name="dev_agent",
        team="engineering",
        role="worker",
        executor="claude",
        allow_rules=tuple(),
        repos={},
        enrolled_by=None,
        enrolled_at_task=None,
        enrolled_at=datetime.now(timezone.utc),
        system_prompt="code",
        description="Builds things.",
    )
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / "dev_agent.md").write_text(
        __import__("runtime.orchestrator.agent_def", fromlist=["render_agent_text"])
            .render_agent_text(agent),
    )

    client = TestClient(app)

    # --- Phase 1: add a repo and confirm GET /agents reflects it ---
    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None
        r = client.post(
            "/api/v1/orgs/alpha/agents/dev_agent/repos",
            json={"action": "add", "repo_name": "happyranch",
                  "url": "https://github.com/t-benze/happyranch.git"},
            headers=auth_headers,
        )
    assert r.status_code == 200

    r = client.get("/api/v1/orgs/alpha/agents", headers=auth_headers)
    assert r.status_code == 200
    rows = {a["name"]: a for a in r.json()["agents"]}
    assert "dev_agent" in rows
    repos = rows["dev_agent"]["repos"]
    assert "happyranch" in repos
    assert repos["happyranch"] == "https://github.com/t-benze/happyranch.git"

    # --- Phase 2: remove the repo and confirm it's gone from GET /agents ---
    repo_dir = workspace / "repos" / "happyranch"
    repo_dir.mkdir(parents=True)
    (repo_dir / ".git").mkdir()
    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.ensure_workspace_ready.return_value = None
        r = client.post(
            "/api/v1/orgs/alpha/agents/dev_agent/repos",
            json={"action": "remove", "repo_name": "happyranch"},
            headers=auth_headers,
        )
    assert r.status_code == 200

    r = client.get("/api/v1/orgs/alpha/agents", headers=auth_headers)
    assert r.status_code == 200
    rows = {a["name"]: a for a in r.json()["agents"]}
    assert "dev_agent" in rows
    repos_after = rows["dev_agent"]["repos"]
    assert "happyranch" not in repos_after


def test_manage_agent_enroll_creates_pending(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _activate_eh_session(org_state)
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
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
    from runtime.orchestrator import prompt_loader
    agent = prompt_loader.load_pending_agent(_paths(org_state), "content_writer")
    assert agent is not None
    assert agent.executor == "codex"


def test_manage_agent_enroll_persists_description(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """description from the request body must round-trip through pending file
    and surface on /agents/enrollments — Codex review caught this regression."""
    _activate_eh_session(org_state)
    desc = "Writes destination guides for HK and Macau."
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "description": desc,
            "system_prompt": "You are the Content Writer...",
            "executor": "claude",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    from runtime.orchestrator import prompt_loader
    pending = prompt_loader.load_pending_agent(_paths(org_state), "content_writer")
    assert pending is not None
    assert pending.description == desc

    list_resp = TestClient(app).get(
        "/api/v1/orgs/alpha/agents/enrollments",
        params={"status": "pending"},
        headers=auth_headers,
    )
    assert list_resp.status_code == 200
    found = [e for e in list_resp.json()["enrollments"] if e["name"] == "content_writer"]
    assert found and found[0]["description"] == desc


def test_manage_agent_enroll_duplicate_returns_409(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _activate_eh_session(org_state)
    # Pre-seed a pending agent file so the duplicate check fires.
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone
    agent = AgentDef(
        name="content_writer", team="content", role="worker", executor="claude",
        allow_rules=(), repos={}, enrolled_by="engineering_head",
        enrolled_at_task=_EH_TASK, enrolled_at=datetime.now(timezone.utc),
        system_prompt="prompt\n",
    )
    prompt_loader.write_pending_agent(_paths(org_state), agent)
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
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


def test_manage_agent_enroll_rejects_invalid_executor_at_boundary(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Invalid executor must 422 at the request boundary, not 500 mid-mutation."""
    _activate_eh_session(org_state)
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
        json={
            "action": "enroll",
            "name": "rogue_agent",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "description": "desc",
            "system_prompt": "prompt",
            "executor": "gpt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 422
    # The pending file must NOT have been created.
    from runtime.orchestrator import prompt_loader
    assert prompt_loader.load_pending_agent(_paths(org_state), "rogue_agent") is None


def test_manage_agent_enroll_invalid_name_returns_422(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _activate_eh_session(org_state)
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
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


def _seed_active_agent(org_state, name: str, team: str = "engineering", executor: str = "claude", system_prompt: str = "prompt\n") -> None:
    """Write an active agent file for testing update/terminate endpoints."""
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    from datetime import datetime, timezone
    agent = AgentDef(
        name=name, team=team, role="worker", executor=executor,
        allow_rules=(), repos={}, enrolled_by="engineering_head",
        enrolled_at_task=_EH_TASK, enrolled_at=datetime.now(timezone.utc),
        system_prompt=system_prompt,
    )
    paths = _paths(org_state)
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / f"{name}.md").write_text(render_agent_text(agent))


def test_manage_agent_update_changes_prompt(
    tmp_home, app, org_state, auth_headers,
) -> None:
    # Use dev_agent which belongs to engineering team (managed by engineering_head).
    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent", system_prompt="old prompt\n")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.ensure_workspace_ready.return_value = None
        r = TestClient(app).post(
            "/api/v1/orgs/alpha/agents/manage",
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
    from runtime.orchestrator import prompt_loader
    updated = prompt_loader.load_agent(_paths(org_state), "dev_agent")
    assert updated is not None
    assert "new prompt" in updated.system_prompt
    assert updated.executor == "codex"


def test_manage_agent_update_persists_executor_to_workspace(
    tmp_home, app, org_state, auth_headers,
) -> None:
    # Use dev_agent which belongs to engineering team (managed by engineering_head).
    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\n")

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
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

    from runtime.daemon.agent_config import load_agent_config

    cfg = load_agent_config(workspace)
    assert cfg["executor"] == "codex"


def test_manage_agent_update_executor_regenerates_bootstrap(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Switching executor without supplying system_prompt must regenerate
    workspace bootstrap for the new executor profile (not default to claude)."""
    from runtime.orchestrator.executor_registry import (
        get_registry,
        ExecutorProfile,
    )

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent", executor="claude", system_prompt="sys prompt")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")

    # Register a custom profile so the registry accepts the new name.
    get_registry().register_custom_profile(
        ExecutorProfile(
            name="testcustom",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["echo", "{prompt}"],
        )
    )

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.ensure_workspace_ready.return_value = None
        r = TestClient(app).post(
            "/api/v1/orgs/alpha/agents/manage",
            json={
                "action": "update",
                "name": "dev_agent",
                "task_id": _EH_TASK,
                "session_id": _EH_SESSION,
                "executor": "testcustom",
            },
            headers=auth_headers,
        )
        assert r.status_code == 200

        # Verify ensure_workspace_ready was called with the new executor
        # name as provider, NOT default "claude".
        mock_ctx.ensure_workspace_ready.assert_called_once()
        call_args = mock_ctx.ensure_workspace_ready.call_args
        # call_args[0] = positional args tuple, call_args[1] = keyword args dict
        assert call_args[1]["provider"] == "testcustom", \
            f"expected provider=testcustom, got {call_args}"
        # System prompt must come from the preserved AgentDef, not the body.
        assert call_args[0][2].strip() == "sys prompt", \
            f"expected system prompt 'sys prompt', got {call_args[0][2]!r}"

    # Agent.yaml must reflect the new executor.
    from runtime.daemon.agent_config import load_agent_config
    cfg = load_agent_config(workspace)
    assert cfg["executor"] == "testcustom"


def test_manage_agent_terminate_removes_workspace(
    tmp_home, app, org_state, auth_headers,
) -> None:
    # Use dev_agent which belongs to engineering team (managed by engineering_head).
    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "CLAUDE.md").write_text("# test")

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
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
    from runtime.orchestrator import prompt_loader
    assert prompt_loader.load_agent(_paths(org_state), "dev_agent") is None


def test_manage_agent_terminate_nonexistent_returns_404(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _activate_eh_session(org_state)
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
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
    tmp_home, app, auth_headers,
) -> None:
    """Requests without an active EH session are rejected."""
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
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
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Requests with a mismatched session_id are rejected."""
    _activate_eh_session(org_state)
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
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
    tmp_home, app, org_state, auth_headers,
) -> None:
    # Pre-seed a pending agent file.
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone
    agent = AgentDef(
        name="content_writer", team="content", role="worker", executor="codex",
        allow_rules=(), repos={}, enrolled_by="engineering_head",
        enrolled_at_task=_EH_TASK, enrolled_at=datetime.now(timezone.utc),
        system_prompt="prompt\n",
    )
    prompt_loader.write_pending_agent(_paths(org_state), agent)

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None
        mock_ctx.create_agent_dirs.return_value = None

        r = TestClient(app).post(
            "/api/v1/orgs/alpha/agents/content_writer/approve",
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert prompt_loader.load_agent(_paths(org_state), "content_writer") is not None
    assert prompt_loader.load_pending_agent(_paths(org_state), "content_writer") is None
    workspace = org_state.root / "workspaces" / "content_writer"
    assert workspace.exists()

    from runtime.daemon.agent_config import load_agent_config

    cfg = load_agent_config(workspace)
    assert cfg["executor"] == "codex"


def test_approve_non_pending_returns_409(
    tmp_home, app, org_state, auth_headers,
) -> None:
    # Seed an active (approved) agent file — not pending.
    _seed_active_agent(org_state, "content_writer", team="content")
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/content_writer/approve",
        headers=auth_headers,
    )
    assert r.status_code == 409


def test_reject_agent(
    tmp_home, app, org_state, auth_headers,
) -> None:
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone
    agent = AgentDef(
        name="content_writer", team="content", role="worker", executor="claude",
        allow_rules=(), repos={}, enrolled_by="engineering_head",
        enrolled_at_task=_EH_TASK, enrolled_at=datetime.now(timezone.utc),
        system_prompt="prompt\n",
    )
    prompt_loader.write_pending_agent(_paths(org_state), agent)
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/content_writer/reject",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert prompt_loader.load_pending_agent(_paths(org_state), "content_writer") is None


def test_reject_agent_removes_from_teams_yaml(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Reject must undo the teams.yaml mutation that enrollment performed."""
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone

    # Simulate a fully-enrolled pending agent: pending file + team membership.
    agent = AgentDef(
        name="rookie_writer", team="content", role="worker", executor="claude",
        allow_rules=(), repos={}, enrolled_by="engineering_head",
        enrolled_at_task=_EH_TASK, enrolled_at=datetime.now(timezone.utc),
        system_prompt="prompt\n",
    )
    prompt_loader.write_pending_agent(_paths(org_state), agent)
    org_state.teams.add_worker("content", "rookie_writer")
    assert "rookie_writer" in org_state.teams.all_agents()

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/rookie_writer/reject",
        headers=auth_headers,
    )
    assert r.status_code == 200
    # Pending file gone AND team membership removed.
    assert prompt_loader.load_pending_agent(_paths(org_state), "rookie_writer") is None
    assert "rookie_writer" not in org_state.teams.all_agents()


def test_list_enrollments(
    tmp_home, app, org_state, auth_headers,
) -> None:
    # Seed one pending and one active agent.
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone
    def _make(name, team):
        return AgentDef(
            name=name, team=team, role="worker", executor="claude",
            allow_rules=(), repos={}, enrolled_by="engineering_head",
            enrolled_at_task=_EH_TASK, enrolled_at=datetime.now(timezone.utc),
            system_prompt="prompt\n",
        )
    paths = _paths(org_state)
    prompt_loader.write_pending_agent(paths, _make("b", "content"))
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    from runtime.orchestrator.agent_def import render_agent_text
    (paths.agents_dir / "a.md").write_text(render_agent_text(_make("a", "engineering")))

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/agents/enrollments",
        params={"status": "pending"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    names = [e["name"] for e in r.json()["enrollments"]]
    assert names == ["b"]


def test_manage_agent_body_accepts_task_and_session() -> None:
    """(task_id + session_id) validates."""
    from runtime.daemon.routes.agents import ManageAgentBody

    body = ManageAgentBody(
        action="enroll",
        name="content_writer",
        task_id="TASK-100",
        session_id="sess-eh",
        description="desc",
        system_prompt="prompt",
    )
    assert body.task_id == "TASK-100"


def test_manage_agent_body_rejects_neither_path() -> None:
    """Supplying neither task_id+sess_id is a validation error."""
    import pytest
    from pydantic import ValidationError
    from runtime.daemon.routes.agents import ManageAgentBody

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
    from runtime.daemon.routes.agents import ManageAgentBody

    with pytest.raises(ValidationError):
        ManageAgentBody(
            action="enroll",
            name="content_writer",
            task_id="TASK-100",
            description="desc",
            system_prompt="prompt",
        )


def test_manage_agent_task_path_writes_audit_entry(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _activate_eh_session(org_state)
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
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
        log for log in org_state.db.get_audit_logs(_EH_TASK)
        if log["action"] == "agent_managed"
    ]
    assert len(managed) == 1
    assert managed[0]["agent"] == "engineering_head"
    assert managed[0]["payload"]["action"] == "enroll"
    assert managed[0]["payload"]["name"] == "content_writer"
    assert managed[0]["payload"]["source"] == "task"


def test_manage_agent_failed_enrollment_does_not_log(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """A 409 duplicate enrollment must not leave an audit row."""
    _activate_eh_session(org_state)
    # Pre-seed a pending agent file so the duplicate check fires.
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone
    agent = AgentDef(
        name="content_writer", team="content", role="worker", executor="claude",
        allow_rules=(), repos={}, enrolled_by="engineering_head",
        enrolled_at_task=_EH_TASK, enrolled_at=datetime.now(timezone.utc),
        system_prompt="prompt\n",
    )
    prompt_loader.write_pending_agent(_paths(org_state), agent)
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
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
        log for log in org_state.db.get_audit_logs(_EH_TASK)
        if log["action"] == "agent_managed"
    ]
    assert len(managed) == 0


# ---------------------------------------------------------------------------
# ManageAgentBody.allow_rules validation tests (FIX 1 security hardening)
# ---------------------------------------------------------------------------

def test_manage_agent_body_allow_rules_accepts_valid() -> None:
    """Valid allow_rules list with safe entries validates successfully."""
    from runtime.daemon.routes.agents import ManageAgentBody

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
    from runtime.daemon.routes.agents import ManageAgentBody

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
    from runtime.daemon.routes.agents import ManageAgentBody

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
    from runtime.daemon.routes.agents import ManageAgentBody

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
    from runtime.daemon.routes.agents import ManageAgentBody

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
    from runtime.daemon.routes.agents import ManageAgentBody

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
    from runtime.daemon.routes.agents import ManageAgentBody

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
    org_state,
) -> None:
    """init_agents target enumeration includes Content Team agents from TeamsRegistry."""
    # The conftest seeds engineering and content teams.
    assert org_state.teams is not None
    agents = org_state.teams.all_agents()
    assert "content_manager" in agents
    assert "content_writer" in agents
    assert "content_qa" in agents


def test_init_agents_targets_include_approved_enrollments(
    org_state,
) -> None:
    """init_agents target enumeration includes approved enrollments from agent files."""
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    from datetime import datetime, timezone
    agent = AgentDef(
        name="seo_agent", team="content", role="worker", executor="claude",
        allow_rules=(), repos={}, enrolled_by="engineering_head",
        enrolled_at_task=None, enrolled_at=datetime.now(timezone.utc),
        system_prompt="You are SEO.\n",
    )
    paths = _paths(org_state)
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / "seo_agent.md").write_text(render_agent_text(agent))
    names = [a.name for a in prompt_loader.list_agents(paths)]
    assert "seo_agent" in names


def test_init_agents_targets_none_teams_is_safe(org_state) -> None:
    """If teams is None the guard prevents a crash; workspace dirs are still used."""
    org_state.teams = None  # type: ignore[assignment]
    # No crash — org.teams is None but the guard `if org.teams is not None` handles it.
    from runtime.orchestrator import prompt_loader
    paths = _paths(org_state)
    known: set[str] = set()
    if org_state.teams is not None:
        known.update(org_state.teams.all_agents())
    ws_dir = paths.workspaces_dir
    if ws_dir.exists():
        known.update(d.name for d in ws_dir.iterdir() if d.is_dir())
    known.update([a.name for a in prompt_loader.list_agents(paths)])
    # No exception raised; result is an empty or workspace-only set.
    assert isinstance(known, set)


# ---------------------------------------------------------------------------
# Task 6.1: file-based enroll / approve / reject tests
# ---------------------------------------------------------------------------

def test_manage_agent_enroll_writes_pending_file(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """manage-agent enroll writes a pending agent file under _pending/."""
    _activate_eh_session(org_state)
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
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
    from runtime.orchestrator import prompt_loader
    agent = prompt_loader.load_pending_agent(_paths(org_state), "seo_agent")
    assert agent is not None
    assert agent.name == "seo_agent"
    assert agent.executor == "claude"


def test_approve_agent_moves_file(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """approve moves the pending file to the active agents dir."""
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator.agent_def import AgentDef
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
    prompt_loader.write_pending_agent(_paths(org_state), agent)

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None
        mock_ctx.create_agent_dirs.return_value = None
        r = TestClient(app).post(
            "/api/v1/orgs/alpha/agents/seo_agent/approve",
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert prompt_loader.load_agent(_paths(org_state), "seo_agent") is not None
    assert prompt_loader.load_pending_agent(_paths(org_state), "seo_agent") is None


def test_approve_agent_refuses_unknown_team(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """A pending agent whose team isn't in teams.yaml must not be promoted.

    Defense in depth against hand-edited pending files. The normal
    manage-agent enroll path already adds the team alongside the pending
    write, so this only triggers for out-of-band file writes.
    """
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator.agent_def import AgentDef
    from datetime import datetime, timezone

    agent = AgentDef(
        name="stranger",
        team="not_a_real_team",
        role="worker",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by="engineering_head",
        enrolled_at_task=_EH_TASK,
        enrolled_at=datetime.now(timezone.utc),
        system_prompt="You are a stranger.\n",
    )
    prompt_loader.write_pending_agent(_paths(org_state), agent)

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/stranger/approve",
        headers=auth_headers,
    )
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "team_not_registered"
    assert detail["team"] == "not_a_real_team"
    # Pending file is untouched on refusal.
    assert prompt_loader.load_pending_agent(_paths(org_state), "stranger") is not None
    assert prompt_loader.load_agent(_paths(org_state), "stranger") is None


def test_reject_agent_unlinks_file(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """reject removes the pending file."""
    from runtime.orchestrator import prompt_loader
    from runtime.orchestrator.agent_def import AgentDef
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
    prompt_loader.write_pending_agent(_paths(org_state), agent)

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/seo_agent/reject",
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert prompt_loader.load_pending_agent(_paths(org_state), "seo_agent") is None


# ---------------------------------------------------------------------------
# Founder set-executor route (PUT /agents/{agent_name}/executor)
# ---------------------------------------------------------------------------

def test_validate_executor_helper_accepts_and_rejects() -> None:
    """The standalone validator passes the supported set and rejects others
    with a 422 that lists the valid values."""
    import pytest
    from fastapi import HTTPException
    from runtime.daemon.routes.agents import _validate_executor

    for ok in ("claude", "codex", "opencode", "pi"):
        _validate_executor(ok)  # must not raise

    with pytest.raises(HTTPException) as ei:
        _validate_executor("gpt")
    assert ei.value.status_code == 422
    assert ei.value.detail["code"] == "invalid_executor"
    assert ei.value.detail["got"] == "gpt"
    assert "claude" in ei.value.detail["valid"] and "pi" in ei.value.detail["valid"]


def test_set_executor_switches_org_and_workspace(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Happy path: org frontmatter + workspace agent.yaml both flip, bootstrap
    is regenerated with the NEW provider, before/after state is reported."""
    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.ensure_workspace_ready.return_value = None
        r = TestClient(app).put(
            "/api/v1/orgs/alpha/agents/dev_agent/executor",
            json={"executor": "pi"},
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["before"]["org_executor"] == "claude"
    assert body["after"]["org_executor"] == "pi"
    assert body["before"]["workspace_executor"] == "claude"
    assert body["after"]["workspace_executor"] == "pi"

    # org .md frontmatter updated
    from runtime.orchestrator import prompt_loader
    reloaded = prompt_loader.load_agent(_paths(org_state), "dev_agent")
    assert reloaded is not None and reloaded.executor == "pi"
    # workspace agent.yaml updated
    from runtime.daemon.agent_config import load_agent_config
    assert load_agent_config(workspace)["executor"] == "pi"
    # bootstrap regenerated with the NEW provider
    assert mock_ctx.ensure_workspace_ready.call_args.kwargs.get("provider") == "pi"


def test_set_executor_invalid_returns_422_and_no_mutation(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Unknown executor is rejected at the boundary; org frontmatter untouched."""
    _seed_active_agent(org_state, "dev_agent", executor="claude")
    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/dev_agent/executor",
        json={"executor": "gpt"},
        headers=auth_headers,
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "invalid_executor"
    assert detail["got"] == "gpt"
    assert "claude" in detail["valid"]

    from runtime.orchestrator import prompt_loader
    unchanged = prompt_loader.load_agent(_paths(org_state), "dev_agent")
    assert unchanged is not None and unchanged.executor == "claude"


def test_set_executor_unknown_agent_returns_404(
    tmp_home, app, auth_headers,
) -> None:
    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/ghost/executor",
        json={"executor": "pi"},
        headers=auth_headers,
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "agent_not_found"


def _seed_claude_workspace_files(workspace) -> None:
    """Create the Claude-only workspace files that go stale on a switch away."""
    (workspace / "CLAUDE.md").write_text("# stale claude bootstrap\n")
    claude_dir = workspace / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("{}\n")


def test_set_executor_away_from_claude_warns_stale_by_default(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Default behavior warns about stale CLAUDE.md/.claude and deletes nothing."""
    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
    _seed_claude_workspace_files(workspace)

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        MockCB.return_value.ensure_workspace_ready.return_value = None
        r = TestClient(app).put(
            "/api/v1/orgs/alpha/agents/dev_agent/executor",
            json={"executor": "pi"},
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body["stale_files"]) == {"CLAUDE.md", ".claude"}
    assert body["cleaned"] is False
    assert body["removed"] == []
    # Nothing deleted without --clean.
    assert (workspace / "CLAUDE.md").exists()
    assert (workspace / ".claude").exists()


def test_set_executor_clean_deletes_stale_claude_files(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """--clean deletes the stale Claude-only files and reports them removed."""
    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
    _seed_claude_workspace_files(workspace)

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        MockCB.return_value.ensure_workspace_ready.return_value = None
        r = TestClient(app).put(
            "/api/v1/orgs/alpha/agents/dev_agent/executor",
            json={"executor": "pi", "clean": True},
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cleaned"] is True
    assert set(body["removed"]) == {"CLAUDE.md", ".claude"}
    assert not (workspace / "CLAUDE.md").exists()
    assert not (workspace / ".claude").exists()


def test_set_executor_to_claude_reports_no_stale(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Switching TO Claude reports no stale files — the symmetric case (stale
    AGENTS.md/.agents) is deliberately out of scope for this change."""
    _seed_active_agent(org_state, "dev_agent", executor="codex")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: codex\n")
    (workspace / "AGENTS.md").write_text("# codex bootstrap\n")

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        MockCB.return_value.ensure_workspace_ready.return_value = None
        r = TestClient(app).put(
            "/api/v1/orgs/alpha/agents/dev_agent/executor",
            json={"executor": "claude"},
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stale_files"] == []
    assert body["cleaned"] is False
    # AGENTS.md is intentionally left untouched (out of scope, not deleted).
    assert (workspace / "AGENTS.md").exists()


# ---------------------------------------------------------------------------
# init-agent executor-drift WARN (additive SSE event; no auto-reconcile)
# ---------------------------------------------------------------------------

def _stream_init_events(app, auth_headers, agent: str) -> list[dict]:
    import json as _json
    events: list[dict] = []
    client = TestClient(app)
    with client.stream(
        "POST", "/api/v1/orgs/alpha/agents/init",
        json={"agent": agent}, headers=auth_headers,
    ) as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data:"):
                events.append(_json.loads(line[len("data:"):].strip()))
    return events


def test_init_emits_executor_drift_and_does_not_reconcile(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """When org frontmatter != workspace agent.yaml on an EXISTING workspace,
    init emits an executor_drift event and changes nothing."""
    _seed_active_agent(org_state, "dev_agent", executor="pi")  # org wants pi
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")  # ws still claude

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None
        mock_ctx.create_agent_dirs.return_value = None
        events = _stream_init_events(app, auth_headers, "dev_agent")

    drift = [e for e in events if e.get("phase") == "executor_drift"]
    assert len(drift) == 1, events
    assert drift[0]["agent"] == "dev_agent"
    assert drift[0]["org_executor"] == "pi"
    assert drift[0]["workspace_executor"] == "claude"
    assert "set-executor" in drift[0]["hint"]
    assert "--executor pi" in drift[0]["hint"]

    # No silent auto-reconcile on either surface.
    from runtime.daemon.agent_config import load_agent_config
    assert load_agent_config(workspace)["executor"] == "claude"
    from runtime.orchestrator import prompt_loader
    assert prompt_loader.load_agent(_paths(org_state), "dev_agent").executor == "pi"


def test_init_no_drift_event_when_aligned(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """No executor_drift event when org frontmatter and agent.yaml agree."""
    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None
        mock_ctx.create_agent_dirs.return_value = None
        events = _stream_init_events(app, auth_headers, "dev_agent")

    assert [e for e in events if e.get("phase") == "executor_drift"] == []


# ---------------------------------------------------------------------------
# THR-067: per-agent model selection — set-executor preserves model
# ---------------------------------------------------------------------------


def test_set_executor_preserves_model_on_both_surfaces(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """After set-model, switching executor must preserve the model on both
    org frontmatter and workspace agent.yaml."""
    from runtime.orchestrator import prompt_loader
    from runtime.daemon.agent_config import load_agent_config

    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")

    # Step 1: set model
    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/dev_agent/model",
        json={"model": "claude-sonnet-4-20250514"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["after"] == "claude-sonnet-4-20250514"

    # Step 2: switch executor
    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.ensure_workspace_ready.return_value = None
        r = TestClient(app).put(
            "/api/v1/orgs/alpha/agents/dev_agent/executor",
            json={"executor": "pi"},
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text

    # Verify model preserved on org frontmatter
    reloaded = prompt_loader.load_agent(_paths(org_state), "dev_agent")
    assert reloaded is not None
    assert reloaded.model == "claude-sonnet-4-20250514"

    # Verify model preserved in workspace agent.yaml
    ws_cfg = load_agent_config(workspace)
    assert ws_cfg.get("model") == "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# THR-067: per-agent model selection — manage-agent update model persistence
# ---------------------------------------------------------------------------


def test_manage_agent_update_set_model(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Update with explicit non-null model sets it on both surfaces."""
    from runtime.orchestrator import prompt_loader
    from runtime.daemon.agent_config import load_agent_config

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        MockCB.return_value.ensure_workspace_ready.return_value = None
        r = TestClient(app).post(
            "/api/v1/orgs/alpha/agents/manage",
            json={
                "action": "update",
                "name": "dev_agent",
                "task_id": _EH_TASK,
                "session_id": _EH_SESSION,
                "model": "claude-sonnet-4-20250514",
            },
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text

    # org frontmatter has model
    reloaded = prompt_loader.load_agent(_paths(org_state), "dev_agent")
    assert reloaded is not None
    assert reloaded.model == "claude-sonnet-4-20250514"

    # workspace agent.yaml has model
    ws_cfg = load_agent_config(workspace)
    assert ws_cfg.get("model") == "claude-sonnet-4-20250514"


def test_manage_agent_update_clear_model_explicit_null(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Update with explicit null model clears it on both surfaces."""
    from runtime.orchestrator import prompt_loader
    from runtime.daemon.agent_config import load_agent_config

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text(
        "repos: {}\nexecutor: claude\nmodel: claude-sonnet-4-20250514\n"
    )

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        MockCB.return_value.ensure_workspace_ready.return_value = None
        r = TestClient(app).post(
            "/api/v1/orgs/alpha/agents/manage",
            json={
                "action": "update",
                "name": "dev_agent",
                "task_id": _EH_TASK,
                "session_id": _EH_SESSION,
                "model": None,
            },
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text

    # org frontmatter cleared
    reloaded = prompt_loader.load_agent(_paths(org_state), "dev_agent")
    assert reloaded is not None
    assert reloaded.model is None

    # workspace agent.yaml cleared
    ws_cfg = load_agent_config(workspace)
    assert "model" not in ws_cfg, f"model key should be absent, got {ws_cfg}"


def test_manage_agent_update_omit_model_preserves(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Update without model field preserves existing model on both surfaces."""
    from runtime.orchestrator import prompt_loader
    from runtime.daemon.agent_config import load_agent_config

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")

    # Set model on both surfaces first via the set-model endpoint
    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/dev_agent/model",
        json={"model": "claude-sonnet-4-20250514"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        MockCB.return_value.ensure_workspace_ready.return_value = None
        r = TestClient(app).post(
            "/api/v1/orgs/alpha/agents/manage",
            json={
                "action": "update",
                "name": "dev_agent",
                "task_id": _EH_TASK,
                "session_id": _EH_SESSION,
                # model omitted entirely
                "executor": "codex",
            },
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text

    # org frontmatter preserves model
    reloaded = prompt_loader.load_agent(_paths(org_state), "dev_agent")
    assert reloaded is not None
    assert reloaded.model == "claude-sonnet-4-20250514"

    # workspace agent.yaml preserves model (set_executor doesn't clobber model)
    ws_cfg = load_agent_config(workspace)
    assert ws_cfg.get("model") == "claude-sonnet-4-20250514"


def test_manage_agent_enroll_with_model_persists(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Enroll with a model persists it in the pending agent file."""
    from runtime.orchestrator import prompt_loader

    _activate_eh_session(org_state)
    paths = _paths(org_state)

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
        json={
            "action": "enroll",
            "name": "new_worker",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "description": "A test worker",
            "system_prompt": "prompt",
            "executor": "claude",
            "model": "claude-sonnet-4-20250514",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    # Pending file contains the model
    pending = prompt_loader.load_pending_agent(paths, "new_worker")
    assert pending is not None
    assert pending.model == "claude-sonnet-4-20250514"
    assert pending.executor == "claude"

    # Cleanup: delete pending file
    (paths.agents_dir / "new_worker.pending.md").unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# THR-055: switch-time skill materialization
# ---------------------------------------------------------------------------


def _system_contract_ids_for_context(context: str, workspace) -> set[str]:
    """Resolve expected system-contract IDs for a session context."""
    from runtime.skills.system_contracts import (
        SessionContext,
        resolve_system_contracts_for_session,
    )
    ctx = SessionContext(context)
    contracts = resolve_system_contracts_for_session(ctx, workspace=workspace)
    return {sc.id for sc in contracts}


def test_set_executor_claude_to_codex_materializes_skills(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Claude→Codex switch leaves .agents/skills/<id>/SKILL.md for all
    contracts any future session context could need (union across all 4
    contexts). Files exist BEFORE any new session starts."""
    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
    # Simulate repos so make-worktree contract is injected
    (workspace / "repos" / "test" / ".git").mkdir(parents=True)

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        MockCB.return_value.ensure_workspace_ready.return_value = None
        r = TestClient(app).put(
            "/api/v1/orgs/alpha/agents/dev_agent/executor",
            json={"executor": "codex"},
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["materialization_errors"] == []

    # Union of all 4 contexts: start-task(task,wake), jobs(all),
    # make-worktree(all,requires_repos), thread(task,thread,wake), dream(dream)
    all_contracts: set[str] = set()
    for ctx in ("task", "thread", "wake", "dream"):
        all_contracts |= _system_contract_ids_for_context(ctx, workspace)

    assert len(all_contracts) >= 1, "at least one contract should be materialized"
    for sid in all_contracts:
        marker = workspace / ".agents" / "skills" / sid / "SKILL.md"
        assert marker.is_file(), (
            f"Expected {marker} to exist after claude→codex switch"
        )


def test_set_executor_codex_to_claude_materializes_skills(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Codex→Claude switch leaves .claude/skills/<id>/SKILL.md for all
    contracts any future session context could need (union across all 4
    contexts). Files exist BEFORE any new session starts."""
    _seed_active_agent(org_state, "dev_agent", executor="codex")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: codex\n")
    (workspace / "repos" / "test" / ".git").mkdir(parents=True)

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        MockCB.return_value.ensure_workspace_ready.return_value = None
        r = TestClient(app).put(
            "/api/v1/orgs/alpha/agents/dev_agent/executor",
            json={"executor": "claude"},
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["materialization_errors"] == []

    all_contracts: set[str] = set()
    for ctx in ("task", "thread", "wake", "dream"):
        all_contracts |= _system_contract_ids_for_context(ctx, workspace)

    assert len(all_contracts) >= 1, "at least one contract should be materialized"
    for sid in all_contracts:
        marker = workspace / ".claude" / "skills" / sid / "SKILL.md"
        assert marker.is_file(), (
            f"Expected {marker} to exist after codex→claude switch"
        )


def test_set_executor_materialization_failure_non_fatal(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """When ensure_system_contracts_materialized raises, the switch still
    succeeds (steps 1-3 have already mutated state). The error is surfaced
    in the response body, not as a 500."""
    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")

    from runtime.orchestrator.workspace_adapters import (
        SystemContractMaterializationError,
    )

    with patch(
        "runtime.daemon.routes.agents.ContextBuilder"
    ) as MockCB, patch(
        "runtime.daemon.routes.agents.ensure_system_contracts_materialized"
    ) as mock_mat:
        MockCB.return_value.ensure_workspace_ready.return_value = None
        mock_mat.side_effect = SystemContractMaterializationError(
            missing_contracts=["start-task"],
            workspace=workspace,
            provider="codex",
        )
        r = TestClient(app).put(
            "/api/v1/orgs/alpha/agents/dev_agent/executor",
            json={"executor": "codex"},
            headers=auth_headers,
        )

    # Switch still succeeds — step 1-3 mutations are already applied
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["before"]["org_executor"] == "claude"
    assert body["after"]["org_executor"] == "codex"
    # Materialization failure is surfaced non-fatally
    assert len(body["materialization_errors"]) == 4, (
        f"Expected 4 materialization errors (one per context), got {body['materialization_errors']}"
    )
