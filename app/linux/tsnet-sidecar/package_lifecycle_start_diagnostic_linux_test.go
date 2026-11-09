package sidecar

import (
	"bytes"
	"encoding/json"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// The real CLI and _spawn_fixed_lifecycle own serialization, pipes, caps,
// concurrent drain, waitpid and public rendering. Only posix_spawn's fixed
// executable is substituted with this test process; no host service is used.
func TestManagedLifecycleStartDiagnostic(t *testing.T) {
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	for _, scenario := range []string{
		"request_open", "stopped_current", "eligibility_preflight", "policy_write", "manager_start", "reply",
		"eligibility-origin", "native-request",
		"spawn_failed", "exit_nonzero", "signaled", "context_done", "deadline_elapsed",
		"step-installed_contract", "step-config", "step-supervisor", "step-trust_metadata",
		"step-trust_load", "step-configured_identity", "step-policy", "step-metadata_bookend",
		"disposition-unknown", "disposition-none", "disposition-control", "disposition-newline",
		"disposition-path", "disposition-long", "disposition-type", "disposition-duplicate",
		"disposition-wrong-branch",
		"step-unknown", "step-none-category", "step-none-site", "step-control", "step-newline",
		"step-path", "step-long", "step-type", "step-duplicate", "unknown-branch", "null-category", "unknown-origin",
		"newline", "control", "path", "long", "wrong-type", "duplicate", "malformed", "oversize",
		"extra-output", "unregistered-category", "deadline", "nonzero-deadline", "legacy",
	} {
		t.Run(scenario, func(t *testing.T) {
			cmd := exec.Command("python3", "-I", "-c", lifecycleStartDiagnosticFixture, root, os.Args[0], scenario)
			cmd.Dir = root
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("shipping start/transport diagnostic: %v\n%s", err, out)
			} else {
				t.Log(string(out))
			}
		})
	}
}

// Separate process for the unchanged native command seam. The native-request
// case calls the shipping Go dispatch and refuses before OpenLifecycle or I/O.
func TestLifecycleStartDiagnosticProcess(t *testing.T) {
	if os.Getenv("HR_START_DIAGNOSTIC_TEST") != "1" {
		return
	}
	raw, err := io.ReadAll(io.LimitReader(os.Stdin, 4097))
	if err != nil {
		os.Exit(90)
	}
	var request map[string]any
	if json.Unmarshal(raw, &request) != nil || request["operation"] != "start" {
		os.Exit(91)
	}
	if os.Getenv("HR_START_DIAGNOSTIC_SCENARIO") == "native-request" {
		request["deadline_ns"] = 0
		raw, _ = json.Marshal(request)
		os.Exit(HandleLifecycleRequest(bytes.NewReader(raw), os.Stdout))
	}
	_, _ = io.WriteString(os.Stdout, os.Getenv("HR_START_DIAGNOSTIC_REPLY"))
	if os.Getenv("HR_START_DIAGNOSTIC_SCENARIO") == "deadline" {
		os.Exit(0)
	}
	os.Exit(78)
}

// Exercise the shipping eligibility exception boundary in one invocation,
// with the installed-contract predicate as the external refusal observation.
func TestManagedEligibilityRefusalIdentity(t *testing.T) {
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	for _, scenario := range []string{"registered-p", "registered-g", "unknown", "null-category", "newline", "control", "path", "long", "unmarked",
		"fallback-installed_contract", "fallback-config", "fallback-supervisor", "fallback-trust_metadata",
		"fallback-trust_load", "fallback-configured_identity", "fallback-policy", "fallback-metadata_bookend", "success"} {
		t.Run(scenario, func(t *testing.T) {
			cmd := exec.Command("python3", "-I", "-c", eligibilityRefusalFixture, root, scenario)
			cmd.Dir = root
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("shipping eligibility refusal identity: %v\n%s", err, out)
			} else {
				t.Log(string(out))
			}
		})
	}
}

