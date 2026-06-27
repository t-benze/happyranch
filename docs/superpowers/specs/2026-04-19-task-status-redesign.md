# Task Status Redesign: Unified Async Execution + Structured Blocking

**Status:** Design approved, ready for implementation plan
**Date:** 2026-04-19
**Branch:** `feature/task-status-redesign`

> **Superseded (vocabulary) by THR-037 Change B (Path B, stored source-of-truth):** the `blocked`-discriminated model below was collapsed — a parent waiting on its children/jobs is now `in_progress` (reason kept in `block_kind`), the await-founder state is the top-level `escalated`, and founder cancels write `cancelled`. See `docs/superpowers/specs/2026-06-27-task-status-pathB-stored-design.md`.

## Problem

The current task lifecycle has three shortcomings that have shown up in practice:

1. **Inconsistent child behavior.** Root tasks transition `pending → in_progress → approved|rejected|escalated`, but delegated child tasks skip `in_progress` entirely — they jump `pending → approved|rejected`. A worker can be running for 20 minutes while `opc tasks` shows its task as `pending`.
2. **Synchronous orchestration loop.** `Orchestrator.run_task` is a synchronous for-loop. The Engineering Head (EH) agent runs, decides, the orchestrator inline-invokes the worker subprocess, waits, loops. There is no concept of "the EH task is blocked waiting on a child" — the EH session is alive in a Python thread the entire time. This forecloses concurrency, natural crash recovery, and agent-aware progress reporting.
3. **Overloaded terminal states.** `approved` conflates "EH returned done" with "founder approved an escalation" with "delegate worker session succeeded." `rejected` conflates "session failed," "worker self-reported blocked," and "founder rejected." `escalated` is a special reversible terminal with a dedicated resolve command. No separation between **why** a task is in its current state and **what** the state is.

## Goals

1. Replace the 7-valued, inconsistently-terminal `TaskStatus` with a 5-valued vocabulary that cleanly separates "state" from "reason."
2. Unify root and child task execution so there is exactly one lifecycle, one set of transition rules, and no special-case code.
3. Convert the orchestration loop from a synchronous Python for-loop to an event-driven async queue model where blocked tasks resume on well-defined events.
4. Preserve all existing semantics: EH remains the default root-task agent; `max_orchestration_steps` still caps runaway tasks; founder escalation/resolution flow is unchanged from the user's perspective.

## Non-goals

1. **KB async event pipeline.** Agents dispatching KB updates with their own status lifecycle is a separate improvement — sibling event stream, independent queue, its own spec. Task status remains ⊥ KB state.
2. **Parallel delegation.** `run_step` spawns exactly one child per `delegate` decision. The design is future-compatible with fanout, but the agent-facing `NextStep` schema is not extended here.
3. **Founder auth hardening.** The `as_founder=True` bearer flag stays a placeholder pending Feishu integration (blueprint step 10).
4. **Mid-task progress streaming from agents.** Agents still report exactly once at the end of a session via `opc report-completion`.
5. **Automatic retry policies.** A failed `run_step` is a failed step. If the parent wants to retry, its next resumption issues a new `delegate`.
6. **`opc kb precedent` behavior.** Its gate is audit-row-based (`log_escalation`) and status-agnostic by design. Untouched.

## Design

### 1. Status vocabulary

Five values. Every task is in exactly one at any time.

| Status | Kind | Meaning |
|---|---|---|
| `pending` | non-terminal | Created, no agent subprocess started yet |
| `in_progress` | non-terminal | An agent subprocess is running *right now* for this task |
| `blocked` | non-terminal-but-absorbing | Suspended, waiting for an external event. Requires `block_kind` to be set. |
| `completed` | terminal | Success |
| `failed` | terminal | Unsuccessful (session failure, worker self-blocked, parse failure, founder rejection). Max-steps does **not** go here — it routes to `blocked(ESCALATED)` so the founder can resolve (see §5 step 2). |

Dropped: `approved`, `rejected`, `escalated`, `completed` (old enum member, dead), `in_review` (dead).

### 2. New columns on `tasks`

```sql
ALTER TABLE tasks ADD COLUMN block_kind TEXT;                      -- NULL unless status='blocked'
ALTER TABLE tasks ADD COLUMN note TEXT;                             -- replaces final_output_summary
ALTER TABLE tasks ADD COLUMN orchestration_step_count INTEGER DEFAULT 0;
```

- `block_kind` is NULL unless `status='blocked'`. Enforced at the Python write path, not as a DB `CHECK`.
- `note` replaces `final_output_summary` — generic human-readable context writeable at any status (completion summary, failure reason, block detail, founder rationale). Old column is dropped by the migration.
- `orchestration_step_count` replaces the in-memory `for step_num in range(...)` counter. Persisted so async resumption across daemon restarts doesn't lose the budget.

