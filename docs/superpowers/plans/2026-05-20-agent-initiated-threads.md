# Agent-Initiated Threads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow any approved agent to compose a new thread from inside an active task or talk session, addressing other agents and optionally `@founder`.

**Architecture:** Additive — new HTTP route `POST /threads/compose-as-agent` (founder route `POST /threads` untouched), three new defaulted columns on `threads`, `@founder` recognized as a literal addressee that routes to Feishu + inbox (NOT a participant row). Composer auto-joins as a participant so reply fan-out reaches them. The Feishu inbound listener learns one new resolver to map founder replies on `thread_addressed` cards back to `POST /threads/{id}/send` in-process.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLite (WAL), `lark-oapi` for Feishu, pytest (unit + integration with `fake_claude.sh`).

**Spec:** `docs/superpowers/specs/2026-05-20-agent-initiated-threads-design.md` (commit `4ff974c`). Read it before starting.

---

## File Structure

**Modified:**
- `src/infrastructure/database.py` — three idempotent ALTERs in the migration block; `_row_to_thread` reads new columns; `insert_thread` writes them.
- `src/models.py` — `ThreadRecord` gains `composed_by`, `composed_from_task_id`, `composed_from_talk_id`.
- `src/daemon/routes/threads.py` — `_thread_row_to_dict` surfaces new fields; new `ComposeAsAgentBody`; new route `POST /threads/compose-as-agent`; new helpers for `@founder` handling.
- `src/infrastructure/audit_logger.py` — extend `log_thread_started` signature; new `log_thread_founder_addressed`.
- `src/infrastructure/feishu/notifier.py` — new `send_thread_addressed` method on `EscalationNotifier`.
- `src/daemon/feishu_listener.py` — new resolver hook + wiring for `kind="thread_addressed"` replies.
- `src/cli.py` — `cmd_threads_compose` gains binding flags + `--from-file` path; subparser updated.
- `protocol/skills/thread/SKILL.md` — new "Compose a new thread" section.
- `protocol/skills/start-task/SKILL.md` — one-line cross-reference under step 4.
- `protocol/skills/talk/SKILL.md` — one-line cross-reference under "What NOT to do — Exceptions".
- `web/src/test/openapi-coverage.test.ts` — add new path to `EXCLUDED_PATHS`.
- `tests/contract/openapi.json` — regenerated.

**Created:**
- `tests/unit/test_threads_compose_as_agent.py` — unit coverage for the new route.
- `tests/integration/test_agent_initiated_threads_e2e.py` — end-to-end with `fake_claude.sh`.

---

## Task 1: Schema migration — three columns on `threads`

**Files:**
- Modify: `src/infrastructure/database.py:376-396` (insertion point — same block that adds `dispatched_from_thread_id`)
- Test: `tests/unit/test_threads_compose_as_agent.py` (create)

- [ ] **Step 1: Create the test file with a schema introspection test**

Create `tests/unit/test_threads_compose_as_agent.py`:

```python
"""Unit coverage for agent-initiated thread composition."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.infrastructure.database import Database


def _columns(db: Database, table: str) -> set[str]:
    cursor = db._conn.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cursor.fetchall()}


def test_threads_table_has_composer_columns(tmp_path: Path) -> None:
    db = Database(tmp_path / "grassland.db")
    cols = _columns(db, "threads")
    assert "composed_by" in cols
    assert "composed_from_task_id" in cols
    assert "composed_from_talk_id" in cols


def test_composer_columns_index_present(tmp_path: Path) -> None:
    db = Database(tmp_path / "grassland.db")
    cursor = db._conn.execute("PRAGMA index_list(threads)")
    index_names = {row["name"] for row in cursor.fetchall()}
    assert "idx_threads_composed_from_task" in index_names
    assert "idx_threads_composed_from_talk" in index_names
```

- [ ] **Step 2: Run the test — expect FAIL (columns not yet added)**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py::test_threads_table_has_composer_columns -v
```

Expected: AssertionError on `composed_by` missing.

- [ ] **Step 3: Add the ALTERs and indexes**

In `src/infrastructure/database.py`, find the block ending around line 386 (after the `idx_tasks_dispatched_from_thread_id` index creation, before the `escalation_notifications.kind` ALTER). Insert:

```python
        # Agent-initiated threads: composer attribution + session binding.
        # Sideways refs — NOT walked by walk_ancestors. Mutually exclusive at
        # insert time (daemon enforces); default 'founder' preserves all
        # existing rows on first migration.
        for ddl in (
            "ALTER TABLE threads ADD COLUMN composed_by TEXT NOT NULL DEFAULT 'founder'",
            "ALTER TABLE threads ADD COLUMN composed_from_task_id TEXT",
            "ALTER TABLE threads ADD COLUMN composed_from_talk_id TEXT",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_threads_composed_from_task "
            "ON threads(composed_from_task_id) "
            "WHERE composed_from_task_id IS NOT NULL"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_threads_composed_from_talk "
            "ON threads(composed_from_talk_id) "
            "WHERE composed_from_talk_id IS NOT NULL"
        )
```

- [ ] **Step 4: Run the tests — expect PASS**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py tests/unit/test_threads_compose_as_agent.py
git commit -m "feat(threads): schema columns for agent-initiated composes"
```

---

## Task 2: `ThreadRecord` model + DB read/write for new fields

**Files:**
- Modify: `src/models.py:173-188` (`ThreadRecord`)
- Modify: `src/infrastructure/database.py:1326-1368` (`insert_thread` + `_row_to_thread`)
- Test: append to `tests/unit/test_threads_compose_as_agent.py`

- [ ] **Step 1: Add a failing round-trip test**

Append to `tests/unit/test_threads_compose_as_agent.py`:

```python
from src.models import ThreadRecord


def test_thread_record_roundtrip_with_composer_fields(tmp_path: Path) -> None:
    db = Database(tmp_path / "grassland.db")
    rec = ThreadRecord(
        id="THR-001",
        subject="cross-team handoff",
        composed_by="engineering_head",
        composed_from_task_id="TASK-091",
    )
    db.insert_thread(rec)
    got = db.get_thread("THR-001")
    assert got is not None
    assert got.composed_by == "engineering_head"
    assert got.composed_from_task_id == "TASK-091"
    assert got.composed_from_talk_id is None


def test_thread_record_defaults_to_founder(tmp_path: Path) -> None:
    db = Database(tmp_path / "grassland.db")
    db.insert_thread(ThreadRecord(id="THR-002", subject="founder thread"))
    got = db.get_thread("THR-002")
    assert got.composed_by == "founder"
    assert got.composed_from_task_id is None
    assert got.composed_from_talk_id is None
```

