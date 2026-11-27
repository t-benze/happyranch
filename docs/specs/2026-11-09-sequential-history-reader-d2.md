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
budgets and five-second absolute deadline remain. This task-projection exception
grants no write-validation change; THR-404 group 2 below separately scopes
start/admission and continuation preflight, and group 3 below scopes initiator
and non-cancel generic dispatch/hold. Group 4 scopes direct control/cancel/close
below. Settings, source outer settlement, bootstrap/schema-load and run list/detail
retain their complete reader.
See the current S7a/U7a/U6a contract.


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

Group 1 introduced the primitive without writer wiring; group 2 below mounts
it for admission/preflight only. The task projection still uses pinned settings
only, and its S7a contract above is unchanged. A
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

For the group-2 writers below, excluded unrelated payloads no longer consume
that write's C ledger; large selected closures and all-run scalar H remain
bounded. Whole-org certification remains complete-history validation outside
the runtime-write ~100ms target: `inspect_sequential_install` (both reads),
`workflow_schema_load._complete_sequential`, bootstrap `_default_target`, and
Founder cutover request enable/disable with `_advance`/`_read` and
`recover_authorized` (EM STOP-1 ruling). `returned_run_ids` retains all-sequential
run replay and mixed cancellation retains its ten-core-table equality inventory.
`WorkflowContinuation.tla` Active/Admit/CapacitySafe semantics are unchanged;
no model update or capacity increase follows from this staged primitive.


## Start/admission and continuation preflight — THR-404 group 2

Admission uses actual run/source/definition/settings roots, including full old
history for every run on the affected source and all earlier wait-set batches.
Original operation-key/body/INTEGER result/task/principal/SID replay and
constraint-backed uniqueness, authority/assignment and rollback checks remain.
The shared control validator accepts these roots only when admission supplies
them; group 3 adds explicit roots at the initiator and non-cancel generic
hold/dispatch sites, and group 4 adds direct control/cancel/close roots below.
Default source-settlement and mixed callers remain complete.
Original @1 admission also authenticates its selected closure/settings chain.

Preflight resolves the actual selected definition/settings IDs and validates
them with its source closure. It reads all unfinished scalar H on its original
preflight ledger, appends the hypothetical members, and calls the unchanged
reservation once. Batch admission does the same before allocation on its
original batch ledger. Post-allocation admission validates the affected state
and reserves H on its original resulting-reader ledger/deadline. None of these
reads survives a mutation or replaces the original native admission checks.

Group-2 isolation and timings cover preflight and direct admission or equivalent
validation only. Group 3 below removes the remaining initiator complete-reader
tail from the native start callback; these earlier phase timings do not measure
full callback latency. All existing lock sets/order, transactions,
error categories, audit shapes, deadlines and budgets remain unchanged.
`WorkflowContinuation.tla` AtomicBatch/CapacitySafe and source-release semantics
are unchanged; no model edit or TLC run is required for this validation wiring.


## Initiator and non-cancel generic dispatch/hold — THR-404 group 3

Initiator dependency observation selects its actual source, closing over every
owned run, earlier batch, wait member, notification, delivery and consumption
receipt. Ordinary/no-owner early returns and the original ordinary dependency,
active join and unfinished-run barriers remain. Delivery and consumption pass
explicit source roots to `validate_control_write` on their original deadline,
with the existing future cancellation reservation. Only notifications actually
delivered to the genuine SID can be consumed. Exact receipt replay remains inert.

Non-cancel generic dispatch (including its replay branches) and business hold
pass the actual run and source. Full affected replay includes sibling wait
members and original receipts/settings; all-unfinished scalar H still reserves
on the resulting reader's original ledger. Held/returned wakes, carrier closure,
source/parent independence, authority/assignment, rollback, errors and audit
shapes remain unchanged. Unrelated payload corruption no longer vetoes these
scoped phases. No allowance or deadline is raised.

The native start callback now uses scoped validation through first delivery,
dependency observation and consumption. Source outer settlement is a distinct
lifecycle phase and retains its complete-reader default, including its mixed
cancellation fence. Group 4 below scopes direct cancel/control/close. Return/rediscovery, settings/builtin,
retained adapters, mixed and historical Final consumers remain for later groups;
STOP-1 whole-org certifications remain complete. Lock set/order and transactions
are unchanged; WorkflowContinuation/SequentialSettlement need no model edit.

## Direct control/cancel and never-launched close — THR-404 group 4

Direct control, native cancel fence and positive-closure finish pass their actual
run and source roots to `validate_control_write`. Full selected replay includes
all sibling wait members, earlier batches, historical proofs/receipts and the
selected settings chains. Original direct-control receipt replay authenticates
that same affected history before returning immutable receipt bytes; original
actor task/principal/SID/INTEGER result and changed-command checks retain precedence.
Replacement controls reserve scalar H for ALL unfinished runs on the original
resulting-reader ledger. Cancellation and inert receipt replay omit future H;
current event capacity, CAS and expected Held-event proofs remain unchanged.

Never-launched close uses the same roots at original receipt replay, before
finish and after finish/wake/canonical audit insertion. Each read authenticates
its current snapshot independently. The queued/null-SID visit, cancelled native
task and every original no-admission/no-launch/journal predicate remain mandatory.
Missing terminal/outer closure still refuses ordinary finish. A business close
retains native uncertainty and grants no cleanup clearance.

Disconnected payload corruption no longer vetoes these addressed checks; selected
event, sibling or native-proof corruption still refuses and rolls back. The
native fence's enclosing mixed owner and legacy/tail retained-family readers stay
complete until group 7b; `drain_source_cancellations` reaches the scoped finish
through its unchanged wrapper. Source outer settlement and STOP-1 global
certification remain complete. No lock set/order, transaction, audit shape,
budget, deadline or represented NeverLaunchedCancelClose/SequentialSettlement
transition changes; no TLA+ edit or TLC execution follows from this wiring.
