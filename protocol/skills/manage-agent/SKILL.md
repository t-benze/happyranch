---
name: manage-agent
description: Enroll, update, or terminate an agent. Write a JSON file and call opc manage-agent --from-file to keep the invocation single-line. Enrollment requires founder approval.
---

# manage-agent

Manage the agent roster. You can **enroll** a new agent (requires founder approval), **update** an existing agent's system prompt or description, or **terminate** an agent (removes its workspace).

## Authentication paths

The daemon accepts two ways to prove you are the Engineering Head:

- **Task path** — supply `task_id` + `session_id` from your current task session. Use this while executing a task.
- **Talk path** — supply `talk_id` from an open talk you are currently in. Use this during a founder talk when the need for an enrollment/update/termination surfaces in conversation.

The two paths are **mutually exclusive** — supply one pair or the other, never both. The daemon rejects payloads that mix them (`422`).

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

   **Talk-path enroll:**
   ```json
   {
     "action": "enroll",
     "name": "content_writer",
     "talk_id": "<talk_id>",
     "description": "Writes destination guides and travel articles",
     "system_prompt": "You are the Content Writer. Your responsibilities are...",
     "executor": "codex",
     "repos": {"web-content": "https://github.com/t-benze/web-content.git"}
   }
   ```

   **Update an existing agent (task path shown; talk path swaps task_id+session_id for talk_id):**
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

   **Terminate an agent (task path shown; talk path swaps task_id+session_id for talk_id):**
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
   opc manage-agent --org {ORG_SLUG} --from-file /tmp/manage-agent-<unique>.json
   ```

   The `--from-file` form is mandatory for agent sessions. In Claude sessions,
   multi-line bash commands are rejected by the `Bash(opc:*)` permission rule
   because newlines count as command separators.

## Access control

Any **team manager** may use this skill to manage agents within their own team. The daemon validates the auth path you supplied:

- Task path: the `(task_id, session_id)` pair must match an active session for a registered team manager.
- Talk path: the `talk_id` must reference a talk whose `agent_name` is a registered team manager and whose `status` is `open`.

Other agents — and closed/abandoned talks — receive a `403 Forbidden` (or `404` if the talk id is unknown).

### Team scoping

Managers may only enroll, update, or terminate agents within their own team:

- **enroll**: The new agent is assigned to the caller's team by default. Optionally, include `"target_team": "<team>"` in the payload — but if `target_team` differs from the caller's team, the request is rejected with `403 cross_team_forbidden`.
- **update / terminate**: The target agent must already belong to the caller's team. Cross-team update or termination is rejected with `403 cross_team_forbidden`.

This prevents a Content Manager from enrolling agents into the engineering team, and vice versa.

## When called during a talk: update your transcript

If you invoke this skill from within a talk, **record the call in the `transcript_markdown` you will send at `/talk end`**. One line per action is enough, e.g.:

```
[during talk] submitted enrollment request for agent `content_writer` (pending founder approval).
```

The transcript is the only human-readable record of what happened in the conversation, and the daemon writes it at talk-end from whatever you provide. Skipping this step silently mutates the roster from the founder's point of view. The audit log (`opc audit --org {ORG_SLUG} <talk_id>`) captures the action too, but the transcript is what the founder reads back.

## What happens

- **enroll**: Creates a pending enrollment request. You may optionally specify `executor: "claude"` or `executor: "codex"`; if omitted, it defaults to `claude`. You may also include `"allow_rules": ["curl https://api.example.com", ...]` to grant additional Bash prefixes beyond the baseline `opc` grant — for example, to allow a specific external API call. The founder must run `opc approve-agent --org {ORG_SLUG} <name>` before the agent's workspace is bootstrapped and the agent becomes available for delegation.
- **update**: Updates the agent's description, system prompt, executor, or repos in the enrollment registry. If the system prompt or executor changes, the workspace bootstrap files are regenerated. Only works on approved agents.
- **terminate**: Marks the agent as terminated and deletes its workspace directory. Only works on approved agents.

## Agent naming

Agent names must be lowercase with underscores only (e.g. `content_writer`, `seo_agent`). No spaces, hyphens, or uppercase.

## Error handling

- If `opc` returns non-zero, retry once after 1 second.
- `409` (duplicate name on enroll, non-approved agent on update/terminate) and `404` (agent not found, talk not found) are not retryable.
- `422` usually means the payload mixed task and talk auth paths, or supplied neither — fix the JSON and retry.
