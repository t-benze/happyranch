# Online consultant move — approved bounded contract

Founder THR-296 seq135 (2026-10-14T05:17:20Z), “@engineering_manager start the implementation”, approves the complete online plan delivered at seq130 by TASK-11307. New sole implementation root TASK-11345 owns serial B, C1, C2, C3, D, E and F. A approval is satisfied, including ONE additive org-DB `team_move_operations` table and affected-agent admission/deferral/forward-recovery/startup compatibility. Historical planning-only/pending-approval text is superseded for this implementation. It supplies no deployment, restart, shutdown, live DB operation or actual move authority. The old TASK10953 two-correction limit belongs to the deferred offline work, not this root.

## Source status and serial ownership

Slice B implements the private durable store, closed request/state/progress/receipt contract, optional layout validation and independent whole-release schema reference selection. C1 binds the org-owned coordinator to actual task allocation, preparation and final Pause/host commitment; its projection remains diagnostic. The installer is explicit and org-only; production startup never calls it. Its only external caller in B constructs disposable release-reference databases. Existing installed optional layouts are validated without installation or row rewriting. Generic Database/runtime-audit remain extension-free.

E now wires founder preview/apply/show/recover routes/CLI and bounded daemon ownership; the current public E section below supersedes earlier checkpoint absence statements. There is no enabled apply or available-feature claim. C1 task, C2 thread and C3 remaining native producer/writer admission exclusion are active in source whenever a valid durable hold exists. `APPLY_AVAILABLE=False`; General store transitions refuse `COMPLETE`; D supplies its private native readback/receipt leaf. Public apply remains unavailable until E/F acceptance. Source/static acceptance is separate from behavior, activation and live-operation acceptance. A table alone cannot make a prefeature reader recognize a hold.

| Serial unit | Required closure; source status |
|---|---|
| C1 | Task allocation/claim and preparation, causal task closure, workflow producers/final-launch, Pause/tracker/host retry ownership: integrated in source; behavioral evidence UNEXECUTED. Installed-layout legacy unowned producers refuse before claim/preparation. |
| C2 | Thread preparation/final claim/launch, all purposes, durable deferred obligations/attempts, probe admission, census and captured running-turn drain seams: integrated in source; behavior UNEXECUTED. Full operation startup and containment recovery remain D. |
| C3 | Dream/wake/schedule/job preparation/final launch, held native recovery/redelivery, complete causal job/remote/workspace census and roster/enrollment/profile/skill/name/policy/cleanup writer reservations: integrated in source; behavior UNEXECUTED. No remote launch engine is added. |
| D | Private native preview, canonical files/cache, audited resets, context/skills, one profile batch/publication owner, bounded recovery, early diagnostic startup and launcher protocol: integrated in source by D; behavior UNEXECUTED, E/F remain gated. |
| E | Founder-controlled API/client/CLI, private request custody, diagnostic recovery/lifetime wiring and supported runbook: integrated in source, APPLY_AVAILABLE=False; behavior UNEXECUTED. |
| F | Root whole-diff/inventory review, independent scoped QA, guarded merge and supported activation/live handoff: outstanding. |

## C1 task and host contract

One coordinator belongs to the org DB owner, bound by Orchestrator to the server TeamsRegistry. Allocation and claims validate optional installed state in their actual transaction; absence preserves ordinary operation and malformed/unknown presence refuses. No production installer or move writer is added. Closure includes assigned/default manager, preserved task team, parents/descendants/revisits, declared chain/fanout agents and immutable workflow bindings. Allocation cannot cross a held closure, including a cross-team child that could wake the old manager. Existing pending/paused/job-blocked/recovery/workflow work is retained as a blocker; no task, result or attribution is cancelled/relabelled.

TaskInvocation reserves preparation before role/prompt/permission reads. The host request carries the server-selected permanent agent ID and the current actual attempt reservation through 429 copies, retaining earlier uncertain attempt reservations separately; no logical-ID parsing or display-name authority. Held tasks are deferred outside host FIFO/capacity. Final launch revalidates reservation and durable task closure in the Pause transaction while the existing terminal mutex still owns the first-wins launch decision. A prepared prompt is discarded on deferral; later dispatch rebuilds context. Installed-layout task/workflow producers without the trusted preparation/Pause owner refuse rather than adopt an already-built prompt. Supervised passthrough and absent-layout legacy execution retain unknown tree evidence after provider return.

Lock order is existing profile/workflow writer leases (outside synchronous gates), task session binding lease, root Pause gate, short move gate, DB RLock/claim transaction, then host terminal mutex. Standalone task allocation/claim decorators enter move gate before DB. Already-owned nested graph transactions check the durable hold directly under DB without acquiring move gate beneath it: their commit and the operation writer's reservation commit serialize on the same DB owner. No await, filesystem, provider/network execution or publication-lease acquisition belongs in these short sections. Terminal hooks/settlement use DB ownership only and never reacquire move gate beneath tracker/terminal locks.

Preparation ownership spans retries, actual callback consumption, tracker cleanup, workflow reconciliation and the outer worker terminal batch. Verified containment receipt with no survivors/cleanup error permits settlement; missing tracker, task termination, provider return, passthrough, failed abandon or failed callback hook does not prove quiescence. Unknown ownership remains an actionable reservation plus the existing durable Pause/intent evidence; no TTL clears it. Genuine completion recovery gets its actual new SID and ordinary budget while retaining an uncertain original reservation separately. INTEGER result/callback identity, Pause captures and immutable workflow pins are unchanged.

Move admission deferral propagates through the inner launch handler to the existing
pending-work owner without task failure, parent wake, followup or callback consumption.
Malformed installed state retains its actionable admission error. Direct 429 and
supervised passthrough retries preserve each returned uncertain attempt as a separate
Pause journal/reservation blocker; the next actual attempt keeps the original retry
budget, session and enqueue age and rechecks durable admission before FIFO/commit.
Provider return never proves containment. Durable Pause attribution remains through
workflow reconciliation and the actual outer cleanup batch. Its explicit disposition
distinguishes safe no-action/preservation from halted, unreaped, action/report failure
or unknown teardown; uncertainty retains the exact task/session owner and cleanup
facts, without changing the normal terminal result or expanding cleanup authority.
Retry attribution uses the existing entry id, task/agent/SID, owner identity,
fingerprint, generation and retry fields; the retained reservation adopts that
entry id only after journal COMMIT. The closed Pause journal format is unchanged.
Failed-tail diagnostics append to existing `cleanup.suffix_lines` within its
65536-byte UTF8 allowance, preserving earlier lines; excess new diagnostics are
explicitly truncated and logged. `execution_unknown` retains ownership independently
of diagnostic capacity. These lines are diagnostics, never quiescence proof or
permission to replay an unknown entry. A refused save retains failed ownership
and propagates unless an original exception is already unwinding; then that
original exception carries the settlement failure note and log. Every collected
outer-tail owner is visited even if another settlement fails.

