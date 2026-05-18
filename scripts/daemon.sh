#!/usr/bin/env bash
set -euo pipefail

GRASSLAND_HOME="${GRASSLAND_DAEMON_HOME:-$HOME/.grassland}"
PID_FILE="$GRASSLAND_HOME/daemon.pid"
PORT_FILE="$GRASSLAND_HOME/daemon.port"
LOG_FILE="$GRASSLAND_HOME/daemon.log"

cmd_start() {
    mkdir -p "$GRASSLAND_HOME"
    if [[ -f "$PID_FILE" ]]; then
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "daemon already running (pid $pid)"
            exit 1
        fi
        rm -f "$PID_FILE"
    fi
    nohup uv run python -m src.daemon >> "$LOG_FILE" 2>&1 &
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
    stop)   cmd_stop   ;;
    status) cmd_status ;;
    *)      echo "Usage: $0 {start|stop|status}"; exit 2 ;;
esac
