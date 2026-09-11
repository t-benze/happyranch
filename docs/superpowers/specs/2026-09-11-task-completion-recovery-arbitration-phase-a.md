# Task-completion recovery arbitration

> Status: implemented in this PR candidate; not merged or deployed.
> Current source: `runtime/orchestrator/run_step.py`, `runtime/orchestrator/orchestrator.py`,
> `runtime/infrastructure/database.py`, and `docs/agent-guides/orchestrator-contracts.md`.

THR-247 grants one and only one recovery opportunity after a clean Codex
provider exit omits the exact authenticated callback. Its durable claim starts
the shared 120-second budget across claim, admission, preparation, launch,
communication, and callback admission. It does not bound descendant work or
settlement/cleanup overhead. Timeout, provider error, cancellation, missing
provider continuity, terminal tasks, a second omission, and failed recovery
remain fail-closed; this is not a general retry, watchdog, or replay.

## Restart settlement

On startup, an unaccepted `claimed` recovery is handled before the ordinary
THR-079 PID-liveness branch.  The ledger transaction may terminalize only the
still-current origin or recovery binding; it atomically records
`restart_settled`, fails the task, and durably marks only its still-running
owned job rows `task_ended` before invoking the existing live owned-job
cleanup.  If the process stops after that commit, a reopened startup sweep
still sees the terminal child through its parked parent and performs the
ordinary bounded parent wake; the later lifespan orphan-job reconciliation
only applies its normal contract to unrelated durable running rows.  The
settlement transaction rolls back task, ledger, and owned-job rows together on
any pre-commit failure.  A callback acceptance, cancellation, or newer durable
binding that wins after the sweep reads a claim makes guarded settlement return
false without overwriting that winner; a callback after settlement is rejected.
This bookkeeping never signals a persisted PID, launches another recovery, or
consumes a prior-session result.  An unconsumed accepted callback is selected
only by its ledger-recorded immutable result id; once consumption is recorded,
ordinary startup handling resumes and must not replay the callback.

## Transaction contract

The orchestrator publishes `tasks.assigned_agent` and
`tasks.current_session_id` before it makes that generation visible through the
`SessionTracker`. A callback for a durably published binding must match both
values at final admission as well as the tracker lease. `assigned_agent` alone
is task-routing metadata written at submission, not invocation publication;
the established tracker-only ordinary path applies until `current_session_id`
is published. A
recovery claim begins `BEGIN IMMEDIATE` and requires that durable
assigned/current origin binding, an in-progress uncancelled task, no exact
origin result, and no live episode. The completion route performs its normal validation before
awaiting `org.db_lock` for stable response ordering. Once it acquires that lock
it holds the existing per-binding tracker lease (without awaiting), rechecks the
active generation, and enters `Database.admit_task_completion_callback()`.
That transaction computes the admission time at the final boundary and rechecks
terminal/cancellation state, recovery ledger, exact result idempotency, result
insertion, and recovery acceptance update. A rollback leaves no task result or
accepted ledger state.

The ledger transitions are `claimed` (the sole opportunity is durably spent),
`callback_accepted` (the immutable exact result row was inserted atomically),
`callback_consumed` (its exact effect committed), `expired`,
`restart_settled`, or `superseded`. Acceptance, expiry, cancellation, and
replacement have one durable winner; historical origin and recovery bindings
remain fenced. A fresh ordinary generation is admissible after all episodes are no longer
claimed, so a valid job-resume callback is not made impossible by the fence.

The tracker lease bridges its `get_active()` check through the synchronized DB
call, so a concurrent replacement cannot slip through the old post-read gap.
It is deliberately not held across an `await`; SQLite remains the durable
claim/result serializer.

## Phase B purpose gate

