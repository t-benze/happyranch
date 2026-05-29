# Task blocked-by-job — Design Spec

**Date:** 2026-05-28
**Status:** Draft, pending implementation plan.

**Supersedes (partially):**
- `docs/superpowers/specs/2026-05-26-jobs-design.md` — §2 "No 'task wakes itself' channel" non-goal is reversed. The reasoning is documented in §1 below.

**Relates to:**
- `docs/superpowers/specs/2026-05-28-thread-task-followup-design.md` — the inverse bridging shape (task terminal → thread re-invocation). This spec mirrors that pattern for job terminal → task resume.
- `docs/superpowers/specs/2026-04-21-opc-revisit-design.md` — the manual unblock path (founder runs `grassland revisit <task-id>`). Still valid for founder-driven recovery; no longer the *only* unblock path for blocked-on-job tasks.

## 1. Goal

Today, when an agent submits a job and cannot proceed without its result, the agent must self-block with `report-completion status=blocked` and the founder must manually run `grassland revisit <task-id>` after the job runs. For `review_required=true` jobs this is fine — the founder is already in the loop. For `review_required=false` jobs (agent-autonomous), it is friction: the daemon ran the job autonomously, the agent self-blocked autonomously, and yet the unblock requires a manual founder step. The agent's autonomy ends at the unblock boundary for no good reason.

This spec adds an auto-resume channel: when a task is blocked on a list of jobs, the system resumes the task automatically once every listed job reaches a terminal state. The bridging shape mirrors the thread task-followup helper (spec 2026-05-28) — same primitives, opposite direction.

### 1.1 Why this reverses the original `jobs-design.md` §2 non-goal

The original jobs spec explicitly listed *"Auto-unblock on completion. The agent self-blocks (when it chose to); founder uses `grassland revisit` to unblock. No 'task wakes itself' channel."* as out-of-scope. That call was made before the thread task-followup bridge shipped on 2026-05-28. The followup bridge proves the architecture supports terminal-state-in-subsystem-A → invocation-in-subsystem-B cleanly, with race-safe CAS, cross-thread enqueue from a worker, and audited-skip when state guards fail. Mirroring that pattern for the job → task direction is mechanical, not novel.

## 2. Non-goals

Out of scope for this spec:

- **ANY-terminal resume policy.** Only ALL-terminal — the task resumes only after every listed job is terminal. A racing/whichever-finishes-first variant is a future enhancement if a real use case emerges.
- **Cross-task blocking.** A task can only block on jobs it submitted (i.e., jobs whose `task_id` matches the blocking task). Blocking on another task's jobs is rejected with `400 job_not_owned_by_task`.
- **Block-on-job at child-task granularity for `block_kind=delegated`.** The existing `delegated` block_kind for parent→child relationships is unchanged. The new `blocked_on_job` block_kind is an independent state.
- **Daemon-side modification of the blocking-job list.** Once submitted, `blocked_on_job_ids` is immutable. To change the wait set, the agent must be resumed (via founder revisit) and re-submit a new block.
- **Notification on resume.** The agent finds out it resumed by being re-invoked with a `BLOCKED-JOBS-RESULTS` header in its bootstrap doc; no Feishu ping fires. (The founder can observe resumes via `grassland audit`.)
- **Resume cap / loop detection.** A pathological agent could block-resume-block-resume forever. The existing `GRASSLAND_MAX_ORCHESTRATION_STEPS` ceiling (default 50) bounds the lineage in practice; tighter detection is deferred.

## 3. Data model

### 3.1 New column on `tasks`

```sql
ALTER TABLE tasks ADD COLUMN blocked_on_job_ids TEXT;  -- JSON array of "JOB-NNN"; NULL when not blocked-on-jobs
```

No new index. The `tasks` table has no `status` or `block_kind` index today (existing indexes: `idx_tasks_parent`, `idx_tasks_revisit_of`, `idx_tasks_dispatched_from_talk_id`, `idx_tasks_dispatched_from_thread_id`); the lookup queries in §5 do a full-table scan filtered by `status='blocked' AND block_kind='blocked_on_job'`. At expected scale (low hundreds of active blocked tasks per org) this is acceptable. If profiling shows it, add a partial index `CREATE INDEX idx_tasks_blocked_on_jobs ON tasks(id) WHERE status='blocked' AND block_kind='blocked_on_job'` in a follow-up.

### 3.2 Extended `block_kind` enum

The existing `BlockKind` (Python enum) and the corresponding `tasks.block_kind` TEXT column gain a third value `blocked_on_job`:

| `block_kind` | Meaning | Resume path |
|---|---|---|
| `delegated` | Waiting on a child task | Child terminal → `_enqueue_parent_if_waiting` (existing) |
| `escalated` | Waiting on founder | Founder runs `grassland resolve-escalation` or `grassland revisit` (existing) |
| `blocked_on_job` | Waiting on N jobs (new) | All jobs terminal → `_maybe_resume_blocked_task` CAS-flips row to `in_progress` and re-enqueues |

