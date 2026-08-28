# Thread Rename and Pinning (THR-209, Phase 1)

> Status: implemented (rev 3 — TASK-5987 fix-forward: optimistic open-list cache mirror + responsive 375px seam)
> Current Source: `docs/agent-guides/features-and-invariants.md` (Threads), `runtime/daemon/routes/threads.py` (rename + pin routes), `runtime/infrastructure/database.py` (pinned_at + list ordering), `web/src/features/threads/ThreadsPage.tsx` (Pinned section + inline rename), `web/src/design-system/providers/_real-threads.ts` (optimistic open-list reorder mirror)
> Provenance: THR-209 (founder request + product spec; messages 9–10 correction), TASK-5618 / TASK-5621 / TASK-5976 / TASK-5987
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
- Pinned threads form a **Pinned** section above the ordinary list on
  **open-thread views only**; the active query/filter still governs inclusion;
  within Pinned, ordering is **immutable numeric thread ID descending**
  (THR-10 above THR-2 — never lexicographic subject/display text, never
  activity); ordinary (unpinned) order is unchanged.
- **Archived/closed views have ZERO pin presentation** (THR-209 msg 9): one
  unified list with no Pinned/Unpinned split and no pin-based ranking — all
  qualifying rows use that view's ordinary existing ordering. A thread may
  retain its stored pin state while archived so restoring/reopening it
  recovers its prior pin behavior, but pin state has no visual or ordering
  effect in archived views.
- The **status-less backend query** (`GET /threads` with no `status`) and the
  web **"all" bucket** merge open AND archived threads, so they are **not**
  the open-thread list that message 10 limits pin-first behavior to; they are
  ordinary unified views (`started_at DESC`, the exact pre-THR-209 merged
  order) with no pin rank and no Pinned section — archived pin state can
  never leak into a mixed/archived presentation while open pinned semantics
  stay correct in the Open bucket (the only pin-qualifying view).
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
  (most recent message `created_at`, derived in the list SQL; informational
  only — pinned ranking uses the numeric thread ID, not activity).

## Ordering semantics

`Database.list_threads` orders (THR-209 msg 9 correction):

1. **`status='open'` (the only pin-qualifying view):** pinned threads first
   (rank 0), ordered by immutable **numeric thread ID descending**
   (`CAST(SUBSTR(t.id, 5) AS INTEGER)` — THR-10 above THR-2), then unpinned
   (rank 1) in the exact existing order (`started_at DESC`). The numeric key
   is conditional on `pinned_at` being set, so unpinned rows tie on it and
   fall through to the existing key — ordinary order is byte-for-byte
   unchanged when no pins exist (regression-tested). Thread activity never
   influences pinned rank.
2. **`status='archived'`:** ZERO pin presentation — `COALESCE(archived_at,
   started_at) DESC` only, no pin rank and no pinned/unpinned split.
3. **status-less (`GET /threads` no status):** ZERO pin presentation —
   `started_at DESC` (the pre-THR-209 mixed-query order); it is not the
   open-thread list, so pin-first ranking does not apply.

The web list renders the Pinned section header + pinned/unpinned split
**only in the Open bucket** (server returns pinned-first numeric-ID-desc;
the page groups preserving server order); the Archived and All buckets render
one flat ordinary list (All = the pre-THR-209 `started_at DESC` merge of
open + archived). Search/filter retains ordinary eligibility first, then the
open bucket's pinned-first + numeric-ID-desc ordering.

**Client/server parity (TASK-5987):** the optimistic path in
`web/src/design-system/providers/_real-threads.ts`
(`useSetThreadPinned.onMutate`) reorders the cached **open-list** variants
(`params.status === 'open'`) immediately under the SAME server rule via the
shared pure `reorderOpenThreads` mirror (pinned first, numeric thread ID
desc, unpinned `started_at DESC`), so pinning THR-10 while THR-2 is pinned
renders THR-10 above THR-2 and unpinning re-inserts the row into ordinary
started-at order BEFORE the response/refetch — no client/server semantic
divergence. Archived and status-less/all cached variants are NOT reordered
(pin has zero presentation effect there). Rollback restores the exact prior
snapshot including ordering.

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
- **Atomicity (one rollback-safe transaction per mutation).** Each mutation
  runs under the org `db_lock` as ONE transaction
  (`rename_thread_with_audit` / `set_thread_pinned_with_audit` in
  `runtime/infrastructure/database.py`): the authoritative old-value read,
  the idempotence decision, the thread `subject`/`pinned_at` update, and the
  corresponding audit row insert are one `BEGIN IMMEDIATE` … `commit` unit.
  Neither the storage helper nor the audit writer commits independently
  inside the unit (uncommitted write + `insert_audit_log_uncommitted`, then a
  single `commit()`); on ANY failure — including audit-insert failure — the
  transaction rolls back and the route returns an error with **no durable
  rename/pin transition and no stray audit row**. The whole unit holds the
  connection lock, so no other daemon thread can join the open transaction.
- **Concurrency.** Concurrent renames serialize on the org `db_lock`; the
  loser re-reads the durable subject inside its own transaction, so the
  outcome is last-successful-save-wins with a truthful sequential old→new
  audit chain (each row's `old_subject` == the previous row's `new_subject`;
  no stale pre-lock snapshots). Concurrent same-state pins yield exactly one
  audit row for the one durable transition (the loser is a true no-op);
  opposite-state overlaps re-read durable state inside each transaction, so
  neither request is misclassified and history matches exactly the durable
  transitions. True no-ops remain unaudited.
