# Project: OPC Multi-Agent Tourism Organization

## What This Is
A one-person company (OPC) that provides online tourism information and booking services for foreign tourists visiting Hong Kong SAR and Macau SAR. The entire operation is run by AI agents supervised by a single human founder.

## Architecture Summary
- **Layer 1**: Founder (human) — sets rules, handles escalations, reviews weekly dashboard
- **Layer 2**: 4 Manager Agents — Content Manager, Engineering Head, Operations Manager, CX Manager
- **Layer 3**: 10 Worker Agents — Content Writer, SEO Agent, Content QA, Product Manager, Dev Agent, Payment Agent, QA Engineer, Partner Liaison, Compliance Agent, Support Agent
- **Infrastructure**: Audit Logger, Escalation Router, Knowledge Base

Agents operate autonomously within defined authority. Managers cross-audit each other (peer review). No agent both proposes and approves consequential actions (maker-checker pattern).

The org-specific content (charter, agents, teams, escalation rules) lives **per runtime** under `<runtime>/org/`, not in the repo. A canonical sample tree ships at `examples/orgs/hk-macau-tourism/` — future runtimes will be bootstrapped via `opc init <path> --slug <slug> --from examples/orgs/hk-macau-tourism` (this CLI form lands in Plan 2; for now the example tree serves as the reference shape).

## Design Documents (read these first)

The following documents are in the `protocol/` folder.

- `00-completion-contract.md` — Universal completion-report format, EH decision schema, agent-callback list
- `05-runtime-blueprint.md` — Index pointing to the split blueprint documents:
  - `05b-agent-runtime.md` — Executor model, memory architecture, lifecycle & scheduling
  - `05c-orchestrator.md` — Orchestrator responsibilities, performance tiers, permissions, task state machine
  - `05e-dashboard.md` — Dashboard layout, API endpoints, implementation order
- `06-knowledge-base.md` — Shared KB rules

Note: `05c-orchestrator.md` and `05e-dashboard.md` are now org-agnostic — they
reference "team manager" / "team alpha" as placeholders rather than naming
specific roles like Engineering Head. The org-specific charter, teams, and
agent prompts live in the runtime under `org/`. The canonical sample tree is
at `examples/orgs/hk-macau-tourism/` for the HK/Macau tourism org.

## Tech Stack
- **Language**: Python 3.11+ (currently running 3.13)
- **Package manager**: `uv`
- **Agent executor**: Per-agent executor selection. Claude Code (`claude -p "<prompt>" --permission-mode auto`) and Codex (`codex exec --json -`) are supported — no third-party agent framework dependency
- **Daemon**: FastAPI HTTP service (`src/daemon/`) — serves orchestrator work, SSE task events, agent callbacks
- **CLI**: Thin HTTP client (`src/client/`) that talks to the daemon over localhost
- **Agent workflow**: Shared workspace skills (`protocol/skills/`) — `start-task`, `make-worktree`, `manage-repo`, `manage-agent`. The orchestrator prompt references the same SOPs for both Claude and Codex workspaces
- **Orchestrator**: Custom Python application. `run_step` is the only primitive — each invocation advances one task by one subprocess call; an async `TaskQueue` + worker pool (`src/daemon/queue.py`) drives re-enqueues across steps. EH drives decisions; performance scoring derived from implicit review verdicts on delegated work
- **Data models**: Pydantic v2 + pydantic-settings
- **Database**: SQLite with WAL mode (audit logs, scorecards, task state)
- **Knowledge base**: File-backed markdown under `<runtime>/kb/` with atomic writes, substring/tag search, and regenerated `_index.md` (see `src/infrastructure/kb_store.py`). Vector store / RAG not yet added
- **LLM**: Provider depends on the selected executor (Claude Code or Codex)
- **Hosting**: Local Mac Mini

