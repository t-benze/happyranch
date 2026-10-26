package sidecar

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/types/key"
)

type fakeTSNetServer struct {
	startErr, upErr, statusErr               error
	upStatus                                 *ipnstate.Status
	statuses                                 []*ipnstate.Status
	startEntered                             chan struct{}
	startRelease                             <-chan struct{}
	upEntered                                chan struct{}
	upRelease                                <-chan struct{}
	statusEntered                            chan struct{}
	statusRelease                            <-chan struct{}
	ignoreContext                            bool
	mu                                       sync.Mutex
	startCalls, upCalls                      int
	statusCalls, closeCalls, listenCalls     int
	clearAuthKeyCalls                        int
	upDeadline, statusDeadline               time.Time
	startAt, upAt, statusAt                  time.Time
	upContext, statusContext                 context.Context
	statusTerminalDelivered                  bool
	upEnteredConsumed, statusEnteredConsumed bool
	listener                                 net.Listener
}

func (f *fakeTSNetServer) Start() error {
	f.mu.Lock()
	f.startCalls++
	f.startAt = time.Now()
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
	f.upAt = time.Now()
	f.upContext = ctx
	f.upDeadline, _ = ctx.Deadline()
	entered, release, status, err, ignoreContext := f.upEntered, f.upRelease, f.upStatus, f.upErr, f.ignoreContext
	if f.upEnteredConsumed {
		entered = nil
	} else if entered != nil {
		f.upEnteredConsumed = true
	}
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
	if f.statusAt.IsZero() {
		f.statusAt = time.Now()
	}
	f.statusContext = ctx
	f.statusTerminalDelivered = f.statusTerminalDelivered || ctx.Err() != nil
	f.statusDeadline, _ = ctx.Deadline()
	entered, release, err, ignoreContext := f.statusEntered, f.statusRelease, f.statusErr, f.ignoreContext
	if f.statusEnteredConsumed {
		entered = nil
	} else if entered != nil {
		f.statusEnteredConsumed = true
	}
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
	if f.listener != nil {
		return f.listener, nil
	}
	return nil, errors.New("not expected")
}
func (f *fakeTSNetServer) Close() error { f.mu.Lock(); defer f.mu.Unlock(); f.closeCalls++; return nil }

func runningWithPeer(host, dns string) *ipnstate.Status {
	return &ipnstate.Status{BackendState: "Running", Peer: map[key.NodePublic]*ipnstate.PeerStatus{{}: {HostName: host, DNSName: dns}}}
}
func testTSNetEngine(server *fakeTSNetServer) *TSNetEngine {
	return &TSNetEngine{newServer: func(EngineConfig, []byte) tsnetServer { return server }}
}
func engineConfig() EngineConfig {
	return EngineConfig{StateDir: "/state", ControlURL: "https://control.invalid", RoleIdentity: "home-sidecar-test", ExpectedPeers: []string{"expected"}}
}

const testWaitTimeout = time.Second

func waitSignal(t *testing.T, signal <-chan struct{}, name string) {
	t.Helper()
	select {
	case <-signal:
	case <-time.After(testWaitTimeout):
		t.Fatalf("timed out waiting for %s", name)
	}
}

func waitResult(t *testing.T, result <-chan error) error {
	t.Helper()
	select {
	case err := <-result:
		return err
	case <-time.After(testWaitTimeout):
		t.Fatal("timed out waiting for Start")
		return nil
	}
}

func releaseSignal(t *testing.T, signal chan struct{}) func() {
	t.Helper()
	var once sync.Once
	release := func() { once.Do(func() { close(signal) }) }
	t.Cleanup(release)
	return release
}

func TestTSNetEngineCarriesConsumedUpDeadlineIntoPeerPoll(t *testing.T) {
	upRelease, statusRelease := make(chan struct{}), make(chan struct{})
	releaseUp, releaseStatus := releaseSignal(t, upRelease), releaseSignal(t, statusRelease)
	server := &fakeTSNetServer{upEntered: make(chan struct{}), upRelease: upRelease, upStatus: runningWithPeer("", "other.example."), statusEntered: make(chan struct{}), statusRelease: statusRelease, statuses: []*ipnstate.Status{runningWithPeer("", "expected.")}}
	engine := testTSNetEngine(server)
	ctx, cancel := context.WithTimeout(context.Background(), 750*time.Millisecond)
	t.Cleanup(cancel)
	callerDeadline, _ := ctx.Deadline()
	type outcome struct {
		receipt RedemptionReceipt
		err     error
	}
	result := make(chan outcome, 1)
	settled := false
	receive := func() outcome {
		select {
		case got := <-result:
			settled = true
			return got
		case <-time.After(testWaitTimeout):
			t.Fatal("Start did not settle")
			return outcome{}
		}
	}
	t.Cleanup(func() {
		releaseUp()
		releaseStatus()
		cancel()
		if !settled {
			receive()
		}
		_ = engine.Close()
	})
	go func() {
		receipt, err := engine.Start(ctx, engineConfig(), []byte("one-use"))
		result <- outcome{receipt, err}
	}()
	waitSignal(t, server.upEntered, "Up")
	if !server.upAt.Before(callerDeadline) || !server.upDeadline.Equal(callerDeadline) {
		t.Fatal("Up missed original caller deadline")
	}
	time.Sleep(10 * time.Millisecond)
	releaseUp()
	waitSignal(t, server.statusEntered, "Status")
	if !server.statusAt.Before(callerDeadline) || !server.statusDeadline.Equal(callerDeadline) || server.upContext != server.statusContext {
		t.Fatal("Status missed or renewed original Up context")
	}
	releaseStatus()
	got := receive()
	if got.err != nil || !got.receipt.Redeemed || !got.receipt.Durable || !got.receipt.ExpectedPeerVisible {
		t.Fatalf("receipt=%+v err=%v", got.receipt, got.err)
	}
	if server.startCalls != 1 || server.upCalls != 1 || server.statusCalls != 1 || server.clearAuthKeyCalls != 1 {
		t.Fatal("startup/poll/key-clear calls differ")
	}
}

