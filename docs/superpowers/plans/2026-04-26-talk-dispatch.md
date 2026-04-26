# Talk-Dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an agent in an OPEN talk dispatch a new root task to the orchestrator without ending the talk, with worker-self-only / manager-intra-team authority rules.

**Architecture:** New endpoint `POST /api/v1/talks/{talk_id}/dispatch` lives on the talks router parallel to `/abandon` and `/end`. Authority is the talk itself (the founder is co-present). Dispatcher role is read from the talk's `agent_name` and validated against `TeamsRegistry`. Worker self-dispatch produces a root task with `assigned_agent=<worker>` (skips the EH gate, since the founder co-presence in the talk already gated it). Manager dispatch can target any agent in the manager's team. Linkage to the originating talk is dual-stored: a new nullable `tasks.dispatched_from_talk_id` column (queryable, indexed) AND a `task_dispatched` audit row on the new task carrying the dispatcher's role for observability.

**Tech Stack:** FastAPI, Pydantic v2, SQLite (idempotent ALTER), httpx CLI, fake-claude integration fixture.

---

## File Structure

**Modify:**
- `src/models.py` — add `dispatched_from_talk_id: str | None = None` to `TaskRecord`.
- `src/infrastructure/database.py` — idempotent ALTER + index in `_create_tables`, plumb new column through `insert_task` / `get_task` / `list_tasks`.
- `src/infrastructure/audit_logger.py` — new `log_task_dispatched` method.
- `src/daemon/routes/talks.py` — new `dispatch_task` endpoint on the talks router.
- `src/daemon/routes/tasks.py` — surface `dispatched_from_talk_id` in `GET /tasks/{id}` response payload (it already includes the full TaskRecord; verify no manual filter strips it).
- `src/cli.py` — new `cmd_dispatch` + parser, render the new line in `cmd_details`.
- `protocol/skills/talk/SKILL.md` — extend the carve-out at line 125.
- `CLAUDE.md` — add `opc dispatch --from-file` to the agent-callbacks list.
- `README.md` — same addition in the equivalent section.

**Create:**
- `protocol/skills/dispatch/SKILL.md` — new skill, copied into Claude workspaces by `ClaudeWorkspaceAdapter`.
- `tests/daemon/test_talks_dispatch.py` — route-level unit tests.
- `tests/integration/test_talk_dispatch_e2e.py` — e2e with fake-claude.

**Test (extensions):**
- `tests/test_database.py` — round-trip the new column, idempotent ALTER, index queryable.
- `tests/test_audit_logger.py` (or wherever AuditLogger is unit-tested) — new method test.
- `tests/test_cli.py` — `opc dispatch` happy path + error passthrough; `opc details` renders the new line.

---

## Task 1: Database column + TaskRecord field

**Files:**
- Modify: `src/models.py:40-56`
- Modify: `src/infrastructure/database.py:195-217` (ALTER block + index), `:284-322` (insert_task), `:325-347` (get_task), `:350-374` (list_tasks)
- Test: `tests/test_database.py`

- [ ] **Step 1: Write failing test for round-trip of `dispatched_from_talk_id`**

Append to `tests/test_database.py`:

```python
def test_task_round_trips_dispatched_from_talk_id(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord

    db = Database(tmp_path / "opc.db")
    task = TaskRecord(
        id="TASK-001",
        brief="dispatched task",
        team="engineering",
        assigned_agent="dev_agent",
        dispatched_from_talk_id="TALK-007",
    )
    db.insert_task(task)
    fetched = db.get_task("TASK-001")
    assert fetched is not None
    assert fetched.dispatched_from_talk_id == "TALK-007"


def test_task_round_trips_dispatched_from_talk_id_when_null(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord

    db = Database(tmp_path / "opc.db")
    task = TaskRecord(id="TASK-001", brief="normal task", team="engineering")
    db.insert_task(task)
    fetched = db.get_task("TASK-001")
    assert fetched is not None
    assert fetched.dispatched_from_talk_id is None


def test_idempotent_dispatched_from_talk_id_migration(tmp_path):
    from src.infrastructure.database import Database

    db_path = tmp_path / "opc.db"
    Database(db_path)            # first init creates the column
    Database(db_path)            # second init must NOT raise


def test_dispatched_from_talk_id_index_queryable(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(
        id="TASK-001", brief="a", team="engineering",
        assigned_agent="dev_agent", dispatched_from_talk_id="TALK-007",
    ))
    db.insert_task(TaskRecord(
        id="TASK-002", brief="b", team="engineering",
        assigned_agent="dev_agent",
    ))
    cur = db._conn.execute(
        "SELECT id FROM tasks WHERE dispatched_from_talk_id = ?", ("TALK-007",),
    )
    rows = [r["id"] for r in cur.fetchall()]
    assert rows == ["TASK-001"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_database.py::test_task_round_trips_dispatched_from_talk_id -v`
Expected: FAIL — `TaskRecord` has no `dispatched_from_talk_id` field.

- [ ] **Step 3: Add field to TaskRecord**

In `src/models.py`, add the field after `revisit_of_task_id` (line 47), keeping alphabetic-ish grouping:

```python
class TaskRecord(BaseModel):
    id: str
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: str | None = None
    team: str = "engineering"
    brief: str
    parent_task_id: str | None = None
    revisit_of_task_id: str | None = None
    dispatched_from_talk_id: str | None = None
    block_kind: BlockKind | None = None
    note: str | None = None
    final_artifact_dir: str | None = None
    orchestration_step_count: int = 0
    revision_count: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
```

- [ ] **Step 4: Add idempotent ALTER + index**

In `src/infrastructure/database.py`, find the loop at line 195-213 (the existing `for ddl in (...)` block that issues idempotent `ALTER TABLE tasks ADD COLUMN` statements). Add a new entry to that tuple **before** the closing paren:

```python
"ALTER TABLE tasks ADD COLUMN dispatched_from_talk_id TEXT",
```

Right after the existing `idx_tasks_revisit_of` index creation (line 215-217), add:

```python
self._conn.execute(
    "CREATE INDEX IF NOT EXISTS idx_tasks_dispatched_from_talk_id "
    "ON tasks(dispatched_from_talk_id) "
    "WHERE dispatched_from_talk_id IS NOT NULL"
)
```

