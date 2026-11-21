# Revisit dedupe repair — 2026-11-21 (TASK-18273)

Source base: `774c91572` (THR-386 seq21). The original pilot results below are
historical evidence at their stated base; the following results supersede their
current-design admission conclusions.

`Revisit` now records `revisitOf` and refuses allocation while any nonterminal
transitive descendant exists. `AllowRepeatedRevisit=FALSE` enables this guard;
`TRUE` preserves the pre-fix behavior. Terminal intermediates remain traversable,
and all-terminal successors permit a later revisit. The check and allocation
are one model action, corresponding to the Python DB owner's `BEGIN IMMEDIATE`
check/insert transaction under `org.db_lock` and `origin_guard`.

Actual TLC 2.19 / pinned v1.7.4 jar outputs, Java 21, one worker, `-fp 0 -seed 1`,
three task slots, one restart (all runs under two seconds):

| Config | Exit | Generated | Distinct | Result |
|---|---:|---:|---:|---|
| `bounded-environment.cfg` | 0 | 35359 | 7443 | Guard and scoped recovery pass (retained config name) |
| `bug139.cfg` | 12 | 54 | 38 | Historical restart twin reproduced |
| `bug574.cfg` | 13 | 37 | 18 | Historical unknown deferral reproduced |
| `current-executors.cfg` | 0 | 35359 | 7443 | `OneLiveRootProcess` and scoped recovery pass |
| `current-hold.cfg` | 13 | 71 | 36 | Known human-hold broad-liveness exception preserved |
| `current-recovery.cfg` | 0 | 38 | 18 | Unheld ordinary recovery passes |
| `current-restart.cfg` | 0 | 144 | 68 | No automatic revisit; recovery passes |
| `current-temporal.cfg` | 0 | 35359 | 7443 | Terminal absorption and scoped recovery pass |
| `current-unconditional.cfg` | 13 | 35359 | 7443 | Known landed-escalation broad-liveness exception preserved |
| `current.cfg` | 0 | 35359 | 7443 | `OneDriver` and scoped recovery pass; no duplicate driver |
| `pre-fix-executors.cfg` | 12 | 802 | 427 | Old `OneLiveRootProcess` counterexample reproduced |
| `pre-fix.cfg` | 12 | 90 | 55 | Old `OneDriver` counterexample reproduced |

All configurations also check `TypeOK` and `TerminalAbsorbing`. The two primary
current safety configurations use `RecoverableDeadProgress`, the pilot's already
qualified property; broad liveness is still explicitly exercised by the unchanged
property in `current-unconditional.cfg` and `current-hold.cfg`. The live-descendant
guard does not fix or hide those intentional human-control exceptions.

The pre-fix trace still contains Cancel(root), Revisit(root,child),
Revisit(root,next) with child pending; the process variant then starts both.
The added edge state changes pre-fix state counts compared with the original
pilot. Neither path is enabled with the current guard. This is finite model
evidence, not proof of the Python implementation or deployment.

Path inspection: HTTP `/tasks/{id}/revisit` and CLI reach
`revisit_from_notification` -> `TasksMixin.insert_revisit_task`; its indexed
recursive query traverses terminal predecessors and checks all statuses outside
completed/failed/cancelled/superseded. Thread dispatch(`resolves`) and escalation
supersession allocate unlinked roots with separate blocked-predecessor/replay
rules; delegate/fanout revisit links are non-root failed-child retries. They do
not enter this root-revisit admission path and were not widened.

The disposable API regression run failed on the base at duplicate admission
(11 failed, four positive terminal-successor cases passed), then all 15 cases
passed with the guard. Refusals preserve persisted rows, audits and queue state.
No production org was mutated. Exact TLC argv, statuses, counts and log hashes,
API/CLI-renderer transcript and focused-test output are in `output/TASK-18273/`.
The original audit conformance analysis was not rerun for this repair.

---

# Original pilot results — 2026-11-20

Source base: `84915adec7f448babfcf5598c9b5d6a1b356850e`.
The commands and assumptions are in [README.md](README.md).

## TLC evidence

All runs use three task slots, one bounded restart, one worker, `-fp 0 -seed 1`.
The two historical flags are off in every `current-*`, `current` and
`bounded-environment` config. Counts include stuttering/duplicate generated
states; safety failures stop exploration early.

