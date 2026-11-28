# Task lifecycle: bounded TLA+ pilot

THR-386 bounded pilot (TASK-18026 / TASK-18023), updated for the founder-approved
revisit dedupe repair (TASK-18273 / TASK-18272). Source mapping was rechecked at
`774c91572` on 2026-11-21. This models the repair in this branch; it is not a proof
of Python execution or deployment. No runtime dependency, CI job, scheduled run
or merge gate is added.

New risky lifecycle changes must update a model per the [coding practice rules](../../../agent-guides/contracts/coding-practice-rules.md).

<a id="sequential-closed-outcome-settlement-u1"></a>

## Sequential closed-outcome settlement (U1/U2)

`SequentialSettlement.tla` models the finite original-owner P0/P1 intent → native
removal → closed receipt sequence and the existing original terminal/outer path.
P2 with missing scratch/outer proof and U0–U5 retain sticky unknown. An interrupted
intent cannot produce a positive receipt; no observer reconstructs native closure.
The model freezes original results, launches and spent allowances, makes Held and
its addressed inbox one atomic action, and models replay as stuttering.
P1 execution failure now precedes a separate original outer-cleanup action:
`OuterClean` admits the existing intent sequence, while `OuterUnknown` stays
fenced. Execution failure supplies neither positive nor uncertain cleanup proof.

| Action | Production owner |
| --- | --- |
| OuterClean / OuterUnknown | `SequentialDispatcher.dispatch` original P1 exception handoff, actual `run_step_impl` outer batch and unchanged `settle_failure` cleanup qualification |
| Intent / RemoveNative / Receipt | `sequential_launch_failures.settle_failure`, original `TaskInvocation.close` and `TeamMoveCoordinator.release_task(failed=False)` |
| Interrupted | Original failure before final closed receipt; retained intent/unsettled fence and native unknown |
| Settle | `generic_closed_visit` / `generic_hold_cause`, dispatcher acceptance and deferred-FK `hold_and_wake_uncommitted` |
| AuthorityReady | Existing admission writer becoming available; no access bypass |
| Replay | Authenticated selected history, saved legacy predicates and original fallback closure; no allocation or history rewrite |