- [ ] **Step 5: Plumb the column through insert_task, get_task, list_tasks**

In `src/infrastructure/database.py:insert_task` (around line 284), add `task.dispatched_from_talk_id` to the `params` tuple **after** `task.revisit_of_task_id` (so the column order matches the SQL list). Update both the `_tasks_has_legacy_type_column` and the modern INSERT SQL strings to include the new column name:

```python
params = (
    task.id,
    task.status.value,
    task.assigned_agent,
    task.team,
    task.brief,
    task.revision_count,
    task.created_at.isoformat(),
    task.updated_at.isoformat(),
    task.completed_at.isoformat() if task.completed_at else None,
    task.parent_task_id,
    task.revisit_of_task_id,
    task.dispatched_from_talk_id,
    task.block_kind.value if task.block_kind else None,
    task.note,
    task.orchestration_step_count,
)
```

Modern SQL (currently around lines 315-321):

```python
self._conn.execute(
    """INSERT INTO tasks (id, status, assigned_agent, team, brief,
       revision_count, created_at, updated_at, completed_at, parent_task_id,
       revisit_of_task_id, dispatched_from_talk_id, block_kind, note,
       orchestration_step_count)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    params,
)
```

Legacy-column SQL (around lines 307-313):

```python
self._conn.execute(
    """INSERT INTO tasks (id, type, status, assigned_agent, team, brief,
       revision_count, created_at, updated_at, completed_at, parent_task_id,
       revisit_of_task_id, dispatched_from_talk_id, block_kind, note,
       orchestration_step_count)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
    (params[0], "general") + params[1:],
)
```

In `get_task` (around line 330) add to the `TaskRecord(...)` constructor call:

```python
dispatched_from_talk_id=row["dispatched_from_talk_id"],
```

Same addition in `list_tasks` (around line 354) inside the list comprehension's `TaskRecord(...)`.

Also add the same line to any other `TaskRecord(...)` construction in this file (e.g., the one inside `walk_revisit_chain` at line 506 — search the file with `grep -n "TaskRecord(" src/infrastructure/database.py` and update every site).

- [ ] **Step 6: Run tests to verify pass**

Run: `uv run pytest tests/test_database.py -v`
Expected: PASS — all four new tests + the rest of the existing suite.

- [ ] **Step 7: Commit**

```bash
git add src/models.py src/infrastructure/database.py tests/test_database.py
git commit -m "feat(talk-dispatch): add dispatched_from_talk_id column to tasks"
```

---

## Task 2: AuditLogger.log_task_dispatched

**Files:**
- Modify: `src/infrastructure/audit_logger.py`
- Test: `tests/test_audit_logger.py` (create if missing) or extend nearest existing audit-logger test file

- [ ] **Step 1: Locate or create the audit-logger test file**

Run: `ls tests/test_audit_logger.py 2>/dev/null || echo missing`

If `missing`, create `tests/test_audit_logger.py` with this skeleton:

```python
from __future__ import annotations

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database


def test_log_task_dispatched_records_payload(tmp_path):
    db = Database(tmp_path / "opc.db")
    AuditLogger(db).log_task_dispatched(
        task_id="TASK-001",
        talk_id="TALK-007",
        dispatcher_agent="dev_agent",
        dispatcher_role="worker",
        effective_target="dev_agent",
        team="engineering",
    )
    rows = db.get_audit_logs_for_task("TASK-001")
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "task_dispatched"
    assert row["agent"] == "dev_agent"
    payload = row["payload"]
    assert payload == {
        "talk_id": "TALK-007",
        "dispatcher_agent": "dev_agent",
        "dispatcher_role": "worker",
        "effective_target": "dev_agent",
        "team": "engineering",
    }
```

If the file already exists, append the test function above to it.

Verify the helper used to read the audit row exists:

```bash
uv run python -c "from src.infrastructure.database import Database; print(hasattr(Database, 'get_audit_logs_for_task'))"
```

If the printed value is `False`, replace `db.get_audit_logs_for_task("TASK-001")` in the test with a direct query: `[dict(r) for r in db._conn.execute("SELECT * FROM audit_log WHERE task_id = ?", ("TASK-001",)).fetchall()]` and `payload = json.loads(row["payload"])`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_audit_logger.py::test_log_task_dispatched_records_payload -v`
Expected: FAIL — `AuditLogger` has no `log_task_dispatched` attribute.

- [ ] **Step 3: Add the method**

Append to `src/infrastructure/audit_logger.py` (after `log_revisit_spawned`, before the talk-event section near line 161):

```python
    def log_task_dispatched(
        self,
        *,
        task_id: str,
        talk_id: str,
        dispatcher_agent: str,
        dispatcher_role: str,
        effective_target: str,
        team: str,
    ) -> None:
        """Record on a NEW task that it was dispatched from a talk.

        `dispatcher_role` is "worker" or "manager" — frozen at dispatch time
        so retroactive role changes don't rewrite history. The task_id scope
        is the new task (not the talk); querying by talk_id uses the
        dispatched_from_talk_id column on tasks instead.
        """
        self._db.insert_audit_log(
            task_id=task_id,
            agent=dispatcher_agent,
            action="task_dispatched",
            payload={
                "talk_id": talk_id,
                "dispatcher_agent": dispatcher_agent,
                "dispatcher_role": dispatcher_role,
                "effective_target": effective_target,
                "team": team,
            },
        )
```

- [ ] **Step 4: Run test to verify pass**

Run: `uv run pytest tests/test_audit_logger.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/audit_logger.py tests/test_audit_logger.py
git commit -m "feat(talk-dispatch): add AuditLogger.log_task_dispatched"
```

---

## Task 3: Dispatch endpoint — happy path (worker self-dispatch)

**Files:**
- Modify: `src/daemon/routes/talks.py`
- Create: `tests/daemon/test_talks_dispatch.py`

- [ ] **Step 1: Write failing test for worker self-dispatch happy path**

Create `tests/daemon/test_talks_dispatch.py`:

```python
from __future__ import annotations

from tests.daemon.conftest import open_talk_for