func TestTSNetEngineCapsBackgroundStartupAtEightySeconds(t *testing.T) {
	const requiredStartupBudget = 80 * time.Second
	statusRelease := make(chan struct{})
	server := &fakeTSNetServer{
		upStatus:      runningWithPeer("other", ""),
		statusEntered: make(chan struct{}),
		statusRelease: statusRelease,
	}
	engine := testTSNetEngine(server)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	releaseStatus := releaseSignal(t, statusRelease)
	started := time.Now()
	result := make(chan error, 1)
	settled := false
	receive := func() error { err := waitResult(t, result); settled = true; return err }
	t.Cleanup(func() {
		releaseStatus()
		cancel()
		if !settled {
			receive()
		}
		_ = engine.Close()
	})
	go func() { _, err := engine.Start(ctx, engineConfig(), []byte("one-use")); result <- err }()
	waitSignal(t, server.statusEntered, "Status")
	if server.upDeadline.IsZero() || server.statusDeadline.IsZero() {
		t.Fatalf("missing deadlines: up=%s status=%s", server.upDeadline, server.statusDeadline)
	}
	if !server.upDeadline.Equal(server.statusDeadline) || server.upContext != server.statusContext {
		t.Fatalf("waits used different deadlines: up=%s status=%s", server.upDeadline, server.statusDeadline)
	}
	if remaining := time.Until(server.upDeadline); remaining > requiredStartupBudget || remaining < requiredStartupBudget-2*time.Second {
		t.Fatalf("background startup budget remaining=%s, want near %s", remaining, requiredStartupBudget)
	}
	if server.upDeadline.After(started.Add(requiredStartupBudget + 100*time.Millisecond)) {
		t.Fatalf("startup deadline=%s exceeds 80-second cap from %s", server.upDeadline, started)
	}
	cancel()
	releaseStatus()
	if err := receive(); !errors.Is(err, ErrNetworkJoin) {
		t.Fatalf("err=%v", err)
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
	ctx, cancel := context.WithTimeout(context.Background(), 750*time.Millisecond)
	t.Cleanup(cancel)
	defer cancel()
	deadline, _ := ctx.Deadline()
	engine := testTSNetEngine(server)
	defer engine.Close()
	receipt, err := engine.Start(ctx, engineConfig(), []byte("one-use"))
	if !errors.Is(err, ErrNetworkJoin) || receipt != (RedemptionReceipt{}) {
		t.Fatalf("receipt=%+v err=%v", receipt, err)
	}
	if server.statusCalls < 1 || !server.statusAt.Before(deadline) || !server.upAt.Before(deadline) {
		t.Fatal("original preexpiry polling setup not reached")
	}
	if !server.upDeadline.Equal(deadline) || !server.statusDeadline.Equal(deadline) || server.upContext != server.statusContext {
		t.Fatal("absent-peer polling renewed original parent/context")
	}
}

func TestTSNetEngineRejectsCancelledReadinessBeforePositiveReceipt(t *testing.T) {
	for _, terminal := range []string{"context_cancelled", "deadline_exceeded", "pending_deadline", "pending_deadline_cancelled"} {
		t.Run(terminal, func(t *testing.T) {
			server := &fakeTSNetServer{upStatus: runningWithPeer("expected", "")}
			ctx, cancel := context.WithCancel(context.Background())
			if terminal == "deadline_exceeded" {
				cancel()
				ctx, cancel = context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
			}
			cancel()
			want := terminal
			if strings.HasPrefix(terminal, "pending_deadline") {
				parent := context.Background()
				if terminal == "pending_deadline_cancelled" {
					parent = ctx // delivered cancellation must outrank elapsed clock
					want = "context_cancelled"
				} else {
					want = "deadline_exceeded"
				}
				ctx = pendingDeadlineContext{Context: parent, deadline: time.Now().Add(-time.Second), done: make(chan struct{})}
				if terminal == "pending_deadline" && ctx.Err() != nil {
					t.Fatal("entry error already delivered")
				}
				select {
				case <-ctx.Done():
					t.Fatal("entry Done already delivered")
				default:
				}
			}
			factories := 0
			engine := testTSNetEngine(server)
			factory := engine.newServer
			engine.newServer = func(c EngineConfig, credential []byte) tsnetServer { factories++; return factory(c, credential) }
			receipt, err := engine.Start(ctx, engineConfig(), []byte("one-use"))
			assertNetworkReason(t, err, want)
			if receipt != (RedemptionReceipt{}) || factories != 0 || server.startCalls != 0 || server.upCalls != 0 || server.statusCalls != 0 {
				t.Fatal("terminal caller admitted upstream work")
			}
		})
	}
}

func TestTSNetEngineDoesNotRenewBudgetAfterContextlessStart(t *testing.T) {
	// Keep the original late Start owner and exercise its dependent boundaries
	// directly: Sidecar's separate success fence cannot mask an adapter receipt.
	for _, stage := range []string{"factory", "start", "up", "peer"} {
		shapes := []string{"success", "error", "nil", "nonrunning", "missing_peer"}
		if stage == "factory" {
			shapes = []string{"success"}
		}
		if stage == "start" {
			shapes = []string{"success", "error"}
		}
		for _, shape := range shapes {
			t.Run(stage+"/"+shape, func(t *testing.T) {
				release := make(chan struct{})
				releaseCall := releaseSignal(t, release)
				entered := make(chan struct{})
				server := &fakeTSNetServer{ignoreContext: true, upStatus: runningWithPeer("expected", "")}
				status := runningWithPeer("expected", "")
				if shape == "nil" {
					status = nil
				}
				if shape == "nonrunning" {
					status = &ipnstate.Status{BackendState: "Starting"}
				}
				if shape == "missing_peer" {
					status = runningWithPeer("other", "")
				}
				if stage == "start" {
					server.startEntered, server.startRelease = entered, release
					if shape == "error" {
						server.startErr = errors.New("START_SECRET_CANARY")
					}
				}
				if stage == "up" {
					server.upEntered, server.upRelease, server.upStatus = entered, release, status
					if shape == "error" {
						server.upErr = errors.New("tsnet.Up: backend: UP_SECRET_CANARY")
					}
				}
				if stage == "peer" {
					server.upStatus = runningWithPeer("other", "")
					server.statusEntered, server.statusRelease = entered, release
					server.statuses = []*ipnstate.Status{status}
					if shape == "error" {
						server.statusErr = errors.New("STATUS_SECRET_CANARY")
					}
				}
				engine := testTSNetEngine(server)
				factory := engine.newServer
				factories := 0
				var factoryAt time.Time
				engine.newServer = func(c EngineConfig, credential []byte) tsnetServer {
					factories++
					factoryAt = time.Now()
					if stage == "factory" {
						close(entered)
						<-release
					}
					return factory(c, credential)
				}
				deadline := time.Now().Add(750 * time.Millisecond)
				ctx := pendingDeadlineContext{Context: context.Background(), deadline: deadline, done: make(chan struct{})}
				type outcome struct {
					receipt RedemptionReceipt
					err     error
				}
				result := make(chan outcome, 1)
				settled := false
				receive := func() outcome {
					select {
					case got := <-result:
						settled = true
						return got
					case <-time.After(testWaitTimeout):
						t.Fatal("adapter Start did not settle")
						return outcome{}
					}
				}
				t.Cleanup(func() {
					releaseCall()
					if !settled {
						receive()
					}
					_ = engine.Close()
				})
				go func() {
					receipt, err := engine.Start(ctx, engineConfig(), []byte("one-use"))
					result <- outcome{receipt, err}
				}()
				waitSignal(t, entered, stage)
				entry := factoryAt
				if stage == "start" {
					entry = server.startAt
				}
				if stage == "up" {
					entry = server.upAt
				}
				if stage == "peer" {
					entry = server.statusAt
				}
				if !entry.Before(deadline) || !time.Now().Before(deadline) {
					t.Fatal("stage missed genuinely future original deadline")
				}
				if stage == "up" || stage == "peer" {
					if !server.upDeadline.Equal(deadline) {
						t.Fatal("Up renewed original deadline")
					}
					if stage == "peer" && (server.upContext != server.statusContext || !server.statusDeadline.Equal(deadline)) {
						t.Fatal("Status renewed original context")
					}
				}
				<-time.After(time.Until(deadline))
				if time.Now().Before(deadline) || ctx.Err() != nil {
					t.Fatal("original deadline not elapsed with pending parent error")
				}
				select {
				case <-ctx.Done():
					t.Fatal("parent delivery not pending")
				default:
				}
				if stage == "up" || stage == "peer" {
					if server.upContext.Err() != nil {
						t.Fatal("effective error already delivered")
					}
					select {
					case <-server.upContext.Done():
						t.Fatal("effective delivery not pending")
					default:
					}
				}
				releaseCall()
				got := receive()
				if stage == "start" && shape == "error" {
					if got.err != ErrEngineStart {
						t.Fatalf("late Start error precedence changed: %v", got.err)
					}
				} else {
					assertNetworkReason(t, got.err, "deadline_exceeded")
				}
				wantStart, wantUp, wantStatus := 1, 0, 0
				if stage == "factory" {
					wantStart = 0
				}
				if stage == "up" || stage == "peer" {
					wantUp = 1
				}
				if stage == "peer" {
					wantStatus = 1
				}
				if got.receipt != (RedemptionReceipt{}) || factories != 1 || server.startCalls != wantStart || server.upCalls != wantUp || server.statusCalls != wantStatus || server.clearAuthKeyCalls != 1 {
					t.Fatal("late boundary minted work/receipt or omitted key clear")
				}
			})
		}
	}
}

func assertNetworkReason(t *testing.T, err error, want string) {
	t.Helper()
	var metadata interface{ NetworkJoinSubReason() string }
	got := ""
	if errors.As(err, &metadata) {
		got = metadata.NetworkJoinSubReason()
	}
	if err == nil || !errors.Is(err, ErrNetworkJoin) || err.Error() != ErrNetworkJoin.Error() || got != want {
		t.Fatalf("safe network reason=%q want=%q sentinel=%t safe_text=%t", got, want, errors.Is(err, ErrNetworkJoin), err != nil && err.Error() == ErrNetworkJoin.Error())
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		t.Fatal("raw cause retained")
	}
}

func TestTSNetEngineMapsInitializationAndReadinessFailures(t *testing.T) {
	hostile := "KEY_CANARY TOKEN_CANARY CREDENTIAL_CANARY https://user:password@host.invalid/path?key=secret 100.64.0.1 /private/config BACKEND_CANARY\x1b[31m"
	for _, tc := range []struct {
		name   string
		server *fakeTSNetServer
		reason string
	}{
		{"start", &fakeTSNetServer{startErr: errors.New(hostile)}, ""},
		{"up_backend", &fakeTSNetServer{upErr: errors.New("tsnet.Up: backend: " + hostile)}, "up_backend_error"},
		{"up_no_ip", &fakeTSNetServer{upErr: errors.New("tsnet.Up: running, but no ip")}, "up_no_ip"},
		{"up_no_ip_suffix", &fakeTSNetServer{upErr: errors.New("tsnet.Up: running, but no ip " + hostile)}, "up_error_unclassified"},
		{"up_backend_lookalike", &fakeTSNetServer{upErr: errors.New("prefix tsnet.Up: backend: " + hostile)}, "up_error_unclassified"},
		{"up_generic", &fakeTSNetServer{upErr: fmt.Errorf("tsnet.Up: %w", errors.New(hostile))}, "up_error_unclassified"},
		{"up_cancelled", &fakeTSNetServer{upErr: fmt.Errorf("%s: %w", hostile, context.Canceled)}, "context_cancelled"},
		{"up_deadline", &fakeTSNetServer{upErr: fmt.Errorf("%s: %w", hostile, context.DeadlineExceeded)}, "deadline_exceeded"},
		{"up_nil", &fakeTSNetServer{}, "up_status_unavailable"},
		{"up_not_running", &fakeTSNetServer{upStatus: &ipnstate.Status{BackendState: hostile}}, "up_not_running"},
		{"peer_error", &fakeTSNetServer{upStatus: runningWithPeer("other", ""), statusErr: errors.New(hostile)}, "peer_status_error"},
		{"peer_cancelled", &fakeTSNetServer{upStatus: runningWithPeer("other", ""), statusErr: context.Canceled}, "context_cancelled"},
		{"peer_deadline", &fakeTSNetServer{upStatus: runningWithPeer("other", ""), statusErr: context.DeadlineExceeded}, "deadline_exceeded"},
		{"peer_nil", &fakeTSNetServer{upStatus: runningWithPeer("other", ""), statuses: []*ipnstate.Status{nil}}, "peer_status_unavailable"},
		{"peer_not_running", &fakeTSNetServer{upStatus: runningWithPeer("other", ""), statuses: []*ipnstate.Status{{BackendState: hostile}}}, "peer_not_running"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := validConfig(t)
			dials := 0
			svc := New(cfg, testTSNetEngine(tc.server), dialFunc(func(context.Context, string, string) (net.Conn, error) { dials++; return nil, errors.New("unexpected") }))
			t.Cleanup(func() { _ = svc.Stop() })
			err := svc.Start(context.Background())
			wantUp := 1
			if tc.name == "start" {
				wantUp = 0
			}
			if tc.server.startCalls != 1 || tc.server.clearAuthKeyCalls != 1 || tc.server.upCalls != wantUp {
				t.Fatal("producer stage not reached")
			}
			if strings.HasPrefix(tc.name, "peer_") {
				if tc.server.statusCalls != 1 || !tc.server.statusAt.Before(tc.server.statusDeadline) || !tc.server.upAt.Before(tc.server.upDeadline) || !tc.server.upDeadline.Equal(tc.server.statusDeadline) || tc.server.upContext != tc.server.statusContext {
					t.Fatal("producer Status did not reach original preexpiry Up context")
				}
			} else if tc.server.statusCalls != 0 {
				t.Fatal("unexpected producer poll")
			}
			if tc.reason == "" {
				if !errors.Is(err, ErrEngineStart) || err.Error() != ErrEngineStart.Error() {
					t.Fatal("initialization category changed")
				}
			} else {
				assertNetworkReason(t, err, tc.reason)
			}
			_ = svc.Stop()
			if dials != 0 || tc.server.listenCalls != 0 || tc.server.closeCalls != 1 {
				t.Fatalf("admission/close dials=%d listens=%d closes=%d", dials, tc.server.listenCalls, tc.server.closeCalls)
			}
			if _, e := os.Stat(cfg.CredentialFile); e != nil {
				t.Fatal("credential consumed without receipt")
			}
			if _, e := os.Stat(filepath.Join(cfg.StateDir, consumedMarker)); !os.IsNotExist(e) {
				t.Fatal("failed receipt committed")
			}
			for _, sentinel := range []string{"KEY_CANARY", "TOKEN_CANARY", "CREDENTIAL_CANARY", "https://", "100.64.0.1", "/private/config", "BACKEND_CANARY"} {
				if strings.Contains(err.Error(), sentinel) {
					t.Fatal("raw cause leaked")
				}
			}
		})
	}
}

