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
and group 5 scopes selected returns below, keeping complete all-run rediscovery.
Group 6 scopes settings below; Group A scopes original @2 source outer
settlement below. Bootstrap/schema-load and run list/detail retain their
complete reader.
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
Default callers remain complete; Group A supplies the original @2 source
settlement root below. Group 7b mixed callers use the
separate ABSENCE-OR-COMPLETE composition below.
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
lifecycle phase scoped by Group A below; its mixed
cancellation fence uses the separate group-7b certificate. Group 4 below scopes direct cancel/control/close; group 5
scopes selected returns and retains complete rediscovery. Settings/builtin,
retained adapters and mixed consumers are described in groups 7R/7b below;
historical Final consumers remain complete;
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
on the ABSENCE-OR-COMPLETE certificate in group 7b;
`drain_source_cancellations` reaches the scoped finish
through its unchanged wrapper. Group A scopes original @2 source outer
settlement below; STOP-1 global certification remains complete. No lock set/order, transaction, audit shape,
budget, deadline or represented NeverLaunchedCancelClose/SequentialSettlement
transition changes; no TLA+ edit or TLC execution follows from this wiring.

## Selected return and complete rediscovery — THR-404 group 5

`returned_source` and `notify_returned` replay the actual run via
`read_write_history`, including full source/wait-set/sibling closure and settings
chains. Positively absent sequential storage returns false. An initial bounded
PK probe permits an empty certificate only for a
verified absent selector. The probe and original `_returned` selection share
one ledger and original five-second deadline; scoped replay uses that same
absolute deadline. No allowance increases. `_returned` remains unchanged:
completed run, exact last returned event, one matching original-source outbox,
null visit. Source equality still gates parent wake. Verified non-return returns
false; addressed event/outbox/ownership corruption raises before effects.

The snapshot closes before source/carrier history rebuild, ordinary parent wake,
terminal-thread tail and queue adaptation. Replay never acknowledges/deletes the
outbox. @2 source completion, authority, transaction/rollback, errors, audit rows,
locks/order and WorkflowContinuation `ReturnCompletesSource=false` are unchanged.

`returned_run_ids` retains the complete reader: the current explicit-root API
does not compose all-run enumeration onto its charged ledger. Keeping this
allowed fallback avoids an extra enumeration allowance or a shared-API change.
All sequential runs, regardless of stored state, and unrelated native/retained
history still authenticate before returning exact obligations for terminal or
nonterminal sources. Exhaustion refuses the entire discovery. No <=100ms
rediscovery claim is made; measure complete discovery separately. STOP-1,
source outer settlement, run list/detail and groups 6–8 were unchanged in group 5;
group 6 is described below.


## Settings, builtin registration and fresh creation — THR-404 group 6

Settings Save/admission and immutable original lookup select definition/settings
roots through `read_write_history`, with no run roots. Replay chooses the original
operation's setting; fresh requests choose the requested registered definition.
The complete identity-wide settings chain starts at generation 1 and authenticates
one immutable operation/receipt per setting and all pinned definition versions.
Post-INSERT validation reacquires that chain before original receipt readback.
The latest CAS, Disabled/Allowed behavior, eligibility/independence, authority
bookends, error codes and uncertain-publication behavior remain with their owner.
No settings headroom policy is introduced. Admitted run pins remain immutable.
A bounded absence probe retains the original COMPLETE legacy validation and
`sequential_storage_unavailable` result when no sequential storage exists.

Builtin registration selects the reviewed catalog definitions and settings roots,
including settings whose missing definition must refuse rather than be repaired.
It keeps the native census/safety and one nondurable reservation, pointer/cache
bookends, original transaction and exact INSERT count/compiled readback/FK checks.
No unrelated run graph is acquired. Readback remains the original post-INSERT
qualification; no second retained-history allowance is replenished.

Only `initialize_fresh_org_database` replaces its final whole-history read with
exact structure, bounded positive emptiness of each owned data table and exact
version/cutover seeds plus their original validator. Native pristine-seed/identity,
full FK and physical installation checks remain. O_EXCL and inode identity are
required; existing DBs cannot enter this path. Global install/load/bootstrap and
Founder cutover keep COMPLETE certification under STOP-1. No budget, deadline,
transaction, lock set/order or transition changes; no TLA+ change follows.


## Retained ABSENCE-OR-COMPLETE mixed certificate — THR-404 group 7R

