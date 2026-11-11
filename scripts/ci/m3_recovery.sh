#!/usr/bin/env bash
# Disposable hosted Ubuntu only; systemd is the sole product supervisor.
set -euo pipefail
M2_BUILD=${M2_BUILD:-${RUNNER_TEMP:-/tmp}/managed-m3-build}
M2_LOGS=${M2_LOGS:-${RUNNER_TEMP:-/tmp}/managed-m3-logs}
# shellcheck source=scripts/ci/m2_tailnet_daemon.sh
source "$(dirname "${BASH_SOURCE[0]}")/m2_tailnet_daemon.sh"

step=0
fail() { echo "M3 step $step: $*" >&2; exit 1; }
property() { systemctl show "$1" -p "$2" --value; }
snapshot() {
  date -u '+%Y-%m-%dT%H:%M:%S.%NZ'
  systemctl show "${services[@]}" -p Id,Type,ActiveState,SubState,MainPID,NRestarts,Result,ExecMainStatus,Restart,RestartUSec,WatchdogUSec,StartLimitIntervalUSec,StartLimitBurst
}
step_journal() {
  sudo journalctl -b --no-pager -o short-precise --after-cursor "$cursor" \
    -u "${services[0]}" -u "${services[1]}" -u "${fixtures[2]}"
}
run_step() {
  local number=$1 operation=$2 cursor
  step=$number
  cursor=$(sudo journalctl -b -n 0 --show-cursor | sed -n 's/^-- cursor: //p')
  [[ -n $cursor ]] || fail 'could not capture the journal cursor'
  (
    set -E
    trap 'fail "command failed (exit $?); inspect this step log and unit journals"' ERR
    # Always retain before/after properties and journal evidence, including failure.
    trap 'rc=$?; echo "M3 step $step: final evidence (exit $rc)"; snapshot || true; step_journal || true; exit "$rc"' EXIT
    echo "M3 step $step: $operation"
    # reset-failed clears start-rate accounting AND NRestarts. Every start in
    # 300s counts, including earlier faults: isolate 1-4 and 6 before snapshots.
    # Step 5 deliberately exhausts the shipped 5/300s budget; never tune it.
    case $step in
      1|2|3|4|6) sudo systemctl reset-failed "${services[@]}" || fail 'could not reset fault accounting' ;;
    esac
    snapshot
    "$operation"
    echo "M3 step $step: passed"
  ) 2>&1 | tee "$M2_LOGS/step-$number.log"
}
both_ready() {
  local unit
  for unit in "${services[@]}"; do
    [[ $(property "$unit" Type) == notify && $(property "$unit" ActiveState) == active &&
       $(property "$unit" SubState) == running ]] || return 1
  done
}
recover() {
  local deadline=$1
  while (( SECONDS < deadline )); do
    if both_ready; then
      ready
      # Reuse M2's real pairing, 200/body and denial/revocation checks. No
      # service starts or restarts occur in these readiness/request helpers.
      request
      return
    fi
    sleep 1
  done
  fail 'both services did not return to notify-READY without help; inspect BindsTo and the journal'
}
assert_restarted() {
  local unit=$1 old_pid=$2 old_restarts=$3 new_pid new_restarts
  new_pid=$(property "$unit" MainPID)
  new_restarts=$(property "$unit" NRestarts)
  echo "$unit MainPID $old_pid -> $new_pid; NRestarts $old_restarts -> $new_restarts; Result=$(property "$unit" Result)"
  [[ $new_pid =~ ^[0-9]+$ && $new_restarts =~ ^[0-9]+$ ]] || fail 'invalid restart evidence'
  (( new_pid > 0 && new_pid != old_pid && new_restarts > old_restarts )) || fail "$unit did not restart automatically"
}
kill_and_recover() {
  local unit=$1 signal=$2 old_pid old_restarts deadline=$((SECONDS + 90))
  old_pid=$(property "$unit" MainPID)
  old_restarts=$(property "$unit" NRestarts)
  if [[ ! $old_pid =~ ^[0-9]+$ ]] || (( old_pid <= 1 )); then fail "$unit has no live MainPID"; fi
  sudo kill -"$signal" "$old_pid" || fail "could not send $signal to $unit"
  # Do nothing to the product units: even the SIGSTOP case relies solely on
  # WatchdogSec=30, TimeoutStopSec and systemd's automatic restart.
  while (( SECONDS < deadline )); do
    if [[ $(property "$unit" MainPID) != "$old_pid" ]] && both_ready; then
      echo "Automatic recovery observed with $((deadline - SECONDS))s remaining in the 90s bound"
      assert_restarted "$unit" "$old_pid" "$old_restarts"
      recover "$deadline"
      return
    fi
    sleep 1
  done
  fail "$unit did not restart with both services notify-READY within 90s; no manual recovery attempted"
}
connector_crash() { kill_and_recover "${services[0]}" 9; }

