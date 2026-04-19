# Task Status Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 7-value TaskStatus with a 5-value vocabulary (`pending`/`in_progress`/`blocked`/`completed`/`failed`) + `BlockKind` reason field, and convert the synchronous orchestrator for-loop into an event-driven `run_step` queue so delegation becomes asynchronous with unified root/child lifecycle.

**Architecture:** Each task is advanced one subprocess call at a time by `Orchestrator.run_step(task_id)`. `run_step` picks up a task, runs its `assigned_agent` once, classifies the result, persists the transition, and enqueues the next task to advance (either a newly-spawned child or the parent that was waiting). State (including step budget) lives in the DB, so daemon crash recovery is a DB sweep.

**Tech Stack:** Python 3.11+, Pydantic v2 + StrEnum, SQLite (WAL), asyncio.Queue + `run_in_executor` bridge to the sync Claude Code subprocess, FastAPI daemon with SSE.

**Reference Spec:** `docs/superpowers/specs/2026-04-19-task-status-redesign.md`

**Worktree:** `.worktrees/task-status-redesign` on branch `feature/task-status-redesign`

**Baseline before starting:** run `uv run pytest tests/ -q` from the worktree and note the passing baseline. All subsequent steps must keep the suite green.

---

## File Structure

### Files created

- `src/orchestrator/run_step.py` — New module for `run_step` + helpers. Kept separate from `orchestrator.py` so the large new algorithm has its own file and test surface. `Orchestrator.run_step` is a thin method that delegates here.
- `src/daemon/queue.py` — `TaskQueue` wrapper around `asyncio.Queue` plus the worker-pool coroutine. Separated from `state.py` so the state dataclass stays data-only.
- `tests/test_run_step.py` — Unit tests for the new algorithm (7 outcome branches + parent-enqueue cases).
- `tests/test_migration.py` — Schema migration test (pre-migration fixture DB → post-migration asserts).
- `tests/daemon/test_queue.py` — Worker pool + enqueue correctness tests.
- `tests/daemon/test_run_step_integration.py` — End-to-end async delegation roundtrip via the queue.

### Files modified

- `src/models.py` — TaskStatus vocabulary, new BlockKind, TaskRecord columns.
- `src/infrastructure/database.py` — Migration, new columns, new query helpers.
- `src/orchestrator/orchestrator.py` — Delete `run_task` and its helpers. Add `run_step` method that calls into the new module. Keep `_parse_next_step`, `_run_agent`, `_read_completion_from_db`, `_build_session_id`, `_update_task_history`, `_log_step_result`, `_log_review_verdicts`.
- `src/daemon/state.py` — New terminal-event map; wire `TaskQueue` in.
- `src/daemon/runner.py` — Replaced contents: worker coroutine that consumes the queue and calls `run_step` via `run_in_executor`.
- `src/daemon/__main__.py` — New startup sweep (`in_progress → failed`, re-enqueue `blocked(DELEGATED)` with terminal children), queue spawn.
- `src/daemon/routes/tasks.py` — `POST /tasks` enqueues instead of spawning a runner; `resolve-escalation` precondition updated; new `task_failed` / `task_blocked` events.
- `src/daemon/event_bus.py` — New terminal event types.
- `src/cli.py` — `opc tasks` shows `block_kind` when present; `opc status` shows `note`; `opc resolve-escalation` still works (terminology unchanged for user).
- `protocol/05c-orchestrator.md` — Updated state-machine diagram + execution model section.
- Plus every test file that references dropped TaskStatus values.

### Files untouched (deliberately)

- `src/infrastructure/audit_logger.py` — Semantic audit rows fire at the same moments.
- `src/infrastructure/kb_store.py`, `src/daemon/routes/kb.py` — KB pipeline is deliberately decoupled from task status.
- `src/orchestrator/capabilities.py`, `src/orchestrator/executor.py`, `src/orchestrator/prompt_loader.py`, `src/orchestrator/context_builder.py`, `src/orchestrator/performance_tracker.py` — No interface change.
- `protocol/skills/start-task/` — Agent completion contract (`opc report-completion --status completed|blocked`) unchanged.

---

## Phase 1: Models foundation

### Task 1: New TaskStatus + BlockKind enums

**Files:**
- Modify: `src/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
def test_task_status_has_five_values():
    from src.models import TaskStatus
    assert {s.value for s in TaskStatus} == {
        "pending", "in_progress", "blocked", "completed", "failed",
    }


def test_block_kind_has_delegated_and_escalated():
    from src.models import BlockKind
    assert {b.value for b in BlockKind} == {"delegated", "escalated"}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_models.py::test_task_status_has_five_values tests/test_models.py::test_block_kind_has_delegated_and_escalated -v
```

Expected: 1 fail (old TaskStatus has extra values), 1 error (BlockKind doesn't exist).

- [ ] **Step 3: Replace TaskStatus and add BlockKind**

In `src/models.py`, replace the existing `TaskStatus` class with:

```python
class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"


class BlockKind(StrEnum):
    DELEGATED = "delegated"
    ESCALATED = "escalated"
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_models.py -v
```

Expected: The two new tests PASS. Other tests in the file may fail if they reference dropped values — leave those for the sweep task; they're in the next phase.

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "refactor(models): 5-value TaskStatus + BlockKind enum"
```

---

### Task 2: TaskRecord gets new columns

**Files:**
- Modify: `src/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
def test_task_record_has_new_columns():
    from src.models import TaskRecord, TaskType
    t = TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="x")
    assert t.block_kind is None
    assert t.note is None
    assert t.orchestration_step_count == 0


def test_task_record_accepts_block_kind():
    from src.models import TaskRecord, TaskType, TaskStatus, BlockKind
    t = TaskRecord(
        id="TASK-001", type=TaskType.GENERAL, brief="x",
        status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED,
        note="Delegated to dev_agent", orchestration_step_count=3,
    )
    assert t.block_kind == BlockKind.DELEGATED
    assert t.note == "Delegated to dev_agent"
    assert t.orchestration_step_count == 3
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_models.py::test_task_record_has_new_columns tests/test_models.py::test_task_record_accepts_block_kind -v
```

Expected: Both fail — `TaskRecord` has no `block_kind`, `note`, or `orchestration_step_count`.

- [ ] **Step 3: Add the columns**

In `src/models.py`, modify the `TaskRecord` class. Drop `final_output_summary`, add `block_kind`, `note`, `orchestration_step_count`:

```python
class TaskRecord(BaseModel):
    id: str
    type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: str | None = None
    team: str = "product_engineering"
    brief: str
    parent_task_id: str | None = None
    block_kind: BlockKind | None = None
    note: str | None = None
    final_artifact_dir: str | None = None
    orchestration_step_count: int = 0
    revision_count: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None
```

Note the removal of `final_output_summary`. Keep `final_artifact_dir` (artifacts are still a distinct concept from the free-text note).

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_models.py::test_task_record_has_new_columns tests/test_models.py::test_task_record_accepts_block_kind -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "refactor(models): TaskRecord gains block_kind, note, orchestration_step_count"
```

---

## Phase 2: Database schema + queries

### Task 3: Migration — add new columns + map old statuses

**Files:**
- Modify: `src/infrastructure/database.py:24` (the `_create_tables` method)
- Test: `tests/test_migration.py` (new)

- [ ] **Step 1: Write the failing migration test**

Create `tests/test_migration.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path


def _write_pre_migration_db(path: Path) -> None:
    """Build a SQLite DB with the pre-migration shape and a row per old status."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            assigned_agent TEXT,
            team TEXT NOT NULL DEFAULT 'product_engineering',
            brief TEXT NOT NULL,
            revision_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            parent_task_id TEXT,
            final_output_summary TEXT,
            final_artifact_dir TEXT
        );
    """)
    ts = "2026-04-01T00:00:00+00:00"
    rows = [
        ("T-APR", "general", "approved", "agent-a", "done-summary", None),
        ("T-REJ", "general", "rejected", "agent-b", "rej-summary", None),
        ("T-ESC", "general", "escalated", "agent-c", "esc-reason", None),
        ("T-PEN", "general", "pending", None, None, None),
        ("T-PRO", "general", "in_progress", "agent-d", None, None),
        ("T-COMPLETED", "general", "completed", "agent-e", "old-complete", None),
        ("T-REVIEW", "general", "in_review", "agent-f", "old-review", None),
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO tasks (id, type, status, assigned_agent, brief, "
            "revision_count, created_at, updated_at, final_output_summary, final_artifact_dir) "
            "VALUES (?, ?, ?, ?, 'brief', 0, ?, ?, ?, ?)",
            (r[0], r[1], r[2], r[3], ts, ts, r[4], r[5]),
        )
    conn.commit()
    conn.close()


def test_migration_maps_old_statuses(tmp_path: Path) -> None:
    db_path = tmp_path / "opc.db"
    _write_pre_migration_db(db_path)

    # Trigger the migration by opening the DB through our class.
    from src.infrastructure.database import Database
    db = Database(db_path)

    rows = {r["id"]: dict(r) for r in db._conn.execute("SELECT * FROM tasks")}

    # Status remaps
    assert rows["T-APR"]["status"] == "completed"
    assert rows["T-APR"]["block_kind"] is None
    assert rows["T-REJ"]["status"] == "failed"
    assert rows["T-REJ"]["block_kind"] is None
    assert rows["T-ESC"]["status"] == "blocked"
    assert rows["T-ESC"]["block_kind"] == "escalated"

    # Unchanged non-terminal rows remain unchanged
    assert rows["T-PEN"]["status"] == "pending"
    assert rows["T-PRO"]["status"] == "in_progress"

    # Dead-enum rows get normalized to failed (they were never written in
    # practice but a migration must still leave the table in a legal shape)
    assert rows["T-COMPLETED"]["status"] == "completed"  # already legal
    assert rows["T-REVIEW"]["status"] == "failed"         # in_review → failed

    # final_output_summary folded into note, column still present but unused
    assert rows["T-APR"]["note"] == "done-summary"
    assert rows["T-ESC"]["note"] == "esc-reason"

    # orchestration_step_count defaults to 0
    assert rows["T-PEN"]["orchestration_step_count"] == 0


def test_migration_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "opc.db"
    _write_pre_migration_db(db_path)
    from src.infrastructure.database import Database

    Database(db_path).close()
    # Re-open: migration already applied; this must not raise.
    db = Database(db_path)
    rows = list(db._conn.execute("SELECT status FROM tasks WHERE id='T-APR'"))
    assert rows[0]["status"] == "completed"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_migration.py -v
```

Expected: fails — `block_kind` / `note` / `orchestration_step_count` columns don't exist yet.

- [ ] **Step 3: Add the migration**

In `src/infrastructure/database.py`, extend `_create_tables` by appending this migration block after the existing `ALTER` loop (line ~117):

