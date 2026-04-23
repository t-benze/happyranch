# Revisit Root Link Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Promote the predecessor ↔ new-root revisit link out of the audit log into a first-class, nullable `revisit_of_task_id` column on `tasks`, with visibility in `opc details`, `opc tasks`, and `opc recall`.

**Architecture:** Add one column + one index on `tasks`. Backfill from existing `revisit_of` audit entries on daemon startup. Preserve attempt isolation by making the column a *sideways reference* only — `walk_ancestors` never follows it. Visibility rides on existing read surfaces via two new read-side helpers (`walk_revisit_chain`, `get_direct_revisits`).

**Tech Stack:** Python 3.11+, FastAPI, SQLite (WAL), Pydantic v2, pytest, `uv run` for everything.

**Spec:** `docs/superpowers/specs/2026-04-23-revisit-root-link-design.md`

---

## File Structure

Files to modify (no new files):

- `src/infrastructure/database.py` — column migration, backfill, `walk_revisit_chain`, `get_direct_revisits`, thread `revisit_of_task_id` through `insert_task` / `get_task` / `list_tasks` / `list_agent_tasks`.
- `src/models.py` — one new field on `TaskRecord`.
- `src/daemon/routes/tasks.py` — pass `revisit_of_task_id` in revisit endpoint insert; include field in `get_recall_payload` (indirectly, via DB helper change).
- `src/cli.py` — `cmd_details` header/chain/footer rendering; `cmd_tasks` suffix.
- `CLAUDE.md` — a short note under the "Revisit (founder recovery)" section.
- Tests in `tests/test_database.py`, `tests/daemon/test_routes_tasks.py`, `tests/test_cli.py`.

Task ordering is chosen so each task compiles and passes its own tests without the next one.

---

## Test Fixtures You Will Need

The `db` fixture (see `tests/conftest.py:26-29`) gives you a fresh `Database` backed by a tmp file:

```python
@pytest.fixture
def db(tmp_dir: Path) -> Database:
    return Database(tmp_dir / "test.db")
```

The daemon route tests use `TestClient(app)` plus `auth_headers`; see existing revisit tests at `tests/daemon/test_routes_tasks.py:660-720` for the pattern.

CLI tests mock the client via `patch("src.cli.OpcClient.from_env", return_value=fake)`; see `tests/test_cli.py:136-149`.

---

## Task 1: Add the `revisit_of_task_id` column + index (schema only)

