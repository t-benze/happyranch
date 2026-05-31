# Jobs — Design Spec

**Date:** 2026-05-26
**Status:** Draft, pending implementation plan.
**Supersedes:**
- `docs/superpowers/specs/2026-05-23-agent-script-requests-design.md` — the "Script Requests" v1 spec; this design renames the module to `jobs` and extends it.
- `docs/superpowers/specs/2026-05-25-feishu-script-request-notifications-design.md` — Feishu integration for the SR path; the notification kind is renamed and the rest carries over verbatim.

**Relates to:**
- `docs/superpowers/specs/2026-04-21-opc-revisit-design.md` — the unblock path after a founder-reviewed job runs (`happyranch revisit <task-id>` surfaces job outputs in its context header).
- `docs/superpowers/specs/2026-05-14-web-ui-design.md` — three-layer web architecture (`lib/api → features/<domain> → components`) and OpenAPI snapshot contract.
- `docs/superpowers/specs/2026-05-13-threads-design.md` — agent-initiated → founder-review pattern reused.

## 1. Goal

Today's `scripts` module exists to solve one problem — an agent hits a permission wall, founder runs the command, agent unblocks via revisit. It can't address a second problem that turns out to be just as common: **agents block on shell calls that don't return** (dev servers, watchers, polling loops, long builds). The current bash tool synchronously blocks until the command exits or the session times out — at which point the orchestrator auto-revisits and the agent has made no progress.

Both problems share the same underlying machinery: spawn a subprocess inside the daemon, capture stdout/stderr to disk with a DB head cap, stream events over SSE, expose detail/list/stop over HTTP, audit transitions, kill cleanly on daemon shutdown. The only differences are **policy**: who gates the start, and whether the subprocess has a bounded timeout.

This design unifies both flows under a single noun (`jobs`) and lets the agent declare its desired policy via two booleans on the submission form. The daemon executes accordingly. No mode enum, no daemon-side classification, no `allow_rules` introspection — the form *is* the differentiation.

Use cases:

- Engineering worker needs to run `gh pr close 247` — submits with `review_required=true, persistent=false`. Founder reviews, runs, agent revisits.
- Engineering worker needs `npm run dev` to verify a UI change — submits with `review_required=false, persistent=true`. Daemon auto-runs; agent tails output via `happyranch jobs tail`.
- Support worker spawns a 15-minute backup script — submits with `review_required=false, persistent=false`. Daemon auto-runs with default timeout; agent checks result later via `happyranch jobs show` / `wait`.
- Founder wants an agent-monitored process under explicit oversight — agent submits with `review_required=true, persistent=true`. Founder reviews, then triggers a persistent run.

## 2. Non-goals

Out of scope for v1:

- Multi-reviewer approval. Founder is the sole reviewer.
- Founder edits the script before running. If wrong, founder rejects with reason; agent re-submits.
- Scheduled / cron jobs. v1 is on-demand only.
- Per-job secrets injection. Daemon uses its own environment.
- Auto-unblock on completion. The agent self-blocks (when it chose to); founder uses `happyranch revisit` to unblock. No "task wakes itself" channel.
- Agent-readable output during a `review_required=true` flow before the founder runs it.
- Job dependency graphs (job B starts when A exits).
- Restart-on-failure policies.
- Resource limits (CPU/memory/network).
- Cross-task persistence — a job dies when its owning task transitions terminal.
- Daemon-side validation of the script against the agent's `allow_rules`. The form is honor-system; misclassification is visible in audit and corrected via the existing talk + learning loop.

In scope but minimal:

- Interpreter set: `bash`, `sh`, `zsh`, `python3`. Same as the current SR module.
- Output truncation: ~64 KB head per stream in DB; full streams written to disk.
- Output-size ceiling: `max_output_bytes` per stream (default 50 MB), kill with `kill_reason=output_cap` when exceeded.

## 3. Module rename — `scripts` → `jobs`

This spec ships as one cutover: the existing `scripts` module is renamed wholesale and the two new boolean fields are added in the same migration. Two-step (rename now, extend later) is rejected — the names would diverge from the semantics for the duration of the gap.

