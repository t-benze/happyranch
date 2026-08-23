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
| `HAPPYRANCH_CLAUDE_CLI_PATH` | `claude` | Default command metadata for claude (config/docs only — executor launch requires ``executors.json`` pin) |
| `HAPPYRANCH_CODEX_CLI_PATH` | `codex` | Default command metadata for codex (config/docs only — executor launch requires ``executors.json`` pin) |
| `HAPPYRANCH_OPENCODE_CLI_PATH` | `opencode` | Default command metadata for opencode (config/docs only — executor launch requires ``executors.json`` pin) |
| `HAPPYRANCH_PI_CLI_PATH` | `pi` | Default command metadata for pi (config/docs only — executor launch requires ``executors.json`` pin) |
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
| `executor_rate_limit_backoff_seconds` | `[5, 15, 45]` | On a rate limit in a **failed** launch (the retry is gated on `rate_limited and not success`, so a successful session is never relaunched) the launch releases its slot, sleeps `backoff[attempt]`, re-acquires, and retries. After the schedule is exhausted the task is marked terminal FAILED under normal failure handling; no daemon successor is spawned. `[]` disables retries. |

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

### HTTP route labels (route-template bucketing)

HTTP latency is labelled by the matched FastAPI **route template** — resolved
after routing and prefixed with the request method — not the literal
`request.url.path`. For example a request to
`/api/v1/orgs/tourism-org/tasks/TASK-1505/completion` is recorded under
`POST /api/v1/orgs/{slug}/tasks/{task_id}/completion`, so dynamic org slugs,
task IDs, thread IDs, and job IDs coalesce into one bounded histogram per
route instead of one unbounded key per concrete value. Method separation and
the `__all__` aggregate bucket are preserved.

Two bounded, stable fallbacks exist and can never contain a raw ID/path:

| Condition | Label |
| --- | --- |
| No matched template (e.g. 404) | `METHOD __unmatched__` |
| `call_next` raises (unhandled exception) | `METHOD __error__` (elapsed time recorded; original exception re-raised) |

### Snapshot format marker and legacy-read compatibility

The shared composer (`compose_metrics_snapshot`) adds an explicit
`format_version` marker to every snapshot: `2` means route-template labels.
Both the live `GET /api/v1/metrics` response and each persisted row carry it
through the same composer, preserving the live/persisted byte-identical
invariant. A stored row **without** the marker is legacy raw-URL-path format;
it remains queryable and readable via `/metrics/history` and is never
rewritten in place.

### Storage telemetry (non-sensitive)

Each successful snapshot persist cycle emits one structured log line with
bounded, non-sensitive operational telemetry sufficient to compare storage
growth across a full 30-day rollover: `route_label_count` (distinct labels,
excluding `__all__`), `serialized_bytes`, `row_count`, `prune_count`,
`oldest_captured_at`, `newest_captured_at`, `db_bytes`, `wal_bytes`,
`page_count`, and `freelist_count`. It never emits route IDs, task IDs, thread
IDs, org slugs, or snapshot contents. A telemetry failure is isolated and can
never crash the scheduler loop or mask a successful persist. To measure the
steady-state reduction, compare these values at the same point in two
consecutive 30-day windows — do not expect an immediate file-size drop, since
row deletion does not shrink SQLite on its own.

**Never** delete `metrics.db`, `metrics.db-wal`, or `metrics.db-shm` by hand,
and never run VACUUM/checkpoint manually outside the daemon-owned maintenance
operation below; retention pruning and the explicit maintenance operation are
the only sanctioned row-removal / compaction paths.

### POST /api/v1/metrics/maintenance — explicit quiescent maintenance

A daemon-owned maintenance operation (bearer-authed, founder-explicit) that
runs ONLY through `MetricsStore` — never a filesystem delete and never shell
SQLite. Requires explicit confirmation (`confirm_quiescent: true`) AND a
quiescent daemon (no nonterminal task, no running job, no active executor
session); otherwise it refuses with HTTP 409 (`confirmation_required` /
`not_quiescent`). On confirmed quiescence it runs under a single store lock:
prune (unchanged 30-day strict-before cutoff) → WAL checkpoint (TRUNCATE) →
`PRAGMA integrity_check` (fail closed on non-`ok`) → `VACUUM` → post-vacuum
integrity + health evidence.

```bash
happyranch metrics maintenance --confirm-quiescent
```

**Request** `POST /api/v1/metrics/maintenance` body: `{"confirm_quiescent": true}`.

**Response** `200 OK` reports before/after DB+WAL bytes, row count, cutoff,
page/free-list counts, duration, checkpoint and integrity results, and pruned
count. No raw task/thread/org identifiers are ever returned.

