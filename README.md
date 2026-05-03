# OPC — AI-Powered Tourism Organization

A one-person company (OPC) that provides online tourism information and booking services for foreign tourists visiting Hong Kong SAR and Macau SAR. The entire operation is run by AI agents supervised by a single human founder.

## How It Works

OPC runs as a local **HTTP daemon** that dispatches tasks to AI agents running as coding-agent CLI sessions. The `opc` CLI is a thin client that talks to the daemon. Each agent has a persistent workspace, a performance scorecard, and a defined role within the organization. Executor selection is per-agent: some agents may run on [Claude Code](https://docs.anthropic.com/en/docs/claude-code) while others run on Codex.

### Agent-Driven Organization

The **Engineering Head** (EH) drives all task execution. When you submit a task, the EH analyzes it and decides what to do at each step — handle it directly, delegate to a team member, or escalate to the founder. There are no hardcoded task chains.

Agents are **dynamic** — the EH can propose new agents via the `manage-agent` skill, and the founder approves enrollment before the agent's workspace is bootstrapped. The roster grows organically as the organization needs new capabilities.

The initial team (created via `opc init-agent`) includes Engineering Head, Product Manager, Dev Agent, Payment Agent, and QA Engineer. Additional agents (e.g., Content QA, Content Writer) are enrolled through the enrollment flow.

The EH can delegate multiple steps (e.g., PM writes spec, then Dev implements), explore the codebase itself, or escalate if the task requires human judgment. A max of 10 orchestration steps prevents runaway loops.

### Org-per-Runtime

The org-specific content (charter, escalation rules, teams, agent definitions) lives **per runtime** under `<runtime>/org/`. Each runtime is its own organization — same source code, different brief. A canonical sample tree ships at `examples/orgs/hk-macau-tourism/` for the HK/Macau tourism org. To bootstrap a new runtime today, copy that tree into the runtime path before running `opc init` (the `--from <example>` flag is on the roadmap).

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager)
- At least one supported agent CLI installed and authenticated:
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
  - Codex CLI

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

# 2. Seed your runtime with org content. Today this is a manual copy of the
#    sample tree; the canonical example is the HK/Macau tourism org.
mkdir -p ~/opc-runtime
cp -R examples/orgs/hk-macau-tourism/org ~/opc-runtime/org

# 3. Create and activate the runtime directory. The --slug stamps the
#    runtime's identity into opc.yaml and is required on first init.
opc init ~/opc-runtime --slug hk-tourism

# 4. Initialize all agent workspaces (creates agent.yaml, loads system prompts,
#    copies skills, clones repos declared in agent.yaml)
opc init-agent

# Or initialize a specific agent
opc init-agent dev_agent

# Run a task. The CLI streams live events until done.
opc run --brief "Explore how the payment module handles refunds"

# Route a task to a specific team
opc run --team engineering --brief "Add Alipay support for international cards"
opc run --team content --brief "Write a Macau visa walkthrough for first-time visitors"

# Re-attach to a running task and stream its events
opc tail TASK-001

# Check task details (status, block_kind, note, results, audit log)
opc details TASK-001

# List recent tasks
opc tasks

# View agent performance tiers
opc agents
opc agents --detail
opc agents dev_agent          # one agent's scorecard