| Layer | Before | After |
|--|--|--|
| ID prefix | `SR-NNN` | `JOB-NNN` |
| DB table | `script_requests` | `jobs` |
| Disk dir | `<runtime>/orgs/<slug>/scripts/` | `<runtime>/orgs/<slug>/jobs/` |
| Files | `SR-NNN.{out,err,script}` | `JOB-NNN.{out,err,script}` |
| Routes | `/api/v1/orgs/{slug}/scripts/...` | `/api/v1/orgs/{slug}/jobs/...` |
| CLI noun | `happyranch scripts ...` | `happyranch jobs ...` |
| Skill dir | `protocol/skills/scripts/` | `protocol/skills/jobs/` |
| Audit kinds | `script_submitted`, `script_run`, `script_completed`, `script_rejected`, `script_failed` | `job_submitted`, `job_run`, `job_completed`, `job_rejected`, `job_failed` (plus new: `job_auto_started`, `job_killed`, `job_stopped`) |
| Runtime module | `src/daemon/scripts_runner.py` | `src/daemon/jobs_runner.py` |
| Routes module | `src/daemon/routes/scripts.py` | `src/daemon/routes/jobs.py` |
| TS API client | `web/src/lib/api/scripts.ts` | `web/src/lib/api/jobs.ts` |
| Web feature | `web/src/features/scripts/` | `web/src/features/jobs/` |
| Feishu notification kind | `script_request` (in `escalation_notifications.kind`) | `job_request` |

### 3.1 Backwards-compat shim

A `happyranch scripts ...` CLI namespace remains for one release. It prints a single-line deprecation warning to stderr and dispatches to the corresponding `jobs` handler:

```
$ happyranch scripts submit --from-file payload.json
[deprecated] `happyranch scripts` is renamed to `happyranch jobs` — alias removed in next release.
ok: submitted JOB-019 ...
```

No HTTP route aliases (the OpenAPI snapshot would reject duplicates). No Python module aliases. Removed in the next release.

## 4. The submission form

The agent's submission payload is a JSON object with these fields. **Everything the framework needs to know about how to handle the job is in this form.**

```json
{
  "task_id": "TASK-091",
  "session_id": "<active session_id>",
  "title": "Run dev server for browser testing",
  "script": "npm run dev\n",
  "interpreter": "bash",
  "cwd_hint": "repos/web-app",
  "rationale": "Need live server to verify component renders.",
  "review_required": false,
  "persistent": true,
  "max_runtime_seconds": null,
  "max_output_bytes": 52428800
}
```

### 4.1 Required fields

- `task_id` — must match an active task whose `assigned_agent` equals the submitting session's agent.
- `session_id` — must match the agent's currently-active session for `task_id`. Stale sessions get `409 session_mismatch`.
- `title` — ≤ 200 chars, founder-facing one-line summary.
- `script` — full source text. ≤ 64 KB hard cap enforced in the route.
- `interpreter` — one of `bash`, `sh`, `zsh`, `python3`. Other values → `400 unknown_interpreter`.

### 4.2 Policy flags (the two booleans)

- **`review_required`** (default `false`) — when `true`, the job is enqueued in `status=pending` and the founder must explicitly run or reject it. When `false`, the daemon auto-runs immediately on submission.
- **`persistent`** (default `false`) — when `false`, the job has a bounded runtime (default 300 s; agent can override via `max_runtime_seconds`). When `true`, the job has no daemon-side runtime ceiling (unless agent sets one); it exits naturally, is killed on task terminal transition, or is stopped explicitly via `POST /stop`.

The four cells of (review × persistent) are all valid; nothing is special-cased in code beyond the two boolean checks at the right call sites.

### 4.3 Optional fields

- `cwd_hint` — relative path under the agent's workspace; absent = workspace root. Same semantics as the current SR module.
- `rationale` — required when `review_required=true` (founder needs context to approve). Optional otherwise; recorded in audit even when blank.
- `max_runtime_seconds` — agent-imposed runtime ceiling. When `null` and `persistent=false`, defaults to 300 s. When `null` and `persistent=true`, no ceiling. When positive, enforced regardless of `persistent`.
- `max_output_bytes` — per-stream cap. Default 50 MB. On exceed, daemon kills the subprocess with `kill_reason=output_cap` and transitions the row to `failed`.

### 4.4 Honor system

The agent decides which classification to declare. The daemon does not introspect the script against `allow_rules`. The judgment is captured in the skill (§9), and the audit log surfaces every submission for retrospective founder review. Misclassification — an agent runs a sensitive command via `review_required=false` — is recoverable: founder stops the job, audit-logs the correction, opens a talk + learning entry to update the agent's behavior.

This is a deliberate choice: the cost of daemon-side validation (parsing compound scripts, tracking allow_rules, handling shell builtins, edge cases around quoting) exceeds the cost of trusting the agent within a system that already audits everything else.

## 5. Lifecycle and state machine

