# Product & Engineering Crew Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a working Python orchestrator that spawns Claude Code agent sessions for the Product & Engineering Crew (Engineering Head, Product Manager, Dev Agent, Payment Agent), with audit logging, revision loop, agent memory, and performance scoring.

**Architecture:** A Python CLI application that manages task lifecycle via SQLite. For each task step, it assembles context into an agent's workspace, spawns `claude -p` with `--permission-mode auto`, reads the completion report, scores the agent, and routes output to the next step or back for revision. All agents run as isolated Claude Code sessions from persistent workspace directories.

**Tech Stack:** Python 3.11+, Pydantic v2 (structured data), SQLite (persistence), subprocess (Claude Code executor), pytest (testing). No CrewAI needed yet -- the orchestrator handles task routing directly.

**Spec:** `docs/superpowers/specs/2026-04-12-product-engineering-crew-design.md` (the source of truth for all requirements).

---

## File Structure

```
src/
  __init__.py
  config.py                   Settings: LLM config, thresholds, paths
  models.py                   Shared Pydantic models: enums, TaskRecord, CompletionReport, etc.
  infrastructure/
    __init__.py
    database.py               SQLite setup + typed CRUD for 4 tables
    audit_logger.py           Structured logging to audit_log + task_results tables
  orchestrator/
    __init__.py
    task_router.py            Pure logic: task type + agent tiers -> ordered step chain
    revision_loop.py          Pure logic: review verdict + revision count -> next action
    performance_tracker.py    Score agents, calculate tiers, write scorecard files
    context_builder.py        Generate CLAUDE.md + .claude/settings.json per agent workspace
    executor.py               Spawn claude -p sessions, read completion_report.json
    orchestrator.py           Main loop: create task, build chain, run steps, handle review
tests/
  __init__.py
  conftest.py                 Shared fixtures: tmp DB, tmp workspaces
  test_models.py              Model validation tests
  test_database.py            CRUD tests against real SQLite
  test_audit_logger.py        Audit logging tests
  test_task_router.py         Task chain generation tests
  test_revision_loop.py       Revision logic tests
  test_performance_tracker.py Scoring + tier calculation tests
  test_context_builder.py     Workspace file generation tests
  test_executor.py            Executor tests (mocked subprocess)
  test_orchestrator.py        Integration tests (mocked executor)
scripts/
  run_product_crew.py         CLI entry point: argparse -> orchestrator
```

Each file has a single responsibility. Files that change together (e.g., models used by database) are co-located in the same package.

---

## Task 1: Project Setup and Shared Models

**Files:**
- Create: `pyproject.toml`
- Create: `src/__init__.py`
- Create: `src/config.py`
- Create: `src/models.py`
- Create: `src/infrastructure/__init__.py`
- Create: `src/orchestrator/__init__.py`
- Create: `src/agents/__init__.py`
- Create: `src/crews/__init__.py`
- Create: `src/tools/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_models.py`

This task creates the project skeleton and the shared types that every other module depends on.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "opc-org"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-asyncio",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

Note: `crewai` and `anthropic` dependencies are not needed yet. The orchestrator spawns `claude` as a subprocess and doesn't use the Anthropic Python SDK directly. Add them later when needed.

- [ ] **Step 2: Create empty `__init__.py` files**

Create these files, all empty:
- `src/__init__.py`
- `src/infrastructure/__init__.py`
- `src/orchestrator/__init__.py`
- `src/agents/__init__.py`
- `src/crews/__init__.py`
- `src/tools/__init__.py`
- `tests/__init__.py`

- [ ] **Step 3: Create `src/config.py`**

```python
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="OPC_",
        env_file=".env",
        extra="ignore",
    )

    # Project root (resolved at import time)
    project_root: Path = Field(
        default_factory=lambda: Path(__file__).resolve().parent.parent
    )

    # Claude Code executor
    claude_cli_path: str = "claude"
    permission_mode: str = "auto"

    # SQLite (relative to project_root)
    db_path: str = "opc.db"

    # Agent workspaces (relative to project_root)
    workspaces_dir: str = "workspaces"

    # Task constraints
    max_revision_rounds: int = 2
    session_timeout_seconds: int = 1800  # 30 minutes

    # Performance tier thresholds
    tier_green_threshold: float = 0.90
    tier_yellow_threshold: float = 0.75

    def get_db_path(self) -> Path:
        return self.project_root / self.db_path

    def get_workspaces_dir(self) -> Path:
        return self.project_root / self.workspaces_dir


settings = Settings()
```

- [ ] **Step 4: Write the failing test for models**

Create `tests/test_models.py`:

```python
from src.models import (
    AgentName,
    CompletionReport,
    PerformanceTier,
    ReviewVerdict,
    TaskRecord,
    TaskStatus,
    TaskStep,
    TaskType,
)


def test_task_status_values():
    assert TaskStatus.PENDING == "pending"
    assert TaskStatus.IN_PROGRESS == "in_progress"
    assert TaskStatus.COMPLETED == "completed"
    assert TaskStatus.IN_REVIEW == "in_review"
    assert TaskStatus.APPROVED == "approved"
    assert TaskStatus.REJECTED == "rejected"
    assert TaskStatus.ESCALATED == "escalated"


def test_task_type_values():
    assert TaskType.IMPLEMENT_FEATURE == "implement_feature"
    assert TaskType.BUG_FIX == "bug_fix"
    assert TaskType.PAYMENT_CHANGE == "payment_change"


def test_agent_name_values():
    assert AgentName.ENGINEERING_HEAD == "engineering_head"
    assert AgentName.PRODUCT_MANAGER == "product_manager"
    assert AgentName.DEV_AGENT == "dev_agent"
    assert AgentName.PAYMENT_AGENT == "payment_agent"


def test_performance_tier_values():
    assert PerformanceTier.GREEN == "green"
    assert PerformanceTier.YELLOW == "yellow"
    assert PerformanceTier.RED == "red"


def test_review_verdict_values():
    assert ReviewVerdict.APPROVE == "approve"
    assert ReviewVerdict.REVISE == "revise"
    assert ReviewVerdict.REJECT == "reject"


def test_task_record_creation():
    record = TaskRecord(
        id="TASK-001",
        type=TaskType.IMPLEMENT_FEATURE,
        brief="Add Alipay support",
    )
    assert record.status == TaskStatus.PENDING
    assert record.revision_count == 0
    assert record.assigned_agent is None
    assert record.crew == "product_engineering"
    assert record.completed_at is None
    assert record.created_at is not None
    assert record.updated_at is not None


def test_completion_report_creation():
    report = CompletionReport(
        task_id="TASK-001",
        agent="dev_agent",
        status="completed",
        confidence=85,
        output_summary="Implemented Alipay payment integration",
        risks_flagged=["Alipay sandbox differs from production"],
        dependencies=["Payment Agent gateway config"],
        suggested_reviewer_focus=["Error handling for failed callbacks"],
    )
    assert report.confidence == 85
    assert len(report.risks_flagged) == 1


def test_completion_report_rejects_invalid_confidence():
    import pytest

    with pytest.raises(Exception):
        CompletionReport(
            task_id="TASK-001",
            agent="dev_agent",
            status="completed",
            confidence=150,  # invalid: above 100
            output_summary="test",
        )


def test_task_step_creation():
    step = TaskStep(
        agent=AgentName.PRODUCT_MANAGER,
        action="write_spec",
        description="Write feature specification",
    )
    assert step.agent == AgentName.PRODUCT_MANAGER
    assert step.action == "write_spec"
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.models'`

- [ ] **Step 6: Create `src/models.py`**

```python
from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class TaskType(StrEnum):
    IMPLEMENT_FEATURE = "implement_feature"
    BUG_FIX = "bug_fix"
    PAYMENT_CHANGE = "payment_change"


class AgentName(StrEnum):
    ENGINEERING_HEAD = "engineering_head"
    PRODUCT_MANAGER = "product_manager"
    DEV_AGENT = "dev_agent"
    PAYMENT_AGENT = "payment_agent"


class PerformanceTier(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class ReviewVerdict(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskRecord(BaseModel):
    id: str
    type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    assigned_agent: str | None = None
    crew: str = "product_engineering"
    brief: str
    revision_count: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    completed_at: datetime | None = None


class CompletionReport(BaseModel):
    task_id: str
    agent: str
    status: str
    confidence: int = Field(ge=0, le=100)
    output_summary: str
    risks_flagged: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    suggested_reviewer_focus: list[str] = Field(default_factory=list)


class TaskStep(BaseModel):
    agent: AgentName
    action: str
    description: str
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_models.py -v`
Expected: All 8 tests PASS

- [ ] **Step 8: Create `tests/conftest.py`**

```python
import os
import tempfile
from pathlib import Path

import pytest

from src.config import Settings


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def test_settings(tmp_dir: Path) -> Settings:
    """Settings that use temporary directories for DB and workspaces."""
    return Settings(
        project_root=tmp_dir,
        db_path="test.db",
        workspaces_dir="workspaces",
    )
```

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml src/ tests/
git commit -m "feat: project setup with shared Pydantic models and config"
```

---

## Task 2: Database Layer

**Files:**
- Create: `src/infrastructure/database.py`
- Create: `tests/test_database.py`

The database module manages a single SQLite file with 4 tables: `tasks`, `audit_log`, `scorecards`, `task_results`. It provides typed CRUD helpers that accept and return Pydantic models.

- [ ] **Step 1: Write failing tests for database**

Create `tests/test_database.py`:

```python
from src.infrastructure.database import Database
from src.models import TaskRecord, TaskStatus, TaskType


def test_init_creates_tables(test_settings):
    db = Database(test_settings.get_db_path())
    # Verify all 4 tables exist
    tables = db.list_tables()
    assert "tasks" in tables
    assert "audit_log" in tables
    assert "scorecards" in tables
    assert "task_results" in tables


