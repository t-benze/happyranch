# HappyRanch — Multi-Agent Org Runtime

HappyRanch is an **org-agnostic runtime** for operating a multi-agent organization supervised by a single human founder. The repo provides the system kernel; the *organization* it runs — charter, teams, agents, escalation rules — is loaded per-runtime from `<runtime>/orgs/<slug>/org/`.

A canonical sample org shipped at `examples/orgs/hk-macau-tourism/` runs a one-person tourism company serving foreign visitors to Hong Kong SAR and Macau SAR. Treat it as the reference shape when bootstrapping a new org.

## How It Works

HappyRanch runs as a local **HTTP daemon** that dispatches tasks to AI agents running as coding-agent CLI sessions. The `happyranch` CLI is a thin client that talks to the daemon. Each agent has a persistent workspace and a defined role within its org. Executor selection is per-agent: agents run on built-in profiles ([Claude Code](https://docs.anthropic.com/en/docs/claude-code), Codex, opencode, Pi) or registered profiles bound to a founder-approved `custom-adapter:<id>`.

A single runtime container hosts **multiple orgs** under `<runtime>/orgs/<slug>/`, each with its own DB, workspaces, KB, and threads. One daemon serves them all concurrently.

### Manager-driven orchestration

Each org has one or more **team managers** that drive task execution. When you submit a task, the manager analyzes it and decides what to do at each step — handle it directly, delegate to a team member, or escalate to the founder. There are no hardcoded task chains. The historical orchestration-step count is monotonic telemetry, not an execution limit.

### Dynamic agents

Agents are dynamic — a manager can propose new agents via the `manage-agent` skill, and the founder approves enrollment before the agent's workspace is bootstrapped. The roster grows organically as the org needs new capabilities.

## Requirements

- macOS or Linux. Both use the same same-owner canonical skill-store model;
  Windows and unknown platforms fail closed before agent materialization or
  launch.
- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- At least one supported agent CLI installed and authenticated:
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
  - Codex CLI
  - opencode CLI
  - Pi CLI

## Setup

```bash
git clone https://github.com/t-benze/happyranch.git
cd happyranch
uv sync
uv run happyranch --help
```

## Quick Start

This walks through setting up a runtime container and materializing the canonical HK/Macau tourism sample org.

```bash
# 1. Start the daemon (once per machine). It listens on localhost:8765 and
#    stores its auth token + runtime registry under ~/.happyranch/.
scripts/daemon.sh start

# 2. Create and activate a runtime container. Slugless — orgs live as
#    subdirectories under <runtime>/orgs/<slug>/.
happyranch init ~/happyranch-runtime

# 3. Materialize an org from a sample tree.
happyranch orgs init hk-macau-tourism --from examples/orgs/hk-macau-tourism

# 4. (Optional) Set the default org so you don't pass --org on every command.
export HAPPYRANCH_ORG_SLUG=hk-macau-tourism

# 5. Initialize agent workspaces (generates bootstrap docs, copies skills,
#    clones repos declared in each agent's org/agents/<name>.md frontmatter).
happyranch init-agent

# 6. Run a task. The CLI streams live events until done.
happyranch run --brief "Explore how the payment module handles refunds"

# Re-attach to a running task and stream events
happyranch tail TASK-001

# Check task details (status, block_kind, note, results, audit log)
happyranch details TASK-001

# List recent tasks
happyranch tasks
```

## Documentation

- [User manual](docs/manual/pages/index.md)
- [Command and configuration reference](docs/manual/pages/04-reference/00-reference-index.md)
- [Documentation map](docs/README.md)
- [Operations](docs/operations/README.md)

## Retired System Assistant

System Assistant is retired (THR-294). Its HTTP/WebSocket routes, CLI commands,
settings, dock and Cmd/Ctrl-K binding are removed. Existing assistant config,
conversations, workspace files and skill links remain inert and are not cleaned
or migrated. Provider conversation roles and ordinary org-agent capabilities
remain supported. The reserved `system_assistant` workspace name stays excluded
from the org-agent YAML migration, preventing legacy YAML reads or sentinel
writes. Historical database, audit and token records remain readable.

## Roadmap

- [ ] Inter-team communication and cross-team handoff
- [ ] Founder dashboard (aggregated audit logs + escalation summaries)
- [ ] Persistent agents (long-running loops for patterns that don't fit single-task batch execution, e.g., real-time customer chat)

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