func TestProductionAdapterViaSidecarDoesNotAdmitAfterCancelledUp(t *testing.T) {
	release := make(chan struct{})
	releaseUp := releaseSignal(t, release)
	server := &fakeTSNetServer{upEntered: make(chan struct{}), upRelease: release, upStatus: runningWithPeer("mac-client-123", "")}
	engine := testTSNetEngine(server)
	cfg := validConfig(t)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)

	result := make(chan error, 1)
	svc := New(cfg, engine, &net.Dialer{})
	settled := false
	receive := func() error { err := waitResult(t, result); settled = true; return err }
	t.Cleanup(func() {
		releaseUp()
		cancel()
		if !settled {
			receive()
		}
		_ = svc.Stop()
	})
	go func() { result <- svc.Start(ctx) }()
	waitSignal(t, server.upEntered, "Up")
	cancel()
	releaseUp()
	assertNetworkReason(t, receive(), "context_cancelled")
	if server.listenCalls != 0 {
		t.Fatalf("listener was admitted: %d calls", server.listenCalls)
	}
	if _, err := os.Stat(cfg.CredentialFile); err != nil {
		t.Fatalf("credential should remain before positive receipt: %v", err)
	}
	if _, err := os.Stat(filepath.Join(cfg.StateDir, consumedMarker)); !os.IsNotExist(err) {
		t.Fatal("marker committed before receipt")
	}
	if server.closeCalls != 1 {
		t.Fatalf("close calls=%d", server.closeCalls)
	}
}

