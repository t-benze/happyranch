# Project: OPC Multi-Agent Tourism Organization

## What This Is
A one-person company (OPC) that provides online tourism information and booking services for foreign tourists visiting mainland China, Hong Kong SAR, and Macau SAR. The entire operation is run by AI agents supervised by a single human founder.

## Architecture Summary
- **Layer 1**: Founder (human) — sets rules, handles escalations, reviews weekly dashboard
- **Layer 2**: 4 Manager Agents — Content Manager, Engineering Head, Operations Manager, CX Manager
- **Layer 3**: 9 Worker Agents — Content Writer, SEO Agent, QA Agent, Product Manager, Dev Agent, Payment Agent, Partner Liaison, Compliance Agent, Support Agent
- **Infrastructure**: Audit Logger, Escalation Router, Knowledge Base

Agents operate autonomously within defined authority. Managers cross-audit each other (peer review). No agent both proposes and approves consequential actions (maker-checker pattern).

## Design Documents (read these first)

The following documents are in the `protocol/` folder.

- `01-org-charter.md` — Mission, brand voice, risk tolerance, budget caps, partner standards, compliance requirements across 3 jurisdictions
- `02-system-prompts-managers.md` — Full system prompts for all 4 manager agents with accountability contracts and performance tiers
- `03-system-prompts-workers.md` — Full system prompts for all 8 worker agents with accountability contracts and performance tiers
- `04-escalation-rules.md` — 12 routing rules (priority-ordered), manager-resolvable categories, peer audit triggers, structured request/response formats
- `05-crewai-blueprint.md` — Index pointing to the split blueprint documents:
  - `05a-crews.md` — Concept mapping, crew definitions, agent tools, CrewAI boundary
  - `05b-agent-runtime.md` — Executor model, memory architecture, lifecycle & scheduling
  - `05c-orchestrator.md` — Orchestrator responsibilities, performance tiers, permissions, task state machine
  - `05d-feishu.md` — Founder interaction via Feishu, bot architecture, notification tiers
  - `05e-dashboard.md` — Dashboard layout, API endpoints, implementation order

## Tech Stack
- **Language**: Python 3.11+ (currently running 3.13)
- **Package manager**: `uv`
- **Agent executor**: Claude Code CLI (`claude -p "<prompt>" --permission-mode auto`) — no CrewAI dependency for now
- **Daemon**: FastAPI HTTP service (`src/daemon/`) — serves orchestrator work, SSE task events, agent callbacks
- **CLI**: Thin HTTP client (`src/client/`) that talks to the daemon over localhost
- **Agent workflow**: Claude Code skills (`protocol/skills/start-task`, `protocol/skills/make-worktree`) — the orchestrator prompt just names the skill and passes parameters
- **Orchestrator**: Custom Python application (EH-driven orchestration loop, performance scoring)
- **Data models**: Pydantic v2 + pydantic-settings
- **Database**: SQLite with WAL mode (audit logs, scorecards, task state)
- **Knowledge base**: Vector store with RAG (planned — not yet implemented)
- **LLM**: Anthropic Claude via Claude Code CLI
- **Hosting**: Local Mac Mini

## Implementation Order (follow this sequence)
1. ~~**Product & Engineering Crew**~~ done — Engineering Head + Product Manager + Dev Agent + Payment Agent with Claude Code executor. EH-driven orchestration loop (EH decides each step: delegate, handle directly, or escalate). Audit logging, agent memory, performance scoring all implemented.
2. ~~**Audit logging**~~ done — SQLite-backed audit logger with session start/end, completion reports, orchestration steps, escalations.
3. ~~**EH-driven orchestration**~~ done — Engineering Head analyzes each task and decides the approach. No hardcoded task chains. Max 10 orchestration steps before escalation.
4. ~~**Agent memory**~~ done — Persistent workspaces with CLAUDE.md, learnings.md, scorecard.md, recent_tasks.md. Context builder regenerates identity on tier changes.
5. ~~**Performance scoring**~~ done — Rolling 30-day scorecards, green/yellow/red tiers, exposed to EH via capabilities prompt.
6. **Content Crew** — Content Writer + QA Agent + SEO Agent + Content Manager.
7. **Ops Crew** — Partner Liaison + Compliance Agent + Operations Manager. Enables real cross-crew audits for payment changes.
8. **Inter-Crew communication** — Orchestrator routes tasks between Crews.
9. **CX Crew** — Support Agent may run as persistent agent for real-time chat, not batch.
10. **Feishu integration** — Bot architecture, notification tiers, reply parsing.
11. **Founder dashboard** — Aggregate audit logs, escalation summaries, scorecards into weekly view.

