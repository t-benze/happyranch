# Cancel-Race — Design Spec

**Date:** 2026-05-26
**Status:** Draft, pending implementation.
**Origin:** Surfaced while investigating TASK-497, which entered a contradictory `blocked(delegated) + cancelled_at != NULL` state after a founder cancel.
**Relates to:**
- `src/daemon/routes/tasks.py` — `submit_completion` (l.221), `submit_progress` (l.275), `cancel_task` (l.682).
- `src/orchestrator/run_step.py` — `run_step_impl` (l.29), `_complete` (l.611), `_fail` (l.632), the `delegate` / `escalate` branches (l.193, l.209).
- `src/daemon/routes/scripts.py` — `submit_script` (l.64), the existing reference for `task_not_active`-before-session validation order.
- `docs/superpowers/specs/2026-04-21-opc-revisit-design.md` — revisit is the founder's recovery primitive *after* this race, but doesn't prevent it.

## 1. Goal

`grassland cancel <task>` must be a hard stop. Once the founder cancels a task, no subsequent decision from that task's in-flight session can spawn a child task, overwrite the cancellation status, or rewrite the founder's note.

## 2. Motivation — what TASK-497 showed

Founder ran `grassland cancel TASK-497 --cascade` at `2026-05-26T01:57:16.726Z`. Nineteen seconds later TASK-501 was created as a child of TASK-497 and ran to terminal completion, shipping PR #165 against `main` from a task tree the founder had explicitly cancelled.

Audit timeline (`grassland audit TASK-497 / TASK-501`):

