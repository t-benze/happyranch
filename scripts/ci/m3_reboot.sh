#!/usr/bin/env bash
# Explicitly authorized disposable Ubuntu VM only. Never run on a product host.
set -euo pipefail
base=/var/lib/happyranch-m3
M2_BUILD=$base/build
M2_WORK=$base/cell
M2_LOGS=$base/logs/${M3_PASS:?set M3_PASS to 1 or 2}
MANAGED_DOWNLOADS=$base/downloads
# shellcheck source=scripts/ci/m2_tailnet_daemon.sh
source "$(dirname "${BASH_SOURCE[0]}")/m2_tailnet_daemon.sh"
fixtures=(happyranch-m3-headscale.service happyranch-m3-peer.service happyranch-m3-daemon.service)
fail() { echo "M3 reboot: $*" >&2; exit 1; }
[[ $EUID == 0 && ${M3_AUTHORIZED_VM:-} == yes && $M3_PASS =~ ^[12]$ ]] || fail 'requires root, M3_AUTHORIZED_VM=yes and pass 1 or 2'
mkdir -p "$M2_LOGS"
boot_id() { cat /proc/sys/kernel/random/boot_id; }
property() { systemctl show "$1" -p "$2" --value; }
both_inactive() {
  [[ $(property "${services[0]}" ActiveState) == inactive && $(property "${services[1]}" ActiveState) == inactive ]]
}
properties() {
  date -u '+%FT%TZ'
  boot_id
  systemctl show "${services[@]}" "$target" "${fixtures[@]}" \
    -p Id,Type,ActiveState,SubState,MainPID,NRestarts,Result,UnitFileState,ActiveEnterTimestampMonotonic,Restart,RestartUSec,WatchdogUSec,StartLimitIntervalUSec,StartLimitBurst
}
# Only TEST fixtures are rendered here. Product units remain installer-owned.
start_fixture() {
  local unit=$1 arg
  shift
  {
    printf '[Unit]\nDescription=HappyRanch M3 reboot fixture\nAfter=network-online.target\n[Service]\nRestart=on-failure\nRestartSec=1\n'
    while [[ ${1:-} == --setenv=* ]]; do
      printf 'Environment=%s\n' "${1#--setenv=}"
      shift
    done
    printf 'ExecStart='
    for arg in "$@"; do
      [[ $arg =~ ^[a-zA-Z0-9_./:=,-]+$ ]] || fail 'unsafe fixture argument'
      printf '%s ' "$arg"
    done
    printf '\n[Install]\nWantedBy=multi-user.target\n'
  } > "/etc/systemd/system/$unit"
  systemctl daemon-reload
  systemctl enable --now "$unit"
}
enrollment_absent() {
  local path
  for path in /etc/happyranch/enrollment.key /etc/credstore/happyranch-tsnet-enrollment \
    "$M2_WORK/enrollment.key"; do
    [[ ! -e $path && ! -L $path ]] || fail "enrollment input remains: $path"
  done
  # systemd retains its initial runtime copy until the enrolling service stops.
  # After reboot there is no source/credstore from which to create another copy.
  if [[ ${1:-restarted} == restarted ]]; then
    absent /run/credentials/happyranch-tsnet-sidecar.service/enrollment-key
  fi
  test -f /var/lib/happyranch-tsnet-sidecar/credential.consumed
}
headscale_snapshot() {
  local kind=$1
  if [[ $kind == nodes ]]; then
    "$M2_WORK/headscale" --config "$M2_WORK/config.yaml" nodes list --output json
  else
    "$M2_WORK/headscale" --config "$M2_WORK/config.yaml" preauthkeys list --user ci --output json
  fi | /usr/bin/python3 -c '
import hashlib, json, sys
kind = sys.argv[1]
rows = json.load(sys.stdin)
assert isinstance(rows, list) and len(rows) == 2, "expected exactly two records"
if kind == "nodes":
    assert {r["given_name"] for r in rows} == {"home-sidecar-ci", "synthetic-peer-ci"}
    assert all(r.get("node_key") and r.get("id") and r.get("ip_addresses") for r in rows)
    safe = [dict(id=r["id"], name=r["given_name"], ips=sorted(r["ip_addresses"]),
                 node_key_sha256=hashlib.sha256(r["node_key"].encode()).hexdigest()) for r in rows]
else:
    assert all(r.get("used") is True and not r.get("reusable", False) for r in rows)
    safe = [dict(id=r["id"], used=r["used"]) for r in rows]
print(json.dumps(sorted(safe, key=lambda r: str(r["id"])), sort_keys=True))
' "$kind"
}
# Same wire boundary as M2, but retain ONE credential privately across both boots.
paired_request() {
  wait_for sidecar_visible
  local address
  address=$(sidecar_ip)
  wait_for_tailnet "$address"
  "$python" - "$address" "$M2_WORK/paired" <<'PY'
import json, os, subprocess, sys
from pathlib import Path
address, credential_path = sys.argv[1:]
p = Path(credential_path)
def call(method, path, credential=None, data=None):
    config = ''
    if credential:
        config += 'header = ' + json.dumps('X-HappyRanch-Device-Credential: ' + credential) + '\n'
    if data:
        config += 'data-binary = ' + json.dumps(data) + '\n'
    output = subprocess.check_output(['curl', '-q', '--silent', '--show-error', '--connect-timeout', '5',
        '--max-time', '15', '--socks5-hostname', '127.0.0.1:11080', '--noproxy', '', '--config', '-',
        '--request', method, '--write-out', '\n%{http_code}', f'http://{address}:443' + path], input=config, text=True)
    body, status = output.rsplit('\n', 1)
    return int(status), json.loads(body)
if not p.exists():
    code = subprocess.check_output(['sudo', '-u', 'happyranch', sys.executable, '-m',
        'runtime.remote_access.cli', 'pair', '--config', '/etc/happyranch/connector.json',
        '--device', 'm3-reboot-client'], text=True).splitlines()[0].split(': ', 1)[1]
    status, body = call('POST', '/pair', data=code)
    assert status == 200, f'pair returned {status}'
    with p.open('x') as out:
        os.chmod(p, 0o600)
        out.write(body['credential'])
status, body = call('GET', '/api/v1/orgs/m2-synthetic/teams', p.read_text())
assert status == 200, f'paired real-daemon read returned {status}'
assert body == {'teams': [{'name': 'default', 'manager': None, 'workers': [],
    'manager_kind': 'human', 'human_manager': 'founder', 'is_default': True}]}
print('Saved paired credential: real-daemon tailnet teams returned 200 and expected body')
PY
}
identity_save() {
  enrollment_absent initial
  headscale_snapshot nodes | tee "$M2_LOGS/nodes-before.json"
  headscale_snapshot keys > "$M2_LOGS/keys-before.json"
  sha256sum /var/lib/happyranch-tsnet-sidecar/credential.consumed > "$M2_LOGS/marker-before.sha"
}
identity_check() {
  local label=$1 boundary=restarted
  if [[ $label == pre-reboot ]]; then boundary=initial; fi
  enrollment_absent "$boundary"
  headscale_snapshot nodes | tee "$M2_LOGS/nodes-$label.json"
  headscale_snapshot keys > "$M2_LOGS/keys-$label.json"
  sha256sum /var/lib/happyranch-tsnet-sidecar/credential.consumed > "$M2_LOGS/marker-$label.sha"
  cmp "$M2_LOGS/nodes-before.json" "$M2_LOGS/nodes-$label.json"
  cmp "$M2_LOGS/keys-before.json" "$M2_LOGS/keys-$label.json"
  cmp "$M2_LOGS/marker-before.sha" "$M2_LOGS/marker-$label.sha"
  echo 'Same node keys/IDs/IPs/count, consumed enrollment IDs and marker; no enrollment input'
}
changed_boot() {
  local file=$1 before after
  before=$(cat "$M2_LOGS/$file")
  after=$(boot_id)
  echo "boot_id $before -> $after"
  [[ $before != "$after" ]] || fail 'boot_id did not change'
}
stopped_sample() {
  local unit deadline=$((SECONDS + 30))
  [[ $(property "$target" UnitFileState) == disabled ]] || fail 'target is still enabled'
  while :; do
    for unit in "${services[@]}"; do
      [[ $(property "$unit" ActiveState) == inactive && $(property "$unit" SubState) == dead &&
         $(property "$unit" MainPID) == 0 && $(property "$unit" NRestarts) == 0 ]] || fail "$unit started/restarted despite intentional disable"
    done
    (( SECONDS >= deadline )) && break
    sleep 1
  done
  echo 'Both services inactive/dead, PID=0 and NRestarts=0 throughout at least 30s'
}
boot_journals() {
  local label=$1 boot
  for boot in -1 0; do
    journalctl -b "$boot" --no-pager -o short-precise -u "${services[0]}" -u "${services[1]}" \
      > "$M2_LOGS/$label-boot$boot.journal.txt"
    if [[ $label == stopped && $boot == 0 ]]; then
      grep -Fxq -- '-- No entries --' "$M2_LOGS/$label-boot$boot.journal.txt" || fail 'unexpected product journal during disabled boot'
    else
      grep -Fq happyranch "$M2_LOGS/$label-boot$boot.journal.txt" || fail 'missing product boot journal'
    fi
  done
}
prepare() {
  [[ $(. /etc/os-release; echo "$ID:$VERSION_ID") == ubuntu:24.04 ]] || fail 'Ubuntu 24.04 required'
  for unit in "${fixtures[@]}"; do absent "/etc/systemd/system/$unit"; done
  # Persistent journald auto-storage retains the previous boot on this test VM.
  mkdir -p /var/log/journal
  journalctl --flush
  provision_cell
  installer install --enrollment-key-file "$M2_WORK/enrollment.key"
  ready
  paired_request
  identity_save
  properties > "$M2_LOGS/installed.properties.txt"
}
teardown_vm() {
  # Refuse to remove diagnostic state until the product is genuinely purged.
  residue
  systemctl disable --now "${fixtures[@]}"
  local unit
  for unit in "${fixtures[@]}"; do rm "/etc/systemd/system/$unit"; done
  systemctl daemon-reload
  rm /usr/local/share/ca-certificates/happyranch-m2.crt
  update-ca-certificates
  rm -rf "$M2_WORK" /opt/happyranch /etc/happyranch /var/log/happyranch-connector /var/log/happyranch-tsnet-sidecar
  userdel happyranch
  if getent group happyranch >/dev/null; then groupdel happyranch; fi
  echo 'Purged product; removed only this test cell, payload/config/account and fixture units'
}
case ${1:-} in
  prepare) prepare ;;
  pre-reboot)
    ready; paired_request; identity_check pre-reboot
    [[ $(property "$target" UnitFileState) == enabled ]] || fail 'target not enabled'
    boot_id > "$M2_LOGS/boot-before"; properties > "$M2_LOGS/pre-reboot.properties.txt"
    journalctl --sync ;;
  post-reboot)
    changed_boot boot-before
    # Read-only observation: no start, restart, enable or installer install here.
    ready; paired_request; identity_check post-reboot
    boot_id > "$M2_LOGS/boot-after"; properties > "$M2_LOGS/post-reboot.properties.txt"
    boot_journals recovery
    echo 'PASS automatic reboot recovery; harness issued no systemctl start' ;;
  stop-pre-reboot)
    systemctl disable --now "$target"
    wait_for both_inactive
    properties > "$M2_LOGS/stop-pre-reboot.properties.txt"
    boot_id > "$M2_LOGS/stop-boot-before"; journalctl --sync ;;
  stop-post-reboot)
    changed_boot stop-boot-before
    properties > "$M2_LOGS/stop-post-reboot.properties.txt"
    stopped_sample
    boot_id > "$M2_LOGS/stop-boot-after"; boot_journals stopped
    echo 'PASS intentional stop/disable survived reboot'
    systemctl enable --now "$target"
    ready; paired_request; identity_check reenabled
    properties > "$M2_LOGS/reenabled.properties.txt"
    echo 'PASS explicit re-enable/start returned both services to READY' ;;
  negative-stop) # Expected to fail on the installed, enabled target; no mutation.
    stopped_sample ;;
  purge) installer uninstall --purge; residue; echo 'PASS purge residue' ;;
  logs) logs ;;
  teardown) teardown_vm ;;
  *) fail 'expected prepare|pre-reboot|post-reboot|stop-pre-reboot|stop-post-reboot|negative-stop|purge|logs|teardown' ;;
esac
