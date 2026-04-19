---
name: opc
description: Manage the OPC AI tourism organization via the `opc` CLI — submit tasks, stream events, check agent performance tiers, inspect audit logs, approve agent enrollments, operate the shared knowledge base, and record founder-resolved precedents. Use when the user asks about OPC tasks, agents, the daemon/runtime, the knowledge base, or the one-person-company system.
metadata:
  {
    "openclaw":
      {
        "emoji": "🏢",
        "requires": { "bins": ["uv"] },
      },
  }
---

# OPC

Manage the OPC (one-person company) AI tourism organization: submit tasks to the EH-driven orchestrator, watch them stream, review agent performance tiers, handle the agent enrollment flow, recall past task context, and operate the shared knowledge base.

> Commands below use the skill-local shim `scripts/opc`, which auto-detects the project root from the skill's own location and invokes `uv --project <root> run opc`. The skill source lives at `<my-opc>/skills/opc/` and is symlinked from `~/.claude/skills/opc` for user-level availability — the shim uses `cd -P` so it always resolves back to the real checkout. Override with `OPC_PROJECT_DIR` if the skill is ever relocated outside the project tree.

## Prerequisites

- `my-opc` project checked out — the shim walks `scripts/ → opc/ → skills/ → project root` from its own real path
- `uv sync` run once in the project (creates `.venv/bin/opc`)
- Daemon running:
  ```bash
  <project>/scripts/daemon.sh start          # pid/port under ~/.opc/
  <project>/scripts/daemon.sh status         # or stop
  ```
- An active runtime — `scripts/opc init <path>` to create, `scripts/opc use <path>` to switch.

## Tasks

```bash
# Submit a task — EH decides the approach; CLI streams SSE events until terminal.
scripts/opc run --brief "Explore how the payment module handles refunds"

# Task-type hint (steers the EH without hardcoding a chain)
scripts/opc run --task implement_feature --brief "Add Alipay for international cards"
scripts/opc run --task bug_fix           --brief "HK booking confirmation emails failing"
scripts/opc run --task payment_change    --brief "Add WeChat Pay as an option"

# Reattach to a running (or historical) task and stream its events
scripts/opc tail TASK-001

# Snapshot: status, results, last event, audit summary
scripts/opc status TASK-001

# Recent tasks (default 20)
scripts/opc tasks
scripts/opc tasks --limit 50

# Recall — fetch a past task's brief, final summary, and written artifacts
scripts/opc recall TASK-001                              # brief + final summary
scripts/opc recall TASK-001 --tree                       # list files under artifacts/TASK-001/
scripts/opc recall TASK-001 --fetch-artifact <relpath>   # read one artifact
```

## Agents

```bash
# Performance tiers (green ≥90%, yellow 75–89%, red <75% — 30-day rolling)
scripts/opc agents
scripts/opc agents --detail

# Initialize or refresh workspaces (CLAUDE.md, settings, skills, repo clones)
scripts/opc init-agent                  # all agents
scripts/opc init-agent dev_agent        # specific agent

# Enrollment flow (founder-gated) — the founder-side counterpart to the
# agent-callback `manage-agent` subcommand.
scripts/opc enrollments --status pending
scripts/opc approve-agent content_writer
scripts/opc reject-agent  content_writer

# Per-agent repos (founder-direct; agents usually go through manage-repo skill)
scripts/opc manage-repo add    --agent dev_agent --repo-name docs --url https://github.com/t-benze/docs.git
scripts/opc manage-repo remove --agent dev_agent --repo-name docs
scripts/opc manage-repo update --agent dev_agent --repo-name docs --url https://github.com/t-benze/docs-v2.git
```

## Knowledge Base

Shared precedents + domain reference under `<runtime>/kb/`. Full rules: `protocol/06-knowledge-base.md`.

```bash
# Read (safe, any agent / any caller)
scripts/opc kb list [--topic <t>] [--type reference|precedent]
scripts/opc kb get <slug>
scripts/opc kb search "<terms>"

# Write (any agent, --from-file only — multi-line opc invocations are blocked
# by the Bash(opc:*) permission matcher, so a file is mandatory)
scripts/opc kb add    --agent <you> --from-file /tmp/kb-<slug>.md
scripts/opc kb update <slug> --agent <you> --from-file /tmp/kb-<slug>.md

# Delete — engineering_head only (per spec §4.6)
scripts/opc kb delete <slug> --author engineering_head

# Regenerate _index.md (usually unnecessary — happens automatically after every write)
scripts/opc kb reindex

# Founder-only: record a precedent from an escalation. Must follow
# `resolve-escalation` in that order (state transition first, KB write second).
scripts/opc kb precedent --task-id TASK-N --as-founder --from-file /tmp/kb-<slug>.md
```

