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
target_job_pending() {
  # A target-name match alone can be the opposite job already at the barrier.
  # Retain the exact requested ordering; unknown/changed queues are no proof.
  local kind="$1"
  timeout --kill-after=1 3 systemctl list-jobs --no-legend --no-pager --full 2>/dev/null | LC_ALL=C awk -v kind="$kind" '
    NF != 4 || $0 ~ /[^[:print:]\t]/ || $1 !~ /^[1-9][0-9]*$/ { invalid=1 }
    $2 == "happyranch-managed.target" {
      count++
      if ($3 != kind || $4 != "waiting") invalid=1
    }
    END { exit (invalid || count != 1) }
  '
}
# Encode a false test inside the privileged command: sudo's own exit1 is
# never affirmative absence. Errors remain failures even in conditional callers.
test_false() {
  local kind="$1" path="$2"; shift 2
  sudo "$@" sh -c 'test "$1" "$2"; status=$?; [ "$status" -eq 1 ]' sh "$kind" "$path"
}
absent() { test_false -e "$1" || fail "residue or failed query at $1"; }
inactive() {
  local value
  value="$(systemctl show "$1" -p ActiveState --value 2>/dev/null)" || return 1
  [[ "$value" == inactive || "$value" == failed ]]
}
systemctl_number() {
  local value
  value="$(systemctl show "$1" -p "$2" --value 2>/dev/null)" || return 1
  [[ "$value" =~ ^(0|[1-9][0-9]{0,17})$ ]] || return 1
  printf '%s' "$value"
}
mainpid() {
  local value
  value="$(systemctl_number "$1" MainPID)" || return 1
  (( value == 0 || (value > 1 && value <= 2147483647) )) || return 1
  printf '%s' "$value"
}
port_absent() {
  local listeners
  listeners="$(ss -H -ltn "sport = :$1" 2>/dev/null)" || return 1
  [[ -z "$listeners" ]]
}
process_absent() {
  local processes
  processes="$(ps -e -o pid= 2>/dev/null)" || return 1
  # A successful full census is required; kill -0 errors also include EPERM.
  printf '%s\n' "$processes" | awk -v pid="$1" '
    NF != 1 || $1 !~ /^[0-9]+$/ { invalid=1 }
    $1 == pid { present=1 }
    END { exit (NR == 0 || invalid || present) }
  '
}
fresh_sidecar_pid() {
  local value
  value="$(mainpid happyranch-tsnet-sidecar.service)" || return 1
  [[ "$value" != 0 && "$value" != "$old_pid" ]] && active happyranch-tsnet-sidecar.service
}
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
# These helpers serve only the six virtual-removal sites below. A failed nc
# remains inconclusive: successful SAME-instance Stop independently establishes
# local listener teardown; a successful negative-phase nc always vetoes it.
# Bounded private text observation for only Stop binding and same-peer checks.
# Raw text is transient, capped, and validated before Bash can strip NUL bytes.
virtual_text() {
  local cap="$1" result status=0; shift
  virtual_text_value=""; virtual_text_reason=unavailable
  result="$(timeout --kill-after=1 5 python - "$failure_capture_driver" "$cap" "$@" 2>/dev/null <<'PY_VIRTUAL_TEXT'
import importlib.util
import sys
import signal

def cancelled(signum, _frame):
    for item in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(item, signal.SIG_IGN)
    raise SystemExit(128 + signum)

for item in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
    signal.signal(item, cancelled)

spec = importlib.util.spec_from_file_location("n3_capture", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
raw, loss = module._bounded_observation(sys.argv[3:], seconds=3, cap=int(sys.argv[2]))
if loss != "observed":
    print(loss)
    raise SystemExit(1)
try:
    value = raw.decode("utf-8", "strict")
    if not value.endswith("\n") or any(not c.isprintable() and c not in "\n\t" for c in value):
        raise ValueError
except (ValueError, UnicodeError):
    print("parse_loss")
    raise SystemExit(1)
print("observed")
sys.stdout.write(value)
PY_VIRTUAL_TEXT
)" || status=$?
  case "$status" in
    0) [[ "$result" == observed$'\n'* ]] || return 1
       virtual_text_value="${result#*$'\n'}"; virtual_text_reason=observed ;;
    124|137) virtual_text_reason=timeout; return 1 ;;
    *) case "$result" in timeout|query_error|launch_failure|truncated|parse_loss) virtual_text_reason="$result" ;; esac
       return 1 ;;
  esac
}
bind_stop_instance() {
  local phase="$1" properties checked key value pid="" invocation="" started="" state="" seen="|"
  stop_phase=""; stop_pid=""; stop_invocation=""; stop_since=""; stop_boot=""
  case "$phase" in final|shipping|health_loss|stop_barrier|readiness_loss|shutdown) ;; *) return 1 ;; esac
  stop_since="$(date -u +%s.%6N)" || { virtual_refuse "$phase" binding unavailable; return 1; }
  [[ "$stop_since" =~ ^[1-9][0-9]{9}\.[0-9]{6}$ ]] || { virtual_refuse "$phase" binding timestamp_invalid; return 1; }
  stop_boot="$(cat /proc/sys/kernel/random/boot_id)" || { virtual_refuse "$phase" binding unavailable; return 1; }
  [[ "$stop_boot" =~ ^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$ ]] || { virtual_refuse "$phase" binding boot_mismatch; return 1; }
  virtual_text 1024 systemctl show happyranch-tsnet-sidecar.service -p MainPID -p InvocationID -p ExecMainStartTimestampMonotonic -p ActiveState || { virtual_refuse "$phase" binding "$virtual_text_reason"; return 1; }
  properties="$virtual_text_value"
  while IFS='=' read -r key value; do
    [[ "$seen" != *"|$key|"* ]] || { virtual_refuse "$phase" binding duplicate_field; return 1; }
    seen="$seen$key|"
    case "$key" in
      MainPID) pid="$value" ;;
      InvocationID) invocation="$value" ;;
      ExecMainStartTimestampMonotonic) started="$value" ;;
      ActiveState) state="$value" ;;
      *) virtual_refuse "$phase" binding parse_loss; return 1 ;;
    esac
  done <<<"$properties"
  [[ "$pid" =~ ^[1-9][0-9]{0,9}$ ]] && (( pid > 1 && pid <= 2147483647 )) || { virtual_refuse "$phase" binding pid_mismatch; return 1; }
  [[ "$invocation" =~ ^[0-9a-f]{32}$ && "$invocation" != 00000000000000000000000000000000 ]] || { virtual_refuse "$phase" binding invocation_mismatch; return 1; }
  [[ "$started" =~ ^[1-9][0-9]{0,17}$ && "$state" == active ]] || { virtual_refuse "$phase" binding changed; return 1; }
  # Recheck the complete tuple before Stop, rather than combining fields from
  # different activations. Journal invocation+boot+time excludes stale/PID reuse.
  virtual_text 1024 systemctl show happyranch-tsnet-sidecar.service -p MainPID -p InvocationID -p ExecMainStartTimestampMonotonic -p ActiveState || { virtual_refuse "$phase" binding "$virtual_text_reason"; return 1; }
  checked="$virtual_text_value"
  [[ "$checked" == "$properties" ]] || { virtual_refuse "$phase" binding changed; return 1; }
  virtual_note "$phase" binding observed || return 1
  stop_pid="$pid"; stop_invocation="$invocation"; stop_phase="$phase"
}
# Fixed tokens only; at most 64 records of at most 128 bytes (8 KiB) per run.
# This private log is already covered by the diagnostics-directory upload.
virtual_note() {
  local phase="$1" check="$2" reason="$3" line
  case "$phase" in final|shipping|health_loss|stop_barrier|readiness_loss|shutdown) ;; *) phase=unknown ;; esac
  case "$check" in binding|sync|journal|mainpid|peer|probe|removal) ;; *) return 1 ;; esac
  case "$reason" in observed|complete|unavailable|timeout|query_error|launch_failure|truncated|over_cap|parse_loss|duplicate_field|binary_field|cursor_invalid|pid_mismatch|invocation_mismatch|boot_mismatch|timestamp_invalid|receipt_noncanonical|receipt_duplicate|receipt_missing|changed|nonzero|missing|connected|failed|failed_timeout|refused) ;; *) return 1 ;; esac
  line="n3-virtual:$phase:$check:$reason"
  printf '%s\n' "$line" >&2
  (( ${virtual_note_count:-0} < 64 && ${#line} < 128 )) || return 1
  virtual_note_count=$(( ${virtual_note_count:-0} + 1 ))
  printf '%s\n' "$line" >>"$diagnostics/virtual-removal.log"
}
virtual_refuse() { virtual_note "$1" "$2" "$3" || true; return 1; }
stop_completed() {
  local phase="$1" boot result status=0 check reason last pending
  [[ -n "${stop_phase:-}" && "$stop_phase" == "$phase" ]] || { virtual_refuse "$phase" binding missing; return 1; }
  boot="$(cat /proc/sys/kernel/random/boot_id)" || { virtual_refuse "$phase" binding unavailable; return 1; }
  [[ "$boot" == "$stop_boot" ]] || { virtual_refuse "$phase" binding boot_mismatch; return 1; }
  # Reuse the existing bounded private observer: each query owns 3s + 1s
  # group teardown, stdout+stderr cap, and no raw output enters diagnostics.
  # The outer 15s + 1s cap also bounds decoder/import failure (three queries).
  result="$(timeout --kill-after=1 15 python - "$failure_capture_driver" "$stop_pid" "$stop_invocation" "$stop_boot" "$stop_since" 2>/dev/null <<'PY_STOP'
import importlib.util
import json
import sys
import signal

def cancelled(signum, _frame):
    for item in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(item, signal.SIG_IGN)
    raise SystemExit(128 + signum)

for item in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
    signal.signal(item, cancelled)

spec = importlib.util.spec_from_file_location("n3_capture", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
pid, invocation, boot, since = sys.argv[2:]

def refuse(reason):
    raise ValueError(reason)

def query(check, argv, cap):
    raw, loss = module._bounded_observation(argv, seconds=3, cap=cap)
    if loss != "observed":
        print(f"{check}:{loss}", flush=True)
        raise SystemExit(1)
    return raw

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            refuse("duplicate_field")
        result[key] = value
    return result

def printable(value):
    if not isinstance(value, str):
        return False
    value.encode("utf-8", "strict")
    return all(character.isprintable() for character in value)

# v255 journalctl uses Varlink Synchronize: deferred NORMAL+15 follows queued
# stdout NORMAL+5. This is acquisition ordering, never receipt/identity proof.
query("sync", ["sudo", "-n", "journalctl", "--sync"], 1024)
print("sync:observed", flush=True)
raw = query("journal", ["sudo", "-n", "journalctl", "-u", "happyranch-tsnet-sidecar.service",
    "--boot=" + boot, "--since=@" + since, "_PID=" + pid,
    "_SYSTEMD_INVOCATION_ID=" + invocation, "--no-pager", "--all", "-o", "json",
    "--output-fields=MESSAGE,_PID,_SYSTEMD_INVOCATION_ID,_BOOT_ID"], 65536)
try:
    if raw and not raw.endswith(b"\n"):
        refuse("truncated")
    frames = raw.splitlines(keepends=True)
    if len(frames) > 256 or any(len(frame) > 8192 for frame in frames):
        refuse("over_cap")
    cutoff = int(since.replace(".", ""))
    expected = {f"lifecycle_stop_complete run={pid} invocation={i}" for i in (1, 2)}
    seen, cursors = set(), set()
    for frame in frames:
        if not frame.endswith(b"\n"):
            refuse("truncated")
        record = json.loads(frame.decode("utf-8", "strict"), object_pairs_hook=unique_object,
                            parse_constant=lambda _: refuse("parse_loss"))
        if not isinstance(record, dict):
            refuse("parse_loss")
        # JSON binary/multivalue fields are arrays, not authentic scalar fields.
        if any(isinstance(value, list) for value in record.values()):
            refuse("binary_field")
        message, cursor = record.get("MESSAGE"), record.get("__CURSOR")
        if not printable(message):
            refuse("parse_loss")
        if not printable(cursor) or not cursor or cursor in cursors:
            refuse("cursor_invalid")
        cursors.add(cursor)
        if record.get("_PID") != pid:
            refuse("pid_mismatch")
        if record.get("_SYSTEMD_INVOCATION_ID") != invocation:
            refuse("invocation_mismatch")
        if record.get("_BOOT_ID") != boot.replace("-", ""):
            refuse("boot_mismatch")
        timestamp = record.get("__REALTIME_TIMESTAMP")
        if (not isinstance(timestamp, str) or not timestamp.isascii()
                or not timestamp.isdecimal() or len(timestamp) > 20 or int(timestamp) < cutoff):
            refuse("timestamp_invalid")
        if "lifecycle_stop_complete" not in message:
            continue
        if message not in expected:
            refuse("receipt_noncanonical")
        if message in seen:
            refuse("receipt_duplicate")
        seen.add(message)
    if seen != expected:
        refuse("receipt_missing")
except (ValueError, UnicodeError, RecursionError) as error:
    reasons = {"truncated", "over_cap", "duplicate_field", "binary_field", "parse_loss",
               "cursor_invalid", "pid_mismatch", "invocation_mismatch", "boot_mismatch",
               "timestamp_invalid", "receipt_noncanonical", "receipt_duplicate", "receipt_missing"}
    # Never emit decoder exceptions or raw journal content.
    reason = str(error) if type(error) is ValueError and str(error) in reasons else "parse_loss"
    print(f"journal:{reason}", flush=True)
    raise SystemExit(1)
print("journal:complete", flush=True)
# A new running instance cannot reuse the preceding completed Stop proof.
raw = query("mainpid", ["systemctl", "show", "happyranch-tsnet-sidecar.service",
                       "-p", "MainPID", "--value"], 32)
if raw != b"0\n":
    reason = "nonzero" if raw.rstrip(b"\n").isdigit() else "parse_loss"
    print(f"mainpid:{reason}", flush=True)
    raise SystemExit(1)
print("mainpid:observed", flush=True)
PY_STOP
)" || status=$?
  while IFS=: read -r check reason; do
    [[ -n "$check" ]] || continue
    virtual_note "$phase" "$check" "$reason" || return 1
  done <<<"$result"
  last="${result##*$'\n'}"; pending=""
  case "$last" in
    '') pending=sync ;;
    sync:observed) pending=journal ;;
    journal:complete) pending=mainpid ;;
  esac
  case "$status" in
    0) [[ "$result" == $'sync:observed\njournal:complete\nmainpid:observed' ]] || { virtual_refuse "$phase" "${pending:-journal}" unavailable; return 1; } ;;
    124|137) virtual_refuse "$phase" "${pending:-journal}" timeout; return 1 ;;
    *) [[ -z "$pending" ]] || virtual_refuse "$phase" "$pending" unavailable; return 1 ;;
  esac
}
virtual_removed() {
  local phase="$1" probe_status=0 probe_failed=0 refused=0 probe_result
  # Always perform the real negative attempt when its inputs are available,
  # even if receipt acquisition will refuse. A successful dial always vetoes.
  if [[ -z "${sidecar_ip:-}" || ! -x "$ts_dir/tailscale" || ! -S "$work/peer.sock" ]]; then
    virtual_note "$phase" probe unavailable || refused=1
  else
    # The fixed launch marker distinguishes sudo/launch unavailability from
    # an actual nc attempt. No nc stdout, stderr or identity is retained.
    probe_result="$(printf 'GET / HTTP/1.0\r\n\r\n' | timeout --kill-after=1 5 sudo -n sh -c '
      printf attempt
      exec "$1" --socket="$2" nc "$3" 443 >/dev/null 2>&1
    ' sh "$ts_dir/tailscale" "$work/peer.sock" "$sidecar_ip" 2>/dev/null)" || probe_status=$?
    [[ "$probe_result" == attempt ]] || probe_status=125
    case "$probe_status" in
      0) virtual_note "$phase" probe connected || refused=1 ;;
      1) probe_failed=1; virtual_note "$phase" probe failed || refused=1 ;;
      124) probe_failed=1; virtual_note "$phase" probe failed_timeout || refused=1 ;;
      *) virtual_note "$phase" probe unavailable || refused=1 ;;
    esac
  fi
  stop_completed "$phase" || refused=1
  # Observe the independent peer check even when receipts refused; no later
  # result overwrites an initiating refusal or independently proves removal.
  if peer_current; then
    virtual_note "$phase" peer observed || refused=1
  else
    virtual_note "$phase" peer "${virtual_peer_reason:-unavailable}" || true
    refused=1
  fi
  (( refused == 0 && probe_failed == 1 )) || { virtual_refuse "$phase" removal refused; return 1; }
  virtual_note "$phase" removal complete
}
peer_process_identity() {
  [[ "${peer_pid:-}" =~ ^[1-9][0-9]{0,9}$ ]] || return 1
  # Linux process start ticks distinguish PID reuse; zombie/dead is not alive.
  python - "$peer_pid" 2>/dev/null <<'PY_PEER'
import sys
from pathlib import Path
pid = sys.argv[1]
raw = Path(f"/proc/{pid}/stat").read_text()
head, tail = raw.rsplit(") ", 1)
fields = tail.split()
if head.split(" (", 1)[0] != pid or len(fields) < 20:
    raise SystemExit(1)
if fields[0] not in {"R", "S", "D", "I", "T"}:
    raise SystemExit(1)
started = fields[19]
if not (started.isascii() and started.isdecimal() and int(started) > 0):
    raise SystemExit(1)
print(started)
PY_PEER
}
peer_identity() {
  # Observe the existing local API without capturing its raw status or keys.
  sudo "$ts_dir/tailscale" --socket="$work/peer.sock" status --json 2>/dev/null | python -c 'import ipaddress,json,sys; d=json.load(sys.stdin); (isinstance(d,dict)) or sys.exit(1); s=d.get("Self"); (d.get("BackendState")=="Running" and isinstance(s,dict) and s.get("HostName")=="synthetic-peer-ci") or sys.exit(1); i=s.get("ID"); (isinstance(i,str) and i and i.isascii() and i.isalnum()) or sys.exit(1); ips=s.get("TailscaleIPs"); (isinstance(ips,list) and ips and all(isinstance(ip,str) and ip for ip in ips)) or sys.exit(1); [ipaddress.ip_address(ip) for ip in ips]; print(i)' 2>/dev/null
}
peer_current() {
  local process identity status=0
  virtual_peer_reason=unavailable
  [[ -n "${peer_started:-}" && -n "${peer_node:-}" ]] || { virtual_peer_reason=missing; return 1; }
  process="$(peer_process_identity)" || return 1
  [[ "$process" == "$peer_started" ]] || { virtual_peer_reason=changed; return 1; }
  virtual_text 65536 sudo -n "$ts_dir/tailscale" --socket="$work/peer.sock" status --json || { virtual_peer_reason="$virtual_text_reason"; return 1; }
  # Preserve the same peer_identity fields and real local API, with bounded
  # acquisition and closed parse failure rather than suppressed raw errors.
  identity="$(timeout --kill-after=1 3 python -c '
import ipaddress,json,sys
def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result
d = json.load(sys.stdin, object_pairs_hook=unique)
(isinstance(d,dict)) or sys.exit(1)
s = d.get("Self")
(d.get("BackendState")=="Running" and isinstance(s,dict) and s.get("HostName")=="synthetic-peer-ci") or sys.exit(1)
i = s.get("ID")
(isinstance(i,str) and i and i.isascii() and i.isalnum()) or sys.exit(1)
ips = s.get("TailscaleIPs")
(isinstance(ips,list) and ips and all(isinstance(ip,str) and ip for ip in ips)) or sys.exit(1)
[ipaddress.ip_address(ip) for ip in ips]
print(i)
' <<<"$virtual_text_value" 2>/dev/null)" || status=$?
  case "$status" in
    0) ;;
    124|137) virtual_peer_reason=timeout; return 1 ;;
    *) virtual_peer_reason=parse_loss; return 1 ;;
  esac
  [[ "$identity" == "$peer_node" ]] || { virtual_peer_reason=changed; return 1; }
  process="$(peer_process_identity)" || return 1
  [[ "$process" == "$peer_started" ]] || { virtual_peer_reason=changed; return 1; }
  virtual_peer_reason=observed
}
peer_inventory_absent() {
  local process
  [[ -n "${peer_started:-}" && -n "${peer_node:-}" ]] || return 1
  process="$(peer_process_identity)" || return 1
  [[ "$process" == "$peer_started" ]] || return 1
  # Validate identity and inventory in the SAME consumed status. A separate
  # good peer_current query cannot make missing/malformed Peer data available.
  sudo "$ts_dir/tailscale" --socket="$work/peer.sock" status --json 2>/dev/null | python -c '
import ipaddress,json,sys
def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result
def invalid_constant(value):
    raise ValueError("invalid JSON constant")
data = json.loads(sys.stdin.buffer.read().decode("utf-8", "strict"), object_pairs_hook=unique_object, parse_constant=invalid_constant)
if not isinstance(data, dict) or data.get("BackendState") != "Running":
    raise ValueError("unavailable backend")
self = data.get("Self")
if (not isinstance(self, dict) or self.get("HostName") != "synthetic-peer-ci"
        or self.get("ID") != sys.argv[1]):
    raise ValueError("changed peer identity")
ips = self.get("TailscaleIPs")
if not isinstance(ips, list) or not ips or not all(isinstance(ip, str) and ip for ip in ips):
    raise ValueError("unavailable peer addresses")
for ip in ips:
    ipaddress.ip_address(ip)
if "Peer" not in data:
    raise ValueError("missing inventory")
peers = data["Peer"]
# Pinned Status.Peer is a Go map without omitempty: nil -> null, empty -> {}.
if peers is not None:
    if not isinstance(peers, dict):
        raise ValueError("invalid inventory")
    present = False
    for key, peer in peers.items():
        if not isinstance(key, str) or not key or not key.isprintable() or not isinstance(peer, dict):
            raise ValueError("invalid peer entry")
        key.encode("utf-8", "strict")
        hostname = peer.get("HostName")
        if not isinstance(hostname, str) or not hostname or not hostname.isprintable():
            raise ValueError("unclassifiable hostname")
        hostname.encode("utf-8", "strict")
        present = present or hostname == "home-sidecar-ci"
    if present:
        raise ValueError("sidecar remains visible")
' "$peer_node" 2>/dev/null || return 1
  process="$(peer_process_identity)" || return 1
  [[ "$process" == "$peer_started" ]]
}
# This later negative leg has an already enrolled identity. Offline is only a
# supporting snapshot: the single-attempt execution history below proves that
# no new sidecar process could have admitted traffic, including before READY.
negative_peer_state() {
  local online="$1" expected="$2" process observation identity observed
  process="$(peer_process_identity)" || { negative_refusal peer_process_query; return 1; }
  [[ "$process" == "$peer_started" ]] || { negative_refusal peer_process_changed; return 1; }
  observation="$( { local query_status=0; timeout --kill-after=1 3 sudo "$ts_dir/tailscale" --socket="$work/peer.sock" status --json 2>/dev/null || query_status=$?
    printf 'peer_query_status=%s\n' "$query_status" >>"$diagnostics/missing-consumed-state.log" || return 1
    (( query_status == 0 )); } | python -c '
