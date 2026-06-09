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
| `HAPPYRANCH_ORG_SLUG` | unset | Default org slug for per-org CLI commands |

Slug resolution for per-org commands: explicit `--org <slug>` > `HAPPYRANCH_ORG_SLUG` > auto-infer only when exactly one org exists > error. Container-level commands such as `happyranch init`, `happyranch use`, and `happyranch orgs ...` take no `--org`.

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
