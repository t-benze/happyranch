---
name: manage-agent
description: Enroll, update, or terminate an agent. Write a JSON file and call happyranch manage-agent --from-file to keep the invocation single-line. Enrollment requires founder approval.
---

# manage-agent

For an `update`, first obtain the target's current `revision` from the active
agent roster and include that exact value as `expected_revision` in the
callback payload. A missing, null, malformed, or stale revision is rejected;
reload and deliberately reapply only the intended field change rather than
resubmitting an older whole definition.

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
     "expected_revision": "<revision from this agent's GET /agents row>",
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

- **enroll**: Creates a pending enrollment request. `executor` is a registered executor profile name; if omitted, it defaults to `claude`. Supported profiles are the built-ins (`claude`, `codex`, `opencode`, `pi`) and custom profiles whose explicit `command_adapter_id: custom-adapter:<id>` resolves to a registered, conformance-passed, founder-approved adapter (see `docs/agent-guides/agent-executors-and-permissions.md`). You may also include `"allow_rules": ["curl https://api.example.com", ...]` to grant additional Bash prefixes beyond the baseline `happyranch` grant — for example, to allow a specific external API call. The founder must run `happyranch approve-agent --org {ORG_SLUG} <name>` before the agent's workspace is bootstrapped and the agent becomes available for delegation.
- **update**: Updates the agent's description, system prompt, executor, model, or repos in the enrollment registry. If the system prompt or executor changes, the workspace bootstrap files are regenerated. A real executor change clears the old executor-specific model when `model` is omitted; include an explicit `model` value (or `null`) to choose the new executor's model (or its CLI default). An unchanged executor preserves an omitted model. Only works on approved agents.
- **terminate**: Archives a quiescent, approved non-manager worker. The active agent file is moved to `org/agents/_terminated/<name>.md`, the workspace is moved to `workspaces/_terminated/<name>/`, and the worker is removed from its team. Historic tasks, audit rows, token records, thread messages/participants, schedules, wakes, dreams, and archived files are preserved. The agent name cannot be re-enrolled while the terminated record exists. Termination is refused when the worker has live work (non-terminal assigned tasks, started thread invocations, firing schedules, running wakes/dreams, or pending/running jobs) or when the target is a manager.

## Lasting permission grants (`allow_rules`)

`allow_rules` is the only supported lasting-grant channel: an `update` payload
may carry an `allow_rules` array that adds Bash prefixes beyond the baseline
`happyranch` grant. This skill is team-manager gated — the daemon accepts it only
from a manager's active task session, and the target must be in that manager's
team (`403 cross_team_forbidden`). A worker cannot re-grant itself: the request
routes through its team manager. A manager changing its own rules (or another
manager's) needs a **founder escalation**. `expected_revision` must be a fresh
64-hex revision (`422 expected_revision_required`); a stale value is rejected
(`409 stale_agent_revision`).

An `allow_rules` update is not a live permission change on every executor:

| Executor | Effect of an `allow_rules` update |
| --- | --- |
| **Claude** | `--allowedTools` is rebuilt from `allow_rules` on every launch, so the new rule is live on the agent's next session. (The generated `.claude/settings.json` `permissions.allow` list is not honoured in headless `-p` mode.) |
| **Codex** | The effective surface is the CLI sandbox flag; `allow_rules` is not wired into it. A lasting Codex grant needs a concrete founder escalation to the actual permission-model surface. |
| **Pi** | `PiExecutor` has no HappyRanch-managed permission surface. A lasting Pi grant likewise needs a concrete founder escalation — separate from Codex. |
| **opencode** | The effective surface is the generated `opencode.json`. An **`allow_rules`-only update does not re-run the workspace bootstrap** (regeneration is gated on a system-prompt or executor change), so the grant is inert until a bootstrap-forcing change or `happyranch init-agent`. This is an open limitation, not repaired here. |

Request the narrowest prefix plus the skill step that needs it and why a
reviewed one-off job is insufficient. Approval of one job never expands ongoing
permissions; no self-edit or new grant endpoint exists. A one-off blocked
operation is a reviewed **job** (see the **jobs** skill), not an `allow_rules`
change.

## Agent naming

Agent names must be lowercase with underscores only (e.g. `content_writer`, `seo_agent`). No spaces, hyphens, or uppercase.

## Error handling

- If `happyranch` returns non-zero, retry once after 1 second.
- `409` (duplicate/terminated name on enroll, non-approved agent on update/terminate, manager target, archive collision, or `agent_not_quiescent` conflicts) and `404` (agent not found) are not retryable.
- `422 expected_revision_required` means an update omitted, supplied `null`,
  or malformed its 64-character revision — read the active roster again and
  compose the update from that row's matching canonical content and revision.
- `409 stale_agent_revision` means another accepted update won. Read the
  active roster again, deliberately reapply only the intended change to its
  current content, and send that row's revision. Never fetch a newer revision
  merely to bless an already-composed stale whole-definition payload.
- Other `422` responses usually mean the payload is missing required auth
  fields (task_id + session_id) — fix the JSON and retry.
