# Orchestrator Contracts

## Conventions

- Type hints on all function signatures.
- `from __future__ import annotations` in every source file.
- Pydantic v2 for structured data.
- `StrEnum` for enumerations.
- Agent names are plain strings; agents are discovered dynamically from `<runtime>/orgs/<slug>/org/agents/*.md`.
- Tests should cover business logic such as escalation rules and audit-log shape.

`README.md` is for end users. `CLAUDE.md` is for repo-wide agent instructions. Design docs in `protocol/` and specs in `docs/superpowers/specs/` are the source of truth for behavior.

When starting a feature, read the relevant design doc first and follow existing patterns in `runtime/orchestrator/`.

## Org Content APIs

`AgentDef` in `runtime/orchestrator/agent_def.py` represents an agent file: markdown with YAML frontmatter parsed/rendered by `parse_agent_text` and `render_agent_text`.

Fields: `name`, `team`, `role`, `executor`, `description`, `allow_rules`, `repos`, `enrolled_by`, `enrolled_at_task`, `enrolled_at`, `model`, and `system_prompt`. There is no `session_timeout_seconds` field.

**THR-095 single-source-of-truth:** `executor`, `repos`, and `model` are read
exclusively from ``AgentDef`` (the ``.md`` frontmatter). The workspace
``agent.yaml`` is no longer read or written by any org-agent path. See
`docs/agent-guides/runtime-and-configuration.md#agent-configuration-single-source-of-truth-thr-095`.

`runtime/orchestrator/prompt_loader.py` is the API for reading/writing agent files: `load_agent`, `list_agents`, `list_pending`, `write_pending_agent`, `approve_agent`, `reject_agent`, `load_terminated_agent`, `list_terminated`, `is_terminated`, and `is_name_unavailable`. Routes and orchestrator code should read through this module against the per-org root.

`TeamsRegistry` in `runtime/orchestrator/teams.py` is seeded from `teams.yaml` and auto-persists on `add_worker` and `remove_worker`. There is no `DEFAULT_LAYOUT`; an org without `teams.yaml` is empty.

## Agent Lifecycle: Enrollment, Approval, and Termination

- **Enrollment.** `manage-agent enroll` creates a pending agent file under `org/agents/_pending/<name>.md`. A founder (or team manager with an active session) may enroll agents only into their own team.
- **Approval.** `POST /agents/{name}/approve` atomically moves the pending file to `org/agents/<name>.md` and bootstraps the workspace under `workspaces/<name>/`. Approved agents appear in `GET /agents` and `GET /agents/enrollments?status=approved`.
- **Termination.** `manage-agent terminate` archives an approved **non-manager worker** on the caller's team. It is refused if the agent is a manager, belongs to another team, or has live work. Live work includes non-terminal tasks assigned to the agent, already-started thread invocations, firing schedules, running work-hours wakes, running dreams, or pending/running jobs attributable to the agent. If the agent is quiescent, the route:
  - archives the active `org/agents/<name>.md` to `org/agents/_terminated/<name>.md`;
  - archives the workspace `workspaces/<name>/` to `workspaces/_terminated/<name>/`;
  - removes the worker from its team;
  - cancels armed schedules, skips pending wakes/dreams, and declines not-yet-started thread invocations with reason `agent_terminated`.
- **Historic records are retained.** Tasks, task results, audit rows, token-usage rows, thread messages/participants, schedules, wakes, dreams, and archived files are never deleted or rewritten. The agent name cannot be re-enrolled while a terminated record exists, so historical identity remains unambiguous.
- **Fail-closed launch.** The orchestrator and thread runner refuse to launch an agent whose active `.md` file is missing or archived. There is no silent fallback to `claude` for an unknown/terminated agent.
- **Enumeration.** `GET /agents` and the default `GET /agents/enrollments` return active agents only. `GET /agents/enrollments?status=terminated` returns archived enrollment metadata.

## Task Status Vocabularies

Agents self-report `status="completed"|"blocked"` via `happyranch report-completion` (the report verb is unchanged — an agent still self-reports "blocked on jobs"). The orchestrator-owned `TaskStatus` on the `tasks` row is distinct, and under THR-037 Change B (Path B) is: `pending`, `in_progress`, `escalated`, `completed`, `failed`, `cancelled`, or `superseded`. (`blocked` is fully retired as of Phase 3 — see the Path-B spec.)

