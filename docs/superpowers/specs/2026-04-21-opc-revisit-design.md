# `opc revisit` — Founder-Initiated Task Revival

**Status:** Design approved, pending implementation plan
**Author:** Founder + Claude Opus
**Date:** 2026-04-21
**Supersedes:** — (new feature)
**Related:**
- `fix(orchestrator): cascade-fail delegated chain + executor diagnostics` (2f5756e) — this design is the counterweight to that commit's no-retry policy.
- `feat(daemon): opc cancel — SIGTERM + cascade + race-safe DB` (a4e7c1c) — revisit composes with cancel: cancel + revisit is the recovery path for wedged `blocked(delegated)` roots, and revisit must clear `cancelled_at` on the root so `run_step`'s cancellation short-circuit doesn't drop the re-enqueued task.

## 1. Problem

The cascade-fail policy landed in 2f5756e makes any delegated child's failure collapse its entire ancestor chain to `FAILED`, with no in-lineage retry path. This is deliberate — it stopped the 6+ duplicate-retry spirals on TASK-033..038 and TASK-041..045 — but it leaves the founder without a recovery surface when a failure is actually worth re-examining.

Today, the only recovery path is to copy the original brief and resubmit as a new root task via `opc run --brief "..."`. That loses the audit lineage of the prior attempt: the EH for the new task cannot see what was tried, what partial progress was made, or which sub-tasks completed before the cascade fired.

We need a founder-initiated mechanism that lets the Engineering Head see the prior attempt's cascade trace and decide — *autonomously* — whether to restart, resume from a specific point, or abandon.

## 2. Non-Goals

- **No reviving in-flight work.** `blocked(delegated)` roots — where children are still running — are rejected. The founder must first `opc cancel` the subtree (landed on main as of a4e7c1c), which flips the root to `failed + cancelled_at != NULL`, making it revisit-eligible.
- **No schema migration.** No `attempt_number` column, no new tables. Revisit state is carried entirely by the existing `tasks` row and `audit_log` table.
- **No UI beyond CLI.** The founder reads revisit history through the existing `opc audit` command.
- **No retry limit.** Revisits are stackable; a revisit whose second attempt also cascade-fails can itself be revisited. Trusting the founder.
- **No agent-facing callback.** `opc revisit` is founder-only (runs from the local CLI, not invoked inside agent sessions). No `--as-founder` flag needed — matches the `opc resolve-escalation` convention.

## 3. User-Facing Interface

### 3.1 CLI

```
opc revisit <task-id> [--note "<founder hint>"]
```

- **`task-id`** — any task whose root is in an eligible state (see §5). Can be a leaf, mid-tree, or the root itself. The flagged task's **own** status is not validated — only the root's. This is intentional: the founder may flag a `completed` sibling as context ("branch from here"), a `failed` leaf as a redo target, or the root itself. The flagged ID is passed to the EH in the prompt header; the EH decides what to do with it.
- **`--note`** — optional free-text hint appended to the `revisit_requested` audit payload. Surfaces to the EH as `Founder note: <text>` in its prompt header. Useful when the founder knows something the audit log does not (e.g. "PR #103 was merged manually out-of-band").

After submission, the CLI streams the **root's** events (not the flagged task's). Prints the resolved root task ID up front so the founder can confirm what they're reviving.

**Example:**

```
$ opc revisit TASK-058 --note "PR #103 already merged manually"
Revisiting TASK-052 (root of TASK-058's lineage).
Cascade: TASK-052 → TASK-058
Submitted; streaming events (Ctrl-C to detach)...
[orchestration_step] {...}
[task_complete] {...}
```

**Safety tier:** matches `resolve-escalation` — lives in the opc skill's *"Confirm with user first"* category.

### 3.2 HTTP

```
POST /api/v1/tasks/{task_id}/revisit
Authorization: Bearer <token>
Content-Type: application/json

{ "founder_note": "..." | null }
```

**200 OK:**
```json
{
  "root_task_id": "TASK-052",
  "cascade": ["TASK-052", "TASK-058"],
  "prior_root_status": "failed"
}
```

`prior_root_status` is one of `"failed"`, `"failed-cancelled"`, or `"blocked-escalated"` — matches the value that will appear in the EH prompt header.

**404** — unknown `task_id`.

