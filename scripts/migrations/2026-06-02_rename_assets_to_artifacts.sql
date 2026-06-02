-- One-shot sweep for the 2026-06-02 assets→artifacts + per-agent artifacts→output rename.
-- Run once per per-org SQLite DB at <runtime>/orgs/<slug>/happyranch.db.
-- Safe to re-run only after the column-rename has happened (the shell wrapper
-- probes for the OLD column and skips the SQL if it's already gone).
--
-- Stop the daemon before running: `scripts/daemon.sh stop`

BEGIN;

-- 1. Rename per-agent artifact-output columns on tasks + task_results.
--    SQLite 3.25+ supports RENAME COLUMN; macOS ships 3.39+.
ALTER TABLE tasks         RENAME COLUMN final_artifact_dir TO final_output_dir;
ALTER TABLE task_results  RENAME COLUMN artifact_dir       TO output_dir;

-- 2. Rewrite stored relative path strings that still start with 'artifacts/'
--    so they point at the renamed on-disk dir 'output/'. The shell sweep
--    will physically `mv` the workspace dir.
UPDATE tasks
   SET final_output_dir = 'output/' || substr(final_output_dir, length('artifacts/') + 1)
 WHERE final_output_dir LIKE 'artifacts/%';

UPDATE task_results
   SET output_dir = 'output/' || substr(output_dir, length('artifacts/') + 1)
 WHERE output_dir LIKE 'artifacts/%';

-- 3. Audit log: forward-only. No UPDATE on audit_log rows — old `asset_put`
--    actions and `asset:<name>` task_ids remain as historical record.

COMMIT;

-- Verification queries (read-only).
SELECT 'tasks columns (must include final_output_dir, NOT final_artifact_dir):' AS check_name;
SELECT name FROM pragma_table_info('tasks') WHERE name LIKE '%output%' OR name LIKE '%artifact%';

SELECT 'task_results columns (must include output_dir, NOT artifact_dir):' AS check_name;
SELECT name FROM pragma_table_info('task_results') WHERE name LIKE '%output%' OR name LIKE '%artifact%';

SELECT 'task_results.output_dir still starting with artifacts/ (should be 0):' AS check_name;
SELECT COUNT(*) FROM task_results WHERE output_dir LIKE 'artifacts/%';

SELECT 'tasks.final_output_dir still starting with artifacts/ (should be 0):' AS check_name;
SELECT COUNT(*) FROM tasks WHERE final_output_dir LIKE 'artifacts/%';