import ipaddress,json,sys
sys.excepthook = lambda *_: print("peer_parser=refused", file=sys.stderr)
def refuse(reason):
    print("peer_refusal=" + reason, file=sys.stderr)
    raise ValueError(reason)
def unique(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            refuse("duplicate")
        result[key] = value
    return result
def invalid_constant(_):
    refuse("nonfinite")
raw = sys.stdin.buffer.read(65537)
if len(raw) > 65536:
    refuse("oversize")
data = json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=unique, parse_constant=invalid_constant)
if not isinstance(data,dict):
    refuse("root_shape")
own = data.get("Self")
if (data.get("BackendState") != "Running" or not isinstance(own,dict)
        or own.get("ID") != sys.argv[1] or own.get("HostName") != "synthetic-peer-ci"):
    refuse("observer_changed")
peers = data.get("Peer")
if not isinstance(peers,dict):
    refuse("inventory_unavailable")
matches = []
for key,peer in peers.items():
    if (not isinstance(key,str) or not key or not isinstance(peer,dict)
            or not isinstance(peer.get("HostName"),str)):
        refuse("peer_shape")
    if peer["HostName"] == "home-sidecar-ci":
        matches.append(peer)
if len(matches) != 1:
    refuse("peer_missing_or_ambiguous")
peer = matches[0]
identity = peer.get("ID")
ips = peer.get("TailscaleIPs")
if (not isinstance(identity,str) or not identity.isascii() or not identity.isalnum()
        or (sys.argv[2] != "new" and identity != sys.argv[2])):
    refuse("identity_changed")
if type(peer.get("Online")) is not bool:
    refuse("online_nonboolean")
if not isinstance(ips,list) or not ips or not all(isinstance(ip,str) and ip for ip in ips):
    refuse("addresses_unavailable")
for ip in ips:
    ipaddress.ip_address(ip)
print(identity, "true" if peer["Online"] else "false")
' "$peer_node" "$expected" 2>>"$diagnostics/missing-consumed-state.log")" || { negative_refusal peer_observation; return 1; }
  process="$(peer_process_identity)" || { negative_refusal peer_process_query; return 1; }
  [[ "$process" == "$peer_started" ]] || { negative_refusal peer_process_changed; return 1; }
  read -r identity observed <<<"$observation"
  printf 'peer_online=%s\n' "$observed" >>"$diagnostics/missing-consumed-state.log" || return 1
  # A valid observation is distinct from a requested predicate. Only the
  # PRE-attempt acquisition uses either; errors never return a settling value.
  if [[ "$online" == either ]]; then printf '%s' "$observed"; return 0; fi
  [[ "$observed" == "$online" ]] || { negative_refusal peer_state_mismatch; return 1; }
  printf '%s' "$identity"
}
negative_refusal() {
  # All callers supply fixed categories, never external bytes or raw errors.
  case "$1" in
    peer_process_query|peer_process_changed|peer_observation|peer_state_mismatch|reference_lost|live_predicate|baseline_predicate|prepare_history|prepare_boot|prepare_deadline|prepare_online|prepare_clock|prepare_sleep|prepare_stop) ;;
    *) return 1 ;;
  esac
  printf 'refusal=%s\n' "$1" >>"$diagnostics/missing-consumed-state.log"
}
negative_prepare_offline() {
  local enrolled="$1" baseline="$2" start now deadline observed attempt
  start="$(python -c 'import time; print(time.monotonic_ns() // 1000)')" || { negative_refusal prepare_clock; return 1; }
  # At most12s, and never beyond40s from holder launch. The original120s
  # holder/125s outer bound stays unchanged; no new lifetime is allocated.
  deadline=$((start + 12000000))
  (( deadline <= negative_reference_started_us + 40000000 )) || deadline=$((negative_reference_started_us + 40000000))
  for attempt in 1 2 3 4; do
    now="$(python -c 'import time; print(time.monotonic_ns() // 1000)')" || { negative_refusal prepare_clock; return 1; }
    (( now < deadline )) || { negative_refusal prepare_deadline; return 1; }
    observed="$(negative_peer_state either "$enrolled")" || return 1
    negative_unit_state "prepare-$attempt" || return 1
    [[ "$(compact_boot_id)" == "$negative_boot" ]] || { negative_refusal prepare_boot; return 1; }
    [[ "$negative_active $negative_sub $negative_result $negative_invocation $negative_ready $negative_main_started $negative_exec_pid $negative_main_pid $negative_control_pid $negative_restarts $negative_inactive_exit" == "$baseline" ]] || { negative_refusal prepare_history; return 1; }
    now="$(python -c 'import time; print(time.monotonic_ns() // 1000)')" || { negative_refusal prepare_clock; return 1; }
    (( now < deadline )) || { negative_refusal prepare_deadline; return 1; }
    printf 'prepare_attempt=%s peer_online=%s\n' "$attempt" "$observed" >>"$diagnostics/missing-consumed-state.log" || return 1
    case "$observed" in
      false) return 0 ;;
      true) ;;
      *) negative_refusal peer_observation; return 1 ;;
    esac
    (( attempt < 4 )) || break
    sleep 0.5 || { negative_refusal prepare_sleep; return 1; }
  done
  negative_refusal prepare_online
  return 1
}