## Implementation Order (follow this sequence)
1. ~~**Product & Engineering Team**~~ done — Engineering Head + Product Manager + Dev Agent + Payment Agent + QA Engineer with executor-backed agent sessions. EH-driven orchestration loop (EH decides each step: delegate, handle directly, or escalate). Audit logging, agent memory, performance scoring all implemented.
2. ~~**Audit logging**~~ done — SQLite-backed audit logger with session start/end, completion reports, orchestration steps, escalations.
3. ~~**EH-driven orchestration**~~ done — Engineering Head analyzes each task and decides the approach. No hardcoded task chains. Max 10 orchestration steps before escalation.
4. ~~**Agent memory**~~ done — Persistent workspaces with executor-specific bootstrap docs (`CLAUDE.md` or `AGENTS.md`), learnings.md, task_history.md. Context builder regenerates identity on tier changes.
5. ~~**Performance scoring**~~ done — Rolling 30-day scorecards, green/yellow/red tiers, exposed to EH via capabilities prompt.
6. **Content Team** — Content Writer + Content QA + SEO Agent + Content Manager.
7. **Ops Team** — Partner Liaison + Compliance Agent + Operations Manager. Enables real cross-team audits for payment changes.
8. **Inter-Team communication** — Orchestrator routes tasks between Teams.
9. **CX Team** — Support Agent may run as persistent agent for real-time chat, not batch.
10. **Founder dashboard** — Aggregate audit logs, escalation summaries, scorecards into weekly view.
11. ~~**Talk flow**~~ done — founder↔agent conversations with SQLite-tracked talks, transcripts under `<runtime>/talks/`, end-of-talk learnings + KB entries.

## Key Constraints
- **Two jurisdictions**: Hong Kong (PDPO), Macau (PDPA) — both must be complied with simultaneously. Mainland China is explicitly out of scope (PIPL/CSL/DSL do not apply).
- **PCI-DSS**: No raw card data storage — ever
- **Political sensitivity**: Any content about China/HK/Macau relations escalates to founder
- **Budget authority**: Auto-approved up to $200 USD single / $100/month recurring. Above that -> founder
- **Refund authority**: CX Manager up to $150 USD. Above that -> founder
- **Downtime tolerance**: 30 minutes max before escalation

## Directory Layout

Source code and protocol docs live in the repo. Runtime data lives in a dedicated **runtime directory** created with `opc init`.

