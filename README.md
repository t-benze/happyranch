# OPC — AI-Powered Tourism Organization

A one-person company (OPC) that provides online tourism information and booking services for foreign tourists visiting mainland China, Hong Kong SAR, and Macau SAR. The entire operation is run by AI agents supervised by a single human founder.

## How It Works

OPC runs as a local **HTTP daemon** that dispatches tasks to AI agents running as [Claude Code](https://docs.anthropic.com/en/docs/claude-code) sessions. The `opc` CLI is a thin client that talks to the daemon. Each agent has a persistent workspace, a performance scorecard, and a defined role within the organization.

### Agent-Driven Organization

The **Engineering Head** (EH) drives all task execution. When you submit a task, the EH analyzes it and decides what to do at each step — handle it directly, delegate to a team member, or escalate to the founder. There are no hardcoded task chains.

Agents are **dynamic** — the EH can propose new agents via the `manage-agent` skill, and the founder approves enrollment before the agent's workspace is bootstrapped. The roster grows organically as the organization needs new capabilities.

The initial crew (created via `opc init-agent`) includes Engineering Head, Product Manager, Dev Agent, and Payment Agent. Additional agents (e.g., QA Agent, Content Writer) are enrolled through the enrollment flow.

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
| `opc audit TASK-ID [--json]` | View audit log for a task (or filter by `--agent`, `--action`) |
| `opc manage-repo add\|remove\|update` | Add, remove, or update a repo in an agent's workspace |
| `opc manage-agent --from-file F` | Enroll, update, or terminate an agent (used by EH skill) |
| `opc enrollments [--status S]` | List agent enrollment requests |
| `opc approve-agent <name>` | Approve a pending enrollment and bootstrap workspace |
| `opc reject-agent <name>` | Reject a pending enrollment |

The CLI does not take a runtime path — every command operates on whichever runtime is currently active. Use `opc use` to switch.

### Enrolling new agents

The EH can propose new agents during task execution using the `manage-agent` skill. Enrollment requires founder approval:

```bash
# The EH submits an enrollment request (happens automatically during tasks)
# opc manage-agent --from-file /tmp/manage-agent-enroll.json

# Founder reviews pending enrollments
opc enrollments --status pending

# Approve — bootstraps workspace (CLAUDE.md, settings, skills, repo clones)
opc approve-agent content_writer

# Or reject
opc reject-agent content_writer
```

Agent names must be lowercase with underscores only (e.g., `content_writer`, `seo_agent`).

### Managing repos

Agents can request repo changes through the `manage-repo` skill, or the founder can manage them directly:

```bash
opc manage-repo add --agent dev_agent --repo-name docs --url https://github.com/user/docs.git
opc manage-repo remove --agent dev_agent --repo-name docs
opc manage-repo update --agent dev_agent --repo-name docs --url https://github.com/user/docs-v2.git
```

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
- `.claude/skills/` — `start-task`, `make-worktree`, `manage-repo`, and `manage-agent` skills
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
