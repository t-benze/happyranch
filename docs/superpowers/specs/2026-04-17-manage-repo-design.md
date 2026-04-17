# manage-repo: Agent-driven repository management

## Summary

Add a `manage-repo` capability that lets agents add, remove, or update repos in their own `agent.yaml` via a single `opc` CLI subcommand backed by a daemon route. All three operations end by regenerating `settings.json` so the PreToolUse git-pull hook stays in sync.

## Operations

| Action   | Effect                                                                 | `url` required? |
|----------|------------------------------------------------------------------------|-----------------|
| `add`    | Write entry to agent.yaml, clone repo, regenerate settings.json        | yes             |
| `remove` | Delete entry from agent.yaml, `shutil.rmtree` clone dir, regenerate   | no              |
| `update` | Update URL in agent.yaml, delete old clone, re-clone, regenerate       | yes             |

## Daemon route

`POST /api/v1/agents/{agent_name}/repos`

### Request body

```json
{
  "action": "add | remove | update",
  "repo_name": "web-app",
  "url": "https://github.com/t-benze/web-app.git"
}
```

- `action`: `add`, `remove`, or `update` (validated via StrEnum)
- `repo_name`: key in the `repos:` dict of `agent.yaml`
- `url`: required for `add` and `update`; ignored for `remove`

### Response

Success: `200 {"ok": true}`

Errors (all return JSON `{"detail": "..."}` body):

| Condition                        | Status |
|----------------------------------|--------|
| Unknown agent (workspace missing)| 404    |
| `add` with duplicate repo_name  | 409    |
| `remove`/`update` nonexistent   | 404    |
| `add`/`update` missing url      | 422    |

### Internal flow

1. Validate workspace exists.
2. Call the appropriate `agent_config` helper (see below).
3. Clone or delete the repo directory under `workspace/repos/`.
4. Call `ContextBuilder.ensure_workspace_ready` to regenerate `settings.json` (the PreToolUse git-pull hook is rebuilt from detected cloned repos).
5. Return result.

## agent_config.py helpers

Three new functions alongside the existing `load_agent_config` / `write_default_agent_config`:

```python
def add_repo(workspace: Path, name: str, url: str) -> None:
    """Add a repo entry. Raises ValueError if name already exists."""

def remove_repo(workspace: Path, name: str) -> None:
    """Remove a repo entry. Raises KeyError if name not found."""

def update_repo_url(workspace: Path, name: str, url: str) -> None:
    """Change the URL for an existing repo. Raises KeyError if not found."""
```

Each function loads `agent.yaml`, mutates the `repos` dict, and writes it back. The route handler is responsible for the filesystem operations (clone/delete) and regenerating workspace files.

## CLI subcommand

```
opc manage-repo <action> --agent <name> --repo-name <name> [--url <url>]
opc manage-repo --from-file <path>
```

- Positional `action`: `add`, `remove`, `update`
- `--from-file`: JSON file with `action`, `agent`, `repo_name`, `url` keys — for agent invocations (single-line `opc` call, avoids multi-line bash permission issues)
- Posts to `POST /api/v1/agents/{agent_name}/repos`

## Skill

`protocol/skills/manage-repo/SKILL.md`

Instructions for the agent:

1. Write a JSON file to `/tmp/manage-repo-<unique>.json`:
   ```json
   {"action": "add", "agent": "dev_agent", "repo_name": "docs", "url": "https://github.com/t-benze/docs.git"}
   ```
2. Invoke: `opc manage-repo --from-file /tmp/manage-repo-<unique>.json`

The skill covers all three actions with examples. It follows the same `--from-file` pattern as `report-completion`.

## Tests

| File                                | What it covers                                             |
|-------------------------------------|------------------------------------------------------------|
| `tests/test_agent_config.py` (new)  | `add_repo`, `remove_repo`, `update_repo_url` + error cases |
| `tests/daemon/test_routes_agents.py`| Route-level: all 3 actions, error responses, settings regen |
| `tests/test_cli.py`                 | Parser accepts action + flags, `--from-file` loads JSON    |
| `tests/test_skills.py`              | Automatically covers frontmatter + `opc` cross-reference   |

## Out of scope

- EH-led agent enrollment (separate future work)
- Listing repos (agents can read their own `agent.yaml` directly)
- Batch operations (one repo per call)
