# Task orchestration contracts

> Moved verbatim from the repository-root `AGENTS.md` on 2026-10-30. Sections keep
> their original relative order. Where a later section says it supersedes or
> extends an earlier one, the later section controls; sections marked
> "Historical" are retained evidence, not current instructions.

## Creation-time task ownership (THR320 seq432/433, seq436/437)

The daemon persists one `workflow_task_ownership` record per task in the same
transaction as task creation and its native binding. Ordinary producers write
`legacy` explicitly. Document authors/revision authors use their draft intent;
reviewers use their request; coding stages use their actual attempt, while the
structural carrier and existing source use the admitted instance. Only the
controlled coding-start writer can transition an existing ordinary source.
A mark supplies routing ownership, never execution authority or a review/merge
permission. TaskRecord, model output, briefs and API update fields cannot set it.

OrgState load initializes this additive companion store before producers attach,
after the existing complete compatibility readers. Initialization inventories
instances, instance-task links, reviewer bridges, document draft intents and all
coding runs/attempts, including source/carrier and historical generations. A
positively empty complete inventory permits ordinary initialization. Nonempty
owners get their actual binding once; ambiguity refuses with the selected task.
The table/view and all records commit atomically. Reopen validates existing
records and never fills a missing one again. Missing/uninitialized storage or
records never mean ordinary. Generic Database/runtime-audit stay workflow-free.

Routine classification reads the indexed mark and selected scalar binding
relations through `workflow_task_owner_bindings`. This literal per-layout view
contains no payload or history reader. It detects selected conflicting/dual
owners and local task/principal/run/attempt inconsistencies without schema,
integrity/FK, audit or unrelated-history scans. Errors return
`reconciliation_required`; enqueue logs the existing refusal. After authorization
and org resolution, the completion endpoint first rejects an absent task with
HTTP404 `detail={"code":"unknown_task","task_id":task_id}`, preserving the
original task ID, before classifying ownership. An existing task with missing,
unsupported or conflicting ownership still refuses with HTTP409
`workflow_reconciliation_required`. This existence-only read supplies no admission
authority: subsequent replay, active-task and session checks retain their order,
including fresh ownership/task/session validation at locked writer admission. Historical
document-only roots/links remain owned and recovery-held. Foreign payload-only
claims refuse at their own complete boundary (Founder347), without inventing an
owner for another task. Actual admission, mutation, recovery and compatibility
owners retain fresh complete semantic/authority/native validation.

PID publication classifies even without a coding dispatcher. Only current
ordinary task/agent/SID/in-progress/noncancelled identity reaches the ordinary
UPDATE, with another ownership check inside its writer transaction. A genuine
coding attempt uses its original native PID journal or refuses when unavailable;
all other selections refuse. The five indirect ordinary mutation guards use
this same selected lookup, never `queued_storage` for an unrelated run. Ordinary
update/chain/fanout guard checks and writes share their native writer interval.

Companion storage preserves frozen `_TASK_COLUMNS`, SELECT-* task captures,
encode_row bytes, INTEGER results, SIDs, audit/chain shapes and historical pins.
Complete independent references distinguish task marks separately from document
layout, coding extension and retained `workflow_owner_*` index dimensions.
Offline layout migrations refresh only the companion's literal binding view;
old document/coding DDL, rows and installed indexes retain their original meaning.
Marked databases require a compatible reader, including when empty; source
rollback does not remove the store or grant old-reader downgrade compatibility.

Historical PR1251 exhausted its per-call schema/history allowance on real
ordinary histories, including the reported19-row case. PR1253 rolled that reader
back; PR1254 repaired complete indexed-reference compatibility. Those failures
and installed owner indexes remain preserved. This mark replaces routine
reconstruction; it does not rewrite reader budgets or assert live relief.
Isolated SQLite/static evidence is separate from full workflow behavior and
post-deployment verification. Family Coding stays Disabled; no workflow start,
live initialization, deployment or host lifecycle action follows from source.

Each attachment storage_key can be claimed by one task only, including within
the same tree. Each direct, child or then-leg list allows at most five refs;
keys must be unique across the entire initial decision, including siblings and
nested legs. Carrier top-level refs belong to the carrier; its first leg reads
through ancestry without a second claim. Initial fanout preserves both child
and nested attachment fields; later chain advancement keeps its existing rules.

Recognized initial pre-child errors include too_many_attachments,
duplicate_attachment, task_attachment_not_found, attachment_already_claimed,
invalid_attachment_display_name and unsupported_attachment_type. Feedback gives
the code, escaped key when supplied, and remediation. Correct in a genuinely
new authorized session/result; never replay the consumed result. Each
decision-owning task has at most TWO committed attachment-error feedback
opportunities shared across sessions and initial delegate/fanout/parallel.
Committed A consumes a chance even if B/enqueue is lost. The third recognized
error uses the existing root ESCALATED or nonroot FAILED disposition. A valid
correction after two remains eligible under current authority and revision
limits. Other authoring, revise, slice and total-step rules stay unchanged.
The exact-task append-only orchestration_step feedback audit adds
attachment_correction version1 with opportunity, accepted_result_id,
accepted_session_id, feedback_result_id and error_code. Strict history reads
validate genuine result/step associations; ambiguous history refuses without
repair. One synchronized BEGIN IMMEDIATE checks the ORIGINAL RetryClaim and
history/count, then commits A (feedback/PENDING) or the exhausted stop atomically.
B separately reserves the exact pending fingerprint through synchronous enqueue;
queue insertion is not a durable claim or crash-atomic exactly-once guarantee.
SQLite/OSError/transaction/postcommit failures retain their existing boundaries;
only six exact built-in ValueError shapes at initial validation/preparation
recover. Callback admission, receipt identity/history and once-only consumption
are preserved. Old schema readers do not enforce this new budget policy.
Stop tails use the captured winning owner and recheck before initiation. Existing
external cross-process/cross-await job, cleanup and notification limits remain;
source inspection supplies no behavioral or automatic-startup coverage.
Keep task-only evidence in output/<actual-task-id>, report output_dir and use
supported recall. Do not reattach an already-claimed maker key to a reviewer.
For a distinct attachment handoff upload a fresh authorized copy with
`happyranch attach-upload --org ORG --as-agent CURRENT_AGENT --file /absolute/source`
within the actual authorized environment (upload has no --session-id flag).
Inherited access grants reading, not another claim. Never upload task-only
logs/screenshots/review packets to Shared Artifacts as an ownership workaround.


