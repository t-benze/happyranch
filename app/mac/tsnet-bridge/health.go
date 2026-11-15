package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/netip"
	"time"

	"happyranch/tsnethealth"
	"tailscale.com/client/tailscale"
	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tailcfg"
)

type healthClient interface {
	Status(context.Context) (*ipnstate.Status, error)
	CurrentDERPMap(context.Context) (*tailcfg.DERPMap, error)
	PingWithOpts(context.Context, netip.Addr, tailcfg.PingType, tailscale.PingOpts) (*ipnstate.PingResult, error)
}

type managedHealth struct {
	client healthClient
	runner *tsnethealth.Runner
	gate   chan struct{}
	joined bool // protected by gate, including the final successful export
}

func newManagedHealth(client healthClient) *managedHealth {
	return &managedHealth{client: client, runner: tsnethealth.NewRunner(nil), gate: make(chan struct{}, 1)}
}

var errHealth = errors.New("managed health unavailable")

func managedHealthJSON(timeoutMs int) ([]byte, error) {
	srvMu.Lock()
	m := managed
	if !managedActive(m) || m.health == nil {
		srvMu.Unlock()
		return nil, errHealth
	}
	h, parent := m.health, m.ctx
	srvMu.Unlock()
	timeout := 10 * time.Second
	if timeoutMs > 0 && timeoutMs < 10000 {
		timeout = time.Duration(timeoutMs) * time.Millisecond
	}
	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	// Health serialization is separately cancellable. Dial/cancel/close never
	// acquire this gate, and srvMu is never held across LocalAPI or network I/O.
	select {
	case h.gate <- struct{}{}:
		defer func() { <-h.gate }()
	case <-ctx.Done():
		return nil, errHealth
	}
	srvMu.Lock()
	active, expected := managedActive(m), m.pinned
	srvMu.Unlock()
	if !active || ctx.Err() != nil {
		return nil, errHealth
	}
	status, err := h.client.Status(ctx)
	if err != nil || status == nil || status.BackendState != "Running" {
		return nil, errHealth
	}
	dm, err := h.client.CurrentDERPMap(ctx)
	if err != nil {
		dm = nil // no fallback map or raw error in the record
	}
	// The fresh per-generation runner needs no join bypass. Failed first calls
	// must also retain the ten-minute cap when retrying the first successful join.
	probe, age, _ := h.runner.Refresh(ctx, dm, false)
	var ping *ipnstate.PingResult
	if peer := status.Peer[expected]; !expected.IsZero() && peer != nil && len(peer.TailscaleIPs) > 0 {
		ping, err = h.runner.Ping(ctx, func(ctx context.Context) (*ipnstate.PingResult, error) {
			return h.client.PingWithOpts(ctx, peer.TailscaleIPs[0], tailcfg.PingDisco, tailscale.PingOpts{})
		})
		if err != nil {
			ping = nil
		}
	}
	trigger := "on_demand"
	if !h.joined {
		trigger = "join"
	}
	record, err := tsnethealth.BuildRecord(time.Now(), trigger, status, expected, ping, dm, probe, age)
	if err != nil {
		return nil, errHealth
	}
	data, err := json.Marshal(record)
	srvMu.Lock()
	defer srvMu.Unlock()
	if err != nil || ctx.Err() != nil || !managedActive(m) || m.pinned != expected {
		return nil, errHealth
	}
	h.joined = true
	return data, nil
}
