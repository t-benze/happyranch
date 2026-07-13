# Tasks vs Threads

**Purpose:** Learn the most important HappyRanch distinction: tasks execute
work; threads carry conversation.

## The Distinction

**Tasks are where work happens.** A task is a unit of execution: build this,
investigate that, write this report, review this change. Tasks can have
subtasks, owners, status, output, and artifacts.

**Threads are where people and agents talk.** A thread is a conversation:
coordination, questions, handoffs, decisions, and status updates.

The rule of thumb:

> If you want work done, dispatch a task. If you want to talk or decide, use a
> thread.

![placeholder: side-by-side Tasks tree and Threads conversation](TODO)

## What You Use First

For the first task path:

1. Use the CLI to create a task with `happyranch run --brief "..."`.
2. Watch the task in the Tasks surface.
3. Check Threads when an agent needs a conversation or decision.
4. Read the task output when it completes.

## Where Each Lives

| Need | Surface |
|---|---|
| Submit work | CLI: `happyranch run --brief "..."` |
| See all work | Web: `/orgs/:slug/tasks`; CLI: `happyranch tasks` |
| Inspect one task | Web: `/orgs/:slug/tasks/:task_id`; CLI: `happyranch details <task_id>` |
| Stream progress | CLI: `happyranch tail <task_id>` |
| Talk with agents | Web: `/orgs/:slug/threads` |

## Manager-Driven Execution

You usually give the task to a team manager. The manager decides whether to do
the work directly, delegate to another agent, fan work out, or escalate to you.
You do not have to prescribe the internal chain for routine work.

## Grounded Technical Facts

- Tasks routes: `/orgs/:slug/tasks`, `/orgs/:slug/tasks/:task_id`.
- Threads routes: `/orgs/:slug/threads`, `/orgs/:slug/threads/:thread_id`.
- Task CLI verbs include `run`, `tasks`, `details`, and `tail`.
- Thread CLI verbs live under `happyranch threads ...`.