enrollment_absent() {
  local path
  for path in /etc/happyranch/enrollment.key /etc/credstore/happyranch-tsnet-enrollment \
    /run/credentials/happyranch-tsnet-sidecar.service/enrollment-key "$M2_WORK/enrollment.key"; do
    if ! sudo test ! -e "$path" || ! sudo test ! -L "$path"; then fail "enrollment input remains: $path"; fi
  done
  sudo test -f /var/lib/happyranch-tsnet-sidecar/credential.consumed || fail 'consumed-credential marker is missing'
  echo 'Enrollment input/source/credstore/runtime key absent; consumed-credential marker present'
}
headscale_snapshot() {
  local kind=$1
  if [[ $kind == nodes ]]; then
    sudo "$M2_WORK/headscale" --config "$M2_WORK/config.yaml" nodes list --output json
  else
    sudo "$M2_WORK/headscale" --config "$M2_WORK/config.yaml" preauthkeys list --user ci --output json
  fi | /usr/bin/python3 -c '
import json, sys
kind = sys.argv[1]
records = json.load(sys.stdin)
assert isinstance(records, list) and len(records) == 2, "expected exactly two fixture records"
if kind == "nodes":
    assert {r["given_name"] for r in records} == {"home-sidecar-ci", "synthetic-peer-ci"}, "wrong nodes"
    safe = [{"id": r["id"], "name": r["given_name"], "ips": sorted(r["ip_addresses"])} for r in records]
    assert all(r["id"] and r["ips"] for r in safe), "missing node identity"
else:
    # Raw key values never leave this pipe or enter logs/artifacts. All
    # initial single-use keys must already be used; compare IDs and usage.
    assert all(r.get("used") is True and not r.get("reusable", False) for r in records), "unused/reusable enrollment key"
    safe = [{"id": r["id"], "used": r["used"]} for r in records]
print(json.dumps(sorted(safe, key=lambda r: str(r["id"])), sort_keys=True))
' "$kind"
}
sidecar_crash() {
  local before_nodes before_keys after_nodes after_keys marker_before marker_after
  enrollment_absent
  before_nodes=$(headscale_snapshot nodes) || fail 'could not record baseline node IDs/IPs/count'
  before_keys=$(headscale_snapshot keys) || fail 'could not record consumed preauthkey IDs'
  marker_before=$(sudo sha256sum /var/lib/happyranch-tsnet-sidecar/credential.consumed)
  echo "Before nodes: $before_nodes; preauthkey metadata: $before_keys"
  kill_and_recover "${services[1]}" 9
  enrollment_absent
  after_nodes=$(headscale_snapshot nodes) || fail 'could not record recovered node IDs/IPs/count'
  after_keys=$(headscale_snapshot keys) || fail 'could not record recovered preauthkey usage'
  marker_after=$(sudo sha256sum /var/lib/happyranch-tsnet-sidecar/credential.consumed)
  echo "After nodes: $after_nodes; preauthkey metadata: $after_keys"
  [[ $before_nodes == "$after_nodes" ]] || fail 'sidecar created a new node or changed node ID/IP/count'
  [[ $before_keys == "$after_keys" ]] || fail 'new preauthkey created or used during restart'
  [[ $marker_before == "$marker_after" ]] || fail 'consumed-credential marker changed during restart'
  echo '#1376: saved node identity reused without an enrollment key or reinstall'
}
connector_watchdog() {
  kill_and_recover "${services[0]}" STOP
  sudo journalctl -b --no-pager --after-cursor "$cursor" -u "${services[0]}" \
    --grep='Watchdog timeout' | grep -F 'Watchdog timeout' || fail 'no watchdog timeout evidence for the stopped connector'
}
intentional_stop() {
  local unit before_connector before_sidecar
  before_connector=$(property "${services[0]}" NRestarts)
  before_sidecar=$(property "${services[1]}" NRestarts)
  sudo systemctl stop "$target" || fail 'intentional target stop failed'
  # PartOf stops can outlive the target job; observe completed inactive state.
  local deadline=$((SECONDS + 60))
  while :; do
    if [[ $(property "${services[0]}" ActiveState) == inactive && $(property "${services[1]}" ActiveState) == inactive ]]; then break; fi
    (( SECONDS < deadline )) || fail 'intentional stop did not leave both services inactive'
    sleep 1
  done
  # Fifteen seconds is clearly past the shipped RestartSec=1. Sample throughout.
  deadline=$((SECONDS + 15))
  while (( SECONDS < deadline )); do
    for unit in "${services[@]}"; do
      [[ $(property "$unit" ActiveState) == inactive && $(property "$unit" SubState) == dead && $(property "$unit" MainPID) == 0 ]] || fail "$unit restarted after intentional stop"
    done
    [[ $(property "${services[0]}" NRestarts) == "$before_connector" && $(property "${services[1]}" NRestarts) == "$before_sidecar" ]] || fail 'restart count changed after intentional stop'
    sleep 1
  done
  echo 'Both services stayed inactive with unchanged NRestarts for at least 15s'
  sudo systemctl start "$target" || fail 'explicit step 4 target start failed'
  recover "$((SECONDS + 90))"
}
crash_loop() {
  local unit=${services[0]} deadline=$((SECONDS + 120)) pid last_pid=0 kills=0 restarts
  # Never reset accounting or start anything until systemd gives up. Each
  # observed new process is killed once, including starts before notify-READY.
  while [[ $(property "$unit" ActiveState) != failed ]]; do
    (( SECONDS < deadline )) || fail 'connector did not reach its shipped start limit within 120s'
    pid=$(property "$unit" MainPID)
    if [[ $pid =~ ^[0-9]+$ ]] && (( pid > 1 && pid != last_pid )); then
      (( kills < 5 )) || fail 'connector exceeded five crash starts without start-limit-hit'
      echo "Crash $((kills + 1)): MainPID=$pid NRestarts=$(property "$unit" NRestarts)"
      sudo kill -9 "$pid" || fail 'could not crash connector during start-limit test'
      last_pid=$pid
      kills=$((kills + 1))
    fi
    sleep 0.1
  done
  [[ $(property "$unit" Result) == start-limit-hit ]] || fail 'connector failed for a reason other than start-limit-hit'
  restarts=$(property "$unit" NRestarts)
  echo "Start limit hit after $kills kills; NRestarts=$restarts"
  deadline=$((SECONDS + 15))
  while (( SECONDS < deadline )); do
    [[ $(property "$unit" ActiveState) == failed && $(property "$unit" Result) == start-limit-hit &&
       $(property "$unit" MainPID) == 0 && $(property "$unit" NRestarts) == "$restarts" ]] || fail 'connector did not stay failed after start-limit-hit'
    sleep 1
  done
  sudo systemctl reset-failed "${services[@]}" || fail 'explicit step 5 reset-failed failed'
  sudo systemctl start "${services[@]}" || fail 'explicit step 5 service start failed'
  recover "$((SECONDS + 90))"
}
daemon_restart() {
  local before_pid before_restarts after_pid after_restarts
  before_pid=$(property "${services[0]}" MainPID)
  before_restarts=$(property "${services[0]}" NRestarts)
  # Only the test-owned daemon fixture is restarted. Product recovery is automatic.
  sudo systemctl restart "${fixtures[2]}" || fail 'could not restart the test-owned daemon fixture'
  wait_for curl --silent --fail --connect-timeout 2 --max-time 3 --output /dev/null http://127.0.0.1:18765/api/v1/health
  recover "$((SECONDS + 90))"
  after_pid=$(property "${services[0]}" MainPID)
  after_restarts=$(property "${services[0]}" NRestarts)
  echo "Daemon fixture restarted without reinstall; connector MainPID $before_pid -> $after_pid; NRestarts $before_restarts -> $after_restarts"
  if [[ $before_pid == "$after_pid" ]]; then echo 'Connector kept the same process'; else echo 'Connector restarted automatically'; fi
}
purge() {
  M2_LOGS="$M2_LOGS/before-uninstall" logs
  installer uninstall --purge || fail 'uninstall --purge failed'
  residue
}
run() {
  mkdir -p "$M2_LOGS"
  (
    set -E
    trap 'fail "baseline command failed (exit $?); inspect step-0.log"' ERR
    provision
    installer install --enrollment-key-file "$M2_WORK/enrollment.key" || fail 'initial product install failed'
    ready
    snapshot
    local unit
    for unit in "${services[@]}"; do
      [[ $(property "$unit" Restart) == on-failure && $(property "$unit" RestartUSec) == 1s &&
         $(property "$unit" WatchdogUSec) == 30s && $(property "$unit" StartLimitIntervalUSec) == 5min &&
         $(property "$unit" StartLimitBurst) == 5 ]] || fail "$unit does not use the shipped 1s/30s/5-in-300s recovery limits"
    done
    request
    echo 'M3 step 0: baseline both notify-READY and paired real-daemon tailnet request 200'
  ) 2>&1 | tee "$M2_LOGS/step-0.log"
  run_step 1 connector_crash
  run_step 2 sidecar_crash
  run_step 3 connector_watchdog
  run_step 4 intentional_stop
  run_step 5 crash_loop
  run_step 6 daemon_restart
  run_step 7 purge
}

case ${1:-} in
  config) config ;;
  run) run ;;
  logs) logs ;;
  teardown) teardown ;;
  *) fail 'expected config, run, logs or teardown' ;;
esac
