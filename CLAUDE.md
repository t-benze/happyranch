# Project: Grassland — Multi-Agent Org Runtime

## What This Is
Grassland is an **org-agnostic runtime** for operating a multi-agent organization supervised by a single human founder. The repo provides the system kernel (orchestrator, daemon + CLI, audit, KB, talk, revisit, escalation primitives); the *organization* it runs — charter, teams, agents, escalation rules, jurisdictions, budget authority — is loaded per-runtime from `<runtime>/orgs/<slug>/org/`.

A canonical sample org shipped at `examples/orgs/hk-macau-tourism/` runs a one-person tourism company serving foreign visitors to Hong Kong SAR and Macau SAR. Treat it as the reference shape when bootstrapping a new org; nothing about its specific teams, agents, or constraints is baked into the system.

## Architecture Summary
- **Layer 1**: Founder (human) — sets org rules, handles escalations, reviews weekly dashboard
- **Layer 2**: Manager agents — defined per-org in `<runtime>/orgs/<slug>/org/agents/<name>.md` with `role: manager`. Each manager owns one team listed in `teams.yaml`.
- **Layer 3**: Worker agents — same file shape, `role: worker`. Workers are assigned to a team via `teams.yaml`.
- **Infrastructure (org-agnostic, lives in this repo)**: orchestrator, FastAPI daemon + `grassland` CLI, audit logger, knowledge base, talk store, revisit primitive, escalation routing.

Agents operate autonomously within authority defined by their org. The system enforces structural patterns regardless of org: managers cross-audit each other (peer review), and no agent both proposes and approves consequential actions (maker-checker pattern). Org-specific authority (e.g., budget thresholds, refund limits) lives in `escalation-rules.md` and the agents' system prompts.

A single runtime container (`<runtime>/`) hosts **multiple orgs** under `<runtime>/orgs/<slug>/`. Each org has its own `org/` content, SQLite DB, workspaces, KB, and talks. One daemon serves all orgs concurrently. Bootstrap: `grassland init <runtime>` creates the empty container; `grassland orgs init <slug> --from examples/orgs/hk-macau-tourism` materializes an org from the sample tree.

## Design Documents (read these first)

In the `protocol/` folder:

- `00-completion-contract.md` — Universal completion-report format, manager decision schema, agent-callback list
- `05-runtime-blueprint.md` — Index pointing to:
  - `05b-agent-runtime.md` — Executor model, memory architecture, lifecycle & scheduling
  - `05c-orchestrator.md` — Orchestrator responsibilities, permissions, task state machine
  - `05e-dashboard.md` — Dashboard layout, API endpoints, implementation order
- `06-knowledge-base.md` — Shared KB rules

`05c-orchestrator.md` and `05e-dashboard.md` are org-agnostic — they reference "team manager" / "team alpha" as placeholders. Org-specific charter, teams, and agent prompts live in `<runtime>/orgs/<slug>/org/`.

## Tech Stack
- **Language**: Python 3.11+ (currently running 3.13)
- **Package manager**: `uv`
- **Agent executor**: Per-agent. Claude Code (`claude -p ... --permission-mode auto`), Codex (`codex exec --json -`), and opencode (`opencode run`) are supported — no third-party agent framework dependency.
- **Daemon**: FastAPI HTTP service (`src/daemon/`) — serves orchestrator work, SSE task events, agent callbacks
- **CLI**: Thin HTTP client (`src/client/`) that talks to the daemon over localhost
- **Web UI**: Localhost SPA bundled into the daemon (`web/` → built to `web/dist/` → served at `/`). React 18 + TypeScript strict + Tailwind 3 + TanStack Query v5 + React Router v6. Auth via the same bearer token at `~/.grassland/daemon.token`, fetched once via `GET /api/v1/auth/bootstrap` (localhost-gated). Spec: `docs/superpowers/specs/2026-05-14-web-ui-design.md`. Launch with `grassland web`.
- **Agent workflow**: Shared workspace skills (`protocol/skills/`) — `start-task`, `make-worktree`, `manage-repo`, `manage-agent`. The orchestrator prompt references the same SOPs across all executors.
- **Orchestrator**: Custom Python application. `run_step` is the only primitive — each invocation advances one task by one subprocess call; an async `TaskQueue` + worker pool (`src/daemon/queue.py`) drives re-enqueues across steps. The team manager drives decisions. Implicit `review_verdict` audit rows are written when a delegation terminates (approved / rejected) — the founder reviews those via `grassland audit` to identify which agents need attention.
- **Data models**: Pydantic v2 + pydantic-settings
- **Database**: SQLite with WAL mode, per-org under `<runtime>/orgs/<slug>/grassland.db`. Schema covers audit logs and task state, plus per-feature tables (token usage, Feishu correlation, threads) documented in the corresponding specs under `docs/superpowers/specs/`.
- **Feishu integration**: `lark-oapi>=1.6,<2` (official ByteDance SDK) — outbound `im.v1.message.create` via `src/infrastructure/feishu/`; inbound WS subscription to `im.message.receive_v1` via `src/daemon/feishu_listener.py`.
- **Knowledge base**: File-backed markdown under `<runtime>/orgs/<slug>/kb/` with atomic writes, substring/tag search, `_index.md` regeneration. No vector store yet.
- **LLM**: Provider depends on the selected executor
- **Hosting**: Local Mac Mini

## Implementation Order (system features)

System kernel milestones — org-agnostic infrastructure. Org content (agent rosters, charters) is not on this list.

**Done (in order shipped):**