- [ ] **Step 2: Run — expect FAIL (Pydantic doesn't know the fields)**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py::test_thread_record_roundtrip_with_composer_fields -v
```

Expected: `ValidationError` or `TypeError` on the unknown field.

- [ ] **Step 3: Extend `ThreadRecord`**

In `src/models.py` after line 187 (`archive_requested_at: datetime | None = None`), add:

```python
    composed_by: str = "founder"
    composed_from_task_id: str | None = None
    composed_from_talk_id: str | None = None
```

- [ ] **Step 4: Update `insert_thread` to write the new columns**

In `src/infrastructure/database.py`, replace the `insert_thread` body (lines 1326-1350) with:

```python
    @_synchronized
    def insert_thread(self, t: ThreadRecord) -> None:
        # Spec §3.1: composed_from_task_id and composed_from_talk_id are
        # mutually exclusive; daemon enforces at insert time.
        if t.composed_from_task_id is not None and t.composed_from_talk_id is not None:
            raise ValueError(
                "composed_from_task_id and composed_from_talk_id are mutually exclusive"
            )
        self._conn.execute(
            """INSERT INTO threads (
                id, subject, started_at, archived_at, status,
                forwarded_from_id, forwarded_from_kind,
                turn_cap, turns_used, summary, new_kb_slugs_json,
                transcript_path, archive_requested_at,
                composed_by, composed_from_task_id, composed_from_talk_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                t.composed_by,
                t.composed_from_task_id,
                t.composed_from_talk_id,
            ),
        )
        self._conn.commit()
```

- [ ] **Step 5: Update `_row_to_thread` to read the new columns**

In `src/infrastructure/database.py`, find `_row_to_thread` (line 1352). Replace its body with:

```python
    def _row_to_thread(self, row) -> ThreadRecord:
        keys = row.keys()
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
            new_learnings_total=row["new_learnings_total"] if "new_learnings_total" in keys else 0,
            transcript_path=row["transcript_path"],
            archive_requested_at=datetime.fromisoformat(row["archive_requested_at"]) if row["archive_requested_at"] else None,
            composed_by=row["composed_by"] if "composed_by" in keys else "founder",
            composed_from_task_id=row["composed_from_task_id"] if "composed_from_task_id" in keys else None,
            composed_from_talk_id=row["composed_from_talk_id"] if "composed_from_talk_id" in keys else None,
        )
```

- [ ] **Step 6: Run the tests — expect PASS**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py -v
```

Expected: all four tests PASS. Also re-run the existing thread unit tests to confirm no regression:

```bash
uv run pytest tests/unit/ -k "thread" -v
```

Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/models.py src/infrastructure/database.py tests/unit/test_threads_compose_as_agent.py
git commit -m "feat(threads): ThreadRecord composer fields + DB round-trip"
```

---

## Task 3: Mutual-exclusion check in `insert_thread`

The constraint was already added in Task 2 (`ValueError` raise). This task adds the targeted test and a complementary check on `composed_by == 'founder'` implying no binding columns.

**Files:**
- Test: append to `tests/unit/test_threads_compose_as_agent.py`

- [ ] **Step 1: Write the failing test**

```python
def test_insert_thread_rejects_dual_binding(tmp_path: Path) -> None:
    db = Database(tmp_path / "grassland.db")
    with pytest.raises(ValueError, match="mutually exclusive"):
        db.insert_thread(
            ThreadRecord(
                id="THR-099",
                subject="bad",
                composed_by="engineering_head",
                composed_from_task_id="TASK-1",
                composed_from_talk_id="TALK-1",
            )
        )
```

- [ ] **Step 2: Run — expect PASS (the guard was added in Task 2)**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py::test_insert_thread_rejects_dual_binding -v
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_threads_compose_as_agent.py
git commit -m "test(threads): pin mutual exclusion of composer bindings"
```

---

## Task 4: Surface new fields in API response serializer

**Files:**
- Modify: `src/daemon/routes/threads.py:207-221` (`_thread_row_to_dict`)
- Test: append to `tests/unit/test_threads_compose_as_agent.py`

- [ ] **Step 1: Write the failing test**

```python
from src.daemon.routes.threads import _thread_row_to_dict


def test_thread_row_dict_exposes_composer_fields(tmp_path: Path) -> None:
    db = Database(tmp_path / "grassland.db")
    db.insert_thread(
        ThreadRecord(
            id="THR-010", subject="s",
            composed_by="engineering_head",
            composed_from_talk_id="TALK-007",
        )
    )
    rec = db.get_thread("THR-010")
    d = _thread_row_to_dict(rec)
    assert d["composed_by"] == "engineering_head"
    assert d["composed_from_task_id"] is None
    assert d["composed_from_talk_id"] == "TALK-007"
```

- [ ] **Step 2: Run — expect FAIL**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py::test_thread_row_dict_exposes_composer_fields -v
```

Expected: `KeyError: 'composed_by'`.

- [ ] **Step 3: Patch the serializer**

In `src/daemon/routes/threads.py`, find `_thread_row_to_dict` (line 207). Replace the return dict with:

```python
def _thread_row_to_dict(t: ThreadRecord) -> dict:
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
        "composed_by": t.composed_by,
        "composed_from_task_id": t.composed_from_task_id,
        "composed_from_talk_id": t.composed_from_talk_id,
    }
```

- [ ] **Step 4: Run — expect PASS**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/unit/test_threads_compose_as_agent.py
git commit -m "feat(threads): expose composer fields on thread API responses"
```

---

## Task 5: Audit logger — `thread_started` payload extension + `thread_founder_addressed`

**Files:**
- Modify: `src/infrastructure/audit_logger.py:548-565` (`log_thread_started`); append new method.
- Modify: `src/daemon/routes/threads.py:167-172` (existing founder compose's `log_thread_started` call — keep signature working).
- Test: append to `tests/unit/test_threads_compose_as_agent.py`

- [ ] **Step 1: Failing tests**

```python
import json as _json

from src.infrastructure.audit_logger import AuditLogger


def test_log_thread_started_payload_includes_composer(tmp_path: Path) -> None:
    db = Database(tmp_path / "grassland.db")
    db.insert_thread(ThreadRecord(id="THR-020", subject="x", composed_by="engineering_head", composed_from_task_id="TASK-9"))
    AuditLogger(db).log_thread_started(
        "THR-020",
        subject="x",
        initial_recipients=["payment_agt"],
        forwarded_from_id=None,
        composed_by="engineering_head",
        composed_from_task_id="TASK-9",
        composed_from_talk_id=None,
    )
    rows = db._conn.execute(
        "SELECT payload_json FROM audit_log WHERE task_id = ? AND action = 'thread_started'",
        ("THR-020",),
    ).fetchall()
    assert len(rows) == 1
    payload = _json.loads(rows[0]["payload_json"])
    assert payload["composed_by"] == "engineering_head"
    assert payload["composed_from_task_id"] == "TASK-9"
    assert payload["composed_from_talk_id"] is None


def test_log_thread_founder_addressed_emits_audit(tmp_path: Path) -> None:
    db = Database(tmp_path / "grassland.db")
    db.insert_thread(ThreadRecord(id="THR-021", subject="x"))
    AuditLogger(db).log_thread_founder_addressed(
        "THR-021", seq=1, speaker="engineering_head", notify_channel="feishu",
    )
    row = db._conn.execute(
        "SELECT payload_json FROM audit_log WHERE task_id = ? AND action = 'thread_founder_addressed'",
        ("THR-021",),
    ).fetchone()
    assert row is not None
    payload = _json.loads(row["payload_json"])
    assert payload == {"seq": 1, "speaker": "engineering_head", "notify_channel": "feishu"}
```

- [ ] **Step 2: Run — expect FAIL (kwargs unknown / method missing)**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py -k "log_thread" -v
```

Expected: `TypeError: unexpected keyword argument` on the first; `AttributeError` on the second.

- [ ] **Step 3: Extend the audit logger**

In `src/infrastructure/audit_logger.py`, replace `log_thread_started` (lines 548-565) with:

```python
    def log_thread_started(
        self,
        thread_id: str,
        *,
        subject: str,
        initial_recipients: list[str],
        forwarded_from_id: str | None,
        composed_by: str = "founder",
        composed_from_task_id: str | None = None,
        composed_from_talk_id: str | None = None,
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=composed_by,
            action="thread_started",
            payload={
                "subject": subject,
                "initial_recipients": initial_recipients,
                "forwarded_from_id": forwarded_from_id,
                "composed_by": composed_by,
                "composed_from_task_id": composed_from_task_id,
                "composed_from_talk_id": composed_from_talk_id,
            },
        )

    def log_thread_founder_addressed(
        self,
        thread_id: str,
        *,
        seq: int,
        speaker: str,
        notify_channel: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id,
            agent=speaker,
            action="thread_founder_addressed",
            payload={"seq": seq, "speaker": speaker, "notify_channel": notify_channel},
        )
```

Note: the existing founder compose call site (`src/daemon/routes/threads.py:167`) uses positional-after-kwarg form already; the new defaults keep it source-compatible. Verify:

- [ ] **Step 4: Run — expect PASS**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py -k "log_thread" -v
uv run pytest tests/unit/ -k "thread" -v
```

Expected: PASS on both.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/audit_logger.py tests/unit/test_threads_compose_as_agent.py
git commit -m "feat(threads): audit composer attribution + founder-addressed event"
```

---

## Task 6: `ComposeAsAgentBody` model + route skeleton

**Files:**
- Modify: `src/daemon/routes/threads.py` — add the body model near line 64 (after `ComposeBody`); add the new route after `compose_thread` ends near line 200.
- Test: append to `tests/unit/test_threads_compose_as_agent.py`

- [ ] **Step 1: Add a failing test that calls the route with empty body**

Append to `tests/unit/test_threads_compose_as_agent.py`:

```python
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """Spin up the FastAPI app against a tmp runtime with one org + agents seeded."""
    from src.daemon import paths as paths_mod
    from src.daemon.app import create_app
    monkeypatch.setenv("GRASSLAND_DAEMON_HOME", str(tmp_path / ".grassland"))
    monkeypatch.setenv("GRASSLAND_DEFAULT_RUNTIME", str(tmp_path / "runtime"))
    runtime = tmp_path / "runtime"
    (runtime / "orgs" / "test" / "org" / "agents").mkdir(parents=True)
    (runtime / "orgs" / "test" / "org" / "teams.yaml").write_text(
        "teams:\n  engineering:\n    manager: engineering_head\n    workers: [payment_agt]\n"
    )
    (runtime / "orgs" / "test" / "grassland.yaml").write_text(
        "schema_version: 2\ntype: multi-org-runtime\n"
    )
    for agent in ("engineering_head", "payment_agt"):
        (runtime / "orgs" / "test" / "org" / "agents" / f"{agent}.md").write_text(
            f"---\nname: {agent}\nteam: engineering\nrole: worker\nexecutor: claude\n"
            "description: test\n---\n# prompt\n"
        )
        (runtime / "orgs" / "test" / "workspaces" / agent).mkdir(parents=True)
    app = create_app()
    token = paths_mod.read_token()
    return TestClient(app), token


def test_compose_as_agent_route_rejects_empty_subject(app_client) -> None:
    client, token = app_client
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "engineering_head",
            "subject": "",
            "recipients": ["payment_agt"],
            "body_markdown": "hi",
            "task_id": "TASK-1", "session_id": "abc",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_subject"
```

- [ ] **Step 2: Run — expect FAIL (404 unknown route)**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py::test_compose_as_agent_route_rejects_empty_subject -v
```

Expected: `assert 404 == 422`.

- [ ] **Step 3: Add the body model + route skeleton**

In `src/daemon/routes/threads.py`, after the existing `ComposeBody` class (around line 65) add:

```python
class ComposeAsAgentBody(BaseModel):
    composer: str
    subject: str
    recipients: list[str]
    body_markdown: str
    addressed_to: list[str] = ["@all"]
    task_id: str | None = None
    session_id: str | None = None
    talk_id: str | None = None
```

After the `compose_thread` function ends (around line 199), add the new route function. This skeleton handles only the subject + body validation; later tasks fill in the rest:

```python
@router.post("/threads/compose-as-agent")
async def compose_thread_as_agent(
    slug: str, body: ComposeAsAgentBody, org: OrgDep, request: Request
) -> dict:
    state: DaemonState = request.app.state.daemon

    subject = body.subject.strip()
    if not subject:
        raise HTTPException(status_code=422, detail={"code": "empty_subject"})
    body_text = body.body_markdown.strip()
    if not body_text:
        raise HTTPException(status_code=422, detail={"code": "empty_body"})
    if not body.recipients:
        raise HTTPException(status_code=422, detail={"code": "empty_recipients"})

    # Later tasks: composer validation, binding XOR, recipient validation,
    # addressed_to subset, transaction insert, fan-out, founder push.
    raise HTTPException(status_code=501, detail={"code": "not_implemented"})
```

- [ ] **Step 4: Run — expect PASS**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py::test_compose_as_agent_route_rejects_empty_subject -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/unit/test_threads_compose_as_agent.py
git commit -m "feat(threads): scaffold POST /threads/compose-as-agent route"
```

---

## Task 7: Composer + binding XOR validation

**Files:**
- Modify: `src/daemon/routes/threads.py` (new route body)
- Test: append to `tests/unit/test_threads_compose_as_agent.py`

- [ ] **Step 1: Failing tests**

```python
def test_compose_as_agent_rejects_missing_binding(app_client) -> None:
    client, token = app_client
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "engineering_head",
            "subject": "s",
            "recipients": ["payment_agt"],
            "body_markdown": "b",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "binding_required"


def test_compose_as_agent_rejects_dual_binding(app_client) -> None:
    client, token = app_client
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "engineering_head",
            "subject": "s",
            "recipients": ["payment_agt"],
            "body_markdown": "b",
            "task_id": "TASK-1", "session_id": "abc",
            "talk_id": "TALK-1",
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "binding_ambiguous"


def test_compose_as_agent_rejects_unknown_composer(app_client) -> None:
    client, token = app_client
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "nobody",
            "subject": "s",
            "recipients": ["payment_agt"],
            "body_markdown": "b",
            "task_id": "TASK-1", "session_id": "abc",
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_composer"
```

- [ ] **Step 2: Run — expect FAIL (route returns 501)**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py -k "binding or unknown_composer" -v
```

Expected: each gets 501 instead of the expected code.

- [ ] **Step 3: Implement composer + binding validation**

In `src/daemon/routes/threads.py`, replace the placeholder `501` line in `compose_thread_as_agent` with the validation block (keep the existing subject/body/recipients checks above it):

```python
    # Composer must be an approved agent with a workspace.
    org_paths = OrgPaths(root=org.root)
    composer_def = prompt_loader.load_agent(org_paths, body.composer)
    composer_workspace = (org.root / "workspaces" / body.composer).exists()
    if composer_def is None or not composer_workspace:
        raise HTTPException(
            status_code=404,
            detail={"code": "unknown_composer", "agent": body.composer},
        )

    # Exactly one binding (task XOR talk).
    has_task = body.task_id is not None
    has_talk = body.talk_id is not None
    if not has_task and not has_talk:
        raise HTTPException(status_code=422, detail={"code": "binding_required"})
    if has_task and has_talk:
        raise HTTPException(status_code=422, detail={"code": "binding_ambiguous"})
    if has_task and not body.session_id:
        raise HTTPException(status_code=422, detail={"code": "binding_required", "missing": "session_id"})

    # Later tasks: validate task/talk binding bodies, recipients, addressed_to, fan-out.
    raise HTTPException(status_code=501, detail={"code": "not_implemented"})
```

- [ ] **Step 4: Run — expect PASS**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py -k "binding or unknown_composer or empty_subject" -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/unit/test_threads_compose_as_agent.py
git commit -m "feat(threads): validate composer + binding XOR"
```

---

## Task 8: Session binding (task path) + talk binding validation

**Files:**
- Modify: `src/daemon/routes/threads.py` (new route body)
- Test: append to `tests/unit/test_threads_compose_as_agent.py`

- [ ] **Step 1: Failing tests**

```python
from src.models import TaskRecord, TalkRecord, TalkStatus


def test_compose_as_agent_task_path_rejects_unowned_task(app_client) -> None:
    client, token = app_client
    # Seed a task assigned to payment_agt, but composer claims engineering_head.
    app = client.app
    org = app.state.daemon.orgs["test"]
    org.db.insert_task(TaskRecord(
        id="TASK-50", brief="x", team="engineering", assigned_agent="payment_agt",
    ))
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "task_id": "TASK-50", "session_id": "abc",
        },
    )
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "composer_not_task_owner"


def test_compose_as_agent_task_path_rejects_session_mismatch(app_client) -> None:
    client, token = app_client
    app = client.app
    org = app.state.daemon.orgs["test"]
    org.db.insert_task(TaskRecord(
        id="TASK-51", brief="x", team="engineering", assigned_agent="engineering_head",
    ))
    app.state.daemon.sessions.set_active("TASK-51", "engineering_head", "real-session")
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "task_id": "TASK-51", "session_id": "wrong",
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "session_mismatch"


def test_compose_as_agent_talk_path_rejects_closed_talk(app_client) -> None:
    client, token = app_client
    app = client.app
    org = app.state.daemon.orgs["test"]
    org.db.insert_talk(TalkRecord(
        id="TALK-9", agent_name="engineering_head", status=TalkStatus.CLOSED,
    ))
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "talk_id": "TALK-9",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "talk_not_open"
```

- [ ] **Step 2: Run — expect FAIL (501)**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py -k "task_path or talk_path" -v
```

Expected: 501 instead of the expected codes.

- [ ] **Step 3: Implement binding-body validation**

Replace the `raise HTTPException(status_code=501, ...)` in `compose_thread_as_agent` (still right after the binding XOR check) with:

```python
    # Task binding: task exists, composer == assigned_agent, active session matches,
    # task in {pending, in_progress}.
    if has_task:
        task = org.db.get_task(body.task_id)
        if task is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_task", "task_id": body.task_id})
        if task.assigned_agent != body.composer:
            raise HTTPException(
                status_code=403,
                detail={"code": "composer_not_task_owner",
                        "composer": body.composer, "assigned_agent": task.assigned_agent},
            )
        active_sid = state.sessions.get_active(body.task_id, body.composer)
        if active_sid is None or active_sid != body.session_id:
            raise HTTPException(
                status_code=409,
                detail={"code": "session_mismatch", "active": active_sid, "got": body.session_id},
            )
        if task.status.value not in ("pending", "in_progress"):
            raise HTTPException(
                status_code=400,
                detail={"code": "task_not_active", "status": task.status.value},
            )

    # Talk binding: talk exists, OPEN, owned by composer.
    if has_talk:
        from src.models import TalkStatus as _TalkStatus
        talk = org.db.get_talk(body.talk_id)
        if talk is None:
            raise HTTPException(status_code=404, detail={"code": "unknown_talk", "talk_id": body.talk_id})
        if talk.status != _TalkStatus.OPEN:
            raise HTTPException(
                status_code=400,
                detail={"code": "talk_not_open", "status": talk.status.value},
            )
        if talk.agent_name != body.composer:
            raise HTTPException(
                status_code=403,
                detail={"code": "composer_not_talk_owner",
                        "composer": body.composer, "talk_agent": talk.agent_name},
            )

    raise HTTPException(status_code=501, detail={"code": "not_implemented"})
```

- [ ] **Step 4: Run — expect PASS**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py -v
```

Expected: all binding tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/unit/test_threads_compose_as_agent.py
git commit -m "feat(threads): validate task/talk binding on agent compose"
```

---

## Task 9: Recipients + `@founder` literal + addressed_to validation

**Files:**
- Modify: `src/daemon/routes/threads.py` (new route body + new helper)
- Test: append to `tests/unit/test_threads_compose_as_agent.py`

- [ ] **Step 1: Failing tests**

```python
def _seed_active_task(app, agent: str, task_id: str = "TASK-200", sid: str = "sid-1") -> tuple[str, str]:
    org = app.state.daemon.orgs["test"]
    org.db.insert_task(TaskRecord(
        id=task_id, brief="x", team="engineering", assigned_agent=agent,
    ))
    app.state.daemon.sessions.set_active(task_id, agent, sid)
    return task_id, sid


def test_compose_as_agent_rejects_self_only(app_client) -> None:
    client, token = app_client
    task_id, sid = _seed_active_task(client.app, "engineering_head")
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["engineering_head"], "body_markdown": "b",
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "empty_external_recipients"


def test_compose_as_agent_rejects_unknown_recipient(app_client) -> None:
    client, token = app_client
    task_id, sid = _seed_active_task(client.app, "engineering_head")
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["who_is_this"], "body_markdown": "b",
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "unknown_agent"


def test_compose_as_agent_accepts_at_founder_literal(app_client) -> None:
    """@founder is a permitted recipient — skips agent existence check."""
    client, token = app_client
    task_id, sid = _seed_active_task(client.app, "engineering_head")
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["@founder"], "body_markdown": "b",
            "addressed_to": ["@founder"],
            "task_id": task_id, "session_id": sid,
        },
    )
    # Route still returns 501 (insert not implemented yet) but must NOT 404.
    assert r.status_code != 404


def test_compose_as_agent_rejects_addressed_to_not_subset(app_client) -> None:
    client, token = app_client
    task_id, sid = _seed_active_task(client.app, "engineering_head")
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "engineering_head", "subject": "s",
            "recipients": ["payment_agt"], "body_markdown": "b",
            "addressed_to": ["@founder"],   # not in recipients
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "addressed_to_not_subset"
```

- [ ] **Step 2: Run — expect FAIL (501 / 404 mismatch)**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py -k "recipient or at_founder or not_subset or self_only" -v
```

Expected: FAILs across the board.

- [ ] **Step 3: Implement recipients + addressed_to validation**

Insert the following block in `compose_thread_as_agent` immediately above the existing `raise HTTPException(status_code=501, ...)`:

```python
    # Dedupe recipients (preserve order).
    seen: set[str] = set()
    recipients: list[str] = []
    for name in body.recipients:
        if name in seen:
            continue
        seen.add(name)
        recipients.append(name)

    # Validate each non-@founder recipient is approved with a workspace.
    for name in recipients:
        if name == "@founder":
            continue
        agent_def = prompt_loader.load_agent(org_paths, name)
        workspace_exists = (org.root / "workspaces" / name).exists()
        if agent_def is None or not workspace_exists:
            raise HTTPException(
                status_code=404,
                detail={"code": "unknown_agent", "agent": name},
            )

    # External-recipients rule: recipients minus composer must be non-empty OR
    # @founder must appear in addressed_to (resolved if @all).
    external = [r for r in recipients if r != body.composer]
    addressed_includes_founder = (
        "@founder" in body.addressed_to
        or (body.addressed_to == ["@all"] and "@founder" in recipients)
    )
    if not external and not addressed_includes_founder:
        raise HTTPException(status_code=422, detail={"code": "empty_external_recipients"})

    # addressed_to: either ["@all"] or non-empty subset of recipients.
    _validate_addressed_to(body.addressed_to, recipients)

    raise HTTPException(status_code=501, detail={"code": "not_implemented"})
```

- [ ] **Step 4: Run — expect PASS**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py -v
```

Expected: all validation tests PASS (the route still 501s on the "happy path" check below — that's the next task).

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/unit/test_threads_compose_as_agent.py
git commit -m "feat(threads): validate recipients with @founder literal + addressed_to"
```

---

## Task 10: Happy path — insert thread + fan-out + audit + SSE

**Files:**
- Modify: `src/daemon/routes/threads.py` (replace remaining `501` raise with the full transaction)
- Test: append to `tests/unit/test_threads_compose_as_agent.py`

- [ ] **Step 1: Failing happy-path tests**

```python
def test_compose_as_agent_happy_path_returns_thread(app_client) -> None:
    client, token = app_client
    task_id, sid = _seed_active_task(client.app, "engineering_head")
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "engineering_head", "subject": "subj",
            "recipients": ["payment_agt"], "body_markdown": "hi",
            "addressed_to": ["@all"],
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["thread_id"].startswith("THR-")
    assert body["composed_by"] == "engineering_head"
    assert body["composed_from_task_id"] == task_id
    assert body["composed_from_talk_id"] is None
    assert body["pending_replies"] == ["payment_agt"]
    assert body["founder_notified"] is False


def test_compose_as_agent_adds_composer_as_participant(app_client) -> None:
    client, token = app_client
    task_id, sid = _seed_active_task(client.app, "engineering_head")
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "engineering_head", "subject": "subj",
            "recipients": ["payment_agt"], "body_markdown": "hi",
            "task_id": task_id, "session_id": sid,
        },
    )
    thread_id = r.json()["thread_id"]
    org = client.app.state.daemon.orgs["test"]
    parts = {p.agent_name for p in org.db.list_thread_participants(thread_id)}
    assert parts == {"engineering_head", "payment_agt"}


def test_compose_as_agent_founder_only_addressing_skips_invocations(app_client) -> None:
    client, token = app_client
    task_id, sid = _seed_active_task(client.app, "engineering_head")
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "engineering_head", "subject": "founder only",
            "recipients": ["payment_agt", "@founder"], "body_markdown": "hi",
            "addressed_to": ["@founder"],
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending_replies"] == []  # @founder isn't an invocation; payment_agt not addressed
    assert body["founder_notified"] is True
```

- [ ] **Step 2: Run — expect FAIL (still 501)**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py -k "happy_path or composer_as_participant or founder_only_addressing" -v
```

Expected: 501 instead of 200.

- [ ] **Step 3: Implement the transaction**

Replace the trailing `raise HTTPException(status_code=501, ...)` in `compose_thread_as_agent` with the full implementation:

```python
    org_cfg = load_org_config(org_paths)
    turn_cap = org_cfg.threads_default_turn_cap

    # Resolve addressed agents:
    # - @all → every recipient (including @founder if present, including composer);
    # - otherwise the explicit list.
    if body.addressed_to == ["@all"]:
        resolved = list(recipients)
    else:
        resolved = list(body.addressed_to)
    # Concrete agent invocations exclude @founder and the composer themselves.
    addressed_agents = [a for a in resolved if a != "@founder" and a != body.composer]
    founder_in_addressed = "@founder" in resolved

    if len(addressed_agents) > turn_cap:
        raise HTTPException(
            status_code=429,
            detail={"code": "turn_cap_exceeded",
                    "used": 0, "cap": turn_cap,
                    "requested": len(addressed_agents)},
        )

    composed_from_task_id = body.task_id if has_task else None
    composed_from_talk_id = body.talk_id if has_talk else None

    async with org.db_lock:
        thread_id = org.db.next_thread_id()
        org.db.insert_thread(ThreadRecord(
            id=thread_id, subject=subject, turn_cap=turn_cap,
            composed_by=body.composer,
            composed_from_task_id=composed_from_task_id,
            composed_from_talk_id=composed_from_talk_id,
        ))
        # Composer + every recipient become participants. @founder is NOT a row
        # (spec §3.3); skip it when iterating recipients.
        org.db.add_thread_participant(thread_id, body.composer, added_by=body.composer)
        for name in recipients:
            if name == "@founder" or name == body.composer:
                continue
            org.db.add_thread_participant(thread_id, name, added_by=body.composer)

        seq = org.db.append_thread_message(
            thread_id=thread_id, speaker=body.composer,
            kind=ThreadMessageKind.MESSAGE,
            body_markdown=body_text, addressed_to=body.addressed_to,
        )
        AuditLogger(org.db).log_thread_started(
            thread_id,
            subject=subject,
            initial_recipients=recipients,
            forwarded_from_id=None,
            composed_by=body.composer,
            composed_from_task_id=composed_from_task_id,
            composed_from_talk_id=composed_from_talk_id,
        )
        AuditLogger(org.db).log_thread_message_sent(
            thread_id, seq=seq, speaker=body.composer,
            addressed_to=body.addressed_to, kind="message",
        )
        if founder_in_addressed:
            AuditLogger(org.db).log_thread_founder_addressed(
                thread_id, seq=seq, speaker=body.composer, notify_channel="feishu",
            )
        tokens_to_enqueue: list[str] = []
        for name in addressed_agents:
            inv = org.db.mint_thread_invocation(
                thread_id=thread_id, agent_name=name,
                triggering_seq=seq, purpose=ThreadInvocationPurpose.REPLY,
            )
            tokens_to_enqueue.append(inv.invocation_token)

    for tok in tokens_to_enqueue:
        await org.thread_queue.put(ThreadJob(org_slug=slug, invocation_token=tok))

    founder_notified = False
    if founder_in_addressed:
        founder_notified = await _maybe_notify_founder_addressed(
            org, thread_id=thread_id, subject=subject, composer=body.composer,
            body_text=body_text, addressed_to=body.addressed_to,
        )

    await _publish_thread_event(
        org, slug,
        thread_id=thread_id, seq=seq, speaker=body.composer,
        kind="message", preview=body_text, status="open",
    )

    return {
        "thread_id": thread_id,
        "started_at": org.db.get_thread(thread_id).started_at.isoformat(),
        "composed_by": body.composer,
        "composed_from_task_id": composed_from_task_id,
        "composed_from_talk_id": composed_from_talk_id,
        "pending_replies": addressed_agents,
        "founder_notified": founder_notified,
    }
```

Also add a stub helper above the route (we'll fill it in Task 11):

```python
async def _maybe_notify_founder_addressed(
    org, *, thread_id: str, subject: str, composer: str,
    body_text: str, addressed_to: list[str],
) -> bool:
    """Push a Feishu card if @founder addressed and org has Feishu configured.

    Returns True iff an attempt was made (delivery failures are swallowed and
    audited, but the caller still reports `founder_notified: true`).
    """
    return False  # Task 11 implements the real push.
```

- [ ] **Step 4: Run — expect PASS on happy-path + previous tests**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py -v
uv run pytest tests/unit/ -k "thread" -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/unit/test_threads_compose_as_agent.py
git commit -m "feat(threads): agent-initiated compose route happy path"
```

---

## Task 11: Feishu push for `@founder` addressing

**Files:**
- Modify: `src/infrastructure/feishu/notifier.py` (`EscalationNotifier` gets `send_thread_addressed`)
- Modify: `src/daemon/routes/threads.py` (fill in `_maybe_notify_founder_addressed`)
- Test: append to `tests/unit/test_threads_compose_as_agent.py`

- [ ] **Step 1: Failing test using the FakeFeishuClient from existing infra**

```python
def test_compose_as_agent_founder_addressed_calls_feishu(app_client, monkeypatch) -> None:
    """When @founder is addressed and Feishu is configured, notifier fires."""
    client, token = app_client
    sent: list[dict] = []

    async def fake_notify(self, *, thread_id, subject, composer, body_text, addressed_to):
        sent.append({
            "thread_id": thread_id, "subject": subject, "composer": composer,
            "body_text": body_text,
        })
        return "msg-fake-123"

    from src.infrastructure.feishu import notifier as notifier_mod
    monkeypatch.setattr(
        notifier_mod.EscalationNotifier, "send_thread_addressed", fake_notify, raising=False,
    )
    # Force the org to think Feishu is enabled.
    org = client.app.state.daemon.orgs["test"]
    org.feishu_notifier = notifier_mod.EscalationNotifier.__new__(notifier_mod.EscalationNotifier)

    task_id, sid = _seed_active_task(client.app, "engineering_head")
    r = client.post(
        "/api/v1/orgs/test/threads/compose-as-agent",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "composer": "engineering_head", "subject": "subj",
            "recipients": ["payment_agt", "@founder"], "body_markdown": "loop you in",
            "addressed_to": ["payment_agt", "@founder"],
            "task_id": task_id, "session_id": sid,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["founder_notified"] is True
    assert len(sent) == 1
    assert sent[0]["composer"] == "engineering_head"
```

- [ ] **Step 2: Run — expect FAIL (`send_thread_addressed` undefined / notifier None)**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py::test_compose_as_agent_founder_addressed_calls_feishu -v
```

Expected: AttributeError or `founder_notified: false`.

- [ ] **Step 3: Add `send_thread_addressed` on the notifier**

In `src/infrastructure/feishu/notifier.py`, after the existing `send_dispatch_error` method, add:

```python
    async def send_thread_addressed(
        self,
        *,
        thread_id: str,
        subject: str,
        composer: str,
        body_text: str,
        addressed_to: list[str],
    ) -> str | None:
        """Push a card to the founder when an agent addresses `@founder` in a thread.

        Returns the Feishu `message_id` on success, None on send failure.
        Failure is swallowed and audited; callers receive None and must NOT
        retry — duplicate cards spam the founder.
        """
        preview = body_text if len(body_text) <= 200 else body_text[:197] + "..."
        text = (
            f"Thread {thread_id} · started by {composer}\n"
            f"Subject: {subject}\n"
            f"Recipients: {', '.join(addressed_to)}\n\n"
            f"{preview}"
        )
        try:
            message_id = await self._client.send_post_message(
                chat_id=self._chat_id, text=text,
            )
        except Exception as exc:
            self._audit.log_thread_founder_notify_failed(
                thread_id=thread_id, reason=str(exc),
            )
            return None
        # Record the row so the listener can route founder replies back.
        self._db.insert_escalation_notification(
            task_id=thread_id, message_id=message_id, kind="thread_addressed",
        )
        return message_id
```

Add the matching audit method to `src/infrastructure/audit_logger.py` (after `log_thread_founder_addressed`):

```python
    def log_thread_founder_notify_failed(
        self, *, thread_id: str, reason: str,
    ) -> None:
        self._db.insert_audit_log(
            task_id=thread_id, agent="founder",
            action="thread_founder_notify_failed",
            payload={"reason": reason},
        )
```

- [ ] **Step 4: Fill in `_maybe_notify_founder_addressed`**

In `src/daemon/routes/threads.py`, replace the stub:

```python
async def _maybe_notify_founder_addressed(
    org, *, thread_id: str, subject: str, composer: str,
    body_text: str, addressed_to: list[str],
) -> bool:
    notifier = getattr(org, "feishu_notifier", None)
    if notifier is None:
        return False
    try:
        await notifier.send_thread_addressed(
            thread_id=thread_id, subject=subject, composer=composer,
            body_text=body_text, addressed_to=addressed_to,
        )
    except Exception:
        # The notifier already swallows transport errors; this is a belt-and-
        # suspenders catch for anything that escapes (e.g., audit logger
        # blowing up). We always report `founder_notified: true` to the caller
        # — the audit log is the canonical "did it actually send" record.
        pass
    return True
```

- [ ] **Step 5: Run — expect PASS**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py::test_compose_as_agent_founder_addressed_calls_feishu -v
uv run pytest tests/unit/ -k "thread" -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/infrastructure/feishu/notifier.py src/infrastructure/audit_logger.py src/daemon/routes/threads.py tests/unit/test_threads_compose_as_agent.py
git commit -m "feat(feishu): thread_addressed card for agent-initiated threads"
```

---

## Task 12: Feishu inbound — founder reply routes back to `POST /threads/{id}/send`

**Files:**
- Modify: `src/daemon/feishu_listener.py` (new resolver hook + wiring)
- Modify: `src/daemon/app.py` (compose the resolver in the lifespan setup — search for the existing escalation/revisit/dispatch wiring)
- Test: append to `tests/integration/test_feishu_dispatch_e2e.py` OR a new file

- [ ] **Step 1: Locate the existing wiring**

Read `src/daemon/feishu_listener.py:48-70` and `src/daemon/app.py` lifespan setup to find where `resolve_escalation_in_process`, `revisit_from_notification`, and `dispatch_via_feishu` are passed in. The new resolver follows the same pattern.

- [ ] **Step 2: Add a failing integration test**

Create `tests/integration/test_agent_initiated_threads_e2e.py` (full file):

```python
"""End-to-end coverage for agent-initiated thread composition.

These tests drive a real daemon with `fake_claude.sh` and exercise:
  - compose-from-task creates thread; recipient is invoked; reply lands.
  - @founder addressing fires a (mocked) Feishu push.
  - Founder reply via Feishu lands as a `send` on the thread.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from src.daemon import paths as paths_mod
from tests.integration.conftest import seed_workspace
from tests.integration.fake_feishu import FakeFeishuClient


pytestmark = pytest.mark.integration


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {paths_mod.read_token()}"}


def _seed(runtime: Path, agent: str) -> None:
    seed_workspace(runtime, agent)
    agents_dir = runtime / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    (agents_dir / f"{agent}.md").write_text(
        f"---\nname: {agent}\nteam: engineering\nrole: worker\nexecutor: claude\n"
        "description: int test\n---\n# prompt\n"
    )


def test_founder_reply_via_feishu_lands_as_send(
    live_daemon_with_fake_feishu,
    runtime,
):
    """An agent composes addressing @founder; the fake Feishu reply path
    converts a founder reply into a `POST /threads/{id}/send`."""
    port, fake_feishu = live_daemon_with_fake_feishu
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"
    _seed(runtime, "engineering_head")
    _seed(runtime, "payment_agt")

    # Seed an active task so the composer has a valid binding.
    httpx.post(
        f"{base}/tasks",
        headers=_auth_headers(),
        json={"brief": "x", "team": "engineering"},
        timeout=5.0,
    )
    # ... (the actual fixture would set up an in-flight session for the
    # composer agent and run the compose call through the agent-side path).

    # Compose-as-agent addressing @founder.
    r = httpx.post(
        f"{base}/threads/compose-as-agent",
        headers=_auth_headers(),
        json={
            "composer": "engineering_head",
            "subject": "loop founder in",
            "recipients": ["payment_agt", "@founder"],
            "addressed_to": ["@founder"],
            "body_markdown": "founder check this",
            "task_id": "TASK-001", "session_id": "stub-session",
        },
        timeout=5.0,
    )
    assert r.status_code == 200, r.text
    thread_id = r.json()["thread_id"]

    # Wait for the fake Feishu client to receive the thread_addressed card.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(c["kind"] == "thread_addressed" and c["thread_id"] == thread_id
               for c in fake_feishu.sent_cards):
            break
        time.sleep(0.1)
    else:
        pytest.fail("thread_addressed card never reached fake Feishu")

    # Simulate founder reply via Feishu.
    fake_feishu.inbound_reply(
        message_id=fake_feishu.sent_cards[-1]["message_id"],
        text="works for me, ship it",
    )

    # Reply should appear as a founder send on the thread.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        r = httpx.get(f"{base}/threads/{thread_id}", headers=_auth_headers(), timeout=5.0)
        if r.status_code == 200:
            msgs = r.json().get("messages", [])
            if any(m["speaker"] == "founder" and "ship it" in (m["body_markdown"] or "")
                   for m in msgs):
                return
        time.sleep(0.2)
    pytest.fail("founder reply never landed as a thread message")
```

Note: the fixtures `live_daemon_with_fake_feishu` and `runtime` may not exist verbatim — check `tests/integration/conftest.py`. If a fake-Feishu fixture is missing, add a minimal one parallel to `live_daemon`.

- [ ] **Step 3: Add the resolver**

In `src/daemon/feishu_listener.py`, add a new `ResolveThreadFn` callable signature alongside the existing types (search for `RevisitFn`/`DispatchFn`):

```python
ResolveThreadFn = Callable[..., Awaitable[None]]
```

Add it to the listener constructor and store the callback:

```python
        resolve_thread_from_notification: ResolveThreadFn,
        ...
        self._resolve_thread_from_notification = resolve_thread_from_notification
```

In `_handle_event_async`, add a branch for `kind="thread_addressed"`:

```python
        elif notification.kind == "thread_addressed":
            await self._resolve_thread_from_notification(
                org_slug=org_slug, thread_id=notification.task_id,
                founder_text=text, message_id=message_id,
            )
```

In `src/daemon/app.py` (or wherever the listener is constructed; search for `resolve_escalation_in_process`), add the new in-process resolver. Implement it as a function that calls the daemon's own `POST /threads/{id}/send` logic in-process:

```python
async def resolve_thread_from_notification(
    org, state, *, org_slug, thread_id, founder_text, message_id,
):
    """Translate a founder Feishu reply on a `thread_addressed` card into a
    `POST /threads/{id}/send` server-side."""
    # Reuse the same code path as the founder-bearer send route.
    from src.daemon.routes.threads import send_message_in_process
    await send_message_in_process(
        org=org, thread_id=thread_id,
        body_markdown=founder_text, addressed_to=["@all"],
    )
    org.db.consume_escalation_notification(message_id=message_id, consumed_by="feishu-reply")
```

You'll also need to factor `send_message_in_process` out of the existing `POST /threads/{id}/send` route handler — same pattern as `dispatch_via_feishu` already does. Keep the HTTP handler thin: validate + call the in-process helper.

- [ ] **Step 4: Run integration tests**

```bash
uv run pytest tests/integration/test_agent_initiated_threads_e2e.py -v -m integration
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/feishu_listener.py src/daemon/app.py src/daemon/routes/threads.py tests/integration/test_agent_initiated_threads_e2e.py
git commit -m "feat(feishu): route founder thread_addressed replies into /send"
```

---

## Task 13: CLI extension — `--task-id/--session-id/--talk-id/--from-file/--composer`

**Files:**
- Modify: `src/cli.py:1561-1578` (`cmd_threads_compose`) and `src/cli.py:2505-2510` (subparser)
- Test: append to `tests/unit/test_threads_compose_as_agent.py` (CLI invocation via runpy / argparse stub)

- [ ] **Step 1: Failing CLI dispatch test**

```python
def test_cli_compose_with_task_binding_calls_new_route(app_client, tmp_path) -> None:
    """`grassland threads compose --task-id ... --session-id ... --from-file ...`
    targets the agent-compose route, NOT the founder route."""
    import json as _json
    client, token = app_client
    org = client.app.state.daemon.orgs["test"]
    org.db.insert_task(TaskRecord(
        id="TASK-500", brief="x", team="engineering", assigned_agent="engineering_head",
    ))
    client.app.state.daemon.sessions.set_active("TASK-500", "engineering_head", "sid-cli")

    payload_path = tmp_path / "compose.json"
    payload_path.write_text(_json.dumps({
        "composer": "engineering_head",
        "subject": "cli test",
        "recipients": ["payment_agt"],
        "body_markdown": "hi from cli",
        "addressed_to": ["@all"],
    }))

    # Invoke the CLI function with parsed args.
    from src.cli import cmd_threads_compose
    import argparse
    ns = argparse.Namespace(
        org="test",
        from_file=str(payload_path),
        task_id="TASK-500", session_id="sid-cli", talk_id=None,
        # Legacy flags retained for back-compat with founder direct invocation
        subject=None, recipients=None, body=None,
    )
    # Point the CLI client at our TestClient via env or monkeypatch the client factory.
    # ...
    cmd_threads_compose(ns)
    # Assert a thread was created.
    threads = list(org.db.list_threads(limit=10))
    assert any(t.composed_by == "engineering_head" for t in threads)
```

The test is intentionally sketched — the exact harness depends on whether `OpcClient.from_env()` is monkeypatchable. If wiring the CLI into the TestClient is too involved, replace this with a more direct test that calls `cmd_threads_compose` with a recorded httpx call (use `respx` or stub `OpcClient`).

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Extend `cmd_threads_compose`**

Replace `cmd_threads_compose` (line 1561) with:

```python
def cmd_threads_compose(args: argparse.Namespace) -> None:
    import json as _json
    client = OpcClient.from_env()
    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )
    # If binding flags are present, this is an agent-initiated compose.
    if getattr(args, "task_id", None) or getattr(args, "talk_id", None):
        if not args.from_file:
            print("error: --from-file is required for agent-initiated compose", file=sys.stderr)
            sys.exit(2)
        with open(args.from_file) as fh:
            payload = _json.load(fh)
        # Binding flags override anything in the file.
        if args.task_id:
            payload["task_id"] = args.task_id
            if args.session_id:
                payload["session_id"] = args.session_id
            payload.pop("talk_id", None)
        else:
            payload["talk_id"] = args.talk_id
            payload.pop("task_id", None)
            payload.pop("session_id", None)
        r = client.post(f"/api/v1/orgs/{slug}/threads/compose-as-agent", json=payload)
        if not _ok(r):
            return
        body = r.json()
        print(
            f"{body['thread_id']}  started={_fmt_ts(body['started_at'])}  "
            f"composed_by={body['composed_by']}  pending={body['pending_replies']}  "
            f"founder_notified={body['founder_notified']}"
        )
        return

    # Founder path — unchanged.
    if not (args.subject and args.recipients and args.body):
        print("error: --subject, --recipients, --body required for founder compose", file=sys.stderr)
        sys.exit(2)
    recipients = [r.strip() for r in args.recipients.split(",") if r.strip()]
    payload = {
        "subject": args.subject,
        "recipients": recipients,
        "body_markdown": args.body,
        "addressed_to": ["@all"],
    }
    r = client.post(f"/api/v1/orgs/{slug}/threads", json=payload)
    if not _ok(r):
        return
    body = r.json()
    print(f"{body['thread_id']}  started={_fmt_ts(body['started_at'])}  pending={body['pending_replies']}")
