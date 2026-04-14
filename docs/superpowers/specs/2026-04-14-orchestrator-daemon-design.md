# Orchestrator Daemon + HTTP API — Implementation Spec

**Status:** Spec 1 of a planned two-spec sequence. Spec 2 (`add-repo` capability) builds on the infrastructure this spec delivers.

Turn the orchestrator into a long-running daemon with an HTTP API. CLI commands become thin clients. Agents call back via the same HTTP surface. Workflow for handling tasks moves into Claude Code skills so the orchestrator's initial prompt stays small.

---

## 1. Responsibilities & Boundary

The daemon is a **machine-scoped, long-running process** that owns:

- The EH-driven orchestration loop (`Orchestrator.run_task`).
- Agent spawning via Claude Code CLI.
- All SQLite writes (tasks, steps, audit, scorecards).
- All workspace state mutations.
- An in-memory event bus serving SSE streams to CLI clients.

**CLI clients** (`opc run`, `opc tasks`, `opc status`, `opc agents`, `opc init`, `opc use`, `opc init-agent`, plus new agent-callback commands) are thin HTTP clients. They do not touch SQLite or workspaces directly.

**One active runtime at a time.** The daemon keeps a registry of known runtime paths and serves exactly one of them at any moment. Switching is an explicit `opc use <path>` call. See §2.

**No in-process fallback.** If the daemon isn't running, CLI commands fail with `"daemon not running — start it with scripts/daemon.sh start"`. Two codepaths for the same work is a bug factory.

**Only lifecycle operations stay outside the daemon:** `scripts/daemon.sh start|stop|status`.

---

## 2. Daemon Home & Runtime Registry

The daemon stores its own state under `~/.opc/`:

```
~/.opc/
├── daemon.pid         # written on start, removed on clean stop
├── daemon.port        # ephemeral TCP port the HTTP API bound to
├── daemon.log         # stdout+stderr, rotated by the user if desired
└── runtimes.yaml      # list of known runtime paths + which one is active
```

`runtimes.yaml`:

```yaml
active: /Users/tangbz/opc-runtime
registered:
  - /Users/tangbz/opc-runtime
  - /Users/tangbz/opc-staging
```

**Why `~/.opc/` and not inside a runtime dir:** daemon lifetime is decoupled from any one runtime. Switching runtimes must not require restarting the daemon; pid/port files inside a runtime would be orphaned on switch.

**Daemon startup flow:**

1. Read `~/.opc/runtimes.yaml` (missing file treated as empty registry). If empty or `active` is null, start in **idle mode** — HTTP API up, task-running endpoints return `409 no_active_runtime`, runtime-management endpoints still work.
2. Otherwise load the active `RuntimeDir`, verify its `opc.yaml` marker, open SQLite (WAL mode), wire up the `Orchestrator`.
3. Bind `127.0.0.1` on an ephemeral port (OS-assigned), write `~/.opc/daemon.port`.
4. Write `~/.opc/daemon.pid`.
5. Install signal handlers for SIGTERM / SIGINT.
6. Start accepting requests.

**CLI client discovery:** every CLI command reads `~/.opc/daemon.port`. Missing file → "daemon not running" error with the script hint.

---

## 3. HTTP API Surface

Local-only (`127.0.0.1` bind), no auth. Path prefix `/api/v1/`. Framework: **FastAPI + uvicorn + sse-starlette**. Reuses Pydantic models from `src/models.py`.