### 3. `BlockKind` enum + workflow contract

```python
class BlockKind(StrEnum):
    DELEGATED = "delegated"     # waiting on a child task to terminate
    ESCALATED = "escalated"     # waiting on founder via resolve-escalation
```

Every `BlockKind` variant must specify all five columns of the workflow contract. Adding a new variant requires filling this table and writing a test.

| block_kind | Entry trigger | Invariant while blocked | Unblock event | Unblock handler | Terminal range |
|---|---|---|---|---|---|
| `DELEGATED` | Agent decision is `delegate` | Exactly one child with `parent_task_id=self.id` and status ∈ {pending, in_progress, blocked} | Child reaches a terminal state | `_enqueue_parent_if_waiting` → `run_step` on the parent, which appends child outcome to history, increments `orchestration_step_count`, re-invokes `assigned_agent`. Over budget → `blocked(ESCALATED)` with `note="max steps exceeded"` | `in_progress` → eventually `completed` / `failed` / `blocked(any)` |
| `ESCALATED` | Agent decision is `escalate` (or a guardrail trips) | `note` contains the escalation reason; `log_escalation` audit row is written | `POST /tasks/{id}/resolve-escalation` with founder bearer | `resolve_founder_escalation(task_id, disposition, rationale)` — transitions to `completed` or `failed`, writes `log_escalation_resolved`, calls `_enqueue_parent_if_waiting` | `completed` / `failed` |

### 4. Async execution flow

The synchronous for-loop is replaced by an event-driven asyncio queue. The orchestrator exposes exactly one primitive:

**`Orchestrator.run_step(task_id)`**

Does exactly one thing: pick up a task that is `pending` or `blocked(DELEGATED) with all children terminal`, invoke its `assigned_agent` once, parse the result, transition the task, trigger follow-up enqueues. Returns. No loops, no recursion — recursion emerges from queue re-entry.

Outcome table:

| Agent returned | Transition applied | Follow-up enqueue |
|---|---|---|
| `{"action": "done", "summary": ...}` | `in_progress → completed`, `note = summary` | `_enqueue_parent_if_waiting(self)` |
| `{"action": "delegate", "agent": X, "prompt": ...}` | `in_progress → blocked(DELEGATED)`, `note = "Delegated to X (child=TASK-NNN)"`, spawn child (pending, `assigned_agent=X`, `parent_task_id=self`) | `queue.put(child_id)` |
| `{"action": "escalate", "reason": ...}` | `in_progress → blocked(ESCALATED)`, `note = reason`, write `log_escalation` | — (parent stays parked until founder resolves) |
| Non-JSON / parse failure / wrong schema | `in_progress → failed`, `note = preview` | `_enqueue_parent_if_waiting(self)` |
| Report `status = "blocked"` (worker self-blocked) | `in_progress → failed`, `note = "self-blocked: <summary>"` | `_enqueue_parent_if_waiting(self)` |
| Session failure / no report / workspace missing | `in_progress → failed`, `note = reason` | `_enqueue_parent_if_waiting(self)` |
| `orchestration_step_count ≥ max` | `in_progress → blocked(ESCALATED)`, `note = "max steps (N) exceeded"`, write `log_escalation` | — (parent stays parked; founder resolves like any other escalation) |

### 5. `run_step` algorithm

