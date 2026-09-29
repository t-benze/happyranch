#!/usr/bin/env bash
# CI-only, zero-skip proof of the packaged N3 production seam.
set -euo pipefail

HEADSCALE_VERSION=0.25.1
HEADSCALE_SHA256=d2cda0a5d748587f77c920a76cd1bf1ab429e5299ba5bc6b3dda90712721b45b
TAILSCALE_VERSION=1.102.3
TAILSCALE_SHA256=36ddd9b51be57ffc2990cf76323cfa13643bfbb1b8a969f6183fa164741cdef5
readonly HEADSCALE_VERSION HEADSCALE_SHA256 TAILSCALE_VERSION TAILSCALE_SHA256

fail() {
  local message="$1"
  echo "n3-real-systemd: $message" >&2
  exit 1
}
wait_for() {
  local label="$1"; shift
  for _attempt in $(seq 1 60); do "$@" && return 0; sleep 1; done
  fail "timeout waiting for $label"
}
port_open() { timeout 1 bash -c "</dev/tcp/127.0.0.1/$1" 2>/dev/null; }
active() { sudo systemctl is-active --quiet "$1"; }
absent() { ! sudo test -e "$1" || fail "residue at $1"; }
evidence() {
  python "$evidence_driver" observe "$evidence_artifact" --phase "$1" --observation "$2" --assertion-id "$run_id:$1:$2"
}
diagnostic() {
  local category="$1" phase="$2" actor="$3" unit="$4" diagnostic_id="${5:-$run_id:$1}"
  python "$evidence_driver" diagnose "$evidence_artifact" --id "$diagnostic_id" --category "$category" --phase "$phase" --actor "$actor" --unit "$unit"
}
tsnet_open() {
  [[ -n "${sidecar_ip:-}" ]] || return 1
  printf 'GET / HTTP/1.0\r\n\r\n' | timeout 5 sudo "$ts_dir/tailscale" --socket="$work/peer.sock" nc "$sidecar_ip" 443 >/dev/null 2>&1
}
systemctl_absent_value() {
  local unit="$1" property="$2" expected="$3" value status restore_errexit=0
  case "$-" in *e*) restore_errexit=1 ;; esac
  set +e
  value="$(systemctl show "$unit" -p "$property" --value 2>/dev/null)"
  status=$?
  (( restore_errexit )) && set -e || set +e
  [[ "$value" == "$expected" ]] || return 1
  # systemctl returns nonzero for a unit which is genuinely not loaded.  The
  # exact absent value is evidence for that exit only; empty/prose output and
  # every other query failure remain fatal.
  (( status == 0 || status == 1 || status == 4 )) || return 1
  (( status == 0 )) || [[ "$value" == not-found || "$value" == inactive || "$value" == dead || "$value" == 0 ]]
}
unit_absent() {
  local unit="$1" unit_root="${N3_UNIT_ROOT:-}"
  systemctl_absent_value "$unit" LoadState not-found || return 1
  systemctl_absent_value "$unit" ActiveState inactive || return 1
  systemctl_absent_value "$unit" SubState dead || return 1
  local main_pid status restore_errexit=0
  case "$-" in *e*) restore_errexit=1 ;; esac
  set +e
  main_pid="$(systemctl show "$unit" -p MainPID --value 2>/dev/null)"
  status=$?
  (( restore_errexit )) && set -e || set +e
  [[ -z "$main_pid" || "$main_pid" == 0 ]] || return 1
  (( status == 0 || status == 1 || status == 4 )) || return 1
  (( status == 0 )) || [[ "$main_pid" == 0 ]] || return 1
  [[ ! -e "$unit_root/etc/systemd/system/$unit" && ! -e "$unit_root/run/systemd/system/$unit" ]] || return 1
  [[ ! -e "$unit_root/etc/systemd/system/$unit.d" && ! -e "$unit_root/run/systemd/system/$unit.d" ]] || return 1
}
diagnostics="${N3_DIAGNOSTICS_DIR:-$(mktemp -d)}"
mkdir -p "$diagnostics"
printf 'subject=%s\n' "${PROOF_SUBJECT_SHA:-missing}" >"$diagnostics/bootstrap.txt"
[[ -n "${PACKAGE_TAR:-}" && -f "$PACKAGE_TAR" ]] || fail "PACKAGE_TAR missing"
[[ "${PROOF_SUBJECT_SHA:-}" =~ ^[0-9a-f]{40}$ ]] || fail "PROOF_SUBJECT_SHA missing"
[[ "$(ps -p 1 -o comm= | xargs)" == systemd ]] || fail "PID 1 is not systemd"
systemctl is-system-running >/dev/null 2>&1 || [[ "$(systemctl is-system-running 2>/dev/null)" == degraded ]] || fail "system manager unavailable"
sudo -n true || fail "passwordless sudo unavailable"
sudo systemd-run --quiet --wait --collect --unit=happyranch-n3-qualification /bin/true || fail "transient units unavailable"

