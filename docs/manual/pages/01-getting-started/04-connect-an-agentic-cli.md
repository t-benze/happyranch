# 04 - Connect an Agentic CLI

**Purpose:** Make sure HappyRanch can launch at least one AI coding CLI for
agent sessions.

## Current Caveat

The THR-088 executor-connect flow is still changing. This page documents the
current stable model: install a CLI, register its absolute binary path under an
executor profile, and initialize agent workspaces. Do not treat this as the
final Step-1 connect walkthrough.

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

You need at least one of those CLIs installed and explicitly registered with a
valid absolute binary path before an agent can do useful work. Simply having
the CLI on `PATH` is not enough — HappyRanch never discovers or auto-resolves
PATH executables for executor launch; only a valid profile-name registration in
`executors.json` makes a profile launchable.

## Check Readiness

The web onboarding page and Settings → Executor Binaries show which executor
profiles have a valid machine-local registration. A profile with no registration
(or a stale path) is visible but unavailable for launch.

![placeholder: Executor readiness panel showing one registered CLI and one unregistered](TODO)

This panel is a check. If a profile is unregistered, install the CLI through
that provider's own instructions, then register its absolute binary path.

## Current Manual Setup

The stable path is:

1. Install at least one supported agentic CLI through that provider's own
   instructions.
2. Create the runtime and org (see the previous page).
3. Explicitly register the CLI's absolute binary path under the intended
   executor profile:

   ```bash
   happyranch executor-binaries register <profile-name> --path <absolute-path>
   ```

   For example: `happyranch executor-binaries register claude --path /usr/local/bin/claude`.
   Without this registration the profile is visible but cannot launch agent
   sessions.
4. Run `happyranch init-agent` so each org agent receives a workspace and
   executor configuration.
5. If using the assistant dock, run `happyranch assistant init` and follow the
   printed registration instructions.

Each agent's executor and repos are declared in its AgentDef frontmatter at
`org/agents/<name>.md` (in the org tree, not the workspace; workspace
`agent.yaml` was retired by THR-095):

```yaml
---
name: dev_agent
team: engineering
role: worker
executor: claude
repos:
  happyranch: https://github.com/t-benze/happyranch.git
---
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
> Template-based generic profiles are retired. Connect a custom CLI through an
> approved `custom-adapter:<id>` wrapper; built-in CLIs retain their existing
> registration flow.
