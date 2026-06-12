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
| `executor_rate_limit_backoff_seconds` | `[5, 15, 45]` | On a detected rate limit the launch releases its slot, sleeps `backoff[attempt]`, re-acquires, and retries. After the schedule is exhausted the result falls through to `run_step._classify_failure_kind`. `[]` disables retries. |

Rate-limit detection is normalized: `_run_command` sets `ExecutorResult.rate_limited` from `is_rate_limit_signature(...)` and the classifier prefers that field over its legacy string heuristic. Two additive audit actions surface the activity through the existing `insert_audit_log` (no schema change): `executor_slot_wait` (`{provider, wait_seconds, ceiling}`) when a launch waited for a slot, and `executor_rate_limit_backoff` (`{provider, attempt, backoff_seconds}`) per 429 retry.

The list/dict-shaped keys (`executor_ceiling_overrides`, `executor_rate_limit_backoff_seconds`) are set via `config.yaml`; the scalar keys also accept `HAPPYRANCH_`-prefixed env vars.

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

## Org Config: Dreaming

Per-org `dreaming:` config controls the private nightly reflection scheduler: enablement, local schedule time/timezone, catch-up behavior, and agent include/exclude selection.

## Session Timeout Resolution

`Orchestrator._resolve_session_timeout(agent_name, task_id=...)` walks three layers:

1. Task override: `tasks.session_timeout_seconds`, set via `happyranch revisit ... --session-timeout-seconds N` and inherited by children.
2. Org override: `session_timeout_seconds:` in `<runtime>/orgs/<slug>/org/config.yaml`.
3. Code default: `Settings.session_timeout_seconds`.

Positive integers only. `<= 0` or non-int raises at parse time. The `agent_name` argument is unused but kept for call-site symmetry. Legacy `session_timeout_seconds` in agent frontmatter is silently ignored.

## Running The Daemon

The CLI is an HTTP client. Start the daemon once, then run CLI commands.

```bash
scripts/daemon.sh start
scripts/daemon.sh status
scripts/daemon.sh stop
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
