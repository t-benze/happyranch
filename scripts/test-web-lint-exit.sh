#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
web_root="${1:-$repo_root/web}"

# Prove the Tailwind v3 plugin reuses one compatibility model across files,
# then retain a separate process-level assertion that supported full lint exits.
node "$repo_root/scripts/test-web-lint-tailwind-cache.mjs" "$web_root"
cd "$web_root"
timeout --signal=TERM --kill-after=5 30 npm run lint
