# Jobs and authorization

A job is a subprocess the daemon runs for an agent task. Obtain any required
Founder authorization in a thread **before submitting**. New authorized jobs
enter automatic admission; there is no per-job approval queue. The job does
not grant permissions or guarantee script success.

Use `/orgs/:slug/jobs` or `/orgs/:slug/jobs/:job_id` to inspect stored commands,
status, output and history. `happyranch jobs list/show/output/tail/wait` expose
the existing read surfaces; structured receipts require the exact task/session.

Omit `review_required` or set false. Rationale is optional. True requests refuse
422 before allocating a job; the new CLI refuses locally with exit2. Never
silently flip true and retry. Older clients may need an upgrade or rationale for
false mode. New clients do not change an old daemon's operating policy.

HTTP201/CLI exit0 means admission. Check the actual terminal reason, exit code
and output. A naturally completed job with a nonzero exit is unsuccessful.
Nonpersistent jobs default to300 seconds; persistent jobs have no default cap;
explicit1..86400 seconds overrides either. Existing output limits and stop/task
termination/shutdown behavior remain.

Historical review-required pending jobs **will not run**. Their commands, flags,
review fields and audits remain readable. The legacy disposition backlog remains
accurate. Inspect and explicitly reject one pending ID with a reason if it should
close (`happyranch jobs reject ID --reason ...`); running jobs retain Stop. Manual
run is retired: `/run` returns410 and both CLI run aliases exit2. No automatic
historical execution, migration, bulk rejection or resubmission occurs.

Initial missing cwd/interpreter refuses before allocation. A genuine later loss
can fail an authenticated never-launched false pending job with spawn_failed,
NULL start/exit and an actual finish/diagnostic; an error identifies a committed
failed ID/status. Inspect that record rather than assuming a successful submit.

## Jobs, tasks and escalations

A task owns the work; a job runs a command for that task; an escalation requests
a decision. Authorization is settled in the existing thread before submission.
Waiting tasks resume through the existing all-jobs-terminal mechanism; terminal
rejection/failure wakes the owner but is never a success proof.

## Pause and Resume

Held roots preserve pending work. Only the ordinary actual running session
captured by that Pause generation retains its existing authorized false-mode
exception, with fresh exact task/agent/session checks. Other automatic jobs can
defer pending. Resume grants no new authority or session and never launches
legacy true rows. Existing jobs may finish; reject/stop remain available.
