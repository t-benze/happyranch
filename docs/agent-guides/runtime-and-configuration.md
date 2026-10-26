# Runtime And Configuration

## Actual-owner startup observations — ROOT13137

Normal boot retains its original P0 prerequisites and P2–P9 order. The earliest
`__main__` monotonic marker precedes heavy imports but excludes interpreter and
package initialization. `BootObservation` in the infrastructure layer retains
only fixed scalar aggregates, attached to that actual DaemonState/StartupRecovery;
normal dynamic org attachment does not enlarge the boot census. It adds no
reader, SQL, filesystem census, payload serialization, native owner or readiness
predicate. It never retains org/root IDs, readers, connections or decoded graphs.

There are 23 fixed timing categories: imports, main preflight, state construction,
registry/runtime, org load/preflight, Database, workflow install/full validation,
move/Pause schema, cutover, roster/orchestrator/services, identity/authority
publication, selectors/authority owner, profile reconciliation and actual ASGI/
listener. Each has count/min/max/total inclusive and exclusive nanoseconds,
first-start/last-end, outcome counts and not_started/complete/failed/incomplete/
local_hold summary. No observation gives count=0 with null min/max/endpoints;
zero duration is never substituted for missing work. P0 nested synchronous
intervals subtract ONLY explicitly nested children on the SAME thread. Aggregated
org intervals include their DB/schema children: do not sum them. ASGI time may
overlap native recovery and subtracts none of that concurrent work. Five fixed
attachment counters distinguish diagnostic skips, refused loads, actual attaches,
nonfatal settings seed and fenced authority publication. Successful org attachment
never claims a caught publication failure succeeded; the publication interval and
fence counter preserve it. Original warning/cleanup/exception behavior remains.

The shell generates one fresh telemetry-only local late-exec marker AFTER its
existing shell/uv/Python/settings preflight. Its closed token (<=60 characters)
contains a random launch correlation, linux/darwin domain label and monotonic ns;
the separate explicit launch-id argument must match. No environment origin is
adopted and no optional original parent clock is accepted. Absent/malformed,
unmatched, unsupported/foreign domain, future or >1h stale markers yield no
interval. A syntactically valid marker is labelled `local_late_exec_untrusted`:
same-host/same-clock-domain transport is an assumption, not host/boot/time-namespace
attestation. Arbitrary supplied arguments confer no trusted provenance, identity,
authority or performance proof. Its nominal `local_late_exec_to_listener_ns` is
only later-marker-to-listener telemetry, excludes all preceding work and is never
a launcher-to-usable API/UI result. Unsupported/otherwise incomparable clock
provenance remains unavailable. Marker generation failure changes no launch
predicate. Exact two-line shell stdout and strict CLI/setup parsers are unchanged.
Mac/direct daemon launch supplies no shell marker; original parent provenance is
explicitly unavailable, without any native sanitizer/environment-policy change.

Only the matching `_DiagnosticServer` successful ASGI/listener adapter and original
`signal_serving` establish the listener time. The early log says socket bound /
ASGI-listener pending. Module-entry-to-listener and owner-to-serving intervals
retain distinct origins; neither proves usable authenticated API or browser
content. Public `launcher_duration_ns`, internal `startup_to_serving_ns`, original
parent/API/UI times and unobserved RSS stay null. No new public telemetry fields
or endpoints are added. Startup refusal exceptions carry the same sanitized public
projection, never the enlarged private snapshot/epoch. Health reads cached original
scalars under the short gate mutex and never acquire recovery's DB or worker lock.

The six original recovery phases retain settled/local_hold dispositions and
mandatory log-failure handling, plus explicit observation outcomes, actual start/
end and inclusive duration. Exclusive recovery time is unavailable because no
explicit nested child interval was measured there. Submit/native begin/native
end/ready/first-failure/first-stop/positive recovery-owner close are separate
once-only facts. Recovery-owner close precedes the separate StartupReads drain;
it certifies neither reader retirement nor full DB/lifecycle shutdown.
Native end is not ready; submit-to-ready wall time includes actual tails, real-loop
acknowledgements, reference binding and dormant-consumer preparation. Native
execution, submit queue delay, native-to-ready gap, failure wall time and actual
serving/native overlap are separate. Failed/interrupted native outcome stays
failed/incomplete; repeated signals/stop/fail do not log a second transition,
replace first cause, erase committed effects or manufacture successful ready/end.

The REAL `refuse_authority_policy_v2_pre_final_on_startup` consuming scope exports
only AFTER its original store reader exits and invalidates, including normal,
early-None/unavailable and exception/incomplete exits. It does not export sweep's
entry-only connection check. Closed `statistics()` copies fixed scalars without
reacquiring the canonical DB mutex. Across the existing attached-org iteration,
37 fixed v2 counters have lifetime count/min/max/total plus four outcome counts
and lifetime duration aggregate. `history_fetches` still has ONE production
increment, after the real audit SELECT/fetchall; hits, misses and logical exact
rechecks remain distinct. Discovery/exact calls, total/max ns, complete/failed,
rows/max rows, real decode attempts/success, characters/unknown size, admission/
rejection, epoch/fallback/failed reads and ten finite invalidation causes remain
actual event observations. Partial fetched/decoded work survives failures.
Known characters are not UTF8 bytes; history_payload_bytes remains null. Existing
8-root/4096-row/8MiB memo/full-metadata accounting, chronology, epoch/bookends,
independent exact rechecks and every transaction/rollback/COMMIT/finally fence
remain unchanged. Extra counters participate in original metadata accounting.

StartupReads reuses actual admission/worker/close/retirement counters, adding
retired/budget/submission failures, admission/work maxima and actual queue/native
time totals/maxima. Four retained leases remain the bound; a Future.done or
cancel is never close/retirement proof. Failed close keeps its incomplete lease.
StartupPublications retains its original bounded snapshot, handles and native
acknowledgements unchanged. New observer/counter faults cannot skip native close,
retirement acknowledgement or invalidation, replace primary exceptions or open
admission. Their optional observation failures remain counted when possible;
original mandatory phase-log failures still fail closed.

Instrumentation is O(1) per existing event with fixed categories, not O(1) whole
startup. No per-query/root/row/request log is added. There are at most eight
aggregate attempts per boot (boot_prepared/boot_failed/serving/native_settled/ready/
failed/stop/closed) and the six original phase logs; existing private error logs
keep their original owners. Aggregate attempts are once-only even if a sink fails;
no delivery/durability guarantee follows. Logging/copying is outside aggregate/
gate/reader/publication locks; fixed copies perform no DB work. No metrics table,
profiler, retry/recovery framework, process census or hard RSS/time bound is added.

This is SOURCE/static delivery only: all actual counter/clock/failure/stop/race/
resource cases NOT RUN and sensitivity PROOF NOT RUN, maker0/3 QA0/3 unchanged.
Root owns OpenAPI/test convergence, finite correctness, coherent PR, independent
FULL final-source review/same-head QA/guarded merge/exact-main lints and separately
authorized operator observation. Under10s remains UNVERIFIED. The eventual oracle
is the ORIGINAL external parent clock to BOTH usable authenticated API and actual
UI, with independent held-launch refusal/responsiveness and separate recovery
duration. No runtime execution, activation, restart or new allocation follows.

## Launcher consumers — serving and recovery readiness

Shell start/status preserve their process identity line and append exactly one
`startup serving=YES recovery=STATE launches=DISPOSITION` line (actual values
are lowercase). `serving` is `yes|no|unknown`; recovery is
`ready|recovering|recovery_failed|stopping|unknown`; launches is `admitted` ONLY
for serving=yes + recovery=ready, otherwise `held`. Admitted here reports startup
readiness, never overrides original authorization/Pause/native controls. Only
fresh exact Boolean serving/recovery_ready/not-stopping plus state=ready is ready;
HTTP200/PID/port alone is insufficient. Missing/malformed/old-server data and
failed probes are unknown. Start returns after observed HTTP serving and may
report recovery held; its exit0 does not mean fully recovered. Status exit0
means process alive only. No curl means start refuses before launching; status
reports unknown. Timeout/failed recovery preserves the original process and
uncertainty, with no automatic restart or second start/import.

`happyranch web` opens the readable thread UI while recovery is held/failed and
prints the actual disposition. Offline `doctor` remains an editable-pointer
check with no daemon. Ordinary startup503 is explicitly not accepted/exit1;
no queue/retry/replay or global client preflight is added, including callbacks.
Setup accepts only the complete finite two-line output, authenticates the ORIGINAL
wrapper/Python pair independently, and requires fresh positive recovery readiness
before dependent reads/import and Settings handoff, alongside every existing
source/native/schema/backup/cooperative-window fence. A held attempt keeps its
receipts and original owner; it authorizes no second start or import.

Mac keeps HTTP/process ownership separate from its typed recovery observation.
An alive recovering/failed/unknown daemon is never crashed/restarted for readiness;
2xx can load the original WebView. One cancellable observation window (120s,
2s spacing, each request/resource <=5s and body <=16KiB) checks original handle/
PID/port/state/generation after suspension. Stop/exit/replacement invalidates it;
failed observation clears prior ready. Ready/failure ends the window with labelled
last observation; expiry/detachment is unknown. Daemon → Check Daemon Status
explicitly renews it without launching. Labels/diagnostics distinguish liveness,
serving and recovery; no raw private cause or credential is added.

Source delivery is separate from activation. ROOT13137 retains isolated OpenAPI/test convergence, finite execution,
full independent review/same-head QA, coherent PR/guarded merge/exact-main lints.
All runtime/platform/race cases here are NOT RUN; maker0/3 QA0/3 unchanged;
under10s remains UNVERIFIED. Before any separately authorized operator restart,
use exact reviewed source and capture external parent monotonic time BEFORE the
normal launcher, usable authenticated thread API AND real browser transcript,
independent pre-ready launch refusal, later ready and separate recovery duration.
Health, shell exit and bind alone are insufficient. Absent launcher duration stays
unavailable; the later exec marker described above supplies no original parent
clock or activation.


## Current coding storage and setup — ROOT13150 / THR320seq261/267

Normal `OrgState.load` installs the unchanged four coding tables beside each
normally admitted org's supported document foundation. Fresh F plus coding is
one `Database.workflow_schema_transaction`; existing complete storage is
validation-only. Before any workflow DDL, the entire reserved coding family is
observed. Partial, extra, case-changed, wrong-kind or malformed objects and real
shared dependency failures refuse without repair or adoption. Body/interruption
and COMMIT failures preserve the primary cause and secondary cleanup errors;
uncertain COMMIT is not success or proof of absence. Audit-watermark advances
publish only after positive COMMIT under the same lock. Failed unpublished org
construction closes/refuses before attachment; later installers are separate.

Coding validates its canonical inventory and actual shared columns, keys, FKs,
owner histories, template/authority/native dependencies and Pause/move/naming
controls. Unrelated native ALTER chronology is not a coding-only whole-org SQL
prerequisite. Nullable submission/request FKs and nonNULL owner checks remain.
F/E/G/H plus coding keep their document identity; I plus coding retains literal
J. Complete old F/E/G/H/I/J histories, original import/mapping/settings/run pins,
INTEGER results, SIDs and UNKNOWN effects stay unchanged. Every cold/metadata/
recovery/post-write/return/cancellation path retains the same complete readers
and one original cumulative row/byte/step allowance, with no recursive admission.
Only a successful same-snapshot canonical reserved-family observation makes
absent E draft/H handoff/I revision exclusions empty; present families retain
full validation and actual conflict queries. SQL/read/lock errors never mean
absence. Mandatory F request bridges and other owners remain required.

Historical F/E whole-reference constructors use literal foundation primitives,
not operational startup. Independent coding companions and a private extension
cache distinction reach both generic whole-reference consumers. Raw whole
nonNULL SQL comparison stays unfiltered; legacy authority keeps its four
histories even after workflow's fifth-history cache fill, and authority-v2 stays
observed-only. No reference preimage or saved pin is rewritten. Generic Database
and runtime-audit remain workflow-free. Even empty F+coding requires compatible
code at downgrade; document blockers and returned/live/held drain rules remain.

The existing human setup coordinator retains present maintenance, backup,
source/UI/loaded-object and original operation/receipt custody. Its normal run
no longer invokes F/E→G→H→I→J migrations. The original supported deployment/start
owns inert coding initialization; post-start revalidation requires complete
coding storage on each normally admitted selected-runtime org, with document
identity unchanged except I+coding=J. Existing explicit document migration tools
remain separate. No old-folder or historical uncertainty settlement is a new
initialization prerequisite. Missing settings projects Disabled; installation
writes no settings, import, roles, grants or run and preserves Explicit settings.
The twelve selected loaded-object producer/consumer bindings remain matched;
health or disk HEAD alone is not loaded acceptance.

This source contract supersedes earlier coding-only J/whole-org-equality and
mandatory offline-chain instructions below, preserving attributed old evidence.
Human deployment/restart, Founder original import/settings, independently loaded
readiness and one genuine four-role scratch run remain separate root-owned
acceptance. Source/static checks prove no workflow behavior. Python units are
RETIRED; integration including callback/lifespan/SessionTracker is SKIPPED24342;
no ad-hoc Product E2E or new test/build merge gate follows.

## Current explicit coding execution and caller return — ROOT13015

Founder THR320 seq194/196/225 and manager TASK-13015 authorize the bounded
coding integration. This current contract supersedes unconditional-disabled
instructions in the historical K3/R2/revision/source checkpoints below; their
original observations, failures, approvals and immutable encodings remain history.

New starts require the mounted native queue/host/runtime, complete coding/shared storage and valid import,
current canonical settings with Admission Explicit and matching settings/release/
mapping pins, and the existing current-authority/native admission predicates.
Missing, malformed or Disabled settings refuse before callback persistence, spend
or allocation. Disabled remains the default; ordinary routing remains legacy.
Literal start@1 stays readable but is not a fresh-start path. Admitted start@2
work retains its original pins across later disable/default changes and still
requires current selected-role/repository authority, Pause/move and native proof.

Source capability projects readiness `{enabled:true,reason:coding_engine_available}`;
this is neither per-org admission nor installed/loaded/operational acceptance.
The strict historical false/downstream-unavailable pair remains readable. Settings
copy in both locales distinguishes capability, admission and saving from a run.

The existing four distinct actual roles and their genuine task/SID/INTEGER results
progress through development, independent review, QA and final live manager.
The selected final manager alone performs the existing local guarded expected-head,
non-admin action and independent actual MERGED/commit/aware-time observation before
its own callback. No daemon PR I/O, job executor or callback substitution is added.
Fresh revisions and the bounded no-action manager successor use the same return.

After manager_completed records merged work AND every original stage has successful
host/quiescence and positive outer settlement, additive event@9/return@1 completes
the original source and structural carrier in that SAME transaction. Their final
summary/directory and note derive from the genuine manager result; source SID and
all original results remain, and the carrier gets no SID/result. The independent
reader derives every changed task field; rollback/full target validation uses the
original aggregate allowance. Literal old complete/held histories are not adopted
or rewritten. Returned metadata uses run@8, retaining old run@1–7 readers.

Only a committed authenticated return may notify the ordinary parent and publish
the existing terminal task history/thread tail. Runtime wiring rediscovers returned
sources omitted from nonterminal scans, using complete readers and the native
stored thread marker to avoid a second notification. A partial thread tail stays
with its original owner. Coding leaves/carriers never enter legacy allocation.
The existing root terminal-worktree tail remains separately attributable.
Ordinary task source admission now checks the same original daemon epoch at
queue enqueue/reservation/raw dequeue, direct worker submission/dispatcher/task
entry, task/session allocation, workspace/scratch preparation and provider entry.
Production construction binds actual queue/org/orchestrator/tracker/host references;
missing, detached or replaced owners refuse. Explicit standalone constructors
retain their existing offline contract. Recovery purpose never exempts a new
session registration. The shared host checks before FIFO/grant, per-attempt
preparation and final native launch, including retries; task self-launch validators
also recheck. Existing containment, terminal winners, cancellation, tracker cleanup
and native retirement retain their original owners. A check alone proves no native
quiescence.

Only selected original P2/P3/P4/P6 TASK publications carry a private exact task,
metadata, queue, attachment, phase and epoch object through queue/generation
adapters. P2 uses its retained sweep/body-or-native-tail owner; workflow P2/P3 uses
its original workflow owner; P4/P6 requires the existing active publication owner.
Authenticated generation raw put still follows its genuine claim and exact ack.
No generic recovery-thread or metadata bypass grants queue/session/provider use.
For original unheld non-v2 retry/recovery-deferred Pause tasks during P6,
task_pause._discover_pause_roots passes the journal owner_identity metadata through
_publish_pause_task's default generation-aware adapter. The actual selected task
publication reaches queue admission and insertion; genuine insertion or SAME
pending acknowledgement is observed before release acknowledgement. Ordinary ready
calls retain their original custom publisher and metadata/call shape. Held and v2
branches retain their existing authentication and explicit non-generation publisher;
stop, failed epoch or changed ownership still refuses and reservations retire through
the existing finally. This task-admission source stage is complete; whole-feature
integration remains incomplete and must not activate or merge independently.

Ordinary thread production now checks that SAME gate before new message,
attachment, dispatch-task, SYSTEM/cap/token, queue insertion/dequeue and runner
reservation/preparation/provider effects. Original per-attempt and final launch
validators recheck; StartupUnavailable propagates without a synthetic FAILED or
DECLINED invocation. Bound org/orchestrator/tracker/host and the original per-org
thread queue must still agree; ready-only dynamic attachment inherits the same
gate before exposure. Explicit offline constructors retain their existing contract.
Queue-owned queued/in-flight dedup and the durable launch claim remain separate.
The consumer checks before a nonblocking pop; it never waits across the gate and
then pops a closed epoch. A later stop retains existing settlement/native ownership.

Cold P2 mandatory thread bookkeeping keeps its actual retained sweep/context and
body-or-registered-tail owner; no queue/session/provider bypass follows. Only the
ORIGINAL P4/P6 thread handoff, registered before submission, may insert while
recovery is closed. The actual real-loop put checks retained handle membership,
org/orchestrator/queue/token/kind, epoch/phase/current native scope, actual Task,
coroutine and Future. A private object by type, metadata or recovery purpose is
insufficient. Native observation still drains outside DB/marker/move/queue/gate
locks, with at most32 retained handoffs and genuine inserted/SAME pending outcomes.
Breaker/exchange direct producers recheck before store-owned effects and after
awaits. Existing callbacks, cleanup, cancellation and native retirement keep their
original owners; closed fresh admission supplies no quiescence proof. Private
thread-release service still requires actual serving AND recovery readiness,
current native ownership and independent reviewer controls, with no new caller.
This named thread source stage does not complete the feature or authorize a
partial merge/activation. Runtime cases and sensitivity PROOF remain NOT RUN;
maker0/3 QA0/3 and the root's final independent review/QA obligations are unchanged.

Ordinary job submission, core/reservation, runner registration, output/scratch
preparation, final native spawn and terminal-resume production now check the
SAME daemon startup gate. Checks precede allocation and repeat after awaited
locks; recovering/failed/stopping admission raises StartupUnavailable without
inventing FAILED, a move deferral or successful no-op. Actual original state,
org/DB/orchestrator, epoch, request loop and resolver must agree. Early reference
attachment closes anonymous lower run_job admission even before P4; P4 binds
and reads back its SAME gate/loop/resolver without I/O. Ready-only dynamic org
attachment inherits these references before exposure. Explicit standalone
construction keeps its offline contract; missing production attachment refuses.

A final-admitted asynchronous spawn retains its original Task/registration and
native attempt until actual creation is observed, even if its waiter is cancelled.
The observed Process is captured before fallible accounting. Closure during
creation suppresses script input and requests original owned termination/reap;
cancellation retains its primary cause and any cleanup failure. The registration
also occupies the org's existing native-user close barrier through its committed
postcommit tail, or remains retained on uncertainty. Signals, output pumps,
terminal CAS/first cause, registration release and native cleanup use identity
validation independently of fresh admission. No closed gate proves quiescence.

Job-terminal notification refusal is separate from execution/terminal COMMIT;
original durable blocked-waiter intent and terminal evidence remain. Deferred
native callbacks are removed only after real registration and renewed admission.
Ordinary Pause pending-job rediscovery propagates startup refusal before release
acknowledgement/cursor advancement. Native P6 discovery remains unchanged and
holds pending jobs without launching. No new deferred queue or recovery exception.
This named job SOURCE stage leaves whole-feature integration and final independent
review/same-head behavioral QA with ROOT13137; runtime/proof NOT RUN, maker0/3
QA0/3 unchanged, under10s UNVERIFIED. No partial merge or activation follows.

Ordinary dream/work-hour/schedule production now checks the same global gate
at queue insertion and checked nonblocking dequeue, direct scheduler and runner
entries, native reservation, preparation, retry and fallback self-launch. Checks
precede runner registration and repeat after awaited admission/teams/DB locks and
repository refresh. Original queue/org/DB/orchestrator/tracker/host references
and epoch are bound before exposure, including ready-only dynamic attachment.
Explicit standalone construction keeps its existing contract; missing production
references refuse. Existing cadence, catch-up/startup_deferred, due/expiry,
recurrence, Pause/move, agent/team and native eligibility remain independent.

Only original P6 dream/wake/schedule handoffs may insert during recovery. The
retained registered-before-submit owner authenticates exact phase/kind/source,
queue/org/orchestrator/epoch and actual loop/Task/coroutine/Future under the active
native scope. This is observed on the real loop, never by calling a native-thread
validator there. Original inserted/SAME queued-or-in-flight ownership supplies
acknowledgement; no consumer runs before P8. Native selectors and P7 recovery
retain their original contract. Stop/refusal never consumes their history.

Actual executor workers remain joined under existing org runner/native ownership
through wrapper cancellation; fresh admission is separate from original terminal,
cancellation, signal, cleanup and DB settlement. StartupUnavailable escapes as
unavailable, without synthetic source failure, successful move deferral or no-op.
Scheduled enqueue failures report the genuine committed pending/firing source.
Work-hour/schedule spawn callbacks recheck actual state/org and the awaited DB
lock before the first task ID. Their original once/current-team/partial-commit
semantics remain; a postcommit delivery refusal preserves task/source rows and
reports the committed task scope without granting another allocation.
This named producer SOURCE stage leaves the whole feature incomplete. Runtime
cases/proof NOT RUN, maker0/3 QA0/3 unchanged, under10s UNVERIFIED; root retains
full final-source independent review, same-head behavioral QA and coherent delivery.

Document/F5 source admission checks the selected original draft/review dispatchers,
submission/revision/receipt stores, authority and activation references, in addition
to the existing seven task/producer references and daemon epoch. References bind
before exposure, including ready-only dynamic attachment; another store sharing
an org pointer is insufficient. Ordinary claim, dispatch/preparation/reservation,
stage, graph/receipt/collection and discovery entries check before capture/effects
and renew after their own asynchronous locks and inside the original writers.
P2 carries its retained sweep owner; P3 uses the same synchronous cores; P6 keeps
its actual publication scope. None grants a fresh claim, session or host launch.

Native terminal/cancellation/cleanup remains independent of fresh admission.
The original retained invocation owns narrow final reconciliation; completed native
sources keep their genuine result/bridge/stage and durable pending delivery when
receipt/graph publication is held. Ordinary direct calls still raise
StartupUnavailable. Postcommit publication refusal identifies the committed tasks;
it supplies no delivery acknowledgement or synthetic execution failure.

Selected document admission now carries the same authenticated store/org/gate/
epoch and captured references through the original profile, publisher and short
DB lease/business writer boundaries. Pause preparation and final launch renew
that validation after original root/move/DB/terminal ownership; postcommit registry
admission checks precede insertion. A closed gate cannot strand an owed original
no-launch settlement: the committed preparation keeps its journal/SID/reservation
until native settlement succeeds, and failed settlement retains uncertainty and
the primary error. Unselected default authority/Pause callers and owed release remain
ungated. The reference-only P4 binder validates all seven captured identities and
the three producer attachments, preserving its actual Task/coroutine/phase/loop
owner and ready-only dynamic attachment. Genuine P2/P3/P6 document owners retain
their existing complete validation; no caller flag grants recovery authority.

Coding source admission separately captures original start/dispatch/roles/import
owners, their support stores and authority references beside the unchanged task
and document tuples. Request-created coding settings retain their own operation,
org/DB/state/epoch/loop references. Ordinary starts, queue preparation, selected
reads, settings/import/mapping writers, advancement and notifications renew
startup admission through original awaited and authority/Pause/registry owners.
Shared complete schema/source-readiness helpers remain available to P0.

Original P2 selected task publication and P4/P6 acknowledged return/redelivery
keep their actual native owners; no new stage or session exception follows.
Genuine callback, terminal, cleanup and atomic performed-work return remain owed
when startup closes. Fresh follow-on work stays pending without rewriting its
source/carrier/results or calling a closed gate proof of native quiescence.

Both coding return histories pass the SAME selected ordinary/P4/P6 validator
into the original `_update_task_history` owner. It checks entry, after its
original task/workspace/recent-history observations and immediately before the
original file write; closed or replaced ownership refuses before the next
effect. Selected calls report whether a write actually returned, so recovery
requires BOTH performed writes and counts each before later fallible inspection.
A skipped write cannot borrow an existing file as success. Default terminal and
cleanup callers keep their ungated None/no-op contract and existing fences.
Earlier writes, committed return and pending delivery remain attributable on
later refusal; original publication drain, marker and positive queue ack still
own completion. Synchronous rechecks do not guarantee atomic exclusion through
arbitrary filesystem I/O and grant no new recovery/session exception.

Direct task/move redelivery, blocked-task resume and Pause discovery now
capture the SAME ordinary or genuine P2/P4/P6 selection before observations,
reservation, chain changes and publication. Original DB/move waits renew it;
parent/chain writers retain current-owner, causal result, failed-lineage and
dedup predicates. Native publication requires actual insertion or SAME pending
ownership. Job redelivery retains its existing checked native registration.
Genuine failed-human callback settlement and job drains stay independent of fresh
admission; refused parent/bookkeeping retains the original operation, phase,
result, pending history and first cause. Job teardown and whole root-finally
cleanup keep their native controls, uncertainty and batch-suppression ownership.

Direct zombie, disk, workspace-cleanup and projection entries check the actual
daemon gate before fresh selection/effects and renew captured references after
their original waits. Zombie warmup/TTL and native cleanup are unchanged. Disk
keeps stop.json/no-follow, date reservation, initial summary before native writers,
same-night/no-catchup and the original budget; later refusal reports actual
marker/report/writer/process scope without rollback or a successful-sweep claim.
Owned worker termination/reap/report and native writer retirement remain owed.

Workspace cleanup renews admission inside its original native reservation,
report audit and awaited task-ID writer. Original executor workers remain joined
through cancellation under the org runner barrier. Postcommit refusal identifies
the original pending task, without replacement allocation or schedule reset.
Projection carries a selected callback through the original sweep, plan, probe,
profile claim and profile-store writer; standalone defaults remain available.
Refusal before admission is unavailable, not a failed projection. Already admitted
profile compensation/retirement remains owed. Actual submitted maintenance workers
remain retained through cancellation at daemon/org close and runtime-swap barriers;
wrapper cancellation is not native completion. Swap retains the original ready
epoch and binds its new selected profile reference after the existing idle checks.
All new startup checks are reference/phase renewals, not new discovery scans;
synchronous checks cannot provide atomic exclusion across arbitrary OS I/O.

These named document, coding and direct maintenance source stages leave the
whole feature incomplete. ROOT13137 retains remaining HTTP/UI/readiness/
launcher/counter closure, full independent final-source review and same-head QA.
Behavior/proof NOT RUN, maker0/3 QA0/3 unchanged, under10s UNVERIFIED;
no partial merge or activation.

The original startup lifecycle now owns one P2-P7 native operation. P0 validation
remains pre-serving; lifespan binds the actual request loop and yields promptly.
Each original org's settled sweep precedes its original v2 publication; actual
producer outcomes distinguish local holds from unknown/failed transaction or
COMMIT. P3 workflow discovery, P4 binding/returned notification, P5 compatibility,
P6 blocked/move/Pause/tokens and P7 stale producers run once in that order. The
original sweep result itself supplies P6 tokens, with no reconstructed history.
The public thread binder performs only reference assignments. Boot P4 owns its
notification phase; ready-only runtime wiring and state.add_org explicitly keep
the original returned selection/error policy. New attachments inherit the same
gate/read domain before publication. Add/remove/swap refuse recovering, failed
or stopping epochs; they never rebind the original boot attachment tuple.
All original suffix consumer bodies wait one activation latch, including every
worker/scheduler/reaper and dashboard bootstrap. Handles are retained as they
are prepared, so partial preparation cannot orphan earlier ones. Ready commit
and latch release have no intervening await. Cold dashboard disk load runs on
the existing owned native executor after activation; its actual Future is joined
before resource close. Existing periodic warm/cancellation behavior is unchanged.
Stop closes intake before awaits and joins actual recovery/tail/binding/read and
partial consumer owners before host/DB close; exchange reaper is included.
This remains incomplete source delivery: root owns UI/launcher/consumer and
instrumentation closure, correctness execution and independent final review/same-head
QA/guarded merge. Behavior/resource/performance/proof NOT RUN; under10s UNVERIFIED;
no partial merge, live activation or new correctness allocation follows.

HTTP startup admission captures actual registered early route/root/assets/SPA
objects and their ordered dispatch bindings before middleware construction.
Missing/replaced app/state/gate/read-domain/route references refuse pre-body;
only the original seven early GETs, new GET /api/v1/health/ready and actual static
contract enter. Auth/OrgDep/loopback remain original. HTTP query, unsupported
method, unknown API or slash redirect never supplies permission. After ready,
original routing/auth/validation and all native launch/reviewer controls apply.

Early thread list/detail/messages use the same ten canonical readers/DTOs with
worker-only filesystem identity, read-only SQLite snapshot and encoded response.
Two workers/four admissions share one2s cooperative deadline including queue delay,
2,000,000 opcode allowance,4096 fetched rows,4MiB raw plus4MiB encoded,1MiB value
and128 attachment checks. Negative early limits refuse; ready legacy limits stay.
Original state/app/gate/epoch/org/path/read-domain references renew before content
publication; missing production bindings never fall back to canonical DB/lock.
Actual native Future/connection/slot and callback-accounting acknowledgement remain
through cancellation/drain; a completed Future alone is not retirement. Failed
close retains an incomplete owner/count. Nested read SAVEPOINT cleanup preserves
its original error even if exhausted cleanup also refuses; actual close is owed.
These are finite cooperative work bounds, not hard OS-I/O/RSS timing guarantees.

/health preserves liveness200/status/active_runtime and cached sanitized
host_sessions, adding bounded startup state/phase/serving/readiness/stop/code/
guidance and timing scalars. /health/ready returns200 only with matching actual
listener AND accepted ready epoch; recovering/failed/stopped/unbound return503.
No DB/history/probe runs for readiness, and no native cause/path/ID collection is
public. owner_to_serving_ns starts at owner construction, never launcher start;
launcher_duration_ns remains null because usable API/UI and original parent
clock evidence are unavailable; private actual-owner observations are described above. Absence of startup
fields on old servers means unknown. TS mapping supplies no polling/activation.
Root still owns final consumer/instrumentation assessment, isolated OpenAPI snapshot
sync and finite behavioral acceptance. Source is not <10s or runtime acceptance.