```
                    ┌─ review_required=false ─→ running (auto-spawn)
   submit ──────────┤
                    └─ review_required=true ──→ pending
                                                  │
                                          founder /reject → rejected (terminal)
                                          founder /run    → running

   running ──→ subprocess exits zero            → completed (terminal)
            ├─ subprocess exits non-zero        → failed (terminal)
            ├─ subprocess killed by timeout     → failed   (reason: timeout)
            ├─ subprocess killed by output cap  → failed   (reason: output_cap)
            ├─ subprocess killed by /stop       → failed   (reason: founder_stop | agent_stop)
            ├─ task transitions terminal        → failed   (reason: task_ended)  ← persistent jobs
            └─ daemon shutdown                  → failed   (reason: daemon_shutdown)
```

Terminal states: `rejected`, `completed`, `failed`. Once terminal, the row is frozen.

`reason` (a new column, see §6) is null for natural exits (`completed`, `failed` via non-zero exit), populated for forcible kills. The existing SR module uses `reason` for the `timeout` case only; this design adds `output_cap`, `founder_stop`, `agent_stop`, `task_ended`, `daemon_shutdown`, `daemon_crash`.

### 5.1 Task-terminal kill (persistent jobs)

When a task transitions to a terminal state (`completed` / `failed` / `failed-cancelled`), the daemon enumerates all `running` jobs with `task_id == <terminating task>` and `persistent=true`, sends SIGTERM to each (5 s grace), then SIGKILL. Each gets `kill_reason=task_ended` and an audit entry.

Non-persistent jobs (`persistent=false`) are bound by their `max_runtime_seconds` ceiling and typically have already terminated by the time the task ends; if one is still running when the task terminates, it's killed by the same task-terminal hook (also with `kill_reason=task_ended`). The hook is uniform across both kinds.

### 5.2 Daemon shutdown

Same shape as the current SR shutdown path (`terminate_all_inflight`):

1. Snapshot in-flight subprocess registry, SIGTERM each.
2. 5 s grace, then SIGKILL any still alive.
3. Await runner background tasks (with timeout) so they persist terminal state before per-org DBs close.

### 5.3 Startup recovery

For each org on daemon startup, scan for `status=running` rows (left over from a crash) and force-fail them with `kill_reason=daemon_crash`. Mirrors `recover_orphaned_running_scripts`.

## 6. Data model

### 6.1 Table — `jobs`

New table per-org DB, replaces `script_requests`. Idempotent `CREATE TABLE IF NOT EXISTS` on daemon startup:

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id                  TEXT PRIMARY KEY,                  -- "JOB-NNN", monotonically allocated per-org
    task_id             TEXT NOT NULL,                     -- FK to submitting agent's active task
    agent_name          TEXT NOT NULL,                     -- composer (= tasks.assigned_agent at submit time)
    title               TEXT NOT NULL,                     -- ≤ 200 chars
    rationale           TEXT,                              -- required when review_required=true; nullable otherwise
    script_text         TEXT NOT NULL,                     -- full source; route enforces 64 KB cap
    interpreter         TEXT NOT NULL,                     -- {bash, sh, zsh, python3}
    cwd_hint            TEXT,                              -- relative to workspace; NULL = root

    -- Policy flags (the two booleans driving the four cells)
    review_required     INTEGER NOT NULL DEFAULT 0,        -- bool 0/1
    persistent          INTEGER NOT NULL DEFAULT 0,        -- bool 0/1

    -- Limits
    max_runtime_seconds INTEGER,                           -- NULL = no agent-imposed ceiling
    max_output_bytes    INTEGER NOT NULL DEFAULT 52428800, -- 50 MB

    -- Status
    status              TEXT NOT NULL DEFAULT 'pending',   -- {pending, rejected, running, completed, failed}
    exit_code           INTEGER,
    reason              TEXT,                              -- {timeout, output_cap, founder_stop, agent_stop, task_ended, daemon_shutdown, daemon_crash} or NULL
    duration_ms         INTEGER,

    -- Output
    stdout_head         TEXT,
    stderr_head         TEXT,
    stdout_path         TEXT,
    stderr_path         TEXT,
    stdout_bytes        INTEGER,
    stderr_bytes        INTEGER,

    -- Resolved at run-time
    cwd_resolved        TEXT,                              -- absolute path used by subprocess

    -- Timestamps
    started_at          TEXT,                              -- set when status enters 'running'
    finished_at         TEXT,                              -- set when status enters terminal
    reviewed_at         TEXT,                              -- set when founder reviews (rejected or running)
    reviewed_by         TEXT,                              -- "founder" (only reviewer in v1)
    reject_reason       TEXT,                              -- required when status='rejected'
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS jobs_task_id_idx ON jobs(task_id);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
```

Compared to the current `script_requests` table:

- Renamed.
- New columns: `review_required`, `persistent`, `max_output_bytes`, `stdout_bytes`, `stderr_bytes`, `reason`.
- `rationale` is now nullable (was NOT NULL); the route enforces non-empty when `review_required=true`.
- `timeout_seconds` removed; replaced by `max_runtime_seconds` (nullable, semantics adjusted per §4.3).

### 6.2 Migration

A one-shot per-org migration runs in the daemon startup `_migrate_schema` path the first time it sees a `script_requests` table without a `jobs` table:

```sql
BEGIN;
-- Rename table
ALTER TABLE script_requests RENAME TO jobs;

