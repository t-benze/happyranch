# Authority v2 schema-refusal recovery correction

> **Scope update (THR-277):** this spec's statements that every committed
> refusal writes one root escalation describe roots only. A still-current
> non-root refusal now atomically writes `authority_v2_refusal_task_failed`,
> becomes FAILED, and wakes its parent without an `escalation` audit or founder
> notification. At startup, an `admitted` causal result reconstructed as
> blocked/non-escalate is left unfinalized with zero writes and no fence;
> acquired missing/malformed evidence, unavailable parser context, later
> stages, and parsed escalation retain the fail-closed refusal path. Actual
> acquisition/decode/transaction errors instead escape to the original sweep
> owner and suppress its entire final DB batch. No schema migration or
> new task status is involved.

Status: historical implementation contract for THR-229 / GitHub issue #918.
The v2 schema-gate portion was superseded by founder THR-229 seq351; the
recovery and idempotency portions remain current.

## Current zombie writer correction — TASK-13753

The real reaper preserves explicit pending/startup-held/lost-owner and selected
human-operation outcomes before marker work. Both Database marker/cancellation
writers renew the SAME original admission after their original RLock and blocking
BEGIN waits and before mutation/COMMIT, retaining original runtime, map and queue identity
as well as state/gate/org/refs/epoch/loop. A same-member map replacement refuses;
standalone construction is not a production bypass. Owed cleanup has no fresh
readiness gate, and ambient BEGIN/SAVEPOINT is never committed or rolled back.

Complete continued final evidence is a prefix, not settlement. Clearing also
requires complete ordinary completion with no blocking Q, or the exact consumed
Q and complete typed settlement evidence. Terminal refusal requires its existing
closed typed stage/completion/candidate proof and matching authentic root
ESCALATED or child FAILED disposition, without restoring the missing pre-final
evidence that may have caused refusal or issuing another parent wake.

Successfully acquired semantic denial remains bounded. Actual acquisition,
audit decode, lock and unexpected projection failures retain the original FIRST
exception/cause/traceback; another read cannot erase them. Owned writer rollback
is attempted at most once. Its failure alone propagates; with an earlier body or
COMMIT error it is separately retained with its traceback, and guarded recording
cannot replace the first error. Only successful owned precommit rollback proves
restoration; committed/uncertain COMMIT, cleanup and late-return prefixes survive.
A failed sweep suppresses its ENTIRE root DB batch and retains its manifest;
independently owed original child/worker joins remain. SOURCE only: behavior NOT
RUN, PROOF NOT RUN, native UNKNOWN and operator acceptance UNVERIFIED. Existing
resource, retired-unit, skipped-integration and daily-only E2E limits remain.

## Current terminal-selection correction (THR-328)

