# Runtime And Configuration

**G submission-schema migration (THR139 seq395).** Deliberate fresh org creation
initializes the complete G layout before attachment. Existing F/E startup,
reopen and enable retain their installed layout; S2 remains available on E.
The explicit org-only operator command is
`python scripts/migrate_workflow_submission_schema.py --runtime-root <absolute-root> --org <slug> [--check]`.
Check returns migration-needed (3) for valid F/E, ready (0) for complete G,
refused (1) for invalid source/ownership, and parser errors return 2. Actual
migration atomically replaces only submissions/events and adds the approved
three tables/two explicit indexes. Event revisions come from an unambiguous
retained submission/round/event/replay closure; ambiguity refuses without
rewriting history. Every G database, including an empty one, needs a compatible
reader. The original F/E definitions and pristine-F downgrade contract remain.
The older draft script still upgrades F to E and reports a validated G as a
no-write G replay. Legacy authority comparison uses independent complete
F/E/G whole-database references; authority-v2 remains observed-only.

An authorized upgrade requires the daemon to be stopped with its configured
home/registration observable and source owners/hosts reconciled. The command
reads bounded existing PID/port/registry evidence and reserves one SQLite
writer; it stops no process and provides no exclusion against an arbitrary
concurrent daemon start. Operator cooperation is a precondition. Active-origin
submissions retain NULL legacy result identity; separate authenticated operation
and INTEGER ordinary-result links preserve the actual result evidence.
Submission/review/link/finalizer producers, U3-U6, independent operator
acceptance, and the separate real Founder UI Request changes and Sign off are
still pending. This implementation work does not authorize live migration,
enablement, deployment or a Phase1-completion claim.


## Settings

Bundled skill sources resolve under the selected package root at
`runtime/skills/bundled/`, including installed wheels. `Settings.get_bundled_skills_dir`
uses the single resolver in `runtime/skills/sources.py`; missing sources never
fall back to another checkout. The retired `protocol_dir` setting remains only
as an inert default-valued compatibility field on the existing settings response.
Non-default YAML/environment overrides are rejected with migration guidance:
publish approved assets in the new release location, then remove the override.
There is no automatic source migration or canonical byte repair. Remove this
compatibility field only in a separately versioned settings-contract change
after deployed override inventory and client migration are complete.

Operational settings are represented by `Settings` in `runtime/config.py`.

Resolution order:

1. `HAPPYRANCH_`-prefixed environment variables.
2. `<daemon-home>/config.yaml`, defaulting to `~/.happyranch/config.yaml`; keys are field names without the prefix.
3. Code defaults.

There is no `.env` support. `settings_customise_sources` drops dotenv and adds `YamlConfigSettingsSource`. The daemon home resolver is inlined in `config.py` as `_daemon_home` to keep `config` free of a daemon dependency. Do not confuse daemon-level `config.yaml` with each org's `<runtime>/orgs/<slug>/org/config.yaml`.

The authenticated `GET|PUT /api/v1/orgs/{slug}/settings/daemon-capacity`
resource is daemon-wide despite its Settings navigation context. It exposes and
atomically stages exactly the paired `queue_workers` and
`host_global_session_cap` values using an opaque revision under a process lock.
PUT requires exactly one quoted strong `If-Match` revision header, both exact
integers, a rationale, and environment-shadow
confirmation when applicable. It preserves unrelated YAML, writes through a
same-directory fsynced temporary file and atomic replace, and records the fixed
honest actor `daemon-bearer-holder` in the existing `config:daemon_capacity`
audit scope. One correlation identity links the durable pre-replace
`daemon_capacity_config_write_authorized` row to exactly one honest terminal
row: `daemon_capacity_config_succeeded`, `daemon_capacity_config_rejected`,
`daemon_capacity_config_failed`, or
`daemon_capacity_config_publication_uncertain`. Each row contains only the
server-observed allow-listed prior/requested pair, known revisions, rationale,
and safe provenance. Audit failure before replace leaves the original YAML
untouched, and replace failure cannot produce an unaudited authoritative file.
Once atomic replace succeeds, any directory-durability, read-back validation,
response-snapshot, or temporary-cleanup failure returns the distinct
`config_publication_uncertain` outcome: the new bytes are authoritative, so
operators must reload and inspect them before retrying. The response reports
temporary-artifact state as `absent`, `present`, or `unknown`; cleanup failure
never fabricates absence or overrides publication. The writer never performs a
second unaudited replacement as compensation.