**Failure & recovery.** Any checkpoint/integrity/VACUUM error returns HTTP 500
(`maintenance_failed`) — never a partial success — and logs recovery guidance.
Pre-existing valid history remains queryable. Recovery requires a fresh
explicit invocation; there is no automatic retry. The periodic writer
continues to catch and log its own persistence failures without crashing the
scheduler loops. A successful `VACUUM` is the only evidence of physical space
reclamation; do not claim reclamation without it.

**Validating the projected steady-state reduction.** The bounded-cardinality
route-template labels only shrink storage as old raw-path rows age out of the
30-day window and a successful maintenance `VACUUM` runs. To validate, compare
the persist-cycle telemetry (`serialized_bytes`, `route_label_count`) plus the
maintenance `before`/`after` `db_bytes` across two full 30-day windows — the
projected 48–57% drop appears only after the old unbounded-label rows are gone.

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
structurally only (non-empty fields and `shutil.which(argv[0])` resolves; this
is self-registration, not executor-binary resolution — the THR-107 seq155
registration-only cutover applies to *executor launch*, not assistant
self-registration) — then auto-configures with no separate approval.
`happyranch assistant` tells the user to run `happyranch assistant init` when
no assistant config exists.

**Executor binary resolution** (built-in and generic-CLI executor profiles)
is registration-only: every built-in and generic-CLI custom executor must have a valid ``executors.json``
entry keyed by the profile name before launch (THR-107 seq155). Custom-adapter
profiles (``command_adapter_id: custom-adapter:<id>``) are an exception — they
use the exact founder-APPROVED, hash-verified absolute adapter executable as
their launch artifact and do **not** require a separate ``executors.json``
record keyed by the profile name. No ``shutil.which`` or PATH discovery is
used for any profile. See
[agent-executors-and-permissions.md](./agent-executors-and-permissions.md).

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
2. **Org override**: `org_settings` DB table, section `session_timeout_seconds` (THR-095 single-store).
3. Code default: `Settings.session_timeout_seconds`.

Positive integers only. `<= 0` or non-int raises at parse time. The `agent_name` argument is unused but kept for call-site symmetry. Legacy `session_timeout_seconds` in agent frontmatter is silently ignored.

## Org Settings Storage (THR-095)

The 5 web-writable operational knobs — `dreaming`, `threads`, `session_timeout_seconds`,
`working_hours`, `reviewer_agents` — are stored in the **`org_settings`** SQLite table (same per-org DB
as `tasks` / `audit_log`). `org/config.yaml` is a **git-tracked seed file only**;
the daemon **never** reads or writes these keys from the file once the one-shot
seed migration has run (first daemon startup after upgrade).  The seed also
**strips the 5 writable keys from config.yaml** (one-time mutation, atomic
write) so the file remains clean thereafter.  Every subsequent `PUT` routes
solely through the DB — the daemon no longer touches `config.yaml` for these
keys, preserving the #408 single-source-of-truth invariant.

### Schema

```sql
CREATE TABLE IF NOT EXISTS org_settings (
    section     TEXT NOT NULL PRIMARY KEY,  -- dreaming | threads | session_timeout_seconds | working_hours | reviewer_agents
    value_json  TEXT NOT NULL,             -- JSON blob for that section's subtree
    updated_at  TEXT NOT NULL,             -- ISO-8601 Z
    updated_by  TEXT DEFAULT 'founder'
);
```

### Resolution ladder

Every consumer site resolves through a **single documented precedence ladder**:

| Knob | Resolution order |
| --- | --- |
| `session_timeout_seconds` | `tasks.session_timeout_seconds` (per-task override) → `org_settings` DB row → `Settings.session_timeout_seconds` |
| `dreaming` / `threads` / `working_hours` | `org_settings` DB row → **dataclass code default** (OrgConfig field defaults, NOT config.yaml) |
| `reviewer_agents` | `org_settings` DB row → **code default `["code_reviewer"]`** (THR-175). A JSON list of agent names; configures which chain legs are reviewer legs that gate auto-advance. Names are validated against the org's live active-agent roster; an unknown name resolves fail-closed to the code default (see below). |

**Code-default tier**: the fallback is always the Python dataclass default
(e.g. `DreamingConfig(enabled=False)`, `OrgConfig().threads_enabled=True`),
never a value parsed from `config.yaml`.  This is critical: after the one-shot
seed, `config.yaml` is **not** the read source for these 4 knobs — stale
seed-file values must never become observable.  The `resolve_org_setting_*`
helpers accept a `code_default` parameter that every call site MUST pass as
the true dataclass default.

No site special-cases storage; every reader uses the appropriate
`resolve_org_setting_*` helper from `runtime/orchestrator/org_config.py`.

### Write path

`PUT /api/v1/settings/org` writes each patched section to the `org_settings`
table, with its `config:<section>` audit row **in a single SQLite transaction**
(atomic upsert + audit insert — a crash before commit rolls BOTH back).
The daemon **does not** touch the git-tracked `org/config.yaml` after the
one-shot seed — the DB is the sole write target.

### Audit

