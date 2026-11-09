package sidecar

import (
	"path/filepath"
	"strings"
	"testing"
)

// Observe the shipping installed-contract boundary with real systemd framing;
// only disk/PID1 observations are substituted, never the parser or comparison.
func TestLoadedStructuredProperties(t *testing.T) {
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	setup := strings.Replace(serviceSettingsTypedFixture[:strings.Index(serviceSettingsTypedFixture, "popen = package.subprocess.Popen")], "unit, scenario = sys.argv[1:]", "unit, scenario = sys.argv[1:3]", 1)
	for _, unit := range []string{"happyranch-connector.service", "happyranch-tsnet-sidecar.service"} {
		for _, prop := range []string{"LoadCredential", "LoadCredentialEncrypted", "ExecStartPreEx"} {
			cases := []string{"exact", "loaded-dropin", "real-framed-pre", "nonempty", "wrong-signature", "missing", "bad-quoting", "trailing", "trailing-space", "no-lf", "extra-lf", "crlf", "non-utf8", "duplicate-key", "extra-key", "missing-key", "wrong-data-type", "wrong-entry-type", "wrong-string-type", "nan", "float", "surrogate", "bad-escape", "unprintable", "oversize", "nonzero", "timeout", "unavailable", "late", "shared-bound"}
			if prop == "LoadCredential" {
				cases = append(cases, "wrong-id", "wrong-path", "extra", "duplicate", "zero", "pair-extra")
				if unit == "happyranch-tsnet-sidecar.service" {
					cases = append(cases, "dropin-wrong-id", "dropin-wrong-path", "dropin-extra", "dropin-duplicate", "dropin-zero")
				}
			}
			if prop == "ExecStartPreEx" {
				cases = append(cases, "wrong-order", "extra", "duplicate", "zero", "missing-entry", "wrong-argv", "wrong-path", "wrong-flags", "extra-flags", "duplicate-flags", "tuple-extra", "argv-type", "flags-type", "dynamic-limits", "bool-int", "negative-unsigned", "timestamp-overflow", "pid-overflow", "code-underflow", "status-overflow", "signed-negative-zero")
			}
			for _, scenario := range cases {
				t.Run(unit+"/"+prop+"/"+scenario, func(t *testing.T) {
					runWatchdogPython(t, root, setup+loadedStructuredFixture, unit, scenario, prop)
				})
			}
		}
	}
}

