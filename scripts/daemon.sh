#!/usr/bin/env bash
set -euo pipefail

HAPPYRANCH_HOME="${HAPPYRANCH_DAEMON_HOME:-$HOME/.happyranch}"
PID_FILE="$HAPPYRANCH_HOME/daemon.pid"
PORT_FILE="$HAPPYRANCH_HOME/daemon.port"
LOG_FILE="$HAPPYRANCH_HOME/daemon.log"

cmd_start() {
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

case "${1:-}" in
    start)  cmd_start  ;;
    stop)   cmd_stop "${2:-}"   ;;
    status) cmd_status ;;
    *)      echo "Usage: $0 {start|stop [--force]|status}"; exit 2 ;;
esac
