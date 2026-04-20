# Project: OPC Multi-Agent Tourism Organization

## What This Is
A one-person company (OPC) that provides online tourism information and booking services for foreign tourists visiting Hong Kong SAR and Macau SAR. The entire operation is run by AI agents supervised by a single human founder.

## Architecture Summary
- **Layer 1**: Founder (human) — sets rules, handles escalations, reviews weekly dashboard
- **Layer 2**: 4 Manager Agents — Content Manager, Engineering Head, Operations Manager, CX Manager
- **Layer 3**: 10 Worker Agents — Content Writer, SEO Agent, Content QA, Product Manager, Dev Agent, Payment Agent, QA Engineer, Partner Liaison, Compliance Agent, Support Agent
- **Infrastructure**: Audit Logger, Escalation Router, Knowledge Base

Agents operate autonomously within defined authority. Managers cross-audit each other (peer review). No agent both proposes and approves consequential actions (maker-checker pattern).

## Design Documents (read these first)

The following documents are in the `protocol/` folder.

- `01-org-charter.md` — Mission, brand voice, risk tolerance, budget caps, partner standards, compliance requirements across 3 jurisdictions
- `02-system-prompts-managers.md` — Full system prompts for all 4 manager agents with accountability contracts and performance tiers
- `03-system-prompts-workers.md` — Full system prompts for all 9 worker agents (incl. QA Engineer and Content QA) with accountability contracts and performance tiers
- `04-escalation-rules.md` — 12 routing rules (priority-ordered), manager-resolvable categories, peer audit triggers, structured request/response formats
- `05-team-blueprint.md` — Index pointing to the split blueprint documents:
  - `05a-teams.md` — Concept mapping, team definitions, agent tools, runtime responsibilities
  - `05b-agent-runtime.md` — Executor model, memory architecture, lifecycle & scheduling
  - `05c-orchestrator.md` — Orchestrator responsibilities, performance tiers, permissions, task state machine
  - `05d-feishu.md` — Founder interaction via Feishu, bot architecture, notification tiers
  - `05e-dashboard.md` — Dashboard layout, API endpoints, implementation order
- `06-knowledge-base.md` — Shared KB rules: entry schema, author/founder write paths, precedent workflow, search, index regeneration

## Tech Stack
- **Language**: Python 3.11+ (currently running 3.13)
- **Package manager**: `uv`
- **Agent executor**: Claude Code CLI (`claude -p "<prompt>" --permission-mode auto`) — no third-party agent framework dependency
- **Daemon**: FastAPI HTTP service (`src/daemon/`) — serves orchestrator work, SSE task events, agent callbacks
- **CLI**: Thin HTTP client (`src/client/`) that talks to the daemon over localhost
- **Agent workflow**: Claude Code skills (`protocol/skills/`) — `start-task`, `make-worktree`, `manage-repo`, `manage-agent`. The orchestrator prompt just names the skill and passes parameters
- **Orchestrator**: Custom Python application. `run_step` is the only primitive — each invocation advances one task by one subprocess call; an async `TaskQueue` + worker pool (`src/daemon/queue.py`) drives re-enqueues across steps. EH drives decisions; performance scoring derived from implicit review verdicts on delegated work
- **Data models**: Pydantic v2 + pydantic-settings
- **Database**: SQLite with WAL mode (audit logs, scorecards, task state)
- **Knowledge base**: File-backed markdown under `<runtime>/kb/` with atomic writes, substring/tag search, and regenerated `_index.md` (see `src/infrastructure/kb_store.py`). Vector store / RAG not yet added
- **LLM**: Anthropic Claude via Claude Code CLI
- **Hosting**: Local Mac Mini

