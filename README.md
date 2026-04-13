# OPC — AI-Powered Tourism Organization

A one-person company (OPC) that provides online tourism information and booking services for foreign tourists visiting mainland China, Hong Kong SAR, and Macau SAR. The entire operation is run by AI agents supervised by a single human founder.

## How It Works

The system uses a **custom orchestrator** that dispatches tasks to AI agents running as [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions. Each agent has a persistent workspace, a performance scorecard, and a defined role within the organization.

### Current: Product & Engineering Crew

Four agents work together through an automated review loop:

| Agent | Role |
|-------|------|
| **Engineering Head** | Manager — reviews output, approves/rejects, routes revisions |
| **Product Manager** | Writes specs, triages bugs, prioritizes roadmap |
| **Dev Agent** | Implements features, fixes bugs |
| **Payment Agent** | Proposes payment flow changes |

### Task Flows

**Feature implementation:** Engineering Head → PM writes spec → Dev implements → Engineering Head reviews → approve / revise / reject

**Bug fix:** Engineering Head → PM triages → Dev fixes → Engineering Head reviews → approve / revise / reject

**Payment change:** Engineering Head → Payment Agent drafts proposal → cross-audit (stubbed) → Engineering Head reviews → approve / revise / reject

If an agent's work is rejected twice, the task escalates to the founder.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager)
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated

## Setup

```bash
# Clone the repo
git clone https://github.com/t-benze/my-opc.git
cd my-opc

# Install dependencies
uv sync

# Run tests to verify
uv run pytest tests/ -v
```

## Usage

```bash
# Initialize all agent workspaces (clones repo, loads system prompts)
opc init-agent

# Or initialize a specific agent
opc init-agent dev_agent

# Run a feature implementation
opc run --task implement_feature --brief "Add Alipay support for international cards"

# Fix a bug
opc run --task bug_fix --brief "Payment confirmation emails not sending for HK bookings"

# Propose a payment change
opc run --task payment_change --brief "Add WeChat Pay as alternative payment method"

# Check task status
opc status TASK-001

# List recent tasks
opc tasks

# View agent performance tiers
opc agents
opc agents --detail
```

### Commands

| Command | Description |
|---------|-------------|
| `opc run --task TYPE --brief "..."` | Run a task (`implement_feature`, `bug_fix`, `payment_change`) |
| `opc status TASK-ID` | Show task details, results, and audit log |
| `opc tasks [--limit N]` | List recent tasks (default: 20) |
| `opc agents [--detail]` | Show agent performance tiers and scorecards |
| `opc init-agent [name]` | Initialize agent workspaces (all or specific agent) |

Global flag: `opc --db /path/to/db.sqlite <command>` to use a custom database.

## Configuration

All settings use the `OPC_` environment variable prefix. Defaults work out of the box.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPC_DATA_DIR` | `~/.opc` | Runtime data directory (database, workspaces) |
| `OPC_CLAUDE_CLI_PATH` | `claude` | Path to Claude Code CLI |
| `OPC_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `OPC_DB_PATH` | `opc.db` | SQLite database filename (relative to data dir) |
| `OPC_WORKSPACES_DIR` | `workspaces` | Workspaces dirname (relative to data dir) |
| `OPC_REPO_URL` | *(auto-detected)* | Git repo URL for agent workspace clones |
| `OPC_MAX_REVISION_ROUNDS` | `2` | Max revisions before escalation |
| `OPC_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout (30 min) |
| `OPC_TIER_GREEN_THRESHOLD` | `0.90` | Acceptance rate for green tier |
| `OPC_TIER_YELLOW_THRESHOLD` | `0.75` | Acceptance rate for yellow tier |

Runtime data is stored in `~/.opc/` by default, separate from the source code.

## Performance Tiers

Agents are scored on a rolling 30-day window based on review verdicts from the Engineering Head:

| Tier | Acceptance Rate | Effect |
|------|----------------|--------|
| **Green** | >= 90% | Standard flow, minimal oversight |
| **Yellow** | 75-89% | Extra pre-review step added to task chain |
| **Red** | < 75% | Double review, reduced task scope |

## Architecture

```
src/cli.py (`opc` command)           CLI entry point
        |
        v
src/orchestrator/orchestrator.py     Main loop -- creates tasks, builds chains, runs review loop
        |
        |-- task_router.py           Builds tier-dependent task chains
        |-- executor.py              Spawns `claude -p` sessions, reads completion reports
        |-- revision_loop.py         Tracks revision rounds, escalates at max
        |-- performance_tracker.py   Calculates tiers, updates scorecards
        |-- context_builder.py       Generates CLAUDE.md + settings per agent workspace
        |
        |-- infrastructure/
        |   |-- database.py          SQLite (WAL mode) -- tasks, audit_log, scorecards, task_results
        |   |-- audit_logger.py      Structured event logging
        |
        |-- models.py                Pydantic models + enums
```

Each agent runs in its own persistent workspace under `workspaces/`. The workspace contains:
- `CLAUDE.md` — agent identity, system prompt, pointers to persistent files
- `.claude/settings.json` — permissions and hooks
- `learnings.md` — agent-written insights, periodically consolidated
- `scorecard.md` — human-readable performance summary
- `recent_tasks.md` — rolling task history

## Design Documents

| File | Description |
|------|-------------|
| `01-org-charter.md` | Mission, brand voice, risk tolerance, budget caps, compliance requirements |
| `02-system-prompts-managers.md` | System prompts for 4 manager agents with accountability contracts |
| `03-system-prompts-workers.md` | System prompts for 8 worker agents with accountability contracts |
| `04-escalation-rules.md` | 12 routing rules, manager-resolvable categories, peer audit triggers |
| `05-crewai-blueprint.md` | Blueprint index pointing to 05a-05e |

## Roadmap

- [ ] Content Crew (Content Writer, QA Agent, SEO Agent, Content Manager)
- [ ] Ops Crew (Partner Liaison, Compliance Agent, Operations Manager)
- [ ] Inter-crew communication and cross-crew audits
- [ ] CX Crew (Support Agent for real-time chat)
- [ ] Feishu integration (founder notifications and reply parsing)
- [ ] Founder dashboard (aggregated audit logs, scorecards, escalation summaries)

## License

Private — all rights reserved.
