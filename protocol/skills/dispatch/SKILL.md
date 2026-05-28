---
name: dispatch
description: Dispatch a new task to the orchestrator from inside an open talk. Workers can only self-dispatch; team managers can dispatch to any agent in their team. Cross-team dispatch is forbidden.
---

# dispatch

> **Self-only from thread / talk:** Inside a thread or talk turn, this command
> may only target yourself. See `protocol/skills/thread/SKILL.md` for the
> doctrine and `grassland threads compose` for cross-agent work.

Inside an OPEN talk, you can submit a new root task to the orchestrator without ending the talk. The founder is co-present in the talk; their authority is what the dispatch borrows. Use this when something actionable surfaces in conversation that you and the founder agree should become a task.

## When to use

- You and the founder have explicitly agreed in conversation that a task should be created.
- The new task fits within your role's authority (workers: yourself; managers: anyone on your team).
- You can describe the work in a single, concrete brief.

If any of those is missing, do not dispatch — keep talking, or recommend the founder run `grassland run` themselves later.

## Authentication

Authority comes from the OPEN talk itself: pass the `talk_id` of the talk you are currently in. There is no task-path auth on dispatch (workers in a task already have their own session; this is a talk-only feature).

## Usage

1. **Write a JSON file** to `/tmp/dispatch-<talk_id>.json` using the Write tool.

   **Worker self-dispatch (most common):**
   ```json
   {
     "talk_id": "<talk_id>",
     "brief": "Implement Option B for TASK-087: change the trigger to a 2-hop join through guide_days."
   }
   ```

   **Manager dispatching to a team worker (explicit target):**
   ```json
   {
     "talk_id": "<talk_id>",
     "brief": "Audit the payment_agent's last three completed tasks for refund-policy drift.",
     "target_agent": "qa_engineer"
   }
   ```

   `target_agent` is optional and defaults to **yourself**. `team` is also optional and defaults to your own team — supplying a different team is rejected.

2. **Invoke as a single-line command:**

   ```bash
   grassland dispatch --org {ORG_SLUG} --from-file /tmp/dispatch-<talk_id>.json
   ```

   The `--from-file` form is mandatory in agent sessions. Multi-line bash is rejected by the `Bash(grassland:*)` permission rule because newlines count as command separators.

## Authorization rules

| Your role     | Can target                         | Default target |
|---------------|------------------------------------|----------------|
| Worker        | Yourself only                      | Yourself       |
| Team manager  | Any agent in your team (incl. you) | Yourself       |

Cross-team dispatch is forbidden in all cases. If you want a task to land on another team, surface it to the founder in conversation and let them decide.

## Record the call in your transcript

After dispatching, **record the call in the `transcript_markdown` you will send at `/talk end`**. One line per dispatch is enough, e.g.:

```
[during talk] dispatched TASK-042 to dev_agent: "Implement Option B for TASK-087".
```

The audit log captures the action (`grassland audit --org {ORG_SLUG} TASK-042`), but the transcript is what the founder reads back. Skipping this silently mutates the queue from the founder's point of view.

## What happens

The orchestrator inserts a new root task with `assigned_agent` set to your `effective_target` and enqueues it for execution. Worker self-dispatch **bypasses the team manager's EH decision step** — the conversation is treated as the gating decision, so the worker runs directly. Manager dispatches to a team worker behave the same way: the manager has already decided, so the orchestrator runs the assignee.

The new task carries `dispatched_from_talk_id = <your talk_id>` for observability. `grassland details --org {ORG_SLUG} TASK-NNN` shows a "Dispatched from" line.

## Error handling

- `404 not_found`: the `talk_id` doesn't exist. Re-check the id you typed.
- `400 talk_not_open`: the talk has been closed or abandoned. Open a new talk if needed.
- `422 empty_brief`: the brief was missing or whitespace-only. Re-state the work clearly.
- `422 empty_team`: you sent `team: ""`. Drop the field (defaults to your own team) or set it to a real team name.
- `422 empty_target_agent`: you sent `target_agent: ""`. Drop the field (defaults to yourself) or set a real agent name.
- `403 teams_registry_unavailable`: the daemon's team registry isn't loaded. Wait briefly and retry; if it persists, escalate to the founder.
- `403 dispatcher_team_unknown`: your agent record is not registered with any team. Ask the founder.
- `403 cross_team_dispatch_forbidden`: you tried to set `team` to a value other than your own.
- `403 worker_must_self_dispatch`: you are a worker and `target_agent` was not yourself.
- `403 target_not_in_team`: you are a manager and `target_agent` is not on your team.
- `404 unknown_agent`: the resolved target has no approved workspace.

If `grassland` returns non-zero, retry once after 1 second. The 4xx codes above are not retryable — fix the payload.

## Naming

Use `/tmp/dispatch-<talk_id>.json` so multiple dispatches in the same talk don't collide on a fixed filename.
