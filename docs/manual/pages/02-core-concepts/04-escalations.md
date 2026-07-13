# Escalations

**Purpose:** Understand what to do when an agent stops and asks for a founder
decision.

## What an Escalation Means

An escalation is not a failure. It means the agent reached a point where it
should not guess: unclear scope, missing authority, a policy question, a risky
action, or a decision only you can make.

The task pauses until you resolve it.

![placeholder: escalated task detail with Continue and Cancel actions](TODO)

## Web Resolution

On the task detail page, the stable web actions are:

- **Continue:** provide the answer or rationale the agent needs, then resume.
- **Cancel:** stop the task. The task ends as cancelled, not failed.

Use Continue when the work should proceed with new input. Use Cancel when the
work should stop.

## CLI-Only: Supersede

There is a third resolution path today: **Supersede**. It replaces the paused
task with a successor task carrying a new brief.

Supersede is **CLI-only** in this v1 content:

```bash
happyranch resolve-escalation --task-id <task_id> --decision supersede --brief "<new brief>"
```

Do not present Supersede as a web action unless the web task action bar exposes
it later.

## CLI Resolution

```bash
happyranch resolve-escalation --task-id <task_id> --decision continue
happyranch cancel <task_id>
```

Note: `resolve-escalation` takes the task via the required `--task-id` flag
(not a positional argument). `cancel` takes the task ID positionally.

`cancel` is a separate CLI verb. It is not a `resolve-escalation` decision.

## Related Approval Path

If the agent asks permission to run a background subprocess, that may appear as
a review-required job instead of a normal escalation. Use the Jobs surface for
that approval.

## Grounded Technical Facts

- The web task action bar exposes Continue and Cancel.
- CLI `resolve-escalation --decision` accepts `continue` and `supersede`.
- Supersede requires a new brief through `--brief` or `--brief-file`.
- Task cancellation uses `happyranch cancel <task_id>`.