`block_kind` is the waiting-reason discriminant for an `in_progress` task — *what it is internally waiting on*: `delegated` (waiting on child subtasks) or `blocked_on_job` (waiting on background jobs). `block_kind IS NULL` ⟺ a subprocess is running now. A parent waiting on its children/jobs stays `in_progress` (not `blocked`); the await-founder state is the top-level `escalated`.

`superseded` is a terminal state, peer to `completed`/`failed`. An `escalated` / `in_progress(delegated)` task transitions here when a human-authorized continuation (founder `revisit`, or a founder/manager thread-dispatch) names it in lineage: the predecessor is closed (block_kind cleared, audit cites the continuation root task_id) instead of being re-run. The close never re-enqueues the superseded task; it still wakes a delegated parent via the normal parent-wake path, and the delegated close is gated on all children being terminal so no live sibling is abandoned or SIGTERM'd. It joins every terminal predicate (`TERMINAL_STATES`, `_TERMINAL_TASK_STATUSES`, `_TERMINAL_STATUS_TO_EVENT`) and is completion-class for the thread task-followup: a thread-originated task that is superseded emits its `_maybe_post_thread_followup` system message (`task_completed` kind) just like a normal completion. The thread-dispatch supersede is manager-authorized only — a worker self-dispatch naming `resolves` is rejected (`403 thread_supersede_not_authorized`); the predecessor is never auto-closed by an unauthorized dispatch. Query the backlog with `happyranch tasks --status escalated` or `happyranch tasks --status in_progress --block-kind delegated`.

## Derived Work-Status Summary (TASK-5522)

`GET /tasks/{task_id}` carries a read-only `work_status` envelope key derived
server-side (`runtime/daemon/work_status.py`) from the task record plus its
existing audit rows — **no schema change, no synthetic audits, no background
monitor**. It exposes only: the current-session start (latest assigned-agent
`session_start` audit), the last heartbeat with an explicit freshness label,
and the timestamp + concise agent-written message of the latest current-
session `progress` receipt. Chain of thought, command stdout, workspace
paths, session ids, and arbitrary audit payloads are never exposed.

State machine (live-task shape = `in_progress` + `block_kind IS NULL`):

| state | meaning |
|---|---|
| `newly_started` | fresh heartbeat; no current-session receipt; session start < 5m old |
| `recent_progress` | fresh heartbeat; latest current-session receipt < 5m old |
| `stale_no_receipt` | fresh heartbeat; no receipt; session start ≥ 5m old |
| `stale_old_receipt` | fresh heartbeat; latest receipt ≥ 5m old |
| `heartbeat_stale` | live shape but heartbeat ≥ 60s old (existing zombie-reaper freshness semantics) |
| `heartbeat_unavailable` | live shape, no heartbeat observed |
| `unavailable` | cannot derive (missing session_start, unassigned, malformed historic data) |
| `not_applicable` | terminal / pending / escalated / in_progress parked-on-block (`reason` discriminates) |

Policies: `STALE_PROGRESS_AFTER_SECONDS = 300` (5-minute display/derivation
policy — it never reaps or acts); heartbeat freshness reuses the existing
60-second semantics (`2 × HEARTBEAT_INTERVAL_SECONDS`). The current-session
lower boundary is the latest assigned-agent `session_start`; a prior
session's `progress` receipts must never satisfy the new session. Labels say
what is observed — a fresh heartbeat is never presented as substantive
progress, and absent/malformed data is surfaced as unavailable, never
fabricated. Both `happyranch details` and the Tasks UI render this summary;
`protocol/skills/start-task/SKILL.md` §5 makes the corresponding worker
checkpoint policy concrete.

### Post-deploy operational measurement (not a shipping gate)

