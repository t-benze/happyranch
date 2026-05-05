# Project: OPC — Multi-Agent Org Runtime

## What This Is
OPC is an **org-agnostic runtime** for operating a multi-agent organization supervised by a single human founder. The repo provides the system kernel (orchestrator, daemon + CLI, audit, scoring, KB, talk, revisit, escalation primitives); the *organization* it runs — charter, teams, agents, escalation rules, jurisdictions, budget authority — is loaded per-runtime from `<runtime>/orgs/<slug>/org/`.

A canonical sample org shipped at `examples/orgs/hk-macau-tourism/` runs a one-person tourism company serving foreign visitors to Hong Kong SAR and Macau SAR. Treat it as the reference shape when bootstrapping a new org; nothing about its specific teams, agents, or constraints is baked into the system.

## Architecture Summary
- **Layer 1**: Founder (human) — sets org rules, handles escalations, reviews weekly dashboard
- **Layer 2**: Manager agents — defined per-org in `<runtime>/orgs/<slug>/org/agents/<name>.md` with `role: manager`. Each manager owns one team listed in `teams.yaml`.
- **Layer 3**: Worker agents — same file shape, `role: worker`. Workers are assigned to a team via `teams.yaml`.
- **Infrastructure (org-agnostic, lives in this repo)**: orchestrator, FastAPI daemon + `opc` CLI, audit logger, performance scoring, knowledge base, talk store, revisit primitive, escalation routing.

Agents operate autonomously within authority defined by their org. The system enforces structural patterns regardless of org: managers cross-audit each other (peer review), and no agent both proposes and approves consequential actions (maker-checker pattern). Org-specific authority (e.g., budget thresholds, refund limits) lives in `escalation-rules.md` and the agents' system prompts.

A single runtime container (`<runtime>/`) hosts **multiple orgs** under `<runtime>/orgs/<slug>/`. Each org has its own `org/` content, SQLite DB, workspaces, KB, and talks. One daemon serves all orgs concurrently. Bootstrap: `opc init <runtime>` creates the empty container; `opc orgs init <slug> --from examples/orgs/hk-macau-tourism` materializes an org from the sample tree.

## Design Documents (read these first)

In the `protocol/` folder:

- `00-completion-contract.md` — Universal completion-report format, manager decision schema, agent-callback list
- `05-runtime-blueprint.md` — Index pointing to:
  - `05b-agent-runtime.md` — Executor model, memory architecture, lifecycle & scheduling
  - `05c-orchestrator.md` — Orchestrator responsibilities, performance tiers, permissions, task state machine
  - `05e-dashboard.md` — Dashboard layout, API endpoints, implementation order
- `06-knowledge-base.md` — Shared KB rules

`05c-orchestrator.md` and `05e-dashboard.md` are org-agnostic — they reference "team manager" / "team alpha" as placeholders. Org-specific charter, teams, and agent prompts live in `<runtime>/orgs/<slug>/org/`.

## Tech Stack
- **Language**: Python 3.11+ (currently running 3.13)
- **Package manager**: `uv`
- **Agent executor**: Per-agent. Claude Code (`claude -p ... --permission-mode auto`), Codex (`codex exec --json -`), and opencode (`opencode run`) are supported — no third-party agent framework dependency
- **Daemon**: FastAPI HTTP service (`src/daemon/`) — serves orchestrator work, SSE task events, agent callbacks
- **CLI**: Thin HTTP client (`src/client/`) that talks to the daemon over localhost
- **Agent workflow**: Shared workspace skills (`protocol/skills/`) — `start-task`, `make-worktree`, `manage-repo`, `manage-agent`. The orchestrator prompt references the same SOPs across all executors
- **Orchestrator**: Custom Python application. `run_step` is the only primitive — each invocation advances one task by one subprocess call; an async `TaskQueue` + worker pool (`src/daemon/queue.py`) drives re-enqueues across steps. The team manager drives decisions; performance scoring derives from implicit review verdicts on delegated work
- **Data models**: Pydantic v2 + pydantic-settings
- **Database**: SQLite with WAL mode (audit logs, scorecards, task state) — per-org under `<runtime>/orgs/<slug>/opc.db`
- **Knowledge base**: File-backed markdown under `<runtime>/orgs/<slug>/kb/` with atomic writes, substring/tag search, `_index.md` regeneration. No vector store yet
- **LLM**: Provider depends on the selected executor
- **Hosting**: Local Mac Mini

