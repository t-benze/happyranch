---
name: jobs
description: Run a script in the background or request founder review; manage the result.
---

# jobs

You want to run a script that either takes longer than your session can wait for, doesn't return at all (a dev server, a watcher), or needs permissions you don't have. Submit a job, fill in the form, and the framework handles the rest.

## When to use

Three signals you should reach for jobs instead of running the command inline:

1. **The command doesn't return.** Dev servers, log watchers, polling loops, things you want running while you do other work.
2. **The command takes too long.** A build, a backup, a long migration that would consume the rest of your session.
3. **The command needs permissions you don't have.** A `gh`, `aws`, `stripe`, `ssh`, or `sudo` invocation your `allow_rules` block — submit with `review_required=true` and the founder will run it for you.

Do NOT use jobs for one-shot, fast, in-sandbox commands. Run those inline — `bash` is still the right tool. Jobs add audit overhead and only pay off for the three signals above.

## The form

You fill in a JSON payload with these fields:

```json
{
  "task_id": "TASK-091",
  "session_id": "<your active session_id>",
  "title": "Run dev server for browser testing",
  "script": "npm run dev\n",
  "interpreter": "bash",
  "cwd_hint": "repos/web-app",
  "rationale": "Need live server to verify component renders.",
  "review_required": false,
  "persistent": true,
  "max_runtime_seconds": null
}
```

**Required:** auth binding + `title` + `script` + `interpreter`.
**Auth binding (exactly one):**
- **Task path** — `task_id` + `session_id` from your active task session (the shape above).
- **Optional:** `cwd_hint`, `rationale`, `max_runtime_seconds`, `max_output_bytes`.

### The two policy flags

**`review_required`** — set to `true` when:

- Your script uses credentials your agent doesn't have (`aws`, `stripe`, `ssh`, `sudo`).
- The leading binary of any line isn't in your `allow_rules`.
- The script mutates external state in ways you couldn't roll back (`gh pr close`, `git push --force`, anything destructive).
- You're uncertain about whether the founder would want to review it. **When in doubt, request review.**

`rationale` is required when `review_required=true` — the founder needs context to approve.

**`persistent`** — set to `true` when:

- Your script doesn't return on its own (`npm run dev`, `tail -f`, polling loops).
- You expect to check on it across multiple bash calls in this session.

If `persistent=false`, the job has a default 300-second timeout. Override with `max_runtime_seconds` if you need longer.

If `persistent=true`, the job has no timeout by default. It runs until you stop it, the task transitions terminal, or the daemon shuts down. You can still set `max_runtime_seconds` as a safety cap.

The two flags are independent. All four combinations are valid:

| review_required | persistent | meaning |
|--|--|--|
| false | false | Auto-run, one-shot — fire and forget a backup, then check `wait`. |
| false | true | Auto-run, long-running — dev server, watcher. |
| true | false | Founder reviews and runs a bounded command — the old "script request" flow. |
| true | true | Founder reviews and runs a long-running process — rare; use when oversight matters more than speed. |

## How to submit

1. Write the payload to `/tmp/job-<random>.json` with the Write tool.
2. Submit as a single line (`--from-file` is mandatory; multi-line bash is rejected by the permission matcher):

   ```bash
   happyranch jobs submit --from-file /tmp/job-<random>.json --org <slug>
   ```

3. Output is `ok: submitted JOB-NNN ...`. Keep the JOB-NNN id.

## After submitting — waiting on jobs

When you need to wait for jobs to finish before proceeding (either
`review_required=true` waiting for founder approval, or `review_required=false`
jobs you can't move forward without), submit your block via `report-completion`
with `status=blocked` and `waiting_on_job_ids` populated:

```json
{
  "status": "blocked",
  "confidence": 0,
  "output_summary": "Waiting for JOB-12 and JOB-13 before I can verify the migration ran cleanly.",
  "waiting_on_job_ids": ["JOB-12", "JOB-13"]
}
```

The system resumes your task automatically once **every** listed job reaches a
terminal state (`completed`, `failed`, or `rejected`). When you resume, your
bootstrap doc will include a `BLOCKED-JOBS-RESULTS` section listing each job's
status and `happyranch jobs show JOB-NNN` / `happyranch jobs output JOB-NNN`
commands to fetch full output. **You don't poll.**

If you need to stay in-session for a fast `review_required=false` job, the
existing `happyranch jobs wait JOB-NNN --timeout-seconds 30` pattern still works.
Prefer block-and-resume for any wait long enough to risk session timeout.

## Cleanup

Before reporting your task complete, stop any of your own jobs you no longer need. Persistent jobs you forget about will be auto-killed when your task transitions terminal, but explicit cleanup makes the audit log cleaner and avoids ambiguous "did the agent forget about this?" questions.

## Error handling

- `422 empty_<field>` — required field missing or whitespace-only. Check and resubmit.
- `422` from validator — auth binding malformed (e.g., supplied both `task_id+session_id` AND `task_id`, or supplied `task_id` without `session_id`).
- `400 unknown_interpreter` — `interpreter` not in the allowed set.
- `400 rationale_required` — submitted `review_required=true` without a `rationale`.
- `400 script_too_large` — script body exceeded 64 KB.
- `404 not_found` / `404 unknown_task` — referenced id doesn't exist.
- `409 session_mismatch` — daemon spawned a newer session for this `(task_id, agent)`. Exit immediately.

Retry once after 1 second on any non-listed error.
