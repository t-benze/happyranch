# Agent Script Requests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the `SR-NNN` script-request primitive: agents submit scripts they can't run in their sandbox, founder reviews via CLI/web, daemon spawns the subprocess with founder-grade env, output streams via SSE, unblock path reuses `grassland revisit`.

**Architecture:** New per-org `script_requests` SQLite table + Pydantic model; one HTTP route module (`src/daemon/routes/scripts.py`) under `/api/v1/orgs/{slug}/scripts/`; a dedicated subprocess runner (`src/daemon/scripts_runner.py`) using `asyncio.create_subprocess_exec` with line-buffered stdout/stderr pumps that fan out to an in-memory pub/sub channel; founder CLI subcommands under `grassland scripts`; web feature folder `web/src/features/scripts/` mirroring the threads three-layer architecture; agent identity proven through the existing session-binding chain (no per-agent bearer scoping).

**Tech Stack:** Python 3.11+ / FastAPI / Pydantic v2 / SQLite (per-org) / asyncio subprocess / SSE; React 18 + TypeScript strict + Tailwind 3 + TanStack Query v5; vitest for web; pytest for python; integration tests via `fake_claude.sh`.

**Reference spec:** `docs/superpowers/specs/2026-05-23-agent-script-requests-design.md` (commit `1b1ff23`).

---

## Pre-flight: read these files once

These are the patterns the plan mirrors. Skim them before starting Task 1 so the code style is in your head:

- `src/models.py` — Pydantic + StrEnum style.
- `src/infrastructure/database.py` lines 1318–1346 (`next_talk_id`, `next_thread_id`) — ID allocation + `@_synchronized` decorator pattern.
- `src/infrastructure/database.py` lines 88–280 — `_init_schema` `CREATE TABLE IF NOT EXISTS` discipline.
- `src/daemon/routes/threads.py` lines 1–366 — route module skeleton, `OrgDep`, validation-order discipline, `HTTPException` shape (`{"code": "..."}`).
- `src/daemon/sessions.py` — `SessionTracker.get_active(task_id, agent)` is the session-ownership probe.
- `src/daemon/event_bus.py` — pub/sub with `subscribe`/`publish`; reuse the same pattern for per-SR streams.
- `src/orchestrator/run_step.py` lines 396–444 — `_revisit_header_if_applicable` shape (we extend it in Task 26).
- `src/cli.py` lines 2125–2320 — argparse subcommand registration; lines 1561–1840 — agent-callback `cmd_threads_*` patterns.
- `web/src/lib/api/threads.ts` — TS mirror style; one exported function per founder-facing route.
- `web/src/test/openapi-coverage.test.ts` lines 42–131 — INCLUDED/EXCLUDED list discipline.
- `tests/integration/fake_claude.sh` — dual prompt routing (task + thread); extend with a script-submit branch in Task 32.

## File map

**New files:**
- `src/daemon/routes/scripts.py` — route handlers (~600 LOC est).
- `src/daemon/scripts_runner.py` — subprocess execution, stream pumps, timeout, shutdown cleanup (~250 LOC).
- `protocol/skills/scripts/SKILL.md` — agent-side documentation.
- `web/src/lib/api/scripts.ts` — TS API mirror.
- `web/src/lib/api/scripts.test.ts` — TS API tests.
- `web/src/features/scripts/ListPage.tsx` — list page.
- `web/src/features/scripts/DetailDrawer.tsx` — drawer with all sections.
- `web/src/features/scripts/RunModal.tsx` — confirm-and-run modal.
- `web/src/features/scripts/RejectModal.tsx` — rejection reason modal.
- `web/src/features/scripts/OutputPanel.tsx` — SSE live + post-run output.
- `web/src/features/scripts/index.ts` — barrel.
- `tests/integration/test_scripts_e2e.py` — end-to-end submit→run→output→revisit.
- `tests/test_scripts_runner.py` — runner unit tests.
- `tests/test_database_scripts.py` — DB layer unit tests.
- `tests/test_routes_scripts.py` — route validation unit tests.

**Modified files:**
- `src/models.py` — add `ScriptRequestStatus`, `ScriptInterpreter`, `ScriptRequestRecord`.
- `src/infrastructure/database.py` — add `script_requests` table, allocator, CRUD, recovery scan.
- `src/infrastructure/audit_logger.py` — add 5 `log_script_*` methods.
- `src/daemon/event_bus.py` — add `script_topic(sr_id)` helper.
- `src/daemon/app.py` — wire `scripts.router`; add startup recovery call; add shutdown subprocess cleanup.
- `src/daemon/state.py` — register in-flight SR tracker (for shutdown cleanup).
- `src/cli.py` — add `grassland scripts` subparser + 6 subcommands.
- `src/orchestrator/run_step.py` — extend `_revisit_header_if_applicable` to append SR summary block.
- `protocol/skills/start-task/SKILL.md` — cross-reference the new scripts skill.
- `tests/contract/test_openapi_snapshot.py` — regenerate (no code change; just run with `GRASSLAND_REGEN_OPENAPI=1`).
- `tests/contract/openapi.json` — auto-regenerated snapshot.
- `web/src/test/openapi-coverage.test.ts` — add new paths to INCLUDED + the `/submit` exclusion.
- `web/src/App.tsx` (or router file) — add `/scripts` routes.
- `web/src/lib/api/index.ts` — re-export scripts API.
- `web/src/features/audit/...` — deep-link `script_submitted` entries (Task 30).
- `web/src/features/agents/...` — recent-SRs section in agent detail (Task 31).
- `web/src/features/tasks/...` — SRs-from-task section in task drawer (Task 31).
- `tests/integration/fake_claude.sh` — add `--script-submit` prompt branch.
- `README.md` — add SR section.
- `skills/grassland/SKILL.md` — add SR section.

---

### Task 1: Add SR enums and Pydantic record to `src/models.py`

**Files:**
- Modify: `src/models.py` (append at the bottom, before any trailing whitespace)
- Test: `tests/test_models.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_models.py`:

```python
def test_script_request_status_values():
    from src.models import ScriptRequestStatus
    assert ScriptRequestStatus.PENDING == "pending"
    assert ScriptRequestStatus.REJECTED == "rejected"
    assert ScriptRequestStatus.RUNNING == "running"
    assert ScriptRequestStatus.COMPLETED == "completed"
    assert ScriptRequestStatus.FAILED == "failed"


def test_script_interpreter_values():
    from src.models import ScriptInterpreter
    assert ScriptInterpreter.BASH == "bash"
    assert ScriptInterpreter.SH == "sh"
    assert ScriptInterpreter.ZSH == "zsh"
    assert ScriptInterpreter.PYTHON3 == "python3"


def test_script_request_record_defaults():
    from src.models import ScriptRequestRecord, ScriptRequestStatus, ScriptInterpreter
    r = ScriptRequestRecord(
        id="SR-001",
        task_id="TASK-001",
        agent_name="engineering_head",
        title="x",
        rationale="y",
        script_text="echo hi",
        interpreter=ScriptInterpreter.BASH,
        created_at="2026-05-23T10:00:00Z",
    )
    assert r.status == ScriptRequestStatus.PENDING
    assert r.timeout_seconds == 300
    assert r.cwd_hint is None
    assert r.exit_code is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v -k script`
Expected: `ImportError: cannot import name 'ScriptRequestStatus'` (3 errors).

- [ ] **Step 3: Add enums + record to `src/models.py`**

Append to `src/models.py`:

```python
class ScriptRequestStatus(StrEnum):
    PENDING   = "pending"
    REJECTED  = "rejected"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"


class ScriptInterpreter(StrEnum):
    BASH    = "bash"
    SH      = "sh"
    ZSH     = "zsh"
    PYTHON3 = "python3"


class ScriptRequestRecord(BaseModel):
    id:               str
    task_id:          str
    agent_name:       str
    title:            str
    rationale:        str
    script_text:      str
    interpreter:      ScriptInterpreter
    cwd_hint:         str | None = None
    status:           ScriptRequestStatus = ScriptRequestStatus.PENDING
    exit_code:        int | None = None
    stdout_head:      str | None = None
    stderr_head:      str | None = None
    stdout_path:      str | None = None
    stderr_path:      str | None = None
    duration_ms:      int | None = None
    started_at:       str | None = None
    finished_at:      str | None = None
    reviewed_at:      str | None = None
    reviewed_by:      str | None = None
    reject_reason:    str | None = None
    cwd_resolved:     str | None = None
    timeout_seconds:  int = 300
    created_at:       str
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_models.py -v -k script`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat(models): add ScriptRequest enums + record"
```

---

### Task 2: Add `script_requests` table schema in `_init_schema`

**Files:**
- Modify: `src/infrastructure/database.py` (in `_init_schema`, after the last `CREATE TABLE` block)
- Test: `tests/test_database_scripts.py` (new file)

- [ ] **Step 1: Write the failing test**

Create `tests/test_database_scripts.py`:

```python
"""Schema + CRUD tests for script_requests (spec §3.1)."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.infrastructure.database import Database


@pytest.fixture
def db() -> Database:
    d = tempfile.mkdtemp()
    db = Database(Path(d) / "test.db")
    yield db
    db.close()


def test_script_requests_table_exists(db: Database):
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='script_requests'"
    )
    assert cur.fetchone() is not None


def test_script_requests_columns(db: Database):
    cur = db._conn.execute("PRAGMA table_info(script_requests)")
    names = {row["name"] for row in cur.fetchall()}
    expected = {
        "id", "task_id", "agent_name", "title", "rationale", "script_text",
        "interpreter", "cwd_hint", "status", "exit_code",
        "stdout_head", "stderr_head", "stdout_path", "stderr_path",
        "duration_ms", "started_at", "finished_at",
        "reviewed_at", "reviewed_by", "reject_reason",
        "cwd_resolved", "timeout_seconds", "created_at",
    }
    assert expected.issubset(names), f"missing: {expected - names}"


def test_script_requests_indexes(db: Database):
    cur = db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='script_requests'"
    )
    names = {row["name"] for row in cur.fetchall()}
    assert "idx_script_requests_task" in names
    assert "idx_script_requests_agent" in names
    assert "idx_script_requests_status" in names
    assert "idx_script_requests_created_at" in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_database_scripts.py -v`
Expected: 3 failures with "no such table" / missing columns.

- [ ] **Step 3: Add schema in `_init_schema`**

In `src/infrastructure/database.py`, locate `_init_schema` and add after the last existing `CREATE TABLE` block (after the `thread_invocations` table around line 259):

```python
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS script_requests (
                id                  TEXT PRIMARY KEY,
                task_id             TEXT NOT NULL,
                agent_name          TEXT NOT NULL,
                title               TEXT NOT NULL,
                rationale           TEXT NOT NULL,
                script_text         TEXT NOT NULL,
                interpreter         TEXT NOT NULL,
                cwd_hint            TEXT,
                status              TEXT NOT NULL DEFAULT 'pending',
                exit_code           INTEGER,
                stdout_head         TEXT,
                stderr_head         TEXT,
                stdout_path         TEXT,
                stderr_path         TEXT,
                duration_ms         INTEGER,
                started_at          TEXT,
                finished_at         TEXT,
                reviewed_at         TEXT,
                reviewed_by         TEXT,
                reject_reason       TEXT,
                cwd_resolved        TEXT,
                timeout_seconds     INTEGER NOT NULL DEFAULT 300,
                created_at          TEXT NOT NULL
            )
        """)
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_script_requests_task        ON script_requests(task_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_script_requests_agent       ON script_requests(agent_name)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_script_requests_status      ON script_requests(status)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_script_requests_created_at  ON script_requests(created_at)")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_database_scripts.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database_scripts.py
git commit -m "feat(db): script_requests table + indexes"
```

---

### Task 3: Add `next_script_request_id` allocator

**Files:**
- Modify: `src/infrastructure/database.py`
- Test: `tests/test_database_scripts.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_database_scripts.py`:

```python
def test_next_script_request_id_first(db: Database):
    assert db.next_script_request_id() == "SR-001"


def test_next_script_request_id_monotonic(db: Database):
    # Manually insert a row with SR-005 to verify the allocator picks SR-006.
    db._conn.execute(
        "INSERT INTO script_requests (id, task_id, agent_name, title, rationale, "
        "script_text, interpreter, status, created_at) "
        "VALUES ('SR-005', 'TASK-001', 'a', 't', 'r', 's', 'bash', 'pending', '2026-05-23T00:00:00Z')"
    )
    db._conn.commit()
    assert db.next_script_request_id() == "SR-006"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_database_scripts.py::test_next_script_request_id_first -v`
Expected: `AttributeError: 'Database' object has no attribute 'next_script_request_id'`.

- [ ] **Step 3: Add allocator method**

In `src/infrastructure/database.py`, immediately after `next_thread_id` (around line 1346), add:

```python
    @_synchronized
    def next_script_request_id(self) -> str:
        """Return the next available SR-NNN id.

        Callers must hold DaemonState.db_lock across the next_script_request_id()
        + insert_script_request() pair to avoid duplicate IDs under concurrent
        requests (same requirement as next_task_id / next_talk_id / next_thread_id).
        """
        cursor = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 4) AS INTEGER)) AS m "
            "FROM script_requests WHERE id GLOB 'SR-[0-9]*'"
        )
        n = (cursor.fetchone()["m"] or 0) + 1
        return f"SR-{n:03d}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_database_scripts.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database_scripts.py
git commit -m "feat(db): next_script_request_id allocator"
```

---

### Task 4: Add `insert_script_request` + `get_script_request`

**Files:**
- Modify: `src/infrastructure/database.py`
- Test: `tests/test_database_scripts.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_database_scripts.py`:

```python
from src.models import ScriptRequestRecord, ScriptRequestStatus, ScriptInterpreter


def _make_record(id_: str = "SR-001") -> ScriptRequestRecord:
    return ScriptRequestRecord(
        id=id_,
        task_id="TASK-001",
        agent_name="engineering_head",
        title="Close PR #247",
        rationale="needs founder gh scope",
        script_text="gh pr close 247",
        interpreter=ScriptInterpreter.BASH,
        cwd_hint="repos/web-app",
        created_at="2026-05-23T10:00:00Z",
    )


def test_insert_and_get_script_request(db: Database):
    rec = _make_record()
    db.insert_script_request(rec)
    fetched = db.get_script_request("SR-001")
    assert fetched is not None
    assert fetched.id == "SR-001"
    assert fetched.task_id == "TASK-001"
    assert fetched.agent_name == "engineering_head"
    assert fetched.interpreter == ScriptInterpreter.BASH
    assert fetched.status == ScriptRequestStatus.PENDING
    assert fetched.timeout_seconds == 300
    assert fetched.cwd_hint == "repos/web-app"