func TestProductionAdapterViaSidecarRejectsApparentSuccessAfterCancellation(t *testing.T) {
	release := make(chan struct{})
	releaseUp := releaseSignal(t, release)
	server := &fakeTSNetServer{upEntered: make(chan struct{}), upRelease: release, upStatus: runningWithPeer("mac-client-123", ""), ignoreContext: true}
	engine := testTSNetEngine(server)
	cfg := validConfig(t)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)

	result := make(chan error, 1)
	svc := New(cfg, engine, &net.Dialer{})
	settled := false
	receive := func() error { err := waitResult(t, result); settled = true; return err }
	t.Cleanup(func() {
		releaseUp()
		cancel()
		if !settled {
			receive()
		}
		_ = svc.Stop()
	})
	go func() { result <- svc.Start(ctx) }()
	waitSignal(t, server.upEntered, "Up")
	cancel()
	releaseUp()
	assertNetworkReason(t, receive(), "context_cancelled")
	if server.listenCalls != 0 || server.closeCalls != 1 {
		t.Fatalf("listen=%d close=%d", server.listenCalls, server.closeCalls)
	}
	if _, err := os.Stat(filepath.Join(cfg.StateDir, consumedMarker)); !os.IsNotExist(err) {
		t.Fatal("marker committed before receipt")
	}
	if _, err := os.Stat(cfg.CredentialFile); err != nil {
		t.Fatalf("credential should remain before positive receipt: %v", err)
	}
}

