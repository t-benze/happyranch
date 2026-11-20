#!/usr/bin/env bash
# Manual, disposable GitHub-hosted Ubuntu 24.04 acceptance only.
set -euo pipefail
M1_BUILD=${M1_BUILD:-${RUNNER_TEMP:-/tmp}/managed-m1-build}
M1_LOGS=${M1_LOGS:-${RUNNER_TEMP:-/tmp}/managed-m1-logs}
M1_WORK=/var/tmp/happyranch-m1
python=/opt/happyranch-desktop/python/bin/python3
helper=happyranch-remote-access-helper.service
fail() { echo "M1: $*" >&2; exit 1; }
admit() {
  [[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted ]] || fail 'requires a disposable GitHub-hosted runner'
  [[ $(uname -m) == x86_64 && $(ps -p 1 -o comm=) == systemd ]] || fail 'requires amd64 and systemd PID 1'
}
inspect() { sudo --preserve-env=GITHUB_ACTIONS,RUNNER_ENVIRONMENT "$python" -I -B "$PWD/scripts/ci/m1_home_checks.py" "$@"; }
call() { /usr/bin/python3 -I -B "$PWD/scripts/ci/m1_home_bus.py" "$@"; }
install_package() {
  admit
  local path
  for path in /opt/happyranch-desktop /etc/happyranch "$M1_WORK" "$HOME/.local/share/happyranch"; do
    [[ ! -e $path && ! -L $path ]] || fail "fixture destination exists: $path"
  done
  ! getent passwd happyranch >/dev/null || fail 'service account already exists'
  ! getent group happyranch >/dev/null || fail 'service group already exists'
  # Preserve the existing hosted fixture's safe /opt ancestor. Some runner
  # images make it group-writable; tightening it is not product provisioning.
  [[ ! -L /opt && $(stat -c '%u:%g' /opt) == 0:0 ]] || fail 'unsafe runner /opt owner/type'
  sudo chmod 0755 /opt
  mkdir -p "$M1_BUILD" "$M1_LOGS"
  bash packaging/linux/build-payload.sh --dist-dir "$M1_BUILD"
  local payloads=("$M1_BUILD"/*.tar.gz)
  [[ ${#payloads[@]} == 1 && -f ${payloads[0]} ]] || fail 'expected one payload'
  bash packaging/linux/build-deb.sh "${payloads[0]}" --dist-dir "$M1_BUILD"
  local packages=("$M1_BUILD"/*.deb)
  [[ ${#packages[@]} == 1 && -f ${packages[0]} ]] || fail 'expected one deb'
  sudo apt-get install -y "${packages[0]}"
  sudo install -d -m 0700 "$M1_WORK"
  sudo systemctl daemon-reload
  # Runner-only authentication simulation. Production policy is unchanged.
  sudo tee /etc/polkit-1/rules.d/00-happyranch-m1.rules >/dev/null <<EOF
polkit.addRule(function(action, subject) {
  var actions = ["enable", "disable", "replace-failed", "retry-policy-renewal"];
  if (subject.user === "$(id -un)" && actions.indexOf(action.id.replace("ai.happyranch.remote-access.", "")) >= 0 &&
      action.id.indexOf("ai.happyranch.remote-access.") === 0) return polkit.Result.YES;
});
EOF
  # This starts the unchanged packaged release unit under the runner's user
  # manager. No source-wheel daemon, replacement unit or drop-in is installed.
  sudo loginctl enable-linger "$(id -un)"
  sudo systemctl start "user@$(id -u).service"
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
  systemctl --user daemon-reload
  systemctl --user start happyranch-daemon.service
  local attempt
  for ((attempt=0; attempt<60; attempt++)); do
    [[ -s $HOME/.local/share/happyranch/daemon.port && -s $HOME/.local/share/happyranch/daemon.token ]] && return
    sleep 1
  done
  fail 'packaged release daemon did not publish discovery files'
}
off() { call Disable; inspect off; inspect uninstall-refusal; }
run() {
  install_package
  inspect clean
  echo 'CASE 1: clean deb -> public Enable -> exact READY'
  call Enable
  inspect ready
  echo 'CASE 2: real health.json plus numeric diagnostic -> immediate public Disable (I5e/I5f/I5g)'
  inspect health-diagnostics
  off
  echo 'CASE 6: exact Off retention, B6 absence and uninstall refusal'
  # Preserve independent cells root-only, outside all uploaded logs. Archiving
  # is fixture isolation, never evidence of product cleanup or uninstall.
  inspect archive-off
  echo 'CASE 3: recognized FAILED manual fragments -> ReplaceFailed -> off -> Enable -> READY'
  inspect failed-fixture
  local attempt
  for ((attempt=0; attempt<8; attempt++)); do
    call ReplaceFailed
    if inspect replacement-off; then break; fi
    inspect replacement-progress
  done
  ((attempt<8)) || fail 'replacement did not close in its bounded phases'
  call Enable
  inspect ready
  off
  inspect archive-off
  echo 'CASE 4: interrupt public clean Enable between preparation steps'
  call Enable --interrupt
  inspect interrupted
  call Enable
  inspect ready
  echo 'CASE 5: refused RetryPolicyRenewal -> cached-policy expiry closes access'
  # Test-side custody fault, never operator policy JSON/provenance. A runner
  # wall-clock jump expires the cached policy without changing its fixed TTL.
  inspect policy-fault
  call RetryPolicyRenewal
  inspect policy-refused
  inspect clock-expire
  inspect policy-closed
  inspect clock-restore
  inspect policy-restore
  call RetryPolicyRenewal
  inspect policy-renewed
  inspect recovered-ready
  off
  echo 'CASE 6 repeated: final exact Off and fixed uninstall refusal verified'
  echo 'All six M1 cases passed. Home-profile uninstall remains deferred.'
  echo 'Disposable authentication simulation does not prove a real desktop dialog.'
}
logs() {
  admit
  mkdir -p "$M1_LOGS"
  # Only explicitly nonsecret properties; no config/state/raw service streams.
  local unit
  for unit in "$helper" happyranch-{connector,tsnet-sidecar,headscale}.service \
    happyranch-managed.target happyranch-release-verifier.{socket,service} \
    happyranch-remote-access-policy-renew.{timer,service}; do
    systemctl show "$unit" -p Id,LoadState,ActiveState,SubState,MainPID,Result,ExecMainStatus,FragmentPath,DropInPaths,NeedDaemonReload \
      > "$M1_LOGS/$unit.properties.txt" || true
  done
}
teardown() {
  admit
  if [[ -x $python ]]; then inspect clock-restore; fi
  # Runner disposal owns retained home state; uninstall remains deferred.
  sudo rm -f /etc/polkit-1/rules.d/00-happyranch-m1.rules
  sudo systemctl stop "$helper" happyranch-managed.target happyranch-headscale.service \
    happyranch-remote-access-policy-renew.timer happyranch-remote-access-policy-renew.service || true
  export XDG_RUNTIME_DIR="/run/user/$(id -u)"
  export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
  systemctl --user stop happyranch-daemon.service || true
}
case ${1:-} in
  run) run ;;
  logs) logs ;;
  teardown) teardown ;;
  *) fail 'expected run, logs or teardown' ;;
esac
