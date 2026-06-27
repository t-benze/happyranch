# Working Hours - Design

> Status: implemented
> Current Source: Executable truth is `runtime/daemon/`, `runtime/orchestrator/org_config.py`, the work-hours store, the working-hours scheduler/runner, and tests.
> Superseded By: None
> Notes: Founder-approved and implemented. All seven open design questions are **resolved** (see the "Resolved Decisions" record at the end). Working-hours wakes are **task-producing triggers** — a wake self-dispatches normal root tasks and is itself *not* a task, talk, or thread. The wake-trigger machinery reuses the nightly-dreaming scheduler/runner skeleton (`2026-06-09-nightly-dreaming-design.md`), but unlike dreams the work lands on the existing task surface and appears in task lists and metrics.

## Goal

Add a per-agent "working hours" mechanism: a scheduler that wakes selected agents on a configured cadence so they perform their standing, self-initiated duties without a founder having to dispatch each one. A wake is a **trigger, not the work**. On each wake, the agent reads its own routine checklist (the `## Routine Tasks` section of its agent file) and self-dispatches **one real root task per routine**. Those root tasks then flow through the normal manager-decision / worker loop and are fully visible in task lists, audit, and metrics.

The mechanism must serve two cadence shapes as first-class citizens:

- A **windowed** agent (e.g. a 9-to-6 `dev_agent`) wakes only inside a business-hours window, on configured weekdays, at a fixed interval.
- A **continuous** agent (e.g. a `customer_service` agent) wakes around the clock at a fixed interval to resolve incoming customer requests, ignoring any window or day restriction.

Both shapes coexist inside one org, so the schedule is resolved **per agent**, not per org.

Working hours are intentionally separate from:

- **Dreams**: private scheduled reflection runs that are not tasks. Working-hours wakes deliberately *do* produce tasks.
- **Talks / Threads**: founder-visible messaging surfaces. A wake never opens a talk or thread; it dispatches tasks.

Working hours reuse the dreaming scheduler/queue/runner skeleton and the executor invocation plumbing, but have their own config block, persistence table, status enum, callback contract, and audit actions.

## Non-Goals

- **No new task-execution path.** Spawned root tasks are ordinary tasks. Working hours add *no* coupling between a `work_hours` row and `TaskRecord` beyond recording the spawned root `task_id`s on the `work_hours` row for provenance. No new column on the `tasks` table.
- **No replay of every missed slot.** After downtime, at most one catch-up wake (the most recent due slot) is enqueued per agent per day. Intermediate missed slots within a day are not backfilled.
- **No multi-turn wake dialogue.** Each wake is one executor invocation whose sole job is to self-dispatch the routine tasks and report.
- **No cross-team dispatch from a wake.** A wake self-dispatches onto the waking agent's own team only. Cross-team handoff continues to route through thread `compose`, never through a wake.
- **No schema migration of existing columns** and **no overloaded-column semantic change.** New `work_hours` table only; new additive token-usage scope *value* `work_hour` populating the existing `scope_id` column with `WORKHOUR-NNN`. `tasks.task_id` is never overloaded.
- **No changes to the permission model, Codex sandbox, opencode permission map, Claude allow-rule generator, auth, daemon bearer-token flow, Feishu, or notification routing.**
- **No `protocol/` edits.** The wake prompt is composed by the daemon runner (as `dream_runner.build_dream_prompt` does), so no `protocol/skills/...` doc is required to ship the mechanism. An optional authoring-guidance skill under `protocol/` would be founder-authored and is out of engineering scope.
- **No web UI beyond founder list/show mirrors** in v1; a dashboard card is a later follow-up.

## Org And Per-Agent Configuration

Working hours are opt-in per org in `<runtime>/orgs/<slug>/org/config.yaml`, with three resolution tiers (lowest to highest precedence: **org default → team default → agent override**).

