package tsnethealth

import (
	"context"
	"errors"
	"maps"
	"sync"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tailcfg"
)

// Runner owns refresh limits for one tailnet. Construct one per consumer and
// retain it across refreshes. The supplied clock must be safe for concurrent use.
type Runner struct {
	mu                                        sync.Mutex
	now                                       func() time.Time
	probing, pinging, probed, changed, cached bool
	probeStart, measured, lastChange          time.Time
	result                                    ProbeResult
}

func NewRunner(now func() time.Time) *Runner {
	if now == nil {
		now = time.Now
	}
	return &Runner{now: now}
}

// Refresh returns cached measurements and their age when limited or in flight.
// available is false until the first measurement completes. Join bypasses only
// the ten-minute window, never the one-in-flight limit. Failed probes are cached.
func (r *Runner) Refresh(ctx context.Context, dm *tailcfg.DERPMap, join bool) (result ProbeResult, age time.Duration, available bool) {
	r.mu.Lock()
	if r.probing || (r.probed && !join && r.now().Sub(r.probeStart) < 10*time.Minute) {
		result = r.result
		result.DERPMillis = maps.Clone(result.DERPMillis)
		age, available = max(0, r.now().Sub(r.measured)), r.cached
		r.mu.Unlock()
		return
	}
	r.probing, r.probed, r.probeStart = true, true, r.now()
	r.mu.Unlock()
	result = Probe(ctx, dm)
	r.mu.Lock()
	r.result, r.measured, r.probing = result, r.now(), false
	r.cached = true
	r.result.DERPMillis = maps.Clone(result.DERPMillis)
	r.mu.Unlock()
	return result, 0, true
}

// Ping invokes one disco-ping adapter, with a three-second context and at most
// one invocation in flight. The adapter must honor ctx and send exactly one ping.
// The consumer schedules visible-window Mac calls every minute or sidecar calls
// every five minutes; join/on-demand calls use the same in-flight/timeout limit.
func (r *Runner) Ping(ctx context.Context, ping func(context.Context) (*ipnstate.PingResult, error)) (*ipnstate.PingResult, error) {
	r.mu.Lock()
	if r.pinging {
		r.mu.Unlock()
		return nil, errors.New("health ping already in flight")
	}
	r.pinging = true
	r.mu.Unlock()
	defer func() { r.mu.Lock(); r.pinging = false; r.mu.Unlock() }()
	ctx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	return ping(ctx)
}

// AllowChange reserves the next change record, at most once per thirty seconds.
func (r *Runner) AllowChange() bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.changed && r.now().Sub(r.lastChange) < 30*time.Second {
		return false
	}
	r.changed, r.lastChange = true, r.now()
	return true
}