-- Add new columns. DEFAULT 0 matches the §6.1 fresh-install schema for future INSERTs;
-- the UPDATE below backfills legacy rows to the values that describe their semantics.
ALTER TABLE jobs ADD COLUMN review_required INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN persistent INTEGER NOT NULL DEFAULT 0;
ALTER TABLE jobs ADD COLUMN max_output_bytes INTEGER NOT NULL DEFAULT 52428800;
ALTER TABLE jobs ADD COLUMN stdout_bytes INTEGER;
ALTER TABLE jobs ADD COLUMN stderr_bytes INTEGER;
ALTER TABLE jobs ADD COLUMN reason TEXT;

-- Backfill legacy rows: every pre-existing row was a "script request" with founder review,
-- and was always one-shot (the only kind that existed). The DEFAULT 0 on the column
-- governs all FUTURE inserts.
UPDATE jobs SET review_required = 1 WHERE review_required = 0;
-- persistent stays 0 for legacy rows (correct — they were all one-shot).

-- Map legacy timeout_seconds → max_runtime_seconds
ALTER TABLE jobs ADD COLUMN max_runtime_seconds INTEGER;
UPDATE jobs SET max_runtime_seconds = timeout_seconds WHERE timeout_seconds IS NOT NULL;
ALTER TABLE jobs DROP COLUMN timeout_seconds;

