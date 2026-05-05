# Token Usage Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture per-session token usage across Claude/Codex/opencode executors, store in a new `session_token_usage` table, and expose an `opc tokens` CLI for inspection and rollups.

**Architecture:** Each executor parses its own JSON output locally (returning a unified `TokenUsage` Pydantic model), the orchestrator writes one `session_token_usage` row per successful subprocess, and a single `GET /tokens` route serves both per-session listings and aggregations.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLite (WAL), FastAPI, uv, pytest.

**Spec:** `docs/superpowers/specs/2026-05-05-token-usage-tracking-design.md`

---

## File Structure

**Create:**
- `src/daemon/routes/tokens.py` — `GET /api/v1/orgs/{slug}/tokens` route
- `tests/test_token_usage_model.py` — TokenUsage Pydantic model tests
- `tests/test_session_token_usage_db.py` — DB CRUD + aggregation tests
- `tests/test_token_usage_parsers.py` — per-executor parser tests with fixtures
- `tests/test_executors_token_capture.py` — `ExecutorResult.token_usage` integration tests
- `tests/test_audit_logger_token_usage.py` — `log_session_end` payload tests
- `tests/test_run_step_token_usage.py` — `run_step` wiring tests
- `tests/daemon/test_tokens_route.py` — route tests
- `tests/test_cli_tokens.py` — `opc tokens` CLI tests
- `tests/integration/test_token_usage_e2e.py` — end-to-end with fake CLIs
- `tests/fixtures/usage_claude.json` — captured Claude `--output-format json` sample
- `tests/fixtures/usage_codex.jsonl` — captured Codex `exec --json` event stream sample
- `tests/fixtures/usage_opencode.json` — synthetic opencode `--format json` sample

**Modify:**
- `src/models.py` — add `TokenUsage` Pydantic model
- `src/infrastructure/database.py` — schema additions + 3 new query methods
- `src/infrastructure/audit_logger.py` — extend `log_session_end` signature
- `src/orchestrator/executors.py` — add 3 parsers, `ExecutorResult.token_usage`, Claude `--output-format json` flag, parser dispatch in `_run_command`
- `src/orchestrator/orchestrator.py:368` — pass `token_usage` to `log_session_end`
- `src/orchestrator/run_step.py` — call `db.insert_session_token_usage` alongside `insert_task_result`
- `src/daemon/app.py` — register tokens router
- `src/client/client.py` — add `list_tokens` / `aggregate_tokens` methods
- `src/cli.py` — add `cmd_tokens` + argparse subparser
- `tests/integration/fake_claude.sh` — emit JSON when `--output-format json` is passed
- `tests/integration/fake_codex.sh` — emit a terminal `session_complete` event with `token_usage`

**Out of scope for this plan:**
- A `fake_opencode.sh` integration binary (does not exist today; opencode coverage stays at unit-test level for this feature)
- Cost estimation, alerting, retroactive backfill (per spec §2 non-goals)

---

## Task 1: Schema, model, and database CRUD

**Files:**
- Create: `tests/test_token_usage_model.py`
- Create: `tests/test_session_token_usage_db.py`
- Modify: `src/models.py`
- Modify: `src/infrastructure/database.py`

- [ ] **Step 1: Write the failing TokenUsage model test**

Create `tests/test_token_usage_model.py`:
```python
from __future__ import annotations

from src.models import TokenUsage


def test_token_usage_all_fields_optional():
    u = TokenUsage()
    assert u.input_tokens is None
    assert u.output_tokens is None
    assert u.cache_read_tokens is None
    assert u.cache_creation_tokens is None
    assert u.reasoning_tokens is None
    assert u.model is None
    assert u.usage_raw_json is None


def test_token_usage_total_excludes_cache_reads():
    u = TokenUsage(
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=200,
        cache_creation_tokens=80,
        reasoning_tokens=30,
    )
    # Per spec §3.1: total = input + output + reasoning. Cache reads excluded.
    assert u.total == 100 + 50 + 30


def test_token_usage_total_treats_none_as_zero():
    u = TokenUsage(input_tokens=10)
    assert u.total == 10  # output=None and reasoning=None contribute 0


def test_token_usage_round_trip_via_model_dump():
    u = TokenUsage(
        input_tokens=1, output_tokens=2, cache_read_tokens=3,
        cache_creation_tokens=4, reasoning_tokens=5, model="claude-sonnet-4-6",
        usage_raw_json='{"raw":"x"}',
    )
    d = u.model_dump()
    u2 = TokenUsage(**d)
    assert u2 == u
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_token_usage_model.py -v
```
Expected: FAIL with `ImportError: cannot import name 'TokenUsage' from 'src.models'`.

- [ ] **Step 3: Add TokenUsage to src/models.py**

Append to `src/models.py`:
```python
class TokenUsage(BaseModel):
    """Per-session token usage, unified across executors.

    All fields nullable so we can write a row even when parsing partially
    succeeds (per spec §4.3). `total` deliberately excludes cache reads —
    cache hits are an effectiveness signal, not new consumption.
    """
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    reasoning_tokens: int | None = None
    model: str | None = None
    usage_raw_json: str | None = None

    @property
    def total(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0) + (self.reasoning_tokens or 0)
```

- [ ] **Step 4: Verify model tests pass**

```
uv run pytest tests/test_token_usage_model.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Write the failing DB test**

Create `tests/test_session_token_usage_db.py`:
```python
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.infrastructure.database import Database
from src.models import TokenUsage


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        yield db


def _usage(input_tokens=100, output_tokens=50, **kw):
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens, **kw)


def test_insert_and_list_session_token_usage(db: Database):
    db.insert_session_token_usage(
        task_id="TASK-1", agent="dev_agent", session_id="sess-a",
        executor="claude", token_usage=_usage(input_tokens=10, output_tokens=20),
    )
    rows = db.list_session_token_usage()
    assert len(rows) == 1
    r = rows[0]
    assert r["task_id"] == "TASK-1"
    assert r["agent"] == "dev_agent"
    assert r["session_id"] == "sess-a"
    assert r["executor"] == "claude"
    assert r["input_tokens"] == 10
    assert r["output_tokens"] == 20


def test_insert_or_ignore_on_duplicate_unique_key(db: Database):
    args = dict(
        task_id="TASK-1", agent="dev_agent", session_id="sess-a", executor="claude",
    )
    db.insert_session_token_usage(**args, token_usage=_usage(input_tokens=10))
    db.insert_session_token_usage(**args, token_usage=_usage(input_tokens=999))
    rows = db.list_session_token_usage()
    assert len(rows) == 1
    assert rows[0]["input_tokens"] == 10  # first write wins (INSERT OR IGNORE)


def test_list_filters_by_task_id_and_agent(db: Database):
    db.insert_session_token_usage(
        task_id="TASK-1", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10),
    )
    db.insert_session_token_usage(
        task_id="TASK-2", agent="dev", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=20),
    )
    db.insert_session_token_usage(
        task_id="TASK-1", agent="qa", session_id="s3", executor="codex",
        token_usage=_usage(input_tokens=30),
    )
    assert {r["session_id"] for r in db.list_session_token_usage(task_id="TASK-1")} == {"s1", "s3"}
    assert {r["session_id"] for r in db.list_session_token_usage(agent="dev")} == {"s1", "s2"}


def test_aggregate_by_agent_sums_correctly(db: Database):
    db.insert_session_token_usage(
        task_id="T1", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10, output_tokens=5),
    )
    db.insert_session_token_usage(
        task_id="T2", agent="dev", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=20, output_tokens=10, reasoning_tokens=3),
    )
    db.insert_session_token_usage(
        task_id="T3", agent="qa", session_id="s3", executor="codex",
        token_usage=_usage(input_tokens=100, output_tokens=50),
    )
    rollup = db.aggregate_session_token_usage_by_agent()
    by_agent = {r["agent"]: r for r in rollup}
    assert by_agent["dev"]["sessions"] == 2
    assert by_agent["dev"]["input_tokens"] == 30
    assert by_agent["dev"]["output_tokens"] == 15
    assert by_agent["dev"]["reasoning_tokens"] == 3
    assert by_agent["qa"]["sessions"] == 1
    assert by_agent["qa"]["input_tokens"] == 100


def test_aggregate_by_task_groups_per_task(db: Database):
    db.insert_session_token_usage(
        task_id="T1", agent="a", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10),
    )
    db.insert_session_token_usage(
        task_id="T1", agent="b", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=20),
    )
    rollup = db.aggregate_session_token_usage_by_task()
    by_task = {r["task_id"]: r for r in rollup}
    assert by_task["T1"]["sessions"] == 2
    assert by_task["T1"]["input_tokens"] == 30
