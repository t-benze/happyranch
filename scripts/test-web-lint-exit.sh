#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root/web"

# Regression for the Tailwind v3 plugin rebuilding its compatibility model
# once per source file under Tailwind v4. A healthy full lint finishes well
# inside this bound; the broken configuration times out.
timeout --signal=TERM --kill-after=5 30 npm run lint