## Implementation Order (follow this sequence)
1. ~~**Product & Engineering Team**~~ done — Engineering Head + Product Manager + Dev Agent + Payment Agent + QA Engineer with Claude Code executor. EH-driven orchestration loop (EH decides each step: delegate, handle directly, or escalate). Audit logging, agent memory, performance scoring all implemented.
2. ~~**Audit logging**~~ done — SQLite-backed audit logger with session start/end, completion reports, orchestration steps, escalations.
3. ~~**EH-driven orchestration**~~ done — Engineering Head analyzes each task and decides the approach. No hardcoded task chains. Max 10 orchestration steps before escalation.
4. ~~**Agent memory**~~ done — Persistent workspaces with CLAUDE.md, learnings.md, scorecard.md, task_history.md. Context builder regenerates identity on tier changes.
5. ~~**Performance scoring**~~ done — Rolling 30-day scorecards, green/yellow/red tiers, exposed to EH via capabilities prompt.
6. **Content Team** — Content Writer + Content QA + SEO Agent + Content Manager.
7. **Ops Team** — Partner Liaison + Compliance Agent + Operations Manager. Enables real cross-team audits for payment changes.
8. **Inter-Team communication** — Orchestrator routes tasks between Teams.
9. **CX Team** — Support Agent may run as persistent agent for real-time chat, not batch.
10. **Feishu integration** — Bot architecture, notification tiers, reply parsing.
11. **Founder dashboard** — Aggregate audit logs, escalation summaries, scorecards into weekly view.

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
|-- protocol/                          # Org charter, system prompts, escalation rules, blueprint, skills
|   |-- 01-org-charter.md
|   |-- 02-system-prompts-managers.md
|   |-- 03-system-prompts-workers.md
|   |-- 04-escalation-rules.md
|   |-- 05*.md
|   +-- skills/                        # Claude Code skills copied into every agent workspace
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
|   |       |-- tasks.py               # POST /tasks, GET /tasks, GET /tasks/{id}, SSE /tasks/{id}/events, GET /tasks/{id}/recall, POST /tasks/{id}/resolve-escalation, callbacks
|   |       |-- agents.py              # GET /agents, POST /agents/init (SSE), POST /agents/{name}/learnings, POST /agents/manage (enroll/update/terminate), GET /agents/enrollments, POST /agents/{name}/approve, POST /agents/{name}/reject, POST /agents/{name}/repos
|   |       |-- audit.py               # GET /audit — filtered audit-log view (task/agent/action/since/limit)
|   |       +-- kb.py                  # Knowledge base: GET /kb, /kb/{slug}, /kb/search; POST /kb, /kb/{slug}, /kb/reindex, /kb/precedent; DELETE /kb/{slug}
|   |-- orchestrator/
|   |   |-- orchestrator.py            # Orchestrator facade: holds deps, exposes run_step (no more run_task)
|   |   |-- run_step.py                # Single-step primitive — advance one task by one subprocess call
|   |   |-- capabilities.py            # Builds capabilities prompt for EH decision sessions
|   |   |-- executor.py                # Spawns `claude -p` subprocess with session_id
|   |   |-- performance_tracker.py     # 30-day rolling scorecards, tier calculation
|   |   |-- context_builder.py         # Generates CLAUDE.md + .claude/settings.json + copies skills
|   |   +-- prompt_loader.py           # Parses system prompts from protocol markdown
|   |-- infrastructure/
|   |   |-- database.py                # SQLite (WAL mode), typed CRUD, task_results.status column, agent_enrollments table, parent_task_id / note / final_artifact_dir / block_kind on tasks
|   |   |-- audit_logger.py            # Semantic logging (session, verdict, escalation, orchestration steps, escalation_resolved)
|   |   +-- kb_store.py                # Knowledge base: slug validation, atomic entry write, list/read/update/delete, search, _index.md regeneration, near-duplicate detection
|   |-- agents/                        # Agent definitions (future)
|   +-- tools/                         # Agent tools (future)
|-- tests/                             # ~390 tests (unit + a couple of integration)
|   |-- daemon/                        # Route-level tests for the FastAPI app
|   |-- integration/                   # End-to-end test with a fake Claude binary
|   +-- test_*.py                      # Orchestrator, executor, config, skills, etc.
+-- docs/superpowers/
    |-- specs/                         # Design specs
    +-- plans/                         # Implementation plans