## Implementation Order (system features)

System kernel milestones — the org-agnostic infrastructure. Building out a specific org's agent roster is *org content work*, not system work, and lives in `<runtime>/orgs/<slug>/org/`.

1. ~~**Bootstrap orchestrator + first team**~~ done — orchestrator with executor-backed agent sessions, manager-driven decision loop. Validated end-to-end against the sample org's engineering team.
2. ~~**Audit logging**~~ done — SQLite-backed audit logger.
3. ~~**Manager-driven orchestration**~~ done — the team manager analyzes each task and decides the approach. No hardcoded task chains. `OPC_MAX_ORCHESTRATION_STEPS` (default 50) before escalation.
4. ~~**Agent memory**~~ done — persistent workspaces with executor-specific bootstrap docs (`CLAUDE.md` or `AGENTS.md`), `learnings.md`, `task_history.md`. Context builder regenerates identity on tier changes.
5. ~~**Performance scoring**~~ done — rolling 30-day scorecards, green/yellow/red tiers, exposed to managers via capabilities prompt.
6. ~~**Talk flow**~~ done — founder↔agent conversations with SQLite-tracked talks, transcripts under `<runtime>/orgs/<slug>/talks/`, end-of-talk learnings + KB entries.
7. ~~**Knowledge Base**~~ done — per-org precedents + reference under `<runtime>/orgs/<slug>/kb/`.
8. ~~**Revisit primitive**~~ done — founder spawns a new root task that inherits a terminal predecessor's brief while leaving the old lineage frozen.
9. ~~**Org-per-runtime layout**~~ done — file-backed `org/{charter.md,escalation-rules.md,teams.yaml,config.yaml,agents/}`, with `opc migrate-to-org-runtime` for legacy DB-backed agents.
10. ~~**Multi-org container**~~ done — one runtime hosts multiple orgs under `<runtime>/orgs/<slug>/`. Per-org DB, workspaces, KB, talks. `opc migrate-to-multi-org` for in-place v1 → v2 migration.
11. **Inter-team communication** — orchestrator routes tasks between teams (e.g., engineering manager hands a payment-change review to a compliance team manager). Currently `--team <name>` works because most runtimes have a single team; cross-team handoff is not yet implemented.
12. **Founder dashboard** — aggregate audit logs, escalation summaries, scorecards into a weekly view. Design doc: `protocol/05e-dashboard.md`.
13. **Persistent agents** — long-running agent loops for runtime patterns that don't fit single-task batch execution (e.g., a real-time customer-chat worker). Currently every agent session is one task → one subprocess.

## Directory Layout