After return, ordinary callers own their later state and later allocations;
original complete captures and stable ancestry identity remain validated, while
all coding-owned current task rows still compare exactly. Returned graphs no
longer block drain; J and all retained new bytes still require a compatible reader.

A later ordinary ancestor cascade retains a genuinely returned coding graph as
immutable history, including through terminal ordinary dependency paths and
alongside fresh or already cancelled graphs. Full return replay, every original
successful/quiescent native and positive outer closure, terminal owned rows and
original capture ordering are required; a complete label alone never qualifies.
Only reachable live ordinary tasks receive their sole cancellation overlay;
only fresh coding graphs receive a fence. Returned source/carrier/stage/result/
event/PR/merge/owner bytes receive no write or control. Batches containing a
returned graph use closed cancellation command/event@2, or ordinary-cancellation@2
when no fresh graph exists; retained anchors select the original authenticated
return or cancellation event. Literal @1 retained anchors still require prior cancellation.
Independent readers reconstruct membership, every anchor and audit/causal order,
then compare full current SQL within the original transaction and ONE aggregate
budget before COMMIT. Missing return, held/unknown/unsettled/native-uncertain work,
hidden live descendants, drift or malformed membership/order refuse and roll back.
Point stops of coding-dependent ordinary ancestors still refuse before writes.

Native deferred recovery remains unavailable. Source/static delivery is not native
or remote workflow acceptance. No deployment, host maintenance/restart, migration,
import, role assignment, activation or historical settlement follows. Benze or the
designated host operator owns existing guided deployment/setup; the human Founder
owns original import/settings. First genuine scratch through four actual roles
and actual merge remains required before ordinary routing. Revision/control,
cross-team persistence, two-org/default/override/backup/rollback remain open until
demonstrated. Python units RETIRED; integration INCLUDING callback/lifespan/
SessionTracker SKIPPED24342; Swift UNEXECUTED/FOUNDER-EXEMPTED/NONBLOCKING32093.


<!-- K3a result-owned queued bootstrap: TASK12003 / ROOT10693 step87 -->
## Result-owned coding start and queued ownership (K3a)

The new closed manager decision is exactly `{action:workflow_start,workflow:COMMAND}`.
`COMMAND` is canonical `workflow-coding-start@1` with exactly
`format,operation_key,release,mapping,work,implementer_override`; release and mapping
are the existing exact pins. Work is exactly `brief,repository_id,object_format,base,paths`:
nonempty brief at most16384 UTF8 bytes, full lowercase SHA1/SHA256 base, and1..64
validated relative selectors. There is no developer head before genuine output.
`happyranch workflows coding run-start --from-file ABS --emit-decision` validates
and prints only this decision for the initiating manager's own final callback.
It contacts no daemon and allocates nothing. No fresh HTTP allocation route exists.

The actual callback and result-action entries select this family before v2
spend/claim or ordinary decision effects. Fresh callback admission refuses before
persisting an unsupported start result, including on legacy I. Its production owner refuses
`coding_downstream_unavailable` before claim consumption or allocation. Readiness
is always false in K3a; there is no caller/config readiness Boolean. Behind that
boundary, the real single admission transaction validates the manager's own full
INTEGER callback, actual ordinary-purpose TaskInvocation/SID and complete RetryClaim,
current policy/Pause/move/ancestry, imported release, pinned role generation,
implementer-only override, four current independent eligible principals, existing
repository scopes, capacity and lower source limits. Existing v2 uncommitted
spend/claim/ack methods keep their native evidence and CAS semantics inside that
same graph transaction. A still-active historical v1 continuation envelope
refuses as unavailable; its self-committing spender is never bypassed or composed
inside bootstrap. No self-committing delegate or task helper is composed.

Bootstrap inserts pinned authorization/binding/context/activation/instance, a
structural nonexecutable carrier parented to the authentic source, one pending
development leaf parented to the carrier, a queued attempt and `run_started`.
Instance root is the carrier; Pause root remains the actual top source ancestor.
CAS starts at1/current_revision0/target_revision1. Source waits delegated;
its original parent/counters/SID remain. Immediate cyclic FKs use uncommitted null
run pointers only until the real attempt/event are inserted and both pointers set.
Full typed native/J/history/FK/schema closure precedes COMMIT. Failure rolls back
all rows, pointers and claim writes; the prior genuine callback remains intact.
Exact same-result/key/body replay returns original IDs without allocation/debit/
append; changed source/result/command conflicts. No carrier result/session,
assignment, submission, approval or receipt is manufactured.

Agent-read authorization/binding/context and initial scope have explicit new @2
encodings; old custody @1 bytes retain their own readers. Context pins original
source/result/claim, authority/profile capture and all complete native checkpoint
observations across waits. Pre-bootstrap native updated_at/last_heartbeat values
are retained independently; every other25 task fields must agree. Saved queued
rows compare all27 current native task fields exactly. K3a supplies no post-start
heartbeat/PID/result/terminal bridge, so unsupported mutation/drift refuses.
The existing native task row primitive is used only after complete prospective
move checks; ordinary task insertion retains its original guard.

The saved reader independently checks these original native start predicates:
The captured invocation has no uncertainty, including producer-settled uncertainty,
and its brief fingerprint equals SHA256 of the exact frozen source brief UTF8.
The frozen source has neither a completed_at nor a zombie_flagged_at marker.
Native journal shape and internally consistent pin digests alone do not prove
original validity. Running/count/identity checks use original frozen evidence;
later native journal phase or current authority is not substituted for that
admission. This correction adds no native lifecycle writer or execution proof.

Complete mixed historical and queued readers share the enclosing row/raw-byte/
step budget, including schema(data=False), cold/reopen, templates, activation and
context pins, recovery and cutover drain. No schema DDL/reference changes, startup
upgrade, old-byte rewrite, custody Q/U/P issuance, R2 or daemon Git/object probe.
Future event families and partial/orphan/foreign/dual-owner graphs refuse. Valid
`coding_source_waiting`, `coding_carrier`, `coding_attempt` ownership cannot launch
through queue, draft/F5, ordinary chain/fanout/recovery or parent wake. Pause/move
rediscovery preserves this wait. Native cancellation of a selected queued graph
is refused before mutation, including ancestor cascade and after the DB-lock wait;
control/terminal writers remain later root-owned work. Initial selected move
projection contains effective actual roles, not unselected replacement candidates.

GET `/api/v1/orgs/{slug}/workflows/coding/runs/{instance_id}?session_id=CURRENT`
and `workflows coding run-show INSTANCE --org ORG --session-id CURRENT --json`
reuse current executable-manager loopback/session admission. Only the original
source-manager principal may select metadata, before private run decoding;
current mapping/effective-role authority is checked again. Closed
`workflow-coding-run@1` exposes exactly
`format,instance_id,release,mapping,effective_roles,cas_version,current_revision,target_revision,state,current_attempt,history,closeout,readiness`.
Queued history has its sole `run_started` metadata; no private native proof,
source callback, peer findings, repository content or execution grant is exposed.
Bearer fallback and browser/remote reachability remain absent. Raw and normalized
native GET candidates are denied independently using the existing one-decode
algorithm before credential lookup/forwarding. Served schema and classification
carry the same selected family; unrelated decisions keep legacy wire behavior.

This is disabled SOURCE delivery only. Independent full review, same-final-head
scoped SOURCE QA, normal commits/nonforce publication, local static all, literal
hosted Source Boundary Lint + Web Lint(Node24), and manager guarded merge remain.
Python units RETIRED; integration INCLUDING callback SKIPPED24342. SQL/parser/
behavior/security/race/mutation/red-green/repetition/native-build/E2E/protected/
executable-QA/test-authoring proof UNEXECUTED/FOUNDER-WAIVED139429/430, never PASS.
Remaining K3 dispatch/native/control/supplements and K4–K6, genuine-agent revision/
fresh review/QA/CI/merge, two actual org defaults/override and backup-copy acceptance
remain on sole ROOT10693. No live migration/assignment/deploy/restart/enable/cutover.
<!-- /K3a result-owned queued bootstrap: TASK12003 / ROOT10693 step87 -->


<!-- Active coding contract: Founder THR139 seq640 / ROOT10693 step79 -->
## Active coding contract — agent-read subjects (seq640)

Founder THR139 seq640 supersedes the earlier seq559/562 coding content-serving
prerequisites and the historical K2b setup/mapping/source checkpoints below.
Those checkpoints and accepted artifacts/hashes remain historical evidence;
they do not require rebuilding their producers. Document contracts, native
permissions, F–J definitions/references and retained history remain in force.

Coding records repository identity, object format, immutable comparison base/head
and optional PR navigation. An eligible assigned agent reads code through its
existing authorized repository tools. Progression requires that agent's genuine
task/session/result, outcome and findings bound to the exact subject, with current
authority and independence. A descriptor/digest grants no access, inspection,
execution or merge authority. Unavailable access, a different subject or an
unassigned/inauthentic result refuses progression. Changed head requires fresh
independent review and QA; prior results and contributor/assignment/retry history
remain historical. Branch/PR navigation never identifies approval.

Reduced K2b on SAME draft PR1083 retains the fixed634-byte release import,
Founder predicate, original publication/admission/replay/current-authority and
complete template/history/cold/recovery/drain consumers. Import support now lives in
`_coding_import_support.CodingImportSupport`, extracted without a setup/adoption/
source prerequisite or expanded agent import authority. Draft setup issue/withdraw,
store adoption/mapping, source mint/refinement and their exclusive route/CLI/model/
contract/native entries are removed. Merged K1 stays dormant with its exact main bytes;
no daemon object read or content-resolution preflight is a coding prerequisite.
Rejected R2 private records, org fence and privacy projections are not implemented.
Keep bounded historical positive readers and malformed/orphan/foreign-data refusal;
reader retention neither restores a producer nor grants current access.

K2c separately supplies org role defaults, eligible repository scopes, per-work
implementer override, independence and pinned generation, not local-store custody.
K3 is the first atomic coding instance/carrier/development-task writer and ships
its own complete readers/recovery. K4 adds review/QA/revision with fresh exact-head
acceptance; K5 adds successful exact-head lint/static CI and guarded expected-head
merge/closeout; K6 adds operator entry/cutover. ROOT10693 retains genuine-agent
revision/re-review/QA/CI/merge, two-org defaults/override and backup-copy operational
acceptance. Coding remains disabled and runtime/security acceptance is missing.

No live migration, grant, provider allocation, deployment, restart, assignment,
cutover, cleanup or human trial follows. Python units are RETIRED; integration
INCLUDING callback is SKIPPED24342. SQL/parser/product/security/mutation/red-green/
repetition/native-build/E2E/executable proof is UNEXECUTED/FOUNDER-WAIVED139429/430,
never PASS. Independent FULL code review, same-final-head scoped SOURCE QA,
normal hooks/nonforce publication, lint/static checks and manager guarded merge
remain required. No new reader-only publication or replacement authority framework.
<!-- /Active coding contract: Founder THR139 seq640 / ROOT10693 step79 -->

<!-- K2c agent-read role defaults: TASK11987 / ROOT10693 step83 -->
## Agent-read role defaults and pinned selection (seq640)

K2c adds current executable-manager CLI POST/GET
`/api/v1/orgs/{slug}/workflows/coding/mappings?session_id=CURRENT` and
nonallocating POST `/api/v1/orgs/{slug}/workflows/coding/role-selections?session_id=CURRENT`.
These reuse existing native task-session/Pause/move/publication admission, with
no bearer fallback, browser or remote connector reachability. CLI forms are
`workflows coding mapping-publish --org ORG --session-id CURRENT --from-file ABS --json`,
`mapping-show --org ORG --session-id CURRENT --json`, and
`role-select --org ORG --session-id CURRENT --from-file ABS --json`.
Keep the original file/key on transport loss; no automatic retry.
The route streams the bounded body outside authority/team locks. The service
alone acquires authority, then teams, then DB/binding ownership for GET, publish
and selection. Native checkpoints use DB-before-binding order; DB is released
before waiting for teams. This agrees with authority-first configuration writers
and agent-update bootstrap/compensation. After each awaited acquisition the
serving org/session/team is rechecked; native invocation, executable manager and
current policy are rechecked at the coherent DB boundary before private decoding,
current use and writes. No non-reentrant authority reacquisition is needed.

Canonical bounded64KiB `workflow-coding-mapping-command@2` has exactly
`format,operation_key,expected_generation,mapping`. Mapping has exactly
`release,roles,eligible_replacements,repository_references,authority`.
Release remains the imported fixed coding v1/digest; four roles are implementer,
code_reviewer, qa_engineer and agent_manager, each `{principal,team}` with
permanent active agent IDs, four distinct principals, at most16 eligible
replacements per role and no replacement collision with another default role.
Agent manager is an actual executable registered agent manager, never Founder.
Every default and eligible replacement must belong to that initiating manager's
own authorized team, including the actual selected implementer override. A worker
in another team of the same org refuses even with an identical repository origin;
shared membership/repository access grants no cross-team action. Original saved
role records validate this radius against their authentic issuer, native manager
team and captured snapshot; current use validates it again under current policy.
Previously admitted out-of-radius role records now fail closed as invalid history,
without rewriting bytes or using today's roster to invent historical authority.
Valid same-team history and separate historical custody formats remain readable.
Each of1..8 repository references is exactly
`{repository_id,repo_key,origin_digest,object_format:sha1|sha256}`. Existing org/key/
configured-origin identity and repository permission must agree for every role
and eligible replacement. No local-store adoption, Git/object/content/provenance
probe, dedicated setup/source capability, R2 record or inspection Boolean exists.

Append-only `workflow-coding-agent-read-role-record@1` uses the unchanged TEN J
mapping columns, with explicit org, full original command/digest, genuine issuer,
native complete admission snapshot, mapping and original timestamp. It has no
custody/adoption fields and never reinterprets historical mapping-record@1.
One existing admission transaction writes mapping, five-column admission and
original native `coding_source_admission` capture; complete cold closure precedes
COMMIT. Full command replay in the same org/principal/key namespace precedes
fresh generation CAS and returns original IDs with current disposition, without
recapture or grant restoration. Changed bytes conflict. New POST201/replay200,
GET200; malformed422, missing404, revoked403, stale/conflict/unavailable409,
corrupt-history500. Closed `workflow-coding-mapping-receipt@2` has exactly
`format,id,generation,mapping_digest,mapping,replayed,current`; current is
`{state:active|revoked,reason:null|Token}`. GET refuses currently revoked use.
After every await and in the writer, native current session/owner/ancestry,
Pause/move, full published policy and role/repository eligibility are rechecked.

Canonical `workflow-coding-role-selection-command@1` is exactly
`{format,mapping:{id,generation,digest},implementer_override:null|{principal,team},subject}`.
Only implementer may override, from that immutable generation's eligible set;
all four actual selected principals stay independent and current. Defaults and
all pinned history remain unchanged by selection. Later default updates affect
future default GETs; an older exact pin is selectable while current authority
remains eligible. Unknown pin404, mismatched generation/digest409. Subject is
existing `workflow-code-change@1`: exact repository reference/object format/full
base+head are required; branch/PR navigation carries no approval identity.
The closed response `workflow-coding-role-selection@1` contains exactly
`format,mapping,roles,subject,overridden,coding_enabled:false`. It allocates no
task/result/instance/carrier/run/stage/effect or entitlement, and proves neither
object existence nor actual inspection. Current complete storage still refuses
ALL coding runs/attempts/events; K3 must compose genuine per-work contributor
history before its first allocation, never trust caller-provided history.

Saved/native/current/public/CLI/template/schema(data=False)/cold/reopen/recovery/
drain consumers validate complete mixed old/new mapping prefixes and all TEN
original typed columns, own full admission/publication/native capture and original
command/generation identity. Unknown/malformed/orphan/foreign/gapped/conflicting
families refuse without repair. Shared bounded prefetch/native row/raw-byte/step
budgets remain; no per-family reset, schema-object filter, startup upgrade, old-byte
rewrite or empty reset. Old binaries may refuse this new saved record family even
though J DDL is identical: use compatible code, with no downgrade-readability claim.
Historical setup/mapping/source/import readers remain; new role records cannot
serve as historical custody/source grants. F–J definitions/references, document/
human receipts, native security/lifecycle mechanisms and dormant K1 are preserved.

This is SOURCE delivery only. Independent FULL review, same-final-head scoped
SOURCE QA, normal hooks/nonforce publication, local static all, literal hosted
Source Boundary Lint + Web Lint(Node24) and manager guarded merge remain required.
Python units RETIRED; integration INCLUDING callback SKIPPED24342. All behavioral/
SQL/parser/security/mutation/red-green/repetition/native-build/E2E/protected/
executable/test-authoring proof UNEXECUTED/FOUNDER-WAIVED139429/430. Coding remains
disabled. ROOT10693 owns K3–K6, genuine-agent rejection/revision/fresh review/QA/
CI/merge, two actual org defaults/override and backup-copy operational acceptance.
No live migration/grant/assignment/deployment/restart/cutover/trial renewal follows.
<!-- /K2c agent-read role defaults: TASK11987 / ROOT10693 step83 -->




<!-- K2b source producer checkpoint: TASK11918 -->
**Historical nonallocating coding source producer — ROOT10693, Founder139562/559/329/595/606.**
Agent-only loopback POST `/workflows/coding/sources?session_id=CURRENT` and
GET `/workflows/coding/sources/{authorization_id}?session_id=CURRENT` use the
existing current manager transport, without bearer fallback or browser/remote
reachability. CLI `workflows coding source-mint --org ORG --session-id CURRENT
--from-file ABS --json` and `source-show AUTHORIZATION --org ORG --session-id
CURRENT --json` preserve one original file/key, with no automatic retry.
Canonical bounded64KiB `workflow-coding-source-command@1` is exactly
`{format,operation_key,parent_id,mapping:{id,generation,digest},action,work}`;
work exactly `{repository_id,store_owner,object_format,base,paths,commands}`.
Only import-release-coding, publish-coding-mapping and adopt-local-store source
actions mint here, within the current reusable U and all narrower parent edges.
Server derives genuine current task/principal/team/SID/manager_decision,
ordinary invocation/Pause/move/complete ancestry, callback absence and original
limits. Native owners and executor enforcement are composed unchanged.
The exact selected mapping/adopted store, source_rule/base_rule, executable
manager repository policy and action/path/argv intersections precede private
object work. Existing supervised adopted-store object machinery verifies the
fixed full base outside writers under one deadline; it returns no repository
content. Fresh native ALL27/ancestry/current policy checks repeat after awaits
and in the atomic writer. No timestamp exemption or counter refill applies.
A genuine immutable source P and its complete OWN five-column admission mint
atomically. Exactly one ORIGINAL source-phase capture retains all original27
source/ancestor/native session-start/invocation/counter/limit bytes; NULL result
and claim mean zero callback/carrier/task/run/stage allocation. Same-owner source
refinements obtain distinct own admissions while retaining the original
source_admission_id and capture. P/U/capture encodings and F–J DDL stay unchanged.
Private audit action coding_source_operation stores closed
`workflow-coding-source-operation@1 {format,org_slug,principal,authorization_id,
admission_id,session_id,command_bytes_base64,command_digest,created_at}` per mint.
This D1 operation metadata preserves each complete command/key/principal for
byte replay; it grants nothing and cannot replace an original capture.
Replay finds original bytes before fresh CAS/object work, returns original
identities/current disposition and never recaptures, reissues or restores grants.
Closed unsigned `workflow-coding-source-receipt@1` is exactly
`{format,authorization_id,admission_id,source_admission_id,operation_key,
request_digest,replayed,current:{state,reason}}`; current is active/inactive/revoked,
with a reason only for revoked. Changed same-key bytes conflict.
Mixed setup/import/mapping/P cold consumers share bounded native source/mapping
capture/operation inventory and the enclosing row/byte/step budget. Full positive
native-row equalities and unknown/orphan/duplicate refusal cover schema(data=False),
OrgState reopen, recovery and drain; ordinary nonmapping watermark behavior stays.
Source expected_head remains NULL/own-development-output@1: it grants no arbitrary
head disclosure, review, QA, CI or merge. K1.resolve remains unavailable because
no genuine exact-subject read entitlement is produced here. Allocating source
callback/C0, run C1/C2, native heartbeat and attempt/control/effect producers remain
K3 and later units, not fabricated subjects/results/grants. Agent release import
and source-to-action use remain separate from these mint metadata operations.
This is an incomplete SAME DRAFT PR1083 source checkpoint, never a delivered
coding engine, operational grant, deployment or merge. Independent FULL review,
same-final-head scoped SOURCE QA and literal lint/static gates remain required.
Runtime/security/rollback guarantees remain UNVERIFIED, engineering_manager-owned
before operational acceptance. Python units RETIRED; integration INCLUDING callback
SKIPPED24342; SQL/parser/product/timing/security/negative/mutation/red-green/repetition/
native/build/E2E/protected/executable-QA/test-authoring proof
UNEXECUTED/FOUNDER-WAIVED139429/430. No live setup/adoption/grant/assignment follows.
<!-- /K2b source producer checkpoint: TASK11918 -->


## Historical K2b mapping publication/adoption checkpoint — ROOT10693 step70

SAME incomplete OPEN DRAFT PR1083 now includes the explicit nonallocating
mapping writer and every own saved/native/current/cold/public/CLI consumer.
POST `/api/v1/orgs/{slug}/workflows/coding/mappings?session_id=CURRENT`
accepts canonical `workflow-coding-mapping-command@1` with only
`format,operation_key,expected_generation,mapping`; mapping has exactly
`release,roles,eligible_replacements,repository_selectors,authority`. Each of
at most eight selectors is exactly `repository_id,store_owner,repo_key,policy`,
with policy `adopted-local-store@1`. No paths, URLs, proof flags or server
capsules are accepted. GET on the same path requires the same genuine current
acting manager binding, selects the latest complete generation and rechecks
current setup/role/store eligibility. Both return the closed six-field
`workflow-coding-mapping-receipt@1 {format,id,generation,mapping_digest,mapping,replayed}`.
POST is201/new or200/exact replay, GET200; missing selection404, malformed422,
revoked manager/scope403 and unavailable/stale/conflicting admission409.
CLI `workflows coding mapping-publish --org ORG --session-id CURRENT
--from-file ABS --json` and `mapping-show --org ORG --session-id CURRENT --json`
use actual loopback session transport, without bearer fallback or automatic
retry. Preserve the original key/file after transport loss. Both routes remain
browser-excluded and native remote-denied on raw and normalized candidates
independently before credential lookup/forwarding.

Server selection derives the actual assigned executable team manager, task/SID,
unique native manager_decision session_start, real ordinary/v2 TaskInvocation,
Pause journal/generation, current tree/move admission and no own-SID callback.
Reusable M.setup roots authorize publication and each adoption separately for
its repository/action/store/format; neither roster membership nor a parsed
capsule grants permission. Fixed coding import and complete published role and
replacement eligibility are required; four contributors stay distinct. Empty
repository selectors require one unambiguous eligible setup; no arbitrary root
selection or empty grant becomes authority.

Configured primary stores are resolved server-side. A shared bounded no-follow
preflight checks custody, ordinary .git/config/object format/origin and object
inventory, refuses linked/indirect/symlink/alternate/promisor/shallow/replace/
graft/helper/unsupported stores, and executes no Git, hook, filter, credential,
fetch or clone. It derives server adoption-proof@1 and config/store identity.
After the await, repeat observation and compare before the SQLite writer, then
revalidate actual native authority/setup/current owner and generation in the
writer without filesystem/object work or awaits. Local adoption attests local
storage, not remote provenance or hostile same-UID/OS isolation.

One native admission transaction appends the exact ten J mapping columns,
complete canonical command/issuer/capture/M roots/adoptions/mapping bytes, and
its fixed original source-phase native capture. Full original task/ancestry,
unique session-start ID, actual invocation ID and admitted counters are retained;
result/claim/callback fields remain genuinely null. Final complete cold closure
precedes COMMIT; rollback preserves prior rows/ownership. Exact replay compares
org/real principal/key plus ALL original command bytes before fresh generation
CAS/adoption I/O, keeps original IDs and issues no work or renewed authority.
Historical validity does not establish today's permission. Complete positive
whole-row family matching covers mappings, original capture audit and all five
admission columns; orphan/foreign/malformed/unsupported families still refuse.
Central schema validation, also data=False, reopen/recovery and drain/downgrade
compose that same reader without recursive schema validation or ID exemptions.
Mapping-positive central scans, including the complete ordinary audit inventory,
use singleton rowid keyset pages with explicit overflow refusal. Before Python
fetch, every selected scalar/variable cell gates its original SQLite storage
class and each TEXT/BLOB UTF8 byte length (including multibyte/NUL suffixes),
and the whole row must fit the remaining shared raw-byte and candidate budget.
Original typeof and a separate refusal sentinel preserve genuine nullable NULL;
invalid/oversized rows never become coerced, truncated or partial success.
One call shares8192 fetched candidates,16384 traversal steps and16MiB accepted
raw bytes across setup, mapping, original-capture and central selected scans;
per-value1MiB and mapping/setup inventories1024 remain upper bounds. Bounds may
refuse earlier; they do not bound total SQLite/process/decoded-object memory.
Complete expected mapping/own-admission/source-audit rows are derived once from
validated records, indexed only for lookup, then compared in full with exact
native types. Original capture inventory and required setup histories are cached
only within this coherent call. No mapping row or supported audit is exempted
by ID or watermark. Unknown/orphan/malformed/duplicate evidence still refuses.
The SAME enclosing traversal allowance also covers metadata discrimination:
every structured node/member, duplicate pair and classified member string,
and malformed lexical prefix/member/whitespace/string/escape visits consume it.
Nested lists/objects and fallback after partial pairs-hook work never reset or
replace that allowance. Exhaustion refuses before a normal return, positive
admission or watermark advance; even small wide ordinary metadata may exhaust
it. JSON-decoder/encoding internals, total SQL/decoded-object/process memory
and wall-clock work are not bounded by this traversal or raw-byte accounting.
Ordinary nonmapping scans retain their historical discriminator/watermark
contract. This R1 source correction is not behavioral/security verification.
Mapping/setup/import have no run/host tail; compatible J reader is always needed.
F–J SQL/reference bytes and all historical document meanings are unchanged.

Current K1 exact-subject disclosure still requires a genuine immutable subject
and selected entitled admission; the placeholder remains unavailable. SAME-K2b
remainder is source capability attenuation/current K1 subject selection and its
original source captures/current readers. K2c–K6 remain per delivery-map; K3 is
the FIRST run writer. No task/run/stage allocation, live setup/mapping/adoption,
migration/deploy/restart/cutover or retired trial execution is authorized.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; SQL/parser/
product/timing/security/negative/mutation/repetition/executable QA proof remains
UNEXECUTED/FOUNDER-WAIVED139429/430. Source assertions/lint are no behavioral,
security or test-authoring PASS. Fresh independent FULL review then same-head
scoped SOURCE QA and manager disposition remain required. Keep PR1083 draft,
unmerged and incomplete until the genuine SAME-K2b remainder is delivered.


## Historical K2b fixed coding release import checkpoint — ROOT10693 / Founder139562/559/329/606

Manager step68 extends SAME incomplete draft PR1083. This source checkpoint adds
explicit fixed release import and its own historical/current/public/cold readers;
setup-only descriptions below retain their original checkpoint meaning. K2b
remains DRAFT/unmerged until mapping/adoption/current K1 and source attenuation
with actual nonallocating task/SID/invocation/Pause/ancestor capture producers and
all their own readers are complete. First coding-run writer is K3, not this unit.

POST `/api/v1/orgs/{slug}/workflows/release-templates/coding/1/import` uses the
existing Founder human predicate and current native authority admission. It takes
only canonical UTF8 `workflow-coding-release-import-command@1` with exactly
`format,operation_key,expected_current_version`; the selector is INTEGER 0..1 (only0 can create the fixed version1) and
the stable key is 1..128 token characters. The full command is at most1024 bytes.
No query/body actor, task, SID, namespace, definition, gate or pin selector is
accepted. The server selects the exact634-byte packaged coding.v1 resource,
SHA256 `6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8`,
namespace `release/happyranch`, template `coding`, version1 and pins
`workflow-coding-compiler@1`, `workflow-coding-validator@1`, `release-input@1`.
Human import creates no task/SID/result and does not require an invented machine
grant. Manager import remains unavailable until genuine source attenuation and
nonallocating admission producers ship on SAME K2b; identity/setup alone is not
machine authority. Existing org/team publication grammar remains unchanged.

CLI `happyranch workflows coding release-import --org ORG --from-file ABS --json`
sends the complete original file once with existing human transport; keep its
original key/file after transport loss. `release-import-show --org ORG --json`
uses GET on the SAME path. Both return a closed
`workflow-coding-release-import-receipt@1` with exact identity/version/admission
IDs, operation_key/request_digest/replayed, current `{state,reason}`, and the
unchanged disabled release projection. POST is201 for initial import,200 for
exact replay; GET is200 and never writes. Current state is admitted/unavailable,
not coding readiness. Entitlement precedes private decode; absent import is404,
invalid command/query422, stale admission/duplicate import/conflict409, corrupt
history/resource500, missing resource503. Both import methods are CLI-only,
browser-excluded and native remote-denied for raw and normalized paths
independently, including prefixes, one decode, literal query split and one final
slash. The original catalog GET remains write-free and enabled:false.

Resource I/O/reference construction/discovery occur outside writer locks; after
waits the real Founder and current native capture are revalidated. One native
admission transaction inserts existing draft/version/identity/version-link/
publish-operation/admission rows and validates complete cold closure before
COMMIT. `author_principal`/`published_by` retain equal typed canonical import
provenance `{format:workflow-coding-release-import-provenance@1,org_slug,publisher,
command_bytes_base64,command_digest,admission:{record,capture},created_at}`;
publisher is the actual Founder provenance, never a synthetic task principal.
The original command and native capture bind every row, exact resource hash/pins,
all five native admission columns and complete original publication/profile
journal. Ordinary document provenance is unchanged. Exact replay precedes fresh
CAS, returns original IDs, changes no authority pointer, and separately reports
current policy loss. A different key cannot create version2 or another import;
changed bytes under the same key conflict. Missing package I/O can hold a fresh
import while exact saved replay remains readable. Any partial/foreign/malformed/
extra/orphan/incorrectly typed relation refuses; failures roll back all new rows.

Only the owned write-free existing-layout install_or_recover branch privately
collects the validated audit device/inode/last-rowid. An outer Database RLock spans
transaction exit and publication after successful COMMIT, publishing that exact
prefix rather than a newer MAX. Validation failure, rollback, uncertain/failed
COMMIT, interruption or detected identity/MAX regression cannot publish it.
Caller transactions remain refused; fresh installation and public schema snapshots
retain their behavior. Complete J/inventory/mapping/queued/source/data=False/FK/
integrity checks remain; bounded mapping/queued paths do not trust watermarks.
This assumes cooperative insert-only history, not arbitrary same-UID rewrites.

