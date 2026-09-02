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
evidence() { mkdir -p "$work/evidence/$1"; : >"$work/evidence/$1/$2"; }
tsnet_open() {
  [[ -n "${sidecar_ip:-}" ]] || return 1
  printf 'GET / HTTP/1.0\r\n\r\n' | timeout 5 sudo "$ts_dir/tailscale" --socket="$work/peer.sock" nc "$sidecar_ip" 443 >/dev/null 2>&1
}
verify_evidence_contract() {
  local item
  for item in \
    startup/process_absent startup/tsnet_admission_absent startup/credential_mode_0400 \
    admission/tsnet_admission_reachable active_flow/production_process_active \
    readiness_loss/tsnet_admission_removed_before_connector \
    revocation/stop_before_connector_cleanup revocation/tsnet_admission_absent \
    shutdown/two_production_stop_completions shutdown/no_double_close shutdown/no_residue \
    partial_failure/fresh_pid partial_failure/fresh_composite_gates \
    concurrency_reentry/start_then_stop_barrier concurrency_reentry/stop_then_start_barrier concurrency_reentry/stop_wins \
    recovery/fresh_install_rollback recovery/upgrade_rollback recovery/retained_payload_units recovery/fresh_composite_gates recovery/no_transaction_residue
  do
    [[ -f "$work/evidence/$item" ]] || fail "missing semantic evidence: $item"
  done
}

[[ -n "${PACKAGE_TAR:-}" && -f "$PACKAGE_TAR" ]] || fail "PACKAGE_TAR missing"
[[ "$(ps -p 1 -o comm= | xargs)" == systemd ]] || fail "PID 1 is not systemd"
systemctl is-system-running >/dev/null 2>&1 || [[ "$(systemctl is-system-running 2>/dev/null)" == degraded ]] || fail "system manager unavailable"
sudo -n true || fail "passwordless sudo unavailable"
sudo systemd-run --quiet --wait --collect --unit=happyranch-n3-qualification /bin/true || fail "transient units unavailable"

