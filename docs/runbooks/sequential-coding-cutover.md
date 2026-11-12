# Sequential Coding cutover and backout — operator draft

**SOURCE ONLY: atomic public Coding cutover and old-fresh fence delivered.**
TASK-15624 attaches reviewed Coding v2 selection and unconditional old fresh
refusal together. Public Coding requires compatible installed storage, registered
reviewed v2, current authority/eligible bindings, mounted native engine and latest
Allowed NEW settings. Noncatalog V3 fixtures retain `check_venue_start`; a marker
never supplies Coding authority or bypasses release selection. Authenticated
admitted replay precedes fresh gates and preserves original pins. Source delivery
grants no live operation or genuine execution acceptance. See the
[U9 compatibility table and caller inventory](../superpowers/specs/2026-11-12-sequential-coding-u9.md).

## Install and setup sequence

1. Obtain separate authority naming actual runtime/org/source and cooperative
   maintenance window. Preserve original owners, settings/save keys, unknown
   effects, backups and native/job/preparation exclusions. Deployment, stop/restart,
   live installation/configuration/admission are separately authorized. Source
   review/merge or a health response supplies none of them. Agent environments
   refuse the new offline operator script; agents cannot self-authorize setup.
2. Preserve historical Coding initialization and PR1262 org/profile/startup
   ownership ordering. Its v1 builtin and old Explicit settings neither install
   nor enable the new sequential family. Never convert those settings.
3. Under the separately authorized offline procedure, use the explicit
   [sequential storage installer](sequential-workflow-storage.md), including
   complete readers, checkpoint/backup/native exclusions and uncertainty rules:

   ```sh
   python scripts/install_workflow_sequential_schema.py --runtime-root /absolute/runtime --org ORG --check
   python scripts/install_workflow_sequential_schema.py --runtime-root /absolute/runtime --org ORG --confirm-cooperative-offline --backup /absolute/backup/happyranch.db --backup-sha256 VERIFIED_SHA256
   ```

   Check exit3 means install-needed. Exit0 means compatible storage or observed
   installation, separate from registration/admission/loaded identity/acceptance.
   Exit1 refuses; exit4 preserves uncertainty. Never delete sidecars or reset
   ownership. Existing startup does not install the sequential extension.
   The installer now selects its own finite retained-history allowance (TASK-15719):
   8,388,608 total rows, 16GiB encoded cells including tags/rowids/schema,
   32MiB raw bytes per row, <=32GiB verified backup and one original 900s deadline.
   Every retained row is hashed; ownership is copied in SQL through file-backed
   temporary staging inside the same transaction. Default readers/venue/startup
   keep their existing budgets. Exhaustion refuses rather than sampling history.
   Neither `migrate_workflow_draft_schema.py` nor
   `migrate_workflow_submission_schema.py` is a prerequisite: the complete reader
   accepts F/E/G/H/I/J with the required complete v1 companion. On the isolated
   happyranch copy, F+Coding+owner indexes installs directly. The separate
   submission `--check` refusal `coding_authority_history_invalid` remains a
   recorded finding, unrepaired and unrelated to sequential installation.

4. Explicitly register ONLY the reviewed Coding v2 package resource:

   ```sh
   python scripts/setup_sequential_coding.py register --runtime-root /absolute/runtime --org ORG --confirm-cooperative-offline --backup /absolute/backup/happyranch.db --backup-sha256 VERIFIED_SHA256
   ```

   It appends only a missing definition. It creates no settings, admission,
   marker, task or run. An exact registered definition is a SELECT-only no-op;
   different bytes/source/ABI refuse. There is no resource-path/version override,
   old-release fallback or automatic registration. Keep the returned complete
   `definition` pin. Do not use direct SQL, old import, the disposable
   `setup_sequential_venue.py` publisher or marker creation as a public shortcut.
5. With separately authorized service ownership, Workflows → Setup at
   `/orgs/ORG/workflows/setup` saves repo-free defaults for that stored definition.
   The historical host setup links there but does not register/install sequential
   data. Explicit first Save starts Disabled; later Save preserves admission,
   allocating no work. Preserve original save key/body and use Check original save
   after uncertainty. The pointer at `/orgs/ORG/settings/coding` retains only OLD
   read-only save recovery; never translate/re-key/resend repo-bearing commands.