| Config | Exit | Generated | Distinct | Outcome |
|---|---:|---:|---:|---|
| `bug139.cfg` | 12 | 54 | 38 | Historical double-driver safety counterexample |
| `bug574.cfg` | 13 | 37 | 18 | Historical unknown-owner liveness counterexample |
| `current-restart.cfg` | 0 | 144 | 68 | Same restart/parked-root scenario passes with no auto-revisit |
| `current-recovery.cfg` | 0 | 38 | 18 | Same unheld ordinary unknown/dead-owner scenario passes |
| `current.cfg` | 12 | 90 | 53 | Current repeated-revisit double-driver counterexample |
| `current-executors.cfg` | 12 | 748 | 393 | Both successor root producers can be alive simultaneously |
| `current-hold.cfg` | 13 | 71 | 36 | Unreleased human hold refutes broad liveness |
| `current-unconditional.cfg` | 13 | 39,347 | 7,188 | Landed escalation result refutes broad liveness |
| `current-temporal.cfg` | 0 | 39,347 | 7,188 | Terminal absorption and recoverable-death liveness pass; single-driver safety is not asserted here |
| `bounded-environment.cfg` | 0 | 28,674 | 6,138 | All asserted properties pass under the explicit one-revisit-per-predecessor caller restriction and recoverable-death qualification |

Each config checks `TypeOK` and `TerminalAbsorbing`; all but the two temporal-only
configs assert one of the single-driver invariants. The last two configs check
`RecoverableDeadProgress`; the others check the broader `DeadProducerProgress`.
The failing current safety run does not independently establish temporal
properties: separate exhaustive temporal runs provide that evidence.

## Historical incident MEM-139 / THR-064, 2026-07-02

The memory is in engineering_manager's namespace. The old
`_maybe_spawn_auto_revisit(child_id)` walked to the root; sweep-local revisit
deduplication did not coordinate with the original root's parent-wake path.

Trimmed **actual TLC trace**, `bug139.cfg`:

```text
1 root=in_progress/delegated; child=in_progress/NULL, alive; next absent
2 Crash: daemon down; child producer dead
3 Boot: daemon up; original root still delegated
4 Recover(child): child=failed (startup Branch 1)
5 LegacyRevisit(next): next=pending root for the same work; queue={next}
6 Wake(root): original delegated root enqueued; queue={root,next}
  OneDriver fails: both original and revisit have dispatch authority.
```

**Current-code finding:** automatic revisit-successor creation was removed by
`ed0c03946809c2ecd6e7012d85b20a609f34cd49`, PR #515 (2026-07-28).
There is no current `_maybe_spawn_auto_revisit` definition. Startup failure calls
`_enqueue_parent_if_waiting(..., root_auto_revisit_spawned=False)`;
`runtime/orchestrator/run_step.py:_enqueue_parent_if_waiting` documents that the
flag is compatibility bookkeeping. `current-restart.cfg` checks that design.
The memory's proposed parked-root skip is therefore not presented as today's fix.

## Historical incident MEM-574 / THR-350, 2026-11-10

The engineering_manager memory attributes the defect to PR #1026,
`952415fec`: crash-interrupted ordinary entries became `unknown`, causing both
startup and reaper to defer before testing the dead producer.

Trimmed **actual TLC trace**, `bug574.cfg`:

```text
1 root=in_progress/NULL, producer alive, journal=running, no callback
2 Crash: producer dead, daemon down
3 Boot: journal running -> unknown; daemon up
4 Stutter forever: legacy unknown deferral disables Recover.
  DeadProducerProgress fails despite weak fairness: recovery is not enabled.
```

**Current-code finding:** PR #1367,
`6bf58e941cc1c1890b6e2f0410d5387e91642b83` (2026-11-11), added narrow ordinary
unknown-owner recovery. Startup and reaper now compute `ordinary_unknown_owner`
and skip only when `pause_deferred_owner` is true **and** that owner is absent.
The ordinary selector excludes held/mixed/stale identities. Positive dead-PID
or exact pre-boot session evidence permits existing lifecycle settlement and
parent wake. Unknown journal uncertainty is retained as settled producer
evidence, not erased. `current-recovery.cfg` passes for that precise domain.
This does not claim that every unknown journal is recoverable.

## Current-design finding: repeated founder revisits (pre-fix)

**Triage: reachable product design defect relative to the requested single-driver
contract; source-supported, not reproduced by mutating the live system.**
Serial founder calls are sufficient; no race is needed. The two successors inherit
the same predecessor brief, so the work-equivalence assumption is concrete here.

Trimmed **actual TLC traces** (`current.cfg`, extended by
`current-executors.cfg`):

```text
1 root=pending
2 Cancel(root): root=cancelled
3 Revisit(root,child): child=pending ROOT; root stays cancelled
4 Revisit(root,next): next=pending ROOT; child is still pending
  OneDriver fails: queue={child,next}; both have parent=none.
5 Start(child): child=in_progress/NULL, alive
6 Start(next): next=in_progress/NULL, alive
  OneLiveRootProcess fails: two executing roots in the same work lineage.
```

