# manage-agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Engineering Head enroll, update, and terminate agents dynamically via `opc manage-agent`, with founder approval for enrollment. Remove the hardcoded `AgentName` enum — agent names become plain strings.

**Architecture:** New `agent_enrollments` DB table tracks agent lifecycle (pending → approved → terminated). The `AgentName` enum is replaced with plain strings everywhere. The capabilities prompt is built dynamically from the enrollments table. A `manage-agent` skill lets EH call `opc manage-agent --from-file` as a single-line command.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLite, pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/infrastructure/database.py` | Modify | Add `agent_enrollments` table + CRUD methods |
| `tests/test_database.py` | Modify | Tests for enrollment CRUD |
| `src/models.py` | Modify | Remove `AgentName` enum, change `TaskStep.agent` to `str` |
| `tests/test_models.py` | Modify | Remove `test_agent_name_values`, update `test_task_step_creation` |
| `src/orchestrator/capabilities.py` | Modify | Dynamic agent list instead of hardcoded `AGENT_DESCRIPTIONS` |
| `tests/test_capabilities.py` | Modify | Updated for new signature |
| `src/orchestrator/performance_tracker.py` | Modify | `get_all_tiers` takes agent list, not enum |
| `tests/test_performance_tracker.py` | Modify | String-based tiers |
| `src/orchestrator/orchestrator.py` | Modify | String agent names, enrollment-based validation |
| `tests/test_orchestrator.py` | Modify | String agent names everywhere |
| `src/daemon/routes/agents.py` | Modify | Add manage/approve/reject/enrollments routes, update init/list |
| `tests/daemon/test_routes_agents.py` | Modify | Tests for new routes + updated init/list |
| `src/cli.py` | Modify | Add manage-agent, enrollments, approve-agent, reject-agent |
| `tests/test_cli.py` | Modify | Parser + handler tests |
| `protocol/skills/manage-agent/SKILL.md` | Create | Agent-facing skill |
| `tests/test_skills.py` | Modify | Add `"manage-agent"` to parameterized check |

---

### Task 1: Add `agent_enrollments` table + CRUD

**Files:**
- Modify: `src/infrastructure/database.py`
- Modify: `tests/test_database.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_database.py`:

```python
def test_insert_enrollment(db):
    db.insert_enrollment(
        name="content_writer",
        description="Writes destination guides",
        system_prompt="You are the Content Writer...",
        repos={"web-content": "https://github.com/t-benze/web-content.git"},
    )
    e = db.get_enrollment("content_writer")
    assert e is not None
    assert e["name"] == "content_writer"
    assert e["description"] == "Writes destination guides"
    assert e["status"] == "pending"
    assert e["repos"] == '{"web-content": "https://github.com/t-benze/web-content.git"}'


def test_get_enrollment_missing(db):
    assert db.get_enrollment("ghost") is None


def test_list_enrollments_by_status(db):
    db.insert_enrollment("a", "desc a", "prompt a")
    db.insert_enrollment("b", "desc b", "prompt b")
    db.update_enrollment_status("a", "approved")
    pending = db.list_enrollments(status="pending")
    assert len(pending) == 1
    assert pending[0]["name"] == "b"
    approved = db.list_enrollments(status="approved")
    assert len(approved) == 1
    assert approved[0]["name"] == "a"
    all_e = db.list_enrollments()
    assert len(all_e) == 2


def test_update_enrollment_status(db):
    db.insert_enrollment("x", "desc", "prompt")
    db.update_enrollment_status("x", "approved")
    assert db.get_enrollment("x")["status"] == "approved"


def test_update_enrollment_fields(db):
    db.insert_enrollment("x", "old desc", "old prompt")
    db.update_enrollment_fields("x", description="new desc", system_prompt="new prompt", repos={"r": "u"})
    e = db.get_enrollment("x")
    assert e["description"] == "new desc"
    assert e["system_prompt"] == "new prompt"


