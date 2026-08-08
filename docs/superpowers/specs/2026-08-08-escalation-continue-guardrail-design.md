# Surface `continue` in the Thread Escalation Guardrail — Design Spec

**Date:** 2026-08-08
**Status:** Draft, pending implementation.
**Origin:** Process audit of THR-107 (GitHub issue [#618](https://github.com/t-benze/happyranch/issues/618)) found that a single thread accumulated 30 `superseded` root tasks averaging 117 minutes each (58.7 of 87 total task-hours) before the initiative was cancelled. Every one of those supersessions re-stated a large multi-paragraph context brief and started a brand-new task (fresh worktree, fresh GitNexus run, fresh agent session) to resolve what was often just a founder "ok proceed" on an already-escalated task.
**Relates to:**
- `docs/superpowers/specs/2026-06-06-thread-escalation-surfacing-design.md` — the `task_escalated` system-message + re-invocation machinery this spec's guardrail already rides on.
- THR-018 §3a / THR-080 — the `resolves` (supersede) and `continue` resolution verbs this spec surfaces, both already implemented.

## 1. Goal

When a manager is re-invoked (REPLY/BOOTSTRAP) in a thread that has an unresolved `task_escalated` message, the injected guardrail note today tells the agent about exactly one resolution path: dispatch a new task naming `{"resolves": "<tid>"}` (supersede). This spec adds the second, already-implemented path — `decision=continue` — to that same note, so the agent is told, at the moment it matters, that resuming the *same* task in place is an option whenever it actually applies.

No new backend capability is introduced. `POST /tasks/{task_id}/resolve-escalation` with `decision=continue` (`runtime/daemon/routes/tasks.py:832-864`, THR-080) already: keeps the task ID unchanged, leaves the original brief untouched, appends the founder's rationale as an audited `note`, and re-enqueues the same task to `PENDING`. It is reachable today via `happyranch resolve-escalation --task-id <tid> --decision continue --rationale "..."`, and the underlying route has no founder-only auth check (agents and the founder share one bearer token — see `runtime/daemon/routes/tasks.py:717-719`).

## 2. Motivation

Audited evidence (`happyranch audit --org happyranch --action escalation_resolved`): org-wide, `continue` has been used only 24 times, ever. For THR-107 specifically — checked every one of its ~80 tasks — it was used **zero** times. Every escalation was instead resolved via `POST /threads/{id}/dispatch` with `resolves:`, which always mints a new task and permanently closes the predecessor as `SUPERSEDED`.

Root cause, verified in code and prompts:

- `engineering_manager.md` (the live org agent definition) never mentions `resolve-escalation`, `continue`, or `supersede` anywhere.
- The one piece of system-injected doctrine text a manager actually sees at the relevant moment — `_maybe_unresolved_escalations_note` in `runtime/daemon/thread_runner.py:177-249` — mentions only the `resolves` dispatch path. It never mentions `continue`.
- `SELF_DISPATCH_HINT` (`runtime/daemon/routes/_doctrine.py`), the only other system-authored doctrine text touching this area, explicitly recommends "self-dispatch a manager root ... (recommended for iterative phase work)" — i.e. it points toward minting new tasks, with no mention of resuming an existing one.
- `protocol/05c-orchestrator.md` and the user manual document `continue` as something the **founder** runs (`happyranch n --decision continue` / a web "Continue" button) — never as something a manager should reach for itself when handling a thread reply.
- The CLI's own help text labels `resolve-escalation` **"(founder only)"** (`cli/commands/tasks.py:1083`), which is inaccurate (no such check exists in the route) but would directly contradict new doctrine text telling a manager to use it.

The behavior was "correct" per each individual prior spec — THR-080 built `continue` as a founder-facing verb and never claimed it was manager-facing; this spec is the first to make it manager-facing in the one place a manager is actually prompted about escalation resolution.

## 3. Non-goals

- No change to `engineering_manager.md` or any other org agent `.md` file.
- No change to `resolve_escalation_in_process`, the `resolve-escalation` routes, or `_eligible_supersede_block_kind` — all already behave correctly.
- No new CLI subcommand. The existing top-level `happyranch resolve-escalation --decision continue` is reused as-is (consistent with the existing trust model: `dispatch {resolves:}` also relies on the shared-bearer trust boundary rather than a thread-lineage-scoped token for its authority check on the manager side).
- No heuristic detection of "is this founder reply a simple approval vs. a substantive new brief." The note presents both options with guidance on when each applies; the manager's own judgment picks.
- No change to `SELF_DISPATCH_HINT` itself (out of scope for this pass — it fires in a different code path, on dispatch validation failure, not on thread wake).

## 4. Eligibility: `continue` is stricter than `resolves`

`_eligible_supersede_block_kind(org, task)` (`runtime/daemon/routes/tasks.py:994+`) returns one of two values for a supersedable predecessor:

- `"escalated"` — `task.status == TaskStatus.ESCALATED`.
- `"delegated"` — `task.status == IN_PROGRESS`, `block_kind == DELEGATED`, and all children are terminal (Gap-B safety gate).

`continue` (`resolve_escalation_in_process`, decision branch) only accepts a task whose status is literally `ESCALATED` — it 409s (`task_not_escalated`) otherwise — and additionally fail-closes if the task has any live children (`_has_live_children`). A `"delegated"` predecessor is therefore **never** `continue`-eligible, only `resolves`-eligible.

The guardrail note must therefore track *which* block-kind each escalated task resolved to, not merely whether one exists, and only offer `continue` for the `"escalated"` case.

## 5. Change 1 — `_maybe_unresolved_escalations_note`

**File:** `runtime/daemon/thread_runner.py`

Current behavior (single- and multi-task branches, lines ~219-249): builds a list of supersedable `task_id`s and emits one `resolves`-only note.

New behavior: partition the eligible task ids by their `_eligible_supersede_block_kind` result into `continue_eligible` (`"escalated"`) and `delegated_only` (`"delegated"`). Note text:

- For a `continue_eligible` task: state both options —
  - *If the founder's reply simply resolves this escalation and no new task-shaped work is needed:* `happyranch resolve-escalation --task-id <tid> --decision continue --rationale "<summary>"`. Same task ID, original brief untouched, resumes in place.
  - *If the founder's direction requires new delegated work:* the existing `{"resolves": "<tid>"}` dispatch example, unchanged.
- For a `delegated_only` task: keep today's `resolves`-only text, unchanged (no mention of `continue`, since it would 409).
- Multiple escalated tasks of mixed kinds: each task gets its own correctly-labeled block, same pattern as the existing multi-task branch.

The function signature does not change; both call sites in `run_invocation` (lines ~652, ~719) need no changes.

Example single-task, `continue`-eligible note (illustrative wording — implementation may adjust phrasing, but must preserve both example payloads verbatim-parseable, matching the existing test style of asserting on substrings like `"resolves"` / `--decision continue`):

```
## Unresolved Escalation in This Thread

Task **TASK-900** escalated in this thread and is still awaiting a
founder-authorized resolution. Pick the option that matches the founder's
reply:

- If the founder's reply resolves this escalation with no new task-shaped
  work needed, resume the SAME task in place — original brief untouched,
  the reply is appended as an audited note:
  `happyranch resolve-escalation --task-id TASK-900 --decision continue --rationale "<summarize the founder's reply>"`

- If the founder's reply requires new delegated work, your next
  self-dispatched task MUST include the explicit linkage:
  ```json
  {"resolves": "TASK-900"}
  ```
  Omitting this field leaves the predecessor open — the runtime cannot
  infer the relationship from brief prose alone.
```

For a `delegated_only` task, the block keeps only the second bullet (today's existing text), unchanged.

## 6. Change 2 — CLI help text

**File:** `cli/commands/tasks.py:1083`

```python
p_resolve = sub.add_parser("resolve-escalation", help="Resolve an escalated task (founder only)")
```

→

```python
p_resolve = sub.add_parser("resolve-escalation", help="Resolve an escalated task")
```

Dropping the inaccurate qualifier only — no behavior change. This keeps the new doctrine text from pointing an agent at a command whose own `--help` claims it isn't allowed to use it.

## 7. Error handling / edge cases

- Zero escalated tasks in thread lineage → note is `""`, unchanged.
- Escalated task resolved/superseded between the message being posted and this invocation running → already filtered out today (`_eligible_supersede_block_kind` re-checks live task status), unaffected by this change.
- A task that is `"escalated"` but has live children in some edge state → `_maybe_unresolved_escalations_note` doesn't check `_has_live_children` (that's `continue`'s own runtime guard). Worst case the agent's `continue` attempt gets a 409 from the route and it falls back to `resolves`; this is an acceptable, already-safe failure mode (the route is the source of truth, the note is guidance, not a guarantee).

## 8. Testing

Extend `tests/test_thread_escalation_guardrail.py`:

- Escalated (status=`ESCALATED`) predecessor → note contains both `continue` (with the exact CLI invocation shape) and `resolves` text.
- Delegated (in_progress/DELEGATED, terminal children) predecessor → note contains `resolves` only, no mention of `continue`.
- Mixed set (one of each) → note correctly labels each task under its own eligibility.
- Existing single/multi/non-manager/already-resolved/not-found/no-teams/no-escalations/dup-id tests continue to pass unchanged (behavior for those cases is preserved).
- `run_invocation` boundary tests (`test_run_invocation_injects_guardrail_for_supersedable_escalation`, `test_run_invocation_skips_guardrail_for_non_supersedable_predecessor`) extended or duplicated for the `continue`-eligible case, asserting the executor-received prompt contains the `continue` CLI invocation text.
- A focused CLI test (or manual `happyranch resolve-escalation --help` check) confirming the help string no longer says "(founder only)".

## 9. Risk

Low. Both changes are prompt/help-text only:
- `_maybe_unresolved_escalations_note` produces guidance text injected into an agent prompt — it has no effect on task/thread state, auth, schema, or the permission model.
- The CLI help-string edit is a docstring change with zero behavioral effect.

Neither touches `runtime/orchestrator/run_step.py`, the task state machine, or any HIGH/CRITICAL symbol. No `protocol/` contract changes — the `resolve-escalation` semantics documented there are unchanged, only who is told about them and when.
