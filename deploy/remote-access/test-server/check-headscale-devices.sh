#!/bin/bash
# Root-only, read-only Headscale collection. Never send raw CLI output to journal.
set -euo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C
if [ "$(id -u)" -ne 0 ]; then
    echo 'ERROR device collection requires root' >&2
    exit 2
fi
scratch=$(mktemp -d /run/headscale-device-check.XXXXXX)
trap 'rm -rf -- "$scratch"' EXIT
trap 'exit 2' HUP INT TERM
fail() { echo 'ERROR device snapshot collection failed; no audit result' >&2; exit 2; }
# Suppress stdout AND stderr on failure; either can carry credentials.
headscale users list -o json >"$scratch/users.json" 2>/dev/null || fail
headscale nodes list -o json >"$scratch/nodes.json" 2>/dev/null || fail
# Validate names before using them as argv. Names never become filenames/logs.
if ! python3 - "$scratch/users.json" >"$scratch/users" 2>/dev/null <<'PY'
import json
import re
import sys
with open(sys.argv[1]) as source:
    rows = json.load(source)
if rows is None:
    rows = []
if not isinstance(rows, list) or len(rows) > 10000:
    raise ValueError
names = []
for row in rows:
    name = row['name']
    if not isinstance(name, str) or not re.fullmatch(r'[a-zA-Z0-9_.@+-]{1,255}', name):
        raise ValueError
    if name in names:
        raise ValueError
    names.append(name)
print('\n'.join(names), end='\n' if names else '')
PY
then
    fail
fi
key_files=()
index=0
while IFS= read -r user; do
    index=$((index + 1))
    path="$scratch/keys-$index.json"
    headscale preauthkeys list --user "$user" -o json >"$path" 2>/dev/null || fail
    key_files+=("$path")
done <"$scratch/users"
if [ "${#key_files[@]}" -eq 0 ]; then
    printf '[]\n' >"$scratch/keys-0.json"
    key_files+=("$scratch/keys-0.json")
fi
python3 /usr/local/libexec/happyranch-test-server/headscale_device_check.py \
    --approved /etc/happyranch-test-server/approved-devices.json \
    --nodes-json "$scratch/nodes.json" --keys-json "${key_files[@]}"
