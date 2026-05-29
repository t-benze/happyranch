# Task Blocked-by-Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `blocked_on_job` task state so agents can self-block on N jobs and the system auto-resumes the task once every listed job is terminal — eliminating the manual `grassland revisit` step that today gates the unblock for `review_required=false` jobs.

**Architecture:** State transitions stay inside `run_step_impl` (the orchestrator owns task state). The resume helper is read-only — predicate-check + enqueue. Three callers fire the helper: jobs-runner terminal hook, self-block submission (inside `run_step_impl`), startup-recovery scan. A new entry-state branch in `run_step_impl` step 1 admits `BLOCKED + BLOCKED_ON_JOB`; the existing CAS at step 3 atomically flips `blocked → in_progress` and writes the `task_resumed_from_jobs` audit row.

**Tech Stack:** Python 3.13, pydantic v2, FastAPI, asyncio, SQLite (per-org), pytest. No new external deps.

**Reference spec:** `docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md`. When in doubt, the spec is authoritative.

**Working directory:** Run all commands from `/Users/tangbz/projects/my-opc/.claude/worktrees/task-blocked-by-job/` (the worktree). Tests: `uv run pytest tests/...`. Lint/type check: this project has no enforced linter — match existing style.

---

## File Structure

**Files to create (8):**
- `tests/orchestrator/test_resume_helper.py` — unit tests for `_maybe_resume_blocked_task`
- `tests/orchestrator/test_run_step_blocked_on_job.py` — unit tests for new run_step branches
- `tests/daemon/test_completion_route_blocked_on_jobs.py` — route validation tests
- `tests/infrastructure/test_database_blocked_on_jobs.py` — DB layer tests
- `tests/integration/test_task_blocked_by_job_autonomous.py` — end-to-end auto-run
- `tests/integration/test_task_blocked_by_job_review_required.py` — end-to-end review-required
- `tests/integration/test_task_blocked_by_job_multi.py` — end-to-end multi-job
- `tests/integration/test_task_blocked_by_job_startup_recovery.py` — end-to-end crash recovery

## Pre-task ritual (CLAUDE.md mandate)

Per `CLAUDE.md > GitNexus — Code Intelligence`, before editing any function, class, or method:

```python
gitnexus_impact({target: "<symbolName>", direction: "upstream"})
```