**409 `cannot_revisit`** — root is in an ineligible state. Body:
```json
{
  "detail": {
    "code": "cannot_revisit",
    "reason": "root TASK-052 is in_progress",
    "root_task_id": "TASK-052",
    "root_status": "in_progress",
    "block_kind": null
  }
}
```

**500 `lineage_too_deep`** — safety bound (20 hops) tripped while walking ancestors. Indicates data corruption; not expected in practice.

## 4. Architecture

```
opc revisit TASK-X
   │
   ▼
POST /tasks/TASK-X/revisit
   │
   ├─ daemon walks parent_task_id chain → root TASK-R
   ├─ validates root state ∈ {failed, blocked(escalated)}
   │   (else 409: cannot_revisit)
   └─ under db_lock, atomically:
       • write audit entry to TASK-R: action="revisit_requested",
         payload={flagged: TASK-X, cascade: [TASK-R, …, TASK-X],
                  prior_status: "failed" | "blocked",
                  prior_cancelled: bool,
                  founder_note: str | null}
       • update tasks SET status='pending', block_kind=NULL,
         cancelled_at=NULL, note='revisit requested for TASK-X'
         WHERE id=TASK-R
   │
   ├─ (outside db_lock) enqueue_task(state, TASK-R)
   │
   ▼
returns { root_task_id, cascade, prior_root_status }
   │
   ▼
CLI streams TASK-R's events via existing SSE pipe
```

### 4.1 State Semantics

- **TASK-R (root)** — mutated in-place. Status flips `failed → pending → in_progress → … → (terminal again)`. If the root was cancelled (`cancelled_at != NULL`), `cancelled_at` is cleared to `NULL` — otherwise `run_step`'s cancellation short-circuit (`run_step.py:40-42`) would silently drop the re-enqueued task. Audit log accumulates: original attempt's session_starts/ends, then `revisit_requested`, then attempt-2 session_starts/ends. A single coherent task row with multiple lifecycles.

- **TASK-X and all frozen descendants** — never mutated by revisit. Their FAILED (or COMPLETED) status + notes — including any `cancelled_at` timestamps from a prior `opc cancel` — remain visible via `opc details` for the EH to inspect during its decision step. They stay parented to the same root. (Descendants stay cancelled; only the root is un-cancelled so it can re-enter the orchestration loop.)

- **New work the EH delegates post-revisit** — becomes brand-new child tasks under TASK-R (new IDs). Sibling-of-spirit to the frozen ones, same `parent_task_id`.

### 4.2 Invariants Preserved

- Cascade-fail policy (2f5756e) still applies **within each attempt's sub-tree**. If attempt-2's child fails, TASK-R cascades back to `FAILED` — and the founder can `opc revisit` it again. Revisits are stackable; no special cascade handling needed.
- No retry *within* a lineage: the EH still never gets another decision step after a cascade-fail automatically. Revisit is the *only* path out of the dead-end, and it requires explicit founder action.
- Single source of truth for task state: everything readable from DB + audit log, no in-memory revisit state.

### 4.3 EH Prompt Injection ("Consumed" Rule)

The existing `run_step` builds an EH prompt per decision step. For revisit, we add a prompt-builder check:

> *Is there a `revisit_requested` audit entry for this task whose timestamp is newer than the latest `orchestration_step` audit entry for this task (or no `orchestration_step` exists since that revisit)?*

If yes, prepend a 4-line (or 5-line with founder note) header to the prompt:

```
REVISIT: founder flagged TASK-X for re-examination.
Cascade chain (root → flagged): TASK-R → TASK-A → TASK-B → TASK-X
Root prior status: failed   (or: failed-cancelled, blocked-escalated)
Founder note: <text>        (omitted if --note not provided)
Investigate via `opc details <id>` and `opc audit <id>` before deciding.
```

`failed-cancelled` signals that the prior attempt was aborted by `opc cancel`, not by its own failure. This matters because cancelled subtrees may have left incomplete work in external systems (half-written files, open PRs) — the EH should use `opc audit <id>` to read the `cancel_requested` audit entry and its `reason` field before deciding.

After the EH writes its first post-revisit `orchestration_step`, the latest-step timestamp exceeds the revisit entry → the header disappears on subsequent cycles. Purely timestamp-based; no explicit consumed flag, no new column.

