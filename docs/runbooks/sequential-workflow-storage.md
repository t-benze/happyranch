# Sequential workflow storage — operator draft

**UNVERIFIED / NOT EXECUTABLE.** TASK-14536 delivers only canonical records,
standalone new-history/combined-structure readers and ownership@2 reference.
No installer/check script exists in this unit. No live DB, migration, V3,
install/reopen/backout or startup upgrade was executed. This draft grants no
operating authorization. Founder THR336 msg85 / TASK14461 revision3 supplies the
source plan; the current task explicitly excludes U2b and V3 execution.

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

The future explicit, separately admitted operator transaction must:

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
   corrupt markers/history and original v1 marks. None of these steps has run.

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
