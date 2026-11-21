---
name: manage-repo
description: Add, remove, or update a repository in your AgentDef configuration (org/agents/<name>.md frontmatter). Write a JSON file and call happyranch manage-repo --from-file to keep the invocation single-line.
---

# manage-repo

`agent` is your permanent agent ID.

Manage the repositories cloned into your `repos/` directory. You can **add** a new repo, **remove** an existing one, or **update** the URL of an existing repo (which deletes the old clone and re-clones from the new URL).

Repository keys must fully match `[a-z0-9-]{1,32}` (ASCII lowercase letters, digits and hyphens). Invalid keys, including `Foo`, return HTTP 422 before any workspace lookup, configuration write, clone or pull. Use the stored lowercase key; names are never silently normalized.

## Usage

1. **Write a JSON file** to `output/<task_id>/manage-repo.json` in your workspace (any absolute path works) using the Write tool:

   **Add a repo:**
   ```json
   {
     "action": "add",
     "agent": "<your_agent_id>",
     "repo_name": "web-app",
     "url": "https://github.com/t-benze/web-app.git"
   }
   ```

   **Remove a repo:**
   ```json
   {
     "action": "remove",
     "agent": "<your_agent_id>",
     "repo_name": "web-app"
   }
   ```

   **Update a repo URL:**
   ```json
   {
     "action": "update",
     "agent": "<your_agent_id>",
     "repo_name": "web-app",
     "url": "https://github.com/t-benze/new-web-app.git"
   }
   ```

2. **Invoke as a single-line command:**

   ```bash
   happyranch manage-repo --org {ORG_SLUG} --from-file /absolute/workspace/output/<task_id>/manage-repo.json
   ```

   The `--from-file` form is mandatory for agent sessions. In Claude sessions,
   multi-line bash commands are rejected by the `Bash(happyranch:*)` permission rule
   because newlines count as command separators.

## What happens

- **add**: writes the entry to your `org/agents/<name>.md` frontmatter (`AgentDef.repos`), clones the repo into `repos/<name>/` (or `git pull --ff-only` if a clone already exists), and refreshes executor workspace metadata.
- **remove**: deletes the entry from your `org/agents/<name>.md` frontmatter, removes the `repos/<name>/` directory, and refreshes executor workspace metadata.
- **update**: updates the URL in your `org/agents/<name>.md` frontmatter, deletes the old clone, re-clones from the new URL, and refreshes executor workspace metadata.

**Warning:** remove/update deletes the whole clone, including task worktrees
inside it and all local branches. The daemon refuses before changing the
frontmatter while any linked worktree is dirty or has commits not reachable from
any remote-tracking ref. Push/finish or clean up worktrees first. Missing/prunable
worktrees, missing/unreadable/cyclic Git metadata in an existing clone, and
failed/timed-out Git checks also refuse; no force override exists. Submodule
edits count as dirty even when local/global Git configuration hides them.

A failed clone/pull on add/update returns HTTP 502 after saving the frontmatter
entry and refreshing workspace metadata. Inspect the saved entry and clone before
acting again; fix a bad URL with **update**. Repeating **add** returns 409 because
the entry already exists.

Session spawn fast-forward-refreshes existing clones under a 30-second batch
budget. If the prompt warns a repo is stale, fetch and read `origin/<branch>`
explicitly; never reset the primary checkout.

## Error handling

- `422`: invalid repo key, invalid request fields, or missing URL on add/update.
- `404`: workspace not found, agent not found, or repo not found on remove/update.
- `409`: duplicate repo on add, or remove/update refused by the linked-worktree
  safety check. The refusal lists offending paths and reasons; frontmatter and
  clone remain unchanged. Existing writer/admission conflicts can also return 409.
- `502`: the frontmatter entry WAS saved but clone/pull failed. The CLI displays
  the failure and exits non-zero; this is not a successful repository change.

Do not automatically retry this mutation on transport errors, 5xx, or other
failures. After an ambiguous or failed result, inspect canonical state in your
`org/agents/<name>.md` frontmatter and `repos/<name>/.git` before acting again.