The EH then investigates at whatever depth it deems fit (its workspace permission is `Bash(opc *)`, so `opc details`, `opc audit`, `opc tasks` all work inside its session) and outputs a normal decision (`delegate` / `done` / `escalate`). The downstream run_step path handles that decision identically to any other EH cycle.

## 5. Validation Rules

All validation happens server-side, inside `state.db_lock`, before the mutation:

| Root state                                   | Action                                                               |
| -------------------------------------------- | -------------------------------------------------------------------- |
| `failed` + `cancelled_at IS NULL`            | **Allow.** Clean revival case (cascade-failure or agent-reported failure). |
| `failed` + `cancelled_at IS NOT NULL`        | **Allow.** Founder-cancelled subtree; revisit un-cancels the root (descendants stay cancelled). |
| `blocked(escalated)`                         | **Allow.** Founder withdraws escalation and retries in its stead.    |
| `blocked(delegated)`                         | **Reject 409.** Siblings in-flight; revisit would race their terminal callbacks. Founder must `opc cancel <root>` first, then revisit. |
| `in_progress`                                | **Reject 409.** EH mid-step; mutating the row would corrupt the run. Cancel first if truly wedged. |
| `pending`                                    | **Reject 409.** Already queued; nothing to revisit.                  |
| `completed`                                  | **Reject 409.** No failure to revisit. Founder should use fresh `opc run`. |

**Ancestor walk:** follow `parent_task_id` until NULL. Hard-cap at 20 hops (defensive; real lineages are 2-4 deep). Overrun → 500 `lineage_too_deep`.

## 6. Atomic Mutation Order

Inside `async with state.db_lock`:

1. **Insert `revisit_requested` audit entry** *first*. If the subsequent update fails, we at least have a record of founder intent. Payload includes `prior_cancelled: bool` so post-hoc audit reads can distinguish "revived a cancelled root" from "revived a naturally-failed root" without re-reading the tasks row.
2. **UPDATE tasks** row: `status='pending'`, `block_kind=NULL`, `cancelled_at=NULL`, `note='revisit requested for TASK-X'`. **The `cancelled_at=NULL` clear is load-bearing** — `run_step` short-circuits on any non-NULL `cancelled_at` (see `run_step.py:40-42`), so failing to clear it would leave the queue event to be silently dropped, stranding the root in `pending` forever.

Outside the lock:

3. **enqueue_task(state, root_id)** — pushes onto the async queue. A lost enqueue would leave the root in `pending` forever; in practice this doesn't fail (in-memory `asyncio.Queue.put_nowait`), but a follow-up `opc revisit` on the same root would just add a second audit entry + re-enqueue, which is benign.

## 7. Edge Cases

1. **Flagged task IS the root.** Cascade is `[root]` (single element). Header reads `Cascade chain (root → flagged): TASK-R → TASK-R`. EH proceeds normally.
2. **Revisit with no `--note`.** `founder_note` is `null` in payload; header omits the 4th line.
3. **Daemon restart between `revisit_requested` insert and next EH step.** Audit entry persists in SQLite; on cold-read, prompt builder still finds it and injects. No transient state loss.
4. **Revisit → revisit.** Attempt-2 cascade-fails again; founder revisits same root again. Audit log now has two `revisit_requested` entries. Prompt builder uses the *latest* one for the header. All prior entries remain visible via `opc audit`.
5. **Revisit a task whose root was successfully revisited and completed.** Root status is `completed` → 409. Founder should use `opc run` with a fresh brief.
6. **Concurrent revisits on the same root.** Both land inside `db_lock` sequentially. First succeeds (root `failed → pending`). Second sees `pending` → 409. No race.
7. **Cancel → revisit.** Founder cancels a wedged in-flight root via `opc cancel`, which flips it to `failed + cancelled_at != NULL + block_kind=None` (per `routes/tasks.py` cancel endpoint). Revisit accepts this state, clears `cancelled_at`, emits the `failed-cancelled` prior-status header to the EH, and enqueues. Descendants remain cancelled — they are historical record, not work to resume.
8. **Cancel → revisit → new cancel.** After revisit, the root runs again. If the founder cancels this second attempt, `cancelled_at` gets set again, `run_step` starts short-circuiting, and the root becomes revisit-eligible again via the same code path. Symmetric with edge case 4 (revisit → revisit).

