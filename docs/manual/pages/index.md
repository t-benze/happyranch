# HappyRanch End-User Manual - v1 Product Pass

> **Status:** product narrative/voice pass over the engineering v1 content from
> `engineering_manager/2026-07-12-user-manual-v1-content/`.
>
> **Audience:** a first-time founder-operator using HappyRanch to get work done.
> This is not an agent implementation guide, not a deployment runbook, and not a
> full page-by-page product reference.
>
> **Honesty fence:** this pass keeps engineering's verified command and route
> claims where they matter, labels CLI-only actions as CLI-only, and leaves the
> evolving THR-088 executor-connect flow caveated instead of turning it into a
> false walkthrough.

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

## v1 Scope

This manual covers only the activation journey: clean runtime to completed
first task. It explains the product model just far enough for a new
founder-operator to know what each surface is for.

Included:
- Getting started: install, daemon, runtime/org creation, executor readiness,
  first task.
- Core concepts: Tasks vs Threads, Agents & Teams, Jobs, Escalations, Memory /
  KB / Artifacts, Usage.
- One end-to-end first-task workflow.

Not included in v1:
- Full web-surface reference.
- Full CLI reference.
- Mac remote-access or deployment/runbook material.
- Detailed THR-088 executor-connect instructions while that flow is still
  changing.
- Screenshot wiring; screenshot slots remain placeholders.

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

## Editorial Accounting

Source set read: 14 files.

Product pass changed: 14 revised Markdown files plus this README summary.

Primary edits:
- Reframed the opening around the founder-operator activation loop.
- Made CLI dispatch explicit and removed the contradictory implication that web
  task creation is part of the stable v1 path.
- Moved implementation detail below user intent where possible.
- Tightened the concepts around "what this is for" and "where you act."
- Kept CLI-only Supersede and other non-web actions labeled.
- Reduced unstable executor-connect content to current readiness/manual setup
  with a clear caveat.

## Founder Decisions (ruled on THR-089 msg35 — baked into this v1)

1. **Node version floor = 24.** The requirements page states Node.js 24 or
   newer as the minimum supported version. Engineering flag: the repo has no
   `engines` field in `web/package.json` and no `.nvmrc`; the only Node pin in
   the codebase is the web CI job (`.github/workflows/ci.yml`, `node-version:
   20`). Founder to reconcile the CI pin (20) with the documented floor (24).
2. **Executor-connect (THR-088) = deferred.** The connect step stays caveated;
   this v1 does not publish a detailed Step-1 connect walkthrough while that
   flow is still evolving.
3. **Web task creation = deferred.** v1 treats task dispatch as CLI-only. If a
   web create-task affordance ships, update the first-task and Tasks-vs-Threads
   pages. (Explicit decision note — web task creation is not yet in scope.)
4. **Supersede = CLI-only.** Supersede stays documented as a CLI-only escalation
   resolution. Verified against code: the task-detail action bar exposes only
   Continue and Cancel (plus Revisit); there is no web Supersede on tasks.
5. **Out of scope confirmed.** v1 excludes deployment/runbook material and Mac
   remote-access content; none is present.