The task-specific deferred notification seam retains tagged metadata and can redeliver through the existing generation-aware/deduplicating queue owner only after durable release. It does not consume result/callback tokens, allocate tasks, release the operation or recover startup. D must join persistent task/Pause/workflow, session/host/remote and writer evidence; an empty process-local reservation registry is never clearance. C2 below connects thread claims/delivery/drain/breaker/probe owners; C3 below supplies the remaining native producer/writer closure and remote evidence census; no new remote launch engine exists. D's whole-operation release, startup/partial-roster recovery and supported old-reader/launcher downgrade guard remain absent. APPLY_AVAILABLE stays literal false, with no apply/COMPLETE writer.

C1/C2 review/QA uses the plan section8 SOURCE/POLICY/STATIC acceptance. Race, hold/deferral/redelivery, callback/quiescence, recovery/startup and compatibility behavior remains UNEXECUTED; integration SKIPPED THR243seq42. No new unit/integration cases or fixtures, retired units, ad hoc E2E, protected experiments, synthetic/live tasks, DB installation/migration, move or activation is authorized by this source slice.

## C3 remaining producer and writer contract

`ProducerMoveAdmission`, owned by the same org coordinator, resolves dream/wake/schedule/job rows to permanent agent, org, source, occurrence and owning SID before AgentDef/prompt/model/skill/context preparation. Native schedulers reserve affected preparation before routine reads and refuse before minting due work or consuming missed-slot/expiry bookkeeping. Schedule claim and missed rules share the short gate and original DB lock; dream/wake inserts and claims independently recheck under their actual DB owner. No gate spans filesystem work, await, throttle sleep, provider execution or publication-lock acquisition. Unknown installed/unowned layouts refuse; absent layouts remain supported without installation.

Running/claimed work blocks the later operation. Pending native rows survive a hold. Safe deferred re-entry keeps the same SID, original retry boundary/schedule/ordinal/enqueue age, and rebuilds current context; unknown attempts cannot be automatically adopted. Dream/wake startup deferrals retain the original startup-policy flag until ordinary reevaluation; timestamps, recurrence, windows, catch-up and normal missed-slot rules are unchanged. Queues deduplicate queued and running notifications, releasing notification ownership in all tails. Existing timers and the explicit release seam rediscover safe durable pending rows only after release. Recurring schedule lifecycle identity derives from its native row's ID/fire_count/fire_at, preserving every older occurrence and uncertainty. Historical schedule.team is creation attribution: actual callbacks resolve current membership by permanent ID. Explicit retired-team references in content and unsupported schedule kinds fail closed with owner remediation; no text is silently rewritten.

The host recognizes a separate private C3 final-commit protocol, preserving C1/C2 protocols and immutable retry copies. A held request stays outside FIFO. The final gate composes native identity/evidence validation with the host terminal leaf and durable possible-launch COMMIT. Executor-pool ownership reaches BOTH actual builtin/custom Popen paths, passthrough and direct429 through an explicit ContextVar; real on_started and terminal observations remain distinct. Missing/direct/passthrough receipts are not process-tree quiescence. An earlier uncertain attempt survives later clean results. No synthetic callback, failure, cancellation, slot consumption or old-prompt replay substitutes for deferral.

Job creation and automatic admission checks use the real task/session/job owner, including Pause submission provenance and full causal task closure. Actual local subprocess launch rechecks and commits its reservation after scratch preparation, before asynchronous creation. Native job terminal/settlement remains able to record real results; direct exit alone keeps tree cleanup unproven. An already admitted safe no-launch deferral retains its exact native runner closure and is redelivered by the existing timer, without changed request or replacement identity. Lost ownership after process loss requires D reconciliation, never blind approval/relaunch. The job-resume bridge retains actual trigger/job metadata through C1 generation-aware task admission.

C3 census scans all native rows and audit histories without presentation limits, joining complete task lineage/chain/fanout/workflow closure, job waits/resume callbacks, actual active session bindings, remote attempts and causal workspaces, pending source enrollments, profile/publication operations and native writer tails. Terminal rows, missing trackers and a remote terminal digest do not prove quiescence. Leased/recreate_pending/uncertain workspaces, missing ownership, unresolved remote cleanup and opaque bindings report bounded code/ID/remedy records. Remote persistence remains staged; C3 adds no network/remote execution engine, cancellation or settlement writer. D must compose C1/C2/host and these observations under its own final decision; this census does not release a hold or exempt/steal its publisher journal. Pending enrollment filesystem capture precedes the DB interval and a writer epoch change requires recollection.

Roster/enrollment/charter/profile/model/executor/repository/name/org-settings/skill/policy/cleanup writers register their native ownership and affected IDs plus source/target membership before mutation. Existing profile→workflow→roster and shared skill-store synchronization remain. No extra async lock is acquired or nested. Global snapshot-input writers reserve that input; known unrelated scoped consumers can continue. Actual same-owner terminal/compensation/evaluation/session binding writes remain admitted and cannot launch new work. The existing human-failed-leaf recovery caller selects an explicit internal settlement interval; its already-owned DB leaf never takes the move gate in reverse order, and original task/v2 allocation/final-launch checks remain. An already-open unrelated SQL transaction cannot be adopted or committed for C3 writer evidence. Workflow/profile intervals keep their native publication journals; no takeover/reset or permission-generation change is introduced. D's canonical reader boundary is `WorkflowAuthorityCoordinator.capture_canonical_snapshot` and its native coherent publication/cache verification, not a new partial-roster reader or unrelated read rewrite.

Closed version1 C3 producer and native-writer evidence uses only existing audit_log. Serializer→strict actual json.dumps bytes→append/history/owner validator→uncommitted audit insert→native COMMIT precedes in-memory publication. Duplicate/unknown fields, unsupported versions, identity/budget/ordinal replacement, attempt removal, rewritten settlement or erased uncertainty refuse. Producer snapshots retain at most64 immutable attempts and actual persisted bytes are bounded16KiB; census/history scans have no success-producing cap. Writer active/settled/unknown records identify actual native reservations, not task/results or founder authentication. Save failure retains live/unknown ownership and preserves a primary exception. Early native dream/wake/schedule/job reapers preserve held or lifecycle-owned rows under their real DB synchronization instead of fabricating restart failure. Complete early attachment/partial-canonical recovery, old-reader/launcher guard, D operation/reset/context/publication, E public API/CLI and F root acceptance remain absent. APPLY_AVAILABLE=False and no COMPLETE writer persist. Source cannot control old running binaries or out-of-band writers.

