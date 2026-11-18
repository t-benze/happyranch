#!/usr/bin/env bash
# Fixture/mutation suite for the Pasture parity check
# (`verify-design-system.sh --scan-pasture`, THR-376 PR12).
#
# The canonical fixture is in parity with its trimmed Pasture reference. Each
# case changes ONE token, component class or reference value and requires exit
# 7 with the exact drift line (or exit 0 where the change must be ignored).
set -euo pipefail

cd "$(dirname "$0")/.."

scanner="scripts/verify-design-system.sh"
fixture="scripts/fixtures/design-system-pasture-parity"
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

tokens="src/design-system/tokens/tokens.css"
appbar="src/design-system/layouts/AppShell/AppBar.tsx"
sidebar="src/design-system/layouts/AppShell/Sidebar.tsx"
idbadge="src/design-system/patterns/IdBadge.tsx"
pagetitle="src/design-system/patterns/PageTitle.tsx"
card="src/design-system/primitives/Card.tsx"
reference="design/pasture-ds.css"

reset() {
  rm -rf "$scratch/tree"
  cp -R "$fixture" "$scratch/tree"
}

run_scan() {
  DESIGN_SYSTEM_SOURCE_ROOT="$scratch/tree/src" \
    DESIGN_SYSTEM_PASTURE_REFERENCE="$scratch/tree/$reference" \
    DESIGN_SYSTEM_TAILWIND_THEME="$scratch/tree/tailwind-theme.css" \
    bash "$scanner" --scan-pasture
}

cases=0
# expect EXIT LABEL [EXACT-STDERR-LINE...]: run the scan on the current tree.
expect() {
  local expected=$1 label=$2 out="$scratch/case.out" status=0
  shift 2
  run_scan >"$out" 2>&1 || status=$?
  if [ "$status" != "$expected" ]; then
    echo "expected exit $expected for: $label (got $status)" >&2
    cat "$out" >&2
    exit 1
  fi
  local line
  for line in "$@"; do
    if ! grep -qxF "  pasture drift: $line" "$out"; then
      echo "missing drift line for: $label: $line" >&2
      cat "$out" >&2
      exit 1
    fi
  done
  if [ "$expected" = 7 ]; then
    local reported
    reported=$(grep -c '^  pasture drift: ' "$out" || true)
    if [ "$reported" != "$#" ]; then
      echo "expected exactly $# drift line(s) for: $label (got $reported)" >&2
      cat "$out" >&2
      exit 1
    fi
  fi
  cases=$((cases + 1))
  echo "ok - $label"
}

# mutate FILE SED-EXPRESSION: apply one edit and prove it changed the file.
mutate() {
  local file="$scratch/tree/$1"
  cp "$file" "$scratch/before"
  sed -i "$2" "$file"
  if cmp -s "$file" "$scratch/before"; then
    echo "mutation did not apply: $1: $2" >&2
    exit 1
  fi
}

# --- canonical ---------------------------------------------------------------
reset
expect 0 'canonical fixture is in parity'
grep -F 'checks=44; drift=0' "$scratch/case.out"

# --- tokens.css (both themes, aliases followed) ------------------------------
reset
mutate "$tokens" 's/^  --color-info:            oklch(0.72 0.10 245);$/  --color-info:            oklch(0.72 0.11 245);/'
expect 7 'dark --color-info chroma 0.11 (the pre-PR12 main value)' \
  'id colour job [dark]: ds.css oklch(0.72 0.1 245) != implementation oklch(0.72 0.11 245)' \
  'id colour task [dark]: ds.css oklch(0.72 0.1 245) != implementation oklch(0.72 0.11 245)' \
  'id colour thread [dark]: ds.css oklch(0.72 0.1 245) != implementation oklch(0.72 0.11 245)'

reset
mutate "$tokens" 's/^  --color-surface-raised:  oklch(1 0 0);$/  --color-surface-raised:  oklch(0.99 0 0);/'
expect 7 'light card surface token' \
  'card surface [light]: ds.css oklch(1 0 0) != implementation oklch(0.99 0 0)'