Central J validation, including validate_data=False, source-data validation,
startup/reopen, recovery pre-legacy discrimination and drain/downgrade all consume
complete immutable import closure; no ID exemption or compiler-pin-only admission
exists. Saved history is independent of today's permission. Ordinary template
listing includes the shared org-local imported release and exposes only actual
publisher provenance, never private capture bytes. Exact release store get and
low-level version readers validate the complete import and J before returning;
the public selected receipt is the dedicated GET/CLI above. Import cannot bind
an activation or document binding: unsupported code execution continues to refuse.
F–J SQL/independent references, create-org I, generic Database/runtime-audit,
K0 disabled readiness and unavailable K1 current resolution remain unchanged.
No startup/catalog import, mapping/P/run/attempt/event/receipt writer or live
operation is added. No schema, authentication, credential, shared transport,
permission, callback, hook or dependency mechanism changes follow.

Python units RETIRED; integration INCLUDING callback SKIPPED24342. SQL/parser/
behavior/security/negative/mutation/repetition/executable-QA proof is UNEXECUTED /
FOUNDER-WAIVED139429/430. Native assertions are retained source, not executed
coverage. Lint/static checks and inert OpenAPI inspection do not prove behavior.
Fresh FULL independent final-head review then same-head scoped SOURCE QA return
to SAME sole ROOT10693. No maker approval/QA/merge or complete K2b acceptance.

## Historical K2b first setup producer checkpoint — ROOT10693 / Founder139562

Manager step65 and THR139seq595/596 select **one K2b draft PR**, continuing
merged1082. Each writer ships its own complete saved/current/native/cold readers;
future run/stage readers are required with their corresponding writers. Earlier
K2a1/K2a2 sections below are historical checkpoint evidence, including their
then-current empty-only/no-producer and all-future-readers-first descriptions.
They do not impose another reader-only publication or change accepted encoding.

This checkpoint supplies Founder setup ISSUE/WITHDRAW, selected GET and exact
replay. POST `/api/v1/orgs/{slug}/workflows/coding/capabilities` accepts only
canonical UTF8 `workflow-coding-capability-setup-command@1` with exactly
`format,operation_key,setup_key,expected_authorization_revision,mode,scope`.
Issue scope fixes repository, actual published manager/team and configured store
owners, finite actions/path/argv maxima, source_rule and base_rule. Empty path
or command scope grants none. Withdrawal requires null scope and genuine prior
issue; its new immutable issuance is an inactive tombstone. No body identity,
agent session, bearer forwarding, query parameters or automatic retry is allowed.
Existing `_require_human` derives the Founder principal on the server.

CLI `happyranch workflows coding capability-set --org ORG --from-file ABS --json`
uses existing human transport and preserves the full original canonical command.
GET `/api/v1/orgs/{slug}/workflows/coding/capabilities/{setup_key}` returns only
the selected closed `workflow-coding-capability-setup-receipt@1`: original
IDs/revision/key/digest/replayed plus current active/inactive/revoked disposition.
Both endpoints are CLI-only/browser-excluded and native remote-denied; denial
checks raw and normalized candidates independently before forwarding. No shared
normalizer, credential or permission semantics change.

Setup records require compatible complete J. Every native admission's five
columns, original Q/issuance bytes, full snapshot/profile mirror/publication
journal, contiguous revision prefix, unique setup/operation/admission identities
and exact latest pointer are checked. One native admission transaction inserts
admission/revision and CAS-updates the pointer; failed validation rolls all back.
Discovery and independent whole-native schema reference construction precede
transactions; final admission uses captured data with current authority checks.
No file/object I/O or await occurs in the writer. Replay compares full original
bytes before fresh CAS, changes no pointer/revision, and reports original IDs
with current inactive/revoked state after withdrawal/replacement/policy loss.
Cold-valid history never establishes current authority or action permission.

Central J validation (also validate_data=False), reopen, recovery's pre-legacy
boundary, drain and downgrade recognize exactly complete setup closure. All
mapping/source/P/run/attempt/event/receipt coding families remain categorically
unsupported; malformed/duplicate/orphan/foreign values refuse. F–I/J DDL and
independent references stay unchanged; create-org remains I, startup never
upgrades, even empty J requires compatible code. Generic Database/runtime-audit
remain workflow-free. K0 stays disabled and K1 current resolution unavailable.

**K2b remains incomplete and DRAFT/unmerged.** SAME-PR mandatory remainder:
explicit fixed release import; mapping publication/local-store adoption/current
K1 resolution; source attenuation and genuine nonallocating task/SID/invocation/
Pause/ancestor capture producers, each with complete own current/native/cold
consumers. K2c owns role defaults/overrides/fresh J; first run writer is K3;
K4/K5 own revision/QA/CI/merge closure, K6 operator entry/cutover. No live setup,
grant/adoption, SQL fixture, deployment/restart/migration/assignment, provider/
browser/native build/ad hoc E2E or retired trial execution follows source work.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; SQL/parser/
security/negative/mutation/repetition/behavior/executable QA UNEXECUTED/
FOUNDER-WAIVED139429/430. Lint/static evidence supplies no behavioral PASS.
Fresh FULL independent review and same-final-head scoped SOURCE QA return to
SAME sole ROOT10693. No setup-only merge or complete K2b acceptance is claimed.


## Approved online team move — C1 task/host and C2 thread source (THR296seq135)

## Integrated online team-move F candidate — THR296seq135 / TASK11345

The integrated F candidate supplies founder-controlled service/CLI source with
`APPLY_AVAILABLE=True` and empty `NOT_INTEGRATED`. This candidate still needs
independent whole-feature review, same-final-head SOURCE/POLICY/STATIC QA and
manager guarded merge. Unmerged source is not accepted or activated capability.
The actual daemon service independently checks schema, native org/coordinator,
profile, host/tracker, naming and current authority before advertising apply.
Explicit recovery uses the SAME native owner gates while allowing a diagnostic
org/fenced publisher; `recover_available` and `recovery_incomplete_reasons` are
separate from apply readiness. Neither projection certifies quiescence. Unknowns
refuse with actionable reasons; no flag/private bypass/acceptance marker exists.
Preview and saved-state reads retain the existing bearer + `_require_human`
boundary, whose shared identity-less secret does not prove human presence.

F native release preparation runs through `OrgState.load`: strict read-only move
discovery precedes naming preflight, generic compatibility writes, roster
validation, publication and producer admission. After complete native workflow
validation, the DB-owned `install_team_move_schema` transaction validates exact
existing layout/history/org identity, refuses held/unknown state, installs only
the literal ONE table if absent, and validates before COMMIT. Repeated compatible
attachment is idempotent. Generic Database/runtime-audit, capability/preview/show
and restricted diagnostic recovery never install or repair it. Source delivery
does not call it on production: separately authorized compatible release loading
through the supported launcher performs native preparation; Founder assembles
no files, SQL or offline manifest. Missing schema/owners are engineering release
dependencies. Even an empty installed table requires reader@3. An absent optional
layout remains compatible with generic/older readers; source rollback cannot
undo durable state or grant a downgrade.

F also freezes C2 thread retry initialization explicitly, including EMPTY
budgets. New initial/fallback/nudge phase initialization is distinct from a
frozen zero budget; deferral/reentry cannot replenish it. Old missing facts keep
their original history interpretation but refuse ambiguous new retry/reentry
with `team_move_thread_retry_initialization_unknown`; no history is backfilled.
Updated shell/Mac launchers require `online-team-move@3` (reads old @1/@2), so
earlier supported parsers refuse before startup. Already-running old daemons,
obsolete launchers and unchecked old interpreters remain outside that guard.
All behavioral/installation/recovery/launcher observations remain UNEXECUTED.
Agents must not self-authorize with ambient credentials. Manage-agent retains
team/role and cannot transfer agents.

Historical task attribution is separate from current launch eligibility. The
complete parent/child/revisit/chain/fanout/workflow closure retains task team keys,
recorded participants and permanent IDs, including retired actors authenticated
by the existing strict naming owner or native INTEGER task/result/SID attribution.
It never resolves settled history through today's manager or requires re-enrollment.
Unknown identity/graph facts remain actionable census blockers without discarding
collected inventory; dependent sessions/jobs/recovery callbacks/workflows remain
joined even for terminal tasks. New execution still requires a current executable
owner. No historical record, team, callback or binding is rewritten.

The SAME INTEGER thread invocation/agent/SID preparation owner spans native
materialization, integrity, repository refresh and launch-spec/cache preparation,
including helper-internal omissions and caught failures. Such uncertainty appends
the existing sticky `failed_cleanup` fact BEFORE ordinary failure settlement;
backend preparation/cleanup and native workspace/cache failures retain ownership.
Later success/callback/terminal state cannot erase it. Serializer, audit COMMIT,
strict history reader, restart/reentry and census preserve that fact, independently
of frozen EMPTY/nonempty retries. Ordinary stale-claim/agent/breaker or missing
launch-seam refusals before effects retain their native no-effect disposition;
returned failure alone never proves compensation. No new schema/journal/backfill
or generic producer identity is introduced. All behavior remains UNEXECUTED.

Ordinary tasks carry their SAME native TaskInvocation/task/agent/runtime SID
through materialization, integrity (including retries), package omissions,
repository refresh, attachment writes/cleanup, task scratch and builtin/custom
cache/workspace setup. Safe deferred ownership becomes durably `prepared` before
preparation resumes; this never clears existing uncertainty.
Preparation failure sets the existing local reservation cleanup flag BEFORE the
fallible strict Pause save/COMMIT, then retains `execution_unknown`/`unknown` in
the admitted journal. Terminal/callback/quiescent later attempts and safe unrelated
worktree cleanup cannot clear it. Settlement persists that fact before release;
failed saves retain local ownership and original plus persistence errors.
Restart treats unclosed task `prepared` entries as unknown, including terminal
rows and prepared recovery/retry owners; absence of first launch is no proof of
preparation cleanup. Unknown deferred entries cannot authorize a fresh launch.
Native pre-effect source/artifact validation, repo-scan and missing launch-seam refusals
retain their no-effect disposition. Repository refresh also refuses before pull
on detached HEAD or missing/unusable/unreadable upstream; skipped repos stay false
and warn of stale source. Every executed pull failure/timeout/exception retains
uncertainty, including dirty/non-fast-forward and recursive partial effects;
equal HEAD/branch/porcelain summaries never prove no effects. Read-only upstream
lookup is admission only, not a remote-state lock. All observations/pulls share
one 30-second batch deadline; exhausted/unattempted siblings stay false. Thread/dream/
wake/Todo await off-loop refresh under the SAME native preparation-effects scope,
joining its worker before cancellation propagates and settlement runs. Timeout
never certifies process-tree quiescence; existing unknown owners remain sticky.
The four async runner entrypoints retain a private org lifetime through native
finish (including failure), beyond the refresh Future. Org unload refuses active
runners and fences detached instances against late starts. Runtime registration
(including the same root) and /runtime/use preflight ALL old orgs for tasks,
runners and team-move holds before any close or registration/activation effect.
Shutdown refuses runtime replacement before construction; idle replacement closes
and fences every retired instance before publishing new OrgStates. A busy later
org leaves earlier orgs open, with busy refusals mapped to409. Native task queue
work also holds the original OrgState/DB through preparation, run_step outer
settlement, heartbeat teardown and the SAME returned worker owner's retirement.
Every submitted future is tracked before a cancellable await; consumer cancellation
cannot retire an active run or redirect late work to a replacement with the same
slug. The retained drain_sync entrypoint uses this same ownership path.
Shutdown fences queue, runner and attachment admission before draining, preserves
host SHUTDOWN before producer cancellation and retains the existing async-runner
join. Queue stop owns its join across repeated waiter cancellation; its bounded
native drain timeout reports incomplete and leaves obligations and DB-close
barriers intact. Future failures/cancellation or failed retirement retain those
barriers, never certify idle or clean shutdown. Org close/unload and all-org
close/runtime reconstruction refuse native users before closing; unpublished
construction failure has no admitted native queue work. The signal handler never
closes DBs and reports nonzero unless the shared close path positively completed,
including when Uvicorn re-emits a signal. No timeout establishes quiescence;
failed persistence retains native uncertainty. External KILL/OOM or termination
past launcher grace remains crash uncertainty; this prevents new post-close
writes when retirement succeeds, not historical recovery or kill survival.
Existing retry/recovery IDs, frozen budgets,
INTEGER callbacks and authority-v2 are preserved. No new persisted field/journal, history
backfill, compensation or clearance writer is introduced. Behavior UNEXECUTED.

The serving lifespan owns one dormant private cleanup-diagnostic coordinator.
Only the real Uvicorn Server adapter supplies listener authority after successful
ASGI/listener startup with started true and no requested exit. The same original
recovery owner signals diagnostic intake once BOTH that observation and positive
recovery readiness exist; either arrival order converges without a new timer. Early port/PID files,
health probes, pre-yield scheduling, maintenance and ad hoc lifespans never start
acquisition. Signalling schedules work only; diagnostics never gate readiness.
Initial org count is checked before enumeration: empty/over-eight/missing context
is unavailable/incomplete. Tokens bind the actual serving state/runtime, canonical
org/DB paths, OrgState/Database/supervisor/tracker references and distinct local
attachment generations. Dynamic attachments and reconstructed runtimes remain
unsupported; no substitute registry or historical process proof is inferred.

One off-loop serial pass uses `Database` and strict `ThreadAdmissionStore` replay,
sharing a budget fixed at readiness: 3s,8 initial contexts,64 owners,1024 fetched
rows,4MiB,256 statements,5M VM steps, including closing comparisons. Native
lock/busy waits are at most25ms, batches target50ms, progress samples1000 VM
steps and record length is capped before allocation. Refusal/missing/malformed/
conflicting/moving evidence stays incomplete. These cooperative limits prove no
hard wall/RSS bound, global snapshot, process/effect closure or absence certificate.

A separate org diagnostic guard registers before executor submission and captures
THAT returned future before any cancellable await, including queued work. Only
positive native return with safe cursor/settings restoration and coordinator
settlement releases it. Cancellation, uncertain submission/return, failed
restoration and drain timeout retain the guard/DB. Detach/same-root reconstruction/
switch capture and revoke under orgs_lock, drain outside locks, then renew exact
map/runtime/org identity and all existing busy/hold preflights before any close or
registry/activation effect; races refuse409. Org removal joins only its own
diagnostic obligation; unrelated org diagnostics do not block it. Refused
operations never restart a revoked pass. OrgState.close revokes/refuses diagnostics independently of runners;
close_all independently revokes/joins/checks before DB closure. Lifespan revokes
before its first shutdown await and retains A's admission fence, host SHUTDOWN,
runner/queue joins and no-clean-success on incomplete drain. Direct/re-emitted
signals still close no DB; KILL/OOM remains crash uncertainty.

Publication rechecks the same serving objects and attachment generation; stale
results are discarded. Only selected-history agreement is reported. Labels are
process_attempt_unknown, effects_unknown, completion_unrecorded_or_unknown,
evidence_incomplete, context_unsupported, and (only for matching prior/final facts
with every recorded attempt settled/quiescent) late_producer_failure_after_recorded_attempt_quiescence.
producer_settled alone is insufficient. No affirmative whole-cleanup fact exists,
so final_bookkeeping_write_failure remains unreachable. Jobs remain uninterpreted
blockers; no release/recovery/mutation follows. Existing daemon logging emits one
summary and at most16 whole owner records, each at most2KiB UTF8 and32KiB total
message bytes including summary. Oversized whole records are suppressed and output
marked incomplete; identities are never shortened. Only bounded exact native IDs,
audit prefixes/journal generations/digests, status/reason/coverage and counters
are projected, without credentials/raw payloads/errors/model text/cleanup suffixes/
argv/environment/private paths/object IDs. Logging failure neither fails serving
nor retries diagnostics. Runtime/SQL/timing/cancellation/crash proof UNEXECUTED;
integration INCLUDING lifespan/SessionTracker/callback SKIPPED24342, units RETIRED.

C1a/C1b private thread-delivery release source (THR317 seq25/34, ROOT12084)
has a dormant service bound to the actual initial app/state/runtime/org-map,
org/DB/path, supervisor/tracker and attachment generation. Only actual successful
ASGI/listener startup AND positive recovery readiness admit it; either arrival
order converges once. Construction/readiness performs no operation or
selection. Native preview/show and the sole `release_exact_thread_delivery`
transaction have no ACTIVE HTTP/CLI, startup, reaper or automatic release caller.
Temporary, dynamic, custom, replaced and missing contexts refuse. Neither
ambient credentials nor caller proof flags enable mutation. Proof-backed release
is unavailable. The approved human risk override requires exact preview, stable
org-wide key, canonical command and explicit confirmation of late producer/session
writes, overlapping work, repeated external effects and missing owner/process/
effect closure. This source supplies no live-use, deployment or restart authority.

Startup and archived-thread resume cutover share a lazy, fully materialized
release view and common lifecycle-family decoding only within one transaction's
mutation-free segment. Unbuilt differs from proven-empty; every write invalidates
both views and selected conclusions. Initialized pairs still pass evidence and
permission checks before the no-op shortcut. Common sibling shape/size/duplicate/
org/thread/audit-owner checks remain complete; selected initial/append/native
agent/session histories replay on demand in caller order. Ordinary absent history
remains None, distinct from bounded release missing/orphan/1024-row refusals.
Permissions, live owners, holds, recovery, Pause, effects and raw census stay fresh.
Terminal status alone is unsafe: callback consumption can precede cleanup, and
settled=true/quiescent=false or sticky failed_cleanup retains unknown. Pending
preservation still uses the stricter retry/deferral/live-owner predicate; nonpending
history must be producer-settled and known before it stops preserving. Other
callers reread normally. This is full common decoding with lazy selected replay,
not unsettled-only pruning or lifecycle clearance. A legacy triggering_seq0 write
followed by another participant's native_delivery_range refusal rolls back the
whole cutover, without reusing the prewrite view or changing resume's prior commit.

The append-only version1 `thread_delivery_release` companion retains real thread
scope/permanent agent meanings and literal lifecycle-v1/unknown/retry history.
Complete bounded five-column and native readers bind source prefix, original and
optional genuine INTEGER replacement; invalid/foreign/orphan/duplicate/future or
incomplete evidence refuses. Only that obstruction's delivery interpretation can
change. Saved-prefix drift or late ORIGINAL lifecycle revokes current permission;
legitimate replacement history and the operation's own suffix preserve committed
history. Native recovery/runnable/reserve/claim, arrival/settlement, exchange
catch-up and separate probe republication share this interpretation. Raw host,
move/Pause/drain and diagnostic census remain blocking. A pending started REPLY
uses one interruption/slot-CAS/covering replacement commit without advancing ack,
resetting breaker/lease/held state or adopting old retries. Terminal history gets
no replacement;15402 gets no second catch-up. Unknown COMMIT requires strict
same-key readback, never remint/rekey. Old-reader/mixed-version operation and
downgrade after use are unsupported. Cooperative 3s/1024-row/4MiB/256-statement/
5M-step budgets include closing reads; exhaustion is unavailable, not absence or
a hard wall/RSS guarantee. C1-13 activation and C2 task-tail remain deferred;
12044/10693 remain unrecovered. All behavior/SQL/locks/races/cancellation/crash/
security proof is UNEXECUTED; integration INCLUDING callback is SKIPPED24342,
Python units RETIRED. Only lint/static evidence and independent full review plus
same-final-head scoped SOURCE QA can be attributed to this slice.

C1-13 dormant adapter source adds an UNMOUNTED four-handler router and an
UNCALLED `threads.register_release` with capability/preview/apply/show handlers,
private request custody and four client methods. Existing `threads.register`,
app mounts and serving owner stay unchanged: no supported installed release
tool, external_adapters_available=false and proof_backed_available=false.
Future request metadata derives from native strict DTOs. Original streamed UTF8
JSON is limited to16KiB before normalization; query/execution bindings refuse.
Bounded32KiB closed results/errors preserve committed=false/null/true and
no-store even on dependency errors; no synthesized202 or absence on failed read.
Preview/full-command digests are distinct. Explicit four-risk confirmation and
positive no-follow same-UID0600 file/0700 private-directory fsync/readback/close
custody precede exact-byte POST. Failures preserve bytes/key. Transport loss
allows at most one same-key GET and never another POST/rekey/poll; show never
publishes. Shared bearer/same UID cannot establish exclusive human presence.
Disconnect leaves native lifetime/outer retirement with the existing owner.
Fixture/native-denial/mount/registration activation remains HELD: the six shared
Web/Swift reader cases lack attributable RED/restoration/GREEN, with no waiver
or execution allocated. C1-13 public delivery and C2 remain pending; all adapter
runtime/security/custody/HTTP/CLI proof is UNEXECUTED, never test-authoring PASS.

C1b registers each operation and org native lifetime before submission, captures
every returned future before a cancellable await and owns outer retirement after
disconnect. Positive native return, rollback/settings restoration and outer
settlement alone discharge that guard. Revoke closes loop intake first and takes
only its C leaf for at most25ms; timeout is incomplete and bars host shutdown,
detach and DB-close fallthrough. Remove/all-close/runtime switch/direct org close
independently require positive C idle and renewed attachment/busy/hold checks.
Final order is move -> DB -> controller -> residue -> tracker -> C leaf -> the
existing COMMIT. All SQL inventory/decoding finishes before owner guards; only
COMMIT occurs inside, with reverse release before rollback/readback/notification.
The original3s budget starts before submission, includes both observation passes
and native+SQL work within5M steps. Under actual owner locks, built-in maps over64
refuse before iteration; each pass reserves n+1 rows,2048*(n+1) raw bytes and4096
native units per record/completion. Any residue/unhealthy measurement, relevant
retained ownership/binding/preparation or outstanding native writer blocks.
Org-level unresolved move/Pause controls conservatively refuse; no task root is
inferred from thread text or tracker absence. Fresh guarded CURRENT facts and
existing admission/release counters detect observed changes, without certifying
invisible historical tracker/residue ABA or old process/effect retirement. After
commit, independent current eligibility permits only SAME-token put_once through
the existing queue; notification loss never rolls back, remints or reapplies.


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

Follow [the online runbook](../runbooks/online-team-move.md) for the engineering-owned
F acceptance/enablement, separately authorized release loading and exact live
Founder operation/readback handoff. Source delivery is not activation or behavior.
SAME13 four answers remain; all absent HTTP/CLI/migration/recovery/race/cleanup/
startup/launcher/downgrade/callback/live observations UNEXECUTED; integration
INCLUDING callback SKIPPED24342, Python units RETIRED. No new cases or campaign.


## Private D operation source — THR296seq135 / TASK11345

D now supplies the private native operation core in `team_move_operation.py`,
with pure canonical/context planning, one two-consumer profile interval and one
operation-owned publication fence. E now mounts the founder service/CLI under
the existing admitted human boundary. F proposes source enablement subject to
actual native readiness; independent whole-feature acceptance remains required.
A provenance model, agent bearer or caller success flag grants no authority.
Earlier B/C/D checkpoint absence statements describe those historical units.

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
launchers pass `--reader-guarded-start=online-team-move@3`: supported older argparse readers reject
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
Public recovery/reattachment is wired by E; F gates it on actual native owners.

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

## Historical C1–C3 source checkpoints (THR296seq135; superseded by F above)

The optional team_move_operations table is explicit org-owned storage, never
generic Database/runtime-audit bootstrap or startup installation in B. Exact
owned layout/all-record validation and complete independent release references
include its presence without modifying naming/pause/workflow/v2 history.
Workflow release validation and legacy authority evidence retain complete
independent whole-release references with validated optional presence. Naming
installation uses N-SAC-1 structural admission inside its existing transaction:
selected/actual DB association, task/audit dependency shapes, naming interaction
closure and unchanged complete workflow/Pause/move/naming validators. It builds
no whole-release reference. Non-install reconciliation retains its prior behavior. Absence remains supported;
malformed presence refuses without installation, stripping or history repair.
C1 binds the coordinator at Orchestrator construction and reads durable holds
in actual allocation/claim transactions without installing schema. Absent layout
keeps normal operation; malformed/unknown installed state refuses. Preparation
ownership spans callbacks and cleanup; unknown tree/cleanup state stays busy.
C1 task redelivery supplies an owner seam only; startup/whole-operation release
and runtime/launcher downgrade requirements remain DATA ONLY until D. D
must discover/recover before partial-roster validation/publication/admission and
refuse incompatible readers. An additive table cannot teach prefeature binaries
a hold. No deployment/restart/live DB operation follows source acceptance.
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

C2 now reserves trusted thread/participant/INTEGER invocation/SID ownership before
preparation, then composes final claims and launch with the short gate, DB and
host terminal ordering. All purposes and actual host/direct429, eviction and
bounded-nudge paths participate. The existing thread audit owner stores strict
bounded append-only attempt/deferral evidence; no schema or task-Pause format
changes. Unknown prior retries stay blockers after later clean outcomes.
Captured actual running turns alone may settle under WAITING; no drain label
admits new work. Queue dedup spans queued/in-flight work, typed deferrals release
notification ownership, and complete durable runnable discovery/redelivery keeps
pending tokens/ranges/probe/catchup obligations intact. Native thread recovery
and raw reapers preserve held/C2-owned history. A pending receipt whose
producer settled without deferral or unknown cleanup and has no live owner
takes ordinary daemon_restart recovery; a shutdown cancellation after every
attempt settled quiescent records no failed_cleanup. Participant/session writes
cooperate during future forward reset; messages remain admissible. The bounded
thread census/drain and post-release seams are available to D, whose complete
startup/containment/operation/old-reader closure remains absent. No public apply
or COMPLETE writer; APPLY_AVAILABLE stays false. Behavior remains UNEXECUTED,
integration SKIPPED24342; no new product tests/fixtures or activation authority.
Native archive admission precedes delivery discard, BOOTSTRAP reaping, status,
participant resets, their audits and the archive system message in one DB-owned
transaction serialized against a forward decision. Refusal leaves all those
obligations and history unchanged; transcript files/events follow COMMIT.
Authentic provider eviction audit and compare-and-clear of the attempted stale
SID commit before fallback phase selection/admission. Only the actual live
producer's durable launched/settled initial attempt can record that observation
under a winning hold; callback/drain/new-launch and general participant guards
remain unchanged. Watermarks, delivery ranges/tokens and runtime SID are retained;
unknown cleanup remains a blocker. Deferred re-entry cannot reuse the evicted SID.
These archive/eviction/re-entry behavioral observations remain UNEXECUTED.

C3 now registers dream/wake/schedule/job native source, permanent org/agent and
owning session before preparation, rechecks actual claims and final host/direct
launch, and retains original retry budgets/SID/enqueue age on safe deferral.
Pending work stays native; recurring schedule occurrences retain distinct
history. Existing queue/timer release seams deduplicate redelivery; reapers
preserve held or uncertain owners. Complete C3 causal census covers job waits,
active bindings, remote attempts/workspaces, pending enrollment and writer tails;
terminal rows or missing trackers never prove cleanup. Native profile/workflow,
roster/name/settings/skill/policy/cleanup writers reserve affected IDs and
source/target membership, keeping their original synchronization and journals.
Strict bounded version1 producer/writer audits append in existing audit_log;
earlier uncertainty survives later clean outcomes. No extra table, permission
or auth change, remote engine, move/reset/publication service or COMPLETE writer.
D composes the canonical capture/coherent publication reader and complete early
startup/recovery/old-reader guard; D/E/F remain outstanding. APPLY_AVAILABLE=False.
All behavior/migration/recovery remains UNEXECUTED; integration SKIPPED24342,
Python units RETIRED, no new cases/fixtures or activation/live-operation authority.

C3 schedule preparation/spec/execution uses the SAME pinned runtime SID;
provider resume identity remains separate. Native caught materialization,
integrity/package/repository failures retain sticky failed_cleanup after source
terminalization; normal return is not cleanup proof. Agent custom-skill identity
admission precedes reservations. Authority/profile sync/async intervals expose
the same native disposition to explicit pre-effect validators, including stale
rename/prompt revisions and roster/profile fallback paths. Naming refresh must
complete its finite native transaction; caught reconciliation/publication/clone
failures retain uncertainty even on a normal return. An unfenced interval or an
HTTP status never certifies absence of effects. Exact validation exceptions may
settle only after native rollback and terminal reconciliation finish. Scratch
cleanup uses no-follow lstat: only ENOENT establishes literal absence; surviving
entries (including dangling symlinks), inspection errors and prior uncertainty
remain blockers, with actual cleanup causes retained in logs/exception notes.
Cancellation, partial files/packages, failed rollback or uncertain compensation
stay unknown. Existing unknown history is immutable; there is no clear writer.
Retry initialization explicitly freezes EMPTY as well as nonempty schedules;
missing legacy facts preserve history but cannot grant new retries or automatic
ambiguous reentry. These are source corrections only; behavior UNEXECUTED,
integration INCLUDING callback SKIPPED24342, APPLY_AVAILABLE=false and D/E/F remain.

See the [approved contract](../superpowers/specs/2026-10-14-online-team-move.md).
Behavior/recovery remains UNEXECUTED; source/static acceptance is separate.


## Current naming metadata prompt wiring

TASK10385 adds a read-only OrgState→Orchestrator naming reader, using the existing
validated org metadata adapter. Every relevant prompt construction captures
current names once for its labels; filesystem namespace capture precedes the
short DB coherent-read section. It does not hold DB locks across file scans or
load files/DB per rendered name. Task role and outer wrapper are separate reads.

Full/delta thread prompts, eviction and non-resumable full fallback, resumed
no-callback nudge, and dream/wake/schedule production builders refresh naming
context. No provider resume ID, launch/admission/executor/scheduler algorithm,
callback identity/order, schema or config identity changes. Unavailable metadata
renders ID-only context, with no reconciliation/write/launch gate or snapshots.
CLI read filters retain historical IDs and naming-unavailable ID access; server
owners keep original checks. Installed skills are unchanged; only release-owned
bundled wording is updated for the current-name versus automation-ID boundary.

The combined candidate includes UI/type consumers. Its maker handoff records
source-pinned frontend, disposable E2E, browser and schema receipts. Published
source still requires final independent review/QA/checks and guarded merge.
No live runtime/provider/config/restart or deployment is authorized by the
frontend verification leg. See the [current naming contract](../superpowers/specs/2026-10-09-identity-names-core.md).

## Naming v1 core, API and routing source

`GET /api/v1/orgs/{slug}/identities` and `happyranch identities list --json`
expose additive `naming_diagnostic`: null for ready rows, otherwise a finite
captured naming category. Unknown, malformed or missing captured codes fall back
to `naming_database_unavailable` when any row is unavailable, including a fresh
metadata-read failure with a stale null diagnostic. The response never exposes
raw exception text, paths or SQL. Older servers may omit the field: absence is
UNKNOWN, never readiness. Canonical roster, definition revisions and existing
read/auth errors retain their behavior; GET never reconciles or installs names.
This category is an observation, not proof of a serving source SHA or root cause.
Source publication does not establish restored naming or authorize live recovery.

A failed actual install attempt retains one bounded category in memory. Later
non-install refreshes that only observe absent naming report that last attempt's
cause; without that provenance they report `naming_not_installed`, never a guessed
stale cause. A different current failure takes precedence while observed. A new
install attempt supersedes the retained cause; any successful refresh clears it.
This diagnostic retention changes no readiness, admission or installation decision
and adds no retry, persistent history or read-time writer.

