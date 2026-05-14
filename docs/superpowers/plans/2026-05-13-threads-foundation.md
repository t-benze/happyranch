# Threads Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the data layer, HTTP API, agent invocation runtime, agent skill, and CLI subcommands for email-style multi-agent threads. The Textual TUI (`opc threads` with no subcommand) is deferred to a follow-up plan (`docs/superpowers/plans/2026-05-13-threads-tui.md`); after this plan, threads are fully usable from the CLI and from agent callbacks.

**Architecture:** Threads are a new daemon-owned resource living alongside talks. Founder composes a thread via `opc threads compose`; the daemon enqueues `ThreadInvocation` records with daemon-minted tokens for each addressed agent; a `ThreadInvocationRunner` spawns headless executor subprocesses (Claude / Codex / opencode) with the full thread context + invocation token; agents reply, decline, or dispatch tasks via callbacks that validate the token. Archive uses an `open → archiving → archived` two-phase transition so close-out callbacks can land before the thread is finalized.

**Tech Stack:** Python 3.13, FastAPI, Pydantic v2, SQLite (WAL mode), `uv` for dependencies, `pytest` for tests (unit + integration with fake CLIs), existing `state.db_lock` + `OrgDep` patterns.

**Spec reference:** `docs/superpowers/specs/2026-05-13-threads-design.md`.

**Out of scope for this plan (deferred to TUI plan):** Textual app, `opc threads` no-subcommand launcher, SSE event payload formatting beyond minimal contract. Founders interact via CLI subcommands only after this plan.

---

## File Structure

**Create:**
- `src/daemon/routes/threads.py` — all HTTP route handlers.
- `src/infrastructure/thread_store.py` — atomic transcript file writes (mirrors `talk_store.py`).
- `src/daemon/thread_queue.py` — `ThreadQueue` + worker pool.
- `src/daemon/thread_runner.py` — `ThreadInvocationRunner` (prompt builder + outcome observer).
- `src/daemon/thread_forward.py` — pure helpers for building forwarded compose bodies from talks/threads.
- `src/orchestrator/org_config.py` — extend with `threads:` block parsing.
- `protocol/skills/thread/SKILL.md` — agent-side instruction file.
- `tests/test_thread_db.py` — DB CRUD unit tests.
- `tests/test_threads_routes.py` — HTTP route unit tests (TestClient).
- `tests/test_thread_runner.py` — runner unit tests with fake executor.
- `tests/test_thread_forward.py` — forward-body helper tests.
- `tests/integration/test_threads_e2e.py` — full daemon + fake CLI flows.

**Modify:**
- `src/models.py` — add `ThreadStatus`, `ThreadInvocationStatus`, `ThreadInvocationPurpose`, `ThreadRecord`, `ThreadParticipant`, `ThreadMessage`, `ThreadMessageKind`, `ThreadInvocation`. Add `dispatched_from_thread_id` to `TaskRecord`.
- `src/infrastructure/database.py` — schema additions + CRUD methods.
- `src/infrastructure/audit_logger.py` — new `log_thread_*` methods.
- `src/daemon/app.py` — mount the threads router; spawn `ThreadQueue` worker task in lifespan.
- `src/daemon/state.py` (or `org_state.py`) — hold per-org `ThreadQueue` reference.
- `src/cli.py` — new `threads` subcommand group with `compose`, `send`, `list`, `show`, `invite`, `archive`, `abandon`, `extend`, `forward`, `reply`, `decline`, `dispatch`, `close-out`.
- `src/orchestrator/workspace_adapters.py` — copy `protocol/skills/thread/` into every workspace.
- `examples/orgs/hk-macau-tourism/org/config.yaml` — add example `threads:` block.

---

## Conventions used throughout this plan

- Every task block ends with a commit step. Commits use conventional-commit shape (`feat:`, `test:`, `refactor:`, etc.).
- All new code includes `from __future__ import annotations` per the project rule.
- All Pydantic v2 BaseModel classes.
- All DB methods use the existing `@_synchronized` decorator + `self._conn.execute(...)` pattern.
- All HTTP routes are async functions taking `slug: str` (auto-injected by FastAPI) and `org: OrgDep`.
- Test files: `pytest` collects `tests/test_*.py` automatically; integration tests under `tests/integration/` are excluded by default and run with `uv run pytest -m integration`.
- Run unit tests after each task with: `uv run pytest tests/test_<file>.py -v`.

---

## Task 1: Schema migration — threads + thread_participants tables

**Files:**
- Modify: `src/infrastructure/database.py:70-195` (extend `_create_tables`)
- Modify: `src/infrastructure/database.py:240-274` (extend the idempotent ALTER list)

- [ ] **Step 1: Add the two table DDLs to `_create_tables`**

Inside the `executescript("""...""")` block in `_create_tables`, immediately after the `processed_event_ids` table block, append:

```python
            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                started_at TEXT NOT NULL,
                archived_at TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                forwarded_from_id TEXT,
                forwarded_from_kind TEXT,
                turn_cap INTEGER NOT NULL DEFAULT 500,
                turns_used INTEGER NOT NULL DEFAULT 0,
                summary TEXT,
                new_kb_slugs_json TEXT,
                transcript_path TEXT,
                archive_requested_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);
            CREATE INDEX IF NOT EXISTS idx_threads_started ON threads(started_at);

            CREATE TABLE IF NOT EXISTS thread_participants (
                thread_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                added_at TEXT NOT NULL,
                added_by TEXT NOT NULL,
                PRIMARY KEY (thread_id, agent_name),
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE INDEX IF NOT EXISTS idx_thread_participants_agent
                ON thread_participants(agent_name);
```

- [ ] **Step 2: Run the daemon's test for schema bootstrap**

Run: `uv run pytest tests/ -v -k "test_database or test_daemon" 2>&1 | tail -20`
Expected: no failures (new tables coexist with existing ones).

- [ ] **Step 3: Commit**

```bash
git add src/infrastructure/database.py
git commit -m "feat(db): add threads + thread_participants tables"
```

---

## Task 2: Schema migration — thread_messages + thread_invocations tables

**Files:**
- Modify: `src/infrastructure/database.py:70-195` (same `_create_tables` block)

- [ ] **Step 1: Append the two more table DDLs**

After the `thread_participants` block from Task 1, add:

```python
            CREATE TABLE IF NOT EXISTS thread_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                speaker TEXT NOT NULL,
                kind TEXT NOT NULL,
                body_markdown TEXT,
                addressed_to_json TEXT,
                decline_reason TEXT,
                system_payload_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_thread_messages_thread_seq
                ON thread_messages(thread_id, seq);

            CREATE TABLE IF NOT EXISTS thread_invocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                invocation_token TEXT NOT NULL UNIQUE,
                triggering_seq INTEGER NOT NULL,
                purpose TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                enqueued_at TEXT NOT NULL,
                started_at TEXT,
                consumed_at TEXT,
                session_id TEXT,
                dispatched_task_id TEXT,
                decline_reason TEXT,
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE INDEX IF NOT EXISTS idx_thread_invocations_token
                ON thread_invocations(invocation_token);
            CREATE INDEX IF NOT EXISTS idx_thread_invocations_thread
                ON thread_invocations(thread_id);
            CREATE INDEX IF NOT EXISTS idx_thread_invocations_pending
                ON thread_invocations(status) WHERE status = 'pending';
```

- [ ] **Step 2: Re-run schema tests**

Run: `uv run pytest tests/ -v -k "test_database or test_daemon" 2>&1 | tail -20`
Expected: still passing.

- [ ] **Step 3: Commit**

```bash
git add src/infrastructure/database.py
git commit -m "feat(db): add thread_messages + thread_invocations tables"
```

---

## Task 3: Schema migration — tasks.dispatched_from_thread_id column

**Files:**
- Modify: `src/infrastructure/database.py:242-283` (idempotent ALTER block)

- [ ] **Step 1: Add ALTER + index after the existing dispatched_from_talk_id block**

In `_create_tables`, locate the existing `"ALTER TABLE tasks ADD COLUMN dispatched_from_talk_id TEXT"` entry inside the tuple at lines ~256-260. After the tuple closes and the partial index for `dispatched_from_talk_id` is created (line ~283), add:

```python
        try:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN dispatched_from_thread_id TEXT"
            )
        except sqlite3.OperationalError:
            pass
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_dispatched_from_thread_id "
            "ON tasks(dispatched_from_thread_id) "
            "WHERE dispatched_from_thread_id IS NOT NULL"
        )
```

- [ ] **Step 2: Write a test that verifies mutual exclusion of the two dispatched_from_* fields**

Create `tests/test_thread_db.py`:

```python
from __future__ import annotations

import pytest

from src.infrastructure.database import Database
from src.models import TaskRecord


def test_dispatched_from_columns_are_independent(tmp_path):
    db = Database(tmp_path / "opc.db")
    # Both NULL — OK.
    db.insert_task(TaskRecord(id="TASK-001", brief="x"))
    # talk only — OK.
    db.insert_task(TaskRecord(id="TASK-002", brief="x", dispatched_from_talk_id="TALK-1"))
    # thread only — OK.
    db.insert_task(TaskRecord(id="TASK-003", brief="x", dispatched_from_thread_id="THR-1"))
```

- [ ] **Step 3: Run the test**

Run: `uv run pytest tests/test_thread_db.py::test_dispatched_from_columns_are_independent -v`
Expected: PASS (TaskRecord.dispatched_from_thread_id doesn't exist yet → ImportError or AttributeError).
If failing for that reason, that confirms we need Task 4 to add the model field.

- [ ] **Step 4: Commit**

```bash
git add src/infrastructure/database.py tests/test_thread_db.py
git commit -m "feat(db): add tasks.dispatched_from_thread_id column"
```

---

## Task 4: TaskRecord gains dispatched_from_thread_id field

**Files:**
- Modify: `src/models.py:40-63` (`TaskRecord`)

- [ ] **Step 1: Add the field next to dispatched_from_talk_id**

In `src/models.py`, in the `TaskRecord` class, immediately after the line `dispatched_from_talk_id: str | None = None`, add:

```python
    dispatched_from_thread_id: str | None = None
```

- [ ] **Step 2: Re-run the Task 3 test**

Run: `uv run pytest tests/test_thread_db.py::test_dispatched_from_columns_are_independent -v`
Expected: PASS.

- [ ] **Step 3: Update `insert_task` in `database.py` to write the new column**

Find the existing `def insert_task` method. Add the new column to the column list and parameter binding. The SQL should look like:

```python
            """INSERT INTO tasks (
                id, status, assigned_agent, team, brief, parent_task_id,
                revisit_of_task_id, dispatched_from_talk_id,
                dispatched_from_thread_id,
                block_kind, note, final_artifact_dir, orchestration_step_count,
                revision_count, session_timeout_seconds,
                created_at, updated_at, completed_at, cancelled_at, last_heartbeat
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
```

Add `task.dispatched_from_thread_id,` to the parameter tuple at the matching position. Also add the matching SELECT projection wherever tasks are read back into TaskRecord (look for `_row_to_task` or equivalent helper near `get_task`).

- [ ] **Step 4: Run the test again**

Run: `uv run pytest tests/test_thread_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/models.py src/infrastructure/database.py
git commit -m "feat(models): TaskRecord.dispatched_from_thread_id"
```

---

## Task 5: Enums + payload models

**Files:**
- Modify: `src/models.py` (add at the end)

- [ ] **Step 1: Add the enums**

Append to `src/models.py`:

```python
class ThreadStatus(StrEnum):
    OPEN = "open"
    ARCHIVING = "archiving"
    ARCHIVED = "archived"
    ABANDONED = "abandoned"


class ThreadMessageKind(StrEnum):
    MESSAGE = "message"
    DECLINE = "decline"
    SYSTEM = "system"


class ThreadInvocationStatus(StrEnum):
    PENDING = "pending"
    CONSUMED = "consumed"
    TIMEOUT = "timeout"
    FAILED = "failed"


class ThreadInvocationPurpose(StrEnum):
    REPLY = "reply"
    BOOTSTRAP = "bootstrap"
    CLOSE_OUT = "close_out"
```

- [ ] **Step 2: Add the record models**

Continuing in `src/models.py`:

```python
class ThreadRecord(BaseModel):
    id: str
    subject: str
    status: ThreadStatus = ThreadStatus.OPEN
    started_at: datetime = Field(default_factory=_now)
    archived_at: datetime | None = None
    forwarded_from_id: str | None = None
    forwarded_from_kind: str | None = None  # 'thread' | 'talk'
    turn_cap: int = 500
    turns_used: int = 0
    summary: str | None = None
    new_kb_slugs: list[str] = Field(default_factory=list)
    transcript_path: str | None = None
    archive_requested_at: datetime | None = None


class ThreadParticipant(BaseModel):
    thread_id: str
    agent_name: str
    added_at: datetime = Field(default_factory=_now)
    added_by: str = "founder"


class ThreadMessage(BaseModel):
    id: int | None = None
    thread_id: str
    seq: int
    speaker: str
    kind: ThreadMessageKind
    body_markdown: str | None = None
    addressed_to: list[str] | None = None
    decline_reason: str | None = None
    system_payload: dict | None = None
    created_at: datetime = Field(default_factory=_now)


class ThreadInvocation(BaseModel):
    id: int | None = None
    thread_id: str
    agent_name: str
    invocation_token: str
    triggering_seq: int
    purpose: ThreadInvocationPurpose
    status: ThreadInvocationStatus = ThreadInvocationStatus.PENDING
    enqueued_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    consumed_at: datetime | None = None
    session_id: str | None = None
    dispatched_task_id: str | None = None
    decline_reason: str | None = None
```

- [ ] **Step 3: Write a smoke test for the new models**

Append to `tests/test_thread_db.py`:

```python
from datetime import datetime

from src.models import (
    ThreadInvocation, ThreadInvocationPurpose, ThreadInvocationStatus,
    ThreadMessage, ThreadMessageKind, ThreadParticipant, ThreadRecord,
    ThreadStatus,
)


def test_thread_models_roundtrip():
    t = ThreadRecord(id="THR-001", subject="Refund policy")
    assert t.status is ThreadStatus.OPEN
    assert t.turn_cap == 500
    p = ThreadParticipant(thread_id="THR-001", agent_name="dev")
    assert p.added_by == "founder"
    m = ThreadMessage(
        thread_id="THR-001", seq=1, speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi",
        addressed_to=["@all"],
    )
    assert m.kind is ThreadMessageKind.MESSAGE
    inv = ThreadInvocation(
        thread_id="THR-001", agent_name="dev",
        invocation_token="abc", triggering_seq=1,
        purpose=ThreadInvocationPurpose.REPLY,
    )
    assert inv.status is ThreadInvocationStatus.PENDING
```

Run: `uv run pytest tests/test_thread_db.py::test_thread_models_roundtrip -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/models.py tests/test_thread_db.py
git commit -m "feat(models): ThreadRecord, ThreadMessage, ThreadInvocation, enums"
```

---

## Task 6: Database — next_thread_id allocator

**Files:**
- Modify: `src/infrastructure/database.py` (add near `next_talk_id`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_thread_db.py`:

```python
def test_next_thread_id_starts_at_one(tmp_path):
    db = Database(tmp_path / "opc.db")
    assert db.next_thread_id() == "THR-001"


def test_next_thread_id_uses_max_suffix(tmp_path):
    db = Database(tmp_path / "opc.db")
    db._conn.execute(
        "INSERT INTO threads (id, subject, started_at, status) "
        "VALUES ('THR-001', 's', '2026-01-01T00:00:00+00:00', 'archived')"
    )
    db._conn.execute(
        "INSERT INTO threads (id, subject, started_at, status) "
        "VALUES ('THR-005', 's', '2026-01-02T00:00:00+00:00', 'open')"
    )
    db._conn.commit()
    assert db.next_thread_id() == "THR-006"
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_thread_db.py::test_next_thread_id_starts_at_one -v`
Expected: FAIL (`AttributeError: 'Database' object has no attribute 'next_thread_id'`).

- [ ] **Step 3: Implement**

In `src/infrastructure/database.py`, immediately after `next_talk_id`, add:

```python
    @_synchronized
    def next_thread_id(self) -> str:
        """Return the next available THR-NNN id.

        Callers must hold DaemonState.db_lock across the next_thread_id() +
        insert_thread() pair to avoid duplicate IDs under concurrent requests
        (same requirement as next_task_id / next_talk_id).
        """
        cursor = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 5) AS INTEGER)) AS m "
            "FROM threads WHERE id GLOB 'THR-[0-9]*'"
        )
        n = (cursor.fetchone()["m"] or 0) + 1
        return f"THR-{n:03d}"
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_thread_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_thread_db.py
git commit -m "feat(db): next_thread_id allocator (MAX(suffix) pattern)"
```

---

## Task 7: Database — threads CRUD

**Files:**
- Modify: `src/infrastructure/database.py` (add after `next_thread_id`)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_thread_db.py`:

```python
def test_insert_and_get_thread(tmp_path):
    db = Database(tmp_path / "opc.db")
    t = ThreadRecord(id="THR-001", subject="Refund policy")
    db.insert_thread(t)
    got = db.get_thread("THR-001")
    assert got is not None
    assert got.id == "THR-001"
    assert got.subject == "Refund policy"
    assert got.status is ThreadStatus.OPEN
    assert got.turn_cap == 500


def test_get_thread_missing_returns_none(tmp_path):
    db = Database(tmp_path / "opc.db")
    assert db.get_thread("THR-404") is None


def test_list_threads_orders_by_started_desc(tmp_path):
    db = Database(tmp_path / "opc.db")
    a = ThreadRecord(id="THR-001", subject="a", started_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    b = ThreadRecord(id="THR-002", subject="b", started_at=datetime(2026, 1, 5, tzinfo=timezone.utc))
    db.insert_thread(a)
    db.insert_thread(b)
    rows = db.list_threads(limit=10)
    assert [r.id for r in rows] == ["THR-002", "THR-001"]
```

