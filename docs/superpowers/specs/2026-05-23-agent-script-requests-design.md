# Agent Script Requests — Design Spec

**Date:** 2026-05-23
**Status:** Draft, pending implementation plan.
**Relates to:**
- `docs/superpowers/specs/2026-04-21-opc-revisit-design.md` — the unblock path after a script runs (`grassland revisit <task-id>` with an extended header that surfaces the SR output).
- `docs/superpowers/specs/2026-05-13-threads-design.md` — agent-initiated → founder-review pattern; route shape, SSE conventions, and web feature scaffold reused.
- `protocol/skills/dispatch/SKILL.md`, `protocol/skills/manage-repo/SKILL.md` — sibling `--from-file` agent callback skills; matching the same single-line invocation discipline.
- `docs/superpowers/specs/2026-05-14-web-ui-design.md` — three-layer web architecture (lib/api → features/<domain> → components) and OpenAPI snapshot contract.

## 1. Goal

Give agents a first-class escape hatch when they need a command run with permissions their executor sandbox doesn't grant — typically a `gh`/`aws`/`ssh`/`sudo`-class invocation that needs founder-grade credentials, or any binary outside the agent's `allow_rules` prefix list.

Today an agent that hits a permission wall must escalate in prose: "please run `aws s3 sync …` for me." That loses the exact script text, gives the founder no replayable trail, and creates no audit object the orchestrator can hang state off of.

The new primitive — **script requests** — is symmetric with threads/talks/dispatch:

1. The agent submits an `SR-NNN` row containing the full script text, an interpreter, a rationale, and a working-directory hint.
2. The founder reviews via CLI or web and either **runs** it (daemon spawns the subprocess, captures stdout/stderr/exit-code, streams output back live) or **rejects** it with a reason.
3. The agent self-blocks its task referencing the SR-NNN; once the founder has the output, they unblock the work with `grassland revisit <task-id>`, which now surfaces SR outputs in its context header.

Use case examples:

- An engineering worker mid-task needs to close a GitHub PR but its `allow_rules` only cover `gh pr comment` — it submits `gh pr close 247 --comment "..."` for the founder to run.
- A support worker realizes the customer's refund needs a one-off `stripe refunds create` call; the agent has no Stripe CLI prefix at all, so it submits the full command.
- A manager needs the founder to rotate an API key (`aws iam create-access-key …`) and have the output pasted back so the agent's next task can wire it up; the manager submits, founder runs, manager's revisited task reads the captured stdout.

## 2. Non-goals

**Out of scope for v1:**

- Multi-reviewer approval. Founder is the sole reviewer.
- Founder-edits-before-run. If the script is wrong, founder rejects with a reason and the agent re-submits. Editing in place re-opens authorship-ambiguity questions ("whose script ran?") we don't want to litigate in v1.
- Scheduled / cron runs. v1 is on-demand only.
- Per-script secrets injection (e.g., "fetch X from a vault and substitute"). Daemon runs with whatever env it was launched with; founder is expected to keep credentials in their shell env.
- Auto-unblock on script completion. The agent self-blocks; the founder uses the existing `grassland revisit` primitive to unblock. No new "task wakes itself" channel.
- Agent-readable output without revisit. Workers cannot poll for their own SR's output mid-task and decide what to do with it — that would re-introduce blocking semantics. Revisit is the only path back into the agent.
- Script libraries / templates. Each submission is one-shot.
- Cross-org script submission. SRs are per-org, same as every other primitive.
- Founder authoring SRs from inside an agent session. SRs are agent-composed only — if the founder wants to run something, they just run it.

**Explicitly in scope but minimal:**

- A single interpreter set: `bash`, `sh`, `zsh`, `python3`. No general-purpose interpreter registration.
- Output truncation: head ~64 KB of stdout/stderr stored in DB columns for fast list/detail rendering; the full streams written to disk for audit.

## 3. Data model

### 3.1 New table — `script_requests`

Idempotent `CREATE TABLE IF NOT EXISTS` on daemon startup, per-org DB (`<runtime>/orgs/<slug>/grassland.db`):