**Verified retry transactions.** Final single/fanout spawn rereads the original
report claim and recorded retry evidence only after `BEGIN IMMEDIATE` under the
Database RLock. `Committed` alone enqueues children; `InvalidLineage` uses owned
atomic feedback, and `LostClaim` drops without writes. Ordinary revision delta
rolls back with spawn. Retry feedback admission holds a separate no-write writer
reservation through synchronous queue insertion, released on every path. It is
not a durable task claim or an exactly-once queue guarantee. Cross-root provenance
is bounded to 20 root records / 19 recorded supersession edges; failed history
and original parent links stay intact. See orchestrator-contracts.md.

**Delegated retry failures.** An exhausted per-slice retry lineage is durable
causal context for the owning manager, not a runtime `runtime_retry_ceiling`
escalation or automatic successor. Apply the fresh-work rule above when selecting
independent work versus linked continuation. Existing native same-parent/same-agent
checks still require a valid failed-child `revisit_of_task_id` where applicable;
report a refusal rather than inventing provenance or a bypass. A manager may
propose escalation through the existing THR-181 path; completed/superseded
descendants retire historical failed ancestors from causal selection, preserving
their history.

## Root task Pause and Resume (THR-292)

THR-350's narrow ordinary unknown-session exception permits existing startup
failure or periodic flag/TTL recovery only with positive death proof and no
operator hold. Its post-terminal writer records producer_settled while retaining
unknown history; it never certifies cleanup or releases Pause. Ordinary
job-wake provenance stays ordinary, while job-owned entries, workflows,
authority/recovery episodes and other deferrals keep their own owners. See
[the exact lifecycle contract](runtime-lifecycle.md#ordinary-pause-unknown-crash-recovery-thr-350).

TASK-11437's bounded correction adds only coherent serializer child_wait
observations (256/query257 per parent,1000 inspected children/250ms per response,
no refill), exact current completed-report recognition and neutral unavailable
fallback. Delegated alone is no outstanding-child/running proof. EN/ZH ordinary
qualifiers reach list/detail/header/rail/timeline and both actual agent cards;
worse rollups suppress parent reasons. Fanout counts require exact selected IDs/
width and seven literal row categories; prior audit join never proves resumed/
completed parent. Unknown/job detail blockers read descendant cleanup unconfirmed
and retain raw IDs/reasons and fail-closed uncertainty. No Pause UI on lists or
agent cards. Only AgentDetailDrawer's local100vw cap is added; shared Drawer,
providers/routes/config, execution/consumption/wakes/stores remain unchanged.
Backend/protected proof remains unexecuted; finite synthetic frontend observations
are separate from lint gates and behavioral Pause acceptance. No live control,
deployment/restart or historical closure is authorized. See the current Pause spec.


Root Pause/Resume (THR-292) is an org-owned control, separate from TaskStatus/BlockKind. New Pause requires a NULL-parent in_progress root; Pending refuses409 without claiming it. Descendants inherit only actual ancestry. Final producer commitment arbitrates with the hold before ordinary/v2/workflow admission and binding. Committed work and genuine results drain; every retry retains the original SID/G/count/owner and budget and checks again. Unknown host/tree/ACTION evidence remains a blocker, including terminal diagnostics. Only actual ordinary-purpose running sessions (including normal v2-admitted manager sessions) captured by that hold generation retain the unreviewed job exception; founder work-starting origin actions refuse before mutation. Separate manager successor roots remain unheld. Org-only installation leaves generic Database/runtime-audit unchanged and requires a compatible reader for rollback. Read-only overview/CLI/TS contract is in docs/superpowers/specs/2026-10-10-root-task-pause-resume.md; PR2 owns web controls. Python unit source/gates are RETIRED under THR-291 seq40, never PASS; corrected product E2E behavior remains pending after historical failures. Required current CI and independent exact-head review/QA remain.

Pause reads all current non-recovery tracker bindings inside the serialized root/DB writer and keeps the short tracker mutex through COMMIT. Durable positively-running evidence, exact current task/agent/SID and server-admitted ordinary purpose must agree. New or moved bindings are considered there; ended or replaced SIDs are excluded. Advisory pre-request snapshots never decide capture. The tracker mutex is a leaf lock: no binding lease is acquired beneath root/DB, and no await, host/provider operation or backoff runs under these locks. Duplicate Pause preserves capture; Resume followed by a new Pause replaces previous-generation capture.

Unclaimed completion recovery retains the actual original return and SIDs while held, with no running deadline. Its 120-second deadline is pinned atomically with the accepted real admission/claim, binding and launch journal. Preclaim/admission waiting spends nothing; claimed recovery retains its expiry across Resume, duplicates, retry and restart, including committed no-launch failure. Unresolved original host/tree evidence is separately retained under the original session/owner/generation before recovery conversion. It remains an unknown drain blocker after callbacks, terminal lifecycle, restart or quiescent recovery, without owning a new producer. Known-quiescent originals add no unknown blocker, and deferred unclaimed recovery alone is not execution.

The original PR1022 L4 run at `6472d886` retained an internal timeout exit 124,
not complete recovery proof. Its incremental traces identify a missing bound
completion audit for successful human-team leaf recovery, two failed-recovery
reads of a nonexistent supervisor method, and fixture startup fingerprint
refusals. Human-team completed recovery now atomically records the actual
INTEGER result and recovery session in its completion audit; historical
agent-team payloads retain their prior shape. Failed recovery reads shutdown
from the existing admission controller without changing host enforcement.
Test-side compiled-code fingerprints use marshal format 2, retaining full
code comparison while excluding object-sharing reference flags. All 224 C4
parameters and their original behavioral assertions remain. These corrections
are source readiness; final-head hosted execution and the existing held proof
inventory remain required, and no partial result is a feature-readiness claim.

C10 canonical enrollment readback includes the existing AgentDef writer's final LF.
The exact submitted draft/POST, focus/selection and all 89 assertions remain;
four readable draft screenshots do not establish completed 29-row browser proof.


The original PR1022 L4 archive at `18e01cfa` retains parent/command exit124,
45 failed calls, 24 passed calls, two setup errors and one interrupted setup-only
case; it is not complete 224-case proof. Its task teardown emitted the existing
informational `task_scratch_report`, which the human failed-recovery reader had
incorrectly treated as foreign causal history. That exact report action remains
informational. Failed recovery permits only the native task-scoped memory
observations: `memory_added`, `memory_digest_impression`, `memory_search`,
`memory_runtime_intent`, `memory_runtime_identity`, `memory_runtime_expectation`,
`memory_runtime_binding`, `memory_runtime_launched`, `memory_runtime_terminal`,
and `memory_collection_seal`. Agent-scoped memory read/update/promotion/lifecycle
rows are outside this task history; collection acceptance/invalidation controls
are not informational recovery exemptions. The exact native launch/refusal
observation `task_native_launch_boundary` is also informational; it does not
select or consume the recovery result. Session starts accept exactly the native
five required keys, optionally with a nonempty string `reasoning_effort`.
Job audits are separately authenticated, never informational: `job_submitted`
accepts the exact legacy shape or journal shape with `submit_session_id` and
`submission_digest`; `job_auto_started`, `job_run_started`, `job_run_completed`
and `job_run_failed` accept only their exact native shapes and an existing jobs
row owned by this task and worker. Any recorded submit session must be the
original or selected recovery session. Submission metadata must match the job;
the journal digest must equal the native submission producer's SHA256 over the
persisted submission fields, including script bytes and creation time. Every
accepted action requires a consistent persisted lifecycle with aware, ordered
creation/review/start/finish timestamps as applicable. Start audits require an
actual start and a running, completed or failed row, with matching recorded
runner, working directory, timeout and interpreter. Pending rows cannot carry
launch or terminal metadata; running rows cannot carry terminal metadata.
Submission and automatic-start actors are the worker. Legacy internal
`job_run_started` uses `founder` as both actor and recorded reviewer; terminal
actors match the recorded runner (worker or founder). Terminal audits require
the matching completed/failed status and finish timestamp, plus a start/reviewer
and output paths for launched runs. Completed rows require integer exit/duration
and no failure reason. A native prelaunch `spawn_failed` refusal is worker-authored:
no start, review, working directory or output paths, false review requirement,
NULL exit, zero duration and a finish at or after creation. Terminal exit,
duration and failure reason must match the job row. Foreign ownership,
malformed shapes and extra keys refuse. Unknown actions, including a
dictionary-payload `memory_unrecognized_transition`, refuse as
`ambiguous_history` before failure, review, marker, job or parent effects.
Selected result/session/owner, review ordering and replay fences are unchanged.
Fixture jobs are submitted
by the original ordinary child before clean omission, never by the purpose-bound
completion-recovery session. The restart-only no-job observer excludes the initial
marker-interruption launch. Native diagnostic events link fully flushed JSON
without replacing existing events, and actual job-submit refusals are retained
before assertions. All 224 C4 cases and their behavioral assertions remain.
These are source corrections only: no new behavioral execution, GREEN,
causal/restoration/repetition or held migration/restore/reboot proof is claimed.

**Root retry source-thread association.** Task details/lists/roots expose nullable
`originating_thread_id`/`originating_task_id` from complete root revisit lineage;
thread linked-task reads include valid root retries. Literal dispatch provenance
and terminal routing stay unchanged; no backfill or authority follows. See
[the read contract](../../agent-guides/features-and-invariants.md#root-retry-source-thread-association).

Association reads batch shared ancestry inside the selection/serialization
snapshot and refuse aggregate exhaustion with HTTP503, never empty/null success.
Per call limits are 4096 distinct lineage IDs, 8192 fetched rows (including
repeated discovery/summaries), 16 MiB fetched value bytes and 128-ID batches;
existing collection SQL/lock/row-size limits also apply. See the linked read
contract for precise counting, `task_thread_origin_unavailable`/`census_database_*`
refusals and cooperative-bound limits. Complete 200-node lineage remains valid;
201-node/malformed lineage has null derived origin, with literal direct rows
preserved on complete successful reads. No cross-snapshot cache is retained.

## Unattached ownership companion@2 reference (THR336 U2a)

TASK-14536 adds `workflow_sequential_ownership.py` as a standalone reference and
reader. Existing ownership storage literals and v1 writers remain unchanged.
The proposed explicit companion rebuild preserves original columns
and v1 marks and allows version2 only for `seq_source_waiting`, `seq_carrier`,
`seq_visit`; its literal view appends relations without changing old branches.
Selected v1 marks use the existing scalar reader. New marks require complete
matching run/visit/native task relations; absent marks never mean ordinary.
Complete companion validation in the full history reader uses run-scoped
primary-key native-task/mark lookups and cached run/visit relations under allowance
C and the original deadline, without per-task inventory rescans. No binding-view
text is acquired inside C; retained legacy `_inventory` completeness runs under L.
TASK-14576 adds installed selected v2 recognition:
validate exact org/version/kind/owner and binding/native relations. U3a returns
that exact kind from `selected_owner`; `classify_task` exposes it only with an
attached same-org `SequentialDispatcher`, otherwise `reconciliation_required`.
The standalone selected reader returns the exact new kind; v1 routing remains
with the original reader and results. Unknown, missing or inconsistent marks
refuse, never become ordinary. The original complete ownership reader accepts v2
only inside the complete sequential read scope; default full readers retain their
original refusal.
No writer, adoption, allocation or execution permission is implemented here.
Native TaskRecord/SELECT-* encodings, results, chains and audit scopes stay
unchanged. Complete old/new reader composition is source-only; the U2b operator
installer and U3 source adoption remain separate. See [the storage boundary](reusable-workflows.md#u2a-storage-and-standalone-reader-boundary).


## U3a sequential ownership holds (THR336 msg85/95)

`seq_source_waiting`, `seq_carrier` and `seq_visit` are routing selections only.
The OrgState-constructed `SequentialDispatcher` is mirrored to the orchestrator
and database; all its ports refuse or do nothing. No mark/task/run producer,
source adoption, engine, launch, callback acceptance or caller return is attached.
Queue admission and heartbeat explicitly hold these kinds. Recovery and routing
return owned without enqueue or dispatch; ordinary PID publication refuses.
Native mutation guards hold them with the existing refusal codes, including the
sole caller of the unchanged historical Coding guard. Enqueue logs the hold.
Completion keeps unknown-task HTTP404 before classification, then HTTP409
`workflow_reconciliation_required` for sequential owners; cancel uses that same
HTTP409. Job, portability, ordinary result-action and parent propagation gates
continue to exclude non-legacy owners. Missing storage/marks, dual bindings,
read errors and missing/wrong-org dispatcher never become ordinary.

See [the U3a boundary](reusable-workflows.md#u3a-ownership-union-and-holding-attachment)
and [source pointer](../../superpowers/specs/2026-11-08-sequential-engine-u3a.md).


## U3b creation-time sequential marks (THR336 msg85/95)

The inert U3b engine adds `insert_owner_uncommitted`'s version2 branch only for
`SEQUENTIAL_KINDS`; the existing v1 INSERT remains unchanged for every other
kind, including ordinary legacy tasks. `adopt_sequential_source_uncommitted`
requires a caller transaction and updates exactly the same-org source's
`version=1,kind=legacy,owner_id=NULL` mark to `version=2,kind=seq_source_waiting`
with its actual run ID. Exactly one row must change or the writer raises.
Carrier and first-visit native insertion use unchanged Database row primitives,
with `seq_carrier`/run and `seq_visit`/visit marks in the same transaction.

No native task ID, audit scope, active chain/fanout or old mark is repurposed.
Only the selected engine's original source is adopted; all failure paths leave
the graph to the caller-owned authority writer's rollback. This producer is
unattached: U3a continues to hold sequential tasks; no source completion,
callback, launcher, recovery or return caller is added. Admission's selected
validator is admission-only; the full reader remains the offline oracle under
[D2's A/B/C/L allowances](reusable-workflows.md#u2a-storage-and-standalone-reader-boundary)
and one original deadline. See [U3b contract](reusable-workflows.md#u3b-inert-admission-and-first-activation-thr336-msg8595).
Disposable observations are not V3 behavioral acceptance or live enablement.


## Sequential visit callback consumer (U3d-1)

U4a adds unmounted task-session repository preflight records/service only; it
does not change this callback or settlement path. U4b2's internal Developer
path starts resolve-only inside its existing node task, binds repository/origin,
base/scope and verified SHA1 before editing. U4b1 records post-settlement
business refusals as durable Held after positive outer COMMIT; U4b2 separately
records pre-result repository refusals without terminal/result proof. U4a receipt bytes alone neither bind
the run nor change its lifecycle. There is no four-role duty-access checkpoint
or readiness label. Later disclosures/publication/actions still require current
authority/access. See the [U4 split and native ordering contract](../../superpowers/specs/2026-11-11-sequential-coding-u4.md).

The inert observer checks literal no-follow clone/object metadata before Git
and at close. Common-directory/gitdir indirection, gitfiles, object alternates,
grafts, replacement refs and linked/unsupported object topology refuse with
Held / `repository_unavailable`. Config includes and inherited Git overrides
are unavailable; shallow history is allowed. No subject is emitted for a
refused preflight observation; that inert service adds no callback or writer.
U4b1 separately attaches the post-settlement business-Held writer below.
U4b2 adds an internal pre-result subject writer and distinct repository-Held
observation; it never borrows callback/terminal proof or manufactures a result.

Only selected seq_visit joins workflow callback admission/replay. The existing
order remains404, classify, local409, task-active, v2, session, exact idempotency,
callback_admission, locked owner/task/session recheck, native writer. Source/carrier
and cancellation still409; recovery.SEQUENTIAL_HELD_KINDS is unchanged. D5 reuses
the existing coding_admission keyword for sequential capture, without a Database
signature change or Coding budget reuse. Sequential typed refusals map409 at the
CodingStartError position. Legacy/Coding/document projections retain their meanings.

Summary is the exact canonical workflow-sequential-callback@1 JSON STRING described
in reusable-workflows.md, not an agent-authored result envelope. Actual decoded
JSON/document byte limits are49152/1048576; over-limit gives409
sequential_output_limit_exceeded with zero new native/seq DML. Metadata/native
aggregate bounds are16384; summary1414488. Other categorical409 codes are
sequential_carrier_limit_exceeded, sequential_carrier_invalid,
sequential_carrier_storage_unavailable and sequential_carrier_identity_conflict.
Do not echo content. Earlier explicit empty waiting_on_job_ids remains400.

A manually corrected zero-write refusal can use the same still-live SID before
any result committed. After uncertain transport preserve the exact body; no
automatic retry/new SID/result. Exact stored full-projection callback replay stays
read-only after tracker removal; changed payload gives workflow_callback_payload_mismatch.
Fresh ended/foreign sessions cannot author output. Native summaries retain their
existing audience; role recipients are no private-custody guarantee. Claims supply
no capability/access or settlement proof. U3f supplies document-only
recipient and typed witnesses after actual native/outer proof; missing repository
U5 proof still leaves advancement unavailable. Source presence grants no live enablement.


## Sequential native queue owner (U3d-2)

A source-only sequential visit can own the native queue preparation tuple and
Pause journal owner `sequential`. `Dispatcher.worker_started` adds one ownership
classification before the unchanged Coding call; legacy and other kinds retain
that Coding tuple. Selected visit admission requires positive source-outer
evidence; no current producer writes it. Queue finish retains the actual native
preparation's close/release obligations. Heartbeat remains a no-op for all three
sequential kinds.

`route_owned_task` sends only `seq_visit` to the sequential dispatcher; source
and carrier return owned without enqueue or launch. Startup `recover_owned_task`
remains unchanged and holds all sequential kinds. Pause discovery may notify a
queued visit through the same source-outer gate. Normal mutation guards remain
unchanged; the dedicated current-invocation PID port is the only added PID route
and refuses without the original live sequential invocation. Cancellation still
returns409: U3e must attach its cancellation fence before a start producer.
No real launch/PID/callback/settlement or V3 behavior has been verified.

### U3e-2 returned source parent bookkeeping

Only the original `seq_source_waiting` task can wake its ordinary caller after
the unchanged complete reader admits the actual returned event and `run_return`
obligation for that same source. Completed-without-return is insufficient, and
the structural carrier never separately wakes the caller. The existing shared
parent helper retains parked/delegated, sibling/lineage, current-owner and
current-generation gates. Its optional attachment queue is passed explicitly to
both reservation and generation-aware publication; existing callers keep their
ordinary queue selection. The real Orchestrator remains the domain owner.

The terminal return writer projects only the final visit task's retained output
summary/directory, clears source/carrier block kinds and records a bounded run
note, after U3c's unchanged return. U3c remains the status/timestamp owner. No
source result is consumed again and no carrier result/SID is created. Postcommit
history rebuilding and parent wake are replayable through returned-only
rediscovery; this gives no durable acknowledgement of caller execution. Thread
follow-up, SYSTEM messages and invocation minting are excluded. No new command
or bundled start-task instruction is required at this source checkpoint.

`returned_run_ids` first uses the org-layout selector's bounded main/TEMP schema
inspection in the same coherent view. With no `workflow_seq_` object name or
table association it returns `()` without the complete sequential/retained
reader: no sequential returned obligations can exist. Any presence, including
partial or TEMP storage, retains the complete reader and its fail-closed
refusals before selection. `notify_returned` retains its complete validation.


## U3e-1a selected-run cancellation

The existing authenticated task-cancel route retains unknown-task 404 ordering
and its original post-COMMIT tail. A direct nonterminal sequential source,
carrier or current visit uses the selected-run cancellation owner and never
falls through to generic update_task. A cancelled direct target can replay only
its exact original stop request. Unsupported source/carrier point stops, mixed
closures and ordinary ancestors containing sequential descendants refuse with
409 and zero task/event/audit writes. The ancestor guard includes terminal paths
and refuses beyond 512 distinct rows on its one unchanged read budget.

The owned transaction collects prepared/live opaque controls and prior statuses,
fences the run and stamps live selected native rows with one aware UTC timestamp
for cancelled_at, completed_at and updated_at, block_kind NULL and the existing
cancellation note. Existing task_cancelled audits are emitted once per changed
task. Historical rows, original result/SID/PID and Pause ownership are retained.
After COMMIT the existing tail invokes controls outside locks in its original
order. Sequential work never uses bare-PID signalling or tracker clearing.
In-progress ownership or retained preparation without positive original
close/release remains teardown-incomplete even when no control exists or a
control returns successfully. Source/carrier cascade=false refuses remaining
live work; a current visit stop fences the whole selected run.


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

The source must still be the same live, admitted and attempt-committed ordinary
or v2 invocation after positive `host_terminal`: `unsettled` is False, its journal
phase is `result_processing`, and its bounded native terminal observation records
`success` with no cleanup error. Pause generation, pre-step count and brief digest
must still match, with no execution uncertainty or recovery deadline. Running
before host terminal, unknown, passthrough, non-quiescent, survivor, cleanup-failed,
missing-observation, foreign/stale-owner, paused/cancelled and continuation-envelope
states refuse before graph, native-action or journal writes. This admission check
does not supply the separate original source-outer settlement proof.

Run identity stays U3b's identity of org/source principal/operation key. Carrier
and first visit use `TASK-SEQ-` plus SHA256 of canonical `[run_id,"carrier"]` and
`[run_id,"visit",1]` respectively; collisions refuse. The single business COMMIT
parks the original source by changing only `block_kind=delegated`,
`note=sequential-owned:<run_id>` and aware `updated_at`; its status, principal,
SID, result, step and revision remain. Carrier/visit have no invented SID/result.
Initial exact admission replay returns original IDs; conflict or later lifecycle
admission replay holds.

Native task scratch accepts these exact `TASK-SEQ-` identities with 64 lowercase
hex digits in addition to its existing task-ID grammar. If scratch preparation
refuses a sequential held task, the native launch seam records the existing
`task_scratch_containment_failed` audit and returns its failed `ExecutorResult`
without a generic task-note write. The sequential task row and its mutation
guard remain unchanged; this reporting supplies no retry or cleanup authority.

This unit publishes no queue item and has no source-outer producer. The admitted
first visit remains ineligible: `queue_admission_allowed` stays false without the
positive original null-visit source-outer evidence. U3e-1b-ii owns that separate
producer and manager skill instructions. Existing U3e-1a cancellation, U3e-2
caller return, default Disabled admission, repository/mixed U5 successful-output hold,
Pause backout and stale-settings limits remain controlling. Marker removal
refuses the next fresh start; it grants no continuation/activation authority.
Structural fixtures and static lint are not genuine callback/host/launch,
restart, cancellation, return or two-template acceptance. V3/V3-0b/U5 remainder remain
UNVERIFIED; no live install/settings/admission/deploy/restart is authorized.

### Complete-reader recognition and native carrier preflight

The complete same-snapshot reader recognizes the entire closed sequential
callback in `task_results.decision_json`. In `workflow_coding_schema.py`,
`_validate_coding_data` passes its already validated sequential inventory and
original retained budget into `_refuse_coding_metadata`; its specialized type
masks follow canonical column names in the selected projection. In
`coding_start_history.py`, `read_queued` excludes recognized sequential sources
from Coding callbacks/runs/owners and retains only a separate local proof inventory.
Its existing native completion-audit acquisition additionally recognizes a strict,
duplicate-free full report matching exactly one such original result and its
actual task/agent, including any existing INTEGER-result/SID attribution. Native
self-evaluation and every non-decision member retain their residual marker checks.
Matched audits still pass full typed row equality; failed matches remain for the
original discriminator and its first refusal, with all charges retained on the
same budget. There is no general audit-location exemption or synthesized audit
field. Coding returned-only discovery composes `read_org_layout` on its original
connection/budget with the original legacy fallback, before selecting any returns;
notification ownership, routing and queues are unchanged. Successful recognition
continues through unchanged validators: independent corruption reports its own
first owning validator. There is no suppressed-refusal receipt or enclosing-reader
exception, and this additional readability supplies no admission or authority.

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


## U3e-1b-ii source outer producer (ROOT TASK-14640)

A genuine executable team manager may use its own final completion decision `workflow_start` with the unchanged `workflow-sequential-start@1` StartCommand, exact pins, stable key and actual remaining limits. Unavailable venue is a refusal, never setup authority. Thread-originated and nested sources remain refused by the inert adapter; no other callback/route/CLI contract changes.

Admission alone cannot publish the first visit. After the original native invocation settles, the source producer reselects the retained source/run/current first visit and actual completed INTEGER result, authenticates the saved command and checks cancellation inside the existing evidence writer. It uses the existing null-visit outer identity and exact source callback identity. One positive evidence COMMIT precedes the existing notify_queued port; exact replay is a no-op and conflicts roll back. Carrier and source gain no result/SID. Existing visit settlement is unchanged; report-completion remains FINAL.


## U3f document-only task evidence and disclosure

For an all-document immutable definition, U3f derives sorted producer/current-
consumer recipients from definition data and maps them through the original
fixed bindings under fresh current authority. Before rendering, exact selected
refs, native provenance, stored recipients and content hashes must match and be
addressed to the actual selected principal. Native summaries keep their existing
audience; this is no private-custody promise or repository entitlement.

Only settled original produced documents (null verdict) and APPROVE document
reviews (matching verdict, canonical JSON object and selected document subject)
receive their registered typed checks. Reviewers differ from every selected
DOCUMENT producer; a prior contextual review may share the reviewer principal.
Nonempty capability_claims remain held. Repository/mixed graphs stayed held
at U3f delivery; the U4b3 exception below attaches Coding declaration output. Current
authority vetoes without changing deterministic original witness bytes.
Document start context is charged. U4b3 adds the same charged binding context
for code-change composition; other unsupported contracts retain the original
lazy start parse at fresh NextEdge successor selection.

Original callback replay remains read-only. Original c/c+1 transition/return,
purpose-separated keys, prior-CAS replay, no incomplete-pair repair and
post-COMMIT publication remain unchanged. A new sequential refusal preserves
prior native/terminal/outer commits and emits no newly accepted output. No new
callback route, completion DTO, skill staging command or native identity exists.
The bundled skill adds one document paragraph; the carrier stays unchanged.

The [current U3f contract](reusable-workflows.md#u3f--u5a-document-witnesses-and-addressed-delivery)
and [source record](../../superpowers/specs/2026-11-09-sequential-document-witness-u3f.md)
cover per-scope SB-5 and the separate open F-U3f-ENTRY, F-U3f-AUTH and
F-U3f-MOVE follow-ups.
Under R-U3f-SB5-2, node_instruction starts its original reader budget before
fresh capture, charges four snapshot JSON decodes/token walks and captured
profile-mirror/digest-map work, and carries the same allowance/deadline through
admission_writer, `_entry` and document/non-document rendering. Protected capture
configuration parsing remains uncharged (F-U3f-AUTH, owner ROOT
engineering_manager), an existing shared-dispatcher class rather than accounting
PASS. PR-3a removes the non-thread team-move guard, including node_instruction
and sequential_engine admission calls. The earlier R-U3f-SB5-3/F-U3f-MOVE
checkpoint records historical uncharged parsing. PR-3b removes the scoped
thread/coordinator readers while Founder settlement,
delivery-release permission, R1 retry and nightly-sweeper readers remain reserved.
U3f C1 used document-only digest deferral; U4b3 extends it only to
code-change composition: terminal/outer serialization occurs only
in the document or supported code-change branch, each prepaid at `48 * 16384 + 512` bytes and one step
on the original budget. Unsupported non-code hold/foreign-result scopes retain zero such
serializations; supported code-change scopes prepay the same proof copies. No duplicate configuration acquisition is added;
any NEW uncharged work outside the three disclosed classes requires STOP to ROOT.
Structural SEEDS are not genuine
callbacks or M1/V3 behavior; Coding/U4/U5 remainder/native recovery remain held.


## U4b1 sequential post-settlement refusal overlay

After genuine callback/native terminal settlement and positive outer evidence
COMMIT, the sequential owner catches only its finite typed business advance
refusals after rollback. An independent non-authorizing native DB writer checks
the original task/principal/SID/INTEGER, successful terminal/positive outer,
callback identity and unchanged CAS/last-event before atomically appending a
closed hold observation and marking visit/run Held. Cancellation or a settled
competing transition wins. Held preserves accepted-result NULL and the entire
event prefix, with no output, successor, return, retry or additional outbox.

The complete and selected readers authenticate cause, original identities,
proof digests and selecting event before recognizing this overlay. Exact outer
redelivery acknowledges saved IDs/cause without fresh access or advancement.
Unknown/mismatched/conflicting observations and failed persistence still error;
no false durable Held is returned. The cause records accountable-manager next
owner and no-automatic-retry/resume-unavailable limitation.

The coding tail observer still precedes the sequential one in unchanged
`task_pause.settle_live`; `run_step` retains its original finally/real-error
aggregation. Callback validation, document composition/limits/witnesses and
agent-facing instructions remain unchanged. See the
[finite causes and U4 status](reusable-workflows.md#u4b1-post-settlement-business-held-thr336--root-task-15144).
Scratch retained-snapshot diagnostics are not native/V3 acceptance; genuine
concurrency and Pause-tail execution remain NOT RUN.


## U4b2 pre-edit subject binding

The selected code-change node's trusted supplement is resolve-only until one
successful U4a receipt commits to the run-level preflight slot. An explicit
internal worker-thread bind observes the exact current SHA1 clone outside its
transaction, then revalidates actual live task/principal/SID, current authority,
configuration and selected run pins in the short writer. It returns the original
receipt only after successful COMMIT; rollback releases no editing phase.
Correction visits inherit the original subject, never a latest repository.
This is the repository/base/scope boundary, not the removed four-role duty check.

The bounded closed subject reader and both complete/selected readers validate
original issuer/native identity, canonical bytes/digest and pins. All native
provenance columns remain NULL before and after later genuine results; no fake
INTEGER, task or session is allocated. Different target/origin/base or expanded
scope, and unsupported preflight, use a distinct non-authorizing repository-Held
observation with finite cause and accountable-manager new-run action. Exact
replay preserves original bytes/IDs; no automatic retry/resume/output/successor
or event-prefix rewrite follows. Cancellation is the only later lifecycle event.

The authenticated original live task/principal/SID may still commit its canonical
negative `BLOCK` callback under current authority, with empty content/claims and causal
findings. Success-shaped callbacks and foreign sessions refuse; exact full-payload
replay retains the one original INTEGER result. Original safe terminal/positive outer
settlement drains without sequential acceptance: run/visit stay Held, accepted result
stays NULL, and no output, successor, editing release, resume or retry follows. Native
completed/failed status is retained; a blocked callback keeps its blocked
result/classification and closes the native task as failed after safe terminal proof.
Cancellation wins over fresh callbacks/settlement; an already committed callback may
only replay unchanged. The immutable subject, hold bytes/cause and event prefix are
preserved.

The repository-Held callback drain preserves callback/terminal/outer ordering.
No native preparation, execution enforcement or public transport changes. U7 owns
the unavailable adapter; U4b3 below attaches successful code-change
declarations/current-authority disclosure. U5 retains review/QA capability and merge. Disposable synthetic-native
scratch diagnostics are not full native first-edit/callback/correction/V3 proof.
Details: [U4b2 subject contract](../../superpowers/specs/2026-11-11-sequential-coding-u4.md).


## U4b3 current-authority publication and addressed disclosure

Founder THR-336 msg131 ACCESS-1 (PRD v1.0.4 update tracked in THR-351) controls
prior access-observer wording. Agents share one OS user and one gh credential;
per-agent effective repository access is not independently observable by the
runtime. Daemon checks current eligible/active fixed principals and unchanged
U4b2 subject only, without GitHub/network I/O/fetch, credentials or permission
changes. Original local clone metadata preflight and native settlement stay
unchanged. Selected reviewers/QA prove access through their own exact-head fetch;
manager expected-head non-admin guarded merge remains U5. Failed operation ->
BLOCK -> Held, distinguishing denied from unverified.

Post-outer code-change composition now validates the closed bounded declaration
and current epoch against the original subject, genuine native result and
terminal/outer proofs. Claims never authorize checks. Malformed declaration,
subject/epoch mismatch or authority loss rolls back and uses the U4b1 finite
Held writer with zero output/check/successor publication; failed COMMIT grants
nothing. At every downstream Coding node_instruction, fresh owned authority and
original addressed selected refs/recipients are checked before returning bytes.
Positively unavailable/ineligible binding is denied; inability to establish
current authority is unverified. Both block disclosure without editing bindings,
recipient set or selection. This slice does not produce/disclose U5 outputs.
Document behavior and 1 MiB document / 49152-byte JSON limits are preserved.

See the [U4b3 declaration and evidence contract](reusable-workflows.md#u4b3-coding-composition-and-access-1-thr-336-msg131).
Disposable synthetic diagnostics are not genuine native/provider/V3 acceptance.
No public start, U7 transport, live install/configuration or action is enabled.


## U6a mixed task history, without cancellation attachment

The explicit complete/selected mixed-history interfaces close native parent/child
and workflow-member dependencies through terminal tasks under the original D2
allowance. They retain exact task values and INTEGER result identities, without
interpreting callback content, native ownership or current authority. They are
inert history readers; their inventory is not a cancellation write set. The
existing ordinary-ancestor mixed refusal and direct selected-run cancellation
remain unchanged. U6b owns mixed cascade/audit/CAS/native-tail integration, and
U5b2b2/U7 remain separate. [U6 source contract](../../superpowers/specs/2026-11-11-sequential-coding-u6.md).


## U6b1 producer interface; Founder attachment still U6b2

The unmounted `sequential_mixed_cancel.cancel_mixed` can atomically cancel a
normal live ordinary/new-sequential write set after complete mixed-history
validation. Terminal dependency rows are retained; live retained document or old
Coding work refuses. Ordinary point stops still refuse sequential/Coding-dependent
ancestors. It neither replaces nor changes the existing task-cancel route.
U6b2 still owns live retained-family writer composition, cross-family overlay
closure, Founder route attachment and native-tail consumption. See the
[precise U6 split](../../superpowers/specs/2026-11-11-sequential-coding-u6.md).


## U6b2a one-owner interface; mixed route remains unavailable

The unmounted original-owner native-tail disposition reader validates complete
mixed history and actual native observations for ONE original source/visit.
It allocates no task/result/event/control and clears no ownership. A qualified
owner cannot establish whole-root clearance. The existing Founder route still
uses direct `cancel_selected`, `guard_ordinary_ancestor` and the original old
Coding/ordinary/document owners; nobody newly reaches U6b1 `cancel_mixed`.
U6b2b must compose live retained-family writers/cross-family overlays, aggregate
all original owners and attach the complete route/native tail. Hidden live work
and coding-dependent ordinary point stops still refuse. See the
[U6 remainder](../../superpowers/specs/2026-11-11-sequential-coding-u6.md).


## U6b2b existing Founder-route composition (TASK-15349)

Superseding the earlier unmounted U6 statuses, `cancel_task` routes fresh
sequential stops and ordinary/Coding ancestors with sequential descendants to
`cancel_mixed`. Exact cancelled selected replay and ordinary-only/document-only
paths retain their original owners. One outer writer reuses native family
commands/events and sole live task overlays, validates complete target history,
then returns opaque Stop controls for delivery outside locks after COMMIT.
Hidden live work and family-dependent ordinary point stops still refuse.
Original committed callbacks only drain unchanged. There is no new endpoint or
Retry/Resume/Reassign/cleanup transport. The original root captures go through
all-original-owner qualification, never one-owner clearance; ordinary/document
positive-proof unavailability stays UNSETTLED as specified by manager Option B.
See the [current U6 contract](../../superpowers/specs/2026-11-11-sequential-coding-u6.md#u6b2b-mounted-composition-task-15349).


## U7d ordinary sequential-start completion (TASK-15578)

The unchanged sequential StartCommand is consumed only through its executable
team manager's own ordinary `workflow_start` completion. Sequential intake now
requires the CLI label with no browser Origin, and under the original binding
lease compares supplied identities to the actual tracker org/task/principal/SID,
rejects recovery and requires completed status without a job wait. The label is
not a credential. The existing venue/settings/pin/ancestry precheck precedes
result insertion. The existing result consumer derives the positive INTEGER
from the committed native PK/full report and authenticates the original native
invocation before atomic native-action/graph/source parking. Pending replay after
tracker clear also selects the saved sequential decision and compares the full native payload, including when the incoming action changes.
Exact full replay keeps original bytes/IDs; changed body/key reuse refuses and
transport loss never authorizes reissue. Cancellation, original limits and positive original host/outer
settlement before first-visit release remain with their unchanged owners.

Public live fresh start remains OFF: both intake and consumer still require the
unchanged disposable-venue guard, which refuses the live daemon home/aliases and
absent/invalid/nonmatching runtime/org/DB-inode marker. U9 owns atomic new public
attachment/release selection plus old-fresh-start fencing; no flag shortcut or
historical engine fallback is added. Historical start@1/@2, original return and
source/thread association, U7a/U7b/U7c, Pause/U6 and LOCAL Final/receipt uncertainty
are unchanged; thread sources still refuse, notifications remain excluded.
No new route/schema/credential/permission/access observer or daemon network I/O.
See the [current U7 source and evidence limits](../../superpowers/specs/2026-11-11-sequential-coding-u7.md#u7d-ordinary-source-completion-consumer-task-15578).