The [current orchestrator contract](../../agent-guides/orchestrator-contracts.md)
also governs the original completion classifier, which retains the exact pre-final
(task, manager, session, INTEGER result) identity from its already acquired
attempt rows. Original reaper-family and accepted classifications retain their
first selected tuple through the same forwards. The common consumer carries it to the hook solely for error
custody: it grants no dispatch, evaluation, stage replay or continuation
permission and performs no extra acquisition. A later family/attempt/binding/
proof acquisition error cannot become ordinary escalation, diagnostic pending,
or a successful reread. Known-v2 refusal and selected post-final calls propagate
the same first cause/traceback through the store, both common/accepted forwards
and the original periodic/cold failure owner. This includes the selected
hook's actual diagnostic audit writer: audit failure escapes before further
effects. Admitted attempts alone run the six stages; consumed_audited reentry
authenticates the ONCE persisted evaluation without reclaim, reevaluation or
reconsumption. Only authenticated continue_applies without a diagnostic enters
final continuation; authentic no-grant selects the existing policy_no_continue
J/task/exact-Q owner with current max_revise_rounds. Earlier genuine failure and
cancelled/replaced winners retain precedence. Startup uses current mechanical/
parser context and the writer's actual returned code; old-boot evidence is
refusal-only and never recreates continuation. Refused/owner_lost and
already-continued attempts keep their original owners and replay meanings.
Acquired malformed/mixed/absent or semantic evidence and deliberate pending
outcomes remain bounded; default legacy/v1 hook decisions and unselected
publication target isolation remain. A legacy diagnostic failure preserves its
original error beside the actual later diagnostic error. Within the six
attempt-stage bodies, the captured original connection owns the transaction;
caller-owned transactions refuse before effects. Mandatory owner reads propagate
acquisition errors, while diagnostic-only classification keeps its bounded
outcome. Exact duplicate refusals remain nonpoisoning even when cleanup fails.
Owned rollback and refusal diagnostics run at most once; mark, forget, rollback
and refusal errors remain separate actual objects/tracebacks beside the first
cause. Consumed-proof selection returns only after successful cleanup of its
original coherent read, without reevaluation or consumption. Refusal preserves
real startup phases, current policy evidence and exact J/task/optional-Q atomicity;
a successful COMMIT is a retained prefix, and lost return/failed cleanup remains
uncertain. The shared synchronizer now retains the first delivered exception
and exact completed body result through release/post-body instrumentation failure;
caller consumption remains separate (see the runtime lifecycle contract). Errors
swallowed inside unchanged helpers cannot be reconstructed by an enclosing owner.
The composed finalization body captures the original connection, requires real
Boolean transaction ownership, and preserves the six-stage/current-root proof.
On failure, authentic-owner mark and forget precede fallible cleanup; each owed
mark/forget/once-only rollback/refusal error delivered here remains an actual
object/traceback beside the SAME first cause, including cancellation. Post-final
reads reuse the committed J/R/K/P/V/a0-a3 and ONCE evaluation before final E/N/D
audits, without live-token restoration, today's selector or another evaluation.
The separate receipt body renews the COMPLETE potentially-related current Q set:
one exact well-formed recovery/result/INTEGER/result-SID tuple only, no duplicate,
partial, malformed, nonterminal or conflicting sibling; disjoint established
terminal history neither authorizes nor vetoes genuine ordinary completion.
Accepted Q consumption and both audits are atomic; consumed replay authenticates
read-only evidence and ordinary absence requires real completion evidence.
Each owned denial/read/replay rollback is marked before its sole attempt and is
never retried. Only successful owned PRECOMMIT rollback restores that transaction;
prior finalization, successful settlement COMMIT and lost-return uncertainty
survive. These are selected body SOURCE promises, not executed proof: unchanged
committed/binding/retained/StartupReads helpers may swallow errors before delivery,
while the shared synchronizer now retains delivered E1 and any completed body
result through release/clock/warning failure (see the runtime lifecycle contract).
That private evidence grants no successful caller return, replay or native closure;
actual caller consumption and helper-internal errors remain ROOT-owned.
The publication claim/ack/failure, generation settlement and public spend bodies
also capture the original connection and refuse caller BEGIN/SAVEPOINT using
real Boolean ownership before effects. Their pending cleanup is marked before
its sole rollback attempt; failed cleanup is never retried. Delivered first
errors keep their original object/traceback and actual later rollback errors
separately. Selected _preserve_errors, cancellation and cleanup failure escape;
default bounded failure outcomes require successful owned cleanup. Successful
COMMIT is retained before return, with no postcommit rollback; lost COMMIT
return remains uncertain. Original boot/lease/attempt, claim/failure audits,
exact generation settlement and R2/report/decision-set proofs stay unchanged.
A failed settlement preserves earlier claim/preparation/native prefixes; raw
put effects survive failed acknowledgement/failure recording. Public spend
commits before its separate decision claim; private coding-start still composes
its uncommitted spend/claim/graph/ack in the original bootstrap writer. None
of these errors grants fresh admission, publication, spend or consumer entry.
The decision claim/ack/refusal wrappers compose the captured original connection
with real Boolean ownership, once-only owned rollback and selected FIRST-error
preservation. A successful public claimed return after COMMIT alone permits the
existing consumer entry. Acknowledgement and interruption refusal preserve every
independently committed task/child effect and generation B; exact terminal
replays remain read-only. The complete spent/claim/event-set proofs and all three
uncommitted cores stay unchanged. Capture the delivered first traceback before
cleanup, retain actual later rollback errors separately, and keep default pending
conversion only after successful cleanup. Successful COMMIT is retained; lost
return stays uncertain and grants no reentry. These are SOURCE promises without
behavioral proof. Retained zombie proof now combines committed no-grant/exact-Q
classification with the full relevant-receipt veto. Continued prefixes need
real ordinary completion/absence or the sole well-formed matching consumed Q
and complete read-only settlement proof. The original marker CAS renews SAME
admission on captured conn, preserves caller transactions, attempts owned cleanup
once, and retains delivered FIRST object/original traceback plus actual later
rollback errors. Observed COMMIT survives late failure; uncertainty grants no
repeat consumer or repair. Unselected zombie owners, actual caller consumption of
shared-wrapper evidence, hidden helper errors and full native lifetimes remain
ROOT work; no enclosing ledger repairs them. Existing stage/final refusal
bookkeeping cannot replace the first error. Native classification/claim and exact publication ack/failure
close their existing writer intervals before the original loop performs raw put.
The generator carries values/errors only, with no live transaction, cursor,
profile/publisher lease or thread-local scope across suspension. Selected
_preserve_errors escapes before logging, fallback reads or new effects; default
per-target isolation remains. A logger or failure-bookkeeping error retains its
actual object/traceback beside the original classification/publication cause.
Native startup unsafe connection custody stays sticky beside
the original cause; it does not replace it. No cleanup gains a readiness gate.
Common completion and accepted recovery independently guard the original
connection BEFORE classifiers/audits/effects, requiring real Boolean ownership
and SAME self._conn. A caller-owned BEGIN/SAVEPOINT returns the private lost_owner
held marker without fallback, success, requeue or worktree tails. Lower hooks and
the terminal owner retain their separate entry guards. No whole-runner/sweep
sentinel guarantee, new connection or settlement/cleanup authority is implied.
Run-step preserves that exact selected result/tuple and forwards the actual
human-recovery operation or typed held/pending/lost return before consumed-Q or
applied-decision settlement. A selected policy terminal's housekeeping_pending
stays structured v2_refused with its J/task/exact-Q owner and cannot enter the
historical bare machinery v2_pending ordinary-escalation fallback. Only the
fresh committed refusal writer surfaces the original human reason.
Original classification/material/spend/claim/refusal/
acknowledgement errors propagate without a successful reread or ordinary fallback.
Enclosing release, phase, context-reset, root-worktree and Pause tails owe
E1's actual object/original traceback beside each real later error. Pause's local
settlement retains each delivered occurrence before fallible marking/diagnostics,
then independently attempts original final move release and applicable coding
observation once in order. A new settlement error rethrows the SAME first cause,
even with an active E1; no new error preserves None and the caller's original
unwind. The outer run-step ledger retains every actual occurrence, including
repeated kinds/objects, diagnostics and failed attachments, without replacing
E1 or prior nested records. The same first cause remains active while the original
root batch, installed task-tail reset and each collected Pause owner are attempted
once in order. Inner preparation/refusal bookkeeping, original release/uncertainty
markers, installed context resets and pending forwarding cannot skip independent
tails. Ordinary and legitimately handled refusal/held/pending shapes remain;
a completed body value before a failed tail is evidence, not successful delivery.
Private attached/traceback records do not certify actual facade/caller consumption,
helper-internal hidden errors, observer postcommit consumption or native/DB-close
closure (see the runtime lifecycle contract). Pending consumption cannot run any part of the failed cold root DB
finally batch, admission/report included.
The earlier waiter/native observation ordering remains at its original owner.
The original startup reader closes before its observation lifetime. An escaping
read/writer/tail E1 retains its actual object and original traceback across
invalidation, sticky unsafe custody and both exits; every actual later exit or
diagnostic error remains separate. No failed sweep may call any part of its
root DB finally batch, including admission/report. Independently owed native
joins and the original DB transaction/cleanup obligations remain.
The SAME original native-writer body or post-reservation admission error and
traceback survive later settlement, note and logger failures. Actual later
exceptions/tracebacks remain separately retained; a settlement-only error still
propagates. Reservation, exact validation-only no-effect and sticky uncertainty
predicates are unchanged, and later diagnostics prove no original retirement.

