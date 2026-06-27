# HappyRanch — Multi-Agent Org Runtime

HappyRanch is an **org-agnostic runtime** for operating a multi-agent organization supervised by a single human founder. The repo provides the system kernel; the *organization* it runs — charter, teams, agents, escalation rules — is loaded per-runtime from `<runtime>/orgs/<slug>/org/`.

A canonical sample org shipped at `examples/orgs/hk-macau-tourism/` runs a one-person tourism company serving foreign visitors to Hong Kong SAR and Macau SAR. Treat it as the reference shape when bootstrapping a new org.

## How It Works

HappyRanch runs as a local **HTTP daemon** that dispatches tasks to AI agents running as coding-agent CLI sessions. The `happyranch` CLI is a thin client that talks to the daemon. Each agent has a persistent workspace and a defined role within its org. Executor selection is per-agent: agents may run on [Claude Code](https://docs.anthropic.com/en/docs/claude-code), Codex, opencode, or Pi.

A single runtime container hosts **multiple orgs** under `<runtime>/orgs/<slug>/`, each with its own DB, workspaces, KB, and threads. One daemon serves them all concurrently.

### Manager-driven orchestration

Each org has one or more **team managers** that drive task execution. When you submit a task, the manager analyzes it and decides what to do at each step — handle it directly, delegate to a team member, or escalate to the founder. There are no hardcoded task chains. `HAPPYRANCH_MAX_ORCHESTRATION_STEPS` (default 50) caps runaway loops.

### Dynamic agents

Agents are dynamic — a manager can propose new agents via the `manage-agent` skill, and the founder approves enrollment before the agent's workspace is bootstrapped. The roster grows organically as the org needs new capabilities.

## Requirements

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
uv run pytest tests/ -v
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

# 3. Optional but recommended: initialize the runtime-global system assistant.
#    This also verifies that at least one supported agentic CLI works in an
#    interactive PTY session.
happyranch assistant init
happyranch assistant

# 4. Materialize an org from a sample tree.
happyranch orgs init hk-macau-tourism --from examples/orgs/hk-macau-tourism

# 5. (Optional) Set the default org so you don't pass --org on every command.
export HAPPYRANCH_ORG_SLUG=hk-macau-tourism

# 6. Initialize agent workspaces (creates agent.yaml, generates bootstrap docs,
#    copies skills, clones repos declared in agent.yaml).
happyranch init-agent

# 7. Run a task. The CLI streams live events until done.
happyranch run --brief "Explore how the payment module handles refunds"

# Re-attach to a running task and stream events
happyranch tail TASK-001

# Check task details (status, block_kind, note, results, audit log)
happyranch details TASK-001

# List recent tasks
happyranch tasks
```

## Multi-org operation

A runtime container can host multiple orgs side by side. Per-org commands take `--org <slug>` (or honor `HAPPYRANCH_ORG_SLUG`). Container-level commands operate on the container as a whole.

```bash
happyranch orgs                                              # list orgs in the active container
happyranch orgs init my-other-org --from /path/to/example    # materialize a second org
happyranch use ~/another-runtime                             # switch which container the daemon serves
happyranch orgs unload <slug>                                # detach an org (does not delete files)
```

Slug resolution for per-org commands: explicit `--org <slug>` flag > `HAPPYRANCH_ORG_SLUG` env > auto-infer (only if exactly one org exists in the container) > error.

### Runtime layout

```
<runtime>/
|-- happyranch.yaml                           # container marker (schema_version: 2)
+-- orgs/
    +-- <slug>/
        |-- happyranch.db                     # per-org SQLite
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

        |-- threads/                   # multi-agent workchannel transcripts
        |-- jobs/                      # JOB-NNN.{out,err,script}
        +-- artifacts/                 # org-shared blob store
```

The files under `org/` are the source of truth for that organization. You can hand-edit them between tasks (e.g., to refine an agent's system prompt) — the next `happyranch init-agent` regenerates the workspace bootstrap accordingly.

## System Assistant

The system assistant is a runtime-global agentic CLI you can attach to for ad-hoc help across the whole container. It is **not** an org agent; it lives under `<runtime>/system/assistant/` and onboards by self-registration — you pick whichever CLI you already have installed (`claude`, `codex`, `opencode`, `pi`, or another) and let it register itself.

```bash
# 1. Prepare (or repair) the runtime-global assistant workspace at
#    <runtime>/system/assistant/workspace. When no assistant is configured
#    yet, this prints the next steps to register one.
happyranch assistant init
happyranch assistant init --reconfigure   # redo config for an already-configured assistant
happyranch assistant init --repair        # fix a broken/partial config
#    --reconfigure and --repair are mutually exclusive.

# 2. Open your own agentic CLI (claude, codex, opencode, pi, ...) IN that
#    workspace and ask it to register itself. It calls back:
happyranch assistant register --from-file <payload.json>
#    The payload declares an agent-chosen {executor, command, argv}.
#    Flag form instead of a file:
happyranch assistant register --executor claude --command claude --argv '["claude"]'

# 3. Attach over the WebSocket PTY (attach is the default subcommand, so
#    bare `happyranch assistant` does the same thing).
happyranch assistant            # == happyranch assistant attach
happyranch assistant attach
happyranch assistant status     # show configuration state and selected executor
```

On register, the daemon validates the payload structurally only — non-empty fields and `shutil.which(argv[0])` resolves (no allowlist, no absolute-path requirement, no `$PATH` guard) — then auto-configures with no separate approval. See [`docs/agent-guides/runtime-and-configuration.md`](docs/agent-guides/runtime-and-configuration.md) for the full configuration contract.

## Commands

Every per-org command takes `--org <slug>`; container-level commands do not.

### Container-level

| Command | Description |
|---------|-------------|
| `happyranch init <path>` | Create a runtime container and set it as active (slugless) |
| `happyranch use <path>` | Switch the daemon's active container |
| `happyranch orgs` | List orgs in the active container |
| `happyranch orgs init <slug> [--from <example-tree>]` | Materialize a new org |
| `happyranch orgs unload <slug>` | Detach an org from the daemon (does not delete files) |

### Per-org

| Command | Description |
|---------|-------------|
| `happyranch run --org <slug> --brief "..."` | Submit a task. The team manager decides the approach. |
| `happyranch run --org <slug> --team TEAM --brief "..."` | Route a task to a specific team |
| `happyranch run --org <slug> --brief-file PATH` | Read the task brief from a file (multi-line briefs); mutually exclusive with `--brief` |
| `happyranch tail --org <slug> TASK-ID` | Stream live events for a running (or historical) task |
| `happyranch details --org <slug> TASK-ID [--full]` | Task details. `--full` skips per-step truncation. |
| `happyranch tasks --org <slug> [--limit N]` | List recent tasks (default: 20) |
| `happyranch init-agent --org <slug> [name]` | Initialize agent workspaces (all or one) |
| `happyranch audit --org <slug> TASK-ID [--json]` | View audit log (or filter by `--agent`, `--action`, `--since`, `--limit`) |
| `happyranch tokens --org <slug> [--task-id X --agent Y --since DATE --limit N]` | Per-session token usage; `--by-agent` / `--by-task` for rollups |
| `happyranch recall --org <slug> TASK-ID [--tree] [--fetch-output]` | Fetch task brief, full subtask tree (`--tree`), and (optionally) output file bodies (`--fetch-output`) |

The CLI does not take a runtime path — every command operates on whichever container is currently active. Use `happyranch use` to switch.

### Founder primitives

| Command | Description |
|---------|-------------|
| `happyranch resolve-escalation --org <slug> --task-id <id> --decision approve\|reject --rationale "..."` | Resolve an escalated task. Approve resumes; reject fails and cascades. |
| `happyranch revisit --org <slug> <task-id> [--note "..." \| --note-file PATH] [--session-timeout-seconds N]` | Spawn a new root inheriting a terminal predecessor's brief. **TTY-gated.** |
| `happyranch enrollments --org <slug> [--status pending]` | List agent enrollment requests |
| `happyranch approve-agent --org <slug> <name>` | Approve a pending enrollment and bootstrap workspace |
| `happyranch reject-agent --org <slug> <name>` | Reject a pending enrollment |

`happyranch revisit` walks any task's lineage to its root and — if the root ended `failed`, `cancelled`, `escalated`, or `completed` — spawns a fresh root inheriting the original brief and team. The old tree stays frozen (read-only history); the new root's manager gets a prompt-header pointer back to it so it can inspect what happened via `happyranch details` / `happyranch audit` / `happyranch recall`. Only humans can trigger it.

### Knowledge base

```bash
happyranch kb list      --org <slug> [--topic <t>] [--type <label>]
happyranch kb get       --org <slug> <slug>
happyranch kb search    --org <slug> "<query>"
happyranch kb add       --org <slug> --agent <you> --from-file /tmp/kb-<slug>.md
happyranch kb update    --org <slug> <slug> --agent <you> --from-file /tmp/kb-<slug>.md
happyranch kb delete    --org <slug> <slug> --agent <you> --confirm [--as-founder]
happyranch kb reindex   --org <slug>
```

Any agent reads/writes; team managers delete (audited); the founder overrides delete via `--as-founder`. Founder rulings on escalated tasks are written through plain `happyranch kb add` — set `source_task: TASK-XXX` in the frontmatter to keep the link to the escalation.

### Per-agent learnings

Each agent has its own private learnings under `<runtime>/orgs/<slug>/workspaces/<agent>/learnings/`, one markdown file per entry with YAML frontmatter (`id`, `slug`, `title`, `topic`, `tags`, `related_to`, `supersedes`, `promoted_to`). The bootstrap doc inlines the regenerated `_index.md` so agents see their accumulated rules at session start.

```bash
happyranch learning list     --org <slug> --agent <you> [--topic T --tag T --promoted|--not-promoted --json]
happyranch learning get      --org <slug> --agent <you> <LRN-NNN-or-slug> [--json]
happyranch learning search   --org <slug> --agent <you> "<query>" [--limit N --include-promoted --json]
happyranch learning add      --org <slug> --agent <you> --from-file /tmp/lrn-<slug>.yaml
happyranch learning update   --org <slug> --agent <you> <LRN-NNN> --from-file /tmp/lrn-<slug>.yaml
happyranch learning promote  --org <slug> --agent <you> <LRN-NNN> --kb-slug <kb-slug>
happyranch learning reindex  --org <slug> --agent <you>
```

`add`/`update` take a YAML payload with `slug`, `title`, `topic`, `body`, and optional `tags`/`source_task`/`related_to`/`supersedes`. Promotion is one-way: the body becomes a 2-line pointer stub and the entry locks against further edits — use `supersedes:` on a new entry to evolve a rule that has already been promoted. Workspaces that predate this layout still use a flat `learnings.md` (legacy `happyranch learning --agent X --text "..."` form); the founder dispatches a one-shot migration task per agent when ready.

### Threads

Email-style multi-agent workchannels. Use threads when you need to involve
multiple agents in a single asynchronous conversation, or when you want to
loop new agents into an existing discussion. Each thread has a subject, a
participants list, and a chronological message log.

**Web UI (primary surface).** Build the bundle once, then launch:

```bash
scripts/build_web.sh        # builds web/dist/ — npm ci + npm run build
happyranch web                     # opens http://127.0.0.1:<port>/ in your browser
happyranch web --no-open           # print the URL only
```

The localhost SPA renders the full threads inbox (compose / reply / invite /
archive / extend / forward / resume / SSE-driven live updates) and is the
single interactive interface for founders. Keyboard shortcuts: `N` new,
`I` invite, `A` archive, `F` forward, `R` focus composer,
`Ctrl+Enter` send, `?` help. Archived threads expose a "Resume thread" button
to reopen them. The dev server (`cd web && npm run dev`)
proxies to the daemon for hot-reload development.

(The previous Textual TUI under `src/tui/` was removed in favor of the
web UI; `happyranch threads` with no subcommand now points you at `happyranch web`.)

**Founder CLI commands** (unchanged — scripts and automations depend on
them):

```bash
happyranch threads compose --org <slug> --subject "..." --recipients alice,bob --body "..."
happyranch threads list --org <slug>
happyranch threads show --org <slug> THR-001
happyranch threads send --org <slug> --thread-id THR-001 --from-file /tmp/send.json
happyranch threads invite --org <slug> --thread-id THR-001 --agent qa
happyranch threads forward --org <slug> --source THR-008 --recipients alice,bob
happyranch threads archive --org <slug> --thread-id THR-001 --from-file /tmp/arch.json
happyranch threads resume --org <slug> --thread-id THR-001
happyranch threads extend --org <slug> --thread-id THR-001 --new-cap 1000
```

Archive is synchronous: the daemon writes the transcript file and flips the
thread to `archived` inside the POST handler. The founder can later reopen
an archived thread with `happyranch threads resume --thread-id <id>`.

Configure per-org:

```yaml
# <runtime>/orgs/<slug>/org/config.yaml
threads:
  enabled: true                       # default true
  default_turn_cap: 500               # total agent-turns budgeted per thread
  # invocation_timeout_seconds: null  # optional override of session_timeout for thread turns
```

`happyranch threads --org <slug>` with no subcommand is kept as a compatibility
stub. It prints a pointer to `happyranch web`; it does not launch a TUI.

### Jobs

When an agent's executor refuses a command (e.g., a `gh` / `aws` / `stripe` /
`sudo` invocation that needs founder-grade credentials), or when an agent needs
a long-running process, it can submit a job. Review-required jobs wait for
founder approval; auto-run jobs start without review. Tasks can self-block on
jobs and resume automatically when all listed jobs are terminal.

```bash
happyranch jobs list                                    # default: --status pending
happyranch jobs list --status all --agent <name>        # narrow by status/agent/task
happyranch jobs show JOB-019                            # see rationale, full script, output if terminal
happyranch jobs run JOB-019                             # TTY-gated confirm + live SSE stream
happyranch jobs run JOB-019 --cwd repos/web-app --timeout-seconds 600
happyranch jobs reject JOB-019 --reason "..."           # or omit --reason to be prompted
happyranch jobs output JOB-019                          # fetch the full captured output post-run
happyranch jobs output JOB-019 --stream stderr --max-bytes 5000000
happyranch jobs tail JOB-019 --stream stdout --lines 100
happyranch jobs wait JOB-019 --timeout-seconds 30
happyranch jobs stop JOB-019
```

The same surface is in the web UI at `/jobs` (list with status chips,
detail drawer with rationale + script preview, Run modal with cwd/timeout
overrides, Reject modal, live SSE output panel). If an agent self-blocks with
`waiting_on_job_ids`, the daemon resumes that task automatically when every
listed job reaches `completed`, `failed`, or `rejected`; the resumed session
gets an injected header pointing at `happyranch jobs show JOB-019` /
`happyranch jobs output JOB-019`.

Output is captured to `<runtime>/orgs/<slug>/jobs/JOB-NNN.{out,err,script}`
on disk (full output, no size cap in v1) plus a 64 KB head per stream in
the database for fast rendering.

Operational notes:
- Jobs run inside the daemon process with `os.environ` inherited from the
  daemon's launch shell. If you rotate credentials in your interactive shell,
  restart the daemon so the new env is picked up.
- `jobs run` requires a TTY. To run non-interactively, use the web UI's
  Run modal (it has the same confirm step in a visual form).
- If the daemon is killed mid-run, the next startup recovery scan marks any
  orphaned `running` rows as `failed`. Output captured up to the kill point
  is preserved on disk.
- `happyranch scripts ...` remains as a deprecated alias for `happyranch jobs ...`
  so older scripts keep working, but new docs and agent skills should use jobs.

### Managing repos

Agents can request repo changes through the `manage-repo` skill, or the founder can manage them directly:

```bash
happyranch manage-repo --org <slug> add    --agent <name> --repo-name docs --url https://github.com/user/docs.git
happyranch manage-repo --org <slug> remove --agent <name> --repo-name docs
happyranch manage-repo --org <slug> update --agent <name> --repo-name docs --url https://github.com/user/docs-v2.git
```

### Enrolling new agents

A manager can propose new agents during task execution using the `manage-agent` skill. Enrollment requires founder approval:

```bash
happyranch enrollments --org <slug> --status pending     # founder reviews
happyranch approve-agent --org <slug> content_writer     # bootstraps workspace, skills, repo clones
happyranch reject-agent  --org <slug> content_writer
```

Agent names must be lowercase with underscores only (e.g., `content_writer`, `seo_agent`).

To enroll an agent that runs on Codex, opencode, or Pi instead of Claude, the manager's `manage-agent` payload sets `executor` accordingly:

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
    "happyranch": "https://github.com/t-benze/happyranch.git"
  }
}
```

After approval, the new workspace will have `executor: codex` (or `opencode` or `pi`) in `agent.yaml` and will be bootstrapped with `AGENTS.md` instead of a Claude-only workspace surface.

### Managing the daemon

`scripts/daemon.sh` is a tiny supervisor that records the pid/port under `~/.happyranch/`:

```bash
scripts/daemon.sh start    # start in background (binds to localhost:8765)
scripts/daemon.sh status   # check if running
scripts/daemon.sh stop     # graceful shutdown
```

The daemon binds to port **8765** by default. Override with `HAPPYRANCH_DAEMON_PORT=<n>` before starting if that port is taken.

## Configuration

Operational settings come from two places, highest precedence first:

1. **Environment variables** with the `HAPPYRANCH_` prefix (e.g. `HAPPYRANCH_QUEUE_WORKERS=6`).
2. **`~/.happyranch/config.yaml`** — a YAML file in the daemon home (the same directory as `auth_token` / `runtimes.yaml`; honors `HAPPYRANCH_DAEMON_HOME`). Keys are the setting names **without** the prefix, e.g.:

   ```yaml
   queue_workers: 6
   session_timeout_seconds: 1800
   ```

If a value isn't set in either, the code default applies. The file is optional — if it doesn't exist, defaults are used. Changes take effect on daemon restart. (This is distinct from each org's `<runtime>/orgs/<slug>/org/config.yaml`, which holds per-org settings.) Runtime paths are derived from the runtime container.

| Variable | Default | Description |
|----------|---------|-------------|
| `HAPPYRANCH_CLAUDE_CLI_PATH` | `claude` | Path to Claude Code CLI |
| `HAPPYRANCH_CODEX_CLI_PATH` | `codex` | Path to Codex CLI |
| `HAPPYRANCH_OPENCODE_CLI_PATH` | `opencode` | Path to opencode CLI |
| `HAPPYRANCH_PI_CLI_PATH` | `pi` | Path to Pi CLI |
| `HAPPYRANCH_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `HAPPYRANCH_MAX_ORCHESTRATION_STEPS` | `50` | Max manager decision steps before escalation |
| `HAPPYRANCH_QUEUE_WORKERS` | `3` | Max agent sessions running at once (daemon-wide, across all orgs). Raise it if tasks queue up waiting for a free slot; the limit is shared, so a busy org can still use every slot. Must be a positive integer. Takes effect on daemon restart. |
| `HAPPYRANCH_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout (30 min) — global default; see overrides below |
| `HAPPYRANCH_DAEMON_PORT` | `8765` | Port the daemon binds to (`0` = ephemeral, old behaviour) |
| `HAPPYRANCH_ORG_SLUG` | _(unset)_ | Default org slug for per-org CLI commands |

### Per-Agent Configuration

Each agent has an `agent.yaml` in its workspace (`<runtime>/orgs/<slug>/workspaces/<agent>/agent.yaml`). Created automatically by `happyranch init-agent` with empty defaults:

```yaml
executor: claude
repos:
  web-app: https://github.com/user/web-app.git
  docs: https://github.com/user/docs.git
```

`executor` may be `claude`, `codex`, `opencode`, or `pi`. If omitted in an older workspace, it defaults to `claude`.

Repos are cloned into the agent's workspace on `happyranch init-agent` and auto-pulled before each task.

### Session timeout overrides

The per-session timeout (default 1800s / 30 min) is resolved in three layers, highest precedence first:

1. **Per-task**: pass `--session-timeout-seconds <int>` when revisiting a stuck task: `happyranch revisit --org <slug> <task-id> --session-timeout-seconds 7200`. The override is stored on the new root and inherited by every child the orchestrator spawns from it (delegated children, auto-revisits, later founder-revisits when the flag is omitted).
2. **Per-org**: create `<runtime>/orgs/<slug>/org/config.yaml` with `session_timeout_seconds: <int>`. Use this to bump every agent in this org above the global default.
3. **Global default**: `HAPPYRANCH_SESSION_TIMEOUT_SECONDS` env var (or the built-in 1800s).

A missing file or `null` value at any layer falls through to the next layer. Values must be positive integers.

### Founder notifications

Feishu was removed in TASK-302 (THR-022). The web UI and threads are the sole control path for dispatch / revisit / resolve-escalation. Legacy `feishu_notifications` config blocks are tolerated on load but ignored. The CLI `happyranch resolve-escalation` and `happyranch revisit` remain the authoritative founder surfaces.

### Nightly dreaming

Dreaming lets each agent run a private nightly reflection on a per-org schedule. It is **opt-in and inert until configured**: with no `dreaming` block (or `enabled: false`), no agent is ever dreamed and behavior is unchanged. Add a `dreaming` block to `<runtime>/orgs/<slug>/org/config.yaml` to turn it on:

```yaml
dreaming:
  enabled: true
  schedule:
    time: "02:00"                # local wall-clock HH:MM (24h); default 02:00
    timezone: "Asia/Shanghai"    # any IANA zone; default UTC
    catch_up_on_startup: true    # default true — run a missed slot once on daemon startup
  agents:
    mode: all                    # all | whitelist; default all
    include: []                  # agents dreamed when mode=whitelist
    exclude: []                  # always subtracted from the selected set, in both modes
```

| Field | Default | Notes |
|---|---|---|
| `enabled` | `false` | Master switch. `false` or block absent → dreaming is a no-op for this org. |
| `schedule.time` | `"02:00"` | Local clock time of the nightly slot, `HH:MM` (hour `00`–`23`). |
| `schedule.timezone` | `"UTC"` | IANA timezone name; an unknown zone fails config validation. |
| `schedule.catch_up_on_startup` | `true` | If the daemon was down over a slot, run it once on next startup. |
| `agents.mode` | `"all"` | `all` selects every eligible agent; `whitelist` selects only `agents.include`. |
| `agents.include` | `[]` | Agent names dreamed when `mode: whitelist`. |
| `agents.exclude` | `[]` | Agent names always removed from the selection, under either mode. |

Eligible agents are the approved agent files under `org/agents/*.md` that have a workspace. Unknown names in `include`/`exclude` fail config validation so a typo never silently skips an agent.

Founder CLI:

```bash
happyranch dreams status --org <slug> [--agent <name>]   # scheduler state / next slot
happyranch dreams list   --org <slug> [--agent <name>] [--limit 20] [--json]
happyranch dreams show   --org <slug> DREAM-NNN [--json]
```

### Working hours

Working hours wake selected agents on a configured cadence so they perform their standing duties without the founder dispatching each one. A wake is a **trigger, not the work**: on each wake the agent reads its own `## Routine Tasks` checklist (below) and self-dispatches one normal root task per routine.

Like dreaming, it is **opt-in and inert until configured**. An agent is woken only when all of these hold: `working_hours.enabled: true`, the agent is selected, a schedule resolves for it, **and** its agent file has a non-empty `## Routine Tasks` section. Absent any of these, no `work_hours` row is created and behavior is unchanged.

Schedules resolve through three tiers, overlaid **leaf-by-leaf**, lowest to highest precedence: org `default` → `teams.<team>` (the agent's team) → `overrides.<agent>`. Each tier may set only the leaves it wants to change; unset leaves inherit from the tier below.

```yaml
working_hours:
  enabled: true

  # Tier 1 (lowest): org-wide default schedule.
  default:
    mode: windowed                 # windowed | continuous
    window:
      start: "09:00"               # local clock HH:MM (hour 00-23)
      end: "18:00"                 # must be after start
      timezone: "Asia/Shanghai"    # IANA zone for this window
    interval: "2h"                 # Nh / Nm, positive; must be <= window length
    days: [mon, tue, wed, thu, fri]  # subset of mon..sun
    catch_up_on_startup: true      # default true

  # Which agents are eligible at all (same semantics as dreaming.agents).
  agents:
    mode: all                      # all | whitelist; default all
    include: []                    # used when mode=whitelist
    exclude: []                    # always subtracted last

  # Tier 2 (middle): per-team partial overrides, keyed by the agent's team.
  teams:
    customer_service:
      mode: continuous             # interval-only; window + days are ignored
      interval: "30m"              # must evenly divide 24h in continuous mode

  # Tier 3 (highest): per-agent partial overrides, keyed by agent name.
  overrides:
    dev_agent:
      interval: "1h"               # inherits windowed 09:00-18:00 weekdays from default
```

Worked example. With the config above:

- **`dev_agent`** (a `windowed` agent) inherits `mode: windowed`, the `09:00`–`18:00` `Asia/Shanghai` window and `mon`–`fri` days from `default`, and only overrides `interval` to `1h` — so it wakes hourly on weekday business hours.
- **A customer-service agent** picks up `mode: continuous` + `interval: "30m"` from its team tier — interval-only, no window or days — so it wakes every 30 minutes around the clock.

| Field | Default | Notes |
|---|---|---|
| `enabled` | `false` | Master switch. `false` or block absent → working hours is a no-op for this org. |
| `mode` | — (required after resolution) | `windowed` (uses `window` + `days`) or `continuous` (interval-only; `window`/`days` ignored). |
| `window.start` / `window.end` | — (required for `windowed`) | Local `HH:MM`; `start` must be before `end`. |
| `window.timezone` | — | IANA zone for the window. In `continuous` mode supply a bare `timezone:` leaf instead. |
| `interval` | — (required after resolution) | `Nh` or `Nm`, positive. `windowed`: must be ≤ window length. `continuous`: must evenly divide 24h. |
| `days` | — (required for `windowed`) | List from `mon, tue, wed, thu, fri, sat, sun`. |
| `catch_up_on_startup` | `true` | Run a missed slot once on daemon startup. |
| `agents.mode` / `include` / `exclude` | `all` / `[]` / `[]` | Eligibility selection, identical to `dreaming.agents`. |

#### `## Routine Tasks` (per-agent)

The routines a wake dispatches come from a `## Routine Tasks` H2 section in the agent's file at `org/agents/<name>.md`. Each top-level list item under that heading becomes one self-dispatched root task; prose before the first list item is shared preamble (context, not a task). An **absent or empty section means the agent has no routines and is never woken**, even if otherwise selected. (Up to 20 routines per wake are dispatched; any beyond that are dropped and the count is recorded.)

```markdown
## Routine Tasks

Run each item as an independent task; keep them surgical.

- Triage any GitHub issues opened since the last wake and label them.
- Check open PRs for failing CI and ping the author on any that have been red > 2h.
```

A `continuous` customer-service agent's file would carry its own list, e.g.:

```markdown
## Routine Tasks

- Sweep the unanswered-tickets queue and draft replies for anything older than 15 minutes.
```

Founder CLI:

```bash
happyranch work-hours status --org <slug> [--agent <name>]   # scheduler state / next slot
happyranch work-hours list   --org <slug> [--agent <name>] [--limit 20] [--json]
happyranch work-hours show   --org <slug> WORKHOUR-NNN [--json]
```

Web UI (founder-only): **Settings → Work Hours** edits the same `working_hours`
block in the browser. The overview lists every agent with a read-only **On**
status (derived from the single global switch **and** eligibility — there is no
per-agent toggle); the per-agent detail shows a 3-tier reconciliation
(org `default` → team → agent override → effective, winning tier highlighted),
mode-aware tier editors with ghosted inherited values and reset-to-inherited,
an org-level eligibility editor, a read-only routine-tasks panel, and a
next-wakes preview. Validation is server-authoritative — the same
`_build_org_config` path that loads the YAML validates every save, so an
invalid edit is rejected and the last-known-good config keeps running; the
client only hints field formats. Each save records an audit row.

## Agent Workspaces

Each agent runs in its own persistent workspace inside the org directory. After `happyranch init-agent`, each workspace contains:

- `agent.yaml` — per-agent config (`executor`, repos, ...)
- `CLAUDE.md` (Claude) or `AGENTS.md` (Codex/opencode/Pi) — agent identity, system prompt, available repos
- `.claude/settings.json` + `.claude/skills/` (Claude) — permissions and skills
- `.agents/skills/` (Codex/opencode/Pi) — shared skills tree
- `opencode.json` (opencode only) — `permission.bash` map
- Pi has no HappyRanch-managed sandbox or permission file; use external containment for Pi-backed agents when command/tool restriction matters.
- `repos/` — git clones of repositories from `agent.yaml` (auto-pulled before each task)
- `learnings/` — agent-written insights from past tasks, one file per entry (`LRN-NNN-<slug>.md`) with YAML frontmatter. A regenerated `_index.md` is inlined into the bootstrap doc. Write via `happyranch learning add --from-file <path>`; read via `happyranch learning list|get|search`; promote durable cross-agent rules to the shared KB via `happyranch learning promote <LRN-NNN> --kb-slug <slug>`. Workspaces created before this layout existed continue to use a flat `learnings.md`; the founder runs a one-shot migration task to switch a workspace over.
- `task_history.md` — rolling per-agent task history

## Roadmap

- [ ] Inter-team communication and cross-team handoff
- [ ] Founder dashboard (aggregated audit logs + escalation summaries)
- [ ] Persistent agents (long-running loops for patterns that don't fit single-task batch execution, e.g., real-time customer chat)

## License

Private — all rights reserved.
