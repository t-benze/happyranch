---
name: manage-agent
description: Enroll, update, or terminate an agent. Write a JSON file and call happyranch manage-agent --from-file to keep the invocation single-line. Enrollment requires founder approval.
---

# manage-agent

## Purpose

Manage the agent roster: **enroll** a new worker (requires founder approval),
**update** an approved agent, or **terminate** a quiescent non-manager worker
(archives its definition and workspace). manage-agent cannot transfer teams or
demote managers; update keeps the agent's team and role.
Do not substitute termination/re-enrollment, direct roster writes or ambient
agent credentials. This prose grants no execution/permission change.

## Usage

Write a JSON file to `${TMPDIR:-/tmp}/manage-agent-<unique>.json` using the Write tool
(expand the variable to an absolute path first).
Use the `(task_id, session_id)` pair from your current task session.

**Task-path enroll:**
```json
{
  "action": "enroll",
  "name": "content_writer",
  "task_id": "<task_id>",
  "session_id": "<session_id>",
  "description": "Writes destination guides and travel articles",
  "system_prompt": "You are the Content Writer. Your responsibilities are...",
  "executor": "claude",
  "model": null,
  "reasoning_effort": "high",
  "repos": {"web-content": "https://github.com/t-benze/web-content.git"},
  "allow_rules": ["gh api /repos/{owner}/{repo}/contents"]
}
```

Enrollment creates a pending request. The founder must run
`happyranch approve-agent --org {ORG_SLUG} <name>` before the workspace is
bootstrapped and the agent becomes available for delegation.

Read the active roster before composing an update:
```bash
happyranch agents list --org ORG --json
```
Example output (one response row; other fields omitted here):
```json
[
  {
    "name": "content_writer",
    "team": "content",
    "role": "worker",
    "revision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "repos": {"web-content": "https://github.com/t-benze/web-content.git"}
  }
]
```

**Update an existing agent:**
```json
{
  "action": "update",
  "name": "content_writer",
  "task_id": "<task_id>",
  "session_id": "<session_id>",
  "expected_revision": "<revision from happyranch agents list --org ORG --json>",
  "description": "Updated description",
  "system_prompt": "Updated system prompt...",
  "executor": "claude",
  "model": null,
  "reasoning_effort": "high",
  "allow_rules": ["gh api /repos/{owner}/{repo}/contents", "curl https://api.example.com"]
}
```

For update, copy that target's exact current 64-character lowercase hex
`revision` into `expected_revision`. Missing, null or malformed values return
`422 expected_revision_required`; stale values return `409 stale_agent_revision`.
Read the roster again and deliberately reapply only the intended field change.
Never fetch a newer revision merely to bless an already-composed stale
whole-definition payload. The example's `allow_rules` must be the complete
intended list: the roster read does **not** show `allow_rules`. Confirm the
current list from authorized canonical content; if you cannot confirm it, do
not send `allow_rules` and escalate instead. For `repos`, copy and edit the
current roster row's dictionary.

**Terminate an agent:**
```json
{
  "action": "terminate",
  "name": "content_writer",
  "task_id": "<task_id>",
  "session_id": "<session_id>"
}
```

Invoke as a single-line command:
```bash
happyranch manage-agent --org {ORG_SLUG} --from-file "${TMPDIR:-/tmp}/manage-agent-<unique>.json"
```
The `--from-file` form is mandatory for agent sessions. In Claude sessions,
multi-line bash commands are rejected by the `Bash(happyranch:*)` permission rule
because newlines count as command separators. The read-only `agents list` verb
also runs under this baseline rule; it does not grant manage-agent authority.

## Field semantics

These tables describe the JSON payload, not the optional CLI flag shorthand.
`allow_rules` and `repos` updates replace the **whole value**, without merging:
omit or supply null to keep the current value, supply a value to replace it,
use `[]` to clear `allow_rules` and `{}` to clear `repos`.
Each `allow_rules` entry must be non-empty, have no leading/trailing whitespace,
and contain none of newline, CR, `;`, `|`, `&`, backtick, or the `$(` sequence.

### Enroll

| Field | Required? | Omitted / null | Supplied value |
| --- | --- | --- | --- |
| `action` | Yes | Invalid | `"enroll"` |
| `name` | Yes | Invalid | New permanent ID; see Naming |
| `task_id`, `session_id` | Both | CLI refuses before request | Current manager task/session pair |
| `description`, `system_prompt` | Both, non-empty | 422 | Initial description and prompt |
| `executor` | No | `claude` | Registered profile; empty string also defaults to `claude` |
| `model` | No | Executor CLI default | Valid model override; empty string stores null |
| `reasoning_effort` | No | No override | Valid effort string; empty string refused |
| `allow_rules` | No | Empty list | Complete extra Bash-prefix list; `[]` empty |
| `repos` | No | Empty dictionary | Repository name-to-URL dictionary; `{}` empty |
| `target_team` | No | Caller's team | Must equal caller's team; empty string uses caller's team |
| `expected_revision` | No | Unused | Unused on enroll |

