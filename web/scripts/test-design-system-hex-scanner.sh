#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

scanner="scripts/verify-design-system.sh"
fixture="scripts/fixtures/design-system-hex-scanner"
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

run_scan() {
  DESIGN_SYSTEM_SOURCE_ROOT="$1" DESIGN_SYSTEM_HEX_ALLOWLIST="$2" \
    bash "$scanner" --scan-hex
}

cp -R "$fixture/src" "$scratch/src"
cp "$fixture/allowlist.tsv" "$scratch/allowlist.tsv"

receipt=$(run_scan "$scratch/src" "$scratch/allowlist.tsv")
grep -F 'Hex scan receipt: denominator=2 production files; hits=5; files=2' <<<"$receipt"
grep -F 'Hex scan files: App.tsx, theme.css' <<<"$receipt"

printf '\nexport const added = <div className="border-[#445566]" />\n' >> "$scratch/src/App.tsx"
if run_scan "$scratch/src" "$scratch/allowlist.tsv" >"$scratch/new.out" 2>&1; then
  echo 'expected an unlisted production hit to fail' >&2
  exit 1
fi
grep -F 'unlisted hit: App.tsx' "$scratch/new.out"
grep -F '#445566' "$scratch/new.out"

printf 'App.tsx\t#445566\tfixture added hit\n' >> "$scratch/allowlist.tsv"
run_scan "$scratch/src" "$scratch/allowlist.tsv" >/dev/null

printf '\nexport const duplicate = <div className="border-[#445566]" />\n' >> "$scratch/src/App.tsx"
if run_scan "$scratch/src" "$scratch/allowlist.tsv" >"$scratch/duplicate.out" 2>&1; then
  echo 'expected a duplicated hit to fail' >&2
  exit 1
fi
grep -F 'unlisted hit: App.tsx' "$scratch/duplicate.out"

sed 's/#445566/#445567/g' "$scratch/src/App.tsx" > "$scratch/App.next"
mv "$scratch/App.next" "$scratch/src/App.tsx"
if run_scan "$scratch/src" "$scratch/allowlist.tsv" >"$scratch/changed.out" 2>&1; then
  echo 'expected changed values and a stale allowlist row to fail' >&2
  exit 1
fi
grep -F 'unlisted hit: App.tsx' "$scratch/changed.out"
grep -F 'stale allowlist entry: App.tsx' "$scratch/changed.out"

grep -v '#445566' "$scratch/allowlist.tsv" > "$scratch/allowlist-next.tsv"
mv "$scratch/allowlist-next.tsv" "$scratch/allowlist.tsv"
grep -v '445567' "$scratch/src/App.tsx" > "$scratch/App.next"
mv "$scratch/App.next" "$scratch/src/App.tsx"
run_scan "$scratch/src" "$scratch/allowlist.tsv" >/dev/null

grep -v '#010203' "$scratch/src/App.tsx" > "$scratch/App.next"
mv "$scratch/App.next" "$scratch/src/App.tsx"
if run_scan "$scratch/src" "$scratch/allowlist.tsv" >"$scratch/stale.out" 2>&1; then
  echo 'expected a deleted legacy hit to make its allowlist row stale' >&2
  exit 1
fi
grep -F 'stale allowlist entry: App.tsx' "$scratch/stale.out"

echo 'design-system hex scanner tests passed'
