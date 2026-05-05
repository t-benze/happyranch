# OPC — Multi-Agent Org Runtime

OPC is an **org-agnostic runtime** for operating a multi-agent organization supervised by a single human founder. The repo provides the system kernel; the *organization* it runs — charter, teams, agents, escalation rules — is loaded per-runtime from `<runtime>/orgs/<slug>/org/`.

A canonical sample org shipped at `examples/orgs/hk-macau-tourism/` runs a one-person tourism company serving foreign visitors to Hong Kong SAR and Macau SAR. Treat it as the reference shape when bootstrapping a new org.

## How It Works

OPC runs as a local **HTTP daemon** that dispatches tasks to AI agents running as coding-agent CLI sessions. The `opc` CLI is a thin client that talks to the daemon. Each agent has a persistent workspace, a performance scorecard, and a defined role within its org. Executor selection is per-agent: agents may run on [Claude Code](https://docs.anthropic.com/en/docs/claude-code), Codex, or opencode.

A single runtime container hosts **multiple orgs** under `<runtime>/orgs/<slug>/`, each with its own DB, workspaces, KB, and talks. One daemon serves them all concurrently.

### Manager-driven orchestration

Each org has one or more **team managers** that drive task execution. When you submit a task, the manager analyzes it and decides what to do at each step — handle it directly, delegate to a team member, or escalate to the founder. There are no hardcoded task chains. `OPC_MAX_ORCHESTRATION_STEPS` (default 50) caps runaway loops.

### Dynamic agents

Agents are dynamic — a manager can propose new agents via the `manage-agent` skill, and the founder approves enrollment before the agent's workspace is bootstrapped. The roster grows organically as the org needs new capabilities.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- At least one supported agent CLI installed and authenticated:
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
  - Codex CLI
  - opencode CLI

## Setup

```bash
git clone https://github.com/t-benze/my-opc.git
cd my-opc
uv sync
uv run pytest tests/ -v
```

## Quick Start

This walks through setting up a runtime container and materializing the canonical HK/Macau tourism sample org.

```bash
# 1. Start the daemon (once per machine). It listens on localhost and
#    stores its auth token + runtime registry under ~/.opc/.
scripts/daemon.sh start

# 2. Create and activate a runtime container. Slugless — orgs live as
#    subdirectories under <runtime>/orgs/<slug>/.
opc init ~/opc-runtime

# 3. Materialize an org from a sample tree.
opc orgs init hk-macau-tourism --from examples/orgs/hk-macau-tourism

# 4. (Optional) Set the default org so you don't pass --org on every command.
export OPC_ORG_SLUG=hk-macau-tourism

# 5. Initialize agent workspaces (creates agent.yaml, generates bootstrap docs,
#    copies skills, clones repos declared in agent.yaml).
opc init-agent

# 6. Run a task. The CLI streams live events until done.
opc run --brief "Explore how the payment module handles refunds"

# Re-attach to a running task and stream events
opc tail TASK-001

# Check task details (status, block_kind, note, results, audit log)
opc details TASK-001

# List recent tasks and view performance tiers
opc tasks
opc agents
```

## Multi-org operation

A runtime container can host multiple orgs side by side. Per-org commands take `--org <slug>` (or honor `OPC_ORG_SLUG`). Container-level commands operate on the container as a whole.

```bash
opc orgs                                              # list orgs in the active container
opc orgs init my-other-org --from /path/to/example    # materialize a second org
opc use ~/another-runtime                             # switch which container the daemon serves
opc orgs unload <slug>                                # detach an org (does not delete files)
```

Slug resolution for per-org commands: explicit `--org <slug>` flag > `OPC_ORG_SLUG` env > auto-infer (only if exactly one org exists in the container) > error.

### Runtime layout

```
<runtime>/
|-- opc.yaml                           # container marker (schema_version: 2)
+-- orgs/
    +-- <slug>/
        |-- opc.db                     # per-org SQLite
        |-- org/                       # editable org content (you can hand-edit this)
        |   |-- charter.md
        |   |-- escalation-rules.md
        |   |-- teams.yaml
        |   |-- config.yaml            # optional org-level overrides
        |   +-- agents/
        |       |-- <name>.md          # active agents
        |       +-- _pending/<name>.md # awaiting founder approval
        |-- workspaces/<agent>/        # per-agent workspace
        |-- kb/                        # per-org knowledge base
        +-- talks/                     # founder<->agent transcripts
```

The files under `org/` are the source of truth for that organization. You can hand-edit them between tasks (e.g., to refine an agent's system prompt) — the next `opc init-agent` regenerates the workspace bootstrap accordingly.

### Migrating older runtimes

| From | To | Command |
|------|----|---------|
| v0 (DB-backed agent enrollments) | v1 (file-based `<runtime>/org/`) | `opc migrate-to-org-runtime <path> --slug <slug> --i-have-a-backup --apply` |
| v1 (single-org, flat) | v2 (multi-org, `orgs/<slug>/`) | `opc migrate-to-multi-org <path> --i-have-a-backup --apply` |

Both migrations are TTY-gated and refuse `--yes` bypass. Make a backup first — the `--i-have-a-backup` flag is mandatory on purpose. Without `--apply`, both run dry and print the planned changes. A v0 runtime needs both migrations in sequence to reach v2.

## Commands

Every per-org command takes `--org <slug>`; container-level commands do not.

### Container-level

| Command | Description |
|---------|-------------|
| `opc init <path>` | Create a runtime container and set it as active (slugless) |
| `opc use <path>` | Switch the daemon's active container |
| `opc orgs` | List orgs in the active container |
| `opc orgs init <slug> [--from <example-tree>]` | Materialize a new org |
| `opc orgs unload <slug>` | Detach an org from the daemon (does not delete files) |
| `opc migrate-to-multi-org <path> --i-have-a-backup --apply` | v1 -> v2 in place |

### Per-org

| Command | Description |
|---------|-------------|
| `opc run --org <slug> --brief "..."` | Submit a task. The team manager decides the approach. |
| `opc run --org <slug> --team TEAM --brief "..."` | Route a task to a specific team |
| `opc run --org <slug> --brief-file PATH` | Read the task brief from a file (multi-line briefs); mutually exclusive with `--brief` |
| `opc tail --org <slug> TASK-ID` | Stream live events for a running (or historical) task |
| `opc details --org <slug> TASK-ID [--full]` | Task details. `--full` skips per-step truncation. |
| `opc tasks --org <slug> [--limit N]` | List recent tasks (default: 20) |
| `opc agents --org <slug> [name] [--detail]` | Show agent performance tiers |
| `opc init-agent --org <slug> [name]` | Initialize agent workspaces (all or one) |
| `opc audit --org <slug> TASK-ID [--json]` | View audit log (or filter by `--agent`, `--action`, `--since`, `--limit`) |
| `opc recall --org <slug> TASK-ID [--tree] [--fetch-artifact <relpath>]` | Fetch task brief + artifact tree/content |

The CLI does not take a runtime path — every command operates on whichever container is currently active. Use `opc use` to switch.

### Founder primitives

| Command | Description |
|---------|-------------|
| `opc resolve-escalation --org <slug> --task-id <id> --decision approve\|reject --rationale "..."` | Resolve an escalated task. Approve resumes; reject fails and cascades. |
| `opc revisit --org <slug> <task-id> [--note "..." \| --note-file PATH] [--session-timeout-seconds N]` | Spawn a new root inheriting a terminal predecessor's brief. **TTY-gated.** |
| `opc enrollments --org <slug> [--status pending]` | List agent enrollment requests |
| `opc approve-agent --org <slug> <name>` | Approve a pending enrollment and bootstrap workspace |
| `opc reject-agent --org <slug> <name>` | Reject a pending enrollment |

`opc revisit` walks any task's lineage to its root and — if the root ended `failed`, `failed-cancelled`, `blocked(escalated)`, or `completed` — spawns a fresh root inheriting the original brief and team. The old tree stays frozen (read-only history); the new root's manager gets a prompt-header pointer back to it so it can inspect what happened via `opc details` / `opc audit` / `opc recall`. Only humans can trigger it.

### Knowledge base

```bash
opc kb list      --org <slug> [--topic <t>] [--type reference|precedent]
opc kb get       --org <slug> <slug>
opc kb search    --org <slug> "<query>"
opc kb add       --org <slug> --agent <you> --from-file /tmp/kb-<slug>.md
opc kb update    --org <slug> <slug> --agent <you> --from-file /tmp/kb-<slug>.md
opc kb delete    --org <slug> <slug> --agent <you> --confirm [--as-founder]
opc kb reindex   --org <slug>
opc kb precedent --org <slug> --task-id <id> --decision approve|reject --rationale "..." --as-founder
```

Any agent reads/writes; team managers delete (audited); the founder overrides delete via `--as-founder` and is the only role that can record precedents.

### Talks

Founder<->agent conversations:

```bash
opc talk start   --org <slug> --agent <name>
opc talk resume  --org <slug> --talk-id TALK-001
opc talk abandon --org <slug> --talk-id TALK-001 [--reason <why>]
opc talk end     --org <slug> --talk-id TALK-001 --from-file /tmp/talk-end-TALK-001.json
opc talk status  --org <slug> [--agent <name>]
opc talk list    --org <slug> [--agent <name>] [--limit N]
opc talk show    --org <slug> TALK-001
```

### Managing repos

Agents can request repo changes through the `manage-repo` skill, or the founder can manage them directly:

```bash
opc manage-repo --org <slug> add    --agent <name> --repo-name docs --url https://github.com/user/docs.git
opc manage-repo --org <slug> remove --agent <name> --repo-name docs
opc manage-repo --org <slug> update --agent <name> --repo-name docs --url https://github.com/user/docs-v2.git
```

### Enrolling new agents

A manager can propose new agents during task execution using the `manage-agent` skill. Enrollment requires founder approval:

```bash
opc enrollments --org <slug> --status pending     # founder reviews
opc approve-agent --org <slug> content_writer     # bootstraps workspace, skills, repo clones
opc reject-agent  --org <slug> content_writer
```

Agent names must be lowercase with underscores only (e.g., `content_writer`, `seo_agent`).

To enroll an agent that runs on Codex or opencode instead of Claude, the manager's `manage-agent` payload sets `executor` accordingly:

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

After approval, the new workspace will have `executor: codex` (or `opencode`) in `agent.yaml` and will be bootstrapped with `AGENTS.md` instead of a Claude-only workspace surface.

### Managing the daemon

`scripts/daemon.sh` is a tiny supervisor that records the pid/port under `~/.opc/`:

```bash
scripts/daemon.sh start    # start in background
scripts/daemon.sh status   # check if running
scripts/daemon.sh stop     # graceful shutdown
```

## Configuration

Operational settings use the `OPC_` env prefix. Runtime paths are derived from the runtime container.

| Variable | Default | Description |
|----------|---------|-------------|
| `OPC_CLAUDE_CLI_PATH` | `claude` | Path to Claude Code CLI |
| `OPC_CODEX_CLI_PATH` | `codex` | Path to Codex CLI |
| `OPC_OPENCODE_CLI_PATH` | `opencode` | Path to opencode CLI |
| `OPC_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `OPC_MAX_ORCHESTRATION_STEPS` | `50` | Max manager decision steps before escalation |
| `OPC_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout (30 min) — global default; see overrides below |
| `OPC_TIER_GREEN_THRESHOLD` | `0.90` | Acceptance rate for green tier |
| `OPC_TIER_YELLOW_THRESHOLD` | `0.75` | Acceptance rate for yellow tier |
| `OPC_ORG_SLUG` | _(unset)_ | Default org slug for per-org CLI commands |

### Per-Agent Configuration

Each agent has an `agent.yaml` in its workspace (`<runtime>/orgs/<slug>/workspaces/<agent>/agent.yaml`). Created automatically by `opc init-agent` with empty defaults:

```yaml
executor: claude
repos:
  web-app: https://github.com/user/web-app.git
  docs: https://github.com/user/docs.git
```

`executor` may be `claude`, `codex`, or `opencode`. If omitted in an older workspace, it defaults to `claude`.

Repos are cloned into the agent's workspace on `opc init-agent` and auto-pulled before each task.

### Session timeout overrides

The per-session timeout (default 1800s / 30 min) is resolved in three layers, highest precedence first:

1. **Per-task**: pass `--session-timeout-seconds <int>` when revisiting a stuck task: `opc revisit --org <slug> <task-id> --session-timeout-seconds 7200`. The override is stored on the new root and inherited by every child the orchestrator spawns from it (delegated children, auto-revisits, later founder-revisits when the flag is omitted).
2. **Per-org**: create `<runtime>/orgs/<slug>/org/config.yaml` with `session_timeout_seconds: <int>`. Use this to bump every agent in this org above the global default.
3. **Global default**: `OPC_SESSION_TIMEOUT_SECONDS` env var (or the built-in 1800s).

A missing file or `null` value at any layer falls through to the next layer. Values must be positive integers.

## Performance Tiers

Agents are scored on a rolling 30-day window based on the team manager's verdicts after delegation:

| Tier | Acceptance Rate | Effect |
|------|----------------|--------|
| **Green** | >= 90% | Full capabilities; the manager trusts the agent |
| **Yellow** | 75-89% | Reduced trust; the manager may add extra review |
| **Red** | < 75% | Low trust; the manager may avoid delegating |

Tier information is exposed to the manager in its capabilities prompt, so it influences delegation decisions naturally.

## Agent Workspaces

Each agent runs in its own persistent workspace inside the org directory. After `opc init-agent`, each workspace contains:

- `agent.yaml` — per-agent config (`executor`, repos, ...)
- `CLAUDE.md` (Claude) or `AGENTS.md` (Codex/opencode) — agent identity, system prompt, available repos
- `.claude/settings.json` + `.claude/skills/` (Claude) — permissions and skills
- `.agents/skills/` (Codex/opencode) — shared skills tree
- `opencode.json` (opencode only) — `permission.bash` map
- `repos/` — git clones of repositories from `agent.yaml` (auto-pulled before each task)
- `learnings.md` — agent-written insights from past tasks (appended via `opc learning`)
- `task_history.md` — rolling per-agent task history

## Roadmap

- [ ] Inter-team communication and cross-team handoff
- [ ] Founder dashboard (aggregated audit logs, scorecards, escalation summaries)
- [ ] Persistent agents (long-running loops for patterns that don't fit single-task batch execution, e.g., real-time customer chat)

## License

Private — all rights reserved.