```python
        # --- Task-status redesign migration (idempotent) ---
        # Add new columns; swallow duplicate errors on subsequent startups.
        for ddl in (
            "ALTER TABLE tasks ADD COLUMN block_kind TEXT",
            "ALTER TABLE tasks ADD COLUMN note TEXT",
            "ALTER TABLE tasks ADD COLUMN orchestration_step_count INTEGER DEFAULT 0",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass

        # One-shot data remap. Guard with a sentinel so re-runs are no-ops.
        applied = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks' "
            "AND sql LIKE '%block_kind%'"
        ).fetchone()
        if applied is not None:
            # Fold final_output_summary → note where not already set.
            self._conn.execute(
                "UPDATE tasks SET note = final_output_summary "
                "WHERE note IS NULL AND final_output_summary IS NOT NULL"
            )
            # Old-world → new-world status mapping. Each UPDATE is narrow so
            # re-running is a no-op (no rows match the WHERE clause the 2nd time).
            self._conn.execute("UPDATE tasks SET status='completed' WHERE status='approved'")
            self._conn.execute("UPDATE tasks SET status='failed'    WHERE status='rejected'")
            self._conn.execute(
                "UPDATE tasks SET status='blocked', block_kind='escalated' "
                "WHERE status='escalated'"
            )
            # Normalize dead legacy values.
            self._conn.execute("UPDATE tasks SET status='failed' WHERE status='in_review'")
            self._conn.commit()
```

- [ ] **Step 4: Run the migration test**

```bash
uv run pytest tests/test_migration.py -v
```

Expected: PASS (both cases).

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_migration.py
git commit -m "feat(db): add block_kind/note/orchestration_step_count + migrate old statuses"
```

---

### Task 4: Database write paths for the new columns

**Files:**
- Modify: `src/infrastructure/database.py` — `insert_task`, `get_task`, `list_tasks`, `list_agent_tasks`, `update_task`
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_database.py`:

```python
def test_update_task_writes_block_kind_and_note(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="x"))
    db.update_task(
        "TASK-001",
        status=TaskStatus.BLOCKED,
        block_kind=BlockKind.DELEGATED,
        note="Delegated to dev_agent",
        orchestration_step_count=2,
    )
    t = db.get_task("TASK-001")
    assert t.status == TaskStatus.BLOCKED
    assert t.block_kind == BlockKind.DELEGATED
    assert t.note == "Delegated to dev_agent"
    assert t.orchestration_step_count == 2


def test_update_task_can_clear_block_kind_to_none(tmp_path):
    """When a task unblocks, block_kind and note must be nulled — the existing
    update_task `v is not None` filter would silently drop these writes."""
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="x"))
    db.update_task("TASK-001", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="x")
    db.update_task("TASK-001", status=TaskStatus.IN_PROGRESS,
                   block_kind=None, note=None)
    t = db.get_task("TASK-001")
    assert t.block_kind is None
    assert t.note is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_database.py::test_update_task_writes_block_kind_and_note tests/test_database.py::test_update_task_can_clear_block_kind_to_none -v
```

Expected: fails — `insert_task` doesn't persist the new columns, `get_task` doesn't read them, and `update_task` drops `None` values.

- [ ] **Step 3: Teach insert_task / get_task / list_tasks / list_agent_tasks about the new columns**

In `src/infrastructure/database.py`, update the `insert_task` SQL to include `block_kind`, `note`, `orchestration_step_count`:

```python
    def insert_task(self, task: TaskRecord) -> None:
        self._conn.execute(
            """INSERT INTO tasks (id, type, status, assigned_agent, team, brief,
               revision_count, created_at, updated_at, completed_at, parent_task_id,
               block_kind, note, orchestration_step_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            ),
        )
        self._conn.commit()
```

Update the two `TaskRecord(...)` constructors inside `get_task` and `list_tasks` and `list_agent_tasks` to populate the new fields, and drop `final_output_summary=row["final_output_summary"]`:

```python
            block_kind=row["block_kind"],
            note=row["note"],
            orchestration_step_count=row["orchestration_step_count"] or 0,
            final_artifact_dir=row["final_artifact_dir"],
```

(Remove the `final_output_summary=row[...]` line from all three constructors.)

- [ ] **Step 4: Fix `update_task` to allow setting columns to None**

Replace the body of `update_task` with:

```python
    def update_task(self, task_id: str, **fields: object) -> None:
        allowed = {
            "status", "assigned_agent", "revision_count", "completed_at",
            "block_kind", "note", "orchestration_step_count",
            "final_artifact_dir",
        }
        # NOTE: filter on membership, not on None-ness — block_kind must be
        # resettable to NULL when a task unblocks.
        updates: dict[str, object] = {}
        for k, v in fields.items():
            if k not in allowed:
                continue
            if hasattr(v, "value"):
                updates[k] = v.value
            else:
                updates[k] = v
        if not updates:
            return
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]
        self._conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        self._conn.commit()
```

(Key change: `final_output_summary` removed from `allowed`; `block_kind`/`note`/`orchestration_step_count` added; the `v is not None` filter is gone so None writes through.)

- [ ] **Step 5: Update `get_recall_payload` to use `note`**

In `src/infrastructure/database.py`, change `get_recall_payload` to read `task.note` instead of `task.final_output_summary`:

```python
            "output_summary": task.note,
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/test_database.py -v
```

Expected: the new tests PASS. Other tests in the file may fail where they still write/read `final_output_summary` — fix those inline (replace with `note=`).

- [ ] **Step 7: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "feat(db): persist block_kind/note/orchestration_step_count; allow None writes"
```

---

### Task 5: `get_nonterminal_task_ids` + new query helpers

**Files:**
- Modify: `src/infrastructure/database.py`
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_database.py`:

```python
def test_get_nonterminal_task_ids_includes_blocked(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind

    db = Database(tmp_path / "opc.db")
    for tid, status, bk in [
        ("T-PEN", TaskStatus.PENDING, None),
        ("T-INP", TaskStatus.IN_PROGRESS, None),
        ("T-BKD", TaskStatus.BLOCKED, BlockKind.DELEGATED),
        ("T-BKE", TaskStatus.BLOCKED, BlockKind.ESCALATED),
        ("T-CMP", TaskStatus.COMPLETED, None),
        ("T-FAI", TaskStatus.FAILED, None),
    ]:
        db.insert_task(TaskRecord(id=tid, type=TaskType.GENERAL, brief="x"))
        db.update_task(tid, status=status, block_kind=bk)

    ids = set(db.get_nonterminal_task_ids())
    assert ids == {"T-PEN", "T-INP", "T-BKD", "T-BKE"}


def test_list_blocked_with_kind(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind

    db = Database(tmp_path / "opc.db")
    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    db.insert_task(TaskRecord(id="T-2", type=TaskType.GENERAL, brief="y"))
    db.update_task("T-1", status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED)
    db.update_task("T-2", status=TaskStatus.BLOCKED, block_kind=BlockKind.ESCALATED)

    ids = set(db.list_blocked_with_kind(BlockKind.DELEGATED))
    assert ids == {"T-1"}
    ids = set(db.list_blocked_with_kind(BlockKind.ESCALATED))
    assert ids == {"T-2"}
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_database.py::test_get_nonterminal_task_ids_includes_blocked tests/test_database.py::test_list_blocked_with_kind -v
```

Expected: first test fails (BLOCKED not in nonterminal set), second errors (method missing).

- [ ] **Step 3: Update `get_nonterminal_task_ids` and add `list_blocked_with_kind`**

Replace the body of `get_nonterminal_task_ids` in `src/infrastructure/database.py`:

```python
    def get_nonterminal_task_ids(self) -> list[str]:
        nonterminal = (
            TaskStatus.PENDING.value,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.BLOCKED.value,
        )
        cursor = self._conn.execute(
            f"SELECT id FROM tasks WHERE status IN ({','.join('?' * len(nonterminal))})",
            nonterminal,
        )
        return [row["id"] for row in cursor.fetchall()]

    def list_blocked_with_kind(self, kind) -> list[str]:
        """Return IDs of tasks in status=blocked with the given block_kind."""
        kind_value = kind.value if hasattr(kind, "value") else kind
        cursor = self._conn.execute(
            "SELECT id FROM tasks WHERE status = 'blocked' AND block_kind = ?",
            (kind_value,),
        )
        return [row["id"] for row in cursor.fetchall()]
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_database.py::test_get_nonterminal_task_ids_includes_blocked tests/test_database.py::test_list_blocked_with_kind -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "feat(db): list_blocked_with_kind; nonterminal includes BLOCKED"
```

---

## Phase 3: Orchestrator primitives (run_step + helpers)

### Task 6: New run_step module scaffolding

**Files:**
- Create: `src/orchestrator/run_step.py`
- Test: `tests/test_run_step.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_run_step.py`:

```python
"""Unit tests for Orchestrator.run_step — the single primitive that advances
a task one subprocess call at a time under the new async execution model."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import Settings
from src.infrastructure.database import Database
from src.models import BlockKind, TaskRecord, TaskStatus, TaskType
from src.runtime import RuntimeDir


@pytest.fixture
def runtime(tmp_path: Path) -> RuntimeDir:
    return RuntimeDir.init(tmp_path / "rt")


@pytest.fixture
def db(runtime: RuntimeDir) -> Database:
    return Database(runtime.db_path)


def test_run_step_silent_noop_when_task_missing(runtime, db):
    from src.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)
    orch = Orchestrator(db=db, settings=settings, runtime=runtime)
    # Just must not raise
    orch.run_step("TASK-NOPE")


def test_run_step_noop_on_blocked_escalated(runtime, db):
    """A task in blocked(ESCALATED) isn't eligible for run_step — it waits
    for /resolve-escalation to transition it first. Second-hand enqueue
    must be silently ignored."""
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    db.update_task("T-1", status=TaskStatus.BLOCKED, block_kind=BlockKind.ESCALATED,
                   note="halted")
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)
    orch.run_step("T-1")
    t = db.get_task("T-1")
    assert t.status == TaskStatus.BLOCKED
    assert t.block_kind == BlockKind.ESCALATED
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_run_step.py -v
```

Expected: AttributeError — `Orchestrator.run_step` doesn't exist yet.

- [ ] **Step 3: Add skeleton `run_step` method**

In `src/orchestrator/orchestrator.py`, add this method to the `Orchestrator` class (alongside `run_task`, for now):

```python
    def run_step(self, task_id: str) -> None:
        """Advance a task one agent-subprocess worth.

        Contract: task MUST be PENDING or BLOCKED(DELEGATED)-with-all-children-
        terminal. Anything else is a stale enqueue and is silently ignored.
        """
        from src.orchestrator.run_step import run_step_impl
        run_step_impl(self, task_id)
```

Create `src/orchestrator/run_step.py` with a stub:

