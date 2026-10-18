# Root task Pause and Resume

> Status: current
> Current source: runtime/infrastructure/task_pause_controls.py,
> runtime/orchestrator/task_pause.py and docs/agent-guides/orchestrator-contracts.md
> Authority: THR-292 seq14, approving seq9 as amended by seq13.

Pause is a separate per-org root control. It does not add a lifecycle status or
block kind. A root is the actual NULL-parent task, not a revisit, thread or
supersession family. Every descendant inherits that root's effective hold.
A newly requested Pause requires an in_progress root. Pending roots refuse with
409 task_not_in_progress, without a control toggle, claim or synthetic session.
Existing holds survive escalation; terminal lifecycle takes precedence.

## Controls and observation

POST /api/v1/orgs/{slug}/tasks/{task_id}/pause and /resume use the existing
bearer-authenticated task control surface. The closed body is
`{"expected_generation": integer}`. Responses contain `changed` and a separate
`pause` projection. A successful toggle increments the generation and audits
once. Duplicates do neither; stale opposite-state intent is a 409
control_generation_conflict, not an automatically retried toggle. Descendants
refuse root_task_required and identify their actual root. Unknown tasks are404;
terminal roots refuse task_terminal. Generation overflow, invalid control or
journal evidence refuses pause_control_unavailable.

`happyranch pause TASK --org ORG`, `resume TASK --org ORG` first read the current
generation unless --expected-generation is supplied, then post once. --json
returns the control response. The TypeScript client refuses negative, fractional or unsafe-number generations before transport; the CLI can carry the full server integer range. Details and task lists expose the separate pause
projection while retaining their actual lifecycle and waiting reason.

GET /tasks/pause-overview is declared before the dynamic task route. Use
`happyranch tasks --org ORG --pause-overview [--all-pages] [--json]`. It examines
stable org-scoped root-ID pages, including runnable Pending roots and genuinely
satisfied parked carriers, Pausing, Paused and separate terminal-drain diagnostics.
Workflow eligibility comes from its actual current owner/readiness; unavailable
ownership is shown separately. Counts are null unless the complete snapshot is
exhausted and all evidence is available. A later page is another observation,
not a global consistent-zero claim. GET never creates controls, reconciles work
or writes audits. This is a task pause overview, never a safe-to-restart check:
other roots, threads, dreams, wakes and schedules can start independently.

## Admission and drain

Preparation reserves only a closed, bounded invocation journal and prospective
identity. After actual host/provider readiness, the short final transaction
rechecks actual ancestry, hold, original owner, eligibility and authority. It
composes the existing ordinary/v2 business admission or exact workflow claim
and launch reservation, policy binding, session audit and possible-launch
journal with the memory launch commitment. Hold winners are typed deferrals;
no failed ExecutorResult, recovery spend, count increment or business claim is
manufactured. Short authority lease acquire/release transactions surround the
business writer; failed writes roll back before ownership release. There are
no nested BEGINs or early manual commits. Async sequencing precedes synchronous
ownership; no profile/publisher/binding/root/DB lock crosses a host, provider,
filesystem or retry wait.

Launch winners drain with the original session, generation, agent and step.
Every retry arbitrates again, retaining the original enqueue time, ordinal,
remaining schedule/budget and eligibility context. Retained v2 delivery carries the original generation tag and authenticates its complete already-settled admission proof at rediscovery, adoption and final commitment; it never claims that generation again. Release intent and bounded
periodic discovery survive lost notifications and restart. They publish through
the existing workflow/v2/ordinary owner paths, never an untagged substitute.
Uncommitted callback recovery retains the actual original successful provider
return, original/runtime provider SIDs and an unclaimed 120-second opportunity.
Held deferral, preclaim checks, admission waits and restart do not start or
expire that opportunity. The final real admission writer atomically pins its
claim, binding, launch evidence and absolute deadline; the live monotonic
budget and all launch/callback consumers use that same expiry. A claimed
recovery never receives a new deadline on Resume, retry, duplicate delivery or
restart; rollback before commitment leaves it unclaimed, while committed
no-launch/crash cases remain spent under existing settlement rules. Authentic late callbacks use existing INTEGER result consumers even while held, with no new provider launch;
consumed results are never replayed to create a continuation.

