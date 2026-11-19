# Configuration

Operational settings come from two places, highest precedence first:

1. **Environment variables** with the `HAPPYRANCH_` prefix (e.g. `HAPPYRANCH_QUEUE_WORKERS=6`).
2. **`~/.happyranch/config.yaml`** — a YAML file in the daemon home (the same directory as `auth_token` / `runtimes.yaml`; honors `HAPPYRANCH_DAEMON_HOME`). Keys are the setting names **without** the prefix, e.g.:

   ```yaml
   queue_workers: 6
   host_global_session_cap: 13
   session_timeout_seconds: 1800
   ```

If a value isn't set in either, the code default applies. The file is optional — if it doesn't exist, defaults are used. Changes take effect on daemon restart. (This is distinct from each org's `<runtime>/orgs/<slug>/org/config.yaml`, which holds per-org settings.) Runtime paths are derived from the runtime container.

The Settings → Capacity page lets a local operator holding the shared
daemon access token atomically stage only `queue_workers` and
`host_global_session_cap`. It is not a generic YAML editor and never applies or
restarts the daemon. The access token cannot be attributed to a verified
person. Environment values take precedence; when either key is shadowed the UI
requires explicit acknowledgement that the environment setting will still
override the saved value after a restart.

| Variable | Default | Description |
|----------|---------|-------------|
| `HAPPYRANCH_CLAUDE_CLI_PATH` | `claude` | Default command name for claude (metadata only — executor launch requires `executors.json` pin) |
| `HAPPYRANCH_CODEX_CLI_PATH` | `codex` | Default command name for codex (metadata only — executor launch requires `executors.json` pin) |
| `HAPPYRANCH_OPENCODE_CLI_PATH` | `opencode` | Default command name for opencode (metadata only — executor launch requires `executors.json` pin) |
| `HAPPYRANCH_PI_CLI_PATH` | `pi` | Default command name for pi (metadata only — executor launch requires `executors.json` pin) |
| `HAPPYRANCH_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `HAPPYRANCH_MAX_ORCHESTRATION_STEPS` | `50` | Legacy accepted setting; inert (not an execution limit) |
| `HAPPYRANCH_QUEUE_WORKERS` | `6` | Task `run_step` slots (daemon-wide, across all orgs). Must be positive. Takes effect on daemon restart. |
| `HAPPYRANCH_HOST_GLOBAL_SESSION_CAP` | `13` | Healthy enforcement-capable host-session admission cap. Capability fallbacks can reduce the effective cap (macOS/no-enforcement remains 4). Must be positive. Takes effect on daemon restart. |
| `HAPPYRANCH_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout (30 min) — global default; see overrides below |
| `HAPPYRANCH_DAEMON_PORT` | `8765` | Port the daemon binds to (`0` = ephemeral, old behaviour) |
| `HAPPYRANCH_ORG_SLUG` | _(unset)_ | Default org slug for per-org CLI commands |

## Per-Agent Configuration

Each agent's authoritative configuration lives in its AgentDef frontmatter at `<runtime>/orgs/<slug>/org/agents/<name>.md` (parsed by `runtime/orchestrator/agent_def.py`). Workspace `agent.yaml` was retired (THR-095) and is no longer read or written for org agents.

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

`executor` is a registered executor profile name. Built-in profiles are `claude`, `codex`, `opencode`, and `pi`; a custom profile is valid only when its explicit `command_adapter_id: custom-adapter:<id>` binding resolves to a registered, conformance-passed, founder-approved adapter. If omitted, `executor` defaults to `claude`.

Repos are cloned into the agent's workspace (`repos/<name>/`) by `happyranch init-agent`, founder enrollment / approval, and `manage-repo add/update`. At each session spawn the daemon only fast-forward-pulls existing clones (`git pull --ff-only`, fail-open); it does not clone a repository that is declared but missing — re-run init or `manage-repo update` to provision it.

## Session timeout overrides

The per-session timeout (default 1800s / 30 min) is resolved in three layers, highest precedence first:

1. **Per-task**: pass `--session-timeout-seconds <int>` when revisiting a stuck task: `happyranch revisit --org <slug> <task-id> --session-timeout-seconds 7200`. The override is stored on the new root and inherited by every delegated child the orchestrator spawns from it.
2. **Per-org**: create `<runtime>/orgs/<slug>/org/config.yaml` with `session_timeout_seconds: <int>`. Use this to bump every agent in this org above the global default.
3. **Global default**: `HAPPYRANCH_SESSION_TIMEOUT_SECONDS` env var (or the built-in 1800s).

A missing file or `null` value at any layer falls through to the next layer. Values must be positive integers.

## Founder notifications

Feishu was removed in TASK-302 (THR-022). The web UI and threads are the sole control path for dispatch / revisit / resolve-escalation. Legacy `feishu_notifications` config blocks are tolerated on load but ignored. The CLI `happyranch resolve-escalation` and `happyranch revisit` remain the authoritative founder surfaces.

Legacy `escalation_notifications` / `processed_event_ids` tables are dropped automatically on the next database open after upgrade (no operator action; their rows are discarded), and a leftover `feishu_notifications` block in org config can be deleted at any time but is harmless.

## Nightly dreaming

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

## Memory compaction

Per-org `org/config.yaml` defaults (also used when the section is absent):

```yaml
memory_compaction:
  enabled: true
  salience_floor: 10
  stale_days: 45
  superseded_grace_days: 7
  max_evictions_per_run: 25
  auto_trigger_entry_count: 150
```

Explicit `enabled: false` disables manual apply and automatic compaction;
`auto_trigger_entry_count: 0` disables automatic triggering alone. Dry-run remains
available. Ordinary memory adds and dream reflections trigger a best-effort sweep
at/above the file threshold (including evicted files); updates and inherited
imports do not. Each eviction is reversible, preserves the note and records the
same `memory_lifecycle_changed` audit as manual compaction, with
`source: compaction`. A sweep or audit failure warns without failing the add or
suppressing its `memory_added` audit. This setting does not enable dreaming.

## Working hours

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

### `## Routine Tasks` (per-agent)

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

- A regular `AGENTS.md` plus a raw relative `CLAUDE.md -> AGENTS.md` link for every built-in executor — agent identity, system prompt, available repos. Startup refuses an incomplete or noncanonical pair before executor launch; repair it with `happyranch init-agent <agent>`.
- `.claude/settings.json` + `.claude/skills/` (Claude) — permissions and skills
- `.agents/skills/` (Codex/opencode/Pi) — shared skills tree
- `opencode.json` (opencode only) — `permission.bash` map
- Pi has no HappyRanch-managed sandbox or permission file; use external containment for Pi-backed agents when command/tool restriction matters.
- `repos/<name>/` — clones of the repositories declared in `org/agents/<name>.md` frontmatter (`AgentDef.repos`); provisioned by init/enrollment/manage-repo, fast-forward-pulled at each session spawn
- `memory/` — agent-written insights from past tasks, one file per entry (`MEM-NNN-<slug>.md`) with YAML frontmatter. A regenerated `_index.md` is inlined into the bootstrap doc. Write via `happyranch memory add --from-file <path>`; read via `happyranch memory list|get|search`; promote durable cross-agent rules to the shared KB via `happyranch memory promote <MEM-NNN> --kb-slug <slug>`. Workspaces created before this layout existed continue to use a flat `learnings.md`; the founder runs a one-shot migration task to switch a workspace over.
- `task_history.md` — rolling per-agent task history
