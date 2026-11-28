# Agents & Teams

**Purpose:** Understand who does the work and how HappyRanch decides where a
task goes.

## The Structure

HappyRanch runs an org as teams of agents:

- An **org** is the isolated company workspace.
- A **team** groups agents around a function.
- An **agent** is a named role with a persistent workspace.
- An **executor** is the CLI that launches the agent session.

![placeholder: Agents surface showing team roster and per-agent detail](TODO)

## Managers and Workers

Managers orchestrate. Workers execute.

When you submit a task, it normally starts with a team manager. The manager
decides the next step: handle it directly, delegate, split it into parallel
subtasks, or ask you for a decision.

Workers receive bounded subtasks and report results back. They do not own the
overall plan.

This is why your task brief can describe the outcome instead of specifying every
agent step.

## Executors

Each agent runs through an executor profile such as `claude`, `codex`,
`opencode`, or `pi`. Different agents can use different executors in the same
org.

For first use, you only need one executor working. Later, executor choice is a
tool for model diversity and specialization.

## Where You See This

- **Agents:** `/orgs/:slug/agents`
- **Agent detail:** `/orgs/:slug/agents/:agent_name`
- **Executor settings:** Settings -> Executors / Executor Binaries
- **Initialize workspaces:** `happyranch init-agent`

Adding or enrolling agents can require founder approval depending on the org's
rules. The v1 manual does not expand agent administration into a full reference.

**Agents** shows two actions, also offered on an empty roster. Both open the
same creation panel. It names the computer and organization the agent will be
created on, groups the form as **Team and role**, **Agent responsibilities** and
**Runtime**, and keeps a live **Review** beside it (at the end of the form on a
phone). Every review row has **Edit**, which moves to that field and sends
nothing. Nothing is sent until you choose the action.

- **Create team** creates a new team together with its manager. Enter the
  manager's name and the new team name (1–64 lowercase ASCII letters, digits or
  `_`, starting with a letter; `default` is reserved). The team name is suggested
  from the manager name until you edit it; after an edit, **Use suggestion**
  puts the suggestion back.
- **Create agent** first asks what the agent will do: **Join an existing team ·
  Worker** or **Lead a new team · Manager**. A worker joins a team you choose
  from a list that names each team's manager, including the special **Default**
  team, which the human Founder manages without a manager agent. Leading a new
  team works as above and ends in **Create team**.

Create stays unavailable, with the reason shown next to it, while the executor
list is loading, could not be read, or is empty; **Retry** only reads the list
again. After a successful create the panel closes and the new agent opens with a
short confirmation; for a new team it offers **Create agent in** that team. If
the server refuses one field (for example, the team already exists), that field
explains it and the rest is kept; changing the value clears the refusal, and a
refusal about the new team name does not block adding a worker instead. If the
answer is lost, the panel says **Creation result unknown** and offers **Check
result**, which re-reads only the original computer and organization; while
agent names cannot be read completely, the result stays unknown. Closing the
panel then cancels nothing: a **Result unknown** chip on Agents reopens it. If
the desktop app could not forward the request at all, the panel says **Not
sent**, names the cause (for example, the computer was busy or could not be
reached) and the next step, keeps your values and lets you submit again. If the
agent was enrolled but its workspace setup failed, it says **Agent created;
workspace setup incomplete**, lists what exists and what remains, and shows the
`happyranch init-agent` command under **Finish setup**; when the server did not
return the agent's ID, **Check result** looks for it first. Nothing is retried or
deleted automatically. In the desktop app, **Open Connection** in a connection
notice lists the current computer; choosing another one asks about your unsent
draft and then opens Connection, where the switch happens. Moving existing
agents between teams is unsupported. Founder-created agents become active
directly; agent-initiated enrollment retains its separate approval flow.

The canonical [Create team and Create agent product specification](../../../product/prds/2026-11-26-team-and-agent-creation.md)
defines this behavior and its acceptance checks.

## Grounded Technical Facts

- Team structure comes from the org's `teams.yaml`.
- Agents have persistent workspaces under the org runtime.
- Built-in executor profiles cover Claude Code, Codex, opencode, and Pi.
- Agent CLI verbs include `init-agent`, `manage-agent`, `manage-repo`,
  `enrollments`, `approve-agent`, and `reject-agent`.
