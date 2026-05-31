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
  "artifact_dir": "artifacts/<task_id>"
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

- `"delegate"` — spawn a child task on another agent. Requires `agent` (target agent name) and `prompt` (child task brief).
- `"done"` — terminal; the root task finishes here. Optional `summary` for a final outcome note.
- `"escalate"` — surface to the founder for resolution. Requires `reason`.

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

The orchestrator spawns the first leg, then auto-advances to the next leg on each child terminal whose `verdict` matches the leg's `expect_verdict`. Any mismatch (or `status=blocked`) clears the chain and wakes the manager. The final leg's match wakes the manager too — chains do not auto-`done`. Each subsequent leg's brief is auto-suffixed with a "Prior leg context" block (the upstream worker's summary + verdict + artifact_dir).

Step-budget effect: declaring a chain consumes one orchestration step; auto-advances do NOT consume steps. A clean small-item workflow (`dev → senior_dev[APPROVE] → qa_engineer[PASS]`) costs 2 steps (declare + final wake) instead of 4.

Cross-team validation runs on every leg at decision-parse time; any off-team agent rejects the whole decision via the feedback mechanism.

See `docs/superpowers/specs/2026-05-30-inline-delegation-chain-design.md`.

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
| `happyranch dispatch --from-file ...` | (Talk-mode) Spawn a new task from inside an open talk. Workers may dispatch only to themselves; team managers may dispatch to any agent in their own team. Cross-team dispatch is forbidden. |
| `happyranch talk end --talk-id ... --from-file ...` | End an open talk; persists the transcript and extracts end-of-talk learnings. |
