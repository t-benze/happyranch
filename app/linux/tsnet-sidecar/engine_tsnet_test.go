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
	statusFn                                 func(context.Context) (*ipnstate.Status, error)
	ignoreContext                            bool
	mu                                       sync.Mutex
	startCalls, upCalls                      int
	statusCalls, closeCalls, listenCalls     int
	clearAuthKeyCalls                        int
	upDeadline, statusDeadline               time.Time
	upEnteredConsumed, statusEnteredConsumed bool
	listener                                 net.Listener
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
	f.statusDeadline, _ = ctx.Deadline()
	entered, release, err, statusFn, ignoreContext := f.statusEntered, f.statusRelease, f.statusErr, f.statusFn, f.ignoreContext
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
	if statusFn != nil {
		return statusFn(ctx)
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
	return &TSNetEngine{newServer: func(EngineConfig, []byte) tsnetServer { return server }, peerPollInterval: time.Millisecond, now: time.Now}
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
	upRelease := make(chan struct{})
	statusRelease := make(chan struct{})
	server := &fakeTSNetServer{
		upEntered:     make(chan struct{}),
		upRelease:     upRelease,
		upStatus:      runningWithPeer("", "other.example."),
		statusEntered: make(chan struct{}),
		statusRelease: statusRelease,
		statuses:      []*ipnstate.Status{runningWithPeer("", "expected.")},
	}
	engine := testTSNetEngine(server)
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	callerDeadline, ok := ctx.Deadline()
	if !ok {
		t.Fatal("caller deadline missing")
	}
	result := make(chan struct {
		receipt RedemptionReceipt
		err     error
	}, 1)
	go func() {
		receipt, err := engine.Start(ctx, engineConfig(), []byte("one-use"))
		result <- struct {
			receipt RedemptionReceipt
			err     error
		}{receipt, err}
	}()
	waitSignal(t, server.upEntered, "Up")
	if !server.upDeadline.Equal(callerDeadline) {
		t.Fatalf("Up deadline=%s caller deadline=%s", server.upDeadline, callerDeadline)
	}
	time.Sleep(10 * time.Millisecond)
	releaseSignal(t, upRelease)()
	waitSignal(t, server.statusEntered, "Status")
	if !server.statusDeadline.Equal(server.upDeadline) {
		t.Fatalf("waits used different deadlines: up=%s status=%s", server.upDeadline, server.statusDeadline)
	}
	if !server.statusDeadline.Equal(callerDeadline) {
		t.Fatalf("Status renewed caller deadline: status=%s caller=%s", server.statusDeadline, callerDeadline)
	}
	releaseSignal(t, statusRelease)()
	select {
	case result := <-result:
		receipt, err := result.receipt, result.err
		if err != nil || !receipt.Redeemed || !receipt.Durable || !receipt.ExpectedPeerVisible {
			t.Fatalf("receipt=%+v err=%v", receipt, err)
		}
	case <-time.After(testWaitTimeout):
		t.Fatal("timed out waiting for Start")
	}
	if server.startCalls != 1 || server.upCalls != 1 || server.statusCalls == 0 {
		t.Fatalf("calls start=%d up=%d status=%d", server.startCalls, server.upCalls, server.statusCalls)
	}
	if server.clearAuthKeyCalls != 1 {
		t.Fatalf("auth key clears=%d", server.clearAuthKeyCalls)
	}
}