# Switch which runtime directory the daemon is serving
opc use ~/another-runtime
```

### Runtime layout

A runtime directory contains both the editable org content and the runtime data the daemon manages:

```
<runtime>/
|-- opc.yaml                           # marker (slug, created_at, schema_version)
|-- opc.db                             # per-runtime SQLite
|-- org/                               # editable org content (you can hand-edit this)
|   |-- charter.md                     # org-level reference doc
|   |-- escalation-rules.md            # when to escalate to founder
|   |-- teams.yaml                     # team layout
|   +-- agents/
|       |-- <name>.md                  # active agents (frontmatter + system prompt)
|       +-- _pending/<name>.md         # awaiting founder approval
|-- workspaces/<agent>/                # per-agent workspace (CLAUDE.md/AGENTS.md, repos, scorecard, learnings)
|-- kb/                                # shared knowledge base (markdown)
+-- talks/                             # founder↔agent transcripts
```

The files under `org/` are the source of truth for the organization. You can hand-edit them between tasks (e.g., to refine an agent's system prompt) — the next `opc init-agent` regenerates the workspace bootstrap accordingly.

### Migrating an old runtime

Runtimes created before the `org/` folder existed had their agent definitions in SQLite. Lift them into the file-based layout with:

```bash
opc migrate-to-org-runtime ~/opc-runtime --slug hk-tourism --i-have-a-backup --apply
```

Make a backup first — the flag is mandatory on purpose. Without `--apply` the command runs in dry-run mode and prints the planned changes.

### Commands

| Command | Description |
|---------|-------------|
| `opc init <path> --slug <slug>` | Create a runtime directory and set it as active (slug required on first init) |
| `opc use <path>` | Switch the daemon's active runtime directory |
| `opc run --brief "..."` | Submit a task and return immediately. The team manager decides the approach. Use `opc tail TASK-ID` to attach. |
| `opc run --team TEAM --brief "..."` | Route a task to a team (e.g., `engineering`, `content`) |
| `opc run --brief-file PATH` | Read the task brief from a file (use this for multi-line briefs); mutually exclusive with `--brief` |
| `opc tail TASK-ID` | Stream live events for a running (or historical) task |
| `opc details TASK-ID [--full]` | Show task details (status, block_kind, note, results, audit log). `--full` prints per-step `output_summary` untruncated. |
| `opc tasks [--limit N]` | List recent tasks (default: 20) |
| `opc agents [name] [--detail]` | Show agent performance tiers (all, or one agent's scorecard) |
| `opc init-agent [name]` | Initialize agent workspaces (all or specific agent) |
| `opc audit TASK-ID [--json]` | View audit log for a task (or filter by `--agent`, `--action`) |
| `opc manage-repo add\|remove\|update` | Add, remove, or update a repo in an agent's workspace |
| `opc manage-agent --from-file F` | Enroll, update, or terminate an agent (used by EH skill) |
| `opc dispatch --from-file F` | Dispatch a new task from inside an open talk (workers self-only; team managers intra-team) |
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

# Approve — bootstraps workspace files, skills, and repo clones
opc approve-agent content_writer

# Or reject
opc reject-agent content_writer
```

Agent names must be lowercase with underscores only (e.g., `content_writer`, `seo_agent`).

To enroll a new developer agent that runs on Codex instead of Claude, the EH's
`manage-agent` payload should include `executor: "codex"`:

```json
{
  "action": "enroll",
  "name": "dev_agent_codex",
  "task_id": "TASK-123",
  "session_id": "sess-abc123",
  "description": "Implements product and platform changes as a Codex-backed developer agent.",
  "system_prompt": "You are the Dev Agent. Your responsibilities are...",
  "executor": "codex",
  "repos": {
    "my-opc": "https://github.com/t-benze/my-opc.git"
  }
}
```

The founder still approves it the same way:

```bash
opc approve-agent dev_agent_codex
```

After approval, the new workspace will have `executor: codex` in `agent.yaml`
and will be bootstrapped with `AGENTS.md` instead of a Claude-only workspace
surface.

### Managing repos

Agents can request repo changes through the `manage-repo` skill, or the founder can manage them directly:

```bash
opc manage-repo add --agent dev_agent --repo-name docs --url https://github.com/user/docs.git
opc manage-repo remove --agent dev_agent --repo-name docs
opc manage-repo update --agent dev_agent --repo-name docs --url https://github.com/user/docs-v2.git
```

### Knowledge base

```bash
opc kb list [--topic <t>] [--type reference|precedent]
opc kb get <slug>
opc kb search "<query>"
opc kb add    --agent <you> --from-file /tmp/kb-<slug>.md
opc kb update <slug> --agent <you> --from-file /tmp/kb-<slug>.md
opc kb delete <slug> --agent <you> --confirm [--as-founder]
opc kb reindex
opc kb precedent --task-id <id> --decision approve|reject --rationale "..." [--slug <s>]

opc resolve-escalation --task-id <id> --decision approve|reject --rationale "..."
opc revisit TASK-052 [--note "..." | --note-file PATH] [--session-timeout-seconds N]   # founder: spawn a new root that inherits a terminal predecessor's brief
```

