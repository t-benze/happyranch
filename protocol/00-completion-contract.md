# Completion Contract

This document is the **canonical specification** of the universal completion-report format, the manager decision schema, and the agent-callback command list. The contract is identical for every agent except where explicitly noted.

The workspace bootstrap docs (`CLAUDE.md` / `AGENTS.md`) and the `start-task` skill (`.claude/skills/start-task/` or `.agents/skills/start-task/`) are the **operational** restatements that agents read at session time. They point back at this contract; they do not re-inline its body. When an agent's runtime behavior conflicts with this document, fix one of them — they must agree.

## Task completion report

When you finish a task, write your completion payload to `/tmp/completion-<task_id>.json` and call back via:

```
happyranch report-completion --from-file /tmp/completion-<task_id>.json
```

The `--from-file` form is mandatory across executors — multi-line `happyranch` invocations are blocked by the shared permission matcher.

Payload shape (required keys: `task_id`, `session_id`, `agent`, `status`, `summary`; everything else optional):

```json
{
  "task_id": "<the task_id from the prompt>",
  "session_id": "<the session_id from the prompt>",
  "agent": "<this agent's name>",
  "status": "completed",
  "summary": "<short prose summary of what you did>",
  "confidence": 85,
  "risks": ["<concern the reviewer should look at hardest>"],
  "dependencies": ["<work this depends on or blocks>"],
  "reviewer_focus": ["<which file(s) or aspect to review first>"],
  "output_dir": "output/<task_id>"
}
```

`summary` is prose; the structured arrays (`risks`, `dependencies`, `reviewer_focus`) are first-class JSON keys, not subfields embedded inside `summary`. `confidence` is an integer 0–100 indicating how sure you are the work is correct (default 80 if omitted).

For review/QA-type workers, optionally include a structured verdict:

```json
{
  "task_id": "...",
  "session_id": "...",
  "agent": "senior_dev",
  "status": "completed",
  "confidence": 92,
  "summary": "Code review complete. All 7 verification rows green...",
  "verdict": "APPROVE"
}
```

`verdict` is a free-string field. Each team's workflow KB entry documents the allowed values (e.g., engineering uses `APPROVE | REQUEST_CHANGES | BLOCK` for reviews; `PASS | REVISE | BLOCK` for QA). Omit when not applicable. Inline delegation chains (see `decision.then` below) use this field to gate auto-advance.

## Blocker path

Use `"status": "blocked"` when you cannot finish and need the orchestrator to route around you. Set `"confidence": 0` and put the blocker reason in `summary` — the orchestrator reads it verbatim when deciding the next step.

## Manager decision field (manager-only)

Team-manager sessions must additionally include a structured `decision` object alongside the prose `summary`. The orchestrator parses `decision` directly via the `NextStep` pydantic model — it never infers intent from prose. Workers omit `decision` entirely.

`decision.action` is one of:

- `"delegate"` — spawn a child task on another agent. Requires `agent` (target agent name) and `prompt` (child task brief). When re-delegating to an agent that has a FAILED child under this parent (e.g., retrying a failed fan-out slice), the field `revisit_of_task_id` is MANDATORY — it must carry the failed predecessor's task id so the orchestrator can track per-slice retry count from existing DB lineage (no schema migration). Omitting `revisit_of_task_id` in that context is a hard reject — the delegate is denied and the owner receives feedback to retry with the field set.
- `"done"` — terminal; the root task finishes here. Optional `summary` for a final outcome note.
- `"escalate"` — surface to the founder for resolution. Requires `reason`.
- `"fanout"` — spawn N child tasks in parallel (2 ≤ N ≤ 8). Requires `children` (array of `{agent, prompt}` objects). `width_cap_ack` is required and must exactly equal the child count. Optional `join_summary` (prose directive for the join prompt). Each child may optionally carry `then`/`expect_verdict` to run its own inline delegation chain — a *pipeline carrier* (Phase 2). A child targeted at a **team manager** is decision-capable (mutating fan-out, THR-056 msg39): it can return delegate-chain decisions that spawn implementation subtrees inside its branch. A child targeted at a regular **worker** is read-only (its structured decisions are ignored; it completes with a summary). NO fan-out review gate of any kind (founder ruling THR-012 msg 129/131) — the width cap (8) is a machine-resource limit only; the real control over what lands is the per-PR merge gate: each mutating child opens its own PR needing code_reviewer APPROVE + qa PASS + CI + founder/EM merge.