def _seed_dev_agent_workspace(daemon_state):
    """Create just enough on disk to satisfy the unknown_agent check.

    The dispatch endpoint requires the target's workspace dir to exist;
    creating an empty dir is sufficient for unit-level coverage.
    """
    ws = daemon_state.runtime.workspaces_dir / "dev_agent"
    ws.mkdir(parents=True, exist_ok=True)
    # Approved enrollment row so the registered-agent check passes.
    daemon_state.db.insert_enrollment(
        name="dev_agent",
        description="dev",
        system_prompt="You are dev",
        executor="claude",
        repos={},
        allow_rules=[],
        requested_by_agent="founder",
        requested_by_task_id=None,
        requested_by_session_id=None,
    )
    daemon_state.db.update_enrollment_status("dev_agent", "approved")


def test_worker_self_dispatch_happy_path(client_with_runtime):
    client, state = client_with_runtime
    _seed_dev_agent_workspace(state)

    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "Add a /healthz route to the daemon"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_id"].startswith("TASK-")
    assert body["team"] == "engineering"
    assert body["assigned_agent"] == "dev_agent"
    assert body["dispatched_from_talk_id"] == talk_id

    # Persistence verified.
    task = state.db.get_task(body["task_id"])
    assert task is not None
    assert task.brief == "Add a /healthz route to the daemon"
    assert task.team == "engineering"
    assert task.assigned_agent == "dev_agent"
    assert task.parent_task_id is None
    assert task.dispatched_from_talk_id == talk_id

    # Audit row written.
    rows = [
        dict(r)
        for r in state.db._conn.execute(
            "SELECT * FROM audit_log WHERE task_id = ? AND action = 'task_dispatched'",
            (body["task_id"],),
        ).fetchall()
    ]
    assert len(rows) == 1
```

If `state.db.insert_enrollment` / `update_enrollment_status` signatures don't match, run `grep -n "def insert_enrollment\|def update_enrollment_status\|def upsert_enrollment" src/infrastructure/database.py` and adapt the seed helper to whatever the codebase actually exposes.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/daemon/test_talks_dispatch.py::test_worker_self_dispatch_happy_path -v`
Expected: FAIL — endpoint does not exist (404).

- [ ] **Step 3: Add the endpoint skeleton**

In `src/daemon/routes/talks.py`, add the request-body model just below `EndTalkBody` (around line 99):

```python
class DispatchBody(BaseModel):
    brief: str
    target_agent: str | None = None
    team: str | None = None
```

Add imports at the top:

```python
from src.daemon.runner import enqueue_task
from src.models import TaskRecord
```

Add the endpoint at the bottom of the file (after `get_talk`, around line 226):

```python
@router.post("/talks/{talk_id}/dispatch")
async def dispatch_task(talk_id: str, body: DispatchBody, request: Request) -> dict:
    state: DaemonState = _require_active(request.app.state.daemon)

    # 1. Talk exists + open.
    talk = state.db.get_talk(talk_id)
    if talk is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "talk_id": talk_id})
    if talk.status != TalkStatus.OPEN:
        raise HTTPException(
            status_code=400,
            detail={"code": "talk_not_open", "status": talk.status.value},
        )

    # 2. Brief non-empty after strip.
    brief = body.brief.strip()
    if not brief:
        raise HTTPException(status_code=422, detail={"code": "empty_brief"})

    # 3. Resolve dispatcher's team.
    dispatcher = talk.agent_name
    is_manager = state.teams.is_team_manager(dispatcher)
    dispatcher_team = (
        state.teams.team_for_manager(dispatcher) if is_manager
        else state.teams.team_for_agent(dispatcher)
    )
    if dispatcher_team is None:
        raise HTTPException(
            status_code=403,
            detail={"code": "dispatcher_team_unknown", "agent": dispatcher},
        )

    # 4. Resolve effective_team and forbid cross-team.
    effective_team = body.team or dispatcher_team
    if effective_team != dispatcher_team:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "cross_team_dispatch_forbidden",
                "dispatcher_team": dispatcher_team,
                "requested_team": effective_team,
            },
        )

    # 5. Resolve effective_target + role-based assignment rule.
    effective_target = body.target_agent or dispatcher
    if not is_manager and effective_target != dispatcher:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "worker_must_self_dispatch",
                "dispatcher": dispatcher,
                "requested_target": effective_target,
            },
        )
    if is_manager:
        team_meta = state.teams.manager_for_team(dispatcher_team)
        in_team = (
            effective_target == team_meta.name
            or effective_target in team_meta.workers
        )
        if not in_team:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "target_not_in_team",
                    "team": dispatcher_team,
                    "requested_target": effective_target,
                },
            )

    # 6. Target agent is registered AND has a workspace.
    enrollment = state.db.get_enrollment(effective_target)
    workspace_exists = (state.runtime.workspaces_dir / effective_target).exists()
    if enrollment is None or enrollment.get("status") != "approved" or not workspace_exists:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_agent", "agent": effective_target},
        )

    # 7. Insert + audit + enqueue.
    async with state.db_lock:
        task_id = state.db.next_task_id()
        state.db.insert_task(TaskRecord(
            id=task_id,
            brief=brief,
            team=effective_team,
            assigned_agent=effective_target,
            dispatched_from_talk_id=talk_id,
        ))
        AuditLogger(state.db).log_task_dispatched(
            task_id=task_id,
            talk_id=talk_id,
            dispatcher_agent=dispatcher,
            dispatcher_role="manager" if is_manager else "worker",
            effective_target=effective_target,
            team=effective_team,
        )

    await enqueue_task(state, task_id)

    return {
        "task_id": task_id,
        "team": effective_team,
        "assigned_agent": effective_target,
        "dispatched_from_talk_id": talk_id,
    }
```

Verify `enqueue_task` is `async`:

```bash
grep -n "async def enqueue_task\|^def enqueue_task" src/daemon/runner.py
```

If `enqueue_task` is sync (no `async`), drop the `await` and the surrounding async-ness isn't needed for that line — just call `enqueue_task(state, task_id)`. Keep the surrounding function `async` because it uses `async with state.db_lock`.

- [ ] **Step 4: Run test to verify pass**

Run: `uv run pytest tests/daemon/test_talks_dispatch.py::test_worker_self_dispatch_happy_path -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/talks.py tests/daemon/test_talks_dispatch.py
git commit -m "feat(talk-dispatch): add POST /talks/{talk_id}/dispatch (worker self-dispatch)"
```

