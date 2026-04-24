# `revisit_of_task_id` — First-Class Revisit Link on the Tasks Table

**Status:** Design approved, pending implementation plan
**Author:** Founder + Claude Opus
**Date:** 2026-04-23
**Supersedes:** —
**Related:**
- `docs/superpowers/specs/2026-04-21-opc-revisit-design.md` — introduced revisit; chose audit-log-only linkage ("no schema migration"). This spec revisits that decision narrowly.

## 1. Problem

Today the link between a revisit's new root (TASK-N) and its predecessor root (TASK-P) lives only in the `audit_log` table:

- `revisit_of` entry on TASK-N, payload: `{predecessor_root, flagged, cascade, founder_note, prior_status}`
- `revisit_spawned` entry on TASK-P, payload: `{new_root}`

That design choice (v2 revisit spec, §2 Non-Goals) has three operational costs:

1. **Lookup is expensive.** "Is TASK-N a revisit, and of what?" needs an audit-log scan keyed by action and task_id.
2. **Reverse lookup is awkward.** "What are all revisits of TASK-P?" is either a second audit-log scan or a pair of JOINs, neither of which has an index built for it.
3. **Nothing in the task views surfaces the relationship.** `opc details`, `opc tasks`, and `opc recall` all treat TASK-N as if it had no lineage. The founder has to know to run `opc audit` to reconstruct the link.

We want the link to be first-class data on the `tasks` row AND visible wherever tasks are read, without regressing the v2 spec's attempt-isolation invariant (the reason the link was not a column to begin with).

## 2. Non-Goals

