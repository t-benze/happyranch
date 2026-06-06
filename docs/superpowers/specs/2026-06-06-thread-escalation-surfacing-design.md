# Thread Escalation Surfacing — Design Spec

**Date:** 2026-06-06
**Status:** Draft, pending implementation.
**Origin:** Founder-reported gap on tourism-org THR-016 (2026-06-06): `TASK-893` (EH Phase-2 deploy) was dispatched from THR-016, escalated to `blocked/escalated`, and the daemon sent the Feishu escalation notification — but nothing appeared in THR-016. The founder watches the thread and expected the escalation to surface where the conversation lives.
**Relates to:**
- `docs/superpowers/specs/2026-05-28-thread-task-followup-design.md` — the terminal task-followup mechanism this extends to the escalated (non-terminal) state. This spec reuses its system-message + re-invocation machinery.
- `docs/superpowers/specs/2026-05-13-threads-design.md` — the threads primitive.
- `docs/superpowers/specs/2026-04-21-opc-revisit-design.md` — the revisit chain the lookup walks.
- `protocol/skills/thread/SKILL.md` — the agent-facing skill that gains the `task_escalated` followup turn shape.

## 1. Goal

When a task dispatched from a thread **escalates to the founder**, the runtime injects a `task_escalated` SYSTEM message into the originating thread and re-invokes the dispatching manager so it can compose a founder-facing follow-up — mirroring the terminal task-followup. The existing Feishu escalation notification is **unchanged and additive**: Feishu is the push channel, the thread is the in-context record.

## 2. Motivation

THR-016 evidence (tourism-org, 2026-06-06):

- THR-016 seq 94 (`task_dispatched`): `TASK-893` dispatched to `engineering_head`.
- `TASK-893` ended `blocked` / `block_kind=escalated` at 19:03:54; audit shows `escalation` then `escalation_notify_sent` (Feishu) at 19:03:55.
- No corresponding entry in THR-016: the escalation went only to Feishu.

Root cause (verified in code):

- `runtime/orchestrator/run_step.py` `escalate` branch (line ~323) flips the task to `blocked(escalated)`, audits, and calls `notify_escalated` (Feishu) — it never calls `_maybe_post_thread_followup`.
- `_maybe_post_thread_followup` (`run_step.py:1588`) is **terminal-only**: it early-returns for any status other than `COMPLETED`/`FAILED` (`run_step.py:1616`). `escalated` is a `blocked` (waiting) state, so it is out of scope by the 2026-05-28 spec §4 fire predicate.

The behavior was correct per the original spec, which deliberately scoped to terminal states. This spec widens it to also surface the escalated state.

## 3. Non-goals

- Backfilling escalation notices for already-escalated tasks (e.g. the current TASK-893). The hook fires on future escalation transitions only.
- Surfacing intermediate progress (`happyranch progress`) in the thread. Only the escalation transition posts.
- Changing the Feishu escalation notification, its reply TTL, or `resolve-escalation` semantics.
- Changing the dispatch authority / token rules.
- Allowing the escalation followup turn to chain a new dispatch (the `TASK_FOLLOWUP` purpose already forbids `dispatch`; §7 loop bound).

## 4. Fire predicate

A new helper `_maybe_post_thread_escalation(orch, task_id, *, reason)` is invoked at run_step's two escalation sites, **after** the audit + `notify_escalated` calls. It fires iff, on a fresh DB read:

| Condition | Action |
|---|---|
| task status is `blocked` AND block_kind is `escalated` | proceed |
| anything else (founder resolved/cancelled in the race window) | no-op |
| the chain has no thread linkage (§5) | no-op (silent; not a thread-dispatched chain) |
| the originating thread is not `OPEN` | no-op + `thread_followup_skipped` audit |

Unlike the terminal helper, there is **no `auto_revisit_spawned` gate** (escalation is orthogonal to revisit) and **no root-only gate** (§5).

### 4.1 Escalation does not cascade — handle any depth

