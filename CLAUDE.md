# Project: HappyRanch — Multi-Agent Org Runtime

## What This Is
HappyRanch is an **org-agnostic runtime** for operating a multi-agent organization supervised by a single human founder. The repo provides the system kernel (orchestrator, daemon + CLI, audit, KB, talk, revisit, escalation primitives); the *organization* it runs — charter, teams, agents, escalation rules, jurisdictions, budget authority — is loaded per-runtime from `<runtime>/orgs/<slug>/org/`.

A canonical sample org shipped at `examples/orgs/hk-macau-tourism/` runs a one-person tourism company serving foreign visitors to Hong Kong SAR and Macau SAR. Treat it as the reference shape when bootstrapping a new org; nothing about its specific teams, agents, or constraints is baked into the system.

## Architecture Summary
- **Layer 1**: Founder (human) — sets org rules, handles escalations, reviews weekly dashboard
- **Layer 2**: Manager agents — defined per-org in `<runtime>/orgs/<slug>/org/agents/<name>.md` with `role: manager`. Each manager owns one team listed in `teams.yaml`.
- **Layer 3**: Worker agents — same file shape, `role: worker`. Workers are assigned to a team via `teams.yaml`.
- **Infrastructure (org-agnostic, lives in this repo)**: orchestrator, FastAPI daemon + `happyranch` CLI, audit logger, knowledge base, talk store, revisit primitive, escalation routing.

Agents operate autonomously within authority defined by their org. The system enforces structural patterns regardless of org: managers cross-audit each other (peer review), and no agent both proposes and approves consequential actions (maker-checker pattern). Org-specific authority (e.g., budget thresholds, refund limits) lives in `escalation-rules.md` and the agents' system prompts.

A single runtime container (`<runtime>/`) hosts **multiple orgs** under `<runtime>/orgs/<slug>/`. Each org has its own `org/` content, SQLite DB, workspaces, KB, and talks. One daemon serves all orgs concurrently. Bootstrap: `happyranch init <runtime>` creates the empty container; `happyranch orgs init <slug> --from examples/orgs/hk-macau-tourism` materializes an org from the sample tree.

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
- **Agent executor**: Per-agent. Claude Code (`claude -p ... --permission-mode auto`), Codex (`codex exec --json -`), opencode (`opencode run`), and Pi (`pi -p ... --mode json`) are supported — no third-party agent framework dependency.
- **Daemon**: FastAPI HTTP service (`runtime/daemon/`) — serves orchestrator work, SSE task events, agent callbacks
- **CLI**: Thin HTTP client (`cli/client/`) that talks to the daemon over localhost
- **Web UI**: Localhost SPA bundled into the daemon (`web/` → built to `web/dist/` → served at `/`). React 18 + TypeScript strict + Tailwind v4 + TanStack Query v5 + React Router v6. Auth via the same bearer token at `~/.happyranch/daemon.token`, fetched once via `GET /api/v1/auth/bootstrap` (localhost-gated). Architecture: `web/ARCHITECTURE.md`. Spec: `docs/superpowers/specs/2026-05-14-web-ui-design.md`. Launch with `happyranch web`.
- **Agent workflow**: Shared workspace skills (`protocol/skills/`) — `start-task`, `make-worktree`, `manage-repo`, `manage-agent`, `dispatch`, `jobs`, `talk`, `thread`. The orchestrator prompt references the same SOPs across all executors.
- **Orchestrator**: Custom Python application. `run_step` is the only primitive — each invocation advances one task by one subprocess call; an async `TaskQueue` + worker pool (`runtime/daemon/queue.py`) drives re-enqueues across steps. The team manager drives decisions. Implicit `review_verdict` audit rows are written when a delegation terminates (approved / rejected) — the founder reviews those via `happyranch audit` to identify which agents need attention.
- **Data models**: Pydantic v2 + pydantic-settings
- **Database**: SQLite with WAL mode, per-org under `<runtime>/orgs/<slug>/happyranch.db`. Schema covers audit logs and task state, plus per-feature tables (token usage, Feishu correlation, threads) documented in the corresponding specs under `docs/superpowers/specs/`.
- **Feishu integration**: `lark-oapi>=1.6,<2` (official ByteDance SDK) — outbound `im.v1.message.create` via `runtime/infrastructure/feishu/`; inbound WS subscription to `im.message.receive_v1` via `runtime/daemon/feishu_listener.py`.
- **Knowledge base**: File-backed markdown under `<runtime>/orgs/<slug>/kb/` with atomic writes, substring/tag search, `_index.md` regeneration. No vector store yet.
- **LLM**: Provider depends on the selected executor
- **Hosting**: Local Mac Mini

## Directory Layout

**Source repo** (`~/projects/happyranch/`) — `protocol/` (kernel docs 00/05*/06 + shared skills), `scripts/daemon.sh`, `cli/{main.py, client/, thread_forward.py}` (extracted `happyranch` HTTP client), `runtime/{config.py, daemon/, orchestrator/, infrastructure/, models.py, runtime.py}`, `tests/`, `examples/orgs/hk-macau-tourism/` (canonical sample org). Run `ls runtime/<pkg>/` for module-level detail.