### Update

| Field | Required? | Omitted / null | Supplied value |
| --- | --- | --- | --- |
| `action` | Yes | Invalid | `"update"` |
| `name` | Yes | Invalid | Approved target's permanent ID |
| `task_id`, `session_id` | Both | CLI refuses before request | Current manager task/session pair |
| `expected_revision` | Yes | 422 | Current canonical definition revision; see update example |
| `description`, `system_prompt` | No | Keep current | Replace string, including an empty string |
| `executor` | No | Keep current | Registered profile; see bootstrap and model rules below |
| `model` | No | Omitted: keep unless executor changes; null: clear | Valid override; empty string clears |
| `reasoning_effort` | No | Omitted: keep unless executor changes; null: clear | Valid effort string; empty string refused |
| `allow_rules` | No | Keep current | Whole-list replacement; `[]` clears; confirm complete list first |
| `repos` | No | Keep current | Whole-dictionary replacement; `{}` clears; copy/edit roster value |
| `target_team` | No | Unused | Ignored; update retains existing team and role |

Supplying a nonempty `system_prompt` or a non-null `executor` (even unchanged)
regenerates the bootstrap when the workspace exists. A real executor change
clears the old executor-specific model and effort when omitted; explicit
`model` / `reasoning_effort` values (or null) select (or clear) overrides.
An unchanged executor preserves omitted model/effort, as do unrelated writes.

Supported enrollment/update/release bootstrap paths regenerate the Shared Artifacts
instructions from `workspace_adapters.py::_shared_artifacts_section`; the
[Shared Artifacts contract](../../../../docs/agent-guides/contracts/shared-artifacts.md)
owns the persistent-file/task-output policy. Do not hand-edit generated workspace
instructions or materialized skills, or regenerate live workspaces merely for doc parity.

`executor` is a registered profile: built-ins `claude`, `codex`, `opencode`, `pi`,
or custom profiles whose explicit `command_adapter_id: custom-adapter:<id>`
resolves to a registered, conformance-passed, founder-approved adapter.
Built-in effort accepts case-preserving `[A-Za-z][A-Za-z0-9_-]{0,31}`, not static
list membership; custom values must belong to their approved finite declaration.
The proposed executor/model/effort pair uses shared token/composition validation,
not provider/account acceptance. OpenCode explicit effort requires nonempty
provider/model slash components, no whitespace/control/NUL, at most one `#` and
a nonempty byte-equal embedded variant. Pi retains its finite recognized suffix
set. Custom adapters require ordinary input-v2 opt-in; legacy input-v1 model:null
behavior remains. See `docs/specs/2026-10-11-agent-reasoning-effort.md` and
`docs/agent-guides/agent-executors-and-permissions.md`.

### Terminate

| Field | Required? | Omitted / null | Supplied value |
| --- | --- | --- | --- |
| `action` | Yes | Invalid | `"terminate"` |
| `name` | Yes | Invalid | Approved non-manager worker's permanent ID |
| `task_id`, `session_id` | Both | CLI refuses before request | Current manager task/session pair |
| `description`, `system_prompt`, `executor`, `model`, `reasoning_effort`, `allow_rules`, `repos`, `target_team`, `expected_revision` | No; omit | Unused | Schema validation still applies (including allow-rule constraints); not used by terminate |

Termination moves the active definition to `org/agents/_terminated/<name>.md`,
the workspace to `workspaces/_terminated/<name>/`, and removes the worker from
its team. Historic tasks, audit rows, token records, thread messages/participants,
schedules, wakes, dreams and archived files are preserved. The name cannot be
re-enrolled while its terminated record exists. Live work prevents termination:
non-terminal assigned tasks, started thread invocations, firing schedules,
running wakes/dreams, or pending/running jobs.

## Access

Only a **team manager's active task session** may enroll, update or terminate
agents within that manager's team. The daemon validates `(task_id, session_id)`;
other agents receive 403. Enrollment defaults to the caller's team; a different
`target_team` returns `403 cross_team_forbidden`. Update/terminate targets must
already belong to that team. Managers cannot terminate managers
(`409 manager_terminate_forbidden`). Recovery sessions are refused, as is a
missing teams registry; see Errors.

## Errors

