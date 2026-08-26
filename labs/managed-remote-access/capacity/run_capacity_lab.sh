#!/usr/bin/env bash
# =============================================================================
# Capacity lab entrypoint — managed remote access (merge unit D)
# =============================================================================
# LAB-ONLY reusable capacity spike harness. Runs the deterministic scenarios
# in harness/main.py on an isolated docker host and leaves machine-readable
# raw results under labs/managed-remote-access/capacity/results/<run-id>/.
#
# Required runtime: an isolated docker host. The already-authorized lab
# runtime for this repository is the GitHub Actions ubuntu-latest runner
# (ships docker) via the workflow_dispatch job .github/workflows/lab-capacity.yml.
#
# Usage:
#   bash run_capacity_lab.sh [--scenarios all] [--run-id cap-...]
#
# Exit 0 only when every requested scenario completed and no residue remains.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
HARNESS_DIR="${REPO_ROOT}/labs/managed-remote-access/capacity"
cd "${REPO_ROOT}"

SCENARIOS="all"
RUN_ID=""
while [ $# -gt 0 ]; do
  case "$1" in
    --scenarios) SCENARIOS="${2:?}"; shift 2 ;;
    --run-id) RUN_ID="${2:?}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# Prefer the repository uv runtime (deterministic); fall back to system python3.
PY_RUNNER=""
if command -v uv >/dev/null 2>&1; then
  PY_RUNNER="uv run --frozen python"
elif command -v python3 >/dev/null 2>&1; then
  PY_RUNNER="python3"
else
  echo "FATAL: neither uv nor python3 available" >&2
  exit 2
fi

# Fresh synthetic run id unless the caller pinned one. Unique per rerun,
# bounded resource names, deterministic cleanup (see harness/models.py).
if [ -z "${RUN_ID}" ]; then
  RUN_ID="cap-$(date -u +%Y%m%dT%H%M%SZ)-$(printf '%04x' $((RANDOM % 65536)))"
fi
OUT_DIR="${HARNESS_DIR}/results/${RUN_ID}"

echo "[lab] repo_root=${REPO_ROOT}"
echo "[lab] python runner: ${PY_RUNNER}"
echo "[lab] run_id:       ${RUN_ID}"
echo "[lab] scenarios:    ${SCENARIOS}"
echo "[lab] out_dir:      ${OUT_DIR}"

# shellcheck disable=SC2086
${PY_RUNNER} "${HARNESS_DIR}/harness/main.py" \
  --out-dir "${OUT_DIR}" \
  --run-id "${RUN_ID}" \
  --scenarios "${SCENARIOS}"

echo "[lab] raw results: ${OUT_DIR}"
echo "[lab] summary:     ${OUT_DIR}/overall.json"
