#!/usr/bin/env bash
# Local CI wrapper — mirrors GitHub Actions commands as closely as practical.
# Runs from the repo root. GitHub CI remains authoritative; this is pre-push
# feedback only, not a replacement for the full matrix.
#
# Node runtime contract: the web/all targets must run under effective Node.js
# major exactly 24 (the repository .nvmrc declaration), matching the GitHub
# "Web (Node 24)" job. The wrapper verifies this before any npm/uv work and
# exits nonzero otherwise — it never runs npm under a different Node major.
#
# Usage:
#   scripts/local_ci.sh [TARGET]
#
# Targets:
#   python       uv sync --frozen; uv run pytest tests/ -v -n 4
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

# ── Node.js runtime contract (web/all targets) ───────────────────────────
# The repository declares its exact Node line in .nvmrc (Node 24), matching
# the GitHub "Web (Node 24)" job (.github/workflows/ci.yml). The web and all
# targets must run under effective Node major 24 exactly — never "whatever is
# installed" — otherwise they are not GitHub-Web parity and can pass/fail for
# the wrong reason. The guard runs before any uv/npm work and exits nonzero
# when Node 24 cannot be verified.

NODE_DECLARATION_FILE="${REPO_ROOT}/.nvmrc"

node_declared_major() {
  # $1: raw .nvmrc content. Prints the declared major (leading integer) or
  # an empty string when the declaration is absent/malformed.
  local raw="${1:-}"
  raw="$(printf '%s' "$raw" | tr -d '[:space:]')"
  case "$raw" in
    v*) raw="${raw#v}" ;;
  esac
  case "$raw" in
    [0-9]*) printf '%s\n' "${raw%%[^0-9]*}" ;;
    *) printf '\n' ;;
  esac
}

effective_node_major() {
  # Prints the effective `node --version` major (leading integer) or empty
  # string when node is absent or its version is unparseable.
  local ver
  ver="$(node --version 2>/dev/null || true)"
  ver="${ver#v}"
  case "$ver" in
    [0-9]*) printf '%s\n' "${ver%%[^0-9]*}" ;;
    *) printf '\n' ;;
  esac
}

try_select_node() {
  # Best-effort: ask a conventional local version manager (nvm) to select the
  # declared Node major. Selection changes the current shell's PATH, so the
  # caller MUST re-verify the effective node version afterwards.
  local decl="$1"
  local nvm_script=""
  if [ -n "${NVM_DIR:-}" ] && [ -f "${NVM_DIR}/nvm.sh" ]; then
    nvm_script="${NVM_DIR}/nvm.sh"
  elif [ -n "${HOME:-}" ] && [ -f "${HOME}/.nvm/nvm.sh" ]; then
    nvm_script="${HOME}/.nvm/nvm.sh"
  fi
  if [ -n "$nvm_script" ]; then
    # shellcheck disable=SC1090,SC1091
    if . "$nvm_script" >/dev/null 2>&1; then
      if command -v nvm >/dev/null 2>&1; then
        if nvm use "$decl" >/dev/null 2>&1; then
          return 0
        fi
      fi
    fi
  fi
  return 1
}

ensure_node_declared() {
  # Fail-fast Node runtime guard. Exits nonzero (before any uv/npm work) when
  # the effective Node major does not exactly match the repository declaration.
  local declared_major effective_major raw
  raw="$(cat "$NODE_DECLARATION_FILE" 2>/dev/null || true)"
  declared_major="$(node_declared_major "$raw")"
  if [ -z "$declared_major" ]; then
    echo -e "${RED}ERROR: repository Node declaration (${NODE_DECLARATION_FILE}) is missing or malformed.${NC}" >&2
    echo "Expected a single Node major (e.g. \"24\")." >&2
    exit 1
  fi

  effective_major="$(effective_node_major)"
  if [ -n "$effective_major" ] && [ "$effective_major" = "$declared_major" ]; then
    return 0
  fi

  if try_select_node "$declared_major"; then
    effective_major="$(effective_node_major)"
    if [ -n "$effective_major" ] && [ "$effective_major" = "$declared_major" ]; then
      return 0
    fi
  fi

  echo -e "${RED}ERROR: effective Node.js ${effective_major:-<none>} does not match the repository declaration (Node ${declared_major} from ${NODE_DECLARATION_FILE}).${NC}" >&2
  echo "The web/all targets must run under Node ${declared_major} exactly to match GitHub CI" >&2
  echo "(Web (Node 24)); they never run under a different Node major." >&2
  echo "Remediation:" >&2
  echo "  - Install Node ${declared_major} so it resolves on PATH, or select it with a" >&2
  echo "    version manager, e.g.: nvm install ${declared_major} && nvm use ${declared_major}" >&2
  echo "  - Then re-run: scripts/local_ci.sh web  (or all)" >&2
  exit 1
}

run_python() {
  echo -e "${GREEN}=== Python unit tests ===${NC}"
  uv sync --frozen
  uv run pytest tests/ -v -n 4
}

run_web() {
  ensure_node_declared
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
  ensure_node_declared
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
  echo "               (uv sync --frozen + uv run pytest tests/ -v -n 4)"
  echo "  web          Run Web CI"
  echo "               (npm ci + lint + typecheck + build + vitest run)"
  echo "  integration  Run Python integration tests"
  echo "               (uv run pytest tests/ -v -m integration)"
  echo "  all          Default: runs python + web (mirrors GitHub PR CI)"
  echo "  help         Show this help"
  echo ""
  echo "Caveats:"
  echo "  - Web/all targets require effective Node.js major exactly 24 (the"
  echo "    repository .nvmrc declaration, matching the GitHub Web (Node 24)"
  echo "    job); the wrapper verifies this before any work and exits nonzero"
  echo "    otherwise."
  echo "  - Python tests use the installed uv + Python interpreter, not the"
  echo "    GHA 3.12/3.13/3.14 matrix."
  echo "  - Integration tests spawn an isolated daemon per test (tmp"
  echo "    HAPPYRANCH_DAEMON_HOME + ephemeral port), so a production"
  echo "    daemon does not conflict. Both share machine RAM."
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
