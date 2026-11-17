# manage-agent: EH-driven agent lifecycle management

## Summary

Give the Engineering Head the ability to enroll, update, and terminate agents via `opc manage-agent`. Enrollment requires founder approval; update and terminate are immediate. Remove the hardcoded `AgentName` enum — agent names become plain strings validated against the runtime's enrollment registry and workspace state.

## Operations

| Action      | Approval | Effect                                                        |
|-------------|----------|---------------------------------------------------------------|
| `enroll`    | Yes      | Save request as pending; founder approves to bootstrap workspace |
| `update`    | No       | Update system prompt or description of an existing agent       |
| `terminate` | No       | Mark agent as terminated, delete workspace                     |

## Data model

### New DB table: `agent_enrollments`

```sql
CREATE TABLE IF NOT EXISTS agent_enrollments (
    name        TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    system_prompt TEXT NOT NULL,
    repos       TEXT DEFAULT '{}',   -- JSON dict {name: url}
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | terminated
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
```

- `repos` is a JSON-encoded dict matching `agent.yaml` format.
- `status` lifecycle: `pending` → `approved` (founder) or `rejected` (founder). An approved agent can later be `terminated` (EH).
- The `name` field is the agent's identifier (e.g. `content_writer`, `seo_agent`). It must be a valid directory name (lowercase, underscores, no spaces).

### Preset support

`prompt_loader.py` continues to parse `protocol/02-system-prompts-managers.md` and `protocol/03-system-prompts-workers.md`. These serve as **presets** — the EH can adopt a preset system prompt verbatim or write a custom one. Presets are not a separate table; they're loaded from protocol docs on demand.

## Remove `AgentName` enum

The `AgentName` StrEnum in `src/models.py` is replaced with plain `str` throughout the codebase. This affects ~46 references across ~12 files:

- **`src/models.py`**: Delete the `AgentName` class. `TaskStep.agent` becomes `str`.
- **`src/cli.py`**: Remove `AgentName` import. `init-agent` no longer constrains choices to enum values.
- **`src/daemon/routes/agents.py`**: Agent listing and init iterate over approved enrollments (DB query) instead of `AgentName` enum. Validation uses the enrollments table, not `AgentName(body.agent)`.
- **`src/orchestrator/capabilities.py`**: `AGENT_DESCRIPTIONS` dict deleted. `build_capabilities_prompt` takes a `list[dict]` of active agents (name, description, tier) queried from the enrollment table.
- **`src/orchestrator/orchestrator.py`**: `AgentName.ENGINEERING_HEAD` becomes the string `"engineering_head"`. Delegation validates agent name against enrolled + approved agents.
- **`src/orchestrator/performance_tracker.py`**: `get_all_tiers` iterates over enrolled agents from the DB, not the enum.
- **Tests**: All `AgentName.X` references become string literals.

### Engineering Head as a special case

The EH is always present — it's the orchestrator itself, not an enrolled agent. The system must guarantee `"engineering_head"` exists even with an empty enrollments table. This is handled by:
- `init-agent` always bootstraps `engineering_head` using the protocol doc preset.
- The orchestrator hardcodes `"engineering_head"` as its own identity (string, not enum).
- The capabilities prompt never lists EH as a delegable agent.

## Daemon routes

### `POST /api/v1/agents/manage`

Request body:
```json
{
  "action": "enroll | update | terminate",
  "name": "content_writer",
  "description": "Writes destination guides and travel articles",
  "system_prompt": "You are the Content Writer...",
  "repos": {"web-content": "https://github.com/t-benze/web-content.git"}
}
```

- `name`, `description`, `system_prompt` required for `enroll`.
- `description` and/or `system_prompt` required for `update` (at least one).
- Only `name` required for `terminate`.
- `repos` optional for `enroll` and `update`.

**Behavior:**

- **enroll**: Validate name is a valid identifier (lowercase + underscores). Check not already enrolled. Insert into `agent_enrollments` with status `pending`. Return `{"ok": true, "status": "pending"}`.
- **update**: Validate agent exists and is `approved`. Update description/system_prompt/repos in DB. Regenerate CLAUDE.md in workspace with the new system prompt. Return `{"ok": true}`.
- **terminate**: Validate agent exists and is `approved`. Set status to `terminated`. Delete workspace directory (`shutil.rmtree`). Return `{"ok": true}`.

