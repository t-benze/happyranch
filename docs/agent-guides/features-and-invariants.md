# Features And Invariants

This file collects feature-specific traps that should be read only when touching the relevant surface.

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