```
~/projects/my-opc/                     # Source code (this repo)
|-- CLAUDE.md
|-- pyproject.toml
|-- protocol/                          # System kernel: completion contract, runtime blueprint, KB rules, skills
|   |-- 00-completion-contract.md
|   |-- 05-runtime-blueprint.md
|   |-- 05b-agent-runtime.md
|   |-- 05c-orchestrator.md
|   |-- 05e-dashboard.md
|   |-- 06-knowledge-base.md
|   +-- skills/                        # Shared skills copied into every agent workspace
|       |-- start-task/                # Parses injected params, runs role, reports via CLI callback
|       |-- make-worktree/             # Creates an isolated git worktree under .claude/worktrees/
|       |-- manage-repo/              # Agent-driven repo add/remove/update via opc manage-repo
|       +-- manage-agent/            # EH-driven agent enroll/update/terminate via opc manage-agent
|-- scripts/
|   +-- daemon.sh                      # Starts the FastAPI daemon (uv run python -m src.daemon)
|-- src/
|   |-- cli.py                         # Unified CLI entry point (`opc` command) — HTTP client
|   |-- config.py                      # Settings (OPC_ env prefix, operational thresholds)
|   |-- runtime.py                     # RuntimeDir — self-describing runtime folder (opc.yaml marker)
|   |-- models.py                      # Pydantic models + StrEnums
|   |-- client/
|   |   +-- client.py                  # httpx-based client for the daemon (+ SSE streaming)
|   |-- daemon/                        # FastAPI HTTP daemon
|   |   |-- __main__.py                # Uvicorn entry (python -m src.daemon)
|   |   |-- app.py                     # FastAPI app factory, lifespan, DaemonState wiring
|   |   |-- state.py                   # DaemonState (db, runtime, settings, sessions, event bus)
|   |   |-- auth.py                    # Bearer-token dependency (~/.opc/auth_token)
|   |   |-- paths.py                   # ~/.opc/ home paths (auth token, runtimes.yaml)
|   |   |-- runtimes.py                # Runtime registry (runtimes.yaml, set/get active)
|   |   |-- runner.py                  # enqueue_task() — thin entry that pushes a task_id onto state.queue
|   |   |-- queue.py                   # Async TaskQueue + worker pool (bridges FastAPI event loop to sync run_step)
|   |   |-- sessions.py                # Active-session tracker (task_id,agent) -> session_id
|   |   |-- event_bus.py               # Per-task event pub/sub with DB replay + synthesized terminals
|   |   |-- agent_config.py            # Read/write workspaces/<agent>/agent.yaml
|   |   +-- routes/
|   |       |-- health.py              # GET /health
|   |       |-- runtimes.py            # POST /runtimes/init, POST /runtimes/use, GET /runtimes
|   |       |-- tasks.py               # POST /tasks, GET /tasks, GET /tasks/{id}, SSE /tasks/{id}/events, GET /tasks/{id}/recall, POST /tasks/{id}/resolve-escalation, POST /tasks/{id}/revisit, callbacks
|   |       |-- agents.py              # GET /agents, POST /agents/init (SSE), POST /agents/{name}/learnings, POST /agents/manage (enroll/update/terminate — file-backed under <runtime>/org/agents/), GET /agents/enrollments, POST /agents/{name}/approve, POST /agents/{name}/reject, POST /agents/{name}/repos
|   |       |-- audit.py               # GET /audit — filtered audit-log view (task/agent/action/since/limit)
|   |       |-- kb.py                  # Knowledge base: GET /kb, /kb/{slug}, /kb/search; POST /kb, /kb/{slug}, /kb/reindex, /kb/precedent; DELETE /kb/{slug}
|   |       +-- talks.py               # /talks — first-class founder↔agent conversations
|   |-- orchestrator/
|   |   |-- orchestrator.py            # Orchestrator facade: holds deps, exposes run_step (no more run_task)
|   |   |-- run_step.py                # Single-step primitive — advance one task by one subprocess call
|   |   |-- capabilities.py            # Builds capabilities prompt for EH decision sessions
|   |   |-- executors.py               # Provider-specific executor subprocess launchers
|   |   |-- performance_tracker.py     # 30-day rolling scorecards, tier calculation
|   |   |-- context_builder.py         # Delegates workspace bootstrap to provider-specific adapters
|   |   |-- workspace_adapters.py      # Generates CLAUDE.md or AGENTS.md, settings, and copies skills (constructors take RuntimeDir)
|   |   |-- agent_def.py               # AgentDef dataclass + frontmatter parser/renderer for <runtime>/org/agents/<name>.md
|   |   |-- prompt_loader.py           # File-based agent loader (active + _pending under <runtime>/org/agents/)
|   |   +-- migration.py               # opc migrate-to-org-runtime — lifts a legacy runtime into the org/ shape
|   |-- infrastructure/
|   |   |-- database.py                # SQLite (WAL mode), typed CRUD, task_results.status column, parent_task_id / note / final_artifact_dir / block_kind on tasks. (The legacy agent_enrollments table is no longer the source of truth — agents now live as files under <runtime>/org/agents/. The table remains in the schema for backward compatibility with old runtimes; new code paths read/write through prompt_loader.)
|   |   |-- audit_logger.py            # Semantic logging (session, verdict, escalation, orchestration steps, escalation_resolved)
|   |   |-- kb_store.py                # Knowledge base: slug validation, atomic entry write, list/read/update/delete, search, _index.md regeneration, near-duplicate detection
|   |   +-- talk_store.py              # Transcript file writer: atomic, per-talk markdown
|   |-- agents/                        # Agent definitions (future)
|   +-- tools/                         # Agent tools (future)
|-- tests/                             # ~390 tests (unit + a couple of integration)
|   |-- daemon/                        # Route-level tests for the FastAPI app
|   |-- integration/                   # End-to-end tests with fake Claude and fake Codex binaries
|   +-- test_*.py                      # Orchestrator, executor, config, skills, etc.
|-- examples/orgs/                     # Canonical sample org trees (copy into a runtime to bootstrap)
|   +-- hk-macau-tourism/
|       +-- org/                       # charter.md, escalation-rules.md, teams.yaml, agents/<name>.md
+-- docs/superpowers/
    |-- specs/                         # Design specs
    +-- plans/                         # Implementation plans

~/.opc/                                # Daemon home (per-user)
|-- auth_token                         # Bearer token shared by daemon + CLI
+-- runtimes.yaml                      # Registered runtime dirs + which one is active

<runtime-dir>/                         # Created by `opc init <path> --slug <slug>`
|-- opc.yaml                           # marker (slug, created_at, schema_version)
|-- opc.db                             # per-runtime SQLite
|-- org/                               # editable org content
|   |-- charter.md                     # reference doc
|   |-- escalation-rules.md            # reference doc
|   |-- teams.yaml                     # team layout
|   |-- config.yaml                    # optional org overrides (e.g. session_timeout_seconds)
|   +-- agents/
|       |-- <name>.md                  # active agents
|       +-- _pending/<name>.md         # awaiting founder approval
|-- workspaces/
|   +-- <agent_name>/                  # One per agent (dynamic — created by init-agent or approve-agent)
|       |-- agent.yaml                 # Per-agent config (repos, etc.)
|       |-- CLAUDE.md                  # Generated from system prompt (protocol docs or enrollment)
|       |-- .claude/
|       |   |-- settings.json          # Permissions + PreToolUse hook (git pull all repos)
|       |   +-- skills/                # All skills copied from protocol/skills/
|       |-- repos/                     # Git clones declared in agent.yaml
|       |   +-- <name>/                # One dir per entry in agent.yaml `repos:`
|       |-- learnings.md
|       +-- task_history.md            # Per-agent history (renamed from recent_tasks.md; legacy files auto-migrated)
|-- kb/                                # Shared knowledge base (see protocol/06-knowledge-base.md)
|   |-- _index.md                      # Regenerated after every write
|   +-- <slug>.md                      # Flat; filename = slug
+-- talks/                             # Transcript files written at /talk end
    +-- TALK-NNN.md
```

