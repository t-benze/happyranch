---
name: jobs
description: Run a script in the background or request founder review; manage the result.
---

# jobs

## THR-247 recovery-turn exception

A server-designated completion-recovery binding must not submit a new job. It
may only observe an already-owned task job, including its actual wait result,
for the recovery report. A normal resumed turn is not a recovery turn and uses
the ordinary rules below.

You want to run a script that either takes longer than your session can wait for, doesn't return at all (a dev server, a watcher), or needs permissions you don't have. Submit a job, fill in the form, and the framework handles the rest.

## When to use

Three signals you should reach for jobs instead of running the command inline:

1. **The command doesn't return.** Dev servers, log watchers, polling loops, things you want running while you do other work.
2. **The command takes too long.** A build, a backup, a long migration that would consume the rest of your session.
3. **The command needs permissions you don't have.** A `gh`, `aws`, `stripe`, `ssh`, or `sudo` invocation your `allow_rules` block — submit with `review_required=true` and the founder will run it for you.

Do NOT use jobs for one-shot, fast, in-sandbox commands. Run those inline — `bash` is still the right tool. Jobs add audit overhead and only pay off for the three signals above.

### Mandatory durable verification boundary

Any verification command expected to exceed one minute or the remaining safe
interactive-session window MUST run as a durable job with exactly
`"review_required": false` and `"persistent": true`. This includes:

- `scripts/local_ci.sh all`;
- canonical/full test suites (including equivalent full Python, web, or
  integration verification); and
- `git push` when its hooks invoke any such verification.

After submission, report `status="blocked"` with the job id in
`waiting_on_job_ids`; do not poll or continue publication in the same session.
On the resumed task, inspect the terminal receipt with `happyranch jobs show`
and `happyranch jobs output`, verify the exact command, effective runtime
path/version, and exit code 0, then continue. A submission id or a terminal
status without that receipt is not passing evidence.

Short/focused tests that are expected to finish inside one minute remain
appropriate to run directly in-session. If duration is uncertain, use the
durable job boundary.

## Classify the failure before requesting a job

A skill step can fail for several different reasons. Only one of them is a
permission refusal, and only that one is fixed by asking for a reviewed job.
Classify first:

| Observed failure | Signal | Permission refusal? | Correct action |
| --- | --- | --- | --- |
| **Executor permission refusal** | The command is refused/denied by the executor's permission or sandbox decision (for example an unlisted leading binary under `allow_rules`, or a sandbox denial) — not a missing program | **Yes** | Record redacted evidence; submit one reviewed bounded job (below) |
| Binary not found | Shell "command not found" / exit 127 | No | Install or use an available tool; do not request permissions |
| Authentication / credential | The program runs and returns 401/403, an auth prompt, or "no credentials" | No | Route through the existing credential/operator workflow; do not request shell permissions |
| Network / service unreachable | DNS/timeout/connection refused from a runnable program | No | Report the dependency; do not request permissions |
| Denied product policy | A HappyRanch policy gate rejects the operation | No | Escalate the policy decision; do not request permissions |
| Ordinary command bug | Non-zero exit with a normal program error | No | Fix the command or its inputs |

Record the minimum redacted evidence: the skill id/version; the exact attempted
command; the cwd or resource scope; the executor's refusal text (redact secrets,
tokens, credentials and private paths); why the step needs it; and the narrowest
scope that would unblock it. Pause that one operation. Do not evade the decision
with an executor switch, a skill-level grant, wildcard expansion, gate
suppression, or an unrelated credential request.

## Request one blocked operation (reviewed, bounded, task-bound job)

For a one-off or rare blocked operation, submit a reviewed job from a **normal
active task session** (`task_id` + `session_id` from that session). Write the
payload and submit it on a single line. `cwd_hint` is a **relative** path: the
daemon resolves it under the workspace root and passes it to the runner as the
process working directory, so the script must **not** `cd` again.

