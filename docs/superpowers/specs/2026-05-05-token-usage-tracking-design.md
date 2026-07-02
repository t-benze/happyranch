# Token Usage Tracking — Per-Session Capture and Aggregation

**Status:** Design approved, pending implementation plan
**Author:** Founder + Claude Opus
**Date:** 2026-05-05
**Supersedes:** — (new feature; partially supersedes vestigial `task_results.token_count` and `task_results.estimated_cost` columns)

## 1. Problem

OPC currently has no visibility into token consumption. Every agent session is a subprocess invocation against one of three executor CLIs (Claude Code, Codex, opencode), each of which already emits structured token-usage metadata in its output — but OPC discards it. The audit log's `log_session_end` event accepts a `token_count` parameter that the orchestrator always passes as `None`, and the `task_results` table carries unused `token_count INTEGER` and `estimated_cost REAL` columns from a half-finished prior iteration.

Without per-session capture, the founder cannot:
- Tell which agents/tasks burn through tokens
- Spot regressions when a prompt change blows up token cost
- Make informed decisions about executor selection per role
- Diagnose runaway loops via cost (currently only via orchestration-step count)

This spec wires up per-session capture across all three executors, stores rows in a new dedicated table, and exposes a single `opc tokens` CLI for inspection and rollups.

## 2. Non-Goals

- **No cost estimation.** Pricing tables drift constantly across providers and tiers. Token *counts* are a stable, executor-emitted fact; converting to dollars is a downstream concern that callers can layer on top.
- **No real-time streaming.** Captures happen at session completion, parsed from the subprocess's terminal output. Live progress streaming via NDJSON is out of scope (Cluster 1 considered `--output-format stream-json` for Claude and rejected it as premature).
- **No retroactive backfill.** Sessions completed before this feature lands stay un-tracked. The `task_results.token_count` column is left untouched on existing rows but stops being written by new code paths.
- **No per-tool-call breakdown.** A session is the atomic unit. If a session invokes 12 tools internally, we record one row with the session's totals — not 12 rows.
- **No schema migration to drop `task_results.token_count`/`estimated_cost`.** SQLite migrations in this codebase are append-only; the columns become vestigial-but-harmless.
- **No alerting / threshold enforcement.** This is observability, not policy. A budget-cap feature can layer on top once the data exists.

## 3. User-Facing Interface

### 3.1 CLI

```
opc tokens --org <slug>
  [--task-id TASK-ID]
  [--agent <name>]
  [--since <YYYY-MM-DD>]
  [--limit N]            # default: 20
  [--by-agent | --by-task]
  [--json]
```

**Default view** (no flags): newest 20 sessions, descending by `created_at`. Columns:

```
created_at            task_id    agent              executor   in      out     cache_r  total
2026-05-05T14:22:11Z  TASK-152   engineering_head   claude     12,345    4,201    8,402   16,546
2026-05-05T14:18:03Z  TASK-152   dev_agent          claude     34,887    9,003   15,003   43,890
...
```

`total = (input_tokens or 0) + (output_tokens or 0) + (reasoning_tokens or 0)`. Cache reads are *not* added to total — they're a hint about cache effectiveness, not new consumption.

**`--by-agent`** rollup:

```
agent                sessions    in         out        cache_r     total
engineering_head     42          512,304    104,887    280,002     617,191
dev_agent            21          780,103    223,001    410,887   1,003,104
...
```

Sums respect `--since` / `--agent` / `--task-id` filters. `--by-agent` is mutually exclusive with `--by-task`. Combining `--by-agent` with `--task-id` is allowed (per-agent rollup scoped to one task lineage).

**`--by-task`** rollup: one row per `task_id` (root or any), sums across all sessions on that task.

**`--json`**: emits the same data as a JSON array; rollup output is also JSON.

**Slug resolution**: same as every other per-org command — `--org` flag > `OPC_ORG_SLUG` env > auto-infer (single-org container) > error.

### 3.2 Daemon route

```
GET /api/v1/orgs/<slug>/tokens
  Query: task_id?, agent?, since?, limit?, group_by?  ('agent' | 'task' | absent)
  Response 200:
    { "rows": [ {...session row...}, ... ] }
    or
    { "rollup": [ {...group row...}, ... ] }
```

