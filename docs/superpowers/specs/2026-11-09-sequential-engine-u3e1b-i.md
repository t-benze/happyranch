# U3e-1b-i inert sequential start adapter

Authority: engineering_manager ROOT TASK-14640, THR-336 Founder msg85/msg102;
TASK-14730 design, accepted D1–D4 and U3e1b inert-adapter split; accepted
TASK-14752 B reader-seam design and TASK-14807 native R1 rev4 design
(SHA256 cdba8414e8d47004ebcb618832a56c0bfa517e7fc64e3aa719676822b2dc4216),
independently reviewed by TASK-14810 and feasibility-assessed by TASK-14811.
ROOT's rev4 acceptance releases implementation TASK-14812 at fetched base
42524d4f2df2ad037fa38ec6db4b7487567462af. Prior failed work and spent allowances
remain preserved; design approval is not implementation review or behavioral QA.

Radius: new sequential_start.py and pure sequential_start_metadata.py; only
NextStep._coding_start_is_closed, submit_completion sequential seams,
CompletionBody.decision `then`/schema import (literal `if` preserved), and the
sequential branch of _consume_completion_report. Accepted B additionally permits
only workflow_coding_schema._validate_coding_data/_refuse_coding_metadata and
coding_start_history.read_queued signature/discrimination with minimal imports.
Three maintained guides and this new spec supply parity. Enclosing
workflow_schema.py/_read_workflow_schema, Coding parsers, native model definitions,
StartCommand/definitions, DDL/D2/engine/caps/authority/venue/dispatch/cancel/return/
skills/CLI/Web/hooks/CI and stale tests/contract/openapi.json stay unchanged.

## U3e-1b-i sequential start adapter

This source checkpoint attaches the closed `SequentialStartDecision` to the
existing completion seam: exactly `action=workflow_start` and the unchanged
`workflow-sequential-start@1` StartCommand. Only that exact format selects the
sequential adapter; Coding @1/@2 keep their existing validator and interpretation.
`CompletionBody.decision` advertises an `anyOf` with the unchanged Coding schema
first and the closed, fully inlined sequential schema second. The existing `if`
selector and `dict | None` transport stay unchanged; JSON Schema extras do not
perform runtime validation. The historical OpenAPI snapshot predates the Coding
schema extra and is retained without unrelated regeneration.

Fresh callbacks retain 404 → ownership classification → unsupported-owner 409 →
task-active ordering, then the existing manager/session/native-policy gates.
The exact sequential decision needs an actual executable team manager's `task`.
Before committing its callback, the route freshly checks the outside-live-home
venue marker and coherent current Allowed settings, definition/pins, bindings,
lower limits and source ancestry. Refusal is 409 `sequential_start_unavailable`
with zero callback/business writes. Malformed sequential decisions use 422
`sequential_start_invalid_decision`; wrong manager/source uses 409
`sequential_start_manager_result_required`. An adopted source accepts only its
original quartet and complete saved completion projection as read-only replay;
changed/fresh source callbacks refuse.

The consumer authenticates the actual INTEGER callback before another fresh
venue/read-only precheck and authority capture. Authority → teams → database
locks precede the sole admission writer. It revalidates the RetryClaim, actual
native invocation, current executable manager, noncancelled ordinary ancestry,
absence of chain/fanout/job wait and independent bindings. Thread-originated or
sequential-owned sources/ancestors refuse. D1 original limits are computed inside
the writer from the exact pinned settings snapshot; native allowances are never
replenished or converted. Native v2 spend/claim/ack uses only existing uncommitted
APIs and rolls back with the unchanged U3b admission/activation graph.

Run identity stays U3b's identity of org/source principal/operation key. Carrier
and first visit use `TASK-SEQ-` plus SHA256 of canonical `[run_id,"carrier"]` and
`[run_id,"visit",1]` respectively; collisions refuse. The single business COMMIT
parks the original source by changing only `block_kind=delegated`,
`note=sequential-owned:<run_id>` and aware `updated_at`; its status, principal,
SID, result, step and revision remain. Carrier/visit have no invented SID/result.
Initial exact admission replay returns original IDs; conflict or later lifecycle
admission replay holds.

