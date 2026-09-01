package main

import (
	"context"
	"errors"
	"sync"
	"testing"
	"time"
)

type recordingNotifier struct {
	mu    sync.Mutex
	calls []string
	err   error
}

func (n *recordingNotifier) Notify(state string, statuses ...string) error {
	n.mu.Lock()
	defer n.mu.Unlock()
	n.calls = append(n.calls, state)
	n.calls = append(n.calls, statuses...)
	return n.err
}

func TestWatchdogLoopReportsHealthyProcessAndStops(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	n := &recordingNotifier{}
	done := make(chan struct{})
	failed := make(chan error, 1)
	go func() {
		watchdogLoop(ctx, cancel, n, time.Millisecond, failed)
		close(done)
	}()
	time.Sleep(5 * time.Millisecond)
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("watchdog did not stop")
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	if len(n.calls) == 0 {
		t.Fatal("healthy process emitted no watchdog notification")
	}
	for _, call := range n.calls {
		if call != "WATCHDOG=1" {
			t.Fatalf("unexpected notification %q", call)
		}
	}
}

func TestWatchdogFailureCancelsService(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	n := &recordingNotifier{err: errors.New("notify failed")}
	failed := make(chan error, 1)
	watchdogLoop(ctx, cancel, n, time.Millisecond, failed)
	select {
	case <-ctx.Done():
	default:
		t.Fatal("notify failure did not cancel service")
	}
	if <-failed == nil {
		t.Fatal("notify failure was not reported")
	}
}
