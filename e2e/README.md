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
bookkeeping and original selected raw-primary final raise expression retain
their order: when reached, main raises the SAME first actually recorded object
with its saved traceback. Only the narrowly matched real module-entry adapter
then translates that raise to SystemExit; this deliberately replaces automatic
uncaught traceback output and selected special BaseException termination.
Calling main without its private production handoff preserves propagation.
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
reaching the final raise or terminal translation. An earlier file/frozen
projection can be stale; unavailable, null or stale metadata proves neither latest/complete cause nor local poison
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

These accepted rows cover only R1–R3 and their interactions; the L1–L7 memberships and validator maps above remain intact. IDs below are finite **design expectations**, not newly authored runnable tests, new manifest variants, or a campaign authorization. No new verifier/harness/venue/seam is proposed. Each observation is a shipping boundary (result.json, sanitized detailed members when admissible, controller final-raise/terminal boundary or actual owned cleanup observations), not a self-comparison with a helper.

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
| R2-01 nested first error | Action throws A; nested phase/bounded unwind and restoration throw B/C before finalization | Original selected raw A/traceback and its fixed first stage/category survive; later restored errors stay secondary in gated evidence. SAME raw object/traceback is preserved through final main raise; the selected terminal adapter below controls the subsequent exit, without asserting product cause. | C / B |
| R2-02 subclass/mapping | Known literals from every caller family, unknown dynamic name, UnicodeError/ValueError, TimeoutError/InterruptedError/OSError, RuntimeError with signal-like text, custom subclass and unrelated custom error | Closed stages and exact ordered built-in categories; no class-name/message projection or signal inference. Real signal receipt still owns signal identity. Final receipt contains only finite fields. | C+J / B |
| R2-03 reentry | Writer active, native callback or signal calls record_error; nested request attempts writer | Raw/first categories latch before I/O; second writer skips without lock/wait/recursion. Current snapshot may omit new latch; next existing boundary may publish only with positive original time. SAME primary object/traceback is preserved through main's final raise when reached; subsequent handling follows the selected adapter below. | C / B |
| R2-04 off-owner | Future nonowner record_error/writer request while owner snapshotting | First-winner memory only; nonowner writer skips/no enqueue/wait, no observer/relay dependency. Unpublished detail remains unknown. No new owner/thread lifecycle is admitted; off-owner skip behavior is unchanged; subsequent terminal handling follows the selected adapter below. | C / B |
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
|R2-01| Preserve the actually-selected raw winner A/traceback, including nested restore B remaining secondary, rather than infer an initiating assertion. Preconditions: completed record_error, safe existing paths/source and positive ORIGINAL effective end. The SAME earlier writer is tried, with the original conditional poison in its main-local finally. Keep poison ordinary-return AND secondary-throw branches; if publication succeeds, the private finite file and eventual frozen launcher projection record A's first category before poison, independently of poison success. A stays the stored primary and the original final raise raises SAME A/traceback when reached, before the selected terminal adapter below; the result remains nonzero/FAIL. An escaping writer still unwinds through the poison attempt; its completion and arbitrary later interruptions are not guaranteed. Refusal does not promise a file. C+H; J observes eventual safe result/noncredit, not proof poison ran. |
|R2-05| With raw A already selected, a usable unpoisoned Browser and positive ORIGINAL time, distinguish (a) metadata/pending/create/write/close/replace failure or absorbed alarm IN the protected writer body: existing handler makes sticky refusal and returns if its assignments/reset complete; later calls consult that sticky state, raw A/traceback survive and pending bytes are never truncated/exported; from (b) an interruption escaping writer entry, handler assignments or active reset: caller finally still preserves the original conditional poison ATTEMPT before outer finalization can consider native work. Publication/active/latch outcomes remain UNKNOWN wherever assignments were never reached; do not promise publication_failed, active=False, cleanup of pending or state repair, and do not alter/reset the writer. Original raw latches and SAME selected-primary final raise remain; the selected adapter below then controls terminal handling, subject to existing later interruption limits. Safe exported result is unavailable or a validated possibly stale projection, with original failure noncredit; that J output cannot prove poison executed. Expiry admits no new optional work or reserve; only the unchanged three calls remain. C observes actual propagation/poison attempt and resulting finalization; H observes real publication/refusal/inode outcomes; J observes frozen failure semantics. |
|X-01| Retain the original raw A/restore B/capture C/failed[] D/writer-failure/reader-collection-port-root-export failure chain. Retain the added earlier-SUCCESS branch: while ORIGINAL time is positive the moved call publishes, then poison/close/capture fails or later expiry prevents updates; frozen safe earlier projection may survive root loss but is stale/noncredit, full detail stays withheld, both cleanup flags stay false for required staging failure, fallback variants={} and native health failure persist. Incorporate D1 within these branches: a writer whole-call escape must still unwind through the original conditional poison attempt; never infer that attempt from J's unavailable/stale fields or promise active/sticky reset for unreached assignments. Raw A ownership and SAME final-primary raise remain before the selected adapter below; successful poison completion and immunity to repeated interruption/crash are not promised. C+H+J; no new journey, row or campaign. |

Four authoring answers for **every changed named row**, with complete shared-block membership:

| Block and exact membership | 1. Observable contract | 2. Credible regression | 3. Nearest keepers, precise gap and distinct additional-layer risks | 4. Test-only production seam |
| --- | --- | --- | --- | --- |
| A: R1-01–R1-11 | Exact optional result member/source/snapshot fields after cleanup and final receipt replacement; unsafe input never appears in artifacts; fixed unavailable/omission and original failure retained. | Admit duplicate keys/source drift/nonfinite value; follow/truncate alias or pending; call stale/null snapshot latest/complete; export raw rejected bytes. | Existing Controller first-error/private staging and run.failure_receipt are nearest, but no existing direct assertion owns the new closed category channel or hostile filesystem/JSON forms. Existing E07 finite launch-record reasons are a shape precedent, not a keeper for this schema. H sees actual inode/read/write/root-loss risks; J independently observes serialized admission/absence/shape and health noncredit, which cannot prove OS behavior. | NO for every listed case. Existing private runtime path and public exported artifact only. |
| B: R2-01–R2-08 | Controller SAME selected-primary object/saved traceback through the actual final raise, then the matched selected terminal handling below, gated detail and separate fixed first/context slots; earlier safe publication independent of poison success and original conditional poison ATTEMPT via main-local finally independent of writer return. No reentrant publication. | Publish during record_error; overwrite first raw/category on restore; recurse on writer failure; diagnose before[]; swallow invalidation/expiry; infer signal from RuntimeError text; equate body catch with whole-call protection and skip poison on entry/reset escape, or invent sticky/reset state for unreached assignments. | Keep phase/bounded traceback restore, record_error, capture's[]/bare raise, prepare_redaction and main's final-primary raise. Extend only R2-01/R2-05 for poison return/secondary throw and whole-call writer escape. Success/TERM/INT lacks these combinations; R2-08 expiry/constructor and R1-10 stale remain distinct. C observes actual poison attempt, primary propagation and resulting finalization; H observes real safe publication/refusal/pending and state uncertainty; J sees finite frozen fields/noncredit and cannot establish poison execution, unexported traceback or cause. Source inspection is not behavioral proof. | NO for every listed case. The private main handoff serves actual production module entry, with no test-only callback/export/injection/parameter/wrapper. |
| D: R3-01–R3-09 | Actual owner-first cleanup and independently observed ports/literal root, original bound transitions, final nonzero/FAIL, sticky both-ok false and variants={} fallback through later export; R3-07 also requires persisted final disposition to agree with terminal commit. | Keep one try grouping; let port failure skip removal; reset C; continue after timeout; clear staging failure after root absence or recollection; perform teardown in export; reintroduce a fallible deadline/alarm path after final replacement. | Keep Owned.cleanup/PID-start/unit/group/watcher gates, run.collection/failure_receipt, validate_timing and cancellation.validate. Existing TERM/INT success closes its diagnostic only, never failed baseline collection/isolation. No direct keeper asserts these new combined failures or the R3-07 final-commit ordering. H sees real removal/port/deadline/queued-alarm effects; J sees serialized failure laundering/empty variants and final disposition; neither layer replaces the other. Aggregate metadata controls reject false shapes but cannot prove cleanup execution or publication ordering. | NO for every listed case. No Owned implementation change or test-only seam. |
| E: X-01–X-02 | End-to-end selected-error precedence and frozen safe projection/noncredit across controller death/root loss/export, with full-context raw withholding. X-01 retains original chain plus earlier snapshot/later poison-close-capture-failure/expiry and caller-finally interaction; X-02 retains terminal disposition matching successful final replacement. | Locally correct helpers combine to skip poison on whole-call writer escape, erase first error/cleanup failure, refresh stale source, admit partial detail, treat unavailable/stale J output as proof poison ran, or rely on another write after a postcommit alarm. | Same B/D keepers and precise cross-process composition gap; extend X-01 only. C owns actual propagation/poison attempt/finalization; H owns actual publication/refusal/root-loss effects; J uniquely owns frozen failure semantics after source loss and can misattribute/launder failure despite correct local refusal. J cannot prove OS or poison execution or repair unknown active/latch state. Preserve12649 X-02 precommit/terminal commitment and queued-alarm semantics. | NO for both cases. Existing main boundaries/artifacts only; no new journey, verifier or seam. |

### E07 caller precedence (TASK12746 / accepted12722-12741, ROOT12738)

ROOT12738 step25 accepts the exact TASK12776 scope-finalization design
(37103 UTF8 bytes, SHA256
`2fd2a058e4297c3e8268069df95a70d23909c3d1c42d2c37263e0b75d376b22a`)
after independent TASK12777 **PASS — DESIGN ONLY**. TASK12778 extends ONLY
existing R2-01 and X-01 below under Founder THR291seq362. This replaces the
older body-catch-only failure-tail contract; the original thirty rows, SAME13,
L1–L7 exact ordered56 EACH3.12/3.14, sixteen shared browser sites and full
validator/control/admission maps remain. C/H/J retain their existing disposable
hosted Ubuntu environments, synthetic roots and process/port isolation; no
execution or new venue is allocated.

`e07.exercise` retains its original workload/body catch/final `save(c)` under
the original case, including offsets16/28/36/42/50, C50/G, all saves and marks.
The terminal wrapper catches the actual combined outgoing with `BaseException`
and saves its object/TB BEFORE recording or release, including finalizer-only
errors. Final-save B or failed[] C still supersedes body A or body-recorder D
locally; completed raw W=A/earlier P and its savedTB/primary/creator facts stay
independent and immutable. When save succeeds after recorder D, D is the local
outgoing. The added recorder cannot reconstruct an already-masked input or
repair an unfinished/refused creator. Saved suffixes plus genuine rethrow
frames remain; the exception object is not frozen. Tuple stores, reservation,
creator claims/seals and further signals remain interruptible.

Release requires BOTH the SAME live case object as the last authentic E07
phase end AND `type(c.timer_phases) is list and not c.timer_phases` immediately
before deletion. ALL positive, native and expired residual entries refuse.
Missing/malformed/unknown/interrupted proof retains the case and never enters
wrapper publication; only independent current-owner synchronization follows.
An absent case creates no owner, deletion or publication. Assignment before
registration/unmatched records refuse; an earlier authentic matching SAME
object qualifies only with the empty list. Numeric equality is no ownership
proof. Nothing clears/copies/renews phases, repairs generic timer enforcement,
or guarantees release of an unregistered owner.

This is RELATIVE protection against deletion-caused widening under ordinary
sequential source-created objects. Controller.phase alone appends on the main
thread; the sole main caller follows returned threads. Between successful
built-in-list proof and ordinary deletion there is no appender/callback, browser
call, await or yield. Raising signals abandon the branch. This premise must be
rechecked on source changes; it is no concurrency/atomicity/hard timing promise.
For the F1 before-effect phase-deletion continuation T<Cend<G, now>Cend, retained
Python/native T keeps expired authentic Cend installed, so unchanged main
prepare_redaction/Store refuse capture even if sync disarms T. Positive or absent
case timer-only admission weaknesses remain pre-existing completed possibilities,
not UNKNOWN and not cured here. After-effect deletion may leave an empty list
and conditionally permit release. No rollback or duration restoration follows.

Eligible deletion and existing publication/sync are independently attempted
when reached, even if save/recorder/deletion failed. Incoming outgoing/TB wins
locally across later tail errors when guards finish; without incoming, the
first completed tail assignment wins. A recorder escape may reach the outer
handler again but never retries publication. Normal success has ONE existing
publish_end/internal sync, no extra success sync/workload/save/mark. Failure of
proof never reaches that publication branch. Further interruption can prevent
completion or delivery; attempt alone proves no deletion/publication effect.

After E07 EXIT, authentic expired case plus empty phases and EVERY other
original effective bound positive may allow unchanged main full fresh BOTH-store
capture and remembered token union, with sanitized failure/scenario/browser
prefixes staged before fallible joins. This is conditional useful failure
evidence, no E07 retry/save/capture/reserve interval. Positive G alone is
insufficient; local/observation/inherited ends, browser poison and saved transport
caps still apply. Main's THREE publishers/conditional poison ATTEMPT, full
W/TB/H0/token/frame/head/claim joins, capture B→[]→B versus failed[]→C, readiness
and withholding stay unchanged. Prepare expiry alone writes no[]. No last-good,
bearer-only or raw rescue is allowed. Frozen category read, sticky collection
failure through root loss, original C60/E120/E-1, safe initial FAIL and terminal
committed replacement remain. Safe staged partial detail may survive with
FAIL/variants={}/false cleanup flags; it proves neither delivery nor closure.

Every following subinput extends BOTH R2-01 and X-01 and inherits its separately
named four-answer block. W is completed raw winner, savedTB its stored reference,
P earlier winner, A body input, B final-save/capture input, C failed[] input;
T/Cend/G denote timer/case/global ends. The FINAL observables are required
behavior, not an execution claim. C is actual local/controller propagation;
H is actual hosted process/file/timer/ownership; J is the independent frozen
serialized consumer in the SAME existing environment.