Schema2 multi-org attachment installs naming only after baseline admission; no
flat-schema1 conversion or history-only ID mining is added. Naming failure is
volatile readiness with an actionable category. Original strict team/attachment
and complete workflow validators remain; global workflow/legacy reference
guards are unchanged. Installer-only admission is naming structural compatibility,
never a global authority grant. Every naming-installed F/E/G needs
a compatible naming reader; existing explicit workflow commands preserve exact
naming row identities/revisions and reject malformed naming without repair.
One daemon controls each organization; no naming cross-process lease is added.

Bounded API reads need no new readiness gate for canonical IDs. Failed naming
metadata returns explicit null addressable_name/name_revision and unavailable
status while original ID roster/lifecycle reads remain. Operator rename refreshes
and validates through the accepted A service and refuses naming-only failure
safely. All four routes use existing OrgDep and org-local owners; no foreign-org
label lookup or alias-based config/session/workspace binding is introduced.
No live migration, enablement, config change or restart is authorized by source.


Current routing source resolves names/IDs case-insensitively with the unchanged
@token grammar. Former inputs refuse409 with current_name before thread creation,
stale closure, callback settlement or application attachment finalization. A
lookup grants no reservation; action boundaries revalidate. Actors/proofs remain
ID-only. Current-founder-only messages suppress new broadcast/cohort wakes, retain
full-recipient obligations and enqueue prior catch-up separately. Founder remains
human inbox only. Restart uses durable IDs/ranges/tokens/exchange/deferral rows;
no historical body reclassification or schema addition occurs. Filesystem capture
precedes the RLock and multipart reads precede final no-await admission/write.
Unavailable naming keeps actual ID behavior; non-ID addresses refuse503.

THR293seq34/35 requires twelve readable E2E scenarios. Core/API/routing,
CLI/prompt and UI/type source are present in the combined candidate. The existing
routing bodies and CLI assertions remain reusable E2E material; shipping-page
MSW assertions cover frontend portions of scenarios2/3/4/5/8/10, without daemon/DB
proof. Exact execution results and remaining obligations live in the maker
handoff. Python units/collection/duration are RETIRED under THR-291 seq40 and
general integration is SKIPPED THR243seq42, never PASS. Focused naming E2E requires
a supported disposable GitHub runner or Mac Linux guest with finite commands,
resources, output, deadline and actual cleanup. Independent full review,
behavioral QA, applicable checks, normal publication and guarded merge remain.


[Current contract](../superpowers/specs/2026-10-09-identity-names-core.md).


**Mounted generic owner review — THR139465/480/493/506, ROOT TASK10693.**
The normal `/orgs/:slug/reviews` list and `/orgs/:slug/reviews/:activationId`
detail use owner-selected discovery and the existing recipient-safe reads.
Template pins supply output/role/outcome labels; there are no template-name
branches. Exact selected identity, canonical base64, strict UTF8 and SHA256 are
validated before rendering raw text. Current r1/r2 and read-only History remain
separate; an individual recorded response does not establish joined approval.
Only current collection validity with approved_use establishes approved input.
Explicit Request changes or Sign off requires exact nonempty operator findings
and a matching @1/@2/@3 command. A synchronous latch precedes hashing; one
closed key/body is frozen in sessionStorage before POST. Ambiguous sends lock
editing/opposite effects and offer only explicit same-command replay after a
fresh selected read, including reload. No effect/poll/locale change auto-sends.
Current read/refetch errors gate effects; access refusal hides selected bytes,
drafts and receipts and evicts sensitive selected cache/envelopes. Query identity
includes org/activation/lane/object; outgoing reads abort, panes detach, and late
hash/mutation results cannot affect a new selection. Locale changes preserve
mounted drafts/focus/selection without network work.
Return to author is a separate confirmation following full server readiness;
POST independently rechecks the original r1 CAS and authority. Genuine successor
and new author task IDs/status come from the server, with no optimistic r2 bytes
or receipt. Fresh r2 responses are required; further revision is unsupported.
Frozen original owner-return replay uses current access, identity and authority
admission separately from new-effect readiness. A pending genuine successor's
held/noncurrent collection permits only explicit identical acknowledgement
recovery; it never permits a new command. Current graph/status comes from the
selected successor independently of retained r1. Pending author completion does
not mean r2 reviews closed; unsupported further revision appears only for a
current objected r2 graph. Selected-read start/refusal or snapshot change
invalidates the admission token synchronously; the writer rechecks that token
and selected identity immediately after asynchronous command validation.
No raw proof editor, peer cancellation, author staging, direct submission,
r3/reassignment, execution/merge or digest-derived access controls are mounted.
This frontend source requires the compatible discovery/revision readers; old
daemon errors remain visible dependencies. No backend/native/schema/auth/shared
transport/dependency/hook changes follow. Bounded intercepted frontend rendering
and assertions establish UI behavior only; independent final-head review/scoped
QA and the assigned public synthetic two-template journey remain separate gates.
Real Founder Request changes AND Sign off and genuine agent judgment remain
UNOBSERVED/root-owned. Python units RETIRED; integration/callback SKIPPED24342;
broad/protected proof UNEXECUTED/FOUNDER-WAIVED139429/430. Required automated
CI/hooks remain Source Boundary Lint + Web Lint(Node24), static only.


**H handoff storage — U3a(a), THR139 seq445/465.** Proven-fresh org creation
initializes H (complete G plus the exact `workflow_author_handoffs` table: 17
columns, six automatic constraint indexes, no marker or explicit index).
Existing F/E/G startup, reopen and enable validate their installed layout and
never add H. F/E must use the separately approved G migration first. The
explicit offline org-only command is
`python scripts/migrate_workflow_author_handoff_schema.py --org-root <absolute-org-root> --org-slug <slug> [--check]`.
For this H command, `--check` returns 1 for valid G requiring migration, 0 for
validated H; ordinary migration/repeat returns 0, refusal 1, parser error 2.
One short writer rechecks source ownership and complete independent references,
adds only the approved table, validates the target and retained rows, then
commits; failures roll back. Operator stop/ownership cooperation is required;
the script stops no process and cannot exclude an arbitrary concurrent start.
Even empty H requires an H-compatible reader. F/E/G SQL, IDs, history, permanent
NULL active legacy result and genuine INTEGER result links retain their meaning.
Whole-database F/E/G/H references preserve the independent Pause/Resume dimension;
generic Database/runtime-audit remain workflow-free and authority-v2 observed-only.

Complete H validation authenticates historical profile readiness from the saved
full dependency/store/registry mirror. Every closed variant preserves raw
nonempty source profile names; namespace and consumer identities are nonblank
UTF8 strings of 1..256 bytes without NUL, generations are exact integers in
0..9223372036854775807, and digests are full lowercase SHA256 strings. Native
row grouping/order and unrelated or removed rows remain intact. The historical
org's active requirements must have matching active store and registry
generations, and their full names, digests and ordered consumer membership must
reconstruct the pinned canonical profile projection. Empty mirror with required
profiles, missing/mismatched required rows or unbound requirements refuse
`workflow_author_handoff_storage_corrupt` before graph creation; an empty
required projection remains valid. Readiness, the offline script and producers
share this reader. Admitted GET exposes only category500; invisible scope404 and
selected-grant403 retain earlier precedence. Today's mutable mirror is never
historical evidence; a genuine later authority change remains a current hold.

New H structured witnesses compare canonical JSON bytes against authentic
retained or derived projections, including the row, immutable source intent,
activation/template and byte pins, context, snapshot, saved pointer and profile
closure. Boolean true/false cannot substitute for integer 1/0 in any such
witness. Exact integers and legitimate embedded snapshot booleans retain their
types; historical event numeric semantics remain unchanged. A mismatch refuses
`workflow_author_handoff_storage_corrupt` before graph effects, with the same
selected scope/grant precedence.

New F5 review-dispatch events also preserve types across every before/after
projection, immutable transition, adjacent event and final retained row. Each
intermediate projection anchors immutable fields to native rows and validates
mutable field types; the initial projection derives the producer's queued
values. Complete callback records and reused results retain native task_results
types, acceptance is exact integer 1, and host quiescence is genuine Boolean
true. Boolean/integer substitutions refuse before owned effects, using
`workflow_review_storage_corrupt` internally and the existing admitted H
category500 mapping. Authentic flags and historical S2 numeric semantics remain.

U3a(b) adds matched immutable `@3` template/activation/context readers,
`submissions.validate_handoff_graph` and the internal handoff/review producers.
Historical `@1`/`@2` families and pins retain their interpretation. An original
CURRENT author with stage+submit authority reserves immutable bytes only. SAME
operation fulfillment requires its genuine INTEGER/full callback and committed
successful/quiescent terminal proof, plus current authority and independence.
The atomic graph retains NULL legacy result, appends the authentic result link,
and creates one unsigned request per configured slot. Actual agent tasks alone
populate the ordered 0..3 reviewer-task IDs; roles, kinds, outcomes and timing
come from immutable template data. Inputs remain filtered to each recipient.

The review owner reuses the existing scheduler/native host lifecycle and
Pause/Resume final admission, publication, prompt reader and cancellation fences.
Completed-source discovery precedes workers and periodic enqueue hints; possible
launch without affirmative proof remains uncertain. No callback is a signature.
U3a(c) mounts the closed handoff POST/GET and unsigned selected-request GET,
with CLI/TypeScript/OpenAPI/native parity and existing F5 callback, replay,
cancellation and portability owner selection. This complete U3a composition remains
unaccepted pending renewed independent same-head assessment and current static gates.
Direct-submit and reviewer-stage public/deny deferral remains in force. Genuine
receipts/joins, revisions/reassignment/recovery, mounted operator UI and operator
acceptance remain later units. Full independent review, honest same-head QA,
unconditional exact-head Source Boundary Lint and Web Lint (Node 24), and
manager guarded merge remain required under THR291 seq69. Actual CLI callback
sources and isolation fences remain retained; execution is not a merge check.
Manager-owned bounded two-template public smoke must precede Founder invitation.
No live migration/enable/assignment/deploy/restart follows.

**G submission-schema migration (THR139 seq395; retained F/E→G path).**
The earlier fresh initializer selected G; U3a(a) now selects H under the H contract.
Existing F/E startup,
reopen and enable retain their installed layout; S2 remains available on E.
The explicit org-only operator command is
`python scripts/migrate_workflow_submission_schema.py --runtime-root <absolute-root> --org <slug> [--check]`.
Check returns migration-needed (3) for valid F/E, ready (0) for complete G,
refused (1) for invalid source/ownership, and parser errors return 2. Actual
migration atomically replaces only submissions/events and adds the approved
three tables/two explicit indexes. Event revisions come from an unambiguous
retained submission/round/event/replay closure; ambiguity refuses without
rewriting history. Every G database, including an empty one, needs a compatible
reader. The original F/E definitions and pristine-F downgrade contract remain.
The older draft script still upgrades F to E and reports a validated G as a
no-write G replay. Legacy authority comparison uses independent complete
F/E/G whole-database references; authority-v2 remains observed-only.

An authorized upgrade requires the daemon to be stopped with its configured
home/registration observable and source owners/hosts reconciled. The command
reads bounded existing PID/port/registry evidence and reserves one SQLite
writer; it stops no process and provides no exclusion against an arbitrary
concurrent daemon start. Operator cooperation is a precondition. Active-origin
submissions retain NULL legacy result identity; separate authenticated operation
and INTEGER ordinary-result links preserve the actual result evidence.
U3a(b) internal submission/review/link producers are staged in the stacked draft.
Genuine receipts/joins/finalization, later U3-U6 units and
independent operator acceptance, including real Founder UI actions, remain pending. This implementation work does not authorize live migration,
enablement, deployment or a Phase1-completion claim.


## Founder-managed Default (THR296)


C10's browser-owned pre-attach fixture adds Product/product_head alongside the
unchanged Engineering/engineering_head and Content/content_manager controls.
HTTP availability is modeled at its owned proxy; normal actions settle through
the real isolated daemon. Empty Default is a coherent transport projection,
not a migration receipt. Existing startup and operator-maintenance gates stay
unchanged; no browser invocation on the development host is authorized.

Offline roster check/apply pins the real detached registered RuntimeDir and
resolved candidate/interpreter bytes. Both closed org and canonical-store
restore inventories are independently checked before apply; final pre-reset
CAS includes canonical/generated metadata and authority/profile controls.
Persistent launcher inhibition is externally owned and remains required across
process loss/reboot. The current adapter cannot certify ordinary unmasked
command-bearing units; see the bounded migration runbook.

`teams.yaml` preserves default_team and task_default_team. Existing missing
task pointers retain Engineering routing. Proven fresh org creation writes an
empty Founder-managed Default and explicitly sets both pointers to Default.
Attachment validates duplicate memberships and both registry→definition and
definition→registry team/role agreement; pending enrollment keeps its supported
semantics. Exactly matching pending bootstrap managers may attach, while missing,
wrong-team/role and duplicate active/pending identities refuse. Pending managers
remain non-executable and cannot make workflow authority ready; ordinary approval
must promote them and publish the coherent active roster. Startup performs no roster move. Tagged managers have exactly kind
and principal; human principal is founder and has no executable-agent name.
New workflow authority snapshots use schema2 typed principals and pointers.
Historical schema1 snapshots/bindings retain their original bytes and digests;
version-aware readers exclude human principals from executable membership.

## Settings

Bundled skill sources resolve under the selected package root at
`runtime/skills/bundled/`, including installed wheels. `Settings.get_bundled_skills_dir`
uses the single resolver in `runtime/skills/sources.py`; missing sources never
fall back to another checkout. The retired `protocol_dir` setting remains only
as an inert default-valued compatibility field on the existing settings response.
Non-default YAML/environment overrides are rejected with migration guidance:
publish approved assets in the new release location, then remove the override.
There is no automatic source migration or canonical byte repair. Remove this
compatibility field only in a separately versioned settings-contract change
after deployed override inventory and client migration are complete.

Operational settings are represented by `Settings` in `runtime/config.py`.

Resolution order:

1. `HAPPYRANCH_`-prefixed environment variables.
2. `<daemon-home>/config.yaml`, defaulting to `~/.happyranch/config.yaml`; keys are field names without the prefix.
3. Code defaults.

There is no `.env` support. `settings_customise_sources` drops dotenv and adds `YamlConfigSettingsSource`. The daemon home resolver is inlined in `config.py` as `_daemon_home` to keep `config` free of a daemon dependency. Do not confuse daemon-level `config.yaml` with each org's `<runtime>/orgs/<slug>/org/config.yaml`.

The authenticated `GET|PUT /api/v1/orgs/{slug}/settings/daemon-capacity`
resource is daemon-wide despite its Settings navigation context. It exposes and
atomically stages exactly the paired `queue_workers` and
`host_global_session_cap` values using an opaque revision under a process lock.
PUT requires exactly one quoted strong `If-Match` revision header, both exact
integers, a rationale, and environment-shadow
confirmation when applicable. It preserves unrelated YAML, writes through a
same-directory fsynced temporary file and atomic replace, and records the fixed
honest actor `daemon-bearer-holder` in the existing `config:daemon_capacity`
audit scope. One correlation identity links the durable pre-replace
`daemon_capacity_config_write_authorized` row to exactly one honest terminal
row: `daemon_capacity_config_succeeded`, `daemon_capacity_config_rejected`,
`daemon_capacity_config_failed`, or
`daemon_capacity_config_publication_uncertain`. Each row contains only the
server-observed allow-listed prior/requested pair, known revisions, rationale,
and safe provenance. Audit failure before replace leaves the original YAML
untouched, and replace failure cannot produce an unaudited authoritative file.
Once atomic replace succeeds, any directory-durability, read-back validation,
response-snapshot, or temporary-cleanup failure returns the distinct
`config_publication_uncertain` outcome: the new bytes are authoritative, so
operators must reload and inspect them before retrying. The response reports
temporary-artifact state as `absent`, `present`, or `unknown`; cleanup failure
never fabricates absence or overrides publication. The writer never performs a
second unaudited replacement as compensation.

**Observed divergence from the "exactly one honest terminal row" description
above — described, not resolved here.** When the terminal audit insert itself
fails after a successful atomic replace, the shipped behaviour records the
durable `daemon_capacity_config_write_authorized` row and *no* terminal row at
all, returning `config_publication_uncertain`
(`tests/daemon/test_routes_settings.py::test_daemon_capacity_terminal_success_audit_failure_is_publication_uncertain`
asserts the audit rows are exactly `["daemon_capacity_config_write_authorized"]`).
Terminal audit completion is therefore **not guaranteed**, and nothing may
assert that a rationale "is recorded in the audit entry". This note records the
divergence between the description and the tested behaviour; it does not change
backend auditing and does not assert that the two agree. The browser capacity
panel's copy is qualified accordingly: it states that the reason is *included
in the save request* and that auditing is addressed org-locally and
bearer-attributed with terminal completion not guaranteed.
The shared daemon bearer is required; it proves possession only and cannot be
attributed to a verified person. Save is next-restart-only and cannot resize
the startup worker or HostSessionSupervisor snapshots.
The read model reports the startup supervisor's runtime-derived producer
envelope/components and capability-derived effective admission cap/reason.
A host cap below the envelope remains valid and warns about intentional
backpressure; a cap above it warns that unused admission capacity creates no
additional producers.

**Frontend numeric limitation (NOT a contract change).** The browser editor
refuses operator input outside `Number.MAX_SAFE_INTEGER` and withholds any
consumed server numeric that did not survive `JSON.parse` as a safe integer.
The API contract remains an unbounded positive integer; the browser bound is an
editor representation limit only. A residual blind spot is retained and
explicitly not closed: the shared HTTP client parses the response body and
discards the raw text, so a raw *fractional* token that `JSON.parse` rounds
into a safe integer (for example `9007199254740990.5` -> `9007199254740990`, or
`1.0000000000000001` -> `1`) passes the guard undetected. Closing that read
site needs a raw-text/BigInt-aware parse in the shared transport or a new API
representation, and is a separate decision.