Recovery registration is a server-only atomic `SessionTracker` operation: the
same binding lease installs the active generation and its recovery purpose.
Ordinary `set_active` always clears that purpose, and stale generation cleanup
cannot clear or taint a replacement. A recovery generation is denied before
side effects at task-bound job submit/stop, schedule creation, thread compose
(JSON and multipart) and post/send, manager changes, progress/learning writes,
and both custom-skill agent creation mounts. Completion may report completed
or blocked work, but its `delegate`, `fanout`, `parallel`, and `supersede`
decisions are rejected before result insertion or work creation.

The jobs dual-auth seam admits session-authorized recovery **reads** only for
the exact task-owned job (`get`, `tail`, `wait`); stop remains denied. Existing
bearer behavior is unchanged. Learning get/search are provenance reads, not a
mutation grant. This is intentionally a partial gate: accepted bearer-only
and sessionless task/thread/task-job-schedule controls, plus shell/workspace
and KB/artifact powers, remain outside it.

## Phase C live launch checkpoint

The shipping outcome boundary now attempts recovery only after a clean Codex
result has no exact callback and exposes a provider conversation id. It first
atomically spends the ledger claim against the still-owned, uncancelled origin
binding; an already committed origin callback wins. Recovery receives a fresh
HappyRanch runtime session id while `ExecutorResult.agent_session_id` is used
only as the provider resume id. The durable current binding is written before
the tracker registers recovery purpose. The callback instruction references the
current binding and already-performed work; it never replays the task brief.

The claim boundary establishes one shared 120-second live monotonic budget
before its durable transaction begins, while persisting a wall-clock expiry for
restart-safe fencing. The active server-owned recovery tracker binding carries
the live deadline to final serialized callback admission; queue/DB-lock delay
and wall-clock rollback therefore cannot extend it, and no monotonic value is
persisted. The remaining interval is passed to launch without extension. For
contained task recovery, the server reruns only the recovery
ownership/deadline check after `prepare` and immediately before the
supervisor's atomic launch commitment; expiry, cancellation, or replacement
there abandons the prepared handle without `backend.launch`. Ordinary callers
retain their single pre-prepare validator. Post-launch communicate enforcement
remains separate: this does not claim to bound descendant execution or cleanup
overhead. A claimed-but-refused launch remains spent;
at the outcome boundary, a durably accepted exact origin callback that raced
the claim is re-decoded and wins without recovery, while a durably accepted
exact recovery callback wins over a later provider error/timeout unless the
task was cancelled. Recovery publication is a SQLite compare-and-swap on the
claimed origin, live in-progress task, and uncancelled current binding, so a
replacement or cancellation during preparation cannot overwrite its newer
generation; PID publication is likewise current-generation guarded. Failure/
second omission without an accepted exact callback falls through to fail-closed
owned-job cleanup. Both scratch fallback
and contained launch paths pass the provider resume id through. A leaf
completion atomically records its terminal row, delegated-verdict audit and
exact consumed result. A manager DONE has deliberately separate boundaries:
the final owner CAS commits its terminal row and ordinary receipt, while its
marker follows only after post-commit cleanup/delivery reconciliation. Startup
therefore reconciles terminal-before-marker only for the exact owner/result and
never selects an unrelated latest result. Manager recovery reuses the accepted result's already
durable orchestration-step audit when re-entering after a crash before its
ordinary decision effects. Its completion-report receipt is keyed by its
recovery session and immutable accepted result id, not an older task/agent
audit; its final done write and consumed marker compare-and-swap the assigned
agent, runtime session, uncancelled in-progress row, and exact
callback-accepted result, leaving a replacement, cancellation, or changed
ledger receipt untouched. A rejected final effect performs no cleanup or
parent/thread delivery. It retains the existing authority-hook and delivery paths, then
spends the receipt only after those effects reconcile. This adds no
new notification guarantee or general manager retry. Root escalation now uses
the recovery-aware form of the existing shipping escalation CAS: task, agent,
current recovery session, immutable accepted result, and `callback_accepted`
are all checked while its completion/escalation receipts and consumed marker
commit together. A replacement at that final boundary leaves the winner and
accepted receipt untouched; notification remains post-commit and is not an
exactly-once external claim. A defensive non-root manager escalation is routed
to its parent without founder escalation or authority evaluation. Its accepted
recovery result atomically stores the exact completion receipt, FAILED effect,
and consumed ledger marker; owned-job cleanup and the bounded parent wake stay
post-commit and reconstructible. Its ownership proofs install cancellation
through the shipping cancel route, or a replacement/reassignment, immediately
before the real final transaction; replacement winners remain in-progress.
Selector proofs begin from a real consumed receipt, then change only
cancellation metadata, session, or agent and exclude stale recovery cleanup
and parent delivery while allowing separately identified ordinary startup work.
The D3a owned-job resume family now covers clean Codex omission followed by an
agent-authored recovered blocked callback across completed/failed job outcomes
and terminal-before/terminal-after-callback timing. It drives the real blocked
transaction, terminal-job predicate/delivery, run-step CAS and ordinary
launch; duplicate job-finished delivery may enqueue more than once, while only
the CAS winner launches the one ordinary provider invocation. The recovery
keeps the original provider conversation id, but the ordinary job-resume launch
preserves existing behavior and receives no `resume_session_id`; it has a fresh
ordinary runtime binding, no recovery purpose/deadline, and its normal timeout
policy. It additionally proves the nonempty ordinary retry policy is restored,
the delivered `BLOCKED-JOBS-RESULTS` context exposes the actual terminal job
outcome, and the immutable accepted blocked-result row is unchanged after the
ordinary turn. An ordinary resumed timeout creates no result, never consumes
that blocked row again, and gets no recovery attempt.