This unit publishes no queue item and has no source-outer producer. The admitted
first visit remains ineligible: `queue_admission_allowed` stays false without the
positive original null-visit source-outer evidence. U3e-1b-ii owns that separate
producer and manager skill instructions. Existing U3e-1a cancellation, U3e-2
caller return, default Disabled admission, U5 access/successful-output hold,
Pause backout and stale-settings limits remain controlling. Marker removal
refuses the next fresh start; it grants no continuation/activation authority.
Structural fixtures and static lint are not genuine callback/host/launch,
restart, cancellation, return or two-template acceptance. V3/V3-0b/U5 remain
UNVERIFIED; no live install/settings/admission/deploy/restart is authorized.


## Evidence boundaries

C1–C6/C10 cover closed grammar/schema parity and manager role, callback order and
zero-write marker/settings refusals, consumer recheck, atomic graph/native-action
rollback, original replay/conflict/later hold, D1 lower/exhausted limits, nested/
thread refusal and absent-outer queue barrier. Observations use real production
objects with labelled synthetic data only, outside passwd home in canonical
own-UID0700 scratch under the current ROOT brief; no daemon/worker/host/job/proxy/monkey-patch. Evidence
belongs in TASK-14812 output, with historical evidence attributed at its original
source/head; no retired unit/test harness is added. Scope A uses ROOT
exception <=4608 rows with same-layout invariance and no production exhaustion.
A non-row and all other individual scopes keep 50% thresholds (33554432 bytes,
4096 rows, 32768 steps for ordinary scopes and narrower retained native scopes).
Uninstrumented candidate complete-reader max must be <=2.5s. Outcome-matched
base/candidate pairs must stay within base max + max(10%,0.1s); newly readable
subjects use matched ordinary controls. Subject versus base-control is report-only,
"new work, not a regression measurement". Profiled elapsed is not a timing gate.
Missing measured proof is reported explicitly, not zero or PASS. Required automated checks are
Source Boundary Lint and Web Lint (Node24), observed once at final head without
trigger/rerun/wait. Python units RETIRED; general integration SKIPPED THR243seq42;
Product E2E not triggered. Independent full review, same-head scoped QA and
manager guarded merge remain; maker supplies no review/QA/merge verdict.

### Complete-reader recognition and native carrier preflight

The complete same-snapshot reader recognizes only the entire closed sequential
callback in `task_results.decision_json`. In `workflow_coding_schema.py`,
`_validate_coding_data` passes its already validated sequential inventory and
original retained budget into `_refuse_coding_metadata`; in
`coding_start_history.py`, `read_queued` applies the same recognition before
excluding a sequential callback from Coding parsing. Each original discriminator
runs first. Failed strict recognition raises that exact original first error
with every charge retained. Successful recognition continues through unchanged
validators: independent corruption reports its own first owning validator.
There is no suppressed-refusal receipt or enclosing-reader exception.

The command must match exactly one complete definition pin and historical
settings generation/digest/bindings in that snapshot; a referenced run must
match its actual INTEGER result and original command bytes. Recognition exempts
only outer `action=workflow_start` and the matched
`workflow.definition.identity=release/happyranch/coding`. Every other member,
including the native self-evaluation carrier, keeps the original reserved-value
scan. Other locations, forged context, malformed/duplicate envelopes and mixed
markers remain refused. Pending callback readability supplies no admission or
current authority. Genuine Coding @1/@2 keep their original parsers.

Before native carrier validation, the pure helper walks the unchanged native
models' declared closed field annotations. Each visited schema/value node
prepays 512 bytes, each model object another 4096 bytes, on the original budget.
Wrong-shaped list members refuse before native model construction; valid lists
are charged element by element without a new cardinality limit. The strict
parse reserves `20N + 4096K + 768(T+1) + 1024` per actual parse (raw length N,
opening containers K, lexical tokens T); original canonical comparisons reserve
`12N + 4096` and each examined definition 4096 bytes. Failed work remains spent;
there is no replacement allowance, refund or cross-call cache. These accounting
terms do not certify hard RSS or wall time.

Finite structural acceptance covers the pinned 16/3/2 run limits and 15000
ordinary tasks. Scope A uses the ROOT exception: at most 4608 rows, invariant
within each layout (I4198/J4492); its schema-row headroom is 3994/I and 3700/J,
and schema growth consumes that allowance. A non-row dimensions and every other
individual scope retain their 50% thresholds; native caps and units are
unchanged. Instrumented elapsed is report-only. Uninstrumented timing uses
warm-up plus five samples; relative comparisons apply only to outcome-matched
pairs, with matched ordinary controls for newly readable sequential subjects.
Authentic native-v2 spend/claim/ack plus graph rollback and genuine host/outer
settlement remain UNVERIFIED; labelled fixtures do not close those gaps.
