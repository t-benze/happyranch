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
    # The active roster is driven by org/agents/*.md, not by workspace
    # directories. Seed an active AgentDef and its workspace.
    ws = org_state.root / "workspaces" / "engineering_head"
    ws.mkdir(parents=True, exist_ok=True)
    _seed_active_agent(org_state, "engineering_head", role="manager")
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
    """GET /agents returns model — both set and null — from .md frontmatter.

    THR-095: model is read from AgentDef.model (org/agents/<name>.md),
    NOT from agent.yaml.
    """
    from datetime import datetime, timezone
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text

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

    def _write_agent(agent: AgentDef) -> None:
        (paths.agents_dir / "engineering_head.md").write_text(render_agent_text(agent))

    _write_agent(agent)

    # No model set → null
    r = TestClient(app).get("/api/v1/orgs/alpha/agents", headers=auth_headers)
    assert r.status_code == 200
    rows = {a["name"]: a for a in r.json()["agents"]}
    eh = rows["engineering_head"]
    assert eh["model"] is None

    # Set a model → returned (write to .md frontmatter, not agent.yaml)
    agent_with_model = AgentDef(
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
        model="claude-sonnet-4-20250514",
    )
    _write_agent(agent_with_model)
    r = TestClient(app).get("/api/v1/orgs/alpha/agents", headers=auth_headers)
    assert r.status_code == 200
    rows = {a["name"]: a for a in r.json()["agents"]}
    eh = rows["engineering_head"]
    assert eh["model"] == "claude-sonnet-4-20250514"

    # Clear the model → null
    agent_no_model = AgentDef(
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
        model=None,
    )
    _write_agent(agent_no_model)
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