**Observed divergence from the "exactly one honest terminal row" description
above — described, not resolved here.** When the terminal audit insert itself
fails after a successful atomic replace, the shipped behaviour records the
durable `daemon_capacity_config_write_authorized` row and *no* terminal row at
all, returning `config_publication_uncertain`
(`tests/daemon/test_routes_settings.py::test_daemon_capacity_terminal_success_audit_failure_is_publication_uncertain`
asserts the audit rows are exactly `["daemon_capacity_config_write_authorized"]`).
Terminal audit completion is therefore **not guaranteed**, and nothing may
assert that a rationale "is recorded in the audit entry". This note records the
divergence between the description and the tested behaviour; it does not change
backend auditing and does not assert that the two agree. The browser capacity
panel's copy is qualified accordingly: it states that the reason is *included
in the save request* and that auditing is addressed org-locally and
bearer-attributed with terminal completion not guaranteed.
The shared daemon bearer is required; it proves possession only and cannot be
attributed to a verified person. Save is next-restart-only and cannot resize
the startup worker or HostSessionSupervisor snapshots.
The read model reports the startup supervisor's runtime-derived producer
envelope/components and capability-derived effective admission cap/reason.
A host cap below the envelope remains valid and warns about intentional
backpressure; a cap above it warns that unused admission capacity creates no
additional producers.

**Frontend numeric limitation (NOT a contract change).** The browser editor
refuses operator input outside `Number.MAX_SAFE_INTEGER` and withholds any
consumed server numeric that did not survive `JSON.parse` as a safe integer.
The API contract remains an unbounded positive integer; the browser bound is an
editor representation limit only. A residual blind spot is retained and
explicitly not closed: the shared HTTP client parses the response body and
discards the raw text, so a raw *fractional* token that `JSON.parse` rounds
into a safe integer (for example `9007199254740990.5` -> `9007199254740990`, or
`1.0000000000000001` -> `1`) passes the guard undetected. Closing that read
site needs a raw-text/BigInt-aware parse in the shared transport or a new API
representation, and is a separate decision.

| Variable | Default | Description |
| --- | --- | --- |
| `HAPPYRANCH_CLAUDE_CLI_PATH` | `claude` | Default command metadata for claude (config/docs only — executor launch requires ``executors.json`` pin) |
| `HAPPYRANCH_CODEX_CLI_PATH` | `codex` | Default command metadata for codex (config/docs only — executor launch requires ``executors.json`` pin) |
| `HAPPYRANCH_OPENCODE_CLI_PATH` | `opencode` | Default command metadata for opencode (config/docs only — executor launch requires ``executors.json`` pin) |
| `HAPPYRANCH_PI_CLI_PATH` | `pi` | Default command metadata for pi (config/docs only — executor launch requires ``executors.json`` pin) |
| `HAPPYRANCH_PERMISSION_MODE` | `auto` | Claude Code permission mode |
| `HAPPYRANCH_PROTOCOL_DIR` | `protocol` | Retired compatibility value; non-default overrides refuse startup |
| `HAPPYRANCH_MAX_ORCHESTRATION_STEPS` | `50` | Legacy accepted setting; inert (not an execution limit) |
| `HAPPYRANCH_QUEUE_WORKERS` | `6` | Daemon-wide `run_step` worker slots; must be greater than 0; restart required |
| `HAPPYRANCH_HOST_GLOBAL_SESSION_CAP` | `13` | Healthy enforcement-capable daemon-wide host-session admission cap; capability fallbacks remain conservative; restart required |
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

The configured schedule remains the default for ordinary invocations. The
bounded THR-247 Codex completion-recovery path alone opts out per invocation
at the host-supervisor seam: its first rate-limited provider result is
finalized and receipted honestly without backoff or re-admission. This does
not alter global throttle settings, ordinary invocation retries, admission, or
rate-limit diagnostics; the honest-passthrough fallback likewise uses its
existing empty executor-throttle backoff for that one provider execution.

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

**Never** delete `metrics.db`, `metrics.db-wal`, or `metrics.db-shm` by hand. Physical compaction (WAL checkpoint / `VACUUM`) is performed **only** by the sanctioned offline maintenance one-shot below — never manually and never against a live daemon.

### Offline metrics maintenance (startup-only one-shot)

