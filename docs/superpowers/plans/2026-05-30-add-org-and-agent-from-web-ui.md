# Add Org and Add Agent from the Web UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the founder create new orgs and new agents (worker into existing team, or manager that defines a new team) entirely from the web app, landing each new agent active immediately.

**Architecture:** Two new founder-facing daemon routes (`GET /teams`, `POST /agents`) extend the existing `/api/v1/orgs/{slug}` router; a small `add_team` method joins `TeamsRegistry`; the web app gains two `Dialog`-based flows wired through the existing provider-aware `DataContext` layer.

**Tech Stack:** FastAPI + pydantic v2 (backend), React 18 + TanStack Query v5 + react-router v6 + Tailwind v4 + vitest (frontend), pytest (backend tests). Bearer-token auth for new founder endpoints.

**Spec:** `docs/superpowers/specs/2026-05-30-add-org-and-agent-from-web-ui-design.md`

---

## Task 1: `TeamsRegistry.add_team` method

**Files:**
- Modify: `src/orchestrator/teams.py` (mutation section, after `remove_worker` at line 138)
- Test: `tests/test_teams.py` (append at end)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_teams.py`:

```python
def test_add_team_inserts_with_empty_workers(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    reg = TeamsRegistry.load(rt.root)
    reg.add_team("delta", manager="delta_head")
    assert reg.teams() == ["delta"]
    m = reg.manager_for_team("delta")
    assert m == TeamManager(name="delta_head", team="delta", workers=())


def test_add_team_auto_persists_to_yaml(tmp_path: Path) -> None:
    rt = _runtime(tmp_path)
    reg = TeamsRegistry.load(rt.root)
    reg.add_team("delta", manager="delta_head")
    # Reloading from disk should round-trip.
    reloaded = TeamsRegistry.load(rt.root)
    assert reloaded.teams() == ["delta"]
    assert reloaded.manager_for_team("delta").name == "delta_head"


def test_add_team_does_not_persist_without_root(tmp_path: Path) -> None:
    # Detached registry (no _root) accepts the mutation but does not write.
    reg = TeamsRegistry({})
    reg.add_team("delta", manager="delta_head")
    assert reg.teams() == ["delta"]


def test_add_team_raises_on_duplicate(tmp_path: Path) -> None:
    rt = _runtime_with_teams(tmp_path)
    reg = TeamsRegistry.load(rt.root)
    with pytest.raises(ValueError, match="engineering"):
        reg.add_team("engineering", manager="someone_else")
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/test_teams.py::test_add_team_inserts_with_empty_workers tests/test_teams.py::test_add_team_auto_persists_to_yaml tests/test_teams.py::test_add_team_does_not_persist_without_root tests/test_teams.py::test_add_team_raises_on_duplicate -v
```

Expected: 4 FAIL with `AttributeError: 'TeamsRegistry' object has no attribute 'add_team'`.

- [ ] **Step 3: Implement `add_team`**

Add to `src/orchestrator/teams.py` immediately after the existing `remove_worker` method (around line 138):

```python
    def add_team(self, name: str, manager: str) -> None:
        """Register a new team with the given manager and empty workers.

        Auto-persists to teams.yaml when ``self._root`` is set, matching
        ``add_worker`` / ``remove_worker`` semantics.

        Raises ValueError if a team with this name already exists.
        """
        if name in self._teams:
            raise ValueError(f"team {name!r} already exists")
        self._teams[name] = TeamManager(name=manager, team=name, workers=())
        if self._root is not None:
            self.save()
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/test_teams.py -v
```

Expected: all tests in `test_teams.py` PASS (including the 4 new ones).

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/teams.py tests/test_teams.py
git commit -m "feat(teams): add TeamsRegistry.add_team for founder-driven team creation"
```

---

## Task 2: `GET /api/v1/orgs/{slug}/teams` route

**Files:**
- Create: `src/daemon/routes/teams.py`
- Modify: `src/daemon/app.py` (import + `include_router` block, lines 9 and 146–159)
- Test: `tests/daemon/test_routes_teams.py`

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/test_routes_teams.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_teams_returns_seeded_teams(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).get("/api/v1/orgs/alpha/teams", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    teams = body["teams"]
    # Sorted alphabetically: content before engineering.
    assert [t["name"] for t in teams] == ["content", "engineering"]
    eng = next(t for t in teams if t["name"] == "engineering")
    assert eng["manager"] == "engineering_head"
    assert "product_manager" in eng["workers"]


def test_list_teams_unknown_org_404(tmp_home, app, auth_headers) -> None:
    r = TestClient(app).get("/api/v1/orgs/nonsense/teams", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_org"


def test_list_teams_requires_auth(tmp_home, app) -> None:
    r = TestClient(app).get("/api/v1/orgs/alpha/teams")
    assert r.status_code in (401, 403)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/daemon/test_routes_teams.py -v
```

Expected: 3 FAIL (route returns 404 because it doesn't exist yet).

- [ ] **Step 3: Create the route file**

Write `src/daemon/routes/teams.py`:

```python
"""Founder-facing team registry reads."""
from __future__ import annotations

from fastapi import APIRouter

from src.daemon.auth import require_token
from src.daemon.routes._org_dep import OrgDep

router = APIRouter(dependencies=[require_token()])


@router.get("/teams")
def list_teams(slug: str, org: OrgDep) -> dict:
    """Return all registered teams + their managers + workers.

    Sorted by team name. When ``org.teams`` is None (legacy no-runtime
    branch) returns an empty list — same shape as an empty registry.
    """
    if org.teams is None:
        return {"teams": []}
    rows = []
    for name in org.teams.teams():
        m = org.teams.manager_for_team(name)
        rows.append({
            "name": name,
            "manager": m.name,
            "workers": list(m.workers),
        })
    return {"teams": rows}
```

- [ ] **Step 4: Register the router**

In `src/daemon/app.py`, update the import block at line 9:

```python
from src.daemon.routes import (
    agents,
    assets,
    audit,
    auth,
    health,
    jobs,
    kb,
    orgs,
    runtime,
    talks,
    tasks,
    teams,
    threads,
    tokens,
)
```

(Insert `teams,` alphabetically. The current import is multi-line — keep it sorted.)

Then add an `include_router` line right after the existing agents line (after `app.include_router(agents.router, ...)` at line 151):

```python
    app.include_router(teams.router, prefix="/api/v1/orgs/{slug}")
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest tests/daemon/test_routes_teams.py -v
```

Expected: all 3 PASS.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/routes/teams.py src/daemon/app.py tests/daemon/test_routes_teams.py
git commit -m "feat(daemon): add GET /api/v1/orgs/{slug}/teams for founder UI"
```

---

## Task 3: Extract shared `_validate_allow_rules` helper

**Files:**
- Modify: `src/daemon/routes/agents.py` (existing `ManageAgentBody._reject_unsafe_allow_rules` at lines 92–106)

**Why:** The new `POST /agents` body and the existing `ManageAgentBody` both need the same allow-rules safety check. Pull it out so both bodies call one function. Keeps the spec's "reuse existing validator" promise concrete.

- [ ] **Step 1: Add the helper above `ManageAgentBody`**

Insert into `src/daemon/routes/agents.py` between the `LearningBody` class (line 60) and the `RepoAction` enum (line 62):

```python
_ALLOW_RULES_FORBIDDEN = ("\n", "\r", ";", "|", "&", "`", "$(")


def _validate_allow_rules(values: list[str] | None) -> list[str] | None:
    """Shared validator for ``allow_rules`` arrays.

    Rejects entries containing shell metacharacters that could break the
    Claude/opencode permission matcher. Returns the original list (or None)
    unchanged on success; raises ``ValueError`` on the first bad entry.
    """
    if values is None:
        return values
    for entry in values:
        if not entry or not entry.strip():
            raise ValueError("allow_rules entries must be non-empty")
        if entry != entry.strip():
            raise ValueError("allow_rules entries must not have leading/trailing whitespace")
        for bad in _ALLOW_RULES_FORBIDDEN:
            if bad in entry:
                raise ValueError(f"allow_rules entries must not contain {bad!r}")
    return values
```

- [ ] **Step 2: Replace the inlined validator on `ManageAgentBody`**

Replace lines 92–106 (`@field_validator("allow_rules")` and its method body):

```python
    @field_validator("allow_rules")
    @classmethod
    def _reject_unsafe_allow_rules(cls, v: list[str] | None) -> list[str] | None:
        return _validate_allow_rules(v)
```

- [ ] **Step 3: Run the existing agent route tests to verify nothing broke**

```bash
uv run pytest tests/daemon/test_routes_agents.py -v
```

Expected: all existing tests still PASS (no behavior change).

- [ ] **Step 4: Commit**

```bash
git add src/daemon/routes/agents.py
git commit -m "refactor(agents): extract _validate_allow_rules helper for reuse"
```

---

## Task 4: `POST /api/v1/orgs/{slug}/agents` founder-create route

**Files:**
- Modify: `src/daemon/routes/agents.py` (add `FounderCreateAgentBody` + `create_agent` route)
- Test: `tests/daemon/test_routes_agents_founder_create.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/daemon/test_routes_agents_founder_create.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from src.orchestrator._paths import OrgPaths
from src.orchestrator import prompt_loader


def _post(client, body):
    return client.post("/api/v1/orgs/alpha/agents", json=body)


def _base_worker(name: str = "alpha_worker_1") -> dict:
    return {
        "name": name,
        "role": "worker",
        "team": "engineering",
        "executor": "claude",
        "description": "does some work",
        "system_prompt": "do the work",
    }


def _base_manager(name: str = "delta_head") -> dict:
    return {
        "name": name,
        "role": "manager",
        "new_team": "delta",
        "executor": "claude",
        "description": "owns delta",
        "system_prompt": "manage the delta team",
    }


def test_founder_create_worker_into_existing_team(client_with_runtime) -> None:
    client, org = client_with_runtime
    r = _post(client, _base_worker())
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "alpha_worker_1", "team": "engineering", "role": "worker"}

    # File landed in active agents/, NOT in _pending/.
    paths = OrgPaths(root=org.root)
    assert (paths.agents_dir / "alpha_worker_1.md").exists()
    assert not (paths.pending_agents_dir / "alpha_worker_1.md").exists()

    # AgentDef carries founder marker.
    agent_def = prompt_loader.load_agent(paths, "alpha_worker_1")
    assert agent_def is not None
    assert agent_def.enrolled_by == "founder"
    assert agent_def.team == "engineering"
    assert agent_def.role == "worker"

    # teams.yaml updated.
    assert "alpha_worker_1" in org.teams.manager_for_team("engineering").workers

    # Workspace bootstrapped.
    assert (org.root / "workspaces" / "alpha_worker_1" / "CLAUDE.md").exists()


def test_founder_create_manager_creates_new_team(client_with_runtime) -> None:
    client, org = client_with_runtime
    r = _post(client, _base_manager())
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "delta_head", "team": "delta", "role": "manager"}

    # New team registered.
    assert "delta" in org.teams.teams()
    m = org.teams.manager_for_team("delta")
    assert m.name == "delta_head"
    assert m.workers == ()


def test_invalid_agent_name_returns_422(client_with_runtime) -> None:
    client, _ = client_with_runtime
    bad = _base_worker(name="Has-Dash")
    r = _post(client, bad)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_agent_name"


def test_duplicate_name_returns_409(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_worker(name="alpha_worker_dup")
    assert _post(client, body).status_code == 200
    r = _post(client, body)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "agent_exists"


def test_role_worker_requires_team(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_worker()
    del body["team"]
    r = _post(client, body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "role_team_mismatch"


def test_role_worker_rejects_new_team(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_worker()
    body["new_team"] = "somethingelse"
    r = _post(client, body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "role_team_mismatch"


def test_role_manager_requires_new_team(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_manager()
    del body["new_team"]
    r = _post(client, body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "role_team_mismatch"


def test_role_manager_rejects_team(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_manager()
    body["team"] = "engineering"
    r = _post(client, body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "role_team_mismatch"


def test_worker_with_unknown_team_returns_404(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_worker()
    body["team"] = "nowhere"
    r = _post(client, body)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_team"


def test_manager_with_existing_team_returns_409(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_manager()
    body["new_team"] = "engineering"  # already exists
    r = _post(client, body)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "team_exists"


def test_missing_description_returns_422(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_worker()
    body["description"] = ""
    r = _post(client, body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "missing_required_field"


def test_missing_system_prompt_returns_422(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_worker()
    body["system_prompt"] = ""
    r = _post(client, body)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "missing_required_field"


def test_unsafe_allow_rule_returns_422(client_with_runtime) -> None:
    client, _ = client_with_runtime
    body = _base_worker()
    body["allow_rules"] = ["echo hi; rm -rf /"]
    r = _post(client, body)
    # Pydantic field-validator failures surface as 422 with `detail` as a list.
    assert r.status_code == 422


def test_audit_row_written_with_founder_actor(client_with_runtime) -> None:
    client, org = client_with_runtime
    _post(client, _base_worker(name="audit_check_worker"))
    rows = org.db.get_audit_logs(task_id="founder")
    actions = [r["action"] for r in rows]
    assert "agent_managed" in actions
    last = next(r for r in rows if r["action"] == "agent_managed")
    assert last["actor"] == "founder"
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/daemon/test_routes_agents_founder_create.py -v
```

Expected: all FAIL — the route returns 404 / 405 (`POST /agents` not defined yet).

- [ ] **Step 3: Add the request body model and route handler**

Insert into `src/daemon/routes/agents.py` immediately after the existing `ManageAgentBody` class (after line 119, before `_VALID_AGENT_NAME = re.compile(...)`):

```python
class FounderCreateAgentBody(BaseModel):
    name: str
    role: Literal["worker", "manager"]
    team: str | None = None
    new_team: str | None = None
    executor: Literal["claude", "codex", "opencode"] = "claude"
    description: str
    system_prompt: str
    allow_rules: list[str] | None = None
    repos: dict[str, str] | None = None

    @field_validator("allow_rules")
    @classmethod
    def _reject_unsafe_allow_rules(cls, v: list[str] | None) -> list[str] | None:
        return _validate_allow_rules(v)
```

Then add the route handler. Place it directly after the existing `manage_agent` route (after the `return {"ok": True}` of the terminate branch + the final `raise HTTPException` for unknown action, around line 484):

```python
@router.post("/agents")
async def founder_create_agent(
    slug: str, body: FounderCreateAgentBody, org: OrgDep,
) -> dict:
    """Founder-driven enroll. Lands the agent ACTIVE immediately (no
    pending hop). Worker: assigned to an existing team. Manager: creates
    a new team in teams.yaml as part of the same call.
    """
    paths = OrgPaths(root=org.root)

    # ---- validation ----
    if not _VALID_AGENT_NAME.match(body.name):
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_agent_name", "name": body.name},
        )
    if not body.description.strip() or not body.system_prompt.strip():
        raise HTTPException(
            status_code=422,
            detail={"code": "missing_required_field"},
        )
    if body.role == "worker":
        if not body.team or body.new_team:
            raise HTTPException(
                status_code=422,
                detail={"code": "role_team_mismatch"},
            )
    else:  # manager
        if not body.new_team or body.team:
            raise HTTPException(
                status_code=422,
                detail={"code": "role_team_mismatch"},
            )

    # Duplicate check (pending OR active).
    if (prompt_loader.load_pending_agent(paths, body.name) is not None
            or prompt_loader.load_agent(paths, body.name) is not None):
        raise HTTPException(
            status_code=409,
            detail={"code": "agent_exists", "name": body.name},
        )

    if org.teams is None:
        # Shouldn't happen for a loaded org (orgs without teams.yaml seed
        # an empty registry), but be explicit.
        raise HTTPException(
            status_code=409,
            detail={"code": "no_team_registry"},
        )

    # ---- team mutation + agent file write, under the same lock ----
    async with org.teams_lock:
        if body.role == "worker":
            assert body.team is not None
            if body.team not in org.teams.teams():
                raise HTTPException(
                    status_code=404,
                    detail={"code": "unknown_team", "team": body.team},
                )
            team_name = body.team
            org.teams.add_worker(team_name, body.name)
        else:
            assert body.new_team is not None
            if body.new_team in org.teams.teams():
                raise HTTPException(
                    status_code=409,
                    detail={"code": "team_exists", "team": body.new_team},
                )
            team_name = body.new_team
            org.teams.add_team(team_name, manager=body.name)

        agent_def = AgentDef(
            name=body.name,
            team=team_name,
            role=body.role,
            executor=body.executor,
            allow_rules=tuple(body.allow_rules or []),
            repos=body.repos or {},
            enrolled_by="founder",
            enrolled_at_task=None,
            enrolled_at=datetime.now(timezone.utc),
            system_prompt=body.system_prompt,
            description=body.description,
        )

        # Atomic write directly into active agents/ (skip _pending/).
        from src.orchestrator.agent_def import render_agent_text
        paths.agents_dir.mkdir(parents=True, exist_ok=True)
        active_path = paths.agents_dir / f"{body.name}.md"
        fd, tmp = tempfile.mkstemp(
            prefix=f".{body.name}.", suffix=".md",
            dir=str(paths.agents_dir),
        )
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(render_agent_text(agent_def))
            os.replace(tmp, active_path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    # ---- workspace bootstrap (mirrors approve_agent) ----
    workspace = paths.workspaces_dir / body.name
    workspace.mkdir(parents=True, exist_ok=True)
    write_default_agent_config(workspace)
    set_executor(workspace, agent_def.executor)
    repos = agent_def.repos or {}
    for repo_name, url in repos.items():
        add_repo(workspace, repo_name, url)
    ctx = ContextBuilder(org.settings, paths, slug=org.slug)
    for repo_name, url in repos.items():
        await asyncio.to_thread(ctx.clone_repo, workspace, repo_name, url)
    await asyncio.to_thread(
        ctx.ensure_workspace_ready,
        workspace,
        body.name,
        agent_def.system_prompt,
        provider=load_agent_config(workspace).get("executor") or "claude",
    )
    await asyncio.to_thread(ctx.create_agent_dirs, workspace, body.name)

    AuditLogger(org.db).log_agent_managed(
        scope_id="founder",
        action="enroll",
        name=body.name,
        source="founder",
        actor="founder",
    )
    return {"name": body.name, "team": team_name, "role": body.role}
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest tests/daemon/test_routes_agents_founder_create.py -v
```

Expected: all 14 tests PASS.

If `test_audit_row_written_with_founder_actor` fails because `get_audit_logs(task_id="founder")` returns 0 rows, check that `AuditLogger.log_agent_managed` actually writes to the `task_id` column — `scope_id` in the helper signature maps to the `task_id` column at the storage layer (this is the documented `task_id` overload pattern called out in CLAUDE.md).

- [ ] **Step 5: Run the existing agents route tests to verify no regression**

```bash
uv run pytest tests/daemon/test_routes_agents.py tests/daemon/test_routes_agents_learnings.py -v
```

Expected: all still PASS.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/routes/agents.py tests/daemon/test_routes_agents_founder_create.py
git commit -m "feat(daemon): add POST /agents founder-create (worker or manager+new-team)"
```

---

## Task 5: Regenerate the OpenAPI snapshot

**Files:**
- Modify: `tests/contract/openapi.json`

- [ ] **Step 1: Confirm the snapshot test currently fails**

```bash
uv run pytest tests/contract/test_openapi_snapshot.py -v
```

Expected: FAIL — snapshot is out of date because `/teams` and the new `POST /agents` exist now.

- [ ] **Step 2: Regenerate the snapshot**

```bash
HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py -v
```

Expected: PASS — snapshot rewritten.

- [ ] **Step 3: Sanity-check the new paths exist in the snapshot**

```bash
python3 -c "import json; p=json.load(open('tests/contract/openapi.json'))['paths']; print(sorted(k for k in p if 'teams' in k or 'agents' in k))"
```

Expected output includes:
- `/api/v1/orgs/{slug}/agents`
- `/api/v1/orgs/{slug}/teams`

- [ ] **Step 4: Commit**

```bash
git add tests/contract/openapi.json
git commit -m "chore(contract): regenerate openapi snapshot for new founder routes"
```

---

## Task 6: Integration test for the full founder workflow

**Files:**
- Create: `tests/integration/test_founder_creates_org_and_agents_e2e.py`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_founder_creates_org_and_agents_e2e.py`:

```python
from __future__ import annotations

import pytest
import yaml

pytestmark = pytest.mark.integration


def test_create_org_then_manager_then_worker_e2e(daemon_with_runtime):
    """End-to-end: org init via POST /orgs → POST /agents manager → POST /agents
    worker. Verifies teams.yaml shape, agent files land in active/, and
    workspace bootstrap ran."""
    client, runtime = daemon_with_runtime

    # 1. Create an empty org.
    r = client.post("/api/v1/orgs", json={"slug": "delta-org"})
    assert r.status_code == 200, r.text

    # 2. Create the manager (which creates the team).
    r = client.post("/api/v1/orgs/delta-org/agents", json={
        "name": "alpha_head",
        "role": "manager",
        "new_team": "alpha",
        "executor": "claude",
        "description": "owns alpha",
        "system_prompt": "manage the alpha team",
    })
    assert r.status_code == 200, r.text

    # 3. Create a worker under that team.
    r = client.post("/api/v1/orgs/delta-org/agents", json={
        "name": "alpha_worker_1",
        "role": "worker",
        "team": "alpha",
        "executor": "claude",
        "description": "does work",
        "system_prompt": "do the work",
    })
    assert r.status_code == 200, r.text

    # 4. teams.yaml has the expected shape.
    org_root = runtime.orgs_dir / "delta-org"
    teams_yaml = yaml.safe_load((org_root / "org" / "teams.yaml").read_text())
    assert teams_yaml == {
        "teams": {
            "alpha": {
                "manager": "alpha_head",
                "workers": ["alpha_worker_1"],
            },
        },
    }

    # 5. Both agent files live in active/, not pending/.
    assert (org_root / "org" / "agents" / "alpha_head.md").exists()
    assert (org_root / "org" / "agents" / "alpha_worker_1.md").exists()
    assert not (org_root / "org" / "agents" / "_pending" / "alpha_head.md").exists()

    # 6. Workspace was bootstrapped.
    assert (org_root / "workspaces" / "alpha_head" / "CLAUDE.md").exists()
    assert (org_root / "workspaces" / "alpha_worker_1" / "CLAUDE.md").exists()

    # 7. GET /agents reflects the new roster.
    r = client.get("/api/v1/orgs/delta-org/agents")
    assert r.status_code == 200
    names = {a["name"] for a in r.json()["agents"]}
    assert {"alpha_head", "alpha_worker_1"}.issubset(names)
```

- [ ] **Step 2: Check what integration fixtures already exist**

```bash
grep -n "daemon_with_runtime\|def " tests/integration/conftest.py | head -40
```

If `daemon_with_runtime` does not exist, either:
  (a) Pick whichever existing fixture spins up the full daemon with a fresh runtime (look for fixtures that call `RuntimeDir.init` and yield a `TestClient`), and adapt the test signature; or
  (b) Add a new fixture in `tests/integration/conftest.py` that creates a `RuntimeDir`, builds the app, returns `(TestClient_with_auth, runtime)`. Use the pattern from `tests/daemon/conftest.py:client_with_runtime` as the template, swapping the pre-seeded "alpha" org for a clean container.

The integration suite already runs against real fake-CLI executors; pick whichever existing fixture name matches "clean container, no pre-seeded org" most closely and use it. **Do not add a new top-level fixture unless none exists** — that's a sign the existing pattern works and you should follow it.

- [ ] **Step 3: Run the test**

```bash
uv run pytest tests/integration/test_founder_creates_org_and_agents_e2e.py -v -m integration
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_founder_creates_org_and_agents_e2e.py
# also commit any conftest changes if you needed to add a fixture:
git add tests/integration/conftest.py 2>/dev/null || true
git commit -m "test(integration): founder creates org + manager + worker e2e"
```

---

## Task 7: TS API client — `teams.ts` + `agents.createAgent`

**Files:**
- Create: `web/src/lib/api/teams.ts`
- Modify: `web/src/lib/api/agents.ts`
- Modify: `web/src/lib/api/index.ts`

- [ ] **Step 1: Write `teams.ts`**

Create `web/src/lib/api/teams.ts`:

```typescript
/** Mirror of src/daemon/routes/teams.py */
import { request } from './client';

export interface TeamSummary {
  name: string;
  manager: string;
  workers: string[];
}

export const listTeams = (slug: string): Promise<{ teams: TeamSummary[] }> =>
  request(`/orgs/${slug}/teams`);
```

- [ ] **Step 2: Add `createAgent` to `agents.ts`**

Insert into `web/src/lib/api/agents.ts` immediately after the existing `listAgents` function (around line 32):

```typescript
export interface CreateAgentBody {
  name: string;
  role: 'worker' | 'manager';
  team?: string;
  new_team?: string;
  executor: 'claude' | 'codex' | 'opencode';
  description: string;
  system_prompt: string;
  allow_rules?: string[];
  repos?: Record<string, string>;
}

export const createAgent = (
  slug: string,
  body: CreateAgentBody,
): Promise<{ name: string; team: string; role: 'worker' | 'manager' }> =>
  request(`/orgs/${slug}/agents`, { method: 'POST', body });
```

- [ ] **Step 3: Re-export `teams` from the package barrel**

Insert into `web/src/lib/api/index.ts` (alphabetical, after the `tasks` line):

```typescript
export * as teams from './teams';
```

The barrel should now read (relevant region):

```typescript
export * as agents from './agents';
export * as audit from './audit';
export * as health from './health';
export * as kb from './kb';
export * as orgs from './orgs';
export * as runtime from './runtime';
export * as jobs from './jobs';
export * as talks from './talks';
export * as tasks from './tasks';
export * as teams from './teams';
export * as threads from './threads';
export * as tokens from './tokens';
```

- [ ] **Step 4: Verify the web app still type-checks**

```bash
cd web && npm run typecheck
```

Expected: no errors. (If `typecheck` is not a script, use `npx tsc --noEmit` instead — check `web/package.json` `scripts`.)

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/api/teams.ts web/src/lib/api/agents.ts web/src/lib/api/index.ts
git commit -m "feat(web): add teams API client and agents.createAgent"
```

---

## Task 8: openapi-coverage contract test additions

**Files:**
- Modify: `web/src/test/openapi-coverage.test.ts`

- [ ] **Step 1: Add the two new paths to `INCLUDED_PATHS`**

In `web/src/test/openapi-coverage.test.ts`, find the agents block (lines 104–113 approximately) and insert two lines. The final agents block should read:

```typescript
  // agents — founder-facing (enrollment + read-only learnings)
  'GET /api/v1/orgs/{slug}/agents',
  'POST /api/v1/orgs/{slug}/agents',
  'POST /api/v1/orgs/{slug}/agents/init',
  'GET /api/v1/orgs/{slug}/agents/enrollments',
  'POST /api/v1/orgs/{slug}/agents/{agent_name}/approve',
  'POST /api/v1/orgs/{slug}/agents/{agent_name}/reject',
  'POST /api/v1/orgs/{slug}/agents/backfill-enrollments',
  'GET /api/v1/orgs/{slug}/agents/{agent_name}/learnings/entries/',
  'GET /api/v1/orgs/{slug}/agents/{agent_name}/learnings/entries/{id_or_slug}',
  'POST /api/v1/orgs/{slug}/agents/{agent_name}/learnings/entries/search',
  // teams — founder-facing
  'GET /api/v1/orgs/{slug}/teams',
```

(The new `POST /api/v1/orgs/{slug}/agents` line goes right after the existing `GET` agents line; the new `GET /teams` line goes at the end of the listed routes, before the closing `]);`.)

- [ ] **Step 2: Run the coverage test to verify it passes**

```bash
cd web && npx vitest run src/test/openapi-coverage.test.ts
```

Expected: PASS — every documented daemon route classified.

- [ ] **Step 3: Commit**

```bash
git add web/src/test/openapi-coverage.test.ts
git commit -m "test(web): cover new founder routes in openapi-coverage"
```

---

## Task 9: Extend `DataContext` with `TeamsApi` + `useCreateAgent`

**Files:**
- Modify: `web/src/design-system/providers/DataContext.ts`

- [ ] **Step 1: Add the `TeamsApi` interface**

Insert into `web/src/design-system/providers/DataContext.ts` immediately after the `AgentsApi` block (after line 264, before the JobsApi section header at line 272):

```typescript
// ---------------------------------------------------------------------------
// TeamsApi — minimal read-only roster driving the Add Agent team dropdown.
// ---------------------------------------------------------------------------

export interface TeamsApi {
  useTeamsList: () => QueryLike<{ teams: import('@/lib/api/teams').TeamSummary[] }>;
}
```

- [ ] **Step 2: Add `CreateAgentArgs/Result` types and extend `AgentsApi`**

Above the existing `AgentsApi` interface (around line 243), add new type aliases:

```typescript
export type CreateAgentArgs = Parameters<typeof agentsApi.createAgent>[1];
export type CreateAgentResult = Awaited<ReturnType<typeof agentsApi.createAgent>>;
```

Then inside `AgentsApi` (just before `useApproveAgent`), add:

```typescript
  useCreateAgent: () => MutationLike<CreateAgentArgs, CreateAgentResult>;
```

- [ ] **Step 3: Wire `teams` into `DataContextValue`**

Update the `DataContextValue` interface (lines 344–365) to include `teams`:

```typescript
export interface DataContextValue {
  orgs: OrgsApi;
  agents: AgentsApi;
  audit: AuditApi;
  threads: ThreadsApi;
  tasks: TasksApi;
  kb: KbApi;
  talks: TalksApi;
  teams: TeamsApi;
  health: HealthApi;
  jobs: JobsApi;
  // ...routes hooks unchanged...
  useThreadRoutes: () => ThreadRoutes;
  useTasksRoutes: () => TasksRoutes;
  useKbRoutes: () => KbRoutes;
  useTalksRoutes: () => TalksRoutes;
  useAgentsRoutes: () => AgentsRoutes;
  useJobsRoutes: () => JobsRoutes;
}
```

- [ ] **Step 4: Verify typecheck still fails in providers (proves the contract is enforced)**

```bash
cd web && npm run typecheck
```

Expected: FAIL on `AppProvider.tsx` and `PrototypeProvider.tsx` — they don't yet supply `teams` or `agents.useCreateAgent`. That's the next two tasks.

- [ ] **Step 5: Commit**

```bash
git add web/src/design-system/providers/DataContext.ts
git commit -m "feat(web): extend DataContext with TeamsApi and useCreateAgent"
```

---

## Task 10: Real + mock provider implementations

**Files:**
- Create: `web/src/design-system/providers/_real-teams.ts`
- Create: `web/src/design-system/providers/_mock-teams.ts`
- Modify: `web/src/design-system/providers/_real-agents.ts`
- Modify: `web/src/design-system/providers/_mock-agents.ts`
- Modify: `web/src/mocks/index.ts` and `web/src/mocks/agents.ts` (only if a new mock fixture is needed)

- [ ] **Step 1: Write `_real-teams.ts`**

Create `web/src/design-system/providers/_real-teams.ts`:

```typescript
/**
 * Real (daemon-backed) `TeamsApi`. Private to the providers folder.
 */
import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { teams as teamsApi } from '@/lib/api';
import type { TeamsApi } from './DataContext';

function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

export const realTeamsApi: TeamsApi = {
  useTeamsList: () => {
    const slug = useRealOrgSlug();
    return useQuery({
      queryKey: ['teams', slug],
      queryFn: () => teamsApi.listTeams(slug),
      enabled: !!slug,
      staleTime: 5 * 60 * 1000,
    });
  },
};
```

- [ ] **Step 2: Write `_mock-teams.ts`**

Create `web/src/design-system/providers/_mock-teams.ts`:

```typescript
/**
 * Mock `TeamsApi` for the prototype harness.
 *
 * The mock org has the same two teams as the real sample to keep prototype
 * compositions visually consistent.
 */
import { useQuery } from '@tanstack/react-query';
import type { TeamSummary } from '@/lib/api/teams';
import type { TeamsApi } from './DataContext';

const MOCK_TEAMS: TeamSummary[] = [
  { name: 'content', manager: 'content_manager', workers: ['content_writer', 'content_qa'] },
  { name: 'engineering', manager: 'engineering_head', workers: ['product_manager', 'dev_agent'] },
];

export const mockTeamsApi: TeamsApi = {
  useTeamsList: () =>
    useQuery({
      queryKey: ['mock-teams'],
      queryFn: async (): Promise<{ teams: TeamSummary[] }> => ({ teams: MOCK_TEAMS }),
      staleTime: Infinity,
    }),
};
```

- [ ] **Step 3: Add `useCreateAgent` to `_real-agents.ts`**

In `web/src/design-system/providers/_real-agents.ts`, add the import and mutation. Update the import line to include `CreateAgentArgs`/`CreateAgentResult`:

```typescript
import type {
  AgentsApi,
  ApproveAgentArgs,
  ApproveAgentResult,
  CreateAgentArgs,
  CreateAgentResult,
  RejectAgentResult,
} from './DataContext';
```

Then add the mutation inside `realAgentsApi`, right before `useApproveAgent`:

```typescript
  useCreateAgent: () => {
    const slug = useRealOrgSlug();
    const qc = useQueryClient();
    return useMutation({
      mutationFn: (body: CreateAgentArgs): Promise<CreateAgentResult> =>
        agentsApi.createAgent(slug, body),
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ['agents', slug] });
        qc.invalidateQueries({ queryKey: ['agent-enrollments', slug] });
        qc.invalidateQueries({ queryKey: ['teams', slug] });
      },
    });
  },
```

- [ ] **Step 4: Add `useCreateAgent` to `_mock-agents.ts`**

In `web/src/design-system/providers/_mock-agents.ts`, add to `mockAgentsApi`:

```typescript
  useCreateAgent: () =>
    useMutation({
      mutationFn: async (body: import('@/lib/api/agents').CreateAgentBody) => ({
        name: body.name,
        team: body.team ?? body.new_team ?? '',
        role: body.role,
      }),
    }),
```

- [ ] **Step 5: Verify typecheck passes for the providers**

```bash
cd web && npm run typecheck
```

Expected: FAIL on `AppProvider.tsx` / `PrototypeProvider.tsx` — they still don't pass `teams` into the context value. Next task.

- [ ] **Step 6: Commit**

```bash
git add web/src/design-system/providers/_real-teams.ts web/src/design-system/providers/_mock-teams.ts web/src/design-system/providers/_real-agents.ts web/src/design-system/providers/_mock-agents.ts
git commit -m "feat(web): real + mock TeamsApi and AgentsApi.useCreateAgent"
```

---

## Task 11: Wire providers into AppProvider + PrototypeProvider + add `useTeamsList` hook

**Files:**
- Modify: `web/src/design-system/providers/AppProvider.tsx`
- Modify: `web/src/design-system/providers/PrototypeProvider.tsx`
- Create: `web/src/hooks/teams.ts`

- [ ] **Step 1: Wire `teams` into `AppProvider`**

In `web/src/design-system/providers/AppProvider.tsx`:

Add the import:

```typescript
import { realTeamsApi } from './_real-teams';
```

Add `teams: realTeamsApi,` to the `DataContext.Provider` value object alongside the existing entries.

- [ ] **Step 2: Wire `teams` into `PrototypeProvider`**

In `web/src/design-system/providers/PrototypeProvider.tsx`, add the symmetric `mockTeamsApi` import and `teams: mockTeamsApi,` in the context value. (Read the file first to find the exact location — the structure mirrors `AppProvider`.)

- [ ] **Step 3: Create the public hook**

Create `web/src/hooks/teams.ts`:

```typescript
/**
 * Public, provider-aware teams hook. Compositions import from here.
 */
import { useData } from '@/design-system/providers/DataContext';

export const useTeamsList: ReturnType<typeof useData>['teams']['useTeamsList'] = () =>
  useData().teams.useTeamsList();
```

- [ ] **Step 4: Add `useCreateAgent` to the public agents hook**

In `web/src/hooks/agents.ts`, append:

```typescript
export const useCreateAgent: ReturnType<typeof useData>['agents']['useCreateAgent'] = () =>
  useData().agents.useCreateAgent();
```

- [ ] **Step 5: Verify typecheck passes**

```bash
cd web && npm run typecheck
```

Expected: PASS — the context contract is now satisfied on both providers.

- [ ] **Step 6: Run all web tests to confirm nothing regressed**

```bash
cd web && npx vitest run
```

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add web/src/design-system/providers/AppProvider.tsx web/src/design-system/providers/PrototypeProvider.tsx web/src/hooks/teams.ts web/src/hooks/agents.ts
git commit -m "feat(web): wire TeamsApi into providers; expose useTeamsList + useCreateAgent hooks"
```

---

## Task 12: `AddOrgDialog` component + test

**Files:**
- Create: `web/src/features/orgs/AddOrgDialog.tsx`
- Create: `web/src/features/orgs/AddOrgDialog.test.tsx`
- Create: `web/src/hooks/createOrg.ts` — small mutation hook that doesn't need provider-routing (orgs are not feature-domain'd in the providers layer right now). Alternative: inline `useMutation` in the dialog itself. We'll inline.

- [ ] **Step 1: Write the failing test**

Create `web/src/features/orgs/AddOrgDialog.test.tsx`:

```typescript
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { AddOrgDialog } from './AddOrgDialog';
import * as orgsApi from '@/lib/api/orgs';

function renderDialog(onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AddOrgDialog open onOpenChange={onClose} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe('AddOrgDialog', () => {
  test('Create disabled until slug matches ^[a-z0-9-]{1,40}$', async () => {
    const user = userEvent.setup();
    renderDialog();
    const input = screen.getByLabelText(/slug/i);
    const submit = screen.getByRole('button', { name: /create/i });
    expect(submit).toBeDisabled();

    await user.type(input, 'Bad_Slug');
    expect(submit).toBeDisabled();

    await user.clear(input);
    await user.type(input, 'good-slug-1');
    expect(submit).not.toBeDisabled();
  });

  test('submits POST /orgs and closes on success', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(orgsApi, 'createOrg').mockResolvedValue({ slug: 'good-slug' });
    const onClose = vi.fn();
    renderDialog(onClose);

    await user.type(screen.getByLabelText(/slug/i), 'good-slug');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith({ slug: 'good-slug' }));
  });

  test('surfaces 409 org_exists inline', async () => {
    const user = userEvent.setup();
    vi.spyOn(orgsApi, 'createOrg').mockRejectedValue(
      Object.assign(new Error('exists'), { status: 409, code: 'org_exists' }),
    );
    renderDialog();

    await user.type(screen.getByLabelText(/slug/i), 'taken');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(screen.getByText(/already exists|org_exists/i)).toBeInTheDocument(),
    );
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd web && npx vitest run src/features/orgs/AddOrgDialog.test.tsx
```

Expected: FAIL — `AddOrgDialog` module not found.

- [ ] **Step 3: Implement `AddOrgDialog`**

Create `web/src/features/orgs/AddOrgDialog.tsx`:

```typescript
/**
 * Add Org dialog — opened from the TopBar.
 *
 * Slug-only form, posts to POST /api/v1/orgs (no seeding). On success
 * the orgs list query is invalidated and the user navigates to
 * `/orgs/<new>/threads`.
 */
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { orgs as orgsApi } from '@/lib/api';
import { Button } from '@/design-system/primitives/Button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';

const SLUG_RE = /^[a-z0-9-]{1,40}$/;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AddOrgDialog({ open, onOpenChange }: Props): JSX.Element {
  const [slug, setSlug] = useState('');
  const [serverError, setServerError] = useState<string | null>(null);
  const qc = useQueryClient();
  const navigate = useNavigate();

  const create = useMutation({
    mutationFn: (body: { slug: string }) => orgsApi.createOrg(body),
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ['orgs'] });
      onOpenChange(false);
      navigate(`/orgs/${resp.slug}/threads`);
    },
    onError: (err: unknown) => {
      const e = err as { code?: string; status?: number; message?: string };
      if (e.code === 'org_exists' || e.status === 409) {
        setServerError(`An org with slug "${slug}" already exists.`);
      } else if (e.code === 'invalid_slug') {
        setServerError('Slug must match ^[a-z0-9-]{1,40}$.');
      } else {
        setServerError(e.message ?? 'Could not create org.');
      }
    },
  });

  const valid = SLUG_RE.test(slug);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New org</DialogTitle>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="org-slug">Slug</Label>
          <Input
            id="org-slug"
            value={slug}
            onChange={(e) => {
              setSlug(e.target.value);
              setServerError(null);
            }}
            placeholder="e.g. hk-macau-tourism"
            autoFocus
          />
          <p className="text-fg-muted text-xs">
            Lowercase letters, digits, and hyphens. 1–40 characters.
          </p>
          {serverError && (
            <p className="text-tier-red text-sm">{serverError}</p>
          )}
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            disabled={!valid || create.isPending}
            onClick={() => create.mutate({ slug })}
          >
            {create.isPending ? 'Creating…' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

If any of `Input` / `Label` / `Dialog` primitives are missing or named differently in this repo, check `web/src/design-system/primitives/` and use the real names. (Don't introduce new primitives — match what already exists, e.g. raw `<input>` with the project's standard utility classes if `Input` isn't a primitive yet.)

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd web && npx vitest run src/features/orgs/AddOrgDialog.test.tsx
```

Expected: all 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/features/orgs/AddOrgDialog.tsx web/src/features/orgs/AddOrgDialog.test.tsx
git commit -m "feat(web): add AddOrgDialog with slug validation"
```

---

## Task 13: `AddAgentDialog` component + test

**Files:**
- Create: `web/src/features/agents/AddAgentDialog.tsx`
- Create: `web/src/features/agents/AddAgentDialog.test.tsx`

The dialog has two visible branches (worker vs manager). The new-team default tracks the agent name until the user manually edits it. This is the most complex form in the plan; tests must cover both branches + the linking behavior.

- [ ] **Step 1: Write the failing test**

Create `web/src/features/agents/AddAgentDialog.test.tsx`:

```typescript
import { describe, expect, test, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { AppProvider } from '@/design-system/providers/AppProvider';
import { AddAgentDialog } from './AddAgentDialog';
import * as agentsApi from '@/lib/api/agents';
import * as teamsApi from '@/lib/api/teams';

function renderDialog(props: { open?: boolean; onOpenChange?: (v: boolean) => void } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/orgs/test/agents']}>
        <Routes>
          <Route
            path="/orgs/:slug/agents"
            element={
              <AppProvider client={qc}>
                <AddAgentDialog open={props.open ?? true} onOpenChange={props.onOpenChange ?? (() => {})} />
              </AppProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(teamsApi, 'listTeams').mockResolvedValue({
    teams: [
      { name: 'engineering', manager: 'engineering_head', workers: [] },
      { name: 'content', manager: 'content_manager', workers: [] },
    ],
  });
});

describe('AddAgentDialog', () => {
  test('worker branch: team dropdown populated from useTeamsList', async () => {
    renderDialog();
    await waitFor(() =>
      expect(screen.getByRole('option', { name: 'engineering' })).toBeInTheDocument(),
    );
  });

  test('manager branch: new_team auto-tracks name until edited', async () => {
    const user = userEvent.setup();
    renderDialog();

    await user.click(screen.getByLabelText(/manager/i));
    await user.type(screen.getByLabelText(/^name$/i), 'delta_head');
    const teamField = screen.getByLabelText(/new team name/i) as HTMLInputElement;
    expect(teamField.value).toBe('delta');

    // Manually editing the team field stops the linking.
    await user.clear(teamField);
    await user.type(teamField, 'manual');
    await user.clear(screen.getByLabelText(/^name$/i));
    await user.type(screen.getByLabelText(/^name$/i), 'omega_head');
    expect(teamField.value).toBe('manual'); // did NOT re-link
  });

  test('worker submit sends team-shaped body', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(agentsApi, 'createAgent').mockResolvedValue({
      name: 'w1', team: 'engineering', role: 'worker',
    });
    renderDialog();
    await waitFor(() => screen.getByRole('option', { name: 'engineering' }));

    await user.type(screen.getByLabelText(/^name$/i), 'alpha_w1');
    await user.selectOptions(screen.getByLabelText(/team/i), 'engineering');
    await user.type(screen.getByLabelText(/description/i), 'desc');
    await user.type(screen.getByLabelText(/system prompt/i), 'prompt');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('test', expect.objectContaining({
        name: 'alpha_w1',
        role: 'worker',
        team: 'engineering',
        executor: 'claude',
      })),
    );
    expect(spy.mock.calls[0][1]).not.toHaveProperty('new_team');
  });

  test('manager submit sends new_team-shaped body', async () => {
    const user = userEvent.setup();
    const spy = vi.spyOn(agentsApi, 'createAgent').mockResolvedValue({
      name: 'delta_head', team: 'delta', role: 'manager',
    });
    renderDialog();

    await user.click(screen.getByLabelText(/manager/i));
    await user.type(screen.getByLabelText(/^name$/i), 'delta_head');
    await user.type(screen.getByLabelText(/description/i), 'desc');
    await user.type(screen.getByLabelText(/system prompt/i), 'prompt');
    await user.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith('test', expect.objectContaining({
        name: 'delta_head',
        role: 'manager',
        new_team: 'delta',
      })),
    );
    expect(spy.mock.calls[0][1]).not.toHaveProperty('team');
  });

  test('worker branch with empty teams list disables Create', async () => {
    vi.spyOn(teamsApi, 'listTeams').mockResolvedValue({ teams: [] });
    const user = userEvent.setup();
    renderDialog();
    await waitFor(() => screen.getByText(/no teams yet/i));
    await user.type(screen.getByLabelText(/^name$/i), 'alpha_w1');
    await user.type(screen.getByLabelText(/description/i), 'desc');
    await user.type(screen.getByLabelText(/system prompt/i), 'prompt');
    expect(screen.getByRole('button', { name: /create/i })).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd web && npx vitest run src/features/agents/AddAgentDialog.test.tsx
```

Expected: FAIL — `AddAgentDialog` not found.

- [ ] **Step 3: Implement `AddAgentDialog`**

Create `web/src/features/agents/AddAgentDialog.tsx`:

```typescript
/**
 * AddAgentDialog — founder creates a new agent.
 *
 * Two visible branches keyed off `role`:
 *
 *   - Worker: pick an existing team from `useTeamsList()`. If no teams,
 *     show an inline note and keep Create disabled.
 *   - Manager: type a new team name. Defaults to the agent name with
 *     the trailing `_<suffix>` stripped — auto-tracks until the user
 *     manually edits the team field.
 *
 * Submit sends exactly ONE of `team` / `new_team` based on role, so the
 * backend's role_team_mismatch guard never fires for legitimate clicks.
 */
import { useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/design-system/primitives/Dialog';
import { Input } from '@/design-system/primitives/Input';
import { Label } from '@/design-system/primitives/Label';
import { Textarea } from '@/design-system/primitives/Textarea';
import { useCreateAgent } from '@/hooks/agents';
import { useTeamsList } from '@/hooks/teams';

const NAME_RE = /^[a-z][a-z0-9_]*$/;

function defaultTeamForName(name: string): string {
  if (!name) return '';
  const i = name.lastIndexOf('_');
  if (i <= 0) return name;
  return name.slice(0, i);
}

type Role = 'worker' | 'manager';
type Executor = 'claude' | 'codex' | 'opencode';

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function AddAgentDialog({ open, onOpenChange }: Props): JSX.Element {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const teamsQuery = useTeamsList();
  const teams = teamsQuery.data?.teams ?? [];

  const [name, setName] = useState('');
  const [role, setRole] = useState<Role>('worker');
  const [team, setTeam] = useState('');
  const [newTeam, setNewTeam] = useState('');
  const [linkedToName, setLinkedToName] = useState(true);
  const [executor, setExecutor] = useState<Executor>('claude');
  const [description, setDescription] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [serverError, setServerError] = useState<string | null>(null);

  const create = useCreateAgent();

  const onNameChange = (next: string) => {
    setName(next);
    setServerError(null);
    if (role === 'manager' && linkedToName) {
      setNewTeam(defaultTeamForName(next));
    }
  };

  const onNewTeamChange = (next: string) => {
    setNewTeam(next);
    setLinkedToName(false);
  };

  const onRoleChange = (next: Role) => {
    setRole(next);
    if (next === 'manager') {
      setLinkedToName(true);
      setNewTeam(defaultTeamForName(name));
    }
  };

  const nameOk = NAME_RE.test(name);
  const fieldsOk =
    nameOk &&
    description.trim().length > 0 &&
    systemPrompt.trim().length > 0 &&
    (role === 'worker' ? !!team && teams.length > 0 : !!newTeam);
  const canSubmit = fieldsOk && !create.isPending;

  const onSubmit = () => {
    const body =
      role === 'worker'
        ? {
            name,
            role,
            team,
            executor,
            description,
            system_prompt: systemPrompt,
          }
        : {
            name,
            role,
            new_team: newTeam,
            executor,
            description,
            system_prompt: systemPrompt,
          };
    create.mutate(body, {
      onSuccess: () => {
        onOpenChange(false);
        // Optional: deep-link to the new agent's drawer.
        if (slug) navigate(`/orgs/${slug}/agents/${name}`);
      },
      onError: (err: unknown) => {
        const e = err as { code?: string; message?: string };
        if (e.code === 'agent_exists') {
          setServerError(`An agent named "${name}" already exists.`);
        } else if (e.code === 'team_exists') {
          setServerError(`Team "${newTeam}" already exists.`);
        } else if (e.code === 'unknown_team') {
          setServerError(`Team "${team}" doesn't exist (was it removed?).`);
        } else {
          setServerError(e.message ?? 'Could not create agent.');
        }
      },
    });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New agent</DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          <div>
            <Label htmlFor="agent-name">Name</Label>
            <Input
              id="agent-name"
              value={name}
              onChange={(e) => onNameChange(e.target.value)}
              placeholder="e.g. alpha_worker_1"
              autoFocus
            />
            <p className="text-fg-muted text-xs">Lowercase + digits + underscores; must start with a letter.</p>
          </div>

          <fieldset>
            <legend className="text-sm font-medium">Role</legend>
            <label className="mr-4 inline-flex items-center gap-1">
              <input
                type="radio"
                name="role"
                value="worker"
                checked={role === 'worker'}
                onChange={() => onRoleChange('worker')}
              />
              Worker
            </label>
            <label className="inline-flex items-center gap-1">
              <input
                type="radio"
                name="role"
                value="manager"
                checked={role === 'manager'}
                onChange={() => onRoleChange('manager')}
              />
              Manager
            </label>
          </fieldset>

          {role === 'worker' ? (
            teams.length === 0 ? (
              <p className="text-fg-muted text-sm">
                No teams yet. Add a manager to create the first team.
              </p>
            ) : (
              <div>
                <Label htmlFor="agent-team">Team</Label>
                <select
                  id="agent-team"
                  value={team}
                  onChange={(e) => setTeam(e.target.value)}
                  className="border-border-subtle bg-bg-subtle w-full rounded border p-2 text-sm"
                >
                  <option value="">Select team…</option>
                  {teams.map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </div>
            )
          ) : (
            <div>
              <Label htmlFor="agent-new-team">New team name</Label>
              <Input
                id="agent-new-team"
                value={newTeam}
                onChange={(e) => onNewTeamChange(e.target.value)}
                placeholder="defaults from name"
              />
            </div>
          )}

          <div>
            <Label htmlFor="agent-executor">Executor</Label>
            <select
              id="agent-executor"
              value={executor}
              onChange={(e) => setExecutor(e.target.value as Executor)}
              className="border-border-subtle bg-bg-subtle w-full rounded border p-2 text-sm"
            >
              <option value="claude">claude</option>
              <option value="codex">codex</option>
              <option value="opencode">opencode</option>
            </select>
          </div>

          <div>
            <Label htmlFor="agent-description">Description</Label>
            <Input
              id="agent-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div>
            <Label htmlFor="agent-system-prompt">System prompt</Label>
            <Textarea
              id="agent-system-prompt"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={6}
            />
          </div>

          {serverError && (
            <p className="text-tier-red text-sm">{serverError}</p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canSubmit} onClick={onSubmit}>
            {create.isPending ? 'Creating…' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

Advanced fields (allow_rules / repos) are intentionally NOT in v1 of the dialog — out per the spec's "out of scope" note for advanced founder editing. Wire only the required surface.

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd web && npx vitest run src/features/agents/AddAgentDialog.test.tsx
```

Expected: all 5 tests PASS.

If `Input` / `Label` / `Textarea` / `Dialog` primitives don't exist under those exact names, fall back to raw `<input>` / `<label>` / `<textarea>` with the project's standard utility classes. Verify by reading `web/src/design-system/primitives/` first.

- [ ] **Step 5: Commit**

```bash
git add web/src/features/agents/AddAgentDialog.tsx web/src/features/agents/AddAgentDialog.test.tsx
git commit -m "feat(web): add AddAgentDialog with worker/manager branches"
```

---

## Task 14: Wire `AddOrgDialog` into `TopBar`

**Files:**
- Modify: `web/src/design-system/layouts/AppShell/TopBar.tsx`

- [ ] **Step 1: Add the dialog mount + trigger**

Insert into `web/src/design-system/layouts/AppShell/TopBar.tsx`:

Add to the imports:

```typescript
import { useState } from 'react';
import { Plus } from 'lucide-react';
import { AddOrgDialog } from '@/features/orgs/AddOrgDialog';
```

Inside the `TopBar` function body, add a state hook:

```typescript
const [addOrgOpen, setAddOrgOpen] = useState(false);
```

Add a `+` button next to the org `<Select>`. Insert immediately after the closing `</Select>` (around line 101):

```typescript
<button
  type="button"
  onClick={() => setAddOrgOpen(true)}
  aria-label="Add org"
  title="Add org"
  className="text-fg-muted hover:bg-bg-raised hover:text-fg focus-visible:ring-accent inline-flex h-7 w-7 items-center justify-center rounded transition-colors focus-visible:ring-2 focus-visible:outline-none"
>
  <Plus size={16} aria-hidden="true" />
</button>
```

Then mount the dialog at the end of the `<header>` (just before `</header>`):

```typescript
<AddOrgDialog open={addOrgOpen} onOpenChange={setAddOrgOpen} />
```

(A sticky-item-in-Select implementation was rejected during planning — the `Select` primitive doesn't render arbitrary non-`SelectItem` children cleanly. The button-next-to-Select pattern matches the existing DensityToggle / ThemeToggle aesthetic.)

- [ ] **Step 2: Run existing TopBar tests + the new dialog test**

```bash
cd web && npx vitest run
```

Expected: all PASS. Existing TopBar tests should still pass — the `+` button is a sibling, not a wrapper.

- [ ] **Step 3: Commit**

```bash
git add web/src/design-system/layouts/AppShell/TopBar.tsx
git commit -m "feat(web): wire AddOrgDialog into TopBar org switcher"
```

---

## Task 15: Wire `AddAgentDialog` into `AgentsPage`

**Files:**
- Modify: `web/src/features/agents/AgentsPage.tsx`
- Modify: `web/src/features/agents/AgentsPage.test.tsx` (add a smoke test if not present)

- [ ] **Step 1: Add "Add agent" button + empty-state CTA**

In `web/src/features/agents/AgentsPage.tsx`:

Imports:

```typescript
import { useState } from 'react';
import { Button } from '@/design-system/primitives/Button';
import { AddAgentDialog } from './AddAgentDialog';
```

Inside the `AgentsPage` function, add state:

```typescript
const [addOpen, setAddOpen] = useState(false);
```

In the `<header>` (around the existing PageHeader / Tabs block at lines 60–71), add an "Add agent" button. The cleanest spot is alongside the PageHeader — wrap PageHeader + button in a flex row:

```typescript
<div className="flex items-start justify-between gap-3">
  <PageHeader
    title="Agents"
    meta="Active roster + pending enrollments."
  />
  <Button onClick={() => setAddOpen(true)}>Add agent</Button>
</div>
```

In the empty state for the Active tab (around line 79–82), replace:

```typescript
<EmptyState
  title="No agents yet"
  body="Run happyranch agents init to bootstrap the team."
/>
```

with:

```typescript
<EmptyState
  title="No agents yet"
  body="Add a manager to create your first team."
  action={<Button onClick={() => setAddOpen(true)}>Add agent</Button>}
/>
```

Verify the existing `EmptyState` API accepts an `action` prop by reading
`web/src/design-system/patterns/EmptyState.tsx`. If it doesn't, render the button below the `EmptyState`:

```typescript
<>
  <EmptyState title="No agents yet" body="Add a manager to create your first team." />
  <div className="mt-4 flex justify-center">
    <Button onClick={() => setAddOpen(true)}>Add agent</Button>
  </div>
</>
```

Mount the dialog inside the page (just before the existing `AgentDetailDrawer` mount around line 132):

```typescript
<AddAgentDialog open={addOpen} onOpenChange={setAddOpen} />
```

- [ ] **Step 2: Run the full agents test suite**

```bash
cd web && npx vitest run src/features/agents/
```

Expected: all PASS. (No regressions in `AgentsPage.test.tsx`; the new dialog test from Task 13 still passes.)

- [ ] **Step 3: Commit**

```bash
git add web/src/features/agents/AgentsPage.tsx
git commit -m "feat(web): mount AddAgentDialog from AgentsPage header + empty state"
```

---

## Task 16: Manual smoke + final verification

**Files:** none — pure verification.

- [ ] **Step 1: Run the full backend test suite (unit only)**

```bash
uv run pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 2: Run the integration tests**

```bash
uv run pytest tests/ -v -m integration
```

Expected: PASS — including the new founder-flow e2e from Task 6.

- [ ] **Step 3: Build the web bundle and run all web tests**

```bash
cd web && npm run typecheck && npx vitest run && cd ..
scripts/build_web.sh
```

Expected: typecheck PASS, vitest PASS, build succeeds.

- [ ] **Step 4: Smoke-test in a real browser**

```bash
scripts/daemon.sh restart
happyranch web
```

Manually:
- Click `+` next to the org switcher → AddOrgDialog opens.
- Type `smoke-test` → Create → lands on `/orgs/smoke-test/threads`.
- Navigate to Agents → empty state shows "Add agent" CTA.
- Click → AddAgentDialog opens.
- Choose Manager → type `alpha_head` → confirm new_team auto-fills to `alpha`.
- Fill description + system prompt → Create.
- Verify agent appears in Active tab with team=alpha, role=manager.
- Re-open dialog, Choose Worker → confirm team dropdown shows `alpha` → create `alpha_w1`.
- Verify it appears under team=alpha in Active tab.

- [ ] **Step 5: Final commit (if any housekeeping)**

If any merge-resolution edits were made, commit them. Otherwise skip.

```bash
git status
# only commit if there's anything new — don't create an empty commit
```

---

## Self-review

Done after writing the plan. Findings logged inline above; here is the audit:

**Spec coverage:**
- `add_team` method — Task 1 ✅
- `GET /teams` — Task 2 ✅
- `POST /agents` founder-create — Task 4 ✅
- Validation table (each row) — covered in Task 4 tests ✅
- Audit `scope_id="founder"` — Task 4 test_audit_row_written_with_founder_actor ✅
- Workspace bootstrap mirrors approve_agent — Task 4 implementation ✅
- Active-immediately invariant — Task 4 test_founder_create_worker_into_existing_team asserts no `_pending/` file ✅
- `_validate_allow_rules` extraction — Task 3 ✅
- OpenAPI snapshot + INCLUDED_PATHS — Tasks 5, 8 ✅
- TS client `teams.ts` + `createAgent` — Task 7 ✅
- DataContext extension — Task 9 ✅
- Real + mock providers — Task 10 ✅
- Hooks `useTeamsList` + `useCreateAgent` — Task 11 ✅
- `AddOrgDialog` — Task 12 ✅
- `AddAgentDialog` with worker/manager branches + new_team auto-default — Task 13 ✅
- TopBar integration — Task 14 ✅
- AgentsPage integration — Task 15 ✅
- Integration e2e test — Task 6 ✅
- Out-of-scope items (advanced repos/allow_rules UI, editing, deletion, samples) — explicitly omitted ✅

**Placeholder scan:** none.

**Type consistency:** `CreateAgentBody` / `CreateAgentArgs` / `CreateAgentResult` referenced consistently across Tasks 7, 9, 10. `TeamSummary` defined in `teams.ts` and re-imported via `import('@/lib/api/teams').TeamSummary` in DataContext (matches the existing pattern for `AgentSummary` etc.).

**Test sanity check:** all JSX in test code re-read; no syntax glitches remain.
