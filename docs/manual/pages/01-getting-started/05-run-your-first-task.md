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

`happyranch details TASK-001` (and the Tasks task-detail page's **Execution
status** card) summarize observed work activity: the current-session start
time, the last heartbeat with an explicit freshness label, and the latest
agent-written progress update when one exists. A live task with no
substantive update is called out explicitly (e.g. `Stale-but-alive — no
substantive update recorded`) rather than implying progress from heartbeats,
so a task whose session is alive but silent is immediately visible.

## What Happens Behind the Scenes

1. The daemon creates the task.
2. A team manager agent receives it.
3. The manager handles it, delegates it, or escalates to you.
4. Worker agents may run subtasks.
5. The final result appears on the task detail and in CLI task details.
6. Any files the agent produced go to Artifacts.

## Next

Go to [Your First Task - End to End](../03-first-task-workflow/01-your-first-task-end-to-end.md).


## Pause and Resume

After your root task is In Progress, `happyranch pause TASK --org ORG` holds future steps in that actual task tree. A Pending root cannot yet be paused. Already committed work finishes; Pausing shows remaining work or unknown evidence, while Paused requires complete drain evidence. `happyranch resume TASK --org ORG` releases the hold using the current generation; it does not restart a completed session. Controls are CLI/API in the backend unit; browser controls arrive separately.

A successful session that omitted its callback keeps its one recovery opportunity while held. The 120-second deadline starts when that recovery is actually admitted, and Resume or restart cannot renew an admitted recovery. Missing closure evidence from the original execution remains visible even after a recovery finishes or the task reaches its terminal status.