The separate mixed-write entry preserves original closure I and reuses
`_graph`, `_settings_chain` and `_replay` for selected v2 runs. Its retained adapter
examines all columns of the exact matched retained workflow schema and the native
Coding/document metadata, cancellation-audit, ownership and journal surfaces.
Every native task/run/instance/result reference in those supported encodings is
included: scalar IDs, nested/escaped JSON, named base64/hex and typed native cells.
Native revisits conservatively select complete fallback; native chain/fanout JSON
is included without changing I. Ordinary native metadata negatives remain bounded
SQL probes on L; unrelated
native result summaries are not acquired. Unknown/duplicate/opaque structured
encodings, a positive reference, selected retained ownership, @1 data or an
unsupported layout all use the original complete reader. No positive retained
inventory is reconstructed by this adapter.

The B1 retained-table subset discriminates every column in SQL under original L,
then acquires only candidates through the existing bounded rowid pages. SQL can
rule out only plain ASCII scalar/JSON values with no matching task/run/instance
or appropriately named result reference. Duplicate keys, nested JSON strings,
encoded/native cells, non-ASCII/opaque text, nonfinite values and excessive depth
remain candidates for the unchanged decoder. Arrays preserve inherited result
field names. This avoids decoding unrelated publication snapshots. It does not
enable the observed F layout. Native task-results metadata and eligible audit
payloads now share this conservative candidate walk, with original reserved
Coding scalar/prefix candidates and independent Coding/workflow audit task
bindings. Distinct action lookup preserves the original eligible action set.
Raw ASCII native JSON can also rule out decoded Unicode string values after
checking every JSON-tree level for the same reference and Coding markers.
Non-ASCII raw text/keys, surrogate escapes (including valid pairs), decoded NUL,
duplicates, encoded fields, nested JSON after Python whitespace, nonfinite/deep
or invalid shapes still reach the original decoder. This is a guarded negative
subclass, not general JSON1/Python parser equivalence. The copied F proof now
avoids the decode-byte exhaustion but exhausts the unchanged 32M SQL allowance
in native result selection at about 1.44 seconds. The F gate therefore stays
COMPLETE. No generic control exemption, F fast-path or approximately 100 ms
acceptance follows.

Only exhaustive absence constructs an EMPTY retained `_CodingInventory` with a
real empty `QueuedStorage` and empty reverse cancellation dependencies. All
returned mixed native rows remain exactly closure I. No retained=None or partial
inventory is accepted. Every probe/replay/fallback charges the same original
A/B/C/L and retained objects/deadline. Private composition inputs pass those
objects and the just-authenticated layout into complete fallback in the unchanged
snapshot; there is no second A/B allowance or comparison. Complete validation
predicates/default callers are unchanged. Exhaustion yields no partial result.

Unused C1 selected retained readers and their documentation are normally reverted.
Complete retained readers remain unchanged; follow-up repair notes belong in
the PR. Group 7R delivered the separate entry; group 7b wires only mixed
cancellation source/target, legacy source/target/lookup, member capture and
original-owner cancellation-tail selection. Each phase retains its original
allowance and absolute deadline; fallback shares cumulative ledgers within it.
The whole ten-core-table preservation inventory/equality and lookup query_only,
savepoint, total_changes and main/temp schema-cookie checks remain unchanged.
Preservation inventory does not authenticate unrelated payload semantics.
Retained references/uncertain native control-result encodings retain COMPLETE
refusals. Original invocation, dependency and positive-closure guards supply no
clearance from missing handles/negative cleanup. Historical @1 Final consumers
(7c), STOP-1 global certification, all-run returned IDs
and full run views remain complete. Locks/order, schema, deadlines/caps, transactions, audit/replay and
permissions remain unchanged, so no TLA+ transition/model change is introduced.

## Original source outer settlement — THR-404 Group A

Only `sequential_initiator.settle_source_runs`'s final validation call now
supplies the actual source ID. The existing explicit-source graph replays all
same-source members and the full wait set, including earlier SIDs/batches,
settings chains, original native links and consumption receipts. After replay,
the existing validator reserves scalar H for ALL unfinished @2 runs on the
resulting reader's original ledger and deadline. It does not select only the
matching-SID members whose outer proofs were just written.

Sibling or prior-receipt corruption and capacity exhaustion refuse with the
original errors and roll back before publication; disconnected run payload
corruption no longer vetoes this write. Positive/negative original outer
qualification, stale/no-member SID early return, original proof bytes and exact
replay remain unchanged. The mixed terminal-source fence still uses group 7b;
historical @1 source settlement, all-run discovery, full views and STOP-1 global
certifications retain their existing paths. No lock/order, budget, transaction,
authority, audit or modeled lifecycle transition changes follow.