All C3 behavior, race, callback, retry, process cleanup, compatibility, migration and recovery observations are UNEXECUTED. Reuse the same thirteen section8 four-answer records; no new cases/parameters/fixtures/campaign. General integration including callback is SKIPPED THR243seq42; Python units are RETIRED. Independent full-diff review, SOURCE/POLICY/STATIC QA, lint-only local all/two hosted checks, normal hooks/nonforce publication and manager guarded merge remain. No activation, live operation, duration or feature-complete claim.

### C3 native correction contract

C3 correction preserves one native schedule runtime SID in materialization,
actual builtin/custom LaunchSpec and executor.run, including contained/passthrough
and retry/redelivery. Provider resume/result identity stays separate. Schedule
preparation appends actual SID/preparation attribution in the existing native
audit; B2 materialization rows still admit only task/thread/wake/dream contexts.
Explicit preparation/integrity intervals and caught package/repository failures
retain failed_cleanup; a normal Python return or terminal source row is not
settlement of partial file/package effects. Retained audit census stays blocking
until native ownership is genuinely resolved; no D repair writer is added.

Agent custom-skill identity checks precede durable writer admission under the
actual binding lease, then current task/session/agent facts are rechecked under
ownership. Lexical native validation-only refusals may settle only the exact
exception after successful rollback and affirmative temporary cleanup. HTTP
status alone is never cleanup evidence; artifact/commit/compensation/interruption
or uncertain audit tails remain unknown. Authority and profile intervals carry
the SAME native disposition through sync/async and fallback/naming wrappers.
Explicit leading read-only validation covers stale rename/prompt CAS and sibling
agent/roster refusals, with mutable checks retained under native locks. Rename
certification reaches interval exit only after its native SQL rollback; failed
rollback cannot inherit the validation exception identity. Completed naming
refresh is finite reconciliation, not an absent-effect assumption; caught refresh
failure stays uncertain through both validation refusal and normal return.
Pre-fence filesystem work, caught clone failures, best-effort publication failure
and incomplete profile closure likewise retain unknown ownership. No blanket
unfenced rule, historical settlement rewrite or new recovery/clear writer exists.
Temporary skill cleanup records the actual removal cause and uses no-follow
lstat. Only ENOENT positively establishes the literal entry is absent; a surviving
entry, dangling symlink, permission/I/O/other inspection failure cannot clear local
uncertainty. Earlier uncertainty remains sticky. Cleanup/probe failures are
logged and attached to an already-unwinding exception without replacing it. Mutable package facts are re-read at
the SQL leaf; settings prevalidate before nested publication and preserve unknown
ownership if a later section fails after an earlier commit. Existing writer
lock order and strict immutable terminal history remain.

New producer audits carry retry_initialized=false before first initialization
and true after it, even with an EMPTY schedule. The pinned boundary and budget,
including zero, cannot change on deferral/reentry or config changes. Missing/null
legacy facts preserve old history through its version-aware reader; they never
initialize a new budget. Ambiguous deferred legacy ownership remains a blocker
and new retry/launch admission refuses with owner remediation. No historical
bytes, enqueue age, ordinal, attempt identity or earlier uncertainty are rewritten.
Marker-bearing new records require correction-compatible C3 readers; this is
not a prefeature binary or old-runtime downgrade safety claim.
These corrections remain SOURCE/POLICY/STATIC only: behavior UNEXECUTED,
integration INCLUDING callback SKIPPED24342, units RETIRED. APPLY_AVAILABLE=false;
D/E/F and actual recovery/old-reader/activation acceptance remain root-owned.

## C2 thread admission and deferred obligations

C2 integrates actual REPLY, BOOTSTRAP and TASK_FOLLOWUP producers. One org-owned
thread admission owner resolves permanent identity from the persisted INTEGER
invocation, thread and participant before agent files, role/prompt/permissions or
skills are prepared. Notifications are never launch authority. The runner peeks
the reply range without starting it; final commitment under move gate → DB
transaction → host terminal leaf rechecks the snapshot, atomically claims and
stamps lifecycle with possible-launch evidence. Changed ranges defer for current
context preparation. Special purposes use their actual invocation row in that
same commitment, without a reply-delivery row. Existing claim/settlement meanings
and immutable invocation/session/participant/attachment/history identities stay.

The private `thread_move_lifecycle` action appends closed version1 snapshots in
the existing real thread audit scope, using its uncommitted writer in the owning
claim/evidence transaction. It borrows neither task Pause nor workflow journals
and adds no schema. Fields bind org, INTEGER invocation, permanent agent, thread,
actual SID and preparation ID; phase is initial/fallback/nudge, finite original
retry schedule/ordinal/boundary and immutable per-attempt IDs/provider/model,
possible launch, actual launched observation, settlement and quiescence. Deferred,
producer-settled and failed-cleanup facts are distinct. Snapshots are at most16KiB
and64 attempts; strict serializer/readers reject unknown/duplicate fields, owner
mismatch, missing initial preparation, changed identity/budget, removed attempts,
rewritten settled evidence and erased uncertainty. Bounds refuse; they never
truncate a census or certify idle. No full invocation credential is added to this
new evidence. The existing genuine reasoning-selection audit stays with its
actual lifecycle commitment.

Held work stays outside host FIFO and final launch. The actual builtin and custom
self-launch bodies revalidate after throttle, including supervised passthrough.
An empty host fallback handle is not a launched-process observation. A narrowly
proven refusal immediately before self-Popen can settle that no-launch attempt;
an interrupted/crashed or unknown attempt cannot use that proof. Host teardown,
residue, receipt publication and lease release keep their ordering; typed
admission errors escape launch-body handlers without provider failure,
auto-decline, callback consumption, acknowledgement or breaker failure. Genuine
provider failures retain the native breaker classification. Missing/passthrough
cleanup and an earlier uncertain retry remain named durable blockers even after
a later clean receipt. Launch-observation persistence failure keeps unknown
ownership and does not interrupt actual containment cleanup.

