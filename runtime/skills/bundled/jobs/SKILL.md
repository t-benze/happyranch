---
name: jobs
description: Run an authorized script in the background; inspect its actual result.
---

# jobs

## When to use

Use a job for a command that does not return (a dev server or watcher), takes
longer than your session can safely wait, or needs an already-authorized daemon
operation. Run short/focused, in-sandbox commands directly.

Obtain any required content authorization in an existing thread BEFORE
submission. Job admission authorizes neither the contents nor new executor
permissions. Submit only from a genuine ordinary active task/session; a thread
invocation token is not a task session ID. A server-designated completion-recovery
turn may only observe an already-owned job and its wait result, never submit a
new one. A normal resumed task uses the ordinary rules.

### Mandatory durable verification boundary

Any verification expected to exceed one minute or the remaining safe session
window MUST use a durable job with exactly `"review_required": false` and
`"persistent": true`. This includes `scripts/local_ci.sh all`, canonical/full
suites in venues the repository still supports, and `git push` when its hooks
invoke such verification. This is no authority to run retired or held suites.

After submission, report `status="blocked"` with the job ID in
`waiting_on_job_ids`; do not poll or continue publication in the same session.
On resume, verify the terminal receipt, exact command, effective runtime
path/version and exit code before continuing. Admission or terminal status alone
is not passing evidence. If duration is uncertain, use this durable boundary.

## The form

Write a complete JSON payload; replace the example task, session and path with
your actual binding and an existing directory relative to your workspace:

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

| Field | Required? | Contract |
| --- | --- | --- |
| `task_id`, `session_id` | Both required | Supply together from the actual active ordinary task session. Neither missing nor partial bindings are valid. |
| `title` | Required | Nonempty after stripping whitespace; at most 200 characters after stripping. |
| `script` | Required | Nonempty after stripping; at most 65536 UTF-8 bytes before stripping. |
| `interpreter` | Required | Exactly one of `bash`, `sh`, `zsh`, `python3`; its binary must be available. |
| `cwd_hint` | Optional; default `null` | Existing directory relative to the agent workspace root; no absolute path or `..` segment. Omission uses the workspace root. Use a path, never a descriptive note; do not `cd` a second time. |
| `rationale` | Optional; default `""` | Context only; may cite existing authorization, never grants it. |
| `review_required` | Optional; default `false` | Deprecated; omit or set false. Parsed true refuses before allocation (`422 review_required_removed`; CLI exit 2). Never flip true and retry or solicit job approval. |
| `persistent` | Optional; default `false` | False defaults to a 300-second runtime cap; true defaults to no runtime cap. Task termination, Stop and daemon shutdown still apply. |
| `max_runtime_seconds` | Optional; default `null` | Explicit integer 1..86400 overrides either persistence default. Null uses that default. |
| `max_output_bytes` | Not a public input | Fixed 50 MiB (52428800 bytes) per stream, stdout and stderr separately. An override is unsupported. |

The public fields and runtime defaults are defined in
`runtime/daemon/routes/jobs.py`; its allocated `JobRecord` inherits the fixed
output cap from `runtime/models.py`.

## Submit

1. Write the payload to an absolute task-owned scratch path, for example
   `${TMPDIR:-/tmp}/job-<random>.json` (expand the variable before writing the file).
2. Submit as a single line; `--from-file` is mandatory:

   ```bash
   happyranch jobs submit --from-file "${TMPDIR:-/tmp}/job-<random>.json" --org <slug>
   ```

3. Keep the returned `JOB-NNN` ID. HTTP 201 / CLI exit 0 means admission only,
   never script success. Inspect returned status and any deferred code.

Initial cwd/interpreter preflight failures allocate no row. A later authenticated
prerequisite loss can fail a never-launched false pending job as
`failed/spawn_failed`, with NULL start/exit. An error identifying a committed
job ID/status requires inspection, not a success claim or silent retry.
Preserve holds, unavailable owners, race winners and possible launches.

Legacy `review_required=true` pending rows stay inert and visible. The `/run`
endpoint is a nonexecuting 410 `job_manual_run_removed` tombstone; `jobs run`
and `scripts run` exit 2 locally. Inspect history and explicitly reject one
pending ID with a reason if appropriate; never mass-dispose or resubmit.

## Block and resume

