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
- **Agent executor**: Per-agent. Claude Code (`claude -p ... --permission-mode auto`), Codex (`codex exec --json -`), and opencode (`opencode run`) are supported — no third-party agent framework dependency.
- **Daemon**: FastAPI HTTP service (`src/daemon/`) — serves orchestrator work, SSE task events, agent callbacks
- **CLI**: Thin HTTP client (`src/client/`) that talks to the daemon over localhost
- **Web UI**: Localhost SPA bundled into the daemon (`web/` → built to `web/dist/` → served at `/`). React 18 + TypeScript strict + Tailwind v4 + TanStack Query v5 + React Router v6. Auth via the same bearer token at `~/.happyranch/daemon.token`, fetched once via `GET /api/v1/auth/bootstrap` (localhost-gated). Architecture: `web/ARCHITECTURE.md`. Spec: `docs/superpowers/specs/2026-05-14-web-ui-design.md`. Launch with `happyranch web`.
- **Agent workflow**: Shared workspace skills (`protocol/skills/`) — `start-task`, `make-worktree`, `manage-repo`, `manage-agent`, `dispatch`, `jobs`, `talk`, `thread`. The orchestrator prompt references the same SOPs across all executors.
- **Orchestrator**: Custom Python application. `run_step` is the only primitive — each invocation advances one task by one subprocess call; an async `TaskQueue` + worker pool (`src/daemon/queue.py`) drives re-enqueues across steps. The team manager drives decisions. Implicit `review_verdict` audit rows are written when a delegation terminates (approved / rejected) — the founder reviews those via `happyranch audit` to identify which agents need attention.
- **Data models**: Pydantic v2 + pydantic-settings
- **Database**: SQLite with WAL mode, per-org under `<runtime>/orgs/<slug>/happyranch.db`. Schema covers audit logs and task state, plus per-feature tables (token usage, Feishu correlation, threads) documented in the corresponding specs under `docs/superpowers/specs/`.
- **Feishu integration**: `lark-oapi>=1.6,<2` (official ByteDance SDK) — outbound `im.v1.message.create` via `src/infrastructure/feishu/`; inbound WS subscription to `im.message.receive_v1` via `src/daemon/feishu_listener.py`.
- **Knowledge base**: File-backed markdown under `<runtime>/orgs/<slug>/kb/` with atomic writes, substring/tag search, `_index.md` regeneration. No vector store yet.
- **LLM**: Provider depends on the selected executor
- **Hosting**: Local Mac Mini

## Directory Layout

```
~/projects/my-opc/                     # Source repo
|-- protocol/                          # System kernel docs (00, 05*, 06) + shared agent skills
|-- scripts/daemon.sh                  # Launch the FastAPI daemon
|-- src/
|   |-- cli.py                         # `happyranch` command — HTTP client
|   |-- client/                        # httpx-based client + SSE streaming
|   |-- daemon/                        # FastAPI app, routes, queue, sessions, Feishu listener
|   |-- orchestrator/                  # run_step, executors, capabilities, performance, prompt_loader
|   `-- infrastructure/                # database, audit_logger, kb_store, talk_store, learnings_store, feishu/
|-- tests/                             # Unit + integration (with fake CLIs)
`-- examples/orgs/hk-macau-tourism/    # Canonical sample org tree

~/.happyranch/                                # Daemon home — auth_token, runtimes.yaml, daemon.pid, daemon.port

<runtime-dir>/                         # Slugless multi-org container (created by `happyranch init <path>`)
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
    `-- assets/                        # org-shared blob store (put/list/get via `happyranch assets`)