def test_insert_and_get_task(test_settings):
    db = Database(test_settings.get_db_path())
    task = TaskRecord(
        id="TASK-001",
        type=TaskType.IMPLEMENT_FEATURE,
        brief="Add Alipay support",
    )
    db.insert_task(task)
    retrieved = db.get_task("TASK-001")
    assert retrieved is not None
    assert retrieved.id == "TASK-001"
    assert retrieved.type == TaskType.IMPLEMENT_FEATURE
    assert retrieved.brief == "Add Alipay support"
    assert retrieved.status == TaskStatus.PENDING


def test_get_nonexistent_task_returns_none(test_settings):
    db = Database(test_settings.get_db_path())
    assert db.get_task("TASK-999") is None


def test_update_task_status(test_settings):
    db = Database(test_settings.get_db_path())
    task = TaskRecord(
        id="TASK-002",
        type=TaskType.BUG_FIX,
        brief="Fix broken links",
    )
    db.insert_task(task)
    db.update_task("TASK-002", status=TaskStatus.IN_PROGRESS, assigned_agent="dev_agent")
    retrieved = db.get_task("TASK-002")
    assert retrieved.status == TaskStatus.IN_PROGRESS
    assert retrieved.assigned_agent == "dev_agent"


def test_increment_revision_count(test_settings):
    db = Database(test_settings.get_db_path())
    task = TaskRecord(
        id="TASK-003",
        type=TaskType.IMPLEMENT_FEATURE,
        brief="Refactor auth",
    )
    db.insert_task(task)
    db.increment_revision_count("TASK-003")
    retrieved = db.get_task("TASK-003")
    assert retrieved.revision_count == 1
    db.increment_revision_count("TASK-003")
    retrieved = db.get_task("TASK-003")
    assert retrieved.revision_count == 2


def test_insert_audit_log(test_settings):
    db = Database(test_settings.get_db_path())
    db.insert_audit_log(
        task_id="TASK-001",
        agent="dev_agent",
        action="session_start",
        payload={"workspace": "/tmp/dev_agent"},
    )
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["agent"] == "dev_agent"
    assert logs[0]["action"] == "session_start"


def test_insert_task_result(test_settings):
    db = Database(test_settings.get_db_path())
    db.insert_task_result(
        task_id="TASK-001",
        agent="dev_agent",
        session_id="sess-abc",
        output_summary="Implemented feature",
        confidence_score=85,
        risks_flagged=["sandbox mismatch"],
        duration_seconds=120,
        token_count=5000,
        estimated_cost=0.15,
    )
    results = db.get_task_results("TASK-001")
    assert len(results) == 1
    assert results[0]["confidence_score"] == 85
    assert results[0]["duration_seconds"] == 120


def test_insert_and_get_scorecard(test_settings):
    db = Database(test_settings.get_db_path())
    db.upsert_scorecard(
        agent="dev_agent",
        period_start="2026-03-13T00:00:00Z",
        period_end="2026-04-13T00:00:00Z",
        acceptance_rate=0.92,
        revision_rate=0.08,
        error_count=1,
        tier="green",
    )
    scorecard = db.get_scorecard("dev_agent")
    assert scorecard is not None
    assert scorecard["acceptance_rate"] == 0.92
    assert scorecard["tier"] == "green"


def test_upsert_scorecard_updates_existing(test_settings):
    db = Database(test_settings.get_db_path())
    db.upsert_scorecard(
        agent="dev_agent",
        period_start="2026-03-13T00:00:00Z",
        period_end="2026-04-13T00:00:00Z",
        acceptance_rate=0.92,
        revision_rate=0.08,
        error_count=1,
        tier="green",
    )
    db.upsert_scorecard(
        agent="dev_agent",
        period_start="2026-03-13T00:00:00Z",
        period_end="2026-04-13T00:00:00Z",
        acceptance_rate=0.70,
        revision_rate=0.30,
        error_count=5,
        tier="yellow",
    )
    scorecard = db.get_scorecard("dev_agent")
    assert scorecard["acceptance_rate"] == 0.70
    assert scorecard["tier"] == "yellow"


def test_next_task_id(test_settings):
    db = Database(test_settings.get_db_path())
    assert db.next_task_id() == "TASK-001"
    task = TaskRecord(id="TASK-001", type=TaskType.BUG_FIX, brief="test")
    db.insert_task(task)
    assert db.next_task_id() == "TASK-002"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_database.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.infrastructure.database'`

- [ ] **Step 3: Implement `src/infrastructure/database.py`**

```python
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.models import TaskRecord, TaskStatus


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                assigned_agent TEXT,
                crew TEXT NOT NULL DEFAULT 'product_engineering',
                brief TEXT NOT NULL,
                revision_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                action TEXT NOT NULL,
                payload TEXT,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scorecards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL UNIQUE,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                acceptance_rate REAL NOT NULL,
                revision_rate REAL NOT NULL,
                error_count INTEGER NOT NULL,
                tier TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                session_id TEXT NOT NULL,
                output_summary TEXT,
                confidence_score INTEGER,
                learnings TEXT,
                risks_flagged TEXT,
                duration_seconds INTEGER,
                token_count INTEGER,
                estimated_cost REAL,
                created_at TEXT NOT NULL
            );
        """)

    def list_tables(self) -> list[str]:
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return [row["name"] for row in cursor.fetchall()]

    # --- Tasks ---

    def insert_task(self, task: TaskRecord) -> None:
        self._conn.execute(
            """INSERT INTO tasks (id, type, status, assigned_agent, crew, brief,
               revision_count, created_at, updated_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.id,
                task.type.value,
                task.status.value,
                task.assigned_agent,
                task.crew,
                task.brief,
                task.revision_count,
                task.created_at.isoformat(),
                task.updated_at.isoformat(),
                task.completed_at.isoformat() if task.completed_at else None,
            ),
        )
        self._conn.commit()

    def get_task(self, task_id: str) -> TaskRecord | None:
        cursor = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return TaskRecord(
            id=row["id"],
            type=row["type"],
            status=row["status"],
            assigned_agent=row["assigned_agent"],
            crew=row["crew"],
            brief=row["brief"],
            revision_count=row["revision_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )

    def update_task(self, task_id: str, **fields: object) -> None:
        allowed = {"status", "assigned_agent", "revision_count", "completed_at"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return
        # Always update updated_at
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        # Convert enums to values
        for k, v in updates.items():
            if hasattr(v, "value"):
                updates[k] = v.value
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]
        self._conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        self._conn.commit()

    def increment_revision_count(self, task_id: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET revision_count = revision_count + 1, updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), task_id),
        )
        self._conn.commit()

    def next_task_id(self) -> str:
        cursor = self._conn.execute("SELECT COUNT(*) as cnt FROM tasks")
        count = cursor.fetchone()["cnt"]
        return f"TASK-{count + 1:03d}"

    # --- Audit Log ---

    def insert_audit_log(
        self,
        task_id: str,
        agent: str,
        action: str,
        payload: dict | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) VALUES (?, ?, ?, ?, ?)",
            (
                task_id,
                agent,
                action,
                json.dumps(payload) if payload else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def get_audit_logs(self, task_id: str) -> list[dict]:
        cursor = self._conn.execute(
            "SELECT * FROM audit_log WHERE task_id = ? ORDER BY id", (task_id,)
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("payload"):
                d["payload"] = json.loads(d["payload"])
            result.append(d)
        return result

    # --- Task Results ---

    def insert_task_result(
        self,
        task_id: str,
        agent: str,
        session_id: str,
        output_summary: str,
        confidence_score: int,
        risks_flagged: list[str] | None = None,
        learnings: str | None = None,
        duration_seconds: int | None = None,
        token_count: int | None = None,
        estimated_cost: float | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO task_results
               (task_id, agent, session_id, output_summary, confidence_score,
                learnings, risks_flagged, duration_seconds, token_count, estimated_cost, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                agent,
                session_id,
                output_summary,
                confidence_score,
                learnings,
                json.dumps(risks_flagged) if risks_flagged else None,
                duration_seconds,
                token_count,
                estimated_cost,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def get_task_results(self, task_id: str) -> list[dict]:
        cursor = self._conn.execute(
            "SELECT * FROM task_results WHERE task_id = ? ORDER BY id", (task_id,)
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("risks_flagged"):
                d["risks_flagged"] = json.loads(d["risks_flagged"])
            result.append(d)
        return result

    def get_agent_task_results(self, agent: str, since: str | None = None) -> list[dict]:
        """Get all task results for an agent, optionally since a date (ISO 8601)."""
        if since:
            cursor = self._conn.execute(
                "SELECT * FROM task_results WHERE agent = ? AND created_at >= ? ORDER BY id",
                (agent, since),
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM task_results WHERE agent = ? ORDER BY id", (agent,)
            )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("risks_flagged"):
                d["risks_flagged"] = json.loads(d["risks_flagged"])
            result.append(d)
        return result

    # --- Scorecards ---

    def upsert_scorecard(
        self,
        agent: str,
        period_start: str,
        period_end: str,
        acceptance_rate: float,
        revision_rate: float,
        error_count: int,
        tier: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO scorecards (agent, period_start, period_end, acceptance_rate,
               revision_rate, error_count, tier, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(agent) DO UPDATE SET
               period_start=excluded.period_start, period_end=excluded.period_end,
               acceptance_rate=excluded.acceptance_rate, revision_rate=excluded.revision_rate,
               error_count=excluded.error_count, tier=excluded.tier, updated_at=excluded.updated_at""",
            (agent, period_start, period_end, acceptance_rate, revision_rate, error_count, tier, now),
        )
        self._conn.commit()

    def get_scorecard(self, agent: str) -> dict | None:
        cursor = self._conn.execute("SELECT * FROM scorecards WHERE agent = ?", (agent,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_database.py -v`
