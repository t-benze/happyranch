# Task-status model — Path B (stored source-of-truth) — THR-037 Change B

**Status:** Ratified (THR-037 seq 9 — founder: "all recommended; for protocol edits, please just include in the PR"). Implemented in Phase 1 (this PR).
**Date:** 2026-06-27
**Supersedes (vocabulary):** the `blocked`-discriminated model in
`docs/superpowers/specs/2026-04-19-task-status-redesign.md`.
**Authority doc (full design + ratification record):** `engineering_manager` artifact
`TASK-944/2026-06-27-thr037-task-status-pathB-stored-source-of-truth-design.md`.
**Ground truth:** `origin/main` @ `39efdcf` (post THR-033 Change A).

## 0. Summary

The pre-Path-B model surfaced a single `blocked` state discriminated by `block_kind`
(`delegated`/`escalated`/`blocked_on_job`). That overloaded "I'm waiting on my own
children/jobs" (healthy, in-progress) with "I need the founder" (genuinely stuck).
Path B stores what is actually true:

| Pre-Path-B (stored) | Path B (stored) | Carrier |
|---|---|---|
| `blocked` + `block_kind=delegated` | **`in_progress`** + `block_kind=delegated` | reason kept in `block_kind` |
| `blocked` + `block_kind=blocked_on_job` | **`in_progress`** + `block_kind=blocked_on_job` | reason kept in `block_kind` |
| `blocked` + `block_kind=escalated` | **`escalated`** (top-level status) | `block_kind` cleared |
| `failed` + `cancelled_at != NULL` | **`cancelled`** (new terminal) — new cancels only | `cancelled_at` still set |

**The load-bearing idea:** `in_progress` is now **two-valued**, discriminated by `block_kind`:

> `in_progress` + `block_kind IS NULL`  ⟺ a subprocess is running right now.
> `in_progress` + `block_kind IN (delegated, blocked_on_job)`  ⟺ parked, no subprocess,
> waiting on children/jobs it manages.

Every consumer that keyed off `status == BLOCKED` is re-expressed against the
`(status, block_kind)` pair. The discriminant is the **existing** `block_kind` column,
repurposed in place (no rename, no new column).

## A. `TaskStatus` + the three lockstep terminal predicates

`runtime/models.py`: `TaskStatus` gains `ESCALATED` (non-terminal) and `CANCELLED`
(terminal). `BLOCKED` and `BlockKind.ESCALATED` are kept as **deprecated** members for
the transition window + reverse migration (deleting an enum member strands a runtime: a
`StrEnum` built from a lingering `'blocked'` row raises `ValueError`). `block_kind`'s live
domain narrows to `{delegated, blocked_on_job}`.

Three predicates move in lockstep (guard test: `tests/test_models.py`):
- `TERMINAL_STATES` (`run_step.py`) — `+CANCELLED`. `ESCALATED` is **not** terminal.
- `_TERMINAL_TASK_STATUSES` (`routes/tasks.py`) — `+CANCELLED`.
- `_TERMINAL_STATUS_TO_EVENT` (`org_state.py`) — `CANCELLED → "task_failed"` (failure-class
  replay, `outcome="cancelled"`); `ESCALATED` in none. `_synthesize_terminal_event`'s
  blocked+escalated special-case becomes `status == ESCALATED`.

`get_nonterminal_task_ids` (`database.py`) returns `{pending, in_progress, escalated}` —
`blocked` dropped (no live row is `blocked` after boot migration), `escalated` added so the
restart sweep visits escalated rows to leave them alone; `cancelled` terminal → excluded.

## B. The restart-sweep landmine — `_sweep_on_startup` (`daemon/__main__.py`)

A parked parent is stored `in_progress` but runs **no subprocess**. The pre-Path-B sweep
did `if status == IN_PROGRESS: → FAILED`. Left unchanged, that force-fails every parked
parent and every blocked-on-job task on **every restart** (silent cascade corruption). The
`block_kind` discriminant saves it. Branches:

1. `in_progress + block_kind IS NULL` → FAILED + auto-revisit (genuinely running, killed).
2. `in_progress + block_kind=DELEGATED` → re-enqueue when all children terminal, else leave.
3. `in_progress + block_kind=BLOCKED_ON_JOB` → re-enqueue when all jobs terminal, else leave.
   **MUST exist** — without it these fall into Branch 1 and are wrongly failed.
4. `pending` → re-enqueue.
5. `escalated` → leave alone (founder owns).

## C. Scheduler / CAS

- **Entry gate** (`run_step_impl`): admit `pending`, `in_progress(delegated)` (children
  terminal), `in_progress(blocked_on_job)` (jobs terminal). `in_progress + block_kind IS
  NULL` falls to `else: skip` — a running subprocess must never be re-admitted (double-spawn).
- **`try_claim_for_step`** — **no SQL change**: the `(status, block_kind)` CAS already keys
  the parked pre-state and transitions to `in_progress(NULL)`. Verified by test.
- **Write sites:** `try_delegate` → `in_progress(delegated)`; self-block-on-job →
  `in_progress(blocked_on_job)`; `try_escalate` + `try_escalate_over_budget` →
  `escalated(NULL)` (with `cancelled` added to their CAS exclusion lists); `cancel_task` →
  `cancelled` + `cancelled_at`. `try_fail_over_budget` (Change A) unchanged.
- **Fan-in / resume / supersede** predicates re-expressed against `(status, block_kind)`.
- The non-cascading failure-recovery contract (TASK-573 / THR-028) and the Change-A
  `is_root` branches are preserved byte-for-byte.

## D. Migration

Idempotent live-row `UPDATE`, folded into the startup ALTER-ladder (`database.py`), no DDL
(neither column has a CHECK constraint):

```sql
UPDATE tasks SET status='escalated', block_kind=NULL  WHERE status='blocked' AND block_kind='escalated';
UPDATE tasks SET status='in_progress'                 WHERE status='blocked' AND block_kind='delegated';
UPDATE tasks SET status='in_progress'                 WHERE status='blocked' AND block_kind='blocked_on_job';
```

Historical terminal rows (`failed` + `cancelled_at`) are **left as-is**; only new cancels
write `status='cancelled'`. Derivations that must classify cancellation read `cancelled_at`
presence, not the status label (backward-compatible by construction).

### Reverse migration (forward-only posture; no supported online downgrade)

A downgrade to pre-Path-B code after the migration must first run:

```sql
UPDATE tasks SET status='blocked', block_kind='escalated' WHERE status='escalated';
UPDATE tasks SET status='blocked' WHERE status='in_progress' AND block_kind IN ('delegated','blocked_on_job');
UPDATE tasks SET status='failed' WHERE status='cancelled';   -- cancelled_at already set
```

The daemon is single-runtime; the ladder runs in `Database.__init__` before `create_app`,
so the table is never half-flipped while request handlers are live.

## E. resolved_superseded gating

- `_classify_predecessor_status` reads `cancelled_at` FIRST (so new `cancelled` and
  historical `failed+cancelled_at` both classify as `failed-cancelled`), then `escalated`
  (new top-level status). The 4-valued prior_status vocabulary is preserved.
- `_eligible_supersede_block_kind` keys `escalated` (status) and `in_progress(delegated)`
  (children terminal). `resolve_escalation` guard becomes `status != ESCALATED`.

## I. Dual-read transition window (Phase 1a) + phasing

Scheduler / sweep / fan-in / supersede predicates tolerate **both** `blocked+kind` and
`in_progress+kind` (and `escalated` ↔ legacy `blocked(escalated)`) as the same logical
state during the transition. In production the boot migration removes live `blocked` rows
before anything queries; the dual-read is belt-and-suspenders for in-memory records.

- **Phase 1 (this PR):** core model + migration + scheduler/sweep/CAS + protocol/doc parity.
- **Phase 2 (separate chain):** display/derivation — `StatusBadge.tsx`, `types.ts`, CLI
  status rendering, dashboard `compute_*` / `_SEVERITY_RANK`, derived escalated sub-label.
- **Phase 3 (after soak, founder-gated):** remove the deprecated `BLOCKED` /
  `BlockKind.ESCALATED` members + the Phase-1a dual-read tolerance.
