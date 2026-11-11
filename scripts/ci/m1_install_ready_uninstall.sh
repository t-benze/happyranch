#!/usr/bin/env bash
# Disposable GitHub-hosted Ubuntu 24.04 only. Bash keeps the CLI phases visible.
set -euo pipefail
M1_BUILD=${M1_BUILD:-${RUNNER_TEMP:-/tmp}/managed-m1-build}
M1_WORK=/var/tmp/happyranch-m1
M1_LOGS=${M1_LOGS:-${RUNNER_TEMP:-/tmp}/managed-m1-logs}
python=/opt/happyranch/venv/bin/python
services=(happyranch-connector.service happyranch-tsnet-sidecar.service)
target=happyranch-managed.target
fixtures=(happyranch-m1-headscale.service happyranch-m1-peer.service happyranch-m1-http.service)
fail() { echo "M1: $*" >&2; exit 1; }
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
  mkdir -p "$M1_BUILD"
  /usr/bin/python3 - "$M1_BUILD" <<'PY'
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
(dest / 'fixture_http.py').write_text('''from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == '/api/v1/health' and self.headers.get('Authorization') == 'Bearer synthetic-m1-daemon-token'
        self.send_response(200 if ok else 403)
        self.end_headers()
        self.wfile.write(b'M1 loopback response' if ok else b'denied')
HTTPServer(('127.0.0.1', 18765), Handler).serve_forever()
''')
PY
}
provision() {
  [[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted ]] || fail 'requires a disposable GitHub-hosted runner'
  [[ $(uname -m) == x86_64 && $(ps -p 1 -o comm=) == systemd ]] || fail 'requires amd64 and systemd PID 1'
  for path in /opt/happyranch /etc/happyranch "$M1_WORK"; do absent "$path"; done
  ! getent passwd happyranch >/dev/null || fail 'happyranch account already exists'
  ! getent group happyranch >/dev/null || fail 'happyranch group already exists'
  mkdir -p "$M1_BUILD" "$M1_LOGS"
  config
  (cd app/linux/tsnet-sidecar && CGO_ENABLED=0 go build -trimpath -o "$M1_BUILD/happyranch-tsnet-sidecar" ./cmd/happyranch-tsnet-sidecar)
  uv build --wheel --out-dir "$M1_BUILD/dist"
  local wheels=("$M1_BUILD"/dist/*.whl)
  [[ ${#wheels[@]} == 1 && -f ${wheels[0]} ]] || fail 'expected one wheel'
  # Hosted runners can have group-writable /opt; installer requires safe parents.
  stat -c '/opt owner=%U group=%G mode=%a' /opt
  sudo chmod 0755 /opt
  sudo install -d -m 0755 /opt/happyranch/bin /etc/happyranch
  # Copy the SYSTEM interpreter: ProtectHome blocks uv-managed Python under home.
  sudo /usr/bin/python3 -m venv --copies /opt/happyranch/venv
  sudo "$python" -m pip install "${wheels[0]}"
  sudo install -m 0755 "$M1_BUILD/happyranch-tsnet-sidecar" /opt/happyranch/bin/
  # Installer validates this exact account; precreate it to own the token input.
  sudo useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin happyranch
  for name in connector sidecar policy; do sudo install -m 0644 "$M1_BUILD/$name.json" "/etc/happyranch/$name.json"; done
  printf '%s\n' synthetic-m1-daemon-token | sudo install -m 0600 -o happyranch -g happyranch /dev/stdin /etc/happyranch/daemon.token
  sudo install -d -m 0700 "$M1_WORK"
  curl --fail --location --retry 3 --connect-timeout 20 --max-time 120 \
    https://github.com/juanfont/headscale/releases/download/v0.25.1/headscale_0.25.1_linux_amd64 -o "$M1_BUILD/headscale"
  echo "d2cda0a5d748587f77c920a76cd1bf1ab429e5299ba5bc6b3dda90712721b45b  $M1_BUILD/headscale" | sha256sum --check
  curl --fail --location --retry 3 --connect-timeout 20 --max-time 120 \
    https://pkgs.tailscale.com/stable/tailscale_1.102.3_amd64.tgz -o "$M1_BUILD/tailscale.tgz"
  echo "36ddd9b51be57ffc2990cf76323cfa13643bfbb1b8a969f6183fa164741cdef5  $M1_BUILD/tailscale.tgz" | sha256sum --check
  tar -xzf "$M1_BUILD/tailscale.tgz" -C "$M1_BUILD"
  sudo install -m 0755 "$M1_BUILD/headscale" "$M1_WORK/headscale"
  sudo install -m 0755 "$M1_BUILD/tailscale_1.102.3_amd64/"{tailscale,tailscaled} "$M1_WORK/"
  sudo install -m 0644 "$M1_BUILD/fixture_http.py" "$M1_WORK/fixture_http.py"
  sudo openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
    -addext subjectAltName=DNS:localhost,IP:127.0.0.1 -keyout "$M1_WORK/key.pem" -out "$M1_WORK/cert.pem"
  sudo chmod 0600 "$M1_WORK/key.pem"
  sudo install -m 0644 "$M1_WORK/cert.pem" /usr/local/share/ca-certificates/happyranch-m1.crt
  sudo update-ca-certificates
  # No public DERP map; the two disposable nodes share this private cell.
  sudo tee "$M1_WORK/config.yaml" >/dev/null <<EOF
server_url: https://127.0.0.1:18080
listen_addr: 127.0.0.1:18080
metrics_listen_addr: 127.0.0.1:19090
grpc_listen_addr: 127.0.0.1:15043
unix_socket: $M1_WORK/headscale.sock
noise:
  private_key_path: $M1_WORK/noise.key
prefixes:
  v4: 100.64.0.0/10
  v6: fd7a:115c:a1e0::/48
database:
  type: sqlite
  sqlite:
    path: $M1_WORK/db.sqlite
tls_cert_path: $M1_WORK/cert.pem
tls_key_path: $M1_WORK/key.pem
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
    private_key_path: $M1_WORK/derp.key
    automatically_add_embedded_derp_region: true
  urls: []
  paths: []
log:
  format: text
  level: info
EOF
  sudo systemd-run --unit="${fixtures[0]}" "$M1_WORK/headscale" serve --config "$M1_WORK/config.yaml"
  wait_for curl --silent --fail --output /dev/null https://127.0.0.1:18080/health
  sudo "$M1_WORK/headscale" --config "$M1_WORK/config.yaml" users create ci
  for name in peer enrollment; do
    # The generated key goes straight into a root-owned 0600 file, never argv/logs.
    sudo bash -euo pipefail -c 'umask 077; "$1/headscale" --config "$1/config.yaml" preauthkeys create --user ci --reusable=false --expiration 10m | tr -d "\r\n" > "$1/$2.key"' _ "$M1_WORK" "$name"
  done
  sudo systemd-run --unit="${fixtures[1]}" "$M1_WORK/tailscaled" --state="$M1_WORK/peer.state" --socket="$M1_WORK/peer.sock" --tun=userspace-networking
  wait_for sudo test -S "$M1_WORK/peer.sock"
  sudo "$M1_WORK/tailscale" --socket="$M1_WORK/peer.sock" up --login-server=https://127.0.0.1:18080 \
    --auth-key="file:$M1_WORK/peer.key" --hostname=synthetic-peer-ci --accept-dns=false --timeout=60s
  sudo rm "$M1_WORK/peer.key"
  sudo systemd-run --unit="${fixtures[2]}" /usr/bin/python3 "$M1_WORK/fixture_http.py"
  wait_for curl --silent --output /dev/null http://127.0.0.1:18765/api/v1/health
}
installer() {
  local rc=0
  # The shell intentionally creates runner-owned artifact logs, not root files.
  # shellcheck disable=SC2024
  sudo timeout 330 "$python" -m runtime.remote_access.installer "$@" \
    > "$M1_LOGS/installer-$1.stdout" 2> "$M1_LOGS/installer-$1.stderr" || rc=$?
  cat "$M1_LOGS/installer-$1.stdout"
  cat "$M1_LOGS/installer-$1.stderr" >&2
  if [[ $1 == install ]]; then sudo rm -f "$M1_WORK/enrollment.key"; fi
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
}
request() {
  sudo -u happyranch "$python" - <<'PY'
import json
import subprocess
import sys
from urllib.request import Request, urlopen
pair = subprocess.check_output([sys.executable, '-m', 'runtime.remote_access.cli', 'pair',
                               '--config', '/etc/happyranch/connector.json', '--device', 'm1-client'], text=True)
code = pair.splitlines()[0].split(': ', 1)[1]
with urlopen(Request('http://127.0.0.1:18443/pair', data=code.encode()), timeout=10) as response:
    credential = json.load(response)['credential']
with urlopen(Request('http://127.0.0.1:18443/api/v1/health',
                     headers={'X-HappyRanch-Device-Credential': credential}), timeout=10) as response:
    assert response.status == 200 and response.read() == b'M1 loopback response'
print('Paired connector loopback request returned the expected HTTP 200 body; tailnet transport remains M2.')
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
    /etc/credstore/happyranch-tsnet-enrollment /etc/happyranch/enrollment.key "$M1_WORK/enrollment.key" \
    /var/lib/happyranch-{connector,tsnet-sidecar} /run/happyranch-{connector,tsnet-sidecar}; do
    if ! sudo test ! -e "$path" || ! sudo test ! -L "$path"; then fail "installer residue: $path"; fi
  done
  getent passwd happyranch
  getent group happyranch
  echo 'Uninstall/purge removed units, enrollment and service state. Payload, config, logs and account are documented retained items.'
}
logs() {
  mkdir -p "$M1_LOGS"
  local unit
  for unit in "${services[@]}" "$target" "${fixtures[@]}"; do
    # These redirects intentionally create runner-owned artifact logs.
    # shellcheck disable=SC2024
    sudo journalctl --no-pager -o short-precise -u "$unit" > "$M1_LOGS/$unit.journal.txt" 2>&1 || true
    # shellcheck disable=SC2024
    sudo systemctl status --no-pager --full "$unit" > "$M1_LOGS/$unit.status.txt" 2>&1 || true
    # shellcheck disable=SC2024
    sudo systemctl cat "$unit" > "$M1_LOGS/$unit.cat.txt" 2>&1 || true
    cat "$M1_LOGS/$unit."*.txt
  done
  local file
  for file in "$M1_LOGS"/installer-*; do [[ ! -f $file ]] || cat "$file"; done
  # Product readiness gate details are plain prose and contain no bearer/key.
  sudo -u happyranch "$python" -c 'from pathlib import Path; from runtime.remote_access.supervisor import ConnectorConfig, ConnectorSupervisor; report = ConnectorSupervisor(config=ConnectorConfig.from_file(Path("/etc/happyranch/connector.json"))).readiness_report(); [print(name, gate.ok, gate.detail) for name, gate in report.gates.items()]' \
    2>&1 | tee "$M1_LOGS/connector-readiness.txt" || true
}
teardown() {
  sudo rm -f "$M1_WORK/peer.key" "$M1_WORK/enrollment.key"
  # Preserve product residue for diagnosis; runner disposal removes retained data.
  sudo systemctl stop "${fixtures[@]}"
  sudo rm -f /usr/local/share/ca-certificates/happyranch-m1.crt
  sudo update-ca-certificates
}
case ${1:-} in
  config) config ;; # Rootless config generation for local inspection.
  run) provision; installer install --enrollment-key-file "$M1_WORK/enrollment.key"; ready; request; installer uninstall --purge; residue ;;
  logs) logs ;;
  teardown) teardown ;;
  *) fail 'expected config, run, logs or teardown' ;;
esac
