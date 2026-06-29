#!/usr/bin/env bash
# Local CI wrapper — mirrors GitHub Actions commands as closely as practical.
# Runs from the repo root. GitHub CI remains authoritative; this is pre-push
# feedback only, not a replacement for the full matrix.
#
# Usage:
#   scripts/local_ci.sh [TARGET]
#
# Targets:
#   python       uv sync --frozen; uv run pytest tests/ -v
#   web          cd web; npm ci; npm run lint; npm run typecheck;
#                npm run build; npx vitest run
#   integration  uv sync --frozen; uv run pytest tests/ -v -m integration
#   all          python + web (default; mirrors GitHub PR CI)
#   help         Show this help
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${REPO_ROOT}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

run_python() {
  echo -e "${GREEN}=== Python unit tests ===${NC}"
  uv sync --frozen
  uv run pytest tests/ -v
}

run_web() {
  echo -e "${GREEN}=== Web CI ===${NC}"
  cd web
  npm ci
  echo -e "${YELLOW}--- Lint ---${NC}"
  npm run lint
  echo -e "${YELLOW}--- Typecheck ---${NC}"
  npm run typecheck
  echo -e "${YELLOW}--- Build ---${NC}"
  npm run build
  echo -e "${YELLOW}--- Test (non-watch) ---${NC}"
  npx vitest run
}

run_integration() {
  echo -e "${GREEN}=== Python integration tests ===${NC}"
  uv sync --frozen
  uv run pytest tests/ -v -m integration
}

run_all() {
  run_python
  echo ""
  run_web
}

show_help() {
  echo "Usage: scripts/local_ci.sh [TARGET]"
  echo ""
  echo "Local CI wrapper — mirrors GitHub Actions commands as closely as practical."
  echo "GitHub CI remains authoritative; this is pre-push feedback only."
  echo ""
  echo "Targets:"
  echo "  python       Run Python unit tests"
  echo "               (uv sync --frozen + uv run pytest tests/ -v)"
  echo "  web          Run Web CI"
  echo "               (npm ci + lint + typecheck + build + vitest run)"
  echo "  integration  Run Python integration tests"
  echo "               (uv run pytest tests/ -v -m integration)"
  echo "  all          Default: runs python + web (mirrors GitHub PR CI)"
  echo "  help         Show this help"
  echo ""
  echo "Caveats:"
  echo "  - Python tests use the installed uv + Python interpreter, not the"
  echo "    GHA 3.12/3.13/3.14 matrix."
  echo "  - Integration tests spawn a real daemon and may conflict with a"
  echo "    running daemon on port 8765."
  echo "  - Web CI runs vitest run (non-watch mode), matching GHA behavior."
  echo "  - uv sync --frozen ensures lockfile parity; run 'uv lock' first if"
  echo "    you've changed pyproject.toml."
}

case "${1:-all}" in
  python)       run_python ;;
  web)          run_web ;;
  integration)  run_integration ;;
  all)          run_all ;;
  help|-h|--help) show_help ;;
  *)
    echo -e "${RED}Unknown target: $1${NC}" >&2
    show_help
    exit 1
    ;;
esac