```yaml
working_hours:
  enabled: true

  # Tier 1 (lowest): org-wide default schedule.
  default:
    mode: windowed              # windowed | continuous
    window:
      start: "09:00"            # local clock HH:MM
      end: "18:00"
      timezone: "Asia/Shanghai"
    interval: "2h"              # Nh / Nm (see "Interval and slot grid")
    days: [mon, tue, wed, thu, fri]
    catch_up_on_startup: true

  # Which agents are eligible to be woken at all (same selection semantics
  # as dreaming.agents).
  agents:
    mode: all                   # all | whitelist
    include: []                 # used when mode=whitelist
    exclude: []                 # always subtracted last

  # Tier 2 (middle): per-team defaults, keyed by the agent's team.
  teams:
    engineering:
      interval: "3h"
    customer_service:
      mode: continuous
      interval: "30m"

  # Tier 3 (highest): per-agent overrides, keyed by agent name.
  # (The customer_service team already defaults to continuous/30m above, so
  # its members need no per-agent entry; overrides are for one-off exceptions.)
  overrides:
    triage_bot:
      mode: continuous          # window + days are ignored in continuous mode
      interval: "1h"            # a single agent that wakes hourly, around the clock
    dev_agent:
      mode: windowed
      window: { start: "09:00", end: "18:00", timezone: "Asia/Shanghai" }
      interval: "2h"
      days: [mon, tue, wed, thu, fri]
```

### Agent selection

Identical to dreaming:

1. Candidate agents are approved agent files under `org/agents/*.md` with existing workspaces.
2. `agents.mode: all` selects every candidate; `whitelist` selects only `include`.
3. `exclude` is applied last for both modes.
4. Unknown names in `include`/`exclude` fail config validation so typos do not silently skip agents (validated at the resolved-candidate point, mirroring `select_dream_agents`).

### Schedule resolution and precedence

For each selected agent, the **effective schedule** is computed by overlaying the three tiers **leaf-key by leaf-key** in precedence order. The merge is at the granularity of: `mode`, `window.start`, `window.end`, `window.timezone`, `interval`, `days`, `catch_up_on_startup`. Each leaf takes the value from the highest tier that sets it; unset leaves inherit downward. Tier keys:

- **Tier 1** `working_hours.default`.
- **Tier 2** `working_hours.teams.<team>`, where `<team>` is the agent's team (the `team` it belongs to in `teams.yaml`). Team keying gives per-function defaults — e.g. a `customer_service` team continuous by default while an `engineering` team stays windowed — without per-agent overrides for every member.
- **Tier 3** `working_hours.overrides.<agent_name>`.

The `mode` discriminator changes which leaves are meaningful:

- **`windowed`**: `window.{start,end,timezone}`, `interval`, and `days` are all required (after resolution). Wakes fire at `window.start`, then every `interval`, up to and including the last grid slot ≤ `window.end`, only on listed `days`, interpreted in `window.timezone`.
- **`continuous`**: `interval` and a `timezone` are required; `window` and `days` are **ignored** (a continuous agent wakes every interval, every day, around the clock). `window.timezone` (or a bare `timezone` leaf) anchors `local_date` and the slot grid.

### Validation

Config load (`OrgConfigError` on failure) must reject:

- `working_hours` not a mapping; `enabled` not a boolean.
- Unknown `mode` (not `windowed`/`continuous`) at any tier.
- Malformed `window.start`/`window.end` (not `HH:MM`, hour > 23); `start >= end` for a windowed effective schedule.
- Unknown `window.timezone` (must resolve via `ZoneInfo`, mirroring dreaming).
- Malformed `interval` (not `Nh`/`Nm`, non-positive).
- For **windowed**: `interval` longer than the window length (would yield zero in-window slots after `start`).
- For **continuous**: `interval` that does not evenly divide 24h. This constraint is settled: it keeps slot boundaries aligned across local dates so the `00:00` slot always exists and the grid restarts cleanly at midnight.
- `days` containing anything outside `{mon,tue,wed,thu,fri,sat,sun}`.
- Unknown agent names in `agents.include`/`agents.exclude` (resolved-candidate check).
- Unknown team names under `working_hours.teams.<team>` (checked against the org's `teams.yaml` at the resolved-candidate point, so a typo'd team key does not silently fail to apply).
- An `overrides.<name>` whose effective `mode` is `windowed` but which, after merge, lacks a complete window/days set.

Validation that depends on the resolved candidate-agent list (unknown-name checks) happens at the scheduler's selection step, exactly as `select_dream_agents` does today; structural validation happens at `OrgConfig` load.

## Interval And Slot Grid

