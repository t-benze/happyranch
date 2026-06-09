# Nightly Dreaming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the private nightly dream mechanism described in `docs/superpowers/specs/2026-06-09-nightly-dreaming-design.md`.

**Architecture:** Dreams are a first-class invocation type, separate from tasks, talks, and threads. The daemon parses per-org `dreaming:` config, schedules one private reflective invocation per selected agent per local date, persists dream rows/transcripts/KB candidates, and creates a founder-only thread only when the dream payload asks for it.

**Tech Stack:** Python 3.12, FastAPI, SQLite, Pydantic v2, argparse CLI, existing HappyRanch executor classes, pytest.

---

## File Structure

- Create `runtime/daemon/dream_queue.py`: async queue and worker loop for `DreamJob`.
- Create `runtime/daemon/dream_runner.py`: executor-backed dream invocation, prompt assembly, token persistence, failure handling.
- Create `runtime/daemon/dream_scheduler.py`: schedule/catch-up decisions, selected-agent resolution, enqueue-once logic.
- Create `runtime/daemon/routes/dreams.py`: founder-facing list/show/status/candidates routes plus agent callback route.
- Create `runtime/infrastructure/dream_store.py`: transcript rendering, atomic transcript writes.
- Create `cli/commands/dreams.py`: founder commands and agent callback command with `body_path` expansion.
- Create `protocol/skills/dream/SKILL.md`: agent-side dream callback instructions.
- Modify `runtime/models.py`: add dream models/enums.
- Modify `runtime/infrastructure/database.py`: schema, migrations, dream CRUD helpers, token scope support.
- Modify `runtime/infrastructure/audit_logger.py`: dream audit methods.
- Modify `runtime/orchestrator/org_config.py`: parse/validate `dreaming:` config.
- Modify `runtime/daemon/app.py`: include dreams router, start/cancel scheduler and dream workers, startup recovery.
- Modify `runtime/daemon/state.py`: wire dream queue when orgs are loaded after startup.
- Modify `cli/main.py`: register dreams command and re-export handlers.
- Modify `docs/agent-guides/features-and-invariants.md` and `docs/agent-guides/runtime-and-configuration.md`: current behavior notes after implementation.
- Modify OpenAPI snapshot and web API mirrors only for browser-callable founder routes.

Use `uv run python -m pytest ...` in this workspace. The `.venv/bin/pytest` console script currently points at an old interpreter path.

## Task 1: Dream Models And Database Tables

**Files:**
- Modify: `runtime/models.py`
- Modify: `runtime/infrastructure/database.py`
- Test: `tests/test_models.py`
- Test: `tests/test_database_dreams.py`

- [ ] **Step 1: Write failing model tests**

Add to `tests/test_models.py`:

```python
def test_dream_status_values() -> None:
    from runtime.models import DreamStatus

    assert DreamStatus.PENDING.value == "pending"
    assert DreamStatus.RUNNING.value == "running"
    assert DreamStatus.COMPLETED.value == "completed"
    assert DreamStatus.FAILED.value == "failed"
    assert DreamStatus.TIMEOUT.value == "timeout"
    assert DreamStatus.SKIPPED.value == "skipped"


def test_dream_record_defaults() -> None:
    from datetime import datetime, timezone
    from runtime.models import DreamRecord, DreamStatus

    rec = DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc),
    )

    assert rec.status == DreamStatus.PENDING
    assert rec.new_learnings_count == 0
    assert rec.kb_candidate_count == 0
    assert rec.founder_thread_id is None
```

- [ ] **Step 2: Write failing database tests**

Create `tests/test_database_dreams.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from runtime.infrastructure.database import Database
from runtime.models import DreamKbCandidate, DreamRecord, DreamStatus


def _dt(hour: int) -> datetime:
    return datetime(2026, 6, 9, hour, 0, tzinfo=timezone.utc)


def test_next_dream_id_and_insert_round_trip(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    assert db.next_dream_id() == "DREAM-001"

    rec = DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_start=_dt(1),
        window_end=_dt(2),
    )
    db.insert_dream(rec)

    got = db.get_dream("DREAM-001")
    assert got is not None
    assert got.id == "DREAM-001"
    assert got.agent_name == "dev_agent"
    assert got.status == DreamStatus.PENDING
    assert db.next_dream_id() == "DREAM-002"


def test_dream_unique_per_agent_local_date(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    rec = DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_end=_dt(2),
    )
    db.insert_dream(rec)

    with pytest.raises(Exception):
        db.insert_dream(rec.model_copy(update={"id": "DREAM-002"}))


def test_list_dreams_filters_and_newest_first(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-08",
        scheduled_for=_dt(1), window_end=_dt(1),
    ))
    db.insert_dream(DreamRecord(
        id="DREAM-002", agent_name="qa_engineer", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))

    assert [d.id for d in db.list_dreams()] == ["DREAM-002", "DREAM-001"]
    assert [d.id for d in db.list_dreams(agent="dev_agent")] == ["DREAM-001"]


def test_last_successful_dream_uses_completed_status(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-08",
        scheduled_for=_dt(1), window_end=_dt(1), status=DreamStatus.FAILED,
        ended_at=_dt(1),
    ))
    db.insert_dream(DreamRecord(
        id="DREAM-002", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2), status=DreamStatus.COMPLETED,
        ended_at=_dt(2),
    ))

    got = db.get_last_successful_dream("dev_agent")
    assert got is not None
    assert got.id == "DREAM-002"


def test_dream_kb_candidates_round_trip(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.insert_dream(DreamRecord(
        id="DREAM-001", agent_name="dev_agent", local_date="2026-06-09",
        scheduled_for=_dt(2), window_end=_dt(2),
    ))
    db.insert_dream_kb_candidate(DreamKbCandidate(
        dream_id="DREAM-001",
        agent_name="dev_agent",
        slug="candidate-one",
        title="Candidate One",
        topic="workflow",
        rationale="Observed repeatedly in task history.",
        body_markdown="Use this rule when it is promoted.\n",
    ))

    rows = db.list_dream_kb_candidates(dream_id="DREAM-001")
    assert len(rows) == 1
    assert rows[0].slug == "candidate-one"
    assert rows[0].status == "pending"
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
uv run python -m pytest tests/test_models.py::test_dream_status_values tests/test_models.py::test_dream_record_defaults tests/test_database_dreams.py -q
```

Expected: FAIL because `DreamStatus`, `DreamRecord`, `DreamKbCandidate`, and database dream methods do not exist.

- [ ] **Step 4: Add models**

In `runtime/models.py`, add after `TalkRecord`:

```python
class DreamStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


class DreamRecord(BaseModel):
    id: str
    agent_name: str
    local_date: str
    scheduled_for: datetime
    window_end: datetime
    window_start: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    status: DreamStatus = DreamStatus.PENDING
    summary: str | None = None
    transcript_path: str | None = None
    new_learnings_count: int = 0
    kb_candidate_count: int = 0
    founder_thread_id: str | None = None
    session_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_now)


class DreamKbCandidate(BaseModel):
    id: int | None = None
    dream_id: str
    agent_name: str
    slug: str
    title: str
    topic: str
    rationale: str
    body_markdown: str
    status: Literal["pending", "promoted", "rejected", "superseded"] = "pending"
    promoted_kb_slug: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
```

- [ ] **Step 5: Add database schema**

In `runtime/infrastructure/database.py`, add the two `CREATE TABLE` blocks and indexes inside schema initialization, after `talks`:

```python
            CREATE TABLE IF NOT EXISTS dreams (
                id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                local_date TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                window_start TEXT,
                window_end TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                summary TEXT,
                transcript_path TEXT,
                new_learnings_count INTEGER NOT NULL DEFAULT 0,
                kb_candidate_count INTEGER NOT NULL DEFAULT 0,
                founder_thread_id TEXT,
                session_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(agent_name, local_date)
            );
            CREATE INDEX IF NOT EXISTS idx_dreams_agent_date
                ON dreams(agent_name, local_date);
            CREATE INDEX IF NOT EXISTS idx_dreams_status
                ON dreams(status);

            CREATE TABLE IF NOT EXISTS dream_kb_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dream_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                topic TEXT NOT NULL,
                rationale TEXT NOT NULL,
                body_markdown TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                promoted_kb_slug TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(dream_id, slug),
                FOREIGN KEY (dream_id) REFERENCES dreams(id)
            );
            CREATE INDEX IF NOT EXISTS idx_dream_candidates_dream
                ON dream_kb_candidates(dream_id);
            CREATE INDEX IF NOT EXISTS idx_dream_candidates_status
                ON dream_kb_candidates(status);
```

- [ ] **Step 6: Add database helpers**

In `runtime/infrastructure/database.py`, import the new models and add helpers near the Talks section:

```python
    @_synchronized
    def next_dream_id(self) -> str:
        cursor = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 7) AS INTEGER)) AS m "
            "FROM dreams WHERE id GLOB 'DREAM-[0-9]*'"
        )
        n = (cursor.fetchone()["m"] or 0) + 1
        return f"DREAM-{n:03d}"

    def _dream_row_to_model(self, row) -> DreamRecord:
        return DreamRecord(
            id=row["id"],
            agent_name=row["agent_name"],
            local_date=row["local_date"],
            scheduled_for=_parse_dt(row["scheduled_for"]),
            window_start=_parse_dt(row["window_start"]) if row["window_start"] else None,
            window_end=_parse_dt(row["window_end"]),
            started_at=_parse_dt(row["started_at"]) if row["started_at"] else None,
            ended_at=_parse_dt(row["ended_at"]) if row["ended_at"] else None,
            status=DreamStatus(row["status"]),
            summary=row["summary"],
            transcript_path=row["transcript_path"],
            new_learnings_count=row["new_learnings_count"],
            kb_candidate_count=row["kb_candidate_count"],
            founder_thread_id=row["founder_thread_id"],
            session_id=row["session_id"],
            error=row["error"],
            created_at=_parse_dt(row["created_at"]),
        )

    @_synchronized
    def insert_dream(self, dream: DreamRecord) -> None:
        self._conn.execute(
            """INSERT INTO dreams (
                id, agent_name, local_date, scheduled_for, window_start, window_end,
                started_at, ended_at, status, summary, transcript_path,
                new_learnings_count, kb_candidate_count, founder_thread_id,
                session_id, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                dream.id, dream.agent_name, dream.local_date,
                dream.scheduled_for.isoformat(),
                dream.window_start.isoformat() if dream.window_start else None,
                dream.window_end.isoformat(),
                dream.started_at.isoformat() if dream.started_at else None,
                dream.ended_at.isoformat() if dream.ended_at else None,
                dream.status.value, dream.summary, dream.transcript_path,
                dream.new_learnings_count, dream.kb_candidate_count,
                dream.founder_thread_id, dream.session_id, dream.error,
                dream.created_at.isoformat(),
            ),
        )
        self._conn.commit()

    @_synchronized
    def get_dream(self, dream_id: str) -> DreamRecord | None:
        row = self._conn.execute("SELECT * FROM dreams WHERE id = ?", (dream_id,)).fetchone()
        return self._dream_row_to_model(row) if row else None

    @_synchronized
    def get_dream_for_agent_date(self, agent_name: str, local_date: str) -> DreamRecord | None:
        row = self._conn.execute(
            "SELECT * FROM dreams WHERE agent_name = ? AND local_date = ?",
            (agent_name, local_date),
        ).fetchone()
        return self._dream_row_to_model(row) if row else None

    @_synchronized
    def list_dreams(self, *, agent: str | None = None, limit: int = 50) -> list[DreamRecord]:
        limit = max(1, min(limit, 500))
        params: list[object] = []
        where = ""
        if agent is not None:
            where = "WHERE agent_name = ?"
            params.append(agent)
        rows = self._conn.execute(
            f"SELECT * FROM dreams {where} ORDER BY scheduled_for DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [self._dream_row_to_model(row) for row in rows]

    @_synchronized
    def get_last_successful_dream(self, agent_name: str) -> DreamRecord | None:
        row = self._conn.execute(
            "SELECT * FROM dreams WHERE agent_name = ? AND status = 'completed' "
            "ORDER BY ended_at DESC LIMIT 1",
            (agent_name,),
        ).fetchone()
        return self._dream_row_to_model(row) if row else None

    @_synchronized
    def update_dream(self, dream_id: str, **fields: object) -> None:
        allowed = {
            "started_at", "ended_at", "status", "summary", "transcript_path",
            "new_learnings_count", "kb_candidate_count", "founder_thread_id",
            "session_id", "error",
        }
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"unsupported dream fields: {sorted(bad)}")
        if not fields:
            return
        values = []
        assignments = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            if hasattr(value, "value"):
                value = value.value
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            values.append(value)
        values.append(dream_id)
        self._conn.execute(
            f"UPDATE dreams SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        self._conn.commit()
```

Add candidate helpers:

```python
    def _dream_candidate_row_to_model(self, row) -> DreamKbCandidate:
        return DreamKbCandidate(
            id=row["id"],
            dream_id=row["dream_id"],
            agent_name=row["agent_name"],
            slug=row["slug"],
            title=row["title"],
            topic=row["topic"],
            rationale=row["rationale"],
            body_markdown=row["body_markdown"],
            status=row["status"],
            promoted_kb_slug=row["promoted_kb_slug"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    @_synchronized
    def insert_dream_kb_candidate(self, candidate: DreamKbCandidate) -> None:
        self._conn.execute(
            """INSERT INTO dream_kb_candidates (
                dream_id, agent_name, slug, title, topic, rationale,
                body_markdown, status, promoted_kb_slug, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                candidate.dream_id, candidate.agent_name, candidate.slug,
                candidate.title, candidate.topic, candidate.rationale,
                candidate.body_markdown, candidate.status,
                candidate.promoted_kb_slug, candidate.created_at.isoformat(),
                candidate.updated_at.isoformat(),
            ),
        )
        self._conn.commit()

    @_synchronized
    def list_dream_kb_candidates(
        self, *, dream_id: str | None = None, agent: str | None = None,
    ) -> list[DreamKbCandidate]:
        clauses = []
        params: list[object] = []
        if dream_id is not None:
            clauses.append("dream_id = ?")
            params.append(dream_id)
        if agent is not None:
            clauses.append("agent_name = ?")
            params.append(agent)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM dream_kb_candidates {where} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [self._dream_candidate_row_to_model(row) for row in rows]
```

If `_parse_dt` does not exist in `database.py`, add this helper near other row converters:

```python
def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
```

- [ ] **Step 7: Run tests**

Run:

```bash
uv run python -m pytest tests/test_models.py::test_dream_status_values tests/test_models.py::test_dream_record_defaults tests/test_database_dreams.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add runtime/models.py runtime/infrastructure/database.py tests/test_models.py tests/test_database_dreams.py
git commit -m "feat(dreams): add dream persistence models"
```

## Task 2: Org Dreaming Config And Agent Selection

**Files:**
- Modify: `runtime/orchestrator/org_config.py`
- Create: `runtime/daemon/dream_scheduler.py`
- Test: `tests/test_org_config_dreaming.py`
- Test: `tests/daemon/test_dream_scheduler.py`

- [ ] **Step 1: Write failing config tests**

Create `tests/test_org_config_dreaming.py`:

```python
from __future__ import annotations

import pytest

from runtime.orchestrator.org_config import OrgConfig, OrgConfigError


def test_dreaming_missing_block_defaults_disabled() -> None:
    cfg = OrgConfig.load_from_text("")
    assert cfg.dreaming.enabled is False


def test_dreaming_full_block_parses() -> None:
    cfg = OrgConfig.load_from_text("""
dreaming:
  enabled: true
  schedule:
    time: "02:00"
    timezone: "Asia/Shanghai"
    catch_up_on_startup: true
  agents:
    mode: whitelist
    include: [dev_agent, qa_engineer]
    exclude: [qa_engineer]
""")
    assert cfg.dreaming.enabled is True
    assert cfg.dreaming.schedule_time == "02:00"
    assert cfg.dreaming.timezone == "Asia/Shanghai"
    assert cfg.dreaming.catch_up_on_startup is True
    assert cfg.dreaming.agent_mode == "whitelist"
    assert cfg.dreaming.include_agents == ["dev_agent", "qa_engineer"]
    assert cfg.dreaming.exclude_agents == ["qa_engineer"]


@pytest.mark.parametrize(
    "text,match",
    [
        ("dreaming: true\n", "dreaming must be a mapping"),
        ("dreaming:\n  enabled: yes\n", "dreaming.enabled must be a boolean"),
        ("dreaming:\n  enabled: true\n  schedule:\n    time: '2am'\n", "HH:MM"),
        ("dreaming:\n  enabled: true\n  schedule:\n    timezone: 42\n", "timezone must be a string"),
        ("dreaming:\n  enabled: true\n  agents:\n    mode: everyone\n", "mode must be one of"),
        ("dreaming:\n  enabled: true\n  agents:\n    include: dev_agent\n", "include must be a list"),
        ("dreaming:\n  enabled: true\n  agents:\n    exclude: [true]\n", "exclude entries must be strings"),
    ],
)
def test_dreaming_invalid_config_rejected(text: str, match: str) -> None:
    with pytest.raises(OrgConfigError, match=match):
        OrgConfig.load_from_text(text)
```

- [ ] **Step 2: Write failing selection tests**

Create `tests/daemon/test_dream_scheduler.py`:

```python
from __future__ import annotations

from runtime.daemon.dream_scheduler import select_dream_agents
from runtime.orchestrator.org_config import DreamingConfig


def test_select_dream_agents_all_with_exclude() -> None:
    cfg = DreamingConfig(
        enabled=True,
        agent_mode="all",
        include_agents=[],
        exclude_agents=["qa_engineer"],
    )
    assert select_dream_agents(
        available_agents=["dev_agent", "qa_engineer", "ops_manager"],
        config=cfg,
    ) == ["dev_agent", "ops_manager"]


def test_select_dream_agents_whitelist_then_exclude() -> None:
    cfg = DreamingConfig(
        enabled=True,
        agent_mode="whitelist",
        include_agents=["qa_engineer", "dev_agent"],
        exclude_agents=["qa_engineer"],
    )
    assert select_dream_agents(
        available_agents=["dev_agent", "qa_engineer", "ops_manager"],
        config=cfg,
    ) == ["dev_agent"]


def test_select_dream_agents_disabled() -> None:
    cfg = DreamingConfig(enabled=False)
    assert select_dream_agents(["dev_agent"], cfg) == []
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_org_config_dreaming.py tests/daemon/test_dream_scheduler.py::test_select_dream_agents_all_with_exclude -q
```

Expected: FAIL because `DreamingConfig` and `select_dream_agents` do not exist.

- [ ] **Step 4: Add config dataclass and parser**

In `runtime/orchestrator/org_config.py`, add:

```python
@dataclass(frozen=True)
class DreamingConfig:
    enabled: bool = False
    schedule_time: str = "02:00"
    timezone: str = "UTC"
    catch_up_on_startup: bool = True
    agent_mode: str = "all"
    include_agents: list[str] = None
    exclude_agents: list[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "include_agents", list(self.include_agents or []))
        object.__setattr__(self, "exclude_agents", list(self.exclude_agents or []))
```

Add field to `OrgConfig`:

```python
    dreaming: DreamingConfig = field(default_factory=DreamingConfig)
```

Import `field`:

```python
from dataclasses import dataclass, field
```

Add parser helpers:

```python
def _validate_agent_list(value: object, name: str, path: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise OrgConfigError(f"{path}: dreaming.agents.{name} must be a list")
    if not all(isinstance(item, str) for item in value):
        raise OrgConfigError(f"{path}: dreaming.agents.{name} entries must be strings")
    return list(value)


def _parse_dreaming(block: dict, path: str) -> DreamingConfig:
    if not isinstance(block, dict):
        raise OrgConfigError(f"{path}: dreaming must be a mapping")

    enabled = block.get("enabled", False)
    if not isinstance(enabled, bool):
        raise OrgConfigError(f"{path}: dreaming.enabled must be a boolean")

    schedule = block.get("schedule", {})
    if schedule is None:
        schedule = {}
    if not isinstance(schedule, dict):
        raise OrgConfigError(f"{path}: dreaming.schedule must be a mapping")
    schedule_time = schedule.get("time", "02:00")
    if not isinstance(schedule_time, str) or not re.match(r"^[0-2][0-9]:[0-5][0-9]$", schedule_time):
        raise OrgConfigError(f"{path}: dreaming.schedule.time must be HH:MM")
    hour = int(schedule_time[:2])
    if hour > 23:
        raise OrgConfigError(f"{path}: dreaming.schedule.time must be HH:MM")
    timezone = schedule.get("timezone", "UTC")
    if not isinstance(timezone, str):
        raise OrgConfigError(f"{path}: dreaming.schedule.timezone must be a string")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise OrgConfigError(f"{path}: unknown dreaming.schedule.timezone {timezone!r}") from exc
    catch_up = schedule.get("catch_up_on_startup", True)
    if not isinstance(catch_up, bool):
        raise OrgConfigError(f"{path}: dreaming.schedule.catch_up_on_startup must be a boolean")

    agents = block.get("agents", {})
    if agents is None:
        agents = {}
    if not isinstance(agents, dict):
        raise OrgConfigError(f"{path}: dreaming.agents must be a mapping")
    mode = agents.get("mode", "all")
    if mode not in {"all", "whitelist"}:
        raise OrgConfigError(f"{path}: dreaming.agents.mode must be one of ['all', 'whitelist']")

    return DreamingConfig(
        enabled=enabled,
        schedule_time=schedule_time,
        timezone=timezone,
        catch_up_on_startup=catch_up,
        agent_mode=mode,
        include_agents=_validate_agent_list(agents.get("include"), "include", path),
        exclude_agents=_validate_agent_list(agents.get("exclude"), "exclude", path),
    )
```

Add imports:

```python
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
```

In `_build_org_config`, parse the block:

```python
    dreaming_block = data.get("dreaming")
    dreaming_cfg = DreamingConfig()
    if dreaming_block is not None:
        dreaming_cfg = _parse_dreaming(dreaming_block, path)
```

Pass it into `OrgConfig(...)`:

```python
        dreaming=dreaming_cfg,
```

- [ ] **Step 5: Add selection helper**

Create `runtime/daemon/dream_scheduler.py`:

```python
"""Nightly dream scheduling decisions."""
from __future__ import annotations

from runtime.orchestrator.org_config import DreamingConfig


def select_dream_agents(
    available_agents: list[str],
    config: DreamingConfig,
) -> list[str]:
    if not config.enabled:
        return []

    available = list(dict.fromkeys(available_agents))
    available_set = set(available)

    if config.agent_mode == "whitelist":
        selected = [name for name in config.include_agents if name in available_set]
    else:
        selected = available

    excluded = set(config.exclude_agents)
    return [name for name in selected if name not in excluded]
```

- [ ] **Step 6: Run tests**

```bash
uv run python -m pytest tests/test_org_config_dreaming.py tests/daemon/test_dream_scheduler.py::test_select_dream_agents_all_with_exclude tests/daemon/test_dream_scheduler.py::test_select_dream_agents_whitelist_then_exclude tests/daemon/test_dream_scheduler.py::test_select_dream_agents_disabled -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add runtime/orchestrator/org_config.py runtime/daemon/dream_scheduler.py tests/test_org_config_dreaming.py tests/daemon/test_dream_scheduler.py
git commit -m "feat(dreams): parse nightly dreaming config"
```

## Task 3: Dream Scheduling Decisions

**Files:**
- Modify: `runtime/daemon/dream_scheduler.py`
- Test: `tests/daemon/test_dream_scheduler.py`

- [ ] **Step 1: Add failing schedule tests**

Append to `tests/daemon/test_dream_scheduler.py`:

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from runtime.daemon.dream_scheduler import should_schedule_for_agent
from runtime.models import DreamRecord, DreamStatus