1. Orchestrator + first team — manager-driven decision loop, executor-backed agent sessions.
2. Audit logging — SQLite-backed semantic events; `session_end` payloads carry full `token_usage` dict.
3. Manager-driven orchestration — `GRASSLAND_MAX_ORCHESTRATION_STEPS` (default 50) before escalation.
4. Agent memory — persistent workspaces, per-entry `learnings/LRN-NNN-<slug>.md`, `task_history.md`. Spec: `2026-05-13-per-agent-learnings-structural-upgrade-design.md`.
5. ~~Performance scoring — 30-day rolling, green/yellow/red tiers.~~ Removed 2026-05-27. The audit log (review verdicts + completion/failure events) is sufficient for the founder to identify which agents need attention; tier classification on top added no enforcement and was misleading. Reviewed via `grassland audit`.
6. Talk flow — founder↔agent conversations with transcripts and end-of-talk learnings.
7. Knowledge Base — per-org KB with freeform `type`; founder rulings via `grassland kb add`.
8. Revisit primitive — spec: `2026-04-21-opc-revisit-design.md`.
9. Org-per-runtime layout — file-backed `org/{charter.md,escalation-rules.md,teams.yaml,config.yaml,agents/}`.
10. Multi-org container — per-org DB/workspaces/KB/talks; `grassland migrate-to-multi-org` for v1 → v2.
11. Feishu notifications — outbound push + reply-to-unblock; specs: `2026-05-08-feishu-notification-design.md`, `2026-05-12-feishu-interactive-actions-design.md`.
12. Threads foundation — email-style multi-agent workchannels with daemon-minted invocation tokens; CLI surface + end-to-end integration coverage via `fake_claude.sh` thread-prompt routing. Spec: `2026-05-13-threads-design.md`.
13. Threads web UI — localhost React+Tailwind SPA bundled into the FastAPI daemon, replaces the original Textual TUI. Three-layer architecture (`lib/api/` 1:1 daemon mirror → `features/<domain>/` → generic `components/`) designed to absorb future CLI domains. OpenAPI snapshot + TS coverage test pin the contract. `grassland threads` (no subcommand) points at `grassland web`; the `src/tui/` tree was deleted. Spec: `2026-05-14-web-ui-design.md`.
14. Jobs (founder-approved + agent-autonomous) — JOB-NNN — agent submits a job with `review_required` and `persistent` flags. `review_required=true` enqueues for founder review; `false` auto-runs immediately. `persistent=true` means unbounded runtime (killed on task terminal or explicit stop); `false` means default 300s timeout. Runner module at `src/daemon/jobs_runner.py`, route module at `src/daemon/routes/jobs.py`, agent skill at `protocol/skills/jobs/SKILL.md`. Spec: `docs/superpowers/specs/2026-05-26-jobs-design.md`.
15. Session-timeout auto-route — classify executor failures by kind (`session_timeout`, `no_callback`, `rate_limit`, `executor_error`, `agent_exception`, `session_failed` fallback) in `run_step._classify_failure_kind`. Per-kind auto-revisit cap (`_AUTO_REVISIT_CAP_PER_KIND = 2`) replaces the prior global cap — same-kind exhaustion at 2, different kinds have independent budgets. Cascade-fail Feishu notifications are suppressed when a root auto-revisit covers the lineage (the cascade still cascade-fails ancestors for state correctness; only the founder ping is dropped). `failure_kind` is hoisted to top-level of the `auto_revisit_of` audit payload for per-kind counting + AUTO-REVISIT-CONTEXT header rendering. Spec: `2026-05-25-session-timeout-auto-route-design.md`. Founder-ratified at TALK-037.
16. Shared Assets — org-wide flat blob store for persistent agent artifacts (reports, exports, screenshots). `grassland assets {put,list,get}` CLI; daemon routes under `/api/v1/orgs/{slug}/assets`; audited puts; CLI-only design works uniformly across Claude/Codex/Opencode. Plan: `docs/superpowers/plans/2026-05-27-shared-assets.md`.

**Open:**

17. **Founder dashboard** — aggregate audit logs and escalation summaries into a weekly view. Design: `protocol/05e-dashboard.md`.
18. **Persistent agents** — long-running loops for runtime patterns that don't fit single-task batch execution (e.g., real-time customer-chat worker). Currently every agent session is one task → one subprocess.

## Directory Layout

```
~/projects/my-opc/                     # Source repo
|-- protocol/                          # System kernel docs (00, 05*, 06) + shared agent skills
|-- scripts/daemon.sh                  # Launch the FastAPI daemon
|-- src/
|   |-- cli.py                         # `grassland` command — HTTP client
|   |-- client/                        # httpx-based client + SSE streaming
|   |-- daemon/                        # FastAPI app, routes, queue, sessions, Feishu listener
|   |-- orchestrator/                  # run_step, executors, capabilities, performance, prompt_loader
|   |-- infrastructure/                # database, audit_logger, kb_store, talk_store, learnings_store, feishu/
|   `-- tui/                           # Textual threads TUI
|-- tests/                             # Unit + integration (with fake CLIs)
`-- examples/orgs/hk-macau-tourism/    # Canonical sample org tree

~/.grassland/                                # Daemon home — auth_token, runtimes.yaml, daemon.pid, daemon.port

<runtime-dir>/                         # Slugless multi-org container (created by `grassland init <path>`)
|-- grassland.yaml                           # marker — schema_version: 2, type: multi-org-runtime
`-- orgs/<slug>/                       # Created by `grassland orgs init <slug> [--from <example>]`
    |-- grassland.db                         # per-org SQLite
    |-- org/                           # editable org content
    |   |-- charter.md, escalation-rules.md, teams.yaml, config.yaml
    |   `-- agents/                    # active `<name>.md` + `_pending/<name>.md`
    |-- workspaces/<agent>/            # agent.yaml, CLAUDE.md|AGENTS.md, .claude/|.agents/, repos/, learnings/, task_history.md
    |-- kb/                            # per-org KB (auto-regenerated `_index.md`)
    |-- talks/                         # TALK-NNN.md
    |-- threads/                       # THR-NNN.md
    |-- jobs/                          # JOB-NNN.{out,err,script} (full captured output + frozen script body)
    `-- assets/                        # org-shared blob store (put/list/get via `grassland assets`)
