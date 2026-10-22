# Daily product E2E health

This independently authored standalone lane implements the accepted E02/E03/E06
case design (`TASK-10476`, SHA256
`bc4c4c9d50a73ce45085ed6b5edcae572cdf75e8e26e3b583e6b7728037fd564`).
The bounded Pause, E04 and E05 increments below follow that original baseline closure.
Source and design acceptance do not establish behavioral PASS. Actual disposable
runner results, authoring proof, independent review/QA and check enforcement are
separate delivery obligations.

Fresh Product E2E keeps the daily `0 19 * * *` schedule (19:00 UTC /
03:00 Asia/Shanghai) on immutable default-branch `github.sha`. Founder
THR291seq277 authorizes engineering_manager under sole linked ROOT TASK-12445
(resolving superseded TASK-12281/TASK-12262) to use the existing main-only `workflow_dispatch`
on demand after concrete reviewed corrections, without per-run approval or a
nightly wait. Reconcile active owners/runs first; no blind same-source retry loop.
Manual admission requires actual `refs/heads/main`, default branch `main`, and
matching repository/workflow/run/source identity. Historical seq247's ONE-run
allocation to TASK-11897 and its receipts remain history. Baseline56 variants
EACH Python3.12/3.14 plus TERM3.12/INT3.14 retain
exactly four execution artifacts and the original bounds. `Daily Product E2E
Health` remains outside merge checks and reports the actual event, run/attempt,
SHA and checkout tree; manual receipts are never labeled scheduled.
There is no local E2E alias, PR/push/release/workflow_call/repository_dispatch
entry, automatic retry, extra schedule or SLA. Admission, lint and scoped QA
success do not establish product execution success. The root owns each actual
dispatch after full independent review/scoped QA, both final-head lints, normal
hooks/nonforce publication, guarded merge and an exact-main two-lint receipt.

