---
name: manage-repo
description: Add, remove, or update a repository in your agent.yaml configuration. Write a JSON file and call opc manage-repo --from-file to keep the invocation single-line.
---

# manage-repo

Manage the repositories cloned into your `repos/` directory. You can **add** a new repo, **remove** an existing one, or **update** the URL of an existing repo (which deletes the old clone and re-clones from the new URL).

## Usage

1. **Write a JSON file** to `/tmp/manage-repo-<unique>.json` using the Write tool:

   **Add a repo:**
   ```json
   {
     "action": "add",
     "agent": "<your_agent_name>",
     "repo_name": "web-app",
     "url": "https://github.com/t-benze/web-app.git"
   }
   ```

   **Remove a repo:**
   ```json
   {
     "action": "remove",
     "agent": "<your_agent_name>",
     "repo_name": "web-app"
   }
   ```

   **Update a repo URL:**
   ```json
   {
     "action": "update",
     "agent": "<your_agent_name>",
     "repo_name": "web-app",
     "url": "https://github.com/t-benze/new-web-app.git"
   }
   ```

2. **Invoke as a single-line command:**

   ```bash
   opc manage-repo --from-file /tmp/manage-repo-<unique>.json
   ```

   The `--from-file` form is mandatory for agent sessions. Multi-line bash
   commands are rejected by the `Bash(opc:*)` permission rule (newlines count
   as command separators).

## What happens

- **add**: writes the entry to `agent.yaml`, clones the repo into `repos/<name>/`, and regenerates `.claude/settings.json` so the PreToolUse git-pull hook covers the new repo.
- **remove**: deletes the entry from `agent.yaml`, removes the `repos/<name>/` directory, and regenerates `.claude/settings.json`.
- **update**: updates the URL in `agent.yaml`, deletes the old clone, re-clones from the new URL, and regenerates `.claude/settings.json`.

## Error handling

- If `opc` returns non-zero, retry once after 1 second.
- `409` (duplicate repo on add) and `404` (repo not found on remove/update) are not retryable — check your `agent.yaml` and adjust.
