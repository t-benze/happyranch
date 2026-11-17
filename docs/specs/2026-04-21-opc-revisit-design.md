# `opc revisit` — Founder-Initiated Task Revival

**Status:** Design approved (revised after adversarial review), pending implementation plan
**Author:** Founder + Claude Opus
**Date:** 2026-04-21
**Supersedes:** — (new feature)
**Related:**
- `fix(orchestrator): cascade-fail delegated chain + executor diagnostics` (2f5756e) — revisit is the founder-controlled recovery path out of the cascade-fail dead-end.
- `feat(daemon): opc cancel — SIGTERM + cascade + race-safe DB` (a4e7c1c) — cancel + revisit are the composable recovery primitives: cancel terminates in-flight work, revisit starts fresh with context from the terminated run.

## Revision History

- **v1 (2026-04-21):** Mutate-in-place design — revived the original root row and reused the existing child namespace.
- **v2 (2026-04-21, this revision):** Create-fresh-root design — predecessor root stays frozen; `opc revisit` inserts a brand-new root that references the predecessor via an audit entry. Motivated by adversarial review: the v1 approach re-parented new children alongside the prior attempt's `FAILED` children, so parent cascade-fail logic (`run_step::_enqueue_parent_if_waiting`) would re-poison the revived root on every completion check. v2 gets attempt isolation for free, resets the orchestration-step budget naturally (new row → `orchestration_step_count=0`), and is simpler to reason about.

## 1. Problem

The cascade-fail policy landed in 2f5756e makes any delegated child's failure collapse its entire ancestor chain to `FAILED`, with no in-lineage retry path. This is deliberate — it stopped the 6+ duplicate-retry spirals on TASK-033..038 and TASK-041..045 — but it leaves the founder without a recovery surface when a failure is actually worth re-examining.

Today, the only recovery path is to copy the original brief and resubmit as a new root task via `opc run --brief "..."`. That loses the audit lineage of the prior attempt: the Engineering Head for the new task cannot see what was tried, what partial progress was made, or which sub-tasks completed before the cascade fired.

We need a founder-initiated mechanism that lets the EH see the prior attempt's full lineage and decide — *autonomously* — whether to restart from scratch, reuse successful sub-task artifacts, or delegate different sub-tasks this time around.

## 2. Non-Goals

- **No in-place mutation of prior tasks.** Predecessor roots and their descendants are fully frozen. Revisit never touches their `status`, `note`, `cancelled_at`, `block_kind`, or `orchestration_step_count`. They are a read-only historical record.
- **No schema migration.** No `attempt_number` column, no `predecessor_task_id` column, no new tables. The predecessor ↔ revisit link is carried entirely in the `audit_log` table via `revisit_of` and `revisit_spawned` entries.
- **No UI beyond CLI.** The founder reads revisit history through the existing `opc audit` and `opc recall` commands.
- **No retry limit.** Revisits are stackable; if a revisited root also fails, it can itself be revisited, chaining predecessors without bound. Trusting the founder.
- **No auth model.** The revisit endpoint continues to sit behind the existing shared-bearer-token dependency. The "founder-only" property is enforced at the CLI layer via a TTY-gated confirmation prompt (see §3.1). This is an honor-system boundary, not a security boundary — consistent with the current daemon's trust model. An auth model can be added later when the runtime grows multi-principal callers.

## 3. User-Facing Interface

### 3.1 CLI

```
opc revisit <task-id> [--note "<founder hint>"]
```

- **`task-id`** — any task in a lineage whose root is in an eligible state (see §5). Can be a leaf, mid-tree, or the root itself. The flagged task's own status is not validated — only the root's. This is intentional: the founder may flag a `completed` sibling as context ("branch from here"), a `failed` leaf as the redo target, or the root itself. The flagged ID is passed to the EH in the prompt header; the EH decides what to do with it.
- **`--note`** — optional free-text hint appended to the `revisit_of` audit payload. Surfaces to the EH as `Founder note: <text>` in its first-step prompt header. Useful when the founder knows something the audit log does not (e.g. "PR #103 was merged manually out-of-band").

**TTY gate.** Before sending anything to the daemon, the CLI:

1. Verifies `sys.stdin.isatty() and sys.stdout.isatty()`. If either returns false, the command aborts with `opc revisit requires an interactive terminal (no --yes bypass)` and exits non-zero. This keeps agent sessions — whose `opc` invocations run in non-interactive subprocesses — out of this code path. Integration tests that exercise revisit go through the HTTP endpoint directly, not through the CLI.
2. Prints a confirmation block:
   ```
   About to revisit TASK-058 (founder-initiated).
   This creates a NEW root task that inherits the original brief.
   The existing lineage rooted at TASK-058 stays frozen (read-only history).
   The EH for the new root can inspect the old lineage via `opc details` / `opc audit` / `opc recall`.
   Continue? [y/N]
   ```