```json
{
  "task_id": "<active TASK id>",
  "session_id": "<active session id>",
  "title": "Run one blocked deployment command for skill X",
  "script": "./deploy.sh --check\n",
  "interpreter": "bash",
  "cwd_hint": "repos/<repo>",
  "rationale": "Skill <slug>@<version> step N is refused by the executor permission layer. Minimum requested operation: ./deploy.sh --check in repos/<repo>. Full refusal evidence and the skill step are in output/<task>/permission-request.md (secrets redacted). This authorizes only this command; it does not change agent permissions.",
  "review_required": true,
  "persistent": false,
  "max_runtime_seconds": 300
}
```

```bash
happyranch jobs submit --org <slug> --from-file /tmp/job-permission-request.json
```

Rules grounded in the serving code:

- `task_id` + `session_id` must come from that active task session. The daemon
  rejects a non-active task (`400 task_not_active`), a stale session
  (`409 session_mismatch`), a completion-recovery session
  (`403 recovery_purpose_forbidden`) and a missing reviewed rationale
  (`400 rationale_required`).
- `title`, `script` and `interpreter` are required; `rationale` is required
  whenever `review_required=true`.
- `cwd_hint` must be relative — no leading `/` and no `..` (`422 invalid_cwd_hint`).
  `max_runtime_seconds` is 1..86400; `persistent` is independent of
  `review_required`.
- Put the skill id/version, scope and evidence context in the supported
  `rationale`, or reference an artifact. Do not invent request fields.
- A submitted reviewed job stays `pending` until the founder approves-and-runs
  or rejects it.
- The reviewed job authorizes **only that command**. Approval never grants a
  lasting permission and never guarantees success.

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

## Normal publication

For a job whose purpose is a normal ref-advancing `git push`, follow the target
repository's documented Git-hook and publication-process requirements. Its
script must contain only safe working-directory/environment setup and `git push`
itself. `git push --no-verify` remains forbidden.

Safe push-only publish job example:

```json
{
  "script": "cd /absolute/path/to/repository && git push -u origin task/TASK-4729\n"
}
```

## After submitting — waiting on jobs