negative_reference_check() {
  local stage="$1" reply
  [[ -n "${negative_ref_write:-}" && -n "${negative_ref_read:-}" ]] || { negative_refusal reference_lost; return 1; }
  # Isolate SIGPIPE if the holder has already disconnected, including when
  # called inside the EXIT cleanup; it cannot terminate the controller shell.
  (printf 'check %s\n' "$stage" >&"$negative_ref_write") || { negative_refusal reference_lost; return 1; }
  IFS= read -r -t 8 reply <&"$negative_ref_read" || { negative_refusal reference_lost; return 1; }
  [[ "$reply" == "held $run_id $stage" ]] || { negative_refusal reference_lost; return 1; }
  printf 'reference=%s held=true\n' "$stage" >>"$diagnostics/missing-consumed-state.log"
}
negative_reference_release() {
  local reply status=0 pid="${negative_ref_pid:-${N3_HISTORY_REF_PID:-}}"
  [[ "${negative_ref_done:-0}" != 1 ]] || return 0
  [[ -n "$pid" ]] || return 0
  if [[ -n "${negative_ref_write:-}" && -n "${negative_ref_read:-}" ]] \
    && (printf 'release\n' >&"$negative_ref_write") \
    && IFS= read -r -t 8 reply <&"$negative_ref_read" \
    && [[ "$reply" == "released $run_id" ]]; then
    printf 'reference=released\n' >>"$diagnostics/missing-consumed-state.log" || status=1
  else
    printf 'reference=release_unconfirmed\n' >>"$diagnostics/missing-consumed-state.log" || true
    status=1
  fi
  # EOF releases an ordinary holder. The original absolute timeout owns a
  # stuck holder's termination/reap (125s + 1s grace from launch); never signal
  # a saved numerical PID after Bash could already have reaped that child.
  if [[ -n "${negative_ref_write:-}" ]]; then exec {negative_ref_write}>&-; fi
  if [[ -n "${negative_ref_read:-}" ]]; then exec {negative_ref_read}<&-; fi
  local wait_status=0
  wait "$pid" || wait_status=$?
  printf 'reference_wait_status=%s\n' "$wait_status" >>"$diagnostics/missing-consumed-state.log" || status=1
  (( wait_status == 0 )) || status=1
  negative_ref_pid=""; negative_ref_write=""; negative_ref_read=""
  negative_ref_done=1
  return "$status"
}
negative_unit_state() {
  local stage="$1" value
  negative_reference_check "$stage-before" || return 1
  # Decode raw bounded bytes before shell substitution can erase NUL/framing
  # errors. One named-property read avoids joining different invocations.
  value="$( { local query_status=0; timeout --kill-after=1 3 systemctl show happyranch-tsnet-sidecar.service \
    -p LoadState -p Type -p ActiveState -p SubState -p Result -p InvocationID \
    -p ActiveEnterTimestampMonotonic -p ExecMainStartTimestampMonotonic \
    -p ExecMainPID -p MainPID -p ControlPID -p NRestarts \
    -p InactiveExitTimestampMonotonic 2>/dev/null || query_status=$?
    printf 'stage=%s query_status=%s\n' "$stage" "$query_status" >>"$diagnostics/missing-consumed-state.log"
    (( query_status == 0 )); } | python -c '
import re,sys
sys.excepthook = lambda *_: print("stage=" + sys.argv[1] + " parser=refused", file=sys.stderr)
raw = sys.stdin.buffer.read(4097)
if len(raw) > 4096 or not raw or any(c < 32 and c != 10 or c > 126 for c in raw):
    raise ValueError("invalid framing")
values = {}
for line in raw.decode("ascii").removesuffix("\n").split("\n"):
    key,sep,value = line.partition("=")
    if not sep or key in values:
        raise ValueError("invalid property")
    values[key] = value
keys = "ActiveState SubState Result InvocationID ActiveEnterTimestampMonotonic ExecMainStartTimestampMonotonic ExecMainPID MainPID ControlPID NRestarts InactiveExitTimestampMonotonic".split()
if set(values) != set(keys) | {"LoadState","Type"} or values["LoadState"] != "loaded" or values["Type"] != "notify":
    raise ValueError("unavailable unit")
for key in keys[:3]:
    if not re.fullmatch("[a-z][a-z-]{0,31}",values[key]):
        raise ValueError("invalid state")
if not re.fullmatch("[0-9a-f]{32}",values["InvocationID"]) or int(values["InvocationID"],16) == 0:
    raise ValueError("unavailable invocation")
for key in keys[4:]:
    if not re.fullmatch("0|[1-9][0-9]{0,17}",values[key]):
        raise ValueError("invalid number")
print(" ".join(values[key] for key in keys))
' "$stage" 2>>"$diagnostics/missing-consumed-state.log")" || { printf 'stage=%s state=unavailable\n' "$stage" >>"$diagnostics/missing-consumed-state.log"; return 1; }
  negative_reference_check "$stage-after" || return 1
  read -r negative_active negative_sub negative_result negative_invocation negative_ready negative_main_started \
    negative_exec_pid negative_main_pid negative_control_pid negative_restarts negative_inactive_exit <<<"$value"
  printf 'stage=%s state=%s\n' "$stage" "$value" >>"$diagnostics/missing-consumed-state.log"
}
negative_start_job() {
  # Only manager-authenticated completed jobs from this boot/attempt interval.
  # Keep raw prose out of diagnostics; overflow, malformed or ambiguous history
  # refuses. This read is not used as a complete process-execution history.
  timeout --kill-after=1 3 sudo journalctl --sync >/dev/null 2>&1 || return 1
  timeout --kill-after=1 3 sudo journalctl -b "$negative_boot" --since="@$negative_since" \
    --no-pager --all -o json \
    --output-fields=UNIT,JOB_ID,JOB_TYPE,JOB_RESULT,MESSAGE_ID,_PID,_UID,_BOOT_ID,__MONOTONIC_TIMESTAMP \
    JOB_TYPE=start UNIT=happyranch-tsnet-sidecar.service 2>/dev/null | python -c '
import json,re,sys
def unique(pairs):
    result = {}
    for key,value in pairs:
        if key in result:
            raise ValueError("duplicate")
        result[key] = value
    return result
def invalid_constant(_):
    raise ValueError("nonfinite")
raw = sys.stdin.buffer.read(16385)
if len(raw) > 16384 or not raw.endswith(b"\n"):
    raise ValueError("unavailable history")
jobs = []
for line in raw.splitlines():
    event = json.loads(line.decode("utf-8","strict"),object_pairs_hook=unique, parse_constant=invalid_constant)
    stamp = event.get("__MONOTONIC_TIMESTAMP")
    if (event.get("_PID") != "1" or event.get("_UID") != "0"
            or event.get("_BOOT_ID") != sys.argv[1] or not isinstance(stamp,str)
            or not re.fullmatch("[0-9]{1,18}",stamp)):
        raise ValueError("unattributed job")
    if int(stamp) < int(sys.argv[2]):
        continue
    if int(stamp) > int(sys.argv[3]):
        raise ValueError("outside interval")
    if event.get("MESSAGE_ID") == "7d4958e842da4a758f6c1cdc7b36dcc5":
        continue
    job = event.get("JOB_ID")
    if (event.get("UNIT") != "happyranch-tsnet-sidecar.service" or event.get("JOB_TYPE") != "start"
            or event.get("MESSAGE_ID") != "be02cf6855d2428ba40df7e9d022f03d"
            or event.get("JOB_RESULT") != "failed" or not isinstance(job,str)
            or not re.fullmatch("[1-9][0-9]{0,9}",job) or int(job) > 4294967295):
        raise ValueError("not an affirmative failed start")
    jobs.append(job)
if len(jobs) != 1:
    raise ValueError("ambiguous start history")
print("failed_start_job=" + jobs[0])
' "$negative_boot" "$negative_start_us" "$negative_end_us" 2>/dev/null >>"$diagnostics/missing-consumed-state.log"
}
missing_consumed_state_refused() {
  local boot reply child_read child_write python_command status=0
  boot="$(compact_boot_id)"; [[ "$boot" != unavailable ]] || return 1
  python_command="$(command -v python)" || return 1
  local negative_reference_started_us
  negative_reference_started_us="$(python -c 'import time; print(time.monotonic_ns() // 1000)')" || return 1
  negative_ref_done=0
  # A one-shot RefUnit disconnects and loses its pin. Keep this exact client
  # alive from before the live tuple through the final stopped-history fence.
  # No installed helper/unit, reconnect, reacquire or shipping unit change.
  coproc N3_HISTORY_REF { exec timeout --kill-after=1 125 sudo -n "$python_command" -u "$failure_capture_driver" --mode negative-unit-reference --boot-id "$boot" --run-id "$run_id" 2>>"$diagnostics/missing-consumed-state.log"; }
  negative_ref_pid="${N3_HISTORY_REF_PID:-}"
  child_read="${N3_HISTORY_REF[0]:-}"; child_write="${N3_HISTORY_REF[1]:-}"
  [[ -n "$negative_ref_pid" && -n "$child_read" && -n "$child_write" ]] || return 1
  exec {negative_ref_read}<&"$child_read" || { negative_reference_release || true; return 1; }
  exec {negative_ref_write}>&"$child_write" || { negative_reference_release || true; return 1; }
  exec {child_read}<&-
  exec {child_write}>&-
  if IFS= read -r -t 8 reply <&"$negative_ref_read" && [[ "$reply" == "held $run_id acquired" ]]; then
    printf 'reference=acquired boot=%s run=%s\n' "$boot" "$run_id" >>"$diagnostics/missing-consumed-state.log" || status=1
    (( status != 0 )) || missing_consumed_state_observed || status=$?
  else
    printf 'reference=acquisition_unconfirmed\n' >>"$diagnostics/missing-consumed-state.log" || true
    status=1
  fi
  # Release on every ordinary return, without replacing an initiating failure.
  negative_reference_release || { (( status != 0 )) || status=1; }
  return "$status"
}
missing_consumed_state_observed() {
  local prior_ready prior_invocation enrolled baseline_exit baseline_tuple attempt_invocation="" status=0 start_status stop_status
  local negative_boot negative_since negative_start_us negative_end_us
  negative_boot="$(compact_boot_id)"; [[ "$negative_boot" != unavailable ]] || return 1
  negative_unit_state live || return 1
  [[ "$negative_active:$negative_sub" == active:running && "$negative_ready" != 0 \
    && "$negative_main_started" != 0 && "$negative_main_pid" != 0 \
    && "$negative_exec_pid" == "$negative_main_pid" && "$negative_main_started" -le "$negative_ready" ]] || { negative_refusal live_predicate; return 1; }
  prior_ready="$negative_ready"; prior_invocation="$negative_invocation"
  enrolled="$(negative_peer_state true new)" || return 1
  timeout --kill-after=1 15 sudo systemctl stop happyranch-managed.target happyranch-tsnet-sidecar.service happyranch-connector.service || { local stop_error=$?; negative_refusal prepare_stop; return "$stop_error"; }
  negative_unit_state baseline || return 1
  [[ "$negative_active:$negative_sub" == inactive:dead && "$negative_ready" == "$prior_ready" \
    && "$negative_invocation" == "$prior_invocation" && "$negative_restarts" == 0 \
    && "$negative_main_pid:$negative_control_pid" == 0:0 ]] || { negative_refusal baseline_predicate; return 1; }
  baseline_tuple="$negative_active $negative_sub $negative_result $negative_invocation $negative_ready $negative_main_started $negative_exec_pid $negative_main_pid $negative_control_pid $negative_restarts $negative_inactive_exit"
  negative_prepare_offline "$enrolled" "$baseline_tuple" || return 1
  baseline_exit="$negative_inactive_exit"
  sudo mv /var/lib/happyranch-tsnet-sidecar/credential.consumed "$work/credential.consumed.held" || return "$?"
  absent /etc/happyranch/enrollment.key
  absent /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf
  absent /var/lib/happyranch-tsnet-sidecar/credential.consumed
  [[ "$(compact_boot_id)" == "$negative_boot" ]] || return 1
  negative_since="$(date +%s)" || return 1
  negative_start_us="$(python -c 'import time; print(time.monotonic_ns() // 1000)')" || return 1
  [[ "$prior_ready" -lt "$negative_start_us" && "$baseline_exit" -lt "$negative_start_us" ]] || return 1
  printf 'boot=%s start_us=%s\n' "$negative_boot" "$negative_start_us" >>"$diagnostics/missing-consumed-state.log" || return 1
  service_start_attempted=1
  if timeout --kill-after=1 15 sudo systemctl start happyranch-managed.target; then start_status=0; else start_status=$?; fi
  printf 'start_status=%s\n' "$start_status" >>"$diagnostics/missing-consumed-state.log" || status=1
  # Preserve pre-stop observations even on a successful/unknown start. Never
  # let their failure skip the independently attempted stop of the owned loop.
  if negative_unit_state before-stop; then
    attempt_invocation="$negative_invocation"
    case "$negative_active:$negative_sub" in failed:failed|inactive:dead|activating:auto-restart) ;; *) status=1 ;; esac
    [[ "$negative_result" == exit-code && "$negative_ready" == "$prior_ready" \
      && "$attempt_invocation" != "$prior_invocation" && "$negative_restarts" == 0 \
      && "$negative_main_started:$negative_exec_pid:$negative_main_pid:$negative_control_pid" == 0:0:0:0 \
      && "$negative_inactive_exit" -gt "$baseline_exit" && "$negative_inactive_exit" -ge "$negative_start_us" ]] || status=1
  else status=1; fi
  if timeout --kill-after=1 15 sudo systemctl stop happyranch-managed.target happyranch-tsnet-sidecar.service happyranch-connector.service; then stop_status=0; else stop_status=$?; fi
  printf 'stop_status=%s\n' "$stop_status" >>"$diagnostics/missing-consumed-state.log" || status=1
  # No reset/reload/start/unload before this history fence. Stop leaves the
  # restart count available until the NEXT start; any queued retry that raced
  # the read/stop has incremented it. A lost nonzero READY baseline refuses GC.
  negative_unit_state after-stop || status=1
  case "$negative_active:$negative_sub" in inactive:dead|failed:failed) ;; *) status=1 ;; esac
  [[ "$(compact_boot_id)" == "$negative_boot" && "$negative_result" == exit-code \
    && "$negative_ready" == "$prior_ready" && "$negative_invocation" == "$attempt_invocation" \
    && "$negative_restarts" == 0 && "$negative_main_started:$negative_exec_pid:$negative_main_pid:$negative_control_pid" == 0:0:0:0 ]] || status=1
  negative_end_us="$(python -c 'import time; print(time.monotonic_ns() // 1000)')" || status=1
  printf 'end_us=%s\n' "$negative_end_us" >>"$diagnostics/missing-consumed-state.log" || status=1
  [[ "$negative_end_us" -ge "$negative_start_us" ]] && (( negative_end_us - negative_start_us <= 45000000 )) || status=1
  negative_start_job || status=1
  if negative_peer_state false "$enrolled" >/dev/null; then
    printf 'peer_after=offline\n' >>"$diagnostics/missing-consumed-state.log" || status=1
  else
    printf 'peer_after=unconfirmed\n' >>"$diagnostics/missing-consumed-state.log" || true
    status=1
  fi
  negative_unit_state final || status=1
  case "$negative_active:$negative_sub" in inactive:dead|failed:failed) ;; *) status=1 ;; esac
  [[ "$(compact_boot_id)" == "$negative_boot" && "$negative_result" == exit-code \
    && "$negative_ready" == "$prior_ready" && "$negative_invocation" == "$attempt_invocation" \
    && "$negative_restarts" == 0 && "$negative_main_started:$negative_exec_pid:$negative_main_pid:$negative_control_pid" == 0:0:0:0 ]] || status=1
  # Status1 is the expected failed systemctl transaction, not proof by itself.
  # Preserve timeout/launch/query/stop failure as a failure, never acceptance.
  (( start_status == 1 )) || { (( start_status != 0 )) && return "$start_status"; return 1; }
  (( stop_status == 0 )) || return "$stop_status"
  (( status == 0 ))
}
systemctl_absent_value() {
  local unit="$1" property="$2" expected="$3" value
  # v255 SHOW_PROPERTIES returns0 for genuine not-found units. STATUS exits
  # 1/4 are not query success and must never admit valid-looking failed output.
  value="$(systemctl show "$unit" -p "$property" --value 2>/dev/null)" || return 1
  [[ "$value" == "$expected" ]]
}
unit_absent() {
  local unit="$1" unit_root="${N3_UNIT_ROOT:-}"
  case "$unit" in
    happyranch-managed.target|happyranch-connector.service|happyranch-tsnet-sidecar.service) ;;
    *) return 1 ;;
  esac
  systemctl_absent_value "$unit" LoadState not-found || return 1
  systemctl_absent_value "$unit" ActiveState inactive || return 1
  systemctl_absent_value "$unit" SubState dead || return 1
  # MainPID belongs to Service, not Target or the common Unit interface.
  # Keep affirmative PID absence for both services; a missing service PID
  # remains unknown, even when all common unit states report absence.
  case "$unit" in
    happyranch-connector.service|happyranch-tsnet-sidecar.service)
      systemctl_absent_value "$unit" MainPID 0 || return 1 ;;
  esac
  test_false -e "$unit_root/etc/systemd/system/$unit" && test_false -e "$unit_root/run/systemd/system/$unit" || return 1
  test_false -e "$unit_root/etc/systemd/system/$unit.d" && test_false -e "$unit_root/run/systemd/system/$unit.d" || return 1
}
cleanup_refusal() {
  # Only these fixed pairs can leave cleanup. Emit each once across all resets
  # and trap reentry: 23 shipping pairs + 24 final pairs, less than the existing 4096 bytes.
  local phase="$1" predicate="$2" key
  case "$phase" in shipping|final) ;; *) return 1 ;; esac
  case "$predicate" in
    reload_failed|fixture_delete_failed|fixture_absence_unconfirmed|unit_absence_unconfirmed|inventory_query_failed|inventory_malformed|unit_file_residue|inventory_scratch_failed|host_listener_residue|virtual_listener_residue|virtual_stop_binding_unconfirmed|virtual_removal_unconfirmed|credential_path_residue|transaction_path_residue|payload_path_residue|runtime_path_residue|process_residue|process_query_failed|stage_residue|stage_query_failed|fixture_process_residue|owned_path_unconfirmed|mainpid_unconfirmed|evidence_failed|work_path_residue) ;;
    *) return 1 ;;
  esac
  # Split only final evidence loss. Legacy residue remains a shipping term.
  case "$phase:$predicate" in
    shipping:virtual_stop_binding_unconfirmed|shipping:virtual_removal_unconfirmed|final:virtual_listener_residue) return 1 ;;
  esac
  key="$phase:$predicate"
  [[ "|${cleanup_refusals_seen:-}|" != *"|$key|"* ]] || return 0
  cleanup_refusals_seen="${cleanup_refusals_seen:-}|$key"
  printf 'n3-cleanup:%s:%s\n' "$phase" "$predicate" >&2 || true
  return 0
}
owned_unit_files_absent() {
  # A named no-match is exit1 on systemd255, indistinguishable from a query
  # failure. Enumerate ALL files successfully, then validate every complete
  # UNIT FILE / STATE / PRESET row before comparing exact owned names.
  # Keep raw bytes out of shell substitution (which strips NUL) and evidence.
  local phase="$1" inventory status=0
  inventory="$(mktemp "$work/.n3-unit-inventory.XXXXXX" 2>/dev/null)" || {
    cleanup_refusal "$phase" inventory_scratch_failed; return 1;
  }
  if LC_ALL=C SYSTEMD_COLORS=0 systemctl list-unit-files --full --no-legend --no-pager >"$inventory" 2>/dev/null; then
    LC_ALL=C awk '
      {
        if (NF != 3 || $0 ~ /[^[:print:]\t]/ ||
            $1 !~ /^[A-Za-z0-9_:.@\\-]+\.(service|socket|target|device|mount|automount|swap|timer|path|slice|scope)$/ ||
            $2 !~ /^(enabled|enabled-runtime|linked|linked-runtime|alias|masked|masked-runtime|static|disabled|indirect|generated|transient|bad)$/ ||
            $3 !~ /^(enabled|disabled|ignored|-|n\/a)$/ || seen[$1]++) malformed=1
        if ($1 == "happyranch-managed.target" || $1 == "happyranch-connector.service" ||
            $1 == "happyranch-tsnet-sidecar.service") residue=1
      }
      END { if (NR == 0 || malformed) exit 11; if (residue) exit 10 }
    ' "$inventory" 2>/dev/null || status=$?
    case "$status" in
      0) ;;
      10) cleanup_refusal "$phase" unit_file_residue ;;
      *) cleanup_refusal "$phase" inventory_malformed ;;
    esac
  else
    status=1
    cleanup_refusal "$phase" inventory_query_failed
  fi
  rm -f -- "$inventory" 2>/dev/null || { cleanup_refusal "$phase" inventory_scratch_failed; return 1; }
  (( status == 0 ))
}
diagnostics="${N3_DIAGNOSTICS_DIR:-$(mktemp -d)}"
mkdir -p "$diagnostics"
: >"$diagnostics/virtual-removal.log"
virtual_note_count=0
printf 'subject=%s\n' "${PROOF_SUBJECT_SHA:-missing}" >"$diagnostics/bootstrap.txt"
[[ -n "${PACKAGE_TAR:-}" && -f "$PACKAGE_TAR" ]] || fail "PACKAGE_TAR missing"
[[ "${PROOF_SUBJECT_SHA:-}" =~ ^[0-9a-f]{40}$ ]] || fail "PROOF_SUBJECT_SHA missing"
pid1_command="$(ps -p 1 -o comm= | xargs)" || fail "PID 1 query failed"
[[ "$pid1_command" == systemd ]] || fail "PID 1 is not systemd"
# This precedes the product cleanup trap. Own the query here, including a
# shell-only cancellation, without retaining raw output or shell-normalizing it.
preflight_pid=""; preflight_signal=0; preflight_reaped=0
preflight_finish_cancel() {
  # TERM may reach Python before initialization, leaving no child diagnostic.
  # Evidence is best effort; a write failure cannot replace the signal exit.
  if [[ ! -f "$diagnostics/system-manager-preflight.txt" ]]; then
    printf 'state=unavailable\nreason=cancelled\nquery_status=not_observed\n' >"$diagnostics/system-manager-preflight.txt" || true
  fi
  printf 'shell_reason=cancelled\nshell_status=%s\n' "$preflight_signal" >>"$diagnostics/system-manager-preflight.txt" || true
  exit "$preflight_signal"
}
preflight_cancel() {
  trap '' HUP INT TERM
  preflight_signal="$1"
  # Once the wait completed, exit synchronously even after the final flag
  # decision. Earlier cancellation uses the owned cleanup/re-wait path below.
  if (( preflight_reaped )); then
    preflight_finish_cancel
  fi
  [[ -z "$preflight_pid" ]] || kill -TERM "$preflight_pid" 2>/dev/null || true
}
trap 'preflight_cancel 129' HUP
trap 'preflight_cancel 130' INT
trap 'preflight_cancel 143' TERM
python - "$diagnostics/system-manager-preflight.txt" <<'PREFLIGHT' &
import os
import selectors
import signal
import subprocess
import sys
import time
from pathlib import Path