### Runtime management

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/api/v1/health` | Liveness ping. Returns `{"status": "ok", "active_runtime": "<path or null>"}`. |
| `POST` | `/api/v1/runtimes/register` | Body: `{"path": "..."}`. Creates `opc.yaml` marker at path, adds to registry, activates. |
| `POST` | `/api/v1/runtimes/activate` | Body: `{"path": "..."}`. Sets active runtime. |
| `GET` | `/api/v1/runtimes` | Lists registered runtimes, shows which is active. |

### Tasks (all scoped to active runtime)

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/api/v1/tasks` | Body: `{"type": "...", "brief": "..."}`. Creates task + starts execution asynchronously. Returns `{"task_id": "TASK-001"}` immediately. |
| `GET` | `/api/v1/tasks` | List tasks. |
| `GET` | `/api/v1/tasks/{id}` | Task detail. |
| `GET` | `/api/v1/tasks/{id}/events` | **SSE stream** of orchestration step events + session log lines. Replays recent history on subscribe, then streams live. Disconnecting does not affect the task. |
| `POST` | `/api/v1/tasks/{id}/completion` | Agent callback. Body: completion report fields (status, confidence, summary, risks, dependencies, reviewer_focus). Persists to DB, emits event. |

### Agents

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/api/v1/agents` | Performance tiers. |
| `POST` | `/api/v1/agents/init` | Body: `{"agent": "..." | null}`. SSE response streaming progress messages (workspace setup, repo clones, skill distribution). Closes when init completes. |
| `POST` | `/api/v1/agents/{name}/learnings` | Agent callback to append to `learnings.md`. Serialized by daemon. |

### Error conventions

- `404` for unknown task / runtime / agent.
- `409 no_active_runtime` for task operations when no runtime is active.
- `400` for malformed bodies.
- `5xx` for daemon-internal errors (logged).

---

## 4. CLI Client Refactor

Every CLI command is a thin wrapper around an HTTP call. A shared helper reads `~/.opc/daemon.port` and handles "daemon not running" uniformly.

### Command mapping

| Command | HTTP | Output |
|---|---|---|
| `opc init <path>` | `POST /runtimes/register` | Prints registered + active confirmation. |
| `opc use <path>` | `POST /runtimes/activate` | Prints new active path. |
| `opc run --brief "..."` | `POST /tasks`, then `GET /tasks/{id}/events` | Prints task_id on submit, streams events live, exits when terminal event (approved/rejected/escalated) arrives. |
| `opc tail TASK-001` | `GET /tasks/{id}/events` | Same stream, detached; for reattaching or watching an in-flight task. |
| `opc tasks` | `GET /tasks` | Table render. |
| `opc status TASK-001` | `GET /tasks/{id}` | Detail render. |
| `opc agents [--detail]` | `GET /agents` | Table render. |
| `opc init-agent [name]` | `POST /agents/init` | Streams progress messages. |
| `opc report-completion` | `POST /tasks/{id}/completion` | Agent callback (used inside skills, not typically by humans). |
| `opc learning` | `POST /agents/{name}/learnings` | Agent callback. |

### UX preservation

`opc run` feels synchronous — client blocks on the SSE stream until terminal event. Internally the daemon is async and can run many tasks at once. Ctrl-C disconnects the client; the task keeps running and can be reattached with `opc tail`.

### Removed

- The `--runtime` flag on all commands. Target runtime is whichever is active in the daemon; `opc use <path>` switches.
- `OPC_RUNTIME` env var handling in the CLI.

---

## 5. Daemon-Side Task Execution

### Workspace layout (per agent, single persistent directory)

```
<runtime>/workspaces/<agent>/
├── CLAUDE.md              # role + agent identity, regenerated only by opc init-agent
├── .claude/
│   ├── settings.json      # permissions + optional repo-pull hooks
│   └── skills/            # copied from protocol/skills/ at init-agent time
│       ├── start-task/SKILL.md
│       └── make-worktree/SKILL.md
├── agent.yaml             # repos config
├── learnings.md           # append-only, daemon-serialized via /learnings endpoint
├── scorecard.md           # daemon-written
├── recent_tasks.md        # daemon-written
└── repos/                 # base clones
```

No `sessions/` subfolder. No task-suffixed files. No `completion_report.json`.

### Session context flow

All task-scoped info is injected via the initial prompt sent to `claude -p "..."`:

```
You are {agent_name}. Use the start-task skill to handle this task.
Parameters:
  task_id: TASK-001
  brief: <brief text>
  role_guidance: <role-specific instructions — for EH this is the decision prompt currently built by capabilities.py; for delegated workers this is next_step.prompt from the EH decision>
