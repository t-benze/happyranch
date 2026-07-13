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

## Check Status

```bash
scripts/daemon.sh status
```

A healthy daemon prints something like:

```text
running (pid 12345, port 8765)
```

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
| `HAPPYRANCH_QUEUE_WORKERS` | `3` | Concurrent agent sessions |
| `HAPPYRANCH_SESSION_TIMEOUT_SECONDS` | `1800` | Agent session timeout |
| `HAPPYRANCH_MAX_ORCHESTRATION_STEPS` | `50` | Manager decision limit |

Restart the daemon after changing these.

## Next

Go to [03 - Create a Runtime + Your First Org](03-create-runtime-and-org.md).