Expected: All 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "feat: SQLite database layer with typed CRUD for 4 tables"
```

---

## Task 3: Audit Logger

**Files:**
- Create: `src/infrastructure/audit_logger.py`
- Create: `tests/test_audit_logger.py`

A thin wrapper around the Database that provides a semantic API for logging audit events and task results.

- [ ] **Step 1: Write failing tests**

Create `tests/test_audit_logger.py`:

```python
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import CompletionReport


def test_log_session_start(test_settings):
    db = Database(test_settings.get_db_path())
    logger = AuditLogger(db)
    logger.log_session_start("TASK-001", "dev_agent", "/tmp/workspace")
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "session_start"
    assert logs[0]["payload"]["workspace"] == "/tmp/workspace"


def test_log_session_end(test_settings):
    db = Database(test_settings.get_db_path())
    logger = AuditLogger(db)
    logger.log_session_end("TASK-001", "dev_agent", duration_seconds=120, token_count=5000)
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "session_end"
    assert logs[0]["payload"]["duration_seconds"] == 120


def test_log_completion_report(test_settings):
    db = Database(test_settings.get_db_path())
    logger = AuditLogger(db)
    report = CompletionReport(
        task_id="TASK-001",
        agent="dev_agent",
        status="completed",
        confidence=85,
        output_summary="Implemented feature",
        risks_flagged=["sandbox mismatch"],
    )
    logger.log_completion_report(report, session_id="sess-abc", duration_seconds=120, token_count=5000, estimated_cost=0.15)
    # Check audit_log entry
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "completion_report"
    # Check task_results entry
    results = db.get_task_results("TASK-001")
    assert len(results) == 1
    assert results[0]["confidence_score"] == 85
    assert results[0]["duration_seconds"] == 120


def test_log_review_verdict(test_settings):
    db = Database(test_settings.get_db_path())
    logger = AuditLogger(db)
    logger.log_review_verdict(
        task_id="TASK-001",
        reviewer="engineering_head",
        verdict="approve",
        feedback=None,
        reviewed_agent="dev_agent",
    )
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "review_verdict"
    assert logs[0]["payload"]["verdict"] == "approve"
    assert logs[0]["payload"]["reviewed_agent"] == "dev_agent"


def test_log_escalation(test_settings):
    db = Database(test_settings.get_db_path())
    logger = AuditLogger(db)
    logger.log_escalation(
        task_id="TASK-001",
        agent="dev_agent",
        reason="Max revision rounds exceeded",
    )
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "escalation"
    assert "Max revision" in logs[0]["payload"]["reason"]


def test_log_cross_audit_stub(test_settings):
    db = Database(test_settings.get_db_path())
    logger = AuditLogger(db)
    logger.log_cross_audit_stub("TASK-001", "payment_change")
    logs = db.get_audit_logs("TASK-001")
    assert len(logs) == 1
    assert logs[0]["action"] == "cross_audit_requested"
    assert logs[0]["payload"]["auto_approved"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_audit_logger.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/infrastructure/audit_logger.py`**

```python
from __future__ import annotations

from src.infrastructure.database import Database
from src.models import CompletionReport


class AuditLogger:
    def __init__(self, db: Database) -> None:
        self._db = db

    def log_session_start(self, task_id: str, agent: str, workspace: str) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="session_start",
            payload={"workspace": workspace},
        )

    def log_session_end(
        self,
        task_id: str,
        agent: str,
        duration_seconds: int,
        token_count: int | None = None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="session_end",
            payload={
                "duration_seconds": duration_seconds,
                "token_count": token_count,
            },
        )

    def log_completion_report(
        self,
        report: CompletionReport,
        session_id: str,
        duration_seconds: int,
        token_count: int | None = None,
        estimated_cost: float | None = None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=report.task_id,
            agent=report.agent,
            action="completion_report",
            payload=report.model_dump(),
        )
        self._db.insert_task_result(
            task_id=report.task_id,
            agent=report.agent,
            session_id=session_id,
            output_summary=report.output_summary,
            confidence_score=report.confidence,
            risks_flagged=report.risks_flagged,
            duration_seconds=duration_seconds,
            token_count=token_count,
            estimated_cost=estimated_cost,
        )

    def log_review_verdict(
        self,
        task_id: str,
        reviewer: str,
        verdict: str,
        feedback: str | None,
        reviewed_agent: str | None = None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=reviewer,
            action="review_verdict",
            payload={
                "verdict": verdict,
                "feedback": feedback,
                "reviewed_agent": reviewed_agent,
            },
        )

    def log_escalation(self, task_id: str, agent: str, reason: str) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent=agent,
            action="escalation",
            payload={"reason": reason},
        )

    def log_cross_audit_stub(self, task_id: str, task_type: str) -> None:
        self._db.insert_audit_log(
            task_id=task_id,
            agent="orchestrator",
            action="cross_audit_requested",
            payload={
                "task_type": task_type,
                "auto_approved": True,
                "note": "Cross-audit stubbed -- Compliance Agent review pending Ops Crew implementation",
            },
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_audit_logger.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/audit_logger.py tests/test_audit_logger.py
git commit -m "feat: audit logger with semantic API for all event types"
```

---

## Task 4: Task Router

**Files:**
- Create: `src/orchestrator/task_router.py`
- Create: `tests/test_task_router.py`

Pure logic module. Given a task type and a dict of agent tiers, it returns the ordered list of steps (who does what).

- [ ] **Step 1: Write failing tests**

Create `tests/test_task_router.py`:

```python
from src.models import AgentName, PerformanceTier, TaskType
from src.orchestrator.task_router import build_task_chain


def test_implement_feature_all_green():
    tiers = {
        AgentName.PRODUCT_MANAGER: PerformanceTier.GREEN,
        AgentName.DEV_AGENT: PerformanceTier.GREEN,
    }
    chain = build_task_chain(TaskType.IMPLEMENT_FEATURE, tiers)
    agents = [step.agent for step in chain]
    assert agents == [
        AgentName.PRODUCT_MANAGER,   # write spec
        AgentName.DEV_AGENT,         # implement
        AgentName.ENGINEERING_HEAD,  # review
    ]


def test_implement_feature_dev_yellow():
    tiers = {
        AgentName.PRODUCT_MANAGER: PerformanceTier.GREEN,
        AgentName.DEV_AGENT: PerformanceTier.YELLOW,
    }
    chain = build_task_chain(TaskType.IMPLEMENT_FEATURE, tiers)
    agents = [step.agent for step in chain]
    # Yellow dev: Engineering Head pre-reviews before final review
    assert agents == [
        AgentName.PRODUCT_MANAGER,
        AgentName.DEV_AGENT,
        AgentName.ENGINEERING_HEAD,  # pre-review
        AgentName.DEV_AGENT,         # revise based on pre-review
        AgentName.ENGINEERING_HEAD,  # final review
    ]


def test_implement_feature_dev_red():
    tiers = {
        AgentName.PRODUCT_MANAGER: PerformanceTier.GREEN,
        AgentName.DEV_AGENT: PerformanceTier.RED,
    }
    chain = build_task_chain(TaskType.IMPLEMENT_FEATURE, tiers)
    agents = [step.agent for step in chain]
    # Red dev: extra Engineering Head review step (Ops Manager not available yet)
    assert agents == [
        AgentName.PRODUCT_MANAGER,
        AgentName.DEV_AGENT,
        AgentName.ENGINEERING_HEAD,  # pre-review
        AgentName.DEV_AGENT,         # revise
        AgentName.ENGINEERING_HEAD,  # second review
        AgentName.ENGINEERING_HEAD,  # final review
    ]


def test_bug_fix_all_green():
    tiers = {
        AgentName.PRODUCT_MANAGER: PerformanceTier.GREEN,
        AgentName.DEV_AGENT: PerformanceTier.GREEN,
    }
    chain = build_task_chain(TaskType.BUG_FIX, tiers)
    agents = [step.agent for step in chain]
    assert agents == [
        AgentName.PRODUCT_MANAGER,   # triage
        AgentName.DEV_AGENT,         # fix
        AgentName.ENGINEERING_HEAD,  # verify
    ]


def test_payment_change_all_green():
    tiers = {
        AgentName.PAYMENT_AGENT: PerformanceTier.GREEN,
    }
    chain = build_task_chain(TaskType.PAYMENT_CHANGE, tiers)
    agents = [step.agent for step in chain]
    assert agents == [
        AgentName.PAYMENT_AGENT,     # draft proposal
        AgentName.ENGINEERING_HEAD,  # review
    ]


def test_default_tier_is_green():
    """If an agent has no scorecard, assume green."""
    chain = build_task_chain(TaskType.IMPLEMENT_FEATURE, {})
    agents = [step.agent for step in chain]
    assert agents == [
        AgentName.PRODUCT_MANAGER,
        AgentName.DEV_AGENT,
        AgentName.ENGINEERING_HEAD,
    ]


def test_step_actions_are_descriptive():
    chain = build_task_chain(TaskType.IMPLEMENT_FEATURE, {})
    assert chain[0].action == "write_spec"
    assert chain[1].action == "implement"
    assert chain[2].action == "review"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_task_router.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/orchestrator/task_router.py`**

```python
from __future__ import annotations

from src.models import AgentName, PerformanceTier, TaskStep, TaskType


def _get_tier(
    agent: AgentName, tiers: dict[AgentName, PerformanceTier]
) -> PerformanceTier:
    return tiers.get(agent, PerformanceTier.GREEN)


def build_task_chain(
    task_type: TaskType,
    agent_tiers: dict[AgentName, PerformanceTier],
) -> list[TaskStep]:
    """Build an ordered list of task steps based on task type and agent tiers."""
    if task_type == TaskType.IMPLEMENT_FEATURE:
        return _implement_feature_chain(agent_tiers)
    elif task_type == TaskType.BUG_FIX:
        return _bug_fix_chain(agent_tiers)
    elif task_type == TaskType.PAYMENT_CHANGE:
        return _payment_change_chain(agent_tiers)
    else:
        raise ValueError(f"Unknown task type: {task_type}")


def _implement_feature_chain(
    tiers: dict[AgentName, PerformanceTier],
) -> list[TaskStep]:
    dev_tier = _get_tier(AgentName.DEV_AGENT, tiers)

    chain = [
        TaskStep(
            agent=AgentName.PRODUCT_MANAGER,
            action="write_spec",
            description="Write feature specification with acceptance criteria",
        ),
        TaskStep(
            agent=AgentName.DEV_AGENT,
            action="implement",
            description="Implement feature based on spec",
        ),
    ]

    if dev_tier == PerformanceTier.YELLOW:
        chain.extend([
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="pre_review",
                description="Pre-review implementation before final review",
            ),
            TaskStep(
                agent=AgentName.DEV_AGENT,
                action="revise",
                description="Revise implementation based on pre-review feedback",
            ),
        ])
    elif dev_tier == PerformanceTier.RED:
        chain.extend([
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="pre_review",
                description="Pre-review implementation before final review",
            ),
            TaskStep(
                agent=AgentName.DEV_AGENT,
                action="revise",
                description="Revise implementation based on pre-review feedback",
            ),
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="second_review",
                description="Second review (red tier requires extra scrutiny)",
            ),
        ])

    chain.append(
        TaskStep(
            agent=AgentName.ENGINEERING_HEAD,
            action="review",
            description="Final review of implementation",
        )
    )
    return chain


