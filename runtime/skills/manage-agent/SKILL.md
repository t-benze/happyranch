---
name: manage-agent
description: Enroll, update, or terminate an agent. Write a JSON file and call happyranch manage-agent --from-file to keep the invocation single-line. Enrollment requires founder approval.
---

# manage-agent

Manage the agent roster. You can **enroll** a new agent (requires founder approval), **update** an existing agent's system prompt or description, or **terminate** a non-manager worker (archives its definition and workspace).

## Authentication paths

The daemon accepts (task_id + session_id) from your current task session. Use this while executing a task.

## Usage

1. **Write a JSON file** to `/tmp/manage-agent-<unique>.json` using the Write tool.

   **Task-path enroll:**
   ```json
   {
     "action": "enroll",
     "name": "content_writer",
     "task_id": "<task_id>",
     "session_id": "<session_id>",
     "description": "Writes destination guides and travel articles",
     "system_prompt": "You are the Content Writer. Your responsibilities are...",
     "executor": "codex",
     "repos": {"web-content": "https://github.com/t-benze/web-content.git"},
     "allow_rules": ["gh api /repos/{owner}/{repo}/contents"]
   }
   ```

   **Update an existing agent:**
   ```json
   {
     "action": "update",
     "name": "content_writer",
     "task_id": "<task_id>",
     "session_id": "<session_id>",
     "description": "Updated description",
     "system_prompt": "Updated system prompt...",
     "executor": "claude"
   }
   ```

   **Terminate an agent:**
   ```json
   {
     "action": "terminate",
     "name": "content_writer",
     "task_id": "<task_id>",
     "session_id": "<session_id>"
   }
   ```

2. **Invoke as a single-line command:**

   ```bash
   happyranch manage-agent --org {ORG_SLUG} --from-file /tmp/manage-agent-<unique>.json
   ```

   The `--from-file` form is mandatory for agent sessions. In Claude sessions,
   multi-line bash commands are rejected by the `Bash(happyranch:*)` permission rule
   because newlines count as command separators.

## Access control

Any **team manager** may use this skill to manage agents within their own team. The daemon validates the `(task_id, session_id)` pair matches an active session for a registered team manager. Other agents receive a `403 Forbidden`.

### Team scoping

Managers may only enroll, update, or terminate agents within their own team:

- **enroll**: The new agent is assigned to the caller's team by default. Optionally, include `"target_team": "<team>"` in the payload — but if `target_team` differs from the caller's team, the request is rejected with `403 cross_team_forbidden`.
- **update / terminate**: The target agent must already belong to the caller's team. Cross-team update or termination is rejected with `403 cross_team_forbidden`.
- **terminate additional restriction**: Only non-manager workers may be terminated. Requests to terminate a team manager are rejected with `409 manager_terminate_forbidden`.

This prevents a Content Manager from enrolling agents into the engineering team, and vice versa.

## What happens

- **enroll**: Creates a pending enrollment request. `executor` is a registered executor profile name; if omitted, it defaults to `claude`. Built-in profiles (`claude`, `codex`, `opencode`, `pi`) are examples, not a closed list; org-config custom profiles are valid once registered (see `docs/agent-guides/agent-executors-and-permissions.md`). You may also include `"allow_rules": ["curl https://api.example.com", ...]` to grant additional Bash prefixes beyond the baseline `happyranch` grant — for example, to allow a specific external API call. The founder must run `happyranch approve-agent --org {ORG_SLUG} <name>` before the agent's workspace is bootstrapped and the agent becomes available for delegation.
- **update**: Updates the agent's description, system prompt, executor, model, or repos in the enrollment registry. If the system prompt or executor changes, the workspace bootstrap files are regenerated. A real executor change clears the old executor-specific model when `model` is omitted; include an explicit `model` value (or `null`) to choose the new executor's model (or its CLI default). An unchanged executor preserves an omitted model. Only works on approved agents.
- **terminate**: Archives a quiescent, approved non-manager worker. The active agent file is moved to `org/agents/_terminated/<name>.md`, the workspace is moved to `workspaces/_terminated/<name>/`, and the worker is removed from its team. Historic tasks, audit rows, token records, thread messages/participants, schedules, wakes, dreams, and archived files are preserved. The agent name cannot be re-enrolled while the terminated record exists. Termination is refused when the worker has live work (non-terminal assigned tasks, started thread invocations, firing schedules, running wakes/dreams, or pending/running jobs) or when the target is a manager.

## Agent naming

Agent names must be lowercase with underscores only (e.g. `content_writer`, `seo_agent`). No spaces, hyphens, or uppercase.

## Error handling

- If `happyranch` returns non-zero, retry once after 1 second.
- `409` (duplicate/terminated name on enroll, non-approved agent on update/terminate, manager target, archive collision, or `agent_not_quiescent` conflicts) and `404` (agent not found) are not retryable.
- `422` usually means the payload is missing required auth fields (task_id + session_id) — fix the JSON and retry.
