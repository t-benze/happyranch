# Runtime lifecycle, launch and preparation contracts

> Moved verbatim from the repository-root `AGENTS.md` on 2026-10-30. Sections keep
> their original relative order. Where a later section says it supersedes or
> extends an earlier one, the later section controls; sections marked
> "Historical" are retained evidence, not current instructions.

### Bounded ordinary native launch refusals (THR-315)

Actual non-hold refusals before durable preparation allow at most THREE consecutive
attempts for the same logical task continuation. Existing audit rows
`task_native_launch_boundary` retain the original fingerprint/owner/agent/generation
key, actual reserved SID, invocation and attempt identities, cumulative count and
first safe cause; the latest three bounded payloads suffice without new schema or
journal fields. Reconstruction/redelivery does not reset this count. A genuine
positive-PID native launch callback records a zero-count boundary before fallible
phase persistence. A reserved SID and memory telemetry never prove launch.

Human-team failed-recovery history treats this exact launch/refusal audit action
as informational. It neither selects a result nor grants recovery authority;
selected result/session/worker/team and replay fences still apply, and unrelated
or unknown causal actions still refuse before terminal or parent effects.

An actual refusal after durable preparation fails earlier: the existing outer
settlement makes an unclosed preparation unknown, and unknown owners cannot safely
be adopted for another launch. FAILED with `launch_refused` uses normal failure,
parent enqueue and thread followup; it never clears `failed_cleanup`, unknown
journal/process evidence, retry lineage or worktree guards. Existing smaller host,
provider and recovery ceilings remain. Intentional Pause/team-move deferral,
child/job/capacity/backoff waits, shutdown and stale/current-owner loss consume no
allowance. Current-owner and authentic callback fences precede evidence and are
rechecked inside failure transition; later continuations have separate keys.
Terminal deferred owners are not republished, even when unknown evidence remains.

Each new retry or recovery launch boundary resets only local launch attribution
before fallible initialization; a previous positive PID cannot mask a new refusal.
Original launch/unknown evidence remains intact. Deferred owners are authenticated
read-only before adoption writes, then revalidated under the native writer. Refusals
retain the durable original SID/agent/generation/continuation; no fallback SID counts
for that owner. An unclaimed recovery uses its exact original-SID journal proof and
existing claim exclusions, never ordinary pending/carrier eligibility. Callback,
current-owner, cancellation and intentional hold winners still exclude accounting.

