//go:build linux

package sidecar

import (
	"encoding/json"
	"errors"
	"golang.org/x/sys/unix"
	"strconv"
	"sync/atomic"
)

var lifecycleDiagnosticActive atomic.Bool

// The guard's original acquisition is distinct from the enclosing Python child.
// Only closed scalars are forwarded; no exception, operand or arbitrary stderr.
func lifecyclePackageGuardValid(raw []byte) bool {
	var v []json.RawMessage
	if len(raw) > 450 || json.Unmarshal(raw, &v) != nil || len(v) != 20 {
		return false
	}
	text := func(i int, choices ...string) bool {
		var s string
		return json.Unmarshal(v[i], &s) == nil && lifecycleOneOf(s, choices...)
	}
	number := func(i, low, high int) bool {
		if string(v[i]) == "null" {
			return true
		}
		n, err := strconv.Atoi(string(v[i]))
		return err == nil && n >= low && n <= high && strconv.Itoa(n) == string(v[i])
	}
	boolean := func(i int) bool { return lifecycleOneOf(string(v[i]), "null", "true", "false") }
	if !text(0, "observed", "absent", "unavailable", "malformed", "unallowlisted", "truncated", "contradictory") ||
		!number(1, -64, 255) || !boolean(2) || !boolean(3) || !boolean(4) || !number(5, -64, 255) ||
		!text(6, "not_attempted", "failed", "started") || !text(7, "not_attempted", "pending", "failed", "reaped") ||
		!boolean(8) || !text(9, "not_requested", "requested", "sent", "not_found", "failed") ||
		!text(10, "not_opened", "unknown", "ok", "error") || !number(11, 0, 4097) || !number(12, 0, 4097) ||
		!number(13, 0, 4) || !boolean(14) || !text(15, "eof", "limit", "error", "cancelled", "not_started", "unavailable", "unknown") ||
		!text(16, "ok", "error", "not_opened", "unknown") {
		return false
	}
	for _, index := range []int{17, 18} {
		var codes []string
		if json.Unmarshal(v[index], &codes) != nil || index == 18 && (string(v[index]) == "null" || len(codes) > 4) {
			return false
		}
		previous := -1
		seen := map[string]bool{}
		for _, code := range codes {
			if seen[code] {
				return false
			}
			seen[code] = true
			if index == 18 {
				if lifecyclePackagePrivateSites[code] == "" {
					return false
				}
			} else {
				position := -1
				for i, allowed := range lifecyclePackageLossOrder {
					if allowed == code {
						position = i
					}
				}
				if position <= previous {
					return false
				}
				previous = position
			}
		}
	}
	if string(v[19]) != "null" {
		var site string
		if json.Unmarshal(v[19], &site) != nil || lifecyclePackagePrivateSites[site] == "" {
			return false
		}
	}
	return true
}

// A suppressed close remains an observation, never a changed return decision.
// The existing bounded stderr owners account for lost/capped delivery.
func lifecycleDiagnosticLoss(site string, cause error) {
	if cause != nil && lifecycleDiagnosticActive.Load() {
		observed := site
		if origin := lifecycleDiagnosticRefusal(cause); origin != nil && origin.Site != site {
			observed = origin.Site + ":" + site
		}
		_, _ = unix.Write(2, []byte("managed_diagnostic_loss:"+observed+"\n"))
	}
}

// Diagnostic errors preserve the original error language and unwrap identity.
// Neither the site nor the wrapper changes any admission decision.
type lifecycleDiagnosticFailure struct {
	site   string
	origin string
	cause  error
}

func (e *lifecycleDiagnosticFailure) Error() string { return e.cause.Error() }
func (e *lifecycleDiagnosticFailure) Unwrap() error { return e.cause }
func lifecycleDiagnosticError(site string, cause error, origins ...error) error {
	if cause == nil {
		return nil
	}
	var previous *lifecycleDiagnosticFailure
	if errors.As(cause, &previous) {
		return cause
	}
	failure := &lifecycleDiagnosticFailure{site: site, cause: cause}
	for _, origin := range origins {
		if errors.As(origin, &previous) {
			failure.origin = previous.origin
			if failure.origin == "" {
				failure.origin = previous.site
			}
			break
		}
	}
	return failure
}
func lifecycleDiagnosticRefusal(cause error) *lifecyclePackageRefusalDiagnostic {
	var failure *lifecycleDiagnosticFailure
	if errors.As(cause, &failure) {
		site := failure.origin
		if site == "" {
			site = failure.site
		}
		return &lifecyclePackageRefusalDiagnostic{Category: "managed_lifecycle_refused", Site: site}
	}
	return nil
}

// Boundary identity remains separate when a sanitized return retains an earlier origin.
func lifecycleDiagnosticBoundary(cause error) *lifecyclePackageRefusalDiagnostic {
	var failure *lifecycleDiagnosticFailure
	if errors.As(cause, &failure) && failure.origin != "" && failure.origin != failure.site {
		return &lifecyclePackageRefusalDiagnostic{Category: "managed_lifecycle_refused", Site: failure.site}
	}
	return nil
}

// Carry only the first failed reached predicate; preserve caller evaluation order.
func lifecycleDiagnosticCheck(origin *error, failure error) bool {
	if failure != nil && *origin == nil {
		*origin = failure
	}
	return failure == nil
}
