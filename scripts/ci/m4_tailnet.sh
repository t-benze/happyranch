#!/usr/bin/env bash
# Authorized disposable HK VM only; invoked by m4_tailnet_aliyun.py.
set -euo pipefail
base=/var/lib/happyranch-m4
M2_BUILD=$base/build
M2_WORK=$base/cell
M2_LOGS=$base/logs
cert_cache=$base/acme-cache
MANAGED_DOWNLOADS=/var/lib/happyranch-m3/downloads
# shellcheck source=scripts/ci/m2_tailnet_daemon.sh
source "$(dirname "${BASH_SOURCE[0]}")/m2_tailnet_daemon.sh"
fixtures=(happyranch-m4-headscale.service happyranch-m4-memory.service happyranch-m4-daemon.service)
fail() { echo "M4: $*" >&2; exit 1; }
[[ $EUID == 0 && ${M4_AUTHORIZED_VM:-} == yes ]] || fail 'requires the authorized disposable VM and root'
start_fixture() {
  local unit=$1
  shift
  systemd-run --unit="$unit" --property=MemoryAccounting=yes --property=MemoryMax=600M "$@"
}
hs() { "$M2_WORK/headscale" --config "$M2_WORK/config.yaml" "$@"; }
relay_observation() {
  "$python" - "$1" <<'PY_RELAY'
import json, sys
sys.path.insert(0, 'scripts/ci')
from m4_tailnet_control import guest_relay_identity, snapshot_relay_launch
try:
    if sys.argv[1] == 'snapshot':
        snapshot_relay_launch()
    else:
        print(json.dumps(guest_relay_identity(initial=sys.argv[1] == 'initial')))
except Exception:
    raise SystemExit('M4 runtime relay identity refused; private details withheld') from None
PY_RELAY
}
certificate_cache() {
  /usr/bin/python3 - "$cert_cache" "$1" <<'PY_CACHE'
import json, os, stat, subprocess, sys
from pathlib import Path
root, action = Path(sys.argv[1]), sys.argv[2]
assert root.is_dir() and not root.is_symlink(), 'retained cache required; new issuance forbidden'
for p in [root, *root.iterdir()]:
    st = p.lstat()
    assert st.st_uid == 0 and not p.is_symlink(), 'unsafe certificate cache owner/type'
    assert (p == root and stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o700) or (p != root and stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and stat.S_IMODE(st.st_mode) == 0o600 and st.st_size <= 131072), 'unsafe certificate cache mode/type'
ledger = root / 'issuance.json'
state = json.loads(ledger.read_text()) if ledger.exists() else {'prior_issuances': 2, 'reserved_new_issuances': 0}
assert set(state) == {'prior_issuances', 'reserved_new_issuances'} and type(state['prior_issuances']) is int and state['prior_issuances'] == 2 and type(state['reserved_new_issuances']) is int and state['reserved_new_issuances'] in (0, 1), 'invalid issuance ledger'
certificate = root / '47-238-219-200.sslip.io'
usable = False
if certificate.exists():
    usable = subprocess.run(['openssl', 'x509', '-checkend', '86400', '-noout'], input=certificate.read_bytes(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
assert state == {'prior_issuances': 2, 'reserved_new_issuances': 1}, 'expected issuance ledger 3; no new issuance'
assert usable, 'cached certificate unusable; STOP without issuance'
if action == 'remove':
    for p in root.iterdir():
        p.unlink()
    root.rmdir()
    print('Explicit final decommission: certificate cache removed')
else:
    print(json.dumps(dict(cache='root-only ACME allowed residue', **state, usable_certificate=usable,
                          issuance_count=2 + int(usable and state['reserved_new_issuances'] == 1))))
PY_CACHE
}
prepare() {
  clean_state
  git rev-parse HEAD > "$base/owned-sha"
  mkdir -p "$M2_LOGS" "$M2_BUILD"
  date -u '+%s.%6N' > "$M2_LOGS/journal-since.epoch"
  sha256sum /etc/hosts /etc/resolv.conf > "$M2_BUILD/dns-before.sha256"
  [[ $(. /etc/os-release; echo "$ID:$VERSION_ID") == ubuntu:24.04 ]] || fail 'requires Ubuntu 24.04'
  cat > "$M2_BUILD/memory.py" <<'PY_MEMORY'
import json, sys, time
from pathlib import Path
peak = {'sampling_interval_seconds': 1, 'samples': 0, 'peak_used_bytes': 0, 'peak_swap_used_bytes': 0}
while True:
    fields = {k: int(v.split()[0])*1024 for k, v in (line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())}
    peak['samples'] += 1
    peak['total_bytes'] = fields['MemTotal']
    peak['swap_total_bytes'] = fields['SwapTotal']
    peak['peak_used_bytes'] = max(peak['peak_used_bytes'], fields['MemTotal']-fields['MemAvailable'])
    peak['peak_swap_used_bytes'] = max(peak['peak_swap_used_bytes'], fields['SwapTotal']-fields['SwapFree'])
    Path(sys.argv[1]).write_text(json.dumps(peak)+'\n')
    time.sleep(1)
PY_MEMORY
  start_fixture "${fixtures[1]}" /usr/bin/python3 "$M2_BUILD/memory.py" "$M2_LOGS/memory.json"
  certificate_cache prepare
  provision_payload
  install -m 0644 "$M2_BUILD/fixture_daemon_probe.py" "$M2_WORK/fixture_daemon_probe.py"
  echo "d2cda0a5d748587f77c920a76cd1bf1ab429e5299ba5bc6b3dda90712721b45b  $MANAGED_DOWNLOADS/headscale" | sha256sum --check
  install -m 0755 "$MANAGED_DOWNLOADS/headscale" "$M2_WORK/headscale"
  /usr/bin/python3 - "$M2_BUILD" /etc/happyranch <<'PY_CONFIG'
import json, sys
from pathlib import Path
for root in map(Path, sys.argv[1:]):
    p = root / 'sidecar.json'
    c = json.loads(p.read_text())
    c.update(ControlURL='https://47-238-219-200.sslip.io', ExpectedPeer='synthetic-m4-outside')
    p.write_text(json.dumps(c))
PY_CONFIG
  cat > "$M2_WORK/config.yaml" <<EOF
server_url: https://47-238-219-200.sslip.io
listen_addr: 0.0.0.0:443
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
tls_letsencrypt_hostname: ""
tls_cert_path: $cert_cache/47-238-219-200.sslip.io
tls_key_path: $cert_cache/47-238-219-200.sslip.io
dns:
  magic_dns: false
  base_domain: ci.invalid
policy:
  mode: file
  path: $M2_WORK/acl.json
derp:
  server:
    enabled: true
    region_id: 999
    region_code: m4
    region_name: HappyRanch M4 private relay
    ipv4: 47.238.219.200
    stun_listen_addr: 0.0.0.0:3478
    private_key_path: $M2_WORK/derp.key
    automatically_add_embedded_derp_region: true
  urls: []
  paths: []
  auto_update_enabled: false
log:
  format: text
  level: info
EOF
  # Explicit direction and one TCP port; no allow-all default or client tags.
  printf '%s\n' '{"acls":[{"action":"accept","proto":"tcp","src":["m4client"],"dst":["m4home:443"]}]}' > "$M2_WORK/acl.json"
  relay_observation snapshot
  start_fixture "${fixtures[0]}" "$M2_WORK/headscale" serve --config "$M2_WORK/config.yaml"
  # Static cached combined PEM certificate/key: no ACME manager or issuance.
  wait_for curl -q --silent --fail --connect-timeout 2 --max-time 4 --output /dev/null https://47-238-219-200.sslip.io/health
  relay_observation initial
  certificate_cache check
  hs users create m4home
  hs users create m4client
  bash -euo pipefail -c 'umask 077; "$1/headscale" --config "$1/config.yaml" preauthkeys create --user m4home --reusable=false --expiration 10m | tr -d "\r\n" > "$1/enrollment.key"' _ "$M2_WORK"
  provision_daemon
  echo 'Prepared public TLS/DERP and real loopback daemon; outside peer must join before install'
}
install_product() {
  touch "$M2_WORK/product-install-attempted"
  installer install --enrollment-key-file "$M2_WORK/enrollment.key"
  ready
  absent /etc/happyranch/enrollment.key
  absent /etc/credstore/happyranch-tsnet-enrollment
  test -f /var/lib/happyranch-tsnet-sidecar/credential.consumed
}
handoff() {
  # Plaintext stays in this process/pipe; only RSA-OAEP ciphertext is returned.
  /usr/bin/python3 - "$1" "$M2_WORK" "$python" <<'PY_HANDOFF'
import base64, subprocess, sys
from pathlib import Path
kind, root, python = sys.argv[1:]
if kind == 'enroll':
    command = [root+'/headscale', '--config', root+'/config.yaml', 'preauthkeys', 'create', '--user', 'm4client', '--reusable=false', '--expiration', '10m']
else:
    assert kind in ('pair-main', 'pair-revoked', 'pair-ready')
    command = ['sudo', '-u', 'happyranch', python, '-m', 'runtime.remote_access.cli', 'pair', '--config', '/etc/happyranch/connector.json', '--device', kind]
p = subprocess.run(command, capture_output=True, check=False)
if p.returncode:
    raise SystemExit('M4 credential ceremony failed')
secret = p.stdout.strip() if kind == 'enroll' else p.stdout.splitlines()[0].split(b': ', 1)[1]
result = subprocess.run(['openssl', 'pkeyutl', '-encrypt', '-pubin', '-inkey', root+'/recipient.pem', '-pkeyopt', 'rsa_padding_mode:oaep', '-pkeyopt', 'rsa_oaep_md:sha256'], input=secret, capture_output=True, check=False)
if result.returncode:
    raise SystemExit('M4 encrypted handoff failed')
print(base64.b64encode(result.stdout).decode())
PY_HANDOFF
}
handoff_check() {
  /usr/bin/python3 - "$M2_WORK" "$python" <<'PY_HANDOFF_CHECK'
import base64, json, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
root, python = Path(sys.argv[1]), sys.argv[2]
config = json.loads(Path('/etc/happyranch/connector.json').read_text())
proof = root / 'handoff-check'
proof.mkdir(mode=0o700)
config['state_path'] = str(proof / 'trust.json')
(proof / 'config.json').write_text(json.dumps(config))
commands = {
    'enroll': [str(root/'headscale'), '--config', str(root/'config.yaml'), 'preauthkeys', 'create', '--user', 'm4client', '--reusable=false', '--expiration', '10m'],
    'pair-test': [python, '-m', 'runtime.remote_access.cli', 'pair', '--config', str(proof/'config.json'), '--device', 'vm-handoff-test'],
}
result = {}
for name, command in commands.items():
    generated = subprocess.run(command, capture_output=True, check=False)
    if generated.returncode:
        raise SystemExit('VM handoff-check ceremony failed')
    if name == 'enroll':
        plaintext = generated.stdout.strip()
    else:
        lines = generated.stdout.splitlines()
        plaintext = lines[0].split(b': ', 1)[1]
        expiry = datetime.fromisoformat(lines[1].split(b': ', 1)[1].decode())
        assert 0 < (expiry-datetime.now(timezone.utc)).total_seconds() <= 300
    encrypted = subprocess.run(['openssl', 'pkeyutl', '-encrypt', '-pubin', '-inkey', str(root/'recipient.pem'), '-pkeyopt', 'rsa_padding_mode:oaep', '-pkeyopt', 'rsa_oaep_md:sha256'], input=plaintext, capture_output=True, check=False)
    if encrypted.returncode:
        raise SystemExit('VM handoff-check encryption failed')
    result[name] = base64.b64encode(encrypted.stdout).decode()
print(json.dumps(result))
PY_HANDOFF_CHECK
}
exposure_sockets() {
  /usr/bin/python3 - <<'PY_SOCKETS'
import json, subprocess
checked = []
for row in subprocess.check_output(['ss', '-H', '-lnt'], text=True).splitlines():
    address, port = row.split()[3].rsplit(':', 1)
    if port in {'18765', '18443', '8765'}:
        assert address in {'127.0.0.1', '[::1]'}, 'public forbidden listener'
        checked.append(int(port))
assert {18765, 18443} <= set(checked), 'missing real daemon/connector loopback controls'
print(json.dumps(dict(public_forbidden_listeners=0, loopback_ports=sorted(checked))))
PY_SOCKETS
}

nodes() {
  hs nodes list --output json | /usr/bin/python3 -c '
import ipaddress, json, sys
rows = json.load(sys.stdin)
if rows is None:
    rows = []
assert isinstance(rows, list), "expected Headscale node list or null"
safe = []
for r in rows:
    safe.append(dict(name=r["given_name"], ips=[ip for ip in r["ip_addresses"] if ipaddress.ip_address(ip).version == 4]))
print(json.dumps(safe))'
}
journal_attribution() {
  /usr/bin/python3 - "$M2_LOGS" "$M2_WORK/product-install-attempted" <<'PY_ATTR' > "$M2_LOGS/journal-attribution.json"
import json, re, sys
from decimal import Decimal
from pathlib import Path
root = Path(sys.argv[1])
boundary = root.joinpath('journal-since.epoch').read_text().strip()
assert re.fullmatch(r'[0-9]+\.[0-9]{6}', boundary), 'invalid run boundary'
files = sorted(root.glob('*.journal.txt'))
assert files, 'no journals'
report = {'boundary_epoch': boundary, 'journals': []}
for p in files:
    stamps = []
    for number, line in enumerate(p.read_text().splitlines(), 1):
        m = re.match(r'^([0-9]+\.[0-9]{6}) ', line)
        if m:
            assert Decimal(m[1]) >= Decimal(boundary), f'pre-boundary journal: {p.name}:{number}'
            stamps.append(m[1])
        elif re.fullmatch(r'-- .* --', line) or (line[:1].isspace() and stamps):
            continue
        else:
            raise AssertionError(f'unrecognized journal: {p.name}:{number}')
    report['journals'].append(dict(file=p.name, entries=len(stamps)))
required = ['happyranch-m4-headscale.service']
if Path(sys.argv[2]).exists():
    required += ['happyranch-tsnet-sidecar.service', 'happyranch-connector.service']
assert all(any(r['file'] == unit+'.journal.txt' and r['entries'] for r in report['journals']) for unit in required)
print(json.dumps(report, indent=2))
PY_ATTR
}
collect_logs() {
  M2_JOURNAL_SINCE=@$(cat "$M2_LOGS/journal-since.epoch")
  if [[ -f $M2_WORK/product-install-attempted ]]; then
    logs
  else
    assert_product_not_installed
    local unit
    for unit in "${fixtures[@]}"; do
      journalctl --no-pager --since "$M2_JOURNAL_SINCE" --output short-unix -u "$unit" > "$M2_LOGS/$unit.journal.txt"
    done
    echo 'not applicable: product not installed' | tee "$M2_LOGS/product-logs-state.txt"
  fi
  journal_attribution
  sha256sum --check "$M2_BUILD/dns-before.sha256"
  systemctl show "${services[@]}" "${fixtures[@]}" -p Id,MemoryPeak,MemorySwapPeak > "$M2_LOGS/service-memory.txt"
}
assert_product_not_installed() {
  local unit path
  absent "$M2_WORK/product-install-attempted"
  for unit in "${services[@]}" "$target"; do
    absent "/etc/systemd/system/$unit"
    absent "/etc/systemd/system/multi-user.target.wants/$unit"
    [[ $(systemctl show "$unit" -p LoadState --value) == not-found ]] || fail "product was loaded: $unit"
    ! systemctl is-active --quiet "$unit" || fail "product was active: $unit"
  done
  for path in /var/lib/happyranch-{connector,tsnet-sidecar} /run/happyranch-{connector,tsnet-sidecar} \
    /etc/credstore/happyranch-tsnet-enrollment; do absent "$path"; done
}
purge() {
  if [[ -f $M2_WORK/product-install-attempted ]]; then
    installer uninstall --purge
    residue
  else
    assert_product_not_installed
    echo 'not applicable: product not installed' | tee "$M2_LOGS/product-purge-state.txt"
  fi
}
teardown_vm() {
  # Partial prepares can be cleaned, but a surviving installed product refuses.
  local unit
  for unit in "${services[@]}" "$target"; do
    absent "/etc/systemd/system/$unit"
    ! systemctl is-active --quiet "$unit" || fail "product still active: $unit"
  done
  absent /var/lib/happyranch-connector
  absent /var/lib/happyranch-tsnet-sidecar
  for unit in "${fixtures[@]}"; do
    if systemctl is-active --quiet "$unit"; then systemctl stop "$unit"; fi
    systemctl reset-failed "$unit" 2>/dev/null || true
  done
  systemctl daemon-reload
  # M4 installs no CA; remove inherited synthetic CA source and generated links.
  rm -f /usr/local/share/ca-certificates/happyranch-m2.crt
  update-ca-certificates --fresh
  rm -rf "$M2_WORK" /opt/happyranch /etc/happyranch /var/log/happyranch-connector /var/log/happyranch-tsnet-sidecar
  if getent passwd happyranch >/dev/null; then userdel happyranch; fi
  if getent group happyranch >/dev/null; then groupdel happyranch; fi
  if [[ -f $M2_BUILD/dns-before.sha256 ]]; then sha256sum --check "$M2_BUILD/dns-before.sha256"; fi
  rm -f "$base/owned-sha"
}
clean_state() {
  local path unit
  for unit in "${services[@]}" "$target" "${fixtures[@]}"; do
    absent "/etc/systemd/system/$unit"
    absent "/etc/systemd/system/multi-user.target.wants/$unit"
    [[ $(systemctl show "$unit" -p LoadState --value) == not-found ]] || fail "still loaded: $unit"
    ! systemctl is-active --quiet "$unit" || fail "still active: $unit"
  done
  for path in "$base/owned-sha" "$M2_WORK" /opt/happyranch /etc/happyranch \
    /var/lib/happyranch-{connector,tsnet-sidecar} /run/happyranch-{connector,tsnet-sidecar} \
    /etc/credstore/happyranch-tsnet-enrollment /run/credentials/happyranch-{tsnet-sidecar,connector}.service \
    /usr/local/share/ca-certificates/happyranch-m2.crt /var/log/happyranch-{connector,tsnet-sidecar}; do absent "$path"; done
  ! getent passwd happyranch >/dev/null || fail 'test account remains'
  ! getent group happyranch >/dev/null || fail 'test group remains'
  /usr/bin/python3 - <<'PY_CLEAN'
import os, subprocess
from pathlib import Path
for p in Path('/etc/ssl/certs').iterdir():
    references = [str(p)] + ([os.readlink(p), str(p.resolve(strict=False))] if p.is_symlink() else [])
    assert not any('happyranch-m2' in s or 'happyranch-m4' in s for s in references), 'synthetic CA link remains'
rows = subprocess.check_output(['ss', '-H', '-lntu'], text=True).splitlines()
assert not any(row.split()[4].rsplit(':', 1)[-1] in {'443', '3478', '18765', '18443', '8765'} for row in rows), 'test listener remains'
# The VM's pre-existing sshd/DHCP sockets stay unchanged. Its SG exposes
# only443/tcp and3478/udp; neither test endpoint may survive teardown.
print('No public test listeners, daemon/connector sockets, synthetic CA or dangling CA links')
PY_CLEAN
  certificate_cache check
  echo "PASS clean-state boot=$(cat /proc/sys/kernel/random/boot_id)"
}
case ${1:-} in
  prepare) prepare ;;
  install) install_product ;;
  enroll|pair-main|pair-revoked|pair-ready) handoff "$1" ;;
  handoff-check) handoff_check ;;
  relay-identity) relay_observation current ;;
  exposure-sockets) exposure_sockets ;;
  devices) sudo -u happyranch "$python" -m runtime.remote_access.cli list-devices --config /etc/happyranch/connector.json ;;
  nodes) nodes ;;
  revoke) sudo -u happyranch "$python" -m runtime.remote_access.cli revoke --config /etc/happyranch/connector.json --device pair-revoked ;;
  logs) collect_logs ;;
  attribution) journal_attribution ;;
  purge) purge ;;
  teardown) teardown_vm ;;
  clean-state) clean_state ;;
  remove-cert-cache) clean_state; certificate_cache remove ;;
  *) fail 'expected prepare|install|enroll|pair-main|pair-revoked|pair-ready|handoff-check|relay-identity|exposure-sockets|devices|nodes|revoke|logs|attribution|purge|teardown|clean-state|remove-cert-cache' ;;
esac