**Files:**
- Modify: `src/infrastructure/database.py:149-178` (add to the existing idempotent migration block)
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_database.py`:

```python
def test_revisit_of_task_id_column_exists(db):
    """The tasks table must gain a nullable revisit_of_task_id column.
    Idempotent on restart: reopening the same DB must not error.
    """
    cols = {row[1] for row in db._conn.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "revisit_of_task_id" in cols

    # Index exists (keeps the reverse lookup `WHERE revisit_of_task_id = ?` cheap).
    indexes = {row[1] for row in db._conn.execute(
        "SELECT * FROM sqlite_master WHERE type='index' AND tbl_name='tasks'"
    ).fetchall()}
    assert "idx_tasks_revisit_of" in indexes


def test_migration_idempotent_over_restart(tmp_path):
    """Opening a Database twice on the same file must not raise."""
    from src.infrastructure.database import Database
    path = tmp_path / "restart.db"
    db1 = Database(path)
    db1.close()
    # Second open is where duplicate-column / duplicate-index errors would fire
    # if the migration weren't guarded.
    db2 = Database(path)
    cols = {row[1] for row in db2._conn.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "revisit_of_task_id" in cols
    db2.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_database.py::test_revisit_of_task_id_column_exists tests/test_database.py::test_migration_idempotent_over_restart -v`

Expected: FAIL — column not in `PRAGMA table_info` output.

- [ ] **Step 3: Add the migration**

In `src/infrastructure/database.py`, locate the existing idempotent migration block around `database.py:163-178` (the `for ddl in (...)` loop that adds `block_kind`, `note`, `orchestration_step_count`, `cancelled_at`). Add one more `ALTER` to that tuple:

```python
        # --- Task-status redesign migration (idempotent) ---
        # Add new columns; swallow duplicate errors on subsequent startups.
        for ddl in (
            "ALTER TABLE tasks ADD COLUMN block_kind TEXT",
            "ALTER TABLE tasks ADD COLUMN note TEXT",
            "ALTER TABLE tasks ADD COLUMN orchestration_step_count INTEGER DEFAULT 0",
            "ALTER TABLE tasks ADD COLUMN cancelled_at TEXT",
            # Revisit link: see docs/superpowers/specs/2026-04-23-revisit-root-link-design.md.
            # Sideways reference to the predecessor root of a revisit; NULL for
            # non-revisit tasks. walk_ancestors MUST NOT follow this column —
            # that's the attempt-isolation invariant from the v2 revisit spec.
            "ALTER TABLE tasks ADD COLUMN revisit_of_task_id TEXT",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        # Index the reverse lookup (`WHERE revisit_of_task_id = ?`).
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_revisit_of ON tasks(revisit_of_task_id)"
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_database.py::test_revisit_of_task_id_column_exists tests/test_database.py::test_migration_idempotent_over_restart -v`

Expected: PASS.

- [ ] **Step 5: Re-run the full database test file (nothing else should regress)**

Run: `uv run pytest tests/test_database.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "feat(db): add revisit_of_task_id column + index

Idempotent ALTER + CREATE INDEX IF NOT EXISTS. Column is a sideways
reference only; walk_ancestors does not follow it (attempt isolation).
"
```

---

## Task 2: Thread `revisit_of_task_id` through the `TaskRecord` model and CRUD

**Files:**
- Modify: `src/models.py:47-63` (TaskRecord)
- Modify: `src/infrastructure/database.py` — `insert_task`, `get_task`, `list_tasks`, `list_agent_tasks`
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_database.py`:

```python
def test_insert_task_round_trips_revisit_of(db):
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="predecessor"))
    db.insert_task(TaskRecord(
        id="TASK-002",
        type=TaskType.GENERAL,
        brief="revisit",
        revisit_of_task_id="TASK-001",
    ))
    got = db.get_task("TASK-002")
    assert got is not None
    assert got.revisit_of_task_id == "TASK-001"

    # Non-revisit tasks keep it NULL on read.
    got_pre = db.get_task("TASK-001")
    assert got_pre.revisit_of_task_id is None


def test_list_tasks_exposes_revisit_of(db):
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="pre"))
    db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="rv",
        revisit_of_task_id="TASK-001",
    ))
    rows = {t.id: t for t in db.list_tasks()}
    assert rows["TASK-002"].revisit_of_task_id == "TASK-001"
    assert rows["TASK-001"].revisit_of_task_id is None


def test_update_task_cannot_change_revisit_of_task_id(db):
    """The column is write-once at insert time. Guards against accidental
    mutation from other write paths."""
    db.insert_task(TaskRecord(
        id="TASK-001", type=TaskType.GENERAL, brief="rv",
        revisit_of_task_id="TASK-000",
    ))
    db.update_task("TASK-001", revisit_of_task_id="TASK-999")
    got = db.get_task("TASK-001")
    assert got.revisit_of_task_id == "TASK-000"  # unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_database.py -k "round_trips_revisit_of or list_tasks_exposes_revisit_of or update_task_cannot_change_revisit_of" -v`

Expected: FAIL — `TaskRecord` has no `revisit_of_task_id` field.

- [ ] **Step 3: Add the field to `TaskRecord`**

In `src/models.py` around line 47-63, extend the model:

```python
class TaskRecord(BaseModel):
    id: str
    type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: str | None = None
    team: str = "product_engineering"
    brief: str
    parent_task_id: str | None = None
    revisit_of_task_id: str | None = None
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

- [ ] **Step 4: Wire the column through `insert_task`**

In `src/infrastructure/database.py`, update `insert_task` (around `database.py:213-236`):

```python
    @_synchronized
    def insert_task(self, task: TaskRecord) -> None:
        self._conn.execute(
            """INSERT INTO tasks (id, type, status, assigned_agent, team, brief,
               revision_count, created_at, updated_at, completed_at, parent_task_id,
               block_kind, note, orchestration_step_count, revisit_of_task_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                task.block_kind.value if task.block_kind else None,
                task.note,
                task.orchestration_step_count,
                task.revisit_of_task_id,
            ),
        )
        self._conn.commit()
```

- [ ] **Step 5: Wire the column through `get_task`, `list_tasks`, `list_agent_tasks`**

Each of these materializes a `TaskRecord` from `row`. Add `revisit_of_task_id=row["revisit_of_task_id"]` to all three. Locations: `get_task` around `database.py:239-261`, `list_tasks` around `database.py:264-288`, `list_agent_tasks` around `database.py:355-389`.

Example patch for `get_task`:

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
            block_kind=row["block_kind"],
            note=row["note"],
            orchestration_step_count=row["orchestration_step_count"] or 0,
            final_artifact_dir=row["final_artifact_dir"],
            cancelled_at=row["cancelled_at"],
            revisit_of_task_id=row["revisit_of_task_id"],
        )
```

Apply the same final-line addition to `list_tasks` and `list_agent_tasks`.

**`update_task` stays unchanged** — the `allowed` set at `database.py:393-397` does NOT include `revisit_of_task_id`. That's what makes the third test pass (updates silently drop the field). Do NOT add it.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_database.py -k "round_trips_revisit_of or list_tasks_exposes_revisit_of or update_task_cannot_change_revisit_of" -v`

Expected: PASS.

- [ ] **Step 7: Run the full database tests to check no regressions**

Run: `uv run pytest tests/test_database.py tests/test_models.py -v`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/models.py src/infrastructure/database.py tests/test_database.py
git commit -m "feat(db): thread revisit_of_task_id through TaskRecord CRUD

Write-once at insert; update_task intentionally omits the column from
its allowed-fields set.
"
```

---

## Task 3: Revisit endpoint writes the column

**Files:**
- Modify: `src/daemon/routes/tasks.py:373-393` (the `insert_task` call inside `revisit_task`)
- Test: `tests/daemon/test_routes_tasks.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/daemon/test_routes_tasks.py` alongside the existing revisit tests (after `test_revisit_creates_new_root_from_failed_predecessor` around line 720):

```python
def test_revisit_writes_revisit_of_task_id_on_new_root(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """The new root's revisit_of_task_id column must equal the predecessor
    root's id. This is what makes the link queryable without audit-log scans."""
    from src.models import TaskRecord, TaskStatus, TaskType
    db = daemon_state.db
    db.insert_task(TaskRecord(
        id="TASK-052", type=TaskType.IMPLEMENT_FEATURE, brief="Add Alipay support",
    ))
    db.update_task("TASK-052", status=TaskStatus.FAILED, note="rc=1")

    r = TestClient(app).post(
        "/api/v1/tasks/TASK-052/revisit",
        json={"founder_note": None},
        headers=auth_headers,
    )
    assert r.status_code == 200
    new_id = r.json()["new_root_task_id"]
    new_root = db.get_task(new_id)
    assert new_root.revisit_of_task_id == "TASK-052"


def test_plain_run_leaves_revisit_of_task_id_null(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """Plain /tasks POST (no revisit) must not set the column."""
    r = TestClient(app).post(
        "/api/v1/tasks",
        json={"type": "general", "brief": "plain task"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    tid = r.json()["task_id"]
    row = daemon_state.db.get_task(tid)
    assert row.revisit_of_task_id is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_routes_tasks.py -k "revisit_writes_revisit_of_task_id or plain_run_leaves_revisit_of_task_id" -v`

Expected: FAIL on the first test — `new_root.revisit_of_task_id` is `None`. The second passes (it already works — kept in the plan as a regression guard).

- [ ] **Step 3: Pass the predecessor id in the revisit endpoint**

In `src/daemon/routes/tasks.py` inside `revisit_task`, the `insert_task` call is around `routes/tasks.py:375-381`. Add `revisit_of_task_id=predecessor.id`:

```python
    async with state.db_lock:
        new_id = state.db.next_task_id()
        state.db.insert_task(TaskRecord(
            id=new_id,
            type=predecessor.type,
            brief=predecessor.brief,
            status=TaskStatus.PENDING,
            parent_task_id=None,
            revisit_of_task_id=predecessor.id,
        ))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/daemon/test_routes_tasks.py -k "revisit_writes_revisit_of_task_id or plain_run_leaves_revisit_of_task_id" -v`

Expected: PASS.

- [ ] **Step 5: Run the full revisit route test suite**

Run: `uv run pytest tests/daemon/test_routes_tasks.py -k revisit -v`

Expected: PASS for all ~11 existing revisit tests — this is the regression gate.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/routes/tasks.py tests/daemon/test_routes_tasks.py
git commit -m "feat(revisit): write revisit_of_task_id on the new root

New revisit calls populate the column; plain /tasks POST leaves NULL.
"
```

---

## Task 4: Backfill historical revisit rows from the audit log

**Files:**
- Modify: `src/infrastructure/database.py` — add a `_backfill_revisit_of_task_id` method called from `_create_tables` at end.
- Test: `tests/test_database.py`

Context: rows created by revisit BEFORE this feature existed have `revisit_of_task_id IS NULL` but a matching `action='revisit_of'` audit entry. We backfill those at startup so `opc details` on old revisits is useful immediately.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_database.py`:

```python
def test_backfill_populates_revisit_of_task_id_from_audit_log(tmp_path):
    """Simulates a pre-feature revisit row: tasks has the column but no value,
    audit_log has the revisit_of entry. Reopening the DB must backfill."""
    from src.infrastructure.database import Database
    import json as _json

    path = tmp_path / "backfill.db"
    db = Database(path)

    # Seed: predecessor root + new root (no revisit_of_task_id yet) + audit entry.
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="pre"))
    db.insert_task(TaskRecord(id="TASK-002", type=TaskType.GENERAL, brief="rv"))
    # Forcibly NULL the column to simulate legacy data even if Task 3 shipped first.
    db._conn.execute(
        "UPDATE tasks SET revisit_of_task_id = NULL WHERE id = 'TASK-002'"
    )
    db._conn.commit()
    db.insert_audit_log(
        task_id="TASK-002",
        agent="founder",
        action="revisit_of",
        payload={
            "predecessor_root": "TASK-001",
            "flagged": "TASK-001",
            "cascade": ["TASK-001"],
            "prior_status": "failed",
            "founder_note": None,
        },
    )
    db.close()

    # Reopen — backfill runs in _create_tables.
    db2 = Database(path)
    row = db2.get_task("TASK-002")
    assert row.revisit_of_task_id == "TASK-001"
    db2.close()


def test_backfill_does_not_overwrite_existing_value(tmp_path):
    """If revisit_of_task_id is already set, backfill must leave it alone —
    idempotent guard against audit-entry drift."""
    from src.infrastructure.database import Database
    path = tmp_path / "no-overwrite.db"
    db = Database(path)
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="pre"))
    db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="rv",
        revisit_of_task_id="TASK-001",
    ))
    # Seed a conflicting audit entry; backfill must NOT overwrite.
    db.insert_audit_log(
        task_id="TASK-002", agent="founder", action="revisit_of",
        payload={"predecessor_root": "TASK-999", "flagged": "TASK-999",
                 "cascade": ["TASK-999"], "prior_status": "failed",
                 "founder_note": None},
    )
    db.close()

    db2 = Database(path)
    assert db2.get_task("TASK-002").revisit_of_task_id == "TASK-001"
    db2.close()


def test_backfill_is_a_noop_when_nothing_to_backfill(tmp_path):
    """Opening a DB with no revisit_of audit entries must not raise."""
    from src.infrastructure.database import Database
    path = tmp_path / "clean.db"
    db = Database(path)
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="x"))
    db.close()
    # No revisit audit entries exist; reopening should be clean.
    Database(path).close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_database.py -k backfill -v`

Expected: FAIL on the first test (`revisit_of_task_id` stays NULL); the other two pass trivially but remain as regression guards.

- [ ] **Step 3: Implement the backfill**

In `src/infrastructure/database.py`, at the very end of `_create_tables` (after the final `self._conn.commit()` around line 201), add:

```python
        # --- Revisit link backfill ---
        # Historical revisit rows (created before revisit_of_task_id existed)
        # have the column but no value; the link lives only in audit_log's
        # revisit_of entry. Populate the column from those entries.
        # IS NULL guard makes this safely idempotent across restarts.
        self._backfill_revisit_of_task_id()

