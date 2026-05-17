#!/usr/bin/env bash
#
# CI gate for the web design system. Runs typecheck + tests + registry
# regeneration + freshness check. Mirrors the rules in
# `web/DESIGN_SYSTEM.md` §10 / §13.
#
# Exit codes:
#   0 — clean
#   1 — typecheck, test, or build failure
#   2 — registry.json out of date (commit the regenerated copy)
#
# Run locally before pushing: `bash scripts/verify-design-system.sh`
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Typecheck"
npm run typecheck

echo "==> Tests"
npm test -- --run

echo "==> Build registry"
npm run build:registry

echo "==> Registry freshness"
if ! git diff --quiet src/design-system/registry.json; then
  echo "FAIL: src/design-system/registry.json is stale."
  echo "      Run 'npm run build:registry' locally and commit the result."
  exit 2
fi
echo "  ok"

echo "All design-system checks passed."