Physical reduction of `metrics.db` (row deletion frees SQLite pages but does
not shrink the file) is a deliberate **offline/startup-only** operation.  There
is **no** live maintenance route, no resident maintenance gate, no
scheduler/automatic maintenance, and no traffic-quiescence system — the
daemon never runs maintenance while serving.

**Invocation (explicit one-shot, reuses the daemon bootstrap):**

```bash
python -m runtime.daemon --maintenance        # or: scripts/daemon.sh maintenance
```

The maintenance process runs **before** the daemon binds an HTTP listener,
before the FastAPI lifespan, and before any scheduler/worker starts, then
exits when maintenance completes (success **or** failure).  It never writes
pid/port files and never starts a normal daemon.  Run it only while the
daemon is stopped; it refuses when a daemon pid is alive, and SQLite
fail-closes (checkpoint busy / `VACUUM` locked) if a live holder exists.

**Ordered sequence (all through `MetricsStore`):**

1. Record bounded **before** telemetry.
2. **Prune** rows strictly before the unchanged **30-day cutoff**
   (`_RETENTION_DAYS = 30`).
3. **Checkpoint** the WAL (`PRAGMA wal_checkpoint(TRUNCATE)`); a busy result
   fails closed.
4. `PRAGMA integrity_check` must return exactly `ok` **before** compaction.
5. Controlled `VACUUM`.
6. **Post-VACUUM WAL checkpoint** (`PRAGMA wal_checkpoint(TRUNCATE)`); a
   busy/error result fails closed — no success report is ever produced after
   a failed post-vacuum checkpoint.
7. Final `PRAGMA integrity_check` must return exactly `ok` (post-vacuum
   integrity evidence).
8. Record bounded **after** telemetry.

The report/log captures before/after DB & WAL bytes, row count, cutoff,
page/free-list counts, duration, pre- and post-vacuum checkpoint/integrity
outcomes, prune count, snapshot-size and route-label cardinality — never
labels, IDs, slugs, or snapshot content.

**Failure/recovery:** an invalid integrity/checkpoint/VACUUM result, stale or
malformed runtime state, or any operational exception returns a **nonzero**
exit with a stable bounded/redacted classification plus fixed recovery
guidance only — never raw exception text, tracebacks, filesystem paths, or
injected content — and no success claim, no automatic retry.  Valid
pre-existing historical rows remain queryable where SQLite guarantees it.
Retry requires a fresh explicit invocation.  Never hand-edit or delete
`metrics.db`, `-wal`, or `-shm`; never run a live compaction against a
production database while the daemon serves.

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

Register and repair also reconcile the canonical system-contract union into
both `<workspace>/.agents/skills/` and `<workspace>/.claude/skills/`. The
runtime-global assistant has no repository or org custom-skill context, so the
exact set is `dream`, `jobs`, `start-task`, `thread`, `todos`, and
`workspace-cleanup`. Repeated repair preserves the instruction pair, config,
knowledge, learnings, logs, and other assistant workspace content. Existing
corrupt canonical packages and unsafe, non-link, or wrong-target skill entries
are detected by a read-only preflight before any workspace write. Refusal leaves
both skill roots, instructions, metadata, knowledge, learnings, logs, and config
unchanged. A later materializer-only failure removes only links and empty parent
directories that were absent before that call; bootstrap never reconstructs a
corrupt package or rewrites/removes pre-existing operator content on refusal.

entry keyed by the profile name before launch (THR-107 seq155). Custom-adapter
profiles (``command_adapter_id: custom-adapter:<id>``) are an exception — they
use the exact founder-APPROVED, hash-verified absolute adapter executable as
their launch artifact and do **not** require a separate ``executors.json``
record keyed by the profile name. No ``shutil.which`` or PATH discovery is
used for any profile. See
[agent-executors-and-permissions.md](./agent-executors-and-permissions.md).