```

Replace the subparser registration (line 2505) with:

```python
    p_threads_compose = threads_sub.add_parser("compose", help="Compose a new thread")
    p_threads_compose.add_argument("--org", default=None, help="Org slug")
    p_threads_compose.add_argument("--from-file", default=None, dest="from_file",
                                   help="JSON payload (required for agent-initiated compose)")
    p_threads_compose.add_argument("--task-id", default=None, dest="task_id",
                                   help="Active task binding for agent-initiated compose")
    p_threads_compose.add_argument("--session-id", default=None, dest="session_id",
                                   help="Active session id (required with --task-id)")
    p_threads_compose.add_argument("--talk-id", default=None, dest="talk_id",
                                   help="Open talk binding for agent-initiated compose")
    # Legacy founder-direct flags (still supported, no --from-file needed):
    p_threads_compose.add_argument("--subject", default=None)
    p_threads_compose.add_argument("--recipients", default=None,
                                   help="Comma-separated agent names (founder path)")
    p_threads_compose.add_argument("--body", default=None,
                                   help="Opening message body (founder path)")
    p_threads_compose.set_defaults(func=cmd_threads_compose)
```

- [ ] **Step 4: Run tests + smoke-test the CLI manually**

```bash
uv run pytest tests/unit/test_threads_compose_as_agent.py -v
uv run grassland threads compose --help  # smoke-check the help text
```

Expected: tests PASS; help text shows the new flags.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py tests/unit/test_threads_compose_as_agent.py
git commit -m "feat(cli): grassland threads compose accepts task/talk binding"
```

