package sidecar

import (
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadedWatchdogContract(t *testing.T) {
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	// Reuse the existing complete external settings fixture, leaving its tests
	// unchanged. Everything following its setup is the actual reader boundary.
	setup := strings.Replace(serviceSettingsTypedFixture[:strings.Index(serviceSettingsTypedFixture, "popen = package.subprocess.Popen")], "unit, scenario = sys.argv[1:]", "unit, scenario = sys.argv[1:3]", 1)
	for _, unit := range []string{"happyranch-connector.service", "happyranch-tsnet-sidecar.service"} {
		for _, state := range []string{"never", "StateChangeTimestampMonotonic", "InactiveExitTimestampMonotonic", "ExecMainStartTimestampMonotonic", "InvocationID"} {
			for _, value := range []string{"infinity", "30s", "0", "1min", "30000000", "", "30s "} {
				t.Run(unit+"/"+state+"/value="+value, func(t *testing.T) {
					runWatchdogPython(t, root, setup+loadedWatchdogFixture, unit, "watchdog", state, value, "valid", "none")
				})
			}
		}
		for _, marker := range []string{"StateChangeTimestampMonotonic", "InactiveExitTimestampMonotonic", "ExecMainStartTimestampMonotonic", "InvocationID"} {
			for _, frame := range []string{"missing", "duplicate", "malformed", "noncanonical", "negative", "overflow", "wrong-type", "crlf", "no-lf", "extra-lf", "invalid-utf8", "oversize", "timeout", "nonzero", "read-failure", "late"} {
				t.Run(unit+"/marker="+marker+"/frame="+frame, func(t *testing.T) {
					runWatchdogPython(t, root, setup+loadedWatchdogFixture, unit, "watchdog", "never", "infinity", frame, marker)
				})
			}
		}
	}
}

func TestReadyWatchdogContract(t *testing.T) {
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	for _, unit := range []string{"happyranch-connector.service", "happyranch-tsnet-sidecar.service"} {
		for _, entry := range []string{"start_ready", "start", "restart", "status", "reconcile", "enable", "disable", "stop"} {
			for _, value := range []string{"30s", "infinity", "0", "1min", "30000000", "", "30s ", "duplicate", "late"} {
				t.Run(unit+"/"+entry+"/value="+value, func(t *testing.T) {
					runWatchdogPython(t, root, readyWatchdogFixture, unit, entry, value)
				})
			}
		}
	}
}

func runWatchdogPython(t *testing.T, root, fixture string, args ...string) {
	t.Helper()
	command := exec.Command("python3", append([]string{"-c", fixture}, args...)...)
	command.Dir = root
	out, err := command.CombinedOutput()
	if err != nil {
		t.Fatalf("production watchdog boundary: %v\n%s", err, out)
	}
	t.Log(string(out))
}

const loadedWatchdogFixture = `
state, value, frame, selected_marker = sys.argv[3:]
markers = ["StateChangeTimestampMonotonic", "InactiveExitTimestampMonotonic", "ExecMainStartTimestampMonotonic", "InvocationID"]
scalar_bound = [None]
context["WatchdogUSec"] = value
omitted.clear()
popen = package.subprocess.Popen
children, queries, bounds = [], [], []
capture = cli._capture_bounded_query_output
deadline = time.monotonic_ns() + 1_000_000_000
def record_capture(child, bound):
    bounds.append(bound)
    if queries[-1][0] == "/usr/bin/systemctl" and "--property=WatchdogUSec" in queries[-1]:
        scalar_bound[0] = bound
    if queries[-1][0] == "/usr/bin/busctl":
        assert bound == scalar_bound[0], (bound, scalar_bound)
    assert bound <= deadline / 1_000_000_000, (bound, deadline)
    return capture(child, bound)
def observation(argv, **kwargs):
    queries.append(argv)
    delay, status = 0, 0
    if argv[0] == "/usr/bin/systemctl":
        names = [arg.removeprefix("--property=") for arg in argv[3:]]
        if argv[2] == unit:
            raw = "".join(name + "=" + context[name] + "\n" for name in names).encode()
        else:
            raw = "".join(name + "=" + ("not-found" if name == "LoadState" else "no" if name == "NeedDaemonReload" else "") + "\n" for name in names).encode()
    else:
        fixed = "/org/freedesktop/systemd1/unit/" + ("happyranch_2dconnector_2eservice" if connector else "happyranch_2dtsnet_2dsidecar_2eservice")
        marker = argv[-1]
        assert marker in markers, argv
        interface = "Service" if marker == "ExecMainStartTimestampMonotonic" else "Unit"
        assert argv == ["/usr/bin/busctl", "get-property", "org.freedesktop.systemd1", fixed,
                        "org.freedesktop.systemd1." + interface, marker], argv
        signature = b"ay" if marker == "InvocationID" else b"t"
        raw = signature + b" 0\n"
        if state == marker:
            raw = (b"ay 16 " + b"17 " * 15 + b"17\n") if marker == "InvocationID" else b"t 123456\n"
        if marker == selected_marker:
            raw = {"missing": b"", "duplicate": raw + raw, "malformed": signature + b" x\n",
                "noncanonical": signature + b" 00\n", "negative": signature + b" -1\n",
                "overflow": signature + b" 18446744073709551616\n", "wrong-type": b"u 0\n",
                "crlf": signature + b" 0\r\n", "no-lf": signature + b" 0",
                "extra-lf": raw + b"\n", "invalid-utf8": signature + b" \xff\n", "oversize": b"x" * 8192}.get(frame, raw)
            if frame == "timeout": delay = 3
            if frame == "nonzero": status = 1
            if frame == "read-failure": raise OSError("test external bus read unavailable")
    child = popen([sys.executable, "-c", "import os,sys,time; time.sleep(float(sys.argv[2])); os.write(1, bytes.fromhex(sys.argv[1])); sys.exit(int(sys.argv[3]))", raw.hex(), str(delay), str(status)], **kwargs)
    children.append(child)
    return child
def after_capture(child, bound):
    result = record_capture(child, bound)
    if frame == "late" and queries[-1][-1] == selected_marker:
        package.time.monotonic_ns = lambda: deadline
    return result
accepted = value == "30s" or (value == "infinity" and state == "never" and frame == "valid")
started = time.monotonic()
with patch.object(package.os, "geteuid", return_value=0), \
     patch.object(package, "_package_managed_history", return_value=(None, {}, None, True)), \
     patch.object(Path, "exists", return_value=False), \
     patch.object(package.subprocess, "Popen", side_effect=observation), \
     patch.object(cli, "_capture_bounded_query_output", side_effect=after_capture), \
     patch.object(package.time, "monotonic_ns", wraps=time.monotonic_ns):
    try:
        result = package._installed_managed_contract(_package=True, deadline_ns=deadline)
    except package.PackageError as error:
        actual = exception_refusal(error)
        assert not accepted, ("expected acceptance", unit, state, value, frame, selected_marker, actual)
        if frame == "valid" and not (state == "InvocationID" and value == "infinity"):
            assert actual == ("managed_loaded_contract_mismatch", "P000263"), actual
        else:
            failed_marker = state if frame == "valid" else selected_marker
            assert any(q[0] == "/usr/bin/busctl" and q[-1] == failed_marker for q in queries), queries
            assert actual != ("managed_loaded_contract_mismatch", "P000263"), actual
        print("REFUSED", unit, state, repr(value), frame, actual)
    else:
        assert accepted, ("unexpected acceptance", unit, state, value, frame, selected_marker)
        assert result == {"dropin_mode": 0o644, "dropin_directory_mode": 0o755}, result
        print("ACCEPTED", unit, state, repr(value), frame)
    typed = [q[-1] for q in queries if q[0] == "/usr/bin/busctl"]
    if accepted and value == "infinity":
        assert typed == markers, typed
    if value != "infinity":
        assert typed == [], typed
assert all(child.poll() is not None and child.stdout.closed for child in children)
assert time.monotonic() - started < 1.5
`

const readyWatchdogFixture = `
import json
import subprocess
import sys
import time
from unittest.mock import patch
from runtime.remote_access import linux_package as package
from runtime.remote_access.package_diagnostics import exception_refusal
unit, entry, value = sys.argv[1:]
original_deadline = time.monotonic_ns() + 800_000_000
clock = [time.monotonic_ns()]
queries = []
def observe(argv, **kwargs):
    assert argv[0:2] == ["/usr/bin/systemctl", "show"], argv
    assert kwargs["timeout"] <= (original_deadline - clock[0]) / 1_000_000_000
    assert package._MANAGED_DEADLINE.get() == original_deadline
    prop = argv[3]
    queries.append((argv[2], prop))
    raw = "active\n"
    if prop == "--property=WatchdogUSec":
        assert queries[:2] == [(u, "--property=ActiveState") for u in package.UNITS[:2]], queries
        raw = "30s\n"
        if argv[2] == unit:
            raw = "30s\n30s\n" if value == "duplicate" else value + "\n"
            if value == "late":
                raw = "30s\n"
                clock[0] = original_deadline
    return subprocess.CompletedProcess(argv, 0, raw, "")
real_manager = package.CompositeServiceManager
def manager():
    return real_manager(run=observe)
def native(_executable, _operation, request, _extra):
    assert request["deadline_ns"] == original_deadline, request
    return json.dumps({"version": 2, "state": "ready", "operation_id": request["operation_id"]}).encode()
token = package._MANAGED_DEADLINE.set(original_deadline)
try:
    with patch.object(package.time, "monotonic_ns", side_effect=lambda: clock[0]), \
         patch.object(package, "_spawn_fixed_lifecycle", side_effect=native), \
         patch.object(package, "CompositeServiceManager", side_effect=manager):
        try:
            if entry == "start_ready":
                manager().start_ready()
            else:
                result = package.managed_lifecycle(entry, deadline_ns=original_deadline)
                assert result["state"] == "ready"
        except package.PackageError as error:
            assert value != "30s", ("expected readiness", exception_refusal(error))
            assert any(prop == "--property=WatchdogUSec" for _, prop in queries), queries
            expected = "managed_lifecycle_deadline" if value == "late" else "service_not_ready"
            assert str(error) == expected, (expected, str(error), exception_refusal(error))
            print("REFUSED READY", unit, entry, repr(value), exception_refusal(error))
        else:
            assert value == "30s", ("unexpected READY", unit, entry, value, queries)
            assert all((u, "--property=WatchdogUSec") in queries for u in package.UNITS[:2]), queries
            print("READY", unit, entry, "30s both units; original deadline", original_deadline)
finally:
    assert package._MANAGED_DEADLINE.get() == original_deadline
    package._MANAGED_DEADLINE.reset(token)
`