| Finite subinput: requirement / preconditions → action | Observable FINAL result → level |
| --- | --- |
| Normal body + save; authentic case; no residual → terminal tail | Identical workload/saves/marks; delete authentic case, one existing success publication/internal sync, normal return. J unchanged validators still require complete acceptance, not a wrapper success claim. C/H/J. |
| Body A; no prior W; save succeeds → unwind and eligible release | W=A/savedTB/creator facts; actual local outgoing A; main selected A when reached. Release only with both checks, otherwise original gate remains. C/H. |
| Body A, final capture B; [] succeeds → continued main | W=A/P, actual local outgoing B; original[] attempt precedes diagnostic latch, readiness not inferred. Empty/authentic release may allow NEW full main capture conditionally; residual+expired Cend refuses it. C/H/J. |
| Body A OR normal, capture B then [] fails C → unwind | Actual outgoing C/TB; W=A/P if already selected, otherwise C gets pre-release first opportunity. B not invented as W; context B slot proves neither invalidation nor readiness. Same conditional capture/refusal branches as preceding row. C/H/J. |
| Normal body; finalizer-only sanitize/serialize/write/SIGALRM B; no prior W → pre-release catch | Genuine B/object/TB/creator opportunity before deletion/optional work; B retained locally across secondary tails and offered to main. No initiating earlier masked input reconstructed. C/H. |
| Prior P; later body A/save B/release D → exit | W=P/savedTB/creator unchanged; local B if save fails, A if save succeeds; release D secondary. Main selected P only with real final raise/join. C/H/J. |
| Body recorder-tail D AFTER completed A/P; save normal OR save B → exit | Local outgoing D OR later B respectively, never silently A. Raw W=A/P. Added recorder/release errors cannot replace completed outgoing/TB. C/H. |
| Recorder interrupted BEFORE OR AFTER reservation; partial primary/claim/seal; callback reentry; SystemExit/KeyboardInterrupt/GeneratorExit → ordinary unwind | Only actual completed W/assignments protected, refused creator stays refused. BaseException retained; finalizer/release attempts when reached, no guaranteed delivery/repair. Partial unknown not used to dismiss a later completed unsafe path. C/H; J cannot infer private transitions. |
| Save error, secondary recorder/ownership/deletion/publication/sync errors → finish guards | Retain actual save outgoing/TB and W. Failed check retains case, no publish; eligible deletion failure still independently reaches publication/sync when reached; later errors cannot replace selected local outgoing. C/H/J. |
| No incoming throwable; proof/deletion D OR publication E OR sync F first, later errors → exit | First actual completed outgoing assignment wins, offered to recorder; final raise fails/noncredits even after marks. No fabricated rollback/owner recreation. Actual deletion-before/after-effect distinguished below. C/H/J. |
| F1 T<Cend<G, now>Cend; authentic expired case; Python OR native residual from interrupted phase deletion BEFORE effect → tail then main | Residual check refuses deletion. Same Cend remains installed, no wrapper publication, sync may disarm T. prepare_redaction/Store still refuse capture through Cend. Original outgoing and raw W unchanged; safe withholding/noncredit, not UNKNOWN completed continuation. C/H/J. |
| Residual Python OR native T still POSITIVE at check, expires LATER; authentic case expired → continued main after T | Same refusal even while T positive, case kept; main capture refused under expired Cend both before/after T. Filtering only currently expired timers is a regression. C/H/J. |
| Earlier phase deletion interrupted AFTER effect; authentic expired case, list genuinely empty, other original effective ends positive → tail/main | Absence succeeds; eligible case release can permit existing main full capture/safe partial staging as detailed above. Phase deletion error/outgoing/W remain real failures; no closure/PASS inference. C/H/J. |
| Authentic expired case, no retained phase after normal full unwind → release then full main capture | Concrete useful path: original A/P reserved, save B/C retained, case deleted, main three publishers/poison ATTEMPT unchanged; successful fresh BOTH-store union permits sanitized failure/scenario/browser prefixes before fallible joins. Missing members/failed joins still FAIL. C/H/J. |
| Ownership proof fails OR timer list missing/malformed/unknown OR interrupted check → continue | No deletion or widened wrapper publication; present case retained. Expired case refuses main capture; positive case retains its existing horizon. Original outgoing/W take precedence; no incoming makes real proof error outgoing. C/H/J. |
| No case: signal before FIRST phase assignment; pre-try prefix separately → exit/main | No case invented, no deletion/publication, sync existing owners only. Main capture subject to unchanged effective ends; absent-case timer-only weakness is pre-existing, no deletion widening claimed. Prefix before try remains outside wrapper. C/H/J. |
| Assignment before interrupted phase append; empty/unmatched last record OR earlier exact matching registered E07 end → release check | Unregistered/unmatched retains installed owner; earlier SAME live authenticated object can qualify only with empty timer list. No numeric guess or previous owner restoration. Actual caller does not bring a surviving foreign threads/jobs owner into E07; abstract unmatched data always refuses. C/H/J. |
| Eligible case deletion interrupted BEFORE effect OR AFTER effect → complete later handlers/main | Before: case stays, expired Cend denies capture even if publication attempted/fails; after: case absent with proven no residual, only other original effective ends gate capture. No claimed deletion on mere attempt; W/outgoing survive finished guards. C/H/J. |
| Case POSITIVE + residual T expired OR T positive-then-expires; also case ABSENT + residual → main | No release/wrapper publication. Existing effective_end may allow capture across T before Cend (or until G if absent); explicitly pre-existing limitation, not global cure, new deletion widening or UNKNOWN. No fake case gate to fix it. C/H/J. |
| G expired OR local expired OR observation expired OR inherited expired → eligible release/continued main | Original numeric ends unchanged; each effective minimum still refuses main capture, no borrowed C/E or renewed save/work. Any residual timer still prevents release independently. Prepare expiry alone does NOT write[]. C/H/J. |
| Publication interruption BEFORE write / pending write / AFTER replace / internal sync, or queued alarm during fallback/rethrow → completed tail | Actual publication effects only; no retry/rollback/duration restoration. Earlier outgoing/W survive completed guards; further interruption can stop delivery. Empty-list custody persists; main admission still uses actual remaining original bounds. C/H/J. |
| Capture invalidation B/C OR prepare-expiry-without[] OR withheld full context → main/collection | Preserve[] precedence and full-context predicate. No last-good/bearer-only rescue or category-as-readiness; unsafe detail withheld; original W/TB/first facts retained if guards complete. C/H/J; R2-06/07/R3-09 keepers. |
| Frozen reader stale/null/unavailable; collection fails then root lost → export | First frozen tuple not refreshed; sticky incompleteness survives real absence. Only actually safe staged bytes survive in memory; root loss proves no private state/closure. C/H/J; R3-01/08/09. |
| Safe partial staging then missing member/export precommit failure OR successful final replacement with queued alarm → terminal outcome | Actual sanitized staged detail can persist despite FAIL/variants={}/false cleanup flags. Initial safe FAIL survives no replacement; committed replacement terminal after original alarm retirement/E-1 check, no postcommit reclassification/rescue. C/H/J; R3-07/X-02. |
| Input masked BEFORE existing body catch or inside nested save/capture → outer/main | No recovered initiating object/cause; actual outgoing only. Existing main/frozen/withholding/noncredit semantics unchanged. C/H/J. |

Four answers apply separately to EACH R2-01 and X-01 subinput above: normal; body/save A/B/C/P; finalizer-only; recorder-tail/partial/reentry/BaseException; secondary/no-incoming; F1 expired/positive-then-expires Python/native; before/after-effect phase/case deletion; empty/residual/unknown/absent/unmatched/unregistered owner; case-positive/absent; global/local/observation/inherited expiry; publication/sync/queued alarm; capture/invalidation/withheld; frozen/root-loss/sticky export/terminal commit; pre-try/masked. Shared blocks name all subinputs rather than create parallel coverage.

| Row | 1 Observable contract | 2 Credible regression | 3 Keeper / distinct layer risk | 4 Test-only seam |
| --- | --- | --- | --- | --- |
| R2-01 EACH named subinput | Actual local outgoing/TB and immutable W/savedTB/creator through E07/main selected raise WHEN reached; pre-release finalizer-only selection; release ONLY authentic+empty; refusal retains expired-case main-capture denial. Relative positive/absent limits explicit. | Release before final save/recording; hide release behind save success; let recorder/tail overwrite outgoing/P; narrow BaseException; numeric owner guess; ignore residual/native/positive timers or insert a check-to-delete appender. | Extend existing R2-01. Phase/bounded own THEIR unwind, old E07 catch only body A: none owns failed terminal save plus safe release/continued-main residual refusal. C actual object/suffix/creator/attempts, H actual owner/work-end/timer/process/file consequences; J serialized fields cannot prove those private transitions. No duplicate helper coverage. | NO export/parameter/flag/injection/dead path; ordinary actual-boundary locals only. |
| X-01 EACH named subinput | Compose A/P/B/C+eligible release OR refusal with THREE publishers/conditional poison ATTEMPT, full capture/safe staging, frozen reader/root-loss/export/commit, withholding/sticky failure/noncredit. | Delete despite residual expiry; treat release/diagnostics as readiness/PASS; retry E07; last-good/bearer-only context; reset sticky failure/borrow C/E; use removal as closure or reclassify commit. | R2-01 stops at local/main propagation. R2-06/07 retain capture[], R3-01/09 collection/withholding, R3-07/X-02 commit. X-01 owns composition. C actual propagation/poison attempt, H actual context/pending/files/timers/joins/root, J independent frozen sanitation/attribution/noncredit; J proves neither C/H nor native closure from null/83. | NO new verifier/fixture/driver/venue/carrier/runnable case or production seam. |

Candidate **NOT RUN**; intended-failure/restored-pass **PROOF NOT RUN**; sensitivity **UNPROVED**, for EACH affected row/subinput. This finite case table adds no separate runnable test or fixture; the candidate wrapper has not executed. Existing actual failed venues are genuine failures, not unavailable-support exemptions or attributable intended-failure proof. The current gate adds no execution authority, mutation campaign, repeat requirement or test-authoring PASS.

Raw-first literal-key immutability, original THREE main writers, genuine
selected handoff joins, full-context withholding and all failure noncredit
remain. E05/E07 source joins dynamically read the genuine current20/24 members;
no manifest/consumer edit or old receipt substitution is made. Keep ALL9 aggregate
controls, ALL14 cancellation controls, complete schedule/manual admission maps,
and the original16 browser sites1/4/5/6/7/8/10/11/15/16/18/19/22/23/24/25.
No runnable test/seam/framework/venue/proof campaign is added.

Latest assessed40025778820 remains FAILED/HealthFAIL47 at91ff3f9a, EACH3.12/3.14
all56 UNKNOWN/null, zero recordedPASS, final_missing/direct83/cleanup-export
ValueError. BOTH HRD2:S:AC:AL:02:02:A:E identify selected alarm leaf/action/exhausted
case minimum/tie ONLY; decoded CONTEXT_VALIDATE:F identifies predicate rejection
ONLY. No operation/[]/writer/bearer/PR1180 cause proof follows. TERM15/3.12 and
INT2/3.14 actual witnesses/owned closure are diagnostic-only. Earlier failures,
53/56/loss/scheduled partial/CI contradictions/dashboard503/native uncertainty
and merge-helper deviation retain their original attribution. Seq388's rendered/
locale/child_wait hypothesis remains unproved; seq390 corrects E07 to LAST.

ROOT12738 retains the byte-exact17554B ledger/SHA256
`fe621dbd20c8ead2f01519a7cc27bc0e1a69d218745412584b205f3ce1311d6a`
WITH ALL22 step3–24 addenda and current step25; no historical field is rewritten.
Failed12753/12755/BLOCK22272 and every predecessor remain. Original12724
preparation/custody/effects/retirement stay separately OPEN/UNKNOWN; source/QA
supplies no historical closure or reuse of the consumed362 exception.
Independent FULL review/same-final-head scoped SOURCE QA, normal hooks/nonforce,
manager guarded merge/exact-main TWOstatic and separate manager-reviewed-main
Product admission remain. Units RETIRED; general integration INCLUDING callback/
lifespan/SessionTracker SKIPPED24342/211270–271. Daily19UTC/standing manager277,
ALL56 EACH3.12/3.14/FOURarchives/allmembers/realTERM-INT/nativeHealth,
480/240/720/60/120/900,31s populated-cache isolation, locked dependency integrity/
registry/install and public API/browser/CLI/result/audit remain. Protected12/
mutation/restoration/repetition unauthorized; abruptR/E04cap/persistentStop HELD;
100MiBfit UNPROVED;Mac11743 unimplemented/11640budget3of3 exhausted. No product/
schema/auth/permission/privacy/access/budget/schedule/monitor/SLA/Jenkins/Mac/
provision/host/deploy/restart/reset/reconciliation authority is added.

### First-recorder and native-log observations (TASK12758 / ROOT12738)

Current manager step17 acceptance of TASK12768 (33947 UTF8 bytes,
SHA2567446d38710ee11ff65c955b60ab5b2a33f175b3dc24f47ac9378f083d82ed981),
independent TASK12769 DESIGN ONLY PASS, selects TASK12770's FOUR-file diagnostic
eligibility revision. This replaces the older TASK12756/12757 inner-positivity
and unknown-origin refusal rules for diagnostics only. The original thirty
rows, A/B/D/E answers, ALL56 L1–L7 membership/order EACH3.12/3.14, sixteen browser
sites and complete validator/admission/control maps remain. No executable
case/injector/proof harness/venue or test-only production seam is added.

`record_error` first inserts candidate raw W/object/savedTB and assigns the
primary before optional work. Only `winner is candidate` on the inserting owner
can claim and complete its separate snapshot. Reentry before completion,
earlier P, later SAME object, off-owner, pending/refused/interrupted/partially
assigned state never receives later completion or terminal repair.
`_snapshot_first_error` is memory only, with no category publication or I/O.
The THREE category publishers, historical check/nonce, original capture A then
[] invalidation B precedence and raw final raise remain unchanged.

The original constructor deadline G is pinned once in `_diagnostic_outer_end`.
Diagnostic admission requires that SAME exact finite non-Boolean numeric G,
unchanged type/value and still-positive monotonic allowance, through creator
completion and selected send. Missing/invalid/drifted/exhausted G refuses.
Expired inner case/local/phase minima alone do not veto failure reporting.
No new end, reserve, interval, C/E borrowing or workload continuation follows:
effective_end/remaining/phase/bounded/_timer_sync/publish_end, Store dynamic
admission, category publication, close/evidence and cleanup/export keep their
original bounds and alarms. A NEW actual raising interruption stops optional
work through existing handlers; an earlier selected workload alarm is a first
fact, not permission to renew work. Admission is cooperative, not hard timing.

