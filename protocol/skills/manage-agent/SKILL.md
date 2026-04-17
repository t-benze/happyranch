---
name: manage-agent
description: Enroll, update, or terminate an agent. Write a JSON file and call opc manage-agent --from-file to keep the invocation single-line. Enrollment requires founder approval.
---

# manage-agent

Manage the agent roster. You can **enroll** a new agent (requires founder approval), **update** an existing agent's system prompt or description, or **terminate** an agent (removes its workspace).

## Usage

1. **Write a JSON file** to `/tmp/manage-agent-<unique>.json` using the Write tool:

   **Enroll a new agent:**
   ```json
   {
     "action": "enroll",
     "name": "content_writer",
     "task_id": "<task_id>",
     "session_id": "<session_id>",
     "description": "Writes destination guides and travel articles",
     "system_prompt": "You are the Content Writer. Your responsibilities are...",
     "repos": {"web-content": "https://github.com/t-benze/web-content.git"}
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
     "system_prompt": "Updated system prompt..."
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
   opc manage-agent --from-file /tmp/manage-agent-<unique>.json
   ```

   The `--from-file` form is mandatory for agent sessions. Multi-line bash
   commands are rejected by the `Bash(opc:*)` permission rule (newlines count
   as command separators).

## Access control

Only the **Engineering Head** may use this skill. The daemon validates that the
`task_id` and `session_id` belong to an active EH session. Other agents will
receive a `403 Forbidden` error.

## What happens

- **enroll**: Creates a pending enrollment request. The founder must run `opc approve-agent <name>` before the agent's workspace is bootstrapped and the agent becomes available for delegation.
- **update**: Updates the agent's description, system prompt, or repos in the enrollment registry. If the system prompt changes, the workspace's CLAUDE.md is regenerated. Only works on approved agents.
- **terminate**: Marks the agent as terminated and deletes its workspace directory. Only works on approved agents.

## Agent naming

Agent names must be lowercase with underscores only (e.g. `content_writer`, `seo_agent`). No spaces, hyphens, or uppercase.

## Error handling

- If `opc` returns non-zero, retry once after 1 second.
- `409` (duplicate name on enroll, non-approved agent on update/terminate) and `404` (agent not found) are not retryable.