The first preparation cause is kept separately from the final Pause/cleanup error:
bounded stage, exception class/stable Pause code and source locations. A
failed task refresh can additionally retain a safe repo identifier, optional
explicit operation=refresh_ff_only and closed refresh_phase=precheck|fetch|
between_stages|merge|submodule_update. Absent selector retains legacy pull_ff_only
shape/meaning; old dictionaries are unchanged. repository_pull is aggregate.
Only the actual failing child's returned exit is recorded; nonchild precheck/
between-stage failures invent no exit or successful-child diagnostic reuse.
Phase identifies operation, not root/network cause. The cause may also retain a
safe exception class and the existing optional closed advisory
`diagnostic_category`/`diagnostic_rule` with exact Boolean `capture_complete`,
`capture_truncated` and `timed_out` facts. Categories are lock/network/auth/other/
unknown; non-fast-forward remains other. Only complete, untruncated, strictly
decoded whole-stream grammar can select a specific wording label; ambiguity,
unsafe structure, timeout or unavailable/failed capture is unknown. Labels never
prove provenance or historical cause; missing/old exit128 stays unknown. Existing
first causes and historical dictionaries are reused unchanged, and no non-task
cause fields are added. Skipped unsupported/unavailable admission stays a stale-source
warning and creates no refresh-failure cause. No exception messages, output, argv, URLs,
environment or locals enter this evidence. Refresh failures latch sticky cleanup
before attribution, attempt the first cause even after an evidence-save failure,
and propagate only fixed safe errors without captured exception chains. An
audit/storage save failure remains an explicit
persistence limitation, not a durable count or terminal-success claim. No new timer,
service, automatic successor or permission follows. Source/scoped verification is
separate from deployment, running-code identity and operational acceptance.

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
retain their no-effect disposition. Automatic repository refresh admits only ONE
raw byte-exact `+refs/heads/*:refs/remotes/origin/*` origin fetch rule,
ONE checked-branch remote=origin and merge=refs/heads/<same branch>, with exact
usable refs/remotes/origin/<same branch> upstream (slash names included).
Applicable scopes/includes count; duplicate/extra/negative/custom mappings skip.
Configuration identity uses ONE `git --no-lazy-fetch config --includes --null
--show-scope --show-origin --get-regexp SELECTOR` per identity, filtered by Git
before the unchanged complete4096B capture. SELECTOR retains only the exact
checked branch's remote/merge/rebase/mergeoptions (subsection escaped as POSIX
ERE data, case preserved), and whole canonical families: remote, remotes, url, http,
ssh, credential, protocol, promisor, core, extensions, safe, feature, index,
checkout, filter, diff, fsck, attr, splitindex, reftable, fetch, transfer, pack,
repack, gc, maintenance, commitgraph, bundle, clone, pull, merge, rebase,
submodule, rerere, apply, stash, hook, user, author, committer, commit, gpg, i18n.
Every `remotes.*` record participates because `fetch -- origin` can resolve
`origin` as a remote group before remote fallback; Git interprets group values.
The entire ordered selected scope/origin/key/raw-value NUL-triple stream joins
B/L/object-format/exact-upstream-name/transport equality; shadowed duplicates
and provenance are retained. Known scopes, nonempty opaque origins, canonical
selected keys, complete triples and final NUL are required; first LF separates
key/value, distinguishing absent, valueless and explicit-empty. Exact raw
single support values remain mandatory across scopes/includes; scalar last-value
and Git-typed prechecks retain admitted semantic-failure timing. Unrelated
branch/custom size or drift does not consume the selected bound or equality;
relevant-family overflow/incomplete/malformed controls refuse, including after
fetch with retained effects/uncertainty and no merge. This finite inventory is
not universal config safety or a hard bound on Git's own parsing memory. Root
config equality does not snapshot nested repos, hook/filter/include file content,
external state, environment or backend files; cooperative ABA/final-window limits
remain. Credential mechanisms remain unchanged; cooperative refresh exclusion is specified below.
Unsupported config, missing/detached/unborn/unreadable/no-upstream or absent exact
source stays false/stale before mutation, with a fixed safe reason and no newly
latched failed_cleanup solely for that refusal. Existing uncertainty stays sticky.
Admitted pull-state prechecks retain unmerged/MERGE_HEAD and branch-before-pull
rebase clean-tracked checks, ignoring submodule-only dirtiness and allowing
untracked files subject to Git's later collision checks. Without complete original-owner closure, semantic precheck failure
is false/unknown before attribution, without an invented mutating-child exit.
Configured `fetch -- origin` must succeed and settle before one exact post-fetch
origin/refs/heads/<branch> advertisement selects locally available commit U.
Admission U0 is existence only; newly fetched U1 may replace it. Unavailable U2,
deleted/malformed/ambiguous source, probe failure or observed B/L/config/upstream/
transport drift refuses without merge, retry or fallback. FETCH_HEAD and late
tracking contents supply no U provenance. Merge uses literal full U with explicit
`--ff-only`, retaining inherited merge settings and pull.autostash true/false
precedence; absent pull override uses rebase.autostash when resolved rebase is
on, otherwise inherits merge behavior. Git owns stash/apply/conflicts.
After successful merge, effective submodule.recurse requires one supervised
`submodule update --recursive --checkout`, also for equal/local-ahead no-ops.
No init/force/remote/rebase workaround or completion after failed merge.
True requires all required children and final exact original-branch/HEAD oracle
AFTER completion: behind ends U; equal stays L=U; local-ahead preserves EXACT L;
divergence refuses FF. Exit0/any descendant of U alone cannot establish freshness.
Any incomplete possible mutating attempt retains uncertainty, including failed
completion after HEAD advances and later no-start/cancel/probe failure. Equal
HEAD/branch/porcelain or a settled child never proves no effects. Checkout/config/
transport stability remains cooperative despite the participating-runtime lease; last-window/ABA and transactional
correspondence of remote observations remain limitations, with no rollback.
All probes/prechecks/children/final checks and siblings share ONE original30-second
batch deadline including scan and executor-queue delay; exhausted/unattempted
siblings stay false. Read-only control output is privately bounded4096B and must
be complete/validated, with lazy object fetching disabled. Child stdout is
discarded; each mutating child's private stderr retains
at most4096 bytes plus bounded read scratch, draining/discarding excess while the
direct child runs. Nonblocking capture permits at most two4096-byte exit-tail
reads, never waiting for descendant EOF or a reader thread. Overflow/incomplete/
failed capture cannot select a specific label. Actual child rc0 remains eligible despite
truncated advisory capture; a post-start read exception remains failure even
if the child later exits0. Direct-child status and actual contained-tree settlement are both required, without a hard
real-time guarantee over OS creation/reap or native persistence. Thread/dream/
wake/Todo await off-loop refresh under the SAME native preparation owner,
joining its worker before cancellation propagates and settlement runs. Timeout
never certifies process-tree quiescence; existing unknown owners remain sticky.
Repository preparation now has a cooperative common-Git-directory descriptor
lease. Literal path/device/inode and stable no-follow lock metadata are pinned;
release closes the descriptor without unlinking or deleting stale locks. Aliases
must converge on the pinned physical directory or refuse. Each batch discovers
physical identities before acquiring leases, refuses duplicate physical entries
with a separate false result for that name, and acquires distinct resources in
(device,inode) order, also used by operator admission. Expected identities are
revalidated inside each lease; aliases never receive a new timeout. The lease lasts through
admission, final validation, actual worker return and evidence completion. All
siblings, queue/lease/host admission, probes and Git commands share the original
30-second batch. Current path/config/transport are revalidated between phases.
Manual Git, hooks, other hosts and arbitrary same-UID writers do not cooperate;
last-window/ABA, OS cleanup and persistence timing limits remain.