The immutable record is exactly23 ASCII bytes `HRD2:S:SS:OO:BB:TT:R:F\n`.
SS is PH/BD/RS/CT/AC/TD/EV/BR/OT. OO is RM/PH/BD/TS/WE/RD/CS/RS/CL/EV/MN/AL/UN,
using only reviewed code objects: remaining, phase/bounded generators,
_timer_sync, publish_end, prepare_redaction, capture/remember_thread_secrets,
close, evidence, main and its original expired closure. A require leaf maps
only through its immediate reviewed caller. The saved walk is at most32 TB
nodes; missing/unrecognized leaf/caller or overflow yields UN without guessing
an ancestor. No frame/text/locals/path/context/error/clock/token is exported.
Same-source `_diagnostic_packet` refuses old21-byte HRD1:S packets; retained
old logs keep their original meaning. HRD1:C collection markers are unchanged.

BB=01..1F labels effective-minimum ties among actual present G=01,C=02,L=04,
O=08,I=10 owners. Absent fallback attributes own no tie; only original inactive
inherited infinity is a sentinel. TT=01..7F includes those owners plus
Python-phase20/native-phase40 at the timer minimum, with at most16 actual phases.
One creator monotonic observation classifies their combined minimum F=P if
positive, E if expired. A completed malformed bounded observation yields
F=U with BB=TT=00, never a guessed known subminimum. Exceptions or incomplete
assignments refuse; they cannot become UNKNOWN-complete later. F=P/E requires
nonzero in-range BB/TT, F=U requires both00, and OO=UN is independent of F.
R=P requires an actual phase/bounded creator before ORIGINAL restoration;
R=A describes first-recorder state only. First P then historical exhausted H0,
or first E then historical positive H0 after restoration, are distinct honest
histories. No terminal resampling or lost-bound reconstruction occurs.
Completed-readiness leaves local_bounds.end present: with the stated other
absences, actual G+L ties give BB05/TT05, never a hardcoded result.

One atomic nonblocking/CLOEXEC anonymous UNIX datagram pair is attempted ONLY
for scenarios. Directional shutdowns, private pass_fds/--_diagnostic-fd and
parent sender release immediately around the ONE Popen precede owner.add/wait.
The child adopts that exact fd, validates actual OS nonblocking/family/type and
restores CLOEXEC BEFORE Controller/observer/exec descendants. Ordinary setup
failure disables diagnostics before that single launch choice; Popen failure
or interruption never retries. Registration/closure/custody gaps remain
UNKNOWN. CLOEXEC supplies no fork-only or same-UID interference guarantee.

The original adapter decision is saved byte-identically before optional send.
The sole sender opportunity charges before validation. Full genuine module/
token/frame/W/object/savedTB/head/outer/H0 claim/nonce/category/Boolean plus the
snapshot's OWN complete claim/W/TB/owner join are required. H0true/false keep
their historical meanings; missing H0 retains selected status1 but no packet.
Encoding and the ONE MSG_DONTWAIT send require still-positive ORIGINAL G,
independent of inner expiry. Success, constructor/pretry, unselected/collision,
exhausted G and poison/replaced-head/tail escapes send nothing; status83/1,
argv or direct exit alone cannot establish selection or custody. New raising
interruptions escape; the saved adapter is never recomputed to mask them.

Only the existing poll loop's genuine direct-controller exit, saved code/phase
exit and original finished/seconds/interrupted assignments admit receiver work,
after log closure, still under SAME command end/alarm BEFORE larger action
restoration/raise-on-code. Charge ONE `recvmsg(513,0,MSG_DONTWAIT)` sample;
exact complete grammar, no ancillary and zero unexplained/truncation flags
are required. Invalid/empty/oversized/error/first-EAGAIN means no probe/write.
Only valid first permits ONE charged `recvmsg(1,0,MSG_PEEK|MSG_DONTWAIT)`.
ANY successful second tuple, including b''/invalid/truncated/ancillary, refuses
ALL. Only exact second EAGAIN/EWOULDBLOCK is candidate absence, followed by
custody/end postcheck, complete R release and original-end prewrite check.
No third call/retry/drain/new poll/reap/wait/EOF inference or receiver in
cleanup/export. Python-call counts are not hard kernel-syscall guarantees.

Each complete eligible observation consumes ONE <=512B fixed-ASCII unbuffered
`os.write(2,line)` opportunity to unchanged inherited launcher fd2. Existing
regular cancellation launcher.log and blocking native pipe are permitted.
Controller lines use `HRD1:controller:` plus the exact packet; collection lines
are `HRD1:C:CLEANUP:LL:K\n` or `HRD1:C:EXPORT:LL:K\n`, K=F/U. LL is ROOT,
TOKEN_READ,TOKEN_EMPTY,CONTEXT_PRESENT,CONTEXT_READ,CONTEXT_DECODE,
CONTEXT_VALIDATE,SCENARIO_PRESENT,SCENARIO_READ,SCENARIO_DECODE,SCENARIO_MERGE,
RAW_ENUM,TEXT_READ,REDACT,IMAGE_READ,REQUIRED,UNKNOWN. Labels describe completed
operation windows, not exact throw instructions. Valid token plus context `[` /
`[]`, or valid context plus scenario `[`, are source counterexamples for decode /
validate / scenario-decode respectively, never native cause proof.

Original raw error/TB and sticky incomplete reservation precede the first site
claim; one failed/unavailable claim spans C/E and never repairs on recollection.
Absent-context policy is unchanged, frozen reader/withholding and independently
eligible original C ports/root removal remain. E keeps its original E-1 cutoff,
safe FAIL/variants={} and terminal successful replacement; no diagnostic in
publish/after commit. Full markers grant no readiness/result/health/cleanup
credit, and no raw/scenarios.txt rescue or validator decoder exists.

No dup/reopen/path/flag/buffering/flush/helper/readiness wait/application retry,
timer extension/deferred/postcommit output. TimeoutError precedes OSError;
ordinary short/OSError consumes UNKNOWN chance, while actual raising alarm,
RuntimeError, KeyboardInterrupt/SystemExit escapes optional handling through
genuine outer handlers. Saved child exit/finalization remains real; an exit
error not yet raised is never fabricated to hide an interruption. Blocking I/O
can delay signals, consume C and lose cleanup/publication. Finite bytes promise
no kernel latency, delivery, retention, durability or later cleanup opportunity;
partial lines are never reconstructed. Only completed assignments are protected.

The following EACH13 four answers cover all accepted setup/adoption/registration,
selection, mapping/owner/tie, reentry, queue/error/expiry/close/write/signal,
collection/frozen/root-loss/recollection and terminal subinputs. C=actual local
identity/propagation, H=existing disposable hosted EACH3.12/3.14 OS/native-log
boundary, J=existing serialized acceptance consumers. They are design
expectations, **not executed assertions**; no new runnable tests were authored.

| Requirement / keeper: finite input and action | 1 Independent expected observable / level | 2 Realistic regression | 3 Nearest keeper gap; distinct C/H/J risks | 4 Seam |
| --- | --- | --- | --- | --- |
| R2-01: expired first case/local/phase with positive G; positive then expired; W versus earlier P/later restore B | SAME W/savedTB at genuine final raise; sealed first facts survive restoration; expired-inner selected report eligible. C/H/J noncredit. | Retain old inner gate, resample, select B or restore workload. | Phase/bounded/E07 owns precedence, formerly excluded this expiry. C creator/TB; H alarm/packet/log; J83 cannot prove identity. | NO |
| R2-02: known/unknown first leaf or require caller; >32 TB; malformed owner/phase; actual ties/inactive infinity; P/E/U | Closed UN or U00 honestly distinguished from known facts; no ancestor/case/clock/text export. C/H/J. | Guess ancestor/minimum, refuse every unknown leaf or leak details. | Mapping keeper lacks complete unknown facts. C observation/privacy; H exact bytes; J no cause inference. | NO |
| R2-03: no snapshot/pending before reservation or seal/reentry during seal/send/complete then later SAME object | Missing/refused never repaired; only creator seals; charges forbid another send/read/write. C/H/J. | Complete pending later, overwrite refusal, retry EAGAIN. | Active-writer keeper does not own snapshot claim. C reentry; H once-only transport; J absence proves neither. | NO |
| R2-04: off-owner first; owner later same/prior claim; original G missing/nonfinite | No surrogate snapshot/enqueue/wait; private refusal never infers W/selection/result. C/H/J. | Owner repairs nonowner or substitutes nearest deadline. | Owner keeper needs original-G/refusal distinction. C ownership; H inherited custody; J cannot infer owner. | NO |
| R2-05: failed setup/adoption/registration/send/sample; invalid/empty/oversize/flags/ancillary/firstEAGAIN; ANY second tuple vs exact EAGAIN/EWOULDBLOCK; failed release/short write | One launch/adoption/send/sample/peek, full R release before <=ONE fd2 write. Gaps UNKNOWN, no third read/fallback. C/H/J. | Accept b'' second, broaden errno, output before close, retry/pass fd broadly/swallow alarm. | File-writer keeper lacks datagram/fd2 custody. C charge/join; H OS flags/backpressure/retention; J result cannot prove transport. | NO |
| R2-08: inner expired/G positive; G exhausted before/during operations; constructor/no-W/unselected/replaced-head/tail; H0true/false/missing; actual ALRM/RuntimeError/KeyboardInterrupt/SystemExit | Inner expiry alone no veto; exhausted G/invalid selection no send. Saved adapter unchanged; new raising optional interruption escapes, no second chance. C/H/J. |83-as-selection, recompute adapter, reserve/C/E/timer renewal or broad catch. | Expiry keeper conflated inner/G. C heads/H0 vs first fact; H actual signal/exec/status; J no reaped-code substitution. | NO |
| R3-01: []/nonlist/empty/member-invalid/missing-bearer decoded predicates vs valid/decode failure; capture[] vs prepare expiry | SAME CONTEXT_VALIDATE only on decoded rejection, raw/TB/sticky reservation first; no value/writer inference or false[] claim. C/H/J. | Conflate decode/validate, infer[], log before reservation/bearer-only rescue. | Collection+R2-06/07 already own predicates. C A→[]→A/B; H files/removal; J withholding. | NO |
| R3-06: G expires between send/sample/peek/release/write; C/E expiry; actual raising alarm at each optional call | No next optional attempt beyond its original end; new raising interruption stops sequence. Cleanup C/export E-1 unchanged. C/H/J. | Borrow C/E, restore action_end before sample, ignore checks or claim finite bytes bound latency. | Existing expiry needs reporting/work separation. C propagation; H kernel delay/partial output; J arithmetic cannot prove closure. | NO |
| R3-07: precommit failure vs successful replacement/queued alarm | Initial FAIL survives no commit; committed replacement terminal, no postcommit diagnostics/check/reclassification. C/H/J. | After-commit marker/rescue/check or log-as-PASS. |12649 keeper already owns order; apply to revised packet. C unwind; H replace/alarm; J persisted disposition. | NO |
| R3-08: root loss/old-null frozen read/later recollection/pending first snapshot | Frozen stays frozen, incomplete stays sticky, no snapshot/channel/root repair. C/H/J. | Reread/recreate in export; UN-as-absence. | Frozen keeper needs explicit U meaning. C first claim; H root/fd; J stale/null/failure. | NO |
| R3-09: unsafe context+complete packet; absent packet+valid context | Full-context withholding unchanged; marker never readiness/artifact/cleanup/PASS; fallback variants={}. C/H/J. | Last-good/bearer-only/scenarios.txt/raw-TB rescue or marker acceptance. | Context keeper needs E/UN noncredit. C capture vs preparation; H sanitation; J required evidence. | NO |
| X-01: ONE expired-first W→selected send→direct exit→sample/absent peek→R release→write, then collection rejection/root loss/incomplete export; each failed-link variant above | Joint observation only after EVERY actual link in SAME invocation/original ends; W/precedence/withholding/safe FAIL survive. C/H/J. | Stitch epochs/H0, infer custody from argv or substitute collection-only utility. | Local R2/serialized R3 lack composition. C W/H0; H actual endpoints/exit/log/root; J frozen failure/commit. | NO |
| X-02: frozen/initial FAIL+root loss/G expiry/short output vs terminal commit | No later drain/retry/first-fact refresh/recollection repair/postcommit output. C/H/J. | Rescue/reopen/third read/unknown-as-latest/reclassify commit. | Frozen/commit keeper forbids later packet lifecycle. C propagation; H close/remove/write/replace; J immutable noncredit. | NO |

R2-06/07 original capture/invalidation precedence and ownership remain with
their original keepers. The SAME13 rows above specify finite inputs, actions,
independent observables/final transitions, environment/levels and EACH four
answers; C/H/J remain distinct risks. All original30 rows remain unchanged.

Candidate **NOT RUN**; intended-failure/restored-pass **PROOF NOT RUN**;
sensitivity **UNPROVED**. Static all/Source Boundary Lint/Web Lint(Node24),
independent FULL review and same-final-head scoped SOURCE QA cannot establish
behavioral/test-authoring/Product acceptance. Python units RETIRED; general
integration INCLUDING callback SKIPPED24342/211270–271. No branch Product E2E,
protected12/mutation/repetition or new execution allocation. Dynamic20 E05/24
E07 source hashes must join genuine later bytes; old receipts remain historical.
Latest assessed40011623903 remains FAILED/HealthFAIL47, EACH56 UNKNOWN/null/zero
recordedPASS, final_missing/direct83/BOTH CONTEXT_VALIDATE:F decoded-predicate rejection
before raw merge, then root-loss sticky incomplete export; TERM3.12/INT3.14 are diagnostic-only. UNKNOWN is not UNREACHED; PR1180
cause stays UNKNOWN. The markers do not establish[]/writer/bearer or original
first cause. The exhausted-inner/positive-G E07/AL route is conditional source
feasibility, not historical causality. The actual failed venue is no unavailable-
capability exemption. No behavioral/Product/reset/test-authoring PASS follows. Complete manager step17 ledger17554B/SHA256
fe621dbd20c8ead2f01519a7cc27bc0e1a69d218745412584b205f3ce1311d6a and ALL22
retained integrity entries/prior addenda preserve older failures/obligations by reference,
including old12724 UNKNOWN native custody and consumed ONE362/12741 history.
Daily19UTC plus manager-owned reviewed-main-only Founder277 authority, original
480/240/720/60/120/900 and31s isolation remain; no schedule/validator/product/
runtime/auth/permission/privacy/access/dependency or deployment change follows.

### Advisory terminal interpretation (TASK12709 / accepted12707-12708)

This extends the SAME recovery record after ROOT12610 step28 acceptance of
TASK12707's design and independent TASK12708 DESIGN ONLY assessment. References
above to primary propagation apply through main's actual final raise; this
selected internal adapter deliberately changes subsequent interpreter handling.
It adds no result field, runtime decoder, test driver or reliable telemetry.