```

Then define the method below `_create_tables`:

```python
    def _backfill_revisit_of_task_id(self) -> None:
        cursor = self._conn.execute(
            "SELECT task_id, payload FROM audit_log WHERE action = 'revisit_of'"
        )
        for row in cursor.fetchall():
            if not row["payload"]:
                continue
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                continue
            predecessor_root = payload.get("predecessor_root")
            if not predecessor_root:
                continue
            self._conn.execute(
                "UPDATE tasks SET revisit_of_task_id = ? "
                "WHERE id = ? AND revisit_of_task_id IS NULL",
                (predecessor_root, row["task_id"]),
            )
        self._conn.commit()
```

Note: this runs under `threading.RLock` via `_synchronized` only because `_create_tables` itself is called from `__init__` (single-threaded). If you ever move this call elsewhere, wrap it; for now, it's safe as-is.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_database.py -k backfill -v`

Expected: PASS.

- [ ] **Step 5: Run the full database test suite**

Run: `uv run pytest tests/test_database.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "feat(db): backfill revisit_of_task_id from audit_log on startup

Populates the new column for revisit rows that predate the migration.
IS NULL guard makes it idempotent across restarts.
"
```

---

## Task 5: `walk_revisit_chain` helper

**Files:**
- Modify: `src/infrastructure/database.py` — add helper near `walk_ancestors` (around line 300)
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_database.py`:

```python
def test_walk_revisit_chain_returns_task_to_original(db):
    """Stacked chain: P (original) → N (revisit of P) → N' (revisit of N).
    walk_revisit_chain(N') returns [N', N, P]."""
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="P"))
    db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="N",
        revisit_of_task_id="TASK-001",
    ))
    db.insert_task(TaskRecord(
        id="TASK-003", type=TaskType.GENERAL, brief="N-prime",
        revisit_of_task_id="TASK-002",
    ))
    chain = db.walk_revisit_chain("TASK-003")
    assert [t.id for t in chain] == ["TASK-003", "TASK-002", "TASK-001"]