const loadedStructuredFixture = `
import copy
prop = sys.argv[3]
test_scenario = scenario
loaded_dropin = scenario == "loaded-dropin" or scenario.startswith("dropin-")
scenario = scenario.removeprefix("dropin-")
if scenario == "real-framed-pre":
    # Isolate H's duplicate ExecStartPreEx failure after its encrypted check.
    context["LoadCredentialEncrypted"] = ""
if loaded_dropin and not connector:
    context["DropInPaths"] = "/etc/systemd/system/happyranch-tsnet-sidecar.service.d/10-enrollment-credential.conf"
data = copy.deepcopy(structured_data(prop))
if loaded_dropin and not connector and prop == "LoadCredential":
    data = [["enrollment.key", "/etc/happyranch/enrollment.key"]]
sample = ["/bin/false", ["/bin/false"], [], 0, 0, 0, 0, 0, 0, 0] if prop.endswith("Ex") else ["SENTINEL", "/private/SENTINEL"]
if scenario == "nonempty": data = [sample]
if not prop.endswith("Ex") and scenario in ("wrong-id", "wrong-path", "pair-extra"):
    data = data or [["enrollment.key", "/etc/happyranch/enrollment.key"]]
    if scenario == "pair-extra": data[0].append("extra")
    else: data[0][0 if scenario == "wrong-id" else 1] = "SENTINEL"
if scenario in ("extra", "duplicate"): data.append(copy.deepcopy(data[0] if data else sample))
if scenario == "zero": data = []
if scenario == "wrong-order": data = list(reversed(data)) if data else [sample]
if scenario == "missing-entry": data = data[:-1] if data else [sample]
if prop.endswith("Ex") and scenario in ("wrong-argv", "wrong-path", "wrong-flags", "extra-flags", "duplicate-flags", "tuple-extra", "argv-type", "flags-type", "dynamic-limits", "bool-int", "negative-unsigned", "timestamp-overflow", "pid-overflow", "code-underflow", "status-overflow", "signed-negative-zero"):
    data = data or [copy.deepcopy(sample)]
    if scenario == "wrong-argv": data[0][1].append("SENTINEL")
    if scenario == "wrong-path": data[0][0] = "/private/SENTINEL"
    if scenario == "wrong-flags": data[0][2] = [] if data[0][2] else ["fully-privileged"]
    if scenario == "extra-flags": data[0][2].append("ignore-failure")
    if scenario == "duplicate-flags": data[0][2] = ["fully-privileged", "fully-privileged"]
    if scenario == "tuple-extra": data[0].append(0)
    if scenario == "argv-type": data[0][1] = "SENTINEL"
    if scenario == "flags-type": data[0][2] = "fully-privileged"
    if scenario == "dynamic-limits":
        if connector: data = []
        else:
            for row in data: row[3:] = [0, 18446744073709551615, 123, 456, 4294967295, -2147483648, 2147483647]
    if scenario == "bool-int": data[0][3] = True
    if scenario == "negative-unsigned": data[0][3] = -1
    if scenario == "timestamp-overflow": data[0][3] = 18446744073709551616
    if scenario == "pid-overflow": data[0][7] = 4294967296
    if scenario == "code-underflow": data[0][8] = -2147483649
    if scenario == "status-overflow": data[0][9] = 2147483648
frame = structured_frame(prop, data)
if scenario == "wrong-signature": frame = frame.replace(structured[prop].encode(), b"as")
if scenario == "missing": frame = b""
if scenario == "bad-quoting": frame = b'{"type":"unterminated}\n'
if scenario == "trailing": frame += b"trailing"
if scenario == "trailing-space": frame = frame[:-1] + b" \n"
if scenario == "no-lf": frame = frame[:-1]
if scenario == "extra-lf": frame += b"\n"
if scenario == "crlf": frame = frame[:-1] + b"\r\n"
if scenario == "non-utf8": frame = frame[:-1] + b"\xff\n"
if scenario == "duplicate-key": frame = frame.replace(b'"data":', b'"data":[],"data":')
if scenario == "extra-key": frame = frame.replace(b'"data":', b'"extra":0,"data":')
if scenario == "missing-key": frame = b'{"data":[]}\n'
if scenario == "wrong-data-type": frame = (json.dumps({"type": structured[prop], "data": {}}) + "\n").encode()
if scenario == "wrong-entry-type": frame = structured_frame(prop, [False])
if scenario == "wrong-string-type": frame = structured_frame(prop, [[True, "x"]] if not prop.endswith("Ex") else [[True, [], [], 0, 0, 0, 0, 0, 0, 0]])
if scenario in ("nan", "float"):
    frame = structured_frame(prop, [["x", "x"]] if not prop.endswith("Ex") else [["x", [], [], 0, 0, 0, 0, 0, 0, 0]])
    frame = frame.replace(b'"x"', b'NaN' if scenario == "nan" else b'1.0', 1)
if scenario == "surrogate": frame = structured_frame(prop, [["\ud800", "x"]] if not prop.endswith("Ex") else [["\ud800", [], [], 0, 0, 0, 0, 0, 0, 0]])
if scenario == "bad-escape": frame = b'{"type":"a(ss)","data":[["\\q","x"]]}\n'
if scenario == "signed-negative-zero": frame = frame.replace(b",0", b",-0", 1)
if scenario == "unprintable": frame = b"[unprintable]\n"
if scenario == "oversize": frame = b"x" * 8192
accepted = scenario in ("exact", "loaded-dropin", "real-framed-pre", "dynamic-limits", "shared-bound") or scenario == "zero" and not connector and not loaded_dropin and prop == "LoadCredential" or scenario == "zero" and connector and prop == "ExecStartPreEx"
mismatch = scenario in ("nonempty", "wrong-id", "wrong-path", "extra", "duplicate", "zero", "wrong-order", "missing-entry", "wrong-argv", "wrong-flags", "extra-flags", "duplicate-flags")
expected_code = "P000264" if prop.endswith("Ex") else "P000263" if prop == "LoadCredentialEncrypted" else "P000265"
popen = package.subprocess.Popen
capture = cli._capture_bounded_query_output
children, queries, bounds = [], [], []
deadline = time.monotonic_ns() + 1_500_000_000
def record_capture(child, bound):
    bounds.append((queries[-1], bound))
    result = capture(child, bound)
    if scenario == "late" and queries[-1][-1] == prop:
        package.time.monotonic_ns = lambda: deadline
    return result
def observation(argv, **kwargs):
    assert kwargs == dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"}, pass_fds=())
    queries.append(argv)
    delay, status = 0, 0
    if argv[0] == "/usr/bin/systemctl":
        names = [arg.removeprefix("--property=") for arg in argv[3:]]
        if argv[2] == unit:
            raw = "".join(name + "=" + context[name] + "\n" for name in names).encode()
            if scenario == "shared-bound" and "User" in names: delay = 0.15
        else:
            raw = "".join(name + "=" + ("not-found" if name == "LoadState" else "no" if name == "NeedDaemonReload" else "") + "\n" for name in names).encode()
    else:
        name = argv[-1]
        fixed = "/org/freedesktop/systemd1/unit/" + ("happyranch_2dconnector_2eservice" if connector else "happyranch_2dtsnet_2dsidecar_2eservice")
        assert argv == ["/usr/bin/busctl", "--json=short", "get-property", "org.freedesktop.systemd1", fixed, "org.freedesktop.systemd1.Service", name]
        assert name in structured
        raw = frame if name == prop else structured_frame(name)
        if name != prop and name == "LoadCredential" and loaded_dropin and not connector:
            raw = structured_frame(name, [["enrollment.key", "/etc/happyranch/enrollment.key"]])
        if name == prop:
            if scenario == "nonzero": status = 1
            if scenario == "timeout": delay = 3
            if scenario == "unavailable": raise OSError("external typed read unavailable")
    child = popen([sys.executable, "-c", "import os,sys,time; time.sleep(float(sys.argv[2])); os.write(1,bytes.fromhex(sys.argv[1])); sys.exit(int(sys.argv[3]))", raw.hex(), str(delay), str(status)], **kwargs)
    children.append(child)
    return child
started = time.monotonic()
with patch.object(package.os, "geteuid", return_value=0), \
     patch.object(package, "_package_managed_history", return_value=(None, {}, None, True)), \
     patch.object(Path, "exists", return_value=False), \
     patch.object(package.subprocess, "Popen", side_effect=observation), \
     patch.object(cli, "_capture_bounded_query_output", side_effect=record_capture), \
     patch.object(package.time, "monotonic_ns", wraps=time.monotonic_ns):
    try:
        result = package._installed_managed_contract(_package=True, deadline_ns=deadline)
    except package.PackageError as error:
        actual = exception_refusal(error)
        assert not accepted, ("expected acceptance", unit, prop, test_scenario, actual)
        if mismatch:
            assert actual == ("managed_loaded_contract_mismatch", expected_code), actual
            loaded = exception_loaded(error)
            assert loaded[1] == prop and loaded[3:5] == ["!", "!"], loaded
        else:
            assert error._managed_package_boundary == ("managed_manager_unavailable", "P900001"), actual
        assert "SENTINEL" not in str(error) and "/private/" not in str(error)
        print("REFUSED", unit, prop, test_scenario, actual)
    else:
        assert accepted, ("unexpected acceptance", unit, prop, test_scenario)
        assert result == {"dropin_mode": 0o644, "dropin_directory_mode": 0o755}, result
        assert any(q[0] == "/usr/bin/busctl" and q[-1] == prop for q in queries), queries
        print("ACCEPTED", unit, prop, test_scenario)
for argv, bound in bounds:
    if argv[0] == "/usr/bin/systemctl": text_bound = bound
    else: assert bound == text_bound, bounds
    assert bound <= deadline / 1_000_000_000
assert all(child.poll() is not None and child.stdout.closed for child in children)
assert time.monotonic() - started < 2
`
