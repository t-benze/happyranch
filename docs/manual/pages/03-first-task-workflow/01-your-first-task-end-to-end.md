# Your First Task, End to End

**Purpose:** Complete the first HappyRanch operating loop: dispatch work,
monitor it, respond if needed, and retrieve the result.

Read this page after Getting Started. It assumes:

- the daemon is running,
- a runtime and org exist,
- agent workspaces have been initialized,
- and at least one executor CLI is available.

Open the web UI next to your terminal:

```text
http://127.0.0.1:8765
```

## Step 1 - Dispatch Work

For v1, create the task from the CLI:

```bash
happyranch run --brief "Analyze the HappyRanch repo and write a one-paragraph summary of its architecture"
```

The command returns a task ID:

```text
Submitted TASK-001. Attach with: happyranch tail TASK-001
```

Keep that task ID. It is the handle you use to monitor and retrieve the work.

![placeholder: terminal showing `happyranch run` returning TASK-001](TODO)

## Step 2 - Monitor Progress

Use **Tasks** for execution status.

- Tasks list: `/orgs/:slug/tasks`
- Task detail: `/orgs/:slug/tasks/:task_id`

The task detail page shows the task tree, current status, subtasks, and final
summary.

CLI equivalents:

```bash
happyranch tail TASK-001
happyranch details TASK-001
happyranch tasks
```

Use **Threads** for conversation.

- Threads list: `/orgs/:slug/threads`
- Thread detail: `/orgs/:slug/threads/:thread_id`

If an agent needs to coordinate, report a decision point, or discuss work with
you, Threads is where that communication belongs.

![placeholder: task detail page showing task tree and live status](TODO)

## Step 3 - Respond When Asked

Most first tasks should finish without founder input. If an agent escalates, the
task pauses until you decide.

On the web task detail page:

- **Continue:** answer the question or provide the required rationale.
- **Cancel:** stop the task.

CLI:

```bash
happyranch resolve-escalation --task-id TASK-001 --decision continue
happyranch cancel TASK-001
```

Supersede exists as a CLI-only advanced path:

```bash
happyranch resolve-escalation --task-id TASK-001 --decision supersede --brief "<new brief>"
```

Do not look for Supersede in the stable v1 web action bar.

![placeholder: escalated task detail with Continue and Cancel actions](TODO)

## Step 4 - Approve Jobs If Needed

Sometimes the agent does not need a conceptual decision; it needs permission to
run a background subprocess. That appears as a review-required job.

Use:

- Jobs list: `/orgs/:slug/jobs`
- Job detail: `/orgs/:slug/jobs/:job_id`
- CLI: `happyranch jobs list`, `happyranch jobs show <id>`,
  `happyranch jobs run <id>`, `happyranch jobs reject <id>`

Approve only when the action is expected and acceptable. Reject when the command
should not run.

## Step 5 - Retrieve the Result

When the task completes, check two places.

First, read the task result:

```bash
happyranch details TASK-001
happyranch recall TASK-001
```

Or open the task detail page.

Second, retrieve files from Artifacts when the task produced a file:

```bash
happyranch artifacts list
happyranch artifacts get <name> --output ./local-copy
```

Web:

- Artifacts: `/orgs/:slug/artifacts`

![placeholder: Artifacts surface listing a completed task output file](TODO)

Short text summaries live on the task. Files live in Artifacts.

## The Loop You Just Learned

| Phase | Web surface | CLI |
|---|---|---|
| Dispatch | Not in stable v1 web path | `happyranch run --brief "..."` |
| Monitor | Tasks, Threads | `tail`, `details`, `tasks` |
| Respond | Task detail, Threads, Jobs | `resolve-escalation`, `cancel`, `jobs ...` |
| Retrieve | Task detail, Artifacts | `details`, `recall`, `artifacts get` |

That is the v1 HappyRanch operating model. Larger work uses the same loop with
more subtasks, more thread coordination, and more artifacts.

## Grounded Technical Facts

- Task dispatch: `happyranch run --brief|--brief-file` (CLI-only in v1; web
  task creation is deferred — see the founder-decisions note in `00-README.md`).
- Monitor: `/orgs/:slug/tasks`, `/orgs/:slug/tasks/:task_id`,
  `/orgs/:slug/threads`, `/orgs/:slug/threads/:thread_id`.
- Respond: web Continue / Cancel; CLI `resolve-escalation --task-id <id> --decision continue`,
  CLI-only `resolve-escalation --task-id <id> --decision supersede`, and separate
  `happyranch cancel <task_id>`.
- Jobs: `/orgs/:slug/jobs`, `/orgs/:slug/jobs/:job_id`, and `happyranch jobs ...`.
- Retrieve: `/orgs/:slug/artifacts`, `happyranch artifacts ...`,
  `happyranch details`, and `happyranch recall`.