**Daemon home** (`~/.happyranch/`) — `auth_token`, `runtimes.yaml`, `daemon.pid`, `daemon.port`, `config.yaml`.

**Runtime container** — slugless multi-org tree (the non-obvious shape):

```
<runtime-dir>/                         # created by `happyranch init <path>`
|-- happyranch.yaml                           # marker — schema_version: 2, type: multi-org-runtime
`-- orgs/<slug>/                       # Created by `happyranch orgs init <slug> [--from <example>]`
    |-- happyranch.db                         # per-org SQLite
    |-- org/                           # editable org content
    |   |-- charter.md, escalation-rules.md, teams.yaml, config.yaml
    |   `-- agents/                    # active `<name>.md` + `_pending/<name>.md`
    |-- workspaces/<agent>/            # agent.yaml, CLAUDE.md|AGENTS.md, .claude/|.agents/, repos/, learnings/, task_history.md
    |-- kb/                            # per-org KB (auto-regenerated `_index.md`)
    |-- talks/                         # TALK-NNN.md
    |-- threads/                       # THR-NNN.md
    |-- jobs/                          # JOB-NNN.{out,err,script} (full captured output + frozen script body)
    `-- artifacts/                     # org-shared blob store (put/list/get via `happyranch artifacts`)
```

HTTP routes: per-org under `/api/v1/orgs/<slug>/...`; container-level under `/api/v1/runtime` and `/api/v1/orgs`. Only `schema_version: 2` is supported — older single-org (v1) and DB-backed enrollment (v0) runtimes are rejected at startup with a re-init hint.

## Configuration

Operational settings (`Settings` in `runtime/config.py`) resolve from, highest precedence first: (1) `HAPPYRANCH_`-prefixed **env vars**, (2) **`<daemon-home>/config.yaml`** (default `~/.happyranch/config.yaml`; honors `HAPPYRANCH_DAEMON_HOME`; keys are field names *without* the prefix, e.g. `queue_workers: 6`), (3) code defaults. There is **no `.env` support** — `settings_customise_sources` drops the dotenv source and adds `YamlConfigSettingsSource` (resolved per-instantiation, so tests can redirect via `HAPPYRANCH_DAEMON_HOME`). The home resolver is inlined in `config.py` (`_daemon_home`) rather than importing `runtime.daemon.paths`, to keep `config` free of a `daemon` dependency. Missing file → defaults (no raise). Do not confuse this daemon-level `config.yaml` with each org's `<runtime>/orgs/<slug>/org/config.yaml` (per-org settings, loaded by `runtime/orchestrator/org_config.py`). Runtime paths are derived from the runtime directory.

| Variable | Default | Description |
|----------|---------|-------------|
| `HAPPYRANCH_CLAUDE_CLI_PATH` | `claude` | Path to Claude Code CLI |
| `HAPPYRANCH_CODEX_CLI_PATH` | `codex` | Path to Codex CLI |
| `HAPPYRANCH_OPENCODE_CLI_PATH` | `opencode` | Path to opencode CLI |
| `HAPPYRANCH_PI_CLI_PATH` | `pi` | Path to Pi CLI |
| `HAPPYRANCH_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `HAPPYRANCH_PROTOCOL_DIR` | `protocol` | Protocol docs dirname (relative to project root) |
| `HAPPYRANCH_MAX_ORCHESTRATION_STEPS` | `50` | Max manager decision steps before escalation |
| `HAPPYRANCH_QUEUE_WORKERS` | `3` | Number of `run_step` worker slots (daemon-wide, shared across all orgs). Caps concurrent agent sessions — each slot blocks on one subprocess for the whole session. Must be > 0. Tunes head-of-line blocking; does NOT add per-org fairness |
| `HAPPYRANCH_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout — global default; see resolution below |
| `HAPPYRANCH_ORG_SLUG` | _(unset)_ | Default org slug for per-org CLI commands. Resolution: explicit `--org` flag > `HAPPYRANCH_ORG_SLUG` env > auto-infer (only if exactly one org exists) > error |

### Session timeout resolution

`Orchestrator._resolve_session_timeout(agent_name, task_id=...)` walks three layers, highest precedence first:

1. **Task override** — `tasks.session_timeout_seconds` column, set via `happyranch revisit ... --session-timeout-seconds N` and inherited by every child spawned from that task.
2. **Org override** — `session_timeout_seconds:` in `<runtime>/orgs/<slug>/org/config.yaml` (loaded by `runtime/orchestrator/org_config.py`).
3. **Code default** — `Settings.session_timeout_seconds` (1800s; overridable via `HAPPYRANCH_SESSION_TIMEOUT_SECONDS`).

Positive integers only; `<= 0` or non-int raises at parse time. The `agent_name` argument is unused (kept for call-site symmetry); legacy `session_timeout_seconds` in agent frontmatter is silently ignored.

### Agent executors

Each workspace declares an `executor` in `agent.yaml`: `claude`, `codex`, `opencode`, or `pi`. Missing values default to `claude`. All four share the same `protocol/skills/` tree. Workspace differences:

| | bootstrap doc | skills dir | permission surface |
|--|--|--|--|
| Claude | `CLAUDE.md` | `.claude/skills/` | `permissions.allow` in `.claude/settings.json` **AND** `--allowedTools` on CLI (both required, see below) |
| Codex | `AGENTS.md` | `.agents/skills/` | sandbox flags on CLI |
| opencode | `AGENTS.md` | `.agents/skills/` | `opencode.json` `permission.bash` map |
| Pi | `AGENTS.md` | `.agents/skills/` | no HappyRanch-managed sandbox; use external containment if needed |

**Codex sandbox**: `CodexExecutor.run` passes `-c sandbox_workspace_write.network_access=true` on every invocation. The `workspace-write` sandbox blocks localhost by default, which would kill the agent's `happyranch report-completion` callback to `127.0.0.1`. Do not remove this flag without re-architecting the callback path away from localhost sockets.

**opencode permissions**: `OpencodeWorkspaceAdapter.write_opencode_json` writes a strict default — `{"permission": {"bash": {"*": "deny", "happyranch *": "allow", ...per-agent allow_rules...}}}`. **Do not pass `--dangerously-skip-permissions` on the CLI** — it bypasses `opencode.json` and erases the per-prefix discipline.

**Pi permissions**: `PiExecutor.run` invokes `pi -p ... --mode json` from the agent workspace. Pi does not provide a HappyRanch-managed permission file or sandbox flag in this integration; rely on external containment for Pi-backed agents when command/tool restriction matters.

Enrolling a non-Claude worker: set `"executor": "codex"` (or `"opencode"` or `"pi"`) in the `happyranch manage-agent --from-file` payload. Founder approval (`happyranch approve-agent`) bootstraps the right surface for the chosen executor. See `protocol/skills/manage-agent/SKILL.md` for full payload shapes.

Repos are configured per agent in `agent.yaml`:
```yaml
repos:
  web-app: https://github.com/t-benze/web-app.git
  docs: https://github.com/t-benze/docs.git