```

HTTP routes: per-org under `/api/v1/orgs/<slug>/...`; container-level under `/api/v1/runtime` and `/api/v1/orgs`. Only `schema_version: 2` is supported — older single-org (v1) and DB-backed enrollment (v0) runtimes are rejected at startup with a re-init hint.

## Configuration

Operational settings use the `HAPPYRANCH_` env prefix. Runtime paths are derived from the runtime directory.

| Variable | Default | Description |
|----------|---------|-------------|
| `HAPPYRANCH_CLAUDE_CLI_PATH` | `claude` | Path to Claude Code CLI |
| `HAPPYRANCH_CODEX_CLI_PATH` | `codex` | Path to Codex CLI |
| `HAPPYRANCH_OPENCODE_CLI_PATH` | `opencode` | Path to opencode CLI |
| `HAPPYRANCH_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `HAPPYRANCH_PROTOCOL_DIR` | `protocol` | Protocol docs dirname (relative to project root) |
| `HAPPYRANCH_MAX_ORCHESTRATION_STEPS` | `50` | Max manager decision steps before escalation |
| `HAPPYRANCH_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout — global default; see resolution below |
| `HAPPYRANCH_ORG_SLUG` | _(unset)_ | Default org slug for per-org CLI commands. Resolution: explicit `--org` flag > `HAPPYRANCH_ORG_SLUG` env > auto-infer (only if exactly one org exists) > error |

### Session timeout resolution

`Orchestrator._resolve_session_timeout(agent_name, task_id=...)` walks three layers, highest precedence first:

1. **Task override** — `tasks.session_timeout_seconds` column, set via `happyranch revisit ... --session-timeout-seconds N` and inherited by every child spawned from that task.
2. **Org override** — `session_timeout_seconds:` in `<runtime>/orgs/<slug>/org/config.yaml` (loaded by `src/orchestrator/org_config.py`).
3. **Code default** — `Settings.session_timeout_seconds` (1800s; overridable via `HAPPYRANCH_SESSION_TIMEOUT_SECONDS`).

Positive integers only; `<= 0` or non-int raises at parse time. The `agent_name` argument is unused (kept for call-site symmetry); legacy `session_timeout_seconds` in agent frontmatter is silently ignored.

### Agent executors

Each workspace declares an `executor` in `agent.yaml`: `claude`, `codex`, or `opencode`. Missing values default to `claude`. All three share the same `protocol/skills/` tree. Workspace differences:

| | bootstrap doc | skills dir | permission surface |
|--|--|--|--|
| Claude | `CLAUDE.md` | `.claude/skills/` | `permissions.allow` in `.claude/settings.json` **AND** `--allowedTools` on CLI (both required, see below) |
| Codex | `AGENTS.md` | `.agents/skills/` | sandbox flags on CLI |
| opencode | `AGENTS.md` | `.agents/skills/` | `opencode.json` `permission.bash` map |

**Codex sandbox**: `CodexExecutor.run` passes `-c sandbox_workspace_write.network_access=true` on every invocation. The `workspace-write` sandbox blocks localhost by default, which would kill the agent's `happyranch report-completion` callback to `127.0.0.1`. Do not remove this flag without re-architecting the callback path away from localhost sockets.

**opencode permissions**: `OpencodeWorkspaceAdapter.write_opencode_json` writes a strict default — `{"permission": {"bash": {"*": "deny", "happyranch *": "allow", ...per-agent allow_rules...}}}`. **Do not pass `--dangerously-skip-permissions` on the CLI** — it bypasses `opencode.json` and erases the per-prefix discipline.

Enrolling a non-Claude worker: set `"executor": "codex"` (or `"opencode"`) in the `happyranch manage-agent --from-file` payload. Founder approval (`happyranch approve-agent`) bootstraps the right surface for the chosen executor. See `protocol/skills/manage-agent/SKILL.md` for full payload shapes.

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

Both surfaces are generated from `allow_rules_for_agent(agent_name, cli=...)` in `src/orchestrator/workspace_adapters.py` (settings uses `Bash(<cmd>:*)`; CLI uses `Bash(<cmd> *)`). **Do not hand-edit either** — `happyranch init-agent` rewrites them. The two-surface requirement exists because Claude Code 2.1.x ignores `permissions.allow` in headless `-p` mode; without the CLI flag, the agent's first `happyranch ...` call is blocked and the task silently rejects.

**When adding new orchestrator capabilities, keep them under the `happyranch` binary** so they stay inside the baseline allow rule. Only add a raw-tool prefix when the operation genuinely cannot be wrapped in `happyranch` (e.g., third-party CLI for external infra we don't own).

**Agent-side completion payloads must be single-line `happyranch` invocations.** The Claude permission matcher treats newlines (and `&&`, `||`, `;`, `|`) as command separators and matches each subcommand independently; multi-line bash with backslash continuations is rejected even when the surface command is `happyranch ...`. The `start-task` skill writes payloads to `/tmp/completion-<task_id>.json` and invokes `happyranch report-completion --from-file <path>` as a single line. Any new agent-facing callback with multiple arguments must follow the same `--from-file` pattern.

## Conventions

**Code style** — Type hints on all function signatures. `from __future__ import annotations` in every source file. Pydantic v2 for structured data, StrEnum for enumerations (agent names are plain strings — agents are discovered dynamically from `<runtime>/orgs/<slug>/org/agents/*.md`). Tests for business logic (escalation rules, audit-log shape).

**Docs split** — `README.md` is for end users (setup, CLI commands, configuration). `CLAUDE.md` (this file) is for developers and AI agents working on the codebase. Design docs in `protocol/` and specs in `docs/superpowers/specs/` are the source of truth for behavior — keep agent system prompts in sync.

**Starting a new feature** — Read the relevant design doc first (e.g., `protocol/05c-orchestrator.md`). Follow existing patterns in `src/orchestrator/`. Write tests alongside implementation.

## Org content APIs

`AgentDef` (`src/orchestrator/agent_def.py`) is the in-memory representation of an agent file: markdown-with-YAML-frontmatter, parsed/rendered by `parse_agent_text` / `render_agent_text`. Fields: `name`, `team`, `role` (worker|manager), `executor` (claude|codex|opencode), `description`, `allow_rules`, `repos`, `enrolled_by`, `enrolled_at_task`, `enrolled_at`, `system_prompt` (body). **No `session_timeout_seconds` field** — see resolution above.

`src/orchestrator/prompt_loader.py` is the only API for reading/writing agent files: `load_agent`, `list_agents`, `list_pending`, `write_pending_agent`, `approve_agent`, `reject_agent`. Routes (`src/daemon/routes/agents.py`) and the orchestrator all read through this module against the per-org root.

`TeamsRegistry` (`src/orchestrator/teams.py`) is seeded from `teams.yaml` and auto-persists on `add_worker` / `remove_worker`. There is no `DEFAULT_LAYOUT` — an org without `teams.yaml` is treated as empty.

## Task status vocabularies

Agents self-report `status="completed"|"blocked"` via `happyranch report-completion` (the worker's view of its session). The orchestrator-owned `TaskStatus` lives on the `tasks` row and is distinct: `{pending, in_progress, blocked, completed, failed}` based on orchestration classification, with `block_kind` (`delegated` | `escalated` | `blocked_on_job`) specifying the reason.

## Manager decision contract

Team-manager completion payloads carry two fields with distinct purposes:

- **`summary`** (prose) — human-readable description of what the manager did or concluded this step. Rendered in `happyranch details`, audit logs, `task_history.md`. Stored on `task_results.output_summary`.
- **`decision`** (JSON object, NextStep schema) — the structured action the orchestrator will execute: `{"action": "delegate"|"done"|"escalate", ...}`. Stored on `task_results.decision_json` (manager-only column; workers leave NULL). Parsed by `Orchestrator._parse_next_step` directly — no prose inference.

Full schema with worked examples lives in `protocol/00-completion-contract.md` ("Manager decision field"). The decision-field name for a delegated child task's brief is **`prompt`, not `brief`** — Pydantic v2 silently ignores extras, so writing `"brief"` produces an empty-brief child task.

## Inline delegation chains

A manager can declare a multi-leg workflow in one `delegate` decision using `NextStep.then` (list of `ChainLeg`) and optional per-leg `expect_verdict` gates. The orchestrator auto-advances to the next leg whenever a child terminates COMPLETED with a matching verdict, without consuming the manager's step budget. Spec: `docs/superpowers/specs/2026-05-30-inline-delegation-chain-design.md`. Protocol: `protocol/00-completion-contract.md` ("Inline delegation chains"). Implementation: `src/orchestrator/chain.py` (pure-logic state model + helpers) wired from `src/orchestrator/run_step.py`.

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

**Load-bearing invariants:**

- **Cross-team validation runs on every leg at decision-parse time.** Any off-team agent rejects the entire decision via the existing `_parse_next_step` feedback path — you cannot declare a chain and have one leg silently skipped.
- **Auto-advances do NOT consume orchestration steps.** Declaring a chain costs 1 step; the final-leg wake costs 1 step. A clean 3-leg workflow costs 2 steps instead of 4 (one manager wake per leg).
- **Final-leg match still wakes the manager.** Chains never auto-`done`; the manager reviews the outcome and decides. Don't add a chain-terminal auto-done shortcut without re-litigating this in the spec.
- **Mismatch or blocked child clears `active_chain` and wakes the manager.** `compute_advance_action` returns `kind="wake"` with `reason ∈ {"child_blocked", "verdict_mismatch", "chain_complete"}`. The orchestrator logs a `chain_auto_advance` or `chain_wake_manager` audit row accordingly.
- **Non-first legs receive a "Prior leg context" suffix.** `build_prior_leg_context` appends the upstream worker's summary, verdict, confidence, and `artifact_dir` to the leg's manager-authored brief. Don't pre-embed upstream context in the authored prompt.
- **Chain state is serialized JSON on `tasks.active_chain`.** `ChainState` holds `step_index`, `first_leg_expect_verdict`, `legs`, and `step_audit_id` (the audit row that spawned the chain, used for crash recovery). Written before the child is enqueued so a crash leaves the chain visible.
- **`active_chain` is exposed in the task detail response.** `GET /api/v1/orgs/{slug}/tasks/{id}` returns a `parsed_active_chain` field (`ChainState` deserialized, or null). The web UI renders a "Current workflow chain" strip from it.

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

**Full founder-facing CLI** — tasks, agents, KB, threads, talks, audit, assets, runtime, migrations — is documented in `skills/happyranch/SKILL.md` (symlinked at `~/.claude/skills/happyranch`).

**Agent-side callbacks** (invoked by skills inside agent sessions; do NOT invoke by hand — they falsify audit data):

- `happyranch report-completion` — terminal callback from the `start-task` skill
- `happyranch progress` — long-running mid-task heartbeat
- `happyranch learning {add,update,promote,reindex}` on migrated workspaces; legacy `happyranch learning --text` on pre-migration
- `happyranch manage-agent`, `happyranch manage-repo`, `happyranch dispatch`
- `happyranch threads {reply,decline,dispatch}`

All use `--from-file <path>` — see "Agent permission model" for why.

## Knowledge Base

Per-org under `<runtime>/orgs/<slug>/kb/` (orgs do not share a KB). One entry shape — `KBEntry.type` is freeform; route validation only enforces non-empty `slug/title/type/topic`. The dedicated `kb precedent` route was removed; founder rulings flow through plain `happyranch kb add` with `source_task: <task-id>` in frontmatter. Implementation: `src/infrastructure/kb_store.py` + `src/daemon/routes/kb.py` (atomic writes, `kb_lock`, substring/tag search, `_index.md` regen). Full rules: `protocol/06-knowledge-base.md`. The context builder injects a "Knowledge Base" section into every agent's bootstrap doc; `start-task` has explicit consult + contribute steps.

## Per-Agent Learnings

Per-agent under `<runtime>/orgs/<slug>/workspaces/<agent>/learnings/`, one `LRN-NNN-<slug>.md` per entry. Full spec: `docs/superpowers/specs/2026-05-13-per-agent-learnings-structural-upgrade-design.md`. Implementation: `src/infrastructure/learnings_store.py` + the `/agents/{name}/learnings/entries/...` block in `src/daemon/routes/agents.py`. CLI: `happyranch learning list|get|search|add|update|promote|reindex`.

**Load-bearing invariants** (full catalog: spec §Non-obvious):

- **Per-workspace migration is state-aware** — `PersistentWorkspaceSetup.ensure()` never creates `learnings/` when a non-empty flat `learnings.md` exists. Existing agents stay on the legacy shape until a founder-dispatched migration moves them; new workspaces start on the new layout.
- **Cross-refs validated at write time** — `related_to` / `supersedes` against existing IDs (unknown → 400); self-refs rejected. `supersedes` is the canonical evolve-a-rule primitive.
- **Promotion to KB is one-way** — `happyranch learning promote <LRN-NNN> --kb-slug <slug>` replaces the body with a 2-line pointer stub and locks the entry.

## Shared Assets (org-wide blob store)

Per-org at `<runtime>/orgs/<slug>/assets/`. Flat directory of opaque files —
persistent artifacts produced by any agent and visible to every other agent
in the same org. Implementation: `src/infrastructure/asset_store.py` +
`src/daemon/routes/assets.py`. CLI: `happyranch assets {put,list,get}`.

**Load-bearing invariants** (full catalog: plan §Non-obvious):

- **CLI-only access by design** — Codex (`workspace-write` sandbox) and Opencode (bash deny-by-default) block direct writes outside the agent's workspace; only the `happyranch` baseline allow-rule works across all three executors. Don't add a "just `cat`/`cp` it" agent skill.
- **Audit `task_id` overload** — `asset_put` writes `f"asset:{name}"` (the `asset:` prefix is mandatory) so asset names like `TASK-123` or `TALK-7` can't pollute the task/talk scopes consumed by `Database.get_audit_logs(task_id)`. Reads (`list`/`get`) are unaudited by design.
- **Not the KB** — assets are blobs. KB is for typed/structured knowledge (frontmatter, slug, type, topic). Don't dump markdown content into `assets/` that should be a KB entry.
- **Dir created at fresh-org init AND idempotently at lifespan startup** for orgs that pre-date the feature. Both code paths are required.

## Revisit (founder recovery)

`happyranch revisit <task-id>` spawns a NEW root task inheriting brief + team from a terminal predecessor; old lineage is frozen. TTY-gated; no `--yes` bypass. Spec: `docs/superpowers/specs/2026-04-21-opc-revisit-design.md`.

Eligible predecessor states: `failed`, `failed-cancelled` (founder-cancelled, normalized on the wire), `blocked(escalated)`, or `completed`. Anything else → `409 cannot_revisit`.

**Load-bearing invariants:**

- **`revisit_of_task_id` is a sideways reference, NOT an ancestor edge.** It lives in two places: the indexed column on `tasks` AND a richer `audit_log` row (`flagged`, `cascade`, `founder_note`, `prior_status`). `walk_ancestors` MUST NOT follow the column, or cascade-fail will re-poison revisits via `_enqueue_parent_if_waiting`. Helpers: `Database.walk_revisit_chain` (backward), `Database.get_direct_revisits` (forward).
- **Per-task overrides copied to revisit roots, narrowly.** `run_step` auto-revisits on opaque-failure recovery; only `session_timeout_seconds` is copied. `dispatched_from_thread_id` and `blocked_on_job_ids` are deliberately NOT copied — the founder/system retry overrides those.

## Session-timeout auto-route

Auto-revisit on opaque agent failures (subprocess timeout, no-completion-callback, executor crash, rate-limit, agent exception) is the system's silent retry path; this section documents the per-kind cap + cascade-fail-suppression shape. Spec: `docs/superpowers/specs/2026-05-25-session-timeout-auto-route-design.md`. Founder-ratified at TALK-037.

**Failure kinds** (`run_step._classify_failure_kind`): `session_timeout` (`error.startswith("Session timed out after")` — written by `executors.py:197`), `no_callback` (`success=True and report is None`, the TASK-045 class), `rate_limit` (substring `"hit your limit"` + `"reset"` OR `"rate limit"` in any of error / stdout_tail / stderr_tail), `executor_error` (non-zero `returncode`), `agent_exception` (exception escapes `_run_agent`). The triad `_SESSION_TIMEOUT_CLASS = {"session_timeout", "no_callback", "rate_limit"}` is a routing-class predicate exposed for future per-class policy; v1 routes all five kinds identically. `session_failed` is the defensive fallback for novel modes.

**Load-bearing invariants** (full catalog: spec §10):

- **Per-kind cap, not global** — `_AUTO_REVISIT_CAP_PER_KIND = 2` in `run_step.py`. Same-kind exhausts independently; a chain that hits one `session_timeout` then one `executor_error` still has budget for another of each. Reverting to a global cap would mask real bugs behind transient infra noise.
- **Call order at opaque-failure sites** — `_maybe_spawn_auto_revisit` MUST run BEFORE `_enqueue_parent_if_waiting` at both branches of `run_step_impl`, because the cascade-fail's notification gate threads through `root_auto_revisit_spawned`. The old order caused 13+ ceremonial founder Feishu pings (TALK-037).
- **`failure_kind` lives top-level on `auto_revisit_of` audit payloads, NOT nested under `error_context`.** `_count_prior_auto_revisits_by_kind` does a flat lookup; nesting it would slow counting and require parser changes.
- **Cascade still cascade-fails ancestors** even when `root_auto_revisit_spawned=True`; only the Feishu notification is suppressed. The new root via `revisit_of_task_id` is an independent retry tree, not a continuation of the old lineage.

## Thread broadcast routing (addressing model)

Every `kind=message` written to a thread mints a `REPLY` invocation for every participant except the speaker. There is no `addressed_to` field, `@all` token, or `@founder` token — all participants receive an invocation on every message. Agents triage via a decline-by-default doctrine injected into the `REPLY` invocation prompt; declines are silent (no transcript row, no turn increment). The founder participates via the web UI exclusively — there are no in-thread Feishu pings (Feishu is used only for task escalations, failures, and job requests, not for ongoing thread conversation). Spec: `docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md`.

**Load-bearing invariants:**

- **Broadcast is unconditional** — `_resolve_addressed_agents` and `_verify_addressed` are removed; the mint loop in `routes/threads.py` iterates `thread_participants` and excludes `speaker_name`. No opt-out.
- **Declines are silent** — `decline` route returns 200 but writes no `thread_messages` row and increments no turn counter. `responder_status` on each message shows per-participant `pending|replied|declined|failed` state (via DB join on `thread_invocations.triggering_seq`).
- **Doctrine is prompt-injected, not skill-embedded** — the reply-vs-decline judgment is in the thread-invocation prompt's "Decline-by-Default" section (purpose `REPLY` only), not in `protocol/skills/thread/SKILL.md`. The skill covers operational mechanics only.
- **Agent replies enforce the same turn_cap as founder `/send`.** The reply endpoint (consumed by REPLY/BOOTSTRAP/TASK_FOLLOWUP invocations) projects `turns_used + 1` against `turn_cap` before the DB lock and raises `429 turn_cap_exceeded` if it would exceed. Without this, agent ping-pong can blow past the cap silently.

## Thread task-followup (system bridges task terminal → thread)

When a task dispatched from a thread reaches its true terminal state, `_maybe_post_thread_followup` (`src/orchestrator/run_step.py`) appends a `task_completed` or `task_failed` SYSTEM message to the originating thread and mints a fresh invocation with purpose `TASK_FOLLOWUP` so the dispatching agent can compose the result-bearing reply it promised. Spec: `docs/superpowers/specs/2026-05-28-thread-task-followup-design.md`.

**Load-bearing invariants** (full catalog: spec §Non-obvious):

- **Call order at opaque-failure sites** — the helper must run *after* `_maybe_spawn_auto_revisit`, because the predicate ignores FAILED-with-spawned (the revisit chain reaches a later terminal that re-enters the helper). Mirrors the `_maybe_spawn_auto_revisit` → `_enqueue_parent_if_waiting` order.
- **Only root tasks fire** — `parent_task_id is not None` short-circuit. Without it, every child completion in a dispatched task tree would spam the thread. Child terminals reach the helper transitively via `_enqueue_parent_if_waiting`'s `_fail(parent, ...)`.
- **Dispatcher identity reads from the `task_dispatched` audit row, not the task.** Do not add a column. Missing row → `thread_followup_skipped(reason=dispatcher_unresolved)`, don't guess.
- **Cross-thread enqueue uses the main loop** — thread queue is bound to the daemon's main asyncio loop; `run_step` runs on a worker thread. Bridge via `asyncio.run_coroutine_threadsafe(queue.put(job), main_loop)`. The orchestrator picks up the loop reference at lifespan startup through `attach_thread_queue(thread_queue, main_loop)`; if either is unset (test orchestrators without daemon context), the helper audits `thread_followup_skipped(reason=enqueue_unavailable)` and the minted invocation stays PENDING.

## Thread / talk dispatch self-only rule

Both `/threads/{id}/dispatch` and `/talks/{id}/dispatch` reject any call
where `effective_target != dispatcher`. The doctrine is "threads/talks are
coordination surfaces; iterative work lives in task trees." Spec:
`docs/superpowers/specs/2026-05-28-thread-talk-self-dispatch-only-design.md`.

**Load-bearing invariants** (full catalog: spec §Non-obvious):

- **Applies to managers AND workers uniformly.** Pre-2026-05-28 managers were exempted; THR-010 surfaced the exemption as a footgun. Don't re-introduce a manager carve-out under a different name — the self-only check supersedes the prior `target_not_in_team` branch (now removed).
- **Doctrine is system-prompt-injected** via `_thread_talk_dispatch_doctrine_section()` in `src/orchestrator/workspace_adapters.py`. The reserved header `"Thread and Talk Dispatch are Self-Only"` is registered in `_RESERVED_AGENT_BODY_HEADERS` so an agent `.md` body cannot author a colliding section. Don't duplicate via a per-org KB entry.
- **Shared error hint at `src/daemon/routes/_doctrine.py`** (`SELF_DISPATCH_HINT`). Both threads + talks routes import it; keep wording in sync.

## Jobs (founder-approved + agent-autonomous)

Per-org `jobs` SQLite table; per-org files at `<runtime>/orgs/<slug>/jobs/JOB-NNN.{out,err,script}`. Spec: `docs/superpowers/specs/2026-05-26-jobs-design.md`. Implementation: `src/daemon/routes/jobs.py` (HTTP), `src/daemon/jobs_runner.py` (subprocess + stream pumps + shutdown cleanup), `src/infrastructure/database.py` (table + state-transition methods), `src/infrastructure/audit_logger.py` (`log_job_*` methods).

Routes under `/api/v1/orgs/{slug}/jobs/`: `POST /submit` (agent callback; auth via session-binding chain OR talk-path), `GET /`, `GET /{id}`, `POST /{id}/run`, `POST /{id}/reject`, `GET /{id}/output`, `GET /{id}/events` (SSE). The `submit` route is in the OpenAPI EXCLUDED set; everything else is mirrored in `web/src/lib/api/jobs.ts`.

**Load-bearing invariants** (full catalog: spec §Non-obvious):

- **Agent identity is derived from auth context, never echoed from the payload's `agent` field.** Source: `task.assigned_agent` (task path) or `talk.agent_name` (talk path). Prevents mis-attribution.
- **Two mutually-exclusive auth paths** — (task_id + session_id) XOR talk_id, enforced by `SubmitBody._exactly_one_auth_path` and inline in dual-router endpoints (`/{id}`, `/tail`, `/stop`, `/wait`) via `_enforce_session_or_bearer`. Mirrors `manage-agent` / `threads.compose`.
- **`task_id` column is overloaded as scope id** — TASK-NNN on task path, TALK-NNN on talk path. Same overload as `audit_log.task_id` and `asset_put`'s `f"asset:{name}"`. The `submitted_from_talk_id` column is the explicit flag; downstream code passes `record.task_id` through without branching.
- **Shutdown awaits runner tasks** — `terminate_all_inflight` SIGTERMs then `asyncio.wait_for(gather(*runners), timeout=5)` so rows reach terminal before per-org DBs close. `recover_orphaned_running_jobs` at lifespan startup is the complementary safety net.
- **Output capture is two-layer** — full streams to disk (no v1 size cap), 65 KB head per stream mirrored to `stdout_head`/`stderr_head` DB columns for fast rendering. `GET /output` reads disk; the drawer + audit deep-link show DB head.
- **`review_required` and `persistent` are honor-system on submit.** The daemon does not introspect the script against `allow_rules`. Misclassification is recoverable via founder stop + audit + talk + learning. Don't add daemon-side validation without re-litigating the design tradeoff in the spec.
- **Auto-resume on terminal supersedes founder revisit for blocked-on-job tasks.** The 2026-05-28 task-blocked-by-job design reverses the original "no task wakes itself" non-goal. `happyranch revisit` is now a founder override ("give up on JOB-X, start over"), not the unblock path.

## Task blocked-by-job (system auto-resumes from job terminals)

Per-org `tasks.blocked_on_job_ids` (JSON text column) + new `BlockKind.BLOCKED_ON_JOB`. Spec: `docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md`. Implementation across `src/orchestrator/run_step.py` (entry-state branch + block-on-jobs branch in self-blocked handler + CAS-win audit + read-only `_maybe_resume_blocked_task` helper + `_blocked_jobs_resume_header_if_applicable`), `src/daemon/jobs_runner.py` (caller A bridge via `fire_resume_check_for_job`), and `src/daemon/app.py` (caller C startup recovery scan).

**Load-bearing invariants** (full catalog: spec §Non-obvious):

- **State transitions are owned by `run_step_impl`, NOT the route or the resume helper.** The route validates + persists; `_maybe_resume_blocked_task` is read-only (predicate-check + enqueue). The in-place `IN_PROGRESS → BLOCKED+BLOCKED_ON_JOB` happens in the self-blocked handler; the reverse goes through the existing CAS `try_claim_for_step`. No new state-mutation primitives.
- **Three resume callers must stay symmetric** — A: jobs-runner terminal hook (after DB commit, all three terminal branches + the rejection-from-notification path), B: immediate predicate check in `run_step_impl`'s block-on-jobs branch (closes the submit-time race for fast `review_required=false` jobs), C: startup recovery in lifespan (after `recover_orphaned_running_jobs`). All read-only; state flips at the CAS in `run_step_impl`.
- **Predicate is ALL-terminal, not ANY-terminal.** A task blocked on JOB-A+B+C resumes only when every one is in `{completed, failed, rejected}`. `triggering_job_id` in the audit payload is the one whose terminal closed the predicate (typically last to finish).
- **`metadata` is a function parameter, NOT shared state.** An earlier draft used `Orchestrator._pending_resume_metadata`; that races under concurrent triggers. Thread `{trigger, triggering_job_id}` through `TaskQueue.enqueue(metadata=...)` → `Orchestrator.run_step(task_id, metadata)`. Don't reintroduce a stash.

## Feishu notifications (founder push + reply-to-unblock)

Per-org opt-in via `feishu_notifications` in `<runtime>/orgs/<slug>/org/config.yaml`. Credentials (`app_id`, `app_secret`) are required when `enabled: true` and live in the same file — treat it as secret-bearing (`chmod 600`, never commit). Specs: `docs/superpowers/specs/2026-05-08-feishu-notification-design.md`, `docs/superpowers/specs/2026-05-12-feishu-interactive-actions-design.md`. Setup runbook: `docs/setup/feishu-notifications.md`.

**Entry points:**

- **Outbound** — `Orchestrator.notify_escalated` / `notify_failed`, loop-aware fire-and-forget. `EscalationNotifier` (`src/infrastructure/feishu/notifier.py`) mints `escalation_notifications` rows keyed by Feishu `message_id`. Send failures audit `escalation_notify_failed` and are swallowed; no row minted on send failure.
- **Inbound** — `FeishuEventListener` (`src/daemon/feishu_listener.py`), one WS connection per org. WS thread runs `lark.ws.Client.start()` (blocking) and bridges to the asyncio loop via `asyncio.run_coroutine_threadsafe`. Wired from FastAPI lifespan and `DaemonState.add_org`.

**Reply routing** (8-step pipeline in `_handle_event_async`, updating `processed_event_ids.outcome` on every branch): dedup → chat-id filter → require `root_id` (reply branch) OR `allow_dispatch=true` (top-level dispatch) → drop bot-self → resolve via `resolve_escalation_in_process` / `revisit_from_notification` / `dispatch_via_feishu` → consume row + audit. Trust boundary is `chat_id` only — no per-Feishu-user authorization in v1.

**Critical invariant:** the lifespan wrapper `_resolve_for_listener` in `app.py` MUST NOT swallow exceptions from the in-process resolvers. If resolution fails (e.g., `409 task_not_escalated` because the founder used the CLI first), the outer `try/except` records `outcome="rejected", reason="handler_exception"` and leaves the row unconsumed — the founder's reply is preserved instead of silently lost.

**Optional features:**

- `notify_on_failure: true` — failure replies; hook in `run_step.py:_notify_failure_if_eligible` gates on enabled + not cancelled + no auto-revisit spawned. Listener routes `(kind=failure, decision=revisit)` to `revisit_from_notification`.
- `allow_dispatch: true` — top-level DISPATCH messages parsed by `parse_top_level_message(text)`; `dispatch_via_feishu` raises `DispatchError(reason ∈ {empty_brief, unknown_team, dispatch_failed})`.
- **Jobs** — `submit_job` fires `notify_job_submitted`; `APPROVE` / `REJECT\n<reason>` reply routes; terminal triggers `notify_job_run_result`. Notification `kind="job_request"`; JOB-NNN lives in the `task_id` column (same `task_id`-column overload used by other non-task scopes). Spec: `docs/superpowers/specs/2026-05-25-feishu-script-request-notifications-design.md`.

CLI fallbacks (`happyranch resolve-escalation`, `happyranch revisit`) consume any open notification row for the task with `consumed_by="cli-fallback"`, so a CLI-first resolution silently no-ops the later Feishu reply.

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
