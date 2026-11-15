package tsnethealth

import (
	"context"
	"testing"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tailcfg"
)

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
