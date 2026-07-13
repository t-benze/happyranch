# Runtime And Configuration

## Settings

Operational settings are represented by `Settings` in `runtime/config.py`.

Resolution order:

1. `HAPPYRANCH_`-prefixed environment variables.
2. `<daemon-home>/config.yaml`, defaulting to `~/.happyranch/config.yaml`; keys are field names without the prefix.
3. Code defaults.

There is no `.env` support. `settings_customise_sources` drops dotenv and adds `YamlConfigSettingsSource`. The daemon home resolver is inlined in `config.py` as `_daemon_home` to keep `config` free of a daemon dependency. Do not confuse daemon-level `config.yaml` with each org's `<runtime>/orgs/<slug>/org/config.yaml`.

| Variable | Default | Description |
| --- | --- | --- |
| `HAPPYRANCH_CLAUDE_CLI_PATH` | `claude` | Path to Claude Code CLI |
| `HAPPYRANCH_CODEX_CLI_PATH` | `codex` | Path to Codex CLI |
| `HAPPYRANCH_OPENCODE_CLI_PATH` | `opencode` | Path to opencode CLI |
| `HAPPYRANCH_PI_CLI_PATH` | `pi` | Path to Pi CLI |
| `HAPPYRANCH_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `HAPPYRANCH_PROTOCOL_DIR` | `protocol` | Protocol docs dirname relative to project root |
| `HAPPYRANCH_MAX_ORCHESTRATION_STEPS` | `50` | Max manager decision steps before escalation |
| `HAPPYRANCH_QUEUE_WORKERS` | `3` | Daemon-wide `run_step` worker slots; must be greater than 0 |
| `HAPPYRANCH_SESSION_TIMEOUT_SECONDS` | `1800` | Global agent-session timeout default |
| `HAPPYRANCH_EXECUTOR_CEILING_DEFAULT` | `8` | Per-provider concurrent-launch ceiling (issue #85); must be greater than 0 |
| `HAPPYRANCH_EXECUTOR_LAUNCH_SPACING_SECONDS` | `1.5` | Minimum interval between same-provider launches; `0` disables spacing |
| `HAPPYRANCH_ORG_SLUG` | unset | Default org slug for per-org CLI commands |

`executor_ceiling_overrides` (a `dict[str,int]`, e.g. `{"codex": 12}`) and `executor_rate_limit_backoff_seconds` (a `list[int]`, default `[5, 15, 45]`) are list/dict-shaped, so they are set via `config.yaml` rather than a scalar env var. See [Executor Throttle](#executor-throttle).

Slug resolution for per-org commands: explicit `--org <slug>` > `HAPPYRANCH_ORG_SLUG` > auto-infer only when exactly one org exists > error. Container-level commands such as `happyranch init`, `happyranch use`, and `happyranch orgs ...` take no `--org`.

## Executor Throttle

A process-wide, **per-provider** throttle (`runtime/orchestrator/throttle.py`, issue #85) gates every agent-subprocess launch at the single chokepoint `executors._run_command`, which both the task `run_step` pool and the thread-reply pool reach on an OS thread. It caps concurrency, de-bursts launches, and absorbs transient 429s — without resizing either pool (they stay as producers; the semaphore is the consumer-side cap). Decision record: [`docs/adr/0001-per-provider-executor-throttle.md`](../adr/0001-per-provider-executor-throttle.md).

Keyed by provider string (`claude | codex | opencode | pi | ...`), so saturating one provider never blocks another:

| Setting | Default | Meaning |
| --- | --- | --- |
| `executor_ceiling_default` | `8` | Per-provider `BoundedSemaphore` size; max concurrent subprocesses for one provider across both pools. Must be > 0. |
| `executor_ceiling_overrides` | `{}` | Per-provider ceiling override (config.yaml), e.g. `{"codex": 12}`. |
| `executor_launch_spacing_seconds` | `1.5` | Minimum interval between same-provider launches. `0` disables. Cross-provider launches are never spaced against each other. |
| `executor_rate_limit_backoff_seconds` | `[5, 15, 45]` | On a rate limit in a **failed** launch (the retry is gated on `rate_limited and not success`, so a successful session is never relaunched) the launch releases its slot, sleeps `backoff[attempt]`, re-acquires, and retries. After the schedule is exhausted the result falls through to `run_step._classify_failure_kind`. `[]` disables retries. |

Rate-limit detection is normalized: `_run_command` sets `ExecutorResult.rate_limited` from `is_rate_limit_signature(...)` and the classifier prefers that field over its legacy string heuristic. Two additive audit actions surface the activity through the existing `insert_audit_log` (no schema change): `executor_slot_wait` (`{provider, wait_seconds, ceiling}`) when a launch waited for a slot, and `executor_rate_limit_backoff` (`{provider, attempt, backoff_seconds}`) per 429 retry.

The list/dict-shaped keys (`executor_ceiling_overrides`, `executor_rate_limit_backoff_seconds`) are set via `config.yaml`; the scalar keys also accept `HAPPYRANCH_`-prefixed env vars.

## Metrics Persistence (THR-066)

The daemon persists runtime metrics as a time-series of full snapshots in a
**daemon-global** SQLite store at `<runtime_root>/metrics.db` — a sibling of
`orgs/`. This is NOT a per-org store; the metrics aggregate spans all orgs
(uptime, loop ticks, HTTP latency histograms, task/job/session/queue counts).

| Property | Value |
| --- | --- |
| Store file | `<runtime_root>/metrics.db` |
| Table | `metrics_snapshots (id INTEGER PK, captured_at TEXT NOT NULL, snapshot_json TEXT NOT NULL)` |
| Index | `idx_metrics_snapshots_captured ON metrics_snapshots(captured_at)` |
| Cadence | ~60s (piggybacks `work_hours_scheduler_loop`; throttled to one write per ~55s) |
| Retention | 30 days (pruned on each write; module constant `_RETENTION_DAYS`) |
| Pattern | Append-only — same durable pattern as `audit_log`, but a separate store (no `audit_log` overload) |

The snapshot payload is the same dict returned by `GET /api/v1/metrics`:
`MetricsRegistry.snapshot()` plus live pull-gauges (`tasks`, `jobs_in_flight`,
`executor_sessions_active`, `run_step_queue_depth`). Both the route and the
periodic writer call the shared `compose_metrics_snapshot(state)` helper in
`runtime/daemon/metrics_store.py` so the persisted payload stays byte-identical
to the live route response.

The store is constructed at daemon startup on `DaemonState` (from
`DaemonState.from_runtime` or `DaemonState.idle`). Schema creation is
idempotent (`CREATE TABLE IF NOT EXISTS`); re-initializing the store after a
restart is a no-op.

**Compatibility:** v0 (DB-backed enrollments) and v1 (flat single-org) runtimes
both get the store on startup — the store is created on demand regardless of
runtime shape and touches no existing DB.

### GET /api/v1/metrics/history — persisted snapshot history

Returns persisted metrics snapshot rows from the `metrics_snapshots` table,
newest-first. Requires bearer auth (inherited from the `metrics` router).

**Request:**

```
GET /api/v1/metrics/history?since=<ISO>&until=<ISO>&limit=<int>
```

| Param | Type | Default | Description |
| --- | --- | --- | --- |
| `since` | ISO-8601 string | none | Lower bound on `captured_at` (inclusive) |
| `until` | ISO-8601 string | none | Upper bound on `captured_at` (inclusive) |
| `limit` | int | 500 | Max rows to return (capped at 5000, min 1) |

**Response** `200 OK`:

```json
{
  "snapshots": [
    {
      "id": 42,
      "captured_at": "2026-07-04T12:10:00+00:00",
      "snapshot_json": "{...}"
    }
  ]
}
```

When `since` and `until` are both omitted, returns the `limit` most recent rows.
When the daemon state is idle (`metrics_store` is `None`), returns
`{"snapshots": []}` gracefully (never 500).

## System Assistant

The system assistant is runtime-global and lives under `<runtime>/system/assistant/`.
It is not an org agent and must not appear in `org/agents/` or `teams.yaml`.

Initialize or repair it on the active runtime:

```bash
happyranch assistant init
happyranch assistant init --repair
happyranch assistant init --reconfigure
```

Onboarding is by self-registration. `happyranch assistant init` prepares or
repairs the assistant workspace and writes registration instructions; the
founder opens their own agentic CLI there and it completes configuration by
calling back `happyranch assistant register --from-file <payload>` declaring an
agent-chosen `{executor, command, argv}`. The daemon validates the payload
structurally only — non-empty fields and `shutil.which(argv[0])` resolves (no
allowlist, no absolute-path requirement, no `$PATH` guard) — then auto-configures
with no separate approval. `happyranch assistant` tells the user to run
`happyranch assistant init` when no assistant config exists.

## Org Config: Timezone and `current_time` Prompt Injection

Top-level `timezone:` in `<runtime>/orgs/<slug>/org/config.yaml` is the org-wide
local zone. It is optional; an explicit value must be a valid IANA name
(validated at load). `None` (the default) means **inherit machine-local**.

`org_config.resolve_org_timezone[_display]` resolves the effective zone:

1. explicit IANA name → `ZoneInfo(value)` (a bad value falls through, never crashes);
2. `None` → machine-local: the IANA name derived from `/etc/localtime` when
   possible, else a fixed offset from `datetime.now().astimezone()`
   displayed as `UTC±HH:MM`;
3. ultimate fallback → UTC.

A `current_time:` line is injected into **every** executor-backed agent session
prompt — across all providers (claude, codex, opencode, pi), fresh on every
spawn, wake, and turn. The single shared renderer `org_config.render_current_time_line(tz, label, now)`
produces the line; each prompt builder resolves its own effective zone and
calls it, so the line is identical everywhere. The four session types and their
builders are:

- **task / subtask** — `Orchestrator._build_agent_prompt` (the shared
  `Parameters:` block), zone via `resolve_org_timezone_display`. `run_step._build_agent_prompt`
  is **not** a separate path: it builds only the inner `role_guidance` body,
  which is wrapped by `Orchestrator._build_agent_prompt`.
- **working-hours wake** — `wake_runner.build_wake_prompt`, zone via `resolve_org_timezone_display`.
- **thread reply/bootstrap** — `thread_runner.build_thread_prompt` (full) and
  `build_thread_delta_prompt` (resumed-turn delta), zone via `resolve_org_timezone_display`.
- **private dream** — `dream_runner.build_dream_prompt`, zone via the dreaming
  precedence `resolve_dreaming_timezone_display` (`dreaming.timezone → org.timezone → machine-local → UTC`).

Format: ISO-8601 with offset plus the zone label, e.g.
`2026-06-27T12:47+08:00 (Asia/Shanghai)`, or `2026-06-27T12:47+08:00 (UTC+08:00)`
when only an offset is derivable. The wall clock is an injectable `now` callable
(default `datetime.now(timezone.utc)`) so prompt snapshot tests are deterministic.

## Org Config: Dreaming

Per-org `dreaming:` config controls the private nightly reflection scheduler: enablement, local schedule time/timezone, catch-up behavior, and agent include/exclude selection.

`dreaming.schedule.timezone` is **inherit-by-default**: an omitted value resolves
`dreaming.timezone (explicit) → org.timezone → machine-local → UTC` via
`resolve_dreaming_timezone`, threaded into `dream_scheduler._scheduled_datetime`
before any `ZoneInfo()` call. (Pre-TASK-976 an omitted value defaulted to the
literal `UTC`; orgs relying on that implicit default now schedule on
machine-local time — host-local night, as intended.)

## Agent Configuration: Single Source of Truth (THR-095)

**Founder-ratified invariant (THR-095 option B):** Every piece of agent
configuration has **exactly one authoritative store**. Two surfaces for the
same value is a breach. There is no precedence ladder — the founder explicitly
rejected resolution-order ladders as a design pattern.

For org agents, the single authoritative store is the **org frontmatter**
(`orgs/<slug>/org/agents/<name>.md`, parsed as ``AgentDef``). The three fields
that were previously dual-surfaced — ``executor``, ``repos``, and ``model`` —
are now read and written **exclusively** through ``AgentDef``:

| Field | Authority | Consumer |
| --- | --- | --- |
| ``executor`` | ``AgentDef.executor`` | ``_resolve_executor_name``, ``thread_runner``, ``dream_runner``, ``wake_runner`` |
| ``repos`` | ``AgentDef.repos`` | ``list_agents``, ``init_agents`` clone loop |
| ``model`` | ``AgentDef.model`` | ``_resolve_model_name``, ``_resolve_agent_model`` |
| ``allow_rules`` | ``AgentDef.allow_rules`` | (already .md-only before THR-095) |

The workspace ``agent.yaml`` file is **no longer read or written** by any
org-agent path. A one-shot startup migration (``migrate_agent_yaml_to_frontmatter``,
idempotent, runs on every daemon start) copies any residual ``agent.yaml``
values into their owning ``.md`` exactly once, then the ``agent.yaml`` is
left untouched. The system assistant (``runtime/system_assistant.py``) is a
**separate subsystem** and writes its own ``agent.yaml`` directly — it has no
``org/agents/`` file and is unaffected.

See also: `docs/agent-guides/orchestrator-contracts.md` (resolver contract),
`docs/agent-guides/agent-executors-and-permissions.md` (executor surface).

## Session Timeout Resolution

`Orchestrator._resolve_session_timeout(agent_name, task_id=...)` walks three layers:

1. Task override: `tasks.session_timeout_seconds`, set via `happyranch revisit ... --session-timeout-seconds N` and inherited by children.
2. Org override: `session_timeout_seconds:` in `<runtime>/orgs/<slug>/org/config.yaml`.
3. Code default: `Settings.session_timeout_seconds`.

Positive integers only. `<= 0` or non-int raises at parse time. The `agent_name` argument is unused but kept for call-site symmetry. Legacy `session_timeout_seconds` in agent frontmatter is silently ignored.

## Bounded Failure-Recovery (TASK-573)

When a subtask fails, the parent task is re-enqueued for a bounded manager-wake
decision step — NOT cascade-failed. This replaces the pre-TASK-573 behavior where
any subtask FAILED unconditionally cascade-failed the parent without giving the
task owner a chance to re-ground.

Contract (founder-approved in THR-028):

1. **Bounded wake.** On subtask failure, re-enqueue the parent for a fresh
   decision step. The failed subtask's reason (`note` + completion report /
   error context) is available so the task owner can author an updated brief.

2. **Round bound.** At most 2 re-spawn rounds per delegation slot. The round
   count is derived from EXISTING database state (count of FAILED subtask
   siblings) — no schema migration, no new/alter/overload column.

3. **Escalation on exhaustion.** When the bound is exhausted (> 2 FAILED
   subtasks in this delegation slot), the parent transitions to
   `escalated` via `try_escalate()`, carrying the last failure
   reason. The parent does NOT cascade-fail — the founder can resolve the
   escalation per existing routes.

4. **Chain-leg failure.** A failed workflow chain leg (subtask FAILED, not
   COMPLETED) clears the active chain and hands the parent back to its
   bounded-wake path (same 2-round bound + escalation).

5. **Happy path unchanged.** All subtasks COMPLETED → parent enqueued for
   next decision step. REVISE-verdict auto-advance in chains is unchanged.

6. **Reviewer/QA verdict discipline.** A review/QA leg completes with an
   APPROVE/REVISE/PASS/FAIL verdict and never self-blocks. A `status=blocked`
   with empty `waiting_on_job_ids` is a malformed report; the leg is treated
   as FAILED and wakes the parent for a decision step.

Implementation: `runtime/orchestrator/run_step.py` —
`_enqueue_parent_if_waiting`, `_advance_chain_for_completed_child`,
`_FAILURE_ROUND_BOUND`. See also
`docs/agent-guides/features-and-invariants.md#bounded-failure-recovery` and
`docs/agent-guides/orchestrator-contracts.md`.

## Running The Daemon

The CLI is an HTTP client. Start the daemon once, then run CLI commands.

```bash
scripts/daemon.sh start
scripts/daemon.sh status
scripts/daemon.sh stop --force     # graceful shutdown (default daemon needs --force)
scripts/build_web.sh
happyranch web [--no-open]
```

The full founder-facing CLI is documented in `skills/happyranch/SKILL.md`.

## Running Tests

```bash
uv run pytest tests/ -v                  # unit tests only (default)
uv run pytest tests/ -v -m integration   # integration tests
uv run pytest tests/ -v -m ""            # unit + integration
```

Integration tests spawn a real daemon and fake CLIs. They are isolated from `~/.happyranch/` via `HAPPYRANCH_DAEMON_HOME`. Run integration tests locally before changes touching daemon lifespan, `SessionTracker`, callback routes, queue recovery, or executor callback behavior.

`tests/integration/fake_claude.sh` routes task invocations through `$FAKE_CLAUDE_PLAN` and thread invocations through `$FAKE_CLAUDE_THREAD_PLAN`. Tests that exercise both flows must set both fixtures.