## 8. Test Plan

### Unit tests (in-process, `tests/`)

1. `test_revisit_walks_cascade_to_root` — leaf → chain → root resolution.
2. `test_revisit_flips_failed_root` — root `failed` (not cancelled) → `pending`, audit entry written, queue gains root, `cancelled_at` stays `NULL`.
3. `test_revisit_flips_escalated_root` — root `blocked(escalated)` → `pending`, `block_kind` cleared.
4. `test_revisit_clears_cancelled_at_on_cancelled_root` — root `failed + cancelled_at != NULL` → `pending`, `cancelled_at` is `NULL`, `prior_cancelled=True` in audit payload, header shows `failed-cancelled`. **Also** verifies `run_step` actually picks up the re-enqueued task (regression guard against the short-circuit bug).
5. `test_revisit_rejects_ineligible_states` — parameterized over `in_progress`, `pending`, `completed`, `blocked(delegated)` → 409 with structured reason.
6. `test_revisit_missing_task` — 404.
7. `test_revisit_prompt_injection_on_first_step` — `revisit_requested` present, no later `orchestration_step` → EH prompt contains the 4-line header.
8. `test_revisit_prompt_no_reinjection_after_first_step` — after one `orchestration_step` lands, header disappears from next EH prompt.
9. `test_revisit_founder_note_in_header` — `--note` round-trips to the 5th prompt line.
10. `test_revisit_cascade_fail_still_applies_in_second_attempt` — revived attempt's child fails → cascade still cascades root back to `FAILED`. Second `revisit_requested` entry can then trigger another revival.
11. `test_revisit_descendants_unchanged` — frozen tasks' `status`, `note`, `block_kind`, `cancelled_at` unmodified after revisit (descendants of a cancelled root stay cancelled).
12. `test_revisit_flagged_task_is_root` — `TASK-R` itself; cascade single-element.
13. `test_revisit_lineage_too_deep` — fabricate a 21-hop chain; expect 500.

### Integration test (`tests/integration/`)

14. `test_revisit_roundtrip_completes` — submit task → fake EH delegates to dev_agent → dev_agent fails → cascade-fail fires → `opc revisit <child>` → fake EH (reads `revisit_requested` via a plan-env flag) returns `{"action": "done"}` → root reaches `completed`. Full end-to-end through the real daemon, fake Claude binary, and SSE stream.

## 9. Implementation Scope (estimate)

| File                                       | Change                                                      | ~LOC |
| ------------------------------------------ | ----------------------------------------------------------- | ---- |
| `src/infrastructure/database.py`           | Add `walk_ancestors(task_id) -> list[TaskRecord]` helper    | 15   |
| `src/daemon/routes/tasks.py`               | Add `POST /tasks/{id}/revisit` endpoint                     | 45   |
| `src/orchestrator/run_step.py` (or helper) | Add revisit-header injection in EH prompt builder           | 35   |
| `src/cli.py`                               | Add `cmd_revisit` + argparse wiring                         | 30   |
| `tests/test_database.py`                   | Unit tests for `walk_ancestors`                             | 20   |
| `tests/daemon/test_routes_tasks.py` (new)  | Unit tests for revisit endpoint                             | 140  |
| `tests/test_run_step.py`                   | Prompt injection + re-injection + cascade-still-applies     | 80   |
| `tests/integration/test_end_to_end.py`     | Revisit roundtrip                                           | 60   |
| `CLAUDE.md`                                | Revisit in CLI list + state semantics note                  | 10   |
| `skills/opc/SKILL.md`                      | Add revisit to the Tasks section + safety rules             | 10   |
| `README.md`                                | Add revisit to user-facing command reference                | 5    |

**Total:** ~450 LOC incl. tests and docs.

## 10. Rollout

Single PR. Feature is opt-in by nature (founder-initiated), no migration required, no existing audit log entries need rewriting. Merge order:

1. Database helper + its unit tests.
2. Endpoint + its unit tests.
3. Prompt injection + its unit tests.
4. CLI subcommand.
5. Integration test.
6. Docs.

Each step runnable in isolation; failures at step N don't block step N-1 work already shipped to main. Prefer one atomic commit if the integration test passes on first push; otherwise split into "revisit: DB + endpoint" and "revisit: CLI + prompt + integration" commits for bisectability.
