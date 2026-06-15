---
name: manage-agent
description: Enroll, update, or terminate an agent. Write a JSON file and call happyranch manage-agent --from-file to keep the invocation single-line. Enrollment requires founder approval.
---

# manage-agent

Manage the agent roster. You can **enroll** a new agent (requires founder approval), **update** an existing agent's system prompt or description, or **terminate** an agent (removes its workspace).

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

This prevents a Content Manager from enrolling agents into the engineering team, and vice versa.

## What happens

- **enroll**: Creates a pending enrollment request. You may optionally specify `executor: "claude"`, `executor: "codex"`, `executor: "opencode"`, or `executor: "pi"`; if omitted, it defaults to `claude`. You may also include `"allow_rules": ["curl https://api.example.com", ...]` to grant additional Bash prefixes beyond the baseline `happyranch` grant — for example, to allow a specific external API call. The founder must run `happyranch approve-agent --org {ORG_SLUG} <name>` before the agent's workspace is bootstrapped and the agent becomes available for delegation.
- **update**: Updates the agent's description, system prompt, executor, or repos in the enrollment registry. If the system prompt or executor changes, the workspace bootstrap files are regenerated. Only works on approved agents.
- **terminate**: Marks the agent as terminated and deletes its workspace directory. Only works on approved agents.

## Agent naming

Agent names must be lowercase with underscores only (e.g. `content_writer`, `seo_agent`). No spaces, hyphens, or uppercase.

## Error handling

- If `happyranch` returns non-zero, retry once after 1 second.
- `409` (duplicate name on enroll, non-approved agent on update/terminate) and `404` (agent not found, talk not found) are not retryable.
- `422` usually means the payload is missing required auth fields (task_id + session_id) — fix the JSON and retry.