A host lease, tracker registration, callback, task status or persisted PID is
not running/quiescence proof. Real launch observation creates a drain blocker;
actual compatible containment terminal/cleanup receipt can discharge only
its own attempt. Before converting an original successful missing-callback
return into recovery ownership, any unresolved original host/tree evidence
is durably retained with its original session, owner, generation and context
in a separate bounded unknown journal entry. It is no longer a producer.
Held recovery deferral, another attempt's quiescent receipt, genuine callback
settlement, terminal lifecycle and restart cannot discharge it. A genuinely
settled known-quiescent original adds no unknown blocker; an unclaimed
recovery alone is not execution, while actual recovery launch has its own
drain evidence.
Passthrough receipts describe absence of containment, not descendant cleanup.
The existing job runner returns its genuine process status and streams, without a containment/tree receipt. Its terminal result settles normally while missing descendant closure remains an unknown job blocker. A failure before runner entry can discharge the proven no-launch reservation. Direct process communicate/wait closes that process only; missing tree evidence
remains an explicit unknown blocker, including after terminal lifecycle or
restart. Interrupted possible launch or cleanup ACTION never becomes safe zero
or an automatic retry merely because a PID is absent. An interrupted result-processing tail also remains unknown until authentic settlement is observed; settlement retires producer ownership without erasing unknown tree evidence.

Pause reads all current non-recovery tracker bindings inside the serialized root/DB writer and keeps the short tracker mutex through COMMIT. Durable positively-running evidence, exact current task/agent/SID and server-admitted ordinary purpose must agree. New or moved bindings are considered there; ended or replaced SIDs are excluded. Advisory pre-request snapshots never decide capture. The tracker mutex is a leaf lock: no binding lease is acquired beneath root/DB, and no await, host/provider operation or backoff runs under these locks. Duplicate Pause preserves capture; Resume followed by a new Pause replaces previous-generation capture.

Only an ordinary-purpose actual running session (including a normal v2-admitted manager session) captured by this pause generation may
submit and auto-run its already-authorized unreviewed jobs while held, with
fresh exact SID/task/agent validation. Prepared, committed-but-not-running,
retry-waiting, recovery and workflow owners have no exception. Otherwise job
submission can remain Pending; every real run entry checks the hold before
review stamps, files, running CAS, launch or success audit. After Resume, an unreviewed Pending job may run after its original session ends only when the original server submission audit and immutable job digest authenticate its provenance and the task/agent remain eligible. Stored session strings alone grant no entitlement. Existing runners
and genuine callbacks/results continue settling. THR309seq1 retires manual /run
with410 (local CLI aliases exit2); legacy true pending rows never run, including
on Resume. New true submissions refuse422 before allocation. Initial preflight
allocates no job; only a genuine authenticated false never-launched prerequisite
loss can commit failed/spawn_failed plus audit/journal settlement before the
existing wake bridge. Hold/owner/session refusals and possible launches remain
unchanged. Reject/stop and Cancel remain
available. Pending rows, deferred work and worktrees are preserved.

Founder Continue/supersede/revisit, shared resolution and thread dispatch(resolves)
check the origin root before IDs, token consumption, notifications or mutations.
Thread Continue remains retired410. A genuinely accepted manager supersession
creates its original separate NULL-parent unheld successor; Pause is not
lineage-wide and Resume invents no successor.

The cleanup hook remains the same real scheduler owner, selector and consumer.
It runs only after final commitment, unlocked, and retains its known prompt
suffix for SAME-owner retry. Interrupted ACTION retains unknown evidence. The
existing terminal worktree reclaimer preserves unresolved/held ownership and
otherwise keeps its non-force conditions.

## Bounded status observation correction (THR-292 seq85/87; TASK-11437)

Stored `in_progress/delegated` is delegation bookkeeping, not outstanding-child
or running-process evidence. Task list, roots and detail serialize optional
`child_wait` in the same coherent database snapshot as their parent rows. The
closed `{state, observed_at}` observation distinguishes unresolved non-Pending
child work (`waiting_children`), only unresolved Pending work (`pending_children`),
nonterminal rows with an exact current completed report (`child_results_received`),
and all-terminal or authoritatively empty children (`continuation_pending`).
Incomplete, malformed, capped or deadline-exceeded reads are `unavailable`.
The reader reuses the existing exact child/agent/current-SID completed-report
predicate and terminal states. It invokes no eligibility, admission, result
consumer or wake. At most256 direct children (query257),1000 inspected children
and250ms additional observation work per response/page are allowed, with no
refill. These limits do not guarantee response performance or constrain execution.
No model/store/schema field is added and no stored status/block_kind/note changes.

Ordinary EN/ZH qualifiers use validated current projections, never delegated
alone or partial recall. Missing/legacy/malformed evidence leaves list/shared
badges and agent cards unqualified; detail adds a subordinate child-status-
unavailable explanation. Header, rail, chain timeline and both actual agent
TaskCard callers share the mapping. A different displayed severity rollup cannot
inherit the parent's qualifier. Existing job waiting copy is preserved.
Pause UI and inherited hold explanations remain detail-only; list and agent
cards receive no pause label, control, disclosure or overview request.