When you need to wait for jobs to finish before proceeding (either
`review_required=true` waiting for founder approval, or `review_required=false`
jobs you can't move forward without), submit your block via `report-completion`
with `status=blocked` and `waiting_on_job_ids` populated:

```json
{
  "task_id": "<TASK>",
  "session_id": "<session>",
  "agent": "<you>",
  "status": "blocked",
  "confidence": 0,
  "summary": "Waiting for JOB-12 and JOB-13 before I can verify the migration ran cleanly.",
  "waiting_on_job_ids": ["JOB-12", "JOB-13"]
}
```

Use the agent-facing `summary` field (the CLI maps it to the daemon's
`output_summary`); do not send the daemon-only alias from a completion file.
`waiting_on_job_ids` is valid only alongside `status="blocked"`: an explicitly
empty list is rejected (`400 empty_waiting_on_job_ids`), so **omit the key
entirely** when there is no live job wait.

The system resumes your task automatically once **every** listed job reaches a
terminal state (`completed`, `failed`, or `rejected`). When you resume, your
bootstrap doc will include a `BLOCKED-JOBS-RESULTS` section listing each job's
status and `happyranch jobs show JOB-NNN` / `happyranch jobs output JOB-NNN`
commands to fetch full output. **You don't poll.**

If you need to stay in-session for a fast `review_required=false` job, the
existing `happyranch jobs wait JOB-NNN --timeout-seconds 30` pattern still works.
Prefer block-and-resume for any wait long enough to risk session timeout.

## Job outcomes and duplicate side effects

When the task resumes, read the actual receipt (`happyranch jobs show` /
`happyranch jobs output`) before acting. Terminal state and exit code are
separate facts:

| Terminal state | Meaning | Required disposition |
| --- | --- | --- |
| `completed`, exit 0 | Executed successfully | Verify output and side effects, then continue the skill step. Do not repeat successful work merely because the task resumed. |
| **`completed`, non-zero exit** | The process ran to completion but **failed** (the runner records `completed` for normal termination and stores `returncode` separately) | Treat as a **failed operation**; inspect output for partial side effects before any retry. |
| `failed` (`timeout` / `output_cap` / external kill) | Did not complete cleanly | May have **partial side effects**; reconcile state, then retry only the unfinished authorized work. |
| `rejected` | Founder declined; the command was **not executed** | **Terminal**: no re-wait and no automatic resubmission. Report the precise blocked disposition with `waiting_on_job_ids` omitted, or finish via an authorized alternative. |
| `running` | Not terminal | The task stays blocked; do not duplicate the submission. |

Never resubmit a byte-identical job automatically. Retry only unfinished
authorized work, after side-effect reconciliation, and never repeat a
successful operation. The blocked-report and lasting-grant rules live in the
**start-task** skill; the executor-specific grant effects live in the
**manage-agent** skill and `docs/agent-guides/agent-executors-and-permissions.md`.

## PR CI / guarded merge helper

For PR-producing engineering tasks, do not hand-roll CI polling scripts. Submit a poll job and use the guarded-merge entrypoint on task resume.

**Poll job:** submit a `review_required=false` job through the existing jobs path whose script invokes the poller entrypoint:

```bash
python -m runtime.daemon.pr_ci_waiter \
  --repo owner/repo --pr N --head-sha <40-char-sha> \
  --expected-check "Python CI" --expected-check "Web CI" \
  --timeout-seconds 3600 --settle-seconds 120 --poll-interval-seconds 15
```

This polls GitHub checks for the pinned head SHA and prints a structured verdict JSON to stdout. It exits 0 for `ci_pass`, non-zero for all other verdicts. **The poll job performs NO merge.**

After the poll job completes, the resumed task inspects the verdict. If `ci_pass`, the task owner triggers the guarded-merge entrypoint as a short daemon-run step:

```bash
python -m runtime.daemon.pr_ci_merge \
  --org <org-slug> --repo owner/repo --pr N --head-sha <40-char-sha> \
  --merge-method squash --ci-verdict ci_pass \
  --review-task-id TASK-xxx --qa-task-id TASK-yyy
```

The merge guard is conjunctive — all must pass before the engine attempts merge:

- review verdict is `APPROVE`;
- QA verdict is `PASS`;
- CI verdict is `ci_pass` for the pinned SHA;
- PR head SHA is unchanged at merge time;
- GitHub mergeability is `CLEAN`;
- the PR is still open and not draft;
- the helper uses the configured merge method.

The guarded-merge implementation validates review/QA evidence (canonical vocabulary
`APPROVE | REQUEST_CHANGES | BLOCK | PASS | REVISE | FAIL`; NON-NULL structured
`verdict` primary; serialized `null` — the durable recall producer's
representation of legacy/no-structured rows — uses the strict annotated
prose `Verdict: PASS — rationale` fallback; missing/contradictory/malformed/
ambiguous evidence fails closed).

It exits successfully only when the PR is merged after all merge guards pass. It exits non-zero for CI failure, stale PR head, timeout, missing checks after settle, non-clean mergeability, rejected job, or failed merge.

The poll job can auto-run without founder interaction. The merge runs inside the guarded-merge entrypoint on the daemon-run / EM-authority path; do not ask for raw `gh pr merge` permission or run arbitrary merge shell from a worker prompt.

## Cleanup

Before reporting your task complete, stop any of your own jobs you no longer need. Persistent jobs you forget about will be auto-killed when your task transitions terminal, but explicit cleanup makes the audit log cleaner and avoids ambiguous "did the agent forget about this?" questions.

## Error handling

- `422 empty_<field>` — required field missing or whitespace-only. Check and resubmit.
- `422` from validator — auth binding malformed (e.g., supplied both `task_id+session_id` AND `task_id`, or supplied `task_id` without `session_id`).
- `422 unknown_interpreter` — `interpreter` not in the allowed set.
- `400 rationale_required` — submitted `review_required=true` without a `rationale`.
- `422 script_too_large` — script body exceeded 64 KB.
- `422 invalid_cwd_hint` — `cwd_hint` is absolute or contains `..`.
- `400 task_not_active` — the referenced task is not pending/in-progress.
- `403 recovery_purpose_forbidden` — this is a completion-recovery binding; it cannot submit new jobs.
- `404 not_found` / `404 unknown_task` — referenced id doesn't exist.
- `409 session_mismatch` — daemon spawned a newer session for this `(task_id, agent)`. Exit immediately.

Retry once after 1 second on any non-listed error.
