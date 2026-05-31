#!/usr/bin/env bash
# Build the HappyRanch web UI bundle into web/dist/.
# The daemon's StaticFiles mount serves web/dist/ at /.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEB_DIR="${REPO_ROOT}/web"

if [ ! -d "${WEB_DIR}" ]; then
  echo "error: ${WEB_DIR} not found" >&2
  exit 1
fi

cd "${WEB_DIR}"

if [ ! -d node_modules ] || [ ! -f node_modules/.package-lock.json ]; then
  echo "[build_web] installing dependencies…"
  npm ci
fi

echo "[build_web] building…"
npm run build

echo "[build_web] done — bundle at ${WEB_DIR}/dist/"