W is the actual immutable `_failure_raw["first"]` tuple, including its original
object and saved traceback, never a category slot or latest cleanup error.
Inside the original owner/active/sticky guards, one private `setdefault` claim
binds W and a fresh invocation nonce before the FIRST initial check prepared
with W. Only that winning nonce may complete ONE immutable observation for the
SAME current W. A check prepared with no W stays unassociated; a later winner
never joins it retroactively. Later calls cannot repair a pending claim.
Off-owner/reentry/sticky skips create no observation. The SAME original
`monotonic() >= effective_end()` comparison executes ONCE; its actual Boolean
and W's unchanged `_failure_error` category complete before the existing true
TimeoutError or false continuation. Other checks retain their original behavior.
No extra clock, check, timer, publisher or reserve is introduced. Partial or
interrupted comparison/category/assignments stay UNKNOWN, with no handler/finally
repair. A completed tuple is historical, never eventual publication state.

Only a narrow catch around the ORIGINAL final raise expression may hand off:
actual raw/primary/caught-object/saved-traceback identity must match, and the
new main traceback head must point exactly to that saved traceback. It stores
real immutable references with the real module token/frame, then BARE RAISES.
The actual module call verifies fresh token/frame, the caught object, its module
traceback head and EXACT following stored main head, then claim/nonce/W/enum/
Boolean. Earlier armed state, matching text/class, the same object at another
site or a completed earlier check alone cannot authorize translation. No wrapper
frame is added to unselected tracebacks. Main without handoff preserves propagation;
imported Controller use gains only finite private per-instance memory.

A complete selected handoff with a matching complete observation emits an exact
built-in integer `80 + 2*k + b`, b=0 for positive, b=1 for exhausted:

| k / existing error enum | Positive | Exhausted |
| --- | ---: | ---: |
| 0 unicode | 80 | 81 |
| 1 timeout | 82 | 83 |
| 2 interruption | 84 | 85 |
| 3 assertion | 86 | 87 |
| 4 value | 88 | 89 |
| 5 io | 90 | 91 |
| 6 runtime | 92 | 93 |
| 7 other | 94 | 95 |

Unicode retains precedence over ValueError, TimeoutError over OSError; interruption
includes KeyboardInterrupt/SystemExit/GeneratorExit/InterruptedError and does not
prove an OS signal. `other` requires completed evidence; it is not UNKNOWN.
Positive means only that check's non-exhausted branch, never publication.
Exhausted means only its exhausted branch, never which inner bound won, cause,
final writer state or why a file is absent. A complete selected handoff without
a matching complete observation emits noncategorical1. Selected ordinary and
special BaseExceptions, including SystemExit0, become `raise SystemExit(status)
from None`: automatic uncaught traceback/special handling is deliberately replaced.

The ONLY otherwise-unselected normalization is exact built-in SystemExit whose
code is an underlying int (including int subclass), within signed64 and with low
byte80–95: direct80 and aliases336/-176 become1. Built-in int operations bypass
subclass operator overrides; no arbitrary conversion or code mutation. None,
noninteger, Boolean0/1, noncolliding and outside-signed64 codes retain handling.
Normal return0, argument2, constructor and other unselected Exception/BaseException
handling remain. Dynamic/stateful SystemExit subclasses, pre-entry/native positive
collisions and interpreter failures remain unisolated. This targets the existing
direct Linux64 CPython3.12/3.14 invocation. Interruption anywhere can prevent
handoff or exit; normal finalization can change the intended exit (including120)
or receipt. No shield, repair, synthetic traceback/stderr, hook, atexit, os._exit,
supervisor or new I/O promises a categorical status.

Documentation is the ONLY new interpreter; runtime retains literal actual
scenario `phase.exit`. Require genuine completed source/admission/event/ref/run/
attempt/SHA/tree/interpreter/controller-command provenance, then exact built-in
int80–95 only. Never coerce bool/string/float or apply modulo to the recorded exit.
Even with all joins, a reserved byte NEVER authenticates encoder entry or cause:
state only “consistent with candidate category/first-check pair under this source;
encoder entry is not independently proved.” Other/null/partial/negative/old-source
or wrong-type records stay UNKNOWN;1 is noncategorical;0 alone gives no case or
health acceptance.120 is outside the table. No requirements1–3/Product/reset,
local encoder/poison/I/O/sanitation/cleanup or native initiating-cause credit follows.

Extend only these existing joins, with all other thirty-row/A-B-D-E expectations,
accepted12693 reader/raw-copy additions, delivered12649/12675/12697 assertions,
56 EACH,16-site and full validator maps retained by reference:

| Affected row and finite subinputs → expected final transition | Observable contract / credible regression | Keeper gap and distinct C/H/J risk | Test-only seam |
| --- | --- | --- | --- |
| R2-01/R2-05/X-01: raw A versus later restore/poison/close B; first associated check positive/exhausted; final raise reached/bypassed | SAME A/saved traceback survives original conditional poison ATTEMPT/finalization to actual final raise when reached. Only complete matched handoff+observation gives A's pair; complete selected handoff without observation gives1. A bypassed final raise gives no selected translation. Regression: encode B, compare text, arm before finalization or omit caller finally. | phase/bounded/raw/capture/poison keepers own precedence and ATTEMPT, not terminal identity/numeric emission. C owns actual object/traceback/check/attempt; H real child emission/failure retention; J consistency/noncredit cannot prove encoder/poison execution. | NO for every named row/subinput; real production module handoff only. |
| R1-11/R2-08/R3-06: no-W; interrupt before claim, comparison, category, completion, handler/reset/handoff; pending nonce, later call, reentry/sticky/off-owner; positive/exhausted; success, argument/constructor, unrelated Exception/BaseException, selected SystemExit0/KeyboardInterrupt/GeneratorExit; exact built-in SystemExit direct80/aliases336/-176, int-subclass overrides, None/noninteger/Boolean/noncolliding/outside-signed64, dynamic subclass/native/finalization collision limits | Complete historical tuple immutable; partial/no-match/unreached UNKNOWN and no later repair/retroactive association. Complete selected unknown gives1; exact collision gives1; others retain handling as specified above. Regression: extra clock, second nonce repairs first, infer bound from handler/final_missing or decode reserved-only output. | Existing expiry/constructor/writer keepers lack association/terminal join. C actual comparison/identity/interruption; H real status/partial receipt/native/finalization limits; J source/type consistency only. | NO for every named row/subinput; no new driver/export/flag. |
| R3-07/R3-09/X-02: completed scenario table int versus null/negative/Boolean/string/float/wrong-source/partial; frozen FAIL, withholding/root loss;12649 precommit failure versus successful terminal replace | Retain exact phase exit, safe FAIL/variants={}, flags/frozen reader and original precommit/terminal ordering. Interpretation stays advisory/noncredit. Regression: coercion/modulo, substitute launcher/reaped exit, bypass sanitation or reclassify after commit. | command/failure_receipt/publish/12649 own carrier/commit, not independent category origin. H real assignment/file/publication/root loss; J serialized source/type consistency/noncredit cannot prove local encoder/poison/I/O/cleanup. | NO for every named row/subinput; existing fields/validators only. |
| Existing TERM3.12/INT3.14: active witnesses, actual launcher signals15/2, independent closure; later controller reaped exit | Preserve launcher_exit1 and interrupted scenario exit=null at their own times; later controller reaping never fills phase null. All signal/closure/timing/raw-copy assertions remain. Regression: decode launcher1, substitute later child or broaden cancellation acceptance. | cancellation.validate/ALL14 controls and accepted raw-copy keeper remain. H owns two actual processes/times, signal and closure; J consistency cannot prove signal delivery/closure. Distinct from completed baseline exit. | NO for every named subinput; no new process or venue. |

Behavior NOT RUN; intended-failure/restored-pass PROOF NOT RUN; sensitivity
UNPROVED. No control/mutation/RED-restoration-GREEN/repetition campaign is allocated.
Genuine native failure is not an unavailable-venue exemption. Static inspection
and lint supply no behavioral/test-authoring PASS. Protected12/abruptR/E04cap/
persistentStop holds and100MiB suffix-fit uncertainty remain.

### Optional reader disposition and raw-copy checks (TASK12697 / accepted12693)

Only requirement4, the launcher's useful actual reader disposition before root
loss, is implemented. Requirements1–3 (first-writer reachability, effective
internal bound, owner/reentry/sticky/publication disposition) remain OPEN. This
is optional observation history, with no reliable final telemetry, latest-state,
error-free, readiness, cleanup, case/PASS or native-cause inference. The existing
category schema1/private file and controller's exactly THREE calls are unchanged.

The optional result sibling `diagnostic_read_observation` has EXACT keys
`schema,source_sha,state,outcome`: exact non-Boolean int1; matching admitted
lowercase40-hex source; exact state string at most9; null or exact outcome string
at most18. After those finite gates its compact UTF8 JSON must be at most256B.
Unknown/missing/extra keys, wrong types/schema/enums/size/source reject. Invalid
receipt source omits BOTH optional projections; internal `source_unavailable`
never appears on the wire and no identity is minted. BOTH members absent remain
legacy/unobserved, not proof of unreached; a present extension requires the
necessary category envelope.

One launcher-local immutable tuple starts unknown/null. Optional separately
contained positive-SAME-C pre-call bookkeeping may record admitted/null; that
proves reaching the checkpoint only, not callee entry/I/O. Mandatory reader
snapshot/error assignment precedes optional positive-C completed freeze. Its
failure never rolls back the mandatory assignment or skips original operations,
read-error handling, incomplete() or cleanup_time(). Only an actual skipped
owner boundary under positive original C, current_attempt=owner, unattempted
sole reader, initial unavailable/null snapshot and no diagnostic-read latch may
freeze unreached/null. Default attempted=false alone proves nothing. Expiry or
interrupted bookkeeping keeps the prior tuple; no rescue observation or time
renewal occurs. Original primary/first-cleanup/read errors remain authoritative.

Completed outcomes occur only on genuine helper returns: `root_unset` for a
valid source/root=None; `root_missing`/`coord_missing` only the existing
missing_ok exact-filename branches after their check; `final_missing` only literal
final ENOENT after verified parents/no pending and checks; `refused` for the
existing ordinary error return; `available` only after the ONE bounded read,
strict decode/source/slots AND all descriptor/directory exits/final check.
Other missing/unsafe inputs refuse. Provisional availability cannot survive exit
failure. Timeout escapes, with no invented expired outcome. Frozen memory alone
feeds initial/final/fallback consumers; export never reads the private file.

For PRESENT metadata, U means exact controller unavailable/snapshot=null; A means
available with the validated schema1/matching-source snapshot and its two
null/stale-or-exact `{stage,error,at}` slots. The category envelope is exactly
`schema,source_sha,controller,launcher`, controller exactly `availability,snapshot`,
snapshot exactly `schema,source_sha,first_observed,context_capture`. Launcher must
contain `diagnostic_read`; its other six slots keep existing consumers. D is null
or the exact teardown read slot. Slot stage is phase/bounded/restore/context/
action/teardown/evidence/browser/other, error is unicode/timeout/interruption/
assertion/value/io/runtime/other, at is exact non-Boolean finite int/float in
0..9007199254740991. Gate exact dictionary type/count before fixed-key access,
and string/numeric types/lengths before domains; traverse these finite paths only.

| State / outcome | Controller | D | Accepted interpretation |
| --- | --- | --- | --- |
| unknown/null | U or A | null or error | Optional observation did not survive; later mandatory assignments can exist. |
| admitted/null | U or A | null or error | Last optional checkpoint only; later read/assignment/latch may have occurred. |
| unreached/null | U only | null only | Genuine positive-C skipped-site observation; A/error contradict it. |
| completed/root_unset, root_missing, coord_missing, final_missing | U only | null or error | Completed prerequisite/allowed absence; real later D errors remain. |
| completed/refused | U only | null or error | Actual completed helper refusal; caller latching need not finish. |
| completed/available | A only | null or error | Valid completed read; a real later D error is not erased. |

Every other pair rejects, including completed/source_unavailable or null,
noncompleted/non-null, unreached+A/error and either opposite completed controller
relation. The finite fields cannot order returned refusal versus later errors.
Observation omission remains possible and earns no credit.

Aggregate's authoritative serialized boundaries are baseline result.json and
ONE additional read/decode EACH of the already exported TERM/INT
`launcher/result.json`, alongside each existing cancellation.json. There is no
cancellation implementation/private-file change or fifth artifact. Each decode
uses strict text and one JSON pairs hook: ordinary dictionaries retain unrelated
last-key behavior. Before collapse, selected duplicate flags cover
`diagnostic_read_observation,source_sha,failure_categories,schema,state,outcome,controller,launcher,availability,snapshot,diagnostic_read,first_observed,context_capture,stage,error,at,launcher_result`.
At most16 duplicate-bearing dictionary identities have strong references/fixed
masks plus overflow. Fixed root summaries separately retain root duplicate mask
and ANY metadata-bearing launcher_result candidate, including one hidden by a
later parent overwrite or record overflow. The hook visits pairs once. No raw
rejected bytes/keys/values are retained for errors or recursively searched later.

With selected new/shadowed metadata, reject relevant duplicate parents/keys at
result, observation, category, controller, snapshot/slots and launcher/read slot;
also cancellation outer source/launcher_result. Overflow refuses conservatively,
including saturation by selected-vocabulary duplicates elsewhere. BOTH genuinely
absent copies with no hidden candidate retain legacy duplicate semantics. The
16-record cap bounds BOOKKEEPING ONLY, not file/JSON allocation, length, key count,
depth, RSS, OS-I/O or time. Existing complete-file decoding/job deadline remains;
no streaming/cap/retry/reserve/framework or hard fit claim is added.

Raw, cancellation row and embedded launcher_result must be actual dictionaries
with strict coherent source equal to independent admission. BOTH present copies
must individually validate the new object and necessary category relation and
agree on presence and type-sensitive finite projections, including snapshot/D;
Boolean1 differs from integer1, as does float1 versus int1. Reject raw-only,
embedded-only, null-as-present, mismatched values/source/relation, selected
parent overwrite/duplicates/overflow. A good derivative cannot rescue bad raw.
No whole-result equality or unrelated duplicate ban applies. Valid source-coherent
BOTH-absent copies need no category envelope and remain legacy/unobserved.