The symbols you'll be editing across this plan:
- `run_step_impl` (T10, T11, T12, T13) — HIGH blast radius
- `try_claim_for_step` (T11 reads it, doesn't modify) — informational
- `CompletionReport`, `CompletionBody` (T7) — MEDIUM blast radius
- `Database._create_tables` (T2) — LOW (additive migration)
- `Database.update_task`, `Database.get_task` (T3) — MEDIUM
- `TaskQueue.enqueue` and `_Dispatcher.run_step` (T8) — HIGH

Report HIGH/CRITICAL findings to the user before proceeding. After each commit, run `gitnexus_detect_changes()` to verify the scope matches expectations.

**Files to modify (10):**
- `src/models.py` — add `BlockKind.BLOCKED_ON_JOB`, add `waiting_on_job_ids` to `CompletionReport`
- `src/infrastructure/database.py` — migration + `get_job_status` + `list_tasks_blocked_on_jobs` + extend `TaskRecord` shape
- `src/infrastructure/audit_logger.py` — add 3 audit methods
- `src/daemon/queue.py` — extend `enqueue` with optional metadata dict
- `src/daemon/routes/tasks.py` — extend `CompletionBody`, add validation matrix
- `src/orchestrator/run_step.py` — entry-state branch + CAS-win audit + block-on-jobs branch + resume helper + resume header
- `src/daemon/jobs_runner.py` — caller A terminal hook
- `src/daemon/app.py` — caller C startup recovery scan
- `protocol/skills/jobs/SKILL.md` — rewrite "After submitting" section
- `src/cli.py` (or equivalent client) — extend `grassland details` output
- `CLAUDE.md` — three documentation sections

---

## Task 1: Add `BlockKind.BLOCKED_ON_JOB` enum value

**Files:**
- Modify: `src/models.py:19-21`
- Test: `tests/infrastructure/test_database_blocked_on_jobs.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/infrastructure/test_database_blocked_on_jobs.py`:

```python
from __future__ import annotations

from src.models import BlockKind


def test_blocked_on_job_enum_value():
    """BlockKind has a BLOCKED_ON_JOB value with the string 'blocked_on_job'."""
    assert BlockKind.BLOCKED_ON_JOB.value == "blocked_on_job"
    assert BlockKind("blocked_on_job") is BlockKind.BLOCKED_ON_JOB
```

- [ ] **Step 2: Run test, verify it fails**

```bash
uv run pytest tests/infrastructure/test_database_blocked_on_jobs.py::test_blocked_on_job_enum_value -v
```
Expected: FAIL with `AttributeError: BLOCKED_ON_JOB` or `ValueError: 'blocked_on_job' is not a valid BlockKind`.

- [ ] **Step 3: Add the enum value**

Edit `src/models.py`. The existing block is:

```python
class BlockKind(StrEnum):
    DELEGATED = "delegated"
    ESCALATED = "escalated"
```

Change to:

```python
class BlockKind(StrEnum):
    DELEGATED = "delegated"
    ESCALATED = "escalated"
    BLOCKED_ON_JOB = "blocked_on_job"
```

- [ ] **Step 4: Run test, verify it passes**

```bash
uv run pytest tests/infrastructure/test_database_blocked_on_jobs.py::test_blocked_on_job_enum_value -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/infrastructure/test_database_blocked_on_jobs.py
git commit -m "feat(models): add BlockKind.BLOCKED_ON_JOB enum value"
```

---

## Task 2: Add `blocked_on_job_ids` column + idempotent migration

**Files:**
- Modify: `src/infrastructure/database.py` (around line 462 — the `_create_tables` ALTER TABLE block)
- Test: `tests/infrastructure/test_database_blocked_on_jobs.py`

- [ ] **Step 1: Write failing test**

Append to `tests/infrastructure/test_database_blocked_on_jobs.py`:

```python
import sqlite3
import tempfile
from pathlib import Path

from src.infrastructure.database import Database


def test_blocked_on_job_ids_column_added():
    """Database init adds blocked_on_job_ids TEXT column to tasks table."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)
        # Inspect schema directly via raw sqlite3 so we don't depend on ORM-side
        # field discovery — the column has to exist at the SQL layer.
        conn = sqlite3.connect(db_path)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        conn.close()
        assert "blocked_on_job_ids" in cols


def test_migration_is_idempotent():
    """Running migration twice (re-opening Database) doesn't error."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        Database(db_path)  # first init creates column
        Database(db_path)  # second open should swallow "duplicate column"
```

- [ ] **Step 2: Run test, verify it fails**

```bash
uv run pytest tests/infrastructure/test_database_blocked_on_jobs.py::test_blocked_on_job_ids_column_added -v
```
Expected: FAIL — column missing.

- [ ] **Step 3: Add the ALTER TABLE in `_create_tables`**

Open `src/infrastructure/database.py`, find the `for ddl in (` block starting at line 461 (the existing `block_kind` ALTER). Add `blocked_on_job_ids` to the tuple:

```python
for ddl in (
    "ALTER TABLE tasks ADD COLUMN block_kind TEXT",
    "ALTER TABLE tasks ADD COLUMN note TEXT",
    "ALTER TABLE tasks ADD COLUMN orchestration_step_count INTEGER DEFAULT 0",
    "ALTER TABLE tasks ADD COLUMN cancelled_at TEXT",
    "ALTER TABLE tasks ADD COLUMN revisit_of_task_id TEXT",
    "ALTER TABLE tasks ADD COLUMN dispatched_from_talk_id TEXT",
    # ... existing ALTERs ...
    "ALTER TABLE tasks ADD COLUMN blocked_on_job_ids TEXT",  # NEW: spec §3.1
):
```

The surrounding `try/except sqlite3.OperationalError: pass` already handles idempotency.

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/infrastructure/test_database_blocked_on_jobs.py -v
```
Expected: both new tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/infrastructure/test_database_blocked_on_jobs.py
git commit -m "feat(db): add blocked_on_job_ids column with idempotent migration"
```

---

## Task 3: Wire `blocked_on_job_ids` into `TaskRecord` model + read/write

**Files:**
- Modify: `src/infrastructure/database.py` — `TaskRecord` dataclass, `get_task`, `update_task`, `_row_to_task` (or equivalent)
- Test: `tests/infrastructure/test_database_blocked_on_jobs.py`

- [ ] **Step 1: Write failing test**

Append to `tests/infrastructure/test_database_blocked_on_jobs.py`:

```python
import json

from src.models import BlockKind, TaskStatus


def test_blocked_on_job_ids_round_trips_through_update_and_read():
    """update_task can set blocked_on_job_ids; get_task reads it back."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)
        # Use an existing test helper / direct insert. Match the pattern used
        # elsewhere in tests/ for creating a Task row.
        from src.models import TaskRecord
        task = TaskRecord(
            id="TASK-001", team="engineering", brief="t",
            status=TaskStatus.IN_PROGRESS, parent_task_id=None,
        )
        db.insert_task(task)
        db.update_task(
            "TASK-001",
            status=TaskStatus.BLOCKED,
            block_kind=BlockKind.BLOCKED_ON_JOB,
            blocked_on_job_ids=json.dumps(["JOB-12", "JOB-13"]),
        )
        loaded = db.get_task("TASK-001")
        assert loaded.status == TaskStatus.BLOCKED
        assert loaded.block_kind == BlockKind.BLOCKED_ON_JOB
        assert loaded.blocked_on_job_ids == '["JOB-12", "JOB-13"]'
```

- [ ] **Step 2: Run test, verify it fails**

```bash
uv run pytest tests/infrastructure/test_database_blocked_on_jobs.py::test_blocked_on_job_ids_round_trips_through_update_and_read -v
```
Expected: FAIL — `TaskRecord` has no `blocked_on_job_ids` field, or `get_task` doesn't surface it.

- [ ] **Step 3: Add field to `TaskRecord` + extend SELECT in `get_task`**

In `src/infrastructure/database.py`, locate `class TaskRecord` (or equivalent dataclass that `get_task` returns). Add `blocked_on_job_ids: str | None = None` to its fields.

Find every `SELECT ... FROM tasks` (especially in `get_task` at line ~666 and the parent-walking helpers). Add `blocked_on_job_ids` to the column list. In the row→TaskRecord mapping, pass `blocked_on_job_ids=row["blocked_on_job_ids"]`.

The existing pattern for `dispatched_from_thread_id` is the template (`database.py:684, 724, 870` — all three sites need symmetric additions).

`update_task(**fields)` is generic kwargs — no change needed there, but verify the column name is whitelisted if there is a whitelist (search for `update_task` allowed-keys; if absent, no change).

- [ ] **Step 4: Run test, verify it passes**

```bash
uv run pytest tests/infrastructure/test_database_blocked_on_jobs.py::test_blocked_on_job_ids_round_trips_through_update_and_read -v
```
Expected: PASS.

Also run the full DB test suite to check nothing regressed:

```bash
uv run pytest tests/infrastructure/ -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/infrastructure/test_database_blocked_on_jobs.py
git commit -m "feat(db): wire blocked_on_job_ids into TaskRecord read/write paths"
```

---

## Task 4: Add `Database.get_job_status` method

**Files:**
- Modify: `src/infrastructure/database.py`
- Test: `tests/infrastructure/test_database_blocked_on_jobs.py`

- [ ] **Step 1: Write failing test**

Append to `tests/infrastructure/test_database_blocked_on_jobs.py`:

```python
def test_get_job_status_terminal_and_running():
    """get_job_status returns the jobs.status string, or None if unknown."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)
        # Insert two jobs directly via the existing jobs-insert path.
        # If tests/ already has a jobs-insert helper, reuse it; otherwise use
        # raw SQL since this test scope is the DB layer.
        conn = db._conn
        conn.execute(
            "INSERT INTO jobs (id, task_id, agent_name, title, script_text, "
            "interpreter, status, created_at) VALUES "
            "('JOB-001', 'TASK-001', 'agent', 't', 's', 'bash', 'completed', "
            "'2026-05-28T00:00:00')"
        )
        conn.execute(
            "INSERT INTO jobs (id, task_id, agent_name, title, script_text, "
            "interpreter, status, created_at) VALUES "
            "('JOB-002', 'TASK-001', 'agent', 't', 's', 'bash', 'running', "
            "'2026-05-28T00:00:00')"
        )
        conn.commit()

        assert db.get_job_status("JOB-001") == "completed"
        assert db.get_job_status("JOB-002") == "running"
        assert db.get_job_status("JOB-999") is None
```

- [ ] **Step 2: Run test, verify it fails**

```bash
uv run pytest tests/infrastructure/test_database_blocked_on_jobs.py::test_get_job_status_terminal_and_running -v
```
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'get_job_status'`.

- [ ] **Step 3: Implement `get_job_status`**

In `src/infrastructure/database.py`, near the other `get_*` methods, add:

```python
@_synchronized
def get_job_status(self, job_id: str) -> str | None:
    """Return jobs.status for the given job id, or None if not present.

    Used by the blocked-on-job predicate-check in _maybe_resume_blocked_task
    and by run_step_impl's entry-state branch (spec §5.1, §5.4).
    """
    row = self._conn.execute(
        "SELECT status FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    return row["status"] if row is not None else None
```

- [ ] **Step 4: Run test, verify it passes**

```bash
uv run pytest tests/infrastructure/test_database_blocked_on_jobs.py::test_get_job_status_terminal_and_running -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/infrastructure/test_database_blocked_on_jobs.py
git commit -m "feat(db): add Database.get_job_status for predicate checks"
```

---

## Task 5: Add `Database.list_tasks_blocked_on_jobs` method

**Files:**
- Modify: `src/infrastructure/database.py`
- Test: `tests/infrastructure/test_database_blocked_on_jobs.py`

- [ ] **Step 1: Write failing test**

Append to `tests/infrastructure/test_database_blocked_on_jobs.py`:

```python
def test_list_tasks_blocked_on_jobs_filters_correctly():
    """Returns only ids of BLOCKED+BLOCKED_ON_JOB tasks; excludes other blocked."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        db = Database(db_path)

        # Three tasks: one blocked-on-job, one blocked-escalated, one in_progress.
        for tid, status, bk, jids in (
            ("TASK-A", TaskStatus.BLOCKED, BlockKind.BLOCKED_ON_JOB, '["JOB-1"]'),
            ("TASK-B", TaskStatus.BLOCKED, BlockKind.ESCALATED,     None),
            ("TASK-C", TaskStatus.IN_PROGRESS, None,                  None),
        ):
            db.insert_task(TaskRecord(
                id=tid, team="engineering", brief="t",
                status=status, parent_task_id=None,
            ))
            if bk is not None:
                db.update_task(tid, status=status, block_kind=bk,
                              blocked_on_job_ids=jids)

        result = db.list_tasks_blocked_on_jobs()
        assert set(result) == {"TASK-A"}
```

- [ ] **Step 2: Run test, verify it fails**

```bash
uv run pytest tests/infrastructure/test_database_blocked_on_jobs.py::test_list_tasks_blocked_on_jobs_filters_correctly -v
```
Expected: FAIL — method missing.

- [ ] **Step 3: Implement `list_tasks_blocked_on_jobs`**

In `src/infrastructure/database.py`:

```python
@_synchronized
def list_tasks_blocked_on_jobs(self) -> list[str]:
    """Return ids of tasks currently in BLOCKED + BLOCKED_ON_JOB state.

    Used by startup recovery (spec §5.7) to re-evaluate the predicate after
    `recover_orphaned_running_jobs` force-fails any leftovers.
    """
    rows = self._conn.execute(
        "SELECT id FROM tasks WHERE status = ? AND block_kind = ?",
        (TaskStatus.BLOCKED.value, BlockKind.BLOCKED_ON_JOB.value),
    ).fetchall()
    return [row["id"] for row in rows]
```

- [ ] **Step 4: Run test, verify it passes**

```bash
uv run pytest tests/infrastructure/test_database_blocked_on_jobs.py::test_list_tasks_blocked_on_jobs_filters_correctly -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/infrastructure/test_database_blocked_on_jobs.py
git commit -m "feat(db): add Database.list_tasks_blocked_on_jobs for startup recovery"
```

---

## Task 6: Add the three audit log methods

**Files:**
- Modify: `src/infrastructure/audit_logger.py`
- Test: `tests/infrastructure/test_audit_logger_blocked_on_jobs.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/infrastructure/test_audit_logger_blocked_on_jobs.py`:

```python
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database


@pytest.fixture
def audit():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        yield AuditLogger(db), db


def test_log_task_blocked_on_jobs(audit):
    logger, db = audit
    logger.log_task_blocked_on_jobs(
        task_id="TASK-1", agent="engineering_worker",
        blocking_job_ids=["JOB-12", "JOB-13"],
        output_summary_excerpt="Waiting for migration verification",
    )
    rows = db.get_audit_logs("TASK-1")
    assert len(rows) == 1
    assert rows[0]["action"] == "task_blocked_on_jobs"
    import json
    payload = json.loads(rows[0]["payload"])
    assert payload["blocking_job_ids"] == ["JOB-12", "JOB-13"]
    assert payload["agent"] == "engineering_worker"
    assert payload["output_summary_excerpt"] == "Waiting for migration verification"


def test_log_task_resumed_from_jobs(audit):
    logger, db = audit
    logger.log_task_resumed_from_jobs(
        task_id="TASK-1",
        blocking_job_ids=["JOB-12", "JOB-13"],
        trigger="job_terminal",
        triggering_job_id="JOB-13",
        job_outcomes={"JOB-12": "completed", "JOB-13": "failed"},
    )
    rows = db.get_audit_logs("TASK-1")
    assert len(rows) == 1
    assert rows[0]["action"] == "task_resumed_from_jobs"
    import json
    payload = json.loads(rows[0]["payload"])
    assert payload["trigger"] == "job_terminal"
    assert payload["triggering_job_id"] == "JOB-13"
    assert payload["job_outcomes"] == {"JOB-12": "completed", "JOB-13": "failed"}


def test_log_task_resume_skipped_empty_job_list(audit):
    logger, db = audit
    logger.log_task_resume_skipped(
        task_id="TASK-1", reason="empty_job_list",
        blocked_on_job_ids_raw="[]",
    )
    rows = db.get_audit_logs("TASK-1")
    assert len(rows) == 1
    assert rows[0]["action"] == "task_resume_skipped"
    import json
    payload = json.loads(rows[0]["payload"])
    assert payload["reason"] == "empty_job_list"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/infrastructure/test_audit_logger_blocked_on_jobs.py -v
```
Expected: 3 failures, each `AttributeError: 'AuditLogger' object has no attribute 'log_task_*'`.

- [ ] **Step 3: Add the three methods**

In `src/infrastructure/audit_logger.py`, after `log_orchestration_step` (around line 316):

```python
def log_task_blocked_on_jobs(
    self,
    task_id: str,
    agent: str,
    blocking_job_ids: list[str],
    output_summary_excerpt: str,
) -> None:
    """Written when run_step_impl transitions a task to BLOCKED+BLOCKED_ON_JOB
    in response to report.status=blocked + report.waiting_on_job_ids non-empty.
    Spec §7.
    """
    self._db.insert_audit_log(
        task_id=task_id,
        agent=agent,
        action="task_blocked_on_jobs",
        payload={
            "agent": agent,
            "blocking_job_ids": blocking_job_ids,
            "output_summary_excerpt": output_summary_excerpt,
        },
    )

def log_task_resumed_from_jobs(
    self,
    task_id: str,
    blocking_job_ids: list[str],
    trigger: str,
    triggering_job_id: str | None,
    job_outcomes: dict[str, str],
) -> None:
    """Written immediately after try_claim_for_step wins on a BLOCKED+BLOCKED_ON_JOB
    row, signalling the resume happened. Read by the resume header injector.
    Spec §5.2, §7.
    """
    self._db.insert_audit_log(
        task_id=task_id,
        agent="orchestrator",
        action="task_resumed_from_jobs",
        payload={
            "blocking_job_ids": blocking_job_ids,
            "trigger": trigger,
            "triggering_job_id": triggering_job_id,
            "job_outcomes": job_outcomes,
        },
    )

def log_task_resume_skipped(
    self,
    task_id: str,
    reason: str,
    blocked_on_job_ids_raw: str | None = None,
) -> None:
    """Diagnostic-only: written when the resume helper returns False with
    reason=empty_job_list (the only audited skip reason). Other no-op cases
    are silent for log-volume reasons. Spec §7.
    """
    payload: dict[str, object] = {"reason": reason}
    if blocked_on_job_ids_raw is not None:
        payload["blocked_on_job_ids_raw"] = blocked_on_job_ids_raw
    self._db.insert_audit_log(
        task_id=task_id,
        agent="orchestrator",
        action="task_resume_skipped",
        payload=payload,
    )
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/infrastructure/test_audit_logger_blocked_on_jobs.py -v
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/audit_logger.py tests/infrastructure/test_audit_logger_blocked_on_jobs.py
git commit -m "feat(audit): add blocked-on-job audit log methods"
```

---

## Task 7: Add `waiting_on_job_ids` to `CompletionReport` and `CompletionBody`

**Files:**
- Modify: `src/models.py:70` — `CompletionReport`
- Modify: `src/daemon/routes/tasks.py:223` — `CompletionBody`
- Test: `tests/daemon/test_completion_route_blocked_on_jobs.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/daemon/test_completion_route_blocked_on_jobs.py`:

```python
from __future__ import annotations

from src.models import CompletionReport


def test_completion_report_default_waiting_on_job_ids_is_empty():
    """waiting_on_job_ids defaults to empty list when omitted."""
    report = CompletionReport(
        task_id="TASK-1", agent="a", status="completed",
        confidence=80, output_summary="done",
    )
    assert report.waiting_on_job_ids == []


def test_completion_report_accepts_waiting_on_job_ids():
    """waiting_on_job_ids deserializes from a list of strings."""
    report = CompletionReport(
        task_id="TASK-1", agent="a", status="blocked",
        confidence=0, output_summary="waiting",
        waiting_on_job_ids=["JOB-12", "JOB-13"],
    )
    assert report.waiting_on_job_ids == ["JOB-12", "JOB-13"]
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/daemon/test_completion_route_blocked_on_jobs.py -v
```
Expected: FAIL with `extra fields not permitted` or `AttributeError`.

- [ ] **Step 3: Add the field to `CompletionReport`**

Edit `src/models.py:70` (the `CompletionReport` class):

```python
class CompletionReport(BaseModel):
    task_id: str
    agent: str
    status: str
    confidence: int = Field(ge=0, le=100)
    output_summary: str
    decision: NextStep | None = None
    risks_flagged: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    suggested_reviewer_focus: list[str] = Field(default_factory=list)
    artifact_dir: str | None = None
    waiting_on_job_ids: list[str] = Field(default_factory=list)  # spec §6.1
```

Also edit `src/daemon/routes/tasks.py:223` (the `CompletionBody` class) and add the same field:

```python
class CompletionBody(BaseModel):
    session_id: str
    agent: str
    status: str
    confidence: int
    output_summary: str
    decision: dict | None = None
    risks_flagged: list[str] = []
    dependencies: list[str] = []
    suggested_reviewer_focus: list[str] = []
    artifact_dir: str | None = None
    waiting_on_job_ids: list[str] = []  # spec §6.1
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/daemon/test_completion_route_blocked_on_jobs.py -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/models.py src/daemon/routes/tasks.py tests/daemon/test_completion_route_blocked_on_jobs.py
git commit -m "feat(models): add waiting_on_job_ids to CompletionReport and CompletionBody"
```

---

## Task 8: Extend `TaskQueue.enqueue` with optional metadata

**Files:**
- Modify: `src/daemon/queue.py:30-80` — `TaskQueue` class + `_worker_loop`
- Modify: `src/daemon/state.py` or wherever `_Dispatcher` is implemented — `run_step(slug, task_id, metadata?)` signature update
- Test: `tests/daemon/test_queue_metadata.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/daemon/test_queue_metadata.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from src.daemon.queue import TaskQueue


class _RecordingDispatcher:
    def __init__(self):
        self.calls: list[tuple] = []

    def run_step(self, slug: str, task_id: str, metadata=None) -> None:
        self.calls.append(("run_step", slug, task_id, metadata))

    def heartbeat(self, slug: str, task_id: str) -> None:
        pass


@pytest.mark.asyncio
async def test_enqueue_carries_metadata_to_run_step():
    q = TaskQueue()
    disp = _RecordingDispatcher()
    q.start(disp, worker_count=1)
    try:
        q.enqueue("org-a", "TASK-1", metadata={"trigger": "job_terminal",
                                                "triggering_job_id": "JOB-5"})
        # Wait for the worker to drain
        for _ in range(50):
            if disp.calls:
                break
            await asyncio.sleep(0.01)
        assert disp.calls == [("run_step", "org-a", "TASK-1",
                              {"trigger": "job_terminal",
                               "triggering_job_id": "JOB-5"})]
    finally:
        await q.stop()


@pytest.mark.asyncio
async def test_enqueue_without_metadata_passes_none():
    q = TaskQueue()
    disp = _RecordingDispatcher()
    q.start(disp, worker_count=1)
    try:
        q.enqueue("org-a", "TASK-2")
        for _ in range(50):
            if disp.calls:
                break
            await asyncio.sleep(0.01)
        assert disp.calls == [("run_step", "org-a", "TASK-2", None)]
    finally:
        await q.stop()
```

(The `q.start` and `q.stop` calls assume the existing TaskQueue API — read the existing file once before implementing to confirm method names; adjust the test to match if needed.)

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/daemon/test_queue_metadata.py -v
```
Expected: FAIL — `enqueue()` got unexpected keyword `metadata` OR `run_step` signature mismatch.

- [ ] **Step 3: Extend the queue**

Edit `src/daemon/queue.py`:

Change the internal queue type:

```python
self._queue: asyncio.Queue[tuple[str, str, dict | None]] = asyncio.Queue()
```

Extend `enqueue` (and the `put_nowait` alias):

```python
def enqueue(self, slug: str, task_id: str, *, metadata: dict | None = None) -> None:
    self._queue.put_nowait((slug, task_id, metadata))

def put_nowait(self, slug: str, task_id: str, *, metadata: dict | None = None) -> None:
    self.enqueue(slug, task_id, metadata=metadata)
```

Update `_worker_loop` to unpack 3-tuple and pass `metadata` to `run_step`:

```python
async def _worker_loop(self, dispatcher: _Dispatcher) -> None:
    loop = asyncio.get_running_loop()
    while not self._stopping:
        slug, task_id, metadata = await self._queue.get()
        hb = asyncio.create_task(self._heartbeat(dispatcher, slug, task_id))
        try:
            await loop.run_in_executor(
                None, dispatcher.run_step, slug, task_id, metadata,
            )
        # ... (rest unchanged)
```

Update the `_Dispatcher` Protocol:

```python
class _Dispatcher(Protocol):
    def run_step(self, slug: str, task_id: str, metadata: dict | None = None) -> None: ...
    def heartbeat(self, slug: str, task_id: str) -> None: ...
```

Then find the concrete dispatcher (search: `class.*Dispatcher` or `def run_step` in `src/daemon/`). It's likely on `DaemonState`. Update its `run_step` signature to accept `metadata: dict | None = None` and pass it through to `Orchestrator.run_step`.

Then find `Orchestrator.run_step` and update its signature to accept and stash `metadata` on a per-task side-channel for `run_step_impl` to read (e.g., `orch._pending_resume_metadata: dict[str, dict] = {}` populated before the inner call, popped inside `run_step_impl`).

Implementation sketch for `src/orchestrator/orchestrator.py` (adjust to actual file):

```python
def run_step(self, task_id: str, metadata: dict | None = None) -> None:
    if metadata is not None:
        self._pending_resume_metadata[task_id] = metadata
    try:
        run_step_impl(self, task_id)
    finally:
        # Always pop — if run_step_impl didn't consume it (e.g., predicate
        # not satisfied this pickup), it's stale for the next pickup.
        self._pending_resume_metadata.pop(task_id, None)
```

Where `self._pending_resume_metadata` is initialized in `__init__` as `{}`.

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/daemon/test_queue_metadata.py -v
uv run pytest tests/orchestrator/ tests/daemon/ -v
```
Expected: new tests PASS, existing tests still pass.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/queue.py src/daemon/state.py src/orchestrator/ tests/daemon/test_queue_metadata.py
git commit -m "feat(queue): thread optional resume metadata through TaskQueue → run_step"
```

---

## Task 9: Add the read-only resume helper `_maybe_resume_blocked_task`

**Files:**
- Modify: `src/orchestrator/run_step.py` — add helper next to `_maybe_post_thread_followup`
- Test: `tests/orchestrator/test_resume_helper.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/orchestrator/test_resume_helper.py`:

```python
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.infrastructure.database import Database
from src.models import BlockKind, TaskRecord, TaskStatus
from src.orchestrator.run_step import _maybe_resume_blocked_task


def _make_orch(db: Database):
    """Minimal orchestrator stub for helper unit-tests."""
    orch = MagicMock()
    orch._db = db
    orch._audit = MagicMock()
    # The helper enqueues via orch._task_queue.enqueue(slug, task_id, metadata=...)
    # so wire a recording mock.
    orch._task_queue = MagicMock()
    orch._org_slug = "org-a"
    return orch


def _insert_task_blocked_on_jobs(db: Database, task_id: str, job_ids: list[str]):
    db.insert_task(TaskRecord(
        id=task_id, team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    db.update_task(
        task_id,
        status=TaskStatus.BLOCKED,
        block_kind=BlockKind.BLOCKED_ON_JOB,
        blocked_on_job_ids=json.dumps(job_ids),
    )


def _insert_job(db: Database, job_id: str, task_id: str, status: str):
    db._conn.execute(
        "INSERT INTO jobs (id, task_id, agent_name, title, script_text, "
        "interpreter, status, created_at) VALUES (?, ?, 'a', 't', 's', 'bash', ?, "
        "'2026-05-28T00:00:00')",
        (job_id, task_id, status),
    )
    db._conn.commit()


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(Path(tmp) / "t.db")


def test_resumes_when_single_job_completed(db):
    _insert_task_blocked_on_jobs(db, "TASK-1", ["JOB-1"])
    _insert_job(db, "JOB-1", "TASK-1", "completed")
    orch = _make_orch(db)
    result = _maybe_resume_blocked_task(
        orch, "TASK-1", trigger="job_terminal", triggering_job_id="JOB-1",
    )
    assert result is True
    orch._task_queue.enqueue.assert_called_once_with(
        "org-a", "TASK-1",
        metadata={"trigger": "job_terminal", "triggering_job_id": "JOB-1"},
    )


def test_resumes_when_all_terminal_mixed_states(db):
    _insert_task_blocked_on_jobs(db, "TASK-1", ["JOB-1", "JOB-2", "JOB-3"])
    _insert_job(db, "JOB-1", "TASK-1", "completed")
    _insert_job(db, "JOB-2", "TASK-1", "failed")
    _insert_job(db, "JOB-3", "TASK-1", "rejected")
    orch = _make_orch(db)
    assert _maybe_resume_blocked_task(orch, "TASK-1", trigger="job_terminal",
                                       triggering_job_id="JOB-3") is True
    orch._task_queue.enqueue.assert_called_once()


def test_does_not_resume_when_one_still_running(db):
    _insert_task_blocked_on_jobs(db, "TASK-1", ["JOB-1", "JOB-2"])
    _insert_job(db, "JOB-1", "TASK-1", "completed")
    _insert_job(db, "JOB-2", "TASK-1", "running")
    orch = _make_orch(db)
    assert _maybe_resume_blocked_task(orch, "TASK-1", trigger="job_terminal",
                                       triggering_job_id="JOB-1") is False
    orch._task_queue.enqueue.assert_not_called()
    orch._audit.log_task_resume_skipped.assert_not_called()


def test_no_audit_when_task_not_blocked(db):
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    orch = _make_orch(db)
    assert _maybe_resume_blocked_task(orch, "TASK-1", trigger="job_terminal",
                                       triggering_job_id="JOB-1") is False
    orch._task_queue.enqueue.assert_not_called()
    orch._audit.log_task_resume_skipped.assert_not_called()


def test_no_audit_when_block_kind_is_escalated(db):
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    db.update_task("TASK-1", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.ESCALATED)
    orch = _make_orch(db)
    assert _maybe_resume_blocked_task(orch, "TASK-1", trigger="job_terminal",
                                       triggering_job_id="JOB-1") is False
    orch._task_queue.enqueue.assert_not_called()
    orch._audit.log_task_resume_skipped.assert_not_called()


def test_audits_skip_when_empty_job_list(db):
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    db.update_task("TASK-1", status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.BLOCKED_ON_JOB,
                   blocked_on_job_ids="[]")
    orch = _make_orch(db)
    assert _maybe_resume_blocked_task(orch, "TASK-1", trigger="job_terminal",
                                       triggering_job_id="JOB-1") is False
    orch._task_queue.enqueue.assert_not_called()
    orch._audit.log_task_resume_skipped.assert_called_once_with(
        task_id="TASK-1", reason="empty_job_list",
        blocked_on_job_ids_raw="[]",
    )


def test_helper_does_not_mutate_task_status(db):
    """Helper is read-only — never writes to tasks.status / block_kind."""
    _insert_task_blocked_on_jobs(db, "TASK-1", ["JOB-1"])
    _insert_job(db, "JOB-1", "TASK-1", "completed")
    orch = _make_orch(db)
    _maybe_resume_blocked_task(orch, "TASK-1", trigger="job_terminal",
                                triggering_job_id="JOB-1")
    after = db.get_task("TASK-1")
    assert after.status == TaskStatus.BLOCKED  # NOT in_progress
    assert after.block_kind == BlockKind.BLOCKED_ON_JOB
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/orchestrator/test_resume_helper.py -v
```
Expected: 7 ImportError or AttributeError failures — helper doesn't exist yet.

- [ ] **Step 3: Implement the helper**

In `src/orchestrator/run_step.py`, near `_maybe_post_thread_followup` (around line 1156), add:

```python
def _maybe_resume_blocked_task(
    orch: "Orchestrator",
    task_id: str,
    *,
    trigger: str,
    triggering_job_id: str | None,
) -> bool:
    """Check predicate (all blocking jobs terminal) and enqueue if satisfied.

    READ-ONLY: does NOT mutate task state. The state transition happens at
    run_step_impl step 3's CAS when the worker picks up the enqueued task.

    Returns True if it enqueued; False otherwise. Idempotent — extra enqueues
    are harmless (run_step_impl's CAS admits exactly one).

    Spec: docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md §5.4
    """
    import json as _json

    db = orch._db
    audit = orch._audit
    task = db.get_task(task_id)
    if task is None:
        return False
    if task.status != TaskStatus.BLOCKED or task.block_kind != BlockKind.BLOCKED_ON_JOB:
        # Steady state — silent no-op (no audit; would drown logs).
        return False

    try:
        job_ids = _json.loads(task.blocked_on_job_ids or "[]")
    except _json.JSONDecodeError:
        audit.log_task_resume_skipped(
            task_id=task_id, reason="empty_job_list",
            blocked_on_job_ids_raw=task.blocked_on_job_ids,
        )
        return False
    if not job_ids:
        audit.log_task_resume_skipped(
            task_id=task_id, reason="empty_job_list",
            blocked_on_job_ids_raw=task.blocked_on_job_ids,
        )
        return False

    # Predicate: every job in {completed, failed, rejected}. Non-terminal:
    # {pending, running}. Unknown (None) is treated as non-terminal — the
    # block_on_jobs branch in §5.3 catches deleted jobs at submit time; if a
    # job somehow vanishes after that, leaving the task blocked is safer than
    # silently dropping the wait.
    _TERMINAL = {"completed", "failed", "rejected"}
    for jid in job_ids:
        status = db.get_job_status(jid)
        if status not in _TERMINAL:
            return False

    # All terminal — enqueue. The CAS at run_step_impl step 3 will write the
    # audit row; if it loses to a concurrent enqueue, that's harmless.
    orch._task_queue.enqueue(
        orch._org_slug, task_id,
        metadata={"trigger": trigger, "triggering_job_id": triggering_job_id},
    )
    return True
```

Make sure the imports at the top of `run_step.py` already cover `TaskStatus` and `BlockKind` (they should — used elsewhere in the file).

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/orchestrator/test_resume_helper.py -v
```
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/orchestrator/test_resume_helper.py
git commit -m "feat(orchestrator): add read-only _maybe_resume_blocked_task helper"
```

---

## Task 10: Add new entry-state branch in `run_step_impl` step 1

**Files:**
- Modify: `src/orchestrator/run_step.py:45-58` — step 1 entry-state check
- Test: `tests/orchestrator/test_run_step_blocked_on_job.py` (new)

- [ ] **Step 1: Write failing test**

Create `tests/orchestrator/test_run_step_blocked_on_job.py`:

```python
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import BlockKind, TaskRecord, TaskStatus
from src.orchestrator.run_step import run_step_impl


@pytest.fixture
def db_and_orch():
    """Minimal orchestrator + DB stub that runs run_step_impl through step 1.

    The agent-invocation site (_run_agent at line ~102) is mocked so we don't
    actually spawn a subprocess. Tests target the step-1 entry-check branch.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "t.db")
        audit = AuditLogger(db)
        orch = MagicMock()
        orch._db = db
        orch._audit = audit
        orch._settings = MagicMock(max_orchestration_steps=50)
        orch._task_queue = MagicMock()
        orch._org_slug = "org-a"
        orch._pending_resume_metadata = {}
        orch.teams = MagicMock(is_team_manager=MagicMock(return_value=False))
        yield db, orch


def _insert_blocked_on_jobs(db: Database, task_id: str, job_ids: list[str]):
    db.insert_task(TaskRecord(
        id=task_id, team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    db.update_task(task_id, status=TaskStatus.BLOCKED,
                   block_kind=BlockKind.BLOCKED_ON_JOB,
                   blocked_on_job_ids=json.dumps(job_ids))


def _insert_job(db: Database, jid: str, status: str, task_id="TASK-1"):
    db._conn.execute(
        "INSERT INTO jobs (id, task_id, agent_name, title, script_text, "
        "interpreter, status, created_at) VALUES (?, ?, 'a', 't', 's', 'bash', ?, "
        "'2026-05-28T00:00:00')", (jid, task_id, status))
    db._conn.commit()


def test_step1_admits_blocked_on_job_when_all_terminal(db_and_orch):
    db, orch = db_and_orch
    _insert_blocked_on_jobs(db, "TASK-1", ["JOB-1"])
    _insert_job(db, "JOB-1", "completed")

    # Mock _run_agent to short-circuit before actually invoking an agent.
    # If step 1 admits, control reaches the CAS at step 3 which would transition
    # the row to in_progress. We assert the row WAS claimed.
    with patch("src.orchestrator.run_step._run_agent",
               side_effect=RuntimeError("we don't actually run the agent here")):
        with pytest.raises(RuntimeError):
            run_step_impl(orch, "TASK-1")

    after = db.get_task("TASK-1")
    # CAS at step 3 should have flipped status to IN_PROGRESS
    assert after.status == TaskStatus.IN_PROGRESS


def test_step1_skips_when_blocking_job_still_running(db_and_orch):
    db, orch = db_and_orch
    _insert_blocked_on_jobs(db, "TASK-1", ["JOB-1"])
    _insert_job(db, "JOB-1", "running")

    # Should return without invoking the agent (and without flipping status).
    run_step_impl(orch, "TASK-1")

    after = db.get_task("TASK-1")
    assert after.status == TaskStatus.BLOCKED
    assert after.block_kind == BlockKind.BLOCKED_ON_JOB


def test_step1_skips_when_blocked_on_job_ids_empty(db_and_orch):
    db, orch = db_and_orch
    _insert_blocked_on_jobs(db, "TASK-1", [])

    run_step_impl(orch, "TASK-1")

    after = db.get_task("TASK-1")
    assert after.status == TaskStatus.BLOCKED  # unchanged
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/orchestrator/test_run_step_blocked_on_job.py -v
```
Expected: at least `test_step1_admits_blocked_on_job_when_all_terminal` FAILs because the current step-1 check returns at line 53-58 for any `BLOCKED+BLOCKED_ON_JOB` row.

- [ ] **Step 3: Add the entry-state branch**

In `src/orchestrator/run_step.py`, modify step 1 (lines 45-58). The existing block:

```python
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
```

Becomes:

```python
# ---- 1. Verify entry state ----
if task.status == TaskStatus.PENDING:
    pass  # eligible
elif task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.DELEGATED:
    children = [db.get_task(cid) for cid in db.get_children(task_id)]
    if any(c is None or c.status not in TERMINAL_STATES for c in children):
        logger.debug("run_step %s: child still running, skipping", task_id)
        return
elif task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.BLOCKED_ON_JOB:
    # New entry: blocked-on-job task whose predicate (all jobs terminal)
    # may now be satisfied. Re-check defensively against the live job table —
    # the helper that enqueued us may have raced with a job state change.
    # Spec §5.1.
    import json as _json
    try:
        job_ids = _json.loads(task.blocked_on_job_ids or "[]")
    except _json.JSONDecodeError:
        logger.debug("run_step %s: blocked_on_job_ids unparseable", task_id)
        return
    if not job_ids:
        logger.debug("run_step %s: blocked_on_job_ids empty", task_id)
        return
    _TERMINAL = {"completed", "failed", "rejected"}
    for jid in job_ids:
        jstatus = db.get_job_status(jid)
        if jstatus not in _TERMINAL:
            logger.debug(
                "run_step %s: blocking job %s still in-flight (status=%s)",
                task_id, jid, jstatus,
            )
            return
    # All terminal — fall through to step 2 + step 3.
else:
    logger.debug(
        "run_step %s: not eligible (status=%s, block_kind=%s)",
        task_id, task.status, task.block_kind,
    )
    return
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/orchestrator/test_run_step_blocked_on_job.py -v
```
Expected: 3 PASS (the first one passes because step 1 admits, control hits step 3 CAS which flips to IN_PROGRESS, then `_run_agent` raises and the test catches it).

Run the full run_step test suite to check no regression:

```bash
uv run pytest tests/orchestrator/ -v
```
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/orchestrator/test_run_step_blocked_on_job.py
git commit -m "feat(orchestrator): add BLOCKED_ON_JOB entry-state branch in run_step step 1"
```

---

## Task 11: Add CAS-win audit hook for `task_resumed_from_jobs`

**Files:**
- Modify: `src/orchestrator/run_step.py` — between step 3 CAS-win and step 4 prompt-build (around line 94)
- Modify: `src/orchestrator/orchestrator.py` — `run_step(task_id, metadata)` already populated `_pending_resume_metadata`; here we consume it
- Test: `tests/orchestrator/test_run_step_blocked_on_job.py`

- [ ] **Step 1: Write failing test**

Append to `tests/orchestrator/test_run_step_blocked_on_job.py`:

```python
def test_cas_win_writes_task_resumed_from_jobs_audit_row(db_and_orch):
    """After step-1 admits and step-3 CAS wins, an audit row exists carrying
    the trigger/triggering_job_id from queue metadata."""
    db, orch = db_and_orch
    _insert_blocked_on_jobs(db, "TASK-1", ["JOB-1", "JOB-2"])
    _insert_job(db, "JOB-1", "completed")
    _insert_job(db, "JOB-2", "failed")

    # Simulate metadata having been passed in via the queue → run_step → impl chain.
    orch._pending_resume_metadata["TASK-1"] = {
        "trigger": "job_terminal", "triggering_job_id": "JOB-2",
    }

    with patch("src.orchestrator.run_step._run_agent",
               side_effect=RuntimeError("don't actually run agent")):
        with pytest.raises(RuntimeError):
            run_step_impl(orch, "TASK-1")

    rows = db.get_audit_logs("TASK-1")
    resumed = [r for r in rows if r["action"] == "task_resumed_from_jobs"]
    assert len(resumed) == 1
    import json
    payload = json.loads(resumed[0]["payload"])
    assert payload["trigger"] == "job_terminal"
    assert payload["triggering_job_id"] == "JOB-2"
    assert payload["blocking_job_ids"] == ["JOB-1", "JOB-2"]
    assert payload["job_outcomes"] == {"JOB-1": "completed", "JOB-2": "failed"}


def test_cas_win_writes_audit_with_unknown_trigger_when_metadata_missing(db_and_orch):
    """If no metadata was attached (manual revisit re-entry, defensive case),
    audit row still fires with trigger='unknown'."""
    db, orch = db_and_orch
    _insert_blocked_on_jobs(db, "TASK-1", ["JOB-1"])
    _insert_job(db, "JOB-1", "completed")

    with patch("src.orchestrator.run_step._run_agent",
               side_effect=RuntimeError("don't actually run agent")):
        with pytest.raises(RuntimeError):
            run_step_impl(orch, "TASK-1")

    rows = db.get_audit_logs("TASK-1")
    resumed = [r for r in rows if r["action"] == "task_resumed_from_jobs"]
    assert len(resumed) == 1
    import json
    payload = json.loads(resumed[0]["payload"])
    assert payload["trigger"] == "unknown"
    assert payload["triggering_job_id"] is None
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/orchestrator/test_run_step_blocked_on_job.py::test_cas_win_writes_task_resumed_from_jobs_audit_row -v
```
Expected: FAIL — no audit row exists.

- [ ] **Step 3: Add the audit hook**

In `src/orchestrator/run_step.py`, around line 95 (after `try_claim_for_step` succeeds at line 88), add the audit hook. The current code:

```python
claimed = db.try_claim_for_step(
    task_id,
    expected_status=task.status,
    expected_block_kind=task.block_kind,
    new_count=next_count,
)
if not claimed:
    logger.debug(...)
    return

# ---- 4. Run the agent subprocess ----
agent = task.assigned_agent or _default_agent_for_root(orch, task)
```

Insert between the `if not claimed` block and the `# ---- 4.` comment:

```python
# Spec §5.2: write task_resumed_from_jobs audit row immediately after the
# CAS wins on a BLOCKED+BLOCKED_ON_JOB → IN_PROGRESS transition. The
# prompt-build at step 4 reads this row to inject BLOCKED-JOBS-RESULTS.
if (task.status == TaskStatus.BLOCKED
        and task.block_kind == BlockKind.BLOCKED_ON_JOB):
    import json as _json
    job_ids = _json.loads(task.blocked_on_job_ids or "[]")
    job_outcomes = {jid: (db.get_job_status(jid) or "unknown")
                    for jid in job_ids}
    metadata = orch._pending_resume_metadata.pop(task_id, None) or {}
    orch._audit.log_task_resumed_from_jobs(
        task_id=task_id,
        blocking_job_ids=job_ids,
        trigger=metadata.get("trigger", "unknown"),
        triggering_job_id=metadata.get("triggering_job_id"),
        job_outcomes=job_outcomes,
    )
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/orchestrator/test_run_step_blocked_on_job.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/orchestrator/test_run_step_blocked_on_job.py
git commit -m "feat(orchestrator): write task_resumed_from_jobs audit row at CAS-win"
```

---

## Task 12: Add block-on-jobs branch in the self-blocked report handler

**Files:**
- Modify: `src/orchestrator/run_step.py:191-204` — the `if report.status == "blocked":` block
- Test: `tests/orchestrator/test_run_step_blocked_on_job.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/orchestrator/test_run_step_blocked_on_job.py`:

```python
from src.models import CompletionReport


def test_block_on_jobs_branch_transitions_in_place(db_and_orch):
    """report.status=blocked + non-empty waiting_on_job_ids → row goes to
    BLOCKED+BLOCKED_ON_JOB (NOT _fail)."""
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.PENDING, parent_task_id=None,
        assigned_agent="engineering_worker",
    ))
    _insert_job(db, "JOB-1", "running")

    fake_result = MagicMock()
    fake_report = CompletionReport(
        task_id="TASK-1", agent="engineering_worker", status="blocked",
        confidence=0, output_summary="Waiting on migration",
        waiting_on_job_ids=["JOB-1"],
    )
    with patch("src.orchestrator.run_step._run_agent",
               return_value=(fake_result, fake_report)):
        run_step_impl(orch, "TASK-1")

    after = db.get_task("TASK-1")
    assert after.status == TaskStatus.BLOCKED
    assert after.block_kind == BlockKind.BLOCKED_ON_JOB
    assert after.blocked_on_job_ids == '["JOB-1"]'

    rows = db.get_audit_logs("TASK-1")
    blocked_audits = [r for r in rows if r["action"] == "task_blocked_on_jobs"]
    assert len(blocked_audits) == 1


def test_block_on_jobs_branch_immediate_resume_when_jobs_already_terminal(db_and_orch):
    """Submit-time race: block submitted but all jobs already done → helper
    enqueues immediately."""
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.PENDING, parent_task_id=None,
        assigned_agent="engineering_worker",
    ))
    _insert_job(db, "JOB-1", "completed")  # already terminal at submit time

    fake_result = MagicMock()
    fake_report = CompletionReport(
        task_id="TASK-1", agent="engineering_worker", status="blocked",
        confidence=0, output_summary="Waiting on migration",
        waiting_on_job_ids=["JOB-1"],
    )
    with patch("src.orchestrator.run_step._run_agent",
               return_value=(fake_result, fake_report)):
        run_step_impl(orch, "TASK-1")

    # Helper should have enqueued via orch._task_queue.enqueue
    orch._task_queue.enqueue.assert_called_once_with(
        "org-a", "TASK-1",
        metadata={"trigger": "block_submit", "triggering_job_id": None},
    )


def test_block_on_jobs_with_missing_job_falls_back_to_fail(db_and_orch):
    """If a JOB id in waiting_on_job_ids doesn't exist (deleted between route
    and worker pickup), degrade to existing _fail path."""
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.PENDING, parent_task_id=None,
        assigned_agent="engineering_worker",
    ))
    # NOTE: no JOB-999 inserted

    fake_result = MagicMock()
    fake_report = CompletionReport(
        task_id="TASK-1", agent="engineering_worker", status="blocked",
        confidence=0, output_summary="Waiting", waiting_on_job_ids=["JOB-999"],
    )
    with patch("src.orchestrator.run_step._run_agent",
               return_value=(fake_result, fake_report)):
        run_step_impl(orch, "TASK-1")

    after = db.get_task("TASK-1")
    assert after.status == TaskStatus.FAILED  # _fail path
    assert "JOB-999 not found" in (after.note or "")
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/orchestrator/test_run_step_blocked_on_job.py -v -k "block_on_jobs"
```
Expected: 3 FAIL — current branch always `_fail`s on blocked.

- [ ] **Step 3: Add the block-on-jobs branch**

In `src/orchestrator/run_step.py:191-204`, replace the existing block:

```python
if report.status == "blocked":
    note = f"self-blocked: {report.output_summary}"
    _fail(orch, task_id, note=note)
    _enqueue_parent_if_waiting(orch, task_id)
    _notify_failure_if_eligible(
        orch, task_id, failure_kind="self_blocked",
        failure_note=note, auto_revisit_spawned=False,
        last_summary=report.output_summary or "",
    )
    _maybe_post_thread_followup(
        orch, task_id,
        status=TaskStatus.FAILED, auto_revisit_spawned=False,
    )
    return
```

With:

```python
if report.status == "blocked":
    if report.waiting_on_job_ids:
        # Spec §5.3: block-on-jobs branch. In-place transition, NOT _fail.
        import json as _json
        deduped = sorted(set(report.waiting_on_job_ids))
        # Defensive re-validation: a job could have been deleted between the
        # route POST and run_step_impl consuming the report (extremely
        # unlikely; jobs are write-once + terminal-frozen). Degrade gracefully.
        for jid in deduped:
            if db.get_job_status(jid) is None:
                note = f"self-blocked but job {jid} not found"
                _fail(orch, task_id, note=note)
                _enqueue_parent_if_waiting(orch, task_id)
                _notify_failure_if_eligible(
                    orch, task_id, failure_kind="self_blocked",
                    failure_note=note, auto_revisit_spawned=False,
                    last_summary=report.output_summary or "",
                )
                _maybe_post_thread_followup(
                    orch, task_id,
                    status=TaskStatus.FAILED, auto_revisit_spawned=False,
                )
                return
        db.update_task(
            task_id,
            status=TaskStatus.BLOCKED,
            block_kind=BlockKind.BLOCKED_ON_JOB,
            blocked_on_job_ids=_json.dumps(deduped),
            note=report.output_summary,
        )
        orch._audit.log_task_blocked_on_jobs(
            task_id=task_id, agent=agent,
            blocking_job_ids=deduped,
            output_summary_excerpt=(report.output_summary or "")[:200],
        )
        # Immediate predicate check (caller B). Spec §5.6: runs HERE, after
        # the agent session has already been cleared by submit_completion.
        # No session race.
        _maybe_resume_blocked_task(
            orch, task_id,
            trigger="block_submit", triggering_job_id=None,
        )
        return
    # Existing escalated path (waiting_on_job_ids empty).
    note = f"self-blocked: {report.output_summary}"
    _fail(orch, task_id, note=note)
    _enqueue_parent_if_waiting(orch, task_id)
    _notify_failure_if_eligible(
        orch, task_id, failure_kind="self_blocked",
        failure_note=note, auto_revisit_spawned=False,
        last_summary=report.output_summary or "",
    )
    _maybe_post_thread_followup(
        orch, task_id,
        status=TaskStatus.FAILED, auto_revisit_spawned=False,
    )
    return
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/orchestrator/test_run_step_blocked_on_job.py -v
uv run pytest tests/orchestrator/ -v
```
Expected: all PASS, existing tests not regressed.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/orchestrator/test_run_step_blocked_on_job.py
git commit -m "feat(orchestrator): add block-on-jobs branch in self-blocked handler"
```

---

## Task 13: Add `_blocked_jobs_resume_header_if_applicable` + inject in `_build_agent_prompt`

**Files:**
- Modify: `src/orchestrator/run_step.py` — add helper next to `_revisit_header_if_applicable` (around line 470), inject in `_build_agent_prompt` (around line 417)
- Test: `tests/orchestrator/test_run_step_blocked_on_job.py`

- [ ] **Step 1: Write failing test**

Append to `tests/orchestrator/test_run_step_blocked_on_job.py`:

```python
from src.orchestrator.run_step import _blocked_jobs_resume_header_if_applicable


def test_resume_header_rendered_after_audit_row(db_and_orch):
    """If a task_resumed_from_jobs audit row exists newer than the most
    recent orchestration_step row, header is rendered."""
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    _insert_job(db, "JOB-1", "completed")
    orch._audit.log_task_resumed_from_jobs(
        task_id="TASK-1",
        blocking_job_ids=["JOB-1"],
        trigger="job_terminal",
        triggering_job_id="JOB-1",
        job_outcomes={"JOB-1": "completed"},
    )

    header = _blocked_jobs_resume_header_if_applicable(orch, "TASK-1")
    assert header is not None
    assert "BLOCKED-JOBS-RESULTS" in header
    assert "JOB-1" in header
    assert "completed" in header
    assert "grassland jobs show JOB-1" in header


def test_resume_header_skipped_after_step_runs(db_and_orch):
    """Once an orchestration_step row exists newer than the audit row, the
    header stops rendering."""
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    _insert_job(db, "JOB-1", "completed")
    orch._audit.log_task_resumed_from_jobs(
        task_id="TASK-1",
        blocking_job_ids=["JOB-1"],
        trigger="job_terminal",
        triggering_job_id="JOB-1",
        job_outcomes={"JOB-1": "completed"},
    )
    # The step that consumed the resume writes its own orchestration_step.
    orch._audit.log_orchestration_step("TASK-1", 1, {"action": "done"})

    header = _blocked_jobs_resume_header_if_applicable(orch, "TASK-1")
    assert header is None


def test_resume_header_none_when_no_audit_row(db_and_orch):
    db, orch = db_and_orch
    db.insert_task(TaskRecord(
        id="TASK-1", team="engineering", brief="t",
        status=TaskStatus.IN_PROGRESS, parent_task_id=None,
    ))
    header = _blocked_jobs_resume_header_if_applicable(orch, "TASK-1")
    assert header is None
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/orchestrator/test_run_step_blocked_on_job.py::test_resume_header_rendered_after_audit_row -v
```
Expected: FAIL — ImportError or AttributeError.

- [ ] **Step 3: Add the helper + injection**

In `src/orchestrator/run_step.py`, near `_revisit_header_if_applicable` (around line 470), add:

```python
def _blocked_jobs_resume_header_if_applicable(
    orch: "Orchestrator", task_id: str,
) -> str | None:
    """Render BLOCKED-JOBS-RESULTS header iff the task's most recent
    `task_resumed_from_jobs` audit row is newer than its most recent
    `orchestration_step` row. The first step run after a resume sees the
    header; the step writes its own `orchestration_step` row on completion,
    causing the next step's prompt-build to skip it.

    Spec: §6.4.
    """
    import json as _json

    rows = orch._db.get_audit_logs(task_id)
    # Walk newest-first to find the most recent task_resumed_from_jobs.
    # NOTE: get_audit_logs returns rows ordered by insertion (verify in your
    # repo; this code reverses defensively).
    resumed = None
    last_step = None
    for r in reversed(rows):
        if resumed is None and r["action"] == "task_resumed_from_jobs":
            resumed = r
        if last_step is None and r["action"] == "orchestration_step":
            last_step = r
        if resumed is not None and last_step is not None:
            break
    if resumed is None:
        return None
    if last_step is not None and last_step["ts"] >= resumed["ts"]:
        # Consumed already.
        return None

    payload = _json.loads(resumed["payload"])
    job_ids = payload.get("blocking_job_ids", [])
    outcomes = payload.get("job_outcomes", {})

    lines = [
        "=== BLOCKED-JOBS-RESULTS (system) ===",
        f"You self-blocked on {', '.join(job_ids)}. They are now terminal:",
        "",
    ]
    for jid in job_ids:
        status = outcomes.get(jid, "unknown")
        lines.append(f"  {jid}  {status}")
        lines.append(f"          → grassland jobs show {jid}")
        lines.append(f"          → grassland jobs output {jid}")
    lines.append("")
    lines.append("Re-read your task brief; decide whether to proceed, retry, or escalate.")
    lines.append("======================================")
    return "\n".join(lines)
```

Then inject at the prompt-build site (around line 417 where `_revisit_header_if_applicable` is called):

```python
revisit = _revisit_header_if_applicable(orch, task.id)
resume_header = _blocked_jobs_resume_header_if_applicable(orch, task.id)
# ... insert into prompt similarly to revisit; render revisit first, then resume,
# matching the order documented in spec §11 ("revisit header first, then resume header").
```

The exact integration depends on how the existing code splices headers — read lines 410-450 of `run_step.py` once and follow the local pattern. The spec doesn't mandate exact position beyond "both can stack, revisit first".

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/orchestrator/test_run_step_blocked_on_job.py -v -k resume_header
```
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/orchestrator/test_run_step_blocked_on_job.py
git commit -m "feat(orchestrator): add BLOCKED-JOBS-RESULTS resume header injection"
```

---

## Task 14: Add `waiting_on_job_ids` validation matrix in completion route

**Files:**
- Modify: `src/daemon/routes/tasks.py:254-301` — `submit_completion` handler
- Test: `tests/daemon/test_completion_route_blocked_on_jobs.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/daemon/test_completion_route_blocked_on_jobs.py` (this file needs a real FastAPI test client + an org fixture; follow the pattern of `tests/daemon/test_*_routes.py`):

```python
# Use the existing test fixtures for spawning an org-bound TestClient.
# The path will depend on the repo's conftest.py — search for "test_completion"
# in tests/daemon/ and reuse the same fixture.

def test_status_completed_with_waiting_on_job_ids_rejects(test_client, org_a):
    # ... build a valid session_id and task in org_a, then POST completion
    # with status=completed and waiting_on_job_ids=["JOB-1"]
    resp = test_client.post(
        f"/api/v1/orgs/{org_a.slug}/tasks/TASK-1/completion",
        json={
            "session_id": "<active>", "agent": "engineering_worker",
            "status": "completed", "confidence": 80,
            "output_summary": "done",
            "waiting_on_job_ids": ["JOB-1"],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "waiting_on_job_ids_requires_blocked"


def test_status_blocked_with_unknown_job_404(test_client, org_a):
    resp = test_client.post(
        f"/api/v1/orgs/{org_a.slug}/tasks/TASK-1/completion",
        json={
            "session_id": "<active>", "agent": "engineering_worker",
            "status": "blocked", "confidence": 0,
            "output_summary": "waiting",
            "waiting_on_job_ids": ["JOB-DOES-NOT-EXIST"],
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "job_not_found"


def test_status_blocked_with_job_owned_by_other_task_400(test_client, org_a):
    # ... insert JOB-X tied to TASK-OTHER, then POST completion on TASK-1
    resp = test_client.post(
        f"/api/v1/orgs/{org_a.slug}/tasks/TASK-1/completion",
        json={
            "session_id": "<active>", "agent": "engineering_worker",
            "status": "blocked", "confidence": 0,
            "output_summary": "waiting",
            "waiting_on_job_ids": ["JOB-X"],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "job_not_owned_by_task"


def test_status_blocked_with_empty_list_400(test_client, org_a):
    """If somebody passes an empty array explicitly (vs omitting the field),
    we reject so the run_step branch doesn't see an empty list."""
    resp = test_client.post(
        f"/api/v1/orgs/{org_a.slug}/tasks/TASK-1/completion",
        json={
            "session_id": "<active>", "agent": "engineering_worker",
            "status": "blocked", "confidence": 0,
            "output_summary": "waiting",
            "waiting_on_job_ids": ["JOB-1", "JOB-1"],  # deduped → ["JOB-1"]
        },
    )
    # Verify dedup landed in the persisted task_result
    # (assert on the row Stores)


def test_happy_path_persists_without_mutating_tasks_status(test_client, org_a):
    """Critical: the route does NOT mutate tasks.status. The orchestrator
    branch picks up the report and does the transition."""
    # ... happy-path POST
    resp = test_client.post(...)
    assert resp.status_code == 200
    # Task row should still be in_progress; the orchestrator hasn't run yet.
    row = org_a.db.get_task("TASK-1")
    assert row.status == TaskStatus.IN_PROGRESS
```

(These tests sketch the assertions; the engineer fills in the existing fixture setup. Search `tests/daemon/` for `def test_submit_completion` to find the pattern.)

- [ ] **Step 2: Run tests, verify they fail**

```bash
uv run pytest tests/daemon/test_completion_route_blocked_on_jobs.py -v
```
Expected: FAIL — current route doesn't validate `waiting_on_job_ids` at all.

- [ ] **Step 3: Add validation in `submit_completion`**

Edit `src/daemon/routes/tasks.py:254-301`. After the existing session-mismatch guards but BEFORE the `insert_task_result` call, add:

```python
# Spec §6.2: validate waiting_on_job_ids if present.
if body.waiting_on_job_ids:
    if body.status != "blocked":
        raise HTTPException(
            status_code=400,
            detail={
                "code": "waiting_on_job_ids_requires_blocked",
                "got_status": body.status,
            },
        )
    deduped = sorted(set(body.waiting_on_job_ids))
    if not deduped:
        raise HTTPException(
            status_code=400,
            detail={"code": "empty_waiting_on_job_ids"},
        )
    for jid in deduped:
        owner = org.db.get_job_owner_task_id(jid)
        if owner is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "job_not_found", "job_id": jid},
            )
        if owner != task_id:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "job_not_owned_by_task",
                    "job_id": jid, "owner_task_id": owner,
                },
            )
    # Persist the deduped list in the task_result so run_step_impl sees the
    # cleaned-up payload (defensive — also avoids "list contained dup" downstream).
    body.waiting_on_job_ids = deduped
```

This depends on `Database.get_job_owner_task_id(job_id) -> str | None`. Add it to `src/infrastructure/database.py`:

```python
@_synchronized
def get_job_owner_task_id(self, job_id: str) -> str | None:
    """Return jobs.task_id for the given job id, or None if not present."""
    row = self._conn.execute(
        "SELECT task_id FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    return row["task_id"] if row is not None else None
```

Then plumb `waiting_on_job_ids` from `CompletionBody` through to the `task_result` row (and onward to the in-memory `CompletionReport` that `run_step_impl` reads). This requires extending `Database.insert_task_result` and the row→report shape. Search for how `risks_flagged` is plumbed (it's a list field that round-trips) and follow the same pattern.

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/daemon/test_completion_route_blocked_on_jobs.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/tasks.py src/infrastructure/database.py tests/daemon/test_completion_route_blocked_on_jobs.py
git commit -m "feat(routes): validate waiting_on_job_ids in completion route"
```

---

## Task 15: Add caller A — terminal hook in `jobs_runner.py`

**Files:**
- Modify: `src/daemon/jobs_runner.py` — after `run_job` produces `JobRunResult` (around line 349)
- Modify: `src/daemon/app.py` lifespan — wire `attach_resume_main_loop` similar to `attach_thread_queue`
- Test: extend `tests/daemon/test_jobs_runner.py` (existing file)

- [ ] **Step 1: Write failing test**

In `tests/daemon/test_jobs_runner.py` (existing) or a new sibling file, add:

```python
def test_terminal_hook_invokes_resume_helper_for_blocked_task(...):
    """When run_job produces a terminal result, the resume helper is invoked
    for every blocked-on-job task referencing this job."""
    # Setup: insert TASK-1 BLOCKED+BLOCKED_ON_JOB on JOB-1, run JOB-1 (mock
    # subprocess), confirm orch._task_queue.enqueue was called with task_id=TASK-1.
```

(Sketch only — the existing `test_jobs_runner.py` provides scaffolding.)

- [ ] **Step 2: Run test, verify it fails**

```bash
uv run pytest tests/daemon/test_jobs_runner.py -v -k resume
```
Expected: FAIL — no terminal hook yet.

- [ ] **Step 3: Implement the terminal hook**

In `src/daemon/jobs_runner.py`, around line 369 (just before `return result`), add the bridge call. The exact site depends on where the route persists the terminal status to the DB row — the bridge must fire AFTER `jobs.status` reaches its terminal value, so this may need to live in `routes/jobs.py` instead, at the post-`run_job` finalization site (search for `jobs.status='completed'` or similar).

Once you've located the right site, fire-and-forget bridge:

```python
import asyncio
# At module-level: bind the daemon's main loop once at lifespan startup, e.g.
# `attach_jobs_resume_main_loop(loop, orch_resolver)` similar to attach_thread_queue.

_resume_main_loop: asyncio.AbstractEventLoop | None = None
_orch_resolver: Callable[[str], Orchestrator | None] | None = None

def attach_jobs_resume_main_loop(loop, orch_resolver):
    global _resume_main_loop, _orch_resolver
    _resume_main_loop = loop
    _orch_resolver = orch_resolver


def _bridge_resume_check(slug: str, task_id: str, job_id: str) -> None:
    if _resume_main_loop is None or _orch_resolver is None:
        # Tests / pre-lifespan path — silent no-op
        return
    orch = _orch_resolver(slug)
    if orch is None:
        return
    asyncio.run_coroutine_threadsafe(
        _resume_blocked_tasks_for_job(orch, job_id),
        _resume_main_loop,
    )


async def _resume_blocked_tasks_for_job(orch: Orchestrator, job_id: str) -> None:
    """Find tasks blocked on this job and invoke the resume helper for each."""
    db = orch._db
    rows = db._conn.execute(
        "SELECT id FROM tasks WHERE status='blocked' "
        "AND block_kind='blocked_on_job' "
        "AND blocked_on_job_ids LIKE ?",
        (f'%"{job_id}"%',),
    ).fetchall()
    for row in rows:
        from src.orchestrator.run_step import _maybe_resume_blocked_task
        _maybe_resume_blocked_task(
            orch, row["id"],
            trigger="job_terminal", triggering_job_id=job_id,
        )
```

Then in `src/daemon/app.py` lifespan startup, call `attach_jobs_resume_main_loop(asyncio.get_running_loop(), state.get_org_orchestrator)`.

In the actual terminal-transition site, call `_bridge_resume_check(org_slug, task_id, job_id)` once.

- [ ] **Step 4: Run tests, verify they pass**

```bash
uv run pytest tests/daemon/test_jobs_runner.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/daemon/jobs_runner.py src/daemon/app.py src/daemon/routes/jobs.py tests/daemon/test_jobs_runner.py
git commit -m "feat(jobs): bridge job terminal → resume helper (caller A)"
```

---

## Task 16: Add caller C — startup recovery scan in `app.py`

**Files:**
- Modify: `src/daemon/app.py` — lifespan startup, after `recover_orphaned_running_jobs`
- Test: integration test in Task 22 covers this; add a small unit-style test too

- [ ] **Step 1: Write failing test**

Create `tests/daemon/test_startup_recovery_blocked_on_jobs.py`:

```python
def test_startup_scan_resumes_tasks_with_all_terminal_jobs(...):
    """After daemon startup, a BLOCKED+BLOCKED_ON_JOB task whose listed jobs
    are all terminal gets enqueued."""
    # Setup: org with TASK-1 BLOCKED+BLOCKED_ON_JOB(["JOB-1"]) and JOB-1 status='failed'
    # Call the recovery scan; assert TaskQueue.enqueue was called.
```

- [ ] **Step 2: Run test, verify it fails**

- [ ] **Step 3: Implement the scan**

In `src/daemon/app.py` lifespan, after `recover_orphaned_running_jobs(...)` runs per-org:

```python
for org in state.orgs.values():
    for task_id in org.db.list_tasks_blocked_on_jobs():
        from src.orchestrator.run_step import _maybe_resume_blocked_task
        _maybe_resume_blocked_task(
            org.orchestrator, task_id,
            trigger="startup_recovery", triggering_job_id=None,
        )
```

- [ ] **Step 4: Run tests, verify they pass**

- [ ] **Step 5: Commit**

```bash
git add src/daemon/app.py tests/daemon/test_startup_recovery_blocked_on_jobs.py
git commit -m "feat(daemon): startup recovery scan for blocked-on-job tasks"
```

---

## Task 17: Rewrite the "After submitting" section of `protocol/skills/jobs/SKILL.md`

**Files:**
- Modify: `protocol/skills/jobs/SKILL.md`

- [ ] **Step 1: Read the current skill**

```bash
cat protocol/skills/jobs/SKILL.md | head -60
```

- [ ] **Step 2: Replace the "After submitting" section**

Find the section beginning "## After submitting" (or its equivalent) and replace with the text from spec §6.3:

```markdown
## After submitting — waiting on jobs

When you need to wait for jobs to finish before proceeding (either
`review_required=true` waiting for founder approval, or `review_required=false`
jobs you can't move forward without), submit your block via `report-completion`
with `status=blocked` and `waiting_on_job_ids` populated:

```json
{
  "status": "blocked",
  "confidence": 0,
  "output_summary": "Waiting for JOB-12 and JOB-13 before I can verify the migration ran cleanly.",
  "waiting_on_job_ids": ["JOB-12", "JOB-13"]
}
```

The system resumes your task automatically once **every** listed job reaches a
terminal state (`completed`, `failed`, or `rejected`). When you resume, your
bootstrap doc will include a `BLOCKED-JOBS-RESULTS` section listing each job's
status and `grassland jobs show JOB-NNN` / `grassland jobs output JOB-NNN`
commands to fetch full output. **You don't poll.**

If you need to stay in-session for a fast `review_required=false` job, the
existing `grassland jobs wait JOB-NNN --timeout-seconds 30` pattern still works.
Prefer block-and-resume for any wait long enough to risk session timeout.
```

- [ ] **Step 3: Commit**

```bash
git add protocol/skills/jobs/SKILL.md
git commit -m "docs(jobs-skill): document block-on-jobs + auto-resume flow"
```

---

## Task 18: Extend `grassland details` with blocked-on-jobs subsection

**Files:**
- Modify: `src/cli.py` (or wherever `grassland details` is implemented)
- Test: `tests/cli/test_details_blocked_on_jobs.py` (new)

- [ ] **Step 1: Locate the details printer**

```bash
grep -n "def cmd_details\|details" src/cli.py | head -10
```

- [ ] **Step 2: Write failing test**

Create `tests/cli/test_details_blocked_on_jobs.py`:

```python
def test_details_renders_blocked_on_jobs_subsection(...):
    """`grassland details TASK-1` on a BLOCKED+BLOCKED_ON_JOB task shows the
    JOB-NNN list with each job's current status."""
    # Setup TASK-1 BLOCKED+BLOCKED_ON_JOB(["JOB-1", "JOB-2"]) with JOB-1=running, JOB-2=completed
    # Call the details handler, capture stdout
    # Assert output contains "Blocked on jobs:" and both JOB ids with statuses
```

- [ ] **Step 3: Implement**

In the details printer, when `task.block_kind == BlockKind.BLOCKED_ON_JOB`:

```python
if task.block_kind == BlockKind.BLOCKED_ON_JOB:
    import json as _json
    print("Blocked on jobs:")
    for jid in _json.loads(task.blocked_on_job_ids or "[]"):
        status = db.get_job_status(jid) or "unknown"
        print(f"  {jid}  {status}")
```

- [ ] **Step 4: Run tests, verify they pass**

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/cli/test_details_blocked_on_jobs.py
git commit -m "feat(cli): render Blocked-on-jobs subsection in grassland details"
```

---

## Task 19: Integration test — autonomous (`review_required=false`) flow

**Files:**
- Create: `tests/integration/test_task_blocked_by_job_autonomous.py`

- [ ] **Step 1: Write the integration test**

```python
from __future__ import annotations

import pytest

# Re-use the existing integration scaffolding. The two-stage fake_claude pattern
# already lets a test script choose what the agent does on each session.

pytestmark = pytest.mark.integration


def test_blocks_on_job_then_auto_resumes(daemon_with_org, fake_claude_plan_env):
    """End-to-end: agent submits a fast `review_required=false` job, blocks,
    job completes, task auto-resumes, agent's next session sees the header."""
    # Stage 1: agent submits a job (e.g. echo hi) + report-completion status=blocked
    #          with waiting_on_job_ids=[<JOB-id>].
    # Stage 2: agent sees BLOCKED-JOBS-RESULTS in prompt, asserts JOB output is
    #          visible via grassland jobs show, then completes.
    # Test: dispatch TASK; wait for terminal; assert task=completed AND the
    # agent's stage 2 was invoked.
```

(Use existing fixtures in `tests/integration/conftest.py`. Reference
`tests/integration/test_jobs_persistent.py` for the daemon-spawn pattern.)

- [ ] **Step 2: Run, verify it passes**

```bash
uv run pytest tests/integration/test_task_blocked_by_job_autonomous.py -v -m integration
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_task_blocked_by_job_autonomous.py
git commit -m "test(integration): end-to-end blocked-by-job autonomous flow"
```

---

## Task 20: Integration test — `review_required=true` flow

**Files:**
- Create: `tests/integration/test_task_blocked_by_job_review_required.py`

- [ ] **Step 1: Write the integration test**

```python
pytestmark = pytest.mark.integration


def test_review_required_approved(daemon_with_org, fake_claude_plan_env):
    """Agent submits review_required=true + blocks → founder approves → job
    runs → task resumes with completed outcome."""


def test_review_required_rejected(daemon_with_org, fake_claude_plan_env):
    """Agent blocks → founder rejects via route → task resumes with rejected
    in the header (NOT escalated)."""
```

- [ ] **Step 2: Run, verify it passes**

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_task_blocked_by_job_review_required.py
git commit -m "test(integration): review_required approve/reject paths"
```

---

## Task 21: Integration test — multi-job flow

**Files:**
- Create: `tests/integration/test_task_blocked_by_job_multi.py`

- [ ] **Step 1: Write the integration test**

```python
pytestmark = pytest.mark.integration


def test_multi_job_resume_waits_for_all(daemon_with_org, fake_claude_plan_env):
    """Agent blocks on JOB-A and JOB-B; JOB-A finishes fast, JOB-B slow.
    Verify task stays blocked until JOB-B completes; triggering_job_id is JOB-B."""
```

- [ ] **Step 2: Run, verify it passes**

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_task_blocked_by_job_multi.py
git commit -m "test(integration): multi-job blocked-by-job flow"
```

---

## Task 22: Integration test — startup recovery

**Files:**
- Create: `tests/integration/test_task_blocked_by_job_startup_recovery.py`

- [ ] **Step 1: Write the integration test**

```python
pytestmark = pytest.mark.integration


def test_recovery_after_daemon_crash(daemon_with_org, fake_claude_plan_env):
    """Agent submits + blocks, daemon is killed mid-block (jobs left in
    'running'), daemon restarts → recover_orphaned_running_jobs force-fails
    them → startup recovery scan resumes the task."""
```

- [ ] **Step 2: Run, verify it passes**

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_task_blocked_by_job_startup_recovery.py
git commit -m "test(integration): startup recovery for blocked-on-job tasks"
```

---

## Task 23: Update CLAUDE.md (three sections)

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add new item 17 under "Implementation Order — Done"**

Find the line "Open:" under "Implementation Order". Insert immediately before it:

```markdown
17. **Task blocked-by-job** — agent self-blocks with `waiting_on_job_ids: ["JOB-NNN", ...]` in the `report-completion` payload; system auto-resumes the task when every listed job is terminal. Per-org `tasks.blocked_on_job_ids` JSON column + new `block_kind=blocked_on_job` value. Spec: `docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md`.
```

Renumber "Founder dashboard" and "Persistent agents" to 18 and 19.

- [ ] **Step 2: Extend "Task status vocabularies"**

Replace the `block_kind` enumeration to include the new value:

```markdown
The orchestrator-owned `TaskStatus` is `{pending, in_progress, blocked, completed, failed}` with `block_kind` (`delegated` | `escalated` | `blocked_on_job`).
```

- [ ] **Step 3: Append invariant to "Jobs" section**

Find the existing **"Non-obvious invariants:"** list in the "Jobs (founder-approved + agent-autonomous)" section. Append:

```markdown
- **Auto-resume on terminal supersedes founder revisit for blocked-on-job tasks.** The original spec (§2) listed "no task wakes itself" as a non-goal; the 2026-05-28 task-blocked-by-job design reverses that. Agents now self-block with `waiting_on_job_ids` and resume automatically. The `grassland revisit` path remains valid as a founder-driven override (e.g., "give up on JOB-X, start over").
```

- [ ] **Step 4: Add a new top-level section**

Between "Jobs" and "Feishu notifications", add a new section titled **"Task blocked-by-job (system auto-resumes from job terminals)"** documenting the three resume sites, the call-order invariant with thread followup, and the backward-read invariant on revisit chains. Mirror §5 and §6.4–6.5 of the spec. Match the "Non-obvious invariants" style of adjacent sections.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): document task blocked-by-job invariants"
```

---

## Final verification

After Task 23, run the full test suite to confirm:

```bash
uv run pytest tests/ -v                 # unit tests
uv run pytest tests/ -v -m integration  # end-to-end
```

Expected: every test passes. Run the contract test to confirm the OpenAPI snapshot still matches (no new public route was added; only request body fields changed, which the snapshot will catch):

```bash
GRASSLAND_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py -v
```

If the snapshot diff is just the new `waiting_on_job_ids` field on `CompletionBody`, commit the updated snapshot:

```bash
git add tests/contract/openapi.json
git commit -m "chore(openapi): regen snapshot for waiting_on_job_ids field"
```

Re-run `gitnexus analyze` (per CLAUDE.md re-index trigger) since this is a meaningful CLAUDE.md update:

```bash
npx gitnexus analyze --force --embeddings
```
