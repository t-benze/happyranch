# Thread Rename and Pinning (THR-209, Phase 1)

> Status: implemented
> Current Source: `docs/agent-guides/features-and-invariants.md` (Threads), `runtime/daemon/routes/threads.py` (rename + pin routes), `runtime/infrastructure/database.py` (pinned_at), `web/src/features/threads/ThreadsPage.tsx` (Pinned section + inline rename)
> Provenance: THR-209 (founder request + product spec), TASK-5618 / TASK-5621
> Date: 2026-08-25

## Summary

Phase 1 ships two **founder-only thread-organization controls**: inline
rename (edits the durable `subject`) and durable pin/unpin (founder-workspace
presentation state). Both change how a thread is found and displayed; neither
changes its identity, participants, routing, unread state, or lifecycle.

## Scope

- Founder-only mutations: `POST /api/v1/orgs/{slug}/threads/{thread_id}/rename`
  and `POST /api/v1/orgs/{slug}/threads/{thread_id}/pin`.
- Inline rename in the thread-detail header (prefilled, Save/Cancel,
  Enter-saves / Escape-cancels, trim, non-empty, ≤ 120 chars, duplicates
  allowed, last successful save wins, failure retains the typed value with an
  inline error and retry).
- Pin/unpin from the list row, the detail header, and a compact overflow
  menu; optimistic update with rollback and a visible error banner.
- Pinned threads form a **Pinned** section above the ordinary list on every
  qualifying founder-dashboard list/search/filter view; the active
  query/filter still governs inclusion; pinned ordering uses most recent
  existing thread activity; ordinary (unpinned) order is unchanged.
- Archived/closed threads may remain pinned and are shown only in views where
  they otherwise qualify (status bucket / search filter).
- Audit rows (`thread_renamed`, `thread_pinned`, `thread_unpinned`) record
  actor, timestamp, immutable thread id, and old/new values using the
  existing `audit_log.task_id` = THR-* scope convention. They never appear as
  thread messages.
- Accessibility: desktop/mobile controls are keyboard-accessible with clear
  accessible labels (`aria-label`, `aria-haspopup`/`aria-expanded` on the
  overflow, `role="alert"`/`aria-live` on errors, `aria-current` untouched).

## Explicitly out of scope (Phase 1)

- Agent-controlled rename/pinning.
- Participant-shared pins.
- Manual ordering of pins.
- Pin limits.
- Bulk actions.
- Notifications of any kind for rename/pin.

## Data model

- Rename persists to the existing `threads.subject` column (the durable
  title; `ThreadRecord.subject`). Identity (`id`, URL, participants,
  routing, unread, lifecycle) is immutable under rename.
- Pin state persists in a new **additive, nullable** `threads.pinned_at`
  column (`TEXT`; non-NULL = pinned). Additive-only migration follows the
  existing idempotent `ALTER TABLE ... ADD COLUMN` convention in
  `Database._create_tables` (try/except `sqlite3.OperationalError`), and the
  `CREATE TABLE IF NOT EXISTS` statement carries the column for fresh DBs.
  `_row_to_thread` guards on `"pinned_at" in keys` so pre-migration rows
  read NULL without error. No column semantics change; no overloaded-column
  usage; no v0/v1 surface touched.
- **Deletion cleanup:** there is no thread-deletion path in the product
  today. Because pin state lives *on the thread row* (`pinned_at`), deleting
  a thread row removes its pin state automatically — no separate cleanup
  surface is needed. If a future deletion path is added, no cascade work is
  required for pins.
- Wire: `GET /threads` and `GET /threads/{id}` rows carry `pinned` (bool,
  derived `pinned_at IS NOT NULL`), `pinned_at`, and `last_activity_at`
  (most recent message `created_at`, derived in the list SQL; used for the
  pinned-section activity ranking).

## Ordering semantics

`Database.list_threads` orders:

1. Pinned threads first (rank 0), then unpinned (rank 1).
2. Within pinned: most recent thread activity
   (`MAX(thread_messages.created_at)`, falling back to `started_at`).