def _bug_fix_chain(
    tiers: dict[AgentName, PerformanceTier],
) -> list[TaskStep]:
    dev_tier = _get_tier(AgentName.DEV_AGENT, tiers)

    chain = [
        TaskStep(
            agent=AgentName.PRODUCT_MANAGER,
            action="triage",
            description="Triage bug: severity, reproduction steps, priority",
        ),
        TaskStep(
            agent=AgentName.DEV_AGENT,
            action="fix",
            description="Fix bug based on triage report",
        ),
    ]

    if dev_tier == PerformanceTier.YELLOW:
        chain.extend([
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="pre_review",
                description="Pre-review fix before final verification",
            ),
            TaskStep(
                agent=AgentName.DEV_AGENT,
                action="revise",
                description="Revise fix based on pre-review feedback",
            ),
        ])
    elif dev_tier == PerformanceTier.RED:
        chain.extend([
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="pre_review",
                description="Pre-review fix before final verification",
            ),
            TaskStep(
                agent=AgentName.DEV_AGENT,
                action="revise",
                description="Revise fix based on pre-review feedback",
            ),
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="second_review",
                description="Second review (red tier requires extra scrutiny)",
            ),
        ])

    chain.append(
        TaskStep(
            agent=AgentName.ENGINEERING_HEAD,
            action="review",
            description="Verify bug fix",
        )
    )
    return chain


def _payment_change_chain(
    tiers: dict[AgentName, PerformanceTier],
) -> list[TaskStep]:
    payment_tier = _get_tier(AgentName.PAYMENT_AGENT, tiers)

    chain = [
        TaskStep(
            agent=AgentName.PAYMENT_AGENT,
            action="draft_proposal",
            description="Draft payment change proposal with compliance considerations",
        ),
    ]

    if payment_tier == PerformanceTier.YELLOW:
        chain.extend([
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="pre_review",
                description="Pre-review payment proposal",
            ),
            TaskStep(
                agent=AgentName.PAYMENT_AGENT,
                action="revise",
                description="Revise proposal based on pre-review feedback",
            ),
        ])
    elif payment_tier == PerformanceTier.RED:
        chain.extend([
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="pre_review",
                description="Pre-review payment proposal",
            ),
            TaskStep(
                agent=AgentName.PAYMENT_AGENT,
                action="revise",
                description="Revise proposal based on pre-review feedback",
            ),
            TaskStep(
                agent=AgentName.ENGINEERING_HEAD,
                action="second_review",
                description="Second review (red tier requires extra scrutiny)",
            ),
        ])

    chain.append(
        TaskStep(
            agent=AgentName.ENGINEERING_HEAD,
            action="review",
            description="Review payment change proposal",
        )
    )
    return chain
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_task_router.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/task_router.py tests/test_task_router.py
git commit -m "feat: task router with tier-dependent chain generation"
```

---

## Task 5: Revision Loop

**Files:**
- Create: `src/orchestrator/revision_loop.py`
- Create: `tests/test_revision_loop.py`

Pure logic module. Given a review verdict, current revision count, and max rounds, it returns what should happen next.

- [ ] **Step 1: Write failing tests**

Create `tests/test_revision_loop.py`:

```python
from src.models import ReviewVerdict
from src.orchestrator.revision_loop import NextAction, decide_next_action


def test_approve_returns_approved():
    action = decide_next_action(
        verdict=ReviewVerdict.APPROVE,
        revision_count=0,
        max_rounds=2,
    )
    assert action.action == "approved"
    assert action.target_agent is None
    assert action.feedback is None


def test_reject_returns_rejected():
    action = decide_next_action(
        verdict=ReviewVerdict.REJECT,
        revision_count=0,
        max_rounds=2,
    )
    assert action.action == "rejected"


def test_revise_first_round():
    action = decide_next_action(
        verdict=ReviewVerdict.REVISE,
        revision_count=0,
        max_rounds=2,
        feedback="Fix the error handling",
        target_agent="dev_agent",
    )
    assert action.action == "revise"
    assert action.target_agent == "dev_agent"
    assert action.feedback == "Fix the error handling"


def test_revise_second_round():
    action = decide_next_action(
        verdict=ReviewVerdict.REVISE,
        revision_count=1,
        max_rounds=2,
        feedback="Still needs work",
        target_agent="dev_agent",
    )
    assert action.action == "revise"


def test_revise_at_max_rounds_escalates():
    action = decide_next_action(
        verdict=ReviewVerdict.REVISE,
        revision_count=2,
        max_rounds=2,
        feedback="Not good enough",
        target_agent="dev_agent",
    )
    assert action.action == "escalated"
    assert "Max revision rounds" in action.feedback


def test_revise_past_max_rounds_escalates():
    action = decide_next_action(
        verdict=ReviewVerdict.REVISE,
        revision_count=5,
        max_rounds=2,
        feedback="Still wrong",
        target_agent="dev_agent",
    )
    assert action.action == "escalated"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_revision_loop.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/orchestrator/revision_loop.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from src.models import ReviewVerdict


@dataclass
class NextAction:
    action: str  # "approved", "rejected", "revise", "escalated"
    target_agent: str | None = None
    feedback: str | None = None


