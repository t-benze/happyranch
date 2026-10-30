# Task orchestration contracts

> Moved verbatim from the repository-root `AGENTS.md` on 2026-10-30. Sections keep
> their original relative order. Where a later section says it supersedes or
> extends an earlier one, the later section controls; sections marked
> "Historical" are retained evidence, not current instructions.

## Task-local ownership classifier reverted (issue: PR1251 refusals)

PR1251's task-local classifier is reverted: its single per-call budget was
exhausted by real schema metadata plus ordinary task audit history, so nearly
every task classified `reconciliation_required` and was silently not enqueued,
claimed or resumed. `classify_task` again uses the complete reader path. The
`workflow_owner_*` index recognition and installer in `workflow_schema.py` stay,
because orgs loaded by PR1251 already carry those indexes and the old exact-layout
reader would otherwise refuse them. `enqueue_task` now logs a warning when it
skips a `reconciliation_required` task. Any redo of task-local classification
needs budgets sized against real task history.

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