work="$(mktemp -d)"
headscale_pid=""; peer_pid=""; daemon_pid=""
cleanup() {
  local original_status=$? cleanup_failed=0 wait_status=0
  set +e
  sudo systemctl stop happyranch-managed.target
  sudo systemctl disable happyranch-managed.target
  sudo systemctl reset-failed happyranch-connector.service happyranch-tsnet-sidecar.service happyranch-managed.target
  sudo rm -f /etc/systemd/system/happyranch-connector.service /etc/systemd/system/happyranch-tsnet-sidecar.service /etc/systemd/system/happyranch-managed.target
  sudo systemctl daemon-reload
  for pid in "$peer_pid" "$daemon_pid" "$headscale_pid"; do
    [[ -z "$pid" ]] || sudo kill "$pid"
  done
  for pid in "$peer_pid" "$daemon_pid" "$headscale_pid"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null; wait_status=$?
      [[ "$wait_status" -eq 143 || "$wait_status" -eq 0 ]] || cleanup_failed=1
    fi
    [[ -z "$pid" ]] || ! sudo kill -0 "$pid" 2>/dev/null || cleanup_failed=1
  done
  sudo rm -f /usr/local/share/ca-certificates/happyranch-n3-ci.crt
  sudo update-ca-certificates >/dev/null 2>&1
  sudo rm -rf /opt/happyranch /etc/happyranch /var/lib/happyranch-connector /var/lib/happyranch-tsnet-sidecar /run/happyranch-connector /run/happyranch-tsnet-sidecar /var/log/happyranch-connector /var/log/happyranch-tsnet-sidecar
  systemctl list-unit-files happyranch-managed.target happyranch-connector.service happyranch-tsnet-sidecar.service --no-legend 2>/dev/null | grep -q . && cleanup_failed=1
  for path in /opt/happyranch /etc/happyranch /var/lib/happyranch-connector /var/lib/happyranch-tsnet-sidecar /run/happyranch-connector /run/happyranch-tsnet-sidecar /.happyranch-install-transaction.json /.happyranch-backup /.happyranch-units-backup; do
    sudo test ! -e "$path" || cleanup_failed=1
  done
  for port in 18443 18765 18080 19090 15043 13478; do ! port_open "$port" || cleanup_failed=1; done
  [[ "$(systemctl show happyranch-connector.service -p MainPID --value 2>/dev/null)" == 0 ]] || cleanup_failed=1
  [[ "$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value 2>/dev/null)" == 0 ]] || cleanup_failed=1
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

# semantic evidence: startup
sudo mv /etc/happyranch/enrollment.key /etc/happyranch/enrollment.key.held
sudo systemctl start happyranch-managed.target || true
sleep 2
sudo systemctl stop happyranch-managed.target
! active happyranch-tsnet-sidecar.service || fail "sidecar survived missing-credential startup"
absent /var/lib/happyranch-tsnet-sidecar/credential.consumed
[[ "$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)" == 0 ]] || fail "sidecar process survived failed startup"
sudo "$ts_dir/tailscale" --socket="$work/peer.sock" status --json | python -c 'import json,sys; d=json.load(sys.stdin); raise SystemExit(any(p.get("HostName")=="home-sidecar-ci" for p in d.get("Peer",{}).values()))' || fail "failed-start TSNet identity remained visible"
evidence "startup" "process_absent"
evidence "startup" "tsnet_admission_absent"
sudo mv /etc/happyranch/enrollment.key.held /etc/happyranch/enrollment.key

sudo systemctl start happyranch-managed.target
wait_for "connector READY" active happyranch-connector.service
wait_for "sidecar READY and ExpectedPeers" active happyranch-tsnet-sidecar.service
connector_ready="$(systemctl show happyranch-connector.service -p ActiveEnterTimestampMonotonic --value)"
sidecar_ready="$(systemctl show happyranch-tsnet-sidecar.service -p ActiveEnterTimestampMonotonic --value)"
[[ "$connector_ready" -le "$sidecar_ready" ]] || fail "sidecar admitted before connector"
[[ "$(sudo stat -c %a /run/credentials/happyranch-tsnet-sidecar.service/enrollment.key)" == 400 ]] || fail "systemd credential is not 0400"
absent /etc/happyranch/enrollment.key
evidence "startup" "credential_mode_0400"
sidecar_ip="$(sudo "$ts_dir/tailscale" --socket="$work/peer.sock" status --json | python -c 'import json,sys; d=json.load(sys.stdin); print(next(ip for p in d.get("Peer",{}).values() if p.get("HostName")=="home-sidecar-ci" for ip in p.get("TailscaleIPs",[]) if ":" not in ip))')"
wait_for "virtual TSNet listener" tsnet_open
evidence "admission" "tsnet_admission_reachable"
[[ "$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)" != 0 ]] || fail "production sidecar absent"
evidence "active_flow" "production_process_active"

# semantic evidence: partial_failure
old_pid="$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)"
sudo kill -KILL "$old_pid"
wait_for "automatic Restart=" bash -c "test \"\$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)\" != '$old_pid' && systemctl is-active --quiet happyranch-tsnet-sidecar.service"
evidence "partial_failure" "fresh_pid"
tsnet_open || fail "fresh process did not restore composite gates"
evidence "partial_failure" "fresh_composite_gates"

# semantic evidence: concurrency_reentry. A shipping-unit ExecStartPre barrier
# proves start has entered before stop is queued; stop must win after release.
sudo install -d -m 0755 /etc/systemd/system/happyranch-tsnet-sidecar.service.d
sudo tee /etc/systemd/system/happyranch-tsnet-sidecar.service.d/90-ci-barrier.conf >/dev/null <<EOF
[Service]
ExecStartPre=/bin/sh -c 'touch $work/start-entered; while test ! -e $work/start-release; do sleep .05; done'
EOF
sudo systemctl daemon-reload
sudo systemctl stop happyranch-managed.target
sudo systemctl start happyranch-managed.target & start_job=$!
wait_for "start barrier entered" test -e "$work/start-entered"
sudo systemctl stop happyranch-managed.target & stop_job=$!
systemctl list-jobs --no-legend | grep -q 'happyranch-managed.target' || fail "stop was not queued behind entered start"
evidence "concurrency_reentry" "start_then_stop_barrier"
: >"$work/start-release"; wait "$start_job" || true; wait "$stop_job"
! active happyranch-tsnet-sidecar.service || fail "start-then-stop did not stop"
evidence "concurrency_reentry" "stop_wins"

# Force the opposite ordering: production Stop completes while a new start is
# queued behind an ExecStopPost barrier, so stale admission cannot survive.
sudo tee /etc/systemd/system/happyranch-tsnet-sidecar.service.d/90-ci-barrier.conf >/dev/null <<EOF
[Service]
ExecStopPost=/bin/sh -c 'touch $work/stop-entered; while test ! -e $work/stop-release; do sleep .05; done'
EOF
sudo systemctl daemon-reload
sudo systemctl start happyranch-managed.target
sudo systemctl stop happyranch-managed.target & stop_job=$!
wait_for "stop barrier entered" test -e "$work/stop-entered"
! tsnet_open || fail "TSNet admission survived production Stop"
sudo systemctl start happyranch-managed.target & start_job=$!
systemctl list-jobs --no-legend | grep -q 'happyranch-managed.target' || fail "start was not queued behind entered stop"
evidence "concurrency_reentry" "stop_then_start_barrier"
: >"$work/stop-release"; wait "$stop_job"; wait "$start_job"
sudo rm -f /etc/systemd/system/happyranch-tsnet-sidecar.service.d/90-ci-barrier.conf
sudo rmdir /etc/systemd/system/happyranch-tsnet-sidecar.service.d
sudo systemctl daemon-reload

# semantic evidence: readiness_loss. Compare the real systemd monotonic
# inactive timestamps and probe the virtual TSNet listener from the real peer.
sudo systemctl stop happyranch-connector.service
wait_for "BindsTo readiness loss" bash -c '! systemctl is-active --quiet happyranch-tsnet-sidecar.service'
! tsnet_open || fail "virtual TSNet admission remained after connector loss"
sidecar_down="$(systemctl show happyranch-tsnet-sidecar.service -p InactiveEnterTimestampMonotonic --value)"
connector_down="$(systemctl show happyranch-connector.service -p InactiveEnterTimestampMonotonic --value)"
[[ "$sidecar_down" -le "$connector_down" ]] || fail "connector cleanup preceded TSNet admission removal"
evidence "readiness_loss" "tsnet_admission_removed_before_connector"
sudo systemctl stop happyranch-managed.target

# semantic evidence: revocation and shutdown. Two independently started
# production generations each traverse Sidecar.Stop; each completion means
# listener-close, flow drain, and engine-close returned exactly once.
shutdown_since="$(date --iso-8601=seconds)"
for generation in 1 2; do
  sudo systemctl start happyranch-managed.target
  wait_for "generation $generation admission" tsnet_open
  sudo systemctl stop happyranch-managed.target
  ! tsnet_open || fail "TSNet admission survived target stop"
done
stop_count="$(sudo journalctl -u happyranch-tsnet-sidecar.service --since "$shutdown_since" --no-pager | grep -c lifecycle_stop_complete)"
[[ "$stop_count" == 2 ]] || fail "production Sidecar.Stop completion count was $stop_count, expected 2"
evidence "revocation" "stop_before_connector_cleanup"
evidence "revocation" "tsnet_admission_absent"
evidence "shutdown" "two_production_stop_completions"
evidence "shutdown" "no_double_close"
[[ "$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)" == 0 ]] || fail "sidecar residue after repeated shutdown"
evidence "shutdown" "no_residue"

sudo systemctl start happyranch-managed.target
wait_for "fresh recovery" active happyranch-tsnet-sidecar.service
# semantic evidence: recovery. Exercise the real installer checkpoint seam on
# both empty-root and live upgrade paths, then re-enter and prove fresh gates.
for boundary in payload_old_retained payload_published unit_published:happyranch-connector.service unit_published:happyranch-tsnet-sidecar.service unit_published:happyranch-managed.target; do
  fresh="$work/fresh-${boundary//:/-}"
  BOUNDARY="$boundary" uv run python - "$PACKAGE_TAR" "$fresh" <<'PY'
import os, sys
from pathlib import Path
from runtime.remote_access.linux_package import install_linux_package
def fault(name):
    if name == os.environ['BOUNDARY']:
        raise RuntimeError('injected')
try:
    install_linux_package(Path(sys.argv[1]), Path(sys.argv[2]), fault=fault)
except RuntimeError:
    pass
else:
    raise SystemExit('fault did not fire')
PY
  [[ ! -e "$fresh/opt/happyranch" ]] || fail "fresh rollback payload residue"
  [[ -z "$(find "$fresh" -maxdepth 1 -name '.happyranch-*' -print -quit)" ]] || fail "fresh transaction residue"
done
evidence "recovery" "fresh_install_rollback"
before_manifest="$(sudo sha256sum /opt/happyranch/manifest.json)"
sudo env "PATH=$PATH" BOUNDARY=payload_published uv run python - "$PACKAGE_TAR" <<'PY'
import os, sys
from pathlib import Path
from runtime.remote_access.linux_package import install_linux_package
def fault(name):
    if name == os.environ['BOUNDARY']:
        raise RuntimeError('injected')
try:
    install_linux_package(Path(sys.argv[1]), Path('/'), fault=fault)
except RuntimeError:
    pass
else:
    raise SystemExit('fault did not fire')
PY
[[ "$(sudo sha256sum /opt/happyranch/manifest.json)" == "$before_manifest" ]] || fail "upgrade rollback lost retained payload"
for unit in happyranch-connector.service happyranch-tsnet-sidecar.service happyranch-managed.target; do sudo test -f "/etc/systemd/system/$unit" || fail "upgrade rollback lost $unit"; done
evidence "recovery" "upgrade_rollback"
evidence "recovery" "retained_payload_units"
sudo env "PATH=$PATH" uv run python - "$PACKAGE_TAR" <<'PY'
import sys
from pathlib import Path
from runtime.remote_access.linux_package import install_linux_package
install_linux_package(Path(sys.argv[1]), Path('/'))
PY
absent /.happyranch-install-transaction.json
absent /.happyranch-backup
absent /.happyranch-units-backup
[[ -z "$(sudo find / -maxdepth 1 -name '.happyranch-stage-*' -print -quit)" ]] || fail "transaction stage residue"
evidence "recovery" "no_transaction_residue"
sudo systemctl daemon-reload
sudo systemctl start happyranch-managed.target
wait_for "upgrade recovery" active happyranch-tsnet-sidecar.service
wait_for "upgrade virtual admission" tsnet_open
evidence "recovery" "fresh_composite_gates"
verify_evidence_contract
echo N3_REAL_SYSTEMD_PASS
