#!/usr/bin/env bash
set -euo pipefail

HAPPYRANCH_HOME="${HAPPYRANCH_DAEMON_HOME:-$HOME/.happyranch}"
PID_FILE="$HAPPYRANCH_HOME/daemon.pid"
PORT_FILE="$HAPPYRANCH_HOME/daemon.port"
LOG_FILE="$HAPPYRANCH_HOME/daemon.log"

# Checkout root — the source deployment whose matching CLI/runtime environment
# the daemon launch requires (pyproject.toml requires-python >=3.12,<3.15).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── GH-709 Slice D: synchronous uv launch preflight ──────────────────────────
# `start` backgrounds bare `uv` (nohup uv run python -m runtime.daemon …); a
# noninteractive or remote shell may not source the user profile and can
# silently drop uv from PATH, causing a silent five-second start timeout
# (GH-709 finding 5). This preflight resolves/checks uv SYNCHRONOUSLY before
# any launch side effect and fails with an actionable diagnostic naming the
# observed PATH / resolved uv / version. It requires the source deployment's
# matching CLI/runtime environment; it never downloads uv and never selects an
# alternate CLI.
_preflight_uv() {
    local uv_bin uv_version python_full python_version python_major python_minor

    if ! uv_bin="$(command -v uv 2>/dev/null)"; then
        echo "ERROR: uv is required to launch the daemon but was not found on PATH." >&2
        echo "  Observed PATH: ${PATH:-<unset>}" >&2
        echo "  'scripts/daemon.sh start' backgrounds bare 'uv'; a noninteractive or" >&2
        echo "  remote shell may not source your profile and can silently drop uv from" >&2
        echo "  PATH (GH-709 finding 5: a silent 5s start timeout)." >&2
        echo "  Remediation (never point the daemon at an alternate CLI):" >&2
        echo "    - re-run from a shell where 'command -v uv' succeeds (e.g. a login shell), or" >&2
        echo "    - export PATH=\"<uv-install-dir>:\$PATH\" with the real uv binary's directory, or" >&2
        echo "    - if uv is not installed, install it at its documented path:" >&2
        echo "      https://docs.astral.sh/uv/getting-started/installation/" >&2
        echo "  Then re-run: scripts/daemon.sh start" >&2
        return 1
    fi

    if [[ ! -f "$uv_bin" || ! -x "$uv_bin" ]]; then
        echo "ERROR: uv resolves to a path that is not an executable regular file." >&2
        echo "  Resolved uv: $uv_bin" >&2
        echo "  Observed PATH: ${PATH:-<unset>}" >&2
        echo "  Remediation: fix or remove that PATH entry so 'command -v uv' yields the" >&2
        echo "  real uv binary, then re-run: scripts/daemon.sh start" >&2
        return 1
    fi

    if ! uv_version="$(cd "$SCRIPT_DIR" && uv --version 2>&1)"; then
        echo "ERROR: uv is present at $uv_bin but could not report a version." >&2
        echo "  uv --version output: ${uv_version:-<none>}" >&2
        echo "  Observed PATH: ${PATH:-<unset>}" >&2
        echo "  Remediation: use a working uv installation (see" >&2
        echo "  https://docs.astral.sh/uv/getting-started/installation/), then re-run:" >&2
        echo "  scripts/daemon.sh start" >&2
        return 1
    fi

    if ! python_full="$(cd "$SCRIPT_DIR" && uv run python --version 2>&1)"; then
        echo "ERROR: the checkout's runtime environment is not usable via uv." >&2
        echo "  Resolved uv: $uv_bin ($uv_version)" >&2
        echo "  'uv run python --version' failed with:" >&2
        echo "    ${python_full:-<none>}" >&2
        echo "  Remediation: from the checkout ($SCRIPT_DIR) run 'uv sync', verify with" >&2
        echo "  'uv run python --version' (requires-python >=3.12,<3.15), then re-run:" >&2
        echo "  scripts/daemon.sh start" >&2
        return 1
    fi

    # "Python 3.14.4" (uv may prefix warnings on earlier lines); require the
    # pyproject.toml requires-python range — keep in lockstep.
    python_version="$(printf '%s\n' "$python_full" \
        | sed -n 's/^Python \([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' | tail -n 1)"
    if [[ -z "$python_version" ]]; then
        echo "ERROR: could not parse the checkout's Python version from uv." >&2
        echo "  Resolved uv: $uv_bin ($uv_version)" >&2
        echo "  Observed 'uv run python --version' output:" >&2
        echo "    $python_full" >&2
        echo "  Observed PATH: ${PATH:-<unset>}" >&2
        echo "  Remediation: use a healthy checkout runtime (run 'uv sync' in" >&2
        echo "  $SCRIPT_DIR, verify 'uv run python --version'), then re-run:" >&2
        echo "  scripts/daemon.sh start" >&2
        return 1
    fi
    python_major="${python_version%%.*}"
    python_minor="${python_version#*.}"
    python_minor="${python_minor%%.*}"
    if [[ "$python_major" != "3" || "$python_minor" -lt 12 || "$python_minor" -ge 15 ]]; then
        echo "ERROR: the checkout's Python runtime is outside requires-python (>=3.12,<3.15)." >&2
        echo "  Resolved uv: $uv_bin ($uv_version)" >&2
        echo "  Observed 'uv run python --version': $python_full" >&2
        echo "  Observed PATH: ${PATH:-<unset>}" >&2
        echo "  Remediation: select/install a matching interpreter for the checkout, e.g." >&2
        echo "    (cd \"$SCRIPT_DIR\" && uv python install 3.14 && uv sync)" >&2
        echo "  then verify 'uv run python --version' and re-run: scripts/daemon.sh start" >&2
        return 1
    fi
}