work="$(mktemp -d)"
evidence_driver="$PWD/app/linux/package/n3_evidence.py"
failure_capture_driver="$PWD/app/linux/package/n3_failure_capture.py"
evidence_artifact="$diagnostics/execution-evidence.json"
run_id="$(cat /proc/sys/kernel/random/uuid)"
barrier_dir="/var/lib/happyranch-tsnet-sidecar/.n3-barrier-$run_id"
capture_window_since_us="$(date +%s%N 2>/dev/null | cut -c1-16 || printf 0)"
package_sha="$(sha256sum "$PACKAGE_TAR" | cut -d' ' -f1)"
python "$evidence_driver" init "$evidence_artifact" --git-head "$PROOF_SUBJECT_SHA" --package-sha256 "$package_sha" --run-id "$run_id"
headscale_pid=""; peer_pid=""; daemon_pid=""
safe_systemctl_value() {
  observe_systemctl_value "$1" "$2"
  printf '%s' "$observation_value"
}
observe_remaining() {
  # A one-second termination grace is part of the current section deadline.
  # Do not start an observation that cannot consume both its command slot and
  # that grace before the deadline.
  local remaining
  (( SECONDS < observe_deadline && observe_bytes_left > 0 )) || return 1
  remaining=$((observe_deadline - SECONDS))
  (( remaining > 2 )) || return 1
  observe_timeout_seconds=1
}
begin_observation_section() {
  # Every section receives a fresh bounded allowance. A timeout or truncation
  # can exhaust only this section and can never suppress a later section.
  observe_deadline=$((SECONDS + $1))
  observe_bytes_left="$2"
}
capture_now_us() {
  local value
  value="$(date +%s%N 2>/dev/null | cut -c1-16 || printf 0)"
  [[ "$value" =~ ^[0-9]{16}$ ]] || value=0
  printf '%s' "$value"
}
compact_boot_id() {
  local value
  value="$(tr -d '-' </proc/sys/kernel/random/boot_id 2>/dev/null | tr '[:upper:]' '[:lower:]' || printf unavailable)"
  [[ "$value" =~ ^[0-9a-f]{32}$ ]] || value=unavailable
  printf '%s' "$value"
}
observe_presence() {
  # A failed/late existence probe is unknown, never a false claim of absence.
  # Restore the caller's errexit state after the probe so a bounded capture can
  # never re-arm `set -e` under the EXIT handler and abort remaining teardown.
  local path="$1" kind="$2" status restore_errexit=0
  presence_value=false; presence_loss=observed_absent
  observe_remaining || { presence_value='"unknown"'; presence_loss=unattempted; return; }
  case "$-" in *e*) restore_errexit=1 ;; esac
  set +e
  timeout --kill-after=1 "$observe_timeout_seconds" sudo test "$kind" "$path"
  status=$?
  (( restore_errexit )) && set -e || set +e
  case "$status" in
    0) presence_value=true; presence_loss=observed_present ;;
    1) presence_value=false; presence_loss=observed_absent ;;
    124) presence_value='"unknown"'; presence_loss=timeout ;;
    *) presence_value='"unknown"'; presence_loss=query_error ;;
  esac
}
observe_systemctl_value() {
  # Each observation has a one-second deadline, one-second kill grace and a
  # 128-byte cap.  `observation_loss` is a closed term: it distinguishes a
  # successful empty response, failed query, real timeout, truncation, parse
  # loss and work which was not attempted. Raw command output is never kept.
  local unit="$1" property="$2" value file status bytes cap=128 restore_errexit=0
  observation_value=unknown; observation_loss=unattempted
  observe_remaining || return 0
  (( observe_bytes_left < cap )) && cap="$observe_bytes_left"
  file="$(mktemp "$diagnostics/.n3-observe.XXXXXX")" || { observation_loss=launch_failure; return; }
  case "$-" in *e*) restore_errexit=1 ;; esac
  set +e
  timeout --kill-after=1 "$observe_timeout_seconds" systemctl show "$unit" -p "$property" --value 2>/dev/null | head -c "$((cap + 1))" >"$file"
  status=${PIPESTATUS[0]}
  (( restore_errexit )) && set -e || set +e
  value="$(<"$file")"; bytes="$(wc -c <"$file")"; rm -f "$file"
  (( bytes > cap )) && { observe_bytes_left=0; observation_loss=truncated; return; }
  observe_bytes_left=$((observe_bytes_left - bytes))
  if (( status == 124 )); then observation_loss=timeout; return; fi
  if (( status != 0 )); then observation_loss=query_error; return; fi
  if [[ -z "$value" ]]; then observation_loss=empty; return; fi
  case "$property:$value" in
    ActiveState:active|ActiveState:inactive|ActiveState:failed|ActiveState:activating|ActiveState:deactivating|ActiveState:unknown|SubState:running|SubState:dead|SubState:failed|SubState:exited|SubState:waiting|Result:success|Result:exit-code|Result:signal|Result:timeout|Result:resources|Result:unknown)
      observation_value="$value"; observation_loss=observed ;;
    ExecMainStatus:*)
      if [[ "$value" =~ ^[0-9]{1,6}$ ]]; then observation_value="$value"; observation_loss=observed; else observation_loss=parse_loss; fi ;;
    # ExecStartPre contains argv/path text and must never become evidence prose.
    *) observation_loss=parse_loss ;;
  esac
}
capture_completed_jobs() {
  # Completed systemd job records remain in the journal after `systemctl
  # start` returns. Retain only closed structured fields attributed to this
  # boot and positive-start window; never retain MESSAGE or other prose.
  local journal_file helper_file field_file="" status bytes now_us field field_status field_bytes restore_errexit=0
  case "$-" in *e*) restore_errexit=1 ;; esac
  completed_jobs='[]'; jobs_loss=unattempted
  observe_remaining || return 0
  journal_file="$(mktemp "$diagnostics/.n3-jobs.XXXXXX")" || { jobs_loss=launch_failure; return; }
  set +e
  timeout --kill-after=1 "$observe_timeout_seconds" journalctl -b "$snapshot_boot_id" --since "@$snapshot_since_seconds" --output=json --output-fields=UNIT,JOB_ID,JOB_TYPE,JOB_RESULT,_BOOT_ID --no-pager JOB_TYPE=start UNIT=happyranch-managed.target UNIT=happyranch-connector.service UNIT=happyranch-tsnet-sidecar.service 2>/dev/null | head -c "$((observe_bytes_left + 1))" >"$journal_file"
  status=${PIPESTATUS[0]}
  (( restore_errexit )) && set -e || set +e
  bytes="$(wc -c <"$journal_file")"
  if (( bytes > observe_bytes_left )); then observe_bytes_left=0; jobs_loss=truncated; rm -f "$journal_file"; return; fi
  observe_bytes_left=$((observe_bytes_left - bytes))
  if (( status == 124 )); then rm -f "$journal_file"; jobs_loss=timeout; return; fi
  if (( status != 0 )); then rm -f "$journal_file"; jobs_loss=query_error; return; fi
  if (( bytes == 0 )); then rm -f "$journal_file"; jobs_loss=empty; return; fi
  now_us="$(capture_now_us)"
  (( now_us >= snapshot_since_us )) || { rm -f "$journal_file"; jobs_loss=parse_loss; return; }
  observe_remaining || { rm -f "$journal_file"; jobs_loss=unattempted; return; }
  helper_file="$(mktemp "$diagnostics/.n3-job-result.XXXXXX")" || { rm -f "$journal_file"; jobs_loss=launch_failure; return; }
  set +e
  timeout --kill-after=1 "$observe_timeout_seconds" python "$failure_capture_driver" --mode jobs --input "$journal_file" --boot-id "$snapshot_boot_id" --since-us "$snapshot_since_us" --until-us "$now_us" >"$helper_file" 2>/dev/null
  status=$?
  (( restore_errexit )) && set -e || set +e
  bytes="$(wc -c <"$helper_file")"
  if (( bytes > observe_bytes_left )); then observe_bytes_left=0; rm -f "$journal_file" "$helper_file"; jobs_loss=truncated; return; fi
  observe_bytes_left=$((observe_bytes_left - bytes))
  if (( status == 124 )); then rm -f "$journal_file" "$helper_file"; jobs_loss=timeout; return; fi
  if (( status != 0 )); then rm -f "$journal_file" "$helper_file"; jobs_loss=parse_loss; return; fi
  for field in jobs loss; do
    observe_remaining || { rm -f "$journal_file" "$helper_file" "$field_file"; jobs_loss=unattempted; return; }
    field_file="$(mktemp "$diagnostics/.n3-job-$field.XXXXXX")" || { rm -f "$journal_file" "$helper_file"; jobs_loss=launch_failure; return; }
    set +e
    timeout --kill-after=1 "$observe_timeout_seconds" python -c 'import json,sys; value=json.load(open(sys.argv[1]))[sys.argv[2]]; allowed={"observed","empty","parse_loss","attribution_loss"}; assert isinstance(value,list) if sys.argv[2]=="jobs" else value in allowed; print(json.dumps(value,separators=(",",":")) if isinstance(value,list) else value)' "$helper_file" "$field" >"$field_file" 2>/dev/null
    field_status=$?
    (( restore_errexit )) && set -e || set +e
    field_bytes="$(wc -c <"$field_file")"
    if (( field_bytes > observe_bytes_left )); then observe_bytes_left=0; rm -f "$journal_file" "$helper_file" "$field_file"; jobs_loss=truncated; return; fi
    observe_bytes_left=$((observe_bytes_left - field_bytes))
    if (( field_status == 124 )); then rm -f "$journal_file" "$helper_file" "$field_file"; jobs_loss=timeout; return; fi
    if (( field_status != 0 )); then rm -f "$journal_file" "$helper_file" "$field_file"; jobs_loss=parse_loss; return; fi
    if [[ "$field" == jobs ]]; then completed_jobs="$(<"$field_file")"; else jobs_loss="$(<"$field_file")"; fi
    rm -f "$field_file"
  done
  rm -f "$journal_file" "$helper_file"
}
capture_diagnostic_receipts() {
  # The sidecar is the sole diagnostic receipt producer. Journal records are
  # accepted only when their unit, invocation, boot, and collection window all
  # match this failing run; the Python helper emits no journal prose.
  local invocation_file journal_file helper_file field_file="" invocation status bytes now_us field_status field_bytes field restore_errexit=0
  case "$-" in *e*) restore_errexit=1 ;; esac
  diagnostic_receipts='[]'; diagnostic_receipt_loss='["unattempted"]'
  observe_remaining || return 0
  invocation_file="$(mktemp "$diagnostics/.n3-invocation.XXXXXX")" || { diagnostic_receipt_loss='["launch_failure"]'; return; }
  set +e
  timeout --kill-after=1 "$observe_timeout_seconds" systemctl show happyranch-tsnet-sidecar.service -p InvocationID --value 2>/dev/null | head -c 65 >"$invocation_file"
  status=${PIPESTATUS[0]}
  (( restore_errexit )) && set -e || set +e
  invocation="$(<"$invocation_file")"; bytes="$(wc -c <"$invocation_file")"; rm -f "$invocation_file"
  if (( bytes > 64 )); then observe_bytes_left=0; diagnostic_receipt_loss='["truncated"]'; return; fi
  observe_bytes_left=$((observe_bytes_left - bytes))
  if (( status == 124 )); then diagnostic_receipt_loss='["timeout"]'; return; fi
  if (( status != 0 )); then diagnostic_receipt_loss='["query_error"]'; return; fi
  if [[ -z "$invocation" ]]; then diagnostic_receipt_loss='["empty"]'; return; fi
  invocation="${invocation//-/}"
  [[ "$invocation" =~ ^[0-9a-fA-F]{32}$ ]] || { diagnostic_receipt_loss='["parse_loss"]'; return; }
  observe_remaining || { diagnostic_receipt_loss='["unattempted"]'; return; }
  journal_file="$(mktemp "$diagnostics/.n3-receipts.XXXXXX")" || { diagnostic_receipt_loss='["launch_failure"]'; return; }
  set +e
  timeout --kill-after=1 "$observe_timeout_seconds" journalctl -u happyranch-tsnet-sidecar.service -b "$snapshot_boot_id" --since "@$snapshot_since_seconds" --output=json --no-pager 2>/dev/null | head -c "$((observe_bytes_left + 1))" >"$journal_file"
  status=${PIPESTATUS[0]}
  (( restore_errexit )) && set -e || set +e
  bytes="$(wc -c <"$journal_file")"
  if (( bytes > observe_bytes_left )); then
    observe_bytes_left=0
    diagnostic_receipt_loss='["truncated"]'
    rm -f "$journal_file"
    return
  fi
  observe_bytes_left=$((observe_bytes_left - bytes))
  if (( status != 0 )); then
    rm -f "$journal_file"
    (( status == 124 )) && diagnostic_receipt_loss='["timeout"]' || diagnostic_receipt_loss='["query_error"]'
    return
  fi
  now_us="$(capture_now_us)"
  (( now_us >= snapshot_since_us )) || { rm -f "$journal_file"; diagnostic_receipt_loss='["parse_loss"]'; return; }
  observe_remaining || { rm -f "$journal_file"; diagnostic_receipt_loss='["unattempted"]'; return; }
  helper_file="$(mktemp "$diagnostics/.n3-receipt-result.XXXXXX")" || { rm -f "$journal_file"; diagnostic_receipt_loss='["launch_failure"]'; return; }
  set +e
  timeout --kill-after=1 "$observe_timeout_seconds" python "$failure_capture_driver" --input "$journal_file" --invocation-id "$invocation" --boot-id "$snapshot_boot_id" --since-us "$snapshot_since_us" --until-us "$now_us" >"$helper_file" 2>/dev/null
  status=$?
  (( restore_errexit )) && set -e || set +e
  bytes="$(wc -c <"$helper_file")"
  if (( bytes > observe_bytes_left )); then observe_bytes_left=0; rm -f "$journal_file" "$helper_file"; diagnostic_receipt_loss='["truncated"]'; return; fi
  observe_bytes_left=$((observe_bytes_left - bytes))
  if (( status == 124 )); then rm -f "$journal_file" "$helper_file"; diagnostic_receipt_loss='["timeout"]'; return; fi
  if (( status != 0 )); then rm -f "$journal_file" "$helper_file"; diagnostic_receipt_loss='["parse_loss"]'; return; fi
  for field in receipts losses; do
    observe_remaining || { rm -f "$journal_file" "$helper_file" "$field_file"; diagnostic_receipt_loss='["unattempted"]'; return; }
    field_file="$(mktemp "$diagnostics/.n3-receipt-$field.XXXXXX")" || { rm -f "$journal_file" "$helper_file"; diagnostic_receipt_loss='["launch_failure"]'; return; }
    set +e
    timeout --kill-after=1 "$observe_timeout_seconds" python -c 'import json,sys; value=json.load(open(sys.argv[1]))[sys.argv[2]]; assert isinstance(value,list); print(json.dumps(value,separators=(",",":")))' "$helper_file" "$field" >"$field_file" 2>/dev/null
    field_status=$?
    (( restore_errexit )) && set -e || set +e
    field_bytes="$(wc -c <"$field_file")"
    if (( field_bytes > observe_bytes_left )); then observe_bytes_left=0; rm -f "$journal_file" "$helper_file" "$field_file"; diagnostic_receipt_loss='["truncated"]'; return; fi
    observe_bytes_left=$((observe_bytes_left - field_bytes))
    if (( field_status == 124 )); then rm -f "$journal_file" "$helper_file" "$field_file"; diagnostic_receipt_loss='["timeout"]'; return; fi
    if (( field_status != 0 )); then rm -f "$journal_file" "$helper_file" "$field_file"; diagnostic_receipt_loss='["parse_loss"]'; return; fi
    if [[ "$field" == receipts ]]; then diagnostic_receipts="$(<"$field_file")"; else diagnostic_receipt_loss="$(<"$field_file")"; fi
    rm -f "$field_file"
  done
  rm -f "$journal_file" "$helper_file"
}
capture_failure_snapshot() {
  # Essential failure sections have independent bounded budgets. No observation
  # changes the exit status that entered cleanup; all retained values are closed.
  local name="${1:-failure-snapshot}" snapshot unit first=1 active sub result main pre source held marker dropin staging source_loss held_loss marker_loss dropin_loss staging_loss loss_first=1 losses_file snapshot_until_us
  local side_active side_sub side_result side_main side_active_loss side_sub_loss side_result_loss side_main_loss
  snapshot="$diagnostics/$name.json"
  losses_file="$(mktemp "$diagnostics/.n3-losses.XXXXXX")" || return 1
  snapshot_since_us="${capture_window_since_us:-$(capture_now_us)}"
  [[ "$snapshot_since_us" =~ ^[0-9]{16}$ ]] || snapshot_since_us="$(capture_now_us)"
  snapshot_since_seconds=$((snapshot_since_us / 1000000))
  snapshot_boot_id="$(compact_boot_id)"

  # Founder seq305 ordering: sidecar state, completed jobs, and attributed
  # sidecar receipt are collected before connector, target, or credentials.
  begin_observation_section 8 1024
  observe_systemctl_value happyranch-tsnet-sidecar.service ActiveState; side_active="$observation_value"; side_active_loss="$observation_loss"
  observe_systemctl_value happyranch-tsnet-sidecar.service SubState; side_sub="$observation_value"; side_sub_loss="$observation_loss"
  observe_systemctl_value happyranch-tsnet-sidecar.service Result; side_result="$observation_value"; side_result_loss="$observation_loss"
  observe_systemctl_value happyranch-tsnet-sidecar.service ExecMainStatus; side_main="$observation_value"; side_main_loss="$observation_loss"
  begin_observation_section 8 8192
  capture_completed_jobs
  begin_observation_section 8 8192
  capture_diagnostic_receipts

  {
    printf '{"schema":"happyranch.n3.failure-snapshot","version":1,"id":"%s","units":{' "$name"
    for unit in happyranch-tsnet-sidecar.service happyranch-connector.service happyranch-managed.target; do
      if [[ "$unit" == happyranch-tsnet-sidecar.service ]]; then
        active="$side_active"; active_loss="$side_active_loss"
        sub="$side_sub"; sub_loss="$side_sub_loss"
        result="$side_result"; result_loss="$side_result_loss"
        main="$side_main"; main_loss="$side_main_loss"
      else
        begin_observation_section 8 1024
        observe_systemctl_value "$unit" ActiveState; active="$observation_value"; active_loss="$observation_loss"
        observe_systemctl_value "$unit" SubState; sub="$observation_value"; sub_loss="$observation_loss"
        observe_systemctl_value "$unit" Result; result="$observation_value"; result_loss="$observation_loss"
        observe_systemctl_value "$unit" ExecMainStatus; main="$observation_value"; main_loss="$observation_loss"
      fi
      # ExecStartPre is argv/path prose and can never be admitted. Preserve the
      # existing snapshot key without spending observation budget on it.
      pre=unknown; pre_loss=not_collected
      (( first )) || printf ','; first=0
      printf '"%s":{"active":"%s","sub":"%s","result":"%s","exec_main_status":"%s","exec_start_pre_status":"%s"}' "$unit" "$active" "$sub" "$result" "$main" "$pre"
      for property_loss in "active:$active_loss" "sub:$sub_loss" "result:$result_loss" "exec_main_status:$main_loss" "exec_start_pre_status:$pre_loss"; do
        [[ "$property_loss" == *:observed ]] && continue
        (( loss_first )) || printf ',' >>"$losses_file"; loss_first=0
        printf '"%s.%s":"%s"' "$unit" "${property_loss%%:*}" "${property_loss#*:}" >>"$losses_file"
      done
    done
    begin_observation_section 5 512
    observe_presence /etc/happyranch/enrollment.key -e; source="$presence_value"; source_loss="$presence_loss"
    observe_presence /etc/happyranch/enrollment.key.held -e; held="$presence_value"; held_loss="$presence_loss"
    observe_presence /var/lib/happyranch-tsnet-sidecar/credential.consumed -e; marker="$presence_value"; marker_loss="$presence_loss"
    observe_presence /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf -e; dropin="$presence_value"; dropin_loss="$presence_loss"
    observe_presence /run/credentials/happyranch-tsnet-sidecar.service -d; staging="$presence_value"; staging_loss="$presence_loss"
    snapshot_until_us="$(capture_now_us)"
    (( snapshot_until_us >= snapshot_since_us )) || snapshot_until_us="$snapshot_since_us"
    printf '},"jobs":%s,"diagnostic_receipts":%s,"credential_presence":{"source":%s,"held_source":%s,"consumed_marker":%s,"transient_dropin":%s,"staged_directory":%s},"collection":{"run_id":"%s","boot_id":"%s","window_start_us":%s,"window_end_us":%s,"journal_since_epoch_seconds":%s,"window_seconds":45,"output_cap_bytes":19968,"budget_model":"reserved-sections","source":"systemctl-and-attributed-systemd-journals"},"observation_loss":{' "$completed_jobs" "$diagnostic_receipts" "$source" "$held" "$marker" "$dropin" "$staging" "${run_id:-unknown}" "$snapshot_boot_id" "$snapshot_since_us" "$snapshot_until_us" "$snapshot_since_seconds"
    for property_loss in "credential.source:$source_loss" "credential.held_source:$held_loss" "credential.consumed_marker:$marker_loss" "credential.transient_dropin:$dropin_loss" "credential.staged_directory:$staging_loss"; do
      [[ "$property_loss" == *:observed_present || "$property_loss" == *:observed_absent ]] && continue
      (( loss_first )) || printf ',' >>"$losses_file"; loss_first=0
      printf '"%s":"%s"' "${property_loss%%:*}" "${property_loss#*:}" >>"$losses_file"
    done
    cat "$losses_file"
    [[ "$jobs_loss" == observed ]] || { (( loss_first )) || printf ','; loss_first=0; printf '"jobs":"%s"' "$jobs_loss"; }
    (( loss_first )) || printf ','
    printf '"diagnostic_receipts":%s}}\n' "$diagnostic_receipt_loss"
  } >"$snapshot" || true
  rm -f "$losses_file"
}
start_managed_target() {
  # Keep the initiating positive-start status authoritative even when bounded
  # capture cannot run; EXIT cleanup preserves this same status in turn.
  # This is intentionally before the start invocation: a sidecar receipt can
  # be emitted before systemctl returns the failing positive-start status.
  capture_window_since_us="$(capture_now_us)"
  if sudo systemctl start happyranch-managed.target; then
    return 0
  else
    local start_status=$?
  fi
  capture_failure_snapshot first-positive-start-failure || true
  return "$start_status"
}
cleanup() {
  local original_status="${1:-$?}" cleanup_failed=0
  # EXIT, INT and TERM share exactly ONE teardown. The first invocation owns it
  # and saves the initiating status; a signal arriving while teardown is running
  # must not start a second pass or replace that saved status.
  if [[ -n "${cleanup_active:-}" ]]; then
    return
  fi
  cleanup_active=1
  set +e
  printf 'cleanup\n' >>"$diagnostics/cleanup-events.log"
  (( original_status == 0 )) || capture_failure_snapshot failure-before-teardown || true
  # Capture isolates its own option changes; never rely on that here. Teardown
  # must attempt its remaining work after an independent stop/disable/reset
  # failure, and only the saved initiating status may leave this handler.
  set +e
  # A trap can arrive while either real service barrier is held. Release both
  # controller-owned latches before stopping units so teardown cannot strand a
  # service in its existing ExecStartPre/ExecStopPost loop.
  if [[ -n "${barrier_dir:-}" ]] && sudo test -d "$barrier_dir"; then
    : | sudo tee "$barrier_dir/start-release" >/dev/null
    : | sudo tee "$barrier_dir/stop-release" >/dev/null
  fi
  sudo systemctl stop happyranch-managed.target || true
  if [[ -n "${sidecar_ip:-}" ]] && [[ -n "$peer_pid" ]] && sudo kill -0 "$peer_pid" 2>/dev/null; then
    ! tsnet_open || cleanup_failed=1
    (( cleanup_failed != 0 )) || evidence cleanup virtual_admission_removed_while_peer_alive || cleanup_failed=1
  fi
  sudo systemctl disable happyranch-managed.target || true
  sudo systemctl reset-failed happyranch-connector.service happyranch-tsnet-sidecar.service happyranch-managed.target || true
  sudo rm -rf /etc/systemd/system/happyranch-tsnet-sidecar.service.d
  sudo rm -f /etc/systemd/system/happyranch-connector.service /etc/systemd/system/happyranch-tsnet-sidecar.service /etc/systemd/system/happyranch-managed.target
  sudo systemctl daemon-reload
  for pid in "$peer_pid" "$daemon_pid" "$headscale_pid"; do
    [[ -z "$pid" ]] || sudo kill "$pid"
  done
  for pid in "$peer_pid" "$daemon_pid" "$headscale_pid"; do
    if [[ -n "$pid" ]]; then
      # The primary failure may be the fixture exiting before teardown. Reap
      # it, but determine residue from liveness after the reap, not exit code.
      wait "$pid" 2>/dev/null
    fi
    [[ -z "$pid" ]] || ! sudo kill -0 "$pid" 2>/dev/null || cleanup_failed=1
  done
  sudo rm -f /usr/local/share/ca-certificates/happyranch-n3-ci.crt
  sudo update-ca-certificates >/dev/null 2>&1
  printf 'fixtures_reaped=%s\n' "$(( cleanup_failed == 0 ))" >"$diagnostics/cleanup-status.txt"
  sudo rm -rf /opt/happyranch /etc/happyranch /var/lib/happyranch-connector /var/lib/happyranch-tsnet-sidecar /run/happyranch-connector /run/happyranch-tsnet-sidecar /var/log/happyranch-connector /var/log/happyranch-tsnet-sidecar
  if systemctl list-unit-files happyranch-managed.target happyranch-connector.service happyranch-tsnet-sidecar.service --no-legend 2>/dev/null | grep -q .; then cleanup_failed=1; fi
  for path in /opt/happyranch /etc/happyranch /var/lib/happyranch-connector /var/lib/happyranch-tsnet-sidecar /run/happyranch-connector /run/happyranch-tsnet-sidecar /var/log/happyranch-connector /var/log/happyranch-tsnet-sidecar /.happyranch-install-transaction.json /.happyranch-backup /.happyranch-units-backup; do
    sudo test ! -e "$path" || cleanup_failed=1
  done
  [[ -z "$(sudo find / -maxdepth 1 \( -name '.happyranch-stage-*' -o -name '.happyranch-tmp-*' \) -print -quit)" ]] || cleanup_failed=1
  for port in 18443 18765 18080 19090 15043 13478; do ! port_open "$port" || cleanup_failed=1; done
  for unit in happyranch-connector.service happyranch-tsnet-sidecar.service; do
    main_pid="$(systemctl show "$unit" -p MainPID --value 2>/dev/null)"
    [[ -z "$main_pid" || "$main_pid" == 0 ]] || cleanup_failed=1
  done
  (( cleanup_failed != 0 )) || evidence cleanup all_residue_absent || cleanup_failed=1
  rm -rf "$work"
  [[ ! -e "$work" ]] || cleanup_failed=1
  (( cleanup_failed != 0 )) || evidence cleanup task_work_removed || cleanup_failed=1
  if (( original_status == 0 && cleanup_failed == 0 )); then
    python "$evidence_driver" finalize "$evidence_artifact" || cleanup_failed=1
    python "$evidence_driver" validate "$evidence_artifact" --expected-subject "$PROOF_SUBJECT_SHA" --expected-run "$run_id" || cleanup_failed=1
  fi
  (( cleanup_failed == 0 )) || echo "n3-real-systemd: teardown residue" >&2
  trap - EXIT INT TERM
  (( original_status != 0 )) && exit "$original_status"
  exit "$cleanup_failed"
}
trap cleanup EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM

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
unix_socket: $work/hs/headscale.sock
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
printf '%s\n' '{"acls":[{"action":"accept","src":["*"],"dst":["*:*"]}]}' >"$work/hs/policy.json"
sudo install -m 0644 "$work/tls/cert.pem" /usr/local/share/ca-certificates/happyranch-n3-ci.crt
sudo update-ca-certificates >/dev/null
"$work/headscale" serve --config "$work/hs/config.yaml" >"$work/headscale.log" 2>&1 & headscale_pid=$!
wait_for "Headscale process" sudo kill -0 "$headscale_pid"
wait_for "Headscale HTTPS listener" port_open 18080
wait_for "Headscale health" curl --silent --fail --cacert "$work/tls/cert.pem" https://127.0.0.1:18080/health
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
# Both plaintext LoadCredential sources remain root-custodied; service users
# receive only systemd's private staged copies.
sudo install -m 0600 -o root -g root "$work/daemon.token" /etc/happyranch/daemon.token

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
# The system manager requires a root-custodied plaintext LoadCredential source;
# the unprivileged service receives only systemd's private staged copy.
sudo install -m 0600 -o root -g root "$work/enrollment.key" /etc/happyranch/enrollment.key
sudo env "PATH=$PATH" uv run python - "$PACKAGE_TAR" <<'PY'
import sys
from pathlib import Path
from runtime.remote_access.linux_package import install_linux_package
install_linux_package(Path(sys.argv[1]), Path('/'), system_service=True)
PY
[[ "$(stat -c %U:%G:%a /opt/happyranch)" == "root:root:755" ]] || fail "system-service payload root custody mismatch"
[[ "$(stat -c %U:%G:%a /opt/happyranch/bin)" == "root:root:755" ]] || fail "system-service binary directory custody mismatch"
for binary in /opt/happyranch/bin/happyranch-connector /opt/happyranch/bin/happyranch-tsnet-sidecar; do
  [[ "$(stat -c %U:%G:%a "$binary")" == "root:root:755" ]] || fail "system-service binary custody mismatch"
  sudo -u happyranch test -x "$binary" || fail "system-service user cannot execute packaged binary"
