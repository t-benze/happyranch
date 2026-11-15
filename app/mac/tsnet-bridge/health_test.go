package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/netip"
	"strings"
	"sync/atomic"
	"testing"
	"time"
	"unsafe"

	"tailscale.com/client/tailscale"
	"tailscale.com/ipn/ipnstate"
	"tailscale.com/net/stun"
	"tailscale.com/tailcfg"
	"tailscale.com/tsnet"
	"tailscale.com/types/key"
)

type fakeHealth struct {
	status *ipnstate.Status
	dm     *tailcfg.DERPMap
	wait   func(context.Context)
	pings  atomic.Int32
	calls  atomic.Int32
	t      *testing.T
}

func (f *fakeHealth) Status(ctx context.Context) (*ipnstate.Status, error) {
	f.calls.Add(1)
	deadline, ok := ctx.Deadline()
	if !ok || time.Until(deadline) > 10*time.Second {
		f.t.Error("missing whole-call cap")
	}
	if f.wait != nil {
		f.wait(ctx)
	}
	return f.status, ctx.Err()
}
func (f *fakeHealth) CurrentDERPMap(context.Context) (*tailcfg.DERPMap, error) {
	return f.dm, nil
}
func (f *fakeHealth) PingWithOpts(ctx context.Context, ip netip.Addr, kind tailcfg.PingType, _ tailscale.PingOpts) (*ipnstate.PingResult, error) {
	f.pings.Add(1)
	deadline, ok := ctx.Deadline()
	if !ok || time.Until(deadline) > 3*time.Second || kind != tailcfg.PingDisco || ip.String() != "100.64.0.1" {
		f.t.Error("ping escaped pinned peer, one-shot type or 3s bound")
	}
	return &ipnstate.PingResult{IP: ip.String(), Endpoint: "203.0.113.74:123", LatencySeconds: .012}, nil
}
func healthEngine(t *testing.T, f *fakeHealth, pinned key.NodePublic) *managedEngine {
	t.Helper()
	cleanEngine(t)
	ctx, cancel := context.WithCancel(context.Background())
	m := &managedEngine{ctx: ctx, cancel: cancel, ready: true, pinned: pinned, health: newManagedHealth(f)}
	srvMu.Lock()
	engineGeneration++
	m.generation = engineGeneration
	managed = m
	srvMu.Unlock()
	// Registered after cleanEngine: detach the synthetic engine before real Close.
	t.Cleanup(func() { cancel(); srvMu.Lock(); managed = nil; srv = nil; srvMu.Unlock() })
	return m
}
func healthString(t *testing.T) string {
	t.Helper()
	p := tsnet_health_json(10000)
	if p == nil {
		t.Fatal("health export returned NULL")
	}
	defer tsnet_free_string(p) // The real C allocation/free pair is exercised.
	var b []byte
	for i := 0; i <= 2048; i++ {
		c := *(*byte)(unsafe.Add(unsafe.Pointer(p), i))
		if c == 0 {
			return string(b)
		}
		b = append(b, c)
	}
	t.Fatal("health JSON exceeds 2 KiB")
	return ""
}
func TestHealthUnavailable(t *testing.T) {
	cleanEngine(t)
	for _, mode := range []string{"none", "legacy", "unready", "stopped"} {
		t.Run(mode, func(t *testing.T) {
			srvMu.Lock()
			srv = nil
			managed = nil
			if mode == "legacy" {
				srv = &tsnet.Server{}
			}
			if mode == "unready" || mode == "stopped" {
				ctx := context.Background()
				managed = &managedEngine{ctx: ctx, ready: mode == "stopped", generation: engineGeneration,
					health: newManagedHealth(&fakeHealth{t: t, status: &ipnstate.Status{BackendState: "Stopped"}})}
			}
			srvMu.Unlock()
			if p := tsnet_health_json(1); p != nil {
				tsnet_free_string(p)
				t.Error("unavailable engine produced health JSON")
			}
			errorMu.Lock()
			got := lastError
			errorMu.Unlock()
			if got != "Managed health unavailable" {
				t.Errorf("error=%q", got)
			}
			srvMu.Lock()
			managed = nil
			srv = nil
			srvMu.Unlock()
		})
	}
}
func TestHealthPinnedPrivacyAndCache(t *testing.T) {
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	var probes atomic.Int32
	done := make(chan struct{})
	t.Cleanup(func() { conn.Close(); <-done })
	go func() {
		defer close(done)
		buf := make([]byte, 2048)
		for {
			n, source, err := conn.ReadFromUDP(buf)
			if err != nil {
				return
			}
			probes.Add(1)
			if n < 20 {
				continue
			}
			var tx stun.TxID
			copy(tx[:], buf[8:20])
			conn.WriteToUDP(stun.Response(tx, netip.MustParseAddrPort("203.0.113.75:987")), source)
		}
	}()
	pinned, other := key.NewNode().Public(), key.NewNode().Public()
	f := &fakeHealth{t: t, status: &ipnstate.Status{BackendState: "Running", AuthURL: "https://secret.invalid/auth", Health: []string{"raw-health-secret"}, Peer: map[key.NodePublic]*ipnstate.PeerStatus{
		other:  {HostName: "other-peer-secret", TailscaleIPs: []netip.Addr{netip.MustParseAddr("100.64.0.99")}},
		pinned: {HostName: "expected-home", PublicKey: pinned, Online: true, CurAddr: "203.0.113.73:456", TailscaleIPs: []netip.Addr{netip.MustParseAddr("100.64.0.1")}},
	}}, dm: &tailcfg.DERPMap{Regions: map[int]*tailcfg.DERPRegion{1: {RegionCode: "hk", Nodes: []*tailcfg.DERPNode{{IPv4: "127.0.0.1", STUNPort: conn.LocalAddr().(*net.UDPAddr).Port}}}}}}
	m := healthEngine(t, f, pinned)
	for i, trigger := range []string{"join", "on_demand"} {
		s := healthString(t)
		var r struct {
			V       int
			Trigger string
			UDP     bool
			Peer    struct {
				Name, Path string
				RTT        int `json:"rtt_ms"`
			}
		}
		if json.Unmarshal([]byte(s), &r) != nil || r.V != 1 || r.Trigger != trigger || !r.UDP || r.Peer.Name != "expected-home" || r.Peer.Path != "direct" || r.Peer.RTT != 12 {
			t.Fatalf("unexpected record: %s", s)
		}
		var compact bytes.Buffer
		if json.Compact(&compact, []byte(s)) != nil || compact.String() != s {
			t.Fatal("not compact JSON")
		}
		for _, secret := range []string{"secret", "100.64.0.", "203.0.113.", pinned.String(), other.String()} {
			if strings.Contains(s, secret) {
				t.Errorf("record leaked %q", secret)
			}
		}
		if probes.Load() != 1 || f.pings.Load() != int32(i+1) {
			t.Fatal("probe cache or one ping per call violated")
		}
	}
	// A new generation gets a fresh runner and a new join trigger.
	srvMu.Lock()
	m.health = newManagedHealth(f)
	engineGeneration++
	m.generation = engineGeneration
	srvMu.Unlock()
	if s := healthString(t); !strings.Contains(s, `"trigger":"join"`) || probes.Load() != 2 {
		t.Fatal("generation reused old runner")
	}
}
func TestHealthMissingPeer(t *testing.T) {
	f := &fakeHealth{t: t, status: &ipnstate.Status{BackendState: "Running", Peer: map[key.NodePublic]*ipnstate.PeerStatus{key.NewNode().Public(): {HostName: "unselected-secret"}}}}
	healthEngine(t, f, key.NodePublic{})
	s := healthString(t)
	if !strings.Contains(s, `"name":""`) || !strings.Contains(s, `"path":"none"`) || strings.Contains(s, "secret") || f.pings.Load() != 0 {
		t.Fatal("missing peer selected another node")
	}
}
func TestHealthDeadlineAndCancel(t *testing.T) {
	for _, mode := range []string{"status timeout", "cancel"} {
		t.Run(mode, func(t *testing.T) {
			entered := make(chan struct{})
			f := &fakeHealth{t: t, wait: func(ctx context.Context) { close(entered); <-ctx.Done() }}
			m := healthEngine(t, f, key.NodePublic{})
			if mode == "cancel" {
				go func() { <-entered; cancelManaged() }() // Requires srvMu while Status is blocked.
			}
			start := time.Now()
			_, err := managedHealthJSON(40)
			if !errors.Is(err, errHealth) || time.Since(start) > time.Second {
				t.Fatal("call ignored timeout/cancel")
			}
			if m.health.joined {
				t.Fatal("failed export consumed join trigger")
			}
		})
	}
}

func TestHealthConcurrentBound(t *testing.T) {
	entered, release := make(chan struct{}, 1), make(chan struct{})
	f := &fakeHealth{t: t, status: &ipnstate.Status{BackendState: "Running"}, wait: func(ctx context.Context) {
		entered <- struct{}{}
		select {
		case <-release:
		case <-ctx.Done():
		}
	}}
	healthEngine(t, f, key.NodePublic{})
	first := make(chan error, 1)
	go func() { _, err := managedHealthJSON(1000); first <- err }()
	<-entered
	_, err := managedHealthJSON(40)
	calls := f.calls.Load()
	close(release)
	firstErr := <-first
	if err == nil || calls != 1 || firstErr != nil {
		t.Fatalf("overlap: second=%v calls=%d first=%v", err, calls, firstErr)
	}
	if s := healthString(t); !strings.Contains(s, `"trigger":"on_demand"`) {
		t.Fatal("successful first call did not consume join")
	}
}