const eligibilityRefusalFixture = `
import contextlib
import io
import json
import os
import sys
import time
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from runtime.remote_access import cli, linux_package as package

scenario = sys.argv[2]
identity = {
    "registered-p": ("managed_loaded_contract_mismatch", "P000263"),
    "registered-g": ("managed_lifecycle_refused", "G000150"),
    "unknown": ("managed_lifecycle_refused", "P999999"),
    "null-category": (None, "P000263"),
    "newline": (None, "P000263\nSENTINEL"),
    "control": (None, "P000263\x1bSENTINEL"),
    "path": (None, "/private/SENTINEL/daemon.token"),
    "long": (None, "SENTINEL" * 128),
}.get(scenario)
failure = package.PackageError("SENTINEL /private/credential")
if identity is not None: failure._managed_package_refusal = identity
stdout, stderr, writes = io.StringIO(), io.StringIO(), []
def observation(fd, raw):
    assert fd == 1
    writes.append(raw)
    return len(raw)
# Supply external observations only. Each injected error is thrown by the actual
# entered reader/call; all preceding shipping eligibility code still executes.
from contextlib import ExitStack
from types import SimpleNamespace
from pathlib import Path
steps = ("installed_contract", "config", "supervisor", "trust_metadata", "trust_load", "configured_identity", "policy", "metadata_bookend")
selected = scenario.removeprefix("fallback-") if scenario.startswith("fallback-") else "installed_contract"
calls = []
def entered(step, result=None):
    calls.append(step)
    if step == selected and scenario != "success": raise failure
    return result
config = SimpleNamespace(managed={}, unit_name=package.UNITS[0], system=True, state_path="/fixture/state")
metadata_calls = [0]
def metadata(path):
    raise FileNotFoundError(2, "SENTINEL")
def absolute(path):
    metadata_calls[0] += 1
    entered("trust_metadata" if metadata_calls[0] <= 2 else "metadata_bookend")
    return True
class Supervisor:
    def __init__(self, **kwargs):
        entered("supervisor")
        self.state_store = SimpleNamespace(load=lambda: entered("trust_load", SimpleNamespace(connector_identity="fixture")))
    def _configured_identity(self): return entered("configured_identity", "fixture")
    def load_policy(self): return entered("policy")
from runtime.remote_access import supervisor as supervisor_module
with ExitStack() as stack:
    stack.enter_context(patch.dict(os.environ, {"HAPPYRANCH_LIFECYCLE_DEADLINE_NS": str(time.monotonic_ns() + 45_000_000_000)}))
    contract = stack.enter_context(patch.object(package, "_installed_managed_contract", side_effect=lambda **kwargs: entered("installed_contract")))
    stack.enter_context(patch.object(supervisor_module.ConnectorConfig, "from_file", side_effect=lambda path: entered("config", config)))
    stack.enter_context(patch.object(supervisor_module, "ConnectorSupervisor", Supervisor))
    stack.enter_context(patch.object(Path, "lstat", metadata))
    stack.enter_context(patch.object(Path, "is_absolute", absolute))
    stack.enter_context(patch.object(os, "write", side_effect=observation))
    stack.enter_context(contextlib.redirect_stdout(stdout))
    stack.enter_context(contextlib.redirect_stderr(stderr))
    result = cli.main(["managed-private", "eligibility"])
assert result == (0 if scenario == "success" else 78) and contract.call_count == 1
expected = b""
if scenario in ("registered-p", "registered-g"):
    expected = (json.dumps({"category": identity[0], "site": identity[1]}, sort_keys=True, separators=(",", ":")) + "\n").encode()
elif scenario.startswith("fallback-") or scenario in ("unknown", "null-category", "unmarked"):
    site = "P9000" + str(10 + steps.index(selected))
    expected = (json.dumps({"category": "managed_operational_error", "site": site}, sort_keys=True, separators=(",", ":")) + "\n").encode()
expected_calls = list(steps[:3]) + ["trust_metadata"] * 2 + list(steps[4:7]) + ["metadata_bookend"] * 2
if scenario != "success": expected_calls = expected_calls[:expected_calls.index(selected)+1]
assert calls == expected_calls, calls
assert b"".join(writes) == expected, (scenario, writes, expected)
assert stdout.getvalue() == stderr.getvalue() == ""
assert package._MANAGED_DEADLINE.get() is None
print("eligibility diagnostic verified:", scenario)
`

