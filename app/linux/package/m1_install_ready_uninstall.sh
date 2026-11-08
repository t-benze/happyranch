#!/usr/bin/env bash
# Disposable ubuntu-24.04 M1 fixture; no N3 admission/evidence machinery.
# These redirects intentionally create runner-owned logs, never privileged files.
# shellcheck disable=SC2024
set -euo pipefail
: "${M1_WORK:?workflow-owned fixture directory required}"
: "${M1_LOGS:?plain log directory required}"
: "${M1_PACKAGE:?built package required}"
: "${M1_PYTHON:?build environment Python required}"
units=(happyranch-connector.service happyranch-tsnet-sidecar.service happyranch-managed.target)
fail() { echo "M1: $*" >&2; exit 1; }
absent() {
  printf 'installer absence check: %s\n' "$1"
  if ! sudo test ! -e "$1" || ! sudo test ! -L "$1"; then
    fail "installer residue: $1"
  fi
}
wait_fixture() {
  local label=$1; shift
  for ((attempt=0; attempt<60; attempt++)); do
    if "$@"; then return 0; fi
    sleep 1
  done
  fail "fixture unavailable: $label"
}
fixture() {
  mkdir -p "$M1_WORK" "$M1_LOGS"
  chmod 0700 "$M1_WORK"
  [[ $(ps -p 1 -o comm=) == systemd ]] || fail 'PID1 must be systemd'
  # Preserve the runner observation before this explicit fixture preparation.
  stat -c '/opt owner=%U group=%G mode=%a' /opt
  sudo chmod 0755 /opt
  ! getent passwd happyranch >/dev/null || fail 'fixture account already exists'
  ! getent group happyranch >/dev/null || fail 'fixture group already exists'
  absent /etc/happyranch
  absent /opt/happyranch
  for unit in "${units[@]}"; do absent "/etc/systemd/system/$unit"; done
  sudo useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin happyranch
  touch "$M1_WORK/account-created"
  sudo install -d -m 0700 -o happyranch -g happyranch /etc/happyranch

  # Reuse the existing CI fixture's pinned control server and genuine peer.
  curl --fail --location --proto '=https' --tlsv1.2 \
    https://github.com/juanfont/headscale/releases/download/v0.25.1/headscale_0.25.1_linux_amd64 -o "$M1_WORK/headscale"
  echo "d2cda0a5d748587f77c920a76cd1bf1ab429e5299ba5bc6b3dda90712721b45b  $M1_WORK/headscale" | sha256sum --check
  curl --fail --location --proto '=https' --tlsv1.2 \
    https://pkgs.tailscale.com/stable/tailscale_1.102.3_amd64.tgz -o "$M1_WORK/tailscale.tgz"
  echo "36ddd9b51be57ffc2990cf76323cfa13643bfbb1b8a969f6183fa164741cdef5  $M1_WORK/tailscale.tgz" | sha256sum --check
  chmod 0700 "$M1_WORK/headscale"
  tar -xzf "$M1_WORK/tailscale.tgz" -C "$M1_WORK"
  mkdir "$M1_WORK/hs" "$M1_WORK/tls"
  openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
    -addext subjectAltName=DNS:localhost,IP:127.0.0.1 \
    -keyout "$M1_WORK/tls/key.pem" -out "$M1_WORK/tls/cert.pem" >/dev/null 2>&1
  chmod 0600 "$M1_WORK/tls/key.pem"
  cat > "$M1_WORK/hs/config.yaml" <<EOF
server_url: https://127.0.0.1:18080
listen_addr: 127.0.0.1:18080
metrics_listen_addr: 127.0.0.1:19090
grpc_listen_addr: 127.0.0.1:15043
unix_socket: $M1_WORK/hs/headscale.sock
log:
  format: json
  level: info
noise:
  private_key_path: $M1_WORK/hs/noise.key
prefixes:
  v4: 100.64.0.0/10
  v6: fd7a:115c:a1e0::/48
  allocation: sequential
database:
  type: sqlite3
  path: $M1_WORK/hs/db.sqlite
tls_cert_path: $M1_WORK/tls/cert.pem
tls_key_path: $M1_WORK/tls/key.pem
dns:
  magic_dns: false
  base_domain: ci.invalid
derp:
  server:
    enabled: true
    region_id: 999
    region_code: ci
    region_name: CI
    stun_listen_addr: "127.0.0.1:13478"
    private_key_path: $M1_WORK/hs/derp.key
  urls: []
  paths: []
  automatically_add_embedded_derp_region: true
policy:
  mode: file
  path: $M1_WORK/hs/policy.json
EOF
  printf '%s\n' '{"acls":[{"action":"accept","src":["*"],"dst":["*:*"]}]}' > "$M1_WORK/hs/policy.json"
  sudo install -m 0644 "$M1_WORK/tls/cert.pem" /usr/local/share/ca-certificates/happyranch-m1-ci.crt
  sudo update-ca-certificates >/dev/null
  "$M1_WORK/headscale" serve --config "$M1_WORK/hs/config.yaml" > "$M1_WORK/headscale.log" 2>&1 &
  echo "$!" > "$M1_WORK/headscale.pid"
  wait_fixture headscale curl --silent --fail --cacert "$M1_WORK/tls/cert.pem" https://127.0.0.1:18080/health
  "$M1_WORK/headscale" users create ci --config "$M1_WORK/hs/config.yaml" >/dev/null
  local peer_key sidecar_key ts_dir="$M1_WORK/tailscale_1.102.3_amd64"
  peer_key=$("$M1_WORK/headscale" preauthkeys create --user ci --reusable=false --expiration 10m --config "$M1_WORK/hs/config.yaml")
  sidecar_key=$("$M1_WORK/headscale" preauthkeys create --user ci --reusable=false --expiration 10m --config "$M1_WORK/hs/config.yaml")
  sudo "$ts_dir/tailscaled" --state="$M1_WORK/peer.state" --socket="$M1_WORK/peer.sock" --tun=userspace-networking > "$M1_WORK/peer.log" 2>&1 &
  echo "$!" > "$M1_WORK/peer.pid"
  wait_fixture peer sudo test -S "$M1_WORK/peer.sock"
  sudo "$ts_dir/tailscale" --socket="$M1_WORK/peer.sock" up --login-server=https://127.0.0.1:18080 \
    --auth-key="$peer_key" --hostname=synthetic-peer-ci --accept-dns=false --timeout=30s > "$M1_WORK/peer-up.log" 2>&1
  "$M1_PYTHON" -m http.server 18765 --bind 127.0.0.1 > "$M1_WORK/daemon.log" 2>&1 &
  echo "$!" > "$M1_WORK/daemon.pid"
  wait_fixture daemon curl --silent --fail --output /dev/null http://127.0.0.1:18765/
  printf '%s\n' synthetic-daemon-token > "$M1_WORK/daemon.token"
  printf '%s\n' "$sidecar_key" > "$M1_WORK/enrollment.key"
  sudo install -m 0600 -o root -g root "$M1_WORK/daemon.token" /etc/happyranch/daemon.token
  sudo install -m 0600 -o root -g root "$M1_WORK/enrollment.key" /etc/happyranch/enrollment.key
  "$M1_PYTHON" - "$M1_WORK" <<'PY'
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
work = Path(sys.argv[1])
artifact = json.loads(Path('tests/contract/managed_remote_access/route-policy.json').read_text())
(work / 'policy.json').write_text(json.dumps({'schema_version': 1, 'artifact_version': int(artifact['version']), 'issued_at': (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(), 'max_age_seconds': 3600, 'revision': 1, 'state': 'active', 'artifact': artifact}))
(work / 'connector.json').write_text(json.dumps({'tenant_id': 'tenant-ci', 'home_id': 'home-ci', 'connector_id': 'connector-ci', 'daemon_port': 18765, 'daemon_token_path': '/etc/happyranch/daemon.token', 'policy_path': '/etc/happyranch/policy.json', 'state_path': '/var/lib/happyranch-connector/trust-state.json', 'system': True, 'service_user': 'happyranch', 'service_group': 'happyranch', 'poll_seconds': 0.2, 'managed': {'bind_host': '127.0.0.1', 'bind_port': 18443, 'token_ttl_seconds': 300, 'credential_ttl_days': 365}}))
(work / 'sidecar.json').write_text(json.dumps({'StateDir': '/var/lib/happyranch-tsnet-sidecar', 'ControlURL': 'https://127.0.0.1:18080', 'RoleIdentity': 'home-sidecar-ci', 'ExpectedPeers': ['synthetic-peer-ci'], 'ListenAddr': ':443', 'ConnectorAddr': '127.0.0.1:18443', 'DERPPolicy': 'private-only'}))
PY
  for config in connector.json sidecar.json policy.json; do
    sudo install -m 0600 -o happyranch -g happyranch "$M1_WORK/$config" "/etc/happyranch/$config"
  done
}
package_operation() {
  local operation=$1
  sudo "$M1_PYTHON" - "$operation" "$M1_PACKAGE" \
    > >(tee "$M1_LOGS/installer-$operation.stdout") \
    2> >(tee "$M1_LOGS/installer-$operation.stderr" >&2) <<'PY'
import json
import sys
from pathlib import Path
from runtime.remote_access.linux_package import (
    install_linux_package, uninstall_linux_package, _MANAGED_PACKAGE_DIAGNOSTIC,
    _empty_managed_package_diagnostic, _managed_package_diagnostic_projection, UNITS,
)

QUERY_PROPERTY_NAMES = frozenset('''
LoadState FragmentPath DropInPaths NeedDaemonReload Wants Requires BindsTo PartOf
After Before Conflicts RequiresMountsFor DefaultDependencies StopWhenUnneeded
RefuseManualStart RefuseManualStop User Group Type NotifyAccess Restart RestartUSec
RestartMode StartLimitIntervalUSec KillMode TimeoutStartUSec TimeoutStopUSec
TimeoutAbortUSec WatchdogUSec WatchdogSignal NoNewPrivileges ProtectSystem
ProtectHome PrivateTmp PrivateDevices ProtectKernelTunables ProtectKernelModules
ProtectControlGroups RestrictSUIDSGID RestrictRealtime LockPersonality
MemoryDenyWriteExecute CapabilityBoundingSet AmbientCapabilities UMask
StateDirectory StateDirectoryMode ExecStop ExecStopPost DynamicUser
SupplementaryGroups ReadWritePaths Environment EnvironmentFiles PassEnvironment
LoadCredentialEncrypted RootDirectory RootImage ExecStartEx ExecStartPreEx
ExecStartPostEx LoadCredential RestartPreventExitStatus RestrictAddressFamilies
SystemCallArchitectures
'''.split())

def query_missing_names(value: object) -> dict | None:
    # The caller supplies the validated private projection. Never emit raw loaded
    # values, even for Query: only fixed requested names and the fixed unit name.
    if not isinstance(value, list) or len(value) != 16 or value[:1] != [2] or value[2:4] != ['Query', 'query']:
        return None
    delta = value[4]
    result = {'unit': UNITS[value[1]], 'missing': [], 'missing_complete': False}
    if delta[:1] not in ('=', '~'):
        return result
    count, _, prefix = delta[1:].partition(':')
    names = prefix.split()
    if delta.startswith('~') and prefix and not prefix.endswith(' '):
        names = names[:-1]  # The last name may have been clipped by existing caps.
    if any(name not in QUERY_PROPERTY_NAMES for name in names):
        return result
    result.update(missing=sorted(set(names)), missing_count=int(count),
                  missing_complete=delta.startswith('=') and len(set(names)) == int(count))
    return result

def without_loaded_context(value: object) -> object:
    # Even bounded loaded facts can contain mount/credential-source paths (F1).
    if isinstance(value, dict):
        result = {key: without_loaded_context(item) for key, item in value.items() if key != 'loaded'}
        missing = query_missing_names(value.get('loaded'))
        if missing is not None:
            result['missing_properties'] = missing
        return result
    if isinstance(value, list):
        return [without_loaded_context(item) for item in value]
    return value

facts = _empty_managed_package_diagnostic()
token = _MANAGED_PACKAGE_DIAGNOSTIC.set(facts)
try:
    if sys.argv[1] == 'install':
        print(install_linux_package(Path(sys.argv[2]), Path('/'), system_service=True))
    else:
        uninstall_linux_package(Path('/'))
except BaseException:
    # Existing stderr tee/upload retains ONLY the closed validated projection.
    # No raw facts, child stderr, exception text, identities or environment.
    try:
        source = {key: value for key, value in facts.items() if key not in ('identities', '_initiating')}
        diagnostic = without_loaded_context(_managed_package_diagnostic_projection(source))
        print('M1 managed-package diagnostic: ' + json.dumps(diagnostic, separators=(',', ':')), file=sys.stderr, flush=True)
    except Exception:
        try:
            print('M1 managed-package diagnostic: unavailable', file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass  # A failed diagnostic sink must preserve the original refusal.
    raise
finally:
    _MANAGED_PACKAGE_DIAGNOSTIC.reset(token)
PY
}
ready() {
  # Shipping start waits on Type=notify READY=1, under its own lifecycle deadline.
  # Sidecar main.go:238 = 75s; connector_supervisor.go:854 = 80s, each
  # clipped to any earlier lifecycle deadline. No fresh polling timeout is added.
  sudo /opt/happyranch/bin/happyranch-connector start --config /etc/happyranch/connector.json \
    > >(tee "$M1_LOGS/start.stdout") 2> >(tee "$M1_LOGS/start.stderr" >&2)
  for unit in "${units[@]:0:2}"; do
    local state type
    type=$(systemctl show "$unit" --property=Type --value)
    state=$(systemctl show "$unit" --property=ActiveState --value)
    printf 'READY check: %s Type=%s ActiveState=%s (systemd READY=1)\n' "$unit" "$type" "$state"
    [[ $type == notify && $state == active ]] || fail "$unit did not report READY"
    systemctl show "$unit" --property=ExecMainStartTimestampMonotonic --property=ActiveEnterTimestampMonotonic
  done
}
residue() {
  sudo systemctl daemon-reload
  # Aggregate every check so a failed path never suppresses the retained listing.
  sudo "$M1_PYTHON" - "${units[@]}" <<'PY'
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
units = sys.argv[1:]
failed = []
def check(condition, label):
    print(('ABSENT/OK: ' if condition else 'FAIL: ') + label, flush=True)
    if not condition:
        failed.append(label)
def absent(path):
    check(not os.path.lexists(path), str(path))
for path in ('/opt/happyranch', '/.happyranch-install-transaction.json', '/.happyranch-install-transaction.json.tmp', '/.happyranch-backup', '/.happyranch-units-backup'):
    absent(path)
for entry in Path('/').iterdir():
    if entry.name.startswith(('.happyranch-stage-', '.happyranch-backup', '.happyranch-units-backup', '.happyranch-install-transaction')):
        check(False, str(entry))
for base in ('/var/lib', '/run', '/var/log'):
    for name in ('happyranch-connector', 'happyranch-tsnet-sidecar'):
        absent(Path(base) / name)
for base in ('/etc/systemd/system', '/run/systemd/system'):
    for unit in units:
        absent(Path(base) / unit)
        absent(Path(base) / (unit + '.d'))
    for entry in Path(base).rglob('*'):
        if entry.name in units:
            check(False, 'unit file/enablement link: ' + str(entry))
for unit in units:
    result = subprocess.run(['systemctl', 'show', unit, '--property=LoadState', '--property=ActiveState', '--property=SubState', '--property=MainPID'], check=True, text=True, capture_output=True)
    print(result.stdout, end='')
    props = dict(line.split('=', 1) for line in result.stdout.splitlines())
    check(props.get('LoadState') == 'not-found' and props.get('ActiveState') == 'inactive' and props.get('SubState') == 'dead', 'unloaded/inactive ' + unit)
    if unit.endswith('.service'):
        check(props.get('MainPID') == '0', 'no unit process: ' + unit)
images = Path('/run/happyranch-connector-images')
print('documented-retained until reboot (ops guide 89-98): ' + str(images), flush=True)
boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
if os.path.lexists(images):
    # Print ALL entries, then validate the closed exception shape without deletion.
    subprocess.run(['find', str(images), '-ls'], check=True)
    check(stat.S_ISDIR(images.lstat().st_mode), 'retained namespace is a directory')
    for entry in images.iterdir() if stat.S_ISDIR(images.lstat().st_mode) else ():
        if entry.name == '.allocation.lock':
            check(stat.S_ISREG(entry.lstat().st_mode), str(entry))
            continue
        valid = re.fullmatch(re.escape(boot) + r'\.[0-9a-f]{32}\.(admission|stopped-admission|body)', entry.name)
        check(bool(valid) and stat.S_ISDIR(entry.lstat().st_mode), 'retained generation ' + str(entry))
        if valid and stat.S_ISDIR(entry.lstat().st_mode):
            members = list(entry.iterdir())
            check(len(members) == 1 and members[0].name == 'image' and stat.S_ISREG(members[0].lstat().st_mode), 'only image member: ' + str(entry))
# Full cheap executable/fd census, no process argv/environment or credential paths.
for proc in Path('/proc').iterdir():
    if not proc.name.isdecimal():
        continue
    try:
        for link in [proc / 'exe', *(proc / 'fd').iterdir()]:
            try:
                target = os.readlink(link)
            except FileNotFoundError:
                continue  # exited process/closed fd
            if target.startswith(str(images) + '/') or target.startswith('/opt/happyranch/'):
                check(False, 'process holds/executes package image: PID=' + proc.name)
    except FileNotFoundError:
        continue
print('fixture items (excluded from installer residue; teardown follows): account/group happyranch; /etc/happyranch configs and credential sources')
raise SystemExit(1 if failed else 0)
PY
}
logs() {
  mkdir -p "$M1_LOGS"
  local status=0
  sudo journalctl --no-pager -u "${units[0]}" -u "${units[1]}" -u "${units[2]}" > "$M1_LOGS/journal.txt" 2>&1 || status=1
  # status may return 3/4 for stopped/unloaded units; retain those real exits.
  for unit in "${units[@]}"; do
    if sudo systemctl status --no-pager --full "$unit" > "$M1_LOGS/$unit.status" 2>&1; then
      echo 'status exit=0'
    else
      echo "status exit=$? ($unit)"
    fi
    sudo systemctl show "$unit" --property=Id,LoadState,ActiveState,SubState,Result,MainPID,Type,StatusText,ExecMainCode,ExecMainStatus \
      > "$M1_LOGS/$unit.show" 2>&1 || status=1
  done
  return "$status"
}
teardown() {
  # Fixture-only teardown. Never remove installer residue or retained images.
  local pidfile pid status=0
  for pidfile in "$M1_WORK"/*.pid; do
    [[ -f $pidfile ]] || continue
    pid=$(cat "$pidfile")
    if sudo kill -0 "$pid" 2>/dev/null; then sudo kill "$pid" || status=1; fi
  done
  if [[ -f $M1_WORK/account-created ]]; then
    # Refuse account/config removal if a service still runs after a failed step.
    for unit in "${units[@]:0:2}"; do
      [[ $(systemctl show "$unit" --property=MainPID --value) == 0 ]] || return 1
    done
    sudo rm -rf -- /etc/happyranch
    sudo userdel happyranch || status=1
    if getent group happyranch >/dev/null; then sudo groupdel happyranch || status=1; fi
  fi
  sudo rm -f /usr/local/share/ca-certificates/happyranch-m1-ci.crt
  sudo update-ca-certificates >/dev/null || status=1
  return "$status"
}
case "${1:-}" in
  fixture) fixture ;;
  install) package_operation install ;;
  ready) ready ;;
  uninstall) package_operation uninstall ;;
  residue) residue ;;
  logs) logs ;;
  teardown) teardown ;;
  *) fail 'expected fixture/install/ready/uninstall/residue/logs/teardown' ;;
esac