```

HTTP routes: per-org under `/api/v1/orgs/<slug>/...`; container-level under `/api/v1/runtime` and `/api/v1/orgs`. Legacy v1 (single-org flat layout) migrates in place via `grassland migrate-to-multi-org` — TTY-gated, refuses with active tasks or open talks. Even older v0 (DB-backed agent enrollments) migrates first via `grassland migrate-to-org-runtime`.

## Configuration

Operational settings use the `GRASSLAND_` env prefix. Runtime paths are derived from the runtime directory.

| Variable | Default | Description |
|----------|---------|-------------|
| `GRASSLAND_CLAUDE_CLI_PATH` | `claude` | Path to Claude Code CLI |
| `GRASSLAND_CODEX_CLI_PATH` | `codex` | Path to Codex CLI |
| `GRASSLAND_OPENCODE_CLI_PATH` | `opencode` | Path to opencode CLI |
| `GRASSLAND_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `GRASSLAND_PROTOCOL_DIR` | `protocol` | Protocol docs dirname (relative to project root) |
| `GRASSLAND_MAX_ORCHESTRATION_STEPS` | `50` | Max manager decision steps before escalation |
| `GRASSLAND_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout — global default; see resolution below |
| `GRASSLAND_ORG_SLUG` | _(unset)_ | Default org slug for per-org CLI commands. Resolution: explicit `--org` flag > `GRASSLAND_ORG_SLUG` env > auto-infer (only if exactly one org exists) > error |

### Session timeout resolution

`Orchestrator._resolve_session_timeout(agent_name, task_id=...)` walks three layers, highest precedence first:

1. **Task override** — `tasks.session_timeout_seconds` column, set via `grassland revisit ... --session-timeout-seconds N` and inherited by every child spawned from that task.
2. **Org override** — `session_timeout_seconds:` in `<runtime>/orgs/<slug>/org/config.yaml` (loaded by `src/orchestrator/org_config.py`).
3. **Code default** — `Settings.session_timeout_seconds` (1800s; overridable via `GRASSLAND_SESSION_TIMEOUT_SECONDS`).

Positive integers only; `<= 0` or non-int raises at parse time. The `agent_name` argument is unused (kept for call-site symmetry); legacy `session_timeout_seconds` in agent frontmatter is silently ignored.

### Agent executors

Each workspace declares an `executor` in `agent.yaml`: `claude`, `codex`, or `opencode`. Missing values default to `claude`. All three share the same `protocol/skills/` tree. Workspace differences:

| | bootstrap doc | skills dir | permission surface |
|--|--|--|--|
| Claude | `CLAUDE.md` | `.claude/skills/` | `permissions.allow` in `.claude/settings.json` **AND** `--allowedTools` on CLI (both required, see below) |
| Codex | `AGENTS.md` | `.agents/skills/` | sandbox flags on CLI |
| opencode | `AGENTS.md` | `.agents/skills/` | `opencode.json` `permission.bash` map |

**Codex sandbox**: `CodexExecutor.run` passes `-c sandbox_workspace_write.network_access=true` on every invocation. The `workspace-write` sandbox blocks localhost by default, which would kill the agent's `grassland report-completion` callback to `127.0.0.1`. Do not remove this flag without re-architecting the callback path away from localhost sockets.

**opencode permissions**: `OpencodeWorkspaceAdapter.write_opencode_json` writes a strict default — `{"permission": {"bash": {"*": "deny", "grassland *": "allow", ...per-agent allow_rules...}}}`. **Do not pass `--dangerously-skip-permissions` on the CLI** — it bypasses `opencode.json` and erases the per-prefix discipline.

Enrolling a non-Claude worker: set `"executor": "codex"` (or `"opencode"`) in the `grassland manage-agent --from-file` payload. Founder approval (`grassland approve-agent`) bootstraps the right surface for the chosen executor. See `protocol/skills/manage-agent/SKILL.md` for full payload shapes.

Repos are configured per agent in `agent.yaml`:
```yaml
repos:
  web-app: https://github.com/t-benze/web-app.git
  docs: https://github.com/t-benze/docs.git