def test_should_schedule_after_local_time_when_no_row() -> None:
    now = datetime(2026, 6, 9, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    decision = should_schedule_for_agent(
        agent_name="dev_agent",
        now=now,
        config=DreamingConfig(enabled=True, schedule_time="02:00", timezone="Asia/Shanghai"),
        existing_for_date=None,
    )
    assert decision.should_schedule is True
    assert decision.local_date == "2026-06-09"
    assert decision.scheduled_for.isoformat().startswith("2026-06-09T02:00:00")


def test_should_not_schedule_before_local_time() -> None:
    now = datetime(2026, 6, 9, 1, 59, tzinfo=ZoneInfo("Asia/Shanghai"))
    decision = should_schedule_for_agent(
        agent_name="dev_agent",
        now=now,
        config=DreamingConfig(enabled=True, schedule_time="02:00", timezone="Asia/Shanghai"),
        existing_for_date=None,
    )
    assert decision.should_schedule is False
    assert decision.reason == "not_due"


def test_should_not_schedule_when_row_exists() -> None:
    now = datetime(2026, 6, 9, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    existing = DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=now,
        window_end=now,
        status=DreamStatus.FAILED,
    )
    decision = should_schedule_for_agent(
        agent_name="dev_agent",
        now=now,
        config=DreamingConfig(enabled=True, schedule_time="02:00", timezone="Asia/Shanghai"),
        existing_for_date=existing,
    )
    assert decision.should_schedule is False
    assert decision.reason == "already_exists"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/daemon/test_dream_scheduler.py -q
```

Expected: FAIL because `should_schedule_for_agent` does not exist.

- [ ] **Step 3: Implement schedule decision**

Add to `runtime/daemon/dream_scheduler.py`:

```python
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from runtime.models import DreamRecord


@dataclass(frozen=True)
class DreamScheduleDecision:
    should_schedule: bool
    local_date: str
    scheduled_for: datetime
    reason: str | None = None


def _scheduled_datetime(now: datetime, config: DreamingConfig) -> tuple[str, datetime]:
    tz = ZoneInfo(config.timezone)
    local_now = now.astimezone(tz)
    hour, minute = [int(part) for part in config.schedule_time.split(":", 1)]
    scheduled = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return scheduled.date().isoformat(), scheduled


def should_schedule_for_agent(
    *,
    agent_name: str,
    now: datetime,
    config: DreamingConfig,
    existing_for_date: DreamRecord | None,
) -> DreamScheduleDecision:
    local_date, scheduled = _scheduled_datetime(now, config)
    if existing_for_date is not None:
        return DreamScheduleDecision(False, local_date, scheduled, "already_exists")
    if now.astimezone(scheduled.tzinfo) < scheduled:
        return DreamScheduleDecision(False, local_date, scheduled, "not_due")
    return DreamScheduleDecision(True, local_date, scheduled, None)
```

- [ ] **Step 4: Run tests**

```bash
uv run python -m pytest tests/daemon/test_dream_scheduler.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/daemon/dream_scheduler.py tests/daemon/test_dream_scheduler.py
git commit -m "feat(dreams): add schedule decision logic"
```

## Task 4: Dream Transcript Store

**Files:**
- Create: `runtime/infrastructure/dream_store.py`
- Test: `tests/test_dream_store.py`

- [ ] **Step 1: Write failing store tests**

Create `tests/test_dream_store.py`:

```python
from __future__ import annotations

from runtime.infrastructure.dream_store import DreamStore


def test_write_and_read_dream_transcript(tmp_path):
    store = DreamStore(tmp_path / "dreams")
    path = store.write_transcript(
        dream_id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        window_start="2026-06-08T02:00:00+00:00",
        window_end="2026-06-09T02:00:00+00:00",
        summary="Found recurring friction.",
        transcript_markdown="Full private transcript.\n",
        new_learnings_count=1,
        kb_candidate_count=2,
        founder_thread_id="THR-001",
    )

    assert path == tmp_path / "dreams" / "DREAM-001.md"
    text = store.read_transcript("DREAM-001")
    assert "dream_id: DREAM-001" in text
    assert "agent_name: dev_agent" in text
    assert "# Summary" in text
    assert "Found recurring friction." in text
    assert "# Transcript" in text
    assert "Full private transcript." in text
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run python -m pytest tests/test_dream_store.py -q
```

Expected: FAIL because `runtime.infrastructure.dream_store` does not exist.

- [ ] **Step 3: Implement store**

Create `runtime/infrastructure/dream_store.py`:

```python
"""Filesystem writes for private dream transcripts."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml


_MAX_TRANSCRIPT_BYTES = 1024 * 1024


class InvalidDreamTranscript(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class DreamStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)

    def path_for(self, dream_id: str) -> Path:
        return self._root / f"{dream_id}.md"

    def write_transcript(
        self,
        *,
        dream_id: str,
        agent_name: str,
        local_date: str,
        window_start: str | None,
        window_end: str,
        summary: str,
        transcript_markdown: str,
        new_learnings_count: int,
        kb_candidate_count: int,
        founder_thread_id: str | None,
    ) -> Path:
        body = self._format(
            dream_id=dream_id,
            agent_name=agent_name,
            local_date=local_date,
            window_start=window_start,
            window_end=window_end,
            summary=summary,
            transcript_markdown=transcript_markdown,
            new_learnings_count=new_learnings_count,
            kb_candidate_count=kb_candidate_count,
            founder_thread_id=founder_thread_id,
        )
        encoded = body.encode("utf-8")
        if len(encoded) > _MAX_TRANSCRIPT_BYTES:
            raise InvalidDreamTranscript(
                "transcript_too_large",
                f"transcript is {len(encoded)} bytes, max {_MAX_TRANSCRIPT_BYTES}",
            )
        target = self.path_for(dream_id)
        fd, tmp_name = tempfile.mkstemp(dir=self._root, prefix=f".{dream_id}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(encoded)
            os.replace(tmp_name, target)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return target

    def read_transcript(self, dream_id: str) -> str:
        return self.path_for(dream_id).read_text(encoding="utf-8")

    def _format(
        self,
        *,
        dream_id: str,
        agent_name: str,
        local_date: str,
        window_start: str | None,
        window_end: str,
        summary: str,
        transcript_markdown: str,
        new_learnings_count: int,
        kb_candidate_count: int,
        founder_thread_id: str | None,
    ) -> str:
        frontmatter = {
            "dream_id": dream_id,
            "agent_name": agent_name,
            "local_date": local_date,
            "window_start": window_start,
            "window_end": window_end,
            "new_learnings_count": new_learnings_count,
            "kb_candidate_count": kb_candidate_count,
            "founder_thread_id": founder_thread_id,
        }
        fm_text = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        return (
            "---\n"
            f"{fm_text}\n"
            "---\n\n"
            "# Summary\n\n"
            f"{summary}\n\n"
            "# Transcript\n\n"
            f"{transcript_markdown}\n"
        )
```

- [ ] **Step 4: Run tests**

```bash
uv run python -m pytest tests/test_dream_store.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add runtime/infrastructure/dream_store.py tests/test_dream_store.py
git commit -m "feat(dreams): add private transcript store"
```

## Task 5: CLI Dreams Command And Body Path Expansion

**Files:**
- Create: `cli/commands/dreams.py`
- Modify: `cli/main.py`
- Test: `tests/test_cli_dreams.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_cli_dreams.py`:

```python
from __future__ import annotations

import argparse
import json

from cli.commands import dreams


class FakeResponse:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def json(self):
        return self._body


class FakeClient:
    def __init__(self):
        self.posts = []
        self.gets = []

    def post(self, path, json=None, params=None):
        self.posts.append((path, json, params))
        return FakeResponse({"dream_id": "DREAM-001", "status": "completed"})

    def get(self, path, params=None):
        self.gets.append((path, params))
        return FakeResponse({"dreams": []})


def test_expand_complete_payload_reads_kb_candidate_body(tmp_path):
    body_path = tmp_path / "candidate.md"
    body_path.write_text("Candidate body.\n")
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps({
        "summary": "s",
        "learnings": [],
        "kb_candidates": [{
            "slug": "candidate",
            "title": "Candidate",
            "topic": "workflow",
            "rationale": "Repeated pattern.",
            "body_path": str(body_path),
        }],
        "founder_thread": {"needed": False},
    }))

    expanded = dreams._complete_payload_from_file(str(payload_path))

    assert expanded["kb_candidates"][0]["body_markdown"] == "Candidate body.\n"
    assert "body_path" not in expanded["kb_candidates"][0]


def test_cmd_dreams_complete_posts_expanded_payload(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr(dreams.OpcClient, "from_env", lambda: client)
    body_path = tmp_path / "candidate.md"
    body_path.write_text("Candidate body.\n")
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps({
        "summary": "s",
        "learnings": [],
        "kb_candidates": [{
            "slug": "candidate",
            "title": "Candidate",
            "topic": "workflow",
            "rationale": "Repeated pattern.",
            "body_path": str(body_path),
        }],
        "founder_thread": {"needed": False},
    }))

    dreams.cmd_dreams_complete(argparse.Namespace(
        org="myorg", dream_id="DREAM-001", from_file=str(payload_path),
    ))

    assert client.posts[0][0] == "/api/v1/orgs/myorg/dreams/DREAM-001/complete"
    assert client.posts[0][1]["kb_candidates"][0]["body_markdown"] == "Candidate body.\n"


def test_dreams_command_registered():
    from cli.main import build_parser

    parser = build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    assert "dreams" in sub.choices
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/test_cli_dreams.py -q
```

Expected: FAIL because `cli.commands.dreams` does not exist.

- [ ] **Step 3: Implement CLI command module**

Create `cli/commands/dreams.py`:

```python
"""Dreaming commands."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cli import _shared
from cli._shared import _fmt_ts, _ok, resolve_org_slug
from cli.client.client import OpcClient


def _complete_payload_from_file(path: str) -> dict:
    try:
        body = json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        print(f"Error reading {path}: {exc}", file=sys.stderr)
        sys.exit(1)
    if not isinstance(body, dict):
        print("error: dream completion payload must be a JSON object", file=sys.stderr)
        sys.exit(1)

    expanded = dict(body)
    candidates = []
    for candidate in expanded.get("kb_candidates", []) or []:
        if not isinstance(candidate, dict):
            print("error: kb_candidates entries must be objects", file=sys.stderr)
            sys.exit(1)
        item = dict(candidate)
        body_path = item.pop("body_path", None)
        if body_path is None:
            print("error: kb_candidates[].body_path is required", file=sys.stderr)
            sys.exit(1)
        try:
            item["body_markdown"] = Path(body_path).read_text()
        except OSError as exc:
            print(f"Error reading {body_path}: {exc}", file=sys.stderr)
            sys.exit(1)
        candidates.append(item)
    expanded["kb_candidates"] = candidates
    return expanded


def _client_and_org(args: argparse.Namespace) -> tuple[OpcClient, str]:
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org,
        available=_shared._fetch_available_orgs(client),
    )
    return client, slug


def cmd_dreams_complete(args: argparse.Namespace) -> None:
    if not args.org:
        print("error: --org <slug> is required for agent callbacks", file=sys.stderr)
        sys.exit(1)
    client = OpcClient.from_env()
    body = _complete_payload_from_file(args.from_file)
    r = client.post(
        f"/api/v1/orgs/{args.org}/dreams/{args.dream_id}/complete",
        json=body,
    )
    if not _ok(r):
        return
    resp = r.json()
    print(f"ok: completed {resp['dream_id']} status={resp['status']}")


def cmd_dreams_status(args: argparse.Namespace) -> None:
    client, slug = _client_and_org(args)
    params = {}
    if args.agent:
        params["agent"] = args.agent
    r = client.get(f"/api/v1/orgs/{slug}/dreams/status", params=params)
    if not _ok(r):
        return
    print(json.dumps(r.json(), indent=2))


def cmd_dreams_list(args: argparse.Namespace) -> None:
    client, slug = _client_and_org(args)
    params = {"limit": args.limit}
    if args.agent:
        params["agent"] = args.agent
    r = client.get(f"/api/v1/orgs/{slug}/dreams", params=params)
    if not _ok(r):
        return
    dreams = r.json().get("dreams", [])
    if args.json:
        print(json.dumps(dreams, indent=2))
        return
    if not dreams:
        print("(no dreams)")
        return
    for d in dreams:
        print(
            f"{d['dream_id']:10s}  {d['status']:10s}  {d['agent_name']:20s}  "
            f"{_fmt_ts(d.get('ended_at') or d['scheduled_for'])}  "
            f"learnings={d['new_learnings_count']} candidates={d['kb_candidate_count']}"
        )


def cmd_dreams_show(args: argparse.Namespace) -> None:
    client, slug = _client_and_org(args)
    r = client.get(f"/api/v1/orgs/{slug}/dreams/{args.dream_id}")
    if not _ok(r):
        return
    body = r.json()
    if args.json:
        print(json.dumps(body, indent=2))
        return
    print(f"# {body['dream_id']} - {body['agent_name']}")
    print(f"status={body['status']} scheduled={_fmt_ts(body['scheduled_for'])}")
    if body.get("summary"):
        print("\n## Summary\n")
        print(body["summary"])
    if body.get("kb_candidates"):
        print("\n## KB Candidates\n")
        for c in body["kb_candidates"]:
            print(f"- {c['id']} `{c['slug']}` [{c['topic']}] {c['title']}")