## Configuration

Operational settings use the `OPC_` environment variable prefix. Runtime paths (database, workspaces) are derived from the runtime directory, not from env vars.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPC_CLAUDE_CLI_PATH` | `claude` | Path to Claude Code CLI |
| `OPC_CODEX_CLI_PATH` | `codex` | Path to Codex CLI |
| `OPC_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `OPC_PROTOCOL_DIR` | `protocol` | Protocol docs dirname (relative to project root) |
| `OPC_MAX_ORCHESTRATION_STEPS` | `10` | Max EH decision steps before escalation |
| `OPC_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout (30 min) — global default; see "Session timeout resolution" below |
| `OPC_TIER_GREEN_THRESHOLD` | `0.90` | Acceptance rate for green tier |
| `OPC_TIER_YELLOW_THRESHOLD` | `0.75` | Acceptance rate for yellow tier |

### Session timeout resolution

`Orchestrator._resolve_session_timeout(agent_name)` walks three layers, highest precedence first:

1. **Agent override** — optional `session_timeout_seconds: <int>` in the agent's frontmatter (`<runtime>/org/agents/<name>.md`). Use for one slow worker (e.g. a Codex agent doing long builds) without affecting peers.
2. **Org override** — optional `session_timeout_seconds: <int>` in `<runtime>/org/config.yaml`. Use to bump the whole runtime above the code default.
3. **Code default** — `Settings.session_timeout_seconds` (1800s), itself overridable via the `OPC_SESSION_TIMEOUT_SECONDS` env var.

Each layer accepts `null`/missing as "inherit from the next layer." Values must be positive integers; non-int (string, float, bool) or `<= 0` raises at parse time. The org config is loaded via `src/orchestrator/org_config.py` (`OrgConfig` dataclass + `load_org_config(runtime)`); `<runtime>/org/config.yaml` is optional and unknown keys are ignored for forward compatibility.

### Agent executors

Each workspace declares an `executor` in `agent.yaml`. Supported values are `claude` and `codex`; missing values in older workspaces default to `claude`.

If the Engineering Head wants to enroll a new Codex-backed worker, the
`opc manage-agent --from-file ...` payload should set `"executor": "codex"`.
Example:

```json
{
  "action": "enroll",
  "name": "dev_agent_codex",
  "task_id": "TASK-123",
  "session_id": "sess-abc123",
  "description": "Implements product and platform changes as a Codex-backed developer agent.",
  "system_prompt": "You are the Dev Agent. Your responsibilities are...",
  "executor": "codex",
  "repos": {
    "my-opc": "https://github.com/t-benze/my-opc.git"
  }
}
```

Founder approval stays unchanged (`opc approve-agent <name>`), but the approved
workspace will be bootstrapped as a Codex workspace: `agent.yaml` keeps
`executor: codex`, the readiness marker becomes `AGENTS.md`, and the
Claude-specific `.claude/settings.json` path is not the primary bootstrap
surface.

Payloads can authenticate via either an active EH task session
(`task_id` + `session_id`) or an open EH talk (`talk_id`). The two paths
are mutually exclusive. See `protocol/skills/manage-agent/SKILL.md` for
the full payload shapes.

**Codex agents share the same skills tree as Claude agents.** Codex CLI
≥0.125 discovers skills by walking `.agents/skills/` from the working
directory up to the repo root (parallel to Claude's `.claude/skills/`).
`CodexWorkspaceAdapter._copy_skills` copies `protocol/skills/` into
`<workspace>/.agents/skills/` on every `opc init-agent`, so `start-task`,
`talk`, `make-worktree`, `manage-repo`, and `manage-agent` all resolve
the same in Codex sessions as they do in Claude sessions. AGENTS.md
points at the start-task skill rather than re-inlining the completion
contract. Enrollment `system_prompt` values may therefore reference skill
names safely — e.g. *"use the start-task skill"* — and should still
focus on role description and quality standards rather than duplicating
lifecycle instructions the skill already carries.

TASK-077 (2026-04-24) is the historical failure that motivated the
inlining: `senior_dev` was enrolled with "Use the **start-task** skill..."
in its prompt, completed the work, then exited 0 without calling
`opc report-completion`, and the orchestrator auto-rejected with *no
completion callback*. The fix was once "inline the contract into
AGENTS.md"; the durable fix is "let Codex resolve the same skill
Claude does," which is what's wired up now. If you ever see the same
symptom again, first check that `.agents/skills/start-task/SKILL.md`
exists in the workspace — if it does not, regenerate via
`opc init-agent <agent>`.

**Codex `workspace-write` sandbox blocks localhost by default.** The
`opc` CLI talks to the daemon over `127.0.0.1` via httpx, so without an
override the agent's `opc report-completion` call dies with
`httpx.ConnectError: [Errno 1] Operation not permitted` and the task
auto-rejects with *no completion callback* — same surface symptom as
TASK-077 but a different root cause. `CodexExecutor.run` therefore passes
`-c sandbox_workspace_write.network_access=true` on every Codex
invocation. Do not remove this flag without first re-architecting the
agent callback path to not require localhost sockets (e.g., a file-drop
the daemon polls). TASK-080 (2026-04-25) is the canonical failure:
`senior_dev` produced a complete 130-line `design-review.md` artifact,
exited rc=0, then was auto-rejected because the final HTTP callback
never made it past the sandbox.

Repos are configured per agent in `<runtime>/workspaces/<agent>/agent.yaml`:
```yaml
repos:
  web-app: https://github.com/t-benze/web-app.git
  docs: https://github.com/t-benze/docs.git
