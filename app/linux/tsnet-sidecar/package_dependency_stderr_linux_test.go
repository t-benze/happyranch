package sidecar

import (
	"bytes"
	"fmt"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// Exercise the shipping Python diagnostic writer through its process stderr.
// This uses the retained native test venue, never the retired Python unit suite.
func TestDependencyRefusalFullStderr(t *testing.T) {
	root, err := filepath.Abs("../../..")
	if err != nil {
		t.Fatal(err)
	}
	for _, code := range []string{"P000261", "P000262"} {
		t.Run(code, func(t *testing.T) {
			command := exec.Command("python3", "-c", `
import sys
from runtime.remote_access.package_diagnostics import dependency_refusal_stderr, loaded_observation
names = {f"happyranch-extra-{i:03}.service" for i in range(40)}
value = " ".join(sorted(names))
context = {name: value for name in ("Wants", "Requires", "After", "Before", "PartOf", "Conflicts", "RequiresMountsFor", "BindsTo")}
context["Environment"] = "SECRET_MUST_NOT_APPEAR"
context["LoadCredential"] = "/private/credential-source"
dependency_refusal_stderr(sys.argv[1], "happyranch-connector.service", "After", value, value, context, missing=names, unexpected=names)
# The independently bounded carrier still accepts exactly its old contract.
import json
facts = loaded_observation(0, "After", "subset", value, value, context, missing=names, unexpected=names)
assert len(json.dumps(facts, ensure_ascii=True, separators=(",", ":"))) <= 240
`, code)
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
			for _, field := range []string{"expected", "observed", "missing", "unexpected", "Wants", "Requires", "After", "Before", "PartOf", "Conflicts", "RequiresMountsFor", "BindsTo"} {
				line := code + " happyranch-connector.service After " + field + "='" + value + "'\n"
				if !strings.Contains(stderr.String(), line) {
					t.Errorf("missing complete %s list (%d bytes, beyond 96/240 bounds)", field, len(value))
				}
			}
			if strings.Contains(stderr.String(), "SECRET_MUST_NOT_APPEAR") || strings.Contains(stderr.String(), "/private/credential-source") {
				t.Fatal("non-dependency operand disclosed")
			}
		})
	}
}