Migration: idempotent `ALTER TABLE` added to the existing `Database._create_tables` block at `src/infrastructure/database.py:462` (where the existing `block_kind` and `dispatched_from_*` `ALTER TABLE` calls live, each wrapped in a `try/except sqlite3.OperationalError: pass` for "duplicate column" idempotency). No schema-version bump — the project has no formal schema-version tracking; the column is additive and nullable so old daemons reading new DBs and vice versa coexist cleanly.

### 3.3 Invariants

- `blocked_on_job_ids IS NOT NULL` **iff** `status='blocked' AND block_kind='blocked_on_job'`. Enforced by `run_step_impl`'s block-on-jobs branch (the sole writer of this column, §5.3) and by the entry-state branch's read-side validation (§5.1). Not a CHECK constraint (SQLite limitations).
- Empty array `[]` rejected at write time with `400 empty_waiting_on_job_ids`. Would resume immediately and is almost certainly a bug.
- Duplicates in the array deduped server-side; order is not significant.
- `blocked_on_job_ids` is not copied onto revisit roots (consistent with how `dispatched_from_thread_id` is read backward via `walk_revisit_chain` rather than propagated forward — see §6.4).

## 4. State machine

```
   in_progress ──┐
                 │  worker observes report.status=="blocked" AND
                 │  report.waiting_on_job_ids non-empty in run_step_impl's
                 │  existing "if report.status == 'blocked':" branch.
                 │  The branch transitions the row IN-PLACE (not via _fail):
                 │     status         = blocked
                 │     block_kind     = blocked_on_job
                 │     blocked_on_job_ids = '["JOB-12","JOB-13"]'
                 │  Then it calls the resume helper for the immediate
                 │  predicate re-check (caller B in §5).
                 ▼
   blocked(blocked_on_job, blocked_on_job_ids=["JOB-12","JOB-13"])
                 │
                 ├─ every listed job in {completed, failed, rejected}
                 │     │  resume helper enqueues task on TaskQueue (read-only,
                 │     │  no state mutation in the helper).
                 │     ▼
                 │  worker picks up task in run_step_impl:
                 │    step 1: NEW entry-state branch accepts
                 │            blocked+blocked_on_job iff predicate satisfied
                 │            (mirrors the existing blocked+delegated branch
                 │            that walks children).
                 │    step 3: existing CAS try_claim_for_step flips
                 │            blocked → in_progress atomically and writes
                 │            audit `task_resumed_from_jobs`.
                 │    step 4: prompt build injects BLOCKED-JOBS-RESULTS header
                 │            (reads the audit row written by step 3).
                 │
                 ├─ founder cancels task → existing cascade-kill of running jobs
                 │   via `terminate_jobs_for_task` (no new hook needed; the
                 │   existing kill cascade keys on task_id and is block_kind-blind)
                 │
                 └─ founder `grassland revisit` (manual override) → existing path;
                   the new root inherits nothing from blocked_on_job_ids
                   (revisit_of_task_id is sideways; see §6.5)
```

