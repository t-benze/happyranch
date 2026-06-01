-- One-shot sweep for the 2026-06-01 thread close-out removal.
-- Run once per per-org SQLite DB at <runtime>/orgs/<slug>/happyranch.db.
-- Safe to re-run (idempotent updates / deletes).
--
-- Stop the daemon before running: `scripts/daemon.sh stop`

BEGIN;

-- 1. Collapse 'archiving' and 'abandoned' rows into 'archived'. Preserve a
--    timestamp by falling back to archive_requested_at, then to now().
UPDATE threads SET status = 'archived',
    archived_at = COALESCE(archived_at, archive_requested_at, datetime('now'))
WHERE status IN ('archiving', 'abandoned');

-- 2. Drop the now-orphaned 'archive_requested' system marker. With archive
--    synchronous, only 'archived' system messages are emitted; legacy
--    'archive_requested' rows render as orphans in the UI/transcript.
DELETE FROM thread_messages
WHERE kind = 'system'
  AND json_extract(system_payload_json, '$.kind_tag') = 'archive_requested';

-- 3. Drop dead close_out invocation rows. The ThreadInvocationPurpose enum
--    no longer accepts 'close_out' — loaders would raise on these rows.
DELETE FROM thread_invocations WHERE purpose = 'close_out';

-- 4. Drop the three close-out-rollup columns. SQLite 3.35+ supports DROP COLUMN
--    directly; macOS ships 3.39+, so this is fine.
ALTER TABLE threads DROP COLUMN new_kb_slugs_json;
ALTER TABLE threads DROP COLUMN new_learnings_total;
ALTER TABLE threads DROP COLUMN archive_requested_at;

COMMIT;

-- Verification queries (read-only).
SELECT 'rows by status:' AS check_name;
SELECT status, COUNT(*) FROM threads GROUP BY status;

SELECT 'remaining archive_requested system messages (should be 0):' AS check_name;
SELECT COUNT(*) FROM thread_messages
WHERE kind = 'system'
  AND json_extract(system_payload_json, '$.kind_tag') = 'archive_requested';

SELECT 'remaining close_out invocations (should be 0):' AS check_name;
SELECT COUNT(*) FROM thread_invocations WHERE purpose = 'close_out';

SELECT 'threads table columns (no new_kb_slugs_json, new_learnings_total, archive_requested_at):' AS check_name;
SELECT name FROM pragma_table_info('threads') ORDER BY cid;