def test_delete_enrollment(db):
    db.insert_enrollment("x", "desc", "prompt")
    db.delete_enrollment("x")
    assert db.get_enrollment("x") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_database.py -v -k enrollment`
Expected: FAIL — methods not defined

- [ ] **Step 3: Implement**

Add the table to `_create_tables` in `src/infrastructure/database.py`:

```sql
CREATE TABLE IF NOT EXISTS agent_enrollments (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    repos TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

Add CRUD methods to the `Database` class:

```python
def insert_enrollment(
    self,
    name: str,
    description: str,
    system_prompt: str,
    repos: dict[str, str] | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    self._conn.execute(
        "INSERT INTO agent_enrollments (name, description, system_prompt, repos, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
        (name, description, system_prompt, json.dumps(repos or {}), now, now),
    )
    self._conn.commit()

def get_enrollment(self, name: str) -> dict | None:
    row = self._conn.execute(
        "SELECT * FROM agent_enrollments WHERE name = ?", (name,),
    ).fetchone()
    return dict(row) if row else None

def list_enrollments(self, status: str | None = None) -> list[dict]:
    if status:
        rows = self._conn.execute(
            "SELECT * FROM agent_enrollments WHERE status = ? ORDER BY created_at",
            (status,),
        ).fetchall()
    else:
        rows = self._conn.execute(
            "SELECT * FROM agent_enrollments ORDER BY created_at",
        ).fetchall()
    return [dict(r) for r in rows]

def update_enrollment_status(self, name: str, status: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    self._conn.execute(
        "UPDATE agent_enrollments SET status = ?, updated_at = ? WHERE name = ?",
        (status, now, name),
    )
    self._conn.commit()

def update_enrollment_fields(
    self,
    name: str,
    description: str | None = None,
    system_prompt: str | None = None,
    repos: dict[str, str] | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    updates = ["updated_at = ?"]
    params: list = [now]
    if description is not None:
        updates.append("description = ?")
        params.append(description)
    if system_prompt is not None:
        updates.append("system_prompt = ?")
        params.append(system_prompt)
    if repos is not None:
        updates.append("repos = ?")
        params.append(json.dumps(repos))
    params.append(name)
    self._conn.execute(
        f"UPDATE agent_enrollments SET {', '.join(updates)} WHERE name = ?",
        params,
    )
    self._conn.commit()

def delete_enrollment(self, name: str) -> None:
    self._conn.execute("DELETE FROM agent_enrollments WHERE name = ?", (name,))
    self._conn.commit()
```

- [ ] **Step 4: Run enrollment tests**

Run: `uv run pytest tests/test_database.py -v -k enrollment`
Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "feat(db): agent_enrollments table with CRUD methods"
```

---

### Task 2: Remove `AgentName` enum

**Files:**
- Modify: `src/models.py`
- Modify: `tests/test_models.py`
- Modify: `src/orchestrator/orchestrator.py`
- Modify: `tests/test_orchestrator.py`
- Modify: `src/orchestrator/performance_tracker.py`
- Modify: `tests/test_performance_tracker.py`
- Modify: `src/orchestrator/capabilities.py`
- Modify: `tests/test_capabilities.py`
- Modify: `src/daemon/routes/agents.py`
- Modify: `tests/daemon/test_routes_agents.py`
- Modify: `src/cli.py`
- Modify: `tests/test_cli.py`

This is the largest task — it touches many files but every change is mechanical: replace `AgentName.X` with the string `"x"`, remove `AgentName` from imports, update type hints from `AgentName` to `str`.

- [ ] **Step 1: Remove `AgentName` from `src/models.py`**

Delete the `AgentName` class (lines 28-33). Change `TaskStep.agent` from `AgentName` to `str`:

```python
class TaskStep(BaseModel):
    agent: str
    action: str
    description: str
```

- [ ] **Step 2: Update `tests/test_models.py`**

Delete `test_agent_name_values` (lines 31-35). Update `test_task_step_creation`:

```python
def test_task_step_creation():
    step = TaskStep(
        agent="product_manager",
        action="write_spec",
        description="Write feature specification",
    )
    assert step.agent == "product_manager"
    assert step.action == "write_spec"
```

Remove `AgentName` from the import at the top.

- [ ] **Step 3: Update `src/orchestrator/capabilities.py`**

Replace the entire file:

```python
from __future__ import annotations

from src.models import PerformanceTier, StepRecord


def build_capabilities_prompt(
    brief: str,
    agents: list[dict],
    step_number: int,
    max_steps: int,
    prior_steps: list[StepRecord] | None = None,
) -> str:
    """Build the prompt sent to the Engineering Head for each decision step.

    ``agents`` is a list of dicts with keys: name, description, tier.
    """
    sections = [
        "# Task\n",
        brief.strip(),
        "\n## Your Orchestration Capabilities\n",
        "You are the Engineering Head. Analyze the task and decide what to do next.",
        "You can explore the codebase, analyze code, and do research yourself in this session.",
        "You can also delegate work to your team.\n",
        "### Available Agents\n",
        "| Agent | Role | Tier |",
        "|-------|------|------|",
    ]

    for agent in agents:
        sections.append(f"| {agent['name']} | {agent['description']} | {agent['tier']} |")

    sections.extend([
        "\n### Available Actions\n",
        "Return your decision as a JSON object in your completion report's `output_summary` field.\n",
        '**delegate** -- Assign work to an agent:',
        "```json",
        '{"action": "delegate", "agent": "<agent_name>", "prompt": "<detailed instructions for the agent>"}',
        "```\n",
        "**done** -- Task is complete (or you handled it yourself):",
        "```json",
        '{"action": "done", "summary": "<what was accomplished or your findings>"}',
        "```\n",
        "**escalate** -- Needs founder attention:",
        "```json",
        '{"action": "escalate", "reason": "<why this needs escalation>"}',
        "```\n",
        "**manage-agent** -- Enroll, update, or terminate an agent:",
        "Use the manage-agent skill to write a JSON file and call `opc manage-agent --from-file <path>`.",
        "Enrollment requires founder approval before the agent becomes active.\n",
        "### Constraints\n",
        f"- This is step {step_number} of maximum {max_steps}",
        "- Budget authority: auto-approved up to $200 USD single / $100 USD monthly recurring",
        "- Any content about China/HK/Macau political relations must escalate to founder",
    ])

    if prior_steps:
        sections.append("\n### Prior Steps\n")
        for step in prior_steps:
            status = "OK" if step.success else "FAILED"
            sections.append(
                f"**Step {step.step_number}** [{step.agent}] {step.action} -- "
                f"{step.result_summary} ({status})"
            )

    return "\n".join(sections)
```

- [ ] **Step 4: Update `tests/test_capabilities.py`**

Replace the entire file:

```python
from __future__ import annotations

from src.models import StepRecord
from src.orchestrator.capabilities import build_capabilities_prompt


def test_prompt_includes_brief():
    prompt = build_capabilities_prompt(
        brief="Add Alipay support for international cards",
        agents=[],
        step_number=1,
        max_steps=10,
    )
    assert "Add Alipay support for international cards" in prompt


def test_prompt_includes_agent_tiers():
    agents = [
        {"name": "dev_agent", "description": "Implements features", "tier": "yellow"},
        {"name": "product_manager", "description": "Writes specs", "tier": "green"},
    ]
    prompt = build_capabilities_prompt(
        brief="Fix bug",
        agents=agents,
        step_number=1,
        max_steps=10,
    )
    assert "dev_agent" in prompt
    assert "yellow" in prompt
    assert "product_manager" in prompt
    assert "green" in prompt


def test_prompt_includes_step_number():
    prompt = build_capabilities_prompt(
        brief="Explore",
        agents=[],
        step_number=3,
        max_steps=10,
    )
    assert "step 3" in prompt.lower()
    assert "10" in prompt


def test_prompt_includes_prior_steps():
    prior = [
        StepRecord(
            step_number=1,
            agent="product_manager",
            action="delegate: write spec",
            result_summary="Spec written with 5 acceptance criteria",
            success=True,
        ),
    ]
    prompt = build_capabilities_prompt(
        brief="Add feature",
        agents=[],
        step_number=2,
        max_steps=10,
        prior_steps=prior,
    )
    assert "product_manager" in prompt
    assert "Spec written" in prompt


def test_prompt_no_prior_steps():
    prompt = build_capabilities_prompt(
        brief="Explore",
        agents=[],
        step_number=1,
        max_steps=10,
    )
    assert "Prior Steps" not in prompt


def test_prompt_includes_available_actions():
    prompt = build_capabilities_prompt(
        brief="Do something",
        agents=[],
        step_number=1,
        max_steps=10,
    )
    assert "delegate" in prompt
    assert "done" in prompt
    assert "escalate" in prompt
    assert "manage-agent" in prompt


def test_prompt_includes_constraints():
    prompt = build_capabilities_prompt(
        brief="Do something",
        agents=[],
        step_number=1,
        max_steps=10,
    )
    assert "$200" in prompt
    assert "founder" in prompt.lower()
```

- [ ] **Step 5: Update `src/orchestrator/performance_tracker.py`**

Remove `AgentName` import. Change `get_all_tiers` to accept a list of agent name strings:

```python
def get_all_tiers(self, agent_names: list[str]) -> dict[str, PerformanceTier]:
    """Get current tier for a list of agents."""
    tiers: dict[str, PerformanceTier] = {}
    for agent in agent_names:
        scorecard = self._db.get_scorecard(agent)
        if scorecard:
            tiers[agent] = PerformanceTier(scorecard["tier"])
        else:
            tiers[agent] = PerformanceTier.GREEN
    return tiers
```

- [ ] **Step 6: Update `tests/test_performance_tracker.py`**

Remove `AgentName` from imports. Update `test_get_all_tiers`:

```python
def test_get_all_tiers(db, test_settings):
    tracker = PerformanceTracker(db, test_settings)
    _seed_task_results(db, "dev_agent", ["approved"] * 9 + ["revised"])
    tracker.update_scorecard("dev_agent")
    tiers = tracker.get_all_tiers(["dev_agent", "product_manager"])
    assert tiers["dev_agent"] == PerformanceTier.GREEN
    assert tiers["product_manager"] == PerformanceTier.GREEN
```

- [ ] **Step 7: Update `src/orchestrator/orchestrator.py`**

Remove `AgentName` from imports. Replace all `AgentName.ENGINEERING_HEAD` with `"engineering_head"`. Change `_run_agent` signature: `agent: AgentName` → `agent: str`. Remove the `AgentName(next_step.agent)` validation — replace with a check against available agents (for now, just validate workspace exists). Change `_update_recent_tasks` to iterate over workspaces directory:

```python
# In run_task, replace the tiers and capabilities prompt call:
agent_names = [d.name for d in self._runtime.workspaces_dir.iterdir() if d.is_dir() and d.name != "engineering_head"]
tiers = self._tracker.get_all_tiers(agent_names)
agents_for_prompt = []
for name in agent_names:
    enrollment = self._db.get_enrollment(name)
    desc = enrollment["description"] if enrollment else name
    tier = tiers.get(name, PerformanceTier.GREEN)
    agents_for_prompt.append({"name": name, "description": desc, "tier": tier.value})
eh_prompt = build_capabilities_prompt(
    brief=task.brief,
    agents=agents_for_prompt,
    step_number=step_num,
    max_steps=max_steps,
    prior_steps=prior_steps,
)
```

Replace the delegation validation block (lines 152-162):

```python
if next_step.action == "delegate":
    if next_step.agent is None:
        prior_steps.append(StepRecord(
            step_number=step_num,
            agent="unknown",
            action="delegate: missing agent name",
            result_summary="Delegate action had no agent specified",
            success=False,
        ))
        continue

    delegate_workspace = self._runtime.workspaces_dir / next_step.agent
    if not delegate_workspace.exists():
        prior_steps.append(StepRecord(
            step_number=step_num,
            agent=next_step.agent,
            action=f"delegate: {(next_step.prompt or '')[:100]}",
            result_summary=f"No workspace for agent: {next_step.agent!r}",
            success=False,
        ))
        continue

    delegate_result, delegate_report = self._run_agent(
        task_id, next_step.agent, next_step.prompt or "",
    )
```

In `_run_agent`, change parameter type and remove `.value`:

```python
def _run_agent(
    self,
    task_id: str,
    agent: str,
    prompt: str,
    on_session_started: Callable[[str, str, str], None] | None = None,
) -> tuple[ExecutorResult, CompletionReport | None]:
    task = self._db.get_task(task_id)
    agent_name = agent  # was agent.value
    workspace = self._runtime.workspaces_dir / agent_name
    # ... rest unchanged
```

In `_update_recent_tasks`, replace enum iteration with workspace iteration:

```python
def _update_recent_tasks(self, task_id: str) -> None:
    task = self._db.get_task(task_id)
    if task is None:
        return
    summary = (
        f"- **{task_id}** ({task.type.value}): {task.brief} "
        f"-- {task.status.value}\n"
    )
    if not self._runtime.workspaces_dir.exists():
        return
    for ws_dir in self._runtime.workspaces_dir.iterdir():
        if not ws_dir.is_dir():
            continue
        recent_path = ws_dir / "recent_tasks.md"
        if recent_path.exists():
            content = recent_path.read_text()
            recent_path.write_text(content + summary)
```

- [ ] **Step 8: Update `tests/test_orchestrator.py`**

Remove `AgentName` from imports. Replace `AgentName.ENGINEERING_HEAD` with `"engineering_head"` throughout. Update `_setup_workspaces` to take an explicit list of agent names:

```python
_DEFAULT_AGENTS = ["engineering_head", "product_manager", "dev_agent", "payment_agent"]

def _setup_workspaces(runtime, agents: list[str] | None = None):
    for agent in (agents or _DEFAULT_AGENTS):
        ws = runtime.workspaces_dir / agent
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "recent_tasks.md").write_text(f"# Recent Tasks: {agent}\n\n")
        skill = ws / ".claude" / "skills" / "start-task"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text("# start-task\n")