func TestProductionAdapterViaSidecarDoesNotAdmitAfterCancelledPeerPoll(t *testing.T) {
	release := make(chan struct{})
	releaseStatus := releaseSignal(t, release)
	server := &fakeTSNetServer{
		upStatus:      runningWithPeer("other", ""),
		statusEntered: make(chan struct{}),
		statusRelease: release,
		statuses:      []*ipnstate.Status{runningWithPeer("mac-client-123", "")},
		ignoreContext: true,
	}
	cfg := validConfig(t)
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)

	result := make(chan error, 1)
	svc := New(cfg, testTSNetEngine(server), &net.Dialer{})
	settled := false
	receive := func() error { err := waitResult(t, result); settled = true; return err }
	t.Cleanup(func() {
		releaseStatus()
		cancel()
		if !settled {
			receive()
		}
		_ = svc.Stop()
	})
	go func() { result <- svc.Start(ctx) }()
	waitSignal(t, server.statusEntered, "Status")
	cancel()
	releaseStatus()
	assertNetworkReason(t, receive(), "context_cancelled")
	if server.listenCalls != 0 || server.closeCalls != 1 {
		t.Fatalf("listen=%d close=%d", server.listenCalls, server.closeCalls)
	}
	if _, err := os.Stat(filepath.Join(cfg.StateDir, consumedMarker)); !os.IsNotExist(err) {
		t.Fatalf("marker was committed before receipt: %v", err)
	}
	if _, err := os.Stat(cfg.CredentialFile); err != nil {
		t.Fatalf("credential was consumed before receipt: %v", err)
	}
}