Supported active roster creation, approval, revision-CAS executor updates,
dedicated executor updates and termination maintain exact per-agent profile
requirements inside their canonical fence; pending enrollments do not bind.
Prompt/model/repo updates preserve the current profile while composing only their intended delta inside the process writer gate. Init and active create/approval retain that gate through final current capture and bootstrap; executor switching retains a single gate through materialization, synchronous bootstrap, final mutation and compensation. Request cancellation and SSE disconnect drain started workers while retaining the gate; admitted request writes finish terminal reconciliation before propagating cancellation, including repeated cancellation. The short teams/canonical sections end before awaited host work.
Their profile leases release before ordinary publication capture and awaited
bootstrap, including compensation exits. At daemon state construction, U2B rebuilds exact per-agent custom-profile
dependencies for every loaded org and reconciles any interrupted coordinated
profile operation before the state is exposed to routes. It uses the U1A
org-local profile relations plus an owner-only same-host `flock`; there is no
new machine-global database or schema. A coherent dependency change publishes
a new org authority generation, while an absent, removed, or otherwise
unpublished required profile or a profile whose custom adapter is not currently
approved and resolvable keeps the org fenced. The shared profile YAML's
read/merge/replace writers additionally take one store-scoped leaf `flock`, so
different profile leases cannot lose each other's entries. Direct-connect
`planned` projections are production-sweep eligible after transient profile
contention. Independent route/sweep contenders re-read the durable terminal row
under the profile lease before any mutation or fence. Dynamic org attachment
captures canonical authority/roster inputs outside profile/publication leases
and SQLite transactions, brackets discovery with the existing durable authority
revision, and validates that revision under profile-then-org mutation ownership.
Changed captures retry boundedly or refuse; synchronization consumes the captured
roster without another directory scan. It holds every canonically ordered
referenced profile lease through coherent synchronization/publication and
shared-map insertion; its mirror
digest must equal the current global digest before readiness is exposed.
Startup does not dispatch, activate, or admit workflow work.

THR139 S1 separates existing and new databases. Existing `OrgState.load` retains
foundation installation where required, validates full F/E/G before workflow
recovery, and never adds the draft extension. F remains usable for legacy work;
its cutover projection/log names the operator migration and recovery waits for validated E/G.
POST /orgs creates a fresh skeleton, initializes complete G before attachment,
and retains its existing cleanup/error ownership. Empty files/missing tables or
startup discovery are not proof of new creation. Generic Database/runtime-audit
construction remains workflow-free.

Compatible cold reopen preserves the complete durable schema, every table's
data and row identities, and the file set, modes and non-database bytes.
Ordinary authority recovery can commit and release an ephemeral lease, changing
SQLite physical pages; closed owners leave no lease or database sidecar residue.
Validator-only and unsupported-reader refusal checks separately retain exact
entire file-byte and mode preservation on a closed database.

Explicit existing-org migration (operator authorization required):

```bash
python scripts/migrate_workflow_draft_schema.py --runtime-root <absolute-root> --org <slug> --check
python scripts/migrate_workflow_draft_schema.py --runtime-root <absolute-root> --org <slug>
```

The script validates the schema-v2 runtime and actual org path, full F/E SQL,
markers/history/integrity and stored source/draft closure without running generic
migrations. One bounded SQLite writer transaction installs only the exact three
draft tables/six indexes and version1 marker, validates E, then commits. Partial,
unknown/corrupt layouts and nonorg/missing/symlink targets refuse; complete E/G
replay preserves existing data and labels its actual layout. `--check` performs no migration, exits0 for ready
E/G or3 for migration-needed F; refusal1, parser2. Pristine F retains preceding-reader
compatibility until migration. **Every E/G requires a compatible reader**, including
new empty orgs; no downgrade stripping or live migration is implied by shipping
this script. Read-only WAL inspection can use SQLite sidecars; crash rollback may
leave a non-hot journal, while original data/schema/files remain intact.


Adapter approval propagates the profile target selected before lease
acquisition into its existing serialized registry writer, including an empty
selection. It revalidates that target before approval, idempotent return or
binding. A concurrent supported submission changing the target returns the
existing 409 `profile_consumer_changed` conflict without adapter/profile
mutation; retry selects afresh after ownership releases. Known-target live
contention remains 409 `profile_coordinator_busy`. No-target approval still
succeeds without a profile, and no new lease is taken under the writer lock.

A genuinely empty default org remains attached with no agents and `teams=[]`
when its initial authority publication is fenced by the missing default reviewer.
Attachment proves absence of active and pending definitions and canonical/in-memory
teams outside leases and transactions, brackets that discovery with the durable
revision, and validates it under profile-then-org ownership. It preserves the
initial fenced generation and publication journal; `verify_admission_ready()`
still refuses `authority_pointer_not_ready`. Outstanding dependency or profile
operation evidence, unfinished canonical writers, and stale captures refuse this
exception before synchronization. Reviewer policy and snapshot validation do not
change; subsequent coherent canonical setup uses ordinary publication/recovery.

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

