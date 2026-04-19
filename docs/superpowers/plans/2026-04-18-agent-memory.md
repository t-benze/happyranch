# Agent Memory Implementation Plan

> **Historical note (2026-04-19):** Some SQL snippets below reference a `crew` column on the `tasks` table. That column has since been renamed to `team` via an `ALTER TABLE ... RENAME COLUMN` migration. The snippets are kept as-is to match the code that was written at the time; read `crew` as `team` in current code.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each agent a per-agent memory of the tasks it has performed so it can recall prior work when a new brief references past activity.

**Architecture:** Delegation spawns child tasks with `parent_task_id`. The orchestrator persists `final_output_summary` and `final_artifact_dir` on every finished task. Each agent's workspace gets a per-agent `task_history.md` index. A new `opc recall` CLI (backed by `GET /tasks/{id}/recall`) returns brief + output + artifact contents so agents can drill in by task_id.

**Tech Stack:** Python 3.11+, SQLite (WAL), Pydantic v2, FastAPI, pytest, Claude Code skills.

**Spec:** `docs/superpowers/specs/2026-04-18-agent-memory-design.md`.

**Conventions:**
- All commits use conventional-commit prefixes (`feat:`, `refactor:`, `test:`, `docs:`, `chore:`).
- Run tests with `uv run pytest <path> -v`.
- Type hints required on all signatures; `from __future__ import annotations` at the top of every new/touched `.py`.

---

## Task 1: Add `parent_task_id` to tasks table

**Files:**
- Modify: `src/models.py:44-54` (TaskRecord)
- Modify: `src/infrastructure/database.py:25-85` (schema), `:104-140` (insert/get), `:142-160` (list)
- Test: `tests/test_database.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write failing test for `parent_task_id` on TaskRecord**

Append to `tests/test_models.py` (create if missing — check first with `ls tests/test_models.py`):

```python
def test_task_record_accepts_parent_task_id():
    from src.models import TaskRecord, TaskType
    t = TaskRecord(id="TASK-002", type=TaskType.GENERAL, brief="child", parent_task_id="TASK-001")
    assert t.parent_task_id == "TASK-001"


def test_task_record_parent_defaults_to_none():
    from src.models import TaskRecord, TaskType
    t = TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="root")
    assert t.parent_task_id is None
```

- [ ] **Step 2: Write failing test for DB round-trip + get_children**

Append to `tests/test_database.py`:

```python
def test_insert_task_with_parent_round_trips(db):
    from src.models import TaskRecord, TaskType
    parent = TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="root")
    child = TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="child", parent_task_id="TASK-001"
    )
    db.insert_task(parent)
    db.insert_task(child)
    got = db.get_task("TASK-002")
    assert got.parent_task_id == "TASK-001"


def test_get_children_returns_direct_children_only(db):
    from src.models import TaskRecord, TaskType
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="root"))
    db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="c1", parent_task_id="TASK-001"
    ))
    db.insert_task(TaskRecord(
        id="TASK-003", type=TaskType.GENERAL, brief="c2", parent_task_id="TASK-001"
    ))
    db.insert_task(TaskRecord(
        id="TASK-004", type=TaskType.GENERAL, brief="grandchild", parent_task_id="TASK-002"
    ))
    assert db.get_children("TASK-001") == ["TASK-002", "TASK-003"]
    assert db.get_children("TASK-002") == ["TASK-004"]
    assert db.get_children("TASK-003") == []
```

- [ ] **Step 3: Run tests, verify they fail**

```
uv run pytest tests/test_models.py tests/test_database.py -v -k "parent or children"
```
Expected: FAIL — `TaskRecord` has no `parent_task_id`; `Database` has no `get_children`.

- [ ] **Step 4: Add `parent_task_id` field to `TaskRecord`**

Edit `src/models.py:44-54`, add after `brief: str`:

```python
    parent_task_id: str | None = None
```

- [ ] **Step 5: Extend the schema and CRUD in `database.py`**

In `_create_tables`, add to the `tasks` CREATE TABLE (after `completed_at TEXT`):

```sql
                parent_task_id TEXT
```

In the same method, after the existing `ALTER TABLE task_results ADD COLUMN status ...` block, add a second best-effort migration:

```python
        try:
            self._conn.execute("ALTER TABLE tasks ADD COLUMN parent_task_id TEXT")
        except sqlite3.OperationalError:
            pass
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id)"
        )
