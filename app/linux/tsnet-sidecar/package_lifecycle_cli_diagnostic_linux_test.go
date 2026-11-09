package sidecar

import (
	"os/exec"
	"path/filepath"
	"testing"
)

// Exercise the public CLI with real config parsing and managed start routing.
// The fixed native subprocess boundary supplies observations; no host service
// runs and no retired Python test module is imported.
func TestManagedLifecycleCLIRefusalSite(t *testing.T) {
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	for _, scenario := range []string{"native-exit", "spawn-error", "unmarked", "unknown-site", "wrong-category", "implicit-context", "success"} {
		t.Run(scenario, func(t *testing.T) {
			command := exec.Command("python3", "-I", "-c", lifecycleCLIRefusalFixture, root, scenario)
			command.Dir = root
			if out, err := command.CombinedOutput(); err != nil {
				t.Fatalf("public managed start diagnostic: %v\n%s", err, out)
			} else {
				t.Log(string(out))
			}
		})
	}
}

const lifecycleCLIRefusalFixture = `
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
from runtime.remote_access import cli, linux_package as package
from runtime.remote_access.package_diagnostics import diagnostic_error

scenario = sys.argv[2]
failure = package.PackageError("managed_lifecycle_refused")
site = {"native-exit": "P000052", "spawn-error": "P000075"}.get(scenario)
if site:
    diagnostic_error(site, "managed_lifecycle_refused", failure)
if scenario == "unknown-site":
    failure._managed_package_refusal = ("managed_lifecycle_refused", "P999999/private/SENTINEL")
if scenario == "wrong-category":
    failure._managed_package_refusal = ("SENTINEL", "P000052")
if scenario == "implicit-context":
    failure.__context__ = diagnostic_error("P000052", "managed_lifecycle_refused", RuntimeError("SENTINEL"))
# Private observations must never be rendered by this public names-only lane.
failure._managed_package_loaded = ["/private/SENTINEL", "credentialSENTINEL"]

def native_observation(executable, operation, request, descriptors):
    assert executable == "/opt/happyranch/bin/happyranch-tsnet-sidecar"
    assert operation == "managed-lifecycle" and request["operation"] == "start" and descriptors == {}
    if scenario == "success":
        return json.dumps({"version": 2, "state": "starting", "operation_id": request["operation_id"]}).encode()
    raise failure

with tempfile.TemporaryDirectory() as temporary:
    config = Path(temporary) / "connector.json"
    config.write_text(json.dumps({"tenant_id": "tenant-ci", "home_id": "home-ci", "connector_id": "connector-ci",
        "daemon_port": 18765, "daemon_token_path": "/unused", "policy_path": "/unused", "managed": {}}))
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.object(package, "_spawn_fixed_lifecycle", side_effect=native_observation) as native, \
         contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        result = cli.main(["start", "--config", str(config)])
    assert native.call_count == 1, "did not reach shipping managed start seam"
    expected = "" if scenario == "success" else "error: managed_lifecycle_refused\n"
    if site:
        expected += "managed lifecycle refusal site: " + site + "\n"
    assert result == (0 if scenario == "success" else 1), result
    assert stdout.getvalue() == "", stdout.getvalue()
    assert stderr.getvalue() == expected, (scenario, stderr.getvalue(), expected)
    assert "SENTINEL" not in stderr.getvalue()
    assert package._MANAGED_DEADLINE.get() is None, "CLI deadline context leaked"
    print("CLI diagnostic verified:", scenario)
`