| Variable | Default | Description |
| --- | --- | --- |
| `HAPPYRANCH_CLAUDE_CLI_PATH` | `claude` | Default command metadata for claude (config/docs only — executor launch requires ``executors.json`` pin) |
| `HAPPYRANCH_CODEX_CLI_PATH` | `codex` | Default command metadata for codex (config/docs only — executor launch requires ``executors.json`` pin) |
| `HAPPYRANCH_OPENCODE_CLI_PATH` | `opencode` | Default command metadata for opencode (config/docs only — executor launch requires ``executors.json`` pin) |
| `HAPPYRANCH_PI_CLI_PATH` | `pi` | Default command metadata for pi (config/docs only — executor launch requires ``executors.json`` pin) |
| `HAPPYRANCH_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `HAPPYRANCH_PROTOCOL_DIR` | `protocol` | Retired compatibility value; non-default overrides refuse startup |
| `HAPPYRANCH_MAX_ORCHESTRATION_STEPS` | `50` | Legacy accepted setting; inert (not an execution limit) |
| `HAPPYRANCH_QUEUE_WORKERS` | `6` | Daemon-wide `run_step` worker slots; must be greater than 0; restart required |
| `HAPPYRANCH_HOST_GLOBAL_SESSION_CAP` | `13` | Healthy enforcement-capable daemon-wide host-session admission cap; capability fallbacks remain conservative; restart required |
| `HAPPYRANCH_SESSION_TIMEOUT_SECONDS` | `1800` | Global agent-session timeout default |
| `HAPPYRANCH_EXECUTOR_CEILING_DEFAULT` | `8` | Per-provider concurrent-launch ceiling (issue #85); must be greater than 0 |
| `HAPPYRANCH_EXECUTOR_LAUNCH_SPACING_SECONDS` | `1.5` | Minimum interval between same-provider launches; `0` disables spacing |
| `HAPPYRANCH_ORG_SLUG` | unset | Default org slug for per-org CLI commands |

`executor_ceiling_overrides` (a `dict[str,int]`, e.g. `{"codex": 12}`) and `executor_rate_limit_backoff_seconds` (a `list[int]`, default `[5, 15, 45]`) are list/dict-shaped, so they are set via `config.yaml` rather than a scalar env var. See [Executor Throttle](#executor-throttle).

Slug resolution for per-org commands: explicit `--org <slug>` > `HAPPYRANCH_ORG_SLUG` > auto-infer only when exactly one org exists > error. Container-level commands such as `happyranch init`, `happyranch use`, and `happyranch orgs ...` take no `--org`.

## Executor Throttle

A process-wide, **per-provider** throttle (`runtime/orchestrator/throttle.py`, issue #85) gates every agent-subprocess launch at the single chokepoint `executors._run_command`, which both the task `run_step` pool and the thread-reply pool reach on an OS thread. It caps concurrency, de-bursts launches, and absorbs transient 429s — without resizing either pool (they stay as producers; the semaphore is the consumer-side cap). Decision record: [`docs/adr/0001-per-provider-executor-throttle.md`](../adr/0001-per-provider-executor-throttle.md).

Keyed by provider string (`claude | codex | opencode | pi | ...`), so saturating one provider never blocks another:

| Setting | Default | Meaning |
| --- | --- | --- |
| `executor_ceiling_default` | `8` | Per-provider `BoundedSemaphore` size; max concurrent subprocesses for one provider across both pools. Must be > 0. |
| `executor_ceiling_overrides` | `{}` | Per-provider ceiling override (config.yaml), e.g. `{"codex": 12}`. |
| `executor_launch_spacing_seconds` | `1.5` | Minimum interval between same-provider launches. `0` disables. Cross-provider launches are never spaced against each other. |
| `executor_rate_limit_backoff_seconds` | `[5, 15, 45]` | On a rate limit in a **failed** launch (the retry is gated on `rate_limited and not success`, so a successful session is never relaunched) the launch releases its slot, sleeps `backoff[attempt]`, re-acquires, and retries. After the schedule is exhausted the task is marked terminal FAILED under normal failure handling; no daemon successor is spawned. `[]` disables retries. |

Rate-limit detection is normalized: `_run_command` sets `ExecutorResult.rate_limited` from `is_rate_limit_signature(...)` and the classifier prefers that field over its legacy string heuristic. Two additive audit actions surface the activity through the existing `insert_audit_log` (no schema change): `executor_slot_wait` (`{provider, wait_seconds, ceiling}`) when a launch waited for a slot, and `executor_rate_limit_backoff` (`{provider, attempt, backoff_seconds}`) per 429 retry.

The configured schedule remains the default for ordinary invocations. The
bounded THR-247 Codex completion-recovery path alone opts out per invocation
at the host-supervisor seam: its first rate-limited provider result is
finalized and receipted honestly without backoff or re-admission. This does
not alter global throttle settings, ordinary invocation retries, admission, or
rate-limit diagnostics; the honest-passthrough fallback likewise uses its
existing empty executor-throttle backoff for that one provider execution.

The list/dict-shaped keys (`executor_ceiling_overrides`, `executor_rate_limit_backoff_seconds`) are set via `config.yaml`; the scalar keys also accept `HAPPYRANCH_`-prefixed env vars.

## Metrics Persistence (THR-066)

The daemon persists runtime metrics as a time-series of full snapshots in a
**daemon-global** SQLite store at `<runtime_root>/metrics.db` — a sibling of
`orgs/`. This is NOT a per-org store; the metrics aggregate spans all orgs
(uptime, loop ticks, HTTP latency histograms, task/job/session/queue counts).

| Property | Value |
| --- | --- |
| Store file | `<runtime_root>/metrics.db` |
| Table | `metrics_snapshots (id INTEGER PK, captured_at TEXT NOT NULL, snapshot_json TEXT NOT NULL)` |
| Index | `idx_metrics_snapshots_captured ON metrics_snapshots(captured_at)` |
| Cadence | ~60s (piggybacks `work_hours_scheduler_loop`; throttled to one write per ~55s) |
| Retention | 30 days (pruned on each write; module constant `_RETENTION_DAYS`) |
| Pattern | Append-only — same durable pattern as `audit_log`, but a separate store (no `audit_log` overload) |

The snapshot payload is the same dict returned by `GET /api/v1/metrics`:
`MetricsRegistry.snapshot()` plus live pull-gauges (`tasks`, `jobs_in_flight`,
`executor_sessions_active`, `run_step_queue_depth`). Both the route and the
periodic writer call the shared `compose_metrics_snapshot(state)` helper in
`runtime/daemon/metrics_store.py` so the persisted payload stays byte-identical
to the live route response.

The store is constructed at daemon startup on `DaemonState` (from
`DaemonState.from_runtime` or `DaemonState.idle`). Schema creation is
idempotent (`CREATE TABLE IF NOT EXISTS`); re-initializing the store after a
restart is a no-op.

**Compatibility:** v0 (DB-backed enrollments) and v1 (flat single-org) runtimes
both get the store on startup — the store is created on demand regardless of
runtime shape and touches no existing DB.

### HTTP route labels (route-template bucketing)

HTTP latency is labelled by the matched FastAPI **route template** — resolved
after routing and prefixed with the request method — not the literal
`request.url.path`. For example a request to
`/api/v1/orgs/tourism-org/tasks/TASK-1505/completion` is recorded under
`POST /api/v1/orgs/{slug}/tasks/{task_id}/completion`, so dynamic org slugs,
task IDs, thread IDs, and job IDs coalesce into one bounded histogram per
route instead of one unbounded key per concrete value. Method separation and
the `__all__` aggregate bucket are preserved.

Two bounded, stable fallbacks exist and can never contain a raw ID/path:

| Condition | Label |
| --- | --- |
| No matched template (e.g. 404) | `METHOD __unmatched__` |
| `call_next` raises (unhandled exception) | `METHOD __error__` (elapsed time recorded; original exception re-raised) |

### Snapshot format marker and legacy-read compatibility

The shared composer (`compose_metrics_snapshot`) adds an explicit
`format_version` marker to every snapshot: `2` means route-template labels.
Both the live `GET /api/v1/metrics` response and each persisted row carry it
through the same composer, preserving the live/persisted byte-identical
invariant. A stored row **without** the marker is legacy raw-URL-path format;
it remains queryable and readable via `/metrics/history` and is never
rewritten in place.

### Storage telemetry (non-sensitive)

Each successful snapshot persist cycle emits one structured log line with
bounded, non-sensitive operational telemetry sufficient to compare storage
growth across a full 30-day rollover: `route_label_count` (distinct labels,
excluding `__all__`), `serialized_bytes`, `row_count`, `prune_count`,
`oldest_captured_at`, `newest_captured_at`, `db_bytes`, `wal_bytes`,
`page_count`, and `freelist_count`. It never emits route IDs, task IDs, thread
IDs, org slugs, or snapshot contents. A telemetry failure is isolated and can
never crash the scheduler loop or mask a successful persist. To measure the
steady-state reduction, compare these values at the same point in two
consecutive 30-day windows — do not expect an immediate file-size drop, since
row deletion does not shrink SQLite on its own.

**Never** delete `metrics.db`, `metrics.db-wal`, or `metrics.db-shm` by hand. Physical compaction (WAL checkpoint / `VACUUM`) is performed **only** by the sanctioned offline maintenance one-shot below — never manually and never against a live daemon.

### Offline metrics maintenance (startup-only one-shot)

Physical reduction of `metrics.db` (row deletion frees SQLite pages but does
not shrink the file) is a deliberate **offline/startup-only** operation.  There
is **no** live maintenance route, no resident maintenance gate, no
scheduler/automatic maintenance, and no traffic-quiescence system — the
daemon never runs maintenance while serving.

**Invocation (explicit one-shot, reuses the daemon bootstrap):**

```bash
python -m runtime.daemon --maintenance        # or: scripts/daemon.sh maintenance
```

The maintenance process runs **before** the daemon binds an HTTP listener,
before the FastAPI lifespan, and before any scheduler/worker starts, then
exits when maintenance completes (success **or** failure).  It never writes
pid/port files and never starts a normal daemon.  Run it only while the
daemon is stopped; it refuses when a daemon pid is alive, and SQLite
fail-closes (checkpoint busy / `VACUUM` locked) if a live holder exists.

**Ordered sequence (all through `MetricsStore`):**

1. Record bounded **before** telemetry.
2. **Prune** rows strictly before the unchanged **30-day cutoff**
   (`_RETENTION_DAYS = 30`).
3. **Checkpoint** the WAL (`PRAGMA wal_checkpoint(TRUNCATE)`); a busy result
   fails closed.
4. `PRAGMA integrity_check` must return exactly `ok` **before** compaction.
5. Controlled `VACUUM`.
6. **Post-VACUUM WAL checkpoint** (`PRAGMA wal_checkpoint(TRUNCATE)`); a
   busy/error result fails closed — no success report is ever produced after
   a failed post-vacuum checkpoint.
7. Final `PRAGMA integrity_check` must return exactly `ok` (post-vacuum
   integrity evidence).
8. Record bounded **after** telemetry.

The report/log captures before/after DB & WAL bytes, row count, cutoff,
page/free-list counts, duration, pre- and post-vacuum checkpoint/integrity
outcomes, prune count, snapshot-size and route-label cardinality — never
labels, IDs, slugs, or snapshot content.

**Failure/recovery:** an invalid integrity/checkpoint/VACUUM result, stale or
malformed runtime state, or any operational exception returns a **nonzero**
exit with a stable bounded/redacted classification plus fixed recovery
guidance only — never raw exception text, tracebacks, filesystem paths, or
injected content — and no success claim, no automatic retry.  Valid
pre-existing historical rows remain queryable where SQLite guarantees it.
Retry requires a fresh explicit invocation.  Never hand-edit or delete
`metrics.db`, `-wal`, or `-shm`; never run a live compaction against a
production database while the daemon serves.

### GET /api/v1/metrics/history — persisted snapshot history

Returns persisted metrics snapshot rows from the `metrics_snapshots` table,
newest-first. Requires bearer auth (inherited from the `metrics` router).

**Request:**

```
GET /api/v1/metrics/history?since=<ISO>&until=<ISO>&limit=<int>
```

| Param | Type | Default | Description |
| --- | --- | --- | --- |
| `since` | ISO-8601 string | none | Lower bound on `captured_at` (inclusive) |
| `until` | ISO-8601 string | none | Upper bound on `captured_at` (inclusive) |
| `limit` | int | 500 | Max rows to return (capped at 5000, min 1) |

**Response** `200 OK`:

```json
{
  "snapshots": [
    {
      "id": 42,
      "captured_at": "2026-07-04T12:10:00+00:00",
      "snapshot_json": "{...}"
    }
  ]
}
```

When `since` and `until` are both omitted, returns the `limit` most recent rows.
When the daemon state is idle (`metrics_store` is `None`), returns
`{"snapshots": []}` gracefully (never 500).

## Retired System Assistant

System Assistant is retired (THR-294). Its HTTP/WebSocket routes, CLI commands,
settings, dock and Cmd/Ctrl-K binding are removed. Existing assistant config,
conversations, workspace files and skill links remain inert and are not cleaned
or migrated. Provider conversation roles and ordinary org-agent capabilities
remain supported. The reserved `system_assistant` workspace name stays excluded
from the org-agent YAML migration, preventing legacy YAML reads or sentinel
writes. Historical database, audit and token records remain readable.

entry keyed by the profile name before launch (THR-107 seq155). Custom-adapter
profiles (``command_adapter_id: custom-adapter:<id>``) are an exception — they
use the exact founder-APPROVED, hash-verified absolute adapter executable as
their launch artifact and do **not** require a separate ``executors.json``
record keyed by the profile name. No ``shutil.which`` or PATH discovery is
used for any profile. See
[agent-executors-and-permissions.md](./agent-executors-and-permissions.md).

Supported active roster creation, approval, revision-CAS executor updates,
dedicated executor updates and termination maintain exact per-agent profile
requirements inside their canonical fence; pending enrollments do not bind.
Prompt/model/repo updates preserve the current profile while composing only their intended delta inside the process writer gate. Init and active create/approval retain that gate through final current capture and bootstrap; executor switching retains a single gate through materialization, synchronous bootstrap, final mutation and compensation. Request cancellation and SSE disconnect drain started workers while retaining the gate; admitted request writes finish terminal reconciliation before propagating cancellation, including repeated cancellation. The short teams/canonical sections end before awaited host work.
Their profile leases release before ordinary publication capture and awaited
bootstrap, including compensation exits. At daemon state construction, U2B rebuilds exact per-agent custom-profile
dependencies for every loaded org and reconciles any interrupted coordinated
profile operation before the state is exposed to routes. It uses the U1A
org-local profile relations plus an owner-only same-host `flock`; there is no
new machine-global database or schema. A coherent dependency change publishes
a new org authority generation, while an absent, removed, or otherwise
unpublished required profile or a profile whose custom adapter is not currently
approved and resolvable keeps the org fenced. The shared profile YAML's
read/merge/replace writers additionally take one store-scoped leaf `flock`, so
different profile leases cannot lose each other's entries. Direct-connect
`planned` projections are production-sweep eligible after transient profile
contention. Independent route/sweep contenders re-read the durable terminal row
under the profile lease before any mutation or fence. Dynamic org attachment
captures canonical authority/roster inputs outside profile/publication leases
and SQLite transactions, brackets discovery with the existing durable authority
revision, and validates that revision under profile-then-org mutation ownership.
Changed captures retry boundedly or refuse; synchronization consumes the captured
roster without another directory scan. It holds every canonically ordered
referenced profile lease through coherent synchronization/publication and
shared-map insertion; its mirror
digest must equal the current global digest before readiness is exposed.
Startup preserves existing workflow ownership. Before workers start, U3a(b)
may fulfill an already committed completed H operation under current authority;
it creates no new author activation or host execution.

THR139 S1 separates existing and new databases. Existing `OrgState.load` retains
foundation installation where required, validates full F/E/G/H before workflow
recovery, and never adds the draft or handoff extension. F remains usable for legacy work;
its cutover projection/log names the operator migration and recovery waits for validated E/G/H.
POST /orgs creates a fresh skeleton, initializes complete H before attachment,
and retains its existing cleanup/error ownership. Empty files/missing tables or
startup discovery are not proof of new creation. Generic Database/runtime-audit
construction remains workflow-free.

Compatible cold reopen preserves the complete durable schema, every table's
data and row identities, and the file set, modes and non-database bytes.
Ordinary authority recovery can commit and release an ephemeral lease, changing
SQLite physical pages; closed owners leave no lease or database sidecar residue.
Validator-only and unsupported-reader refusal checks separately retain exact
entire file-byte and mode preservation on a closed database.

Explicit existing-org migration (operator authorization required):

```bash
python scripts/migrate_workflow_draft_schema.py --runtime-root <absolute-root> --org <slug> --check
python scripts/migrate_workflow_draft_schema.py --runtime-root <absolute-root> --org <slug>
```

The script validates the schema-v2 runtime and actual org path, full F/E SQL,
markers/history/integrity and stored source/draft closure without running generic
migrations. One bounded SQLite writer transaction installs only the exact three
draft tables/six indexes and version1 marker, validates E, then commits. Partial,
unknown/corrupt layouts and nonorg/missing/symlink targets refuse; complete E/G
replay preserves existing data and labels its actual layout. `--check` performs no migration, exits0 for ready
E/G or3 for migration-needed F; refusal1, parser2. Pristine F retains preceding-reader
compatibility until migration. **Every E/G/H requires a compatible reader**, including
new empty orgs; no downgrade stripping or live migration is implied by shipping
this script. Read-only WAL inspection can use SQLite sidecars; crash rollback may
leave a non-hot journal, while original data/schema/files remain intact.


Adapter approval propagates the profile target selected before lease
acquisition into its existing serialized registry writer, including an empty
selection. It revalidates that target before approval, idempotent return or
binding. A concurrent supported submission changing the target returns the
existing 409 `profile_consumer_changed` conflict without adapter/profile
mutation; retry selects afresh after ownership releases. Known-target live
contention remains 409 `profile_coordinator_busy`. No-target approval still
succeeds without a profile, and no new lease is taken under the writer lock.

A genuinely empty default org remains attached with no agents and `teams=[]`
when its initial authority publication is fenced by the missing default reviewer.
Attachment proves absence of active and pending definitions and canonical/in-memory
teams outside leases and transactions, brackets that discovery with the durable
revision, and validates it under profile-then-org ownership. It preserves the
initial fenced generation and publication journal; `verify_admission_ready()`
still refuses `authority_pointer_not_ready`. Outstanding dependency or profile
operation evidence, unfinished canonical writers, and stale captures refuse this
exception before synchronization. Reviewer policy and snapshot validation do not
change; subsequent coherent canonical setup uses ordinary publication/recovery.

## Org Config: Timezone and `current_time` Prompt Injection

Top-level `timezone:` in `<runtime>/orgs/<slug>/org/config.yaml` is the org-wide
local zone. It is optional; an explicit value must be a valid IANA name
(validated at load). `None` (the default) means **inherit machine-local**.

`org_config.resolve_org_timezone[_display]` resolves the effective zone:

1. explicit IANA name → `ZoneInfo(value)` (a bad value falls through, never crashes);
2. `None` → machine-local: the IANA name derived from `/etc/localtime` when
   possible, else a fixed offset from `datetime.now().astimezone()`
   displayed as `UTC±HH:MM`;
3. ultimate fallback → UTC.

A `current_time:` line is injected into **every** executor-backed agent session
prompt — across all providers (claude, codex, opencode, pi), fresh on every
spawn, wake, and turn. The single shared renderer `org_config.render_current_time_line(tz, label, now)`
produces the line; each prompt builder resolves its own effective zone and
calls it, so the line is identical everywhere. The four session types and their
builders are:

- **task / subtask** — `Orchestrator._build_agent_prompt` (the shared
  `Parameters:` block), zone via `resolve_org_timezone_display`. `run_step._build_agent_prompt`
  is **not** a separate path: it builds only the inner `role_guidance` body,
  which is wrapped by `Orchestrator._build_agent_prompt`.
- **working-hours wake** — `wake_runner.build_wake_prompt`, zone via `resolve_org_timezone_display`.
- **thread reply/bootstrap** — `thread_runner.build_thread_prompt` (full) and
  `build_thread_delta_prompt` (resumed-turn delta), zone via `resolve_org_timezone_display`.
- **private dream** — `dream_runner.build_dream_prompt`, zone via the dreaming
  precedence `resolve_dreaming_timezone_display` (`dreaming.timezone → org.timezone → machine-local → UTC`).

Format: ISO-8601 with offset plus the zone label, e.g.
`2026-06-27T12:47+08:00 (Asia/Shanghai)`, or `2026-06-27T12:47+08:00 (UTC+08:00)`
when only an offset is derivable. The wall clock is an injectable `now` callable
(default `datetime.now(timezone.utc)`) so prompt snapshot tests are deterministic.

## Org Config: Dreaming

Per-org `dreaming:` config controls the private nightly reflection scheduler: enablement, local schedule time/timezone, catch-up behavior, and agent include/exclude selection.

`dreaming.schedule.timezone` is **inherit-by-default**: an omitted value resolves
`dreaming.timezone (explicit) → org.timezone → machine-local → UTC` via
`resolve_dreaming_timezone`, threaded into `dream_scheduler._scheduled_datetime`
before any `ZoneInfo()` call. (Pre-TASK-976 an omitted value defaulted to the
literal `UTC`; orgs relying on that implicit default now schedule on
machine-local time — host-local night, as intended.)


## Independent nightly disk sweep (THR307)

One daemon-wide operator control is `<daemon_home>/disk-sweep/stop.json`.
Its only supported content is a JSON object containing exactly `stop` with a
real Boolean: `{"stop":true}` suspends scheduled deletion for all orgs;
`{"stop":false}` or a genuinely absent literal file permits the default policy.
The control is read afresh once per regular scheduled attempt, after the normal
same-night reservation and before native writer reservation or worker launch,
inside the shared1800s budget. Invalid/unreadable/oversized/truncated/changed
content, wrong/extra/duplicate keys or types, invalid UTF8, symlinks (including
dangling links or aliased parents), hardlinks, directories and special files
visibly suspend. Only final-file ENOENT means absent; other I/O failures suspend.
Malformed control does not prevent daemon/org startup. Operators alone write or
remove this local file; there is no per-org override, setting/API or new schedule.
Suspension logs its cause and, when publication is safe, writes `outcome:
suspended` with daemon-scoped `scheduled_admission` (`path,state,reason` and an
error when invalid) in the existing summary/audit payload. All orgs remain
unreached, with no worker/PID, reaping, removal-byte or successful-sweep claim.
The existing audit empty-task/daemon envelope and org ordering remain. Marker
and report reads/writes traverse literal no-follow parents; publication uses a
new exclusive pending inode and descriptor-relative replacement. A missing or
aliased parent, aliased/hardlinked/special final metadata or existing pending
entry refuses without writing through that path. Scheduled work never creates
a missing control parent. Refusal is visible through the existing daemon log;
a summary or org audit may be unavailable and must not be claimed as persisted.
Only manual preview can create missing report parents, with no-follow traversal.
After the single stop observation, the actual initial date summary is published
before any native writer reservation. Allowed admission initially records
`incomplete`, empty org results and all orgs unreached, without a finished/PID or
mutation claim. Unsafe date-summary or pending metadata therefore refuses with
zero reservations or worker effects. Later checkpoint/publication failures after
genuine reservation retain native uncertainty; this initial observation is not
a fence against subsequent filesystem changes.
These are filesystem observations, not a racing-rename fence or hard real-time
certification. Publication failure never permits deletion. Manual `--dry-run` remains usable and nonmutating with
true, invalid or absent control; it may write its existing reports/audits.
Removing/fixing the file affects the NEXT existing scheduled attempt only:
no same-night catchup/retry or marker reset. This is a run-admission boundary;
writing stop does not cancel an already launched worker or promise a hard
real-time stop. Existing deletion/ownership/age/containment gates still apply.

Founder THR307seq134/136 makes scheduled deletion effective for EVERY normally
admitted org, including missing config, legacy off/dry_run/on and newly admitted
orgs. The obsolete `disk_sweep` key is ignored regardless of shape/value; there is
no per-org opt-out or replacement setting. Unrelated config validation remains.
Reports carry actual `mode` (scheduled on, explicit preview dry_run), config digest
and no `configured_mode`. Explicit manual preview inventories even legacy off.
This is independent of `workspace_cleanup` and its unchanged legacy settings.
Machine-local03:00 (not org timezone), next future occurrence only, sequential org order and one1800s monotonic
batch budget. No cursor/rotation or candidate count cap. Unknown eligibility and
unreached scope retain; the owned worker process group is stopped on deadline or
shutdown. `output/` and durable scratch manifest/lock siblings stay excluded.

Actual task AND structural root must each be completed/failed/cancelled/superseded.
Recognized finished jobs completed/failed/rejected are skipped before owner-lineage
lookup; active jobs still require valid lineage and retain their structural root.
Unknown job status/active lineage retains.
Under Founder THR307 seq194 item2, nested Git repositories and `.git` files/
directories, Unix sockets and FIFOs inside admitted task scratch are disposable
after both task and structural root end. Socket/FIFO handling uses no-follow
stat/allocation and contained unlink with metadata-only O_PATH descriptors,
never endpoint/content I/O. Devices/unknown special types retain. Worktrees and
Git metadata keep strict special-object checks; all ownership/jobs/age/link/
mount gates and durable manifest/lock/output exclusions remain.
Recursive mtime+ctime age of at least one hour and no-follow/same-filesystem containment
apply to both phases. Git additionally requires owned registration,
clean/nonignored-untracked status and HEAD reachability through retained local
branches/remotes; branches remain, ignored contents are disposable. Git removal
is nonforce, with at most one fully refreshed contained repair/retry. Dry run
never chmods/removes/prunes. Before trusting Git exit0, a complete no-follow
linked-admin census reconciles entries, metadata pointers and worktree backlinks
with Git output and literal task roots. Missing/unreadable/aliased/conflicting
metadata retains that entire repository as a literal unknown scope with null
candidate count; independent scopes continue within the same deadline. Identities
are refreshed before Git removal, repair, retry and prune; earlier partial effects
stay unknown rather than becoming zero-effect success. A genuinely absent admin
with primary-only inventory and no task roots remains valid. Partial discovery
and uncertain Git-prune scope prohibit prune.

Manual dry run: `python -m runtime.daemon.disk_sweeper --dry-run --runtime-root
/absolute/runtime` (one command). JSON is in `<daemon_home>/disk-sweep/`; existing
org audit action is `disk_sweep_summary`. Allocation is inode-deduped and successful
removal-attributed; retained/partial/unreached bytes and physical freed space stay
unknown, whole-disk percent is context only. Below20% is flagged in audit/JSON;
no new notification interface. Founder THR307 seq117 removes all OS-process
eligibility inspection from this nightly sweeper, including process namespace,
cwd/root/exe/fd/environment and the former four-name/zombie policy. Top-level
JSON and org/audit `process_scope` state that process usage is not checked;
`process_exemptions` remains an unused empty compatibility field. This claims
neither process absence nor executable authentication. Filesystem mount boundaries
still retain via `/proc/self/mountinfo`; other owners' legacy/root-terminal process
gates are unchanged. Ended tasks/root status does not prove OS-process quiescence.


See the [full current contract](../superpowers/specs/2026-10-15-nightly-disk-sweeper.md)
for evidence semantics, limitations, finite disposable verification and staged
operational acceptance. The bounded `--corrections-only` lane now runs C1–C7 plus S1–S5 (cases8–12),
with finding-specific selection through `--correction-cases`; C4–C7 cover all-org
deletion, every legacy preview, fresh admission/retry and partial/native reporting.
C3 retains its no-process-inspection assertions at Python
syscall boundaries while allowing real self mountinfo reads. Task/job/clock inputs
are synthetic; filesystem/Git/config/JSON/audit projection are real. Historical case9/10
terminal-lineage expectations are superseded; active/unknown refusals remain.
The mount/process-root isolation fixtures remain SKIPPED/UNEXECUTED under seq62.
The separate historical isolation source supports disposable namespace boundaries. Run as the ordinary user; only its
private helper is mapped root. Missing capabilities return77/UNAVAILABLE, never
PASS; no privileged fallback or new automated check is authorized. Source
publication alone does not change loaded code. Root TASK12353 owns reviewed
delivery, normal rollout/serving verification and seven actual scheduled on-night
receipts. Founder seq134/136 already authorizes all-org activation after delivery,
without another activation decision. Original TASK11742/JOB4898 recovery and
root11948/PR1128 keep their separate ownership/evidence. Seven legacy agent
sessions remain until seven actual approved on-night observations and separately
approved cutover; no manual cleanup fallback.

## Org Config: Workspace Cleanup

`workspace_cleanup.enabled` is a boolean scheduler switch that defaults to
`true`; setting it to `false` disables the daemon-managed cleanup scheduler. When
enabled, the scheduler evaluates the daily local 03:30 occurrence in the org
timezone, comparing existing occurrences as UTC instants (skipping nonexistent
spring-forward times and taking the first `fold=0` instance of ambiguous fall-back
times), with exactly one post-warmup current-window catch-up, no historical backfill
and no rolling 24-hour or seven-day trigger cooldown.
`workspace_cleanup.reclamation_actions_enabled` is separately strictly boolean
and defaults to `false`. When true, the bounded pre-agent reclamation hook may
select and revalidate finite canonical targets before invoking the existing
consumer; it acts only on a third-or-later cleanup ordinal whose preclaim owner
is assigned to a registered in-memory `TeamsRegistry` agent and reconciles to
the invocation's initial successful claim (the first two runs stay
report-only), under one shared one-second deadline and at most 23 read/load
admissions with at most five best-effort consumer calls and no refill or
recovery. `false` prevents those action admissions and affects later admissions
only; it cannot revoke an already admitted call. Malformed values retain the
shared loader's existing error behavior.

Routine scheduled and exact-marker manual cleanup reports remain durable on the
existing agent page via ordinary completion/results (THR-259 seq418). The scheduler
admits a clean-root task without report-thread configuration, lookup, ID allocation
or composite creation. Task ID allocation, composition and ordinary insertion stay
synchronous under `org.db_lock` after awaited measurement; enqueue follows successful
insertion. Complete marker history, ordinals, cadence and deduplication are unchanged.
There is no routine thread posting/reuse obligation. Preserve all historical threads,
messages, associations, results and audits, explicit founder-requested coordination,
truthful anomalies/partial failures/unknown bytes/independent verification gaps and
the current-session final `happyranch report-completion` callback. Exact manual
first-line display eligibility (alone/LF/CRLF) neither creates trigger audits nor
changes daemon count or action authority. Runtime and canonical bundled-skill
rollout require separately authorized deployment after merge.

Cleanup activity uses three nonunique indexes installed with `IF NOT EXISTS`
after legacy columns exist: tasks(assigned_agent,created_at DESC,id DESC),
audit_log(task_id,agent) where action='workspace_cleanup_triggered', and
task_results(task_id,agent,id DESC). Existing definitions are not validated;
reader SQL, history and cleanup authority are unchanged. Breaker listing/mint
use sequential `await asyncio.to_thread` calls and the original DB RLock, also
used by close. Removal can log a sweep error; cancellation can leave a worker
running and a committed pending token for later attached-tick recovery. No
org lifetime lock, worker drain or new shutdown guarantee is added.

The daemon-composed daily brief and manual dispatch both follow the ONE shared
`workspace-cleanup` TASK system contract (`requires_repo=false`; source
`runtime/skills/bundled/workspace-cleanup/SKILL.md`), whose exact manual first
line is `HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)` (an unmarked
manual request is inventory-only). Its bundled read-only
`scripts/check_path_use.py` applies the approved THR-259 seq171/seq185
observation: an authoritative recorded terminal status plus a fresh complete
same-user process scan replaces separate live-session/task-to-process identity,
and a fixed login/session daemon (sshd-session, systemd --user, (sd-pam),
ssh-agent, gpg-agent, gcr-ssh-agent) qualifies only by exact readable process
name AND exact bounded cgroup role and is deliberately uninspected; any other
unreadable same-user process is `unknown` and skips.

Preparation and outer-batch evidence use the packaged `prepare_cleanup.py` and
`observe_cleanup_batch.py` (THR-259 seq506). Compare explicit aware terminal
instants, preserving original strings, exact inclusive604800/86400-second floors
and both action-time rechecks; naive/missing/malformed/changed times refuse.
Freeze cap40 with historical overlap exclusions and no refill. Useful preparation,
honest complete empty/no-repo evidence and incomplete enumeration are distinct;
empty leaves retain native inventory/peer/history receipts and emit no request.
Render one supported task/session-bound request with omitted or relative cwd,
actual remaining budget and preserved TMPDIR; submission rejection/uncertainty
ends that attempt without an alternate or retry. The outer host observer requires
readable self context, host-init/unstacked proc and protected/mount metadata,
including workspace .codex/.aws without credential-content reads. Root PID1
namespace denial is diagnostic, without a new equality veto. An incomplete
baseline admits no batch; native batch failure and postcheck failure/drift both
remain nonzero/incomplete with their original causes. Only the default packaged
batch/candidate runner executes; scanner/action gates are unchanged. Local report
or persisted result is distinct from canonical lifecycle/output publication;
failed/unpublished evidence overrides local success. Known-excluded nonempty inventories finish no-action only with verified
same-bound preparation, original/copied inventory and manifest/history hashes,
owner/native receipts and independently consistent excluded rows; a state label
alone is insufficient. Result admission/pending remains separate from evidence
completeness: missing inventory/baseline/postcheck stays blocked/nonzero. Native
batch argv/cwd/exit/raw is persisted before postwrites; after/sequence write
failure retains source/binding and both causes as unknown/nonzero, using bounded
captured cleanup_sequence_fallback JSON if the sequence cannot be persisted.
JSON writers flush, fsync and close a one-attempt .NAME.pending sibling before
publishing the canonical name with an exclusive hard link; existing canonical or
pending evidence is never replaced. Pending siblings remain evidence, are never
consumed as canonical receipts, and need no unlink for failure safety. This does
not add a directory-fsync/crash-durability guarantee. On observer write failure, report
--sequence must select the exact captured cleanup_sequence_fallback JSON file;
report retains its native batch/source/binding/write causes, blocks even against
success-valued pending bytes, and separately retains admitted-result/pending facts.
A missing canonical sequence also produces blocked intent with an evidence error.
The local evidence ordering is attempt envelope → fresh native gates → canonical
eligibility → first mutation → native terminal outcome → verified fsynced journal
append → canonical reference-only acknowledgement → whole-coverage admission.
The batch journal is the sole ordered outcome ledger. Direct Bash use needs the
same bound manifest/history/journal/evidence sink; stdout alone grants no action.
Cache isolation-directory creation is mutation. Failed rename/rmdir causes and
separate original/cache/directory identity and measurements survive; only positive
restoration/absence/source/status/raw-index proof admits safe refusal. Residue or
uncertainty halts. Git read observations suppress optional index refresh.

Each attempt and acknowledgement uses the existing exclusive pending write,
flush/fsync/close and exclusive hard-link publisher. A canonical file with its
identical expected staging sibling is valid; pending alone, conflict or collision
is not. Acknowledgements reference binding, envelope/eligibility identities and
exact journal prefix/row range/hash/ordinal, without a second success ledger.
Batch resume and report preflight the entire bounded namespace and journal before
any skip/admission; orphan or unacknowledged terminal evidence blocks, including
when fallback is missing/truncated. Explicit native failure/interruption wins over
readable files. Same-batch acknowledged prefix progress and later enclosing native
batch/sequence/postcheck completeness are separate prerequisites. No automatic
recovery or directory-fsync/crash-atomic guarantee follows. Historical bytes remain
historical, with missing new-contract proof explicit; never rebind or retry them.

Captured native uncertainty (output cap, timeout, spawn/capture failure, signal or
failed publication) is distinct from a fully captured ordinary nonzero gate
denial. The writer and every eligibility/coverage/resume/report reader retain
the native cause and block action or next admission, even with healthy enclosing
files or missing fallback. Native action/capture children inherit the candidate
cancellation group. The batch stops and reconciles its owned group before
terminal observations; outer capture allows that cleanup before forced kill.
Residual or unproven survival is unknown and unsafe, never clean completion or
permission to retry. Possible mutation remains partial/unknown without reuse.


A genuine bound completion callback is the final session action. This repair grants no cleanup allocation.

The shared procedure executes that scanner only through a task-bound,
host-visible HappyRanch job and validates a closed-schema, non-truncated receipt
binding task/session, actual job, stored execution identity, terminal result,
complete output totals, and scanner coverage; direct in-session fallback is
forbidden. Candidate-related task and trigger evidence uses complete paging
rather than an org-wide history cap. PR evidence is completely paginated and
repeated, with every open, closed-unmerged, duplicate, changing, conflicting,
or malformed result refusing. Preservation accepts the existing durable ref,
an owning origin task branch whose head equals or descends from the candidate,
an owning-task merged PR, or an any-task merged PR whose confirmed head
contains the candidate. An existing owning branch is authoritative:
non-containment or failed containment evidence refuses without merged-PR
fallback. The last route requires complete stable double-read
discovery, merged/default-branch confirmation, and a separate complete stable
compare; discovery alone and other-task unmerged PRs never count, while an
owning-branch unmerged PR still refuses. Merged integration may not preserve
original commit topology. Dirty whole worktrees remain protected, with the sole narrow exception
of a literal root `.venv`/`node_modules` cache whose removal leaves tracked
source bytes and Git status unchanged.
The containing worktree must be at the owning primary's exact registered
`.claude/worktrees/<TASK>` location on `task/<TASK>`. A complete no-follow
`lstat` walk before and at action time refuses nested mounts, cross-device or
foreign-owned entries, protected descendants, incomplete evidence, and drift.
Root-plus-descendant byte accounting precedes action; a success receipt requires
literal absence and unchanged protected-path identities.

## Terminal task-worktree reclamation

There is no configuration switch or cadence for future root-terminal release.
All four terminal states (`completed`, `failed`, `cancelled`, `superseded`)
trigger the original post-commit owner once for the actual parent tree,
including NULL-parent workflow tasks. Child deferrals become advisory only in
this root pass; all current independent preservation gates remain. Child hooks
retain their one-task/five-second policy and recorded deferral gate.

Root batches share10s work/0.5s DB/50ms lock admissions,1s report,1s owned-group
reap,16 events/256 nodes/depth64/page64/16 attempts,5s clipped candidates,
1MiB capture/256KiB receipts. This is a conditional 12s ordinary-I/O target,
not preemption of SQLite/fsync/kernel I/O. Interrupted start never retries;
incomplete closure gives zero removal and explicit frontier/remainder.
Verified success requires absence, unregistration, preserved branch and
protected identities; possible partial action or report anomaly halts.

The shared safety helper and unchanged packaged same-user scanner serve root
release; async owners await the finite pass off-loop outside locks. Manual/daily
retain their authenticated host-job scanner, age/ordinal/peer/history/cache
gates. No boot history scan, recurring continuation or rollout authority is
added. `workspace_cleanup.enabled` and
`workspace_cleanup.reclamation_actions_enabled` retain their separate meanings.
See [the full owner/evidence contract](orchestrator-contracts.md#forward-only-terminal-task-worktrees).

## Agent Configuration: Single Source of Truth (THR-095)

**Founder-ratified invariant (THR-095 option B):** Every piece of agent
configuration has **exactly one authoritative store**. Two surfaces for the
same value is a breach. There is no precedence ladder — the founder explicitly
rejected resolution-order ladders as a design pattern.

For org agents, the single authoritative store is the **org frontmatter**
(`orgs/<slug>/org/agents/<name>.md`, parsed as ``AgentDef``). The three fields
that were previously dual-surfaced — ``executor``, ``repos``, and ``model`` —
are now read and written **exclusively** through ``AgentDef``:

| Field | Authority | Consumer |
| --- | --- | --- |
| ``executor`` | ``AgentDef.executor`` | ``_resolve_executor_name``, ``thread_runner``, ``dream_runner``, ``wake_runner`` |
| ``repos`` | ``AgentDef.repos`` | ``list_agents``, ``init_agents`` clone loop |
| ``model`` | ``AgentDef.model`` | ``_resolve_model_name``, ``_resolve_agent_model`` |
| ``reasoning_effort`` | ``AgentDef.reasoning_effort`` | task/thread/dream/wake/schedule launch selection |
| ``allow_rules`` | ``AgentDef.allow_rules`` | (already .md-only before THR-095) |

The workspace ``agent.yaml`` file is **no longer read or written** by any
org-agent path. A one-shot startup migration (``migrate_agent_yaml_to_frontmatter``,
idempotent, runs on every daemon start) copies any residual ``agent.yaml``
values into their owning ``.md`` exactly once, then deletes ``agent.yaml`` and
writes the ``.agent_yaml_consumed`` sentinel. The reserved legacy name
``system_assistant`` is excluded before any YAML read or sentinel write.
Retired assistant workspaces remain inert; startup never repairs or consumes them.

See also: `docs/agent-guides/orchestrator-contracts.md` (resolver contract),
`docs/agent-guides/agent-executors-and-permissions.md` (executor surface).

### Optional reasoning effort

Built-ins accept case-preserving fullmatch `[A-Za-z][A-Za-z0-9_-]{0,31}` through shared `model_effort_args` validation; historical catalog values are metadata, not a membership gate. UI uses plain text: blank on explicit Save maps to null/default, without trimming or truncation. Explicit wire/direct empty strings refuse; CLI clear retains its existing meaning. Custom adapters still require approved declared-value membership (at most16 lowercase identifiers); legacy/model-only effort controls remain disabled with saved values visible. Token acceptance proves syntax/transport only, not native model/account support.

With explicit effort, OpenCode requires nonempty provider/model slash components (additional model path segments allowed), no whitespace/control/NUL, at most one # and a nonempty embedded variant byte-equal to effort; it composes one literal `-m provider/model#effort`. Unset preserves the original model transport. Pi recognizes only its existing finite thinking suffix set; arbitrary safe colon suffixes do not become native thinking syntax. Codex keeps one literal quoted config argv element.

Bounded evidence does not verify dynamic manage_agent update/enrollment at runtime (source only), native models/accounts, full task/thread/dream/wake/schedule lifecycles, host/callback or custom contained-wrapper prelaunch. General integration including callback is SKIPPED THR243seq42; former Python units are RETIRED. No ad hoc Product E2E or protected mutation/repetition proof is authorized.

`reasoning_effort` is optional frontmatter beside `model`, never a second YAML store. Omitted updates preserve it; explicit `null` clears it. A real CLI change clears omitted model/effort; an explicit new pair is validated together. Unrelated whole-definition writes preserve the field. The residual YAML importer preserves it for the same CLI, clears it on a CLI change, and refuses incompatible model/effort before replacing canonical content.

Built-in tokens follow the shared grammar above; approved custom input-v2 declarations retain finite membership, surfaced through existing executor prerequisite metadata. Unset preserves existing model transport and emits no effort override. Static support says nothing about the chosen model/account accepting a level. OpenCode requires `provider/model`; embedded OpenCode `#variant` and recognized Pi `:thinking` must agree with explicit effort. See the [current contract](../superpowers/specs/2026-10-11-agent-reasoning-effort.md) for mappings/defaults and compatibility limits.

## Session Timeout Resolution

`Orchestrator._resolve_session_timeout(agent_name, task_id=...)` walks three layers:

1. Task override: `tasks.session_timeout_seconds`, set via `happyranch revisit ... --session-timeout-seconds N` and inherited by children.
2. **Org override**: `org_settings` DB table, section `session_timeout_seconds` (THR-095 single-store).
3. Code default: `Settings.session_timeout_seconds`.

Positive integers only. `<= 0` or non-int raises at parse time. The `agent_name` argument is unused but kept for call-site symmetry. Legacy `session_timeout_seconds` in agent frontmatter is silently ignored.

## Org Settings Storage (THR-095)

The 5 web-writable operational knobs — `dreaming`, `threads`, `session_timeout_seconds`,
`working_hours`, `reviewer_agents` — are stored in the **`org_settings`** SQLite table (same per-org DB
as `tasks` / `audit_log`). `org/config.yaml` is a **git-tracked seed file only**;
the daemon **never** reads or writes these keys from the file once the one-shot
seed migration has run (first daemon startup after upgrade).  The seed also
**strips the 5 writable keys from config.yaml** (one-time mutation, atomic
write) so the file remains clean thereafter.  Every subsequent `PUT` routes
solely through the DB — the daemon no longer touches `config.yaml` for these
keys, preserving the #408 single-source-of-truth invariant.