When an admitted job must finish before you proceed, make your genuine
`report-completion` callback the final action, with this shape:

```json
{
  "task_id": "<TASK>",
  "session_id": "<session>",
  "agent": "<you>",
  "status": "blocked",
  "confidence": 0,
  "summary": "Waiting for JOB-12 and JOB-13 before verifying their results.",
  "waiting_on_job_ids": ["JOB-12", "JOB-13"]
}
```

Use `summary`; the CLI maps it to daemon `output_summary`. The wait field is
valid only with `status="blocked"` and real, owned, nonterminal jobs. Omit it
entirely when no live wait exists; an empty list returns
`400 empty_waiting_on_job_ids`. An authorization question has no job ID.

The task resumes after every listed job is terminal (`completed`, `failed`,
or `rejected`). Its bootstrap includes `BLOCKED-JOBS-RESULTS`. Graceful daemon
shutdown can produce `failed (daemon_shutdown, exit -15)` (signal codes may
differ); resumption occurs on the next startup, not during teardown. Do not poll.

For a fast false-mode job outside the mandatory durable verification boundary,
`happyranch jobs wait JOB-NNN --timeout-seconds 30` can keep you in-session.
Prefer block/resume whenever the wait risks session timeout.

## Verify the actual result

Keep the simple human inspection commands:

```bash
happyranch jobs show JOB-NNN --org ORG
happyranch jobs output JOB-NNN --org ORG
```

For a structured receipt bound to your exact task/session, use either:

```bash
happyranch jobs show JOB-NNN --org ORG --json --task-id TASK --session-id SID
happyranch jobs output JOB-NNN --org ORG --json --task-id TASK --session-id SID --stream both --max-bytes 1048576
```

Both `--json` forms require both binding flags (CLI exit 2 if either is absent)
and return the same receipt endpoint. Structured receipts are terminal-only;
a nonterminal job returns `409 not_terminal`. The receipt includes authenticated
binding, script/interpreter, resolved cwd, status/reason/nullable exit, timestamps,
output and truncation/total-byte metadata. Inspect truncation before treating
captured output as complete. The script itself must print effective runtime
path/version when that evidence is needed; the receipt does not discover it.

The job must belong to the presented task, and the session must be that task's
current live session for the job's agent. A completed job remains readable by
its own live session (including a genuine resumed session of the same task).
A successor task cannot authenticate a predecessor task's job with `--json`.
Use the unbound read-only `jobs show` / `jobs output` forms above for that
historical receipt, and retain its original task attribution; never substitute
a predecessor session ID. `409 session_mismatch` explains the actual refusal
and includes expected/presented task and session fields. `expected_session_state`
distinguishes `active` from `inactive`; an unseen active session ID is withheld
(null) to avoid disclosing an authentication credential. CLI output explicitly
distinguishes a withheld active ID from no active session.

Verify the exact command, cwd, runtime, output, exit code and observable side
effects before acting. Terminal state and exit code are separate facts:

| State | Meaning and disposition |
| --- | --- |
| `completed`, exit 0 | Verify output and effects, then continue. Do not repeat successful work merely because the task resumed. |
| `completed`, nonzero exit | Normal process termination with a failed operation. Inspect output and partial effects before any retry. |
| `failed` (`timeout`, `output_cap`, external kill) | Possible partial effects. Reconcile state; retry only unfinished authorized work. |
| `rejected` | Founder declined; command not executed. Terminal: no re-wait or automatic resubmission. Report the precise blocker without the wait field, or use an authorized alternative. |
| `pending` / `running` | Not terminal; preserve the existing submission and stay blocked when its result is needed. |

Never automatically resubmit a byte-identical job or replay an uncertain effect.
A helper exit alone does not prove script completion: missing/invalid bounded
launch confirmation means `failed/spawn_failed`; confirmed script nonzero exits
remain `completed` with the actual exit code.

## Stop and cleanup

Stop your own jobs you no longer need before the final task callback:

```bash
happyranch jobs stop JOB-NNN --org ORG --task-id TASK --session-id SID
happyranch jobs show JOB-NNN --org ORG
happyranch jobs wait JOB-NNN --org ORG --task-id TASK --session-id SID --timeout-seconds 30
```

