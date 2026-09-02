#!/usr/bin/env bash
# CI-only, zero-skip proof of the packaged N3 production seam.
set -euo pipefail

HEADSCALE_VERSION=0.25.1
HEADSCALE_SHA256=d2cda0a5d748587f77c920a76cd1bf1ab429e5299ba5bc6b3dda90712721b45b
TAILSCALE_VERSION=1.102.3
TAILSCALE_SHA256=36ddd9b51be57ffc2990cf76323cfa13643bfbb1b8a969f6183fa164741cdef5
readonly HEADSCALE_VERSION HEADSCALE_SHA256 TAILSCALE_VERSION TAILSCALE_SHA256

fail() { echo "n3-real-systemd: $1" >&2; exit 1; }
wait_for() {
  local label="$1"; shift
  for _attempt in $(seq 1 120); do "$@" && return 0; sleep 1; done
  fail "timeout waiting for $label"
}
port_open() { timeout 1 bash -c "</dev/tcp/127.0.0.1/$1" 2>/dev/null; }
active() { sudo systemctl is-active --quiet "$1"; }
absent() { ! sudo test -e "$1" || fail "residue at $1"; }

[[ -n "${PACKAGE_TAR:-}" && -f "$PACKAGE_TAR" ]] || fail "PACKAGE_TAR missing"
[[ "$(ps -p 1 -o comm= | xargs)" == systemd ]] || fail "PID 1 is not systemd"
systemctl is-system-running >/dev/null 2>&1 || [[ "$(systemctl is-system-running 2>/dev/null)" == degraded ]] || fail "system manager unavailable"
sudo -n true || fail "passwordless sudo unavailable"
sudo systemd-run --quiet --wait --collect --unit=happyranch-n3-qualification /bin/true || fail "transient units unavailable"

work="$(mktemp -d)"
headscale_pid=""; peer_pid=""; daemon_pid=""
cleanup() {
  local original_status=$? cleanup_failed=0
  set +e
  sudo systemctl stop happyranch-managed.target
  sudo systemctl disable happyranch-managed.target
  sudo systemctl reset-failed happyranch-connector.service happyranch-tsnet-sidecar.service happyranch-managed.target
  sudo rm -f /etc/systemd/system/happyranch-connector.service /etc/systemd/system/happyranch-tsnet-sidecar.service /etc/systemd/system/happyranch-managed.target
  sudo systemctl daemon-reload
  [[ -z "$peer_pid" ]] || sudo kill "$peer_pid"
  [[ -z "$daemon_pid" ]] || kill "$daemon_pid"
  [[ -z "$headscale_pid" ]] || kill "$headscale_pid"
  sudo rm -f /usr/local/share/ca-certificates/happyranch-n3-ci.crt
  sudo update-ca-certificates >/dev/null 2>&1
  sudo rm -rf /opt/happyranch /etc/happyranch /var/lib/happyranch-connector /var/lib/happyranch-tsnet-sidecar /run/happyranch-connector /run/happyranch-tsnet-sidecar /var/log/happyranch-connector /var/log/happyranch-tsnet-sidecar
  systemctl list-unit-files happyranch-managed.target happyranch-connector.service happyranch-tsnet-sidecar.service --no-legend 2>/dev/null | grep -q . && cleanup_failed=1
  for path in /opt/happyranch /etc/happyranch /var/lib/happyranch-connector /var/lib/happyranch-tsnet-sidecar /run/happyranch-connector /run/happyranch-tsnet-sidecar /.happyranch-install-transaction.json /.happyranch-backup /.happyranch-units-backup; do
    sudo test ! -e "$path" || cleanup_failed=1
  done
  ! port_open 18443 || cleanup_failed=1
  rm -rf "$work"
  (( cleanup_failed == 0 )) || echo "n3-real-systemd: teardown residue" >&2
  trap - EXIT INT TERM
  (( original_status != 0 )) && exit "$original_status"
  exit "$cleanup_failed"
}
trap cleanup EXIT INT TERM

hs_url="https://github.com/juanfont/headscale/releases/download/v${HEADSCALE_VERSION}/headscale_${HEADSCALE_VERSION}_linux_amd64"
ts_url="https://pkgs.tailscale.com/stable/tailscale_${TAILSCALE_VERSION}_amd64.tgz"
curl --fail --location --proto '=https' --tlsv1.2 "$hs_url" -o "$work/headscale"
echo "$HEADSCALE_SHA256  $work/headscale" | sha256sum --check --status || fail "Headscale checksum mismatch"
curl --fail --location --proto '=https' --tlsv1.2 "$ts_url" -o "$work/tailscale.tgz"
echo "$TAILSCALE_SHA256  $work/tailscale.tgz" | sha256sum --check --status || fail "Tailscale checksum mismatch"
chmod 0700 "$work/headscale"; tar -xzf "$work/tailscale.tgz" -C "$work"
ts_dir="$work/tailscale_${TAILSCALE_VERSION}_amd64"

