package tsnethealth

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"tailscale.com/net/stun"
	"tailscale.com/tailcfg"
)

func responder(t *testing.T, mapped string, reply bool) (*tailcfg.DERPNode, *atomic.Int32) {
	t.Helper()
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	count := new(atomic.Int32)
	done := make(chan struct{})
	t.Cleanup(func() { conn.Close(); <-done })
	go func() {
		defer close(done)
		buffer := make([]byte, 2048)
		for {
			n, source, err := conn.ReadFromUDP(buffer)
			if err != nil {
				return
			}
			count.Add(1)
			if !reply || n < 20 {
				continue
			}
			var tx stun.TxID
			copy(tx[:], buffer[8:20])
			// Neither malformed packets nor a different transaction may win.
			conn.WriteToUDP([]byte("invalid"), source)
			conn.WriteToUDP(stun.Response(stun.NewTxID(), netip.MustParseAddrPort("203.0.113.99:99")), source)
			conn.WriteToUDP(stun.Response(tx, netip.MustParseAddrPort(mapped)), source)
			// A repeated valid response with another mapping must be ignored.
			conn.WriteToUDP(stun.Response(tx, netip.MustParseAddrPort("203.0.113.99:99")), source)
		}
	}()
	return &tailcfg.DERPNode{IPv4: "127.0.0.1", HostName: "unused.invalid", STUNPort: conn.LocalAddr().(*net.UDPAddr).Port}, count
}

func TestProbeMapOnly(t *testing.T) {
	for _, varies := range []bool{false, true} {
		t.Run(fmt.Sprint(varies), func(t *testing.T) {
			dm := &tailcfg.DERPMap{Regions: map[int]*tailcfg.DERPRegion{}}
			counts := map[int]*atomic.Int32{}
			decoy, decoyCount := responder(t, "", false)
			disabled := *decoy
			disabled.STUNPort = -1
			for id := 10; id > 0; id-- {
				mapped := "198.51.100.1:1234"
				if varies && id == 8 {
					mapped = "198.51.100.1:5678"
				}
				node, count := responder(t, mapped, true)
				node.STUNOnly = id%2 == 0
				counts[id] = count
				dm.Regions[id] = &tailcfg.DERPRegion{RegionID: id, Nodes: []*tailcfg.DERPNode{nil, &disabled, node, decoy}}
			}
			unmapped, unmappedCount := responder(t, "198.51.100.1:1234", true)
			assertObserverControl(t, unmapped, unmappedCount)
			got := Probe(context.Background(), dm)
			if unmappedCount.Load() != 1 {
				t.Fatal("off-map observer received a packet")
			}
			if len(got.DERPMillis) != 8 || !got.UDP || got.NATVaries != varies {
				t.Fatalf("measurements: %+v; want 8 regions, UDP, NAT varies=%v", got, varies)
			}
			for id, count := range counts {
				_, measured := got.DERPMillis[id]
				if measured != (id <= 8) || (id <= 8 && (count.Load() != 1 || got.DERPMillis[id] < 0)) || (id > 8 && count.Load() != 0) {
					t.Errorf("region %d: measured=%v packets=%d", id, measured, count.Load())
				}
			}
			if decoyCount.Load() != 0 {
				t.Fatal("sent to disabled, non-first or unmapped node")
			}
			data, err := json.Marshal(got)
			if err != nil || strings.Contains(string(data), "198.51.100") || strings.Contains(string(data), "127.0.0.1") || strings.Contains(string(data), "unused.invalid") {
				t.Fatalf("address retained: %s (%v)", data, err)
			}
		})
	}
}

func TestProbeEmpty(t *testing.T) {
	observer, count := responder(t, "198.51.100.1:1234", true)
	assertObserverControl(t, observer, count)
	node := *observer
	node.STUNPort = -1
	for _, dm := range []*tailcfg.DERPMap{nil, {}, {Regions: map[int]*tailcfg.DERPRegion{1: {RegionID: 1, Nodes: []*tailcfg.DERPNode{&node}}}}} {
		got := Probe(context.Background(), dm)
		if got.DERPMillis == nil || len(got.DERPMillis) != 0 || got.UDP || got.NATVaries || count.Load() != 1 {
			t.Fatalf("empty/disabled map must send nothing: %+v packets=%d", got, count.Load())
		}
	}
}

