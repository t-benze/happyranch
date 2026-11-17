#!/usr/bin/env bash
# Fixture regression suite for the unknown-token utility scan
# (`verify-design-system.sh --scan-tokens`, THR-376).
set -euo pipefail

cd "$(dirname "$0")/.."

scanner="scripts/verify-design-system.sh"
fixture="scripts/fixtures/design-system-token-scanner"
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

run_scan() {
  DESIGN_SYSTEM_SOURCE_ROOT="$1" DESIGN_SYSTEM_TAILWIND_THEME="$2" \
    bash "$scanner" --scan-tokens
}

expect_exit() {
  local expected=$1 label=$2 out=$3
  shift 3
  local status=0
  "$@" >"$out" 2>&1 || status=$?
  if [ "$status" != "$expected" ]; then
    echo "expected exit $expected for: $label (got $status)" >&2
    cat "$out" >&2
    exit 1
  fi
}

cp -R "$fixture/src" "$scratch/src"
cp "$fixture/tailwind-theme.css" "$scratch/theme.css"

# Canonical fixture: exactly the seven undefined names fail (colour, spacing,
# radius, shadow, a variant-prefixed name and an @apply list); defined tokens,
# Tailwind default-theme tokens, keywords, bare side utilities, numeric values,
# comments and copy catalogs do not.
expect_exit 4 'canonical fixture' "$scratch/canonical.out" run_scan "$scratch/src" "$scratch/theme.css"
grep -F 'Unknown-token scan receipt: denominator=2 production files;' "$scratch/canonical.out"
grep -F 'hits=7' "$scratch/canonical.out"
for hit in \
  'App.tsx:7:25	bg-surface-page' \
  'App.tsx:7:41	text-link-primary' \
  'App.tsx:7:59	border-danger-border' \
  'App.tsx:7:86	p-huge' \
  'App.tsx:7:96	rounded-blob' \
  'App.tsx:7:109	shadow-glow' \
  'styles.css:3:17	text-nope'; do
  grep -F "unknown token utility: $hit" "$scratch/canonical.out"
done
if grep -E 'not-a-token|also-not-a-token|comment-only|end-to-end|top-level|left-out|text-heavy' "$scratch/canonical.out"; then
  echo 'comments or copy catalogs were scanned as class lists' >&2
  exit 1
fi

# Defining a token inside @theme resolves its utility.
sed 's/^  --text-body: 0.875rem;$/  --text-body: 0.875rem;\n  --color-surface-page: oklch(0.975 0.007 80);/' \
  "$fixture/src/design-system/tokens/tokens.css" > "$scratch/src/design-system/tokens/tokens.css"
expect_exit 4 'one token defined' "$scratch/defined.out" run_scan "$scratch/src" "$scratch/theme.css"
grep -F 'hits=6' "$scratch/defined.out"
if grep -F 'bg-surface-page' "$scratch/defined.out"; then
  echo 'a utility for a token defined in @theme still failed' >&2
  exit 1
fi
cp "$fixture/src/design-system/tokens/tokens.css" "$scratch/src/design-system/tokens/tokens.css"

# A Tailwind default-theme token resolves only while the default theme defines it.
grep -v -- '--radius-xl' "$fixture/tailwind-theme.css" > "$scratch/theme-without-xl.css"
expect_exit 4 'default token removed' "$scratch/default.out" run_scan "$scratch/src" "$scratch/theme-without-xl.css"
grep -F 'hits=8' "$scratch/default.out"
grep -F 'rounded-xl' "$scratch/default.out"

# Mapping every unknown utility to a defined token makes the scan pass.
sed -e 's/bg-surface-page/bg-surface/' -e 's/text-link-primary/text-ink/' -e 's/hover:border-danger-border/hover:border-line/' \
    -e 's/md:p-huge/md:p-gutter/' -e 's/rounded-blob/rounded-card/' -e 's/shadow-glow/shadow-soft/' \
  "$fixture/src/App.tsx" > "$scratch/src/App.tsx"
sed 's/text-nope/text-surface/' "$fixture/src/styles.css" > "$scratch/src/styles.css"
expect_exit 0 'all mapped' "$scratch/clean.out" run_scan "$scratch/src" "$scratch/theme.css"
grep -F 'hits=0' "$scratch/clean.out"

