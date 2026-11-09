//go:build linux

package sidecar

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os/exec"
	"strings"
	"syscall"
)

// Closed public start-branch registry, mirrored by lifecycle_diagnostics.py.
var lifecycleStartBranches = map[string]string{
	"request_open": "managed_lifecycle_refused", "stopped_current": "managed_lifecycle_refused",
	"eligibility_preflight": "managed_lifecycle_refused", "policy_write": "managed_lifecycle_refused",
	"manager_start": "managed_lifecycle_refused", "reply": "managed_lifecycle_refused",
}

// Closed observation of the SAME cmd.Run error. Never sample context/time again.
func lifecycleEligibilityChildDisposition(cause error) string {
	if cause == nil {
		return ""
	}
	if errors.Is(cause, context.DeadlineExceeded) {
		return "deadline_elapsed"
	}
	if errors.Is(cause, context.Canceled) {
		return "context_done"
	}
	var exited *exec.ExitError
	if errors.As(cause, &exited) {
		if status, ok := exited.Sys().(syscall.WaitStatus); ok && status.Signaled() {
			return "signaled"
		}
		return "exit_nonzero"
	}
	return "spawn_failed"
}

func lifecycleStartOriginValid(origin *lifecyclePackageRefusalDiagnostic, native bool) bool {
	if origin == nil || origin.Category == "" || (native && !strings.HasPrefix(origin.Site, "G")) ||
		(!native && !strings.HasPrefix(origin.Site, "P") && !strings.HasPrefix(origin.Site, "G")) {
		return false
	}
	category, registered := lifecyclePackagePrivateSites[origin.Site]
	return registered && category != "" && category == origin.Category && len(origin.Loaded) == 0
}

// stdout already belongs to the bounded Python helper transport. Never write
// error text, stderr, loaded facts or operands into this optional failure frame.
func writeLifecycleStartRefusal(output io.Writer, branch string, cause error) {
	category, registered := lifecycleStartBranches[branch]
	if !registered || category != "managed_lifecycle_refused" {
		return
	}
	frame := map[string]any{"version": 1, "branch": branch}
	if origin := lifecycleDiagnosticRefusal(cause); lifecycleStartOriginValid(origin, true) {
		frame["origin"] = origin
	}
	var failure *lifecycleDiagnosticFailure
	if (branch == "eligibility_preflight" || branch == "reply") && errors.As(cause, &failure) {
		if lifecycleStartOriginValid(failure.eligibility, false) {
			frame["eligibility"] = failure.eligibility
		}
		if lifecycleOneOf(failure.eligibilityDisposition, "spawn_failed", "exit_nonzero", "signaled", "context_done", "deadline_elapsed") {
			frame["disposition"] = failure.eligibilityDisposition
		}
	}
	raw, err := json.Marshal(frame)
	if err == nil && len(raw)+1 <= 512 {
		_, _ = output.Write(append(raw, '\n'))
	}
}

// Consume the SAME eligibility invocation's stdout without influencing its
// exit or deadline decision. Overflow is latched; all later bytes are discarded.
type lifecycleEligibilityCapture struct {
	raw      []byte
	overflow bool
}

func (c *lifecycleEligibilityCapture) Write(raw []byte) (int, error) {
	if !c.overflow {
		if len(raw) > 512-len(c.raw) {
			c.raw, c.overflow = nil, true
		} else {
			c.raw = append(c.raw, raw...)
		}
	}
	return len(raw), nil
}

func (c *lifecycleEligibilityCapture) origin() *lifecyclePackageRefusalDiagnostic {
	var origin lifecyclePackageRefusalDiagnostic
	if c.overflow || json.Unmarshal(c.raw, &origin) != nil || !lifecycleStartOriginValid(&origin, false) {
		return nil
	}
	// Re-encoding the exact two-field closed grammar rejects duplicates,
	// unknown fields, partial output and noncanonical/trailing data.
	raw, err := json.Marshal(map[string]string{"category": origin.Category, "site": origin.Site})
	if err != nil || !bytes.Equal(c.raw, append(raw, '\n')) {
		return nil
	}
	return &origin
}