done
sudo systemctl daemon-reload
[[ "$(sudo stat -c %U:%G:%a /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf)" == "root:root:600" ]] || fail "transient credential drop-in custody mismatch"
[[ "$(sudo stat -c %U:%G:%a /etc/happyranch/daemon.token)" == "root:root:600" ]] || fail "daemon credential source custody mismatch"
[[ "$(sudo stat -c %U:%G:%a /etc/happyranch/enrollment.key)" == "root:root:600" ]] || fail "enrollment credential source custody mismatch"

capture_denial_matrix() {
  local arm_id="$1"
  # Execute bounded probes as the shipping service user before teardown. Only
  # fixed identifiers/classifications leave this process; exception prose,
  # paths, identities, credentials, and control responses are discarded.
  sudo timeout 15 systemd-run --quiet --wait --collect --pipe \
    --unit="happyranch-n3-denial-${arm_id}" \
    --property=User=happyranch --property=Group=happyranch \
    --property=NoNewPrivileges=yes --property=PrivateDevices=yes \
    --property=ProtectSystem=strict --property=ProtectHome=yes \
    --property=ReadWritePaths=/var/lib/happyranch-tsnet-sidecar \
    --property='RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX AF_NETLINK' \
    --property='CapabilityBoundingSet=' \
    /usr/bin/python3 - "$arm_id" >"$diagnostics/$arm_id-denial-matrix.json" <<'PY'
import errno, json, os, socket, sys

ERRNOS = {errno.EACCES:"EACCES", errno.EPERM:"EPERM", errno.ENOENT:"ENOENT",
          errno.ENODEV:"ENODEV", errno.EAFNOSUPPORT:"EAFNOSUPPORT",
          errno.ETIMEDOUT:"ETIMEDOUT", errno.ECONNREFUSED:"ECONNREFUSED", errno.EIO:"EIO"}
def measured(operation, probe):
    try:
        value = probe()
        if hasattr(value, "close"): value.close()
        return {"id":operation,"measured":True,"result":"allow","category":"none","errno":None}
    except OSError as exc:
        code = ERRNOS.get(exc.errno, "OTHER")
        category = "permission_denied" if exc.errno in (errno.EACCES, errno.EPERM) else "unavailable"
        if exc.errno == errno.ETIMEDOUT: category = "timeout"
        return {"id":operation,"measured":True,"result":"deny" if category == "permission_denied" else "unknown","category":category,"errno":code}
    except Exception:
        return {"id":operation,"measured":True,"result":"unknown","category":"operational_error","errno":"OTHER"}
def writable():
    path = "/var/lib/happyranch-tsnet-sidecar/probe-write"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd); os.unlink(path)