---

## Task 14: Skill updates

**Files:**
- Modify: `protocol/skills/thread/SKILL.md` (insert new section)
- Modify: `protocol/skills/start-task/SKILL.md` (one cross-ref line)
- Modify: `protocol/skills/talk/SKILL.md` (one cross-ref line)

- [ ] **Step 1: Update `protocol/skills/thread/SKILL.md`**

Read the current file. Find the "What NOT to do" section. Insert the following block immediately above it (after the "Close-out" section):

```markdown
## Compose a new thread (from inside a task or talk)

Use this when:

- You need written async input from another agent and aren't blocked enough
  to justify an escalation.
- You want a durable record of a cross-team coordination decision.
- You're inside a talk and want to loop in an agent who isn't present.

Requirements:

- You are currently in an active task session (you have a `task_id` +
  `session_id` from `start-task`) OR an open talk (`talk_id` from `/talk
  start`).
- You name the OTHER agents you want in the thread. You may also include
  `@founder` if you want the founder pushed via Feishu (and otherwise
  notified).

### Procedure

1. Write `/tmp/thread-compose-<short-tag>.json`:

   {"composer": "<your name>",
    "subject": "<≤120 chars>",
    "recipients": ["agent_a", "agent_b"],
    "addressed_to": ["@all"] OR a subset of recipients (+ optional "@founder"),
    "body_markdown": "<the message>"}

2. From a task, single-line:

   grassland threads compose --org <slug> --task-id <TASK> --session-id <SID> --from-file /tmp/thread-compose-<tag>.json

   From a talk:

   grassland threads compose --org <slug> --talk-id <TALK> --from-file /tmp/thread-compose-<tag>.json

3. Capture the returned `thread_id`. Mention it in your task completion
   summary (or talk transcript) so the founder can find it.

### Authority

- Any agent → any agent. No team or role gate.
- You are automatically added as a participant; replies will come back to
  you on a future invocation, NOT in your current session.

### When NOT to compose

- The work is yours to do → don't outsource it via a thread. Do the work
  (or dispatch a task to yourself).
- You're blocked and need founder intervention → use `status: "blocked"` on
  `report-completion` instead. Threads are for conversation, not escalation.
- You'd be sending the same content to every agent — that's a broadcast,
  not a conversation. Talk to the founder first.
- You're already on a thread that covers the same topic → reply there.
```