The route lives in `src/daemon/routes/tokens.py` (new module), wired into the FastAPI app alongside the other per-org routers. Bearer-token-protected like all other routes.

Errors:
- `400` if `--by-agent` and `--by-task` (`group_by=agent&group_by=task`) both supplied — handled at CLI layer; route accepts at most one `group_by`.
- `200 {"rows": []}` (or `{"rollup": []}` for rollups) whenever filters yield no rows — including when `task_id` matches no task. Empty is not an error and the route does not distinguish "unknown task" from "known task with no recorded usage"; both legitimately mean "no data" and callers shouldn't have to query `opc tasks` first to interpret the response.

## 4. Architecture

### 4.1 Data flow

```
[Executor.run()]                                       [run_step]                            [Database]
  ├─ subprocess.Popen(claude/codex/opencode CLI)
  ├─ wait for completion via communicate()
  ├─ on success, parse stdout JSON locally
  ├─ extract usage → unified TokenUsage struct          receives ExecutorResult.token_usage  ──► insert_session_token_usage(...)
  └─ return ExecutorResult(success, token_usage, ...)                                              row in session_token_usage table
                                                                                                    │
                                                                                                    ▼
                                                       audit_logger.log_session_end(
                                                           token_count=usage.total,
                                                           token_usage=usage.model_dump(),
                                                       )                                       audit_log row with full payload
```

Three principles:

1. **Each executor parses its own JSON shape.** `ClaudeExecutor`, `CodexExecutor`, `OpencodeExecutor` each gain a small private parser that maps their per-CLI usage payload to the unified `TokenUsage` model. Per-executor parsing isolation: when Claude bumps its CLI version and changes payload shape, only `_parse_claude_usage` needs updating.
2. **Best-effort, never breaking.** A subprocess timeout, a missing `usage` block, malformed JSON, or an unexpected payload schema all degrade gracefully: the executor returns `token_usage=None` and `run_step` writes a row with NULL token columns but populated keys. The session itself never fails because token capture failed.
3. **Synchronous DB write.** The `insert_session_token_usage` call sits inside `run_step`'s post-execution block, in the same transaction-style flow that already writes `task_results`. No background workers, no queues.

### 4.2 Components

| Component | File | Responsibility |
|-----------|------|----------------|
| `TokenUsage` Pydantic model | `src/models.py` (extend) | Unified per-session usage record; all fields nullable |
| Per-executor parser | `src/orchestrator/executors.py` (extend) | One free function per executor: `_parse_claude_usage`, `_parse_codex_usage`, `_parse_opencode_usage`. Each returns `TokenUsage \| None` |
| `ExecutorResult.token_usage` field | `src/orchestrator/executors.py` | New optional attribute on the existing `ExecutorResult` dataclass |
| `Database.insert_session_token_usage` | `src/infrastructure/database.py` | Inserts a row keyed by `(task_id, agent, session_id)` UNIQUE |
| `Database.list_session_token_usage` / `aggregate_session_token_usage_by_agent` / `aggregate_session_token_usage_by_task` | `src/infrastructure/database.py` | Read APIs powering the route |
| `audit_logger.log_session_end` | `src/infrastructure/audit_logger.py` | Extended payload — adds `token_usage` dict; back-compat `token_count` retained as derived total |
| `tokens` route | `src/daemon/routes/tokens.py` (new) | `GET /api/v1/orgs/<slug>/tokens` |
| `client.list_tokens` / `client.aggregate_tokens` | `src/client/client.py` (extend) | Thin httpx wrappers for the route |
| `cmd_tokens` | `src/cli.py` (extend) | argparse subcommand + table renderer |

### 4.3 Capture point in `run_step`

`Orchestrator._run_session` (or whichever `run_step` helper invokes the executor) wraps the existing `result = executor.run(...)` call. After the executor returns:

```python
if result.token_usage is not None:
    db.insert_session_token_usage(
        task_id=task_id,
        agent=agent_name,
        session_id=result.session_id,
        executor=executor_name,
        token_usage=result.token_usage,
    )
audit.log_session_end(
    task_id=task_id,
    agent=agent_name,
    duration_seconds=result.duration_seconds,
    token_usage=result.token_usage,  # full dict, or None
)
```

**`token_usage` semantics — when is it `None` vs. a `TokenUsage` with NULL fields:**