operations = [
    measured("address_family_netlink", lambda: socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, 0)),
    measured("linux_capabilities", lambda: socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)),
    measured("device_access", lambda: open("/dev/net/tun", "rb", buffering=0)),
    measured("writable_paths", writable),
    measured("control_plane_operations", lambda: socket.create_connection(("127.0.0.1", 18080), timeout=2)),
]
json.dump({"schema":"happyranch.n3.sandbox-denial-matrix","version":1,
           "arm_id":sys.argv[1],"operations":operations}, sys.stdout, separators=(",", ":"))
PY
  python "$evidence_driver" validate-denial-matrix "$diagnostics/$arm_id-denial-matrix.json" --expected-arm "$arm_id"
}
shipping_cleanup() {
  local cleanup_complete=0 residue_root="${N3_RESIDUE_ROOT:-}"
  # These requests are deliberately idempotent: the pre-arm reset also runs
  # after a prior cleanup has removed the units.  The explicit process, port,
  # fixture, credential, transaction, and path checks below decide success.
  sudo systemctl stop happyranch-managed.target happyranch-tsnet-sidecar.service happyranch-connector.service || true
  sudo systemctl disable happyranch-managed.target || true
  sudo systemctl reset-failed happyranch-managed.target happyranch-tsnet-sidecar.service happyranch-connector.service || true
  sudo rm -rf /etc/systemd/system/happyranch-tsnet-sidecar.service.d
  sudo rm -f /etc/systemd/system/happyranch-managed.target /etc/systemd/system/happyranch-tsnet-sidecar.service /etc/systemd/system/happyranch-connector.service
  sudo systemctl daemon-reload || cleanup_complete=1
  sudo rm -rf /opt/happyranch /etc/happyranch /var/lib/happyranch-connector /var/lib/happyranch-tsnet-sidecar /run/happyranch-connector /run/happyranch-tsnet-sidecar /var/log/happyranch-connector /var/log/happyranch-tsnet-sidecar
  while read -r fixture_id; do
    [[ -z "$fixture_id" ]] || "$work/headscale" nodes delete --identifier "$fixture_id" --force --config "$work/hs/config.yaml" >/dev/null || cleanup_complete=1
  done < <("$work/headscale" nodes list --output json --config "$work/hs/config.yaml" | python -c 'import json,sys; print("\n".join(str(n["id"]) for n in json.load(sys.stdin) if n.get("givenName")=="home-sidecar-ci" or n.get("name")=="home-sidecar-ci"))')
  "$work/headscale" nodes list --output json --config "$work/hs/config.yaml" | python -c 'import json,sys; raise SystemExit(any(n.get("givenName")=="home-sidecar-ci" or n.get("name")=="home-sidecar-ci" for n in json.load(sys.stdin)))' || cleanup_complete=1
  for unit in happyranch-managed.target happyranch-tsnet-sidecar.service happyranch-connector.service; do
    unit_absent "$unit" || cleanup_complete=1
  done
  ! systemctl list-unit-files happyranch-managed.target happyranch-tsnet-sidecar.service happyranch-connector.service --no-legend 2>/dev/null | grep -q . || cleanup_complete=1
  ! port_open 18443 || cleanup_complete=1
  ! tsnet_open || cleanup_complete=1
  [[ ! -e "$residue_root/etc/happyranch/enrollment.key" && ! -e "$residue_root/etc/systemd/system/happyranch-tsnet-sidecar.service.d" ]] || cleanup_complete=1
  [[ ! -e "$residue_root/.happyranch-install-transaction.json" && ! -e "$residue_root/.happyranch-backup" && ! -e "$residue_root/.happyranch-units-backup" ]] || cleanup_complete=1
  [[ ! -e "$residue_root/opt/happyranch" && ! -e "$residue_root/etc/happyranch" && ! -e "$residue_root/var/lib/happyranch-connector" && ! -e "$residue_root/var/lib/happyranch-tsnet-sidecar" ]] || cleanup_complete=1
  [[ ! -e "$residue_root/run/happyranch-connector" && ! -e "$residue_root/run/happyranch-tsnet-sidecar" && ! -e "$residue_root/var/log/happyranch-connector" && ! -e "$residue_root/var/log/happyranch-tsnet-sidecar" ]] || cleanup_complete=1
  ! pgrep -f '(^|/)(happyranch-connector|happyranch-tsnet-sidecar)( |$)' >/dev/null || cleanup_complete=1
  [[ -z "$(sudo find "${residue_root:-/}" -maxdepth 1 \( -name '.happyranch-stage-*' -o -name '.happyranch-tmp-*' \) -print -quit)" ]] || cleanup_complete=1
  (( cleanup_complete == 0 ))
}
reset_shipping_unit() {
  shipping_cleanup || return 1
  sudo install -d -m 0700 -o happyranch -g happyranch /etc/happyranch
  sudo install -m 0600 -o root -g root "$work/daemon.token" /etc/happyranch/daemon.token
  sudo install -m 0600 -o happyranch -g happyranch "$work/connector.json" /etc/happyranch/connector.json
  sudo install -m 0600 -o happyranch -g happyranch "$work/policy.json" /etc/happyranch/policy.json
  sudo install -m 0600 -o happyranch -g happyranch "$work/sidecar.json" /etc/happyranch/sidecar.json
  local fresh_key
  fresh_key="$("$work/headscale" preauthkeys create --user ci --reusable=false --expiration 10m --config "$work/hs/config.yaml")"
  printf '%s\n' "$fresh_key" >"$work/enrollment.key"
  sudo install -m 0600 -o root -g root "$work/enrollment.key" /etc/happyranch/enrollment.key
  sudo env "PATH=$PATH" uv run python - "$PACKAGE_TAR" <<'PY'
import sys
from pathlib import Path
from runtime.remote_access.linux_package import install_linux_package
install_linux_package(Path(sys.argv[1]), Path('/'), system_service=True)
PY
  sudo systemctl daemon-reload
  printf 'shipping_unit_reset=complete cleanup_complete=true\n' >>"$diagnostics/shipping-unit.log"
}
reset_shipping_unit || fail "fresh shipping-unit reset/setup failed"