- [ ] **Step 2: Update `protocol/skills/start-task/SKILL.md`**

Find step 4 ("Plan and execute"). Append the following bullet to that step:

```markdown
   If during the task you realize you need async input from another agent
   (and you're not yet blocked), consult `protocol/skills/thread/SKILL.md`
   "Compose a new thread" rather than escalating.
```

- [ ] **Step 3: Update `protocol/skills/talk/SKILL.md`**

In the "What NOT to do" section, find the existing "Exception:" bullets (around `grassland manage-agent` and `grassland dispatch`). Add a third exception:

```markdown
- **Exception:** Composing a thread to loop in another agent is allowed
  via the talk-path payload (`--talk-id` on `grassland threads compose`).
  See the `thread` skill. Record the thread_id in your
  `transcript_markdown` so the founder has a record at talk-end.
```

- [ ] **Step 4: Commit**

```bash
git add protocol/skills/thread/SKILL.md protocol/skills/start-task/SKILL.md protocol/skills/talk/SKILL.md
git commit -m "docs(skills): document agent-initiated thread compose flow"
```

---

## Task 15: Contract pinning — regenerate OpenAPI snapshot + Web TS coverage

**Files:**
- Regenerate: `tests/contract/openapi.json`
- Modify: `web/src/test/openapi-coverage.test.ts` (`EXCLUDED_PATHS`)