Also import `timezone` at the top of the test file: `from datetime import datetime, timezone`.

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_thread_db.py::test_insert_and_get_thread -v`
Expected: FAIL (`insert_thread` doesn't exist).

- [ ] **Step 3: Implement insert/get/list**

In `src/infrastructure/database.py`, after `next_thread_id`:

```python
    @_synchronized
    def insert_thread(self, t: ThreadRecord) -> None:
        self._conn.execute(
            """INSERT INTO threads (
                id, subject, started_at, archived_at, status,
                forwarded_from_id, forwarded_from_kind,
                turn_cap, turns_used, summary, new_kb_slugs_json,
                transcript_path, archive_requested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                t.id,
                t.subject,
                t.started_at.isoformat(),
                t.archived_at.isoformat() if t.archived_at else None,
                t.status.value,
                t.forwarded_from_id,
                t.forwarded_from_kind,
                t.turn_cap,
                t.turns_used,
                t.summary,
                json.dumps(t.new_kb_slugs) if t.new_kb_slugs else None,
                t.transcript_path,
                t.archive_requested_at.isoformat() if t.archive_requested_at else None,
            ),
        )
        self._conn.commit()

    def _row_to_thread(self, row) -> ThreadRecord:
        return ThreadRecord(
            id=row["id"],
            subject=row["subject"],
            status=ThreadStatus(row["status"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            archived_at=datetime.fromisoformat(row["archived_at"]) if row["archived_at"] else None,
            forwarded_from_id=row["forwarded_from_id"],
            forwarded_from_kind=row["forwarded_from_kind"],
            turn_cap=row["turn_cap"],
            turns_used=row["turns_used"],
            summary=row["summary"],
            new_kb_slugs=json.loads(row["new_kb_slugs_json"]) if row["new_kb_slugs_json"] else [],
            transcript_path=row["transcript_path"],
            archive_requested_at=datetime.fromisoformat(row["archive_requested_at"]) if row["archive_requested_at"] else None,
        )

    @_synchronized
    def get_thread(self, thread_id: str) -> ThreadRecord | None:
        cursor = self._conn.execute(
            "SELECT * FROM threads WHERE id = ?", (thread_id,)
        )
        row = cursor.fetchone()
        return self._row_to_thread(row) if row else None

    @_synchronized
    def list_threads(self, *, status: str | None = None, limit: int = 50) -> list[ThreadRecord]:
        if status:
            cursor = self._conn.execute(
                "SELECT * FROM threads WHERE status = ? ORDER BY started_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM threads ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
        return [self._row_to_thread(r) for r in cursor.fetchall()]
```

Add the necessary imports at the top of `database.py` if not already present: `from src.models import ThreadRecord, ThreadStatus`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_thread_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_thread_db.py
git commit -m "feat(db): threads insert/get/list"
```

---

## Task 8: Database — thread_participants CRUD

**Files:**
- Modify: `src/infrastructure/database.py`

- [ ] **Step 1: Failing tests**

Append to `tests/test_thread_db.py`:

```python
def test_add_and_list_participants(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.add_thread_participant("THR-001", "bob", added_by="founder")
    names = [p.agent_name for p in db.list_thread_participants("THR-001")]
    assert sorted(names) == ["alice", "bob"]


def test_add_thread_participant_idempotent(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    assert db.add_thread_participant("THR-001", "alice", added_by="founder") is False


def test_is_thread_participant(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    assert db.is_thread_participant("THR-001", "alice")
    assert not db.is_thread_participant("THR-001", "bob")
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_thread_db.py::test_add_and_list_participants -v`
Expected: FAIL (methods don't exist).

- [ ] **Step 3: Implement**

In `src/infrastructure/database.py`:

```python
    @_synchronized
    def add_thread_participant(
        self, thread_id: str, agent_name: str, *, added_by: str
    ) -> bool:
        """Insert a participant. Returns True if inserted, False if duplicate."""
        try:
            self._conn.execute(
                "INSERT INTO thread_participants (thread_id, agent_name, added_at, added_by) "
                "VALUES (?, ?, ?, ?)",
                (thread_id, agent_name, _now().isoformat(), added_by),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    @_synchronized
    def is_thread_participant(self, thread_id: str, agent_name: str) -> bool:
        cursor = self._conn.execute(
            "SELECT 1 FROM thread_participants WHERE thread_id = ? AND agent_name = ?",
            (thread_id, agent_name),
        )
        return cursor.fetchone() is not None

    @_synchronized
    def list_thread_participants(self, thread_id: str) -> list[ThreadParticipant]:
        cursor = self._conn.execute(
            "SELECT thread_id, agent_name, added_at, added_by "
            "FROM thread_participants WHERE thread_id = ? ORDER BY added_at",
            (thread_id,),
        )
        return [
            ThreadParticipant(
                thread_id=r["thread_id"],
                agent_name=r["agent_name"],
                added_at=datetime.fromisoformat(r["added_at"]),
                added_by=r["added_by"],
            )
            for r in cursor.fetchall()
        ]
```

Add `from src.models import ThreadParticipant` to the imports.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_thread_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_thread_db.py
git commit -m "feat(db): thread_participants CRUD"
```

---

## Task 9: Database — thread_messages CRUD with atomic seq allocation

**Files:**
- Modify: `src/infrastructure/database.py`

- [ ] **Step 1: Failing tests**

Append to `tests/test_thread_db.py`:

```python
def test_append_thread_message_allocates_monotonic_seq(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    seq_a = db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="hello", addressed_to=["@all"],
    )
    seq_b = db.append_thread_message(
        thread_id="THR-001", speaker="alice",
        kind=ThreadMessageKind.MESSAGE,
        body_markdown="hi back",
    )
    assert seq_a == 1
    assert seq_b == 2
    msgs = db.list_thread_messages("THR-001")
    assert [m.seq for m in msgs] == [1, 2]
    assert msgs[0].addressed_to == ["@all"]
    assert msgs[1].addressed_to is None


def test_append_thread_decline_message(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.append_thread_message(
        thread_id="THR-001", speaker="alice",
        kind=ThreadMessageKind.DECLINE,
        decline_reason="bob covered it",
    )
    msgs = db.list_thread_messages("THR-001")
    assert msgs[0].kind is ThreadMessageKind.DECLINE
    assert msgs[0].decline_reason == "bob covered it"


def test_append_thread_system_message(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.SYSTEM,
        system_payload={"kind_tag": "participant_added", "agent_name": "alice"},
    )
    msgs = db.list_thread_messages("THR-001")
    assert msgs[0].system_payload["kind_tag"] == "participant_added"
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_thread_db.py::test_append_thread_message_allocates_monotonic_seq -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `src/infrastructure/database.py`:

```python
    @_synchronized
    def append_thread_message(
        self,
        *,
        thread_id: str,
        speaker: str,
        kind: ThreadMessageKind,
        body_markdown: str | None = None,
        addressed_to: list[str] | None = None,
        decline_reason: str | None = None,
        system_payload: dict | None = None,
    ) -> int:
        """Append a message and return its allocated seq.

        Atomic against concurrent appends — both the seq allocation and the
        insert happen under the connection's transaction, and the unique
        index on (thread_id, seq) guards against any race.
        """
        cursor = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq "
            "FROM thread_messages WHERE thread_id = ?",
            (thread_id,),
        )
        next_seq = cursor.fetchone()["next_seq"]
        self._conn.execute(
            "INSERT INTO thread_messages (thread_id, seq, speaker, kind, "
            "body_markdown, addressed_to_json, decline_reason, system_payload_json, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                thread_id,
                next_seq,
                speaker,
                kind.value,
                body_markdown,
                json.dumps(addressed_to) if addressed_to else None,
                decline_reason,
                json.dumps(system_payload) if system_payload else None,
                _now().isoformat(),
            ),
        )
        self._conn.commit()
        return next_seq

    @_synchronized
    def list_thread_messages(
        self, thread_id: str, *, since_seq: int = 0, limit: int = 1000
    ) -> list[ThreadMessage]:
        cursor = self._conn.execute(
            "SELECT * FROM thread_messages "
            "WHERE thread_id = ? AND seq > ? ORDER BY seq LIMIT ?",
            (thread_id, since_seq, limit),
        )
        return [
            ThreadMessage(
                id=r["id"],
                thread_id=r["thread_id"],
                seq=r["seq"],
                speaker=r["speaker"],
                kind=ThreadMessageKind(r["kind"]),
                body_markdown=r["body_markdown"],
                addressed_to=json.loads(r["addressed_to_json"]) if r["addressed_to_json"] else None,
                decline_reason=r["decline_reason"],
                system_payload=json.loads(r["system_payload_json"]) if r["system_payload_json"] else None,
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in cursor.fetchall()
        ]

    @_synchronized
    def get_thread_message_by_seq(
        self, thread_id: str, seq: int
    ) -> ThreadMessage | None:
        cursor = self._conn.execute(
            "SELECT * FROM thread_messages WHERE thread_id = ? AND seq = ?",
            (thread_id, seq),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return ThreadMessage(
            id=row["id"],
            thread_id=row["thread_id"],
            seq=row["seq"],
            speaker=row["speaker"],
            kind=ThreadMessageKind(row["kind"]),
            body_markdown=row["body_markdown"],
            addressed_to=json.loads(row["addressed_to_json"]) if row["addressed_to_json"] else None,
            decline_reason=row["decline_reason"],
            system_payload=json.loads(row["system_payload_json"]) if row["system_payload_json"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )
```

Add `from src.models import ThreadMessage, ThreadMessageKind` to imports.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_thread_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_thread_db.py
git commit -m "feat(db): thread_messages append + list with monotonic seq"
```

---

## Task 10: Database — thread_invocations mint/validate/consume/reap

**Files:**
- Modify: `src/infrastructure/database.py`

- [ ] **Step 1: Failing tests**

Append to `tests/test_thread_db.py`:

```python
from src.models import ThreadInvocationPurpose, ThreadInvocationStatus


def test_mint_thread_invocation(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    assert inv.status is ThreadInvocationStatus.PENDING
    assert len(inv.invocation_token) >= 16
    assert inv.purpose is ThreadInvocationPurpose.REPLY


def test_get_pending_invocation_by_token(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    found = db.get_pending_invocation(inv.invocation_token)
    assert found is not None
    assert found.agent_name == "alice"
    assert db.get_pending_invocation("nonsense") is None


def test_consume_invocation_marks_consumed(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    assert db.consume_invocation(inv.invocation_token) is True
    # Second consume returns False (already consumed).
    assert db.consume_invocation(inv.invocation_token) is False
    assert db.get_pending_invocation(inv.invocation_token) is None


def test_record_dispatch_on_invocation(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    # First dispatch attempt succeeds.
    assert db.record_dispatch_on_invocation(inv.invocation_token, task_id="TASK-009") is True
    # Second dispatch attempt fails (one dispatch per token).
    assert db.record_dispatch_on_invocation(inv.invocation_token, task_id="TASK-010") is False


def test_reap_pending_invocations(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="a",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="b",
        triggering_seq=1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
    )
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="c",
        triggering_seq=2, purpose=ThreadInvocationPurpose.CLOSE_OUT,
    )
    reaped = db.reap_pending_invocations(
        "THR-001",
        purposes=[ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP],
        decline_reason="archive_started",
    )
    assert reaped == 2
    # Close-out stays pending.
    pending = db.list_thread_invocations("THR-001", status=ThreadInvocationStatus.PENDING)
    assert len(pending) == 1
    assert pending[0].agent_name == "c"
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_thread_db.py::test_mint_thread_invocation -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `src/infrastructure/database.py`:

```python
    @_synchronized
    def mint_thread_invocation(
        self,
        *,
        thread_id: str,
        agent_name: str,
        triggering_seq: int,
        purpose: ThreadInvocationPurpose,
    ) -> ThreadInvocation:
        import uuid as _uuid
        token = _uuid.uuid4().hex
        now = _now().isoformat()
        cursor = self._conn.execute(
            "INSERT INTO thread_invocations (thread_id, agent_name, "
            "invocation_token, triggering_seq, purpose, status, enqueued_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (thread_id, agent_name, token, triggering_seq, purpose.value, now),
        )
        self._conn.commit()
        return ThreadInvocation(
            id=cursor.lastrowid,
            thread_id=thread_id,
            agent_name=agent_name,
            invocation_token=token,
            triggering_seq=triggering_seq,
            purpose=purpose,
            status=ThreadInvocationStatus.PENDING,
            enqueued_at=datetime.fromisoformat(now),
        )

    def _row_to_invocation(self, row) -> ThreadInvocation:
        return ThreadInvocation(
            id=row["id"],
            thread_id=row["thread_id"],
            agent_name=row["agent_name"],
            invocation_token=row["invocation_token"],
            triggering_seq=row["triggering_seq"],
            purpose=ThreadInvocationPurpose(row["purpose"]),
            status=ThreadInvocationStatus(row["status"]),
            enqueued_at=datetime.fromisoformat(row["enqueued_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            consumed_at=datetime.fromisoformat(row["consumed_at"]) if row["consumed_at"] else None,
            session_id=row["session_id"],
            dispatched_task_id=row["dispatched_task_id"],
            decline_reason=row["decline_reason"],
        )

    @_synchronized
    def get_pending_invocation(self, token: str) -> ThreadInvocation | None:
        cursor = self._conn.execute(
            "SELECT * FROM thread_invocations "
            "WHERE invocation_token = ? AND status = 'pending'",
            (token,),
        )
        row = cursor.fetchone()
        return self._row_to_invocation(row) if row else None

    @_synchronized
    def get_invocation_any_status(self, token: str) -> ThreadInvocation | None:
        cursor = self._conn.execute(
            "SELECT * FROM thread_invocations WHERE invocation_token = ?",
            (token,),
        )
        row = cursor.fetchone()
        return self._row_to_invocation(row) if row else None

    @_synchronized
    def consume_invocation(self, token: str) -> bool:
        """Mark a pending invocation as consumed. Returns True on success."""
        cursor = self._conn.execute(
            "UPDATE thread_invocations SET status = 'consumed', "
            "consumed_at = ? WHERE invocation_token = ? AND status = 'pending'",
            (_now().isoformat(), token),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def record_dispatch_on_invocation(
        self, token: str, *, task_id: str
    ) -> bool:
        """Stamp dispatched_task_id on a pending invocation. Idempotent-fail
        when already set or non-pending. Returns True if the stamp landed."""
        cursor = self._conn.execute(
            "UPDATE thread_invocations SET dispatched_task_id = ? "
            "WHERE invocation_token = ? AND status = 'pending' "
            "AND dispatched_task_id IS NULL",
            (task_id, token),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def fail_invocation(
        self, token: str, *, status: ThreadInvocationStatus, decline_reason: str
    ) -> bool:
        cursor = self._conn.execute(
            "UPDATE thread_invocations SET status = ?, decline_reason = ?, "
            "consumed_at = ? WHERE invocation_token = ? AND status = 'pending'",
            (status.value, decline_reason, _now().isoformat(), token),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def stamp_invocation_started(
        self, token: str, *, session_id: str | None
    ) -> None:
        self._conn.execute(
            "UPDATE thread_invocations SET started_at = ?, session_id = ? "
            "WHERE invocation_token = ? AND status = 'pending'",
            (_now().isoformat(), session_id, token),
        )
        self._conn.commit()

    @_synchronized
    def list_thread_invocations(
        self,
        thread_id: str,
        *,
        status: ThreadInvocationStatus | None = None,
    ) -> list[ThreadInvocation]:
        if status is not None:
            cursor = self._conn.execute(
                "SELECT * FROM thread_invocations "
                "WHERE thread_id = ? AND status = ? ORDER BY id",
                (thread_id, status.value),
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM thread_invocations WHERE thread_id = ? ORDER BY id",
                (thread_id,),
            )
        return [self._row_to_invocation(r) for r in cursor.fetchall()]

    @_synchronized
    def reap_pending_invocations(
        self,
        thread_id: str,
        *,
        purposes: list[ThreadInvocationPurpose] | None = None,
        decline_reason: str,
    ) -> int:
        """Mark pending invocations on a thread as failed. Returns count reaped.

        If `purposes` is None, ALL pending invocations are reaped. Otherwise
        only those whose purpose is in the list.
        """
        now = _now().isoformat()
        if purposes is None:
            cursor = self._conn.execute(
                "UPDATE thread_invocations SET status = 'failed', "
                "decline_reason = ?, consumed_at = ? "
                "WHERE thread_id = ? AND status = 'pending'",
                (decline_reason, now, thread_id),
            )
        else:
            placeholders = ",".join("?" * len(purposes))
            values = [decline_reason, now, thread_id] + [p.value for p in purposes]
            cursor = self._conn.execute(
                f"UPDATE thread_invocations SET status = 'failed', "
                f"decline_reason = ?, consumed_at = ? "
                f"WHERE thread_id = ? AND status = 'pending' "
                f"AND purpose IN ({placeholders})",
                values,
            )
        self._conn.commit()
        return cursor.rowcount
```

Add `from src.models import ThreadInvocation, ThreadInvocationPurpose, ThreadInvocationStatus` to imports.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_thread_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_thread_db.py
git commit -m "feat(db): thread_invocations mint/validate/consume/reap"
```

---

## Task 11: Database — turn-cap accounting + thread status transitions

**Files:**
- Modify: `src/infrastructure/database.py`

- [ ] **Step 1: Failing tests**

Append to `tests/test_thread_db.py`:

```python
def test_increment_turns_used(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.increment_thread_turns_used("THR-001", by=2)
    db.increment_thread_turns_used("THR-001", by=1)
    t = db.get_thread("THR-001")
    assert t.turns_used == 3


def test_set_thread_status_archiving(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.set_thread_status(
        "THR-001",
        status=ThreadStatus.ARCHIVING,
        summary="done talking",
    )
    t = db.get_thread("THR-001")
    assert t.status is ThreadStatus.ARCHIVING
    assert t.summary == "done talking"
    assert t.archive_requested_at is not None


def test_finalize_thread_archived(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.set_thread_status("THR-001", status=ThreadStatus.ARCHIVING, summary="s")
    db.finalize_thread_archived(
        "THR-001",
        transcript_path="/tmp/THR-001.md",
        new_kb_slugs=["refund-policy"],
    )
    t = db.get_thread("THR-001")
    assert t.status is ThreadStatus.ARCHIVED
    assert t.archived_at is not None
    assert t.transcript_path == "/tmp/THR-001.md"
    assert t.new_kb_slugs == ["refund-policy"]


def test_set_thread_turn_cap(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.set_thread_turn_cap("THR-001", new_cap=1000)
    assert db.get_thread("THR-001").turn_cap == 1000
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_thread_db.py::test_increment_turns_used -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `src/infrastructure/database.py`:

```python
    @_synchronized
    def increment_thread_turns_used(self, thread_id: str, *, by: int = 1) -> None:
        self._conn.execute(
            "UPDATE threads SET turns_used = turns_used + ? WHERE id = ?",
            (by, thread_id),
        )
        self._conn.commit()

    @_synchronized
    def set_thread_status(
        self,
        thread_id: str,
        *,
        status: ThreadStatus,
        summary: str | None = None,
    ) -> None:
        now = _now().isoformat()
        if status is ThreadStatus.ARCHIVING:
            self._conn.execute(
                "UPDATE threads SET status = ?, summary = COALESCE(?, summary), "
                "archive_requested_at = ? WHERE id = ?",
                (status.value, summary, now, thread_id),
            )
        elif status is ThreadStatus.ABANDONED:
            self._conn.execute(
                "UPDATE threads SET status = ?, archived_at = COALESCE(archived_at, ?) "
                "WHERE id = ?",
                (status.value, now, thread_id),
            )
        else:
            self._conn.execute(
                "UPDATE threads SET status = ? WHERE id = ?",
                (status.value, thread_id),
            )
        self._conn.commit()

    @_synchronized
    def finalize_thread_archived(
        self,
        thread_id: str,
        *,
        transcript_path: str,
        new_kb_slugs: list[str],
    ) -> None:
        self._conn.execute(
            "UPDATE threads SET status = 'archived', archived_at = ?, "
            "transcript_path = ?, new_kb_slugs_json = ? WHERE id = ?",
            (
                _now().isoformat(),
                transcript_path,
                json.dumps(new_kb_slugs) if new_kb_slugs else None,
                thread_id,
            ),
        )
        self._conn.commit()

    @_synchronized
    def set_thread_turn_cap(self, thread_id: str, *, new_cap: int) -> None:
        self._conn.execute(
            "UPDATE threads SET turn_cap = ? WHERE id = ?",
            (new_cap, thread_id),
        )
        self._conn.commit()

    @_synchronized
    def add_thread_kb_slug(self, thread_id: str, slug: str) -> None:
        """Idempotent set-union over new_kb_slugs_json."""
        cursor = self._conn.execute(
            "SELECT new_kb_slugs_json FROM threads WHERE id = ?", (thread_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return
        slugs = json.loads(row["new_kb_slugs_json"]) if row["new_kb_slugs_json"] else []
        if slug in slugs:
            return
        slugs.append(slug)
        self._conn.execute(
            "UPDATE threads SET new_kb_slugs_json = ? WHERE id = ?",
            (json.dumps(slugs), thread_id),
        )
        self._conn.commit()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_thread_db.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_thread_db.py
git commit -m "feat(db): thread turn-cap + status transition helpers"
```

---

## Task 12: AuditLogger — thread actions

**Files:**
- Modify: `src/infrastructure/audit_logger.py`

- [ ] **Step 1: Add log methods**

Open `src/infrastructure/audit_logger.py`. Append, near the existing `log_talk_*` methods:

```python
    def log_thread_started(
        self,
        thread_id: str,
        *,
        subject: str,
        initial_recipients: list[str],
        forwarded_from_id: str | None,
    ) -> None:
        self._insert(
            task_id=thread_id,
            agent="founder",
            action="thread_started",
            payload={
                "subject": subject,
                "initial_recipients": initial_recipients,
                "forwarded_from_id": forwarded_from_id,
            },
        )

    def log_thread_message_sent(
        self,
        thread_id: str,
        *,
        seq: int,
        speaker: str,
        addressed_to: list[str] | None,
        kind: str,
    ) -> None:
        self._insert(
            task_id=thread_id,
            agent=speaker,
            action="thread_message_sent",
            payload={"seq": seq, "addressed_to": addressed_to, "kind": kind},
        )

    def log_thread_participant_added(
        self,
        thread_id: str,
        *,
        agent_name: str,
        added_by: str,
    ) -> None:
        self._insert(
            task_id=thread_id,
            agent=added_by,
            action="thread_participant_added",
            payload={"agent_name": agent_name, "added_by": added_by},
        )

    def log_thread_dispatch(
        self,
        thread_id: str,
        *,
        task_id: str,
        dispatcher: str,
        target_agent: str,
        team: str,
    ) -> None:
        self._insert(
            task_id=thread_id,
            agent=dispatcher,
            action="thread_dispatch",
            payload={
                "task_id": task_id,
                "dispatcher": dispatcher,
                "target_agent": target_agent,
                "team": team,
            },
        )

    def log_thread_archive_requested(
        self,
        thread_id: str,
        *,
        close_out_count: int,
    ) -> None:
        self._insert(
            task_id=thread_id,
            agent="founder",
            action="thread_archive_requested",
            payload={"close_out_count": close_out_count},
        )

    def log_thread_archived(
        self,
        thread_id: str,
        *,
        new_learnings_total: int,
        new_kb_slugs: list[str],
        turns_used: int,
    ) -> None:
        self._insert(
            task_id=thread_id,
            agent="founder",
            action="thread_archived",
            payload={
                "new_learnings_total": new_learnings_total,
                "new_kb_slugs": new_kb_slugs,
                "turns_used": turns_used,
            },
        )

    def log_thread_abandoned(self, thread_id: str, *, reason: str) -> None:
        self._insert(
            task_id=thread_id, agent="founder",
            action="thread_abandoned", payload={"reason": reason},
        )

    def log_thread_close_out_received(
        self,
        thread_id: str,
        *,
        agent: str,
        new_learnings_count: int,
        new_kb_slugs: list[str],
    ) -> None:
        self._insert(
            task_id=thread_id,
            agent=agent,
            action="thread_close_out_received",
            payload={
                "new_learnings_count": new_learnings_count,
                "new_kb_slugs": new_kb_slugs,
            },
        )

    def log_thread_invocation_failed(
        self,
        thread_id: str,
        *,
        agent: str,
        token: str,
        purpose: str,
        reason: str,
        kind: str = "thread_invocation_failed",
    ) -> None:
        self._insert(
            task_id=thread_id,
            agent=agent,
            action=kind,
            payload={"invocation_token": token[:8] + "…", "purpose": purpose, "reason": reason},
        )
```

If `AuditLogger` uses a different internal helper than `_insert` for writing audit rows (read existing methods like `log_talk_started` for the pattern), adapt accordingly — the bodies of the new methods should follow the existing shape exactly.

- [ ] **Step 2: Quick smoke test**

Run: `uv run pytest tests/ -v -k "audit" 2>&1 | tail -20`
Expected: no regressions.

- [ ] **Step 3: Commit**

```bash
git add src/infrastructure/audit_logger.py
git commit -m "feat(audit): thread_* audit actions"
```

---

## Task 13: ThreadStore — atomic transcript writer

**Files:**
- Create: `src/infrastructure/thread_store.py`
- Create: `tests/test_thread_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_thread_store.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from src.infrastructure.thread_store import ThreadStore


def test_write_transcript_creates_file(tmp_path):
    store = ThreadStore(tmp_path / "threads")
    path = store.write_transcript(
        thread_id="THR-001",
        subject="Refund policy",
        started_at=datetime(2026, 5, 13, 10, 42, tzinfo=timezone.utc),
        archived_at=datetime(2026, 5, 13, 14, 10, tzinfo=timezone.utc),
        participants=["alice", "bob"],
        turns_used=4,
        new_learnings_total=3,
        new_kb_slugs=["refund-policy"],
        forwarded_from_id=None,
        summary="Settled at 45 days.",
        rendered_transcript="# Transcript\n\nMessage 1 …\n",
    )
    text = path.read_text(encoding="utf-8")
    assert "thread_id: THR-001" in text
    assert "Refund policy" in text
    assert "Settled at 45 days." in text
    assert "Message 1" in text


def test_write_transcript_is_atomic(tmp_path):
    """Writer should leave no .tmp artifacts on success."""
    store = ThreadStore(tmp_path / "threads")
    store.write_transcript(
        thread_id="THR-001",
        subject="x",
        started_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
        archived_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
        participants=[],
        turns_used=0,
        new_learnings_total=0,
        new_kb_slugs=[],
        forwarded_from_id=None,
        summary="",
        rendered_transcript="",
    )
    tmps = list((tmp_path / "threads").glob(".THR-001.*.md.tmp"))
    assert tmps == []
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_thread_store.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `src/infrastructure/thread_store.py`:

```python
"""Filesystem writes for thread transcripts under <runtime>/orgs/<slug>/threads/."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

import yaml


class ThreadStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)

    def path_for(self, thread_id: str) -> Path:
        return self._root / f"{thread_id}.md"

    def write_transcript(
        self,
        *,
        thread_id: str,
        subject: str,
        started_at: datetime,
        archived_at: datetime,
        participants: list[str],
        turns_used: int,
        new_learnings_total: int,
        new_kb_slugs: list[str],
        forwarded_from_id: str | None,
        summary: str,
        rendered_transcript: str,
    ) -> Path:
        frontmatter = {
            "thread_id": thread_id,
            "subject": subject,
            "started_at": started_at.isoformat(),
            "archived_at": archived_at.isoformat(),
            "participants": participants,
            "forwarded_from_id": forwarded_from_id,
            "turns_used": turns_used,
            "new_learnings_total": new_learnings_total,
            "new_kb_slugs": new_kb_slugs,
        }
        fm_text = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        body = (
            "---\n"
            f"{fm_text}\n"
            "---\n\n"
            "# Summary\n\n"
            f"{summary}\n\n"
            f"{rendered_transcript}\n"
        )
        target = self.path_for(thread_id)
        fd, tmp_name = tempfile.mkstemp(dir=self._root, prefix=f".{thread_id}.", suffix=".md.tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body.encode("utf-8"))
            os.replace(tmp_name, target)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return target
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_thread_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/thread_store.py tests/test_thread_store.py
git commit -m "feat(infra): ThreadStore atomic transcript writer"
```

---

## Task 14: Transcript renderer (pure function)

**Files:**
- Modify: `src/infrastructure/thread_store.py`
- Modify: `tests/test_thread_store.py`

- [ ] **Step 1: Failing tests**

Append to `tests/test_thread_store.py`:

```python
from src.infrastructure.thread_store import render_transcript_body
from src.models import ThreadMessage, ThreadMessageKind


def test_render_transcript_renders_message_decline_system():
    msgs = [
        ThreadMessage(
            thread_id="THR-001", seq=1, speaker="founder",
            kind=ThreadMessageKind.MESSAGE,
            body_markdown="should we cap refunds at 30 days?",
            addressed_to=["@all"],
        ),
        ThreadMessage(
            thread_id="THR-001", seq=2, speaker="alice",
            kind=ThreadMessageKind.MESSAGE,
            body_markdown="Alipay 60d, Stripe 120d.",
        ),
        ThreadMessage(
            thread_id="THR-001", seq=3, speaker="bob",
            kind=ThreadMessageKind.DECLINE,
            decline_reason="alice covered it",
        ),
        ThreadMessage(
            thread_id="THR-001", seq=4, speaker="alice",
            kind=ThreadMessageKind.SYSTEM,
            system_payload={
                "kind_tag": "task_dispatched",
                "task_id": "TASK-091",
                "target_agent": "dev",
                "brief_preview": "Cap at 45 days",
            },
        ),
    ]
    out = render_transcript_body(msgs)
    assert "## Message 1 — founder" in out
    assert "To: @all" in out
    assert "## Message 3 — bob" in out
    assert "declined" in out and "alice covered it" in out
    assert "system: dispatched TASK-091 to dev" in out
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_thread_store.py::test_render_transcript_renders_message_decline_system -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement**

Append to `src/infrastructure/thread_store.py`:

```python
def render_transcript_body(messages: list) -> str:
    """Render a chronological list of ThreadMessage into markdown.

    Pure function for easier testing; consumed by /archive Phase B.
    """
    lines: list[str] = ["# Transcript", ""]
    for m in messages:
        ts = m.created_at.isoformat() if hasattr(m.created_at, "isoformat") else str(m.created_at)
        kind_name = m.kind.value if hasattr(m.kind, "value") else str(m.kind)
        if kind_name == "message":
            header = f"## Message {m.seq} — {m.speaker} · {ts}"
            lines.append(header)
            if m.addressed_to:
                lines.append(f"> To: {', '.join(m.addressed_to)}")
                lines.append("")
            lines.append(m.body_markdown or "")
            lines.append("")
        elif kind_name == "decline":
            lines.append(f"## Message {m.seq} — {m.speaker} · {ts}")
            lines.append(f"> 👁 declined: {m.decline_reason or ''}")
            lines.append("")
        elif kind_name == "system":
            payload = m.system_payload or {}
            tag = payload.get("kind_tag", "system")
            if tag == "participant_added":
                rendered = f"founder added {payload.get('agent_name')} to the thread"
            elif tag == "task_dispatched":
                tgt = payload.get("target_agent")
                tid = payload.get("task_id")
                brief = payload.get("brief_preview", "")
                rendered = f"system: dispatched {tid} to {tgt}" + (
                    f" — {brief}" if brief else ""
                )
            elif tag == "turn_cap_extended":
                rendered = (
                    f"system: turn cap extended from {payload.get('prior_cap')} "
                    f"to {payload.get('new_cap')}"
                )
            elif tag == "archived":
                rendered = "system: thread archived"
            else:
                rendered = f"system: {tag}"
            lines.append(f"## Message {m.seq} — {m.speaker} · {ts}")
            lines.append(f"> {rendered}")
            lines.append("")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_thread_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/thread_store.py tests/test_thread_store.py
git commit -m "feat(infra): render_transcript_body pure helper"
```

---

## Task 15: Org config — threads block

**Files:**
- Modify: `src/orchestrator/org_config.py`
- Create or modify: `tests/test_org_config_threads.py`

- [ ] **Step 1: Failing tests**

Create `tests/test_org_config_threads.py`:

```python
from __future__ import annotations

import textwrap

from src.orchestrator.org_config import OrgConfig


def test_threads_defaults_when_missing(tmp_path):
    cfg = OrgConfig.load_from_text("")
    assert cfg.threads_enabled is True
    assert cfg.threads_default_turn_cap == 500
    assert cfg.threads_close_out_wait_seconds == 300
    assert cfg.threads_invocation_timeout_seconds is None


def test_threads_loaded_from_yaml():
    text = textwrap.dedent("""
    threads:
      enabled: false
      default_turn_cap: 200
      close_out_wait_seconds: 120
      invocation_timeout_seconds: 900
    """)
    cfg = OrgConfig.load_from_text(text)
    assert cfg.threads_enabled is False
    assert cfg.threads_default_turn_cap == 200
    assert cfg.threads_close_out_wait_seconds == 120
    assert cfg.threads_invocation_timeout_seconds == 900


def test_threads_invalid_cap_raises():
    import pytest
    from src.orchestrator.org_config import OrgConfigError
    text = "threads:\n  default_turn_cap: -1\n"
    with pytest.raises(OrgConfigError):
        OrgConfig.load_from_text(text)
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_org_config_threads.py -v`
Expected: FAIL (`OrgConfig` has no `threads_*` attributes or `load_from_text` may not exist as a classmethod).

- [ ] **Step 3: Read the existing OrgConfig and extend it**

Read `src/orchestrator/org_config.py`. Find the `OrgConfig` class and `OrgConfigError`. Add four fields and parse them in whatever loader method exists. Pattern:

```python
@dataclass
class OrgConfig:
    # ... existing fields ...
    threads_enabled: bool = True
    threads_default_turn_cap: int = 500
    threads_close_out_wait_seconds: int = 300
    threads_invocation_timeout_seconds: int | None = None

    @classmethod
    def load_from_text(cls, text: str) -> "OrgConfig":
        # ... existing parsing ...
        data = yaml.safe_load(text) or {}
        threads = data.get("threads") or {}
        if not isinstance(threads, dict):
            raise OrgConfigError("threads must be a mapping")
        kwargs = {}
        if "enabled" in threads:
            kwargs["threads_enabled"] = bool(threads["enabled"])
        if "default_turn_cap" in threads:
            cap = threads["default_turn_cap"]
            if not isinstance(cap, int) or cap <= 0:
                raise OrgConfigError("threads.default_turn_cap must be a positive int")
            kwargs["threads_default_turn_cap"] = cap
        if "close_out_wait_seconds" in threads:
            w = threads["close_out_wait_seconds"]
            if not isinstance(w, int) or w <= 0:
                raise OrgConfigError("threads.close_out_wait_seconds must be a positive int")
            kwargs["threads_close_out_wait_seconds"] = w
        if "invocation_timeout_seconds" in threads:
            t = threads["invocation_timeout_seconds"]
            if t is not None and (not isinstance(t, int) or t <= 0):
                raise OrgConfigError("threads.invocation_timeout_seconds must be a positive int or null")
            kwargs["threads_invocation_timeout_seconds"] = t
        return cls(..., **kwargs)
```

Adapt to the actual existing constructor signature.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_org_config_threads.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/org_config.py tests/test_org_config_threads.py
git commit -m "feat(org-config): threads block (enabled/cap/timeouts)"
```

---

## Task 16: ThreadQueue skeleton (in-memory async queue)

**Files:**
- Create: `src/daemon/thread_queue.py`
- Create: `tests/test_thread_queue.py`

- [ ] **Step 1: Failing test**

Create `tests/test_thread_queue.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from src.daemon.thread_queue import ThreadQueue, ThreadJob


@pytest.mark.asyncio
async def test_queue_enqueue_then_get():
    q = ThreadQueue()
    job = ThreadJob(org_slug="hk", invocation_token="abc")
    await q.put(job)
    got = await asyncio.wait_for(q.get(), timeout=1.0)
    assert got.invocation_token == "abc"
    assert got.org_slug == "hk"


@pytest.mark.asyncio
async def test_queue_size_reflects_pending():
    q = ThreadQueue()
    assert q.size == 0
    await q.put(ThreadJob(org_slug="hk", invocation_token="a"))
    await q.put(ThreadJob(org_slug="hk", invocation_token="b"))
    assert q.size == 2
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_thread_queue.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Create `src/daemon/thread_queue.py`:

```python
"""Async queue + job payload for thread invocations.

Each ThreadJob points to a thread_invocations row by `invocation_token`.
A worker pool consumes jobs and hands them to ThreadInvocationRunner.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class ThreadJob:
    org_slug: str
    invocation_token: str


class ThreadQueue:
    def __init__(self) -> None:
        self._q: asyncio.Queue[ThreadJob] = asyncio.Queue()

    async def put(self, job: ThreadJob) -> None:
        await self._q.put(job)

    async def get(self) -> ThreadJob:
        return await self._q.get()

    @property
    def size(self) -> int:
        return self._q.qsize()
```

- [ ] **Step 4: Add `pytest-asyncio` mark config check**

Confirm `pyproject.toml` or `pytest.ini` enables asyncio mode. If `asyncio_mode = "auto"` isn't set, add to `pyproject.toml`:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

If it already is set, skip this step.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_thread_queue.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/thread_queue.py tests/test_thread_queue.py pyproject.toml
git commit -m "feat(daemon): ThreadQueue + ThreadJob"
```

---

## Task 17: OrgState — wire ThreadQueue + ThreadStore

**Files:**
- Modify: `src/daemon/org_state.py` (or wherever `OrgState` is defined)

- [ ] **Step 1: Locate OrgState**

Run: `grep -n "class OrgState" src/daemon/*.py`. Open the file. It already holds per-org `db`, `db_lock`, `teams`, `teams_lock`, etc.

- [ ] **Step 2: Add fields**

In the `OrgState` class:

```python
from src.daemon.thread_queue import ThreadQueue
from src.infrastructure.thread_store import ThreadStore

# inside __init__ after existing fields:
self.thread_queue: ThreadQueue = ThreadQueue()
self.thread_store: ThreadStore = ThreadStore(self.root / "threads")
```

- [ ] **Step 3: Verify daemon still imports cleanly**

Run: `uv run python -c "from src.daemon.app import create_app; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add src/daemon/org_state.py
git commit -m "feat(daemon): OrgState wires ThreadQueue + ThreadStore"
```

---

## Task 18: Routes module skeleton + mounting

**Files:**
- Create: `src/daemon/routes/threads.py`
- Modify: `src/daemon/app.py` (mount router)

- [ ] **Step 1: Create the skeleton**

Create `src/daemon/routes/threads.py`:

```python
"""Thread endpoints — email-style multi-agent workchannel."""
from __future__ import annotations

from fastapi import APIRouter

from src.daemon.auth import require_token

router = APIRouter(dependencies=[require_token()])
```

- [ ] **Step 2: Mount in app.py**

In `src/daemon/app.py`, find where the existing routers (`talks_router`, `tasks_router`, etc.) are included for the per-org prefix. Add the threads router alongside them:

```python
from src.daemon.routes import threads as threads_routes
# ...
app.include_router(
    threads_routes.router,
    prefix="/api/v1/orgs/{slug}",
    tags=["threads"],
)
```

If the existing pattern uses a different mount mechanism (e.g., a helper that adds all per-org routers), follow that pattern instead.

- [ ] **Step 3: Confirm import + mount**

Run: `uv run python -c "from src.daemon.app import create_app; app = create_app(); print([r.path for r in app.routes if 'threads' in r.path.lower()][:3])"`
Expected: prints at least one route path containing `threads` (after we add endpoints).

For now expect `[]` — the router is empty. Just confirm no import error.

- [ ] **Step 4: Commit**

```bash
git add src/daemon/routes/threads.py src/daemon/app.py
git commit -m "feat(routes): threads router skeleton + mount"
```

---

## Task 19: POST /threads (compose)

**Files:**
- Modify: `src/daemon/routes/threads.py`
- Create: `tests/test_threads_routes.py`

- [ ] **Step 1: Failing test**

Create `tests/test_threads_routes.py`. Adapt the existing test harness from `tests/test_talks_routes.py` if available — look at how it builds a TestClient with a stub OrgState. If not, use the `conftest.py` org fixture used by other route tests:

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# Reuse whatever test-fixture pattern other routes use; the existing
# tests/test_talks_routes.py is the closest precedent.

from tests.conftest import make_test_client, register_agent  # adjust to actual conftest helpers


def test_compose_creates_thread_and_invocations(test_org):
    client, org = test_org  # adapt to existing fixture shape
    register_agent(org, "alice", team="engineering")
    register_agent(org, "bob", team="engineering")

    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={
            "subject": "Refund policy",
            "recipients": ["alice", "bob"],
            "body_markdown": "should we cap refunds at 30 days?",
            "addressed_to": ["@all"],
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["thread_id"].startswith("THR-")
    assert set(data["pending_replies"]) == {"alice", "bob"}

    invocations = org.db.list_thread_invocations(data["thread_id"])
    assert len(invocations) == 2
    assert all(inv.purpose.value == "reply" for inv in invocations)


def test_compose_rejects_unknown_recipient(test_org):
    client, org = test_org
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={
            "subject": "x",
            "recipients": ["ghost"],
            "body_markdown": "hi",
            "addressed_to": ["@all"],
        },
    )
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "unknown_agent"


def test_compose_rejects_empty_subject(test_org):
    client, org = test_org
    register_agent(org, "alice", team="engineering")
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={
            "subject": "   ",
            "recipients": ["alice"],
            "body_markdown": "hi",
            "addressed_to": ["@all"],
        },
    )
    assert resp.status_code == 422
```

If the test-fixture pattern is different in this repo, mirror the closest existing route test exactly. Look at `tests/test_talks_routes.py` and reuse its conftest fixtures.

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_threads_routes.py::test_compose_creates_thread_and_invocations -v`
Expected: FAIL (endpoint missing → 404 or 405).

- [ ] **Step 3: Implement compose endpoint**

In `src/daemon/routes/threads.py`:

```python
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.routes._org_dep import OrgDep
from src.daemon.state import DaemonState
from src.daemon.thread_queue import ThreadJob
from src.infrastructure.audit_logger import AuditLogger
from src.models import (
    ThreadInvocationPurpose, ThreadMessageKind, ThreadRecord, ThreadStatus,
)
from src.orchestrator import prompt_loader
from src.orchestrator._paths import OrgPaths

router = APIRouter(dependencies=[require_token()])


class ComposeBody(BaseModel):
    subject: str
    recipients: list[str]
    body_markdown: str
    addressed_to: list[str] = ["@all"]
    forwarded_from_id: str | None = None
    forwarded_from_kind: str | None = None  # 'thread' | 'talk'


def _validate_addressed_to(addressed_to: list[str], recipients: list[str]) -> None:
    if addressed_to == ["@all"]:
        return
    for name in addressed_to:
        if name == "@all":
            raise HTTPException(
                status_code=422,
                detail={"code": "addressed_to_mixed_at_all"},
            )
        if name not in recipients:
            raise HTTPException(
                status_code=422,
                detail={"code": "addressed_to_not_subset", "name": name},
            )


def _resolve_addressed_agents(addressed_to: list[str], recipients: list[str]) -> list[str]:
    if addressed_to == ["@all"]:
        return list(recipients)
    return list(addressed_to)


@router.post("/threads")
async def compose_thread(
    slug: str, body: ComposeBody, org: OrgDep, request: Request
) -> dict:
    state: DaemonState = request.app.state.daemon

    subject = body.subject.strip()
    if not subject:
        raise HTTPException(status_code=422, detail={"code": "empty_subject"})
    if not body.recipients:
        raise HTTPException(status_code=422, detail={"code": "empty_recipients"})
    body_text = body.body_markdown.strip()
    if not body_text:
        raise HTTPException(status_code=422, detail={"code": "empty_body"})

    # Validate each recipient is an approved agent with a workspace.
    org_paths = OrgPaths(root=org.root)
    for name in body.recipients:
        agent_def = prompt_loader.load_agent(org_paths, name)
        workspace_exists = (org.root / "workspaces" / name).exists()
        if agent_def is None or not workspace_exists:
            raise HTTPException(
                status_code=404,
                detail={"code": "unknown_agent", "agent": name},
            )

    _validate_addressed_to(body.addressed_to, body.recipients)

    # Validate forwarded source if set.
    if (body.forwarded_from_id is None) != (body.forwarded_from_kind is None):
        raise HTTPException(
            status_code=422,
            detail={"code": "forwarded_fields_must_pair"},
        )
    if body.forwarded_from_kind not in (None, "thread", "talk"):
        raise HTTPException(
            status_code=422,
            detail={"code": "forwarded_kind_invalid"},
        )
    if body.forwarded_from_id is not None:
        if body.forwarded_from_kind == "thread":
            src = org.db.get_thread(body.forwarded_from_id)
        else:
            src = org.db.get_talk(body.forwarded_from_id)
        if src is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "forwarded_source_not_found"},
            )

    # Turn cap from org config (default 500).
    org_cfg = org.config  # assumes OrgState exposes the loaded config; if named differently, adapt.
    turn_cap = getattr(org_cfg, "threads_default_turn_cap", 500)

    addressed_agents = _resolve_addressed_agents(body.addressed_to, body.recipients)
    addressed_count = len(addressed_agents)

    async with org.db_lock:
        # Turn-cap check: brand-new thread, so turns_used = 0 < cap is trivially true,
        # but we still mint the right number of invocations.
        thread_id = org.db.next_thread_id()
        org.db.insert_thread(ThreadRecord(
            id=thread_id, subject=subject, turn_cap=turn_cap,
            forwarded_from_id=body.forwarded_from_id,
            forwarded_from_kind=body.forwarded_from_kind,
        ))
        for name in body.recipients:
            org.db.add_thread_participant(thread_id, name, added_by="founder")
        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.MESSAGE,
            body_markdown=body_text, addressed_to=body.addressed_to,
        )
        AuditLogger(org.db).log_thread_started(
            thread_id,
            subject=subject,
            initial_recipients=body.recipients,
            forwarded_from_id=body.forwarded_from_id,
        )
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker="founder",
            addressed_to=body.addressed_to, kind="message",
        )
        # Mint pending invocations for each addressed agent.
        tokens_to_enqueue: list[str] = []
        for name in addressed_agents:
            inv = org.db.mint_thread_invocation(
                thread_id=thread_id, agent_name=name,
                triggering_seq=seq, purpose=ThreadInvocationPurpose.REPLY,
            )
            tokens_to_enqueue.append(inv.invocation_token)

    # Outside the lock: enqueue jobs for the worker pool.
    for token in tokens_to_enqueue:
        await org.thread_queue.put(ThreadJob(org_slug=slug, invocation_token=token))

    return {
        "thread_id": thread_id,
        "started_at": org.db.get_thread(thread_id).started_at.isoformat(),
        "pending_replies": addressed_agents,
    }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_threads_routes.py -v`
Expected: the 3 compose tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/test_threads_routes.py
git commit -m "feat(routes): POST /threads compose endpoint"
```

---

## Task 20: GET /threads, GET /threads/{id}, GET /threads/{id}/messages

**Files:**
- Modify: `src/daemon/routes/threads.py`
- Modify: `tests/test_threads_routes.py`

- [ ] **Step 1: Failing tests**

Append to `tests/test_threads_routes.py`:

```python
def test_list_threads_returns_recent(test_org):
    client, org = test_org
    register_agent(org, "alice")
    client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={"subject": "a", "recipients": ["alice"], "body_markdown": "x", "addressed_to": ["@all"]},
    )
    client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={"subject": "b", "recipients": ["alice"], "body_markdown": "x", "addressed_to": ["@all"]},
    )
    resp = client.get(f"/api/v1/orgs/{org.slug}/threads")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["threads"]) == 2
    assert data["threads"][0]["subject"] in {"a", "b"}


def test_get_thread_returns_messages_and_participants(test_org):
    client, org = test_org
    register_agent(org, "alice")
    r = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={"subject": "a", "recipients": ["alice"], "body_markdown": "hi", "addressed_to": ["@all"]},
    ).json()
    tid = r["thread_id"]
    resp = client.get(f"/api/v1/orgs/{org.slug}/threads/{tid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["thread_id"] == tid
    assert data["participants"] == ["alice"]
    assert data["messages"][0]["body_markdown"] == "hi"


def test_get_thread_missing_returns_404(test_org):
    client, org = test_org
    resp = client.get(f"/api/v1/orgs/{org.slug}/threads/THR-999")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_threads_routes.py::test_list_threads_returns_recent -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `src/daemon/routes/threads.py`:

```python
def _thread_row_to_dict(t) -> dict:
    return {
        "thread_id": t.id,
        "subject": t.subject,
        "status": t.status.value,
        "started_at": t.started_at.isoformat(),
        "archived_at": t.archived_at.isoformat() if t.archived_at else None,
        "forwarded_from_id": t.forwarded_from_id,
        "forwarded_from_kind": t.forwarded_from_kind,
        "turn_cap": t.turn_cap,
        "turns_used": t.turns_used,
        "summary": t.summary,
        "new_kb_slugs": t.new_kb_slugs,
        "transcript_path": t.transcript_path,
    }


def _msg_to_dict(m) -> dict:
    return {
        "seq": m.seq,
        "speaker": m.speaker,
        "kind": m.kind.value,
        "body_markdown": m.body_markdown,
        "addressed_to": m.addressed_to,
        "decline_reason": m.decline_reason,
        "system_payload": m.system_payload,
        "created_at": m.created_at.isoformat(),
    }


@router.get("/threads")
async def list_threads_endpoint(
    slug: str, org: OrgDep, status: str | None = None, limit: int = 50,
) -> dict:
    rows = org.db.list_threads(status=status, limit=min(limit, 500))
    return {"threads": [_thread_row_to_dict(t) for t in rows]}


@router.get("/threads/{thread_id}")
async def get_thread_endpoint(
    slug: str, thread_id: str, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    participants = [p.agent_name for p in org.db.list_thread_participants(thread_id)]
    msgs = org.db.list_thread_messages(thread_id, limit=200)
    d = _thread_row_to_dict(t)
    d["participants"] = participants
    d["messages"] = [_msg_to_dict(m) for m in msgs]
    return d


@router.get("/threads/{thread_id}/messages")
async def list_thread_messages_endpoint(
    slug: str, thread_id: str, org: OrgDep,
    since_seq: int = 0, limit: int = 200,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    msgs = org.db.list_thread_messages(thread_id, since_seq=since_seq, limit=min(limit, 1000))
    return {"messages": [_msg_to_dict(m) for m in msgs]}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_threads_routes.py -v`
Expected: PASS for the three GET tests.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/test_threads_routes.py
git commit -m "feat(routes): GET /threads, /threads/{id}, /threads/{id}/messages"
```

---

## Task 21: POST /threads/{id}/reply with token validation

**Files:**
- Modify: `src/daemon/routes/threads.py`
- Modify: `tests/test_threads_routes.py`

- [ ] **Step 1: Failing tests**

Append:

```python
def _start_thread(client, org, *, recipient="alice", addressed=None):
    register_agent(org, recipient)
    addressed = addressed or ["@all"]
    r = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={"subject": "s", "recipients": [recipient], "body_markdown": "hi", "addressed_to": addressed},
    ).json()
    inv = org.db.list_thread_invocations(r["thread_id"])[0]
    return r["thread_id"], inv.invocation_token


def test_reply_appends_message_and_consumes_token(test_org):
    client, org = test_org
    tid, token = _start_thread(client, org)
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads/{tid}/reply",
        json={
            "thread_id": tid, "invocation_token": token,
            "speaker": "alice", "body_markdown": "hello back",
            "in_response_to_seq": 1,
        },
    )
    assert resp.status_code == 200, resp.text
    msgs = org.db.list_thread_messages(tid)
    assert msgs[-1].body_markdown == "hello back"
    assert org.db.get_thread(tid).turns_used == 1
    assert org.db.get_pending_invocation(token) is None


def test_reply_rejects_missing_token(test_org):
    client, org = test_org
    tid, _token = _start_thread(client, org)
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads/{tid}/reply",
        json={"thread_id": tid, "invocation_token": "bogus",
              "speaker": "alice", "body_markdown": "x", "in_response_to_seq": 1},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invocation_token_invalid"


def test_reply_rejects_consumed_token(test_org):
    client, org = test_org
    tid, token = _start_thread(client, org)
    p = {"thread_id": tid, "invocation_token": token,
         "speaker": "alice", "body_markdown": "hi", "in_response_to_seq": 1}
    assert client.post(f"/api/v1/orgs/{org.slug}/threads/{tid}/reply", json=p).status_code == 200
    second = client.post(f"/api/v1/orgs/{org.slug}/threads/{tid}/reply", json=p)
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "invocation_token_consumed"


def test_reply_rejects_mismatched_speaker(test_org):
    client, org = test_org
    register_agent(org, "alice")
    register_agent(org, "bob")
    r = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={"subject": "s", "recipients": ["alice", "bob"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
    ).json()
    tid = r["thread_id"]
    # token belongs to alice; bob tries to use it
    alice_token = next(
        inv.invocation_token
        for inv in org.db.list_thread_invocations(tid)
        if inv.agent_name == "alice"
    )
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads/{tid}/reply",
        json={"thread_id": tid, "invocation_token": alice_token,
              "speaker": "bob", "body_markdown": "x", "in_response_to_seq": 1},
    )
    assert resp.status_code == 401
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_threads_routes.py::test_reply_appends_message_and_consumes_token -v`
Expected: FAIL.

- [ ] **Step 3: Add token-validation helper and reply endpoint**

In `src/daemon/routes/threads.py`:

```python
from src.models import ThreadInvocationPurpose, ThreadInvocationStatus


class ReplyBody(BaseModel):
    thread_id: str
    invocation_token: str
    speaker: str
    body_markdown: str
    in_response_to_seq: int


def _validate_invocation_token(
    org, *, token: str, expected_agent: str, expected_thread_id: str,
    require_purposes: list[ThreadInvocationPurpose],
):
    # Phase 1: pending lookup.
    inv = org.db.get_pending_invocation(token)
    if inv is None:
        # Distinguish consumed vs invalid for better client feedback.
        any_inv = org.db.get_invocation_any_status(token)
        if any_inv is None:
            raise HTTPException(
                status_code=401,
                detail={"code": "invocation_token_invalid"},
            )
        raise HTTPException(
            status_code=409,
            detail={"code": "invocation_token_consumed", "status": any_inv.status.value},
        )
    if inv.thread_id != expected_thread_id or inv.agent_name != expected_agent:
        raise HTTPException(
            status_code=401,
            detail={"code": "invocation_token_invalid", "reason": "mismatch"},
        )
    if inv.purpose not in require_purposes:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "wrong_invocation_purpose",
                "actual": inv.purpose.value,
                "required": [p.value for p in require_purposes],
            },
        )
    return inv


def _verify_addressed(org, *, thread_id: str, seq: int, speaker: str) -> None:
    m = org.db.get_thread_message_by_seq(thread_id, seq)
    if m is None:
        raise HTTPException(status_code=400, detail={"code": "not_addressed", "reason": "seq missing"})
    addr = m.addressed_to or []
    if addr == ["@all"]:
        return
    if speaker not in addr:
        # bootstrap invocations: triggering_seq points at a participant_added
        # system message — accept those by checking the system payload.
        if m.kind.value == "system" and (m.system_payload or {}).get("agent_name") == speaker:
            return
        raise HTTPException(status_code=400, detail={"code": "not_addressed"})


@router.post("/threads/{thread_id}/reply")
async def reply_thread_endpoint(
    slug: str, thread_id: str, body: ReplyBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})

    body_text = body.body_markdown.strip()
    if not body_text:
        raise HTTPException(status_code=422, detail={"code": "empty_body"})

    _validate_invocation_token(
        org, token=body.invocation_token,
        expected_agent=body.speaker, expected_thread_id=thread_id,
        require_purposes=[ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP],
    )
    if not org.db.is_thread_participant(thread_id, body.speaker):
        raise HTTPException(status_code=403, detail={"code": "not_participant"})
    _verify_addressed(org, thread_id=thread_id, seq=body.in_response_to_seq, speaker=body.speaker)

    async with org.db_lock:
        # Re-validate token under lock to avoid races.
        inv = org.db.get_pending_invocation(body.invocation_token)
        if inv is None:
            raise HTTPException(status_code=409, detail={"code": "invocation_token_consumed"})
        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker=body.speaker,
            kind=ThreadMessageKind.MESSAGE, body_markdown=body_text,
        )
        org.db.consume_invocation(body.invocation_token)
        org.db.increment_thread_turns_used(thread_id, by=1)
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker=body.speaker,
            addressed_to=None, kind="message",
        )
    return {"thread_id": thread_id, "seq": seq, "kind": "message"}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_threads_routes.py -v -k reply`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/test_threads_routes.py
git commit -m "feat(routes): POST /threads/{id}/reply with token validation"
```

---

## Task 22: POST /threads/{id}/decline

**Files:**
- Modify: `src/daemon/routes/threads.py`
- Modify: `tests/test_threads_routes.py`

- [ ] **Step 1: Failing test**

Append:

```python
def test_decline_records_decline_and_consumes_token(test_org):
    client, org = test_org
    tid, token = _start_thread(client, org)
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads/{tid}/decline",
        json={"thread_id": tid, "invocation_token": token,
              "speaker": "alice", "reason": "nothing to add",
              "in_response_to_seq": 1},
    )
    assert resp.status_code == 200
    msgs = org.db.list_thread_messages(tid)
    assert msgs[-1].kind.value == "decline"
    assert msgs[-1].decline_reason == "nothing to add"
    assert org.db.get_pending_invocation(token) is None
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_threads_routes.py::test_decline_records_decline_and_consumes_token -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `src/daemon/routes/threads.py`:

```python
class DeclineBody(BaseModel):
    thread_id: str
    invocation_token: str
    speaker: str
    reason: str
    in_response_to_seq: int


@router.post("/threads/{thread_id}/decline")
async def decline_thread_endpoint(
    slug: str, thread_id: str, body: DeclineBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail={"code": "empty_reason"})
    _validate_invocation_token(
        org, token=body.invocation_token,
        expected_agent=body.speaker, expected_thread_id=thread_id,
        require_purposes=[ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP],
    )
    if not org.db.is_thread_participant(thread_id, body.speaker):
        raise HTTPException(status_code=403, detail={"code": "not_participant"})
    _verify_addressed(org, thread_id=thread_id, seq=body.in_response_to_seq, speaker=body.speaker)

    async with org.db_lock:
        if org.db.get_pending_invocation(body.invocation_token) is None:
            raise HTTPException(status_code=409, detail={"code": "invocation_token_consumed"})
        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker=body.speaker,
            kind=ThreadMessageKind.DECLINE, decline_reason=reason,
        )
        org.db.consume_invocation(body.invocation_token)
        org.db.increment_thread_turns_used(thread_id, by=1)
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker=body.speaker,
            addressed_to=None, kind="decline",
        )
    return {"thread_id": thread_id, "seq": seq, "kind": "decline"}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_threads_routes.py -v -k decline`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/test_threads_routes.py
git commit -m "feat(routes): POST /threads/{id}/decline"
```

---

## Task 23: POST /threads/{id}/dispatch with talks-dispatch parity

**Files:**
- Modify: `src/daemon/routes/threads.py`
- Modify: `tests/test_threads_routes.py`

- [ ] **Step 1: Failing tests**

Append (full coverage of the auth gates parallels the talks-dispatch test file — read `tests/test_talks_dispatch.py` first if present for patterns):

```python
def test_worker_self_dispatch_creates_task_with_thread_link(test_org):
    client, org = test_org
    register_agent(org, "alice", team="engineering")  # worker
    tid, token = _start_thread(client, org, recipient="alice")
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "alice",
              "brief": "Implement option B"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["dispatched_from_thread_id"] == tid
    assert data["assigned_agent"] == "alice"

    # System message landed.
    msgs = org.db.list_thread_messages(tid)
    sys_msg = [m for m in msgs if m.kind.value == "system"][-1]
    assert sys_msg.system_payload["kind_tag"] == "task_dispatched"

    # Token stays pending (dispatch does NOT consume).
    assert org.db.get_pending_invocation(token) is not None
    # But dispatched_task_id is recorded.
    inv = org.db.get_invocation_any_status(token)
    assert inv.dispatched_task_id == data["task_id"]


def test_worker_cannot_dispatch_to_other_agent(test_org):
    client, org = test_org
    register_agent(org, "alice", team="engineering")
    register_agent(org, "bob", team="engineering")
    tid, token = _start_thread(client, org, recipient="alice")
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads/{tid}/dispatch",
        json={"thread_id": tid, "invocation_token": token,
              "dispatcher": "alice", "target_agent": "bob",
              "brief": "do x"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "worker_must_self_dispatch"


def test_dispatch_twice_on_same_token_rejected(test_org):
    client, org = test_org
    register_agent(org, "alice", team="engineering")
    tid, token = _start_thread(client, org, recipient="alice")
    p = {"thread_id": tid, "invocation_token": token,
         "dispatcher": "alice", "brief": "x"}
    assert client.post(f"/api/v1/orgs/{org.slug}/threads/{tid}/dispatch", json=p).status_code == 200
    again = client.post(f"/api/v1/orgs/{org.slug}/threads/{tid}/dispatch", json=p)
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "dispatch_already_used"
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_threads_routes.py -v -k dispatch`
Expected: FAIL.

- [ ] **Step 3: Implement (mirrors talks dispatch — copy the role/team logic)**

Append to `src/daemon/routes/threads.py`:

```python
from src.daemon.runner import enqueue_task
from src.models import TaskRecord


class DispatchBody(BaseModel):
    thread_id: str
    invocation_token: str
    dispatcher: str
    brief: str
    target_agent: str | None = None
    team: str | None = None


@router.post("/threads/{thread_id}/dispatch")
async def dispatch_from_thread_endpoint(
    slug: str, thread_id: str, body: DispatchBody, org: OrgDep, request: Request,
) -> dict:
    state: DaemonState = request.app.state.daemon
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})
    brief = body.brief.strip()
    if not brief:
        raise HTTPException(status_code=422, detail={"code": "empty_brief"})
    if body.team is not None and not body.team.strip():
        raise HTTPException(status_code=422, detail={"code": "empty_team"})
    if body.target_agent is not None and not body.target_agent.strip():
        raise HTTPException(status_code=422, detail={"code": "empty_target_agent"})

    inv = _validate_invocation_token(
        org, token=body.invocation_token,
        expected_agent=body.dispatcher, expected_thread_id=thread_id,
        require_purposes=[ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP],
    )
    # Reject second dispatch on same token.
    if inv.dispatched_task_id is not None:
        raise HTTPException(status_code=409, detail={"code": "dispatch_already_used"})

    if not org.db.is_thread_participant(thread_id, body.dispatcher):
        raise HTTPException(status_code=403, detail={"code": "not_participant"})
    if org.teams is None:
        raise HTTPException(status_code=403, detail={"code": "teams_registry_unavailable"})

    dispatcher = body.dispatcher
    async with org.teams_lock:
        is_manager = org.teams.is_team_manager(dispatcher)
        dispatcher_team = (
            org.teams.team_for_manager(dispatcher) if is_manager
            else org.teams.team_for_agent(dispatcher)
        )
        if dispatcher_team is None:
            raise HTTPException(status_code=403, detail={"code": "dispatcher_team_unknown"})
        effective_team = body.team if body.team is not None else dispatcher_team
        if effective_team != dispatcher_team:
            raise HTTPException(
                status_code=403,
                detail={"code": "cross_team_dispatch_forbidden",
                        "dispatcher_team": dispatcher_team,
                        "requested_team": effective_team},
            )
        effective_target = body.target_agent if body.target_agent is not None else dispatcher
        if not is_manager and effective_target != dispatcher:
            raise HTTPException(
                status_code=403,
                detail={"code": "worker_must_self_dispatch",
                        "dispatcher": dispatcher,
                        "requested_target": effective_target},
            )
        if is_manager:
            team_meta = org.teams.manager_for_team(dispatcher_team)
            in_team = (
                effective_target == team_meta.name
                or effective_target in team_meta.workers
            )
            if not in_team:
                raise HTTPException(
                    status_code=403,
                    detail={"code": "target_not_in_team",
                            "team": dispatcher_team,
                            "requested_target": effective_target},
                )

    org_paths = OrgPaths(root=org.root)
    agent_def = prompt_loader.load_agent(org_paths, effective_target)
    workspace_exists = (org.root / "workspaces" / effective_target).exists()
    if agent_def is None or not workspace_exists:
        raise HTTPException(status_code=404, detail={"code": "unknown_agent", "agent": effective_target})

    async with org.db_lock:
        # Re-check token under lock.
        cur_inv = org.db.get_pending_invocation(body.invocation_token)
        if cur_inv is None or cur_inv.dispatched_task_id is not None:
            raise HTTPException(status_code=409, detail={"code": "dispatch_already_used"})
        task_id = org.db.next_task_id()
        org.db.insert_task(TaskRecord(
            id=task_id, brief=brief, team=effective_team,
            assigned_agent=effective_target,
            dispatched_from_thread_id=thread_id,
        ))
        # System message into the thread.
        sys_seq = org.db.append_thread_message(
            thread_id=thread_id, speaker=dispatcher,
            kind=ThreadMessageKind.SYSTEM,
            system_payload={
                "kind_tag": "task_dispatched",
                "task_id": task_id,
                "dispatcher": dispatcher,
                "target_agent": effective_target,
                "team": effective_team,
                "brief_preview": brief[:160],
            },
        )
        # Stamp dispatched_task_id on the invocation (token stays pending).
        org.db.record_dispatch_on_invocation(body.invocation_token, task_id=task_id)
        # Audit on the new task (talk-dispatch reuses log_task_dispatched —
        # mirror that here).
        AuditLogger(org.db).log_thread_dispatch(
            thread_id, task_id=task_id, dispatcher=dispatcher,
            target_agent=effective_target, team=effective_team,
        )

    # Outside the lock: enqueue the new task on the orchestrator queue.
    enqueue_task(state, slug, task_id)

    return {
        "task_id": task_id,
        "team": effective_team,
        "assigned_agent": effective_target,
        "dispatched_from_thread_id": thread_id,
        "system_message_seq": sys_seq,
    }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_threads_routes.py -v -k dispatch`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/test_threads_routes.py
git commit -m "feat(routes): POST /threads/{id}/dispatch with token + role gates"
```

---

## Task 24: POST /threads/{id}/send (founder follow-up)

**Files:**
- Modify: `src/daemon/routes/threads.py`
- Modify: `tests/test_threads_routes.py`

- [ ] **Step 1: Failing test**

Append:

```python
def test_founder_send_appends_and_enqueues(test_org):
    client, org = test_org
    register_agent(org, "alice")
    register_agent(org, "bob")
    r = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={"subject": "s", "recipients": ["alice", "bob"],
              "body_markdown": "hi", "addressed_to": ["alice"]},
    ).json()
    tid = r["thread_id"]
    before_invocations = len(org.db.list_thread_invocations(tid))
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads/{tid}/send",
        json={"body_markdown": "any thoughts bob?", "addressed_to": ["bob"]},
    )
    assert resp.status_code == 200
    after_invocations = len(org.db.list_thread_invocations(tid))
    assert after_invocations == before_invocations + 1
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_threads_routes.py -v -k founder_send`
Expected: FAIL.

- [ ] **Step 3: Implement**

Append to `src/daemon/routes/threads.py`:

```python
class SendBody(BaseModel):
    body_markdown: str
    addressed_to: list[str]


@router.post("/threads/{thread_id}/send")
async def send_thread_endpoint(
    slug: str, thread_id: str, body: SendBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})
    body_text = body.body_markdown.strip()
    if not body_text:
        raise HTTPException(status_code=422, detail={"code": "empty_body"})

    participants = [p.agent_name for p in org.db.list_thread_participants(thread_id)]
    _validate_addressed_to(body.addressed_to, participants)
    addressed = _resolve_addressed_agents(body.addressed_to, participants)

    # Turn-cap check.
    if t.turns_used + len(addressed) > t.turn_cap:
        raise HTTPException(
            status_code=429,
            detail={"code": "turn_cap_exceeded",
                    "used": t.turns_used, "cap": t.turn_cap,
                    "requested": len(addressed)},
        )

    tokens_to_enqueue: list[str] = []
    async with org.db_lock:
        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.MESSAGE,
            body_markdown=body_text, addressed_to=body.addressed_to,
        )
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker="founder",
            addressed_to=body.addressed_to, kind="message",
        )
        for name in addressed:
            inv = org.db.mint_thread_invocation(
                thread_id=thread_id, agent_name=name,
                triggering_seq=seq, purpose=ThreadInvocationPurpose.REPLY,
            )
            tokens_to_enqueue.append(inv.invocation_token)

    for token in tokens_to_enqueue:
        await org.thread_queue.put(ThreadJob(org_slug=slug, invocation_token=token))

    return {"thread_id": thread_id, "seq": seq, "pending_replies": addressed}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_threads_routes.py -v -k founder_send`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/test_threads_routes.py
git commit -m "feat(routes): POST /threads/{id}/send (founder follow-up)"
```

---

## Task 25: POST /threads/{id}/invite

**Files:**
- Modify: `src/daemon/routes/threads.py`
- Modify: `tests/test_threads_routes.py`

- [ ] **Step 1: Failing test**

```python
def test_invite_adds_participant_and_bootstrap_invocation(test_org):
    client, org = test_org
    register_agent(org, "alice")
    register_agent(org, "qa")
    r = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={"subject": "s", "recipients": ["alice"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
    ).json()
    tid = r["thread_id"]
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads/{tid}/invite",
        json={"agent_name": "qa"},
    )
    assert resp.status_code == 200
    parts = [p.agent_name for p in org.db.list_thread_participants(tid)]
    assert "qa" in parts
    # System message inserted.
    msgs = org.db.list_thread_messages(tid)
    sys_msgs = [m for m in msgs if m.kind.value == "system"]
    assert sys_msgs[-1].system_payload["kind_tag"] == "participant_added"
    # Bootstrap invocation minted.
    pending = org.db.list_thread_invocations(tid)
    assert any(
        inv.agent_name == "qa" and inv.purpose.value == "bootstrap"
        for inv in pending
    )


def test_invite_already_participant_409(test_org):
    client, org = test_org
    register_agent(org, "alice")
    r = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={"subject": "s", "recipients": ["alice"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
    ).json()
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads/{r['thread_id']}/invite",
        json={"agent_name": "alice"},
    )
    assert resp.status_code == 409
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_threads_routes.py -v -k invite`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
class InviteBody(BaseModel):
    agent_name: str


@router.post("/threads/{thread_id}/invite")
async def invite_thread_endpoint(
    slug: str, thread_id: str, body: InviteBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})

    org_paths = OrgPaths(root=org.root)
    agent_def = prompt_loader.load_agent(org_paths, body.agent_name)
    workspace_exists = (org.root / "workspaces" / body.agent_name).exists()
    if agent_def is None or not workspace_exists:
        raise HTTPException(status_code=404, detail={"code": "unknown_agent"})

    # Turn-cap budget reserved for the bootstrap invocation.
    if t.turns_used + 1 > t.turn_cap:
        raise HTTPException(
            status_code=429,
            detail={"code": "turn_cap_exceeded",
                    "used": t.turns_used, "cap": t.turn_cap, "requested": 1},
        )

    token_to_enqueue: str | None = None
    async with org.db_lock:
        inserted = org.db.add_thread_participant(thread_id, body.agent_name, added_by="founder")
        if not inserted:
            raise HTTPException(status_code=409, detail={"code": "already_participant"})
        sys_seq = org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.SYSTEM,
            system_payload={
                "kind_tag": "participant_added",
                "agent_name": body.agent_name,
                "added_by": "founder",
            },
        )
        AuditLogger(org.db).log_thread_participant_added(
            thread_id, agent_name=body.agent_name, added_by="founder",
        )
        inv = org.db.mint_thread_invocation(
            thread_id=thread_id, agent_name=body.agent_name,
            triggering_seq=sys_seq, purpose=ThreadInvocationPurpose.BOOTSTRAP,
        )
        token_to_enqueue = inv.invocation_token

    await org.thread_queue.put(ThreadJob(org_slug=slug, invocation_token=token_to_enqueue))
    return {"thread_id": thread_id, "agent_name": body.agent_name, "system_message_seq": sys_seq}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_threads_routes.py -v -k invite`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/test_threads_routes.py
git commit -m "feat(routes): POST /threads/{id}/invite"
```

---

## Task 26: POST /threads/{id}/extend (turn-cap bump)

**Files:**
- Modify: `src/daemon/routes/threads.py`
- Modify: `tests/test_threads_routes.py`

- [ ] **Step 1: Failing test**

```python
def test_extend_increases_turn_cap(test_org):
    client, org = test_org
    register_agent(org, "alice")
    r = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={"subject": "s", "recipients": ["alice"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
    ).json()
    tid = r["thread_id"]
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads/{tid}/extend",
        json={"new_cap": 1000},
    )
    assert resp.status_code == 200
    assert org.db.get_thread(tid).turn_cap == 1000


def test_extend_rejects_non_increase(test_org):
    client, org = test_org
    register_agent(org, "alice")
    r = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={"subject": "s", "recipients": ["alice"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
    ).json()
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads/{r['thread_id']}/extend",
        json={"new_cap": 50},
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_threads_routes.py -v -k extend`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
class ExtendBody(BaseModel):
    new_cap: int


@router.post("/threads/{thread_id}/extend")
async def extend_thread_endpoint(
    slug: str, thread_id: str, body: ExtendBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is not ThreadStatus.OPEN:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})
    if body.new_cap <= t.turn_cap:
        raise HTTPException(
            status_code=422,
            detail={"code": "new_cap_must_be_greater",
                    "current": t.turn_cap, "requested": body.new_cap},
        )
    async with org.db_lock:
        prior_cap = t.turn_cap
        org.db.set_thread_turn_cap(thread_id, new_cap=body.new_cap)
        org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.SYSTEM,
            system_payload={"kind_tag": "turn_cap_extended",
                            "prior_cap": prior_cap, "new_cap": body.new_cap},
        )
    return {"thread_id": thread_id, "turn_cap": body.new_cap}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_threads_routes.py -v -k extend`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/test_threads_routes.py
git commit -m "feat(routes): POST /threads/{id}/extend"
```

---

## Task 27: POST /threads/{id}/abandon

**Files:**
- Modify: `src/daemon/routes/threads.py`
- Modify: `tests/test_threads_routes.py`

- [ ] **Step 1: Failing test**

```python
def test_abandon_reaps_pending_and_writes_no_transcript(test_org, tmp_path):
    client, org = test_org
    register_agent(org, "alice")
    r = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={"subject": "s", "recipients": ["alice"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
    ).json()
    tid = r["thread_id"]
    # invocation is pending
    assert len(org.db.list_thread_invocations(tid)) == 1
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads/{tid}/abandon",
        json={"reason": "nothing useful"},
    )
    assert resp.status_code == 200
    t = org.db.get_thread(tid)
    assert t.status.value == "abandoned"
    assert t.transcript_path is None
    # invocations reaped
    from src.models import ThreadInvocationStatus
    pending = org.db.list_thread_invocations(tid, status=ThreadInvocationStatus.PENDING)
    assert pending == []
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_threads_routes.py -v -k abandon`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
class AbandonBody(BaseModel):
    reason: str


@router.post("/threads/{thread_id}/abandon")
async def abandon_thread_endpoint(
    slug: str, thread_id: str, body: AbandonBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status in {ThreadStatus.ARCHIVED, ThreadStatus.ABANDONED}:
        return {"thread_id": thread_id, "status": t.status.value, "idempotent": True}
    reason = body.reason.strip() or "abandoned"
    async with org.db_lock:
        org.db.set_thread_status(thread_id, status=ThreadStatus.ABANDONED)
        org.db.reap_pending_invocations(
            thread_id, purposes=None, decline_reason="thread_abandoned",
        )
        AuditLogger(org.db).log_thread_abandoned(thread_id, reason=reason)
    return {"thread_id": thread_id, "status": "abandoned"}
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_threads_routes.py -v -k abandon`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/test_threads_routes.py
git commit -m "feat(routes): POST /threads/{id}/abandon"
```

---

## Task 28: POST /threads/{id}/archive — Phase A (synchronous transition)

**Files:**
- Modify: `src/daemon/routes/threads.py`
- Modify: `tests/test_threads_routes.py`

- [ ] **Step 1: Failing test**

```python
def test_archive_phase_a_transitions_to_archiving(test_org):
    client, org = test_org
    register_agent(org, "alice")
    register_agent(org, "bob")
    r = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={"subject": "s", "recipients": ["alice", "bob"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
    ).json()
    tid = r["thread_id"]
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads/{tid}/archive",
        json={"summary": "wrapped up", "request_close_outs": True},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "archiving"
    assert data["close_out_count"] == 2  # one per participant
    # close-out invocations minted
    from src.models import ThreadInvocationPurpose
    invs = org.db.list_thread_invocations(tid)
    close_outs = [inv for inv in invs if inv.purpose is ThreadInvocationPurpose.CLOSE_OUT]
    assert len(close_outs) == 2
    # original reply invocations reaped (status='failed')
    pending = [inv for inv in invs if inv.status.value == "pending"]
    # only the 2 close-outs stay pending
    assert len(pending) == 2
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_threads_routes.py -v -k archive_phase_a`
Expected: FAIL.

- [ ] **Step 3: Implement Phase A (no background finalize yet — Task 29)**

```python
class ArchiveBody(BaseModel):
    summary: str
    request_close_outs: bool = True


@router.post("/threads/{thread_id}/archive", status_code=202)
async def archive_thread_endpoint(
    slug: str, thread_id: str, body: ArchiveBody, org: OrgDep, request: Request,
) -> dict:
    state: DaemonState = request.app.state.daemon
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status is ThreadStatus.ARCHIVED:
        return {"thread_id": thread_id, "status": "archived",
                "transcript_path": t.transcript_path, "idempotent": True}
    if t.status is ThreadStatus.ABANDONED:
        raise HTTPException(status_code=400, detail={"code": "thread_not_open"})
    if t.status is ThreadStatus.ARCHIVING:
        raise HTTPException(
            status_code=409,
            detail={"code": "archive_in_progress",
                    "archive_requested_at": t.archive_requested_at.isoformat() if t.archive_requested_at else None},
        )
    summary = body.summary.strip()

    close_out_tokens: list[str] = []
    async with org.db_lock:
        org.db.reap_pending_invocations(
            thread_id,
            purposes=[ThreadInvocationPurpose.REPLY, ThreadInvocationPurpose.BOOTSTRAP],
            decline_reason="archive_started",
        )
        org.db.set_thread_status(
            thread_id, status=ThreadStatus.ARCHIVING, summary=summary,
        )
        participants = [p.agent_name for p in org.db.list_thread_participants(thread_id)]
        # System message for the archive request (visible in transcript).
        sys_seq = org.db.append_thread_message(
            thread_id=thread_id, speaker="founder",
            kind=ThreadMessageKind.SYSTEM,
            system_payload={"kind_tag": "archive_requested", "summary": summary},
        )
        AuditLogger(org.db).log_thread_archive_requested(
            thread_id, close_out_count=len(participants) if body.request_close_outs else 0,
        )
        if body.request_close_outs:
            for name in participants:
                inv = org.db.mint_thread_invocation(
                    thread_id=thread_id, agent_name=name,
                    triggering_seq=sys_seq, purpose=ThreadInvocationPurpose.CLOSE_OUT,
                )
                close_out_tokens.append(inv.invocation_token)

    for token in close_out_tokens:
        await org.thread_queue.put(ThreadJob(org_slug=slug, invocation_token=token))

    # Schedule Phase B finalization (Task 29 wires the actual coroutine).
    # For now, expose a helper so the test can pump it in unit-test setting.
    state.thread_finalizers.spawn_finalizer(slug, thread_id)

    return {
        "thread_id": thread_id,
        "status": "archiving",
        "close_out_count": len(close_out_tokens),
        "transcript_path": None,
    }
```

Note: `state.thread_finalizers` is introduced in Task 29. For now, you can leave the `spawn_finalizer` call commented out (`# state.thread_finalizers.spawn_finalizer(...)`) so this test passes without Task 29. Re-enable it after Task 29 lands.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_threads_routes.py -v -k archive_phase_a`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/test_threads_routes.py
git commit -m "feat(routes): /archive Phase A — transition to archiving + mint close-outs"
```

---

## Task 29: Archive Phase B — background finalizer

**Files:**
- Create: `src/daemon/thread_archive_finalizer.py`
- Modify: `src/daemon/state.py` (add `thread_finalizers` registry)
- Modify: `src/daemon/routes/threads.py` (uncomment `spawn_finalizer`)
- Create: `tests/test_thread_archive_finalizer.py`

- [ ] **Step 1: Failing test**

Create `tests/test_thread_archive_finalizer.py`:

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.daemon.thread_archive_finalizer import finalize_thread
from src.infrastructure.database import Database
from src.models import (
    ThreadInvocationPurpose, ThreadMessageKind, ThreadRecord,
    ThreadStatus,
)


@pytest.mark.asyncio
async def test_finalize_thread_writes_transcript_and_archives(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi", addressed_to=["@all"],
    )
    db.set_thread_status("THR-001", status=ThreadStatus.ARCHIVING, summary="done")
    # No pending close-outs → finalizer returns immediately.
    from src.infrastructure.thread_store import ThreadStore
    store = ThreadStore(tmp_path / "threads")
    await finalize_thread(
        db=db, store=store, thread_id="THR-001",
        close_out_wait_seconds=2,
    )
    t = db.get_thread("THR-001")
    assert t.status is ThreadStatus.ARCHIVED
    assert t.transcript_path is not None


@pytest.mark.asyncio
async def test_finalize_waits_for_close_outs_or_times_out(tmp_path):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.set_thread_status("THR-001", status=ThreadStatus.ARCHIVING, summary="done")
    db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.CLOSE_OUT,
    )
    from src.infrastructure.thread_store import ThreadStore
    store = ThreadStore(tmp_path / "threads")
    start = asyncio.get_event_loop().time()
    await finalize_thread(
        db=db, store=store, thread_id="THR-001",
        close_out_wait_seconds=1,
    )
    elapsed = asyncio.get_event_loop().time() - start
    # Hit the timeout (~1s), reaped, finalized.
    assert 0.9 <= elapsed <= 2.0
    assert db.get_thread("THR-001").status is ThreadStatus.ARCHIVED
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_thread_archive_finalizer.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the finalizer**

Create `src/daemon/thread_archive_finalizer.py`:

```python
"""Background finalizer that moves a thread from 'archiving' to 'archived'.

Waits up to `close_out_wait_seconds` for all pending close-out invocations to
land (consume/timeout/fail), then writes the transcript and flips status.
"""
from __future__ import annotations

import asyncio
import logging

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.thread_store import ThreadStore, render_transcript_body
from src.models import ThreadInvocationStatus

logger = logging.getLogger(__name__)


async def finalize_thread(
    *,
    db,
    store: ThreadStore,
    thread_id: str,
    close_out_wait_seconds: int,
) -> None:
    deadline = asyncio.get_event_loop().time() + close_out_wait_seconds
    while True:
        pending = [
            inv for inv in db.list_thread_invocations(
                thread_id, status=ThreadInvocationStatus.PENDING,
            )
        ]
        if not pending:
            break
        if asyncio.get_event_loop().time() >= deadline:
            # Reap remaining close-outs as timeouts.
            db.reap_pending_invocations(
                thread_id, purposes=None, decline_reason="close_out_timeout",
            )
            break
        await asyncio.sleep(0.25)

    thread = db.get_thread(thread_id)
    if thread is None or thread.status.value != "archiving":
        logger.warning("finalize_thread: thread %s not in archiving state", thread_id)
        return

    participants = [p.agent_name for p in db.list_thread_participants(thread_id)]
    msgs = db.list_thread_messages(thread_id, limit=10000)
    rendered = render_transcript_body(msgs)
    summary = thread.summary or ""
    # Compute archived_at now so transcript and DB row agree.
    from datetime import datetime, timezone
    archived_at = datetime.now(timezone.utc)
    transcript_path = store.write_transcript(
        thread_id=thread_id,
        subject=thread.subject,
        started_at=thread.started_at,
        archived_at=archived_at,
        participants=participants,
        turns_used=thread.turns_used,
        new_learnings_total=0,  # Task 30 fills this from close-out callbacks.
        new_kb_slugs=thread.new_kb_slugs,
        forwarded_from_id=thread.forwarded_from_id,
        summary=summary,
        rendered_transcript=rendered,
    )
    db.append_thread_message(
        thread_id=thread_id, speaker="founder",
        kind=ThreadMessageKind.SYSTEM if False else __import__("src.models", fromlist=["ThreadMessageKind"]).ThreadMessageKind.SYSTEM,
        system_payload={
            "kind_tag": "archived",
            "new_kb_slugs": thread.new_kb_slugs,
        },
    )
    db.finalize_thread_archived(
        thread_id, transcript_path=str(transcript_path),
        new_kb_slugs=thread.new_kb_slugs,
    )
    AuditLogger(db).log_thread_archived(
        thread_id,
        new_learnings_total=0,
        new_kb_slugs=thread.new_kb_slugs,
        turns_used=thread.turns_used,
    )
```

Replace the silly `if False` line with the clean import at the top of the file: `from src.models import ThreadMessageKind` and use it directly. The pattern shown was just to make the diff intent visible — clean it up.

- [ ] **Step 4: Wire `state.thread_finalizers`**

In `src/daemon/state.py`:

```python
class ThreadFinalizerRegistry:
    """Tracks in-flight archive finalizers so we don't spawn duplicates."""
    def __init__(self) -> None:
        self._active: dict[tuple[str, str], asyncio.Task] = {}

    def spawn_finalizer(self, slug: str, thread_id: str, *,
                        org_state, close_out_wait_seconds: int = 300) -> None:
        key = (slug, thread_id)
        if key in self._active and not self._active[key].done():
            return
        async def _runner():
            try:
                await finalize_thread(
                    db=org_state.db, store=org_state.thread_store,
                    thread_id=thread_id,
                    close_out_wait_seconds=close_out_wait_seconds,
                )
            finally:
                self._active.pop(key, None)
        self._active[key] = asyncio.create_task(_runner())
```

Add to `DaemonState.__init__`:

```python
from src.daemon.thread_archive_finalizer import finalize_thread
self.thread_finalizers = ThreadFinalizerRegistry()
```

- [ ] **Step 5: Re-enable spawn_finalizer in routes/threads.py**

In the `/archive` endpoint, replace the commented-out line with:

```python
state.thread_finalizers.spawn_finalizer(
    slug, thread_id,
    org_state=org,
    close_out_wait_seconds=getattr(
        getattr(org, "config", None),
        "threads_close_out_wait_seconds",
        300,
    ),
)
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_thread_archive_finalizer.py tests/test_threads_routes.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/daemon/thread_archive_finalizer.py src/daemon/state.py src/daemon/routes/threads.py tests/test_thread_archive_finalizer.py
git commit -m "feat(daemon): archive Phase B finalizer + ThreadFinalizerRegistry"
```

---

## Task 30: POST /threads/{id}/close-out

**Files:**
- Modify: `src/daemon/routes/threads.py`
- Modify: `tests/test_threads_routes.py`

- [ ] **Step 1: Failing test**

```python
def test_close_out_writes_learnings_and_kb_slugs(test_org):
    client, org = test_org
    register_agent(org, "alice")
    r = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={"subject": "s", "recipients": ["alice"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
    ).json()
    tid = r["thread_id"]
    # Archive Phase A → mints close-out invocation for alice.
    client.post(
        f"/api/v1/orgs/{org.slug}/threads/{tid}/archive",
        json={"summary": "done", "request_close_outs": True},
    )
    inv = next(
        i for i in org.db.list_thread_invocations(tid)
        if i.purpose.value == "close_out" and i.agent_name == "alice"
    )
    # Need a KB slug pre-existing in the KB store, since the endpoint validates it.
    # Use the helper that the talks tests use to seed a KB row.
    # (The KB API is documented in protocol/06-knowledge-base.md.)
    from src.infrastructure.kb_store import KBStore
    KBStore(org.root / "kb").write_entry(
        slug="thread-learning",
        markdown="# Thread learning\n\nbody.",
        author="alice",
    )
    resp = client.post(
        f"/api/v1/orgs/{org.slug}/threads/{tid}/close-out",
        json={"thread_id": tid, "invocation_token": inv.invocation_token,
              "agent": "alice",
              "learnings": [{"text": "refunds beyond 30d are fine."}],
              "kb_slugs": ["thread-learning"]},
    )
    assert resp.status_code == 200, resp.text
    assert "thread-learning" in org.db.get_thread(tid).new_kb_slugs
    # Token consumed.
    assert org.db.get_pending_invocation(inv.invocation_token) is None
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_threads_routes.py -v -k close_out`
Expected: FAIL.

- [ ] **Step 3: Implement**

```python
class CloseOutLearning(BaseModel):
    text: str


class CloseOutBody(BaseModel):
    thread_id: str
    invocation_token: str
    agent: str
    learnings: list[CloseOutLearning] = []
    kb_slugs: list[str] = []


@router.post("/threads/{thread_id}/close-out")
async def close_out_thread_endpoint(
    slug: str, thread_id: str, body: CloseOutBody, org: OrgDep,
) -> dict:
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    if t.status not in {ThreadStatus.OPEN, ThreadStatus.ARCHIVING}:
        raise HTTPException(status_code=400, detail={"code": "thread_already_finalized"})
    _validate_invocation_token(
        org, token=body.invocation_token,
        expected_agent=body.agent, expected_thread_id=thread_id,
        require_purposes=[ThreadInvocationPurpose.CLOSE_OUT],
    )
    if not org.db.is_thread_participant(thread_id, body.agent):
        raise HTTPException(status_code=403, detail={"code": "not_participant"})

    # Verify each kb_slug exists; reject if not.
    from src.infrastructure.kb_store import KBStore
    kb = KBStore(org.root / "kb")
    for slug_id in body.kb_slugs:
        if kb.read_entry(slug_id) is None:
            raise HTTPException(
                status_code=400,
                detail={"code": "kb_slug_not_found", "slug": slug_id},
            )

    # Append learnings to the agent's learnings.md via the same helper talks use.
    from src.daemon.routes.agents import _append_to_learnings_file
    workspace = org.root / "workspaces" / body.agent
    for entry in body.learnings:
        _append_to_learnings_file(workspace, entry.text)

    async with org.db_lock:
        if org.db.get_pending_invocation(body.invocation_token) is None:
            raise HTTPException(status_code=409, detail={"code": "invocation_token_consumed"})
        org.db.consume_invocation(body.invocation_token)
        for slug_id in body.kb_slugs:
            org.db.add_thread_kb_slug(thread_id, slug_id)
        AuditLogger(org.db).log_thread_close_out_received(
            thread_id, agent=body.agent,
            new_learnings_count=len(body.learnings),
            new_kb_slugs=body.kb_slugs,
        )
    return {
        "thread_id": thread_id, "agent": body.agent,
        "new_learnings_count": len(body.learnings),
        "new_kb_slugs": body.kb_slugs,
    }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_threads_routes.py -v -k close_out`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/test_threads_routes.py
git commit -m "feat(routes): POST /threads/{id}/close-out"
```

---

## Task 31: SSE endpoints — `/threads/events` + `/threads/{id}/tail`

**Files:**
- Modify: `src/daemon/routes/threads.py`
- Modify: `src/daemon/event_bus.py` (extend with thread topics)

- [ ] **Step 1: Find the existing event bus pattern**

Run: `grep -n "class EventBus\|publish\|subscribe" src/daemon/event_bus.py | head -30`

Read the patterns. Existing per-task SSE is the precedent (see `src/daemon/routes/tasks.py` SSE handler).

- [ ] **Step 2: Add thread topics**

In `src/daemon/event_bus.py`, add helpers (adapt names to existing patterns):

```python
def thread_topic(thread_id: str) -> str:
    return f"thread:{thread_id}"


def thread_inbox_topic(org_slug: str) -> str:
    return f"thread_inbox:{org_slug}"
```

- [ ] **Step 3: Publish on each thread state change**

Inside each endpoint that appends a message (`compose`, `send`, `reply`, `decline`, `dispatch`, `invite`, `extend`, `archive`, `close-out`, `abandon`) — after the audit log call, publish:

```python
# Per-thread tail event.
await state.event_bus.publish(
    thread_topic(thread_id),
    {"thread_id": thread_id, "seq": seq, "speaker": speaker_name, "kind": kind_name, "preview": (body_text or "")[:160]},
)
# Org-wide inbox event.
await state.event_bus.publish(
    thread_inbox_topic(slug),
    {"thread_id": thread_id, "event_kind": kind_name, "status": new_status},
)
```

Pick a single helper inside the route module to centralize this:

```python
async def _publish_thread_event(state, slug: str, *,
                                thread_id: str, seq: int | None,
                                speaker: str, kind: str,
                                preview: str = "", status: str = "open") -> None:
    await state.event_bus.publish(
        thread_topic(thread_id),
        {"thread_id": thread_id, "seq": seq, "speaker": speaker,
         "kind": kind, "preview": preview[:160]},
    )
    await state.event_bus.publish(
        thread_inbox_topic(slug),
        {"thread_id": thread_id, "event_kind": kind, "status": status},
    )
```

Then call `_publish_thread_event(...)` from each route after the DB write.

- [ ] **Step 4: SSE handler endpoints**

Append to `src/daemon/routes/threads.py`:

```python
from fastapi.responses import StreamingResponse


@router.get("/threads/{thread_id}/tail")
async def tail_thread_endpoint(
    slug: str, thread_id: str, org: OrgDep, request: Request, since_seq: int = 0,
) -> StreamingResponse:
    state: DaemonState = request.app.state.daemon
    t = org.db.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"code": "not_found"})

    async def gen():
        # Replay missed messages first.
        for m in org.db.list_thread_messages(thread_id, since_seq=since_seq, limit=1000):
            yield f"data: {json.dumps(_msg_to_dict(m))}\n\n"
        # Live updates.
        async for event in state.event_bus.subscribe(thread_topic(thread_id)):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/threads/events")
async def threads_inbox_events_endpoint(
    slug: str, request: Request,
) -> StreamingResponse:
    state: DaemonState = request.app.state.daemon

    async def gen():
        async for event in state.event_bus.subscribe(thread_inbox_topic(slug)):
            yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
```

Add `import json` at the top of the routes file if not already there.

- [ ] **Step 5: Smoke test SSE**

Append to `tests/test_threads_routes.py`:

```python
def test_tail_sse_replays_existing_messages(test_org):
    client, org = test_org
    register_agent(org, "alice")
    r = client.post(
        f"/api/v1/orgs/{org.slug}/threads",
        json={"subject": "s", "recipients": ["alice"],
              "body_markdown": "hi", "addressed_to": ["@all"]},
    ).json()
    tid = r["thread_id"]
    # Use TestClient.stream context manager.
    with client.stream("GET", f"/api/v1/orgs/{org.slug}/threads/{tid}/tail") as resp:
        assert resp.status_code == 200
        # Read one event chunk (replay of msg seq=1).
        for line in resp.iter_lines():
            if line.startswith("data:"):
                assert "hi" in line
                break
```

Run: `uv run pytest tests/test_threads_routes.py -v -k tail_sse`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/event_bus.py src/daemon/routes/threads.py tests/test_threads_routes.py
git commit -m "feat(routes): SSE /threads/events and /threads/{id}/tail"
```

---

## Task 32: ThreadInvocationRunner — prompt builder

**Files:**
- Create: `src/daemon/thread_runner.py`
- Create: `tests/test_thread_runner.py`

- [ ] **Step 1: Failing test**

Create `tests/test_thread_runner.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

from src.daemon.thread_runner import build_thread_prompt
from src.models import (
    ThreadMessage, ThreadMessageKind, ThreadParticipant, ThreadRecord,
)


def test_build_prompt_includes_token_and_history():
    thread = ThreadRecord(
        id="THR-001", subject="Refund policy",
        started_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    participants = [
        ThreadParticipant(thread_id="THR-001", agent_name="alice"),
        ThreadParticipant(thread_id="THR-001", agent_name="bob"),
    ]
    msgs = [
        ThreadMessage(
            thread_id="THR-001", seq=1, speaker="founder",
            kind=ThreadMessageKind.MESSAGE,
            body_markdown="should we cap?",
            addressed_to=["@all"],
        ),
    ]
    prompt = build_thread_prompt(
        thread=thread, participants=participants, messages=msgs,
        invocation_token="TOK-ABC",
        invoked_agent="alice", purpose="reply", triggering_seq=1,
    )
    assert "THR-001" in prompt
    assert "Refund policy" in prompt
    assert "TOK-ABC" in prompt
    assert "Message 1 — founder" in prompt
    assert "should we cap?" in prompt
    assert "addressed @all" in prompt.lower() or "@all" in prompt
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_thread_runner.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement prompt builder**

Create `src/daemon/thread_runner.py`:

```python
"""Headless executor invocation for thread participation.

Single-turn lifecycle: build prompt → spawn subprocess → wait for token to be
consumed (via reply/decline/close-out callback) → exit. No NextStep loop.
"""
from __future__ import annotations

from src.models import (
    ThreadMessage, ThreadMessageKind, ThreadParticipant, ThreadRecord,
)


def _render_message(m: ThreadMessage) -> str:
    ts = m.created_at.isoformat()
    if m.kind is ThreadMessageKind.MESSAGE:
        head = f"[Message {m.seq} — {m.speaker} · {ts}]"
        addressed = f"To: {', '.join(m.addressed_to)}" if m.addressed_to else ""
        body = m.body_markdown or ""
        return "\n".join(filter(None, [head, addressed, "", body])) + "\n---"
    if m.kind is ThreadMessageKind.DECLINE:
        return (
            f"[Message {m.seq} — {m.speaker} · {ts}]\n"
            f"👁 declined: {m.decline_reason or ''}\n---"
        )
    # system
    payload = m.system_payload or {}
    tag = payload.get("kind_tag", "system")
    return f"[Message {m.seq} — {m.speaker} · {ts}]\nsystem: {tag} · {payload}\n---"


def _purpose_note(purpose: str, triggering_seq: int, addressed_to: list[str] | None,
                  invoked_agent: str) -> str:
    if purpose == "bootstrap":
        return "The founder has added you to this thread"
    if purpose == "close_out":
        return "This thread is being archived; provide a close-out"
    # purpose == "reply"
    addr = addressed_to or []
    if addr == ["@all"]:
        return f"Message {triggering_seq} addressed @all"
    if invoked_agent in addr:
        return f"Message {triggering_seq} addressed you individually"
    return f"Message {triggering_seq} (no explicit addressee)"


def build_thread_prompt(
    *,
    thread: ThreadRecord,
    participants: list[ThreadParticipant],
    messages: list[ThreadMessage],
    invocation_token: str,
    invoked_agent: str,
    purpose: str,                # 'reply' | 'bootstrap' | 'close_out'
    triggering_seq: int,
) -> str:
    triggering = next((m for m in messages if m.seq == triggering_seq), None)
    addressed_to = triggering.addressed_to if triggering else None
    parts_str = ", ".join(p.agent_name for p in participants)
    history = "\n".join(_render_message(m) for m in messages)
    forwarded = (
        f"Forwarded from {thread.forwarded_from_id}."
        if thread.forwarded_from_id else ""
    )
    note = _purpose_note(purpose, triggering_seq, addressed_to, invoked_agent)
    return (
        f"You are participating in thread {thread.id}: \"{thread.subject}\".\n\n"
        f"Participants: {parts_str}.\n"
        f"Started: {thread.started_at.isoformat()}. {forwarded}\n\n"
        f"Full message history follows. Most recent message is at the bottom.\n\n"
        f"---\n{history}\n\n"
        f"You have been invoked because:\n  {note}\n\n"
        f"Your invocation_token for this turn is: {invocation_token}\n"
        f"Include this token in every callback payload (reply, decline, dispatch,\n"
        f"close-out). It authorizes this single turn and is single-use for the\n"
        f"terminal callback (reply/decline/close-out).\n\n"
        f"Consult `protocol/skills/thread/SKILL.md` and respond.\n"
    )
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_thread_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/thread_runner.py tests/test_thread_runner.py
git commit -m "feat(daemon): build_thread_prompt"
```

---

## Task 33: ThreadInvocationRunner — subprocess execution + outcome observation

**Files:**
- Modify: `src/daemon/thread_runner.py`
- Modify: `tests/test_thread_runner.py`

- [ ] **Step 1: Add `run_invocation` function**

Append to `src/daemon/thread_runner.py`:

```python
import asyncio
import logging
from pathlib import Path

from src.config import Settings
from src.infrastructure.audit_logger import AuditLogger
from src.models import (
    ThreadInvocationPurpose, ThreadInvocationStatus, ThreadMessageKind,
)
from src.orchestrator.executors import (
    ClaudeExecutor, CodexExecutor, OpencodeExecutor,
)
from src.orchestrator._paths import OrgPaths

logger = logging.getLogger(__name__)


_EXECUTOR_MAP = {
    "claude": ClaudeExecutor,
    "codex": CodexExecutor,
    "opencode": OpencodeExecutor,
}


async def run_invocation(
    *,
    org_state,
    invocation_token: str,
    settings: Settings,
) -> None:
    """Execute one thread invocation end-to-end.

    Reads the pending row, builds the prompt, spawns the executor subprocess,
    waits for the token to leave 'pending' (via callback) within the timeout,
    and records auto-decline rows on no-callback / timeout / failure.
    """
    inv = org_state.db.get_pending_invocation(invocation_token)
    if inv is None:
        logger.info("run_invocation: token %s already non-pending", invocation_token[:8])
        return

    thread = org_state.db.get_thread(inv.thread_id)
    if thread is None:
        org_state.db.fail_invocation(
            invocation_token,
            status=ThreadInvocationStatus.FAILED,
            decline_reason="thread_missing",
        )
        return

    participants = org_state.db.list_thread_participants(inv.thread_id)
    messages = org_state.db.list_thread_messages(inv.thread_id, limit=10000)

    prompt = build_thread_prompt(
        thread=thread, participants=participants, messages=messages,
        invocation_token=invocation_token,
        invoked_agent=inv.agent_name,
        purpose=inv.purpose.value,
        triggering_seq=inv.triggering_seq,
    )

    # Resolve workspace + executor.
    workspace = org_state.root / "workspaces" / inv.agent_name
    from src.daemon.agent_config import read_agent_config
    agent_yaml = read_agent_config(workspace)
    executor_name = (agent_yaml or {}).get("executor", "claude")
    executor_cls = _EXECUTOR_MAP.get(executor_name, ClaudeExecutor)
    executor = executor_cls(settings=settings)

    # Resolve per-org / global timeout.
    org_cfg = getattr(org_state, "config", None)
    timeout = (
        getattr(org_cfg, "threads_invocation_timeout_seconds", None)
        if org_cfg else None
    )
    if timeout is None:
        timeout = settings.session_timeout_seconds

    org_state.db.stamp_invocation_started(invocation_token, session_id=None)

    # Spawn subprocess. The agent is responsible for calling
    # `opc threads reply|decline|close-out` with the invocation_token.
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: executor.run(
                prompt=prompt,
                workspace=workspace,
                task_id=inv.thread_id,    # used for audit grouping
                agent=inv.agent_name,
                session_timeout_seconds=timeout,
            ),
        )
    except Exception as exc:  # subprocess crash
        org_state.db.fail_invocation(
            invocation_token,
            status=ThreadInvocationStatus.FAILED,
            decline_reason=f"runner_crash: {exc}",
        )
        AuditLogger(org_state.db).log_thread_invocation_failed(
            inv.thread_id, agent=inv.agent_name, token=invocation_token,
            purpose=inv.purpose.value, reason=str(exc),
        )
        return

    # After subprocess exit, check the token state.
    after = org_state.db.get_invocation_any_status(invocation_token)
    if after is None:
        return  # row vanished — shouldn't happen.
    if after.status is ThreadInvocationStatus.CONSUMED:
        return  # success
    # Subprocess exited without consuming → auto-decline.
    reason = (
        "invocation_timeout"
        if (not getattr(result, "success", True))
        and "timeout" in str(getattr(result, "error", "")).lower()
        else f"no_callback: rc={getattr(result, 'returncode', '?')}"
    )
    status = (
        ThreadInvocationStatus.TIMEOUT
        if reason == "invocation_timeout"
        else ThreadInvocationStatus.FAILED
    )
    org_state.db.fail_invocation(
        invocation_token, status=status, decline_reason=reason,
    )
    if inv.purpose is not ThreadInvocationPurpose.CLOSE_OUT:
        # Insert an auto-decline message so the founder sees the absence.
        org_state.db.append_thread_message(
            thread_id=inv.thread_id, speaker=inv.agent_name,
            kind=ThreadMessageKind.DECLINE,
            decline_reason=reason,
        )
        org_state.db.increment_thread_turns_used(inv.thread_id, by=1)
    AuditLogger(org_state.db).log_thread_invocation_failed(
        inv.thread_id, agent=inv.agent_name, token=invocation_token,
        purpose=inv.purpose.value, reason=reason,
        kind="thread_invocation_failed",
    )
```

- [ ] **Step 2: Failing test with stub executor**

Append to `tests/test_thread_runner.py`:

```python
import pytest

from src.daemon.thread_runner import run_invocation
from src.config import Settings
from src.infrastructure.database import Database


class FakeExecutorResult:
    def __init__(self, success, error=""):
        self.success = success
        self.error = error
        self.returncode = 0
        self.session_id = "sess-x"


class FakeOrgState:
    def __init__(self, db, root):
        self.db = db
        self.root = root


@pytest.mark.asyncio
async def test_run_invocation_no_callback_inserts_auto_decline(tmp_path, monkeypatch):
    db = Database(tmp_path / "opc.db")
    db.insert_thread(ThreadRecord(id="THR-001", subject="x"))
    db.add_thread_participant("THR-001", "alice", added_by="founder")
    db.append_thread_message(
        thread_id="THR-001", speaker="founder",
        kind=ThreadMessageKind.MESSAGE, body_markdown="hi", addressed_to=["@all"],
    )
    inv = db.mint_thread_invocation(
        thread_id="THR-001", agent_name="alice",
        triggering_seq=1, purpose=ThreadInvocationPurpose.REPLY,
    )
    # Workspace stub so the runner finds agent.yaml.
    ws = tmp_path / "workspaces" / "alice"
    ws.mkdir(parents=True)
    (ws / "agent.yaml").write_text("executor: claude\n")

    # Monkeypatch the executor class to return a "no callback" result.
    import src.daemon.thread_runner as runner_mod
    class _FakeExec:
        def __init__(self, settings): pass
        def run(self, **kwargs): return FakeExecutorResult(success=True)
    monkeypatch.setitem(runner_mod._EXECUTOR_MAP, "claude", _FakeExec)

    org = FakeOrgState(db=db, root=tmp_path)
    await run_invocation(
        org_state=org, invocation_token=inv.invocation_token,
        settings=Settings(),
    )
    msgs = db.list_thread_messages("THR-001")
    # Auto-decline appended.
    assert any(m.kind.value == "decline" for m in msgs)
    inv_after = db.get_invocation_any_status(inv.invocation_token)
    assert inv_after.status.value in {"failed", "timeout"}
```

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/test_thread_runner.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/daemon/thread_runner.py tests/test_thread_runner.py
git commit -m "feat(daemon): run_invocation — subprocess + auto-decline on no callback"
```

---

## Task 34: ThreadQueue worker loop (lifespan integration)

**Files:**
- Modify: `src/daemon/app.py`
- Modify: `src/daemon/thread_queue.py`

- [ ] **Step 1: Worker function**

Append to `src/daemon/thread_queue.py`:

```python
import logging

from src.config import Settings
from src.daemon.thread_runner import run_invocation

logger = logging.getLogger(__name__)


async def thread_worker_loop(state, settings: Settings) -> None:
    """Single worker task that drains ThreadJobs across all orgs.

    Multiple workers can be spawned; the work-stealing is implicit since each
    org's queue is its own `asyncio.Queue`. For now this worker round-robins
    by sampling each org's queue in turn — keep it simple, add per-org workers
    later if contention becomes an issue.
    """
    while True:
        # Round-robin across all orgs.
        all_orgs = list(state.orgs.values())
        if not all_orgs:
            await asyncio.sleep(0.5)
            continue
        for org in all_orgs:
            if org.thread_queue.size == 0:
                continue
            try:
                job = await asyncio.wait_for(org.thread_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            try:
                await run_invocation(
                    org_state=org,
                    invocation_token=job.invocation_token,
                    settings=settings,
                )
            except Exception:
                logger.exception(
                    "thread_worker_loop: invocation %s crashed",
                    job.invocation_token[:8],
                )
        await asyncio.sleep(0.05)
```

- [ ] **Step 2: Spawn workers in lifespan**

In `src/daemon/app.py`, find the existing lifespan handler. Add to startup:

```python
import asyncio

# Inside _lifespan:
worker_count = 4
state.thread_worker_tasks = [
    asyncio.create_task(thread_worker_loop(state, state.settings))
    for _ in range(worker_count)
]
```

And on shutdown:

```python
for t in getattr(state, "thread_worker_tasks", []):
    t.cancel()
```

- [ ] **Step 3: Verify daemon still starts**

Run: `uv run python -c "from src.daemon.app import create_app; create_app(); print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add src/daemon/thread_queue.py src/daemon/app.py
git commit -m "feat(daemon): thread worker loop in lifespan"
```

---

## Task 35: thread_forward — body builder for talks/threads

**Files:**
- Create: `src/daemon/thread_forward.py`
- Create: `tests/test_thread_forward.py`

- [ ] **Step 1: Failing test**

Create `tests/test_thread_forward.py`:

```python
from __future__ import annotations

from src.daemon.thread_forward import build_forward_body_from_talk, build_forward_body_from_thread


def test_build_forward_body_from_talk_truncates_at_4kib():
    talk_summary = "x" * 8000
    body = build_forward_body_from_talk(
        source_id="TALK-008", summary=talk_summary, agent_name="alice",
    )
    assert "TALK-008" in body
    assert "alice" in body
    assert len(body.encode("utf-8")) <= 4096 + 200  # 4 KiB + small overhead


def test_build_forward_body_from_thread_quotes_messages():
    from src.models import ThreadMessage, ThreadMessageKind
    from datetime import datetime, timezone
    msgs = [
        ThreadMessage(thread_id="THR-1", seq=1, speaker="founder",
                      kind=ThreadMessageKind.MESSAGE, body_markdown="hello",
                      created_at=datetime(2026, 5, 13, tzinfo=timezone.utc)),
        ThreadMessage(thread_id="THR-1", seq=2, speaker="alice",
                      kind=ThreadMessageKind.MESSAGE, body_markdown="hi back",
                      created_at=datetime(2026, 5, 13, tzinfo=timezone.utc)),
    ]
    body = build_forward_body_from_thread(
        source_id="THR-001", messages=msgs, subject="Refund",
    )
    assert "THR-001" in body
    assert "Refund" in body
    assert "hello" in body
    assert "hi back" in body
    assert body.lstrip().startswith(">")
```

- [ ] **Step 2: Run, verify failure**

Run: `uv run pytest tests/test_thread_forward.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Create `src/daemon/thread_forward.py`:

```python
"""Build forwarded-context blocks for new threads."""
from __future__ import annotations

from src.models import ThreadMessage, ThreadMessageKind

_MAX_QUOTED_BYTES = 4096


def _truncate(s: str, *, limit: int = _MAX_QUOTED_BYTES) -> str:
    encoded = s.encode("utf-8")
    if len(encoded) <= limit:
        return s
    return encoded[:limit].decode("utf-8", errors="ignore") + "\n\n(... source truncated)"


def build_forward_body_from_talk(*, source_id: str, summary: str, agent_name: str) -> str:
    body = (
        f"> **Forwarded from {source_id}** (talk with {agent_name})\n>\n"
        f"> {_truncate(summary).strip().replace(chr(10), chr(10) + '> ')}\n\n"
        "---\n\n"
    )
    return body


def build_forward_body_from_thread(*, source_id: str, messages: list[ThreadMessage], subject: str) -> str:
    quoted_lines: list[str] = [
        f"> **Forwarded from {source_id}** (thread: {subject})",
        ">",
    ]
    rendered = []
    for m in messages:
        if m.kind is ThreadMessageKind.MESSAGE:
            rendered.append(f"> {m.speaker}: {(m.body_markdown or '').strip()}")
        elif m.kind is ThreadMessageKind.DECLINE:
            rendered.append(f"> ({m.speaker} declined: {m.decline_reason})")
        elif m.kind is ThreadMessageKind.SYSTEM:
            tag = (m.system_payload or {}).get("kind_tag", "system")
            rendered.append(f"> (system: {tag})")
    quoted = "\n".join(rendered)
    truncated = _truncate(quoted)
    return "\n".join(quoted_lines + [truncated, "", "---", ""])
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_thread_forward.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/thread_forward.py tests/test_thread_forward.py
git commit -m "feat(daemon): thread_forward helpers (talk/thread → quoted body)"
```

---

## Task 36: protocol/skills/thread/SKILL.md

**Files:**
- Create: `protocol/skills/thread/SKILL.md`

- [ ] **Step 1: Write the skill file**

Create `protocol/skills/thread/SKILL.md` with the body sketched in `docs/superpowers/specs/2026-05-13-threads-design.md` §10.2. Copy that section's content verbatim into the file, with the YAML frontmatter:

```markdown
---
name: thread
description: Use this skill when the orchestrator invokes you for thread participation. Decide whether to reply, decline, or dispatch a task — all based on the thread context provided in your prompt.
---

# thread

(... paste body from §10.2 of the spec verbatim ...)
```

The spec body is the source of truth — do not paraphrase or shorten.

- [ ] **Step 2: Update workspace adapters to copy the new skill into workspaces**

In `src/orchestrator/workspace_adapters.py`, find where existing skills (`start-task`, `talk`, `dispatch`, `manage-repo`, `manage-agent`, `make-worktree`) are copied. Add `thread` to that list:

```python
SHARED_SKILLS = [
    "start-task",
    "talk",
    "dispatch",
    "thread",          # NEW
    "manage-repo",
    "manage-agent",
    "make-worktree",
]
```

Adapt to the actual existing constant / list. Run `grep -n "start-task\|talk\b" src/orchestrator/workspace_adapters.py` to find the right place.

- [ ] **Step 3: Re-run init-agent for the sample org and confirm the skill lands**

Run: `uv run opc init-agent --org hk-macau-tourism alice 2>&1 | tail -5` (or whichever agent exists in your dev runtime). Confirm: `ls <runtime>/orgs/hk-macau-tourism/workspaces/alice/.claude/skills/` lists `thread`.

If you don't have a runtime set up, skip the manual check.

- [ ] **Step 4: Commit**

```bash
git add protocol/skills/thread/SKILL.md src/orchestrator/workspace_adapters.py
git commit -m "feat(skills): thread skill + workspace adapter copies it"
```

---

## Task 37: CLI — founder subcommands (compose, list, show)

**Files:**
- Modify: `src/cli.py`

- [ ] **Step 1: Locate the existing `talks` CLI group**

Run: `grep -n "@app.group\|def talks\|talks_app\|TyperOrClickPattern" src/cli.py | head -20`. Note the framework (likely Typer or Click). Mirror its shape for the new `threads` group.

- [ ] **Step 2: Add `opc threads compose`**

In `src/cli.py`:

```python
import json

threads_app = typer.Typer(help="Email-style multi-agent threads.")
app.add_typer(threads_app, name="threads")


@threads_app.command("compose")
def threads_compose(
    org: str = typer.Option(..., "--org"),
    from_file: Path = typer.Option(..., "--from-file"),
):
    """Compose a new thread from a JSON file with subject/recipients/body/addressed_to."""
    payload = json.loads(from_file.read_text(encoding="utf-8"))
    resp = client.post(f"/api/v1/orgs/{org}/threads", json=payload)
    print(json.dumps(resp.json(), indent=2))
```

Adapt to the actual CLI framework / config object usage. Reference `opc talk start` for the pattern.

- [ ] **Step 3: Add `opc threads list` and `opc threads show`**

```python
@threads_app.command("list")
def threads_list(
    org: str = typer.Option(..., "--org"),
    status: str = typer.Option(None, "--status"),
    limit: int = typer.Option(50, "--limit"),
):
    params = {"limit": limit}
    if status:
        params["status"] = status
    resp = client.get(f"/api/v1/orgs/{org}/threads", params=params)
    rows = resp.json()["threads"]
    for t in rows:
        print(f"{t['thread_id']:<10}  {t['status']:<10}  {t['subject']}")


@threads_app.command("show")
def threads_show(
    org: str = typer.Option(..., "--org"),
    thread_id: str = typer.Argument(...),
    transcript: bool = typer.Option(False, "--transcript"),
):
    resp = client.get(f"/api/v1/orgs/{org}/threads/{thread_id}")
    data = resp.json()
    print(json.dumps({k: v for k, v in data.items() if k != "messages"}, indent=2))
    print()
    for m in data["messages"]:
        print(f"--- seq {m['seq']} — {m['speaker']} · {m['kind']}")
        if m.get("body_markdown"):
            print(m["body_markdown"])
        elif m.get("decline_reason"):
            print(f"👁 declined: {m['decline_reason']}")
        elif m.get("system_payload"):
            print(f"system: {m['system_payload']}")
        print()
    if transcript and data.get("transcript_path"):
        print(Path(data["transcript_path"]).read_text(encoding="utf-8"))
```

- [ ] **Step 4: Smoke test against the local daemon**

If you have a running dev daemon:

```bash
echo '{"subject":"smoke","recipients":["alice"],"body_markdown":"hi","addressed_to":["@all"]}' > /tmp/c.json
uv run opc threads compose --org <slug> --from-file /tmp/c.json
uv run opc threads list --org <slug>
```

Otherwise just confirm `uv run opc threads --help` shows the subcommands.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py
git commit -m "feat(cli): opc threads compose/list/show"
```

---

## Task 38: CLI — founder subcommands (send, invite, extend, abandon, archive, forward)

**Files:**
- Modify: `src/cli.py`

- [ ] **Step 1: Add the rest of the founder commands**

Append to the `threads_app` group:

```python
@threads_app.command("send")
def threads_send(
    org: str = typer.Option(..., "--org"),
    thread_id: str = typer.Option(..., "--thread-id"),
    from_file: Path = typer.Option(..., "--from-file"),
):
    payload = json.loads(from_file.read_text(encoding="utf-8"))
    resp = client.post(f"/api/v1/orgs/{org}/threads/{thread_id}/send", json=payload)
    print(json.dumps(resp.json(), indent=2))


@threads_app.command("invite")
def threads_invite(
    org: str = typer.Option(..., "--org"),
    thread_id: str = typer.Option(..., "--thread-id"),
    agent: str = typer.Option(..., "--agent"),
):
    resp = client.post(
        f"/api/v1/orgs/{org}/threads/{thread_id}/invite",
        json={"agent_name": agent},
    )
    print(json.dumps(resp.json(), indent=2))


@threads_app.command("extend")
def threads_extend(
    org: str = typer.Option(..., "--org"),
    thread_id: str = typer.Option(..., "--thread-id"),
    new_cap: int = typer.Option(..., "--new-cap"),
):
    resp = client.post(
        f"/api/v1/orgs/{org}/threads/{thread_id}/extend",
        json={"new_cap": new_cap},
    )
    print(json.dumps(resp.json(), indent=2))


@threads_app.command("abandon")
def threads_abandon(
    org: str = typer.Option(..., "--org"),
    thread_id: str = typer.Option(..., "--thread-id"),
    reason: str = typer.Option(..., "--reason"),
):
    resp = client.post(
        f"/api/v1/orgs/{org}/threads/{thread_id}/abandon",
        json={"reason": reason},
    )
    print(json.dumps(resp.json(), indent=2))


@threads_app.command("archive")
def threads_archive(
    org: str = typer.Option(..., "--org"),
    thread_id: str = typer.Option(..., "--thread-id"),
    from_file: Path = typer.Option(..., "--from-file"),
):
    payload = json.loads(from_file.read_text(encoding="utf-8"))
    resp = client.post(
        f"/api/v1/orgs/{org}/threads/{thread_id}/archive", json=payload,
    )
    print(json.dumps(resp.json(), indent=2))


@threads_app.command("forward")
def threads_forward(
    org: str = typer.Option(..., "--org"),
    source: str = typer.Option(..., "--source", help="THR-NNN or TALK-NNN"),
    recipients: str = typer.Option(..., "--recipients", help="comma-separated"),
    note_file: Path = typer.Option(None, "--note-file"),
    subject: str = typer.Option(None, "--subject"),
):
    """Compose a new thread quoting an existing talk or thread."""
    note = note_file.read_text(encoding="utf-8") if note_file else ""
    # The CLI does NOT build the quote — it asks the daemon to resolve the
    # source and prepend a quoted block. Simplification: build it locally and
    # post a regular compose with forwarded_from_id set.
    from src.daemon.thread_forward import (
        build_forward_body_from_talk, build_forward_body_from_thread,
    )
    if source.startswith("TALK-"):
        # Fetch the talk summary.
        talk_resp = client.get(f"/api/v1/orgs/{org}/talks/{source}").json()
        quoted = build_forward_body_from_talk(
            source_id=source,
            summary=talk_resp.get("summary") or "",
            agent_name=talk_resp.get("agent_name") or "?",
        )
        kind = "talk"
        default_subject = f"Fwd: {talk_resp.get('agent_name')} talk"
    elif source.startswith("THR-"):
        thr_resp = client.get(f"/api/v1/orgs/{org}/threads/{source}").json()
        # Convert message dicts back into ThreadMessage-shaped objects.
        from src.models import ThreadMessage, ThreadMessageKind
        from datetime import datetime
        msgs = [
            ThreadMessage(
                thread_id=source, seq=m["seq"], speaker=m["speaker"],
                kind=ThreadMessageKind(m["kind"]),
                body_markdown=m.get("body_markdown"),
                decline_reason=m.get("decline_reason"),
                system_payload=m.get("system_payload"),
                created_at=datetime.fromisoformat(m["created_at"]),
            )
            for m in thr_resp["messages"]
        ]
        quoted = build_forward_body_from_thread(
            source_id=source, messages=msgs, subject=thr_resp["subject"],
        )
        kind = "thread"
        default_subject = f"Fwd: {thr_resp['subject']}"
    else:
        raise typer.BadParameter("--source must start with TALK- or THR-")

    body = quoted + (note or "")
    payload = {
        "subject": subject or default_subject,
        "recipients": [r.strip() for r in recipients.split(",") if r.strip()],
        "body_markdown": body,
        "addressed_to": ["@all"],
        "forwarded_from_id": source,
        "forwarded_from_kind": kind,
    }
    resp = client.post(f"/api/v1/orgs/{org}/threads", json=payload)
    print(json.dumps(resp.json(), indent=2))
```

- [ ] **Step 2: Smoke-test help output**

Run: `uv run opc threads --help`
Expected: lists all 9 subcommands.

- [ ] **Step 3: Commit**

```bash
git add src/cli.py
git commit -m "feat(cli): opc threads send/invite/extend/abandon/archive/forward"
```

---

## Task 39: CLI — agent callbacks (reply, decline, dispatch, close-out)

**Files:**
- Modify: `src/cli.py`

- [ ] **Step 1: Add callbacks**

Append:

```python
@threads_app.command("reply")
def threads_reply(
    org: str = typer.Option(..., "--org"),
    thread_id: str = typer.Option(..., "--thread-id"),
    from_file: Path = typer.Option(..., "--from-file"),
):
    payload = json.loads(from_file.read_text(encoding="utf-8"))
    resp = client.post(f"/api/v1/orgs/{org}/threads/{thread_id}/reply", json=payload)
    print(json.dumps(resp.json(), indent=2))


@threads_app.command("decline")
def threads_decline(
    org: str = typer.Option(..., "--org"),
    thread_id: str = typer.Option(..., "--thread-id"),
    from_file: Path = typer.Option(..., "--from-file"),
):
    payload = json.loads(from_file.read_text(encoding="utf-8"))
    resp = client.post(f"/api/v1/orgs/{org}/threads/{thread_id}/decline", json=payload)
    print(json.dumps(resp.json(), indent=2))


@threads_app.command("dispatch")
def threads_dispatch(
    org: str = typer.Option(..., "--org"),
    thread_id: str = typer.Option(..., "--thread-id"),
    from_file: Path = typer.Option(..., "--from-file"),
):
    payload = json.loads(from_file.read_text(encoding="utf-8"))
    resp = client.post(f"/api/v1/orgs/{org}/threads/{thread_id}/dispatch", json=payload)
    print(json.dumps(resp.json(), indent=2))


@threads_app.command("close-out")
def threads_close_out(
    org: str = typer.Option(..., "--org"),
    thread_id: str = typer.Option(..., "--thread-id"),
    from_file: Path = typer.Option(..., "--from-file"),
):
    payload = json.loads(from_file.read_text(encoding="utf-8"))
    resp = client.post(f"/api/v1/orgs/{org}/threads/{thread_id}/close-out", json=payload)
    print(json.dumps(resp.json(), indent=2))
```

- [ ] **Step 2: Verify all 13 subcommands present**

Run: `uv run opc threads --help | grep -E '^\s+(compose|list|show|send|invite|extend|abandon|archive|forward|reply|decline|dispatch|close-out)\s' | wc -l`
Expected: `13`.

- [ ] **Step 3: Commit**

```bash
git add src/cli.py
git commit -m "feat(cli): opc threads reply/decline/dispatch/close-out callbacks"
```

---

## Task 40: Example org config — threads block

**Files:**
- Modify: `examples/orgs/hk-macau-tourism/org/config.yaml`

- [ ] **Step 1: Add the block**

Open the file. Append (or merge with existing top-level keys):

```yaml
threads:
  enabled: true
  default_turn_cap: 500
  close_out_wait_seconds: 300
  # invocation_timeout_seconds: null  # falls through to session_timeout_seconds
```

- [ ] **Step 2: Commit**

```bash
git add examples/orgs/hk-macau-tourism/org/config.yaml
git commit -m "docs(example-org): threads config example"
```

---

## Task 41: Integration test — compose → reply → archive → transcript

**Files:**
- Create: `tests/integration/test_threads_e2e.py`

- [ ] **Step 1: Write the test**

Create `tests/integration/test_threads_e2e.py`:

```python
"""End-to-end thread flow using the existing fake-CLI fixture from talks tests."""
from __future__ import annotations

import json
import time

import pytest

pytestmark = pytest.mark.integration


def test_compose_reply_archive_writes_transcript(integration_runtime):
    """integration_runtime is the existing fixture from talks integration tests
    — spawns a real daemon, fake claude binary, sample org. Reuse it."""
    runtime = integration_runtime  # provides .org_slug, .client, .root, .fake_claude
    slug = runtime.org_slug

    # Configure fake claude to reply to thread invocations.
    runtime.fake_claude.script = """
    # Read the invocation_token from the prompt env or stdin.
    # The fake claude binary already knows how to issue `opc threads reply`.
    """
    # Compose.
    resp = runtime.client.post(
        f"/api/v1/orgs/{slug}/threads",
        json={"subject": "Refund policy", "recipients": ["alice"],
              "body_markdown": "should we cap?", "addressed_to": ["@all"]},
    )
    assert resp.status_code == 200
    tid = resp.json()["thread_id"]

    # Poll for alice's reply (fake claude should have called /reply).
    deadline = time.time() + 30
    while time.time() < deadline:
        msgs = runtime.client.get(f"/api/v1/orgs/{slug}/threads/{tid}").json()["messages"]
        if any(m["speaker"] == "alice" and m["kind"] == "message" for m in msgs):
            break
        time.sleep(0.5)
    else:
        pytest.fail("alice never replied within 30s")

    # Archive.
    arch = runtime.client.post(
        f"/api/v1/orgs/{slug}/threads/{tid}/archive",
        json={"summary": "wrap", "request_close_outs": False},
    )
    assert arch.status_code == 202

    # Wait for finalize.
    deadline = time.time() + 30
    while time.time() < deadline:
        t = runtime.client.get(f"/api/v1/orgs/{slug}/threads/{tid}").json()
        if t["status"] == "archived":
            break
        time.sleep(0.5)
    else:
        pytest.fail("thread never archived")

    transcript_path = runtime.root / "orgs" / slug / "threads" / f"{tid}.md"
    assert transcript_path.exists()
    content = transcript_path.read_text(encoding="utf-8")
    assert "Refund policy" in content
    assert "should we cap?" in content
```

If the existing integration fixture (`integration_runtime` or equivalent) doesn't include a way to script fake claude's behavior for thread invocations, extend `tests/conftest.py` to teach it — same shape used to teach it about `talks`. Look at the existing talks integration test for the precedent.

- [ ] **Step 2: Run**

Run: `uv run pytest tests/integration/test_threads_e2e.py -v -m integration`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_threads_e2e.py tests/conftest.py
git commit -m "test(integration): compose → reply → archive → transcript"
```

---

## Task 42: Integration test — dispatch from thread + token replay rejection

**Files:**
- Modify: `tests/integration/test_threads_e2e.py`

- [ ] **Step 1: Add tests**

```python
def test_agent_dispatch_from_thread_creates_task_and_keeps_thread_open(integration_runtime):
    runtime = integration_runtime
    slug = runtime.org_slug
    # Script fake claude to dispatch then reply.
    runtime.fake_claude.script_dispatch_then_reply()
    resp = runtime.client.post(
        f"/api/v1/orgs/{slug}/threads",
        json={"subject": "t", "recipients": ["alice"],
              "body_markdown": "should we cap?", "addressed_to": ["@all"]},
    )
    tid = resp.json()["thread_id"]

    # Wait for the system message + reply.
    deadline = time.time() + 30
    while time.time() < deadline:
        msgs = runtime.client.get(f"/api/v1/orgs/{slug}/threads/{tid}").json()["messages"]
        sys_msgs = [m for m in msgs if m["kind"] == "system" and (m.get("system_payload") or {}).get("kind_tag") == "task_dispatched"]
        replies = [m for m in msgs if m["speaker"] == "alice" and m["kind"] == "message"]
        if sys_msgs and replies:
            break
        time.sleep(0.5)
    else:
        pytest.fail("dispatch+reply did not occur")
    assert runtime.client.get(f"/api/v1/orgs/{slug}/threads/{tid}").json()["status"] == "open"


def test_token_replay_returns_409(integration_runtime):
    runtime = integration_runtime
    slug = runtime.org_slug
    resp = runtime.client.post(
        f"/api/v1/orgs/{slug}/threads",
        json={"subject": "t", "recipients": ["alice"],
              "body_markdown": "x", "addressed_to": ["@all"]},
    )
    tid = resp.json()["thread_id"]
    # Read the token directly from the DB through a debug endpoint OR by
    # arranging fake claude to print it. For this test, query the DB via
    # a small admin route that exposes pending invocations in test mode.
    invs = runtime.db.list_thread_invocations(tid)
    token = invs[0].invocation_token

    p = {"thread_id": tid, "invocation_token": token,
         "speaker": "alice", "body_markdown": "hi", "in_response_to_seq": 1}
    first = runtime.client.post(f"/api/v1/orgs/{slug}/threads/{tid}/reply", json=p)
    assert first.status_code == 200
    second = runtime.client.post(f"/api/v1/orgs/{slug}/threads/{tid}/reply", json=p)
    assert second.status_code == 409
```

- [ ] **Step 2: Run**

Run: `uv run pytest tests/integration/test_threads_e2e.py -v -m integration`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_threads_e2e.py
git commit -m "test(integration): dispatch-from-thread + token replay"
```

---

## Task 43: README + CLAUDE.md touchup

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add a Threads section to README.md**

In `README.md`, after the Talks section, add:

````markdown
## Threads

Email-style multi-agent workchannels. Use threads when you need to involve
multiple agents in a single asynchronous conversation, or when you want to
loop new agents into an existing discussion. Each thread has a subject, a
participants list, and a chronological message log.

Founder commands:

```bash
opc threads compose --org <slug> --from-file /tmp/compose.json
opc threads list --org <slug>
opc threads show --org <slug> THR-001
opc threads send --org <slug> --thread-id THR-001 --from-file /tmp/send.json
opc threads invite --org <slug> --thread-id THR-001 --agent qa
opc threads forward --org <slug> --source TALK-008 --recipients alice,bob
opc threads archive --org <slug> --thread-id THR-001 --from-file /tmp/arch.json
opc threads abandon --org <slug> --thread-id THR-001 --reason "..."
opc threads extend --org <slug> --thread-id THR-001 --new-cap 1000
```

Configure per-org:

```yaml
# <runtime>/orgs/<slug>/org/config.yaml
threads:
  enabled: true
  default_turn_cap: 500
  close_out_wait_seconds: 300
```

A Textual TUI (`opc threads` with no subcommand) is planned for a follow-up
release.
````

- [ ] **Step 2: Add Threads section to CLAUDE.md**

In `CLAUDE.md`, in the "Implementation Order" list, add a new completed entry between items 11 and 12:

```markdown
12. ~~**Threads (foundation)**~~ done — email-style multi-agent workchannels with daemon-minted invocation tokens. CLI surface only; Textual TUI is a follow-up. Spec: `docs/superpowers/specs/2026-05-13-threads-design.md`. Plan: `docs/superpowers/plans/2026-05-13-threads-foundation.md`.
```

(Adjust numbering for items below.)

In the same file, in the directory layout section, add `threads/` alongside `talks/`:

```
        +-- talks/
        +-- threads/
            +-- THR-NNN.md
```

In the Tech Stack section, mention that threads share the same SQLite schema and queue patterns as tasks/talks.

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: threads foundation in README + CLAUDE.md"
```

---

## Task 44: Final test sweep + cleanup

**Files:**
- (verification only)

- [ ] **Step 1: Run all unit tests**

Run: `uv run pytest tests/ -v 2>&1 | tail -40`
Expected: all PASS, no skips of new tests.

- [ ] **Step 2: Run integration tests**

Run: `uv run pytest tests/ -v -m integration 2>&1 | tail -40`
Expected: all PASS.

- [ ] **Step 3: Confirm no stray TODOs in new code**

Run: `grep -rnE "TBD|TODO|XXX|FIXME" src/daemon/routes/threads.py src/daemon/thread_*.py src/infrastructure/thread_store.py protocol/skills/thread/SKILL.md || echo "(clean)"`
Expected: `(clean)`.

- [ ] **Step 4: Confirm allow-rule baseline is sufficient**

Read `src/orchestrator/workspace_adapters.py` for the baseline `Bash(opc *)` and confirm no new prefix is needed. Threads stay under `opc threads ...` which the baseline already covers.

- [ ] **Step 5: Open the PR**

```bash
git push -u origin <branch>
gh pr create --title "feat: threads foundation (CLI, agent invocation, transcript)" --body "$(cat <<'EOF'
## Summary
- New email-style multi-agent threads alongside talks
- Daemon-minted invocation tokens prevent out-of-band agent callbacks
- `open → archiving → archived` two-phase archive lets close-outs land before finalization
- Agent dispatch from threads mirrors talk-dispatch (worker self / manager team-scope; cross-team forbidden)
- CLI surface (`opc threads compose|list|show|send|invite|forward|archive|abandon|extend` + agent callbacks)
- Textual TUI deferred to follow-up plan (`docs/superpowers/plans/2026-05-13-threads-tui.md`)

## Test plan
- [x] `uv run pytest tests/ -v` (unit)
- [x] `uv run pytest tests/ -v -m integration` (integration with fake CLIs)
- [ ] Manual smoke: compose → fake-claude reply → archive in dev runtime

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Done**

This plan is complete. The TUI plan is a separate document and may be started in a fresh worktree.

---

## Follow-up: TUI plan

A separate plan covering the Textual TUI (`opc threads` no-subcommand launcher) will live at `docs/superpowers/plans/2026-05-13-threads-tui.md`. It depends on this foundation plan being merged. It will cover:

- Textual app skeleton (`src/tui/threads_app.py`)
- Inbox / Thread / Compose panes
- Keybindings + modals (forward, invite, archive)
- Live SSE updates (httpx streaming)
- Snapshot-based testing

Write that plan when this one is merged and you're ready to start TUI work.