The selected completion error route also covers the real next-result spend,
decision claim, acknowledgement and claimed-replay refusal. The common gate and
winning caller use the existing optional ``_preserve_errors`` writer contract;
actual evidence/BEGIN/audit/COMMIT and late-wrapper failures escape to the same
original failure owner instead of being logged as pending success. Each owned
rollback obligation is marked before its sole attempt. A failed denial rollback
is not retried; body E1/traceback remains primary beside actual rollback E2 and
its traceback. Caller BEGIN/SAVEPOINT still returns semantic ``transaction_owned``
before owned work, and acquired denials retain their bounded outcomes. Default
public outcomes and private coding composition retain their transaction owners.
The spend binding proof uses the existing semantic-denial/unavailable partition.

A reserved/receipt R2 need not have its own unfinalized attempt. In that case the
classifier retains the exact tuple from its already acquired original result
row solely for error custody; it neither creates proof nor changes dispatch
permission. A caller's first tuple/INTEGER still wins over later classification.
If a root terminal transition committed before acknowledgement E1, or an earlier
root was captured before replay-refusal E1 in the same sweep, that E1 suppresses
the ENTIRE retained root DB batch, including admission/audit. A separate marker
denial cannot restore lost attribution. Previously committed spend/claim/root
prefixes survive; original child/worker joins and native/close/unsafe-custody
barriers remain owed through cancellation, stop and late publication. Unsupported
cold ``workflow_start`` still remains pending/recovery_failed without a bootstrap
bridge or ready-only recovery consumer. SOURCE only; behavior/proof NOT RUN.