- **Non-effects (regression-tested):** neither route appends a thread
  message, publishes a thread/SSE event, sends a notification, touches
  participants/unread, or changes `started_at`/`archived_at`/activity
  timestamps.

## Audit

The atomic DB methods (`rename_thread_with_audit`,
`set_thread_pinned_with_audit`) write the rows following the existing
thread-scope convention (`audit_log.task_id` = THR-* id):

- `thread_renamed` payload `{old_subject, new_subject}`.
- `thread_pinned` / `thread_unpinned` payload `{pinned}`.

These are audit rows only — never thread messages. The web audit narrative
map (`web/src/features/audit/audit-narrative.ts`) and filter bucket map
(`audit-filters.ts`) render them ("renamed", "pinned", "unpinned" scope
sentences) to stay in parity with the audit row shapes.

## Web UI

- `ThreadHeader` gains a controlled inline rename mode (`renaming`,
  `renameDraft`, save/cancel/error props) — edit state lives in the page so
  the direct header Rename action shares it.
- `InboxRow` gains an optional sibling `pinControl` slot (never nested inside
  the `<a>` — interactive-inside-interactive is invalid HTML).
- `useSetThreadPinned` (real provider) is optimistic: flips the cached list
  rows + detail row before the write, reorders cached OPEN lists under the
  server rule (TASK-5987), rolls back the exact snapshot (state + order) on
  failure, and invalidates on settle. `useRenameThread` patches the detail
  cache and invalidates the list.
- Responsive seam (TASK-5987): the AppShell rail collapses from `w-rail`
  (244px) to the compact `w-rail-narrow` (56px) icon rail below `md`
  (768px) — nav labels stay in the a11y tree via `sr-only` — and the
  ThreadsPage header tabs+filter row wraps (`flex-wrap`) with the filter
  going full-width below `sm`, so the thread list is readable and actionable
  at 375x812 with no sliver/clip/overflow; OPEN and ARCHIVED buckets share
  the same responsive structure (Pinned section remains OPEN-only).
- List renders a "Pinned" section header only in the **Open** bucket when
  any qualifying (filtered) thread is pinned; Archived and All buckets are
  single flat lists (no section headers, no pin rank);
  rows carry per-row pin toggles; the detail header carries direct Rename,
  Pin/Unpin, Archive, and Mention routing buttons (plus Resume when archived),
  all keyboard-accessible with accessible labels.

## Tests

- `tests/test_thread_rename_pin_db.py` — storage/migration/ordering + atomic
  rename/pin-with-audit transactions (rollback on audit failure, uncommitted
  helpers never commit independently). Msg-9 ordering: open pinned numeric-ID
  desc (multi-digit THR-10-vs-THR-2 proves numeric not lexicographic),
  activity independence, unpinned order unchanged, archived + status-less
  queries ignore pin state entirely, empty list.
- `tests/daemon/test_thread_rename_pin_routes.py` — routes/validation/auth/
  audit/non-effects + deterministic audit-fault rollback tests and
  overlapping-request interleavings (rename truthful chain, same-state pin
  single audit, opposite-state pin truthful history) through the real
  `db_lock` + transaction path. Msg-9: wire ordering for open (numeric ID
  desc), archived (pin ignored, ordinary archived order), and status-less
  (pin ignored) lists.
- `web/src/features/threads/ThreadsPage.thr209.test.tsx` — UI behaviors
  (rename flow, pin optimistic/rollback, Pinned section, filter, archived
  eligibility, overflow accessibility). Msg-9: open numeric-ID-desc render
  order, activity never re-ranks, filtered pinned-first numeric order, no
  Pinned section in Archived/All buckets, empty/single, keyboard/accessible
  section + control semantics.
- `web/src/design-system/providers/_real-threads.test.ts` — optimistic cache
  update + rollback at the provider level; TASK-5987 adds the pure
  `reorderOpenThreads`/`numericThreadId` mirror tests and multi-row
  optimistic pin/unpin reorder (deferred response — order asserted BEFORE
  the response/refetch), archived/status-less cache variants untouched,
  rollback restoring pin state AND exact prior ordering, and success-time
  server reconciliation (invalidation).
- `web/src/features/threads/ThreadsPage.thr209.test.tsx` — page-level
  multi-row optimistic pin/unpin reorder assertions with a gated POST
  (order before response/refetch) and multi-row rollback order restoration.
- `web/src/features/threads/ThreadsPage.responsive.test.tsx` + `Sidebar.test.tsx`
  (TASK-5987) — responsive contract: rail collapse classes + sr-only labels
  keeping accessible names, header `flex-wrap` + full-width filter below
  `sm`, OPEN/ARCHIVED responsive equivalence, keyboard reachability.
  Real-browser 375x812 bounding-box + screenshot + accessibility evidence is
  captured at the repair head (Playwright harness).
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
  `pinned_at`). It is carried on the wire for informational/detail use but no
  longer ranks the pinned section (numeric thread ID desc does).
- No thread-message or SSE event is emitted for rename/pin; the thread-tail
  SSE consumers are deliberately untouched.
