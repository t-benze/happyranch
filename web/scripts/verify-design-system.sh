#!/usr/bin/env bash
#
# CI gate for the web design system. Runs typecheck + lint + tests +
# deterministic Storybook static build + hex-code grep.
#
# Exit codes:
#   0 — clean
#   1 — typecheck, lint, test, or build failure
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

echo "==> Build Storybook"
npm run build-storybook

echo "==> New hex colors outside tokens.css"
HEX_HITS=$(
  git diff --unified=0 origin/main -- src/ \
    | grep '^+' \
    | grep -E '(\[|:[[:space:]]*)#[0-9a-fA-F]{3,8}([^0-9a-fA-F]|$)' \
    | grep -v 'src/design-system/tokens/tokens.css' \
    || true
)
if [ -n "$HEX_HITS" ]; then
  echo "FAIL: this branch adds hex colors outside tokens.css:"
  echo "$HEX_HITS"
  exit 3
fi
echo "  ok"

echo "All design-system checks passed."