The S1a cleanup correction retains a local original callable owner under the
existing maintenance barrier BEFORE its sole native submission. The shared
waiter preserves the SAME first cause/message/traceback and already-ended-error
race rule, consuming the SAME worker outcome. Actual later native failures and
tracebacks remain in fixed slots independently of repeated cancellation notes.
Even an already-cancelled wrapper selects through the local retention callback at
the first cancellation observation, using the native finished/error snapshot then.
An earlier native error keeps its SAME object/original traceback; an earlier
observed cancellation cannot be replaced by later native completion or delivery.
Cancelled wrappers and enqueue-then-raise or missing handoff/retirement
acknowledgements retain unknown native custody; no shield spin, inferred no-run
or resubmission follows. Positive callable completion and successful original-loop
handoff/retirement are required. Drain refuses unknown ownership without an
unbounded wait for a possibly absent worker. Reference renewal preserves the
original org/map/dispatcher; owed cleanup gains no new readiness gate. The
separate ordinary job-termination tail likewise registers its SAME org/DB owner
before Task/Thread submission and remains visible through final DB/parent/root
work, actual native exit and original-loop retirement. Its first actual exception
is selected by presence with the original native traceback; validation, marker,
publication, callback and partial registry failures stay separately retained.
Publication cannot skip the independently owed original callback or retirement.
Maintenance preserves the same owner and first cause across later workers and
failed secondary diagnostics, keeping the original snapshot order and full positive
retirement predicates. The shared waiter uses native BaseException traceback
capture/rethrow before fallible diagnostics, preserving its original cancellation
race, one-time owner selection and cancelled-wrapper skip. A failed original
retention callback cannot skip consumption of the same wait/result; every delivered
observation, callback or attachment failure keeps its actual object/traceback,
without identity deduplication or recursive diagnostic retries. Earlier linked
records and completed values remain local evidence, including in the rethrown frame
if attachment fails. Hidden callback-internal errors, outer close consumers and
independent native/retirement barriers remain separate source boundaries.
The Orchestrator and Dispatcher facades forward the exact run-step result without
conversion or consumption. The original queue work retains the SAME object before
later tails and separately records observed return, including None. False/INTEGER/
strings and human/held/pending objects keep their identity/type without application
or replay. An operation/pending marker alone adds no permanent queue blocker; the
human operation retains its separate original registry/completion lifetime.
Queue work retains raw first captures and previous-linked pending occurrences
before fallible collection, formatting or attachment, with the original error
object/native traceback and separately attributed later raised or gather-delivered
errors. Failed retention cannot erase the failure or skip independently owed tails;
failed diagnostics are not recursively retried and nested records remain intact. Each heartbeat cancellation/join,
qualifying prepared-owner retirement, task_done and applicable metrics attempt is
protected separately and marked before its effect, with normal return recorded
separately. The acknowledgement of this worker's own heartbeat cancellation is
not a new incident. Only a positively returned prepared owner and the original
qualifying ended/unperformed run permit worker_finished; cancelled/missing native
Futures and uncertain submissions do not. Org-token release and queue removal wait
for accounting/metrics success, retain their existing failure predicates and run
once without DB work, external effects or waits under registry locks. Partial release
is evidence,
never re-registration, retry or inferred native success; SAME failed work retains
the available original queue/close barrier. Known no-run StartupUnavailable refusal
stays a refusal, without provider-activity inference. `_join_native` retains each
join on the SAME work and Future. Before each original preparation/run/retirement
executor handoff, the work retains that exact bound callable/item/attachment and
attempt. Its local wrapper invokes the original callback at most once, retaining
actual native entry, exact returned value or raised BaseException/native traceback,
and real exit separately from submission return, Future delivery and caller return.
A short same-work lock orders only the original raw native-error and waiter-error/
cancellation observations before diagnostics: earlier captured native E1 survives
later submission/wait E2, while earlier waiter cancellation survives later native E1.
No final-state inference supplies missing chronology. The heartbeat gather keeps its
existing Future observation contract; this capture cannot recover heartbeat-internal
errors. Native callbacks, formatting and all loop/DB/registry effects run outside
the capture lock. Presence and native BaseException traceback access preserve the
selected object and forwarded provenance before fallible retention or attachment. Actual later occurrences
stay separate; result re-observation and caller context delivery are labelled,
without merging separately thrown objects or messages. Failed retention/attachment
stays on that owner or rethrown frame and cannot skip original Future consumption.
Repeated cancellation continues the same shielded wait. A successful original
Future still delivers its exact value, including None/False/INTEGER/string or
human/held/pending objects, to its original caller; suppressed cancellation alone
creates no raised failure or permanent blocker. Preparation owner and qualifying
retirement therefore remain reachable. A done cancelled wrapper or missing actual
acknowledgement stays UNKNOWN; no spin, replacement operation or native-exit proof
follows. Submission retains the original work/attachment/item, acquired and
registered prefixes, coroutine and handoff attempt before create_task. An ambiguous
submission error retains those originals without a no-run inference, coroutine
close/cancel, token repair or resubmission. Stop retains one sticky drain owner
before its task handoff; missing task or callback acknowledgement never authorizes
a replacement. The drain snapshots actual original workers and native work,
retains cancellation requests and individual join outcomes, and attempts the other
original joins after an ended error or observation failure. Missing/cancelled native
handles remain UNKNOWN; an acknowledgement of this drain's own accepted worker
cancellation is wrapper evidence only. Other cancelled consumers remain separately
recorded; suppression requires positive original native joins and the idle guard.
It never settles cancelled/unknown native handles. No None handle enters a gather,
and no done cancelled handle is polled indefinitely. Original submission, drain and stop
readers freeze the SAME work's raw or delivered failure at their original observation,
before fallible error collection, diagnostics or further outcome observation. The raw
native-failure anchor preserves its original per-invocation first-observation snapshot;
an earlier captured native E1 survives later submission, cancellation or missing-handle
E2 even before Future delivery. A genuine earlier outer cancellation remains selected
when native E1 is first captured later. Prior selected owner causes stay selected.
An actual failure_pending remains visible after failures-list retention fails; successful
suppressed join cancellation alone adds no failure marker. Raw-only drain cancellation candidates remain suppressible after positive original
joins and idle; a raw capture alone adds no permanent failure predicate. Each original reader retains
its observation and original cause links, with later actual errors separately reachable.
The original registry/snapshot order is not a total historical error order across owners;
final work state supplies no earlier waiter chronology. Native raw error/body/exit
observations still supply no missing Task/Future or retirement acknowledgement.
Successful suppressed cancellation with positive original completion adds no new
retirement blocker. Stop's distinct waiter still delivers its original cancellation
after positive drain completion. Each stop keeps its original finite deadline across
repeated cancellation; timeout/cancellation neither settles nor restarts the drain.
The observer retains the original outcome and diagnostic errors without DB/native
work; failed callback registration stays with the same queue/drain owner. A completed result before failed
caller delivery supplies evidence, not close permission. A recorded native body value
or exit never fills a missing/cancelled Future or submission acknowledgement, retires
an owner, permits replay or releases the original queue/org close barriers. Uncertain
handoffs stay UNKNOWN without resubmission, replacement, repair or invented acknowledgement.
CodingDispatcher's local `_writer` captures the actual first body/schema/COMMIT
exception and native traceback before its one original rollback, retains an actual
rollback error separately, and delivers that same first object without formatting,
truth tests or virtual exception methods. Prior owner/attachment records remain linked;
a failed attachment stays with the same CodingDispatcher owner and both actual
errors, including when contextlib restores a yielded body's traceback.
Failed rollback proves no restored transaction, success, no-run, retirement or
DB-close permission. `_heartbeat` internal catches, other CodingDispatcher
preparation/terminal and helper/outer/native lifetimes remain ROOT boundaries. The shared OrgState idle guard also checks the original boot-captured
task queue after its existing thread-release, org-token and runner checks. Retained
work whose captured attachment is that exact OrgState refuses direct close/remove/
swap with native_tasks_in_flight, even after token release effects-then-raises,
consumer cancellation or a done Task/Future. Original state/gate/orchestrator and
queue identities are validated without requiring READY, so failed/stopping recovery
can still close an otherwise idle attachment. Missing/replaced production bindings
refuse; only explicitly all-None standalone bindings with no captured admission
references retain the offline contract. Work solely for another attachment, including
a same-slug replacement, is not attributed to this org. Unattached work has no
invented org owner; the unchanged global queue guard still refuses all retained work.
The finite read-only identity observation holds the existing queue lock, with no
DB/profile work, callbacks, formatting, joins or awaits under it; refusal occurs
after release. Submission/preflight/close remain synchronous on the original event
loop, not a general cross-thread race fence. Positive original removal leaves no
new blocker; suppressed cancellation alone does not change retirement. An idle queue
satisfies only this predicate: every other lifetime/close barrier remains required.
No token repair, re-registration, removal, result replay or cleanup follows.
Local custody cannot recover errors hidden in unedited helpers
or certify callable/Thread/Process/pump/backend/host/lease/job/DB-close retirement.
The contained terminal
callback retains the first actual cause/native traceback and every delivered later
error, including repeated objects, context/root/marker and attachment failures.
An active host cause alone preserves ordinary successful hook return. Original
entry/workflow failure still owes one marked generation-safe tracker clear, with
no skipped predecessor-gated workflow effect. Root work follows tracker only with
the delivered original manifest: missing delivery, pending consumption or failed
reset suppresses ALL root DB batch entry, admission/audit/report included, retaining
the same owner and UNKNOWN without replay. Host-wrapper swallowing/logging and
outer marker/lease failure remain uncorrected source limits, never native closure.
Active or missing/failed submission/start/handoff/retirement acknowledgement refuses
dependent destruction without resubmission or an absent-worker wait. A positive terminal CAS or accepted
completed/nonroot consumed receipt still owes its job hook exactly once when
verdict/history fails, under the original current-owner checks and outside writer
locks. Direct cancel retains tasks from a positive coding-batch return and
captures each legacy per-task COMMIT before its separate audit/publication.
It attempts each unattempted hook on exceptional/cancelled unwind without
skipping other captures or root work.
The first request cause and actual later control/hook/root outcomes stay distinct.
Its document dispatcher remains separate. Each private foreign-loop signal-stage
request has one fixed five-second acknowledgement window through cancellation.
Missing acknowledgement gives UNKNOWN/refusal with the original callback/Future/
wrapper retained, no replay and sticky uncertainty even after a late real outcome.
Remaining original stages/targets still progress; the bound proves neither no-run
nor native exit/retirement. Original bookkeeping/drain E1 and its traceback
survive later owner-reread, root-join E2 or cancellation, with actual later errors
retained independently.
Task-end intent targets the SAME registered pending spawn on its owning loop;
a successful late Process receives no script input and owes original native/
pump/finalization cleanup. Pause's committed drain and cold first-cause ownership
remain distinct; no INTEGER, receipt, first tuple or committed prefix is replaced.