Every Git child, including admission/control probes, uses the existing configured
healthy HostSessionSupervisor and opaque backend lifecycle. Missing guaranteed
containment is a pre-child stale refusal. There is no uncontained fallback,
synthetic executor session or TaskInvocation.started observation for Git. Each
child launches behind a stdin gate (`/bin/sh` reads one byte, then execs Git with
the same PID and /dev/null stdin) so a sub-second Git exit cannot precede the
backend's scope/limit verification; the body releases it only after `started`
and capture setup, and EOF without the byte exits125 without running Git. The
original Popen consumes its status; actual status and a genuine Boolean-quiescent
host receipt with no survivors/cleanup error are separate facts. Capture stays
private/bounded4096B with draining and at most two exit-tail reads. An admitted
unsettled probe cannot become a harmless unsupported-configuration skip. Async
cancellation stops admission and joins the real worker under its original OrgState
lifetime before evidence completion and lease release. Completion runs in the
same joined off-loop executor mechanism after the actual batch worker returns.
Repository custody precedes binding, root/Pause, move and short DB arbitration.
Worker and completion acquisition, including native owner rechecks, binding/root
gate-map lookups and failure attribution, uses the original remaining deadline;
expired finishing tails acquire only immediately available gates. Closure digest
construction and revalidation share the admitted writer's coherent transaction
and finite SQL/work allowance, without a fresh lock-wait budget. After successful
COMMIT, the original gates still protect receipt/sequence and selected-obligation
publication; clock expiry cannot turn committed closure into a no-write claim.
Failure latches local uncertainty before bounded persistence admission; unavailable
attribution retains UNKNOWN without claiming a persisted update. Human reconcile refuses
known active workers/completion before resource admission and uses immediate
resource/application-lease refusal on the serving loop, including tracker/root
registry mutexes before lookup in their SAME persistent maps. Registry mutexes
release before the returned gates; the already-owned short transaction never
reenters ordinary registry/writer admission. It revalidates the live owner,
binding, root/Pause row, evidence set and physical pins under the acquired gates.
Only already-closed original obligations use the settlement predicate branch.
Successful reconciliation COMMIT retains its actual committed value even if
later context/publication fails; uncertainty requires original-key readback.

Version1 repository_preparation events append to existing audit rows with the
original org/task/agent/reserved SID/invocation/refusal attempt/boot/repository
binding; threads retain their actual INTEGER invocation and other producers their
native occurrence/preparation identity. Contiguous bounded phases distinguish
intent, attempted creation, actual start, consumed status, host finish, worker
return and outer closure. Only finite command kinds/probe codes, PID/start-pin and
opaque receipt references, safe cause and retained-effect dispositions persist.
Closure concerns owned native preparation only: it proves neither absence of
remote/external Git effects nor replay safety. No argv, remote URLs, stderr,
exception text/chains, config/environment or backend
token is public or persisted. Oversized, duplicate, missing or mismatched evidence
refuses rather than truncating into positive proof. Each phase enforces its own
required/forbidden facts; command/probe/request identity remains bound to original
intent and any later supplied handle/PID/start pin must equal the original start.
Historical omitted consumed/finished pins remain supported. Status, capture and
receipt facts cannot contradict the original consumed result. Valid opened/intent/
attempted/started/consumed prefixes and truthful no-start or uncertain finishes
remain appendable and incomplete, without a fabricated closed suffix. Saved
successful reconciliation requires the original explicit closed event as well as
the complete coherent proof. Earlier materialized/scratch
paths remain retained effects; closed original write intervals, native receipts,
worker/outer closure and current ownership must all agree. A retained directory
alone proves neither a live writer nor successful cleanup.