~/.opc/                                # Daemon home (per-user)
|-- auth_token                         # Bearer token shared by daemon + CLI
+-- runtimes.yaml                      # Registered runtime dirs + which one is active

<runtime-dir>/                         # Created by `opc init <path>`
|-- opc.yaml                           # Marker file (presence = valid runtime folder)
|-- opc.db                             # SQLite database
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
|       |-- scorecard.md
|       +-- task_history.md            # Per-agent history (renamed from recent_tasks.md; legacy files auto-migrated)
+-- kb/                                # Shared knowledge base (see protocol/06-knowledge-base.md)
    |-- _index.md                      # Regenerated after every write
    +-- <slug>.md                      # Flat; filename = slug
```

## Configuration

Operational settings use the `OPC_` environment variable prefix. Runtime paths (database, workspaces) are derived from the runtime directory, not from env vars.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPC_CLAUDE_CLI_PATH` | `claude` | Path to Claude Code CLI |
| `OPC_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `OPC_PROTOCOL_DIR` | `protocol` | Protocol docs dirname (relative to project root) |
| `OPC_MAX_ORCHESTRATION_STEPS` | `10` | Max EH decision steps before escalation |
| `OPC_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout (30 min) |
| `OPC_TIER_GREEN_THRESHOLD` | `0.90` | Acceptance rate for green tier |
| `OPC_TIER_YELLOW_THRESHOLD` | `0.75` | Acceptance rate for yellow tier |

### Agent permission model

Each agent workspace's `.claude/settings.json` explicitly allows only `Bash(opc:*)` — the orchestrator's CLI. Rationale: `opc report-completion`, `opc learning`, and any future agent-facing subcommand are capabilities the orchestrator exposes to agents and must never be silently blocked by Claude Code's `auto`-mode prompting (a blocked callback manifests as a mystery `failed` task — see TASK-007 post-mortem). Everything else (Read/Grep/Glob, general Bash, Write) inherits Claude Code's default `auto` behavior. When adding new orchestrator-side capabilities, keep them under the `opc` binary so they stay inside this allow rule; do **not** widen the allow list to cover arbitrary tools.

Repos are configured per agent in `<runtime>/workspaces/<agent>/agent.yaml`:
```yaml
repos:
  web-app: https://github.com/t-benze/web-app.git
  docs: https://github.com/t-benze/docs.git
```

`opc init-agent` creates a default `agent.yaml` with empty repos if one doesn't exist.

### Agent permission model

Agents call the orchestrator's CLI (`opc report-completion`, `opc learning`, future callbacks) as their only sanctioned side-effect channel. The allow rule `Bash(opc:*)` lives in **two places** and both must stay in sync:

1. `.claude/settings.json` `permissions.allow` — written by `context_builder._build_settings_json`. Used by interactive (non-`-p`) sessions and surfaces intent to anyone inspecting the workspace.
2. `--allowedTools "Bash(opc *)"` on the CLI — passed by `AgentExecutor.run` for every headless session.

**Why both:** in headless `-p` mode, Claude Code 2.1.105 ignores the workspace's `permissions.allow` list (observed empirically: `command_permissions.allowedTools: []` regardless of settings.json). Without the `--allowedTools` flag the agent's first `opc ...` call is blocked by auto-mode prompting, the callback never reaches the daemon, and the task silently rejects — see the TASK-007/008/009 post-mortem. When adding new orchestrator-side capabilities, keep them under the `opc` binary so they stay inside this allow rule; do **not** widen either location to cover arbitrary tools.

**Agent-side completion payloads must be single-line `opc` invocations.** Claude Code's permission matcher treats newlines (and `&&`, `||`, `;`, `|`) as command separators and matches each subcommand independently. Multi-line bash with backslash continuations is rejected even though the surface command is `opc ...`. The `start-task` skill therefore mandates writing the payload to `/tmp/completion-<task_id>.json` and invoking `opc report-completion --from-file <path>` as a single line. Any new agent-facing callback with multiple arguments should follow the same `--from-file` pattern.