```

`opc init-agent` creates a default `agent.yaml` with empty repos if one doesn't exist.

### Agent permission model

Agents call the orchestrator's CLI (`opc report-completion`, `opc learning`, future callbacks) as their only sanctioned side-effect channel. The `--from-file` callback pattern is shared across executors. Claude workspaces additionally rely on explicit Bash allow rules, which live in **two places** and must stay in sync for Claude sessions:

1. `.claude/settings.json` `permissions.allow` — written by `ClaudeWorkspaceAdapter.write_settings_json`. Used by interactive (non-`-p`) sessions and surfaces intent to anyone inspecting the workspace.
2. `--allowedTools` on the CLI — passed by `ClaudeExecutor.run` for every headless session.

Both surfaces are generated from `allow_rules_for_agent(agent_name, cli=...)` in `src/orchestrator/workspace_adapters.py`, which renders the *same* per-agent list in each syntax (settings uses `Bash(<cmd>:*)`; CLI uses `Bash(<cmd> *)`). Do not hand-edit one side without the other — and don't hand-edit the generated `.claude/settings.json`, `opc init-agent` will rewrite it.

**Baseline grant (every agent):** `opc` — the callback channel.

**Engineering Head extras** (see `### Allow Rules` under "Engineering Head" in `protocol/02-system-prompts-managers.md`): `gh pr close`, `gh pr comment`, `gh issue close`, `gh issue comment`. Purpose: EH needs to close superseded/stale PRs and close issues substantively fixed on `main` during revisit cleanup — without these, Claude Code's headless risk heuristic refuses those calls even in `--permission-mode auto` (see TASK-067, where `gh issue close 93` was declined). The extras are deliberately narrow — no `gh pr merge`, no `gh pr create`, no `gh issue delete` — because each extra prefix can silently mutate shared external state on every future task.

**Why both surfaces for Claude:** in headless `-p` mode, Claude Code 2.1.105 ignores the workspace's `permissions.allow` list (observed empirically: `command_permissions.allowedTools: []` regardless of settings.json). Without the `--allowedTools` flag the agent's first `opc ...` call is blocked by auto-mode prompting, the callback never reaches the daemon, and the task silently rejects — see the TASK-007/008/009 post-mortem.

**When adding new orchestrator-side capabilities, keep them under the `opc` binary so they stay inside the baseline allow rule.** Only add a raw-tool prefix to the protocol's `### Allow Rules` list when the operation genuinely cannot be wrapped in `opc` (e.g., third-party CLI targeting external infrastructure we don't own). Each new prefix bypasses the auto-mode risk heuristic for every task that agent runs thereafter, so scope it as narrowly as the `gh pr close`/`gh issue close` grants above.