const lifecycleStartDiagnosticFixture = `
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
from runtime.remote_access import cli, linux_package as package

helper, scenario = sys.argv[2:]
branches = ("request_open", "stopped_current", "eligibility_preflight", "policy_write", "manager_start", "reply")
record = {"version": 1, "branch": scenario if scenario in branches else "eligibility_preflight",
          "origin": {"category": "managed_lifecycle_refused", "site": "G000150"}}
dispositions = ("spawn_failed", "exit_nonzero", "signaled", "context_done", "deadline_elapsed")
steps = ("installed_contract", "config", "supervisor", "trust_metadata", "trust_load", "configured_identity", "policy", "metadata_bookend")
if scenario in dispositions: record["disposition"] = scenario
if scenario.startswith("step-") and scenario[5:] in steps:
    record["eligibility"] = {"category": "managed_operational_error", "site": "P9000" + str(10 + steps.index(scenario[5:]))}
invalid_steps = {"step-unknown": "P999999", "step-none-site": None,
    "step-control": "P900010\x1bSENTINEL", "step-newline": "P900010\nSENTINEL",
    "step-path": "/private/SENTINEL", "step-long": "SENTINEL" * 128, "step-type": ["P900010"]}
if scenario in invalid_steps:
    record["eligibility"] = {"category": "managed_operational_error", "site": invalid_steps[scenario]}
if scenario == "step-none-category": record["eligibility"] = {"category": None, "site": "P900010"}
if scenario == "step-duplicate": record["eligibility"] = {"category": "managed_operational_error", "site": "P900010"}
invalid_dispositions = {"disposition-unknown": "SENTINEL", "disposition-none": None,
    "disposition-control": "exit_nonzero\x1bSENTINEL", "disposition-newline": "exit_nonzero\nSENTINEL",
    "disposition-path": "/private/SENTINEL", "disposition-long": "SENTINEL" * 128,
    "disposition-type": ["exit_nonzero"], "disposition-duplicate": "exit_nonzero", "disposition-wrong-branch": "exit_nonzero"}
if scenario in invalid_dispositions: record["disposition"] = invalid_dispositions[scenario]
if scenario == "disposition-wrong-branch": record["branch"] = "manager_start"
if scenario == "eligibility-origin":
    record["eligibility"] = {"category": "managed_loaded_contract_mismatch", "site": "P000263"}
if scenario == "unknown-branch": record["branch"] = "UNREGISTERED_SENTINEL"
if scenario == "null-category": record["origin"]["category"] = None
if scenario == "unknown-origin": record["origin"]["site"] = "G999999"
if scenario == "unregistered-category": record["origin"]["category"] = "SENTINEL"
for name, value in {"newline": "reply\nSENTINEL", "control": "reply\x1bSENTINEL",
                    "path": "/private/SENTINEL/daemon.token", "long": "SENTINEL" * 128,
                    "wrong-type": ["reply"]}.items():
    if scenario == name: record["branch"] = value
raw = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
if scenario == "duplicate": raw = raw.replace('"version":1', '"version":1,"version":1')
if scenario == "step-duplicate": raw = raw.replace('"site":"P900010"', '"site":"P900010","site":"P900010"')
if scenario == "disposition-duplicate": raw = raw.replace('"disposition":"exit_nonzero"', '"disposition":"exit_nonzero","disposition":"exit_nonzero"')
if scenario == "malformed": raw = '{"branch":'
if scenario == "oversize": raw = "SENTINEL" * 600
if scenario == "extra-output": raw += "SENTINEL\n"
if scenario == "legacy": raw = ""

spawn, convert = os.posix_spawn, os.waitstatus_to_exitcode
clock = [None]
requests, children = [], []
def spawn_observation(executable, argv, environment, **kwargs):
    assert executable == "/opt/happyranch/bin/happyranch-tsnet-sidecar"
    assert argv == [executable, "managed-lifecycle"]
    environment.update(HR_START_DIAGNOSTIC_TEST="1", HR_START_DIAGNOSTIC_SCENARIO=scenario,
                       HR_START_DIAGNOSTIC_REPLY=raw)
    child = spawn(helper, [helper, "-test.run=^TestLifecycleStartDiagnosticProcess$"], environment, **kwargs)
    children.append(child)
    return child
real_spawn = package._spawn_fixed_lifecycle
def transport(executable, operation, request, descriptors):
    requests.append(request)
    return real_spawn(executable, operation, request, descriptors)
def converted(status):
    result = convert(status)
    if scenario in ("deadline", "nonzero-deadline"):
        clock[0] = requests[0]["deadline_ns"] + 1
    return result
monotonic = package.time.monotonic_ns
def sampled():
    return monotonic() if clock[0] is None else clock[0]

with tempfile.TemporaryDirectory() as temporary:
    config = Path(temporary) / "connector.json"
    config.write_text(json.dumps({"tenant_id": "tenant-ci", "home_id": "home-ci", "connector_id": "connector-ci",
        "daemon_port": 18765, "daemon_token_path": "/unused", "policy_path": "/unused", "managed": {}}))
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.object(package, "_spawn_fixed_lifecycle", side_effect=transport), \
         patch.object(package.os, "posix_spawn", side_effect=spawn_observation), \
         patch.object(package.os, "waitstatus_to_exitcode", side_effect=converted), \
         patch.object(package.time, "monotonic_ns", side_effect=sampled), \
         contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = cli.main(["start", "--config", str(config)])
    expected = "error: managed_lifecycle_refused\nmanaged lifecycle refusal site: P000052\n"
    if scenario == "oversize":
        expected = "error: managed_lifecycle_response_invalid\nmanaged lifecycle refusal site: P000051\n"
    else:
        outcome = {"deadline": "deadline_elapsed", "nonzero-deadline": "nonzero_deadline_elapsed"}.get(scenario, "nonzero")
        expected += "managed lifecycle helper outcome: " + outcome + "\n"
        if scenario in branches or scenario in ("eligibility-origin", "native-request") or scenario in dispositions or scenario.startswith("step-") and scenario[5:] in steps:
            branch = "request_open" if scenario == "native-request" else record["branch"]
            expected += "managed lifecycle start branch: " + branch + "\n"
            if scenario != "native-request":
                expected += "managed lifecycle native origin: G000150\n"
            if scenario == "eligibility-origin":
                expected += "managed lifecycle eligibility origin: P000263\n"
            if scenario in dispositions:
                expected += "managed lifecycle eligibility disposition: " + scenario + "\n"
            if scenario.startswith("step-") and scenario[5:] in steps:
                expected += "managed lifecycle eligibility origin: " + record["eligibility"]["site"] + "\n"
                expected += "managed lifecycle eligibility step: " + scenario[5:] + "\n"
    assert result == 1, result
    assert stdout.getvalue() == "", stdout.getvalue()
    assert stderr.getvalue() == expected, (scenario, stderr.getvalue(), expected)
    assert "SENTINEL" not in stderr.getvalue()
    assert len(requests) == len(children) == 1, "predicate/helper repeated"
    try:
        os.waitpid(children[0], os.WNOHANG)
    except ChildProcessError:
        pass
    else:
        raise AssertionError("helper not reaped by shipping transport")
    assert package._MANAGED_DEADLINE.get() is None
    print("start diagnostic verified:", scenario)
`