func TestProductionAdapterViaSidecarWaitsForRunningAndPeerBeforeAdmission(t *testing.T) {
	upRelease := make(chan struct{})
	statusRelease := make(chan struct{})
	releaseUp := releaseSignal(t, upRelease)
	releaseStatus := releaseSignal(t, statusRelease)
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = listener.Close() })
	server := &fakeTSNetServer{
		upEntered:     make(chan struct{}),
		upRelease:     upRelease,
		upStatus:      runningWithPeer("other", ""),
		statusEntered: make(chan struct{}),
		statusRelease: statusRelease,
		statuses:      []*ipnstate.Status{runningWithPeer("mac-client-123", "")},
		listener:      listener,
	}
	cfg := validConfig(t)
	probes := 0
	result := make(chan error, 1)
	sidecar := New(cfg, testTSNetEngine(server), dialFunc(func(context.Context, string, string) (net.Conn, error) {
		probes++
		client, peer := net.Pipe()
		t.Cleanup(func() { _ = client.Close(); _ = peer.Close() })
		_ = peer.Close()
		return client, nil
	}))
	ctx, cancel := context.WithCancel(context.Background())
	t.Cleanup(cancel)
	settled := false
	receive := func() error { err := waitResult(t, result); settled = true; return err }
	t.Cleanup(func() {
		releaseUp()
		releaseStatus()
		cancel()
		if !settled {
			receive()
		}
		_ = sidecar.Stop()
	})
	go func() { result <- sidecar.Start(ctx) }()
	waitSignal(t, server.upEntered, "Up")
	if _, err := os.Stat(cfg.CredentialFile); err != nil || probes != 0 || server.listenCalls != 0 {
		t.Fatalf("admitted while Up blocked: credential=%v probes=%d listens=%d", err, probes, server.listenCalls)
	}
	releaseUp()
	waitSignal(t, server.statusEntered, "Status")
	if _, err := os.Stat(filepath.Join(cfg.StateDir, consumedMarker)); !os.IsNotExist(err) || probes != 0 || server.listenCalls != 0 {
		t.Fatalf("admitted while peer blocked: marker=%v probes=%d listens=%d", err, probes, server.listenCalls)
	}
	releaseStatus()
	if err := receive(); err != nil {
		t.Fatalf("Start error=%v", err)
	}
	if probes != 1 || server.upCalls != 1 || server.statusCalls != 1 || server.listenCalls != 1 {
		t.Fatalf("calls up=%d status=%d probe=%d listen=%d", server.upCalls, server.statusCalls, probes, server.listenCalls)
	}
	if _, err := os.Stat(filepath.Join(cfg.StateDir, consumedMarker)); err != nil {
		t.Fatalf("marker missing after receipt: %v", err)
	}
	if err := sidecar.Stop(); err != nil {
		t.Fatal(err)
	}
}

func TestTSNetEngineRejectsNilAndEmptyPeerStatusWithoutReceipt(t *testing.T) {
	for name, server := range map[string]*fakeTSNetServer{
		"nil-up":     {upStatus: nil},
		"nil-poll":   {upStatus: runningWithPeer("other", ""), statuses: []*ipnstate.Status{nil}},
		"empty-peer": {upStatus: &ipnstate.Status{BackendState: "Running"}, statuses: []*ipnstate.Status{{BackendState: "Running"}}},
	} {
		t.Run(name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 750*time.Millisecond)
			defer cancel()
			deadline, _ := ctx.Deadline()
			engine := testTSNetEngine(server)
			defer engine.Close()
			receipt, err := engine.Start(ctx, engineConfig(), []byte("one-use"))
			if !errors.Is(err, ErrNetworkJoin) || receipt != (RedemptionReceipt{}) {
				t.Fatalf("receipt=%+v err=%v", receipt, err)
			}
			if server.upCalls != 1 || !server.upAt.Before(deadline) || !server.upDeadline.Equal(deadline) {
				t.Fatal("Up original parent not reached")
			}
			if name != "nil-up" && (server.statusCalls == 0 || !server.statusAt.Before(deadline) || !server.statusDeadline.Equal(deadline) || server.statusContext != server.upContext) {
				t.Fatal("Status original preexpiry context not reached")
			}
		})
	}
}