Provider429 and host retry copies keep their existing finite budgets. Selecting
fallback or the one allowed nudge persists that phase before a winning hold can
defer it. Safe redelivery re-prepares from current context with the same genuine
invocation/SID, immutable claimed range if already admitted, and retained phase
and retry ordinal. It cannot restart the budget or silently replay an uncertain
attempt. Known original provider-session eviction commits its native audit and
compare-and-clear of the actually attempted stale SID BEFORE fallback phase
persistence and fallible hold/launch admission. The actual live claimed producer,
INTEGER invocation/thread/agent/runtime SID and durable launched/settled initial
attempt authenticate this observation under DB ownership, independent of callback
or drain eligibility. A winning hold cannot interrupt that invalidation; a changed
pointer is not cleared. Only then may fallback selection defer. Released re-entry
or failed fallback cannot silently retain/reuse the evicted durable SID. Delivery
watermark/token/range/runtime SID, finite budgets and uncertain-attempt blockers
stay intact; ordinary participant/callback/launch guards are unchanged. C2 adds
no move reset or restoration. Clean no-callback nudge
failure keeps the native post-launch-contract classification. Unknown original
ownership requires the actual recovery/containment owner, not another model run.

Queue put and put_once use the same queued/in-flight token dedup owner. Deferral,
cancellation and exception release notification ownership; success acknowledges
only that notification. Durable pending tokens, required-through ranges and
catchup/probe records remain authoritative. The existing breaker timer performs
complete durable runnable discovery and republishes safe obligations after hold
release; D also has an explicit post-release redelivery seam. Queue awaits occur
outside gate/DB. Probe admission skips each held pair before leasing/minting,
without consuming cooldown or preventing unrelated work. Messages and normal
obligation/coalescing/exchange writes continue under their native owners.

D's capture seam accepts only a committed WAITING owner with drain consent and
records exact actual currently launched preparations; capture grants no retry or
new launch entitlement. Only that live captured turn may post its genuine
reply/decline, dispatch or normal terminal settlement under the hold. New final
launches always check the hold. A bounded drain interface (at most30 seconds)
returns actual remaining blockers; it neither releases the operation nor writes a
forward decision. Complete thread census joins durable lifecycle/attempts,
pending invocations, required ranges, running delivery, probe leases, open
exchanges and held/released catchup obligations. Ended tokens/SIDs, a clean later
attempt or empty process-local state prove no idle. Restart loses live capture
eligibility; unknown containment remains blocked, never reconstructed from a
label. D still owes complete host/session/remote joins and owned operation deadline
and forward-commit decisions.

Startup's existing thread recovery, cutover and raw reply/bootstrap/followup
reapers preserve held and C2-owned obligations before rewriting a token or status.
Safe deferred/unstarted work can be rediscovered through the same queue owner;
lost preparation/process/cleanup evidence stays diagnostic. Participant creation,
removal and resume/reset writes cooperate under DB ownership during the future
forward-commit/reset window. Ordinary messages/delivery obligations continue.
The complete native archive boundary checks every affected participant before
its first durable effect, then holds the same DB RLock and one transaction through
discard/reply and BOOTSTRAP terminalization, breaker/exchange suppression, status,
participant reset, native audits and the system message. Its uncommitted leaves
never commit independently; standalone discard/reaper callers retain their native
commit ownership. This DB interval serializes against the move's decision writer,
without acquiring move gate beneath DB or performing await/filesystem/provider
work. A committed affected hold refuses with original status, pointers, pending
tokens/ranges/acknowledgements, breaker/exchange and audit history unchanged.
Authorized archive without that hold commits the intended native effects together;
idempotent archived requests append nothing. Transcript rendering/file/path,
archived audit and event publication keep their existing post-COMMIT owners.
All archive refusal/success and eviction/hold/re-entry observations are UNEXECUTED.
The actual operation-scoped reset writer and early partial-roster attachment,
full startup/launcher/old-reader compatibility remain D, not C2. No canonical
move/reset/publication service, public route/CLI or COMPLETE writer is enabled.
`APPLY_AVAILABLE` remains literal false. D/E/F and independent final-head
review, SOURCE/POLICY/STATIC QA and manager guarded merge remain outstanding.

Behavioral/race/retry/drain/callback/recovery/compatibility observations remain
UNEXECUTED. General integration SKIPPED THR243seq42; no new cases, parameters,
fixtures, retired Python units, ad hoc E2E or protected execution is authorized.

## Exact resulting roster and preservation

The eventual single operation moves permanent IDs `consultant_codex` and `consultant_head` together from `consultant` into the existing `default`, whose manager is `{kind: human, principal: founder}`. Both become workers. Explicitly demote `consultant_head`; retire Consultant only from the active registry and only after complete source membership is accounted for. Preserve unrelated teams/workers. Preserve `default_team: default`, omitted `task_default_team` and effective omitted-owner routing to Engineering. Founder never executes agent tasks; explicit human-team tasks still require an executable worker owner.

Only three canonical files change in the eventual operation: `org/agents/consultant_head.md`, `org/agents/consultant_codex.md`, `org/teams.yaml`. The former head's sole manager sentence becomes exactly:

> Advise the founder and HappyRanch teams on product, services and business operations. You are an individual consultant reporting directly to the founder.

The codex worker's advisory prompt remains. Preview exposes exact bytes/diffs. Preserve IDs/current names, original workspace paths, executors/models/reasoning, descriptions/repos/explicit allow rules/enrollment, provider/runtime memory, outputs/worktrees/history and frozen task/workflow bindings. Preserve thread participants/messages/attachments/delivery acknowledgements/breaker/cooldown/exchange/invocation history and historical authority snapshots/policy selectors/releases/activations. No rename/re-enrollment/archive/workspace move/memory copying, policy copying, promotion/election, active-task transfer or general migration framework.

The sole intentional resume change is `agent_session_id=NULL`, `last_resumed_seq=0` for both agents through the existing audited per-agent reset owner. It is not memory deletion or delivery acknowledgement. Recovery never restores old pointers. New operation/audit/publication records append real evidence without changing historical bytes.

## Private storage and transaction contract

`runtime/infrastructure/team_move_store.py` owns exactly one table, schema_version1. The unique operation key and deterministic org/key operation ID bind exact canonical UTF8 normalized request bytes and SHA256. Closed versioned request data binds org, fixed founder target, affected permanent IDs, complete source/target scope, explicit demotion/retirement/drain consent, immutable expected canonical/roster/naming/profile/dependency/policy/skill/permission/authority inputs, ordered per-agent revisions, exact three before/after images with digests and file device/inode/mode identities, and both original workspace identities. Image bodies are private canonical definitions, never provider/runtime memory or credentials.

The row also stores existing-boundary admitted-founder provenance (principal, org, `_require_human`, admission identity/time), constrained phase/revision, one nullable UNIQUE `active_slot` equal to1 while unresolved, immutable forward decision, per-agent reset/context evidence, per-file after progress, exact owned publisher invocation/journal/namespace/generation/digest, recovery ownership/quiescence evidence, bounded code/ID diagnostics and terminal refusal/abandonment receipt bytes/digest. These internal types confer no identity authority. E must derive admission from the actual existing founder boundary, never a caller-supplied actor or ambient agent credentials.