Source code lives in the repo. Runtime data lives in a dedicated **runtime container** created with `opc init`.

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
|       |-- make-worktree/             # Creates an isolated git worktree
|       |-- manage-repo/               # Agent-driven repo add/remove/update via opc manage-repo
|       +-- manage-agent/              # Manager-driven agent enroll/update/terminate via opc manage-agent
|-- scripts/
|   +-- daemon.sh                      # Starts the FastAPI daemon (uv run python -m src.daemon)
|-- src/
|   |-- cli.py                         # Unified CLI entry point (`opc` command) — HTTP client
|   |-- config.py                      # Settings (OPC_ env prefix, operational thresholds)
|   |-- runtime.py                     # RuntimeDir — self-describing runtime folder (opc.yaml marker)
|   |-- models.py                      # Pydantic models + StrEnums
|   |-- client/client.py               # httpx-based client for the daemon (+ SSE streaming)
|   |-- daemon/                        # FastAPI HTTP daemon
|   |   |-- __main__.py                # Uvicorn entry (python -m src.daemon)
|   |   |-- app.py                     # FastAPI app factory, lifespan, DaemonState wiring
|   |   |-- state.py                   # DaemonState (db, runtime, settings, sessions, event bus)
|   |   |-- auth.py                    # Bearer-token dependency (~/.opc/auth_token)
|   |   |-- paths.py                   # ~/.opc/ home paths (auth token, runtimes.yaml)
|   |   |-- runtimes.py                # Runtime registry (runtimes.yaml, set/get active)
|   |   |-- runner.py                  # enqueue_task() — pushes a task_id onto state.queue
|   |   |-- queue.py                   # Async TaskQueue + worker pool
|   |   |-- sessions.py                # Active-session tracker (task_id, agent) -> session_id
|   |   |-- event_bus.py               # Per-task event pub/sub with DB replay + synthesized terminals
|   |   |-- agent_config.py            # Read/write workspaces/<agent>/agent.yaml
|   |   |-- migration_multi_org.py     # opc migrate-to-multi-org — v1 → v2
|   |   +-- routes/                    # health, runtimes, tasks, agents, audit, kb, talks
|   |-- orchestrator/
|   |   |-- orchestrator.py            # Orchestrator facade: holds deps, exposes run_step
|   |   |-- run_step.py                # Single-step primitive
|   |   |-- capabilities.py            # Builds capabilities prompt for manager decision sessions
|   |   |-- executors.py               # Provider-specific executor subprocess launchers
|   |   |-- performance_tracker.py     # 30-day rolling scorecards, tier calculation
|   |   |-- context_builder.py         # Delegates workspace bootstrap to provider-specific adapters
|   |   |-- workspace_adapters.py      # Generates CLAUDE.md or AGENTS.md, settings, copies skills
|   |   |-- agent_def.py               # AgentDef dataclass + frontmatter parser/renderer
|   |   |-- prompt_loader.py           # File-based agent loader (active + _pending under org/agents/)
|   |   |-- teams.py                   # TeamsRegistry — seeded from teams.yaml
|   |   |-- org_config.py              # OrgConfig — loads optional org/config.yaml
|   |   +-- migration.py               # opc migrate-to-org-runtime — v0 (DB) → v1 (file-based)
|   +-- infrastructure/
|       |-- database.py                # SQLite (WAL), typed CRUD, task_results, parent_task_id, revisit_of_task_id, etc.
|       |-- audit_logger.py            # Semantic logging
|       |-- kb_store.py                # Knowledge base store
|       +-- talk_store.py              # Transcript file writer
|-- tests/                             # Unit + integration (with fake CLIs)
+-- examples/orgs/                     # Canonical sample org trees
    +-- hk-macau-tourism/

~/.opc/                                # Daemon home (per-user)
|-- auth_token                         # Bearer token shared by daemon + CLI
+-- runtimes.yaml                      # Registered runtime dirs + which one is active

<runtime-dir>/                         # Created by `opc init <path>` (slugless container)
|-- opc.yaml                           # container marker (schema_version: 2, type: multi-org-runtime)
+-- orgs/
    +-- <slug>/                        # Created by `opc orgs init <slug> [--from <example-tree>]`
        |-- opc.db                     # per-org SQLite
        |-- org/                       # editable org content
        |   |-- charter.md             # reference doc
        |   |-- escalation-rules.md    # reference doc
        |   |-- teams.yaml             # team layout
        |   |-- config.yaml            # optional org overrides (e.g. session_timeout_seconds)
        |   +-- agents/
        |       |-- <name>.md          # active agents
        |       +-- _pending/<name>.md # awaiting founder approval
        |-- workspaces/
        |   +-- <agent_name>/          # One per agent (created by init-agent or approve-agent)
        |       |-- agent.yaml         # Per-agent config (executor, repos, ...)
        |       |-- CLAUDE.md          # or AGENTS.md, depending on executor
        |       |-- .claude/           # (Claude only) settings.json + skills/
        |       |-- .agents/skills/    # (Codex/opencode) shared skills tree
        |       |-- opencode.json      # (opencode only) permission file
        |       |-- repos/<name>/      # Git clones declared in agent.yaml
        |       |-- learnings.md
        |       +-- task_history.md
        |-- kb/
        |   |-- _index.md              # Regenerated after every write
        |   +-- <slug>.md              # Flat; filename = slug
        +-- talks/
            +-- TALK-NNN.md