```

- [ ] **Step 6: Run DB tests to verify they fail**

```
uv run pytest tests/test_session_token_usage_db.py -v
```
Expected: FAIL — `Database` has no `insert_session_token_usage` method.

- [ ] **Step 7: Add the schema to `Database._init_schema`**

In `src/infrastructure/database.py`, find `_init_schema` (should be in the `__init__` flow). Add to the `executescript` block alongside the existing `CREATE TABLE IF NOT EXISTS task_results` definition:

```sql
CREATE TABLE IF NOT EXISTS session_token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    agent      TEXT NOT NULL,
    session_id TEXT NOT NULL,
    executor   TEXT NOT NULL,
    model      TEXT,
    input_tokens          INTEGER,
    output_tokens         INTEGER,
    cache_read_tokens     INTEGER,
    cache_creation_tokens INTEGER,
    reasoning_tokens      INTEGER,
    usage_raw_json TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, agent, session_id)
);
CREATE INDEX IF NOT EXISTS idx_session_token_usage_task   ON session_token_usage (task_id);
CREATE INDEX IF NOT EXISTS idx_session_token_usage_agent  ON session_token_usage (agent, created_at);
```

- [ ] **Step 8: Add the three CRUD methods to `Database`**

In `src/infrastructure/database.py`, add (placement: after `get_task_results_for_session`):

```python
def insert_session_token_usage(
    self,
    task_id: str,
    agent: str,
    session_id: str,
    executor: str,
    token_usage: "TokenUsage",
) -> None:
    """Insert one row per (task, agent, session). INSERT OR IGNORE on the
    UNIQUE (task_id, agent, session_id) key — first write wins."""
    from datetime import datetime, timezone
    self._conn.execute(
        """INSERT OR IGNORE INTO session_token_usage
           (task_id, agent, session_id, executor, model,
            input_tokens, output_tokens, cache_read_tokens,
            cache_creation_tokens, reasoning_tokens,
            usage_raw_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            task_id, agent, session_id, executor, token_usage.model,
            token_usage.input_tokens, token_usage.output_tokens,
            token_usage.cache_read_tokens, token_usage.cache_creation_tokens,
            token_usage.reasoning_tokens, token_usage.usage_raw_json,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    self._conn.commit()


def list_session_token_usage(
    self,
    task_id: str | None = None,
    agent: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Return per-session rows, newest first."""
    where = []
    params: list[object] = []
    if task_id is not None:
        where.append("task_id = ?")
        params.append(task_id)
    if agent is not None:
        where.append("agent = ?")
        params.append(agent)
    if since is not None:
        where.append("created_at >= ?")
        params.append(since)
    sql = "SELECT * FROM session_token_usage"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    rows = self._conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def aggregate_session_token_usage_by_agent(
    self, since: str | None = None, task_id: str | None = None,
) -> list[dict]:
    where = []
    params: list[object] = []
    if since is not None:
        where.append("created_at >= ?")
        params.append(since)
    if task_id is not None:
        where.append("task_id = ?")
        params.append(task_id)
    sql = """SELECT agent,
                    COUNT(*) AS sessions,
                    SUM(input_tokens)          AS input_tokens,
                    SUM(output_tokens)         AS output_tokens,
                    SUM(cache_read_tokens)     AS cache_read_tokens,
                    SUM(cache_creation_tokens) AS cache_creation_tokens,
                    SUM(reasoning_tokens)      AS reasoning_tokens
             FROM session_token_usage"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY agent ORDER BY agent"
    rows = self._conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def aggregate_session_token_usage_by_task(
    self, since: str | None = None, agent: str | None = None,
) -> list[dict]:
    where = []
    params: list[object] = []
    if since is not None:
        where.append("created_at >= ?")
        params.append(since)
    if agent is not None:
        where.append("agent = ?")
        params.append(agent)
    sql = """SELECT task_id,
                    COUNT(*) AS sessions,
                    SUM(input_tokens)          AS input_tokens,
                    SUM(output_tokens)         AS output_tokens,
                    SUM(cache_read_tokens)     AS cache_read_tokens,
                    SUM(cache_creation_tokens) AS cache_creation_tokens,
                    SUM(reasoning_tokens)      AS reasoning_tokens
             FROM session_token_usage"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY task_id ORDER BY task_id"
    rows = self._conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
```

Add the `TokenUsage` import at the top of `database.py`:
```python
from src.models import TokenUsage  # noqa: TCH001 (used via type-hint string)
```
(If circular-import risk: keep the inline `from src.models import ...` in the method body shown above — it's lazy and avoids the cycle.)

- [ ] **Step 9: Run all of Task 1's tests**

```
uv run pytest tests/test_token_usage_model.py tests/test_session_token_usage_db.py -v
```
Expected: 9 passed.

- [ ] **Step 10: Commit**

```bash
git add src/models.py src/infrastructure/database.py tests/test_token_usage_model.py tests/test_session_token_usage_db.py
git commit -m "feat(db): add session_token_usage table and TokenUsage model"
```

---

## Task 2: Claude usage parser

**Files:**
- Create: `tests/fixtures/usage_claude.json`
- Create: `tests/test_token_usage_parsers.py`
- Modify: `src/orchestrator/executors.py`

- [ ] **Step 1: Write a Claude usage fixture**

Create `tests/fixtures/usage_claude.json`:
```json
{
  "type": "result",
  "result": "Done.",
  "model": "claude-sonnet-4-6",
  "usage": {
    "input_tokens": 12345,
    "output_tokens": 4201,
    "cache_creation_input_tokens": 8042,
    "cache_read_input_tokens": 8402,
    "service_tier": "standard"
  }
}
```

- [ ] **Step 2: Write the failing parser tests**

Create `tests/test_token_usage_parsers.py`:
```python
from __future__ import annotations

import json
from pathlib import Path

from src.orchestrator.executors import _parse_claude_usage


FIXTURES = Path(__file__).parent / "fixtures"


def _claude_fixture() -> str:
    return (FIXTURES / "usage_claude.json").read_text()


def test_parse_claude_usage_happy_path():
    u = _parse_claude_usage(_claude_fixture())
    assert u is not None
    assert u.input_tokens == 12345
    assert u.output_tokens == 4201
    assert u.cache_read_tokens == 8402
    assert u.cache_creation_tokens == 8042
    assert u.reasoning_tokens is None  # Claude doesn't bill reasoning separately
    assert u.model == "claude-sonnet-4-6"
    assert u.usage_raw_json is not None
    raw = json.loads(u.usage_raw_json)
    assert raw["input_tokens"] == 12345


def test_parse_claude_usage_malformed_returns_raw_json_with_null_fields():
    u = _parse_claude_usage("not valid json {{{")
    # Per spec §4.3: parser never returns None on a non-empty stdout; instead
    # returns TokenUsage with token fields NULL and raw payload preserved.
    assert u is not None
    assert u.input_tokens is None
    assert u.output_tokens is None
    assert u.usage_raw_json is not None
    assert "not valid json" in u.usage_raw_json


def test_parse_claude_usage_missing_usage_block():
    payload = json.dumps({"type": "result", "result": "ok", "model": "claude"})
    u = _parse_claude_usage(payload)
    assert u is not None
    assert u.input_tokens is None
    assert u.output_tokens is None
    assert u.model == "claude"
    assert u.usage_raw_json == payload


def test_parse_claude_usage_empty_stdout():
    u = _parse_claude_usage("")
    assert u is not None
    assert u.input_tokens is None
    assert u.usage_raw_json is None or u.usage_raw_json == ""
```

- [ ] **Step 3: Run tests to verify they fail**

```
uv run pytest tests/test_token_usage_parsers.py -v
```
Expected: FAIL — `_parse_claude_usage` not defined.

- [ ] **Step 4: Implement `_parse_claude_usage` in executors.py**

In `src/orchestrator/executors.py`, add at module top after imports:
```python
import json
import logging

logger = logging.getLogger(__name__)
```

Add the parser as a module-level function (after the dataclass, before `ClaudeExecutor`):
```python
from src.models import TokenUsage


def _parse_claude_usage(stdout: str) -> TokenUsage | None:
    """Parse Claude Code's `--output-format json` stdout into TokenUsage.

    Best-effort: returns TokenUsage(usage_raw_json=...) on parse failure
    (token fields NULL) so the row still gets written for forensics.
    Returns None only when stdout is empty (no parse attempted).
    """
    if not stdout or not stdout.strip():
        return None
    try:
        obj = json.loads(stdout.strip())
    except json.JSONDecodeError:
        logger.warning("claude usage parser: stdout is not valid JSON")
        return TokenUsage(usage_raw_json=stdout[:4000])
    usage = obj.get("usage") if isinstance(obj, dict) else None
    if not isinstance(usage, dict):
        return TokenUsage(
            model=obj.get("model") if isinstance(obj, dict) else None,
            usage_raw_json=stdout[:4000],
        )
    return TokenUsage(
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cache_read_tokens=usage.get("cache_read_input_tokens"),
        cache_creation_tokens=usage.get("cache_creation_input_tokens"),
        reasoning_tokens=None,
        model=obj.get("model"),
        usage_raw_json=json.dumps(usage),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```
uv run pytest tests/test_token_usage_parsers.py -v
```
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/usage_claude.json tests/test_token_usage_parsers.py src/orchestrator/executors.py
git commit -m "feat(executors): claude --output-format json usage parser"
```

---

## Task 3: Codex usage parser

**Files:**
- Create: `tests/fixtures/usage_codex.jsonl`
- Modify: `tests/test_token_usage_parsers.py`
- Modify: `src/orchestrator/executors.py`

- [ ] **Step 1: Write a Codex event-stream fixture**

Create `tests/fixtures/usage_codex.jsonl`:
```jsonl
{"type":"agent_message","content":"hello"}
{"type":"tool_call","name":"shell","args":{}}
{"type":"agent_message","content":"done"}
{"type":"session_complete","model":"gpt-5","token_usage":{"input_tokens":34887,"output_tokens":9003,"cached_tokens":15003,"reasoning_tokens":1234}}
```

- [ ] **Step 2: Append failing tests to `tests/test_token_usage_parsers.py`**

Append:
```python
from src.orchestrator.executors import _parse_codex_usage


def _codex_fixture() -> str:
    return (FIXTURES / "usage_codex.jsonl").read_text()


def test_parse_codex_usage_happy_path():
    u = _parse_codex_usage(_codex_fixture())
    assert u is not None
    assert u.input_tokens == 34887
    assert u.output_tokens == 9003
    assert u.cache_read_tokens == 15003
    assert u.cache_creation_tokens is None  # Codex doesn't separate creation
    assert u.reasoning_tokens == 1234
    assert u.model == "gpt-5"


def test_parse_codex_usage_no_session_complete_event():
    stream = '{"type":"agent_message","content":"hi"}\n{"type":"tool_call","name":"x"}\n'
    u = _parse_codex_usage(stream)
    assert u is not None
    assert u.input_tokens is None
    assert u.usage_raw_json is not None


def test_parse_codex_usage_skips_non_json_lines():
    stream = '\nWARNING: some stderr\n{"type":"session_complete","model":"gpt-5","token_usage":{"input_tokens":1,"output_tokens":2}}\n'
    u = _parse_codex_usage(stream)
    assert u is not None
    assert u.input_tokens == 1
    assert u.output_tokens == 2


def test_parse_codex_usage_takes_last_session_complete():
    stream = (
        '{"type":"session_complete","model":"gpt-5","token_usage":{"input_tokens":1}}\n'
        '{"type":"session_complete","model":"gpt-5","token_usage":{"input_tokens":99}}\n'
    )
    u = _parse_codex_usage(stream)
    assert u is not None
    assert u.input_tokens == 99


def test_parse_codex_usage_empty_stdout():
    assert _parse_codex_usage("") is None
```

- [ ] **Step 3: Run tests to verify they fail**

```
uv run pytest tests/test_token_usage_parsers.py -v
```
Expected: 5 new failures with `_parse_codex_usage` not defined.

- [ ] **Step 4: Implement `_parse_codex_usage`**

Append in `src/orchestrator/executors.py` after `_parse_claude_usage`:
```python
def _parse_codex_usage(stdout: str) -> TokenUsage | None:
    """Parse Codex `exec --json` NDJSON event stream into TokenUsage.

    Walks events, picks the last `session_complete`. Returns None on empty
    stdout, TokenUsage with NULL token fields if no session_complete found
    (forensic preservation), populated TokenUsage on success.

    Note: the Codex event name "session_complete" is the documented terminal
    event. Verify against the running Codex CLI version during integration
    testing — if the schema changes, only this function needs updating.
    """
    if not stdout or not stdout.strip():
        return None
    last_complete: dict | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "session_complete":
            last_complete = event
    if last_complete is None:
        return TokenUsage(usage_raw_json=stdout[:4000])
    tu = last_complete.get("token_usage") or {}
    if not isinstance(tu, dict):
        tu = {}
    return TokenUsage(
        input_tokens=tu.get("input_tokens"),
        output_tokens=tu.get("output_tokens"),
        cache_read_tokens=tu.get("cached_tokens"),
        cache_creation_tokens=None,
        reasoning_tokens=tu.get("reasoning_tokens"),
        model=last_complete.get("model"),
        usage_raw_json=json.dumps(last_complete),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```
uv run pytest tests/test_token_usage_parsers.py -v
```
Expected: all 9 pass (4 Claude + 5 Codex).

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/usage_codex.jsonl tests/test_token_usage_parsers.py src/orchestrator/executors.py
git commit -m "feat(executors): codex --json usage parser"
```

---

## Task 4: opencode usage parser

**Files:**
- Create: `tests/fixtures/usage_opencode.json`
- Modify: `tests/test_token_usage_parsers.py`
- Modify: `src/orchestrator/executors.py`

> Note: opencode JSON shape per spec §6.3 is "expected" pending verification against the actual `opencode run --format json` output. The parser handles the shape below; if real opencode output differs at integration-time, update both this fixture and the parser.

- [ ] **Step 1: Write the opencode fixture**

Create `tests/fixtures/usage_opencode.json`:
```json
{
  "messages": [
    {"role": "user", "content": "hello"},
    {"role": "assistant", "model": "claude-sonnet-4-6", "content": "tool call",
     "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 0, "cache_write_tokens": 100}},
    {"role": "tool_result", "content": "..."},
    {"role": "assistant", "model": "claude-sonnet-4-6", "content": "final",
     "usage": {"input_tokens": 200, "output_tokens": 75, "cache_read_tokens": 100, "cache_write_tokens": 0}}
  ]
}
```

- [ ] **Step 2: Append failing tests**

Append to `tests/test_token_usage_parsers.py`:
```python
from src.orchestrator.executors import _parse_opencode_usage


def _opencode_fixture() -> str:
    return (FIXTURES / "usage_opencode.json").read_text()


def test_parse_opencode_usage_sums_assistant_messages():
    u = _parse_opencode_usage(_opencode_fixture())
    assert u is not None
    assert u.input_tokens == 300       # 100 + 200
    assert u.output_tokens == 125      # 50 + 75
    assert u.cache_read_tokens == 100  # 0 + 100
    assert u.cache_creation_tokens == 100  # mapped from cache_write_tokens; 100 + 0
    assert u.model == "claude-sonnet-4-6"


def test_parse_opencode_usage_malformed_json():
    u = _parse_opencode_usage("not json")
    assert u is not None
    assert u.input_tokens is None
    assert u.usage_raw_json is not None


def test_parse_opencode_usage_no_assistant_messages():
    stream = '{"messages": [{"role": "user", "content": "hi"}]}'
    u = _parse_opencode_usage(stream)
    assert u is not None
    assert u.input_tokens is None
    assert u.usage_raw_json is not None


def test_parse_opencode_usage_empty_stdout():
    assert _parse_opencode_usage("") is None
```

- [ ] **Step 3: Run tests to verify they fail**

```
uv run pytest tests/test_token_usage_parsers.py -v
```
Expected: 4 new failures.

- [ ] **Step 4: Implement `_parse_opencode_usage`**

Append in `src/orchestrator/executors.py`:
```python
def _parse_opencode_usage(stdout: str) -> TokenUsage | None:
    """Parse opencode `--format json` stdout into TokenUsage.

    Sums assistant-role message usage. Model taken from last assistant
    message (sessions can span multiple models for tool use; last is the
    canonical 'this session ran on' answer).
    """
    if not stdout or not stdout.strip():
        return None
    try:
        obj = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return TokenUsage(usage_raw_json=stdout[:4000])
    if not isinstance(obj, dict):
        return TokenUsage(usage_raw_json=stdout[:4000])
    messages = obj.get("messages") or []
    assistant_msgs = [m for m in messages if isinstance(m, dict) and m.get("role") == "assistant" and isinstance(m.get("usage"), dict)]
    if not assistant_msgs:
        return TokenUsage(usage_raw_json=stdout[:4000])

    def _sum(field: str) -> int | None:
        vals = [m["usage"].get(field) for m in assistant_msgs]
        nums = [v for v in vals if isinstance(v, int)]
        return sum(nums) if nums else None

    last_model = next((m.get("model") for m in reversed(assistant_msgs) if m.get("model")), None)
    return TokenUsage(
        input_tokens=_sum("input_tokens"),
        output_tokens=_sum("output_tokens"),
        cache_read_tokens=_sum("cache_read_tokens"),
        cache_creation_tokens=_sum("cache_write_tokens"),
        reasoning_tokens=_sum("reasoning_tokens"),
        model=last_model,
        usage_raw_json=json.dumps([m["usage"] for m in assistant_msgs]),
    )
```

- [ ] **Step 5: Run tests to verify they pass**

```
uv run pytest tests/test_token_usage_parsers.py -v
```
Expected: 13 passed (4 + 5 + 4).

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/usage_opencode.json tests/test_token_usage_parsers.py src/orchestrator/executors.py
git commit -m "feat(executors): opencode --format json usage parser"
```

---

## Task 5: Wire `token_usage` into `ExecutorResult` and switch Claude to JSON output

**Files:**
- Create: `tests/test_executors_token_capture.py`
- Modify: `src/orchestrator/executors.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_executors_token_capture.py`:
```python
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.config import Settings
from src.orchestrator.executors import ClaudeExecutor, CodexExecutor, OpencodeExecutor, ExecutorResult


def _make_completed_proc(stdout: str, returncode: int = 0):
    p = MagicMock()
    p.communicate.return_value = (stdout, "")
    p.returncode = returncode
    p.pid = 12345
    return p


def test_executor_result_has_token_usage_field_default_none():
    r = ExecutorResult(success=True, duration_seconds=1, session_id="s")
    assert r.token_usage is None


def test_claude_executor_attaches_token_usage_on_success(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)

    fixture = (Path(__file__).parent / "fixtures" / "usage_claude.json").read_text()
    fake_proc = _make_completed_proc(stdout=fixture)
    with patch("src.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
        # allow_rules_for_agent reads from <runtime>/org/agents/<name>.md;
        # short-circuit it for this isolated unit test.
        with patch("src.orchestrator.workspace_adapters.allow_rules_for_agent", return_value=["Bash(opc *)"]):
            ex = ClaudeExecutor(
                claude_cli_path="claude",
                permission_mode="auto",
                settings=Settings(),
                paths=None,
            )
            result = ex.run(workspace, prompt="hi", session_id="sess-x")
    assert result.success
    assert result.token_usage is not None
    assert result.token_usage.input_tokens == 12345


def test_claude_executor_passes_output_format_json_flag(tmp_path: Path):
    workspace = tmp_path / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    fake_proc = _make_completed_proc(stdout="{}")

    captured_cmd = []

    def _capture_popen(cmd, **kw):
        captured_cmd.extend(cmd)
        return fake_proc

    with patch("src.orchestrator.executors.subprocess.Popen", side_effect=_capture_popen):
        with patch("src.orchestrator.workspace_adapters.allow_rules_for_agent", return_value=[]):
            ex = ClaudeExecutor("claude", "auto", Settings(), paths=None)
            ex.run(workspace, prompt="hi", session_id="sess-x")
    assert "--output-format" in captured_cmd
    json_idx = captured_cmd.index("--output-format")
    assert captured_cmd[json_idx + 1] == "json"


def test_claude_executor_token_usage_is_none_on_subprocess_failure(tmp_path: Path):
    workspace = tmp_path / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    fake_proc = _make_completed_proc(stdout="{}", returncode=1)
    with patch("src.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
        with patch("src.orchestrator.workspace_adapters.allow_rules_for_agent", return_value=[]):
            ex = ClaudeExecutor("claude", "auto", Settings(), paths=None)
            r = ex.run(workspace, prompt="hi", session_id="sess-x")
    assert not r.success
    assert r.token_usage is None  # subprocess failed → no row should be written


def test_codex_executor_attaches_token_usage(tmp_path: Path):
    workspace = tmp_path / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    fixture = (Path(__file__).parent / "fixtures" / "usage_codex.jsonl").read_text()
    fake_proc = _make_completed_proc(stdout=fixture)
    with patch("src.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
        ex = CodexExecutor("codex", sandbox_mode="workspace-write")
        r = ex.run(workspace, prompt="hi", session_id="sess-x")
    assert r.success
    assert r.token_usage is not None
    assert r.token_usage.input_tokens == 34887


def test_opencode_executor_attaches_token_usage(tmp_path: Path):
    workspace = tmp_path / "workspaces" / "dev_agent"
    workspace.mkdir(parents=True)
    fixture = (Path(__file__).parent / "fixtures" / "usage_opencode.json").read_text()
    fake_proc = _make_completed_proc(stdout=fixture)
    with patch("src.orchestrator.executors.subprocess.Popen", return_value=fake_proc):
        ex = OpencodeExecutor("opencode")
        r = ex.run(workspace, prompt="hi", session_id="sess-x")
    assert r.success
    assert r.token_usage is not None
    assert r.token_usage.input_tokens == 300
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_executors_token_capture.py -v
```
Expected: most fail — `ExecutorResult` lacks `token_usage`; Claude doesn't pass `--output-format json`.

- [ ] **Step 3: Add `token_usage` to `ExecutorResult`**

In `src/orchestrator/executors.py`, modify the dataclass:
```python
@dataclass
class ExecutorResult:
    """Outcome of a subprocess execution. Completion data lives in the DB.

    ``returncode``/``stdout_tail``/``stderr_tail`` feed the enriched
    ``agent session failed`` note in ``run_step._session_failed_note`` so
    a subprocess that exits without calling back is self-diagnosing from
    the audit trail alone (the TASK-044/045/077 class of failure).
    Timeouts leave ``returncode=None`` because the process was killed
    before an exit code could be observed; in that case the enriched
    note renders ``rc=?`` and the ``error`` string carries the timeout.
    """

    success: bool
    duration_seconds: int
    session_id: str
    returncode: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str | None = None
    token_usage: "TokenUsage | None" = None  # populated only on success
```

- [ ] **Step 4: Refactor `_run_command` to expose full stdout for parsing**

The current `_run_command` truncates stdout before returning. We need the full stdout for the parser, then truncate for `stdout_tail`. Modify `_run_command` to accept an optional `usage_parser: Callable[[str], TokenUsage | None]`:

```python
def _run_command(
    cmd: list[str],
    workspace: Path,
    session_id: str | None,
    timeout_seconds: int,
    input_text: str | None = None,
    on_started: Callable[[int], None] | None = None,
    usage_parser: Callable[[str], "TokenUsage | None"] | None = None,
) -> ExecutorResult:
    sid = session_id or f"sess-{uuid.uuid4().hex}"
    workspace.mkdir(parents=True, exist_ok=True)
    start_time = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=str(workspace),
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if on_started is not None:
        on_started(proc.pid)
    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return ExecutorResult(
            success=False,
            duration_seconds=int(time.monotonic() - start_time),
            session_id=sid,
            error=f"Session timed out after {timeout_seconds} seconds",
        )
    full_stdout = stdout or ""
    full_stderr = stderr or ""
    stdout_tail = full_stdout[-_TAIL_BYTES:]
    stderr_tail = full_stderr[-_TAIL_BYTES:]
    if proc.returncode != 0:
        error_summary = (full_stderr or full_stdout).strip()
        if error_summary:
            error_summary = f": {error_summary}"
        return ExecutorResult(
            success=False,
            duration_seconds=int(time.monotonic() - start_time),
            session_id=sid,
            returncode=proc.returncode,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            error=f"Command exited with code {proc.returncode}{error_summary}",
        )
    token_usage = None
    if usage_parser is not None:
        try:
            token_usage = usage_parser(full_stdout)
        except Exception as exc:  # parser must never break the task
            logger.warning("usage parser raised: %s", exc)
            token_usage = None
    return ExecutorResult(
        success=True,
        duration_seconds=int(time.monotonic() - start_time),
        session_id=sid,
        returncode=proc.returncode,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        token_usage=token_usage,
    )
```

- [ ] **Step 5: Switch Claude to `--output-format json` and pass parser**

Update `ClaudeExecutor.run`:
```python
def run(
    self,
    workspace: Path,
    prompt: str,
    session_id: str | None = None,
    timeout_seconds: int = 1800,
    on_started: Callable[[int], None] | None = None,
) -> ExecutorResult:
    from src.orchestrator.workspace_adapters import allow_rules_for_agent
    allowed = " ".join(allow_rules_for_agent(self._paths, workspace.name, cli=True))
    cmd = [
        self._cli_path,
        "-p", prompt,
        "--permission-mode", self._permission_mode,
        "--allowedTools", allowed,
        "--output-format", "json",
    ]
    return _run_command(
        cmd, workspace, session_id, timeout_seconds,
        on_started=on_started,
        usage_parser=_parse_claude_usage,
    )
```

- [ ] **Step 6: Pass parser for Codex and opencode (no CLI flag changes)**

Update `CodexExecutor.run`'s `_run_command` call: add `usage_parser=_parse_codex_usage`.
Update `OpencodeExecutor.run`'s `_run_command` call: add `usage_parser=_parse_opencode_usage`.

- [ ] **Step 7: Run tests**

```
uv run pytest tests/test_executors_token_capture.py tests/test_token_usage_parsers.py -v
```
Expected: all pass.

- [ ] **Step 8: Run full unit suite — make sure no other test breaks**

```
uv run pytest tests/ -v
```
Expected: same baseline (787 passed before this change, plus the new tests). No regressions.

- [ ] **Step 9: Commit**

```bash
git add src/orchestrator/executors.py tests/test_executors_token_capture.py
git commit -m "feat(executors): wire token_usage into ExecutorResult; claude --output-format json"
```

---

## Task 6: Extend `audit_logger.log_session_end`

**Files:**
- Create: `tests/test_audit_logger_token_usage.py`
- Modify: `src/infrastructure/audit_logger.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_audit_logger_token_usage.py`:
```python
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import TokenUsage


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(Path(tmp) / "test.db")


def _entries(db, action):
    rows = db.get_audit_log()
    return [r for r in rows if r["action"] == action]


def test_log_session_end_without_token_usage_keeps_back_compat_shape(db: Database):
    a = AuditLogger(db)
    a.log_session_end(task_id="T1", agent="dev", duration_seconds=42)
    e = _entries(db, "session_end")
    assert len(e) == 1
    payload = json.loads(e[0]["payload"]) if isinstance(e[0]["payload"], str) else e[0]["payload"]
    assert payload["duration_seconds"] == 42
    assert payload["token_count"] is None


def test_log_session_end_with_token_usage_carries_dict_and_derived_total(db: Database):
    a = AuditLogger(db)
    u = TokenUsage(input_tokens=100, output_tokens=50, reasoning_tokens=10)
    a.log_session_end(task_id="T1", agent="dev", duration_seconds=42, token_usage=u)
    e = _entries(db, "session_end")
    payload = json.loads(e[0]["payload"]) if isinstance(e[0]["payload"], str) else e[0]["payload"]
    assert payload["duration_seconds"] == 42
    assert payload["token_count"] == 160  # 100 + 50 + 10 (cache reads excluded)
    assert payload["token_usage"]["input_tokens"] == 100
    assert payload["token_usage"]["output_tokens"] == 50
    assert payload["token_usage"]["reasoning_tokens"] == 10


def test_log_session_end_with_partial_token_usage(db: Database):
    a = AuditLogger(db)
    u = TokenUsage(usage_raw_json='{"raw":"x"}')  # parse-failure case: all token fields NULL
    a.log_session_end(task_id="T1", agent="dev", duration_seconds=10, token_usage=u)
    e = _entries(db, "session_end")
    payload = json.loads(e[0]["payload"]) if isinstance(e[0]["payload"], str) else e[0]["payload"]
    assert payload["token_count"] == 0  # all None → 0
    assert payload["token_usage"]["usage_raw_json"] == '{"raw":"x"}'
```

(Note: `db.get_audit_log()` may not exist by exactly that name — adjust to whatever the existing read API is, e.g., `db.list_audit_log()` or a direct `SELECT`. Check `src/infrastructure/database.py` for the audit-read API and use it.)

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_audit_logger_token_usage.py -v
```
Expected: FAIL — `log_session_end` doesn't accept `token_usage`.

- [ ] **Step 3: Update `log_session_end` signature**

In `src/infrastructure/audit_logger.py`:
```python
def log_session_end(
    self,
    task_id: str,
    agent: str,
    duration_seconds: int,
    token_usage: "TokenUsage | None" = None,
) -> None:
    payload: dict = {"duration_seconds": duration_seconds}
    if token_usage is not None:
        payload["token_usage"] = token_usage.model_dump()
        payload["token_count"] = token_usage.total
    else:
        payload["token_count"] = None
    self._db.insert_audit_log(
        task_id=task_id, agent=agent, action="session_end", payload=payload,
    )
```

Add the import at the top of the file:
```python
from src.models import CompletionReport, TokenUsage
```

(If `CompletionReport` was already imported there, just append `TokenUsage` to the same line.)

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_audit_logger_token_usage.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Run full unit suite to confirm no regression**

```
uv run pytest tests/ -v
```
Expected: green (the old call-site `self._audit.log_session_end(task_id, agent_name, result.duration_seconds)` still works because `token_usage` defaults to `None`).

- [ ] **Step 6: Commit**

```bash
git add src/infrastructure/audit_logger.py tests/test_audit_logger_token_usage.py
git commit -m "feat(audit): log_session_end carries token_usage dict; derives token_count for back-compat"
```

---

## Task 7: Wire token capture into `run_step` / orchestrator

**Files:**
- Create: `tests/test_run_step_token_usage.py`
- Modify: `src/orchestrator/orchestrator.py`
- Modify: `src/orchestrator/run_step.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_run_step_token_usage.py`:
```python
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import TokenUsage
from src.orchestrator.executors import ExecutorResult


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        yield Database(Path(tmp) / "test.db")


def test_orchestrator_log_session_end_passes_token_usage(db: Database):
    """The orchestrator's existing log_session_end call site must forward
    result.token_usage to the audit logger.

    Specifically, src/orchestrator/orchestrator.py:368 was previously:
        self._audit.log_session_end(task_id, agent_name, result.duration_seconds)
    It must now pass token_usage=result.token_usage.
    """
    audit = MagicMock(spec=AuditLogger)
    result = ExecutorResult(
        success=True, duration_seconds=10, session_id="s1",
        token_usage=TokenUsage(input_tokens=5, output_tokens=10),
    )
    # Simulate the call shape — verify the helper or method that holds the
    # log_session_end call passes token_usage.
    audit.log_session_end(
        task_id="T1", agent="dev", duration_seconds=result.duration_seconds,
        token_usage=result.token_usage,
    )
    audit.log_session_end.assert_called_once_with(
        task_id="T1", agent="dev", duration_seconds=10,
        token_usage=result.token_usage,
    )


def test_run_step_writes_session_token_usage_row_on_success(db: Database):
    """When run_step receives a successful ExecutorResult with token_usage,
    it inserts a row in session_token_usage.

    This test calls db.insert_session_token_usage directly to confirm the
    expected row shape; run_step plumbing is verified by the integration
    test in Task 11.
    """
    db.insert_session_token_usage(
        task_id="T1", agent="dev", session_id="s1", executor="claude",
        token_usage=TokenUsage(input_tokens=100, output_tokens=50, model="claude-sonnet-4-6"),
    )
    rows = db.list_session_token_usage(task_id="T1")
    assert len(rows) == 1
    assert rows[0]["model"] == "claude-sonnet-4-6"


def test_run_step_skips_token_row_when_token_usage_is_none(db: Database):
    """ExecutorResult.token_usage = None (subprocess failed) → no row written."""
    # Confirm baseline: empty table.
    rows = db.list_session_token_usage()
    assert rows == []
    # No insert call → no row. (The actual conditional lives in run_step;
    # this test documents the contract.)


def test_run_step_writes_partial_row_on_parse_failure(db: Database):
    """ExecutorResult.token_usage = TokenUsage(usage_raw_json=...) (parse failed)
    → row is written with NULL token columns + populated raw_json."""
    db.insert_session_token_usage(
        task_id="T1", agent="dev", session_id="s1", executor="claude",
        token_usage=TokenUsage(usage_raw_json='{"weird":"shape"}'),
    )
    rows = db.list_session_token_usage(task_id="T1")
    assert len(rows) == 1
    assert rows[0]["input_tokens"] is None
    assert rows[0]["output_tokens"] is None
    assert rows[0]["usage_raw_json"] == '{"weird":"shape"}'
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_run_step_token_usage.py -v
```
Expected: 4 passed (these tests cover the contract shape, not the live orchestrator call yet — the contract holds even before wiring).

- [ ] **Step 3: Update `orchestrator.py:368`**

In `src/orchestrator/orchestrator.py`, change line ~368:
```python
# OLD:
self._audit.log_session_end(task_id, agent_name, result.duration_seconds)
# NEW:
self._audit.log_session_end(
    task_id=task_id,
    agent=agent_name,
    duration_seconds=result.duration_seconds,
    token_usage=result.token_usage,
)
```

- [ ] **Step 4: Add session_token_usage write in run_step**

In `src/orchestrator/run_step.py`, find the block around line 180 where `db.insert_task_result(...)` is called after a successful executor run. Insert the token-usage write *before* `insert_task_result` (so a parse failure that somehow throws would not block the task_result insert — though `insert_session_token_usage` should never throw under normal operation):

Locate:
```python
db.insert_task_result(
    task_id=task_id,
    ...
)
```

Just above it, add:
```python
if result.token_usage is not None:
    db.insert_session_token_usage(
        task_id=task_id,
        agent=agent_name,
        session_id=result.session_id,
        executor=executor_name,  # see step 5 if executor_name isn't already in scope
        token_usage=result.token_usage,
    )
```

- [ ] **Step 5: Resolve `executor_name` in scope**

Inside `run_step`, the agent's executor name lives in the agent's `agent.yaml`. The scope around line 180 may or may not have it directly. Two options:

(a) If an `agent_def` or `agent_config` object is in scope, read `agent_def.executor` (or whichever attribute name is correct on `AgentDef`).

(b) If not in scope, read it via `db` or `prompt_loader.load_agent(agent_name).executor`.

Inspect the surrounding ~30 lines of context and pick the cleanest. The executor name MUST be one of `'claude' | 'codex' | 'opencode'` to match the schema's expectation (the column has no CHECK constraint, so a typo would silently land — be careful).

- [ ] **Step 6: Run all tests**

```
uv run pytest tests/ -v
```
Expected: full suite still green; new tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/orchestrator/orchestrator.py src/orchestrator/run_step.py tests/test_run_step_token_usage.py
git commit -m "feat(orchestrator): persist session_token_usage rows after successful sessions"
```

---

## Task 8: Daemon route — `GET /tokens`

**Files:**
- Create: `src/daemon/routes/tokens.py`
- Create: `tests/daemon/test_tokens_route.py`
- Modify: `src/daemon/app.py`

- [ ] **Step 1: Write failing route tests**

Create `tests/daemon/test_tokens_route.py`:
```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

# Reuse the existing daemon test fixture pattern — see other files in
# tests/daemon/ (e.g., test_audit_route.py) for the shared fixture that
# spins up a TestClient + an org with a known slug + bearer token.


def test_get_tokens_empty(client_with_org, auth_headers, slug):
    r = client_with_org.get(f"/api/v1/orgs/{slug}/tokens", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"rows": []}


def test_get_tokens_returns_inserted_rows(client_with_org, auth_headers, slug, db):
    from src.models import TokenUsage
    db.insert_session_token_usage(
        task_id="T1", agent="dev", session_id="s1", executor="claude",
        token_usage=TokenUsage(input_tokens=100, output_tokens=50),
    )
    r = client_with_org.get(f"/api/v1/orgs/{slug}/tokens", headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["task_id"] == "T1"
    assert rows[0]["input_tokens"] == 100


def test_get_tokens_filters_by_task_id(client_with_org, auth_headers, slug, db):
    from src.models import TokenUsage
    db.insert_session_token_usage(task_id="T1", agent="dev", session_id="s1", executor="claude", token_usage=TokenUsage(input_tokens=10))
    db.insert_session_token_usage(task_id="T2", agent="dev", session_id="s2", executor="claude", token_usage=TokenUsage(input_tokens=20))
    r = client_with_org.get(f"/api/v1/orgs/{slug}/tokens?task_id=T2", headers=auth_headers)
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["task_id"] == "T2"


def test_get_tokens_group_by_agent_returns_rollup(client_with_org, auth_headers, slug, db):
    from src.models import TokenUsage
    db.insert_session_token_usage(task_id="T1", agent="dev", session_id="s1", executor="claude", token_usage=TokenUsage(input_tokens=10))
    db.insert_session_token_usage(task_id="T2", agent="dev", session_id="s2", executor="claude", token_usage=TokenUsage(input_tokens=20))
    db.insert_session_token_usage(task_id="T3", agent="qa",  session_id="s3", executor="codex", token_usage=TokenUsage(input_tokens=100))
    r = client_with_org.get(f"/api/v1/orgs/{slug}/tokens?group_by=agent", headers=auth_headers)
    assert r.status_code == 200
    rollup = {row["agent"]: row for row in r.json()["rollup"]}
    assert rollup["dev"]["sessions"] == 2
    assert rollup["dev"]["input_tokens"] == 30
    assert rollup["qa"]["sessions"] == 1


def test_get_tokens_group_by_task_returns_rollup(client_with_org, auth_headers, slug, db):
    from src.models import TokenUsage
    db.insert_session_token_usage(task_id="T1", agent="dev", session_id="s1", executor="claude", token_usage=TokenUsage(input_tokens=10))
    db.insert_session_token_usage(task_id="T1", agent="qa",  session_id="s2", executor="claude", token_usage=TokenUsage(input_tokens=20))
    r = client_with_org.get(f"/api/v1/orgs/{slug}/tokens?group_by=task", headers=auth_headers)
    rollup = {row["task_id"]: row for row in r.json()["rollup"]}
    assert rollup["T1"]["sessions"] == 2
    assert rollup["T1"]["input_tokens"] == 30


def test_get_tokens_invalid_group_by(client_with_org, auth_headers, slug):
    r = client_with_org.get(f"/api/v1/orgs/{slug}/tokens?group_by=invalid", headers=auth_headers)
    assert r.status_code == 400


def test_get_tokens_unauthorized(client_with_org, slug):
    r = client_with_org.get(f"/api/v1/orgs/{slug}/tokens")
    assert r.status_code in (401, 403)
```

(Note: `client_with_org`, `auth_headers`, `slug`, and `db` are pytest fixtures defined in `tests/daemon/conftest.py` per the existing pattern — verify their exact names by checking another daemon route test, e.g., `tests/daemon/test_audit_route.py`. Adjust fixture names if they differ in this codebase.)

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/daemon/test_tokens_route.py -v
```
Expected: FAIL — route does not exist.

- [ ] **Step 3: Implement the route**

Create `src/daemon/routes/tokens.py`:
```python
"""GET /api/v1/orgs/{slug}/tokens — per-session listing and rollups.

See spec: docs/superpowers/specs/2026-05-05-token-usage-tracking-design.md §3.2
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from src.daemon.auth import auth_dependency
from src.daemon.state import DaemonState

router = APIRouter()


@router.get("/tokens")
def list_tokens(
    slug: str,
    task_id: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    since: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1, le=10_000),
    group_by: str | None = Query(default=None),
    state: DaemonState = Depends(),
    _auth: object = Depends(auth_dependency),
) -> dict:
    org = state.get_org_or_404(slug)  # mirror existing route helper; adjust to actual API
    db = org.db

    if group_by is not None and group_by not in ("agent", "task"):
        raise HTTPException(status_code=400, detail=f"invalid group_by: {group_by}")

    if group_by == "agent":
        rollup = db.aggregate_session_token_usage_by_agent(since=since, task_id=task_id)
        return {"rollup": rollup}
    if group_by == "task":
        rollup = db.aggregate_session_token_usage_by_task(since=since, agent=agent)
        return {"rollup": rollup}

    rows = db.list_session_token_usage(
        task_id=task_id, agent=agent, since=since, limit=limit,
    )
    return {"rows": rows}
```

(Adjust `state.get_org_or_404` and `auth_dependency` references to match the actual helpers in this codebase — read `src/daemon/routes/audit.py` for the exact pattern. The route signature otherwise is fixed by the spec.)

- [ ] **Step 4: Register the router in `app.py`**

In `src/daemon/app.py`, alongside the other `app.include_router(...)` lines:
```python
from src.daemon.routes import tokens

app.include_router(tokens.router, prefix="/api/v1/orgs/{slug}")
```

- [ ] **Step 5: Run route tests**

```
uv run pytest tests/daemon/test_tokens_route.py -v
```
Expected: 7 passed.

- [ ] **Step 6: Commit**

```bash
git add src/daemon/routes/tokens.py src/daemon/app.py tests/daemon/test_tokens_route.py
git commit -m "feat(daemon): GET /tokens route with filters and group_by rollups"
```

---

## Task 9: Client wrapper

**Files:**
- Modify: `src/client/client.py`

- [ ] **Step 1: Add client methods**

In `src/client/client.py`, add (mirror the `client.audit(...)` shape — the existing pattern usually returns the parsed JSON body):
```python
def list_tokens(
    self,
    slug: str,
    task_id: str | None = None,
    agent: str | None = None,
    since: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    params = {k: v for k, v in {
        "task_id": task_id, "agent": agent, "since": since, "limit": limit,
    }.items() if v is not None}
    r = self.get(f"/api/v1/orgs/{slug}/tokens", params=params)
    r.raise_for_status()
    return r.json()["rows"]


def aggregate_tokens(
    self,
    slug: str,
    group_by: str,                      # 'agent' | 'task'
    task_id: str | None = None,
    agent: str | None = None,
    since: str | None = None,
) -> list[dict]:
    if group_by not in ("agent", "task"):
        raise ValueError(f"group_by must be 'agent' or 'task', got: {group_by}")
    params = {"group_by": group_by}
    if task_id is not None:
        params["task_id"] = task_id
    if agent is not None:
        params["agent"] = agent
    if since is not None:
        params["since"] = since
    r = self.get(f"/api/v1/orgs/{slug}/tokens", params=params)
    r.raise_for_status()
    return r.json()["rollup"]
```

- [ ] **Step 2: Verify (no test added — exercised via CLI tests in Task 10)**

```
uv run pytest tests/ -v
```
Expected: still green.

- [ ] **Step 3: Commit**

```bash
git add src/client/client.py
git commit -m "feat(client): list_tokens / aggregate_tokens wrappers"
```

---

## Task 10: `opc tokens` CLI command

**Files:**
- Create: `tests/test_cli_tokens.py`
- Modify: `src/cli.py`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_cli_tokens.py`. Mirror the existing CLI test pattern — most CLI commands are tested via subprocess invocation against a real daemon, but for unit isolation use `argparse` directly + mocked client:

```python
from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from src.cli import _build_parser  # confirm this is the parser-builder name


def _parse(*args):
    parser = _build_parser()
    return parser.parse_args(args)


def test_tokens_subcommand_parses():
    ns = _parse("tokens", "--org", "myorg")
    assert ns.command == "tokens"
    assert ns.org == "myorg"
    assert ns.by_agent is False
    assert ns.by_task is False


def test_tokens_subcommand_parses_filters():
    ns = _parse("tokens", "--org", "myorg", "--task-id", "TASK-1",
                "--agent", "dev", "--since", "2026-05-01", "--limit", "5", "--json")
    assert ns.task_id == "TASK-1"
    assert ns.agent == "dev"
    assert ns.since == "2026-05-01"
    assert ns.limit == 5
    assert ns.json is True


def test_tokens_subcommand_parses_by_agent():
    ns = _parse("tokens", "--org", "myorg", "--by-agent")
    assert ns.by_agent is True


def test_tokens_subcommand_rejects_by_agent_and_by_task_together(capsys):
    with pytest.raises(SystemExit):
        _parse("tokens", "--org", "myorg", "--by-agent", "--by-task")


def test_cmd_tokens_calls_list_when_no_group_by(capsys):
    from src.cli import cmd_tokens
    args = argparse.Namespace(
        org="myorg", task_id=None, agent=None, since=None, limit=None,
        by_agent=False, by_task=False, json=False,
    )
    fake_client = MagicMock()
    fake_client.list_tokens.return_value = []
    with patch("src.cli.OpcClient.from_env", return_value=fake_client):
        with patch("src.cli.resolve_org_slug", return_value="myorg"):
            with patch("src.cli._fetch_available_orgs", return_value=["myorg"]):
                cmd_tokens(args)
    fake_client.list_tokens.assert_called_once()
    fake_client.aggregate_tokens.assert_not_called()


def test_cmd_tokens_calls_aggregate_when_by_agent(capsys):
    from src.cli import cmd_tokens
    args = argparse.Namespace(
        org="myorg", task_id=None, agent=None, since=None, limit=None,
        by_agent=True, by_task=False, json=False,
    )
    fake_client = MagicMock()
    fake_client.aggregate_tokens.return_value = []
    with patch("src.cli.OpcClient.from_env", return_value=fake_client):
        with patch("src.cli.resolve_org_slug", return_value="myorg"):
            with patch("src.cli._fetch_available_orgs", return_value=["myorg"]):
                cmd_tokens(args)
    fake_client.aggregate_tokens.assert_called_once_with(
        slug="myorg", group_by="agent",
        task_id=None, agent=None, since=None,
    )
```

(Adjust mock targets — `OpcClient.from_env`, `resolve_org_slug`, `_fetch_available_orgs` — to whatever symbols `cmd_audit` actually uses. Check `src/cli.py:cmd_audit` for the canonical pattern.)

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_cli_tokens.py -v
```
Expected: FAIL — `cmd_tokens` and the `tokens` subparser don't exist.

- [ ] **Step 3: Add the argparse subparser**

In `src/cli.py`, find `_build_parser` (or wherever subparsers are registered — look for `add_parser("audit", ...)`). Add alongside it:

```python
p_tokens = subparsers.add_parser(
    "tokens",
    help="show per-session token usage (or rollups via --by-agent / --by-task)",
)
p_tokens.add_argument("--org", default=None)
p_tokens.add_argument("--task-id", dest="task_id", default=None)
p_tokens.add_argument("--agent", default=None)
p_tokens.add_argument("--since", default=None, help="ISO date or timestamp")
p_tokens.add_argument("--limit", type=int, default=None)
p_tokens.add_argument("--json", action="store_true", help="emit JSON")
group = p_tokens.add_mutually_exclusive_group()
group.add_argument("--by-agent", dest="by_agent", action="store_true",
                   help="rollup: one row per agent")
group.add_argument("--by-task", dest="by_task", action="store_true",
                   help="rollup: one row per task")
p_tokens.set_defaults(func=cmd_tokens)
```

- [ ] **Step 4: Implement `cmd_tokens`**

Place near `cmd_audit` (it's the closest sibling pattern). In `src/cli.py`:

```python
def cmd_tokens(args: argparse.Namespace) -> None:
    """Show per-session token usage or aggregated rollups via the daemon."""
    import json as _json

    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    slug = resolve_org_slug(
        args_org=args.org, available=_fetch_available_orgs(client),
    )

    if args.by_agent or args.by_task:
        group_by = "agent" if args.by_agent else "task"
        rollup = client.aggregate_tokens(
            slug=slug, group_by=group_by,
            task_id=args.task_id, agent=args.agent, since=args.since,
        )
        if args.json:
            print(_json.dumps(rollup, indent=2))
            return
        if not rollup:
            print("No token usage rows match the filters.")
            return
        if group_by == "agent":
            print(f"{'Agent':<22} {'Sessions':>8} {'Input':>10} {'Output':>10} {'CacheR':>10} {'Total':>10}")
            print("-" * 76)
            for r in rollup:
                inp = r.get("input_tokens") or 0
                out = r.get("output_tokens") or 0
                rea = r.get("reasoning_tokens") or 0
                cr = r.get("cache_read_tokens") or 0
                total = inp + out + rea
                print(f"{(r.get('agent') or '-'):<22} {r['sessions']:>8} {inp:>10,} {out:>10,} {cr:>10,} {total:>10,}")
        else:
            print(f"{'Task':<14} {'Sessions':>8} {'Input':>10} {'Output':>10} {'CacheR':>10} {'Total':>10}")
            print("-" * 68)
            for r in rollup:
                inp = r.get("input_tokens") or 0
                out = r.get("output_tokens") or 0
                rea = r.get("reasoning_tokens") or 0
                cr = r.get("cache_read_tokens") or 0
                total = inp + out + rea
                print(f"{(r.get('task_id') or '-'):<14} {r['sessions']:>8} {inp:>10,} {out:>10,} {cr:>10,} {total:>10,}")
        return

    rows = client.list_tokens(
        slug=slug, task_id=args.task_id, agent=args.agent,
        since=args.since, limit=args.limit if args.limit is not None else 20,
    )
    if args.json:
        print(_json.dumps(rows, indent=2))
        return
    if not rows:
        print("No token usage rows match the filters.")
        return
    print(f"{'Created':<20} {'Task':<10} {'Agent':<22} {'Exec':<10} {'Input':>10} {'Output':>10} {'CacheR':>10} {'Total':>10}")
    print("-" * 116)
    for r in rows:
        ts = _fmt_ts(r.get("created_at"))
        inp = r.get("input_tokens") or 0
        out = r.get("output_tokens") or 0
        rea = r.get("reasoning_tokens") or 0
        cr = r.get("cache_read_tokens") or 0
        total = inp + out + rea
        print(
            f"{ts:<20} {(r.get('task_id') or '-'):<10} "
            f"{(r.get('agent') or '-'):<22} {(r.get('executor') or '-'):<10} "
            f"{inp:>10,} {out:>10,} {cr:>10,} {total:>10,}"
        )
```

- [ ] **Step 5: Run CLI tests**

```
uv run pytest tests/test_cli_tokens.py -v
```
Expected: 6 passed.

- [ ] **Step 6: Run full unit suite**

```
uv run pytest tests/ -v
```
Expected: still green.

- [ ] **Step 7: Manual smoke** (optional but recommended)

In a temporary runtime — start daemon, init container + org from the sample, run `uv run opc tokens --org <slug>`. Expected: "No token usage rows match the filters." (since no tasks have run yet).

- [ ] **Step 8: Commit**

```bash
git add src/cli.py tests/test_cli_tokens.py
git commit -m "feat(cli): opc tokens command with --by-agent/--by-task rollups"
```

---

## Task 11: Update integration fakes and add E2E test

**Files:**
- Modify: `tests/integration/fake_claude.sh`
- Modify: `tests/integration/fake_codex.sh`
- Create: `tests/integration/test_token_usage_e2e.py`

- [ ] **Step 1: Update `fake_claude.sh` to emit JSON**

The existing `fake_claude.sh` exits with no stdout. Now `ClaudeExecutor` passes `--output-format json`, so the fake must emit a valid JSON object with a `usage` block. Modify the script to print a fixture JSON to stdout before exiting:

```bash
#!/usr/bin/env bash
set -e

PROMPT=""
OUTPUT_FORMAT=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -p) PROMPT="$2"; shift 2 ;;
        --permission-mode) shift 2 ;;
        --allowedTools) shift 2 ;;
        --output-format) OUTPUT_FORMAT="$2"; shift 2 ;;
        *) shift ;;
    esac
done

# (existing TASK_ID / SESSION_ID / AGENT / ORG_SLUG extraction unchanged)
TASK_ID=$(echo "$PROMPT" | awk -F': ' '/^[[:space:]]*task_id: /{gsub(/^[[:space:]]*/, "", $0); print $2; exit}')
SESSION_ID=$(echo "$PROMPT" | awk -F': ' '/^[[:space:]]*session_id: /{gsub(/^[[:space:]]*/, "", $0); print $2; exit}')
AGENT=$(echo "$PROMPT" | awk '/^You are /{sub(/^You are /, "", $0); sub(/\..*$/, "", $0); print; exit}')
ORG_PARENT="${PWD%/workspaces/*}"
ORG_SLUG="${ORG_PARENT##*/}"

if [[ -n "${FAKE_CLAUDE_PLAN:-}" && -f "$FAKE_CLAUDE_PLAN" ]]; then
    bash "$FAKE_CLAUDE_PLAN" "$TASK_ID" "$SESSION_ID" "$AGENT" "$ORG_SLUG"
fi

# Emit a Claude-shaped JSON result with a token usage block when invoked
# in --output-format json mode (which is now always — see CLAUDE.md spec).
if [[ "$OUTPUT_FORMAT" == "json" ]]; then
    cat <<EOF
{"type":"result","result":"ok","model":"claude-sonnet-4-6","usage":{"input_tokens":1000,"output_tokens":500,"cache_creation_input_tokens":300,"cache_read_input_tokens":200}}
EOF
fi

exit 0
```

The plan-extension hook (`FAKE_CLAUDE_PLAN`) keeps working for tests that script side-effects via `opc ...` calls; the JSON emission is appended at the end so it doesn't interfere with the plan's own stdout (plans typically write to stderr or `/tmp` files, not stdout).

(Verify this by re-reading existing `FAKE_CLAUDE_PLAN` files in the repo and confirming none of them write to stdout. If any do, the JSON emission must come *before* the plan, not after — or we need a different mechanism.)

- [ ] **Step 2: Update `fake_codex.sh` to emit a `session_complete` event**

Read the current `fake_codex.sh` and apply the same shape: append a `session_complete` NDJSON event with a `token_usage` block to the end of stdout. Codex events are typically printed as one JSON object per line:

```bash
# (existing argument parsing + plan invocation unchanged)
echo '{"type":"session_complete","model":"gpt-5","token_usage":{"input_tokens":2000,"output_tokens":800,"cached_tokens":150,"reasoning_tokens":100}}'
exit 0
```

- [ ] **Step 3: Write the E2E test**

Create `tests/integration/test_token_usage_e2e.py`:
```python
from __future__ import annotations

import json
import subprocess

import pytest


pytestmark = pytest.mark.integration


def test_claude_session_writes_token_usage_row(daemon_runtime, run_opc):
    """End-to-end: a successful Claude-fake task lands a row in
    session_token_usage and shows up via `opc tokens`."""
    out = run_opc("run", "--org", daemon_runtime.slug, "--brief", "ping")
    task_id = _extract_task_id(out)
    _wait_terminal(daemon_runtime, task_id)

    # Verify the row landed.
    rows = daemon_runtime.db.list_session_token_usage(task_id=task_id)
    assert len(rows) >= 1
    assert all(r["executor"] == "claude" for r in rows)
    assert all(r["input_tokens"] == 1000 for r in rows)  # fake_claude.sh fixture
    assert all(r["output_tokens"] == 500 for r in rows)

    # Verify `opc tokens` lists it.
    listing = run_opc("tokens", "--org", daemon_runtime.slug, "--json")
    parsed = json.loads(listing)
    assert any(p["task_id"] == task_id for p in parsed)


def test_audit_log_carries_token_usage_payload(daemon_runtime, run_opc):
    out = run_opc("run", "--org", daemon_runtime.slug, "--brief", "ping")
    task_id = _extract_task_id(out)
    _wait_terminal(daemon_runtime, task_id)
    audit_rows = daemon_runtime.db.get_audit_log_for_task(task_id)  # adjust to actual API
    session_ends = [r for r in audit_rows if r["action"] == "session_end"]
    assert session_ends
    payload = json.loads(session_ends[0]["payload"]) if isinstance(session_ends[0]["payload"], str) else session_ends[0]["payload"]
    assert "token_usage" in payload
    assert payload["token_count"] == 1500  # 1000 + 500 + 0 reasoning


# Helper stubs — match these to the existing integration helpers in
# tests/integration/conftest.py (e.g., the test_end_to_end.py file should
# show the canonical patterns).
def _extract_task_id(stdout: str) -> str:
    import re
    m = re.search(r"(TASK-\d+)", stdout)
    assert m, f"no TASK-NNN id in: {stdout!r}"
    return m.group(1)


def _wait_terminal(rt, task_id: str, timeout=30) -> None:
    import time
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = rt.db.get_task(task_id)
        if task and task["status"] in ("completed", "failed"):
            return
        time.sleep(0.2)
    raise AssertionError(f"task {task_id} did not reach a terminal state in {timeout}s")
```

(Note: `daemon_runtime`, `run_opc` and `db.get_audit_log_for_task` are fixture names placeholding the real ones in `tests/integration/conftest.py`. Match them to the existing patterns in `tests/integration/test_end_to_end.py`.)

- [ ] **Step 4: Run integration tests**

```
uv run pytest tests/integration/test_token_usage_e2e.py -v -m integration
```
Expected: 2 passed.

- [ ] **Step 5: Run the full integration suite — make sure no other test breaks**

```
uv run pytest tests/ -v -m integration
```
Expected: all 14 existing integration scenarios + the 2 new ones pass. If any existing scenario breaks, it's almost certainly because:
- The fake-Claude script's new JSON emission interferes with the plan-script's stdout (re-order: JSON last, plan first — already the case in step 1).
- An existing test asserts `result.stdout_tail == ""` or similar (Claude now emits a JSON line).

Fix any breakages before continuing.

- [ ] **Step 6: Run unit + integration full pass**

```
uv run pytest tests/ -v -m ""
```
Expected: everything green.

- [ ] **Step 7: Commit**

```bash
git add tests/integration/fake_claude.sh tests/integration/fake_codex.sh tests/integration/test_token_usage_e2e.py
git commit -m "test(integration): emit JSON usage from fake CLIs; add token-usage e2e tests"
```

---

## Task 12: Documentation updates

**Files:**
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Update CLAUDE.md Implementation Order**

In the "Implementation Order (system features)" list, the spec called for adding "token usage tracking" as a sub-bullet under "Audit logging". Edit `CLAUDE.md` — find the line:

```
2. ~~**Audit logging**~~ done — SQLite-backed audit logger.
```

Replace with:

```
2. ~~**Audit logging**~~ done — SQLite-backed audit logger. Per-session `session_end` payloads now carry full `token_usage` dict (input/output/cache_read/cache_creation/reasoning) plus a derived back-compat scalar `token_count`.
```

- [ ] **Step 2: Add `opc tokens` to the CLAUDE.md CLI block**

In the "Per-org" CLI block (look for the `opc audit ...` line), add right after it:

```bash
opc tokens --org <slug> [--task-id X --agent Y --since DATE --limit N --json]   # per-session list
opc tokens --org <slug> --by-agent | --by-task                                  # rollup view
```

- [ ] **Step 3: Update CLAUDE.md Tech Stack — Database bullet**

Find the "**Database**" line and append:

```
- per-session token usage rows live in `session_token_usage` (one per successful subprocess); see `protocol/...` or `docs/superpowers/specs/2026-05-05-token-usage-tracking-design.md`
```

(Or fold it into the existing bullet — pick whichever flows better.)

- [ ] **Step 4: Update README.md — add `opc tokens` to the Per-org Commands table**

In `README.md`, the "Per-org" commands table — add a row alongside `opc audit`:

```
| `opc tokens --org <slug> [--task-id X --agent Y --since DATE --limit N]` | Per-session token usage; `--by-agent` / `--by-task` for rollups |
```

- [ ] **Step 5: Verify docs render reasonably**

```
grep -nE "tokens|session_token_usage" CLAUDE.md README.md
```
Expected: shows the new entries, no obvious typos.

- [ ] **Step 6: Final full-suite confirmation**

```
uv run pytest tests/ -v -m ""
```
Expected: all tests green.

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: opc tokens + session_token_usage table"
```

---

## Self-Review Checklist (run before declaring plan complete)

**Spec coverage:**
- §1 Problem → addressed by entire plan
- §2 Non-Goals → enforced by plan scope (no cost field, no backfill, no streaming, no flag-gate, no per-tool-call breakdown)
- §3.1 CLI shape → Task 10
- §3.2 Daemon route → Task 8
- §4.1 Data flow → Tasks 5+7
- §4.2 Components table → all tasks 1-10 cover it
- §4.3 token_usage semantics (None vs partial) → Task 5 + Task 7
- §5 Schema → Task 1
- §6.1 Claude parser → Task 2
- §6.2 Codex parser → Task 3
- §6.3 opencode parser → Task 4
- §7 Audit log integration → Task 6
- §8.1 Unit tests → Tasks 1-7
- §8.2 Route tests → Task 8
- §8.3 CLI tests → Task 10
- §8.4 Integration test → Task 11
- §9 Migration / rollout → Task 11 (fakes) + Task 12 (docs)
- §11 Implementation Order CLAUDE.md note → Task 12

**Placeholder scan:** No "TBD"/"TODO"/"implement later" in any step. The opencode JSON shape carries an explicit "Note" flagging that real-world verification at integration-time is required — that's a documented uncertainty, not a placeholder.

**Type consistency:** `TokenUsage` field names (`input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_creation_tokens`, `reasoning_tokens`, `model`, `usage_raw_json`) are used identically across model definition (Task 1), all three parsers (Tasks 2-4), `ExecutorResult.token_usage` (Task 5), audit logger (Task 6), DB methods (Task 1), route response (Task 8), client wrappers (Task 9), and CLI renderer (Task 10). `executor` field is `'claude' | 'codex' | 'opencode'` consistently. Method names `insert_session_token_usage` / `list_session_token_usage` / `aggregate_session_token_usage_by_agent` / `aggregate_session_token_usage_by_task` are stable across all references.