```

`role_guidance` replaces the current per-call prompt text the orchestrator sends. Existing prompt-building code in `src/orchestrator/capabilities.py` keeps producing that text; it's just passed as a `role_guidance` parameter instead of being the whole prompt. The skill carries the workflow (§6). The orchestrator's prompt does not describe how to use `opc` CLI or handle errors.

### Completion handling

Agent calls `opc report-completion --task-id ... --status ... ...` mid-session. Daemon writes to DB (timestamped) and emits an event. When the Claude Code subprocess exits, the daemon looks up the **most recent** completion record for `(task_id, agent)` by timestamp:

- **Found** → mark session complete with that report's status.
- **Not found** → mark `"session ended without completion report"` and escalate.

If the agent calls `opc report-completion` more than once in a single session (e.g., after a retry), the last call wins. All calls are retained in the DB for audit.

### Concurrency model

- API layer enqueues tasks, returns immediately.
- Each task runs as its own asyncio task wrapping `asyncio.to_thread(orchestrator.run_task, task_id)`. Existing blocking `AgentExecutor` code is reused unchanged.
- **No per-agent locks.** Concurrent sessions on the same agent role share cwd and coexist because:
  - `CLAUDE.md` is read-only during sessions.
  - `learnings.md` writes go through the daemon (serialized).
  - `scorecard.md` / `recent_tasks.md` are daemon-written only.
  - Scratch files: skill instructs agents to use `/tmp`, never cwd.
- DB writes serialized via a single `asyncio.Lock` around the WAL SQLite writer. Reads unrestricted.

### Event bus

In-memory pub/sub, keyed by task_id. On SSE subscribe, the bus queries the DB for prior events of that task and replays them, then switches to live. Disconnect on either side is non-fatal.

### Daemon-restart policy

On startup, any task still `IN_PROGRESS` in the DB gets marked `ESCALATED` with reason `"daemon restarted mid-task"`. Their worktrees are best-effort cleaned (may fail if dirty — leave for manual inspection). Task resumption is out of scope.

---

## 6. Claude Code Skills

Agent workflow lives in versioned markdown files, not in orchestrator prompt-building code.

### Source of truth

```
protocol/skills/
├── start-task/SKILL.md
└── make-worktree/SKILL.md
```

### Distribution

`opc init-agent` **copies** these files into each agent workspace's `.claude/skills/` directory. Copy (not symlink) avoids stale-link surprises across filesystems and during runtime dir moves.

### `start-task` skill contents

- Parse `task_id`, `brief`, `role_guidance` from the orchestrator prompt.
- Execute the work loop appropriate to role (may invoke `make-worktree` if repo writes are needed).
- Report-back protocol:
  - Success: `opc report-completion --task-id <id> --status completed --confidence <n> --summary "..." --risks "..." --dependencies "..." --reviewer-focus "..."`
  - Blocker: `opc report-completion --task-id <id> --status blocked --summary "..."` then exit.
  - Mid-task learning: `opc learning --agent <name> --text "..."`
- Cleanup protocol: always run worktree cleanup as the final step, even on blocker/error paths (best-effort).
- Retry policy: if `opc` CLI returns non-zero, retry once after 1s, then exit with log.

### `make-worktree` skill contents

- When to invoke: before any `git commit` / `git checkout` / file edit inside `repos/<name>/`. Read-only exploration doesn't need a worktree.
- Flow (Bash-only, no daemon involvement):
  1. `cd workspaces/<agent>/repos/<repo_name>`
  2. `mkdir -p .claude/worktrees`
  3. `git worktree add .claude/worktrees/<task_id> -b task/<task_id>`
  4. `cd .claude/worktrees/<task_id>` — all writes happen here.
- Concurrency note: if `git worktree add` races another session, retry once.
- Cleanup: `git worktree remove .claude/worktrees/<task_id>` at end of task. Invoked from `start-task`.

---

## 7. Worktree Convention — Agent-Owned

Worktrees live inside the repo they branch from, following Claude Code's `.claude/worktrees/` convention:

```
<runtime>/workspaces/<agent>/repos/<repo_name>/
├── .git/
├── ... (base checkout, read-only during sessions)
└── .claude/worktrees/
    ├── <task_id_a>/        # branch task/<task_id_a>
    └── <task_id_b>/        # branch task/<task_id_b>