# semantic evidence: startup
sudo mv /etc/happyranch/enrollment.key /etc/happyranch/enrollment.key.held
sudo systemctl start happyranch-managed.target || true
sleep 2
sudo systemctl stop happyranch-managed.target happyranch-tsnet-sidecar.service happyranch-connector.service
! active happyranch-tsnet-sidecar.service || fail "sidecar survived missing-credential startup"
absent /var/lib/happyranch-tsnet-sidecar/credential.consumed
[[ "$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)" == 0 ]] || fail "sidecar process survived failed startup"
sudo "$ts_dir/tailscale" --socket="$work/peer.sock" status --json | python -c 'import json,sys; d=json.load(sys.stdin); raise SystemExit(any(p.get("HostName")=="home-sidecar-ci" for p in (d.get("Peer") or {}).values()))' || fail "failed-start TSNet identity remained visible"
evidence "startup" "process_absent"
evidence "startup" "tsnet_admission_absent"
negative_leg_diagnostic_id="$run_id:negative-leg-expected:credential_input"
diagnostic credential_input input_acquisition systemd happyranch-tsnet-sidecar.service "$negative_leg_diagnostic_id"
printf 'diagnostic_id=%s expectation=expected category=credential_input\n' "$negative_leg_diagnostic_id" >>"$diagnostics/diagnostic-expectations.log"
# The unit has Restart=on-failure.  End the deliberately failed credential
# transaction completely before restoring the one-use source, otherwise a
# queued restart can race the first real enrollment and consume its staging.
sudo systemctl reset-failed happyranch-tsnet-sidecar.service happyranch-connector.service
wait_for "failed credential staging cleanup" sudo test ! -e /run/credentials/happyranch-tsnet-sidecar.service
capture_failure_snapshot completed-stop-reset || true
sudo mv /etc/happyranch/enrollment.key.held /etc/happyranch/enrollment.key
capture_failure_snapshot restored-source-pre-probe || true