Terminal job statuses are `{completed, failed, rejected}`. `pending` and `running` are non-terminal. (Note: the `jobs` table's `status` column does not include a separate `killed` state — kills land in `failed` with a populated `reason` column.)

**Critical: the in_progress → blocked transition is owned by `run_step_impl`, not by the completion route.** The route just persists the `task_result` row and clears the session, as today. State transitions remain inside the orchestrator's worker loop, preserving the existing architecture and avoiding the session-tracker race described in §5.6 (caller B placement).

## 5. The three resume sites + the two run_step changes

The system has **two distinct state-transitioning sites** (both inside `run_step_impl`) and **three resume triggers** (read-only predicate-check + enqueue).

### 5.0 The two state transitions

Both live in `run_step_impl` (`src/orchestrator/run_step.py`):

| Transition | Where | When |
|---|---|---|
| `in_progress → blocked(blocked_on_job)` | New branch added to the existing `if report.status == "blocked":` block at `run_step.py:191` | Worker observes `report.waiting_on_job_ids` non-empty after the agent self-blocks |
| `blocked(blocked_on_job) → in_progress` | Existing CAS `try_claim_for_step` at `run_step.py:82` | Worker enters `run_step_impl` for a re-enqueued blocked-on-job task; new entry-state branch in step 1 admits it |

State transitions are owned by `run_step_impl`. The completion route, the resume helper, and the jobs runner **never** mutate `tasks.status` for the blocked-on-job lifecycle. This preserves the existing architecture where the orchestrator is the sole writer of task state.

### 5.1 New `run_step_impl` entry-state branch (step 1)

The existing step-1 check at `run_step.py:46-58` admits only `PENDING` and `BLOCKED+DELEGATED`. Add a third branch that mirrors the DELEGATED predicate-check shape:

```python
elif task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.BLOCKED_ON_JOB:
    job_ids = json.loads(task.blocked_on_job_ids or "[]")
    if not job_ids:
        logger.debug("run_step %s: blocked_on_job with empty job list (corrupted)", task_id)
        return
    statuses = {jid: orch._db.get_job_status(jid) for jid in job_ids}
    if any(s in {"pending", "running"} or s is None for s in statuses.values()):
        logger.debug("run_step %s: blocking job still in-flight, skipping", task_id)
        return
    # All terminal → fall through to step 2 (budget guard) and step 3 (CAS claim).
    # The CAS will atomically flip blocked → in_progress with expected_block_kind=BLOCKED_ON_JOB.
    # Audit task_resumed_from_jobs is written on CAS-win (see §5.2).
```

The CAS at step 3 (`try_claim_for_step`) already takes `expected_status` and `expected_block_kind` (lines 82-87) and naturally handles the `BLOCKED+BLOCKED_ON_JOB → IN_PROGRESS` transition. No CAS changes required — the existing primitive already covers this shape.

`Database.get_job_status(job_id) -> str | None` is a new lightweight method (returns the status string or None if the job doesn't exist).

### 5.2 CAS-win audit hook

Immediately after the successful `try_claim_for_step` (line 88) — i.e., on the BLOCKED→IN_PROGRESS transition for this task — if the prior status was `BLOCKED+BLOCKED_ON_JOB`, write the `task_resumed_from_jobs` audit row:

```python
if task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.BLOCKED_ON_JOB:
    orch._audit.log_task_resumed_from_jobs(
        task_id=task_id,
        blocking_job_ids=job_ids,                       # from step 1
        triggering_job_id=None,                         # filled in by the helper that enqueued us
        trigger="job_terminal" | "block_submit" | "startup_recovery",  # passed via enqueue metadata; see §5.4
        job_outcomes={jid: statuses[jid] for jid in job_ids},
    )
```

Because the audit row is written between CAS-win and the prompt-build at `run_step.py:100`, the resume header injection (§6.4) can read it from the audit log when `_build_agent_prompt` runs in the same step.

The `trigger` and `triggering_job_id` metadata come from the enqueue site — the `TaskQueue.put` API gets a small extension to carry `dict | None` metadata that `run_step_impl` can read for this single audit-row purpose. If metadata is absent (e.g., manual founder revisit re-entry, which shouldn't happen for this state but is defensively handled), log `trigger="unknown"`.

### 5.3 New block-on-jobs branch in the `report.status == "blocked"` handler

The existing handler at `run_step.py:191-204` calls `_fail(...)` for every blocked completion. Replace with:

```python
if report.status == "blocked":
    if report.waiting_on_job_ids:
        # Block on jobs - in-place transition, NOT _fail
        deduped = sorted(set(report.waiting_on_job_ids))
        # Route-side validation already ran (existence, ownership); defensive
        # re-validation here protects against a job being deleted/rejected
        # between the route POST and run_step_impl consuming the report.
        for jid in deduped:
            jstatus = orch._db.get_job_status(jid)
            if jstatus is None:
                # Job vanished — degrade to escalated rather than block forever
                _fail(orch, task_id, note=f"self-blocked but job {jid} not found")
                # ... (rest of existing _fail tail: enqueue_parent, notify, followup)
                return
        orch._db.update_task(
            task_id,
            status=TaskStatus.BLOCKED,
            block_kind=BlockKind.BLOCKED_ON_JOB,
            blocked_on_job_ids=_json.dumps(deduped),
            note=report.output_summary,
        )
        orch._audit.log_task_blocked_on_jobs(
            task_id=task_id, agent=agent,
            blocking_job_ids=deduped,
            output_summary_excerpt=(report.output_summary or "")[:200],
        )
        # Immediate predicate check (caller B). Runs HERE, in run_step_impl,
        # AFTER the agent session has completed and the SessionTracker has
        # already been cleared by submit_completion. No session race.
        _maybe_resume_blocked_task(
            orch, task_id, trigger="block_submit", triggering_job_id=None,
        )
        return
    # Existing escalated path unchanged below.
    note = f"self-blocked: {report.output_summary}"
    _fail(orch, task_id, note=note)
    _enqueue_parent_if_waiting(orch, task_id)
    _notify_failure_if_eligible(...)
    _maybe_post_thread_followup(...)
    return
```

Two important properties of this placement:

1. **The block_on_jobs transition uses `update_task`, not `_fail`.** The task does NOT pass through FAILED, so `_maybe_post_thread_followup` is not called here (the task is still alive). When the resumed agent eventually completes or fails for real, that terminal site fires the followup as normal.
2. **The session race in Codex P2 cannot occur.** `submit_completion` (`src/daemon/routes/tasks.py:289`) clears the SessionTracker before `_run_agent` returns control to `run_step_impl`. By the time we reach this branch, the session entry is already cleared. The immediate predicate check + enqueue happens against a fresh, sessionless task; the resumed worker can create a new session safely.

### 5.4 The helper — `_maybe_resume_blocked_task` (read-only)

Lives in `src/orchestrator/run_step.py` next to `_maybe_post_thread_followup`. Now **read-only** — it does not mutate task state:

```python
def _maybe_resume_blocked_task(
    orch: "Orchestrator",
    task_id: str,
    *,
    trigger: str,                       # "job_terminal" | "block_submit" | "startup_recovery"
    triggering_job_id: str | None,      # the JOB-NNN whose terminal fired us (None for submit/recovery)
) -> bool:
    """Check predicate and enqueue if satisfied. Does NOT flip task state.

    Returns True if it enqueued; False otherwise. Idempotent: extra enqueues
    are harmless — the CAS at run_step_impl step 3 admits exactly one.
    """
```

Algorithm:

1. Load the `tasks` row. If `status != BLOCKED` or `block_kind != BLOCKED_ON_JOB`, return False (no audit).
2. Parse `blocked_on_job_ids`. If empty (corrupted), audit `task_resume_skipped(reason=empty_job_list)` and return False.
3. For each JOB-NNN, query `jobs.status`. If any are in `{pending, running}`, return False (no audit — common steady state).
4. Enqueue the task on the `TaskQueue` with metadata `{trigger, triggering_job_id}` so `run_step_impl` can write the audit row on CAS-win (§5.2). Return True.

That's it. No state mutation. No audit on the happy path (run_step writes it after CAS-win). Extra concurrent enqueues all converge on the single CAS-winner in `run_step_impl`.

### 5.5 Caller A — job terminal site (jobs_runner)

In `src/daemon/jobs_runner.py`, after `run_job` produces its `JobRunResult` and the route layer persists the terminal status, fire-and-forget bridge to the main loop:

```python
asyncio.run_coroutine_threadsafe(
    _resume_blocked_tasks_for_job(orch, task_id=task.task_id, job_id=job_id),
    _main_loop,
)
```

`_resume_blocked_tasks_for_job` issues one scan query (unindexed today; see §3.1):

```sql
SELECT id FROM tasks
 WHERE status='blocked'
   AND block_kind='blocked_on_job'
   AND blocked_on_job_ids LIKE ?  -- '%"JOB-12"%'
```

For each match (at most 1 in practice, since blocking is task-local), calls `_maybe_resume_blocked_task(trigger="job_terminal", triggering_job_id=<job_id>)`.

The `LIKE '%"JOB-NNN"%'` pattern is acceptable because (a) the surrounding quotes anchor the suffix so `JOB-1` does not match `JOB-12`, and (b) the helper re-validates against the parsed JSON, so a false positive is at worst one extra helper call that no-ops at step 1.

### 5.6 Caller B — block-submission immediate check (in run_step)

Caller B lives in `run_step_impl`'s new block-on-jobs branch (§5.3), **not** in the route. This is a deliberate architectural choice that resolves Codex P2:

- The route handler `submit_completion` (`routes/tasks.py:254`) persists the report and clears the SessionTracker on line 289.
- `_run_agent` returns control to `run_step_impl` only after the agent subprocess exits.
- The new block-on-jobs branch runs AFTER both of the above — the session is already cleared by the time we call `_maybe_resume_blocked_task`.
- Therefore if the predicate is already satisfied (a `review_required=false` job finished between submission and block), the helper enqueues immediately, the worker picks up the resumed task with a clean session slate, and `_run_agent` mints a fresh `set_active` entry safely. No `clear` ever races against the new session.

The route's role is unchanged from today: validate the request, persist the task_result row, clear the session, return 200. The route does NOT call the resume helper.

### 5.7 Caller C — startup recovery

In `src/daemon/app.py` lifespan, after `recover_orphaned_running_jobs` runs per-org:

```python
for task_id in db.list_tasks_blocked_on_jobs():
    _maybe_resume_blocked_task(orch, task_id, trigger="startup_recovery", triggering_job_id=None)
```

`list_tasks_blocked_on_jobs` is a new `Database` method:

```sql
SELECT id FROM tasks WHERE status='blocked' AND block_kind='blocked_on_job'
```

This handles the crash-mid-block case: jobs are recovered (force-failed with `kill_reason=daemon_crash`) but never go through `run_job` again, so caller A never fires for them. Without this scan, the task would stay blocked forever.

### 5.8 Concurrency

Three callers (A, B, C) can fire simultaneously for the same task. Each independently enqueues; the queue may carry duplicates briefly. The CAS at `run_step.py:82` (`try_claim_for_step` with `expected_status=BLOCKED, expected_block_kind=BLOCKED_ON_JOB`) admits exactly one worker; losers return silently at line 88-93 (existing "lost claim race" log). No additional locking primitive is needed.

`task_resume_skipped(reason=cas_lost)` is **not** written for queue duplicates — the existing `try_claim_for_step` loser path already logs at debug level and is the dominant case for this design. The audit-skip codes (`empty_job_list` only) stay narrow to avoid log noise.

### 5.9 Call-order invariant with thread task-followup

In `run_step.py`'s opaque-failure branches, the existing call order is `_maybe_spawn_auto_revisit` → `_enqueue_parent_if_waiting` → `_maybe_post_thread_followup`. None of this code is touched by this design — the new block-on-jobs branch in §5.3 is a **separate** branch of the `if report.status == "blocked":` handler, sitting beside the existing `_fail`-driven escalated path. The opaque-failure machinery only fires on agent exceptions / opaque session failures, never on a clean `report.status=="blocked"` callback.

When a resumed task later reaches a true terminal (the agent eventually completes or fails), the existing thread task-followup helper fires as normal at that terminal site. The block-on-jobs in-place transition is not a terminal transition; the thread-followup pipeline ignores it.

## 6. Agent-facing API

### 6.1 `CompletionReport` payload extension

Add one field to `src/models.py:70`:

```python
class CompletionReport(BaseModel):
    task_id: str
    agent: str
    status: str
    confidence: int = Field(ge=0, le=100)
    output_summary: str
    decision: NextStep | None = None
    risks_flagged: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    suggested_reviewer_focus: list[str] = Field(default_factory=list)
    artifact_dir: str | None = None
    waiting_on_job_ids: list[str] = Field(default_factory=list)  # NEW
```

The non-empty `waiting_on_job_ids` list implies `block_kind=blocked_on_job`; the agent does not pass `block_kind` directly. This mirrors today's pattern where `status=blocked` with no further hint implies `block_kind=escalated` (founder unblock).

### 6.2 Route validation matrix

The route only **validates and persists** the report — it never mutates `tasks.status`. State transitions stay inside `run_step_impl` (§5.0). On validation success the route inserts the `task_result` row, clears the session, returns 200, and lets `run_step_impl` observe `report.waiting_on_job_ids` non-empty to drive the state transition (§5.3).

| Condition | HTTP | Reason code |
|---|---|---|
| `status != "blocked"` and `waiting_on_job_ids` non-empty | 400 | `waiting_on_job_ids_requires_blocked` |
| `status == "blocked"` and `waiting_on_job_ids` empty | (unchanged behaviour — falls through to existing `block_kind=escalated` once `run_step_impl` consumes the report) | — |
| `status == "blocked"` and `waiting_on_job_ids` non-empty | route validates JOB-NNNs and returns 200; `run_step_impl` later runs the new branch (§5.3) | — |
| Any JOB-NNN doesn't exist | 404 | `job_not_found` |
| Any JOB-NNN's `task_id` mismatch | 400 | `job_not_owned_by_task` |
| `waiting_on_job_ids` becomes empty after dedup | 400 | `empty_waiting_on_job_ids` |

Defensive re-validation runs again in `run_step_impl` (§5.3): if a job was deleted between the route POST and `run_step_impl` consuming the report (extremely unlikely; jobs are write-once + terminal-frozen), the worker degrades to the existing `_fail`-driven escalated path with `note="self-blocked but job <JID> not found"`.

### 6.3 `protocol/skills/jobs/SKILL.md` "After submitting" rewrite

The existing skill section (jobs-design.md §9.1, the paragraph beginning "If `review_required=true`...") is replaced with:

> **When you need to wait for jobs to finish before proceeding** (either `review_required=true` waiting for founder approval, or `review_required=false` jobs you can't move forward without):
>
> Submit your block via `report-completion` with `status=blocked` and `waiting_on_job_ids` populated:
>
> ```json
> {
>   "status": "blocked",
>   "confidence": 0,
>   "output_summary": "Waiting for JOB-12 and JOB-13 before I can verify the migration ran cleanly.",
>   "waiting_on_job_ids": ["JOB-12", "JOB-13"]
> }
> ```
>
> The system will resume your task automatically once **every** listed job reaches a terminal state (`completed`, `failed`, or `rejected`). When you resume, your bootstrap doc will include a `BLOCKED-JOBS-RESULTS` section listing each job's status, exit code, and `grassland jobs show JOB-NNN` / `grassland jobs output JOB-NNN` commands to fetch full output. **You don't poll.**

The skill's existing "If `review_required=false`" branch (loop on `grassland jobs wait`) remains as a valid pattern for short-lived auto-running jobs the agent can stay in-session for. The block-and-resume path is the recommended pattern for any wait long enough to risk session timeout.

### 6.4 Resume header — new `_blocked_jobs_resume_header_if_applicable`

Resume is not revisit (no new task row spawned), so `_revisit_header_if_applicable` does not fire. A parallel helper, `_blocked_jobs_resume_header_if_applicable(orch, task_id)`, is added next to it.

Predicate: search the audit log for the most recent `task_resumed_from_jobs` row where `task_id` matches; render the header iff its timestamp is newer than the most recent `orchestration_step` audit row for the same task (`audit_logger.py:314` writes one `orchestration_step` row per step run, so this is the natural "have we already consumed this resume?" boundary). The first step run after a resume will see the row; the step writes its own `orchestration_step` row on completion, which causes the next step's prompt-build to skip the header. No `resume_header_consumed_at` column needed.

Render shape:

```
=== BLOCKED-JOBS-RESULTS (system) ===
You self-blocked on JOB-12, JOB-13. They are now terminal:

  JOB-12  completed  exit=0   12.3s   "Run migration on staging"
          → grassland jobs show JOB-12
          → grassland jobs output JOB-12 --stream stdout
  JOB-13  failed     exit=2   4.1s    "Verify schema"
          reason: non-zero exit
          → grassland jobs show JOB-13
          → grassland jobs output JOB-13 --stream stderr

Re-read your task brief; decide whether to proceed, retry, or escalate.
======================================
```

The header is injected at the same call site as `_revisit_header_if_applicable` in the orchestrator's prompt-build path. Both helpers can fire on the same step (a revisit root whose first step resumed from blocked-on-jobs is unusual but possible if a manual revisit was then auto-blocked — both headers stack).

### 6.5 Backward read of `blocked_on_job_ids` across revisit chains

Consistent with how `dispatched_from_thread_id` is treated (CLAUDE.md "Thread task-followup invariants"): `blocked_on_job_ids` is **not** copied onto revisit roots by `/revisit` or by auto-revisit. The column lives on the original blocked row; once a manual revisit spawns a fresh root, the new root is unblocked from scratch.

This is intentional. A founder using `grassland revisit` on a blocked-on-jobs task is explicitly overriding the wait — propagating the wait would defeat the purpose. The original row's `blocked_on_job_ids` remains in the DB for audit purposes; the new row starts at `status=pending` with `blocked_on_job_ids=NULL`.

## 7. Audit events

| Kind | Trigger site | Payload |
|---|---|---|
| `task_blocked_on_jobs` | `run_step_impl` block-on-jobs branch (§5.3), written after `update_task` to BLOCKED+BLOCKED_ON_JOB succeeds, before the immediate predicate check | `{task_id, agent, blocking_job_ids, output_summary_excerpt}` |
| `task_resumed_from_jobs` | `run_step_impl` step 3, written immediately after `try_claim_for_step` wins on a BLOCKED+BLOCKED_ON_JOB row (§5.2) | `{task_id, blocking_job_ids, trigger, triggering_job_id, job_outcomes: {JOB-X: status}}` |
| `task_resume_skipped` | `_maybe_resume_blocked_task` returns False with `reason="empty_job_list"` only | `{task_id, reason: "empty_job_list", blocked_on_job_ids_raw}` |

`task_resume_skipped` is **deliberately narrow** — only the `empty_job_list` corrupted-row case writes an audit row. The other no-op cases are silent for log-volume reasons:

- `not_blocked` (steady state — every job terminal pings every matching task; most aren't blocked) → no audit.
- `jobs_still_running` (steady state — partial-terminal is the dominant case during a multi-job block) → no audit.
- `cas_lost` (queue-duplicate convergence) → already covered by the existing "lost claim race" debug log in `try_claim_for_step` at `run_step.py:88-93`.

The `triggering_job_id` field on `task_resumed_from_jobs` is the founder's debugging breadcrumb: "why did this task resume?" → look at the JOB-NNN that closed the predicate. Threaded from the enqueue site via task-queue metadata (§5.2). For `trigger="block_submit"` and `trigger="startup_recovery"`, `triggering_job_id` is `null`.

## 8. Web UI and CLI surface changes

- **`grassland details <task-id>`**: gains a "Blocked on jobs:" subsection when `block_kind=blocked_on_job`, listing each JOB-NNN with its current status (so the founder can see what the agent is waiting on).
- **Web UI task detail panel**: same — render the JOB-NNN list with links to `/jobs/:id` when block_kind is `blocked_on_job`. Lives in `web/src/features/tasks/` (the existing task-detail panel; no new feature folder needed).
- **No new top-level route** — block submission is an extension of the existing `POST /tasks/{id}/completion` route, not a new endpoint. OpenAPI snapshot regenerates because the request body model changes, but no INCLUDED/EXCLUDED set changes.

## 9. Test coverage

### 9.1 Unit tests (default `pytest` run)

**`tests/orchestrator/test_resume_helper.py` (new)** — covers `_maybe_resume_blocked_task` (read-only predicate-check + enqueue, no state mutation):

- Single job, completed → enqueues
- Single job, failed → enqueues
- Single job, rejected → enqueues
- Multi-job, all terminal in mixed states (completed + failed + rejected) → enqueues
- Multi-job, one still running → returns False, no audit, no enqueue (steady state)
- Task not in `blocked` status → returns False, no audit
- Task in `blocked` but `block_kind=escalated` → returns False, no audit
- Empty `blocked_on_job_ids` array (corrupted state) → returns False + writes `task_resume_skipped(empty_job_list)`
- Helper does NOT mutate `tasks.status` — verify row unchanged after every call
- Multiple concurrent calls → multiple enqueues land; the CAS in `run_step_impl` (covered by the next test file) is what enforces single-fire

**`tests/orchestrator/test_run_step_blocked_on_job.py` (new)** — covers the new branches inside `run_step_impl`:

- Entry-state branch: row at BLOCKED+BLOCKED_ON_JOB with all jobs terminal → step 1 admits, step 3 CAS wins, audit `task_resumed_from_jobs` written with correct `trigger`/`triggering_job_id` from queue metadata, prompt-build sees the audit row
- Entry-state branch: row at BLOCKED+BLOCKED_ON_JOB with at least one job still `running` → step 1 returns silently, no CAS, no audit
- Entry-state branch: row at BLOCKED+BLOCKED_ON_JOB with empty `blocked_on_job_ids` (corrupted) → step 1 returns silently
- Block-on-jobs completion branch: `report.status="blocked"` + `report.waiting_on_job_ids=["JOB-X"]` with JOB-X still running → row transitions to BLOCKED+BLOCKED_ON_JOB, audit `task_blocked_on_jobs` written, no enqueue (helper finds predicate unsatisfied)
- Block-on-jobs completion branch: same payload but JOB-X already terminal (submit-time race) → row transitions, helper enqueues immediately, the resumed step runs in the same worker pickup
- Block-on-jobs completion branch with stale JOB-X (deleted between route POST and worker pickup) → degrades to `_fail` path with diagnostic note (existing tail behaviour unchanged)
- Concurrent CAS contention: two queue duplicates for the same task → exactly one `try_claim_for_step` wins, exactly one `task_resumed_from_jobs` audit row; loser triggers the existing "lost claim race" debug log only (no `task_resume_skipped` row)
- Thread-followup invariant: a thread-dispatched task that passes through BLOCKED+BLOCKED_ON_JOB and later completes — `_maybe_post_thread_followup` still fires correctly at the true terminal (not at the block transition)

**`tests/daemon/test_completion_route_blocked_on_jobs.py` (new)** — covers the route-side validation matrix only. The route does NOT mutate task state, so the assertions are about HTTP response shape + the `task_result` row + session clear, not about `tasks.status`:

- Happy path: `status=blocked` + 2 valid `waiting_on_job_ids` → 200, `task_result` row inserted carrying the waiting list, session cleared, NO `tasks.status` mutation by the route (the orchestrator branch later flips it; verified by inspecting `tasks.status` immediately after the route returns)
- Job not owned by task → 400 `job_not_owned_by_task`, no `task_result` row, no session clear
- Job doesn't exist → 404 `job_not_found`
- `waiting_on_job_ids` with `status=completed` → 400 `waiting_on_job_ids_requires_blocked`
- Empty list after dedup → 400 `empty_waiting_on_job_ids`
- Duplicate IDs server-side → deduped on persist (verify the persisted `task_result` carries the deduped list)

**`tests/infrastructure/test_database_blocked_on_jobs.py` (new)** — covers the DB layer:

- Schema migration adds `blocked_on_job_ids` column idempotently (running twice doesn't error)
- `list_tasks_blocked_on_jobs` returns only matching rows (excludes other blocked types)
- `get_job_status(job_id)` returns terminal/non-terminal status correctly, `None` for unknown ids
- `try_claim_for_step` with `expected_status=BLOCKED, expected_block_kind=BLOCKED_ON_JOB` flips the row atomically (existing CAS already covers this shape; one new fixture row + assertion)
- TaskQueue extension carries `dict | None` metadata through to the consumer (small targeted test if the queue gains a new param)

### 9.2 Integration tests (`-m integration`)

**`tests/integration/test_task_blocked_by_job_autonomous.py` (new)** — agent submits `review_required=false` job, blocks, job completes, task auto-resumes, agent's next session sees the `BLOCKED-JOBS-RESULTS` header. Uses the existing `fake_claude_plan_env` two-stage plan pattern (stage 1 submits + blocks; stage 2 reads header + completes).

**`tests/integration/test_task_blocked_by_job_review_required.py` (new)** — covers the founder-in-loop path:

- Agent submits `review_required=true` job, blocks
- Founder approves via route → job runs → task auto-resumes (covers the `completed` outcome)
- Separate test: founder rejects → task auto-resumes with `rejected` in the header

**`tests/integration/test_task_blocked_by_job_multi.py` (new)** — agent blocks on JOB-A + JOB-B, one finishes fast, one slow. Verifies:

- Task stays blocked while JOB-B is still running (caller A fired by JOB-A's terminal, found JOB-B not terminal, no-op)
- Task resumes only after JOB-B terminates
- Audit row's `triggering_job_id` is JOB-B (the one that closed the predicate)

**`tests/integration/test_task_blocked_by_job_startup_recovery.py` (new)** — covers caller C:

- Agent submits + blocks, daemon killed mid-block (jobs left in `running` state on disk)
- Daemon restarted → `recover_orphaned_running_jobs` force-fails the jobs with `daemon_crash` → caller C's recovery scan re-evaluates the predicate → task resumes
- Resumed agent session sees the `BLOCKED-JOBS-RESULTS` header listing `failed (reason=daemon_crash)` for each job

**Existing `tests/integration/test_threads_e2e.py`** — extend to verify thread task-followup still fires correctly for tasks that pass through `blocked_on_job` state. A thread-dispatched task that self-blocks, resumes, and reaches a true terminal must trigger the existing followup helper as before. This guards against accidental call-order regressions between the new resume helper and the existing thread followup helper.

## 10. CLAUDE.md updates

The following CLAUDE.md sections gain content; the spec author should make these edits as part of the implementation, not as a separate documentation pass:

**"Implementation Order — Done" list** — add a new item between #16 (Shared Assets) and the formerly-#17 (Founder dashboard, currently the first "Open" item):

> 17. **Task blocked-by-job** — agent self-blocks with `waiting_on_job_ids: ["JOB-NNN", ...]` in the `report-completion` payload; system auto-resumes the task when every listed job is terminal. Per-org `tasks.blocked_on_job_ids` JSON column + new `block_kind=blocked_on_job` value. Spec: `docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md`.

**"Task status vocabularies" section** — extend the `block_kind` enumeration to include `blocked_on_job`:

> The orchestrator-owned `TaskStatus` is `{pending, in_progress, blocked, completed, failed}` with `block_kind` (`delegated` | `escalated` | `blocked_on_job`).

**"Jobs (founder-approved + agent-autonomous)" section — Non-obvious invariants** — append:

> - **Auto-resume on terminal supersedes founder revisit for blocked-on-job tasks.** The original spec (§2) listed "no task wakes itself" as a non-goal; the 2026-05-28 task-blocked-by-job design reverses that. Agents now self-block with `waiting_on_job_ids` and resume automatically. The `grassland revisit` path remains valid as a founder-driven override (e.g., "give up on JOB-X, start over").

A new top-level section **"Task blocked-by-job (system auto-resumes from job terminals)"** is added between the existing "Jobs" and "Feishu notifications" sections, documenting the three resume sites, the call-order invariant with thread followup, and the backward-read invariant on revisit chains. The content mirrors §5 and §6.4–6.5 of this spec, with the same "Non-obvious invariants" style as adjacent sections.

## 11. Open questions / known limitations

- **No loop detection.** A pathological agent could block-resume-block-resume forever. The existing `GRASSLAND_MAX_ORCHESTRATION_STEPS` ceiling (default 50) bounds the lineage in practice; tighter detection (e.g., "this task has blocked-on-jobs N times in M minutes") is deferred until a real instance occurs.
- **No partial-result resume.** With ALL-terminal policy, a long-running JOB-B holds up the task even if JOB-A finished hours ago. The agent has no way to say "show me JOB-A's results now and JOB-B's results whenever, just unblock me on JOB-A." If this becomes a real pattern, add an ANY-terminal mode in a follow-up spec.
- **No cross-task blocking.** An agent who needs another agent's job result can't block on it directly — they have to coordinate via threads or have the founder bridge it. Adequate for v1; coordination across agents is the job of threads.
- **Unindexed scan for caller A's lookup.** Caller A's lookup uses `WHERE status='blocked' AND block_kind='blocked_on_job' AND blocked_on_job_ids LIKE ?`; the `tasks` table has no `status` or `block_kind` index today (§3.1) and the LIKE pattern is opaque to any index. Acceptable at expected scale (low hundreds of active blocked tasks per org). Remedy ladder if profiling shows it: (1) add the partial index in §3.1, (2) replace the LIKE with a `json_each(blocked_on_job_ids)` subquery (requires SQLite JSON1, available in the project's Python builds), (3) denormalize into a `task_blocking_jobs` join table.
- **Resume header injection point is shared with revisit header.** Both helpers fire at the orchestrator's prompt-build call site. Their content can stack on the same step (manual revisit + auto-resume in the same prep). The render order is "revisit header first, then resume header" — tested but not currently a documented invariant; lock it down in the implementation plan.