cancelled = 0

def cancel(signum, _frame):
    global cancelled
    cancelled = 128 + signum

for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
    signal.signal(signum, cancel)

deadline = time.monotonic() + 120
state, reason, query_status = "unavailable", "unavailable", "not_observed"
query = None
output = bytearray()
try:
    if not cancelled:
        query = subprocess.Popen(
            ["systemctl", "is-system-running", "--wait"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True,
            env={**os.environ, "LC_ALL": "C", "SYSTEMD_COLORS": "0"},
        )
        os.set_blocking(query.stdout.fileno(), False)
        with selectors.DefaultSelector() as selector:
            selector.register(query.stdout, selectors.EVENT_READ)
            eof = False
            while True:
                if cancelled:
                    reason = "cancelled"
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    state, reason = "unavailable", "timeout"
                    break
                if not eof:
                    for _key, _events in selector.select(min(remaining, 0.1)):
                        chunk = os.read(query.stdout.fileno(), 33 - len(output))
                        output.extend(chunk)
                        if not chunk:
                            eof = True
                            selector.unregister(query.stdout)
                else:
                    time.sleep(min(remaining, 0.1))
                if len(output) > 32:
                    state, reason = "unknown", "malformed"
                    break
                status = query.poll()
                if eof and status is not None:
                    query_status = status
                    # v255 puts exactly one LF-terminated state. No decoding,
                    # whitespace stripping or Bash substitution may admit it.
                    states = ("running", "degraded", "initializing", "starting",
                              "maintenance", "stopping", "offline", "unknown")
                    state = next((s for s in states if output == (s + "\n").encode("ascii")), "unknown")
                    if not output and status != 0:
                        state, reason = "unavailable", "unavailable"
                    elif output != (state + "\n").encode("ascii"):
                        reason = "malformed"
                    elif (state == "running" and status == 0) or (state == "degraded" and status == 1):
                        reason = "ready"
                    elif state in ("running", "degraded"):
                        reason = "contradictory"
                    elif status != 1:
                        reason = "query_failed"
                    else:
                        reason = "unknown" if state == "unknown" else "not_ready"
                    # Timely bytes alone cannot admit a late exit/validation.
                    if time.monotonic() >= deadline:
                        state, reason = "unavailable", "timeout"
                    break
except OSError:
    reason = "unavailable" if query is None else "query_failed"
finally:
    if query is not None:
        try:
            if query.poll() is None:
                query.kill()
            query_status = query.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            reason = "reap_failed"
        query.stdout.close()
    if cancelled and reason != "reap_failed":
        reason = "cancelled"
    if reason == "ready" and time.monotonic() >= deadline:
        state, reason = "unavailable", "timeout"

# No scratch or upstream stdout/stderr enters the always-uploaded diagnostics.
try:
    Path(sys.argv[1]).write_text(
        f"state={state}\nreason={reason}\nquery_status={query_status}\n", encoding="ascii",
    )
except OSError:
    reason = "evidence_unavailable"
print(f"n3-system-manager:{state}:{reason}", file=sys.stderr)
raise SystemExit(cancelled or (0 if reason == "ready" else 1))
PREFLIGHT
preflight_pid=$!
# A signal between trap installation and PID assignment is not lost.
if (( preflight_signal != 0 )); then
  kill -TERM "$preflight_pid" 2>/dev/null || true
fi
preflight_status=0
wait "$preflight_pid" || preflight_status=$?
# If cancellation interrupted wait, its handler already ignores further
# signals and the branch below confirms reap. Otherwise the child is reaped;
# any signal from here until trap removal must exit synchronously.
preflight_reaped=1
if (( preflight_signal != 0 )); then
  # The interrupted Bash wait did not reap the child; its own kill/reap has
  # a one-second allowance beyond the 120-second observation deadline.
  wait "$preflight_pid" 2>/dev/null || true
  preflight_finish_cancel
fi
trap - HUP INT TERM
case "$preflight_status" in
  0) ;;
  129|130|143) exit "$preflight_status" ;;
  *) fail "system manager unavailable" ;;
esac
sudo -n true || fail "passwordless sudo unavailable"
sudo systemd-run --quiet --wait --collect --unit=happyranch-n3-qualification /bin/true || fail "transient units unavailable"