func TestTSNetEngineCapsBackgroundStartupAtEightySeconds(t *testing.T) {
	statusRelease := make(chan struct{})
	server := &fakeTSNetServer{
		upStatus:      runningWithPeer("other", ""),
		statusEntered: make(chan struct{}),
		statusRelease: statusRelease,
	}
	engine := testTSNetEngine(server)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	started := time.Now()
	result := make(chan error, 1)
	go func() { _, err := engine.Start(ctx, engineConfig(), []byte("one-use")); result <- err }()
	waitSignal(t, server.statusEntered, "Status")
	if server.upDeadline.IsZero() || server.statusDeadline.IsZero() {
		t.Fatalf("missing deadlines: up=%s status=%s", server.upDeadline, server.statusDeadline)
	}
	if !server.upDeadline.Equal(server.statusDeadline) {
		t.Fatalf("waits used different deadlines: up=%s status=%s", server.upDeadline, server.statusDeadline)
	}
	if remaining := time.Until(server.upDeadline); remaining > tsnetStartupBudget || remaining < tsnetStartupBudget-2*time.Second {
		t.Fatalf("background startup budget remaining=%s, want near %s", remaining, tsnetStartupBudget)
	}
	if server.upDeadline.After(started.Add(tsnetStartupBudget + 100*time.Millisecond)) {
		t.Fatalf("startup deadline=%s exceeds 80-second cap from %s", server.upDeadline, started)
	}
	cancel()
	releaseSignal(t, statusRelease)()
	if err := waitResult(t, result); !errors.Is(err, ErrNetworkJoin) {
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
	for _, terminal := range []string{"context_cancelled", "deadline_exceeded"} {
		t.Run(terminal, func(t *testing.T) {
			server := &fakeTSNetServer{upStatus: runningWithPeer("expected", "")}
			ctx, cancel := context.WithCancel(context.Background())
			if terminal == "deadline_exceeded" {
				cancel()
				ctx, cancel = context.WithDeadline(context.Background(), time.Now().Add(-time.Second))
			}
			cancel()
			receipt, err := testTSNetEngine(server).Start(ctx, engineConfig(), []byte("one-use"))
			assertNetworkReason(t, err, terminal)
			if receipt != (RedemptionReceipt{}) || server.startCalls != 0 || server.upCalls != 0 || server.statusCalls != 0 {
				t.Fatal("terminal caller admitted upstream work")
			}
		})
	}
}

func TestTSNetEngineDoesNotRenewBudgetAfterContextlessStart(t *testing.T) {
	release := make(chan struct{})
	releaseStart := releaseSignal(t, release)
	server := &fakeTSNetServer{startEntered: make(chan struct{}), startRelease: release, upStatus: runningWithPeer("expected", "")}
	engine := testTSNetEngine(server)
	deadline := time.Now().Add(time.Hour)
	ctx, cancel := context.WithDeadline(context.Background(), deadline)
	defer cancel()
	engine.now = func() time.Time { return deadline.Add(time.Nanosecond) }
	result := make(chan error, 1)
	go func() { _, err := engine.Start(ctx, engineConfig(), []byte("one-use")); result <- err }()
	waitSignal(t, server.startEntered, "Start")
	releaseStart()
	assertNetworkReason(t, waitResult(t, result), "deadline_exceeded")
	if server.clearAuthKeyCalls != 0 || server.upCalls != 0 || server.statusCalls != 0 {
		t.Fatalf("late start minted post-start work: clear=%d up=%d status=%d", server.clearAuthKeyCalls, server.upCalls, server.statusCalls)
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
			err := svc.Start(context.Background())
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

type fakeTSNetLocalClient struct {
	status    *ipnstate.Status
	statusErr error
}

func (c fakeTSNetLocalClient) Status(context.Context) (*ipnstate.Status, error) {
	return c.status, c.statusErr
}

func TestTSNetEngineNilFactoriesRetainInitializationRefusal(t *testing.T) {
	for _, name := range []string{"nil_factory", "nil_server"} {
		t.Run(name, func(t *testing.T) {
			cfg := validConfig(t)
			engine := &TSNetEngine{}
			if name == "nil_server" {
				engine.newServer = func(EngineConfig, []byte) tsnetServer { return nil }
			}
			dials := 0
			svc := New(cfg, engine, dialFunc(func(context.Context, string, string) (net.Conn, error) { dials++; return nil, errors.New("unexpected") }))
			err := svc.Start(context.Background())
			if !errors.Is(err, ErrEngineStart) || err.Error() != ErrEngineStart.Error() || errors.Is(err, ErrNetworkJoin) {
				t.Fatal("nil factory changed initialization refusal")
			}
			_ = svc.Stop()
			if dials != 0 {
				t.Fatal("nil factory admitted connector")
			}
			if _, err := os.Stat(cfg.CredentialFile); err != nil {
				t.Fatal("nil factory consumed credential")
			}
			if _, err := os.Stat(filepath.Join(cfg.StateDir, consumedMarker)); !os.IsNotExist(err) {
				t.Fatal("nil factory committed failed receipt")
			}
		})
	}
}

func TestProductionTSNetServerStatusUsesLocalClientAndMapsClosedEngineError(t *testing.T) {
	hostileAcquire := fmt.Errorf("hostile acquisition detail: %w", errors.New("acquire failed"))
	hostileStatus := fmt.Errorf("hostile status detail: %w", errors.New("status failed"))
	ready := runningWithPeer("expected", "")
	for name, tc := range map[string]struct {
		localClient func() (tsnetLocalClient, error)
		wantErr     error
		wantStatus  *ipnstate.Status
	}{
		"acquisition failure": {
			localClient: func() (tsnetLocalClient, error) { return nil, hostileAcquire },
			wantErr:     hostileAcquire,
		},
		"downstream status failure": {
			localClient: func() (tsnetLocalClient, error) { return fakeTSNetLocalClient{statusErr: hostileStatus}, nil },
			wantErr:     hostileStatus,
		},
		"immediate success": {
			localClient: func() (tsnetLocalClient, error) { return fakeTSNetLocalClient{status: ready}, nil },
			wantStatus:  ready,
		},
	} {
		t.Run(name, func(t *testing.T) {
			production := productionTSNetServer{localClient: tc.localClient}
			status, err := production.Status(context.Background())
			if tc.wantErr != nil {
				if !errors.Is(err, tc.wantErr) || status != nil {
					t.Fatalf("status=%+v err=%v", status, err)
				}
				return
			}
			if err != nil || status != tc.wantStatus {
				t.Fatalf("status=%+v err=%v", status, err)
			}
		})
	}

	for name, localClient := range map[string]func() (tsnetLocalClient, error){
		"acquisition": func() (tsnetLocalClient, error) { return nil, hostileAcquire },
		"status": func() (tsnetLocalClient, error) {
			return fakeTSNetLocalClient{statusErr: hostileStatus}, nil
		},
	} {
		t.Run("closed engine "+name, func(t *testing.T) {
			production := productionTSNetServer{localClient: localClient}
			server := &fakeTSNetServer{
				upStatus: runningWithPeer("other", ""),
				statusFn: production.Status,
			}
			_, err := testTSNetEngine(server).Start(context.Background(), engineConfig(), []byte("one-use"))
			if !errors.Is(err, ErrNetworkJoin) || err.Error() != ErrNetworkJoin.Error() {
				t.Fatalf("err=%v, want closed %v", err, ErrNetworkJoin)
			}
			assertNetworkReason(t, err, "peer_status_error")
			if strings.Contains(err.Error(), "hostile") || strings.Contains(err.Error(), "acquire") || strings.Contains(err.Error(), "status") {
				t.Fatalf("wrapped LocalClient cause leaked through engine: %v", err)
			}
		})
	}
}

func TestProductionAdapterViaSidecarDoesNotAdmitAfterCancelledUp(t *testing.T) {
	server := &fakeTSNetServer{upEntered: make(chan struct{}), upRelease: make(chan struct{}), upStatus: runningWithPeer("expected", "")}
	engine := testTSNetEngine(server)
	cfg := validConfig(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan error, 1)
	go func() { result <- New(cfg, engine, &net.Dialer{}).Start(ctx) }()
	waitSignal(t, server.upEntered, "Up")
	cancel()
	assertNetworkReason(t, waitResult(t, result), "context_cancelled")
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
	releaseUp := releaseSignal(t, release)
	server := &fakeTSNetServer{upEntered: make(chan struct{}), upRelease: release, upStatus: runningWithPeer("expected", ""), ignoreContext: true}
	engine := testTSNetEngine(server)
	cfg := validConfig(t)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan error, 1)
	go func() { result <- New(cfg, engine, &net.Dialer{}).Start(ctx) }()
	waitSignal(t, server.upEntered, "Up")
	cancel()
	releaseUp()
	assertNetworkReason(t, waitResult(t, result), "context_cancelled")
	if server.listenCalls != 0 || server.closeCalls != 1 {
		t.Fatalf("listen=%d close=%d", server.listenCalls, server.closeCalls)
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
	defer cancel()
	result := make(chan error, 1)
	go func() { result <- New(cfg, testTSNetEngine(server), &net.Dialer{}).Start(ctx) }()
	waitSignal(t, server.statusEntered, "Status")
	cancel()
	releaseStatus()
	assertNetworkReason(t, waitResult(t, result), "context_cancelled")
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
		_ = peer.Close()
		return client, nil
	}))
	t.Cleanup(func() { _ = sidecar.Stop() })
	t.Cleanup(func() {
		releaseUp()
		releaseStatus()
	})
	go func() { result <- sidecar.Start(context.Background()) }()
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
	if err := waitResult(t, result); err != nil {
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
		"empty-peer": {upStatus: &ipnstate.Status{BackendState: "Running"}, statuses: []*ipnstate.Status{&ipnstate.Status{BackendState: "Running"}}},
	} {
		t.Run(name, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 15*time.Millisecond)
			defer cancel()
			receipt, err := testTSNetEngine(server).Start(ctx, engineConfig(), []byte("one-use"))
			if !errors.Is(err, ErrNetworkJoin) || receipt.Redeemed || receipt.Durable || receipt.ExpectedPeerVisible {
				t.Fatalf("receipt=%+v err=%v", receipt, err)
			}
		})
	}
}

