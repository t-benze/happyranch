# Jobs & Approvals

**Purpose:** Know what it means when an agent creates a background job or asks
you to approve one.

## What a Job Is

A job is a subprocess the daemon runs outside the agent's active session.
Agents use jobs for work that should not block the session, such as long test
runs, watchers, or commands that need human approval first.

You usually do not create jobs yourself. You review them when an agent asks.

## Review-Required Jobs

Some jobs are marked review-required. Those jobs wait until you decide.

- **Approve / run:** the daemon runs the job.
- **Reject:** the daemon does not run it, and the agent is told.

![placeholder: Jobs surface with a review-required job awaiting founder approval](TODO)

Use this as a safety control. If an agent wants to run something sensitive or
credentialed, the job queue gives you the chance to inspect it before execution.

## Where You Act

- **Web:** `/orgs/:slug/jobs`, `/orgs/:slug/jobs/:job_id`
- **CLI:** `happyranch jobs list`, `happyranch jobs show <id>`,
  `happyranch jobs run <id>`, `happyranch jobs reject <id>`

## Jobs vs Tasks vs Escalations

| Concept | What it means |
|---|---|
| Task | The unit of work an agent owns |
| Job | A subprocess launched for that work |
| Escalation | A decision request to you |

A review-required job is a specific kind of approval: "may I run this command?"

## Grounded Technical Facts

- Jobs routes: `/orgs/:slug/jobs`, `/orgs/:slug/jobs/:job_id`.
- Job CLI verbs are under `happyranch jobs ...`.
- The `review_required` job policy flag holds execution for founder approval.