func TestTSNetEngineDeadlinePollArbitrationRefusesTerminalReceipt(t *testing.T) {
	server := &fakeTSNetServer{upStatus: runningWithPeer("other", "")}
	engine := testTSNetEngine(server)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	defer engine.Close()
	deadline, _ := ctx.Deadline()
	receipt, err := engine.Start(ctx, engineConfig(), nil)
	if server.upCalls != 1 || !server.upAt.Before(deadline) || !server.upDeadline.Equal(deadline) {
		t.Fatal("preexpiry nonmatching Up setup not reached")
	}
	var metadata interface{ NetworkJoinSubReason() string }
	if !errors.As(err, &metadata) {
		t.Fatal("missing finite network category")
	}
	reason := metadata.NetworkJoinSubReason()
	if reason != "deadline_exceeded" && reason != "peer_wait_deadline" {
		t.Fatalf("unexpected arbitration reason=%q", reason)
	}
	assertNetworkReason(t, err, reason)
	if receipt != (RedemptionReceipt{}) {
		t.Fatal("terminal arbitration admitted receipt")
	}
	if server.statusCalls > 0 {
		if server.statusContext != server.upContext || !server.statusDeadline.Equal(deadline) {
			t.Fatal("Status renewed context")
		}
		if server.statusTerminalDelivered && reason != "deadline_exceeded" {
			t.Fatal("reached Status after delivered terminal misclassified")
		}
	}
	// Status0/deadline_exceeded is an earlier terminal fence, never select proof.
	// peer_wait_deadline labels the selected Done case, never inferred poll order.
	t.Logf("joined Status=%d terminal-at-entry=%t reason=%s", server.statusCalls, server.statusTerminalDelivered, reason)
}

func TestProductionAdapterViaSidecarTerminalContextWinsOverLateResult(t *testing.T) {
	for _, stage := range []string{"start", "up", "peer"} {
		for _, terminal := range []string{"context_cancelled", "deadline_exceeded"} {
			lateResults := []string{"success", "error"}
			if stage == "start" {
				lateResults = []string{"success"} // upstream Start errors retain engine_start precedence
			}
			for _, late := range lateResults {
				t.Run(stage+"/"+terminal+"/"+late, func(t *testing.T) {
					release := make(chan struct{})
					releaseCall := releaseSignal(t, release)
					entered := make(chan struct{})
					server := &fakeTSNetServer{ignoreContext: true, upStatus: runningWithPeer("mac-client-123", "")}
					if stage == "start" {
						server.startEntered = entered
						server.startRelease = release
					} else if stage == "up" {
						server.upEntered = entered
						server.upRelease = release
						if late == "error" {
							server.upErr = errors.New("tsnet.Up: backend: TOKEN_CANARY")
						}
					} else {
						server.upStatus = runningWithPeer("other", "")
						server.statusEntered = entered
						server.statusRelease = release
						server.statuses = []*ipnstate.Status{runningWithPeer("mac-client-123", "")}
						if late == "error" {
							server.statusErr = errors.New("TOKEN_CANARY")
						}
					}
					ctx, cancel := context.WithTimeout(context.Background(), 750*time.Millisecond)
					t.Cleanup(cancel)

					cfg := validConfig(t)
					dials := 0
					svc := New(cfg, testTSNetEngine(server), dialFunc(func(context.Context, string, string) (net.Conn, error) { dials++; return nil, errors.New("unexpected") }))
					result := make(chan error, 1)
					settled := false
					receive := func() error { err := waitResult(t, result); settled = true; return err }
					t.Cleanup(func() {
						releaseCall()
						cancel()
						if !settled {
							receive()
						}
						_ = svc.Stop()
					})
					go func() { result <- svc.Start(ctx) }()
					waitSignal(t, entered, stage)
					deadline, _ := ctx.Deadline()
					entry := server.startAt
					if stage == "up" {
						entry = server.upAt
					}
					if stage == "peer" {
						entry = server.statusAt
						if server.upContext != server.statusContext || !server.upDeadline.Equal(deadline) || !server.statusDeadline.Equal(deadline) {
							t.Fatal("peer stage renewed original parent")
						}
					}
					if !entry.Before(deadline) || ctx.Err() != nil {
						t.Fatal("stage missed original preterminal setup")
					}
					if terminal == "context_cancelled" {
						cancel()
					} else {
						waitSignal(t, ctx.Done(), "caller deadline")
					}
					if (terminal == "context_cancelled" && !errors.Is(ctx.Err(), context.Canceled)) || (terminal == "deadline_exceeded" && !errors.Is(ctx.Err(), context.DeadlineExceeded)) {
						t.Fatal("terminal delivery not observed")
					}
					releaseCall()
					assertNetworkReason(t, receive(), terminal)
					if stage == "start" && (server.clearAuthKeyCalls != 1 || server.upCalls != 0 || server.statusCalls != 0) {
						t.Fatal("late contextless Start admitted post-start work")
					}
					_ = svc.Stop()
					if dials != 0 || server.listenCalls != 0 || server.closeCalls != 1 {
						t.Fatal("late result admitted or close repeated")
					}
					if _, err := os.Stat(cfg.CredentialFile); err != nil {
						t.Fatal("late result consumed credential")
					}
					if _, err := os.Stat(filepath.Join(cfg.StateDir, consumedMarker)); !os.IsNotExist(err) {
						t.Fatal("late result committed")
					}
				})
			}
		}
	}
}