## Org Config: Workspace Cleanup

`workspace_cleanup.enabled` is a boolean scheduler switch that defaults to
`true`; setting it to `false` disables the daemon-managed cleanup scheduler. When
enabled, the scheduler evaluates the daily local 03:30 occurrence in the org
timezone, comparing existing occurrences as UTC instants (skipping nonexistent
spring-forward times and taking the first `fold=0` instance of ambiguous fall-back
times), with exactly one post-warmup current-window catch-up, no historical backfill
and no rolling 24-hour or seven-day trigger cooldown.
`workspace_cleanup.reclamation_actions_enabled` is separately strictly boolean
and defaults to `false`. When true, the bounded pre-agent reclamation hook may
select and revalidate finite canonical targets before invoking the existing
consumer; it acts only on a third-or-later cleanup ordinal whose preclaim owner
is assigned to a registered in-memory `TeamsRegistry` agent and reconciles to
the invocation's initial successful claim (the first two runs stay
report-only), under one shared one-second deadline and at most 23 read/load
admissions with at most five best-effort consumer calls and no refill or
recovery. `false` prevents those action admissions and affects later admissions
only; it cannot revoke an already admitted call. Malformed values retain the
shared loader's existing error behavior.

Routine scheduled and exact-marker manual cleanup reports remain durable on the
existing agent page via ordinary completion/results (THR-259 seq418). The scheduler
admits a clean-root task without report-thread configuration, lookup, ID allocation
or composite creation. Task ID allocation, composition and ordinary insertion stay
synchronous under `org.db_lock` after awaited measurement; enqueue follows successful
insertion. Complete marker history, ordinals, cadence and deduplication are unchanged.
There is no routine thread posting/reuse obligation. Preserve all historical threads,
messages, associations, results and audits, explicit founder-requested coordination,
truthful anomalies/partial failures/unknown bytes/independent verification gaps and
the current-session final `happyranch report-completion` callback. Exact manual
first-line display eligibility (alone/LF/CRLF) neither creates trigger audits nor
changes daemon count or action authority. Runtime and canonical bundled-skill
rollout require separately authorized deployment after merge.

Cleanup activity uses three nonunique indexes installed with `IF NOT EXISTS`
after legacy columns exist: tasks(assigned_agent,created_at DESC,id DESC),
audit_log(task_id,agent) where action='workspace_cleanup_triggered', and
task_results(task_id,agent,id DESC). Existing definitions are not validated;
reader SQL, history and cleanup authority are unchanged. Breaker listing/mint
use sequential `await asyncio.to_thread` calls and the original DB RLock, also
used by close. Removal can log a sweep error; cancellation can leave a worker
running and a committed pending token for later attached-tick recovery. No
org lifetime lock, worker drain or new shutdown guarantee is added.

The daemon-composed daily brief and manual dispatch both follow the ONE shared
`workspace-cleanup` TASK system contract (`requires_repo=false`; source
`runtime/skills/bundled/workspace-cleanup/SKILL.md`), whose exact manual first
line is `HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)` (an unmarked
manual request is inventory-only). Its bundled read-only
`scripts/check_path_use.py` applies the approved THR-259 seq171/seq185
observation: an authoritative recorded terminal status plus a fresh complete
same-user process scan replaces separate live-session/task-to-process identity,
and a fixed login/session daemon (sshd-session, systemd --user, (sd-pam),
ssh-agent, gpg-agent, gcr-ssh-agent) qualifies only by exact readable process
name AND exact bounded cgroup role and is deliberately uninspected; any other
unreadable same-user process is `unknown` and skips.