**Explicit bounded compatibility delta:** malformed/truncated/invalid-encoding,
nonobject or unreadable raw legacy launcher files and raw/embedded source
disagreement now FAIL even without the new member. Previously the standalone raw
cancellation file was only checked for presence/nonempty bytes. Missing/empty
already FAIL. Valid supported absent-member receipts remain supported; historical
evidence is neither reread nor relabeled. This is not unchanged compatibility.

Baseline read/decode and selected validation have separate local catches with
fixed `e2e-3.12: invalid diagnostic metadata` / `e2e-3.14: invalid diagnostic metadata`.
Readable dictionaries remain in rows for original directory/interpreter/exercise,
E04/E05/E07, aggregate and admission attempts in their original order/groups.
Cancellation raw and row reads are attempted independently; read catches contain
OSError/UnicodeError/ValueError/TypeError/RecursionError/OverflowError. Selected
validation separately contains ValueError/KeyError/TypeError/IndexError/
RecursionError/OverflowError. At most ONE fixed `TERM: invalid diagnostic metadata`
or `INT: invalid diagnostic metadata` is added per label, without exception or
rejected-byte echo. EVERY readable cancellation row still enters the ORIGINAL
status → row admission → embedded admission → validate_cancellation/timing group.
Ordinary errors retain THEIR OWN short-circuit/catch/messages; metadata neither
replaces them nor jumps to their catch. Unreadable rows have no invented fallback.
Other cells, file/missing/empty/failed-job errors, health/summary/exit, signals,
witnesses and ownership closure retain their original semantics.

The following increments extend the existing thirty cases and A/B/D/E blocks;
they do not replace any row. Each line supplies answers1–3. Answer4 is **NO for
EVERY named row/subinput**: no test-only production export/flag/parameter/wrapper/
hook/path, runnable test, verifier or venue. H/C/J retain the environments above:
H actual file/process/exit; C propagation/original bounds; J independent serialized
relation cannot prove local I/O, poison, sanitation or cleanup execution.

| Existing rows / requirement | 1. Precondition/input/action → independent expected result / level | 2. Credible regression; 3. keeper/gap/distinct risk |
| --- | --- | --- |
| R1-01 completed positive | Actual safe file read completes within C, including exits → completed/available beside unchanged validated snapshot in actual result, retained after removal. H+J. | Label at open. Keep strict reader/failure_receipt; missing contract is completed-return discrimination. H sees exits, J frozen claim. |
| R1-02 prerequisites | Valid source with root=None, allowed exact root/coord missing or literal final ENOENT → distinct completed/U outcome; invalid source omits BOTH projections. Other missing/unsafe forms refuse. H+J. | Collapse ENOENT/null errors. Keep no-follow/pending/missing_ok gates; H identifies actual branch, J source omission. |
| R1-03/R1-04 unsafe/pending | Original alias/owner/mode/link/special/pending inputs, including old valid final → completed/refused only on completed error return; otherwise unknown/admitted. No unsafe copy/repair. H+J. | Allow missing before pending or infer latest. Existing guards own refusal; gap is truthful completed disposition, not a second filesystem parser. |
| R1-05/R1-06/R1-07 raw provenance | Retain4096/4097/short/change/private UTF8/JSON/exit cases. Add raw selected member/schema/source/category/controller/slot duplicates and outer launcher_result/source duplicates, same-looking last values, hidden metadata parent and overflow → fixed metadata FAIL; no embedded rescue. BOTH valid absent copies remain unobserved; unrelated duplicates preserved. H+J. | Validate only collapsed embedded copy or truth-test member. Strict private reader/admission are keepers; H owns private refusal/exit, J owns exported duplicate/copy loss. |
| R1-07/R1-08 joined types | Enumerate EVERY D1 matrix row, opposite controller, D null/error, Boolean schema/time, source drift, missing/extra keys, completed/source_unavailable and wrong enum/schema/type/size → only listed forms accepted, fixed failures/no echo otherwise; absent extension unobserved. J. | Independent enum checks admit unreached+A, coercion or internal enum leak. Keep private source/slot reader and admission; gap is joined serialized truth. |
| R1-09/R1-10 stale/null | Completed actual read of valid null/stale slots → available read only, never latest/writer/capture success; absent extension remains unobserved. H+J. | Translate availability into readiness. Keep reader's stale/null semantics; J independently prevents health laundering but proves no actual local completion. |
| R1-11/R2-08/R3-06 partial/expiry | Interrupt before call, after mandatory snapshot assignment, before/during freeze, before/after error latch; owner skip positive C → unreached/U/null-D only, expiry retains prior tuple. Unknown/admitted allow U/A and real D; later D survives completed available/absence. Original nonzero/incomplete/C remain. C+H+J. | Default false as proof, force unknown/admitted U, require completed D=null or renew C. Keep operations/cleanup_time/first-error winner; C owns propagation/admission, H read/exit, J rejects impossible frozen joins. |
| R3-02 independent continuation | Positive-C refusal retains read error/incomplete flags and independently eligible collection/ports/root attempts; allowed absence clears no prior false cleanup. H+J. | Optional freeze/error skips root or clears flags. Keep owner-FIRST cleanup; H owns actual independent attempts, J only serialized noncredit. |
| R3-07/R3-08/R3-09/X-02 raw/copy/export | Freeze then root loss/partial collection/context/export failure; add raw-only/embedded-only/value/source/snapshot/error mismatch, duplicates/overflow and malformed/missing/empty/unreadable raw or cancellation.json. Fixed metadata FAIL; EVERY readable row still attempts original group, retaining actual timing/admission/signal/closure/missing errors. BOTH absent coherent copies unobserved. J+H. | Metadata try skips ordinary group, good derivative rescues raw, or malformed legacy acceptance called unchanged. Keep publisher/failure_receipt/cancellation.validate; J owns additive copy/error boundary, H real root loss/publication. |
| R3-07/X-02 terminal commit | Initial FAIL remains if replacement never commits, possibly absent/earlier metadata. Successful final replacement remains terminal; evaluate ONLY actual exported bytes/copy, no postcommit check/private reread/recovery. H+J. | Reconstruct later observation at export, upgrade initial bytes in prose or require rescue. Keep12649 terminal publisher/sticky fallback; H commit/alarm effects, J actual receipt relation. |
| R2-01/R2-05/X-01 composition | Original raw/traceback/capture[]/invalidation/conditional poison ATTEMPT/THREE calls plus admitted/refused/unavailable bookkeeping and later collection failure → original propagation/primary retained, no recursive/native diagnostic work. C+H+J. | Add writer or diagnose in record_error. Existing raw/poison keepers own local effects; J cannot prove those effects. Reader-only changes leave them intact. |
| Existing TERM3.12/INT3.14 consumers | Real active witnesses/signals15/2/non-success/owned closure remain independent; add D2 raw/copy inputs at SAME aggregate boundary. Metadata failure adds HealthFAIL even if ordinary validator succeeds; metadata success masks none of fourteen negatives. J, actual signal H/C diagnostic-only. | Substitute metadata success or skip cancellation.validate on metadata failure. Keep cancellation.validate/controls/signals/closure; two-stage serialization differs from baseline parsing and actual delivery. |

All original30 rows, unaffected12686 additions, delivered12649/12675 assertions,
all56 EACH L1–L7 order, A01–A16/shared16-site map and full TASK12631 validator map
above remain. No runnable assertions or existing controls changed. Corrected
behavior **NOT RUN**, intended-failure/restored-pass **PROOF NOT RUN**, sensitivity
**UNPROVED**; no execution/control/mutation/repetition campaign is allocated.
Actual39803512986 remains FAILED/HealthFAIL47, BOTH56 UNKNOWN/null, zero
recordedPASS/no provedUNREACHED, scenario1/fallback unavailable/null, cleanup
flags false despite absence observations, initiating cause unknown and npm success.
TERM3.12/INT3.14 actual witnesses/signals/closure PASS remains diagnostic-only;
JOB5283 completed0/four archives32members357878B stays parent step20-attributed
COLLECTION ONLY. Prior failures/receipts above are preserved; this actual failure
is not an unavailable-venue exemption. Product/reset INCOMPLETE and
ownership_evidence_complete=false UNKNOWN remain. No proof tooling is added.

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

### Active adopted-child reaping and completed-case retention (TASK12942)

Founder THR291seq434 and manager TASK12939 under sole ROOT12937 approve
this linked correction. TASK12915's accepted package governs this source;
historical unknown preparation and failed Product receipts remain unchanged.
This section supersedes only the older diagnostic-only statements below about
unchanged consuming waits and terminal-only result retention.

`Owned` pins its own launcher PID/start independently of optional diagnostics.
`Owned.popen` holds the SAME RLock across creation, retained Popen ownership,
numeric exclusion (including unknown generation), and generation registration.
All dependency/setup commands and the scenarios controller use it; cleanup's
systemctl queries do too. Interrupted creation leaves sticky unknown custody
and cannot authorize consuming waits or successful cleanup. The creator's
Popen remains the exclusive poll/wait owner, including after interruption;
its later status is recorded separately and never fills an earlier phase null.

The existing watcher samples/registers generations and invokes `Owned.reap`.
That method and cleanup serialize through the same lock. Only a registered
PID/start currently bracketed as an exited child of the pinned live launcher,
excluded from ALL direct-Popen numbers, is eligible. A pidfd is opened, the
same target/parent generations rechecked, WEXITED|WNOHANG|WNOWAIT observes
actual exit eligibility, and the same descriptor consumes WEXITED|WNOHANG
only after another custody/generation check. A claimed generation is disarmed
BEFORE consumption. Actual si_code/si_status/decoded exit and launcher/target
generations are retained in cleanup.reaped; strict disappearance must follow.
Unconfirmed claims remain explicit and fail cleanup. No waitpid(-1), live-child
wait, daemon-parent child wait, PID-only authority, Z-as-absence or second
consuming owner remains. Cleanup polls retained direct Popen objects, joins the
watcher, and only then publishes detached ownership evidence. An unjoined
watcher yields failure without reading its mutable registry into the result.
Optional census/old waitpid-attribution slots remain separate: incomplete slots
neither grant nor prevent independently qualified reaping, and old returns are
not relabeled as pidfd receipts. The old optional returns projection remains
incomplete/unknown on this new path.

`Controller.mark` commits one closed observation and then an append-only-prefix
ledger in private coord BEFORE returning to later case work. It leaves the
original result/evidence/at and all case assertions intact. `completed.py` is
the directly consumed E2E-only publisher/reader shared by Controller, launcher
and aggregate. It introduces no product hook, CLI flag, test-only seam or venue.
The <=56 records use actual completion sequence, exact manifest index/variant,
source SHA/tree, Actions run/attempt, Python lane and exercise. Completion order
is the existing execution order, not a reordering of the manifest. Evidence is
an immutable `case-NN.json` reference with exact byte length/SHA256, containing
ONLY that identity/time and bounded integer counts of HTTP/commands/starts/
durable observations/evidence fields. These closed observations retain reported
PASS from assertions but are not independent content proof. Aggregate shows
UNKNOWN unless complete original results and ALL existing content gates pass.
No raw body, log, exception text, token, arbitrary field or path is admitted;
later secret discovery cannot expose an early raw stream through this channel.
`prepare_redaction`, `_atomic_json`, small/final raw evidence and full-context
withholding keep their original gates; closed observations do not relax them.

The ledger grammar rejects unknown/duplicate JSON keys, duplicates/conflicts,
wrong source/tree/run/attempt/lane/exercise/variant/sequence/index, malformed
counts/times, unsafe paths, absent/digest-mismatched references and overflow.
The ledger is <=262144 bytes, each referenced observation <=4096 bytes, max56.
Literal parents are opened no-follow and pinned/rechecked; files must be owned,
regular, single-link and not group/world writable. Writes use exclusive0600
pending files, flush/fsync, descriptor-relative replacement and directory fsync;
existing conflicting bytes refuse. Readers validate references before path use
and recheck the ledger. These are cooperative same-user observations, not a
hostile same-UID or crash-atomic two-file transaction guarantee. An orphan
observation without a committed ledger row supplies no completion credit.

Launcher captures this closed bundle before root teardown and independently
of token/context/bulky export. Export commits/reopens the safe references before
initial FAIL, detailed or fallback result publication names the SAME bundle.
Failure receipts preserve it while keeping variants={} and status FAIL. The
first actual controller/action error remains independent of later cleanup or
export failure. Full success additionally joins all56 original result names,
order, statuses and exact times to the ledger, then retains every original
content/timing/diagnostic/cleanup/export/Health obligation. Partial records
never replace observations.json, E04/E05/E07 content, images or witnesses; any
missing obligation still makes run/Health FAIL. Missing/unsafe evidence stays
UNKNOWN; no historical PASS is reconstructed. Group isolation is deferred.

Individual case promotion also requires every required artifact/phase log in
that Python lane to pass the existing presence/nonempty rules and its result
to pass diagnostic decoding/projection admission. Missing/empty required images
(including lifecycle, response-loss, org-switch or browser-final) or rejected
diagnostic metadata keep ALL retained cases in that lane UNKNOWN: these checks
have no finer accepted case attribution. Ledger/result joins and E04/E05/E07
content checks cannot substitute for those admissions. Complete valid evidence
retains the original promotion path through all existing row/admission gates;
Health errors remain independently fatal.

Four answers / finite acceptance map (extends the existing R3-05/R3-06,
Controller.mark/export and aggregate.controls keepers):