func TestTSNetEnginePeerWaitSelectReportsDeadline(t *testing.T) {
	server := &fakeTSNetServer{upStatus: runningWithPeer("other", "")}
	engine := testTSNetEngine(server)
	engine.peerPollInterval = time.Hour
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	receipt, err := engine.Start(ctx, engineConfig(), nil)
	assertNetworkReason(t, err, "peer_wait_deadline")
	if receipt != (RedemptionReceipt{}) || server.statusCalls != 0 {
		t.Fatal("peer-wait select mislabeled status work")
	}
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
					ctx, cancel := context.WithCancel(context.Background())
					if terminal == "deadline_exceeded" {
						cancel()
						ctx, cancel = context.WithTimeout(context.Background(), 50*time.Millisecond)
					}
					defer cancel()
					cfg := validConfig(t)
					dials := 0
					svc := New(cfg, testTSNetEngine(server), dialFunc(func(context.Context, string, string) (net.Conn, error) { dials++; return nil, errors.New("unexpected") }))
					result := make(chan error, 1)
					go func() { result <- svc.Start(ctx) }()
					waitSignal(t, entered, stage)
					if terminal == "context_cancelled" {
						cancel()
					} else {
						waitSignal(t, ctx.Done(), "caller deadline")
					}
					releaseCall()
					assertNetworkReason(t, waitResult(t, result), terminal)
					if stage == "start" && (server.clearAuthKeyCalls != 0 || server.upCalls != 0 || server.statusCalls != 0) {
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