work="$(mktemp -d)"
evidence_driver="$PWD/app/linux/package/n3_evidence.py"
failure_capture_driver="$PWD/app/linux/package/n3_failure_capture.py"
evidence_artifact="$diagnostics/execution-evidence.json"
run_id="$(cat /proc/sys/kernel/random/uuid)"
barrier_dir="/var/lib/happyranch-tsnet-sidecar/.n3-barrier-$run_id"
capture_window_since_us="$(date +%s%N 2>/dev/null | cut -c1-16)" || capture_window_since_us=0
package_sha="$(sha256sum "$PACKAGE_TAR" | cut -d' ' -f1)" || fail "package digest query failed"
[[ "$package_sha" =~ ^[0-9a-f]{64}$ ]] || fail "package digest malformed"
python "$evidence_driver" init "$evidence_artifact" --git-head "$PROOF_SUBJECT_SHA" --package-sha256 "$package_sha" --run-id "$run_id"
headscale_pid=""; peer_pid=""; daemon_pid=""
service_start_attempted=0
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
  value="$(date +%s%N 2>/dev/null | cut -c1-16)" || value=0
  [[ "$value" =~ ^[0-9]{16}$ ]] || value=0
  printf '%s' "$value"
}
compact_boot_id() {
  local value
  value="$(tr -d '-' </proc/sys/kernel/random/boot_id 2>/dev/null | tr '[:upper:]' '[:lower:]')" || value=unavailable
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
  timeout --kill-after=1 "$observe_timeout_seconds" sudo sh -c 'test "$1" "$2"; status=$?; case "$status" in 0) exit 0;; 1) exit 3;; *) exit 2;; esac' sh "$kind" "$path"
  status=$?
  (( restore_errexit )) && set -e || set +e
  case "$status" in
    0) presence_value=true; presence_loss=observed_present ;;
    3) presence_value=false; presence_loss=observed_absent ;;
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
  local -a pipeline_status
  observation_value=unknown; observation_loss=unattempted
  observe_remaining || return 0
  (( observe_bytes_left < cap )) && cap="$observe_bytes_left"
  file="$(mktemp "${work:-${TMPDIR:-/tmp}}/.n3-observe.XXXXXX")" || { observation_loss=launch_failure; return; }
  capture_raw_files+=("$file")
  case "$-" in *e*) restore_errexit=1 ;; esac
  set +e
  timeout --kill-after=1 "$observe_timeout_seconds" systemctl show "$unit" -p "$property" --value 2>/dev/null | head -c "$((cap + 1))" >"$file"
  pipeline_status=("${PIPESTATUS[@]}")
  status="${pipeline_status[0]}"
  (( status != 0 || pipeline_status[1] == 0 )) || status="${pipeline_status[1]}"
  (( restore_errexit )) && set -e || set +e
  value="$(<"$file")" || { observation_loss=query_error; rm -f "$file"; return; }
  bytes="$(wc -c <"$file")" || { observation_loss=query_error; rm -f "$file"; return; }
  [[ "$bytes" =~ ^(0|[1-9][0-9]{0,17})$ ]] || { observation_loss=query_error; rm -f "$file"; return; }
  rm -f "$file"
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
  local journal_file="" helper_file="" field_file="" status bytes now_us field field_status field_bytes restore_errexit=0
  local -a pipeline_status
  case "$-" in *e*) restore_errexit=1 ;; esac
  completed_jobs='[]'; jobs_loss=unattempted
  observe_remaining || return 0
  journal_file="$(mktemp "${work:-${TMPDIR:-/tmp}}/.n3-jobs.XXXXXX")" || { jobs_loss=launch_failure; return; }
  capture_raw_files+=("$journal_file")
  set +e
  timeout --kill-after=1 "$observe_timeout_seconds" journalctl -b "$snapshot_boot_id" --since "@$snapshot_since_seconds" --output=json --output-fields=UNIT,JOB_ID,JOB_TYPE,JOB_RESULT,MESSAGE_ID,_PID,_UID,_BOOT_ID,__REALTIME_TIMESTAMP --no-pager JOB_TYPE=start UNIT=happyranch-managed.target UNIT=happyranch-connector.service UNIT=happyranch-tsnet-sidecar.service 2>/dev/null | head -c "$((observe_bytes_left + 1))" >"$journal_file"
  pipeline_status=("${PIPESTATUS[@]}")
  status="${pipeline_status[0]}"
  (( status != 0 || pipeline_status[1] == 0 )) || status="${pipeline_status[1]}"
  (( restore_errexit )) && set -e || set +e
  bytes="$(wc -c <"$journal_file")" || { completed_jobs='[]'; jobs_loss=query_error; rm -f "$journal_file" "$helper_file" "$field_file"; return; }
  [[ "$bytes" =~ ^(0|[1-9][0-9]{0,17})$ ]] || { completed_jobs='[]'; jobs_loss=query_error; rm -f "$journal_file" "$helper_file" "$field_file"; return; }
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
  bytes="$(wc -c <"$helper_file")" || { completed_jobs='[]'; jobs_loss=query_error; rm -f "$journal_file" "$helper_file" "$field_file"; return; }
  [[ "$bytes" =~ ^(0|[1-9][0-9]{0,17})$ ]] || { completed_jobs='[]'; jobs_loss=query_error; rm -f "$journal_file" "$helper_file" "$field_file"; return; }
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
    field_bytes="$(wc -c <"$field_file")" || { completed_jobs='[]'; jobs_loss=query_error; rm -f "$journal_file" "$helper_file" "$field_file"; return; }
    [[ "$field_bytes" =~ ^(0|[1-9][0-9]{0,17})$ ]] || { completed_jobs='[]'; jobs_loss=query_error; rm -f "$journal_file" "$helper_file" "$field_file"; return; }
    if (( field_bytes > observe_bytes_left )); then observe_bytes_left=0; rm -f "$journal_file" "$helper_file" "$field_file"; jobs_loss=truncated; return; fi
    observe_bytes_left=$((observe_bytes_left - field_bytes))
    if (( field_status == 124 )); then rm -f "$journal_file" "$helper_file" "$field_file"; jobs_loss=timeout; return; fi
    if (( field_status != 0 )); then rm -f "$journal_file" "$helper_file" "$field_file"; jobs_loss=parse_loss; return; fi
    if [[ "$field" == jobs ]]; then completed_jobs="$(<"$field_file")" || { completed_jobs='[]'; jobs_loss=query_error; rm -f "$journal_file" "$helper_file" "$field_file"; return; }; else jobs_loss="$(<"$field_file")" || { completed_jobs='[]'; jobs_loss=query_error; rm -f "$journal_file" "$helper_file" "$field_file"; return; }; fi
    rm -f "$field_file"
  done
  rm -f "$journal_file" "$helper_file"
}
capture_sidecar_failure_lines() {
  # This is the first failure observation and owns its whole allowance. Filter
  # journal prose in-stream before either cap so unrelated output cannot evict
  # a category-only receipt or the two closed sidecar exit messages.
  local filtered_file json_file status filter_status bytes lines json_status restore_errexit=0
  local -a pipeline_status
  case "$-" in *e*) restore_errexit=1 ;; esac
  sidecar_failure_lines='[]'; sidecar_failure_lines_loss=unattempted
  filtered_file="$(mktemp "$diagnostics/.n3-sidecar-lines.XXXXXX")" || { sidecar_failure_lines_loss=launch_failure; return; }
  json_file="$(mktemp "$diagnostics/.n3-sidecar-lines-json.XXXXXX")" || { rm -f "$filtered_file"; sidecar_failure_lines_loss=launch_failure; return; }
  set +e
  timeout --kill-after=1 3 journalctl -u happyranch-tsnet-sidecar.service -b "$snapshot_boot_id" --since "@$snapshot_since_journal" --until "@$sidecar_failure_until_journal" -o cat --no-pager 2>/dev/null \
    | timeout --kill-after=1 3 python "$failure_capture_driver" --mode plain 2>/dev/null \
    | head -n 33 \
    | head -c 4097 >"$filtered_file"
  pipeline_status=("${PIPESTATUS[@]}")
  (( restore_errexit )) && set -e || set +e
  status="${pipeline_status[0]}"
  filter_status="${pipeline_status[1]}"
  (( status != 0 || (pipeline_status[2] == 0 && pipeline_status[3] == 0) )) || status=1
  bytes="$(wc -c <"$filtered_file")" || { sidecar_failure_lines='[]'; sidecar_failure_lines_loss=query_error; rm -f "$filtered_file" "$json_file"; return; }
  [[ "$bytes" =~ ^(0|[1-9][0-9]{0,17})$ ]] || { sidecar_failure_lines='[]'; sidecar_failure_lines_loss=query_error; rm -f "$filtered_file" "$json_file"; return; }
  lines="$(wc -l <"$filtered_file")" || { sidecar_failure_lines='[]'; sidecar_failure_lines_loss=query_error; rm -f "$filtered_file" "$json_file"; return; }
  [[ "$lines" =~ ^(0|[1-9][0-9]{0,17})$ ]] || { sidecar_failure_lines='[]'; sidecar_failure_lines_loss=query_error; rm -f "$filtered_file" "$json_file"; return; }
  if (( bytes > 4096 || lines > 32 )); then
    rm -f "$filtered_file" "$json_file"
    sidecar_failure_lines_loss=truncated
    return
  fi
  if (( status == 124 )); then
    rm -f "$filtered_file" "$json_file"
    sidecar_failure_lines_loss=timeout
    return
  fi
  if (( status != 0 )); then
    rm -f "$filtered_file" "$json_file"
    sidecar_failure_lines_loss=query_error
    return
  fi
  if (( filter_status == 124 || filter_status == 137 )); then
    rm -f "$filtered_file" "$json_file"
    sidecar_failure_lines_loss=timeout
    return
  fi
  if (( filter_status != 0 && filter_status != 141 )); then
    rm -f "$filtered_file" "$json_file"
    sidecar_failure_lines_loss=parse_loss
    return
  fi
  if (( bytes == 0 )); then
    rm -f "$filtered_file" "$json_file"
    sidecar_failure_lines_loss=empty
    return
  fi
  set +e
  timeout --kill-after=1 1 python -c 'import json,sys; lines=open(sys.argv[1], encoding="utf-8").read().splitlines(); assert all(line in {"readiness_unavailable", "watchdog_unavailable"} or line.startswith("diagnostic_receipt={") and line.endswith("}") for line in lines); print(json.dumps(lines,separators=(",",":")))' "$filtered_file" >"$json_file" 2>/dev/null
  json_status=$?
  (( restore_errexit )) && set -e || set +e
  rm -f "$filtered_file"
  if (( json_status == 124 )); then rm -f "$json_file"; sidecar_failure_lines_loss=timeout; return; fi
  if (( json_status != 0 )); then rm -f "$json_file"; sidecar_failure_lines_loss=parse_loss; return; fi
  sidecar_failure_lines="$(<"$json_file")" || { sidecar_failure_lines='[]'; sidecar_failure_lines_loss=query_error; rm -f "$filtered_file" "$json_file"; return; }
  rm -f "$json_file"
  sidecar_failure_lines_loss=observed
}
capture_diagnostic_receipts() {
  # The sidecar is the sole diagnostic receipt producer. Journal records are
  # accepted only when their unit, invocation, boot, and collection window all
  # match this failing run; the Python helper emits no journal prose.
  local invocation_file="" journal_file="" helper_file="" field_file="" invocation status bytes now_us field_status field_bytes field restore_errexit=0
  local -a pipeline_status
  case "$-" in *e*) restore_errexit=1 ;; esac
  diagnostic_receipts='[]'; diagnostic_receipt_loss='["unattempted"]'
  observe_remaining || return 0
  invocation_file="$(mktemp "${work:-${TMPDIR:-/tmp}}/.n3-invocation.XXXXXX")" || { diagnostic_receipt_loss='["launch_failure"]'; return; }
  capture_raw_files+=("$invocation_file")
  set +e
  timeout --kill-after=1 "$observe_timeout_seconds" systemctl show happyranch-tsnet-sidecar.service -p InvocationID --value 2>/dev/null | head -c 65 >"$invocation_file"
  pipeline_status=("${PIPESTATUS[@]}")
  status="${pipeline_status[0]}"
  (( status != 0 || pipeline_status[1] == 0 )) || status="${pipeline_status[1]}"
  (( restore_errexit )) && set -e || set +e
  invocation="$(<"$invocation_file")" || { diagnostic_receipts='[]'; diagnostic_receipt_loss='["query_error"]'; rm -f "$invocation_file" "$journal_file" "$helper_file" "$field_file"; return; }
  bytes="$(wc -c <"$invocation_file")" || { diagnostic_receipts='[]'; diagnostic_receipt_loss='["query_error"]'; rm -f "$invocation_file" "$journal_file" "$helper_file" "$field_file"; return; }
  [[ "$bytes" =~ ^(0|[1-9][0-9]{0,17})$ ]] || { diagnostic_receipts='[]'; diagnostic_receipt_loss='["query_error"]'; rm -f "$invocation_file" "$journal_file" "$helper_file" "$field_file"; return; }
  rm -f "$invocation_file"
  if (( bytes > 64 )); then observe_bytes_left=0; diagnostic_receipt_loss='["truncated"]'; return; fi
  observe_bytes_left=$((observe_bytes_left - bytes))
  if (( status == 124 )); then diagnostic_receipt_loss='["timeout"]'; return; fi
  if (( status != 0 )); then diagnostic_receipt_loss='["query_error"]'; return; fi
  if [[ -z "$invocation" ]]; then diagnostic_receipt_loss='["empty"]'; return; fi
  invocation="${invocation//-/}"
  [[ "$invocation" =~ ^[0-9a-fA-F]{32}$ ]] || { diagnostic_receipt_loss='["parse_loss"]'; return; }
  observe_remaining || { diagnostic_receipt_loss='["unattempted"]'; return; }
  journal_file="$(mktemp "${work:-${TMPDIR:-/tmp}}/.n3-receipts.XXXXXX")" || { diagnostic_receipt_loss='["launch_failure"]'; return; }
  capture_raw_files+=("$journal_file")
  set +e
  timeout --kill-after=1 "$observe_timeout_seconds" journalctl -u happyranch-tsnet-sidecar.service -b "$snapshot_boot_id" --since "@$snapshot_since_seconds" --output=json --no-pager 2>/dev/null | head -c "$((observe_bytes_left + 1))" >"$journal_file"
  pipeline_status=("${PIPESTATUS[@]}")
  status="${pipeline_status[0]}"
  (( status != 0 || pipeline_status[1] == 0 )) || status="${pipeline_status[1]}"
  (( restore_errexit )) && set -e || set +e
  bytes="$(wc -c <"$journal_file")" || { diagnostic_receipts='[]'; diagnostic_receipt_loss='["query_error"]'; rm -f "$invocation_file" "$journal_file" "$helper_file" "$field_file"; return; }
  [[ "$bytes" =~ ^(0|[1-9][0-9]{0,17})$ ]] || { diagnostic_receipts='[]'; diagnostic_receipt_loss='["query_error"]'; rm -f "$invocation_file" "$journal_file" "$helper_file" "$field_file"; return; }
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
  bytes="$(wc -c <"$helper_file")" || { diagnostic_receipts='[]'; diagnostic_receipt_loss='["query_error"]'; rm -f "$invocation_file" "$journal_file" "$helper_file" "$field_file"; return; }
  [[ "$bytes" =~ ^(0|[1-9][0-9]{0,17})$ ]] || { diagnostic_receipts='[]'; diagnostic_receipt_loss='["query_error"]'; rm -f "$invocation_file" "$journal_file" "$helper_file" "$field_file"; return; }
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
    field_bytes="$(wc -c <"$field_file")" || { diagnostic_receipts='[]'; diagnostic_receipt_loss='["query_error"]'; rm -f "$invocation_file" "$journal_file" "$helper_file" "$field_file"; return; }
    [[ "$field_bytes" =~ ^(0|[1-9][0-9]{0,17})$ ]] || { diagnostic_receipts='[]'; diagnostic_receipt_loss='["query_error"]'; rm -f "$invocation_file" "$journal_file" "$helper_file" "$field_file"; return; }
    if (( field_bytes > observe_bytes_left )); then observe_bytes_left=0; rm -f "$journal_file" "$helper_file" "$field_file"; diagnostic_receipt_loss='["truncated"]'; return; fi
    observe_bytes_left=$((observe_bytes_left - field_bytes))
    if (( field_status == 124 )); then rm -f "$journal_file" "$helper_file" "$field_file"; diagnostic_receipt_loss='["timeout"]'; return; fi
    if (( field_status != 0 )); then rm -f "$journal_file" "$helper_file" "$field_file"; diagnostic_receipt_loss='["parse_loss"]'; return; fi
    if [[ "$field" == receipts ]]; then diagnostic_receipts="$(<"$field_file")" || { diagnostic_receipts='[]'; diagnostic_receipt_loss='["query_error"]'; rm -f "$invocation_file" "$journal_file" "$helper_file" "$field_file"; return; }; else diagnostic_receipt_loss="$(<"$field_file")" || { diagnostic_receipts='[]'; diagnostic_receipt_loss='["query_error"]'; rm -f "$invocation_file" "$journal_file" "$helper_file" "$field_file"; return; }; fi
    rm -f "$field_file"
  done
  rm -f "$journal_file" "$helper_file"
}
capture_headscale_fixture() {
  # Failure-only, read-only and before teardown. The helper owns independent
  # PID/read/query/parser deadlines and terminates each child process group.
  local safe_file value bytes status=0 restore_errexit=0
  case "$-" in *e*) restore_errexit=1 ;; esac
  headscale_observation='{"process_state":"unknown","log":{"events":[],"losses":["unattempted"]},"nodes":{"peer":{"count":null,"state":"unknown"},"sidecar":{"count":null,"state":"unknown"},"losses":["unattempted"]}}'
  safe_file="$(mktemp "$diagnostics/.n3-headscale-safe.XXXXXX")" || return 0
  set +e
  timeout --kill-after=1 16 python "$failure_capture_driver" --mode headscale --work "${work:-}" --headscale-pid "${headscale_pid:-}" >"$safe_file" 2>/dev/null
  status=$?
  (( restore_errexit )) && set -e || set +e
  bytes="$(wc -c <"$safe_file")" || { headscale_observation='{"process_state":"unknown","log":{"events":[],"losses":["query_error"]},"nodes":{"peer":{"count":null,"state":"unknown"},"sidecar":{"count":null,"state":"unknown"},"losses":["query_error"]}}'; rm -f "$safe_file"; return 0; }
  [[ "$bytes" =~ ^(0|[1-9][0-9]{0,17})$ ]] || { headscale_observation='{"process_state":"unknown","log":{"events":[],"losses":["query_error"]},"nodes":{"peer":{"count":null,"state":"unknown"},"sidecar":{"count":null,"state":"unknown"},"losses":["query_error"]}}'; rm -f "$safe_file"; return 0; }
  if (( status == 0 )) && [[ -s "$safe_file" ]] && (( bytes <= 4096 )); then
    if value="$(<"$safe_file")"; then
      headscale_observation="$value"
    else
      headscale_observation='{"process_state":"unknown","log":{"events":[],"losses":["query_error"]},"nodes":{"peer":{"count":null,"state":"unknown"},"sidecar":{"count":null,"state":"unknown"},"losses":["query_error"]}}'
    fi
  else
    local loss=launch_failure
    (( status != 124 )) || loss=timeout
    headscale_observation="{\"process_state\":\"unknown\",\"log\":{\"events\":[],\"losses\":[\"$loss\"]},\"nodes\":{\"peer\":{\"count\":null,\"state\":\"unknown\"},\"sidecar\":{\"count\":null,\"state\":\"unknown\"},\"losses\":[\"$loss\"]}}"
  fi
  rm -f "$safe_file"
}
capture_connector_journal() {
  # Reserved BEFORE the live process sample; journal identity survives exit.
  # Fixed labels only. The Python worker owns/reaps its journal child group.
  local safe_file value bytes status=0 restore_errexit=0 loss=launch_failure python_command
  connector_journal='{"unit":"happyranch-connector.service","events":[],"original_invocation":null,"attribution":"window_only","losses":["unattempted"],"tail_complete":false}'
  case "$-" in *e*) restore_errexit=1 ;; esac
  safe_file="$(mktemp "$diagnostics/.n3-connector-journal.XXXXXX")" || {
    connector_journal="${connector_journal//unattempted/launch_failure}"; return 0
  }
  python_command="$(command -v python)" || { rm -f "$safe_file"; connector_journal="${connector_journal//unattempted/query_error}"; return 0; }
  set +e
  timeout --kill-after=1 4 sudo -n "$python_command" "$failure_capture_driver" --mode connector-journal --boot-id "$snapshot_boot_id" --since-us "$snapshot_since_us" --until-us "$sidecar_failure_until_us" >"$safe_file" 2>/dev/null
  status=$?
  (( restore_errexit )) && set -e || set +e
  bytes="$(wc -c <"$safe_file")" || { rm -f "$safe_file"; connector_journal="${connector_journal//unattempted/query_error}"; return 0; }
  if [[ ! "$bytes" =~ ^(0|[1-9][0-9]{0,17})$ ]]; then loss=query_error
  elif (( status == 124 || status == 137 )); then loss=timeout
  elif (( status != 0 )); then loss=query_error
  elif (( bytes > 2048 )); then loss=truncated
  elif (( bytes == 0 )); then loss=parse_loss
  elif value="$(<"$safe_file")"; then
    connector_journal="$value"; rm -f "$safe_file"; return 0
  else loss=query_error
  fi
  connector_journal="${connector_journal//unattempted/$loss}"
  rm -f "$safe_file"
}
capture_connector_instance() {
  # Own a reserved section, independent of sidecar/Headscale caps. Read only
  # the original unit/process tree and staged-file numeric mode in its root
  # view. Existing budgets/privilege boundary stay fixed; no token reads.
  local safe_file value bytes status=0 restore_errexit=0 python_command loss=launch_failure
  connector_observation='{"unit":"happyranch-connector.service","binding":null,"listening":null,"bound_port":null,"status":"unavailable","loss":"unattempted","credential_mode":{"location":"systemd-daemon-token","namespace":"connector-service-root","mode":null,"observed_at_us":null,"loss":"unattempted"},"collection":{"observation_budget_seconds":6,"command_budget_seconds":8,"kill_grace_seconds":1,"output_cap_bytes":2048},"readiness_gates":{"daemon_loopback":{"ok":null,"loss":"unavailable_internal"},"credential_permissions":{"ok":null,"loss":"unavailable_internal"},"current_policy":{"ok":null,"loss":"unavailable_internal"},"bind_identity":{"ok":null,"loss":"unavailable_internal"},"trust_state":{"ok":null,"loss":"unavailable_internal"}}}'
  case "$-" in *e*) restore_errexit=1 ;; esac
  safe_file="$(mktemp "$diagnostics/.n3-connector-safe.XXXXXX")" || {
    connector_observation="${connector_observation//\"loss\":\"unattempted\"/\"loss\":\"launch_failure\"}"
    return 0
  }
  python_command="$(command -v python)" || { rm -f "$safe_file"; connector_observation="${connector_observation//\"loss\":\"unattempted\"/\"loss\":\"query_error\"}"; return 0; }
  set +e
  # 6s internal live sample plus margin and 1s kill grace; the other 5s
  # and 2048B of the original connector allowance are reserved for journal labels.
  timeout --kill-after=1 8 sudo -n "$python_command" "$failure_capture_driver" --mode connector >"$safe_file" 2>/dev/null
  status=$?
  (( restore_errexit )) && set -e || set +e
  bytes="$(wc -c <"$safe_file")" || { connector_observation="${connector_observation//\"loss\":\"unattempted\"/\"loss\":\"query_error\"}"; rm -f "$safe_file"; return 0; }
  [[ "$bytes" =~ ^(0|[1-9][0-9]{0,17})$ ]] || { connector_observation="${connector_observation//\"loss\":\"unattempted\"/\"loss\":\"query_error\"}"; rm -f "$safe_file"; return 0; }
  if (( status == 0 )) && [[ -s "$safe_file" ]] && (( bytes <= 2048 )); then
    if value="$(<"$safe_file")"; then
      connector_observation="$value"
    else
      connector_observation="${connector_observation//\"loss\":\"unattempted\"/\"loss\":\"query_error\"}"
    fi
  else
    if (( status == 124 || status == 137 )); then loss=timeout
    elif (( bytes > 2048 )); then loss=truncated
    elif (( status != 0 )); then loss=query_error
    else loss=parse_loss
    fi
    connector_observation="${connector_observation//\"loss\":\"unattempted\"/\"loss\":\"$loss\"}"
  fi
  rm -f "$safe_file"
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
  snapshot_since_journal="${snapshot_since_us:0:10}.${snapshot_since_us:10:6}"
  snapshot_boot_id="$(compact_boot_id)"
  sidecar_failure_until_us="$(capture_now_us)"
  (( sidecar_failure_until_us >= snapshot_since_us )) || sidecar_failure_until_us="$snapshot_since_us"
  sidecar_failure_until_journal="${sidecar_failure_until_us:0:10}.${sidecar_failure_until_us:10:6}"

  # Founder seq305 ordering: the independently reserved, filter-first sidecar
  # text channel precedes every JSON, connector, target, and credential read.
  # Sidecar state, completed jobs, and the invocation-attributed JSON receipt
  # continue to precede connector, target, and credential observations.
  sidecar_failure_lines='[]'; sidecar_failure_lines_loss=unattempted
  if [[ "$name" == first-positive-start-failure || "$name" == failure-before-teardown ]]; then
    capture_sidecar_failure_lines
  fi
  begin_observation_section 8 1024
  observe_systemctl_value happyranch-tsnet-sidecar.service ActiveState; side_active="$observation_value"; side_active_loss="$observation_loss"
  observe_systemctl_value happyranch-tsnet-sidecar.service SubState; side_sub="$observation_value"; side_sub_loss="$observation_loss"
  observe_systemctl_value happyranch-tsnet-sidecar.service Result; side_result="$observation_value"; side_result_loss="$observation_loss"
  observe_systemctl_value happyranch-tsnet-sidecar.service ExecMainStatus; side_main="$observation_value"; side_main_loss="$observation_loss"
  begin_observation_section 8 8192
  capture_completed_jobs
  begin_observation_section 8 8192
  capture_diagnostic_receipts
  connector_observation='{"unit":"happyranch-connector.service","binding":null,"listening":null,"bound_port":null,"status":"unavailable","loss":"unattempted","credential_mode":{"location":"systemd-daemon-token","namespace":"connector-service-root","mode":null,"observed_at_us":null,"loss":"unattempted"},"collection":{"observation_budget_seconds":6,"command_budget_seconds":8,"kill_grace_seconds":1,"output_cap_bytes":2048},"readiness_gates":{}}'
  connector_journal='{"unit":"happyranch-connector.service","events":[],"original_invocation":null,"attribution":"window_only","losses":["unattempted"],"tail_complete":false}'
  headscale_observation='{"process_state":"unknown","log":{"events":[],"losses":["unattempted"]},"nodes":{"peer":{"count":null,"state":"unknown"},"sidecar":{"count":null,"state":"unknown"},"losses":["unattempted"]}}'
  if [[ "$name" == first-positive-start-failure || "$name" == failure-before-teardown ]]; then
    capture_connector_journal
    capture_connector_instance
    capture_headscale_fixture
  fi

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
    printf '},"jobs":%s,"diagnostic_receipts":%s,"sidecar_failure_lines":%s,"credential_presence":{"source":%s,"held_source":%s,"consumed_marker":%s,"transient_dropin":%s,"staged_directory":%s},"collection":{"run_id":"%s","boot_id":"%s","window_start_us":%s,"window_end_us":%s,"journal_since_epoch_seconds":%s,"window_seconds":45,"output_cap_bytes":24064,"legacy_aggregate_metadata":true,"scheduled_section_allowance_seconds":82,"section_accounted_bytes":32256,"aggregate_cap_enforced":false,"budget_model":"reserved-sections","connector_journal_capture":{"command_budget_seconds":4,"kill_grace_seconds":1,"raw_cap_bytes":65536,"frame_cap_bytes":8192,"frame_cap":256,"event_cap":6,"output_cap_bytes":2048},"connector_process_capture":{"command_budget_seconds":8,"kill_grace_seconds":1,"output_cap_bytes":2048},"source":"systemctl-filtered-sidecar-text-and-attributed-systemd-journals","sidecar_failure_capture":{"unit":"happyranch-tsnet-sidecar.service","window_start_us":%s,"window_end_us":%s,"line_cap":32,"output_cap_bytes":4096}},"observation_loss":{' "$completed_jobs" "$diagnostic_receipts" "$sidecar_failure_lines" "$source" "$held" "$marker" "$dropin" "$staging" "${run_id:-unknown}" "$snapshot_boot_id" "$snapshot_since_us" "$snapshot_until_us" "$snapshot_since_seconds" "$snapshot_since_us" "$sidecar_failure_until_us"
    for property_loss in "credential.source:$source_loss" "credential.held_source:$held_loss" "credential.consumed_marker:$marker_loss" "credential.transient_dropin:$dropin_loss" "credential.staged_directory:$staging_loss"; do
      [[ "$property_loss" == *:observed_present || "$property_loss" == *:observed_absent ]] && continue
      (( loss_first )) || printf ',' >>"$losses_file"; loss_first=0
      printf '"%s":"%s"' "${property_loss%%:*}" "${property_loss#*:}" >>"$losses_file"
    done
    cat "$losses_file"
    [[ "$jobs_loss" == observed ]] || { (( loss_first )) || printf ','; loss_first=0; printf '"jobs":"%s"' "$jobs_loss"; }
    (( loss_first )) || printf ','
    printf '"diagnostic_receipts":%s,"sidecar_failure_lines":"%s"},"connector_journal":%s,"connector":%s,"headscale":%s}\n' "$diagnostic_receipt_loss" "$sidecar_failure_lines_loss" "$connector_journal" "$connector_observation" "$headscale_observation"
  } >"$snapshot" || true
  rm -f "$losses_file"
}
start_managed_target() {
  # Keep the initiating positive-start status authoritative even when bounded
  # capture cannot run; EXIT cleanup preserves this same status in turn.
  # This is intentionally before the start invocation: a sidecar receipt can
  # be emitted before systemctl returns the failing positive-start status.
  capture_window_since_us="$(capture_now_us)"
  service_start_attempted=1
  if sudo systemctl start happyranch-managed.target; then
    return 0
  else
    local start_status=$?
  fi
  capture_failure_snapshot first-positive-start-failure || true
  return "$start_status"
}
cleanup() {
  local original_status="${1:-$?}" cleanup_failed=0 raw_file stage_paths
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
  # Cancellation may interrupt the private reference holder. Release it even
  # when evidence failed; no holder failure may suppress the remaining stops.
  negative_reference_release || cleanup_failed=1
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
  bind_stop_instance final || { cleanup_failed=1; cleanup_refusal final virtual_stop_binding_unconfirmed; }
  sudo systemctl stop happyranch-managed.target || true
  virtual_removed final || { cleanup_failed=1; cleanup_refusal final virtual_removal_unconfirmed; }
  (( cleanup_failed != 0 )) || evidence cleanup virtual_admission_removed_while_peer_alive || { cleanup_failed=1; cleanup_refusal final evidence_failed; }
  sudo systemctl disable happyranch-managed.target || true
  sudo systemctl reset-failed happyranch-connector.service happyranch-tsnet-sidecar.service happyranch-managed.target || true
  sudo rm -rf /etc/systemd/system/happyranch-tsnet-sidecar.service.d || { cleanup_failed=1; cleanup_refusal final owned_path_unconfirmed; }
  sudo rm -f /etc/systemd/system/happyranch-connector.service /etc/systemd/system/happyranch-tsnet-sidecar.service /etc/systemd/system/happyranch-managed.target || { cleanup_failed=1; cleanup_refusal final owned_path_unconfirmed; }
  sudo systemctl daemon-reload || { cleanup_failed=1; cleanup_refusal final reload_failed; }
  for pid in "$peer_pid" "$daemon_pid" "$headscale_pid"; do
    [[ -z "$pid" ]] || sudo kill "$pid"
  done
  for pid in "$peer_pid" "$daemon_pid" "$headscale_pid"; do
    if [[ -n "$pid" ]]; then
      # The primary failure may be the fixture exiting before teardown. Reap
      # it, but determine residue from liveness after the reap, not exit code.
      wait "$pid" 2>/dev/null
    fi
    [[ -z "$pid" ]] || process_absent "$pid" || { cleanup_failed=1; cleanup_refusal final fixture_process_residue; }
  done
  sudo rm -f /usr/local/share/ca-certificates/happyranch-n3-ci.crt || { cleanup_failed=1; cleanup_refusal final owned_path_unconfirmed; }
  sudo update-ca-certificates >/dev/null 2>&1 || { cleanup_failed=1; cleanup_refusal final owned_path_unconfirmed; }
  printf 'fixtures_reaped=%s\n' "$(( cleanup_failed == 0 ))" >"$diagnostics/cleanup-status.txt"
  sudo rm -rf /opt/happyranch /etc/happyranch /var/lib/happyranch-connector /var/lib/happyranch-tsnet-sidecar /run/happyranch-connector /run/happyranch-tsnet-sidecar /var/log/happyranch-connector /var/log/happyranch-tsnet-sidecar || { cleanup_failed=1; cleanup_refusal final owned_path_unconfirmed; }
  owned_unit_files_absent final || cleanup_failed=1
  for path in /opt/happyranch /etc/happyranch /var/lib/happyranch-connector /var/lib/happyranch-tsnet-sidecar /run/happyranch-connector /run/happyranch-tsnet-sidecar /var/log/happyranch-connector /var/log/happyranch-tsnet-sidecar /.happyranch-install-transaction.json /.happyranch-backup /.happyranch-units-backup; do
    test_false -e "$path" || { cleanup_failed=1; cleanup_refusal final owned_path_unconfirmed; }
  done
  if stage_paths="$(sudo find / -maxdepth 1 \( -name '.happyranch-stage-*' -o -name '.happyranch-tmp-*' \) -print -quit 2>/dev/null)"; then
    [[ -z "$stage_paths" ]] || { cleanup_failed=1; cleanup_refusal final stage_residue; }
  else
    cleanup_failed=1; cleanup_refusal final stage_query_failed
  fi
  for port in 18443 18765 18080 19090 15043 13478; do port_absent "$port" || { cleanup_failed=1; cleanup_refusal final host_listener_residue; }; done
  for unit in happyranch-connector.service happyranch-tsnet-sidecar.service; do
    systemctl_absent_value "$unit" MainPID 0 || { cleanup_failed=1; cleanup_refusal final mainpid_unconfirmed; }
  done
  (( cleanup_failed != 0 )) || evidence cleanup all_residue_absent || { cleanup_failed=1; cleanup_refusal final evidence_failed; }
  for raw_file in "${capture_raw_files[@]}"; do rm -f "$raw_file"; done
  rm -rf "$work"
  [[ ! -e "$work" ]] || { cleanup_failed=1; cleanup_refusal final work_path_residue; }
  (( cleanup_failed != 0 )) || evidence cleanup task_work_removed || { cleanup_failed=1; cleanup_refusal final evidence_failed; }
  if (( original_status == 0 && cleanup_failed == 0 )); then
    python "$evidence_driver" finalize "$evidence_artifact" || { cleanup_failed=1; cleanup_refusal final evidence_failed; }
    python "$evidence_driver" validate "$evidence_artifact" --expected-subject "$PROOF_SUBJECT_SHA" --expected-run "$run_id" || { cleanup_failed=1; cleanup_refusal final evidence_failed; }
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
log:
  format: json
  level: info
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
peer_started="$(peer_process_identity)" || fail "peer process identity unavailable"
peer_node="$(peer_identity)" || fail "peer node identity unavailable"

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
payload_custody="$(stat -c %U:%G:%a /opt/happyranch)" || fail "system-service payload root custody query failed"
[[ "$payload_custody" == "root:root:755" ]] || fail "system-service payload root custody mismatch"
binary_directory_custody="$(stat -c %U:%G:%a /opt/happyranch/bin)" || fail "system-service binary directory custody query failed"
[[ "$binary_directory_custody" == "root:root:755" ]] || fail "system-service binary directory custody mismatch"
for binary in /opt/happyranch/bin/happyranch-connector /opt/happyranch/bin/happyranch-tsnet-sidecar; do
  binary_custody="$(stat -c %U:%G:%a "$binary")" || fail "system-service binary custody query failed"
  [[ "$binary_custody" == "root:root:755" ]] || fail "system-service binary custody mismatch"
  sudo -u happyranch test -x "$binary" || fail "system-service user cannot execute packaged binary"
done
sudo systemctl daemon-reload
dropin_custody="$(sudo stat -c %U:%G:%a /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf)" || fail "transient credential drop-in custody query failed"
[[ "$dropin_custody" == "root:root:600" ]] || fail "transient credential drop-in custody mismatch"
daemon_source_custody="$(sudo stat -c %U:%G:%a /etc/happyranch/daemon.token)" || fail "daemon credential source custody query failed"
[[ "$daemon_source_custody" == "root:root:600" ]] || fail "daemon credential source custody mismatch"
enrollment_source_custody="$(sudo stat -c %U:%G:%a /etc/happyranch/enrollment.key)" || fail "enrollment credential source custody query failed"
[[ "$enrollment_source_custody" == "root:root:600" ]] || fail "enrollment credential source custody mismatch"

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
  local cleanup_complete=0 residue_root="${N3_RESIDUE_ROOT:-}" stage_paths process_status never_started=0
  if [[ "$service_start_attempted" == 0 ]]; then
    never_started=1
  else
    bind_stop_instance shipping || { cleanup_complete=1; cleanup_refusal shipping virtual_listener_residue; }
  fi
  # These requests are deliberately idempotent: the pre-arm reset also runs
  # after a prior cleanup has removed the units.  The explicit process, port,
  # fixture, credential, transaction, and path checks below decide success.
  sudo systemctl stop happyranch-managed.target happyranch-tsnet-sidecar.service happyranch-connector.service || true
  sudo systemctl disable happyranch-managed.target || true
  sudo systemctl reset-failed happyranch-managed.target happyranch-tsnet-sidecar.service happyranch-connector.service || true
  sudo rm -rf /etc/systemd/system/happyranch-tsnet-sidecar.service.d || { cleanup_complete=1; cleanup_refusal shipping owned_path_unconfirmed; }
  sudo rm -f /etc/systemd/system/happyranch-managed.target /etc/systemd/system/happyranch-tsnet-sidecar.service /etc/systemd/system/happyranch-connector.service || { cleanup_complete=1; cleanup_refusal shipping owned_path_unconfirmed; }
  sudo systemctl daemon-reload || { cleanup_complete=1; cleanup_refusal shipping reload_failed; }
  sudo rm -rf /opt/happyranch /etc/happyranch /var/lib/happyranch-connector /var/lib/happyranch-tsnet-sidecar /run/happyranch-connector /run/happyranch-tsnet-sidecar /var/log/happyranch-connector /var/log/happyranch-tsnet-sidecar || { cleanup_complete=1; cleanup_refusal shipping owned_path_unconfirmed; }
  local fixture_ids
  if fixture_ids="$("$work/headscale" nodes list --output json --config "$work/hs/config.yaml" | python -c 'import json,sys; d=json.load(sys.stdin); (isinstance(d,list) and all(isinstance(n,dict) and type(n.get("id")) is int and 0<n["id"]<=18446744073709551615 and isinstance(n.get("name"),str) and n["name"] and all(k not in n or isinstance(n[k],str) for k in ("givenName","given_name")) for n in d)) or sys.exit(1); print("\n".join(str(n["id"]) for n in d if n.get("givenName")=="home-sidecar-ci" or n.get("given_name")=="home-sidecar-ci" or n.get("name")=="home-sidecar-ci"))')"; then
    while read -r fixture_id; do
      [[ -z "$fixture_id" ]] || "$work/headscale" nodes delete --identifier "$fixture_id" --force --config "$work/hs/config.yaml" >/dev/null || { cleanup_complete=1; cleanup_refusal shipping fixture_delete_failed; }
    done <<<"$fixture_ids"
  else
    cleanup_complete=1; cleanup_refusal shipping fixture_absence_unconfirmed
  fi
  "$work/headscale" nodes list --output json --config "$work/hs/config.yaml" | python -c 'import json,sys; d=json.load(sys.stdin); (isinstance(d,list) and all(isinstance(n,dict) and type(n.get("id")) is int and 0<n["id"]<=18446744073709551615 and isinstance(n.get("name"),str) and n["name"] and all(k not in n or isinstance(n[k],str) for k in ("givenName","given_name")) for n in d)) or sys.exit(1); raise SystemExit(any(n.get("givenName")=="home-sidecar-ci" or n.get("given_name")=="home-sidecar-ci" or n.get("name")=="home-sidecar-ci" for n in d))' || { cleanup_complete=1; cleanup_refusal shipping fixture_absence_unconfirmed; }
  for unit in happyranch-managed.target happyranch-tsnet-sidecar.service happyranch-connector.service; do
    unit_absent "$unit" || { cleanup_complete=1; cleanup_refusal shipping unit_absence_unconfirmed; }
  done
  owned_unit_files_absent shipping || cleanup_complete=1
  port_absent 18443 || { cleanup_complete=1; cleanup_refusal shipping host_listener_residue; }
  if (( never_started )); then
    # Initial call only: no harness start (even a failed one) has been requested.
    # Install writes files only. In this isolated fixture, successful unit/PID,
    # full process, owned-path, host-listener and Headscale absence together
    # exclude an owned virtual engine/listener. Missing IP contributes nothing.
    [[ "$service_start_attempted" == 0 ]] || { cleanup_complete=1; cleanup_refusal shipping virtual_listener_residue; }
    if [[ -n "${sidecar_ip:-}" ]]; then
      tsnet_open && { cleanup_complete=1; cleanup_refusal shipping virtual_listener_residue; }
    fi
  else
    virtual_removed shipping || { cleanup_complete=1; cleanup_refusal shipping virtual_listener_residue; }
  fi
  test_false -e "$residue_root/etc/happyranch/enrollment.key" && test_false -e "$residue_root/etc/systemd/system/happyranch-tsnet-sidecar.service.d" || { cleanup_complete=1; cleanup_refusal shipping credential_path_residue; }
  test_false -e "$residue_root/.happyranch-install-transaction.json" && test_false -e "$residue_root/.happyranch-backup" && test_false -e "$residue_root/.happyranch-units-backup" || { cleanup_complete=1; cleanup_refusal shipping transaction_path_residue; }
  test_false -e "$residue_root/opt/happyranch" && test_false -e "$residue_root/etc/happyranch" && test_false -e "$residue_root/var/lib/happyranch-connector" && test_false -e "$residue_root/var/lib/happyranch-tsnet-sidecar" || { cleanup_complete=1; cleanup_refusal shipping payload_path_residue; }
  test_false -e "$residue_root/run/happyranch-connector" && test_false -e "$residue_root/run/happyranch-tsnet-sidecar" && test_false -e "$residue_root/var/log/happyranch-connector" && test_false -e "$residue_root/var/log/happyranch-tsnet-sidecar" || { cleanup_complete=1; cleanup_refusal shipping runtime_path_residue; }
  if pgrep -f '(^|/)(happyranch-connector|happyranch-tsnet-sidecar)( |$)' >/dev/null 2>&1; then
    cleanup_complete=1; cleanup_refusal shipping process_residue
  else
    process_status=$?
    (( process_status == 1 )) || { cleanup_complete=1; cleanup_refusal shipping process_query_failed; }
  fi
  if stage_paths="$(sudo find "${residue_root:-/}" -maxdepth 1 \( -name '.happyranch-stage-*' -o -name '.happyranch-tmp-*' \) -print -quit 2>/dev/null)"; then
    [[ -z "$stage_paths" ]] || { cleanup_complete=1; cleanup_refusal shipping stage_residue; }
  else
    cleanup_complete=1; cleanup_refusal shipping stage_query_failed
  fi
  (( cleanup_complete == 0 ))
}
reset_shipping_unit() {
  shipping_cleanup || return 1
  sudo install -d -m 0700 -o happyranch -g happyranch /etc/happyranch || return 1
  sudo install -m 0600 -o root -g root "$work/daemon.token" /etc/happyranch/daemon.token || return 1
  sudo install -m 0600 -o happyranch -g happyranch "$work/connector.json" /etc/happyranch/connector.json || return 1
  sudo install -m 0600 -o happyranch -g happyranch "$work/policy.json" /etc/happyranch/policy.json || return 1
  sudo install -m 0600 -o happyranch -g happyranch "$work/sidecar.json" /etc/happyranch/sidecar.json || return 1
  local fresh_key
  fresh_key="$("$work/headscale" preauthkeys create --user ci --reusable=false --expiration 10m --config "$work/hs/config.yaml")" || return 1
  printf '%s\n' "$fresh_key" >"$work/enrollment.key" || return 1
  sudo install -m 0600 -o root -g root "$work/enrollment.key" /etc/happyranch/enrollment.key || return 1
  sudo env "PATH=$PATH" uv run python - "$PACKAGE_TAR" <<'PY' || return 1
import sys
from pathlib import Path
from runtime.remote_access.linux_package import install_linux_package
install_linux_package(Path(sys.argv[1]), Path('/'), system_service=True)
PY
  sudo systemctl daemon-reload || return 1
  printf 'shipping_unit_reset=complete cleanup_complete=true\n' >>"$diagnostics/shipping-unit.log" || return 1
}
reset_shipping_unit || fail "fresh shipping-unit reset/setup failed"

