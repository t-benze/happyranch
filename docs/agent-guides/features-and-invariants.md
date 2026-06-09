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
- **Per-agent learnings & memory.** Each agent keeps durable `LRN-NNN` learnings plus task recall. Specs `docs/superpowers/specs/2026-04-18-agent-memory-design.md` (superseded), `docs/superpowers/specs/2026-05-13-per-agent-learnings-structural-upgrade-design.md`; impl `runtime/infrastructure/learnings_store.py`. See [Per-Agent Learnings](#per-agent-learnings) below for traps.
- **System assistant.** A founder-facing assistant surface backed by a PTY-exec session. Spec `docs/superpowers/specs/2026-06-08-system-assistant-design.md` (current); impl `runtime/daemon/routes/assistant.py`, `runtime/daemon/assistant_pty.py`; CLI `cli/commands/assistant.py`.
- **Jobs.** Background subprocesses run by the daemon, with two policy flags (`review_required`, `persistent`) and founder-review gating. Spec `docs/superpowers/specs/2026-05-26-jobs-design.md` (current); skill `protocol/skills/jobs/SKILL.md`; impl `runtime/daemon/routes/jobs.py`, `runtime/daemon/jobs_runner.py`. (Jobs absorbed the earlier "agent script requests" feature, `docs/superpowers/specs/2026-05-23-agent-script-requests-design.md`, now superseded.) See [Jobs](#jobs) below for traps.
- **Task blocked by job.** A task can self-block on one or more jobs via `tasks.blocked_on_job_ids`; it auto-resumes when all are terminal. Spec `docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md`. See [Task Blocked By Job](#task-blocked-by-job) below for traps.

### Collaboration surfaces

- **Threads.** Founder-visible broadcast conversations for coordination and cross-team handoff; every message mints a reply invocation for each participant, dispatch from a thread is self-only. Specs `docs/superpowers/specs/2026-05-13-threads-design.md` and successors (broadcast-only, agent-initiated, markdown composer, task-followup, escalation surfacing, working indicator, close-out removal/resume, file attachments); impl `runtime/infrastructure/thread_store.py`, `runtime/daemon/thread_runner.py`. See [Thread Broadcast Routing](#thread-broadcast-routing), [Thread Agent-Session Resume](#thread-agent-session-resume), and [Thread Task Followup](#thread-task-followup) below for traps.
- **Talks.** Founder-activated one-on-one conversational sessions with a single agent; dispatch from a talk is self-only. Specs `docs/superpowers/specs/2026-04-21-talk-flow-design.md`, `docs/superpowers/specs/2026-04-26-talk-dispatch-design.md`; impl `runtime/infrastructure/talk_store.py`, `runtime/daemon/routes/talks.py`. See [Thread / Talk Dispatch Self-Only Rule](#thread--talk-dispatch-self-only-rule) below for traps.
- **Knowledge base.** Per-org shared, durable cross-agent knowledge (rules, references, founder rulings); orgs do not share a KB. Contract `protocol/06-knowledge-base.md`; impl `runtime/infrastructure/kb_store.py`, `runtime/daemon/routes/kb.py`. See [Knowledge Base](#knowledge-base) below for traps.
- **Shared artifacts.** Per-org opaque file blobs produced by one agent and visible to all agents in the org. Impl `runtime/infrastructure/artifact_store.py`, `runtime/daemon/routes/artifacts.py`; CLI `happyranch artifacts {put,list,get}`. See [Shared Artifacts](#shared-artifacts) below for traps.

### Org & runtime

- **Multi-org runtime.** A single daemon hosts multiple orgs in parallel under a schema-v2 container (`<runtime>/orgs/<slug>/...`); per-org routes live under `/api/v1/orgs/<slug>/...`. Specs `docs/superpowers/specs/2026-04-26-multi-org-runtime-design.md` (superseded), `docs/superpowers/specs/2026-04-28-parallel-multi-org-runtime-design.md`; current shape `docs/agent-guides/project-layout.md`; impl `runtime/daemon/org_state.py`, `runtime/daemon/runtimes.py`.
- **Org content model.** Each org is loaded from `org/` — charter, `teams.yaml`, per-agent `agents/*.md`, and `config.yaml`. Guide `docs/agent-guides/project-layout.md`; impl `runtime/orchestrator/org_config.py`, `runtime/orchestrator/teams.py`, `runtime/orchestrator/agent_def.py`.
- **Token-usage tracking.** Per-task, per-agent, and thread/talk-scoped token accounting. Specs `docs/superpowers/specs/2026-05-05-token-usage-tracking-design.md`, `docs/superpowers/specs/2026-06-08-thread-talk-token-usage-scope-design.md`; API `runtime/daemon/routes/tokens.py`; CLI `happyranch tokens`.
- **Feishu notifications & interactive actions.** Per-org opt-in outbound escalation/failure notifications plus inbound interactive approvals (e.g. job review) over a Feishu websocket. Specs `docs/superpowers/specs/2026-05-08-feishu-notification-design.md`, `docs/superpowers/specs/2026-05-12-feishu-interactive-actions-design.md`; impl `runtime/infrastructure/feishu/`, `runtime/daemon/feishu_listener.py`. See [Feishu Notifications](#feishu-notifications) below for traps.

### Web & CLI

- **Web UI.** React SPA dashboard for tasks, audit, KB, threads, talks, and org/agent management, served from `web/dist/`. Specs `docs/superpowers/specs/2026-05-14-web-ui-design.md`, `docs/superpowers/specs/2026-05-30-dashboard-overhaul-design.md`, and the per-surface `2026-05-19-web-*` specs; architecture `web/ARCHITECTURE.md`; guide `docs/agent-guides/web-and-cli.md`.
- **CLI.** `happyranch`, a thin HTTP client over the daemon API used by both the founder and agents for all side effects. Guide `docs/agent-guides/web-and-cli.md`; impl `cli/`.
- **Audit log.** Append-only record of every state-changing action, keyed by task id (with scope prefixes for non-task actors). Impl `runtime/infrastructure/audit_logger.py`, `runtime/daemon/routes/audit.py`; CLI `happyranch audit`.

### Background / reflection

- **Nightly dreaming.** Private scheduled per-agent reflection runs, separate from tasks/talks/threads, that may write learnings, propose KB candidates, and open a founder-only thread on meaningful output. Spec `docs/superpowers/specs/2026-06-09-nightly-dreaming-design.md`; impl `runtime/infrastructure/dream_store.py`, `runtime/daemon/dream_runner.py`, `runtime/daemon/dream_scheduler.py`, `runtime/daemon/dream_queue.py`, `runtime/daemon/routes/dreams.py`. See [Dreams](#dreams) below for traps. (The spec README still labels this "not implemented yet"; the listed modules show it is now implemented.)

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

Traps:

- Agent access is CLI-only by design; sandboxed executors block direct writes outside the workspace.
- `artifact_put` audit rows use `task_id="artifact:<name>"`; the prefix is mandatory.
- Artifacts are blobs, not KB entries. Do not dump markdown that belongs in KB into `artifacts/`.

## Revisit

`happyranch revisit <task-id>` spawns a new root task inheriting brief and team from a terminal predecessor. Old lineage is frozen. It is TTY-gated and has no `--yes` bypass. Spec: `docs/superpowers/specs/2026-04-21-opc-revisit-design.md`.

Eligible predecessor states: failed, founder-cancelled failed, blocked/escalated, or completed.

Traps:

- `revisit_of_task_id` is a sideways reference, not an ancestor edge. `walk_ancestors` must not follow it.
- Per-task overrides copied to revisit roots are narrow; auto-revisit copies only `session_timeout_seconds`.

## Session-Timeout Auto-Route

Auto-revisit on opaque agent failures is the silent retry path. Spec: `docs/superpowers/specs/2026-05-25-session-timeout-auto-route-design.md`.

Failure kinds: `session_timeout`, `no_callback`, `rate_limit`, `executor_error`, `agent_exception`, `session_failed`; `daemon_restart` is injected by startup recovery.

Traps:

- `_AUTO_REVISIT_CAP_PER_KIND = 2`; it is per kind, not global.
- `_maybe_spawn_auto_revisit` must run before `_enqueue_parent_if_waiting`.
- `failure_kind` is top-level on `auto_revisit_of`, not under `error_context`.
- Cascade still fails ancestors when `root_auto_revisit_spawned=True`; only the Feishu notification is suppressed.
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

## Dreams

Dreams are private scheduled reflection runs, separate from tasks, talks, and threads. Per-org config lives under `dreaming:` in `<runtime>/orgs/<slug>/org/config.yaml`. A dream may write per-agent learnings, persist KB candidates, and create a founder-only thread when there is meaningful output.

Traps:

- Dreams are not `TaskRecord`s and must not appear in task metrics.
- Dreams produce KB candidates, not KB entries.
- Startup catch-up runs at most today's missed dream; it does not replay every missed day.
- Failed or timed-out dreams do not advance the next input window.

## Thread / Talk Dispatch Self-Only Rule

`/threads/{id}/dispatch` and `/talks/{id}/dispatch` reject calls where `effective_target != dispatcher`. Spec: `docs/superpowers/specs/2026-05-28-thread-talk-self-dispatch-only-design.md`.

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
- Submit auth paths are mutually exclusive: `(task_id + session_id)` XOR `talk_id`.
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

## Feishu Notifications

Per-org opt-in via `feishu_notifications` in `<runtime>/orgs/<slug>/org/config.yaml`. Credentials are required when enabled and live in the same file; treat it as secret-bearing. Specs: `docs/superpowers/specs/2026-05-08-feishu-notification-design.md` and `docs/superpowers/specs/2026-05-12-feishu-interactive-actions-design.md`. Setup: `docs/setup/feishu-notifications.md`.

Entry points:

- Outbound: `Orchestrator.notify_escalated` and `notify_failed`.
- Inbound: `FeishuEventListener`, one WS per org, bridged into the asyncio loop.

Critical invariant: `_resolve_for_listener` in `runtime/daemon/app.py` must not swallow exceptions from in-process resolvers. On failure it records `outcome="rejected", reason="handler_exception"` and leaves the row unconsumed.

Optional features: `notify_on_failure`, `allow_dispatch`, and Jobs approval/rejection. CLI fallbacks consume open rows with `consumed_by="cli-fallback"`, so a CLI-first resolution silently no-ops the later Feishu reply.
