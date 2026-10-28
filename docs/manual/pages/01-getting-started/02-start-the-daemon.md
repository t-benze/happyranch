# 02 - Start the Daemon

**Purpose:** Start the local HappyRanch daemon that the CLI and web UI talk to.

## The Important Bit

The daemon is started with `scripts/daemon.sh`, not a `happyranch start`
command. There is no `happyranch start`.

From inside the repo:

```bash
scripts/daemon.sh start
```

By default the daemon binds to `127.0.0.1:8765`. It is local to your machine.

The start command waits for `GET /api/v1/health` to answer before reporting
success. The default startup timeout is 120 seconds; set a different positive
integer when needed:

```bash
HAPPYRANCH_DAEMON_START_TIMEOUT=60 scripts/daemon.sh start
```

If the process exits before it becomes healthy, startup fails immediately. A
process-exit or timeout failure includes the last 20 lines of `daemon.log`.
Curl is required before launching. A PID or port file cannot prove serving.
Success may mean serving while recovery continues; it never implies recovery
completion by itself. A timeout can leave the original process alive. Preserve
that owner and inspect status/logs; do not start another daemon automatically.

## Check Status

```bash
scripts/daemon.sh status
```

An alive daemon serving readable threads during recovery prints:

```text
running (pid 12345, port 8765)
startup serving=yes recovery=recovering launches=held
```

Only `startup serving=yes recovery=ready launches=admitted` reports recovery
readiness. Original permissions and admission controls still apply. Other
recovery states are `recovery_failed`, `stopping` and `unknown`; serving may
also be `no` or `unknown`. Missing/malformed fields or a failed probe never mean
ready. Status exit0 means only that the process is alive. `happyranch web` can
open threads while recovery is held or failed. Preserve the original process
and operation on failure/unknown; no automatic restart/import/replay follows.

The daemon records its process and logs under `~/.happyranch/`:

| File | What it is for |
|---|---|
| `~/.happyranch/daemon.pid` | Running process ID |
| `~/.happyranch/daemon.port` | Bound port, default `8765` |
| `~/.happyranch/daemon.log` | Daemon stdout/stderr |

If startup fails, check `~/.happyranch/daemon.log`.

## What the Daemon Does

The daemon is the local control plane. It:

- serves the web UI at `http://127.0.0.1:8765/`,
- receives CLI requests,
- manages orgs and their SQLite databases,
- queues and launches agent tasks,
- records threads, artifacts, jobs, and usage,
- and streams live updates to the UI.

One daemon serves the active runtime container on your machine.

## Stop the Daemon

```bash
scripts/daemon.sh stop --force
```

The `--force` requirement protects the default founder daemon from accidental
shutdowns. Use it only when you intend to stop the local daemon.

## Optional Configuration

Common environment variables:

| Variable | Default | Controls |
|---|---|---|
| `HAPPYRANCH_DAEMON_PORT` | `8765` | Local port |
| `HAPPYRANCH_DAEMON_START_TIMEOUT` | `120` | Seconds to wait for startup health |
| `HAPPYRANCH_QUEUE_WORKERS` | `3` | Concurrent agent sessions |
| `HAPPYRANCH_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout |
| `HAPPYRANCH_MAX_ORCHESTRATION_STEPS` | `50` | Legacy accepted setting; no longer limits decisions |

Restart the daemon after changing these.

## Next

Go to [03 - Create a Runtime + Your First Org](03-create-runtime-and-org.md).