```sql
CREATE TABLE IF NOT EXISTS script_requests (
    id                  TEXT PRIMARY KEY,                  -- "SR-NNN", monotonically allocated per-org
    task_id             TEXT NOT NULL,                     -- always FK to the submitting agent's active task
    agent_name          TEXT NOT NULL,                     -- composer; redundant with tasks.assigned_agent but cached for query speed
    title               TEXT NOT NULL,                     -- ≤ 200 chars, founder-facing one-line summary
    rationale           TEXT NOT NULL,                     -- markdown, why the script is needed; renders in founder review surface
    script_text         TEXT NOT NULL,                     -- full source; no length cap in v1 (sanity limit at 64 KB enforced in route)
    interpreter         TEXT NOT NULL,                     -- one of {"bash","sh","zsh","python3"}
    cwd_hint            TEXT,                              -- relative to <runtime>/orgs/<slug>/workspaces/<agent>/; NULL = workspace root
    status              TEXT NOT NULL DEFAULT 'pending',   -- {pending, rejected, running, completed, failed}
    exit_code           INTEGER,                           -- populated on completed/failed
    stdout_head         TEXT,                              -- head ~64 KB of stdout (truncation marker appended if cut)
    stderr_head         TEXT,                              -- head ~64 KB of stderr
    stdout_path         TEXT,                              -- absolute path to <runtime>/orgs/<slug>/scripts/SR-NNN.out
    stderr_path         TEXT,                              -- absolute path to <runtime>/orgs/<slug>/scripts/SR-NNN.err
    duration_ms         INTEGER,                           -- finished_at - started_at, populated on completed/failed
    started_at          TEXT,                              -- ISO8601 UTC; set when status enters 'running'
    finished_at         TEXT,                              -- ISO8601 UTC; set when status enters terminal state
    reviewed_at         TEXT,                              -- ISO8601 UTC; set when status enters {rejected, running}
    reviewed_by         TEXT,                              -- "founder" (only reviewer in v1; kept as text for future multi-reviewer)
    reject_reason       TEXT,                              -- founder-supplied; required when status='rejected'
    cwd_resolved        TEXT,                              -- absolute path actually used at run-time; populated when status enters 'running'
    timeout_seconds     INTEGER NOT NULL DEFAULT 300,      -- overridable at run-time by founder --timeout-seconds
    created_at          TEXT NOT NULL                      -- ISO8601 UTC; set at insert
);

CREATE INDEX IF NOT EXISTS idx_script_requests_task        ON script_requests(task_id);
CREATE INDEX IF NOT EXISTS idx_script_requests_agent       ON script_requests(agent_name);
CREATE INDEX IF NOT EXISTS idx_script_requests_status      ON script_requests(status);
CREATE INDEX IF NOT EXISTS idx_script_requests_created_at  ON script_requests(created_at);
```

ID allocation mirrors `next_thread_id` / `next_talk_id`: zero-padded sequence (`SR-001`, `SR-002`, …) minted inside `org.db_lock`.

### 3.2 Pydantic model — `ScriptRequestRecord`

Add to `src/models.py`:

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

### 3.3 Audit events

| Action | Scope | Payload |
|---|---|---|
| `script_submitted`        | `task_id` + `script_request_id` | `{title, interpreter, cwd_hint, byte_size, line_count, agent}` |
| `script_rejected`         | `script_request_id`             | `{reviewer, reason}` |
| `script_run_started`      | `script_request_id`             | `{reviewer, cwd_resolved, timeout_seconds, interpreter}` |
| `script_run_completed`    | `script_request_id`             | `{exit_code, duration_ms, stdout_bytes, stderr_bytes, truncated_stdout: bool, truncated_stderr: bool}` |
| `script_run_failed`       | `script_request_id`             | `{exit_code?, duration_ms?, reason}` — `reason ∈ {timeout, spawn_failed, killed, internal_error}` |

`script_run_completed` is emitted on any natural process exit (including non-zero — completion is "process terminated of its own accord"). `script_run_failed` is reserved for daemon-side failures: timeout-killed, spawn syscall failure, or unexpected internal exception.

### 3.4 On-disk artifacts

Per-org directory `<runtime>/orgs/<slug>/scripts/` (created on first SR):

- `SR-NNN.out` — full stdout (no truncation), written incrementally during run, fsync'd on completion.
- `SR-NNN.err` — full stderr, same.
- `SR-NNN.script` — copy of the executed script text, frozen at run-time so the file matches what actually ran even if (hypothetically) the DB column changed (v1 disallows edits, but the file gives us a permanent forensic record).

## 4. Agent-side flow

### 4.1 Skill — `protocol/skills/scripts/SKILL.md`

New skill, summarized:

- **When to use:** an executor permission denial (Claude `--allowedTools` reject, opencode `permission.bash` deny, Codex sandbox block); OR a command the agent knows from its `allow_rules` frontmatter that it can't run; OR an operation that genuinely needs founder-grade credentials (`gh` push scopes, `aws`, `stripe`, `ssh` to prod). Should NOT be used for anything the agent could just `chmod +x` and run in its own workspace.
- **Single-line callback discipline:** write `/tmp/script-<random>.json` with the payload, invoke `grassland scripts submit --from-file /tmp/script-<random>.json` as a single bash line. Same rationale as `report-completion` (Claude permission matcher splits multi-line bash on newlines/`&&`/`;`/`|`).
- **Lifecycle expectation:** after submit, the agent must self-block the task — finish its session with `report-completion` carrying `status="blocked"` and `summary="Awaiting SR-NNN: <title>"`. The orchestrator's manager will see the block and escalate to the founder. Once the founder runs the SR, they use `grassland revisit <task-id>` to spawn a fresh root with the SR output available in context.

Cross-references added to `protocol/skills/start-task/SKILL.md` ("if you hit a permission wall, see `scripts/`") and to the executor adapter docs (manage-repo, manage-agent siblings).

### 4.2 Payload shape

`/tmp/script-<random>.json`:

```json
{
  "task_id": "TASK-091",
  "session_id": "8b3f-...-e91a",
  "title": "Close PR #247 with approval comment",
  "rationale": "PR review is complete. My allow_rules cover `gh pr comment` but not `gh pr close`. Need founder to merge-close so the auth-rewrite branch can be deleted.",
  "script": "set -euo pipefail\ngh pr close 247 --comment 'Approved and closed per review thread THR-014.'\n",
  "interpreter": "bash",
  "cwd_hint": "repos/web-app"
}
```