reset
mutate "$tokens" 's/^  --color-border-default:  oklch(0.32 0.012 72);$/  --color-border-default:  oklch(0.36 0.012 72);/'
expect 7 'dark border token (dark block overrides light)' \
  'topbar border-bottom colour [dark]: ds.css oklch(0.32 0.012 72) != implementation oklch(0.36 0.012 72)' \
  'card border colour [dark]: ds.css oklch(0.32 0.012 72) != implementation oklch(0.36 0.012 72)'

reset
mutate "$tokens" 's/^  --color-id-task:         var(--color-info);$/  --color-id-task:         var(--color-accent-text);/'
expect 7 'ID alias re-pointed away from info (light; dark inherits it)' \
  'id colour task [light]: ds.css oklch(0.53 0.1 248) != implementation oklch(0.46 0.1 152)' \
  'id colour task [dark]: ds.css oklch(0.72 0.1 245) != implementation oklch(0.8 0.12 152)'

reset
mutate "$tokens" 's/^  --color-accent-soft:     oklch(0.32 0.06 152);$/  --color-accent-soft:     oklch(0.30 0.06 152);/'
expect 7 'dark nav-active fill token' \
  'nav-active background [dark]: ds.css oklch(0.32 0.06 152) != implementation oklch(0.3 0.06 152)'

reset
mutate "$tokens" 's/^  --text-page-title--letter-spacing: -0.3px;$/  --text-page-title--letter-spacing: -0.5px;/'
expect 7 'page-title tracking token' \
  'page title letter-spacing: ds.css -0.3px != implementation -0.5px'

reset
mutate "$tokens" 's/^  --radius-lg: 18px;$/  --radius-lg: 16px;/'
expect 7 'card radius token' 'card radius: ds.css 18px != implementation 16px'

reset
mutate "$tokens" 's/^  --shadow-pasture-sm: 0 1px 2px rgba(40, 30, 20, 0.05);$/  --shadow-pasture-sm: 0 1px 2px rgba(40, 30, 20, 0.08);/'
expect 7 'card shadow token' \
  'card shadow: ds.css 0 1px 2px rgba(40,30,20,0.05) != implementation 0 1px 2px rgba(40,30,20,0.08)'

reset
mutate "$tokens" 's/^  --font-display: "Newsreader", Georgia, serif;$/  --font-display: Georgia, serif;/'
expect 7 'display font family token' \
  'page title font family: ds.css newsreader != implementation georgia' \
  'appbar title font family: ds.css newsreader != implementation georgia'

reset
mutate "$tokens" '/^  --text-page-title--font-weight:    500;$/d'
expect 7 'page-title weight token removed (falls back to 400)' \
  'page title font weight: ds.css 500 != implementation 400'

# --- owning component classes ------------------------------------------------
reset
mutate "$appbar" 's/ tracking-\[-0.2px\]//'
expect 7 'AppBar title loses its tracking (the pre-PR12 main class list)' \
  'appbar title letter-spacing: ds.css -0.2px != implementation 0px'

reset
mutate "$appbar" 's/ h-14 / h-12 /; s/ gap-3.5 / gap-3 /'
expect 7 'topbar height and gap classes' \
  'topbar height: ds.css 56px != implementation 48px' \
  'topbar gap: ds.css 14px != implementation 12px'

reset
mutate "$appbar" 's/ px-6.5"/ px-6 py-1"/'
expect 7 'topbar padding class' \
  'topbar padding: ds.css 0px 26px 0px 26px != implementation 4px 24px 4px 24px'

reset
mutate "$appbar" 's#bg-surface-canvas/80#bg-surface-canvas/90#'
expect 7 'topbar background opacity' \
  'topbar background [light]: ds.css color-mix(in oklab,oklch(0.975 0.007 80) 80%,transparent) != implementation color-mix(in oklab,oklch(0.975 0.007 80) 90%,transparent)' \
  'topbar background [dark]: ds.css color-mix(in oklab,oklch(0.205 0.011 70) 80%,transparent) != implementation color-mix(in oklab,oklch(0.205 0.011 70) 90%,transparent)'

