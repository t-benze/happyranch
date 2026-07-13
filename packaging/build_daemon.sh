#!/usr/bin/env bash
# Build the HappyRanch daemon into a standalone frozen executable via PyInstaller.
#
# USAGE:  ./build/build_daemon.sh
#
# REQUIREMENTS: uv, Python 3.12–3.14
#
# OUTPUT:  dist/happyranch-daemon/happyranch-daemon  (console binary)
#          dist/happyranch-daemon/_internal/          (bundled deps + data)
#
# PyInstaller is a BUILD-TIME dependency only; it is not added to the
# daemon's runtime dependency group. See pyproject.toml [dependency-groups].build.
#
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== HappyRanch daemon freeze build ==="
echo ""

# ---- 1. Ensure build deps ----
echo "[1/3] Installing build dependencies (pyinstaller)..."
uv sync --group build --frozen 2>&1 | tail -1

# ---- 2. PyInstaller ----
echo "[2/3] Running PyInstaller..."
uv run pyinstaller packaging/daemon.spec --clean --noconfirm 2>&1

# ---- 3. Verify output ----
echo ""
echo "[3/3] Verifying output..."

# Verify daemon binary
BIN="dist/happyranch-daemon/happyranch-daemon"
if [ -x "$BIN" ] || [ -f "$BIN" ]; then
    echo "  Binary: $BIN"
    if [ -x "$BIN" ]; then
        echo "  Perms:  executable"
    else
        echo "  Perms:  NOT executable — applying chmod +x"
        chmod +x "$BIN"
    fi
else
    echo "ERROR: Daemon binary not found at $BIN"
    exit 1
fi

# Verify CLI binary
CLI_BIN="dist/happyranch-daemon/happyranch"
if [ -x "$CLI_BIN" ] || [ -f "$CLI_BIN" ]; then
    echo "  Binary: $CLI_BIN"
    if [ -x "$CLI_BIN" ]; then
        echo "  Perms:  executable"
    else
        echo "  Perms:  NOT executable — applying chmod +x"
        chmod +x "$CLI_BIN"
    fi
else
    echo "ERROR: CLI binary not found at $CLI_BIN"
    exit 1
fi

SIZE=$(du -sh "dist/happyranch-daemon" | cut -f1)
echo "  Size:   $SIZE"
echo ""
echo "=== Build complete ==="
echo "Run:  dist/happyranch-daemon/happyranch-daemon"
echo "CLI:  dist/happyranch-daemon/happyranch"