`interval` is expressed as `Nh` or `Nm` (e.g. `2h`, `90m`, `15m`). The **slot grid** is the set of wake instants for an agent on a given `local_date`, each identified by a canonical `slot` string = the local clock time `HH:MM` of that wake:

- **windowed**: grid is anchored at `window.start` and stepped by `interval` while `≤ window.end`, e.g. start `09:00`, interval `2h` → slots `09:00, 11:00, 13:00, 15:00, 17:00`. Slots exist only on configured `days`.
- **continuous**: grid is anchored at `00:00` and stepped by `interval` across the full day, e.g. interval `15m` → `00:00, 00:15, …, 23:45`. Every day is eligible; no `days` filter.

`local_date` is the calendar date, in the effective timezone, of the slot instant. The `(agent_name, local_date, slot)` triple is the scheduling identity (see uniqueness guard below). At a `continuous` agent's midnight rollover, the `00:00` slot belongs to the new `local_date`, so the grid restarts cleanly with no collision.

## Data Model

Add `WorkHourStatus` (mirrors `DreamStatus`):

- `pending`
- `running`
- `completed`
- `failed`
- `timeout`
- `skipped`

Add a `work_hours` table (modeled on `dreams`):

```sql
CREATE TABLE work_hours (
    id TEXT PRIMARY KEY,                       -- WORKHOUR-NNN
    agent_name TEXT NOT NULL,
    local_date TEXT NOT NULL,                  -- YYYY-MM-DD in the agent's effective timezone
    slot TEXT NOT NULL,                        -- canonical HH:MM grid slot (local clock)
    mode TEXT NOT NULL,                        -- windowed | continuous (effective mode at schedule time)
    scheduled_for TEXT NOT NULL,               -- ISO timestamp (UTC) of the slot instant
    started_at TEXT,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    routine_count INTEGER NOT NULL DEFAULT 0,  -- routines parsed/injected into the wake prompt
    spawned_task_ids TEXT,                     -- JSON array of root task ids this wake spawned (provenance)
    spawned_task_count INTEGER NOT NULL DEFAULT 0,
    summary TEXT,                              -- wake's own report (not the spawned tasks' output)
    transcript_path TEXT,
    session_id TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(agent_name, local_date, slot)
);

CREATE INDEX IF NOT EXISTS idx_work_hours_agent_date
    ON work_hours(agent_name, local_date);
CREATE INDEX IF NOT EXISTS idx_work_hours_status
    ON work_hours(status);
```

`spawned_task_ids` is the **only** linkage between a wake and the task surface — a JSON array of root `task_id`s. There is no foreign key and no new column on `tasks`; the spawned tasks are ordinary root tasks. This satisfies the ruling "do not couple working-hours rows to `TaskRecord` beyond recording the spawned root `task_id`s for provenance."

Wake transcripts live at:

```text
<runtime>/orgs/<slug>/work_hours/WORKHOUR-NNN.md
```

The transcript frontmatter carries work-hour id, agent, local_date, slot, mode, status, routine count, spawned task count, and spawned task ids, followed by the wake's prose summary and transcript.

## Routine Tasks: Source Of Truth And Parse Contract

The routine checklist is a new `## Routine Tasks` H2 section in each `org/agents/<name>.md`. The **daemon** parses it both to gate scheduling and to inject it into the wake prompt.

Parse contract:

- **Locate** the first markdown H2 whose text is exactly `## Routine Tasks`. The section body runs from that header to the next H2 (`## `) or EOF.
- **Routines** are the top-level markdown list items (`- ` or `1. `) in that body. Each list item's text (including nested continuation lines indented under it) is one routine.
- Each routine becomes the seed for **one** self-dispatched root task. Spawned tasks are **always** on the waking agent's own team; there is no per-routine team selector (a wake has no cross-team path — see Security).
- Non-list prose appearing before the first list item is a **preamble**: injected into the wake prompt as shared context but spawning no task.
- **Absent section** (no `## Routine Tasks` header), or a section with **zero list items** (empty/preamble-only), is treated as "no routines" → the scheduler **skips silently**: no `work_hours` row is inserted and no wake is enqueued for that agent/slot. No error, no audit noise.
- A **cap** of `MAX_ROUTINES_PER_WAKE` (proposed: 20) bounds how many tasks one wake may spawn; routines beyond the cap are dropped and the drop is recorded in the wake summary and audit (no silent truncation).