func TestNodeAddress(t *testing.T) {
	for _, test := range []struct{ v4, v6, host, want string }{
		{"127.0.0.1", "::1", "unused.invalid", "127.0.0.1:3478"},
		{"invalid", "::1", "unused.invalid", "[::1]:3478"},
		{"invalid", "invalid", "127.0.0.1", "127.0.0.1:3478"},
	} {
		got, err := nodeAddress(context.Background(), &tailcfg.DERPNode{IPv4: test.v4, IPv6: test.v6, HostName: test.host})
		if err != nil || got.String() != test.want {
			t.Errorf("destination=%v (%v), want %s", got, err, test.want)
		}
	}
	for _, port := range []int{-2, 65536} {
		if _, err := nodeAddress(context.Background(), &tailcfg.DERPNode{STUNPort: port}); err == nil {
			t.Errorf("accepted invalid port %d", port)
		}
	}
}

func TestProbeDeadline(t *testing.T) {
	node, count := responder(t, "", false)
	dm := &tailcfg.DERPMap{Regions: map[int]*tailcfg.DERPRegion{1: {RegionID: 1, Nodes: []*tailcfg.DERPNode{node}}}}
	start := time.Now()
	got := Probe(context.Background(), dm)
	if elapsed := time.Since(start); elapsed < 4*time.Second || elapsed > 6*time.Second || got.UDP || count.Load() != 1 {
		t.Fatalf("deadline: elapsed=%v result=%+v packets=%d", elapsed, got, count.Load())
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	start = time.Now()
	Probe(ctx, dm)
	if time.Since(start) > time.Second || count.Load() != 1 {
		t.Fatal("cancelled probe sent traffic or failed to return promptly")
	}
}

// The same independent UDP observer must catch a request when its endpoint is
// in the map, before asserting that the main scenario sends it nothing more.
func assertObserverControl(t *testing.T, node *tailcfg.DERPNode, count *atomic.Int32) {
	t.Helper()
	dm := &tailcfg.DERPMap{Regions: map[int]*tailcfg.DERPRegion{1: {RegionID: 1, Nodes: []*tailcfg.DERPNode{node}}}}
	got := Probe(context.Background(), dm)
	if !got.UDP || count.Load() != 1 {
		t.Fatalf("observer positive control: result=%+v packets=%d", got, count.Load())
	}
}

func TestRefreshPreservesReplyDuringDNS(t *testing.T) {
	for _, untilDeadline := range []bool{true, false} {
		t.Run(fmt.Sprint(untilDeadline), func(t *testing.T) {
			node, count := responder(t, "198.51.100.1:1234", true)
			original := net.DefaultResolver
			t.Cleanup(func() { net.DefaultResolver = original })
			// No DNS transport: the existing production resolver boundary is supplied
			// with a synthetic failure. No tests in this package run in parallel.
			net.DefaultResolver = &net.Resolver{PreferGo: true, Dial: func(ctx context.Context, _, _ string) (net.Conn, error) {
				if untilDeadline {
					<-ctx.Done()
					return nil, ctx.Err()
				}
				select {
				case <-ctx.Done():
					return nil, ctx.Err()
				case <-time.After(time.Second):
					return nil, errors.New("synthetic DNS failure")
				}
			}}
			dm := &tailcfg.DERPMap{Regions: map[int]*tailcfg.DERPRegion{
				1: {RegionID: 1, Nodes: []*tailcfg.DERPNode{node}},
				2: {RegionID: 2, Nodes: []*tailcfg.DERPNode{{HostName: "slow.invalid"}}},
			}}
			now := time.Time{}
			r := NewRunner(func() time.Time { return now })
			got, _, available := r.Refresh(context.Background(), dm, false)
			rtt, measured := got.DERPMillis[1]
			if !available || !got.UDP || !measured || len(got.DERPMillis) != 1 || count.Load() != 1 {
				t.Fatalf("lost reply during DNS: result=%+v available=%v packets=%d", got, available, count.Load())
			}
			if rtt < 0 || rtt >= 750 {
				t.Fatalf("RTT includes unrelated DNS stall: %dms", rtt)
			}
			now = now.Add(time.Minute)
			cached, age, available := r.Refresh(context.Background(), nil, false)
			cachedRTT, measured := cached.DERPMillis[1]
			if !available || !cached.UDP || !measured || cachedRTT != rtt || age != time.Minute || count.Load() != 1 {
				t.Fatalf("successful reply missing from cache: result=%+v age=%v available=%v packets=%d", cached, age, available, count.Load())
			}
		})
	}
}