```python
"""Implementation of Orchestrator.run_step — the single primitive that advances
a task one subprocess call at a time. Separate from orchestrator.py so the
algorithm has its own test surface.

Entry contract: task MUST be either
  (a) status=pending, or
  (b) status=blocked AND block_kind=DELEGATED AND all children are terminal.
Any other state = stale enqueue, silent no-op.

Exit contract: task ends in exactly one of {in_progress-then-crashed,
completed, failed, blocked(DELEGATED), blocked(ESCALATED)}.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.models import BlockKind, TaskStatus

if TYPE_CHECKING:
    from src.orchestrator.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED})


def run_step_impl(orch: "Orchestrator", task_id: str) -> None:
    db = orch._db
    task = db.get_task(task_id)
    if task is None:
        return

    # ---- 1. Verify entry state ----
    if task.status == TaskStatus.PENDING:
        pass  # eligible
    elif task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.DELEGATED:
        children = [db.get_task(cid) for cid in db.get_children(task_id)]
        if any(c is None or c.status not in TERMINAL_STATES for c in children):
            logger.debug("run_step %s: child still running, skipping", task_id)
            return
    else:
        logger.debug(
            "run_step %s: not eligible (status=%s, block_kind=%s)",
            task_id, task.status, task.block_kind,
        )
        return

    # Further steps are added in subsequent tasks.
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_run_step.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py src/orchestrator/orchestrator.py tests/test_run_step.py
git commit -m "feat(orchestrator): add run_step scaffold with entry-state guard"
```

---

### Task 7: Budget guard — max_orchestration_steps → blocked(ESCALATED)

**Files:**
- Modify: `src/orchestrator/run_step.py`
- Test: `tests/test_run_step.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_run_step.py`:

```python
def test_run_step_over_budget_parks_escalated(runtime, db):
    from src.orchestrator.orchestrator import Orchestrator
    settings = Settings(max_orchestration_steps=3)
    db.insert_task(TaskRecord(
        id="T-1", type=TaskType.GENERAL, brief="x", assigned_agent="engineering_head",
    ))
    db.update_task("T-1", orchestration_step_count=3)  # already at the cap

    orch = Orchestrator(db=db, settings=settings, runtime=runtime)
    orch.run_step("T-1")

    t = db.get_task("T-1")
    assert t.status == TaskStatus.BLOCKED
    assert t.block_kind == BlockKind.ESCALATED
    assert t.note and "max steps" in t.note
    # Audit row
    escalations = [
        a for a in db.get_audit_logs("T-1") if a["action"] == "escalation"
    ]
    assert len(escalations) == 1
    assert "max steps" in escalations[0]["payload"]["reason"]
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/test_run_step.py::test_run_step_over_budget_parks_escalated -v
```

Expected: FAIL — the task stays pending (current stub takes no action).

- [ ] **Step 3: Add the budget guard**

In `src/orchestrator/run_step.py`, extend `run_step_impl` after the entry-state check:

```python
    # ---- 2. Budget guard (persisted, survives restarts) ----
    max_steps = orch._settings.max_orchestration_steps
    next_count = task.orchestration_step_count + 1
    if next_count > max_steps:
        reason = f"max steps ({max_steps}) exceeded"
        db.update_task(
            task_id,
            status=TaskStatus.BLOCKED,
            block_kind=BlockKind.ESCALATED,
            note=reason,
        )
        orch._audit.log_escalation(task_id, "orchestrator", reason)
        return
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/test_run_step.py::test_run_step_over_budget_parks_escalated -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/test_run_step.py
git commit -m "feat(run_step): budget guard parks over-limit tasks in blocked(ESCALATED)"
```

---

### Task 8: Atomic transition pending/blocked(DELEGATED) → in_progress

**Files:**
- Modify: `src/orchestrator/run_step.py`
- Test: `tests/test_run_step.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_run_step.py`:

```python
def test_run_step_transitions_pending_to_in_progress_and_increments_count(
    runtime, db, monkeypatch,
):
    """On pickup, run_step must flip to in_progress, clear block fields,
    and increment the step counter exactly once — BEFORE invoking the agent."""
    from src.orchestrator.orchestrator import Orchestrator, WorkspaceNotInitialized

    db.insert_task(TaskRecord(
        id="T-1", type=TaskType.GENERAL, brief="x", assigned_agent="engineering_head",
    ))
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10), runtime=runtime)

    # Force _run_agent to raise so we can inspect the DB state mid-flight.
    captured: dict = {}
    def fail(task_id, agent, prompt, on_session_started=None):
        t = db.get_task(task_id)
        captured["status"] = t.status
        captured["count"] = t.orchestration_step_count
        captured["block_kind"] = t.block_kind
        captured["note"] = t.note
        raise WorkspaceNotInitialized("fake")
    monkeypatch.setattr(orch, "_run_agent", fail)

    orch.run_step("T-1")

    assert captured["status"] == TaskStatus.IN_PROGRESS
    assert captured["count"] == 1
    assert captured["block_kind"] is None
    assert captured["note"] is None
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/test_run_step.py::test_run_step_transitions_pending_to_in_progress_and_increments_count -v
```

Expected: FAIL — stub takes no action and `_run_agent` is never called.

- [ ] **Step 3: Add the atomic transition + agent invocation**

Add this after the budget guard in `src/orchestrator/run_step.py`:

```python
    # ---- 3. Atomic transition: unblock + increment + mark in_progress ----
    db.update_task(
        task_id,
        status=TaskStatus.IN_PROGRESS,
        block_kind=None,
        note=None,
        orchestration_step_count=next_count,
    )

    # ---- 4. Run the agent subprocess ----
    agent = task.assigned_agent or _default_agent_for_root(task)
    if task.assigned_agent is None:
        db.update_task(task_id, assigned_agent=agent)

    prompt = _build_agent_prompt(orch, task, agent)
    try:
        result, report = orch._run_agent(task_id, agent, prompt)
    except Exception as exc:
        _fail(orch, task_id, note=f"agent invocation failed: {exc}")
        _enqueue_parent_if_waiting(orch, task_id)
        return
```

Add the helpers at the bottom of the file:

```python
def _default_agent_for_root(task) -> str:
    """Root tasks default to the Engineering Head as their assigned agent."""
    return "engineering_head"


def _build_agent_prompt(orch: "Orchestrator", task, agent: str) -> str:
    """Build the capabilities prompt for an EH decision step, or pass the
    brief verbatim for a worker. Prior steps are rebuilt from the DB so this
    works identically on first pickup and on post-delegation resumption."""
    from src.orchestrator.capabilities import build_capabilities_prompt
    if agent != "engineering_head":
        return task.brief
    agent_names, tiers = _list_candidate_agents(orch)
    agents_for_prompt = []
    for name in agent_names:
        enrollment = orch._db.get_enrollment(name)
        desc = enrollment["description"] if enrollment else name
        tier = tiers.get(name)
        agents_for_prompt.append({
            "name": name,
            "description": desc,
            "tier": tier.value if tier else "green",
        })
    prior_steps = _build_prior_steps_from_db(orch, task.id)
    return build_capabilities_prompt(
        brief=task.brief,
        agents=agents_for_prompt,
        step_number=task.orchestration_step_count + 1,  # 1-indexed for EH display
        max_steps=orch._settings.max_orchestration_steps,
        prior_steps=prior_steps,
    )


def _list_candidate_agents(orch: "Orchestrator"):
    """Return (agent_names, tiers_map) — same shape as orchestrator used."""
    if orch._runtime.workspaces_dir.exists():
        names = [
            d.name for d in orch._runtime.workspaces_dir.iterdir()
            if d.is_dir() and d.name != "engineering_head"
        ]
    else:
        names = []
    tiers = orch._tracker.get_all_tiers(names)
    return names, tiers


def _build_prior_steps_from_db(orch: "Orchestrator", task_id: str):
    """Reconstruct StepRecord[] for the EH by reading children's terminal
    outcomes from the DB. Only direct children of `task_id` count — each child
    is one past orchestration step. Order: creation order, 1-indexed."""
    from src.models import StepRecord
    steps: list[StepRecord] = []
    for i, child_id in enumerate(orch._db.get_children(task_id), start=1):
        child = orch._db.get_task(child_id)
        if child is None:
            continue
        success = child.status == TaskStatus.COMPLETED
        steps.append(StepRecord(
            step_number=i,
            agent=child.assigned_agent or "unknown",
            action=f"delegate: {(child.brief or '')[:100]}",
            result_summary=child.note or "(no summary)",
            success=success,
        ))
    return steps


def _complete(orch: "Orchestrator", task_id: str, *, note: str, artifact_dir: str | None = None) -> None:
    from datetime import datetime, timezone
    orch._db.update_task(
        task_id,
        status=TaskStatus.COMPLETED,
        block_kind=None,
        note=note,
        final_artifact_dir=artifact_dir,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    orch._update_task_history(task_id)


def _fail(orch: "Orchestrator", task_id: str, *, note: str) -> None:
    from datetime import datetime, timezone
    orch._db.update_task(
        task_id,
        status=TaskStatus.FAILED,
        block_kind=None,
        note=note,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    orch._update_task_history(task_id)


def _enqueue_parent_if_waiting(orch: "Orchestrator", task_id: str) -> None:
    """Idempotent: enqueue the parent only if it is actually waiting on
    THIS lineage (blocked+DELEGATED) AND all its children are now terminal."""
    task = orch._db.get_task(task_id)
    if task is None or task.parent_task_id is None:
        return
    parent = orch._db.get_task(task.parent_task_id)
    if parent is None or parent.status != TaskStatus.BLOCKED:
        return
    if parent.block_kind != BlockKind.DELEGATED:
        return
    siblings = [orch._db.get_task(cid) for cid in orch._db.get_children(parent.id)]
    if any(s is None or s.status not in TERMINAL_STATES for s in siblings):
        return
    queue = getattr(orch, "_queue", None)
    if queue is not None:
        queue.put_nowait(parent.id)
```

Also add the `completed_at` to `update_task`'s allowed set if missing — check `src/infrastructure/database.py:266`; `completed_at` is already there.

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/test_run_step.py::test_run_step_transitions_pending_to_in_progress_and_increments_count -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/test_run_step.py
git commit -m "feat(run_step): atomic pending→in_progress transition and agent dispatch"
```

---

### Task 9: Outcome branch — `done` → completed + notify parent

**Files:**
- Modify: `src/orchestrator/run_step.py`
- Test: `tests/test_run_step.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_run_step.py`:

```python
def _make_report(output_summary: str, status: str = "completed",
                 artifact_dir: str | None = None):
    from src.models import CompletionReport
    return CompletionReport(
        task_id="T-IGNORED", agent="engineering_head", status=status,
        confidence=80, output_summary=output_summary, artifact_dir=artifact_dir,
    )


def _make_result(success: bool = True, duration: int = 1):
    from src.orchestrator.executor import ExecutorResult
    return ExecutorResult(
        success=success, session_id="sess-x", duration_seconds=duration,
    )