6. Separately Allow/Disable through the cooperative offline operator. Reestablish
   complete offline/native/job exclusions and take a fresh verified checkpointed
   backup matching the source after defaults were saved. Preserve a canonical
   UTF8 JSON command file (sorted keys, compact separators, no final newline),
   at most 16384 bytes, with exactly:

   ```json
   {"admission":"Allowed","definition":{"capabilities":"workflow-sequential-capabilities@1","compiler":"workflow-sequential-compiler@1","definition_digest":"a3b012d117c5905a1bac3c67a3158a8b1afa5a571acdba73c876ac7de24f790c","identity":"release/happyranch/coding","interpreter":"workflow-sequential-interpreter@1","source_digest":"5039e9eb60a03f8de110b40123a123d25962fbff48c39af5271a0666d679d065","validator":"workflow-sequential-validator@1","version":2},"expected_generation":1,"format":"workflow-sequential-coding-admission@1","operation_key":"original-admission-key"}
   ```

   Replace generation with the actual current generation and key with the new
   stable operation key. Use `Disabled` for the separate backout action. The
   command copies only that generation's NEW saved bindings/limits, advances
   generation under CAS and never changes admitted pins or constructs defaults.

   ```sh
   python scripts/setup_sequential_coding.py admission --runtime-root /absolute/runtime --org ORG --confirm-cooperative-offline --backup /absolute/backup/current.db --backup-sha256 VERIFIED_SHA256 --from-file /absolute/admission.json
   python scripts/setup_sequential_coding.py admission-show --runtime-root /absolute/runtime --org ORG --confirm-cooperative-offline --operation-key original-admission-key
   ```

   Stored provenance is `operator/sequential-coding`, separate from Founder
   Save/lookup; it is not a new executable principal or permission. Readback
   distinguishes the original command/receipt from latest generation/admission.
   Exact command replay is read-only, even after newer defaults/admission; changed
   key-body reuse conflicts. A read-only no-op/replay needs no backup. Absence or
   read failure supplies no resend, replacement key or success authority.
7. Verify loaded selector/engine identity separately from source/storage/settings.
   With separately authorized operating ownership, the
   genuine ordinary executable manager's own unchanged sequential `workflow_start`
   callback is the only public Coding initiation. No Founder/browser Start action.
   Original source host/outer settlement precedes first-visit release. Genuine
   Coding scratch remains separately reserved; V3 is not that scratch.

