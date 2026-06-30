#!/usr/bin/env bash
set -euo pipefail

# build-app.sh — assemble a minimal, double-clickable HappyRanchApp.app bundle.
#
# Prerequisites: Xcode 16+ (macOS 15+) with Swift 6 toolchain.
# Run from:      app/mac/  (the directory containing Package.swift).
# Output:        HappyRanchApp.app in the current directory.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUNDLE_NAME="HappyRanchApp"
APP_DIR="$PROJECT_DIR/${BUNDLE_NAME}.app"

echo "=== Building ${BUNDLE_NAME} ==="

# 1. Build the SwiftPM executable
cd "$PROJECT_DIR"
swift build -c release

# 2. Assemble the .app bundle structure (idempotent — remove any prior bundle)
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

# 3. Copy the release binary (or fall back to debug if release tree is absent)
BINARY="$PROJECT_DIR/.build/release/${BUNDLE_NAME}"
if [ -x "$BINARY" ]; then
    cp "$BINARY" "$APP_DIR/Contents/MacOS/${BUNDLE_NAME}"
else
    BINARY="$PROJECT_DIR/.build/debug/${BUNDLE_NAME}"
    cp "$BINARY" "$APP_DIR/Contents/MacOS/${BUNDLE_NAME}"
fi

# 4. Write Info.plist from the committed template
if [ -f "$PROJECT_DIR/scripts/Info.plist" ]; then
    cp "$PROJECT_DIR/scripts/Info.plist" "$APP_DIR/Contents/Info.plist"
    echo "  Info.plist → $APP_DIR/Contents/Info.plist"
else
    echo "ERROR: scripts/Info.plist not found — cannot assemble bundle" >&2
    exit 1
fi

echo "=== Bundle assembled ==="
echo "Path: $APP_DIR"
echo ""
echo "Launch options:"
echo "  open ${BUNDLE_NAME}.app"
echo "  (or double-click ${BUNDLE_NAME}.app in Finder)"
echo ""
echo "First launch (unsigned — Gatekeeper bypass):"
echo "  Right-click ${BUNDLE_NAME}.app → Open, then confirm"
echo "  OR: xattr -cr ${BUNDLE_NAME}.app"
