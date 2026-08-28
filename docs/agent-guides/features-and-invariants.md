# Features And Invariants

This file serves two purposes. The **Feature Modules Overview** below is an orientation map of the product's feature modules — each module is one short paragraph (what it does) plus a pointer to its authoritative spec or implementation. The per-surface sections after it are the original feature-specific traps, to be read only when touching the relevant surface; the overview points down to those sections where one exists rather than restating them.

For current behavior always prefer `protocol/`, `docs/agent-guides/`, tests, the OpenAPI snapshot, and implementation over the design specs — `docs/superpowers/specs/` is append-only design history unless `docs/superpowers/specs/README.md` marks a spec `current`.

## Feature Modules Overview

### Orchestration core

- **Orchestrator & task state machine.** The daemon-side loop that advances each task one step at a time, drives manager-decision turns, spawns children, and records terminal state. Spec `docs/superpowers/specs/2026-04-14-orchestrator-daemon-design.md`; current contract `docs/agent-guides/orchestrator-contracts.md`; impl `runtime/orchestrator/run_step.py`, `runtime/orchestrator/orchestrator.py`.
- **Task-owner decision loop & completion contract.** Task owners (task_type='task') end every turn with a `decision` (`delegate`/`fanout`/`done`/`escalate`; the `parallel` alias is accepted for `fanout`); subtask agents report a plain completion. **Only root tasks (`parent_task_id is None`) escalate to the founder; a non-root task that would escalate (via `decision:escalate` or by exceeding the step budget) instead fails and hands back to its parent, and bounded failure-recovery carries it up (THR-033 Change A).** Contract `protocol/00-completion-contract.md`; guide `docs/agent-guides/orchestrator-contracts.md`; impl in `runtime/orchestrator/run_step.py`.
- **Inline delegation chains.** A task owner can declare a multi-leg subtask chain inline via `then: [...]`; the orchestrator auto-advances routine legs on matching verdict without consuming orchestration steps. Spec `docs/superpowers/specs/2026-05-30-inline-delegation-chain-design.md` (current); impl `runtime/orchestrator/chain.py`.
- **Task status model.** The canonical task status vocabulary and transition rules (`pending`, `in_progress`, `escalated`, `completed`, `failed`, `cancelled`, `superseded`). Under THR-037 Change B (Path B) a parent waiting on its children/jobs is `in_progress` with the reason in `block_kind`; the await-founder state is the top-level `escalated`; `cancelled` is a founder-initiated terminal. Specs `docs/superpowers/specs/2026-04-19-task-status-redesign.md` + `docs/superpowers/specs/2026-06-27-task-status-pathB-stored-design.md` (current); current vocabulary `docs/agent-guides/orchestrator-contracts.md`.
- **Subtask / composite tasks.** Subtask agents spawn bounded subtasks under a parent task, for decomposing a single delegation into iterative steps. Spec `docs/superpowers/specs/2026-06-03-subtask-composite-task-design.md`; impl in `runtime/orchestrator/run_step.py`.
- **Revisit.** `happyranch revisit <task-id>` spawns a fresh root task inheriting brief and team from a terminal predecessor; old lineage freezes. Specs `docs/superpowers/specs/2026-04-21-opc-revisit-design.md`, `docs/superpowers/specs/2026-04-23-revisit-root-link-design.md`. See [Revisit](#revisit) below for traps.
- **Session-timeout auto-route (RETIRED — TASK-3604).** Automatic daemon successor creation on opaque agent failures has been removed per founder direction. Opaque failures now end FAILED and hand to the existing parent/founder recovery paths (bounded manager-wake, escalation, explicit founder revisit). Legacy `auto_revisit_of` audit rows remain readable for historical compatibility. Original spec `docs/superpowers/specs/2026-05-25-session-timeout-auto-route-design.md` (retired).
- **Cancel (race + actor attribution).** Founder/agent task cancellation with race-safe state handling and audit attribution of who cancelled. Specs `docs/superpowers/specs/2026-05-26-cancel-race-design.md`, `docs/superpowers/specs/2026-06-06-cancel-actor-attribution-design.md`; impl in task routes and run-step helpers.
- **Bounded failure-recovery (TASK-573 / THR-078 / THR-183).** When a subtask fails, the parent task is re-enqueued for a bounded manager-wake decision step (not cascade-failed). Each delegated slot gets exactly one retry: the current unresolved FAILED leaf of a slice's `revisit_of_task_id` lineage exhausts the slot on its second failure and triggers root-only escalation via `is_root(parent)+try_escalate`; a later COMPLETED or SUPERSEDED descendant retires earlier FAILED ancestors so a completed-child wake cannot select a stale reason. Other child failures give the parent a bounded manager wake. Retry is determined via the failing child's `revisit_of_task_id` lineage within the parent (no sibling counting, no schema migration). Failed chain legs also wake the parent instead of cascading. Happy path (all subtasks COMPLETED) and REVISE-verdict auto-advance are unchanged. Threads: THR-028, THR-078. Implementation: `runtime/orchestrator/run_step.py:_enqueue_parent_if_waiting`, `_is_slice_retry_exhausted`. See [Bounded failure-recovery](#bounded-failure-recovery).

### Agent runtime & executors

- **Agent executors & permissions.** Pluggable executors (Claude, Codex, opencode, Pi) with per-executor sandbox/allow-rule generation and workspace bootstrap. Spec `docs/superpowers/specs/2026-04-20-multi-executor-design.md`; contract `protocol/05b-agent-runtime.md`; guide `docs/agent-guides/agent-executors-and-permissions.md`; impl `runtime/orchestrator/executors.py`.
- **Manage-agent (enrollment).** Enroll, update, or terminate an agent; enrollment is founder-gated. Spec `docs/superpowers/specs/2026-04-17-manage-agent-design.md`; skill `protocol/skills/manage-agent/SKILL.md`; route `runtime/daemon/routes/agents.py`.
- **Manage-repo.** Add, remove, or update a repository in an agent's `org/agents/<name>.md` frontmatter. Spec `docs/superpowers/specs/2026-04-17-manage-repo-design.md`; CLI `happyranch manage-repo`.
- **Founder-facing executor-switch.** `happyranch set-executor` switches an existing agent's executor end-to-end in the org `.md` frontmatter and executor bootstrap (THR-095: agent.yaml is no longer synced). Switching away from a provider leaves stale config files behind (CLAUDE.md, .claude/) and WARNs by default; cleanup requires an explicit `--clean` flag. CLI-only — no web surface. Keep this distinct from the **system assistant self-registration** (`happyranch assistant register`), which is the assistant's own executor declaration. Commit cf4c9e0; impl `cli/commands/agents.py`, `runtime/daemon/routes/agents.py`.
- **Per-agent memory.** Each agent keeps durable `MEM-NNN` learnings plus task recall. Specs `docs/superpowers/specs/2026-04-18-agent-memory-design.md` (superseded), `docs/superpowers/specs/2026-05-13-per-agent-learnings-structural-upgrade-design.md`; impl `runtime/infrastructure/learnings_store.py`. See [Per-Agent Learnings](#per-agent-learnings) below for traps.
- **System assistant.** A founder-facing assistant surface reached via the **Cmd-K dock** (global &#8984;K A-mode structured chat dock mounted in the AppShell) and the **CLI** (`happyranch assistant status|init|register`). Onboarding is by **self-registration** (unchanged). The dock uses a JSON-framed WebSocket for structured conversations. **Action chips:** (a) *reference-existing* chips (Approve JOB-083, Open THR-021, Show diff, any TASK/JOB/THR/KB id) deep-link/navigate to the existing object's approval or detail surface — no POST, no self-approval; (b) *propose-new-action* chips (a chip proposing a gated op that does NOT yet exist as an object, e.g. "propose merging PR X") MUST create a PENDING `review_required` job through the EXISTING jobs gate (the already-authenticated assistant WS carries the structured frame; the daemon-side handler submits the job through the existing jobs mechanism). The assistant NEVER self-approves or self-executes a privileged op. Impl `runtime/daemon/routes/assistant_a_mode.py` (A-mode WS), `runtime/daemon/headless_assistant.py` (headless adapters), `web/src/features/system-assistant/AssistantDockHost.tsx` (⌘K dock).
- **Jobs.** Background subprocesses run by the daemon, with two policy flags (`review_required`, `persistent`) and founder-review gating. Spec `docs/superpowers/specs/2026-05-26-jobs-design.md` (current); skill `protocol/skills/jobs/SKILL.md`; impl `runtime/daemon/routes/jobs.py`, `runtime/daemon/jobs_runner.py`. (Jobs absorbed the earlier "agent script requests" feature, `docs/superpowers/specs/2026-05-23-agent-script-requests-design.md`, now superseded.) See [Jobs](#jobs) below for traps.
- **Task blocked by job.** A task can self-block on one or more jobs via `tasks.blocked_on_job_ids`; it auto-resumes when all are terminal. Spec `docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md`. See [Task Blocked By Job](#task-blocked-by-job) below for traps.
- **PR CI wait / guarded merge.** PR-producing engineering tasks use the jobs + `blocked_on_job_ids` path to wait for GitHub CI outside the agent session. Two CLI entrypoints (`python -m runtime.daemon.pr_ci_waiter` and `python -m runtime.daemon.pr_ci_merge`) wired to real `gh` provide the polling and guarded-merge mechanisms. The poll job (submitted through the existing jobs path with `review_required=false`) polls checks for a pinned PR head SHA, handles no-checks-yet settling, detects stale heads and timeouts, and prints a structured verdict JSON. The poll job performs NO merge. On resume, the task owner triggers `guarded_merge` as a short daemon-run step; it re-enforces all guards (review APPROVE + QA PASS + CI PASS + unchanged SHA + mergeable CLEAN) before merge. No new daemon route, no new task state, and no raw `gh pr merge` permission broadening. Current contract: `protocol/00-completion-contract.md` (including the **Merge-evidence contract** — the canonical vocabulary `APPROVE | REQUEST_CHANGES | BLOCK | PASS | REVISE | FAIL` and the structured-vs-prose extraction rules: a NON-NULL structured `verdict` is primary; serialized `null`, the durable recall producer's representation of legacy/no-structured rows, uses the strict annotated-prose fallback); implementation: `runtime/daemon/pr_ci_waiter.py`, `runtime/daemon/pr_ci_merge.py`.

### Collaboration surfaces

- **Threads.** Founder-visible coordination and cross-team handoff conversations with per-thread recipient-set routing (THR-198): routing is default-enabled per thread, valid current-participant @-mentions narrow conversational REPLY wakes to exactly that set (speaker excluded), zero valid mentions or a disabled thread broadcasts to participants, and TASK_FOLLOWUP/BOOTSTRAP wakes are isolated and never mention-routed; dispatch from a thread is self-only. Threads carry composer attribution (`composed_by`, `composed_from_task_id`, `composed_from_dream_id`) — the dream marker identifies dream-originated founder threads. Specs `docs/superpowers/specs/2026-05-13-threads-design.md` and successors (broadcast-only, agent-initiated, markdown composer, task-followup, escalation surfacing, working indicator, close-out removal/resume, file attachments); impl `runtime/infrastructure/thread_store.py`, `runtime/daemon/thread_runner.py`. See [Thread Broadcast Routing](#thread-broadcast-routing), [Thread Agent-Session Resume](#thread-agent-session-resume), and [Thread Task Followup](#thread-task-followup) below for traps.
- **Thread rename + pin (THR-209 Phase 1).** Founder-only thread-organization controls. Rename edits the durable `subject` (`POST /threads/{id}/rename`; trim, non-empty, ≤120 chars, duplicates allowed, last successful save wins); pin/unpin is durable founder-workspace presentation state stored in an additive nullable `threads.pinned_at` column (`POST /threads/{id}/pin`, strict bool). **Invariants:** both are presentation/organization controls — they never create a thread message, never send a notification, never touch participants/unread, and never change activity timestamps (`started_at`/`archived_at`); identity (`id`, URL, participants, routing, lifecycle) is immutable under rename/pin. Pinned threads rank above unpinned across every qualifying list/search/filter view (Pinned section), ordered by most recent thread activity; ordinary (unpinned) order is unchanged; archived/closed pins appear only where otherwise eligible; pin state lives on the thread row, so deleting a thread removes its pin state automatically. Audit rows `thread_renamed` / `thread_pinned` / `thread_unpinned` use the existing `audit_log.task_id` = THR-* scope convention and never appear as thread messages. Each mutation is ONE rollback-safe transaction under the org `db_lock` (`rename_thread_with_audit` / `set_thread_pinned_with_audit` in `runtime/infrastructure/database.py`): authoritative old-value read + idempotence decision + `subject`/`pinned_at` write + audit row commit atomically; on audit failure everything rolls back (no durable unaudited transition, error response). Concurrent renames are last-successful-save-wins with a truthful sequential old→new audit chain; concurrent same/opposite-state pins emit exactly the audit rows for the durable transitions (true no-ops unaudited). Spec `docs/superpowers/specs/2026-08-25-thread-rename-and-pinning-design.md`; routes `runtime/daemon/routes/threads.py`; wire adds `pinned`/`pinned_at`/`last_activity_at` to thread rows; web UI `web/src/features/threads/ThreadsPage.tsx` (Pinned section, inline rename, row/header/overflow pin controls).
- **Thread escalation surfacing.** When a thread-dispatched task escalates to `escalated`, the runtime injects a `task_escalated` system message into the originating thread and re-invokes the dispatching manager for a founder-facing followup — mirroring the terminal task-followup. Rendered in both web (ThreadsPage.tsx `task_escalated` case) and CLI (`thread forward`). Spec `docs/superpowers/specs/2026-06-06-thread-escalation-surfacing-design.md`; impl `runtime/orchestrator/run_step.py`, `runtime/daemon/thread_runner.py`.
- **Tasks surface.** Org-wide work list (not a kanban) with roots-only default view, group-by segmented control (Status / Agent / Thread), severity rollup per root reflecting the worst status of its subtree (DERIVE over `parent_task_id` children — no schema change; `GET /orgs/{slug}/tasks/roots`). Bidirectional lineage inline (\u2190 supersedes / \u2192 revisits) backed by `revisit_of_task_id` + `get_direct_revisits()`. Task detail pane with connected vertical chain timeline (`walk_revisit_chain()`), blocked node naming its blocker, property rail, append activity log, and raw monospace brief with "Show full" toggle. State vocabulary: Loading (skeleton rows by group), Empty per group, Error-with-retry. Keyboard: \u2191/\u2193 move selection, Enter opens, Esc clears. Spec `docs/design-overhaul/product_lead-2026-06-17-design-overhaul-PRD-final.md` (\u00a74.3); web `web/src/features/tasks/TasksPage.tsx`, `TaskDetailPane.tsx`; route `runtime/daemon/routes/tasks.py` (`/tasks/roots`).
- **Knowledge base.** Per-org shared, durable cross-agent knowledge (rules, references, founder rulings); orgs do not share a KB. Contract `protocol/06-knowledge-base.md`; impl `runtime/infrastructure/kb_store.py`, `runtime/daemon/routes/kb.py`. See [Knowledge Base](#knowledge-base) below for traps.
- **KB view tracking.** Agent-CLI KB entry read counting scoped to agent consults only (founder ruling THR-009). Distinguished from web reads via `X-HappyRanch-Surface: cli` request header (a source label, not auth). Read surface is CLI-only: `happyranch kb stats` renders a table ordered by view count; no web surface. Spec `docs/superpowers/specs/2026-06-10-kb-view-tracking-design.md`; impl `cli/commands/kb.py`, `runtime/daemon/routes/kb.py`, `runtime/infrastructure/database.py` (`kb_views` table).
- **Shared artifacts.** Per-org opaque file blobs produced by one agent and visible to all agents in the org. Impl `runtime/infrastructure/artifact_store.py`, `runtime/daemon/routes/artifacts.py`; CLI `happyranch artifacts {put,list,get}`. See [Shared Artifacts](#shared-artifacts) below for traps.

### Org & runtime

- **Multi-org runtime.** A single daemon hosts multiple orgs in parallel under a schema-v2 container (`<runtime>/orgs/<slug>/...`); per-org routes live under `/api/v1/orgs/<slug>/...`. Specs `docs/superpowers/specs/2026-04-26-multi-org-runtime-design.md` (superseded), `docs/superpowers/specs/2026-04-28-parallel-multi-org-runtime-design.md`; current shape `docs/agent-guides/project-layout.md`; impl `runtime/daemon/org_state.py`, `runtime/daemon/runtimes.py`.
- **Org content model.** Each org is loaded from `org/` — charter, `teams.yaml`, per-agent `agents/*.md`, and `config.yaml`. Guide `docs/agent-guides/project-layout.md`; impl `runtime/orchestrator/org_config.py`, `runtime/orchestrator/teams.py`, `runtime/orchestrator/agent_def.py`.
- **Org portability (Slice A).** CLI-only, relocation-only preflight + reconciliation. See [Org Portability](#org-portability-thr-187-slice-a) below. Impl `runtime/portability/` (pure classifier + eligibility), `runtime/daemon/routes/portability.py`, `cli/commands/runtime.py`.
- **Token-usage tracking.** Per-task, per-agent, thread-scoped, dream-scoped, and work-hour-scoped token accounting with two complementary metrics: **churn** (`churn_tokens` = input + output + reasoning, the cache-excluded fresh-work cost used for ranking/thresholds) and **context** (`context_tokens` = churn + cache_read + cache_creation, the cache-inclusive total). Specs `docs/superpowers/specs/2026-05-05-token-usage-tracking-design.md`, `docs/superpowers/specs/2026-06-08-thread-talk-token-usage-scope-design.md`; API `runtime/daemon/routes/tokens.py`; CLI `happyranch tokens` (issue #216).
### Web & CLI

- **Web UI.** React SPA with a flat primary sidebar nav (Home · Threads · Tasks · Jobs · Todos · Agents · Work Hours · Skills · Knowledge · Artifacts · Audit · Dreams · Usage · Health), footer-pinned Settings + account row (founder-approved flattening: THR-140 seq 208 / PR #644 superseded the #633/#577 grouped-nav pilot), org switcher at the sidebar top, theme toggle in the AppBar; desktop window chrome, served from `web/dist/`. **Default landing route is Home** (the Dashboard page at `/orgs/:slug/dashboard`). Specs `docs/superpowers/specs/2026-05-14-web-ui-design.md`, `docs/superpowers/specs/2026-05-30-dashboard-overhaul-design.md`, `docs/design-overhaul/product_lead-2026-06-16-design-overhaul-PRD-build-spec.md` (Direction-A IA); architecture `web/ARCHITECTURE.md`; guide `docs/agent-guides/web-and-cli.md`.
- **CLI.** `happyranch`, a thin HTTP client over the daemon API used by both the founder and agents for all side effects. Guide `docs/agent-guides/web-and-cli.md`; impl `cli/`.
- **Audit log.** Append-only record of every state-changing action, keyed by task id (with scope prefixes for non-task actors). Impl `runtime/infrastructure/audit_logger.py`, `runtime/daemon/routes/audit.py`; CLI `happyranch audit`.
- **Token-usage visibility (Phase 1 dashboard panel).** A `TopTokenThreadsPanel` on the org dashboard showing thread-scoped token spend ranked by total tokens, plus CLI drill-down (`happyranch tokens --by-thread`). This is a **dashboard panel**, NOT a dedicated page. The underlying token-accounting infrastructure (per-task, per-agent, thread-scoped) is documented under [Token-usage tracking](#org--runtime) below. Commit f1dd539; impl `web/src/features/dashboard/components/TopTokenThreadsPanel.tsx`, `cli/commands/tasks.py`.

### Background / reflection

- **Nightly dreaming.** Private scheduled per-agent reflection runs, separate from tasks and threads, that may write learnings, propose KB candidates, and open a founder-only thread on meaningful output. Dream-originated threads carry the `composed_from_dream_id` marker (A4 migration, design-overhaul). Spec `docs/superpowers/specs/2026-06-09-nightly-dreaming-design.md`; impl `runtime/infrastructure/dream_store.py`, `runtime/daemon/dream_runner.py`, `runtime/daemon/dream_scheduler.py`, `runtime/daemon/dream_queue.py`, `runtime/daemon/routes/dreams.py`. See [Dreams](#dreams) below for traps.
- **Per-agent work-hours / scheduled wakes.** Founder-configured per-agent work windows (windowed or continuous) that wake idle agents on schedule to self-dispatch routine tasks parsed from per-agent `org/agents/<name>.md`. Backed by a `work_hours` table mirroring the dreams data model. Founder-facing `happyranch work-hours status|list|show` plus the agent wake callback `spawn`. Funded as #92. **Web UI (design-overhaul, THR-035 consolidation):** the wake-execution list (formerly a standalone Schedule surface at `/orgs/:slug/schedule`) is now the **Wakes** in-page tab of the Work Hours surface at `/orgs/:slug/work-hours?view=wakes`; old `/schedule` bookmarks redirect. Lists per-agent work-hour wakes grouped by agent with real stored fields (date, slot, mode, scheduled-for, status, routine count, spawned task IDs via IdBadge click-through). No authoring controls — creating named recurring wakes is deferred (D6). Spec `docs/superpowers/specs/2026-06-10-working-hours-design.md`; impl `runtime/daemon/work_hours_scheduler.py`, `runtime/daemon/wake_runner.py`, `runtime/daemon/wake_queue.py`, `runtime/daemon/routes/work_hours.py`, `runtime/infrastructure/work_hours_store.py`, `cli/commands/work_hours.py`, web `features/work-hours-config/WakesView.tsx`. The consolidated Work Hours surface (config overview + wakes tab) lives in `web/src/features/work-hours-config/`.

## Org Portability (THR-187 Slice A)

Slice A is **preflight + reconciliation only** — no archive, export, import,
staging, transfer fence, source deletion, workspace/task-output transfer, or
cancellation of live work. It is CLI-private (`happyranch orgs ...`); there is
no UI, TS client, or browser contract.

- **`happyranch orgs portability-preflight <slug>`** — read-only, founder-
  authenticated. Exhaustively classifies every direct org-root child exactly
  once as `include`, a *named* `exclude`, or `reject`. Allow-list: `happyranch.db`,
  `org`, `artifacts`, `kb`, `threads`, `task-attachments`, `jobs`, `dreams`,
  `work_hours`, `schedules`, `talks`, conditional valid legacy `skills`, and only
  `workspaces/<agent>/memory/**`. Generated markers, derived projection, WAL/SHM
  sidecars, caches, zero-byte legacy residue, and non-memory workspace data are
  named exclusions; unknown/nonregular/nonzero-residue/invalid-skill roots reject.
  Quiescence: refuses any pending/in_progress/escalated task (live, delegated,
  or job-parked), active session/PID or queue entry, pending thread invocation,
  pending/running job/dream/work-hour, or any armed/firing schedule. It *reports*
  possible zombies; it never resolves them.

  **Conservative schedule policy (founder).** Preflight refuses when **any**
  schedule is **armed or firing**; there is no relocation-specific disarm
  command or export fence. The response reports only *existing* controls as
  actionable remedies: an **armed** schedule →
  `happyranch todos pause --org <slug> <schedule_id>` or
  `happyranch todos cancel --org <slug> <schedule_id>`; a **firing** schedule
  → no pause/cancel is permitted, so the correct non-mutating remedy is to wait
  for it to reach a terminal state and re-run the preflight (no new control);
  live nonterminal tasks/active jobs → `happyranch cancel <task_id> --org
  <slug>` / `happyranch jobs stop <job_id> --org <slug>`; active sessions,
  queued items, pending invocations, dreams, and work-hours have no founder
  cancel control → wait/resolve; and a confirmed zombie → the founder-only
  `happyranch orgs reconcile-portability <slug> --from-file <absolute-json>`
  path. Preflight is read-only and never invokes any of these controls.
- **`happyranch orgs reconcile-portability <slug> --from-file <request.json>`** —
  founder/master-bearer only (reuses the existing human-authority dependency).
  Names exactly one candidate + evidence/disposition; revalidates a true zombie
  under the org DB lock; invokes the shared result/terminalization seam
  (`_consume_completion_report` for an orphaned result, or the reaper's
  `cancelled` transition). Audits actor, SHA-256 request hash, evidence,
  disposition, and before/after state under the ordinary `task_id` scope. A
  delegated/job-blocked task is never a zombie merely because it is old.
  Preflight never calls reconciliation; reconciliation has no export-cancellation
  path.

## Knowledge Base

Per-org KB entries live under `<runtime>/orgs/<slug>/kb/`. Orgs do not share a KB. `KBEntry.type` is freeform; route validation only enforces non-empty `slug`, `title`, `type`, and `topic`.

The dedicated `kb precedent` route was removed. Founder rulings flow through `happyranch kb add` with `source_task: <task-id>` in frontmatter.

Implementation: `runtime/infrastructure/kb_store.py` and `runtime/daemon/routes/kb.py`. Full rules: `protocol/06-knowledge-base.md`.

## Per-Agent Learnings

Per-agent memory lives under `<runtime>/orgs/<slug>/workspaces/<agent>/memory/`, one `MEM-NNN-<slug>.md` per entry. CLI: `happyranch memory list|get|search|add|update|promote|reindex`.

Implementation: `runtime/infrastructure/learnings_store.py` and `runtime/daemon/routes/agents.py`. Spec: `docs/superpowers/specs/2026-05-13-per-agent-learnings-structural-upgrade-design.md`.

Traps:

- `PersistentWorkspaceSetup.ensure()` never creates `memory/` when a non-empty flat `learnings.md` exists.
- `happyranch memory promote` is one-way: it replaces the body with a stub and locks the entry.

## Shared Artifacts

Per-org artifacts live at `<runtime>/orgs/<slug>/artifacts/`. They are opaque files produced by any agent and visible to every other agent in the same org.

Implementation: `runtime/infrastructure/artifact_store.py` and `runtime/daemon/routes/artifacts.py`. CLI: `happyranch artifacts {put,list,get}`.

Route surface: `POST /artifacts` (upload), `GET /artifacts` (list), `GET /artifacts/{name}` (download), `DELETE /artifacts/{name}` (delete). There is no update route — `POST` is an idempotent create-or-overwrite. Delete is exposed in the founder web artifacts UI only; there is **no** `happyranch artifacts delete` CLI verb.

Web UI (design-overhaul): **Flat 3-column card grid** (`web/src/features/artifacts/ArtifactsPage.tsx`). Each card shows only stored fields (`name`, `size_bytes` as a formatted size, `modified_at` as a formatted timestamp) plus Download and Delete actions. The artifact record carries **no** agent, task_id, thread, kind/type, dream_id, or PR/CI fields — so the surface renders **no** provenance badges, kind pills, status tags, PR/CI panels, or dream markers. Upload is available via a collapsible form in the page header. States: loading skeleton, calm empty ("No artifacts yet"), error with retry (`invalidateQueries` on `['artifacts', slug]`).

Traps:

- Agent access is CLI-only by design; sandboxed executors block direct writes outside the workspace.
- `artifact_put` **and** `artifact_delete` audit rows use `task_id="artifact:<name>"`; the prefix is mandatory (artifact names are user-controlled and would otherwise collide with `TASK-`/`TALK-`/`SR-` scopes).
- Org config writes (the web Settings PUT — e.g. working_hours under THR-035) emit an `org_config_write` audit row keyed `task_id="config:<section>"` (e.g. `config:working_hours`). Same mandatory scope-prefix convention for non-task actors as `artifact:<name>` above: the `config:` prefix is chosen so the value cannot collide with `TASK-`/`TALK-`/`SR-`/`JOB-` ids. Additive — no schema migration, no column change (`audit_log.task_id` already doubles as the generic scope id).
- Artifacts are blobs, not KB entries. Do not dump markdown that belongs in KB into `artifacts/`.

## Revisit

`happyranch revisit <task-id>` spawns a new root task inheriting brief and team from a terminal predecessor. Old lineage is frozen. It is TTY-gated and has no `--yes` bypass. Spec: `docs/superpowers/specs/2026-04-21-opc-revisit-design.md`.

Eligible predecessor states: failed, cancelled (incl. historical founder-cancelled failed rows), escalated, in_progress/delegated (all children terminal), or completed.

**Auto-resolve forcing function (THR-018 tier #3).** When `revisit` (or a founder/manager thread-dispatch) creates a continuation whose predecessor root is `escalated` / `in_progress(delegated)`, that predecessor is auto-transitioned to the terminal `superseded` state — block_kind cleared, audit citing the new continuation root task_id (+ founder note / thread ruling). This is the maker-checker boundary: auto-resolution fires **only** because a human authorized the continuation; an un-ruled escalation with no continuation is never auto-closed. The close does **not** re-enqueue the predecessor (it would otherwise spawn a wasted manager session), but it preserves parent-wake (`_enqueue_parent_if_waiting`) so a delegated parent still learns its branch reached terminal. The delegated close is gated on **all children being terminal** and never reuses the `cancel` cascade, so live siblings are never SIGTERM'd.

Traps:

- `revisit_of_task_id` is a sideways reference, not an ancestor edge. `walk_ancestors` must not follow it.
- Per-task overrides copied to revisit roots are narrow; explicit human revisit copies only `session_timeout_seconds`.
- Auto-resolve to `superseded` must NEVER fire without a recorded successor task_id / thread ruling in the audit citation. The negative case (un-ruled escalation stays blocked) is a tested invariant.
- On the thread-dispatch path the continuation carries an optional `resolves <task_id>`, honored **only** for a manager-authorized dispatch (the founder supersedes via `revisit`). A worker self-dispatch naming `resolves` is rejected `403 thread_supersede_not_authorized` and never closes the predecessor — the maker-checker boundary, tested both directions.

## Session-Timeout Auto-Route (RETIRED — TASK-3604)

Automatic daemon successor creation on opaque agent failures (timeout, no-callback,
rate-limit, executor error, agent exception) has been **removed** per founder
direction (TASK-3604). Opaque failures now mark the task FAILED and route through
the existing recovery paths: bounded parent-wake for delegated subtasks,
escalation for root exhaustion, and explicit founder `happyranch revisit` for
human-authorized retries.

Legacy `auto_revisit_of` audit rows and the `_auto_revisit_header` / `_revisit_header_if_applicable`
readers remain for historical compatibility — existing DB rows must not become
unreadable. The `AuditLogger.log_auto_revisit_of` method is preserved for the
same reason.

Original spec: `docs/superpowers/specs/2026-05-25-session-timeout-auto-route-design.md` (retired).

Traps (historical):

- The `_maybe_spawn_auto_revisit` function and its helpers (`_classify_failure_kind`,
  `_count_prior_auto_revisits_by_kind`, `_executor_failure_context`) have been removed.
- `_enqueue_parent_if_waiting` no longer receives `root_auto_revisit_spawned=True` —
  all call sites pass `False`.
- `_maybe_post_thread_followup` no longer receives `auto_revisit_spawned=True` or
  `revisit_task_id` from the opaque-failure branches — all pass `False` and
  omit `revisit_task_id`.
- Startup sweep already fails dead sessions without spawning an auto-revisit
  (THR-079 ruling); this is unchanged.

## Bounded Failure-Recovery (TASK-573 / THR-078)

When a subtask fails, the parent task is re-enqueued for a bounded manager-wake
decision step — NOT cascade-failed. This replaces the pre-TASK-573 behavior
where any child FAILED unconditionally cascade-failed the parent.

Contract (founder-approved in THR-028; refined in THR-078):

1. **Bounded wake.** On child failure, re-enqueue the parent for a fresh
   decision step. The failed subtask's reason (`note` + completion report /
   error context) is available so the task owner can author an updated brief.

2. **Per-slice retry ceiling.** Each delegated slot gets exactly one retry
   (THR-078, `_SLICE_RETRY_CEILING = 1`). A second failure of the **same**
   retried slice exhausts the slot and escalates. Determination uses existing
   `revisit_of_task_id` lineage within the same parent — the orchestrator
   walks the revisit chain backward looking for a FAILED ancestor with
   `parent_task_id == parent.id` (`_is_slice_retry_exhausted`). A later
   COMPLETED or SUPERSEDED descendant in the lineage retires earlier FAILED
   ancestors for ceiling evaluation (THR-183). No schema migration, no sibling
   counting.

3. **Root-only escalation on exhaustion.** When the per-slice ceiling is
   exhausted (the retried slice's second failure), the parent transitions to
   `escalated` via `try_escalate()` — **only if `is_root(parent)`** (THR-033
   Change A) — carrying the causal terminal event (the current unresolved
   FAILED leaf) in the escalation reason, not a stale sibling. A non-root
   parent would fail and route upward instead.

4. **Other child failures → bounded manager wake.** A child failure that is
   **not** a retry of a previously-FAILED slice does not count toward the
   ceiling; the parent wakes for a fresh decision step. Multiple independent
   slice failures each produce their own wake, but each distinct slice
   exhausts independently only after its own retry fails.

5. **Fan-out join context.** On a fan-out parent, per-slice terminal context
   (including the exhausted-slice trigger) is injected via
   `_inject_fanout_join_context`, giving the task owner per-slice detail.

6. **Chain-leg failure.** A failed workflow chain leg (subtask FAILED, not
   COMPLETED) clears the active chain and hands the parent back to the
   bounded-wake path (same per-slice ceiling + escalation).

7. **Happy path unchanged.** All subtasks COMPLETED → parent enqueued for
   next decision step. REVISE-verdict auto-advance in chains is unchanged.

8. **Reviewer/QA verdict discipline.** A review/QA leg completes with a canonical verdict — review: `APPROVE | REQUEST_CHANGES | BLOCK`; QA: `PASS | REVISE | BLOCK` (plus the legacy persisted `FAIL`) — and never self-blocks. A `status=blocked`
   with empty `waiting_on_job_ids` is a malformed report; the leg is treated
   as FAILED and wakes the parent for a decision step.

Traps:

- `_SLICE_RETRY_CEILING = 1`: exactly one retry after a slice's first failure;
  the same slice's second failure escalates. `_FAILURE_ROUND_BOUND = 2` is
  kept as a doc-only reference (`protocol/05c`).
- Retry detection: `_is_slice_retry_exhausted` walks the child's
  `revisit_of_task_id` chain; only FAILED ancestors with the same
  `parent_task_id` count toward the ceiling, and a COMPLETED/SUPERSEDED
  ancestor retires earlier FAILED ancestors for ceiling evaluation
  (THR-183). A retry of a previously COMPLETED slice is a fresh dispatch,
  not an escalation trigger.
- Root-only escalation: `is_root(parent)` guard before `try_escalate`; the
  escalation reason names the current unresolved FAILED leaf, not a stale
  sibling. Non-root parents on exhaustion fail and route upward (THR-033
  Change A).
- Escalation clears any active chain/fanout before escalating.
- Chain-advance branch handles FAILED subtasks as well as COMPLETED:
  FAILED subtasks clear the chain and fall through to sibling-check +
  bounded-wake.
## Thread Broadcast Routing

Every `kind=message` thread row is a **conversational arrival** for its recipient set. Since GH-688 Phase 1 (Slice A store + Slice B route/runner wiring), arrivals coalesce: a `(thread_id, agent_name)` pair holds at most one unstarted `REPLY` (queued) and at most one running `REPLY`; a burst advances the queued/running wake's `required_through_seq` instead of minting one invocation per message. The store owns the queued/running token transitions (`record_conversational_arrival`, `claim_conversational_reply`, `settle_conversational_reply`) — routes and the runner never open-code them. Founder participates through the web UI; Feishu is not used for ongoing thread conversation. Spec: `docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md`; approved Phase-1 design recorded in THR-198 and TASK-5437 output.

**Phase-2 mention routing (THR-198, Slice B) is the production routing contract.** The additive columns `threads.mention_routing_enabled` (INTEGER NOT NULL DEFAULT 1 — default enabled for all threads incl. existing) and `thread_messages.mentions_json` (TEXT) persist a server-derived structured mention signal, and the two conversational store seams now resolve the wake set at message-write time from that signal + the thread's setting via `resolve_wake_set` (`runtime/daemon/thread_mentions.py`). Ratified matrix: **disabled** → full participant broadcast minus speaker; **enabled + one/more valid current-participant mentions** → exactly that stable deduplicated set (speaker excluded); **enabled + zero valid mentions** — including no mentions, invalid/nonparticipant-only (`@founder`, typos, terminated), and self-only bodies — → full broadcast. The signal is derived from `body_markdown` at write time (never client-declared; system/decline rows and pre-change history stay NULL). Routing is **write-time frozen**: a participant change after the write does not retroactively re-route already-minted wakes — the next message re-resolves against the current roster. **TASK_FOLLOWUP and BOOTSTRAP are isolated and NEVER mention-routed** (their replies keep the full broadcast). Founder-only toggle: `POST /threads/{id}/mention-routing` (audited `thread_mention_routing_changed`, `task_id=thread_id` scope) and `happyranch threads mention-routing`; `GET /threads*` exposes the boolean. `addressed_to_json` remains unwritten/unread (its separate cleanup plan is intact). **Slice C (merged) adds the per-thread web control**: the founder-only direct thread-detail header button opens a "Mention routing" dialog whose `role="switch"` truthfully renders the thread's current `mention_routing_enabled`, persists explicit changes through the Slice-B strict-boolean `POST /threads/{id}/mention-routing` API (optimistic flip with rollback; same-state server no-ops surface as success via `idempotent`), disables the switch while a change is in flight (no duplicate mutation), shows a visible error + reverts to server state on failure, and is keyboard/switch-accessible; it is routing-only — never priority/fairness. **Slice D (merged) ships the read-only release-measurement harness** (`runtime/infrastructure/thread_release_measurement.py`, documented in `docs/operations/gh-688-phase1-release-checklist.md` §8) reproducing the ratified acceptance metrics (G1 mentioned-message saving 293/499→~24/204, G2 founder coverage 698/0-loss, G3 org-wide decline report-only) over the Phase-2 epoch `2026-08-26T14:25:23Z` with interim labeling until the one-month window completes. **Observation remains pending — a shipped diagnostic is not proof of the population outcome.**

Traps:

- Broadcast is unconditional; declines are silent.
- Decline-by-default doctrine is prompt-injected for `REPLY`, not in `protocol/skills/thread/SKILL.md`.
- Agent replies no longer enforce a hard `turn_cap` ceiling; turn count is still tracked and displayed but cap enforcement was removed per THR-046.
- **Coalescing (GH-688 Phase 1).** A burst creates at most one unstarted `REPLY` per pair; new queue tokens are enqueued only after the arrival transaction commits. An arrival while queued or running only advances `required_through_seq`. A successful reply/decline acknowledges **only the claimed coverage** (the immutable `running_through_seq` snapshot at claim); arrivals during the run yield exactly one post-settlement follow-on. Failure/timeout never hot-loop: the range stays unacknowledged, projects `retry_required`, and the next conversational arrival covers the retained plus new range.
- **Runner claim (GH-688 Phase 1).** A conversational `REPLY` must pass the durable queued→running CAS (`claim_conversational_reply`) before any prompt materialization or provider work; a stale/duplicate queue notification no-ops there. The per-`(thread, agent)` in-memory lock remains for process-local serialization only — it is not the durability mechanism. The prompt explicitly states the claimed inclusive `[running_from_seq, running_through_seq]` range and renders each required message in order.
- **Settlement is exactly-once.** Route reply/decline and every runner terminal path (clean-no-callback, provider failure, timeout, materialization failure, runner crash) settle through the store. Abort/archive/participant-removal discard through an explicit boundary (`discard_reply_delivery`); discarded wakes never resurrect and a later message starts after the boundary.
- **Startup recovery (GH-688 Phase 1).** `_sweep_on_startup` replaces only the conversational `REPLY` portion of the generic reaper: a valid queued wake — a pending, **unstarted** same-pair `REPLY` (the claim CAS enforces the same precondition) — is retained and re-enqueued; an interrupted running `REPLY` is terminalized once as `daemon_restart` and replaced by exactly one queued wake. A malformed queued slot referencing a **started** receipt fails closed: the owned pending `REPLY` receipts for the pair are retired with `invalid_queued_started_on_recovery`, the slot clears, nothing is re-enqueued, and the preserved `required_through_seq` lets the next conversational arrival mint the single covering wake. `BOOTSTRAP` and `TASK_FOLLOWUP` keep the generic `daemon_restart` reaping unchanged.
- **Wire contract (GH-688 Phase 1).** `GET /threads/{id}` and `GET /threads/{id}/messages` carry a pair-level `reply_delivery` projection (queued | running | retry_required, inclusive `from_seq`/`through_seq`, store-computed `coalesced_message_count`, `started_at`, `last_terminal_reason`). It is derived from `thread_reply_delivery_state`, never fabricated from per-message rows, and never claims a subprocess exists beyond `started_at`. Historical per-message `responder_status` strips are unchanged in shape and now ALSO carry the authoritative invocation `purpose` (`reply` | `task_followup`; BOOTSTRAP stays excluded from the grouped query) so wire classification never has to infer purpose from the triggering row kind — a coalesced REPLY delivery range can anchor on a SYSTEM row (the follow-on mint keys the first unacknowledged sequence, which may be a system divider).
- **UI presentation (GH-688 Phase 1 Slice C).** The thread detail rail shows a compact "Reply delivery" section driven ONLY by the store projection: current `running` pairs stay individually visible first with full wrapping agent identities; `queued` pairs are visually distinct and grouped behind a native keyboard/screen-reader disclosure with truthful singular/plural counts; `retry_required` stays visible as a diagnostic. Detail captions preserve the inclusive range: queued reads "N message(s) coalesced · message(s) F–T" (static — never an active subprocess), running reads "replying · messages F–T" (the only subprocess evidence is `started_at`), and retry-required reads "retry required · messages F–T · last: <reason>". Fully settled pairs remain absent from this live store projection; their truthful terminal evidence remains in the per-message responder history rather than being recreated in the rail. The transcript tail's live indicator (TypingBubble) is driven by the same pair projection; inferred per-message rows remain only for special-purpose wakes (BOOTSTRAP / TASK_FOLLOWUP) that are intentionally outside `reply_delivery` — preserved even when the same agent concurrently holds a conversational REPLY pair. Inferred-row suppression is purpose-aware (the wire `purpose`, never the triggering row's kind): a REPLY whose coalesced range anchors on a system row still reads `purpose=reply` and is masked by its pair row — exactly one replying bubble — while a same-agent TASK_FOLLOWUP stays visible (never agent-name-only). Per-message terminal responder history is unchanged (and now also renders under SYSTEM rows, so a settled system-row-anchored REPLY range shows its terminal `replied` marker). SSE tail events and the send/invite/remove/archive/resume/abort mutations invalidate the thread-detail query so the projection stays fresh — no new wire events were added.
- **Audit lifecycle (GH-688 Phase 1 Slice C).** The six approved actions — `thread_reply_wake_created`, `thread_reply_wake_coalesced`, `thread_reply_wake_claimed`, `thread_reply_wake_settled`, `thread_reply_wake_cancelled`, `thread_reply_wake_recovered` — are emitted ATOMICALLY inside the store transitions (`record_conversational_arrival`, `claim_conversational_reply`, `settle_conversational_reply`, `discard_reply_delivery`, `recover_reply_delivery_state`) using the existing `audit_log.task_id = THR-*` scope convention. Duplicate queue notifications and idempotent recovery can never fabricate events (stale claim CAS no-ops, `seq <= required` idempotent arrivals, and pure slot-clears emit nothing). Payloads carry only truthfully observed fields — agent, inclusive range, 8-char token prefix, outcome/reason/follow-on — never full single-use tokens.

## Thread Agent-Session Resume

Claude-backed thread participants reuse their Claude session across turns. State lives on `thread_participants.agent_session_id` and `last_resumed_seq`. Plan: `docs/superpowers/plans/2026-06-02-thread-claude-session-resume.md`.

Implementation: `runtime/daemon/thread_runner.py`, `runtime/orchestrator/executors.py`, `runtime/infrastructure/database.py`, and `runtime/infrastructure/audit_logger.py`.

**TASK-5977 (THR-200 seq 31) parity:** the same resume machinery is proven and shipped for **codex** (installed codex-cli 0.148.0: `codex exec resume <thread_id> --json -`, same `thread.started.thread_id` re-emitted after continuation, prompt via stdin) and **pi** (installed pi 0.84.2: `pi -p --mode json --session <id>`, same session header `id` re-emitted, prompt via stdin). OpenCode is an UNPROVEN gap (no binary on this machine) — it stays fresh. Evidence-backed per-executor support matrix lives in `protocol/05b-agent-runtime.md` (Thread provider-session lifecycle).

Traps:

- Optimization, never a correctness dependency — for claude/codex/pi only.
- `last_resumed_seq` advances only after a successful subprocess.
- Per-`(thread, agent)` `asyncio.Lock` protects read-run-update.
- **Eviction invalidation is transactional (THR-200).** On a provider-declared
  session-not-found, the `agent_session_evicted_fallback` audit and the durable
  `agent_session_id = NULL` invalidation commit in ONE transaction BEFORE the
  full-prompt fallback; a failed fallback leaves the id NULL and the delivery
  watermark unadvanced. Classified ONLY from the executor that ran, reading
  ONLY the proven return code and stream, and REQUIRING the anchored
  provider-declared signature bound to the regex-escaped attempted session id
  — claude `No conversation found with session ID: <attempted-id>` (rc=1
  stderr), codex `no rollout found for thread id <attempted-id>
  (code -32600)` (rc=1 stderr), pi `No session found matching
  '<attempted-id>'` (rc=1 stderr) — each verified 2026-08-28 to echo the
  attempted id verbatim. Generic legacy substrings, cross-provider text,
  stdout-only text, wrong rc, wrong/missing id, prefix/suffix near-matches,
  a marker embedded in auth/quota/transport output, and generic failure /
  auth / quota / transport / ambiguous output never trigger the fresh retry.
- **Resume eligibility (<=0 watermark).** A stored provider id whose
  `last_resumed_seq` is null/zero/negative (<= 0) is never eligible for
  resume — the runner must make a fresh invocation with the complete
  canonical transcript; resume requires a strictly positive delivered
  frontier (plus the full contiguity proof).
- **Lifecycle invalidation (THR-200).** Archive, successful executor switch, and
  agent termination clear resume state (id NULL, watermark 0). Each boundary is
  a database-owned transaction: the participant reset and its
  `thread_session_invalidated` audit commit atomically (termination runs
  reset+audit inside the existing terminate-cleanup transaction; archive wraps
  the status flip, every participant reset, and the audit in one transaction).
  A reset/audit failure leaves no partial lifecycle state — a failed switch is
  rolled back (no new executor installed) and a failed archive leaves the
  thread OPEN with every session row unmodified. Participant removal deletes
  the row and its session state together (no redundant clear).
- **No fingerprint invalidation.** The proven codex/pi contracts resume across
  model/config changes, so no executor/model/config fingerprint column is added.
- **Equality self-heals (THR-200).** `last_resumed_seq == running_from_seq` runs
  the full prompt (never omits a required sequence) and recovers after one
  successfully settled full-prompt turn — no watermark-comparison change.
- **Transport (THR-200).** Claude/Pi/Codex deliver the prompt on stdin
  (pinned-version canaries) — resume prompts included; opencode/generic-CLI
  stay argv-based with a pre-spawn `prompt_transport_too_large` guard. Encoded
  byte size is transport-only, never a cost/reset policy.
- **pi uses `--session`, never `--session-id`.** `--session` fails when the id
  is missing (the eviction signature); `--session-id` would silently CREATE a
  fresh session and omit transcript messages.
- **TASK execution never resumes provider sessions.** Only
  `thread_runner.run_invocation` passes `resume_session_id`; the orchestrator's
  task path and wake/dream runners always launch fresh. Pinned by
  `tests/test_thread_resume_parity.py`.
- `ExecutorResult.agent_session_id` is not `ExecutorResult.session_id`.
- **GH-688 Phase 1 claim gate + no-message-omission proof.** For a claimed conversational `REPLY`, a resumed session may use the delta only when the stored watermark is strictly below the claim's `running_from_seq` AND the ENTIRE required post-watermark range is proven present and contiguous at the production seam: the runner loads the canonical transcript UNCAPPED, independently queries the authoritative transcript max, and authorizes the delta only when every required claimed sequence exists (the claim's inclusive end must also exist). Truncated loads, internal holes, equal/ahead watermarks, a null/zero/negative (<= 0) watermark (a stored id with watermark <= 0 is never eligible), and claim ends beyond the transcript fail closed to the genuinely complete canonical full prompt — a delta can never omit a message the delivery state requires. `last_resumed_seq` is observed session presentation and is never the delivery cursor.

## Thread Task Followup

When a task dispatched from a thread reaches true terminal state, `_maybe_post_thread_followup` appends a system message and mints a `TASK_FOLLOWUP` invocation. Spec: `docs/superpowers/specs/2026-05-28-thread-task-followup-design.md`.

Traps:

- Helper runs after task failure is finalized (no auto-revisit spawns; the task is already terminal FAILED).
- Only root tasks fire followups.
- Dispatcher identity comes from the `task_dispatched` audit row.
- Cross-thread enqueue uses `asyncio.run_coroutine_threadsafe(queue.put(job), main_loop)`.
- Terminal gate is completion/failed **plus** `superseded` (completion-class → `task_completed` kind). A thread-originated task auto-resolved by a continuation must still emit its followup; missing this terminal silently drops the superseded state from the thread lifecycle.
- **GH-688 Phase 1 isolation.** `TASK_FOLLOWUP` is a causal one-shot direct mint (`mint_followup_invocation_with_cap_extend`), never coalesced into reply delivery state and never routed through the claim/settle surface — even with a reply backlog on the same pair, the followup fires as its own `TASK_FOLLOWUP` row.
- **Daemon lifespan ordering (THR-109).** `_attach_thread_queue_wiring` must run before `ensure_workers_started` so the orchestrator's `_thread_queue` and `_main_loop` references are populated before any task worker can execute a step. Without this ordering, a rapid terminal task fires `_append_followup_system_and_reinvoke` while those references are still `None`, producing `enqueue_unavailable` and stranding the invocation as permanently pending. The blocked-on-job recovery after wiring still has a wired queue (THR-109 PR).

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

- Applies to task owners and subtask agents uniformly.
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

## PR CI Wait / Guarded Merge

Traps:

- SHA-pin every wait. If the PR head changes, stop with `stale_head`; do not continue polling the new head silently.
- "No checks" is not pass. Use required contexts when branch protection provides them, otherwise require explicit expected checks and a bounded settle window.
- GitHub `mergeable` / `mergeStateStatus` is not CI green. It is one guard in addition to CI pass.
- Do not use `gh pr merge --auto` as proof of safety while required checks are absent on `main`.
- Merge completion is allowed only through the guarded helper or founder-reviewed job, not broad worker `gh pr merge` allow-rules.
- The poll job entrypoint is `python -m runtime.daemon.pr_ci_waiter --repo ... --pr N --head-sha <sha> --expected-check ...`; the merge entrypoint is `python -m runtime.daemon.pr_ci_merge --org ... --repo ... --pr N --head-sha <sha> --merge-method ... --ci-verdict ... --review-task-id ... --qa-task-id ...`. Both print structured JSON to stdout and exit with mapped codes.

## Feishu Notifications (REMOVED)

Feishu was removed in TASK-302 (THR-022). The web UI and threads are the sole control path for dispatch / revisit / resolve-escalation. Legacy `feishu_notifications` config blocks are tolerated on load but ignored. Database correlation tables (`escalation_notifications`, `processed_event_ids`) remain dormant in place.