```

Update `insert_task` to include the new column:

```python
    def insert_task(self, task: TaskRecord) -> None:
        self._conn.execute(
            """INSERT INTO tasks (id, type, status, assigned_agent, team, brief,
               revision_count, created_at, updated_at, completed_at, parent_task_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.id,
                task.type.value,
                task.status.value,
                task.assigned_agent,
                task.team,
                task.brief,
                task.revision_count,
                task.created_at.isoformat(),
                task.updated_at.isoformat(),
                task.completed_at.isoformat() if task.completed_at else None,
                task.parent_task_id,
            ),
        )
        self._conn.commit()
```

Update `get_task` (and `list_tasks`) to read `parent_task_id` from the row and pass it to `TaskRecord(...)`. Example for `get_task` — replace the `return TaskRecord(...)` call:

```python
        return TaskRecord(
            id=row["id"],
            type=row["type"],
            status=row["status"],
            assigned_agent=row["assigned_agent"],
            team=row["team"],
            brief=row["brief"],
            revision_count=row["revision_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            parent_task_id=row["parent_task_id"],
        )
```

Apply the same extra kwarg in `list_tasks`.

Add a new method right after `list_tasks`:

```python
    def get_children(self, parent_task_id: str) -> list[str]:
        """Return direct children of a task, ordered by creation time."""
        cursor = self._conn.execute(
            "SELECT id FROM tasks WHERE parent_task_id = ? ORDER BY created_at",
            (parent_task_id,),
        )
        return [row["id"] for row in cursor.fetchall()]
```

- [ ] **Step 6: Re-run tests, verify they pass**

```
uv run pytest tests/test_models.py tests/test_database.py -v
```
Expected: PASS (all prior tests still green + new ones).

- [ ] **Step 7: Commit**

```bash
git add src/models.py src/infrastructure/database.py tests/test_models.py tests/test_database.py
git commit -m "feat: add parent_task_id to tasks for sub-task linking"
```

---

## Task 2: Add `final_output_summary` and `final_artifact_dir` to tasks

**Why:** `task_results.output_summary` can contain raw JSON (EH decisions) or a worker summary. The tasks table needs one canonical, human-readable string per task plus the artifact folder path for recall.

**Files:**
- Modify: `src/models.py:44-54` (TaskRecord)
- Modify: `src/infrastructure/database.py` (schema, insert, get, list, update_task)
- Test: `tests/test_database.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_database.py`:

```python
def test_update_task_sets_final_summary_and_artifact(db):
    from src.models import TaskRecord, TaskType
    db.insert_task(TaskRecord(id="TASK-010", type=TaskType.GENERAL, brief="b"))
    db.update_task(
        "TASK-010",
        final_output_summary="Produced Q1 report",
        final_artifact_dir="artifacts/TASK-010",
    )
    got = db.get_task("TASK-010")
    assert got.final_output_summary == "Produced Q1 report"
    assert got.final_artifact_dir == "artifacts/TASK-010"


def test_final_fields_default_to_none(db):
    from src.models import TaskRecord, TaskType
    db.insert_task(TaskRecord(id="TASK-011", type=TaskType.GENERAL, brief="b"))
    got = db.get_task("TASK-011")
    assert got.final_output_summary is None
    assert got.final_artifact_dir is None
```

- [ ] **Step 2: Run tests, verify failure**

```
uv run pytest tests/test_database.py -v -k "final"
```
Expected: FAIL — update_task rejects unknown fields, TaskRecord lacks them.

- [ ] **Step 3: Extend TaskRecord**

Edit `src/models.py`, add after `parent_task_id: str | None = None`:

```python
    final_output_summary: str | None = None
    final_artifact_dir: str | None = None
```

- [ ] **Step 4: Extend the schema and CRUD**

In `database.py` `_create_tables`, add two more best-effort `ALTER TABLE` statements next to the parent_task_id one:

```python
        for ddl in (
            "ALTER TABLE tasks ADD COLUMN final_output_summary TEXT",
            "ALTER TABLE tasks ADD COLUMN final_artifact_dir TEXT",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
```

In `update_task`, extend the allowed set:

```python
        allowed = {
            "status", "assigned_agent", "revision_count", "completed_at",
            "final_output_summary", "final_artifact_dir",
        }
```

In `get_task` and `list_tasks`, add the two new fields when constructing `TaskRecord(...)`:

```python
            final_output_summary=row["final_output_summary"],
            final_artifact_dir=row["final_artifact_dir"],
```

Include both in the `tasks` CREATE TABLE statement so fresh DBs also have them:

```sql
                final_output_summary TEXT,
                final_artifact_dir TEXT
```

- [ ] **Step 5: Re-run tests**

```
uv run pytest tests/test_database.py tests/test_models.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/models.py src/infrastructure/database.py tests/test_database.py
git commit -m "feat: add final_output_summary/final_artifact_dir to tasks"
```

---

## Task 3: Add `artifact_dir` to task_results and CompletionReport

**Files:**
- Modify: `src/models.py:57-65` (CompletionReport)
- Modify: `src/infrastructure/database.py` (schema + insert_task_result + get_latest_task_result)
- Test: `tests/test_database.py`, `tests/test_models.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_models.py`:

```python
def test_completion_report_accepts_artifact_dir():
    from src.models import CompletionReport
    r = CompletionReport(
        task_id="TASK-001", agent="dev_agent", status="completed",
        confidence=80, output_summary="done", artifact_dir="artifacts/TASK-001",
    )
    assert r.artifact_dir == "artifacts/TASK-001"


def test_completion_report_artifact_defaults_to_none():
    from src.models import CompletionReport
    r = CompletionReport(
        task_id="T", agent="a", status="completed", confidence=0, output_summary="",
    )
    assert r.artifact_dir is None
```

Append to `tests/test_database.py`:

```python
def test_insert_task_result_stores_artifact_dir(db):
    db.insert_task_result(
        task_id="TASK-001", agent="dev_agent", session_id="s1",
        output_summary="done", confidence_score=80,
        artifact_dir="artifacts/TASK-001",
    )
    rows = db.get_task_results("TASK-001")
    assert rows[0]["artifact_dir"] == "artifacts/TASK-001"


def test_insert_task_result_artifact_optional(db):
    db.insert_task_result(
        task_id="TASK-002", agent="dev_agent", session_id="s2",
        output_summary="done", confidence_score=80,
    )
    rows = db.get_task_results("TASK-002")
    assert rows[0]["artifact_dir"] is None
```

- [ ] **Step 2: Run tests, verify failure**

```
uv run pytest tests/test_models.py tests/test_database.py -v -k "artifact"
```
Expected: FAIL.

- [ ] **Step 3: Add `artifact_dir` to CompletionReport**

Edit `src/models.py`, inside `CompletionReport`, add after `suggested_reviewer_focus`:

```python
    artifact_dir: str | None = None
```

- [ ] **Step 4: Extend schema + CRUD**

In `database.py` `_create_tables`, add to `task_results` CREATE TABLE (before `created_at`):

```sql
                artifact_dir TEXT,
```

And add a best-effort migration alongside the existing `status` migration:

```python
        try:
            self._conn.execute("ALTER TABLE task_results ADD COLUMN artifact_dir TEXT")
        except sqlite3.OperationalError:
            pass
```

Update `insert_task_result` signature + SQL. Change the definition to:

```python
    def insert_task_result(
        self,
        task_id: str,
        agent: str,
        session_id: str,
        output_summary: str,
        confidence_score: int,
        status: str = "completed",
        risks_flagged: list[str] | None = None,
        learnings: str | None = None,
        duration_seconds: int | None = None,
        token_count: int | None = None,
        estimated_cost: float | None = None,
        artifact_dir: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO task_results
               (task_id, agent, session_id, status, output_summary, confidence_score,
                learnings, risks_flagged, duration_seconds, token_count, estimated_cost,
                artifact_dir, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                agent,
                session_id,
                status,
                output_summary,
                confidence_score,
                learnings,
                json.dumps(risks_flagged) if risks_flagged is not None else None,
                duration_seconds,
                token_count,
                estimated_cost,
                artifact_dir,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
```

- [ ] **Step 5: Re-run tests**

```
uv run pytest tests/test_models.py tests/test_database.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/models.py src/infrastructure/database.py tests/test_models.py tests/test_database.py
git commit -m "feat: persist artifact_dir on task_results and completion reports"
```

---

## Task 4: Thread `artifact_dir` through the completion callback

**Files:**
- Modify: `src/daemon/routes/tasks.py:67-75` (CompletionBody), `:113-122` (insert_task_result call)
- Modify: `src/cli.py:305-333` (`_completion_payload_from_file`), `:336-378` (cmd_report_completion), `:652-671` (argparse for report-completion)
- Modify: `src/infrastructure/audit_logger.py:36-62` (log_completion_report)
- Test: `tests/daemon/test_routes_tasks.py`, `tests/test_cli.py`, `tests/test_audit_logger.py`

- [ ] **Step 1: Write failing tests**

Inspect existing `tests/daemon/test_routes_tasks.py` to learn the client fixture pattern, then add a new test following it:

```python
def test_submit_completion_persists_artifact_dir(client, db):
    # Arrange: seed a task + active session using the same setup other tests use
    # (see existing test_submit_completion_* for the pattern).
    ...  # set up task TASK-001, claim session "sess-a" for agent "dev_agent"

    resp = client.post(
        "/api/v1/tasks/TASK-001/completion",
        json={
            "session_id": "sess-a",
            "agent": "dev_agent",
            "status": "completed",
            "confidence": 80,
            "output_summary": "Wrote Q1 report",
            "artifact_dir": "artifacts/TASK-001",
        },
    )
    assert resp.status_code == 200
    rows = db.get_task_results("TASK-001")
    assert rows[-1]["artifact_dir"] == "artifacts/TASK-001"
```

If the test file doesn't already have that fixture shape, read its existing passing tests and mirror exactly what they do to set up the task + active session; don't invent new patterns.

Append to `tests/test_cli.py`:

```python
def test_completion_payload_from_file_accepts_artifact_dir(tmp_path):
    import json as _json
    from src.cli import _completion_payload_from_file

    path = tmp_path / "c.json"
    path.write_text(_json.dumps({
        "task_id": "TASK-001",
        "session_id": "s",
        "agent": "dev_agent",
        "status": "completed",
        "summary": "done",
        "artifact_dir": "artifacts/TASK-001",
    }))
    task_id, body = _completion_payload_from_file(str(path))
    assert task_id == "TASK-001"
    assert body["artifact_dir"] == "artifacts/TASK-001"


def test_completion_payload_from_file_artifact_optional(tmp_path):
    import json as _json
    from src.cli import _completion_payload_from_file

    path = tmp_path / "c.json"
    path.write_text(_json.dumps({
        "task_id": "T", "session_id": "s", "agent": "a",
        "status": "completed", "summary": "done",
    }))
    _, body = _completion_payload_from_file(str(path))
    assert body.get("artifact_dir") is None
```

- [ ] **Step 2: Run, verify failure**

```
uv run pytest tests/test_cli.py tests/daemon/test_routes_tasks.py -v -k "artifact"
```
Expected: FAIL.

- [ ] **Step 3: Extend daemon CompletionBody**

Edit `src/daemon/routes/tasks.py:67-75` — add to `CompletionBody`:

```python
    artifact_dir: str | None = None
```

In `submit_completion`, pass it through:

```python
        state.db.insert_task_result(
            task_id=task_id,
            agent=body.agent,
            session_id=body.session_id,
            status=body.status,
            output_summary=body.output_summary,
            confidence_score=body.confidence,
            risks_flagged=body.risks_flagged,
            artifact_dir=body.artifact_dir,
        )
```

- [ ] **Step 4: Extend CLI payload parser and argparse**

In `src/cli.py` `_completion_payload_from_file`, after building `body`, add:

```python
    if data.get("artifact_dir"):
        body["artifact_dir"] = data["artifact_dir"]
```

In `cmd_report_completion`'s else-branch (flag-based build), extend `body` similarly:

```python
        if args.artifact_dir:
            body["artifact_dir"] = args.artifact_dir
```

In `build_parser`, add the flag to `p_rep`:

```python
    p_rep.add_argument("--artifact-dir", dest="artifact_dir", default=None,
                       help="Relative path to the artifact directory under the agent workspace")
```

- [ ] **Step 5: Update audit_logger to persist artifact_dir through CompletionReport**

Edit `src/infrastructure/audit_logger.py` `log_completion_report`. The method already calls `insert_task_result`; add `artifact_dir=report.artifact_dir` to that call:

```python
        self._db.insert_task_result(
            task_id=report.task_id,
            agent=report.agent,
            session_id=session_id,
            status=report.status,
            output_summary=report.output_summary,
            confidence_score=report.confidence,
            risks_flagged=report.risks_flagged,
            duration_seconds=duration_seconds,
            token_count=token_count,
            estimated_cost=estimated_cost,
            artifact_dir=report.artifact_dir,
        )
```

- [ ] **Step 6: Re-run tests**

```
uv run pytest tests/test_cli.py tests/daemon/test_routes_tasks.py tests/test_audit_logger.py -v
```
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/daemon/routes/tasks.py src/cli.py src/infrastructure/audit_logger.py \
  tests/test_cli.py tests/daemon/test_routes_tasks.py
git commit -m "feat: thread artifact_dir through the completion callback"
```

---

## Task 5: Orchestrator — spawn sub-task on delegate

**Why:** Today, EH's delegation runs the worker agent under the **root** task_id. After this task, delegation creates a child task with its own id, so each agent-level unit of work is addressable.

**Files:**
- Modify: `src/orchestrator/orchestrator.py:87-213` (`run_task`)
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_orchestrator.py`:

```python
@patch.object(Orchestrator, "_run_agent")
def test_delegate_spawns_child_task(mock_run, orchestrator, test_runtime):
    _setup_workspaces(test_runtime)
    calls = 0

    def side_effect(task_id, agent, prompt):
        nonlocal calls
        calls += 1
        if agent == "engineering_head":
            if calls == 1:
                return _make_eh_decision(task_id, {
                    "action": "delegate",
                    "agent": "dev_agent",
                    "prompt": "Implement Alipay integration",
                })
            return _make_eh_decision(task_id, {
                "action": "done", "summary": "Dev agent delivered."
            })
        # Worker completions should carry the CHILD task id, not the root.
        assert task_id == "TASK-002", f"expected child id, got {task_id}"
        return _make_agent_result(task_id, agent)

    mock_run.side_effect = side_effect

    root_id = orchestrator.create_task(TaskType.GENERAL, "Add Alipay")
    assert root_id == "TASK-001"
    orchestrator.run_task(root_id)

    child = orchestrator._db.get_task("TASK-002")
    assert child is not None
    assert child.parent_task_id == "TASK-001"
    assert child.assigned_agent == "dev_agent"
    assert child.brief == "Implement Alipay integration"
    assert orchestrator._db.get_children("TASK-001") == ["TASK-002"]
```

- [ ] **Step 2: Run, verify failure**

```
uv run pytest tests/test_orchestrator.py::test_delegate_spawns_child_task -v
```
Expected: FAIL — currently only one task row is created; dev_agent runs under the root id.

- [ ] **Step 3: Create a helper `_spawn_delegate_task` and use it**

In `src/orchestrator/orchestrator.py`, add a method near `create_task`:

```python
    def _spawn_delegate_task(
        self, parent_task_id: str, agent: str, prompt: str, task_type: TaskType,
    ) -> str:
        """Persist a child task for a delegated work unit.

        Inherits ``task_type`` from the parent so downstream consumers see a
        consistent type across the tree.
        """
        child_id = self._db.next_task_id()
        child = TaskRecord(
            id=child_id,
            type=task_type,
            brief=prompt,
            assigned_agent=agent,
            parent_task_id=parent_task_id,
        )
        self._db.insert_task(child)
        return child_id
```

Then in `run_task`, inside the `if next_step.action == "delegate":` block, replace the `_run_agent` call and its `StepRecord` append with a version that routes through the child id:

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

                child_task_id = self._spawn_delegate_task(
                    parent_task_id=task_id,
                    agent=next_step.agent,
                    prompt=next_step.prompt or "",
                    task_type=task.type,
                )

                delegate_result, delegate_report = self._run_agent(
                    child_task_id, next_step.agent, next_step.prompt or "",
                )
                if delegate_result.success and delegate_report is not None:
                    self._log_step_result(child_task_id, delegate_result, delegate_report)

                delegate_blocked = (
                    delegate_report is not None and delegate_report.status == "blocked"
                )
                if delegate_report is None:
                    result_summary = "Agent session failed"
                elif delegate_blocked:
                    result_summary = f"blocked: {delegate_report.output_summary}"
                else:
                    result_summary = delegate_report.output_summary
                prior_steps.append(StepRecord(
                    step_number=step_num,
                    agent=next_step.agent,
                    action=f"delegate: {(next_step.prompt or '')[:100]}",
                    result_summary=result_summary,
                    success=(
                        delegate_result.success
                        and delegate_report is not None
                        and not delegate_blocked
                    ),
                ))
```

Key changes from the original: `child_task_id = self._spawn_delegate_task(...)` and the subsequent `_run_agent(child_task_id, ...)` + `_log_step_result(child_task_id, ...)`.

- [ ] **Step 4: Re-run full orchestrator suite**

```
uv run pytest tests/test_orchestrator.py -v
```
Expected: all tests pass, including pre-existing ones (they don't assert child-id specifically; EH sessions still run under root id).

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: spawn child tasks on EH delegate for per-agent work units"
```

---

## Task 6: Orchestrator — finalize tasks (populate `final_output_summary` / `final_artifact_dir`)

**Why:** `task_history.md` and `opc recall` read from `tasks.final_output_summary` / `.final_artifact_dir`. Worker tasks copy from their last completion report; root tasks whose EH returned `done` parse the `summary` field out of the EH's JSON.

**Files:**
- Modify: `src/orchestrator/orchestrator.py` (add `_finalize_task`; call from the four terminal branches in `run_task`)
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_orchestrator.py`:

```python
@patch.object(Orchestrator, "_run_agent")
def test_finalize_root_task_parses_eh_summary(mock_run, orchestrator, test_runtime):
    _setup_workspaces(test_runtime)
    mock_run.return_value = _make_eh_decision("TASK-001", {
        "action": "done",
        "summary": "Reviewed Q1 metrics. Three risks, five actions.",
    })
    task_id = orchestrator.create_task(TaskType.GENERAL, "Review Q1")
    orchestrator.run_task(task_id)
    task = orchestrator._db.get_task(task_id)
    assert task.final_output_summary == "Reviewed Q1 metrics. Three risks, five actions."


@patch.object(Orchestrator, "_run_agent")
def test_finalize_child_task_from_worker_report(mock_run, orchestrator, test_runtime):
    _setup_workspaces(test_runtime)
    calls = 0
    def side_effect(task_id, agent, prompt):
        nonlocal calls
        calls += 1
        if agent == "engineering_head":
            if calls == 1:
                return _make_eh_decision(task_id, {
                    "action": "delegate",
                    "agent": "dev_agent",
                    "prompt": "Implement Alipay",
                })
            return _make_eh_decision(task_id, {"action": "done", "summary": "good"})
        # Worker completion carries artifact_dir:
        from src.orchestrator.executor import ExecutorResult
        from src.models import CompletionReport
        return (
            ExecutorResult(success=True, duration_seconds=10, session_id="sess-w"),
            CompletionReport(
                task_id=task_id, agent=agent, status="completed", confidence=85,
                output_summary="Alipay integration shipped",
                artifact_dir="artifacts/TASK-002",
            ),
        )
    mock_run.side_effect = side_effect

    orchestrator.create_task(TaskType.GENERAL, "Add Alipay")
    orchestrator.run_task("TASK-001")
    child = orchestrator._db.get_task("TASK-002")
    assert child.final_output_summary == "Alipay integration shipped"
    assert child.final_artifact_dir == "artifacts/TASK-002"
```

- [ ] **Step 2: Run, verify failure**

```
uv run pytest tests/test_orchestrator.py -v -k "finalize"
```
Expected: FAIL — `final_*` fields stay None.

- [ ] **Step 3: Add `_finalize_task` and call it at terminal points**

Add to `Orchestrator`:

```python
    def _finalize_task(
        self,
        task_id: str,
        report: CompletionReport | None,
        override_summary: str | None = None,
    ) -> None:
        """Populate tasks.final_output_summary / final_artifact_dir.

        ``override_summary`` wins if set (used for escalation reasons and for
        the parsed EH 'summary' of root tasks). Otherwise we read from the
        report. Silent no-op if there's nothing to persist.
        """
        summary = override_summary
        artifact: str | None = None
        if report is not None:
            if summary is None:
                summary = report.output_summary
            artifact = report.artifact_dir
        fields: dict[str, object] = {}
        if summary is not None:
            fields["final_output_summary"] = summary
        if artifact is not None:
            fields["final_artifact_dir"] = artifact
        if fields:
            self._db.update_task(task_id, **fields)
```

Update the four terminal branches in `run_task`:

1. **EH session fails (reject):** after `self._db.update_task(task_id, status=TaskStatus.REJECTED)`:
   ```python
   self._finalize_task(task_id, report=None, override_summary="EH session failed")
   ```

2. **EH returns `done` (approve):** after `self._db.update_task(task_id, status=TaskStatus.APPROVED)` and before `_log_review_verdicts`:
   ```python
   self._finalize_task(task_id, report=eh_report, override_summary=next_step.summary)
   ```
   `next_step.summary` is the parsed JSON `summary` field.

3. **EH returns `escalate`:** after the `update_task(status=ESCALATED)` call:
   ```python
   self._finalize_task(task_id, report=None, override_summary=next_step.reason or "Escalated by Engineering Head")
   ```

4. **Max steps exceeded:** after the final `update_task(status=ESCALATED)`:
   ```python
   self._finalize_task(task_id, report=None, override_summary=f"Max orchestration steps ({max_steps}) exceeded")
   ```

5. **Sub-task completion:** inside the delegate branch, *after* `prior_steps.append(...)` and regardless of success/blocked/failed, call:
   ```python
   self._finalize_task(
       child_task_id,
       report=delegate_report,
       override_summary=(
           "Agent session failed" if delegate_report is None
           else f"blocked: {delegate_report.output_summary}" if delegate_blocked
           else None
       ),
   )
   ```

- [ ] **Step 4: Re-run orchestrator tests**

```
uv run pytest tests/test_orchestrator.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: finalize tasks with canonical summary and artifact dir"
```

---

## Task 7: Per-agent `task_history.md` — replace the global writer

**Why:** Today `_update_recent_tasks` appends the same line to every workspace. Replace with a per-agent writer that only touches the assigned_agent's workspace, prepends newest-first, and emits the richer format from the spec.

**Files:**
- Rename behavior: `src/orchestrator/orchestrator.py` (`_update_recent_tasks` → `_update_task_history`)
- Modify: `src/orchestrator/context_builder.py` (file-name change is Task 8; don't touch here)
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_orchestrator.py`:

```python
import re


@patch.object(Orchestrator, "_run_agent")
def test_task_history_written_per_agent_only(mock_run, orchestrator, test_runtime):
    _setup_workspaces(test_runtime)
    calls = 0
    def side_effect(task_id, agent, prompt):
        nonlocal calls
        calls += 1
        if agent == "engineering_head":
            if calls == 1:
                return _make_eh_decision(task_id, {
                    "action": "delegate", "agent": "dev_agent",
                    "prompt": "Implement Alipay",
                })
            return _make_eh_decision(task_id, {"action": "done", "summary": "shipped"})
        return _make_agent_result(task_id, agent, summary="dev did it")
    mock_run.side_effect = side_effect

    orchestrator.create_task(TaskType.GENERAL, "Add Alipay support")
    orchestrator.run_task("TASK-001")

    eh_hist = (test_runtime.workspaces_dir / "engineering_head" / "task_history.md").read_text()
    dev_hist = (test_runtime.workspaces_dir / "dev_agent" / "task_history.md").read_text()
    pm_hist = (test_runtime.workspaces_dir / "product_manager" / "task_history.md").read_text()

    # Root lives in EH's history; child lives in dev's; PM untouched.
    assert "TASK-001" in eh_hist
    assert "TASK-002" in dev_hist
    assert "TASK-001" not in dev_hist
    assert "TASK-002" not in pm_hist


@patch.object(Orchestrator, "_run_agent")
def test_task_history_entry_format(mock_run, orchestrator, test_runtime):
    _setup_workspaces(test_runtime)
    mock_run.return_value = _make_eh_decision("TASK-001", {
        "action": "done", "summary": "Reviewed Q1. Three risks, five actions.",
    })
    orchestrator.create_task(TaskType.GENERAL, "Review Q1 project status")
    orchestrator.run_task("TASK-001")

    hist = (test_runtime.workspaces_dir / "engineering_head" / "task_history.md").read_text()
    # Header line: **TASK-001** (YYYY-MM-DD, approved) — Review Q1 project status
    assert re.search(r"\*\*TASK-001\*\* \(\d{4}-\d{2}-\d{2}, approved\) — Review Q1", hist)
    assert "Outcome: Reviewed Q1. Three risks, five actions." in hist
    # Artifact line is absent when no artifact_dir was produced
    assert "Artifact:" not in hist


@patch.object(Orchestrator, "_run_agent")
def test_task_history_newest_first(mock_run, orchestrator, test_runtime):
    _setup_workspaces(test_runtime)
    mock_run.return_value = _make_eh_decision("TASK-001", {"action": "done", "summary": "first"})
    orchestrator.create_task(TaskType.GENERAL, "First task")
    orchestrator.run_task("TASK-001")
    mock_run.return_value = _make_eh_decision("TASK-002", {"action": "done", "summary": "second"})
    orchestrator.create_task(TaskType.GENERAL, "Second task")
    orchestrator.run_task("TASK-002")

    hist = (test_runtime.workspaces_dir / "engineering_head" / "task_history.md").read_text()
    idx2 = hist.index("TASK-002")
    idx1 = hist.index("TASK-001")
    assert idx2 < idx1  # newest first
```

- [ ] **Step 2: Pre-seed the workspace helper with the new filename**

Update `tests/test_orchestrator.py` `_setup_workspaces` to write `task_history.md` instead of `recent_tasks.md`:

```python
        (ws / "task_history.md").write_text(f"# Task History: {agent}\n\n")
```

- [ ] **Step 3: Run tests, verify failure**

```
uv run pytest tests/test_orchestrator.py -v -k "task_history"
```
Expected: FAIL.

- [ ] **Step 4: Replace `_update_recent_tasks` with `_update_task_history`**

In `src/orchestrator/orchestrator.py`, delete `_update_recent_tasks` and add:

```python
    _HISTORY_CAP = 50
    _HEADER = "# Task History"

    def _update_task_history(self, task_id: str) -> None:
        """Prepend one entry to the assigned_agent's task_history.md.

        No-op if the task has no assigned_agent or its workspace is missing.
        Older entries roll off past _HISTORY_CAP.
        """
        task = self._db.get_task(task_id)
        if task is None or not task.assigned_agent:
            return
        ws = self._runtime.workspaces_dir / task.assigned_agent
        if not ws.exists():
            return

        date = (task.completed_at or task.updated_at or task.created_at)[:10] \
            if isinstance(task.completed_at or task.updated_at or task.created_at, str) \
            else (task.completed_at or task.updated_at or task.created_at).date().isoformat()
        status = task.status.value
        brief = (task.brief or "").replace("\n", " ").strip()[:120]
        outcome = (task.final_output_summary or "").replace("\n", " ").strip()[:160]
        entry_lines = [
            f"- **{task.id}** ({date}, {status}) — {brief}",
            f"  - Outcome: {outcome}" if outcome else "  - Outcome: (none)",
        ]
        if task.final_artifact_dir:
            entry_lines.append(f"  - Artifact: `{task.final_artifact_dir}`")
        entry = "\n".join(entry_lines) + "\n"

        path = ws / "task_history.md"
        old = path.read_text() if path.exists() else f"{self._HEADER}: {task.assigned_agent}\n\n"
        header, _, body = old.partition("\n\n")
        # Split body into existing entries (each starts with "- **")
        entries = [blk for blk in body.split("\n- **") if blk.strip()]
        # Re-attach the leading "- **" we lost on the first entry
        normalized = []
        for i, blk in enumerate(entries):
            normalized.append(("- **" if i > 0 else "- **") + blk.rstrip())
        # Filter out any pre-existing entry for this task id (re-runs)
        normalized = [e for e in normalized if f"**{task.id}**" not in e]
        normalized.insert(0, entry.rstrip())
        normalized = normalized[: self._HISTORY_CAP]
        new_body = "\n".join(normalized) + "\n"
        path.write_text(f"{header}\n\n{new_body}")
```

Note: the parser above is minimal — it assumes entries start with `- **` and are separated by newlines. For a plan-approved design, this is acceptable; if the file format drifts we rewrite here.

Simpler alternative — swap the whole method for this (preferred, because round-tripping markdown is fiddly):

```python
    def _update_task_history(self, task_id: str) -> None:
        task = self._db.get_task(task_id)
        if task is None or not task.assigned_agent:
            return
        ws = self._runtime.workspaces_dir / task.assigned_agent
        if not ws.exists():
            return
        path = ws / "task_history.md"

        # Rebuild from scratch: query the DB for this agent's recent tasks.
        recent = self._db.list_agent_tasks(task.assigned_agent, limit=self._HISTORY_CAP)
        header = f"{self._HEADER}: {task.assigned_agent}\n\n"
        lines: list[str] = []
        for t in recent:
            date = (t.completed_at or t.updated_at or t.created_at)
            date_str = date.date().isoformat() if hasattr(date, "date") else str(date)[:10]
            brief = (t.brief or "").replace("\n", " ").strip()[:120]
            outcome = (t.final_output_summary or "").replace("\n", " ").strip()[:160]
            lines.append(f"- **{t.id}** ({date_str}, {t.status.value}) — {brief}")
            lines.append(f"  - Outcome: {outcome}" if outcome else "  - Outcome: (none)")
            if t.final_artifact_dir:
                lines.append(f"  - Artifact: `{t.final_artifact_dir}`")
        path.write_text(header + "\n".join(lines) + ("\n" if lines else ""))
```

Use the rebuild-from-DB version. Then add `list_agent_tasks` to `database.py`:

```python
    def list_agent_tasks(self, agent: str, limit: int = 50) -> list[TaskRecord]:
        cursor = self._conn.execute(
            """SELECT * FROM tasks WHERE assigned_agent = ?
               ORDER BY datetime(COALESCE(completed_at, updated_at, created_at)) DESC
               LIMIT ?""",
            (agent, limit),
        )
        return [
            TaskRecord(
                id=row["id"],
                type=row["type"],
                status=row["status"],
                assigned_agent=row["assigned_agent"],
                team=row["team"],
                brief=row["brief"],
                revision_count=row["revision_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                parent_task_id=row["parent_task_id"],
                final_output_summary=row["final_output_summary"],
                final_artifact_dir=row["final_artifact_dir"],
            )
            for row in cursor.fetchall()
        ]
```

Replace all four `self._update_recent_tasks(task_id)` call sites in `run_task` with `self._update_task_history(task_id)`. Also call it for every completed sub-task, right after `_finalize_task(child_task_id, ...)` in the delegate branch.

- [ ] **Step 5: Re-run full suite**

```
uv run pytest tests/test_orchestrator.py tests/test_database.py -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/orchestrator.py src/infrastructure/database.py tests/test_orchestrator.py
git commit -m "feat: per-agent task_history.md replaces global recent_tasks.md"
```

---

## Task 8: Rename persistent file in context_builder and migrate old files

**Files:**
- Modify: `src/orchestrator/context_builder.py:121-128` (persistent files list), `:78-93` (CLAUDE.md template)
- Test: `tests/test_context_builder.py`

- [ ] **Step 1: Write failing tests**

Update `tests/test_context_builder.py` to expect the new filename and test migration. Append / replace:

```python
def test_workspace_ready_creates_task_history(tmp_path, settings):
    from src.orchestrator.context_builder import ContextBuilder
    ws = tmp_path / "dev_agent"
    ContextBuilder(settings).ensure_workspace_ready(ws, "dev_agent", "prompt")
    assert (ws / "task_history.md").exists()
    assert not (ws / "recent_tasks.md").exists()


def test_workspace_migrates_recent_tasks_to_task_history(tmp_path, settings):
    from src.orchestrator.context_builder import ContextBuilder
    ws = tmp_path / "dev_agent"
    ws.mkdir(parents=True)
    (ws / "recent_tasks.md").write_text("# Recent Tasks: dev_agent\n\n- entry\n")
    ContextBuilder(settings).ensure_workspace_ready(ws, "dev_agent", "prompt")
    assert (ws / "task_history.md").read_text().startswith("# Recent Tasks")
    assert not (ws / "recent_tasks.md").exists()
```

Update the existing `test_claude_md_mentions_recent_tasks` (or similar) to check for `task_history.md` instead. Read the file first, then adjust the specific assertion on line 63 and any other `recent_tasks.md` reference.

- [ ] **Step 2: Run, verify failure**

```
uv run pytest tests/test_context_builder.py -v
```
Expected: FAIL — existing tests still reference `recent_tasks.md`.

- [ ] **Step 3: Update context_builder**

Edit `src/orchestrator/context_builder.py`:

In `write_claude_md`, change the Persistent Files list line:

```python
            "- `task_history.md` -- read-only, updated by orchestrator",
```

In `ensure_workspace_ready`, replace the `recent_tasks.md` entry in the persistent-files loop with `task_history.md`, and add a migration block right before the loop:

```python
        old = workspace / "recent_tasks.md"
        new = workspace / "task_history.md"
        if old.exists() and not new.exists():
            old.rename(new)

        for filename, default_content in [
            ("learnings.md", f"# Learnings: {agent_name}\n\n"),
            ("scorecard.md", "# Scorecard\n\nNo performance data yet. Tier: green (default)\n"),
            ("task_history.md", f"# Task History: {agent_name}\n\n"),
        ]:
```

- [ ] **Step 4: Run tests**

```
uv run pytest tests/test_context_builder.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/context_builder.py tests/test_context_builder.py
git commit -m "refactor: rename recent_tasks.md to task_history.md with migration"
```

---

## Task 9: `Database.get_recall_payload`

**Files:**
- Modify: `src/infrastructure/database.py`
- Test: `tests/test_database.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_database.py`:

```python
def test_get_recall_payload_returns_task_with_children(db):
    from src.models import TaskRecord, TaskType
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="root"))
    db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="child", parent_task_id="TASK-001"
    ))
    db.update_task(
        "TASK-001", final_output_summary="All done", final_artifact_dir="artifacts/TASK-001",
    )
    payload = db.get_recall_payload("TASK-001")
    assert payload is not None
    assert payload["task_id"] == "TASK-001"
    assert payload["parent_task_id"] is None
    assert payload["brief"] == "root"
    assert payload["output_summary"] == "All done"
    assert payload["artifact_dir"] == "artifacts/TASK-001"
    assert payload["children"] == ["TASK-002"]


def test_get_recall_payload_missing_task_returns_none(db):
    assert db.get_recall_payload("TASK-404") is None
```

- [ ] **Step 2: Verify failure**

```
uv run pytest tests/test_database.py -v -k "recall_payload"
```
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `database.py` near `get_task`:

```python
    def get_recall_payload(self, task_id: str) -> dict | None:
        """Return a flat dict suitable for the /recall endpoint, or None."""
        task = self.get_task(task_id)
        if task is None:
            return None
        return {
            "task_id": task.id,
            "parent_task_id": task.parent_task_id,
            "assigned_agent": task.assigned_agent,
            "brief": task.brief,
            "status": task.status.value,
            "created_at": task.created_at.isoformat() if hasattr(task.created_at, "isoformat") else task.created_at,
            "completed_at": (
                task.completed_at.isoformat() if hasattr(task.completed_at, "isoformat")
                else task.completed_at
            ),
            "output_summary": task.final_output_summary,
            "artifact_dir": task.final_artifact_dir,
            "children": self.get_children(task.id),
        }
```

- [ ] **Step 4: Verify pass**

```
uv run pytest tests/test_database.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "feat: add Database.get_recall_payload for /recall endpoint"
```

---

## Task 10: `GET /tasks/{id}/recall` daemon route

**Files:**
- Modify: `src/daemon/routes/tasks.py`
- Test: `tests/daemon/test_routes_tasks.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/daemon/test_routes_tasks.py` (follow the existing client + db fixture pattern):

```python
def test_recall_returns_task_payload(client, db, active_runtime):
    # Seed a task with final summary + artifact dir.
    from src.models import TaskRecord, TaskType
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="Review Q1"))
    db.update_task(
        "TASK-001",
        status="approved",
        final_output_summary="Report delivered",
        final_artifact_dir="artifacts/TASK-001",
    )
    r = client.get("/api/v1/tasks/TASK-001/recall")
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "TASK-001"
    assert body["output_summary"] == "Report delivered"
    assert body["artifact_dir"] == "artifacts/TASK-001"
    assert body["children"] == []


def test_recall_missing_task_returns_404(client, active_runtime):
    r = client.get("/api/v1/tasks/TASK-404/recall")
    assert r.status_code == 404


def test_recall_tree_includes_descendants(client, db, active_runtime):
    from src.models import TaskRecord, TaskType
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="root"))
    db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="child", parent_task_id="TASK-001",
    ))
    r = client.get("/api/v1/tasks/TASK-001/recall", params={"tree": "true"})
    assert r.status_code == 200
    body = r.json()
    assert body["task_id"] == "TASK-001"
    # children list now contains full payloads, not just ids
    assert isinstance(body["children"], list)
    assert body["children"][0]["task_id"] == "TASK-002"


def test_recall_include_artifact_reads_files(tmp_path, client, db, active_runtime, test_runtime):
    from src.models import TaskRecord, TaskType
    # Create an artifact on disk in the assigned agent's workspace.
    ws = test_runtime.workspaces_dir / "dev_agent"
    artifact = ws / "artifacts" / "TASK-001"
    artifact.mkdir(parents=True)
    (artifact / "report.md").write_text("# Q1 report\n\nAll good.")
    db.insert_task(TaskRecord(
        id="TASK-001", type=TaskType.GENERAL, brief="b", assigned_agent="dev_agent",
    ))
    db.update_task("TASK-001", final_artifact_dir="artifacts/TASK-001")
    r = client.get("/api/v1/tasks/TASK-001/recall", params={"include_artifact": "true"})
    assert r.status_code == 200
    body = r.json()
    assert body["artifact"]["files"] == [
        {"path": "report.md", "content": "# Q1 report\n\nAll good."},
    ]
    assert body["artifact"]["truncated"] is False
```

Check the existing fixture names (`active_runtime`, `test_runtime`) in `tests/daemon/conftest.py`; if they don't exist, mirror whatever the passing daemon tests already use.

- [ ] **Step 2: Verify failure**

```
uv run pytest tests/daemon/test_routes_tasks.py -v -k "recall"
```
Expected: FAIL — endpoint doesn't exist.

- [ ] **Step 3: Implement route**

Add to `src/daemon/routes/tasks.py`:

```python
MAX_ARTIFACT_BYTES = 200 * 1024


def _read_artifact(runtime_workspaces, assigned_agent: str | None, artifact_dir: str | None) -> dict | None:
    """Read files under <workspace>/<artifact_dir>. Returns {files, truncated}
    or None if paths are missing."""
    from pathlib import Path
    if not assigned_agent or not artifact_dir:
        return None
    base = Path(runtime_workspaces) / assigned_agent / artifact_dir
    if not base.exists():
        return {"files": [], "truncated": False}
    files: list[dict] = []
    total = 0
    for f in sorted(base.rglob("*")):
        if not f.is_file():
            continue
        try:
            text = f.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        total += len(text.encode("utf-8"))
        if total > MAX_ARTIFACT_BYTES:
            return {
                "files": [{"path": str(f.relative_to(base))} for f in sorted(base.rglob("*")) if f.is_file()],
                "truncated": True,
            }
        files.append({"path": str(f.relative_to(base)), "content": text})
    return {"files": files, "truncated": False}


def _recall_node(state: DaemonState, task_id: str, tree: bool, include_artifact: bool) -> dict | None:
    payload = state.db.get_recall_payload(task_id)
    if payload is None:
        return None
    if include_artifact:
        payload["artifact"] = _read_artifact(
            state.runtime.workspaces_dir,
            payload.get("assigned_agent"),
            payload.get("artifact_dir"),
        )
    if tree:
        child_ids = payload["children"]
        payload["children"] = [
            _recall_node(state, cid, tree=True, include_artifact=include_artifact)
            for cid in child_ids
        ]
    return payload


@router.get("/tasks/{task_id}/recall")
def recall_task(
    task_id: str,
    request: Request,
    tree: bool = False,
    include_artifact: bool = False,
) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    node = _recall_node(state, task_id, tree=tree, include_artifact=include_artifact)
    if node is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    return node
```

Note: `state.runtime` is the `RuntimeDir`. Verify the attribute name (`runtime` vs `runtime_dir`) by reading `src/daemon/state.py` — use the actual attribute name in the code.

- [ ] **Step 4: Run tests**

```
uv run pytest tests/daemon/test_routes_tasks.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/tasks.py tests/daemon/test_routes_tasks.py
git commit -m "feat: add /tasks/{id}/recall endpoint with tree and artifact reading"
```

---

## Task 11: `opc recall` CLI command

**Files:**
- Modify: `src/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_cli.py`:

```python
def test_cli_recall_parses_flags():
    from src.cli import build_parser
    p = build_parser()
    args = p.parse_args(["recall", "TASK-001", "--tree", "--fetch-artifact"])
    assert args.task_id == "TASK-001"
    assert args.tree is True
    assert args.fetch_artifact is True
```

- [ ] **Step 2: Verify failure**

```
uv run pytest tests/test_cli.py -v -k "recall"
```
Expected: FAIL — subparser missing.

- [ ] **Step 3: Implement**

In `src/cli.py`, add `cmd_recall`:

```python
def cmd_recall(args: argparse.Namespace) -> None:
    """Fetch a task's brief, outcome, and optional artifact contents."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    params: dict[str, str] = {}
    if args.tree:
        params["tree"] = "true"
    if args.fetch_artifact:
        params["include_artifact"] = "true"
    r = client.get(f"/api/v1/tasks/{args.task_id}/recall", params=params)
    if r.status_code == 404:
        print(f"Task {args.task_id} not found.")
        sys.exit(1)
    if not _ok(r):
        return
    import json as _json
    print(_json.dumps(r.json(), indent=2))
```

And wire it in `build_parser`:

```python
    p_recall = sub.add_parser("recall", help="Recall a task: brief, outcome, optional artifact contents")
    p_recall.add_argument("task_id", help="Task ID (e.g. TASK-001)")
    p_recall.add_argument("--tree", action="store_true", help="Include full subtree of children")
    p_recall.add_argument("--fetch-artifact", dest="fetch_artifact", action="store_true",
                          help="Inline artifact file contents (capped at 200KB)")
    p_recall.set_defaults(func=cmd_recall)
```

- [ ] **Step 4: Verify pass**

```
uv run pytest tests/test_cli.py -v -k "recall"
```

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat: add opc recall CLI for fetching task brief and artifacts"
```

---

## Task 12: Update start-task skill — Step 1.5 + artifact convention

**Files:**
- Modify: `protocol/skills/start-task/SKILL.md`
- Test: `tests/test_skills.py`

- [ ] **Step 1: Read the existing skill file + existing test**

```bash
cat protocol/skills/start-task/SKILL.md
cat tests/test_skills.py
```

This is a doc change; the test reads the skill file and asserts key strings are present.

- [ ] **Step 2: Write failing tests**

Append to `tests/test_skills.py`:

```python
def test_start_task_skill_documents_memory_consult():
    from pathlib import Path
    text = Path("protocol/skills/start-task/SKILL.md").read_text()
    assert "task_history.md" in text
    assert "opc recall" in text
    assert "Consult memory" in text


def test_start_task_skill_documents_artifact_convention():
    from pathlib import Path
    text = Path("protocol/skills/start-task/SKILL.md").read_text()
    assert "artifacts/" in text
    assert "artifact_dir" in text
```

- [ ] **Step 3: Verify failure**

```
uv run pytest tests/test_skills.py -v
```
Expected: FAIL.

- [ ] **Step 4: Edit the SKILL.md**

Insert after the Step 1 ("Parse parameters") block, before Step 2 ("Plan and execute"):

```markdown
2. **Consult memory.** Before planning:
   1. Read `task_history.md` in your workspace root. It lists your recent tasks with briefs, outcomes, and artifact paths.
   2. If the current brief references prior work — phrases like "follow up on", "continue", "the report from last week", a specific date, or an explicit `TASK-xxx` — identify the matching entry and fetch details:
      ```bash
      opc recall <task_id> --fetch-artifact
      ```
   3. If the brief does not reference prior work, skip this step. Do not pull history speculatively.
```

Renumber subsequent steps accordingly (Plan and execute → 3, Report mid-task learnings → 4, Report completion → 5, Cleanup → 6).

In "Plan and execute", add near the end:

```markdown
If the task produces a standalone document (report, plan, analysis), write its files under `artifacts/<task_id>/` in your workspace root (not in any repo or worktree). Include the relative path in your completion payload as `artifact_dir`.
```

In "Report completion" JSON example, add the optional field:

```json
{
  "task_id": "<task_id>",
  "session_id": "<session_id>",
  "agent": "<your_agent_name>",
  "status": "completed",
  "confidence": 85,
  "summary": "<what you did>",
  "risks": ["<concern>"],
  "dependencies": ["<assumption>"],
  "reviewer_focus": ["<where to look hardest>"],
  "artifact_dir": "artifacts/<task_id>"
}
```

And in the block describing optional keys, add `artifact_dir` to the list of fields that may be omitted.

- [ ] **Step 5: Verify tests pass**

```
uv run pytest tests/test_skills.py -v
```

- [ ] **Step 6: Full suite sanity**

```
uv run pytest tests/ -v
```
Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add protocol/skills/start-task/SKILL.md tests/test_skills.py
git commit -m "docs: start-task skill — consult memory and write artifacts"
```

---

## Wrap-up

After Task 12 passes, the memory spec is implemented end-to-end. Manual smoke test (optional):

```bash
scripts/daemon.sh restart
opc run --brief "Review the payment module and give me a short report"
# ... wait for approved
opc recall TASK-001 --fetch-artifact
# Then:
opc run --brief "Follow up on TASK-001 — what actions should I take?"
```

If the EH recalls correctly, the second run should pull TASK-001's artifact via `opc recall` during its session.
