# Thread close-out removal + resume — Design

## Status
Draft — 2026-06-01

## Problem

Today, archiving a thread runs a two-phase ritual:

1. **Phase A** — `POST /threads/{id}/archive` flips the thread to `archiving`, mints a `close_out` invocation token for every participant, and writes a `kind_tag="archive_requested"` system message.
2. **Phase B** — a background finalizer (`src/daemon/thread_archive_finalizer.py`) waits up to `close_out_wait_seconds` (default 300) for every close-out callback to land. Each callback (`POST /threads/{id}/close-out`) accepts `learnings[]` (appended to the agent's `learnings.md`) and `kb_slugs[]` (set-unioned into `threads.new_kb_slugs_json`). When all close-outs land or the timer expires, the finalizer flips status to `archived`, writes the transcript file, and emits a second `kind_tag="archived"` system message.

The ritual was designed as a forcing function (extract learnings + attribute KB entries to the thread at archive time, symmetric with `opc talk end`). In practice the founder rarely needs the per-thread rollup — agents already create KB entries and learnings via `happyranch kb add` and `happyranch learning add` at any time during the conversation. The close-out callback duplicates that pathway with a forced terminal turn that costs invocation budget for no behavioural change.

Additionally, an archived thread is currently a one-way terminal. The founder cannot re-open a thread they archived prematurely; they must compose a new one.

## Goals

- **Remove** the close-out ritual entirely (route, runner branch, finalizer module, enum value, CLI subcommand, web UI surfaces, DB columns).
- **Collapse** archive into a synchronous state transition: `open → archived` happens inside the POST handler with no transitional `archiving` state and no background wait.
- **Collapse** the historical `abandoned` state into `archived` — without close-outs there is no behavioural difference between "force-close" and "archive", and the founder's intent (reason / summary) is now carried as free text on the single `archived` event.
- **Add** a `POST /threads/{id}/resume` route + CLI + web UI surface that flips `archived → open`, so archive becomes purely an organisational state rather than a terminal.
- **Sweep** the local SQLite DBs directly (per the founder's explicit instruction) to clean up legacy `archiving` rows, `kind_tag="archive_requested"` system messages, and dropped columns. No runtime migration code.

## Non-goals

- Multi-org runtime migration tooling. The founder operates a single local runtime container and explicitly wants a direct sweep, not a portable migration script.
- Preserving the historical `new_kb_slugs_json` / `new_learnings_total` rollups. Source of truth (KB entries, `learnings.md`) is untouched; the per-thread denormalisation goes away.
- Renaming the `archived_at` column or `archived` status value. Both retain their current names; only their semantics widen ("terminal" → "currently archived, may be resumed").

## Design

### 1. Archive becomes synchronous

`POST /api/v1/orgs/{slug}/threads/{thread_id}/archive` is rewritten:

```python
class ArchiveBody(BaseModel):
    summary: str = ""
```

The body shrinks to a single optional `summary` field. `request_close_outs` is removed (no behaviour to gate).

Handler flow:

1. 404 if thread not found.
2. 400 if status is already `archived` → return `{thread_id, status: "archived", transcript_path, idempotent: True}`.
3. Under `db_lock`:
   1. Reap pending `REPLY` and `BOOTSTRAP` invocations with `decline_reason="archive_started"` (unchanged).
   2. `UPDATE threads SET status='archived', summary=?, archived_at=?` (write `body.summary` directly — empty string overwrites prior summary on re-archive; no `COALESCE` because the field is always a string after the Pydantic default). The `archive_requested_at` column is dropped (see §4); there is no transitional state.
   3. Append a single system message `{kind_tag: "archived", summary}` and capture its `seq` (`sys_seq`). This message keeps the role of "visible chat-stream marker + SSE seq carrier"; its FK-target role is gone now that no close-out invocations point at it.
   4. Audit `thread_archived` with `{summary}` (replaces the historical `thread_archive_requested` action; see §4).
4. Outside the lock: write the transcript file synchronously via the existing `ThreadStore.write_transcript` helper. Empty `summary` ⇒ omit the summary block from the file header (preserving the original "make summary optional" intent).
5. Publish SSE event `{seq: sys_seq, kind: "system", preview: "archived", status: "archived"}`.
6. Return `200 {thread_id, status: "archived", transcript_path}`. (Was 202; no async work remains.)

Effect: every archive completes inside one HTTP request. The web UI no longer needs an "archiving" badge or progress poll.

### 2. Resume

New route `POST /api/v1/orgs/{slug}/threads/{thread_id}/resume`. Founder-only (bearer-token gated, same surface as `/archive`):

- 404 if not found.
- 400 if status is already `open` → idempotent return `{thread_id, status: "open", idempotent: True}`.
- 400 if status is anything else unexpected (defensive — only `archived` is reachable after §6).
- Under `db_lock`:
  - `UPDATE threads SET status='open'`. Leave `archived_at` and `summary` populated as historical record of the most recent archive.
  - Append system message `{kind_tag: "resumed"}` capturing its `seq` (`sys_seq`).
  - Audit `thread_resumed` with `{prior_archived_at}` (the timestamp that just got "expired").
- Publish SSE event `{seq: sys_seq, kind: "system", preview: "resumed", status: "open"}`.
- Return `{thread_id, status: "open"}`.

Participants stay in `thread_participants` (no row removal on archive, so no re-add on resume). On the next founder message via `POST /send`, the existing broadcast mint loop fans out fresh `REPLY` invocations to every participant — no special-case bootstrap.

### 3. CLI

- **Add** `happyranch threads resume --thread-id THR-NNN [--org SLUG]`. No request body — verb-only.
- **Remove** `happyranch threads close-out` subcommand and its `cmd_threads_close_out` handler.
- **Remove** `happyranch threads abandon` subcommand and its `cmd_threads_abandon` handler (collapsed into archive per Q1).
- **Modify** `happyranch threads archive --from-file <path>`:
  - The JSON payload schema becomes `{summary?: string}`. Today's payload `{summary, request_close_outs}` still parses (Pydantic silently drops unknown fields), but the field is documented as removed and CLI help text is updated.
- **Modify** `--status` filter help on `happyranch threads list`: `(open|archived)` (was `open|archiving|archived|abandoned`).

### 4. Database — direct sweep (one-shot, founder-executed)

Per the founder's explicit instruction in this brainstorm, schema cleanup is a direct SQL sweep against the local runtime DBs, not a runtime migration. The sweep script lives at `scripts/migrations/2026-06-01_drop_close_out_columns.sql` and the founder runs it once against each per-org DB under `<runtime>/orgs/<slug>/happyranch.db`.

Sweep contents:

```sql
-- 1. Collapse 'archiving' and 'abandoned' rows into 'archived'.
UPDATE threads SET status = 'archived',
    archived_at = COALESCE(archived_at, archive_requested_at, datetime('now'))
WHERE status IN ('archiving', 'abandoned');

-- 2. Drop historic close-out system messages so they don't render as orphans.
DELETE FROM thread_messages
WHERE kind = 'system'
  AND json_extract(system_payload, '$.kind_tag') = 'archive_requested';

-- 3. Drop historic 'archived' system messages that the finalizer wrote as a
--    duplicate of 'archive_requested' (Phase B emit). After this sweep the new
--    archive handler writes exactly one 'archived' system message per archive.
--    NOTE: this DELETE is best-effort dedup — if a thread was archived under
--    the old code path and has both messages, we keep the later one.
DELETE FROM thread_messages
WHERE id IN (
    SELECT m1.id
    FROM thread_messages m1
    JOIN thread_messages m2
      ON m1.thread_id = m2.thread_id
     AND m1.id < m2.id
     AND json_extract(m1.system_payload, '$.kind_tag') = 'archived'
     AND json_extract(m2.system_payload, '$.kind_tag') = 'archived'
);

-- 4. Drop dead invocations (CLOSE_OUT was the only consumer of these rows
--    beyond REPLY/BOOTSTRAP; any close-out row is dead weight now).
DELETE FROM thread_invocations WHERE purpose = 'close_out';

-- 5. Drop dropped-column fixtures from the threads table.
--    SQLite < 3.35 cannot drop columns directly. Recreate the table.
--    (Schema fragment shown in the implementation plan; not duplicated here.)
ALTER TABLE threads DROP COLUMN new_kb_slugs_json;     -- requires SQLite 3.35+
ALTER TABLE threads DROP COLUMN new_learnings_total;
ALTER TABLE threads DROP COLUMN archive_requested_at;
```

The Python schema (`src/infrastructure/database.py` line 314 region) is updated to match: the three columns are removed from the `CREATE TABLE threads (...)` statement so freshly-initialised per-org DBs never have them.

Audit rows for the historic `thread_archive_requested` and `thread_close_out_received` actions stay in `audit_log` as immutable history. The action strings simply stop being written. The audit reader UI must continue to accept them so old timelines render.

### 5. Code deletions

| Path | What goes |
|---|---|
| `src/daemon/thread_archive_finalizer.py` | Whole module |
| `src/daemon/state.py` | `close_out_wait_seconds` param + finalizer spawn machinery (lines 30-44) |
| `src/daemon/thread_runner.py` | `purpose == 'close_out'` branch (lines 61-62) + the `'close_out'` value in the purpose comment / type hint at line 110 |
| `src/daemon/routes/threads.py` | `close_out_thread_endpoint` (route handler + `CloseOutBody` / `CloseOutLearning` models) and the `_publish_thread_event` call inside it; `abandon_thread_endpoint` + `AbandonBody` |
| `src/cli.py` | `cmd_threads_close_out`, `cmd_threads_abandon`, their subparsers, the threads-list `--status` help update |
| `src/models.py` | `ThreadStatus.ARCHIVING`, `ThreadStatus.ABANDONED`, `ThreadInvocationPurpose.CLOSE_OUT`; `Thread.new_kb_slugs`, `Thread.new_learnings_total`, `Thread.archive_requested_at`; `CloseOutResult.new_learnings_count`, `CloseOutResult.new_kb_slugs` (or the entire `CloseOutResult` model if it's not used elsewhere) |
| `src/infrastructure/database.py` | `finalize_thread_archived`, `add_thread_kb_slug`, `add_thread_learnings_count`; column references in `_thread_row_to_model`, `_insert_thread`, `set_thread_status`; the three dropped columns from the CREATE TABLE statement |
| `src/infrastructure/audit_logger.py` | `log_thread_close_out_received` (kept for historic-row read compatibility? No — the writer is removed; the action string stays valid for historic rows). `log_thread_archive_requested` → renamed to `log_thread_archived` (and the action string flips). `log_thread_abandoned` is removed. |
| `src/orchestrator/org_config.py` | `threads_close_out_wait_seconds` config field |
| `examples/orgs/hk-macau-tourism/org/config.yaml` | `threads_close_out_wait_seconds:` key if present |

### 6. Web UI changes

| File | Change |
|---|---|
| `web/src/lib/api/types.ts` | Remove `Thread.new_kb_slugs`, `Thread.new_learnings_total`; remove `CloseOutResult` type if not referenced elsewhere; the `ThreadStatus` union becomes `'open' \| 'archived'`. |
| `web/src/lib/api/threads.ts` | Remove `closeOutThread` and `abandonThread` mirror functions; add `resumeThread`. Update OpenAPI mirror coverage. |
| `web/src/features/threads/ArchiveDialog.tsx` | Drop the `summary.trim()` empty-string guard; relabel field "Founder summary (optional, will be saved to transcript)"; remove the `request_close_outs` toggle if rendered; rephrase blurb to "Archive this thread (a summary will be saved to the transcript)." |
| `web/src/features/threads/ThreadsPage.tsx` | Remove the `case 'archive_requested':` branch in `describeSystem` (no new ones are written; historic rows were swept). Add `case 'resumed': return 'resumed';`. Remove the `archiving` and `abandoned` badge / filter chips. Add a "Resume thread" button visible when `status === 'archived'`. |
| `web/src/features/threads/ResumeButton.tsx` (new) | Confirm-dialog wrapper + mutation hook. |
| `web/src/mocks/threads.ts`, `web/src/design-system/providers/_mock-threads.ts` | Remove `new_kb_slugs` / `new_learnings_total` from mock rows; remove `'archiving'` / `'abandoned'` status rows. |
| `web/src/test/openapi-coverage.test.ts` | Add `POST /api/v1/orgs/{slug}/threads/{thread_id}/resume` to `INCLUDED_PATHS`. Remove the two close-out and abandon entries from `INCLUDED_PATHS`. |

### 7. Tests

- **Add** `tests/test_threads_resume.py` — happy path (archive → resume → next /send mints REPLY invocations to every participant), idempotency (`resume` on `open` thread), 404 on missing thread.
- **Add** `tests/test_threads_archive_sync.py` — archive returns 200 immediately, transcript file exists synchronously, no `archiving` state observable, summary optional.
- **Delete** every test in `tests/` that exercises close-out, abandon, or the archive finalizer. Catalogue them in the implementation plan; do not leave skipped tests.
- **Regenerate** the OpenAPI snapshot (`tests/contract/openapi.json`) via `HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py`.
- **Regenerate** the integration `fake_claude.sh` close-out path? No — fake_claude doesn't have a close-out path; integration tests that wrote close-outs went through the CLI shim which is now deleted.

### 8. Spec doc updates

- `docs/superpowers/specs/2026-05-13-threads-design.md` — mark §5.12 (close-out), §5.13 (abandon), the `archiving` state, and the close-out portions of §5.8 (turn cap), §5.9 (transcript), §5.10 (archive) as superseded by this design. Add an inline pointer to this spec.
- `docs/superpowers/specs/2026-05-30-thread-broadcast-only-design.md` — strip any close-out invocation references (the "non-conversational" carve-out goes away; everything is REPLY now).
- `protocol/skills/thread/SKILL.md` — drop the close-out operational mechanics; add a resume verb under founder actions.
- `CLAUDE.md` (project root) — the thread broadcast section's mention of close-out invocations needs trimming; the archived-state language updates from "terminal" to "currently archived (resumable)".

## Operating order

This is the suggested implementation order so each step is mergeable on its own and tests stay green between steps:

1. **Resume (additive)** — route, CLI, web UI, tests. No removals. Existing close-out flow still works. Green at every checkpoint.
2. **Synchronous archive** — collapse `open → archived`, drop the finalizer module, write transcript inside the handler. The `request_close_outs=false` path already exists today and exercises this code shape; flipping the default is a tiny step.
3. **Sweep + schema** — run the SQL sweep against the local runtime DBs; update the Python `CREATE TABLE` to match; drop the three columns from the model.
4. **Code deletions** — remove close-out route, runner branch, CLI subcommands, enum values, audit writer.
5. **Web UI cleanup** — remove close-out + abandon types/mocks/components; trim `ThreadsPage.tsx`.
6. **Doc updates** — spec, skill, CLAUDE.md.

## Risks & tradeoffs

- **Loss of per-thread KB attribution.** After the columns are dropped, the founder cannot ask "which KB slugs came out of THR-NNN?" The audit log retains the underlying `kb_entry_created` events, but no rollup. Acceptable per founder's call — the source of truth (KB entries themselves) is untouched.
- **Agents that have not yet learned the new shape may still attempt close-out callbacks via the old skill.** After §5, the route 404s. The `protocol/skills/thread/SKILL.md` update lands in the same change so newly-bootstrapped workspaces pick up the new doctrine immediately; existing workspaces inherit the new skill on their next agent.yaml refresh.
- **Resume invalidates the "terminal" assumption baked into some UI flows.** Audit: every `ThreadStatus.ARCHIVED` check in `src/` and `web/src/` needs review to confirm it still expresses the writer's intent. Where the check meant "stop accepting new replies", it stays. Where it meant "this thread is done forever", it gets re-evaluated (mostly indexing / sorting decisions — likely all keep their current behaviour).

## Open implementation questions

(None remaining — all four founder decisions captured.)

## References

- `2026-05-13-threads-design.md` — original threads design (parts now superseded)
- `2026-05-30-thread-broadcast-only-design.md` — broadcast addressing model (touched lightly here)
- `src/daemon/routes/threads.py` — archive + close-out + abandon routes
- `src/daemon/thread_archive_finalizer.py` — module to delete
- `src/daemon/thread_runner.py` — close-out branch to delete