A complete already-persisted proof can close only its selected NEW obligation
before the ordinary Git failure handler. Current original owner, loaded/source/boot
agreement, root/Pause/move/task/session eligibility, cooperative resource exclusion
and the exact state digest are rechecked under the existing writer. Missing proof,
escaping preparation interval, lost owner or persistence failure keeps sticky
failed_cleanup/UNKNOWN before fallible attribution. No Boolean reset, retry-policy
relaxation, historical overlay, journal rewrite or foreign reservation release is
provided. Existing historical optional cause forms remain supported. In particular,
TASK12738/QA12724 remain unresolved without their unavailable original proof.

The permitted read is `happyranch tasks preparation-recovery TASK --org ORG --json`
(GET /api/v1/orgs/{slug}/tasks/{task_id}/preparation-recovery). Human-only
`happyranch tasks reconcile-preparation TASK --org ORG --from-file ABS --json`
uses POST at the corresponding /reconcile-preparation path and the unchanged
_require_human/bearer/native trust boundary. The closed <=16KiB version1 body has
only operation_key, expected_state_digest, invocation_id and attempt_id. Duplicate
or unknown keys refuse; callers provide no PID, proof, force, retry, successor or
exception. The server derives the original tuple. Missing proof returns a retained
first cause, missing predicates and committed=false; absent original owners are
never reconstructed. Refusal audit persistence is distinct from settlement.

Expected refusals are existing401/403, unknown-task404, invalid-request422,
409 stale_state/active_owner/held/conflicting_history/original_owner_unavailable/
unresolved_preparation and503 storage/control/capability/evidence unavailable.
One key binds one exact body. After transport/COMMIT ambiguity, inspect with
`--operation-key ORIGINAL_KEY` for strict saved readback; do not automatically
reapply or choose a new key. Native process/receipt evidence remains private.
Source delivery grants no live inspect/apply against historical ownership,
deployment/restart, historical clearance, retry allocation or E2E acceptance.
Behavioral/native/proof results are NOT RUN unless separately attributed; Python
units remain RETIRED and general integration INCLUDING callback SKIPPED24342.

Team-move GET/POST requests register their service task and original attached
org native-user lifetime before native submission. An admitted apply/recover
handoff shares that original lease with its loop-owned runner, including after
shutdown fences fresh org admission. Request disconnect, repeated cancellation
or a cancelled bridge never establishes native return. Service close owns one
shielded join: fence new requests, join admitted request handoffs, then snapshot,
cancel and join operation runners. Uncertain submission/return or restricted
context close retains strong owners/handles and refuses remove/swap/DB close.
Restricted DB acquisition and both inner inspection connections register their
actual handles to the SAME service/native task before fallible validation, even
when no context is returned. Each original helper attempts close once; affine
inspection connections close on their acquiring thread. Positive original close
alone removes that handle. Failed cleanup retains the owner and safe close code
beside the primary failure in diagnostic readback; native return, a saved terminal
receipt and a later successful observation never establish handle retirement.
A service-owned redelivery child shares the original org lifetime through native
generation claim, real loop queue put and native exact acknowledgement/failure.
Its parent joins it across repeated cancellation before returning or closing;
uncertain native return retains both queue reservation and service close barrier.
Synchronous generation publishers keep their original entry and error behavior.
Dynamic attachment registers its strong child and native submission ticket before
cancellable work, captures the actual loaded/unpublished DB and native close
outcome, and joins each native interval through repeated cancellation. Loop queue,
session, host and thread references are wired before native profile validation
publishes into the original map within the original profile lease interval.
Returned notification rediscovery keeps its existing owner and waits for actual
loop task/thread queue puts before retirement. Their actual outcomes are retained
before submission. An uncertain task put keeps the original attachment owner and
bounded reservation even after native return or caught publication error; a known
callback failure retires that put with its original error. Cancelled bridges and
map absence prove no retirement, and no automatic uncertainty clearance is added.
Published attachments survive later errors;
unknown submission/return/constructor close retains its original handle and blocks
shutdown/removal/swap, without a second speculative close.
All shared-map iterations use finite captures. The state mutex covers only map
membership and in-memory reader bookkeeping, never I/O/DB/profile acquisition,
worker join or await. Native profile/metrics readers and ordinary request
attachments retain DB lifetime independently of runner admission. The selected
thread/task/job event responses end their endpoint reader before transport and
capture their original attachment for each lazy DB replay or job poll. EventBus
registration/replay/cleanup stay on the original bus, without a DB reader during
idle waits or sends. A detached/replaced attachment refuses later DB reads rather
than resolving its slug again. Fully materialized task attachments and audit
bodies need no transport reader; audit retains its original budget/identity fence
without later SQL. Detach/close
atomically refuse active readers and fence new captures; runtime swap closes
capture admission at its final preflight before registry effects, then renews the
original map/profile identity on replacement. Shutdown fences native insertion and
joins attachment children before host/DB close. Source/static evidence establishes
no behavioral readiness, hard completion bound or new launch permission.