```

A single daemon serves every org in the container concurrently. Per-org HTTP routes live under `/api/v1/orgs/<slug>/...`; container-level routes under `/api/v1/runtime` and `/api/v1/orgs`.

Legacy v1 runtimes (single-org, flat `<runtime>/{org,workspaces,kb,talks}/`) migrate in place via `opc migrate-to-multi-org <path> --i-have-a-backup --apply` — TTY-gated, refuses if active tasks or open talks exist. Even older v0 (DB-backed agent enrollments) migrates first via `opc migrate-to-org-runtime`.

## Configuration

Operational settings use the `OPC_` env prefix. Runtime paths are derived from the runtime directory.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPC_CLAUDE_CLI_PATH` | `claude` | Path to Claude Code CLI |
| `OPC_CODEX_CLI_PATH` | `codex` | Path to Codex CLI |
| `OPC_OPENCODE_CLI_PATH` | `opencode` | Path to opencode CLI |
| `OPC_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `OPC_PROTOCOL_DIR` | `protocol` | Protocol docs dirname (relative to project root) |
| `OPC_MAX_ORCHESTRATION_STEPS` | `50` | Max manager decision steps before escalation |
| `OPC_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout — global default; see resolution below |
| `OPC_TIER_GREEN_THRESHOLD` | `0.90` | Acceptance rate for green tier |
| `OPC_TIER_YELLOW_THRESHOLD` | `0.75` | Acceptance rate for yellow tier |
| `OPC_ORG_SLUG` | _(unset)_ | Default org slug for per-org CLI commands. Resolution: explicit `--org` flag > `OPC_ORG_SLUG` env > auto-infer (only if exactly one org exists) > error |

### Session timeout resolution

`Orchestrator._resolve_session_timeout(agent_name, task_id=...)` walks three layers, highest precedence first:

1. **Task override** — optional `tasks.session_timeout_seconds` column, set by the founder via `opc revisit ... --session-timeout-seconds <int>` and inherited by every child the orchestrator spawns from that task (delegate children, auto-revisits, founder-revisits when the flag is omitted).
2. **Org override** — optional `session_timeout_seconds: <int>` in `<runtime>/orgs/<slug>/org/config.yaml`. Loaded via `src/orchestrator/org_config.py`. Unknown keys ignored for forward compat.
3. **Code default** — `Settings.session_timeout_seconds` (1800s), itself overridable via `OPC_SESSION_TIMEOUT_SECONDS`.

Each layer accepts `null`/missing as "inherit from next." Values must be positive integers; non-int or `<= 0` raises at parse time.

The `agent_name` argument is accepted for symmetry with older call sites but is unused — there is no per-agent layer. Legacy `session_timeout_seconds` in agent frontmatter is silently ignored.

### Agent executors

Each workspace declares an `executor` in `agent.yaml`: `claude`, `codex`, or `opencode`. Missing values default to `claude`.

All three share the same `protocol/skills/` tree. Workspace differences:

| | bootstrap doc | skills dir | permission surface |
|--|--|--|--|
| Claude | `CLAUDE.md` | `.claude/skills/` | `permissions.allow` in `.claude/settings.json` **AND** `--allowedTools` on CLI (both required, see below) |
| Codex | `AGENTS.md` | `.agents/skills/` | sandbox flags on CLI |
| opencode | `AGENTS.md` | `.agents/skills/` | `opencode.json` `permission.bash` map |

**Codex sandbox**: `CodexExecutor.run` passes `-c sandbox_workspace_write.network_access=true` on every invocation. The `workspace-write` sandbox blocks localhost by default, which would kill the agent's `opc report-completion` callback to `127.0.0.1`. Do not remove this flag without re-architecting the callback path away from localhost sockets.

**opencode permissions**: `OpencodeWorkspaceAdapter.write_opencode_json` writes a strict default — `{"permission": {"bash": {"*": "deny", "opc *": "allow", ...per-agent allow_rules...}}}`. **Do not pass `--dangerously-skip-permissions` on the CLI** — it bypasses `opencode.json` and erases the per-prefix discipline.

Enrolling a non-Claude worker: set `"executor": "codex"` (or `"opencode"`) in the `opc manage-agent --from-file` payload. Founder approval (`opc approve-agent`) bootstraps the right surface for the chosen executor. See `protocol/skills/manage-agent/SKILL.md` for full payload shapes.

Repos are configured per agent in `agent.yaml`:
```yaml
repos:
  web-app: https://github.com/t-benze/web-app.git
  docs: https://github.com/t-benze/docs.git
```
`opc init-agent` creates a default `agent.yaml` with empty repos if missing.

### Agent permission model

Agents call the orchestrator's CLI (`opc report-completion`, `opc learning`, `opc manage-repo`, `opc manage-agent`, `opc dispatch`, ...) as their only sanctioned side-effect channel. **Baseline allow rule for every agent: `opc`.**

