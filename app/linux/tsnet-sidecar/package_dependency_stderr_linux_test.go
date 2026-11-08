package sidecar

import (
	"bytes"
	"fmt"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Exercise the shipping Python refusal path and writer through process stderr.
// This uses the retained native test venue, never the retired Python unit suite.
func TestDependencyRefusalFullStderr(t *testing.T) {
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	for _, code := range []string{"P000261", "P000262"} {
		t.Run(code, func(t *testing.T) {
			for _, scenario := range []string{"unit-edges", "mount-operands", "installed-contract"} {
				t.Run(scenario, func(t *testing.T) {
					command := exec.Command("python3", "-c", `
import json
import sys
from runtime.remote_access.package_diagnostics import dependency_refusal_stderr, loaded_observation
names = {f"happyranch-extra-{i:03}.service" for i in range(40)}
value = " ".join(sorted(names))
context = {name: value for name in ("Wants", "Requires", "After", "Before", "PartOf", "Conflicts", "BindsTo")}
context["RequiresMountsFor"] = "/private/credential-source/mount-context"
context["Environment"] = "SECRET_MUST_NOT_APPEAR"
context["LoadCredential"] = "/private/credential-source"
if sys.argv[2] == "mount-operands":
    dependency_refusal_stderr(sys.argv[1], "happyranch-connector.service", "RequiresMountsFor",
        "/private/credential-source/expected", "/private/credential-source/observed", context,
        missing={"/private/credential-source/missing"}, unexpected={"/private/credential-source/unexpected"})
    sys.exit(0)
if sys.argv[2] == "installed-contract":
    from pathlib import Path
    from unittest.mock import patch
    from runtime.remote_access import linux_package as package
    from runtime.remote_access.package_diagnostics import exception_refusal, exception_loaded
    # Supply external package-history/PID1 observations only. The real query
    # reader, parser, graph decisions, loaded_error and stderr writer all run.
    context.update({
        "LoadState": "loaded", "FragmentPath": "/etc/systemd/system/" + package.UNITS[0],
        "DropInPaths": "", "NeedDaemonReload": "no",
        "Wants": value + " network-online.target",
        "Requires": "", "Conflicts": "",
        "PartOf": package.UNITS[1] + " " + package.UNITS[2],
        "Before": package.UNITS[2], "BindsTo": "",
        "DefaultDependencies": "yes", "StopWhenUnneeded": "no",
        "RefuseManualStart": "no", "RefuseManualStop": "no",
    })
    if sys.argv[1] == "P000262":
        context["Wants"] += " " + package.UNITS[1]
    popen = package.subprocess.Popen
    def systemctl_observation(argv, **kwargs):
        assert argv[:3] == ["/usr/bin/systemctl", "show", package.UNITS[0]]
        raw = "".join(name.removeprefix("--property=") + "=" + context[name.removeprefix("--property=")] + "\n" for name in argv[3:])
        return popen([sys.executable, "-c", "import sys; sys.stdout.write(sys.argv[1])", raw], **kwargs)
    with patch.object(package.os, "geteuid", return_value=0), \
         patch.object(package, "_package_managed_history", return_value=(None, {}, None, True)), \
         patch.object(Path, "exists", return_value=False), \
         patch.object(package.subprocess, "Popen", side_effect=systemctl_observation):
        try:
            package._installed_managed_contract(_package=True)
        except package.PackageError as error:
            assert str(error) == "managed_loaded_contract_mismatch"
            assert exception_refusal(error) == ("managed_loaded_contract_mismatch", sys.argv[1])
            facts = exception_loaded(error)
            assert facts is not None
            assert len(json.dumps(facts, ensure_ascii=True, separators=(",", ":"))) <= 240
        else:
            raise AssertionError("actual installed-contract refusal was not reached")
    sys.exit(0)
dependency_refusal_stderr(sys.argv[1], "happyranch-connector.service", "After", value, value, context, missing=names, unexpected=names)
# The independently bounded carrier still accepts exactly its old contract.
facts = loaded_observation(0, "After", "subset", value, value, context, missing=names, unexpected=names)
assert len(json.dumps(facts, ensure_ascii=True, separators=(",", ":"))) <= 240
`, code, scenario)
					command.Dir = root
					var stderr bytes.Buffer
					command.Stderr = &stderr
					if out, err := command.Output(); err != nil {
						t.Fatalf("production writer: %v\n%s\n%s", err, out, stderr.String())
					}
					var names []string
					for i := 0; i < 40; i++ {
						names = append(names, fmt.Sprintf("happyranch-extra-%03d.service", i))
					}
					value := strings.Join(names, " ")
					if scenario == "unit-edges" {
						for _, field := range []string{"expected", "observed", "missing", "unexpected", "Wants", "Requires", "After", "Before", "PartOf", "Conflicts", "BindsTo"} {
							line := code + " happyranch-connector.service After " + field + "='" + value + "'\n"
							if !strings.Contains(stderr.String(), line) {
								t.Errorf("missing complete %s list (%d bytes, beyond 96/240 bounds)", field, len(value))
							}
						}
					} else if scenario == "installed-contract" {
						line := code + " happyranch-connector.service Wants After='" + value + "'\n"
						if !strings.Contains(stderr.String(), line) {
							t.Errorf("actual refusal lost its complete After context (%d bytes)", len(value))
						}
					} else {
						for _, field := range []string{"expected", "observed", "missing", "unexpected"} {
							line := code + " happyranch-connector.service RequiresMountsFor " + field + "='<withheld>'\n"
							if !strings.Contains(stderr.String(), line) {
								t.Errorf("mount %s operand was not explicitly withheld", field)
							}
						}
					}
					if strings.Contains(stderr.String(), " RequiresMountsFor=") {
						t.Error("path-bearing mount context disclosed")
					}
					if strings.Contains(stderr.String(), "SECRET_MUST_NOT_APPEAR") || strings.Contains(stderr.String(), "/private/credential-source") {
						t.Fatal("non-dependency operand disclosed")
					}
				})
			}
		})
	}
}