KB payload files use YAML frontmatter (`slug`, `title`, `type`, `topic`, optional `tags`, `source_task`) followed by a markdown body.

## Founder Escalation Resolution

```bash
# State transition — founder marks an escalated task resolved with a disposition.
scripts/opc resolve-escalation TASK-N --disposition "<what the founder decided>"

# Then (if the decision is worth preserving as precedent):
scripts/opc kb precedent --task-id TASK-N --as-founder --from-file /tmp/kb-<slug>.md
```

The two-command flow is mandatory — `kb precedent` will reject writes that aren't backed by a resolved escalation audit entry.

## Audit Log

```bash
scripts/opc audit TASK-007                                  # full entries for a task
scripts/opc audit --agent engineering_head --limit 10       # recent for one agent, any task
scripts/opc audit TASK-007 --action orchestration_step      # only EH decisions
scripts/opc audit --since 2026-04-18T00:00:00Z              # time-filtered
scripts/opc audit TASK-007 --json                           # raw JSON (full payloads)
```

Common action values: `session_start`, `session_end`, `completion_report`, `orchestration_step`, `escalation`, `escalation_resolved`, `verdict`.

## Runtime

```bash
scripts/opc init /path/to/runtime       # register + activate a new runtime directory
scripts/opc use  /path/to/runtime       # switch which runtime the daemon serves
```

Every command operates on whichever runtime is currently active — the CLI does not take a runtime path.

## Common Workflows

**Submit + watch**
```bash
scripts/opc run --brief "…"                              # streams until terminal
```

**Pick up context on a past task**
```bash
scripts/opc recall TASK-012                              # brief + completion summary
scripts/opc recall TASK-012 --tree                       # what did it produce
scripts/opc recall TASK-012 --fetch-artifact report.md   # read a specific artifact
```

**Diagnose a rejected task**
```bash
scripts/opc status TASK-007
scripts/opc audit  TASK-007 --json | jq '.[] | select(.action == "escalation")'
```

**Onboard a new agent proposed by the EH**
```bash
scripts/opc enrollments --status pending
scripts/opc approve-agent <name>        # bootstraps workspace + clones repos
```

**Record a founder-resolved precedent**
```bash
scripts/opc resolve-escalation TASK-N --disposition "…"
# Draft KB entry in /tmp/kb-<slug>.md, then:
scripts/opc kb precedent --task-id TASK-N --as-founder --from-file /tmp/kb-<slug>.md
```

## Safety Rules

- **Safe (no confirmation):** `run`, `tail`, `status`, `tasks`, `recall`, `audit`, `agents`, `enrollments`, `init-agent`, `kb list`, `kb get`, `kb search`, `kb reindex`
- **Confirm with user first:**
  - `use` — changes which runtime the daemon serves (affects all subsequent commands)
  - `approve-agent` / `reject-agent` — irreversible enrollment state changes
  - `manage-repo remove` / `manage-repo update` — mutates agent workspace config
  - `kb add` / `kb update` — writes to shared KB (visible to every agent; hard to un-ring)
  - `kb delete` — destructive, engineering_head only
  - `resolve-escalation` — founder state transition; paired with `kb precedent`
  - `kb precedent` — founder-only KB write tied to a resolved escalation
- **Agent-callback subcommands — do NOT invoke by hand:**
  - `report-completion`, `learning`, `manage-agent`
  - These are meant to run inside an agent's Claude Code session under the `Bash(opc:*)` allow rule. Invoking them manually falsifies audit data and can corrupt scorecards.

## Troubleshooting

- **`Connection refused` on any command** → daemon not running. Start it: `<project>/scripts/daemon.sh start`.
- **`no active runtime`** → `scripts/opc use <path>` (or `scripts/opc init <path>` if new).
- **Task silently ends as `rejected`** → likely a blocked agent callback. Check `scripts/opc audit <id>` for the session_end event; the TASK-007/008/009 post-mortem in the project's CLAUDE.md explains the `Bash(opc:*)` allowlist requirement.
- **`kb precedent` returns 4xx with "no resolved escalation"** → run `scripts/opc resolve-escalation <task-id> --disposition …` first.
- **`No such file or directory`** / `uv: command not found` → install `uv` and ensure the project root resolution is working (set `OPC_PROJECT_DIR` if the skill is in an unusual location). Shim calls `uv --project` under the hood; nothing else is expected on PATH.