```
`happyranch init-agent` creates a default `agent.yaml` with empty repos if missing.

### Agent permission model

Agents call the orchestrator's CLI (`happyranch report-completion`, `happyranch learning`, `happyranch manage-repo`, `happyranch manage-agent`, `happyranch dispatch`, ...) as their only sanctioned side-effect channel. **Baseline allow rule for every agent: `happyranch`.**

Per-agent extras are declared in agent frontmatter (`<runtime>/orgs/<slug>/org/agents/<name>.md`) under `allow_rules:`. Example: the sample org's `engineering_head` declares `gh pr close`, `gh pr comment`, `gh issue close`, `gh issue comment` — needed because Claude's headless risk heuristic refuses those calls otherwise even in `--permission-mode auto`. Keep extras narrow: each prefix can silently mutate shared external state on every future task.

**For Claude specifically**, allow rules must land in two places kept in sync:

1. `.claude/settings.json` `permissions.allow` — written by `ClaudeWorkspaceAdapter.write_settings_json` (used by interactive sessions; surfaces intent).
2. `--allowedTools` on the CLI — passed by `ClaudeExecutor.run` for headless sessions.

Both surfaces are generated from `allow_rules_for_agent(agent_name, cli=...)` in `runtime/orchestrator/workspace_adapters.py` (settings uses `Bash(<cmd>:*)`; CLI uses `Bash(<cmd> *)`). **Do not hand-edit either** — `happyranch init-agent` rewrites them. The two-surface requirement exists because Claude Code 2.1.x ignores `permissions.allow` in headless `-p` mode; without the CLI flag, the agent's first `happyranch ...` call is blocked and the task silently rejects.

**When adding new orchestrator capabilities, keep them under the `happyranch` binary** so they stay inside the baseline allow rule. Only add a raw-tool prefix when the operation genuinely cannot be wrapped in `happyranch` (e.g., third-party CLI for external infra we don't own).

**Agent-side completion payloads must be single-line `happyranch` invocations.** The Claude permission matcher treats newlines (and `&&`, `||`, `;`, `|`) as command separators and matches each subcommand independently; multi-line bash with backslash continuations is rejected even when the surface command is `happyranch ...`. The `start-task` skill writes payloads to `/tmp/completion-<task_id>.json` and invokes `happyranch report-completion --from-file <path>` as a single line. Any new agent-facing callback with multiple arguments must follow the same `--from-file` pattern.

## Conventions

**Code style** — Type hints on all function signatures. `from __future__ import annotations` in every source file. Pydantic v2 for structured data, StrEnum for enumerations (agent names are plain strings — agents are discovered dynamically from `<runtime>/orgs/<slug>/org/agents/*.md`). Tests for business logic (escalation rules, audit-log shape).

**Docs split** — `README.md` is for end users (setup, CLI commands, configuration). `CLAUDE.md` (this file) is for developers and AI agents working on the codebase. Design docs in `protocol/` and specs in `docs/superpowers/specs/` are the source of truth for behavior — keep agent system prompts in sync.

**Starting a new feature** — Read the relevant design doc first (e.g., `protocol/05c-orchestrator.md`). Follow existing patterns in `runtime/orchestrator/`. Write tests alongside implementation.

## Org content APIs

`AgentDef` (`runtime/orchestrator/agent_def.py`) is the in-memory representation of an agent file: markdown-with-YAML-frontmatter, parsed/rendered by `parse_agent_text` / `render_agent_text`. Fields: `name`, `team`, `role` (worker|manager), `executor` (claude|codex|opencode|pi), `description`, `allow_rules`, `repos`, `enrolled_by`, `enrolled_at_task`, `enrolled_at`, `system_prompt` (body). **No `session_timeout_seconds` field** — see resolution above.

`runtime/orchestrator/prompt_loader.py` is the only API for reading/writing agent files: `load_agent`, `list_agents`, `list_pending`, `write_pending_agent`, `approve_agent`, `reject_agent`. Routes (`runtime/daemon/routes/agents.py`) and the orchestrator all read through this module against the per-org root.

`TeamsRegistry` (`runtime/orchestrator/teams.py`) is seeded from `teams.yaml` and auto-persists on `add_worker` / `remove_worker`. There is no `DEFAULT_LAYOUT` — an org without `teams.yaml` is treated as empty.

## Task status vocabularies

Agents self-report `status="completed"|"blocked"` via `happyranch report-completion` (the worker's view of its session). The orchestrator-owned `TaskStatus` lives on the `tasks` row and is distinct: `{pending, in_progress, blocked, completed, failed}` based on orchestration classification, with `block_kind` (`delegated` | `escalated` | `blocked_on_job`) specifying the reason.

## Manager decision contract

Team-manager completion payloads carry two fields with distinct purposes:

- **`summary`** (prose) — human-readable description of what the manager did or concluded this step. Rendered in `happyranch details`, audit logs, `task_history.md`. Stored on `task_results.output_summary`.
- **`decision`** (JSON object, NextStep schema) — the structured action the orchestrator will execute: `{"action": "delegate"|"done"|"escalate", ...}`. Stored on `task_results.decision_json` (manager-only column; workers leave NULL). Parsed by `Orchestrator._parse_next_step` directly — no prose inference.

Full schema with worked examples lives in `protocol/00-completion-contract.md` ("Manager decision field"). The decision-field name for a delegated child task's brief is **`prompt`, not `brief`** — Pydantic v2 silently ignores extras, so writing `"brief"` produces an empty-brief child task.

## Inline delegation chains

A manager can declare a multi-leg workflow in one `delegate` decision using `NextStep.then` (list of `ChainLeg`) and optional per-leg `expect_verdict` gates. The orchestrator auto-advances to the next leg whenever a child terminates COMPLETED with a matching verdict, without consuming the manager's step budget. Spec: `docs/superpowers/specs/2026-05-30-inline-delegation-chain-design.md`. Protocol: `protocol/00-completion-contract.md` ("Inline delegation chains"). Implementation: `runtime/orchestrator/chain.py` (pure-logic state model + helpers) wired from `runtime/orchestrator/run_step.py`.

Example decision payload:
```json
{
  "action": "delegate",
  "agent": "dev_agent",
  "prompt": "Build the feature...",
  "then": [
    {"agent": "senior_dev",  "prompt": "Code-review the PR.", "expect_verdict": "APPROVE"},
    {"agent": "qa_engineer", "prompt": "QA the PR.",          "expect_verdict": "PASS"}
  ]
}
```

**Inline traps** (full catalog: spec §Non-obvious + `chain.py`):

- **Auto-advances do NOT consume orchestration steps** — declaring a chain costs 1 step, the final-leg wake costs 1; a clean 3-leg workflow is 2 steps, not 4.
- **Final-leg match still wakes the manager** — chains never auto-`done`. Don't add a chain-terminal auto-done shortcut without re-litigating it in the spec.
- **Cross-team validation runs on every leg at parse time** — an off-team agent on any leg rejects the whole decision; no leg is ever silently skipped.
- **Don't pre-embed upstream context in a leg's prompt** — `build_prior_leg_context` appends the prior leg's summary/verdict/`output_dir` automatically.

## Running Tests
```bash
uv run pytest tests/ -v                  # unit tests only (default)
uv run pytest tests/ -v -m integration   # end-to-end tests (spawns a real daemon + fake executor binaries)
uv run pytest tests/ -v -m ""            # both
```

Integration tests are excluded by default because they spawn a real daemon and fake CLIs. They are isolated from `~/.happyranch/` via `HAPPYRANCH_DAEMON_HOME`. **Run them locally before any change touching the daemon lifespan, SessionTracker, or callback routes** — that's the surface area where unit tests have historically missed regressions. CI runs them on every PR.

`tests/integration/fake_claude.sh` recognizes two prompt shapes and routes to two plan-env vars:

- **Task invocations** — extracts `task_id` / `session_id` from the start-task SKILL's `Parameters:` block and sources `$FAKE_CLAUDE_PLAN` with `(task_id, session_id, agent, org_slug)`.
- **Thread invocations** — detects the `Your invocation_token for this turn is: …` line, extracts `THR-NNN` + token + purpose (reply / bootstrap), and sources `$FAKE_CLAUDE_THREAD_PLAN` with `(thread_id, token, agent, org_slug, purpose)`. Agent name comes from `${PWD##*/}` because the thread prompt's first line is "You are participating in thread …" rather than "You are <agent>." — keep that derivation if you touch the script.

Two env vars / two fixtures (`fake_claude_plan_env` and `fake_claude_thread_plan_env`) keep the two flows independent. A test that exercises BOTH a thread invocation AND a dispatched task (e.g., `tests/integration/test_threads_e2e.py::test_agent_dispatch_from_thread_creates_task`) sets both plans.

## Web UI

Layer rules, boundary rule, and agent-callback omissions live in `web/ARCHITECTURE.md` (authoritative). Full design: `docs/superpowers/specs/2026-05-14-web-ui-design.md`.

**Contract pinning** — every browser-callable daemon route maps 1:1 to one TS function in `web/src/lib/api/`. Two paired tests enforce this:

- Python — `tests/contract/test_openapi_snapshot.py` pins the OpenAPI to `tests/contract/openapi.json`. Regenerate intentional changes via `HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py`.
- TS — `web/src/test/openapi-coverage.test.ts` asserts every documented path is in `INCLUDED_PATHS` (TS mirror written) or `EXCLUDED_PATHS` (justified). Adding a new daemon route fails this test until resolved.

**Build + dev:**

```bash
scripts/build_web.sh        # production build → web/dist/, served by daemon at /
cd web && npm run dev       # Vite dev server, /api/* proxied to the daemon
happyranch web               # open the built bundle in the default browser
```

**Auth model:** the SPA fetches the daemon's bearer token once via `GET /api/v1/auth/bootstrap` (localhost-gated; rejects any peer that isn't `127.0.0.1` / `::1` / `localhost`), caches it in `sessionStorage`, and attaches it to every HTTP+SSE call. CLI bearer-token model unchanged.

## Running the Daemon + CLI

The CLI is an HTTP client. Start the daemon once, then run CLI commands.

```bash
scripts/daemon.sh start    # background; pid/port under ~/.happyranch/
scripts/daemon.sh status   # or stop
scripts/build_web.sh       # build web/dist/ (npm ci + vite build)
happyranch web [--no-open]        # open the SPA in the default browser
```

Slug resolution for per-org commands: explicit `--org <slug>` > `HAPPYRANCH_ORG_SLUG` env > auto-infer (only when the container has exactly one org) > error. Container-level commands (`happyranch init`, `happyranch use`, `happyranch orgs ...`) take no `--org`.

**Full founder-facing CLI** — tasks, agents, KB, threads, talks, audit, artifacts, runtime, migrations — is documented in `skills/happyranch/SKILL.md` (symlinked at `~/.claude/skills/happyranch`).

**Agent-side callbacks** (invoked by skills inside agent sessions; do NOT invoke by hand — they falsify audit data):

- `happyranch report-completion` — terminal callback from the `start-task` skill
- `happyranch progress` — long-running mid-task heartbeat
- `happyranch learning {add,update,promote,reindex}` on migrated workspaces; legacy `happyranch learning --text` on pre-migration
- `happyranch manage-agent`, `happyranch manage-repo`, `happyranch dispatch`
- `happyranch threads {reply,decline,dispatch}`

All use `--from-file <path>` — see "Agent permission model" for why.

## Knowledge Base

Per-org under `<runtime>/orgs/<slug>/kb/` (orgs do not share a KB). One entry shape — `KBEntry.type` is freeform; route validation only enforces non-empty `slug/title/type/topic`. The dedicated `kb precedent` route was removed; founder rulings flow through plain `happyranch kb add` with `source_task: <task-id>` in frontmatter. Implementation: `runtime/infrastructure/kb_store.py` + `runtime/daemon/routes/kb.py` (atomic writes, `kb_lock`, substring/tag search, `_index.md` regen). Full rules: `protocol/06-knowledge-base.md`. The context builder injects a "Knowledge Base" section into every agent's bootstrap doc; `start-task` has explicit consult + contribute steps.

## Per-Agent Learnings

Per-agent under `<runtime>/orgs/<slug>/workspaces/<agent>/learnings/`, one `LRN-NNN-<slug>.md` per entry. Full spec: `docs/superpowers/specs/2026-05-13-per-agent-learnings-structural-upgrade-design.md`. Implementation: `runtime/infrastructure/learnings_store.py` + the `/agents/{name}/learnings/entries/...` block in `runtime/daemon/routes/agents.py`. CLI: `happyranch learning list|get|search|add|update|promote|reindex`.

**Inline traps** (full catalog: spec §Non-obvious):

- **Migration is state-aware** — `PersistentWorkspaceSetup.ensure()` never creates `learnings/` when a non-empty flat `learnings.md` exists; existing agents stay legacy until a founder-dispatched migration moves them.
- **Promotion to KB is one-way** — `happyranch learning promote` replaces the body with a 2-line stub and locks the entry. `supersedes` (validated against existing IDs at write time) is the evolve-a-rule primitive.

## Shared Artifacts (org-wide blob store)

Per-org at `<runtime>/orgs/<slug>/artifacts/`. Flat directory of opaque files —
persistent artifacts produced by any agent and visible to every other agent
in the same org. Implementation: `runtime/infrastructure/artifact_store.py` +
`runtime/daemon/routes/artifacts.py`. CLI: `happyranch artifacts {put,list,get}`.

**Inline traps** (full catalog: plan §Non-obvious):

- **CLI-only access by design** — sandboxed executors block direct writes outside the workspace; only the `happyranch` baseline allow-rule works across them. Don't add a "just `cat`/`cp` it" agent skill.
- **Audit `task_id` overload** — `artifact_put` writes `f"artifact:{name}"` (prefix mandatory) so artifact names can't pollute task/talk scopes. Pre-2026-06-01 `asset_put` rows (`task_id="asset:<name>"`) are forward-only, not migrated. Reads are unaudited.
- **Blobs, not KB** — don't dump markdown that belongs in the KB into `artifacts/`. Dir is created at both fresh-org init and lifespan startup (both paths required).

## Revisit (founder recovery)

`happyranch revisit <task-id>` spawns a NEW root task inheriting brief + team from a terminal predecessor; old lineage is frozen. TTY-gated; no `--yes` bypass. Spec: `docs/superpowers/specs/2026-04-21-opc-revisit-design.md`.

Eligible predecessor states: `failed`, `failed-cancelled` (founder-cancelled, normalized on the wire), `blocked(escalated)`, or `completed`. Anything else → `409 cannot_revisit`.

**Load-bearing invariants:**

- **`revisit_of_task_id` is a sideways reference, NOT an ancestor edge.** It lives in two places: the indexed column on `tasks` AND a richer `audit_log` row (`flagged`, `cascade`, `founder_note`, `prior_status`). `walk_ancestors` MUST NOT follow the column, or cascade-fail will re-poison revisits via `_enqueue_parent_if_waiting`. Helpers: `Database.walk_revisit_chain` (backward), `Database.get_direct_revisits` (forward).
- **Per-task overrides copied to revisit roots, narrowly.** `run_step` auto-revisits on opaque-failure recovery; only `session_timeout_seconds` is copied. `dispatched_from_thread_id` and `blocked_on_job_ids` are deliberately NOT copied — the founder/system retry overrides those.

## Session-timeout auto-route

Auto-revisit on opaque agent failures (subprocess timeout, no-completion-callback, executor crash, rate-limit, agent exception) is the system's silent retry path; this section documents the per-kind cap + cascade-fail-suppression shape. Spec: `docs/superpowers/specs/2026-05-25-session-timeout-auto-route-design.md`. Founder-ratified at TALK-037.

**Failure kinds** (`run_step._classify_failure_kind`, see source for exact match strings): `session_timeout`, `no_callback` (`success=True and report is None`), `rate_limit`, `executor_error` (non-zero returncode), `agent_exception`; `session_failed` is the novel-mode fallback. A sixth, `daemon_restart`, is injected by `_sweep_on_startup` when post-restart recovery force-fails an `IN_PROGRESS` task — routed through the same triad as in-process sites (replaces an older "wake the parent" path that poisoned the children list, TASK-687). v1 routes all kinds identically.

**Inline traps** (full catalog: spec §10):

- **Per-kind cap, not global** — `_AUTO_REVISIT_CAP_PER_KIND = 2`; same-kind exhausts independently. Reverting to a global cap masks real bugs behind transient infra noise.
- **Call order at opaque-failure sites** — `_maybe_spawn_auto_revisit` MUST run BEFORE `_enqueue_parent_if_waiting` (both `run_step_impl` branches + the sweep), because the cascade notification gate threads through `root_auto_revisit_spawned`. The old order caused 13+ ceremonial Feishu pings (TALK-037).
- **`failure_kind` lives top-level on `auto_revisit_of` payloads, NOT under `error_context`** — `_count_prior_auto_revisits_by_kind` does a flat lookup.
- **Cascade still fails ancestors** when `root_auto_revisit_spawned=True`; only the Feishu notification is suppressed. The new revisit root is an independent retry tree.
- **Sweep dedups per-restart** via `revisited_roots: set[str]` (≤1 auto-revisit per predecessor root per sweep); counter-style dedup misses this. Degraded mode (`orchestrator=None`, tests only) skips auto-revisit/cascade/notify — don't add a "wake the parent" fallback.

## Thread broadcast routing (addressing model)

Every `kind=message` written to a thread mints a `REPLY` invocation for every participant except the speaker. There is no `addressed_to` field, `@all` token, or `@founder` token — all participants receive an invocation on every message. Agents triage via a decline-by-default doctrine injected into the `REPLY` invocation prompt; declines are silent (no transcript row, no turn increment). The founder participates via the web UI exclusively — there are no in-thread Feishu pings (Feishu is used only for task escalations, failures, and job requests, not for ongoing thread conversation). Spec: `docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md`.

**Inline traps:**

- **Broadcast is unconditional, declines are silent** — the mint loop excludes only `speaker_name`; no opt-out. `decline` returns 200 but writes no row and bumps no turn counter (`responder_status` tracks `pending|replied|declined|failed`).
- **Doctrine is prompt-injected** (the `REPLY`-only "Decline-by-Default" section), not in `protocol/skills/thread/SKILL.md`.
- **Agent replies enforce the same `turn_cap` as founder `/send`** — projects `turns_used + 1` before the DB lock, `429` if it would exceed. Without it, agent ping-pong blows past the cap silently.

## Thread agent-session resume (turn 2+ via `--resume`)

Claude-backed thread participants reuse their Claude session across turns instead of re-shipping the full transcript + workspace `CLAUDE.md` every invocation. Per-`(thread, agent)` state lives in two columns on `thread_participants`: `agent_session_id` (the resumable session; executor-neutral name) and `last_resumed_seq` (the delta watermark). Issue #53. Implementation: `runtime/daemon/thread_runner.py` (`build_thread_delta_prompt`, `_is_session_not_found`, resume wiring in `run_invocation`), `runtime/orchestrator/executors.py` (`ClaudeExecutor.run` `resume_session_id` param + `ExecutorResult.agent_session_id` + `_parse_claude_session_id`), `runtime/infrastructure/database.py` (`get_thread_session` / `update_thread_session`), `runtime/infrastructure/audit_logger.py` (`log_agent_session_reused` / `log_agent_session_evicted_fallback`). Plan: `docs/superpowers/plans/2026-06-02-thread-claude-session-resume.md`.

**Inline traps** (full catalog: plan):

- **Claude-only optimization, never a correctness dependency** — gated on `executor_name == "claude"`; the SQLite transcript is canonical. Any parse miss, eviction, or non-Claude executor silently falls back to a full-context fresh session. Storage/audit names are generic so other executors are additive later.
- **`last_resumed_seq` advances ONLY on a successful subprocess** — a failed turn leaves the watermark untouched so the next resume re-includes the skipped messages. `--resume` may fork a new id, so always persist `result.agent_session_id` from each successful turn (not the id passed in).
- **Concurrency safety is an explicit per-`(thread, agent)` `asyncio.Lock`** (`_session_lock`), NOT the queue — the 4-worker pool can dispatch two turns for the same participant in parallel. The lock wraps read→run→update; without it both `--resume` the same session and race `last_resumed_seq`. `nullcontext` for non-Claude. Don't move state mutation outside it.
- **Eviction fallback re-runs the executor once within `run_invocation`, then clears `resume_sid`** (the failed resume never consumed the single-use token). Audits `agent_session_evicted_fallback`, not `agent_session_reused`. Detection `_is_session_not_found` is best-effort substring matching — verify markers against the real CLI.
- **`ExecutorResult.agent_session_id` ≠ `ExecutorResult.session_id`** (the latter is the `sess-<uuid>` for SessionTracker/`/cancel`). Schema columns live in BOTH the CREATE TABLE and the idempotent ALTER block.

## Thread task-followup (system bridges task terminal → thread)

When a task dispatched from a thread reaches its true terminal state, `_maybe_post_thread_followup` (`runtime/orchestrator/run_step.py`) appends a `task_completed` or `task_failed` SYSTEM message to the originating thread and mints a fresh invocation with purpose `TASK_FOLLOWUP` so the dispatching agent can compose the result-bearing reply it promised. Spec: `docs/superpowers/specs/2026-05-28-thread-task-followup-design.md`.

**Inline traps** (full catalog: spec §Non-obvious):

- **Call order** — the helper runs *after* `_maybe_spawn_auto_revisit` (the predicate ignores FAILED-with-spawned; the revisit chain re-enters at a later terminal).
- **Only root tasks fire** — `parent_task_id is not None` short-circuit, else every child completion spams the thread. Child terminals reach the helper transitively via `_enqueue_parent_if_waiting`.
- **Dispatcher identity reads from the `task_dispatched` audit row, not the task** — no column. Missing row → `thread_followup_skipped(reason=dispatcher_unresolved)`, don't guess.
- **Cross-thread enqueue uses the main loop** — bridge via `asyncio.run_coroutine_threadsafe(queue.put(job), main_loop)` (`run_step` is on a worker thread). Loop ref set at lifespan via `attach_thread_queue`; if unset (test orchestrators), audits `thread_followup_skipped(reason=enqueue_unavailable)`.

## Thread / talk dispatch self-only rule

Both `/threads/{id}/dispatch` and `/talks/{id}/dispatch` reject any call
where `effective_target != dispatcher`. The doctrine is "threads/talks are
coordination surfaces; iterative work lives in task trees." Spec:
`docs/superpowers/specs/2026-05-28-thread-talk-self-dispatch-only-design.md`.

**Inline traps** (full catalog: spec §Non-obvious):

- **Applies to managers AND workers uniformly** — don't re-introduce a manager carve-out (THR-010 footgun); the self-only check supersedes the removed `target_not_in_team` branch.
- **Doctrine is system-prompt-injected** via `_thread_talk_dispatch_doctrine_section()`; its reserved header is in `_RESERVED_AGENT_BODY_HEADERS` so an agent body can't collide. Shared error hint `SELF_DISPATCH_HINT` (`routes/_doctrine.py`) — both routes import it, keep in sync.

## Jobs (founder-approved + agent-autonomous)

Per-org `jobs` SQLite table; per-org files at `<runtime>/orgs/<slug>/jobs/JOB-NNN.{out,err,script}`. Spec: `docs/superpowers/specs/2026-05-26-jobs-design.md`. Implementation: `runtime/daemon/routes/jobs.py` (HTTP), `runtime/daemon/jobs_runner.py` (subprocess + stream pumps + shutdown cleanup), `runtime/infrastructure/database.py` (table + state-transition methods), `runtime/infrastructure/audit_logger.py` (`log_job_*` methods).

Routes under `/api/v1/orgs/{slug}/jobs/`: `POST /submit` (agent callback; auth via session-binding chain OR talk-path), `GET /`, `GET /{id}`, `POST /{id}/run`, `POST /{id}/reject`, `GET /{id}/output`, `GET /{id}/events` (SSE). The `submit` route is in the OpenAPI EXCLUDED set; everything else is mirrored in `web/src/lib/api/jobs.ts`.

**Inline traps** (full catalog: spec §Non-obvious):

- **Agent identity derives from auth context, never the payload's `agent` field** — `task.assigned_agent` or `talk.agent_name`. Prevents mis-attribution.
- **Two mutually-exclusive auth paths** — (task_id + session_id) XOR talk_id, via `SubmitBody._exactly_one_auth_path` + `_enforce_session_or_bearer` on dual-router endpoints. `task_id` column is overloaded as scope id (TASK-NNN / TALK-NNN); `submitted_from_talk_id` is the explicit flag.
- **`review_required` / `persistent` are honor-system on submit** — the daemon does not introspect the script against `allow_rules`. Don't add daemon-side validation without re-litigating the spec tradeoff.
- **Auto-resume on terminal supersedes founder revisit for blocked-on-job tasks** — `happyranch revisit` is now a founder override ("give up on JOB-X"), not the unblock path. (Shutdown SIGTERMs + awaits runners before DBs close; output is two-layer — disk + 65 KB DB head.)

## Task blocked-by-job (system auto-resumes from job terminals)

Per-org `tasks.blocked_on_job_ids` (JSON text column) + new `BlockKind.BLOCKED_ON_JOB`. Spec: `docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md`. Implementation across `runtime/orchestrator/run_step.py` (entry-state branch + block-on-jobs branch in self-blocked handler + CAS-win audit + read-only `_maybe_resume_blocked_task` helper + `_blocked_jobs_resume_header_if_applicable`), `runtime/daemon/jobs_runner.py` (caller A bridge via `fire_resume_check_for_job`), and `runtime/daemon/app.py` (caller C startup recovery scan).

**Inline traps** (full catalog: spec §Non-obvious):

- **State transitions are owned by `run_step_impl`, NOT the route or resume helper** — `_maybe_resume_blocked_task` is read-only (predicate + enqueue); the reverse flip goes through the existing CAS `try_claim_for_step`. No new state-mutation primitives.
- **Three resume callers must stay symmetric** — A: jobs-runner terminal hook, B: immediate check in the block-on-jobs branch (closes the fast-job race), C: startup recovery. All read-only; state flips at the CAS.
- **Predicate is ALL-terminal, not ANY** — resumes only when every job is in `{completed, failed, rejected}`.
- **`metadata` is a function parameter, NOT shared state** — thread `{trigger, triggering_job_id}` through `TaskQueue.enqueue(metadata=...)`; an `Orchestrator`-level stash races under concurrent triggers.

## Feishu notifications (founder push + reply-to-unblock)

Per-org opt-in via `feishu_notifications` in `<runtime>/orgs/<slug>/org/config.yaml`. Credentials (`app_id`, `app_secret`) are required when `enabled: true` and live in the same file — treat it as secret-bearing (`chmod 600`, never commit). Specs: `docs/superpowers/specs/2026-05-08-feishu-notification-design.md`, `docs/superpowers/specs/2026-05-12-feishu-interactive-actions-design.md`. Setup runbook: `docs/setup/feishu-notifications.md`.

**Entry points** — Outbound: `Orchestrator.notify_escalated` / `notify_failed` (fire-and-forget); `EscalationNotifier` mints `escalation_notifications` rows keyed by `message_id` (send failure audits + swallows, no row). Inbound: `FeishuEventListener`, one WS per org, bridges to the asyncio loop via `run_coroutine_threadsafe`. Reply routing is an 8-step pipeline in `_handle_event_async` (dedup → chat-id filter → `root_id`/`allow_dispatch` gate → drop bot-self → resolve → consume + audit). Trust boundary is `chat_id` only — no per-user authz in v1.

**Critical invariant:** the lifespan wrapper `_resolve_for_listener` in `app.py` MUST NOT swallow exceptions from the in-process resolvers. On failure (e.g. `409 task_not_escalated` because the founder used the CLI first) it records `outcome="rejected", reason="handler_exception"` and leaves the row unconsumed — the reply is preserved, not silently lost.

**Optional features** — `notify_on_failure` (gated in `_notify_failure_if_eligible` on enabled + not cancelled + no auto-revisit), `allow_dispatch` (top-level DISPATCH → `dispatch_via_feishu`, `DispatchError(reason ∈ {empty_brief, unknown_team, dispatch_failed})`), and Jobs (`APPROVE`/`REJECT\n<reason>` routes; `kind="job_request"`, JOB-NNN in the `task_id` column; spec `docs/superpowers/specs/2026-05-25-feishu-script-request-notifications-design.md`). CLI fallbacks consume any open row with `consumed_by="cli-fallback"`, so a CLI-first resolution silently no-ops the later Feishu reply.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **happyranch** (13547 symbols, 30267 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

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
| `gitnexus://repo/happyranch/context` | Codebase overview, check index freshness |
| `gitnexus://repo/happyranch/clusters` | All functional areas |
| `gitnexus://repo/happyranch/processes` | All execution flows |
| `gitnexus://repo/happyranch/process/{name}` | Step-by-step execution trace |

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