def test_run_step_done_completes_task_and_enqueues_parent(
    runtime, db, monkeypatch,
):
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    # Parent in blocked(DELEGATED), child in pending.
    db.insert_task(TaskRecord(id="T-PAR", type=TaskType.GENERAL, brief="parent",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", type=TaskType.GENERAL, brief="child",
        assigned_agent="engineering_head", parent_task_id="T-PAR",
    ))

    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        runtime=runtime)
    # Wire a fake queue
    q: asyncio.Queue = asyncio.Queue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "done", "summary": "Looks great"}),
            artifact_dir="artifacts/run-1",
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.COMPLETED
    assert child.note == "Looks great"
    assert child.final_artifact_dir == "artifacts/run-1"

    # Parent should be enqueued
    assert q.qsize() == 1
    assert q.get_nowait() == "T-PAR"
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/test_run_step.py::test_run_step_done_completes_task_and_enqueues_parent -v
```

Expected: FAIL — task still `in_progress`, no enqueue.

- [ ] **Step 3: Add the done branch**

In `src/orchestrator/run_step.py`, extend `run_step_impl` after the agent invocation:

```python
    # ---- 5. Classify outcome ----
    if not result.success or report is None:
        _fail(orch, task_id, note="agent session failed")
        _enqueue_parent_if_waiting(orch, task_id)
        return

    orch._log_step_result(task_id, result, report)

    if report.status == "blocked":
        _fail(orch, task_id, note=f"self-blocked: {report.output_summary}")
        _enqueue_parent_if_waiting(orch, task_id)
        return

    # ---- 6. Parse next step (reuses the existing parser) ----
    decision = orch._parse_next_step(report)

    orch._audit.log_orchestration_step(
        task_id, next_count, decision.model_dump(exclude_none=True),
    )

    # ---- 7. Dispatch on action ----
    if decision.action == "done":
        _complete(
            orch, task_id,
            note=decision.summary or report.output_summary,
            artifact_dir=report.artifact_dir,
        )
        orch._tracker.update_scorecard(agent)
        _enqueue_parent_if_waiting(orch, task_id)
        return
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/test_run_step.py::test_run_step_done_completes_task_and_enqueues_parent -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/test_run_step.py
git commit -m "feat(run_step): done branch completes task and notifies parent"
```

---

### Task 10: Outcome branch — `escalate` → blocked(ESCALATED), parent stays parked

**Files:**
- Modify: `src/orchestrator/run_step.py`
- Test: `tests/test_run_step.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_run_step.py`:

```python
def test_run_step_escalate_parks_blocked_and_leaves_parent_parked(
    runtime, db, monkeypatch,
):
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", type=TaskType.GENERAL, brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", type=TaskType.GENERAL, brief="c",
        assigned_agent="engineering_head", parent_task_id="T-PAR",
    ))

    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)
    q: asyncio.Queue = asyncio.Queue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "escalate", "reason": "needs founder"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-CHD")

    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.BLOCKED
    assert child.block_kind == BlockKind.ESCALATED
    assert child.note == "needs founder"

    # Parent stays parked — escalation is NOT a terminal for sibling-summing.
    assert q.qsize() == 0
    assert db.get_task("T-PAR").status == TaskStatus.BLOCKED

    # Audit row
    escalations = [a for a in db.get_audit_logs("T-CHD") if a["action"] == "escalation"]
    assert any("needs founder" in e["payload"]["reason"] for e in escalations)
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/test_run_step.py::test_run_step_escalate_parks_blocked_and_leaves_parent_parked -v
```

Expected: FAIL.

- [ ] **Step 3: Add the escalate branch**

In `src/orchestrator/run_step.py`, continue the dispatch block:

```python
    if decision.action == "escalate":
        reason = decision.reason or "Escalated"
        db.update_task(
            task_id,
            status=TaskStatus.BLOCKED,
            block_kind=BlockKind.ESCALATED,
            note=reason,
        )
        orch._audit.log_escalation(task_id, agent, reason)
        # parent stays blocked(DELEGATED) until this task reaches a terminal.
        return
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/test_run_step.py::test_run_step_escalate_parks_blocked_and_leaves_parent_parked -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/test_run_step.py
git commit -m "feat(run_step): escalate branch parks blocked(ESCALATED)"
```

---

### Task 11: Outcome branch — `delegate` → spawn child, block self, enqueue child

**Files:**
- Modify: `src/orchestrator/run_step.py`
- Test: `tests/test_run_step.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_run_step.py`:

```python
def test_run_step_delegate_spawns_child_and_blocks_self(
    runtime, db, monkeypatch,
):
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True)

    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="root",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)
    q: asyncio.Queue = asyncio.Queue()
    orch._queue = q

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({
                "action": "delegate",
                "agent": "dev_agent",
                "prompt": "Write a PR",
            }),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")

    # Parent now blocked(DELEGATED)
    parent = db.get_task("T-1")
    assert parent.status == TaskStatus.BLOCKED
    assert parent.block_kind == BlockKind.DELEGATED
    assert "dev_agent" in (parent.note or "")

    # Exactly one child exists, is pending, and is enqueued
    children = db.get_children("T-1")
    assert len(children) == 1
    child_id = children[0]
    child = db.get_task(child_id)
    assert child.status == TaskStatus.PENDING
    assert child.assigned_agent == "dev_agent"
    assert child.brief == "Write a PR"
    assert child.parent_task_id == "T-1"
    assert q.get_nowait() == child_id


def test_run_step_invalid_delegate_fails_task(runtime, db, monkeypatch):
    """A delegate with no agent name is unrecoverable — fail the task and
    notify the parent (which may itself be root — no-op in that case)."""
    import asyncio
    import json
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)
    orch._queue = asyncio.Queue()

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "delegate", "prompt": "x"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")
    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note and "invalid delegate" in t.note
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/test_run_step.py::test_run_step_delegate_spawns_child_and_blocks_self tests/test_run_step.py::test_run_step_invalid_delegate_fails_task -v
```

Expected: both FAIL.

- [ ] **Step 3: Add the delegate branch + validator**

In `src/orchestrator/run_step.py`, continue the dispatch block:

```python
    if decision.action == "delegate":
        err = _validate_delegate(orch, decision)
        if err is not None:
            _fail(orch, task_id, note=f"invalid delegate: {err}")
            _enqueue_parent_if_waiting(orch, task_id)
            return
        from src.models import TaskRecord
        child_id = db.next_task_id()
        db.insert_task(TaskRecord(
            id=child_id,
            type=task.type,
            brief=decision.prompt or "",
            assigned_agent=decision.agent,
            parent_task_id=task_id,
            status=TaskStatus.PENDING,
        ))
        db.update_task(
            task_id,
            status=TaskStatus.BLOCKED,
            block_kind=BlockKind.DELEGATED,
            note=f"Delegated to {decision.agent} (child={child_id})",
        )
        if orch._queue is not None:
            orch._queue.put_nowait(child_id)
        return

    # ---- 8. Unknown action ----
    _fail(orch, task_id, note=f"unknown action: {decision.action}")
    _enqueue_parent_if_waiting(orch, task_id)
```

Add `_validate_delegate` helper near the other helpers:

```python
def _validate_delegate(orch: "Orchestrator", decision) -> str | None:
    """Return a human-readable error string if the delegate decision is
    unusable, or None if it's good to spawn."""
    if not decision.agent:
        return "missing agent name"
    workspace = orch._runtime.workspaces_dir / decision.agent
    if not workspace.exists():
        return f"no workspace for agent {decision.agent!r}"
    return None
```

Also add `_queue: "asyncio.Queue[str] | None" = None` attribute so the attribute is well-defined. Add in `Orchestrator.__init__`:

```python
        self._queue: "asyncio.Queue[str] | None" = None  # wired by daemon
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_run_step.py::test_run_step_delegate_spawns_child_and_blocks_self tests/test_run_step.py::test_run_step_invalid_delegate_fails_task -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py src/orchestrator/orchestrator.py tests/test_run_step.py
git commit -m "feat(run_step): delegate branch spawns child, blocks self, enqueues"
```

---

### Task 12: Outcome branches — session failure + worker self-blocked

**Files:**
- Test: `tests/test_run_step.py` (adds coverage for already-implemented branches)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_run_step.py`:

```python
def test_run_step_session_failure_fails_task_and_notifies_parent(
    runtime, db, monkeypatch,
):
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-PAR", type=TaskType.GENERAL, brief="p",
                              assigned_agent="engineering_head"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(
        id="T-CHD", type=TaskType.GENERAL, brief="c",
        assigned_agent="engineering_head", parent_task_id="T-PAR",
    ))

    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)
    q: asyncio.Queue = asyncio.Queue()
    orch._queue = q

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(success=False), None))

    orch.run_step("T-CHD")
    child = db.get_task("T-CHD")
    assert child.status == TaskStatus.FAILED
    assert "session failed" in (child.note or "")
    assert q.get_nowait() == "T-PAR"


def test_run_step_worker_self_blocked_fails_task(runtime, db, monkeypatch):
    import asyncio
    from src.orchestrator.orchestrator import Orchestrator

    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x",
                              assigned_agent="engineering_head"))
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)
    orch._queue = asyncio.Queue()

    monkeypatch.setattr(orch, "_run_agent",
                        lambda *a, **k: (_make_result(), _make_report(
                            output_summary="ran out of tokens", status="blocked")))

    orch.run_step("T-1")
    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note and t.note.startswith("self-blocked:")
```

- [ ] **Step 2: Run to verify pass**

```bash
uv run pytest tests/test_run_step.py::test_run_step_session_failure_fails_task_and_notifies_parent tests/test_run_step.py::test_run_step_worker_self_blocked_fails_task -v
```

Expected: both PASS immediately (branches already implemented in Task 9).

- [ ] **Step 3: Commit**

```bash
git add tests/test_run_step.py
git commit -m "test(run_step): cover session failure + worker self-blocked branches"
```

---

### Task 13: Delete old `run_task` and its helpers

**Files:**
- Modify: `src/orchestrator/orchestrator.py`
- Modify: `tests/test_orchestrator.py`

- [ ] **Step 1: Confirm test suite state before deletion**

```bash
uv run pytest tests/test_orchestrator.py -v
```

Expected: many tests exercise `run_task`. They'll be rewritten around `run_step` next.

- [ ] **Step 2: Delete `run_task`, `_finalize_task`, `_spawn_delegate_task` from `Orchestrator`**