### Schema

```sql
CREATE TABLE IF NOT EXISTS org_settings (
    section     TEXT NOT NULL PRIMARY KEY,  -- dreaming | threads | session_timeout_seconds | working_hours | reviewer_agents
    value_json  TEXT NOT NULL,             -- JSON blob for that section's subtree
    updated_at  TEXT NOT NULL,             -- ISO-8601 Z
    updated_by  TEXT DEFAULT 'founder'
);
```

### Resolution ladder

Every consumer site resolves through a **single documented precedence ladder**:

| Knob | Resolution order |
| --- | --- |
| `session_timeout_seconds` | `tasks.session_timeout_seconds` (per-task override) → `org_settings` DB row → `Settings.session_timeout_seconds` |
| `dreaming` / `threads` / `working_hours` | `org_settings` DB row → **dataclass code default** (OrgConfig field defaults, NOT config.yaml) |
| `reviewer_agents` | `org_settings` DB row → **code default `["code_reviewer"]`** (THR-175). A JSON list of agent names; configures which chain legs are reviewer legs that gate auto-advance. Names are validated against the org's live active-agent roster; an unknown name resolves fail-closed to the code default (see below). |

**Code-default tier**: the fallback is always the Python dataclass default
(e.g. `DreamingConfig(enabled=False)`, `OrgConfig().threads_enabled=True`),
never a value parsed from `config.yaml`.  This is critical: after the one-shot
seed, `config.yaml` is **not** the read source for these 4 knobs — stale
seed-file values must never become observable.  The `resolve_org_setting_*`
helpers accept a `code_default` parameter that every call site MUST pass as
the true dataclass default.

No site special-cases storage; every reader uses the appropriate
`resolve_org_setting_*` helper from `runtime/orchestrator/org_config.py`.

### Write path

`PUT /api/v1/settings/org` writes each patched section to the `org_settings`
table, with its `config:<section>` audit row **in a single SQLite transaction**
(atomic upsert + audit insert — a crash before commit rolls BOTH back).
The daemon **does not** touch the git-tracked `org/config.yaml` after the
one-shot seed — the DB is the sole write target.

### Audit

Each `config:<section>` audit row carries a `tiers` list of **exactly the keys
that changed** (not the full before-snapshot).  A partial threads update that
only changes `default_turn_cap` emits `tiers: ["default_turn_cap"]`, not
`["enabled", "default_turn_cap", "invocation_timeout_seconds"]`.  This preserves
`AuditLogger.log_org_config_write` touched-tiers semantics.

Audit rows are atomic with their settings row — a crash before commit rolls
both back (no split-brain).

### Seed migration

A **one-shot, idempotent** seed runs on the first daemon startup after upgrade
(`org/.org_settings_seeded` sentinel). It copies the current `config.yaml`
values for the 5 writable keys into `org_settings`, then writes the sentinel.
On subsequent startups the sentinel makes the seed a no-op. After seeding,
`config.yaml` values are ignored — the DB is the single authoritative store.

`reviewer_agents` (THR-175) additionally has an idempotent **backfill** that
runs on every startup for orgs whose seed sentinel already fired before the
feature shipped: if the `reviewer_agents` row is absent it persists the
config.yaml value (or the `["code_reviewer"]` code default), and it never
overwrites an existing explicit row.

`reviewer_agents` names are **validated against the org's live active-agent
roster** (the file-based `org/agents/*.md` registry) at every surface that can
seed, backfill, or persist them — not just `PUT /settings`. A configured name
that is not a real active agent is never persisted as a reviewer setting (the
code default is persisted instead), and an already-persisted malformed or
unknown value resolves fail-closed to `["code_reviewer"]` at every read path.
This guarantees an unknown reviewer string can never silently demote
`code_reviewer` from the reviewer set and re-open the QA auto-advance hole.

## Bounded Failure-Recovery (TASK-573)

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

Attachment authoring correction is distinct from failed-child lineage/revisit. It
creates no historical revival, successor or execution grant.
Keep task-only evidence in output/<actual-task-id>, report output_dir and use
supported recall. Do not reattach an already-claimed maker key to a reviewer.
For a distinct attachment handoff upload a fresh authorized copy with
`happyranch attach-upload --org ORG --as-agent CURRENT_AGENT --file /absolute/source`
within the actual authorized environment (upload has no --session-id flag).
Inherited access grants reading, not another claim. Never upload task-only
logs/screenshots/review packets to Shared Artifacts as an ownership workaround.


When a subtask fails, the parent task is re-enqueued for a bounded manager-wake
decision step — NOT cascade-failed. This replaces the pre-TASK-573 behavior where
any subtask FAILED unconditionally cascade-failed the parent without giving the
task owner a chance to re-ground.

Contract (founder-approved in THR-028, refined in THR-078):

1. **Bounded wake.** On child failure, re-enqueue the parent for a fresh
   decision step. The failed subtask's reason (`note` + completion report /
   error context) is available so the task owner can author an updated brief.

2. **Mechanical retry provenance (THR-078 / THR-091).** A manager may
   re-dispatch unchanged work or direct revised work with a valid
   `revisit_of_task_id` link to a same-agent FAILED child under the current
   parent. A current successor root may also link the same-agent FAILED child
   of a predecessor root when the bounded verifier authenticates every
   intervening recorded supersession (at most 20 root records / 19 edges).
   The final spawn transaction repeats claim and lineage validation after
   `BEGIN IMMEDIATE`; an unresolved local failure cannot be bypassed by a
   remote historical link. Original parent links and failed history remain
   unchanged. The link is historical provenance, not semantic brief
   comparison or automatic root escalation.
   A later COMPLETED or SUPERSEDED descendant retires earlier FAILED ancestors
   from causal selection (THR-183).

3. **Manager ownership on exhaustion.** A retried slice's second failure keeps
   its durable causal lineage and wakes the owning manager. It is not a runtime
   escalation or upward cascade. Any later manager-proposed escalation follows
   the configured THR-181 hook; inactive/static policy is not evaluator
   CONTINUE, and committed escalations remain human-resolved.

   Authority-v2 refusal follows the same structural split (THR-277): a
   non-root writes `authority_v2_refusal_task_failed`, terminalizes as FAILED,
   and wakes its parent through the fenced ordinary/recovery cleanup tail;
   roots retain the existing founder escalation. Startup classification of an
   `admitted` non-escalate causal result is read-only and does not fence the
   task from ordinary recovery in that boot.

   THR308seq133/127 uses one private Database/connection-bound startup reader
   invocation with optional authenticated exclusion-only anchors. Both discovery
   and exact getters share complete strictly decoded root histories only after
   stable outside autocommit connection/data_version/total_changes/schema_version
   bookends enclose a pinned main-table coherent read under the RLock. Exact
   getters each open a current closure; no speculative target/hit/absence escapes
   before clean matching exit. Local writes (including rollback), other-connection
   commits (including results-only), schema/attachment/isolation/ownership drift
   or unavailable tokens clear all entries; only clean cleanup permits one
   fresh uncached fallback. Current identity/authority checks and shipping parsing
   remain fresh. Private raw scalar tuples/decoded graphs reach only closed
   read-only validators; public audit readers allocate independent graphs.
   THR308seq151 / ROOT12914 retains caps of 8 entries across both maps,
   4096 rows/root and 8 MiB. One complete new-payload charge plus actual
   session/field/map/wrapper/charge/epoch/group metadata is a conservative
   object-graph upper bound; shared objects can overcharge and reduce capacity.
   Old payloads are not rewalked for sizing. Shallow trial rejection preserves
   original map allocation/order/entry/charge identities. Discovery affinity
   plans at most 16 chronological positions / 8 repeated-root descriptors,
   protects only admitted fitting histories through last use and evicts LRU
   unprotected entries. Nonfitting plans do not evict useful entries; all-protected
   admission rejects unchanged. Projections/first-use reads stay chronological;
   exact reads retain ordinary LRU and individual fresh closure. Group state
   clears before fallback/exact/publication and on exit/invalidation/escape.
   Oversize evaluates complete history uncached; accounting Exception clears
   retention, while strict evidence/cleanup/interruption keep original escapes.
   Transient allocations/SQLite/RSS/time are not bounded. Memo references clear
   before refusal/tails, on failure and invocation exit. S1–S3/Q1–Q5 behavior/
   discovery/exact SELECT/fetch/decode counts/sensitivity remain NOT RUN/PROOF
   NOT RUN. ROOT12914 accepts current source-delivery gaps; ROOT12801's historical
   limitations remain attributed to that delivery.
   Attributable later exact/refusal failures quarantine the root before bounded
   logging, skipping all later same-root work; pending also excludes siblings.
   Ambiguous attribution fails task recovery globally. No early diagnostic append is added and local
   quarantine is not durable fencing. Unrelated roots continue only with known
   clean owned cleanup and a usable connection. Failed cleanup or unresolved
   COMMIT/connection state instead aborts the sweep, skips its entire finally
   worktree batch and propagates before state publication/serving. Caller-owned
   helpers preserve their transaction; full sweep entry refuses it before Pause
   or workflow effects. Earlier committed effects remain, with cleanup explicitly
   incomplete and no automatic retry/repair. See the
   [refusal safety contract](../superpowers/specs/2026-09-29-authority-v2-schema-refusal-recovery.md).

4. **Chain-leg failure.** A failed chain leg clears the chain and returns a
   decision owner to bounded wake. Passive pipeline carriers instead fail
   closed and settle through their outer fan-out barrier with causal-leaf
   context intact.

5. **Happy path unchanged.** All subtasks COMPLETED → parent enqueued for
   next decision step. REVISE-verdict auto-advance in chains is unchanged.

6. **Reviewer/QA verdict discipline.** A review/QA leg completes with an
   APPROVE/REVISE/PASS/FAIL verdict and never self-blocks. A `status=blocked`
   with empty `waiting_on_job_ids` is a malformed report; the leg is treated
   as FAILED and wakes the parent for a decision step.

Implementation: `runtime/orchestrator/run_step.py` —
`_enqueue_parent_if_waiting`, `_advance_chain_for_completed_child`,
and the retry-link validation seam. See also
`docs/agent-guides/features-and-invariants.md#bounded-failure-recovery` and
`docs/agent-guides/orchestrator-contracts.md`.

## Running The Daemon

The CLI is an HTTP client. Start the daemon once, then run CLI commands.

```bash
scripts/daemon.sh start
scripts/daemon.sh status
scripts/daemon.sh stop --force     # graceful shutdown (default daemon needs --force)
scripts/build_web.sh              # default: local UI flag profile
scripts/build_web.sh --production # production UI flag profile
happyranch web [--no-open]
```

`scripts/build_web.sh` defaults to `npm run build:local`: development UI flag
defaults, then optional gitignored `web/config/features.local.json`, then the
existing explicit `VITE_ENABLE_KB_COMPOSE` compatibility input. It writes
`web/dist/` and runs `npm ci` first if `node_modules` or its `.package-lock.json`
is absent. `scripts/build_web.sh --production`, `npm run build` and Mac
packaging use only `web/config/features.production.json`, ignoring local/ambient
feature overrides and hiding unfinished UI such as Work Hours. `npm run dev`
uses the local profile; Vitest uses development defaults plus the KB input,
ignoring the local file. The flags change UI visibility only: daemon settings,
scheduler, API and CLI behavior do not depend on them. See
`web/ARCHITECTURE.md` § Build-time UI flags.

`scripts/daemon.sh start` removes a stale port file, launches the daemon, and
waits up to `HAPPYRANCH_DAEMON_START_TIMEOUT` seconds (default `120`, positive
integers only) for `GET /api/v1/health` to answer on the configured bind host.
Wildcard bind addresses are probed through their loopback equivalent. If the
background process exits or serving observation times out, startup exits1 and
prints the last20 daemon-log lines. Curl is required before launch; no port-file
fallback establishes serving. The finite startup line above reports recovery
separately; timeout retains any possible launch. Actual-Python lifetime
duplicate-start protection remains separate.

After genuine successful Server startup, the serving lifespan's private diagnostic
coordinator schedules one bounded off-loop pass; startup/readiness never waits
for its acquisition or result. Port/PID files and health probes do not signal it.
Only initial supported attachments are eligible; later attachments/reconstruction
stay unavailable. One shared readiness budget and bounded privacy-safe daemon logs
report selected-history agreement/uncertainty only, never cleanup or release.
Shutdown/detach revoke and positively join native work before DB close; incomplete
drain retains the DB barrier and cannot advertise clean shutdown. See the native
lifetime paragraphs above for exact bounds, ownership, labels and log limits.
Source delivery is not deployment or runtime verification.

The full founder-facing CLI is documented in `skills/happyranch/SKILL.md`.

### Task-scoped audit index installation (THR-278)

`Database` uses its existing connection and initialization path to install one
nonunique, nonpartial `idx_audit_log_task_id ON audit_log(task_id)` after the
audit table exists. It applies to fresh and existing databases and is idempotent
on reopen. It preserves historical rows, raw scopes, payload bytes, IDs and
writer transaction boundaries; it adds no `task_results` index.

The same post-executescript initializer also installs
`CREATE INDEX IF NOT EXISTS idx_audit_log_action ON audit_log(action)` beside
the task index. Fresh/supported existing initialization and reopen authenticate
this exact explicit, nonunique, nonpartial, single ascending BINARY action key
(the implicit rowid tail is allowed). A conflicting selected-name definition
refuses without drop/repair. Existing rows/IDs/actions/payloads, queries and
ascending-ID results remain unchanged. Current independent fresh/v0/v2/v2-organic
whole-schema references include it through current Database; historical preimages,
raw schema equality and preflight/layout ordering remain unchanged. A raw offline
old DB needs the supported initializer before current-reference-dependent use.
Strict older readers may refuse; rollback requires a reviewed compatible reader
preserving both indexes/history, with no new workflow migration/version or live
index installation. Initializer-wide atomicity and measured speedup are not claimed.

For a separately authorized operator, install the reviewed source using the
deployment procedure for that runtime, with its locked dependencies and
compatible binaries. Before the ordinary restart, drain active consumers and
take the normal consistent database backup. Use `scripts/daemon.sh start` and
`scripts/daemon.sh status` under that operational authorization; the existing
startup initializer installs the index. No standalone live SQL repair is needed.
Verify the installed source revision, normal health/readiness and each org's
ready/fenced receipt separately. With a read-only SQLite inspection, verify the
exact index SQL, `PRAGMA index_list('audit_log')` nonunique/nonpartial flags and
`PRAGMA index_info('idx_audit_log_task_id')` single `task_id` key; the unchanged
task-scoped query should report `SEARCH audit_log USING INDEX
idx_audit_log_task_id` without a temporary ORDER BY tree. Read back retained
audit IDs/raw scopes and actual decoded results. Measure live startup after
installation before claiming readiness within the default 120 seconds.

Rollback uses a reviewed compatible release that understands the complete
indexed schema and preserves the index/history. A preceding reader whose v1
reference lacks the index can refuse full-schema comparison; do not claim it
is downgrade compatible, drop the index live, or rewrite failed attempt residue.
Merged source alone proves neither installed schema nor live settings repair.

## Running Tests

