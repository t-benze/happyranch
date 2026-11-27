# Full sequential-history reader — Decision 2

TASK-14734 under ROOT TASK-14640 implements Founder THR336 msg102 Decision 2,
per accepted TASK-14643 d2-reader-design-rev4.md SHA256
02938d04e0121909307a1a088152b70e598fa7828029652880f52364f81d5caf
(design APPROVE TASK-14671; design sizing QA PASS TASK-14676).

The current [storage contract](../agent-guides/contracts/reusable-workflows.md#u2a-storage-and-standalone-reader-boundary)
separates observed schema/native metadata A, independent reference/comparison B,
and data/run-scoped ownership C. Each has the existing 8192-row, 64 MiB,
65536-step and 2M SQL ceilings. L meters org-wide zero-row guards and retained
legacy SQL at 32M; its acquired rows/bytes/steps consume C. All share the same
original five-second deadline and accumulate without reset. The fixed literal
reference prelude remains deadline-bracketed only. Schema comparison, literal
DDL, supported layouts, per-value guards and default legacy readers are unchanged.

This replaces the single cumulative allowance/shared handler described in
2026-11-08-sequential-storage-u2a.md (lines 17, 25–28, 41–44). The known
TASK14631 small-org limits and open Decision2 wording in
2026-11-08-sequential-engine-u3b.md (lines 22–28) describe the reader at U3b's
head. D2 supersedes those limits for the current contract. Those append-only
historical paragraphs retain their original-head meaning and are not rewritten.

Correction to accepted design section 6: its claim that one bounded fresh run
fits the old reader was disproved by TASK-14733. A valid engine-built Coding-data
returned history with 10 visits, 3 epochs and 2 content returns exhausted the old
combined allowance at row 8193. It blocked the U3e-2 return consumer; D2's
structural regression observation covers this case. No V3 acceptance follows.

F-A: retained legacy SQL remains O(org). F-C: non-run type/size/NUL/decode
predicates also remain org-wide SQL work under L, while whole-org row acquisition
and ownership traversal are removed from C. Both are disclosed engineering
choices inside Decision 2, with no decision requested. F-B: C still bounds
cumulative history and payloads; a later Founder-owned capacity decision is
reserved before ordinary production use. This PR makes no capacity decision.

Evidence is disposable structural observation only, never behavioral or V3 PASS.
No live DB, installation, configuration, migration, deployment, restart, admission
or U3e/U4–U9 capability is authorized. Independent full review, scoped QA,
lint/static checks, normal hooks and guarded manager merge remain required.

## Task projection exception — THR-404 Option B (#1905)

Only read_projection_v2(kind='task') composes sequential_task_history instead
of the complete oracle. Complete combined layout/schema/native ABI recognition
is unchanged. Existing validators replay selected runs, pinned settings and
operations, all owned sequential rows, native links/results and ownership; source
and generic wait-set closure includes every member/batch and its inbox,
deliveries, consumption receipts and notifications. Retained @1 sequential
Coding evidence is validated by its original replay. Separate retained
document/old-Coding instances cannot share selected native ownership. The
original Coding discriminator checks selected native metadata on its unchanged
retained 16 MiB allowance.

Unpinned settings generations and disconnected retained/sequential data do not
veto a task projection. Selected corruption refuses without partial output.
The indexed negative task-link path never enters org-wide data validation.
Fresh authority bookends, rollback-only snapshot, lock set/order, original D2
budgets and five-second absolute deadline remain. All write/admission,
continuation/settings/initiator/bootstrap/schema-load and run list/detail
callers retain the complete reader byte-for-byte. This exception grants no
capacity or write-validation change. See the current S7a/U7a/U6a contract.


## Shared write-scope primitives — THR-404 group 1 (staged)

`sequential_task_history.read_write_history` now accepts explicit run/source
roots and optional definition/settings roots in the caller's existing snapshot.
It retains complete layout/native ABI recognition and full original @1/@2
replay of the selected source/wait-set closure, including earlier batches,
reverse visit/event/ownership edges, native results and immutable receipts.
Every settings generation for the selected definition identities is loaded,
including definitions pinned by those generations, and `_settings` authenticates
contiguity from generation 1 and the complete operation/receipt chain. A
settings/definition-only scope can contain no runs. Missing explicit pins/runs
refuse; disconnected run payload corruption is outside this certificate.

This is a shared primitive only: group 1 wires no writer. The task projection
still uses pinned settings only, and its S7a contract above is unchanged. A
selected sequential certificate supplies no retained-family inventory or current
authority; mixed-family consumers still need their original family validators.
No snapshot, transaction, lock or cross-mutation cache is added.

For S4a H, `read_cancellation_headroom` selects only scalar identities of **all**
unfinished @2 runs (`running` or `held`), including `progress=cancelling`, and
validates their selected active-visit relation. It does not replay unrelated
payloads or reserve capacity. The caller combines any hypothetical additions
and applies the unchanged `_reserve_generic_cancellation` once on its original,
already-charged C ledger. Each unfinished run still reserves 16MiB + 128 copies
of known identity bytes, 32 rows, 4096 work and 65536 SQL steps. Three is a
nominal fit, not a promised capacity: earlier ledger use and long identities can
refuse earlier. Caller-supplied retained budgets are preserved and only this
read's deltas are charged into C. No allowance or deadline is reset or enlarged.

After later writer wiring, excluded unrelated payloads will no longer consume
that write's C ledger; large selected closures and all-run scalar H remain
bounded. Whole-org certification remains complete-history validation outside
the runtime-write ~100ms target: `inspect_sequential_install` (both reads),
`workflow_schema_load._complete_sequential`, bootstrap `_default_target`, and
Founder cutover request enable/disable with `_advance`/`_read` and
`recover_authorized` (EM STOP-1 ruling). `returned_run_ids` retains all-sequential
run replay and mixed cancellation retains its ten-core-table equality inventory.
`WorkflowContinuation.tla` Active/Admit/CapacitySafe semantics are unchanged;
no model update or capacity increase follows from this staged primitive.
