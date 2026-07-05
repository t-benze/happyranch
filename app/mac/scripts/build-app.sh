#!/usr/bin/env bash
set -euo pipefail

# build-app.sh — assemble a self-contained HappyRanchApp.app bundle.
#
# Stages:
#   1. Build the frozen daemon (PyInstaller) via packaging/build_daemon.sh
#   2. Build the web frontend (web/dist)
#   3. Build the SwiftPM executable
#   4. Assemble the .app bundle with daemon + web/dist in Resources
#   5. Place the final .app at ~/Desktop/HappyRanchApp.app
#
# Prerequisites: Xcode 16+ (macOS 15+) with Swift 6 toolchain,
#                 Python 3.12–3.14, uv, Node.js + npm.
#
# Run from:      app/mac/  (the directory containing Package.swift).
# Output:        ~/Desktop/HappyRanchApp.app  (unsigned local build)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PROJECT_DIR/../.." && pwd)"
BUNDLE_NAME="HappyRanchApp"
DESKTOP_APP="$HOME/Desktop/${BUNDLE_NAME}.app"

echo "=== HappyRanch self-contained .app build ==="
echo ""

# ---- Stage 1: Build frozen daemon ----
echo "[1/5] Building frozen daemon (PyInstaller)..."
cd "$REPO_ROOT"
bash packaging/build_daemon.sh
echo "  Frozen daemon built at: $REPO_ROOT/dist/happyranch-daemon/"
echo ""

# ---- Stage 2: Build web frontend ----
echo "[2/5] Building web frontend (Vite)..."
cd "$REPO_ROOT/web"
npm ci 2>&1 | tail -5
npm run build 2>&1 | tail -3
echo "  Web dist built at: $REPO_ROOT/web/dist/"
echo ""

# ---- Stage 3: Build Swift app ----
echo "[3/5] Building Swift app..."
cd "$PROJECT_DIR"
swift build -c release
echo ""

# ---- Stage 4: Assemble .app bundle ----
echo "[4/5] Assembling .app bundle..."
cd "$PROJECT_DIR"

# Remove any prior local bundle (idempotent)
rm -rf "${BUNDLE_NAME}.app"
mkdir -p "${BUNDLE_NAME}.app/Contents/MacOS"
mkdir -p "${BUNDLE_NAME}.app/Contents/Resources/daemon"
mkdir -p "${BUNDLE_NAME}.app/Contents/Resources/web"

# Copy Swift binary
BINARY="$PROJECT_DIR/.build/release/${BUNDLE_NAME}"
if [ -x "$BINARY" ]; then
    cp "$BINARY" "${BUNDLE_NAME}.app/Contents/MacOS/${BUNDLE_NAME}"
else
    BINARY="$PROJECT_DIR/.build/debug/${BUNDLE_NAME}"
    cp "$BINARY" "${BUNDLE_NAME}.app/Contents/MacOS/${BUNDLE_NAME}"
fi
echo "  Binary copied."

# Copy frozen daemon bundle into Resources
cp -R "$REPO_ROOT/dist/happyranch-daemon/" "${BUNDLE_NAME}.app/Contents/Resources/daemon/"
chmod +x "${BUNDLE_NAME}.app/Contents/Resources/daemon/happyranch-daemon" 2>/dev/null || true
echo "  Daemon bundle copied."

# Copy web/dist into Resources
cp -R "$REPO_ROOT/web/dist/" "${BUNDLE_NAME}.app/Contents/Resources/web/dist/"
echo "  Web dist copied."

# Generate Info.plist from template with LSEnvironment for bundled mode
cp "$PROJECT_DIR/scripts/Info.plist" "${BUNDLE_NAME}.app/Contents/Info.plist"

# Inject GitCommitSHA (best-effort)
GIT_SHA=$(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo "unknown")
/usr/libexec/PlistBuddy \
    -c "Set :GitCommitSHA $GIT_SHA" \
    "${BUNDLE_NAME}.app/Contents/Info.plist" 2>/dev/null || true

# Inject LSEnvironment to set PACKAGING_MODE=bundled when launched as .app
/usr/libexec/PlistBuddy \
    -c "Add :LSEnvironment dict" \
    -c "Add :LSEnvironment:PACKAGING_MODE string bundled" \
    "${BUNDLE_NAME}.app/Contents/Info.plist" 2>/dev/null || true

echo "  Info.plist configured (PACKAGING_MODE=bundled)."
echo ""

# ---- Stage 5: Place at Desktop ----
echo "[5/5] Placing at ~/Desktop..."
rm -rf "$DESKTOP_APP"
cp -R "${BUNDLE_NAME}.app" "$DESKTOP_APP"

APP_SIZE=$(du -sh "$DESKTOP_APP" | cut -f1)
echo "  Desktop .app size: $APP_SIZE"
echo ""

echo "=== Build complete ==="
echo "Path: $DESKTOP_APP"
echo ""
echo "Launch options:"
echo "  open ~/Desktop/${BUNDLE_NAME}.app"
echo "  (or double-click in Finder)"
echo ""
echo "First launch (unsigned — Gatekeeper bypass):"
echo "  Right-click ${BUNDLE_NAME}.app → Open, then confirm"
echo "  OR: xattr -cr ~/Desktop/${BUNDLE_NAME}.app"