**Agent-side completion payloads must be single-line `opc` invocations.** This is mandatory across executors. For Claude specifically, the permission matcher treats newlines (and `&&`, `||`, `;`, `|`) as command separators and matches each subcommand independently. Multi-line bash with backslash continuations is rejected even though the surface command is `opc ...`. The `start-task` skill therefore mandates writing the payload to `/tmp/completion-<task_id>.json` and invoking `opc report-completion --from-file <path>` as a single line. Any new agent-facing callback with multiple arguments should follow the same `--from-file` pattern.

## Code Style
- Type hints on all function signatures
- Pydantic v2 models for structured data, StrEnum for enumerations (agent names are plain strings, not enums — agents are discovered dynamically from `<runtime>/org/agents/*.md`)
- Tests for business logic (escalation rules, scoring, tier calculation)
- `from __future__ import annotations` in all source files

## Org-per-runtime layout

Each runtime carries its own org content under `<runtime>/org/`:

- `charter.md` — org-level reference doc (purpose, team scope, etc.)
- `escalation-rules.md` — when to escalate to founder
- `teams.yaml` — team layout (which manager owns which workers)
- `config.yaml` — optional org-level setting overrides (currently `session_timeout_seconds`)
- `agents/<name>.md` — active agent definitions (frontmatter + system prompt)
- `agents/_pending/<name>.md` — pending enrollments awaiting founder approval

`AgentDef` (in `src/orchestrator/agent_def.py`) is the in-memory representation:
markdown-with-YAML-frontmatter, parsed/rendered by `parse_agent_text` /
`render_agent_text`. Fields: `name`, `team`, `role` (worker|manager),
`executor` (claude|codex), `description`, `allow_rules`, `repos`,
`enrolled_by`, `enrolled_at_task`, `enrolled_at`, `session_timeout_seconds`
(optional per-agent override; see "Session timeout resolution"),
`system_prompt` (body).

`src/orchestrator/prompt_loader.py` is the only API for reading and writing
these files — `load_agent`, `list_agents`, `list_pending`, `write_pending_agent`,
`approve_agent`, `reject_agent`. Routes (`src/daemon/routes/agents.py`) and
the orchestrator (`src/orchestrator/run_step.py`,
`src/orchestrator/workspace_adapters.py`) all read through this module. Do
NOT reach into the legacy `agent_enrollments` SQLite table for new code paths
— it's preserved for migration only.

`TeamsRegistry` (in `src/orchestrator/teams.py`) is seeded from
`<runtime>/org/teams.yaml` and auto-persists on `add_worker` / `remove_worker`.
There is no `DEFAULT_LAYOUT` — a runtime without a `teams.yaml` is treated as
an empty registry until the founder writes one.

`opc init <path> --slug <slug>` is the only way to create a new runtime;
`--slug` is mandatory on first init. `RuntimeDir.init(path, slug=...)`
raises `ValueError` if the slug is missing on a fresh path. Idempotent
re-runs preserve the existing slug.

For runtimes created before the `org/` folder existed, `opc migrate-to-org-runtime
<path> --slug <slug> --i-have-a-backup --apply` lifts the legacy DB-backed
agents into the file-based layout.

## Task status vocabularies

