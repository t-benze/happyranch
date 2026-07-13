# 04 - Connect an Agentic CLI

**Purpose:** Make sure HappyRanch can launch at least one AI coding CLI for
agent sessions.

## Current Caveat

The THR-088 executor-connect flow is still changing. This page documents the
current stable model: check readiness, choose an executor profile, and initialize
agent workspaces. Do not treat this as the final Step-1 connect walkthrough.

## What an Executor Is

An executor is the CLI HappyRanch launches when an agent starts a session. Each
agent has an executor profile.

Built-in profiles:

| Profile | Binary |
|---|---|
| `claude` | `claude` |
| `codex` | `codex` |
| `opencode` | `opencode` |
| `pi` | `pi` |

You need at least one of those CLIs installed and available on `PATH` before an
agent can do useful work.

## Check Readiness

The web onboarding page shows an executor readiness panel. It reports which
supported binaries are found and which are missing.

![placeholder: Executor readiness panel showing one ready CLI and one missing CLI](TODO)

This panel is a check, not the final connect flow. If a CLI is missing, install
it through that provider's own instructions, then return to HappyRanch.

## Current Manual Setup

The stable path is:

1. Install at least one supported agentic CLI.
2. Create the runtime and org.
3. Run `happyranch init-agent` so each org agent gets a workspace and executor
   configuration.
4. If using the assistant dock, run `happyranch assistant init` and follow the
   printed registration instructions.

Each agent workspace includes an `agent.yaml` that declares the executor:

```yaml
executor: claude
repos:
  happyranch: https://github.com/t-benze/happyranch.git
```

If your org changes an agent's executor, re-run initialization for that agent so
the workspace matches the intended setup.

## Where Executor Settings Live

- **Web:** Settings -> Executors and Settings -> Executor Binaries.
- **CLI:** `happyranch executors ...` and `happyranch executor-binaries ...`
  command groups.

This manual does not expand those into a full reference in v1. The activation
goal is narrower: have one executor ready so your first task can run.

## Next

Go to [05 - Run Your First Task](05-run-your-first-task.md).
