package sidecar

import (
	"bytes"
	"encoding/json"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Run the shipping M1 Python program, including its diagnostic validator and
// actual stderr rendering. Supply package observations without installing or
// querying a service. This uses the retained native venue, not Python units.
func TestM1ScalarMismatchDiagnostic(t *testing.T) {
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	for _, scenario := range []string{
		"connector-Type", "sidecar-Type", "connector-Environment", "sidecar-Environment",
		"connector-LoadCredentialEncrypted", "sidecar-LoadCredentialEncrypted",
		"nested-sidecar", "missing-loaded", "unknown-property", "other-site", "target-unit", "invalid-loaded",
	} {
		t.Run(scenario, func(t *testing.T) {
			command := exec.Command("python3", "-c", m1ScalarMismatchFixture, scenario)
			command.Dir = root
			var stderr bytes.Buffer
			command.Stderr = &stderr
			if out, err := command.Output(); err != nil {
				t.Fatalf("M1 diagnostic program: %v\n%s\n%s", err, out, stderr.String())
			}
			const prefix = "M1 managed-package diagnostic: "
			raw := stderr.String()
			if !strings.HasPrefix(raw, prefix) || strings.Count(raw, "\n") != 1 {
				t.Fatalf("expected one diagnostic line: %q", raw)
			}
			for _, forbidden := range []string{"SENTINEL", `"loaded":`, `"expected":`, `"observed":`, "/private/"} {
				if strings.Contains(raw, forbidden) {
					t.Fatalf("private observation disclosed: %q", raw)
				}
			}
			if scenario == "invalid-loaded" {
				if raw != prefix+"unavailable\n" {
					t.Fatalf("invalid observation did not fail closed: %q", raw)
				}
				return
			}
			var diagnostic map[string]any
			if err := json.Unmarshal([]byte(strings.TrimPrefix(raw, prefix)), &diagnostic); err != nil {
				t.Fatal(err)
			}
			var refusal map[string]any
			if scenario == "nested-sidecar" {
				stages := diagnostic["stages"].([]any)
				refusal = stages[0].([]any)[2].(map[string]any)["refusal"].(map[string]any)
			} else {
				refusal = diagnostic["refusal"].(map[string]any)
			}
			wantNames := strings.HasPrefix(scenario, "connector-") || strings.HasPrefix(scenario, "sidecar-") || scenario == "nested-sidecar"
			names, present := refusal["mismatched_property"]
			if !wantNames {
				if present {
					t.Fatalf("unexpected mismatch names: %#v", names)
				}
				return
			}
			unit, prop := "happyranch-connector.service", strings.TrimPrefix(scenario, "connector-")
			if strings.HasPrefix(scenario, "sidecar-") {
				unit, prop = "happyranch-tsnet-sidecar.service", strings.TrimPrefix(scenario, "sidecar-")
			} else if scenario == "nested-sidecar" {
				unit, prop = "happyranch-tsnet-sidecar.service", "LoadCredentialEncrypted"
			}
			fields, ok := names.(map[string]any)
			if !present || !ok || len(fields) != 2 || fields["unit"] != unit || fields["property"] != prop {
				t.Fatalf("missing exact mismatch names: got %#v, want %s / %s", names, unit, prop)
			}
			if refusal["site"] != "P000263" || refusal["category"] != "managed_loaded_contract_mismatch" {
				t.Fatalf("refusal identity changed: %#v", refusal)
			}
		})
	}
}

const m1ScalarMismatchFixture = `
import sys
from pathlib import Path
from unittest.mock import patch
from runtime.remote_access import linux_package as package
from runtime.remote_access.package_diagnostics import diagnostic_error, loaded_observation

scenario = sys.argv[1]
unit = 1 if scenario.startswith("sidecar-") or scenario == "nested-sidecar" else 0
prop = scenario.split("-", 1)[1] if scenario.startswith(("connector-", "sidecar-")) else "LoadCredentialEncrypted"
if scenario == "unknown-property": prop = "UnknownSENTINEL"
if scenario == "target-unit": unit = 2
site = "P000265" if scenario == "other-site" else "P000263"
loaded = loaded_observation(unit, prop, "equal", "expectedSENTINEL", "observedSENTINEL",
                            {"After": "contextSENTINEL", "RequiresMountsFor": "/private/SENTINEL"})
if scenario == "invalid-loaded": loaded[0] = True
failure = diagnostic_error(site, "managed_loaded_contract_mismatch", package.PackageError("managed_loaded_contract_mismatch"))

def install_observation(*args, **kwargs):
    refusal = {"category": "managed_loaded_contract_mismatch", "site": site}
    if scenario != "missing-loaded": refusal["loaded"] = loaded
    facts = package._MANAGED_PACKAGE_DIAGNOSTIC.get()
    if scenario != "nested-sidecar":
        facts["refusal"] = refusal
    else:
        # A closed nested failure observation exercises the same recursive
        # projection used by package_body in the hosted install diagnostic.
        facts["stages"] = [["admission_child", "failure", {
            "reason": "admission_contract", "diagnostic_state": "observed", "helper_exit": 78,
            "deadline_elapsed": False, "kill_requested": False, "reaped": True, "reap_exit": 78,
            "process": {"launch": "started", "wait": "reaped", "wait_error": True,
                        "kill": "not_requested", "writer_close": "ok"},
            "refusal": refusal,
            "native": {"source": "nested_frozen_stderr", "state": "private_only", "messages": [],
                       "eof": True, "bytes_seen": 319, "frames_seen": 1, "redacted": False,
                       "truncated": False, "losses": [], "read": "eof", "close": "ok"},
        }]]
    raise failure

script = Path("app/linux/package/m1_install_ready_uninstall.sh").read_text()
program = script.split("package_operation() {", 1)[1].split("<<'PY'\n", 1)[1].split("\nPY\n}", 1)[0]
with patch.object(package, "install_linux_package", side_effect=install_observation), \
     patch.object(sys, "argv", ["-", "install", "/unused-package"]):
    try:
        exec(compile(program, "<shipping M1 package_operation>", "exec"), {"__name__": "__main__"})
    except package.PackageError as error:
        assert error is failure, "diagnostic replaced initiating refusal"
    else:
        raise AssertionError("initiating refusal was suppressed")
assert package._MANAGED_PACKAGE_DIAGNOSTIC.get() is None, "diagnostic context leaked"
`