## Code Style
- Type hints on all function signatures
- Pydantic v2 models for structured data, StrEnum for enumerations (agent names are plain strings, not enums — agents are discovered dynamically from workspaces + enrollments DB)
- Tests for business logic (escalation rules, scoring, tier calculation)
- `from __future__ import annotations` in all source files

## Task status vocabularies

Note: agents self-report `status="completed"|"blocked"` via `opc report-completion`
(the worker's view of its session). The orchestrator-owned `TaskStatus` lives on
the `tasks` row and is distinct: it takes one of `{pending, in_progress,
blocked, completed, failed}` based on orchestration classification, with
`block_kind` (`delegated` | `escalated`) specifying the reason.

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

opc init /path/to/runtime                                       # register + activate a runtime dir
opc use /path/to/other-runtime                                  # switch the daemon's active runtime
opc run --brief "Explore the payment module"                    # submit a task; EH decides approach
opc run --task implement_feature --brief "Add Alipay support"   # with task type hint
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
# Knowledge base (read: any; write: any via --from-file; delete: engineering_head; precedent: founder):
opc kb list [--topic <t>] [--type reference|precedent]
opc kb get <slug>
opc kb search <query> [--limit N]
opc kb add --agent <you> --from-file /tmp/kb-<slug>.md
opc kb update <slug> --agent <you> --from-file /tmp/kb-<slug>.md
opc kb delete <slug> --agent <you> --confirm [--as-founder]
opc kb reindex
opc kb precedent --task-id TASK-001 --decision approve|reject --rationale "..." [--slug <s>] --as-founder   # founder-only; follows resolve-escalation
opc resolve-escalation --task-id TASK-001 --decision approve|reject --rationale "..."                       # founder state transition (precedes kb precedent)
# Agent-side callbacks (invoked by skills):
opc report-completion --task-id TASK-001 --session-id <sid> --status completed ...
opc learning --agent dev_agent --session-id <sid> --task-id TASK-001 --text "..."
opc manage-repo add --agent dev_agent --repo-name docs --url https://github.com/t-benze/docs.git
opc manage-agent --from-file /tmp/manage-agent-enroll.json  # enroll/update/terminate an agent
# Founder-side enrollment management:
opc enrollments [--status pending]     # list enrollment requests
opc approve-agent <name>               # approve and bootstrap workspace
opc reject-agent <name>                # reject enrollment
```

## Knowledge Base

Shared precedents + domain reference live under `<runtime>/kb/`. Any agent can
read; any agent can write (via `opc kb add --from-file`); only Engineering Head
deletes. Full rules: `protocol/06-knowledge-base.md`. The founder records
precedents via the two-command flow `opc resolve-escalation ...` (state
transition) followed by `opc kb precedent --as-founder ...` (KB write, founder-only
per spec §4.6).

The context builder injects a "Knowledge Base" section into every agent's
generated CLAUDE.md (see `context_builder._build_claude_md`). The `start-task`
skill has explicit **Consult KB** and **Contribute to KB** steps.

Also stock tech stack references: **knowledge base is now implemented**
(`src/infrastructure/kb_store.py` + `src/daemon/routes/kb.py`) — file-backed
markdown entries with atomic writes, a `kb_lock` in daemon state to serialize
writes, substring/tag search, and `_index.md` regeneration after every write.
No vector store yet.

## Maintaining Documentation
- **README.md** is for end users of the system — only usage-related content (setup, CLI commands, configuration, agent workspaces). No developer internals, code style, directory layout, or implementation details.
- **CLAUDE.md** is for developers and AI agents working on the codebase — architecture, code patterns, directory layout, implementation order.

## When Starting a New Implementation Phase
1. Read the relevant design doc first (e.g., blueprint in `protocol/05c-orchestrator.md`)
2. Check existing code for patterns to follow — especially `src/orchestrator/` for the established patterns
3. Write tests alongside implementation
4. Keep agents' system prompts in sync with the markdown docs — the docs are the source of truth