def decide_next_action(
    verdict: ReviewVerdict,
    revision_count: int,
    max_rounds: int,
    feedback: str | None = None,
    target_agent: str | None = None,
) -> NextAction:
    """Decide what happens after a review verdict."""
    if verdict == ReviewVerdict.APPROVE:
        return NextAction(action="approved")

    if verdict == ReviewVerdict.REJECT:
        return NextAction(action="rejected")

    # verdict == REVISE
    if revision_count >= max_rounds:
        return NextAction(
            action="escalated",
            target_agent=target_agent,
            feedback=f"Max revision rounds ({max_rounds}) exceeded. Original feedback: {feedback}",
        )

    return NextAction(
        action="revise",
        target_agent=target_agent,
        feedback=feedback,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_revision_loop.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/revision_loop.py tests/test_revision_loop.py
git commit -m "feat: revision loop with max-rounds escalation logic"
```

---

## Task 6: Performance Tracker

**Files:**
- Create: `src/orchestrator/performance_tracker.py`
- Create: `tests/test_performance_tracker.py`

Reads task results from the database, calculates rolling 30-day metrics per agent, determines the performance tier, updates the SQLite scorecards table, and writes a human-readable `scorecard.md` to the agent's workspace.

- [ ] **Step 1: Write failing tests**

Create `tests/test_performance_tracker.py`:

```python
from pathlib import Path

from src.infrastructure.database import Database
from src.models import AgentName, PerformanceTier, TaskRecord, TaskStatus, TaskType
from src.orchestrator.performance_tracker import PerformanceTracker


def _seed_task_results(db: Database, agent: str, outcomes: list[str]) -> None:
    """Seed task results. outcomes is a list of 'approved' or 'revised' or 'rejected'."""
    for i, outcome in enumerate(outcomes):
        task_id = f"TASK-{i+1:03d}"
        db.insert_task(TaskRecord(id=task_id, type=TaskType.IMPLEMENT_FEATURE, brief="test"))
        db.insert_task_result(
            task_id=task_id,
            agent=agent,
            session_id=f"sess-{i}",
            output_summary="test output",
            confidence_score=80,
            duration_seconds=60,
            token_count=1000,
            estimated_cost=0.05,
        )
        # Log the review verdict in audit_log
        db.insert_audit_log(
            task_id=task_id,
            agent="engineering_head",
            action="review_verdict",
            payload={"verdict": outcome, "reviewed_agent": agent},
        )


def test_calculate_tier_green(test_settings):
    db = Database(test_settings.get_db_path())
    tracker = PerformanceTracker(db, test_settings)
    # 10 tasks, 9 approved, 1 revised = 90% acceptance -> green
    _seed_task_results(db, "dev_agent", ["approved"] * 9 + ["revised"])
    tier = tracker.calculate_tier("dev_agent")
    assert tier == PerformanceTier.GREEN


def test_calculate_tier_yellow(test_settings):
    db = Database(test_settings.get_db_path())
    tracker = PerformanceTracker(db, test_settings)
    # 10 tasks, 8 approved, 2 revised = 80% acceptance -> yellow
    _seed_task_results(db, "dev_agent", ["approved"] * 8 + ["revised"] * 2)
    tier = tracker.calculate_tier("dev_agent")
    assert tier == PerformanceTier.YELLOW


def test_calculate_tier_red(test_settings):
    db = Database(test_settings.get_db_path())
    tracker = PerformanceTracker(db, test_settings)
    # 10 tasks, 7 approved, 3 revised = 70% acceptance -> red
    _seed_task_results(db, "dev_agent", ["approved"] * 7 + ["revised"] * 3)
    tier = tracker.calculate_tier("dev_agent")
    assert tier == PerformanceTier.RED


def test_no_results_defaults_to_green(test_settings):
    db = Database(test_settings.get_db_path())
    tracker = PerformanceTracker(db, test_settings)
    tier = tracker.calculate_tier("dev_agent")
    assert tier == PerformanceTier.GREEN


def test_update_scorecard_writes_to_db(test_settings):
    db = Database(test_settings.get_db_path())
    tracker = PerformanceTracker(db, test_settings)
    _seed_task_results(db, "dev_agent", ["approved"] * 9 + ["revised"])
    tracker.update_scorecard("dev_agent")
    scorecard = db.get_scorecard("dev_agent")
    assert scorecard is not None
    assert scorecard["tier"] == "green"
    assert scorecard["acceptance_rate"] == 0.9


def test_write_scorecard_file(test_settings):
    db = Database(test_settings.get_db_path())
    tracker = PerformanceTracker(db, test_settings)
    workspace = test_settings.get_workspaces_dir() / "dev_agent"
    workspace.mkdir(parents=True)
    _seed_task_results(db, "dev_agent", ["approved"] * 9 + ["revised"])
    tracker.update_scorecard("dev_agent")
    tracker.write_scorecard_file("dev_agent", workspace)
    scorecard_path = workspace / "scorecard.md"
    assert scorecard_path.exists()
    content = scorecard_path.read_text()
    assert "green" in content.lower()
    assert "90" in content  # 90% acceptance rate


def test_get_all_tiers(test_settings):
    db = Database(test_settings.get_db_path())
    tracker = PerformanceTracker(db, test_settings)
    _seed_task_results(db, "dev_agent", ["approved"] * 9 + ["revised"])
    tracker.update_scorecard("dev_agent")
    tiers = tracker.get_all_tiers()
    assert tiers[AgentName.DEV_AGENT] == PerformanceTier.GREEN
    # Agents without scorecards default to green
    assert tiers[AgentName.PRODUCT_MANAGER] == PerformanceTier.GREEN
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_performance_tracker.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/orchestrator/performance_tracker.py`**

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.config import Settings
from src.infrastructure.database import Database
from src.models import AgentName, PerformanceTier


class PerformanceTracker:
    def __init__(self, db: Database, settings: Settings) -> None:
        self._db = db
        self._settings = settings

    def calculate_tier(self, agent: str) -> PerformanceTier:
        """Calculate performance tier based on review verdicts in the last 30 days."""
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        logs = self._db.get_audit_logs_by_action("review_verdict", since=since)
        # Filter to verdicts about this agent
        verdicts = [
            log for log in logs
            if log.get("payload", {}).get("reviewed_agent") == agent
        ]
        if not verdicts:
            return PerformanceTier.GREEN  # no data = benefit of the doubt

        approved = sum(
            1 for v in verdicts if v["payload"]["verdict"] == "approved"
        )
        total = len(verdicts)
        acceptance_rate = approved / total

        if acceptance_rate >= self._settings.tier_green_threshold:
            return PerformanceTier.GREEN
        elif acceptance_rate >= self._settings.tier_yellow_threshold:
            return PerformanceTier.YELLOW
        else:
            return PerformanceTier.RED

    def _compute_rates(self, agent: str) -> tuple[float, float, int]:
        """Return (acceptance_rate, revision_rate, error_count)."""
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        logs = self._db.get_audit_logs_by_action("review_verdict", since=since)
        verdicts = [
            log for log in logs
            if log.get("payload", {}).get("reviewed_agent") == agent
        ]
        if not verdicts:
            return 1.0, 0.0, 0

        total = len(verdicts)
        approved = sum(1 for v in verdicts if v["payload"]["verdict"] == "approved")
        revised = sum(1 for v in verdicts if v["payload"]["verdict"] == "revised")
        rejected = sum(1 for v in verdicts if v["payload"]["verdict"] == "rejected")

        acceptance_rate = approved / total if total else 1.0
        revision_rate = revised / total if total else 0.0
        return acceptance_rate, revision_rate, rejected

    def update_scorecard(self, agent: str) -> None:
        """Recalculate and persist scorecard for an agent."""
        acceptance_rate, revision_rate, error_count = self._compute_rates(agent)
        tier = self.calculate_tier(agent)
        now = datetime.now(timezone.utc)
        period_start = (now - timedelta(days=30)).isoformat()
        period_end = now.isoformat()
        self._db.upsert_scorecard(
            agent=agent,
            period_start=period_start,
            period_end=period_end,
            acceptance_rate=round(acceptance_rate, 4),
            revision_rate=round(revision_rate, 4),
            error_count=error_count,
            tier=tier.value,
        )

    def write_scorecard_file(self, agent: str, workspace: Path) -> None:
        """Write a human-readable scorecard.md to the agent's workspace."""
        scorecard = self._db.get_scorecard(agent)
        if scorecard is None:
            content = "# Scorecard\n\nNo performance data yet. Tier: green (default)\n"
        else:
            content = (
                f"# Scorecard: {agent}\n\n"
                f"**Tier: {scorecard['tier'].upper()}**\n\n"
                f"| Metric | Value |\n"
                f"|--------|-------|\n"
                f"| Acceptance rate | {scorecard['acceptance_rate'] * 100:.0f}% |\n"
                f"| Revision rate | {scorecard['revision_rate'] * 100:.0f}% |\n"
                f"| Errors (rejections) | {scorecard['error_count']} |\n"
                f"| Period | {scorecard['period_start'][:10]} to {scorecard['period_end'][:10]} |\n"
                f"| Updated | {scorecard['updated_at'][:19]} |\n"
            )
        (workspace / "scorecard.md").write_text(content)

    def get_all_tiers(self) -> dict[AgentName, PerformanceTier]:
        """Get current tier for all Product & Engineering agents."""
        tiers: dict[AgentName, PerformanceTier] = {}
        for agent in AgentName:
            scorecard = self._db.get_scorecard(agent.value)
            if scorecard:
                tiers[agent] = PerformanceTier(scorecard["tier"])
            else:
                tiers[agent] = PerformanceTier.GREEN
        return tiers
```

- [ ] **Step 4: Add missing `get_audit_logs_by_action` method to Database**

The performance tracker needs to query audit logs by action type and date. Add this method to `src/infrastructure/database.py`:

```python
def get_audit_logs_by_action(self, action: str, since: str | None = None) -> list[dict]:
    """Get audit logs filtered by action, optionally since a date."""
    if since:
        cursor = self._conn.execute(
            "SELECT * FROM audit_log WHERE action = ? AND timestamp >= ? ORDER BY id",
            (action, since),
        )
    else:
        cursor = self._conn.execute(
            "SELECT * FROM audit_log WHERE action = ? ORDER BY id", (action,)
        )
    rows = cursor.fetchall()
    result = []
    for row in rows:
        d = dict(row)
        if d.get("payload"):
            d["payload"] = json.loads(d["payload"])
        result.append(d)
    return result
```

Add this method right after the existing `get_audit_logs` method in `src/infrastructure/database.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_performance_tracker.py -v`
Expected: All 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/performance_tracker.py src/infrastructure/database.py tests/test_performance_tracker.py
git commit -m "feat: performance tracker with tier calculation and scorecard files"
```

---

## Task 7: Context Builder

**Files:**
- Create: `src/orchestrator/context_builder.py`
- Create: `tests/test_context_builder.py`

Generates `CLAUDE.md` and `.claude/settings.json` in each agent's workspace directory. CLAUDE.md contains: system prompt, org charter summary, pointers to persistent files, and optionally the task brief. settings.json configures Claude Code permissions and the git pull hook.

- [ ] **Step 1: Write failing tests**

Create `tests/test_context_builder.py`:

```python
import json
from pathlib import Path

from src.orchestrator.context_builder import ContextBuilder


def test_build_settings_json(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    builder.write_settings_json(workspace)
    settings_path = workspace / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "permissions" in data
    assert "allow" in data["permissions"]
    assert "Read(*)" in data["permissions"]["allow"]
    assert "hooks" in data
    assert "PreToolUse" in data["hooks"]


def test_build_claude_md_contains_system_prompt(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    system_prompt = "You are the Dev Agent for a tourism services company."
    builder.write_claude_md(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt=system_prompt,
    )
    claude_md = workspace / "CLAUDE.md"
    assert claude_md.exists()
    content = claude_md.read_text()
    assert "Dev Agent" in content
    assert "tourism services" in content


def test_build_claude_md_contains_persistent_file_pointers(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    builder.write_claude_md(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )
    content = (workspace / "CLAUDE.md").read_text()
    assert "learnings.md" in content
    assert "scorecard.md" in content
    assert "recent_tasks.md" in content


def test_build_claude_md_with_task_brief(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    builder.write_claude_md(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
        task_brief="Implement Alipay integration for international cards",
    )
    content = (workspace / "CLAUDE.md").read_text()
    assert "Alipay integration" in content


def test_initialize_workspace_creates_persistent_files(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    builder.initialize_workspace(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )
    assert (workspace / "learnings.md").exists()
    assert (workspace / "scorecard.md").exists()
    assert (workspace / "recent_tasks.md").exists()
    assert (workspace / "CLAUDE.md").exists()
    assert (workspace / ".claude" / "settings.json").exists()


def test_initialize_workspace_does_not_overwrite_existing_learnings(test_settings, tmp_dir):
    builder = ContextBuilder(test_settings)
    workspace = tmp_dir / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    (workspace / "learnings.md").write_text("# Learnings\n\n- Important lesson\n")
    builder.initialize_workspace(
        workspace=workspace,
        agent_name="dev_agent",
        system_prompt="You are the Dev Agent.",
    )
    content = (workspace / "learnings.md").read_text()
    assert "Important lesson" in content
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_context_builder.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/orchestrator/context_builder.py`**

```python
from __future__ import annotations

import json
from pathlib import Path

from src.config import Settings


_SETTINGS_JSON = {
    "permissions": {
        "allow": ["Read(*)", "Write(*)", "Bash(*)", "Glob(*)", "Grep(*)"]
    },
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash|Read|Grep|Glob",
                "command": "cd repo && git pull --ff-only 2>/dev/null; true",
                "runOnce": True,
            }
        ]
    },
}


class ContextBuilder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def write_settings_json(self, workspace: Path) -> None:
        """Write .claude/settings.json to workspace."""
        claude_dir = workspace / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "settings.json").write_text(
            json.dumps(_SETTINGS_JSON, indent=2) + "\n"
        )

    def write_claude_md(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        task_brief: str | None = None,
    ) -> None:
        """Write CLAUDE.md to workspace with system prompt and context pointers."""
        sections = [
            f"# Agent: {agent_name}\n",
            "## System Prompt\n",
            system_prompt.strip() + "\n",
            "## Persistent Files\n",
            "- `learnings.md` -- your accumulated operational learnings (append new insights here)",
            "- `scorecard.md` -- your current performance scorecard (read-only, updated by orchestrator)",
            "- `recent_tasks.md` -- summary of your recent tasks (read-only, updated by orchestrator)\n",
            "## Completion Report\n",
            "At the end of every task, write `completion_report.json` to this workspace root:",
            "```json",
            '{',
            '  "task_id": "<from your task brief>",',
            '  "agent": "' + agent_name + '",',
            '  "status": "completed",',
            '  "confidence": 85,',
            '  "output_summary": "<what you did>",',
            '  "risks_flagged": ["<any concerns>"],',
            '  "dependencies": ["<what you assumed or relied on>"],',
            '  "suggested_reviewer_focus": ["<where to look hardest>"]',
            '}',
            "```\n",
            "If you learn something reusable for future tasks, append it to `learnings.md` in this workspace.\n",
        ]

        if task_brief:
            sections.extend([
                "## Current Task\n",
                task_brief.strip() + "\n",
            ])

        (workspace / "CLAUDE.md").write_text("\n".join(sections))

    def initialize_workspace(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
    ) -> None:
        """Set up an agent workspace with all required files.

        Creates persistent files only if they don't already exist.
        Always regenerates CLAUDE.md and settings.json.
        """
        workspace.mkdir(parents=True, exist_ok=True)

        # Persistent files: create only if missing
        for filename, default_content in [
            ("learnings.md", f"# Learnings: {agent_name}\n\n"),
            ("scorecard.md", "# Scorecard\n\nNo performance data yet. Tier: green (default)\n"),
            ("recent_tasks.md", f"# Recent Tasks: {agent_name}\n\n"),
        ]:
            path = workspace / filename
            if not path.exists():
                path.write_text(default_content)

        # Regenerated files
        self.write_claude_md(workspace, agent_name, system_prompt)
        self.write_settings_json(workspace)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_context_builder.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/context_builder.py tests/test_context_builder.py
git commit -m "feat: context builder generates CLAUDE.md and settings.json for agent workspaces"
```

---

## Task 8: Agent Executor

**Files:**
- Create: `src/orchestrator/executor.py`
- Create: `tests/test_executor.py`

Spawns `claude -p "<prompt>" --permission-mode auto` as a subprocess from the agent's workspace directory. Waits for completion. Reads `completion_report.json` from the workspace. Returns structured results.

- [ ] **Step 1: Write failing tests**

Create `tests/test_executor.py`:

```python
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.models import CompletionReport
from src.orchestrator.executor import AgentExecutor, ExecutorResult


def test_executor_result_from_completion_report():
    report = CompletionReport(
        task_id="TASK-001",
        agent="dev_agent",
        status="completed",
        confidence=85,
        output_summary="Done",
    )
    result = ExecutorResult(
        success=True,
        report=report,
        duration_seconds=60,
        session_id="sess-001",
    )
    assert result.success is True
    assert result.report.confidence == 85


def test_executor_result_when_no_report():
    result = ExecutorResult(
        success=False,
        report=None,
        duration_seconds=120,
        session_id="sess-002",
        error="No completion_report.json found",
    )
    assert result.success is False
    assert result.error == "No completion_report.json found"


def test_read_completion_report(tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    report_data = {
        "task_id": "TASK-001",
        "agent": "dev_agent",
        "status": "completed",
        "confidence": 85,
        "output_summary": "Implemented feature",
        "risks_flagged": [],
        "dependencies": [],
        "suggested_reviewer_focus": [],
    }
    (workspace / "completion_report.json").write_text(json.dumps(report_data))

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto")
    report = executor.read_completion_report(workspace)
    assert report is not None
    assert report.task_id == "TASK-001"
    assert report.confidence == 85


def test_read_completion_report_missing_file(tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto")
    report = executor.read_completion_report(workspace)
    assert report is None


def test_read_completion_report_invalid_json(tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()
    (workspace / "completion_report.json").write_text("not valid json")

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto")
    report = executor.read_completion_report(workspace)
    assert report is None


@patch("src.orchestrator.executor.subprocess")
def test_run_agent_session_success(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    # Pre-create the completion report (simulates what the agent writes)
    report_data = {
        "task_id": "TASK-001",
        "agent": "dev_agent",
        "status": "completed",
        "confidence": 85,
        "output_summary": "Done",
        "risks_flagged": [],
        "dependencies": [],
        "suggested_reviewer_focus": [],
    }
    (workspace / "completion_report.json").write_text(json.dumps(report_data))

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = "Agent output"
    mock_subprocess.run.return_value = mock_process

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto")
    result = executor.run(
        workspace=workspace,
        prompt="Implement Alipay support",
        timeout_seconds=30,
    )

    assert result.success is True
    assert result.report is not None
    assert result.report.task_id == "TASK-001"

    # Verify subprocess was called correctly
    call_args = mock_subprocess.run.call_args
    cmd = call_args[0][0]
    assert "claude" in cmd[0]
    assert "-p" in cmd
    assert "--permission-mode" in cmd
    assert "auto" in cmd


@patch("src.orchestrator.executor.subprocess")
def test_run_agent_session_timeout(mock_subprocess, tmp_path):
    import subprocess

    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    mock_subprocess.TimeoutExpired = subprocess.TimeoutExpired
    mock_subprocess.run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=30)

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto")
    result = executor.run(
        workspace=workspace,
        prompt="Long task",
        timeout_seconds=30,
    )

    assert result.success is False
    assert "timed out" in result.error.lower()


@patch("src.orchestrator.executor.subprocess")
def test_run_cleans_old_report_before_session(mock_subprocess, tmp_path):
    workspace = tmp_path / "dev_agent"
    workspace.mkdir()

    # Write an old completion report
    old_report = {"task_id": "OLD-TASK", "agent": "dev_agent", "status": "completed",
                  "confidence": 50, "output_summary": "old"}
    (workspace / "completion_report.json").write_text(json.dumps(old_report))

    mock_process = MagicMock()
    mock_process.returncode = 0
    mock_process.stdout = ""
    mock_subprocess.run.return_value = mock_process

    executor = AgentExecutor(claude_cli_path="claude", permission_mode="auto")
    result = executor.run(workspace=workspace, prompt="New task", timeout_seconds=30)

    # Old report was cleaned, no new one written by mock -> failure
    assert result.success is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_executor.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/orchestrator/executor.py`**

```python
from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from src.models import CompletionReport


@dataclass
class ExecutorResult:
    success: bool
    report: CompletionReport | None
    duration_seconds: int
    session_id: str
    error: str | None = None


class AgentExecutor:
    def __init__(self, claude_cli_path: str, permission_mode: str) -> None:
        self._cli_path = claude_cli_path
        self._permission_mode = permission_mode

    def read_completion_report(self, workspace: Path) -> CompletionReport | None:
        """Read and parse completion_report.json from a workspace."""
        report_path = workspace / "completion_report.json"
        if not report_path.exists():
            return None
        try:
            data = json.loads(report_path.read_text())
            return CompletionReport(**data)
        except (json.JSONDecodeError, Exception):
            return None

    def run(
        self,
        workspace: Path,
        prompt: str,
        timeout_seconds: int = 1800,
    ) -> ExecutorResult:
        """Spawn a claude -p session and return the result."""
        session_id = f"sess-{uuid.uuid4().hex[:8]}"

        # Clean old completion report
        report_path = workspace / "completion_report.json"
        if report_path.exists():
            report_path.unlink()

        cmd = [
            self._cli_path,
            "-p", prompt,
            "--permission-mode", self._permission_mode,
        ]

        start_time = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            duration = int(time.monotonic() - start_time)
            return ExecutorResult(
                success=False,
                report=None,
                duration_seconds=duration,
                session_id=session_id,
                error=f"Session timed out after {timeout_seconds} seconds",
            )

        duration = int(time.monotonic() - start_time)

        # Read completion report
        report = self.read_completion_report(workspace)
        if report is None:
            return ExecutorResult(
                success=False,
                report=None,
                duration_seconds=duration,
                session_id=session_id,
                error="No completion_report.json found after session completed",
            )

        return ExecutorResult(
            success=True,
            report=report,
            duration_seconds=duration,
            session_id=session_id,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_executor.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/executor.py tests/test_executor.py
git commit -m "feat: agent executor spawns claude -p sessions and reads completion reports"
```

---

## Task 9: Main Orchestrator

**Files:**
- Create: `src/orchestrator/orchestrator.py`
- Create: `tests/test_orchestrator.py`

The main coordination loop. Receives task requests, creates DB records, builds tier-dependent task chains, spawns executor sessions step by step, handles review verdicts via the revision loop, logs everything, and manages cross-crew audit stubs. This integrates all previous modules.

- [ ] **Step 1: Write failing tests**

Create `tests/test_orchestrator.py`:

```python
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.config import Settings
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import (
    AgentName,
    CompletionReport,
    PerformanceTier,
    ReviewVerdict,
    TaskStatus,
    TaskType,
)
from src.orchestrator.executor import ExecutorResult
from src.orchestrator.orchestrator import Orchestrator


def _make_executor_result(task_id: str, agent: str, verdict: str = "completed") -> ExecutorResult:
    return ExecutorResult(
        success=True,
        report=CompletionReport(
            task_id=task_id,
            agent=agent,
            status=verdict,
            confidence=85,
            output_summary="Work completed",
        ),
        duration_seconds=60,
        session_id="sess-test",
    )


def _make_review_result(task_id: str, verdict: str, feedback: str | None = None) -> ExecutorResult:
    """Simulate Engineering Head returning a review verdict via completion report."""
    return ExecutorResult(
        success=True,
        report=CompletionReport(
            task_id=task_id,
            agent="engineering_head",
            status="completed",
            confidence=90,
            output_summary=json.dumps({
                "verdict": verdict,
                "feedback": feedback,
                "target_agent": "dev_agent",
            }),
        ),
        duration_seconds=30,
        session_id="sess-review",
    )


@pytest.fixture
def orchestrator(test_settings):
    db = Database(test_settings.get_db_path())
    return Orchestrator(db=db, settings=test_settings)


import pytest


def test_create_task(orchestrator):
    task_id = orchestrator.create_task(
        task_type=TaskType.IMPLEMENT_FEATURE,
        brief="Add Alipay support",
    )
    assert task_id == "TASK-001"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.PENDING
    assert task.brief == "Add Alipay support"


def test_create_task_increments_id(orchestrator):
    id1 = orchestrator.create_task(TaskType.IMPLEMENT_FEATURE, "Feature 1")
    id2 = orchestrator.create_task(TaskType.BUG_FIX, "Bug 1")
    assert id1 == "TASK-001"
    assert id2 == "TASK-002"


def test_build_chain_uses_tiers(orchestrator):
    chain = orchestrator.build_chain(TaskType.IMPLEMENT_FEATURE)
    # Default tiers (all green): PM -> Dev -> Eng Head
    agents = [s.agent for s in chain]
    assert agents == [
        AgentName.PRODUCT_MANAGER,
        AgentName.DEV_AGENT,
        AgentName.ENGINEERING_HEAD,
    ]


@patch.object(Orchestrator, "_run_agent_step")
def test_run_task_approved_flow(mock_run_step, orchestrator, test_settings):
    """Test the happy path: PM writes spec, Dev implements, Eng Head approves."""
    # Setup workspaces
    for agent in ["engineering_head", "product_manager", "dev_agent"]:
        ws = test_settings.get_workspaces_dir() / agent
        ws.mkdir(parents=True, exist_ok=True)

    call_count = 0

    def mock_step(task_id, step, prior_output):
        nonlocal call_count
        call_count += 1
        agent = step.agent.value
        if step.action == "review":
            return _make_review_result(task_id, "approve")
        return _make_executor_result(task_id, agent)

    mock_run_step.side_effect = mock_step

    task_id = orchestrator.create_task(TaskType.IMPLEMENT_FEATURE, "Add feature")
    result = orchestrator.run_task(task_id)

    assert result == "approved"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.APPROVED
    assert call_count == 3  # PM + Dev + review


@patch.object(Orchestrator, "_run_agent_step")
def test_run_task_revise_then_approve(mock_run_step, orchestrator, test_settings):
    """Test: Eng Head rejects first, Dev revises, then approved."""
    for agent in ["engineering_head", "product_manager", "dev_agent"]:
        ws = test_settings.get_workspaces_dir() / agent
        ws.mkdir(parents=True, exist_ok=True)

    review_call = 0

    def mock_step(task_id, step, prior_output):
        nonlocal review_call
        agent = step.agent.value
        if step.action == "review":
            review_call += 1
            if review_call == 1:
                return _make_review_result(task_id, "revise", "Fix error handling")
            return _make_review_result(task_id, "approve")
        return _make_executor_result(task_id, agent)

    mock_run_step.side_effect = mock_step

    task_id = orchestrator.create_task(TaskType.IMPLEMENT_FEATURE, "Add feature")
    result = orchestrator.run_task(task_id)

    assert result == "approved"
    task = orchestrator._db.get_task(task_id)
    assert task.revision_count == 1


@patch.object(Orchestrator, "_run_agent_step")
def test_run_task_escalates_after_max_revisions(mock_run_step, orchestrator, test_settings):
    for agent in ["engineering_head", "product_manager", "dev_agent"]:
        ws = test_settings.get_workspaces_dir() / agent
        ws.mkdir(parents=True, exist_ok=True)

    def mock_step(task_id, step, prior_output):
        agent = step.agent.value
        if step.action == "review":
            return _make_review_result(task_id, "revise", "Still not right")
        return _make_executor_result(task_id, agent)

    mock_run_step.side_effect = mock_step

    task_id = orchestrator.create_task(TaskType.IMPLEMENT_FEATURE, "Add feature")
    result = orchestrator.run_task(task_id)

    assert result == "escalated"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.ESCALATED
    assert task.revision_count == 2


@patch.object(Orchestrator, "_run_agent_step")
def test_payment_change_logs_cross_audit_stub(mock_run_step, orchestrator, test_settings):
    for agent in ["engineering_head", "payment_agent"]:
        ws = test_settings.get_workspaces_dir() / agent
        ws.mkdir(parents=True, exist_ok=True)

    def mock_step(task_id, step, prior_output):
        agent = step.agent.value
        if step.action == "review":
            return _make_review_result(task_id, "approve")
        return _make_executor_result(task_id, agent)

    mock_run_step.side_effect = mock_step

    task_id = orchestrator.create_task(TaskType.PAYMENT_CHANGE, "Add WeChat Pay")
    orchestrator.run_task(task_id)

    logs = orchestrator._db.get_audit_logs(task_id)
    cross_audit = [l for l in logs if l["action"] == "cross_audit_requested"]
    assert len(cross_audit) == 1
    assert cross_audit[0]["payload"]["auto_approved"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `src/orchestrator/orchestrator.py`**

```python
from __future__ import annotations

import json
import logging
from pathlib import Path

from src.config import Settings
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import (
    AgentName,
    ReviewVerdict,
    TaskRecord,
    TaskStatus,
    TaskStep,
    TaskType,
)
from src.orchestrator.context_builder import ContextBuilder
from src.orchestrator.executor import AgentExecutor, ExecutorResult
from src.orchestrator.performance_tracker import PerformanceTracker
from src.orchestrator.revision_loop import decide_next_action
from src.orchestrator.task_router import build_task_chain

logger = logging.getLogger(__name__)


# System prompts for Product & Engineering Crew agents.
# In production these are read from the markdown docs; here we store short
# versions as defaults so the orchestrator can function without the docs.
_DEFAULT_SYSTEM_PROMPTS: dict[str, str] = {
    "engineering_head": "You are the Engineering Head. Review work from your team. Return a JSON verdict in your output_summary: {\"verdict\": \"approve\"|\"revise\"|\"reject\", \"feedback\": \"...\", \"target_agent\": \"...\"}",
    "product_manager": "You are the Product Manager. Write specs and triage bugs.",
    "dev_agent": "You are the Dev Agent. Implement features and fix bugs.",
    "payment_agent": "You are the Payment Agent. Draft payment change proposals with compliance considerations.",
}


class Orchestrator:
    def __init__(self, db: Database, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._audit = AuditLogger(db)
        self._tracker = PerformanceTracker(db, settings)
        self._context = ContextBuilder(settings)
        self._executor = AgentExecutor(
            claude_cli_path=settings.claude_cli_path,
            permission_mode=settings.permission_mode,
        )

    def create_task(self, task_type: TaskType, brief: str) -> str:
        """Create a new task and persist it."""
        task_id = self._db.next_task_id()
        task = TaskRecord(id=task_id, type=task_type, brief=brief)
        self._db.insert_task(task)
        logger.info("Created task %s: %s", task_id, brief)
        return task_id

    def build_chain(self, task_type: TaskType) -> list[TaskStep]:
        """Build a task chain based on current agent tiers."""
        tiers = self._tracker.get_all_tiers()
        return build_task_chain(task_type, tiers)

    def run_task(self, task_id: str) -> str:
        """Run a task through its full lifecycle. Returns final status string."""
        task = self._db.get_task(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found")

        chain = self.build_chain(task.type)
        self._db.update_task(task_id, status=TaskStatus.IN_PROGRESS)

        # For payment_change, log cross-audit stub before review
        if task.type == TaskType.PAYMENT_CHANGE:
            self._audit.log_cross_audit_stub(task_id, task.type.value)

        prior_output: str | None = None
        review_step_index = self._find_review_step(chain)

        # Run pre-review steps
        for step in chain[:review_step_index]:
            result = self._run_agent_step(task_id, step, prior_output)
            if not result.success:
                self._db.update_task(task_id, status=TaskStatus.REJECTED)
                return "rejected"
            self._log_step_result(task_id, result)
            prior_output = result.report.output_summary if result.report else None

        # Review loop
        return self._review_loop(task_id, task, chain, review_step_index, prior_output)

    def _find_review_step(self, chain: list[TaskStep]) -> int:
        """Find the index of the final review step."""
        for i in range(len(chain) - 1, -1, -1):
            if chain[i].action == "review":
                return i
        return len(chain) - 1

    def _review_loop(
        self,
        task_id: str,
        task: TaskRecord,
        chain: list[TaskStep],
        review_index: int,
        prior_output: str | None,
    ) -> str:
        """Run the review step, handle revisions, return final status."""
        revision_count = 0
        max_rounds = self._settings.max_revision_rounds

        while True:
            # Run the review step
            review_step = chain[review_index]
            self._db.update_task(task_id, status=TaskStatus.IN_REVIEW)
            result = self._run_agent_step(task_id, review_step, prior_output)

            if not result.success or result.report is None:
                self._db.update_task(task_id, status=TaskStatus.REJECTED)
                return "rejected"

            self._log_step_result(task_id, result)

            # Parse verdict from Engineering Head's output
            verdict, feedback, target_agent = self._parse_review_verdict(result)
            reviewed_agent = target_agent or self._find_last_worker(chain, review_index)
            self._audit.log_review_verdict(
                task_id, review_step.agent.value, verdict, feedback,
                reviewed_agent=reviewed_agent,
            )

            # Use revision loop to decide next action
            action = decide_next_action(
                verdict=ReviewVerdict(verdict),
                revision_count=revision_count,
                max_rounds=max_rounds,
                feedback=feedback,
                target_agent=target_agent,
            )

            if action.action == "approved":
                self._db.update_task(task_id, status=TaskStatus.APPROVED)
                self._update_recent_tasks(task_id)
                return "approved"

            if action.action == "rejected":
                self._db.update_task(task_id, status=TaskStatus.REJECTED)
                self._update_recent_tasks(task_id)
                return "rejected"

            if action.action == "escalated":
                self._db.update_task(task_id, status=TaskStatus.ESCALATED)
                self._audit.log_escalation(task_id, "orchestrator", action.feedback or "Max revisions exceeded")
                self._update_recent_tasks(task_id)
                return "escalated"

            # action.action == "revise"
            revision_count += 1
            self._db.increment_revision_count(task_id)

            # Find the worker to revise and re-run them
            revise_agent = target_agent or self._find_last_worker(chain, review_index)
            revise_step = TaskStep(
                agent=AgentName(revise_agent),
                action="revise",
                description=f"Revise based on feedback: {feedback}",
            )
            revise_result = self._run_agent_step(task_id, revise_step, feedback)
            if revise_result.success and revise_result.report:
                self._log_step_result(task_id, revise_result)
                prior_output = revise_result.report.output_summary

    def _parse_review_verdict(self, result: ExecutorResult) -> tuple[str, str | None, str | None]:
        """Parse the Engineering Head's review verdict from the completion report."""
        if result.report is None:
            return "reject", "No completion report", None
        try:
            data = json.loads(result.report.output_summary)
            return (
                data.get("verdict", "reject"),
                data.get("feedback"),
                data.get("target_agent"),
            )
        except (json.JSONDecodeError, AttributeError):
            # If output_summary isn't JSON, treat as approve if confidence is high
            if result.report.confidence >= 80:
                return "approve", None, None
            return "revise", result.report.output_summary, None

    def _find_last_worker(self, chain: list[TaskStep], before_index: int) -> str:
        """Find the last non-review agent in the chain before the review step."""
        for i in range(before_index - 1, -1, -1):
            if chain[i].agent != AgentName.ENGINEERING_HEAD:
                return chain[i].agent.value
        return AgentName.DEV_AGENT.value

    def _run_agent_step(
        self,
        task_id: str,
        step: TaskStep,
        prior_output: str | None,
    ) -> ExecutorResult:
        """Set up workspace context and run an agent session."""
        agent_name = step.agent.value
        workspace = self._settings.get_workspaces_dir() / agent_name

        # Ensure workspace exists
        system_prompt = _DEFAULT_SYSTEM_PROMPTS.get(agent_name, "")
        self._context.initialize_workspace(workspace, agent_name, system_prompt)

        # Build task prompt
        task = self._db.get_task(task_id)
        prompt_parts = [
            f"Task ID: {task_id}",
            f"Action: {step.action}",
            f"Description: {step.description}",
            f"Brief: {task.brief}" if task else "",
        ]
        if prior_output:
            prompt_parts.append(f"Input from previous step:\n{prior_output}")
        prompt = "\n\n".join(p for p in prompt_parts if p)

        # Log session start
        self._audit.log_session_start(task_id, agent_name, str(workspace))
        self._db.update_task(task_id, assigned_agent=agent_name)

        # Run
        result = self._executor.run(
            workspace=workspace,
            prompt=prompt,
            timeout_seconds=self._settings.session_timeout_seconds,
        )

        # Log session end
        self._audit.log_session_end(task_id, agent_name, result.duration_seconds)

        return result

    def _update_recent_tasks(self, task_id: str) -> None:
        """Append a summary to recent_tasks.md for all agents involved in this task."""
        task = self._db.get_task(task_id)
        if task is None:
            return
        summary = f"- **{task_id}** ({task.type.value}): {task.brief} — {task.status.value} (revisions: {task.revision_count})\n"
        for agent in AgentName:
            workspace = self._settings.get_workspaces_dir() / agent.value
            recent_path = workspace / "recent_tasks.md"
            if recent_path.exists():
                content = recent_path.read_text()
                recent_path.write_text(content + summary)

    def _log_step_result(self, task_id: str, result: ExecutorResult) -> None:
        """Log a successful step result to audit trail."""
        if result.report:
            self._audit.log_completion_report(
                report=result.report,
                session_id=result.session_id,
                duration_seconds=result.duration_seconds,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_orchestrator.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: main orchestrator with task lifecycle, review loop, and cross-audit stub"
```

---

## Task 10: CLI Script and Agent Workspace Bootstrap

**Files:**
- Create: `scripts/run_product_crew.py`
- Create: `tests/test_cli.py`

The CLI entry point that exposes the orchestrator to the command line. Also validates that workspaces can be bootstrapped.

- [ ] **Step 1: Write failing test for CLI argument parsing**

Create `tests/test_cli.py`:

```python
import sys
from unittest.mock import MagicMock, patch

from src.models import TaskType


def test_parse_args():
    # Import inline to avoid config issues
    sys.argv = [
        "run_product_crew.py",
        "--task", "implement_feature",
        "--brief", "Add Alipay support for international cards",
    ]
    from scripts.run_product_crew import parse_args

    args = parse_args()
    assert args.task == "implement_feature"
    assert args.brief == "Add Alipay support for international cards"


def test_parse_args_bug_fix():
    sys.argv = [
        "run_product_crew.py",
        "--task", "bug_fix",
        "--brief", "Fix broken payment links",
    ]
    from scripts.run_product_crew import parse_args

    args = parse_args()
    assert args.task == "bug_fix"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Create `scripts/run_product_crew.py`**

```python
#!/usr/bin/env python3
"""CLI entry point for running Product & Engineering Crew tasks."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import settings
from src.infrastructure.database import Database
from src.models import TaskType
from src.orchestrator.orchestrator import Orchestrator


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a Product & Engineering Crew task"
    )
    parser.add_argument(
        "--task",
        required=True,
        choices=["implement_feature", "bug_fix", "payment_change"],
        help="Type of task to run",
    )
    parser.add_argument(
        "--brief",
        required=True,
        help="Task description / brief",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to SQLite database (default: opc.db in project root)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    db_path = Path(args.db) if args.db else settings.get_db_path()
    db = Database(db_path)

    orchestrator = Orchestrator(db=db, settings=settings)

    task_type = TaskType(args.task)
    task_id = orchestrator.create_task(task_type, args.brief)

    logging.info("Created task %s (%s): %s", task_id, args.task, args.brief)
    logging.info("Running task...")

    result = orchestrator.run_task(task_id)

    logging.info("Task %s completed with status: %s", task_id, result)

    # Print summary
    task = db.get_task(task_id)
    print(f"\n{'='*60}")
    print(f"Task ID:    {task_id}")
    print(f"Type:       {args.task}")
    print(f"Status:     {result}")
    print(f"Revisions:  {task.revision_count}")
    print(f"{'='*60}")

    db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/test_cli.py -v`
Expected: All 2 tests PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/run_product_crew.py tests/test_cli.py
git commit -m "feat: CLI entry point for running Product & Engineering Crew tasks"
```

---

## Task 11: Full Test Suite Verification

**Files:**
- No new files -- verify everything works together.

- [ ] **Step 1: Run the full test suite**

Run: `cd /Users/tangbz/projects/my-opc && python -m pytest tests/ -v`
Expected: All tests PASS (approximately 42 tests across 8 test files)

- [ ] **Step 2: Fix any failures**

If any tests fail, fix them before proceeding.

- [ ] **Step 3: Run the CLI with `--help` to verify it's wired up**

Run: `cd /Users/tangbz/projects/my-opc && python scripts/run_product_crew.py --help`
Expected: Help text showing `--task`, `--brief`, `--db`, and `--verbose` options.

- [ ] **Step 4: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: test suite passing for Product & Engineering Crew implementation"
```

---

## Dependency Graph

```
Task 1 (Setup + Models)
  ├── Task 2 (Database)
  │     ├── Task 3 (Audit Logger)
  │     └── Task 6 (Performance Tracker)
  ├── Task 4 (Task Router)
  ├── Task 5 (Revision Loop)
  ├── Task 7 (Context Builder)
  └── Task 8 (Executor)
        └── Task 9 (Orchestrator) ← depends on Tasks 2-8
              └── Task 10 (CLI + Workspaces)
                    └── Task 11 (Full Verification)
```

Tasks 4, 5, 7, and 8 are independent of each other and can be developed in parallel after Task 1. Tasks 3 and 6 are independent of each other and can be developed in parallel after Task 2.