def test_walk_revisit_chain_non_revisit_returns_single(db):
    """Plain task: returns [task] only."""
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="plain"))
    chain = db.walk_revisit_chain("TASK-001")
    assert [t.id for t in chain] == ["TASK-001"]


def test_walk_revisit_chain_missing_task_returns_empty(db):
    assert db.walk_revisit_chain("TASK-999") == []


def test_walk_revisit_chain_raises_when_over_limit(db):
    """Defensive bound matching walk_ancestors."""
    from src.infrastructure.database import LineageTooDeep
    db.insert_task(TaskRecord(id="TASK-000", type=TaskType.GENERAL, brief="orig"))
    prev = "TASK-000"
    for i in range(1, 25):
        tid = f"TASK-{i:03d}"
        db.insert_task(TaskRecord(
            id=tid, type=TaskType.GENERAL, brief=f"t{i}",
            revisit_of_task_id=prev,
        ))
        prev = tid
    with pytest.raises(LineageTooDeep):
        db.walk_revisit_chain(prev, max_hops=20)


def test_walk_ancestors_does_not_follow_revisit_edge(db):
    """REGRESSION GUARD: cascade-fail in run_step keys on walk_ancestors. If
    walk_ancestors ever followed revisit_of_task_id, a predecessor's FAILED
    children would poison the new root via _enqueue_parent_if_waiting.
    Never let this test go green by making walk_ancestors follow the edge.
    """
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="P"))
    db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="N",
        revisit_of_task_id="TASK-001",  # NOT a parent edge.
        parent_task_id=None,             # Still a root.
    ))
    chain = db.walk_ancestors("TASK-002")
    assert [t.id for t in chain] == ["TASK-002"]  # Does NOT include TASK-001.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_database.py -k "walk_revisit_chain or walk_ancestors_does_not_follow_revisit_edge" -v`

Expected: FAIL on the four `walk_revisit_chain` tests (helper missing). The regression guard on `walk_ancestors` should already pass — if it doesn't, you have a bigger problem than this plan.

- [ ] **Step 3: Implement `walk_revisit_chain`**

In `src/infrastructure/database.py`, add just after `walk_ancestors` (around line 319):

```python
    @_synchronized
    def walk_revisit_chain(self, task_id: str, max_hops: int = 20) -> list[TaskRecord]:
        """Return [task, predecessor, ..., original] by following revisit_of_task_id.

        Sideways edge — does NOT cross into parent_task_id ancestor space.
        Non-revisit tasks return [task]. Missing task returns []. Overruns
        raise LineageTooDeep (same pattern as walk_ancestors).
        """
        chain: list[TaskRecord] = []
        current_id: str | None = task_id
        for _ in range(max_hops):
            if current_id is None:
                return chain
            task = self.get_task(current_id)
            if task is None:
                return chain
            chain.append(task)
            current_id = task.revisit_of_task_id
        if current_id is not None:
            raise LineageTooDeep(
                f"revisit chain from {task_id} exceeded {max_hops} hops"
            )
        return chain
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_database.py -k "walk_revisit_chain or walk_ancestors_does_not_follow_revisit_edge" -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "feat(db): walk_revisit_chain helper + attempt-isolation guard

Helper walks the sideways revisit edge. Regression test pins
walk_ancestors to parent_task_id only.
"
```