Stop 200 acknowledges an owned signal request or a positively proven dead
retained-job settlement; it does not prove successful execution or cleanup.
Inspect actual row/reason/nullable exit and output. Stop remains available under
root Pause. Running without a signal target can return 409
`job_runner_pending`, `job_runner_finalizing` or `job_liveness_unknown`;
unavailable attribution returns `job_owner_unavailable`. Signal/audit 503 reports
`request_issued` true/false/null. Do not blindly retry.

Startup retains unproven running rows as `job_liveness_unknown`. Missing PID,
daemon death, timeout, empty/unavailable advisory fence observations or terminal
status alone never prove process-tree quiescence or release authority. Only the
existing exact-job identity/death/fence checks may settle a dead retained job;
that path sends no signals and grants no cleanup, setup retry or hold release.
Offline operator stopping needs separate authority. Never unlink daemon locks
or fences, unlock inherited custody, or edit job/hold rows to force progress.

The maintained [jobs and cleanup contract](../../../../docs/agent-guides/contracts/jobs-and-cleanup.md)
owns Stop proofs, independent runtime guardians, shutdown grace, retained-job
diagnostics and offline operator procedures. Bounded jobs retain their original
deadline after daemon loss; unbounded persistence supplies no permission grant.
The [runtime lifecycle contract](../../../../docs/agent-guides/contracts/runtime-lifecycle.md)
owns daemon lock, migration, preparation and original-owner settlement details.

Scheduled disk sweeping uses `dry_run=False` only after admission and stop-control
gates; repository-wide empty-scope prune is removed. Follow the maintained
[nightly sweeper contract](../../../../docs/specs/2026-10-15-nightly-disk-sweeper.md)
for scheduled deletion and operator stop control. This grants agents no cleanup,
activation, repair, deploy or restart authority.

## Authorization and uncertain ownership

Classify an operation's failure before requesting a job:

| Failure | Signal | Action |
| --- | --- | --- |
| Executor permission refusal | Executor/sandbox refuses the exact command | Record redacted evidence; obtain required bounded thread authorization before submission. |
| Missing binary | Command not found / exit 127 | Use an available tool or the existing installation process; no permission request. |
| Authentication/credentials | 401/403, auth prompt or missing credentials | Use the credential/operator workflow; no shell-permission request. |
| Network/service | DNS, timeout or connection refusal | Report the dependency. |
| Product policy denial | HappyRanch gate rejects the action | Escalate the policy decision; a job cannot override it. |
| Command bug | Ordinary nonzero program error | Correct the command or inputs within authorized scope. |

For an executor refusal record skill/version, exact command/cwd or resource,
redacted refusal, why it is needed and narrow scope in the existing task artifact.
Pause that operation until required authorization exists. Do not evade it with
an executor switch, skill-level grant, wildcard expansion, gate suppression or
an unrelated credential request. Rationale or compatible source delivery grants
no authority and does not change an older server's policy.

Preserve exact task/session/job ownership, native identity, Pause and cleanup
uncertainty. An already-admitted safe no-launch deferral keeps its exact runner
and submission; historical attribution alone is not current launch eligibility.
Preparation/process failure or later success, callback, terminal state or unrelated
worktree cleanup cannot erase sticky `failed_cleanup`, `execution_unknown` or
unknown producer history. Only explicit native validation/rollback/cleanup and
positive original-owner settlement can establish the existing safe disposition.
Never clear trackers/journals or rewrite history to force release.
Unclosed prepared owners remain unknown after restart, even without a first
launch; unknown deferred ownership cannot authorize a fresh launch.

Frozen retry budgets, including EMPTY schedules, cannot replenish on deferral
or reentry. Missing legacy initialization facts grant no fresh retry/reentry.
A schedule's runtime SID stays distinct from provider resume identity.
Custom-skill authority must validate before reservation; surviving scratch,
inspection errors or prior uncertainty stay unknown, not compensated. Scratch
cleanup requires no-follow lstat ENOENT; a returned failure alone proves none.