-- Any rows in 'running' at migration time are orphaned by definition: the daemon
-- has exited (otherwise this migration wouldn't be running on startup). Force-fail
-- them so the rest of the rewrite has only terminal-state rows to touch. The
-- startup-recovery scan in §5.3 would do the same thing for 'jobs' rows but never
-- sees these legacy rows because they're rewritten to JOB-* below before §5.3 runs.
UPDATE jobs
   SET status = 'failed',
       reason = 'daemon_crash',
       finished_at = COALESCE(finished_at, started_at, created_at)
 WHERE status = 'running';

-- Rename IDs in the jobs table itself
UPDATE jobs SET id = 'JOB-' || SUBSTR(id, 4) WHERE id LIKE 'SR-%';

-- Rewrite stored output paths from .../scripts/SR-NNN.{out,err} to .../jobs/JOB-NNN.{out,err}.
-- The filesystem move below physically relocates the files; without this UPDATE, the row's
-- stdout_path/stderr_path columns still point at the pre-rename location and GET /{id},
-- /output, /tail all fail for migrated rows.
UPDATE jobs
   SET stdout_path = REPLACE(REPLACE(stdout_path, '/scripts/SR-', '/jobs/JOB-'), '/scripts/', '/jobs/')
 WHERE stdout_path IS NOT NULL;
UPDATE jobs
   SET stderr_path = REPLACE(REPLACE(stderr_path, '/scripts/SR-', '/jobs/JOB-'), '/scripts/', '/jobs/')
 WHERE stderr_path IS NOT NULL;

-- Ripple ID rename through cross-referencing tables
UPDATE escalation_notifications
   SET task_id = 'JOB-' || SUBSTR(task_id, 4)
 WHERE kind = 'script_request' AND task_id LIKE 'SR-%';

UPDATE escalation_notifications
   SET kind = 'job_request'
 WHERE kind = 'script_request';

-- Rewrite audit actions (the audit_log table's verb column is `action`, not `kind`)
UPDATE audit_log
   SET action = 'job_' || SUBSTR(action, 8)
 WHERE action LIKE 'script_%';

-- Rewrite audit payloads that reference SR-NNN ids
-- (`payload` is opaque JSON-text; do a string replace on the {"script_id":"SR-..."} pattern)
UPDATE audit_log
   SET payload = REPLACE(payload, '"script_id"', '"job_id"')
 WHERE payload LIKE '%"script_id"%';

UPDATE audit_log
   SET payload = REPLACE(payload, '"SR-', '"JOB-')
 WHERE payload LIKE '%"SR-%';

-- Rename indexes if any (script_requests_*)
DROP INDEX IF EXISTS script_requests_task_id_idx;
DROP INDEX IF EXISTS script_requests_status_idx;
CREATE INDEX IF NOT EXISTS jobs_task_id_idx ON jobs(task_id);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
COMMIT;
```

After the DB migration, a filesystem migration runs (idempotent, no transaction):

```bash
# Per org
mv <runtime>/orgs/<slug>/scripts <runtime>/orgs/<slug>/jobs
cd <runtime>/orgs/<slug>/jobs
for f in SR-*.out SR-*.err SR-*.script; do
    mv "$f" "JOB-${f#SR-}"
done
```

The migration is automatic — no `happyranch migrate-scripts-to-jobs` command is required because the rename is unambiguous and reversible at the schema level (we can write a backstop reverse-rename if needed, but expect not to). The migration handles `running` rows inline (force-failing them with `reason=daemon_crash` as shown above) rather than aborting and asking the founder to bounce the daemon, because the per-org startup-recovery scan in §5.3 only inspects the renamed `jobs` table — a legacy `script_requests` row in `running` would otherwise have no path to a terminal state and would block the upgrade indefinitely.

## 7. Routes

Per-org routes under `/api/v1/orgs/{slug}/jobs/`:

| Method + path | Auth | Purpose |
|--|--|--|
| `POST /submit` | session-binding (agent callback) | Submit a new job. Excluded from OpenAPI. |
| `GET /` | bearer (founder via web/CLI) | List, with filters: `?status=pending|running|completed|failed|rejected`, `?task=TASK-NNN`, `?review_required=true|false`, `?persistent=true|false` |
| `GET /{id}` | bearer OR session-binding (agent inspects own job) | Detail (full row + head text). Agent path returns 409 `session_mismatch` for someone else's job, same shape as `/stop` and `/tail`. |
| `POST /{id}/run` | bearer | Founder triggers run of a `pending` row. 409 if not pending. |
| `POST /{id}/reject` | bearer | Founder rejects a `pending` row with reason. 409 if not pending. |
| `POST /{id}/stop` | bearer OR session-binding (agent stops own job) | Kill running subprocess. 409 if not running. |
| `POST /{id}/wait?timeout_seconds=N` | bearer OR session-binding (own job) | Long-poll; returns when status terminal or timeout. |
| `GET /{id}/output?stream=stdout|stderr&offset=N&limit=N` | bearer | Byte window into the on-disk file. |
| `GET /{id}/tail?stream=stdout|stderr&lines=N` | bearer OR session-binding (own job) | Last N lines from the on-disk file. New for v1. |
| `GET /{id}/events` | bearer OR session-binding (own job) | SSE stream (line events + terminal). Same pattern as SR. |

**Session-binding** for `/submit`, `/stop`, `/wait`, `/tail`, `/events` (when invoked by the agent) follows the same validation chain as `POST /api/v1/orgs/{slug}/scripts/submit` today: `task_not_active` checked before `session_mismatch`, `agent_name` derived from `task.assigned_agent` rather than payload.

**OpenAPI** — `POST /submit` is in the EXCLUDED set (agent callback); everything else is in INCLUDED and mirrored in `web/src/lib/api/jobs.ts`.

## 8. CLI

The CLI noun is `jobs`. All verbs work for any row regardless of policy flags — the row's own `review_required` / `persistent` / `status` fields drive the behavior, not the CLI verb.

### 8.1 Founder-facing

```bash
happyranch jobs list [--status pending|running|completed|failed|rejected]
                   [--review-required true|false]
                   [--persistent true|false]
                   [--task TASK-NNN]
happyranch jobs show <id>
happyranch jobs run <id> [--max-runtime-seconds N]   # pending → running
happyranch jobs reject <id> --reason "<text>"        # pending → rejected
happyranch jobs stop <id>                            # running → failed (founder_stop)
happyranch jobs output <id> [--stream stdout|stderr]
happyranch jobs tail <id> [--stream stdout|stderr] [--lines N]
happyranch jobs wait <id> [--timeout-seconds N]
```

### 8.2 Agent-facing

All agent-side invocations follow the established `--from-file` discipline so the Claude permission matcher sees one line:

```bash
happyranch jobs submit --from-file /tmp/job-<rand>.json [--org <slug>]
happyranch jobs tail   <id> [--stream stdout|stderr] [--lines N]
happyranch jobs wait   <id> [--timeout-seconds N]
happyranch jobs stop   <id>
happyranch jobs show   <id>
```

Agent-facing commands fail with `409 session_mismatch` if invoked against a job the agent doesn't own (i.e., `agent_name` mismatch) or after the agent's session has been superseded.

### 8.3 `happyranch scripts ...` shim

For one release, `happyranch scripts <verb>` prints `[deprecated] happyranch scripts is renamed to happyranch jobs — alias removed in next release.` to stderr and dispatches to the corresponding `jobs` handler. Removed in the next release.

## 9. Skill

A single skill at `protocol/skills/jobs/SKILL.md`. The previous `scripts/SKILL.md` is deleted (no `protocol/skills/scripts/` directory survives the rename). Workspaces regenerate their skill tree on next session start; in-flight sessions hold the old skill in their workspace, which still works against the new CLI via the deprecation shim for one release.

### 9.1 Full SKILL.md draft

````markdown
---
name: jobs
description: Run a script in the background or request founder review; manage the result.
---

# jobs

You want to run a script that either takes longer than your session can wait for, doesn't return at all (a dev server, a watcher), or needs permissions you don't have. Submit a job, fill in the form, and the framework handles the rest.

## When to use

Three signals you should reach for jobs instead of running the command inline:

1. **The command doesn't return.** Dev servers, log watchers, polling loops, things you want running while you do other work.
2. **The command takes too long.** A build, a backup, a long migration that would consume the rest of your session.
3. **The command needs permissions you don't have.** A `gh`, `aws`, `stripe`, `ssh`, or `sudo` invocation your `allow_rules` block — submit with `review_required=true` and the founder will run it for you.

Do NOT use jobs for one-shot, fast, in-sandbox commands. Run those inline — `bash` is still the right tool. Jobs add audit overhead and only pay off for the three signals above.

## The form

You fill in a JSON payload with these fields:

```json
{
  "task_id": "TASK-091",
  "session_id": "<your active session_id>",
  "title": "Run dev server for browser testing",
  "script": "npm run dev\n",
  "interpreter": "bash",
  "cwd_hint": "repos/web-app",
  "rationale": "Need live server to verify component renders.",
  "review_required": false,
  "persistent": true,
  "max_runtime_seconds": null
}
```

**Required:** `task_id`, `session_id`, `title`, `script`, `interpreter`.
**Allowed `interpreter`:** `bash`, `sh`, `zsh`, `python3`.
**Optional:** `cwd_hint`, `rationale`, `max_runtime_seconds`, `max_output_bytes`.

### The two policy flags

**`review_required`** — set to `true` when:

- Your script uses credentials your agent doesn't have (`aws`, `stripe`, `ssh`, `sudo`).
- The leading binary of any line isn't in your `allow_rules`.
- The script mutates external state in ways you couldn't roll back (`gh pr close`, `git push --force`, anything destructive).
- You're uncertain about whether the founder would want to review it. **When in doubt, request review.**

`rationale` is required when `review_required=true` — the founder needs context to approve.

**`persistent`** — set to `true` when:

- Your script doesn't return on its own (`npm run dev`, `tail -f`, polling loops).
- You expect to check on it across multiple bash calls in this session.

If `persistent=false`, the job has a default 300-second timeout. Override with `max_runtime_seconds` if you need longer.

If `persistent=true`, the job has no timeout by default. It runs until you stop it, the task transitions terminal, or the daemon shuts down. You can still set `max_runtime_seconds` as a safety cap.

The two flags are independent. All four combinations are valid:

| review_required | persistent | meaning |
|--|--|--|
| false | false | Auto-run, one-shot — fire and forget a backup, then check `wait`. |
| false | true | Auto-run, long-running — dev server, watcher. |
| true | false | Founder reviews and runs a bounded command — the old "script request" flow. |
| true | true | Founder reviews and runs a long-running process — rare; use when oversight matters more than speed. |

## How to submit

1. Write the payload to `/tmp/job-<random>.json` with the Write tool.
2. Submit as a single line (`--from-file` is mandatory; multi-line bash is rejected by the permission matcher):

   ```bash
   happyranch jobs submit --from-file /tmp/job-<random>.json --org <slug>
   ```

3. Output is `ok: submitted JOB-NNN ...`. Keep the JOB-NNN id.

## After submitting

**If `review_required=true`:** the job is `pending`. You can't proceed until the founder reviews. Self-block your task with `report-completion status=blocked` referencing the JOB-NNN. The founder will run it and use `happyranch revisit <task-id>` to bring you back with the output available via the revisit header.

**If `review_required=false`:** the job is `running`. Continue your work. Check on the job with:

- `happyranch jobs tail JOB-NNN` — see recent output.
- `happyranch jobs wait JOB-NNN --timeout-seconds 30` — block until terminal or timeout.
- `happyranch jobs show JOB-NNN` — full status snapshot.
- `happyranch jobs stop JOB-NNN` — kill it (useful if you're done with the dev server).

## Cleanup

Before reporting your task complete, stop any of your own jobs you no longer need. Persistent jobs you forget about will be auto-killed when your task transitions terminal, but explicit cleanup makes the audit log cleaner and avoids ambiguous "did the agent forget about this?" questions.

## Error handling

- `422 empty_<field>` — required field missing or whitespace-only. Check and resubmit.
- `400 unknown_interpreter` — `interpreter` not in the allowed set.
- `400 rationale_required` — submitted `review_required=true` without a `rationale`.
- `400 script_too_large` — script body exceeded 64 KB.
- `404 not_found` — `task_id` or `session_id` doesn't exist.
- `409 session_mismatch` — daemon spawned a newer session for this `(task_id, agent)`. Exit immediately.

Retry once after 1 second on any non-listed error.
````

(The skill file is committed as part of this spec's implementation; this draft is the source of truth.)

## 10. Runtime — `src/daemon/jobs_runner.py`

Rename of `scripts_runner.py` with three behavioral changes:

1. **Optional timeout.** The `run_script(..., timeout_seconds: int | None)` becomes `run_job(..., max_runtime_seconds: int | None)`. When `None`, no `asyncio.wait_for` wrapper — `await proc.wait()` runs to natural completion. Otherwise unchanged.
2. **Output-size cap.** The `_pump_stream` helper gains a `max_bytes: int | None` parameter. When `byte_counter[0] >= max_bytes`, the pump triggers a kill signal back to the parent (via an `asyncio.Event` shared with the runner), and the runner SIGTERM-then-SIGKILL the subprocess with `kill_reason=output_cap`. The pump continues draining whatever's already buffered, then exits.
3. **Task-terminal kill API.** A new module-level function `terminate_jobs_for_task(task_id: str)` returns the in-flight JOB-NNN ids matching that task, sends SIGTERM to each (5 s grace), then SIGKILL. Called from the task-status update path (§11).

The in-flight registry, `register_runner_task`, `terminate_all_inflight`, the shutdown await pattern, and `recover_orphaned_running_scripts` (renamed `recover_orphaned_running_jobs`) all carry over unchanged in behavior.

## 11. Task-lifecycle integration

Persistent jobs must die when their owning task transitions terminal. The hook lives in the task-status update path in `src/infrastructure/database.py` and `src/orchestrator/run_step.py`:

- `Database.update_task_status(task_id, status)` — when the new status is in `{completed, failed, failed-cancelled}`, the daemon dispatches `terminate_jobs_for_task(task_id)` (fire-and-forget via `asyncio.create_task` or a daemon-thread bridge, matching the Feishu notifier pattern).
- The terminate function:
  1. Queries `SELECT id FROM jobs WHERE task_id = ? AND status = 'running'`.
  2. For each, calls into the in-flight registry and sends SIGTERM (5 s grace) then SIGKILL.
  3. Audit-logs `job_killed` with `reason=task_ended` per row.

Audit-log entries from this path use `kind=job_killed` with payload `{job_id, reason: "task_ended", terminating_task_id}` so weekly review can spot tasks that left a lot of jobs to clean up.

## 12. Audit events

| Kind | Trigger | Payload |
|--|--|--|
| `job_submitted` | `POST /submit` succeeds | `{job_id, task_id, agent, review_required, persistent, max_runtime_seconds, max_output_bytes, title}` |
| `job_auto_started` | `review_required=false` job transitions submit → running | `{job_id, task_id, agent}` |
| `job_run` | Founder triggers `POST /{id}/run` (review_required=true path) | `{job_id, task_id, agent, max_runtime_seconds_override?}` |
| `job_rejected` | Founder triggers `POST /{id}/reject` | `{job_id, task_id, agent, reject_reason}` |
| `job_completed` | Subprocess exits 0 | `{job_id, task_id, exit_code: 0, duration_ms, stdout_bytes, stderr_bytes}` |
| `job_failed` | Subprocess exits non-zero (natural) | `{job_id, task_id, exit_code, duration_ms, stdout_bytes, stderr_bytes}` |
| `job_killed` | Subprocess killed (any reason) | `{job_id, task_id, reason: "timeout"|"output_cap"|"founder_stop"|"agent_stop"|"task_ended"|"daemon_shutdown"|"daemon_crash", duration_ms}` |
| `job_stopped` | `POST /{id}/stop` triggers the kill (founder or agent) | `{job_id, task_id, stopped_by: "founder"|"agent"}` (precedes the eventual `job_killed`) |

`job_killed` and `job_stopped` are distinct kinds because `stopped_by` is metadata about the actor, while `reason` is metadata about the kill mechanism — separating them keeps audit queries clean (`SELECT * FROM audit_log WHERE kind='job_killed' AND payload_json LIKE '%task_ended%'` works without parsing actor info).

## 13. Feishu integration

The `script_request` notification kind is renamed to `job_request`. Behavior carries over verbatim from `2026-05-25-feishu-script-request-notifications-design.md` with one extension: notifications are only sent for `review_required=true` jobs. Auto-running jobs (`review_required=false`) don't ping the founder — the whole point is autonomy.

- Submission with `review_required=true` → `notify_job_submitted` (founder-facing approval ping; reply with `APPROVE` → run, `REJECT\n<reason>` → reject).
- Terminal transition on a previously-notified job → `notify_job_run_result` (success / failure / killed, with a snippet of stdout/stderr head).
- Auto-running jobs (`review_required=false`): no submission notification, no result notification (founder browses `/jobs` to see them).

The `escalation_notifications.kind` enum gains `job_request`; the old `script_request` value is rewritten by the migration (§6.2).

## 14. Web UI

`web/src/features/jobs/` replaces `web/src/features/scripts/`. The `web/src/lib/api/jobs.ts` module mirrors `web/src/lib/api/scripts.ts` route-by-route. OpenAPI snapshot regenerates (`HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py`).

UI surface:

- `/jobs` — table view, grouped or filterable by status. Columns: id, title, agent, task, status, review_required (icon), persistent (icon), duration, age.
- `/jobs/:id` — detail panel with the full row, head text, live SSE stream when `status=running`, action buttons:
  - `pending` → Run / Reject
  - `running` → Stop
  - terminal → (no actions; immutable)
- Filter chips at top of the list: `Pending review` (review_required=true, status=pending), `Running`, `Completed`, `Failed`, `Rejected`.

The "split into request vs job columns" idea from earlier brainstorming is rejected — the row's status + flags are the right primary axes. A founder filtering by "pending review" sees exactly the review queue without a per-mode column.

## 15. Test coverage

Unit tests (per `tests/orchestrator/` and `tests/daemon/` patterns):

- `tests/daemon/test_jobs_runner.py` — adapts the existing `test_scripts_runner.py`, plus new tests for `max_runtime_seconds=None`, output-cap kill, task-terminal kill.
- `tests/daemon/test_jobs_routes.py` — adapts `test_scripts_routes.py`; new tests for both policy flags, validation of `rationale` when `review_required=true`, `tail` endpoint.
- `tests/infrastructure/test_jobs_migration.py` — new; runs the §6.2 migration against a fixture v0 DB with mixed SR-NNN rows + audit + notification rows, asserts every reference renames atomically.

Integration tests (under `tests/integration/`, opt-in via `-m integration`):

- `tests/integration/test_jobs_persistent.py` — spawns a real daemon, submits a `persistent=true` job (a `sleep 60 && echo done` loop), confirms agent can `tail`/`stop`, confirms task-terminal kill.
- `tests/integration/test_jobs_review_required.py` — submits `review_required=true`, confirms founder approval path via the route, confirms Feishu notification kind is `job_request`.
- `tests/integration/test_jobs_legacy_alias.py` — `happyranch scripts <verb>` shim writes deprecation warning + dispatches correctly.

## 16. Open questions / known limitations

- **No daemon-side allow_rules validation.** Misclassified `review_required=false` submissions can run anything the daemon can run. Tradeoff acceptable for the one-person org context; revisitable when multi-tenant or multi-founder org variants are introduced.
- **No output rotation.** Disk files for persistent jobs can grow unbounded *up to* `max_output_bytes` (default 50 MB). For a multi-day dev server this is fine; for an unusually chatty process the agent should set a lower cap or just stop the job periodically.
- **No job dependencies / queueing.** All `review_required=false` jobs auto-run in parallel. A future enhancement could add a per-agent or per-org concurrency cap if the founder observes resource problems.
- **Task-terminal kill is best-effort SIGTERM → SIGKILL.** A subprocess that ignores both (extremely unlikely with the 5s grace) would survive — the daemon would log the failure but not block the task transition. The startup recovery scan catches such zombies on next restart by force-failing any `running` row whose subprocess is unreachable.
- **No agent introspection of other agents' jobs.** Agent A cannot see or stop agent B's jobs even within the same org. Founder can see and stop everything. Adequate for v1; cross-agent coordination is the job of threads.