Selected recall must be a complete valid recursive tree for that task. Fanout
counts require nonempty distinct exact selected IDs, positive matching integer
width and exactly one valid direct row per ID. Invalid/missing membership yields
null counts, without a numeric summary, width or bar. Seven literal categories
(pending, in_progress, escalated, completed, failed, cancelled, superseded) drive
the same summary and proportional accessible bar. Terminal includes the four
actual terminal outcomes; in_progress is not running proof and cancelled or
superseded is not success. Spawned headlines follow the independent current
parent child_wait. A genuine prior audit join stays historical and shows current
row counts separately; it never proves parent resumed/completed or consumed
results. Queued chain/join/continuation ownership remains unchanged.

Only AgentDetailDrawer's local `DrawerContent className="max-w-[100vw]"` caps
its original desktop clamp: min(100vw,clamp(480px,45vw,720px)). The shared primitive
and production routes/providers/config remain unchanged. The shipping route
mounts the pane; a separately hashed disposable real-component/provider entry
supplies drawer evidence. Owned synthetic browser observations are frontend
only, with actual request/payload, source/input/dist hashes and viewed images.

Detail unknown blockers with owner_kind=job and actual job_id read “Job
descendant cleanup unconfirmed” / “作业子进程清理尚未确认”. Their summary counts
job cleanup unconfirmed separately from known running sessions/jobs and other
unresolved items. IDs/raw reasons remain literal. A terminal job, exit0 or PID
absence never closes descendant uncertainty; the future job-owned closure
producer is excluded. TASK-11345 and its16 historical unknowns remain held.
Backend/race/restart/Pause/R/E04 and protected red/mutation/repetition/test-authoring
proof remain UNEXECUTED/held. Python units RETIRED; integration and actual CLI
callback SKIPPED THR243seq42. Automated gates stay lint/static only; manual
synthetic frontend builds/observations do not constitute Product E2E or behavioral
Pause acceptance. Native independent final-head review/QA and manager merge/
exact-main lint remain required; source delivery authorizes no live activation.

## Persistence, deployment and evidence

Only OrgState.load and proven fresh org creation install/validate the approved
single task_pause_controls table. Generic Database/runtime-audit initialization
stays unchanged. The table stores schema1, actual root FK, hold, monotonic
generation/timestamps/actor, release intent and a closed bounded journal, not
prompts/output/results. Independent complete whole-database workflow/release
references include the pause dimension; F/E/G/H bytes and history are retained,
and v2 schema remains observed-only. Rollback requires a pause-compatible
reader, even with empty controls. No live migration, restart or deployment is
performed by delivering source.