```python
def run_step(task_id: str) -> None:
    """Invoke the task's assigned_agent once, transition state, fire follow-up.

    Entry contract: task MUST be in {pending} OR {blocked(DELEGATED) with all
    children terminal}. Any other state = stale enqueue, silent no-op.
    Exit contract: task is in exactly one of {in_progress-then-crashed,
    completed, failed, blocked(DELEGATED), blocked(ESCALATED)}.
    """
    task = db.get_task(task_id)
    if task is None:
        return

    # ---- 1. Verify entry state ----
    if task.status == TaskStatus.PENDING:
        prior_steps = []
    elif task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.DELEGATED:
        if any(c.status not in TERMINAL_STATES for c in db.get_children(task_id)):
            return                                    # another child still live
        prior_steps = _build_prior_steps_from_db(task_id)
    else:
        return                                        # stale or invalid entry

    # ---- 2. Budget guard (persisted, survives restarts) ----
    #   Max-steps breach parks the task in blocked(ESCALATED) — founder visible,
    #   matches today's behavior where runaway tasks escalate rather than fail
    #   silently. The parent (if any) stays blocked(DELEGATED) until the founder
    #   resolves THIS task to a terminal state.
    next_count = task.orchestration_step_count + 1
    if next_count > settings.max_orchestration_steps:
        db.update_task(task_id,
                       status=TaskStatus.BLOCKED,
                       block_kind=BlockKind.ESCALATED,
                       note=f"max steps ({settings.max_orchestration_steps}) exceeded")
        audit.log_escalation(task_id, "orchestrator", "max steps exceeded")
        return

    # ---- 3. Atomic transition: unblock + increment ----
    db.update_task(task_id,
                   status=TaskStatus.IN_PROGRESS,
                   block_kind=None,
                   note=None,
                   orchestration_step_count=next_count)

    # ---- 4. Run agent subprocess ----
    agent = task.assigned_agent
    prompt = _build_agent_prompt(task, prior_steps)
    session_id = _build_session_id()

    audit.log_session_start(task_id, agent, workspace_path)
    try:
        result = executor.run(workspace, prompt, session_id, timeout)
    except WorkspaceNotInitialized as exc:
        _fail(task_id, note=str(exc))
        _enqueue_parent_if_waiting(task_id)
        return
    audit.log_session_end(task_id, agent, result.duration_seconds)

    report = _read_completion_from_db(task_id, agent, session_id)

    # ---- 5. Classify outcome ----
    if not result.success or report is None:
        _fail(task_id, note="agent session failed")
        _enqueue_parent_if_waiting(task_id)
        return

    audit.log_completion_report(report, session_id, result.duration_seconds)

    if report.status == "blocked":
        _fail(task_id, note=f"self-blocked: {report.output_summary}")
        _enqueue_parent_if_waiting(task_id)
        return

    # ---- 6. Parse NextStep (same parser as today) ----
    decision = _parse_next_step(report)

    # ---- 7. Dispatch on action ----
    if decision.action == "done":
        _complete(task_id, note=decision.summary or report.output_summary,
                  artifact_dir=report.artifact_dir)
        tracker.log_verdict(agent, approved=True)
        _enqueue_parent_if_waiting(task_id)
        return

    if decision.action == "escalate":
        db.update_task(task_id,
                       status=TaskStatus.BLOCKED,
                       block_kind=BlockKind.ESCALATED,
                       note=decision.reason or "Escalated")
        audit.log_escalation(task_id, agent, decision.reason or "Escalated")
        return  # parent stays blocked(DELEGATED) until this task reaches terminal

    if decision.action == "delegate":
        err = _validate_delegate(decision)
        if err is not None:
            _fail(task_id, note=f"invalid delegate: {err}")
            _enqueue_parent_if_waiting(task_id)
            return
        child_id = db.next_task_id()
        db.insert_task(TaskRecord(
            id=child_id, type=task.type, brief=decision.prompt,
            assigned_agent=decision.agent, parent_task_id=task_id,
            status=TaskStatus.PENDING,
        ))
        db.update_task(task_id,
                       status=TaskStatus.BLOCKED,
                       block_kind=BlockKind.DELEGATED,
                       note=f"Delegated to {decision.agent} (child={child_id})")
        queue.put_nowait(child_id)
        return

    _fail(task_id, note=f"unknown action: {decision.action}")
    _enqueue_parent_if_waiting(task_id)


def _complete(task_id, *, note, artifact_dir=None):
    db.update_task(task_id, status=TaskStatus.COMPLETED, block_kind=None,
                   note=note, final_artifact_dir=artifact_dir, completed_at=now())
    _update_task_history(task_id)

def _fail(task_id, *, note):
    db.update_task(task_id, status=TaskStatus.FAILED, block_kind=None,
                   note=note, completed_at=now())
    _update_task_history(task_id)

def _enqueue_parent_if_waiting(task_id):
    """Idempotent: parent is enqueued only if it's actually waiting on THIS
    lineage (blocked(DELEGATED)) AND all its children are now terminal."""
    task = db.get_task(task_id)
    if task.parent_task_id is None:
        return
    parent = db.get_task(task.parent_task_id)
    if parent is None or parent.status != TaskStatus.BLOCKED:
        return
    if parent.block_kind != BlockKind.DELEGATED:
        return
    if any(c.status not in TERMINAL_STATES for c in db.get_children(parent.id)):
        return
    queue.put_nowait(parent.id)
```

### 6. Queue + worker pool

- Single `asyncio.Queue[str]` of task IDs in `DaemonState`.
- `N=3` worker coroutines pull IDs and call `run_step` via `loop.run_in_executor` (because `claude -p` is a sync subprocess).
- Three entry points enqueue:
  1. **Task creation** (`POST /tasks`) — inserts `pending`, enqueues.
  2. **Child termination** (inside `run_step`) — `_enqueue_parent_if_waiting`.
  3. **Escalation resolved** (`POST /tasks/{id}/resolve-escalation`) — after flipping status to terminal, calls `_enqueue_parent_if_waiting`.
