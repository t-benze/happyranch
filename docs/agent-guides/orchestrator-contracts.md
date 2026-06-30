# Orchestrator Contracts

## Conventions

- Type hints on all function signatures.
- `from __future__ import annotations` in every source file.
- Pydantic v2 for structured data.
- `StrEnum` for enumerations.
- Agent names are plain strings; agents are discovered dynamically from `<runtime>/orgs/<slug>/org/agents/*.md`.
- Tests should cover business logic such as escalation rules and audit-log shape.

`README.md` is for end users. `CLAUDE.md` is for repo-wide agent instructions. Design docs in `protocol/` and specs in `docs/superpowers/specs/` are the source of truth for behavior.

When starting a feature, read the relevant design doc first and follow existing patterns in `runtime/orchestrator/`.

## Org Content APIs

`AgentDef` in `runtime/orchestrator/agent_def.py` represents an agent file: markdown with YAML frontmatter parsed/rendered by `parse_agent_text` and `render_agent_text`.

Fields: `name`, `team`, `role`, `executor`, `description`, `allow_rules`, `repos`, `enrolled_by`, `enrolled_at_task`, `enrolled_at`, and `system_prompt`. There is no `session_timeout_seconds` field.

`runtime/orchestrator/prompt_loader.py` is the API for reading/writing agent files: `load_agent`, `list_agents`, `list_pending`, `write_pending_agent`, `approve_agent`, and `reject_agent`. Routes and orchestrator code should read through this module against the per-org root.

`TeamsRegistry` in `runtime/orchestrator/teams.py` is seeded from `teams.yaml` and auto-persists on `add_worker` and `remove_worker`. There is no `DEFAULT_LAYOUT`; an org without `teams.yaml` is empty.

## Task Status Vocabularies

Agents self-report `status="completed"|"blocked"` via `happyranch report-completion` (the report verb is unchanged — an agent still self-reports "blocked on jobs"). The orchestrator-owned `TaskStatus` on the `tasks` row is distinct, and under THR-037 Change B (Path B) is: `pending`, `in_progress`, `escalated`, `completed`, `failed`, `cancelled`, or `resolved_superseded`. (`blocked` is fully retired as of Phase 3 — see the Path-B spec.)

`block_kind` is the waiting-reason discriminant for an `in_progress` task — *what it is internally waiting on*: `delegated` (waiting on child subtasks) or `blocked_on_job` (waiting on background jobs). `block_kind IS NULL` ⟺ a subprocess is running now. A parent waiting on its children/jobs stays `in_progress` (not `blocked`); the await-founder state is the top-level `escalated`.

`resolved_superseded` is a terminal state, peer to `completed`/`failed`. An `escalated` / `in_progress(delegated)` task transitions here when a human-authorized continuation (founder `revisit`, or a founder/manager thread-dispatch) names it in lineage: the predecessor is closed (block_kind cleared, audit cites the continuation root task_id) instead of being re-run. The close never re-enqueues the superseded task; it still wakes a delegated parent via the normal parent-wake path, and the delegated close is gated on all children being terminal so no live sibling is abandoned or SIGTERM'd. It joins every terminal predicate (`TERMINAL_STATES`, `_TERMINAL_TASK_STATUSES`, `_TERMINAL_STATUS_TO_EVENT`) and is completion-class for the thread task-followup: a thread-originated task that is superseded emits its `_maybe_post_thread_followup` system message (`task_completed` kind) just like a normal completion. The thread-dispatch supersede is manager-authorized only — a worker self-dispatch naming `resolves` is rejected (`403 thread_supersede_not_authorized`); the predecessor is never auto-closed by an unauthorized dispatch. Query the backlog with `happyranch tasks --status escalated` or `happyranch tasks --status in_progress --block-kind delegated`.

## Manager Decision Contract

Team-manager completion payloads carry two fields:

- `summary`: human-readable prose stored on `task_results.output_summary` and rendered in details, audit logs, and `task_history.md`.
- `decision`: a JSON `NextStep` object stored on `task_results.decision_json` and parsed directly by `Orchestrator._parse_next_step`.