Required: `task_id`, `session_id`, `title`, `rationale`, `script`, `interpreter`. Optional: `cwd_hint` (relative path under the agent's workspace; if absent, the workspace root is used).

`task_id` and `session_id` are both supplied by the agent (the start-task skill bakes them into the session context, same as `report-completion` / `compose-as-agent`). Agents can run concurrent sessions per the project invariants, so the daemon CANNOT auto-derive `task_id` from "find the active session for this agent" — there may be more than one. Daemon-side validation in §5.1 verifies the session ownership chain.

### 4.3 CLI command — `grassland scripts submit`

Single-line, `--from-file`-only. No flag-form for the payload (consistent with `report-completion`, `manage-repo`, `dispatch`). Output:

```
ok: submitted SR-019 (status=pending). Self-block your task referencing this ID.
```

Exit 0 on 201 from the daemon; non-zero with a human-readable error otherwise.

### 4.4 Allow-rule impact

None. The baseline `grassland` prefix already covers `grassland scripts submit`. No agent frontmatter or `.claude/settings.json` edits needed.

## 5. HTTP API

All routes under `/api/v1/orgs/{slug}/scripts/`. The agent-callback route (`POST /submit`) is listed in `EXCLUDED_PATHS` of `web/src/test/openapi-coverage.test.ts` with reason "agent-only callback"; all other routes get TS mirrors in `web/src/lib/api/scripts.ts`.

### 5.1 `POST /submit` — agent callback

Request body matches §4.2 (agent supplies `task_id` and `session_id` explicitly).

Validation order, each step gating the next:

1. **Agent identity from bearer** — the bearer token resolves to an agent name via the existing token-scoping map. Else 401 `unknown_agent`.
2. **Task exists and is owned by this agent** — `tasks` row for `task_id` exists; `tasks.assigned_agent == agent`. Else 404 `unknown_task` / 403 `agent_not_task_owner`.
3. **Session ownership** — `SessionTracker.expected_session_id(task_id) == session_id` (same check used by `report-completion`). Else 409 `session_mismatch`.
4. **Task status** — task is `pending` or `in_progress`. Else 400 `task_not_active`.
5. **`title`** — non-empty after strip, ≤ 200 chars. Else 422 `empty_title` / `title_too_long`.
6. **`rationale`** — non-empty after strip. Else 422 `empty_rationale`.
7. **`script`** — non-empty after strip, ≤ 65536 bytes (UTF-8 encoded). Else 422 `empty_script` / `script_too_large`.
8. **`interpreter`** — exactly one of `{bash, sh, zsh, python3}`. Else 422 `unknown_interpreter`.
9. **`cwd_hint`** (if present) — non-absolute, no `..` segments after normalization, resolves under `<runtime>/orgs/<slug>/workspaces/<agent>/`. Path existence is NOT checked at submit-time (the founder may want to create it before running); existence is re-checked at `/run` time. Else 422 `invalid_cwd_hint`.

Effect (single transaction under `org.db_lock`):

1. Allocate `id = state.db.next_script_request_id()`.
2. Insert `script_requests` row with `status='pending'`, `created_at=now`, and the auto-injected `task_id` / `agent_name`.
3. Audit `script_submitted`.

Response 201:

```json
{
  "id": "SR-019",
  "status": "pending",
  "created_at": "2026-05-23T10:14:02Z"
}
```

### 5.2 `GET /` — list

Founder-only. Query params: `status` (CSV of statuses; default = all), `agent` (single agent name), `task_id`, `limit` (default 50, max 200), `cursor` (created_at-keyed, opaque).

Response: array of full `ScriptRequestRecord` objects (no field omission — the script text is shown in list view because rationale-without-script is rarely useful for triage). Add a `next_cursor` field when more pages remain.

### 5.3 `GET /{id}` — detail

Founder-only. Returns full `ScriptRequestRecord`. 404 `unknown_script_request` if not found.

### 5.4 `POST /{id}/run` — founder triggers execution

Founder-only. Request body:

```json
{
  "cwd_override":      "repos/web-app/scripts/",   // optional; absolute OR rel to workspace root
  "timeout_seconds":   600                          // optional; overrides the stored 300 default
}
```

Validation:

1. SR exists. Else 404.
2. SR status is `pending`. Else 409 `not_pending` (covers already-running, rejected, completed, failed).
3. If `cwd_override` is set, normalize and verify it exists as a directory; absolute paths are allowed (founder is trusted), relative resolves against `<runtime>/orgs/<slug>/workspaces/<agent>/`. Else 422 `invalid_cwd_override`.
4. If `cwd_override` is not set, use `cwd_hint` (or workspace root). Verify the resolved path exists. Else 409 `cwd_missing` (carries the resolved path for diagnosis).
5. `timeout_seconds` ∈ [1, 86400]. Else 422 `invalid_timeout`.

Effect, atomically under `org.db_lock`:

1. Transition status `pending → running`; set `reviewed_at=now`, `reviewed_by='founder'`, `started_at=now`, `cwd_resolved=<absolute>`, `timeout_seconds=<resolved>`.
2. Write `<runtime>/orgs/<slug>/scripts/SR-NNN.script` (frozen copy).
3. Audit `script_run_started`.
4. Spawn subprocess (see §6). Spawning is OUTSIDE the lock — the lock only covers the DB transition. If `subprocess.Popen` raises, transition status `running → failed` with `reason=spawn_failed` and audit `script_run_failed`.

Response 202:

```json
{
  "id": "SR-019",
  "status": "running",
  "started_at": "2026-05-23T10:18:44Z",
  "cwd_resolved": "/Users/founder/grassland-runtime/orgs/hk-tour/workspaces/engineering_head/repos/web-app",
  "timeout_seconds": 600,
  "events_url": "/api/v1/orgs/hk-tour/scripts/SR-019/events"
}
```

### 5.5 `GET /{id}/events` — SSE stream

Founder-only. Server-sent events:

- `event: stdout` / `data: {"line": "...", "ts": "..."}` — one event per stdout line, line-buffered.
- `event: stderr` / `data: {"line": "...", "ts": "..."}` — one event per stderr line.
- `event: terminal` / `data: {"status": "completed"|"failed", "exit_code": N, "duration_ms": N, "reason": "..."?}` — sent exactly once, then the stream closes.

If the SR is already terminal at connect time, the route emits a `terminal` event immediately (with the final status) and closes — no replay of historical stdout/stderr lines over SSE. Use `GET /{id}/output` for after-the-fact reads.

Late subscribers during a `running` SR see only events from the moment they connect; they do NOT get a replay of earlier output. This is acceptable in v1 because (a) the live-output UX is primarily for the founder watching their own click, and (b) the full output is always retrievable via `/output` once terminal.

### 5.6 `POST /{id}/reject` — founder rejects

Founder-only. Request body: `{"reason": "..."}` (non-empty after strip; ≤ 1000 chars).

Validation: SR exists, status is `pending`. Else 409 `not_pending`.

Effect: status `pending → rejected`; set `reviewed_at`, `reviewed_by='founder'`, `reject_reason`. Audit `script_rejected`. Response 200 with the updated record.

### 5.7 `GET /{id}/output` — post-run output

Founder-only. Query params: `stream` ∈ {`stdout`, `stderr`, `both`}, default `both`. `max_bytes` (default 1_048_576 = 1 MiB; max 10 MiB).

Returns JSON `{stdout: "...", stderr: "...", truncated_stdout: bool, truncated_stderr: bool, total_stdout_bytes: int, total_stderr_bytes: int}`. Reads from the on-disk `.out` / `.err` files (not the DB head columns), so even after DB-column truncation the full output is reachable.

If status is not terminal (`pending`, `running`), returns 409 `not_terminal`.

## 6. Subprocess execution

### 6.1 Spawning

The daemon spawns the subprocess via `asyncio.create_subprocess_exec`. The script is passed via stdin to the interpreter — NOT written to a temp file and exec'd — to avoid leaving extra artifacts on disk and to keep the executable-bit/PATH concerns simple:

```python
proc = await asyncio.create_subprocess_exec(
    interpreter_binary,   # /bin/bash, /bin/sh, /bin/zsh, /usr/bin/env python3
    "-",                  # read script from stdin (bash/sh/zsh; python3 reads stdin with "-")
    cwd=cwd_resolved,
    env=os.environ,       # daemon's env — see §6.3
    stdin=asyncio.subprocess.PIPE,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    start_new_session=True,   # so SIGKILL on timeout can target the whole process group
)
proc.stdin.write(script_text.encode("utf-8"))
proc.stdin.close()
```

`interpreter_binary` is resolved per interpreter via `shutil.which`:

| `interpreter` | `shutil.which` lookup |
|---|---|
| `bash`     | `bash` |
| `sh`       | `sh` |
| `zsh`      | `zsh` |
| `python3`  | `python3` |

If `which` returns `None` (interpreter not installed), the `POST /run` route rejects 422 `interpreter_unavailable` BEFORE transitioning state.

### 6.2 Output capture

Two long-running asyncio tasks per run: `_pump_stream(proc.stdout, "stdout", path)` and `_pump_stream(proc.stderr, "stderr", path)`. Each pump:

1. Reads line-by-line (`await stream.readline()`).
2. Appends to the on-disk file (`<runtime>/orgs/<slug>/scripts/SR-NNN.{out,err}`), opened in append+binary mode.
3. Publishes an SSE event on the per-SR pubsub channel (small in-memory `asyncio.Queue` per active SR, fanned out to any connected `/events` subscribers).
4. Appends to an in-memory head buffer capped at 64 KB; once full, further bytes are discarded from the buffer (NOT from disk).

When both pumps return (EOF) AND `proc.wait()` returns, transition to terminal state:

1. Compute `duration_ms = finished_at - started_at`.
2. Persist `stdout_head` / `stderr_head` from the in-memory buffer; append `\n[truncated: N more bytes — see {stdout_path}]` if the buffer was capped.
3. Set `exit_code`, `finished_at`, `stdout_path`, `stderr_path`.
4. Status = `completed` if the subprocess exited naturally (any exit code, including non-zero); `failed` only if the daemon killed it (timeout, see §6.4) or `subprocess.Popen` raised (spawn_failed).
5. Audit `script_run_completed` or `script_run_failed`.
6. Emit terminal SSE event and close the pubsub channel.

### 6.3 Environment

The subprocess inherits the daemon's `os.environ` verbatim. Rationale: the daemon is launched from the founder's shell (`scripts/daemon.sh start`), so `os.environ` matches the founder's interactive env at daemon-launch time. No env filtering, no env injection, no per-org env namespace in v1.

**Operational note** (founder-facing, documented in the README section we'll add): if the founder rotates credentials in their interactive shell, the daemon won't see them until restarted. This is a known v1 constraint.

### 6.4 Timeout

`asyncio.wait_for(proc.wait(), timeout=timeout_seconds)` wraps the wait. On `asyncio.TimeoutError`:

1. `os.killpg(proc.pid, signal.SIGTERM)`; wait 5 seconds for graceful exit.
2. If still alive, `os.killpg(proc.pid, signal.SIGKILL)`.
3. Drain pumps for ≤ 2 seconds, then close.
4. Transition `running → failed` with `reason=timeout`, `exit_code=-signal.SIGTERM` (or `-SIGKILL` if escalated).
5. Audit `script_run_failed` with `reason=timeout`.

`start_new_session=True` ensures `killpg` reaches the script's full process tree, not just the interpreter.

### 6.5 Concurrency

Multiple SRs can run concurrently across the org — there's no global script lock. A second `POST /run` on the same SR returns 409 (status check in §5.4 prevents it). The daemon does NOT impose a per-org concurrent-script cap in v1; if abuse surfaces, add a setting later.

### 6.6 Daemon shutdown

On clean daemon shutdown (FastAPI lifespan exit), in-flight script subprocesses are sent `SIGTERM` (5s grace), then `SIGKILL`. Their SRs are transitioned `running → failed` with `reason=killed` and audited. The on-disk `.out`/`.err` files are flushed and closed; partial output is preserved. On unclean shutdown (OS kill), recovery on next daemon startup scans for `status='running'` SRs and transitions them to `failed` with `reason=killed_daemon_restart`.

## 7. Founder CLI

All under `grassland scripts ...`. Talks to the daemon over HTTP using the existing bearer-token client.

### 7.1 `grassland scripts list [--status pending|all|...] [--agent X] [--task TASK-NNN] [--limit N]`

Default `--status pending`. Output (tabular):

```
ID       AGENT             TASK        STATUS    AGE      TITLE
SR-019   engineering_head  TASK-091    pending   3m       Close PR #247 with approval comment
SR-018   payment_agt       TASK-088    running   12s      Stripe refund for order #44912
SR-017   qa_engineer       TASK-085    completed 1h       Bulk update test fixtures
```

### 7.2 `grassland scripts show SR-NNN`

Prints full record:

```
SR-019   pending   submitted 2026-05-23T10:14:02Z
Agent:        engineering_head
Task:         TASK-091
Interpreter:  bash
Cwd hint:     repos/web-app

Title:        Close PR #247 with approval comment

Rationale:
  PR review is complete. My allow_rules cover `gh pr comment` but not `gh pr close`.
  Need founder to merge-close so the auth-rewrite branch can be deleted.

Script:
  set -euo pipefail
  gh pr close 247 --comment 'Approved and closed per review thread THR-014.'

Founder actions:
  grassland scripts run SR-019 [--cwd PATH] [--timeout-seconds N]
  grassland scripts reject SR-019 --reason "..."
```

For terminal SRs, append the captured output (head of stdout/stderr, with a footer pointing at `grassland scripts output SR-019` for the full read).

### 7.3 `grassland scripts run SR-NNN [--cwd PATH] [--timeout-seconds N]`

TTY-gated confirmation (no `--yes` bypass — script execution is one of the most dangerous operations in the system, and we want a beat of human attention every time):

```
About to execute SR-019:
  Agent:       engineering_head
  Task:        TASK-091
  Interpreter: bash
  Cwd:         /Users/founder/grassland-runtime/orgs/hk-tour/workspaces/engineering_head/repos/web-app
  Timeout:     300s

Script:
  set -euo pipefail
  gh pr close 247 --comment 'Approved and closed per review thread THR-014.'

Proceed? [y/N]: y

[stdout] ✓ Closed pull request #247
[done]   exit=0 duration=1.4s
```

Output is streamed live via the SSE endpoint. The terminal event determines the exit status of the CLI command itself: `0` if subprocess `completed` with exit 0; `1` if `completed` with non-zero exit (the script ran but failed); `2` if `failed` (daemon-side failure — timeout, spawn error).

If stdin is not a TTY, the command fails with `error: scripts run requires a TTY (interactive confirmation). Use the web UI to run non-interactively.` — this is intentional friction; non-TTY automation paths should go through web instead.

### 7.4 `grassland scripts reject SR-NNN [--reason TEXT]`

If `--reason` is omitted, prompt for it (multi-line, end with `.` on its own line, mirrors existing CLI prompt patterns). Empty reason rejected at CLI before the HTTP call.

### 7.5 `grassland scripts output SR-NNN [--stream stdout|stderr|both] [--max-bytes N]`

Plain text dump to stdout. For `--stream both` (default), prints `--- stdout ---\n<...>\n--- stderr ---\n<...>`. Hits `GET /output` under the hood.

### 7.6 Help integration

`grassland --help` and `grassland scripts --help` updated. `skills/grassland/SKILL.md` (the founder-facing skill at `~/.claude/skills/grassland`) gains a "Script requests" section mirroring the existing "Threads", "Talks", "KB" sections.

## 8. Web UI

New feature folder `web/src/features/scripts/` mirroring the threads architecture. New API mirror `web/src/lib/api/scripts.ts` exposing one function per non-agent-only route (`list`, `get`, `run`, `reject`, `output`, plus an SSE helper for `events`).

### 8.1 Routes

- `/scripts` — list page
- `/scripts/:id` — detail (rendered as a drawer over the list, matches `fab0e77` tasks pattern)

### 8.2 List page

- Header: title "Script Requests", agent filter dropdown, status filter chips (`pending` | `running` | `completed` | `failed` | `rejected` | `all`), refresh button.
- Default filter: `pending`. The `pending` chip carries a count badge (live via existing SSE event bus pattern from threads — extended with `script_submitted` / `script_*` events).
- Card layout per row (no table; matches the post-fab0e77 scannable-card discipline):
  - Top line: SR-NNN · agent · age · status pill
  - Middle: bold title
  - Bottom: collapsed first line of rationale, two-line ellipsis
- Click → opens detail drawer.

### 8.3 Detail drawer

Scrollable per the `fab0e77` discipline. Sections top-to-bottom:

1. **Header** — SR-NNN, status pill, agent, task link (deep-links into `/tasks/:id` drawer), submitted-at.
2. **Title + rationale** — rationale rendered as markdown.
3. **Script** — fenced code block, monospace, no syntax highlighting in v1 (we have no syntax-highlighter dep; v2 can add Shiki/Prism). Read-only.
4. **Interpreter / cwd hint** — small key-value strip.
5. **Action bar** (pending status only):
   - "Run" button → opens a confirm modal showing the resolved cwd, timeout, and full script; modal contains optional "cwd override" and "timeout (seconds)" form fields, both pre-filled with the resolved defaults; final "Run now" button POSTs to `/run` with `cwd_override` and `timeout_seconds` populated from the form.
   - "Reject" button → opens a reason modal (textarea, required, ≤ 1000 chars); POST to `/reject`.
6. **Output panel** (running / terminal statuses only) — live SSE during `running` (autoscroll, with a "pause autoscroll" toggle); static after. Renders stdout and stderr in two tabs, with "Full output" button that hits `/output` for the un-truncated dump (opens in a new tab as plain text).
7. **Reject reason** (rejected status only) — readonly.

### 8.4 Cross-links

- The agent-detail page (`/agents/:name`) gains a "Recent script requests" section listing the last 10 SRs for that agent.
- The task-detail drawer (`/tasks/:id`) gains a "Script requests from this task" sub-section listing any SRs with `task_id == this`.
- The `script_submitted` audit event (rendered in `/audit`) deep-links into the SR drawer.

### 8.5 Auth + contract

Same model as threads: bearer fetched once from `/auth/bootstrap`, attached to HTTP and SSE. `tests/contract/test_openapi_snapshot.py` regenerates after the route additions; `web/src/test/openapi-coverage.test.ts` adds `/scripts/...` paths under INCLUDED, with `/scripts/submit` under EXCLUDED (reason: `"agent-only callback (matches /report-completion pattern)"`).

## 9. Orchestrator integration

### 9.1 Revisit-header extension

`_revisit_header_if_applicable(orch, task_id)` in `src/orchestrator/run_step.py` already prepends a 5-6 line context header pointing at `grassland details` / `grassland audit` / `grassland recall` for the frozen predecessor.

Extension: if the predecessor task's audit log contains any `script_submitted` events, append:

```
This task previously submitted script requests:
  - SR-019 (completed) — Close PR #247 with approval comment
  - SR-020 (rejected) — Stripe refund for order #44912

Read the outputs / rejection reasons before continuing:
  grassland scripts show SR-019
  grassland scripts output SR-019
```

Only completed/failed/rejected SRs are listed (pending/running shouldn't exist on a frozen predecessor in practice — if they do, surface them in the header too with a `[still pending — founder action needed]` marker).

### 9.2 No new task-status state

Agents self-block as `status=blocked, block_kind=escalated` referencing the SR-NNN in their summary. The orchestrator does NOT learn about SRs as a first-class block reason in v1 — the manager-driven decision loop already handles "this agent is blocked, escalate to founder." Adding a new `block_kind=awaiting_script` would let the dashboard distinguish script-blocks from other escalations; that's a v2 polish, not a v1 requirement.

### 9.3 No execution under `run_step`

Script execution happens entirely inside the daemon HTTP layer (route handler + asyncio tasks), independent of the orchestrator. The orchestrator does not see SRs at all — it only sees the task block, then later the revisit. This keeps the script subsystem isolated from the orchestration state machine.

## 10. Auth boundaries

| Caller | Routes |
|---|---|
| Agent bearer | `POST /scripts/submit` only |
| Founder bearer (CLI + Web) | All other `/scripts/*` routes |

Bearer-to-role mapping uses the existing token-scoping logic in `src/daemon/routes/_org_dep.py` (the same mechanism that gates `report-completion` to agents and `manage-agent` approval to founder). Any attempt by an agent token to call a founder-only route returns 403 `agent_not_authorized`. Any attempt by founder to call `POST /submit` returns 403 `founder_cannot_submit_as_agent` (founders run scripts directly, they don't submit them).

## 11. Failure modes

| Scenario | Behavior |
|---|---|
| Agent submits but never self-blocks | SR stays `pending`; founder sees it in `grassland scripts list` and can run/reject independently of any task state. The agent's task proceeds as if the script weren't needed (which is wrong — but that's an agent-discipline issue, not a system bug). |
| Founder runs SR but the agent's task already completed | SR completes normally. Output is durable in DB + on disk. Founder can revisit the (now-completed) task to feed the output back into a new session if needed. |
| Founder rejects SR, agent never sees the reject | Same as above — agent discipline expects self-block + revisit; if the agent didn't self-block, the reject is just an audit row the founder reads. Spec-level fix: the `start-task` skill cross-reference will say "always self-block after `scripts submit`." |
| Daemon crashes mid-run | On restart, the recovery scan in §6.6 transitions in-flight SRs to `failed` with `reason=killed_daemon_restart`. Partial output on disk is preserved. |
| Script writes huge output (GB-scale) | On-disk files grow unbounded in v1 — no size cap on `.out` / `.err`. DB head columns are bounded at 64 KB. If this becomes a problem operationally, add a per-SR output cap in v2 (kill with `reason=output_cap_exceeded`). |
| Network partition during SSE | Subscriber reconnects; gets a fresh stream from "now" (no replay). Final state always retrievable via `/output`. |
| Script depends on the agent's repo state but the founder runs it from a different cwd | Founder's responsibility — `--cwd` and `cwd_hint` are advisory. The frozen `.script` file lets a forensic re-run later. |
| Founder accidentally runs a destructive script | TTY confirmation in CLI; explicit confirm modal in web. No `--yes` bypass. v1 mitigation. The full audit trail (text + cwd + exit + output) makes post-hoc analysis straightforward. |

## 12. Testing

### 12.1 Unit (per `src/...` module touched)

- `src/infrastructure/database.py` — SR allocation (`next_script_request_id`), per-status transitions, recovery scan on startup.
- `src/daemon/routes/scripts.py` — every validation gate in §5.1 (one test per error code), happy-path `/run` with a fake interpreter, reject path, output path with truncation.
- Subprocess execution helper — fake script (`echo hi; sleep 0.1; echo bye`), timeout-killed path (`sleep 60` with `timeout_seconds=1`), spawn-failure path (interpreter not on PATH), non-zero exit path.

### 12.2 Integration (`-m integration`)

End-to-end test: `fake_claude.sh` extended with a `--script-submit` plan branch that fakes an agent submitting an SR and self-blocking. The test then drives the founder side via the HTTP API to run the SR with a fake script (e.g., `bash` script that touches a file in the workspace), asserts the file appears, asserts the audit chain, then revisits the task and asserts the revisit-header carries the SR pointer.

Both `fake_claude_plan_env` and the new `--script-submit` plan must coexist with the existing thread plan branch — same pattern as the dual-plan integration tests already in the suite.

### 12.3 Contract

- `tests/contract/test_openapi_snapshot.py` — regenerate after route additions.
- `web/src/test/openapi-coverage.test.ts` — add `/scripts/*` paths; `/scripts/submit` to EXCLUDED with reason.

### 12.4 Web (vitest)

- `web/src/lib/api/scripts.test.ts` — one test per exported function (list, get, run, reject, output), happy-path + 4xx error.
- Feature-level rendering tests for the list and detail drawer (mocked API).

## 13. Migration

None. New table on existing DBs (idempotent `CREATE TABLE IF NOT EXISTS`). No data backfill. No agent-side action required — existing agents simply gain access to a new callback they choose whether to use.

## 14. Rollout order

1. Schema + `ScriptRequestRecord` model + DB methods (allocation, CRUD, recovery scan).
2. `POST /submit` route + bearer auth gate.
3. Subprocess execution helper (`src/daemon/scripts_runner.py` — new module).
4. `POST /run`, `POST /reject`, `GET /`, `GET /{id}`, `GET /{id}/output`, `GET /{id}/events`.
5. `grassland scripts submit|list|show|run|reject|output` CLI commands.
6. `protocol/skills/scripts/SKILL.md` + cross-references from `start-task` and adapter docs.
7. Revisit-header extension in `_revisit_header_if_applicable`.
8. Web `lib/api/scripts.ts` + `features/scripts/` (list, detail, run modal, reject modal, SSE output).
9. Audit-page deep-link, agent-page recent-SRs section, task-drawer SRs-from-task section.
10. Integration test with the dual-plan `fake_claude.sh` extension.
11. README + `skills/grassland/SKILL.md` updates.

Each step is independently merge-safe — the partial state (e.g., daemon route exists but no CLI yet) doesn't break anything because no agent will start using `scripts submit` until the skill cross-reference is in.