Each `config:<section>` audit row carries a `tiers` list of **exactly the keys
that changed** (not the full before-snapshot).  A partial threads update that
only changes `default_turn_cap` emits `tiers: ["default_turn_cap"]`, not
`["enabled", "default_turn_cap", "invocation_timeout_seconds"]`.  This preserves
`AuditLogger.log_org_config_write` touched-tiers semantics.

Audit rows are atomic with their settings row — a crash before commit rolls
both back (no split-brain).

### Seed migration

A **one-shot, idempotent** seed runs on the first daemon startup after upgrade
(`org/.org_settings_seeded` sentinel). It copies the current `config.yaml`
values for the 5 writable keys into `org_settings`, then writes the sentinel.
On subsequent startups the sentinel makes the seed a no-op. After seeding,
`config.yaml` values are ignored — the DB is the single authoritative store.

`reviewer_agents` (THR-175) additionally has an idempotent **backfill** that
runs on every startup for orgs whose seed sentinel already fired before the
feature shipped: if the `reviewer_agents` row is absent it persists the
config.yaml value (or the `["code_reviewer"]` code default), and it never
overwrites an existing explicit row.

`reviewer_agents` names are **validated against the org's live active-agent
roster** (the file-based `org/agents/*.md` registry) at every surface that can
seed, backfill, or persist them — not just `PUT /settings`. A configured name
that is not a real active agent is never persisted as a reviewer setting (the
code default is persisted instead), and an already-persisted malformed or
unknown value resolves fail-closed to `["code_reviewer"]` at every read path.
This guarantees an unknown reviewer string can never silently demote
`code_reviewer` from the reviewer set and re-open the QA auto-advance hole.

## Bounded Failure-Recovery (TASK-573)

When a subtask fails, the parent task is re-enqueued for a bounded manager-wake
decision step — NOT cascade-failed. This replaces the pre-TASK-573 behavior where
any subtask FAILED unconditionally cascade-failed the parent without giving the
task owner a chance to re-ground.

Contract (founder-approved in THR-028, refined in THR-078):

1. **Bounded wake.** On child failure, re-enqueue the parent for a fresh
   decision step. The failed subtask's reason (`note` + completion report /
   error context) is available so the task owner can author an updated brief.

2. **Per-slice retry ceiling (THR-078).** A delegated slot gets exactly one
   retry: the ceiling is `_SLICE_RETRY_CEILING = 1` — a slice whose
   `revisit_of_task_id` ancestor (a FAILED child of the same parent) failed
   again exhausts the ceiling. The ceiling is evaluated per-slice via
   `_is_slice_retry_exhausted` from the failing child's `revisit_of_task_id`
   lineage (no schema migration). A later COMPLETED or SUPERSEDED descendant
   in the same lineage retires earlier FAILED ancestors for ceiling evaluation
   (THR-183).

3. **Escalation on exhaustion.** When a slice's retry ceiling is exhausted
   (its 2nd failure), a root parent transitions to `escalated` via
   `try_escalate()`, carrying the causal terminal event (the current
   unresolved FAILED leaf) in the escalation reason; a completed-child wake
   cannot select a stale sibling reason. A non-root parent fails and recurses
   upward (THR-033 root-only escalation). The parent does NOT cascade-fail —
   the founder or upstream manager resolves the termination per existing routes.

4. **Chain-leg failure.** A failed workflow chain leg (subtask FAILED, not
   COMPLETED) clears the active chain and hands the parent back to its
   bounded-wake path (same per-slice ceiling + escalation).

5. **Happy path unchanged.** All subtasks COMPLETED → parent enqueued for
   next decision step. REVISE-verdict auto-advance in chains is unchanged.

6. **Reviewer/QA verdict discipline.** A review/QA leg completes with an
   APPROVE/REVISE/PASS/FAIL verdict and never self-blocks. A `status=blocked`
   with empty `waiting_on_job_ids` is a malformed report; the leg is treated
   as FAILED and wakes the parent for a decision step.

Implementation: `runtime/orchestrator/run_step.py` —
`_enqueue_parent_if_waiting`, `_advance_chain_for_completed_child`,
`_is_slice_retry_exhausted`, `_SLICE_RETRY_CEILING`. See also
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
uv run pytest tests/ -v -n 4              # unit tests only (default; -n 4 = pytest-xdist parallel)
uv run pytest tests/ -v -m integration   # integration tests
uv run pytest tests/ -v -m ""            # unit + integration
```

Integration tests spawn a real daemon and fake CLIs. They are isolated from `~/.happyranch/` via `HAPPYRANCH_DAEMON_HOME`. Run integration tests locally before changes touching daemon lifespan, `SessionTracker`, callback routes, queue recovery, or executor callback behavior.

`tests/integration/fake_claude.sh` routes task invocations through `$FAKE_CLAUDE_PLAN` and thread invocations through `$FAKE_CLAUDE_THREAD_PLAN`. Tests that exercise both flows must set both fixtures.