For current retained-lane boundaries, see
[test placement](project-layout.md#test-placement).

Python unit tests and their gate are **RETIRED** by founder THR-291 seq40.
The former default `not integration` selection was deleted, including its
unmarked component, contract and system acceptance cases. Git history preserves
that source; do not restore it as a hidden unit or renamed E2E suite.
`scripts/local_ci.sh python` refuses with exit 2. `all` runs source boundary and
Web lint/static checks on Node 24 and reports units RETIRED. Its literal daily
PENDING message means corrected behavior is unproved; historical schedules failed.
There is no default Python selection: pytest requires explicit existing canonical
platform file selectors, or the isolated parent with `-m integration`.

Founder THR-291 seq69 makes automated PR/main merge CI **lint/static only**.
Required checks at the final PR head and exact merge on main are exactly
`Source Boundary Lint` and `Web Lint (Node 24)`, both unconditional with no
path applicability. The manager supplies these two names to `pr_ci_waiter`;
`pr_ci_merge` retains its independent review/QA, pinned-head and guarded-merge
contract. Freshly observed GitHub protection has empty required contexts/checks
and `strict=false`; source configuration is not external enforcement.

Default/`all` and `web` run only source boundary lint (all), Node24 colour scan,
ESLint and TypeScript `--noEmit` analysis, with locked `npm ci` setup.
Behavioral tests and builds are outside all/pre-push/required merge checks.
Normal hooks stay enabled; never bypass hooks or the guarded worktree.
Independent full code review and scoped QA remain mandatory. Static checks
cannot establish lifecycle, callback, browser or platform correctness;
behavioral regressions can be detected only later when their coverage executes.

Fresh Product E2E keeps the daily `0 19 * * *` schedule (19:00 UTC /
03:00 Asia/Shanghai) on immutable default-branch `github.sha`. Founder
THR291seq247 additionally authorizes **ONE** immediate full main execution under
sole root TASK-11897 through the reviewed `workflow_dispatch` entry. Manual
admission requires actual `refs/heads/main`, default branch `main`, and matching
repository/workflow/run/source identity. This entry is not standing execution
authority. Baseline56 variants EACH Python3.12/3.14 plus TERM3.12/INT3.14 retain
exactly four execution artifacts and the original bounds. `Daily Product E2E
Health` remains outside merge checks and reports the actual event, run/attempt,
SHA and checkout tree; manual receipts are never labeled scheduled.
There is no local E2E alias, PR/push/release/workflow_call/repository_dispatch
entry, automatic retry, extra schedule or SLA. Admission, lint and scoped QA
success do not establish product execution success. The root owns the one
actual dispatch after independent review/scoped QA, both final-head lints,
guarded merge and an exact-main receipt.
Scheduled historical runs38482572306 and38644465643 were observed and failed
at their own immutable sources. Corrected behavior remains **PENDING** until
an authorized main run executes the repair; historical3.13
failures and all-six-cell evidence remain unchanged. Prior first-slice receipts remain
historical evidence at their own source, not daily/final-head verification.
Engineering_manager owns daily failure/missing-evidence triage through existing
tasks/threads; no new notifications or guaranteed response SLA is implied.

Python units stay RETIRED; `python` exits2. General integration is **SKIPPED**
under THR-243 seq42, never PASS. Web Vitest, native/platform, integration,
the named actual CLI callback and their isolation fences retain their sources.
Docs Manual build and the real tenant-isolation harness retain manual-only
workflows, outside PR/main. Manual/tag macOS packaging is a separate release
activity and invokes no fresh E2E. Legacy nightly integration remains separate
at 07:00 UTC, with its historical failures and fixtures unchanged; its manual
`all_only` input runs the same lint/static all selection. No legacy repair or
run is authorized by this cadence change.

See [the reset coverage gap record](../python-test-reset.md) and
[Local CI](../local-ci.md) for retained selectors and evidence limits.

Direct pytest uses `tmp_path_retention_policy = "failed"`: passing `tmp_path`
and `tmpdir` fixture directories are removed best effort; ordinary failed-call
diagnostics are retained. This does not cover arbitrary tempfile writes or
guarantee retention after setup/teardown errors or interrupts. Use the frozen
uv environment (currently pytest 9.0.3); the option requires pytest 7.3+, while
the declared `pytest>=7.0` range also admits unsupported 7.0–7.2. See
[pytest scratch scope and limits](../local-ci.md#per-run-pytest-scratch-lifecycle)
for factory directories, explicit basetemp, version compatibility and cleanup
limits. Unit selections are retired; retained integration belongs only
in the documented disposable CI venue.

Integration tests run real production orchestration with deterministic external
CLI stubs in disposable GitHub runners or a separately authorized Mac Linux guest.
The test parent is sanitized before pytest/runtime imports, with temporary homes,
configuration, registries and runtime data plus exact source/callback/stub identity.
Direct integration collection without that parent refuses. Never run integration
on the live Linux daemon host, including through jobs. See `docs/local-ci.md`.
The disposable default roster exists before startup/registration so the real
lifecycle initializes eligible manager selectors. Example-based two-org creation
likewise supplies the declared roster, including the default `code_reviewer`,
before POST so canonical reviewer discovery is coherent; the Codex bootstrap case uses
the supported pending-manager approval path. Explicit task plans write bound JSON
payloads and invoke the tested-source CLI on one line with an absolute `--from-file`.
Registered shell stubs restore their own temporary bin directory before identity
admission, so uv or daemon PATH normalization cannot shadow that callback.
`_nested_daemon_env` still copies the sanitized test parent and removes exactly
its two outer containment markers; it does not sanitize a production environment.

`tests/integration/fake_claude.sh` routes task invocations through `$FAKE_CLAUDE_PLAN` and thread invocations through `$FAKE_CLAUDE_THREAD_PLAN`. Tests that exercise both flows must set both fixtures and write explicit
`DeterministicPlan` bytes. Missing, changed or unavailable plans refuse before
execution; an intentional no-op is an explicit plan. The original two-org fixture
opts into a bounded, source-authenticated pre-session exception observer without
changing launch, return or exception behavior. Historical two-org cause remains
UNKNOWN; the offline missing-agent control is not a historical diagnosis.

### S2 activation attachment and recovery

Compatible readers dispatch schema1/@1 and document-review schema2/@2 by exact
immutable definition plus compiler/validator/source pins across every draft and
version, including noncurrent rows. Publication version numbers do not denote
format. Earlier readers refuse any @2 template data; retained @1 history is never
rewritten to upgrade it. No old-reader downgrade compatibility is promised for
mixed/new data. These formats use existing immutable JSON/BLOB storage meanings
and leave F/E DDL/layout/reference, foundation markers, fresh-org initialization
and explicit-only existing-org migration unchanged. Startup/reopen/enable installs
no extension or format conversion. Recovery continues through its shipping owner
with pinned template-driven role checks; possible launch remains uncertain.


Actual OrgState attachment installs the consumed activation/draft services before
workers; the generic Database constructor and runtime-audit remain workflow-free.
The S1 explicit migration/new-org contract remains unchanged: existing F startup,
reopen and enable never install E. Empty orgs stay fenced until real coherent
roster/team/profile publication makes them ready. Initial activation requires
ready E/G and the actual Founder cutover chain. A queued committed intent survives
lost enqueue notification; startup and periodic sweeps rediscover its existing
eligible task. Live author-capacity refusal, including a claim requeued before
launch, waits for a later sweep; deduplicated queue notification runs after every
profile/org/publication/SQLite lease releases. Missing/malformed
workflow evidence fences legacy effects. Possible host launch cannot be recovered
from PID absence, TTL expiration or session registration: it remains uncertain
under workflow_recovery and blocks drain until genuine containment evidence exists.
Disable fences new admission/prelaunch and preserves ownership of already admitted
work. No startup deployment, live migration, enable, assignment or restart follows
from publishing S2 source.


## Root task Pause and Resume (THR-292)

The supported single daemon per org owns task_pause_controls installation/complete validation before recovery/admission; fresh org creation initializes the same literal extension. Generic Database and runtime-audit remain pause-free. No new config, dependency or permission switch is introduced. Durable holds/release intent and deferred original owners survive lost notifications. Startup preserves possible-launch/ACTION uncertainty and uses actual existing callback consumers, never consumed-result replay. Complete independent schema references include the pause dimension without weakening F/E/G/H comparisons; v2 raw schema is still observed-only. Rollback requires compatible code, including for an empty extension. Deployment/restart/live migration require their separate operator authority. [Current contract](../superpowers/specs/2026-10-10-root-task-pause-resume.md).

Unclaimed completion recovery retains the actual original return and SIDs while held, with no running deadline. Its 120-second deadline is pinned atomically with the accepted real admission/claim, binding and launch journal. Preclaim/admission waiting spends nothing; claimed recovery retains its expiry across Resume, duplicates, retry and restart, including committed no-launch failure. Unresolved original host/tree evidence is separately retained under the original session/owner/generation before recovery conversion. It remains an unknown drain blocker after callbacks, terminal lifecycle, restart or quiescent recovery, without owning a new producer. Known-quiescent originals add no unknown blocker, and deferred unclaimed recovery alone is not execution.


## THR299 compatible reconciliation installation

The [exact receipt producer/reader](orchestrator-contracts.md#exact-failed-continuation-reconciliation-thr299)
has no schema migration or startup reconciliation. Existing org load/reopen
never creates its audit receipt. Only SAME TASK10725 owns the later guarded
merge, supported compatible installation and exact operation; source delivery
by a child authorizes no production install/restart/write. Preserve naming
TASK10565 and its active work while coordinating supported drain/installation.

Supported source install remains `uv sync` from the selected clean reviewed
source, then `uv run happyranch --help`. Daemon lifecycle uses
`scripts/daemon.sh start`, `status`, `stop --force`; there is no `happyranch start`.
No live command is implied here. Record exact reviewed SHA/file digests,
Python interpreter/CLI import origin and actual launch cwd/PID/start identity;
health/status/doctor alone do not identify serving bytes. Then only the actual
ordinary TASK10725 session may obtain fresh preflight, submit one exact receipt,
verify canonical replay and BOTH original advisory retry paths before naming
use. A changed binding/provenance/state refuses; do not extend the pair or
allocate a replacement operator. Keep original command/key on uncertain COMMIT
and inspect historical receipt before replay.

Once the new audit producer exists, old code that ignores it is an unsafe
operational rollback for retry consumers even though schema is unchanged.
Rollback requires a compatible receipt validator/verifier; never delete an
audit, reparent children, revive failure, or rewrite history to downgrade. Real
post-install positive and running-source evidence remain separate obligations;
naturally allocated synthetic positive/replay/crash proof is UNPROVEN under
Founder299seq11, never full behavioral PASS.


### Current owner review discovery (THR139 seq506)

GET `/api/v1/orgs/{slug}/workflows/activations/{activation_id}/review-selection?proof_kind=owner-human`
returns closed `workflow-owner-review-selection@1`: activation/instance IDs, nullable
original `{handoff_id,human_request_id,collection}`, nullable successor
`{revision_operation_id,human_request_id}`, and `return` `{eligible,reason,required_action}`.
The collection has the existing closed RevisionCollection fields, including current
validity and approved-use status. No H yet gives null original; no authenticated graph
gives null request/collection; a template with no human slot has null human ID.
A genuine successor is discoverable immediately at admission, before r2 staging.
IDs navigate independently authenticated readers and grant no access or approval.

Relational same-org/current activation/current owner and active read grants precede
private proof decoding, repeat after each awaited lock and at the coherent read
boundary. Complete H/I, pinned contract and template-selected human request closure
validate before disclosure. No peer IDs/findings/inputs, bytes, sessions or proofs
are returned. The GET reuses READ-ONLY `current_collection`; no domain DB, authority
lease, event, queue or join writes. It never collects, discovers or fulfills work.

Return readiness reuses the full read-only predicate consumed inside POST's atomic
writer: supported @3/@4 document contract/actions and I, full current authority,
a genuine successful Boolean-quiescent r1 source, all peers closed with an objection
and no join, no successor, same author and available capacity. A needs_revision
label alone is insufficient. GET advises only; POST retains replay/CAS/current
revalidation and allocation ordering. Access loss refuses the whole read; visible
effect holds return eligible:false with existing reason/action. Missing proof403,
invisible/wrong-owner404, malformed/duplicate/unknown/blank selectors, IDs or any
nonempty GET body422, missing compatible H/I409 with operator remedy, storage500
category-only. No migration is performed.

The typed `getOwnerReviewSelection` client sends only owner-human. Older daemons
report discovery unavailable; dependent actions stay disabled without fallback
access or guessed IDs. Native route classification/assertion parity preserves the
existing SurfaceAllowList and credential/transport policy; source inspection is
not native execution evidence. Mounted UI is a separate serial unit. Python units
remain retired, integration SKIPPED24342, broad behavior/proof UNEXECUTED and
FOUNDER-WAIVED139429/430; lint/static evidence is not behavioral acceptance.


## Disabled coding release catalog — K0

`runtime/workflows/builtin_templates/coding.v1.json` is the sole canonical release
resource,634 bytes without newline, SHA256
`6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8`.
Exactly one wheel force-include pair supplies it to the existing shared
`packaging/daemon.spec` inventory for daemon and CLI; the spec stays unchanged.
The reader resolves `importlib.resources.files("runtime").joinpath("workflows",
"builtin_templates","coding.v1.json")`, reading at most16384 bytes; exact634-byte validation also
rejects any longer resource, including a read that reaches that bound. No caller path, source fallback or default replacement exists.
Missing/unreadable/unsupported loader holds503 resource_unavailable; invalid
bytes refuse500 resource_corrupt. Static inventory is not installed-wheel or
frozen-loader evidence; both remain unverified. No startup read/seed, migration,
capability issuance, mapping, readiness flag, deployment or restart follows.

See [the current ordered-coding K0 contract](../superpowers/specs/2026-10-14-ordered-coding-workflow.md).
Independent final-head source review/scoped QA and lint/static checks remain
separate gates. Python units RETIRED; integration including callback SKIPPED24342;
behavioral/negative/mutation/repetition/protected proof UNEXECUTED /
FOUNDER-WAIVED139429/430. No package/native build or ad hoc daily E2E is authorized.

## Bounded repository artifact source — K1

`repository_artifacts.py` supplies closed canonical saved binding/adoption/presentation
readers; the private `repository_artifact_objects.py` supplies bounded no-follow
adopted-primary-store copies, sanitized typed Git reads, recomputed object hashes
and complete raw base/head tree manifests. Allocation failures are category-only
storage_unavailable with no launch or acquired directory. Every refusal retains
the parent's acquired private-directory identity for owner remediation; process
quiescence and omitted child metadata do not prove cleanup. Worker cleanup stays
alarm-bounded; the parent performs no recursive cleanup and accepts success only
after witnessing directory absence. Unknown launch/nested process/reap ownership
remains internal. Retained directory identity is not a claim of present storage.
Saved validation proves historical integrity only. OrgState mounts `WorkflowRepositoryArtifactService`; its current
resolve operation categorically refuses before decode or object I/O because K2b's
authenticated adoption/current authority producer is absent. No constructor flag,
caller Boolean, record/digest or injected callback authorizes content access.
K0 readiness remains false and code activation still refuses before allocation.
Current document formats/producers, routes/CLI/DTOs/schema/receipt/recovery and
native permissions are unchanged. Later coding writers must ship complete selected
read/stage/prelaunch/receipt/closeout consumers with before-I/O admission and
post-await authority revalidation. No live adoption/setup, execution or coding
readiness is delivered. Python units RETIRED; integration/callback SKIPPED24342;
behavior/security/protected proof UNEXECUTED/FOUNDER-WAIVED139429/430. Source-only
review/static checks cannot establish reader-security or behavioral acceptance.
See [the current K0/K1 contract](../superpowers/specs/2026-10-14-ordered-coding-workflow.md).

## Empty-only coding schema — K2a1

Founder THR139seq562 approving559/329 and sole ROOT TASK10693 authorize this
bounded source slice. Layout J is exact I plus `workflow_coding_mappings`,
`workflow_coding_runs`, `workflow_coding_attempts`, `workflow_coding_events`:
3987 UTF8 SQL bytes/SHA256
`2c4b556671ef76582cdcbaed1e68d8575d9648ce29773f52c3d947ceaeeb0d70`,32 event
kinds,17 expected automatic constraint indexes (4/2/7/4), no explicit index or
marker. F–I DDL/history and document @1–@4/r1/r2/one-human formats remain literal.
The complete owned-layout reader and every metadata/cutover path admit J ONLY
with all four coding tables empty and valid retained I/document/native data.
Partial/extra/changed layout, any populated coding table, reserved coding
format/pin/import/source ownership or native coding audit without its readers
refuses; it never returns legacy. Arbitrary document content is not a family
discriminator; formats/kinds are checked in owned metadata. K2a2 must supply complete populated saved-data
readers before any coding producer, including setup/import/mappings.

Workflow whole-reference selection retains F–J and adds workflow-only fifth
history `v2-chronological-20260530` (THR320seq150, accepted ROOT12848 composite
TASK12862/addendum, independent DESIGN-ONLY PASS12866). Its fixed whole seed is
56585 UTF8 bytes/SHA256
`9ac9a2034d63ecc502d4038b6d50f811692b8062c921c3d04115567f61ecaad7`:
195 objects independently constructed through complete historical Database
constructors bda5d062→1be72fe7 and the authentic thread sweep. Original S2 marker
and `sqlite_sequence(session_token_usage,0)` remain intact; observed/live schema
is comparison only, never constructor input. Both old preimages remain literal.

Legacy authority still admits ONLY fresh/v0/v2/v2-organic, rejecting the fifth
name before shared cache lookup even after workflow cache fill. Generic workflow reference consumers retain their whole-reference helper;
coding import uses scoped storage validation. Cache identity includes
`(layout,history,naming,Pause,team_move,coding_extension)`; ANY unavailable reference refuses
even if another digest matches. All nonNULL raw sqlite_master SQL ordered by
name participates, without normalization or object filtering. Authority-v2 stays
observed-only; generic Database/runtime-audit remain workflow-free.

Only fifth-history J uses whole seed → current Database → unchanged complete-I
initializer → shipped canonical coding DDL in workflow_schema_transaction →
require full validator J → unchanged Pause/team-move/naming order → raw digest.
Exceptions/unexpected layout remain unavailable. Other fifth-history layouts
retain their F/E/G/H/I paths; old families keep their construction and digests.
The strict fresh-J initializer still requires empty native operational storage
and valid remote markers; it correctly refuses this retained sequence row.
Neither that guard nor any real migration changes; calling the real coding
migration here would recurse through reference validation. Actual create-org
still explicitly initializes I. Normal OrgState.load subsequently adds coding
beside the actual document foundation; I plus coding is J. Enable performs no
document upgrade. Empty J uses the existing I draft/submission/handoff/
revision and document drain readers. Any J, even empty, blocks pre-J downgrade;
retain compatible J code or a separately approved verified pre-J backup. No
stripping, automatic migration or old-reader downgrade claim follows.

Historical offline document/coding transitions remain separately owned,
with target inspection; normal coding setup no longer requires that chain; submission/coding check3 differ from
exact handoff/revision migration-required1, and refused1 stops. The first three
writers remove only --check; coding requires --confirm-cooperative-offline.
Exact import absence is HTTP404 coding_release_import_not_found/CLI1, never
empty success or clearance of uncertain original file/key/body/effects/new-key
retry. Founder save remains Admission Disabled; operational acceptance stays
NOT RUN/open. See the ordered-coding-workflow spec's historical compatibility
operator guidance for the host/offline/backup/owner prerequisites.

The operator source `scripts/migrate_workflow_coding_schema.py --runtime-root
/absolute/runtime --org ORG [--check] [--confirm-cooperative-offline]` implements
the approved cooperative offline I→J path. All invocations open read-only first:
valid I --check returns3 (migration needed), compatible empty J replay returns0,
and invalid or unavailable observations return1. Only exact I with explicit
cooperative offline confirmation can reopen mode=rw. The independently resolved
owned org path and pre-open device/inode/mode observation bind the RO PRAGMA
main path; ORIGINAL identity survives RO close/RW reopen, is compared before
and after writer open, after BEGIN EXCLUSIVE and before commit. Main/path and
attachment checks, regular/single-link/current uid, org root/slug/marker and
symlink constraints retain the shipped G pattern. Missing observations or known
path/file/lifecycle drift refuse with original-owner remediation; the writer's
replacement file never becomes a new source baseline.

This is cooperative pathname/file observation, not independent native descriptor
proof or hostile/direct same-user swap protection. Founder270 approving265/269
and Founder562 preserve that limit. Step43 withdraws step41/42's excess native
opened-file bridge requirement; their categorical binding_unavailable refusal,
FAILED11447 and adverse11446/11448 receipts remain historical. No native bridge,
extension/dependency/VFS/raw-pointer/ctypes change is supplied or needed under
this model. Direct same-user file/DB swapping between observations remains a
disclosed cooperative limit; source-only delivery cannot certify its behavior.

Writes require positive current stopped state and complete current writer,
preparation and process exclusion throughout the affected registered-runtime,
normal-org/shared-daemon/store/source/environment and cooperative-other-host
window, including publications, profiles, moves, recovery and competing restarts.
Establish human control before constructor effects or a preceding source update;
repeat supported scope/process/lifecycle/identity observations before and after
effects and keep the original SQLite reservations. One census, PID absence or a
human yes alone is insufficient; unavailable present exclusion still refuses.
Historical pending jobs on ended owners, nonquiescent receipts, failed old
preparations/started audits and retained relocation storage alone require no
closure certification. Preserve their labels/ownership/keys/bytes; never settle,
relabel, clear or replay them. Current tasks, automatically consumable pending
work, source-result processing and current publication/profile/Pause/move/recovery
owners still hold. Complete data/history validation remains separate. This is
cooperative exclusion, not a cross-host lock, hostile same-user or hard real-time
guarantee. The original supported stop must succeed if attempted; independently
selected already-stopped entry skips stop without reconstructing old shutdown.
Exact I/source SQL types/rowids/bytes/FKs, unchanged retained equality, four empty
additions, complete independent J validation, FK ON and rollback remain required.
The primitive receives the script's original source identity and independently
compares it, never derives a replacement baseline. Runnable source is supplied;
valid check/replay/migration/rollback and drift refusal remain UNEXECUTED, with
no migration-safety or OS isolation claim. Earlier scripts keep original targets.

The finite shared discriminator inventory covers typed scalar fields and
structured metadata using the same rules. Malformed lexical member scanning
recognizes complete, escaped, duplicate and incomplete reserved envelopes
without searching quoted document prose. Authority `source_pin` and coding
setup namespaces are checked independently of canonical/hash-valid bytes,
including orphan rows and current authority pointers. Typed scalar/native
`business_purpose=coding-replacement-selection@1` participates in the same
valid/malformed/duplicate/escaped/incomplete metadata rules. Ordinary
worker_execution/session purpose and free document prose remain noncoding.
Recovery.classify_task runs the empty/reserved boundary under its existing lock/error fence before
no-workflow and no-root/draft/F5 legacy returns; any coding-family schema
object, including unknown/case-changed names or wrong types, also requires the
complete central reader. Names and owning-table names are each checked for
tables/views/indexes/triggers; no old ownership alias is needed. Missing org identity
reconciles categorically before either legacy return. Unknown/malformed coding
returns reconciliation_required, with no coding ownership, task writer or dispatch.
Ordinary noncoding legacy and the existing document draft/F5 paths remain.

K0 stays disabled; K1 current resolution still refuses before private decode or
object I/O. No capability/setup/import/mapping/adoption/start/stage/native task/
event/audit producer, new route/DTO/native permission/dependency/hook, live SQL,
rollout/deploy/restart/assignment or trial renewal is delivered. Source inspection
and lint/static receipts cannot prove migration, readers, security, behavior or
upgrade safety: all remain UNEXECUTED. Python units RETIRED; general integration
INCLUDING callback SKIPPED24342; broad/negative/mutation/repetition/protected proof
UNEXECUTED/FOUNDER-WAIVED139429/430. Full independent review, same-final-head scoped
QA and Source Boundary Lint + Web Lint(Node24)/local all remain required, followed
by manager guarded merge. Root owns K2a2–K6 and operational/real-agent acceptance.


## Saved coding grammar source — K2a2a

Sole ROOT TASK10693, manager step48 and Founder THR139seq562/559/329
bound `runtime/workflows/coding_saved_records.py` to pure, unmounted saved-record
inspection. All eight entry points check complete canonical bytes, strict types,
bounds and contained equalities. Original command bytes/IDs/CAS/selectors remain
intact; contextual coding repository roles preserve K1 identity/owner invariants
without widening document/K1 readers. Parsing creates no adoption or authority.

Accepted TASK11459 B2/D1 appendix SHA256
`5a1baa4967ffefb07967d1aa5e5771b6048801bfcd71b37d690ab74aa3722c9b`
closes ready/pointer/profiles/full mirror, exact Founder provenance and complete
five-column native admission plus capture on issue AND withdrawal. Native v1/v2
snapshots retain their complete original projection and ordering; reconstruction
includes all consumers, generations and digests. Genuine empty profiles differ
from missing evidence; other-namespace/removed/unbound/unused mirror rows remain
validated. Historical pointer/journal/fence are retained, never read from today.

Grant selection is position-specific: setup/issuance terminates in full original
issue/withdraw Q; mapping uses closed M with 1–8 ordered distinct complete U
setups; capability selects its permitted U/P parent. Complete preimages/hash,
ID/parent/time/source ownership, source versus mint admission, level/action,
queued-null versus actual bound SID and component/argv radius attenuation are
checked without format fallback or truncation. Each ancestry ends at U→issue Q,
with at most64 capability nodes/16MiB decoded ancestry and original1MiB limits.
Mapping cannot union one repository's setup onto another. U subtree digest is
not the containing native issuance-wrapper digest. Fixed repository/base/head
and provider subject are separate from navigation. Saved ready/Founder strings,
a hash or a parsed grant never prove authentic native rows or current access.

Full relational/native↔J/event/history readers remain required BEFORE every
producer: identical genuine admissions and historical cache_installed journals,
setup issuance-subtree/native-wrapper/pointer/withdrawal, source result/start
claim and native task/session/parent chronology, mapped role eligibility and
independence, mapping generation/adoption authenticity, run/context/binding/
activation/request/receipt/contributor closure, journal replay/CAS and host/
cleanup/provider/effect ownership. Current-use owners revalidate every setup/
ancestor/grant, cancellation/Pause/move and actual executor permissions after
waits. Valid revoked history permits no new launch/disclosure/effect. No store,
roster, object, provider, DB or network I/O, resolver or trusted flag is supplied.

K2a1 still refuses ALL populated/reserved coding data including validate_data=False;
F–I/J schema/reference/cache/recovery/migration owners, create-org I and document
history are unchanged. K0 stays disabled and K1 current resolution unavailable.
No mount, native admission insertion, producer, transport/auth/permission change,
live setup/migration/assignment/deployment/restart or retired trial follows.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; behavior/
security/negative/mutation/repetition/protected/executable proof UNEXECUTED/
FOUNDER-WAIVED139429/430. AST/raw-byte inspection and lint/static checks establish
no behavioral or test-authoring PASS. Fresh FULL independent review then
same-final-head scoped SOURCE QA and manager guarded merge remain required.


## Retained native setup issuance history — K2a2b1

Sole ROOT TASK10693 step50 / Founder THR139seq562/559/329 adds unmounted
`coding_authority_history.read_setup_issuance_history`. The caller must admit
the complete native schema and own a coherent read transaction/connection/lock
with raw native storage values. Fixed-table parameterized SELECTs only; no
BEGIN, commit, writer, repair, resolver or injected truth flag is supplied.
The org-wide setup namespace inventory checks complete canonical issuance
wrappers, exact native types, source pin `coding-capability-issuer@1`, namespace,
ID/revision/time, full original command, expected-revision CAS and unique
Founder operation keys/setup subtrees. The selected native authorization ID
resolves one contiguous same-namespace prefix with a genuine prior issue for
withdrawal. Setup IDs and authorization row IDs are distinct identities.

For every issue AND withdrawal in that prefix, the complete five-column native
admission matches the saved command digest/Founder principal/org publication
namespace/generation/ID. The named complete twelve-column historical journal
must match namespace/generation/expected generation, exact full snapshot
bytes/digest/profile fence and cache_installed state, without native NULL/type
coercion. The shipped pure readers reconstruct saved v1/v2 snapshot/profile/full
mirror evidence, including genuine empty profiles. Neither today's pointer,
cache, roster nor mirror is historical proof. Exact setup_bytes are the issue's
canonical subtree; its digest differs from the native issuance-wrapper digest.
Valid old issuance remains history after withdrawal and never restores activity.

All three SELECTs gate every projected value by exact native storage class
before Python fetch: TEXT plus byte length via CAST AS BLOB (including multibyte
characters and embedded-NUL suffixes), BLOB plus byte length, INTEGER scalar
only. Invalid/oversize values return a bounded NULL refusal sentinel while
original typeof evidence stays intact. Genuine nullable file_phase_owner NULL
is preserved; accepted values retain exact historical bytes/types without
coercion or truncation. Each projected text/BLOB value is at most1MiB.
Inventory admits at most1024 org setup rows; one additional bounded row can be
fetched only to refuse overflow. After each bounded fetch, the shared budget
enforces16MiB cumulative accepted raw native text/BLOB bytes across inventory
and required admission/journal reads. This is retained-byte accounting, not a
total process-memory ceiling: SQLite engine work, rejected fetched rows and
decoded/copied structures are outside that accounting. Overflow/missing/
mismatched/ambiguous/malformed/unsupported evidence refuses categorically.
Only the selected prefix's admissions/journals are established, not unrelated/
later histories. No live human, current grant, source task/SID/result or populated-J
admission follows. Full capability/task/result/session ancestry, mapping/J/run/
event/receipt/heartbeat and current-use closure remain NEXT serial units before
any producer; K2a2 is incomplete. No mount/OrgState/DDL/reference/cache/recovery/
migration/selector or public/CLI/TS/auth/permission/dependency/hook change.
K2a1 populated/reserved refusal INCLUDING validate_data=False, create-org I,
document history, disabled K0 and unavailable K1 current resolution stay intact.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; behavior/
security/migration/negative/mutation/repetition/protected proof UNEXECUTED/
FOUNDER-WAIVED139429/430. Source/static evidence is not behavioral acceptance.
Fresh FULL independent review then same-final-head scoped SOURCE QA return to
sole ROOT10693; no operational action or trial renewal is authorized.


## Retained mapping history and setup links — K2a2b2

Sole ROOT TASK10693 step53 / Founder THR139seq562/559/329 adds unmounted
`coding_mapping_history.read_mapping_history`. Caller admission of the complete
native/J structural schema and one coherent raw-value read snapshot/connection/
lock precede this SELECT-only call. It neither starts nor ends a transaction.
The selected native mapping ID resolves its complete same-org/release generation
prefix, never the current default. All ten native columns and original typeof
labels are retained, with full saved mapping bytes/digest, original complete
command/digest/key, strict INTEGER generation and expected_generation CAS,
original time and operation principal independently compared. Attribution follows
`WorkflowTemplatePrincipal.operation_principal`: agent:<issuer.principal>,
separate from raw role IDs and human:founder issuance provenance. Missing/gapped/
forked/conflicting operations refuse; no newest-row or same-digest fallback.

For each required mapping, its actual complete five-column admission matches the
original command digest/agent operation principal/capture namespace/generation/
ID. Its historical twelve-column publication journal matches full snapshot bytes/
digest/profile fence/expected generation/cache_installed and original nullable
owner. Shipped `read_saved_mapping_record` checks complete canonical mapping/
command/adoption/M grammar, setup prior-source and adoption times, repository
radius and all binding/adoption/capture pin equalities. Each M.setup resolves by
setup ID AND full canonical bytes to one genuine issuance.setup subtree, then
its complete earlier issuance/admission/publication prefix. The private native
setup composition shares the existing history owner; wrapper hash is distinct
from setup hash. Founder issue/withdraw semantics and authentic later withdrawal
remain historical facts; this read never restores a grant. No current pointer,
roster, cache, filesystem/store scan, adoption or remote-origin attestation.

Every projected value in all four fixed-table parameterized SELECTs gates exact
SQLite storage class BEFORE fetch: TEXT byte length via CAST AS BLOB, BLOB byte
length, native INTEGER scalar; genuine owner NULL remains NULL. Invalid/oversize
values become bounded NULL refusal sentinels with original typeof evidence, not
coercion/truncation. Per text/BLOB value at most1MiB; each mapping/setup inventory
at most1024 rows plus one bounded overflow fetch solely for refusal. Across the
whole composed call one shared budget limits8192 accepted native rows,16384
traversal steps and16MiB cumulative accepted raw text/BLOB bytes after bounded
fetch. Call-local indexed inventory/verified history can avoid repeated reads;
no per-root/recursion budget reset. SQL engine work, rejected-row and decoded/
copied structures are outside this accounting; no total memory ceiling claim.
Existing `read_setup_issuance_history` signature/result and retained semantics
remain unchanged; its established smaller inventories fit these added call caps.

Return values establish complete inventory grammar/attribution/operation
uniqueness and selected mapping-prefix admissions/publications plus the required
setup issuance prefixes only. They do not authenticate actual issuer task/SID/
policy/source_rule/current grant/capability/J graph or live store observations.
Full capability/native task/result/session/ancestry/J/run/event/request/receipt/
contributor/heartbeat/current-use/recovery/cutover readers still precede EVERY
producer; K2a2 remains incomplete. No mount/__init__/OrgState/schema/reference/
cache/recovery/migration/selector/producer/public/CLI/TS/native/auth/permission/
dependency/hook change. Populated/reserved coding refusal INCLUDING
validate_data=False, create-org I, document history, disabled K0 and unavailable
K1 current resolution remain intact. Root retains K2a2–K6 and genuine-agent/
two-org/override/backup-copy operational delivery. Accepted C01/C03/C09/C14/C15/
C16 and appendix variants remain source-scoped; no new case study/execution.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; parser/SQL/
product/security/negative/mutation/repetition/protected proof UNEXECUTED/
FOUNDER-WAIVED139429/430. Static lint/source assessment is not behavioral proof.
Fresh FULL independent review then same-final-head scoped SOURCE QA return to
sole ROOT10693; no maker verdict/merge/operational action or trial renewal.


## Retained native capability ancestry — K2a2b3

Sole ROOT TASK10693 step56 / Founder THR139seq562/559/329 accepts capability
encoding contract SHA25638d80c7a4cc7a3283fdea37dd6f21fb366df9c2eba5fc47415ebe2061051fc7f
following independent DESIGN PASS11478. Unmounted
`coding_capability_history.read_capability_history` adds the retained capability
closure after the earlier K2a2b1/b2 checkpoints. Their historical descriptions
of then-missing capability readers remain history; full K2a2 is still incomplete.

A selected actual authorization ID resolves all seven native columns: ID=P.id,
namespace=org/{org}/coding-capability/{P.id}, INTEGER revision1, complete canonical
P BLOB, full-P SHA256 TEXT, literal workflow-coding-capability@1 source pin and
exact P.created_at TEXT. ID/namespace/digest aliases conflict rather than act as
fallbacks. Each P's OWN complete five-column native admission matches its capture
ID/org publication namespace/INTEGER generation, H(complete P) request_digest
and historical agent:<P.issuer.principal> operation attribution. Neither parent
capture nor original source_admission_id substitutes for a child's admission.
H(P) is finite; capture.grant_digest still hashes the complete upstream P or U.
Historical attribution does not authenticate a currently acting source/service.

The existing pure saved reader validates the selected complete canonical P/U/Q
ancestry once, including immutable source ownership/admission, level/action,
component/argv radius attenuation, refinements, queued-null versus bound SID
and effect subject equalities. Every embedded P must then resolve to its OWN
native row with byte-identical complete P, exact envelope, own admission and
historical publication capture. Terminal U resolves by setup ID AND complete
canonical subtree bytes to the actual containing issue plus complete preceding
issuance/admission/publication history. U ID is not an authorization row alias;
H(U) is not H(issuance wrapper). Terminal original ISSUE Q stays intact. Missing,
duplicate/conflicting/cyclic/cross-org/wrong-type/mismatched evidence refuses
with one category, no private bytes or SQL diagnostics and no partial history.

Caller must admit the complete native/J structural schema and own one coherent
raw-value read transaction/connection/lock. Fixed parameterized SELECTs only,
no transaction control. All new/reused projected values gate exact SQLite storage
class and byte length BEFORE fetch; TEXT uses BLOB byte length including NUL
suffixes/multibyte characters. Original typeof/refusal sentinels and genuine
nullable journal-owner NULL survive; no coercion or truncation. Each variable
value is at most1MiB. One shared whole-call budget permits8192 accepted native
rows,16384 traversal steps,16MiB accepted raw TEXT/BLOB bytes, setup inventory1024
and selected capability ancestry64. One bounded overflow fetch only refuses.
Call-local setup caching retains complete histories; no recursive budget reset.
Enclosing/decoded saved bounds remain and can refuse before64 nodes. These raw
byte limits do not bound total SQLite/process/decoded-copy memory.

`CodingCapabilityHistory` retains source-to-selected native capability facts,
all seven columns/original typeof/matched own admissions, and actual setup issue
history. Later withdrawal preserves history without restoring any grant. No
current pointer/cache/roster/store/object/network is read. Necessary timestamp
equalities/order do not prove actual mint order, source execution, native
allocation/task/SID/result/assignment/ancestry/policy, acting-service ownership
or full J graph. Complete native source/action-claim/task/session/result/policy,
run/context/binding/activation/event/replay/request/receipt/contributor/heartbeat,
current-use/recovery/cutover readers still precede EVERY producer. No source-rule
verifier, current permission, genuine launch or provider effect follows.

Existing setup/mapping APIs and source owners are unchanged; their bounded
private composition suffices without a new helper or owner edit. No mount/
__init__/OrgState/producer/schema/reference/cache/recovery/migration/public/CLI/
TS/native/auth/permission/dependency/hook change. Populated/reserved refusal
INCLUDING validate_data=False, create-org I, document history, disabled K0 and
unavailable K1 current resolution remain intact. Original contract9f0cab1c,
appendix5a1baa49/cases17d50aa2 stay byte-preserved; only the four accepted native
envelope/own-mint/full-ancestry/history-and-bounds variants are appended to task
case evidence. Python units RETIRED; integration INCLUDING callback SKIPPED24342;
all SQL/parser/product/security/negative/mutation/repetition/protected executable
proof UNEXECUTED/FOUNDER-WAIVED139429/430. Static/source assessment establishes
no behavioral or test-authoring PASS. Fresh FULL independent reviewer then
same-final-head scoped SOURCE QA return to SAME root, with actual lint/static
checks, normal publication/hooks and manager guarded merge. Root retains full
K2a2–K6 and real-agent/two-org/override/backup-copy operational acceptance; human
trial remains retired and no live operation is authorized.


### Retained native source identities — K2a2b4

ROOT10693 step60 / Founder139562/559/329 accepts TASK11484 appendix
4ee1d1819d3b58ff1350b192d6438fa8ed8bf99ba7cc06abfb0fe315fb4312e1/cases
f55f71a1db09cf708dbad083cd8c955cd5241cf5f244586eaa3b19d7909c24ae after DESIGN11485.
Unmounted `coding_source_history.read_source_native_records` composes genuine
source-level P and complete native P/U/own-admission/publication history, sharing
one call budget/setup cache with the existing unchanged public readers. It returns
exactly immutable capability_history, source_task_record, ancestor_task_records,
session_start_audit_record and same_session_result_records.

Complete27 task/18 result/6 audit columns retain native storage classes/NULLs,
empty free text, all UTF8/JSON bytes, original timestamp spelling and finite REAL
cost. Source/parent-to-NULL rows are TODAY'S coherent records; ancestry includes
source within20 and follows no revisit alias. Missing/cyclic/foreign-team ancestry
refuses. The original source task/principal/SID selects exactly one native
manager_decision session_start under its closed owner grammar and the entire
bounded same-SID result inventory. Selected allocating INTEGER/full result must
match the source tuple and full closed workflow_start command/INITIAL work radius, never
latest result/action label alone. Nonallocating NULL selection proves neither
original callback absence nor absence of a later callback. Current assignment/SID,
tracker reconstruction, source admission/claim/current policy are separate facts.

Caller independently admits complete native/J structure and holds one raw-value
connection/transaction/lock; fixed parameterized SELECTs only, no await/transaction
control/DTO/default/converter. Original typeof/native byte gates precede fetch,
including multibyte/NUL suffixes; each new native row's combined variable bytes
must fit remaining budget. Singleton keyset pages renew that gate; bounded task
uniqueness probe prevents LIMIT1 conflict masking. Every fetched/rejected/overflow
candidate shares8192 rows/16384 steps/16MiB raw bytes,1MiB per value,P64/setup1024/
source20. Nullable/empty/finite-REAL accounting does not weaken existing mandatory
setup/admission validation. This is not a total process/SQLite/copy memory bound.
Category-only coding_source_history_invalid returns no partial records/private SQL.

Fixed callback C0/source C1 captures and independent historical consumer remain
unimplemented here: independently admitted C0/C1 compare exactly25 named fields
and preserve both timestamp observations only at the first boundary; ALL27 C1=C2
must agree after preflight, so intervening heartbeat refuses with zero partial
coding allocation/debit/publication. Full27 replay and version-aware heartbeat
handoff remain mandatory after bootstrap. Complete captures/J/native/authority/
invocation/Pause/move/host/recovery/cutover consumers must precede EVERY producer.
No capture/native writer/public mount or live authority follows. F–J/refusal,
create-org I/document history, disabled K0 and unavailable K1 remain unchanged.
FIRST FULL independent reviewer, same-final-head scoped SOURCE QA, static local
all/hosted lint, normal hooks/nonforce push and manager guarded merge remain.
Python units RETIRED; integration INCLUDING callback SKIPPED24342; all SQL/parser/
product/native/security/negative/mutation/repetition/executable proof UNEXECUTED/
FOUNDER-WAIVED139429/430. Root retains K2a2–K6 and operational acceptance.

Native result `learnings` is nullable free TEXT, inserted unchanged by its owner:
NULL, empty, prose and UTF8 bytes remain exact in EVERY same-SID result. Only
owner-serialized JSON columns undergo JSON validation; all native storage/UTF8/
prefetch per-value and shared-byte gates remain. The selected allocating callback's
full original work repository/object-format/base/paths matches the INITIAL source
P exactly, taken from the first node of the already validated complete native
source-to-selected lineage. Same-issuer source refinements may narrow paths and
retain that original callback; the selected P and all parent attenuation checks
remain intact. A callback matching only a narrower child, conflicting identity,
missing native ancestor or widening refuses; no caller-picked ancestor, broad
subset substitute, extra query, historical rewrite or current grant follows.

### Original source-capture history — K2a2b5

ROOT10693 step63 / Founder139562/559/329 selects accepted TASK11484 appendix
4ee1d1819d3b58ff1350b192d6438fa8ed8bf99ba7cc06abfb0fe315fb4312e1 and cases
f55f71a1db09cf708dbad083cd8c955cd5241cf5f244586eaa3b19d7909c24ae after DESIGN11485.
Unmounted `coding_source_admission_history.read_source_admission_history` consumes
the fixed `coding_source_admission` / `workflow-coding-source-admission@1` audits.
It returns immutable original source/callback captures, explicit INITIAL native P
and native identity links, and allocating full-decision fingerprint. Selection by
authorization_id keeps the complete selected attenuation but never substitutes a
refinement's own mint admission or a mapping capture for the original source P.
The existing five-field native identity API and its free-text learnings/INITIAL
work-radius behavior remain intact through private shared-budget composition.

Closed phase/owner/claim/budget and lossless typed27-task/18-result rows retain
NULL/empty UTF8 bytes, strict signed INTEGER and finite binary64 bits, complete
ordered original parent-to-NULL ancestry, original command and raw native audit
JSON bytes (which need not be canonical). Native immutable task identity/parent,
full result/decision and genuine unique manager_decision session-start links are
checked separately from today's mutable assignment/SID/counters. Allocating C0
and C1 compare exactly25 named load-bearing fields in every source/ancestor row;
both original updated_at/last_heartbeat observations remain independently valid
and retained. Nonallocating captured NULL means absence at that admitted boundary,
never absence of later callbacks or allocating permission. Source claim/invocation
ID/frozen limits are retained facts; actual invocation/Pause and policy proof is
still separate. Zero max_revise_rounds keeps its native meaning.

One coherent caller-owned raw SQLite transaction/lock; SELECT only. Complete
bounded fixed-action audit inventory exposes duplicate/conflicting/foreign owners
without JSON-selection predicates or latest-row fallback. Native typeof and each
row's remaining row/byte ceilings gate payloads BEFORE fetch. Shared8192 fetched
candidates/16384 traversal steps/16MiB byte budget includes conservative decoded
byte copies, with1MiB per value/whole raw audit, source20/P64/setup1024. These are
not total SQLite/process/JSON-object memory limits. Missing required captures
raise coding_source_capture_unavailable; invalid evidence raises category-only
coding_source_history_invalid, without private details or partial return.

This delivers the historical capture consumer only. Callback/source capture
WRITERS, direct mapping-history capture selection, C1=C2 ALL27 writer equality,
full J run_started/source-baseline/replay/native meaningful-writer and heartbeat
bridges, actual invocation/Pause/move/host/terminal/outer cleanup, historical and
current policy/source_rule/base/permission and recovery/cutover readers remain
mandatory BEFORE EVERY producer. No J validator, complete source authorization,
valid-run Boolean, native/public/CLI/TS mount or producer permission is supplied.
F–J/populated/reserved refusal including validate_data=False, create-org I,
document histories, disabled K0/unavailable K1 and protected owners stay unchanged.
FIRST independent FULL review, then same-final-head scoped SOURCE QA, then SAME
ROOT10693; static local all/Source Boundary Lint/Web Lint(Node24), normal hooks,
nonforce push and manager guarded merge remain. Python units RETIRED; integration
INCLUDING callback SKIPPED24342; SQL/parser/product/security/protected/executable
proof UNEXECUTED/FOUNDER-WAIVED139429/430. Root retains K2a2–K6, real-agent/two-org/
override/backup-copy operational acceptance. No live action or trial renewal.


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
THR307seq134 scheduled deletion covers every admitted org; metadata/jobs/age/
containment safeguards and explicit manual dry-run remain. This C3 source
checkpoint grants no live cleanup authority. These source changes have no
behavioral/migration/recovery proof; integration INCLUDING callback SKIPPED24342,
Python units RETIRED. That C3 checkpoint had no COMPLETE writer; current D/E/F
source and independent gates are described above. No live capability follows.

<!-- TASK11932 current-ancestor correction and owner support boundary -->
**Historical TASK11932 checkpoint.** Seq640 removes this producer/worker path;
its unresolved finding and rejected R2 proposal stay history, not a prerequisite
or a runtime cleanup/security PASS. The active contract above controls.

Every selected source ancestor now revalidates its OWN saved operation and
original mapping against current admission before object work and again in the
writer. One bounded iterative traversal shares the native owner and mapping
selection; historical containment never substitutes for current admission.
P1 minted under M1 cannot authorize P2 under newer M2 while P1 is revoked; deeper
stale ancestors also refuse. Original GET/replay keeps original identities and
returns revoked metadata without recapture; an eligible initial source under U
is independent of that revoked lineage.
TASK11927's preflight ownership finding remains OPEN: the current request does
not durably retain worker/process/private-storage obligations through refusal,
cancellation or org close. A concrete private ownership support delta is returned
to ROOT10693 before dependent persistence/lifecycle edits. No cleanup, restart,
security or cancellation guarantee follows from this source correction or lint.
SAME PR1083 remains incomplete OPEN DRAFT and unmerged; independent FULL review
and same-final-head scoped SOURCE QA are still required. Python units RETIRED;
integration INCLUDING callback SKIPPED24342; behavioral/security/cleanup proof
UNEXECUTED/FOUNDER-WAIVED139429/430, never PASS. No live operation follows.
<!-- /TASK11932 current-ancestor correction and owner support boundary -->

<!-- K3b native development: TASK12023 / ROOT10693 step91 -->
## Disabled first-development native ownership (K3b)

The production CodingDispatcher is mounted beside the coding start owner. Actual
owned routing, queue admission and TaskInvocation use the explicit `coding`
owner; source-wait and carrier remain structural. Both global start/callback
readiness and actual coding queue/dispatch admission remain unconditionally
unavailable before SID/host effects. No caller/config readiness Boolean exists.
This source slice does not enable the coding engine or allocate a next stage.

The first-development journal pairs full native rows with session_bound,
launch_reserved, actual handle/PID launch_observed/host_reconciled, own INTEGER
callback_recorded, terminal_recorded and late cleanup_settled observations.
Callback alone cannot complete or advance. Real terminal success still waits
for tracker/reconciliation/task-tail/root-worktree-batch/settle_live closure.
Original captures remain unchanged; unknown teardown retains the same owner.
The fixed native coding_task_heartbeat audit overlays exactly last_heartbeat and
updated_at under the actual queue worker lifetime. Unattributed full-row drift
and unsupported stage/control/cancel/retry/successor mutations refuse.
Complete mixed saved/native/J/schema(data=False)/cold/reopen/recovery/drain
consumers use the same closed first-development reader and full27 task equality.
New native `coding` journal values require compatible code, including on empty J.
F–J DDL/reference bytes and ordinary native owner/permission/host meanings stay.

The consumed development supplement pins original brief/repository/object format/
fixed base/paths and actual run/attempt/task/role/generation. The agent uses its
existing authorized tools; references grant no access or inspection assertion.
Head remains absent until genuine output. The final callback belongs to that
actual task/SID; no unavailable staging/advancement command is advertised.

Independent FULL code review then same-final-head scoped SOURCE QA remain
required. Local all and hosted Source Boundary Lint/Web Lint(Node24) are static
only. Python units RETIRED; integration INCLUDING callback SKIPPED24342. All
behavior/SQL/parser/security/race/rollback/negative/mutation/red-green/repetition/
native-build/E2E/protected/executable/test-authoring proof is UNEXECUTED /
FOUNDER-WAIVED139429/430. K3 control/recovery and K4–K6 plus actual agent/two-org/
override/backup-copy acceptance stay ROOT10693-owned. No live operation follows.
<!-- /K3b native development: TASK12023 / ROOT10693 step91 -->

<!-- K3b four-finding correction: TASK12029 / ROOT10693 step92 -->
The K3b correction separates fresh launch from the original admitted owner's
callback. Once enabled by the still-missing whole-engine prerequisites, a genuine
running owner may submit its first full INTEGER callback after root Pause;
cancellation, foreign identity, stale ownership or revoked current authority
still refuse. Launch hold/capacity/session-list checks are not callback admission.
Exact historical callback replay is read-only, including while readiness is false.
Fresh callbacks and all coding launch entrypoints remain unconditionally disabled.

Bootstrap run_started stays literal event@1 and retains its original authority A.
Native lifecycle event@2/internal-command@2 adds a closed native-admission@1
observation at session_bound and callback_recorded: the actual full authority B
capture/publication, original native invocation/Pause, bounded causal task/move
closure, effective bindings, original limits and launch-only capacity facts.
Independent saved/native/current/cold/schema(data=False)/recovery/drain readers
reconstruct source-time predicates; today's roster or the unchanged bootstrap
capture cannot substitute for original dispatch or callback admission. Unsupported
old native events/heartbeats lacking these proofs refuse without rewriting bytes.

Before the first coding heartbeat, the actual queue worker prepares its real
TaskInvocation. Heartbeat@2 carries that native entry's invocation/claim and native
Pause admission generation; current task SID may still be null. The same identity
survives real SID binding and worker retirement. No synthetic claim is replaced;
ordinary duplicate delivery retains native ownership and teardown semantics.

Native cleanup consumes the actual committed close disposition and final move
release observation. Every development cleanup independently joins original
invocation/claim/task/agent/SID/owner/fingerprint and sticky uncertainty. Null,
foreign, unknown, missing close, failed save/release or retained unknown journals
cannot certify safe settlement. Positive removal requires owned close evidence;
missing-owner gaps retain the original owner and uncertainty. Ordinary close,
deferred ownership and exception ordering remain native; no host protocol or
persistence mechanism is added. F–J/Pause DDL and references remain unchanged.

These are disabled source corrections. Runtime case results remain MISSING;
Python units RETIRED, integration INCLUDING callback SKIPPED24342. Behavioral,
security/race/rollback/negative/mutation/red-green/repetition/native-build/E2E/
protected/executable-QA/test-authoring proof UNEXECUTED/FOUNDER-WAIVED139429/430.
Fresh FULL independent review then same-final-head scoped SOURCE QA return to
ROOT10693; K3 controls/recovery/K4–K6 and operational acceptance remain root-owned.
<!-- /K3b four-finding correction: TASK12029 / ROOT10693 step92 -->


<!-- K3b native service attribution: TASK12034 / ROOT10693 step93 -->
Native lifecycle event@2 service attribution is independently fixed to
`service:coding-dispatcher:` plus the authenticated org. The saved reader requires
both the event column and operation principal to equal that value and derives
the deterministic event identity from it. Recomputed keys/digests/pointers cannot
admit a foreign service, agent principal or another org's dispatcher. Genuine
worker/source evidence.origin and original native claim/admission/callback/cleanup
proofs remain separate and independently checked. Valid dispatcher history keeps
its original meaning; unsupported attribution refuses without rewriting history.
The common saved/native/J/schema(data=False)/cold/reopen/recovery/drain readers
apply this same check. All coding readiness remains false before SID/host effects.
Runtime observations MISSING; Python units RETIRED; integration INCLUDING callback
SKIPPED24342. Behavioral/security/negative/mutation/red-green/repetition/native-build/
E2E/protected/executable-QA/test-authoring proof UNEXECUTED/FOUNDER-WAIVED139429/430.
Fresh FULL independent review then same-final-head scoped SOURCE QA return to
ROOT10693; K3 controls/recovery/K4–K6 and operational acceptance remain root-owned.
<!-- /K3b native service attribution: TASK12034 / ROOT10693 step93 -->


## Coding settings pins at execution boundaries

The disabled engine consumes canonical DB settings through the common current/
pinned resolver. Start@2 binds a strict positive settings generation/digest to its
existing mapping Pin and genuine human publication. E0 and disabled current state
permit no fresh selection. An admitted run keeps its original pin after defaults
change or coding is disabled; current selected-role/native authority still applies.
Corrupt current settings is a refusal, never a fallback to a healthy old pointer.
The new family requires compatible code even though no DDL changes. Source,
installed code, loaded daemon and enabled product acceptance are distinct.


## Disabled settled no-action manager successor — ROOT12568

A NEW genuine manager attempt may follow only a newly recorded complete bound
held callback whose validated effect evidence affirmatively records a pre-action
guard refusal, no command/remote effect and no action revalidation, AND original
successful host, positive source/outer settlement and no unknown native owner.
Partial intent, callback-only, unknown/preparation/close/no-handle evidence,
uncertain action, merged/complete/cancelled work and literal historical held
families cannot enter. Unsupported held subtypes retain their original owner.

The same bound currently eligible manager receives one new task/queued attempt,
same content revision/subject/reference/three original current upstream results,
predecessor identity and all retained contributors. Stage sequence becomes2;
role generation/CAS/events increase independently. Same-principal retry remains1
and original lower cumulative limits remain; no principal reselection, revision,
host retry, source revival, settings remap or budget reset occurs. Current
settings/authority/ancestry/Pause/move/independence/capacity are revalidated in
one caller-owned writer. Exact accepted original-result replay returns the SAME
successor before fresh capacity checks. Full independent target reconstruction
precedes COMMIT; notification follows, and cold recovery rediscovers that queue.
Superseded callbacks cannot settle the current attempt.

Additive event@8/edge@6/edge-command@6/admission@5 and final-scope@4 encode the
new hold/transition and predecessor. Successor-native event/command@1,
uncertainty-only@2 and admission@1 preserve native ownership; final-manager@4
is the actual consumed supplement. Public run@6 retains field inventory and
allows content revision1..3 for a successor; CLI/served schema share its reader.
Selection/output remain literal @2/r1 and @3/r2..3; LOCAL finalize-command
remains @1/r1 and @2/r2..3.
Complete saved/native/current/schema(data=False)/cold/reopen/recovery/drain
readers reject mixed/orphan/fork/incomplete history on ONE cumulative allowance,
including target rereads. Old held/native/r1..r3 bytes and meanings remain.

The new actor independently rechecks its repository/base/head/results/checks and
current entitlements in its OWN live SID. Only that actor may perform its fresh
expected-head non-admin action and independently observe remote MERGED/full
actual commit/aware time BEFORE its own callback. Never reuse a predecessor
intent, reservation, action or receipt. The daemon performs no PR I/O or effect
recovery. Readiness/require_ready stay false; no allocation is executed here.
Source/static delivery is separate from installed/loaded/enabled acceptance.
Native/SQL/workflow/CAS/replay/rollback proof is NOT RUN / PROOF NOT RUN without
an entitled genuine venue. Python units RETIRED; integration INCLUDING callback/
lifespan/SessionTracker SKIPPED24342; missing Swift proof UNEXECUTED /
FOUNDER-EXEMPTED / NONBLOCKING320seq93. Fresh FULL independent reviewer is next;
root separately routes QA. Deferred-native reconciliation,
reassignment and operational/scratch/Founder/two-org/rollback/live acceptance
remain separately bounded root work. No DDL/release/auth/permission/live effect.


## Coding-owned cancellation — ROOT12568 C11/F5/F8

The existing authorized task-cancel route now composes a coding cancellation
writer even while coding admission is disabled. Under the real DB lock it
rediscovers the target/cascade and complete affected coding graphs, retains
terminal tasks, and atomically writes native cancellation rows, original
`task_cancelled` audits, run/attempt fences and closed cancellation events.
A direct coding leaf stops its complete graph; an ordinary ancestor may stop
multiple graphs. A later ordinary ancestor stop also commits when every coding
graph was already cancelled, with or without ordinary siblings. Previously
cancelled graphs stay in the complete closure: their terminal tasks, results,
events, causes and native owners remain intact, with no second fence or control.
For G1 cancelled, then ordinary P cancelled, then higher live Q, retained
coding discovery crosses terminal ordinary dependency intermediates independently
of the live mutation walk. Only authenticated coding dependency paths are
followed below a terminal ordinary row; newly captured rows there must be
terminal and newly discovered graphs must have an earlier genuine cancellation.
Hidden live work refuses rather than expanding legacy mutation semantics;
unrelated ordinary subtrees remain untouched. P and G1 retain their exact rows,
while Q and its normally reachable live ordinary siblings receive their sole
overlay. Multiple retained graphs and a normally reachable fresh G2 compose in
the same operation; only G2 receives a fresh fence.
When no fresh coding graph exists, the distinct task-scoped audit action
`coding_ordinary_cancellation` carries `workflow-coding-ordinary-cancellation@1`:
the original command, exact ordinary task audits, operation digest and earlier
genuine cancellation-event anchors. It changes no existing event family or
`task_cancelled` audit shape/scope. Independent readers close original rows,
causal membership, retained graphs and strict audit ordering, then derive only
the ordinary-row overlay and freshly compare every target before COMMIT.
Retained coding task originals compare at that later operation's independently
reconstructed boundary, including admissible native observations already present
there. The authenticated event chain and heartbeat audit ordering must agree
with the cancellation capture interval; ambiguous or inconsistent order refuses.
Every later native suffix still replays in full to exact current task/SQL equality,
including executor_pid. Reader-derived boundary copies and traversal consume the
same original allowance; no captured-row trust, field exemption, duplicate stop,
native/effect clearance or history rewrite follows.
Reverse coding/causal dependencies are discovered separately from requested
mutation cascade through the complete authenticated start and edge captures.
A point stop of a coding-dependent ordinary ancestor refuses before any task,
audit, ownership, control or tail mutation, including cancelled graphs and deep
ancestry. Ordinary-only point cancellation retains its existing behavior.
Partial/foreign/orphan/mixed or oversized closure refuses;
complete independent target validation shares the original cumulative allowance
and precedes COMMIT. Existing opaque native/prepared controls and worktree/tail
ordering follow outside the writer. Coding controls never fall back to a PID.
Duplicate cancellation allocates no result, task or new control invocation.

`workflow-coding-cancellation-command@1` records the original target/cascade,
actor/rationale/time, complete affected task originals, coding owner IDs and
actual native owner snapshots. `workflow-coding-cancellation-event@1` stores
that closure once with the original audits and previous-event/CAS anchor;
readers derive every resulting task/run/attempt row independently. Closed
`workflow-coding-cancelled-native-event@1` / command@1 admit only original
handle/PID/terminal/outer observations. A cancellation is a business stop,
never native quiescence, remote-effect absence, preparation cleanup or success.
Affirmative owned queued prelaunch history is distinct from an absent handle.
Missing/failed controls, unclosed preparation, reserved-no-handle, stale journals,
unknown effect or missing terminal/outer proof retain the original owner.
They also set the existing conservative root-tail teardown-incomplete disposition,
including genuine prepared/unknown entries with no control/PID. Successful
control return or absent PID is not settlement; coding batches retain ordinary
outer tracker ownership too. Positively settled original owners keep their
existing disposition. Ordinary-only and document draft/F5 cancellation retain
their existing behavior.
Fresh callback/launch/stage/successor/merge admission is fenced. A precommitted
original INTEGER/task/SID callback may drain or replay exactly, read-only,
without advancing. Tasks/runs remain cancelled when later native facts settle;
no finalizer reservation, historical result/subject/contributor/effect evidence,
generation, lower limit, age or uncertainty is reset. No daemon PR I/O occurs.

Saved/current/native/schema(data=False)/cold/reopen/recovery/drain consumers
share the independent closed reader, including complete cross-graph audit/task
closure. Start ancestry and every development/review/QA/final/revision/successor
edge retain original admission predicates and captured bytes. A changed external
row creates an unresolved equality obligation, resolved only after ALL independent
batches close: freshly read current SQL must equal the sole authenticated
cancellation overlay of that exact original row. No partial census, trusted
after-image, mutable-field exemption or reusable validation cache is accepted;
all copying/traversal/reads and post-write reconstruction share the ONE original
aggregate allowance before COMMIT. Public `workflow-coding-run@7` keeps the existing field inventory,
explicit cancelled state and cancelled-or-uncertain current attempt; the CLI
and served route consume the same union. Literal old held/r1-r3/native/document/
then histories retain their own meanings. Deferred native recovery still refuses
`coding_native_recovery_unavailable`; reassignment remains separately bounded.
The current supplements require stopping fresh work on cancellation and retaining
the original callback/effect/native owner, without granting a new control tool.

SOURCE delivery only: readiness remains false, installed source is untouched,
loaded identity UNVERIFIED and enabled acceptance absent. Native/SQL/workflow/
race/causal proof NOT RUN / PROOF NOT RUN without a genuine authorized venue
(THR320seq99). Python units RETIRED; general integration INCLUDING callback/
lifespan/SessionTracker SKIPPED24342; all missing Swift proof UNEXECUTED /
FOUNDER-EXEMPTED / NONBLOCKING320seq93. Static checks prove no workflow behavior.
Founder THR320seq136 authorizes source recovery under sole ROOT12727 without
reviving failed ROOT12568 or predecessors. Genuine four-stage execution,
schema-import/operator/scratch availability are no longer source-merge
prerequisites. Real workflow/Founder persistence/across-team/two-org/backup/
reopen/rollback acceptance remains NOT RUN/open and required before ordinary
work or activation. Historical checkpoint evidence and failures stay attributed
to their original owners/heads. Fresh FULL independent final-head review and
separately root-routed scoped QA remain required; the actual entitled final
manager alone owns expected-head/non-admin guarded merge and independent remote
confirmation in its own live session before callback. No schema/auth/permission/
grant/restart/deploy/live experiment, self-merge or cleanup is authorized here.

## Guided human host coding setup

After root supplies the actual reviewed shipped commit S, use the existing human
host checkout. Retain its previous full HEAD and status before updating; once
original source consumers are idle, `git fetch origin main`, inspect the promised
full S, then `git merge --ff-only S`, stopping on each error. No moving-main
assumption, automatic updater/reset/clean/stash or `.codegraph` traversal is used.
Engineering reconciles a checkout that cannot fast-forward while preserving work.
The source environment must already support read-only preflight; missing packages
are an original-environment-owner hold, never implicit pre-clearance uv sync.

Run `python3 scripts/setup_coding.py --org happyranch` from a genuine human host
terminal. `--org` is required. Strict optional flags are `--runtime-root ABS`
(the existing literal active registered runtime; default read from runtimes.yaml),
`--backup-dir ABS` (exclusive new private destination; default daemon-home/
operator-backups/coding-ORG-UTC-random), `--ui-profile local|production` (local),
and `--import-file ABS` (default daemon-home/coding-ORG-v1-import.json). Source is
the checkout containing the script; daemon home retains its existing configured
selection. No force/yes/skip/resume/rollback/key/revision override exists.

Managed invocation/workspace/native ancestry or unavailable attribution refuses
before imports/credentials/effects. All registered runtimes and loaded/broken/
partial orgs are inspected with raw RO connections, original complete F–J
document/coding/shared-data/history/FK/lifecycle readers, without constructing product
Database/OrgState. Current admitted work, preparations, processes, automatically
consumable pending work, result-processing and publication/profile/move/recovery
conflicts hold. Repeated current exclusion and frozen identities supplement the
maintained human window; historical uncertainty alone needs no closure certificate.

After clearance, confirmation and revalidation, the private recovery directory
is initialized before stop. command-NNN.intent.json is durable before invocation;
command-NNN.json records the observed exit (null if unobserved), launch/closure
uncertainty, stream sizes and bounded digests. Incomplete captures explicitly
identify prefix-only evidence. Lifecycle stderr/arbitrary log bodies remain with
the original log owner; only closed safe stdout is retained. An intent without a
result remains unresolved after interruption/crash or failed persistence; it never
authorizes replay. Receipt failure prevents the next effect. The printed existing
receipt path is recovery evidence, not a complete backup or proof of stop closure.

Under a cleared stopped window the command inventories all affected durable
files/DBs/config/source/environment/UI and writes exclusive 0700/0600 recovery
material. SQLite backup includes committed WAL and checks integrity/FKs; original
WAL/SHM remain. Genuine credentials stay unopened/uncopied in original custody;
filename suspicion alone is not a credential finding. Only admitted exact public
material can follow the C06-B/C07 original-copy path below. Unaccounted aliases,
files, drift or incomplete inventory prevent dependency/UI
and migration writes. `INCOMPLETE.json` marks the receipt-only directory before stop;
`BACKUP-STARTED.json` begins the inventory without replacing pre-stop receipts. Only a full
verified `manifest.json` accepted in this invocation establishes complete backup. `FAILED.json`, when available,
retains partial mapping. Each saved file records its exact original restore path,
identity and hash. Recovery is manually owned, never automatic restore/replay;
pre-J code must not boot J. Restore/rollback acceptance remains NOT RUN.

Locked uv preparation selects the existing Python without downloads; Node24 and
npm ci precede the matching original build_web profile. The actual selected served
dist must equal that checkout's web/dist. The coordinator runs no migration
chain; original controlled start owns atomic coding storage and subsequent
complete readiness validation beside each selected-runtime document foundation. A failed/uncertain step leaves last accepted
layout/command evidence and original recovery owner; no automatic restart.

Start prints the uv wrapper PID; daemon.pid contains its Python child PID.
The coordinator authenticates both against checked process observations, selected
executables/source/home/custody, the pre-start boundary and direct parent/start
relationship. Status must report the actual daemon PID and daemon.port value,
followed by the closed startup observation line described above. Recovery-ready
is freshly required before dependent reads/import/Settings handoff; held/failed/
unknown preserves this original attempt without a second start/import.
Repeated pair/file observations bracket status and serving/import checks; absent,
unrelated, reused or moving identities hold without restart. The saved
receipts/started-processes.json contains only closed identity evidence.

After one start, missing actual boot/loaded-workflow/source/org/UI observations
hold import. Selected memory identity adds twelve already-loaded workflow objects:
five workflow_schema readers/validators, three workflow_coding_schema readers/validators,
and the actual org CodingReleaseImportStore get/import_release/_current/_receipt
methods. Already-loaded module bindings, OrgState validator alias and bound
__func__/class/org agreement must match; no import/reload/new store supplies proof.
Existing origin/co_filename/interpreter, recursive nested-code/source comparison,
closed sorted distinct rows, file/code/acquisition budgets and opening/closing
bookends remain. This is finite current-object identity, not proof of startup
invocation or transitive workflow behavior. Changed identity may need separate
genuine memory qualification; epochs and collection acceptance are untouched.
Health200, disk HEAD and SPA/index equality are insufficient. The original serving/native owner resolves unavailable proof;
setup adds no endpoint, maintenance lock, settlement writer or auth grant.
Finish at Settings → Coding with Admission Disabled. Real workflow/Founder
persistence/scratch/rejection/revision/control/cross-team/two-org/backup/restore/
reopen/rollback acceptance remains open before ordinary coding use. Source delivery
alone is not installed, loaded, operational or enabled acceptance.

SQLite read-only/WAL observation may update SHM reader-coordination metadata;
retaining the original WAL/SHM means no deletion/reset/checkpoint/journal-mode
change, not a byte-immutability claim for SHM. Disposable backup observation must
separate logical/main/WAL preservation from those SQLite-owned readmarks. New
preparation caches live within the private recovery destination, avoiding writes
to unrelated existing uv/npm caches; they are not restoration evidence.

Setup refusals carry an incident/stage and safe bounded class/function-line
references, last completed stage/accepted layout and attempted/uncertain effects.
The sole instruction is `Send this output to engineering_manager`. Main creates
the stdlib in-memory context before the unchanged guard and passes it into the
constructor; partial initialization, parser2/help0, exec/bootstrap/tool failures
and interruption share it. Before permitted private custody, detail is output-only
and no private diagnostic file is claimed. Later a bounded exclusive diagnostic
is saved only after write/readback/fsync in the existing receipts directory.
Original failure/effects survive failed persistence/reporting; a minimal fallback
is attempted once. No arbitrary exception bodies, argv/environment, source/locals,
HTTP/lifecycle/file/credential/receipt bodies are echoed. All-sink failure, kill or
pre-main failure has no delivery guarantee. Constructor temporaries and RO SQLite
SHM prevent a global no-change inference; only positive pre-effect proof permits it.

Serving preparations consume the producer's original five-field LIST
(ordinal/task_id/agent/session_id/expectation_known). Validate the entire list
before classification: only valid empty permits the next original check; valid
nonempty holds exact originals privately, preserving independent observation
errors/pending/null cutoff. Missing/null/int0/bool/object and malformed rows, even
a bad tail after valid rows, refuse without coercion/filter/deduplication. Closed
identity validation precedes projections; missing/malformed identity and every
boot/cutoff/source/runtime/org/error check remain mandatory. Family epoch and
Danze profile errors are independent holds, not fixed by workflow coverage.

Under ROOT12912 / Founder THR320seq177–178, historical job/receipt/launch
uncertainty alone is not a migration-maintenance refusal. Preserve all original
labels, ownership, keys, bytes and adverse evidence without cancel/relabel/settle,
journal clearing, replay or claims of never-ran/quiescence. Account for `_pending`
and `_archive` boundaries only: do not descend/read/copy their members, archives
or OP evidence, move or delete them. They are untouched migration exclusions,
not backup targets. A real present writer involving them still conflicts; unknown
ordinary/partial orgs and actually affected uncopied material still hold.

The human-controlled complete cooperative window begins before constructor
commands/RO effects and any preceding host update. It covers all affected
registered runtimes, normal orgs, shared-daemon stores, source/environment users
and other hosts through backup, preparation, every migration, controlled restart,
import checks and return. Repeat supported current owner/process/preparation/
lifecycle/identity checks before and after effects; retain SQLite reservations.
A one-time census, PID absence or human confirmation alone supplies no exclusion.
Missing present attribution holds. No cross-host lock, hostile same-user or hard
real-time guarantee is claimed; failure retains safe custody for Engineering.

Running entry requires complete trustworthy current observations and one original
stop/intent/result/status/process closure. A failed/uncertain stop in this invocation
never falls into stopped success. Independently selected positive already-stopped/
excluded entry uses existing lifecycle/process/cooperative controls, skips stop
truthfully and queries no stopped HTTP service; it reconstructs no old shutdown.
Fresh distinct wrapper/Python checks remain mandatory after original start.
Current failed views with opening preparations[], null cutoff/no installed
identity are incomplete and hold running entry. Engineering/serving must establish
a supported complete running observation, or the human operator must establish
genuine positive stopped entry with existing controls. No error bypass or memory
endpoint/reset is supplied. Final selected loaded-source/UI acceptance remains
after restart, with all twelve actual selected objects, full D1 list validation,
closed identity/budgets/bookends and human rendering before import.

Stop's shutdown effects precede the full stopped backup; receipt-only evidence
is incomplete. Full affected DB/config/source/environment/UI/shared-daemon recovery
inventory, WAL-inclusive SQLite backup/integrity/FKs and verified copy hashes,
identities, restore mappings and original credential custody precede preparation
or schema mutation. No WAL/SHM reset/checkpoint occurs. Accept the complete manifest
only after verification and exclusive durable publication; retain its identity/
digest and require that accepted custody before later stages. Existence alone is
insufficient. Each original writer0 requires its exact post-check0 and full target
validation before accepted-layout advance. All earlier pre/post-reservation calls
and all three I→J idle calls, including post-DDL, use current maintenance selection;
the nonmigration roster caller retains its original semantics. Full schema/data/
history/types/rowids/bytes/release/FK validation, original FK policies, RO→RW
identity, reservations/offline bookends and rollback remain unchanged. Behavioral,
backup/restore/reopen/rollback and operational acceptance remain NOT RUN/open.

C06-B/C07 public recovery uses the existing guided command, without a prewritten
inputs/roles/selector/mapping ledger. Setup first discovers complete literal
root/parent/object metadata, membership, inode aliases and allowed-link/referent
closure. Separate current-custodian questions bind exact machine membership to
known/mixed/unknown public/package/generated roles, private separation and all
writer/shared-inode-user control. Exceptions name exact paths/subtrees and only
deny admission, including innocently named private material. Unknown/disputed or
conflicting roles refuse unopened. These are genuine present facts, not an
access grant, proof of human knowledge or a historical installation certificate;
no such host knowledge/control is supplied by source delivery. Ordinary nonmatched
shared DB/config/state stays with its existing backup owner; ordinary private
business state is not relabeled public code, and credential exceptions still deny.

Reference custody/public role precedes reference content access. When installed
matches require public inputs, the conversation selects a literal private local
archive directory (default `<backup.parent>/public-origin-inputs/<source-tree>`)
and an exact prior-reviewed full source commit only if OLD locks differ. Setup
produces exact lock/member joins, dependencies and a retained recovery plan;
humans supply no per-file identities or archive mapping JSON. Missing-input
output points to exact reviewed lock URLs/hashes. Only a human later obtains
anonymous exact-lock public archives. Setup adds no download/network client,
cache/home/auth-config scan or installation before complete recovery.

Candidate role AND independent pinned Git blob, whole-lock-verified ordinary
wheel/tar member or supported CPython expectation precede candidate open, even a
header/hash. Names, package prefixes, RECORD, archive membership, UID/nlink or a
generic yes alone never admit. The finite CPython recipe uses independent paired
public source, exact cache tag/filename, optimization0/1/2, current compiler/marshal
configuration and magic/header flags0/1/3, exact source timestamp/size or hash, and
in-memory compile/serialization; the paired actual source is verified first.
It never imports/unmarshals/executes candidates or writes caches. Node selects the
exact longest lock installation path and safe ordinary tar member. Unsupported
wheel/platform/generation, unsafe namespace, mismatch or ambiguity refuses;
reference bytes and rebuilds never replace ORIGINAL recovery copies.

Admitted reads/copies hold no-follow parent/file descriptors with full metadata
and path bookends, byte equality and exclusive private independent copies.
Stable source nlink>1 requires actual complete shared-user control; no link-breaking,
chmod/chown or preparation repair. Record unobserved external aliases honestly;
recovery promises selected content/paths, not their topology. Allowed env/node/raw
CLAUDE→AGENTS links, including lib64, retain every intermediate hop and close
through admitted originals or the positively untouched usable selected external
base interpreter/chain. Other unknown
external dependencies refuse. The existing daemon token stays unopened/uncopied
in original custody, with metadata rechecks and an exact unchanged-owner effect
reason; no blanket credential exception exists. Any affected genuine credential
blocks completeness: return its exact object/custodian/proposed operation and
C07 remover to Engineering before access.

Retain ALL actual OLD source/env/node/dist/feature state and affected shared-daemon/
runtime/ordinary-partial-org DB/config/referents. Existing WAL-consistent SQLite
backup/integrity/FKs/mappings remain; SHM readmarks are not byte-immutable evidence.
Shared cooperative bounds are200000 entries,512MiB archive bytes,1GiB expanded
members,64MiB selected member,8MiB compiler source,1MiB lock input and600s origin
work; no hard RSS/OS-I/O time or hostile-user fence is claimed. Closing verification
recomputes full original membership/coverage, then verifies retained inputs/plan/
copies and exclusively publishes/readbacks the durable manifest. Every later
uv/npm/build/migration/start/import/return gate authenticates complete accepted
OLD recovery custody, including reference/copy membership; after legitimate C07
replacement it does not compare the new environment with OLD contents. Any drift,
missing copy/reference, publication or later failure clears invocation acceptance,
retains the first cause/partial originals/receipts and secondary persistence failure,
and permits zero next effects, with no repair/retry/new key/restore/replay/restart.
Behavior/recovery proof remains NOT RUN/PROOF NOT RUN; setup NOT READY, Coding Disabled.

Effects are recorded before fallible writes/launches and accepted only after
original checks: receipt-only/partial custody, stop/positive closure, partial/full
backup, uv/npm/UI preparation, each migration/last accepted layout, start/pair/
serving and original import file/intent/CLI send/receipt/GET. A returned command
is not stage acceptance; a failed command remains failed despite later effect
observation. Persistence failure prevents continuation and retains original keys,
bytes, intents/results and uncertainty. No automatic restart/restore/resume/replay.