3. Within unpinned: the **exact existing order** (open → `started_at DESC`;
   archived → `archived_at DESC`). The activity sort is conditional on
   `pinned_at` being set, so unpinned rows tie on it and fall through to the
   existing key — ordinary order is byte-for-byte unchanged when no pins
   exist (regression-tested).

The web list groups client-side by the `pinned` flag (a "Pinned" section
header) after the active status-bucket + search filter is applied; the "all"
bucket merge re-sorts pinned-first with the same activity rule so the
semantics hold there too.

## Mutation contract

- `POST .../rename` body `{"subject": str}` — trims surrounding whitespace;
  `422 empty_subject` when empty after trim; `422 subject_too_long` when
  > 120 chars; duplicates allowed (immutable id stays canonical); identical
  save is an idempotent success (no duplicate audit row); works on archived
  threads; 404 on unknown thread.
- `POST .../pin` body `{"pinned": bool}` — **strict** bool (`Field(strict=True)`:
  `"yes"`/`1` are rejected, not coerced); idempotent no-op when state
  already matches; works on archived threads; 404 on unknown thread.
- Both routes sit behind the existing `require_token()` dependency (master
  bearer = founder). Agents (invocation-token callers) are rejected — no
  permission-model generation changed.
- **Non-effects (regression-tested):** neither route appends a thread
  message, publishes a thread/SSE event, sends a notification, touches
  participants/unread, or changes `started_at`/`archived_at`/activity
  timestamps.

## Audit

New `AuditLogger` helpers following the existing thread-scope convention
(`audit_log.task_id` = THR-* id):

- `thread_renamed` payload `{old_subject, new_subject}`.
- `thread_pinned` / `thread_unpinned` payload `{pinned}`.

These are audit rows only — never thread messages. The web audit narrative
map (`web/src/features/audit/audit-narrative.ts`) and filter bucket map
(`audit-filters.ts`) render them ("renamed", "pinned", "unpinned" scope
sentences) to stay in parity with the single-writer audit logger.

## Web UI

- `ThreadHeader` gains a controlled inline rename mode (`renaming`,
  `renameDraft`, save/cancel/error props) — edit state lives in the page so
  the overflow-menu Rename item shares it.
- `InboxRow` gains an optional sibling `pinControl` slot (never nested inside
  the `<a>` — interactive-inside-interactive is invalid HTML).
- `useSetThreadPinned` (real provider) is optimistic: flips the cached list
  rows + detail row before the write, rolls back the exact snapshot on
  failure, and invalidates on settle. `useRenameThread` patches the detail
  cache and invalidates the list.
- List renders a "Pinned" section header when any filtered thread is pinned;
  rows carry per-row pin toggles; the detail header carries Rename +
  Pin/Unpin buttons and a ⋯ overflow menu (Rename / Pin·Unpin / Archive),
  all keyboard-accessible with accessible labels.

## Tests

- `tests/test_thread_rename_pin_db.py` — storage/migration/ordering.
- `tests/daemon/test_thread_rename_pin_routes.py` — routes/validation/auth/
  audit/non-effects.
- `web/src/features/threads/ThreadsPage.thr209.test.tsx` — UI behaviors
  (rename flow, pin optimistic/rollback, Pinned section, filter, archived
  eligibility, overflow accessibility).
- `web/src/design-system/providers/_real-threads.test.ts` — optimistic cache
  update + rollback at the provider level.
- `web/src/lib/api/threads.test.ts` — client mirror functions.
- `web/src/features/audit/audit-narrative.test.ts` — narrative coverage for
  the new actions.
- OpenAPI snapshot + `route-classification.json` regenerate for the two new
  founder-facing routes.

## Decisions / notes for reviewers

- Strict bool on the pin body: a fresh contract, so fail-closed typing
  prevents `"yes"`/`1` from silently pinning.
- `last_activity_at` is derived (never stored), keeping the schema additive
  and the "no activity-timestamp change" invariant provable (pin writes only
  `pinned_at`).
- No thread-message or SSE event is emitted for rename/pin; the thread-tail
  SSE consumers are deliberately untouched.