**Field-name note:** the child task's brief lives in `decision.prompt`, not `decision.brief`. The schema silently ignores unknown keys, so writing `"brief"` produces a child task with an empty brief. Use `"prompt"`.

Examples — same payload shape as a worker's, plus a top-level `decision`:

```json
{
  "task_id": "...",
  "session_id": "...",
  "agent": "<this agent's name>",
  "status": "completed",
  "summary": "Triaged the request; staging implementation work for dev_agent.",
  "decision": {
    "action": "delegate",
    "agent": "<target agent name>",
    "prompt": "<child task brief>"
  }
}
```

```json
{
  "task_id": "...",
  "session_id": "...",
  "agent": "<this agent's name>",
  "status": "completed",
  "summary": "Reviewed dev_agent's output and verified tests pass; root task complete.",
  "decision": {
    "action": "done",
    "summary": "<one-line outcome>"
  }
}
```

```json
{
  "task_id": "...",
  "session_id": "...",
  "agent": "<this agent's name>",
  "status": "completed",
  "summary": "Hit a budget threshold beyond my authority; surfacing to founder.",
  "decision": {
    "action": "escalate",
    "reason": "<why founder intervention is required>"
  }
}
```

### Inline delegation chains

A manager can declare a multi-leg workflow in one decision via `decision.then` (additional legs) and per-leg `expect_verdict` gates:

```json
{
  "task_id": "...",
  "session_id": "...",
  "agent": "engineering_head",
  "status": "completed",
  "summary": "Dispatching Item 1a small-feature gate chain.",
  "decision": {
    "action": "delegate",
    "agent": "dev_agent",
    "prompt": "Build Item 1a Gallery uplift...",
    "then": [
      {"agent": "senior_dev",  "prompt": "Code-review the PR described in prior-leg context.", "expect_verdict": "APPROVE"},
      {"agent": "qa_engineer", "prompt": "QA the PR described in prior-leg context.",          "expect_verdict": "PASS"}
    ]
  }
}
```

The orchestrator spawns the first leg, then auto-advances to the next leg on each child terminal whose `verdict` matches the leg's `expect_verdict`. Any mismatch (or `status=blocked`) clears the chain and wakes the manager. The final leg's match wakes the manager too — chains do not auto-`done`. Each subsequent leg's brief is auto-suffixed with a "Prior leg context" block (the upstream worker's summary + verdict + output_dir).

Step-budget effect: declaring a chain consumes one orchestration step; auto-advances do NOT consume steps. A clean small-item workflow (`dev → senior_dev[APPROVE] → qa_engineer[PASS]`) costs 2 steps (declare + final wake) instead of 4.

Cross-team validation runs on every leg at decision-parse time; any off-team agent rejects the whole decision via the feedback mechanism.

See `docs/superpowers/specs/2026-05-30-inline-delegation-chain-design.md`.

### Fan-out (parallel delegation, Phase 1)

A manager can spawn N child tasks in parallel:

```json
{
  "task_id": "...",
  "session_id": "...",
  "agent": "engineering_head",
  "status": "completed",
  "summary": "Dispatching parallel read-only investigation across 3 agents.",
  "decision": {
    "action": "fanout",
    "children": [
      {"agent": "dev_agent",    "prompt": "Investigate module A"},
      {"agent": "qa_engineer",   "prompt": "Investigate module B"},
      {"agent": "product_manager", "prompt": "Investigate module C"}
    ],
    "width_cap_ack": 3,
    "join_summary": "Synthesize findings into a unified plan"
  }
}
```

Constraints: 2 ≤ N ≤ 8 (hard cap); `width_cap_ack` is required and must exactly equal the child count. No fan-out review gate of any kind (founder ruling THR-012 msg 129/131) — the width cap (8) is a machine-resource limit only; the real control over what lands is the per-PR merge gate. Each child may optionally carry `then`/`expect_verdict` (a *pipeline carrier* — Phase 2): the child runs its own inline delegation chain (`{agent, prompt, expect_verdict}` legs, validated like an inline `delegate + then` chain) and reaches a terminal state only after that chain completes, at which point it counts toward the parent's fan-out barrier. A child targeted at a **team manager** is decision-capable (mutating fan-out, THR-056 msg39): it receives `task_type='task'` so its delegate-chain decisions are parsed and can spawn implementation subtrees inside its branch. A child targeted at a regular **worker** is read-only (its structured decisions are ignored; it completes with a summary). The parent parks in `in_progress(delegated)` with `active_fanout` metadata and wakes once when all children (carriers included) are terminal. The manager receives a structured join context block with each child's outcome.