---

## Task 6: `get_direct_revisits` reverse lookup

**Files:**
- Modify: `src/infrastructure/database.py` — add helper near `get_children` (around line 290)
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_database.py`:

```python
def test_get_direct_revisits_returns_all_direct_children(db):
    """Two revisits of the same predecessor — both appear, ordered by creation."""
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="P"))
    db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="rv1",
        revisit_of_task_id="TASK-001",
    ))
    db.insert_task(TaskRecord(
        id="TASK-003", type=TaskType.GENERAL, brief="rv2",
        revisit_of_task_id="TASK-001",
    ))
    assert db.get_direct_revisits("TASK-001") == ["TASK-002", "TASK-003"]


def test_get_direct_revisits_does_not_include_transitive(db):
    """In P → N → N', P.get_direct_revisits returns only [N], not [N, N']."""
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="P"))
    db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="N",
        revisit_of_task_id="TASK-001",
    ))
    db.insert_task(TaskRecord(
        id="TASK-003", type=TaskType.GENERAL, brief="N'",
        revisit_of_task_id="TASK-002",
    ))
    assert db.get_direct_revisits("TASK-001") == ["TASK-002"]
    assert db.get_direct_revisits("TASK-002") == ["TASK-003"]


def test_get_direct_revisits_none(db):
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="x"))
    assert db.get_direct_revisits("TASK-001") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_database.py -k get_direct_revisits -v`

Expected: FAIL — method missing.

- [ ] **Step 3: Implement `get_direct_revisits`**

In `src/infrastructure/database.py` just after `get_children` (around line 297):

```python
    @_synchronized
    def get_direct_revisits(self, task_id: str) -> list[str]:
        """Return IDs of tasks whose revisit_of_task_id points at this task,
        ordered by creation. Uses idx_tasks_revisit_of.
        """
        cursor = self._conn.execute(
            "SELECT id FROM tasks WHERE revisit_of_task_id = ? ORDER BY created_at",
            (task_id,),
        )
        return [row["id"] for row in cursor.fetchall()]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_database.py -k get_direct_revisits -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "feat(db): get_direct_revisits reverse-lookup helper"