The shared procedure executes that scanner only through a task-bound,
host-visible HappyRanch job and validates a closed-schema, non-truncated receipt
binding task/session, actual job, stored execution identity, terminal result,
complete output totals, and scanner coverage; direct in-session fallback is
forbidden. Candidate-related task and trigger evidence uses complete paging
rather than an org-wide history cap. PR evidence is completely paginated and
repeated, with every open, closed-unmerged, duplicate, changing, conflicting,
or malformed result refusing. Preservation accepts the existing durable ref,
an owning origin task branch whose head equals or descends from the candidate,
an owning-task merged PR, or an any-task merged PR whose confirmed head
contains the candidate. An existing owning branch is authoritative:
non-containment or failed containment evidence refuses without merged-PR
fallback. The last route requires complete stable double-read
discovery, merged/default-branch confirmation, and a separate complete stable
compare; discovery alone and other-task unmerged PRs never count, while an
owning-branch unmerged PR still refuses. Merged integration may not preserve
original commit topology. Dirty whole worktrees remain protected, with the sole narrow exception
of a literal root `.venv`/`node_modules` cache whose removal leaves tracked
source bytes and Git status unchanged.
The containing worktree must be at the owning primary's exact registered
`.claude/worktrees/<TASK>` location on `task/<TASK>`. A complete no-follow
`lstat` walk before and at action time refuses nested mounts, cross-device or
foreign-owned entries, protected descendants, incomplete evidence, and drift.
Root-plus-descendant byte accounting precedes action; a success receipt requires
literal absence and unchanged protected-path identities.

## Terminal task-worktree reclamation

Terminal task-worktree reclamation has no configuration key or cadence. On the
approved ordinary `completed`, `failed`, and `cancelled` writer seams, after
durable terminal state and applicable process/session/control/job teardown, the
runtime makes one bounded attempt for only the assigned registered agent's
literal `repos/happyranch/.claude/worktrees/<task-id>` candidate. It requires a
canonical non-symlink same-device primary and worktree, exact Git registration
and branch identity, clean status, durable remote containment, no open or
closed-unmerged PR, no live session/control/PID or shared-scanner process reference, no recorded
`worktree-deferred:` risk, and a shared deadline. Unknown, unavailable,
malformed, timed-out, dirty, unpublished, live, foreign, or ambiguous evidence
preserves the worktree.

The process gate loads the bundled `workspace-cleanup` scanner by explicit file
path, so its seq171/seq185 exact name-plus-expected-cgroup exception table is
the single source for both paths. Root-owned processes are out of scope; any
other unreadable same-user process is uncertain; a positive
cwd/root/exe/maps/fd reference is live. `run_step` already executes in the
queue's worker thread, and the terminal hook retains one five-second total
deadline.

Successful removal is literal non-force `git worktree remove`; no branch is
deleted. A failed gate or removal is a typed/logged preservation outcome and
never changes terminal semantics or schedules a retry. `superseded`,
`blocked_on_job`, accepted/restart completion-recovery settlement, legacy
normalization, and historical cleanup remain outside this mechanism. This is
separate from `workspace_cleanup.enabled` and
`workspace_cleanup.reclamation_actions_enabled`; neither switch expands or
disables the terminal hook.

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
values into their owning ``.md`` exactly once, then deletes ``agent.yaml`` and
writes the ``.agent_yaml_consumed`` sentinel. The system assistant (``runtime/system_assistant.py``) is a
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

2. **Mechanical retry provenance (THR-078 / THR-091).** A manager may
   re-dispatch unchanged work or direct revised work with a valid
   `revisit_of_task_id` link to a same-agent FAILED child under the current
   parent. A current successor root may also link the same-agent FAILED child
   of a predecessor root when the bounded verifier authenticates every
   intervening recorded supersession (at most 20 root records / 19 edges).
   The final spawn transaction repeats claim and lineage validation after
   `BEGIN IMMEDIATE`; an unresolved local failure cannot be bypassed by a
   remote historical link. Original parent links and failed history remain
   unchanged. The link is historical provenance, not semantic brief
   comparison or automatic root escalation.
   A later COMPLETED or SUPERSEDED descendant retires earlier FAILED ancestors
   from causal selection (THR-183).

3. **Manager ownership on exhaustion.** A retried slice's second failure keeps
   its durable causal lineage and wakes the owning manager. It is not a runtime
   escalation or upward cascade. Any later manager-proposed escalation follows
   the configured THR-181 hook; inactive/static policy is not evaluator
   CONTINUE, and committed escalations remain human-resolved.

   Authority-v2 refusal follows the same structural split (THR-277): a
   non-root writes `authority_v2_refusal_task_failed`, terminalizes as FAILED,
   and wakes its parent through the fenced ordinary/recovery cleanup tail;
   roots retain the existing founder escalation. Startup classification of an
   `admitted` non-escalate causal result is read-only and does not fence the
   task from ordinary recovery in that boot.

4. **Chain-leg failure.** A failed chain leg clears the chain and returns a
   decision owner to bounded wake. Passive pipeline carriers instead fail
   closed and settle through their outer fan-out barrier with causal-leaf
   context intact.