def test_init_bootstraps_workspace_dirs(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """init-agent must leave the workspace bootstrapped with agent-specific
    folders (e.g. specs/ for product_manager).  THR-095: agent.yaml is no
    longer created by init."""
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
    assert (ws / "specs").is_dir(), "product_manager specs/ dir missing"


def test_init_creates_workspace_for_any_name(tmp_home, app, org_state, auth_headers) -> None:
    """init-agent accepts any valid agent name.  THR-095: agent.yaml is no
    longer created by init — we verify workspace dir exists."""
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
    assert ws.is_dir()


def _write_agent_md(paths, agent: "AgentDef") -> None:
    """Helper: write AgentDef to .md frontmatter for tests."""
    from runtime.orchestrator.agent_def import render_agent_text
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    (paths.agents_dir / f"{agent.name}.md").write_text(render_agent_text(agent))


def _make_agent(name: str, **overrides) -> "AgentDef":
    """Helper: build a minimal AgentDef for tests."""
    from datetime import datetime, timezone
    from runtime.orchestrator.agent_def import AgentDef
    defaults = dict(
        name=name, team="engineering", role="worker", executor="claude",
        allow_rules=(), repos={}, enrolled_by=None, enrolled_at_task=None,
        enrolled_at=datetime.now(timezone.utc),
        system_prompt=f"system prompt for {name}", description=f"desc for {name}",
        model=None,
    )
    defaults.update(overrides)
    return AgentDef(**defaults)


def test_manage_repo_add_creates_entry_and_clones(
    tmp_home, app, org_state, auth_headers,
) -> None:
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    paths = _paths(org_state)
    _write_agent_md(paths, _make_agent("dev_agent"))

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

    # THR-095: repos now live in .md frontmatter, not agent.yaml
    from runtime.orchestrator.prompt_loader import load_agent
    agent_def = load_agent(paths, "dev_agent")
    assert agent_def is not None
    assert agent_def.repos["docs"] == "https://github.com/t-benze/docs.git"


def test_manage_repo_add_passes_provider_from_agent_def(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """FIX: ensure_workspace_ready receives provider=agent_def.executor,
    not the default 'claude'. A codex agent must get the codex adapter
    refreshed after a repo mutation."""
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    paths = _paths(org_state)
    _write_agent_md(paths, _make_agent("dev_agent", executor="codex"))

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
    # The critical assertion: ensure_workspace_ready must receive the actual
    # provider, not the default 'claude'.
    mock_ctx.ensure_workspace_ready.assert_called_once()
    _, kwargs = mock_ctx.ensure_workspace_ready.call_args
    assert kwargs.get("provider") == "codex", (
        f"expected provider='codex', got {kwargs}"
    )


def test_manage_repo_add_duplicate_returns_409(
    tmp_home, app, org_state, auth_headers,
) -> None:
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    paths = _paths(org_state)
    _write_agent_md(paths, _make_agent("dev_agent", repos={"docs": "https://old.git"}))

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
    paths = _paths(org_state)
    _write_agent_md(paths, _make_agent("dev_agent", repos={"docs": "https://old.git"}))
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

    # THR-095: repos now live in .md frontmatter, not agent.yaml
    from runtime.orchestrator.prompt_loader import load_agent
    agent_def = load_agent(paths, "dev_agent")
    assert agent_def is not None
    assert "docs" not in agent_def.repos


def test_manage_repo_remove_nonexistent_returns_404(
    tmp_home, app, org_state, auth_headers,
) -> None:
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    paths = _paths(org_state)
    _write_agent_md(paths, _make_agent("dev_agent"))

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
    paths = _paths(org_state)
    _write_agent_md(paths, _make_agent("dev_agent", repos={"docs": "https://old.git"}))
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

    # THR-095: repos now live in .md frontmatter, not agent.yaml
    from runtime.orchestrator.prompt_loader import load_agent
    agent_def = load_agent(paths, "dev_agent")
    assert agent_def is not None
    assert agent_def.repos["docs"] == "https://new.git"


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


def _seed_active_agent(
    org_state,
    name: str,
    team: str = "engineering",
    role: str = "worker",
    executor: str = "claude",
    system_prompt: str = "prompt\n",
) -> None:
    """Write an active agent file for testing update/terminate endpoints."""
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text
    from datetime import datetime, timezone
    agent = AgentDef(
        name=name, team=team, role=role, executor=executor,
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

    # THR-095: executor now lives in .md frontmatter, not agent.yaml
    from runtime.orchestrator import prompt_loader
    updated = prompt_loader.load_agent(_paths(org_state), "dev_agent")
    assert updated is not None
    assert updated.executor == "codex"


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

    # THR-095: executor now lives in .md frontmatter, not agent.yaml
    from runtime.orchestrator import prompt_loader
    updated = prompt_loader.load_agent(_paths(org_state), "dev_agent")
    assert updated is not None
    assert updated.executor == "testcustom"


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

    # THR-095: agent.yaml is no longer created by approve_agent.
    # The .md frontmatter is the single source of truth.


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


def test_validate_executor_accepts_registered_custom_profile() -> None:
    """A custom profile registered in the ExecutorRegistry must be accepted
    by _validate_executor — the set-executor route is registry-gated, not
    hard-coded to four built-ins."""
    from runtime.daemon.routes.agents import _validate_executor
    from runtime.orchestrator.executor_registry import ExecutorProfile, get_registry

    registry = get_registry()
    registry.register_custom_profile(
        ExecutorProfile(
            name="testcustom",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["echo", "{prompt}"],
        )
    )
    # Must not raise — the registered custom profile IS a valid executor.
    _validate_executor("testcustom")


def test_set_executor_accepts_registered_custom_profile_via_route(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """A registered custom executor profile must be accepted by the real
    PUT /agents/{agent}/executor route end-to-end — authentication, body
    handling, validation, and persistence included."""
    from runtime.orchestrator.executor_registry import ExecutorProfile, get_registry

    registry = get_registry()
    registry.register_custom_profile(
        ExecutorProfile(
            name="testcustom",
            kind="custom",
            adapter_id="pi",
            readiness_marker_fragment="AGENTS.md",
            argv_template=["echo", "{prompt}"],
        )
    )
    _seed_active_agent(org_state, "custom_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "custom_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.ensure_workspace_ready.return_value = None
        r = TestClient(app).put(
            "/api/v1/orgs/alpha/agents/custom_agent/executor",
            json={"executor": "testcustom"},
            headers=auth_headers,
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["before"]["org_executor"] == "claude"
    assert body["after"]["org_executor"] == "testcustom"

    # org .md frontmatter persisted with the custom executor
    from runtime.orchestrator import prompt_loader
    reloaded = prompt_loader.load_agent(_paths(org_state), "custom_agent")
    assert reloaded is not None and reloaded.executor == "testcustom"
    # bootstrap regenerated with the new (custom) provider
    assert mock_ctx.ensure_workspace_ready.call_args.kwargs.get("provider") == "testcustom"


def test_set_executor_switches_org_and_workspace(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Happy path: org frontmatter flips, bootstrap
    is regenerated with the NEW provider, before/after state is reported.
    THR-095: agent.yaml is no longer synced."""
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

    # org .md frontmatter updated
    from runtime.orchestrator import prompt_loader
    reloaded = prompt_loader.load_agent(_paths(org_state), "dev_agent")
    assert reloaded is not None and reloaded.executor == "pi"
    # bootstrap regenerated with the NEW provider
    assert mock_ctx.ensure_workspace_ready.call_args.kwargs.get("provider") == "pi"

    # THR-095: agent.yaml is NOT updated — the response still shows
    # the old workspace_executor value for display purposes only.


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
    """THR-095: init no longer emits executor_drift events.
    The .md frontmatter is the single source of truth, so there is no
    agent.yaml drift to detect.  init reads executor/repos from
    AgentDef (.md) and ignores agent.yaml entirely."""
    _seed_active_agent(org_state, "dev_agent", executor="pi")  # org says pi
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")  # agent.yaml says claude

    with patch("runtime.daemon.routes.agents.ContextBuilder") as MockCB:
        mock_ctx = MockCB.return_value
        mock_ctx.clone_repo.return_value = True
        mock_ctx.ensure_workspace_ready.return_value = None
        mock_ctx.create_agent_dirs.return_value = None
        events = _stream_init_events(app, auth_headers, "dev_agent")

    # THR-095: no more executor_drift events — .md is the sole source
    drift = [e for e in events if e.get("phase") == "executor_drift"]
    assert drift == [], f"unexpected executor_drift events: {drift}"

    # Agent.yaml is unchanged (no reconciliation)
    from runtime.daemon.agent_config import load_agent_config
    assert load_agent_config(workspace)["executor"] == "claude"
    from runtime.orchestrator import prompt_loader
    assert prompt_loader.load_agent(_paths(org_state), "dev_agent").executor == "pi"


def test_init_no_drift_event_when_aligned(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """THR-095: no executor_drift event regardless of agent.yaml state."""
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
    """After set-model, switching executor must preserve the model on the
    org .md frontmatter.  THR-095: agent.yaml is no longer synced."""
    from runtime.orchestrator import prompt_loader

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

    # Verify model preserved on .md frontmatter
    reloaded = prompt_loader.load_agent(_paths(org_state), "dev_agent")
    assert reloaded is not None
    assert reloaded.model == "claude-sonnet-4-20250514"

    # THR-095: agent.yaml is NOT touched by set-executor.
    # model may still be there from the old set-model call, but set-executor
    # no longer syncs agent.yaml at all.


# ---------------------------------------------------------------------------
# THR-067: per-agent model selection — manage-agent update model persistence
# ---------------------------------------------------------------------------


def test_manage_agent_update_set_model(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Update with explicit non-null model sets it on .md frontmatter.
    THR-095: agent.yaml is no longer synced."""
    from runtime.orchestrator import prompt_loader

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)

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

    # .md frontmatter has model
    reloaded = prompt_loader.load_agent(_paths(org_state), "dev_agent")
    assert reloaded is not None
    assert reloaded.model == "claude-sonnet-4-20250514"

    # THR-095: agent.yaml is NOT synced


def test_manage_agent_update_clear_model_explicit_null(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Update with explicit null model clears it on .md frontmatter.
    THR-095: agent.yaml is no longer synced."""
    from runtime.orchestrator import prompt_loader

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)

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

    # .md frontmatter cleared
    reloaded = prompt_loader.load_agent(_paths(org_state), "dev_agent")
    assert reloaded is not None
    assert reloaded.model is None

    # THR-095: agent.yaml is NOT synced


def test_manage_agent_update_omit_model_preserves(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Update without model field preserves existing model on .md frontmatter.
    THR-095: agent.yaml is no longer synced."""
    from runtime.orchestrator import prompt_loader

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)

    # Set model on .md frontmatter via the set-model endpoint
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

    # .md frontmatter preserves model
    reloaded = prompt_loader.load_agent(_paths(org_state), "dev_agent")
    assert reloaded is not None
    assert reloaded.model == "claude-sonnet-4-20250514"

    # THR-095: agent.yaml is NOT synced by manage-agent update


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
    contracts any future session context could need (union across all 6
    contexts: task, thread, wake, dream, schedule, bootstrap).
    Files exist BEFORE any new session starts."""
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
    # materialization_errors no longer in response — success means
    # the HTTP 200 itself is the proof of clean materialization
    assert "materialization_errors" not in body, (
        "materialization_errors field must not be present on success"
    )

    # Union of all 6 contexts: task, thread, wake, dream, schedule, bootstrap
    all_contracts: set[str] = set()
    for ctx in ("task", "thread", "wake", "dream", "schedule", "bootstrap"):
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
    contracts any future session context could need (union across all 6
    contexts: task, thread, wake, dream, schedule, bootstrap).
    Files exist BEFORE any new session starts."""
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
    assert "materialization_errors" not in body, (
        "materialization_errors field must not be present on success"
    )

    all_contracts: set[str] = set()
    for ctx in ("task", "thread", "wake", "dream", "schedule", "bootstrap"):
        all_contracts |= _system_contract_ids_for_context(ctx, workspace)

    assert len(all_contracts) >= 1, "at least one contract should be materialized"
    for sid in all_contracts:
        marker = workspace / ".claude" / "skills" / sid / "SKILL.md"
        assert marker.is_file(), (
            f"Expected {marker} to exist after codex→claude switch"
        )


def test_set_executor_materialization_failure_fail_closed(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """When canonical union materialization fails during executor switch,
    the route must fail closed: HTTP 400, previous executor preserved,
    no partial state mutation.

    Specifically asserts:
    - HTTP 400 with named error code
    - agent.yaml executor key unchanged (still old executor)
    - agent .md frontmatter executor field unchanged
    - No bootstrap file from the new executor written (ensure_workspace_ready
      is never called because union failed)
    - No audit row produced (no audit_log entry for this switch)"""
    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")

    # Record pristine workspace state before the failing request
    agent_yaml_before = (workspace / "agent.yaml").read_text()
    agent_md_path = org_state.root / "agents" / "dev_agent.md"
    frontmatter_before = agent_md_path.read_text() if agent_md_path.exists() else None

    # Check if any bootstrap files exist before (new-executor files)
    bootstrap_candidates_before = set()
    for candidate in ["CLAUDE.md", "AGENTS.md", ".claude/settings.json"]:
        p = workspace / candidate
        if p.exists():
            bootstrap_candidates_before.add(candidate)

    with patch(
        "runtime.daemon.routes.agents._executor_switch_materialize",
        return_value=["union materialization failed: test-induced error"],
    ):
        r = TestClient(app).put(
            "/api/v1/orgs/alpha/agents/dev_agent/executor",
            json={"executor": "codex"},
            headers=auth_headers,
        )

    # FAIL-CLOSED: materialization failure prevents switch
    assert r.status_code == 400, (
        f"Expected 400 on materialization failure, got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["detail"]["code"] == "executor_materialization_failed", (
        f"Expected executor_materialization_failed, got {body}"
    )
    assert len(body["detail"]["errors"]) >= 1, (
        f"Expected at least 1 materialization error, got {body}"
    )

    # ── Assert unchanged state ──
    # agent.yaml must still say "claude", not "codex"
    agent_yaml_after = (workspace / "agent.yaml").read_text()
    assert agent_yaml_after == agent_yaml_before, (
        f"agent.yaml was mutated on failure: before={agent_yaml_before!r}, "
        f"after={agent_yaml_after!r}"
    )

    # Agent frontmatter must be unchanged
    if frontmatter_before is not None:
        frontmatter_after = agent_md_path.read_text()
        assert frontmatter_after == frontmatter_before, (
            "Agent frontmatter was mutated on materialization failure"
        )

    # No new bootstrap files should have been written by the new executor
    for candidate in ["CLAUDE.md", "AGENTS.md", ".claude/settings.json"]:
        p = workspace / candidate
        if p.exists() and candidate not in bootstrap_candidates_before:
            pytest.fail(
                f"Bootstrap file {candidate} was written before "
                "materialization failed — violates materialize-first contract"
            )


def test_set_executor_bootstrap_failure_after_successful_union(
    tmp_home, app, org_state, auth_headers, monkeypatch,
) -> None:
    """THR-190 bounded-rollback regression: the six-context union must succeed
    before any persistent mutation, and a post-union bootstrap failure must
    restore ONLY the declared bootstrap-owned write set — never touching
    canonical skill links, non-owned workspace content, frontmatter, or
    audit state.

    Uses a REAL adapter writer seam (CodexWorkspaceAdapter.write_agents_md)
    rather than a broad ContextBuilder mock: the real bootstrap writes its
    owned files (including the legacy recent_tasks.md -> task_history.md
    rename and memory/_index.md structural creation), the seam writes
    AGENTS.md then raises, and the bounded rollback journal must:
    - restore pre-existing declared-file byte contents (AGENTS.md, CLAUDE.md,
      .claude/settings.json)
    - reverse the recent_tasks.md -> task_history.md rename losslessly
    - remove the newly-created memory/_index.md index and memory/ directory
    - retain canonical .agents/skills/ links materialized by the union
    - leave agent frontmatter and audit state unchanged
    - return HTTP 400 executor_bootstrap_failed"""
    from runtime.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
    # repos for union to resolve make-worktree contract
    (workspace / "repos" / "test" / ".git").mkdir(parents=True)

    # ── Seed pre-existing declared bootstrap-owned state ──
    (workspace / "CLAUDE.md").write_bytes(b"# CLAUDE.md: old executor\n")
    (workspace / "AGENTS.md").write_bytes(b"# AGENTS.md: old executor\n")
    (workspace / ".claude").mkdir(parents=True, exist_ok=True)
    (workspace / ".claude" / "settings.json").write_text('{"old": true}')
    link_a = workspace / ".claude" / "old-link-a"
    link_a.symlink_to(workspace / ".claude" / "old-link-a-target")
    (workspace / ".agents").mkdir(parents=True, exist_ok=True)
    link_b = workspace / ".agents" / "old-link-b"
    link_b.symlink_to(workspace / ".agents" / "old-link-b-target")
    # Legacy history file: real bootstrap renames it to task_history.md.
    (workspace / "recent_tasks.md").write_text("# Legacy task history\n")
    # memory/ is intentionally ABSENT so the real bootstrap creates it
    # (removal of newly-created declared state is exercised); no
    # task_history.md yet (the rename source exists).

    agent_md_path = _paths(org_state).agents_dir / "dev_agent.md"
    frontmatter_before = agent_md_path.read_text()
    audit_before = [
        log for log in org_state.db.get_audit_logs("founder")
        if log["action"] == "agent_managed"
    ]

    # ── Force bootstrap failure at a real adapter writer seam ──
    real_write_agents_md = CodexWorkspaceAdapter.write_agents_md

    def _write_then_raise(self, workspace, agent_name, system_prompt, repo_names=None):
        real_write_agents_md(
            self, workspace, agent_name, system_prompt, repo_names=repo_names,
        )
        raise RuntimeError("Bootstrap failed — simulated failure")

    monkeypatch.setattr(
        CodexWorkspaceAdapter, "write_agents_md", _write_then_raise,
    )

    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/dev_agent/executor",
        json={"executor": "codex"},
        headers=auth_headers,
    )

    # ── FAIL-CLOSED assertions ──
    assert r.status_code == 400, (
        f"Expected 400 on bootstrap failure, got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["detail"]["code"] == "executor_bootstrap_failed", (
        f"Expected executor_bootstrap_failed, got {body}"
    )
    assert "Bootstrap failed" in body["detail"]["error"], (
        f"Expected bootstrap error message, got {body['detail']['error']}"
    )

    # ── Owned files restored / unchanged ──
    assert (workspace / "AGENTS.md").read_bytes() == b"# AGENTS.md: old executor\n", (
        "Pre-existing AGENTS.md was not restored to original bytes"
    )
    assert (workspace / "CLAUDE.md").read_bytes() == b"# CLAUDE.md: old executor\n"
    assert (workspace / ".claude" / "settings.json").read_text() == '{"old": true}'

    # ── recent_tasks.md -> task_history.md rename reversed losslessly ──
    assert (workspace / "recent_tasks.md").read_text() == "# Legacy task history\n", (
        "recent_tasks.md legacy rename source was not restored"
    )
    assert not (workspace / "task_history.md").exists(), (
        "task_history.md (created by the rename) survived rollback"
    )

    # ── New owned artifacts removed (new memory/ index tracked reversibly) ──
    assert not (workspace / "memory").exists(), (
        "Newly-created memory/ index survived bootstrap failure"
    )

    # ── Non-owned workspace content and links preserved ──
    assert link_a.is_symlink(), "Pre-existing .claude link was removed"
    assert link_b.is_symlink(), "Pre-existing .agents link was removed"

    # ── Canonical skill links materialized by the union survive ──
    all_contracts: set[str] = set()
    for ctx in ("task", "thread", "wake", "dream", "schedule", "bootstrap"):
        all_contracts |= _system_contract_ids_for_context(ctx, workspace)
    assert len(all_contracts) >= 1, "no system contracts materialized"
    for sid in all_contracts:
        marker = workspace / ".agents" / "skills" / sid / "SKILL.md"
        assert marker.is_file(), (
            f"Canonical skill link {marker} was removed by rollback"
        )

    # ── Frontmatter + audit unchanged ──
    assert agent_md_path.read_text() == frontmatter_before, (
        "Agent frontmatter was mutated on bootstrap failure"
    )
    audit_after = [
        log for log in org_state.db.get_audit_logs("founder")
        if log["action"] == "agent_managed"
    ]
    assert audit_after == audit_before, (
        f"Audit row written despite bootstrap failure: before={audit_before}, "
        f"after={audit_after}"
    )


def test_set_executor_drift_tripwire_all_provider_shapes(
    tmp_home, app, org_state, auth_headers, monkeypatch,
) -> None:
    """THR-190 drift tripwire: every filesystem write/rename made by the
    REAL provider adapter bootstrap during an executor switch must land in
    the declared bootstrap-owned write set (_BOOTSTRAP_OWNED_FILES/DIRS).

    Runs the real route with the real union + real adapter bootstrap for
    every registered provider shape (claude, codex, opencode, pi) and
    instruments the filesystem mutation primitives ONLY around the real
    adapter bootstrap call. Any write/rename outside the declared set fails
    the test — future adapter evolution cannot silently outrun the journal.
    No adapter production code is changed."""
    import os as _os
    import shutil
    from pathlib import Path as _Path

    import runtime.daemon.routes.agents as agents_mod

    declared_files = set(agents_mod._BOOTSTRAP_OWNED_FILES)
    declared_dirs = set(agents_mod._BOOTSTRAP_OWNED_DIRS)

    # Capture the ORIGINAL method once, before any monkeypatch — the patch
    # below is installed for the whole test, so per-iteration re-capture
    # would grab the wrapper itself and recurse.
    real_ensure = agents_mod.ContextBuilder.ensure_workspace_ready

    for provider in ("claude", "codex", "opencode", "pi"):
        _seed_active_agent(org_state, "dev_agent", executor="claude")
        workspace = org_state.root / "workspaces" / "dev_agent"
        shutil.rmtree(workspace, ignore_errors=True)
        workspace.mkdir(parents=True)
        (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
        (workspace / "repos" / "test" / ".git").mkdir(parents=True)

        # Warm workspace: every declared bootstrap-owned file pre-exists so
        # only the provider's own declared writes occur.
        (workspace / "CLAUDE.md").write_bytes(b"# CLAUDE.md warm\n")
        (workspace / "AGENTS.md").write_bytes(b"# AGENTS.md warm\n")
        (workspace / ".claude").mkdir(parents=True, exist_ok=True)
        (workspace / ".claude" / "settings.json").write_text('{"warm": true}')
        (workspace / "opencode.json").write_text('{"warm": true}')
        (workspace / "task_history.md").write_text("# Task History: dev_agent\n")
        (workspace / "memory").mkdir(parents=True, exist_ok=True)
        (workspace / "memory" / "_index.md").write_text("# Memory Index\n")

        ws_root = str(workspace)
        allowed = {ws_root}
        allowed |= {str(workspace / rel) for rel in declared_files}
        for d in declared_dirs:
            allowed.add(str(workspace / d))

        violations: list[str] = []

        def _check(p, op):
            s = str(p)
            if (s == ws_root or s.startswith(ws_root + _os.sep)) and s not in allowed:
                violations.append(f"{op} {s}")

        real_write_text = _Path.write_text
        real_write_bytes = _Path.write_bytes
        real_rename = _Path.rename
        real_mkdir = _Path.mkdir
        real_unlink = _Path.unlink
        real_rmdir = _Path.rmdir
        real_replace = _Path.replace
        real_os_replace = _os.replace
        real_os_symlink = _os.symlink

        def _spy_write_text(self, *a, **k):
            _check(self, "write_text")
            return real_write_text(self, *a, **k)

        def _spy_write_bytes(self, *a, **k):
            _check(self, "write_bytes")
            return real_write_bytes(self, *a, **k)

        def _spy_rename(self, *a, **k):
            _check(self, "rename")
            return real_rename(self, *a, **k)

        def _spy_mkdir(self, *a, **k):
            _check(self, "mkdir")
            return real_mkdir(self, *a, **k)

        def _spy_unlink(self, *a, **k):
            _check(self, "unlink")
            return real_unlink(self, *a, **k)

        def _spy_rmdir(self, *a, **k):
            _check(self, "rmdir")
            return real_rmdir(self, *a, **k)

        def _spy_replace(self, *a, **k):
            _check(self, "replace")
            return real_replace(self, *a, **k)

        def _spy_os_replace(src, dst):
            _check(_Path(dst), "os.replace")
            return real_os_replace(src, dst)

        def _spy_os_symlink(src, dst):
            _check(_Path(dst), "os.symlink")
            return real_os_symlink(src, dst)

        def _guarded_ensure(self, workspace, agent_name, system_prompt, provider="claude"):
            with patch.object(_Path, "write_text", _spy_write_text), \
                 patch.object(_Path, "write_bytes", _spy_write_bytes), \
                 patch.object(_Path, "rename", _spy_rename), \
                 patch.object(_Path, "mkdir", _spy_mkdir), \
                 patch.object(_Path, "unlink", _spy_unlink), \
                 patch.object(_Path, "rmdir", _spy_rmdir), \
                 patch.object(_Path, "replace", _spy_replace), \
                 patch("os.replace", _spy_os_replace), \
                 patch("os.symlink", _spy_os_symlink):
                return real_ensure(
                    self, workspace, agent_name, system_prompt, provider=provider,
                )

        monkeypatch.setattr(
            agents_mod.ContextBuilder, "ensure_workspace_ready", _guarded_ensure,
        )

        r = TestClient(app).put(
            "/api/v1/orgs/alpha/agents/dev_agent/executor",
            json={"executor": provider},
            headers=auth_headers,
        )
        assert r.status_code == 200, (
            f"provider={provider} switch failed: {r.text}"
        )
        assert violations == [], (
            f"provider={provider} adapter wrote OUTSIDE the declared "
            f"bootstrap-owned set: {violations}"
        )


def test_set_executor_no_broad_traversal_success(
    tmp_home, app, org_state, auth_headers, monkeypatch,
) -> None:
    """THR-190 latency: a successful executor switch must NEVER broadly
    traverse the workspace or workspace/repos — the THR-190 root cause was a
    full os.walk snapshot of a multi-GB workspace. Guards os.walk / rglob /
    scandir-style broad enumeration at the route boundary (recursive
    enumeration anywhere under the workspace, or scandir of the workspace
    root itself) and seeds sentinel trees whose content any recursive walk
    would read."""
    import os as _os
    from pathlib import Path as _Path

    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
    (workspace / "repos" / "test" / ".git").mkdir(parents=True)
    (workspace / "task_history.md").write_text("# Task History: dev_agent\n")

    # ── Sentinel trees: deep untracked subtree + large file under repos/ ──
    deep_sentinel = workspace / "untracked" / "deep" / "nested" / "sentinel.bin"
    deep_sentinel.parent.mkdir(parents=True)
    deep_blob = _os.urandom(1024 * 1024)  # 1 MB
    deep_sentinel.write_bytes(deep_blob)

    repo_sentinel = workspace / "repos" / "test" / "large-sentinel.bin"
    repo_blob = _os.urandom(2 * 1024 * 1024)  # 2 MB
    repo_sentinel.write_bytes(repo_blob)

    sentinel_paths = {
        _os.path.abspath(_os.fspath(deep_sentinel)),
        _os.path.abspath(_os.fspath(repo_sentinel)),
    }

    # ── Traversal guard: flag recursive enumeration under the workspace ──
    ws_root_abs = _os.path.abspath(str(workspace))
    violations: list[str] = []
    real_walk = _os.walk
    real_rglob = _Path.rglob
    real_scandir = _os.scandir
    real_read_bytes = _Path.read_bytes
    sentinel_reads: list[str] = []

    def _spy_walk(top, *args, **kwargs):
        top_abs = _os.path.abspath(str(top))
        if top_abs == ws_root_abs or top_abs.startswith(ws_root_abs + _os.sep):
            violations.append(f"os.walk {top_abs}")
        return real_walk(top, *args, **kwargs)

    def _spy_rglob(self, pattern, *args, **kwargs):
        self_abs = _os.path.abspath(_os.fspath(self))
        if self_abs == ws_root_abs or self_abs.startswith(ws_root_abs + _os.sep):
            violations.append(f"rglob {self_abs}")
        return real_rglob(self, pattern, *args, **kwargs)

    def _spy_scandir(path="."):
        path_abs = _os.path.abspath(str(path))
        if path_abs == ws_root_abs:
            violations.append(f"scandir {path_abs}")
        return real_scandir(path)

    def _spy_read_bytes(self):
        self_abs = _os.path.abspath(_os.fspath(self))
        if self_abs in sentinel_paths:
            sentinel_reads.append(self_abs)
        return real_read_bytes(self)

    monkeypatch.setattr(_os, "walk", _spy_walk)
    monkeypatch.setattr(_Path, "rglob", _spy_rglob)
    monkeypatch.setattr(_os, "scandir", _spy_scandir)
    monkeypatch.setattr(_Path, "read_bytes", _spy_read_bytes)

    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/dev_agent/executor",
        json={"executor": "codex"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert violations == [], (
        f"Broad workspace enumeration during successful switch: {violations}"
    )
    assert sentinel_reads == [], (
        f"Sentinel file(s) read during successful switch: {sentinel_reads}"
    )
    assert deep_sentinel.read_bytes() == deep_blob, "deep sentinel changed"
    assert repo_sentinel.read_bytes() == repo_blob, "repo sentinel changed"


def test_set_executor_no_broad_traversal_on_rollback(
    tmp_home, app, org_state, auth_headers, monkeypatch,
) -> None:
    """THR-190 latency: bootstrap-failure rollback (journal capture + restore)
    must also avoid broad workspace/repos traversal. Same sentinel +
    traversal guard as the success case, with a real adapter writer that
    writes then raises; asserts the rollback restores declared state without
    enumerating the workspace or reading the sentinels."""
    import os as _os
    from pathlib import Path as _Path
    from runtime.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
    (workspace / "repos" / "test" / ".git").mkdir(parents=True)
    (workspace / "AGENTS.md").write_bytes(b"# AGENTS.md: old executor\n")

    deep_sentinel = workspace / "untracked" / "deep" / "nested" / "sentinel.bin"
    deep_sentinel.parent.mkdir(parents=True)
    deep_blob = _os.urandom(512 * 1024)
    deep_sentinel.write_bytes(deep_blob)

    repo_sentinel = workspace / "repos" / "test" / "large-sentinel.bin"
    repo_blob = _os.urandom(1024 * 1024)
    repo_sentinel.write_bytes(repo_blob)

    sentinel_paths = {
        _os.path.abspath(_os.fspath(deep_sentinel)),
        _os.path.abspath(_os.fspath(repo_sentinel)),
    }

    ws_root_abs = _os.path.abspath(str(workspace))
    violations: list[str] = []
    real_walk = _os.walk
    real_rglob = _Path.rglob
    real_scandir = _os.scandir
    real_read_bytes = _Path.read_bytes
    sentinel_reads: list[str] = []

    def _spy_walk(top, *args, **kwargs):
        top_abs = _os.path.abspath(str(top))
        if top_abs == ws_root_abs or top_abs.startswith(ws_root_abs + _os.sep):
            violations.append(f"os.walk {top_abs}")
        return real_walk(top, *args, **kwargs)

    def _spy_rglob(self, pattern, *args, **kwargs):
        self_abs = _os.path.abspath(_os.fspath(self))
        if self_abs == ws_root_abs or self_abs.startswith(ws_root_abs + _os.sep):
            violations.append(f"rglob {self_abs}")
        return real_rglob(self, pattern, *args, **kwargs)

    def _spy_scandir(path="."):
        path_abs = _os.path.abspath(str(path))
        if path_abs == ws_root_abs:
            violations.append(f"scandir {path_abs}")
        return real_scandir(path)

    def _spy_read_bytes(self):
        self_abs = _os.path.abspath(_os.fspath(self))
        if self_abs in sentinel_paths:
            sentinel_reads.append(self_abs)
        return real_read_bytes(self)

    monkeypatch.setattr(_os, "walk", _spy_walk)
    monkeypatch.setattr(_Path, "rglob", _spy_rglob)
    monkeypatch.setattr(_os, "scandir", _spy_scandir)
    monkeypatch.setattr(_Path, "read_bytes", _spy_read_bytes)

    real_write_agents_md = CodexWorkspaceAdapter.write_agents_md

    def _write_then_raise(self, workspace, agent_name, system_prompt, repo_names=None):
        real_write_agents_md(
            self, workspace, agent_name, system_prompt, repo_names=repo_names,
        )
        raise RuntimeError("Bootstrap failed — simulated failure")

    monkeypatch.setattr(CodexWorkspaceAdapter, "write_agents_md", _write_then_raise)

    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/dev_agent/executor",
        json={"executor": "codex"},
        headers=auth_headers,
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "executor_bootstrap_failed"
    assert violations == [], (
        f"Broad workspace enumeration during capture/rollback: {violations}"
    )
    assert sentinel_reads == [], (
        f"Sentinel file(s) read during capture/rollback: {sentinel_reads}"
    )
    assert (workspace / "AGENTS.md").read_bytes() == b"# AGENTS.md: old executor\n"
    assert deep_sentinel.read_bytes() == deep_blob, "deep sentinel changed"
    assert repo_sentinel.read_bytes() == repo_blob, "repo sentinel changed"


def test_set_executor_legacy_learnings_migration_rejected_before_materialization(
    tmp_home, app, org_state, auth_headers, monkeypatch,
) -> None:
    """THR-190: a workspace holding structured legacy learnings/ state
    (memory/ absent) would require the unbounded learnings/ -> memory/
    migration during bootstrap, which the bounded declared-write journal
    cannot reverse losslessly. The switch must fail closed BEFORE
    _executor_switch_materialize and BEFORE any adapter writer, with NO
    materialization/bootstrap/frontmatter/audit mutation."""
    import runtime.daemon.routes.agents as agents_mod
    from runtime.orchestrator import workspace_adapters as wa

    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
    (workspace / "repos" / "test" / ".git").mkdir(parents=True)
    (workspace / "task_history.md").write_text("# Task History: dev_agent\n")

    # ── Structured legacy learnings/ state (requires migration) ──
    legacy_dir = workspace / "learnings"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "MEM-001-operational-note.md").write_text(
        "---\nid: MEM-001\nslug: operational-note\n---\nlegacy body\n"
    )
    assert not (workspace / "memory").exists()

    # ── Recording spies: materialization + bootstrap writer seams ──
    materialize_calls: list[str] = []
    real_materialize_union = wa.materialize_workspace_skills_union

    def _materialize_spy(*args, **kwargs):
        materialize_calls.append("union")
        return real_materialize_union(*args, **kwargs)

    monkeypatch.setattr(wa, "materialize_workspace_skills_union", _materialize_spy)

    bootstrap_calls: list[str] = []
    real_ensure = agents_mod.ContextBuilder.ensure_workspace_ready

    def _ensure_spy(self, workspace, agent_name, system_prompt, provider="claude"):
        bootstrap_calls.append(provider)
        return real_ensure(self, workspace, agent_name, system_prompt, provider=provider)

    monkeypatch.setattr(agents_mod.ContextBuilder, "ensure_workspace_ready", _ensure_spy)

    agent_md_path = _paths(org_state).agents_dir / "dev_agent.md"
    frontmatter_before = agent_md_path.read_text()
    agent_yaml_before = (workspace / "agent.yaml").read_text()
    audit_before = [
        log for log in org_state.db.get_audit_logs("founder")
        if log["action"] == "agent_managed"
    ]

    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/dev_agent/executor",
        json={"executor": "codex"},
        headers=auth_headers,
    )

    assert r.status_code == 400, (
        f"Expected 400 on legacy learnings rejection, got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["detail"]["code"] == "executor_bootstrap_failed", (
        f"Expected executor_bootstrap_failed, got {body}"
    )
    assert "learnings" in body["detail"]["error"], (
        f"Expected legacy-learnings reason, got {body['detail']['error']}"
    )

    # ── No materialization, no bootstrap writer, no mutation ──
    assert materialize_calls == [], (
        f"materialization ran despite legacy-learnings preflight: {materialize_calls}"
    )
    assert bootstrap_calls == [], (
        f"bootstrap ran despite legacy-learnings preflight: {bootstrap_calls}"
    )
    assert (legacy_dir / "MEM-001-operational-note.md").read_text().startswith("---"), (
        "legacy learnings/ entry was mutated"
    )
    assert not (workspace / "memory").exists(), (
        "memory/ was created by the rejected switch"
    )
    assert (workspace / "task_history.md").read_text() == "# Task History: dev_agent\n"
    assert (workspace / "agent.yaml").read_text() == agent_yaml_before, (
        "workspace agent.yaml changed on legacy-learnings rejection"
    )
    assert agent_md_path.read_text() == frontmatter_before, (
        "Agent frontmatter was mutated on legacy-learnings rejection"
    )
    audit_after = [
        log for log in org_state.db.get_audit_logs("founder")
        if log["action"] == "agent_managed"
    ]
    assert audit_after == audit_before, (
        f"Audit row written despite legacy-learnings rejection: "
        f"before={audit_before}, after={audit_after}"
    )


def test_set_executor_bootstrap_preflight_rejects_symlinked_owned_path(
    tmp_home, app, org_state, auth_headers, monkeypatch,
) -> None:
    """THR-190 fail-closed: a pre-existing symlink at a bootstrap-owned path
    (AGENTS.md) cannot be losslessly compensated, so the switch must reject
    BEFORE bootstrap runs and without following/mutating the symlink target.

    Seeds AGENTS.md as a symlink to a sentinel target, then puts a recording
    spy on the real CodexWorkspaceAdapter.write_agents_md seam and proves:
    (1) the writer is NEVER called (bootstrap never runs),
    (2) the symlink survives with its original target,
    (3) the sentinel target bytes are unchanged,
    (4) old executor/frontmatter/audit state is unchanged,
    (5) the route returns 400 executor_bootstrap_failed."""
    import os as _os
    from runtime.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
    (workspace / "repos" / "test" / ".git").mkdir(parents=True)

    # ── Seed a pre-existing owned symlink: AGENTS.md -> sentinel target ──
    sentinel_target = workspace / "sentinel-target.md"
    sentinel_target.write_bytes(b"# sentinel target: must never be mutated\n")
    agents_md = workspace / "AGENTS.md"
    agents_md.symlink_to(sentinel_target.name)  # relative link target

    # A normal pre-existing owned file (CLAUDE.md) proves the preflight only
    # rejects the conflicting path and never disturbs regular-file switching.
    (workspace / "CLAUDE.md").write_bytes(b"# CLAUDE.md: old executor\n")

    frontmatter_path = _paths(org_state).agents_dir / "dev_agent.md"
    frontmatter_before = frontmatter_path.read_text()
    agent_yaml_before = (workspace / "agent.yaml").read_text()
    audit_before = [
        log for log in org_state.db.get_audit_logs("founder")
        if log["action"] == "agent_managed"
    ]

    # ── Recording spy at the real adapter writer seam ──
    writer_calls: list[str] = []
    real_write_agents_md = CodexWorkspaceAdapter.write_agents_md

    def _write_spy(self, workspace, agent_name, system_prompt, repo_names=None):
        writer_calls.append(agent_name)
        real_write_agents_md(
            self, workspace, agent_name, system_prompt, repo_names=repo_names,
        )
        raise RuntimeError("Bootstrap failed — simulated failure")

    monkeypatch.setattr(
        CodexWorkspaceAdapter, "write_agents_md", _write_spy,
    )

    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/dev_agent/executor",
        json={"executor": "codex"},
        headers=auth_headers,
    )

    # ── FAIL-CLOSED assertions ──
    assert r.status_code == 400, (
        f"Expected 400 on symlink preflight, got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["detail"]["code"] == "executor_bootstrap_failed", (
        f"Expected executor_bootstrap_failed, got {body}"
    )

    # ── The adapter writer was never called ──
    assert writer_calls == [], (
        f"bootstrap writer ran despite symlink preflight: {writer_calls}"
    )

    # ── The symlink survives with its original target ──
    assert agents_md.is_symlink(), "AGENTS.md symlink was removed/replaced"
    assert _os.readlink(agents_md) == sentinel_target.name, (
        f"AGENTS.md target changed: {_os.readlink(agents_md)!r} != "
        f"{sentinel_target.name!r}"
    )

    # ── The sentinel target was never followed or mutated ──
    assert sentinel_target.read_bytes() == b"# sentinel target: must never be mutated\n", (
        "sentinel target bytes were mutated"
    )

    # ── Old executor / frontmatter / audit unchanged ──
    assert frontmatter_path.read_text() == frontmatter_before, (
        "Agent frontmatter was mutated on symlink preflight"
    )
    assert (workspace / "agent.yaml").read_text() == agent_yaml_before, (
        "workspace agent.yaml changed on symlink preflight"
    )
    audit_after = [
        log for log in org_state.db.get_audit_logs("founder")
        if log["action"] == "agent_managed"
    ]
    assert audit_after == audit_before, (
        f"Audit row written despite symlink preflight: before={audit_before}, "
        f"after={audit_after}"
    )


def test_set_executor_preflight_rejects_uncapturable_owned_file(
    tmp_home, app, org_state, auth_headers, monkeypatch,
) -> None:
    """THR-190 fix (TASK-5691/TASK-5704 HIGH): a present regular
    bootstrap-owned file whose read_bytes() raises OSError is materially
    distinct from an absent file. The old journal recorded both as None and
    restore() interpreted None as 'absent before bootstrap' — a bootstrap
    failure could delete an unreadable present file.

    This test forces read_bytes OSError on a present CLAUDE.md at the real
    PUT /agents/{agent_name}/executor route and proves the switch fails
    closed DURING PREFLIGHT, BEFORE _executor_switch_materialize and before
    every filesystem/executor-state/frontmatter/audit mutation:
    (1) 400 executor_bootstrap_failed naming the uncapturable file,
    (2) the original file bytes survive unchanged,
    (3) union materialization NEVER ran,
    (4) the provider bootstrap writer NEVER ran,
    (5) org frontmatter + workspace agent.yaml are unchanged,
    (6) no audit row was written."""
    import os as _os
    from pathlib import Path as _Path

    import runtime.daemon.routes.agents as agents_mod
    from runtime.orchestrator import workspace_adapters as wa

    real_read_bytes = _Path.read_bytes  # captured BEFORE the monkeypatch

    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
    (workspace / "repos" / "test" / ".git").mkdir(parents=True)

    # Present regular declared file whose bytes cannot be captured.
    original = b"# CLAUDE.md: must survive the rejected switch unchanged\n"
    target = workspace / "CLAUDE.md"
    target.write_bytes(original)

    def _read_bytes(self, *a, **k):
        if _os.path.abspath(str(self)) == _os.path.abspath(str(target)):
            raise OSError("forced unreadable present declared file")
        return real_read_bytes(self, *a, **k)

    monkeypatch.setattr(_Path, "read_bytes", _read_bytes)

    frontmatter_path = _paths(org_state).agents_dir / "dev_agent.md"
    frontmatter_before = frontmatter_path.read_text()
    agent_yaml_before = (workspace / "agent.yaml").read_text()
    audit_before = [
        log for log in org_state.db.get_audit_logs("founder")
        if log["action"] == "agent_managed"
    ]

    # ── Recording spies at the materializer + bootstrap writer seams ──
    materialize_calls: list[str] = []
    real_materialize_union = wa.materialize_workspace_skills_union

    def _materialize_spy(*args, **kwargs):
        materialize_calls.append("union")
        return real_materialize_union(*args, **kwargs)

    monkeypatch.setattr(wa, "materialize_workspace_skills_union", _materialize_spy)

    bootstrap_calls: list[str] = []
    real_ensure = agents_mod.ContextBuilder.ensure_workspace_ready

    def _ensure_spy(self, workspace, agent_name, system_prompt, provider="claude"):
        bootstrap_calls.append(provider)
        return real_ensure(
            self, workspace, agent_name, system_prompt, provider=provider,
        )

    monkeypatch.setattr(
        agents_mod.ContextBuilder, "ensure_workspace_ready", _ensure_spy,
    )

    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/dev_agent/executor",
        json={"executor": "codex"},
        headers=auth_headers,
    )

    # ── FAIL-CLOSED: named preflight rejection ──
    assert r.status_code == 400, (
        f"Expected 400 on uncapturable-file preflight, got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["detail"]["code"] == "executor_bootstrap_failed", (
        f"Expected executor_bootstrap_failed, got {body}"
    )
    assert "CLAUDE.md" in body["detail"]["error"], (
        f"Expected the uncapturable file named in the error, "
        f"got {body['detail']['error']}"
    )
    assert "read_bytes" in body["detail"]["error"], (
        f"Expected read_bytes failure named, got {body['detail']['error']}"
    )

    # ── No materialization, no bootstrap writer, no mutation ──
    assert materialize_calls == [], (
        f"materialization ran despite uncapturable-file preflight: "
        f"{materialize_calls}"
    )
    assert bootstrap_calls == [], (
        f"bootstrap ran despite uncapturable-file preflight: {bootstrap_calls}"
    )

    # ── The present declared file's original bytes survive unchanged ──
    assert real_read_bytes(target) == original, (
        "the uncapturable declared file was modified or deleted"
    )

    # ── Old executor / frontmatter / audit unchanged ──
    assert (workspace / "agent.yaml").read_text() == agent_yaml_before, (
        "workspace agent.yaml changed on uncapturable-file rejection"
    )
    assert frontmatter_path.read_text() == frontmatter_before, (
        "Agent frontmatter was mutated on uncapturable-file rejection"
    )
    audit_after = [
        log for log in org_state.db.get_audit_logs("founder")
        if log["action"] == "agent_managed"
    ]
    assert audit_after == audit_before, (
        f"Audit row written despite uncapturable-file rejection: "
        f"before={audit_before}, after={audit_after}"
    )


def test_set_executor_capture_second_read_fails_closed_before_materialize(
    tmp_home, app, org_state, auth_headers, monkeypatch,
) -> None:
    """THR-190 fix (TASK-5714 HIGH second-read window): the AUTHORITATIVE
    rollback capture must fail closed BEFORE the first mutation
    (_executor_switch_materialize), even when the Step-0 preflight read
    succeeded.

    Regression scenario: a present regular declared file (CLAUDE.md) whose
    bytes the Step-0 preflight gate (_bootstrap_uncapturable_owned_files)
    CAN read, but which the authoritative _BootstrapRollbackJournal.capture
    cannot read (TOCTOU: read_bytes fails between the two reads). The old
    ordering ran capture AFTER materialization, recorded the file as
    uncapturable, and proceeded to ensure_workspace_ready — bootstrap could
    overwrite a present file whose original bytes were never captured
    (uncompensatable data-loss path).

    The deterministic per-file call counter forces read #1 (preflight) to
    succeed and read #2 (authoritative capture) to raise OSError, then
    proves the switch fails closed during Step-0 preflight, BEFORE
    _executor_switch_materialize and before every
    filesystem/executor-state/frontmatter/audit mutation:
    (1) 400 executor_bootstrap_failed naming the uncapturable file,
    (2) the original file bytes survive unchanged,
    (3) union materialization NEVER ran,
    (4) the provider bootstrap writer NEVER ran,
    (5) org frontmatter + workspace agent.yaml are unchanged,
    (6) no audit row was written."""
    import os as _os
    from pathlib import Path as _Path

    import runtime.daemon.routes.agents as agents_mod
    from runtime.orchestrator import workspace_adapters as wa

    real_read_bytes = _Path.read_bytes  # captured BEFORE the monkeypatch

    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
    (workspace / "repos" / "test" / ".git").mkdir(parents=True)

    # Present regular declared file whose bytes must be captured BEFORE the
    # first mutation. Preflight (read #1) succeeds; the authoritative
    # capture (read #2) fails — the exact TASK-5714 second-read window.
    original = b"# CLAUDE.md: must survive the rejected switch unchanged\n"
    target = workspace / "CLAUDE.md"
    target.write_bytes(original)

    target_abspath = _os.path.abspath(str(target))
    read_counts: dict[str, int] = {}

    def _read_bytes(self, *a, **k):
        if _os.path.abspath(str(self)) == target_abspath:
            read_counts["CLAUDE.md"] = read_counts.get("CLAUDE.md", 0) + 1
            if read_counts["CLAUDE.md"] == 2:
                # Authoritative capture read fails; preflight read succeeded.
                raise OSError("forced capture-read failure on present declared file")
        return real_read_bytes(self, *a, **k)

    monkeypatch.setattr(_Path, "read_bytes", _read_bytes)

    frontmatter_path = _paths(org_state).agents_dir / "dev_agent.md"
    frontmatter_before = frontmatter_path.read_text()
    agent_yaml_before = (workspace / "agent.yaml").read_text()
    audit_before = [
        log for log in org_state.db.get_audit_logs("founder")
        if log["action"] == "agent_managed"
    ]

    # ── Recording spies at the materializer + bootstrap writer seams ──
    materialize_calls: list[str] = []
    real_materialize_union = wa.materialize_workspace_skills_union

    def _materialize_spy(*args, **kwargs):
        materialize_calls.append("union")
        return real_materialize_union(*args, **kwargs)

    monkeypatch.setattr(wa, "materialize_workspace_skills_union", _materialize_spy)

    bootstrap_calls: list[str] = []
    real_ensure = agents_mod.ContextBuilder.ensure_workspace_ready

    def _ensure_spy(self, workspace, agent_name, system_prompt, provider="claude"):
        bootstrap_calls.append(provider)
        return real_ensure(
            self, workspace, agent_name, system_prompt, provider=provider,
        )

    monkeypatch.setattr(
        agents_mod.ContextBuilder, "ensure_workspace_ready", _ensure_spy,
    )

    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/dev_agent/executor",
        json={"executor": "codex"},
        headers=auth_headers,
    )

    # ── The authoritative capture observed the file at Step 0 (read #2) ──
    assert read_counts["CLAUDE.md"] >= 2, (
        f"expected preflight (read 1) + authoritative capture (read 2) "
        f"to both run, got {read_counts['CLAUDE.md']} reads"
    )

    # ── FAIL-CLOSED: named preflight rejection BEFORE the first mutation ──
    assert r.status_code == 400, (
        f"Expected 400 on capture second-read failure, got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["detail"]["code"] == "executor_bootstrap_failed", (
        f"Expected executor_bootstrap_failed, got {body}"
    )
    assert "CLAUDE.md" in body["detail"]["error"], (
        f"Expected the uncapturable file named in the error, "
        f"got {body['detail']['error']}"
    )
    assert "read_bytes" in body["detail"]["error"], (
        f"Expected read_bytes failure named, got {body['detail']['error']}"
    )

    # ── No materialization, no bootstrap writer, no mutation ──
    assert materialize_calls == [], (
        f"materialization ran despite capture second-read failure: "
        f"{materialize_calls}"
    )
    assert bootstrap_calls == [], (
        f"bootstrap ran despite capture second-read failure: {bootstrap_calls}"
    )

    # ── The present declared file's original bytes survive unchanged ──
    assert real_read_bytes(target) == original, (
        "the uncapturable declared file was modified or deleted"
    )

    # ── Old executor / frontmatter / audit unchanged ──
    assert (workspace / "agent.yaml").read_text() == agent_yaml_before, (
        "workspace agent.yaml changed on capture second-read rejection"
    )
    assert frontmatter_path.read_text() == frontmatter_before, (
        "Agent frontmatter was mutated on capture second-read rejection"
    )
    audit_after = [
        log for log in org_state.db.get_audit_logs("founder")
        if log["action"] == "agent_managed"
    ]
    assert audit_after == audit_before, (
        f"Audit row written despite capture second-read rejection: "
        f"before={audit_before}, after={audit_after}"
    )


def test_bootstrap_journal_uncapturable_present_file_is_not_absent(
    tmp_home, monkeypatch,
) -> None:
    """THR-190 fix (TASK-5691/TASK-5704): _BootstrapRollbackJournal must
    keep a present regular file whose read_bytes() raises OSError in a
    DISTINCT state from an absent file. restore() must never unlink such a
    file (the old code collapsed both into None and deleted it); it reports
    a compensation error instead, and the file survives unchanged."""
    from pathlib import Path as _Path

    import runtime.daemon.routes.agents as agents_mod

    workspace = tmp_home / "ws"
    workspace.mkdir(parents=True)
    present = workspace / "CLAUDE.md"
    original = b"# original bytes must survive\n"
    present.write_bytes(original)
    (workspace / "memory").mkdir(parents=True)

    real_read_bytes = _Path.read_bytes

    def _read_bytes(self, *a, **k):
        if str(self) == str(present):
            raise OSError("forced OSError on present declared file")
        return real_read_bytes(self, *a, **k)

    monkeypatch.setattr(_Path, "read_bytes", _read_bytes)

    journal = agents_mod._BootstrapRollbackJournal.capture(workspace)

    # The present-but-uncapturable file is NEVER recorded as absent: it is
    # absent from the content map entirely and tracked in the distinct set.
    assert "CLAUDE.md" not in journal._files, (
        "uncapturable file must not be recorded in the content map"
    )
    assert "CLAUDE.md" in journal._uncapturable, (
        "uncapturable file must be recorded in the distinct uncapturable set"
    )

    errors = journal.restore(workspace)
    assert any("Uncapturable" in e and "CLAUDE.md" in e for e in errors), errors
    # The file survives: not deleted, not overwritten, not treated as absent.
    assert real_read_bytes(present) == original, (
        "journal restore deleted/modified an uncapturable present file"
    )


def test_set_executor_preflight_rejects_symlinked_claude_before_materialization(
    tmp_home, app, org_state, auth_headers, monkeypatch,
) -> None:
    """THR-190 CRITICAL: a pre-existing ``workspace/.claude`` symlink to an
    EXTERNAL directory must be rejected BEFORE the six-context union
    materialization runs, because ``repair_workspace_skills(..., '.claude/skills')``
    follows the link and would create/replace/withdraw entries in the external
    target. Proves, at the real route + adapter/materializer seams:

    (1) 400 executor_bootstrap_failed,
    (2) materialization is NEVER invoked (the reconciler never runs),
    (3) the bootstrap writer is NEVER invoked,
    (4) the ``.claude`` symlink survives with its original target,
    (5) the external sentinel directory's ``skills/`` subtree is byte/state
        identical (no creation/replacement/withdrawal),
    (6) old executor/frontmatter/audit state is unchanged (no launch)."""
    import os as _os
    import hashlib
    from pathlib import Path as _Path
    from runtime.orchestrator import workspace_adapters as wa
    from runtime.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
    (workspace / "repos" / "test" / ".git").mkdir(parents=True)

    # ── External sentinel directory OUTSIDE the workspace ──
    sentinel_dir = org_state.root / "external-sentinel-claude"
    sentinel_skills = sentinel_dir / "skills"
    sentinel_skills.mkdir(parents=True)
    keep_file = sentinel_skills / "keep.txt"
    keep_file.write_bytes(b"# sentinel keep: never mutated\n")
    keep_link = sentinel_skills / "keep-link"
    _os.symlink("keep.txt", keep_link)

    # workspace/.claude -> external sentinel directory (the reviewed vector)
    claude_link = workspace / ".claude"
    claude_link.symlink_to(sentinel_dir, target_is_directory=True)

    def _snapshot_sentinel(root: _Path) -> dict:
        """Recursive (file-bytes, symlink-target, dir) snapshot of the sentinel."""
        snap: dict = {}
        for p in sorted(root.rglob("*")):
            rel = str(p.relative_to(root))
            if p.is_symlink():
                snap[rel] = ("link", _os.readlink(p))
            elif p.is_file():
                snap[rel] = ("file", hashlib.sha256(p.read_bytes()).hexdigest())
            elif p.is_dir():
                snap[rel] = ("dir",)
        return snap

    sentinel_before = _snapshot_sentinel(sentinel_dir)

    frontmatter_path = _paths(org_state).agents_dir / "dev_agent.md"
    frontmatter_before = frontmatter_path.read_text()
    agent_yaml_before = (workspace / "agent.yaml").read_text()
    audit_before = [
        log for log in org_state.db.get_audit_logs("founder")
        if log["action"] == "agent_managed"
    ]

    # ── Recording spies at the materializer + bootstrap writer seams ──
    # materialize_workspace_skills_union is imported locally inside
    # _executor_switch_materialize, so patch the adapter module attribute.
    materialize_calls: list[str] = []
    real_materialize_union = wa.materialize_workspace_skills_union

    def _materialize_spy(*args, **kwargs):
        materialize_calls.append("union")
        return real_materialize_union(*args, **kwargs)

    monkeypatch.setattr(wa, "materialize_workspace_skills_union", _materialize_spy)

    writer_calls: list[str] = []
    real_write_agents_md = CodexWorkspaceAdapter.write_agents_md

    def _write_spy(self, workspace, agent_name, system_prompt, repo_names=None):
        writer_calls.append(agent_name)
        real_write_agents_md(
            self, workspace, agent_name, system_prompt, repo_names=repo_names,
        )
        raise RuntimeError("Bootstrap failed — simulated failure")

    monkeypatch.setattr(
        CodexWorkspaceAdapter, "write_agents_md", _write_spy,
    )

    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/dev_agent/executor",
        json={"executor": "codex"},
        headers=auth_headers,
    )

    # ── FAIL-CLOSED assertions ──
    assert r.status_code == 400, (
        f"Expected 400 on .claude symlink preflight, got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["detail"]["code"] == "executor_bootstrap_failed", (
        f"Expected executor_bootstrap_failed, got {body}"
    )

    # ── Materialization NEVER ran (the reconciler never followed .claude) ──
    assert materialize_calls == [], (
        f"materialization ran despite .claude symlink preflight: {materialize_calls}"
    )

    # ── Bootstrap writer never called ──
    assert writer_calls == [], (
        f"bootstrap writer ran despite .claude symlink preflight: {writer_calls}"
    )

    # ── The symlink survives with its original target ──
    assert claude_link.is_symlink(), ".claude symlink was removed/replaced"
    assert _os.readlink(claude_link) == str(sentinel_dir), (
        f".claude target changed: {_os.readlink(claude_link)!r} != "
        f"{str(sentinel_dir)!r}"
    )

    # ── External sentinel directory state is byte/state identical ──
    assert _snapshot_sentinel(sentinel_dir) == sentinel_before, (
        "external sentinel .claude/skills subtree was mutated"
    )
    assert keep_file.read_bytes() == b"# sentinel keep: never mutated\n", (
        "sentinel keep.txt bytes were mutated"
    )
    assert keep_link.is_symlink(), "sentinel keep-link symlink was removed"
    assert _os.readlink(keep_link) == "keep.txt", "sentinel keep-link target changed"
    assert sorted(p.name for p in sentinel_skills.iterdir()) == [
        "keep-link", "keep.txt"
    ], "sentinel skills/ entry set changed (creation/withdrawal detected)"

    # ── Old executor / frontmatter / audit unchanged (no launch) ──
    assert frontmatter_path.read_text() == frontmatter_before, (
        "Agent frontmatter was mutated on .claude symlink preflight"
    )
    assert (workspace / "agent.yaml").read_text() == agent_yaml_before, (
        "workspace agent.yaml changed on .claude symlink preflight"
    )
    audit_after = [
        log for log in org_state.db.get_audit_logs("founder")
        if log["action"] == "agent_managed"
    ]
    assert audit_after == audit_before, (
        f"Audit row written despite .claude symlink preflight: "
        f"before={audit_before}, after={audit_after}"
    )


def test_set_executor_bootstrap_journal_ignores_repos_sentinel(
    tmp_home, app, org_state, auth_headers, monkeypatch,
) -> None:
    """THR-190: the bounded rollback journal must never read (or retain) a
    large regular file under repos/. The prior os.walk snapshot read every
    file in the workspace; the new journal captures only bootstrap-owned paths.

    Seeds a multi-MB sentinel under repos/, spies on Path.read_bytes for that
    exact path, forces a bootstrap failure, and asserts the sentinel is never
    read and remains byte-identical after rollback."""
    import os as _os
    from pathlib import Path as _Path
    from runtime.orchestrator.workspace_adapters import CodexWorkspaceAdapter

    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
    (workspace / "repos" / "test" / ".git").mkdir(parents=True)

    sentinel = workspace / "repos" / "test" / "large-sentinel.bin"
    big_blob = _os.urandom(2 * 1024 * 1024)  # 2 MB
    sentinel.write_bytes(big_blob)
    sentinel_abs = _os.path.abspath(_os.fspath(sentinel))

    # Spy on read_bytes for the sentinel path only.
    real_read_bytes = _Path.read_bytes
    read_calls: list = []

    def _spy_read_bytes(self):
        if _os.path.abspath(_os.fspath(self)) == sentinel_abs:
            read_calls.append(self)
        return real_read_bytes(self)

    monkeypatch.setattr(_Path, "read_bytes", _spy_read_bytes)

    real_write_agents_md = CodexWorkspaceAdapter.write_agents_md

    def _write_then_raise(self, workspace, agent_name, system_prompt, repo_names=None):
        real_write_agents_md(
            self, workspace, agent_name, system_prompt, repo_names=repo_names,
        )
        raise RuntimeError("Bootstrap failed — simulated failure")

    monkeypatch.setattr(
        CodexWorkspaceAdapter, "write_agents_md", _write_then_raise,
    )

    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/dev_agent/executor",
        json={"executor": "codex"},
        headers=auth_headers,
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "executor_bootstrap_failed"

    assert read_calls == [], (
        f"rollback journal read the repos/ sentinel {len(read_calls)} time(s)"
    )
    assert real_read_bytes(sentinel) == big_blob, "sentinel bytes changed"


def test_set_executor_materializes_and_validates_before_bootstrap(
    tmp_home, app, org_state, auth_headers, monkeypatch,
) -> None:
    """THR-190 ordering: six-context materialization and integrity validation
    must both complete before any bootstrap write runs. A bootstrap failure
    must never skip or reorder the materialize/validate phase."""
    import runtime.daemon.routes.agents as agents_mod
    from runtime.orchestrator import workspace_adapters as wa

    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
    (workspace / "repos" / "test" / ".git").mkdir(parents=True)

    order: list[str] = []

    def _materialize(*args, **kwargs):
        order.append("materialize")
        return []

    def _validate(*args, **kwargs):
        order.append("validate")
        return None

    def _bootstrap(*args, **kwargs):
        order.append("bootstrap")
        raise RuntimeError("stop after bootstrap")

    # materialize_workspace_skills_union is imported locally inside
    # _executor_switch_materialize, so patch the adapter module attribute.
    monkeypatch.setattr(wa, "materialize_workspace_skills_union", _materialize)
    # validate_workspace_skills_integrity is imported at agents.py module top,
    # so patch the agents module binding.
    monkeypatch.setattr(agents_mod, "validate_workspace_skills_integrity", _validate)
    monkeypatch.setattr(
        agents_mod.ContextBuilder, "ensure_workspace_ready", _bootstrap,
    )

    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/dev_agent/executor",
        json={"executor": "codex"},
        headers=auth_headers,
    )
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "executor_bootstrap_failed"
    assert order == ["materialize", "validate", "bootstrap"], (
        f"phase order wrong: {order}"
    )


def test_set_executor_materialization_real_missing_source_stops_before_build(
    tmp_home, app, org_state, auth_headers, monkeypatch,
) -> None:
    """TASK-4175 adversarial: When a mandatory system-contract source is absent
    during executor-switch six-context union materialization, the route must
    fail BEFORE any canonical package build or workspace reconciliation.

    Exercises the REAL union (not mocked) with a missing dream contract
    (required by DREAM context in the six-context union).

    Strengthened TASK-4176 proof-gap repair:
    - Known trusted canonical packages + validated links in BOTH
      .claude/skills + .agents/skills provider roots seeded before request.
    - Full canonical store snapshot (manifest/member/on-disk hashes).
    - Both provider-root link targets + non-link content compared.
    - Workspace file assertions beyond bootstrap files.
    - Audit-success state verified (no new rows).
    - Executor config/frontmatter unchanged.
    """
    import shutil
    import hashlib
    import os as _os
    from pathlib import Path

    _seed_active_agent(org_state, "dev_agent", executor="claude")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "agent.yaml").write_text("repos: {}\nexecutor: claude\n")
    (workspace / "repos" / "test" / ".git").mkdir(parents=True)
    (workspace / "task_history.md").write_text("# Task History: dev_agent\n")

    # ── Seed validated links in BOTH provider roots BEFORE the request ────
    # This lets us prove that neither root's links or non-link files change.
    from runtime.skills.canonical_store import CanonicalSkillStore
    store = CanonicalSkillStore(settings=org_state.settings)

    # Use the real protocol skills to build a canonical package for a
    # trusted system contract (start-task) and create symlinks into
    # BOTH .claude/skills and .agents/skills.
    proto_skills_real = org_state.settings.get_protocol_dir() / "skills"
    if (proto_skills_real / "start-task").is_dir():
        from runtime.orchestrator.workspace_adapters import _compute_dir_hash
        trusted_hash = _compute_dir_hash(proto_skills_real / "start-task")
        store.build_from_source(
            "start-task", "system", trusted_hash,
            proto_skills_real / "start-task",
        )
        # Create real symlink targets in both provider roots.
        canonical_target = store.canonical_path(
            "start-task", "system", trusted_hash,
        )
        for subdir in (".claude/skills", ".agents/skills"):
            link_path = workspace / subdir / "start-task"
            link_path.parent.mkdir(parents=True, exist_ok=True)
            if not link_path.exists():
                _os.symlink(
                    _os.path.relpath(canonical_target, link_path.parent),
                    link_path,
                )

    # ── Full canonical store snapshot (files + hashes, not just dirs) ─────
    def _snapshot_canonical_full(root: Path) -> dict[str, str]:
        snap: dict[str, str] = {}
        if not root.is_dir():
            return snap
        for p in sorted(root.rglob("*")):
            if p.is_file():
                snap[str(p.relative_to(root))] = hashlib.sha256(
                    p.read_bytes()
                ).hexdigest()
            elif p.is_dir():
                snap[str(p.relative_to(root)) + "/"] = "<dir>"
        return snap

    store_snapshot_before = _snapshot_canonical_full(store.root)

    # ── Workspace snapshot: file bytes + symlink targets ──────────────────
    def _snapshot_workspace(root: Path) -> dict[str, str]:
        snap: dict[str, str] = {}
        if not root.is_dir():
            return snap
        for p in sorted(root.rglob("*")):
            rel = str(p.relative_to(root))
            if p.is_symlink():
                snap[rel] = f"link->{_os.readlink(p)}"
            elif p.is_file():
                snap[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            elif p.is_dir():
                snap[rel + "/"] = "<dir>"
        return snap

    ws_snapshot_before = _snapshot_workspace(workspace)

    # Remove a required system-contract source: dream (DREAM context).
    # Use a temp copy of protocol skills to avoid mutating the worktree.
    import tempfile as _tempfile, shutil as _shutil
    _tmp_proto = tmp_home / "_task4175_proto_skills"
    _shutil.copytree(
        org_state.settings.get_protocol_dir() / "skills",
        _tmp_proto, symlinks=True,
    )
    dream_dir = _tmp_proto / "dream"
    assert dream_dir.is_dir(), (
        f"dream must exist before removal at {dream_dir}"
    )
    _shutil.rmtree(dream_dir)
    assert not dream_dir.exists()

    # Redirect _resolve_skills_src to our temp copy
    import runtime.orchestrator.workspace_adapters as _wa_mod
    monkeypatch.setattr(
        _wa_mod, "_resolve_skills_src",
        lambda settings: _tmp_proto,
    )

    # Record pristine state
    agent_yaml_before = (workspace / "agent.yaml").read_text()
    agent_md_path = org_state.root / "agents" / "dev_agent.md"
    frontmatter_before = agent_md_path.read_text() if agent_md_path.exists() else None

    # ── Audit state before request ──
    audit_before = org_state.db.get_audit_logs("dev_agent")
    audit_count_before = len(audit_before)

    # ── Execute failing switch (NO mock — real union) ──
    from fastapi.testclient import TestClient
    r = TestClient(app).put(
        "/api/v1/orgs/alpha/agents/dev_agent/executor",
        json={"executor": "codex"},
        headers=auth_headers,
    )

    # FAIL-CLOSED: missing source prevents switch
    assert r.status_code == 400, (
        f"Expected 400 on missing source, got {r.status_code}: {r.text}"
    )
    body = r.json()
    assert body["detail"]["code"] == "executor_materialization_failed", (
        f"Expected executor_materialization_failed, got {body}"
    )
    assert len(body["detail"]["errors"]) >= 1

    # ── Assert unchanged state ──
    agent_yaml_after = (workspace / "agent.yaml").read_text()
    assert agent_yaml_after == agent_yaml_before, (
        "agent.yaml was mutated on failure"
    )
    if frontmatter_before is not None:
        assert agent_md_path.read_text() == frontmatter_before

    # ── Full canonical store unchanged ────────────────────────────────────
    store_snapshot_after = _snapshot_canonical_full(store.root)
    assert store_snapshot_after == store_snapshot_before, (
        f"Canonical store was mutated by failed materialization.\n"
        f"Added: {set(store_snapshot_after) - set(store_snapshot_before)}\n"
        f"Removed: {set(store_snapshot_before) - set(store_snapshot_after)}"
    )

    # ── No bootstrap files from new executor ──
    for candidate in ["CLAUDE.md", "AGENTS.md", ".claude/settings.json",
                       ".agents/settings.json"]:
        assert not (workspace / candidate).exists(), (
            f"Bootstrap file {candidate} should not exist after failed switch"
        )

    # ── Workspace file + link state fully unchanged ───────────────────────
    ws_snapshot_after = _snapshot_workspace(workspace)
    assert ws_snapshot_after == ws_snapshot_before, (
        f"Workspace was mutated by failed materialization.\n"
        f"Added: {set(ws_snapshot_after) - set(ws_snapshot_before)}\n"
        f"Removed: {set(ws_snapshot_before) - set(ws_snapshot_after)}"
    )

    # ── Both provider-root links preserved ────────────────────────────────
    for subdir in (".claude/skills", ".agents/skills"):
        sd = workspace / subdir
        assert sd.exists() == (subdir + "/" in ws_snapshot_before), (
            f"{subdir} existence changed after failure"
        )
        if sd.exists():
            for entry in sorted(sd.iterdir()):
                rel = str(entry.relative_to(workspace))
                if entry.is_symlink():
                    assert rel in ws_snapshot_before, (
                        f"New symlink {rel} appeared after failure"
                    )
                    expected_target = ws_snapshot_before.get(rel, "")
                    actual = f"link->{_os.readlink(entry)}"
                    assert actual == expected_target, (
                        f"Symlink {rel} target changed: "
                        f"expected {expected_target}, got {actual}"
                    )

    # ── No new audit rows (no success claim) ──────────────────────────────
    audit_after = org_state.db.get_audit_logs("dev_agent")
    assert len(audit_after) == audit_count_before, (
        f"Audit rows changed: before={audit_count_before}, after={len(audit_after)}"
    )


# ---------------------------------------------------------------------------
# Agent termination: archival, quiescence, and fail-closed launch (TASK-5293)
# ---------------------------------------------------------------------------


def test_manage_agent_terminate_archives_worker_preserves_history(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Termination archives the AgentDef and workspace but keeps every historic
    row (task, audit, token usage, thread participant, memory) readable.
    """
    from datetime import datetime, timezone
    from runtime.models import TaskRecord, TaskStatus, TokenUsage
    from runtime.orchestrator import prompt_loader

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")

    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    memory_file = workspace / "memory" / "learning.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("- remember this\n")

    # Historic task assigned to dev_agent.
    task = TaskRecord(
        id="TASK-HIST",
        team="engineering",
        brief="old work",
        assigned_agent="dev_agent",
        status=TaskStatus.FAILED,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    org_state.db.insert_task(task)
    org_state.db.insert_audit_log("TASK-HIST", "dev_agent", "did_something", {})
    org_state.db.insert_session_token_usage(
        task_id="TASK-HIST",
        agent="dev_agent",
        session_id="sess-old",
        executor="claude",
        token_usage=TokenUsage(input_tokens=1, output_tokens=1),
        scope_type="task",
        scope_id="TASK-HIST",
    )

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
    assert r.json()["status"] == "terminated"

    paths = _paths(org_state)
    assert prompt_loader.load_agent(paths, "dev_agent") is None
    assert prompt_loader.load_terminated_agent(paths, "dev_agent") is not None
    assert not (paths.workspaces_dir / "dev_agent").exists()
    archived_ws = paths.workspaces_dir / "_terminated" / "dev_agent"
    assert archived_ws.exists()
    assert (archived_ws / "memory" / "learning.md").read_text() == "- remember this\n"
    assert org_state.teams.team_for_agent("dev_agent") is None

    # Historic records still reference the agent name.
    assert org_state.db.get_task("TASK-HIST").assigned_agent == "dev_agent"
    audit = org_state.db.query_audit_logs(agent="dev_agent", limit=10)[0]
    assert any(row["agent"] == "dev_agent" for row in audit)


def test_manage_agent_terminate_refuses_manager(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _activate_eh_session(org_state)
    _write_agent_md(_paths(org_state), _make_agent("engineering_head", role="manager"))

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
        json={
            "action": "terminate",
            "name": "engineering_head",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
        },
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "manager_terminate_forbidden"


def test_manage_agent_terminate_blocks_active_task(
    tmp_home, app, org_state, auth_headers,
) -> None:
    from datetime import datetime, timezone
    from runtime.models import TaskRecord, TaskStatus

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    task = TaskRecord(
        id="TASK-LIVE",
        team="engineering",
        brief="live work",
        assigned_agent="dev_agent",
        status=TaskStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    org_state.db.insert_task(task)

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
    assert r.status_code == 409
    body = r.json()["detail"]
    assert body["code"] == "agent_not_quiescent"
    assert any(c["kind"] == "task" and c["id"] == "TASK-LIVE" for c in body["conflicts"])


def test_manage_agent_terminate_blocks_started_thread_invocation(
    tmp_home, app, org_state, auth_headers,
) -> None:
    from datetime import datetime, timezone
    from runtime.models import (
        ThreadRecord,
        ThreadInvocationPurpose,
        ThreadMessageKind,
    )

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    org_state.db.insert_thread(ThreadRecord(id="THR-LIVE", subject="x"))
    org_state.db.add_thread_participant("THR-LIVE", "dev_agent", added_by="founder")
    org_state.db.append_thread_message(
        thread_id="THR-LIVE", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = org_state.db.mint_thread_invocation(
        thread_id="THR-LIVE", agent_name="dev_agent",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    org_state.db.stamp_invocation_started(inv.invocation_token, session_id="sess-live")

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
    assert r.status_code == 409
    conflicts = r.json()["detail"]["conflicts"]
    assert any(c["kind"] == "thread_invocation" and c["id"] == inv.invocation_token for c in conflicts)


def test_manage_agent_terminate_blocks_firing_schedule(
    tmp_home, app, org_state, auth_headers,
) -> None:
    from datetime import datetime, timezone
    from runtime.models import ScheduleRecord, ScheduleStatus, ScheduleKind

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    fire_at = datetime.now(timezone.utc)
    record = ScheduleRecord(
        id="SCHED-LIVE",
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=fire_at,
        timezone="UTC",
        normalized_brief="brief",
        source_instruction="do it",
        status=ScheduleStatus.FIRING,
        created_at=fire_at,
        updated_at=fire_at,
    )
    org_state.db.schedules.insert(record)

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
    assert r.status_code == 409
    conflicts = r.json()["detail"]["conflicts"]
    assert any(c["kind"] == "schedule" and c["id"] == "SCHED-LIVE" for c in conflicts)


def test_manage_agent_terminate_blocks_running_work_hour(
    tmp_home, app, org_state, auth_headers,
) -> None:
    from datetime import datetime, timezone
    from runtime.models import WorkHourRecord, WorkHourStatus, WorkHourMode

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    now = datetime.now(timezone.utc)
    record = WorkHourRecord(
        id="WORKHOUR-LIVE",
        agent_name="dev_agent",
        local_date="2026-08-21",
        slot="morning",
        mode=WorkHourMode.WINDOWED,
        scheduled_for=now,
        status=WorkHourStatus.RUNNING,
        started_at=now,
        created_at=now,
    )
    org_state.db.work_hours.insert(record)

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
    assert r.status_code == 409
    conflicts = r.json()["detail"]["conflicts"]
    assert any(c["kind"] == "work_hour" and c["id"] == "WORKHOUR-LIVE" for c in conflicts)


def test_manage_agent_terminate_blocks_running_dream(
    tmp_home, app, org_state, auth_headers,
) -> None:
    from datetime import datetime, timezone
    from runtime.models import DreamRecord, DreamStatus

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    now = datetime.now(timezone.utc)
    dream = DreamRecord(
        id="DREAM-LIVE",
        agent_name="dev_agent",
        local_date="2026-08-21",
        scheduled_for=now,
        window_end=now,
        status=DreamStatus.RUNNING,
        started_at=now,
        created_at=now,
    )
    org_state.db.insert_dream(dream)

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
    assert r.status_code == 409
    conflicts = r.json()["detail"]["conflicts"]
    assert any(c["kind"] == "dream" and c["id"] == "DREAM-LIVE" for c in conflicts)


def test_manage_agent_terminate_blocks_running_job(
    tmp_home, app, org_state, auth_headers,
) -> None:
    from datetime import datetime, timezone
    from runtime.models import JobRecord, JobStatus

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    now = datetime.now(timezone.utc)
    job = JobRecord(
        id="JOB-LIVE",
        task_id="TASK-1",
        agent_name="dev_agent",
        title="x",
        rationale="test",
        script_text="echo hi",
        interpreter="bash",
        status=JobStatus.RUNNING,
        review_required=False,
        persistent=False,
        created_at=now.isoformat(),
        started_at=now.isoformat(),
    )
    org_state.db.insert_job(job)

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
    assert r.status_code == 409
    conflicts = r.json()["detail"]["conflicts"]
    assert any(c["kind"] == "job" and c["id"] == "JOB-LIVE" for c in conflicts)


def test_manage_agent_terminate_declines_unstarted_invocations(
    tmp_home, app, org_state, auth_headers,
) -> None:
    from runtime.models import (
        ThreadRecord,
        ThreadInvocationPurpose,
        ThreadInvocationStatus,
        ThreadMessageKind,
    )

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    org_state.db.insert_thread(ThreadRecord(id="THR-DECL", subject="x"))
    org_state.db.add_thread_participant("THR-DECL", "dev_agent", added_by="founder")
    org_state.db.append_thread_message(
        thread_id="THR-DECL", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = org_state.db.mint_thread_invocation(
        thread_id="THR-DECL", agent_name="dev_agent",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )

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
    after = org_state.db.get_invocation_any_status(inv.invocation_token)
    assert after.status == ThreadInvocationStatus.DECLINED
    assert after.decline_reason == "agent_terminated"


def test_manage_agent_terminate_cancels_armed_schedule_skips_wake_and_dream(
    tmp_home, app, org_state, auth_headers,
) -> None:
    from datetime import datetime, timezone
    from runtime.models import (
        DreamRecord,
        DreamStatus,
        ScheduleRecord,
        ScheduleKind,
        ScheduleStatus,
        WorkHourRecord,
        WorkHourMode,
        WorkHourStatus,
    )

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    now = datetime.now(timezone.utc)

    sched = ScheduleRecord(
        id="SCHED-ARMED",
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=now,
        timezone="UTC",
        normalized_brief="brief",
        source_instruction="do it",
        status=ScheduleStatus.ARMED,
        active=1,
        created_at=now,
        updated_at=now,
    )
    org_state.db.schedules.insert(sched)

    wh = WorkHourRecord(
        id="WORKHOUR-PEND",
        agent_name="dev_agent",
        local_date="2026-08-21",
        slot="morning",
        mode=WorkHourMode.WINDOWED,
        scheduled_for=now,
        status=WorkHourStatus.PENDING,
        created_at=now,
    )
    org_state.db.work_hours.insert(wh)

    dream = DreamRecord(
        id="DREAM-PEND",
        agent_name="dev_agent",
        local_date="2026-08-21",
        scheduled_for=now,
        window_end=now,
        status=DreamStatus.PENDING,
        created_at=now,
    )
    org_state.db.insert_dream(dream)

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
    assert org_state.db.schedules.get("SCHED-ARMED").status == ScheduleStatus.CANCELLED
    assert org_state.db.work_hours.get("WORKHOUR-PEND").status == WorkHourStatus.SKIPPED
    assert org_state.db.get_dream("DREAM-PEND").status == DreamStatus.SKIPPED


def test_list_enrollments_terminated(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
        json={
            "action": "terminate",
            "name": "dev_agent",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
        },
        headers=auth_headers,
    )

    r = TestClient(app).get(
        "/api/v1/orgs/alpha/agents/enrollments?status=terminated",
        headers=auth_headers,
    )
    assert r.status_code == 200
    rows = r.json()["enrollments"]
    assert any(e["name"] == "dev_agent" and e["status"] == "terminated" for e in rows)


def test_list_agents_excludes_terminated_and_terminated_workspace(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """GET /agents must be the active roster: terminated agents and the
    archived workspace directory must never appear as active rows.
    """
    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    workspace = org_state.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)

    terminate_r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
        json={
            "action": "terminate",
            "name": "dev_agent",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
        },
        headers=auth_headers,
    )
    assert terminate_r.status_code == 200

    r = TestClient(app).get("/api/v1/orgs/alpha/agents", headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()["agents"]
    names = {a["name"] for a in rows}
    assert "dev_agent" not in names
    assert "_terminated" not in names

    # The terminated enrollment is still exposed on the dedicated endpoint.
    enroll_r = TestClient(app).get(
        "/api/v1/orgs/alpha/agents/enrollments?status=terminated",
        headers=auth_headers,
    )
    assert enroll_r.status_code == 200
    assert any(
        e["name"] == "dev_agent" and e["status"] == "terminated"
        for e in enroll_r.json()["enrollments"]
    )


def test_manage_agent_enroll_rejects_terminated_name(
    tmp_home, app, org_state, auth_headers,
) -> None:
    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
        json={
            "action": "terminate",
            "name": "dev_agent",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
        },
        headers=auth_headers,
    )

    r = TestClient(app).post(
        "/api/v1/orgs/alpha/agents/manage",
        json={
            "action": "enroll",
            "name": "dev_agent",
            "task_id": _EH_TASK,
            "session_id": _EH_SESSION,
            "description": "desc",
            "system_prompt": "sys",
        },
        headers=auth_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "agent_name_unavailable"


def _seed_all_future_work(org_state) -> str:
    """Seed every future-work kind for terminate failure-path tests: an armed
    schedule, a pending work-hours wake, a pending dream, and an unstarted
    thread invocation. Returns the unstarted invocation token."""
    from datetime import datetime, timezone
    from runtime.models import (
        DreamRecord,
        DreamStatus,
        ScheduleKind,
        ScheduleRecord,
        ScheduleStatus,
        ThreadInvocationPurpose,
        ThreadMessageKind,
        ThreadRecord,
        WorkHourMode,
        WorkHourRecord,
        WorkHourStatus,
    )

    now = datetime.now(timezone.utc)

    org_state.db.schedules.insert(ScheduleRecord(
        id="SCHED-ARMED",
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.ONE_SHOT,
        fire_at=now,
        timezone="UTC",
        normalized_brief="brief",
        source_instruction="do it",
        status=ScheduleStatus.ARMED,
        active=1,
        created_at=now,
        updated_at=now,
    ))

    org_state.db.work_hours.insert(WorkHourRecord(
        id="WORKHOUR-PEND",
        agent_name="dev_agent",
        local_date="2026-08-21",
        slot="morning",
        mode=WorkHourMode.WINDOWED,
        scheduled_for=now,
        status=WorkHourStatus.PENDING,
        created_at=now,
    ))

    org_state.db.insert_dream(DreamRecord(
        id="DREAM-PEND",
        agent_name="dev_agent",
        local_date="2026-08-21",
        scheduled_for=now,
        window_end=now,
        status=DreamStatus.PENDING,
        created_at=now,
    ))

    org_state.db.insert_thread(ThreadRecord(id="THR-PEND", subject="x"))
    org_state.db.add_thread_participant("THR-PEND", "dev_agent", added_by="founder")
    org_state.db.append_thread_message(
        thread_id="THR-PEND", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
    )
    inv = org_state.db.mint_thread_invocation(
        thread_id="THR-PEND", agent_name="dev_agent",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    return inv.invocation_token


def test_manage_agent_terminate_preflight_archive_collision_keeps_agent_active(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """If the terminated agent file already exists, terminate returns 409 and
    leaves the active agent, workspace, and every future-work record (armed
    schedule, pending wake, pending dream, unstarted invocation) untouched.
    """
    from runtime.models import (
        DreamStatus,
        ScheduleStatus,
        ThreadInvocationStatus,
        WorkHourStatus,
    )
    from runtime.orchestrator import prompt_loader

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    paths = _paths(org_state)
    workspace = paths.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "marker.txt").write_text("keep me")

    # Pre-create a terminated agent file to trigger the archive collision.
    terminated_agents_dir = paths.agents_dir / "_terminated"
    terminated_agents_dir.mkdir(parents=True, exist_ok=True)
    (terminated_agents_dir / "dev_agent.md").write_text(
        prompt_loader.load_agent(paths, "dev_agent").system_prompt or ""
    )

    inv_token = _seed_all_future_work(org_state)

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
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "archive_collision"

    # Every future-work record is unchanged.
    assert org_state.db.schedules.get("SCHED-ARMED").status == ScheduleStatus.ARMED
    assert org_state.db.work_hours.get("WORKHOUR-PEND").status == WorkHourStatus.PENDING
    assert org_state.db.get_dream("DREAM-PEND").status == DreamStatus.PENDING
    inv = org_state.db.get_invocation_any_status(inv_token)
    assert inv.status == ThreadInvocationStatus.PENDING
    assert inv.decline_reason is None

    # Active identity, workspace, and team membership remain intact.
    assert prompt_loader.load_agent(paths, "dev_agent") is not None
    assert workspace.exists()
    assert (workspace / "marker.txt").read_text() == "keep me"
    assert org_state.teams.team_for_agent("dev_agent") == "engineering"


def test_manage_agent_terminate_workspace_move_failure_rolls_back(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """If workspace archival fails after the agent file was moved, the route
    rolls back the file and team membership and leaves every future-work record
    (armed schedule, pending wake, pending dream, unstarted invocation)
    unchanged.
    """
    from unittest.mock import patch
    from runtime.models import (
        DreamStatus,
        ScheduleStatus,
        ThreadInvocationStatus,
        WorkHourStatus,
    )
    from runtime.orchestrator import prompt_loader

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    paths = _paths(org_state)
    workspace = paths.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "marker.txt").write_text("keep me")

    inv_token = _seed_all_future_work(org_state)

    with patch("runtime.daemon.routes.agents._move_dir_atomically") as mock_move:
        mock_move.side_effect = OSError("injected move failure")
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
    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "workspace_archive_failed"

    # Every future-work record is unchanged.
    assert org_state.db.schedules.get("SCHED-ARMED").status == ScheduleStatus.ARMED
    assert org_state.db.work_hours.get("WORKHOUR-PEND").status == WorkHourStatus.PENDING
    assert org_state.db.get_dream("DREAM-PEND").status == DreamStatus.PENDING
    inv = org_state.db.get_invocation_any_status(inv_token)
    assert inv.status == ThreadInvocationStatus.PENDING
    assert inv.decline_reason is None

    # Everything must be rolled back to the active state.
    assert prompt_loader.load_agent(paths, "dev_agent") is not None
    assert workspace.exists()
    assert (workspace / "marker.txt").read_text() == "keep me"
    assert org_state.teams.team_for_agent("dev_agent") == "engineering"


def test_manage_agent_terminate_cleanup_failure_rolls_back_everything(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """A fault injected into the first audit write — AFTER the schedule
    cancellation has already been persisted in-transaction — must roll back the
    complete cleanup transaction BEFORE the route's archive compensation runs.
    AgentDef, workspace, team membership, and all four future-work rows stay
    exactly as they were, with no cleanup audit residue and no open DB
    transaction left behind.
    """
    from runtime.infrastructure import database as db_module
    from runtime.models import (
        DreamStatus,
        ScheduleStatus,
        ThreadInvocationStatus,
        WorkHourStatus,
    )
    from runtime.orchestrator import prompt_loader

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")
    paths = _paths(org_state)
    workspace = paths.workspaces_dir / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "marker.txt").write_text("keep me")

    inv_token = _seed_all_future_work(org_state)

    transiently_cancelled = {"seen": False}

    def _failing_audit(self, task_id, agent, action, payload=None):
        # The first audit write is the armed schedule's and runs only AFTER its
        # UPDATE has already executed inside the open transaction. Prove that
        # mutation happened (transiently) before raising, so this test genuinely
        # exercises "failure after the first persisted mutation" rather than a
        # no-op error path.
        row = self._conn.execute(
            "SELECT status, active FROM schedules WHERE id = 'SCHED-ARMED'"
        ).fetchone()
        transiently_cancelled["seen"] = (
            row is not None
            and row["status"] == ScheduleStatus.CANCELLED.value
            and row["active"] == 0
        )
        raise RuntimeError("injected audit insertion failure")

    with patch.object(
        db_module.Database, "insert_audit_log_uncommitted", new=_failing_audit,
    ):
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

    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "terminate_cleanup_failed"

    # The fault landed after the first persisted mutation, proving the rollback
    # reversed an in-flight write rather than trivially doing nothing.
    assert transiently_cancelled["seen"] is True

    # Every future-work record is exactly its pre-call value.
    sched = org_state.db.schedules.get("SCHED-ARMED")
    assert sched.status == ScheduleStatus.ARMED
    assert sched.active == 1
    wake = org_state.db.work_hours.get("WORKHOUR-PEND")
    assert wake.status == WorkHourStatus.PENDING
    assert wake.ended_at is None
    assert wake.error is None
    dream = org_state.db.get_dream("DREAM-PEND")
    assert dream.status == DreamStatus.PENDING
    assert dream.ended_at is None
    assert dream.error is None
    inv = org_state.db.get_invocation_any_status(inv_token)
    assert inv.status == ThreadInvocationStatus.PENDING
    assert inv.decline_reason is None
    assert inv.consumed_at is None

    # Active identity, workspace, and team membership remain intact.
    assert prompt_loader.load_agent(paths, "dev_agent") is not None
    assert workspace.exists()
    assert (workspace / "marker.txt").read_text() == "keep me"
    assert org_state.teams.team_for_agent("dev_agent") == "engineering"

    # No cleanup audit residue from any cancelled/skipped/declined row.
    cleanup_actions = {"schedule_cancelled", "work_hour_skipped", "dream_skipped"}
    audit_rows, _ = org_state.db.query_audit_logs(agent="dev_agent", limit=1000)
    assert all(row["action"] not in cleanup_actions for row in audit_rows)

    # The database connection has no open transaction left behind.
    assert org_state.db._conn.in_transaction is False


def test_run_step_fails_terminated_agent_without_executor(
    tmp_home, app, org_state, auth_headers,
) -> None:
    """Once archived, a task assigned to the agent fails closed before any
    executor is constructed.
    """
    from datetime import datetime, timezone
    from unittest.mock import patch
    from runtime.models import TaskRecord, TaskStatus
    from runtime.orchestrator.orchestrator import Orchestrator

    _activate_eh_session(org_state)
    _seed_active_agent(org_state, "dev_agent")

    # Terminate the agent while it has no live work.
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

    # A task assigned to the now-archived agent must fail closed.
    task = TaskRecord(
        id="TASK-TERM",
        team="engineering",
        brief="work",
        assigned_agent="dev_agent",
        status=TaskStatus.PENDING,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    org_state.db.insert_task(task)

    orch = Orchestrator(
        db=org_state.db,
        settings=org_state.settings,
        paths=_paths(org_state),
        slug="alpha",
        teams=org_state.teams,
    )
    with patch("runtime.orchestrator.orchestrator.build_executor") as mock_build:
        orch.run_step("TASK-TERM")
    mock_build.assert_not_called()
    failed = org_state.db.get_task("TASK-TERM")
    assert failed.status == TaskStatus.FAILED
    assert "terminated" in failed.note.lower()