```

---

## Task 7: `get_recall_payload` exposes `revisit_of_task_id`

**Files:**
- Modify: `src/infrastructure/database.py:322-352` (`get_recall_payload`)
- Test: `tests/daemon/test_routes_tasks.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/daemon/test_routes_tasks.py` (near the other recall tests, around line 218):

```python
def test_recall_payload_includes_revisit_of_task_id(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    from src.models import TaskRecord, TaskType
    db = daemon_state.db
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="P"))
    db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="rv",
        revisit_of_task_id="TASK-001",
    ))
    r = TestClient(app).get("/api/v1/tasks/TASK-002/recall", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["revisit_of_task_id"] == "TASK-001"

    # Non-revisit: NULL round-trips as null, not missing key.
    r2 = TestClient(app).get("/api/v1/tasks/TASK-001/recall", headers=auth_headers)
    assert r2.status_code == 200
    assert r2.json()["revisit_of_task_id"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/daemon/test_routes_tasks.py::test_recall_payload_includes_revisit_of_task_id -v`

Expected: FAIL — key absent from response.

- [ ] **Step 3: Update `get_recall_payload`**

In `src/infrastructure/database.py` around `database.py:341-352`, add the key to the returned dict:

```python
        return {
            "task_id": task.id,
            "parent_task_id": task.parent_task_id,
            "revisit_of_task_id": task.revisit_of_task_id,
            "assigned_agent": task.assigned_agent,
            "brief": task.brief,
            "status": task.status.value,
            "created_at": created_at,
            "completed_at": completed_at,
            "output_summary": task.note,
            "artifact_dir": task.final_artifact_dir,
            "children": self.get_children(task.id),
        }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/daemon/test_routes_tasks.py::test_recall_payload_includes_revisit_of_task_id -v`

Expected: PASS.

- [ ] **Step 5: Run the full recall test group**

Run: `uv run pytest tests/daemon/test_routes_tasks.py -k recall -v`

Expected: PASS — the existing recall tests shouldn't care about the new key.

- [ ] **Step 6: Commit**

```bash
git add src/infrastructure/database.py tests/daemon/test_routes_tasks.py
git commit -m "feat(recall): expose revisit_of_task_id in /recall payload"
```

---

## Task 8: `opc details` header, chain line, and footer

**Files:**
- Modify: `src/cli.py:161-195` (`cmd_details`)
- Test: `tests/test_cli.py`

The route `GET /tasks/{id}` returns `{"task": {...}, "results": [...], "audit_log": [...]}` (see `src/daemon/routes/tasks.py:69-73`). After Task 2, `task` includes `revisit_of_task_id`. We also need the predecessor's normalized `prior_status` (from the `revisit_of` audit entry payload), the revisit chain (client-side reconstruction via `walk_revisit_chain`'s data), and the list of direct revisits. Two options:

**Option A (chosen):** Add the chain + direct-revisit list to the `GET /tasks/{id}` response so the CLI has everything it needs in one round trip. Keeps the CLI dumb.

**Option B:** Have the CLI make follow-up requests. Rejected — chatty.

- [ ] **Step 1: Write the failing daemon test**

Add to `tests/daemon/test_routes_tasks.py`:

```python
def test_get_task_includes_revisit_chain_and_direct_revisits(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """GET /tasks/{id} must surface the full revisit context for the CLI."""
    from src.models import TaskRecord, TaskType
    db = daemon_state.db
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="P"))
    db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="N",
        revisit_of_task_id="TASK-001",
    ))
    db.insert_task(TaskRecord(
        id="TASK-003", type=TaskType.GENERAL, brief="another revisit of P",
        revisit_of_task_id="TASK-001",
    ))
    # prior_status comes from the revisit_of audit entry on TASK-002.
    db.insert_audit_log(
        task_id="TASK-002", agent="founder", action="revisit_of",
        payload={"predecessor_root": "TASK-001", "flagged": "TASK-001",
                 "cascade": ["TASK-001"], "prior_status": "failed-cancelled",
                 "founder_note": None},
    )

    r = TestClient(app).get("/api/v1/tasks/TASK-002", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    # Chain: [task, predecessor, ...]
    assert body["revisit_chain"] == ["TASK-002", "TASK-001"]
    # prior_status pulled from audit entry
    assert body["predecessor_prior_status"] == "failed-cancelled"
    # Direct revisits of THIS task (not its predecessor) — should be empty.
    assert body["direct_revisits"] == []

    r2 = TestClient(app).get("/api/v1/tasks/TASK-001", headers=auth_headers)
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["revisit_chain"] == ["TASK-001"]
    assert body2["predecessor_prior_status"] is None
    assert set(body2["direct_revisits"]) == {"TASK-002", "TASK-003"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/daemon/test_routes_tasks.py::test_get_task_includes_revisit_chain_and_direct_revisits -v`

Expected: FAIL — keys absent.

- [ ] **Step 3: Extend `get_task` route**

In `src/daemon/routes/tasks.py` around `routes/tasks.py:62-73`:

```python
@router.get("/tasks/{task_id}")
def get_task(task_id: str, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    _require_active(state)
    task = state.db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    # Revisit context: chain (this task back to original), direct revisits
    # (tasks that revisit THIS task), and the predecessor's normalized
    # prior_status (pulled from the revisit_of audit entry, which carries the
    # 4-valued spec label: failed / failed-cancelled / blocked-escalated / completed).
    chain = [t.id for t in state.db.walk_revisit_chain(task_id)]
    direct_revisits = state.db.get_direct_revisits(task_id)
    prior_status = None
    if task.revisit_of_task_id is not None:
        for entry in state.db.get_audit_logs(task_id):
            if entry["action"] == "revisit_of":
                payload = entry.get("payload") or {}
                prior_status = payload.get("prior_status")
                break

    return {
        "task": task.model_dump(),
        "results": state.db.get_task_results(task_id),
        "audit_log": state.db.get_audit_logs(task_id),
        "revisit_chain": chain,
        "direct_revisits": direct_revisits,
        "predecessor_prior_status": prior_status,
    }
```

- [ ] **Step 4: Run the daemon test to verify it passes**

Run: `uv run pytest tests/daemon/test_routes_tasks.py::test_get_task_includes_revisit_chain_and_direct_revisits -v`

Expected: PASS.

- [ ] **Step 5: Run the full route test file**

Run: `uv run pytest tests/daemon/test_routes_tasks.py -v`

Expected: PASS.

- [ ] **Step 6: Write the failing CLI test**

Add to `tests/test_cli.py`:

```python
def test_cmd_details_shows_revisit_header_chain_and_footer(capsys):
    """When the task is a revisit AND has later revisits, details must show:
    - a `Revisit of:` header line with the predecessor id and prior_status
    - a `Chain:` line with the full chain, oldest leftmost, (this) marker
    - a `Revisited as:` footer line listing direct revisits
    """
    from src.cli import cmd_details
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "task": {
            "id": "TASK-072",
            "type": "implement_feature",
            "status": "pending",
            "assigned_agent": None,
            "brief": "Add Alipay support",
            "created_at": "2026-04-23T10:00:00+00:00",
            "updated_at": "2026-04-23T10:00:00+00:00",
            "revisit_of_task_id": "TASK-068",
        },
        "results": [],
        "audit_log": [],
        "revisit_chain": ["TASK-072", "TASK-068", "TASK-052"],
        "direct_revisits": ["TASK-091", "TASK-103"],
        "predecessor_prior_status": "failed-cancelled",
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-072")
        cmd_details(args)
    out = capsys.readouterr().out
    # Header
    assert "Revisit of: TASK-068" in out
    assert "failed-cancelled" in out
    # Chain: oldest-first, with (this) marker on the current task
    assert "TASK-052" in out
    assert "TASK-068" in out
    assert "TASK-072" in out
    assert "(this)" in out
    # Arrow direction — ← reads "created from"
    assert "←" in out
    # Footer
    assert "Revisited as: TASK-091, TASK-103" in out


def test_cmd_details_omits_revisit_blocks_when_plain_task(capsys):
    """Non-revisit task with no descendants must render cleanly — no empty
    'Revisit of:' / 'Chain:' / 'Revisited as:' lines."""
    from src.cli import cmd_details
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "task": {
            "id": "TASK-001",
            "type": "general",
            "status": "pending",
            "assigned_agent": None,
            "brief": "plain task",
            "created_at": "2026-04-23T10:00:00+00:00",
            "updated_at": "2026-04-23T10:00:00+00:00",
            "revisit_of_task_id": None,
        },
        "results": [],
        "audit_log": [],
        "revisit_chain": ["TASK-001"],
        "direct_revisits": [],
        "predecessor_prior_status": None,
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-001")
        cmd_details(args)
    out = capsys.readouterr().out
    assert "Revisit of:" not in out
    assert "Chain:" not in out
    assert "Revisited as:" not in out


def test_cmd_details_shows_footer_only_when_predecessor_has_revisits(capsys):
    """Predecessor-side view: task is NOT a revisit (no header/chain) but
    HAS been revisited (footer present)."""
    from src.cli import cmd_details
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {
        "task": {
            "id": "TASK-052",
            "type": "general",
            "status": "failed",
            "assigned_agent": None,
            "brief": "the original",
            "created_at": "2026-04-21T10:00:00+00:00",
            "updated_at": "2026-04-21T10:00:00+00:00",
            "revisit_of_task_id": None,
        },
        "results": [],
        "audit_log": [],
        "revisit_chain": ["TASK-052"],
        "direct_revisits": ["TASK-072"],
        "predecessor_prior_status": None,
    }
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(task_id="TASK-052")
        cmd_details(args)
    out = capsys.readouterr().out
    assert "Revisit of:" not in out
    assert "Chain:" not in out
    assert "Revisited as: TASK-072" in out
```

- [ ] **Step 7: Run the CLI tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "cmd_details_shows_revisit or cmd_details_omits_revisit or cmd_details_shows_footer_only_when_predecessor" -v`

Expected: FAIL — rendering not implemented yet.

- [ ] **Step 8: Update `cmd_details`**

In `src/cli.py` around `cli.py:161-195`, extend `cmd_details`:

```python
def cmd_details(args: argparse.Namespace) -> None:
    """Show status of a specific task."""
    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)
    r = client.get(f"/api/v1/tasks/{args.task_id}")
    if r.status_code == 404:
        print(f"Task {args.task_id} not found.")
        sys.exit(1)
    if not _ok(r):
        return
    body = r.json()
    task = body["task"]

    # Revisit header: shown only when this task IS a revisit.
    if task.get("revisit_of_task_id"):
        prior = body.get("predecessor_prior_status") or "unknown"
        print(f"Revisit of: {task['revisit_of_task_id']}  (predecessor: {prior})")
        chain = body.get("revisit_chain") or []
        if len(chain) > 1:
            # Oldest leftmost; arrows point current ← predecessor ← ... ← original.
            # walk_revisit_chain returns [task, predecessor, ..., original], so
            # reverse for display.
            display = list(reversed(chain))
            # Mark the current task (always the last entry after reverse).
            display[-1] = f"{display[-1]} (this)"
            print(f"Chain:      {' ← '.join(display)}")

    print(f"Task:       {task['id']}")
    print(f"Type:       {task['type']}")
    print(f"Status:     {task['status']}")
    print(f"Agent:      {task.get('assigned_agent') or '-'}")
    print(f"Brief:      {task['brief']}")
    print(f"Created:    {task['created_at']}")
    print(f"Updated:    {task['updated_at']}")
    if task.get("block_kind"):
        print(f"Block kind: {task['block_kind']}")
    if task.get("note"):
        print(f"Note:       {task['note']}")
    if body.get("results"):
        print(f"\nResults ({len(body['results'])}):")
        for r_ in body["results"]:
            print(f"  - [{r_['agent']}] confidence={r_['confidence_score']}  {r_['output_summary'][:80]}")
    if body.get("audit_log"):
        print(f"\nAudit log ({len(body['audit_log'])} entries):")
        for log in body["audit_log"]:
            print(f"  {log['timestamp'][:19]}  {log['agent']:20s}  {log['action']}")

    # Revisit footer: shown only when this task HAS been revisited.
    direct = body.get("direct_revisits") or []
    if direct:
        print(f"\nRevisited as: {', '.join(direct)}")
```

Note the reverse-chain logic: `walk_revisit_chain` returns `[task, predecessor, ..., original]`. To display "original ← ... ← current (this)" we reverse so `original` is first, then suffix the final element with `(this)`.

- [ ] **Step 9: Run the CLI tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "cmd_details_shows_revisit or cmd_details_omits_revisit or cmd_details_shows_footer_only_when_predecessor" -v`

Expected: PASS.

- [ ] **Step 10: Run the full CLI test file**

Run: `uv run pytest tests/test_cli.py -v`

Expected: PASS — existing `test_cmd_details_handles_404` and `test_cmd_details_shows_note` still pass.

- [ ] **Step 11: Commit**

```bash
git add src/daemon/routes/tasks.py src/cli.py tests/daemon/test_routes_tasks.py tests/test_cli.py
git commit -m "feat(cli): details shows revisit header, chain, and footer

GET /tasks/{id} now returns revisit_chain, direct_revisits, and
predecessor_prior_status so the CLI can render the full context
in a single round trip.
"
```

---

## Task 9: `opc tasks` suffix on revisit rows

**Files:**
- Modify: `src/cli.py:136-158` (`cmd_tasks`)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_cmd_tasks_suffixes_revisit_rows(capsys):
    """Tasks that have a predecessor root show `↩ TASK-XXX` as a trailing
    marker; plain tasks render unchanged."""
    from src.cli import cmd_tasks
    fake = MagicMock()
    fake.get.return_value.status_code = 200
    fake.get.return_value.json.return_value = {"tasks": [
        {
            "id": "TASK-072", "type": "implement_feature", "status": "pending",
            "brief": "Add Alipay support",
            "assigned_agent": None,
            "revisit_of_task_id": "TASK-052",
        },
        {
            "id": "TASK-001", "type": "general", "status": "completed",
            "brief": "plain task",
            "assigned_agent": "dev_agent",
            "revisit_of_task_id": None,
        },
    ]}
    with patch("src.cli.OpcClient.from_env", return_value=fake):
        args = MagicMock(limit=20)
        cmd_tasks(args)
    out = capsys.readouterr().out
    lines = out.splitlines()
    revisit_line = next(line for line in lines if "TASK-072" in line)
    plain_line = next(line for line in lines if "TASK-001" in line)
    assert "↩ TASK-052" in revisit_line
    assert "↩" not in plain_line
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_cmd_tasks_suffixes_revisit_rows -v`

Expected: FAIL — marker not rendered.

- [ ] **Step 3: Update `cmd_tasks`**

In `src/cli.py` around `cli.py:152-158`, append the marker:

```python
    for t in tasks:
        brief = t["brief"][:40] + "..." if len(t["brief"]) > 40 else t["brief"]
        agent = t.get("assigned_agent") or "-"
        status = t["status"]
        if t.get("block_kind"):
            status = f"{status}({t['block_kind']})"
        # Revisit marker — appended after the brief so row widths stay stable
        # for non-revisit rows. `↩` is a U+21A9 leftwards arrow with hook.
        if t.get("revisit_of_task_id"):
            brief = f"{brief}  ↩ {t['revisit_of_task_id']}"
        print(f"{t['id']:<12} {t['type']:<20} {status:<22} {agent:<18} {brief}")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_cli.py::test_cmd_tasks_suffixes_revisit_rows -v`

Expected: PASS.

- [ ] **Step 5: Run the full CLI test suite**

Run: `uv run pytest tests/test_cli.py -v`

Expected: PASS — existing `test_cmd_tasks_*` tests still pass (the new suffix only appears when `revisit_of_task_id` is truthy).

- [ ] **Step 6: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(cli): tasks list shows ↩ suffix on revisit rows"
```

---

## Task 10: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md` — the "Revisit (founder recovery)" section (search for the heading)

- [ ] **Step 1: Read the existing "Revisit (founder recovery)" section**

Run: `grep -n "Revisit (founder recovery)" CLAUDE.md`

Note the line number. The section documents the audit-log-only link; update it to mention the new column.

- [ ] **Step 2: Update the section**

Find the paragraph in `CLAUDE.md` that starts with:

> Architecture — the predecessor ↔ new-root link lives entirely in `audit_log`;
> no schema migration, no new columns.

Replace the ENTIRE paragraph (through the end of step 5 of the numbered list, where it says "5. enqueues the new root outside the lock") with:

```markdown
Architecture — the predecessor ↔ new-root link lives in two places: a
first-class nullable `tasks.revisit_of_task_id` column (queryable, indexed
via `idx_tasks_revisit_of`) AND an `audit_log` entry that carries the
richer payload (`flagged`, `cascade`, `founder_note`, `prior_status`).
The column is a sideways reference — `walk_ancestors` MUST NOT follow
it, or cascade-fail will re-poison revisits via
`_enqueue_parent_if_waiting`. Two helpers read the edge:
`Database.walk_revisit_chain(task_id)` walks backward to the original;
`Database.get_direct_revisits(task_id)` returns immediate revisits.

Inside `state.db_lock` the endpoint atomically:
1. walks ancestors via `walk_ancestors(task_id, max_hops=20)` to find the root
2. inserts the new root `TaskRecord` (same `brief` + `type`, fresh `id`,
   `revisit_of_task_id=predecessor.id`)
3. logs `revisit_of` on the new root (payload: `predecessor_root`, `flagged`,
   `cascade`, `prior_status`, `founder_note`)
4. logs `revisit_spawned` on the predecessor root
5. enqueues the new root outside the lock

Historical revisits (created before the column existed) are backfilled on
daemon startup from the `revisit_of` audit entries; the UPDATE is guarded by
`IS NULL` so restarts are idempotent.
```

- [ ] **Step 3: Add a note to the "Running Tests" / "Running the Daemon + CLI" section**

Find the section that documents `opc details` (search for `opc details TASK-001`). No change needed there — the behaviour change is invisible to the command shape. But verify the surrounding context doesn't claim "shows status and results only" or similar; if it does, expand it to mention the revisit header/chain/footer. Otherwise skip.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: document the first-class revisit_of_task_id link"
```

---

## Final Verification

- [ ] **Step 1: Run the full test suite**

Run: `uv run pytest tests/ -v`

Expected: PASS across the ~390 existing tests + ~15 new ones.

- [ ] **Step 2: Run the integration suite**

Run: `uv run pytest tests/integration/ -v -m integration`

Expected: PASS. The existing `test_revisit_roundtrip_creates_new_root_and_completes` incidentally exercises the new column (every revisit now writes it). If it fails, you've broken the revisit contract — do not ship.

- [ ] **Step 3: Manual smoke test**

If a local runtime is available:

```bash
scripts/daemon.sh start
uv run opc run --brief "test predecessor"
# Wait for it to fail or cancel it via opc cancel, then:
uv run opc revisit TASK-00X  # (the id that just failed/was cancelled)
uv run opc details TASK-00Y  # (the new root id from the revisit output)
```

Expected: `opc details` output begins with a `Revisit of: TASK-00X (predecessor: ...)` line and a `Chain:` line. `opc tasks` output has `↩ TASK-00X` on the new root's row.

---

## Rollout

Single PR, merge straight to main. No feature flag. No migration required for the user — the column is added and backfilled on next daemon start.

If any step's tests fail to pass on first attempt, fix the failure in place before moving on; do not stack unresolved failures across tasks. Each task's commit should leave `uv run pytest tests/` green.
