#!/usr/bin/env bash
# Disposable GitHub-hosted Ubuntu 24.04 only. Bash keeps the CLI phases visible.
set -euo pipefail
M2_BUILD=${M2_BUILD:-${RUNNER_TEMP:-/tmp}/managed-m2-build}
M2_WORK=/var/tmp/happyranch-m2
M2_LOGS=${M2_LOGS:-${RUNNER_TEMP:-/tmp}/managed-m2-logs}
MANAGED_DOWNLOADS=${MANAGED_DOWNLOADS:-${RUNNER_TEMP:-/tmp}/managed-downloads}
python=/opt/happyranch/venv/bin/python
services=(happyranch-connector.service happyranch-tsnet-sidecar.service)
target=happyranch-managed.target
fixtures=(happyranch-m2-headscale.service happyranch-m2-peer.service happyranch-m2-daemon.service)
fail() { echo "M2: $*" >&2; exit 1; }
absent() { [[ ! -e $1 && ! -L $1 ]] || fail "unexpected residue: $1"; }
wait_for() {
  local attempt
  for ((attempt=0; attempt<60; attempt++)); do
    if "$@"; then return 0; fi
    sleep 1
  done
  fail "timed out waiting for $*"
}
config() {
  mkdir -p "$M2_BUILD"
  /usr/bin/python3 - "$M2_BUILD" <<'PY'
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
dest = Path(sys.argv[1])
artifact = json.loads(Path('runtime/remote_access/route_policy.json').read_text())
policy = dict(schema_version=1, artifact_version=int(artifact['version']),
              issued_at=(datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
              max_age_seconds=3600, revision=1, state='active', artifact=artifact)
connector = dict(tenant_id='tenant-ci', home_id='home-ci', connector_id='connector-ci',
                 daemon_port=18765, daemon_token_path='/etc/happyranch/daemon.token',
                 policy_path='/etc/happyranch/policy.json',
                 state_path='/var/lib/happyranch-connector/trust-state.json',
                 system=True, service_user='happyranch', service_group='happyranch',
                 poll_seconds=0.2, managed=dict(bind_host='127.0.0.1', bind_port=18443))
sidecar = dict(StateDir='/var/lib/happyranch-tsnet-sidecar',
               ControlURL='https://127.0.0.1:18080', RoleIdentity='home-sidecar-ci',
               ExpectedPeer='synthetic-peer-ci', ListenAddr=':443',
               ConnectorAddr='127.0.0.1:18443', DERPPolicy='private-only')
for name, value in [('policy', policy), ('connector', connector), ('sidecar', sidecar)]:
    (dest / f'{name}.json').write_text(json.dumps(value))
(dest / 'fixture_daemon_probe.py').write_text("""# Local fixture setup uses the bearer only on loopback, never in the tailnet client.
import json
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
port, token_path = sys.argv[1:]
token = Path(token_path).read_text().strip()
base = f'http://127.0.0.1:{port}/api/v1'
client = build_opener(ProxyHandler({}))
route = '/orgs/m2-synthetic/teams'
def call(path, data=None):
    with client.open(Request(base + path, data=data, headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}), timeout=10) as response:
        assert response.status == 200
        return json.load(response)
call('/orgs', json.dumps({'slug': 'm2-synthetic'}).encode())
expected = {'teams': [{'name': 'default', 'manager': None, 'workers': [],
                      'manager_kind': 'human', 'human_manager': 'founder', 'is_default': True}]}
assert call(route) == expected, 'unexpected real-daemon teams body'
try:
    client.open(base + route, timeout=10)
except HTTPError as error:
    assert error.code == 401, f'expected direct missing-bearer 401, got {error.code}'
else:
    raise AssertionError('real daemon accepted an unauthenticated org read')
print('M2 fixture: synthetic org teams returned 200 with bearer and 401 without bearer')
""")
PY
}
provision() {
  [[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted ]] || fail 'requires a disposable GitHub-hosted runner'
  [[ $(uname -m) == x86_64 && $(ps -p 1 -o comm=) == systemd ]] || fail 'requires amd64 and systemd PID 1'
  for path in /opt/happyranch /etc/happyranch "$M2_WORK"; do absent "$path"; done
  ! getent passwd happyranch >/dev/null || fail 'happyranch account already exists'
  ! getent group happyranch >/dev/null || fail 'happyranch group already exists'
  mkdir -p "$M2_BUILD" "$M2_LOGS"
  config
  (cd app/linux/tsnet-sidecar && CGO_ENABLED=0 go build -trimpath -o "$M2_BUILD/happyranch-tsnet-sidecar" ./cmd/happyranch-tsnet-sidecar)
  uv build --wheel --out-dir "$M2_BUILD/dist"
  local wheels=("$M2_BUILD"/dist/*.whl)
  [[ ${#wheels[@]} == 1 && -f ${wheels[0]} ]] || fail 'expected one wheel'
  # Hosted runners can have group-writable /opt; installer requires safe parents.
  stat -c '/opt owner=%U group=%G mode=%a' /opt
  sudo chmod 0755 /opt
  sudo install -d -m 0755 /opt/happyranch/bin /etc/happyranch
  # Copy the SYSTEM interpreter: ProtectHome blocks uv-managed Python under home.
  sudo /usr/bin/python3 -m venv --copies /opt/happyranch/venv
  sudo "$python" -m pip install "${wheels[0]}"
  sudo install -m 0755 "$M2_BUILD/happyranch-tsnet-sidecar" /opt/happyranch/bin/
  # Installer validates this exact account; precreate it to own the token input.
  sudo useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin happyranch
  for name in connector sidecar policy; do sudo install -m 0644 "$M2_BUILD/$name.json" "/etc/happyranch/$name.json"; done
  printf '%s\n' synthetic-m2-daemon-token | sudo install -m 0600 -o happyranch -g happyranch /dev/stdin /etc/happyranch/daemon.token
  sudo install -d -m 0700 "$M2_WORK"
  mkdir -p "$MANAGED_DOWNLOADS"
  if [[ ! -f $MANAGED_DOWNLOADS/headscale ]]; then
    curl --fail --location --retry 3 --connect-timeout 20 --max-time 120 \
      https://github.com/juanfont/headscale/releases/download/v0.25.1/headscale_0.25.1_linux_amd64 -o "$MANAGED_DOWNLOADS/headscale"
  fi
  echo "d2cda0a5d748587f77c920a76cd1bf1ab429e5299ba5bc6b3dda90712721b45b  $MANAGED_DOWNLOADS/headscale" | sha256sum --check
  if [[ ! -f $MANAGED_DOWNLOADS/tailscale.tgz ]]; then
    curl --fail --location --retry 3 --connect-timeout 20 --max-time 120 \
      https://pkgs.tailscale.com/stable/tailscale_1.102.3_amd64.tgz -o "$MANAGED_DOWNLOADS/tailscale.tgz"
  fi
  echo "36ddd9b51be57ffc2990cf76323cfa13643bfbb1b8a969f6183fa164741cdef5  $MANAGED_DOWNLOADS/tailscale.tgz" | sha256sum --check
  tar -xzf "$MANAGED_DOWNLOADS/tailscale.tgz" -C "$M2_BUILD"
  sudo install -m 0755 "$MANAGED_DOWNLOADS/headscale" "$M2_WORK/headscale"
  sudo install -m 0755 "$M2_BUILD/tailscale_1.102.3_amd64/"{tailscale,tailscaled} "$M2_WORK/"
  sudo install -m 0644 "$M2_BUILD/fixture_daemon_probe.py" "$M2_WORK/fixture_daemon_probe.py"
  sudo openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
    -addext subjectAltName=DNS:localhost,IP:127.0.0.1 -keyout "$M2_WORK/key.pem" -out "$M2_WORK/cert.pem"
  sudo chmod 0600 "$M2_WORK/key.pem"
  sudo install -m 0644 "$M2_WORK/cert.pem" /usr/local/share/ca-certificates/happyranch-m2.crt
  sudo update-ca-certificates
  # No public DERP map; the two disposable nodes share this private cell.
  sudo tee "$M2_WORK/config.yaml" >/dev/null <<EOF
server_url: https://127.0.0.1:18080
listen_addr: 127.0.0.1:18080
metrics_listen_addr: 127.0.0.1:19090
grpc_listen_addr: 127.0.0.1:15043
unix_socket: $M2_WORK/headscale.sock
noise:
  private_key_path: $M2_WORK/noise.key
prefixes:
  v4: 100.64.0.0/10
  v6: fd7a:115c:a1e0::/48
database:
  type: sqlite
  sqlite:
    path: $M2_WORK/db.sqlite
tls_cert_path: $M2_WORK/cert.pem
tls_key_path: $M2_WORK/key.pem
dns:
  magic_dns: false
  base_domain: ci.invalid
derp:
  server:
    enabled: true
    region_id: 999
    region_code: ci
    region_name: CI
    stun_listen_addr: 127.0.0.1:13478
    private_key_path: $M2_WORK/derp.key
    automatically_add_embedded_derp_region: true
  urls: []
  paths: []
log:
  format: text
  level: info
EOF
  sudo systemd-run --unit="${fixtures[0]}" "$M2_WORK/headscale" serve --config "$M2_WORK/config.yaml"
  wait_for curl --silent --fail --connect-timeout 2 --max-time 3 --output /dev/null https://127.0.0.1:18080/health
  sudo "$M2_WORK/headscale" --config "$M2_WORK/config.yaml" users create ci
  for name in peer enrollment; do
    # The generated key goes straight into a root-owned 0600 file, never argv/logs.
    sudo bash -euo pipefail -c 'umask 077; "$1/headscale" --config "$1/config.yaml" preauthkeys create --user ci --reusable=false --expiration 10m | tr -d "\r\n" > "$1/$2.key"' _ "$M2_WORK" "$name"
  done
  sudo systemd-run --unit="${fixtures[1]}" "$M2_WORK/tailscaled" --state="$M2_WORK/peer.state" --socket="$M2_WORK/peer.sock" --tun=userspace-networking --socks5-server=127.0.0.1:11080
  wait_for sudo test -S "$M2_WORK/peer.sock"
  sudo "$M2_WORK/tailscale" --socket="$M2_WORK/peer.sock" up --login-server=https://127.0.0.1:18080 \
    --auth-key="file:$M2_WORK/peer.key" --hostname=synthetic-peer-ci --accept-dns=false --timeout=60s
  sudo rm "$M2_WORK/peer.key"
  # The installed product entrypoint auto-provisions its runtime in this test-owned home.
  sudo install -d -m 0700 "$M2_WORK/daemon-home"
  sudo install -m 0600 /etc/happyranch/daemon.token "$M2_WORK/daemon-home/daemon.token"
  sudo systemd-run --unit="${fixtures[2]}" \
    --setenv="HAPPYRANCH_DAEMON_HOME=$M2_WORK/daemon-home" \
    --setenv=HAPPYRANCH_DAEMON_BIND_HOST=127.0.0.1 --setenv=HAPPYRANCH_DAEMON_PORT=18765 \
    "$python" -m runtime.daemon
  wait_for curl --silent --fail --connect-timeout 2 --max-time 3 --output /dev/null http://127.0.0.1:18765/api/v1/health
  sudo "$python" "$M2_WORK/fixture_daemon_probe.py" 18765 /etc/happyranch/daemon.token
}
installer() {
  local rc=0
  # The shell intentionally creates runner-owned artifact logs, not root files.
  # shellcheck disable=SC2024
  sudo timeout 330 "$python" -m runtime.remote_access.installer "$@" \
    > "$M2_LOGS/installer-$1.stdout" 2> "$M2_LOGS/installer-$1.stderr" || rc=$?
  cat "$M2_LOGS/installer-$1.stdout"
  cat "$M2_LOGS/installer-$1.stderr" >&2
  if [[ $1 == install ]]; then sudo rm -f "$M2_WORK/enrollment.key"; fi
  return "$rc"
}
ready() {
  local unit
  for unit in "${services[@]}"; do
    wait_for systemctl is-active --quiet "$unit"
    systemctl show "$unit" -p ActiveState,SubState,Type,NotifyAccess,Result,StatusText,ExecMainStatus,ActiveEnterTimestampMonotonic
    [[ $(systemctl show "$unit" -p Type --value) == notify && $(systemctl show "$unit" -p SubState --value) == running ]] || fail "$unit did not reach notify READY"
  done
  installer check-drift
  runtime_directories
}
runtime_directories() {
  local unit path
  date -u '+M2 runtime directories %Y-%m-%dT%H:%M:%S.%NZ'
  for unit in "${services[@]}"; do
    systemctl show "$unit" -p User,Group,ActiveState,SubState,RuntimeDirectory,RuntimeDirectoryPreserve || true
  done
  for path in /run/happyranch-{connector,tsnet-sidecar}; do
    sudo stat -c '%n owner=%U:%G mode=%a inode=%i type=%F' "$path" || true
    sudo ls -ld "$path" "$path"/* 2>/dev/null || true
  done
}
sidecar_ip() {
  sudo "$M2_WORK/tailscale" --socket="$M2_WORK/peer.sock" status --json | /usr/bin/python3 -c '
import ipaddress, json, sys
peers = json.load(sys.stdin).get("Peer", {}).values()
ips = [ip for peer in peers if peer["HostName"] == "home-sidecar-ci"
       for ip in peer["TailscaleIPs"] if ipaddress.ip_address(ip).version == 4]
if len(ips) != 1:
    raise SystemExit(1)
print(ips[0])'
}
sidecar_visible() { sidecar_ip >/dev/null; }
wait_for_tailnet() {
  local address=$1 deadline=$((SECONDS + 60)) remaining ping_timeout last_ping='not attempted'
  # Netmap visibility can precede endpoint discovery. TSMP traverses WireGuard
  # without either host's OS stack, including in userspace-networking mode.
  while (( (remaining = deadline - SECONDS) > 0 )); do
    ping_timeout=5
    if (( remaining < ping_timeout )); then ping_timeout=$remaining; fi
    if last_ping=$(sudo timeout "${remaining}s" "$M2_WORK/tailscale" --socket="$M2_WORK/peer.sock" \
      ping --tsmp --c 1 --timeout "${ping_timeout}s" "$address" 2>&1); then
      printf '%s\n' "$last_ping"
      return 0
    fi
    if (( SECONDS < deadline )); then sleep 1; fi
  done
  fail "tailnet data path to $address not reachable within 60s; last ping: $last_ping"
}
request() {
  wait_for sidecar_visible
  local address
  address=$(sidecar_ip)
  wait_for_tailnet "$address"
  # Credentials/codes stay in process memory and curl's stdin, never argv or logs.
  # No daemon token is read by this tailnet client.
  sudo -u happyranch "$python" - "$address" <<'PY'
import json
import subprocess
import sys
import time

base = f'http://{sys.argv[1]}:443'
route = '/api/v1/orgs/m2-synthetic/teams'
expected = {'teams': [{'name': 'default', 'manager': None, 'workers': [],
                      'manager_kind': 'human', 'human_manager': 'founder', 'is_default': True}]}

def request(method, path, credential=None, data=None):
    config = ''
    if credential is not None:
        config += 'header = ' + json.dumps('X-HappyRanch-Device-Credential: ' + credential) + '\n'
    if data is not None:
        config += 'data-binary = ' + json.dumps(data) + '\n'
    # -q disables ambient curlrc; the explicit SOCKS5 path has no direct fallback.
    result = subprocess.check_output([
        'curl', '-q', '--silent', '--show-error', '--connect-timeout', '5', '--max-time', '15',
        '--socks5-hostname', '127.0.0.1:11080', '--noproxy', '', '--config', '-',
        '--request', method, '--write-out', '\n%{http_code}', base + path,
    ], input=config, text=True)
    body, status = result.rsplit('\n', 1)
    return int(status), json.loads(body)

def ceremony(command, device):
    return subprocess.check_output([sys.executable, '-m', 'runtime.remote_access.cli', command,
                                   '--config', '/etc/happyranch/connector.json',
                                   '--device', device], text=True)

def pair(device):
    code = ceremony('pair', device).splitlines()[0].split(': ', 1)[1]
    status, body = request('POST', '/pair', data=code)
    assert status == 200, f'pair redemption returned {status}'
    return body['credential']

def allowed(credential):
    status, body = request('GET', route, credential)
    assert status == 200, f'paired org read returned {status}'
    assert body == expected, 'unexpected real-daemon teams body'

paired = pair('m2-client')
allowed(paired)
print('M2 step 2: paired tailnet -> sidecar -> connector -> real-daemon org teams returned 200')
for label, credential in [('no credential', None), ('never-paired credential', 'hrpair_' + '0' * 32)]:
    status, body = request('GET', route, credential)
    assert status == 403, f'{label}: expected 403, got {status}'
    assert body == {'error': 'identity_denied'}, f'{label}: wrong refusal body {body!r}'
    print(f'M2 step 3: unpaired {label} refused with 403 (identity_denied)')

revoked = pair('m2-revoked-client')
allowed(revoked)
print('M2 step 4: second paired device org teams returned 200 before revocation')
print(ceremony('revoke', 'm2-revoked-client').strip())
# Idle HTTP reconciliation normally takes one 0.2s poll; bound this observation.
deadline = time.monotonic() + 30
time.sleep(0.5)  # Allow more than two configured 0.2s connector poll intervals.
while True:
    status, body = request('GET', route, revoked)
    assert time.monotonic() < deadline, (
        f'revoked identity_denied refusal not observed within 30s; last status={status}, body={body!r}')
    if status == 403 and body == {'error': 'identity_denied'}:
        break
    assert status in (200, 403), f'revoked request returned unexpected status {status}, body={body!r}'
    time.sleep(0.2)
print('M2 step 4: revoked device refused with 403 (identity_denied) after bounded reconciliation wait')
allowed(paired)
for method, path, category in [
    ('POST', '/api/v1/executor-binaries/register', 'method_denied'),
    ('POST', '/api/v1/runtime/custom-cli/m2-operation/commit', 'route_denied'),
    ('POST', '/api/v1/runtime/use', 'route_denied'),
    ('DELETE', '/api/v1/runtime/adapters/m2-adapter', 'method_denied'),
    ('DELETE', '/api/v1/orgs/m2-synthetic', 'route_denied'),
    ('GET', '/api/v1/auth/bootstrap', 'route_denied'),
]:
    status, body = request(method, path, paired)
    assert status == 403, f'{method} {path}: expected 403, got {status}'
    assert body == {'error': category}, f'{method} {path}: expected {category}, got {body!r}'
    print(f'M2 step 5: forbidden {method} {path} refused with 403 ({category})')
allowed(paired)
status, body = request('GET', '/api/v1/orgs', paired)
assert status == 200 and [org['slug'] for org in body['orgs']] == ['m2-synthetic']
print('M2 step 5: synthetic org remains loaded and readable after forbidden DELETE')
PY
}
residue() {
  local path unit
  for unit in "${services[@]}" "$target"; do
    absent "/etc/systemd/system/$unit"
    [[ $(systemctl show "$unit" -p LoadState --value) == not-found ]] || fail "still loaded: $unit"
    ! systemctl is-active --quiet "$unit" || fail "still active: $unit"
  done
  for path in /etc/systemd/system/multi-user.target.wants/happyranch-managed.target \
    /etc/credstore/happyranch-tsnet-enrollment /etc/happyranch/enrollment.key "$M2_WORK/enrollment.key" \
    /var/lib/happyranch-{connector,tsnet-sidecar} /run/happyranch-{connector,tsnet-sidecar}; do
    if ! sudo test ! -e "$path" || ! sudo test ! -L "$path"; then fail "installer residue: $path"; fi
  done
  getent passwd happyranch
  getent group happyranch
  echo 'Uninstall/purge removed units, enrollment and service state. Payload, config, logs and account are documented retained items.'
}
logs() {
  mkdir -p "$M2_LOGS"
  runtime_directories 2>&1 | tee "$M2_LOGS/runtime-directories.txt"
  # Manager reloads are not included in the individual unit journals.
  # shellcheck disable=SC2024
  sudo journalctl -b --no-pager -o short-precise _PID=1 --grep='Reloading|Reloaded|happyranch' \
    > "$M2_LOGS/systemd-manager.journal.txt" 2>&1 || true
  cat "$M2_LOGS/systemd-manager.journal.txt"
  local unit
  for unit in "${services[@]}" "$target" "${fixtures[@]}"; do
    # These redirects intentionally create runner-owned artifact logs.
    # shellcheck disable=SC2024
    sudo journalctl --no-pager -o short-precise -u "$unit" > "$M2_LOGS/$unit.journal.txt" 2>&1 || true
    # shellcheck disable=SC2024
    sudo systemctl status --no-pager --full "$unit" > "$M2_LOGS/$unit.status.txt" 2>&1 || true
    # shellcheck disable=SC2024
    sudo systemctl cat "$unit" > "$M2_LOGS/$unit.cat.txt" 2>&1 || true
    cat "$M2_LOGS/$unit."*.txt
  done
  local file
  for file in "$M2_LOGS"/installer-*; do [[ ! -f $file ]] || cat "$file"; done
  # Product readiness gate details are plain prose and contain no bearer/key.
  sudo -u happyranch "$python" -c 'from pathlib import Path; from runtime.remote_access.supervisor import ConnectorConfig, ConnectorSupervisor; report = ConnectorSupervisor(config=ConnectorConfig.from_file(Path("/etc/happyranch/connector.json"))).readiness_report(); [print(name, gate.ok, gate.detail) for name, gate in report.gates.items()]' \
    2>&1 | tee "$M2_LOGS/connector-readiness.txt" || true
  echo 'M2 step 7: plain diagnostics captured, including daemon stderr, tailscaled and connector readiness'
}
teardown() {
  sudo rm -f "$M2_WORK/peer.key" "$M2_WORK/enrollment.key"
  # Preserve product residue for diagnosis; runner disposal removes retained data.
  local rc=0
  sudo systemctl stop "${fixtures[@]}" || rc=$?
  sudo rm -f /usr/local/share/ca-certificates/happyranch-m2.crt
  sudo update-ca-certificates
  return "$rc"
}
# M3 sources the same fixture/lifecycle helpers. Direct M2 commands are unchanged.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
case ${1:-} in
  config) config ;; # Rootless config generation for local inspection.
  run)
    provision
    installer install --enrollment-key-file "$M2_WORK/enrollment.key"
    ready
    echo 'M2 step 1: product install, both notify-READY services and check-drift passed'
    request
    M2_LOGS="$M2_LOGS/before-uninstall" logs
    installer uninstall --purge
    residue
    echo 'M2 step 6: uninstall --purge and M1 residue assertions passed; daemon fixture remains test-owned'
    ;;
  logs) logs ;;
  teardown) teardown ;;
  *) fail 'expected config, run, logs or teardown' ;;
esac
fi