At the actual periodic join, a normally returned typed root ``unknown`` retains
the SAME disposition/receipts/process facts and original owner through maintenance,
idle/detach and DB-close, and stops dependent periodic activity. Earlier E1 stays
primary beside actual later outcomes. Attachment load/native/close waiters use
the same first-observation ordering and retain actual later failures separately.
An ended attachment error still owes independent recovery/read/release/diagnostic
and move drains; active/unknown attachment workers or captured-org readers refuse
final DB destruction. Independently owed original children and
an otherwise authorized root tail still join; a failed sweep suppresses the
ENTIRE root DB batch, including admission/report. Safe/preserved/deferred keep
their local meanings, with no host/native closure inference or extra allowance.
Behavior NOT RUN; PROOF NOT RUN. This source repair does not discharge wider
shutdown, original resource/case eligibility or operator acceptance.

Retained job IDs require complete original job/submission/SID joins, ALL-attempt
producer, strict Pause/control and native custody proof. Preflight terminal and
Pause removal before original native finish deny; genuine settled no-launch can
qualify. Absent, malformed, foreign or UNKNOWN evidence never grants clearance.
New readers preserve old histories; old typed binaries reject policy_no_continue.
Rollback therefore requires compatible code and drained old consumers, with no
schema/history repair, loading, deployment, restart or live-cleanup authority.
C1–C9 behavior NOT RUN; native/sentinel/causal/rollback PROOF NOT RUN. Historical
proof below is not new execution.

## Current startup observation extension — ROOT13137