**Integration model (a).** Each mutating child opens its own PR. The parent join summarizes outcomes. Children own DISJOINT file sets (manager responsibility); shared-file convergence routes through a SERIAL follow-up delegate after join, never a fan-out child.

See KB `fanout-primitive-founder-ratification` and
`output/TASK-1101/native-fanout-phase1-refresh.md`.

## Completion blocked on an asynchronous external condition

A task whose requested outcome depends on an ASYNCHRONOUS EXTERNAL TERMINAL CONDITION — a long-running external job, a deploy, an external approval workflow, an external CI run — is NOT complete until that condition resolves. The task owner may not report `done` at an intermediate milestone (e.g., submission, handoff, or initiation) when completion requires the external system's terminal verdict.

The runtime primitive for waiting on external conditions is the existing jobs plus `waiting_on_job_ids` path:

1. The task owner captures the identity of the external artifact or process it must wait on.
2. The task owner submits a bounded poller job that monitors the external condition to a terminal verdict.
3. The task owner reports `status="blocked"` with `waiting_on_job_ids=["JOB-NNN"]`.
4. The task remains `in_progress(blocked_on_job)` until the job is terminal. The normal blocked-on-job resume path reinvokes the task owner with the job result.
5. On resume, the task owner inspects the job output. It reports `done` only if the job proves the external condition resolved successfully. Failure, timeout, or a missing/disputed result must produce a revise/fail/escalate decision — never a false completion.

Do not infer external success from an intermediate signal. The poller job — not the task owner's session — reaches the terminal verdict; the task owner gates completion on that verdict alone.

Example: a task that must land a pull request waits on that PR's external CI through this path; the engineering-domain specifics (SHA-pinning, settle window, guarded-merge gates) live in the jobs skill and agent guides.

### Who emits a decision, and delegation scope

**Decision emitters:** Any agent that owns a `task_type=task` task must emit a `decision` field — not only `role: manager` agents. Conversely, an agent owning a `task_type=subtask` task is a leaf: it reports `status` + `output_summary` and omits `decision` entirely. The orchestration gate keys on `task.task_type`, not on agent role.

**Self-delegation (self-decomposition):** A non-manager owner may `delegate` only to **itself** — spawning the next sub-task in a sequence it owns and orchestrates, getting woken on each child terminal. Team managers may delegate to own-team agents or to themselves. Any attempt by a non-manager to delegate to a different agent is rejected with feedback; the task re-runs so the owner can revise its decision.

**Escalation:** Only a **root** task (`task_type='task'`, no parent) escalates to the founder. A non-root subtask that would escalate instead **fails** and hands back to its parent; bounded failure-recovery (TASK-573) carries it up, and the root escalates if it cannot resolve.

See `docs/superpowers/specs/2026-06-03-subtask-composite-task-design.md` for the design rationale.

## Mid-task learnings

Durable lessons go through:
```
happyranch learning --agent <you> --session-id <sid> --task-id <task_id> --text "..."
```

Cross-agent reference material — SOPs, partner-API quirks, founder rulings — belongs in the Knowledge Base (`happyranch kb add --from-file ...`), not in `learnings.md`.

## Other agent-side callbacks

| Command | Purpose |
|---|---|
| `happyranch report-completion --from-file ...` | End-of-task callback (mandatory). |
| `happyranch learning --agent ... --session-id ... --task-id ... --text ...` | Durable per-agent operational lesson. |
| `happyranch manage-repo {add\|remove\|update} --agent ... --repo-name ... [--url ...]` | Add/remove/update a repo clone in your workspace. |
| `happyranch manage-agent --from-file ...` | (Team managers only) enroll/update/terminate an agent within your own team. |
| `happyranch kb add --agent ... --from-file ...` | Contribute a knowledge-base entry. |
| `happyranch kb update <slug> --agent ... --from-file ...` | Update an existing entry. |