The D3b fail-closed family covers the second clean omission, provider error,
provider timeout, and recovery launch exception after an eligible origin. Each
case has exactly one durable spent episode, no accepted result, no third
provider launch after re-entry, terminal owned-job `task_ended` cleanup, and
late origin/recovery callback rejection. An older same-task/agent result under
a distinct session remains byte-for-byte unchanged and is never selected as an
invocation result. Ineligible origins (timeout, provider error, no provider
resume id, or cancellation before clean return) make no recovery claim or
launch; cancellation remains the terminal winner. The remaining final
inventory and publication gates remain independent of this implementation;
this PR candidate is not merged or deployed.

## Acceptance evidence map

The executable regression suite is the acceptance source of truth. The
`test_run_step_codex_*` families prove clean-omission claim and callback
arbitration, duplicate/lost-response/late/stale rejection, cancellation and
newer-binding winners at the final transaction, accepted-result races, the
shared 120-second budget, and no recovery re-admission after a recovery 429.
Only named route/SQLite/tracker race tests with finite barriers establish
concurrent admission; ordered setup cases establish only their stated order.

`tests/daemon/test_startup_recovery.py::test_manager_done_postcommit_cleanup_pending_restarts_once`
proves terminal-before-marker restart reconciliation, and
`tests/daemon/test_startup_recovery.py::test_accepted_manager_done_recovery_final_owner_and_receipt_fences`
proves rejected final-CAS owners cause no stale cleanup or parent delivery.
`test_database.py` and accepted manager/leaf families cover durable row
settlement, leaf atomicity, root/non-root escalation, and authority continuation.
D3a covers zero/nonzero terminal jobs before
and after blocked callbacks, ordinary resume, and absence of stale timeout
reuse. D3b covers the second omission, provider failure/timeout/launch failure
cleanup, exact old-session result preservation, and timeout/error/missing-
provider/cancelled ineligible origins with owned-only cleanup. Route tests
cover the recovery-purpose admission surface; contained-launch and supervisor
tests cover preparation/launch fences and owned process cleanup. A recovery
turn uses its supplied fresh runtime binding only to report already-performed
work or observe an actual owned-job wait; it creates no ordinary work or new
job. Ordinary resumes retain ordinary policy. These tests
do not assert deployment, provider replay, or a broader retry authority.