def register(sub) -> None:
    p = sub.add_parser("dreams", help="Nightly private agent reflection")
    dream_sub = p.add_subparsers(dest="dream_command", required=True)

    p_complete = dream_sub.add_parser("complete", help="Agent callback: complete a dream")
    p_complete.add_argument("--org", required=True)
    p_complete.add_argument("--dream-id", required=True)
    p_complete.add_argument("--from-file", required=True)
    p_complete.set_defaults(func=cmd_dreams_complete)

    p_status = dream_sub.add_parser("status", help="Show dream scheduler status")
    p_status.add_argument("--org", default=None)
    p_status.add_argument("--agent")
    p_status.set_defaults(func=cmd_dreams_status)

    p_list = dream_sub.add_parser("list", help="List recent dreams")
    p_list.add_argument("--org", default=None)
    p_list.add_argument("--agent")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.add_argument("--json", action="store_true")
    p_list.set_defaults(func=cmd_dreams_list)

    p_show = dream_sub.add_parser("show", help="Show a dream")
    p_show.add_argument("--org", default=None)
    p_show.add_argument("dream_id")
    p_show.add_argument("--json", action="store_true")
    p_show.set_defaults(func=cmd_dreams_show)
```

- [ ] **Step 4: Register CLI module**

In `cli/main.py`, add `dreams` to `from cli.commands import (...)` and call `dreams.register(sub)` in `build_parser()` after `artifacts.register(sub)`.

Add re-exports:

```python
from cli.commands.dreams import (  # noqa: F401
    _complete_payload_from_file,
    cmd_dreams_complete,
    cmd_dreams_list,
    cmd_dreams_show,
    cmd_dreams_status,
)
```

- [ ] **Step 5: Run tests**

```bash
uv run python -m pytest tests/test_cli_dreams.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add cli/commands/dreams.py cli/main.py tests/test_cli_dreams.py
git commit -m "feat(cli): add dreams commands"
```

## Task 6: Dream Routes Completion, List, Show, Status

**Files:**
- Create: `runtime/daemon/routes/dreams.py`
- Modify: `runtime/daemon/app.py`
- Modify: `runtime/infrastructure/audit_logger.py`
- Test: `tests/daemon/test_dreams_routes.py`

- [ ] **Step 1: Write failing route tests**

Create `tests/daemon/test_dreams_routes.py` using the existing daemon test client fixture style:

```python
from __future__ import annotations

from datetime import datetime, timezone

from runtime.models import DreamRecord, DreamStatus


def _dt(hour: int) -> datetime:
    return datetime(2026, 6, 9, hour, 0, tzinfo=timezone.utc)


def test_complete_dream_persists_outputs(client, org):
    org.db.insert_dream(DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_start=_dt(1),
        window_end=_dt(2),
        status=DreamStatus.RUNNING,
    ))
    (org.root / "workspaces" / "dev_agent" / "learnings").mkdir(parents=True, exist_ok=True)

    resp = client.post("/api/v1/orgs/test/dreams/DREAM-001/complete", json={
        "summary": "Private summary.",
        "learnings": [{
            "slug": "dream-learning",
            "title": "Dream learning",
            "topic": "workflow",
            "body": "Private durable learning.\n",
        }],
        "kb_candidates": [{
            "slug": "candidate-one",
            "title": "Candidate One",
            "topic": "workflow",
            "rationale": "Repeated pattern.",
            "body_markdown": "Candidate body.\n",
        }],
        "founder_thread": {"needed": False},
    })

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dream_id"] == "DREAM-001"
    assert body["status"] == "completed"

    dream = org.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.COMPLETED
    assert dream.new_learnings_count == 1
    assert dream.kb_candidate_count == 1
    assert dream.transcript_path
    assert org.db.list_dream_kb_candidates(dream_id="DREAM-001")[0].slug == "candidate-one"


def test_complete_dream_creates_founder_thread(client, org):
    org.db.insert_dream(DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_end=_dt(2),
        status=DreamStatus.RUNNING,
    ))
    (org.root / "workspaces" / "dev_agent" / "learnings").mkdir(parents=True, exist_ok=True)

    resp = client.post("/api/v1/orgs/test/dreams/DREAM-001/complete", json={
        "summary": "Private summary.",
        "learnings": [],
        "kb_candidates": [],
        "founder_thread": {
            "needed": True,
            "subject": "Nightly reflection: dev_agent",
            "body_markdown": "Founder-visible finding.",
        },
    })

    assert resp.status_code == 200, resp.text
    dream = org.db.get_dream("DREAM-001")
    assert dream.founder_thread_id is not None
    thread = org.db.get_thread(dream.founder_thread_id)
    assert thread is not None
    assert org.db.list_thread_participants(thread.id) == []


def test_list_and_show_dreams(client, org):
    org.db.insert_dream(DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_end=_dt(2),
    ))

    list_resp = client.get("/api/v1/orgs/test/dreams")
    assert list_resp.status_code == 200
    assert list_resp.json()["dreams"][0]["dream_id"] == "DREAM-001"

    show_resp = client.get("/api/v1/orgs/test/dreams/DREAM-001")
    assert show_resp.status_code == 200
    assert show_resp.json()["dream_id"] == "DREAM-001"
```

If the local fixture names are different, adapt the test to match `tests/daemon/conftest.py` while preserving the assertions.

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/daemon/test_dreams_routes.py -q
```

Expected: FAIL because dreams routes are not registered.

- [ ] **Step 3: Add audit methods**

In `runtime/infrastructure/audit_logger.py`, add:

```python
    def log_dream_scheduled(self, dream_id: str, agent: str, *, local_date: str) -> None:
        self.log(dream_id, agent, "dream_scheduled", {"local_date": local_date})

    def log_dream_started(self, dream_id: str, agent: str) -> None:
        self.log(dream_id, agent, "dream_started", {})

    def log_dream_completed(
        self,
        dream_id: str,
        agent: str,
        *,
        new_learnings_count: int,
        kb_candidate_count: int,
        founder_thread_id: str | None,
    ) -> None:
        self.log(dream_id, agent, "dream_completed", {
            "new_learnings_count": new_learnings_count,
            "kb_candidate_count": kb_candidate_count,
            "founder_thread_id": founder_thread_id,
        })

    def log_dream_failed(self, dream_id: str, agent: str, *, reason: str) -> None:
        self.log(dream_id, agent, "dream_failed", {"reason": reason})
```

- [ ] **Step 4: Implement routes**

Create `runtime/daemon/routes/dreams.py`:

```python
"""Dream endpoints: private scheduled agent reflection."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from runtime.daemon.auth import require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.dream_store import DreamStore
from runtime.infrastructure.learnings_store import LearningEntry, LearningsStore
from runtime.models import DreamKbCandidate, DreamStatus, ThreadMessageKind, ThreadRecord

router = APIRouter(dependencies=[require_token()])


def _store(org) -> DreamStore:
    return DreamStore(org.root / "dreams")


class DreamLearningBody(BaseModel):
    slug: str
    title: str
    topic: str
    body: str


class DreamKbCandidateBody(BaseModel):
    slug: str
    title: str
    topic: str
    rationale: str
    body_markdown: str


class FounderThreadBody(BaseModel):
    needed: bool = False
    subject: str | None = None
    body_markdown: str | None = None


class DreamCompleteBody(BaseModel):
    summary: str = Field(min_length=1)
    learnings: list[DreamLearningBody] = []
    kb_candidates: list[DreamKbCandidateBody] = []
    founder_thread: FounderThreadBody = Field(default_factory=FounderThreadBody)


def _dream_to_dict(dream, *, candidates=None, transcript: str | None = None) -> dict:
    data = {
        "dream_id": dream.id,
        "agent_name": dream.agent_name,
        "local_date": dream.local_date,
        "scheduled_for": dream.scheduled_for.isoformat(),
        "window_start": dream.window_start.isoformat() if dream.window_start else None,
        "window_end": dream.window_end.isoformat(),
        "started_at": dream.started_at.isoformat() if dream.started_at else None,
        "ended_at": dream.ended_at.isoformat() if dream.ended_at else None,
        "status": dream.status.value,
        "summary": dream.summary,
        "transcript_path": dream.transcript_path,
        "new_learnings_count": dream.new_learnings_count,
        "kb_candidate_count": dream.kb_candidate_count,
        "founder_thread_id": dream.founder_thread_id,
        "error": dream.error,
    }
    if candidates is not None:
        data["kb_candidates"] = [
            {
                "id": c.id,
                "dream_id": c.dream_id,
                "agent_name": c.agent_name,
                "slug": c.slug,
                "title": c.title,
                "topic": c.topic,
                "rationale": c.rationale,
                "status": c.status,
                "promoted_kb_slug": c.promoted_kb_slug,
            }
            for c in candidates
        ]
    if transcript is not None:
        data["transcript"] = transcript
    return data


@router.get("/dreams/status")
def dream_status(slug: str, org: OrgDep, agent: str | None = None) -> dict:
    dreams = org.db.list_dreams(agent=agent, limit=20)
    return {"recent": [_dream_to_dict(d) for d in dreams]}


@router.get("/dreams")
def list_dreams(slug: str, org: OrgDep, agent: str | None = None, limit: int = 50) -> dict:
    return {"dreams": [_dream_to_dict(d) for d in org.db.list_dreams(agent=agent, limit=limit)]}


@router.get("/dreams/{dream_id}")
def show_dream(slug: str, dream_id: str, org: OrgDep) -> dict:
    dream = org.db.get_dream(dream_id)
    if dream is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "dream_id": dream_id})
    candidates = org.db.list_dream_kb_candidates(dream_id=dream_id)
    transcript = None
    if dream.transcript_path:
        try:
            transcript = _store(org).read_transcript(dream_id)
        except FileNotFoundError:
            transcript = None
    return _dream_to_dict(dream, candidates=candidates, transcript=transcript)


@router.post("/dreams/{dream_id}/complete")
async def complete_dream(slug: str, dream_id: str, body: DreamCompleteBody, org: OrgDep, request: Request) -> dict:
    if body.founder_thread.needed:
        if not (body.founder_thread.subject or "").strip():
            raise HTTPException(status_code=422, detail={"code": "empty_thread_subject"})
        if not (body.founder_thread.body_markdown or "").strip():
            raise HTTPException(status_code=422, detail={"code": "empty_thread_body"})

    async with org.db_lock:
        dream = org.db.get_dream(dream_id)
        if dream is None:
            raise HTTPException(status_code=404, detail={"code": "not_found", "dream_id": dream_id})
        if dream.status != DreamStatus.RUNNING:
            raise HTTPException(status_code=400, detail={"code": "dream_not_running", "status": dream.status.value})

        founder_thread_id = None
        if body.founder_thread.needed:
            founder_thread_id = org.db.next_thread_id()
            org.db.insert_thread(ThreadRecord(
                id=founder_thread_id,
                subject=body.founder_thread.subject.strip(),
                composed_by=dream.agent_name,
            ))
            org.db.append_thread_message(
                thread_id=founder_thread_id,
                speaker=dream.agent_name,
                kind=ThreadMessageKind.MESSAGE,
                body_markdown=body.founder_thread.body_markdown.strip(),
            )

        learnings_dir = org.root / "workspaces" / dream.agent_name / "learnings"
        learnings_dir.mkdir(parents=True, exist_ok=True)
        store = LearningsStore(learnings_dir)
        for learning in body.learnings:
            store.write_entry(LearningEntry(
                id=store.next_id(),
                slug=learning.slug,
                title=learning.title,
                topic=learning.topic,
                body=learning.body if learning.body.endswith("\n") else learning.body + "\n",
                source_task=dream_id,
            ), agent=dream.agent_name)
        if body.learnings:
            store.regenerate_index()

        for candidate in body.kb_candidates:
            org.db.insert_dream_kb_candidate(DreamKbCandidate(
                dream_id=dream_id,
                agent_name=dream.agent_name,
                slug=candidate.slug,
                title=candidate.title,
                topic=candidate.topic,
                rationale=candidate.rationale,
                body_markdown=candidate.body_markdown,
            ))

        now = datetime.now(timezone.utc)
        transcript_path = _store(org).write_transcript(
            dream_id=dream_id,
            agent_name=dream.agent_name,
            local_date=dream.local_date,
            window_start=dream.window_start.isoformat() if dream.window_start else None,
            window_end=dream.window_end.isoformat(),
            summary=body.summary,
            transcript_markdown=body.summary,
            new_learnings_count=len(body.learnings),
            kb_candidate_count=len(body.kb_candidates),
            founder_thread_id=founder_thread_id,
        )
        org.db.update_dream(
            dream_id,
            status=DreamStatus.COMPLETED,
            ended_at=now,
            summary=body.summary,
            transcript_path=str(transcript_path),
            new_learnings_count=len(body.learnings),
            kb_candidate_count=len(body.kb_candidates),
            founder_thread_id=founder_thread_id,
        )

    AuditLogger(org.db).log_dream_completed(
        dream_id,
        dream.agent_name,
        new_learnings_count=len(body.learnings),
        kb_candidate_count=len(body.kb_candidates),
        founder_thread_id=founder_thread_id,
    )
    return {"dream_id": dream_id, "status": "completed", "founder_thread_id": founder_thread_id}
```