The per-task states above make **individual** tasks observable; they do not,
by themselves, measure the population metric this change is meant to move.
That metric is the **share of COMPLETED tasks whose wall-clock duration is
strictly greater than 15 minutes**, measured by the read-only per-org SQL
procedure below — never from `progress` audit rows. `progress` receipts /
`work_status` are a diagnostic companion measure only (see "What this
contract does and does not make observable" below).

**Pre-deploy baseline (authoritative).** Immediately before this change
shipped, **277 of 600 completed tasks (46.2%)** exceeded a 15-minute
wall-clock duration. **46.2% — never 55%** — is the baseline this deployment
is measured against. The post-deploy operational/release target is **<20%**
of completed tasks exceeding 15 minutes, evaluated inside the explicitly
defined post-deploy observation window below. That target is an
**operational post-deploy goal only**: it is NOT a PR shipping, approval,
merge, or CI gate, and no CI or merge check enforces it.

**Observation procedure (read-only SQL, per org).** Each org is measured
independently against its own database — the org boundary is the per-org
SQLite file `<runtime>/orgs/<slug>/happyranch.db` (the daemon's
`OrgPaths.db_path`, `runtime/orchestrator/_paths.py`). Orgs are never
pooled: the evaluator must substitute each org's actual storage scope and
timestamps. The window is **half-open** `[window_start, window_end)` and
uses the task **completion time** (`tasks.completed_at`) for membership; the
numerator applies the same bounds and additionally requires a wall-clock
duration **strictly greater than 15 minutes (900 seconds)**. Wall-clock
duration is the difference between the persisted completion and creation
timestamps (`tasks.completed_at` − `tasks.created_at` — both columns are
non-null on every `completed` row and store ISO-8601 UTC text, so
`julianday(...)` arithmetic applies directly; this is the full lifecycle
wall clock from task creation to completion, an upper bound on active work
time). Run the query once per org DB file:

```sql
-- Per-org read-only observation: completed-task wall-clock > 15 min share.
-- Open the org's DB read-only:  sqlite3 "file:<runtime>/orgs/<slug>/happyranch.db?mode=ro"
-- Bind the evaluator's actual values (sqlite3 CLI: .parameter init, then
-- .parameter set :window_start '<utc-iso>'; .parameter set :window_end '<utc-iso>'):
--   :window_start  post-deploy observation window start, inclusive (UTC ISO)
--   :window_end    post-deploy observation window end,   exclusive (UTC ISO)
WITH windowed AS (
  SELECT (julianday(t.completed_at) - julianday(t.created_at)) * 86400 AS dur_seconds
  FROM tasks AS t
  WHERE t.status = 'completed'
    AND t.completed_at >= :window_start
    AND t.completed_at <  :window_end
)
SELECT
  COUNT(*)                                           AS denominator,
  SUM(CASE WHEN dur_seconds > 900 THEN 1 ELSE 0 END) AS numerator,
  CASE
    WHEN COUNT(*) = 0 THEN NULL  -- zero denominator => N/A, never 0%
    ELSE ROUND(
      100.0 * SUM(CASE WHEN dur_seconds > 900 THEN 1 ELSE 0 END) / COUNT(*),
      1
    )
  END                                                AS pct_over_15_min
FROM windowed;
```

Procedure notes:

- **Per-org, never pooled.** Re-run the query against each org's own DB file
  and report each org's `denominator` / `numerator` / `pct_over_15_min`
  separately. Do not aggregate orgs into one denominator.
- **Strict inequality.** The numerator counts `dur_seconds > 900`; a task
  whose duration equals exactly 15:00.000 does not count.
- **Half-open window.** Membership is `completed_at >= :window_start` AND
  `completed_at < :window_end`; a task completing exactly at `:window_end`
  belongs to the next window.
- **Zero denominator.** An org/window with no completed tasks yields
  `denominator = 0` and the percentage is **N/A** (the `CASE` yields NULL) —
  never 0%, which would falsely claim the target was met.
- **Read-only.** The query contains no writes; open the DB in read-only mode
  (`?mode=ro`) or run it against a snapshot/copy.

**Post-deploy observation window.** The operator defines a fixed half-open
window at deployment time — for example the 30 days following the deploy
timestamp — and evaluates the same query with `:window_start` = deploy
timestamp and `:window_end` = window end. The pre-deploy baseline 277/600
was measured with the same definition over the pre-deploy completed-task
population.

**What this contract does and does not make observable.** `action=progress`
remains optional and is the only persisted agent-written substantive receipt
in the current contract. When it is absent, the server-derived `work_status`
explicitly reports `newly_started` (session under 5 minutes) or
`stale_no_receipt` ("no substantive update recorded") for a live session —
heartbeat is liveness evidence only and is never substantive work. That
absence classification makes silence observable for operational follow-up
(a live-but-silent task is visibly distinguishable from one with recent
substantive progress). However, a progress-only audit query cannot prove the
implementation moved the >15-minute completion-duration metric: receipts are
optional and the population metric is defined over completed-task wall-clock
durations, not receipts. The primary metric is therefore measured from
completed-task wall-clock durations by the per-org query above;
`progress` / `work_status` may be used only as a diagnostic companion
measure.

## Manager Decision Contract

Team-manager completion payloads carry two fields:

- `summary`: human-readable prose stored on `task_results.output_summary` and rendered in details, audit logs, and `task_history.md`.
- `decision`: a JSON `NextStep` object stored on `task_results.decision_json` and parsed directly by `Orchestrator._parse_next_step`.

The child-task brief field in a `delegate` decision is `prompt`, not `brief`. Pydantic v2 silently ignores extras, so `"brief"` creates an empty-brief child task.

Full schema and examples: `protocol/00-completion-contract.md`.

## Inline Delegation Chains

A manager can declare a multi-leg workflow in one `delegate` decision using `NextStep.then` and optional per-leg `expect_verdict` gates. The orchestrator auto-advances to the next leg when a child terminates completed with a matching verdict. Since THR-211, auto-advance may also fire from a child whose completion report has durably landed while its task row still reads `in_progress` (the completion-status-lag window) — the recognition is session-safe and at-most-once, and the chain gate consumes the exact authenticated `(task_id, assigned_agent, current_session_id)` report so a newer unrelated row can never advance or clear the chain; see `protocol/00-completion-contract.md` and `protocol/05c-orchestrator.md` (Completion-status lag).

Implementation: `runtime/orchestrator/chain.py` and `runtime/orchestrator/run_step.py`. Spec: `docs/superpowers/specs/2026-05-30-inline-delegation-chain-design.md`.

Example:

```json
{
  "action": "delegate",
  "agent": "dev_agent",
  "prompt": "Build the feature...",
  "then": [
    {"agent": "senior_dev", "prompt": "Code-review the PR.", "expect_verdict": "APPROVE"},
    {"agent": "qa_engineer", "prompt": "QA the PR.", "expect_verdict": "PASS"}
  ]
}
```

## PR CI Wait / Guarded Merge

Merge-scoped engineering tasks are complete only after the PR is merged. PR creation, review APPROVE, or QA PASS alone are not terminal completion.

Use the first-class PR CI helper to submit a bounded job pinned to the PR head SHA, then block with `waiting_on_job_ids`. On resume, complete only if the job's structured verdict proves the PR was merged at that SHA. If the helper reports stale head, timeout, CI failure, missing checks, non-clean mergeability, or merge failure, re-ground instead of marking done.

The helper must enforce: review APPROVE, QA PASS, CI PASS, unchanged head SHA, mergeable CLEAN, open/non-draft PR, configured merge method. It must not rely on `gh pr merge --auto` when branch protection lacks required checks.

## Task/Subtask Terminology

The data model uses `task_type` `Literal['task','subtask']`:

- **Task** (`task_type='task'`): the task owner — holds the decision-making
  loop and produces `decision` blocks (`delegate`/`fanout`/`done`/`escalate`; the `parallel` alias is accepted for `fanout`).
- **Subtask** (`task_type='subtask'`): the delegated agent — executes a
  bounded unit of work and reports a plain completion (no `decision` field).

Prose in docstrings, comments, and prompt strings prefers "task owner" and
"subtask agent" over the legacy "team manager" / "worker" language. The
`task_type` enum values were already correct before TASK-573; the sweep only
updated prose, not schema or role-identity strings.

## Bounded Failure-Recovery (TASK-573)

When a subtask fails, the parent task is re-enqueued for a bounded manager-wake
decision step — NOT cascade-failed. This replaces the pre-TASK-573 behavior where
any subtask FAILED unconditionally cascade-failed the parent without giving the
task owner a chance to re-ground.

Contract (founder-approved in THR-028, refined in THR-078):

1. **Bounded wake.** On child failure, re-enqueue the parent for a fresh
   decision step. The failed subtask's reason is available so the task owner can
   author an updated brief.

2. **Per-slice retry ceiling (THR-078).** A delegated slot gets exactly one
   retry: the Ceiling is `_SLICE_RETRY_CEILING = 1` — a slice that already had a
   FAILED predecessor under the same parent (tracked via `revisit_of_task_id`
   lineage, evaluated by `_is_slice_retry_exhausted`) and fails again exhausts
   the ceiling. Retry of a COMPLETED predecessor does not count toward the
   ceiling, and a later COMPLETED or SUPERSEDED descendant in the same lineage
   retires earlier FAILED ancestors for ceiling evaluation (THR-183).

3. **Escalation on exhaustion.** When a slice's retry ceiling is exhausted
   (its 2nd failure), a root parent transitions to `escalated` via
   `try_escalate()`, carrying the causal terminal event — the current
   unresolved FAILED leaf of the slice's lineage — in the escalation reason;
   a completed-child wake cannot select a stale sibling reason. A non-root
   parent fails and recurses upward (THR-033 root-only escalation). The parent
   does NOT cascade-fail.

4. **Chain-leg failure.** A failed workflow chain leg (subtask FAILED, not
   COMPLETED) clears the active chain and hands the parent back to its
   bounded-wake path (same per-slice ceiling + escalation).

5. **Happy path unchanged.** All subtasks COMPLETED → parent enqueued for
   next decision step. REVISE-verdict auto-advance in chains is unchanged.

6. **Reviewer/QA verdict discipline.** A review/QA leg completes with an
   APPROVE/REVISE/PASS/FAIL verdict and never self-blocks. A `status=blocked`
   with empty `waiting_on_job_ids` is a malformed report; the leg is treated
   as FAILED and wakes the parent for a decision step.

Traps:

- Retry ceiling is per-slice: `_is_slice_retry_exhausted` walks the failing
  child's `revisit_of_task_id` chain; only a FAILED predecessor under the
  same parent triggers escalation. COMPLETED/SUPERSEDED predecessors retire
  earlier FAILED ancestors for ceiling evaluation (THR-183).
- Ceiling constant: `_SLICE_RETRY_CEILING = 1` (one retry after a slice's
  first failure).
- The exhaustion escalation uses `try_escalate` (atomic CAS under Database
  RLock) for roots and names the current unresolved FAILED leaf, not a stale
  sibling reason; non-root parents fail and hand upward.
- Chain-advance in `_enqueue_parent_if_waiting` handles FAILED subtasks:
  failed chain legs clear the chain and fall through to bounded-wake.
- Self-block (`status=blocked` + empty `waiting_on_job_ids`) is a malformed
  report that fails the review/QA leg. Never self-block in a review/QA role.

Inline traps:

- Auto-advances do not consume orchestration steps. Declaring a chain costs one step; the final-leg wake costs one.
- A final-leg match still wakes the manager. Chains never auto-`done`.
- Cross-team validation runs on every leg at parse time. An off-team agent on any leg rejects the whole decision.
- Do not pre-embed upstream context in a leg prompt; `build_prior_leg_context` appends it automatically.

## Daemon-Restart Sweep (THR-064)

On daemon restart, `_sweep_on_startup` recovers tasks that were killed mid-flight.
Branch 1 (in_progress + block_kind IS NULL — a live subprocess killed by the restart):

1. **Mark failed with restart context.** The killed child's note is enriched to
   `"daemon_restart -- infra fault, not a code failure; status-assess the
   branch/PR/CI and adopt already-pushed work before re-dispatching"`. This
   note surfaces to the parent manager via `_build_prior_steps_from_db`
   (as `result_summary`), so the manager can ground its next decision on the
   failure cause rather than treating it as a code bug.

2. **Parked-ancestor recovery.** The sweep marks killed children FAILED. If a
   killed child has a parked non-terminal ancestor
   (`in_progress` + `block_kind` in `{DELEGATED, BLOCKED_ON_JOB}`),
   bounded-parent recovery wakes the ancestor directly — no duplicate root
   is created. This is the same bounded-wake path used for any child failure.

3. **No auto-revisit (THR-079 + TASK-3604).** Startup recovery does NOT spawn
   an auto-revisit successor — the THR-079 ruling superseded the earlier
   heartbeat/revisit approach, and TASK-3604 removed automatic successor
   creation entirely from the run_step path. Dead in_progress tasks are
   fail-closed; the founder receives a `daemon_restart_failure` audit row
   and decides whether to re-dispatch.

4. **Fan-out barrier preserved.** A restart-killed child among still-live
   siblings does NOT wake the parked root early — only marking the killed
   child FAILED without enqueuing the parent. The existing N-wide all-children-
   terminal barrier in `_enqueue_parent_if_waiting` resolves when all legs
   report. The restart note survives to that eventual wake.