5. **Happy path unchanged.** All subtasks COMPLETED → parent enqueued for
   next decision step. REVISE-verdict auto-advance in chains is unchanged.

6. **Reviewer/QA verdict discipline.** A review/QA leg completes with an
   APPROVE/REVISE/PASS/FAIL verdict and never self-blocks. A `status=blocked`
   with empty `waiting_on_job_ids` is a malformed report; the leg is treated
   as FAILED and wakes the parent for a decision step.

Implementation: `runtime/orchestrator/run_step.py` —
`_enqueue_parent_if_waiting`, `_advance_chain_for_completed_child`,
and the retry-link validation seam. See also
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

`scripts/daemon.sh start` removes a stale port file, launches the daemon, and
waits up to `HAPPYRANCH_DAEMON_START_TIMEOUT` seconds (default `30`, positive
integers only) for `GET /api/v1/health` to answer on the configured bind host.
Wildcard bind addresses are probed through their loopback equivalent. If the
background process exits or readiness times out, startup exits 1 and prints
the last 20 lines of `daemon.log`; if `curl` is unavailable, it announces a
fallback to the fresh `daemon.port` file.

The full founder-facing CLI is documented in `skills/happyranch/SKILL.md`.

### Task-scoped audit index installation (THR-278)

`Database` uses its existing connection and initialization path to install one
nonunique, nonpartial `idx_audit_log_task_id ON audit_log(task_id)` after the
audit table exists. It applies to fresh and existing databases and is idempotent
on reopen. It preserves historical rows, raw scopes, payload bytes, IDs and
writer transaction boundaries; it adds no `task_results` index.

For a separately authorized operator, install the reviewed source using the
deployment procedure for that runtime, with its locked dependencies and
compatible binaries. Before the ordinary restart, drain active consumers and
take the normal consistent database backup. Use `scripts/daemon.sh start` and
`scripts/daemon.sh status` under that operational authorization; the existing
startup initializer installs the index. No standalone live SQL repair is needed.
Verify the installed source revision, normal health/readiness and each org's
ready/fenced receipt separately. With a read-only SQLite inspection, verify the
exact index SQL, `PRAGMA index_list('audit_log')` nonunique/nonpartial flags and
`PRAGMA index_info('idx_audit_log_task_id')` single `task_id` key; the unchanged
task-scoped query should report `SEARCH audit_log USING INDEX
idx_audit_log_task_id` without a temporary ORDER BY tree. Read back retained
audit IDs/raw scopes and actual decoded results. Measure live startup after
installation before claiming readiness within the default 30 seconds.

Rollback uses a reviewed compatible release that understands the complete
indexed schema and preserves the index/history. A preceding reader whose v1
reference lacks the index can refuse full-schema comparison; do not claim it
is downgrade compatible, drop the index live, or rewrite failed attempt residue.
Merged source alone proves neither installed schema nor live settings repair.

## Running Tests

