# Sequential workflow storage — operator draft

## Automatic daemon-start installation (A4:2)

Founder THR-336 msg250/251 amends design v2 R2 (retained by v6): positive
previous-owner proof is not required. Automatic startup uses the same
one-daemon-at-a-time trust as ordinary startup schema migrations, plus original
A1 EX, full I1a integrity, verified SHA-256 backup before source writes and all
complete schema validation and startup ownership checks, repeated before COMMIT.
A concurrent pre-A1/old-code daemon is a **documented cooperative limitation**, because it
does not acquire the participating lock. No clean-shutdown receipt is used.

On the next startup each of happyranch, danze and family is treated conditionally:
installed storage takes ordinary complete validation without another backup or
installer; absent storage installs only if every gate passes. A refusal classified
`not_installed` (absent/held discovery, no worker or closed worker without permit)
loads the current layout under fresh SH after positive original custody closure.
It stays out of `broken_orgs`; workflow-setup status and the daemon log expose the
reason. A validated prior `not_installed` hold falls back on later boots without
retry or latch modification. Unknown effects/close, in-flight workers, sent permit
and registration mismatch remain broken. Normal loader validation still applies.
Only the existing historical UNKNOWN empty-scope `disk_sweep` exemption is applied
with an empty live-owner set under startup EX. Founder Option A, THR-380
(2026-11-18), additionally carries forward producer UNKNOWN attempts, Pause
journals, unresolved/orphan thread history, open repository-preparation
obligations and native coding source owners. Missing lifecycle is
pre-instrumentation legacy at startup. Their rows and original readers retain
custody; nothing is settled, initialized or repaired.

EM R7/R8 includes unresolved native `task_completion_recoveries` as carried
history only if its task is in existing `run_step.TERMINAL_STATES`. Non-terminal
or missing tasks still refuse with `workflow_setup_history_not_idle`; recovery
rows remain byte-identical and the shared offline validator stays strict.
Private copies have no process holding their org fence. Source-daemon running
rows are prior-boot evidence relative to the copy, owned by ordinary
`OrgState.load` recovery as on fallback. Retain original A1 EX and existing
native-writer/workflow owner predicates; no additional live reader is needed.

Preservation-only reads remain mandatory: typed whole-DB inventory, complete
schema/Pause validation, integrity and unchanged default load qualification.
Ownership rowids/types/bytes are copied exactly. Installation creates no runs,
visits or settings; qualified registration adds definition only. Existing native
callback/session/result admission and the old-fresh fence remain. Private
rehearsal loads affect private copies; registration/normal load run the unchanged
Pause boot recovery. Compare Pause rows against an identical old-layout boot and
all other retained tables against the preboot source, excluding intended
sequential additions. Observe this at loader return; later daemon selector
initialization retains its ordinary native-writer audit additions.

Unneeded native history replays and the aggregate history cap are removed.
Required native-writer replay retains its own 8192-row/64MiB scalar bound;
publication/profile/dispatch/recovery/remote/team-move owners still refuse using
bounded-output scans and existing predicates. Original EX/current source
observations still refuse cooperating live owners and concurrent changes.
The five-second history read, cumulative SQL and startup windows are unchanged.
The required registrar native-writer census uses one advancing rowid lower
bound instead of two equivalent bounds, so SQLite cannot repeatedly rescan the
original prefix. Its singleton whole-row gate is materialized once instead of
being flattened into every projected cell. Strict validation, live safety checks
and the default SQL, time, row and byte limits are unchanged.

Unavailable/lost EX
selects `writer_exclusion_unproven`; missing predecessor proof does not. No live
DB observation is needed to state this policy. Existing profile dependency
uncertainty may also hold other consumers.

