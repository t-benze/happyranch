//go:build linux

package sidecar

import (
	"encoding/json"
	"errors"
	"golang.org/x/sys/unix"
	"regexp"
	"strconv"
	"strings"
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
	site                   string
	origin                 string
	cause                  error
	eligibility            *lifecyclePackageRefusalDiagnostic
	eligibilityDisposition string
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

// Same private positional grammar as Python loaded_projection. No raw stderr,
// command/environment/credential operands or new acquisition/channel/budget.
var lifecycleLoadedSafe = regexp.MustCompile(`^[A-Za-z0-9_./:@ \x5c"'~-]*$`)
var lifecycleLoadedProperty = regexp.MustCompile(`^[A-Za-z][A-Za-z0-9]{0,31}$`)

func lifecyclePackageLoadedValid(raw []byte) bool {
	var facts []json.RawMessage
	if len(raw) > 240 || json.Unmarshal(raw, &facts) != nil || (len(facts) != 13 && len(facts) != 16) {
		return false
	}
	offset := 0
	if len(facts) == 16 {
		if string(facts[0]) != "2" {
			return false
		}
		offset = 1
	}
	unit, err := strconv.Atoi(string(facts[offset]))
	if err != nil || unit < 0 || unit > 2 || strconv.Itoa(unit) != string(facts[offset]) {
		return false
	}
	var prop, relation string
	if json.Unmarshal(facts[offset+1], &prop) != nil || !lifecycleLoadedProperty.MatchString(prop) ||
		json.Unmarshal(facts[offset+2], &relation) != nil || !lifecycleOneOf(relation, "equal", "subset", "after", "peers", "query") {
		return false
	}
	sensitive := lifecycleOneOf(prop, "Environment", "EnvironmentFiles", "PassEnvironment", "LoadCredential", "LoadCredentialEncrypted", "ExecStartEx", "ExecStartPreEx", "ExecStartPostEx", "ExecStop", "ExecStopPost")
	if offset == 1 {
		if prop == "Query" && relation == "query" {
			// Missing requested names only; Python enforces the same empty value slots.
			for i := 5; i < len(facts); i++ {
				var value string
				if json.Unmarshal(facts[i], &value) != nil || i == 5 && value != "=0:" || i > 5 && value != "?" {
					return false
				}
			}
		} else if !lifecycleOneOf(prop, "Wants", "Requires", "After", "Before", "PartOf", "Conflicts", "RequiresMountsFor", "BindsTo") || relation == "query" {
			return false
		}
	}
	for i := offset + 3; i < len(facts); i++ {
		var value string
		if json.Unmarshal(facts[i], &value) != nil || len(value) == 0 ||
			!lifecycleOneOf(value[:1], "=", "~", "!", "?") ||
			(value[0] == '!' || value[0] == '?') && len(value) != 1 ||
			!lifecycleLoadedSafe.MatchString(value[1:]) || sensitive && i < 5 && value != "!" {
			return false
		}
		if offset == 1 && (i == 4 || i == 5) && value != "!" && value != "?" {
			count, _, found := strings.Cut(value[1:], ":")
			n, err := strconv.Atoi(count)
			if !found || err != nil || n < 0 || len(count) > 5 || strconv.Itoa(n) != count {
				return false
			}
		}
	}
	return true
}

func lifecyclePackageLoadedShrink(raw *json.RawMessage) bool {
	var facts []json.RawMessage
	if !lifecyclePackageLoadedValid(*raw) || json.Unmarshal(*raw, &facts) != nil {
		return false
	}
	groups := [][2]int{{3, 13}}
	if len(facts) == 16 {
		groups = [][2]int{{8, 16}, {6, 8}, {4, 6}}
	}
	selected := -1
	for _, group := range groups {
		longest := 1
		for i := group[0]; i < group[1]; i++ {
			var value string
			_ = json.Unmarshal(facts[i], &value)
			floor := 1
			if len(facts) == 16 && (i == 4 || i == 5) && (value[0] == '=' || value[0] == '~') {
				floor = strings.IndexByte(value, ':') + 1
			}
			if len(value) > floor && len(value) > longest {
				longest, selected = len(value), i
			}
		}
		if selected >= 0 {
			break
		}
	}
	if selected < 0 {
		return false
	}
	var value string
	_ = json.Unmarshal(facts[selected], &value)
	facts[selected], _ = json.Marshal("~" + value[1:len(value)-1])
	*raw, _ = json.Marshal(facts)
	return true
}