// THR228seq390: authored while all unit execution is suspended.
func TestCredentialWhitespaceAtProductionAuthKeyInput(t *testing.T) {
	for _, tc := range []struct{ name, input, want string }{
		{"clean", "key-value", "key-value"},
		{"lf", "key-value\n", "key-value"},
		{"crlf", "key-value\r\n", "key-value"},
		{"surrounding", " \t\r\nkey-value\r\n\t ", "key-value"},
		{"unicode_edges", "\u00a0key-value\u2003", "key-value"},
		{"interior", " \tkey \t\r\nvalue\x00\xff \r\n", "key \t\r\nvalue\x00\xff"},
	} {
		for _, failure := range []string{"success", "start", "up"} {
			t.Run(tc.name+"/"+failure, func(t *testing.T) {
				cfg := validConfig(t)
				if err := os.WriteFile(cfg.CredentialFile, []byte(tc.input), 0o600); err != nil {
					t.Fatal(err)
				}
				listener := &oneListener{accepted: make(chan struct{}), blockAfter: true, closed: make(chan struct{})}
				server := &fakeTSNetServer{upStatus: runningWithPeer("mac-client-123", ""), listener: listener}
				if failure == "start" {
					server.startErr = errors.New("SECRET_CANARY")
				}
				if failure == "up" {
					server.upErr = errors.New("tsnet.Up: backend: SECRET_CANARY")
				}
				engine := NewTSNetEngine()
				factory := engine.newServer
				var owned []byte
				calls := 0
				engine.newServer = func(c EngineConfig, credential []byte) tsnetServer {
					calls++
					owned = credential // retain alias to verify Start's clearing on return
					production := factory(c, credential).(productionTSNetServer)
					if production.server.AuthKey != tc.want {
						t.Fatal("production AuthKey bytes differ")
					}
					production.ClearAuthKey()
					if production.server.AuthKey != "" {
						t.Fatal("production AuthKey reference retained")
					}
					return server // never launch a real provider in a regression
				}
				probeClient, probeServer := net.Pipe()
				defer probeServer.Close()
				defer probeClient.Close()
				svc := New(cfg, engine, dialFunc(func(context.Context, string, string) (net.Conn, error) { return probeClient, nil }))
				t.Cleanup(func() { _ = svc.Stop() })
				err := svc.Start(context.Background())
				_ = svc.Stop()
				wantUp, wantListen := 1, 0
				if failure == "start" {
					wantUp = 0
				}
				if failure == "success" {
					wantListen = 1
				}
				if server.startCalls != 1 || server.upCalls != wantUp || server.listenCalls != wantListen || server.statusCalls != 0 {
					t.Fatal("adapter stage not reached exactly")
				}
				if calls != 1 || server.clearAuthKeyCalls != 1 {
					t.Fatal("credential consumer/clear count differs")
				}
				for _, b := range owned[:cap(owned)] {
					if b != 0 {
						t.Fatal("owned credential buffer retained bytes")
					}
				}
				_, markerErr := os.Stat(filepath.Join(cfg.StateDir, consumedMarker))
				_, fileErr := os.Stat(cfg.CredentialFile)
				if failure == "success" {
					if err != nil || markerErr != nil || !os.IsNotExist(fileErr) {
						t.Fatal("success did not durably consume once")
					}
					// A reintroduced key must refuse even after successful normalization.
					if err := os.WriteFile(cfg.CredentialFile, []byte(tc.input), 0o600); err != nil {
						t.Fatal(err)
					}
					if _, err := consumeInput(cfg); !errors.Is(err, ErrCredentialInput) {
						t.Fatal("consumed input replay accepted")
					}
				} else {
					if err == nil || strings.Contains(err.Error(), "SECRET_CANARY") || !os.IsNotExist(markerErr) || fileErr != nil {
						t.Fatal("failed startup changed one-use lifecycle or leaked")
					}
				}
			})
		}
	}
}

func TestProductionAdapterViaSidecarReceivesSeventyFiveSecondAcceptanceCeiling(t *testing.T) {
	cfg := validConfig(t)
	cfg.ExpectedPeers = []string{"expected"}
	events := []string{}
	listener := &oneListener{blockAfter: true, closed: make(chan struct{}), accepted: make(chan struct{}), events: &events}
	server := &fakeTSNetServer{upStatus: runningWithPeer("expected", ""), listener: listener}
	probe := &countedProbe{}
	engine := testTSNetEngine(server)
	svc := New(cfg, engine, dialFunc(func(ctx context.Context, _, _ string) (net.Conn, error) {
		deadline, _ := ctx.Deadline()
		if deadline.After(server.upDeadline) {
			t.Fatal("connector renewed engine acceptance budget")
		}
		return probe, nil
	}))
	t.Cleanup(func() { _ = svc.Stop() })
	entry := time.Now()
	if err := svc.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	if server.upDeadline.Before(entry.Add(75*time.Second)) || server.upDeadline.After(time.Now().Add(75*time.Second)) {
		t.Fatalf("adapter deadline=%s; independent80s cap escaped enclosing75s", server.upDeadline)
	}
	if server.startCalls != 1 || server.upCalls != 1 || server.listenCalls != 1 || server.clearAuthKeyCalls != 1 || probe.closes != 1 {
		t.Fatal("startup/redeem/admission repeated")
	}
	if err := svc.Stop(); err != nil {
		t.Fatal(err)
	}
	if server.closeCalls != 1 {
		t.Fatal("engine not closed exactly once")
	}
}