- **No change to `walk_ancestors` semantics.** It follows `parent_task_id` and only `parent_task_id`. The new column is NOT an ancestor edge. This is the load-bearing invariant that keeps cascade-fail (`run_step::_enqueue_parent_if_waiting`) from re-poisoning new roots on predecessor-child failures — see the v2 spec's revision history.
- **No rewrite of existing audit entries.** `revisit_of` and `revisit_spawned` stay exactly as today; they still carry the rich payload (cascade, flagged, founder_note, prior_status) that doesn't fit in a scalar column. The column is a materialized edge; audit remains the rich record.
- **No FK constraint.** Matches `parent_task_id`'s modeling in the current schema.
- **No generation number, no original-root column.** Stacked chains (P → N → N') are walked with the new helper when needed; short in practice.
- **No new CLI verb.** Visibility rides on existing `opc details` / `opc tasks` / `opc recall`.

## 3. Data Model

One nullable column on `tasks`:

```sql
ALTER TABLE tasks ADD COLUMN revisit_of_task_id TEXT;
CREATE INDEX IF NOT EXISTS idx_tasks_revisit_of ON tasks(revisit_of_task_id);
```

Semantics: points at the **predecessor root** task id (i.e. the task referenced as `predecessor_root` in the `revisit_of` audit payload). NULL for every task that was not created by the revisit endpoint. In a stacked chain P → N → N', `N.revisit_of_task_id = P`, `N'.revisit_of_task_id = N` — the immediate predecessor, never transitively collapsed to P.

### 3.1 Migration

Applied inside `Database._create_tables` alongside the existing idempotent migrations (see `database.py:149-178`). Two steps:

1. `ALTER TABLE tasks ADD COLUMN revisit_of_task_id TEXT` wrapped in `try/except sqlite3.OperationalError` so restart over an already-migrated DB is a no-op.
2. Backfill for any revisit rows created before this migration. Done in Python, not pure SQL, because SQLite's JSON1 extension is not guaranteed on the embedded build:
   ```python
   for entry in db.get_audit_logs_by_action("revisit_of"):
       predecessor_root = entry["payload"]["predecessor_root"]
       db._conn.execute(
           "UPDATE tasks SET revisit_of_task_id = ? "
           "WHERE id = ? AND revisit_of_task_id IS NULL",
           (predecessor_root, entry["task_id"]),
       )
   db._conn.commit()
   ```
   The `IS NULL` guard makes the backfill safely idempotent across restarts (no row is touched twice). Runs once per daemon startup; cost is O(number of historical revisits), which is bounded by founder action count and is tiny.

No backfill of `revisit_spawned` audit entries — they already exist for every historical revisit and are not being replaced.

### 3.2 `TaskRecord` model

Add one field:

```python
class TaskRecord(BaseModel):
    ...
    revisit_of_task_id: str | None = None
```

`insert_task`, `get_task`, `list_tasks`, `list_agent_tasks` all thread the column through. `update_task`'s allowed-fields set does **not** include `revisit_of_task_id` — the column is write-once at insert time, never updated.

## 4. Write Path

In `POST /tasks/{task_id}/revisit` (see `src/daemon/routes/tasks.py:319-403`), inside the existing `async with state.db_lock` block, the new root's `TaskRecord(...)` gets `revisit_of_task_id=predecessor.id`. Single-line addition. The two audit entries (`revisit_of` on TASK-N, `revisit_spawned` on TASK-P) remain.

No change to any other write path. Plain `opc run` inserts leave the column NULL.

## 5. Traversal

**`walk_ancestors` — unchanged.** Still follows `parent_task_id` only. Still used by `_enqueue_parent_if_waiting` and `revisit_task` itself to resolve the predecessor root. Regression test asserts the new column is not followed.

**New helper `walk_revisit_chain(task_id, max_hops=20) → list[TaskRecord]`.** Follows `revisit_of_task_id` backward. Returns `[task, predecessor, predecessor's predecessor, ...]`. For a non-revisit task returns `[task]` (single element). Raises `LineageTooDeep` on overrun (same pattern as `walk_ancestors`). Used for the `opc details` chain line and nothing else.

**New helper `get_direct_revisits(task_id) → list[str]`.** `SELECT id FROM tasks WHERE revisit_of_task_id = ? ORDER BY created_at`. Uses the new index. Returns direct revisits only; transitively later revisits (N' revisits N revisits P) do not appear in P's result unless explicitly walked.

## 6. Read Surfaces

### 6.1 `GET /tasks/{id}` and `GET /tasks`

Automatically gain `revisit_of_task_id` via the `TaskRecord` field. No explicit route-layer change beyond whatever serialization already goes through `TaskRecord.model_dump()` (or equivalent).

### 6.2 `GET /tasks/{id}/recall`

`get_recall_payload` adds `"revisit_of_task_id": task.revisit_of_task_id` to the returned dict. The payload stays flat.

### 6.3 `opc details TASK-N`

When `task.revisit_of_task_id` is set, prepend a two-line header before the existing output:

```
Revisit of: TASK-052  (predecessor: failed-cancelled)
Chain:      TASK-052 ← TASK-068 ← TASK-072 (this)
```

- `predecessor: <prior_status>` reads the `revisit_of` audit entry on TASK-N to recover the normalized status label (`failed` / `failed-cancelled` / `blocked-escalated` / `completed`). Falls back to the predecessor's live `status` if the audit entry is missing (defensive; shouldn't happen post-migration).
- `Chain:` built by `walk_revisit_chain(task_id)` (which returns `[task, predecessor, ..., original]`), then reversed before rendering so the `←` arrows read left-to-right as "created from" — original predecessor leftmost, current task rightmost. The `(this)` suffix marks the current task.

When `get_direct_revisits(task_id)` returns a non-empty list, append a footer line after the existing output:

```
Revisited as: TASK-091, TASK-103
```

Both blocks are omitted entirely when their source data is empty. No formatting change for non-revisit tasks that also have no revisits.

### 6.4 `opc tasks`

On rows whose `revisit_of_task_id` is set, append `↩ TASK-052` as a suffix on the existing row. No new column, no layout change. Rows without the column print unchanged.

### 6.5 No other surfaces

`opc tail`, `opc audit`, `opc agents`, etc. are unchanged. The data is reachable via `opc details` and the raw API for anyone who wants it.

## 7. Test Plan

Unit tests (`tests/`, in-process):

1. `test_migration_adds_column_idempotent` — fresh DB: column present; restart on an already-migrated DB: no error, no duplicate-column. Verify the index exists.
2. `test_migration_backfills_historical_revisits` — seed a `tasks` row for TASK-N with `revisit_of_task_id=NULL` and an `audit_log(action='revisit_of')` row with payload `{"predecessor_root": "TASK-P"}`. Run migration. Assert column populated. Run again; assert no extra writes.
3. `test_revisit_endpoint_writes_column` — full revisit flow: predecessor in `failed`, call `/tasks/{X}/revisit`, assert new root row has `revisit_of_task_id == predecessor.id`.
4. `test_plain_run_leaves_column_null` — `POST /tasks` via `opc run`: inserted row has `revisit_of_task_id IS NULL`.
5. **`test_walk_ancestors_does_not_follow_revisit_edge`** — construct P (root, parent_task_id=NULL) and N (root, parent_task_id=NULL, revisit_of_task_id=P). Assert `walk_ancestors(N) == [N]`. This is the regression guard for the attempt-isolation invariant. If this test ever breaks, cascade-fail will poison revisits.
6. `test_walk_revisit_chain` — stacked chain P → N → N': assert `walk_revisit_chain(N') == [N', N, P]`. Non-revisit task: `[task]`. Over-depth chain raises `LineageTooDeep`.
7. `test_get_direct_revisits_concurrent_case` — two revisits on the same predecessor (matches v2 spec §7 edge case 6): both ids appear, ordered by creation.
8. `test_details_output_with_revisit` — golden-string CLI test: header + chain line + footer when applicable; no noise when task has no revisit link; footer only when task has descendants.
9. `test_tasks_list_suffix` — `opc tasks` output contains `↩ TASK-052` on the revisit row and nothing on the plain row.
10. `test_recall_payload_includes_field` — `/recall` returns the new key with correct value (including NULL round-trip).

Integration tests are not required — the existing revisit roundtrip test (`tests/integration/test_end_to_end.py::test_revisit_roundtrip_completes`) will incidentally exercise the new column because every revisit now writes it. Extending that test to assert the column is cheap but not load-bearing.

## 8. Implementation Scope

| File                                       | Change                                                             | ~LOC |
| ------------------------------------------ | ------------------------------------------------------------------ | ---- |
| `src/infrastructure/database.py`           | Column + index + backfill + `walk_revisit_chain` + `get_direct_revisits` | 55   |
| `src/models.py`                            | `TaskRecord.revisit_of_task_id` field                              | 2    |
| `src/daemon/routes/tasks.py`               | Pass `revisit_of_task_id` in revisit endpoint; include in recall payload | 5    |
| `src/cli.py`                               | `opc details` header/chain/footer rendering; `opc tasks` suffix     | 50   |
| `tests/test_database.py`                   | Migration + backfill + traversal + reverse lookup tests            | 90   |
| `tests/daemon/test_routes_tasks.py`        | Revisit endpoint writes column; recall payload includes field     | 25   |
| `tests/test_cli.py`                        | Details + tasks-list rendering tests                               | 40   |
| `CLAUDE.md`                                | Update the "Revisit (founder recovery)" section to note the column | 5    |

**Total:** ~270 LOC incl. tests and docs.

## 9. Rollout

Single PR. No data migration from the user — the backfill runs automatically on daemon startup. No feature flag. No API breakage: new optional field in responses, new optional column in DB.

Merge order inside the PR:

1. Column + migration + backfill + model field + traversal helpers (with tests).
2. Revisit endpoint passes the column; recall payload exposes it (with tests).
3. CLI rendering — details header/chain/footer, tasks suffix (with tests).
4. Docs (CLAUDE.md update).

Each step is a coherent commit; step N is safe to ship without step N+1.

## 10. Open Questions

None. Answered during brainstorm:

- *Pointer only, vs. pointer + generation + root-of-chain?* Pointer only. Derived data stays derived.
- *Reuse `parent_task_id`?* No. Would break attempt isolation and re-introduce the v1 contamination bug.
- *Is this a schema migration and a visibility change, or separate?* Single design; they're trivially coupled (the column exists so the views can read it without scanning audit log).