mkdir -p "$work/hs" "$work/tls"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost -addext subjectAltName=DNS:localhost,IP:127.0.0.1 -keyout "$work/tls/key.pem" -out "$work/tls/cert.pem" >/dev/null 2>&1
chmod 0600 "$work/tls/key.pem"
cat >"$work/hs/config.yaml" <<EOF
server_url: https://127.0.0.1:18080
listen_addr: 127.0.0.1:18080
metrics_listen_addr: 127.0.0.1:19090
grpc_listen_addr: 127.0.0.1:15043
noise:
  private_key_path: $work/hs/noise.key
prefixes:
  v4: 100.64.0.0/10
  v6: fd7a:115c:a1e0::/48
  allocation: sequential
database:
  type: sqlite3
  path: $work/hs/db.sqlite
tls_cert_path: $work/tls/cert.pem
tls_key_path: $work/tls/key.pem
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
    private_key_path: $work/hs/derp.key
  urls: []
  paths: []
  automatically_add_embedded_derp_region: true
policy:
  mode: file
  path: $work/hs/policy.json
EOF
printf '%s\n' '{"acls":[{"action":"accept","src":["*"],"dst":["*:*"],"proto":["*"]}]}' >"$work/hs/policy.json"
sudo install -m 0644 "$work/tls/cert.pem" /usr/local/share/ca-certificates/happyranch-n3-ci.crt
sudo update-ca-certificates >/dev/null
"$work/headscale" serve --config "$work/hs/config.yaml" >"$work/headscale.log" 2>&1 & headscale_pid=$!
wait_for "Headscale HTTPS" curl --silent --fail --cacert "$work/tls/cert.pem" https://127.0.0.1:18080/health
"$work/headscale" users create ci --config "$work/hs/config.yaml"
peer_key="$("$work/headscale" preauthkeys create --user ci --reusable=false --expiration 10m --config "$work/hs/config.yaml")"
sidecar_key="$("$work/headscale" preauthkeys create --user ci --reusable=false --expiration 10m --config "$work/hs/config.yaml")"

sudo "$ts_dir/tailscaled" --state="$work/peer.state" --socket="$work/peer.sock" --tun=userspace-networking >"$work/peer.log" 2>&1 & peer_pid=$!
wait_for "synthetic peer local API" sudo test -S "$work/peer.sock"
sudo "$ts_dir/tailscale" --socket="$work/peer.sock" up --login-server=https://127.0.0.1:18080 --auth-key="$peer_key" --hostname=synthetic-peer-ci --accept-dns=false --timeout=30s

# N3 only needs a genuine reachable loopback daemon gate. Request admission
# and authorization-negative proof are deliberately deferred to N6.
python -m http.server 18765 --bind 127.0.0.1 >"$work/daemon.log" 2>&1 & daemon_pid=$!
wait_for "loopback daemon" port_open 18765
sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin happyranch 2>/dev/null || true
sudo install -d -m 0700 -o happyranch -g happyranch /etc/happyranch
printf '%s\n' synthetic-daemon-token >"$work/daemon.token"
sudo install -m 0600 -o happyranch -g happyranch "$work/daemon.token" /etc/happyranch/daemon.token

