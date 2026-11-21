# manage-repo: Agent-driven repository management

## Current behavior

`happyranch manage-repo` adds, removes, or updates a repository in
`org/agents/<agent-id>.md` frontmatter (`AgentDef.repos`). The daemon refreshes
executor workspace metadata from a fresh canonical snapshot after filesystem
work. `agent.yaml` and a per-workspace git-pull hook are not used.

| Action | Effect | `url` required? |
| --- | --- | --- |
| `add` | Save the entry, clone into `repos/<name>/` or fast-forward-pull an existing clone | yes |
| `remove` | Check linked worktrees, delete the entry and the whole clone | no |
| `update` | Check linked worktrees, save the URL, delete the whole old clone and re-clone | yes |

Remove/update deletes task worktrees inside the clone and every local branch.
Before the canonical frontmatter write, existing clones are inspected with
`git worktree list --porcelain -z`, excluding the primary. Every linked worktree
must have empty `git status --porcelain --untracked-files=all --ignore-submodules=none` and
`git rev-list HEAD --not --remotes` results. Detached HEAD uses the same commit
reachability rule. Missing/prunable entries or any failed/timed-out Git check
refuse with HTTP 409, naming paths and reasons; config and clone stay untouched.
An absent clone directory needs no preservation check; an existing clone with
missing, unreadable or cyclic Git metadata refuses. Explicit status options
override local/global submodule-ignore and untracked-display configuration.
There is no force override. All probes share 30 seconds, each subprocess at most
5 seconds. Probes run outside the teams lock, inside read-only writer validation;
the writer is drained before cancellation can release it. This is a sampled
preflight, not a filesystem race fence against concurrent same-user edits.

Remote-tracking refs are the local preservation evidence; this check does not
fetch or prove the current remote state. The primary worktree and local branches
not checked out in a linked worktree are outside this veto. Push/finish work or
clean up linked worktrees before retrying deliberately.

## Daemon route

`POST /api/v1/orgs/{slug}/agents/{agent_name}/repos`

```json
{"action": "add", "repo_name": "web-app", "url": "https://github.com/t-benze/web-app.git"}
```

Repository keys fully match `[a-z0-9-]{1,32}` and are validated before workspace
lookup or any write. URL is required for add/update and ignored on remove.

Success is `200 {"ok": true}`. Errors use a JSON `detail` field:

| Condition | Status |
| --- | --- |
| Invalid repo key/request or missing URL on add/update | 422 |
| Workspace or agent missing | 404 |
| Remove/update of an unconfigured repo | 404 |
| Duplicate add | 409 |
| Unsafe/unverifiable linked worktree, or existing writer/admission conflict | 409 |
| Clone or pull fails after frontmatter save | 502 |

A nonzero `git pull --ff-only` exit returns `False` from `clone_repo`, with stderr
logged. A failed clone/pull marks the existing native disposition uncertain.
The frontmatter remains saved; workspace reconciliation finishes under
`_finish_consumer_write` before the HTTP 502 propagates. Its detail explicitly
says the frontmatter entry WAS saved but clone/pull failed. The CLI prints that
failure and exits 1. Existing init/enrollment/approval callers continue to mark
`False` uncertain; init additionally emits `repo_failed`.

After failed/ambiguous transport or a failure response, inspect canonical
frontmatter and `repos/<name>/.git` before acting again. Never automatically
retry the mutation. Re-add returns 409 because the entry is already saved.
After a 502 from a failed clone, recover from a bad URL as follows:

1. Check that the leftover `repos/<name>/` folder has no `.git` and contains
   nothing you need (normally it is empty).
2. From the agent workspace, remove only that leftover folder with
   `rmdir repos/<name>` (substitute the stored repo key). This refuses a
   non-empty folder; inspect and preserve its contents if it refuses.
3. Use a CLI/skill payload with `action` set to `update`, the same
   `agent` and `repo_name`, and the correct `url`, then run the single-line
   `happyranch manage-repo --org {ORG_SLUG} --from-file /absolute/workspace/output/<task_id>/manage-repo.json`.

While that folder exists without `.git`, update and remove return 409
`cannot verify linked worktrees`. Once the folder is gone, the worktree check
passes, update saves the corrected URL and the clone runs. Remove also works
after deleting the leftover folder, if the intent is to remove the saved entry;
use the same command form with a remove payload.

## CLI and skill

`agent` is the permanent agent ID. Agent sessions write
`output/<task_id>/manage-repo.json` in their workspace and invoke a single line:

```bash
happyranch manage-repo --org {ORG_SLUG} --from-file /absolute/workspace/output/<task_id>/manage-repo.json
```

Any absolute payload path works. The bundled source is
`runtime/skills/bundled/manage-repo/SKILL.md`. The `--from-file` rule preserves
single-line invocation across executor permission matchers.

Session spawn fast-forward-refreshes existing clones under a 30-second batch
budget. Agents warned of stale source fetch and read `origin/<branch>` explicitly
and never reset the primary checkout. Refresh observations, runner/unload/runtime
replacement fencing and shutdown lifetime details already live in
[the lifecycle contract](../agent-guides/contracts/runtime-lifecycle.md).
They are implementation context, not additional repo-mutation steps.

## Verification

`python -B -m unittest scripts.test_manage_repo -v` drives the actual handler via
in-process HTTP, real disposable Git repos and the CLI error path. It covers
both remove/update refusals before config writes (dirty/untracked, local-only
commits including detached HEAD, configured hidden submodule edits, missing
worktree/Git metadata, cyclic/dangling metadata and Git failures), clean pushed
worktrees, failed pull returns, and failed clone/pull HTTP/CLI results. Writer
and metadata bootstrap are isolated test dependencies; the real drained-write
helper and canonical file writes execute. No live daemon or retired units run.

## Out of scope

Batch operations, force deletion, remote fetch during preservation checks,
protection of primary-worktree changes or unused local branches, and changes to
enrollment/approval success responses.