# The failed shipping-service start above causes systemd to create the unit's
# StateDirectory. The denial probe can now retain the shipping unit's exact
# ReadWritePaths and explicit AF_NETLINK allowance while measuring every other
# sandbox dimension fail closed, without a harness-created drop-in.
capture_denial_matrix shipping-unit
capture_failure_snapshot post-probe-pre-positive-start || true

start_managed_target || exit "$?"
wait_for "connector READY" active happyranch-connector.service
wait_for "sidecar READY and ExpectedPeers" active happyranch-tsnet-sidecar.service
connector_ready="$(systemctl show happyranch-connector.service -p ActiveEnterTimestampMonotonic --value)"
sidecar_ready="$(systemctl show happyranch-tsnet-sidecar.service -p ActiveEnterTimestampMonotonic --value)"
[[ "$sidecar_ready" -le "$connector_ready" ]] || fail "connector reported composite READY before sidecar admission"
check_staged_credential() {
  local path="$1" directory="$2" name="$3"
  [[ "$path" == "$directory/$name" ]] || fail "staged credential provenance mismatch"
  [[ "$(readlink -f -- "$path")" == "$path" ]] || fail "staged credential escape mismatch"
  sudo test -f "$path" || fail "staged credential type mismatch"
  ! sudo test -L "$path" || fail "staged credential symlink mismatch"
  sudo -u happyranch test -r "$path" || fail "staged credential unreadable"
  ! sudo -u happyranch test -w "$path" || fail "staged credential service-writable"
  ! sudo -u happyranch test -w "$directory" || fail "staged credential directory service-writable"
  printf 'credential_observation name=%s file=%s directory=%s\n' \
    "$name" "$(sudo stat -c %U:%G:%a:%F "$path")" "$(sudo stat -c %U:%G:%a:%F "$directory")"
}
check_staged_credential /run/credentials/happyranch-connector.service/daemon.token /run/credentials/happyranch-connector.service daemon.token
check_staged_credential /run/credentials/happyranch-tsnet-sidecar.service/enrollment.key /run/credentials/happyranch-tsnet-sidecar.service enrollment.key
absent /etc/happyranch/enrollment.key
absent /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf
evidence "startup" "connector_staged_credential_service_readable_non_writable"
evidence "startup" "sidecar_staged_credential_service_readable_non_writable"
evidence "startup" "credential_source_retired"
evidence "startup" "credential_dropin_retired"
evidence "startup" "composite_ready_after_sidecar"
sudo systemctl stop happyranch-managed.target
sudo mv /var/lib/happyranch-tsnet-sidecar/credential.consumed "$work/credential.consumed.held"
sudo systemctl start happyranch-managed.target || true
sleep 2
! active happyranch-tsnet-sidecar.service || fail "missing consumed state started sidecar"
evidence "startup" "missing_consumed_state_failed_closed"
sudo mv "$work/credential.consumed.held" /var/lib/happyranch-tsnet-sidecar/credential.consumed
sudo systemctl reset-failed happyranch-tsnet-sidecar.service happyranch-connector.service
sudo systemctl start happyranch-managed.target
wait_for "credential-free stopped-service restart" active happyranch-tsnet-sidecar.service
absent /etc/happyranch/enrollment.key
absent /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf
evidence "recovery" "credential_free_stopped_restart"

