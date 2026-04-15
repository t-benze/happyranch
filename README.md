# OPC — AI-Powered Tourism Organization

A one-person company (OPC) that provides online tourism information and booking services for foreign tourists visiting mainland China, Hong Kong SAR, and Macau SAR. The entire operation is run by AI agents supervised by a single human founder.

## How It Works

OPC runs as a local **HTTP daemon** that dispatches tasks to AI agents running as [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions. The `opc` CLI is a thin client that talks to the daemon. Each agent has a persistent workspace, a performance scorecard, and a defined role within the organization.

### Current: Product & Engineering Crew

The **Engineering Head** (EH) drives all task execution. When you submit a task, the EH analyzes it and decides what to do at each step — handle it directly, delegate to a team member, or escalate to the founder. There are no hardcoded task chains.

| Agent | Role |
|-------|------|
| **Engineering Head** | Manager — analyzes tasks, delegates work, reviews results |
| **Product Manager** | Writes specs, triages bugs, prioritizes roadmap |
| **Dev Agent** | Implements features, fixes bugs |
| **Payment Agent** | Proposes payment flow changes |

The EH can delegate multiple steps (e.g., PM writes spec, then Dev implements), explore the codebase itself, or escalate if the task requires human judgment. A max of 10 orchestration steps prevents runaway loops.

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
# 1. Start the daemon (once per machine). It listens on localhost and
#    stores its auth token + runtime registry under ~/.opc/.
scripts/daemon.sh start

# 2. Create and activate a runtime directory (stores database, agent workspaces)
opc init ~/opc-runtime

# 3. Initialize all agent workspaces (creates agent.yaml, loads system prompts,
#    copies skills, clones repos declared in agent.yaml)
opc init-agent

# Or initialize a specific agent
opc init-agent dev_agent

# Run a task (EH decides the approach). The CLI streams live events until done.
opc run --brief "Explore how the payment module handles refunds"

# Provide a task type hint to guide the EH
opc run --task implement_feature --brief "Add Alipay support for international cards"
opc run --task bug_fix --brief "Payment confirmation emails not sending for HK bookings"
opc run --task payment_change --brief "Add WeChat Pay as alternative payment method"

# Re-attach to a running task and stream its events
opc tail TASK-001

# Check task status
opc status TASK-001

# List recent tasks
opc tasks

# View agent performance tiers
opc agents
opc agents --detail

# Switch which runtime directory the daemon is serving
opc use ~/another-runtime
```

### Commands

| Command | Description |
|---------|-------------|
| `opc init <path>` | Create a runtime directory and set it as active |
| `opc use <path>` | Switch the daemon's active runtime directory |
| `opc run --brief "..."` | Submit a task and stream its events (EH decides approach) |
| `opc run --task TYPE --brief "..."` | Run with task type hint (`general`, `implement_feature`, `bug_fix`, `payment_change`) |
| `opc tail TASK-ID` | Stream live events for a running (or historical) task |
| `opc status TASK-ID` | Show task details, results, and audit log |
| `opc tasks [--limit N]` | List recent tasks (default: 20) |
| `opc agents [--detail]` | Show agent performance tiers and scorecards |
| `opc init-agent [name]` | Initialize agent workspaces (all or specific agent) |

The CLI does not take a runtime path — every command operates on whichever runtime is currently active. Use `opc use` to switch.

### Managing the daemon

`scripts/daemon.sh` is a tiny supervisor that records the pid/port under `~/.opc/`:

```bash
scripts/daemon.sh start    # start in background
scripts/daemon.sh status   # check if running
scripts/daemon.sh stop     # graceful shutdown
```

## Configuration

Operational settings use the `OPC_` environment variable prefix. Runtime paths (database, workspaces) are derived from the runtime directory.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPC_CLAUDE_CLI_PATH` | `claude` | Path to Claude Code CLI |
| `OPC_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `OPC_MAX_ORCHESTRATION_STEPS` | `10` | Max EH decision steps before escalation |
| `OPC_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout (30 min) |
| `OPC_TIER_GREEN_THRESHOLD` | `0.90` | Acceptance rate for green tier |
| `OPC_TIER_YELLOW_THRESHOLD` | `0.75` | Acceptance rate for yellow tier |

### Per-Agent Configuration

Each agent has an `agent.yaml` in its workspace (`<runtime>/workspaces/<agent>/agent.yaml`). Created automatically by `opc init-agent` with empty defaults:

```yaml
repos:
  web-app: https://github.com/user/web-app.git
  docs: https://github.com/user/docs.git
```

Repos are cloned into the agent's workspace on `opc init-agent` and auto-pulled before each task.

## Performance Tiers

Agents are scored on a rolling 30-day window based on the Engineering Head's verdicts after delegation:

| Tier | Acceptance Rate | Effect |
|------|----------------|--------|
| **Green** | >= 90% | Full capabilities, EH trusts the agent |
| **Yellow** | 75-89% | EH sees reduced trust, may add extra review |
| **Red** | < 75% | EH sees low trust, may avoid delegating to this agent |

Tier information is exposed to the EH in its capabilities prompt, so it influences delegation decisions naturally.

## Agent Workspaces

Each agent runs in its own persistent workspace inside the runtime directory. After running `opc init-agent`, each workspace contains:

- `agent.yaml` — per-agent configuration (repos, etc.)
- `CLAUDE.md` — agent identity, system prompt, available repos
- `.claude/settings.json` — permissions and git-pull hooks
- `.claude/skills/` — `start-task` and `make-worktree` skills that the agent runs during each session
- `repos/` — git clones of repositories configured in `agent.yaml` (auto-pulled before each task)
- `learnings.md` — agent-written insights from past tasks (appended via `opc learning`)
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
