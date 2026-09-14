package sidecar

import (
	"context"
	"errors"
	"net"
	"os"
	"sync"
	"testing"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/types/key"
)

type fakeTSNetServer struct {
	startErr, upErr, statusErr           error
	upStatus                             *ipnstate.Status
	statuses                             []*ipnstate.Status
	startEntered                         chan struct{}
	startRelease                         <-chan struct{}
	upEntered                            chan struct{}
	upRelease                            <-chan struct{}
	statusEntered                        chan struct{}
	statusRelease                        <-chan struct{}
	ignoreContext                        bool
	mu                                   sync.Mutex
	startCalls, upCalls                  int
	statusCalls, closeCalls, listenCalls int
	clearAuthKeyCalls                    int
	upDeadline, statusDeadline           time.Time
}

func (f *fakeTSNetServer) Start() error {
	f.mu.Lock()
	f.startCalls++
	entered, release, err := f.startEntered, f.startRelease, f.startErr
	f.mu.Unlock()
	if entered != nil {
		close(entered)
	}
	if release != nil {
		<-release
	}
	return err
}
func (f *fakeTSNetServer) ClearAuthKey() { f.mu.Lock(); defer f.mu.Unlock(); f.clearAuthKeyCalls++ }
func (f *fakeTSNetServer) Up(ctx context.Context) (*ipnstate.Status, error) {
	f.mu.Lock()
	f.upCalls++
	f.upDeadline, _ = ctx.Deadline()
	entered, release, status, err, ignoreContext := f.upEntered, f.upRelease, f.upStatus, f.upErr, f.ignoreContext
	f.mu.Unlock()
	if entered != nil {
		close(entered)
	}
	if release != nil {
		if ignoreContext {
			<-release
		} else {
			select {
			case <-release:
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		}
	}
	return status, err
}
func (f *fakeTSNetServer) Status(ctx context.Context) (*ipnstate.Status, error) {
	f.mu.Lock()
	f.statusCalls++
	f.statusDeadline, _ = ctx.Deadline()
	entered, release, err, ignoreContext := f.statusEntered, f.statusRelease, f.statusErr, f.ignoreContext
	f.mu.Unlock()
	if entered != nil {
		close(entered)
	}
	if release != nil {
		if ignoreContext {
			<-release
		} else {
			select {
			case <-release:
			case <-ctx.Done():
				return nil, ctx.Err()
			}
		}
	}
	if err != nil {
		return nil, err
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.statuses) == 0 {
		return &ipnstate.Status{BackendState: "Running"}, nil
	}
	status := f.statuses[0]
	if len(f.statuses) > 1 {
		f.statuses = f.statuses[1:]
	}
	return status, nil
}
func (f *fakeTSNetServer) Listen(string) (net.Listener, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.listenCalls++
	return nil, errors.New("not expected")
}
func (f *fakeTSNetServer) Close() error { f.mu.Lock(); defer f.mu.Unlock(); f.closeCalls++; return nil }

func runningWithPeer(host, dns string) *ipnstate.Status {
	return &ipnstate.Status{BackendState: "Running", Peer: map[key.NodePublic]*ipnstate.PeerStatus{{}: {HostName: host, DNSName: dns}}}
}
func testTSNetEngine(server *fakeTSNetServer) *TSNetEngine {
	return &TSNetEngine{newServer: func(EngineConfig, []byte) tsnetServer { return server }, peerPollInterval: time.Millisecond}
}
func engineConfig() EngineConfig {
	return EngineConfig{StateDir: "/state", ControlURL: "https://control.invalid", RoleIdentity: "home-sidecar-test", ExpectedPeers: []string{"expected"}}
}

func TestTSNetEngineUsesOneBoundedContextAndWaitsForExpectedPeer(t *testing.T) {
	server := &fakeTSNetServer{upStatus: runningWithPeer("", "other.example."), statuses: []*ipnstate.Status{runningWithPeer("", "expected.")}}
	engine := testTSNetEngine(server)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	receipt, err := engine.Start(ctx, engineConfig(), []byte("one-use"))
	if err != nil || !receipt.Redeemed || !receipt.Durable || !receipt.ExpectedPeerVisible {
		t.Fatalf("receipt=%+v err=%v", receipt, err)
	}
	if server.startCalls != 1 || server.upCalls != 1 || server.statusCalls == 0 {
		t.Fatalf("calls start=%d up=%d status=%d", server.startCalls, server.upCalls, server.statusCalls)
	}
	if server.upDeadline != server.statusDeadline {
		t.Fatalf("waits used different deadlines: up=%s status=%s", server.upDeadline, server.statusDeadline)
	}
	if server.clearAuthKeyCalls != 1 {
		t.Fatalf("auth key clears=%d", server.clearAuthKeyCalls)
	}
}

func TestTSNetEngineImmediateExpectedHostNameNeedsNoPeerPoll(t *testing.T) {
	server := &fakeTSNetServer{upStatus: runningWithPeer("expected", "")}
	receipt, err := testTSNetEngine(server).Start(context.Background(), engineConfig(), []byte("one-use"))
	if err != nil || !receipt.ExpectedPeerVisible || server.statusCalls != 0 {
		t.Fatalf("receipt=%+v err=%v polls=%d", receipt, err, server.statusCalls)
	}
}

func TestTSNetEngineDoesNotRefreshCallerDeadlineWhilePeerIsAbsent(t *testing.T) {
	server := &fakeTSNetServer{upStatus: runningWithPeer("other", "")}
	engine := testTSNetEngine(server)
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Millisecond)
	defer cancel()
	_, err := engine.Start(ctx, engineConfig(), []byte("one-use"))
	if !errors.Is(err, ErrNetworkJoin) {
		t.Fatalf("err=%v", err)
	}
	if server.statusCalls < 1 {
		t.Fatal("expected peer polling")
	}
}