`run.daily_admission` validates genuine schedule payloads with the exact cron;
manual payloads carry their actual main ref and no invented schedule. Both
launchers and aggregate agree on event, cron (null for manual), repository,
workflow ref/SHA, run ID/attempt, source ref/SHA and independently read checkout
tree. Missing or cross-event/run/source receipt fields fail health, including
cancellation wrapper versus inner launcher. The admission environment/payload
checks are not a hostile-runner security boundary. GitHub supplies the
[run/workflow metadata](https://docs.github.com/en/actions/reference/workflows-and-actions/variables)
and [dispatch event identity](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch).

Founder THR291seq171/172 selects exactly56 variants EACH on Python3.12/3.14
and TERM3.12/INT3.14 launcher diagnostics. Product Python support remains unchanged. Exactly four execution directories,
`e2e-3.12`, `e2e-3.14`, `e2e-cancel-TERM`, `e2e-cancel-INT`, feed
`Daily Product E2E Health`, outside required merge checks. Scheduled runs38482572306 and38644465643 failed at their own sources;
corrected behavior remains pending the existing daily lane. Historical TASK10532/run38035241095 on `afb46cbb6` retains both
3.12/3.14 eight-case/23-variant baselines and TERM/INT cleanup; it is not fresh
head/daily evidence. Engineering_manager owns ordinary task/thread triage;
there are no retries, new notifications or promised response SLA.

Every cell builds the real Web on Node24 and uses
Playwright 1.58.0/Chromium with an exact hash-locked dependency closure. This
lane does not change Web dependencies or reuse the screenshot harness's mock
server. `browser-events.json` retains response/status/page-error observations
and scoped partial response-selection/body diagnostics;
raw headers, bootstrap bodies, HAR, browser storage and raw trace archives are
never uploaded. PNGs contain only the rendered synthetic task state.

The closed temporary root owns HOME, runtime, two publicly initialized orgs,
registry, browser, sockets, provider plans and credentials. Source/hash-bound
adjacent wrappers call the actual CLI. The unchanged external stubs and guard
are reused as executable assets; neither `integration_parent` nor
`guard.install()`/sitecustomize is loaded. All retained pytest integration,
platform-marked and callback selections still require their original parent.
No old unit test is imported, collected, copied or executed.

Both Node24 setup steps enable npm caching keyed by `web/package-lock.json`.
Each launcher step discovers the runner npm prefix with a checked
`npm config get cache` assignment and passes only its absolute `_cacache` path
as `E2E_NPM_DOWNLOAD_STORE`; cancellation inherits it to the same installer.
After admission/source/runner checks, the launcher copies public download bytes
into private `root/cache/npm/_cacache` and always runs locked
`npm ci --cache PRIVATE_PREFIX`. An absent bridge or missing store is a cold
ordinary install, with unchanged registry, scripts, audit and lock/integrity
validation. Stale/corrupt content remains subject to npm integrity handling;
missing downloads still require the registry and installation can fail or time out.

Only after npm ci succeeds does the launcher copy private `_cacache` bytes back
to the declared runner store. These are ordinary copies into independent inodes;
aliased paths, symlinks, hardlinked files, special entries and transfer errors
fail visibly. This validation is not a hostile filesystem race guarantee.
Live npm logs/config/credentials, `_npx`, `node_modules`, browser, daemon and
provider state are never transferred. The closed child environment and private
root cleanup remain; the ephemeral runner/action owns exported download bytes
outside that root. Transfers consume the original720s action/900s outer bounds;
the npm command retains240s. Export never runs in failure/interruption cleanup.

The pinned setup-node action archives its discovered **whole runner npm prefix**,
not selectively `_cacache`; only download bytes are exported from the private
root. Its post-save requires job success and skips exact key hits, so a partial
exact-hit entry is not refreshed. Concurrent jobs and failed/cancelled jobs do
not guarantee warming. Cache presence never skips installation or determines
PASS. Native warm/cold consumption, corrupt-store behavior, timing savings and
cleanup proof are NOT RUN; this offers no outage immunity or measured speedup.

The controller holds genuine provider processes while running the real CLI.
The daemon consumes their callbacks after normal process exit. A transparent
relay observes actual upstream HTTP200 and a newly reopened committed result
before dropping one downstream response. The next CLI invocation retries the
same payload bytes. SQL is read-only and assertions use fixed scenario data,
never product serializers as expected-value generators.

Lifecycle final-state readback uses a normal browser refresh after durable
completion, waiting for real detail and Recall responses in the same browser
session. The shipping detail/Recall queries do not poll while mounted; this
case does not establish automatic live-view refresh.

The accepted TASK11803 amendment (TASK11788 manager step4) selects a finite
caller-owned cohort. Before arm, each caller declares immutable scenario facts,
current boot/origin/document, action/destination and endpoint/query scopes.
Actual main-frame GET Requests starting after arm and accepted destination/commit
own their original Response, one private body read and successful requestfinished.
Known prearm objects never become current. New-document observations require the
unredirected native Request/commit. Before commit, a same-document event is
allowed only when exact source=target=live URL, the original nonnull unredirected
source Request/document sequence is still current, no navigation was pending at
arm, and no invalid/aborted state exists. Both source redirect checks remain
required. Any later pending navigation must be the selected exact unredirected
target Request. This exception establishes no reached/commit/pending lineage or
API readiness; ordinary initial goto without source lineage remains supported.
After commit, same-document initialization must retain the NEW Request lineage
and exact target. Changed query/hash, foreign URL, superseding document or
departure/return refuses permanently, without rearm/retry or budget renewal.
Reused endpoints are allowed: a delayed producer starting a new read in the same unchanged declared
state can qualify. This proves temporal fresh-original-read coverage; initiating
React hook/click causation and complete future producer closure remain unproved.

Readiness/content/absence assertions run while admission is OPEN. Provisional
body loans stay private and memoized, without accepted records or case marks.
The explicit caller checkpoint freezes a nonempty set at every endpoint. Every
admitted member must independently match predeclared scenario expectations and
agree with all peers, including unknown fields and array order. Failed, non200,
conflicting, stale, redirected, incomplete or late members fail the entire group.
After checkpoint, matching starts refuse through settlement. No first200,
request count, quiet period or networkidle certifies closure. Earliest-start
representatives become public only after all siblings' body/finish joins, owned
task settlement and final synchronous guard/disposal/seal. Inner scope exit does
not certify, detach or restore time. The original earliest absolute page/case/run
bound covers action, readiness, bodies, finish, cleanup and guarded consumption.
Local task cancellation is not server settlement; uncertainty poisons reuse and
retains the first error and partial diagnostics under the existing containment.
Each owned body completion retrieves its exception or cancellation, latches the
first observed failure and wakes the group even while another member is pending.
Guards and abort reconcile already-done tasks before queued callbacks run. Local
abort detaches active wake callbacks and requests cancellation only for owned
pending tasks. A consume-only callback records any eventual outcome without
waking acceptance or driving native transport. This is best effort: if the
original loop never advances again, pending tasks cannot settle locally; no
cleanup budget or server-settlement proof follows. Secondary body/cleanup errors
retain type-only partial diagnostics and cannot replace the first observed cause.
Activity after disposal is outside this finite dispatcher-observed guarantee.

All three roots sites (IDs1/18/19: open_task/e07_terminal/Pause held) use ONE
endpoint scope with seven literal queries: exactly limit=["50"] plus exactly
one status=[pending/in_progress/escalated/completed/failed/cancelled/superseded].
Strict parsing retains blanks/multiplicity/UTF8/fragment refusal. Unfiltered,
cursor/filter/extra-key queries cannot satisfy a member. Ordinary and attention
escalated requests are indistinguishable; each admitted duplicate independently
validates, with no hook-owner inference. Every query needs an original body
while OPEN before DOM/checkpoint/freeze; every admitted body AND successful
finish must settle before whole-group seal. Missing queries or a failed/late
member refuse all. No count/first200/quiet shortcut supplies closure.
Each root has unique identity, null parent and requested status, cursor:null;
the independently authored target occurs exactly once only in its authored
status partition, and zero times elsewhere. Empty non-target partitions are
valid. Complete type-sensitive images compare only within the same query,
retaining raw array order and unknown fields. The sealed exact-query accessor
selects that query's earliest original; unqualified multistatus access refuses.
Recall requires exactly tree=["true"]; detail/jobs require an empty query;
all reject fragments and use fixed parse-error categories without query text.
Response admission repeats the query and original Request identity checks.
Literal harness-only R/D/C/J shapes and independently supplied scenario facts
reject equal-wrong bodies, malformed UTF8/JSON, duplicate keys and nonfinite JSON.
Private comparison copies omit only independently validated UTC observation
stamps at D.pause/R.tasks[i].pause (including identical blocker stamps) and
D.task.child_wait/R.tasks[i].child_wait. Delegated-parent child_wait identity,
phase, legal state and known waiting_children/pending_children requirements stay
mandatory; an undeclared delegated row refuses stamp comparison. Null is required
elsewhere. Unknown fields, durable timestamps,
heartbeat/results/audit/work_status and all raw/exported bodies remain intact.
No cross-transition comparison or recursive timestamp stripping is used. E07's
three terminal tree selections now use the actual complete same-phase tree URL
and snapshot, preserving recursive equality, children and independent freeze,
HTTP-index, cancellation, control and ledger assertions.

The27-site source census classifies exactly16 shared transport sites
1/4/5/6/7/8/10/11/15/16/18/19/22/23/24/25, including all seven original public-body
sites8/10/15/16/18/22/24 and nine private-body sites. Six DOM-only sites2/3
(open_task detail/Recall),20/21 (Pause root-link),26/27 (Pause terminal) retain
exact URL, scenario briefs/state/outcomes, child identity/order, localized
controls and old/alien absence checks within the removed pairs' original bounds.
At those six sites we deliberately relinquish fresh HTTP, method/query/status,
original Response/body/finish ownership and failed/conflicting/duplicate
background-request coverage. DOM content is visible navigation evidence. Later
reload/API/DB evidence does not prove the removed click transport. Cached-return
passive target-org path200 provenance remains weaker. For the same populated
alpha-beta-alpha cached return ONLY, Founder THR291seq290 approves moving the
earlier fresh-list checkpoint and list-before-wait breadcrumb. One fixed early
E=min(entry effective_end,entry+stored clipped page cap) covers both selector
actions, initial own/alien list URL/href/render assertions and cached detail/
Recall/provenance; the original cached-entry inner end can only tighten E.
Release both before the unchanged31000ms wait ON DETAIL, retaining original
remaining(32)>=31 admission and enclosing absolute owners. After the wait, arm
original ID1 once before the ordinary breadcrumb, require seven-query R/list
proof and whole seal, then separate original fresh D+C(tree=true)/full own/alien
DOM/Recall/screenshot/mark. Inclusive browser-cache-revisit timing remains.
Later evidence does not certify the relinquished earlier HTTP checkpoint.
Existing observations cannot prove complete prewait cache-update age: earlier
refreshes may finish during the wait, leaving fresh/coalesced keys. No settled
flag or success guarantee is inferred; missing/ineligible new originals refuse
within original R/DC, and such refusal is not automatically a product regression.
No extra wait/retry/reload/eviction/context/fetch/state/clock/product seam or time
expansion is introduced; other callers keep their original order and caps.
No accepted HTTP record is fabricated for a DOM site.

Native sites9/12/13/14/17 (Reject, thread list/detail/messages, Cancel) retain
their original predicates/passive sinks and weaker start/query/ownership limits;
they receive no shared-cohort credit. Pause's unchanged overview URL-family sink
latches forbidden-overview-read on arrival and remains live through all roots
body/finish settlement. A final synchronous empty assertion precedes its local
detach/seal. Arrival after completed disposal remains unobserved. Dashboard503
and original native error/cleanup evidence remain distinct.

Only ID18 exports every admitted original roots body through the existing
browser-events artifact. Its group/checkpoint/source/interpreter/boot/origin/
native-document/arm/freeze/seal/member reference joins views[2] and list-reload.
Arm-time source document sequence, verified source URL, pending-navigation and
redirect facts stay separate from target lineage; native Request objects are
never serialized. Same-document trace steps retain the live document and pending
navigation sequences. ID18 independently requires source sequence before arm,
exact source/target URLs and no pending navigation at arm; precommit steps must
retain that source sequence before/after the one selected Request, while
postcommit steps must retain its NEW sequence with no pending navigation.
Missing attribution refuses, including historical traces lacking these fields.
aggregate.validate_e07 independently reconstructs the seven literal queries,
all original body/finish outcomes, per-query full images and earliest cancelled
representative, binding genuine P/W allocation/cancellation/DB/audit/session
facts. It compares the complete selected public body/reference in existing
copies, without importing maker query/image predicates, trusting success labels,
unioning bodies or stitching groups. Raw SHA identifies private raw bytes only,
not sanitized JSON. Missing/truncated/unparseable/redacted required content,
including unknown fields, refuses; four artifacts/export bounds are unchanged.
100MiB suffix fit, repaired GREEN, flake and sensitivity remain unproved.

These are source contracts, not runtime outcomes. A01–A16 remain the accepted
four-answer case record in TASK11803; task/PR evidence attributes any isolated
actual-callable synthetic controls separately from browser behavior. Historical
run39069788195 remains daily FAIL (four completed marks/E02 partial/51 incomplete
per version); TERM/INT prove launcher diagnostics only. Corrected behavior, all
browser/DOM equivalence, sanitation/screenshots, timing/roughly100MiB fit and
protected12 mutations/repetitions remain UNEXECUTED or unproved. General
integration INCLUDING actual callback is SKIPPED24342; Python units RETIRED.
No behavioral/test-authoring/full-reset PASS follows from source/static checks.
Daily19UTC,56 EACH3.12/3.14,TERM3.12/INT3.14,four artifacts and every original
scenario/command/containment/cleanup/evidence bound remain unchanged. Abrupt R,
wider E04 cap/Stop and other held work remain held; no new execution is granted.

Terminal stop admits only the healthy pinned owner/standard loop with positive
existing work. It invokes synchronous context exit once under one absolute S.
Expiry is sticky and leaves exactly one queued call_soon successor, preserving
cooperative enforcement across advancing dispatcher/transport/asyncgen phases.
Finally deactivates before canceling its handle, with no second exit/loop drive.
Healthy pre-S completion can succeed; expiry always fails, even after normal
return. Unsupported/poisoned/exhausted states refuse before exit. Scheduler
registration failure and nonadvancing synchronous work have no proven local
phase-spanning/hard-preemption bound. Local stop does not prove process absence;
only the unchanged outer Owned receipts establish it.

Controller selects the first actual error/traceback with an immutable in-memory
winner before diagnostic time, legacy failure accumulation or I/O. `record_error`
is latch-only. Separate first-observed and context-capture categories never
replace that winner. Failed capture first attempts the original `[]` invalidation;
its actual capture/invalidation exception precedence and readiness are unchanged.
After the action catch's `record_error("controller-action", exc)` completes,
main tries the SAME earlier `_stage_failure_categories()` call before poison;
its local finally retains the SAME `if controller.browser` conditional
`controller.browser.poison(exc)` attempt, including if the ENTIRE writer
invocation escapes. Earlier safe publication can precede poison's secondary
throw or consumption of remaining time. Caller finally preserves an ATTEMPT,
not poison or publication completion. The outer close/evidence finally, phase
bookkeeping and selected raw-primary final raise remain unchanged: when reached,
that raise propagates the first actually recorded winner and its traceback.
Exactly THREE existing owner-only nonrecursive main publisher calls remain:
action catch, close-finalization and post-evidence. Off-owner/reentry skips
without locks or waits. A protected-body caught metadata/I/O/alarm failure makes
publication refusal sticky IF the existing handler/reset assignments complete;
entry/handler/reset escape leaves unreached publication/active/latch assignments
UNKNOWN. No active/sticky reset, pending repair or retry is promised.
Each optional operation checks the SAME effective end=min(global, case, local,
observation, inherited), including after an absorbed alarm. Earlier I/O can
consume original time; no extra slack/reserve is added. Constructor failure,
interruption in the legacy record_error tail, arbitrary later/repeated
interruption, crash and hard OS-I/O remain limits on publication, poison and
final propagation. An earlier file/frozen projection can be stale; unavailable,
null or stale metadata proves neither latest/complete cause nor local poison
execution, and earns no context/readiness/case/cleanup/health credit.
Controller requests observer stop, and atomically stages small complete sanitized
marks/trace before optional native teardown and bulky evidence. Resource attempts independently retain
secondary failures. Observer stop is not termination: join uses only positive
existing work (at most1s), and full stable prefix snapshots report cutoff,
liveness/error/join state and partial status. Nonterminal cancellation_hold keeps
its observer live. Exhausted work grants no join/flush/relay/daemon/native reserve.
Full valid redaction context is mandatory; unavailable context withholds unsafe
output. Genuine E07 witness bytes/digests, sibling evidence and final assertions
remain. No partial/missing data becomes absence or PASS.

The independent populated
alpha-beta-alpha cache case never reloads during org switching. The return
first verifies the cached rendered alpha state with its prior real HTTP200
provenance. It then navigates to the list, waits 31s for the shipping 30s query
staleness window, and revisits the detail through its real link, requiring new
detail/Recall HTTP200 and the same render/isolation assertions. No query cache,
clock or product state is changed. This explicit extra phase retains both the
populated-cache and fresh-response oracles; the case's original 30s target may
be exceeded, while the complete run retains its 600s target/900s hard cap.
Assertions cover alien task content/links in both list and detail. Assertions use
the shipping English heading `Recall tree` and raw status label `completed`.
The workflow explicitly installs its selected Python before resolving the
absolute interpreter; installation remains inside the cleanup-inclusive clock.

The outer watcher registers before launch, acts as a Linux subreaper, observes
only its descendants and exact admitted provider PID/start identities, and
tracks production-created per-session cgroups/units where present. It uses
pidfd signaling after identity checks, covers providers outside daemon PGID,
and requires process/unit/cgroup/port/socket/data absence. It does not provision
systemd or force a production backend: the test-owned provider descendant calls
`setsid()` before its launch witness so out-of-daemon-PGID coverage is exercised.
This retains the production-created containment membership. The watcher tolerates
only vanished proc entries during observation; other observation errors retain
full tracebacks and fail cleanup. It never sweeps unrelated host processes.
Missing backend capability fails
setup; unavailable/unknown cleanup fails the run. Catchable cancellation runs
cleanup; SIGKILL/runner loss cannot produce a successful receipt.

The separate `cancellation.py` supervisor runs Python3.12/SIGTERM and
Python3.14/SIGINT diagnostics on disposable Ubuntu. Each uses the same frozen
setup, real daemon, locked browser, public-created task and actual CLI callback.
After the upstream200 is committed, the deterministic provider remains held.
The launcher publishes its exact PID/start, active controller/daemon/provider,
owned identities, native units/cgroups and two listeners. The supervisor checks
those identities and sends the real signal through a pidfd to the launcher.
The launcher must record receipt and exit1 with FAIL. Only the separate
cancellation diagnostic may pass, after complete owned closure. A fake signal
receipt, already-exited provider, setup failure, missing identity or unknown
cleanup fails. Both diagnostics are required by the aggregate alongside the
unchanged eight-case/23-variant baseline; they do not count as product cases or
product-cancellation coverage. Full setup/action/interruption/cleanup/evidence
phase times and reaped child exit statuses are retained. Actual signal and
new daily baseline evidence remain PENDING until observed from the schedule.

Target is 600s, action cutoff720s, cleanup reserve60s, evidence reserve120s,
hard cap900s including dependency installation and build. Baseline scenarios
(bootstrap, baseline, Pause/restart, E04, E05, controller close/evidence together) have
at most480s; each other command and cancellation scenarios retain240s.
The launcher computes `min(command entry + cap, original start + 720)` once
before spawn, passes that exact end to the controller and uses it for its own
watchdog. No restart, case or phase resets the original clock. Exhausted
allowances refuse launch; actions cannot borrow teardown or evidence time.

Cleanup begins on normal completion, failure or catchable interruption. Its
end is `min(cleanup entry + 60, original start + 780, original start + 900)`.
Unchanged Owned process/unit/group closure is FIRST. Then one independent safe
category read, valid-only evidence staging, existing port metadata/individual
probes and one identity-checked owned-root removal/literal absence observation
share that SAME original allowance. Ordinary failures do not skip remaining
eligible attempts; Owned timeout or original expiry stops cleanup. Each observed
port is probed once in existing order, with its1s timeout clipped to remaining
time. Missing port metadata is unknown, never a successful empty list. Sanitized
text/PNG evidence is staged in memory before removing its source. Staging,
closure and cleanup incompleteness are sticky: later absence or successful
read-only recollection cannot clear them. Both cleanup.ok and timing.cleanup.ok
remain false when required work failed or is incomplete. Literal root absence
supplies no process/unit/group absence. Serial per-unit query ceilings may
exceed60s; timeout fails honestly and retains observed ownership, without a
claim of absence. If cleanup fails, export may read partial diagnostics but
performs no more teardown or actions.

The first nonempty readable daemon token remains the redaction context through
partial data removal; later collection cannot erase it or replace sanitized
staging with raw text. If no trustworthy context was captured (missing, empty
or unreadable token), raw diagnostics and the detailed receipt are withheld.
Known-safe staging is retained where available; bounded incomplete metadata
has variants={} and reports FAIL, even when owned-root cleanup succeeds. This
failure path does not establish complete evidence or runtime sanitation safety.

Export has its own end `min(export entry + 120, original start + 900)`.
It writes a metadata-only FAIL first, then sanitized detailed artifacts and
the final receipt, keeping the last second of that same allowance for a bounded
failure receipt. Each metadata-only receipt copies timing with cleanup.ok=false
alongside its false cleanup flag; it cannot certify incomplete detail as cleanup
acceptance. This copy does not rewrite an actually complete in-memory cleanup
outcome. Serialization, sanitation, required-detail checks and the pending-file
write precede final replacement. After staging, the launcher retires SIGALRM
process-wide (including queued delivery), disarms it and checks the SAME original
export deadline minus one second before the atomic replacement. A thread mask
alone would not cover alarm delivery through a surviving cleanup watcher.
Successful replacement is the terminal commit: no subsequent deadline check,
alarm restoration or failure rewrite can reclassify it. Precommit failure leaves
an actually published initial FAIL when replacement does not commit; failed
initial persistence is never claimed as a remote receipt. The existing bounded
FAIL fallback restores the alarm and checks the original export end before
replacement; success does not depend on that write. Export failure is sticky.
Recollection is read-only, never teardown, and cannot erase prior
staging/closure/cleanup failure. Incomplete detail still publishes variants={},
with both cleanup flags false, even if safe partial members were written.
Known serialization, missing-data or precommit expiry failures cannot admit PASS.
No fresh120s is granted after900. Fractional `setitimer` alarms enforce positive
remainders without integer truncation. Timing receipts retain original ends,
command caps/entry/end, cleanup/export allowance/elapsed/outcome and failure
phase; both daily baseline and cancellation consumers require complete timing.
Elapsed export/total values are sampled at final receipt construction. Staging
runs under the export timer; final replacement is cooperatively admitted after
retiring that timer. An admitted OS call can overrun its check or be interrupted
by a crash; there is no postcommit rollback, hard OS-I/O timing, fsync or crash
durability guarantee. Hosted upload shares the remaining outer900s/15-minute job
envelope; a terminal launcher commit does not establish upload or actual product
acceptance.

The optional `result.json.failure_categories` is a closed primitive-only channel,
independent of raw detail sanitation. It is omitted without an admitted lowercase
40-hex source SHA. Otherwise its exact keys are `schema,source_sha,controller,launcher`;
schema is non-Boolean integer1. Controller has exactly `availability,snapshot`:
`available` requires a fully validated private envelope, `unavailable` requires null.
The private envelope at literal `ROOT/coord/failure-categories.json` has exactly
`schema,source_sha,first_observed,context_capture`, with the same admitted SHA.
Each slot is null or exactly `{stage,error,at}`. `at` is finite non-Boolean int/float
in inclusive0..9007199254740991. Stages are exactly phase/bounded/restore/context/
action/teardown/evidence/browser/other; unknown caller spellings map to other.
Ordered built-in subclasses map UnicodeError→unicode, TimeoutError→timeout,
KeyboardInterrupt/SystemExit/GeneratorExit/InterruptedError→interruption,
AssertionError→assertion, ValueError→value, OSError→io, RuntimeError→runtime,
otherwise other. No group/cause/context traversal or text-based signal inference.
Launcher has exactly seven first-failure slots: action/owner/diagnostic_read/
collection/port/root/export. Their stages are action; teardown for owner/read/
port/root; evidence for collection/export. No exception text/name/args/traceback,
path/URL/token/header/content or command output enters this projection.

The writer traverses literal no-follow directory components, pins parent/root/
coord identities and requires owned exact0700 root/coord. Final files must be
regular owned exact0600/nlink1; no links/aliases/special files are followed.
The sole `.failure-categories.pending` sibling must be literally absent before
exclusive no-follow creation; any collision refuses without truncation/removal.
At most4096 strict UTF8 bytes are atomically replaced descriptor-relatively after
identity/deadline checks. Publication failure disables later attempts locally;
no cleanup/retry/fsync/crash-durability or racing-rename guarantee is added.
Reader never reads pending; any pending entry makes observation unavailable and
incomplete. It reads at most4097 bytes ONCE and admits at most4096 with complete
unchanged name/descriptor/parent identity and size. Strict UTF8/JSON rejects BOM,
duplicate/extra/missing keys at every object, trailing content, nonfinite values,
wrong types/enums/schema/source and parse/read failures. Rejected bytes/reasons
are never exported. Safe literal final ENOENT is unavailable without read error;
unsafe/unfinished/malformed input is unavailable plus sticky diagnostic-read
incompleteness. Missing root/coord after an earlier failure/removal gives no new
absence-as-error claim; unexpected required-parent loss is incomplete.

The launcher validates and freezes the primitive controller snapshot before root
removal and never rereads category files during export. Available means only a
validated published snapshot, never latest/complete observation, initiating cause,
successful capture or readiness. A stale final may survive a later failed write;
null means only no observation in that snapshot. Missing diagnostics alone do
not fail otherwise complete evidence. No category supplies variants, mandatory
members, health or cleanup credit. Raw detail still requires unchanged complete
context sanitation; old/native failures and unknown causes stay unchanged.

The workflow starts the monotonic clock before checkout/tool setup. No budget exhaustion becomes a
skip. Every enumerated variant must be present with PASS, and failed/missing
matrix jobs fail the stable `Daily Product E2E Health` aggregate. The aggregate's
bounded controls prove refusal of missing cells, skips, failed/missing variants, wrong
SHA, failed jobs and failed cleanup; these controls are not product evidence.

This launcher correction is source delivery only. The illustrative future
combined375s estimate has105s nominal slack under480 only when global remaining
time permits; no runtime fit, signal/timer/cleanup behavior or E04/E05 assertion
validation has been observed. The baseline/Pause42 variants remain at their original boundaries; the E04 prefix below adds six. New deliberate
defects, RED/restoration/GREEN and repetitions remain UNEXECUTED/unauthorized.

## Accepted case record

All cases use existing public boundaries with **no test-only product seam**.
The nearest retired route/unit coverage used seeded internal state; the retained
general integration suite is SKIPPED under THR-243 seq42. Web mocked/jsdom
coverage cannot establish live daemon or populated browser isolation.

| Case and implementation | Observable contract | Credible regression/control | Distinct coverage gap |
| --- | --- | --- | --- |
| 1 E02, `scenarios.journey`, `Store.finished`, `Browser.completed` | Public create → M1 delegate → W complete → natural M2 done; browser, public detail/Recall, result rows and ordered audit agree | Suppressed child enqueue leaves no W/final child; missing rendered outcome independently fails | Existing journey lacks built-browser final proof; ordinary browser task creation does not exist |
| 2 E06, `journey`/`deny` | Literal active replay200 retains exact row; terminal replay409 task_not_active | Duplicate insertion fails exact result count; reordered terminal gate fails response | Genuine held-process and terminal transport |
| 3 E06, `Relay`, `prove_committed`, `journey(lost=True)` | Real upstream200 + reopened committed row, lost response/nonzero CLI, identical retry200, one delegation | Duplicate append fails immutable row/effect counts; missing committed proof refuses drop | Actual post-commit loss, not pre-send disconnect or automatic CLI retry |
| 4 E06, `journey` | Changed active payload200 retains first delegate; changed terminal409 | Overwriting original decision fails row and downstream child oracle | Ordinary empty/static family, not v2/draft conflict semantics |
| 5 E03, `identity_denials` | Stale/fabricated known-slot409 session_mismatch; no-slot409 unknown_session; unknown-task404; no writes; genuine M2 succeeds | Wrong acknowledgement fails exact response; write-before-denial fails durable snapshot | Natural stale S1 under live S2, not unknown-task-only denial |
| 6 E03, `bearer`, owned positive control | Four exact GET/POST401 denials, genuine GET/POST200 and completed control | Omitted router auth fails exact denial/no-write oracle | Real shipping routes, not a minimal secured mock application |
| 7 E03, `collisions` | Public org-local IDs actually collide; both crossed real sessions409; both genuine completions | Wrong org dispatch fails response and dual-store snapshot | Crossed identity rejection, not concurrent success alone |
| 8 E03, `Browser.open_task`/`completed` | Same populated context alpha-beta-alpha, target HTTP, brief/outcome/Recall/status/href and no alien task content | Missing slug query key can show cached alien state and fails render/response | Real built browser cache and real org storage, not mocked providers |

`manifest.json` enumerates every variant. `observations.json` records commands,
HTTP bodies/status, launch identity, public/durable snapshots and relay evidence.
`result.json` records source, tools, phases, variants and cleanup. An incomplete
or missing receipt is failure, including a scenario exception before reporting.

Authoring evidence remains pending until actual attributable RED/control,
byte-exact restoration, final GREEN and usual five isolated/five sibling
repetitions are recorded for these cases on bounded disposable runs. No
protected auth/identity/product mutation is authorized by this harness. The
parent must resolve any proof-control authority gap before dependent execution;
an unrelated setup failure is never attributable RED. Keep evidence in this
case record/PR rather than inventing a second proof framework.

### TASK-12643 accepted recovery case map (ROOT12610 / THR291)

Parent step9 accepts TASK12634 revision2 section2 and this complete finite map
(SHA256465bf5a03ab64d73e2d4677d35c9d58d40421d683a68a491366caa5ae8c3943a),
after independent DESIGN ONLY PASS12637. The record below is preserved from that
accepted proposal, with implementation status clarified; it adds no executable
tests, manifest cases, proof framework or execution allocation.

Native39765942783 remains HealthFAIL47, all56 EACH3.12/3.14 UNKNOWN, zero recorded
PASS/no proved UNREACHED, scenario exit1 then cleanup/export ValueError, original
assertion/URL/traceback unavailable; npm succeeded. TERM3.12/INT3.14 authentic PASS
is diagnostic-only; JOB5234 completed0 is COLLECTION ONLY. Parent step6 owns
FOUR-digest/ALL32-member355569B authentication and full ledgers, reused without
collection/download rerun. Historical39758401569 EACH53PASS/interruptedE07/two
UNREACHED/E05relay/signals,39713142724 npm240s/inference-only cause, dashboard503/
ownership uncertainty, PR1168 admin-helper deviation and terminal12574/12445/12281
remain historical. Product/reset acceptance INCOMPLETE. Protected12, RED/restoration/
GREEN/repetitions remain unauthorized; abruptR/E04cap/persistentStop HELD;100MiB
suffix fit unproved. No assertion is removed, skipped or weakened.

All following expectations are design requirements, NOT new executed cases. Every item is NOT RUN; intended-failure/restored-pass evidence is PROOF NOT RUN because this maker authorizes source/lint work only, with no product execution, controls, mutation harness or case campaign. Historical native successes are attribution/context only. No keeper is deleted, skipped or weakened. No production/runtime export, flag, injection hook or test-only seam is required (answer4=NO for EVERY row). Shared answer blocks below explicitly name their full membership; table entries supply answers1–3 and each additional layer's distinct risk.

Membership read from BOTH assessed ledgers, with EACH member UNKNOWN in EACH Python3.12/3.14 cell:

| Group | Complete case membership |
| --- | --- |
| L1 | `E02.lifecycle-browser` |
| L2 | `E06.literal-active`, `E06.literal-terminal`, `E06.committed-response-lost`, `E06.changed-active`, `E06.changed-terminal` |
| L3 | `E03.stale`, `E03.fabricated`, `E03.no-slot`, `E03.unknown-task`, `E03.bearer-get-absent`, `E03.bearer-get-wrong`, `E03.bearer-post-absent`, `E03.bearer-post-wrong`, `E03.bearer-get-valid`, `E03.bearer-post-valid`, `E03.cross-alpha-beta`, `E03.cross-beta-alpha`, `E03.genuine-alpha`, `E03.genuine-beta`, `E03.browser-alpha`, `E03.browser-beta`, `E03.browser-alpha-return` |
| L4 | `Pause.chain-drain`, `Pause.child-pause-denied`, `Pause.child-resume-denied`, `Pause.pause-idempotent`, `Pause.browser-pausing-en`, `Pause.browser-pausing-zh`, `Pause.overview-readonly`, `Pause.same-org-unheld`, `Pause.drained-before-restart`, `Pause.drained-restart`, `Pause.browser-paused-en`, `Pause.browser-paused-zh`, `Pause.resume-idempotent`, `Pause.stale-resume`, `Pause.restart-final`, `Pause.terminal-denied`, `Pause.resume-during-drain`, `Pause.drain-final`, `Pause.exact-closure` |
| L5 | `E04.approve-exit0`, `E04.review-required-refused`, `E04.completed-exit7`, `E04.failed-timeout`, `E04.two-jobs-A-first`, `E04.two-jobs-B-first` |
| L6 | `E05.compose-race`, `E05.reply-natural`, `E05.self-dispatch-followup`, `E05.consumed-token`, `E05.nonparticipant-task-send` |
| L7 | `E07.cancel-cascade-browser`, `E07.cancel-terminal-unchanged`, `E07.cancelled-late-cli` |

Counts1+5+17+19+6+5+3=56. Names above are a finite impact map, not a replacement result ledger.

| Cases / validator or changed path | Answer1: precondition/action → independent observable contract / level | Answer2: credible regression | Answer3: keeper, gap and distinct risk |
| --- | --- | --- | --- |
| L1–L3, all named members | Existing public lifecycle/callback/replay/auth/org-switch journeys → retain their real CLI/API/browser/DB/audit assertions and valid sanitized marks; category-only fallback never supplies case success. Product E2E. | New exporter mistakes diagnostic availability for valid scenario evidence, or silently drops prior case marks. | Keep `scenarios.exercise` and Browser methods; `aggregate.validate` owns complete manifest/phase acceptance. These earlier journeys do not establish later unknown-token failure handling; no duplicate journeys proposed. |
| L4, all19 members | Existing Pause/drain/one graceful owned restart/resume flow → original hold/counter/public/browser results, old/new boot logs remain sanitized, actual cleanup retained. Product E2E. | Replacing retained context with final bearer leaks an old boot secret; diagnostics/removal loses a restart ownership record. | Keep `pause.exercise`/pause browser assertions and owned census, with launcher validation. Unlike L1–L3 this crosses a real daemon generation; it still does not test malformed-context recovery. |
| L5, all6 members | Genuine public job/CLI actions, parked owner/wakes and refusal → unchanged exact results, audits and finite capture/cleanup. Product E2E. | New fallback hides failed job/scenario or replaces an authentic refused result with PASS. | Keep `jobs.exercise`, `aggregate.validate_e04` including `validate_review_refusal`. Existing journey semantics differ from launcher evidence-error isolation; no new E04 behavior/control. |
| L6, all5 members | Real thread browser/CLI/token issue/consume/replay/denial → complete known bearer+invocation sanitation, original durable/no-effect/relay predicates. Product E2E. | Category code prints invocation token/exception text, or an incomplete last-known context exports raw thread captures. | Keep `threads.exercise/frame/command`, `thread_provider.command/redaction_context`, `aggregate.validate_e05`. Thread tokens add a distinct producer-side full-stream/suffix risk not owned by final exporter. Dedicated sanitation sensitivity is unproved. |
| L7, all3 members | Existing C-only cancellation/terminal refusal/original delayed CLI → unchanged prefix equality, launches, serialized content, ownership and full observation window. Product E2E. | Diagnostics changes E07 first-error ordering or permits invalid-context raw partial launch export. | Keep `e07.exercise/save`, `Controller.collect_e07_launches`, `aggregate.validate_e07`. It adds late captured streams and killed-client provenance; neither L6 nor TERM/INT proves these semantics. No inferred new E07 case. |
| TERM3.12 / INT3.14 | Existing active witness → signal exact launcher15/2, exit1, absent owned processes/units/groups/ports/root within original limits. Disposable launcher diagnostic. | Reordered cleanup loses active identity or reports clean after timeout; category fallback marks interruption success. | Keep `cancellation_hold`, `cancellation.validate`, `aggregate.cancellation_controls`. Prior run39765942783 PASS is authentic only for these diagnostics. They do not own baseline unknown-context failure or product cancellation acceptance. |
| `run.validate_timing`, `aggregate.validate/controls` | Complete actual phase records/56 variants each plus cleanup/export → only exact caps, actual valid records and successful complete execution admit health. Serialized validator. | New diagnostic field is allowed to override failed phase/export or empty variants. | Keep positive fixture and ALL9 existing controls: `missing-cell`, `skip`, `failed-variant`, `missing-variant`, `wrong-sha`, `cleanup`, `failed-job`, `failed-export`, `wrong-scenario-deadline`. Distinct from journeys: rejects forged aggregate shape; synthetic fixtures cannot prove cleanup execution. All controls NOT RUN. |
| `aggregate.validate_e04`, `validate_review_refusal`, `validate_e05`, `validate_e07`, `aggregate.main` artifact discovery | Reopen actual exported members → independent content/provenance/browser/result/source equality; missing detailed evidence stays unhealthy even with readable categories. Serialized content validators. | Treat category file as observations, relax required members/failure lists/source hashes, or populate old PASS marks. | Keep existing independent readers, public refusal/body semantics, E05 source hashes and E07 C prefix/source/launch closure. Their distinct risk is internally consistent but false producer evidence. No dedicated new synthetic validator fixtures or sensitivity claimed. |
| `cancellation.validate`, `aggregate.cancellation_controls` | Valid active/signal/cleanup receipt plus independently varied bad fields → reject invalid interruption closure. Serialized diagnostic validator. | Category success or root absence substitutes for launcher signal/contained-unit closure. | Keep positive fixture and ALL14 controls: `no-delivery`, `wrong-signal`, `false-success`, `not-active`, `missing-identity`, `missing-unit`, `live-cgroup`, `missing-port`, `data-residue`, `setup-failure`, `wrong-event`, `wrong-run`, `wrong-tree`, `missing-admission`. Distinct from baseline validators: real signal-to-launcher binding; controls NOT RUN. |
| `daily_admission`, `validate_admission`, `aggregate.admission_controls` | Same actual main event/run/attempt/SHA/tree joins across four artifacts → diagnostics never become another run or source receipt. Admission validator. | Category sidecar substitutes source/run or marks manual event scheduled. | Keep existing schedule/manual positives and complete table-generated negatives for missing/wrong environment fields, unsupported events, checkout/tree/repository/default/cron/manual-ref/schedule mismatch. These functions/inputs are unchanged; no rerun/new cases proposed. Distinct risk is identity, not behavior or sanitation. |

The new category and cleanup-isolation expectations have no attributable intended-failure/restored-pass pair. Current native FAIL documents the evidence gap but does NOT prove a category implementation's sensitivity or the hypothesized token failure. No authorized new venue exists in this leaf. Retired Python units are never an alternative; integration INCLUDING callback is SKIPPED24342/211270/271. Protected12, RED/restoration/GREEN and repetitions remain unauthorized. Do not create a production seam or restore tests to obtain proof.


### Accepted recovery expectations and authoring answers

These accepted rows cover only R1–R3 and their interactions; the L1–L7 memberships and validator maps above remain intact. IDs below are finite **design expectations**, not newly authored runnable tests, new manifest variants, or a campaign authorization. No new verifier/harness/venue/seam is proposed. Each observation is a shipping boundary (result.json, sanitized detailed members when admissible, original controller exit/traceback or actual owned cleanup observations), not a self-comparison with a helper.

Environment/levels: **H** = existing disposable hosted Ubuntu harness CLI/process/file boundary, synthetic run root, real ownership and original clocks, in EACH existing CPython3.12/3.14 cell; **J** = existing serialized result/consumer boundary in that same environment; **C** = actual controller/launcher propagation/finalization boundary within H. Root/process/port ownership is serialized through the existing root allocation. No live host, PR/local runner, extra disposable venue or infrastructure is allocated. No behavioral command is run for these rows; source/AST/diff and lint are recorded separately. Source/design inspection can assess these expectations; it cannot prove them. All rows have corrected behavior NOT RUN and intended-failure/restored-pass PROOF NOT RUN. Native39765942783 is the preserved genuine failure, not intended RED for this source repair.

| Requirement / case | Preconditions and action/input | Independently observable expected result, including final transition | Level / answer block |
| --- | --- | --- | --- |
| R1-01 valid | Safe existing0700 root/coord, no pending, exact0600/nlink1 final, matching SHA and two valid slots; read once before removal | result.failure_categories.controller available with only the exact validated envelope; deletion does not lose that in-memory projection; later failure receipt retains it. No readiness/case/health credit. | H+J / A |
| R1-02 absent | Safe root/coord, literal final ENOENT, no pending; or constructor never produced detail | unavailable/snapshot=null, no fabricated error or capture success; action failure remains. Mere unpublished absence does not itself mint a cleanup failure or success. After root loss same staged unavailable remains. | H+J / A |
| R1-03 unsafe | Symlink/dangling/alias parent, wrong root/coord uid/mode, or final link/hardlink/special/directory/wrong uid/mode | No unsafe open/write-through/root recreation. Fixed unavailable and diagnostic_read failure/incomplete cleanup when an attempted existing-root read is unsafe. Later eligible root work remains bounded and identity-gated. No raw reason exported. | H+J / A |
| R1-04 unfinished/collision | Existing pending (regular or unsafe), with absent or older final; invoke writer/read | Writer never truncates/removes pending and refuses; reader unavailable/null. Existing final bytes survive; actual unfinished read is incomplete diagnostic work. No promised visibility of local publication failure. | H+J / A |
| R1-05 size/UTF8 | Final of4096 acceptable bytes versus4097 overflow, invalid/truncated UTF8/BOM | Only strict complete at-most4096-byte envelope admitted; at most4097 read. Other inputs produce fixed unavailable, incomplete read attempt, no copied bytes. Finite remaining time governs later cleanup. | H+J / A |
| R1-06 closed JSON | Duplicate/extra/missing keys at top or either SLOT, array/scalar/wrong slot type, invalid JSON or recursive depth | Every malformed form unavailable, fixed value category for validation (actual parsing exception uses map), no echo. Actual read failure remains sticky through fallback. No alternate permissive parser. | H+J / A |
| R1-07 identity/schema | Wrong/missing/uppercase/non40/nonhex SHA, source unequal to admitted/result source, schema true/string/other integer | No available projection from another source; admitted source absent means whole optional member omitted. Existing admission joins still reject mismatched receipts independently. | H+J / A |
| R1-08 enums/numbers | Every fixed stage/error, unknown spelling, null slots; test non-Boolean0 and2^53-1, finite fractional value, negative/too large/string/Boolean/nonfinite/overflow | Exact enums/domain accepted only; all out-of-domain forms unavailable. Unknown caller/error type maps to other at construction, but arbitrary incoming enum strings are rejected. Null means only no observation in snapshot. | H+J / A |
| R1-09 null success ambiguity | Valid envelope with both slots null, then no further publication | available/null slots never certify error absence or successful capture, never fill missing variants/artifacts. Any underlying native health failure stays failure. | J / A |
| R1-10 stale | Earlier valid published envelope; later write fails before pending creation or dies before a boundary; read prior final | available may contain old/null slots; no latest/complete/publication-failure guarantee. If pending exists instead, R1-04 unavailable applies. No overwrite of first controller latches or raw primary. | H+J / A |
| R1-11 read/write expiry | Original effective end expires during read/parse/write/replace or an I/O error occurs | Before/after checks refuse new optional work. Expiry terminates applicable attempt sequence, not a recoverable unavailable branch. A committed replace may exist but no rollback/latest claim; initial safe result, if already published, survives. | H+C / A |
| R2-01 nested first error | Action throws A; nested phase/bounded unwind and restoration throw B/C before finalization | Original selected raw A/traceback and its fixed first stage/category survive; later restored errors stay secondary in gated evidence. Original nonzero/propagation is preserved through final main raise; category never asserts product cause. | C / B |
| R2-02 subclass/mapping | Known literals from every caller family, unknown dynamic name, UnicodeError/ValueError, TimeoutError/InterruptedError/OSError, RuntimeError with signal-like text, custom subclass and unrelated custom error | Closed stages and exact ordered built-in categories; no class-name/message projection or signal inference. Real signal receipt still owns signal identity. Final receipt contains only finite fields. | C+J / B |
| R2-03 reentry | Writer active, native callback or signal calls record_error; nested request attempts writer | Raw/first categories latch before I/O; second writer skips without lock/wait/recursion. Current snapshot may omit new latch; next existing boundary may publish only with positive original time. Primary still propagates. | C / B |
| R2-04 off-owner | Future nonowner record_error/writer request while owner snapshotting | First-winner memory only; nonowner writer skips/no enqueue/wait, no observer/relay dependency. Unpublished detail remains unknown. No new owner/thread lifecycle is admitted; original process outcome is unchanged. | C / B |
| R2-05 writer failure | Any publication metadata/create/write/close/replace failure after raw latch | Local publication state failed, no record_error/_attempt recursion, first raw/traceback/category retained. Existing pending never truncated; subsequent optional work rechecks original deadline. Remote detail may be old/unavailable. | H+C / B |
| R2-06 capture failure | Capture query/remember throws A; existing[] invalidation succeeds | [] attempt precedes diagnostic latch. Same A propagates with original traceback; separate first context slot records context/category without setting readiness or replacing first_observed. Unsafe detail withheld; no last-good/bearer-only republish. | C+J / B |
| R2-07 invalidation failure | Capture throws A and original[] write throws B | Same B/traceback/exception precedence propagates as before; fixed context capture slot about A may latch after failed[] attempt and local invalidation_failed=true, never a successful invalidation claim. Writer/category errors cannot replace B or supply readiness. | C+J / B |
| R2-08 expiry/constructor | SIGALRM absorbed by diagnostic best effort, or constructor fails before main try | Positive original end checked before each later optional step; expired means none begins. No changed timer/close/evidence/observer/native lifecycle. Constructor may have no controller snapshot; launcher action remains failure. | C / B |
| R3-01 collection fails | Owned returned, category snapshot staged; valid-only collection throws or exits without required context after partial staging | Collection fixed first slot and staging_incomplete persist. Eligible ports/root still attempted once under C, and their true facts retained. Both cleanup ok fields false; fallback FAIL/variants={} even if root removed. | H+J / D |
| R3-02 reader fails | Category read unsafe/invalid/I/O failure, with positive C after owner | diagnostic_read latched, unavailable snapshot staged; collection, ports/root independently eligible. Failure cannot skip later work or be erased by their success. True absences do not repair cleanup acceptance. | H+J / D |
| R3-03 ports | Port list unreadable/missing on an admitted extant root, or first probe fails but time remains for another observed port | First port category latches, missing observations unknown; subsequent individual observed probes and root attempt only with time. Per-probe1s clipped to remaining C, no reset or new scan. cleanup/timing false through export. | H+J / D |
| R3-04 root/partial removal | rmtree raises after partial deletion, literal absence observation fails, or identity changes | No removal through changed identity; residue/unknown preserved. Eligible literal observation may show actual absence but cannot clear root failure or certify units/groups. No retry/rescue removal during export. | H+J / D |
| R3-05 owner failure | Owned returns false/ordinary error versus Owned TimeoutError | Existing owner receipt and uncertainty retained first. Ordinary error allows only already-authorized file work with positive C and identity gates; timeout ends cleanup sequence. No additional Owned signal/sample/wait. Native units/groups unknown unless independently proved by original owner receipt. | H+C / D |
| R3-06 expiry | C expires after collection, between individual ports, or before root removal; an inner catch absorbed its alarm | No next attempt starts; required unattempted work unknown/incomplete, both ok fields false. Original C never recomputed. Export retains original E and may read diagnostics only, never resume teardown. | H+C / D |
| R3-07 later export failure | Initial metadata FAIL was published; later detail write/serialization/recollection/pending write or original deadline admission fails before final replacement; otherwise replacement succeeds | Precommit failure retains the initial FAIL if replacement never commits; export slot latched when possible and export.ok=false. Fallback variants={}, no unsafe raw fields, both cleanup ok fields false for incompleteness. No unavailable persistence claim or reliance on a later rescue write. Successful final replacement is terminal, with alarm retired before the last original E-1 check and no postcommit deadline/alarm reclassification. OS-I/O overrun/crash persistence remains unguaranteed. | H+J / D |
| R3-08 recollection success after failure | Cleanup staging failed/partial root remains; later E-bound recollection succeeds | Sticky cleanup/staging failure survives; safe partial members may be diagnostic, result cannot become PASS or regain marks in incomplete fallback. Later detail does not launder failed cleanup. | H+J / D |
| R3-09 detailed withholding | Invalid/incomplete context with valid safe categories and genuine port/root absence; or all categories unavailable with otherwise valid detail | Categories never bypass sanitation, complete-context/required-member or native health failures. First scenario/nonzero remains failure. Optional metadata absence supplies no positive/negative fabricated behavior result by itself. | H+J / D |
| X-01 R1+R2+R3 chain | Raw action A; nested restore B; capture C with failed[] D; boundary writer I/O fails; launcher read unavailable/failed; collection fails; one port throws; root removal succeeds; export fails | A remains raw original-primary if it won first; existing propagated capture/invalidation precedence is preserved; finite slots never overwritten. Later independent attempts occur only while C positive. True root absence is a fact only; both cleanup ok fields remain false, initial FAIL survives, variants={} on fallback, no raw secrets, native member/health failure independent. | C+H+J / E |
| X-02 stale/null+root loss+expiry | Old/null validated snapshot read once; late controller writer fails; root removed; E expires after initial FAIL before final admission, or an alarm is queued at final replacement | Frozen old/null projection survives root loss but asserts no latest/complete cause. No re-read/recreation; precommit expiry preserves genuine initial FAIL when no later replacement commits, with original failed/incomplete deadline receipts. Successful final replacement is terminal despite queued alarm delivery; no later FAIL classification/rescue dependency. Prior latched failures remain non-PASS; a successful commit gives no actual product acceptance. Cooperative OS-I/O/crash limits remain. | H+J / E |

The following accepted extensions append inputs and assertions to R2-01,
R2-05 and X-01 above; all thirty original rows remain intact. They use the
SAME C/H/J environments and add no runnable cases or proof campaign.

| Affected existing row | Requirement → precondition/action → final observable result → level |
| --- | --- |
|R2-01| Preserve the actually-selected raw winner A/traceback, including nested restore B remaining secondary, rather than infer an initiating assertion. Preconditions: completed record_error, safe existing paths/source and positive ORIGINAL effective end. The SAME earlier writer is tried, with the original conditional poison in its main-local finally. Keep poison ordinary-return AND secondary-throw branches; if publication succeeds, the private finite file and eventual frozen launcher projection record A's first category before poison, independently of poison success. A stays the stored primary and the unchanged final raise propagates A/traceback when reached; the result remains nonzero/FAIL. An escaping writer still unwinds through the poison attempt; its completion and arbitrary later interruptions are not guaranteed. Refusal does not promise a file. C+H; J observes eventual safe result/noncredit, not proof poison ran. |
|R2-05| With raw A already selected, a usable unpoisoned Browser and positive ORIGINAL time, distinguish (a) metadata/pending/create/write/close/replace failure or absorbed alarm IN the protected writer body: existing handler makes sticky refusal and returns if its assignments/reset complete; later calls consult that sticky state, raw A/traceback survive and pending bytes are never truncated/exported; from (b) an interruption escaping writer entry, handler assignments or active reset: caller finally still preserves the original conditional poison ATTEMPT before outer finalization can consider native work. Publication/active/latch outcomes remain UNKNOWN wherever assignments were never reached; do not promise publication_failed, active=False, cleanup of pending or state repair, and do not alter/reset the writer. Original raw latches and unchanged selected-primary final propagation/nonzero remain, subject to existing later interruption limits. Safe exported result is unavailable or a validated possibly stale projection, with original failure noncredit; that J output cannot prove poison executed. Expiry admits no new optional work or reserve; only the unchanged three calls remain. C observes actual propagation/poison attempt and resulting finalization; H observes real publication/refusal/inode outcomes; J observes frozen failure semantics. |
|X-01| Retain the original raw A/restore B/capture C/failed[] D/writer-failure/reader-collection-port-root-export failure chain. Retain the added earlier-SUCCESS branch: while ORIGINAL time is positive the moved call publishes, then poison/close/capture fails or later expiry prevents updates; frozen safe earlier projection may survive root loss but is stale/noncredit, full detail stays withheld, both cleanup flags stay false for required staging failure, fallback variants={} and native health failure persist. Incorporate D1 within these branches: a writer whole-call escape must still unwind through the original conditional poison attempt; never infer that attempt from J's unavailable/stale fields or promise active/sticky reset for unreached assignments. Raw A ownership and unchanged final-primary propagation remain; successful poison completion and immunity to repeated interruption/crash are not promised. C+H+J; no new journey, row or campaign. |

Four authoring answers for **every changed named row**, with complete shared-block membership:

| Block and exact membership | 1. Observable contract | 2. Credible regression | 3. Nearest keepers, precise gap and distinct additional-layer risks | 4. Test-only production seam |
| --- | --- | --- | --- | --- |
| A: R1-01–R1-11 | Exact optional result member/source/snapshot fields after cleanup and final receipt replacement; unsafe input never appears in artifacts; fixed unavailable/omission and original failure retained. | Admit duplicate keys/source drift/nonfinite value; follow/truncate alias or pending; call stale/null snapshot latest/complete; export raw rejected bytes. | Existing Controller first-error/private staging and run.failure_receipt are nearest, but no existing direct assertion owns the new closed category channel or hostile filesystem/JSON forms. Existing E07 finite launch-record reasons are a shape precedent, not a keeper for this schema. H sees actual inode/read/write/root-loss risks; J independently observes serialized admission/absence/shape and health noncredit, which cannot prove OS behavior. | NO for every listed case. Existing private runtime path and public exported artifact only. |
| B: R2-01–R2-08 | Controller original selected-primary exit/traceback when the final raise is reached, gated detail and separate fixed first/context slots; earlier safe publication independent of poison success and original conditional poison ATTEMPT via main-local finally independent of writer return. No reentrant publication. | Publish during record_error; overwrite first raw/category on restore; recurse on writer failure; diagnose before[]; swallow invalidation/expiry; infer signal from RuntimeError text; equate body catch with whole-call protection and skip poison on entry/reset escape, or invent sticky/reset state for unreached assignments. | Keep phase/bounded traceback restore, record_error, capture's[]/bare raise, prepare_redaction and main's final-primary raise. Extend only R2-01/R2-05 for poison return/secondary throw and whole-call writer escape. Success/TERM/INT lacks these combinations; R2-08 expiry/constructor and R1-10 stale remain distinct. C observes actual poison attempt, primary propagation and resulting finalization; H observes real safe publication/refusal/pending and state uncertainty; J sees finite frozen fields/noncredit and cannot establish poison execution, unexported traceback or cause. Source inspection is not behavioral proof. | NO for every listed case. No new callback/export/injection/parameter/wrapper or writer change. |
| D: R3-01–R3-09 | Actual owner-first cleanup and independently observed ports/literal root, original bound transitions, final nonzero/FAIL, sticky both-ok false and variants={} fallback through later export; R3-07 also requires persisted final disposition to agree with terminal commit. | Keep one try grouping; let port failure skip removal; reset C; continue after timeout; clear staging failure after root absence or recollection; perform teardown in export; reintroduce a fallible deadline/alarm path after final replacement. | Keep Owned.cleanup/PID-start/unit/group/watcher gates, run.collection/failure_receipt, validate_timing and cancellation.validate. Existing TERM/INT success closes its diagnostic only, never failed baseline collection/isolation. No direct keeper asserts these new combined failures or the R3-07 final-commit ordering. H sees real removal/port/deadline/queued-alarm effects; J sees serialized failure laundering/empty variants and final disposition; neither layer replaces the other. Aggregate metadata controls reject false shapes but cannot prove cleanup execution or publication ordering. | NO for every listed case. No Owned implementation change or test-only seam. |
| E: X-01–X-02 | End-to-end selected-error precedence and frozen safe projection/noncredit across controller death/root loss/export, with full-context raw withholding. X-01 retains original chain plus earlier snapshot/later poison-close-capture-failure/expiry and caller-finally interaction; X-02 retains terminal disposition matching successful final replacement. | Locally correct helpers combine to skip poison on whole-call writer escape, erase first error/cleanup failure, refresh stale source, admit partial detail, treat unavailable/stale J output as proof poison ran, or rely on another write after a postcommit alarm. | Same B/D keepers and precise cross-process composition gap; extend X-01 only. C owns actual propagation/poison attempt/finalization; H owns actual publication/refusal/root-loss effects; J uniquely owns frozen failure semantics after source loss and can misattribute/launder failure despite correct local refusal. J cannot prove OS or poison execution or repair unknown active/latch state. Preserve12649 X-02 precommit/terminal commitment and queued-alarm semantics. | NO for both cases. Existing main boundaries/artifacts only; no new journey, verifier or seam. |

No existing success journey owns category-failure, cleanup-isolation or adversarial-sanitation sensitivity. No new runnable assertion is claimed for these design cases. Existing tooling/venue authorization does not permit a new fail/pass, protected mutation or repetition campaign here. Under current test-authoring skill, corrected behavior is **NOT RUN**, attributable intended-failure/restored-pass is **PROOF NOT RUN**. This honest limitation does not fabricate coverage and does not alone block design review; actual missing/unsound design requirements still warrant corrections. This source delivery preserves these case/keeper/gap records and current authority limits; no retired units, new verifier or test-only production seam can fill the gap.

The full unaffected validator reference is TASK12631's **All validator/control memberships retained (NOT RUN here)**, byte-identical parent step8/qa-design-assessment.md SHA256d8c96b54c7e8be86244455a75098ab00985bae4b6c9a6c7d66c8cb9a62972799. Retain BOTH schedule/workflow_dispatch admission positives, all existing missing/wrong environment inputs, unsupported events, checkout/tree/repository/default/cron/manual-ref negatives, and per-event missing/conflicting comparisons for EACH ADMISSION_FIELDS member. Keep all original nine aggregate negatives and fourteen cancellation negatives listed above, all timing/nonoverlap/arithmetic caps and actual-content source/public/wire/CLI/browser/result/audit/provenance readers. These remain existing references, not reimplemented or executed validators. Optional categories cannot supply their required files or missing observations.

## Root-owned protected proof operations — not executed

The accepted four-answer case record above remains authoritative. The following
sizes its outstanding proof operations against the converged main source; no
operation is authorized or run by this child. The v24 obligation is credible
production-regression RED, byte-exact restoration, GREEN, **then** five isolated
and five sibling repetitions. Existing baseline, denial, binding and aggregate
controls do not satisfy production-regression proof.

| Case | Real source/symbol and intended temporary regression | Attributable assertion expected to fail |
| --- | --- | --- |
| 1 | `runtime/orchestrator/run_step.py::_consume_completion_report_body`, remove only `_enqueue_task_generation_aware(orch, child_id)` immediately after successful `try_delegate` | `journey` never receives the actual worker socket witness within30s; committed child exists, but no worker `session_start` or final closure. This is action failure after successful public setup, not bootstrap failure. |
| 2 active | `runtime/daemon/routes/tasks.py::submit_completion`, ordinary `prior is not None` / `v2_admission is None` replay arm: insert a second result through existing `org.db.insert_task_result` with the same task/agent/session and submitted summary/confidence/status/decision before returning200 | `unchanged` and `Store.result` reject an additional row; literal HTTP200 alone cannot pass. |
| 2 terminal | `runtime/daemon/routes/tasks.py::_require_task_active`, remove only terminal-status rejection while preserving unknown-task rejection | Literal terminal replay reaches the prior-result return200; `deny(E06.literal-terminal)` expects exact409/task_not_active. |
| 3 | Same duplicate-result operation as case2 active, exercised independently by `journey(lost=True)` after the relay records committed upstream200 and drops the response | `unchanged`/exact committed-row count fails on the real byte-identical retry; pre-commit/setup failures do not qualify. |
| 4 active | `runtime/daemon/routes/tasks.py::submit_completion`, same ordinary replay arm: replace read-only return with last-write-wins `UPDATE task_results SET output_summary=?, decision_json=? WHERE id=?` using submitted values and `prior["id"]`, commit before returning200 | Literal replay remains unchanged; changed payload fails `unchanged` and `Store.result(first)` original-summary/decision equality. Mutation of product persistence needs root disposition. |
| 4 terminal | Same terminal-gate operation as case2 terminal, independently exercise changed terminal replay | `deny(E06.changed-terminal)` rejects200 instead of exact409/task_not_active. An earlier literal replay failure is not this variant's proof. |
| 5 known slot | `runtime/daemon/routes/tasks.py::submit_completion`, replace only the pre-lock `expected != body.session_id` denial with unconditional `{ok: True}` return | Stale natural S1 under live S2 and fabricated known-slot cases independently fail exact409/session_mismatch. The mutation also affects case7; its earlier failure is not case5 evidence. |
| 5 no slot / unknown task | Separately replace the no-prior `unknown_session` raise with `{ok: True}`; separately remove the `task is None`404 guard in `_require_task_active` | No-slot must fail exact409/unknown_session; unknown-task must fail exact404/unknown_task (exception/500 is failure, never accepted). Keep both operations distinct from known-slot proof. |
| 6 | `runtime/daemon/routes/tasks.py::router`, remove only `dependencies=[require_token()]` | Four absent/wrong GET/POST requests independently fail exact401; unauthorized POST may create state in this disposable proof only and must fail the no-write oracle. No protected auth mutation was made. |
| 7 | `runtime/daemon/routes/_org_dep.py::resolve_org`, temporarily swap alpha/beta selection **only for completion-path requests**, retaining public init/create/read routing | Both directions of genuine colliding-ID crossed sessions must independently reject the erroneous200 and dual-store mutation. Ordinary setup must succeed first; a setup regression is not this proof. |
| 8 | `web/src/design-system/providers/_real-tasks.ts::useTask/useTaskRecall`, remove only slug from both query keys; retain request URL builders | Populated alpha→beta task click must fail target-org detail/Recall response or render sentinel assertions for genuine colliding IDs. Build/import failures do not qualify. |

This is twelve separately attributable operations: cases2/3 share one mutation
recipe but need distinct runs; terminal2/4 likewise; case5 has three recipes.
Each listed variant must be reached in an isolated case execution. The current
baseline is fail-fast and has no case-selection CLI: an earlier case7 denial
failure cannot be credited to case5, nor a literal replay failure to a lost or
changed replay. Root must size the minimal existing-harness case dispatch for
that campaign before execution; no new proof framework or product seam is
proposed. The full sibling baseline stays eight cases/23 variants.

Common restoration/containment command plan for **each** operation (proposal
only): bind `PROOF_BASE` to the final converged clean candidate; save
`git show "$PROOF_BASE:$PROOF_PATH"` and its SHA256 before any mutation. On an
isolated disposable proof checkout, restore with
`git restore --source="$PROOF_BASE" --worktree --staged -- "$PROOF_PATH"`, then
`git diff --exit-code "$PROOF_BASE" -- "$PROOF_PATH"` and compare the restored
SHA256 to the saved bytes. Verify **every** changed path, including both Web
query keys, before a clean committed GREEN source is launched. The existing
launcher requires clean committed source: a separate ephemeral RED commit and
a byte-restored GREEN commit are necessary; never publish a protected mutation
to this PR. Run only through the existing closed `e2e/run.py` disposable-runner
boundary with its900s hard cap and PID/start/unit/cgroup/port/socket/data cleanup.
A nonzero RED without the named assertion, a dirty restoration, or unknown
cleanup invalidates proof. Repetitions remain unexecuted until this disposition
and attributable proof are complete.

## Enforcement and uncovered work

The YAML defines the daily schedule and health aggregate; it does not configure
GitHub rulesets. Only `Source Boundary Lint` and `Web Lint (Node 24)` are automated
merge checks, both unconditional at the final PR head and exact merge on main;
read their status back independently. `local_ci.sh all` runs source-boundary
lint plus Web static checks, and cannot establish Product E2E success. Docs and
the real hostile lab are manual-only; retained canonical/platform/native and
actual CLI callback sources keep their fences and are not automated merge gates.
Independent full review, scoped QA, normal hooks and manager guarded merge remain
required. No old unit or full historical G-run allocation is renewed.

The E04 prefix below authors automatic exit0, review-required refusal, exit7,
timeout and both fan-in orders; their behavior remains unproven. Wider jobs,
threads/replay, cancellation/late
callback, restart windows beyond the bounded Pause increment, PR1014 compatibility
and authentic memory thresholds remain root-owned uncovered work. Provider hold and response
loss in the baseline do not establish restart/recovery. No merge or deployment follows from a
successful run. Deletion PR1024 is merged at
`5e87e778c6968338a12bd5cd65d461e5699836be`; this lane preserves that deletion and
all current product/Web/shared assets. The current full baseline covers
Python3.12/3.14, with TERM3.12/INT3.14 launcher diagnostics at 19:00 UTC /
03:00 Asia/Shanghai from immutable default-branch source. Historical scheduled
executions failed; corrected behavior is pending. Historical TASK10532/run38035241095 at `afb46cbb6` supplies only the
3.12/3.14 baseline and launcher cleanup, not current-head or daily evidence.

## Admission controls (THR291seq247 / TASK-11897)

`python e2e/aggregate.py --controls` exercises shipping admission and receipt
validators with isolated synthetic Actions-shaped payloads/environment and git
read outputs. Child launch is prohibited in the admission controls. These
fixtures never invoke scenarios or authorize local E2E. Existing aggregate
refusal controls retain missing/failed cells, variants, cleanup/export and
deadline cases. The finite change cases and four authoring answers are:

| Case/input and expected observation | Observable assertion | Wrong outcome detected | Why earlier evidence misses it | Production seam added? |
| --- | --- | --- | --- | --- |
| Exact scheduled payload/env/SHA/tree -> schedule identity; missing/wrong cron/env/source -> refusal | `daily_admission` returns literal expected identity or raises | Admission of unsupported or inconsistent schedule | Older predicate had no run/tree binding | NO; direct existing callable, fake git reads only |
| Main dispatch payload without schedule -> workflow_dispatch; non-main/default/repo/workflow/run/SHA/tree failures -> refusal | Same shipping callable and child-launch prohibition | Non-main or mislabeled manual admission | Older predicate rejected every dispatch | NO |
| Absent/PR/push/release/workflow_call/repository_dispatch -> refusal | Same predicate; static workflow/CLI selection checks | Broadened unsupported entry or local alias | Schedule-only positive did not exercise these changed branches | NO |
| Coherent manual/schedule receipts -> identity accepted; any missing/conflicting field -> refusal; missing execution evidence -> FAIL | `validate_admission`, cancellation wrapper/inner joins, aggregate controls and health output | Cross-event/run/tree receipt adoption or scheduled label for manual | Older source only compared SHA and hardcoded schedule | NO; existing validator lane only |
| Current docs/workflow retain cron,56 each3.12/3.14,TERM/INT,four artifacts,bounds,two lint checks | Static source comparison at final head | Policy drift, hidden subset/retry or added merge test | Control success cannot verify documentation or workflow preservation | NO |

These are source/static and isolated predicate/aggregate control levels on the
maker/QA host, never product/browser/daemon/provider execution or authentic
Actions receipts. Concrete command/outcome attribution lives in the task/PR
record. Protected12 defect operations, RED/restoration/GREEN and repetitions
remain UNEXECUTED and unauthorized. General integration INCLUDING actual
callback stays SKIPPED THR243seq42; Python units stay RETIRED. Historical daily-only
case-stage descriptions below remain evidence of their original source, not
current manual-admission policy or a renewed execution budget.

## Cadence delivery and remaining test-authoring evidence (THR-291 seq69)

The existing four-answer case record and twelve protected deliberate-defect
operations above are preserved. All twelve operations, attributable RED,
byte-exact restoration/GREEN, five isolated/five sibling repetitions remain
**UNEXECUTED / unauthorized**. Baseline success and harness refusal controls
are not full assertion validation. This separate authoring gap does not impose
an obsolete E2E merge hold: independent full review/scoped QA may accept this
cadence/policy change without E2E execution/protected proof, and must identify
that exclusion. No complete behavioral/test-authoring acceptance is claimed.

Daily summary reports actual health/source SHA, exact artifact names and
failure/missing evidence; each artifact retains seven days. Missing/failed/
skipped/wrong-source/wrong-version cells, incomplete diagnostics/artifacts and
unknown cleanup fail health. Static merge checks offer no equivalent behavioral
coverage and delayed detection remains a risk. Wider jobs/threads/product
cancellation/late callbacks/actual daemon restart increments remain separately
reviewable root-owned daily work, unproven until real evidence. General
integration is SKIPPED THR243seq42. Archived THR297 Build11 remains FAILURE
143pass/3fail/40errors/28UNTESTED with two-org cause UNKNOWN; this baseline does
not fix fixture/systemd/interpreter gaps. TASK10582 owns detailed reconciliation.
PR1014 compatibility/authentic-memory obligations remain unproven.

The accepted TASK11077 revision1 / TASK11080 design assessment and TASK11054
implementation select only O1–O7 observer and J1.a–c audit-reader repair in five
harness files. The selected scheduled run38330308418/source7dc78c0548294f9c2e14005bb7d637506df1129e
remains FAILURE: five authenticated ZIPs/105 files/863 source blobs/13 checkout
joins; all168 dispositions stay111 recorded-with-gaps,3 attempted-errors and
54 unreached, with actual starts19/11/19 against39 full starts. Both corrected
Pause closures exist on3.12/3.14;3.13 fails earlier. No E04 completes and E05/E07
are unreached. Winning response/shared cause remains unknown; all three E04
SID assumptions are separate source-confirmed latent defects.

O1–O7 and J1.a–c reuse the existing cases/venues and add no production test seam.
Old mounted GET near approval/reload, nested Recall ordering, body loss/invalid
JSON, wrong method/origin/frame/query/redirect and exact live/final S1/S2 history
are the distinct seams; deliberate negative controls remain UNEXECUTED and
unauthorized. This source repair supplies no corrected GREEN, behavioral,
causal/sensitivity/repetition or test-authoring PASS, intermediate DOM/process
joins, comprehensive sanitation, complete Pause/product acceptance or full
runtime/roughly100MiB suffix fit. Independent full review and same-head SCOPED
STATIC QA remain required with Source Boundary Lint/Web Lint(Node24), normal
hooks and manager guarded merge. Corrected behavior awaits the existing daily
19UTC default-source Python3.12/3.14 evidence. General AND actual CLI callback
integration remain SKIPPED THR243seq42; Python units remain RETIRED. All56
variants/38 starts and budgets480/240/720/60/120/900 stay unchanged. THR291seq119
abrupt R andseq126 E04 cap/persistent Stop implementation/acceptance remain HELD.

## Bounded Pause source increment (THR-292 / TASK-10555)

`controller.main` executes the unchanged `scenarios.exercise` closure first,
then `pause.exercise`, inside the shared admitted launcher. The original
23 variants, 9 starts, alpha 6 tasks/8 results and beta 1 task/1 result are still
asserted before any Pause work. The manifest adds 19 separately attributed
variants (42 combined). `observations.json` records their inputs, CLI transport,
public projections, read-only durable snapshots, actual process identities and
host anchors; `scenario.json` passes named variants/phases to the existing
`result.json` and daily aggregate. Exact required-set/PASS validation remains
unchanged: absent, failed or skipped evidence fails daily health.

Authority is the current TASK-10555 consolidated step9 brief under
THR292seq14 approving9 amended13, seq18/23 and THR291seq69/87/88. It reuses the
accepted TASK10494 case design (SHA256
`fc214b0aee68c9b579add9040f62bb1d12744e471a9a9bd998d8a9f2cec5e353`),
TASK10496 DESIGN-ONLY PASS and manager acceptance. Only the external provider's
finite `code_reviewer` input and reviewer fixture body are amended; frontmatter,
teams, protocol, 300-second timeout, stubs, registry and enforcement are retained.
The genuine reviewer is its own normally created child with an actual APPROVE
callback; no worker is relabelled and no producer/session/history is seeded.

Each row records requirement, public action/scenario, fixed expected observation,
level, credible regression, nearest existing source and distinct missing seam.
Every row answers **test-only production seam: NO**. Historical Python units in
the accepted case map stay retired; none are recreated, imported or executed.

| Group / requirement | Variant IDs and action → fixed expected observation (fresh public CLI/API/browser E2E) | Credible regression caught | Nearest existing source; distinct missing coverage |
| --- | --- | --- | --- |
| 1 P01a/P05 chain/drain | `Pause.chain-drain`, `Pause.drained-before-restart`: public NULL-parent root; genuine manager delegates worker then reviewer; Pause generation0→1 with live worker → unchanged lifecycle/chain/links, authentic running worker capture/blocker. One normal worker completion/exit → original INTEGER result consumed, exactly one pending reviewer/no SID, quiescent real host receipts, paused with no blockers. Bounded socket/executable/audit/admission observations deny reviewer launch until Resume. | Missing descendant gate, fabricated capture, early Paused, suppressed callback settlement or duplicate reviewer. | `scenarios.journey` covers ordinary one-child continuation without hold; no chain/Pause/host-drain seam. |
| 2 P11/P14/P17 identity/idempotence | `Pause.child-pause-denied`, `Pause.child-resume-denied`, `Pause.pause-idempotent`, `Pause.resume-idempotent`, `Pause.stale-resume`: child controls409/root_task_required; repeated root Pause/current Resume200/changed=false preserves capture/generation/audit/queue/results/sessions in both orgs. After first Resume generation2 and genuine reviewer launch, new Pause generation3; stale Resume generation1 must409/control_generation_conflict and keep newer hold. Explicit Resume generation3→4 proceeds without duplicate work. CLI uses explicit org/generation and compares real response records without a one-wire assumption. | Name-based root scope, duplicate audit/toggle/enqueue, stale release or new session invented by retry. | Baseline E06 callback idempotence and retained Web `pause.test.tsx`/`write-path.test.tsx` cover different callback/UI seams; no real control generation arbitration. |
| 3 P14b/c same-org progress | `Pause.same-org-unheld`: while original worker/tree held, public distinct NULL-parent root in alpha → genuine launch, CLI callback, durable/public natural done with available capacity. Reads cannot create/update Pause controls. First hold/worker identity remains. Beta baseline remains unchanged. | Org-wide hold, blocked independent capacity, GET creates control or separate tree corrupts results. | Baseline `collisions` covers different orgs; missing two independently active roots in the same org under hold. |
| 4 P12 drained persistence/P01a finish | `Pause.drained-restart`, `Pause.restart-final`: verified paused/quiescent closure → pidfd TERM/wait on exact owned disposable daemon PID/start, start same shipping executable/same publicly initialized files, no re-init/DB writes. Control/generation/chain/child/results/consumption state exact; pending reviewer still has no launch/admission/SID. CLI Resume → one real reviewer APPROVE and one natural M2 done; original consumed results persist and exact start/end/consumption/chain counts agree. | Restart forgets hold, reaper falsely fails, replay of consumed callback, duplicate reviewer/M2 or no release discovery. | Baseline response-loss and `cancellation_hold` are transport/launcher cleanup; neither restarts the product daemon against retained files. Only this drained Pause window is authored. |
| 5 P01b separate drain Resume | `Pause.resume-during-drain`, `Pause.drain-final`: separate root/worker; Pause then Resume before worker callback → original PID/start/SID stays alive, hold clears; normal worker completion → exactly one reviewer/M2 path with 4 authentic results and 3 tasks. No restart here. | Resume kills/relaunches worker, callback duplicated, chain wakes manager early or diverges from restart journey. | `scenarios.journey` has no control while its worker is live; distinct from group4's drained restart. |
| 6 P16/P18 browser/readback/terminal | `Pause.overview-readonly`, `Pause.browser-pausing-en`, `Pause.browser-pausing-zh`, `Pause.browser-paused-en`, `Pause.browser-paused-zh`, `Pause.terminal-denied`: shipping Chromium responses at1440×900, user locale selector, root detail state/blocker/Resume reached through the list row, genuine child inherited/root link with no controls. In both held phases, root and child Pause projections require the authored ROOT lifecycle `in_progress`, exact alpha/root/generation1/held/effective_hold/control_state and, when paused, complete evidence/empty blockers. Own task/detail/Recall lifecycle stays separate: root `in_progress`, live worker `in_progress` then `completed`, reviewer `pending` until Resume. No browser overview panel, overview read or list-row pause control/label/evidence on the Tasks page (founder THR-292 seq41/seq45), CLI alpha overview/read-only counts and restart limitation, real reload preserves generation1. Terminal browser retains completed and no Pause/Resume; both CLI controls409/task_terminal with unchanged state. `Pause.exact-closure` joins every launch/result/host count. | Pause lifecycle incorrectly uses child status or a different root; synthetic-only UI masks transport drift, child control appears, locale/reload mutates state, GET queues work, missing org isolation/count honesty or terminal revival. | Retained Web `pause.test.tsx` (detail controls), `pause-overview.test.tsx` (list/panel absence, detail focus) use synthetic projections; root and pausing-worker observations both have `in_progress` and cannot distinguish a pending reviewer from its inherited root Pause lifecycle. Baseline Browser lacks actual daemon hold/restart bilingual readback. |

### Preserved Pause restart/count boundary

The E04 prefix below begins after these unchanged literal Pause boundaries.
The named process-local receipt reset retains the old boot archive; it is not
lost evidence or an arbitrary counter rebaseline. Host receipts expose aggregate/recent summaries,
not session IDs: serial phase deltas, exact admitted/released/published counts,
durable session audits and PID/start disappearance supply the join. Nothing
claims a receipt identity field that the API does not expose.

| Phase / named anchor | Actual launches since suite start | This daemon's admitted/released/published (quiescent) |
| --- | ---: | --- |
| Unchanged baseline closure / `baseline-before-pause` | 9 | 9/9/9 |
| First chain M1 ended; worker live / `restart-case-worker-running` | 11 | 11/10/10, active1 |
| Independent alpha root done; worker consumed; reviewer pending / `pre-restart-12` | 12 | 12/12/12 |
| `pause-owned-daemon-restart`: old PID/start verified exited, new PID/start ready / `post-restart-zero` | 12 | 0/0/0 |
| Explicit Resume reviewer live / `post-restart-reviewer-running` | 13 | 1/0/0, active1 |
| First tree reviewer and natural M2 done / `post-restart-first-chain-2` | 14 | 2/2/2 |
| Separate drain case M1 ended; worker live / `drain-case-worker-running`, `resumed-draining-worker-4` | 16 | 4/3/3, active1 |
| Final closure / `final-post-restart-6` | 18 | 6/6/6 |

Exact combined alpha closure is13 tasks/17 results; beta stays1/1. Each chain
has3 tasks/4 results, root count2, each child count1, four starts/ends/consumed
reports, exactly one chain advance and delegate→done manager decisions. Closure
joins each actual task/agent/session launch to exactly one genuine
INTEGER `task_results` row, with identical row contents in the final snapshot.
`session_start` supplies the matching SID; `CompletionReport.model_dump()` has
no SID. Its completion audit supplies task/agent/status/confidence/summary/verdict
and decision semantics, joined to the result and ordered start/end audit rows.
Serialized `NextStep`/`ChainLeg` null fields and empty lists are compared as
model defaults; every declared decision value and extra active value is checked.
The already-read snapshot is retained through sanitized `c.durable` export as
`pause_chain_observation`, explicitly partial/unvalidated, before assertions.
`pause_chain_closure` and named PASS markers still require every assertion.
Final socket holds are empty and every actual provider PID/start is gone. The outer
unchanged watcher still owns all browser/relay/socket/old+new-port cleanup.
Controller phase evidence retains old/new daemon identities and cumulative
ports even if restart readiness fails. No serving daemon is touched.

### Authored versus executed

The first daily run [38181136948](https://github.com/t-benze/happyranch/actions/runs/38181136948)
executed source `bfeb109fe0e7801f17508abe96a39d376f2886fb` on 2026-10-11
at22:37UTC and FAILED in all Python3.12/3.13/3.14 cells. Each records14 partial
Pause PASS observations: chain-drain, child-pause-denied, child-resume-denied,
pause-idempotent, browser-pausing-en/zh, overview-readonly, same-org-unheld,
drained-before-restart, drained-restart, browser-paused-en/zh, resume-idempotent
and stale-resume. `Pause.restart-final` aborted on the harness's nonexistent
completion-audit `session_id` expectation; terminal-denied, resume-during-drain,
drain-final and exact-closure were not reached. This is no attributable product
regression RED or complete post-Resume audit/behavioral acceptance.

TASK10870/TASK10874 correct the existing `finish_chain` consumer join for both
PAUSE_RESTART and PAUSE_DRAIN, reusing accepted groups4/5/6 above without a new
production seam. Corrected behavior, wrong/missing/duplicate result identity,
wrong audit owner/payload/order rejection, protected RED/restoration/GREEN and
repetition remain **UNEXECUTED**. The failed run did not export its final chain
SQL snapshot; public results/Recall support partial lifecycle observations only.
Full case ledger and original designs: TASK10870 assessment.md,
original-case-records.md and evidence/pause-case-ledger.json; TASK10869 supplies
independent aggregate corroboration. New partial snapshot retention establishes
no retrospective SQL proof. Local syntax/source/static inspection and lint
cannot establish behavior or complete test-authoring PASS. All three aborted
runs recorded cleanup.ok and empty survivors/errors; their scenario times
76.722/73.925/69.951s measure abort prefixes, not full combined runtime fit. Manifest
600-second target, 720-second action stop, 900-second hard limit, evidence/cleanup
reserves and provider300 remain unchanged; there is no ad hoc/direct controller,
provider, browser or disposable-daemon execution, rerun or forged event admission.

All twelve inherited deliberate-defect operations and any Pause proof are
**UNEXECUTED/unauthorized**; no RED/restoration/GREEN or five-isolated/five-sibling
repetitions. Authentic held-queued workflow history remains **UNAVAILABLE**;
six P07/G4 capture interleavings, other restart windows, v2/workflow histories,
crash-after-Resume-before-enqueue and unscheduled/unimplemented cases remain
**UNEXECUTED**. Launcher TERM/INT cleanup is not product cancellation/restart
coverage. General integration is **SKIPPED THR243seq42**, never PASS; former
Python units remain **RETIRED**. No compatibility/authentic-memory/deployment or
feature-completion acceptance. Full independent review, source/static scoped QA,
exact-head two-lint receipts, normal hooked publication and manager guarded merge
remain separate delivery gates. No production schema/auth/permission/enforcement,
registry/shared stub, dependency, cadence/cap or cleanup-machinery changes.

## E04 jobs prefix (TASK-10686 / THR-291)

Final saved `inner_text` requires literal `RECALL TREE`, `OUTCOME`, the exact
scenario summary and `completed`, alongside each exact existing screenshot.
CSS uppercase affects this rendered representation; existing own-body
`textContent` and accessible-name expectations remain `Recall tree`/`Outcome`.
The six final-record keepers (including review-required refusal) retain every
SQL/result/session/audit/CLI/HTTP/no-effect join; no artifact is lowercased.
Run39758401569 contains all twelve final records/PNGs before E07. Its native
Health FAIL remains historical evidence of this representation mismatch;
corrected consumer behavior is NOT RUN, sensitivity PROOF NOT RUN.

Current source amendment TASK12452 / sole ROOT TASK-12445, Founder THR291
seq301(b)/303, accepted manager disposition after independent TASK12447 DESIGN
REVISE: replace only slot44/index43 with `E04.review-required-refused`. No legacy
fixture. Individual legacy pending-job rejection AND natural waiter wake are
explicitly RETIRED for this variant, with NO parity keeper. The old prerequisite
failure is historical, not replacement RED or rejection/wake proof. Other five
automatic-job journeys retain their real block/wake/second-generation assertions.

Automatic admission still records case_manager job_auto_started and terminal
authorship, with no browser run mutation. Each actual script's existing socket
observes running immediately after submit and through S1, before release or
product timeout. Pair's other job remains running. Both detail states retain
no Run/Approve controls; unrelated individual Reject/running Stop controls keep
their existing assertions. Completed/nonzero/timeout caps and outcomes stay exact.

One public-created ordinary alpha/case_manager task retains its actual live
provider PID/start/SID. Its first jobs action is `jobs-refuse-review`, mapped to
the genuine `jobs submit --org alpha --from-file PATH --json`. The valid authored
UTF8 file uses review_required literal true, persistent false, cap30, python3,
cwd_hint null and short nonempty title/rationale. CLI must return exactly exit2,
empty stdout and the shipping newline-terminated scripts-submit file error with
that actual pathname; no timeout or relay traffic. Separately, while the SAME
provider remains live, existing Controller.api sends identical sorted JSON bytes
to authenticated public alpha/jobs/submit: exact422/detail.code
`review_required_removed` and the independently authored shipping message.
Earlier auth/model/session/purpose/title failures are not coverage.

Only the selected literal POST captures actual sent UTF8 entity/hash/length,
payload-file identity, unique http index, loopback destination/connected peer,
daemon PID/start/boot/source and monotonic enter/send/response/finish, plus raw
response/status/parsed JSON. Partial records precede transport and survive through
the existing sanitized exporter; no Authorization headers/bearer are exported.
Provider/controller/aggregate independently enforce [jobs-refuse-review,jobs-final]
then natural exit: no repeats, reads, blocks, mixed modes, extra commands,
arbitrary expected_exit or broad nonzero tolerance. Ordinary submits still
require exit0/201; ordinary callbacks exit0/200.

Before CLI, after CLI and after API, reopened read-only Store.cancellation_frame
captures BOTH orgs' whole relevant task/control/admission-journal/lineage/result/
job/audit/thread rows and product file hashes; full job/output bytes are retained
separately. Files are observed outside the SQL transaction, with honest enclosing
intervals. Only current-task last_heartbeat AND updated_at may jointly move
strictly forward as valid aware timestamps, updated_at>=last_heartbeat; all other
content/membership is identical. Producer: queue._heartbeat ->
dispatcher.heartbeat -> db.tasks.update_task. No refusal audit exists; next_job_id
only reads. No audit/control/result/waiter/resume/job/file allowance is made.

The independently authored refused script's first effect writes a persistent
sentinel and uses the existing jobs socket with a bounded deadline. No sentinel
cleanup precedes absence proof. Immediately preceding automatic0 supplies the
real positive sentinel/socket/PID/start/output/audit control, without another job.
Bounded owned-descendant raw PID/start/parent evidence and intended CLI child
witnesses complement sentinel/socket/file/job/audit absence after each refusal,
closure and E04 final drain; unknown ownership fails closed. A passive provider
counter or422 alone cannot establish no launch.

Original SID's last CLI reports completed/done/confidence90 with truthful summary
`E04_REVIEW_REQUIRED_REFUSED no job submitted`. One positive INTEGER result,
ordered session_start/end/completion_report/orchestration_step1, no blocked result,
resume or second generation, actual task/list/Recall and e04-task-1.png remain
required. No settled anchor is taken while it is live; original15s case cap stays.
All replacement native GREEN/RED/sensitivity/mutation/restoration/repetitions/
protected12/full-suffix100MiB proof are ABSENT/UNEXECUTED; prohibited proof stays
prohibited. This source delivery is not complete test-authoring or product PASS.
Historical source/design digests below remain provenance, expressly amended by
Founder303 and accepted R1–R3; they are not relabeled as the replacement design.

The daily controller now runs the unchanged baseline23, then Pause19, then
`jobs.exercise`'s six serial variants: **48 authored variants**. E04 source
reuses accepted design SHA256
`14cf2584b91514bfaee04e59f23f862429e2c865179c298167e88df7b9b7cdf1`
and composition SHA256
`68f4a10b8822fdf109cea03949d1babb1105210004184bb98a2486294f3e1eb9`.
PR1036's merged limits supersede the old designs' internal240 baseline limit.
The common controller/watchdog deadline is min(entry+480, original720);
per-case limits20/15/20/25/25/25 and read/socket caps only shorten it.
Other commands/diagnostics240, cleanup60/export120, target600/hard900 and
hosted15min remain. Fit, closure and sanitation behavior are UNPROVEN.

The preserved baseline closes at9 providers/alpha6tasks8results/beta1/1.
Pause archives bootA12, binds the actual same-files old/new daemon identity,
then closes bootB6/cumulative18 with alpha13tasks17results/beta1/1.
E04 before/S1/final bootB checkpoints are(6,7,8), refusal(pre8,final9),
(9,10,11),(11,12,13),(13,14,15),(15,16,17). Final cumulative29=A12+B17;
alpha19tasks28results/beta1task1result, seven jobs/seven script starts:
three completed0, three completed7, one failed/timeout/-9, zero rejected.
E04 retains27 anchors and54 provider commands: eight submit attempts (one
refusal), five blocks, six finals and35 reads. E05 follows at53/36/B24.

For every row below: level is real public CLI/browser/durable E2E;
**test-only production seam: NO**. Only the external owned provider and
submitted deterministic Python scripts gain synchronization. No DB seed,
internal enqueue, fake SID, production import, registry bypass or product edit.
Each credible mutation is an unexecuted sensitivity proposal, not authorization.

| Named case / contract and action → literal oracle | Credible regression | Nearest coverage and missing seam |
| --- | --- | --- |
| `E04.approve-exit0`: provider CLI submits false cap30 and observes automatic owned running; blocks/exits/parks; browser verifies no-run after S1 settlement; owned script released after running readback → completed/0/NULL, exact E04_OK streams, submitting-agent auto-start/terminal audits; natural live S2 reads then final callback/exit/done. | Hidden manual start requirement, wrong start actor or lost terminal wake. | Baseline task completion does not submit or run a job. |
| `E04.review-required-refused`: exact local CLI exit2/empty stdout/stderr/no wire, separate identical-byte authenticated public422; coherent no-effect frames, owned processes and automatic0 positive control; original SID completed/done and real task/Recall. | True guard moved after allocation, wrong error, launched script or lost same-SID completion. | Automatic admitted-job wake and layer-focused Web tests do not own dual refusal/no-effect/one-generation continuation. |
| `E04.completed-exit7`: real cap30 Python exits7 → completed/7/NULL, exact E04_EXIT7 streams, completed audit; CLI/receipt/browser and final summary explicitly preserve nonzero. | Normalize exit7 to0 or label natural nonzero failed. | Ordinary provider exits do not cover job runner return codes. |
| `E04.failed-timeout`: real cap5 direct script ignores TERM and is never controller-released → product timeout/KILL, failed/timeout/-9, exact streams, duration5–17s, PID/start gone, empty PGID and socket EOF before outer cleanup, one natural S2. | Omit cap forwarding, discard reason, or accept terminal row without process closure. | Launcher TERM/INT diagnoses harness cleanup, not product job timeout. |
| `E04.two-jobs-A-first`: A completed0 while B remains running across durable/audit/CLI/browser and two +0 settled checkpoints; B then completed7 → one S2, both literal outcomes, two results/count2, no third provider. | Coordinated all→any producer/admission predicate or lost claim fence. | Single-job wake and ordinary child joins do not cover job fan-in. |
| `E04.two-jobs-B-first`: B completed7 first, A running through the same checkpoints, then A completed0 → identical final truth with reverse triggering-job ID. | Asymmetric terminal ordering, premature wake or duplicate return. | A-first alone cannot establish order symmetry. |

For both pair variants, the independent first-terminal snapshot consumer binds
exactly the two submitted IDs to their task/agent, checks the planned first
job's literal completed exit0/7 and the other raw running row with no terminal
fields, and retains the unchanged blocked S1 result/current SID/wait IDs,
no-resume audit and both +0 anchors before the second action. The existing
live-peer observation corroborates the running row; no pending row is fabricated.

The following job-read, live-S2 and two-generation contracts apply to the five
admitted-job journeys only. The replacement has one provider, no allocated job,
no S2 and no job reads: exact local CLI refusal precedes the separate same-file
public API refusal, then the original SID's final callback. Individual legacy
pending-job rejection and natural waiter wake remain retired only for this slot.

`provider.jobs_command` invokes the bound CLI as a genuine provider child,
using the real issued task/session. S1's final CLI action is blocked callback;
S2 runs show/receipt/output/tail/wait, then exactly one completed/done callback
as its final CLI action and naturally exits. Read-command exit0 never means
job success. `Controller.provider_cli` retains command/argv/child PID-start/
parent PID/source/interpreter/payload/HTTP receipts. Existing org discovery is
retained and reconciled without assuming one request per read. For each job in
the five admitted journeys, `validate_e04` requires the shipping method, exact
alpha/job path,
query (including genuine S2 binding), literal HTTP200 and actual body: human
show GET detail, JSON show/output GET receipt, tail GET and wait POST. It
compares full detail/wait rows (including the captured output cap), the closed
receipt identity and exact output/tail bytes against raw durable evidence;
CLI JSON/text must agree with those responses. Discovery-only or error wire
cannot satisfy a job read. `jobs.exercise` and the aggregate both require the
first human-show line to equal returned ID, literal status and created timestamp;
a completed/failed word in the title cannot substitute. Independent literal
exit7 and timeout/-9/reason lines remain required. The replacement requires the
exact local refusal stderr and separate API refusal body, with no rejection-reason line.

The live-S2 frame retains source, actual provider identity/PID-start and
snapshot entry/finish times. The aggregate requires one in-progress task bound
to S2, exactly the unchanged blocked S1 result, literal terminal job rows and
the natural S1-consumption → wake → S2-start audit chain, with no S2 end/result.
Its snapshot must precede all actual S2 reads and the final callback, and its
result/audit rows must reconcile with the final durable snapshot. Missing or
contradictory live frames fail. Completed-only
`Store.result` is not used for S1; `Store.jobs_parked` and `jobs.final_task`
inspect literal blocked/completed results and original historical wait IDs.

The three E04 consumers (`jobs.final_task`, aggregate live S1/natural S2 and
aggregate final two generations) bind distinct positive INTEGER result IDs
and genuine result SIDs to original callback/provider/session_start identities.
Ordinary session_end and completion_report audits emit no SID: exact counts,
unique audit IDs, task/agent envelopes and ordered start < end < consume slots
supply their generation join. Status, confidence90, original summary, null
verdict and blocked-null/final-done decision must match the accepted result and
original public callback. Only closed NextStep null optionals and empty
then/children defaults are accepted. The final orchestration_step has actor
orchestrator and its distinct exclude_none done representation after S2 consume.

`final_task` captures one genuine final snapshot, attaches its sanitized copy
to the existing durable record as partial/unvalidated before assertions, then
validates the same original object. Failure preserves that frame and propagates;
no replacement read or continuation is allowed. The independent aggregate
reconstructs live and final cardinalities/semantics/wake maps from original
commands and parked/live/final rows, checks the unchanged complete S1 result
and every retained audit by unique ID/full original payload, and ignores maker
validation labels. Live S2 has only S1 end/consume/result and no done step;
final has both generations and exactly one original done step. Missing or
contradictory evidence fails daily health despite success markers.

`jobs.pending`, `run_script`, `terminal_oracle`, `final_task`, browser job methods
and the six records map actions to literal independent SQLite/file/process/UI
oracles. Script tokens and hashes are distinct from genuine task/SID/PID joins.
`jobs.anchor` retains two fresh settled public metrics projections and all
intermediate samples, with exact admission/release/publication counts, ordered
newest-first tails including duplicates, no eviction and no unexplained reset.
S1 fully consumes/parks/publishes/releases before browser mutation; no S2 host
receipt is required while S2 is still live. `Controller.observe_providers`
continuously records socket readiness and executable witness counts across
bootstrap, both boots, inter-case gaps and final drain. Its failure or missing
observations fail health. This is serial causal attribution under the closed
synthetic workload; host summaries contain no SID/PID/unique receipt identity.

Evidence/control authoring records (all four answers; production seam NO):

| Named fixture/control or consumer | Contract / assertion mapping | Credible regression; nearest coverage/gap |
| --- | --- | --- |
| Provider jobs protocol + wrapper child witness | `jobs_command`/`provider_cli` order absolute child/exchange deadlines with one-second receipt delivery room, append partial timeout evidence before refusal, bind actual child PID/parent/argv/source/SID and enforce block/final last-CLI rule. | Equal caller/child timeouts lose streams/identity; controller impersonation or post-final CLI. Exit-only provider lacks these command/evidence boundaries; persistence and sensitivity are unexecuted. |
| Owned submitted-script socket/sentinel | `pending`/`run_script` bind exact script/token/job to SO_PEERCRED, E2E_ROOT, PID/start/PGID, natural EOF/closure and untruncated streams. | Wrong automatic launch owner/peer, controller substituting timeout; baseline provider socket is a different process family. |
| Passive observer + boot/window records | `anchor`, passive Pause archive hooks, unchanged baseline/Pause boundary assertions and `aggregate.validate_e04` recompute A0→9→12/B0→6→17, admitted journeys' S1/final +1/+1, refusal's single +1 and both pair +0 checkpoints, full tails/counters and observed source identities. E04 retains54 provider commands and seven jobs/seven scripts. | Missing/reset/evicted window, extra attempt without provider, duplicate summaries deduplicated, receipt-before-S1/after-S2 confusion; old aggregate only checked metadata. |
| Browser jobs fixture (O1–O7) | `Browser.response` binds armed Request/document scope and captures opt-in JSON inside the original deadline; `job_action` captures actual unchanged-cap body, status, dialog-open no-launch; `job_terminal`/`jobs_waiting` read fresh shipping detail, output, Recall and screenshots. | Wrong dialog/action, stale outcome or exit7 laundered; existing Browser covers only ordinary tasks. |
| Actual-content daily aggregate (J1.b/J1.c) | `validate_e04` independently reconstructs genuine result/start SIDs, closed semantic completion/defaults, exact live/final lifecycle and full retained history; reads observations and browser events, resolves records and screenshots, recomputes source/boot/counter/CLI/job/audit/process/result joins. Five admitted journeys retain all five method/path/query/HTTP200/body/stdout/durable reads per job, source-timed live-S2/blocked-S1 chronology and exact human-show first header. The one-generation refusal instead requires exact CLI/API refusal, raw no-effect and original-SID final evidence; all reject empty/hash-only/disagreeing content. | Discovery-only/error/contradictory job wire, missing live S2 in an admitted journey, title masquerading as status or forged PASS/count-only content; metadata controls cannot validate these joins. Dedicated synthetic E04 aggregate fixtures are not authored or executed; sensitivity remains MISSING. |
| Existing metadata `controls` fixture with56 manifest entries | Existing `validate` controls retain the missing/failed/skip/source/cleanup/timing envelope against the current56-ID manifest (historically48 after E04). They are explicitly metadata-only; daily main additionally requires actual-content validation. | An omitted E04 name slips past stale manifest; old42-ID fixture covered baseline/Pause only. No control was executed for this delivery. |
| Failure export / redaction context (J1.a) | `final_task` publishes its single sanitized partial final frame before assertions; `Controller.sanitize` + private retained old/new bearer context feed `run.collect_evidence/redact` before observations/scenario/browser-event/failure export; PR1036 unknown-context withholding and reserves remain. | Old boot secret survives final-token-only redaction, or failure loses partial chain; original exporter retained only final bearer. |

Consumers are `Controller.mark/durable/evidence` → observations.json and
scenario.json → run.py result.json/sanitized export → existing five seven-day
artifacts → aggregate.main/validate_e04. Partial records and CLI attempts are appended before
success marks. R1 uses a single absolute exchange deadline set by the controller
before send: min(now+10, case end, common scenario end). The provider's absolute
subprocess deadline is one second earlier; manifest/payload/spawn work consumes
that same allowance, and communicate uses only its remaining time. A call with
at most one second remaining refuses before dispatch. Socket send/read consume
the original exchange end, never a fresh ten seconds. On subprocess timeout,
the provider returns actual child identity and partial streams with explicit
timed_out/non-success before raising; the controller appends that receipt before
asserting success. The one-second delivery room covers serialization/transport
inside the existing cap, not cleanup/export reserves. Existing sanitized
finally export carries the partial receipt and executable witnesses, even
without a final case record. Unknown secret context still withholds unsafe text;
OS scheduling/transport loss or watchdog termination is not a persistence or
runtime-fit guarantee, and delivery/sanitation proof remains UNEXECUTED. Cancellation uses the
preserved exit-only provider and independent diagnostic shape; it does not
require an E04 case or its content validator. The outer Owned mechanics and
all old/current units remain unchanged; recorded job peers join its existing
PID ledger without discarding earlier peers. Exhausted cleanup fails visibly.

Delivery verification is SOURCE/CONFIGURATION/STATIC ONLY. New cases, controls,
assertion sensitivity, timing fit, cleanup/sanitation, all protected12 deliberate
mutations, RED/restoration/GREEN and repetitions remain **MISSING/UNEXECUTED**.
No harness/product/provider/browser/job execution or new aggregate control run
is authorized here. Historical daily failures were observed; corrected daily behavior is pending;
that collector is untouched. General integration SKIPPED THR243seq42; Python
units RETIRED. Daily19UTC, Python3.12/3.14 plus TERM3.12/INT3.14 and only
Source Boundary Lint/Web Lint(Node24) merge contexts remain. No deployment,
Jenkins, compatibility/authentic-memory PASS, E05 or program completion follows.

## E05 threads and replay: authored source only

The accepted TASK10637 design (`832d218b693b6e6f660a2f4ca484f498cf452878ae5f0e092d278986d0924f40`)
and TASK10653 composition addendum govern four cases/five variants. The entire
23 baseline +19 Pause +6 E04 variants, with Founder303's slot44 amendment, run before E05, giving53 source
variants. E05 adds three public-created threads, eight messages, six genuine
thread invocations and one genuine dispatched task/result: seven providers.
C/N/D settle at bootB20/21/24; archived bootA12 stays separate, cumulative36.
Final alpha20tasks29results/beta1task1result, seven prior jobs/seven scripts,
B18task+6thread summaries and four settled delivery pairs are separate oracles.
These counts are source expectations, not observations.

| Case / variant | Contract and literal oracle | Credible regression (unexecuted) | Nearest coverage and distinct gap | Production-only seam |
| --- | --- | --- | --- | --- |
| Compose / `E05.compose-race` | Shipping dialog, same synchronous Enter/click/click batch, one actual POST/200/committed opening/detail entry | Duplicate POST/thread if submit guard is removed | Retained Web write-path mocks lack real SPA/proxy/SQL join; eligibility alone cannot prove guard reentry | NO |
| Reply / `E05.reply-natural` | C has two messages, M1/W1/W2 genuine tokens, claimed1..1/1..1/2..2, one consumed/two declined; natural closure | Lost wake, duplicate reply or unsettled delivery | Retained thread integration lacks this exact public/browser/durable closed workload; baseline task replay uses different authority | NO |
| Dispatch / `E05.self-dispatch-followup` | D has one source-linked NULL-parent Engineering task/result, system2/reply3/system4/followup5; no replacement | Duplicate dispatch, lost followup, recursive task | Retained followup integration has broader seeded cardinalities, not this finite public-created venue | NO |
| Denials / `E05.consumed-token`, `E05.nonparticipant-task-send` | Same live M1 replays the same file for409 consumed; actual live task SID sends to worker-only N for403 not_a_participant; coherent before/after content unchanged | Removed/reordered token or participant gate permits another message/effect | Baseline E03/E06 covers task callbacks, not thread invocation replay or this participant boundary | NO |

`thread_provider.py` spends only its delivered token through its own bound CLI
and private payload file; the task plan uses its own real task/SID. Controller
instructions carry finite literals, never replacement authority. SHA256 token
aliases, actual socket PID/start, genuine CLI child/source/argv witnesses and
read-only durable identities remain distinct from anonymous host summaries.
Private files are removed before normal provider exit. No DB writer, product
serializer oracle, altered roster, stub/registry bypass or provider substitution
is used. Same-thread nonparticipant reply authority is not publicly constructible
here; the actual task-session send denial is the named boundary exercised.

The E05 browser context uses its own registered loopback P, forwarding to the
actual daemon D; existing CLI relay L and original browser context remain.
Only the first exact compose request's original200 response can be held after
an independent commit, with one arm deadline at most5s. Ordinary content and
SSE are forwarded as bytes, without bootstrap/auth substitution or manufactured
responses. Partial evidence includes actual network/navigation/eligibility
records. Disabled controls do not prove handler reentry or causal guard
sensitivity. Owned context, listener, connections and workers close before daemon
teardown; missing/unknown closure fails daily health.

The forwarding loop checks actual HTTP response completion before touching the
saved upstream socket again: `read1` can close that socket while returning the
final nonempty Content-Length chunk. That chunk is still written and flushed in
full. Unknown-length and chunked bodies continue through EOF or terminal chunk,
and SSE retains streaming reads, deadlines and visible transport failures.

The changed fixture/consumer contracts are: controller plan/peer/source registration
must reject unexpected authority (nearest E04 task-only plan); the provider's
finite CLI protocol must preserve same-file replay and final-callback ordering
(nearest jobs protocol); Browser/BrowserRelay must preserve real bytes and the
original held response (nearest baseline CLI loss relay cannot serve a whole
browser origin); Store.thread_frame must retain one reopened mode=ro/query_only
transaction across all affected tables and file manifests (nearest separate
Store.query calls are not a coherent negative frame). Credible regressions are
respectively wrong-owner callbacks, replay-file substitution, response fabrication
or loss, and false unchanged-state evidence. Every one has NO production-only
seam. These are external fixture/source assertions, with sensitivity unexecuted.

Controller observations and scenario references flow through unchanged run.py
result merge/sanitized export into the four execution artifact directories.
`aggregate.validate_e04` validates the amended29-provider checkpoint from its
actual retained witness prefix; `validate_e05` independently opens the E05 browser
artifact and actual observations, recomputing literal messages, claims, denials,
source/CLI joins, ordered anonymous tails and closure. Missing/empty/hash-only
or contradictory references fail. The aggregate's regression is false healthy
reporting from metadata/totals; nearest E04 consumer lacks thread/proxy content.
The manifest adds exactly five IDs, preventing an omitted E05 case from passing.
NO production-only seam is added by either consumer. Validator controls are not
product defect proof; no new controls were executed.

The R1–R3 correction extends these same four-answer records, without a new
proof framework or variant. All source predicates below are authored; dedicated
contradictory-receipt/browser-ledger/sanitation controls are **UNAUTHORED** and
assertion sensitivity remains **UNEXECUTED**.

| Existing case or fixture/consumer | Required contract | Credible regression rejected by named source predicates | Nearest coverage / remaining gap | Production-only seam |
| --- | --- | --- | --- | --- |
| Compose / `E05.compose-race`; Browser snapshots and `BrowserRelay` | Complete chronological event ledger, exact held/released prefixes and actual proxy release interval; zero premature navigation, one automatic entry, four paired intentional revisits; actual initial GET/messages and rendered opening | A premature event omitted from a case snapshot, duplicate automatic navigation or empty/wrong opening fails `R2` prefix/navigation/projection/bubble checks | Earlier PNG and snapshot membership checks did not validate chronology or rendered semantics; same-tick eligibility is retained, guard sensitivity UNPROVEN | NO |
| Reply / `E05.reply-natural`; provider and aggregate | Every decline/reply equals its canonical receipt and original instruction; exact argv, request, route, status, selected response, exit and shipping stdout; both literal C messages in their own sequence bubbles | Accepted SQL state with exit1/KeyError on decline, wrong endpoint/body, detached receipt or omitted rendered reply fails `R1` outcome and `R2` bubble checks | Earlier identity/source/terminal-row joins did not prove positive CLI delivery or actual rendering | NO |
| Dispatch / `E05.self-dispatch-followup`; N/D compose, task read and final callback | Full founder compose command/wire joins; exact dispatch and replies; D2 GET returns this completed task/result/summary and prints literal details; callback remains last; full D system payloads and distinct rendered reply/summary | Wrong task/details summary or an absent D reply hidden by the completion system summary fails `R1` task/read and `R2` per-bubble/system checks | Earlier two-substring D check and selected-response presence did not establish all literal content | NO |
| Denials / `E05.consumed-token`, `E05.nonparticipant-task-send`; Relay E05 request capture | Exact original instruction/real409 or403/request/response/error stdout, same retained file/live owner and coherent unchanged frame | Another endpoint/status/body or detached denial cannot pass by retaining valid durable rows | Existing negative frame checks are retained; dedicated contradictory artifact controls remain unauthored | NO |
| `thread_provider.redaction_context/command`; Controller sanitation/export closure | Validate private retained context before and after capture, including current/old bearer and invocation tokens; sanitize full binary streams before16384-byte clipping or JSON/socket output; original byte counts/flags, safe digest identities | Boundary-straddling credential, multibyte output, timeout suffix or missing/unsafe/lost context cannot take the old clip-before-redact path; unknown context raises fixed text and withholds output | Existing full-string exporter cannot repair a fragment already clipped by a producer; sanitation and timeout behavior remain unproved | NO |
| `aggregate.validate_e05`, threads command/compose/settled producers, browser response/DOM producers | Reopen existing artifacts and recompute actual command, public response, full ledger and rendered content joins; PNG signature proves file format only | Metadata-only or valid durable rows cannot substitute for public outcome/content | Extends the existing actual-content consumer; no executed controls or full test-authoring PASS | NO |

The CLI relay captures request JSON/response text only after E05 starts; the
original baseline/Pause/E04 wire receipts keep their prior shape. The controller
sanitizes retained observations with the complete credential context; the
unchanged launcher keeps its unsafe-export refusal. Binary provider capture
preserves original stdout/stderr byte counts and per-stream cap flags. Complete
secrets are removed before clipping; timeout capture also withholds a trailing
credential prefix, and UTF-8 clipping drops incomplete codepoints. Private
context failure emits only a fixed error and no captured output. Token/payload
digests remain identity evidence, never credentials or sanitation proof.

All E05 behavior, assertion sensitivity, proxy compatibility, guard reentry,
token sanitation, delivery/persistence, process closure and measured budget fit
remain MISSING/UNEXECUTED. Protected12 deliberate defects, RED/restoration/GREEN
and repetitions remain unauthorized/unexecuted. No full test-authoring PASS.
C18/N8/D25/final4 share the existing480 scenario allowance clipped by original720;
cleanup60/export120 and hard900/target600 are unchanged. Arithmetic is not fit
proof. Daily19UTC source binding and all four execution artifacts remain unchanged.
General integration is SKIPPED; Python units are RETIRED. TASK10555 owns the
first-daily collector and Pause acceptance. Later cancellation/late-callback/
restart coverage and eventual daily acceptance remain root-owned and unproven.

## E07 C source increment — TASK-10773

Accepted TASK10763 design
`5e9f7f401389841748b0e018ff9988e126f7a2511dabcb140d19ee2e6dc580a7`
adds C only after the fully evaluated and archived E05 end. Exactly three
variants bring the authored total to56: `E07.cancel-cascade-browser`,
`E07.cancel-terminal-unchanged`, `E07.cancelled-late-cli`. These are source
assertions, not observed behavior. R/full composition remains HELD; no crash,
new boot, credential rotation, or dormant restart implementation is included.

P publicly delegates to W using its real bound provider CLI, then exits
naturally. The one INTEGER parent result is consumed as delegate. W writes
its own bound final payload and launches the actual hash-bound CLI wrapper.
A dedicated one-shot relay captures the complete original completion POST
before upstream transmission; provider acknowledgement precedes communicate.
Live PID/start/PPID/argv/hash and actual /proc socket inode/FD/TCP tuple join the
issuer to that request. Before Cancel, W then P each retain real goto/reload
and actual200 detail/Recall tree reads. While each group is OPEN, its unique
visible task-owned h1 supplies identity/status/qualifier within the ORIGINAL4s
and group deadline. P requires own identity, `in_progress`, literal
`· Waiting on child work`, and sealed public in_progress/delegated with
child_wait.state=waiting_children. W requires own identity, `in_progress`, no
waiting qualifier, and sealed public in_progress/null block_kind/null child_wait
with parent_task_id=P. The independent aggregate checks these exported own
headers and public/tree identities; sibling/Recall/chrome text cannot satisfy
the header. These sequential views make no simultaneous-snapshot claim.
The browser Cancel dialog sends `E07_CANCEL_TREE`.
Product teardown must close W, its CLI and launch root before Fcancel; cleanup
cannot satisfy the case. The original retained POST is then forwarded once to
boot B: actual409 `task_not_active`, cancelled true. Dead downstream EOF and the
upstream response are distinct; no killed CLI response/exit is fabricated.

| Case or changed evidence consumer | Contract | Credible defect | Prior gap | Shipping seam |
| --- | --- | --- | --- | --- |
| Cascade browser, SQL/public frames and receipt assertions | Two cancelled tasks, exact shared timestamp/note/two founder audits; P result retained, W result absent; real terminal UI; one active cancelled receipt, no queued cancellation | Wrong cascade/status, lost result, result race overwrite, surviving child or parent resume | Original53 and launcher interruption do not cancel a live public product tree | Real create/delegate, CancelTaskDialog/cancel route, run-step/host containment, read-only SQL and public detail/recall/tree/list |
| Terminal-unchanged and command ledger | Actual P and identified completed-baseline cancel commands exit1 with exact409 and human message; full relevant content unchanged | Duplicate audit/timestamp/result mutation or wrong guard | Callback replay and Pause refusals are different routes | Shipping `happyranch cancel` and public cancel route |
| Delayed CLI, provider/relay/socket fixtures and unchanged window | Original real W POST forwarded after cancel/closure; exact409; full1s fresh observations, <=50ms interval, no extra admission/result/consumption/revisit/peer | Copied SID masquerades as issuer, pre-forward leak, wrong auth denial, late resume or result | Existing committed-response loss delays a response, not an issued request | Genuine executable provider/CLI, loopback HTTP, task-active fence, host metrics and coherent SQL |
| Prefix/export/aggregate consumers | Exact53 prefix, genuine E05 byte/record offset, entire suffix classification; raw/observations/scenario/result agree; independent actual-content recomputation | Dropped evidence field, count-only PASS, accepted unaccounted callback, truncated prefix | Former aggregate assumed final37 and whole E05 witness equality | Four daily execution artifacts and source-bound independent aggregate |
| Sanitation/partial process records | Retained bearer and E05 contexts redact whole captures before clipping; unknown context/caps refuse full-content claims; launch evidence survives client death | Secret fragments on timeout/EOF/signal or fabricated posthumous return | Prior synchronous success receipt cannot describe a killed CLI | Private launch receipt, real Popen/socket streams and existing unsafe-export refusal |

R1–R5 correction contracts (TASK10778):

| Changed assertion/fixture/consumer | Contract | Credible defect | Previous coverage gap | Actual seam |
| --- | --- | --- | --- | --- |
| E05 aggregate transport | Original daemon/CLI/browser D/L/P keys and registered ports only; independent E07 listener/upstream predicate stays in E07 | Valid E05 fails on nonexistent E07 keys | Misplaced predicate disagreed with unchanged E05 producer | `threads.exercise` serialized transport |
| Browser cancel and independent aggregate | Full literal `{ok:true, task_id:P, cancelled:[P,W], killed:[{task_id:W,agent:case_worker}]}` with exact order and membership | Reject real successful200 or accept incomplete response | Prior literal omitted ok/task_id | Shipping `tasks.cancel_task` and actual dialog POST |
| Both new stub witnesses | Closed seven-field records, exact Claude/source/stub/plan hashes, integer argc7 and ordered flags `[-p,--output-format]` | Arbitrary provider/hash/argc/flag or unknown-field corruption passes internal copy equality | Suffix counted kinds/source only | Task templates select fresh unpinned Claude; `ClaudeAdapter` seven-argument launch, original `fake_claude.sh` argv and unchanged executable guard |
| E07 ownership updates | Atomic temporary/replace union retains all earlier PIDs; register before process observations/waits | Watcher reads truncated JSON or failure loses an observed ancestor | New direct overwrites differed from atomic `Controller.start` | Existing `Owned.sample/observe` with unchanged PID-start/cgroup/unit checks |
| Private launch writer and final collector | mode0600 temporary creation/replace, initial PID record retained until full acknowledgement commits; each issued command requires a record; retain valid siblings and valid initial records with fixed missing/malformed/incomplete/unexpected diagnostics | Interrupted write destroys all later evidence or glob hides missing record | Unguarded comprehension and truncate/chmod publication | Genuine provider Popen and acknowledgement, `Controller.evidence`, existing run e07 whitelist/export, independent aggregate |

Launch collection parses each expected command-bound file independently. Failure
categories contain only fixed words and a command index; raw malformed bytes,
filenames and exception text are not exported. Valid partial records and the
failure list travel inside `e07` through raw/observations/scenario/result. Other
evidence is serialized before collection failures raise; aggregate rejects any
failure and independently requires both complete records in command order.
Sanitation remains outside parse catches: unavailable retained context still
refuses export, and incomplete records never fabricate an acknowledgement.
These source corrections add no dedicated control or execution evidence.

All dedicated sensitivity controls remain **UNAUTHORED**; behavior, assertion
sensitivity, transport provenance, delivery/persistence, fit, process closure
and sanitation are **MISSING/UNEXECUTED**. No full test-authoring PASS. Protected12
mutations/RED-restoration-GREEN/repetitions remain unauthorized/UNEXECUTED.
General and actual callback integration remain SKIPPED under THR243seq42.

Exact cumulative source expectations:38 starts=A12+B26,23 tasks/31 results
(alpha22/30,beta1/1),21 completed/two cancelled; only P adds an accepted result.
Seven jobs/seven scripts and every E05 thread/invocation/delivery count remain.
B26/25/25/active1 becomes settled B26, with P success then W cancellation and
full duplicate-preserving B24/B25 tails; queued-cancel delta0. E04 retains27
anchors and54 provider commands, E05 retains starts29:36 and its original
observer/witness boundaries. E07 commands/anchors are separate.

C partitions16+12+8+6+8=50 use fixed absolute ends clipped by original C/scenario
ends. The shared scenario remains min(entry+480,original+720), other commands
and interruptions240, cleanup60/export120, hard900/target600/hosted15min.
375+50=425 is unmeasured arithmetic. No borrowed reserve or shortened1s window.
Daily19UTC/default-branch Ubuntu Python3.12/3.14 and TERM3.12/INT3.14,
four seven-day execution artifacts and lint-only merge gates remain unchanged.

Source-observed successful live cancel-CLI printing mismatch (`killed` has
agent/task_id but cmd_cancel reads pid) remains unresolved and unexecuted;
positive cancellation uses the shipping browser. Retained CLI terminal-refusal
branches do not reach that print. E04 output-cap/persistent-stop, R/full
unchanged-bound disposition and all daily behavioral acceptance remain with
root TASK10680. TASK10555 retains first-daily/Pause acceptance; historical daily runs failed;
corrected behavior is pending. Authored, independently reviewed, merged, executed and
accepted are separate milestones; this is not completion of the reset.

## E07 restart hold restoration — TASK-10804

THR291 seq119 held R implementation and merge until root assessed actual
source-bound first-daily prefix/archive/close timings. PR1043 nevertheless
merged as `e4ba47ce3ab528447d7cded481f721dec07d175f` from reviewed
`80f7cf197ca27552627fe980b524341f155653e7`. Its source review, static QA and
lint did not satisfy that hold. This correction restores **56 active variants,
38 expected starts=A12+B26, 23 tasks/31 results**, retaining baseline23,
Pause19, E04six, E05five and Cthree. These remain source expectations, not
observed execution counts. The original Pause same-files **graceful** restart
is required and remains active.

The abrupt R provider command, daemon SIGKILL/recovery/C0 path, R subreaper
handshake and sole publication-gap exception are removed. There is no disabled
runner, placeholder PASS, skipped variant or extra cell. The manifest and
actual-evidence/export/aggregate requirements no longer require
`e07-c-prefix.json`, `e07-restart.json`, `e07-predeath.json` or `e07-restart.png`.
The aggregate again consumes complete final C38 observations and the entire
witness stream, preserving literal ordered A12/B26 receipts without an R gap.
Partial failures retain available sanitized C evidence and report missing
content as failure; they receive no completion credit.

The accepted TASK10763 original design
`5e9f7f401389841748b0e018ff9988e126f7a2511dabcb140d19ee2e6dc580a7`
and TASK10782 R addendum
`83d892a3e6eae329879ebf7be7a9dca47854f15f6bbe67be3811c4820e1fe184`
remain held design/history. The removed implementation and its four-answer
record remain available in [merged PR1043](https://github.com/t-benze/happyranch/pull/1043)
and that immutable merge's `e2e/restart.py` and README. R is unimplemented in
the active daily path and unproven; this correction does not release it.

Scheduling returns to `D=min(scenarios entry+480, original run start+720)`
with C capped at50 and its original16/28/36/42/50 partitions. There is no
D−150 admission, R75 or separate final25 reservation. Terminal close requests observer stop and stages small evidence before
independent browser/proxy/provider/daemon attempts within D, retaining
the daemon's at-most20s wait, at-most1s observer/late-transport joins and
at-most3s ordinary relay joins. Other commands
and interruption remain240, cleanup60/export120/hard900/target600/hosted15min.
No pre-existing operation cap or reserve is enlarged or borrowed, and no
measured-fit claim follows.

Shared deadline clipping remains necessary for the retained smaller caps:
Controller.effective_end/bounded/publish_end bind the original scenario and
case ends to provider sockets, generated job scripts, read-only SQLite,
transport and direct Playwright callers. The watchdog uses that same end;
C resets it to the original D after leaving its case, without a new allowance.
The aggregate checks each generated E04 end against its actual case and the
serialized scenario deadline, with no R allocation oracle. Browser.response
uses min(effective end at entry, entry monotonic + stored main-page cap)
(initial15, E04 at-most10, C at-most4) for action, nested native wait, opt-in
body retrieval/UTF8 JSON parsing and late-return checks. The original body RPC uses an owned bounded native task; explicit Python
phases enforce decoding/parsing with the same absolute timer owner. Short
SIGALRM never enters that native dispatch; no exit/body deadline is renewed.
E05's thread page retains10 and provider release retains its caller's at-most5.
The E04 task/Recall inner_text cap remains10. Full31s cache, both Pause windows,
all full1s/at-most50ms C observations, genuine callbacks and literal content
joins remain. Half-ready boot token/port capture, immediate ownership and
numeric PID collision refusal remain valid for bootA/graceful Pause bootB
and partial-failure sanitation; no R helper is needed by these owners.

### Retained C observation correction

TASK10801/step18's finite correction remains active. `e07.frame` stores full
raw task-list/detail/Recall responses and exact indexed GET/path/status200
HTTP joins. Cross-observation comparison copies validate the presence and
string type of Pause `observed_at`, parseable timezone-aware UTC and each
blocker's equal enclosing stamp, then exclude **only** `tasks[*].pause.observed_at`
or detail-envelope `pause.observed_at` and those projections' ordered
`blockers[*].observed_at`. Every other key, value, shape, array order, unknown
field and durable timestamp remains compared. Raw evidence is never changed;
there is no broad recursive normalization or clock-order assertion.

`e07.content/public_content/detail_content` applies this to C freeze, terminal
refusals, full window, final and browser joins. The aggregate independently
reopens serialized evidence and uses its own
`e07_public_state/e07_detail_state/e07_pause_state` validators, including exact
same-observation raw joins before comparison. SQL datetime serialization retains
the shipping UTC-Z mapping. Only the R-specific recovered-list producer and
consumer are removed. PR1042's actual-content/stub/private atomic launch,
ownership and partial-export corrections remain intact.

Four answers for these retained C cases (`E07.cancel-cascade-browser`,
`E07.cancel-terminal-unchanged`, `E07.cancelled-late-cli`):

1. Observable contract: real browser cascade, terminal refusal and the original
   late provider callback preserve exact state and evidence across fresh reads.
2. Credible regression: raw timestamp equality rejects unchanged state; dropping
   other Pause or unknown fields hides real state changes; an R artifact or
   reservation accidentally left required rejects a complete C-only run.
3. Coverage gap: prior C content predicates needed validated observation-only
   comparisons; historical source/static approval supplies no behavioral proof.
4. Actual seam: shipping task/Pause routes and real CLI/browser/HTTP boundaries
   feed full SQL/public/evidence captures and independent reopened aggregate.
   No production or test-only seam was added.

The accepted TASK12614/TASK12616 design and ROOT12610 step3 scope extend these
existing cases and A01–A16 keepers only. Four answers for the bounded recovery:

| Changed existing keeper | Observable contract | Credible regression | Nearest coverage/gap | Test-only production seam |
| --- | --- | --- | --- | --- |
| E07.cancel-cascade-browser own h1 capture/assertion | Own P waiting and W running headers agree with sealed public detail/Recall before Cancel. | Stale qualifier, sibling header or omitted child_wait check. | Locale tests lack the held real child/public/native boundary; extend the existing cascade. | None. |
| Shared observer at all16 transport sites | Old-source precommit initialization grants no new-document or API readiness; exact new Request/commit still required. | Unconditional precommit invalidation, or broad allowance admitting old/cached/foreign reads. | A01–A16/current/request/body/finish keepers remain; original3.14 exposed the positive ordering, negative sensitivity unproved. | None. |
| validate_e07 own-header and ID18 attribution | Reopened evidence independently distinguishes own live states and old-source/new-Request sequences. | Stale consumer label, missing attribution or precommit source accepted as new lineage. | Browser assertions cannot own independent exported-content checks; seven-query/body/finish/full-image joins remain. | None. |
| validate_e04/validate_review_refusal final records | Rendered uppercase headings, exact summary/completed and existing PNG remain required. | Title-case oracle against CSS-uppercase inner_text, or dropped content/image check. | Existing six E04 final keepers; textContent/accessible names observe a different representation. | None. |

Supplied run39758401569 remains native Health FAIL:53 recordedPASS EACH3.12/3.14,
interrupted E07 cascade and two UNREACHED EACH. Original3.12 stale label and3.14
arm2857/same-document2858/Request2859/commit2877 support the repair, but exact
initiating action/full refused URL remain inference/unproved. TERM3.12/INT3.14
and E05 relay PASS are historical run-specific; JOB5221 exit0 is COLLECTION ONLY.
Corrected native behavior is NOT RUN; negative sensitivity, protected mutations,
restoration and repetitions are PROOF NOT RUN with no allocation. No aggregate
rerun against old failed artifacts relabels them green. Missing final evidence
and browser poison remain failures. Static delivery is not product/reset
acceptance; later reviewed-main verification belongs to ROOT12610.

This is source-only restoration, not full test-authoring or behavioral PASS.
All behavioral/sensitivity/provenance/transport/persistence/delivery/sanitation/
closure/fit proof is MISSING/UNEXECUTED; dedicated controls UNAUTHORED.
Protected12/RED-restoration-GREEN/repetitions remain unauthorized/unexecuted.
General AND actual CLI callback integration remain SKIPPED THR243seq42;
Python units RETIRED. Daily19UTC/default immutable source/Ubuntu/Python
3.12,3.14 plus TERM3.12/INT3.14 and four7day execution artifacts remain unchanged.
TASK10555/JOB4257/Pause ownership is untouched; first-daily status requires its
owner's actual source-bound evidence. No extra collection/run/retry/schedule/
notification, production/schema/auth/permission change, deployment or Jenkins
work is authorized. Root TASK10680 remains INCOMPLETE and alone owns renewed
independent full review, scoped static QA, hold assessment and guarded merge.

### Owned rendered task bodies (THR-291 / TASK-11958)

`Browser.rendered` is a shared test-side observer used by ordinary task and
Pause readback. List checks bind the exact org/task row to its explicitly
expected headline and full raw `title`. Detail checks bind the localized Brief
section to its direct body. Recall checks bind each node's own header,
canonical agent identity, status, Brief and separate Outcome to the exact
recursive children, preorder and complete link inventory. The root summary
remains required through its own Recall Outcome; detail has no separate
Outcome section. Correct text in a sibling, descendant, other section or org
cannot satisfy an own-body assertion.

The observer requires one structural outer AppShell `main` (`main:not(main main)`).
On the list, it additionally requires one TasksPage inner `main` directly under
the shell's route-root div. Detail/Recall instead require the content column
inside ContentWrap's two div wrappers. Native readiness and the actual extractor
use these same owners; returned paths, visibility and expand/recollapse clicks
all stay relative to the outer shell. List locators are rebound after navigation.
Missing/duplicate owners or matching content only in shell chrome refuse.
E07 terminal list reload binds the unique exact parent href and its own first
direct status column's literal `cancelled` badge, never child severity rollup,
another row or chrome. Its text comes from the route list alone. The seven
original four-second operation caps, roots response/group and checkpoint18
sequence remain unchanged.

The finite body contract admits the audited plain paragraphs, the exact
`Delegated to case_worker (child=TASK-[0-9]+)` lifecycle paragraph (ASCII digits
only), and the exact prior-worker context generated for Pause's reviewer. That context renders as
three h2 blocks, five br elements and zero hr elements, with exact metadata
and spacing. Each br is followed by a standalone LF text node and then a separate
exact metadata text node; inter-block LFs also remain standalone. Unknown Markdown or malformed
context explicitly refuses. List titles have a separate contract: a multiline
headline example is list-only, not a supported detail body. Pause supplies
rendered expectations from known scenario IDs and states before observation.
Recall projects the current task note: both pausing and paused/post-restart
root Outcomes name the ORIGINAL worker, even after the distinct reviewer exists.
The initial public detail must retain that exact delegation note alongside the
accepted original callback result `PAUSE_RESTART_DELEGATE`, with its INTEGER
identity, task/agent/session and decision unchanged. Existing final audit joins
retain the callback summary and ordered consumption evidence. Final Recall
instead requires `PAUSE_RESTART_DONE`, with exact worker/reviewer outcomes.
No returned note, API summary or DOM value supplies the rendered expectation;
the prearm raw transport oracle remains unchanged.

Body-owned controls retain exact English/Chinese labels and character counts,
Recall's 240/detail's 600 thresholds, complete expanded content and identical
recollapsed previews. Native calls still require visible owned bodies, blocks,
headers, links, labels and controls and check the controller's remaining time
after synchronous operations. Before each one-shot snapshot (including after
expand/recollapse), bounded native auto-wait requires the exact list link and
its direct title, the own detail section's direct prose, or the Recall section's
direct root header link and expected own Brief/Outcome prose. The independent
roots/Recall queries can still be pending when main, URL or Recall heading is
ready. TaskRecallTree mounts the entire tree from one resolved recallNode;
a descendant link cannot satisfy root readiness. Strict final validation still
refuses malformed content, extra/wrong owners and incorrect recursive trees.
Existing action/group/checkpoint deadlines,
cache expiry 31s, org return, reloads and six accepted DOM-only sites are
unchanged. No baseline variant, product renderer or transport comparison is
changed.

Finite maker/QA controls execute the actual owner-resolving DOM expression on
fresh disposable jsdom fixtures, then the actual validators loaded from the
candidate AST. They cover generated/plain content, wrong ownership/tenant,
recursive statuses/order/links, malformed shapes and bilingual short/long,
full/recollapsed snapshots. These controls establish structural sensitivity
only. Additional AST-loaded actual-bridge controls with narrow Playwright
doubles establish readiness/extraction order and remaining/deadline handling
under simulation; they do not establish native auto-wait timing or visibility.
Affected shell controls use realistic two-main lists, two-div detail/Recall
wrappers and primary-status/title/lineage ownership. Scripted list/detail/Pause,
cache and E07 caller controls establish wiring only, not native orchestration.
Native Playwright bridging, React clicks, CSS visibility/layout/timing,
built bundle, sanitizer/export/screenshots and full Product E2E sensitivity
remain UNVERIFIED until actual evidence. Fixture transitions are not React
interaction proof or protected deliberate-defect proof.

Historical run39133417299 remains 33 PASS +1 partial/error +22 unreached EACH
Python 3.12/3.14; its one manual allocation is consumed. Scheduled run39166770569
at source29d743a2 remains 4 PASS +1 partial/failed E02 +51 unreached EACH: the
earliest failure expected one page-wide main but observed two on the list.
E07 and corrected Markdown success remain natively unreached/unverified.
No historical result transfers to this repair. No corrected daily or
overall test-authoring/product PASS follows. Protected12/repetitions remain
UNEXECUTED/unauthorized; integration INCLUDING actual CLI callback is SKIPPED
THR243seq42, and Python units remain RETIRED. Required automation stays
lint/static only; independent full review, same-head scoped QA and manager
guarded merge remain with the delivery root. TASK-11958/PR1129 is delivered
history; the bounded lifecycle-expectation continuation belongs to sole ROOT
TASK-12085. Scheduled run39336928907 at `2f7f158d` remains EACH27 PASS +1
partial/failed `Pause.browser-pausing-en` +28 unreached. Its reached own-body
content failure compared the actual delegation note to the callback summary.
Native corrected GREEN, paused/restart/Resume/final transition and downstream
jobs/threads/E07 behavior remain MISSING until authorized scheduled evidence.
Finite actual-source controls establish only their exercised observer/producer
boundaries, never full behavior or test-authoring PASS. No new E2E execution is released.
