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
| `OPC_REPOS` | *(auto-detected)* | Git repos for agent clones, JSON dict: `{"name": "url"}` |
| `OPC_MAX_REVISION_ROUNDS` | `2` | Max revisions before escalation |
| `OPC_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout (30 min) |
| `OPC_TIER_GREEN_THRESHOLD` | `0.90` | Acceptance rate for green tier |
| `OPC_TIER_YELLOW_THRESHOLD` | `0.75` | Acceptance rate for yellow tier |

Multi-repo example in `.env`:
```
OPC_REPOS={"my-opc": "https://github.com/t-benze/my-opc.git", "web-app": "https://github.com/t-benze/web-app.git"}
```

If `OPC_REPOS` is not set, `opc init-agent` auto-detects the current git remote as a single repo.

Runtime data is stored in `~/.opc/` by default, separate from the source code.

## Performance Tiers

Agents are scored on a rolling 30-day window based on review verdicts from the Engineering Head:

| Tier | Acceptance Rate | Effect |
|------|----------------|--------|
| **Green** | >= 90% | Standard flow, minimal oversight |
| **Yellow** | 75-89% | Extra pre-review step added to task chain |
| **Red** | < 75% | Double review, reduced task scope |

## Agent Workspaces

Each agent runs in its own persistent workspace under `~/.opc/workspaces/`. After running `opc init-agent`, each workspace contains:

- `CLAUDE.md` — agent identity, system prompt, available repos
- `.claude/settings.json` — permissions and git-pull hooks
- `repos/` — git clones of configured repositories (auto-pulled before each task)
- `learnings.md` — agent-written insights from past tasks
- `scorecard.md` — performance summary (updated by orchestrator)
- `recent_tasks.md` — rolling task history

## Roadmap

- [ ] Content Crew (Content Writer, QA Agent, SEO Agent, Content Manager)
- [ ] Ops Crew (Partner Liaison, Compliance Agent, Operations Manager)
- [ ] Inter-crew communication and cross-crew audits
- [ ] CX Crew (Support Agent for real-time chat)
- [ ] Feishu integration (founder notifications and reply parsing)
- [ ] Founder dashboard (aggregated audit logs, scorecards, escalation summaries)

## License

Private — all rights reserved.
