# Sequential workflow storage — operator draft

**UNVERIFIED / NOT EXECUTABLE.** TASK-14536 delivers only canonical records,
standalone new-history/combined-structure readers and ownership@2 reference.
No installer/check script exists in this unit. No live DB, migration, V3,
install/reopen/backout or startup upgrade was executed. This draft grants no
operating authorization. Founder THR336 msg85 / TASK14461 revision3 supplies the
source plan; the current task explicitly excludes U2b and V3 execution.

Before U2b writes any script, finish the remaining U2a integration: compose all
original complete document/Coding data readers with the new schema, preserve
their independent reference checks and add installed scalar/routing recognition.
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
