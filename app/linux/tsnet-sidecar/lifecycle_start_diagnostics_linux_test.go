package sidecar

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
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
	for _, site := range []string{"P900010", "P900011", "P900012", "P900013", "P900014", "P900015", "P900016", "P900017"} {
		t.Run(site, func(t *testing.T) {
			raw := `{"category":"managed_operational_error","site":"` + site + `"}` + "\n"
			var capture lifecycleEligibilityCapture
			_, _ = capture.Write([]byte(raw))
			failure := lifecycleDiagnosticError("G000150", ErrLifecycle).(*lifecycleDiagnosticFailure)
			failure.eligibility = capture.origin()
			var output bytes.Buffer
			writeLifecycleStartRefusal(&output, "eligibility_preflight", failure)
			expected := `{"branch":"eligibility_preflight","eligibility":{"category":"managed_operational_error","site":"` + site + `"},"origin":{"category":"managed_lifecycle_refused","site":"G000150"},"version":1}` + "\n"
			if output.String() != expected {
				t.Fatalf("fallback native transfer: %q, want %q", output.String(), expected)
			}
		})
	}

	for _, disposition := range []string{"unknown", "", "exit_nonzero\nSENTINEL", "exit_nonzero\x1bSENTINEL", "/private/SENTINEL", strings.Repeat("SENTINEL", 128)} {
		failure := lifecycleDiagnosticError("G000150", ErrLifecycle).(*lifecycleDiagnosticFailure)
		failure.eligibilityDisposition = disposition
		var output bytes.Buffer
		writeLifecycleStartRefusal(&output, "eligibility_preflight", failure)
		expected := `{"branch":"eligibility_preflight","origin":{"category":"managed_lifecycle_refused","site":"G000150"},"version":1}` + "\n"
		if output.String() != expected {
			t.Fatalf("invalid disposition leaked: %q", output.String())
		}
	}
}

// Distinct producer risk: classify actual cmd.Run results before the existing
// native serializer. The CLI table separately owns transport/sink validation.
func TestLifecycleEligibilityChildDisposition(t *testing.T) {
	for _, scenario := range []string{"spawn_failed", "exit_nonzero", "signaled", "context_done", "deadline_elapsed", "success"} {
		t.Run(scenario, func(t *testing.T) {
			ctx := context.Background()
			if scenario == "context_done" {
				var cancel context.CancelFunc
				ctx, cancel = context.WithCancel(ctx)
				cancel()
			}
			if scenario == "deadline_elapsed" {
				var cancel context.CancelFunc
				ctx, cancel = context.WithDeadline(ctx, time.Unix(1, 0))
				defer cancel()
			}
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestLifecycleEligibilityChildProcess$")
			cmd.Env = append(os.Environ(), "HR_ELIGIBILITY_CHILD="+scenario)
			if scenario == "spawn_failed" {
				cmd.Path = filepath.Join(t.TempDir(), "absent")
			}
			result := cmd.Run()
			disposition := lifecycleEligibilityChildDisposition(result)
			failure := lifecycleDiagnosticError("G000150", ErrLifecycle).(*lifecycleDiagnosticFailure)
			failure.eligibilityDisposition = disposition
			var output bytes.Buffer
			writeLifecycleStartRefusal(&output, "eligibility_preflight", failure)
			var frame map[string]any
			if json.Unmarshal(output.Bytes(), &frame) != nil {
				t.Fatal("native output unavailable")
			}
			if scenario == "success" {
				if result != nil || disposition != "" || frame["disposition"] != nil {
					t.Fatal("successful child gained failure detail")
				}
			} else if frame["disposition"] != scenario || result == nil {
				t.Fatalf("SAME cmd.Run native output: %q, expected disposition %s", output.String(), scenario)
			}
		})
	}
}

func TestLifecycleEligibilityChildProcess(t *testing.T) {
	switch os.Getenv("HR_ELIGIBILITY_CHILD") {
	case "exit_nonzero":
		os.Exit(7)
	case "signaled":
		_ = syscall.Kill(os.Getpid(), syscall.SIGKILL)
	case "success":
		os.Exit(0)
	}
}
