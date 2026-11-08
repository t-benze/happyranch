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
select or consume the recovery result. Unknown actions, including a
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
Complete companion validation uses bounded binding/native-task/mark acquisition
and cached run/visit relations on the history call’s original allowance, without
per-task inventory rescans. TASK-14576 adds installed selected v2 recognition:
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
validator is admission-only; the unchanged full reader remains the offline
oracle with its known small-org scale limit. See [U3b contract](reusable-workflows.md#u3b-inert-admission-and-first-activation-thr336-msg8595).
Disposable observations are not V3 behavioral acceptance or live enablement.


## Sequential visit callback consumer (U3d-1)

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
no capability/access or settlement proof. Missing U5 proof leaves advancement
unavailable. No queued native launch, caller notification or live enablement follows.


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