Code pointers checked at the source base:

* `runtime/daemon/routes/tasks.py:_classify_predecessor_status` continues to accept
  the cancelled/failed/completed predecessor after the first revisit.
* `revisit_from_notification` inserts a fresh pending NULL-parent root linked by
  `revisit_of_task_id`. It closes the predecessor/family only for
  `blocked-escalated` or `blocked-delegated`; a terminal predecessor is unchanged.
  The terminal branch does not reject an already-live successor.
* `_collect_eligible_revisit_family` closes only eligible blocked relatives; it
  does not provide a terminal-predecessor live-successor admission guard.
* `runtime/orchestrator/task_pause.py:origin_guard` serializes the origin's Pause
  hold. It is not a revisit-family exclusivity fence.
* `runtime/infrastructure/db/tasks.py:_try_claim_for_step_uncommitted` claims by
  the individual task ID/status/block. Distinct pending successor IDs can each
  win; a per-task CAS cannot reject the sibling root.

This historical finding led to founder approval THR-386 seq21. The repair
and rerun above implement the live-descendant guard. At the original pilot base,
`AllowRepeatedRevisit=FALSE` was only a caller restriction; it now models the
transactional guard and allows repeated revisits after terminal successors.

## Current liveness: human control is an explicit exception

`current-hold.cfg` produces:

```text
running ordinary root -> Crash -> Boot(journal=unknown)
-> Hold -> stutter forever with dead producer and in_progress row
```

`_ordinary_unknown_entries` returns no ordinary recovery owner when `row["held"]`
is true. Both sweep callers consequently defer on the retained journal. Weak
fairness cannot force a disabled action. **Triage: the unconditional requirement
is too strong for the intentional human hold contract (model/property scope
error), not a recurrence of unheld MEM-574.** No autonomous hold release is assumed.

`current-unconditional.cfg` finds a second qualification. Its prefix delegates
two bounded children that fail preparation, wakes the root, then publishes a
root result. The crash suffix is:

```text
root running with a landed callback -> Crash -> Boot(unknown)
-> Finish(escalated) -> Rerun -> Start -> Finish(escalated) -> repeat
```

The exact result survives the crash and asks for escalation. Root escalation is
nonterminal and founder-owned (`run_step.py:_consume_completion_report_body`);
fair recovery cannot promise human resolution or eventual success of repeated
reruns. **Triage: broad property scope error, not a new product bug.** The weaker
recoverable-death property excludes landed callbacks and passes with terminal
absorption in `current-temporal.cfg`. No unconditional current-design PASS is
claimed.

## One-off 30-day audit conformance

The source DB was confirmed as `~/.happyranch/runtime/orgs/happyranch/happyranch.db`
(both `tasks` and `audit_log` present); the candidate `org.db` had neither table.
Python opened the live source with `mode=ro` and used SQLite backup into runtime
task scratch. Analysis opened only that copy read-only. No DB writes, live PID
probes, lifecycle repairs or audit instrumentation changes were performed.

Window: **2026-10-21 10:04:55 UTC through 2026-11-20 10:04:55 UTC**.
The snapshot contains 18,030 tasks. Coverage is 6,488 tasks, 272,350 exact-task
audit rows (350,114 total audit rows; other scopes excluded), and 38,833 selected
semantic events. Reconstructed observations yield **20,085 checkpoint pairs**:

* 20,016 direct allowed edges: **99.6565% direct conformance**.
* 69 gaps: **56 spec gaps** from passive carriers completing without a producer,
  and **13 audit-too-coarse gaps** requiring unrecorded intermediate states.
  Every gap is listed by task and event IDs in the full report.
* 100% are reachable if hidden intermediate states are permitted. This does
  **not** establish that those hidden states occurred, or validate passive
  carriers against the executable-task model. No confirmed real bug was found
  by this audit projection.
* Across 3,713 observed root session starts, no unclosed linked-family session
  overlap was observed. The snapshot contains one dispatch-capable root in one
  mechanical lineage and no duplicate family. This cannot prove the historical
  driver invariant: queue/claim intervals and SID-bearing session-end receipts
  are absent, and unrelated fresh roots lack a semantic work key.

Completion reports (10,364) and orchestration decisions (4,390) were counted as
consumer inputs, not misread as committed terminal/parking writes. An initial
projection that mapped resolution `supersede` to `cancelled` was corrected after
checking the implementation; those two false incompatibilities are not reported
as product defects. Historical code versions within the window may differ from
the source base.

The script and full report stay in `output/TASK-18026/`; the shared report is
`dev_agent-2026-11-20-tla-audit-conformance.md`. Retained output includes exact
commands, exits and full small TLC logs. No live database or raw audit payloads
are included in the PR or artifact.