| Time | Event |
|---|---|
| 01:08:09.557 | TASK-497 session_start (EH) |
| **01:57:16.726** | **task_cancelled, cascade=true** |
| 01:57:35.702 | EH session_end (19s post-cancel) |
| 01:57:35.704 | EH completion_report (decision: `delegate→dev_agent`) |
| 01:57:35.705 | orchestration_step (parent: delegate) |
| 01:57:35.707 | TASK-501 session_start (dev_agent) |
| 02:16:34.701 | TASK-501 completed (PR #165 shipped) |

End state of TASK-497:
- `status = "blocked"`
- `block_kind = "delegated"`
- `cancelled_at = 2026-05-26T01:57:16.724980Z`
- `completed_at = 2026-05-26T01:57:16.724980Z`
- `note = "Delegated to dev_agent (child=TASK-501)"`

A status row that simultaneously says "blocked, delegated" and "cancelled by founder" is invalid. The founder's note (`"cancelled by founder"`) was overwritten silently.

The blast radius isn't theoretical — PR #165 is real, on GitHub, sitting open, from a lineage the founder cancelled. If the cancel had been issued for cost reasons (production OSS upload), the downstream `grassland scripts submit` flow would have proceeded too.

## 3. Root cause — three missing guards

The cancel route (`tasks.py:682-781`) is two-phase by intentional design (see its docstring at l.686-695):

1. **Phase 1 (under `db_lock`)**: stamp `status=FAILED + cancelled_at=now`, audit `task_cancelled`, snapshot live PIDs from `SessionTracker.iter_task_pids`.
2. **Phase 2 (outside the lock)**: `os.kill(pid, SIGTERM)`, then `SessionTracker.clear(tid, agent)`.

The ordering's rationale: stamp the DB row *before* SIGTERM so that when the subprocess dies with rc=-15, run_step's post-Popen classifier sees `status=FAILED` and short-circuits via the idempotence guards in `_complete` / `_fail`. **That mechanism is correct for the case it was designed for** — what it doesn't anticipate is a completion that arrives *between* Phase 1 and Phase 2.

Three independent guards are missing:

### 3.1 Guard A — `submit_completion` route has no cancellation check

`tasks.py:221-266` validates exactly two things: `get_active` non-None (rejects `unknown_session`) and `expected == body.session_id` (rejects `session_mismatch`). It does not look at `task.cancelled_at`.

Race window:
- T+0 Phase 1 releases `db_lock`. Tracker still has the session (Phase 2 hasn't run yet).
- T+ε `submit_completion` arrives, calls `get_active` → returns the still-valid session_id.
- T+ε `submit_completion` blocks briefly on `db_lock` to acquire `insert_task_result`.
- T+δ Phase 2 runs `os.kill` + `clear`, but the row is already written.

The race is small but real, and `scripts.py:76-81` already implements exactly the right pattern for the symmetric SR route: check `task.status not in {pending, in_progress}` *before* session ownership. The completion route should mirror that, plus check `cancelled_at`.

### 3.2 Guard B — `run_step_impl` doesn't re-check `cancelled_at` after `_run_agent` returns

The cancellation guard at `run_step.py:41` only fires for *new* queue events. A still-running step that entered run_step_impl pre-cancel passes the guard, claims the task, blocks on `_run_agent(...)` for the entire session (49 minutes in TASK-497's case), and returns into the post-Popen flow with no re-check.

If Guard A misses (or the daemon ever gains a non-HTTP completion-discovery path), the orchestrator has no way to notice the task is now cancelled before it processes `report`.

### 3.3 Guard C — `delegate` and `escalate` branches have no idempotence

`_complete` (l.611-629) and `_fail` (l.632-649) both implement:

```python
existing = orch._db.get_task(task_id)
if existing is not None and existing.status in TERMINAL_STATES:
    return
```

This is what makes the cancel-route's "Phase 1 stamps DB before SIGTERM" design work — a post-cancel `_fail("rc=-15")` short-circuits and doesn't overwrite the founder's note.

The `delegate` branch (l.209-290) and `escalate` branch (l.193-207) have no such guard. They unconditionally call `db.update_task(task_id, status=...)`. `delegate` *also* calls `db.insert_task(...)` for the child + `orch._queue.put_nowait(child_id)`.

That's why TASK-497 ended up `blocked(delegated)` with `cancelled_at` set and the note overwritten: the `delegate` branch resurrected its parent.

## 4. Non-goals

- **No changes to the Phase 1 / Phase 2 ordering in `cancel_task`.** The "stamp DB before SIGTERM" rationale is correct and load-bearing for the rc=-15 case. We add gates *around* it, not inside it.
- **No `tasks.cancelled_at`-aware predicate at the SQL UPDATE layer** (e.g., a CAS `WHERE cancelled_at IS NULL` on every `update_task`). Out of scope; a Python-level idempotence check at the call sites is sufficient for v1 and easier to audit.
- **No retroactive cleanup of historical contradictory rows.** TASK-497 is one known instance; the founder unstuck it manually (TASK-497 transitioned to `failed-cancelled` on 2026-05-26 mid-day). Future occurrences are prevented by this spec; existing rows are not rewritten.
- **No change to revisit eligibility.** Once a cancelled task is properly `failed-cancelled` (status FAILED + cancelled_at set), `grassland revisit` already accepts it. The bug was a row that *should* have been `failed-cancelled` instead reading as `blocked(delegated)`.
- **No new failure_kind for "post-cancel completion."** The completion is *dropped*, not failed — the task was already terminal-by-cancel. A dropped completion produces no new audit row (the existing `task_cancelled` row is the founder-of-record); a debug log is sufficient.

## 5. Design — three layered guards

### 5.1 Guard A — HTTP-level rejection in `submit_completion` and `submit_progress`

Add a task-active check *before* session validation, matching the `scripts.py` order:

```python
# src/daemon/routes/tasks.py:221, submit_completion
task = org.db.get_task(task_id)
if task is None:
    raise HTTPException(404, detail={"code": "unknown_task", "task_id": task_id})
if task.cancelled_at is not None or task.status in _TERMINAL_TASK_STATUSES:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "task_not_active",
            "task_id": task_id,
            "status": task.status.value,
            "cancelled": task.cancelled_at is not None,
        },
    )
# existing session checks follow unchanged
```

Apply the same prefix to `submit_progress` (l.275). The progress route doesn't spawn children, but a post-cancel progress beat is still noise on the audit log and is symptomatic of the same race.

`_TERMINAL_TASK_STATUSES` is already defined at `tasks.py:414` (`frozenset({COMPLETED, FAILED})`); the constant moves to the top of the module so the routes can reference it.

**Validation order rationale**: `task_not_active` before `session_mismatch` because (a) it matches `scripts.py:76-81`, (b) a cancelled task reporting `session_mismatch` would mislead the agent into thinking its session was bumped when actually its whole task was terminated, and (c) reporting the terminal cause is more informative for debugging.

### 5.2 Guard B — orchestrator post-`_run_agent` re-check

In `run_step.py:103`, after the `try/except` block that already handles exceptions from `_run_agent`, re-fetch the task and short-circuit if cancelled:

```python
result, report = orch._run_agent(task_id, agent, prompt)

# Token usage is persisted REGARDLESS of cancellation. The session really
# consumed those tokens on the provider side; founder /tokens rollups must
# reflect actual spend, not moral judgment about whether the work survived.
# Existing contract: ExecutorResult.token_usage writes are unconditional
# (see tests/test_run_step_token_usage.py and src/daemon/routes/tokens.py
# which reads session_token_usage directly). Keep this BEFORE the drop.
if result.token_usage is not None:
    db.insert_session_token_usage(
        task_id=task_id,
        agent=agent,
        session_id=result.session_id,
        executor=orch._resolve_executor_name(agent),
        token_usage=result.token_usage,
    )

# Cancellation re-check: /cancel can land between try_claim_for_step and
# subprocess exit. The l.41 guard only catches NEW enqueues. If we observe
# cancelled_at != NULL here, the report (if any) is from a cancelled tree
# and must not feed the decision pipeline.
refetch = db.get_task(task_id)
if refetch is None or refetch.cancelled_at is not None:
    logger.debug(
        "run_step %s: cancelled during session, dropping report", task_id,
    )
    return
```

**Placement**: the re-check sits *between* the existing token-usage persistence (currently at run_step.py:121-133) and the outcome classification (currently at l.135). In practice that means the existing token-usage block stays where it is, and the new `refetch / cancelled_at` check is inserted immediately after it. Codifying token-usage-first is intentional: provider spend is the source of truth for `/tokens` rollups and must not be dropped on the cancellation path.

**No `_fail` call** in the drop path. `_fail` would attempt to overwrite the `note` field, which the cancel route already set to `"cancelled by founder: <rationale>"`. The idempotence guard in `_fail` would catch that, but it's cleaner to just return.

### 5.3 Guard C — idempotence on `delegate` and `escalate`

Mirror the pattern from `_complete` / `_fail`. Lift the check into a helper to keep the four call sites symmetric:

```python
# src/orchestrator/run_step.py
def _is_already_terminal(orch: "Orchestrator", task_id: str) -> bool:
    existing = orch._db.get_task(task_id)
    return existing is None or existing.status in TERMINAL_STATES or existing.cancelled_at is not None
```

The helper is the source of truth for `_complete` / `_fail`'s Python-level idempotence — both get refactored to call it. Single source of truth replaces two near-duplicate inline guards.

**Why include `cancelled_at` in the predicate even though `status` should be FAILED whenever `cancelled_at` is set**: defense in depth. `_complete`'s pre-spec comment documents the *intent* ("don't resurrect a cancelled task back to COMPLETED"), but only checks `status in TERMINAL_STATES`. If a future code path ever sets `cancelled_at` without flipping `status` (e.g., a partial-cancel for a sub-feature, a migration bug), the existing guard misses. The new predicate is correct under both invariants.

### 5.3.1 Atomic CAS for the spawn-new-work branches (`delegate`, `escalate`)

A Python-level `if _is_already_terminal(...): return` followed by `db.insert_task(...)` / `db.update_task(...)` is non-atomic with the cancel route's `update_task` — Codex review of PR #34 surfaced that `/cancel` can land *between* the helper's `get_task` and the subsequent write, leaving the original TASK-497 bug shape intact (cancelled parent ends up with a child + status overwritten back to `blocked(...)`).

The `delegate` and `escalate` branches must therefore use SQL-level CAS, matching the existing `Database.try_claim_for_step` pattern (`database.py:805-844`). Two new methods land on `Database`:

```python
@_synchronized
def try_escalate(self, task_id: str, *, reason: str) -> bool:
    """Conditional UPDATE: transition task to BLOCKED(ESCALATED) only if
    cancelled_at IS NULL AND status NOT IN ('completed', 'failed').
    Returns True iff the row transitioned."""
    cursor = self._conn.execute(
        """UPDATE tasks
           SET status = ?, block_kind = ?, note = ?, updated_at = ?
           WHERE id = ?
             AND cancelled_at IS NULL
             AND status NOT IN ('completed', 'failed')""",
        (TaskStatus.BLOCKED.value, BlockKind.ESCALATED.value, reason, now, task_id),
    )
    self._conn.commit()
    return cursor.rowcount == 1

@_synchronized
def try_delegate(
    self, parent_id: str, child: TaskRecord, *, parent_note: str,
) -> bool:
    """Atomic: insert child + transition parent to BLOCKED(DELEGATED).
    Both writes happen under one @_synchronized acquisition (threading.RLock),
    the same lock the cancel route's update_task acquires."""
    row = self._conn.execute(
        "SELECT status, cancelled_at FROM tasks WHERE id = ?", (parent_id,)
    ).fetchone()
    if row is None or row["cancelled_at"] is not None or row["status"] in ("completed", "failed"):
        return False
    self.insert_task(child)
    self._conn.execute(
        """UPDATE tasks SET status = ?, block_kind = ?, note = ?, updated_at = ?
           WHERE id = ?""",
        (TaskStatus.BLOCKED.value, BlockKind.DELEGATED.value, parent_note, now, parent_id),
    )
    self._conn.commit()
    return True
```

**Atomicity guarantee**: `@_synchronized` is the `threading.RLock` decorator already used throughout `Database`. The cancel route's `update_task` is also `@_synchronized`. So the only two interleavings are:

- **Cancel acquired RLock first** → cancel stamps row → `try_*` runs → SELECT (or CAS predicate) sees `cancelled_at != NULL` → returns False, no writes.
- **`try_*` acquired RLock first** → method runs to completion (commits child + parent in one atomic window) → RLock released → cancel runs → sees parent in `BLOCKED(DELEGATED)`, transitions to FAILED, cascade-cancels the now-existing child.

Either order is correct. The middle case — "cancel interleaves between SELECT and UPDATE" — is impossible because the RLock is held for the entire method body.

The branches in `run_step.py` collapse to:

```python
if decision.action == "escalate":
    reason = decision.reason or "Escalated"
    if not db.try_escalate(task_id, reason=reason):
        return  # cancel won the race
    orch._audit.log_escalation(task_id, agent, reason)
    orch.notify_escalated(...)
    return

if decision.action == "delegate":
    # ...existing _validate_delegate, cross-team check, revision tracking...
    child = TaskRecord(id=child_id, ..., parent_task_id=task_id, ...)
    if not db.try_delegate(task_id, child, parent_note=f"Delegated to {agent} (child={child_id})"):
        return  # cancel won the race; no child, no overwrite, no enqueue
    if orch._queue is not None:
        orch._queue.put_nowait(orch._slug, child_id)
    return
```

The Python-level `if _is_already_terminal(orch, task_id): return` is **removed** from these branches — the CAS replaces it. Guard B's re-fetch above remains; it's an early-rejection cheap path that avoids the SQL round-trip in the common case (and keeps token-usage persistence before the drop).

**What about `_complete` and `_fail`?** Lower-priority by impact: neither spawns new work. The original Python-level idempotence guards (via `_is_already_terminal`) survive on these. The residual race is "founder's note `cancelled by founder: stop` may be observable for one window before `_fail` runs and the idempotence guard catches it" — but the *intent* of the founder's cancel is preserved (status stays FAILED, cancelled_at stays set). Promoting these to CAS too would be straight-line cleanup; deferred as cost-benefit isn't load-bearing.

## 6. Interaction with auto-revisit

`_maybe_spawn_auto_revisit` already gates on `chain[0].cancelled_at` (`run_step.py:965`), so auto-revisit correctly suppresses for cancelled chains. **No changes needed there.**

The drop-on-cancel path in Guard B does *not* call `_maybe_spawn_auto_revisit` — the session ran successfully (or failed for reasons we're now ignoring); either way, the founder's cancel intent rules. Skipping auto-revisit on a cancellation drop is the correct behavior; it's also what would happen today because `_maybe_spawn_auto_revisit` is only called from the `except Exception` branch and the `not result.success or report is None` branch, neither of which the Guard B drop traverses.

## 7. Interaction with `_enqueue_parent_if_waiting`

If TASK-497 had a parent (it didn't — it was a root), Guard B would skip the call to `_enqueue_parent_if_waiting`. That's correct: a cancelled task's parent is *also* cancelled by `cascade=true` (default), and even on `cascade=false` the parent should not be auto-resumed by a child that was cancelled mid-flight. The parent enters its own `blocked(delegated)` state and either the founder revisits or the task ages out — both founder-visible behaviors that don't depend on auto-resume.

(Reviewers: verify `cascade=false` parent behavior in the test suite; this spec doesn't change it, but the interaction is worth a regression test.)

## 8. Test plan

### 8.1 Unit tests

1. **`tests/unit/test_submit_completion_cancel_gate.py`** — new file:
   - Insert task in `FAILED` + `cancelled_at` set. Register active session in tracker. POST to `/completion` with valid session_id → assert 409 with `code=task_not_active`.
   - Insert task in `PENDING`, no `cancelled_at`. POST → assert 200 + row inserted (regression).
   - Insert task in `COMPLETED` (no `cancelled_at`). POST → assert 409 (`task_not_active` triggers on terminal status alone, not just cancellation).
   - Same matrix for `/progress`.

2. **`tests/unit/test_run_step_post_cancel_drop.py`** — new file or extend existing run_step tests:
   - Stub `_run_agent` to (a) call `db.update_task(task_id, status=FAILED, cancelled_at=<now>)` mid-call to simulate `/cancel` landing during the session, then (b) return `(success_result, delegate_decision_report)`.
   - Invoke `run_step_impl` with a `PENDING` task. The l.41 guard does NOT fire (task is PENDING at entry); the claim succeeds; `_run_agent` runs to its monkey-patched body; Guard B then observes `cancelled_at != NULL` on re-fetch and drops the report.
   - Assert: no child task was inserted, the parent's status / note remain in the founder-set "cancelled by founder" shape, and the token-usage row WAS persisted (token accounting must survive the drop — see §5.2).
   - **Do not** test by stamping `cancelled_at` *before* calling `run_step_impl`. That path is already covered by the l.41 entry guard and would never exercise Guard B.

3. **Database CAS tests** (in `tests/test_database.py`):
   - `try_escalate` happy path / cancelled-rejected / terminal-rejected / missing-task.
   - `try_delegate` happy path (parent transitions AND child inserted in one atomic window) / cancelled-parent-rejected-AND-no-child-inserted / terminal-parent-rejected / missing-parent.

4. **Atomic-race tests** in `tests/test_run_step.py`: simulate the worst-case interleaving — cancel landing between Guard B's re-fetch and the CAS write — by monkey-patching `db.try_delegate` / `db.try_escalate` to stamp `cancelled_at` immediately before delegating to the real implementation. Assert no child created, parent state preserved, queue empty.

### 8.2 Integration test

**`tests/integration/test_cancel_race.py`** — new file, replays the TASK-497 timeline:

- Use `fake_claude_plan_env` to script a manager session that sleeps ~3 seconds and then POSTs a `delegate→worker` completion.
- Submit a root task, wait for `session_start` audit.
- Concurrently call `grassland cancel <task-id>` mid-session.
- Wait for the manager session to exit (it will POST late).
- Assertions:
  - The HTTP POST to `/completion` returned 409 `task_not_active` (Guard A).
  - No child task exists (`db.get_children(task_id) == []`).
  - Parent row remains `FAILED + cancelled_at + note=cancelled by founder` (Guard C — no overwrite even if Guard A somehow missed).
  - No `orchestration_step` audit row after `task_cancelled` (Guard B — orchestrator dropped the report).

The integration test is the load-bearing regression check; the unit tests are coverage for each guard in isolation.

### 8.3 Regression — re-run existing cancel suite

- `tests/integration/test_cancel.py` (or wherever the existing cancel coverage lives) — verify the "stamp DB before SIGTERM" rc=-15 case still short-circuits correctly. None of the three new guards interferes with the SIGTERM → rc=-15 → `_fail`-idempotent flow because that flow doesn't involve a completion report.

## 9. Migration / backwards compatibility

None required. The three guards are pure preventive checks; no schema change, no audit-payload change, no on-disk format change.

Historical contradictory rows (TASK-497 and any like it the founder uncovers) remain as-is in the audit log. Future analytics that want to find them can query `tasks WHERE cancelled_at IS NOT NULL AND status != 'failed'`. The fix prevents creation of new such rows; it does not rewrite history.

## 10. Implementation order

1. Add Guard A (`submit_completion` + `submit_progress`). Smallest diff, biggest effect. Write the unit tests first; watch them fail; ship the route change; watch them pass.
2. Add Guard B (orchestrator re-check). Independent of A.
3. Add Guard C (decision-branch idempotence helper + four call sites). Independent of A and B.
4. Ship the integration test last; it exercises all three.

Each guard is independently safe to land. Recommended single PR for review coherence, but a three-PR split is acceptable if Guard B's run_step changes provoke larger discussion.

## 11. Known limits

- **Window between `get_task` and `insert_task_result` in `submit_completion`** is still racy in theory: the cancel route could land between the new `get_task` check and the `insert_task_result`. Guard B catches anything that slips through here. Closing the daemon-side window completely would require holding `db_lock` across both the read and the write (currently the read is outside the lock); deferred as cost-benefit doesn't justify it given Guard B.
- **Phase 2 of `cancel_task` still clears the tracker outside `db_lock`.** Moving the clear inside Phase 1 would close another window, but the comment at `tasks.py:686-695` explicitly designs around the SIGTERM ordering. Out of scope for this spec; reasonable follow-up if a future race is found.
- **Token usage IS persisted on cancellation drops** — explicitly NOT a limit. See §5.2. The provider charged for those tokens; `/tokens` rollups must reflect spend, not survival. (Earlier draft of this spec proposed dropping usage on cancel; that would have undercounted spend and broken the `tests/test_run_step_token_usage.py` contract — corrected after Codex review.)
- **`_complete` and `_fail` keep their Python-level idempotence**, not the SQL-CAS upgrade applied to `delegate` / `escalate`. The residual race is observable only as "founder's `cancelled by founder` note may be momentarily overwritten before the idempotence guard's get_task catches it" — and even that window is bounded by the next-statement `if existing is None or ... in TERMINAL_STATES: return`. Neither branch spawns new work; the worst-case is a status flicker, not a corrupted tree. Promoting these to CAS would be straight-line cleanup if the audit log ever shows the flicker actually happening; until then, the cost-benefit doesn't justify it.