| Cases | Observable contract and credible regression | Nearest keeper / precise gap and distinct risk | Test-only production seam / actual evidence |
| --- | --- | --- | --- |
| A1 adopted exited child while controller live | One actual generation-qualified consumed status/disappearance; controller remains live then retains its own intended exit. Removing active reap reproduces the late-wait dependency. | Owned tracking/cleanup, E07 strict disappearance and old R3-05/R3-06; WNOWAIT diagnostics/late numeric returns did not establish active reaping. | NO. One authorized isolated Linux exercise executed: adopted exit23, live controller then own37, setup parent own19; cleanup joined/one receipt. |
| A1 live/foreign-parent/unknown/stale/direct-Popen, including creation-registration | No wait/status theft; dropping generation/parent checks or registering exclusion after unlocking admits foreign or direct status. | Same keeper; creation interval and direct dependency Popen custody differ from adopted child eligibility. | NO. Same exercise observed overall refusal for live/foreign/unknown/simulated stale registration and a contending reaper blocked during real Popen creation/registration. Stale input also had live/foreign guards, so isolated stale-generation sensitivity is NOT proved; actual OS PID reuse was NOT forced. |
| A1 watcher plus cleanup/freeze | One consuming claim, actual statuses, no publication from an active watcher. A second consuming loop or pre-join publication loses status/immutability. | Same cleanup and stopped-watcher freeze; optional diagnostic completeness supplies no consuming authority. | NO. Same exercise checked duplicate refusal and stopped-watcher cleanup; asynchronous interruption/syscall-gap schedules remain source-reviewed, NOT RUN. |
| A2 completed prefix then later action/cleanup/export failure | Committed closed evidence survives producer memory loss; unsafe fallback erasure or marker-only success would lose evidence/launder failure. | Controller.mark, run failure_receipt/export, aggregate.controls. Previous terminal-only raw staging lost all cases. | NO. Existing controls execute actual mark/atomic publication/reopened prefix/copy and UNKNOWN refusal. Whole Product late-failure cleanup/export transitions remain NOT RUN; source ordering reviewed. |
| A2 identity/duplicate/conflict/path/secret safety | Closed bound exact identities and accepted references only; loosening unknown fields leaks arbitrary data. | Same existing aggregate.controls, extended completed_controls. Source/run join, filesystem custody and future-secret rejection are distinct from whole-run status checks. | NO. Actual publisher/reader controls cover wrong coherent identities, duplicate JSON/case, bad sequence/index/reference/digest, symlink/conflicting publication, raw-field rejection and secret-free bytes. |
| A2 all56 complete record | All original identities/results join without changing ordered membership or full final oracles; a partial-ledger shortcut could fabricate complete health. | Existing validate and independent validate_e04/e05/e07 remain; new join is consistency only. | NO. Actual producer/all56 ledger consistency control executed; full56 EACH3.12/3.14 Product remains manager-owned/NOT RUN here. |
| A2 required-file/diagnostic admission (TASK12950) | Missing/empty required artifacts or invalid diagnostics keep affected-lane cases UNKNOWN and Health FAIL; omitting either admission from promotion can falsely label all56 PASS. Complete valid evidence retains every original gate. | Existing main required-file loop/diagnostic projection and controls/completed_controls; controls cover metadata/ledger consistency, not main's full-artifact promotion. This gap concerns unavailable independent content despite an accepted ledger. | NO new test or production seam. PROOF NOT RUN: no existing complete full-artifact control fixture reaches main with valid independent content/Actions admission. Missing-image/invalid-projection before/after assessment is source-traced; old unknown-field proof does not cover it. No new harness or Product allocation. |

Task evidence records the exact Python3.14.4 commands/cwd, candidate source
hashes, complete streams and exits. `python -B e2e/aggregate.py --controls`
returned0 after one attributable intended-failure/restored-pass pair: temporary
unknown-field admission caused `unsafe completed case accepted: raw-field`,
exit1; byte-exact restoration then returned0. This establishes bounded reader/
publisher sensitivity only, not Product behavior, all concurrency schedules,
assertion completeness, sanitation of full raw exports,100MiB fit or full
same-runtime Product acceptance. No retired Python units were run/imported/
collected/restored/added; integration INCLUDING actual CLI callback remains
SKIPPED24342/211270-271. Protected12/mutations/repetition, abruptR/E04cap/
persistentStop and Mac limits remain unchanged. Required checks remain exactly
Source Boundary Lint and Web Lint(Node24); normal local all/hooks, independent
full review/same-head scoped QA and manager guarded merge remain required.

### Historical diagnostic ownership contract (TASK12803 / ROOT12738)

The original diagnostic-only delivery below is preserved for its receipts and
closed optional reader grammar. TASK12942 above governs current active reaping,
direct-Popen ownership and unavailable old waitpid-return attribution.

Manager step39 accepts TASK12796's consolidated54165B design/SHA256
`7959be74101b32b2b54c616e1d280f8e94746cd37e4a4cc457fcaa902ce9969b`
after independent TASK12799 **DESIGN ONLY PASS** (15083B/SHA256
`d7fada62c8f09ead0795ac27af00ada7bacd2288e92fb5d8fdcbeaacf7b43724`).
Founder THR291seq362 authorizes this ordinary bounded recovery, with HIGH/DEV-G03
satisfied by the current manager brief. Failed12753/12755/BLOCK22272 and12793
REVISE remain history; consumed ONE362 native retry12741 is never reused.

Only baseline `run.main`/its existing `command`, `Owned.__init__/sample/cleanup`
and existing result preparations change. `Owned.observe` remains the same one
watcher. identity/add/register_provider/signal, all original Popen polls/consuming
waits/signals and controller/E07/oracle/aggregate/Health/product/runtime/workflow/
wrappers remain dependencies. No helper/method/class, new import dependency,
observer/channel/traversal/poll/consumer/venue or executable scenario is added.
Cancellation is unarmed. This is diagnostic source, not a reaper/custody fix.

Successful immutable initial admission/root/launcher binding and actual scenarios
allocation precede observations. Original exit/finished/seconds/interrupted
assignments alone supply phase outcome; a complete terminal allocation survives
normal controller absence at the ORIGINAL cleanup entry as
`bound_terminal_absent`. Interrupted/partial absence is `bound_outcome_unknown`;
unbound/reused/malformed never mint a terminal join. Live joins require their
own same-generation brackets. Main alone consumes its first original cleanup
sample before TERM; watcher can claim at most one earlier active census. The
whole original immediate-child traversal is harvested (including the direct
phase child), never descendants/cgroups as substitutes or first17. Partial or
18th/unknown membership consumes/refuses the slot, without a replacement sample.

The original direct-Popen numeric PID is excluded from ALL optional wait probes,
even failed binding/reuse/absence; unknown numeric identity refuses probes. Each
admitted registered nonphase target has at most ONE pidfd_open, ONE
`waitid(P_PIDFD, WEXITED|WNOHANG|WNOWAIT)` and ONE close opportunity between valid
stat brackets. None means no requested exit state, ECHILD means ineligible, and
eligible_exit means only observed waitability at that instant. No consuming
fallback/adoption/exclusive-custody claim follows. Uncertain acquisition/close
disarms later opens. Actual raising TimeoutError precedes OSError. Original
positive waitpid returns alone supply raw W; the original later decode alone
supplies E (including negative signal exit). Generation attribution requires a
unique eligible precleanup pin and fully accounted consuming sequence; otherwise
unknown, with no phase-status transfer. Original interrupted custody risk remains.

Before any acquisition main selects an independent unavailable disposition.
Only its usable ORIGINAL cleanup return with watcher_stopped=true, ended main
writes and the SAME positive original cleanup deadline admit ONE detached freeze.
Active/unknown watcher/missing return permits no mutable-slot inspection/copy.
Claim precedes work; complete is last; interruption/refusal/exhaustion/format
failure is permanent, with no export retry/upgrade/late fill. The same admitted
member/absence feeds initial FAIL, eligible detailed and fallback preparations.
Detailed ORIGINAL redaction/reparse/full-context gates precede safe member attach;
redact/publish remain unchanged. Preparation is not persistence. Actual replace
supplies that effect, and final COMMIT remains terminal.

Closed local member `ownership_evidence` has exactly `availability,reason,data`.
Unavailable has data=null and a closed freeze reason; available has reason=none
and exactly binding/phase/active/precleanup/returns/freeze. U is exact int0..2^64-1,
P exact int1..2^64-1; D is the ORIGINAL ASCII [1-9][0-9]{0,19} string (run IDs,
attempts/start ticks), H lowercase40-hex, T exact finite int/float0..2^53-1,
E signed32-bit decoded exit, W unsigned32-bit raw wait status. Bool never counts
as a number; original Booleans remain exact bool. Null is literal null. No raw
exception/stat/comm/argv/env/path/context/credential text is admitted.

| Record | EXACT keys and domains/null rules |
| --- | --- |
| binding | `state,reason,source_sha,source_tree,event,run_id,run_attempt,exercise,root,launcher,subreaper`. state=`unbound|bound|refused`; reason=`none|unreached|incomplete|interrupted|mismatch|malformed|budget`. A bound record requires reason none, H source_sha/source_tree, event=`schedule|workflow_dispatch`, D run_id/run_attempt byte/type-equal to ORIGINAL admission, exercise=`baseline`, root and launcher below, subreaper=`initialized`. Non-bound record has all nine identity/setup fields null, reason non-none; no later reconstruction. Full admission still independently checks repository/workflow/ref/cron; no new permissive admission. |
| root | EXACT `device,inode`, both U with inode>0, from ORIGINAL root_info/root_identity, not pathname or a new stat. |
| pin | EXACT `pid,start`: P and D. Launcher pin comes from same-buffer independent launcher bracket; controller pin from actual original Popen PID, owner.add generation and initial controller bracket. |
| phase | `name,allocation_state,reason,controller,entered,deadline,outcome,exit,finished,seconds,interrupted`. name=`scenarios`; allocation_state=`unbound|bound|refused`; reason uses binding reasons. Bound requires controller pin, T entered/deadline with entered<=deadline, exact original cap/min relation; unbound/refused has these three null. outcome=`unreached|partial|terminal|interrupted|malformed`. exit=E/null; finished/seconds=T/null; interrupted=exact bool/null, only from ORIGINAL assigned fields. terminal requires bound, E exit, finished>=entered, seconds=finished-entered, interrupted=false and fully completed original finalization. interrupted requires bound, exit=null, complete valid finished/seconds and interrupted=true. partial copies only valid actually assigned exit/finished/seconds/interrupted, leaving missing fields null; it supplies no terminal join even if exit82 was assigned. Unreached has those four null. Malformed nulls those four and supplies no outcome claim; no coercion. A valid original null exit with interrupted=false is partial, not successful terminal. |
| census slot (active and precleanup have same shape) | `state,reason,owner,entered,finished,deadline,join,launcher_a,launcher_b,controller_a,controller_b,targets`. state=`unattempted|claimed|incomplete|refused|complete`; owner=null or `watcher|main`; times T/null. join=`unknown|bound_live|bound_interrupted_live|bound_terminal_absent|bound_terminal_observed|bound_outcome_unknown|never_bound|reused|mismatch|malformed`. A complete slot has reason none, full times with entered<=finished<=deadline, valid launcher brackets and whole finite targets array. Unattempted has reason unreached, owner/times null, join unknown, four stat fields null and targets=null. Claimed/incomplete/refused have non-none reason, targets=null and no admitted positive stat fields; owner and only valid reached times may survive. At freeze an unfinished claimed slot projects as incomplete, never promotes itself. Refused join may retain only its closed classification; no partial census rows. |
| stat (four context fields and target a/b) | EXACT `outcome,at,start,state,ppid`. outcome=`observed|absent|unreadable|interrupted|empty|partial|oversized|malformed|unattempted`; at=T for actually reached read, otherwise null. Observed requires D start, P or0 ppid, state one of `R,S,D,Z,T,t,W,X,x,K,P,I`. All other outcomes have start/state/ppid=null. One complete LF-terminated<=4096B stat buffer, leading requested PID, final comm-closing delimiter and sufficient tail fields; start/state/ppid come from SAME tail19/0/1. Acquisition max4097B detects overflow; no raw text copied. Only ENOENT/ESRCH means absent at that read. |
| target | `pid,start,kind,registration,a,b,wait,close`. pid=P; start=D original registered generation or null; kind=`direct_phase|phase_number_excluded|nonphase`; registration=`matched|unregistered|reused`; a/b are stat/null. Deduplicated in original launcher-child encounter order, at most17 TOTAL immediate children including direct phase. direct_phase means original bound PID/generation; phase_number_excluded means same original direct Popen numeric PID but unavailable/different generation. BOTH are excluded from all optional wait probes. Nonphase requires not that numeric PID and a current matching registered pin before probing. No descendant-only or cgroup-only membership substitutes for launcher-immediate-child census. |
| wait | EXACT `state,reason,outcome,at,code,status`. state=`unattempted|claimed|incomplete|refused|complete`; outcome=`unknown|no_exit_state|ineligible|eligible_exit`. at=T only for reached optional call; otherwise null. code=null or `CLD_EXITED|CLD_KILLED|CLD_DUMPED`; status=null or exact nonnegative signed32-bit int. Positive CLD_EXITED status0..255; KILLED/DUMPED status>0. Only complete eligible_exit has code/status non-null; it requires matching si_pid, SIGCHLD, valid brackets and successful single close. Complete no_exit_state has reason none, code/status null (Python None). Complete ineligible has reason echild, code/status null (ChildProcessError/ECHILD). Unattempted reason excluded/not_registered/unreached/close_unknown; claimed/incomplete/refused outcome unknown and code/status null. No `None` as live/absent/adopted and no ECHILD as another named owner. |
| close | `not_opened|closed|unknown`. Exactly one close attempt for each successfully obtained descriptor; unknown never becomes a qualified positive. No descriptor number exported, transfer, fallback or retry. |
| returns | `state,reason,outcome,rows,coverage`. state=`unattempted|claimed|incomplete|refused|complete`; outcome=`unreached|returned|interrupted|unknown`; coverage=`none|all_reached|partial`. Unattempted is reason unreached/outcome unreached/coverage none/rows=null. Claimed or refused has outcome unknown, non-none reason, coverage none and rows=null. Complete requires reason none/coverage all_reached/outcome returned and array<=34 of records below; [] is valid ONLY for a completely annotated original path with no positive returns. Incomplete requires non-none reason, coverage partial, outcome interrupted if known else unknown, and actual earlier rows or null if none recorded. Missing work is never represented by complete/[]. Original reaped list/behavior unchanged. |
| return row | `index,pid,wait_status,exit,generation,join,slot,target`. index=int1..34 original positive-return order; pid=P actual waitpid return; wait_status=W actual raw return; exit=E/null ORIGINAL existing decoded value, null if assignment not reached. generation=D/null; join=`qualified|unknown`; slot=null or `precleanup`; target=null or index0..16. Qualified requires a unique preceding precleanup complete eligible_exit pin and fully accounted uninterrupted consuming sequence with no intervening same-PID consumption/reallocation; generation/slot/target identify that pin. Otherwise generation/slot/target=null and join unknown. Never qualify solely by PID/status equality, registry history, later absence or a matching decoded exit. |
| freeze | EXACT `state,watcher,writes`; fixed `frozen,stopped,ended` only. These assert original watcher termination positively observed and optional main writes ended BEFORE projection. They do not assert owner cleanup.ok, product closure or persistence. |