reset
mutate "$appbar" 's/ border-b / /'
expect 7 'topbar loses its bottom rule' 'topbar border width: ds.css 1px != implementation 0px'

reset
mutate "$appbar" 's/ font-medium / font-semibold /'
expect 7 'AppBar title weight class' 'appbar title font weight: ds.css 500 != implementation 600'

reset
mutate "$idbadge" 's/ font-medium / /'
expect 7 'IdBadge loses weight 500 (the pre-PR12 main class list)' \
  'id font weight: ds.css 500 != implementation 400'

reset
mutate "$idbadge" "s/  job: 'text-info',/  job: 'text-accent-text',/"
expect 7 'IdBadge kind mapped to a non-info colour' \
  'id colour job [light]: ds.css oklch(0.53 0.1 248) != implementation oklch(0.46 0.1 152)' \
  'id colour job [dark]: ds.css oklch(0.72 0.1 245) != implementation oklch(0.8 0.12 152)'

reset
mutate "$idbadge" 's/font-mono text-xs/font-mono text-lg/'
expect 7 'IdBadge size class' 'id font size: ds.css 12px != implementation 18px'

reset
mutate "$sidebar" 's/gap-2.5 rounded-sm px-2 py-1.5 md:gap-2.75 md:px-2.5 md:py-2 text-nav/gap-2.5 rounded-sm px-2 py-1.5 text-nav/'
expect 7 'nav row padding/gap (the pre-PR12 main class list)' \
  'nav row padding: ds.css 8px 10px 8px 10px != implementation 6px 8px 6px 8px' \
  'nav row gap: ds.css 11px != implementation 10px'

reset
mutate "$sidebar" 's/md:gap-2.75 md:px-2.5 md:py-2"/md:gap-2.75 md:py-2"/'
expect 7 'disabled nav row loses the md: inline padding' \
  'nav row (disabled) padding: ds.css 8px 10px 8px 10px != implementation 8px 8px 8px 8px'

reset
mutate "$sidebar" 's/ md:px-2.5 md:py-2 text-nav/ md:px-3 md:py-2 text-nav/'
expect 7 'md: utility overrides the unprefixed one' \
  'nav row padding: ds.css 8px 10px 8px 10px != implementation 8px 12px 8px 12px'

reset
mutate "$sidebar" 's/ focus-visible:ring-2 / hover:px-4 focus-visible:ring-2 /'
expect 0 'state variants (hover:) do not change the resting row'

reset
mutate "$sidebar" "s/? 'bg-accent-soft text-accent-text font-semibold'/? 'bg-accent-soft text-accent-text font-semibold border-l-2'/"
expect 7 'nav-active gains a left bar' 'nav-active left bar: ds.css none != implementation present'

reset
mutate "$sidebar" "s/? 'bg-accent-soft text-accent-text font-semibold'/? 'bg-surface-raised text-accent-text font-medium shadow-pasture-sm'/"
expect 7 'nav-active fill, weight and shadow' \
  'nav-active background [light]: ds.css oklch(0.93 0.045 152) != implementation oklch(1 0 0)' \
  'nav-active background [dark]: ds.css oklch(0.32 0.06 152) != implementation oklch(0.245 0.012 70)' \
  'nav-active font weight: ds.css 600 != implementation 500' \
  'nav-active box-shadow: ds.css none != implementation a shadow-* class'

reset
mutate "$card" "s/container: 'rounded-lg'/container: 'rounded-md'/; s/default: 'p-5'/default: 'p-4'/"
expect 7 'card radius and padding classes' \
  'card radius: ds.css 18px != implementation 12px' \
  'card padding: ds.css 20px 20px 20px 20px != implementation 16px 16px 16px 16px'