```
`grassland init-agent` creates a default `agent.yaml` with empty repos if missing.

### Agent permission model

Agents call the orchestrator's CLI (`grassland report-completion`, `grassland learning`, `grassland manage-repo`, `grassland manage-agent`, `grassland dispatch`, ...) as their only sanctioned side-effect channel. **Baseline allow rule for every agent: `grassland`.**

Per-agent extras are declared in agent frontmatter (`<runtime>/orgs/<slug>/org/agents/<name>.md`) under `allow_rules:`. Example: the sample org's `engineering_head` declares `gh pr close`, `gh pr comment`, `gh issue close`, `gh issue comment` — needed because Claude's headless risk heuristic refuses those calls otherwise even in `--permission-mode auto`. Keep extras narrow: each prefix can silently mutate shared external state on every future task.

**For Claude specifically**, allow rules must land in two places kept in sync:

1. `.claude/settings.json` `permissions.allow` — written by `ClaudeWorkspaceAdapter.write_settings_json` (used by interactive sessions; surfaces intent).
2. `--allowedTools` on the CLI — passed by `ClaudeExecutor.run` for headless sessions.

Both surfaces are generated from `allow_rules_for_agent(agent_name, cli=...)` in `src/orchestrator/workspace_adapters.py` (settings uses `Bash(<cmd>:*)`; CLI uses `Bash(<cmd> *)`). **Do not hand-edit either** — `grassland init-agent` rewrites them. The two-surface requirement exists because Claude Code 2.1.x ignores `permissions.allow` in headless `-p` mode; without the CLI flag, the agent's first `grassland ...` call is blocked and the task silently rejects.

**When adding new orchestrator capabilities, keep them under the `grassland` binary** so they stay inside the baseline allow rule. Only add a raw-tool prefix when the operation genuinely cannot be wrapped in `grassland` (e.g., third-party CLI for external infra we don't own).

**Agent-side completion payloads must be single-line `grassland` invocations.** The Claude permission matcher treats newlines (and `&&`, `||`, `;`, `|`) as command separators and matches each subcommand independently; multi-line bash with backslash continuations is rejected even when the surface command is `grassland ...`. The `start-task` skill writes payloads to `/tmp/completion-<task_id>.json` and invokes `grassland report-completion --from-file <path>` as a single line. Any new agent-facing callback with multiple arguments must follow the same `--from-file` pattern.

## Code Style
- Type hints on all function signatures
- `from __future__ import annotations` in all source files
- Pydantic v2 models for structured data, StrEnum for enumerations (agent names are plain strings — agents are discovered dynamically from `<runtime>/orgs/<slug>/org/agents/*.md`)
- Tests for business logic (escalation rules, audit-log shape)

## Org content APIs

`AgentDef` (`src/orchestrator/agent_def.py`) is the in-memory representation of an agent file: markdown-with-YAML-frontmatter, parsed/rendered by `parse_agent_text` / `render_agent_text`. Fields: `name`, `team`, `role` (worker|manager), `executor` (claude|codex|opencode), `description`, `allow_rules`, `repos`, `enrolled_by`, `enrolled_at_task`, `enrolled_at`, `system_prompt` (body). **No `session_timeout_seconds` field** — see resolution above.

`src/orchestrator/prompt_loader.py` is the only API for reading/writing agent files: `load_agent`, `list_agents`, `list_pending`, `write_pending_agent`, `approve_agent`, `reject_agent`. Routes (`src/daemon/routes/agents.py`) and the orchestrator all read through this module against the per-org root. **Do NOT reach into the legacy `agent_enrollments` SQLite table** for new code paths — it remains in the schema for backward compat with v0 runtimes only.

`TeamsRegistry` (`src/orchestrator/teams.py`) is seeded from `teams.yaml` and auto-persists on `add_worker` / `remove_worker`. There is no `DEFAULT_LAYOUT` — an org without `teams.yaml` is treated as empty.

## Task status vocabularies

Agents self-report `status="completed"|"blocked"` via `grassland report-completion` (the worker's view of its session). The orchestrator-owned `TaskStatus` lives on the `tasks` row and is distinct: `{pending, in_progress, blocked, completed, failed}` based on orchestration classification, with `block_kind` (`delegated` | `escalated`) specifying the reason.

## Manager decision contract

Team-manager completion payloads carry two fields with distinct purposes:

- **`summary`** (prose) — human-readable description of what the manager did or concluded this step. Rendered in `grassland details`, audit logs, `task_history.md`. Stored on `task_results.output_summary`.
- **`decision`** (JSON object, NextStep schema) — the structured action the orchestrator will execute: `{"action": "delegate"|"done"|"escalate", ...}`. Stored on `task_results.decision_json` (manager-only column; workers leave NULL). Parsed by `Orchestrator._parse_next_step` directly — no prose inference.

Full schema with worked examples lives in `protocol/00-completion-contract.md` ("Manager decision field"). The decision-field name for a delegated child task's brief is **`prompt`, not `brief`** — Pydantic v2 silently ignores extras, so writing `"brief"` produces an empty-brief child task.

## Running Tests
```bash
uv run pytest tests/ -v                  # unit tests only (default)
uv run pytest tests/ -v -m integration   # end-to-end tests (spawns a real daemon + fake executor binaries)
uv run pytest tests/ -v -m ""            # both
```

Integration tests are excluded by default because they spawn a real daemon and fake CLIs. They are isolated from `~/.grassland/` via `GRASSLAND_DAEMON_HOME`. **Run them locally before any change touching the daemon lifespan, SessionTracker, or callback routes** — that's the surface area where unit tests have historically missed regressions. CI runs them on every PR.

`tests/integration/fake_claude.sh` recognizes two prompt shapes and routes to two plan-env vars:

- **Task invocations** — extracts `task_id` / `session_id` from the start-task SKILL's `Parameters:` block and sources `$FAKE_CLAUDE_PLAN` with `(task_id, session_id, agent, org_slug)`.
- **Thread invocations** — detects the `Your invocation_token for this turn is: …` line, extracts `THR-NNN` + token + purpose (reply / bootstrap / close_out), and sources `$FAKE_CLAUDE_THREAD_PLAN` with `(thread_id, token, agent, org_slug, purpose)`. Agent name comes from `${PWD##*/}` because the thread prompt's first line is "You are participating in thread …" rather than "You are <agent>." — keep that derivation if you touch the script.

Two env vars / two fixtures (`fake_claude_plan_env` and `fake_claude_thread_plan_env`) keep the two flows independent. A test that exercises BOTH a thread invocation AND a dispatched task (e.g., `tests/integration/test_threads_e2e.py::test_agent_dispatch_from_thread_creates_task`) sets both plans.

## Web UI

Localhost SPA at `web/`. Three layers (strict, codified in `web/ARCHITECTURE.md`):

1. **`web/src/lib/api/<X>.ts`** — one TS module per `src/daemon/routes/<X>.py`,
   exposing one pure function per `@router.*` decorator. Agent-callback
   endpoints are deliberately omitted (`/report-completion`, `/tasks/{id}/
   completion|progress`, `/agents/manage|repos`, `/agents/{a}/learnings*`
   writes, thread `/reply|/decline|/dispatch|/close-out`).
2. **`web/src/features/<domain>/`** — React feature folders. Threads is the
   only one populated. May import only from `lib/` and `components/`. No
   cross-feature imports.
3. **`web/src/components/`** — generic primitives (Button, Modal). Promoted
   from a feature on third use.

Contract pinning:

- **Python side** — `tests/contract/test_openapi_snapshot.py` pins paths +
  methods + params + responses to `tests/contract/openapi.json`. Regenerate
  intentional changes via `GRASSLAND_REGEN_OPENAPI=1 uv run pytest
  tests/contract/test_openapi_snapshot.py`.
- **TS side** — `web/src/test/openapi-coverage.test.ts` reads the same
  snapshot and asserts every documented path is in exactly one of
  `INCLUDED_PATHS` or `EXCLUDED_PATHS`. Adding a new daemon route fails
  this test until the engineer either writes a TS mirror (and lists the
  path under INCLUDED) or justifies the exclusion (EXCLUDED with a reason).

Build + dev:

```bash
scripts/build_web.sh        # production build → web/dist/, served by daemon at /
cd web && npm run dev       # Vite dev server, /api/* proxied to the daemon
grassland web                     # open the built bundle in the default browser
```

Auth model: the SPA fetches the daemon's existing bearer token once via
`GET /api/v1/auth/bootstrap` (localhost-gated; rejects any peer that isn't
`127.0.0.1`/`::1`/`localhost`), caches it in `sessionStorage`, and attaches
it to every subsequent HTTP+SSE call. The CLI bearer-token model is
unchanged.

## Running the Daemon + CLI

The CLI is an HTTP client. Start the daemon once, then run CLI commands.

```bash
scripts/daemon.sh start    # background; pid/port under ~/.grassland/
scripts/daemon.sh status   # or stop
scripts/build_web.sh       # build web/dist/ (npm ci + vite build)
grassland web [--no-open]        # open the SPA in the default browser
```

Slug resolution for per-org commands: explicit `--org <slug>` > `GRASSLAND_ORG_SLUG` env > auto-infer (only when the container has exactly one org) > error. Container-level commands (`grassland init`, `grassland use`, `grassland orgs ...`, `grassland migrate-to-multi-org`) take no `--org`.

**Full founder-facing CLI** — tasks, agents, KB, threads, talks, audit, assets, runtime, migrations — is documented in `skills/grassland/SKILL.md` (symlinked at `~/.claude/skills/grassland`).

**Agent-side callbacks** (invoked by skills inside agent sessions; do NOT invoke by hand — they falsify audit data):

- `grassland report-completion` — terminal callback from the `start-task` skill
- `grassland progress` — long-running mid-task heartbeat
- `grassland learning {add,update,promote,reindex}` on migrated workspaces; legacy `grassland learning --text` on pre-migration
- `grassland manage-agent`, `grassland manage-repo`, `grassland dispatch`
- `grassland threads {reply,decline,dispatch,close-out}`

Every agent callback uses `--from-file <path>` because Claude's permission matcher splits multi-line bash into separate commands; see "Agent permission model" above.

## Knowledge Base

Per-org under `<runtime>/orgs/<slug>/kb/` (orgs do not share a KB). One entry shape — `KBEntry.type` is freeform; route validation only enforces non-empty `slug/title/type/topic`. The dedicated `kb precedent` route was removed; founder rulings flow through plain `grassland kb add` with `source_task: <task-id>` in frontmatter. Implementation: `src/infrastructure/kb_store.py` + `src/daemon/routes/kb.py` (atomic writes, `kb_lock`, substring/tag search, `_index.md` regen). Full rules: `protocol/06-knowledge-base.md`. The context builder injects a "Knowledge Base" section into every agent's bootstrap doc; `start-task` has explicit consult + contribute steps.

## Per-Agent Learnings

Per-agent under `<runtime>/orgs/<slug>/workspaces/<agent>/learnings/`, one `LRN-NNN-<slug>.md` per entry. Full spec: `docs/superpowers/specs/2026-05-13-per-agent-learnings-structural-upgrade-design.md`. Implementation: `src/infrastructure/learnings_store.py` + the `/agents/{name}/learnings/entries/...` block in `src/daemon/routes/agents.py`. CLI: `grassland learning list|get|search|add|update|promote|reindex`.

**Non-obvious invariants:**

- **Per-workspace migration is state-aware** — `PersistentWorkspaceSetup.ensure()` never creates `learnings/` when a non-empty flat `learnings.md` exists. Existing agents stay on the legacy shape until a founder-dispatched migration moves them; new workspaces start on the new layout.
- **Legacy 410** — `grassland learning --agent X --text "..."` returns `410 Gone` once `learnings/` exists.
- **Cross-refs** — `related_to` and `supersedes` validated against existing IDs at write time (unknown → 400); self-refs rejected. `supersedes` is the canonical evolve-a-rule primitive.
- **Promotion** — `grassland learning promote <LRN-NNN> --kb-slug <slug>` is one-way; body becomes a 2-line pointer stub and entry locks.
- **End-of-talk** — `end_talk` writes into the new store on migrated workspaces (synthesized slug `talk-<talk_id>-<idx>`, topic `talk-residue`); pre-migration → flat-file append.

## Shared Assets (org-wide blob store)

Per-org at `<runtime>/orgs/<slug>/assets/`. Flat directory of opaque files —
persistent artifacts produced by any agent and visible to every other agent
in the same org. Implementation: `src/infrastructure/asset_store.py` +
`src/daemon/routes/assets.py`. CLI: `grassland assets {put,list,get}`.

**Non-obvious invariants:**

- **CLI-only access by design** — Codex (`workspace-write` sandbox) and
  Opencode (bash deny-by-default) both block direct writes outside the
  agent's workspace; only the `grassland` baseline allow-rule works across
  all three executors. Don't add a "just `cat`/`cp` it" agent skill.
- **Flat namespace; no nesting v1** — names match `[A-Za-z0-9._-]+`, max
  200 chars, no leading `.`. Slash-bearing names rejected as
  `invalid_asset_name`.
- **Size cap is 10 MB per file** (`MAX_ASSET_BYTES`). Larger uploads → HTTP
  413. v1 has no chunking / multipart resumption.
- **PUT is idempotent (overwrites)** — no version history; agents are
  expected to encode date/identity in the name if they care about
  history. Atomic via `tempfile.mkstemp` + `os.replace` so partial writes
  never leak.
- **`asset_put` is audited; `list`/`get` are not** — read paths are free,
  consistent with KB list/get and on the same rationale (no PII gradient
  inside the asset store). The audit row's `task_id` column stores
  `f"asset:{name}"` (the `asset:` prefix is mandatory) so asset names like
  `TASK-123` or `TALK-7` can never pollute the corresponding task/talk
  scopes consumed by `Database.get_audit_logs(task_id)`.
- **Not the KB** — assets are blobs. The KB is for typed/structured
  knowledge (frontmatter, slug, type, topic). Don't dump markdown content
  into assets/ that should be a KB entry.
- **Dir created at fresh-org init AND idempotently at lifespan startup**
  for orgs that pre-date the feature. Both code paths are required.

## Revisit (founder recovery)

`grassland revisit <task-id>` spawns a NEW root task inheriting brief + team from a terminal predecessor; old lineage is frozen. TTY-gated; no `--yes` bypass. Spec: `docs/superpowers/specs/2026-04-21-opc-revisit-design.md`.

Eligible predecessor states: `failed`, `failed-cancelled` (founder-cancelled, normalized on the wire), `blocked(escalated)`, or `completed`. Anything else → `409 cannot_revisit`.

**Non-obvious invariants:**

- The predecessor-link lives in TWO places: `tasks.revisit_of_task_id` column (indexed, queryable) AND a richer `audit_log` entry (`flagged`, `cascade`, `founder_note`, `prior_status`). The column is a **sideways** reference — `walk_ancestors` MUST NOT follow it, or cascade-fail will re-poison revisits via `_enqueue_parent_if_waiting`. Helpers: `Database.walk_revisit_chain` (backward) and `Database.get_direct_revisits` (forward).
- On the new root's first orchestration step, `_revisit_header_if_applicable(orch, task_id)` prepends a 5-6 line context header pointing the manager at `grassland details` / `grassland audit` / `grassland recall` for the frozen predecessor.
- `run_step` also auto-revisits on opaque-failure recovery; task-row `session_timeout_seconds` is copied onto every spawned revisit root.

## Session-timeout auto-route

Auto-revisit on opaque agent failures (subprocess timeout, no-completion-callback, executor crash, rate-limit, agent exception) is the system's silent retry path; this section documents the per-kind cap + cascade-fail-suppression shape. Spec: `docs/superpowers/specs/2026-05-25-session-timeout-auto-route-design.md`. Founder-ratified at TALK-037.

**Failure kinds** (`run_step._classify_failure_kind`): `session_timeout` (`error.startswith("Session timed out after")` — written by `executors.py:197`), `no_callback` (`success=True and report is None`, the TASK-045 class), `rate_limit` (substring `"hit your limit"` + `"reset"` OR `"rate limit"` in any of error / stdout_tail / stderr_tail), `executor_error` (non-zero `returncode`), `agent_exception` (exception escapes `_run_agent`). The triad `_SESSION_TIMEOUT_CLASS = {"session_timeout", "no_callback", "rate_limit"}` is a routing-class predicate exposed for future per-class policy; v1 routes all five kinds identically. `session_failed` is the defensive fallback for novel modes.

**Non-obvious invariants:**

- **Per-kind cap, not global** — `_AUTO_REVISIT_CAP_PER_KIND = 2` (in `run_step.py`); same-kind exhausts independently of other kinds. A chain that hits one `session_timeout` then one `executor_error` still has budget for another timeout AND another executor_error. Reverting to a global cap would mask a real bug behind transient infra noise.
- **Call order matters** — in both opaque-failure branches of `run_step_impl` (`except Exception` at the top and the `not result.success or report is None` block), `_maybe_spawn_auto_revisit` MUST run BEFORE `_enqueue_parent_if_waiting`, because the cascade-fail's notification gate threads through `root_auto_revisit_spawned`. The old order (cascade first, then revisit) caused 13+ ceremonial founder Feishu pings catalogued at TALK-037 — the work was being retried but the founder saw it anyway.
- **`failure_kind` lives top-level on `auto_revisit_of` audit payloads, NOT nested under `error_context`** — `_count_prior_auto_revisits_by_kind` does a flat `payload.get("failure_kind")` lookup; nesting it would slow per-kind counting + require parser changes.
- **Pre-spec auto-revisit rows count as zero** — audit entries written before this feature shipped have no `failure_kind` field. The counter ignores them, so an upgrade-in-flight chain gets at worst 2 extra retries (one per kind) above what the legacy global cap would have allowed. Mildly lenient by design; spec §10.
- **`_enqueue_parent_if_waiting` callers in route code keep the default `False`** — `src/daemon/routes/tasks.py:387` calls `_enqueue_parent_if_waiting(org.orchestrator, task_id)` on the founder-rejected-escalation path, where an auto-revisit would contradict the founder's decision. The kwarg default is correct; do not start passing `True` from anywhere outside `run_step.py`'s opaque-failure branches.
- **The cascade still cascade-fails ancestors** even when `root_auto_revisit_spawned=True`; ONLY the Feishu notification is suppressed. Parents going FAILED is load-bearing for the existing parent-state machine — the new root via `revisit_of_task_id` is the independent retry tree, not a continuation of the old lineage.
- **`_count_prior_auto_revisits_by_kind` must NOT use `walk_revisit_chain(truncate=True)`** — under per-kind cap accounting, silent truncation at 20 hops lets older same-kind entries fall out of the count window and re-opens the supposedly-capped budget on long-lived tasks (founder revisits consume hops without counting). The counter walks with `max_hops=_CHAIN_HOP_LIMIT_FOR_COUNTING=200` and `truncate=False`; `LineageTooDeep` is caught and returns `cap` (refuses to spawn) as the conservative answer + circuit breaker against revisit-spawn loops.

## Thread task-followup (system bridges task terminal → thread)

When a task dispatched from a thread reaches its true terminal state, `_maybe_post_thread_followup` (`src/orchestrator/run_step.py`) appends a `task_completed` or `task_failed` SYSTEM message to the originating thread and mints a fresh invocation with purpose `TASK_FOLLOWUP` so the dispatching agent can compose the result-bearing reply it promised. Spec: `docs/superpowers/specs/2026-05-28-thread-task-followup-design.md`.

**Non-obvious invariants:**

- **Call order matters.** The helper must be invoked *after* `_maybe_spawn_auto_revisit` at the two opaque-failure sites in `run_step_impl`, because the predicate ignores FAILED-with-spawned (the revisit chain will reach a later terminal that re-enters the helper). Mirrors the existing constraint between `_maybe_spawn_auto_revisit` and `_enqueue_parent_if_waiting`.
- **Thread linkage lives on the original root, not on revisit roots.** Auto-revisit and `/revisit` only copy `session_timeout_seconds`; they do NOT copy `dispatched_from_thread_id`. The helper walks `db.walk_revisit_chain(task_id, direction="backward")` and reads the column off `chain[-1]`. Do not propagate the column on revisit insert — the backward walk is the contract.
- **Dispatcher identity is read from audit, not stored on the task.** The `task_dispatched` audit row written by the dispatch route at `src/daemon/routes/threads.py` is the source of truth. If absent (missing original row), the helper audits `thread_followup_skipped(reason=dispatcher_unresolved)` rather than guessing.
- **Only root tasks fire.** Child task terminals cascade up to the root via `_enqueue_parent_if_waiting`'s `_fail(parent, ...)`, which re-enters the helper at that site. The `parent_task_id is not None` short-circuit is load-bearing — without it, every child completion in a dispatched-task tree would spam the thread.
- **`TASK_FOLLOWUP` purpose can reply or decline, but not dispatch.** `/threads/{id}/dispatch` keeps `require_purposes=[REPLY, BOOTSTRAP]`, which structurally rules out followup→dispatch recursion. Combined with the turn-cap auto-extend being per-followup, the loop is bounded.
- **Turn-cap auto-extend silently bumps `turn_cap` by 1 when projected over.** Audited via `thread_turn_cap_auto_extended(reason=task_followup)`. The pending-load projection counts `REPLY + BOOTSTRAP + TASK_FOLLOWUP` invocations via `Database.count_pending_turn_obligations`; `CLOSE_OUT` is excluded.
- **Non-OPEN threads skip everything.** `archiving`, `archived`, `abandoned` → audit-only, no system message, no mutation. The state-machine guards on send/reply/dispatch already reject non-OPEN; the helper matches that policy.
- **Cancelled tasks fire** (founder set `cancelled_at` → status FAILED → `auto_revisit_spawned=False` → fire). The system message's `cancelled: true` field is the surface; the founder gets a transparent thread record of the dispatch chain ending. To suppress, change the predicate; do not silently filter in the call sites.
- **PENDING-task cancellation has its own hook in `cancel_task`.** The RUNNING-task path is covered transitively (SIGTERM → rc=-15 → `run_step` Site B), but PENDING tasks never enter `run_step`. The cancel route captures `prior_statuses` during its BFS walk and fires the helper only for tasks that were `PENDING` before the cancel update, avoiding a double-fire on RUNNING tasks.
- **Cross-thread enqueue.** The thread queue is bound to the daemon's main asyncio loop; `run_step` runs on a worker thread. Bridging is via `asyncio.run_coroutine_threadsafe(queue.put(job), main_loop)`. The orchestrator picks up the loop reference at lifespan startup through `attach_thread_queue(thread_queue, main_loop)`; if either is unset (test orchestrators without daemon context), the helper audits `thread_followup_skipped(reason=enqueue_unavailable)` and the minted invocation stays PENDING.

## Jobs (founder-approved + agent-autonomous)

Per-org `jobs` SQLite table; per-org files at `<runtime>/orgs/<slug>/jobs/JOB-NNN.{out,err,script}`. Spec: `docs/superpowers/specs/2026-05-26-jobs-design.md`. Implementation: `src/daemon/routes/jobs.py` (HTTP), `src/daemon/jobs_runner.py` (subprocess + stream pumps + shutdown cleanup), `src/infrastructure/database.py` (table + state-transition methods), `src/infrastructure/audit_logger.py` (`log_job_*` methods).

Routes under `/api/v1/orgs/{slug}/jobs/`: `POST /submit` (agent callback; auth via session-binding chain), `GET /`, `GET /{id}`, `POST /{id}/run`, `POST /{id}/reject`, `GET /{id}/output`, `GET /{id}/events` (SSE). The `submit` route is in the OpenAPI EXCLUDED set; everything else is mirrored in `web/src/lib/api/jobs.ts`.

**Non-obvious invariants:**

- **Agent identity is derived, not echoed** — `agent_name` on the job row comes from `task.assigned_agent` after the session-mismatch check; the payload's `agent` field (if present) is ignored. This prevents an agent from mis-attributing a job to another agent.
- **Validation order matters** — `task_not_active` is checked BEFORE `session_mismatch`. A completed task has no live session, and reporting `session_mismatch` would mislead. Same discipline as `compose-as-agent`.
- **Subprocess env must be `dict(os.environ)`** — `asyncio.create_subprocess_exec(env=os.environ, ...)` raises `TypeError: Expected dict, got _Environ` under **uvloop** (FastAPI's default in production), even though stdlib asyncio accepts the mapping. Don't revert to passing `os.environ` directly.
- **SSE `/events` must re-poll the DB** — the `event_bus.subscribe` queue is registered inside the generator on first `__anext__`, so the runner can publish a terminal event between our initial status check and our subscription registration. The handler races the subscription against a 1s DB-poll loop; the row is authoritative.
- **Shutdown awaits runner tasks** — `terminate_all_inflight` snapshots `_RUNNER_TASKS` after SIGTERM/SIGKILL and `asyncio.wait_for(gather(*runners), timeout=5)` so each runner can transition its job row to terminal BEFORE per-org DBs close. Without this, rows sit in `running` until the next startup recovery scan, making dead jobs look live.
- **Startup recovery is the safety net** — `recover_orphaned_running_jobs` runs in the lifespan startup loop for every org; it force-fails any `running` row left from a crash. Independent of, and complementary to, the shutdown-await path.
- **Revisit header is the unblock path** — agents do NOT poll their own job output. The agent submits + self-blocks with `report-completion status=blocked`; the founder runs the job; the founder revisits the task; `_revisit_header_if_applicable` prepends a section listing predecessor jobs with `grassland jobs show/output JOB-NNN` commands so the new agent session reads them on its own.
- **Output capture is two-layer** — full streams to disk (no v1 size cap; spec §11 known limit), 65 KB head per stream mirrored to `stdout_head`/`stderr_head` DB columns for fast rendering. `GET /output` reads disk; the drawer + audit deep-link show DB head.
- **CLI `jobs run` uses raw httpx, not `OpcClient.stream`** — `OpcClient.stream` strips `event:` lines and yields only data payloads; useless for the multi-event-type (stdout/stderr/terminal) job stream. `cmd_jobs_run` accesses `client._client.stream(...)` directly to parse raw SSE frames. If this pattern repeats, promote `stream_raw` to the `OpcClient` public API.
- **`review_required` and `persistent` are honor-system on submit** — the daemon does not introspect the script against `allow_rules`. Misclassification is recoverable via founder stop + audit + talk + learning. Do NOT add daemon-side validation here without re-litigating the design tradeoff in the spec.
- **Task-terminal kill uses `_KILL_REASON_OVERRIDE` to signal `reason='task_ended'`** — the override dict is read inside `run_job` after the kill happens. If you add more kill paths, set the override BEFORE sending the signal so the runner sees it on the next bookkeeping pass.

## Feishu notifications (founder push + reply-to-unblock)

Per-org opt-in via `feishu_notifications` in `<runtime>/orgs/<slug>/org/config.yaml`. Credentials (`app_id`, `app_secret`) are required when `enabled: true` and live in the same file — treat it as secret-bearing (`chmod 600`, never commit). Specs: `docs/superpowers/specs/2026-05-08-feishu-notification-design.md`, `docs/superpowers/specs/2026-05-12-feishu-interactive-actions-design.md`. Setup runbook: `docs/setup/feishu-notifications.md`.

**Entry points:**

- Outbound: `Orchestrator.notify_escalated` / `notify_failed` — loop-aware fire-and-forget (creates an asyncio task when a loop is running, else spawns a daemon thread). `EscalationNotifier` in `src/infrastructure/feishu/notifier.py` mints `escalation_notifications` rows keyed by Feishu `message_id`. Send failures audit `escalation_notify_failed` and are swallowed; no row is minted on send failure.
- Inbound: `FeishuEventListener` (`src/daemon/feishu_listener.py`) starts one WebSocket connection per org with full Feishu config. WS thread runs `lark.ws.Client.start()` (blocking) and bridges to the asyncio loop via `asyncio.run_coroutine_threadsafe`. Listener helpers live in `feishu_listener.py` (not `app.py`) to avoid a circular import with `state.py`. Wired from FastAPI lifespan and `DaemonState.add_org`. WS threads are `daemon=True`.

**Reply routing** (8-step pipeline in `_handle_event_async`, updating `processed_event_ids.outcome` on every branch): dedup → chat-id filter → require `root_id` (reply branch) OR `allow_dispatch=true` (top-level dispatch) → drop bot-self → resolve via `resolve_escalation_in_process` / `revisit_from_notification` / `dispatch_via_feishu` → consume row + audit. Trust boundary is `chat_id` only — no per-Feishu-user authorization in v1.

**Critical invariant:** the lifespan wrapper `_resolve_for_listener` in `app.py` MUST NOT swallow exceptions from the in-process resolvers. If resolution fails (e.g., `409 task_not_escalated` because the founder used the CLI first), the outer `try/except` records `outcome="rejected", reason="handler_exception"` and leaves the row unconsumed — the founder's reply is preserved instead of silently lost.

**Optional features:**

- `notify_on_failure: true` — failure replies; hook in `run_step.py:_notify_failure_if_eligible` gates on enabled + not cancelled + no auto-revisit spawned. Listener routes `(kind=failure, decision=revisit)` to `revisit_from_notification`.
- `allow_dispatch: true` — top-level DISPATCH messages parsed by `parse_top_level_message(text)`; `dispatch_via_feishu` extracts the in-process helper from `submit_task` and raises `DispatchError(reason ∈ {empty_brief, unknown_team, dispatch_failed})`.
- **Jobs** — `submit_job` route fires `notify_job_submitted` after audit; founder reply `APPROVE` → `run_job_from_notification` (stored defaults), `REJECT\n<reason>` → `reject_job_from_notification`. On terminal transition, `_run_and_persist` looks up the job's notification via `get_latest_notification_for_job(job_id, kind="job_request")` and fires `notify_job_run_result` as a threaded follow-up. Notification kind: `job_request` (fourth value in `escalation_notifications.kind`); the JOB-NNN id lives in the `task_id` column, same overload as `thread_addressed`. Spec: `docs/superpowers/specs/2026-05-25-feishu-script-request-notifications-design.md`.

CLI fallbacks (`grassland resolve-escalation`, `grassland revisit`) consume any open notification row for the task with `consumed_by="cli-fallback"`, so a CLI-first resolution silently no-ops the later Feishu reply.

## Maintaining Documentation
- **README.md** is for end users — setup, CLI commands, configuration. No developer internals.
- **CLAUDE.md** is for developers and AI agents working on the codebase — architecture, code patterns, directory layout, implementation order.

## When Starting a New Implementation Phase
1. Read the relevant design doc first (e.g., `protocol/05c-orchestrator.md`)
2. Check existing code for patterns to follow — especially `src/orchestrator/`
3. Write tests alongside implementation
4. Keep agents' system prompts in sync with the markdown docs — the docs are the source of truth

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **my-opc** (10386 symbols, 23435 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/my-opc/context` | Codebase overview, check index freshness |
| `gitnexus://repo/my-opc/clusters` | All functional areas |
| `gitnexus://repo/my-opc/processes` | All execution flows |
| `gitnexus://repo/my-opc/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