Because the absence check is a precondition for scheduling, an agent with no `## Routine Tasks` section never accrues `work_hours` rows even when otherwise selected.

## Wake Input And Prompt

Each wake is one executor invocation (a `WakeRunner`, mirroring `DreamRunner`). The prompt is composed by the daemon and must:

- State that this is a **working-hours wake**: a trigger to launch the agent's standing routines, *not* itself the work, *not* a reflection.
- Include the agent's normal bootstrap context (workspace, role, team).
- Inject the parsed `## Routine Tasks` section verbatim (preamble + list).
- Instruct the agent to translate **each** routine list item into one concrete root-task brief and to submit them **in a single** `work-hours spawn` callback (the single-line `--from-file` form below).
- State the cadence context (local_date, slot, mode) so the agent can phrase briefs appropriately (e.g. "since the last wake at …").

The wake session is **not** asked to do the routine work itself — only to phrase and dispatch it. The real work happens in the spawned root tasks via the normal loop.

### Always a session (settled)

Every slot runs a real wake executor session that reads the checklist and self-dispatches; there is **no** daemon-direct spawn path in v1. This is settled — it honors ruling #1 ("the wake invocation reads the checklist and self-dispatches") and keeps wake-trigger cost attributable under the `work_hour` token scope (a daemon-direct spawn would produce zero-token, unattributable task births). The trade-off is one executor invocation per slot per agent. That cost is bounded by choosing sane intervals: a 15-minute continuous agent is **96 wakes/day**, so a continuous customer-service agent should normally run a **30–60 minute** interval rather than 15 minutes. The interval is a per-agent/team/org config leaf, so the founder tunes the cost/responsiveness trade-off directly.

## Invocation Contract

The wake session completes by a single-line callback (mirrors `happyranch dreams complete`):

```bash
happyranch work-hours spawn --org <slug> --work-hour-id WORKHOUR-001 --from-file /tmp/wake-WORKHOUR-001.json
```

Payload shape:

```json
{
  "summary": "Launched 3 routine tasks for the 09:00 windowed wake.",
  "routines": [
    { "slug": "triage-tickets", "brief": "Triage and resolve any customer tickets opened since the last wake; escalate billing disputes." },
    { "slug": "followups",      "brief": "Send scheduled follow-ups for tickets awaiting customer reply > 24h." }
  ]
}
```

Daemon-side handling of `work-hours spawn`:

1. Validate the target `WORKHOUR-NNN` exists and is `running` (reject `completed`/`failed`/`skipped`/`pending` with a clear 409 — single-use, so the endpoint cannot be reused as a generic task-spawn backdoor).
2. Validate the payload: `summary` required; each `routines[]` needs a non-empty `brief`. There is no per-routine `team` field — every spawned task lands on the waking agent's own team (see Security).
3. For each routine, create one root task **targeted to the waking agent as its executor** — the task is born on the waking agent's own team with `assigned_agent = <waking agent>` pre-set, then enqueued exactly as a founder-created root task is. The waking agent is thus the owner/executor of its own routine work, not merely the team entrypoint:
   - When the waking agent is a **worker**, it executes the spawned task directly — bounded work → report — rather than the task being triaged through the team manager's decision loop.
   - When the waking agent is a **team manager**, the spawned task naturally enters that manager's own decision loop (the manager *is* the assigned agent), where it may decompose and delegate.

   This is the **intended orchestrator semantics**. At build time the implementer must verify, via GitNexus impact analysis, that the root-task entrypoint supports pre-setting the executor (`assigned_agent`) at creation and that `run_step` honors a pre-set `assigned_agent` instead of defaulting the root to the team manager — and must confirm how the waking agent is attributed as the task's originator **without adding a column to the `tasks` table** (provenance today is the reverse linkage `work_hours.spawned_task_ids`; any forward originator marker must reuse an existing field). If executor targeting turns out to require changing the task-creation contract or any load-bearing invariant, that is a **founder escalation**, not an in-spec assertion.
4. Record the resulting `task_id`s into `work_hours.spawned_task_ids` (JSON) and set `spawned_task_count`; write a `work_hour_spawned` audit row carrying the id list.
5. Mark the work-hour `completed`, write the transcript, and store `summary`.