Closed reason selection: binding/phase reasons are as above. Census and wait reasons are ONLY `none,unreached,incomplete,interrupted,excluded,not_registered,unbound,mismatch,reused,malformed,absent,unsupported,io_error,no_budget,overflow,reentry,off_owner,echild,close_unknown,cycle_limit`. Return reasons are ONLY `none,unreached,incomplete,interrupted,malformed,annotation_limit,no_budget`. Freeze/unavailable reasons are ONLY `not_frozen,watcher_active,watcher_unknown,main_writes_open,interrupted,no_budget,malformed,overflow,format_refused`. None is the enum spelling `none`, never JSON null for a reason. Applicable reason/state joins above are closed: no positive payload with a refusal reason. An impossible joined shape rejects the whole optional member into unavailable/malformed or omission, never repairs one field. Unknown means no supported claim, not an unlisted enum.

Cross-record admission: available requires bound binding; phase controller pin and original times are the same immutable allocation used by both slots. Active/precleanup complete owners are watcher/main respectively; their deadlines equal ORIGINAL scenarios/cleanup ends. Every admitted stat/wait time lies in its slot interval; launcher_a<=target work<=launcher_b, and target a<=wait<=b when wait actually ran. Launcher A/B starts equal binding.launcher.start; target matched brackets equal its original start and both PPIDs equal binding.launcher.pid for a measured current immediate-parent claim. Bracket change yields unknown/refused target wait, not positive eligibility. Complete active requires original phase still active through its commit, and entered>=phase.entered. A subsequently terminal phase can coexist with that earlier bound_live active slot; it is not a current-live claim at freeze. Precleanup starts at original cleanup entry and follows any reached original phase finished time. Controller current-live joins require same-generation nonterminal A/B; terminal-absent is the explicit exception requiring complete original terminal assignment, with absence but no live bracket. Unbound phase has outcome unreached/partial/malformed only and cannot yield complete slots. All noncomplete slot stat fields are null; claimed state is internal and becomes incomplete at frozen projection. Within a complete census, unattempted target wait is unknown/null-code/null-status; target calls interrupted mid-work cannot masquerade as complete. PID order, distinctness, source/root/run/attempt/instance and immutable-reference equality are mandatory, without new syscalls.


TWO <=17-total-target slots <=8192 formatted UTF8 bytes EACH; rest<=16384B;
whole actual optional addition<=32768B including key/wrappers/indent/separators.
Admission uses indent=2 at attachment depth and refuses rather than clipping.
At most80 stat acquisitions<=327760B,34 pidfd/waitid/single-close opportunities,
one optional target fd live,512 aggregate clock checks,34 actual positive-return
annotations/1156 bounded pin comparisons,24000 existing-cycle checks/432000
bounded token steps. All work charges ORIGINAL action/scenarios/cleanup ends;
no refill/borrow/new timer/lock wait/extra join. These cooperative bounds are NOT
hard timing/RSS/syscall/serialization or100MiB-fit proof.

#### Affected finite cases and four answers

Environment for ALL rows is the EXISTING authorized disposable hosted Ubuntu baseline launcher/controller, synthetic org/private root, genuine public CLI/browser/provider boundaries, EACH Python3.12/3.14; no new fixture/venue/seam/executable test. C=actual raw exception/object/savedTB/creator/selected/raising; H=actual same-invocation generation/status/custody/timing/file effect; J=independent reopening of ACTUAL FINAL safe bytes. J cannot prove private C or unrecorded H. Each row extends the named existing keeper, not a new parallel test.

| Keeper / finite input and ORIGINAL action | Exact independent FINAL expectation / distinct risk |
| --- | --- |
| R1-07/R1-08/R2-08, R3-05/R3-06: complete allocation -> ORIGINAL terminal assignments -> controller normally absent at original entry | If stopped/admitted, phase terminal with actual exit (e.g.82), precleanup complete/none/bound_terminal_absent, controller absent, independent target waits eligible/no_exit_state/ineligible as actually observed; no current-live or product PASS. H real original poll/reap vs J historical join. |
| SAME binding/return keepers: bound interrupted controller still live; bound interrupted/partial outcome absent; never-bound before/after Popen/add; terminal assignment interrupted after exit/before finished/interrupted | Live bracket gives bound_interrupted_live with exit=null; absent interrupted/partial gives bound_outcome_unknown, never terminal. Never-bound gives refused/unbound/never_bound, targets=null; internal original direct numeric PID always excluded. Partial retains only actual typed assignments. Global active/unknown watcher still yields data=null/omission. C original interruption differs from H custody and J null joins. |
| R1-07/R1-08/R2-08: reused controller/launcher, foreign source/run/attempt/root/Owned, malformed Boolean PID/start/run/time/exit, changed immutable allocation | refused/reused/mismatch/malformed as applicable; launcher failure cannot admit target census. No string conversion or later binding. Exact original admitted D strings survive valid projection. J rejects impossible joined shape; H original generation observation is separate. |
| R3-05/R3-06/R2-08: watcher enters sample before main entry, during main entry marker, after refused original entry; later cleanup samples; active closure/replacement/reentry across formatting and slot installation, including interruptions | Watcher can own only one active slot; precleanup owner=main only. Original main entry interruption/refusal consumes precleanup; no later complete replacement, no watcher stealing. After a stopped-watcher freeze, an invalidated active attempt has refused/unbound (closure alone), refused/reentry (changed immutable reference or revoked claim), or incomplete/interrupted, with targets and all four stat fields null; an unfinished displaced claim projects incomplete/incomplete. A valid completion entirely before closure remains complete/none/bound_live even with a subsequently terminal phase. H actual pre-first-signal/closure/store ordering differs from J final slot fields; C original raising/exception handling remains separate. |
| E07.cancel-cascade-browser/R3-05: registered immediate child vs descendant-only/nonchild; Python None; ECHILD; eligible EXITED/KILLED/DUMPED; unregistered/absent/reused/unsupported/I/O/mismatched result; phase PID in EVERY branch | Wait complete/no_exit_state/null-null vs complete/echild/ineligible/null-null vs complete/none/eligible_exit/actual code-status; unknown/refused otherwise. Phase wait always unattempted/excluded, even reused numeric PID or terminal absence. Same-generation Z still blocks original E07 closure. H non-consumption/current eligibility is distinct from J enums and from adoption/exclusive custody. |
| R1-11/R2-01/R2-03/R2-04/R2-08/R3-06: claimed/partial traversal, missed task,18th distinct child, reentry, off-owner, cycle exhaustion; deadline during read/parse/commit | incomplete/interrupted or refused precise reason, targets=null, one attempt consumed; no first17/late nicer sample. Earlier other completed slot may survive ONLY stopped-watcher freeze. C original raw winner/TB/selected and raising TimeoutError precedence persist; H bounded work; J incomplete is not empty-success. |
| R3-05/R3-06: original zero/ECHILD,1..34 positive raw returns, negative decoded signal exit, interruption before/after existing decode/annotation,35th return, stale/no eligible pin or unaccounted sequence | Only actual positive rows, original W distinct from E (e.g. signal status vs -15). Null decoded exit if assignment unreached; coverage incomplete/partial on gaps/limit, never success by empty rows. Qualified generation only unique precleanup eligible pin+accounted sequence, otherwise unknown/null joins. No phase exit filled from cleanup reaped rows. H actual consumption vs J provenance; preexisting custody risk retained. |
| R1-11/R2-08/R3-06/X-01: original watcher stopped/active/unknown, errors but stopped, main writes open; real interruption/exhaustion/malformed/overflow during freeze | Stopped+ended may give available even cleanup false; active/unknown/open writes -> exact unavailable reason/data=null without mutable inspection. Interrupted freeze permanently unavailable/interrupted or omission; no late completion/export upgrade. Original watcher errors continue. C propagation/H thread lifetime/J detached consistency differ. |
| R3-01/R3-09/X-01: stopped watcher and admitted data -> REQUIRED:U, failed capture/failed[], invalid/missing full context, later root loss/export ValueError; counterpart with unavailable freeze | ACTUAL safe final FAIL may contain SAME available member or fixed unavailable/absence. variants={}, skipped0, false flags, sticky incomplete; all private detail remains withheld. No context repair/partial PASS or universal delivery. C capture[]/selection, H root/publication, J safe noncredit are distinct. |
| R3-07/X-02: slot8192/rest16384/whole32768 boundary and one byte over after actual wrappers/indentation; unavailable form not admitted; original serialization/write/replace interruption at each of three paths | Format overflow -> permanent unavailable/format_refused or whole omission, never clipped/retried. Every actually committed initial/detailed/fallback contains equal ownership member/absence. If first publication never commits, result may be absent; later failure can leave initial bytes. Final COMMIT terminal, no postcommit check/rescue. H actual replace vs J final bytes; no persistence inferred from a prepared object. |
| E07.cancelled-late-cli / E07.cancel-terminal-unchanged | Original full1s/<=50ms negative window, delayed409/dead downstream, genuine PID/groups/pending, public/SQL/task/result/audit/lineage and final screenshots unchanged. Phase-active interval is not E07 gate interval; compare genuine safe retained E07 pins/times before any interval claim, otherwise UNKNOWN. No diagnostic buys time or replaces content. |

TASK12814 extends that SAME R2-08/R3-05/R3-06 concurrency case, following
independent FULL reviewer12810's HIGH finding. Exact schedule: watcher passes
active/reference/claim checks; formatting yields; main closes active_open before
original finalization and may replace evidence_phase at cleanup; watcher resumes
within the original deadline and installs its candidate. Installation alone is
provisional: the post-install gate detects the monotone closure/replacement and
permanently refuses the installed candidate with null targets/stat fields.
This gate performs no later positive store, so it leaves no check/store gap.
The failure target is rebound to the candidate BEFORE installation: interruption
after that handoff/store cannot scrub only the displaced claim and leave a
complete candidate. Before handoff, the consumed original claim remains null;
freeze projects any unfinished claim as incomplete/incomplete. Before successful
admission, a propagated interruption clears the current failure target to
incomplete/interrupted. No later sample retries or upgrades either disposition.

Review the finite boundary orderings within these existing cases: closure before
formatting, during formatting, after formatting/last old checks but before store,
and after store but before the post-install gate; phase/reference replacement or
claim reentry before/during that interval; interruption before candidate handoff,
between handoff/store, and after store before admission; and the unaffected
success with unchanged claim/references and open phase through admission, followed
by later closure. Closure after a successful post-install check necessarily
follows an already valid installation; it does not erase that historical success.
No window reopen/reference restoration/claim revival exists in this invocation.
An active/unknown watcher still forbids live inspection altogether. J independently
reopens actual FINAL result bytes after original stopped/ended freeze and expects
the exact refusal/incomplete/nulls above or the unaffected earlier bound_live
completion; all three existing preparations use that SAME frozen disposition.
If global freeze is unavailable/omitted, J expects data=null/the same absence,
not fabricated slot detail. H ordering and C interruption evidence cannot be
inferred from J alone. These are unexecuted finite cases, not a historical
occurrence claim or a new executable concurrency harness.

Four authoring answers for EACH affected existing keeper and ALL its finite subinputs above (F1 and F2 jointly):

| Existing keepers | 1 Observable contract | 2 Credible regression | 3 Nearest keeper / nonduplication / distinct risk | 4 Test-only production seam |
| --- | --- | --- | --- | --- |
| R1-07/R1-08/R2-08 binding, terminal-absent/interrupted/unbound/reuse/partial/type inputs | Final phase allocation/outcome/current observation joins have the exact selected nulls/states; normal reap preserves historical terminal join. | Require live stat after genuine poll, infer unbound phase from registry, coerce Boolean/string IDs, fill partial finalizer or swap invocation. | Existing admission/stat pins own source and parentage, not baseline allocation plus original-terminal absence. Extend their table; H genuine pin/poll differs from J selected joined fields. | NO; existing qualified owners only. |
| E07.cancel-cascade-browser/R3-05 immediate census/eligibility and direct exclusion | Final distinctions unattempted/None/ECHILD/eligible; unchanged original generation closure and real Popen exit. | Drop WNOWAIT, probe direct PID on unbound/reused/absent branch, accept None as live/closed, treat nonphase as adopted. | Existing stat keeper lacks wait-state meaning; direct Popen keeper owns its exit, not another child's eligibility. H non-consumption differs from J representation; no duplicate reaper case. | NO. |
| R3-05/R3-06 original-return/terminal/interrupted/partial/limit inputs | Authentic W/E and conservative generation join; incomplete coverage never empty-success, no phase-status transfer. | Decode raw W as exit, use PID equality as generation, reuse reap exit for phase null, annotate unseen return or reset limit. | Original cleanup keeper owns consuming wait, failure receipt omits detailed attribution; extend its projection. H actual consumption cannot be proven by J numeric equality. | NO. |
| R1-11/R2-01/R2-03/R2-04/R2-08/R3-05/R3-06 claims/original-entry/reentry/budgets/freeze-interruption inputs, including TASK12814 formatting/store/closure orderings | Original C exception/TB/creator/selected/ends unchanged; main-only entry, consumed incomplete/refused claims and one permanent freeze disposition in FINAL bytes. R2-08/R3-05/R3-06 J expects invalidated active attempts refused/unbound or reentry, interrupted attempts incomplete, all with null targets/stat fields; an unchanged earlier completion survives later closure. | Let watcher steal entry, refill >17/partial slot, reset claim/end, catch TimeoutError as ordinary OSError or upgrade interrupted freeze; move the final gate before store or clear only the displaced claim on a post-store interruption. | EACH25 existing R2-01 inputs own precedence, R2-06/07 own capture[]; R2-08 phase binding and R3-05/06 entry/freeze own this new ordering gap, so extend them together. C propagation, H actual phase/store/thread ordering and J final refusal are distinct; no duplicated wait/reaper test. Same existing hosted Ubuntu/private-root/genuine public boundaries on EACH3.12/3.14; timing/scheduling remains an unexecuted environment risk. | NO; no new export, flag, fixture, callback, verifier or injection seam. |
| R3-01/R3-09/X-01 watcher stopped/active/unknown, withheld-context/root-loss/REQUIRED:U/three-path inputs | Safe detached SAME member only under stopped+ended; otherwise fixed unavailable/omission. Existing FAIL/withholding/capture[] persist. | Shallow-copy live slots, infer stopped from join timeout, hide behind COMPLETE reader, copy private text/context, reset sticky fail. | Existing collection/context keepers own withholding, X-01 owns same-invocation composition; current fallback omits ownership detail. C[]/H watcher-root/J sanitization differ; do not duplicate precedence tests. | NO. |
| R3-07/X-02 format/three-publication/late-write/terminal-commit inputs | Whole formatted ceilings and equal admitted member in actual initial/detailed/fallback; only real replace establishes persistence. | Measure compact payload only, clip first17, rebuild at export, let late writes alter fallback, rewrite after COMMIT. | Existing12649 publisher keeper owns terminal commit; extend additive-content survival/format refusal there, no new publisher. H file effects differ from J bytes. | NO. |