**Errors:**

| Condition                              | Status |
|----------------------------------------|--------|
| Unknown action                         | 422    |
| Enroll with name already enrolled      | 409    |
| Update/terminate nonexistent agent     | 404    |
| Update/terminate non-approved agent    | 409    |
| Missing required fields                | 422    |
| Invalid agent name (bad characters)    | 422    |

### `GET /api/v1/agents/enrollments`

Query params: `status` (optional, filter by status).

Returns:
```json
{
  "enrollments": [
    {"name": "content_writer", "description": "...", "status": "pending", "created_at": "..."}
  ]
}
```

### `POST /api/v1/agents/{name}/approve`

Validates agent exists with status `pending`. Sets status to `approved`. Bootstraps workspace:
1. Create workspace directory.
2. Write `agent.yaml` with repos from the enrollment record.
3. Clone repos.
4. Call `ensure_workspace_ready` with the system prompt from the enrollment record.

Returns `{"ok": true}`.

### `POST /api/v1/agents/{name}/reject`

Validates agent exists with status `pending`. Sets status to `rejected`. Returns `{"ok": true}`.

## CLI subcommands

### Agent-facing (EH uses these)

```
opc manage-agent enroll --from-file <path>
opc manage-agent update --from-file <path>
opc manage-agent terminate --from-file <path>
```

JSON file for enroll:
```json
{
  "action": "enroll",
  "agent": "content_writer",
  "description": "Writes destination guides and travel articles",
  "system_prompt": "You are the Content Writer...",
  "repos": {"web-content": "https://github.com/t-benze/web-content.git"}
}
```

### Founder-facing

```
opc enrollments [--status pending]     # list enrollment requests
opc approve-agent <name>               # approve and bootstrap
opc reject-agent <name>                # reject
```

## Capabilities prompt update

`build_capabilities_prompt` changes signature:

```python
def build_capabilities_prompt(
    brief: str,
    agents: list[dict],        # [{"name": "dev_agent", "description": "...", "tier": "green"}]
    step_number: int,
    max_steps: int,
    prior_steps: list[StepRecord] | None = None,
) -> str:
```

The caller (orchestrator) queries the DB for approved agents, fetches their tiers, and passes them in. The hardcoded `AGENT_DESCRIPTIONS` dict is deleted.

A new `manage-agent` action is added to the prompt so EH knows it can enroll/update/terminate agents:

```
**manage-agent** -- Enroll, update, or terminate an agent:
Use the manage-agent skill to write a JSON file and call `opc manage-agent --from-file <path>`.
Enrollment requires founder approval before the agent becomes active.
```

## Skill

`protocol/skills/manage-agent/SKILL.md` — instructions for the EH to write a JSON file and call `opc manage-agent --from-file /tmp/manage-agent-<unique>.json` as a single-line command. Covers all three actions with examples. Same `--from-file` pattern as `manage-repo` and `report-completion`.

## Tests

| File | What it covers |
|------|---------------|
| `tests/test_database.py` | CRUD for `agent_enrollments` table |
| `tests/daemon/test_routes_agents.py` | Route-level: enroll/update/terminate + approve/reject + errors |
| `tests/test_cli.py` | Parser + handler for manage-agent, enrollments, approve-agent, reject-agent |
| `tests/test_capabilities.py` | Updated for new signature (list of dicts instead of enum) |
| `tests/test_orchestrator.py` | String agent names, enrollment-based delegation validation |
| `tests/test_performance_tracker.py` | String agent names, DB-driven tier iteration |
| `tests/test_skills.py` | Parameterized frontmatter + CLI cross-reference for manage-agent |

## Out of scope

- Escalation-flow integration (approval is CLI-only for now)
- Agent role hierarchy or permissions beyond the existing `Bash(opc:*)` rule
- Migrating existing agents into the enrollments table (they continue to work via workspace detection)
