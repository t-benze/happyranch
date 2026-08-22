# 01 - Requirements & Install

**Purpose:** Get the HappyRanch repo installed and confirm your machine can run
the local daemon, CLI, and web UI.

## What You Need

| Requirement | Minimum / status | Why it matters |
|---|---|---|
| Operating system | macOS or Linux | Runs canonical skill delivery and executor sessions; Windows is unsupported |
| Python | 3.12–3.14 | Runs the HappyRanch daemon and CLI |
| uv | Recent version | Installs Python dependencies into the local environment |
| Git | Recent version | Clones the repo and lets agents clone managed repos |
| Agentic CLI | At least one | Runs the actual agent sessions |
| Node.js | 24 or newer | Needed for the web UI path |

HappyRanch supports these built-in executor profiles:
- Claude Code (`claude`)
- Codex CLI (`codex`)
- opencode (`opencode`)
- Pi (`pi`)

You can install HappyRanch before choosing an executor, but the first real task
will not run until at least one supported agentic CLI is available.

HappyRanch uses the same same-owner canonical skill-store model on macOS and
Linux. Integrity checks detect mismatches and fail closed before launch; they
do not prevent another process running as the daemon user from changing files
or racing validation. Windows and unknown platforms are unsupported and have no
legacy copy fallback.

## Install

From a terminal:

```bash
git clone https://github.com/t-benze/happyranch.git
cd happyranch
uv sync
```

`uv sync` installs the Python dependencies into the repo-local environment. You
do not need a global `pip` install.

## Verify the Install

Run:

```bash
uv run pytest tests/ -v -n 4
```

If you want a faster first check, skip integration tests:

```bash
uv run pytest tests/ -v -m "not integration"
```

## What You Have Now

You have the source tree and the `happyranch` CLI available from inside the
repo. You do not yet have:

- a running daemon,
- a runtime container,
- an org,
- initialized agent workspaces,
- or a completed task.

Those come next.

## Next

Go to [02 - Start the Daemon](02-start-the-daemon.md).