Installation uses the existing DB connection/RLock ownership, a short `BEGIN IMMEDIATE`, complete org workflow discriminator validation, one literal CREATE and exact layout/record validation before commit. No existing column/table/meaning changes, generic bootstrap addition or separate event relation. No automatic installation on reopen/startup/enable. SQL object/index/column/FK inventory compares the complete owned surface with a constructed literal reference; unexpected objects/partial layouts/versions and malformed records fail closed. Full history scans refuse overflow rather than declaring a capped census complete. Whole workflow release references include exactly optional naming/pause/move layouts; no broad whitelist or SQL-object stripping. F/E/G/H/I and v2 schemas/data/history remain unchanged.

Reservation, state/progress and existing `audit_log` writes commit together under the DB owner; exceptions, including interruption, roll back. Audit scope is `config:team-move:<operation_id>` with explicit admitted founder provenance. Reuse `insert_audit_log_uncommitted`; the committing audit method cannot be used inside this unit. Same key/exact body returns saved state/receipt without revision/audit/reset/publication churn. Changed body refuses; another unresolved reservation refuses. CAS refuses stale revision, skipped/backward transitions and immutable decision/progress rewrite. An unchanged state replay accepts the current or immediately preceding revision, adding no work. Unknown future completion layouts fail closed.

Committed-history reads require the initial empty WAITING reservation with no decision, diagnostic or receipt. Every historical phase requires its supported decision/receipt/diagnostic presence; precommit progress stays empty. Reader and writer share the finite edge contract: WAITING can commit or terminate precommit; forward stages can progress within the same phase, advance exactly one stage, or enter repair. Repair progress retains the interrupted phase and can return only to that phase. The reader reconstructs and compares the final resume phase and requires retained recovery ownership evidence on return; the current row validates the ownership chain. Terminal history is final, including intermediate refusal/abandonment receipts; unchanged replay appends no audit. Historical progress bodies are not stored in audit, so digest/edge validation does not prove past native effects or quiescence. Unsupported history refuses without rewriting it.

## State and forward recovery

PREVIEW is read-only and reserves nothing. WAITING commits intent/hold after founder admission under the short producer gate. Recheck full census; hard blockers refuse without domain mutations. Only captured already-running threads can drain with consent. Recommended bounded drain deadline is30 seconds within code, not a duration promise. Before forward decision, busy/deadline/cancel/process loss may produce REFUSED/ABANDONED plus immutable receipt and release only with no domain mutations/forward decision. B checks its empty progress/decision contract; D must reconcile actual unchanged native state before invoking that release after integrated preparation.

COMMIT_DECIDED durably freezes the exact target and final writer/census/input revalidation before any reset or file change. After it there is no timeout/expiry/TTL release or public undo. Stages are FENCED → RESET → CANONICAL → CONTEXT → PUBLISH → eventual COMPLETE. Stage/progress validation requires owned publication identity, both resets before canonical work, all three after images before contexts and both contexts before publication readback. Progress is append-only within the row; completed reset/context/publication identities cannot be replaced. A postcommit error records `repair_required`, retains active_slot and the precise interrupted phase. Recovery can resume only that same phase/target under validated owner/quiescence and CAS; time alone never permits takeover.

D opens one invocation-owned ProfileCoordinator batch and WorkflowAuthorityCoordinator fence. Capture both dependencies including empty sets; never nest two single-consumer async writer intervals. If identity persistence is uncertain inspect the SAME owned journal rather than opening another writer's fence. Native resets use `config:team-move:<operation_id>:<agent_id>` and actor founder. Nonzero reset records actual row count/audit ID; zero rows record a validated zero step with no invented `thread_session_invalidated`. A reset-before-progress crash is reconciled from exact scoped audit and actual cleared participant state, then only unfinished work continues. Freeze affected participant creation/resume changes during reset while messages accumulate normally.

Canonical replacement requires complete candidate validation, exact owned before/after hashes, durable individual atomic replacements with file/parent fsync, full bidirectional membership validation and coherent cache publication after all three files. Unknown bytes/type/ownership stay held for repair. Native context/permission/all-context skill renderers regenerate the two original workspaces without clone/refresh, provider deletion or memory recreation. Pure preview must share native selection/rendering functions and never invoke mutating adapters. Remove old manager text through ordinary rendering/server role checks; recompute eligibility without policy copying. Unexpected permission grants require concrete separate authorization. Shared package/store partial effects remain with native synchronization/integrity owners; no recursive cleanup or unrelated rollback.

Workflow publication's best-effort return is insufficient. COMPLETE requires exact canonical/coherent registry, both audited reset or validated zero steps, both native context/integrity receipts, SAME owned journal `cache_installed`, ready pointer/file/cache/digest agreement and current canonical/profile/dependency/skill/permission inputs plus settled producer/callback/host/remote cleanup ownership. D must implement this validator and its atomic receipt/release; B provides no caller-controlled complete/release shortcut. Lost receipt after completion returns exact bytes without another reset/generation. Notifications after release are at-least-once through existing deduplicated delivery/schedule owners, including crash after receipt before notification.

## Producer, writer and lock contract

One daemon and one org owner coordinate the durable hold and a distinct short synchronous producer gate. Producer registration precedes any role/prompt/permission preparation or claim; retain attributable preparation ownership through provider retry, executor fallback, callback and actual terminal host cleanup. A returned uncertain attempt keeps a separate reservation and existing Pause journal entry while the next actual retry arbitrates under its own preparation. Reserve/recheck again at final launch. Either a producer wins and is visible as busy, or a move wins and affected work remains unclaimed. Resolve permanent identity through task/session/workflow/schedule/job owners, never logical-ID string heuristics. Unknown ownership or cleanup refuses actionably. No-supervisor/legacy paths require the same contract; they cannot silently bypass it. Held agents stay outside host FIFO; normal unaffected work continues.

Composition order is existing producer ownership/binding lease → applicable root/Pause gate → move gate → DB RLock/claim transaction → tracker leaf mutex where its existing owner requires it. Terminal callbacks must not acquire the move gate under DB/tracker locks. Native profile/publication writer order remains profile → workflow → roster, acquired outside the short gate, with synchronous revalidation after acquisition. No gate/DB transaction across await, filesystem scans/writes, network/provider/bootstrap or acquiring publication leases. Persisted holds outlive mutex/process/TTL leases.

The root inventory must include every transitive caller/retry/fallback/callback/startup owner, beyond proposed filenames:

| Owner family | Required predicate and preserved pending work |
|---|---|
| Task/create/run_step/TaskInvocation | Pending/in_progress/escalated/paused/blocked affected roots, ancestors/descendants, decision managers, active chain/fanout, continuation and claimed/callback-accepted recovery block. Terminal task with unsettled child/job/session/callback also blocks. Do not relabel task/team/history or treat Pause as clearance. |
| Thread runner/claim/queue/breaker | Hold claims before token/lifecycle/prompt. Pending obligations stay pending, without decline/ack. Only captured running turns finish real reply/decline plus cleanup. Probe/exchange/catchup must settle or block; preserve history. BOOTSTRAP/TASK_FOLLOWUP/direct paths included. |
| Dreams/wakes | Running/claimed work blocks. Newly due ticks defer before occurrence/window consumption or minting work. Existing pending work revalidates through its owner after release; preserve missed-slot policy. |
| Schedule store/scheduler/runner | Firing/session/admitted request blocks. ID-only future armed schedules may remain, held before claim AND missed/expiry bookkeeping. Reevaluate ordinary deadlines after release without double fire/shift/catchup. Retired-team bindings block until owner revises/cancels. |
| Host/tracker/retry/fallback | Actual bindings/grants/provider attempts/processes/survivors/cleanup/terminal publication must settle. Missing tracker/PID/token does not prove quiescence. |
| Jobs/remote/workspaces | Pending/running job/wait callback, nonterminal remote attempts, leased/recreate_pending/uncertain workspaces or unknown cleanup block. Join actual task/session/workspace owners; never autoapprove/reject jobs. Opaque affected writers block. |
| Workflows | Nonterminal instances/dispatch/outbox/bridge/staged callbacks and nested pinned bindings/host_launch_started tied to agents/source block. Immutable historical pins remain. List unrelated draft invalidation from org generation advance; ordinary stale refusal/revalidation stays. |
| Roster/enrollment/profile/skills/name/policy/cleanup | Conflicting affected IDs/source/target/canonical/generated writes refuse; pending source enrollment or active materializer/profile/cleanup blocks. Canonical readers must see an owned coherent candidate or defer. Preserve names, selector history and shared-store synchronization. |

## Startup and persistent compatibility

D must discover/validate the operation through DB/schema ownership before normal OrgState membership validation, compatibility writers, authority cold publication and task/thread/dream/wake/schedule/job recovery/admission. Exact forward-committed partial before/after state is reconstructed/repaired through the restricted owned recovery path before ordinary attachment. Corruption/unknown layout/owner/process evidence leaves that org diagnostic-only; other orgs may attach. Coherent roster plus incomplete contexts attaches only with durable affected holds installed. Source rollback never undoes resets/publication or clears a hold.

All supported runtime attach/switch/launcher downgrade entrypoints must refuse an incompatible reader before execution when an unresolved move requires the corrected `online-team-move@2` reader (which also reads @1 records). This guard is NOT implemented by B's capability data. Empty additive schema is structurally recognized only by compatible layout readers; prefeature binaries do not learn hold semantics from a table. An updated in-process guard cannot control an already-running old daemon. Direct unchecked old-interpreter invocation is unsupported recovery. Apply stays unavailable until the root delivers the real guard and C1-D closure.

## Original public contract (historical B checkpoint; current E below)

Initial delivery is CLI/API, no new frontend flow. Proposed founder-controlled `teams move preview/apply/show/recover` maps to `/api/v1/orgs/{slug}/team-moves` preview, apply, selected GET and bounded recovery. Narrow cancel can abandon only unchanged precommit waiting. Request is closed/extra-forbidden: resolve human inputs to permanent IDs before digest, freeze key/body locally before POST and print the key. Preview ID/digest is not authority/reservation; transport loss queries/replays the same key/body, never a blind new apply. No force/actor/role/permission/raw-path override or agent-binding fields. Reuse actual `_require_human` founder bearer without session/invocation/manager fallback. Shared bearer remains identity-less; headers/environment cannot prove human presence. No auth redesign or ambient-agent-bearer apply authority.

Preview returns exact three-file diffs/hashes, active membership/revisions/naming/routing/human target, authority/profile/dependency/selection digests and policy deltas, pure generated manifest, full causal blockers/owners/remedies/completeness, running-thread drain candidates and unrelated draft impact. No credentials/private memory bodies or arbitrary LIMIT census. Unknown topology/output/policy grants/writer conditions refuse. `can_apply_now`, drain capability/version and missing evidence are honest observations, never an idle certificate.

Unknown scope422; stale input/target/source/skill/charter/census or competing move409 committed:false; busy refusal lists concrete IDs. Nonterminal accepted work202 with Location/operation/phase/revision/barrier/next action, not success. HTTP200/CLI exit0 only after complete readback/release. Proposed accepted CLI exit3 and refusal/repair exit1; no indefinite polling. Already-satisfied with a new key requires full role/context/authority checks and no reset/write; roster alone is insufficient.

## Approved acceptance and delivery limits

Reuse TASK11307 section8's finite scenario/expected-observation map: exact eligible request; stale/wrong target/third member/concurrent writers; session-only worker refusal; causal pending work; captured threads/pending followups; task/thread/schedule races/retry/fallback; due schedules; file/reset/context/publication failures/timeouts; separately authorized process-loss restart; identical/changed-key replay; permission/skill differences; historical/natural postmove work; unrelated execution/draft staleness. Store/state handoff carries four answers for each applicable case (real seam, expected observation, actual static evidence, missing behavior). It adds no test cases/fixtures/campaign.

NO new unit/integration cases/parameters/fixtures, retired Python run/import/collection/restoration, ad hoc Product E2E, protected mutation/process-kill/replay/restoration experiments, Jenkins, live synthetic task/DB operation/provider reset/roster move or reinstated four-control premerge gate. General integration SKIPPED THR243seq42. All unobserved behavior/migration/recovery scenarios remain UNEXECUTED. Source/static QA PASS is not behavioral PASS. Independent full final-head reviewer, honest scoped QA, normal hooks/nonforce publication and manager guarded merge remain; each push renews required gates. PR/main automation remains ONLY Source Boundary Lint and Web Lint(Node24), with standalone source-only dependency lint preserved.

Root source completion owes current-main composition, exact PR/head/checks and inventory, availability and evidence debt plus supported activation/preview/apply/readback handoff. Separately authorized release owner loads source; loaded SHA is UNKNOWN until actually attested. Founder-controlled apply and narrow actual roster/context/reset/authority readback plus ordinary consultant thread turns require separate exact authorization. Preserve holds/receipts on failure and fix the concrete source defect through normal review. Existing consultants stay in Consultant until that actual admitted operation. Historical PR1022/1052 offline evidence remains history, not online readiness or a tool to execute here.