# semantic evidence: startup
sudo mv /etc/happyranch/enrollment.key /etc/happyranch/enrollment.key.held
service_start_attempted=1
sudo systemctl start happyranch-managed.target || true
sleep 2
sudo systemctl stop happyranch-managed.target happyranch-tsnet-sidecar.service happyranch-connector.service || true
inactive happyranch-tsnet-sidecar.service || fail "sidecar survived missing-credential startup"
absent /var/lib/happyranch-tsnet-sidecar/credential.consumed
systemctl_absent_value happyranch-tsnet-sidecar.service MainPID 0 || fail "sidecar process survived failed startup"
peer_inventory_absent || fail "failed-start TSNet identity remained visible or peer inventory unavailable"
evidence "startup" "process_absent"
evidence "startup" "tsnet_admission_absent"
negative_leg_diagnostic_id="$run_id:negative-leg-expected:credential_input"
diagnostic credential_input input_acquisition systemd happyranch-tsnet-sidecar.service "$negative_leg_diagnostic_id"
printf 'diagnostic_id=%s expectation=expected category=credential_input\n' "$negative_leg_diagnostic_id" >>"$diagnostics/diagnostic-expectations.log"
# The unit has Restart=on-failure.  End the deliberately failed credential
# transaction completely before restoring the one-use source, otherwise a
# queued restart can race the first real enrollment and consume its staging.
# Best-effort reset request, not absence evidence: an unloaded unit is valid.
sudo systemctl reset-failed happyranch-tsnet-sidecar.service happyranch-connector.service || true
wait_for "failed credential staging cleanup" test_false -e /run/credentials/happyranch-tsnet-sidecar.service
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
connector_ready="$(systemctl_number happyranch-connector.service ActiveEnterTimestampMonotonic)"
sidecar_ready="$(systemctl_number happyranch-tsnet-sidecar.service ActiveEnterTimestampMonotonic)"
[[ "$sidecar_ready" -le "$connector_ready" ]] || fail "connector reported composite READY before sidecar admission"
check_staged_credential() {
  local path="$1" directory="$2" name="$3" resolved_path file_metadata directory_metadata
  [[ "$path" == "$directory/$name" ]] || fail "staged credential provenance mismatch"
  # Canonical metadata inspection must traverse root-private systemd staging.
  # Service access is proved separately below under the happyranch identity.
  resolved_path="$(sudo readlink -f -- "$path")" || fail "staged credential provenance query failed"
  [[ "$resolved_path" == "$path" ]] || fail "staged credential escape mismatch"
  sudo test -f "$path" || fail "staged credential type mismatch"
  test_false -L "$path" || fail "staged credential symlink mismatch"
  sudo -u happyranch test -r "$path" || fail "staged credential unreadable"
  test_false -w "$path" -u happyranch || fail "staged credential service-writable"
  test_false -w "$directory" -u happyranch || fail "staged credential directory service-writable"
  file_metadata="$(sudo env LC_ALL=C stat -c %U:%G:%a:%F "$path")" || fail "staged credential file metadata query failed"
  directory_metadata="$(sudo env LC_ALL=C stat -c %U:%G:%a:%F "$directory")" || fail "staged credential directory metadata query failed"
  [[ "$file_metadata" =~ ^[^:[:space:]]+:[^:[:space:]]+:[0-7]{3,4}:regular\ (empty\ )?file$ ]] || fail "staged credential file metadata malformed"
  [[ "$directory_metadata" =~ ^[^:[:space:]]+:[^:[:space:]]+:[0-7]{3,4}:directory$ ]] || fail "staged credential directory metadata malformed"
  printf 'credential_observation name=%s file=%s directory=%s\n' \
    "$name" "$file_metadata" "$directory_metadata" || fail "staged credential observation write failed"
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
missing_consumed_state_refused || { negative_status=$?; echo 'n3-real-systemd: missing consumed state refusal unproved; retained attempt evidence' >&2; exit "$negative_status"; }
evidence "startup" "missing_consumed_state_failed_closed"
sudo mv "$work/credential.consumed.held" /var/lib/happyranch-tsnet-sidecar/credential.consumed
# Best-effort reset request, not absence evidence: an unloaded unit is valid.
sudo systemctl reset-failed happyranch-tsnet-sidecar.service happyranch-connector.service || true
service_start_attempted=1
sudo systemctl start happyranch-managed.target
wait_for "credential-free stopped-service restart" active happyranch-tsnet-sidecar.service
absent /etc/happyranch/enrollment.key
absent /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf
evidence "recovery" "credential_free_stopped_restart"

# Re-enter after interruption between drop-in reload and source unlink.
sudo systemctl stop happyranch-managed.target
printf '%s\n' interrupted-retirement >"$work/enrollment.key"
sudo install -m 0600 -o root -g root "$work/enrollment.key" /etc/happyranch/enrollment.key
service_start_attempted=1
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
service_start_attempted=1
sudo systemctl start happyranch-managed.target
wait_for "explicit fresh re-enrollment" active happyranch-tsnet-sidecar.service
absent /etc/happyranch/enrollment.key
absent /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf
evidence "recovery" "explicit_fresh_reenrollment"
sidecar_ip="$(sudo "$ts_dir/tailscale" --socket="$work/peer.sock" status --json | python -c 'import ipaddress,json,sys; d=json.load(sys.stdin); ip=next(ip for p in d.get("Peer",{}).values() if p.get("HostName")=="home-sidecar-ci" for ip in p.get("TailscaleIPs",[]) if ":" not in ip); print(ipaddress.IPv4Address(ip))')" || fail "sidecar address unavailable"
wait_for "virtual TSNet listener" tsnet_open
evidence "admission" "tsnet_admission_reachable"
sidecar_pid="$(mainpid happyranch-tsnet-sidecar.service)" && [[ "$sidecar_pid" != 0 ]] || fail "production sidecar absent"
evidence "active_flow" "production_process_active"
watchdog_before="$(systemctl_number happyranch-connector.service WatchdogTimestampMonotonic)"
sleep 12
watchdog_after="$(systemctl_number happyranch-connector.service WatchdogTimestampMonotonic)"
[[ "$watchdog_after" -gt "$watchdog_before" ]] || fail "connector watchdog did not follow current composite health"
active happyranch-tsnet-sidecar.service || fail "watchdog continued without sidecar health"
evidence "active_flow" "watchdog_composite_current"

bind_stop_instance health_loss || fail "health-loss instance unavailable"
sudo systemctl stop happyranch-tsnet-sidecar.service
wait_for "connector watchdog cessation" inactive happyranch-connector.service
virtual_removed health_loss || fail "health-loss listener teardown unconfirmed"
watchdog_stopped="$(systemctl_number happyranch-connector.service WatchdogTimestampMonotonic)"
sleep 2
watchdog_current="$(systemctl_number happyranch-connector.service WatchdogTimestampMonotonic)"
[[ "$watchdog_current" == "$watchdog_stopped" ]] || fail "watchdog refreshed after sidecar loss"
evidence "active_flow" "watchdog_ceased_on_sidecar_loss"
service_start_attempted=1
sudo systemctl start happyranch-managed.target
wait_for "composite restart after health loss" active happyranch-tsnet-sidecar.service
wait_for "virtual admission after health recovery" tsnet_open

# semantic evidence: partial_failure
old_pid="$(mainpid happyranch-tsnet-sidecar.service)"
[[ "$old_pid" != 0 ]] || fail "sidecar PID unavailable before kill"
sudo kill -KILL "$old_pid"
wait_for "automatic Restart=" fresh_sidecar_pid
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
service_start_attempted=1
sudo systemctl start happyranch-managed.target & start_job=$!
wait_for "start barrier entered" sudo test -e "$barrier_dir/start-entered"
sudo systemctl stop happyranch-managed.target & stop_job=$!
target_job_pending stop || fail "stop queue ordering unconfirmed behind entered start"
evidence "concurrency_reentry" "start_then_stop_barrier"
: | sudo tee "$barrier_dir/start-release" >/dev/null; wait "$start_job" || true; wait "$stop_job"
inactive happyranch-tsnet-sidecar.service || fail "start-then-stop did not stop"
evidence "concurrency_reentry" "stop_wins"

# Force the opposite ordering: production Stop completes while a new start is
# queued behind an ExecStopPost barrier, so stale admission cannot survive.
sudo tee /etc/systemd/system/happyranch-tsnet-sidecar.service.d/90-ci-barrier.conf >/dev/null <<EOF
[Service]
ExecStopPost=/bin/sh -c 'touch $barrier_dir/stop-entered; while test ! -e $barrier_dir/stop-release; do sleep .05; done'
EOF
sudo systemctl daemon-reload
service_start_attempted=1
sudo systemctl start happyranch-managed.target
bind_stop_instance stop_barrier || fail "Stop-barrier instance unavailable"
sudo systemctl stop happyranch-managed.target & stop_job=$!
wait_for "stop barrier entered" sudo test -e "$barrier_dir/stop-entered"
virtual_removed stop_barrier || fail "Stop-barrier listener teardown unconfirmed"
service_start_attempted=1
sudo systemctl start happyranch-managed.target & start_job=$!
target_job_pending start || fail "start queue ordering unconfirmed behind entered stop"
evidence "concurrency_reentry" "stop_then_start_barrier"
: | sudo tee "$barrier_dir/stop-release" >/dev/null; wait "$stop_job"; wait "$start_job"
sudo rm -f /etc/systemd/system/happyranch-tsnet-sidecar.service.d/90-ci-barrier.conf
sudo rm -f "$barrier_dir/start-entered" "$barrier_dir/start-release" "$barrier_dir/stop-entered" "$barrier_dir/stop-release"
sudo rmdir "$barrier_dir" || fail "barrier residue"
sudo systemctl daemon-reload

# semantic evidence: readiness_loss. Compare the real systemd monotonic
# inactive timestamps and probe the virtual TSNet listener from the real peer.
bind_stop_instance readiness_loss || fail "readiness-loss instance unavailable"
sudo systemctl stop happyranch-connector.service
wait_for "BindsTo readiness loss" inactive happyranch-tsnet-sidecar.service
virtual_removed readiness_loss || fail "readiness-loss listener teardown unconfirmed"
sidecar_down="$(systemctl_number happyranch-tsnet-sidecar.service InactiveEnterTimestampMonotonic)"
connector_down="$(systemctl_number happyranch-connector.service InactiveEnterTimestampMonotonic)"
[[ "$sidecar_down" -le "$connector_down" ]] || fail "connector cleanup preceded TSNet admission removal"
evidence "readiness_loss" "tsnet_admission_removed_before_connector"
sudo systemctl stop happyranch-managed.target

# semantic evidence: revocation and shutdown. The packaged binary calls Stop
# twice on the same Sidecar and emits invocation-scoped receipts for its PID.
service_start_attempted=1
sudo systemctl start happyranch-managed.target
wait_for "shutdown admission" tsnet_open
bind_stop_instance shutdown || fail "shutdown instance unavailable"
sudo systemctl stop happyranch-managed.target
virtual_removed shutdown || fail "shutdown listener teardown unconfirmed"
evidence "revocation" "stop_before_connector_cleanup"
evidence "revocation" "tsnet_admission_absent"
evidence "shutdown" "same_instance_stop_twice"
evidence "shutdown" "no_double_close"
systemctl_absent_value happyranch-tsnet-sidecar.service MainPID 0 || fail "sidecar residue after repeated shutdown"
evidence "shutdown" "no_residue"

service_start_attempted=1
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
  stage_paths="$(sudo find / -maxdepth 1 \( -name '.happyranch-stage-*' -o -name '.happyranch-tmp-*' \) -print -quit)" || fail "stage query failed"
  [[ -z "$stage_paths" ]] || fail "fresh transaction residue"
  sudo env "PATH=$PATH" uv run python - "$PACKAGE_TAR" <<'PY'
import sys
from pathlib import Path
from runtime.remote_access.linux_package import install_linux_package
install_linux_package(Path(sys.argv[1]), Path('/'), system_service=True)
PY
  sudo systemctl daemon-reload
  service_start_attempted=1
  sudo systemctl start happyranch-managed.target
  wait_for "fresh $boundary composite startup" active happyranch-tsnet-sidecar.service
  wait_for "fresh $boundary virtual admission" tsnet_open
done
evidence "recovery" "fresh_install_rollback_reentry_each_checkpoint"
before_manifest="$(sudo sha256sum /opt/happyranch/manifest.json)" || fail "retained payload digest query failed"
[[ "$before_manifest" =~ ^[0-9a-f]{64}\ \ /opt/happyranch/manifest\.json$ ]] || fail "retained payload digest malformed"
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
after_manifest="$(sudo sha256sum /opt/happyranch/manifest.json)" || fail "rollback payload digest query failed"
[[ "$after_manifest" =~ ^[0-9a-f]{64}\ \ /opt/happyranch/manifest\.json$ ]] || fail "rollback payload digest malformed"
[[ "$after_manifest" == "$before_manifest" ]] || fail "upgrade rollback lost retained payload"
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
stage_paths="$(sudo find / -maxdepth 1 -name '.happyranch-stage-*' -print -quit)" || fail "stage query failed"
[[ -z "$stage_paths" ]] || fail "transaction stage residue"
evidence "recovery" "no_transaction_residue"
sudo systemctl daemon-reload
service_start_attempted=1
sudo systemctl start happyranch-managed.target
wait_for "upgrade recovery" active happyranch-tsnet-sidecar.service
wait_for "upgrade virtual admission" tsnet_open
evidence "recovery" "fresh_composite_gates"
echo N3_REAL_SYSTEMD_PASS