---

## Task 4: Validation — talk lifecycle errors

**Files:**
- Modify: `tests/daemon/test_talks_dispatch.py`

- [ ] **Step 1: Add failing tests for talk_not_open and not_found**

Append to `tests/daemon/test_talks_dispatch.py`:

```python
def test_dispatch_unknown_talk_returns_404(client_with_runtime):
    client, _ = client_with_runtime
    r = client.post(
        "/api/v1/talks/TALK-999/dispatch",
        json={"brief": "irrelevant"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "not_found"


def test_dispatch_closed_talk_returns_400(client_with_runtime):
    client, state = client_with_runtime
    _seed_dev_agent_workspace(state)
    talk_id = open_talk_for(client, "dev_agent")
    # Close the talk via the abandon endpoint.
    client.post(
        f"/api/v1/talks/{talk_id}/abandon",
        json={"reason": "test"},
    )
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "irrelevant"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "talk_not_open"
    assert r.json()["detail"]["status"] == "abandoned"
```

- [ ] **Step 2: Run tests to verify pass**

Run: `uv run pytest tests/daemon/test_talks_dispatch.py -v`
Expected: PASS — these gates are already implemented in Task 3.

- [ ] **Step 3: Commit**

```bash
git add tests/daemon/test_talks_dispatch.py
git commit -m "test(talk-dispatch): cover talk-lifecycle gating in dispatch"
```

---

## Task 5: Validation — empty brief

**Files:**
- Modify: `tests/daemon/test_talks_dispatch.py`

- [ ] **Step 1: Write the test**

Append:

```python
import pytest


@pytest.mark.parametrize("bad_brief", ["", "   ", "\t\n"])
def test_dispatch_empty_brief_rejected(client_with_runtime, bad_brief):
    client, state = client_with_runtime
    _seed_dev_agent_workspace(state)
    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": bad_brief},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_brief"
```

- [ ] **Step 2: Run tests to verify pass**

Run: `uv run pytest tests/daemon/test_talks_dispatch.py::test_dispatch_empty_brief_rejected -v`
Expected: PASS — Task 3 already raises 422 `empty_brief`.

- [ ] **Step 3: Commit**

```bash
git add tests/daemon/test_talks_dispatch.py
git commit -m "test(talk-dispatch): reject empty brief"
```

---

## Task 6: Validation — dispatcher_team_unknown

**Files:**
- Modify: `tests/daemon/test_talks_dispatch.py`

- [ ] **Step 1: Write failing test**

A talk for an agent that is **not** in any team registry should be rejected. The default seed `TeamsRegistry` includes `engineering` (with `dev_agent` worker) and `content` workers — see `src/orchestrator/teams.py:14`. Pick an agent name that's outside both: `orphan_agent`.

```python
def test_dispatch_dispatcher_team_unknown(client_with_runtime):
    client, state = client_with_runtime
    # Orphan workspace + enrollment so the unknown_agent check would pass.
    ws = state.runtime.workspaces_dir / "orphan_agent"
    ws.mkdir(parents=True, exist_ok=True)
    state.db.insert_enrollment(
        name="orphan_agent",
        description="orphan",
        system_prompt="x",
        executor="claude",
        repos={},
        allow_rules=[],
        requested_by_agent="founder",
        requested_by_task_id=None,
        requested_by_session_id=None,
    )
    state.db.update_enrollment_status("orphan_agent", "approved")

    talk_id = open_talk_for(client, "orphan_agent")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "anything"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "dispatcher_team_unknown"
```

- [ ] **Step 2: Run tests to verify pass**

Run: `uv run pytest tests/daemon/test_talks_dispatch.py::test_dispatch_dispatcher_team_unknown -v`
Expected: PASS — already covered by the `dispatcher_team is None` branch in Task 3.

- [ ] **Step 3: Commit**

```bash
git add tests/daemon/test_talks_dispatch.py
git commit -m "test(talk-dispatch): reject orphan-agent dispatcher"
```

---

## Task 7: Validation — cross_team_dispatch_forbidden

**Files:**
- Modify: `tests/daemon/test_talks_dispatch.py`

- [ ] **Step 1: Write the test**

```python
def test_dispatch_cross_team_forbidden(client_with_runtime):
    client, state = client_with_runtime
    _seed_dev_agent_workspace(state)  # dev_agent on engineering team
    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "x", "team": "content"},
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "cross_team_dispatch_forbidden"
    assert detail["dispatcher_team"] == "engineering"
    assert detail["requested_team"] == "content"
```

- [ ] **Step 2: Run tests to verify pass**

Run: `uv run pytest tests/daemon/test_talks_dispatch.py::test_dispatch_cross_team_forbidden -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/daemon/test_talks_dispatch.py
git commit -m "test(talk-dispatch): reject cross-team body.team override"
```

---

## Task 8: Validation — worker_must_self_dispatch

**Files:**
- Modify: `tests/daemon/test_talks_dispatch.py`

- [ ] **Step 1: Write the test**

```python
def test_dispatch_worker_must_self_dispatch(client_with_runtime):
    client, state = client_with_runtime
    _seed_dev_agent_workspace(state)
    # Add a second registered worker on the engineering team.
    ws = state.runtime.workspaces_dir / "qa_engineer"
    ws.mkdir(parents=True, exist_ok=True)
    state.db.insert_enrollment(
        name="qa_engineer",
        description="qa",
        system_prompt="x",
        executor="claude",
        repos={},
        allow_rules=[],
        requested_by_agent="founder",
        requested_by_task_id=None,
        requested_by_session_id=None,
    )
    state.db.update_enrollment_status("qa_engineer", "approved")

    talk_id = open_talk_for(client, "dev_agent")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "x", "target_agent": "qa_engineer"},
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "worker_must_self_dispatch"
    assert detail["dispatcher"] == "dev_agent"
    assert detail["requested_target"] == "qa_engineer"
```

- [ ] **Step 2: Run tests to verify pass**

Run: `uv run pytest tests/daemon/test_talks_dispatch.py::test_dispatch_worker_must_self_dispatch -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/daemon/test_talks_dispatch.py
git commit -m "test(talk-dispatch): reject worker dispatching to peer"
```

---

## Task 9: Validation — manager dispatch (intra-team and out-of-team)