3. Reads a line from stdin. Only `y` / `yes` (case-insensitive) proceeds; anything else aborts without calling the daemon.

There is no `--yes` / `--force` flag. The gate is intentionally hard.

**After confirmation.** The CLI POSTs to the daemon, prints the new root's ID, and streams the new root's events via the existing SSE pipe.

**Example:**

```
$ opc revisit TASK-058 --note "PR #103 already merged manually"
About to revisit TASK-058 (founder-initiated).
This creates a NEW root task that inherits the original brief.
The existing lineage rooted at TASK-058 stays frozen (read-only history).
The EH for the new root can inspect the old lineage via `opc details` / `opc audit` / `opc recall`.
Continue? [y/N] y
Created TASK-072 (predecessor: TASK-052, flagged: TASK-058).
Submitted; streaming events (Ctrl-C to detach)...
[orchestration_step] {...}
[task_complete] {...}
```

**Safety tier:** matches `resolve-escalation` — lives in the opc skill's *"Confirm with user first"* category. The TTY gate is a secondary defence that does not replace the skill's soft-confirmation convention.

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
  "new_root_task_id": "TASK-072",
  "predecessor_root_task_id": "TASK-052",
  "flagged_task_id": "TASK-058",
  "cascade": ["TASK-052", "TASK-053", "TASK-058"],
  "predecessor_status": "failed"
}
```

`predecessor_status` is one of `"failed"`, `"failed-cancelled"`, `"blocked-escalated"`, or `"completed"` — matches the value that will appear in the EH's first-step prompt header.

**404** — unknown `task_id`.

**409 `cannot_revisit`** — predecessor root is in a non-terminal state. Body:
```json
{
  "detail": {
    "code": "cannot_revisit",
    "reason": "predecessor TASK-052 is in_progress",
    "predecessor_root_task_id": "TASK-052",
    "predecessor_status": "in_progress",
    "block_kind": null
  }
}
```

**500 `lineage_too_deep`** — safety bound (20 hops) tripped while walking ancestors. Indicates data corruption; not expected in practice.

## 4. Architecture

```
opc revisit TASK-X  (TTY-gated, confirmed)
   │
   ▼
POST /tasks/TASK-X/revisit
   │
   ├─ daemon walks parent_task_id chain → predecessor root TASK-P
   ├─ validates TASK-P state ∈ terminal-set (see §5)
   │   (else 409: cannot_revisit)
   │
   ├─ reads TASK-P.brief (original brief inherited by the new root)
   │
   └─ under db_lock, atomically:
       • insert tasks row TASK-N with:
           - id: TASK-N (next allocated)
           - brief: TASK-P.brief
           - task_type: TASK-P.task_type
           - parent_task_id: NULL (root)
           - status: 'pending'
           - orchestration_step_count: 0
           - cancelled_at: NULL
       • insert audit entry on TASK-N: action='revisit_of',
         payload={predecessor_root: TASK-P,
                  flagged: TASK-X,
                  cascade: [TASK-P, …, TASK-X],
                  predecessor_status: 'failed'|'failed-cancelled'|'blocked-escalated'|'completed',
                  founder_note: str|null}
       • insert audit entry on TASK-P: action='revisit_spawned',
         payload={new_root: TASK-N}
   │
   ├─ (outside db_lock) enqueue_task(state, TASK-N)
   │
   ▼
returns { new_root_task_id: TASK-N, predecessor_root_task_id: TASK-P,
          flagged_task_id: TASK-X, cascade, predecessor_status }
   │
   ▼
