package tsnethealth

import (
	"context"
	"net"
	"net/netip"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/net/stun"
	"tailscale.com/tailcfg"
)

func TestRefreshConcurrent(t *testing.T) {
	conn, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.IPv4(127, 0, 0, 1)})
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	conn.SetReadDeadline(time.Now().Add(3 * time.Second))
	dm := &tailcfg.DERPMap{Regions: map[int]*tailcfg.DERPRegion{1: {Nodes: []*tailcfg.DERPNode{{IPv4: "127.0.0.1", STUNPort: conn.LocalAddr().(*net.UDPAddr).Port}}}}}
	r := NewRunner(nil)
	done := make(chan ProbeResult, 1)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	go func() { p, _, _ := r.Refresh(ctx, dm, true); done <- p }()
	buffer := make([]byte, 2048)
	n, source, err := conn.ReadFromUDP(buffer)
	if err != nil || n < 20 {
		t.Fatalf("first request: %d %v", n, err)
	}
	var tx stun.TxID
	copy(tx[:], buffer[8:20])
	var extra atomic.Int32
	readerDone := make(chan struct{})
	go func() {
		defer close(readerDone)
		for {
			if _, _, err := conn.ReadFromUDP(buffer); err != nil {
				return
			}
			extra.Add(1)
		}
	}()
	var wg sync.WaitGroup
	for i := 0; i < 32; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			p, _, available := r.Refresh(ctx, dm, true)
			if available || p.UDP {
				t.Error("concurrent caller launched/completed another probe")
			}
		}()
	}
	wg.Wait()
	conn.WriteToUDP(stun.Response(tx, netip.MustParseAddrPort("198.51.100.1:1234")), source)
	first := <-done
	conn.Close()
	<-readerDone
	if !first.UDP || extra.Load() != 0 {
		t.Fatalf("shared probe: UDP=%v extra packets=%d", first.UDP, extra.Load())
	}
	p, _, available := r.Refresh(context.Background(), dm, false)
	if !available || !p.UDP || len(p.DERPMillis) != 1 {
		t.Fatal("shared result not cached")
	}
}

func TestRefreshCaps(t *testing.T) {
	now := time.Time{}
	r := NewRunner(func() time.Time { return now })
	node, count := responder(t, "198.51.100.1:1234", true)
	dm := &tailcfg.DERPMap{Regions: map[int]*tailcfg.DERPRegion{1: {RegionID: 1, Nodes: []*tailcfg.DERPNode{node}}}}
	for _, step := range []struct {
		advance time.Duration
		join    bool
		packets int32
	}{
		{0, false, 1}, {time.Minute, false, 1}, {0, true, 2}, {9*time.Minute + 59*time.Second, false, 2}, {time.Second, false, 3},
	} {
		now = now.Add(step.advance)
		got, age, available := r.Refresh(context.Background(), dm, step.join)
		if !available || !got.UDP || count.Load() != step.packets || (step.packets == 1 && step.advance > 0 && age != time.Minute) {
			t.Fatalf("refresh: result=%+v age=%v available=%v packets=%d step=%+v", got, age, available, count.Load(), step)
		}
		delete(got.DERPMillis, 1)
	}
	got, _, _ := r.Refresh(context.Background(), dm, false)
	if len(got.DERPMillis) != 1 {
		t.Fatal("caller mutated cache")
	}
	if !r.AllowChange() || r.AllowChange() {
		t.Fatal("change limit missing")
	}
	now = now.Add(30*time.Second - time.Nanosecond)
	if r.AllowChange() {
		t.Fatal("change allowed too soon")
	}
	now = now.Add(time.Nanosecond)
	if !r.AllowChange() {
		t.Fatal("change blocked at boundary")
	}
}

func TestPingInFlight(t *testing.T) {
	r := NewRunner(nil)
	entered, release, done := make(chan struct{}), make(chan struct{}), make(chan error, 1)
	go func() {
		_, err := r.Ping(context.Background(), func(ctx context.Context) (*ipnstate.PingResult, error) {
			deadline, ok := ctx.Deadline()
			if !ok || time.Until(deadline) > 3*time.Second {
				t.Error("missing ping deadline")
			}
			close(entered)
			<-release
			return &ipnstate.PingResult{}, nil
		})
		done <- err
	}()
	<-entered
	_, err := r.Ping(context.Background(), func(context.Context) (*ipnstate.PingResult, error) { t.Error("second ping invoked"); return nil, nil })
	close(release)
	if err == nil || <-done != nil {
		t.Fatal("in-flight ping guard failed")
	}
	if _, err := r.Ping(context.Background(), func(context.Context) (*ipnstate.PingResult, error) { return nil, nil }); err != nil {
		t.Fatal(err)
	}
}