Backend transport is PR1. PR2 consumes it through provider-aware Web hooks:
root-only Task detail controls. Founder THR-292 seq41 (TASK-10917) removed
the standalone browser Task pause overview panel from the ordinary Tasks page, and
seq45 (TASK-10920) places ALL rendered pause UI on Task detail only: list rows show
no pause control, state label, drain evidence, blocker, unavailable message or
inherited-root explanation, only ordinary lifecycle, navigation and lineage. The
JSON/API list projection still carries `pause`, and the read-only overview remains
available through GET /tasks/pause-overview, its TS client and
`happyranch tasks --pause-overview`, independently of any rendered list or panel.
Pending roots have no Pause; descendant detail shows inherited hold and the actual
root link. Founder THR-292 seq77 (TASK-11155) sets the header presentation from the
shared "Task Header - Pause States" artifact: compact Pause in the ordinary
Revisit/Cancel/audit row; Pausing = amber "Waiting for current work to finish"
indicator, secondary Resume and a default-closed disclosure; Paused = separate
Paused indicator and primary Resume; mobile order indicator/Resume, disclosure,
actions in normal document flow. Supported-data adaptations: blocker rows render only
kind, actual task/job links, raw owner_kind and reason (no title, agent or start
time exists); owner_kind is not an assignee and observed_at is not a start time, so
the single projection observed_at appears inside the disclosure. Only
task_session/job count as "still running"; launch/result_processing/unknown are
"other items to resolve" (unknown job items separately say cleanup unconfirmed), and incomplete evidence is stated. The artifact's
"evidence moves to View audit" note is not adopted: pause/resume audit rows carry
generation/held and event time, not live drain observations, so necessary
unknown/incomplete/terminal diagnostics stay in the detail disclosure; the existing
`/orgs/:slug/audit?task_id=` link and label are unchanged. Primary Resume uses the
existing primary button token and fonts. Disclosure metadata and
notes use the existing secondary text token, not the artifact's lighter grey, so the
12px text stays at least 4.5:1 in both themes. Separate control/evidence presentation preserves real lifecycle, waiting,
attention and lineage; terminal drain diagnostics remain visible. Missing or
unsafe generation/projection is unavailable. Pending controls disable repeated
submissions across mounted views. Actions submit the displayed generation and
refresh all task list families/detail for the original org, without
provider-level replay or an opposite action on conflict/refusal. Existing shared
HTTP authentication recovery is unchanged (a401 may bootstrap/retry once).
CLI/API overview pages are independent timestamped observations, never summed
counts or a complete-zero assertion; null counts/evidence limits stay explicit.
Both locales and responsive layouts preserve original filters, dialogs, focus,
drafts and actions. Prototype and browser-interception evidence is UI/transport
only. The daily source increment in e2e/pause.py and e2e/pause_browser.py authors
the finite P01a/P05 chain drain, P11/P14/P17 identity/idempotence/stale control,
P14b/c same-org unheld progress, P12 drained same-files disposable product-daemon
restart and explicit Resume, separate P01b Resume-during-drain, and P16/P18 live
English/Chinese list/detail/child/reload readback (the browser case now asserts
no overview panel or overview read and no list-row pause UI, reading root state
from detail; the CLI overview-readonly case is unchanged). Its 19 named Pause
variants follow the unchanged 9-launch/23-variant baseline; exact host anchors
are 12 before restart, 0 on restart readiness and 6 at final closure, with 18
combined launches. These are full source expectations. Scheduled run38181136948
at2026-10-11T22:37UTC, source bfeb109fe0e7801f17508abe96a39d376f2886fb,
FAILED on Python3.12/3.13/3.14 with14 partial Pause PASS observations per cell.
`Pause.restart-final` aborted on a harness completion-audit `session_id` lookup;
terminal-denied, resume-during-drain, drain-final and exact-closure were unreached.
TASK10870's authenticated19-case ledger preserves each observation and gap.
This establishes a consumer field-contract defect, not an attributable product
regression RED, complete closure proof or full runtime-fit evidence.
The TASK10874 bounded correction joins genuine task/agent/SID result rows and
INTEGER identities to snapshot contents and `session_start`, then checks actual
completion-audit semantic fields and strict owner/count/order/chain assertions.
CompletionReport has no SID. A sanitized pre-assertion snapshot is explicitly
partial/unvalidated; successful closure records and PASS markers remain gated
by every assertion. The old failed final SQL snapshot remains missing.
Corrected restart/drain behavior and wrong/missing/duplicate identity or wrong
audit owner/payload/order rejection remain UNEXECUTED; source/static acceptance
is distinct from behavior. Existing accepted case groups4/5/6 are reused.
Only the existing 19:00 UTC default-branch 3.12/3.14 schedule may execute
it, within unchanged 600/720/900-second limits. Protected assertion mutation,
RED/restoration/GREEN and repetitions remain UNEXECUTED/unauthorized. All eight
restart windows, authentic held-queued workflow history and P07/G4 interleavings
remain uncovered; launcher interruption cleanup is separate. Source/static
inspection is not behavioral acceptance. Python unit source and gates are RETIRED under THR291seq40; prior suspended
unit/full/focused/RED-GREEN/keeper/mutation/repetition/duration receipts remain
SKIPPED NONBLOCKING under THR291/THR228seq355, never PASS. The first scheduled
Product E2E is FAILED with the partial observations above; general integration
is SKIPPED THR243seq42/THR211seq270–271.
Current automatic PR and push-main checks are only Source Boundary Lint and
Web Lint (Node 24), both unconditional under THR291seq69; local_ci.sh all is
source-boundary/colour/ESLint/TypeScript static analysis only. Retained native,
platform and actual callback source/fences remain; they are not automatic merge
requirements. No ad hoc E2E, execution-mode or cap change is authorized.
Final immutable-head independent review/scoped QA, normal
publication and manager-guarded merges remain separate gates.

### Bounded ordinary native launch refusals (THR-315)

Actual non-hold refusals before durable preparation allow at most THREE consecutive
attempts for the same logical task continuation. Existing audit rows
`task_native_launch_boundary` retain the original fingerprint/owner/agent/generation
key, actual reserved SID, invocation and attempt identities, cumulative count and
first safe cause; the latest three bounded payloads suffice without new schema or
journal fields. Reconstruction/redelivery does not reset this count. A genuine
positive-PID native launch callback records a zero-count boundary before fallible
phase persistence. A reserved SID and memory telemetry never prove launch.

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

The first preparation cause is kept separately from the final Pause/cleanup error:
bounded stage, exception class/stable Pause code and source locations only. An
executed pull adds a safe repo identifier, fixed operation and return code or
exception class. Skipped pre-pull admission remains a stale-source warning and
creates no pull-failure cause. No exception messages, output, argv, URLs, environment
or locals enter this evidence. An audit/storage save failure remains an explicit
persistence limitation, not a durable count or terminal-success claim. No new timer,
service, automatic successor or permission follows. Source/scoped verification is
separate from deployment, running-code identity and operational acceptance.