- [ ] **Step 5: Register route**

In `runtime/daemon/app.py`, import `dreams` in the routes tuple and add:

```python
    app.include_router(dreams.router, prefix="/api/v1/orgs/{slug}", tags=["dreams"])
```

- [ ] **Step 6: Run route tests**

```bash
uv run python -m pytest tests/daemon/test_dreams_routes.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add runtime/daemon/routes/dreams.py runtime/daemon/app.py runtime/infrastructure/audit_logger.py tests/daemon/test_dreams_routes.py
git commit -m "feat(dreams): add dream API routes"
```

## Task 7: Dream Queue, Runner, Prompt, And Token Scope

**Files:**
- Create: `runtime/daemon/dream_queue.py`
- Create: `runtime/daemon/dream_runner.py`
- Modify: `runtime/infrastructure/database.py`
- Test: `tests/daemon/test_dream_runner.py`
- Test: `tests/test_session_token_usage_db.py`

- [ ] **Step 1: Write failing token scope test**

Add to `tests/test_session_token_usage_db.py`:

```python
def test_insert_token_usage_supports_dream_scope(db):
    from runtime.models import TokenUsage

    db.insert_session_token_usage(
        task_id=None,
        agent="dev_agent",
        session_id="dream-session",
        executor="claude",
        usage=TokenUsage(input_tokens=10, output_tokens=5, model="test"),
        scope_type="dream",
        scope_id="DREAM-001",
    )

    rows = db.list_session_token_usage(scope_type="dream", scope_id="DREAM-001")
    assert len(rows) == 1
    assert rows[0]["scope_type"] == "dream"
    assert rows[0]["scope_id"] == "DREAM-001"
```

If the helper names differ, adjust to the existing token usage API while preserving the dream `scope_type` assertion.

- [ ] **Step 2: Write failing runner tests**

Create `tests/daemon/test_dream_runner.py`:

```python
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from runtime.daemon.dream_runner import build_dream_prompt, run_dream
from runtime.models import DreamRecord, DreamStatus


def _dt(hour: int) -> datetime:
    return datetime(2026, 6, 9, hour, 0, tzinfo=timezone.utc)


def test_build_dream_prompt_contains_private_contract(tmp_path):
    prompt = build_dream_prompt(
        org_slug="test",
        dream=DreamRecord(
            id="DREAM-001",
            agent_name="dev_agent",
            local_date="2026-06-09",
            scheduled_for=_dt(2),
            window_start=_dt(1),
            window_end=_dt(2),
        ),
        workspace=tmp_path,
        recent_audit=[],
        task_history="TASK-001 completed\n",
    )

    assert "private reflection" in prompt
    assert "happyranch dreams complete" in prompt
    assert "DREAM-001" in prompt
    assert "TASK-001 completed" in prompt


class FakeResult:
    success = True
    error = None
    session_id = "executor-session"
    agent_session_id = "agent-session"
    token_usage = None


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResult()


async def test_run_dream_marks_running_and_waits_for_callback(org):
    org.db.insert_dream(DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=_dt(2),
        window_start=_dt(1),
        window_end=_dt(2),
    ))
    workspace = org.root / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True, exist_ok=True)
    fake = FakeExecutor()

    await run_dream(org_state=org, dream_id="DREAM-001", executor_factory=lambda *_args, **_kwargs: fake)

    dream = org.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert "no_callback" in dream.error
    assert fake.calls[0]["workspace"] == Path(workspace)
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run python -m pytest tests/daemon/test_dream_runner.py tests/test_session_token_usage_db.py::test_insert_token_usage_supports_dream_scope -q
```

Expected: FAIL because runner does not exist and token helper may reject dream scope.

- [ ] **Step 4: Add queue**

Create `runtime/daemon/dream_queue.py`:

```python
"""Async queue for dream invocations."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from runtime.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DreamJob:
    org_slug: str
    dream_id: str


class DreamQueue:
    def __init__(self) -> None:
        self._q: asyncio.Queue[DreamJob] = asyncio.Queue()

    async def put(self, job: DreamJob) -> None:
        await self._q.put(job)

    async def get(self) -> DreamJob:
        return await self._q.get()

    @property
    def size(self) -> int:
        return self._q.qsize()


async def dream_worker_loop(state, settings: Settings) -> None:
    from runtime.daemon.dream_runner import run_dream

    while True:
        for org in list(state.orgs.values()):
            if org.dream_queue.size == 0:
                continue
            try:
                job = await asyncio.wait_for(org.dream_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            try:
                await run_dream(org_state=org, dream_id=job.dream_id, settings=settings)
            except Exception:
                logger.exception("dream_worker_loop: dream %s crashed", job.dream_id)
        await asyncio.sleep(0.05)
```

- [ ] **Step 5: Add runner**

Create `runtime/daemon/dream_runner.py`:

```python
"""Executor-backed private dream invocations."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from runtime.config import Settings, settings as global_settings
from runtime.daemon.thread_runner import _build_executor_for_provider
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import DreamRecord, DreamStatus


def build_dream_prompt(
    *,
    org_slug: str,
    dream: DreamRecord,
    workspace: Path,
    recent_audit: list[dict],
    task_history: str,
) -> str:
    return f"""# Private Nightly Dream

You are {dream.agent_name}. This is private reflection for HappyRanch org `{org_slug}`.
This is not a task, talk, or thread. Do not call report-completion.

Dream id: {dream.id}
Window start: {dream.window_start.isoformat() if dream.window_start else "last 24 hours"}
Window end: {dream.window_end.isoformat()}

Review recent work, recurring friction, stale assumptions, contradictions, and durable lessons.
Write KB candidate bodies to temporary markdown files, then complete with:

happyranch dreams complete --org {org_slug} --dream-id {dream.id} --from-file /tmp/dream-result-{dream.id}.json

Task history:
{task_history}

Recent audit:
{recent_audit}
"""


def _load_task_history(workspace: Path) -> str:
    path = workspace / "task_history.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")[-20000:]


def _executor_name(workspace: Path) -> str:
    try:
        from runtime.daemon.agent_config import load_agent_config
        agent_yaml = load_agent_config(workspace) or {}
    except Exception:
        agent_yaml = {}
    return (agent_yaml.get("executor") or "claude").lower()


async def run_dream(
    *,
    org_state,
    dream_id: str,
    settings: Settings = global_settings,
    executor_factory: Callable | None = None,
) -> None:
    dream = org_state.db.get_dream(dream_id)
    if dream is None or dream.status != DreamStatus.PENDING:
        return

    workspace = org_state.root / "workspaces" / dream.agent_name
    now = datetime.now(timezone.utc)
    org_state.db.update_dream(dream_id, status=DreamStatus.RUNNING, started_at=now)
    AuditLogger(org_state.db).log_dream_started(dream_id, dream.agent_name)

    recent_audit = org_state.db.get_audit_logs(dream_id)
    prompt = build_dream_prompt(
        org_slug=org_state.slug,
        dream=dream,
        workspace=workspace,
        recent_audit=recent_audit,
        task_history=_load_task_history(workspace),
    )

    executor_name = _executor_name(workspace)
    if executor_name not in {"claude", "codex", "opencode", "pi"}:
        executor_name = "claude"
    executor = executor_factory(executor_name, settings, None) if executor_factory else _build_executor_for_provider(executor_name, settings, None)

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, lambda: executor.run(
        workspace=workspace,
        prompt=prompt,
        session_id=None,
        timeout_seconds=settings.session_timeout_seconds,
    ))

    if getattr(result, "token_usage", None) is not None:
        org_state.db.insert_session_token_usage(
            task_id=None,
            agent=dream.agent_name,
            session_id=getattr(result, "agent_session_id", None) or getattr(result, "session_id", None) or dream_id,
            executor=executor_name,
            usage=result.token_usage,
            scope_type="dream",
            scope_id=dream_id,
        )

    refreshed = org_state.db.get_dream(dream_id)
    if refreshed is None:
        return
    if refreshed.status == DreamStatus.COMPLETED:
        return
    if result.success:
        org_state.db.update_dream(
            dream_id,
            status=DreamStatus.FAILED,
            ended_at=datetime.now(timezone.utc),
            session_id=getattr(result, "agent_session_id", None) or getattr(result, "session_id", None),
            error="no_callback",
        )
        AuditLogger(org_state.db).log_dream_failed(dream_id, dream.agent_name, reason="no_callback")
        return
    org_state.db.update_dream(
        dream_id,
        status=DreamStatus.FAILED,
        ended_at=datetime.now(timezone.utc),
        error=str(getattr(result, "error", "") or "executor_failed"),
    )
    AuditLogger(org_state.db).log_dream_failed(
        dream_id,
        dream.agent_name,
        reason=str(getattr(result, "error", "") or "executor_failed"),
    )
```

- [ ] **Step 6: Allow dream token scope**

In `runtime/infrastructure/database.py`, if token usage methods validate `scope_type`, add `"dream"` to the accepted set. Preserve existing behavior for `task`, `thread`, and `talk`.

- [ ] **Step 7: Run tests**

```bash
uv run python -m pytest tests/daemon/test_dream_runner.py tests/test_session_token_usage_db.py::test_insert_token_usage_supports_dream_scope -q
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add runtime/daemon/dream_queue.py runtime/daemon/dream_runner.py runtime/infrastructure/database.py tests/daemon/test_dream_runner.py tests/test_session_token_usage_db.py
git commit -m "feat(dreams): run private dream invocations"
```

## Task 8: Scheduler Integration And Startup Recovery

**Files:**
- Modify: `runtime/daemon/dream_scheduler.py`
- Modify: `runtime/daemon/app.py`
- Modify: `runtime/daemon/org_state.py`
- Modify: `runtime/daemon/state.py`
- Test: `tests/daemon/test_dream_scheduler_integration.py`

- [ ] **Step 1: Write failing integration tests**

Create `tests/daemon/test_dream_scheduler_integration.py`:

```python
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from runtime.daemon.dream_scheduler import schedule_due_dreams, recover_running_dreams
from runtime.models import DreamRecord, DreamStatus


def test_schedule_due_dreams_inserts_and_enqueues(org, monkeypatch):
    (org.root / "org" / "agents").mkdir(parents=True, exist_ok=True)
    (org.root / "org" / "agents" / "dev_agent.md").write_text("---\nname: dev_agent\nteam: engineering\nrole: worker\n---\n")
    (org.root / "workspaces" / "dev_agent").mkdir(parents=True, exist_ok=True)
    (org.root / "org" / "config.yaml").write_text("""
dreaming:
  enabled: true
  schedule:
    time: "02:00"
    timezone: "Asia/Shanghai"
  agents:
    mode: all
""")
    enqueued = []

    async def put(job):
        enqueued.append(job)

    monkeypatch.setattr(org.dream_queue, "put", put)

    count = schedule_due_dreams(
        org=org,
        now=datetime(2026, 6, 9, 3, 0, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    assert count == 1
    dream = org.db.get_dream_for_agent_date("dev_agent", "2026-06-09")
    assert dream is not None
    assert enqueued[0].dream_id == dream.id


def test_recover_running_dreams_marks_failed(org):
    org.db.insert_dream(DreamRecord(
        id="DREAM-001",
        agent_name="dev_agent",
        local_date="2026-06-09",
        scheduled_for=datetime(2026, 6, 9, 2, 0, tzinfo=ZoneInfo("UTC")),
        window_end=datetime(2026, 6, 9, 2, 0, tzinfo=ZoneInfo("UTC")),
        status=DreamStatus.RUNNING,
    ))

    changed = recover_running_dreams(org)

    assert changed == 1
    dream = org.db.get_dream("DREAM-001")
    assert dream.status == DreamStatus.FAILED
    assert dream.error == "daemon_restart"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run python -m pytest tests/daemon/test_dream_scheduler_integration.py -q
```

Expected: FAIL because integration helpers and `OrgState.dream_queue` do not exist.

- [ ] **Step 3: Add dream queue to org state**

In `runtime/daemon/org_state.py`, import `DreamQueue` and add to `OrgState`:

```python
    dream_queue: DreamQueue = field(default_factory=DreamQueue)
```

- [ ] **Step 4: Implement scheduler helpers**

Add to `runtime/daemon/dream_scheduler.py`:

```python
import asyncio
from datetime import timedelta, timezone

from runtime.daemon.dream_queue import DreamJob
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import DreamRecord, DreamStatus
from runtime.orchestrator import prompt_loader
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.org_config import load_org_config


def _available_agents(org) -> list[str]:
    paths = OrgPaths(root=org.root)
    agents = []
    for agent in prompt_loader.list_agents(paths):
        if (org.root / "workspaces" / agent.name).exists():
            agents.append(agent.name)
    return agents


def _window_start(org, agent_name: str, window_end):
    prior = org.db.get_last_successful_dream(agent_name)
    if prior and prior.ended_at:
        return prior.ended_at
    return window_end - timedelta(hours=24)


async def _enqueue(org, dream_id: str) -> None:
    await org.dream_queue.put(DreamJob(org_slug=org.slug, dream_id=dream_id))


def schedule_due_dreams(*, org, now) -> int:
    cfg = load_org_config(OrgPaths(root=org.root)).dreaming
    selected = select_dream_agents(_available_agents(org), cfg)
    count = 0
    for agent in selected:
        local_date, _scheduled = _scheduled_datetime(now, cfg)
        existing = org.db.get_dream_for_agent_date(agent, local_date)
        decision = should_schedule_for_agent(
            agent_name=agent,
            now=now,
            config=cfg,
            existing_for_date=existing,
        )
        if not decision.should_schedule:
            continue
        dream_id = org.db.next_dream_id()
        dream = DreamRecord(
            id=dream_id,
            agent_name=agent,
            local_date=decision.local_date,
            scheduled_for=decision.scheduled_for,
            window_start=_window_start(org, agent, now),
            window_end=now,
        )
        org.db.insert_dream(dream)
        AuditLogger(org.db).log_dream_scheduled(dream_id, agent, local_date=decision.local_date)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(_enqueue(org, dream_id))
        else:
            # Unit tests may monkeypatch an async put and call this synchronously.
            asyncio.run(_enqueue(org, dream_id))
        count += 1
    return count


def recover_running_dreams(org) -> int:
    changed = 0
    for dream in org.db.list_dreams(limit=500):
        if dream.status == DreamStatus.RUNNING:
            org.db.update_dream(
                dream.id,
                status=DreamStatus.FAILED,
                error="daemon_restart",
                ended_at=datetime.now(timezone.utc),
            )
            changed += 1
    return changed


async def dream_scheduler_loop(state, *, interval_seconds: int = 60) -> None:
    while True:
        now = datetime.now(timezone.utc)
        for org in list(state.orgs.values()):
            schedule_due_dreams(org=org, now=now)
        await asyncio.sleep(interval_seconds)
```

- [ ] **Step 5: Wire lifespan**

In `runtime/daemon/app.py`, import `dream_worker_loop`, `dream_scheduler_loop`, and `recover_running_dreams` inside `_lifespan`. After job recovery and before `yield`, add:

```python
    for org in state.orgs.values():
        recover_running_dreams(org)

    dream_worker_tasks = [
        asyncio.create_task(dream_worker_loop(state, state.settings))
        for _ in range(1)
    ]
    dream_scheduler_task = asyncio.create_task(dream_scheduler_loop(state))
```

In the `finally` block, cancel these tasks:

```python
        dream_scheduler_task.cancel()
        for t in dream_worker_tasks:
            t.cancel()
```

- [ ] **Step 6: Run tests**