For where new test files belong, see the forward-only
[test-placement rule](project-layout.md#test-placement).

The founder suspended Python unit-suite execution in THR-291 seq5
(TASK-10169). While this pause applies, do not launch Python unit tests,
including focused tests or duration measurements. The `python-unit` GitHub
job is skipped; `scripts/local_ci.sh python` reports **SUSPENDED**, and `all`
reports the same suspension before continuing Web CI. This also pauses the
unit invocation in the hosted manual `local-ci-all` lane. A successful wrapper
exit or an `all` receipt establishes only the remaining checks, never a unit
PASS. Preserve test sources, selections and coverage definitions. Web,
canonical validation and integration jobs retain their own existing contracts;
no hook bypass is authorized. Existing historical workflow reruns and old
checkouts do not acquire this pause automatically and must not be used to
launch the unit suite. Restore execution only after founder release of the
stop instruction, by reverting the TASK-10169 pause commit through normal
review and merge. The ordinary commands below describe the restored behavior.

The manual `local-ci-all` workflow step invokes the fixed
`uv run python scripts/nightly_local_ci_all.py` entry from the checkout root.
Its G follow-on retains the fixed unit-suspension guard and zero-child receipt;
source provenance authenticates both this script and the workflow YAML.
Source-copy keepers read the script from their own archived checkout. See
[Local CI](../local-ci.md) for the retained dormant plan and source controls.

```bash
uv run pytest tests/ -v -n 4              # unit tests only (default; -n 4 = pytest-xdist parallel)
uv run python tests/helpers/integration_parent.py -- pytest tests/ -v -m integration  # disposable only
uv run python tests/helpers/integration_parent.py -- pytest tests/ -v -m ""  # disposable only
```

Direct pytest uses `tmp_path_retention_policy = "failed"`: passing `tmp_path`
and `tmpdir` fixture directories are removed best effort; ordinary failed-call
diagnostics are retained. This does not cover arbitrary tempfile writes or
guarantee retention after setup/teardown errors or interrupts. Use the frozen
uv environment (currently pytest 9.0.3); the option requires pytest 7.3+, while
the declared `pytest>=7.0` range also admits unsupported 7.0–7.2. See
[pytest scratch scope and limits](../local-ci.md#per-run-pytest-scratch-lifecycle)
for factory directories, explicit basetemp, version compatibility and cleanup
limits. Full unit selections containing real daemon/socket tests also belong
in the documented disposable CI venue.

Integration tests run real production orchestration with deterministic external
CLI stubs in disposable GitHub runners or a separately authorized Mac Linux guest.
The test parent is sanitized before pytest/runtime imports, with temporary homes,
configuration, registries and runtime data plus exact source/callback/stub identity.
Direct integration collection without that parent refuses. Never run integration
on the live Linux daemon host, including through jobs. See `docs/local-ci.md`.
The disposable default roster exists before startup/registration so the real
lifecycle initializes eligible manager selectors. Example-based two-org creation
likewise supplies the declared roster, including the default `code_reviewer`,
before POST so canonical reviewer discovery is coherent; the Codex bootstrap case uses
the supported pending-manager approval path. Explicit task plans write bound JSON
payloads and invoke the tested-source CLI on one line with an absolute `--from-file`.
Registered shell stubs restore their own temporary bin directory before identity
admission, so uv or daemon PATH normalization cannot shadow that callback.
`_nested_daemon_env` still copies the sanitized test parent and removes exactly
its two outer containment markers; it does not sanitize a production environment.

`tests/integration/fake_claude.sh` routes task invocations through `$FAKE_CLAUDE_PLAN` and thread invocations through `$FAKE_CLAUDE_THREAD_PLAN`. Tests that exercise both flows must set both fixtures and write explicit
`DeterministicPlan` bytes. Missing, changed or unavailable plans refuse before
execution; an intentional no-op is an explicit plan. The original two-org fixture
opts into a bounded, source-authenticated pre-session exception observer without
changing launch, return or exception behavior. Historical two-org cause remains
UNKNOWN; the offline missing-agent control is not a historical diagnosis.

### S2 activation attachment and recovery

Compatible readers dispatch schema1/@1 and document-review schema2/@2 by exact
immutable definition plus compiler/validator/source pins across every draft and
version, including noncurrent rows. Publication version numbers do not denote
format. Earlier readers refuse any @2 template data; retained @1 history is never
rewritten to upgrade it. No old-reader downgrade compatibility is promised for
mixed/new data. These formats use existing immutable JSON/BLOB storage meanings
and leave F/E DDL/layout/reference, foundation markers, fresh-org initialization
and explicit-only existing-org migration unchanged. Startup/reopen/enable installs
no extension or format conversion. Recovery continues through its shipping owner
with pinned template-driven role checks; possible launch remains uncertain.


Actual OrgState attachment installs the consumed activation/draft services before
workers; the generic Database constructor and runtime-audit remain workflow-free.
The S1 explicit migration/new-org contract remains unchanged: existing F startup,
reopen and enable never install E. Empty orgs stay fenced until real coherent
roster/team/profile publication makes them ready. Initial activation requires
ready E/G and the actual Founder cutover chain. A queued committed intent survives
lost enqueue notification; startup and periodic sweeps rediscover its existing
eligible task. Live author-capacity refusal, including a claim requeued before
launch, waits for a later sweep; deduplicated queue notification runs after every
profile/org/publication/SQLite lease releases. Missing/malformed
workflow evidence fences legacy effects. Possible host launch cannot be recovered
from PID absence, TTL expiration or session registration: it remains uncertain
under workflow_recovery and blocks drain until genuine containment evidence exists.
Disable fences new admission/prelaunch and preserves ownership of already admitted
work. No startup deployment, live migration, enable, assignment or restart follows
from publishing S2 source.
