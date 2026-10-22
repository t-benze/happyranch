#!/usr/bin/env bash
# Build the HappyRanch web UI bundle into web/dist/.
# The daemon's StaticFiles mount serves web/dist/ at /.
set -euo pipefail

usage() {
  echo "Usage: build_web.sh [--production]"
  echo "  No arguments: build web/dist/ with the local UI flag profile (npm run build:local)."
  echo "  --production: use the production UI flag profile (npm run build)."
  echo "  -h, --help: show this help."
}

BUILD_SCRIPT="build:local"
if [ "$#" -gt 1 ]; then
  echo "error: expected no arguments or one of --production, -h, --help" >&2
  usage >&2
  exit 2
fi
if [ "$#" -eq 1 ]; then
  case "$1" in
    --production) BUILD_SCRIPT="build" ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
fi

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
npm run "${BUILD_SCRIPT}"

echo "[build_web] done — bundle at ${WEB_DIR}/dist/"