Note: agents self-report `status="completed"|"blocked"` via `opc report-completion`
(the worker's view of its session). The orchestrator-owned `TaskStatus` lives on
the `tasks` row and is distinct: it takes one of `{pending, in_progress,
blocked, completed, failed}` based on orchestration classification, with
`block_kind` (`delegated` | `escalated`) specifying the reason.

## EH decision contract

Engineering Head completion payloads carry two fields with distinct purposes
— keep them both when modifying the EH-facing prompt or skill:

- **`summary`** (prose) — human-readable description of what the EH did or
  concluded this step. Rendered in `opc details`, audit logs, `task_history.md`.
  Stored on `task_results.output_summary` exactly like worker summaries.
- **`decision`** (JSON object, NextStep schema) — the structured action the
  orchestrator will execute: `{"action": "delegate"|"done"|"escalate", ...}`.
  Stored on `task_results.decision_json` (EH-only column; workers leave it
  NULL). Parsed by `Orchestrator._parse_next_step` directly — no prose
  inference.

Pre-TASK-071 contract had `output_summary` itself be the JSON decision. That
double-encoding trap tripped whenever EH ran commands itself and wrote a prose
"here's what I did" at completion time (e.g. `gh issue close` cleanup tasks).
The structured `decision` field eliminates the trap: EH can write natural prose
in `summary` while the orchestrator acts on a separately-typed `decision`.

A legacy fallback (parse `output_summary` as JSON when `decision` is NULL)
stays in the parser during the transition so in-flight workspaces on older
skill copies still work; remove it after confirming every workspace has been
`opc init-agent`-regenerated with the new skill.

## Running Tests
```bash
uv run pytest tests/ -v                  # unit tests only (default)
uv run pytest tests/ -v -m integration   # end-to-end tests (spawns a real daemon)
uv run pytest tests/ -v -m ""            # both
```

Integration tests are excluded by default (`addopts = "-m 'not integration'"`)
because they spawn a real daemon process and a fake Claude binary, which
makes them slower and slightly more brittle than the in-process unit tests.
They are isolated from the developer's real `~/.opc/` via the `OPC_DAEMON_HOME`
env redirect — they will not touch your live runtime, audit DB, or pid files.

Run them locally before any change that touches the daemon lifespan,
SessionTracker, or callback routes — that's the surface area where the
unit tests have repeatedly failed to catch regressions (see commit 8581f26
post-mortem). CI should run them on every PR for the same reason.

## Running the Daemon + CLI

The CLI is an HTTP client. Start the daemon once, then run CLI commands.

```bash
scripts/daemon.sh start                                         # start daemon in background (pid/port under ~/.opc/)
scripts/daemon.sh status                                        # or stop

opc init /path/to/runtime --slug hk-tourism                     # create + register + activate a runtime dir (slug required)
opc use /path/to/other-runtime                                  # switch the daemon's active runtime
opc run --brief "Explore the payment module"                    # submit a task; EH decides approach
opc run --team engineering --brief "Add Alipay support"          # route to a team
opc tail TASK-001            # stream live SSE events for a task
opc tasks                    # list recent tasks
opc details TASK-001         # show task details (status, block_kind, note, results, audit log)
opc agents [--detail]        # show performance tiers
opc audit TASK-007                               # filtered audit-log view (task, agent, action, since, limit)
opc audit --agent engineering_head --limit 10    # recent entries for one agent, any task
opc audit TASK-007 --json                        # raw JSON with full payloads
opc init-agent               # initialize all agent workspaces (repo clones + system prompts + skills)
opc init-agent dev_agent     # initialize a specific agent
opc recall TASK-001 [--tree] [--fetch-artifact <relpath>]   # fetch task brief + artifact tree/content
# Knowledge base (read: any; write: any via --from-file; delete: any team manager (audited); founder via --as-founder; precedent: founder):
opc kb list [--topic <t>] [--type reference|precedent]
opc kb get <slug>
opc kb search <query> [--limit N]
opc kb add --agent <you> --from-file /tmp/kb-<slug>.md
opc kb update <slug> --agent <you> --from-file /tmp/kb-<slug>.md
opc kb delete <slug> --agent <you> --confirm [--as-founder]
opc kb reindex
opc kb precedent --task-id TASK-001 --decision approve|reject --rationale "..." [--slug <s>] --as-founder   # founder-only; follows resolve-escalation
opc resolve-escalation --task-id TASK-001 --decision approve|reject --rationale "..."                       # founder state transition (precedes kb precedent)
opc revisit TASK-052 [--note "..."]                             # founder: spawn NEW root that inherits the predecessor's brief (TTY-gated)
# Talk flow (founder↔agent conversations):
opc talk start --agent <name>
opc talk resume --talk-id TALK-001
opc talk abandon --talk-id TALK-001 [--reason <why>]
opc talk end --talk-id TALK-001 --from-file /tmp/talk-end-TALK-001.json
opc talk status [--agent <name>]
opc talk list [--agent <name>] [--limit N]
opc talk show TALK-001
# Agent-side callbacks (invoked by skills):
opc report-completion --task-id TASK-001 --session-id <sid> --status completed ...
opc learning --agent dev_agent --session-id <sid> --task-id TASK-001 --text "..."
opc manage-repo add --agent dev_agent --repo-name docs --url https://github.com/t-benze/docs.git
opc manage-agent --from-file /tmp/manage-agent-enroll.json  # enroll/update/terminate an agent (task-path or talk-path auth)
opc dispatch --from-file /tmp/dispatch-<talk_id>.json   # agent: dispatch a new task from inside an open talk (workers self-only; team managers intra-team)
# Founder-side enrollment management:
opc enrollments [--status pending]     # list enrollment requests
opc approve-agent <name>               # approve and bootstrap workspace
opc reject-agent <name>                # reject enrollment
opc backfill-enrollments               # founder recovery: import pre-existing workspaces into the registry (TTY-gated)
opc migrate-to-org-runtime <path> --slug <slug> --i-have-a-backup --apply   # one-shot: lift legacy runtime into the org/ shape
```

## Knowledge Base

Shared precedents + domain reference live under `<runtime>/kb/`. Any agent can
read; any agent can write (via `opc kb add --from-file`); any team manager deletes (audited); founder overrides via `--as-founder`. Full rules: `protocol/06-knowledge-base.md`. The founder records
precedents via the two-command flow `opc resolve-escalation ...` (state
transition) followed by `opc kb precedent --as-founder ...` (KB write, founder-only
per spec §4.6).

The context builder injects a "Knowledge Base" section into every agent's
bootstrap document (`CLAUDE.md` for Claude, `AGENTS.md` for Codex). The
`start-task` skill has explicit **Consult KB** and **Contribute to KB** steps.

Also stock tech stack references: **knowledge base is now implemented**
(`src/infrastructure/kb_store.py` + `src/daemon/routes/kb.py`) — file-backed
markdown entries with atomic writes, a `kb_lock` in daemon state to serialize
writes, substring/tag search, and `_index.md` regeneration after every write.
No vector store yet.

## Revisit (founder recovery)

`opc revisit <task-id>` is a founder-initiated primitive that spawns a **new
root task** inheriting the brief + team of a terminal predecessor. The
existing lineage stays frozen (read-only history) — nothing in the old tree
is mutated. Design doc: `docs/superpowers/specs/2026-04-21-opc-revisit-design.md`.

Eligibility — predecessor root must be one of:
- `failed` (orchestrator gave up)
- `failed` + `cancelled_at != NULL` (founder-cancelled; normalized as
  `failed-cancelled` on the wire)
- `blocked(escalated)` (waiting on founder forever)
- `completed` (re-run an already-finished task, e.g. to retry against new code)

Anything else (`pending`, `in_progress`, `blocked(delegated)`) returns
**409 `cannot_revisit`** with the predecessor's current status.

Architecture — the predecessor ↔ new-root link lives in two places: a
first-class nullable `tasks.revisit_of_task_id` column (queryable, indexed
via `idx_tasks_revisit_of`) AND an `audit_log` entry that carries the
richer payload (`flagged`, `cascade`, `founder_note`, `prior_status`).
The column is a sideways reference — `walk_ancestors` MUST NOT follow
it, or cascade-fail will re-poison revisits via
`_enqueue_parent_if_waiting`. Two helpers read the edge:
`Database.walk_revisit_chain(task_id)` walks backward to the original;
`Database.get_direct_revisits(task_id)` returns immediate revisits.

Inside `state.db_lock` the endpoint atomically:
1. walks ancestors via `walk_ancestors(task_id, max_hops=20)` to find the root
2. inserts the new root `TaskRecord` (same `brief` + `type`, fresh `id`,
   `revisit_of_task_id=predecessor.id`)
3. logs `revisit_of` on the new root (payload: `predecessor_root`, `flagged`,
   `cascade`, `prior_status`, `founder_note`)
4. logs `revisit_spawned` on the predecessor root
5. enqueues the new root outside the lock

Historical revisits (created before the column existed) are backfilled on
daemon startup from the `revisit_of` audit entries; the UPDATE is guarded by
`IS NULL` so restarts are idempotent.

First EH step injection — on the new root's first `orchestration_step`,
`_revisit_header_if_applicable(orch, task_id)` prepends a 5-6 line context
header to the EH prompt. Detected by: `revisit_of` audit entry present AND
no `orchestration_step` entry yet. Header points the EH at
`opc details` / `opc audit` / `opc recall` for the frozen predecessor.

Why TTY-gated — no `--yes` bypass, no scripted callers. The CLI (`cmd_revisit`
in `src/cli.py`) hard-requires `sys.stdin.isatty() and sys.stdout.isatty()`
then prompts `Continue? [y/N]`. Agent sessions run headless (no TTY), so the
only way a revisit lands in the audit log is if a human typed it.

## Maintaining Documentation
- **README.md** is for end users of the system — only usage-related content (setup, CLI commands, configuration, agent workspaces). No developer internals, code style, directory layout, or implementation details.
- **CLAUDE.md** is for developers and AI agents working on the codebase — architecture, code patterns, directory layout, implementation order.

## When Starting a New Implementation Phase
1. Read the relevant design doc first (e.g., blueprint in `protocol/05c-orchestrator.md`)
2. Check existing code for patterns to follow — especially `src/orchestrator/` for the established patterns
3. Write tests alongside implementation
4. Keep agents' system prompts in sync with the markdown docs — the docs are the source of truth
