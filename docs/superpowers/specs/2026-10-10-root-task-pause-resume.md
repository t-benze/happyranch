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

Only an ordinary-purpose actual running session (including a normal v2-admitted manager session) captured by this pause generation may
submit and auto-run its already-authorized unreviewed jobs while held, with
fresh exact SID/task/agent validation. Prepared, committed-but-not-running,
retry-waiting, recovery and workflow owners have no exception. Otherwise job
submission can remain Pending; every real run entry checks the hold before
review stamps, files, running CAS, launch or success audit. After Resume, an unreviewed Pending job may run after its original session ends only when the original server submission audit and immutable job digest authenticate its provenance and the task/agent remain eligible. Stored session strings alone grant no entitlement. Existing runners
and genuine callbacks/results continue settling. Reject/stop and Cancel remain
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

Backend transport is PR1; browser controls are a separate PR2. Source/static
inspection is not behavioral acceptance. Python unit source and gates are RETIRED under THR291seq40; prior suspended
unit/full/focused/RED-GREEN/keeper/mutation/repetition/duration receipts remain
SKIPPED NONBLOCKING under THR291/THR228seq355, never PASS. Fresh product E2E
remains PENDING; general integration is SKIPPED THR243seq42/THR211seq270–271.
No replacement harness is assumed. Current Node24 Web, Python3.14 canonical and
selected ordinary stub-Codex callback, macOS15 canonical, Docs Manual and actual
path gates remain required. Final immutable-head independent review/QA, normal
publication and manager-guarded merges remain separate gates.