The actual authority startup consuming lifetime now exports fixed scalar counter/
duration aggregates after original reader exit and invalidation, including early
unavailable/failed/incomplete paths. The independent entry-only sweep check is
not counted as the consuming reader. Closed export acquires no canonical DB lock;
health/request projections add no reader/scan. Real fetch/hit/miss/decode and
independent logical exact semantics, original history/cache metadata caps,
refusal/check/log/tail ordering and every cleanup/transaction fence remain.
Ten finite invalidation causes and direct max/outcome counters extend the original
statistics. Characters remain characters; unavailable payload bytes remain null.
See [bounded runtime observations](../../agent-guides/runtime-and-configuration.md#actual-owner-startup-observations--root13137).
Runtime/clock/counter/sensitivity proof NOT RUN. The future C12 control must target
the single production `history_fetches` increment after real fetchall, with BOTH
`C12_history_fetch_count_matches_sql` and `C12_fetch_is_not_cache_hit` observed by
an unchanged independent SQL/decoder observer and byte-restored same-command pass.
No such mutation/run, new resource allocation, deployment or restart is authorized
by this source extension; earlier evidence retains its original attribution.

## Problem

Historical org databases may retain `agent_enrollments`. The former v2
integrity oracle treated that shipped legacy object as arbitrary drift, then the
pre-final refusal path forgot its process-local owner before persisting the
mapped terminal refusal category. The hook returned `v2_pending`, and the
result-bearing zombie sweep repeatedly consumed the same result without a
recovery hint. Each pass appended another `orchestration_step` audit while the
root remained `in_progress`.

## Required behavior

The original issue #918 implementation accepted only the complete SQLite
metadata generated by the actual seven-, eight- and nine-column
`agent_enrollments` histories. Founder THR-229 seq351 subsequently removed that
v2 schema gate: v2 performs no reference comparison, integrity check, or schema
recheck, and structural differences do not refuse continuation. Claim still
records the real claim-time raw-schema digest, inventory digest, and object
count on both K and P as observed-only diagnostics; those values are never
placeholders, compared, or rechecked. The legacy v1 schema clause is unchanged.

Before dropping a genuine pre-final live owner, the stage writer persists the
same closed refusal-category mapping used by the hook. Refusal finalization may
ignore unrelated terminal recovery receipts as historical evidence; a live or
ambiguous unrelated receipt still fails closed. A committed refusal owns the
single root escalation write and then uses the ordinary notification/thread
tail. Startup gives refusal discovery the existing orchestrator and surfaces
only the writer's newly committed `refused` outcome, after that transaction
returns. An authenticated `already_refused` replay, `owner_lost` disposition or
pending outcome emits no notification or thread follow-up; the fenced-root skip
for accepted recovery, pid failure and Pending enqueue is unchanged. If refusal
remains pending in the ordinary completion consumer, the original consumer
forwards its actual negative before ordinary escalation, audit/notification,
enqueue or receipt/decision settlement. Machinery `v2_pending` and a selected
policy-terminal structured `v2_refused`/housekeeping_pending retain their distinct
meanings; neither supplies success or widens ordinary fallback. Selected policy
pending retains its J/task/exact-Q owner and original custody until genuine
terminal settlement. Preserve the exact obligation and greatest committed prefix, including
genuine final E/N/D/Jcontinued and task Pending after a post-COMMIT return loss.
Both common-wrapper branches propagate the negative without acknowledging an
admitted decision as applied. Accepted recovery forwards it before receipt
settlement; the cold owner records recovery failure and suppresses the whole
final batch. The causal accepted-recovery caller and both v2 hook post-final
branches require the actual `POST_FINAL_RECONCILED` result before successful
None/continued bookkeeping. Settlement refusal, a not-finalized/unexpected result
in these already-final contexts propagates the same `v2_pending`;
actual selected reconciliation acquisition/transaction exceptions instead retain
the original cause/traceback through the failure owner. Missing/corrupt N/D and
hidden/empty discovery cannot erase either observation. A
real authenticated settled prefix remains positive when later queue publication
is pending. A newly finalized v2 continuation is classified before the v1-only
continued-root receipt bridge and never uses that bridge or repeats reconciliation
to settle v2. Owed native/host/Pause/move/cancel/DB/shutdown cleanup retains its
original owner and sticky failure regardless of the returned negative.

Completion-reader absence also authenticates retained attempt canonical/column/
preimage and admitted or terminal-refusal stage evidence. A continued attempt
without its matching retained E cannot authorize `no_v2` or terminal/later work.
Every retained continued generation must join its original E before existing
complete terminal proof permits later work. Retired D joins N/E's complete
original root/owner/session/envelope/generation/candidate/result/selector tuple
and requires captured root membership; historical A-after-B proof remains valid.
Completion classification and actual material-read exceptions propagate through
the common/accepted boundary on the same original selected INTEGER/result. A
second successful read cannot erase the first failed observation. Nested a0..a3,
final/publication/settlement/admission/spent/decision audit reads and pinned
binding/release/activation/selector authentication retain the same error boundary.
SQL/synchronizer/audit-JSON acquisition and unexpected projection failures escape;
successfully read malformed/absent/mismatched/type/key/cardinality observations
retain their original rejection and complete historical proof. No extra read,
transaction, write or native ownership transfer is introduced. Both accepted
common-forwarding branches preserve original pending negatives. The cold sweep's
existing exception owner records unresolved failure before readiness and suppresses
the ENTIRE final batch; authenticated successful no-op skips remain distinct.
Committed prefixes and independently owed native/host/Pause/move/cancel/DB/shutdown
cleanup retain their original owners. This source correction runs no scenarios
and grants no new status, retry, execution allocation or activation.

Before first accepted-Q settlement or exact consumed-Q replay, the owned
transaction classifies the COMPLETE root/permanent-agent Q set and requires the
sole relevant row to be the genuine asserted recovery/accepted INTEGER/session
identity. True explicit absence is `receipt_missing`, never ordinary fallback.
Only closed disjoint terminal history with nonempty origin/recovery sessions and
a coherent accepted positive SQLite-range INTEGER/nonempty-session pair is
nonblocking; consumed Q requires that pair, while genuine unaccepted
superseded/expired/restart-settled history retains both NULLs. Partial/malformed
identity or a second potentially related row refuses before Q/audit mutation.
Transition settlement requires active E with NULL spend/decision beside needed N
and pending D, as does shared proof for pre-admission publication/generation
claim. The shared settlement and retained-publication readers also require active
E with NULL spending_result_id/decision_state while same-G D is admitted: first
admission settlement, exact settled replay and admitted/settled consumer-outran
publish_returned acknowledgement all refuse a prematurely consumed E before
writes or replay success. A model-valid result FK is not spend proof. Generic
post-final evidence still supports genuine later consumed E and settled N/retired
D (or a distinct B) through the ORIGINAL full R2/report/binding/spent/decision proof.

Exact next-result spend replay also proves the COMPLETE retained decision-family
set for the original cause/G/reserved session/R2 and re-derived report digest:
independent ZERO potentially-related decision events for ready, exactly the
original claimed event for claimed, claimed+applied for applied, and
claimed+interrupted for refused. Closed other-stage shape is relevance, not
state authority. Missing, duplicate, mixed, opaque, extra/missing-key, mistyped
(including Boolean/float identities), unknown-stage or incompatible same-cause
evidence refuses read-only; legitimate fully disjoint history remains valid.
Only successful OWNED PRECOMMIT rollback undoes that transaction. Wrong-G/SID/R2
or distinct-B refusals preserve prior consumed/retired receipts and their
original later decision state. COMMIT-before-error, failed rollback and late
clock/warning/return loss leave committed or unknown effects; non-success/lost
return supplies no consumer permission and proves no absence of spend.
Run-step commits the public spend before its separate decision claim; current
coding-start composes the private spend core, claim, graph and acknowledgement
within the ORIGINAL bootstrap `admission_writer`: private claim/ack outcomes
and graph/claim/ack writes remain provisional until that writer's COMMIT.
Successful public OWNED decision outcomes follow their own COMMIT; only an actual
successful authorizing claimed return at its real owner boundary permits one
ordinary consumer entry, never a lost return. Only successful OWNED PRECOMMIT
rollback restores that transaction. Failed rollback may escape and retains
uncertainty; COMMIT-before-error or late synchronized clock/warning/return loss
can leave claim, applied+audit, or refused+interruption audit+optional task
escalation durable. `ack_pending` and `refusal_pending` are nonauthorizing and
prove neither claimed/ready nor absence of writes: ready -> ack `not_claimed`
stays ready; refused -> ack `already_refused` stays refused. Public caller
BEGIN/SAVEPOINT refuses `transaction_owned` before owned BEGIN/rollback,
preserving caller writes. Exact replay remains authenticated and read-only;
negative/lost returns grant no replay, consumer reentry, repair, remint or new
authority. Both paths preserve prior genuine spends/claims/results/task-child/
preparation/native effects and historical A/B/cancel/terminal duties, with no
new retry rule, readiness gate on owed cleanup or consumer authority.

Generation invalidation classifies its complete relevant event set before any
first N/D/audit write and requires coherent absence plus supported source-stage
proof. Exact invalidated replay requires one authentic closed type-exact event
and retired same-G D; a distinct B is untouched. Before replay success as well
as first writes, unchanged original P/boot/nextSID must authenticate needed/P0
absence, bound publishing/published claims and observations, actual audited
cleared failure, or the exact original admitted reservation/claim. Invalidated N
requires absent settlement/spend/decision history. Canonical N/body agreement
and closed other-stage payloads are not evidence authority; missing/incompatible
proof refuses read-only without repair or fresh readiness/owner-liveness gates.
Healthy causal pre-admission ownership and authentic admitted reserved-nextSID
ownership both refuse unchanged.
Only actual cancellation, well-typed nonempty replacement or displaced G supplies
a cause; null/partial/malformed identity never does. Independently owed native
cleanup retains its original owner without a new readiness prerequisite.

Nonclaimed/error/lost returns grant no fresh launch or re-admission permission.
Successful owned PRECOMMIT rollback undoes only that transaction; COMMIT or late
wrapper clock/warning/log/return failure can leave committed or unknown effects,
and rollback failure retains uncertainty. Separate settlement failure cannot erase
an earlier committed claim/preparation. Pause composes both private cores in ONE
writer, whose successful precommit rollback cannot erase prior preparation/native
ownership. Preserve the first cause and exactly the originally acquired RLock level.
These corrections change no native retry predicate, result bridge or permission.

This supersedes the original issue #918
ordinary-escalation fallback while retaining its historical attribution. Genuine
legacy escalation is unchanged; no schema or history repair follows.

Every completion-consumer entry checks the persisted orchestration-step audit
before appending a decision audit. Startup, zombie, accepted-recovery and
ordinary entry therefore share one durable idempotency key even if an older
caller omitted its recovery hint. Zombie recovery also passes that hint
explicitly. Replays must preserve the exact result and attempt identity and
produce no additional step audit, escalation, notification, thread follow-up,
candidate, evaluation, child or enqueue.

## Startup safety prerequisite (THR308seq133/127; ROOT12801)

Private Database-owned startup composition shares complete ordered root history
in BOTH discovery and exact getters within one explicit exact Database/connection/
invocation lifetime. Every truthy payload uses the original strict JSON behavior.
There is no action/stage prefilter, shortened sibling history or obligation-only
reader. General ValueError-to-None, escaping TypeError/storage and swallowed
obligation-read behavior remain unchanged; private strict failures are
distinguishable before those conversions.

Under the RLock, outside autocommit bookends (connection identity,
main.data_version, total_changes, main.schema_version) enclose the existing
owned coherent_read_view and a real main-table snapshot pin. Matching clean exit
is required before returning a hit/target/absence/anchor or accepting retained
entries. Discovery can share speculative complete histories among repeated roots
within that view; those pending entries are distinct from cross-read admission.
Each exact getter obtains a new current coherent closure, never discovery's
snapshot. No discovery target/absence, current canonical/admission/result/session/
binding/owner verdict or completion/parser decision is cached.

Local DML including rolled-back writes, independent commits including results-only
changes, schema/attachment/isolation drift, changed connection/ownership and
moved/missing/failed tokens invalidate the entire memo. After affirmative clean
owned exit and usable-connection checks, at most ONE fresh uncached coherent
fallback may run. Failed/unresolved owned cleanup or COMMIT retains the sticky
unsafe abort below; fallback/global None cannot sanitize it. Caller-owned helpers
clear/bypass retention and join their transaction without BEGIN/COMMIT/ROLLBACK.
An unexpected mid-session transaction never becomes safe caller ownership.
Invalidate before real refusal and any subsequent tail/effect, on failures,
uncertainty and invocation exit. Writers still independently authenticate current
authority in their own transaction.

The retained graph is complete raw immutable scalar row tuples plus private
decoded list/dict data. Only the closed read-only obligation validator and
admitted result-stage validator, through the scalar anchor builder, consume it
inside locked reads. Neither mutates, retains or returns containers. Targets,
anchors and codes contain scalars; no callback/parser/formatter/writer/public
consumer receives aliases. Generic get_audit_logs remains uncached and allocates
independent graphs, preserving JSON types, Unicode and empty payload behavior.

THR308seq151 / sole ROOT12914 changes local accounting/admission/affinity only.
Private caps remain 8 entries across both maps, 4096 rows/root and 8 MiB. A private
immutable payload is (actual root key, complete raw scalar row tuples, decoded
history). Compute its complete identity-deduplicated supported-builtin C once on
new admission work; retain (payload, C). M + sum(C for EVERY retained entry)
is a conservative object-graph upper bound, not an exact identity-union promise.
Overlapping/shared objects may overcharge, lowering effective capacity. M covers
the actual session/materialized field dictionary/field keys and owned values,
both actual OrderedDict allocations/keys including reserved storage, wrappers,
actual charge integers, epoch/attachments/flags and all bounded group metadata.
Exclude borrowed db/conn owners only at explicit fields, charging their reference
storage. Trial measurement substitutes prospective field VALUES while measuring
the actual field dictionary, never its copy; stop M only at fully charged payload
tuples. No persistent identity ledger, guessed JSON-byte size or old-payload
sizing walk on hit/admission/eviction/transfer is introduced. Equal epochs retain
the charged object; map swaps transfer the same wrappers/charges and preserve
field allocation/reachability. Actual metadata changes are remeasured.

Build a shallow trial OrderedDict in original pending order, append the complete
candidate and evict whole eligible oldest entries only in the trial. Publish only
if candidate survives and all caps fit. Benign oversize/all-protected rejection
preserves original map object/allocation/order/wrapper/payload/charge identities.
All ordered rows and strict truthy JSON decode precede admission or conversion,
including unrelated malformed last siblings. Empty/error/partial/over4096-row
histories are not retained; complete current evaluation still proceeds uncached.
Accounting/copy/plan Exception clears both maps/epoch/groups without retaining
errors. Catch accounting only; strict evidence/authentication/projection/cleanup
errors keep original semantics, and BaseException invalidates/escapes immediately.

Discovery grouping is bounded chronological root affinity. Within already-fetched
rows, windows contain at most 16 positions and 8 repeated-root (exact-string key,
first, last) descriptors in first-occurrence order. Bounded pairwise raw-key scans
create no per-root position lists, slices, extra output array or future SQL/JSON/
canonical/authentication work. Actual group/position storage counts in M throughout
its across-projection lifetime. Admit a plan only if it fits existing entries;
nonfit discards it without evicting useful entries merely for planning and uses
ordinary LRU for the rest of that discovery call. Every
_project and first-use complete history read stays at its original position.
Protect only complete fitting histories already reached/admitted through last
use/window end; evict LRU unprotected entries. If all eligible victims are
protected, reject trial unchanged and evaluate candidate uncached. Oversized,
empty and rejected roots receive no pin. Ordinary LRU applies outside groups.
Release all plan/protection state at last use/window/exit, invalidation/escape,
caller/unsupported epoch and before fallback/exact/clean cross-read publication.
Eligible histories may remain ordinary LRU for both-pass reuse. Exact reads and
effects retain original chronology and fresh individual closure; no future
target/anchor/authentication or snapshot is held across effects.

Failure-order examples: with valid A1/all A history, malformed unrelated last B
audit and malformed A2 canonical JSON, A1,B1,A2 stops at B. With malformed A1
canonical but valid A history and malformed B audit, A1 fails before reading B.
No reordered projection, eager prefetch, stored-error replay or deferred cleanup/
BaseException repairs these contracts. Conditional A1,B1,C1,D1,E1,F1,G1,H1,I1,A2
within one window can protect fitting A and evict unprotected B at I; this is
source rationale, not measured savings or a one-read-per-root guarantee.

Eviction/scope exit release whole entries/charges; no trial/replaced-map/helper
alias survives to the next projection. Bounds exclude transient fetch/parse/
traversal and SQLite native memory, hard RSS/time or whole-startup work. No
setting/dependency/production inspection export is added.

An optional frozen scalar anchor authenticates the complete canonical admitted
attempt, original admission audit, actual INTEGER result/task/agent/SID, sealed
binding/release/activation/selector and owner/boot tuple, including optional
candidate linkage. Canonical agreement, deterministic IDs and stored UUIDs alone
are insufficient. An anchor grants exclusion only, never live-owner proof,
refusal or owner-death authority. Absent admission evidence does not impose a
new normal eligibility gate. Shipping `completion_report_from_result_row` plus
`_parse_next_step`, including legacy fallback, still determines inert blocked/
non-escalate admission with zero writes and no fence. The refusal writer keeps
its independent current authentication and existing ownership/outcome meanings.
Finalized/continued rechecks cannot replay discovery's stale pre-final projection.

Any anchored exact/read/conversion/refusal failure installs downstream fencing
AND failed-root quarantine before bounded logging, then skips every later
same-root exact/refusal/tail operation. Pending also excludes later same-root
work using its existing category. Ambiguous attribution fails task recovery
globally when the connection is affirmatively clean. Successful non-inert
housekeeping need not quarantine legitimate siblings. Earlier inert siblings
cannot conceal the later fence, and earlier genuine commits/tails remain intact.
Only the freshly committed refused outcome owns existing postcommit tails;
already_refused/owner_lost/pending never duplicate notification/parent/thread/queue
effects. A tail exception with unknown transaction disposition aborts startup.

R1 OMIT is literal: no early `_record_hook_outcome`/capture_failure append,
replacement diagnostic writer or extra diagnostic transaction is introduced.
Exclusion precedes logging fixed server categories with no raw exception, history,
prose or traceback. Logging failure cannot undo quarantine. Local sets/logs are
not durable fencing; only existing authentic residue/commits retain durability.
Zero new diagnostic attempts is a source requirement, not three executed faults.

Startup-only observation is threaded through the real refusal writer's actual
BEGIN/COMMIT/every rollback boundary, preserving independent authentication,
SQL/audit ordering, cleanup and ordinary callers. Successful pre-COMMIT rollback
plus locked same usable connection checks permits root-local recovery. Failed
early cleanup stays unsafe even after a later successful cleanup. A raised
unresolved COMMIT is unsafe even if later autocommit is observed. Unknown,
replaced/unusable connection or unexpected remaining transaction is sticky unsafe.
No authority/daemon rollback retry, reconnect or repair is added.

The private read composition retains a body failure while exiting the existing
owned coherent_read_view normally, observes cleanup separately and retains both
causes on failure. Failed exit publishes no target/anchor. Generic read-view
behavior is unchanged. Caller-owned helpers join the exact snapshot without
BEGIN/COMMIT/ROLLBACK; the real refusal retains transaction_owned. Full daemon
sweep entry rejects caller-owned/unknown connections before Pause/workflow work.
Unsafe signaling escapes broad catches; the direct consumer rethrows before
later effects and skips the ENTIRE finally root-worktree batch, even though
teardown_incomplete alone would still permit DB work. It resets only in-memory
context and retains the unsafe cause if reset/logging fails. A storage-first
`_build_state`/main preflight abort still prevents app/listener publication.
A deferred native recovery abort instead retains its original cause and keeps
recovery-ready, the consumer latch and fresh launches closed. It does not
invalidate a genuine matching ASGI/listener signal, including failure before
that signal; health/thread reads retain their original authenticated boundary.
Normal clean ordering/batching and BaseException propagation remain intact;
earlier genuine commits are not undone and cleanup remains explicitly incomplete.

Source/static delivery is separate from operational proof. S1–S3/Q1–Q5,
including corruption/ownership/cleanup/caller/downstream/bounds assertions and
separate discovery/exact SELECT/fetch/strict-parse count reductions, are NOT RUN;
sensitivity is PROOF NOT RUN without the accepted full v2 producer, deterministic
barriers and actual downstream observer. ROOT12914 explicitly accepts current
source-delivery uncertainty; ROOT12801 historical limitations remain attached to
the original delivery. No runtime fault/count/alias/bound result follows from lint.
General integration is SKIPPED24342; Python units RETIRED. No executed query
savings, benchmark, speedup/under30s or activation claim follows from source.

## Non-goals

This correction adds no DDL or migration, changes no existing column meaning,
and does not rewrite existing duplicate audit rows. The later seq351 removal
accepts organic schema structure for the v2 decision path without mutating it;
it does not weaken permission or other non-schema fences. Neither change
performs a live database, daemon, policy-release, authentication, permission,
dependency, cancellation, or task-reset action.