Use the pinned [TLC recipe](#run-tlc) with module `SequentialSettlement` and
`sequential-settlement.cfg`: all invariants and conditional `Completion` must pass.
`sequential-settlement-before.cfg` must expose stranded clean negative/no-callback
outcomes (temporal counterexample, exit 13). `sequential-settlement-unsafe.cfg`
permits false settlement of unknown and must fail `NoFalseHold` (exit 12).

Liveness assumes fair original finishing/settlement, eventual valid authority and
successful durable writes; uncertain states have no promised business completion.
The abstraction does not prove Python refinement, byte/digest authentication,
native containment, queue delivery, semantic reports, external effects or live
recovery. U2 extends `Restart`/`Sweep` over the closed-but-unsettled window,
with lost queue hints, delayed readiness, replay and independent `Observe`.
Restart before closure retains uncertainty; it never manufactures closure.
`sequential-settlement-restart.cfg` checks conditional Completion, Delivery and
UnknownObserved as well as the retained safety invariants. Weak fairness assumes
eventual authority, scans, storage and delivery readiness; alarm state is a log
observation only. Pages, digest/SQL budgets, clock thresholds, Python races and
host containment remain outside this finite abstraction. TLC results are review
evidence,
never a CI gate; actual commands and limits belong in the PR/task output.

## Sequential first-launch refusals

`RefusedSequentialLaunch.tla` / `sequential-refused.cfg` model the future-only
instruction-refusal path for Coding v3 and Draft→Review v1 (TASK-19020, PR #1865).
The initial journal ranges over zero, one or two historical unknown entries,
including both unsettled and producer-settled unknowns. A refused render creates
no `TaskInvocation` and no journal entry. A successful render may prepare its
own single entry and launch; this keeps the no-growth property specific to
refusals rather than prohibiting normal preparation.

| Model action | Source correspondence |
|---|---|
| `Refuse` | `SequentialDispatcher.queue_started`: `_render_node_instruction` refuses before `TaskInvocation` construction/`prepare`; `_record_preparation_free_refusal`, `sequential_launch_refusals.record_refusal` and existing `hold_and_wake_uncommitted` record count three, Held/event and the recorded initiator inbox in one writer |
| `Render`, `Prepare`, `Launch` | Successful instruction freeze, `TaskInvocation.prepare`, then existing launch COMMIT; detailed authority/cancellation revalidation remains in Python and the separate [sequential launch model](../sequential-launch/README.md) |
| `Reaper` | `discover_pause_work` / zombie-reaper queue hints, filtered by `queue_admission_allowed`; Held is ineligible |
| `Restart` | Stuttering over retained receipts, Held, inbox and journal; no counter reset or historical cleanup |

`JournalGrowthBound` requires zero added entries while queued/refused/Held and
at most one own entry after successful rendering/preparation. `RefusalCap`
requires at most three consecutive refusals, with the third reaching Held.
`HeldInboxAndNoPublication` requires exactly one Held event and one inbox item
addressed to the recorded initiator, and freezes reaper publications at Hold.
`HistoricalUnknownPreserved` retains each original unknown disposition. No
action clears or settles those entries; these receipts are not recovery proof.
`Settles` additionally checks eventual Held or launch under fair queue/host
delivery and successful durable writes. Restart does not discard queue hints
in this abstraction. Crash windows, failed writes, byte-level journals, native
closure, provider execution, human recovery and later visits are not proved.
There is no unbounded-history or Python refinement proof.

Run the pinned [TLC recipe below](#run-tlc) with module
`RefusedSequentialLaunch`, config `sequential-refused.cfg` and a distinct
task-scratch metadir. Actual TLC 2.19 / Java 21.0.13.1 result on 2026-11-24:
**exit 0**, no errors, 240 generated / 120 distinct states, depth 8, queue empty;
all five invariants and `Settles` checked. Exact argv, stream hashes and logs:
`output/TASK-19020/commands.jsonl` and
`output/TASK-19020/tlc-sequential-refused.stdout.txt`. This is review evidence,
not a CI gate or authority to recover historical entries, restart or launch a pilot.

## Refused repository preparation

THR-395: `RefusedPreparation.tla` separately abstracts the original preparation
tail for two repositories. `Close` maps to worker/outer/durable closure in
`_repo_refresh_complete`; `Release` models a lease exception; `Refuse` and
`Settle` map to native unheld refusal/held deferral and `refused_task_idle` under the Pause
writer. Launch/possibly-running, unreadable, cleanup-live and running-job flags
abstract independent missing proof. `refused-current.cfg` checks no false paused,
hold preservation, hold-winner accounting, Resume accounting and safe-case progress.
Pause may precede the original closure; only original finishing receipts drain
under hold. A held lease-error winner keeps its child pending with zero refused
rows/attempts; positive closure/cleanup removes its journal, while missing proof
stays unknown. One Resume re-prepares under unheld refusal accounting. The model
uses successful journal removal for the producer-settled disposition, without
inventing a terminal held refusal. `refused-before.cfg` must find the
stranded-tail liveness counterexample (exit13); `refused-unsafe.cfg` must find a
false-paused safety counterexample (exit12). Use the documented TLC command with
these configs and module `RefusedPreparation`. This finite model abstracts
successful durable writes; it does not prove Python execution, COMMIT behavior,
historical repair, native containment or deployment.

Prior TASK-18947 TLC v2.19 result (before the held-timing extension): current exit0, 296 generated /248 distinct states;
before exit13, 320 /312 and a retained-open last repository; unsafe exit12,
241 /201 and false paused with unreadable proof. All completed under one second
using the pinned jar below; logs are task output for TASK-18947.

TASK-18970 held-timing extension: TLC current exit0 (660 generated /506 distinct
states); before exit13 (expected liveness counterexample); unsafe exit12
(expected NoFalsePaused violation). Logs are `output/TASK-18970/tlc-*.log`.

**Result:** both historical incidents and the preserved pre-fix repeated-revisit
counterexamples reproduce. With transactional live-descendant admission,
`current.cfg` passes `OneDriver` and `current-executors.cfg` passes
`OneLiveRootProcess`. Human-control liveness exceptions remain explicit. See
[RESULTS.md](RESULTS.md) for actual outputs and limits.

## State and source correspondence

Paths in this document are relative to the repository root. `TaskStatus` and
`BlockKind` are defined in `runtime/models.py`.

| Model value | Stored task shape / interpretation |
|---|---|
| `absent` | Unallocated bounded task slot; not a stored status |
| `pending` | `TaskStatus.PENDING` |
| `in_progress`, block `none` | `IN_PROGRESS`, `block_kind IS NULL`; running claim, not proof that its process is alive |
| `in_progress`, block `delegated` | `IN_PROGRESS`, `BlockKind.DELEGATED`; waiting on children |
| `in_progress`, block `blocked_on_job` | `IN_PROGRESS`, `BlockKind.BLOCKED_ON_JOB`; waiting on jobs |
| `escalated` | Nonterminal `ESCALATED`; human decision required |
| `completed`, `failed`, `cancelled`, `superseded` | The four terminal `TaskStatus` values |

The three names `root`, `child`, `next` are slots, not fixed roles: in the
normal scenario either unused slot can become a child or a successor root.
`parent` preserves structural ancestry; `revisitOf` separately records founder
revisit edges. All roots in one run represent the same
logical work lineage, including successor roots whose stored parent is NULL.
`alive` records producer existence independently of status; `result` records an
available, session-matched callback. `queue` is a set of admitted wake hints.
`woke` is a history bit recording a terminal child's parent notification.

`journal` models the **effective producer deferral**, not a byte-for-byte journal.
The actual phase vocabulary in `runtime/infrastructure/task_pause_controls.py:PHASES`
is `prepared`, `possible_launch`, `action_started`, `running`,
`result_processing`, `retry_deferred`, `recovery_deferred`, `pending_job`,
`unknown`. All appear in `Phases`; `none` is the model's additional abstraction.
Only `running`, `result_processing`, `unknown`, `none` are reached in these
configs. `TaskInvocation.prepare`, `phase`, `commit`, `release_live` and
`settle_live` in `runtime/orchestrator/task_pause.py` implement journal ownership.
`Boot` folds interrupted preparation/launch/result phases to `unknown`, as
`restore_pause_preparations` does. Successful producer settlement is represented
by `none`; **this does not mean deleting retained unknown journal bytes**.
`OrdinaryUnknownOwner.settle` actually retains them with
`execution_unknown=true`, `producer_settled=true`.

## Action map

These are abstract transitions, often combining a committed write and its
eventually delivered tail. They are not claims that separate Python effects
are crash-atomic.

| Action(s) | Current implementing code: `file:function` |
|---|---|
| `Start` | `runtime/orchestrator/run_step.py:_run_step_body`; `runtime/infrastructure/db/tasks.py:try_claim_for_step`, `_try_claim_for_step_uncommitted`; `runtime/orchestrator/orchestrator.py:_run_agent_impl` |
| `Delegate`, `Retry` | `runtime/orchestrator/run_step.py:_consume_completion_report_body`, `_check_retry_link_required`; `runtime/infrastructure/db/tasks.py:verify_retry_link` |
| `Feedback` | `runtime/orchestrator/run_step.py:_feedback_and_reenqueue`, `_attachment_feedback_and_reenqueue`; bounded feedback returns a nonterminal row to pending |
| `RejectPreparation` | `runtime/orchestrator/run_step.py:_fail`; prelaunch refusal closes a pending task without an agent session |
| `WaitJob` | `runtime/orchestrator/run_step.py:_consume_completion_report_body`; `runtime/infrastructure/db/tasks.py:try_park_on_jobs` |
| `Wake` | `runtime/orchestrator/run_step.py:_enqueue_parent_if_waiting`, `_maybe_resume_blocked_task`; `runtime/daemon/__main__.py:_sweep_on_startup` Branches 2/3; `runtime/daemon/queue.py:TaskQueue.enqueue_if_absent` |
| `PublishResult` | `runtime/daemon/routes/tasks.py:submit_completion`; persists the current-session result before consumer effects |
| `Finish` | `runtime/orchestrator/run_step.py:_consume_completion_report`, `_consume_completion_report_body`, `_complete`, `_fail`; includes recovery consumption of a landed result |
| `Crash`, `Boot` | Environmental host/daemon loss; `runtime/daemon/__main__.py:_sweep_on_startup`; `runtime/orchestrator/task_pause.py:restore_pause_preparations` |
| `Recover` | Startup Branch 1; `runtime/daemon/zombie_reaper.py:_sweep_org_zombies`; `runtime/orchestrator/task_pause.py:ordinary_unknown_owner`, `_ordinary_unknown_entries`, `OrdinaryUnknownOwner.process_dead`, `writer`, `settle`, `pause_deferred_owner` |
| `LegacyRevisit` | Historical `_maybe_spawn_auto_revisit` in `runtime/orchestrator/run_step.py`, called after startup child failure; absent from current source (removed in PR #515) |
| `Revisit` | `runtime/daemon/routes/tasks.py:revisit_from_notification`, `_classify_predecessor_status`, `_supersede_predecessor_locked`, `_collect_eligible_revisit_family`; `runtime/infrastructure/db/tasks.py:insert_revisit_task` checks transitive live successors and inserts in one SQLite write transaction |
| `Rerun` | `runtime/daemon/routes/tasks.py:resolve_escalation_in_process` (`continue`); uses the same nonterminal ID |
| `ManagerSupersede` | `runtime/orchestrator/run_step.py:_consume_completion_report_body`; `runtime/infrastructure/db/tasks.py:try_manager_supersede`, `_has_live_manager_supersession_family_work_uncommitted` |
| `Cancel` | `runtime/daemon/routes/tasks.py:cancel_task`; leaf-first abstraction of the terminal cancellation path |
| `Hold` | `runtime/orchestrator/task_pause.py:assert_origin_unheld`, `origin_guard`; `runtime/infrastructure/task_pause_controls.py:TaskPauseStore.control` |

The enum comment saying escalation resolution `supersede` means `cancelled`
is stale: current `resolve_escalation_in_process` creates a successor and closes
the predecessor as `superseded`. The model follows the implementation.

## Properties

* `TypeOK`: all variables stay in their finite domains.
* `OneDriver`: at most one root in the work lineage is pending, running, or
  queued to resume. A delegated parent and its executing child are one owner
  hierarchy. A queued delegated original plus a revisit root are two drivers.
* `OneLiveRootProcess`: the weaker process-only form, checked separately to show
  the pre-fix counterexample can advance from two pending successors to two
  executing root producers.
* `TerminalAbsorbing`: once any task reaches a terminal status, its status never
  changes, including terminal-to-terminal rewrites. Retries/revisits allocate a
  new ID; only escalation `continue` and feedback rerun a nonterminal ID.
* `DeadProducerProgress`: a running claim with a dead producer eventually becomes
  terminal or wakes its parent. This states the requested broad liveness claim;
  current-design checks expose its human-hold/escalation exceptions.
* `RecoverableDeadProgress`: the same progress claim for an abandoned producer
  **without a landed callback**, in the unheld, exact ordinary-owner domain.
  This separates death recovery from consuming an intentional escalation result.

`Spec` places weak fairness on each task's recovery, parent/job wake and successful
result-consumption action, and on daemon boot. Fairness is not merely attached
to the disjunction `Next`. Crashes are bounded, so an enabled recovery cannot
lose to infinitely many restarts. No fairness is imposed on founder cancellation,
revisit, escalation resolution or releasing a hold. A human decision is not an
autonomous daemon guarantee. Terminal stuttering is permitted; TLC's deadlock
check is disabled deliberately, while temporal properties remain checked.

## Explicit assumptions and limits

1. There are three task slots and at most one restart per run. Slot exhaustion
   disables allocations; it does not stand for budget replenishment. `Retry`
   permits one linked retry per failed predecessor. Real retry schedules,
   revision counters and authority budgets are not proved here.
2. Per-task claim, session identity and current-owner revalidation are atomic
   abstractions of existing CAS/locks. One `alive` bit represents one producer
   per task. This model investigates cross-ID double driving; it assumes,
   rather than independently verifies, the implementation's same-ID CAS.
3. `Crash` can kill any subset of producers, including all of them (host crash),
   or retain live producers (daemon-only loss). A false `alive` bit means
   **positive death proof**, not an unprobed numeric PID. Recycled PID,
   permission-denied, missing identity, unavailable boot evidence and descendants
   are outside this proof. Startup/reaper timers and stale heartbeat/TTL waits
   collapse into fair recovery. No process signals are modeled or sent.
4. Ordinary unknown recovery assumes unheld legacy ownership, exact current
   task/agent/SID/fingerprint, authenticated ordinary invocation purpose, no
   mixed authority/recovery/workflow owner, and a usable death observation.
   These are real `_ordinary_unknown_entries` gates. Prepared-only uncertainty,
   retry/recovery-deferred journals, v2 continuation generations and workflow
   recovery are named in the source map but are **not verified** by these runs.
5. `result` abstracts one already-admitted current-session report; it does not
   prove callback deduplication, idempotent decision replay, transactional result
   ownership or the completion-recovery ledger. Consuming a report may escalate.
   Normal return and its decision are otherwise collapsed into `Finish`.
6. All job waits eventually observe job termination, represented by `Wake`.
   Jobs, thread delivery and workflows are not separate state machines. Passive
   carriers completing without a producer are outside the executable-task
   model; the trace audit explicitly reports that gap.
7. Queue entries are hints, collapsed into a set, and terminal tails eventually
   publish their parent wake. Enqueue/COMMIT gaps and byte-level journals are
   not refinement-checked. `Hold` is persistent and affects the single root in
   its dedicated scenario; independent successor-root hold generations are
   outside that scenario. Accepted callbacks can still be consumed while held.
8. `AllowRepeatedRevisit=FALSE` models the source guard in this branch: no new root
   while any transitive revisit descendant is nonterminal, including escalated
   and blocked rows. Reachability is closed over all three bounded slots; terminal
   intermediates are traversed. It allows another revisit after successors end,
   rather than imposing the pilot's old one-revisit-per-predecessor restriction.
   `TRUE` bypasses that guard only in `pre-fix*.cfg` to retain the old defect.
   All `current*.cfg` use the guard. `bounded-environment.cfg` is retained by
   name and now checks the same guard/scoped liveness rather than restricting
   the caller. `current`/`current-executors` use `RecoverableDeadProgress`;
   the known broad liveness counterexample remains checked in
   `current-unconditional.cfg` (and human hold in `current-hold.cfg`).
   Structural child retries and thread dispatch are outside `Revisit` admission:
   retries have non-NULL parents; thread `resolves` roots have no revisit edge.
9. Source inspection supports the mapping; TLC explores the model, not Python.
   Passing finite checks do not prove unbounded behavior or deployment state.
   No production mutation or reproduction was performed.

## Run TLC

Use the existing `/usr/bin/java`; keep the jar and all TLC state directories
outside the repo. The author used the pinned official
[TLA+ tools v1.7.4 release](https://github.com/tlaplus/tlaplus/releases/tag/v1.7.4)
(`TLC2 Version 2.19`, revision `5a47802`), jar SHA-256
`936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`,
and OpenJDK `21.0.13.1`. See the [TLC CLI guide](https://docs.tlapl.us/using:tlc:start).

From this directory, with absolute task-scratch paths:

```sh
curl -fL https://github.com/tlaplus/tlaplus/releases/download/v1.7.4/tla2tools.jar -o "$TASK_TMP/tla2tools.jar"
/usr/bin/java -XX:+UseParallelGC -Xmx512m -cp "$TASK_TMP/tla2tools.jar" tlc2.TLC -workers 1 -fp 0 -seed 1 -metadir "$TASK_TMP/states-bug139" -config bug139.cfg TaskLifecycle
```

Substitute each config in the results table, using a distinct `-metadir`.
Every author run completed in under three seconds; no TLC job was necessary.
If expanding the model can exceed a minute, use the repository's durable jobs
workflow. Exit 12 is an invariant counterexample; exit 13 is a temporal
counterexample. Neither is an invocation failure or a passing design.

Repair TLC logs, commands and hashes are in `output/TASK-18273/`.
The original pilot audit script and full audit report remain in `output/TASK-18026/`. None belong in this
folder. The shared audit artifact is
`dev_agent-2026-11-20-tla-audit-conformance.md`.

## Explicit never-launched cancellation business close

`NeverLaunchedCancelClose.tla` models the human-only transition in
`runtime/workflows/sequential_cancel_close.py`: `Cancel` is the existing fence;
`HumanClose` checks cancelled task plus the conjunction of no durable admission
or other launch evidence, commits one finish/CAS/audit/receipt, and `Replay`
preserves that receipt. `EngineFinish` abstracts the unchanged positive original
terminal+outer tail. `Admit` cannot run after the cancellation fence. Two keys
model conflicting requests and exact-key replay. The 256 historical unknown
entries stay unchanged; completion releases only computed business headroom.

Run with the pinned jar/command above, substituting module
`NeverLaunchedCancelClose` and configs `never-launched.cfg` and
`never-launched-unsafe.cfg`. The unsafe config deliberately removes the proof
conjunction and must violate `SafeHumanClose`. Results belong in task review
evidence. This finite atomic-transaction abstraction does not verify Python,
SQL parsers, individual proof sources, actual locks, crash storage, or native
cleanup. Refusal/rollback stutters; no cleanup or liveness claim is made.

TASK-19067 author run (TLC 2.19, official v1.7.4 jar SHA256
`936a262061c914694dfd669a543be24573c45d5aa0ff20a8b96b23d01e050e88`):
`never-launched.cfg` exit 0, 14 generated/11 distinct states, depth 4, all five
invariants pass; `never-launched-unsafe.cfg` exit 12, 12 generated/9 distinct,
depth 3, expected `SafeHumanClose` violation with retained positive evidence.
Both used one worker, fingerprint 0, seed 1 and task-scratch state directories.
Small logs: `dev_agent/output/TASK-19067/tlc-{current,unsafe}.log`.

## Startup publication interval

`StartupPublication.tla` and `startup-publication.cfg` model pre-worker closed
visit settlement, publication after wiring, and an initial normal reaper interval.
A completion arriving after wiring remains durable and is eventually published by
a fair periodic sweep. Startup cannot serve before settlement/wiring, and the
first periodic sweep cannot precede the interval. Two abstract ticks stand for
30 seconds; this is not a wall-clock serving bound. No cache, authority, history
validation, cancellation, native custody or queue consumption is modeled. Those
remain production/private-fixture obligations. Run the pinned TLC recipe above
with module `StartupPublication` and this config; evidence is task output.
