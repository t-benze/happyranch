# Sequential workflow storage — operator draft

**UNVERIFIED execution behavior / SOURCE ONLY.** TASK-14597 adds
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

## Explicit check/install interface (execution UNVERIFIED)

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
source path. The streaming preservation inventory has separate finite ceilings:
8192 rows, 64 MiB encoded cells and five cooperative seconds per observation;
unsupported virtual/generated/WITHOUT ROWID/shadowed-rowid tables refuse rather
than omit identities. These bounds supplement, never replace or relax, the
existing complete-reader allowances.

The complete layout comparison retains its fully acquired and independently
validated column tuples inside the same schema snapshot. Sequential row reads
reuse those tuples for the unchanged type/length gates instead of acquiring
`pragma_table_xinfo` a second time. The metadata map consumes the original copy
allowance; the 8192-row, byte, traversal, SQL-step and deadline limits remain
unchanged. Full native metadata and all legacy comparisons are still required.

The native writer opens `mode=rw`, keeps `foreign_keys=1` and
`defer_foreign_keys=0`, and owns one `BEGIN IMMEDIATE` through COMMIT. After writer
acquisition it rechecks the original identity, lifecycle, backup observation,
complete source readers and original inventory. It executes the literal sequential
DDL without `executescript`, inserts marker1, and rebuilds only the ownership
table/view from the reviewed v2 literals. Explicit original rowids and every v1
column/value are copied unchanged; original view branches and owner-index dimension
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

Existing org startup must not install this extension. Fresh-org initialization
needs its own reviewed source/rollout authorization. Generic Database and the
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
one original row/raw/decoded/copy/work/deadline allowance includes independent
schema references and new companion ownership inventory. Exhaustion refuses; an
operator cannot substitute a subset or call exhaustion empty/unavailable data.
The connection and its SQLite progress hook are exclusively owned for the call.
TASK-14552's disposable AST/SQLite observations are structural evidence only;
no populated-history, installer, native or migration behavior is established.

Legacy-only and combined validation pass an exact retained `_Budget` with its
original per-call caps through the unchanged native connection, inside the same
snapshot and SQL progress/deadline context. TASK-14576 keeps those narrower
phase counters separate from the new schema/copy charges and adds them to the
unchanged outer totals on success or refusal. Outer success checks enforce the
original aggregate allowance without replenishment; retained refusals propagate.
Acquisition uses the retained per-call caps plus shared SQL-step/deadline bound,
not pre-reservation against the outer 64 MiB cap. Unbudgeted retained document/
ownership query bytes are not included in the outer byte counter.
