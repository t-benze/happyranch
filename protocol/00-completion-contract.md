# Completion Contract

This document is appended to every agent's system prompt by the orchestrator at session-build time. It defines the universal completion-report format, the Engineering Head decision schema, and the agent-callback command list. The contract is identical for every agent except where explicitly noted.

## Task completion report

When you finish a task, write your completion payload to `/tmp/completion-<task_id>.json` and call back via:

```
opc report-completion --from-file /tmp/completion-<task_id>.json
```

The `--from-file` form is mandatory across executors — multi-line `opc` invocations are blocked by the shared permission matcher.

Payload shape:

```json
{
  "task_id": "<the task_id from the prompt>",
  "session_id": "<the session_id from the prompt>",
  "agent": "<this agent's name>",
  "status": "completed",
  "summary": "<short prose summary of what you did>"
}
```

The summary should include:
- **Confidence** — how sure you are the work is correct (high/med/low + one-line reason).
- **Risks flagged** — anything the reviewer should look at hardest.
- **Dependencies** — work this depends on or blocks.
- **Suggested reviewer focus** — which file(s) or which aspect to review first.

## Blocker path

Use `"status": "blocked"` when you cannot finish and need the orchestrator to route around you. Put the blocker reason in `summary` — the orchestrator reads it verbatim when deciding the next step.

## Engineering Head decision field (manager-only)

Engineering Head sessions must additionally include a structured `decision` object. `summary` stays prose; the orchestrator parses `decision` directly.

```json
{
  "task_id": "...",
  "session_id": "...",
  "agent": "engineering_head",
  "status": "completed",
  "summary": "<what you did or concluded this step>",
  "decision": {
    "action": "delegate",
    "agent": "<target agent name>",
    "brief": "<child task brief>"
  }
}
```

`decision.action` is one of:
- `"delegate"` — spawn a child task on another agent (also set `agent` + `brief`).
- `"done"` — terminal; the root task finishes here.
- `"escalate"` — surface to the founder for resolution (also set `reason`).

## Mid-task learnings

Durable lessons go through:
```
opc learning --agent <you> --session-id <sid> --task-id <task_id> --text "..."
```

Cross-agent reference / precedent material belongs in the Knowledge Base (`opc kb add --from-file ...`), not in `learnings.md`.

## Other agent-side callbacks

| Command | Purpose |
|---|---|
| `opc report-completion --from-file ...` | End-of-task callback (mandatory). |
| `opc learning --agent ... --session-id ... --task-id ... --text ...` | Durable per-agent operational lesson. |
| `opc manage-repo {add\|remove\|update} --agent ... --repo-name ... [--url ...]` | Add/remove/update a repo clone in your workspace. |
| `opc manage-agent --from-file ...` | (Engineering Head + Content Manager) enroll/update/terminate an agent within your team. |
| `opc kb add --agent ... --from-file ...` | Contribute a knowledge-base entry. |
| `opc kb update <slug> --agent ... --from-file ...` | Update an existing entry. |
