# 05 - Run Your First Task

**Purpose:** Submit the first unit of work and know where to watch it.

## The 60-Second Model

A **task** is work for agents to execute. A **thread** is a conversation with
agents. You submit work as a task, then monitor the task and any related thread
activity.

For v1, task submission is **CLI-only**. The web UI is for monitoring,
responding, and retrieving outputs after work starts. Web task creation is
deferred (not yet in v1 scope); see the founder-decisions note in
[the manual index](../00-README.md).

## Dispatch from the CLI

From inside the repo, with the daemon running and an org initialized:

```bash
happyranch run --brief "Analyze the HappyRanch repo and write a one-paragraph summary of its architecture"
```

The command returns a task ID:

```text
Submitted TASK-001. Attach with: happyranch tail TASK-001
```

Useful options:

| Option | Use it when |
|---|---|
| `--brief "..."` | The task brief is short |
| `--brief-file PATH` | The task brief is longer or already written |
| `--team TEAM` | You want a specific team manager |
| `--owner NAME` | You need to set task owner explicitly |
| `--org SLUG` | More than one org exists or you want no ambiguity |

## Watch Progress

CLI:

```bash
happyranch tail TASK-001
happyranch details TASK-001
happyranch details TASK-001 --full
happyranch tasks
```

Web:

- Tasks list: `/orgs/:slug/tasks`
- Task detail: `/orgs/:slug/tasks/:task_id`
- Threads: `/orgs/:slug/threads`

Tasks show execution status and results. Threads show conversation and
coordination.

## What Happens Behind the Scenes

1. The daemon creates the task.
2. A team manager agent receives it.
3. The manager handles it, delegates it, or escalates to you.
4. Worker agents may run subtasks.
5. The final result appears on the task detail and in CLI task details.
6. Any files the agent produced go to Artifacts.

## Next

Go to [Your First Task - End to End](../03-first-task-workflow/01-your-first-task-end-to-end.md).