# Re-enter after interruption between drop-in reload and source unlink.
sudo systemctl stop happyranch-managed.target
printf '%s\n' interrupted-retirement >"$work/enrollment.key"
sudo install -m 0600 -o root -g root "$work/enrollment.key" /etc/happyranch/enrollment.key
sudo systemctl start happyranch-managed.target
wait_for "interrupted retirement re-entry" active happyranch-tsnet-sidecar.service
absent /etc/happyranch/enrollment.key
evidence "recovery" "interrupted_retirement_reentry"

# A fresh enrollment is explicit and service-stopped; ordinary restarts never
# recreate this root-custodied source or transient LoadCredential drop-in.
sudo systemctl stop happyranch-managed.target
fresh_sidecar_key="$("$work/headscale" preauthkeys create --user ci --reusable=false --expiration 10m --config "$work/hs/config.yaml")"
printf '%s\n' "$fresh_sidecar_key" >"$work/enrollment.key"
sudo install -m 0600 -o root -g root "$work/enrollment.key" /etc/happyranch/enrollment.key
sudo /opt/happyranch/bin/happyranch-connector prepare-fresh-enrollment --source /etc/happyranch/enrollment.key --marker /var/lib/happyranch-tsnet-sidecar/credential.consumed --dropin /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf
sudo systemctl start happyranch-managed.target
wait_for "explicit fresh re-enrollment" active happyranch-tsnet-sidecar.service
absent /etc/happyranch/enrollment.key
absent /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf
evidence "recovery" "explicit_fresh_reenrollment"
sidecar_ip="$(sudo "$ts_dir/tailscale" --socket="$work/peer.sock" status --json | python -c 'import json,sys; d=json.load(sys.stdin); print(next(ip for p in d.get("Peer",{}).values() if p.get("HostName")=="home-sidecar-ci" for ip in p.get("TailscaleIPs",[]) if ":" not in ip))')"
wait_for "virtual TSNet listener" tsnet_open
evidence "admission" "tsnet_admission_reachable"
[[ "$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)" != 0 ]] || fail "production sidecar absent"
evidence "active_flow" "production_process_active"
watchdog_before="$(systemctl show happyranch-connector.service -p WatchdogTimestampMonotonic --value)"
sleep 12
watchdog_after="$(systemctl show happyranch-connector.service -p WatchdogTimestampMonotonic --value)"
[[ "$watchdog_after" -gt "$watchdog_before" ]] || fail "connector watchdog did not follow current composite health"
active happyranch-tsnet-sidecar.service || fail "watchdog continued without sidecar health"
evidence "active_flow" "watchdog_composite_current"

sudo systemctl stop happyranch-tsnet-sidecar.service
wait_for "connector watchdog cessation" bash -c '! systemctl is-active --quiet happyranch-connector.service'
! tsnet_open || fail "admission survived sidecar health loss"
watchdog_stopped="$(systemctl show happyranch-connector.service -p WatchdogTimestampMonotonic --value)"
sleep 2
[[ "$(systemctl show happyranch-connector.service -p WatchdogTimestampMonotonic --value)" == "$watchdog_stopped" ]] || fail "watchdog refreshed after sidecar loss"
evidence "active_flow" "watchdog_ceased_on_sidecar_loss"
sudo systemctl start happyranch-managed.target
wait_for "composite restart after health loss" active happyranch-tsnet-sidecar.service
wait_for "virtual admission after health recovery" tsnet_open

