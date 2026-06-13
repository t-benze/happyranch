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

Agents self-report `status="completed"|"blocked"` via `happyranch report-completion`. The orchestrator-owned `TaskStatus` on the `tasks` row is distinct: `pending`, `in_progress`, `blocked`, `completed`, `failed`, or `resolved_superseded`.

`block_kind` specifies why a task is blocked: `delegated`, `escalated`, or `blocked_on_job`.

`resolved_superseded` is a terminal state, peer to `completed`/`failed`. A `blocked(escalated|delegated)` task transitions here when a human-authorized continuation (founder `revisit`, or a founder/manager thread-dispatch) names it in lineage: the predecessor is closed (block_kind cleared, audit cites the continuation root task_id) instead of being re-run. The close never re-enqueues the superseded task; it still wakes a delegated parent via the normal parent-wake path, and the delegated close is gated on all children being terminal so no live sibling is abandoned or SIGTERM'd. It joins every terminal predicate (`TERMINAL_STATES`, `_TERMINAL_TASK_STATUSES`, `_TERMINAL_STATUS_TO_EVENT`). Query the backlog with `happyranch tasks --status blocked --block-kind escalated|delegated`.

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

Inline traps:

- Auto-advances do not consume orchestration steps. Declaring a chain costs one step; the final-leg wake costs one.
- A final-leg match still wakes the manager. Chains never auto-`done`.
- Cross-team validation runs on every leg at parse time. An off-team agent on any leg rejects the whole decision.
- Do not pre-embed upstream context in a leg prompt; `build_prior_leg_context` appends it automatically.