Terminal states cascade: a child failure fails the parent, which re-enters `_maybe_post_thread_followup` at the root, so that helper assumes "only root fires" (`run_step.py:1638`). Escalation does **not** cascade — `run_step.py:345`: "parent stays blocked(DELEGATED) until this task reaches a terminal." A team manager at any depth can return `action=escalate`, leaving its own task `blocked/escalated` while ancestors sit `blocked/delegated`.

Therefore the escalation helper must resolve the originating thread from a possibly-non-root task by walking ancestors first (§5), and must **not** apply the root-only early-return.

## 5. Finding the originating thread

```
ancestors = db.walk_ancestors(task_id)          # [task, parent, ..., root]; root.parent_task_id is None
root      = ancestors[-1] if ancestors else <current task>
chain     = db.walk_revisit_chain(root.id, truncate=True)  # [root, predecessor, ..., original]
original  = chain[-1] if chain else root
thread_id = original.dispatched_from_thread_id
if thread_id is None: return                     # not a thread-dispatched chain
```

`walk_ancestors` already exists (`database.py:818`); `walk_revisit_chain` already exists (`database.py:840`). Revisit roots don't copy `dispatched_from_thread_id`, which is why the revisit walk runs from the chain root. `LineageTooDeep` from `walk_ancestors` is caught defensively → `thread_followup_skipped(reason="chain_too_deep")` + return (mirrors the terminal helper). The revisit walk uses `truncate=True` (read-path convention) so it never raises.

**Dispatcher identity** comes from the `thread_dispatch` audit row keyed by `original.id` — identical to the terminal helper. If absent → `thread_followup_skipped(reason="dispatcher_unresolved")` + return.

## 6. Hook behavior

### 6.1 Shared tail extraction

`_maybe_post_thread_followup`'s post+mint+enqueue tail (from "append system message" through the thread-queue enqueue, `run_step.py:~1713-1772`) is extracted verbatim into:

```
_append_followup_system_and_reinvoke(
    orch, *, thread_id, dispatcher, original_id, source_task_id, system_payload,
) -> None
```

It: appends the SYSTEM message (`db.append_thread_message`), runs the atomic cap-projection + conditional bump + mint (`db.mint_followup_invocation_with_cap_extend`), audits (`thread_turn_cap_auto_extended` if bumped, `thread_task_followup_enqueued`), and enqueues the `ThreadJob` via `run_coroutine_threadsafe` with the existing queue/loop-unavailable defenses (`thread_followup_skipped(reason="enqueue_failed"|"enqueue_unavailable")`).

The terminal helper is refactored to build its `system_payload` then call this tail — **no behavior change** for the terminal path. The escalation helper builds its own payload and calls the same tail.

### 6.2 System message payload (escalation)

```
system_payload = {
  "kind_tag": "task_escalated",
  "task_id": <escalated task id, may be a child or revisit root>,
  "original_task_id": <original dispatched task id>,
  "root_task_id": <chain root id>,
  "status": "escalated",
  "reason": <escalation reason, the same text passed to notify_escalated>,
  "revisit_chain_length": <len(chain)>,
}
```

The message carries the full `reason`, so the escalation is visible in-thread even if the re-invocation later no-ops or fails (§6.4). `speaker` = dispatcher; `kind` = `SYSTEM`.

### 6.3 Re-invocation

Purpose is `ThreadInvocationPurpose.TASK_FOLLOWUP` (reused, no new enum value). `reply` and `decline` already accept `TASK_FOLLOWUP`; `dispatch` already rejects it. Turn-cap auto-extend (each followup bumps by at most 1) is reused as-is via the shared tail.

### 6.4 Failure mode

Identical to 2026-05-28 §6.5: if the SYSTEM message commits but mint/enqueue fails, the message stays (transparency) and `thread_task_followup_enqueue_failed` / `thread_followup_skipped(reason="enqueue_failed")` is audited. The founder still sees the escalation in-thread and can `@<agent>` manually.