**Files:**
- Modify: `tests/daemon/test_talks_dispatch.py`

- [ ] **Step 1: Write the tests**

```python
def _seed_eh_workspace(state):
    ws = state.runtime.workspaces_dir / "engineering_head"
    ws.mkdir(parents=True, exist_ok=True)
    state.db.insert_enrollment(
        name="engineering_head",
        description="eh",
        system_prompt="x",
        executor="claude",
        repos={},
        allow_rules=[],
        requested_by_agent="founder",
        requested_by_task_id=None,
        requested_by_session_id=None,
    )
    state.db.update_enrollment_status("engineering_head", "approved")


def test_manager_dispatches_to_team_worker(client_with_runtime):
    client, state = client_with_runtime
    _seed_dev_agent_workspace(state)
    _seed_eh_workspace(state)

    talk_id = open_talk_for(client, "engineering_head")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "implement X", "target_agent": "dev_agent"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["assigned_agent"] == "dev_agent"
    assert body["team"] == "engineering"

    rows = [
        dict(r)
        for r in state.db._conn.execute(
            "SELECT * FROM audit_log WHERE task_id = ? AND action = 'task_dispatched'",
            (body["task_id"],),
        ).fetchall()
    ]
    assert len(rows) == 1
    import json as _json
    payload = _json.loads(rows[0]["payload"])
    assert payload["dispatcher_role"] == "manager"
    assert payload["dispatcher_agent"] == "engineering_head"


def test_manager_target_not_in_team(client_with_runtime):
    client, state = client_with_runtime
    _seed_eh_workspace(state)
    # Add an agent on the content team.
    ws = state.runtime.workspaces_dir / "content_writer"
    ws.mkdir(parents=True, exist_ok=True)
    state.db.insert_enrollment(
        name="content_writer",
        description="cw",
        system_prompt="x",
        executor="claude",
        repos={},
        allow_rules=[],
        requested_by_agent="founder",
        requested_by_task_id=None,
        requested_by_session_id=None,
    )
    state.db.update_enrollment_status("content_writer", "approved")

    talk_id = open_talk_for(client, "engineering_head")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "x", "target_agent": "content_writer"},
    )
    assert r.status_code == 403
    detail = r.json()["detail"]
    assert detail["code"] == "target_not_in_team"
    assert detail["team"] == "engineering"
    assert detail["requested_target"] == "content_writer"
```

- [ ] **Step 2: Run tests to verify pass**

Run: `uv run pytest tests/daemon/test_talks_dispatch.py -v`
Expected: PASS — already implemented by Task 3.

- [ ] **Step 3: Commit**

```bash
git add tests/daemon/test_talks_dispatch.py
git commit -m "test(talk-dispatch): cover manager intra-team and out-of-team rules"
```

---

## Task 10: Validation — unknown_agent (workspace missing)

**Files:**
- Modify: `tests/daemon/test_talks_dispatch.py`

- [ ] **Step 1: Write the test**

```python
def test_dispatch_unknown_agent_when_workspace_missing(client_with_runtime):
    client, state = client_with_runtime
    # Manager talk so role check passes; target agent has enrollment but no workspace.
    _seed_eh_workspace(state)
    state.db.insert_enrollment(
        name="dev_agent",
        description="dev",
        system_prompt="x",
        executor="claude",
        repos={},
        allow_rules=[],
        requested_by_agent="founder",
        requested_by_task_id=None,
        requested_by_session_id=None,
    )
    state.db.update_enrollment_status("dev_agent", "approved")
    # No workspace dir created on disk.

    talk_id = open_talk_for(client, "engineering_head")
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "x", "target_agent": "dev_agent"},
    )
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail["code"] == "unknown_agent"
    assert detail["agent"] == "dev_agent"
```

- [ ] **Step 2: Run tests to verify pass**

Run: `uv run pytest tests/daemon/test_talks_dispatch.py::test_dispatch_unknown_agent_when_workspace_missing -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/daemon/test_talks_dispatch.py
git commit -m "test(talk-dispatch): reject when target workspace missing"
```

---

## Task 11: CLI — `opc dispatch --from-file`

**Files:**
- Modify: `src/cli.py`
- Test: `tests/test_cli.py` (or appropriate existing CLI test file)

- [ ] **Step 1: Write failing test for the CLI happy path**

Find the existing CLI test pattern by running:

```bash
grep -n "def test_.*dispatch\|monkeypatch.*OpcClient\|cmd_manage_agent" tests/test_cli.py | head
```

Append (or create the file if absent) the following test, mirroring whichever CLI-mock pattern the file already uses:

```python
import json
from pathlib import Path

import src.cli as cli


class _StubResp:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)
    def json(self):
        return self._body


class _StubClient:
    def __init__(self, resp: _StubResp):
        self._resp = resp
        self.last_call = None
    def post(self, path, json=None, **_):
        self.last_call = (path, json)
        return self._resp


def test_cmd_dispatch_happy_path(tmp_path, monkeypatch, capsys):
    payload = {
        "talk_id": "TALK-001",
        "brief": "make a thing",
        "target_agent": "dev_agent",
        "team": "engineering",
    }
    f = tmp_path / "dispatch.json"
    f.write_text(json.dumps(payload))

    stub = _StubClient(_StubResp(200, {
        "task_id": "TASK-042",
        "team": "engineering",
        "assigned_agent": "dev_agent",
        "dispatched_from_talk_id": "TALK-001",
    }))
    monkeypatch.setattr(cli.OpcClient, "from_env", staticmethod(lambda: stub))

    args = type("Args", (), {"from_file": str(f)})()
    cli.cmd_dispatch(args)
    out = capsys.readouterr().out
    assert "TASK-042" in out
    assert stub.last_call[0] == "/api/v1/talks/TALK-001/dispatch"
    assert stub.last_call[1] == {
        "brief": "make a thing",
        "target_agent": "dev_agent",
        "team": "engineering",
    }


def test_cmd_dispatch_missing_talk_id_raises(tmp_path, monkeypatch):
    f = tmp_path / "bad.json"
    f.write_text(json.dumps({"brief": "x"}))
    monkeypatch.setattr(
        cli.OpcClient, "from_env",
        staticmethod(lambda: _StubClient(_StubResp(200, {}))),
    )
    args = type("Args", (), {"from_file": str(f)})()
    import pytest as _pyt
    with _pyt.raises(SystemExit):
        cli.cmd_dispatch(args)
```