The original 1,024-discovery cap is unchanged. Exhaustion holds only the
unselected org with `startup_upgrade_budget_unavailable`, before any of its DB,
fence or publication resources are acquired; remaining orgs and serving proceed.
Shutdown also requires positive closure of pre-worker selection connections,
untransferred fences and descriptors. The first close failure stays retained;
shutdown cannot claim success or retry that failed native close.
A later org refused before owned-worker admission has no worker sockets or child;
its original discovery closes through the same one-shot path. An earlier pending
or expired original keeps its EX until positive closure, including after reap.
The org HOLDs persist; this supplies no replay, restore or new attempt.
All startup attempt-evidence reads share one custodied path, including the final
registered read after the helper returns. A new child enters custody before its
parent close. A close fault visibly holds the org and retains FIRST error and
original EX through repeated refused shutdowns. A committed install/registration
stays intact, without restore, undo or another attempt. Advisory status retains
its independent traversal.

Automatic backups stay at
`<daemon_home>/backups/workflow-sequential/<runtime-key>/<org>/<attempt-id>/before.db`.
Only a known COMMIT permits source checkpoint; verified storage, actual A2
qualified registrar, positive closure and runtime audit precede registered
status. Failed/unknown attempts, pre-intent hold latches and incomplete phases
remain indefinitely for separately authorized operator assessment. Restart never
replays them or automatically restores a backup. No supported reset/unlatch or
fresh automatic-attempt command exists; preserve prior records and request
separately authorized operator assessment. A last pre_commit stays unknown;
committed/verified without registered stays installed_unverified. Never treat
missing/malformed evidence or a daily advisory report as setup success.

The one-time pre-serving upgrade window is 120s/org, 180s total, 20s final reserve,
>=60s before another attempt, 0.5s cumulative admission and 100s pre-COMMIT work.
The separate ordinary all-org load/listener gate remains <=10s, complete reads
remain 32M SQL and 5s, and the original A/B/C limits remain. Source delivery
provides no deploy/restart, live manual installation, Coding setup/roles/Allow
or PR1252 release. The explicit manual-install block below is unchanged.

I1a adds mandatory explicit full physical checks at the coherent source,
RO/query-only verified backup, writer source after `BEGIN IMMEDIATE`, and actual
writable target before any COMMIT attempt. One cumulative integrity ledger
(at most 640M VM steps, original 900s offline ceiling) covers all four; it grants
no ownership/inventory allowance. Failures use sanitized
`install_integrity_failed stage=source|backup|writer_source|target` or
`install_integrity_unavailable stage=... cause=sql_budget|deadline|busy|io|invalid_result`.
After writer acquisition, `not_installed` requires owned rollback, writer close
and independent RO original-inventory equality; uncertain proof is `unknown`
(exit 4). There is no new post-COMMIT physical veto or automatic restore.
The existing offline backup remains operator-created and untouched; A4 owns
automatic bootstrap/phase publication. I2 removes inline physical checks;
the daily read-only report is I1b. See the
[I1a contract](../specs/2026-11-14-automatic-workflow-setup.md).

