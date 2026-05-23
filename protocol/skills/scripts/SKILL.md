---
name: scripts
description: Submit a script the founder will run for you when you hit a permission wall.
---

# scripts

You hit a permission wall and need a command run that your sandbox can't run itself. Submit a script for the founder to review and execute.

## When to use

Typical signals:

- Your `--allowedTools` (Claude) or `permission.bash` (opencode) denies a `gh`, `aws`, `stripe`, `ssh`, or `sudo` invocation — and the operation genuinely needs founder-grade credentials.
- A binary you need is not in any of your `allow_rules` prefixes.
- An operation requires environment / credentials that only the founder's shell has.

Do NOT use this skill for anything you could just do in your own workspace (e.g., `chmod +x` and run a local helper). Submitting a script to the founder is a one-shot blocking interaction; use it when there is no other way.

## How to submit

1. **Write a JSON payload** to `/tmp/script-<random>.json` using the Write tool:

   ```json
   {
     "task_id": "TASK-091",
     "session_id": "<your active session_id>",
     "title": "Close PR #247 with approval comment",
     "rationale": "PR review is complete. My allow_rules cover `gh pr comment` but not `gh pr close`. Need founder to merge-close so the auth-rewrite branch can be deleted.",
     "script": "set -euo pipefail\ngh pr close 247 --comment 'Approved and closed per review thread THR-014.'\n",
     "interpreter": "bash",
     "cwd_hint": "repos/web-app"
   }
   ```

   **Required fields:** `task_id`, `session_id`, `title`, `rationale`, `script`, `interpreter`

   **Optional fields:** `cwd_hint` (relative path under your workspace; absent = workspace root)

   **Allowed `interpreter` values:** `bash`, `sh`, `zsh`, `python3`

2. **Invoke as a single-line command:**

   ```bash
   grassland scripts submit --from-file /tmp/script-<random>.json --org <slug>
   ```

   The `--from-file` form is mandatory in agent sessions. Multi-line bash is rejected by the `Bash(grassland:*)` permission rule because newlines count as command separators.

3. **Keep the submission ID.** Output is `ok: submitted SR-NNN ...`. Keep the `SR-NNN` id — reference it in your completion report.

## After submitting: self-block

Always self-block your task immediately after submit. Report completion with `status="blocked"`, summary referencing the SR-NNN:

```json
{
  "task_id": "TASK-091",
  "status": "blocked",
  "summary": "Awaiting SR-019 (Close PR #247 with approval comment). Cannot proceed until founder runs and confirms output."
}
```

The orchestrator's manager will see the block and escalate to the founder. Once the founder has run the script and reviewed the output, they will use `grassland revisit <task-id>` to spawn a fresh root with your SR's output available in context. You do NOT need to poll for the output yourself — it will arrive in your next revisited task.

## If the founder rejects

The reject reason will be visible in the SR's audit trail. The founder may revisit the task with a different brief, or you may need to re-submit a corrected script if asked.

## Error handling

- `422 empty_<field>`: required field was missing or whitespace-only. Check your payload and resubmit.
- `400 unknown_interpreter`: `interpreter` was not one of the allowed values.
- `404 not_found`: the `task_id` or `session_id` doesn't exist. Verify you extracted the correct values from your prompt.
- `409 session_mismatch`: the daemon has spawned a newer session for this `(task_id, agent)`. Exit immediately — the task lineage has been reset.

If `grassland` returns non-zero and the error is not in the list above, retry once after 1 second.