If the existing test file uses a different stubbing convention, adapt to match — but preserve the assertions on the URL path and body shape.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py::test_cmd_dispatch_happy_path -v`
Expected: FAIL — `cli.cmd_dispatch` does not exist.

- [ ] **Step 3: Add the helper, command function, and parser**

In `src/cli.py`, add a payload validator next to `_manage_agent_payload_from_file` (around line 506):

```python
def _dispatch_payload_from_file(path: str) -> dict:
    """Load a dispatch payload from JSON file.

    Required: talk_id, brief. Optional: target_agent, team.
    """
    import json as _json
    with open(path) as f:
        data = _json.load(f)
    if not data.get("talk_id"):
        raise ValueError("dispatch file missing 'talk_id'")
    if not data.get("brief") or not str(data["brief"]).strip():
        raise ValueError("dispatch file missing or empty 'brief'")
    return data
```

Add the command function (next to `cmd_manage_agent`, around line 531):

```python
def cmd_dispatch(args: argparse.Namespace) -> None:
    """Agent callback: dispatch a new task from inside an open talk."""
    import json as _json
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    try:
        data = _dispatch_payload_from_file(args.from_file)
    except (OSError, _json.JSONDecodeError, ValueError) as exc:
        print(f"Error reading dispatch file {args.from_file}: {exc}")
        sys.exit(1)
    talk_id = data["talk_id"]
    body = {"brief": data["brief"]}
    if data.get("target_agent"):
        body["target_agent"] = data["target_agent"]
    if data.get("team"):
        body["team"] = data["team"]
    r = client.post(f"/api/v1/talks/{talk_id}/dispatch", json=body)
    if not _ok(r):
        return
    result = r.json()
    print(
        f"ok: dispatched {result['task_id']} "
        f"(team={result['team']} agent={result['assigned_agent']} "
        f"from {result['dispatched_from_talk_id']})"
    )
```

Add the parser block in the `main()` function below the `manage-agent` block (around line 1168, just after `p_ma.set_defaults(...)`):

```python
    # opc dispatch
    p_dispatch = sub.add_parser("dispatch", help="Dispatch a new task from an open talk")
    p_dispatch.add_argument(
        "--from-file", dest="from_file", required=True,
        help="Path to JSON file with dispatch payload (talk_id, brief, optional target_agent/team)",
    )
    p_dispatch.set_defaults(func=cmd_dispatch)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `uv run pytest tests/test_cli.py::test_cmd_dispatch_happy_path tests/test_cli.py::test_cmd_dispatch_missing_talk_id_raises -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(talk-dispatch): add 'opc dispatch --from-file' CLI"
```

---

## Task 12: `opc details` rendering

**Files:**
- Modify: `src/cli.py:cmd_details` (around line 215-234)
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write failing test for the rendered line**

Add to `tests/test_cli.py`:

```python
def test_cmd_details_renders_dispatched_from(monkeypatch, capsys):
    body = {
        "task": {
            "id": "TASK-042",
            "team": "engineering",
            "status": "completed",
            "assigned_agent": "dev_agent",
            "brief": "x",
            "created_at": "2026-04-26T10:00:00+00:00",
            "updated_at": "2026-04-26T10:05:00+00:00",
            "dispatched_from_talk_id": "TALK-007",
        },
        "audit_log": [
            {
                "timestamp": "2026-04-26T10:00:00+00:00",
                "agent": "dev_agent",
                "action": "task_dispatched",
                "payload": {
                    "talk_id": "TALK-007",
                    "dispatcher_agent": "dev_agent",
                    "dispatcher_role": "worker",
                    "effective_target": "dev_agent",
                    "team": "engineering",
                },
            },
        ],
    }
    stub = _StubClient(_StubResp(200, body))
    # Match whatever cmd_details uses on OpcClient. Inspect the function:
    # client.get(f"/api/v1/tasks/{args.task_id}") — so .get is what we stub.
    def _get(self, path, **_):
        return _StubResp(200, body)
    monkeypatch.setattr(cli.OpcClient, "from_env", staticmethod(lambda: stub))
    monkeypatch.setattr(_StubClient, "get", lambda self, p, **_: _StubResp(200, body), raising=False)

    args = type("Args", (), {"task_id": "TASK-042"})()
    cli.cmd_details(args)
    out = capsys.readouterr().out
    assert "Dispatched from: TALK-007" in out
    assert "dev_agent / worker" in out
```

If your `_StubClient` only has `.post`, extend it to also support `.get` returning `self._resp`. Adapt to whatever shape the existing CLI test fixtures use.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_cmd_details_renders_dispatched_from -v`
Expected: FAIL — no "Dispatched from" line emitted.

- [ ] **Step 3: Update cmd_details**

In `src/cli.py:cmd_details`, after the `task = body["task"]` line and before the existing revisit-header block (around line 201), add the dispatch line. Place it after the revisit header so revisit-of-a-dispatch still reads naturally (revisit context first):

```python
    # Dispatch header: shown only when this task was dispatched from a talk.
    if task.get("dispatched_from_talk_id"):
        # Pull dispatcher fields from the task_dispatched audit row.
        dispatcher = "?"
        role = "?"
        for log in body.get("audit_log") or []:
            if log.get("action") == "task_dispatched":
                payload = log.get("payload") or {}
                dispatcher = payload.get("dispatcher_agent", "?")
                role = payload.get("dispatcher_role", "?")
                break
        print(
            f"Dispatched from: {task['dispatched_from_talk_id']}  "
            f"(dispatcher: {dispatcher} / {role})"
        )