reset
mutate "$card" 's/shadow-pasture-sm border/shadow-pasture border/'
expect 7 'card shadow class' \
  'card shadow: ds.css 0 1px 2px rgba(40,30,20,0.05) != implementation 0 1px 3px rgba(40,30,20,0.06)'

reset
mutate "$pagetitle" 's/font-display text-page-title/font-display text-lg tracking-tight/'
expect 7 'PageTitle size/tracking classes' \
  'page title font size: ds.css 26px != implementation 18px' \
  'page title font weight: ds.css 500 != implementation 400' \
  'page title letter-spacing: ds.css -0.3px != implementation -0.45px'

reset
mutate "$appbar" 's/>{title}</>{props.title}</'
expect 7 'unlocatable owner class list fails instead of passing' \
  "$scratch/tree/$appbar: cannot locate the AppBar title <span>" \
  'appbar title font family: implementation unresolved (no font-<family> class); ds.css wants newsreader' \
  'appbar title font size: implementation unresolved (no text-<size token> class); ds.css wants 18px' \
  'appbar title font weight: implementation unresolved (no text-<size token> class); ds.css wants 500' \
  'appbar title letter-spacing: implementation unresolved (no text-<size token> class); ds.css wants -0.2px'

# --- the reference (ds.css) is read, not hard-coded -------------------------
reset
mutate "$reference" 's/^  --info: oklch(0.72 0.10 245);$/  --info: oklch(0.72 0.11 245);/'
expect 7 'reference dark --info changes' \
  'id colour job [dark]: ds.css oklch(0.72 0.11 245) != implementation oklch(0.72 0.1 245)' \
  'id colour task [dark]: ds.css oklch(0.72 0.11 245) != implementation oklch(0.72 0.1 245)' \
  'id colour thread [dark]: ds.css oklch(0.72 0.11 245) != implementation oklch(0.72 0.1 245)'

reset
mutate "$reference" 's/font-size: 18px; }$/font-size: 18px; letter-spacing: -0.4px; }/'
expect 7 'Direction-A override of .page-title wins over the base rule' \
  'appbar title letter-spacing: ds.css -0.4px != implementation -0.2px'

reset
mutate "$reference" 's/  padding: 8px 10px; border-radius/  padding: 9px 12px; border-radius/'
expect 7 'reference .nav-item padding changes' \
  'nav row padding: ds.css 9px 12px 9px 12px != implementation 8px 10px 8px 10px' \
  'nav row (disabled) padding: ds.css 9px 12px 9px 12px != implementation 8px 10px 8px 10px'

reset
mutate "$reference" 's/^\[data-dir="b"\] \.nav-item\.active::before/[data-dir="a"] .nav-item.active::before/'
expect 7 'reference gains a Direction-A left bar' 'nav-active left bar: ds.css present != implementation none'

reset
mutate "$reference" 's/^  \.topbar { padding: 0 16px; gap: 10px; }$/  .topbar { padding: 0 8px; gap: 4px; height: 40px; }/'
expect 0 '@media (mobile) overrides are ignored'

reset
mutate "$reference" 's/^\[data-dir="b"\]\[data-theme="dark"\] { --info: oklch(0.72 0.11 240); }$/[data-dir="b"][data-theme="dark"] { --info: oklch(0.5 0.2 10); }/'
mutate "$reference" 's/^\[data-dir="b"\] \.card { border-radius: var(--radius); }$/[data-dir="b"] .card { border-radius: 2px; }/'
expect 0 'Direction B values are ignored'

reset
mutate "$reference" '/^  --info: oklch(0.53 0.10 248);$/d'
expect 7 'an unresolvable reference value fails' \
  'id colour job [light]: ds.css reference unresolved (undefined --info)' \
  'id colour task [light]: ds.css reference unresolved (undefined --info)' \
  'id colour thread [light]: ds.css reference unresolved (undefined --info)'

# --- missing input -----------------------------------------------------------
reset
rm "$scratch/tree/$card"
expect 1 'a missing owning component is an input error'

echo "Pasture parity suite: $cases cases passed"