## 7. Call sites

Both escalation paths in `runtime/orchestrator/run_step.py`, each **after** the existing `log_escalation` + `notify_escalated`:

1. `decision.action == "escalate"` branch (line ~339): `_maybe_post_thread_escalation(orch, task_id, reason=reason)`.
2. Max-steps-exceeded budget guard (line ~96): `_maybe_post_thread_escalation(orch, task_id, reason="max steps (<N>) exceeded")` (the same `reason` string already passed to `notify_escalated`).

## 8. Prompt builder change

`runtime/daemon/thread_runner.py::_purpose_note`, `task_followup` branch: when the triggering message's `system_payload["status"] == "escalated"` (equivalently `kind_tag == "task_escalated"`), return escalation-specific wording:

```
Task <task_id> that you dispatched from this thread has ESCALATED to the
founder: "<reason, trimmed>". The task is blocked awaiting a founder
decision. Post a concise reply in this thread that states what you need
from the founder and why, so she sees it in context (pull details via
`happyranch details <task_id>`). Do not attempt to resolve the escalation
yourself; do not dispatch a new task from this turn. Decline if the Feishu
escalation already says everything and a thread restatement adds nothing.
```

The completed/failed wording is unchanged for non-escalated `task_followup` turns.

## 9. Renderers

New `task_escalated` kind_tag case in the two thread system-message renderers (the only two; task-lifecycle `task_completed`/`task_failed` references elsewhere are unrelated event logs):

- **`runtime/infrastructure/thread_store.py`** (transcript, ~line 103): mirror `task_failed`:
  `**Task TASK-NNN escalated**` + ` (chain root TASK-MMM)` when root differs + ` · {reason[:240]}`.
- **`web/src/features/threads/ThreadsPage.tsx`** (~line 530): `case 'task_escalated'` mirroring `task_failed` — task-id `<Link>` + ` · {reason[:240]}`.

## 10. Skill doc

`protocol/skills/thread/SKILL.md`: document that an escalated dispatched task injects a `task_escalated` SYSTEM message and a `task_followup` turn whose intent is to restate the founder ask in-thread (not to resolve it), alongside the existing `task_completed`/`task_failed` description.

## 11. Loop bound

Unchanged from 2026-05-28 §8: the `TASK_FOLLOWUP` token cannot `dispatch`, and each followup auto-extends the turn cap by at most 1, so re-invocation cannot recurse. An escalation that is later resolved and reaches a terminal posts the existing `task_completed`/`task_failed` followup — a distinct, bounded event. Re-escalation after resolution posts a new `task_escalated` — one per escalation transition (`db.try_escalate` CAS guarantees one transition per event).

## 12. Web UI / contract

No new route; the threads stream already carries SYSTEM messages and their payload generically. No `web/src/lib/api/` change, no OpenAPI snapshot change. Only the `ThreadsPage.tsx` renderer case is added.

## 13. Testing

- **`run_step` unit:**
  - thread-dispatched **root** escalates → `task_escalated` SYSTEM message appended with `reason` + `TASK_FOLLOWUP` invocation minted/enqueued.
  - **child-depth** escalation (manager-of-subtask) → surfaces via `walk_ancestors` to the thread-dispatched root.
  - non-thread-dispatched task escalates → no thread mutation (Feishu path untouched).
  - founder resolved/cancelled in the race window (status no longer `blocked/escalated`) → no-op.
  - max-steps-exceeded escalation on a thread-dispatched chain → posts.
- **`thread_store`** render test: `task_escalated` payload renders with task id, chain-root suffix, trimmed reason.
- **`ThreadsPage.test.tsx`**: `task_escalated` renders task-id link + reason.
- **Terminal-path regression:** existing `_maybe_post_thread_followup` tests still pass after the tail extraction (no behavior change).
- No daemon-lifespan / `SessionTracker` / queue-recovery surface changes; existing thread-queue wiring is reused.