`opc revisit` takes any task id in a lineage, walks to its root, and — if the
root ended `failed`, `failed-cancelled`, `blocked(escalated)`, or `completed` —
spawns a fresh root inheriting the original brief and team. The old tree
stays frozen (read-only history); the new root's Engineering Head gets a
prompt-header pointer back to it so it can inspect what happened via
`opc details` / `opc audit` / `opc recall`.

Revisit is **TTY-gated** — no `--yes` bypass, no scripted invocations. The CLI
refuses if stdin/stdout aren't a terminal and requires a `y` at the
`Continue? [y/N]` confirmation prompt. Only humans can trigger it.

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
| `OPC_CODEX_CLI_PATH` | `codex` | Path to Codex CLI |
| `OPC_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `OPC_MAX_ORCHESTRATION_STEPS` | `50` | Max EH decision steps before escalation |
| `OPC_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout (30 min) — global default; can be overridden per-runtime in `<runtime>/org/config.yaml` and per-task via `opc revisit --session-timeout-seconds <int>` |
| `OPC_TIER_GREEN_THRESHOLD` | `0.90` | Acceptance rate for green tier |
| `OPC_TIER_YELLOW_THRESHOLD` | `0.75` | Acceptance rate for yellow tier |

### Per-Agent Configuration

Each agent has an `agent.yaml` in its workspace (`<runtime>/workspaces/<agent>/agent.yaml`). Created automatically by `opc init-agent` with empty defaults:

```yaml
executor: claude
repos:
  web-app: https://github.com/user/web-app.git
  docs: https://github.com/user/docs.git
```

`executor` may be `claude` or `codex`. If omitted in an older workspace, it defaults to `claude`.

Repos are cloned into the agent's workspace on `opc init-agent` and auto-pulled before each task.

### Session timeout overrides

The per-session timeout (default 1800s / 30 min) is resolved in three layers, highest precedence first:

1. **Per-task**: pass `--session-timeout-seconds <int>` when revisiting a stuck task — `opc revisit <task-id> --session-timeout-seconds 7200`. The override is stored on the new root task and inherited by every child the orchestrator spawns from it (delegated children + auto-revisits + later founder-revisits when the flag is omitted). Use this when a single failing lineage needs longer (or shorter) sessions than the rest of the runtime.
2. **Per-runtime (org)**: create `<runtime>/org/config.yaml` with `session_timeout_seconds: <int>`. Use this to bump every agent in this runtime above the global default.
3. **Global default**: `OPC_SESSION_TIMEOUT_SECONDS` env var (or the built-in 1800s).

A missing file or `null` value at any layer falls through to the next layer. Values must be positive integers.

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

- `agent.yaml` — per-agent configuration (`executor`, repos, etc.)
- `CLAUDE.md` — Claude workspaces: agent identity, system prompt, available repos
- `AGENTS.md` — Codex workspaces: agent identity, system prompt, available repos
- `.claude/settings.json` — Claude workspaces: permissions and git-pull hooks
- `.claude/skills/` — shared skills copied into the workspace
- `repos/` — git clones of repositories configured in `agent.yaml` (auto-pulled before each task)
- `learnings.md` — agent-written insights from past tasks (appended via `opc learning`)
- `task_history.md` — rolling per-agent task history

## Roadmap

- [ ] Content Team (Content Writer, Content QA, SEO Agent, Content Manager)
- [ ] Ops Team (Partner Liaison, Compliance Agent, Operations Manager)
- [ ] Inter-team communication and cross-team audits
- [ ] CX Team (Support Agent for real-time chat)
- [ ] Founder dashboard (aggregated audit logs, scorecards, escalation summaries)

## License

Private — all rights reserved.