Synchronous startup retains every positively loaded handle before selectors or
reconciliation can fail. With both coordinator bindings retained, complete
startup classification/recovery and dependency synchronization precede eligible
selector initialization. Classification is renewed after selectors and before
returning the unpublished state, including a selector publication that leaves
an ordinary prepared journal or native uncertainty without raising. Dynamic add
prepares its unpublished dependencies before selectors, then recaptures and
validates them again under the final attachment leases before runnable insertion.
A narrow startup-held inventory preserves original objects and diagnostics while
ordinary lookup and producer snapshots expose only
runnable attachments. Profile readers, identity/drain preflights and shutdown
include both inventories. Dynamic add refuses a retained held slug and never
constructs a replacement to clear it. An idle local DB close does not settle an
external ordinary writer or its durable UNKNOWN evidence.
Startup holds an org when its native writer history has any non-`settled` record
or an ordinary publication journal is active. The single exception is the
THR320seq477 historical sweep record (`historical_disk_sweep`): `unknown`,
publisher `disk_sweep`, empty agents AND teams, and absent from the mounted
native owner's live writers. The nightly sweep only changes workspace scratch and
worktrees, never startup's publication inputs. The org load check and the
profile coordinator's `reconcile_startup` classification apply the same
predicate. An unavailable native owner keeps every record blocking; the record
stays UNKNOWN and still blocks team move.

Whole-runtime construction records its actual candidate on the original caller's
owner before later fallible work. Both normal startup and runtime replacement
preserve the first error through unwind, execute independently owed safe closes
once, and retain exact failed/unknown handles and actual secondary errors. A
partial old close or failed loaded candidate leaves replacement incomplete:
`attachment_retiring` continues through route finally, `get_org`/producer snapshots
expose no retired originals or failed candidate, and stale native objects refuse
admission. No rollback, second constructor or retry of a failed close is implied.
Successful adoption transfers the full held inventory and binds profile capture
and hold callbacks to the adopted actual state. Startup recovery/enqueue stays
synchronous, before serving, and visits runnable orgs only.

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
Only the real Uvicorn Server adapter signals it after successful ASGI/listener
startup returns with started true and no requested exit. Early port/PID files,
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

**Startup refusal safety (THR308seq133/127, ROOT12801).** Private Database-owned
startup reads reuse scalar candidate catalogues in discovery and exact getters
only within one exact Database/connection/invocation and stable coherent epochs.
Fresh projections retain optional authenticated immutable scalar anchors for
exclusion. An anchored exact/read/refusal failure quarantines its root before
bounded logging; pending also excludes later same-root attempts. Missing attribution fails task recovery
globally. Readable inert admissions still use the shipping completion/parser,
including legacy fallback, with zero classification writes/fence; standing B below may close independently authenticated terminal-root candidates after the loop. Absent admission proof alone
is no new eligibility rule. The existing refusal writer independently authenticates
current ownership, and only fresh committed `refused` owns its existing tails.
No early diagnostic append/replacement writer is added; local exclusion/logs are
not durable fencing. Actual writer phases and owned read cleanup determine clean
recovery. Failed cleanup, unresolved raised COMMIT, replaced/unusable connection
or unexpected transaction abort startup before later effects and suppress the
entire daemon finally worktree batch. Caller read transactions remain untouched;
the full sweep rejects them before Pause/workflow work. Earlier genuine commits
remain intact. General readers/writers and generic coherent_read_view retain
their contracts. Outside autocommit connection/data_version/total_changes/
main.schema_version bookends enclose a pinned main-table snapshot under the RLock;
only main alone or main plus positively empty connection-local TEMP is eligible.
Workflow integrity validation can initialize that empty TEMP. Its actual integer
temp.schema_version joins both bookends and retained epochs; absent TEMP stays
absent without probing it. Every attachment/unknown database shape and every
nonempty TEMP, including nonshadowing objects, disables sharing. TEMP create/drop
ending empty changes the epoch, and a moved/nonempty closure discards speculative
output before the existing clean uncached fallback. Default isolation, legacy
autocommit and read_uncommitted=0 guards remain required;
no hit/target/absence/anchor escapes before clean matching closure. Movement or
unavailable tokens discard the whole memo; only clean ownership/cleanup permits
one fresh uncached coherent fallback. Caller transactions bypass sharing.
Canonical/result/binding/owner checks and shipping parsing remain fresh.
THR308seq280 / ROOT14157 replaces the historical ROOT12914 graph retention
and 8-root/4096-row/8MiB/16-position machinery. First chronological use fully
fetches and ordinarily decodes ALL truthy root audit payloads, including heavy
histories and unrelated LAST siblings. Reduce through raw selected keys to
zero/one/many cardinalities and unique INTEGER IDs; release the full root graph
and reduction frame before the next root. Each chronological projection reads
at most two selected rows fresh by id AND root and reapplies predicates/closed
checks. Point failures escape benign absence adapters. Unsupported descriptors
use the original full path at their own ordinal. Fresh auth/results/parser and
independent writer checks remain; no arbitrary I/O-fault trace equivalence is
claimed. Only scalar plans/catalogues and existing scalar outputs survive across
roots. Invalidation releases all catalogue evidence; last exact use (including
skips) and exit release remaining ownership. Raw plans authorize no result.
Memory includes actual maps/wrappers, initial attempt TEXT, scalar outputs,
one root's raw/decoded/reducer peak, current selected buffers, full results and
SQLite/native allocations; no hard RSS/time or old retained-byte bound follows.
Six cases/counts/lifetimes/timings/speed are NOT RUN, sensitivity PROOF NOT RUN;
ROOT14157 explicitly accepts the source-delivery gap. Historical ROOT12914 and
ROOT12801 receipts retain their own attribution. Independent review and scoped
QA remain mandatory; units RETIRED, integration SKIPPED24342.