- [ ] **Step 1: Regenerate the OpenAPI snapshot**

```bash
GRASSLAND_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py -v
```

Expected: snapshot rewritten and the test passes on the second run:

```bash
uv run pytest tests/contract/test_openapi_snapshot.py -v
```

- [ ] **Step 2: Update Web TS coverage**

Read `web/src/test/openapi-coverage.test.ts`. Find the `EXCLUDED_PATHS` block (around line 111). Add an entry:

```typescript
  ['POST /api/v1/orgs/{slug}/threads/compose-as-agent',
   'agent callback — not exercised from the Web UI'],
```

- [ ] **Step 3: Run TS coverage**

```bash
cd web && npm run test -- --run openapi-coverage
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
cd /Users/tangbz/projects/my-opc/.claude/worktrees/agent-threads
git add tests/contract/openapi.json web/src/test/openapi-coverage.test.ts
git commit -m "test(contract): pin compose-as-agent route in OpenAPI snapshot"
```

---

## Task 16: Integration test via `fake_claude.sh` task-plan invoking compose

**Files:**
- Modify: `tests/integration/test_agent_initiated_threads_e2e.py` (extend with task-plan-driven test)
- Possibly extend: `tests/integration/conftest.py` for a fixture if missing

- [ ] **Step 1: Write the end-to-end test**

