package sidecar

import (
	"os/exec"
	"path/filepath"
	"testing"
)

// Run the real installed-contract reader, including both query subprocesses,
// strict parsers and exact service comparisons. Only external observations are
// supplied here; no retired Python tests or production test hooks are involved.
func TestServiceSettingsTypedEmptyArrays(t *testing.T) {
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	for _, unit := range []string{"happyranch-connector.service", "happyranch-tsnet-sidecar.service"} {
		t.Run(unit, func(t *testing.T) {
			for _, scenario := range []string{
				"empty", "printed-empty", "nonempty", "nonempty-env", "nonempty-stop-post",
				"nonempty-pre", "nonempty-post", "wrong-signature", "missing-signature",
				"extra-bytes", "extra-newline", "no-newline", "invalid-utf8", "control",
				"crlf", "nonzero", "timeout", "overflow", "missing-scalar",
				"printed-nonempty", "duplicate", "unknown", "malformed-show", "control-show",
				"missing-command", "printed-command-mismatch", "omit-pre", "omit-post", "shared-bound",
			} {
				t.Run(scenario, func(t *testing.T) {
					command := exec.Command("python3", "-c", serviceSettingsTypedFixture, unit, scenario)
					command.Dir = root
					if out, err := command.CombinedOutput(); err != nil {
						t.Fatalf("shipping service-settings check: %v\n%s", err, out)
					} else {
						t.Log(string(out))
					}
				})
			}
		})
	}
}