| Exit / HTTP status | Cause and response |
| --- | --- |
| CLI exit 1, no request | Mandatory `--from-file` payload has missing or partial `task_id` / `session_id`; fix the pair. |
| 403 | No active team-manager session; `recovery_purpose_forbidden` for a manager recovery session; no teams registry (no active runtime); `cross_team_forbidden` for a different team. |
| 404 | Agent not found, including pending/non-approved agents on terminate, or on update after a syntactically valid revision. Missing/malformed update revision is 422 first. |
| 409 | `agent_name_unavailable` (active/pending/terminated name on enroll), `stale_agent_revision`, `manager_terminate_forbidden`, `agent_not_quiescent`, `archive_collision`, or profile-coordinator/naming conflicts (for example `profile_consumer_changed`, `profile_coordinator_unavailable`, `naming_unavailable`). Do not retry conflicts blindly. |
| 409 (org dependency) | `no_active_runtime` when the daemon has no active runtime; org dependency resolution precedes manager authentication. |
| 422 | Invalid permanent ID; enroll missing nonempty description/system_prompt; `invalid_executor`; allow-rule validation; `invalid_reasoning_effort` (invalid/unsupported/conflicting executor/model/effort); reserved headers in `system_prompt`; `expected_revision_required` for missing/null/malformed update revision; schema/type errors. Fix the payload. |

404/409 responses are not retryable; resolve the cause first. For other nonzero
returns, the existing retry limit is once after 1 second.

## Lasting permission grants (`allow_rules`)

`allow_rules` is the only supported lasting-grant channel for extra Bash prefixes
beyond the baseline `happyranch` grant. It uses the whole-value update semantics
above and is team-manager gated. A worker cannot re-grant itself: the request
routes through its team manager. A manager changing its own rules (or another
manager's) needs a **founder escalation**.

| Executor | Effect of an `allow_rules` update |
| --- | --- |
| **Claude** | `--allowedTools` is rebuilt from `allow_rules` on every launch, so the new rule is live on the agent's next session. Generated `.claude/settings.json` `permissions.allow` is not honoured in headless `-p` mode. |
| **Codex** | The effective surface is the CLI sandbox flag; `allow_rules` is not wired into it. A lasting Codex grant needs a concrete founder escalation to the actual permission-model surface. |
| **Pi** | `PiExecutor` has no HappyRanch-managed permission surface. A lasting Pi grant needs a concrete founder escalation, separate from Codex. |
| **opencode** | The effective surface is generated `opencode.json`. An `allow_rules`-only update does not regenerate it. When the workspace exists, resending the current nonempty `system_prompt` with the complete `allow_rules` update forces bootstrap regeneration; supplying `executor` also forces it, even unchanged. `happyranch init-agent` is the separate bootstrap option. |

Request the narrowest prefix plus the skill step that needs it and why a
thread-authorized one-off job is insufficient. Authorization of one command
never expands ongoing permissions; no self-edit or new grant endpoint exists.
For a one-off blocked operation, obtain required authorization in a thread
BEFORE submitting an automatic **job** from an ordinary task session (see the
**jobs** skill). Omit/set false for review_required; true is refused, never queued.

## Founder-only retirement

Manager-session `manage-agent terminate` is unchanged: only quiescent workers
in the caller's team; managers still return `manager_terminate_forbidden`.
Founder can instead use `happyranch agents retire <name> --org ORG`, backed by
`POST /api/v1/orgs/{slug}/agents/{agent_name}/retire` and the existing Founder
bearer. This grants no agent-session retirement authority or permission to use
ambient credentials. Both paths preserve the AgentDef/workspace in `_terminated`
and retain memory/history, with the same future-work cleanup and rollback.

Founder may also retire a quiescent manager together with its empty team.
All workers, including pending enrollments, must be resolved first
(`team_not_empty`). A display/task routing team cannot retire
(`default_team_retire_forbidden`, `task_default_team_retire_forbidden`), nor a
team with pending/in-progress/escalated tasks (`team_has_open_tasks`).
Human-managed teams have no agent manager and are outside this operation.
Other teams, routing pointers and historical team-keyed policy rows stay intact.

## Naming

`name` is the permanent ID used by frontmatter, teams, workspaces and callbacks.
It must match `^[a-z][a-z0-9_]*$` (for example `content_writer`, `seo_agent2`)
and pass the org's shared case-insensitive ASCII naming namespace. Editable
labels follow different rules: 1–64 ASCII letters/digits/underscore/hyphen,
letter/digit first, without trimming. Never use a label as manage-agent's `name`
or as its canonical definition revision.
Operator `identities rename` is separate, requires positive
`expected_name_revision` CAS with uncertain-failure readback, and grants no
agent-session rename authority. The additive `addressable_name`, `name_revision`
and `naming_status` roster fields do not change `name` or `revision` semantics.
For the generic current Name / permanent ID and callback identity rules, see
[start-task](../start-task/SKILL.md#current-names-and-permanent-automation-ids).