# Numeric spacing steps must stay on Tailwind's linear scale (THR-376 PR4):
# a linear --spacing-N passes, a re-mapped one (the removed t-shirt remap) fails.
sed 's/^  --spacing-gutter: 20px;$/  --spacing-0: 0;\n  --spacing-4: 1rem;\n  --spacing-gutter: 20px;/' \
  "$fixture/src/design-system/tokens/tokens.css" > "$scratch/src/design-system/tokens/tokens.css"
expect_exit 0 'linear numeric spacing' "$scratch/linear.out" run_scan "$scratch/src" "$scratch/theme.css"
grep -F 'hits=0' "$scratch/linear.out"
sed 's/^  --spacing-gutter: 20px;$/  --spacing-4: 1rem;\n  --spacing-5: 1.5rem;\n  --spacing-gutter: 20px;/' \
  "$fixture/src/design-system/tokens/tokens.css" > "$scratch/src/design-system/tokens/tokens.css"
expect_exit 5 'remapped numeric spacing' "$scratch/remap.out" run_scan "$scratch/src" "$scratch/theme.css"
grep -F 'non-linear spacing token: --spacing-5: 1.5rem (linear scale is 1.25rem)' "$scratch/remap.out"
if grep -F -- '--spacing-4' "$scratch/remap.out"; then
  echo 'a linear spacing step was reported as a remap' >&2
  exit 1
fi
cp "$fixture/src/design-system/tokens/tokens.css" "$scratch/src/design-system/tokens/tokens.css"

# Clearing a namespace in tokens.css removes Tailwind's defaults (THR-376 PR5):
# with `--shadow-*: initial` the default `shadow-sm` and bare `shadow` render no
# CSS and fail, while a shadow re-declared in tokens.css (`shadow-soft`) passes.
cp "$fixture/src/App.tsx" "$scratch/src/App.tsx"
cp "$fixture/src/styles.css" "$scratch/src/styles.css"
sed 's/^  --shadow-soft: /  --shadow-*: initial;\n  --shadow-soft: /' \
  "$fixture/src/design-system/tokens/tokens.css" > "$scratch/src/design-system/tokens/tokens.css"
expect_exit 4 'default shadow scale removed' "$scratch/shadow-reset.out" run_scan "$scratch/src" "$scratch/theme.css"
grep -F 'hits=9' "$scratch/shadow-reset.out"
grep -F 'unknown token utility: App.tsx:6:70	shadow-sm' "$scratch/shadow-reset.out"
grep -F 'unknown token utility: App.tsx:6:187	shadow' "$scratch/shadow-reset.out"
if grep -F 'shadow-soft' "$scratch/shadow-reset.out"; then
  echo 'a shadow re-declared in tokens.css failed after the reset' >&2
  exit 1
fi
# The same for radius: `rounded-xl`, bare `rounded` and bare `rounded-tl` fail.
sed 's/^  --radius-card: /  --radius-*: initial;\n  --radius-card: /' \
  "$fixture/src/design-system/tokens/tokens.css" > "$scratch/src/design-system/tokens/tokens.css"
expect_exit 4 'default radius scale removed' "$scratch/radius-reset.out" run_scan "$scratch/src" "$scratch/theme.css"
grep -F 'hits=10' "$scratch/radius-reset.out"
grep -F 'unknown token utility: App.tsx:6:59	rounded-xl' "$scratch/radius-reset.out"
grep -F 'unknown token utility: App.tsx:6:48	rounded-tl' "$scratch/radius-reset.out"
grep -F 'unknown token utility: App.tsx:6:194	rounded' "$scratch/radius-reset.out"
if grep -F 'rounded-card' "$scratch/radius-reset.out"; then
  echo 'a radius re-declared in tokens.css failed after the reset' >&2
  exit 1
fi
cp "$fixture/src/design-system/tokens/tokens.css" "$scratch/src/design-system/tokens/tokens.css"

# A missing scan input is a hard failure, never a silent pass.
expect_exit 1 'missing default theme' "$scratch/missing.out" run_scan "$scratch/src" "$scratch/absent-theme.css"
grep -F 'unknown-token scan input missing' "$scratch/missing.out"

echo 'design-system unknown-token scanner tests passed'