const serviceSettingsTypedFixture = `
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch
from runtime.remote_access import linux_package as package
from runtime.remote_access import cli
from runtime.remote_access.package_diagnostics import exception_refusal, exception_loaded

unit, scenario = sys.argv[1:]
connector = unit == "happyranch-connector.service"
scalar = {
    "User": "happyranch", "Group": "happyranch", "Type": "notify", "NotifyAccess": "main",
    "Restart": "on-failure" if connector else "no", "RestartMode": "normal",
    "StartLimitIntervalUSec": "0", "KillMode": "mixed", "TimeoutStartUSec": "2min",
    "TimeoutStopUSec": "35s", "TimeoutAbortUSec": "35s", "WatchdogUSec": "30s",
    "WatchdogSignal": "12", "NoNewPrivileges": "yes", "ProtectSystem": "strict",
    "ProtectHome": "yes", "PrivateTmp": "yes", "PrivateDevices": "yes",
    "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes", "ProtectControlGroups": "yes",
    "RestrictSUIDSGID": "yes", "RestrictRealtime": "yes", "LockPersonality": "yes",
    "MemoryDenyWriteExecute": "yes", "CapabilityBoundingSet": "", "AmbientCapabilities": "",
    "UMask": "0077", "StateDirectory": "happyranch-connector" if connector else "happyranch-tsnet-sidecar",
    "StateDirectoryMode": "0700", "ExecStop": "", "ExecStopPost": "", "DynamicUser": "no",
    "SupplementaryGroups": "", "ReadWritePaths": "", "Environment": "", "EnvironmentFiles": "",
    "PassEnvironment": "", "LoadCredentialEncrypted": "", "RootDirectory": "", "RootImage": "",
}
if connector:
    scalar["RestartUSec"] = "30s"
base = {
    "LoadState": "loaded", "FragmentPath": "/etc/systemd/system/" + unit,
    "DropInPaths": "", "NeedDaemonReload": "no", "Wants": "network-online.target",
    "Requires": "", "BindsTo": "", "PartOf": "happyranch-managed.target " + (
        "happyranch-tsnet-sidecar.service" if connector else "happyranch-connector.service"),
    "After": "network-online.target", "Before": "happyranch-managed.target", "Conflicts": "",
    "RequiresMountsFor": "", "DefaultDependencies": "yes", "StopWhenUnneeded": "no",
    "RefuseManualStart": "no", "RefuseManualStop": "no",
}
if connector:
    base["Wants"] += " happyranch-tsnet-sidecar.service"
def execution(argv, flags=""):
    return "{ path=" + argv.split()[0] + " ; argv[]=" + argv + " ; flags=" + flags + " ; start_time=[] ; stop_time=[] ; pid=0 ; code=(null) ; status=0/0 }"
if connector:
    start = "/opt/happyranch/bin/happyranch-tsnet-sidecar supervise-connector /opt/happyranch/bin/happyranch-connector run --managed --config /etc/happyranch/connector.json"
    pre, post = "", ""
else:
    start = "/opt/happyranch/bin/happyranch-tsnet-sidecar --config /etc/happyranch/sidecar.json"
    pre = execution("/opt/happyranch/bin/happyranch-connector reconcile-enrollment-retirement --source /etc/happyranch/enrollment.key --marker /var/lib/happyranch-tsnet-sidecar/credential.consumed --dropin /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf", "fully-privileged") + " " + execution("/opt/happyranch/bin/happyranch-connector credential-capability --name enrollment.key --unit happyranch-tsnet-sidecar.service --consumed-marker /var/lib/happyranch-tsnet-sidecar/credential.consumed")
    post = execution("/opt/happyranch/bin/happyranch-connector retire-enrollment-source --source /etc/happyranch/enrollment.key --marker /var/lib/happyranch-tsnet-sidecar/credential.consumed --dropin /etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf", "fully-privileged")
commands = {
    "ExecStartEx": execution(start), "ExecStartPreEx": pre, "ExecStartPostEx": post,
    "LoadCredential": "daemon.token:/etc/happyranch/daemon.token" if connector else "",
    "RestartPreventExitStatus": " ".join(str(n) for n in range(2, 256)) if connector else "",
    "RestrictAddressFamilies": "AF_INET AF_INET6 AF_UNIX AF_NETLINK", "SystemCallArchitectures": "native",
}
context = {**base, **scalar, **commands}
signatures = {"EnvironmentFiles": "a(sb)", "ExecStop": "a(sasbttttuii)",
              "ExecStopPost": "a(sasbttttuii)", "ExecStartPreEx": "a(sasasttttuii)",
              "ExecStartPostEx": "a(sasasttttuii)"}
omitted = {name for name in signatures if context[name] == ""}
nonempty_property = {"nonempty": "ExecStop", "nonempty-env": "EnvironmentFiles",
    "nonempty-stop-post": "ExecStopPost", "nonempty-pre": "ExecStartPreEx",
    "nonempty-post": "ExecStartPostEx"}.get(scenario)
if nonempty_property:
    omitted = {nonempty_property}
if scenario == "printed-empty":
    omitted.clear()
if scenario in ("printed-nonempty", "duplicate", "unknown", "malformed-show", "control-show"):
    omitted.clear()
if scenario == "missing-scalar":
    omitted = {"User"}
if scenario in ("missing-command", "printed-command-mismatch"):
    omitted.clear()
    if scenario == "missing-command":
        omitted.add("ExecStartEx")
if scenario in ("omit-pre", "omit-post"):
    omitted = {"ExecStartPreEx" if scenario == "omit-pre" else "ExecStartPostEx"}

popen = package.subprocess.Popen
capture = cli._capture_bounded_query_output
queries, children, bounds = [], [], []
def record_capture(child, bound):
    bounds.append((queries[-1], bound))
    return capture(child, bound)
def observation(argv, **kwargs):
    assert kwargs == dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}, pass_fds=())
    queries.append(argv)
    delay, status = 0, 0
    if argv[0] == "/usr/bin/systemctl":
        assert argv[:2] == ["/usr/bin/systemctl", "show"]
        assert all(arg.startswith("--property=") for arg in argv[3:])
        names = [arg.removeprefix("--property=") for arg in argv[3:]]
        if argv[2] != unit:
            raw = "".join(name + "=" + ("not-found" if name == "LoadState" else "no" if name == "NeedDaemonReload" else "") + "\n" for name in names).encode()
        else:
            values = dict(context)
            if scenario == "printed-nonempty":
                values["EnvironmentFiles"] = "/etc/unexpected (ignore_errors=no)"
            if scenario == "printed-command-mismatch":
                values["ExecStartPreEx"] = execution("/bin/false")
            raw = "".join(name + "=" + values[name] + "\n" for name in names if name not in omitted).encode()
            if "User" in names:
                if scenario == "duplicate": raw += b"User=happyranch\n"
                if scenario == "unknown": raw += b"Unknown=\n"
                if scenario == "malformed-show": raw += b"User\n"
                if scenario == "control-show": raw = raw.replace(b"User=happyranch\n", b"User=happy\tranch\n")
                if scenario == "shared-bound": delay = 0.2
    else:
        fixed_path = "/org/freedesktop/systemd1/unit/" + ("happyranch_2dconnector_2eservice" if connector else "happyranch_2dtsnet_2dsidecar_2eservice")
        assert argv[:-1] == ["/usr/bin/busctl", "get-property", "org.freedesktop.systemd1", fixed_path, "org.freedesktop.systemd1.Service"]
        assert argv[-1] in omitted & signatures.keys()
        raw = (signatures[argv[-1]] + " 0\n").encode()
        if nonempty_property:
            record = ('1 "/unexpected" false' if argv[-1] == "EnvironmentFiles" else
                      '1 "/bin/false" 1 "/bin/false" 0 0 0 0 0 0 0 0' if argv[-1].endswith("Ex") else
                      '1 "/bin/false" 1 "/bin/false" false 0 0 0 0 0 0 0')
            raw = (signatures[argv[-1]] + " " + record + "\n").encode()
        if scenario == "wrong-signature": raw = b"as 0\n"
        if scenario == "missing-signature": raw = b"0\n"
        if scenario == "extra-bytes": raw += b"extra"
        if scenario == "extra-newline": raw += b"\n"
        if scenario == "no-newline": raw = raw[:-1]
        if scenario == "invalid-utf8": raw += b"\xff"
        if scenario == "control": raw += b"\x00"
        if scenario == "crlf": raw = raw[:-1] + b"\r\n"
        if scenario == "nonzero": status = 1
        if scenario == "timeout": delay = 3
        if scenario == "overflow": raw = b"x" * 8192
    child = popen([sys.executable, "-c", "import os,sys,time; time.sleep(float(sys.argv[2])); os.write(1, bytes.fromhex(sys.argv[1])); sys.exit(int(sys.argv[3]))", raw.hex(), str(delay), str(status)], **kwargs)
    children.append(child)
    return child

expected = None
if scenario in ("nonempty", "wrong-signature", "missing-signature", "extra-bytes", "extra-newline", "no-newline", "invalid-utf8", "control", "crlf", "nonzero", "timeout", "overflow"):
    expected = ("managed_manager_unavailable", "P900001")
if nonempty_property:
    expected = ("managed_manager_unavailable", "P900001")
if scenario == "timeout":
    expected = ("managed_operational_error", "Q000004")
elif scenario in ("missing-scalar", "missing-command"):
    expected = ("managed_manager_unavailable", "P000241")
elif scenario == "printed-nonempty":
    expected = ("managed_loaded_contract_mismatch", "P000263")
elif scenario == "printed-command-mismatch" or not connector and scenario in ("omit-pre", "omit-post"):
    expected = ("managed_loaded_contract_mismatch", "P000264")
elif scenario in ("duplicate", "unknown", "malformed-show", "control-show"):
    expected = ("managed_manager_unavailable", "P000240")
started = time.monotonic()
with patch.object(package.os, "geteuid", return_value=0), \
     patch.object(package, "_package_managed_history", return_value=(None, {}, None, True)), \
     patch.object(Path, "exists", return_value=False), \
     patch.object(package.subprocess, "Popen", side_effect=observation), \
     patch.object(cli, "_capture_bounded_query_output", side_effect=record_capture):
    try:
        result = package._installed_managed_contract(_package=True)
    except Exception as error:
        actual = exception_refusal(error)
        assert expected is not None and actual == expected, (scenario, expected, actual)
        assert str(error) == ("managed_manager_unavailable" if scenario == "timeout" else expected[0])
        if scenario == "timeout":
            assert error._managed_package_boundary == ("managed_manager_unavailable", "P900001")
        if scenario in ("missing-scalar", "missing-command"):
            assert exception_loaded(error) is not None
            assert not any(q[0] == "/usr/bin/busctl" for q in queries)
        print("refused", actual)
    else:
        assert expected is None, (scenario, "unexpected acceptance")
        assert result == {"dropin_mode": 0o644, "dropin_directory_mode": 0o755}, result
        print("complete service settings accepted")
elapsed = time.monotonic() - started
assert all(child.poll() is not None and child.stdout.closed for child in children)
if scenario == "timeout":
    assert elapsed < 1.5, elapsed
if scenario == "shared-bound":
    systemctl_bound = None
    typed = 0
    for argv, bound in bounds:
        if argv[0] == "/usr/bin/systemctl":
            systemctl_bound = bound
        else:
            assert bound == systemctl_bound, (bound, systemctl_bound)
            typed += 1
    assert typed == len(omitted), (typed, omitted)
if scenario in ("printed-empty", "printed-nonempty", "printed-command-mismatch"):
    assert not any(q[0] == "/usr/bin/busctl" for q in queries)
print("children reaped; elapsed=", elapsed)
`