Current-main C3 composition preserves automatic job admission: omitted/false
review_required executes through native owner checks; true refuses before allocation,
legacy true pending is inert, and /run remains a tombstone. Authenticated later
prerequisite loss rechecks move/causal ownership in the native Pause transaction
before its failed-before-launch CAS, audit/journal COMMIT and genuine wake. Only
that exact completed no-launch refusal may settle; partial scratch/environment,
output or process effects and earlier uncertainty remain in producer history.
Naming keeps INSTALL-only N-SAC-1 structural admission and actual install-diagnostic
provenance alongside SAME native disposition, rollback and reconciliation rules;
no removed whole-release naming fingerprint is restored.

The scheduled daemon disk worker reserves a global native writer per admitted
org before spawn, through supervision/reap/report. Held/unowned orgs defer with
bounded codes in the existing summary/audit; other admitted orgs continue. On-mode
child admission and prune re-read the actual parent's active global writer ID
through strict append-only history and validate the optional move layout/hold.
An installed layout with missing/unknown ownership refuses before mutation; absent
layout preserves native behavior. Missing/incomplete effect closure, interruption
and partial mutation retain unknown writer evidence; a clean later provider result
cannot erase it. The common1800-second window includes reservation admission.
Off/default dry_run, metadata/process/age/containment safeguards and manual dry-run
remain; no cleanup/activation authority follows. These source changes have no
behavioral/migration/recovery proof; integration INCLUDING callback SKIPPED24342,
Python units RETIRED. APPLY_AVAILABLE=false, no COMPLETE writer; D/E/F remain root-owned.


## Private D operation source — THR296seq135 / TASK11345

D now supplies the private native operation core in `team_move_operation.py`,
with pure canonical/context planning, one two-consumer profile interval and one
operation-owned publication fence. E now mounts the founder service/CLI under
the existing admitted human boundary, with `APPLY_AVAILABLE=False`. A provenance
model, agent bearer or caller success flag grants no authority. F and independent
whole-history acceptance remain outstanding. Earlier B/C/D checkpoint absence
statements describe those units; the public E section below is current.

The planner uses native AgentDef/TeamsRegistry validation and instruction/skill
selectors, without materializing, creating an ArtifactStore root, building a
package or emitting an event. It pins the three exact before/after images,
original file/workspace identities and modes, current names, complete selected
policy records, profiles/dependencies and generated permission/context/skill
manifests. It preserves unspecified YAML bytes and omitted Engineering routing;
only the approved two workers, exact head charter and empty Consultant retirement
are changed. Unsupported topology/metadata, explicit grants or new selected skill
packages refuse. Native permission algorithms and Codex enforcement stay intact.

WAITING reserves before capturing only actual running thread attempts. The final
census joins C1 task/Pause/workflow, C2 thread and C3 producer/writer/remote owners
with actual host ownership/residue, outside short gate/DB leaves. Native writer
epoch and DB change observations renew at the decision/release leaf. Hard pending
work or unknown cleanup remains blocking; no timeout, missing tracker, expired
lease or later cleanup clears sticky uncertainty. Profile leases precede the
publisher lock; no gate/DB transaction spans filesystem work or await. One native
batch handles both unchanged executor consumers, including empty dependencies.

The immutable decision binds one publisher invocation. Native fence discovery
recovers uncertain saves by that exact invocation, never another journal or an
old ready pointer. Audited per-agent resets derive founder attribution only from
the stored admitted service provenance and recognize the exact scoped audit plus
cleared post-state. A zero-row step invents no audit. Complete candidate validation
precedes every replacement; each exact owned before/after image is read back,
file/parent-fsynced and CAS-recorded. Canonical readers capture coherently or defer
through the short window; the shared TeamsRegistry object is updated in place.
Original workspaces use native instruction/settings, all-context skill package/link
and integrity owners without cloning, refresh, memory recreation or deletion.
Partial/omitted native effects hold repair; no unrelated shared-store rollback.

Private COMPLETE requires actual canonical/context/reset/selection/profile readback,
settled causal/process/writer facts, and the SAME journal `cache_installed` with
matching ready pointer/file/cache/digest and current inputs. The DB-only leaf saves
the original receipt and releases atomically. General store transitions still
refuse caller-controlled COMPLETE. Terminal replay returns saved state without
reset/materialization/publication. Explicit recovery requires the same target,
CAS and native original-owner finish or actual process-identity loss; no TTL or
supplied quiescence Boolean authorizes takeover. Held obligations redeliver through
existing deduplicating producers; armed schedules retain normal missed-slot rules.

Schema-owner discovery precedes Database compatibility writers, membership,
naming/cold publication and producer recovery. Without complete native proof,
affected orgs stay diagnostic-only; unrelated orgs may attach. A restricted recovery
handle validates the existing schema and exact images without bootstrap/migrations,
normal attachment or producer admission. Diagnostic orgs also fence global profile
reconciliation/writers so their dependencies cannot disappear from the closure.
Runtime registration/switch refuses to detach a hold. Updated shell and native Mac
launchers pass `--reader-guarded-start=online-team-move@2`: supported older argparse readers reject
this protocol before startup, including the earlier D Boolean-flag reader.
Updated launchers require a matching reader; direct
old interpreters, already-running old daemons and obsolete launchers remain outside
this cooperative guard. Source rollback cannot undo a durable decision. No actual
activation/restart/downgrade experiment is authorized or performed.

D correction: the existing operation audit owner records a closed version1
staging protocol with the RESET-to-CANONICAL transition, then an exact
operation/path/stage intent BEFORE exclusive allocation, acquired file and parent
identities BEFORE writing, and append-only terminal disposition AFTER file/parent
fsync. Recovery reconciles only those exact artifacts. A present path without
acquired identity stays actionable repair_required; it is never adopted, globbed
or deleted. Failed cleanup stays durable through owner-finished and recovery.
COMPLETE requires all stages disposed, exact replaced-inode coverage and fresh
absence readback. ONE-table SQL, old Progress serializers, historical audits and
saved terminal receipts remain unchanged. Legacy canonical-stage operations
without this protocol remain readable but held for missing ownership evidence;
no scan, inferred cleanup or historical rewrite can upgrade that evidence.

All per-org acquisition, strict resolution, filesystem and SQLite-open/read/close
observations normalize to bounded native diagnostics, preserving errno/SQLite
classification and original exception causes. Only genuine first-observed DB
absence is fresh. Raced disappearance or unknown observation remains diagnostic;
startup and runtime-switch callers retain earlier diagnostics through subsequent
reads and state adoption. Previously unavailable diagnostic/recovery owners
require an existing DB. Compatible readers leave only the affected org unattached;
incompatible readers and source-runtime detach fail closed.
Restricted recovery preserves the original error if handle close also fails;
standalone close failures retain native classification and never prove release.