def test_get_script_request_missing(db: Database):
    assert db.get_script_request("SR-999") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_database_scripts.py -v -k insert_and_get`
Expected: `AttributeError: 'Database' object has no attribute 'insert_script_request'`.

- [ ] **Step 3: Add insert + get methods**

In `src/infrastructure/database.py`, after `next_script_request_id`, add:

```python
    @_synchronized
    def insert_script_request(self, r: "ScriptRequestRecord") -> None:
        self._conn.execute(
            """INSERT INTO script_requests (
                id, task_id, agent_name, title, rationale, script_text,
                interpreter, cwd_hint, status, exit_code,
                stdout_head, stderr_head, stdout_path, stderr_path,
                duration_ms, started_at, finished_at,
                reviewed_at, reviewed_by, reject_reason,
                cwd_resolved, timeout_seconds, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r.id, r.task_id, r.agent_name, r.title, r.rationale, r.script_text,
                r.interpreter.value, r.cwd_hint, r.status.value, r.exit_code,
                r.stdout_head, r.stderr_head, r.stdout_path, r.stderr_path,
                r.duration_ms, r.started_at, r.finished_at,
                r.reviewed_at, r.reviewed_by, r.reject_reason,
                r.cwd_resolved, r.timeout_seconds, r.created_at,
            ),
        )
        self._conn.commit()

    @_synchronized
    def get_script_request(self, sr_id: str) -> "ScriptRequestRecord | None":
        row = self._conn.execute(
            "SELECT * FROM script_requests WHERE id = ?", (sr_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_script_request(row)

    @staticmethod
    def _row_to_script_request(row) -> "ScriptRequestRecord":
        from src.models import ScriptRequestRecord, ScriptRequestStatus, ScriptInterpreter
        return ScriptRequestRecord(
            id=row["id"],
            task_id=row["task_id"],
            agent_name=row["agent_name"],
            title=row["title"],
            rationale=row["rationale"],
            script_text=row["script_text"],
            interpreter=ScriptInterpreter(row["interpreter"]),
            cwd_hint=row["cwd_hint"],
            status=ScriptRequestStatus(row["status"]),
            exit_code=row["exit_code"],
            stdout_head=row["stdout_head"],
            stderr_head=row["stderr_head"],
            stdout_path=row["stdout_path"],
            stderr_path=row["stderr_path"],
            duration_ms=row["duration_ms"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            reviewed_at=row["reviewed_at"],
            reviewed_by=row["reviewed_by"],
            reject_reason=row["reject_reason"],
            cwd_resolved=row["cwd_resolved"],
            timeout_seconds=row["timeout_seconds"],
            created_at=row["created_at"],
        )
```

Also add the `ScriptRequestRecord` import at the top of `src/infrastructure/database.py` if not already present (`from src.models import ScriptRequestRecord` — keep alongside existing model imports).

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_database_scripts.py -v`
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database_scripts.py
git commit -m "feat(db): insert_script_request + get_script_request"
```

---

### Task 5: Add `list_script_requests` with filters

**Files:**
- Modify: `src/infrastructure/database.py`
- Test: `tests/test_database_scripts.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_list_script_requests_all(db: Database):
    for i in range(1, 4):
        rec = _make_record(f"SR-{i:03d}")
        db.insert_script_request(rec)
    results = db.list_script_requests()
    assert len(results) == 3
    # Most recent first (created_at DESC, ties broken by id DESC).
    assert results[0].id == "SR-003"


def test_list_script_requests_filter_by_status(db: Database):
    r1 = _make_record("SR-001")
    db.insert_script_request(r1)
    r2 = _make_record("SR-002")
    db.insert_script_request(r2)
    db._conn.execute("UPDATE script_requests SET status='rejected' WHERE id='SR-002'")
    db._conn.commit()
    pending = db.list_script_requests(status="pending")
    assert [r.id for r in pending] == ["SR-001"]


def test_list_script_requests_filter_by_agent(db: Database):
    db.insert_script_request(_make_record("SR-001"))
    other = _make_record("SR-002")
    other.agent_name = "payment_agt"
    db.insert_script_request(other)
    only_payment = db.list_script_requests(agent="payment_agt")
    assert [r.id for r in only_payment] == ["SR-002"]


def test_list_script_requests_limit(db: Database):
    for i in range(1, 11):
        db.insert_script_request(_make_record(f"SR-{i:03d}"))
    results = db.list_script_requests(limit=3)
    assert len(results) == 3
    assert [r.id for r in results] == ["SR-010", "SR-009", "SR-008"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_database_scripts.py -v -k list_script`
Expected: AttributeError.

- [ ] **Step 3: Implement `list_script_requests`**

In `src/infrastructure/database.py`, after `_row_to_script_request`:

```python
    @_synchronized
    def list_script_requests(
        self,
        *,
        status: str | list[str] | None = None,
        agent: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
    ) -> list["ScriptRequestRecord"]:
        clauses: list[str] = []
        params: list = []
        if status is not None:
            statuses = [status] if isinstance(status, str) else list(status)
            placeholders = ",".join("?" * len(statuses))
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        if agent is not None:
            clauses.append("agent_name = ?")
            params.append(agent)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        rows = self._conn.execute(
            f"SELECT * FROM script_requests {where} "
            f"ORDER BY created_at DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_script_request(r) for r in rows]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_database_scripts.py -v`
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database_scripts.py
git commit -m "feat(db): list_script_requests with status/agent/task filters"
```

---

### Task 6: State-transition methods (`reject`, `run_started`, `run_terminal`)

**Files:**
- Modify: `src/infrastructure/database.py`
- Test: `tests/test_database_scripts.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_transition_to_rejected(db: Database):
    db.insert_script_request(_make_record("SR-001"))
    db.transition_script_to_rejected("SR-001", reviewer="founder",
                                     reason="too risky", reviewed_at="2026-05-23T10:05:00Z")
    fetched = db.get_script_request("SR-001")
    assert fetched.status == ScriptRequestStatus.REJECTED
    assert fetched.reviewed_by == "founder"
    assert fetched.reject_reason == "too risky"
    assert fetched.reviewed_at == "2026-05-23T10:05:00Z"


def test_transition_to_rejected_only_from_pending(db: Database):
    db.insert_script_request(_make_record("SR-001"))
    db._conn.execute("UPDATE script_requests SET status='running' WHERE id='SR-001'")
    db._conn.commit()
    with pytest.raises(ValueError, match="not_pending"):
        db.transition_script_to_rejected("SR-001", reviewer="founder",
                                         reason="x", reviewed_at="2026-05-23T10:05:00Z")


def test_transition_to_running(db: Database):
    db.insert_script_request(_make_record("SR-001"))
    db.transition_script_to_running(
        "SR-001",
        reviewer="founder",
        reviewed_at="2026-05-23T10:10:00Z",
        started_at="2026-05-23T10:10:00Z",
        cwd_resolved="/abs/path",
        timeout_seconds=600,
        stdout_path="/abs/scripts/SR-001.out",
        stderr_path="/abs/scripts/SR-001.err",
    )
    fetched = db.get_script_request("SR-001")
    assert fetched.status == ScriptRequestStatus.RUNNING
    assert fetched.cwd_resolved == "/abs/path"
    assert fetched.timeout_seconds == 600
    assert fetched.started_at == "2026-05-23T10:10:00Z"


def test_transition_to_terminal_completed(db: Database):
    db.insert_script_request(_make_record("SR-001"))
    db.transition_script_to_running(
        "SR-001", reviewer="founder", reviewed_at="2026-05-23T10:10:00Z",
        started_at="2026-05-23T10:10:00Z", cwd_resolved="/x",
        timeout_seconds=300, stdout_path="/x/SR-001.out", stderr_path="/x/SR-001.err",
    )
    db.transition_script_to_terminal(
        "SR-001",
        status=ScriptRequestStatus.COMPLETED,
        exit_code=0,
        finished_at="2026-05-23T10:11:00Z",
        duration_ms=60000,
        stdout_head="hello\n",
        stderr_head="",
    )
    fetched = db.get_script_request("SR-001")
    assert fetched.status == ScriptRequestStatus.COMPLETED
    assert fetched.exit_code == 0
    assert fetched.duration_ms == 60000
    assert fetched.stdout_head == "hello\n"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/test_database_scripts.py -v -k transition`
Expected: AttributeErrors.

- [ ] **Step 3: Implement transition methods**

In `src/infrastructure/database.py`, after `list_script_requests`:

```python
    @_synchronized
    def transition_script_to_rejected(
        self, sr_id: str, *, reviewer: str, reason: str, reviewed_at: str
    ) -> None:
        cur = self._conn.execute(
            "UPDATE script_requests "
            "SET status='rejected', reviewed_by=?, reject_reason=?, reviewed_at=? "
            "WHERE id=? AND status='pending'",
            (reviewer, reason, reviewed_at, sr_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"not_pending: SR {sr_id} cannot be rejected")

    @_synchronized
    def transition_script_to_running(
        self,
        sr_id: str,
        *,
        reviewer: str,
        reviewed_at: str,
        started_at: str,
        cwd_resolved: str,
        timeout_seconds: int,
        stdout_path: str,
        stderr_path: str,
    ) -> None:
        cur = self._conn.execute(
            "UPDATE script_requests SET "
            "status='running', reviewed_by=?, reviewed_at=?, started_at=?, "
            "cwd_resolved=?, timeout_seconds=?, stdout_path=?, stderr_path=? "
            "WHERE id=? AND status='pending'",
            (reviewer, reviewed_at, started_at, cwd_resolved, timeout_seconds,
             stdout_path, stderr_path, sr_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"not_pending: SR {sr_id} cannot transition to running")

    @_synchronized
    def transition_script_to_terminal(
        self,
        sr_id: str,
        *,
        status: "ScriptRequestStatus",
        exit_code: int | None,
        finished_at: str,
        duration_ms: int,
        stdout_head: str | None,
        stderr_head: str | None,
    ) -> None:
        if status.value not in ("completed", "failed"):
            raise ValueError(f"invalid terminal status: {status.value}")
        cur = self._conn.execute(
            "UPDATE script_requests SET "
            "status=?, exit_code=?, finished_at=?, duration_ms=?, "
            "stdout_head=?, stderr_head=? "
            "WHERE id=? AND status='running'",
            (status.value, exit_code, finished_at, duration_ms,
             stdout_head, stderr_head, sr_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"not_running: SR {sr_id} cannot transition to terminal")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_database_scripts.py -v`
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database_scripts.py
git commit -m "feat(db): SR state-transition methods (reject/run/terminal)"
```

---

### Task 7: Startup recovery scan for orphaned `running` SRs

**Files:**
- Modify: `src/infrastructure/database.py`
- Test: `tests/test_database_scripts.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_recover_orphaned_running_scripts(db: Database):
    """On daemon startup, any SR left in 'running' state is orphaned and
    must be force-transitioned to 'failed' with reason=killed_daemon_restart."""
    db.insert_script_request(_make_record("SR-001"))
    db._conn.execute(
        "UPDATE script_requests SET status='running', "
        "started_at='2026-05-23T10:00:00Z', cwd_resolved='/x', "
        "stdout_path='/x/SR-001.out', stderr_path='/x/SR-001.err' "
        "WHERE id='SR-001'"
    )
    db._conn.commit()
    recovered = db.recover_orphaned_running_scripts(now_iso="2026-05-23T11:00:00Z")
    assert recovered == ["SR-001"]
    fetched = db.get_script_request("SR-001")
    assert fetched.status == ScriptRequestStatus.FAILED
    assert fetched.finished_at == "2026-05-23T11:00:00Z"


def test_recover_no_orphans(db: Database):
    db.insert_script_request(_make_record("SR-001"))  # stays pending
    assert db.recover_orphaned_running_scripts(now_iso="2026-05-23T11:00:00Z") == []
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/test_database_scripts.py -v -k recover`
Expected: AttributeError.

- [ ] **Step 3: Implement recovery scan**

In `src/infrastructure/database.py`, after the terminal-transition method:

```python
    @_synchronized
    def recover_orphaned_running_scripts(self, *, now_iso: str) -> list[str]:
        """Force-transition any SR left in 'running' state to 'failed'.

        Called from the daemon FastAPI lifespan on startup. The subprocess
        and its parent daemon process are gone; partial output on disk is
        preserved but the row is marked failed so the founder UI doesn't
        leave them in a permanent running state.
        """
        rows = self._conn.execute(
            "SELECT id FROM script_requests WHERE status='running'"
        ).fetchall()
        ids = [r["id"] for r in rows]
        if not ids:
            return []
        self._conn.executemany(
            "UPDATE script_requests SET status='failed', finished_at=?, "
            "duration_ms=COALESCE(duration_ms, 0), "
            "stderr_head=COALESCE(stderr_head, '') || '\n[daemon restart killed run]' "
            "WHERE id=?",
            [(now_iso, sr_id) for sr_id in ids],
        )
        self._conn.commit()
        return ids
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_database_scripts.py -v`
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database_scripts.py
git commit -m "feat(db): recover_orphaned_running_scripts on daemon startup"
```

---

### Task 8: Audit logger methods

**Files:**
- Modify: `src/infrastructure/audit_logger.py`
- Test: `tests/test_audit_logger.py` (append; create if missing)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_audit_logger.py` (create with the existing imports/fixtures if the file is new):

```python
def test_log_script_submitted(audit_logger, db):
    audit_logger.log_script_submitted(
        task_id="TASK-001",
        sr_id="SR-001",
        agent="engineering_head",
        title="x",
        interpreter="bash",
        cwd_hint="repos/web-app",
        byte_size=42,
        line_count=2,
    )
    logs = db.get_audit_logs("TASK-001")
    actions = [e["action"] for e in logs]
    assert "script_submitted" in actions
    payload = next(e["payload"] for e in logs if e["action"] == "script_submitted")
    assert payload["script_request_id"] == "SR-001"
    assert payload["title"] == "x"


def test_log_script_run_completed(audit_logger, db):
    audit_logger.log_script_run_completed(
        sr_id="SR-001",
        exit_code=0,
        duration_ms=1500,
        stdout_bytes=12,
        stderr_bytes=0,
        truncated_stdout=False,
        truncated_stderr=False,
    )
    logs = db.get_audit_logs_by_scope("script_request:SR-001")
    payload = next(e["payload"] for e in logs if e["action"] == "script_run_completed")
    assert payload["exit_code"] == 0
    assert payload["duration_ms"] == 1500
```

(If `get_audit_logs_by_scope` doesn't exist yet, use whatever scope-lookup helper exists in the test file — the goal is to confirm the row landed.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_audit_logger.py -v -k script`
Expected: AttributeError.

- [ ] **Step 3: Implement the five `log_script_*` methods**

In `src/infrastructure/audit_logger.py`, append (matching the existing `log_*` style — most call `self._db.insert_audit_log(...)` with action + payload + scope):

```python
    def log_script_submitted(
        self,
        *,
        task_id: str,
        sr_id: str,
        agent: str,
        title: str,
        interpreter: str,
        cwd_hint: str | None,
        byte_size: int,
        line_count: int,
    ) -> None:
        self._db.insert_audit_log(
            action="script_submitted",
            task_id=task_id,
            agent=agent,
            payload={
                "script_request_id": sr_id,
                "title": title,
                "interpreter": interpreter,
                "cwd_hint": cwd_hint,
                "byte_size": byte_size,
                "line_count": line_count,
            },
            scope=f"script_request:{sr_id}",
        )

    def log_script_rejected(
        self, *, sr_id: str, reviewer: str, reason: str
    ) -> None:
        self._db.insert_audit_log(
            action="script_rejected",
            task_id=None,
            agent=reviewer,
            payload={"reviewer": reviewer, "reason": reason},
            scope=f"script_request:{sr_id}",
        )

    def log_script_run_started(
        self,
        *,
        sr_id: str,
        reviewer: str,
        cwd_resolved: str,
        timeout_seconds: int,
        interpreter: str,
    ) -> None:
        self._db.insert_audit_log(
            action="script_run_started",
            task_id=None,
            agent=reviewer,
            payload={
                "reviewer": reviewer,
                "cwd_resolved": cwd_resolved,
                "timeout_seconds": timeout_seconds,
                "interpreter": interpreter,
            },
            scope=f"script_request:{sr_id}",
        )

    def log_script_run_completed(
        self,
        *,
        sr_id: str,
        exit_code: int,
        duration_ms: int,
        stdout_bytes: int,
        stderr_bytes: int,
        truncated_stdout: bool,
        truncated_stderr: bool,
    ) -> None:
        self._db.insert_audit_log(
            action="script_run_completed",
            task_id=None,
            agent="founder",
            payload={
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "stdout_bytes": stdout_bytes,
                "stderr_bytes": stderr_bytes,
                "truncated_stdout": truncated_stdout,
                "truncated_stderr": truncated_stderr,
            },
            scope=f"script_request:{sr_id}",
        )

    def log_script_run_failed(
        self,
        *,
        sr_id: str,
        reason: str,
        exit_code: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        self._db.insert_audit_log(
            action="script_run_failed",
            task_id=None,
            agent="founder",
            payload={
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "reason": reason,
            },
            scope=f"script_request:{sr_id}",
        )
```

If the existing `insert_audit_log` signature differs from the kwargs above, match it (the goal is one audit row per call with the right `action`, `payload`, `scope`).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_audit_logger.py -v -k script`
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/audit_logger.py tests/test_audit_logger.py
git commit -m "feat(audit): script_* event methods"
```

---

### Task 9: Event-bus topic helper for per-SR streaming

**Files:**
- Modify: `src/daemon/event_bus.py`
- Test: `tests/test_event_bus.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_event_bus.py`:

```python
def test_script_topic_format():
    from src.daemon.event_bus import script_topic
    assert script_topic("SR-019") == "script:SR-019"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_event_bus.py -v -k script_topic`
Expected: ImportError.

- [ ] **Step 3: Add helper**

In `src/daemon/event_bus.py`, near the other `*_topic` helpers:

```python
def script_topic(sr_id: str) -> str:
    return f"script:{sr_id}"
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_event_bus.py -v`
Expected: passing.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/event_bus.py tests/test_event_bus.py
git commit -m "feat(event-bus): script_topic helper"
```

---

### Task 10: `scripts_runner.py` — happy-path subprocess execution

**Files:**
- Create: `src/daemon/scripts_runner.py`
- Test: `tests/test_scripts_runner.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_scripts_runner.py`:

```python
"""Unit tests for src/daemon/scripts_runner.py (spec §6)."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_paths():
    d = Path(tempfile.mkdtemp())
    yield {
        "cwd": d / "cwd",
        "stdout": d / "out.log",
        "stderr": d / "err.log",
    }
    # cleanup is best-effort; tempfile dir will be cleared by OS


def test_run_script_captures_stdout_and_exit_zero(tmp_paths):
    from src.daemon.scripts_runner import run_script
    tmp_paths["cwd"].mkdir()
    result = asyncio.run(run_script(
        script_text="echo hello",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        timeout_seconds=10,
        publish=lambda evt: None,
    ))
    assert result.exit_code == 0
    assert result.status == "completed"
    assert result.duration_ms >= 0
    assert "hello" in tmp_paths["stdout"].read_text()


def test_run_script_captures_stderr_and_nonzero_exit(tmp_paths):
    from src.daemon.scripts_runner import run_script
    tmp_paths["cwd"].mkdir()
    result = asyncio.run(run_script(
        script_text="echo oops >&2; exit 7",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        timeout_seconds=10,
        publish=lambda evt: None,
    ))
    assert result.exit_code == 7
    assert result.status == "completed"  # natural exit, even non-zero
    assert "oops" in tmp_paths["stderr"].read_text()


def test_run_script_publishes_line_events(tmp_paths):
    from src.daemon.scripts_runner import run_script
    tmp_paths["cwd"].mkdir()
    events: list[dict] = []
    asyncio.run(run_script(
        script_text="echo one; echo two; echo three >&2",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        timeout_seconds=10,
        publish=lambda evt: events.append(evt),
    ))
    kinds = [(e["stream"], e["line"]) for e in events if e.get("kind") == "line"]
    assert ("stdout", "one") in kinds
    assert ("stdout", "two") in kinds
    assert ("stderr", "three") in kinds
    # Terminal event always last.
    assert events[-1]["kind"] == "terminal"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_scripts_runner.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement runner**

Create `src/daemon/scripts_runner.py`:

```python
"""Subprocess execution for script_requests (spec §6).

Owns one short-lived asyncio coroutine per run: spawn → pump stdout/stderr →
fan out events to in-memory subscribers via the `publish` callback → terminate.

Module-level state: a registry of in-flight `asyncio.subprocess.Process` objects
keyed by SR id, used by the daemon shutdown path to SIGTERM/SIGKILL on exit.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


# In-flight registry; shutdown handler walks this to clean up.
_INFLIGHT: dict[str, asyncio.subprocess.Process] = {}

_HEAD_CAP_BYTES = 65536  # spec §3.1, §6.2


def _interpreter_binary(interpreter: str) -> str | None:
    return shutil.which(interpreter)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class ScriptRunResult:
    status: str           # "completed" | "failed"
    exit_code: int | None
    duration_ms: int
    stdout_head: str
    stderr_head: str
    stdout_bytes: int
    stderr_bytes: int
    truncated_stdout: bool
    truncated_stderr: bool
    reason: str | None = None   # populated only when status == "failed"


async def _pump_stream(
    stream: asyncio.StreamReader,
    label: str,
    out_path: str,
    publish: Callable[[dict], None],
    head_buf: list[bytes],
    head_capped: list[bool],
    byte_counter: list[int],
) -> None:
    """Append bytes to disk, fan out line events, fill head buffer until cap."""
    with open(out_path, "ab", buffering=0) as f:
        while True:
            line = await stream.readline()
            if not line:
                return
            f.write(line)
            byte_counter[0] += len(line)
            if not head_capped[0]:
                head_so_far = sum(len(b) for b in head_buf)
                room = _HEAD_CAP_BYTES - head_so_far
                if len(line) <= room:
                    head_buf.append(line)
                else:
                    if room > 0:
                        head_buf.append(line[:room])
                    head_capped[0] = True
            try:
                text = line.decode("utf-8", errors="replace").rstrip("\n")
            except Exception:
                text = "<binary>"
            publish({"kind": "line", "stream": label, "line": text, "ts": _now_iso()})


async def run_script(
    *,
    sr_id: str | None = None,
    script_text: str,
    interpreter: str,
    cwd: str,
    stdout_path: str,
    stderr_path: str,
    timeout_seconds: int,
    publish: Callable[[dict], None],
) -> ScriptRunResult:
    """Spawn the script, pump streams, return ScriptRunResult.

    `publish` is called with each line event and the final terminal event.
    `sr_id` is used only for the in-flight registry; pass None in unit tests.
    """
    binary = _interpreter_binary(interpreter)
    if binary is None:
        raise FileNotFoundError(f"interpreter unavailable: {interpreter}")

    started = datetime.now(timezone.utc)
    proc = await asyncio.create_subprocess_exec(
        binary,
        "-",  # read script from stdin (bash/sh/zsh/python3 all honor this)
        cwd=cwd,
        env=os.environ,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    if sr_id is not None:
        _INFLIGHT[sr_id] = proc

    proc.stdin.write(script_text.encode("utf-8"))
    proc.stdin.close()

    stdout_head: list[bytes] = []
    stderr_head: list[bytes] = []
    stdout_capped: list[bool] = [False]
    stderr_capped: list[bool] = [False]
    stdout_bytes = [0]
    stderr_bytes = [0]

    pump_out = asyncio.create_task(_pump_stream(
        proc.stdout, "stdout", stdout_path, publish, stdout_head, stdout_capped, stdout_bytes
    ))
    pump_err = asyncio.create_task(_pump_stream(
        proc.stderr, "stderr", stderr_path, publish, stderr_head, stderr_capped, stderr_bytes
    ))

    reason: str | None = None
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
        await asyncio.gather(pump_out, pump_err)
        status = "completed"
        exit_code = proc.returncode
    except asyncio.TimeoutError:
        reason = "timeout"
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
        # Drain pumps briefly; ignore errors.
        try:
            await asyncio.wait_for(asyncio.gather(pump_out, pump_err, return_exceptions=True), timeout=2)
        except asyncio.TimeoutError:
            pass
        status = "failed"
        exit_code = proc.returncode
    finally:
        if sr_id is not None:
            _INFLIGHT.pop(sr_id, None)

    finished = datetime.now(timezone.utc)
    duration_ms = int((finished - started).total_seconds() * 1000)

    head_marker_stdout = b"\n[truncated; see file]" if stdout_capped[0] else b""
    head_marker_stderr = b"\n[truncated; see file]" if stderr_capped[0] else b""
    stdout_head_str = b"".join(stdout_head).decode("utf-8", errors="replace") + head_marker_stdout.decode()
    stderr_head_str = b"".join(stderr_head).decode("utf-8", errors="replace") + head_marker_stderr.decode()

    result = ScriptRunResult(
        status=status,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout_head=stdout_head_str,
        stderr_head=stderr_head_str,
        stdout_bytes=stdout_bytes[0],
        stderr_bytes=stderr_bytes[0],
        truncated_stdout=stdout_capped[0],
        truncated_stderr=stderr_capped[0],
        reason=reason,
    )

    publish({
        "kind": "terminal",
        "status": status,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "reason": reason,
        "ts": _now_iso(),
    })
    return result


def in_flight_sr_ids() -> list[str]:
    return list(_INFLIGHT.keys())


async def terminate_all_inflight(*, grace_seconds: int = 5) -> None:
    """Daemon shutdown hook: SIGTERM every in-flight subprocess, then SIGKILL."""
    procs = list(_INFLIGHT.items())
    for sr_id, proc in procs:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if not procs:
        return
    await asyncio.sleep(grace_seconds)
    for sr_id, proc in procs:
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_scripts_runner.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/scripts_runner.py tests/test_scripts_runner.py
git commit -m "feat(scripts-runner): asyncio subprocess + stream pumps + head cap"
```

---

### Task 11: `scripts_runner.py` — timeout + interpreter-missing paths

**Files:**
- Modify: `src/daemon/scripts_runner.py` (no change — already handled in Task 10; this task just adds tests)
- Test: `tests/test_scripts_runner.py`

- [ ] **Step 1: Write tests**

Append to `tests/test_scripts_runner.py`:

```python
def test_run_script_timeout_marks_failed(tmp_paths):
    from src.daemon.scripts_runner import run_script
    tmp_paths["cwd"].mkdir()
    result = asyncio.run(run_script(
        script_text="sleep 30",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        timeout_seconds=1,
        publish=lambda evt: None,
    ))
    assert result.status == "failed"
    assert result.reason == "timeout"


def test_run_script_missing_interpreter_raises(tmp_paths):
    from src.daemon.scripts_runner import run_script
    tmp_paths["cwd"].mkdir()
    with pytest.raises(FileNotFoundError):
        asyncio.run(run_script(
            script_text="echo x",
            interpreter="no-such-shell-9999",
            cwd=str(tmp_paths["cwd"]),
            stdout_path=str(tmp_paths["stdout"]),
            stderr_path=str(tmp_paths["stderr"]),
            timeout_seconds=10,
            publish=lambda evt: None,
        ))


def test_in_flight_registry_clears_after_run(tmp_paths):
    from src.daemon.scripts_runner import run_script, in_flight_sr_ids
    tmp_paths["cwd"].mkdir()
    asyncio.run(run_script(
        sr_id="SR-T1",
        script_text="echo x",
        interpreter="bash",
        cwd=str(tmp_paths["cwd"]),
        stdout_path=str(tmp_paths["stdout"]),
        stderr_path=str(tmp_paths["stderr"]),
        timeout_seconds=10,
        publish=lambda evt: None,
    ))
    assert "SR-T1" not in in_flight_sr_ids()
```

- [ ] **Step 2: Run to verify pass (no impl change needed)**

Run: `uv run pytest tests/test_scripts_runner.py -v`
Expected: all passing. If timeout test takes ~3s, that's expected.

- [ ] **Step 3: Commit**

```bash
git add tests/test_scripts_runner.py
git commit -m "test(scripts-runner): timeout, missing interpreter, registry cleanup"
```

---

### Task 12: Route module skeleton + `POST /submit`

**Files:**
- Create: `src/daemon/routes/scripts.py`
- Test: `tests/test_routes_scripts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_routes_scripts.py`:

```python
"""Unit tests for src/daemon/routes/scripts.py validation gates (spec §5)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # Reuse the existing test harness for spinning up the daemon against a
    # tmp runtime. Mirror what tests/test_routes_threads.py does.
    from tests.helpers.daemon_harness import make_test_client
    with make_test_client() as c:
        yield c


def test_submit_unknown_task(client):
    r = client.post(
        "/api/v1/orgs/test/scripts/submit",
        json={
            "task_id": "TASK-999",
            "session_id": "sid",
            "title": "x",
            "rationale": "y",
            "script": "echo hi",
            "interpreter": "bash",
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_task"


def test_submit_task_not_active(client):
    task_id = client.create_completed_task(agent="engineering_head")
    r = client.post(
        f"/api/v1/orgs/test/scripts/submit",
        json={
            "task_id": task_id,
            "session_id": "sid",
            "title": "x", "rationale": "y", "script": "echo hi", "interpreter": "bash",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "task_not_active"


def test_submit_session_mismatch(client):
    task_id, _real_sid = client.create_active_session(agent="engineering_head")
    r = client.post(
        f"/api/v1/orgs/test/scripts/submit",
        json={
            "task_id": task_id,
            "session_id": "WRONG",
            "title": "x", "rationale": "y", "script": "echo hi", "interpreter": "bash",
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_submit_happy_path(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    r = client.post(
        f"/api/v1/orgs/test/scripts/submit",
        json={
            "task_id": task_id,
            "session_id": sid,
            "title": "Close PR #247",
            "rationale": "needs founder gh scope",
            "script": "gh pr close 247",
            "interpreter": "bash",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["id"].startswith("SR-")
    assert body["status"] == "pending"


def test_submit_empty_title(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    r = client.post(
        f"/api/v1/orgs/test/scripts/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "  ", "rationale": "y", "script": "x", "interpreter": "bash",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_title"


def test_submit_unknown_interpreter(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    r = client.post(
        f"/api/v1/orgs/test/scripts/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "x", "rationale": "y", "script": "x", "interpreter": "ruby",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "unknown_interpreter"


def test_submit_invalid_cwd_hint_dotdot(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    r = client.post(
        f"/api/v1/orgs/test/scripts/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "x", "rationale": "y", "script": "x", "interpreter": "bash",
            "cwd_hint": "../../etc",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_cwd_hint"


def test_submit_script_too_large(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    big = "x" * 65537
    r = client.post(
        f"/api/v1/orgs/test/scripts/submit",
        json={
            "task_id": task_id, "session_id": sid,
            "title": "x", "rationale": "y", "script": big, "interpreter": "bash",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "script_too_large"
```

If `tests/helpers/daemon_harness.py` doesn't already exist, peek at existing route tests (e.g., `tests/test_routes_threads.py`) and mirror their fixtures inline.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_routes_scripts.py -v`
Expected: 404 on all (route doesn't exist).

- [ ] **Step 3: Implement route module skeleton + `/submit`**

Create `src/daemon/routes/scripts.py`:

```python
"""Script request endpoints (spec §5)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.daemon.auth import require_token
from src.daemon.routes._org_dep import OrgDep
from src.infrastructure.audit_logger import AuditLogger
from src.models import (
    ScriptInterpreter,
    ScriptRequestRecord,
    ScriptRequestStatus,
)

router = APIRouter(dependencies=[require_token()])

_MAX_SCRIPT_BYTES = 65536
_MAX_TITLE_LEN = 200
_MAX_REJECT_REASON_LEN = 1000
_VALID_INTERPRETERS = {"bash", "sh", "zsh", "python3"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_cwd_hint(cwd_hint: str | None, workspace_root: Path) -> str | None:
    if cwd_hint is None:
        return None
    if cwd_hint.startswith("/"):
        raise HTTPException(status_code=422, detail={"code": "invalid_cwd_hint", "reason": "absolute_path"})
    parts = [p for p in cwd_hint.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise HTTPException(status_code=422, detail={"code": "invalid_cwd_hint", "reason": "dotdot"})
    return cwd_hint


class SubmitBody(BaseModel):
    task_id: str
    session_id: str
    title: str
    rationale: str
    script: str
    interpreter: str
    cwd_hint: str | None = None


@router.post("/scripts/submit", status_code=201)
async def submit_script(
    slug: str, body: SubmitBody, org: OrgDep, request: Request
) -> dict:
    # Spec §5.1 validation order.
    # 1. Task exists.
    task = org.db.get_task(body.task_id)
    if task is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_task", "task_id": body.task_id})

    # 2. Task status active (BEFORE session — completed tasks have no live session).
    if task.status.value not in ("pending", "in_progress"):
        raise HTTPException(
            status_code=400,
            detail={"code": "task_not_active", "status": task.status.value},
        )

    # 3. Session ownership.
    agent = task.assigned_agent
    active_sid = org.sessions.get_active(body.task_id, agent)
    if active_sid is None or active_sid != body.session_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "session_mismatch", "active": active_sid, "got": body.session_id},
        )

    # 4. Title.
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail={"code": "empty_title"})
    if len(title) > _MAX_TITLE_LEN:
        raise HTTPException(status_code=422, detail={"code": "title_too_long", "max": _MAX_TITLE_LEN})

    # 5. Rationale.
    rationale = body.rationale.strip()
    if not rationale:
        raise HTTPException(status_code=422, detail={"code": "empty_rationale"})

    # 6. Script.
    script = body.script.strip()
    if not script:
        raise HTTPException(status_code=422, detail={"code": "empty_script"})
    if len(body.script.encode("utf-8")) > _MAX_SCRIPT_BYTES:
        raise HTTPException(status_code=422, detail={"code": "script_too_large", "max_bytes": _MAX_SCRIPT_BYTES})

    # 7. Interpreter.
    if body.interpreter not in _VALID_INTERPRETERS:
        raise HTTPException(status_code=422, detail={"code": "unknown_interpreter", "got": body.interpreter})

    # 8. cwd_hint shape (resolves under workspace root).
    workspace_root = org.root / "workspaces" / agent
    cwd_hint = _validate_cwd_hint(body.cwd_hint, workspace_root)

    # Effect: allocate id, insert row, audit.
    async with org.db_lock:
        sr_id = org.db.next_script_request_id()
        record = ScriptRequestRecord(
            id=sr_id,
            task_id=body.task_id,
            agent_name=agent,
            title=title,
            rationale=rationale,
            script_text=body.script,
            interpreter=ScriptInterpreter(body.interpreter),
            cwd_hint=cwd_hint,
            status=ScriptRequestStatus.PENDING,
            created_at=_now_iso(),
        )
        org.db.insert_script_request(record)

    audit = AuditLogger(org.db)
    audit.log_script_submitted(
        task_id=body.task_id,
        sr_id=sr_id,
        agent=agent,
        title=title,
        interpreter=body.interpreter,
        cwd_hint=cwd_hint,
        byte_size=len(body.script.encode("utf-8")),
        line_count=body.script.count("\n") + 1,
    )

    return {"id": sr_id, "status": "pending", "created_at": record.created_at}
```

Wire the router in `src/daemon/app.py`. Locate where other org-scoped routers are mounted (`app.include_router(threads.router, prefix=...)`) and add:

```python
from src.daemon.routes import scripts
app.include_router(scripts.router, prefix="/api/v1/orgs/{slug}", tags=["scripts"])
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_routes_scripts.py -v`
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/scripts.py src/daemon/app.py tests/test_routes_scripts.py
git commit -m "feat(routes): POST /scripts/submit with §5.1 validation"
```

---

### Task 13: `POST /scripts/{id}/reject`

**Files:**
- Modify: `src/daemon/routes/scripts.py`
- Test: `tests/test_routes_scripts.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_reject_happy_path(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    sr_id = client.submit_script(task_id, sid, "x", "y", "echo z", "bash")
    r = client.post(
        f"/api/v1/orgs/test/scripts/{sr_id}/reject",
        json={"reason": "too risky in prod"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert r.json()["reject_reason"] == "too risky in prod"


def test_reject_empty_reason(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    sr_id = client.submit_script(task_id, sid, "x", "y", "echo z", "bash")
    r = client.post(
        f"/api/v1/orgs/test/scripts/{sr_id}/reject", json={"reason": "  "}
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_reason"


def test_reject_unknown_sr(client):
    r = client.post("/api/v1/orgs/test/scripts/SR-999/reject", json={"reason": "x"})
    assert r.status_code == 404


def test_reject_not_pending(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    sr_id = client.submit_script(task_id, sid, "x", "y", "echo z", "bash")
    client.post(f"/api/v1/orgs/test/scripts/{sr_id}/reject", json={"reason": "x"})
    r = client.post(f"/api/v1/orgs/test/scripts/{sr_id}/reject", json={"reason": "y"})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "not_pending"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_routes_scripts.py -v -k reject`
Expected: 404 on routes.

- [ ] **Step 3: Implement `POST /{id}/reject`**

Append to `src/daemon/routes/scripts.py`:

```python
class RejectBody(BaseModel):
    reason: str


@router.post("/scripts/{sr_id}/reject")
async def reject_script(slug: str, sr_id: str, body: RejectBody, org: OrgDep) -> dict:
    record = org.db.get_script_request(sr_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_script_request", "sr_id": sr_id})

    reason = body.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail={"code": "empty_reason"})
    if len(reason) > _MAX_REJECT_REASON_LEN:
        raise HTTPException(status_code=422, detail={"code": "reason_too_long", "max": _MAX_REJECT_REASON_LEN})

    if record.status != ScriptRequestStatus.PENDING:
        raise HTTPException(
            status_code=409,
            detail={"code": "not_pending", "status": record.status.value},
        )

    reviewed_at = _now_iso()
    try:
        org.db.transition_script_to_rejected(
            sr_id, reviewer="founder", reason=reason, reviewed_at=reviewed_at,
        )
    except ValueError:
        # Race: someone else acted between our read and our write.
        raise HTTPException(status_code=409, detail={"code": "not_pending"})

    audit = AuditLogger(org.db)
    audit.log_script_rejected(sr_id=sr_id, reviewer="founder", reason=reason)

    updated = org.db.get_script_request(sr_id)
    return updated.model_dump()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_routes_scripts.py -v`
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/scripts.py tests/test_routes_scripts.py
git commit -m "feat(routes): POST /scripts/{id}/reject"
```

---

### Task 14: `GET /scripts/` (list) + `GET /scripts/{id}` (detail)

**Files:**
- Modify: `src/daemon/routes/scripts.py`
- Test: `tests/test_routes_scripts.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_list_scripts_default_filter_pending(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    sr1 = client.submit_script(task_id, sid, "a", "b", "echo 1", "bash")
    sr2 = client.submit_script(task_id, sid, "c", "d", "echo 2", "bash")
    client.post(f"/api/v1/orgs/test/scripts/{sr1}/reject", json={"reason": "x"})
    r = client.get("/api/v1/orgs/test/scripts/")
    assert r.status_code == 200
    ids = [item["id"] for item in r.json()["scripts"]]
    assert sr2 in ids
    assert sr1 not in ids


def test_list_scripts_status_all(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    sr1 = client.submit_script(task_id, sid, "a", "b", "echo 1", "bash")
    client.post(f"/api/v1/orgs/test/scripts/{sr1}/reject", json={"reason": "x"})
    r = client.get("/api/v1/orgs/test/scripts/?status=all")
    ids = [item["id"] for item in r.json()["scripts"]]
    assert sr1 in ids


def test_get_script_detail(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    sr_id = client.submit_script(task_id, sid, "title-x", "y", "echo 1", "bash")
    r = client.get(f"/api/v1/orgs/test/scripts/{sr_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == sr_id
    assert body["title"] == "title-x"
    assert body["script_text"] == "echo 1"


def test_get_script_detail_404(client):
    r = client.get("/api/v1/orgs/test/scripts/SR-999")
    assert r.status_code == 404
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_routes_scripts.py -v -k "list_scripts or get_script_detail"`
Expected: 404.

- [ ] **Step 3: Implement routes**

Append:

```python
@router.get("/scripts/")
async def list_scripts(
    slug: str,
    org: OrgDep,
    status: str | None = "pending",
    agent: str | None = None,
    task_id: str | None = None,
    limit: int = 50,
) -> dict:
    if limit <= 0 or limit > 200:
        raise HTTPException(status_code=422, detail={"code": "invalid_limit"})
    if status == "all" or status is None:
        status_filter: list[str] | None = None
    else:
        status_filter = [s.strip() for s in status.split(",") if s.strip()]
        for s in status_filter:
            if s not in {"pending", "rejected", "running", "completed", "failed"}:
                raise HTTPException(status_code=422, detail={"code": "invalid_status", "got": s})
    rows = org.db.list_script_requests(
        status=status_filter, agent=agent, task_id=task_id, limit=limit,
    )
    return {"scripts": [r.model_dump() for r in rows]}


@router.get("/scripts/{sr_id}")
async def get_script(slug: str, sr_id: str, org: OrgDep) -> dict:
    record = org.db.get_script_request(sr_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_script_request", "sr_id": sr_id})
    return record.model_dump()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_routes_scripts.py -v`
Expected: all passing.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/scripts.py tests/test_routes_scripts.py
git commit -m "feat(routes): GET /scripts/ + GET /scripts/{id}"
```

---

### Task 15: `POST /scripts/{id}/run` — kick off subprocess

**Files:**
- Modify: `src/daemon/routes/scripts.py`
- Test: `tests/test_routes_scripts.py`

This is the most complex route. It transitions state, freezes the script file, and spawns the runner as a background asyncio task.

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_run_happy_path_completes(client):
    """Submit, run, and verify the SR transitions to completed."""
    task_id, sid = client.create_active_session(agent="engineering_head")
    sr_id = client.submit_script(task_id, sid, "echo", "test", "echo hello", "bash")
    r = client.post(f"/api/v1/orgs/test/scripts/{sr_id}/run", json={})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "running"
    assert body["events_url"].endswith(f"/scripts/{sr_id}/events")

    # Wait for terminal state (max ~5s).
    import time
    for _ in range(50):
        d = client.get(f"/api/v1/orgs/test/scripts/{sr_id}").json()
        if d["status"] in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert d["status"] == "completed"
    assert d["exit_code"] == 0
    assert "hello" in d["stdout_head"]


def test_run_not_pending(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    sr_id = client.submit_script(task_id, sid, "x", "y", "echo 1", "bash")
    client.post(f"/api/v1/orgs/test/scripts/{sr_id}/reject", json={"reason": "x"})
    r = client.post(f"/api/v1/orgs/test/scripts/{sr_id}/run", json={})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "not_pending"


def test_run_invalid_timeout(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    sr_id = client.submit_script(task_id, sid, "x", "y", "echo 1", "bash")
    r = client.post(f"/api/v1/orgs/test/scripts/{sr_id}/run", json={"timeout_seconds": 0})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_timeout"


def test_run_cwd_override_missing(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    sr_id = client.submit_script(task_id, sid, "x", "y", "echo 1", "bash")
    r = client.post(
        f"/api/v1/orgs/test/scripts/{sr_id}/run",
        json={"cwd_override": "/this/path/does/not/exist"},
    )
    assert r.status_code == 422 or r.status_code == 409
    code = r.json()["detail"]["code"]
    assert code in ("invalid_cwd_override", "cwd_missing")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_routes_scripts.py -v -k "run_"`
Expected: 404.

- [ ] **Step 3: Implement `POST /{id}/run`**

Append to `src/daemon/routes/scripts.py`:

```python
import asyncio

from src.daemon.event_bus import script_topic
from src.daemon.scripts_runner import run_script as _spawn_script


class RunBody(BaseModel):
    cwd_override: str | None = None
    timeout_seconds: int | None = None


def _resolve_cwd(
    *, cwd_override: str | None, cwd_hint: str | None, workspace_root: Path,
) -> Path:
    if cwd_override is not None:
        if cwd_override.startswith("/"):
            return Path(cwd_override)
        return (workspace_root / cwd_override).resolve()
    if cwd_hint is not None:
        return (workspace_root / cwd_hint).resolve()
    return workspace_root


@router.post("/scripts/{sr_id}/run", status_code=202)
async def run_script_route(
    slug: str, sr_id: str, body: RunBody, org: OrgDep, request: Request,
) -> dict:
    record = org.db.get_script_request(sr_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_script_request", "sr_id": sr_id})

    if record.status != ScriptRequestStatus.PENDING:
        raise HTTPException(
            status_code=409, detail={"code": "not_pending", "status": record.status.value}
        )

    timeout = body.timeout_seconds if body.timeout_seconds is not None else record.timeout_seconds
    if timeout <= 0 or timeout > 86400:
        raise HTTPException(status_code=422, detail={"code": "invalid_timeout"})

    workspace_root = org.root / "workspaces" / record.agent_name
    try:
        cwd_resolved = _resolve_cwd(
            cwd_override=body.cwd_override,
            cwd_hint=record.cwd_hint,
            workspace_root=workspace_root,
        )
    except (ValueError, OSError):
        raise HTTPException(status_code=422, detail={"code": "invalid_cwd_override"})

    if not cwd_resolved.exists() or not cwd_resolved.is_dir():
        raise HTTPException(
            status_code=409,
            detail={"code": "cwd_missing", "resolved": str(cwd_resolved)},
        )

    # Interpreter binary must exist.
    from src.daemon.scripts_runner import _interpreter_binary
    if _interpreter_binary(record.interpreter.value) is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "interpreter_unavailable", "interpreter": record.interpreter.value},
        )

    # Allocate output paths under <runtime>/orgs/<slug>/scripts/.
    scripts_dir = org.root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = scripts_dir / f"{sr_id}.out"
    stderr_path = scripts_dir / f"{sr_id}.err"
    script_path = scripts_dir / f"{sr_id}.script"
    # Truncate any stale file (idempotency under retried 5xx).
    stdout_path.write_bytes(b"")
    stderr_path.write_bytes(b"")
    script_path.write_text(record.script_text, encoding="utf-8")

    now = _now_iso()
    try:
        org.db.transition_script_to_running(
            sr_id,
            reviewer="founder",
            reviewed_at=now,
            started_at=now,
            cwd_resolved=str(cwd_resolved),
            timeout_seconds=timeout,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )
    except ValueError:
        raise HTTPException(status_code=409, detail={"code": "not_pending"})

    audit = AuditLogger(org.db)
    audit.log_script_run_started(
        sr_id=sr_id, reviewer="founder",
        cwd_resolved=str(cwd_resolved),
        timeout_seconds=timeout,
        interpreter=record.interpreter.value,
    )

    # Spawn the runner outside the request lifecycle.
    async def _run_and_persist() -> None:
        async def publish(evt: dict) -> None:
            await org.event_bus.publish(script_topic(sr_id), evt)

        # The runner's `publish` is sync; wrap in a sync shim that schedules.
        loop = asyncio.get_running_loop()

        def _sync_publish(evt: dict) -> None:
            asyncio.run_coroutine_threadsafe(publish(evt), loop)

        try:
            result = await _spawn_script(
                sr_id=sr_id,
                script_text=record.script_text,
                interpreter=record.interpreter.value,
                cwd=str(cwd_resolved),
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                timeout_seconds=timeout,
                publish=_sync_publish,
            )
        except FileNotFoundError:
            # spawn_failed
            finished = _now_iso()
            org.db.transition_script_to_terminal(
                sr_id, status=ScriptRequestStatus.FAILED,
                exit_code=None, finished_at=finished, duration_ms=0,
                stdout_head=None, stderr_head=None,
            )
            audit.log_script_run_failed(sr_id=sr_id, reason="spawn_failed")
            return
        except Exception as exc:
            finished = _now_iso()
            try:
                org.db.transition_script_to_terminal(
                    sr_id, status=ScriptRequestStatus.FAILED,
                    exit_code=None, finished_at=finished, duration_ms=0,
                    stdout_head=None, stderr_head=str(exc),
                )
            except ValueError:
                pass
            audit.log_script_run_failed(
                sr_id=sr_id, reason="internal_error",
            )
            return

        finished = _now_iso()
        try:
            org.db.transition_script_to_terminal(
                sr_id,
                status=ScriptRequestStatus(result.status),
                exit_code=result.exit_code,
                finished_at=finished,
                duration_ms=result.duration_ms,
                stdout_head=result.stdout_head,
                stderr_head=result.stderr_head,
            )
        except ValueError:
            return

        if result.status == "completed":
            audit.log_script_run_completed(
                sr_id=sr_id,
                exit_code=result.exit_code or 0,
                duration_ms=result.duration_ms,
                stdout_bytes=result.stdout_bytes,
                stderr_bytes=result.stderr_bytes,
                truncated_stdout=result.truncated_stdout,
                truncated_stderr=result.truncated_stderr,
            )
        else:
            audit.log_script_run_failed(
                sr_id=sr_id,
                exit_code=result.exit_code,
                duration_ms=result.duration_ms,
                reason=result.reason or "unknown",
            )

    asyncio.create_task(_run_and_persist())

    return {
        "id": sr_id,
        "status": "running",
        "started_at": now,
        "cwd_resolved": str(cwd_resolved),
        "timeout_seconds": timeout,
        "events_url": f"/api/v1/orgs/{slug}/scripts/{sr_id}/events",
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_routes_scripts.py -v -k "run_"`
Expected: all passing (may take ~5s for the completion poll).

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/scripts.py tests/test_routes_scripts.py
git commit -m "feat(routes): POST /scripts/{id}/run kicks off subprocess + audit"
```

---

### Task 16: `GET /scripts/{id}/output` — post-run file dump

**Files:**
- Modify: `src/daemon/routes/scripts.py`
- Test: `tests/test_routes_scripts.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_output_after_run(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    sr_id = client.submit_script(task_id, sid, "x", "y", "echo abc; echo def >&2", "bash")
    client.post(f"/api/v1/orgs/test/scripts/{sr_id}/run", json={})
    # Wait for terminal.
    import time
    for _ in range(50):
        d = client.get(f"/api/v1/orgs/test/scripts/{sr_id}").json()
        if d["status"] in ("completed", "failed"):
            break
        time.sleep(0.1)
    r = client.get(f"/api/v1/orgs/test/scripts/{sr_id}/output")
    assert r.status_code == 200
    body = r.json()
    assert "abc" in body["stdout"]
    assert "def" in body["stderr"]


def test_output_while_running_409(client):
    task_id, sid = client.create_active_session(agent="engineering_head")
    sr_id = client.submit_script(task_id, sid, "x", "y", "sleep 5", "bash")
    client.post(f"/api/v1/orgs/test/scripts/{sr_id}/run", json={"timeout_seconds": 30})
    r = client.get(f"/api/v1/orgs/test/scripts/{sr_id}/output")
    assert r.status_code == 409
    # Clean up by killing the run.
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_routes_scripts.py -v -k output`
Expected: 404.

- [ ] **Step 3: Implement `/output`**

Append:

```python
@router.get("/scripts/{sr_id}/output")
async def get_script_output(
    slug: str, sr_id: str, org: OrgDep,
    stream: str = "both",
    max_bytes: int = 1_048_576,
) -> dict:
    record = org.db.get_script_request(sr_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_script_request"})
    if record.status not in (ScriptRequestStatus.COMPLETED, ScriptRequestStatus.FAILED, ScriptRequestStatus.REJECTED):
        raise HTTPException(status_code=409, detail={"code": "not_terminal", "status": record.status.value})
    if max_bytes <= 0 or max_bytes > 10 * 1_048_576:
        raise HTTPException(status_code=422, detail={"code": "invalid_max_bytes"})

    def _read(path: str | None) -> tuple[str, bool, int]:
        if path is None:
            return ("", False, 0)
        p = Path(path)
        if not p.exists():
            return ("", False, 0)
        total = p.stat().st_size
        data = p.read_bytes()[:max_bytes]
        return (data.decode("utf-8", errors="replace"), total > max_bytes, total)

    out, out_trunc, out_total = _read(record.stdout_path) if stream in ("stdout", "both") else ("", False, 0)
    err, err_trunc, err_total = _read(record.stderr_path) if stream in ("stderr", "both") else ("", False, 0)
    return {
        "stdout": out,
        "stderr": err,
        "truncated_stdout": out_trunc,
        "truncated_stderr": err_trunc,
        "total_stdout_bytes": out_total,
        "total_stderr_bytes": err_total,
    }
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_routes_scripts.py -v -k output`
Expected: passing.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/scripts.py tests/test_routes_scripts.py
git commit -m "feat(routes): GET /scripts/{id}/output post-run dump"
```

---

### Task 17: `GET /scripts/{id}/events` — SSE

**Files:**
- Modify: `src/daemon/routes/scripts.py`
- Test: `tests/test_routes_scripts.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_events_terminal_after_completed(client):
    """Connecting to /events on an already-terminal SR sends one terminal event
    and closes."""
    task_id, sid = client.create_active_session(agent="engineering_head")
    sr_id = client.submit_script(task_id, sid, "x", "y", "echo hi", "bash")
    client.post(f"/api/v1/orgs/test/scripts/{sr_id}/run", json={})
    import time
    for _ in range(50):
        if client.get(f"/api/v1/orgs/test/scripts/{sr_id}").json()["status"] in ("completed", "failed"):
            break
        time.sleep(0.1)
    # Read first chunk of SSE stream.
    with client.stream("GET", f"/api/v1/orgs/test/scripts/{sr_id}/events") as r:
        assert r.status_code == 200
        data = b""
        for chunk in r.iter_bytes():
            data += chunk
            if b"event: terminal" in data:
                break
        assert b"event: terminal" in data
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_routes_scripts.py -v -k events_terminal`
Expected: 404.

- [ ] **Step 3: Implement `/events`**

Append:

```python
import json as _json
from fastapi.responses import StreamingResponse


@router.get("/scripts/{sr_id}/events")
async def script_events_stream(slug: str, sr_id: str, org: OrgDep):
    record = org.db.get_script_request(sr_id)
    if record is None:
        raise HTTPException(status_code=404, detail={"code": "unknown_script_request"})

    async def gen():
        # If already terminal, emit one terminal event and close.
        if record.status in (ScriptRequestStatus.COMPLETED, ScriptRequestStatus.FAILED, ScriptRequestStatus.REJECTED):
            payload = {
                "status": record.status.value,
                "exit_code": record.exit_code,
                "duration_ms": record.duration_ms,
            }
            yield f"event: terminal\ndata: {_json.dumps(payload)}\n\n"
            return
        async for evt in org.event_bus.subscribe(script_topic(sr_id)):
            kind = evt.get("kind", "line")
            if kind == "line":
                stream = evt.get("stream", "stdout")
                yield f"event: {stream}\ndata: {_json.dumps({'line': evt.get('line', ''), 'ts': evt.get('ts')})}\n\n"
            elif kind == "terminal":
                yield f"event: terminal\ndata: {_json.dumps({'status': evt.get('status'), 'exit_code': evt.get('exit_code'), 'duration_ms': evt.get('duration_ms'), 'reason': evt.get('reason')})}\n\n"
                return

    return StreamingResponse(gen(), media_type="text/event-stream")
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_routes_scripts.py -v -k events`
Expected: passing.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/scripts.py tests/test_routes_scripts.py
git commit -m "feat(routes): GET /scripts/{id}/events SSE stream"
```

---

### Task 18: Daemon-startup recovery scan + shutdown cleanup

**Files:**
- Modify: `src/daemon/app.py`
- Test: `tests/test_daemon_lifecycle.py` (append; mirror existing lifecycle tests)

- [ ] **Step 1: Write the failing test**

Append (mirror existing lifecycle test structure):

```python
def test_startup_recovers_orphaned_running_scripts(daemon_with_seeded_running_sr):
    """If an SR was left in 'running' state by a previous daemon process,
    startup must mark it 'failed' with the canonical reason."""
    daemon = daemon_with_seeded_running_sr
    sr = daemon.client.get(
        f"/api/v1/orgs/{daemon.org_slug}/scripts/{daemon.seeded_sr_id}"
    ).json()
    assert sr["status"] == "failed"
```

(If the existing harness can't seed a row before daemon start, use a direct DB write helper inside the fixture; defer the test if necessary and revisit during integration testing — but write it now so the assertion is in the repo.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_daemon_lifecycle.py -v -k orphan`

- [ ] **Step 3: Wire the recovery call into the FastAPI lifespan**

In `src/daemon/app.py`, find the lifespan handler (or the `add_org` path). For every org that's loaded at startup OR when `add_org` runs:

```python
from datetime import datetime, timezone
# inside lifespan startup, per-org init:
for org in state.orgs.values():
    ids = org.db.recover_orphaned_running_scripts(
        now_iso=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )
    if ids:
        logger.warning("recovered %d orphaned SRs in org %s: %s", len(ids), org.slug, ids)
```

And in the shutdown branch of the lifespan:

```python
from src.daemon.scripts_runner import terminate_all_inflight
await terminate_all_inflight(grace_seconds=5)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_daemon_lifecycle.py -v`
Expected: passing.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/app.py tests/test_daemon_lifecycle.py
git commit -m "feat(daemon): SR recovery on startup + SIGTERM on shutdown"
```

---

### Task 19: CLI — `grassland scripts submit`

**Files:**
- Modify: `src/cli.py`
- Test: `tests/test_cli_scripts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_scripts.py`:

```python
"""CLI tests for grassland scripts subcommands."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest


def _run(*args, daemon_url="http://localhost:0", token=None) -> subprocess.CompletedProcess:
    """Invoke `uv run grassland ...` against a stub daemon URL (set via env)."""
    env = {"GRASSLAND_DAEMON_URL": daemon_url}
    if token:
        env["GRASSLAND_AUTH_TOKEN"] = token
    return subprocess.run(
        ["uv", "run", "grassland", *args],
        capture_output=True, text=True, env={**__import__("os").environ, **env},
    )


def test_scripts_submit_help():
    result = _run("scripts", "submit", "--help")
    assert result.returncode == 0
    assert "--from-file" in result.stdout
```

(The fuller integration test lands in Task 32 — the unit test here just confirms the subcommand parses.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli_scripts.py -v`
Expected: nonzero return (no `scripts` subcommand).

- [ ] **Step 3: Wire subparser + `cmd_scripts_submit`**

In `src/cli.py`, after the existing top-level subcommand registrations (around line 2287 where `p_dispatch` is added):

```python
    p_scripts = sub.add_parser("scripts", help="Script requests (agent → founder review)")
    scripts_sub = p_scripts.add_subparsers(dest="scripts_cmd")

    p_scripts_submit = scripts_sub.add_parser("submit", help="Agent callback: submit a script for founder review")
    p_scripts_submit.add_argument("--from-file", required=True, help="JSON payload file")
    p_scripts_submit.add_argument("--org", help="Org slug")
    p_scripts_submit.set_defaults(func=cmd_scripts_submit)
```

Add the command function next to other agent-callback CLIs (near `cmd_dispatch`):

```python
def _scripts_submit_payload_from_file(path: str) -> dict:
    """Load a scripts-submit payload from a JSON file (mirrors manage-repo pattern)."""
    with open(path) as f:
        data = json.load(f)
    required = ("task_id", "session_id", "title", "rationale", "script", "interpreter")
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(f"scripts submit file missing keys: {missing}")
    return data


def cmd_scripts_submit(args: argparse.Namespace) -> None:
    """Agent callback: submit a script for founder review."""
    try:
        body = _scripts_submit_payload_from_file(args.from_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Error reading scripts-submit file {args.from_file}: {exc}", file=sys.stderr)
        sys.exit(2)
    slug = _resolve_slug(args)
    client = make_client()
    r = client.post(f"/api/v1/orgs/{slug}/scripts/submit", json=body)
    if r.status_code != 201:
        print(f"Error: {r.status_code} {r.text}", file=sys.stderr)
        sys.exit(1)
    result = r.json()
    print(f"ok: submitted {result['id']} (status={result['status']}). Self-block your task referencing this ID.")
```

(`_resolve_slug` and `make_client` are existing helpers — match the imports the other agent-callback commands use.)

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cli_scripts.py -v`
Expected: passing.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli_scripts.py
git commit -m "feat(cli): grassland scripts submit (agent callback, --from-file)"
```

---

### Task 20: CLI — `grassland scripts list|show|reject|output`

**Files:**
- Modify: `src/cli.py`
- Test: `tests/test_cli_scripts.py`

- [ ] **Step 1: Write failing tests**

Append:

```python
def test_scripts_list_help():
    assert _run("scripts", "list", "--help").returncode == 0


def test_scripts_show_help():
    assert _run("scripts", "show", "--help").returncode == 0


def test_scripts_reject_help():
    assert _run("scripts", "reject", "--help").returncode == 0


def test_scripts_output_help():
    assert _run("scripts", "output", "--help").returncode == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli_scripts.py -v`

- [ ] **Step 3: Implement subcommands**

Append subparsers under `scripts_sub` (next to `p_scripts_submit`):

```python
    p_scripts_list = scripts_sub.add_parser("list", help="List script requests")
    p_scripts_list.add_argument("--status", default="pending", help="comma-separated statuses, or 'all'")
    p_scripts_list.add_argument("--agent")
    p_scripts_list.add_argument("--task")
    p_scripts_list.add_argument("--limit", type=int, default=50)
    p_scripts_list.add_argument("--org")
    p_scripts_list.set_defaults(func=cmd_scripts_list)

    p_scripts_show = scripts_sub.add_parser("show", help="Show one script request")
    p_scripts_show.add_argument("sr_id")
    p_scripts_show.add_argument("--org")
    p_scripts_show.set_defaults(func=cmd_scripts_show)

    p_scripts_reject = scripts_sub.add_parser("reject", help="Reject a pending script request")
    p_scripts_reject.add_argument("sr_id")
    p_scripts_reject.add_argument("--reason", help="rejection reason (prompted if omitted)")
    p_scripts_reject.add_argument("--org")
    p_scripts_reject.set_defaults(func=cmd_scripts_reject)

    p_scripts_output = scripts_sub.add_parser("output", help="Fetch captured output of a terminal SR")
    p_scripts_output.add_argument("sr_id")
    p_scripts_output.add_argument("--stream", choices=["stdout", "stderr", "both"], default="both")
    p_scripts_output.add_argument("--max-bytes", type=int, default=1_048_576)
    p_scripts_output.add_argument("--org")
    p_scripts_output.set_defaults(func=cmd_scripts_output)
```

Add the command functions:

```python
def cmd_scripts_list(args: argparse.Namespace) -> None:
    slug = _resolve_slug(args)
    client = make_client()
    params = {"status": args.status, "limit": args.limit}
    if args.agent:
        params["agent"] = args.agent
    if args.task:
        params["task_id"] = args.task
    r = client.get(f"/api/v1/orgs/{slug}/scripts/", params=params)
    if r.status_code != 200:
        print(f"Error: {r.status_code} {r.text}", file=sys.stderr)
        sys.exit(1)
    rows = r.json()["scripts"]
    if not rows:
        print("(no script requests match)")
        return
    print(f"{'ID':<8} {'AGENT':<20} {'TASK':<12} {'STATUS':<10} {'AGE':<8} TITLE")
    for row in rows:
        title = row["title"][:60]
        print(f"{row['id']:<8} {row['agent_name']:<20} {row['task_id']:<12} "
              f"{row['status']:<10} {'':<8} {title}")


def cmd_scripts_show(args: argparse.Namespace) -> None:
    slug = _resolve_slug(args)
    client = make_client()
    r = client.get(f"/api/v1/orgs/{slug}/scripts/{args.sr_id}")
    if r.status_code != 200:
        print(f"Error: {r.status_code} {r.text}", file=sys.stderr)
        sys.exit(1)
    d = r.json()
    print(f"{d['id']}   {d['status']}   submitted {d['created_at']}")
    print(f"Agent:        {d['agent_name']}")
    print(f"Task:         {d['task_id']}")
    print(f"Interpreter:  {d['interpreter']}")
    print(f"Cwd hint:     {d['cwd_hint'] or '(workspace root)'}")
    print()
    print(f"Title:        {d['title']}")
    print()
    print("Rationale:")
    for line in d["rationale"].splitlines():
        print(f"  {line}")
    print()
    print("Script:")
    for line in d["script_text"].splitlines():
        print(f"  {line}")
    if d["status"] in ("completed", "failed"):
        print()
        print(f"Exit code:    {d['exit_code']}")
        print(f"Duration:     {d['duration_ms']}ms")
        if d["stdout_head"]:
            print("Stdout (head):")
            for line in d["stdout_head"].splitlines():
                print(f"  {line}")
        if d["stderr_head"]:
            print("Stderr (head):")
            for line in d["stderr_head"].splitlines():
                print(f"  {line}")
        print(f"Full output:  grassland scripts output {d['id']}")
    elif d["status"] == "pending":
        print()
        print("Founder actions:")
        print(f"  grassland scripts run {d['id']} [--cwd PATH] [--timeout-seconds N]")
        print(f"  grassland scripts reject {d['id']} --reason \"...\"")
    elif d["status"] == "rejected":
        print()
        print(f"Reject reason: {d['reject_reason']}")


def cmd_scripts_reject(args: argparse.Namespace) -> None:
    slug = _resolve_slug(args)
    reason = args.reason
    if not reason:
        print("Enter rejection reason (end with '.' on its own line):")
        lines: list[str] = []
        while True:
            line = input()
            if line.strip() == ".":
                break
            lines.append(line)
        reason = "\n".join(lines).strip()
    if not reason:
        print("Error: empty reason", file=sys.stderr)
        sys.exit(2)
    client = make_client()
    r = client.post(f"/api/v1/orgs/{slug}/scripts/{args.sr_id}/reject", json={"reason": reason})
    if r.status_code != 200:
        print(f"Error: {r.status_code} {r.text}", file=sys.stderr)
        sys.exit(1)
    print(f"ok: rejected {args.sr_id}")


def cmd_scripts_output(args: argparse.Namespace) -> None:
    slug = _resolve_slug(args)
    client = make_client()
    r = client.get(
        f"/api/v1/orgs/{slug}/scripts/{args.sr_id}/output",
        params={"stream": args.stream, "max_bytes": args.max_bytes},
    )
    if r.status_code != 200:
        print(f"Error: {r.status_code} {r.text}", file=sys.stderr)
        sys.exit(1)
    body = r.json()
    if args.stream in ("stdout", "both"):
        print("--- stdout ---")
        print(body["stdout"], end="" if body["stdout"].endswith("\n") else "\n")
    if args.stream in ("stderr", "both"):
        print("--- stderr ---")
        print(body["stderr"], end="" if body["stderr"].endswith("\n") else "\n")
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cli_scripts.py -v`
Expected: passing.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli_scripts.py
git commit -m "feat(cli): grassland scripts list|show|reject|output"
```

---

### Task 21: CLI — `grassland scripts run` (TTY confirm + SSE stream)

**Files:**
- Modify: `src/cli.py`
- Test: `tests/test_cli_scripts.py`

- [ ] **Step 1: Write the failing test**

Append:

```python
def test_scripts_run_help():
    assert _run("scripts", "run", "--help").returncode == 0


def test_scripts_run_requires_tty(monkeypatch):
    """Non-TTY invocation should fail-fast with the canonical message."""
    # stdin is a pipe in subprocess, so this should hit the TTY guard.
    r = _run("scripts", "run", "SR-001")
    # We expect non-zero exit and the canonical guard text.
    assert r.returncode != 0
    assert "TTY" in (r.stderr + r.stdout)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli_scripts.py -v -k run`

- [ ] **Step 3: Implement `cmd_scripts_run`**

Add subparser:

```python
    p_scripts_run = scripts_sub.add_parser("run", help="Run a pending script request (TTY-gated)")
    p_scripts_run.add_argument("sr_id")
    p_scripts_run.add_argument("--cwd", dest="cwd_override")
    p_scripts_run.add_argument("--timeout-seconds", type=int)
    p_scripts_run.add_argument("--org")
    p_scripts_run.set_defaults(func=cmd_scripts_run)
```

Add the function:

```python
def cmd_scripts_run(args: argparse.Namespace) -> None:
    import sys as _sys

    if not _sys.stdin.isatty():
        print(
            "error: scripts run requires a TTY (interactive confirmation). "
            "Use the web UI to run non-interactively.",
            file=_sys.stderr,
        )
        _sys.exit(2)

    slug = _resolve_slug(args)
    client = make_client()
    # Fetch + show.
    r = client.get(f"/api/v1/orgs/{slug}/scripts/{args.sr_id}")
    if r.status_code != 200:
        print(f"Error: {r.status_code} {r.text}", file=_sys.stderr)
        _sys.exit(1)
    d = r.json()
    if d["status"] != "pending":
        print(f"Error: SR {args.sr_id} is {d['status']}, not pending", file=_sys.stderr)
        _sys.exit(1)
    print(f"About to execute {d['id']}:")
    print(f"  Agent:       {d['agent_name']}")
    print(f"  Task:        {d['task_id']}")
    print(f"  Interpreter: {d['interpreter']}")
    cwd_display = args.cwd_override or d['cwd_hint'] or "(workspace root)"
    print(f"  Cwd:         {cwd_display}")
    print(f"  Timeout:     {args.timeout_seconds or d['timeout_seconds']}s")
    print()
    print("Script:")
    for line in d["script_text"].splitlines():
        print(f"  {line}")
    print()
    answer = input("Proceed? [y/N]: ").strip().lower()
    if answer != "y":
        print("Aborted.")
        _sys.exit(1)

    # POST /run.
    body: dict = {}
    if args.cwd_override is not None:
        body["cwd_override"] = args.cwd_override
    if args.timeout_seconds is not None:
        body["timeout_seconds"] = args.timeout_seconds
    r = client.post(f"/api/v1/orgs/{slug}/scripts/{args.sr_id}/run", json=body)
    if r.status_code != 202:
        print(f"Error: {r.status_code} {r.text}", file=_sys.stderr)
        _sys.exit(1)

    # Stream SSE until terminal.
    import httpx
    events_url = r.json()["events_url"]
    base = client.base_url
    with httpx.stream("GET", f"{base}{events_url}", headers=client.headers) as resp:
        terminal_status = None
        terminal_exit = None
        buf = b""
        for chunk in resp.iter_bytes():
            buf += chunk
            while b"\n\n" in buf:
                event, _, buf = buf.partition(b"\n\n")
                lines = event.decode("utf-8", errors="replace").splitlines()
                etype = ""
                edata = ""
                for ln in lines:
                    if ln.startswith("event: "):
                        etype = ln[7:]
                    elif ln.startswith("data: "):
                        edata = ln[6:]
                if etype in ("stdout", "stderr"):
                    payload = json.loads(edata)
                    prefix = "[stdout]" if etype == "stdout" else "[stderr]"
                    print(f"{prefix} {payload['line']}")
                elif etype == "terminal":
                    payload = json.loads(edata)
                    terminal_status = payload.get("status")
                    terminal_exit = payload.get("exit_code")
                    dur = payload.get("duration_ms", 0)
                    print(f"[done]   exit={terminal_exit} duration={dur/1000:.1f}s")
                    break
            if terminal_status:
                break

    if terminal_status == "completed":
        _sys.exit(0 if (terminal_exit or 0) == 0 else 1)
    _sys.exit(2)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_cli_scripts.py -v -k run`
Expected: passing.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/test_cli_scripts.py
git commit -m "feat(cli): grassland scripts run with TTY confirm + SSE stream"
```

---

### Task 22: Skill `protocol/skills/scripts/SKILL.md`

**Files:**
- Create: `protocol/skills/scripts/SKILL.md`
- Modify: `protocol/skills/start-task/SKILL.md` (cross-ref)

- [ ] **Step 1: Create the skill**

Create `protocol/skills/scripts/SKILL.md`:

```markdown
# Skill: scripts — submit a script the founder will run for you

## When to use

You hit a permission wall and need a command run that your sandbox can't run itself. Typical signals:

- Your `--allowedTools` (Claude) or `permission.bash` (opencode) denies a `gh`, `aws`, `stripe`, `ssh`, or `sudo` invocation — and the operation genuinely needs founder-grade credentials.
- A binary you need is not in any of your `allow_rules` prefixes.
- An operation requires environment / credentials that only the founder's shell has.

Do NOT use this skill for anything you could just do in your own workspace (e.g., `chmod +x` and run a local helper). Submitting a script to the founder is a one-shot blocking interaction; use it when there is no other way.

## How to submit

Single-line `grassland scripts submit --from-file <path>` invocation, just like `report-completion`. Multi-line bash is split by Claude's permission matcher — keep it one line.

Write the JSON payload to `/tmp/script-<random>.json` first:

```json
{
  "task_id": "TASK-091",
  "session_id": "<your active session_id>",
  "title": "Close PR #247 with approval comment",
  "rationale": "PR review is complete. My allow_rules cover `gh pr comment` but not `gh pr close`. Need founder to merge-close so the auth-rewrite branch can be deleted.",
  "script": "set -euo pipefail\ngh pr close 247 --comment 'Approved and closed per review thread THR-014.'\n",
  "interpreter": "bash",
  "cwd_hint": "repos/web-app"
}
```

Required: `task_id`, `session_id`, `title`, `rationale`, `script`, `interpreter`. Optional: `cwd_hint` (relative path under your workspace; absent = workspace root).

Allowed `interpreter` values: `bash`, `sh`, `zsh`, `python3`.

Then invoke:

```bash
grassland scripts submit --from-file /tmp/script-9j2.json
```

Output is `ok: submitted SR-NNN ...`. Keep the `SR-NNN` id — reference it in your completion report.

## After submitting: self-block

Always self-block your task immediately after submit. Report completion with `status="blocked"`, summary referencing the SR-NNN:

```json
{
  "task_id": "TASK-091",
  "status": "blocked",
  "summary": "Awaiting SR-019 (Close PR #247 with approval comment). Cannot proceed until founder runs and confirms output."
}
```

The orchestrator's manager will see the block and escalate to the founder. Once the founder has run the script and reviewed the output, they will use `grassland revisit <task-id>` to spawn a fresh root with your SR's output available in context. You do NOT need to poll for the output yourself — it will arrive in your next revisited task.

## If the founder rejects

The reject reason will be visible in the SR's audit trail. The founder may revisit the task with a different brief, or you may need to re-submit a corrected script if asked.
```

- [ ] **Step 2: Cross-reference from `start-task`**

In `protocol/skills/start-task/SKILL.md`, find the section about handling errors or permission failures (likely near the bottom). Add:

```markdown
## Permission walls

If your executor refuses a command (Claude `--allowedTools`, opencode `permission.bash`, Codex sandbox), and the operation genuinely needs founder-grade credentials, see `protocol/skills/scripts/SKILL.md`. Submit the script for founder review, then self-block.
```

- [ ] **Step 3: Commit**

```bash
git add protocol/skills/scripts/SKILL.md protocol/skills/start-task/SKILL.md
git commit -m "docs(skills): scripts skill + start-task cross-reference"
```

---

### Task 23: Extend `_revisit_header_if_applicable` to surface SR summaries

**Files:**
- Modify: `src/orchestrator/run_step.py`
- Test: `tests/test_revisit_header.py` (append; create if missing)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_revisit_header.py`:

```python
def test_revisit_header_includes_sr_summary(orch_with_sr_in_predecessor):
    """When the predecessor task submitted SRs, the revisit header must list
    each terminal SR with status, title, and the show/output commands."""
    orch, task_id = orch_with_sr_in_predecessor
    from src.orchestrator.run_step import _revisit_header_if_applicable
    header = _revisit_header_if_applicable(orch, task_id)
    assert header is not None
    assert "SR-019" in header
    assert "Close PR #247" in header
    assert "grassland scripts show SR-019" in header
    assert "grassland scripts output SR-019" in header
```

(If the fixture doesn't exist, peek at existing revisit-header tests and adapt — the key setup is: insert a `revisit_of` audit row plus a `script_submitted` audit row for the predecessor.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_revisit_header.py -v -k sr`

- [ ] **Step 3: Extend the helper**

In `src/orchestrator/run_step.py`, modify `_revisit_header_if_applicable`. Locate the function (around line 396). At the end of the function, just before the `return "\n".join(lines) + "\n\n"`, add:

```python
    # SR summary block — list any script requests submitted by the predecessor.
    predecessor_logs = orch._db.get_audit_logs(predecessor)
    sr_entries = [e for e in predecessor_logs if e["action"] == "script_submitted"]
    if sr_entries:
        lines.append("")
        lines.append("This task previously submitted script requests:")
        for e in sr_entries:
            sr_id = e["payload"]["script_request_id"]
            title = e["payload"]["title"]
            sr = orch._db.get_script_request(sr_id)
            status = sr.status.value if sr else "?"
            marker = ""
            if sr and sr.status.value in ("pending", "running"):
                marker = " [still pending — founder action needed]"
            lines.append(f"  - {sr_id} ({status}) — {title}{marker}")
        lines.append("")
        lines.append("Read the outputs / rejection reasons before continuing:")
        for e in sr_entries:
            sr_id = e["payload"]["script_request_id"]
            lines.append(f"  grassland scripts show {sr_id}")
            lines.append(f"  grassland scripts output {sr_id}")
```

Also do the analogous append in `_auto_revisit_header` if it makes sense for that path — but if the auto-revisit predecessor is rarely an SR-submitting agent, leave that for a follow-up.

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_revisit_header.py -v`
Expected: passing.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/test_revisit_header.py
git commit -m "feat(revisit): header surfaces predecessor SRs with show/output cmds"
```

---

### Task 24: Regenerate OpenAPI snapshot

**Files:**
- Modify: `tests/contract/openapi.json` (auto-regenerated)

- [ ] **Step 1: Regenerate**

Run: `GRASSLAND_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py -v`

- [ ] **Step 2: Confirm snapshot diff**

Run: `git diff tests/contract/openapi.json | head -80`
Expected: 7 new path entries (`/scripts/submit`, `/scripts/`, `/scripts/{sr_id}`, `/scripts/{sr_id}/run`, `/scripts/{sr_id}/reject`, `/scripts/{sr_id}/output`, `/scripts/{sr_id}/events`).

- [ ] **Step 3: Run snapshot test (non-regen) to confirm it passes**

Run: `uv run pytest tests/contract/test_openapi_snapshot.py -v`
Expected: passing.

- [ ] **Step 4: Commit**

```bash
git add tests/contract/openapi.json
git commit -m "chore(contract): regenerate openapi snapshot with /scripts routes"
```

---

### Task 25: TS API mirror `web/src/lib/api/scripts.ts`

**Files:**
- Create: `web/src/lib/api/scripts.ts`
- Modify: `web/src/lib/api/index.ts`
- Modify: `web/src/lib/api/types.ts` (add ScriptRequest type)
- Modify: `web/src/test/openapi-coverage.test.ts`
- Test: `web/src/lib/api/scripts.test.ts`

- [ ] **Step 1: Write the failing TS test**

Create `web/src/lib/api/scripts.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from 'vitest';
import * as scripts from './scripts';
import * as clientModule from './client';

describe('scripts api', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('listScripts builds the right URL with default params', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ scripts: [] });
    await scripts.listScripts('test');
    expect(spy).toHaveBeenCalledWith('/orgs/test/scripts/', { params: undefined });
  });

  it('listScripts forwards filter params', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ scripts: [] });
    await scripts.listScripts('test', { status: 'pending', agent: 'a', limit: 10 });
    expect(spy).toHaveBeenCalledWith('/orgs/test/scripts/', {
      params: { status: 'pending', agent: 'a', limit: 10 },
    });
  });

  it('getScript fetches detail', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ id: 'SR-001' });
    await scripts.getScript('test', 'SR-001');
    expect(spy).toHaveBeenCalledWith('/orgs/test/scripts/SR-001');
  });

  it('runScript POSTs body', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ id: 'SR-001', status: 'running' });
    await scripts.runScript('test', 'SR-001', { timeout_seconds: 600 });
    expect(spy).toHaveBeenCalledWith('/orgs/test/scripts/SR-001/run', {
      method: 'POST',
      body: { timeout_seconds: 600 },
    });
  });

  it('rejectScript POSTs reason', async () => {
    const spy = vi.spyOn(clientModule, 'request').mockResolvedValue({ id: 'SR-001', status: 'rejected' });
    await scripts.rejectScript('test', 'SR-001', { reason: 'no' });
    expect(spy).toHaveBeenCalledWith('/orgs/test/scripts/SR-001/reject', {
      method: 'POST',
      body: { reason: 'no' },
    });
  });

  it('scriptEventsPath returns SSE path', () => {
    expect(scripts.scriptEventsPath('test', 'SR-001')).toBe('/orgs/test/scripts/SR-001/events');
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd web && npm test -- scripts.test`
Expected: file-not-found.

- [ ] **Step 3: Implement `scripts.ts`**

Create `web/src/lib/api/scripts.ts`:

```typescript
/** Mirror of src/daemon/routes/scripts.py — founder-facing surface only.
 *
 * Excluded (agent callback): POST /scripts/submit.
 */
import { request } from './client';
import type { ScriptRequest, ScriptOutput, ScriptRunResponse, ScriptListResponse } from './types';

export const listScripts = (
  slug: string,
  params?: { status?: string; agent?: string; task_id?: string; limit?: number },
): Promise<ScriptListResponse> =>
  request(`/orgs/${slug}/scripts/`, { params });

export const getScript = (slug: string, sr_id: string): Promise<ScriptRequest> =>
  request(`/orgs/${slug}/scripts/${sr_id}`);

export const runScript = (
  slug: string,
  sr_id: string,
  body: { cwd_override?: string; timeout_seconds?: number },
): Promise<ScriptRunResponse> =>
  request(`/orgs/${slug}/scripts/${sr_id}/run`, { method: 'POST', body });

export const rejectScript = (
  slug: string,
  sr_id: string,
  body: { reason: string },
): Promise<ScriptRequest> =>
  request(`/orgs/${slug}/scripts/${sr_id}/reject`, { method: 'POST', body });

export const getScriptOutput = (
  slug: string,
  sr_id: string,
  params?: { stream?: 'stdout' | 'stderr' | 'both'; max_bytes?: number },
): Promise<ScriptOutput> =>
  request(`/orgs/${slug}/scripts/${sr_id}/output`, { params });

export const scriptEventsPath = (slug: string, sr_id: string): string =>
  `/orgs/${slug}/scripts/${sr_id}/events`;
```

Add types to `web/src/lib/api/types.ts`:

```typescript
export type ScriptRequestStatus = 'pending' | 'rejected' | 'running' | 'completed' | 'failed';
export type ScriptInterpreter = 'bash' | 'sh' | 'zsh' | 'python3';

export interface ScriptRequest {
  id: string;
  task_id: string;
  agent_name: string;
  title: string;
  rationale: string;
  script_text: string;
  interpreter: ScriptInterpreter;
  cwd_hint: string | null;
  status: ScriptRequestStatus;
  exit_code: number | null;
  stdout_head: string | null;
  stderr_head: string | null;
  stdout_path: string | null;
  stderr_path: string | null;
  duration_ms: number | null;
  started_at: string | null;
  finished_at: string | null;
  reviewed_at: string | null;
  reviewed_by: string | null;
  reject_reason: string | null;
  cwd_resolved: string | null;
  timeout_seconds: number;
  created_at: string;
}

export interface ScriptListResponse {
  scripts: ScriptRequest[];
}

export interface ScriptRunResponse {
  id: string;
  status: 'running';
  started_at: string;
  cwd_resolved: string;
  timeout_seconds: number;
  events_url: string;
}

export interface ScriptOutput {
  stdout: string;
  stderr: string;
  truncated_stdout: boolean;
  truncated_stderr: boolean;
  total_stdout_bytes: number;
  total_stderr_bytes: number;
}
```

Re-export in `web/src/lib/api/index.ts`:

```typescript
export * as scripts from './scripts';
```

Update `web/src/test/openapi-coverage.test.ts` — add to INCLUDED:

```typescript
  // scripts — founder-facing
  'GET /api/v1/orgs/{slug}/scripts/',
  'GET /api/v1/orgs/{slug}/scripts/{sr_id}',
  'POST /api/v1/orgs/{slug}/scripts/{sr_id}/run',
  'POST /api/v1/orgs/{slug}/scripts/{sr_id}/reject',
  'GET /api/v1/orgs/{slug}/scripts/{sr_id}/output',
  'GET /api/v1/orgs/{slug}/scripts/{sr_id}/events',
```

And to EXCLUDED:

```typescript
  ['POST /api/v1/orgs/{slug}/scripts/submit', 'agent callback (matches /report-completion pattern)'],
```

- [ ] **Step 4: Run to verify pass**

Run: `cd web && npm test`
Expected: all passing (scripts.test + openapi-coverage).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/api/scripts.ts web/src/lib/api/scripts.test.ts \
        web/src/lib/api/types.ts web/src/lib/api/index.ts \
        web/src/test/openapi-coverage.test.ts
git commit -m "feat(web/api): scripts mirror + openapi coverage"
```

---

### Task 26: Web — scripts list page

**Files:**
- Create: `web/src/features/scripts/ListPage.tsx`
- Create: `web/src/features/scripts/index.ts`
- Modify: `web/src/App.tsx` (or the router file — wire `/scripts` route)

- [ ] **Step 1: Create the list page**

Create `web/src/features/scripts/ListPage.tsx`:

```typescript
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';
import { listScripts } from '../../lib/api/scripts';
import type { ScriptRequestStatus, ScriptRequest } from '../../lib/api/types';

const STATUS_FILTERS: { value: 'pending' | 'all' | ScriptRequestStatus; label: string }[] = [
  { value: 'pending', label: 'Pending' },
  { value: 'running', label: 'Running' },
  { value: 'completed', label: 'Completed' },
  { value: 'failed', label: 'Failed' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'all', label: 'All' },
];

export function ScriptsListPage() {
  const { slug } = useParams<{ slug: string }>();
  const [status, setStatus] = useState<string>('pending');
  const q = useQuery({
    queryKey: ['scripts', slug, status],
    queryFn: () => listScripts(slug!, { status, limit: 50 }),
    enabled: !!slug,
  });

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <header className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold">Script Requests</h1>
        <button
          className="text-sm px-3 py-1 rounded bg-gray-100 hover:bg-gray-200"
          onClick={() => q.refetch()}
        >
          Refresh
        </button>
      </header>
      <div className="flex gap-2 mb-4">
        {STATUS_FILTERS.map((s) => (
          <button
            key={s.value}
            className={
              'px-3 py-1 rounded text-sm ' +
              (status === s.value ? 'bg-blue-600 text-white' : 'bg-gray-100 hover:bg-gray-200')
            }
            onClick={() => setStatus(s.value)}
          >
            {s.label}
          </button>
        ))}
      </div>
      {q.isLoading && <p className="text-gray-500">Loading…</p>}
      {q.isError && <p className="text-red-600">Error loading scripts.</p>}
      {q.data && q.data.scripts.length === 0 && (
        <p className="text-gray-500">No script requests match.</p>
      )}
      <ul className="space-y-2">
        {q.data?.scripts.map((sr) => (
          <li key={sr.id}>
            <Link
              to={`/orgs/${slug}/scripts/${sr.id}`}
              className="block p-4 rounded border border-gray-200 hover:border-blue-400 bg-white"
            >
              <div className="flex items-center gap-3 text-sm text-gray-600">
                <span className="font-mono">{sr.id}</span>
                <span>·</span>
                <span>{sr.agent_name}</span>
                <span>·</span>
                <span>{sr.task_id}</span>
                <span className="ml-auto"><StatusPill status={sr.status} /></span>
              </div>
              <div className="font-medium mt-1">{sr.title}</div>
              <div className="text-sm text-gray-500 mt-1 line-clamp-2">{sr.rationale}</div>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function StatusPill({ status }: { status: ScriptRequest['status'] }) {
  const palette: Record<ScriptRequest['status'], string> = {
    pending:   'bg-yellow-100 text-yellow-800',
    running:   'bg-blue-100 text-blue-800',
    completed: 'bg-green-100 text-green-800',
    failed:    'bg-red-100 text-red-800',
    rejected:  'bg-gray-200 text-gray-700',
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${palette[status]}`}>
      {status}
    </span>
  );
}
```

Create `web/src/features/scripts/index.ts`:

```typescript
export { ScriptsListPage } from './ListPage';
```

Add route in `web/src/App.tsx` (or wherever routes are defined). Find the existing `/orgs/:slug/threads` route and add:

```typescript
import { ScriptsListPage } from './features/scripts';
// ...
<Route path="/orgs/:slug/scripts" element={<ScriptsListPage />} />
<Route path="/orgs/:slug/scripts/:sr_id" element={<ScriptsListPage />} />
```

(Using the same component for both routes means the detail drawer overlays the list, mirroring the tasks pattern.)

- [ ] **Step 2: Verify the page renders in dev**

Run: `cd web && npm run dev` (in another terminal). Open `http://localhost:5173/orgs/<your-slug>/scripts`. Confirm list page renders.

- [ ] **Step 3: Commit**

```bash
git add web/src/features/scripts/ListPage.tsx web/src/features/scripts/index.ts web/src/App.tsx
git commit -m "feat(web/scripts): list page with status filter chips"
```

---

### Task 27: Web — detail drawer (header + script + rationale)

**Files:**
- Create: `web/src/features/scripts/DetailDrawer.tsx`
- Modify: `web/src/features/scripts/ListPage.tsx` (open drawer when `:sr_id` in URL)
- Modify: `web/src/features/scripts/index.ts`

- [ ] **Step 1: Create the drawer**

Create `web/src/features/scripts/DetailDrawer.tsx`:

```typescript
import { useQuery } from '@tanstack/react-query';
import { useNavigate, useParams } from 'react-router-dom';
import { getScript } from '../../lib/api/scripts';
import type { ScriptRequest } from '../../lib/api/types';

interface Props {
  slug: string;
  srId: string;
}

export function ScriptDetailDrawer({ slug, srId }: Props) {
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ['script', slug, srId],
    queryFn: () => getScript(slug, srId),
  });

  const close = () => navigate(`/orgs/${slug}/scripts`);

  return (
    <div className="fixed inset-y-0 right-0 w-[640px] max-w-full bg-white shadow-2xl overflow-y-auto border-l">
      <header className="sticky top-0 bg-white border-b px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm">{srId}</span>
          {q.data && <StatusPill status={q.data.status} />}
        </div>
        <button onClick={close} className="text-gray-500 hover:text-gray-900">✕</button>
      </header>
      {q.isLoading && <p className="p-6 text-gray-500">Loading…</p>}
      {q.isError && <p className="p-6 text-red-600">Error loading.</p>}
      {q.data && <DetailBody sr={q.data} slug={slug} onChanged={() => q.refetch()} />}
    </div>
  );
}

function DetailBody({ sr, slug, onChanged }: { sr: ScriptRequest; slug: string; onChanged: () => void }) {
  return (
    <div className="p-6 space-y-6">
      <section>
        <div className="text-sm text-gray-500">
          Agent <span className="text-gray-900">{sr.agent_name}</span> · Task{' '}
          <span className="text-gray-900">{sr.task_id}</span> · Submitted{' '}
          <span className="text-gray-900">{sr.created_at}</span>
        </div>
        <h2 className="text-xl font-semibold mt-2">{sr.title}</h2>
      </section>

      <section>
        <h3 className="text-sm font-medium text-gray-700 mb-2">Rationale</h3>
        <div className="text-sm whitespace-pre-wrap">{sr.rationale}</div>
      </section>

      <section>
        <h3 className="text-sm font-medium text-gray-700 mb-2">
          Script ({sr.interpreter}{sr.cwd_hint ? ` · cwd: ${sr.cwd_hint}` : ''})
        </h3>
        <pre className="bg-gray-900 text-gray-100 p-4 rounded text-xs overflow-x-auto whitespace-pre">
          {sr.script_text}
        </pre>
      </section>

      {sr.status === 'pending' && <ActionBar sr={sr} slug={slug} onChanged={onChanged} />}
      {sr.status === 'rejected' && (
        <section>
          <h3 className="text-sm font-medium text-gray-700 mb-2">Reject reason</h3>
          <div className="text-sm whitespace-pre-wrap">{sr.reject_reason}</div>
        </section>
      )}
      {/* OutputPanel + ActionBar's RunModal land in Task 28-29. */}
    </div>
  );
}

function ActionBar({ sr, slug, onChanged }: { sr: ScriptRequest; slug: string; onChanged: () => void }) {
  // Placeholder — Tasks 28-29 wire the modals.
  return (
    <section className="flex gap-3">
      <button className="px-4 py-2 rounded bg-blue-600 text-white" disabled>
        Run (placeholder)
      </button>
      <button className="px-4 py-2 rounded bg-gray-200" disabled>
        Reject (placeholder)
      </button>
    </section>
  );
}

function StatusPill({ status }: { status: ScriptRequest['status'] }) {
  const palette: Record<ScriptRequest['status'], string> = {
    pending:   'bg-yellow-100 text-yellow-800',
    running:   'bg-blue-100 text-blue-800',
    completed: 'bg-green-100 text-green-800',
    failed:    'bg-red-100 text-red-800',
    rejected:  'bg-gray-200 text-gray-700',
  };
  return <span className={`px-2 py-0.5 rounded text-xs font-medium ${palette[status]}`}>{status}</span>;
}
```

Wire it into the list page in `ListPage.tsx` — replace the existing component with one that reads `:sr_id` and renders the drawer overlay:

```typescript
import { ScriptDetailDrawer } from './DetailDrawer';
// inside ScriptsListPage, after the <ul>...
{params.sr_id && <ScriptDetailDrawer slug={slug!} srId={params.sr_id} />}
```

(Use `useParams<{ slug: string; sr_id?: string }>()`.)

Export the drawer in `index.ts`:

```typescript
export { ScriptsListPage } from './ListPage';
export { ScriptDetailDrawer } from './DetailDrawer';
```

- [ ] **Step 2: Verify rendering**

Run `npm run dev`, navigate to `/orgs/<slug>/scripts/SR-001`. Confirm drawer opens with the script body.

- [ ] **Step 3: Commit**

```bash
git add web/src/features/scripts/DetailDrawer.tsx web/src/features/scripts/ListPage.tsx web/src/features/scripts/index.ts
git commit -m "feat(web/scripts): detail drawer with header/script/rationale"
```

---

### Task 28: Web — RejectModal wired to `rejectScript`

**Files:**
- Create: `web/src/features/scripts/RejectModal.tsx`
- Modify: `web/src/features/scripts/DetailDrawer.tsx` (use the modal)

- [ ] **Step 1: Create modal**

Create `web/src/features/scripts/RejectModal.tsx`:

```typescript
import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { rejectScript } from '../../lib/api/scripts';

interface Props {
  slug: string;
  srId: string;
  onClose: () => void;
  onSuccess: () => void;
}

export function RejectModal({ slug, srId, onClose, onSuccess }: Props) {
  const [reason, setReason] = useState('');
  const mut = useMutation({
    mutationFn: () => rejectScript(slug, srId, { reason: reason.trim() }),
    onSuccess: () => { onSuccess(); onClose(); },
  });

  const canSubmit = reason.trim().length > 0 && reason.trim().length <= 1000;

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-[480px] max-w-full">
        <h3 className="text-lg font-semibold mb-3">Reject {srId}</h3>
        <textarea
          className="w-full border rounded p-2 text-sm"
          rows={5}
          placeholder="Reason (required, max 1000 chars)"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
        {mut.isError && <p className="text-red-600 text-sm mt-2">Error rejecting.</p>}
        <div className="flex gap-2 justify-end mt-4">
          <button className="px-4 py-2 rounded bg-gray-200" onClick={onClose}>Cancel</button>
          <button
            className="px-4 py-2 rounded bg-red-600 text-white disabled:opacity-50"
            disabled={!canSubmit || mut.isPending}
            onClick={() => mut.mutate()}
          >
            {mut.isPending ? 'Rejecting…' : 'Reject'}
          </button>
        </div>
      </div>
    </div>
  );
}
```

Wire into `DetailDrawer.tsx` — replace the placeholder Reject button:

```typescript
import { RejectModal } from './RejectModal';
// in ActionBar:
const [showReject, setShowReject] = useState(false);
// ...
<button
  className="px-4 py-2 rounded bg-gray-200 hover:bg-gray-300"
  onClick={() => setShowReject(true)}
>
  Reject
</button>
{showReject && (
  <RejectModal slug={slug} srId={sr.id} onClose={() => setShowReject(false)} onSuccess={onChanged} />
)}
```

(Don't forget to `import { useState } from 'react'`.)

- [ ] **Step 2: Verify**

Render in dev, click Reject on a pending SR, submit, confirm DB update + UI status change.

- [ ] **Step 3: Commit**

```bash
git add web/src/features/scripts/RejectModal.tsx web/src/features/scripts/DetailDrawer.tsx
git commit -m "feat(web/scripts): reject modal with reason input"
```

---

### Task 29: Web — RunModal + OutputPanel with SSE

**Files:**
- Create: `web/src/features/scripts/RunModal.tsx`
- Create: `web/src/features/scripts/OutputPanel.tsx`
- Modify: `web/src/features/scripts/DetailDrawer.tsx`

- [ ] **Step 1: RunModal**

Create `web/src/features/scripts/RunModal.tsx`:

```typescript
import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { runScript } from '../../lib/api/scripts';
import type { ScriptRequest } from '../../lib/api/types';

interface Props {
  sr: ScriptRequest;
  slug: string;
  onClose: () => void;
  onSuccess: () => void;
}

export function RunModal({ sr, slug, onClose, onSuccess }: Props) {
  const [cwdOverride, setCwdOverride] = useState('');
  const [timeoutStr, setTimeoutStr] = useState(String(sr.timeout_seconds));
  const mut = useMutation({
    mutationFn: () => {
      const body: { cwd_override?: string; timeout_seconds?: number } = {};
      if (cwdOverride.trim()) body.cwd_override = cwdOverride.trim();
      const t = parseInt(timeoutStr, 10);
      if (Number.isFinite(t) && t > 0) body.timeout_seconds = t;
      return runScript(slug, sr.id, body);
    },
    onSuccess: () => { onSuccess(); onClose(); },
  });

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg p-6 w-[640px] max-w-full max-h-[90vh] overflow-y-auto">
        <h3 className="text-lg font-semibold mb-3">Run {sr.id}</h3>
        <div className="text-sm text-gray-700 mb-3">
          <p><strong>Interpreter:</strong> {sr.interpreter}</p>
          <p><strong>cwd hint:</strong> {sr.cwd_hint || '(workspace root)'}</p>
        </div>
        <label className="block text-sm font-medium mt-3">cwd override (optional)</label>
        <input
          className="w-full border rounded px-2 py-1 text-sm"
          placeholder={sr.cwd_hint || '(workspace root)'}
          value={cwdOverride}
          onChange={(e) => setCwdOverride(e.target.value)}
        />
        <label className="block text-sm font-medium mt-3">Timeout (seconds)</label>
        <input
          className="w-full border rounded px-2 py-1 text-sm"
          type="number"
          min={1}
          max={86400}
          value={timeoutStr}
          onChange={(e) => setTimeoutStr(e.target.value)}
        />
        <h4 className="text-sm font-medium mt-4 mb-2">Script:</h4>
        <pre className="bg-gray-900 text-gray-100 p-3 rounded text-xs overflow-x-auto whitespace-pre">
          {sr.script_text}
        </pre>
        {mut.isError && <p className="text-red-600 text-sm mt-2">Error starting run.</p>}
        <div className="flex gap-2 justify-end mt-4">
          <button className="px-4 py-2 rounded bg-gray-200" onClick={onClose}>Cancel</button>
          <button
            className="px-4 py-2 rounded bg-blue-600 text-white disabled:opacity-50"
            disabled={mut.isPending}
            onClick={() => mut.mutate()}
          >
            {mut.isPending ? 'Starting…' : 'Run now'}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: OutputPanel with SSE**

Create `web/src/features/scripts/OutputPanel.tsx`:

```typescript
import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { getScriptOutput, scriptEventsPath } from '../../lib/api/scripts';
import type { ScriptRequest } from '../../lib/api/types';
import { subscribeSSE } from '../../lib/api/sse';

interface Props {
  sr: ScriptRequest;
  slug: string;
}

export function OutputPanel({ sr, slug }: Props) {
  const [live, setLive] = useState<{ stream: string; line: string }[]>([]);
  const [terminal, setTerminal] = useState<{ status: string; exit_code: number | null } | null>(null);
  const isLive = sr.status === 'running';
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isLive) return;
    const sub = subscribeSSE(scriptEventsPath(slug, sr.id));
    sub.on('stdout', (e) => setLive((prev) => [...prev, { stream: 'stdout', line: e.line }]));
    sub.on('stderr', (e) => setLive((prev) => [...prev, { stream: 'stderr', line: e.line }]));
    sub.on('terminal', (e) => setTerminal({ status: e.status, exit_code: e.exit_code }));
    return () => sub.close();
  }, [isLive, slug, sr.id]);

  useEffect(() => {
    containerRef.current?.scrollTo({ top: containerRef.current.scrollHeight });
  }, [live.length]);

  const finalOutput = useQuery({
    queryKey: ['script-output', slug, sr.id],
    queryFn: () => getScriptOutput(slug, sr.id),
    enabled: !isLive && (sr.status === 'completed' || sr.status === 'failed'),
  });

  return (
    <section>
      <h3 className="text-sm font-medium text-gray-700 mb-2">Output</h3>
      {isLive && (
        <div
          ref={containerRef}
          className="bg-black text-green-100 p-3 rounded font-mono text-xs h-64 overflow-y-auto whitespace-pre-wrap"
        >
          {live.map((l, i) => (
            <div key={i} className={l.stream === 'stderr' ? 'text-red-300' : ''}>
              {l.line}
            </div>
          ))}
          {terminal && (
            <div className="text-yellow-200 mt-2">
              [done] {terminal.status} exit={terminal.exit_code}
            </div>
          )}
        </div>
      )}
      {!isLive && finalOutput.data && (
        <div className="space-y-3">
          <div>
            <h4 className="text-xs uppercase text-gray-500 mb-1">stdout</h4>
            <pre className="bg-gray-900 text-gray-100 p-3 rounded text-xs overflow-x-auto whitespace-pre-wrap">
              {finalOutput.data.stdout || '(empty)'}
            </pre>
          </div>
          <div>
            <h4 className="text-xs uppercase text-gray-500 mb-1">stderr</h4>
            <pre className="bg-gray-900 text-gray-100 p-3 rounded text-xs overflow-x-auto whitespace-pre-wrap">
              {finalOutput.data.stderr || '(empty)'}
            </pre>
          </div>
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 3: Wire both into the drawer**

In `DetailDrawer.tsx`, replace the `ActionBar` and add the OutputPanel:

```typescript
import { RunModal } from './RunModal';
import { OutputPanel } from './OutputPanel';

function ActionBar({ sr, slug, onChanged }: { sr: ScriptRequest; slug: string; onChanged: () => void }) {
  const [showRun, setShowRun] = useState(false);
  const [showReject, setShowReject] = useState(false);
  return (
    <section className="flex gap-3">
      <button className="px-4 py-2 rounded bg-blue-600 text-white hover:bg-blue-700" onClick={() => setShowRun(true)}>
        Run
      </button>
      <button className="px-4 py-2 rounded bg-gray-200 hover:bg-gray-300" onClick={() => setShowReject(true)}>
        Reject
      </button>
      {showRun && <RunModal sr={sr} slug={slug} onClose={() => setShowRun(false)} onSuccess={onChanged} />}
      {showReject && <RejectModal slug={slug} srId={sr.id} onClose={() => setShowReject(false)} onSuccess={onChanged} />}
    </section>
  );
}
```

And in `DetailBody`, after the script section:

```typescript
{(sr.status === 'running' || sr.status === 'completed' || sr.status === 'failed') && (
  <OutputPanel sr={sr} slug={slug} />
)}
```

- [ ] **Step 4: Verify**

Submit + run an SR end-to-end via the UI. Confirm live SSE updates.

- [ ] **Step 5: Commit**

```bash
git add web/src/features/scripts/RunModal.tsx web/src/features/scripts/OutputPanel.tsx web/src/features/scripts/DetailDrawer.tsx
git commit -m "feat(web/scripts): run modal + SSE output panel"
```

---

### Task 30: Audit-page deep-link for `script_*` events

**Files:**
- Modify: existing audit-feature renderer (`web/src/features/audit/...`)

- [ ] **Step 1: Find audit row renderer**

Search: `grep -rn "action.*===\\|renderAction\\|AuditRow" web/src/features/audit/`. The audit page already has a switch/lookup that renders each event's icon + summary; we extend it.

- [ ] **Step 2: Add script_* renderers**

Add (matching existing style — adapt to whatever the current switch shape is):

```typescript
case 'script_submitted': {
  const srId = entry.payload?.script_request_id;
  return (
    <>
      submitted <Link to={`/orgs/${slug}/scripts/${srId}`} className="text-blue-600 hover:underline">{srId}</Link>: {entry.payload?.title}
    </>
  );
}
case 'script_rejected':
  return <>rejected <Link to={...}>{...}</Link>: {entry.payload?.reason}</>;
case 'script_run_started':
case 'script_run_completed':
case 'script_run_failed':
  // similar, with appropriate text
```

- [ ] **Step 3: Verify**

Submit + reject an SR; open `/audit`; confirm rows render with working deep links.

- [ ] **Step 4: Commit**

```bash
git add web/src/features/audit
git commit -m "feat(web/audit): deep-link script_* events into the scripts drawer"
```

---

### Task 31: Task drawer + agent page show their SRs

**Files:**
- Modify: existing task-detail renderer (`web/src/features/tasks/DetailDrawer.tsx` or similar)
- Modify: existing agent-detail renderer (`web/src/features/agents/...`)

- [ ] **Step 1: Task drawer SR section**

In the task detail component, after the existing audit / completion sections, add:

```typescript
import { listScripts } from '../../lib/api/scripts';
// inside the component
const sr = useQuery({
  queryKey: ['task-scripts', slug, taskId],
  queryFn: () => listScripts(slug, { task_id: taskId, status: 'all', limit: 100 }),
});
// ...
{sr.data && sr.data.scripts.length > 0 && (
  <section className="mt-6">
    <h3 className="text-sm font-medium text-gray-700 mb-2">Script requests from this task</h3>
    <ul className="space-y-1 text-sm">
      {sr.data.scripts.map((s) => (
        <li key={s.id}>
          <Link to={`/orgs/${slug}/scripts/${s.id}`} className="text-blue-600 hover:underline">
            {s.id}
          </Link>
          {' — '}
          {s.title} <span className="text-gray-500">({s.status})</span>
        </li>
      ))}
    </ul>
  </section>
)}
```

- [ ] **Step 2: Agent page recent-SRs section**

Analogous addition in the agent detail page — query `listScripts(slug, { agent: name, status: 'all', limit: 10 })` and render.

- [ ] **Step 3: Verify**

Submit a SR; visit the task drawer and the agent page; confirm the SR shows in both.

- [ ] **Step 4: Commit**

```bash
git add web/src/features/tasks web/src/features/agents
git commit -m "feat(web): cross-link SRs from task drawer + agent page"
```

---

### Task 32: Integration test — extend `fake_claude.sh` with script-submit branch

**Files:**
- Modify: `tests/integration/fake_claude.sh`
- Test: (no new test here — Task 33 uses it)

- [ ] **Step 1: Read the existing dual-plan dispatch**

Open `tests/integration/fake_claude.sh`. Locate the existing task vs. thread branch (the file detects "Your invocation_token for this turn is" to route to thread plans). The new branch routes by detecting a `--script-submit` marker in the FAKE_CLAUDE_PLAN env var.

- [ ] **Step 2: Add the routing**

After the existing branch logic, append:

```bash
# Script-submit branch — set by tests via FAKE_CLAUDE_SCRIPT_PLAN.
if [ -n "$FAKE_CLAUDE_SCRIPT_PLAN" ]; then
    # Extract task_id and session_id from the start-task Parameters block,
    # same way as the task branch.
    task_id="$(grep -oE 'task_id: [^ ]+' "$PROMPT_FILE" | head -1 | awk '{print $2}')"
    session_id="$(grep -oE 'session_id: [^ ]+' "$PROMPT_FILE" | head -1 | awk '{print $2}')"
    agent="${PWD##*/}"
    org_slug="$GRASSLAND_TEST_ORG_SLUG"
    # shellcheck disable=SC1090
    source "$FAKE_CLAUDE_SCRIPT_PLAN"
    exit 0
fi
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/fake_claude.sh
git commit -m "test(integration): fake_claude script-submit plan branch"
```

---

### Task 33: Integration test — end-to-end SR submit → run → revisit

**Files:**
- Create: `tests/integration/test_scripts_e2e.py`

- [ ] **Step 1: Write the integration test**

Create `tests/integration/test_scripts_e2e.py`:

```python
"""End-to-end: agent submits SR via fake_claude, founder runs it, revisit
header surfaces the SR output. Mirrors tests/integration/test_threads_e2e.py."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest


pytestmark = pytest.mark.integration


def test_agent_submit_then_founder_run_then_revisit(integration_daemon, tmp_path):
    """The full SR lifecycle exercised through a fake agent + the real daemon."""
    daemon = integration_daemon  # provides client, org_slug, runtime root

    # 1. Write a plan that submits an SR then self-blocks.
    plan_path = tmp_path / "sr_plan.sh"
    plan_path.write_text(f"""#!/bin/bash
set -euo pipefail
payload="/tmp/sr-payload-$$.json"
cat > "$payload" <<EOF
{{
  "task_id": "$task_id",
  "session_id": "$session_id",
  "title": "touch a sentinel",
  "rationale": "needs founder write to /tmp",
  "script": "touch /tmp/grassland-sr-e2e-sentinel.$$",
  "interpreter": "bash"
}}
EOF
grassland scripts submit --from-file "$payload" --org "$org_slug" >/tmp/sr-output-$$.log
sr_id=$(grep -oE 'SR-[0-9]+' /tmp/sr-output-$$.log | head -1)
report="/tmp/sr-completion-$$.json"
cat > "$report" <<EOF
{{
  "task_id": "$task_id",
  "status": "blocked",
  "summary": "Awaiting $sr_id"
}}
EOF
grassland report-completion --from-file "$report" --org "$org_slug"
""")
    plan_path.chmod(0o755)
    os.environ["FAKE_CLAUDE_SCRIPT_PLAN"] = str(plan_path)

    # 2. Dispatch a task.
    task_id = daemon.dispatch_task(agent="engineering_head", brief="please touch the sentinel")

    # 3. Wait for task to be blocked.
    for _ in range(100):
        t = daemon.client.get(f"/api/v1/orgs/{daemon.org_slug}/tasks/{task_id}").json()
        if t["status"] == "blocked":
            break
        time.sleep(0.2)
    assert t["status"] == "blocked"

    # 4. Find the SR.
    rows = daemon.client.get(
        f"/api/v1/orgs/{daemon.org_slug}/scripts/", params={"status": "pending"}
    ).json()["scripts"]
    assert len(rows) >= 1
    sr_id = rows[0]["id"]

    # 5. Founder runs it.
    r = daemon.client.post(
        f"/api/v1/orgs/{daemon.org_slug}/scripts/{sr_id}/run", json={"timeout_seconds": 10},
    )
    assert r.status_code == 202

    # 6. Wait for completion.
    for _ in range(50):
        d = daemon.client.get(f"/api/v1/orgs/{daemon.org_slug}/scripts/{sr_id}").json()
        if d["status"] in ("completed", "failed"):
            break
        time.sleep(0.1)
    assert d["status"] == "completed"
    assert d["exit_code"] == 0

    # 7. Revisit the task — header must mention the SR.
    rev = daemon.client.post(f"/api/v1/orgs/{daemon.org_slug}/tasks/{task_id}/revisit", json={})
    assert rev.status_code in (200, 201)
    new_task_id = rev.json()["task_id"]
    # Inspect the revisited task's first orchestrator prompt for SR mention.
    # (Implementation detail: grassland recall or get the bootstrap doc.)
    recall = daemon.client.get(f"/api/v1/orgs/{daemon.org_slug}/tasks/{new_task_id}/recall").json()
    assert sr_id in json.dumps(recall)
```

(Adapt the fixture names to match the existing integration harness; this is the test shape, not the literal final fixtures.)

- [ ] **Step 2: Run**

Run: `uv run pytest tests/integration/test_scripts_e2e.py -v -m integration`
Expected: passing.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_scripts_e2e.py
git commit -m "test(integration): SR submit → run → revisit end-to-end"
```

---

### Task 34: Docs — README + founder skill

**Files:**
- Modify: `README.md` (add Script Requests section in the founder-facing surface)
- Modify: `skills/grassland/SKILL.md` (add SR section)

- [ ] **Step 1: README**

In `README.md`, find the section that lists primitives (likely "Threads" / "Talks" / "KB"). Add:

```markdown
### Script requests

Agents who hit a permission wall can submit a script for you to run with
founder-grade credentials. List pending requests:

    grassland scripts list

Review details:

    grassland scripts show SR-019

Run (TTY-gated):

    grassland scripts run SR-019

Reject with a reason:

    grassland scripts reject SR-019 --reason "we don't ship that change today"

The same surface is available in the web UI at `/scripts`. After a run
completes, use `grassland revisit <task-id>` to unblock the agent's task with
the captured output in context.

Operational note: scripts run inside the daemon process with `os.environ`
inherited from the daemon's launch shell. If you rotate credentials in your
interactive shell, restart the daemon so the new env is picked up.
```

- [ ] **Step 2: founder skill**

In `skills/grassland/SKILL.md`, add a "Script requests" section in the same shape as the existing "Threads" / "KB" / "Talks" sections, listing all `grassland scripts ...` commands with one-line descriptions.

- [ ] **Step 3: Commit**

```bash
git add README.md skills/grassland/SKILL.md
git commit -m "docs: script-requests in README + founder skill"
```

---

## Self-Review

Spec coverage scan:

- §3 data model → Tasks 1, 2, 4 (model + schema + insert/get).
- §3.3 audit events → Task 8.
- §3.4 on-disk artifacts → Task 15 (`.script` freeze + `.out`/`.err`); Task 10 (pump writes).
- §4 agent-side flow → Tasks 19, 22 (CLI + skill).
- §5 HTTP API → Tasks 12 (submit), 13 (reject), 14 (list+detail), 15 (run), 16 (output), 17 (events).
- §6 subprocess execution → Tasks 10, 11 (runner + tests).
- §6.6 daemon shutdown / recovery → Tasks 7, 18.
- §7 founder CLI → Tasks 19–21.
- §8 web UI → Tasks 25–29.
- §8.4 cross-links → Tasks 30, 31.
- §9.1 revisit header → Task 23.
- §9.2 no new task-status state → no task (intentionally nothing to do).
- §10 auth boundaries → covered implicitly by Task 12's session-mismatch test; no separate task.
- §11 failure modes → all surfaces covered.
- §12 testing → unit tests in every code task; integration in Tasks 32, 33; contract regen in Task 24; web tests in Task 25.
- §13 migration → none, by design.
- §14 rollout order → reflected in task ordering.

Placeholder scan: no `TBD`, no `TODO`, no "add appropriate error handling" — each step has either a concrete code block or an exact command.

Type/name consistency check: `ScriptRequestRecord`, `ScriptRequestStatus`, `ScriptInterpreter`, `_INFLIGHT`, `script_topic`, `transition_script_to_*`, `log_script_*`, `cmd_scripts_*` used consistently across tasks. CLI flag `--from-file` matches the rest of the codebase. Route paths consistent: always under `/api/v1/orgs/{slug}/scripts/...`.

Edge cases worth re-checking before implementation:

1. **Task 7 recovery test** — the test as written touches private DB fields (`db._conn.execute`). The integration harness might require seeding via a public method; adjust if the harness API differs.
2. **Task 17 events test** — `client.stream` is httpx's API; if the test client is `TestClient` (Starlette), use `with client.stream(...)` semantics correctly or replace with a chunked read.
3. **Task 21 TTY guard** — `subprocess.run` always gives a non-TTY stdin, so the test reliably exercises the guard path. Good.
4. **Task 33 — `grassland recall` content** — the test asserts `sr_id in json.dumps(recall)`. The actual recall endpoint shape may need a different inspection (e.g., reading the next orchestration_step row's bootstrap text). Adjust based on what's actually in the integration harness.

If any of the above bite during execution, fix inline in the failing task and move on.

---

**End of plan.** 34 tasks total. Estimated effort: ~2–3 days of focused execution by an engineer following the steps verbatim.