# semantic evidence: partial_failure
old_pid="$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)"
sudo kill -KILL "$old_pid"
wait_for "automatic Restart=" bash -c "test \"\$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)\" != '$old_pid' && systemctl is-active --quiet happyranch-tsnet-sidecar.service"
evidence "partial_failure" "fresh_pid"
tsnet_open || fail "fresh process did not restore composite gates"
evidence "partial_failure" "fresh_composite_gates"

# semantic evidence: concurrency_reentry. A shipping-unit ExecStartPre barrier
# proves start has entered before stop is queued; stop must win after release.
sudo install -d -m 0700 -o happyranch -g happyranch "$barrier_dir"
sudo install -d -m 0755 /etc/systemd/system/happyranch-tsnet-sidecar.service.d
sudo tee /etc/systemd/system/happyranch-tsnet-sidecar.service.d/90-ci-barrier.conf >/dev/null <<EOF
[Service]
ExecStartPre=/bin/sh -c 'touch $barrier_dir/start-entered; while test ! -e $barrier_dir/start-release; do sleep .05; done'
EOF
sudo systemctl daemon-reload
sudo systemctl stop happyranch-managed.target
sudo systemctl start happyranch-managed.target & start_job=$!
wait_for "start barrier entered" sudo test -e "$barrier_dir/start-entered"
sudo systemctl stop happyranch-managed.target & stop_job=$!
systemctl list-jobs --no-legend | grep -q 'happyranch-managed.target' || fail "stop was not queued behind entered start"
evidence "concurrency_reentry" "start_then_stop_barrier"
: | sudo tee "$barrier_dir/start-release" >/dev/null; wait "$start_job" || true; wait "$stop_job"
! active happyranch-tsnet-sidecar.service || fail "start-then-stop did not stop"
evidence "concurrency_reentry" "stop_wins"

# Force the opposite ordering: production Stop completes while a new start is
# queued behind an ExecStopPost barrier, so stale admission cannot survive.
sudo tee /etc/systemd/system/happyranch-tsnet-sidecar.service.d/90-ci-barrier.conf >/dev/null <<EOF
[Service]
ExecStopPost=/bin/sh -c 'touch $barrier_dir/stop-entered; while test ! -e $barrier_dir/stop-release; do sleep .05; done'
EOF
sudo systemctl daemon-reload
sudo systemctl start happyranch-managed.target
sudo systemctl stop happyranch-managed.target & stop_job=$!
wait_for "stop barrier entered" sudo test -e "$barrier_dir/stop-entered"
! tsnet_open || fail "TSNet admission survived production Stop"
sudo systemctl start happyranch-managed.target & start_job=$!
systemctl list-jobs --no-legend | grep -q 'happyranch-managed.target' || fail "start was not queued behind entered stop"
evidence "concurrency_reentry" "stop_then_start_barrier"
: | sudo tee "$barrier_dir/stop-release" >/dev/null; wait "$stop_job"; wait "$start_job"
sudo rm -f /etc/systemd/system/happyranch-tsnet-sidecar.service.d/90-ci-barrier.conf
sudo rm -f "$barrier_dir/start-entered" "$barrier_dir/start-release" "$barrier_dir/stop-entered" "$barrier_dir/stop-release"
sudo rmdir "$barrier_dir" || fail "barrier residue"
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

# semantic evidence: revocation and shutdown. The packaged binary calls Stop
# twice on the same Sidecar and emits invocation-scoped receipts for its PID.
sudo systemctl start happyranch-managed.target
wait_for "shutdown admission" tsnet_open
shutdown_pid="$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)"
sudo systemctl stop happyranch-managed.target
! tsnet_open || fail "TSNet admission survived target stop"
shutdown_receipts="$(sudo journalctl -u happyranch-tsnet-sidecar.service _PID="$shutdown_pid" --no-pager -o cat | grep "lifecycle_stop_complete run=$shutdown_pid invocation=" || true)"
[[ "$(printf '%s\n' "$shutdown_receipts" | grep -c .)" == 2 ]] || fail "same-instance Stop did not produce two receipts"
grep -qx "lifecycle_stop_complete run=$shutdown_pid invocation=1" <<<"$shutdown_receipts" || fail "missing first same-instance Stop receipt"
grep -qx "lifecycle_stop_complete run=$shutdown_pid invocation=2" <<<"$shutdown_receipts" || fail "missing second same-instance Stop receipt"
evidence "revocation" "stop_before_connector_cleanup"
evidence "revocation" "tsnet_admission_absent"
evidence "shutdown" "same_instance_stop_twice"
evidence "shutdown" "no_double_close"
[[ "$(systemctl show happyranch-tsnet-sidecar.service -p MainPID --value)" == 0 ]] || fail "sidecar residue after repeated shutdown"
evidence "shutdown" "no_residue"

sudo systemctl start happyranch-managed.target
wait_for "fresh recovery" active happyranch-tsnet-sidecar.service
# semantic evidence: recovery. Exercise the real installer checkpoint seam on
# both empty-root and live upgrade paths, then re-enter and prove fresh gates.
for boundary in payload_old_retained payload_published unit_published:happyranch-connector.service unit_published:happyranch-tsnet-sidecar.service unit_published:happyranch-managed.target; do
  sudo systemctl stop happyranch-managed.target
  sudo env "PATH=$PATH" uv run python - <<'PY'
from pathlib import Path
from runtime.remote_access.linux_package import uninstall_linux_package
uninstall_linux_package(Path('/'))
PY
  BOUNDARY="$boundary" sudo env "PATH=$PATH" BOUNDARY="$boundary" uv run python - "$PACKAGE_TAR" <<'PY'
import os, sys
from pathlib import Path
from runtime.remote_access.linux_package import install_linux_package
def fault(name):
    if name == os.environ['BOUNDARY']:
        raise RuntimeError('injected')
try:
    install_linux_package(Path(sys.argv[1]), Path('/'), system_service=True, fault=fault)
except RuntimeError:
    pass
else:
    raise SystemExit('fault did not fire')
PY
  absent /opt/happyranch
  for unit in happyranch-connector.service happyranch-tsnet-sidecar.service happyranch-managed.target; do absent "/etc/systemd/system/$unit"; done
  for path in /.happyranch-install-transaction.json /.happyranch-backup /.happyranch-units-backup; do absent "$path"; done
  [[ -z "$(sudo find / -maxdepth 1 \( -name '.happyranch-stage-*' -o -name '.happyranch-tmp-*' \) -print -quit)" ]] || fail "fresh transaction residue"
  sudo env "PATH=$PATH" uv run python - "$PACKAGE_TAR" <<'PY'
import sys
from pathlib import Path
from runtime.remote_access.linux_package import install_linux_package
install_linux_package(Path(sys.argv[1]), Path('/'), system_service=True)
PY
  sudo systemctl daemon-reload
  sudo systemctl start happyranch-managed.target
  wait_for "fresh $boundary composite startup" active happyranch-tsnet-sidecar.service
  wait_for "fresh $boundary virtual admission" tsnet_open
done
evidence "recovery" "fresh_install_rollback_reentry_each_checkpoint"
before_manifest="$(sudo sha256sum /opt/happyranch/manifest.json)"
sudo env "PATH=$PATH" BOUNDARY=payload_published uv run python - "$PACKAGE_TAR" <<'PY'
import os, sys
from pathlib import Path
from runtime.remote_access.linux_package import install_linux_package
def fault(name):
    if name == os.environ['BOUNDARY']:
        raise RuntimeError('injected')
try:
    install_linux_package(Path(sys.argv[1]), Path('/'), system_service=True, fault=fault)
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
install_linux_package(Path(sys.argv[1]), Path('/'), system_service=True)
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
echo N3_REAL_SYSTEMD_PASS
