# Sub-tasks / Type-Driven Orchestration — Design

**Date:** 2026-06-03
**Status:** Design ratified; ready for implementation plan
**Origin:** Today the capability to orchestrate a multi-step workstream is welded to `role: manager` — only managers' completion output is parsed as a `NextStep` decision (`run_step.py:299`), only managers get the orchestration prompt, and only managers spawn child tasks. The founder wants orchestration driven by the **task** rather than the **manager role**: any agent that owns a top-level task should be able to spawn sub-tasks, drive their completion, and be woken when each sub-task terminates — without a manager in the loop.

## Goal

Let a top-level task spawn sub-tasks that its owning agent orchestrates: the owner is woken when each sub-task reaches a terminal state and decides what runs next. Decouple "can orchestrate" from `role: manager` and re-gate it on **task type**. The driving sentence: *whether a task orchestrates depends on whether it is a top-level `task` (vs a spawned `subtask`), not on who owns it.*

This generalizes the existing manager-delegation machinery — it does **not** add a parallel mechanism. After this change, manager delegation and an agent's self-decomposition are the *same act*: "spawn a sub-task from an ongoing task." The only thing the manager role still gates for spawning is **who you may target** (own-team agents vs self).

## Non-goals

- **Nesting / recursive orchestration.** A `subtask` is leaf-only — it cannot itself spawn sub-tasks. The task tree is strictly two levels: one orchestrator (`type=task`) → N leaf executors (`type=subtask`). This preserves the proven topology the system runs today and keeps termination trivially bounded by the existing per-task step budget. Recursion (with a shared tree-wide budget) is a deliberate future upgrade, deferred until a concrete need appears.
- **Manager delegation of an orchestrating task.** A manager-delegated child is always a leaf `subtask`. A manager cannot hand a worker a self-managed sub-orchestration (that would be nesting). If a delegated leaf turns out too big, it reports `blocked` and the manager re-decomposes.
- **Cross-agent fan-out by non-managers.** A non-manager owner may only spawn sub-tasks targeting **itself**. Cross-agent delegation stays a manager power (own-team scope, unchanged).
- **New governance powers.** `manage-agent`, KB delete, peer-review / `review_verdict`, and dispatch role-tagging stay welded to `role: manager`. This change touches orchestration capability only.
- **Reusable / named workflow templates.** No typed workflow registry. Orchestration is authored at runtime by the owning agent, exactly as managers author decisions today.

## Core model

### 1. `type` is provenance, set at spawn

`TaskRecord` gains:

```python
type: Literal["task", "subtask"] = "task"
```

- **`task`** — a top-level task. Created by founder dispatch. Its owner may orchestrate (spawn sub-tasks).
- **`subtask`** — a task spawned from an *ongoing* task. Leaf-only: its owner executes and terminates; it cannot spawn.

`type` records **where the task came from**, not a behavior label chosen up front. "Composite / orchestrator" is never stored — it is derived: a task acts as an orchestrator exactly when it is a `type=task` root. (Equivalently: only `task`s can have sub-tasks, so "is composite" ≡ "is a `task` that has spawned sub-tasks.")

**Migration (ratified — uniform backfill):** every existing `tasks` row → `type="task"` via the column `DEFAULT 'task'`. No conditional backfill on `parent_task_id`. The provenance rule (*spawned-from-ongoing → `subtask`*) is forward-only and does not retro-classify historical children. This is safe because the `type=="task"` gate only fires when the owner emits a `decision`, which legacy leaf workers never do; the sole observable effect is that a rare *in-flight* legacy child, if re-run after migration, would receive the orchestrator prompt — an acceptable edge case the founder accepted in favor of a trivial migration.

### 2. The spawn gate is `type == "task"`

The `is_team_manager(agent)` gate on decision parsing (`run_step.py:299`) is **replaced** by `task.type == "task"`. This is the whole feature in one line: orchestration is driven by task type, not manager role.

- Owner of a `type=task` → completion `decision` is parsed; may `delegate` (spawn sub-task), `done`, or `escalate`.
- Owner of a `type=subtask` → leaf path, unchanged from today's worker behavior: `status=completed` → task completes; `status=blocked` → block; no `decision` parsing.

### 3. Target scope (the one thing role still gates for spawning)

Validated at decision-parse time, for the `delegate` target and every `then` leg:

| Owner role | May target |
|------------|------------|
| Manager (of a `type=task`) | Own-team agents *(unchanged)* — and self (see below) |
| Non-manager (of a `type=task`) | **Self only** |

Any out-of-scope target rejects the whole decision with feedback, reusing the existing off-team reject-and-re-decide path (`_chain_legs_off_team`).

**Self-targeting must be explicitly un-banned — for managers and non-managers alike** (a manager may also break its own work into bounded sub-task sessions). Two existing guards stand in the way:

1. **Roster omission (cosmetic):** `_list_candidate_agents` does `team_members.discard(calling_manager)` (`run_step.py:592`), so the owner is never *offered* itself as a target in its prompt. For a `type=task` owner, include self in the candidate roster.
2. **Revision-count heuristic (the load-bearing one):** `run_step.py:400–411` bumps `revision_count` when a new delegation targets the **worker-of-record** (earliest-completed child's agent), on the convention "re-delegating to the same agent = a revise cycle," which trips escalate-after-2-rounds. Self-sequencing delegates to *itself every step*, so this would fire on every self-spawn and spuriously self-escalate after two sub-tasks. **Fix: self-targeted delegations must be exempt from the `revision_count` bump** — a self-targeted leg is a sequence step, not a revise of another agent's work. The revise-loop heuristic only applies across a maker→checker boundary, which self-sequencing has none of.

### 4. Topology: strictly two levels

```
founder dispatch
      │
      ▼
  [type=task]  ← orchestrator (any agent; manager → team / non-manager → self)
   │   │   │
   ▼   ▼   ▼
 [subtask][subtask][subtask]   ← leaf executors, cannot spawn
```

This is exactly today's manager→worker shape, with the orchestrator generalized to any agent. No third level exists.

### 5. Entry points

- **Founder dispatch** creates `type=task` roots. The dispatch route grows an optional `owner` parameter: `--owner <agent>` assigns the root directly to any agent (which then self-decomposes, no manager in the loop). When `owner` is omitted, behavior is unchanged — the root auto-assigns to `manager_for_team(team)`.
- **Manager delegation** produces `type=subtask` leaves only (today's manager→worker path, now formally typed). A manager cannot create an orchestrating task via delegation (that would be nesting — Non-goal).

### 6. Escalation routing

On `decision=escalate` from a `type=task` owner: walk `parent_task_id` to the nearest **manager-owned** ancestor.
- **Found** → wake that manager for a re-decision; the escalation reason is surfaced in the manager's next-step prompt header (reuse the existing resolved-escalation header mechanism).
- **None** (founder-dispatched lineage with no manager ancestor) → existing `notify_escalated` → founder.

A `type=subtask` leaf does not escalate via `decision`; it reports `status=blocked` and its `type=task` parent is woken to decide (today's worker→manager behavior).

### 7. Prompt surface

`_build_agent_prompt` re-gates on `task.type`:

- **`type=task`, manager owner** → full capabilities prompt (decision schema + own-team roster + prior steps) — unchanged from today.
- **`type=task`, non-manager owner** → a **reduced** decision schema: self-targeted `delegate` + `done` + `escalate`. No team roster (target is always self).
- **`type=subtask`** → returns `""` (lean leaf prompt — the brief, as workers get today).

This keeps the spawn schema *off* every leaf invocation; only `type=task` invocations carry it.

### 8. Chains come for free

A `type=task` owner may pre-declare a sequence of self-targeted sub-task legs via the existing `NextStep.then` (auto-advanced by `compute_advance_action` without consuming wakes), **or** spawn reactively one-at-a-time and decide on each wake. Self-targeted legs are validated by §3 like any other leg. No new chain code — the existing inline-delegation-chain machinery applies once self-targeting is permitted.

### 9. Budget & termination

Per-task `orchestration_step_count` with `max_orchestration_steps` (50), unchanged. Because only `type=task` roots wake/increment and sub-tasks are leaves (run once, terminate, never wake), a single root bounds its whole tree at ≤ 1 + 50 tasks. Termination is guaranteed by code that already exists; **no new budget machinery.**

## Completion-contract resolution (the central `run_step` change)

Replace the role gate at `run_step.py:299` with a type gate:

```python
# before:  if orch.teams.is_team_manager(agent): decision = orch._parse_next_step(report)
# after:
if task.type == "task":
    decision = orch._parse_next_step(report)   # delegate / done / escalate
    # ... log_orchestration_step, then dispatch on decision.action ...
else:  # subtask → leaf
    decision = NextStep(action="done", summary=report.output_summary)
```

Resolution table for a `type=task` owner:

| Report | Result |
|--------|--------|
| `decision=delegate` (target valid per §3) | Spawn `type=subtask` child, parent → `BLOCKED(DELEGATED)`, woken on child terminal |
| `decision=delegate` (target invalid) | Reject decision, re-decide (existing feedback path) |
| `decision=done` | Task completes |
| `decision=escalate` | Route per §6 |
| no `decision` + `status=completed` | Task completes (safety default — leaf-style finish from a root owner) |
| no `decision` + `status=blocked` | Block (existing) |

## Schema additions

**`src/models.py` — `TaskRecord`:**

```python
type: Literal["task", "subtask"] = "task"
```

**Database — one new column on `tasks`:**

```sql
ALTER TABLE tasks ADD COLUMN type TEXT NOT NULL DEFAULT 'task';
```

Added in **both** the `tasks` CREATE TABLE (fresh DBs) and the idempotent ALTER block (existing DBs), matching the two-place schema convention. The `DEFAULT 'task'` migrates every existing row.

When spawning a child (`run_step.py` delegate branch + the dispatch route's manager-delegation), the child `TaskRecord` is created with `type="subtask"`. Founder-dispatched roots are created with `type="task"`.

## Touch points (implementation surface)

- `src/models.py` — `TaskRecord.type`.
- `src/infrastructure/database.py` — column in CREATE + ALTER; `create_task` / child-insert paths carry `type`; `get_task` hydrates it.
- `src/orchestrator/run_step.py` — gate flip (`is_team_manager` → `type=="task"`); self-target validation in `_validate_one_leg` / `_chain_legs_off_team`; lift self-delegation bans; child created with `type="subtask"`; escalate routing walks to nearest manager ancestor.
- `src/orchestrator/run_step.py` `_build_agent_prompt` — three-way branch on `type` + role (§7); a reduced self-only capabilities prompt.
- `src/orchestrator/capabilities.py` — reduced (self-only, roster-less) variant of the capabilities prompt.
- `src/daemon/routes/tasks.py` — dispatch route grows optional `owner`; stops force-assigning `manager_for_team` when `owner` is given; default path unchanged.
- `src/cli.py` — `dispatch` grows `--owner`.
- Web/OpenAPI — dispatch body gains optional `owner`; regenerate the OpenAPI snapshot + mirror the TS function (contract-pinning tests).
- `protocol/00-completion-contract.md` — document that `type=task` owners (not just managers) emit decisions, self-targeted delegation, and the escalate-to-nearest-manager rule.

## Invariants to preserve

- **`type` is provenance, never a behavior label set by a human's up-front judgment.** A task is a `subtask` iff it was spawned from an ongoing task. Do not add an API that lets a caller declare a child `type=task`.
- **Sub-tasks never spawn.** The `type=="task"` gate is the single chokepoint that guarantees the two-level topology and therefore termination. Do not add a code path that parses a `subtask` owner's `decision`.
- **Self-target validation is role-gated, not type-gated.** Managers keep own-team scope; non-managers get self-only. Both only apply to `type=task` owners (subtasks can't spawn at all).
- **Governance stays manager-only** (manage-agent, KB delete, peer review, dispatch role-tagging).
- **Existing parent-wake / cascade-fail / auto-revisit machinery is untouched** — it keys off `parent_task_id` + terminal state, not role or type, so it works for any orchestrator without change.
- **No new maker-checker hole.** Self-decomposition involves no cross-agent approval, so it neither violates nor satisfies maker-checker. Cross-agent review still requires a manager or an escalation; a non-manager owner cannot spawn a reviewer (self-only).

## Resolved decisions

- **Managers may also self-target** a sub-task (symmetry with non-managers — lets a manager break its own work into bounded sessions). Requires the §3 guard changes: include self in the candidate roster, and exempt self-targeted delegations from the `revision_count` bump.
- **Migration is the uniform `DEFAULT 'task'` backfill** (see §1).

## Open sub-decisions (resolve during planning)

- **Founder dispatch `--owner` validation:** the owner must be a registered agent with a workspace (reuse the existing agent-exists check). Does `--owner` imply a team, or is `--team` still required for KB/escalation context? Likely require both, or infer team from the agent's registration.