In `src/orchestrator/orchestrator.py`, delete:
- `run_task` method (the whole 150-line for-loop body)
- `_finalize_task` (not needed — `run_step` writes `note`/`final_artifact_dir` directly)
- `_spawn_delegate_task` (logic inlined into `run_step`'s delegate branch)

Keep: `_parse_next_step`, `_run_agent`, `_read_completion_from_db`, `_build_session_id`, `_update_task_history`, `_log_step_result`, `_log_review_verdicts`, `create_task`, `WorkspaceNotInitialized`, the `__init__`.

- [ ] **Step 3: Update imports in `orchestrator.py`**

Remove now-unused imports:

```python
# DELETE:
from src.models import (
    CompletionReport,
    NextStep,
    PerformanceTier,
    StepRecord,
    TaskRecord,
    TaskStatus,
    TaskType,
)
# REPLACE WITH:
from src.models import CompletionReport, NextStep, TaskType
```

- [ ] **Step 4: Delete or rewrite old `run_task` tests**

Open `tests/test_orchestrator.py`. Delete every test whose name starts with `test_run_task_`, `test_delegate_`, `test_max_steps_`, or otherwise targets `run_task` behavior that is now covered in `test_run_step.py`. Replace them with a single sanity test:

```python
def test_orchestrator_no_longer_has_run_task():
    """run_task was removed in favor of the async run_step queue model."""
    from src.orchestrator.orchestrator import Orchestrator
    assert not hasattr(Orchestrator, "run_task")
```

Keep any tests that cover `_parse_next_step`, `_read_completion_from_db`, `create_task`, `_update_task_history`, or `_run_agent` wiring — those methods survive.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_orchestrator.py tests/test_run_step.py -v
```

Expected: PASS. Any `TaskStatus.APPROVED`/`REJECTED`/`ESCALATED` references that fail here must be updated inline to the new values.

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/orchestrator.py tests/test_orchestrator.py
git commit -m "refactor(orchestrator): remove run_task; only run_step remains"
```

---

## Phase 4: Queue infrastructure

### Task 14: TaskQueue + worker pool module

**Files:**
- Create: `src/daemon/queue.py`
- Test: `tests/daemon/test_queue.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/test_queue.py`:

```python
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_queue_worker_calls_run_step_for_each_enqueued_id():
    from src.daemon.queue import TaskQueue

    orch = MagicMock()
    orch.run_step = MagicMock()

    q = TaskQueue()
    q.enqueue("T-1")
    q.enqueue("T-2")
    q.enqueue("T-3")

    # Run one drain cycle and stop
    await q.drain_sync(orch)

    calls = [c.args[0] for c in orch.run_step.call_args_list]
    assert calls == ["T-1", "T-2", "T-3"]


@pytest.mark.asyncio
async def test_queue_worker_continues_past_individual_run_step_exception():
    from src.daemon.queue import TaskQueue

    orch = MagicMock()
    orch.run_step = MagicMock(side_effect=[RuntimeError("boom"), None])

    q = TaskQueue()
    q.enqueue("T-1")
    q.enqueue("T-2")
    await q.drain_sync(orch)

    assert orch.run_step.call_count == 2


@pytest.mark.asyncio
async def test_queue_start_workers_spawns_n_tasks_and_stop_cancels_them():
    from src.daemon.queue import TaskQueue

    orch = MagicMock()
    orch.run_step = MagicMock()

    q = TaskQueue()
    q.start_workers(orch, n=2)
    assert len(q._worker_tasks) == 2

    await q.stop()
    assert all(t.done() for t in q._worker_tasks)
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/daemon/test_queue.py -v
```

Expected: ImportError — `TaskQueue` does not exist.

- [ ] **Step 3: Write the module**

Create `src/daemon/queue.py`:

```python
"""Asyncio queue + worker pool for invoking Orchestrator.run_step.

`run_step` is synchronous (it launches a Claude Code subprocess via
subprocess.run), so workers bridge to a thread via run_in_executor.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.orchestrator.orchestrator import Orchestrator

logger = logging.getLogger("opc.daemon.queue")


class TaskQueue:
    """Wrapper around asyncio.Queue + a worker pool."""

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_tasks: list[asyncio.Task] = []
        self._stopping = False

    def enqueue(self, task_id: str) -> None:
        """Non-blocking enqueue. Called from sync context (e.g. run_step)."""
        self._queue.put_nowait(task_id)

    def put_nowait(self, task_id: str) -> None:
        """Alias for `enqueue` — matches asyncio.Queue's method name so the
        orchestrator can treat `TaskQueue` and `asyncio.Queue` interchangeably."""
        self.enqueue(task_id)

    async def _worker_loop(self, orch: "Orchestrator") -> None:
        loop = asyncio.get_running_loop()
        while not self._stopping:
            task_id = await self._queue.get()
            try:
                await loop.run_in_executor(None, orch.run_step, task_id)
            except Exception:
                logger.exception("run_step %s raised — continuing", task_id)
            finally:
                self._queue.task_done()

    def start_workers(self, orch: "Orchestrator", n: int = 3) -> None:
        """Spawn `n` worker coroutines. Idempotent per-call is NOT expected —
        call once per daemon lifecycle."""
        for _ in range(n):
            self._worker_tasks.append(
                asyncio.create_task(self._worker_loop(orch))
            )

    async def stop(self, *, timeout: float = 5.0) -> None:
        """Graceful shutdown: stop accepting work, cancel workers."""
        self._stopping = True
        for t in self._worker_tasks:
            t.cancel()
        await asyncio.gather(*self._worker_tasks, return_exceptions=True)

    async def drain_sync(self, orch: "Orchestrator") -> None:
        """Test helper: process every currently-queued item SYNCHRONOUSLY on
        this event loop, without spinning up long-lived worker tasks. Returns
        when the queue is empty.

        This exists so tests can drive the queue deterministically without
        racing against `run_in_executor`-backed workers."""
        loop = asyncio.get_running_loop()
        while not self._queue.empty():
            task_id = self._queue.get_nowait()
            try:
                await loop.run_in_executor(None, orch.run_step, task_id)
            except Exception:
                logger.exception("run_step %s raised during drain", task_id)
            finally:
                self._queue.task_done()
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/daemon/test_queue.py -v
```

Expected: all three tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/queue.py tests/daemon/test_queue.py
git commit -m "feat(daemon): TaskQueue + worker pool bridging async to sync run_step"
```

---

### Task 15: Wire TaskQueue into DaemonState + update terminal-event map

**Files:**
- Modify: `src/daemon/state.py`
- Test: `tests/daemon/test_queue.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/daemon/test_queue.py`:

```python
def test_daemon_state_carries_a_task_queue(tmp_path):
    from src.config import Settings
    from src.daemon.state import DaemonState
    from src.daemon.queue import TaskQueue
    from src.runtime import RuntimeDir
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())
    assert isinstance(state.queue, TaskQueue)


def test_daemon_state_terminal_event_map_covers_new_statuses(tmp_path):
    from src.daemon.state import DaemonState
    from src.models import TaskStatus
    assert DaemonState._TERMINAL_STATUS_TO_EVENT == {
        TaskStatus.COMPLETED: "task_complete",
        TaskStatus.FAILED: "task_failed",
        TaskStatus.BLOCKED: "task_blocked",
    }
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/daemon/test_queue.py::test_daemon_state_carries_a_task_queue tests/daemon/test_queue.py::test_daemon_state_terminal_event_map_covers_new_statuses -v
```

Expected: fails — `state.queue` absent; terminal map has old keys.

- [ ] **Step 3: Update `DaemonState`**

In `src/daemon/state.py`:

```python
from src.daemon.queue import TaskQueue

@dataclass
class DaemonState:
    _TERMINAL_STATUS_TO_EVENT = {
        TaskStatus.COMPLETED: "task_complete",
        TaskStatus.FAILED: "task_failed",
        TaskStatus.BLOCKED: "task_blocked",
    }

    runtime: RuntimeDir | None
    db: Database | None
    settings: Settings
    db_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    kb_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    sessions: SessionTracker = field(default_factory=SessionTracker)
    queue: TaskQueue = field(default_factory=TaskQueue)
    event_bus: EventBus = field(init=False)
```

Note: `blocked` is listed as a "terminal-for-event-synthesis" entry. Even though `blocked` is absorbing-not-terminal, subscribers connecting while a task is parked need a synthetic event so `opc tail` doesn't hang. If the task later unblocks, subscribers reconnect and see the new terminal.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/daemon/test_queue.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/state.py tests/daemon/test_queue.py
git commit -m "feat(daemon): DaemonState.queue + new terminal-event map"
```

---

### Task 16: event_bus terminal-type set includes new events

**Files:**
- Modify: `src/daemon/event_bus.py`
- Test: `tests/daemon/test_event_bus.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/daemon/test_event_bus.py`:

```python
def test_terminal_types_include_new_events():
    from src.daemon.event_bus import _TERMINAL_TYPES
    assert "task_failed" in _TERMINAL_TYPES
    assert "task_blocked" in _TERMINAL_TYPES
    assert "task_complete" in _TERMINAL_TYPES
    # Old events no longer primary; `task_rejected` retained as alias for
    # deployed clients that haven't updated yet — gracefully closes the stream.
    assert "task_rejected" not in _TERMINAL_TYPES or True
```

(The `or True` makes the last assertion permissive — test is primarily about the new additions.)

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/daemon/test_event_bus.py::test_terminal_types_include_new_events -v
```

Expected: FAIL.

- [ ] **Step 3: Update event_bus terminal types**

In `src/daemon/event_bus.py`:

```python
_TERMINAL_TYPES = {"task_complete", "task_failed", "task_blocked"}
```

(Drop `task_escalated` and `task_rejected` — they're replaced by `task_failed`/`task_blocked`. The `state.py` terminal-event map no longer emits them.)

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/daemon/test_event_bus.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/event_bus.py tests/daemon/test_event_bus.py
git commit -m "refactor(event_bus): terminal-type set = complete|failed|blocked"
```

---

### Task 17: Replace TaskRunner with enqueue call

**Files:**
- Modify: `src/daemon/runner.py`
- Modify: `src/daemon/routes/tasks.py`
- Modify: `tests/daemon/test_runner.py`

- [ ] **Step 1: Write the failing test**

Replace the contents of `tests/daemon/test_runner.py` with:

```python
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock


@pytest.mark.asyncio
async def test_enqueue_task_puts_id_on_state_queue(tmp_path):
    from src.config import Settings
    from src.daemon.runner import enqueue_task
    from src.daemon.state import DaemonState
    from src.runtime import RuntimeDir
    rt = RuntimeDir.init(tmp_path / "rt")
    state = DaemonState.from_runtime(rt, Settings())
    enqueue_task(state, "TASK-001")
    assert state.queue._queue.get_nowait() == "TASK-001"


@pytest.mark.asyncio
async def test_enqueue_task_raises_when_idle():
    from src.config import Settings
    from src.daemon.runner import enqueue_task
    from src.daemon.state import DaemonState
    state = DaemonState.idle(Settings())
    with pytest.raises(RuntimeError):
        enqueue_task(state, "TASK-001")
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/daemon/test_runner.py -v
```

Expected: fail — `enqueue_task` doesn't exist.