func TestTSNetEngineRejectsCancelledReadinessBeforePositiveReceipt(t *testing.T) {
	server := &fakeTSNetServer{upStatus: runningWithPeer("expected", "")}
	engine := testTSNetEngine(server)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	_, err := engine.Start(ctx, engineConfig(), []byte("one-use"))
	if !errors.Is(err, ErrNetworkJoin) {
		t.Fatalf("err=%v", err)
	}
	if server.startCalls != 0 || server.upCalls != 0 {
		t.Fatalf("unexpected upstream work: start=%d up=%d", server.startCalls, server.upCalls)
	}
}

func TestTSNetEngineDoesNotRenewBudgetAfterContextlessStart(t *testing.T) {
	release := make(chan struct{})
	server := &fakeTSNetServer{startEntered: make(chan struct{}), startRelease: release, upStatus: runningWithPeer("expected", "")}
	engine := testTSNetEngine(server)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	result := make(chan error, 1)
	go func() { _, err := engine.Start(ctx, engineConfig(), []byte("one-use")); result <- err }()
	<-server.startEntered
	<-ctx.Done()
	close(release)
	if err := <-result; !errors.Is(err, ErrNetworkJoin) {
		t.Fatalf("err=%v", err)
	}
	if server.upCalls != 0 {
		t.Fatalf("late start minted readiness work: up=%d", server.upCalls)
	}
}

func TestTSNetEngineMapsInitializationAndReadinessFailures(t *testing.T) {
	for name, server := range map[string]*fakeTSNetServer{
		"start": {startErr: errors.New("init")}, "up": {upErr: errors.New("join")}, "status": {upStatus: runningWithPeer("other", ""), statusErr: errors.New("status")},
	} {
		t.Run(name, func(t *testing.T) {
			_, err := testTSNetEngine(server).Start(context.Background(), engineConfig(), []byte("one-use"))
			want := ErrNetworkJoin
			if name == "start" {
				want = ErrEngineStart
			}
			if !errors.Is(err, want) {
				t.Fatalf("err=%v want=%v", err, want)
			}
		})
	}
}

func TestProductionAdapterViaSidecarDoesNotAdmitAfterCancelledUp(t *testing.T) {
	server := &fakeTSNetServer{upEntered: make(chan struct{}), upRelease: make(chan struct{}), upStatus: runningWithPeer("expected", "")}
	engine := testTSNetEngine(server)
	cfg := validConfig(t)
	ctx, cancel := context.WithCancel(context.Background())
	result := make(chan error, 1)
	go func() { result <- New(cfg, engine, &net.Dialer{}).Start(ctx) }()
	<-server.upEntered
	cancel()
	if err := <-result; !errors.Is(err, ErrNetworkJoin) {
		t.Fatalf("err=%v", err)
	}
	if server.listenCalls != 0 {
		t.Fatalf("listener was admitted: %d calls", server.listenCalls)
	}
	if _, err := os.Stat(cfg.CredentialFile); err != nil {
		t.Fatalf("credential should remain before positive receipt: %v", err)
	}
	if server.closeCalls != 1 {
		t.Fatalf("close calls=%d", server.closeCalls)
	}
}

func TestProductionAdapterViaSidecarRejectsApparentSuccessAfterCancellation(t *testing.T) {
	release := make(chan struct{})
	server := &fakeTSNetServer{upEntered: make(chan struct{}), upRelease: release, upStatus: runningWithPeer("expected", ""), ignoreContext: true}
	engine := testTSNetEngine(server)
	cfg := validConfig(t)
	ctx, cancel := context.WithCancel(context.Background())
	result := make(chan error, 1)
	go func() { result <- New(cfg, engine, &net.Dialer{}).Start(ctx) }()
	<-server.upEntered
	cancel()
	close(release)
	if err := <-result; !errors.Is(err, ErrNetworkJoin) {
		t.Fatalf("err=%v", err)
	}
	if server.listenCalls != 0 || server.closeCalls != 1 {
		t.Fatalf("listen=%d close=%d", server.listenCalls, server.closeCalls)
	}
	if _, err := os.Stat(cfg.CredentialFile); err != nil {
		t.Fatalf("credential should remain before positive receipt: %v", err)
	}
}