The setup script reuses existing org/lifecycle/checkpoint identity owners and one
BEGIN IMMEDIATE through COMMIT. It requires a registered runtime, absent daemon
PID/port controls and explicit operator-maintained exclusion of all current
writers/preparation/processes/native owners/jobs across affected runtimes,
shared stores and cooperative hosts. These observations do not prove historical
closure or OS exclusion. Unknown owners remain with their original owner.
It refuses missing/partial storage; it never installs or repairs. Full source
and target readers and scoped preservation/identity/backup/lifecycle checks share
one original 900-second cooperative deadline. All schema SQL and exact typed
rowid/cells in the three writable tables are preserved; an INSERT-only SQLite
authorizer prevents other table/schema/trigger effects. Full backup SHA256 is
streamed in 4MiB chunks up to32GiB; scoped backup equivalence covers all writable
rows and SQL, not equality of unrelated historical payloads. Complete readers
retain their workflow-data limits; only this operator selects 640M ownership SQL
steps instead of the unchanged 32M default. Ordinary history contributes no rows
or payload bytes to the preservation copy. Disable, original-key replay/readback
stay available in the sized ordinary-history envelope; invalid/exhausted workflow
history still refuses. Replay/readback require no backup/hash. See the explicit
[10x sizing arithmetic and caller inventory](../superpowers/specs/2026-11-12-sequential-coding-u9.md#retained-history-repair-task-15618).
These are cooperative limits, not hard OS-I/O or unbounded-history guarantees.

Setup exits: **0** observed publication/read; **1** refused; **2** usage;
**4** uncertain after COMMIT attempt, rollback/close/delivery or post-publication
failure. Keep exact command/key, stdout/stderr and original receipt; reconcile
before any explicitly authorized retry. No automatic resend. Success is not
public attachment, effective access, loaded identity or execution acceptance.

## Happyranch operator checklist (separate operating authority required)

Use compatible reviewed source and an ordinary operator shell, outside an agent
session. The commands below are an ordered maintenance procedure, never authority
for an agent to operate the live org. `RUNTIME` is the actual registered runtime
(e.g. `/home/benze/.happyranch/runtime`); use a new private backup directory outside
it. All paths passed to the scripts must be absolute. Preserve logs and original
operation custody throughout. No PR1252 action is part of this procedure.

1. Obtain the named stop/install/register/restart authority and maintenance
   window. Reconcile every original unsettled writer/preparation/native owner/job
   across registered runtimes, shared daemon stores and cooperative hosts.
   Exclude new publications, profile/move/start/restart throughout. Use the
   documented `scripts/daemon.sh stop --force` and `scripts/daemon.sh status`;
   positively establish stopped controls (no daemon PID/port) and native/job
   exclusion. A stopped daemon or missing PID alone does not settle unknown work.
2. Preserve the original DB and recovery files. Under the stopped-state custody,
   use SQLite's native writable open/recovery and `PRAGMA wal_checkpoint(TRUNCATE)`;
   close it, then create a separate native SQLite backup. Never delete a journal,
   WAL or SHM to satisfy the check. If nonempty recovery residue remains, hold
   this source and reconcile with its owner; a separately preserved checkpointed
   SQLite backup image can be assessed as a new explicit source, never silently
   substituted. Record `PRAGMA integrity_check` = `ok`,
   `PRAGMA foreign_key_check` = no rows, the backup's SHA256, source identity,
   all-table row counts and full typed content/rowid digests. No draft/submission
   migration is required. Do not run either migration as a workaround.

   After establishing those exclusions, create the pre-install backup without
   overwriting an existing recovery artifact (replace the absolute backup path):

   ```sh
   python - <<'PYTHON'
   import hashlib, sqlite3
   from pathlib import Path
   source = Path('/home/benze/.happyranch/runtime/orgs/happyranch/happyranch.db')
   backup = Path('/absolute/private-backup/pre-install.db')
   if backup.exists():
       raise RuntimeError('preserve existing backup; choose a new custody path')
   conn = sqlite3.connect(source.as_uri() + '?mode=rw', uri=True)
   try:
       assert conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0] == 0
       assert conn.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
       assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
   finally:
       conn.close()
   source_conn = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
   backup_conn = sqlite3.connect(backup)
   try:
       source_conn.backup(backup_conn)
   finally:
       backup_conn.close()
       source_conn.close()
   with backup.open('rb') as stream:
       print(hashlib.file_digest(stream, 'sha256').hexdigest())
   PYTHON
   ```
3. Run the following in order, retaining actual return codes (check exit 3 is expected
   for a first install; check exit 0 means already installed):

   ```sh
   python scripts/install_workflow_sequential_schema.py --runtime-root /home/benze/.happyranch/runtime --org happyranch --check
   python scripts/install_workflow_sequential_schema.py --runtime-root /home/benze/.happyranch/runtime --org happyranch --confirm-cooperative-offline --backup /absolute/private-backup/pre-install.db --backup-sha256 VERIFIED_SHA256
   python scripts/install_workflow_sequential_schema.py --runtime-root /home/benze/.happyranch/runtime --org happyranch --check
   ```

   Expected installation exit 0 and readback exit 0; refusal exit 1 or uncertainty exit 4 stops the
   sequence. Before registration, independently read with
   `inspect_sequential_install` / `read_sequential_history` in one native snapshot:
   require sequential=True, tasks count = ownership count, complete old/new
   validation and clean integrity/FKs. Compare every pre-existing table's typed
   row/rowid digest and all unchanged SQL. Ownership's schema changes to v2;
   every original mark and rowid stays identical. Keep these receipts.

   The following direct production readers provide that readback; retain its
   output, including every old-table count/digest. They have no admission effect.

   ```sh
   python - <<'PYTHON'
   import json, sqlite3, time
   from contextlib import closing
   from pathlib import Path
   from runtime.infrastructure.workflow_schema import (
       SequentialInstallAllowance, inspect_sequential_install, sequential_storage_inventory,
   )
   allowance = SequentialInstallAllowance(deadline=time.monotonic() + 900)
   def snapshot(path):
       with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as conn:
           conn.row_factory = sqlite3.Row
           conn.execute('PRAGMA foreign_keys=ON')
           conn.execute('BEGIN')
           layout = inspect_sequential_install(conn, expected_org_slug='happyranch', allowance=allowance)
           assert [tuple(row) for row in conn.execute('PRAGMA integrity_check')] == [('ok',)]
           assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
           return layout, sequential_storage_inventory(conn, allowance=allowance)
   _, before = snapshot(Path('/absolute/private-backup/pre-install.db'))
   layout, after = snapshot(Path('/home/benze/.happyranch/runtime/orgs/happyranch/happyranch.db'))
   assert layout.sequential
   assert all(after[1][table] == digest for table, digest in before[1].items())
   assert after[1]['tasks'][0] == after[1]['workflow_task_ownership'][0]
   print(json.dumps(after[1], sort_keys=True))
   PYTHON
   ```
4. Take a NEW verified checkpointed backup matching the installed source. Register
   the reviewed release with no admission/default changes:

   ```sh
   python scripts/setup_sequential_coding.py register --runtime-root /home/benze/.happyranch/runtime --org happyranch --confirm-cooperative-offline --backup /absolute/private-backup/pre-register.db --backup-sha256 NEW_VERIFIED_SHA256
   python scripts/setup_sequential_coding.py register --runtime-root /home/benze/.happyranch/runtime --org happyranch --confirm-cooperative-offline
   ```

   Retain the returned definition pin; repeating exact registration is a read-only
   no-op and verifies the same stored definition. Registration exit 0 does not enable Coding.
   Existing defaults/admission steps above remain separately authorized.
5. With the named service owner's authority, use `scripts/daemon.sh start` and
   `scripts/daemon.sh status`. Verify the loaded compatible release/engine and
   complete stored definition separately. Verify Workflows Setup and current
   Disabled/Allowed state without allocating a run. Genuine native/public Coding
   acceptance remains separate; storage/check/readback alone does not prove it.
6. On pre-COMMIT failure preserve source, sidecars and backup; native rollback or
   recovery must leave all original content unchanged. A SIGKILL before cache
   spill can leave a nonempty cold journal: checks correctly refuse checkpoint
   residue even though the old complete reader accepts the untouched DB. Keep
   that evidence and obtain an explicit checkpointed-image disposition; never
   delete the journal or blindly retry. After COMMIT use compatible readers;
   old-code-only downgrade is unsupported. Restore the verified pre-install DB
   only as separately authorized offline recovery after checking no new/remote
   effects require retention. After registration/runs preserve their newer
   evidence and follow the backout/drain rules below.

TASK-15719 executed this on a **copy only**, not the live org: 1,466,736,640
file bytes /777,130 rows. Two fresh-source `--check` calls took 6.753s/6.648s
(exit 3); two full installs 26.893s/27.267s (exit 0), including script-side
backup hashing/validation. Native backup acquisition plus scratch duplication
was 1.370s. Budget roughly one minute for check+install on this host at this
shape, plus the operator's exclusion, custody, backup/readback, registration
and restart work (not timed). The 10x envelope is sizing arithmetic plus separate
actual row/byte-cap and 160k-owner fixtures, not a timed full 10x combined install.
900s is a cooperative refusal deadline, not a hard OS-I/O or duration guarantee.
Live install/register/restart/rollback and genuine V3 acceptance: **NOT RUN**.

## Backout and drain

- Before extension installation, use separately authorized source deployment/
  backout ownership. Source rollback is no evidence about admitted work.
- After extension installation, even empty storage needs compatible schema and
  ownership@2 readers. Old-code-only downgrade is unsupported. Never drop tables,
  erase marks, rewrite pins/settings/events or reset source/carrier rows.
- Close NEW admission using a fresh explicit Disabled command with current CAS,
  preserving original operations. Keep admitted old/new callbacks, settlement,
  cancellation and LOCAL Final under original pins/current authority. Keep the
  unconditional old-fresh fence: backout must not reopen old @2 or fall back into bespoke progression.
- Defaults/admission changes never reassign or re-pin work. Supported cancellation
  is a business stop; Stop success/PID absence does not settle native/remote
  uncertainty. Original command=None/receipt uncertainty permits no reissue.
- Database restore is separate recovery and cannot undo external effects.

Record installed/loaded versions, storage/settings identities, original operations,
commands/exits and NOT RUN evidence. Real loaded cutover, operator installation/
reopen/backout, native/genuine V3/V5/M4 and access/merge acceptance remain NOT RUN
by this source unit. V3 ends 2026-11-23T08:48:38Z; nothing here extends it or
authorizes live changes. Independent review/QA/guarded merge remain source gates.
