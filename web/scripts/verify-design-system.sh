#!/usr/bin/env bash
#
# CI gate for the web design system. Runs typecheck + lint + tests +
# registry regeneration + freshness check + hex-code grep. Mirrors the
# rules in `web/DESIGN_SYSTEM.md` §10 / §13.
#
# Exit codes:
#   0 — clean
#   1 — typecheck, lint, test, or build failure
#   2 — registry.json out of date (commit the regenerated copy)
#   3 — hex code found outside tokens.css (escapes the token layer)
#
# Run locally before pushing: `bash scripts/verify-design-system.sh`
set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Typecheck"
npm run typecheck

echo "==> ESLint"
npm run lint

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

echo "==> Hex codes outside tokens.css"
HEX_HITS=$(
  grep -RIn --include='*.ts' --include='*.tsx' --include='*.css' \
       -E '#[0-9a-fA-F]{3,8}\b' src/ \
    | grep -v 'src/design-system/tokens/tokens.css' \
    || true
)
if [ -n "$HEX_HITS" ]; then
  echo "FAIL: hex codes found outside tokens.css:"
  echo "$HEX_HITS"
  exit 3
fi
echo "  ok"

echo "All design-system checks passed."