| Subprocess outcome | Parser returns | DB row written? |
|---|---|---|
| Success, JSON parsed, all fields extracted | `TokenUsage(input=..., output=..., ...)` | yes, fully populated |
| Success, JSON parsed, no `usage` block found | `TokenUsage(usage_raw_json=<raw>)` (token fields all `None`) | yes, NULL token columns + raw payload preserved |
| Success, JSON parse failed (malformed/unexpected shape) | `TokenUsage(usage_raw_json=<stdout snippet>)` (token fields all `None`) | yes, NULL token columns + raw payload preserved (with `WARNING` logged) |
| Failure (timeout, non-zero exit) | `None` | no — failure is captured by `task_results.status` and the audit `session_end` payload's `token_count: null` |

Rule: a successful subprocess always produces *some* `TokenUsage`, even if it's empty. A failed subprocess produces `None`. This means the DB row count for `session_token_usage` matches the count of successful sessions, and "we tried but couldn't parse" is forensically distinguishable via NULL token columns + non-NULL `usage_raw_json`.

The DB insert uses `INSERT OR IGNORE` to absorb the rare case of a re-run hitting the same `(task_id, agent, session_id)` UNIQUE key (e.g. orchestrator restart after a crash mid-write).

## 5. Schema

```sql
CREATE TABLE session_token_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    agent      TEXT NOT NULL,
    session_id TEXT NOT NULL,
    executor   TEXT NOT NULL,            -- 'claude' | 'codex' | 'opencode'
    model      TEXT,                     -- e.g. 'claude-sonnet-4-6', 'gpt-5'

    input_tokens          INTEGER,
    output_tokens         INTEGER,
    cache_read_tokens     INTEGER,
    cache_creation_tokens INTEGER,
    reasoning_tokens      INTEGER,       -- Codex/o1-style; NULL for Claude/typical opencode

    usage_raw_json TEXT,                 -- per-executor original payload, forensic backup

    created_at TEXT NOT NULL,
    UNIQUE (task_id, agent, session_id)
);

CREATE INDEX idx_session_token_usage_task   ON session_token_usage (task_id);
CREATE INDEX idx_session_token_usage_agent  ON session_token_usage (agent, created_at);
```

**Migration**: append `CREATE TABLE IF NOT EXISTS session_token_usage` and the two `CREATE INDEX IF NOT EXISTS` statements to `Database._init_schema`. Idempotent on every daemon start, same pattern as the existing `task_results` table.

**Pydantic model:**

```python
# src/models.py
class TokenUsage(BaseModel):
    input_tokens:          int | None = None
    output_tokens:         int | None = None
    cache_read_tokens:     int | None = None
    cache_creation_tokens: int | None = None
    reasoning_tokens:      int | None = None
    model:                 str | None = None
    usage_raw_json:        str | None = None  # JSON-serialized original payload

    @property
    def total(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0) + (self.reasoning_tokens or 0)
```

`total` deliberately excludes cache reads — cache hits are not new consumption, they're effectiveness signal.

**Vestigial columns retired:** `task_results.token_count` and `task_results.estimated_cost` are no longer written by any code path after this lands. Existing populated rows keep their values (forensic continuity). Code paths that previously wrote them stop, with a one-line comment explaining where token data now lives.

## 6. Capture per Executor

### 6.1 Claude Code

**CLI change** — add `--output-format json` to the existing invocation in `ClaudeExecutor.run`:

```python
cmd = [
    self._cli_path,
    "-p", prompt,
    "--permission-mode", self._permission_mode,
    "--allowedTools", allowed,
    "--output-format", "json",
]
```

This is the only behavioral change to the executor. `--allowedTools` and `--permission-mode` are orthogonal and continue to work.

**Output shape** (Claude Code 2.1.x with `--output-format json`):

```json
{
  "type": "result",
  "result": "...assistant final text...",
  "model": "claude-sonnet-4-6",
  "usage": {
    "input_tokens": 12345,
    "output_tokens": 4201,
    "cache_creation_input_tokens": 8042,
    "cache_read_input_tokens": 8402,
    "service_tier": "standard"
  },
  ...
}
```

**Parser** (`_parse_claude_usage(stdout: str) -> TokenUsage | None`). Per §4.3, the parser is only called for successful subprocesses, and always returns *some* `TokenUsage` (never `None`) so the row gets written:

1. Strip + locate the trailing JSON object (the CLI emits a single object on success).
2. `json.loads`. On failure: `logger.warning(...)` and return `TokenUsage(usage_raw_json=<stdout snippet>)` with all token fields `None` (forensic preservation).
3. On success — map:
   - `input_tokens` ← `usage.input_tokens`
   - `output_tokens` ← `usage.output_tokens`
   - `cache_read_tokens` ← `usage.cache_read_input_tokens`
   - `cache_creation_tokens` ← `usage.cache_creation_input_tokens`
   - `reasoning_tokens` ← `None`
   - `model` ← top-level `model`
   - `usage_raw_json` ← `json.dumps(usage_obj)`
4. Any missing key falls through to `None` — the row gets written with the keys that did parse, the absence is informative.

(`-> TokenUsage | None` in the signature exists because callers can pass `None` upstream when the subprocess itself failed; the parser itself never returns `None`.)

**Stdout handling** — `_run_command` currently truncates stdout to `_TAIL_BYTES = 2000` for the `stdout_tail` field. The full stdout must remain available for parsing before truncation. Concretely: parse first, then truncate for `stdout_tail`. This is an internal change in `_run_command` to receive a parser callback or pass the full stdout back to the caller.

### 6.2 Codex

**No CLI change.** Codex already runs with `exec --json -` and emits NDJSON events.

**Output shape** (Codex 0.125+, terminal event):

```jsonl
{"type": "agent_message", ...}
{"type": "tool_call", ...}
...
{"type": "session_complete", "token_usage": {"input_tokens": 34887, "output_tokens": 9003, "cached_tokens": 15003, "reasoning_tokens": 1234}, "model": "gpt-5"}
```

**Parser** (`_parse_codex_usage(stdout: str) -> TokenUsage | None`). Same all-or-`TokenUsage` discipline as Claude:

1. Walk lines; for each, attempt `json.loads`. Skip non-JSON lines (preamble, blank lines).
2. Find the last event whose `type == "session_complete"` (or the documented terminal event name; verify against current Codex emission during implementation).
3. If no `session_complete` event found: return `TokenUsage(usage_raw_json=<stdout snippet>)` with token fields `None`.
4. Extract `token_usage` and `model`. Map:
   - `input_tokens` ← `token_usage.input_tokens`
   - `output_tokens` ← `token_usage.output_tokens`
   - `cache_read_tokens` ← `token_usage.cached_tokens`
   - `cache_creation_tokens` ← `None` (Codex doesn't separate creation from read)
   - `reasoning_tokens` ← `token_usage.reasoning_tokens`
   - `model` ← top-level `model` field on the event
   - `usage_raw_json` ← raw event JSON

### 6.3 opencode

**No CLI change.** opencode already runs with `--format json`.

**Output shape** (opencode emits a JSON object with an array of messages, each carrying its own usage):

```json
{
  "messages": [
    {"role": "user", ...},
    {"role": "assistant", "usage": {"input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 0, "cache_write_tokens": 100}, "model": "claude-sonnet-4-6"},
    {"role": "assistant", "usage": {"input_tokens": 200, "output_tokens": 75, "cache_read_tokens": 100, "cache_write_tokens": 0}, "model": "claude-sonnet-4-6"},
    ...
  ],
  ...
}
```

(Schema TBD against current opencode version during implementation; this is the expected shape based on protocol/skills/manage-agent context. Implementer must verify against an actual session output before finalizing the parser.)

**Parser** (`_parse_opencode_usage(stdout: str) -> TokenUsage | None`). Same all-or-`TokenUsage` discipline:

1. `json.loads(stdout)`. On failure: return `TokenUsage(usage_raw_json=<stdout snippet>)` with token fields `None`.
2. Walk `messages[]`, sum each role-`assistant` message's `usage` fields.
3. Map summed values to `TokenUsage`. `model` taken from the last assistant message (sessions occasionally span multiple models for tool use; last is the canonical "this session ran on" answer).
4. `usage_raw_json` ← JSON-serialized list of the per-message usage objects.
5. Empty `messages` or all-missing `usage` → return `TokenUsage(usage_raw_json=<full stdout>)` with token fields `None`.

## 7. Audit Log Integration

`AuditLogger.log_session_end` signature evolves:

```python
def log_session_end(
    self,
    task_id: str,
    agent: str,
    duration_seconds: int,
    token_usage: TokenUsage | None = None,
) -> None:
    payload = {"duration_seconds": duration_seconds}
    if token_usage is not None:
        payload["token_usage"] = token_usage.model_dump()
        payload["token_count"] = token_usage.total  # back-compat scalar
    else:
        payload["token_count"] = None
    self._db.insert_audit_log(task_id=task_id, agent=agent, action="session_end", payload=payload)
```

Audit consumers reading the old `token_count` integer field continue to work — the field is still present, now derived from the structured usage. New consumers can read `token_usage` for the breakdown.

The orchestrator call site (currently `self._audit.log_session_end(task_id, agent_name, result.duration_seconds)`) updates to pass `token_usage=result.token_usage`.

## 8. Testing Strategy

### 8.1 Unit tests

| Area | Test |
|------|------|
| Parsers | One test per executor with a real-shape JSON fixture (captured from actual `claude --output-format json`, `codex exec --json`, `opencode run --format json` runs) → asserts mapped `TokenUsage` |
| Parsers — robustness | Each parser handles: empty stdout, malformed JSON, missing `usage` block, missing fields → returns `None` or `TokenUsage` with NULL fields, never raises |
| Database | `insert_session_token_usage` round-trips via `list_session_token_usage`; UNIQUE constraint on `(task_id, agent, session_id)` enforced; `INSERT OR IGNORE` semantics |
| Database — aggregation | `aggregate_session_token_usage_by_agent(since=)` sums correctly across multiple rows; `aggregate_session_token_usage_by_task` likewise |
| `run_step` | (a) Subprocess-success + parse-success → row written with all populated columns + audit log carries dict. (b) Subprocess-success + parse-failure → row written with NULL token columns + populated `usage_raw_json` + `WARNING` logged + audit log carries the (mostly-NULL) `TokenUsage` dict. (c) Subprocess-failure (timeout / non-zero exit) → no row + audit log carries `token_count: null`. |
| Audit logger | Back-compat `token_count` field present and correctly derived; new `token_usage` dict present when provided |
| Models | `TokenUsage.total` excludes cache reads; sums `None` fields as 0 |

### 8.2 Route tests

| Endpoint | Test |
|----------|------|
| `GET /tokens` (no filter) | Returns newest N sessions ordered by `created_at DESC` |
| `GET /tokens?task_id=X` | Filters correctly; empty result on unknown task returns `200 {"rows": []}` per spec §3.2 |
| `GET /tokens?agent=X&since=Y` | Combined filter works; `since` is inclusive |
| `GET /tokens?group_by=agent` | Returns rollup shape `{"rollup": [...]}` |
| `GET /tokens?group_by=task` | Returns rollup keyed by `task_id` |
| `GET /tokens?group_by=invalid` | `400` |
| Auth | Missing bearer token returns `401`; wrong org slug returns `404` |

### 8.3 CLI tests

| Command | Test |
|---------|------|
| `opc tokens --org X` | Default view renders table with expected columns; `--limit` honored |
| `opc tokens --org X --task-id Y` | Filter passed to route; output filtered |
| `opc tokens --org X --by-agent` | Rollup view rendered; `--by-task` likewise |
| `opc tokens --org X --by-agent --by-task` | CLI rejects with usage error before hitting daemon |
| `opc tokens --org X --json` | Emits valid JSON to stdout |

### 8.4 Integration test (fake CLIs)

One end-to-end test in `tests/integration/`: spawn the real daemon, run a task through the fake-Claude binary (which now emits `--output-format json`-shaped output with usage), assert that:

1. The task completes successfully.
2. A row appears in `session_token_usage` for the session.
3. `opc tokens --org <slug>` lists the row.
4. Audit log's `session_end` payload includes the `token_usage` dict.

The fake-Codex and fake-opencode binaries get parallel updates so tests cover all three executors. Updating these fakes is the largest blast-radius change in this feature — they're shared by all 14 existing integration scenarios in `tests/integration/`.

## 9. Migration / Rollout

- **Schema**: append-only. New table + indexes added in `_init_schema`. No drops, no renames.
- **Code**: feature is on the critical path for every session after this lands — no flag-gating. Best-effort capture means an executor whose output shape we mis-parsed produces NULL rows but never breaks tasks; this is the rollout safety net.
- **Existing fake CLIs**: `tests/fakes/fake_claude*.py` and parallel files for Codex/opencode must switch to JSON output mode. This is mandatory — without it, the existing integration suite would break the moment ClaudeExecutor passes `--output-format json`. Bundled into the same PR as the parser work.
- **Vestigial columns**: `task_results.token_count` and `task_results.estimated_cost` get a one-line comment in `database.py` noting "no longer written; see session_token_usage table". Rows already populated stay.

## 10. Open Questions Resolved During Brainstorming

- **Storage location**: new dedicated `session_token_usage` table (option B in cluster 2). Rejected extending `task_results` (mixes concerns) and JSON-only-in-audit-log (bad fit for SQL aggregations).
- **Schema fields**: full unified shape — input/output/cache_read/cache_creation/reasoning + model + `usage_raw_json` (option C). Rejected ultra-minimal (loses cache-effectiveness signal) and "minimal + reasoning" without raw JSON (forensic gap).
- **CLI shape**: single `opc tokens` command with flags (option A). Rejected subcommand structure (deviates from `opc audit`'s flag-driven idiom) and rollup-only (hides per-session use case).
- **Capture mechanism**: switch Claude to `--output-format json` (option A). Rejected stream-json (premature complexity), opt-in flag (adds a flag we'd never flip off), Claude-skip (loses majority of usage data since Claude is the default executor).

## 11. Implementation Order Reminder

Per CLAUDE.md "Implementation Order (system features)", token tracking is *not* one of the listed milestones (10–13). It's a smaller diagnostic feature that lands as a single PR rather than a milestone-level effort. Should still be tracked in the next CLAUDE.md update once shipped, perhaps as a sub-bullet under "Audit logging" or its own line ("~~Token usage tracking~~ done").

## 12. Post-Landing Clarification (Issue #216, 2026-06-28)

### Metric semantics: churn vs context

The original `total_tokens` was defined as `input + output + reasoning`, excluding cache columns. This is renamed in CLI/API presentation to **churn** (`churn_tokens`) — the fresh, non-cache work a session performed. A new complementary computed column **context_tokens** = `churn + cache_read + cache_creation` captures the cache-inclusive total of all recorded token fields. Both are computed columns (no schema migration), exposed in every rollup and the per-session listing.

- `total_tokens` is retained for backward compatibility (identical to `churn_tokens`).
- CLI column headers renamed from "Total" to "Churn"; an "AllTokens" column shows `context_tokens`.
- Rankings and thresholds (--top, --over-threshold) continue to use churn only — cache is never folded into ranking.

### Dream-runner token persistence bug

`dream_runner.py` passed `usage=result.token_usage` to `Database.insert_session_token_usage`, but the parameter name is `token_usage=`. This caused `TypeError: unexpected keyword argument 'usage'` on every dream session that returned token usage. Fixed to `token_usage=result.token_usage`.

### Codex `input_tokens` includes `cached_input_tokens` (CONFIRMED, issue #216)

Codex CLI follows the OpenAI convention where `input_tokens` is the inclusive total (includes `cached_input_tokens`). Confirmed live: one code_reviewer turn recorded input=4,412,984 with cached=4,307,072. On ingest, the parser normalizes `input_tokens` to net-fresh = max(input - cached, 0), making it apples-to-apples with Claude (where cache is tracked in a separate column). `cache_read_tokens` is preserved as-is. Normalization is forward-only; historical rows are NOT retro-corrected (founder accepted).

### Opencode command shape and parser update

Opencode >= 1.14.31 rejects the `--prompt` flag (now positional). The executor command was updated to pass the prompt positionally. The parser now handles both the old single-JSON-object format (`messages[].usage`) and the new JSONL format (`step_finish.part.tokens`).

### Pi structured token parsing

Pi 0.80.2+ emits usage fields on terminal JSONL events (`message_end` and `turn_end`) at `message.usage` with keys `input`, `output`, `cacheRead`, `cacheWrite`, `totalTokens`. The parser extracts the last terminal event's usage into a structured `TokenUsage` instead of preserving only raw JSON.