Original30/SAME13/ordered56 EACH3.12/3.14/L1–L7/16 browser sites/full maps and
genuine20E05–24E07 joins/prior EACH25 R2-01/X-01 gaps remain. Raw W/P/object/
savedTB/creator/selected/H0/saved adapter, raising interruptions, THREE controller
publishers, capture B->[]->B versus failed[]->C, frozen full context/withholding/
sticky failure, Z-blocking/groups/pending/full1s/<=50ms/late409/dead downstream/
public-SQL-task-result-audit/screenshots assertions are unchanged.

**Candidate NOT RUN; PROOF NOT RUN; sensitivity/cost/serialization/100MiB-fit
UNPROVED for EACH affected input.** The actual failed venue is not a capability
exemption. No new verifier/harness/fixture/venue/protected campaign is added.
Python units RETIRED; integration INCLUDING callback/lifespan/SessionTracker
SKIPPED24342/211270–271. Static checks prove no behavior or test-authoring PASS.
Latest assessed40047851140 stays FAILED/HealthFAIL11/EACH56UNKNOWN-null-zeroPASS/
skipped0;972 prior stat samples are PARTIALLY EXECUTED, COMPLETE reader unreached.
Old12724 custody/effects/retirement stay separate OPEN/UNKNOWN; consultant stand-in
unallocated and PR1180 cause/adoption/wait/reap/exclusive custody UNPROVED.
Independent FULL review/same-final-H scoped SOURCE QA, normal hooks/nonforce,
exact-head TWOstatic and later manager guarded merge/exact-main TWOstatic remain.
No Product run/dispatch/merge/deploy/restart or historical reconciliation follows.

### E07 process-state observations (TASK12784 / ROOT12738)

Manager step31 accepts TASK12782's exact50050B design/SHA256
`3de9f37ac0d3bc69f02011368ced38e020c651fad2fde129a0e8b6d8fec7b97f`
after independent TASK12783 **DESIGN ONLY PASS** (29157B/SHA256
`9e12849b391b5b14d047cb18e52e5c4e3d4cba0d13fd7e0f053831044527e45e`).
Founder THR291seq362 authorizes ordinary bounded recovery under SAME ROOT12738;
the ONE native retry consumed by12741/revisit12724 is not reused. This extends
the existing cascade/window/complete-reader/R2-01/X-01 keeper records only.
Original30/SAME13/ordered56 EACH3.12/3.14,16sites/full maps and genuine dynamic
20E05/24E07 source-file membership remain unchanged, as do EACH25 prior
R2-01/X-01 proof gaps. Terminal-unchanged's inputs/assertions remain unchanged.

`closure` first assembles the ORIGINAL `processes` identity reads in their
original order, `groups`, `sampled` and `pending`, with original exceptions.
Those fields remain the sole closure oracle. Only then each process row gains
`diagnostic`, and the snapshot gains `controller_diagnostic`. Neither can supply
`observed_start`, remove a group member, clear pending, create a freeze or accept
same-generation Z as closed. All public/SQL/terminal/freeze/late POST409 and
dead-downstream gates, full1s/<=50ms observations and original ends remain.

Each diagnostic contains entered/finished, expected_start, target_a, parent,
target_b, parent_pin and relation. Each attempted read contains only PID,
start/state/PPID, finite reason and its own entered/finished times. ONE
unbuffered stat read acquires at most4097B and admits at most4096B, requires
the final LF record and comm-closing delimiter, exact requested leading PID,
sufficient tail fields, single ASCII state letter,20-digit nonnegative PPID
and20-digit positive start ticks. State/PPID/start come from that SAME buffer's
tail0/1/19; comm/raw stat/path/exception/argv/environ/credentials are not exported.
Reasons are `observed`, `absent`, `unreadable`, `interrupted`, `empty`, `partial`,
`oversized`, `malformed`. Every non-observed reason has null start/state/PPID;
only ENOENT/ESRCH means absent at that separate read, never at another interval.
Booleans/nonfinite times/wrong scalar types are invalid optional content.

TargetA admits ONE parent read then targetB only after a valid original-generation
match with positive PPID. Absent/unknown/reused A stops the triple; PPID0 has
no-parent. Bracketing requires A/B start and PPID agreement, allowing real state
changes. Relations are target-absent/target-unknown/target-reused/no-parent,
target-changed, parent-missing/parent-unknown/parent-reused, or bracketed.
Parent reuse is claimed only against an existing tree/CLI/daemon or bracketed
context pin. Unpinned parent generation is measured only. Self/ONEparent/self
context joins the existing scenarios PID/daemon PPID and, when available,
owner.add controller generation. The measured immediate parent has launcher
ROLE **UNKNOWN**: no independent same-invocation launcher pin exists. Numeric
PPID, source Popen structure or another invocation's active.json cannot grant it.
Sequential reads do not establish atomic genealogy, continuous custody,
adoption, wait/reap ownership, no PID race, or a causal cancellation fix.

At most17 existing targets (tree16+CLI), three reads each plus three context
reads gives54 reads/221238 acquiredB PER closure, with no old sample dropped or
truncated. Existing `c.remaining()` checks before I/O and after completed work/
before return charge the ORIGINAL effective minimum including local/observation/
inherited ends. TimeoutError precedes OSError; raising alarms, KeyboardInterrupt,
SystemExit, GeneratorExit and controller RuntimeError propagate. Ordinary narrow
I/O/parse failures supply finite UNKNOWN reasons without retry. No outer
assertion/deadline catch, timer operation, phase/case mutation or new allowance
is added. The interrupted unreturned latest sample remains lost/UNKNOWN.
Finite bytes do not certify syscall latency,50ms cadence, serialization size,
RSS or100MiB fit; actual cost/fit remain **UNPROVED**, never a reason to widen.

Failure consumption is useful **MANUAL inspection** of existing safely sanitized/
staged e07.json/observations.json, with source/run/attempt/archive/pin/interval
attribution and original failure intact. A completed prefix can survive later
timeout through original wrapper/main full fresh context, evidence export and
safe staging BEFORE REQUIRED:U, even with later root loss/export failure.
This is conditional: stage_small_evidence alone does NOT stage e07. No completed
sample, unreturned work, exhausted other ends, residual owner, failed capture/[],
unsafe/missing full context or write/staging/export interruption can withhold
all detail. Original raw W/P/object/savedTB/creator/selected/H0/adapter, THREE
publishers/conditional poison ATTEMPT, capture B→[]→B versus failed[]→C,
preparation expiry, frozen sanitation, withholding, sticky failure and terminal
COMMIT stay intact. No extra save/capture/raw rescue/last-good/bearer-only context
or fabricated final screenshot/scenario/cleanup follows.

`validate_e07` independently checks optional diagnostics for COMPLETE cases
only, AFTER every original full e07==observations.e07==row.e07, actual-source,
manifest/prefix and final behavioral gate. Historical absence means unobserved;
valid UNKNOWN gives no state or closure credit. It checks all retained samples,
strict keys/types/nulls/finite times, original target order/pins, sequential read
and parent relations, supported controller receipts and54-read cardinality.
It imports no producer parser; copied agreement cannot prove a syscall ran.
Actual aggregate.main's E04-first failed try SKIPS validate_e07, so these checks
are no automatic failure-tail consumer. main/_diagnostic_projection/Health,
run.py launcher pins, new carrier/channel/protocol/observer/driver/venue are
outside this selected implementation.

Finite accepted cases below use the EXISTING disposable hosted Ubuntu genuine
/proc, synthetic orgs/private root, real public CLI/browser/provider stubs and
original controller/launcher on EACH Python3.12/3.14. C observes actual error/
object/TB/selection; H observes actual same-invocation process/timer/file/custody;
J independently reopens frozen safe evidence. J proves neither private C objects
nor unexported H state. No executable test/fixture/seam is added or run here.

| Existing requirement → finite precondition/input/action | Independent FINAL observable | C/H/J distinct risk |
| --- | --- | --- |
| Cascade: matching original generation in R/S/Z/D/T/t/I/X/other state; original disappearance or PID reuse → unchanged poll, later stat | Original match always blocks closure; later same-buffer state/PPID has its own pin/time. Absence/reuse cannot rewrite earlier identity. | C original gate; H actual transitions; J attribution, not OS execution proof. |
| Cascade: EACCES/EIO/EINTR/empty/short/overlength/missing delimiter/LF/wrong PID/state/PPID/start/20-digit overflow → bounded read/parse | Finite UNKNOWN/nulls; original identity errors still escape; no null-as-disappearance. | C narrow exceptions; H real I/O; J strict types/reasons. |
| Cascade/context: valid A, PPID0 or ONE parent then B; parent absent/reused/unpinned; B changed/missing/error; R↔S/Z; controller/daemon/wrapper/provider/unrelated parent | Only stable same-generation PPID bracket gives measured relation; parent pin comparison only where available; launcher role UNKNOWN. | H sequential races/custody; J independently ordered complete pins, no adoption/wait inference. |
| Late CLI: shared closure after delayed409/dead downstream during full1s/<=50ms negative window | All original IPC/content/witness/generation/group/pending/final assertions retained; diagnostic cost can cause a real failure. | C public/window decision; H actual time; J original completed evidence. |
| Terminal unchanged: shared full export after genuine terminal refusals | Exact original human409/task/result/audit/lineage/job/thread/public content still required. | Unchanged keeper, no new terminal case. |
| R2-01: no incoming or existing W/P; interrupt before/during read/parse, after sample or before return; each original global/case/local/observation/inherited bound expires; residual positive/expired/native timer | Real raising exception and original outgoing/TB/creator/selected precedence survive; previous completed prefix may survive, unreturned sample may not; no bound/timer release by diagnostics. | C actual object/TB; H alarm/work effects; J cannot reconstruct them. |
| X-01: completed prefix→later timeout→save fails, authentic eligible exit and ALL other original ends positive→full fresh capture/export/staging→REQUIRED:U/root loss/export failure | Safe earlier diagnostic MAY survive with original FAIL/variants{}/false cleanup/error precedence; no fabricated final receipts. | C original selection; H real publication/root effects; J frozen noncredit. |
| X-01 withholding: absent completed sample, lost latest sample, exhausted end/residual owner, capture/failed[]/context/write/staging failure | UNKNOWN/missing detail stays withheld, sticky incomplete/FAIL remains; no fallback repair/second save. | C/H actual refusal/custody; J absence is no closure. |
| COMPLETE reader: optional absent/valid/UNKNOWN/malformed, wrong generation/parent/order/nonfinite/type/source or contradictory copy | Old absence remains unobserved; malformed positives refuse; every original equality/source/final gate still controls, skipped E04 path stays skipped. | J independent exported-content check cannot certify OS facts. |

Each affected keeper has these four answers; EACH finite subinput above remains
candidate **NOT RUN**, intended-failure/restored-pass **PROOF NOT RUN**,
sensitivity **UNPROVED**. Independent DESIGN acceptance is not test-authoring
or behavioral PASS; the genuine failed venue is no unavailable-support exemption.

| Keeper | 1. Observable contract | 2. Credible regression | 3. Nearest keeper/gap and distinct risk | 4. Test-only production seam |
| --- | --- | --- | --- | --- |
| Cascade / closure / exercise.closed | Known generation must disappear before freeze; state/parent remains at its own pin/time. | Forgiving None replaces original oracle, Z accepted, stale/reused state attributed. | Initial tree/launch owns pre-cancel identity, Owned owns later cleanup; existing cascade owns pre-rescue observation. C gate/H race/J attribution differ. | NO flag/export/module/injection/observer/driver. |
| Late CLI / negative window | Genuine delayed409/EOF or peer-gone plus unchanged full1s/50ms/generation/group/pending. | Widen timing for diagnostic cost, ignore Z, substitute diagnostic for response. | Cascade freeze and terminal request do not own post-forward IPC/window; existing window keeper retained. H time differs from J consistency. | NO new seam/retry. |
| validate_e07 COMPLETE optional reader | Independently reopened typed ordered pin/context relation; UNKNOWN gives no closure credit; all original gates. | Import producer oracle, accept reused attribution or bypass source/full equality/finals. | Producer assertions do not own export consistency; existing complete reader does. It cannot own failed-tail reachability or syscall proof. | NO venue/fixture/production export. |
| R2-01 EACH affected read/parse/interruption/budget input | Actual outgoing/TB/raw W-P/savedTB/creator/selected under original ends. | Catch TimeoutError under OSError, broad swallow/retry/renew/release, overwrite original winner. | Existing25 wrapper inputs stay open; R2-06/07 own capture[], not process observation interruption. C private identity and H alarm cannot be inferred from J. | NO injected source/runnable case/seam. |
| X-01 EACH affected prefix/timeout/save/capture/staging/root-loss/withholding input | Safe earlier snapshot may survive while original FAIL/variants{}/false cleanup/error precedence and withholding persist. | Raw partial rescue, stage_small-as-e07, lost-context refresh, sticky failure reset or snapshot-as-closure. | R2-01 stops at propagation; R3-01/09 own collection/withholding and R3-07/X-02 COMMIT. Existing X-01 owns SAME-invocation composition; J cannot recover lost C/H facts. | NO carrier/channel/verifier/driver/venue. |

Latest ASSESSED40038807544 remains FAILED/HealthFAIL11/EACH56UNKNOWN-null-zeroPASS/
skipped0. BOTH initiating closure-poll-sleep absolute TimeoutErrors and483/476
provider+CLI matching generations, absent wrapper/empty scope/pendingfalse remain
historical; postcancel state/PPID/adoption/waitowner/PR1180 cause remain UNKNOWN/
UNPROVED. Eight Playwright errors+eight incompleteE05 EACH/provider observernull/
REQUIRED:U/direct82/missing finals/cleanupfalse/export ValueError remain. TERM15/
3.12 and INT2/3.14 are diagnostic-only. Prior FOUR archives/all122members/57logs
retain their attribution; this source leg collects none. Units RETIRED;
integration INCLUDING callback/lifespan/SessionTracker SKIPPED24342/211270–271.
Protected12/mutation/repetition and Product execution are not allocated here.
Old12724 exact custody tuple remains UNKNOWN/separate OPEN, not reconciled by
this source work. Fresh FULL independent review/same-final-H scoped SOURCE QA,
normal hooks/nonforce and TWOstatic head/main plus manager guarded merge remain;
source/static acceptance does not establish Product/reset/native closure.

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