The shared profile checker performs no diagnostic path/DB acquisition under a
profile lease. Ordinary global, rebind, claim/retry and synchronization writers
refuse `profile_team_move_diagnostic_org_held` without clearing the retained root;
EMPTY dependency sets still check diagnostics before yielding or mutating.
Adapter removal translates only `ProfileCoordinatorError` from its coordinator
span to HTTP409 with `detail.code`, including the retained
`profile_team_move_diagnostic_org_held` refusal for EMPTY dependencies. Unrelated
exceptions keep their existing handling; removal, audit and compensation stay
with the native adapter-store owner.
Positive reinspection belongs to native startup/attachment outside leases.
`add_org` requires the existing DB for a previously unavailable org and loads it
through strict schema/history and canonical readers. Its matching attachment
segment revalidates the actual already-open DB owner under the profile lease;
a current hold or invalid history refuses. Only successful synchronization and
shared-map insertion permit the existing post-attachment diagnostic removal.
Failed attachment and other diagnostic roots stay held. D's matching own-operation
exception likewise uses its opened native owner plus actual ContextVar identity,
including EMPTY dependencies; neither exception performs filesystem discovery.
Public recovery/reattachment is wired by E and remains source-gated pending F.

The one batch interval (including empty dependencies) captures AgentDefs, complete
profile/dependency closure, candidate, current inputs and census outside durable
mutation leases. Short segments revalidate actual operation revision, native
roster journal/pointer, dependency mirror, writer epoch and both local-write and
SQLite data_version observations. Only bounded exact-image mutation runs within
the canonical/profile leases. Publication capture and final discovery run after
the leases release. The final DB-only leaf revalidates the original capture from
BEFORE discovery as well as the completed census, closing intervening-writer gaps.
These are source predicates; no crash, cleanup, race or recovery proof was run.

Acceptance is SOURCE/POLICY/STATIC ONLY on the SAME thirteen four-answer
observations. Every absent behavior/migration/recovery/native callback/permission/
process-loss/launcher/live-move result remains UNEXECUTED; general integration
INCLUDING callback SKIPPED THR243seq42; Python units RETIRED. No new cases,
parameters, fixtures, replacement campaign or execution allocation follows.

## Public online team-move E source — THR296seq135 / TASK11345

The founder service and `happyranch teams move capability/preview/apply/show/recover`
are wired in source. `APPLY_AVAILABLE=False` and
`whole_history_integration_acceptance` still gate every new apply and explicit
recovery execution; no request/environment/private bypass exists. Preview and
saved-state reads are available to the existing bearer + `_require_human`
boundary, whose shared identity-less secret does not prove human presence.
Agents must not self-authorize with ambient credentials. Manage-agent retains
team/role and cannot transfer agents.

Preview resolves native current names/IDs, rejects former/unknown names, uses
only D's pure native planner and returns exact founder-only diff/digests,
context/permission/skill deltas, complete blockers/owners, drain candidates and
org-wide publication impact. It reserves nothing. Internal canonical images
stay private; never copy these admitted diffs to shared artifacts/logs.
Only already-running captured threads can drain (at most30s); pending tasks,
paused roots/children/jobs/workflows/remote/cleanup uncertainty remain blockers.
`can_drain_threads` identifies candidate turns, not clearance of hard blockers.

The client saves its exact normalized envelope and stable key privately before
POST. Fresh saves, identical-save reuse and apply reads all establish the same
positive custody boundary: pinned no-follow private directory/file descriptors,
exact canonical bytes and stable inode/type/mode/ownership, file fsync/successful
close, then bottom-up containing-directory fsync/successful close. Existing
complete bytes after a failed save proceed only after this boundary succeeds;
partial/changed bytes remain preserved and refuse without overwrite/new key.
The CLI prints the key and retains it on uncertainty. Same-UID races remain a
cooperative-storage limitation, not an OS isolation guarantee.
Apply transport loss performs one same-key GET, never a second POST/new key.
HTTP202/CLI3 means accepted/in-flight; only saved COMPLETE/readback/release yields
apply HTTP200/CLI0. Refusal/repair is CLI1; local argument validation is2.
Show never advances/writes. Explicit recover uses the same ID/expected revision,
native previous-owner/process-identity proof and CAS, with no TTL takeover.
New-key already-satisfied verification requires a prior genuine COMPLETE plus
full current native context/role/authority/census verification; no alias row,
reset, publication or invented receipt is written.

The daemon owns/reaps bounded tasks across client disconnect and before shutdown
DB close; timeout/cancellation never erases committed holds. Diagnostic-only orgs
have strict read-only saved-state access outside OrgDep and use D's restricted
native recovery owner. Close that context before positive ordinary attachment;
only successful native discovery/revalidation/map insertion clears diagnostics,
then real deduplicating owners redeliver.
Saved `diagnostic` retains the strict code/at-most64 safe owner IDs and original
history; it is a bounded refusal record, never a complete current census.
`saved_owners` derives original and latest recovery owner and original publisher
from strict decision/progress. `current_observation` separately reads native
blocker kind/ID/owner/remedy and current publisher/recovery/file-phase owners;
unknown facts stay null/unavailable, with no idle certificate. Diagnostic-only
orgs use a read-only restricted native context, without attachment or mutating
preview. Missing trackers remain unknown. `service_diagnostic` carries safe
asynchronous failure facts and secondary close/reattachment codes, explicitly
process-local/non-durable; restart loss never erases saved IDs/diagnostics.
Prior-owner proof/CAS still governs recovery. Native census refusal saves bounded
IDs only through the existing strict repair transition after that proof; show
never writes. Resolve the named native dependencies before explicit SAME-key,
SAME-target recovery at the newly observed revision, without retry/takeover.
Both GET by-key and GET by-ID declare202 while the daemon-owned task runs; private
no-store/Location and CLI3 remain, with200 reserved for saved completed readback.

Org unload/runtime switch refuse active
owners/holds. D staging/history/inode/ENOENT cleanup, all-eight-site outside-lease
discovery, C1-C3 sticky uncertainty/EMPTY budgets, INTEGER callbacks/v2 and native
permissions remain intact. No new table/column, auth, permission or hook changes.

Follow [the online runbook](../../runbooks/online-team-move.md) for the engineering-owned
F acceptance/enablement, separately authorized release loading and exact live
Founder operation/readback handoff. Source delivery is not activation or behavior.
SAME13 four answers remain; all absent HTTP/CLI/migration/recovery/race/cleanup/
startup/launcher/downgrade/callback/live observations UNEXECUTED; integration
INCLUDING callback SKIPPED24342, Python units RETIRED. No new cases or campaign.
