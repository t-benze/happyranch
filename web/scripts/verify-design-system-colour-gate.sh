#!/usr/bin/env bash
# Shared shipping gate: preserve the real production-tree scan across local
# and GitHub Web CI while the scanner regression unit suite is suspended.
set -euo pipefail

cd "$(dirname "$0")/.."

# Retained unit command; restore only after founder release.
# bash scripts/test-design-system-hex-scanner.sh
echo "SKIPPED: scanner regression units SUSPENDED under THR-291 / THR-228 seq355; not a unit-test PASS."
bash scripts/verify-design-system.sh --scan-hex
