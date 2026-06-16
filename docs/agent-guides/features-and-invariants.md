# Features And Invariants

This file serves two purposes. The **Feature Modules Overview** below is an orientation map of the product's feature modules — each module is one short paragraph (what it does) plus a pointer to its authoritative spec or implementation. The per-surface sections after it are the original feature-specific traps, to be read only when touching the relevant surface; the overview points down to those sections where one exists rather than restating them.

For current behavior always prefer `protocol/`, `docs/agent-guides/`, tests, the OpenAPI snapshot, and implementation over the design specs — `docs/superpowers/specs/` is append-only design history unless `docs/superpowers/specs/README.md` marks a spec `current`.

## Feature Modules Overview

### Orchestration core

- **Orchestrator & task state machine.** The daemon-side loop that advances each task one step at a time, drives manager-decision turns, spawns children, and records terminal state. Spec `docs/superpowers/specs/2026-04-14-orchestrator-daemon-design.md`; current contract `docs/agent-guides/orchestrator-contracts.md`; impl `runtime/orchestrator/run_step.py`, `runtime/orchestrator/orchestrator.py`.
- **Manager-decision loop & completion contract.** Team managers end every turn with a `decision` (`delegate`/`done`/`escalate`); workers report a plain completion. Contract `protocol/00-completion-contract.md`; guide `docs/agent-guides/orchestrator-contracts.md`; impl in `runtime/orchestrator/run_step.py`.
- **Inline delegation chains.** A manager can declare a multi-leg worker chain inline via `then: [...]`; the orchestrator auto-advances routine legs on matching verdict without consuming orchestration steps. Spec `docs/superpowers/specs/2026-05-30-inline-delegation-chain-design.md` (current); impl `runtime/orchestrator/chain.py`.
- **Task status model.** The canonical task status vocabulary and transition rules (`in_progress`, `blocked`, `completed`, `failed`, etc.). Spec `docs/superpowers/specs/2026-04-19-task-status-redesign.md`; current vocabulary `docs/agent-guides/orchestrator-contracts.md`.
- **Subtask / composite tasks.** Worker-spawned bounded subtasks under a parent, for decomposing a single delegation into iterative steps. Spec `docs/superpowers/specs/2026-06-03-subtask-composite-task-design.md`; impl in `runtime/orchestrator/run_step.py`.
- **Revisit.** `happyranch revisit <task-id>` spawns a fresh root task inheriting brief and team from a terminal predecessor; old lineage freezes. Specs `docs/superpowers/specs/2026-04-21-opc-revisit-design.md`, `docs/superpowers/specs/2026-04-23-revisit-root-link-design.md`. See [Revisit](#revisit) below for traps.
- **Session-timeout auto-route.** Silent auto-revisit on opaque agent failures (timeout, no-callback, rate-limit, executor error, etc.), capped per failure kind. Spec `docs/superpowers/specs/2026-05-25-session-timeout-auto-route-design.md`. See [Session-Timeout Auto-Route](#session-timeout-auto-route) below for traps.
- **Cancel (race + actor attribution).** Founder/agent task cancellation with race-safe state handling and audit attribution of who cancelled. Specs `docs/superpowers/specs/2026-05-26-cancel-race-design.md`, `docs/superpowers/specs/2026-06-06-cancel-actor-attribution-design.md`; impl in task routes and run-step helpers.

### Agent runtime & executors

- **Agent executors & permissions.** Pluggable executors (Claude, Codex, opencode, Pi) with per-executor sandbox/allow-rule generation and workspace bootstrap. Spec `docs/superpowers/specs/2026-04-20-multi-executor-design.md`; contract `protocol/05b-agent-runtime.md`; guide `docs/agent-guides/agent-executors-and-permissions.md`; impl `runtime/orchestrator/executors.py`.
- **Manage-agent (enrollment).** Enroll, update, or terminate an agent; enrollment is founder-gated. Spec `docs/superpowers/specs/2026-04-17-manage-agent-design.md`; skill `protocol/skills/manage-agent/SKILL.md`; route `runtime/daemon/routes/agents.py`.
- **Manage-repo.** Add, remove, or update a repository in an agent's `agent.yaml`. Spec `docs/superpowers/specs/2026-04-17-manage-repo-design.md`; CLI `happyranch manage-repo`.
- **Founder-facing executor-switch.** `happyranch set-executor` switches an existing agent's executor end-to-end across org frontmatter, workspace `agent.yaml`, and executor bootstrap. Switching away from a provider leaves stale config files behind (CLAUDE.md, .claude/) and WARNs by default; cleanup requires an explicit `--clean` flag. Also surfaces executor-drift on `init-agent` when frontmatter and workspace disagree. CLI-only — no web surface. Keep this distinct from the **system assistant self-registration** (`happyranch assistant register`), which is the assistant's own executor declaration. Commit cf4c9e0; impl `cli/commands/agents.py`, `runtime/daemon/routes/agents.py`.
- **Per-agent learnings & memory.** Each agent keeps durable `LRN-NNN` learnings plus task recall. Specs `docs/superpowers/specs/2026-04-18-agent-memory-design.md` (superseded), `docs/superpowers/specs/2026-05-13-per-agent-learnings-structural-upgrade-design.md`; impl `runtime/infrastructure/learnings_store.py`. See [Per-Agent Learnings](#per-agent-learnings) below for traps.
- **System assistant.** A founder-facing assistant surface backed by a PTY-exec session. Two complementary surfaces: **web** (Settings dialog `System Assistant` section for status + init/repair, plus a `SystemAssistantPage` with an xterm.js terminal at `/orgs/:slug/assistant`) and **CLI** (`happyranch assistant status|init|register|repair|attach`). Onboarding is by **self-registration**: `happyranch assistant init` prepares the workspace and writes registration instructions; the founder opens their own agentic CLI there and it calls back `happyranch assistant register --from-file <payload>` declaring `{executor, command, argv}` (or registers via the web Assistant page). The daemon (`POST /assistant/register`) validates **structurally only** — non-empty fields and `shutil.which(argv[0])` resolves (no allowlist, no absolute-path requirement) — then auto-configures with no separate approval; `selected_argv` is launched raw in the founder-attended attach PTY. `executor` is a free string (any CLI may register; no fixed enum). Web auth uses the browser bearer-subprotocol (`Sec-WebSocket-Protocol: happyranch.bearer.<token>`, THR-006 Option A) for the PTY WebSocket. Specs `docs/superpowers/specs/2026-06-08-system-assistant-design.md` (runtime/attach surface), `docs/superpowers/specs/2026-06-10-assistant-self-registration-design.md` (registration onboarding, replaces executor probing), `docs/superpowers/specs/2026-06-12-system-assistant-web-ui-design.md` (web surface); impl `runtime/daemon/routes/assistant.py`, `runtime/daemon/assistant_pty.py`, `runtime/system_assistant.py`, `web/src/features/system-assistant/` (page + terminal), `web/src/features/settings/SettingsDialog.tsx` (status + init/repair); CLI `cli/commands/assistant.py`.
- **Jobs.** Background subprocesses run by the daemon, with two policy flags (`review_required`, `persistent`) and founder-review gating. Spec `docs/superpowers/specs/2026-05-26-jobs-design.md` (current); skill `protocol/skills/jobs/SKILL.md`; impl `runtime/daemon/routes/jobs.py`, `runtime/daemon/jobs_runner.py`. (Jobs absorbed the earlier "agent script requests" feature, `docs/superpowers/specs/2026-05-23-agent-script-requests-design.md`, now superseded.) See [Jobs](#jobs) below for traps.
- **Task blocked by job.** A task can self-block on one or more jobs via `tasks.blocked_on_job_ids`; it auto-resumes when all are terminal. Spec `docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md`. See [Task Blocked By Job](#task-blocked-by-job) below for traps.

### Collaboration surfaces

- **Threads.** Founder-visible broadcast conversations for coordination and cross-team handoff; every message mints a reply invocation for each participant, dispatch from a thread is self-only. Threads carry composer attribution (`composed_by`, `composed_from_task_id`, `composed_from_dream_id`) — the dream marker identifies dream-originated founder threads. Specs `docs/superpowers/specs/2026-05-13-threads-design.md` and successors (broadcast-only, agent-initiated, markdown composer, task-followup, escalation surfacing, working indicator, close-out removal/resume, file attachments); impl `runtime/infrastructure/thread_store.py`, `runtime/daemon/thread_runner.py`. See [Thread Broadcast Routing](#thread-broadcast-routing), [Thread Agent-Session Resume](#thread-agent-session-resume), and [Thread Task Followup](#thread-task-followup) below for traps.
- **Thread escalation surfacing.** When a thread-dispatched task escalates to `blocked(escalated)`, the runtime injects a `task_escalated` system message into the originating thread and re-invokes the dispatching manager for a founder-facing followup — mirroring the terminal task-followup. Rendered in both web (ThreadsPage.tsx `task_escalated` case) and CLI (`thread forward`). Spec `docs/superpowers/specs/2026-06-06-thread-escalation-surfacing-design.md`; impl `runtime/orchestrator/run_step.py`, `runtime/daemon/thread_runner.py`.
- **Knowledge base.** Per-org shared, durable cross-agent knowledge (rules, references, founder rulings); orgs do not share a KB. Contract `protocol/06-knowledge-base.md`; impl `runtime/infrastructure/kb_store.py`, `runtime/daemon/routes/kb.py`. See [Knowledge Base](#knowledge-base) below for traps.
- **KB view tracking.** Agent-CLI KB entry read counting scoped to agent consults only (founder ruling THR-009). Distinguished from web reads via `X-HappyRanch-Surface: cli` request header (a source label, not auth). Read surface is CLI-only: `happyranch kb stats` renders a table ordered by view count; no web surface. Spec `docs/superpowers/specs/2026-06-10-kb-view-tracking-design.md`; impl `cli/commands/kb.py`, `runtime/daemon/routes/kb.py`, `runtime/infrastructure/database.py` (`kb_views` table).
- **Shared artifacts.** Per-org opaque file blobs produced by one agent and visible to all agents in the org. Impl `runtime/infrastructure/artifact_store.py`, `runtime/daemon/routes/artifacts.py`; CLI `happyranch artifacts {put,list,get}`. See [Shared Artifacts](#shared-artifacts) below for traps.

### Org & runtime

- **Multi-org runtime.** A single daemon hosts multiple orgs in parallel under a schema-v2 container (`<runtime>/orgs/<slug>/...`); per-org routes live under `/api/v1/orgs/<slug>/...`. Specs `docs/superpowers/specs/2026-04-26-multi-org-runtime-design.md` (superseded), `docs/superpowers/specs/2026-04-28-parallel-multi-org-runtime-design.md`; current shape `docs/agent-guides/project-layout.md`; impl `runtime/daemon/org_state.py`, `runtime/daemon/runtimes.py`.
- **Org content model.** Each org is loaded from `org/` — charter, `teams.yaml`, per-agent `agents/*.md`, and `config.yaml`. Guide `docs/agent-guides/project-layout.md`; impl `runtime/orchestrator/org_config.py`, `runtime/orchestrator/teams.py`, `runtime/orchestrator/agent_def.py`.
- **Token-usage tracking.** Per-task, per-agent, and thread-scoped token accounting. Specs `docs/superpowers/specs/2026-05-05-token-usage-tracking-design.md`, `docs/superpowers/specs/2026-06-08-thread-talk-token-usage-scope-design.md`; API `runtime/daemon/routes/tokens.py`; CLI `happyranch tokens`.
### Web & CLI

- **Web UI.** React SPA dashboard for tasks, audit, KB, threads, and org/agent management, served from `web/dist/`. Specs `docs/superpowers/specs/2026-05-14-web-ui-design.md`, `docs/superpowers/specs/2026-05-30-dashboard-overhaul-design.md`, and the per-surface `2026-05-19-web-*` specs; architecture `web/ARCHITECTURE.md`; guide `docs/agent-guides/web-and-cli.md`.
- **CLI.** `happyranch`, a thin HTTP client over the daemon API used by both the founder and agents for all side effects. Guide `docs/agent-guides/web-and-cli.md`; impl `cli/`.
- **Audit log.** Append-only record of every state-changing action, keyed by task id (with scope prefixes for non-task actors). Impl `runtime/infrastructure/audit_logger.py`, `runtime/daemon/routes/audit.py`; CLI `happyranch audit`.
- **Token-usage visibility (Phase 1 dashboard panel).** A `TopTokenThreadsPanel` on the org dashboard showing thread-scoped token spend ranked by total tokens, plus CLI drill-down (`happyranch tokens --by-thread`). This is a **dashboard panel**, NOT a dedicated page. The underlying token-accounting infrastructure (per-task, per-agent, thread-scoped) is documented under [Token-usage tracking](#org--runtime) below. Commit f1dd539; impl `web/src/features/dashboard/components/TopTokenThreadsPanel.tsx`, `cli/commands/tasks.py`.

### Background / reflection

- **Nightly dreaming.** Private scheduled per-agent reflection runs, separate from tasks and threads, that may write learnings, propose KB candidates, and open a founder-only thread on meaningful output. Dream-originated threads carry the `composed_from_dream_id` marker (A4 migration, design-overhaul). Spec `docs/superpowers/specs/2026-06-09-nightly-dreaming-design.md`; impl `runtime/infrastructure/dream_store.py`, `runtime/daemon/dream_runner.py`, `runtime/daemon/dream_scheduler.py`, `runtime/daemon/dream_queue.py`, `runtime/daemon/routes/dreams.py`. See [Dreams](#dreams) below for traps.
- **Per-agent work-hours / scheduled wakes.** Founder-configured per-agent work windows (windowed or continuous) that wake idle agents on schedule to self-dispatch routine tasks parsed from per-agent `org/agents/<name>.md`. Backed by a `work_hours` table mirroring the dreams data model. Backend + CLI only on main (d022671): founder-facing `happyranch work-hours status|list|show` plus the agent wake callback `spawn`. Funded as #92. **No web UI surface yet** — the web mirrors (list/show/status pages) are stranded on unmerged branch `task/TASK-098`. Spec `docs/superpowers/specs/2026-06-10-working-hours-design.md`; impl `runtime/daemon/work_hours_scheduler.py`, `runtime/daemon/wake_runner.py`, `runtime/daemon/wake_queue.py`, `runtime/daemon/routes/work_hours.py`, `runtime/infrastructure/work_hours_store.py`, `cli/commands/work_hours.py`.

## Knowledge Base

Per-org KB entries live under `<runtime>/orgs/<slug>/kb/`. Orgs do not share a KB. `KBEntry.type` is freeform; route validation only enforces non-empty `slug`, `title`, `type`, and `topic`.

The dedicated `kb precedent` route was removed. Founder rulings flow through `happyranch kb add` with `source_task: <task-id>` in frontmatter.

Implementation: `runtime/infrastructure/kb_store.py` and `runtime/daemon/routes/kb.py`. Full rules: `protocol/06-knowledge-base.md`.

## Per-Agent Learnings

Per-agent learnings live under `<runtime>/orgs/<slug>/workspaces/<agent>/learnings/`, one `LRN-NNN-<slug>.md` per entry. CLI: `happyranch learning list|get|search|add|update|promote|reindex`.

Implementation: `runtime/infrastructure/learnings_store.py` and `runtime/daemon/routes/agents.py`. Spec: `docs/superpowers/specs/2026-05-13-per-agent-learnings-structural-upgrade-design.md`.

Traps:

- `PersistentWorkspaceSetup.ensure()` never creates `learnings/` when a non-empty flat `learnings.md` exists.
- `happyranch learning promote` is one-way: it replaces the body with a stub and locks the entry.

## Shared Artifacts

Per-org artifacts live at `<runtime>/orgs/<slug>/artifacts/`. They are opaque files produced by any agent and visible to every other agent in the same org.

Implementation: `runtime/infrastructure/artifact_store.py` and `runtime/daemon/routes/artifacts.py`. CLI: `happyranch artifacts {put,list,get}`.

Route surface: `POST /artifacts` (upload), `GET /artifacts` (list), `GET /artifacts/{name}` (download), `DELETE /artifacts/{name}` (delete). There is no update route — `POST` is an idempotent create-or-overwrite. Delete is exposed in the founder web artifacts UI only; there is **no** `happyranch artifacts delete` CLI verb.

Traps:

- Agent access is CLI-only by design; sandboxed executors block direct writes outside the workspace.
- `artifact_put` **and** `artifact_delete` audit rows use `task_id="artifact:<name>"`; the prefix is mandatory (artifact names are user-controlled and would otherwise collide with `TASK-`/`TALK-`/`SR-` scopes).
- Artifacts are blobs, not KB entries. Do not dump markdown that belongs in KB into `artifacts/`.

## Revisit

`happyranch revisit <task-id>` spawns a new root task inheriting brief and team from a terminal predecessor. Old lineage is frozen. It is TTY-gated and has no `--yes` bypass. Spec: `docs/superpowers/specs/2026-04-21-opc-revisit-design.md`.

Eligible predecessor states: failed, founder-cancelled failed, blocked/escalated, blocked/delegated, or completed.

**Auto-resolve forcing function (THR-018 tier #3).** When `revisit` (or a founder/manager thread-dispatch) creates a continuation whose predecessor root is `blocked(escalated|delegated)`, that predecessor is auto-transitioned to the terminal `resolved_superseded` state — block_kind cleared, audit citing the new continuation root task_id (+ founder note / thread ruling). This is the maker-checker boundary: auto-resolution fires **only** because a human authorized the continuation; an un-ruled escalation with no continuation is never auto-closed. The close does **not** re-enqueue the predecessor (it would otherwise spawn a wasted manager session), but it preserves parent-wake (`_enqueue_parent_if_waiting`) so a delegated parent still learns its branch reached terminal. The delegated close is gated on **all children being terminal** and never reuses the `cancel` cascade, so live siblings are never SIGTERM'd.

Traps:

- `revisit_of_task_id` is a sideways reference, not an ancestor edge. `walk_ancestors` must not follow it.
- Per-task overrides copied to revisit roots are narrow; auto-revisit copies only `session_timeout_seconds`.
- Auto-resolve to `resolved_superseded` must NEVER fire without a recorded successor task_id / thread ruling in the audit citation. The negative case (un-ruled escalation stays blocked) is a tested invariant.
- On the thread-dispatch path the continuation carries an optional `resolves <task_id>`, honored **only** for a manager-authorized dispatch (the founder supersedes via `revisit`). A worker self-dispatch naming `resolves` is rejected `403 thread_supersede_not_authorized` and never closes the predecessor — the maker-checker boundary, tested both directions.

## Session-Timeout Auto-Route

Auto-revisit on opaque agent failures is the silent retry path. Spec: `docs/superpowers/specs/2026-05-25-session-timeout-auto-route-design.md`.

Failure kinds: `session_timeout`, `no_callback`, `rate_limit`, `executor_error`, `agent_exception`, `session_failed`; `daemon_restart` is injected by startup recovery.

Traps:

- `_AUTO_REVISIT_CAP_PER_KIND = 2`; it is per kind, not global.
- `_maybe_spawn_auto_revisit` must run before `_enqueue_parent_if_waiting`.
- `failure_kind` is top-level on `auto_revisit_of`, not under `error_context`.
- Cascade still fails ancestors when `root_auto_revisit_spawned=True`.
- Startup sweep dedups with `revisited_roots: set[str]`.

## Thread Broadcast Routing

Every `kind=message` thread row mints a `REPLY` invocation for every participant except the speaker. There is no `addressed_to`, `@all`, or `@founder` token. Founder participates through the web UI; Feishu is not used for ongoing thread conversation. Spec: `docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md`.

Traps:

- Broadcast is unconditional; declines are silent.
- Decline-by-default doctrine is prompt-injected for `REPLY`, not in `protocol/skills/thread/SKILL.md`.
- Agent replies enforce the same `turn_cap` as founder `/send`.

## Thread Agent-Session Resume

Claude-backed thread participants reuse their Claude session across turns. State lives on `thread_participants.agent_session_id` and `last_resumed_seq`. Plan: `docs/superpowers/plans/2026-06-02-thread-claude-session-resume.md`.

Implementation: `runtime/daemon/thread_runner.py`, `runtime/orchestrator/executors.py`, `runtime/infrastructure/database.py`, and `runtime/infrastructure/audit_logger.py`.

Traps:

- Claude-only optimization, never a correctness dependency.
- `last_resumed_seq` advances only after a successful subprocess.
- Per-`(thread, agent)` `asyncio.Lock` protects read-run-update.
- Eviction fallback re-runs once and audits `agent_session_evicted_fallback`.
- `ExecutorResult.agent_session_id` is not `ExecutorResult.session_id`.

## Thread Task Followup

When a task dispatched from a thread reaches true terminal state, `_maybe_post_thread_followup` appends a system message and mints a `TASK_FOLLOWUP` invocation. Spec: `docs/superpowers/specs/2026-05-28-thread-task-followup-design.md`.

Traps:

- Helper runs after `_maybe_spawn_auto_revisit`.
- Only root tasks fire followups.
- Dispatcher identity comes from the `task_dispatched` audit row.
- Cross-thread enqueue uses `asyncio.run_coroutine_threadsafe(queue.put(job), main_loop)`.
- Terminal gate is completion/failed **plus** `resolved_superseded` (completion-class → `task_completed` kind). A thread-originated task auto-resolved by a continuation must still emit its followup; missing this terminal silently drops the superseded state from the thread lifecycle.

## Dreams

Dreams are private scheduled reflection runs, separate from tasks and threads. Per-org config lives under `dreaming:` in `<runtime>/orgs/<slug>/org/config.yaml`. A dream may write per-agent learnings, persist KB candidates, and create a founder-only thread when there is meaningful output.

Traps:

- Dreams are not `TaskRecord`s and must not appear in task metrics.
- Dreams produce KB candidates, not KB entries.
- Startup catch-up runs at most today's missed dream; it does not replay every missed day.
- Failed or timed-out dreams do not advance the next input window.

## Thread Dispatch Self-Only Rule

`/threads/{id}/dispatch` rejects calls where `effective_target != dispatcher`. Spec: `docs/superpowers/specs/2026-05-28-thread-talk-self-dispatch-only-design.md`.

Traps:

- Applies to managers and workers uniformly.
- Doctrine is system-prompt-injected through `_thread_talk_dispatch_doctrine_section()`.
- Shared error hint `SELF_DISPATCH_HINT` lives in `runtime/daemon/routes/_doctrine.py`.

## Jobs

Per-org jobs use a SQLite table and files at `<runtime>/orgs/<slug>/jobs/JOB-NNN.{out,err,script}`. Spec: `docs/superpowers/specs/2026-05-26-jobs-design.md`.

Implementation: `runtime/daemon/routes/jobs.py`, `runtime/daemon/jobs_runner.py`, `runtime/infrastructure/database.py`, and `runtime/infrastructure/audit_logger.py`.

Routes under `/api/v1/orgs/{slug}/jobs/`: `POST /submit`, `GET /`, `GET /{id}`, `POST /{id}/run`, `POST /{id}/reject`, `GET /{id}/output`, and `GET /{id}/events`.

Traps:

- Agent identity derives from auth context, never payload `agent`.
- Submit auth path: `(task_id + session_id)`.
- `review_required` and `persistent` are honor-system on submit.
- Auto-resume on terminal supersedes founder revisit for blocked-on-job tasks.

## Task Blocked By Job

Blocked-on-job tasks use `tasks.blocked_on_job_ids` plus `BlockKind.BLOCKED_ON_JOB`. Spec: `docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md`.

Implementation touches `runtime/orchestrator/run_step.py`, `runtime/daemon/jobs_runner.py`, and `runtime/daemon/app.py`.

Traps:

- State transitions are owned by `run_step_impl`, not the route or resume helper.
- Three resume callers must stay symmetric: job terminal hook, immediate block branch check, and startup recovery.
- Predicate is all-terminal, not any-terminal.
- `metadata` is a function parameter, not shared state.

## Feishu Notifications (REMOVED)

Feishu was removed in TASK-302 (THR-022). The web UI and threads are the sole control path for dispatch / revisit / resolve-escalation. Legacy `feishu_notifications` config blocks are tolerated on load but ignored. Database correlation tables (`escalation_notifications`, `processed_event_ids`) remain dormant in place.
