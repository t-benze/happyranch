#!/usr/bin/env bash
# Fixture regression suite for the unknown-token utility scan
# (`verify-design-system.sh --scan-tokens`, THR-376).
set -euo pipefail

cd "$(dirname "$0")/.."

scanner="scripts/verify-design-system.sh"
fixture="scripts/fixtures/design-system-token-scanner"
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

# The fixture's own semantic colour names (the real list lives in the scanner).
fixture_semantic='surface ink line'
run_scan() {
  DESIGN_SYSTEM_SOURCE_ROOT="$1" DESIGN_SYSTEM_TAILWIND_THEME="$2" \
    DESIGN_SYSTEM_SEMANTIC_COLOURS="${SEMANTIC:-$fixture_semantic}" \
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
SEMANTIC="$fixture_semantic surface-page" \
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

# Default scale names that start with a digit (`2xl`, `2xs`) are theme keys, not
# numeric values: they resolve while the default theme declares them, and fail
# once their namespace is cleared, while the re-declared Pasture scale passes.
cat > "$scratch/src/App.tsx" <<'TSX'
export const Probe = () => <div className="shadow-2xs shadow-2xl rounded-2xl rounded-3xl rounded-4xl rounded-sm rounded-md rounded-lg rounded-pill shadow-pasture-sm shadow-pasture shadow-pasture-lg p-4 gap-x-3" />;
TSX
cp "$fixture/src/styles.css" "$scratch/src/styles.css"
{ cat "$fixture/tailwind-theme.css"; printf '@theme default {\n  --radius-2xl: 1rem;\n  --radius-3xl: 1.5rem;\n  --radius-4xl: 2rem;\n  --radius-sm: 0.25rem;\n  --shadow-2xs: 0 1px rgb(0 0 0 / 0.05);\n  --shadow-2xl: 0 25px 50px -12px rgb(0 0 0 / 0.25);\n}\n'; } > "$scratch/theme-scale.css"
sed 's/^  --text-body: 0.875rem;$/  --text-body: 0.875rem;\n  --radius-md: 12px;\n  --radius-lg: 18px;\n  --radius-pill: 999px;\n  --shadow-pasture-sm: 0 1px 2px rgba(40, 30, 20, 0.05);\n  --shadow-pasture: 0 2px 6px rgba(40, 30, 20, 0.06);\n  --shadow-pasture-lg: 0 12px 32px rgba(40, 30, 20, 0.12);/' \
  "$fixture/src/design-system/tokens/tokens.css" > "$scratch/scale-tokens.css"
cp "$scratch/scale-tokens.css" "$scratch/src/design-system/tokens/tokens.css"
sed -i 's/@theme {/@theme {\n  --radius-sm: 8px;/' "$scratch/src/design-system/tokens/tokens.css"
expect_exit 4 'numeric-prefix scale before reset (styles.css only)' "$scratch/scale-default.out" run_scan "$scratch/src" "$scratch/theme-scale.css"
grep -F 'hits=1' "$scratch/scale-default.out"
grep -F 'unknown token utility: styles.css:3:17	text-nope' "$scratch/scale-default.out"
sed 's/@theme {/@theme {\n  --radius-*: initial;\n  --radius-sm: 8px;\n  --shadow-*: initial;/' \
  "$scratch/scale-tokens.css" > "$scratch/src/design-system/tokens/tokens.css"
expect_exit 4 'numeric-prefix scale removed by reset' "$scratch/scale-reset.out" run_scan "$scratch/src" "$scratch/theme-scale.css"
grep -F 'hits=6' "$scratch/scale-reset.out"
for hit in \
  'App.tsx:1:44	shadow-2xs' \
  'App.tsx:1:55	shadow-2xl' \
  'App.tsx:1:66	rounded-2xl' \
  'App.tsx:1:78	rounded-3xl' \
  'App.tsx:1:90	rounded-4xl' \
  'styles.css:3:17	text-nope'; do
  grep -F "unknown token utility: $hit" "$scratch/scale-reset.out"
done
if grep -E 'rounded-(sm|md|lg|pill)|shadow-pasture|p-4|gap-x-3' "$scratch/scale-reset.out"; then
  echo 'a re-declared Pasture radius/shadow or a numeric spacing step failed after the reset' >&2
  exit 1
fi
cp "$fixture/src/App.tsx" "$scratch/src/App.tsx"
cp "$fixture/src/design-system/tokens/tokens.css" "$scratch/src/design-system/tokens/tokens.css"

# One colour vocabulary (THR-376 PR10): a non-semantic --color-* in tokens.css
# must be var() (or a color-mix() alpha tint) of a semantic token, in @theme
# and in the dark block alike; literals and aliases of aliases fail (exit 6).
tokens_fixture="$fixture/src/design-system/tokens/tokens.css"
with_light() { sed "s|^  --text-body: 0.875rem;\$|  --text-body: 0.875rem;\n$1|" "$tokens_fixture"; }
with_dark() { sed "s|^  --shadow-glow: \(.*\)\$|  --shadow-glow: \1\n$1|" "$tokens_fixture"; }

with_light '  --color-fg: oklch(0.28 0.014 60);' > "$scratch/src/design-system/tokens/tokens.css"
expect_exit 6 'literal alias in @theme' "$scratch/alias-light.out" run_scan "$scratch/src" "$scratch/theme.css"
grep -F 'literal alias colour: '"$scratch"'/src/design-system/tokens/tokens.css:12	--color-fg: oklch(0.28 0.014 60)' "$scratch/alias-light.out"
grep -c 'literal alias colour' "$scratch/alias-light.out" | grep -qx 1

with_dark '  --color-fg: oklch(0.94 0.008 80);' > "$scratch/src/design-system/tokens/tokens.css"
expect_exit 6 'literal alias in the dark block' "$scratch/alias-dark.out" run_scan "$scratch/src" "$scratch/theme.css"
grep -F 'tokens.css:17	--color-fg: oklch(0.94 0.008 80)' "$scratch/alias-dark.out"

with_light '  --color-fg-tint: oklch(0.93 0.045 152 / 0.60);' > "$scratch/src/design-system/tokens/tokens.css"
expect_exit 6 'literal alpha tint alias' "$scratch/alias-tint.out" run_scan "$scratch/src" "$scratch/theme.css"
grep -F -- '--color-fg-tint: oklch(0.93 0.045 152 / 0.60)' "$scratch/alias-tint.out"

with_light '  --color-fg: var(--color-ink);\n  --color-fg-muted: var(--color-fg);' > "$scratch/src/design-system/tokens/tokens.css"
expect_exit 6 'alias of an alias' "$scratch/alias-chain.out" run_scan "$scratch/src" "$scratch/theme.css"
grep -F -- '--color-fg-muted: var(--color-fg)' "$scratch/alias-chain.out"
if grep -F -- '--color-fg: var(--color-ink)' "$scratch/alias-chain.out"; then
  echo 'a var() alias of a semantic token was reported' >&2
  exit 1
fi

with_light '  --color-fg-tint: color-mix(in oklab, oklch(0.93 0.045 152) 60%, transparent);' > "$scratch/src/design-system/tokens/tokens.css"
expect_exit 6 'color-mix of a literal' "$scratch/alias-mix-literal.out" run_scan "$scratch/src" "$scratch/theme.css"
grep -F -- '--color-fg-tint: color-mix(in oklab, oklch(0.93 0.045 152) 60%, transparent)' "$scratch/alias-mix-literal.out"

# Valid aliases (var() and color-mix() of a semantic token, in both blocks) and a
# literal inside a comment pass the guard; the scan goes on to the canonical
# fixture's seven unknown utilities (exit 4), so the guard did not stop it.
with_light '  /* --color-old: oklch(0.5 0.1 150); */\n  --color-fg: var(--color-ink);\n  --color-fg-tint: color-mix(in oklab, var(--color-line) 60%, transparent);' \
  > "$scratch/aliases.css"
sed "s|^  --shadow-glow: \(.*\)\$|  --shadow-glow: \1\n  --color-fg: var(--color-surface);|" "$scratch/aliases.css" \
  > "$scratch/src/design-system/tokens/tokens.css"
expect_exit 4 'valid aliases' "$scratch/alias-ok.out" run_scan "$scratch/src" "$scratch/theme.css"
grep -F 'hits=7' "$scratch/alias-ok.out"
if grep -F 'literal alias colour' "$scratch/alias-ok.out"; then
  echo 'a valid alias or a commented literal failed the alias guard' >&2
  exit 1
fi

# Only names in the semantic list may hold literals: dropping `ink` from it makes
# its literal fail.
SEMANTIC='surface line' expect_exit 6 'literal outside the semantic list' "$scratch/alias-semantic.out" \
  run_scan "$scratch/src" "$scratch/theme.css"
grep -F -- '--color-ink: oklch(0.28 0.014 60)' "$scratch/alias-semantic.out"
# There are no exemptions: any non-semantic name holding a literal fails with
# the scanner's defaults, including the removed tier-slate-tint (THR-376 PR10).
with_light '  --color-tier-slate-tint: oklch(0.94 0.008 75);\n  --color-anything-else: oklch(0.5 0.1 150);' \
  > "$scratch/src/design-system/tokens/tokens.css"
expect_exit 6 'no alias literal exemption' "$scratch/alias-no-exempt.out" \
  run_scan "$scratch/src" "$scratch/theme.css"
grep -F -- '--color-tier-slate-tint: oklch(0.94 0.008 75)' "$scratch/alias-no-exempt.out"
grep -F -- '--color-anything-else: oklch(0.5 0.1 150)' "$scratch/alias-no-exempt.out"
grep -c 'literal alias colour' "$scratch/alias-no-exempt.out" | grep -qx 2
cp "$tokens_fixture" "$scratch/src/design-system/tokens/tokens.css"

# The v3 colour vocabulary is retired (THR-376 PR11). Against the REAL tokens.css
# and Tailwind theme, every deleted v3 utility is an unknown token, while the
# deferred bg-muted alias and the semantic replacements still resolve.
mkdir -p "$scratch/v3"
cat > "$scratch/v3/App.tsx" <<'TSX'
export const V3 = () => <div className="bg-bg text-fg border-border bg-bg-subtle bg-bg-raised text-fg-muted text-fg-subtle bg-accent/10 text-accent-fg hover:bg-surface bg-accent-muted bg-bg-muted bg-surface-canvas text-text-primary border-border-default bg-accent-default/10 text-text-inverse bg-surface-raised bg-accent-soft" />;
TSX
v3_scan() {
  DESIGN_SYSTEM_SOURCE_ROOT="$scratch/v3" DESIGN_SYSTEM_TOKENS=src/design-system/tokens/tokens.css \
    bash "$scanner" --scan-tokens
}
expect_exit 4 'retired v3 utilities' "$scratch/v3.out" v3_scan
grep -F 'hits=11' "$scratch/v3.out"
# The scanner reports the utility without its variant or opacity modifier.
for hit in \
  'App.tsx:1:41	bg-bg' \
  'App.tsx:1:47	text-fg' \
  'App.tsx:1:55	border-border' \
  'App.tsx:1:69	bg-bg-subtle' \
  'App.tsx:1:82	bg-bg-raised' \
  'App.tsx:1:95	text-fg-muted' \
  'App.tsx:1:109	text-fg-subtle' \
  'App.tsx:1:124	bg-accent' \
  'App.tsx:1:137	text-accent-fg' \
  'App.tsx:1:152	bg-surface' \
  'App.tsx:1:169	bg-accent-muted'; do
  grep -F "unknown token utility: $hit" "$scratch/v3.out"
done
if grep -E '	(bg-bg-muted|bg-surface-canvas|text-text-primary|border-border-default|bg-accent-default|text-text-inverse|bg-surface-raised|bg-accent-soft)$' "$scratch/v3.out"; then
  echo 'the deferred bg-muted alias or a semantic replacement failed' >&2
  exit 1
fi

# A missing scan input is a hard failure, never a silent pass.
expect_exit 1 'missing default theme' "$scratch/missing.out" run_scan "$scratch/src" "$scratch/absent-theme.css"
grep -F 'unknown-token scan input missing' "$scratch/missing.out"

echo 'design-system unknown-token scanner tests passed'
