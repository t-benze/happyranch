package main

import (
	"bytes"
	"context"
	"errors"
	"os"
	"sync"
	"testing"
	"time"
)

func TestStructuredChildHealthAcceptsExactRecords(t *testing.T) {
	records := make(chan childHealth, 2)
	failed := make(chan error, 1)
	scanChildHealth(bytes.NewBufferString(healthRecord("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 1, "ready")), records, failed)
	select {
	case err := <-failed:
		t.Fatal(err)
	default:
	}
	record := <-records
	if record.Version != 1 || record.Sequence != 1 || record.State != "ready" {
		t.Fatalf("unexpected record: %#v", record)
	}
}

func TestStructuredChildHealthRejectsMalformedPartialAndUnknownShape(t *testing.T) {
	for _, raw := range []string{
		"not-json\n",
		`{"version":1,"generation":"a","sequence":1}` + "\n",
		`{"version":1,"generation":"a","sequence":1,"state":"ready","extra":true}` + "\n",
	} {
		records := make(chan childHealth, 2)
		failed := make(chan error, 1)
		scanChildHealth(bytes.NewBufferString(raw), records, failed)
		select {
		case <-failed:
		default:
			t.Fatalf("accepted malformed record %q", raw)
		}
	}
}

type countingStopper struct{ calls int }

func (s *countingStopper) Stop() error {
	s.calls++
	return nil
}

func TestStopTwiceUsesSameProductionInstanceAndReceiptsEachInvocation(t *testing.T) {
	stopper := &countingStopper{}
	file, err := os.CreateTemp(t.TempDir(), "receipt")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if err := stopTwice(stopper, file, 4242); err != nil {
		t.Fatal(err)
	}
	if stopper.calls != 2 {
		t.Fatalf("Stop calls = %d, want 2", stopper.calls)
	}
	if err := file.Sync(); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(file.Name())
	if err != nil {
		t.Fatal(err)
	}
	want := "lifecycle_stop_complete run=4242 invocation=1\nlifecycle_stop_complete run=4242 invocation=2\n"
	if string(raw) != want {
		t.Fatalf("receipt = %q, want %q", raw, want)
	}
}

type recordingNotifier struct {
	mu    sync.Mutex
	calls []string
	err   error
}

func (n *recordingNotifier) Notify(states ...string) error {
	n.mu.Lock()
	defer n.mu.Unlock()
	n.calls = append(n.calls, states...)
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