Root Pause can leave an admitted job pending; its ID, tracker binding or host
lease is not running evidence or PASS. Only an actual ordinary running session
captured by that Pause generation has the existing auto-run exception, subject
to fresh exact task/agent/SID and generation checks. Resume never enables manual
run. A pre-request snapshot grants no exception; duplicate Pause never renews
capture. Job-owned unknown journals are excluded from ordinary crash recovery;
periodic/startup paths do not re-probe them. Only post-reap proof of the original
group and session both empty may close the job entry; missing handles or failed
tails stay unknown. Terminal status/PID absence cannot settle them. Descendants
escaping via setsid/daemonization remain outside full containment. See the
[task Pause contract](../../../../docs/agent-guides/contracts/task-orchestration.md#root-task-pause-and-resume-thr-292).

## Normal publication

For a normal ref-advancing push, follow the target repository's hook and
publication requirements. A publish job script contains only safe cwd/environment
setup and `git push`; never `--no-verify` or `--force`.

Use an existing workspace-relative worktree directory, with no absolute `cd`:

```json
{
  "task_id": "TASK-4729",
  "session_id": "<your active session_id>",
  "title": "Push task branch",
  "script": "git push -u origin task/TASK-4729\n",
  "interpreter": "bash",
  "cwd_hint": "repos/happyranch/.claude/worktrees/TASK-4729",
  "rationale": "Publish the authorized committed change with normal hooks.",
  "review_required": false,
  "persistent": true,
  "max_runtime_seconds": 3600
}
```

## PR CI and guarded merge

An authorized poll job reports readiness only. For HappyRanch, retain exactly the
two lint/static expected checks at the pinned PR head and exact merged main:

```bash
python -m runtime.daemon.pr_ci_waiter \
  --repo owner/repo --pr N --head-sha <40-char-sha> \
  --expected-check "Source Boundary Lint" \
  --expected-check "Web Lint (Node 24)" \
  --timeout-seconds 3600 --settle-seconds 120 --poll-interval-seconds 15
```

Use the maintained [CI policy](../../../../docs/operations/local-ci.md).
Local default/all/pre-push checks are lint/static only; Python units are RETIRED
(exit 2), general integration stays SKIPPED unless separately authorized. Daily
Product E2E Health runs at 19:00 UTC and stays outside merge-check lists. This
skill grants no E2E dispatch, new trigger, retry or broader run authority.
Other repositories follow their own policy. Determine protection configuration
through a fresh authorized read when it matters; never infer external enforcement
from a past snapshot. Static checks are not behavioral proof.

The poller prints a structured verdict, exits 0 only for `ci_pass` and performs
NO merge. After verifying its terminal receipt, the final assigned manager must
personally check settled independent exact-head APPROVE/PASS and successful
literal checks, then act and observe remotely in its genuine live session:

```bash
python -m runtime.daemon.pr_ci_merge \
  --org <org-slug> --repo owner/repo --pr N --head-sha <40-char-sha> \
  --merge-method squash --ci-verdict ci_pass \
  --review-task-id TASK-xxx --qa-task-id TASK-yyy
```

All merge guards remain conjunctive: independent review APPROVE, QA PASS,
unchanged pinned head, `ci_pass`, open non-draft PR and CLEAN mergeability.
Pending, missing, ambiguous, skipped or neutral literal checks hold Final.
The ordinary configured method uses `gh pr merge --match-head-commit H` without
`--admin`. Missing, contradictory, malformed or ambiguous verdict evidence fails
closed; structured verdict is primary, with only the existing strict annotated
legacy prose fallback for serialized null.

Success needs actual read-only remote confirmation of addressed repo/PR/URL,
pinned head, MERGED state, full lowercase SHA1 merge commit and timezone-aware
`mergedAt`. Missing/invalid metadata or query failure after command success
means `merge_failed` (exit 14), with post-effect uncertainty. Never substitute
an empty commit/local time, use admin fallback or automatically replay.
Later read-only confirmation can establish an effect but cannot rewrite the
original failed/uncertain receipt or permit a second merge. Callback completion
never triggers daemon PR finalization/recovery. Existing tool/repository
entitlement remains required; workers must not request raw merge permission or
run arbitrary merge shell.

## Coding and selected live Final

Only the genuinely selected live Final manager may use the mounted LOCAL
sequential Final transport, under its existing current-authority, selection,
independent exact-head review/QA, literal-lint and expected-head non-admin
action checks. The maintained
[Final contract](../../../../docs/specs/2026-11-11-sequential-coding-u7.md#u7c2b-complete-final-admission-task-15564)
owns selection, local intent/command/remote receipts, uncertainty and authenticated
success admission. Wiring proves neither deployment nor new authority.

Never move Final selection, intent/action/observation/receipt production,
`execute_selected_final` or an uncertain-effect retry into a job, daemon,
second CLI OrgState or recovery runner. Access denied versus unverified remains
causal BLOCK/Held; graph data grants no credential or access-observer authority.
Failed/uncertain/missing command, including recording failure with command=None,
keeps causal BLOCK, empty content/claims and original local records, never a
merged bundle or second action. Preserve existing per-complete-read allowances
on one original deadline.

The [Coding contract](../../../../docs/agent-guides/contracts/coding-workflow.md)
owns actual binding/version rules and agent responsibilities. R2 unit (a) supports
explicit Founder Workflows → Setup Save of Coding v3 role defaults and separate
Allow/Disable of new-run admission. Save preserves admission and starts no work;
Disable preserves admitted-run pins. Registration alone grants no role assignment,
Allow, job or credential entitlement. Public Start@2 multi-run admission and
continuation are supported by R2 unit (b) through genuine task completion;
Draft→Review registration remains a later unit. The offline script still refuses
fresh admission. Its generic node
brief requires authorized repository/base/worktree preflight, independent
exact-head review, same-head QA and genuine live Final with matching-head literal
checks and remote MERGED observation. Follow the generic callback@2 and bounded
JSON report convention in start-task and that contract. Generic acceptance is
not semantic/remote verification; semantic adherence/pilot is NOT RUN. Historical special Coding
LOCAL finalize instructions apply only to actual old bindings, never to v3.
Coding source/carrier/stage jobs remain forbidden under existing document/legacy
predicates, even for an agent_manager role. Separately entitled ordinary legacy
preparation/verification/publication jobs retain their rules; infer none here.

When original authorized Coding cancellation wins, stop fresh repository, stage,
result, successor and Final work. Preserve original intent/action/receipt,
native preparation and terminal/outer uncertainty. Only an already-committed
exact original INTEGER/task/SID callback may drain/replay unchanged without
advance; a stopped task, missing handle or successful control return proves no
cleanup or remote-effect clearance. The existing Founder cancellation route
owns control; this skill supplies none. Readiness, live allocation and deferred
recovery/reassignment holds remain as defined by the actual binding's contract.
No automatic held-manager reallocation or PR recovery service is authorized.
Always use your own actual task/SID; genuine report-completion is the final
action, never a fabricated earlier result or another task's callback.

## Error handling

| Surface / response | Meaning |
| --- | --- |
| CLI local error, exit 2 | Missing/falsy `title`, `script` or `interpreter`; missing both task/session values or supplying only one. No submission. |
| Direct HTTP request-model 422 | Omitted required fields or missing/partial task/session binding; standard validation, not an `empty_<field>` code. |
| `422 empty_title` / `empty_script` | Whitespace-only title/script reaching the explicit checks (script size is checked first). |
| `422 title_too_long` / `script_too_large` | Stripped title exceeds 200 characters / original script exceeds 65536 UTF-8 bytes. |
| `422 unknown_interpreter` | Interpreter not exactly in the allowed set, including whitespace-only. |
| `422 interpreter_unavailable` | Allowed interpreter has no available binary. |
| `422 review_required_removed` | Parsed true mode; CLI also rejects locally with exit 2. Obtain required authorization first; no flip/retry. |
| `422 invalid_cwd_hint` / `409 cwd_missing` | Absolute/`..` hint / resolved directory missing or not a directory. |
| `422 invalid_timeout` | Explicit runtime outside 1..86400 seconds. |
| `400 task_not_active` | Task is neither pending nor in progress. |
| `403 recovery_purpose_forbidden` / `workflow_document_only` | Completion-recovery binding / task outside the supported legacy job path. |
| `404 unknown_task` | Submission references a missing task. |
| `404 unknown_job` | Show, receipt, output, Stop or wait references a missing job ID. |
| `409 not_terminal` | Receipt/output requested before terminal state. |
| `409 session_mismatch` | Receipt task differs from the job's owner, binding is missing, task/agent ownership differs, or session is inactive/different. Inspect the reason and expected/presented pair; do not retry with a predecessor SID. Historical unbound read-only show/output remain available. |

Do not automatically retry submission or flip policy fields. Reconcile the
actual ID and effects and resolve the error before a fresh authorized request.
