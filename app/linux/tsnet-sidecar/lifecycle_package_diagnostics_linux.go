//go:build linux

package sidecar

import "errors"

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
