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

## Grounded Technical Facts

- Team structure comes from the org's `teams.yaml`.
- Agents have persistent workspaces under the org runtime.
- Built-in executor profiles cover Claude Code, Codex, opencode, and Pi.
- Agent CLI verbs include `init-agent`, `manage-agent`, `manage-repo`,
  `enrollments`, `approve-agent`, and `reject-agent`.