```

Drop this block immediately after the existing `revisit_of` block but before `print(f"Task: ...")` so the header banner reads top-to-bottom: revisit-of, dispatched-from, then the task summary.

- [ ] **Step 4: Run test to verify pass**

Run: `uv run pytest tests/test_cli.py::test_cmd_details_renders_dispatched_from -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(talk-dispatch): render 'Dispatched from' line in opc details"
```

---

## Task 13: New skill — `protocol/skills/dispatch/SKILL.md`

**Files:**
- Create: `protocol/skills/dispatch/SKILL.md`

- [ ] **Step 1: Write the skill file**

Create `protocol/skills/dispatch/SKILL.md` with this exact content:

````markdown
---
name: dispatch
description: Dispatch a new task to the orchestrator from inside an open talk. Workers can only self-dispatch; team managers can dispatch to any agent in their team. Cross-team dispatch is forbidden.
---

# dispatch

Inside an OPEN talk, you can submit a new root task to the orchestrator without ending the talk. The founder is co-present in the talk; their authority is what the dispatch borrows. Use this when something actionable surfaces in conversation that you and the founder agree should become a task.

## When to use

- You and the founder have explicitly agreed in conversation that a task should be created.
- The new task fits within your role's authority (workers: yourself; managers: anyone on your team).
- You can describe the work in a single, concrete brief.

If any of those is missing, do not dispatch — keep talking, or recommend the founder run `opc run` themselves later.

## Authentication

Authority comes from the OPEN talk itself: pass the `talk_id` of the talk you are currently in. There is no task-path auth on dispatch (workers in a task already have their own session; this is a talk-only feature).

## Usage

1. **Write a JSON file** to `/tmp/dispatch-<talk_id>.json` using the Write tool.

   **Worker self-dispatch (most common):**
   ```json
   {
     "talk_id": "<talk_id>",
     "brief": "Implement Option B for TASK-087: change the trigger to a 2-hop join through guide_days."
   }
   ```

   **Manager dispatching to a team worker (explicit target):**
   ```json
   {
     "talk_id": "<talk_id>",
     "brief": "Audit the payment_agent's last three completed tasks for refund-policy drift.",
     "target_agent": "qa_engineer"
   }
   ```

   `target_agent` is optional and defaults to **yourself**. `team` is also optional and defaults to your own team — supplying a different team is rejected.

2. **Invoke as a single-line command:**

   ```bash
   opc dispatch --from-file /tmp/dispatch-<talk_id>.json
   ```

   The `--from-file` form is mandatory in agent sessions. Multi-line bash is rejected by the `Bash(opc:*)` permission rule because newlines count as command separators.

## Authorization rules

| Your role     | Can target                         | Default target |
|---------------|------------------------------------|----------------|
| Worker        | Yourself only                      | Yourself       |
| Team manager  | Any agent in your team (incl. you) | Yourself       |

Cross-team dispatch is forbidden in all cases. If you want a task to land on another team, surface it to the founder in conversation and let them decide.

## Record the call in your transcript

After dispatching, **record the call in the `transcript_markdown` you will send at `/talk end`**. One line per dispatch is enough, e.g.:

```
[during talk] dispatched TASK-042 to dev_agent: "Implement Option B for TASK-087".
```

The audit log captures the action (`opc audit TASK-042`), but the transcript is what the founder reads back. Skipping this silently mutates the queue from the founder's point of view.

## What happens

The orchestrator inserts a new root task with `assigned_agent` set to your `effective_target` and enqueues it for execution. Worker self-dispatch **bypasses the team manager's EH decision step** — the conversation is treated as the gating decision, so the worker runs directly. Manager dispatches to a team worker behave the same way: the manager has already decided, so the orchestrator runs the assignee.

The new task carries `dispatched_from_talk_id = <your talk_id>` for observability. `opc details TASK-NNN` shows a "Dispatched from" line.

## Error handling

- `404 not_found`: the `talk_id` doesn't exist. Re-check the id you typed.
- `400 talk_not_open`: the talk has been closed or abandoned. Open a new talk if needed.
- `422 empty_brief`: the brief was missing or whitespace-only. Re-state the work clearly.
- `403 dispatcher_team_unknown`: your agent record is not registered with any team. Ask the founder.
- `403 cross_team_dispatch_forbidden`: you tried to set `team` to a value other than your own.
- `403 worker_must_self_dispatch`: you are a worker and `target_agent` was not yourself.
- `403 target_not_in_team`: you are a manager and `target_agent` is not on your team.
- `404 unknown_agent`: the resolved target has no approved workspace.

If `opc` returns non-zero, retry once after 1 second. The 4xx codes above are not retryable — fix the payload.

## Naming

Use `/tmp/dispatch-<talk_id>.json` so multiple dispatches in the same talk don't collide on a fixed filename.
````

- [ ] **Step 2: Verify the skill is picked up by the workspace adapter**

Run:

```bash
grep -n "skills_dir\|copy.*skills\|SKILL.md" src/orchestrator/workspace_adapters.py | head -10
```

Confirm the adapter copies the entire `protocol/skills/` tree (it should — `start-task`, `make-worktree`, `manage-repo`, `manage-agent` are all picked up by the same mechanism). If it does, no adapter change is needed.

- [ ] **Step 3: Commit**

```bash
git add protocol/skills/dispatch/SKILL.md
git commit -m "docs(talk-dispatch): add dispatch skill"
```

---

## Task 14: Update `protocol/skills/talk/SKILL.md`

**Files:**
- Modify: `protocol/skills/talk/SKILL.md` (around line 124-126, the existing carve-out block)

- [ ] **Step 1: Edit the carve-out**

In `protocol/skills/talk/SKILL.md`, find the line near 125 that reads:

```markdown
- **Exception:** `opc manage-agent` (enroll / update / terminate) is allowed during a talk via the talk-path payload (pass `talk_id` instead of `task_id`+`session_id`). See the `manage-agent` skill. Record any such call in your `transcript_markdown` so the founder has a human-readable record at talk-end.
```

Add a sibling bullet directly below it:

```markdown
- **Exception:** `opc dispatch` (create a new task from inside the talk) is allowed via the talk-path payload — see the `dispatch` skill. Workers can only dispatch to themselves; team managers can dispatch to any agent in their team. Cross-team dispatch is forbidden. Record any such call in your `transcript_markdown` so the founder has a human-readable record at talk-end.
```

- [ ] **Step 2: Verify the talk skill still parses**

```bash
head -1 protocol/skills/talk/SKILL.md
```

Should still start with `---`. No tooling validates frontmatter beyond that, but a quick visual scan of the file is worth it.

- [ ] **Step 3: Commit**

```bash
git add protocol/skills/talk/SKILL.md
git commit -m "docs(talk-dispatch): add dispatch carve-out to talk skill"
```

---

## Task 15: Update `CLAUDE.md` and `README.md`

**Files:**
- Modify: `CLAUDE.md` (the agent-side callbacks list under "Running the Daemon + CLI")
- Modify: `README.md` (the equivalent CLI listing for end users)

- [ ] **Step 1: Add the dispatch line to CLAUDE.md**

In `CLAUDE.md`, find the `## Running the Daemon + CLI` section's code block, locate the `# Agent-side callbacks (invoked by skills):` group, and add after the `opc manage-agent --from-file ...` line:

```bash
opc dispatch --from-file /tmp/dispatch-<talk_id>.json   # agent: dispatch a new task from inside an open talk (workers self-only; team managers intra-team)
```

- [ ] **Step 2: Add to README.md**

In `README.md`, find the corresponding CLI usage block. Add the same line in the same relative position. (Run `grep -n "manage-agent.*from-file" README.md` to find the spot.)

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs(talk-dispatch): document opc dispatch in CLAUDE.md and README"
```

---

## Task 16: Integration test (e2e with fake-claude)

**Files:**
- Create: `tests/integration/test_talk_dispatch_e2e.py`

- [ ] **Step 1: Look at the existing talk-flow integration test**

Read `tests/integration/test_talk_flow_e2e.py` end-to-end so the new test mirrors its patterns (daemon spawn, runtime init, fake-claude harness, polling for task completion). Run:

```bash
sed -n '1,80p' tests/integration/test_talk_flow_e2e.py
```

- [ ] **Step 2: Write the e2e test**

Create `tests/integration/test_talk_dispatch_e2e.py`:

```python
"""End-to-end: agent in a talk dispatches a task; orchestrator runs it.

Mirrors the bootstrap + lifecycle pattern of test_talk_flow_e2e.py.
The new task is dispatched by simulating the agent calling
'opc dispatch --from-file ...' over the daemon's HTTP API; we don't
exercise the literal Claude subprocess for the dispatch call itself
(that's covered by the unit tests). What we DO exercise here is that the
daemon enqueues the task and the worker pool picks it up to run under
fake-claude.
"""
from __future__ import annotations

import json
import time

import pytest


pytestmark = pytest.mark.integration


def test_worker_self_dispatch_runs_to_completion(integration_daemon, runtime_path):
    """integration_daemon and runtime_path come from tests/integration/conftest.py."""
    # Identify the existing fixtures by reading conftest first; if the names
    # differ, adapt this test's signature accordingly.
    client = integration_daemon.client  # http client wrapper, see conftest

    # Open a talk for dev_agent.
    r = client.post("/api/v1/talks", json={"agent_name": "dev_agent"})
    assert r.status_code == 200, r.text
    talk_id = r.json()["talk_id"]

    # Dispatch a worker self-task.
    r = client.post(
        f"/api/v1/talks/{talk_id}/dispatch",
        json={"brief": "fake-claude work item"},
    )
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]

    # Poll the daemon until the task reaches a terminal status.
    deadline = time.time() + 30.0
    final_status = None
    while time.time() < deadline:
        r = client.get(f"/api/v1/tasks/{task_id}")
        if r.status_code == 200:
            t = r.json()["task"]
            if t["status"] in ("completed", "failed", "blocked"):
                final_status = t["status"]
                break
        time.sleep(0.5)
    assert final_status in ("completed", "failed", "blocked"), (
        f"Task {task_id} did not reach terminal status; last: {final_status!r}"
    )

    # Whatever the verdict, the task row must carry the talk_id.
    body = client.get(f"/api/v1/tasks/{task_id}").json()
    assert body["task"]["dispatched_from_talk_id"] == talk_id
    assert body["task"]["assigned_agent"] == "dev_agent"
    assert body["task"]["parent_task_id"] is None
```

If the integration conftest's fixture names differ (see `tests/integration/conftest.py`), rename the parameters to match — typical names in this codebase are `integration_daemon`, `tmp_runtime`, or similar. Read the conftest before adapting.

- [ ] **Step 3: Run the integration test**

Run: `uv run pytest tests/integration/test_talk_dispatch_e2e.py -v -m integration`
Expected: PASS — fake-claude completes the dispatched task.

- [ ] **Step 4: Run the full test suite (unit only)**

Run: `uv run pytest tests/ -v`
Expected: All previously-passing tests still pass; new tests pass.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_talk_dispatch_e2e.py
git commit -m "test(talk-dispatch): integration e2e for worker self-dispatch from talk"
```

---

## Self-Review Checklist (run after all tasks)

- [ ] **Spec coverage:** Every section of the spec is implemented somewhere in tasks 1–16.
  - Authority model (worker self / manager intra-team): tasks 3, 8, 9
  - Cross-team forbidden: task 7
  - HTTP API + 8 error codes: tasks 3, 4, 5, 6, 7, 8, 9, 10
  - Schema change + TaskRecord field: task 1
  - Audit entry: task 2
  - CLI: tasks 11, 12
  - Skill + carve-out: tasks 13, 14
  - Docs: task 15
  - Integration: task 16

- [ ] **No placeholder text:** No "TBD", "implement later", "similar to Task N", "add appropriate validation". Every code step shows the actual code.

- [ ] **Type consistency:** Confirm column name `dispatched_from_talk_id` is identical in: `src/models.py`, `src/infrastructure/database.py` (ALTER + INSERT + SELECT mappings), `src/daemon/routes/talks.py` (TaskRecord constructor + response field), `src/cli.py` (cmd_details lookup), and all tests.

- [ ] **Audit row payload key consistency:** `talk_id`, `dispatcher_agent`, `dispatcher_role`, `effective_target`, `team` are spelled identically in `audit_logger.py` and in the route's call site (Task 2 + Task 3).

- [ ] **Skill discovery:** `protocol/skills/dispatch/SKILL.md` is automatically picked up by `ClaudeWorkspaceAdapter` (it walks `protocol/skills/`). Verified in Task 13 step 2.

- [ ] **`team_for_agent` already exists:** confirmed at `src/orchestrator/teams.py:104` during exploration. No new helper task needed.

- [ ] **`_build_agent_prompt` already passes `task.brief` for non-managers:** confirmed at `src/orchestrator/run_step.py:261`. No orchestrator change needed for worker self-dispatch.