cmd_start() {
    _preflight_uv || exit 1
    cd "$SCRIPT_DIR"
    mkdir -p "$HAPPYRANCH_HOME"
    if [[ -f "$PID_FILE" ]]; then
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "daemon already running (pid $pid)"
            exit 1
        fi
        rm -f "$PID_FILE"
    fi
    nohup uv run python -m runtime.daemon >> "$LOG_FILE" 2>&1 &
    bg_pid=$!
    # Wait up to 5s for port file to materialize
    for _ in 1 2 3 4 5; do
        if [[ -f "$PORT_FILE" ]]; then
            port=$(cat "$PORT_FILE")
            echo "daemon started (pid $bg_pid, port $port)"
            exit 0
        fi
        sleep 1
    done
    echo "daemon failed to start within 5s — see $LOG_FILE"
    exit 1
}

cmd_stop() {
    local force_flag="${1:-}"
    # Guard: when stopping the DEFAULT home (HAPPYRANCH_DAEMON_HOME unset),
    # require --force to prevent agents from killing the founder's real daemon.
    # Isolated instances (HAPPYRANCH_DAEMON_HOME set) skip this guard entirely.
    if [ -z "${HAPPYRANCH_DAEMON_HOME:-}" ]; then
        if [ "$force_flag" != "--force" ]; then
            echo "Refusing to stop the default daemon at $HAPPYRANCH_HOME without --force."
            echo "This is likely the founder's real daemon."
            echo "Re-run: scripts/daemon.sh stop --force"
            echo "(integration tests set HAPPYRANCH_DAEMON_HOME and are unaffected.)"
            exit 1
        fi
    fi
    if [[ ! -f "$PID_FILE" ]]; then
        echo "daemon not running"
        exit 0
    fi
    pid=$(cat "$PID_FILE")
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "stale pid file (process $pid not alive)"
        rm -f "$PID_FILE" "$PORT_FILE"
        exit 0
    fi
    kill -TERM "$pid"
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$PID_FILE" "$PORT_FILE"
            echo "daemon stopped"
            exit 0
        fi
        sleep 1
    done
    kill -KILL "$pid" || true
    rm -f "$PID_FILE" "$PORT_FILE"
    echo "daemon force-killed"
}

cmd_status() {
    if [[ ! -f "$PID_FILE" ]]; then
        echo "not running"
        exit 1
    fi
    pid=$(cat "$PID_FILE")
    if ! kill -0 "$pid" 2>/dev/null; then
        echo "stale (pid file from dead process)"
        exit 1
    fi
    port=$(cat "$PORT_FILE" 2>/dev/null || echo "?")
    echo "running (pid $pid, port $port)"
}

cmd_maintenance() {
    # OFFLINE/STARTUP-ONLY metrics maintenance one-shot (TASK-5443
    # replacement).  Runs to completion in the FOREGROUND and propagates its
    # exit code (0 = success, nonzero = fail-closed).  It never starts the
    # normal daemon — no port/pid files are written, no server is bound.
    # Run it only while the daemon is stopped.
    # NOTE: no mkdir here — daemon-home initialization happens INSIDE the
    # Python entry's single bounded/redacted failure boundary
    # (runtime/daemon/__main__.py run_maintenance -> paths.ensure_daemon_home),
    # so a hostile/malformed HAPPYRANCH_DAEMON_HOME (e.g. an existing file)
    # returns the fixed exit-1 classification instead of a raw mkdir
    # diagnostic that leaks the configured path.
    uv run python -m runtime.daemon --maintenance
}

case "${1:-}" in
    start)  cmd_start  ;;
    stop)   cmd_stop "${2:-}"   ;;
    status) cmd_status ;;
    maintenance) cmd_maintenance ;;
    *)      echo "Usage: $0 {start|stop [--force]|status|maintenance}"; exit 2 ;;
esac