Append to `tests/integration/test_agent_initiated_threads_e2e.py`:

```python
def test_agent_compose_from_task_creates_thread_and_invokes_recipient(
    live_daemon,
    runtime,
    fake_claude_plan_env,
    fake_claude_thread_plan_env,
):
    """A worker task plan runs `grassland threads compose --task-id ... --session-id ...`,
    spawning a thread that invokes payment_agt via the thread queue.

    The thread-plan path then accepts payment_agt's reply.
    """
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"
    _seed(runtime, "engineering_head")
    _seed(runtime, "payment_agt")

    # Task plan: composer writes a compose payload, calls grassland threads compose,
    # then report-completion.
    plan = (runtime / "fake_claude_plan.sh")
    plan.write_text(
        'cat > /tmp/thread-compose-int.json <<EOF\n'
        '{"composer": "engineering_head", "subject": "int test", '
        '"recipients": ["payment_agt"], '
        '"addressed_to": ["@all"], '
        '"body_markdown": "looping you in"}\n'
        'EOF\n'
        'grassland threads compose --org test --task-id "$task_id" --session-id "$session_id" '
        '--from-file /tmp/thread-compose-int.json\n'
        'cat > /tmp/completion-$task_id.json <<EOF\n'
        '{"task_id": "$task_id", "session_id": "$session_id", '
        '"agent": "engineering_head", "status": "completed", "confidence": 90, '
        '"summary": "composed thread"}\n'
        'EOF\n'
        'grassland report-completion --org test --from-file /tmp/completion-$task_id.json\n'
    )
    fake_claude_plan_env(plan)

    # Thread plan: payment_agt replies with a fixed body.
    thread_plan = (runtime / "fake_claude_thread_plan.sh")
    thread_plan.write_text(
        'cat > /tmp/thread-reply-int.json <<EOF\n'
        '{"thread_id": "$thread_id", "invocation_token": "$token", '
        '"speaker": "$agent", "body_markdown": "got it", "in_response_to_seq": 1}\n'
        'EOF\n'
        'grassland threads reply --org test --thread-id "$thread_id" '
        '--from-file /tmp/thread-reply-int.json\n'
    )
    fake_claude_thread_plan_env(thread_plan)

    # Kick off the composer's task.
    r = httpx.post(
        f"{base}/tasks", headers=_auth_headers(),
        json={"brief": "compose a thread", "team": "engineering",
              "assigned_agent": "engineering_head"},
        timeout=5.0,
    )
    assert r.status_code in (200, 201), r.text
    task_id = r.json()["task_id"]

    # Wait for the thread to appear.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        threads = httpx.get(f"{base}/threads", headers=_auth_headers(), timeout=5.0).json()
        agent_threads = [t for t in threads["threads"]
                         if t["composed_by"] == "engineering_head"]
        if agent_threads:
            thread_id = agent_threads[0]["thread_id"]
            break
        time.sleep(0.5)
    else:
        pytest.fail("agent-composed thread never appeared")

    # Wait for payment_agt's reply.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        msgs = httpx.get(f"{base}/threads/{thread_id}", headers=_auth_headers(),
                         timeout=5.0).json().get("messages", [])
        if any(m["speaker"] == "payment_agt" for m in msgs):
            return
        time.sleep(0.5)
    pytest.fail("payment_agt never replied on the agent-composed thread")
```