```

**No daemon involvement.** No worktree HTTP endpoint, no `opc get-worktree` CLI. The `make-worktree` skill teaches agents to manage worktrees themselves.

**Stale worktree handling:** `start-task` skill runs cleanup on every path, including failure. If stale worktrees still accumulate, a reaper is future work (out of Spec 1).

---

## 8. Lifecycle Script — `scripts/daemon.sh`

Shell script, one argument: `start`, `stop`, or `status`.

### `start`

1. Check `~/.opc/daemon.pid`. If file exists and the PID is alive (`kill -0 $PID`), exit 1 with "daemon already running (pid N)".
2. Remove stale pid file if process not alive.
3. `mkdir -p ~/.opc/`.
4. Launch detached: `nohup uv run python -m src.daemon >> ~/.opc/daemon.log 2>&1 &`
5. Capture the background PID, write `~/.opc/daemon.pid`.
6. Wait up to 5 seconds for `~/.opc/daemon.port` to appear. On success print `daemon started on port <port>`; on timeout print failure with log hint, exit 1.

### `stop`

1. Read `~/.opc/daemon.pid`. If missing → exit 0 with "daemon not running".
2. Send SIGTERM. Wait up to 10 seconds for exit.
3. If still alive, SIGKILL.
4. Remove `~/.opc/daemon.pid` and `~/.opc/daemon.port`.

### `status`

- pid file present + process alive → print `running (pid N, port P)`, exit 0.
- pid file present + process dead → print `stale (pid file from dead process)`, exit 1.
- No pid file → print `not running`, exit 1.

### Daemon-side signal handling

- SIGTERM / SIGINT → stop accepting new task submissions, mark in-flight `IN_PROGRESS` tasks as `ESCALATED` with reason `"daemon shutdown"`, close DB, remove pid + port files, exit 0.

### Port selection

Daemon asks the OS for an ephemeral port (`bind(('127.0.0.1', 0))` then reads the assigned port), writes to `~/.opc/daemon.port`.

### Dev mode

`uv run python -m src.daemon` in the foreground uses the same entry point without nohup/background. For local debugging.

---

## 9. Code Changes

### New packages

- `src/daemon/`
  - `__main__.py` — entry point; logging, port bind, port/pid files, uvicorn startup, signal handlers.
  - `app.py` — FastAPI app factory, route registration.
  - `state.py` — daemon-scoped state holder (active `RuntimeDir`, `Database`, `Orchestrator`, event bus, DB lock).
  - `event_bus.py` — in-memory pub/sub + DB replay on subscribe.
  - `runner.py` — per-task asyncio wrapper around `orchestrator.run_task` via `asyncio.to_thread`; emits events.
  - `routes/` — one file per resource: `health.py`, `runtimes.py`, `tasks.py`, `agents.py`.

- `src/client/`
  - `client.py` — reads `~/.opc/daemon.port`, provides `get` / `post` / `stream` helpers with uniform "daemon not running" error.

### New files

- `protocol/skills/start-task/SKILL.md`
- `protocol/skills/make-worktree/SKILL.md`
- `scripts/daemon.sh`

### Changed files

- `src/cli.py` — every command handler becomes a thin HTTP client call. New commands: `cmd_tail`, `cmd_use`, `cmd_report_completion`, `cmd_learning`. Remove `--runtime` flag.
- `src/orchestrator/orchestrator.py` — `_run_agent` no longer reads `completion_report.json`. After subprocess exits, reads latest completion record from DB for `(task_id, agent)`; if missing, marks escalated. `initialize_workspace` no longer called per session (workspace is set up once at `opc init-agent`).
- `src/orchestrator/context_builder.py` — `write_claude_md` drops the `task_brief` parameter; CLAUDE.md no longer contains "Current Task" section. `initialize_workspace` copies skill files from `protocol/skills/` to `<workspace>/.claude/skills/`.
- `src/orchestrator/executor.py` — `ExecutorResult.report` may be `None` on success (completion now arrives via HTTP); callers updated.
- `src/config.py` — adds daemon-home path (default `~/.opc/`), bind host (default `127.0.0.1`).

### Removed

- Direct-subprocess orchestration from the CLI path — `opc run` cannot run without a daemon.
- `OPC_RUNTIME` env var handling in CLI commands.
- `completion_report.json` file handling throughout.

### New dependencies

- `fastapi`
- `uvicorn`
- `sse-starlette` (or built-in FastAPI streaming if sufficient)
- `httpx` (sync + streaming) for the client

---

## 10. Testing

Layered tests, least to most fidelity:

1. **Daemon route unit tests** (`tests/daemon/test_routes_*.py`) — FastAPI `TestClient`, in-process, no uvicorn. One file per route module. Covers happy + error paths.
2. **Event bus tests** (`tests/daemon/test_event_bus.py`) — late subscribers get replay then live, two subscribers see same stream, disconnect is non-fatal.
3. **Runner tests** (`tests/daemon/test_runner.py`) — mock `AgentExecutor.run`; test subprocess-exits-without-completion → escalation, subprocess-exits-after-completion → success.
4. **CLI client tests** (`tests/cli/test_client_*.py`) — point client at a `TestClient`-mounted app. Covers SSE streaming, formatting, "daemon not running" error.
5. **Integration tests** (`tests/integration/test_end_to_end.py`) — real daemon via `scripts/daemon.sh start` on a temp runtime; real CLI via subprocess; stubbed Claude Code binary. Gated behind `pytest -m integration`.
6. **Skill file validation** (`tests/test_skills.py`) — static checks of skill markdown: frontmatter is valid, required fields present, referenced CLI commands actually exist. Catches drift.

### Fixtures

- `daemon_app` — FastAPI app with fresh temp runtime + in-memory DB.
- `live_daemon` — starts real daemon for integration tests.
- `tests/fixtures/fake_claude` — shell script that reads a scripted response plan and optionally calls `opc report-completion` to simulate an agent.

### Existing tests

Most of the 106 existing tests keep working because the daemon wraps the existing `Orchestrator` class. Tests tied to `completion_report.json` file-reading need adjustment for the DB-backed lookup.

### Deliberately not tested in Spec 1

- Real Claude Code subprocesses (costly, nondeterministic). Integration uses the fake binary.
- Multi-runtime simultaneity — one active at a time, single-runtime coverage is sufficient.

---

## 11. Out of Scope

- **`add-repo` capability** — Spec 2. Will add a daemon endpoint, a CLI, and likely a skill update.
- **launchd auto-start** — future polish spec. Daemon runs via `scripts/daemon.sh` in Spec 1.
- **Web UI** — HTTP API is web-ready, no frontend ships here.
- **Remote access / auth** — `127.0.0.1` only. Tokens/TLS are a future security spec.
- **Task resumption after daemon restart** — in-flight tasks are escalated with `"daemon restarted mid-task"`.
- **Stale worktree reaper** — skill-level cleanup only.
- **Structured audit query API** — single-task lookup only. Founder dashboard (implementation order step 11) will design a query surface.
- **Concurrent runtime activation** — one active at a time.
- **MCP** — explicitly rejected in brainstorming.
- **Fully async CLI UX** — `opc run` still blocks on SSE for familiar UX; `opc tail` covers detached watching.