Per-agent extras are declared in agent frontmatter (`<runtime>/orgs/<slug>/org/agents/<name>.md`) under `allow_rules:`. Example: the sample org's `engineering_head` declares `gh pr close`, `gh pr comment`, `gh issue close`, `gh issue comment` — needed because Claude's headless risk heuristic refuses those calls otherwise even in `--permission-mode auto`. Keep extras narrow: each prefix can silently mutate shared external state on every future task.

**For Claude specifically**, allow rules must land in two places kept in sync:

1. `.claude/settings.json` `permissions.allow` — written by `ClaudeWorkspaceAdapter.write_settings_json` (used by interactive sessions; surfaces intent).
2. `--allowedTools` on the CLI — passed by `ClaudeExecutor.run` for headless sessions.

Both surfaces are generated from `allow_rules_for_agent(agent_name, cli=...)` in `src/orchestrator/workspace_adapters.py` (settings uses `Bash(<cmd>:*)`; CLI uses `Bash(<cmd> *)`). **Do not hand-edit either** — `opc init-agent` rewrites them. The two-surface requirement exists because Claude Code 2.1.x ignores `permissions.allow` in headless `-p` mode; without the CLI flag, the agent's first `opc ...` call is blocked and the task silently rejects.

**When adding new orchestrator capabilities, keep them under the `opc` binary** so they stay inside the baseline allow rule. Only add a raw-tool prefix when the operation genuinely cannot be wrapped in `opc` (e.g., third-party CLI for external infra we don't own).

**Agent-side completion payloads must be single-line `opc` invocations.** The Claude permission matcher treats newlines (and `&&`, `||`, `;`, `|`) as command separators and matches each subcommand independently; multi-line bash with backslash continuations is rejected even when the surface command is `opc ...`. The `start-task` skill writes payloads to `/tmp/completion-<task_id>.json` and invokes `opc report-completion --from-file <path>` as a single line. Any new agent-facing callback with multiple arguments must follow the same `--from-file` pattern.

## Code Style
- Type hints on all function signatures
- `from __future__ import annotations` in all source files
- Pydantic v2 models for structured data, StrEnum for enumerations (agent names are plain strings — agents are discovered dynamically from `<runtime>/orgs/<slug>/org/agents/*.md`)
- Tests for business logic (escalation rules, scoring, tier calculation)

## Org content APIs

`AgentDef` (`src/orchestrator/agent_def.py`) is the in-memory representation of an agent file: markdown-with-YAML-frontmatter, parsed/rendered by `parse_agent_text` / `render_agent_text`. Fields: `name`, `team`, `role` (worker|manager), `executor` (claude|codex|opencode), `description`, `allow_rules`, `repos`, `enrolled_by`, `enrolled_at_task`, `enrolled_at`, `system_prompt` (body). **No `session_timeout_seconds` field** — see resolution above.

`src/orchestrator/prompt_loader.py` is the only API for reading/writing agent files: `load_agent`, `list_agents`, `list_pending`, `write_pending_agent`, `approve_agent`, `reject_agent`. Routes (`src/daemon/routes/agents.py`) and the orchestrator all read through this module against the per-org root. **Do NOT reach into the legacy `agent_enrollments` SQLite table** for new code paths — it remains in the schema for backward compat with v0 runtimes only.

`TeamsRegistry` (`src/orchestrator/teams.py`) is seeded from `teams.yaml` and auto-persists on `add_worker` / `remove_worker`. There is no `DEFAULT_LAYOUT` — an org without `teams.yaml` is treated as empty.

## Task status vocabularies

Agents self-report `status="completed"|"blocked"` via `opc report-completion` (the worker's view of its session). The orchestrator-owned `TaskStatus` lives on the `tasks` row and is distinct: `{pending, in_progress, blocked, completed, failed}` based on orchestration classification, with `block_kind` (`delegated` | `escalated`) specifying the reason.

## Manager decision contract

Team-manager completion payloads carry two fields with distinct purposes:

- **`summary`** (prose) — human-readable description of what the manager did or concluded this step. Rendered in `opc details`, audit logs, `task_history.md`. Stored on `task_results.output_summary`.
- **`decision`** (JSON object, NextStep schema) — the structured action the orchestrator will execute: `{"action": "delegate"|"done"|"escalate", ...}`. Stored on `task_results.decision_json` (manager-only column; workers leave NULL). Parsed by `Orchestrator._parse_next_step` directly — no prose inference.

Full schema with worked examples lives in `protocol/00-completion-contract.md` ("Manager decision field"). The decision-field name for a delegated child task's brief is **`prompt`, not `brief`** — Pydantic v2 silently ignores extras, so writing `"brief"` produces an empty-brief child task.

## Running Tests
```bash
uv run pytest tests/ -v                  # unit tests only (default)
uv run pytest tests/ -v -m integration   # end-to-end tests (spawns a real daemon + fake executor binaries)
uv run pytest tests/ -v -m ""            # both
```

Integration tests are excluded by default because they spawn a real daemon and fake CLIs. They are isolated from `~/.opc/` via `OPC_DAEMON_HOME`. **Run them locally before any change touching the daemon lifespan, SessionTracker, or callback routes** — that's the surface area where unit tests have historically missed regressions. CI runs them on every PR.

## Running the Daemon + CLI

The CLI is an HTTP client. Start the daemon once, then run CLI commands.

In a multi-org container, every per-org command takes `--org <slug>`. Slug resolution: explicit `--org` flag > `OPC_ORG_SLUG` env > auto-infer (only if exactly one org exists) > error. Shell `export OPC_ORG_SLUG=<slug>` is the usual ergonomic shortcut. Container-level commands (`opc init`, `opc use`, `opc orgs ...`, `opc migrate-to-multi-org`) take no `--org`.

```bash
scripts/daemon.sh start                                         # start daemon in background
scripts/daemon.sh status                                        # or stop

# Container-level
opc init /path/to/runtime                                       # create + register + activate a container (slugless)
opc use /path/to/other-runtime                                  # switch the active container
opc orgs                                                        # list orgs in active container
opc orgs init <slug> [--from <example-tree>]                    # materialize a new org
opc orgs unload <slug>                                          # detach an org from the daemon (does not delete files)
opc migrate-to-multi-org <path> --i-have-a-backup --apply       # v1 -> v2 in place (TTY-gated)

# Per-org (every command takes --org <slug> or honors OPC_ORG_SLUG)
opc run --org <slug> --brief "Explore the payment module"
opc run --org <slug> --team engineering --brief "Add Alipay support"
opc run --org <slug> --team engineering --brief-file /tmp/manager-brief.md
opc tail --org <slug> TASK-001                              # stream live SSE events for a task
opc tasks --org <slug>                                      # list recent tasks
opc details --org <slug> TASK-001 [--full]                  # task details (--full skips 80-char truncation)
opc agents --org <slug> [--detail]                          # show performance tiers
opc audit --org <slug> TASK-007 [--agent X --action Y --since T --limit N --json]
opc init-agent --org <slug> [<agent>]                       # initialize all (or one) agent workspaces
opc recall --org <slug> TASK-001 [--tree] [--fetch-artifact <relpath>]

# Knowledge base (read: any; write: any via --from-file; delete: team manager (audited) or founder via --as-founder)
opc kb list --org <slug> [--topic <t>] [--type reference|precedent]
opc kb get --org <slug> <slug>
opc kb search --org <slug> <query> [--limit N]
opc kb add --org <slug> --agent <you> --from-file /tmp/kb-<slug>.md
opc kb update --org <slug> <slug> --agent <you> --from-file /tmp/kb-<slug>.md
opc kb delete --org <slug> <slug> --agent <you> --confirm [--as-founder]
opc kb reindex --org <slug>
opc kb precedent --org <slug> --task-id TASK-001 --decision approve|reject --rationale "..." [--slug <s>] --as-founder

# Founder primitives
opc resolve-escalation --org <slug> --task-id TASK-001 --decision approve|reject --rationale "..."
opc revisit --org <slug> TASK-052 [--note "..." | --note-file PATH] [--session-timeout-seconds N]   # TTY-gated

# Talks (founder<->agent conversations; per-org)
opc talk start  --org <slug> --agent <name>
opc talk resume --org <slug> --talk-id TALK-001
opc talk abandon --org <slug> --talk-id TALK-001 [--reason <why>]
opc talk end    --org <slug> --talk-id TALK-001 --from-file /tmp/talk-end-TALK-001.json
opc talk status --org <slug> [--agent <name>]
opc talk list   --org <slug> [--agent <name>] [--limit N]
opc talk show   --org <slug> TALK-001

# Agent-side callbacks (invoked by skills; --org is mandatory, never auto-inferred)
opc report-completion --org <slug> --task-id TASK-001 --session-id <sid> --status completed ...
opc learning          --org <slug> --agent dev_agent --session-id <sid> --task-id TASK-001 --text "..."
opc manage-repo       --org <slug> add --agent dev_agent --repo-name docs --url https://...
opc manage-agent      --org <slug> --from-file /tmp/manage-agent-enroll.json
opc dispatch          --org <slug> --from-file /tmp/dispatch-<talk_id>.json

# Founder-side enrollment management
opc enrollments         --org <slug> [--status pending]
opc approve-agent       --org <slug> <name>
opc reject-agent        --org <slug> <name>
opc backfill-enrollments --org <slug>                       # founder recovery; TTY-gated
opc migrate-to-org-runtime <path> --slug <slug> --i-have-a-backup --apply   # legacy v0 -> v1
```

## Knowledge Base

Per-org under `<runtime>/orgs/<slug>/kb/` (orgs do not share a KB). Any agent reads/writes; team managers delete (audited); founder overrides via `--as-founder`. Full rules: `protocol/06-knowledge-base.md`.

The founder records precedents via the two-command flow: `opc resolve-escalation ...` (state transition) followed by `opc kb precedent --as-founder ...` (KB write, founder-only).

The context builder injects a "Knowledge Base" section into every agent's bootstrap document. The `start-task` skill has explicit **Consult KB** and **Contribute to KB** steps.

Implementation: `src/infrastructure/kb_store.py` + `src/daemon/routes/kb.py` — file-backed entries, atomic writes, `kb_lock` in daemon state to serialize writes, substring/tag search, `_index.md` regeneration.

## Revisit (founder recovery)

`opc revisit <task-id>` spawns a **new root task** inheriting the brief + team of a terminal predecessor. The existing lineage stays frozen — nothing in the old tree is mutated. Design doc: `docs/superpowers/specs/2026-04-21-opc-revisit-design.md`.

Eligibility — predecessor root must be one of:
- `failed` (orchestrator gave up)
- `failed` + `cancelled_at != NULL` (founder-cancelled; normalized as `failed-cancelled` on the wire)
- `blocked(escalated)` (waiting on founder forever)
- `completed` (re-run an already-finished task, e.g. to retry against new code)

Anything else returns **409 `cannot_revisit`** with the predecessor's current status.

The predecessor <-> new-root link lives in two places: a nullable `tasks.revisit_of_task_id` column (queryable, indexed) AND an `audit_log` entry carrying the richer payload (`flagged`, `cascade`, `founder_note`, `prior_status`). The column is a sideways reference — `walk_ancestors` MUST NOT follow it, or cascade-fail will re-poison revisits via `_enqueue_parent_if_waiting`. Helpers: `Database.walk_revisit_chain(task_id)` (backward) and `Database.get_direct_revisits(task_id)` (forward).

On the new root's first orchestration step, `_revisit_header_if_applicable(orch, task_id)` prepends a 5-6 line context header pointing the manager at `opc details` / `opc audit` / `opc recall` for the frozen predecessor.

`opc revisit` is **TTY-gated** — no `--yes` bypass. The CLI hard-requires `sys.stdin.isatty() and sys.stdout.isatty()` then prompts `Continue? [y/N]`. Agent sessions run headless, so the only way a revisit lands in the audit log is if a human typed it.

`run_step` also auto-revisits on opaque-failure recovery; the task-row `session_timeout_seconds` is copied onto every spawned revisit root (founder or auto).

## Maintaining Documentation
- **README.md** is for end users — setup, CLI commands, configuration. No developer internals.
- **CLAUDE.md** is for developers and AI agents working on the codebase — architecture, code patterns, directory layout, implementation order.

## When Starting a New Implementation Phase
1. Read the relevant design doc first (e.g., `protocol/05c-orchestrator.md`)
2. Check existing code for patterns to follow — especially `src/orchestrator/`
3. Write tests alongside implementation
4. Keep agents' system prompts in sync with the markdown docs — the docs are the source of truth