The child-task brief field in a `delegate` decision is `prompt`, not `brief`. Pydantic v2 silently ignores extras, so `"brief"` creates an empty-brief child task.

Full schema and examples: `protocol/00-completion-contract.md`.

## Inline Delegation Chains

A manager can declare a multi-leg workflow in one `delegate` decision using `NextStep.then` and optional per-leg `expect_verdict` gates. The orchestrator auto-advances to the next leg when a child terminates completed with a matching verdict.

Implementation: `runtime/orchestrator/chain.py` and `runtime/orchestrator/run_step.py`. Spec: `docs/superpowers/specs/2026-05-30-inline-delegation-chain-design.md`.

Example:

```json
{
  "action": "delegate",
  "agent": "dev_agent",
  "prompt": "Build the feature...",
  "then": [
    {"agent": "senior_dev", "prompt": "Code-review the PR.", "expect_verdict": "APPROVE"},
    {"agent": "qa_engineer", "prompt": "QA the PR.", "expect_verdict": "PASS"}
  ]
}
```

## Task/Subtask Terminology

The data model uses `task_type` `Literal['task','subtask']`:

- **Task** (`task_type='task'`): the task owner — holds the decision-making
  loop and produces `decision` blocks (`delegate`/`fanout`/`done`/`escalate`; the `parallel` alias is accepted for `fanout`).
- **Subtask** (`task_type='subtask'`): the delegated agent — executes a
  bounded unit of work and reports a plain completion (no `decision` field).

Prose in docstrings, comments, and prompt strings prefers "task owner" and
"subtask agent" over the legacy "team manager" / "worker" language. The
`task_type` enum values were already correct before TASK-573; the sweep only
updated prose, not schema or role-identity strings.

## Bounded Failure-Recovery (TASK-573)

When a subtask fails, the parent task is re-enqueued for a bounded manager-wake
decision step — NOT cascade-failed. This replaces the pre-TASK-573 behavior where
any subtask FAILED unconditionally cascade-failed the parent without giving the
task owner a chance to re-ground.

Contract (founder-approved in THR-028):

1. **Bounded wake.** On subtask failure, re-enqueue the parent for a fresh
   decision step. The failed subtask's reason is available so the task owner can
   author an updated brief.

2. **Round bound.** At most 2 re-spawn rounds per delegation slot. The round
   count is derived from EXISTING database state (count of FAILED subtask
   siblings) — no schema migration.

3. **Escalation on exhaustion.** When the bound is exhausted (> 2 FAILED
   subtasks in this delegation slot), the parent transitions to
   `escalated` via `try_escalate()`. The parent does NOT cascade-fail.

4. **Chain-leg failure.** A failed workflow chain leg (subtask FAILED, not
   COMPLETED) clears the active chain and hands the parent back to its
   bounded-wake path (same 2-round bound + escalation).

5. **Happy path unchanged.** All subtasks COMPLETED → parent enqueued for
   next decision step. REVISE-verdict auto-advance in chains is unchanged.

6. **Reviewer/QA verdict discipline.** A review/QA leg completes with an
   APPROVE/REVISE/PASS/FAIL verdict and never self-blocks. A `status=blocked`
   with empty `waiting_on_job_ids` is a malformed report; the leg is treated
   as FAILED and wakes the parent for a decision step.

Traps:

- Round count = `len([s for s in siblings if s.status == FAILED])`;
  threshold `_FAILURE_ROUND_BOUND = 2` (`>= 2` → escalate).
- The bound escalation uses `try_escalate` (atomic CAS under Database RLock).
- Chain-advance in `_enqueue_parent_if_waiting` handles FAILED subtasks:
  failed chain legs clear the chain and fall through to bounded-wake.
- Self-block (`status=blocked` + empty `waiting_on_job_ids`) is a malformed
  report that fails the review/QA leg. Never self-block in a review/QA role.

Inline traps:

- Auto-advances do not consume orchestration steps. Declaring a chain costs one step; the final-leg wake costs one.
- A final-leg match still wakes the manager. Chains never auto-`done`.
- Cross-team validation runs on every leg at parse time. An off-team agent on any leg rejects the whole decision.
- Do not pre-embed upstream context in a leg prompt; `build_prior_leg_context` appends it automatically.
