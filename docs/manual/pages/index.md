<span className="hr-eyebrow">HappyRanch Manual · v1</span>

# Run a small organization of AI agents — under one founder.

<p className="hr-lede">You give the org a goal. Agents do the work in isolated workspaces. You stay in control through tasks, threads, escalations, jobs, artifacts, memory, KB, and usage.</p>

<a className="hr-cta" href="/01-getting-started/01-requirements-install">Get started →</a>

## What HappyRanch Is

HappyRanch is a local runtime for running a small AI-agent organization under
one human founder. You give the org a goal, agents do the work in isolated
workspaces, and you stay in control through task status, threads, escalations,
jobs, artifacts, memory, KB, and usage.

The core operating loop is simple:

1. Start the daemon and create an org.
2. Make sure at least one agentic CLI executor is available.
3. Dispatch a task from the CLI.
4. Monitor progress in Tasks and Threads.
5. Respond when an agent asks for a decision.
6. Retrieve the result from the task output or Artifacts.

## What v1 Covers

This manual covers the activation journey: a clean runtime to a completed first
task. It explains the product model just far enough for a new founder-operator
to know what each surface is for.

- **Getting started** — install, daemon, runtime/org creation, executor
  readiness, first task.
- **Core concepts** — Tasks vs Threads, Agents & Teams, Jobs, Escalations,
  Memory / KB / Artifacts, Usage.
- **First task workflow** — one end-to-end walkthrough.

Not included in v1: a full web-surface reference, a full CLI reference, Mac
remote-access or deployment/runbook material, detailed executor-connect
instructions while that flow is still changing, and wired screenshots
(screenshot slots remain placeholders).

## Contents

### 1. Getting Started
- [Requirements & install](01-getting-started/01-requirements-install.md)
- [Start the daemon](01-getting-started/02-start-the-daemon.md)
- [Create a runtime + your first org](01-getting-started/03-create-runtime-and-org.md)
- [Connect an agentic CLI](01-getting-started/04-connect-an-agentic-cli.md)
- [Run your first task](01-getting-started/05-run-your-first-task.md)

### 2. Core Concepts
- [Concepts index](02-core-concepts/00-concepts-index.md)
- [Tasks vs Threads](02-core-concepts/01-tasks-vs-threads.md)
- [Agents & Teams](02-core-concepts/02-agents-and-teams.md)
- [Jobs & approvals](02-core-concepts/03-jobs-and-approvals.md)
- [Escalations](02-core-concepts/04-escalations.md)
- [Memory, KB & Artifacts](02-core-concepts/05-memory-kb-artifacts.md)
- [Usage & tokens](02-core-concepts/06-usage-and-tokens.md)

### 3. First Task Workflow
- [Your first task, end to end](03-first-task-workflow/01-your-first-task-end-to-end.md)