The CLI reads `--from-file` and forwards the validated body to the daemon; the daemon performs task creation server-side so task births stay daemon-authoritative. The wake session never calls `create_task` directly.

Validation:

- `summary` required.
- At least one routine with a non-empty `brief`; routine count must not exceed `MAX_ROUTINES_PER_WAKE`.
- No per-routine team selector exists; the spawn handler always uses the waking agent's own team and targets the waking agent as executor.

## Scheduler

Add a daemon `work_hours_scheduler_loop`, started during FastAPI lifespan startup and cancelled during shutdown, running alongside the task queue workers, dream scheduler, thread workers, Feishu listeners, and jobs recovery — directly mirroring `dream_scheduler_loop`.

Steady-state loop behavior (every `interval_seconds`, proposed 60s):

1. For each loaded org, load current org config; skip orgs where `working_hours.enabled` is false.
2. Resolve selected agents (`agents.mode`/`include`/`exclude`), failing loudly (log, not crash) for a misconfigured org so other orgs keep scheduling — mirrors the dream loop's `OrgConfigError` handling.
3. For each selected agent, compute its effective schedule and the **current due slot**: the most recent grid slot at-or-before `now` that is valid for the agent's mode (in-window + on a configured day for `windowed`; any grid slot for `continuous`).
4. If a due slot exists, and the agent's `## Routine Tasks` section is present and non-empty, and **no `work_hours` row exists for `(agent, local_date, slot)`**, insert a `pending` row and enqueue a `WakeJob`.
5. Absent/empty routine section → skip silently (no row).

Startup catch-up behavior:

- The first loop iteration is the startup catch-up pass (mirrors the dream loop's `startup=True` first tick), run once after orgs are loaded and DB recovery has run.
- It enqueues **at most one** wake per agent — the single most recent due slot of the current local_date — and only when the effective `catch_up_on_startup` is true.
- When `catch_up_on_startup` is false, a missed current slot is recorded as a `skipped` row so the steady-state guard suppresses re-scheduling it later the same day (mirrors dreaming's skipped-row trick).
- Intermediate missed slots earlier in the day are **never** backfilled; historical days are never replayed. This is settled: only the single most-recent due slot is considered at startup. A 24h continuous agent recovering from, say, a 2h outage fires **once** (the latest due slot), not once per missed interval.

Queue behavior:

- Add a `WakeQueue` and a small worker pool (or a single worker for v1) running `WakeRunner`.
- A per-agent lock prevents two wake invocations for the same agent in the same org from running concurrently.

### Uniqueness guard and continuous mode

The dreaming guard is `UNIQUE(agent_name, local_date)` (one dream per agent per day). Working hours generalize this to `UNIQUE(agent_name, local_date, slot)`, allowing many wakes per day while still guaranteeing **exactly one** wake per agent per slot per local_date. Because both `windowed` and `continuous` modes reduce to a deterministic `HH:MM` slot on a `local_date`, the guard is identical for both — `continuous` simply produces more slots per day (and on every day) and applies no window/day filter. The midnight rollover assigns the `00:00` slot to the new date, so continuous wakes never collide across the date boundary.

## Failure Handling

Mirrors dreaming, adapted for the spawn step:

- **Wake executor failure**: mark `failed`, preserve error; the unique `(agent, local_date, slot)` row prevents automatic re-attempts of the same slot. No tasks spawned.
- **Timeout**: mark `timeout`, preserve timeout error (distinct audit action so token/audit reporting can separate it). No tasks spawned.
- **Missing callback**: mark `failed`/`timeout` at the same invocation-timeout boundary used for dreams.
- **Callback validation failure**: mark `failed`, preserve validation error, spawn no tasks.
- **Partial spawn**: the daemon validates the entire payload before creating any task. If task creation nonetheless fails partway, the already-created root tasks are real and proceed normally; their ids are still recorded in `spawned_task_ids`, and the work-hour is marked `failed` with a `partial_spawn` error. No attempt is made to roll back created tasks — this no-rollback behavior is settled (already-spawned routine work is real work and should not be discarded because a sibling spawn failed).
- **Daemon restart while running**: startup recovery marks stale `running` work-hours `failed` with reason `daemon_restart` (mirrors `recover_running_dreams`). Tasks already spawned before the crash proceed; the slot's unique row prevents a duplicate wake for that slot.
- **Duplicate scheduling**: DB uniqueness on `(agent_name, local_date, slot)` is authoritative.

A failed wake for a given slot is not auto-retried within the same slot; this avoids a runaway wake-retry loop on a continuously-failing agent.

## Founder Surfaces

V1 founder-facing CLI/API:

```bash
happyranch work-hours status --org <slug> [--agent <name>]
happyranch work-hours list   --org <slug> [--agent <name>] [--limit N]
happyranch work-hours show   --org <slug> WORKHOUR-001 [--json]
```

`show` surfaces the wake's status, slot/mode, routine count, and the spawned root `task_id`s (so the founder can pivot to the real tasks via `happyranch recall <task_id>` / task views). Because the spawned tasks are ordinary tasks, they already appear in existing task lists, the dashboard, and metrics — no new task surface is needed.

Web mirror: browser-callable `work-hours` list/show routes get TypeScript mirrors under `web/src/lib/api/`. A dashboard "working hours" card is a later follow-up; v1 only needs list/show plus the audit rows that make a future card straightforward.

> **Update (THR-035, implemented):** the web surface went past read-only list/show. The **Work-Hours Config UI** (Settings → Work Hours) is now a founder-only *authoring* surface: a single global on/off switch, the org-level eligibility selector, a 3-tier reconciliation view with per-leaf provenance, reusable tier editors (`default` / `teams` / `overrides`), an eligibility editor, and a next-wakes preview (`GET /work-hours/next-wakes`). Writes reuse `save_org_config`'s validate-then-atomic-write (invalid config never reaches disk) and validation stays server-authoritative (`_build_org_config`); the client does format hints only. Each successful config write emits an `org_config_write` audit row keyed `task_id="config:<section>"` (e.g. `config:working_hours`) — the same mandatory scope-prefix convention used for `artifact:<name>`/`THR-`/`WORKHOUR-` (load-bearing invariant; additive, no schema migration, no column change). Routine-task editing remains read-only in this MVP. Current behavior: `docs/agent-guides/web-and-cli.md` (Settings dialog + Work-Hours Config) and `README.md` (Working hours).

## Audit And Token Usage

Audit actions (mirroring the dreaming action set):

- `work_hour_scheduled`
- `work_hour_started`
- `work_hour_spawned` — payload carries the spawned root `task_id` list.
- `work_hour_completed`
- `work_hour_failed`
- `work_hour_timeout`

As with dreams, the `audit_log.task_id` column (the existing overloaded *generic scope id*) stores `WORKHOUR-NNN` for these rows — this is the established invariant, not a new overload, and it is distinct from the token-usage table below. The spawned root tasks additionally emit their own ordinary `task_*` audit rows; the two streams are correlated by the `task_id`s recorded in `work_hour_spawned` and in `work_hours.spawned_task_ids`.

Token usage: the wake executor session records usage with `scope_type = "work_hour"` and `scope_id = WORKHOUR-NNN` in the existing `session_token_usage` table — a new additive scope **value** populating existing columns, exactly as dreaming added `scope_type="dream"`. `task_id` is **not** overloaded. The spawned root tasks record their own usage under the normal `task` scope, so wake-trigger cost and routine-work cost are separable. The token-reporting route (`runtime/daemon/routes/tokens.py`) must recognize the new `work_hour` scope value for grouping (additive code; no schema migration).

## Security And Permissions

- The wake callback follows the single-line `happyranch ... --from-file <path>` convention. No multi-line callbacks.
- A wake invocation gets only the baseline `happyranch` CLI side-effect channel plus ordinary read/write inside the agent workspace. It receives **no** special permission to edit org config, write the KB directly, alter permissions, or dispatch *other* agents.
- The `work-hours spawn` endpoint is **single-use and slot-scoped**: it accepts only a `running` `WORKHOUR-NNN` and rejects everything else, so it cannot be reused as a generic "spawn arbitrary root tasks" backdoor. Each accepted call transitions the work-hour to `completed`/`failed`.
- **Self-team only (structural)**: spawned root tasks are always created on the waking agent's own team and targeted to the waking agent as executor. There is no per-routine team selector and no other parameter through which a wake could reach another team, so the self-dispatch doctrine holds by construction — cross-team work routes through thread `compose`, never through a wake.
- No change to the permission model, Codex sandbox, opencode permission map, Claude allow-rule generator, auth, daemon bearer-token flow, Feishu, or notification routing.

## OpenAPI And Web Contract

- Every browser-callable founder-facing `work-hours` route (`list`, `show`) must have a TypeScript mirror under `web/src/lib/api/` or be explicitly excluded with justification.
- OpenAPI snapshot changes (`tests/contract/test_openapi_snapshot.py`) must be intentional and regenerated through the existing contract test (`HAPPYRANCH_REGEN_OPENAPI=1`).
- The `work-hours spawn` agent callback route, not being browser-callable, is treated like the existing dream/talk/thread callback routes and documented accordingly (no web mirror).

## Test Plan

Unit tests:

- `OrgConfig` parses a valid `working_hours:` block (windowed and continuous).
- Invalid mode, malformed window times, `start >= end`, unknown timezone, malformed interval, interval > window length (windowed), interval not dividing 24h (continuous), bad `days`, and unknown include/exclude agents each fail clearly.
- Three-tier precedence resolution: org default ← team default (`teams.<team>`) ← agent override, leaf-by-leaf; partial overrides inherit unset leaves. An agent's team default applies to every member of that team unless an agent override sets the leaf.
- Unknown team key under `working_hours.teams.<team>` fails validation at the resolved-candidate point.
- Continuous mode ignores `window` and `days`.
- Slot-grid computation: windowed (anchored at `window.start`, stepped by interval, bounded by `window.end`, day-filtered) and continuous (anchored at `00:00`, full-day), including the continuous midnight rollover assigning `00:00` to the new local_date.
- `(agent, local_date, slot)` uniqueness allows multiple slots/day but blocks duplicates; continuous and windowed both honored.
- Startup catch-up enqueues only the latest due slot, never replays earlier slots or historical days; `catch_up_on_startup:false` records a `skipped` row.
- `## Routine Tasks` parse: list-item extraction, preamble handling, cap enforcement (with recorded drop), absent/empty section → no wake scheduled.

Daemon / route tests:

- `work-hours spawn` on a `running` work-hour creates one root task per routine, records `spawned_task_ids`/`spawned_task_count`, writes `work_hour_spawned` audit + transcript, and marks `completed`.
- Spawned tasks appear in the normal task list / queue (verifying the task-producing contract).
- **Executor targeting**: each spawned root task is created on the waking agent's own team with `assigned_agent = <waking agent>`. A **worker** wake's task is executed directly by that worker (bounded work → report), not routed into the team manager's decision loop; a **manager** wake's task enters that manager's own decision loop. (Asserts the Q2 ruling against the orchestrator's actual root-task entrypoint behavior — pre-set `assigned_agent` honored, not overridden by the team-manager default.)
- `work-hours spawn` on a non-`running` work-hour is rejected (single-use guard).
- Failed and timed-out wakes spawn no tasks; partial-spawn records created ids and marks `failed`.
- Startup recovery marks stale `running` work-hours `failed`.

Integration tests:

- Fake executor completes a wake through `happyranch work-hours spawn`, and the spawned root tasks run through the fake-executor loop.
- The scheduler enqueues a windowed wake and a continuous wake under controlled time (frozen clock), including a continuous wake straddling midnight.
- Token-usage rows for the wake use `scope_type="work_hour"` / `scope_id=WORKHOUR-NNN`; spawned tasks use `task` scope.

## Implementation Notes

Doc-only task — no code in this PR. Before any follow-up edits, GitNexus impact analysis (`gitnexus_impact`) is required on every touched symbol, and `gitnexus_detect_changes()` before the implementation PR. Likely-touched modules (to be confirmed by impact analysis at build time):

Likely new modules:

- `runtime/daemon/wake_queue.py`
- `runtime/daemon/wake_runner.py`
- `runtime/daemon/work_hours_scheduler.py`
- `runtime/daemon/routes/work_hours.py`
- `runtime/infrastructure/work_hours_store.py`
- `cli/commands/work_hours.py`
- a routine-section parser helper (likely beside `runtime/orchestrator/prompt_loader.py`)

Likely modified modules:

- `runtime/orchestrator/org_config.py` (new `WorkingHoursConfig` + `_parse_working_hours` + tier resolution)
- `runtime/infrastructure/database.py` (new `work_hours` table only; **no** existing-column change)
- `runtime/infrastructure/audit_logger.py` (new `log_work_hour_*` methods)
- `runtime/daemon/app.py` (start/stop `work_hours_scheduler_loop`)
- `runtime/daemon/state.py` (per-org `wake_queue`)
- `runtime/models.py` (`WorkHourStatus`, `WorkHourRecord`)
- `runtime/daemon/routes/tokens.py` (recognize `work_hour` scope value)
- `cli/main.py` (register `work-hours` verbs)
- OpenAPI snapshot + `web/src/lib/api/` mirrors for founder-facing routes

The implementation should reuse the dreaming scheduler/queue/runner skeleton and shared executor/token-parsing/bootstrap helpers, while keeping the wake decoupled from `TaskRecord` (linkage is only the recorded `spawned_task_ids`), from dream lifecycle, and from talk/thread state. The wake prompt is composed in `wake_runner` (no `protocol/` edit needed to ship).

## Resolved Decisions

All seven design questions raised in the proposal have been ruled on and are now baked into the spec body above. This record preserves the question → ruling → who-ruled trail; the body is authoritative.

| # | Question | Ruling | Ruled by |
| --- | --- | --- | --- |
| Q1 | Middle-tier key — `role` vs `team`. | **Key Tier 2 by `team`.** Precedence is `org default → team default (working_hours.teams.<team>) → agent override`. Team keying gives per-function defaults (e.g. a `customer_service` team continuous by default) without per-agent entries. *(Baked into: Org And Per-Agent Configuration — config block, precedence list, validation; Test Plan.)* | Engineering Manager (founder-unvetoed) |
| Q2 | Worker-vs-manager root-task targeting. | **The waking agent is the owner/executor of its own routine tasks.** Each spawned root task is created targeted to the waking agent (`assigned_agent = <waking agent>`): a worker executes its task directly (bounded work → report), a manager's task enters that manager's own decision loop. Intended orchestrator semantics; implementer must verify the entrypoint supports executor targeting and originator attribution (no new `tasks` column) via GitNexus impact analysis at build time, and escalate to the founder if it requires a task-creation-contract change. *(Baked into: Invocation Contract steps 2–3; Test Plan executor-targeting item.)* | Founder |
| Q3 | Missed-slot policy. | **Confirm no-backfill.** Only the single most-recent due slot is considered at startup; intermediate missed slots are never backfilled and historical days are never replayed. A 24h agent recovering from an outage fires once, not once per missed interval. *(Baked into: Non-Goals; Scheduler — startup catch-up.)* | Engineering Manager (unvetoed) |
| Q4 | Partial-spawn atomicity. | **Confirm no-rollback.** Already-created root tasks proceed; the wake is marked `failed` with a `partial_spawn` error. Already-spawned routine work is real work and is not discarded. *(Baked into: Failure Handling — partial spawn.)* | Engineering Manager (unvetoed) |
| Q5 | Per-routine team tag. | **Drop the tag entirely for v1.** Spawned tasks are always on the waking agent's own team, so the optional `(team: …)` selector is dead weight. Removed from the parse contract, payload shape, daemon validation, Security, and Test Plan. The self-team-only invariant now holds structurally (no cross-team path from a wake). *(Baked into: Routine Tasks parse contract; Invocation Contract payload + validation; Security; Test Plan.)* | Engineering Manager (unvetoed) |
| Q6 | Interval format and divisibility. | **Confirm `Nh`/`Nm` format with the divide-24h constraint** for continuous mode (keeps slot boundaries aligned across local dates so `00:00` always exists). *(Baked into: Interval And Slot Grid; Validation.)* | Engineering Manager (unvetoed) |
| Q7 | Always-a-session vs daemon-direct spawn. | **Keep the always-a-session model.** One wake executor invocation per slot reads the checklist and self-dispatches; no daemon-direct spawn path in v1 (it would produce unattributable, zero-token task births). Cost note retained; recommend sane continuous intervals (30–60m for a CS agent) to bound the 96-wakes/day worst case. *(Baked into: Wake Input And Prompt — "Always a session (settled)".)* | Founder |
