# Sequential Coding cutover and backout — operator draft

**SOURCE ONLY: registration/admission delivered; public cutover remains OFF.**
TASK-15616 delivers only U9b obligation 1, the expressly permitted inert first
PR. Public sequential fresh start still requires `check_venue_start`; old @2
fresh admission retains its original Explicit/native gates. The public selector
attachment and old-fresh fence MUST follow together in one merge unit. Even
Allowed new settings do not remove this fence. This runbook grants no live
operation. See the [U9 spec](../superpowers/specs/2026-11-12-sequential-coding-u9.md)
for the exact compatibility table, caller inventory and remaining obligations.

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
3. Under the separately authorized offline procedure, use the unchanged
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
   **Known pre-existing limitation (TASK-15618):** this unchanged installer/check
   inventories the whole database with 8192 rows/64MiB/5s, plus an 8192-owner install
   copy cap. Current retained production history already exceeds it. This PR
   deliberately does not repair installation; follow-on installer work is required
   before initial installation on such an org. No direct-SQL or live workaround.
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
7. **Still pending atomic U9b:** verify loaded selector/engine identity separately
   from source/storage/settings. Once attached and separately authorized, the
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

## Backout and drain

- Before extension installation, use separately authorized source deployment/
  backout ownership. Source rollback is no evidence about admitted work.
- After extension installation, even empty storage needs compatible schema and
  ownership@2 readers. Old-code-only downgrade is unsupported. Never drop tables,
  erase marks, rewrite pins/settings/events or reset source/carrier rows.
- Close NEW admission using a fresh explicit Disabled command with current CAS,
  preserving original operations. Keep admitted old/new callbacks, settlement,
  cancellation and LOCAL Final under original pins/current authority. This inert
  split does not yet fence old @2 fresh starts. After atomic cutover, keep that
  fence: backout must not reopen old @2 or fall back into bespoke progression.
- Defaults/admission changes never reassign or re-pin work. Supported cancellation
  is a business stop; Stop success/PID absence does not settle native/remote
  uncertainty. Original command=None/receipt uncertainty permits no reissue.
- Database restore is separate recovery and cannot undo external effects.

Record installed/loaded versions, storage/settings identities, original operations,
commands/exits and NOT RUN evidence. Public cutover, real operator installation/
reopen/backout, native/genuine V3/V5/M4 and access/merge acceptance remain NOT RUN
by this source unit. V3 ends 2026-11-23T08:48:38Z; nothing here extends it or
authorizes live changes. Independent review/QA/guarded merge remain source gates.