Confirm `fake_claude_plan_env` and `fake_claude_thread_plan_env` exist as fixtures in `tests/integration/conftest.py`. If they don't accept a callable form like above, adapt to the actual fixture signature (e.g., `fake_claude_plan_env(path=...)` or env var).

- [ ] **Step 2: Run the integration test**

```bash
uv run pytest tests/integration/test_agent_initiated_threads_e2e.py -v -m integration
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_agent_initiated_threads_e2e.py
git commit -m "test(threads): e2e — agent task spawns thread, recipient replies"
```

---

## Task 17: Final regression sweep

- [ ] **Step 1: Run the full unit suite**

```bash
uv run pytest tests/ -v
```

Expected: all PASS (no regressions in existing thread, talk, task, KB, or audit tests).

- [ ] **Step 2: Run the integration suite**

```bash
uv run pytest tests/ -v -m integration
```

Expected: all PASS.

- [ ] **Step 3: Build the web bundle to catch any TS regression**

```bash
scripts/build_web.sh
```

Expected: build succeeds. The `openapi-coverage` test should pass during the build.

- [ ] **Step 4: Commit if any incidental fixes were needed**

If steps 1–3 surfaced incidental issues, fix and commit each one in its own focused commit. Otherwise, the implementation is complete and ready for PR.

---

## Notes for the implementer

- The `OpenAPI` snapshot regeneration in Task 15 will fail noisily until Task 6's route is registered. Run Task 6 before Task 15 strictly.
- Tasks 11 and 12 both touch Feishu wiring; if your Feishu fixture isn't already factored out, do Task 11 first (which only exercises the outbound side via monkeypatch) and then Task 12.
- The fixtures in `tests/integration/conftest.py` may not match the exact names used in this plan; adapt names but keep behavior the same.
- Watch out for `prompt_loader.load_agent` — if a workspace exists but no `<runtime>/orgs/<slug>/org/agents/<name>.md` is present, the function returns None. The seed helper in the integration tests writes both; do the same in unit fixtures.
- Composer's auto-add to `thread_participants` uses `added_by=<composer>` (not `"founder"`). The existing `participant_added` system message is NOT triggered for initial composes; only `POST /threads/{id}/invite` writes that system message. Don't add one for compose-time additions.