python - "$work" <<'PY'
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
base=Path(sys.argv[1])
artifact=json.loads(Path('tests/contract/managed_remote_access/route-policy.json').read_text())
(base/'policy.json').write_text(json.dumps({'schema_version':1,'artifact_version':int(artifact['version']),'issued_at':(datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat(),'max_age_seconds':3600,'revision':1,'state':'active','artifact':artifact}))
config={'tenant_id':'tenant-ci','home_id':'home-ci','connector_id':'connector-ci','daemon_port':18765,'daemon_token_path':'/etc/happyranch/daemon.token','policy_path':'/etc/happyranch/policy.json','state_path':'/var/lib/happyranch-connector/trust-state.json','system':True,'service_user':'happyranch','service_group':'happyranch','poll_seconds':0.2,'managed':{'bind_host':'127.0.0.1','bind_port':18443,'token_ttl_seconds':300,'credential_ttl_days':365}}
(base/'connector.json').write_text(json.dumps(config))
PY
sudo install -m 0600 -o happyranch -g happyranch "$work/connector.json" /etc/happyranch/connector.json
sudo install -m 0600 -o happyranch -g happyranch "$work/policy.json" /etc/happyranch/policy.json
printf '%s\n' "{\"StateDir\":\"/var/lib/happyranch-tsnet-sidecar\",\"ControlURL\":\"https://127.0.0.1:18080\",\"RoleIdentity\":\"home-sidecar-ci\",\"ExpectedPeers\":[\"synthetic-peer-ci\"],\"ListenAddr\":\":443\",\"ConnectorAddr\":\"127.0.0.1:18443\",\"DERPPolicy\":\"private-only\"}" >"$work/sidecar.json"
sudo install -m 0600 -o happyranch -g happyranch "$work/sidecar.json" /etc/happyranch/sidecar.json
printf '%s\n' "$sidecar_key" >"$work/enrollment.key"
sudo install -m 0600 -o happyranch -g happyranch "$work/enrollment.key" /etc/happyranch/enrollment.key
sudo env "PATH=$PATH" uv run python - "$PACKAGE_TAR" <<'PY'
import sys
from pathlib import Path
from runtime.remote_access.linux_package import install_linux_package
install_linux_package(Path(sys.argv[1]), Path('/'))
PY
sudo systemctl daemon-reload

# proof: startup_failure_cleanup
sudo mv /etc/happyranch/enrollment.key /etc/happyranch/enrollment.key.held
sudo systemctl start happyranch-managed.target || true
sleep 2
sudo systemctl stop happyranch-managed.target
! active happyranch-tsnet-sidecar.service || fail "sidecar survived missing-credential startup"
! port_open 443 || fail "tailnet listener survived failed startup"
absent /var/lib/happyranch-tsnet-sidecar/credential.consumed
[[ "$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)" == 0 ]] || fail "sidecar process survived failed startup"
sudo mv /etc/happyranch/enrollment.key.held /etc/happyranch/enrollment.key

# proof: startup_expected_peers
sudo systemctl start happyranch-managed.target
wait_for "connector READY" active happyranch-connector.service
wait_for "sidecar READY and ExpectedPeers" active happyranch-tsnet-sidecar.service
connector_ready="$(systemctl show happyranch-connector.service -p ActiveEnterTimestampMonotonic --value)"
sidecar_ready="$(systemctl show happyranch-tsnet-sidecar.service -p ActiveEnterTimestampMonotonic --value)"
[[ "$connector_ready" -le "$sidecar_ready" ]] || fail "sidecar admitted before connector"
[[ "$(sudo stat -c %a /run/credentials/happyranch-tsnet-sidecar.service/enrollment.key)" == 400 ]] || fail "systemd credential is not 0400"
absent /etc/happyranch/enrollment.key

# proof: automatic_restart
old_pid="$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)"
sudo kill -KILL "$old_pid"
wait_for "automatic Restart=" bash -c "test \"\$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)\" != '$old_pid' && systemctl is-active --quiet happyranch-tsnet-sidecar.service"
# proof: concurrent_stop_orderings
sudo systemctl restart happyranch-tsnet-sidecar.service & restart_job=$!
sudo systemctl stop happyranch-managed.target; wait "$restart_job" || true
! active happyranch-tsnet-sidecar.service || fail "concurrent stop lost"
sudo systemctl start happyranch-managed.target & start_job=$!
sudo systemctl stop happyranch-managed.target; wait "$start_job" || true
! active happyranch-tsnet-sidecar.service || fail "concurrent start defeated stop"
# proof: repeated_shutdown
sudo systemctl stop happyranch-managed.target
sudo systemctl stop happyranch-managed.target
! port_open 18443 || fail "listener survived repeated shutdown"

sudo systemctl start happyranch-managed.target
wait_for "fresh recovery" active happyranch-tsnet-sidecar.service
# proof: readiness_loss
sudo systemctl stop happyranch-connector.service
wait_for "BindsTo readiness loss" bash -c '! systemctl is-active --quiet happyranch-tsnet-sidecar.service'
! port_open 18443 || fail "admission remained after connector loss"

# proof: upgrade_recovery -- exact-package reinstall is the upgrade/re-entry boundary.
sudo env "PATH=$PATH" uv run python - "$PACKAGE_TAR" <<'PY'
import sys
from pathlib import Path
from runtime.remote_access.linux_package import install_linux_package
install_linux_package(Path(sys.argv[1]), Path('/'))
PY
absent /.happyranch-install-transaction.json
absent /.happyranch-backup
absent /.happyranch-units-backup
sudo systemctl daemon-reload
sudo systemctl start happyranch-managed.target
wait_for "upgrade recovery" active happyranch-tsnet-sidecar.service
echo N3_REAL_SYSTEMD_PASS
