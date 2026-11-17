#!/usr/bin/env bash
# Shared shipping gate: keep the deterministic scanner regression suites and
# the real production-tree scans (raw colours + unknown-token utilities, THR-376)
# inseparable across local and GitHub Web CI.
set -euo pipefail

cd "$(dirname "$0")/.."

bash scripts/test-design-system-hex-scanner.sh
bash scripts/verify-design-system.sh --scan-hex
bash scripts/test-design-system-token-scanner.sh
bash scripts/verify-design-system.sh --scan-tokens
