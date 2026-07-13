#!/usr/bin/env bash
set -euo pipefail

# build-app.sh — assemble a self-contained HappyRanch.app bundle.
#
# Stages:
#   1. Build the frozen daemon (PyInstaller) via packaging/build_daemon.sh
#   2. Build the web frontend (web/dist)
#   3. Build the SwiftPM executable
#   4. Assemble the .app bundle with daemon + web/dist in Resources
#   5. Place the final .app at ~/Desktop/HappyRanch.app
#
# Prerequisites: Xcode 16+ (macOS 15+) with Swift 6 toolchain,
#                 Python 3.12–3.14, uv, Node.js + npm.
#
# Run from:      app/mac/  (the directory containing Package.swift).
# Output:        ~/Desktop/HappyRanch.app  (unsigned local build)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PROJECT_DIR/../.." && pwd)"
BUNDLE_NAME="HappyRanch"
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

# Copy Swift binary (source uses SwiftPM product name HappyRanchApp,
# destination uses BUNDLE_NAME for the user-visible .app naming)
BINARY="$PROJECT_DIR/.build/release/HappyRanchApp"
if [ -x "$BINARY" ]; then
    cp "$BINARY" "${BUNDLE_NAME}.app/Contents/MacOS/${BUNDLE_NAME}"
else
    BINARY="$PROJECT_DIR/.build/debug/HappyRanchApp"
    cp "$BINARY" "${BUNDLE_NAME}.app/Contents/MacOS/${BUNDLE_NAME}"
fi
echo "  Binary copied."

# Copy frozen daemon bundle into Resources
cp -R "$REPO_ROOT/dist/happyranch-daemon/" "${BUNDLE_NAME}.app/Contents/Resources/daemon/"
chmod +x "${BUNDLE_NAME}.app/Contents/Resources/daemon/happyranch-daemon" 2>/dev/null || true
if ! test -x "${BUNDLE_NAME}.app/Contents/Resources/daemon/happyranch-daemon"; then
    echo "ERROR: Bundled daemon binary missing or not executable after staging — check dist/happyranch-daemon was built via packaging/build_daemon.sh" >&2
    exit 1
fi
chmod +x "${BUNDLE_NAME}.app/Contents/Resources/daemon/happyranch" 2>/dev/null || true
if ! test -x "${BUNDLE_NAME}.app/Contents/Resources/daemon/happyranch"; then
    echo "ERROR: Bundled CLI binary missing or not executable after staging — check dist/happyranch-daemon was built via packaging/build_daemon.sh" >&2
    exit 1
fi
echo "  Daemon bundle copied."

# Copy web/dist into Resources
cp -R "$REPO_ROOT/web/dist/" "${BUNDLE_NAME}.app/Contents/Resources/web/dist/"
echo "  Web dist copied."

# ----- Assemble AppIcon.icns from vendored PNGs -----
ICONSET_SRC="$PROJECT_DIR/Resources/AppIcon.appiconset"
if [ -d "$ICONSET_SRC" ]; then
    TEMP_DIR="$(mktemp -d /tmp/happyranch-icon.XXXXXX)"
    trap 'rm -rf "$TEMP_DIR"' EXIT
    TEMP_ICONSET="$TEMP_DIR/AppIcon.iconset"
    mkdir -p "$TEMP_ICONSET"
    # Map vendored filenames -> standard .iconset filenames per Contents.json
    #   size       scale   source
    #   16x16      1x      icon_16.png   -> icon_16x16.png
    #   16x16      2x      icon_32.png   -> icon_16x16@2x.png
    #   32x32      1x      icon_32.png   -> icon_32x32.png
    #   32x32      2x      icon_64.png   -> icon_32x32@2x.png
    #   128x128     1x      icon_128.png  -> icon_128x128.png
    #   128x128     2x      icon_256.png  -> icon_128x128@2x.png
    #   256x256     1x      icon_256.png  -> icon_256x256.png
    #   256x256     2x      icon_512.png  -> icon_256x256@2x.png
    #   512x512     1x      icon_512.png  -> icon_512x512.png
    #   512x512     2x      icon_1024.png -> icon_512x512@2x.png
    cp "$ICONSET_SRC/icon_16.png"   "$TEMP_ICONSET/icon_16x16.png"
    cp "$ICONSET_SRC/icon_32.png"   "$TEMP_ICONSET/icon_16x16@2x.png"
    cp "$ICONSET_SRC/icon_32.png"   "$TEMP_ICONSET/icon_32x32.png"
    cp "$ICONSET_SRC/icon_64.png"   "$TEMP_ICONSET/icon_32x32@2x.png"
    cp "$ICONSET_SRC/icon_128.png"  "$TEMP_ICONSET/icon_128x128.png"
    cp "$ICONSET_SRC/icon_256.png"  "$TEMP_ICONSET/icon_128x128@2x.png"
    cp "$ICONSET_SRC/icon_256.png"  "$TEMP_ICONSET/icon_256x256.png"
    cp "$ICONSET_SRC/icon_512.png"  "$TEMP_ICONSET/icon_256x256@2x.png"
    cp "$ICONSET_SRC/icon_512.png"  "$TEMP_ICONSET/icon_512x512.png"
    cp "$ICONSET_SRC/icon_1024.png" "$TEMP_ICONSET/icon_512x512@2x.png"
    iconutil -c icns -o "$TEMP_ICONSET/AppIcon.icns" "$TEMP_ICONSET"
    cp "$TEMP_ICONSET/AppIcon.icns" "${BUNDLE_NAME}.app/Contents/Resources/AppIcon.icns"
    echo "  AppIcon.icns assembled and copied into Resources."
else
    echo "  WARNING: $ICONSET_SRC not found — skipping icon assembly."
fi

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