```bash
uv run python -m pytest tests/daemon/test_dream_scheduler_integration.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add runtime/daemon/dream_scheduler.py runtime/daemon/app.py runtime/daemon/org_state.py runtime/daemon/state.py tests/daemon/test_dream_scheduler_integration.py
git commit -m "feat(dreams): schedule nightly dream runs"
```

## Task 9: Protocol Skill And Current Docs

**Files:**
- Create: `protocol/skills/dream/SKILL.md`
- Modify: `tests/test_skills.py`
- Modify: `docs/agent-guides/features-and-invariants.md`
- Modify: `docs/agent-guides/runtime-and-configuration.md`

- [ ] **Step 1: Write failing skill test**

Add to `tests/test_skills.py`:

```python
def test_dream_skill_documents_callback_contract() -> None:
    body = (SKILLS_ROOT / "dream" / "SKILL.md").read_text()
    assert "happyranch dreams complete" in body
    assert "--from-file" in body
    assert "body_path" in body
    assert "private reflection" in body
    assert "Do not write KB entries directly" in body
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run python -m pytest tests/test_skills.py::test_dream_skill_documents_callback_contract -q
```

Expected: FAIL because the dream skill does not exist.

- [ ] **Step 3: Add dream skill**

Create `protocol/skills/dream/SKILL.md`:

```markdown
---
name: dream
description: Use this skill only when the daemon starts a private scheduled dream invocation. It produces private learnings, KB candidates, and optional founder-thread output.
---

# dream

This is a private reflection invocation. It is not a task, talk, or thread.

## Procedure

1. Review the dream prompt and the recent window it provides.
2. Identify durable private lessons for your own future work.
3. Identify possible org-wide KB candidates, but do not write KB entries directly.
4. If founder attention is needed, prepare a short founder-visible thread body.
5. Write KB candidate bodies to temporary markdown files.
6. Write a JSON payload to `/tmp/dream-result-<DREAM-ID>.json`.
7. Complete with a single-line callback:

```bash
happyranch dreams complete --org <slug> --dream-id <DREAM-ID> --from-file /tmp/dream-result-<DREAM-ID>.json
```

## Payload Shape

```json
{
  "summary": "private markdown summary",
  "learnings": [
    {
      "slug": "short-id",
      "title": "Durable private lesson",
      "topic": "workflow",
      "body": "..."
    }
  ],
  "kb_candidates": [
    {
      "slug": "candidate-slug",
      "title": "Possible org-wide rule",
      "topic": "operations",
      "rationale": "Why this may belong in KB",
      "body_path": "/tmp/dream-kb-candidate-slug.md"
    }
  ],
  "founder_thread": {
    "needed": true,
    "subject": "Nightly reflection: agent_name",
    "body_markdown": "Short founder-visible summary with candidates/actions"
  }
}
```

## Rules

- Keep the full dream private unless `founder_thread.needed` is true.
- Do not write KB entries directly. Dreams produce KB candidates for founder review.
- Do not dispatch tasks or other agents from a dream.
- Do not call `happyranch report-completion`.
- Use `body_path` for KB candidate bodies so the JSON payload stays small.
```

- [ ] **Step 4: Update guides**

In `docs/agent-guides/features-and-invariants.md`, add a `## Dreams` section:

```markdown
## Dreams

Dreams are private scheduled reflection runs, separate from tasks, talks, and threads. Per-org config lives under `dreaming:` in `<runtime>/orgs/<slug>/org/config.yaml`. A dream may write per-agent learnings, persist KB candidates, and create a founder-only thread when there is meaningful output.

Traps:

- Dreams are not `TaskRecord`s and must not appear in task metrics.
- Dreams produce KB candidates, not KB entries.
- Startup catch-up runs at most today's missed dream; it does not replay every missed day.
- Failed or timed-out dreams do not advance the next input window.
```

In `docs/agent-guides/runtime-and-configuration.md`, add a config table row for `dreaming:` under org config behavior:

```markdown
Per-org `dreaming:` config controls the private nightly reflection scheduler: enablement, local schedule time/timezone, catch-up behavior, and agent include/exclude selection.
```

- [ ] **Step 5: Run tests**

```bash
uv run python -m pytest tests/test_skills.py::test_dream_skill_documents_callback_contract -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add protocol/skills/dream/SKILL.md tests/test_skills.py docs/agent-guides/features-and-invariants.md docs/agent-guides/runtime-and-configuration.md
git commit -m "docs(dreams): document dream callback contract"
```

## Task 10: OpenAPI, Web API Mirror, And Full Verification

**Files:**
- Modify: `tests/contract/openapi.json`
- Modify: `web/src/lib/api/` files as required by OpenAPI coverage
- Modify: `web/src/test/openapi-coverage.test.ts`
- Test: `tests/contract/test_openapi_snapshot.py`
- Test: `web/src/test/openapi-coverage.test.ts`

- [ ] **Step 1: Run OpenAPI snapshot to see intentional diff**

```bash
uv run python -m pytest tests/contract/test_openapi_snapshot.py -q
```

Expected: FAIL because dreams founder-facing routes are new.

- [ ] **Step 2: Regenerate OpenAPI snapshot**

```bash
HAPPYRANCH_REGEN_OPENAPI=1 uv run python -m pytest tests/contract/test_openapi_snapshot.py -q
```

Expected: PASS and `tests/contract/openapi.json` changes.

- [ ] **Step 3: Add web API mirror for founder routes**

If OpenAPI coverage requires TS mirrors, create `web/src/lib/api/dreams.ts`:

```typescript
import { apiFetch } from './client'

export interface DreamRecord {
  dream_id: string
  agent_name: string
  local_date: string
  scheduled_for: string
  window_start: string | null
  window_end: string
  started_at: string | null
  ended_at: string | null
  status: string
  summary: string | null
  transcript_path: string | null
  new_learnings_count: number
  kb_candidate_count: number
  founder_thread_id: string | null
  error: string | null
}

export async function listDreams(org: string, params: { agent?: string; limit?: number } = {}) {
  const qs = new URLSearchParams()
  if (params.agent) qs.set('agent', params.agent)
  if (params.limit) qs.set('limit', String(params.limit))
  return apiFetch<{ dreams: DreamRecord[] }>(`/api/v1/orgs/${org}/dreams?${qs}`)
}

export async function getDream(org: string, dreamId: string) {
  return apiFetch<DreamRecord & { transcript?: string; kb_candidates?: unknown[] }>(
    `/api/v1/orgs/${org}/dreams/${dreamId}`,
  )
}
```

Update the API barrel/export file if the existing API layer uses one.

- [ ] **Step 4: Update OpenAPI coverage**

Run:

```bash
cd web && npm test -- openapi-coverage.test.ts --runInBand
```

Expected: FAIL naming missing dream route mirrors or exclusions.

Add the new route paths to `web/src/test/openapi-coverage.test.ts` in the same style as existing included API functions. Include founder-facing `GET` routes. Exclude `POST /dreams/{dream_id}/complete` with a comment that it is an agent callback, not browser-callable.

- [ ] **Step 5: Run focused verification**

```bash
uv run python -m pytest \
  tests/test_models.py::test_dream_status_values \
  tests/test_models.py::test_dream_record_defaults \
  tests/test_database_dreams.py \
  tests/test_org_config_dreaming.py \
  tests/daemon/test_dream_scheduler.py \
  tests/test_dream_store.py \
  tests/test_cli_dreams.py \
  tests/daemon/test_dreams_routes.py \
  tests/daemon/test_dream_runner.py \
  tests/daemon/test_dream_scheduler_integration.py \
  tests/test_skills.py::test_dream_skill_documents_callback_contract \
  tests/contract/test_openapi_snapshot.py \
  -q
```

Expected: PASS.

- [ ] **Step 6: Run broad verification**

```bash
uv run python -m pytest tests/ -q
```

Expected: PASS.

If web API files changed:

```bash
cd web && npm test -- openapi-coverage.test.ts --runInBand
```

Expected: PASS.

- [ ] **Step 7: Run GitNexus change detection**

```bash
# Use MCP tool instead of shell:
# mcp__gitnexus.detect_changes(repo="happyranch", scope="all")
```

Expected: Risk matches the expected dream modules, with no unrelated symbols.

- [ ] **Step 8: Commit**

```bash
git add runtime cli protocol tests docs web
git commit -m "feat(dreams): add nightly private reflection"
```

## Self-Review

Spec coverage:

- Private dreams separate from tasks/talks/threads: Tasks 1, 6, 7.
- Enable all, whitelist, blacklist config: Task 2.
- Local nightly schedule and one startup catch-up: Tasks 3 and 8.
- Last successful dream window: Tasks 1, 3, 8.
- Single reflective invocation: Task 7.
- Private learnings and KB candidates: Tasks 5 and 6.
- KB body via temp file path read by CLI: Task 5.
- Founder-only thread when meaningful: Task 6.
- Failure and daemon restart handling: Tasks 7 and 8.
- Token usage dream scope: Task 7.
- CLI/API/docs/OpenAPI: Tasks 5, 6, 9, 10.

Placeholder scan: all implementation steps name concrete files, commands, and expected outcomes.

Type consistency:

- Dream IDs use `DREAM-NNN`.
- Status enum is `DreamStatus`.
- Dream row model is `DreamRecord`.
- Candidate model is `DreamKbCandidate`.
- Callback route is `POST /api/v1/orgs/{slug}/dreams/{dream_id}/complete`.
- CLI callback is `happyranch dreams complete --org <slug> --dream-id DREAM-NNN --from-file <path>`.