## Key Constraints
- **Three jurisdictions**: Mainland China (PIPL, CSL, DSL), Hong Kong (PDPO), Macau (PDPA) — all must be complied with simultaneously
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
|       +-- make-worktree/             # Creates an isolated git worktree under .claude/worktrees/
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
|   |   |-- runner.py                  # TaskRunner — runs orchestrator in a thread, snapshots runtime/db
|   |   |-- sessions.py                # Active-session tracker (task_id,agent) -> session_id
|   |   |-- event_bus.py               # Per-task event pub/sub with DB replay + synthesized terminals
|   |   |-- agent_config.py            # Read/write workspaces/<agent>/agent.yaml
|   |   +-- routes/
|   |       |-- health.py              # GET /health
|   |       |-- runtimes.py            # POST /runtimes/init, POST /runtimes/use, GET /runtimes
|   |       |-- tasks.py               # POST /tasks, GET /tasks, GET /tasks/{id}, SSE /tasks/{id}/events, callbacks
|   |       |-- agents.py              # GET /agents, POST /agents/init (SSE), POST /agents/{name}/learnings
|   |       +-- audit.py               # GET /audit — filtered audit-log view (task/agent/action/since/limit)
|   |-- orchestrator/
|   |   |-- orchestrator.py            # EH-driven loop: ask Engineering Head, execute decisions
|   |   |-- capabilities.py            # Builds capabilities prompt for EH decision sessions
|   |   |-- executor.py                # Spawns `claude -p` subprocess with session_id
|   |   |-- performance_tracker.py     # 30-day rolling scorecards, tier calculation
|   |   |-- context_builder.py         # Generates CLAUDE.md + .claude/settings.json + copies skills
|   |   +-- prompt_loader.py           # Parses system prompts from protocol markdown
|   |-- infrastructure/
|   |   |-- database.py                # SQLite (WAL mode), typed CRUD, task_results.status column
|   |   +-- audit_logger.py            # Semantic logging (session, verdict, escalation, orchestration steps)
|   |-- agents/                        # Agent definitions (future)
|   |-- crews/                         # Crew definitions (future)
|   +-- tools/                         # Agent tools (future)
|-- tests/                             # 211 tests (210 unit + 1 integration)
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
+-- workspaces/
    |-- engineering_head/
    |   |-- agent.yaml                 # Per-agent config (repos, etc.)
    |   |-- CLAUDE.md                  # Generated from protocol/02-system-prompts-managers.md
    |   |-- .claude/
    |   |   |-- settings.json          # Permissions + PreToolUse hook (git pull all repos)
    |   |   +-- skills/                # start-task + make-worktree copied from protocol/skills/
    |   |-- repos/                     # Git clones declared in agent.yaml
    |   |   +-- <name>/                # One dir per entry in agent.yaml `repos:`
    |   |-- learnings.md
    |   |-- scorecard.md
    |   +-- recent_tasks.md
    |-- product_manager/
    |   |-- ...
    |   +-- specs/
    |-- dev_agent/
    |   +-- ...
    +-- payment_agent/
        |-- ...
        +-- proposals/
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
- Pydantic v2 models for structured data, StrEnum for enumerations
- Tests for business logic (escalation rules, scoring, tier calculation)
- `from __future__ import annotations` in all source files

## Running Tests
```bash
uv run pytest tests/ -v
```

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
opc status TASK-001          # show task details
opc agents [--detail]        # show performance tiers
opc audit TASK-007                               # filtered audit-log view (task, agent, action, since, limit)
opc audit --agent engineering_head --limit 10    # recent entries for one agent, any task
opc audit TASK-007 --json                        # raw JSON with full payloads
opc init-agent               # initialize all agent workspaces (repo clones + system prompts + skills)
opc init-agent dev_agent     # initialize a specific agent
# Agent-side callbacks (invoked by the start-task skill):
opc report-completion --task-id TASK-001 --session-id <sid> --status completed ...
opc learning --agent dev_agent --session-id <sid> --task-id TASK-001 --text "..."
```

## Maintaining Documentation
- **README.md** is for end users of the system — only usage-related content (setup, CLI commands, configuration, agent workspaces). No developer internals, code style, directory layout, or implementation details.
- **CLAUDE.md** is for developers and AI agents working on the codebase — architecture, code patterns, directory layout, implementation order.

## When Starting a New Implementation Phase
1. Read the relevant design doc first (e.g., blueprint in `protocol/05c-orchestrator.md`)
2. Check existing code for patterns to follow — especially `src/orchestrator/` for the established patterns
3. Write tests alongside implementation
4. Keep agents' system prompts in sync with the markdown docs — the docs are the source of truth