### Synchronous startup observations

THR308seq280 records monotonic fixed-phase success/incomplete timings at actual
owners, with no new readiness/cleanup action. Normal startup begins after
maintenance exclusion, before daemon-home initialization; it excludes process
bootstrap before that origin. Under THR308seq353/354, two additive
`startup_timing` phases measure that earlier work: `launcher` runs from the
first timestamp in `scripts/daemon.sh start` to entry into the daemon package,
and `imports` runs from that package entry to the unchanged backend origin
(including module imports, logging setup and argument parsing). The package
captures wall and monotonic clocks before heavy imports, including under
`python -m runtime.daemon` and its canonical-module reimport. The launcher
passes `HAPPYRANCH_LAUNCHER_START_EPOCH` only to its child: Bash's
`EPOCHREALTIME` when available, otherwise portable `date +%s` with second
precision (up to one second of overstatement). No uv preflight is removed.
`launcher` is a cross-process wall-clock difference; `imports` and the
existing backend observations use monotonic time. Wall-clock adjustments
between launcher and package entry can distort the launcher measurement.
Missing, malformed, nonfinite, nonpositive or future launcher timestamps log
`phase=launcher outcome=unavailable seconds=None` without refusing startup;
missing process clocks similarly make their spans unavailable.

`org_load` covers preflight through return or
failed unpublished close. `org_database`, `org_workflow_schema` (install/recover
plus first locked validation), and `org_workflow_revalidation` are nested load
children; move/Pause/ownership/roster/coding work stays load residual.
`authority_discovery` includes coherent views/bookends/materialization/fallback;
`authority_exact` includes chronological current reads/results/parser/refusal,
tails and skips. `thread_recovery` covers Branch6 delivery validation, all
cutover/reply/exchange recovery, orphan reaping COMMIT and move runnable tokens.
`sweep` ends after its actual finally disposition, including unsafe suppression.
`build_state` is inclusive of loads/recovery/publication. `lifespan_pre_yield`
ends just before yield; scheduled work is not thereby complete. Pre-yield failure
adds no teardown that the original path did not own.

Only `_DiagnosticServer.startup` returning from real same-app ASGI/listener
startup with started true and no server/lifespan exit request records
`normal_startup outcome=serving`. Port/PID files and the earlier listening log
are pre-server markers. `server_startup` stops at that check before best-effort
signals; `diagnostic_signals` is separate. Failures retain reached spans and
`normal_startup outcome=incomplete`, without a completed partition. A serving
partition's existing backend fields sum disjoint `org_load`,
`authority_discovery`, `authority_exact`, `thread_recovery` and
`remaining_startup` (backend total less those four). Their meanings and values,
and `normal_startup outcome=serving seconds`, remain backend-only. The
`startup_partition outcome=serving seconds` dictionary additionally contains
`launcher`, `imports` and `launcher_to_serving`; the last is the aggregate
launcher + imports + backend seconds, or `None` if an earlier span is
unavailable. Do not sum that aggregate back into the partition. Missing
pre-backend spans do not suppress the existing backend partition. Inclusive
build/sweep/schema/lifespan/server times are NEVER added to that partition.
Caught org-load failures still contribute their elapsed work, not an assertion
that every org attached successfully. Missing timing suppresses a completed
partition. These are cooperative monotonic observations, not hard deadlines.