CLI streams TASK-N's events via existing SSE pipe
```

### 4.1 State Semantics

- **TASK-P (predecessor root) and all its descendants** — never mutated by revisit. Their `status`, `note`, `block_kind`, `cancelled_at`, `orchestration_step_count`, and child relationships remain exactly as they were. The only change on TASK-P's lineage is a new `revisit_spawned` audit entry recorded against TASK-P itself, which is purely observational.

- **TASK-N (new root)** — a first-class, fresh task row. `parent_task_id` is `NULL`. `orchestration_step_count` starts at `0`, so the EH gets a full `max_orchestration_steps` budget. `cancelled_at` starts `NULL`. `run_step`'s normal flow handles it identically to any `opc run` submission, with one exception: the prompt builder injects a predecessor-context header on the very first orchestration step (see §4.3).

- **New work the EH delegates post-revisit** — becomes brand-new child tasks under **TASK-N** (not TASK-P). The child namespaces are fully disjoint. If the EH chooses to reuse outputs from a successful TASK-P descendant, it does so by referencing the artifact directory (`artifacts/<old-id>/…`) in the new child's brief — not by re-parenting the old task row.

### 4.2 Invariants Preserved

- **Attempt isolation.** Because each revisit creates a new root with its own child namespace, the cascade-fail logic (`run_step::_enqueue_parent_if_waiting`) never sees descendants from prior attempts when evaluating TASK-N's child set. This cleanly resolves the v1 design's contamination risk.
- **Fresh budget.** TASK-N starts at `orchestration_step_count=0`. The `max_orchestration_steps` escalation path (`run_step.py:59-71`) cannot trip on residual counters from the predecessor.
- **Cascade-fail still applies within an attempt's sub-tree.** If TASK-N's own delegated child fails, TASK-N cascade-fails to `FAILED` — and the founder can `opc revisit` it again, creating TASK-N', which references TASK-N as predecessor. Revisit chains are singly-linked through `revisit_of` audit entries.
- **Single source of truth.** The predecessor ↔ revisit link lives in the audit log. Reconstructing the chain is a simple audit-log scan; no JOINs, no extra columns.

### 4.3 EH Prompt Injection

The existing `run_step` builds an EH prompt per decision step. For a revisited root, on its very first orchestration step, the prompt builder prepends a context header.

**Trigger condition** (evaluated at prompt-build time):

> *Does the current task have a `revisit_of` audit entry AND no prior `orchestration_step` audit entry?*

This is a one-shot header — the absence of any `orchestration_step` uniquely identifies "first step" and is cheaper than timestamp comparison. On step 2+, the `orchestration_step` count is non-zero, so the header disappears.

**Header shape (5–6 lines):**

```
REVISIT CONTEXT: this root is a revisit of TASK-P (which ended in <prior-status>).
Founder flagged TASK-X in the predecessor lineage — start your investigation there.
Cascade chain (predecessor root → flagged): TASK-P → TASK-A → TASK-X
Founder note: <text>                              (omitted if --note not provided)
Inspect via: `opc details TASK-P`, `opc audit TASK-P`, `opc recall TASK-P`.
You may reuse successful sub-tasks' artifacts (referenced by path in new child briefs); old child task rows stay frozen.
```

**`<prior-status>` values:**

| Value                 | Meaning                                                                          |
| --------------------- | -------------------------------------------------------------------------------- |
| `failed`              | Prior root failed naturally (agent-reported or cascade-fail).                    |
| `failed-cancelled`    | Prior root was terminated by `opc cancel`. Some subtree work may be incomplete. |
| `blocked-escalated`   | Prior root escalated to the founder; founder chose revisit instead of resolve.  |
| `completed`           | Prior root succeeded but founder wants a variation / redo of a specific leaf.   |

The EH then investigates at whatever depth it deems fit (its workspace permission is `Bash(opc *)`, so `opc details`, `opc audit`, `opc recall` all work inside its session) and outputs a normal decision (`delegate` / `done` / `escalate`). The downstream `run_step` path handles that decision identically to any other EH cycle.

## 5. Validation Rules

All validation happens server-side, inside `state.db_lock`, before the INSERTs. The check targets the **predecessor root** (TASK-P), not the flagged task (TASK-X):

| Predecessor root state                     | Action                                                               |
| ------------------------------------------ | -------------------------------------------------------------------- |
| `failed` + `cancelled_at IS NULL`          | **Allow.** Natural failure; predecessor history is settled.          |
| `failed` + `cancelled_at IS NOT NULL`      | **Allow.** Founder-cancelled subtree; predecessor history is settled (if incompletely so). |
| `blocked(escalated)`                       | **Allow.** Founder declining to resolve via `resolve-escalation` and starting fresh instead. |
| `completed`                                | **Allow.** Founder wants a variation on a succeeded run.             |
| `blocked(delegated)`                       | **Reject 409.** Predecessor's sub-tree is still running; its history isn't final. Founder must `opc cancel TASK-P` first, then revisit (which will then see it as `failed-cancelled`). |
| `in_progress`                              | **Reject 409.** EH mid-step on predecessor. Cancel first if truly wedged. |
| `pending`                                  | **Reject 409.** Predecessor hasn't started yet; no history to inherit. |

**Ancestor walk:** follow `parent_task_id` until `NULL`. Hard-cap at 20 hops (defensive; real lineages are 2-4 deep). Overrun → 500 `lineage_too_deep`.

## 6. Atomic Mutation Order

Inside `async with state.db_lock`:

1. **Insert new task row (TASK-N)** — a brand-new root task with `brief=<predecessor.brief>`, `task_type=<predecessor.task_type>`, `parent_task_id=NULL`, `status='pending'`, `orchestration_step_count=0`, `cancelled_at=NULL`.
2. **Insert `revisit_of` audit entry on TASK-N** — records the predecessor ID, flagged ID, cascade chain, normalized predecessor status, and founder note. This is what the prompt builder reads on TASK-N's first step.
3. **Insert `revisit_spawned` audit entry on TASK-P** — records that TASK-P spawned TASK-N. Purely observational; makes forward-traversal trivial when reading the predecessor's history.

If any of these three writes fails (mid-flight SQLite exception), none of them commit — they share the same DB transaction. The lock boundary plus single-connection serialization guarantee atomicity.

Outside the lock:

4. **enqueue_task(state, TASK-N)** — pushes onto the async queue. A lost enqueue would strand TASK-N in `pending`. In practice `asyncio.Queue.put_nowait` does not fail; if it ever did, the founder can detect the stranded task via `opc tasks` and issue a second `opc revisit` (which inserts *another* new root referencing TASK-P — benign duplicate).

## 7. Edge Cases

1. **Flagged task IS the predecessor root.** Cascade is `[TASK-P]` (single element). Header reads `Cascade chain: TASK-P → TASK-P`. EH proceeds normally.
2. **Revisit with no `--note`.** `founder_note` is `null` in payload; header omits the `Founder note:` line.
3. **Daemon restart between inserts and `enqueue_task`.** Both the task row and the audit entries are persisted to SQLite before the enqueue. On daemon start-up, the existing startup-recovery path (whatever re-enqueues `pending` tasks that have no in-memory queue entry) picks up TASK-N. If no such recovery path exists yet, the founder re-runs `opc revisit` — `revisit_of` audit history already tells the EH what happened.
4. **Revisit a revisit.** Say TASK-P → TASK-N (via revisit). TASK-N also fails. Founder runs `opc revisit TASK-N`. Predecessor walk lands on TASK-N (it's already a root). TASK-N becomes the predecessor; TASK-N' is the new root. Audit log on TASK-N gets a `revisit_spawned` entry; TASK-N' gets a `revisit_of(predecessor=TASK-N)` entry. Chains singly-linked forever.
5. **Revisit a completed root.** Allowed. Header says `prior-status: completed`. Useful for regenerating artifacts when the original outputs are stale.
6. **Concurrent revisits on the same predecessor.** Both land inside `db_lock` sequentially. First inserts TASK-N and its audit entries. Second finds TASK-P still terminal, inserts TASK-N' with its own audit entries. Both are legal; TASK-P's audit log ends up with two `revisit_spawned` entries pointing at different new roots. Slightly awkward but harmless; in practice the founder will `Ctrl-C` after the first.
7. **Cancel → revisit.** Founder cancels a wedged `blocked(delegated)` root via `opc cancel`, which flips TASK-P to `failed + cancelled_at != NULL` (per `routes/tasks.py` cancel endpoint). Revisit accepts this state as `failed-cancelled`, emits the `failed-cancelled` header, and creates TASK-N. TASK-P's descendants stay cancelled (history).
8. **Agent tries to run `opc revisit` from a worker session.** The worker's `opc` is non-interactive (stdin piped, stdout redirected). The CLI's `isatty()` check fails; the command aborts with `opc revisit requires an interactive terminal (no --yes bypass)` and never hits the daemon. This is the entire enforcement mechanism for founder-only; there is no server-side role check.
9. **`revisit_of` audit entry present on a task created by `opc run` (not by revisit).** Cannot happen: `revisit_of` is only written by the revisit endpoint, inside the same transaction as the task row's INSERT. No race window.

## 8. Test Plan

### Unit tests (in-process, `tests/`)

1. `test_revisit_walks_cascade_to_root` — leaf → chain → predecessor root resolution.
2. `test_revisit_creates_new_root_from_failed_predecessor` — predecessor `failed` → new root inserted with `parent_task_id=NULL`, `orchestration_step_count=0`, `cancelled_at=NULL`; `revisit_of` entry written on new root; `revisit_spawned` entry written on predecessor; new root on the queue.
3. `test_revisit_does_not_mutate_predecessor` — before/after snapshot of predecessor row: `status`, `note`, `block_kind`, `cancelled_at`, `orchestration_step_count`, `brief`, `task_type` all unchanged.
4. `test_revisit_inherits_brief_and_task_type` — new root's `brief` and `task_type` exactly match the predecessor's.
5. `test_revisit_handles_cancelled_predecessor` — predecessor `failed + cancelled_at != NULL` → new root created, audit entry records `predecessor_status='failed-cancelled'`.
6. `test_revisit_handles_escalated_predecessor` — predecessor `blocked(escalated)` → new root created; predecessor stays `blocked(escalated)` (revisit does not implicitly resolve the escalation).
7. `test_revisit_handles_completed_predecessor` — predecessor `completed` → new root created, audit entry records `predecessor_status='completed'`.
8. `test_revisit_rejects_ineligible_states` — parameterized over `in_progress`, `pending`, `blocked(delegated)` → 409 with structured reason.
9. `test_revisit_missing_task` — 404.
10. `test_revisit_flagged_task_is_root` — flagged ID = predecessor root ID; cascade single-element; new root still created.
11. `test_revisit_lineage_too_deep` — fabricate a 21-hop chain; expect 500.
12. `test_revisit_prompt_header_on_first_step` — new root has `revisit_of` entry, no `orchestration_step` entry → EH prompt contains the 5/6-line header with correct fields.
13. `test_revisit_prompt_header_absent_on_second_step` — after one `orchestration_step` audit entry lands on the new root, header disappears.
14. `test_revisit_founder_note_in_header` — `--note` round-trips into the `Founder note:` line.
15. `test_revisit_chain_of_chains` — revisit a revisit: TASK-P → TASK-N → TASK-N'. Each `revisit_of` points at the immediately-prior root; audit-log walk reconstructs the full chain.
16. `test_revisit_concurrent_on_same_predecessor` — two overlapping POSTs against the same predecessor both succeed; TASK-P has two `revisit_spawned` entries, pointing at two distinct new roots.
17. `test_revisit_cli_rejects_non_tty` — CLI invoked with stdin piped exits non-zero with the TTY error before any HTTP call fires. (Asserts no daemon interaction.)

### Integration test (`tests/integration/`)

18. `test_revisit_roundtrip_completes` — submit task → fake EH delegates to dev_agent → dev_agent fails → cascade-fail fires → POST to `/tasks/<child>/revisit` directly (bypassing CLI, since integration tests run non-interactively) → fake EH sees the `revisit_of` audit entry via a plan-env flag and returns `{"action": "done"}` on the new root → new root reaches `completed`, predecessor stays `failed`. Full end-to-end through the real daemon, fake Claude binary, and SSE stream.

## 9. Implementation Scope (estimate)

| File                                       | Change                                                           | ~LOC |
| ------------------------------------------ | ---------------------------------------------------------------- | ---- |
| `src/infrastructure/database.py`           | Add `walk_ancestors(task_id) -> list[TaskRecord]` helper         | 15   |
| `src/daemon/routes/tasks.py`               | Add `POST /tasks/{id}/revisit` endpoint (predecessor walk + INSERT new task + 2 audit entries) | 60   |
| `src/orchestrator/run_step.py` (or helper) | First-step header injection when `revisit_of` present            | 40   |
| `src/cli.py`                               | Add `cmd_revisit` + TTY gate + confirmation prompt + argparse    | 45   |
| `tests/test_database.py`                   | Unit tests for `walk_ancestors`                                  | 20   |
| `tests/daemon/test_routes_tasks.py` (new)  | Unit tests for revisit endpoint                                  | 180  |
| `tests/test_run_step.py`                   | First-step header injection + second-step absence                | 60   |
| `tests/test_cli.py` (extend)               | TTY rejection + confirmation prompt acceptance/rejection         | 40   |
| `tests/integration/test_end_to_end.py`     | Revisit roundtrip                                                | 70   |
| `CLAUDE.md`                                | Revisit in CLI list + predecessor-link note                      | 12   |
| `skills/opc/SKILL.md`                      | Add revisit to the Tasks section + TTY-gate note                 | 12   |
| `README.md`                                | Add revisit to user-facing command reference                     | 5    |

**Total:** ~560 LOC incl. tests and docs.

## 10. Rollout

Single PR. Feature is opt-in by nature (founder-initiated, TTY-gated), no migration required, no existing audit log entries need rewriting. Merge order:

1. Database helper + its unit tests.
2. Endpoint + its unit tests.
3. First-step header injection + its unit tests.
4. CLI subcommand (TTY gate + confirmation prompt) + its unit tests.
5. Integration test.
6. Docs.

Each step runnable in isolation; failures at step N don't block step N-1 work already shipped to main. Prefer one atomic commit if the integration test passes on first push; otherwise split into "revisit: DB + endpoint" and "revisit: CLI + prompt + integration" commits for bisectability.