```

In each test's `mock_side_effect`, change `agent == AgentName.ENGINEERING_HEAD` to `agent == "engineering_head"` and `agent.value` to just `agent`.

- [ ] **Step 9: Update `src/daemon/routes/agents.py`**

Remove `AgentName` from imports. Update `list_agents` to query workspaces directory + enrollments instead of iterating the enum. Update `init_agents` to accept any string name (validate workspace or enrollment exists, not enum):

In `list_agents`:
```python
@router.get("/agents")
def list_agents(request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    tracker = PerformanceTracker(state.db, state.settings)
    # Collect agents from workspaces directory
    agents = []
    ws_dir = state.runtime.workspaces_dir
    if ws_dir.exists():
        agent_names = sorted(d.name for d in ws_dir.iterdir() if d.is_dir())
    else:
        agent_names = []
    tiers = tracker.get_all_tiers(agent_names)
    for name in agent_names:
        agents.append({
            "name": name,
            "tier": tiers.get(name, PerformanceTier.GREEN).value,
            "scorecard": state.db.get_scorecard(name),
        })
    return {"agents": agents}
```

In `init_agents`, replace `AgentName` validation with a simple name check:
```python
@router.post("/agents/init")
async def init_agents(body: InitBody, request: Request):
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    if body.agent is None:
        # Init all agents that have workspaces
        ws_dir = state.runtime.workspaces_dir
        targets = sorted(d.name for d in ws_dir.iterdir() if d.is_dir()) if ws_dir.exists() else []
    else:
        targets = [body.agent]

    async def gen():
        protocol_dir = state.settings.get_protocol_dir()
        prompts = load_all_prompts(protocol_dir)
        ctx = ContextBuilder(state.settings)
        for agent_name in targets:
            workspace = state.runtime.workspaces_dir / agent_name
            workspace.mkdir(parents=True, exist_ok=True)
            yield {"data": _json.dumps({"agent": agent_name, "phase": "starting"})}
            try:
                write_default_agent_config(workspace)
                repos = load_agent_config(workspace).get("repos") or {}
                for repo_name, url in repos.items():
                    yield {"data": _json.dumps({
                        "agent": agent_name, "phase": "repo_cloning",
                        "repo": repo_name,
                    })}
                    ok = await asyncio.to_thread(
                        ctx.clone_repo, workspace, repo_name, url,
                    )
                    yield {"data": _json.dumps({
                        "agent": agent_name,
                        "phase": "repo_ready" if ok else "repo_failed",
                        "repo": repo_name,
                    })}
                # Use enrollment system_prompt if available, else protocol preset
                enrollment = state.db.get_enrollment(agent_name)
                sys_prompt = enrollment["system_prompt"] if enrollment else prompts.get(agent_name, "")
                await asyncio.to_thread(
                    ctx.ensure_workspace_ready, workspace, agent_name, sys_prompt,
                )
                await asyncio.to_thread(
                    ctx.create_agent_dirs, workspace, agent_name,
                )
            except Exception as exc:
                yield {"data": _json.dumps({
                    "agent": agent_name, "phase": "error", "detail": str(exc),
                })}
                return
            yield {"data": _json.dumps({"agent": agent_name, "phase": "done"})}
        yield {"data": _json.dumps({"phase": "all_done"})}

    return EventSourceResponse(gen())
```

- [ ] **Step 10: Update `tests/daemon/test_routes_agents.py`**

Update `test_list_agents_returns_tiers` to create workspace directories instead of relying on the enum. Update `test_init_unknown_agent_returns_422` — it should now succeed (no enum validation) or be removed.

- [ ] **Step 11: Update `src/cli.py`**

Remove `AgentName` import. Remove `choices=[a.value for a in AgentName]` from the `init-agent` parser (accept any string).

- [ ] **Step 12: Update `tests/test_cli.py`**

No `AgentName` references to remove (tests already use string values). No changes needed.

- [ ] **Step 13: Run full test suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 14: Commit**

```bash
git add -A
git commit -m "refactor: remove AgentName enum, use plain strings for agent names"
```

---

### Task 3: Daemon routes for manage-agent lifecycle

**Files:**
- Modify: `src/daemon/routes/agents.py`
- Modify: `tests/daemon/test_routes_agents.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/daemon/test_routes_agents.py`:

```python
def test_manage_agent_enroll_creates_pending(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "description": "Writes destination guides",
            "system_prompt": "You are the Content Writer...",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "pending"
    e = daemon_state.db.get_enrollment("content_writer")
    assert e is not None
    assert e["status"] == "pending"


def test_manage_agent_enroll_duplicate_returns_409(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    daemon_state.db.insert_enrollment("content_writer", "desc", "prompt")
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "content_writer",
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 409


def test_manage_agent_enroll_invalid_name_returns_422(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={
            "action": "enroll",
            "name": "Content Writer",
            "description": "desc",
            "system_prompt": "prompt",
        },
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_manage_agent_update_changes_prompt(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
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
                "system_prompt": "new prompt",
            },
            headers=auth_headers,
        )
    assert r.status_code == 200
    assert daemon_state.db.get_enrollment("content_writer")["system_prompt"] == "new prompt"


def test_manage_agent_terminate_removes_workspace(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    daemon_state.db.insert_enrollment("content_writer", "desc", "prompt")
    daemon_state.db.update_enrollment_status("content_writer", "approved")
    workspace = daemon_state.runtime.workspaces_dir / "content_writer"
    workspace.mkdir(parents=True)
    (workspace / "CLAUDE.md").write_text("# test")

    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={"action": "terminate", "name": "content_writer"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert not workspace.exists()
    assert daemon_state.db.get_enrollment("content_writer")["status"] == "terminated"


def test_manage_agent_terminate_nonexistent_returns_404(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    r = TestClient(app).post(
        "/api/v1/agents/manage",
        json={"action": "terminate", "name": "ghost"},
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_approve_agent_bootstraps_workspace(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    daemon_state.db.insert_enrollment("content_writer", "desc", "prompt")

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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/daemon/test_routes_agents.py -v -k "manage_agent or approve or reject or enrollments"`
Expected: FAIL — routes not defined

- [ ] **Step 3: Implement routes**

Add to `src/daemon/routes/agents.py`:

```python
import re

class ManageAgentAction(StrEnum):
    enroll = "enroll"
    update = "update"
    terminate = "terminate"


class ManageAgentBody(BaseModel):
    action: ManageAgentAction
    name: str
    description: str | None = None
    system_prompt: str | None = None
    repos: dict[str, str] | None = None


_VALID_AGENT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@router.post("/agents/manage")
async def manage_agent(body: ManageAgentBody, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)

    if not _VALID_AGENT_NAME.match(body.name):
        raise HTTPException(status_code=422, detail=f"invalid agent name: {body.name!r}")

    if body.action == ManageAgentAction.enroll:
        if not body.description or not body.system_prompt:
            raise HTTPException(status_code=422, detail="description and system_prompt required for enroll")
        if state.db.get_enrollment(body.name) is not None:
            raise HTTPException(status_code=409, detail=f"agent {body.name!r} already enrolled")
        state.db.insert_enrollment(
            name=body.name,
            description=body.description,
            system_prompt=body.system_prompt,
            repos=body.repos,
        )
        return {"ok": True, "status": "pending"}

    elif body.action == ManageAgentAction.update:
        enrollment = state.db.get_enrollment(body.name)
        if enrollment is None:
            raise HTTPException(status_code=404, detail=f"agent {body.name!r} not found")
        if enrollment["status"] != "approved":
            raise HTTPException(status_code=409, detail=f"agent {body.name!r} is {enrollment['status']}, not approved")
        state.db.update_enrollment_fields(
            body.name,
            description=body.description,
            system_prompt=body.system_prompt,
            repos=body.repos,
        )
        # Regenerate workspace files if system_prompt changed
        if body.system_prompt:
            workspace = state.runtime.workspaces_dir / body.name
            if workspace.exists():
                ctx = ContextBuilder(state.settings)
                await asyncio.to_thread(
                    ctx.ensure_workspace_ready, workspace, body.name, body.system_prompt,
                )
        return {"ok": True}

    elif body.action == ManageAgentAction.terminate:
        enrollment = state.db.get_enrollment(body.name)
        if enrollment is None:
            raise HTTPException(status_code=404, detail=f"agent {body.name!r} not found")
        if enrollment["status"] != "approved":
            raise HTTPException(status_code=409, detail=f"agent {body.name!r} is {enrollment['status']}, not approved")
        state.db.update_enrollment_status(body.name, "terminated")
        workspace = state.runtime.workspaces_dir / body.name
        if workspace.exists():
            shutil.rmtree(workspace)
        return {"ok": True}

    raise HTTPException(status_code=422, detail=f"unknown action: {body.action}")


@router.get("/agents/enrollments")
def list_enrollments(request: Request, status: str | None = None) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    enrollments = state.db.list_enrollments(status=status)
    return {"enrollments": [
        {"name": e["name"], "description": e["description"], "status": e["status"],
         "created_at": e["created_at"]}
        for e in enrollments
    ]}


@router.post("/agents/{agent_name}/approve")
async def approve_agent(agent_name: str, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    enrollment = state.db.get_enrollment(agent_name)
    if enrollment is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_name!r} not found")
    if enrollment["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"agent is {enrollment['status']}, not pending")

    state.db.update_enrollment_status(agent_name, "approved")

    # Bootstrap workspace
    workspace = state.runtime.workspaces_dir / agent_name
    workspace.mkdir(parents=True, exist_ok=True)
    write_default_agent_config(workspace)

    # Write repos from enrollment
    repos = json.loads(enrollment["repos"]) if enrollment["repos"] else {}
    if repos:
        from src.daemon.agent_config import add_repo
        for repo_name, url in repos.items():
            add_repo(workspace, repo_name, url)

    ctx = ContextBuilder(state.settings)
    for repo_name, url in repos.items():
        await asyncio.to_thread(ctx.clone_repo, workspace, repo_name, url)

    await asyncio.to_thread(
        ctx.ensure_workspace_ready, workspace, agent_name, enrollment["system_prompt"],
    )
    await asyncio.to_thread(ctx.create_agent_dirs, workspace, agent_name)

    return {"ok": True}


@router.post("/agents/{agent_name}/reject")
def reject_agent(agent_name: str, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    enrollment = state.db.get_enrollment(agent_name)
    if enrollment is None:
        raise HTTPException(status_code=404, detail=f"agent {agent_name!r} not found")
    if enrollment["status"] != "pending":
        raise HTTPException(status_code=409, detail=f"agent is {enrollment['status']}, not pending")
    state.db.update_enrollment_status(agent_name, "rejected")
    return {"ok": True}
```

Add `import json` and `import re` to the top-level imports if not already present. Note: `json` is already imported as `_json` — use that or add a separate import for the enrollment repos parsing.

- [ ] **Step 4: Run route tests**

Run: `uv run pytest tests/daemon/test_routes_agents.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/agents.py tests/daemon/test_routes_agents.py
git commit -m "feat(daemon): manage-agent lifecycle routes (enroll/update/terminate/approve/reject)"
```

---

### Task 4: CLI subcommands

**Files:**
- Modify: `src/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_cli.py`:

```python
def test_manage_agent_parser_enroll():
    parser = build_parser()
    args = parser.parse_args([
        "manage-agent", "enroll",
        "--from-file", "/tmp/enroll.json",
    ])
    assert args.command == "manage-agent"
    assert args.action == "enroll"
    assert args.from_file == "/tmp/enroll.json"


def test_manage_agent_parser_terminate():
    parser = build_parser()
    args = parser.parse_args([
        "manage-agent", "terminate",
        "--name", "content_writer",
    ])
    assert args.action == "terminate"
    assert args.name == "content_writer"


def test_cmd_manage_agent_posts_to_daemon():
    from src.cli import cmd_manage_agent

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"ok": True, "status": "pending"}
    args = MagicMock(
        from_file=None,
        action="enroll", name="content_writer",
        description="Writes guides", system_prompt="You are...",
        repos=None,
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_manage_agent(args)
    args_pos, kwargs = fake.post.call_args
    assert args_pos[0] == "/api/v1/agents/manage"
    assert kwargs["json"]["action"] == "enroll"
    assert kwargs["json"]["name"] == "content_writer"


def test_cmd_manage_agent_from_file(tmp_path):
    import json

    from src.cli import cmd_manage_agent

    payload = {
        "action": "enroll",
        "name": "content_writer",
        "description": "Writes guides",
        "system_prompt": "You are the Content Writer...",
    }
    f = tmp_path / "enroll.json"
    f.write_text(json.dumps(payload))

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"ok": True, "status": "pending"}
    args = MagicMock(
        from_file=str(f),
        action=None, name=None, description=None,
        system_prompt=None, repos=None,
    )
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_manage_agent(args)
    args_pos, kwargs = fake.post.call_args
    assert kwargs["json"]["action"] == "enroll"
    assert kwargs["json"]["name"] == "content_writer"


def test_enrollments_parser():
    parser = build_parser()
    args = parser.parse_args(["enrollments", "--status", "pending"])
    assert args.command == "enrollments"
    assert args.status == "pending"


def test_cmd_enrollments_lists(capsys):
    from src.cli import cmd_enrollments

    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "enrollments": [
            {"name": "content_writer", "description": "Writes", "status": "pending",
             "created_at": "2026-04-17T00:00:00"},
        ],
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_enrollments(MagicMock(status="pending"))
    out = capsys.readouterr().out
    assert "content_writer" in out
    assert "pending" in out


def test_approve_agent_parser():
    parser = build_parser()
    args = parser.parse_args(["approve-agent", "content_writer"])
    assert args.command == "approve-agent"
    assert args.name == "content_writer"


def test_cmd_approve_agent_posts(capsys):
    from src.cli import cmd_approve_agent

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {"ok": True}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        cmd_approve_agent(MagicMock(name="content_writer"))
    fake.post.assert_called_once_with("/api/v1/agents/content_writer/approve", json={})
    assert "approved" in capsys.readouterr().out.lower()


def test_reject_agent_parser():
    parser = build_parser()
    args = parser.parse_args(["reject-agent", "content_writer"])
    assert args.command == "reject-agent"
    assert args.name == "content_writer"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -v -k "manage_agent or enrollments or approve_agent or reject_agent"`
Expected: FAIL

- [ ] **Step 3: Implement handlers and parsers**

Add handlers to `src/cli.py`:

```python
def _manage_agent_payload_from_file(path: str) -> dict:
    """Load a manage-agent payload from a JSON file."""
    import json as _json
    with open(path) as f:
        data = _json.load(f)
    required = ["action", "name"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"manage-agent file missing keys: {missing}")
    return data


def cmd_manage_agent(args: argparse.Namespace) -> None:
    """Agent callback: enroll, update, or terminate an agent."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    import json as _json
    if args.from_file:
        try:
            body = _manage_agent_payload_from_file(args.from_file)
        except (OSError, _json.JSONDecodeError, ValueError) as exc:
            print(f"Error reading manage-agent file {args.from_file}: {exc}")
            sys.exit(1)
    else:
        body = {"action": args.action, "name": args.name}
        if args.description:
            body["description"] = args.description
        if args.system_prompt:
            body["system_prompt"] = args.system_prompt
        if args.repos:
            body["repos"] = _json.loads(args.repos)

    r = client.post("/api/v1/agents/manage", json=body)
    if not _ok(r):
        return
    result = r.json()
    status = result.get("status", "ok")
    print(f"ok: {body['action']} {body['name']} (status: {status})")


def cmd_enrollments(args: argparse.Namespace) -> None:
    """List agent enrollment requests."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    params = {}
    if args.status:
        params["status"] = args.status
    r = client.get("/api/v1/agents/enrollments", params=params)
    if not _ok(r):
        return
    enrollments = r.json()["enrollments"]
    if not enrollments:
        print("No enrollments found.")
        return
    print(f"{'Name':<22} {'Status':<12} {'Description':<40} Created")
    print("-" * 90)
    for e in enrollments:
        desc = e["description"][:37] + "..." if len(e["description"]) > 37 else e["description"]
        print(f"{e['name']:<22} {e['status']:<12} {desc:<40} {e['created_at'][:19]}")


def cmd_approve_agent(args: argparse.Namespace) -> None:
    """Founder action: approve a pending agent enrollment."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post(f"/api/v1/agents/{args.name}/approve", json={})
    if not _ok(r):
        return
    print(f"Approved: {args.name}")


def cmd_reject_agent(args: argparse.Namespace) -> None:
    """Founder action: reject a pending agent enrollment."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.post(f"/api/v1/agents/{args.name}/reject", json={})
    if not _ok(r):
        return
    print(f"Rejected: {args.name}")
```

Add parser entries in `build_parser()`:

```python
    # opc manage-agent
    p_ma = sub.add_parser("manage-agent", help="Enroll, update, or terminate an agent")
    p_ma.add_argument("action", nargs="?", default=None, choices=["enroll", "update", "terminate"])
    p_ma.add_argument("--name", default=None, help="Agent name")
    p_ma.add_argument("--description", default=None, help="Agent description")
    p_ma.add_argument("--system-prompt", dest="system_prompt", default=None, help="System prompt")
    p_ma.add_argument("--repos", default=None, help="JSON dict of repos")
    p_ma.add_argument("--from-file", dest="from_file", default=None,
                       help="Path to JSON file with enrollment payload")
    p_ma.set_defaults(func=cmd_manage_agent)

    # opc enrollments
    p_enroll = sub.add_parser("enrollments", help="List agent enrollment requests")
    p_enroll.add_argument("--status", default=None, choices=["pending", "approved", "rejected", "terminated"])
    p_enroll.set_defaults(func=cmd_enrollments)

    # opc approve-agent
    p_approve = sub.add_parser("approve-agent", help="Approve a pending agent enrollment")
    p_approve.add_argument("name", help="Agent name to approve")
    p_approve.set_defaults(func=cmd_approve_agent)

    # opc reject-agent
    p_reject = sub.add_parser("reject-agent", help="Reject a pending agent enrollment")
    p_reject.add_argument("name", help="Agent name to reject")
    p_reject.set_defaults(func=cmd_reject_agent)
```

- [ ] **Step 4: Run CLI tests**

Run: `uv run pytest tests/test_cli.py -v -k "manage_agent or enrollments or approve_agent or reject_agent"`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(cli): manage-agent, enrollments, approve-agent, reject-agent subcommands"
```

---

### Task 5: manage-agent skill + docs

**Files:**
- Create: `protocol/skills/manage-agent/SKILL.md`
- Modify: `tests/test_skills.py`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Write the skill**

Create `protocol/skills/manage-agent/SKILL.md`:

```markdown
---
name: manage-agent
description: Enroll, update, or terminate an agent. Write a JSON file and call opc manage-agent --from-file to keep the invocation single-line. Enrollment requires founder approval.
---

# manage-agent

Manage the agent roster. You can **enroll** a new agent (requires founder approval), **update** an existing agent's system prompt or description, or **terminate** an agent (removes its workspace).

## Usage

1. **Write a JSON file** to `/tmp/manage-agent-<unique>.json` using the Write tool:

   **Enroll a new agent:**
   ```json
   {
     "action": "enroll",
     "name": "content_writer",
     "description": "Writes destination guides and travel articles",
     "system_prompt": "You are the Content Writer. Your responsibilities are...",
     "repos": {"web-content": "https://github.com/t-benze/web-content.git"}
   }
   ```

   **Update an existing agent:**
   ```json
   {
     "action": "update",
     "name": "content_writer",
     "description": "Updated description",
     "system_prompt": "Updated system prompt..."
   }
   ```

   **Terminate an agent:**
   ```json
   {
     "action": "terminate",
     "name": "content_writer"
   }
   ```

2. **Invoke as a single-line command:**

   ```bash
   opc manage-agent --from-file /tmp/manage-agent-<unique>.json
   ```

## What happens

- **enroll**: Creates a pending enrollment request. The founder must run `opc approve-agent <name>` before the agent's workspace is bootstrapped and the agent becomes available for delegation.
- **update**: Updates the agent's description, system prompt, or repos in the enrollment registry. If the system prompt changes, the workspace's CLAUDE.md is regenerated. Only works on approved agents.
- **terminate**: Marks the agent as terminated and deletes its workspace directory. Only works on approved agents.

## Agent naming

Agent names must be lowercase with underscores only (e.g. `content_writer`, `seo_agent`). No spaces, hyphens, or uppercase.

## Error handling

- If `opc` returns non-zero, retry once after 1 second.
- `409` (duplicate name on enroll, non-approved agent on update/terminate) and `404` (agent not found) are not retryable.
```

- [ ] **Step 2: Update skill test parametrize**

In `tests/test_skills.py`:

```python
@pytest.mark.parametrize("skill_name", ["start-task", "make-worktree", "manage-repo", "manage-agent"])
```

- [ ] **Step 3: Update CLAUDE.md**

Add `manage-agent` to the skills listing and CLI examples.

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add protocol/skills/manage-agent/SKILL.md tests/test_skills.py CLAUDE.md
git commit -m "feat(skills): manage-agent skill + CLAUDE.md update"
```