Database owns composed native trace registration through `observe_trace`; native
connection type, constructor and independent authorizer/progress slots stay exact.
The unpublished load now borrows complete sequential validation only in audited
source/three unchanged target intervals, with exact file/execution/default-policy
identity and a pinned zero-write snapshot. Writes/write rollback, schema/config
execution or failed observation invalidate it; replaced files refuse. Retained
charges import once per compatible caller and changed targets debit original
cumulative counters. Public/unknown snapshots and later loads validate fresh.
Every miss retains full FK/domain checks and original finite limits. Historically,
A0b-2 capacity acceptance required actual default writable installed danze,
family and
genuine fresh-org FIRST/NEXT returns on Python3.12/3.14. Installed happyranch's
FIRST and attempted NEXT had to refuse with zero DB changes, no partial certificate
and no leaked trace registration: **EXPECTED REFUSAL - part 1 retained until I2**,
never PASS. Retain the once-only original pre-A0b-2 observation of that private
image; continuation and head re-verification must not repeat it.
Installed happyranch FIRST/NEXT/Pause/all-org-tail capacity acceptance belongs to
I2, on its fresh base and capture; all other A0b-2 cases remain required. No cap,
check, deadline or caller changes here. The current [I2 policy](../specs/2026-11-14-automatic-workflow-setup.md#i2-load-and-revalidation-policy)
requires successful primary/tail reads under the 10s all-org envelope.
Automatic install stays size-guarded/held for over-budget orgs; no setup or
operating authority follows.
The [v3/F1 case map](local-ci.md#a0b-2-requirement-to-case-map-v3-section-4-and-f1)
records each named assertion and the separately evidenced old-Coding queued-family
applicability limit. It is not an executed capacity or readiness receipt.

A0b finite SQL prerequisite: complete readers materialize shared singleton row
shape/size once and specialize equivalent ownership type predicates. The private
exceptional-text scan still follows a complete whole-table type/size proof in
the same snapshot, with bounded acquisition and original logical charges.
This component adds no installer/startup caller or new reuse and retains
full FK/domain checks and every existing runtime/operator allowance below.

**SOURCE ONLY; isolated-copy installer evidence, no live acceptance.** TASK-14597 adds
`scripts/install_workflow_sequential_schema.py` and the explicit installer in
`runtime/infrastructure/workflow_schema.py`. It uses the U2a
complete readers and ownership@2 reference; importing source installs nothing.
Founder THR336 msg85 / TASK14461 revision3 authorizes source delivery, selected
by TASK14595 under ROOT14563 (msg95 continue). This runbook grants no operating
authorization. No real/live DB installation, V3, deployment or restart is
performed or authorized by this unit.

TASK-14576 adds U2a source-only complete document/Coding reader composition and
installed scalar recognition. After full new-history validation, an explicit
connection-bound scope selects independent combined references for original
recursive readers. Exact sequential table names and full typed-row equality
enroll new metadata; all non-sequential Coding refusals remain. Installed v2
routing is reconciliation-held; v1 keeps its original route. This is not U2b
installation/check/reopen/backout evidence.
The current standalone reader authenticates neither native settlement nor
capability/current-authority claims; U3 and later adapters must do that. Existing
production code intentionally refuses the new schema even when empty.

The explicit, separately admitted operator transaction must:

1. Recognize the entire original F/E/G/H/I/J layout, Coding, owner-index and v1
   mark dimensions independently. Refuse partial/corrupt/unknown objects or data.
   Check registered daemon cooperation/stoppage and original native owners/jobs;
   do not infer closure from task status. Capture verified backup/inventory and
   original settings/operation custody before any mutation.
2. Dry-check without writes. In one owning schema transaction install the
   literal sequential extension and its marker; rebuild only the ownership
   companion to the reviewed v2 reference, preserving every old column, row,
   version1 value, ID, byte, view branch and existing index dimension. Never
   adopt ordinary sources as sequential sources in this operation.
3. Compare the complete target reference and all old/new data/FKs before COMMIT.
   Preserve rollback/error/ambiguous-COMMIT evidence. Publication follows an
   observed successful COMMIT, never a delivery flag. No other table alteration
   or old document-layout upgrade belongs here.
4. In the authorized disposable V3 venue, independently observe backup → check →
   install → reopen → rollback/refusal, including empty extension, partial schema,
   corrupt markers/history and original v1 marks. V3 acceptance has NOT RUN;
   disposable structural observations are distinct.

The earlier discovery-only checkpoint is historical and superseded by the
mounted A4:2 contract above. Discovery owns SH selection, positive SH close and
fresh EX selection under one original startup/admission ledger. Budget expiry
holds absent storage without an install attempt; installed selection stays
ordinary. A contended EX may reacquire SH to observe current storage, but absent
storage HOLDs with `writer_exclusion_unproven`. Under the current contract above,
a proven no-effect refusal closes original custody and acquires fresh SH for
ordinary loading; it never transfers a held selection directly. Failed
acquisition/unknown closure quarantines. Installed selection can transfer its
original SH into the normal loader. The same
120s/org, 180s-total, 60s admission, 20s reserve and cumulative0.5s waiting limits
remain; these clocks are not a hard bound on filesystem I/O. Native supervision,
registration and startup are mounted; whole-listener acceptance is separate.
See the historical
[discovery checkpoint](../specs/2026-11-14-automatic-workflow-setup.md#a4-original-discovery-and-upgrade-window-checkpoint).

## Explicit check/install interface (isolated copies executed)

Use compatible source and a separately authorized cooperative offline operation.
The command names below describe the delivered interface, not permission to run
it on a real org:

```sh
python scripts/install_workflow_sequential_schema.py --runtime-root /absolute/runtime --org ORG --check
python scripts/install_workflow_sequential_schema.py --runtime-root /absolute/runtime --org ORG --confirm-cooperative-offline --backup /absolute/backup/happyranch.db --backup-sha256 VERIFIED_SHA256
```

Exit codes: **0** `compatible-already-installed` (or observed `installed`),
**3** `install-needed` for a valid original source in check mode, **1** `refused`,
**2** usage error, **4** `uncertain` after attempted COMMIT or connection-close
failure. A failed COMMIT remains uncertain even if a later rollback returns.
Keep stdout, stderr, exit status, source/backup inventory and original operation
custody; reconcile with the original owner before any explicitly admitted retry.
No automatic retry or success claim follows an ambiguous return.

The following A4 checkpoint descriptions are historical. Their unmounted status,
absent automatic caller and incomplete integration is superseded by the
current A4 contract at the top of this runbook; whole-startup/listener timing
acceptance remains separate.

The A4 draft added an optional pre-COMMIT coordinator callback to the owning
primitive and a private durable phase publisher. The offline interface above
does not select either automatically. See the [A4 checkpoint](../specs/2026-11-14-automatic-workflow-setup.md#a4-phase-publication-and-commit-boundary-checkpoint)
for their historical boundary before startup integration. These source
owners grant no live install, restart, restore or retry authority.

The draft source/backup checkpoint introduced the then-unmounted
`BootstrapSource` pre-checkpoint owner:
native coherent RO WAL snapshot, exact preservation inventory, exclusive native
online backup, file/directory fsync and independent RO full-integrity/inventory/
hash verification before backup_verified. Its failed/partial backup is retained;
original close uncertainty never becomes retry authority. The [source/backup
checkpoint](../specs/2026-11-14-automatic-workflow-setup.md#a4-coherent-source-and-verified-backup-checkpoint)
records the then-remaining volume, rehearsal and startup integration gates.
The offline commands below still require their own explicit checkpointed image and guards.

The rehearsal checkpoint introduced the then-unmounted `BootstrapRehearsal`
capacity stage: exact verified-backup hash, no-follow canonical metadata copy, real private install,
then writable default FIRST/NEXT on that same image. Metadata/backup drift,
original read exhaustion, insufficient rehearsal-volume headroom or pre-COMMIT
time refuse before any source writer. Scratch is outside backup/output storage
and is disposable only after positive original closure and directory identity
checks. That checkpoint added no source restore, live native-owner inference or
automatic caller. See the [rehearsal checkpoint](../specs/2026-11-14-automatic-workflow-setup.md#a4-private-writable-rehearsal-checkpoint);
the offline commands and their explicit guards remain unchanged.

The writer checkpoint's then-unmounted `BootstrapInstall` composed original
source checkpoint, native writer, default target and post-COMMIT independent reopen/preservation.
It accepts one parent permission only after durable pre_commit and rechecks the
original twenty-second reserve. All original handles close before verified;
uncertain COMMIT/rollback/close never becomes unchanged or replay authority.
Known refusal needs independent original equality before not_installed. Combined
backup/DB/temp/rehearsal headroom is checked by device before backup creation
and again before writer admission. DB/temp consume one allowance on a shared
device, while backup and rehearsal allowances add; the minimum observed free
space must cover the total. An unavailable observation refuses before backup
creation. The supervised helper must select its private writable
SQLITE_TMPDIR before SQLite work. These observations do not reserve disk blocks.
This is a source stage, not an added command or live migration authorization.
Original-owner qualification, parent supervision, registration/audit/status and
automatic startup integration were still required at that checkpoint. See the [writer boundary](../specs/2026-11-14-automatic-workflow-setup.md#a4-original-writer-and-independent-reopen-checkpoint).

The selection checkpoint's then-unmounted `BootstrapSelection` added no command
or setup action. Its bounded read-only routing honors prior attempt latches before SQL and closes its original
handles before returning. Sequential objects route directly to ordinary complete
validation without installer/backup/inventory/full-integrity work. Presence is
not a validity claim; an absent result still requires new EX discovery and full
original-owner qualification. A failed/uncertain attempt is never automatically
retried, including after not_installed. Registered evidence must name the same
present DB path/device/inode. Nothing here restores a backup or authorizes live
maintenance. The automatic startup caller was incomplete at that checkpoint;
current mounting follows the A4 contract above, with timing acceptance separate.

Every invocation starts with `resolve_org_database` and an immutable read-only
SQLite connection (`mode=ro&immutable=1`, query-only, foreign keys enabled), one
snapshot and before/after file observations. It never creates sidecars. Nonempty
WAL/journal files refuse with `workflow_seq_checkpoint_required`; arrange a
separately authorized checkpointed image first. Empty recovery files and an
existing SHM are observed for drift. This check intentionally cannot inspect a
live WAL snapshot. Do not remove recovery files to make it pass.

The existing complete `read_sequential_history` recognizes F/E/G/H/I/J, optional
Coding, absent/complete owner indexes and ownership dimensions independently.
All original family refusals propagate. Installation specifically requires an
existing complete v1 ownership companion, even when empty. Missing ownership or
sequential storage paired with v1/absent ownership refuses; this script never
backfills marks, repairs a partial install or adopts ordinary sources. Installed
complete ownership@2 is a compatible no-op, including populated readable storage;
its integrity is not admission or engine availability.

Before opening a writer, the operator must positively maintain stopped state
and exclusion of all current native writer/preparation/process/owner/job work
across affected registered runtimes, shared-daemon stores and cooperative hosts.
Retain original settings, operation and unsettled-effect custody. The explicit
confirmation attests that precondition; the script reuses the bounded existing
lifecycle observation and requires registered runtime plus absent daemon PID/port
files. These observations cannot certify native closure or fence hostile
same-user activity. Unknown/uncertain owners must be reconciled by their original
owners; absence of a PID or terminal task alone supplies no clearance.

A separate absolute checkpointed SQLite backup and its operator-verified lowercase
SHA256 are mandatory for a new install. The script verifies the checksum, complete
family readers and equality of all SQL plus typed rows/rowids to the original RO
source inventory. It refuses aliases/hardlinks and known file drift; the backup
is never written. Keep the backup and inventory with operator custody outside the
source path. TASK-15719 sizes only this operator's streaming preservation inventory
for 8,388,608 total rows, 16GiB encoded cells (including schema/tags/rowids), and
32MiB raw cell bytes per row. A SQL preflight checks every row's length before
Python payload acquisition. The backup is hashed in 4MiB chunks up to 32GiB.
One original 900s cooperative deadline spans source, backup and locked pre/post
checks, ownership copying and commit preflight. Only the installer selects 640M
ownership SQL steps per complete read. Default inventory callers retain 8192
rows/64MiB/5s; default/runtime/venue reader budgets and all workflow-data limits
are unchanged. Exhaustion has named inventory/row/backup/deadline causes;
unsupported virtual/generated/WITHOUT ROWID/shadowed-rowid tables refuse rather
than omit identities. These bounds supplement, never replace or relax, the
existing complete-reader allowances.

TASK-15741 adds installer-only preflights BEFORE each complete-reader acquisition
(source, backup, locked source and target, including an installed no-op), in the
SAME native read snapshot. Each of tasks and ownership is limited to160,000 rows;
a bounded COUNT reads at most160,001 scalar rows and refuses with
`workflow_seq_install_task_ownership_count_exhausted:tasks` or
`:workflow_task_ownership`. A second SQL scalar-length preflight limits the SUM
of tasks.id and all five ownership cells to64MiB, refusing with
`workflow_seq_install_ownership_bytes_exhausted`. These are admission limits,
not alternate validators: the unchanged complete reader still checks every
completeness/duplicate/foreign/wrong-owner/cross-family binding predicate.
The 64MiB bound prevents arbitrarily long IDs/marks from defeating the count cap;
unrelated task/result/audit payloads remain with the streamed inventory.
Memory may grow with task COUNT only within the finite160,000 cap, alongside
bounded acquired bytes/rows and unchanged workflow-reader allowances. SQLite
native allocations and hard RSS are outside these Python observations.

The complete layout comparison retains its fully acquired and independently
validated column tuples inside the same schema snapshot. Sequential row reads
reuse those tuples for the unchanged type/length gates instead of acquiring
`pragma_table_xinfo` a second time. The metadata map consumes C. Decision 2
separates schema observation/native metadata (A), reference/comparison (B), data
and run-scoped ownership (C), and org-wide guard/legacy SQL (L), with one original
deadline and no allowance reset. Full native metadata and all legacy
comparisons are still required.

The native writer opens `mode=rw`, keeps `foreign_keys=1` and
`defer_foreign_keys=0`, and owns one `BEGIN IMMEDIATE` through COMMIT. After writer
acquisition it rechecks the original identity, lifecycle, backup observation,
complete source readers and original inventory. It executes the literal sequential
DDL without `executescript`, inserts marker1, and rebuilds only the ownership
table/view from the reviewed v2 literals. Explicit original rowids and every v1
column/value are copied unchanged through a file-backed TEMP SQL table (no
whole-history Python list); original view branches and owner-index dimension
remain. No source adoption or old document-layout upgrade occurs.

Before COMMIT, the complete scoped reader independently compares the entire
target and validates old/new data/ownership; all FKs, old SQL bytes except the two
explicit replacements, and every original typed row/rowid must match. Only the
new marker is nonempty. Identity/lifecycle/backup observations are checked again.
Known validation failures roll back and preserve the original error (plus any
rollback error); publication follows successful COMMIT and connection close.
Compatible reruns return from the initial RO check without a writer, backup or
offline acknowledgement. Check/install never grants startup reachability,
settings authority or execution permission.

Neither draft nor submission migration is a prerequisite: the copied F+Coding
org installs directly. Separate submission-check `coding_authority_history_invalid`
remains unrepaired. See the [happyranch operator checklist and measured durations](sequential-coding-cutover.md#happyranch-operator-checklist-separate-operating-authority-required).
Copy experiments cover all supported document layouts with/without Coding/indexes,
full preservation, finite limits and interrupted transactions. They do not establish
live/native/V3 acceptance. Before-COMMIT SIGKILL may retain a cold nonempty journal;
the unchanged checkpoint guard refuses until an explicitly assessed checkpointed
image is available. Never remove recovery files to force a rerun.

A2 did not install existing orgs; the automatic existing-org startup owner is now A4 above. A2's distinct POST `/orgs` owner now exclusively creates
the DB in its new skeleton, installs J + Coding + sequential/ownership-2 and
validates the literal independent combined reference plus install-time physical
integrity. Historical initializer defaults stay byte-unchanged, and absent DBs
on startup never establish freshness. Default stays human-managed with zero
agents and current reviewer configuration `none`. Qualified catalog registration
adds only Coding v2 definition bytes; no settings, admission, roles, runs or tasks.
Missing defaults remain Disabled; existing saved state is preserved exactly.
Generic Database and the
machine runtime-audit remain excluded. MEM498: do not add a new-column index to
an executescript block used by legacy upgrades. MEM374: unchanged old bytes do
not by themselves prove a new constraint or migration safe.

Before installation, source rollback keeps original semantics. After installation
**even empty storage/ownership@2 requires compatible code**; old-code-only
rollback must refuse. After runs, close NEW admission only through its authorized
settings owner while retaining compatible historical readers, dispatch and
settlement. Do not drop tables, down-convert, rewrite pins/events/marks or reset
source/carrier tasks. A DB restore is separate operational recovery and cannot
undo remote effects. Leave uncertain original effects with their owners.

The new-table admission contract also reserves `(org_slug, source_principal,
start_operation_key)` uniquely, retaining exact original source/result/request
binding. For future U3 run-first admission, only the new run's `last_event_id`
foreign key is `DEFERRABLE INITIALLY DEFERRED`. Within the existing owning
`BEGIN IMMEDIATE`, keep `foreign_keys=1` and `defer_foreign_keys=0`, insert the
provisional run with a null active pointer, then visit/task/mark/event and final
pointers, and validate complete target/history/FKs before COMMIT. An unresolved
event is a commit failure. This is a new-table dependency contract, not a
startup change or permission to run an admission writer.

Standalone inspection uses the [finite reader contract](../agent-guides/contracts/reusable-workflows.md#u2a-storage-and-standalone-reader-boundary):
A/B/C each retain 8192 rows, 64 MiB raw/decoded/copy reservations, 65536 steps
and two million SQL progress units. L supplies 32 million SQL progress units for
zero-row guards and retained legacy composition, with acquired rows/bytes/steps
charged to C. Ownership acquisition is run-scoped through cached run/visit
relations and primary-key task/mark lookups; no binding-view text is acquired in
C. All four accumulate without reset under one original five-second deadline.
The fixed reference prelude is deadline-bracketed only, not SQL-metered.
Exhaustion refuses; an operator cannot substitute a subset or call exhaustion empty/unavailable data.
The connection and its SQLite progress hook are exclusively owned for the call.
TASK-14552's disposable AST/SQLite observations are structural evidence only;
no populated-history, installer, native or migration behavior is established.

Legacy-only and combined validation pass an exact retained `_Budget` with its
original per-call caps through the unchanged native connection, inside the same
snapshot under L's SQL meter and the shared original deadline. TASK-14576 keeps
those narrower phase counters separate from schema/copy charges and folds them
into C on success or refusal. Success checks enforce C without replenishment;
retained refusals propagate. Acquisition uses retained per-call caps plus L and
the original deadline, not pre-reservation against C's 64 MiB cap. Unbudgeted
retained document/ownership query bytes are not included in C's byte counter.


## I1b advisory daily observation

At the existing future-local 03:00 daemon wake, an independent read-only worker
checks each known org snapshot without setup or repair authority. Sweep stop,
reservation failure and slow sweep work do not gate that launch. Down at wake
means missed; no same-date retry or startup catch-up occurs. Per-date receipt
and final report plus latest live under
`<daemon_home>/workflow-integrity/<runtime-key>/<slug>/`; do not remove them to
retry. An unfinished receipt, stale date or unavailable result is not success.
The existing Audit filter `action=workflow_integrity_check` exposes loaded-org
reports; unattached audit failure is retained in external reporting metadata.
See [report semantics](../agent-guides/contracts/observability.md#daily-workflow-physical-integrity-advisory-i1b).
These records authorize no restore, hold, cancellation, installation or admission
change. I2 removes inline physical checks; full FKs and parts 2–7 remain on
complete reads under the original budgets and deadline.

## Cooperative org fence (A1)

The supported explicit installers/migration writers now acquire stable EX before the first org connection; read-only check branches acquire SH. Sequential setup holds EX across its source reads, writer, close and publication. The source-only preflight/backup reader and maintenance readers acquire SH. Existing human-host exclusions precede any new fence-file effect. Operator confirmation, registry/lifecycle, backup, source/target checks, original deadlines and uncertain-outcome handling remain required.

Lock files live under `<daemon_home>/org-access-fences/` and are never removed/replaced by the protocol. EX is cooperative exclusion, not original native closure or migration permission. Preserve retained unknown native history and its original owner. Automatic A4 carries the listed historical categories unchanged under Option A and uses the startup-migration trust and cooperative limitation explicitly amended above by THR-336 msg250/251, without predecessor receipts or PID-based clearance. Explicit offline/manual operations retain their existing operator exclusion requirements.

## History

These references remain inline to preserve sentence meaning and owner, artifact,
or case identity.

- `TASK-14597` — Sequential workflow storage — operator draft (original line 51).
- `THR336 msg85` — Sequential workflow storage — operator draft (original line 55).
- `TASK14461` — Sequential workflow storage — operator draft (original line 55).
- `TASK14595` — Sequential workflow storage — operator draft (original line 56).
- `ROOT14563` — Sequential workflow storage — operator draft (original line 56).
- `TASK-14576` — Sequential workflow storage — operator draft (original line 60).
- `TASK-15719` — Explicit check/install interface (isolated copies executed) (original line 143).
- `TASK-15741` — Explicit check/install interface (isolated copies executed) (original line 156).
- `TASK-14552` — Explicit check/install interface (isolated copies executed) (original line 252).
- `TASK-14576` — Explicit check/install interface (isolated copies executed) (original line 257).
