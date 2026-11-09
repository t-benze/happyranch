package sidecar

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"
)

// Distinct risk from the CLI tests: the native preflight capture and native
// serializer must validate the exact identity before transferring any bytes.
func TestLifecycleStartDiagnosticCarrier(t *testing.T) {
	for _, scenario := range []string{"registered", "unknown", "null-category", "newline", "control", "path", "long", "malformed", "duplicate", "extra-field", "loaded", "oversize", "extra-output"} {
		t.Run(scenario, func(t *testing.T) {
			raw := `{"category":"managed_loaded_contract_mismatch","site":"P000263"}` + "\n"
			switch scenario {
			case "unknown":
				raw = strings.Replace(raw, "P000263", "P999999", 1)
			case "null-category":
				raw = strings.Replace(raw, `"managed_loaded_contract_mismatch"`, "null", 1)
			case "newline", "control", "path", "long":
				value := map[string]string{"newline": "P000263\nSENTINEL", "control": "P000263\x1bSENTINEL", "path": "/private/SENTINEL/daemon.token", "long": strings.Repeat("SENTINEL", 128)}[scenario]
				encoded, _ := json.Marshal(value)
				raw = strings.Replace(raw, `"P000263"`, string(encoded), 1)
			case "malformed":
				raw = "{"
			case "duplicate":
				raw = strings.Replace(raw, `"site":`, `"site":"P000263","site":`, 1)
			case "extra-field":
				raw = strings.Replace(raw, "}", `,"extra":"SENTINEL"}`, 1)
			case "loaded":
				raw = strings.Replace(raw, "}", `,"loaded":["SENTINEL"]}`, 1)
			case "oversize":
				raw = strings.Repeat("SENTINEL", 600)
			case "extra-output":
				raw += "SENTINEL\n"
			}
			var capture lifecycleEligibilityCapture
			// Chunking cannot turn overflow or malformed output into a valid frame.
			for _, piece := range []string{raw[:1], raw[1:]} {
				if n, err := capture.Write([]byte(piece)); n != len(piece) || err != nil {
					t.Fatal("diagnostic capture changed command disposition")
				}
			}
			failure := lifecycleDiagnosticError("G000150", ErrLifecycle).(*lifecycleDiagnosticFailure)
			failure.eligibility = capture.origin()
			var output bytes.Buffer
			writeLifecycleStartRefusal(&output, "eligibility_preflight", failure)
			expected := `{"branch":"eligibility_preflight",`
			if scenario == "registered" {
				expected += `"eligibility":{"category":"managed_loaded_contract_mismatch","site":"P000263"},`
			}
			expected += `"origin":{"category":"managed_lifecycle_refused","site":"G000150"},"version":1}` + "\n"
			if output.String() != expected || output.Len() > 512 {
				t.Fatalf("native transfer: got %q, want %q", output.String(), expected)
			}
		})
	}
	for _, branch := range []string{"unknown", "reply\nSENTINEL", "reply\x1bSENTINEL", "/private/SENTINEL", strings.Repeat("SENTINEL", 100)} {
		var output bytes.Buffer
		writeLifecycleStartRefusal(&output, branch, lifecycleDiagnosticError("G000150", ErrLifecycle))
		if output.Len() != 0 {
			t.Fatalf("unregistered branch disclosed: %q", output.String())
		}
	}
}