- Graceful shutdown: on SIGTERM, stop accepting new enqueues, drain the queue, let in-flight `run_step` calls finish up to a timeout, then cancel.
- Crash recovery on startup:
  - `in_progress` rows → `failed` with `note="daemon restart"` (conservative; matches current behavior).
  - `blocked(DELEGATED)` rows where all children are terminal → re-enqueue (the terminating child's `_enqueue_parent_if_waiting` call was lost to the crash).

### 7. Uniform lifecycle: root ≡ child

No special-case code. A root task is just `parent_task_id IS NULL`. Every task traverses:

```
pending → (run_step) → in_progress → { completed | failed | blocked(DELEGATED) | blocked(ESCALATED) }
                                         ↓                                ↑
                                    child terminates                      │
                                         ↓                                │
                                    (run_step again)                     │
                                         └────────────────────────────────┘
```

EH is not privileged: it is just the default `assigned_agent` for root tasks. Any agent can return any `NextStep` action, including `delegate` (i.e. workers can themselves delegate further if policy allows — guarded by the step budget).

### 8. Delegation walkthrough (end-to-end)

```
t0    EH root task T-010:  pending                                           enqueued on POST /tasks
t0+ε  (worker coroutine picks up T-010)
t1    T-010: pending → in_progress (count=1)
t1+5m T-010 returns: {"action": "delegate", "agent": "dev_agent", "prompt": "..."}
      T-011 inserted: pending, assigned_agent=dev_agent, parent_task_id=T-010
      T-010: in_progress → blocked(DELEGATED, note="Delegated to dev_agent (child=T-011)")
      queue.put(T-011)
t2    (worker coroutine picks up T-011, possibly on a different thread)
t2+ε  T-011: pending → in_progress (count=1)
t2+20m T-011 returns: {"action": "done", "summary": "Feature X landed"}
       T-011: in_progress → completed, note="Feature X landed"
       _enqueue_parent_if_waiting(T-011) sees T-010=blocked(DELEGATED),
       all children terminal → queue.put(T-010)
t3    (worker coroutine picks up T-010)
t3+ε  T-010: blocked(DELEGATED) → in_progress (count=2), prior_steps rebuilt from DB
t3+5m T-010 returns: {"action": "done", "summary": "Delegation succeeded"}
      T-010: in_progress → completed
      _enqueue_parent_if_waiting(T-010): parent_task_id is NULL → no-op
```

At every point, `opc tasks` shows the true state. Crash between any two lines → startup sweep recovers cleanly (either the task is already at a terminal, or it's `in_progress` and gets failed with `"daemon restart"`, or it's `blocked(DELEGATED)` with a terminal child and gets re-enqueued).

## Migration

One schema migration, run by the daemon at startup before serving requests. Idempotent (checks `PRAGMA table_info(tasks)` for column presence before `ALTER`).

```sql
-- Add new columns
ALTER TABLE tasks ADD COLUMN block_kind TEXT;
ALTER TABLE tasks ADD COLUMN note TEXT;
ALTER TABLE tasks ADD COLUMN orchestration_step_count INTEGER DEFAULT 0;

-- Fold final_output_summary into note
UPDATE tasks SET note = final_output_summary WHERE note IS NULL;

-- Map old statuses to new
UPDATE tasks SET status = 'completed' WHERE status = 'approved';
UPDATE tasks SET status = 'failed'    WHERE status = 'rejected';
UPDATE tasks SET status = 'blocked', block_kind = 'escalated'
    WHERE status = 'escalated';

-- Drop final_output_summary column (SQLite: rebuild table). Separate helper
-- that detects presence before rebuilding, so re-running the migration is a no-op.
```

## Code change surface

| File | Change |
|---|---|
| `src/models.py` | New `TaskStatus` (5 values); new `BlockKind` StrEnum; `TaskRecord` gains `block_kind`, `note`, `orchestration_step_count`; drops `final_output_summary` |
| `src/infrastructure/database.py` | Schema migration; `get_nonterminal_task_ids` update; ensure `get_children(task_id)` exists; add `get_blocked_with_kind(kind)` for startup sweep; rename reads/writes of `final_output_summary` → `note` |
| `src/orchestrator/orchestrator.py` | Delete `run_task`. Add `run_step`, `_complete`, `_fail`, `_enqueue_parent_if_waiting`, `_build_prior_steps_from_db`, `_validate_delegate`. `_parse_next_step` unchanged. |
| `src/daemon/state.py` | Queue + worker pool. `_TERMINAL_STATUS_TO_EVENT` updated: `COMPLETED → task_complete`, `FAILED → task_failed`, `BLOCKED → task_blocked` (new event type) |
| `src/daemon/runner.py` | Replaced with queue-worker coroutines pulling `task_id` and calling `run_step` via `run_in_executor` |
| `src/daemon/__main__.py` | Startup sweep described in §6 |
| `src/daemon/routes/tasks.py` | `POST /tasks` enqueues; SSE emits new `task_blocked` event; `resolve-escalation` precondition flips to `status==BLOCKED and block_kind==ESCALATED`; success calls `_enqueue_parent_if_waiting` |
| `src/cli.py` | `opc tasks` column headers extended with `block_kind` when present; `opc status` shows `note` |
| `tests/*` | Every TaskStatus reference updated; new tests for queue worker, child→parent resumption, startup sweep, budget persistence across restart, migration correctness |
| `src/infrastructure/audit_logger.py` | **Unchanged**. Semantic audit rows (`log_escalation`, `log_escalation_resolved`, etc.) fire at the same moments. |
| `src/infrastructure/kb_store.py`, `src/daemon/routes/kb.py` | **Unchanged.** KB pipeline is deliberately decoupled from task status. |

## Error handling

- **Daemon crash mid-`run_step`** → task stuck `in_progress` → swept to `failed` on restart. Parent (if any) gets enqueued by the startup sweep logic.
- **Invalid delegate decision** (missing agent, missing workspace, unknown agent) → `failed` with a specific `note`. Parent resumes.
- **Non-JSON EH output** → `failed` with a preview of the bad output. Same parser that already handles this in the main branch today.
- **Double-enqueue of the same task** → first call wins (transitions `pending → in_progress`), second call sees not-valid-entry and returns (see `run_step` step 1).
- **Resolve-escalation on a non-escalated task** → 409 with `code: task_not_escalated` (existing behavior; precondition updated to check `status==BLOCKED and block_kind==ESCALATED`).
- **Worker returns `status="blocked"`** → task → `failed` with prefix `self-blocked:`. Parent (if any) resumes.

## Testing

**Unit — `run_step`:**
Table-driven on the seven outcome branches in §4. Each case asserts (a) resulting `status`/`block_kind`/`note`, (b) whether parent was enqueued, (c) which audit rows were written, (d) `orchestration_step_count` incremented exactly once.

**Unit — `_enqueue_parent_if_waiting`:**
- Root task (no parent) → no-op.
- Parent in wrong status (e.g. `in_progress`) → no-op.
- Parent with wrong `block_kind` (e.g. `ESCALATED`) → no-op.
- Sibling still running → no-op.
- Legit waiting parent → enqueued exactly once.

**Unit — migration:**
Load fixture DB with pre-migration rows (all 7 old statuses) + some with `final_output_summary`, run migration, assert new shape:
- Every row has a valid new-world `status`.
- `block_kind` is `escalated` exactly for rows that were `escalated`, NULL elsewhere.
- `note` equals old `final_output_summary`.

**Unit — budget persistence:**
Create a task, run 3 steps, kill the in-memory orchestrator, re-run `run_step` on the same task ID, assert the counter continues from 4 (not 1).

**Integration — full delegation roundtrip:**
Using the existing fake-claude binary scaffold:
- Root task → EH returns `delegate` → child spawned and enqueued → fake claude runs as dev_agent → returns `done` → child completes → parent re-enqueued → EH returns `done` → root completes.
- Assert event ordering via the SSE stream.

**Integration — escalation roundtrip:**
- Child returns `escalate` → child becomes `blocked(ESCALATED)` → parent stays `blocked(DELEGATED)` → `POST /resolve-escalation approve` → child becomes `completed` → parent enqueued → parent runs to `done`.

**Integration — crash recovery:**
- Task stuck `in_progress` at daemon startup → swept to `failed(note="daemon restart")`.
- Task `blocked(DELEGATED)` with all children already terminal → re-enqueued and runs to completion.

## Open questions

None blocking. Discussed and resolved during brainstorming:
- `NEEDS_FOUNDER` → `ESCALATED` (keeps terminology continuous with existing audit/CLI).
- KB precedent flow stays independent (design principle: task status ⊥ KB state).
- Async model = per-step re-invocation (not `claude --resume`), because state lives in the DB, recovery is trivial, and it matches existing bones.
- Reason representation = structured (`block_kind` enum + generic `note` text field), not free-text with sentinel prefixes.