One aggregate authority counter record separates discovery/exact full-history
fetch attempts/completions/rows and truthy decodes from selected-row work,
materializations/reuse/rebuilds/fallbacks and fixed invalidation reasons,
current/peak scalar entries and exact classifications/result-list calls/rows.
Existing final startup_authority_counts also records fixed metadata reasons:
unsupported databases, nonempty TEMP, isolation, autocommit, read_uncommitted,
invalid integers and metadata failure, plus TEMP-schema epoch movement.
Anchor-result reads are counted separately; no generic result-JSON count is
claimed. Fixed labels/outcomes/counts only: no identities, payloads, SQL or
exception dumps, no per-attempt log stream or extra evidence scans. Clock/log
failure cannot replace startup/cleanup outcomes. Later normal operator startup
may observe success metrics; it cannot prove all six failure/equivalence cases
or establish historical/under10s improvement. Source delivery authorizes no
restart/deployment, a checkpoint or history deletion. Standing B (THR308seq366/367) separately authorizes source for terminal-root inert closure as described below.

**Standing B startup closure (THR308seq366/367).** On every boot, after the
exact loop and within its open Database-owned reader, eligible authenticated
inert terminal-root attempts close to admitted/`inert_closed`/NULL. The exact
CHECK/guard initializer rebuild preserves retained bytes/FKs; compatible code is
required at the schema boundary and after B runs. Live/escalated, fenced/failed,
obligation-bearing, owner-present/unavailable or changed-result candidates skip.
At most 256 rows/transaction, SAVEPOINT per row, a separate strict inert-close
audit and read-back before COMMIT add no task/Q/thread/deletion effect. Only
confirmed clean pre-COMMIT rollback plus post-writer checks continue; uncertain
BEGIN/COMMIT, cleanup/SAVEPOINT or connection outcomes abort as sticky unsafe
(`inert_close_transaction` for writer custody failure), suppressing the entire
finally worktree batch. Earlier commits survive; an uncertain batch is unknown.
The existing aggregate adds `inert_closed`, `inert_close_skipped:<category>` and
`inert_close_batches`, without per-row logs. Activation uses the merge commit;
a separately authorized operator reads first/second-boot authority_discovery/
authority_exact spans, these counters and startup_partition. Second boot should
find no newly closable rows absent new eligibility. No latency/savings or under10s
claim is proven. See [the strict contract](authority-policy.md#standing-startup-inert-closure-thr-308-seq366367-task-14648).

**Task-scoped audit lookup (THR-278).** Generic `Database` initialization installs
the nonunique, nonpartial `idx_audit_log_task_id` on `audit_log(task_id)` for fresh
and existing databases. Literal task/config/thread/artifact scope values, audit
IDs, payload decoding and ascending-ID results remain unchanged. The index is
included in independent complete legacy v1 schema references and actual
observed-only v2 schema diagnostics. Failed-claim residue and malformed-attempt
startup fences retain their existing behavior. Source delivery, installed
schema, live startup timing and reviewer settings are separate evidence;
installation and compatible-code rollback guidance is in the runtime guide.
The initializer also installs and authenticates explicit nonunique/nonpartial
`idx_audit_log_action ON audit_log(action)`, one ascending BINARY key with an
implicit rowid tail. Wrong same-name definitions refuse without repair. Current
independent complete references include it; historical preimages stay unchanged.
Strict older readers may refuse; compatible-reader rollback preserves index/history.

- **Atomic pending parent-wake admission.** Receipt-owned cleanup tails and startup recovery can produce the same bounded parent wake concurrently. Every shipping parent-wake producer and the startup delegated/blocked-job dedupe paths use `TaskQueue.enqueue_if_absent`, which claims an ephemeral per-task reservation under the queue lock, runs generation-aware/DB-aware publication with that lock released, then clears the reservation on success, refusal, or failure. A concurrent bounded producer loses to either the reservation or an actual pending item. Dequeue atomically ends pending suppression so a later legitimate transition can wake the task again; unrelated FIFO ordering and ordinary non-coalescing enqueue are unchanged. Tests join every cleanup worker they create before another sweep, queue assertions, or shared `jobs_runner` restoration because durable settlement is not cleanup completion.
- **Graceful daemon shutdown preserves blocked-on-job recovery.** Lifespan teardown permanently stops `TaskQueue` consumers and producer admission before signalling in-flight jobs. A genuinely owned signal request preserves the first actual timeout/cap/task/shutdown/Stop cause; a natural pre-signal exit keeps its result. The original job owner commits its row, terminal audit, producer observation and strict Pause disposition together; persistence failure remains unknown, never a fabricated terminal result. The stopped queue refuses `enqueue`, `put_nowait`, and `enqueue_if_absent` (`False`), so a terminal-job hook cannot claim the waiter during teardown. The task stays `in_progress(blocked_on_job)` with its session unchanged; startup rediscovers only genuine all-terminal waits through Branch 3 / the lifespan scan plus pending-item dedupe, subject to cancellation, Pause and move admission. Unproven running jobs remain running/unknown. Resume headers and non-JSON `happyranch jobs show` expose the stored reason and exit code; the existing `task_resumed_from_jobs.job_outcomes` status values remain unchanged.


## Sequential ownership startup/recovery hold (THR336 U3a)

OrgState attaches the holding-only `SequentialDispatcher` beside the historical
Coding dispatcher and mirrors it to its orchestrator and DB. This constructs
no storage, writer or engine and installs/upgrades nothing. A selected
`seq_source_waiting`, `seq_carrier` or `seq_visit` is exposed only with that
same-org attachment; without it, classification is `reconciliation_required`.
Both selections remain held. Startup/recovery routing returns owned without
legacy recovery, enqueue or sequential dispatch. Queue admission refuses and
heartbeat/PID publication cannot write through ordinary task paths. Enqueue
emits the ownership hold warning; Pause discovery keeps sequential work held
and marks downstream execution evidence incomplete. Existing non-legacy zombie,
host, session, result-action and parent gates remain in force.

U3a supplies no producer, admission, launch, callback/transition engine or caller
return. No startup schema upgrade, real-org install, live migration, deploy,
restart or activation is authorized. See [the U3a source boundary](reusable-workflows.md#u3a-ownership-union-and-holding-attachment).


## Sequential original-owner settlement consumer (U3d-1)

A genuine selected visit callback retains its exact canonical summary wrapper in
its original INTEGER native result; callback acceptance sets no sequential result
quartet/output. Original host terminal then positive original outer settlement
COMMIT precede fresh authority capture and explicit original run/visit/result
advance. Terminal's request identity, attempt0, observed original handle/backend,
Boolean quiescence, cleanup clean/term/kill, empty survivors and absent cleanup
error follow Coding's line-parallel safe predicate. U3d-2 still owns all sequential
producers of those inputs; queue admission remains False. The settle-finally
sequential call follows Coding with the same exception/add_note preservation.
Other owners are a no-op.

Evidence carries bounded JSON proof with NULL SQL native identity columns and PK
reads only; imported check_evidence_size bounds each body to16384 bytes. Original
callback discovery uses the existing indexed task/agent LIMIT2, then exact PK;
no latest/foreign adoption. Safe receipt alone is distinct from completed native
callback and positive original outer cleanup/close/release. Absent or unsafe proof
never advances; F-1 option(i) leaves sequential active/running after session ended
meaning stalled, with cancellation as exit, without fabricating held/result/event.
Restart has no native recovery authorization. A retained carrier is evidence only.

Fresh sequential accept, transition and terminal return share one atomic writer;
notification is after COMMIT. Original-source stable transition/return keys are
purpose-separated. CAS=c before accept, c for transition, c+1 for terminal return;
event sequences c+2/c+3, final CAS c+2. Original replay recovers transition.prior_cas,
never later current CAS, and performs no replacement allocation or partial-pair
repair. Cancellation precedes both paths. Native/terminal/outer commits survive a
later sequential refusal. Engine scopes remain separate; no redundant validator.
Current authorized capability/access witnesses are unavailable U5 dependencies;
unsigned carrier claims confer none. Existing native-summary visibility applies.
No actual callback/terminal/outer/advance or V3 behavioral acceptance is claimed.


## Sequential launch ownership and backout (U3d-2)

Source ports follow queue admission → native prepare → native claim → U3c
session_bound → launch commitment → observed original handle → current PID.
Source-outer evidence is a deterministic PK point read and must be positive;
U3e alone will produce it. No U3s venue-start call or settings publication is
attached here, and defaults remain Disabled. Source delivery authorizes no live
DB/install/migration, activation, restart, deployment or V3 execution.

The actual queue invocation has owner `sequential`, its original SID and host
request key. Current-authority ownership surrounds the same native synchronous
commit; no await or filesystem/network operation occurs inside its writer.
U3c owns running/SID changes. Selected-run validation closes the final native
journal save at possible_launch; the Coding schema validator stays in place for
Coding. Original request and handle feed the unchanged U3d-1 terminal/outer
consumer. Positive host paths remain UNVERIFIED (V3).

Sequential retries are refused. Deferred continuation raises
`sequential_native_recovery_unavailable`; startup recovery holds source, carrier
and visits. No missing handle/PID or ended SID proves cleanup. Heartbeat emits
neither native task stamps nor audit rows.

SB-8 adds only `sequential` to the persisted Pause owner vocabulary. Older code
cannot validate a root retaining that owner: **roll back only with no nonterminal sequential Pause entries**. This consequence becomes relevant only after U3e
and separately authorized live admission; it is not permission to clear journals
or infer settlement. Existing U3d-1 settlement hunks remain byte-preserved.