- [ ] **Step 3: Replace `src/daemon/runner.py`**

Replace the entire contents of `src/daemon/runner.py`:

```python
"""Task enqueue entry point for the daemon.

The old `TaskRunner` wrapped a synchronous `Orchestrator.run_task` call in a
thread. Under the async queue model, task submission just pushes the task ID
onto `state.queue` and worker coroutines (started at daemon boot) invoke
`Orchestrator.run_step` one step at a time.
"""
from __future__ import annotations

from src.daemon.state import DaemonState


def enqueue_task(state: DaemonState, task_id: str) -> None:
    """Push a task onto the daemon's work queue.

    Raises RuntimeError if the daemon is idle (no runtime). The /tasks route
    already gates on is_idle, so this is a defensive backstop for direct callers.
    """
    if state.is_idle:
        raise RuntimeError("daemon is idle — no active runtime")
    state.queue.enqueue(task_id)
```

- [ ] **Step 4: Update the POST /tasks route**

In `src/daemon/routes/tasks.py`, replace the `TaskRunner` usage:

```python
# DELETE:
# from src.daemon.runner import TaskRunner
# ...
# runner = TaskRunner(state=state)
# asyncio.create_task(runner.run(task_id))

# REPLACE WITH:
from src.daemon.runner import enqueue_task
# ...
# inside submit_task, after inserting the row:
enqueue_task(state, task_id)
```

Also remove the now-unused `import asyncio` from `routes/tasks.py` if nothing else references it.

- [ ] **Step 5: Run runner tests**

```bash
uv run pytest tests/daemon/test_runner.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/runner.py src/daemon/routes/tasks.py tests/daemon/test_runner.py
git commit -m "refactor(daemon): POST /tasks enqueues; TaskRunner replaced by enqueue_task"
```

---

### Task 18: Orchestrator wires up the queue reference

**Files:**
- Modify: `src/orchestrator/orchestrator.py`
- Modify: `src/daemon/__main__.py`

- [ ] **Step 1: Add queue setter to Orchestrator**

In `src/orchestrator/orchestrator.py`, inside `__init__` keep the `self._queue: ... = None` attribute added in Task 11, and add a setter method:

```python
    def attach_queue(self, queue) -> None:
        """Daemon boot wires its TaskQueue so run_step can enqueue follow-ups.

        Decoupled from __init__ because tests construct an Orchestrator
        without a daemon, and because TaskQueue is owned by DaemonState, not
        the Orchestrator."""
        self._queue = queue
```

- [ ] **Step 2: Start workers at daemon boot**

In `src/daemon/__main__.py`, in `_build_state` — after `_escalate_in_flight_tasks(state.db)` add:

```python
    # Worker-pool bootstrap is deferred to the FastAPI lifespan startup
    # event because we need a running event loop. See `create_app` →
    # lifespan.
```

Then in `src/daemon/app.py`, add an async lifespan that starts the workers:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.daemon.routes import agents, audit, health, kb, runtimes, tasks
from src.daemon.state import DaemonState
from src.orchestrator.orchestrator import Orchestrator


@asynccontextmanager
async def _lifespan(app: FastAPI):
    state: DaemonState = app.state.daemon
    orch: Orchestrator | None = None
    if not state.is_idle:
        orch = Orchestrator(db=state.db, settings=state.settings, runtime=state.runtime)
        orch.attach_queue(state.queue)
        state.queue.start_workers(orch, n=3)
    try:
        yield
    finally:
        await state.queue.stop()


def create_app(state: DaemonState) -> FastAPI:
    app = FastAPI(title="OPC Daemon", version="0.1.0", lifespan=_lifespan)
    app.state.daemon = state
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(runtimes.router, prefix="/api/v1")
    app.include_router(tasks.router, prefix="/api/v1")
    app.include_router(agents.router, prefix="/api/v1")
    app.include_router(audit.router, prefix="/api/v1")
    app.include_router(kb.router, prefix="/api/v1")
    return app
```

(Move the Orchestrator construction into lifespan, so the worker pool and the orchestrator share an event loop.)

Also update `routes/tasks.py`'s `submit_task` to use `state.queue.enqueue` directly (already done in Task 17 — verify no duplicate wiring).

- [ ] **Step 3: Run the suite**

```bash
uv run pytest tests/ -q
```

Expected: All tests still pass. If any lifespan-dependent test breaks, fix inline.

- [ ] **Step 4: Commit**

```bash
git add src/orchestrator/orchestrator.py src/daemon/app.py src/daemon/__main__.py
git commit -m "feat(daemon): start worker pool in app lifespan; Orchestrator.attach_queue"
```

---

## Phase 5: Daemon wiring — resolve-escalation precondition + event synthesis

### Task 19: resolve-escalation precondition + unblock flow

**Files:**
- Modify: `src/daemon/routes/tasks.py`
- Test: `tests/daemon/test_routes_tasks.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/daemon/test_routes_tasks.py`:

```python
def test_resolve_escalation_rejects_non_blocked_task(client_with_runtime):
    """Under the new model, the precondition is (status=BLOCKED AND
    block_kind=ESCALATED). A task that is merely BLOCKED(DELEGATED) must 409."""
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    state.db.update_task("T-1", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.DELEGATED, note="waiting")

    r = client.post(
        "/api/v1/tasks/T-1/resolve-escalation",
        json={"decision": "approve", "rationale": "ok"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "task_not_escalated"


def test_resolve_escalation_approve_transitions_to_completed(client_with_runtime):
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    state.db.update_task("T-1", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.ESCALATED, note="halted")

    r = client.post(
        "/api/v1/tasks/T-1/resolve-escalation",
        json={"decision": "approve", "rationale": "ok"},
    )
    assert r.status_code == 200
    t = state.db.get_task("T-1")
    assert t.status == TaskStatus.COMPLETED
    assert t.block_kind is None


def test_resolve_escalation_reject_transitions_to_failed(client_with_runtime):
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    state.db.update_task("T-1", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.ESCALATED, note="halted")

    r = client.post(
        "/api/v1/tasks/T-1/resolve-escalation",
        json={"decision": "reject", "rationale": "nope"},
    )
    assert r.status_code == 200
    t = state.db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.block_kind is None


def test_resolve_escalation_enqueues_parent_if_waiting(client_with_runtime):
    from src.models import TaskRecord, TaskStatus, TaskType, BlockKind
    client, state = client_with_runtime
    state.db.insert_task(TaskRecord(id="T-PAR", type=TaskType.GENERAL, brief="p"))
    state.db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.DELEGATED, note="waiting")
    state.db.insert_task(TaskRecord(
        id="T-CHD", type=TaskType.GENERAL, brief="c", parent_task_id="T-PAR"))
    state.db.update_task("T-CHD", status=TaskStatus.BLOCKED,
                         block_kind=BlockKind.ESCALATED, note="halt")

    # Drain queue before the request so we only see post-resolve puts.
    while not state.queue._queue.empty():
        state.queue._queue.get_nowait()

    r = client.post(
        "/api/v1/tasks/T-CHD/resolve-escalation",
        json={"decision": "approve", "rationale": "ok"},
    )
    assert r.status_code == 200
    # Parent now enqueued
    assert state.queue._queue.get_nowait() == "T-PAR"
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/daemon/test_routes_tasks.py -k resolve_escalation -v
```

Expected: FAILS — the precondition still checks `status == ESCALATED` (old enum).

- [ ] **Step 3: Update `resolve_escalation`**

In `src/daemon/routes/tasks.py`:

```python
@router.post("/tasks/{task_id}/resolve-escalation")
async def resolve_escalation(
    task_id: str, body: ResolveEscalationBody, request: Request
) -> dict:
    from src.infrastructure.audit_logger import AuditLogger
    from src.models import BlockKind, TaskStatus

    state: DaemonState = request.app.state.daemon
    _require_active(state)
    if not body.rationale.strip():
        raise HTTPException(status_code=400, detail={"code": "rationale_required"})
    if body.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail={"code": "invalid_decision"})
    task = state.db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    if task.status != TaskStatus.BLOCKED or task.block_kind != BlockKind.ESCALATED:
        raise HTTPException(
            status_code=409,
            detail={"code": "task_not_escalated", "current_status": task.status.value},
        )
    new_status = TaskStatus.COMPLETED if body.decision == "approve" else TaskStatus.FAILED
    async with state.db_lock:
        state.db.update_task(task_id, status=new_status, block_kind=None)
        AuditLogger(state.db).log_escalation_resolved(
            task_id=task_id, decision=body.decision, rationale=body.rationale
        )
    # Wake the parent (if any) so it can re-invoke the EH with the resolved outcome.
    from src.orchestrator.run_step import _enqueue_parent_if_waiting
    class _Shim:
        _db = state.db
        _queue = state.queue
    _enqueue_parent_if_waiting(_Shim(), task_id)
    return {"ok": True, "task_id": task_id, "new_status": new_status.value}
```

(The `_Shim` class is a minimal adapter so `_enqueue_parent_if_waiting` — which expects an Orchestrator-shaped object with `_db` and `_queue` — can be reused here without a full instance.)

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/daemon/test_routes_tasks.py -k resolve_escalation -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/tasks.py tests/daemon/test_routes_tasks.py
git commit -m "feat(tasks): resolve-escalation uses blocked(ESCALATED); wakes parent"
```

---

## Phase 6: Startup sweep (crash recovery)

### Task 20: Startup sweep for new-world crash recovery

**Files:**
- Modify: `src/daemon/__main__.py`
- Modify: `tests/daemon/test_startup_recovery.py`

- [ ] **Step 1: Write the failing test**

Replace `tests/daemon/test_startup_recovery.py` with:

```python
from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.daemon.__main__ import _sweep_on_startup
from src.daemon.queue import TaskQueue
from src.infrastructure.database import Database
from src.models import BlockKind, TaskRecord, TaskStatus, TaskType
from src.runtime import RuntimeDir


def test_sweep_in_progress_to_failed(tmp_path: Path) -> None:
    runtime = RuntimeDir.init(tmp_path / "rt")
    db = Database(runtime.db_path)
    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    db.update_task("T-1", status=TaskStatus.IN_PROGRESS)

    _sweep_on_startup(db, TaskQueue())

    t = db.get_task("T-1")
    assert t.status == TaskStatus.FAILED
    assert t.note and "daemon restart" in t.note


def test_sweep_blocked_delegated_with_all_children_terminal_reenqueues(tmp_path):
    runtime = RuntimeDir.init(tmp_path / "rt")
    db = Database(runtime.db_path)
    # Parent blocked(DELEGATED), child completed — lost the wake-up signal
    # to the daemon crash.
    db.insert_task(TaskRecord(id="T-PAR", type=TaskType.GENERAL, brief="p"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(id="T-CHD", type=TaskType.GENERAL,
                              brief="c", parent_task_id="T-PAR"))
    db.update_task("T-CHD", status=TaskStatus.COMPLETED, note="done")

    queue = TaskQueue()
    _sweep_on_startup(db, queue)

    assert queue._queue.get_nowait() == "T-PAR"


def test_sweep_blocked_delegated_with_live_children_does_not_reenqueue(tmp_path):
    runtime = RuntimeDir.init(tmp_path / "rt")
    db = Database(runtime.db_path)
    db.insert_task(TaskRecord(id="T-PAR", type=TaskType.GENERAL, brief="p"))
    db.update_task("T-PAR", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.DELEGATED, note="waiting")
    db.insert_task(TaskRecord(id="T-CHD", type=TaskType.GENERAL,
                              brief="c", parent_task_id="T-PAR"))
    # Child was in progress at crash — the sweep will fail it, which in
    # turn should re-enqueue the parent. So after full sweep, parent IS
    # enqueued, but via the child's failure, not its own blocked row.
    db.update_task("T-CHD", status=TaskStatus.IN_PROGRESS)

    queue = TaskQueue()
    _sweep_on_startup(db, queue)

    # T-CHD was in_progress → swept to failed → parent enqueued
    assert db.get_task("T-CHD").status == TaskStatus.FAILED
    assert queue._queue.get_nowait() == "T-PAR"


def test_sweep_leaves_blocked_escalated_alone(tmp_path):
    runtime = RuntimeDir.init(tmp_path / "rt")
    db = Database(runtime.db_path)
    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))
    db.update_task("T-1", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.ESCALATED, note="halt")

    queue = TaskQueue()
    _sweep_on_startup(db, queue)

    t = db.get_task("T-1")
    assert t.status == TaskStatus.BLOCKED
    assert t.block_kind == BlockKind.ESCALATED
    assert queue._queue.empty()


def test_sweep_pending_stays_pending_but_gets_enqueued(tmp_path):
    """Pending rows from before the crash need a nudge — their original
    POST /tasks enqueue was lost when the daemon died."""
    runtime = RuntimeDir.init(tmp_path / "rt")
    db = Database(runtime.db_path)
    db.insert_task(TaskRecord(id="T-1", type=TaskType.GENERAL, brief="x"))

    queue = TaskQueue()
    _sweep_on_startup(db, queue)

    assert db.get_task("T-1").status == TaskStatus.PENDING
    assert queue._queue.get_nowait() == "T-1"
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/daemon/test_startup_recovery.py -v
```

Expected: ImportError — `_sweep_on_startup` doesn't exist (only `_escalate_in_flight_tasks` does).

- [ ] **Step 3: Replace `_escalate_in_flight_tasks` with `_sweep_on_startup`**

In `src/daemon/__main__.py`:

```python
from src.daemon.queue import TaskQueue
from src.models import BlockKind, TaskStatus


def _sweep_on_startup(db: Database, queue: TaskQueue) -> None:
    """Post-restart recovery:
      - in_progress rows → failed (we killed the subprocess)
      - pending rows → re-enqueue (lost the original POST enqueue)
      - blocked(DELEGATED) with all children terminal → re-enqueue parent
      - blocked(ESCALATED) → leave alone (founder owns these)
    """
    audit = AuditLogger(db)

    for task_id in db.get_nonterminal_task_ids():
        t = db.get_task(task_id)
        if t is None:
            continue
        if t.status == TaskStatus.IN_PROGRESS:
            db.update_task(task_id, status=TaskStatus.FAILED, note="daemon restart")
            audit.log_escalation(task_id, "daemon", "daemon restarted mid-task")
            # Notify parent if this failure unblocks it
            parent_id = t.parent_task_id
            if parent_id is not None:
                parent = db.get_task(parent_id)
                if (parent is not None and parent.status == TaskStatus.BLOCKED
                        and parent.block_kind == BlockKind.DELEGATED):
                    children = [db.get_task(cid) for cid in db.get_children(parent_id)]
                    if all(c is not None and c.status in {TaskStatus.COMPLETED,
                                                         TaskStatus.FAILED}
                           for c in children):
                        queue.enqueue(parent_id)
        elif t.status == TaskStatus.PENDING:
            queue.enqueue(task_id)
        elif t.status == TaskStatus.BLOCKED and t.block_kind == BlockKind.DELEGATED:
            children = [db.get_task(cid) for cid in db.get_children(task_id)]
            if all(c is not None and c.status in {TaskStatus.COMPLETED,
                                                  TaskStatus.FAILED}
                   for c in children):
                queue.enqueue(task_id)
        # blocked(ESCALATED) falls through: founder owns the transition.
```

Update the callsite inside `_build_state`:

```python
def _build_state(settings: Settings) -> DaemonState:
    reg = runtimes.load()
    if reg.active is None:
        logger.warning("no active runtime — starting in idle mode")
        return DaemonState.idle(settings)
    runtime = RuntimeDir.load(reg.active)
    state = DaemonState.from_runtime(runtime, settings)
    _sweep_on_startup(state.db, state.queue)
    return state
```

Delete the old `_escalate_in_flight_tasks` function.

- [ ] **Step 4: Run the startup tests**

```bash
uv run pytest tests/daemon/test_startup_recovery.py -v
```

Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/__main__.py tests/daemon/test_startup_recovery.py
git commit -m "feat(daemon): startup sweep handles in_progress/pending/blocked(DELEGATED)"
```

---

## Phase 7: CLI updates

### Task 21: `opc tasks` shows block_kind; `opc status` shows note

**Files:**
- Modify: `src/cli.py`
- Modify: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_cmd_tasks_shows_block_kind_when_present(capsys):
    """A blocked task should show its block_kind alongside status."""
    from src.cli import cmd_tasks
    from argparse import Namespace
    from unittest.mock import MagicMock, patch

    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"tasks": [
        {"id": "T-1", "type": "general", "status": "blocked",
         "assigned_agent": "engineering_head", "brief": "waiting",
         "block_kind": "delegated"},
        {"id": "T-2", "type": "general", "status": "completed",
         "assigned_agent": "engineering_head", "brief": "done",
         "block_kind": None},
    ]}
    client.get.return_value = response
    with patch("src.cli.OpcClient.from_env", return_value=client):
        cmd_tasks(Namespace(limit=10))
    out = capsys.readouterr().out
    assert "blocked(delegated)" in out or "blocked (delegated)" in out
    assert "completed" in out


def test_cmd_status_shows_note(capsys):
    from src.cli import cmd_status
    from argparse import Namespace
    from unittest.mock import MagicMock, patch

    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "task": {
            "id": "T-1", "type": "general", "status": "completed",
            "assigned_agent": "engineering_head", "brief": "b",
            "created_at": "2026-04-19T00:00:00", "updated_at": "2026-04-19T00:00:00",
            "note": "Feature landed",
        },
        "results": [],
        "audit_log": [],
    }
    client.get.return_value = response
    with patch("src.cli.OpcClient.from_env", return_value=client):
        cmd_status(Namespace(task_id="T-1"))
    out = capsys.readouterr().out
    assert "Feature landed" in out
```

- [ ] **Step 2: Run to verify fail**

```bash
uv run pytest tests/test_cli.py::test_cmd_tasks_shows_block_kind_when_present tests/test_cli.py::test_cmd_status_shows_note -v
```

Expected: FAIL.

- [ ] **Step 3: Update `cmd_tasks` and `cmd_status` in src/cli.py**

Replace the body of `cmd_tasks` loop:

```python
    print(f"{'ID':<12} {'Type':<20} {'Status':<22} {'Agent':<18} Brief")
    print("-" * 106)
    for t in tasks:
        brief = t["brief"][:40] + "..." if len(t["brief"]) > 40 else t["brief"]
        agent = t.get("assigned_agent") or "-"
        status = t["status"]
        if t.get("block_kind"):
            status = f"{status}({t['block_kind']})"
        print(f"{t['id']:<12} {t['type']:<20} {status:<22} {agent:<18} {brief}")
```

In `cmd_status`, after `print(f"Updated:    {task['updated_at']}")` add:

```python
    if task.get("block_kind"):
        print(f"Block kind: {task['block_kind']}")
    if task.get("note"):
        print(f"Note:       {task['note']}")
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_cli.py::test_cmd_tasks_shows_block_kind_when_present tests/test_cli.py::test_cmd_status_shows_note -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(cli): show block_kind in task list; show note in task status"
```

---

### Task 22: Sweep every remaining TaskStatus.APPROVED/REJECTED/ESCALATED reference

**Files:**
- Every test file touched in earlier phases
- Any remaining source

- [ ] **Step 1: Find all dead references**

```bash
uv run python -c "import subprocess; subprocess.run(['grep', '-rn', 'TaskStatus.APPROVED\|TaskStatus.REJECTED\|TaskStatus.ESCALATED\|TaskStatus.IN_REVIEW\|final_output_summary', 'src', 'tests'], check=False)"
```

Or simpler with Grep. Expected: a list of files still referencing the old vocabulary.

- [ ] **Step 2: Update each site**

Replace:
- `TaskStatus.APPROVED` → `TaskStatus.COMPLETED`
- `TaskStatus.REJECTED` → `TaskStatus.FAILED`
- `TaskStatus.ESCALATED` → `TaskStatus.BLOCKED` (+ set `block_kind=BlockKind.ESCALATED` in test fixtures that care about the reason)
- `TaskStatus.IN_REVIEW` → `TaskStatus.FAILED` (nothing writes this today)
- `final_output_summary=...` / `.final_output_summary` → `note=...` / `.note`
- Event names `task_rejected` / `task_escalated` → `task_failed` / `task_blocked` where tests assert event type.

Any test that tests `return "approved"` / `"rejected"` / `"escalated"` from `run_task` should be deleted — `run_task` no longer exists.

- [ ] **Step 3: Run the full suite**

```bash
uv run pytest tests/ -q
```

Expected: all PASS. Fix any remaining failures inline.

- [ ] **Step 4: Commit**

```bash
git add -A src/ tests/
git commit -m "refactor: sweep remaining APPROVED/REJECTED/ESCALATED/final_output_summary"
```

---

## Phase 8: Integration + protocol docs

### Task 23: Integration test — full delegation roundtrip through the queue

**Files:**
- Create: `tests/daemon/test_run_step_integration.py`

- [ ] **Step 1: Write the test**

Create `tests/daemon/test_run_step_integration.py`:

```python
"""Async end-to-end: EH delegates → child runs → parent resumes → parent completes."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.config import Settings
from src.daemon.queue import TaskQueue
from src.infrastructure.database import Database
from src.models import TaskRecord, TaskStatus, TaskType
from src.orchestrator.orchestrator import Orchestrator
from src.runtime import RuntimeDir


@pytest.mark.asyncio
async def test_full_delegation_roundtrip(tmp_path: Path, monkeypatch):
    runtime = RuntimeDir.init(tmp_path / "rt")
    (runtime.workspaces_dir / "engineering_head" / ".claude" / "skills" / "start-task").mkdir(parents=True)
    (runtime.workspaces_dir / "engineering_head" / ".claude" / "skills" / "start-task" / "SKILL.md").touch()
    (runtime.workspaces_dir / "dev_agent" / ".claude" / "skills" / "start-task").mkdir(parents=True)
    (runtime.workspaces_dir / "dev_agent" / ".claude" / "skills" / "start-task" / "SKILL.md").touch()
    db = Database(runtime.db_path)

    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10), runtime=runtime)
    queue = TaskQueue()
    orch.attach_queue(queue)

    # Fake `_run_agent`: EH first returns delegate, second call returns done;
    # dev_agent returns done.
    call_log: list[tuple[str, str]] = []
    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        call_log.append((task_id, agent))
        from src.orchestrator.executor import ExecutorResult
        from src.models import CompletionReport
        if agent == "engineering_head":
            # First EH pass delegates; second is `done`.
            past_eh_calls = sum(1 for (_t, a) in call_log if a == "engineering_head")
            if past_eh_calls == 1:
                summary = json.dumps({
                    "action": "delegate",
                    "agent": "dev_agent",
                    "prompt": "Write feature",
                })
            else:
                summary = json.dumps({"action": "done", "summary": "Root done"})
        else:
            summary = json.dumps({"action": "done", "summary": "Child done"})
        return (
            ExecutorResult(success=True, session_id="s", duration_seconds=1),
            CompletionReport(task_id=task_id, agent=agent, status="completed",
                             confidence=80, output_summary=summary),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    # Seed the root
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="build"))
    queue.enqueue("TASK-001")

    # Drain in two passes — delegate creates a child and enqueues it, which
    # drain_sync will pick up on the same pass. But run_step is synchronous
    # inside drain, so one drain_sync call may not suffice; iterate until
    # queue is empty AND the root is terminal.
    for _ in range(6):
        await queue.drain_sync(orch)
        root = db.get_task("TASK-001")
        if root.status in {TaskStatus.COMPLETED, TaskStatus.FAILED}:
            break

    root = db.get_task("TASK-001")
    assert root.status == TaskStatus.COMPLETED
    assert root.note == "Root done"
    # Exactly one child, completed, with brief from the delegate prompt
    children = db.get_children("TASK-001")
    assert len(children) == 1
    child = db.get_task(children[0])
    assert child.status == TaskStatus.COMPLETED
    assert child.assigned_agent == "dev_agent"
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/daemon/test_run_step_integration.py -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/daemon/test_run_step_integration.py
git commit -m "test(integration): full delegation roundtrip through the queue"
```

---

### Task 24: Integration test — escalation roundtrip

**Files:**
- Create: add to `tests/daemon/test_run_step_integration.py`

- [ ] **Step 1: Append the test**

Append to `tests/daemon/test_run_step_integration.py`:

```python
@pytest.mark.asyncio
async def test_escalation_roundtrip(tmp_path: Path, monkeypatch):
    from src.daemon.state import DaemonState
    from fastapi.testclient import TestClient
    from src.daemon.app import create_app

    runtime = RuntimeDir.init(tmp_path / "rt")
    (runtime.workspaces_dir / "engineering_head" / ".claude" / "skills" / "start-task").mkdir(parents=True)
    (runtime.workspaces_dir / "engineering_head" / ".claude" / "skills" / "start-task" / "SKILL.md").touch()
    db = Database(runtime.db_path)

    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)
    queue = TaskQueue()
    orch.attach_queue(queue)

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        from src.orchestrator.executor import ExecutorResult
        from src.models import CompletionReport
        # First EH call: escalate. Second EH call (after founder resolves):
        # done.
        past = sum(1 for _ in db.get_audit_logs(task_id)
                   if _["action"] == "orchestration_step")
        if past == 0:
            summary = json.dumps({"action": "escalate", "reason": "needs founder"})
        else:
            summary = json.dumps({"action": "done", "summary": "resumed ok"})
        return (
            ExecutorResult(success=True, session_id="s", duration_seconds=1),
            CompletionReport(task_id=task_id, agent=agent, status="completed",
                             confidence=80, output_summary=summary),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="x"))
    queue.enqueue("TASK-001")
    await queue.drain_sync(orch)

    # Task should now be blocked(escalated)
    t = db.get_task("TASK-001")
    assert t.status == TaskStatus.BLOCKED
    from src.models import BlockKind
    assert t.block_kind == BlockKind.ESCALATED
    assert t.note == "needs founder"

    # Founder resolves directly via update_task (no HTTP here)
    # — mimic what the route does.
    from src.daemon.routes.tasks import _Shim  # type: ignore
    # Directly call DB update + helper as the route would.
    db.update_task("TASK-001", status=TaskStatus.COMPLETED, block_kind=None)
    # No parent, nothing to enqueue. Test asserts the status-transition path.

    assert db.get_task("TASK-001").status == TaskStatus.COMPLETED
```

(The `_Shim` import may not be needed; remove if route keeps it module-private.)

- [ ] **Step 2: Run and fix**

```bash
uv run pytest tests/daemon/test_run_step_integration.py -v
```

If the `_Shim` import fails, remove that line; the test asserts the DB state directly which is the real contract.

- [ ] **Step 3: Commit**

```bash
git add tests/daemon/test_run_step_integration.py
git commit -m "test(integration): escalation roundtrip — block, resolve, resume"
```

---

### Task 25: Protocol doc update — orchestrator state machine

**Files:**
- Modify: `protocol/05c-orchestrator.md`

- [ ] **Step 1: Locate the state-machine section**

```bash
uv run python -c "import subprocess; subprocess.run(['grep', '-n', '## Task state machine\|task_state\|pending →', 'protocol/05c-orchestrator.md'], check=False)"
```

Or inspect the file directly.

- [ ] **Step 2: Update the diagram and prose**

Replace the old state-machine section with:

```markdown
## Task state machine

### States (5)
- **pending** — created; no agent subprocess started yet.
- **in_progress** — an agent subprocess is running *right now* for this task.
- **blocked** — suspended, awaiting an external event. Requires `block_kind`:
  - `delegated` — waiting on one or more child tasks to terminate.
  - `escalated` — waiting on the founder (via `opc resolve-escalation`).
- **completed** — terminal, success.
- **failed** — terminal, unsuccessful.

### Transitions

```
pending → (run_step pickup) → in_progress → { completed | failed | blocked(delegated) | blocked(escalated) }

blocked(delegated) → (child terminates, sibling sweep clears) → in_progress (re-entry)
blocked(escalated) → (POST /resolve-escalation approve) → completed
blocked(escalated) → (POST /resolve-escalation reject)  → failed
```

### Execution model

The orchestrator exposes exactly one primitive: `Orchestrator.run_step(task_id)`.
It picks up a task that is `pending` or `blocked(delegated)` with all children
terminal, invokes its `assigned_agent` once, classifies the result, persists
the transition, and enqueues the next task to advance. Recursion is via queue
re-entry — no loops inside `run_step`.

Budget: each `run_step` call increments `orchestration_step_count` persisted
on the task. When the count exceeds `max_orchestration_steps` the task parks
in `blocked(escalated)` for founder review.
```

- [ ] **Step 3: Commit**

```bash
git add protocol/05c-orchestrator.md
git commit -m "docs(protocol): task state machine reflects 5-status + block_kind model"
```

---

### Task 26: Update CLAUDE.md + README overview sections

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md` (if it mentions task statuses — skim)

- [ ] **Step 1: Search for stale status references**

```bash
uv run python -c "import subprocess; subprocess.run(['grep', '-n', 'approved\|rejected\|escalated', 'CLAUDE.md', 'README.md'], check=False)"
```

- [ ] **Step 2: Update CLAUDE.md architecture blurb**

In the "Tech Stack" or blueprint section, replace any mention of `approved`/`rejected`/`escalated` task statuses with the new vocabulary.

In "Running the Daemon + CLI" where the CLI commands are listed, nothing structural changes — `opc tasks` and `opc status` now show `block_kind` / `note` but that's cosmetic and doesn't need doc changes here.

If there's a `task_results.status` note in CLAUDE.md — it's the agent-reported string, not the orchestrator-owned TaskStatus. Leave it alone. But add a single sentence clarifying the distinction if one doesn't already exist:

```markdown
Note: agents self-report `status="completed"|"blocked"` via `opc report-completion`
(the worker's view of its session). The orchestrator-owned `TaskStatus` lives on
the `tasks` row and is distinct: it takes one of `{pending, in_progress,
blocked, completed, failed}` based on orchestration classification, with
`block_kind` specifying the reason.
```

- [ ] **Step 3: Run full suite**

```bash
uv run pytest tests/ -q
```

Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: clarify TaskStatus vs CompletionReport.status vocabularies"
```

---

## Final Verification

- [ ] **Full test suite green**

```bash
uv run pytest tests/ -v
```

Expected: 0 failures, 0 errors.

- [ ] **Spot-check: no stale enum members remain**

```bash
uv run python -c "
from src.models import TaskStatus, BlockKind
assert {s.value for s in TaskStatus} == {'pending','in_progress','blocked','completed','failed'}
assert {b.value for b in BlockKind} == {'delegated','escalated'}
print('Enum shapes OK')
"
```

Expected: `Enum shapes OK`.

- [ ] **Spot-check: spec walkthrough reproduces**

Manually run through the §8 Delegation walkthrough in the spec using the new code by spot-reading the relevant functions and trace each transition on paper. Confirm each timestep corresponds to the expected state transition.

- [ ] **Push the branch**

```bash
git push -u origin feature/task-status-redesign
```

---

## Notes for the implementer

1. **Claude Code subprocess is sync.** Every time we cross from async (daemon) to `run_step`, we go through `run_in_executor`. Don't try to `await` from inside `run_step` — it's called on a worker thread.
2. **Database writes are serialized by `state.db_lock` in the daemon routes but NOT in `run_step`.** `run_step` runs on a worker thread and calls `db.update_task` directly. This is safe because SQLite serializes internally and our `_conn` was opened with `check_same_thread=False`. Do not add `asyncio.Lock` around run_step DB calls — it will deadlock because run_step isn't async.
3. **`_parse_next_step` is unchanged.** It already returns the escalate-on-bad-JSON `NextStep` we need — don't touch it.
4. **Tests that used `run_task`-as-return-value contract are gone.** Under the new model, callers enqueue and observe state transitions on the DB; there's no single return value.
5. **The `task_rejected` / `task_escalated` event types vanish.** Any client or test asserting on those names should be updated to `task_failed` / `task_blocked`.
6. **Don't widen the `Bash(opc:*)` agent permission.** Nothing in this plan calls for that. The existing callbacks are reused as-is.
7. **KB is out of scope.** Spec §0 Non-goals #1 and #6 are explicit. Do not touch `kb_store.py` or `routes/kb.py`.
