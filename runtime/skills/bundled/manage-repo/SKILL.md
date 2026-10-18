---
name: manage-repo
description: Add, remove, or update a repository in your AgentDef configuration (org/agents/<name>.md frontmatter). Write a JSON file and call happyranch manage-repo --from-file to keep the invocation single-line.
---

## Current names and permanent automation IDs

Fresh prompts may show current Name · ID for this turn. Use the permanent ID
from the injected parameters for callback agent/composer/speaker, task/session/
invocation proofs, configuration and delegate/then/fanout targets. A name is
human-addressing/presentation data, never actor authority. Human destination
arguments and @mentions accept current names or IDs; former names refuse with
the current name and do not forward. Founder is a separate human; recipient
transport remains `@founder`. Metadata failure gives ID-only context.

The manage-repo callback agent remains the permanent ID, and repository keys/URLs/configuration stay unchanged. Operator name resolution never rewrites this payload.


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
   happyranch manage-repo --org {ORG_SLUG} --from-file /tmp/manage-repo-<unique>.json
   ```

   The `--from-file` form is mandatory for agent sessions. In Claude sessions,
   multi-line bash commands are rejected by the `Bash(happyranch:*)` permission rule
   because newlines count as command separators.

## What happens

- **add**: writes the entry to your `org/agents/<name>.md` frontmatter (`AgentDef.repos`), clones the repo into `repos/<name>/` (or `git pull --ff-only` if a clone already exists), and refreshes executor workspace metadata. Repo freshness is daemon-side (THR-103); no per-workspace git-pull hook is emitted.
- **remove**: deletes the entry from your `org/agents/<name>.md` frontmatter, removes the `repos/<name>/` directory, and refreshes executor workspace metadata.
- **update**: updates the URL in your `org/agents/<name>.md` frontmatter, deletes the old clone, re-clones from the new URL, and refreshes executor workspace metadata.

Session-spawn `refresh_workspace_repos` attempts refresh under one 30-second
batch deadline. Detached HEAD or missing/unusable/unreadable upstream refuses
before pull; refused/unattempted repos stay false and the prompt warns of stale
source. Every executed pull failure, including dirty/non-FF/partial recursive
effects, retains native uncertainty; upstream lookup or equal parent summaries
never certifies no effects. Async thread/dream/wake/Todo paths await a joined
offload inside their actual preparation-effects scope before cancellation and
settlement; prior unknowns stay sticky. The enclosing runner retains its org
lifetime through native finish, including failure. Unload refuses active runners
and fences late starts on detached instances. Runtime registration (including
the same root) and /runtime/use preflight all old orgs for task/runner/team-move
holds before any close or registration/activation effect, returning409 if busy.
Shutdown refuses replacement before construction. Idle replacement closes and
fences all retired instances before publishing the new org map; a busy later org
leaves earlier orgs open. Shutdown stops runner/new-org
attachment admission and joins actual runners before DB close. Cancellation or
timeout never establishes process quiescence. Runtime/race/recovery verification
remains UNVERIFIED.

## Error handling

- If `happyranch` returns non-zero, retry once after 1 second.
- `409` (duplicate repo on add) and `404` (repo not found on remove/update) are not retryable — check your `org/agents/<name>.md` frontmatter and adjust.
