package sidecar

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

type fakeEngine struct {
	listener                      net.Listener
	receipt                       RedemptionReceipt
	startErr, listenErr, closeErr error
	events                        *[]string
	listenEntered, listenRelease  chan struct{}
	closed                        chan struct{}
	startFn                       func(context.Context, EngineConfig, []byte) (RedemptionReceipt, error)
}

func (f *fakeEngine) Start(ctx context.Context, cfg EngineConfig, credential []byte) (RedemptionReceipt, error) {
	*f.events = append(*f.events, "start")
	if f.startFn != nil {
		return f.startFn(ctx, cfg, credential)
	}
	return f.receipt, f.startErr
}
func (f *fakeEngine) Listen(string) (net.Listener, error) {
	*f.events = append(*f.events, "listen")
	if f.listenEntered != nil {
		close(f.listenEntered)
		<-f.listenRelease
	}
	return f.listener, f.listenErr
}
func (f *fakeEngine) Close() error {
	*f.events = append(*f.events, "engine-close")
	if f.closed != nil {
		close(f.closed)
	}
	return f.closeErr
}

func validConfig(t *testing.T) Config {
	t.Helper()
	state := filepath.Join(t.TempDir(), "state")
	if err := os.Mkdir(state, 0o700); err != nil {
		t.Fatal(err)
	}
	cred := filepath.Join(filepath.Dir(state), "credential")
	if err := os.WriteFile(cred, []byte("secret-value\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return Config{StateDir: state, CredentialFile: cred, ControlURL: "https://headscale.private.example", RoleIdentity: "home-sidecar-123", ExpectedPeers: []string{"mac-client-123"}, ListenAddr: ":443", ConnectorAddr: "127.0.0.1:9443", DERPPolicy: "private-only"}
}

func TestValidateRejectsUnsafeTopology(t *testing.T) {
	base := validConfig(t)
	cases := []func(*Config){
		func(c *Config) { c.ControlURL = "https://controlplane.tailscale.com" },
		func(c *Config) { c.ControlURL = "http://headscale.private.example" },
		func(c *Config) { c.ConnectorAddr = "0.0.0.0:9443" },
		func(c *Config) { c.ConnectorAddr = "127.0.0.2:9443" },
		func(c *Config) { c.ConnectorAddr = "127.0.0.1:8765" },
		func(c *Config) { c.RoleIdentity = "ambiguous" },
		func(c *Config) { c.DERPPolicy = "public-fallback" },
	}
	for i, mutate := range cases {
		c := base
		mutate(&c)
		if err := c.Validate(); !errors.Is(err, ErrConfiguration) {
			t.Fatalf("case %d: %v", i, err)
		}
	}
}

func TestCredentialFailuresOccurBeforeListenAndAreRedacted(t *testing.T) {
	for _, name := range []string{"missing", "symlink", "loose", "empty", "whitespace", "replay"} {
		t.Run(name, func(t *testing.T) {
			cfg := validConfig(t)
			events := []string{}
			target := cfg.CredentialFile
			switch name {
			case "missing":
				os.Remove(target)
			case "symlink":
				os.Remove(target)
				os.Symlink(filepath.Join(t.TempDir(), "hostile-secret"), target)
			case "loose":
				os.Chmod(target, 0o644)
			case "empty":
				os.WriteFile(target, nil, 0o600)
			case "whitespace":
				os.WriteFile(target, []byte(" \t\r\n\u00a0"), 0o600)
			case "replay":
				os.WriteFile(filepath.Join(cfg.StateDir, consumedMarker), []byte("1"), 0o600)
			}
			e := &fakeEngine{receipt: RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, events: &events}
			s := New(cfg, e, &net.Dialer{})
			err := s.Start(context.Background())
			if err == nil || strings.Contains(err.Error(), "secret") || len(events) != 0 {
				t.Fatalf("err=%v events=%v", err, events)
			}
		})
	}
}

func TestCredentialPathWithSymlinkedParentFailsClosed(t *testing.T) {
	cfg := validConfig(t)
	realParent := filepath.Dir(cfg.CredentialFile)
	alias := filepath.Join(t.TempDir(), "alias")
	if err := os.Symlink(realParent, alias); err != nil {
		t.Fatal(err)
	}
	cfg.CredentialFile = filepath.Join(alias, filepath.Base(cfg.CredentialFile))
	events := []string{}
	err := New(cfg, &fakeEngine{events: &events}, &net.Dialer{}).Start(context.Background())
	if !errors.Is(err, ErrCredentialInput) || len(events) != 0 {
		t.Fatalf("err=%v events=%v", err, events)
	}
}

func TestRedemptionAndDeletionMustBeDurableBeforeListen(t *testing.T) {
	for _, receipt := range []RedemptionReceipt{{}, {Redeemed: true}, {Redeemed: true, Durable: true}} {
		cfg := validConfig(t)
		events := []string{}
		e := &fakeEngine{receipt: receipt, events: &events}
		want := ErrDurableCommit
		if receipt.Redeemed && receipt.Durable {
			want = ErrNetworkJoin
		}
		if err := New(cfg, e, &net.Dialer{}).Start(context.Background()); !errors.Is(err, want) {
			t.Fatalf("%v", err)
		}
		if contains(events, "listen") {
			t.Fatal(events)
		}
	}
}

func TestConnectorProbeFailureClosesEngineBeforeListener(t *testing.T) {
	cfg := validConfig(t)
	events := []string{}
	e := &fakeEngine{receipt: RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, events: &events}
	s := New(cfg, e, dialFunc(func(context.Context, string, string) (net.Conn, error) { return nil, errors.New("hostile secret") }))
	err := s.Start(context.Background())
	if !errors.Is(err, ErrConnector) || strings.Contains(err.Error(), "secret") || contains(events, "listen") {
		t.Fatalf("err=%v events=%v", err, events)
	}
	if events[len(events)-1] != "engine-close" {
		t.Fatal(events)
	}
}

func TestStartSuccessConsumesCredentialThenProxiesRawBytes(t *testing.T) {
	cfg := validConfig(t)
	events := []string{}
	probeClient, probeServer := net.Pipe()
	tailClient, tailServer := net.Pipe()
	connectorClient, connectorServer := net.Pipe()
	listener := &oneListener{conn: tailServer, blockAfter: true, closed: make(chan struct{}), accepted: make(chan struct{}), events: &events}
	e := &fakeEngine{listener: listener, receipt: RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, events: &events}
	dials := []net.Conn{probeClient, connectorClient}
	caller := context.Background()
	dial := dialFunc(func(ctx context.Context, _, _ string) (net.Conn, error) {
		if len(dials) == 1 && (ctx != caller || ctx.Err() != nil) {
			return nil, errors.New("proxy did not retain caller lifetime")
		}
		c := dials[0]
		dials = dials[1:]
		return c, nil
	})
	s := New(cfg, e, dial)
	if err := s.Start(caller); err != nil {
		t.Fatal(err)
	}
	_ = probeServer.Close()
	if _, err := os.Stat(cfg.CredentialFile); !os.IsNotExist(err) {
		t.Fatalf("credential remains: %v", err)
	}
	<-listener.accepted
	go tailClient.Write([]byte("raw-request"))
	got := make([]byte, 11)
	if _, err := io.ReadFull(connectorServer, got); err != nil {
		t.Fatal(err)
	}
	if string(got) != "raw-request" {
		t.Fatalf("%q", got)
	}
	go connectorServer.Write([]byte("raw-reply"))
	reply := make([]byte, 9)
	if _, err := io.ReadFull(tailClient, reply); err != nil {
		t.Fatal(err)
	}
	if string(reply) != "raw-reply" {
		t.Fatalf("%q", reply)
	}
	if err := s.Stop(); err != nil {
		t.Fatal(err)
	}
	if events[len(events)-2] != "listener-close" || events[len(events)-1] != "engine-close" {
		t.Fatal(events)
	}
}

func TestSystemdStagedCredentialIsConsumedOnceAndNeverUnlinked(t *testing.T) {
	cfg := validConfig(t)
	credentialDir := filepath.Join(filepath.Dir(cfg.StateDir), "systemd-credentials")
	if err := os.Mkdir(credentialDir, 0o700); err != nil {
		t.Fatal(err)
	}
	exactCredential := filepath.Join(credentialDir, "enrollment.key")
	if err := os.Rename(cfg.CredentialFile, exactCredential); err != nil {
		t.Fatal(err)
	}
	cfg.CredentialFile = exactCredential
	t.Setenv("CREDENTIALS_DIRECTORY", credentialDir)
	if err := os.Chmod(cfg.CredentialFile, 0o400); err != nil {
		t.Fatal(err)
	}
	events := []string{}
	probeClient, probeServer := net.Pipe()
	defer probeServer.Close()
	listener := &oneListener{acceptErr: net.ErrClosed, events: &events, accepted: make(chan struct{})}
	e := &fakeEngine{listener: listener, receipt: RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, events: &events}
	s := New(cfg, e, dialFunc(func(context.Context, string, string) (net.Conn, error) { return probeClient, nil }))
	if err := s.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(cfg.CredentialFile); err != nil {
		t.Fatalf("systemd-staged credential must remain systemd-owned: %v", err)
	}
	if _, err := os.Stat(filepath.Join(cfg.StateDir, consumedMarker)); err != nil {
		t.Fatalf("durable consumption marker missing: %v", err)
	}
	_ = s.Stop()
	replayEvents := []string{}
	// Halt before dialing if a regression wrongly reaches the second engine.
	replayEngine := &fakeEngine{startErr: ErrEngineStart, events: &replayEvents}
	if err := New(cfg, replayEngine, &net.Dialer{}).Start(context.Background()); !errors.Is(err, ErrCredentialInput) || len(replayEvents) != 0 {
		t.Fatalf("staged replay error = %v; engine events = %v", err, replayEvents)
	}
}

func TestEnrolledStateRestartDoesNotReadOrRequireCredential(t *testing.T) {
	cfg := validConfig(t)
	if err := os.Remove(cfg.CredentialFile); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(cfg.StateDir, consumedMarker), []byte("durable\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	events := []string{}
	probeClient, probeServer := net.Pipe()
	defer probeServer.Close()
	listener := &oneListener{acceptErr: net.ErrClosed, events: &events, accepted: make(chan struct{})}
	e := &fakeEngine{listener: listener, receipt: RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, events: &events}
	s := New(cfg, e, dialFunc(func(context.Context, string, string) (net.Conn, error) { return probeClient, nil }))
	if err := s.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	_ = s.Stop()
}

func TestPartialStartAndFailuresCloseListenerFirst(t *testing.T) {
	cfg := validConfig(t)
	events := []string{}
	l := &oneListener{acceptErr: errors.New("raw hostile path /tmp/secret"), events: &events, accepted: make(chan struct{})}
	e := &fakeEngine{listener: l, receipt: RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, events: &events}
	probeClient, probeServer := net.Pipe()
	s := New(cfg, e, dialFunc(func(context.Context, string, string) (net.Conn, error) { return probeClient, nil }))
	if err := s.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	_ = probeServer.Close()
	<-l.accepted
	if err := s.Stop(); err != nil && strings.Contains(err.Error(), "secret") {
		t.Fatal(err)
	}
	if events[len(events)-2] != "listener-close" || events[len(events)-1] != "engine-close" {
		t.Fatal(events)
	}
}

func TestConcurrentStopIsIdempotent(t *testing.T) {
	cfg := validConfig(t)
	events := []string{}
	l := &oneListener{acceptErr: net.ErrClosed, events: &events, accepted: make(chan struct{})}
	probeClient, probeServer := net.Pipe()
	s := New(cfg, &fakeEngine{listener: l, receipt: RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, events: &events}, dialFunc(func(context.Context, string, string) (net.Conn, error) { return probeClient, nil }))
	if err := s.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	_ = probeServer.Close()
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); _ = s.Stop() }()
	}
	wg.Wait()
	if count(events, "listener-close") != 1 || count(events, "engine-close") != 1 {
		t.Fatal(events)
	}
}

func TestStopWhileListenBlockedRejectsPostShutdownAdmission(t *testing.T) {
	cfg := validConfig(t)
	events := []string{}
	entered, release := make(chan struct{}), make(chan struct{})
	l := &oneListener{acceptErr: errors.New("must never accept"), events: &events, accepted: make(chan struct{})}
	probeClient, probeServer := net.Pipe()
	e := &fakeEngine{listener: l, receipt: RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, events: &events, listenEntered: entered, listenRelease: release}
	s := New(cfg, e, dialFunc(func(context.Context, string, string) (net.Conn, error) { return probeClient, nil }))
	startResult := make(chan error, 1)
	go func() { startResult <- s.Start(context.Background()) }()
	<-entered
	if err := s.Start(context.Background()); err != ErrListener || count(events, "start") != 1 {
		t.Fatal("concurrent Start escaped admission fence")
	}
	_ = probeServer.Close()
	stopResult := make(chan error, 1)
	go func() { stopResult <- s.Stop() }()
	select {
	case err := <-stopResult:
		t.Fatalf("Stop returned before in-flight Listen was resolved: %v", err)
	case <-time.After(20 * time.Millisecond):
	}
	close(release)
	if err := <-startResult; !errors.Is(err, ErrListener) || strings.Contains(err.Error(), "secret") {
		t.Fatalf("Start error = %v", err)
	}
	if err := <-stopResult; err != nil {
		t.Fatalf("Stop error = %v", err)
	}
	select {
	case <-l.accepted:
		t.Fatal("post-shutdown accept loop admitted")
	default:
	}
	if strings.Join(events, ",") != "start,listen,listener-close,engine-close" {
		t.Fatalf("teardown order = %v", events)
	}
}

func TestUnexpectedAcceptFailureAutomaticallyTearsDownAndIsReported(t *testing.T) {
	cfg := validConfig(t)
	events := []string{}
	l := &oneListener{acceptErr: errors.New("hostile secret /tmp/private"), events: &events, accepted: make(chan struct{})}
	probeClient, probeServer := net.Pipe()
	closed := make(chan struct{})
	e := &fakeEngine{listener: l, receipt: RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, events: &events, closed: closed}
	s := New(cfg, e, dialFunc(func(context.Context, string, string) (net.Conn, error) { return probeClient, nil }))
	if err := s.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	_ = probeServer.Close()
	<-l.accepted
	select {
	case <-closed:
	case <-time.After(time.Second):
		t.Fatal("automatic teardown did not complete")
	}
	if err := s.Stop(); !errors.Is(err, ErrListener) || strings.Contains(err.Error(), "secret") || strings.Contains(err.Error(), "/tmp") {
		t.Fatalf("Stop error = %v", err)
	}
	if strings.Join(events[len(events)-2:], ",") != "listener-close,engine-close" {
		t.Fatalf("teardown order = %v", events)
	}
}

// These fixtures control only the production Engine/Listener/Dialer contracts.
// They never implement Sidecar ownership, completion or draining themselves.
type shutdownEvents struct {
	mu     sync.Mutex
	values []string
}

func (e *shutdownEvents) add(value string) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.values = append(e.values, value)
}
func (e *shutdownEvents) snapshot() []string {
	e.mu.Lock()
	defer e.mu.Unlock()
	return append([]string(nil), e.values...)
}

type shutdownListener struct {
	conn                                        net.Conn
	failure, closed, closeEntered, closeRelease chan struct{}
	acceptEntered, acceptRelease                chan struct{}
	errorEntered, errorRelease                  chan struct{}
	closeReturned                               chan struct{}
	errorOnce, closeOnce, closeReturnedOnce     sync.Once
	events                                      *shutdownEvents
}

func (l *shutdownListener) Accept() (net.Conn, error) {
	if l.conn != nil {
		if l.acceptEntered != nil {
			close(l.acceptEntered)
			<-l.acceptRelease
		}
		conn := l.conn
		l.conn = nil
		return conn, nil
	}
	l.errorOnce.Do(func() {
		if l.errorEntered != nil { close(l.errorEntered) }
		if l.errorRelease != nil { <-l.errorRelease }
	})
	select {
	case <-l.failure:
	case <-l.closed:
	}
	return nil, errors.New("private listener failure")
}
func (l *shutdownListener) Close() error {
	defer func() { if l.closeReturned != nil { l.closeReturnedOnce.Do(func() { close(l.closeReturned) }) } }()
	l.events.add("listener-close")
	l.closeOnce.Do(func() { close(l.closed); close(l.closeEntered) })
	<-l.closeRelease
	return nil
}
func (l *shutdownListener) Addr() net.Addr { return &net.TCPAddr{} }

type shutdownEngine struct {
	listener         net.Listener
	events           *shutdownEvents
	entered, release chan struct{}
	returned         chan struct{}
	err              error
}

func (e *shutdownEngine) Start(context.Context, EngineConfig, []byte) (RedemptionReceipt, error) {
	return RedemptionReceipt{true, true, true}, nil
}
func (e *shutdownEngine) Listen(string) (net.Listener, error) { return e.listener, nil }
func (e *shutdownEngine) Close() error {
	defer func() { if e.returned != nil { close(e.returned) } }()
	e.events.add("engine-close")
	close(e.entered)
	<-e.release
	return e.err
}
func awaitShutdown(t *testing.T, done <-chan struct{}, message string) {
	t.Helper()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal(message)
	}
}

func TestShutdownAcceptCallerYieldsToStopOwner(t *testing.T) {
	if os.Getenv("HAPPYRANCH_SHUTDOWN_HELPER") != "1" {
		// A pre-fix wait cycle must fail its assertion and leave no blocked test
		// goroutines/resources in sibling repetitions. Own and reap the helper.
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		command := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestShutdownAcceptCallerYieldsToStopOwner$", "-test.count=1", "-test.timeout=4s")
		command.Env = append(os.Environ(), "HAPPYRANCH_SHUTDOWN_HELPER=1")
		output, err := command.CombinedOutput()
		if err != nil {
			t.Fatalf("accept/Stop coordination helper failed: %v\n%s", err, output)
		}
		return
	}
	events := &shutdownEvents{}
	probe, probePeer := net.Pipe()
	t.Cleanup(func() { _ = probe.Close(); _ = probePeer.Close() })
	l := &shutdownListener{failure: make(chan struct{}), closed: make(chan struct{}), closeEntered: make(chan struct{}), closeRelease: make(chan struct{}), closeReturned: make(chan struct{}), errorEntered: make(chan struct{}), errorRelease: make(chan struct{}), events: events}
	e := &shutdownEngine{listener: l, events: events, entered: make(chan struct{}), release: make(chan struct{}), returned: make(chan struct{})}

	releaseError := releaseSignal(t, l.errorRelease)
	releaseListener := releaseSignal(t, l.closeRelease)
	releaseEngine := releaseSignal(t, e.release)
	s := New(validConfig(t), e, dialFunc(func(context.Context, string, string) (net.Conn, error) { return probe, nil }))
	results := make(chan error, 8)
	launched, received := 0, 0
	receive := func() error { err := waitResult(t, results); received++; return err }
	acceptDone := make(chan struct{})
	observerLaunched, observerJoined := false, false
	joinAccept := func() { awaitShutdown(t, acceptDone, "actual accept registration did not drain"); observerJoined = true }
	t.Cleanup(func() {
		releaseError(); releaseListener(); releaseEngine()
		_ = probe.Close(); _ = probePeer.Close()
		if launched == 0 { launched++; go func() { results <- s.Stop() }() }
		var observer <-chan struct{}
		if observerLaunched && !observerJoined { observer = acceptDone }
		timer := time.NewTimer(testWaitTimeout)
		defer timer.Stop()
		for received < launched || observer != nil {
			select {
			case <-results: received++
			case <-observer: observerJoined = true; observer = nil
			case <-timer.C:
				t.Errorf("cleanup incomplete: Stop results remaining=%d observer pending=%t", launched-received, observer != nil)
				return
			}
		}
	})
	if err := s.Start(context.Background()); err != nil { t.Fatal(err) }
	awaitShutdown(t, l.errorEntered, "actual Start-owned Accept did not enter")
	observerLaunched = true
	go func() { s.acceptWG.Wait(); close(acceptDone) }()
	// Elect the deliberate Stop owner before releasing the actual Accept error.
	launched++
	go func() { results <- s.Stop() }()
	awaitShutdown(t, l.closeEntered, "Stop did not acquire listener-first teardown")
	for launched < cap(results) { launched++; go func() { results <- s.Stop() }() }
	releaseError()
	// The real accept owner must finish while Listener.Close is still held.
	joinAccept()
	select { case <-e.entered: t.Fatal("engine closed while listener method held"); default: }
	releaseListener()
	awaitShutdown(t, l.closeReturned, "Listener.Close did not return")
	awaitShutdown(t, e.entered, "engine close did not follow actual accept drain")
	select {
	case err := <-results:
		received++
		t.Fatalf("Stop returned before Engine.Close completion: %v", err)
	default:
	}
	releaseEngine()
	awaitShutdown(t, e.returned, "Engine.Close did not return")
	for received < launched { if err := receive(); err != nil { t.Fatalf("Stop=%v want=%v", err, nil) } }
	if err := s.Stop(); err != nil { t.Fatalf("repeat Stop=%v want=%v", err, nil) }
	if got := strings.Join(events.snapshot(), ","); got != "listener-close,engine-close" { t.Fatalf("teardown=%s", got) }
}

func TestStopJoinsAcceptOwnerAfterTeardown(t *testing.T) {
	events := &shutdownEvents{}
	probe, probePeer := net.Pipe()
	t.Cleanup(func() { _ = probe.Close(); _ = probePeer.Close() })
	l := &shutdownListener{failure: make(chan struct{}), closed: make(chan struct{}), closeEntered: make(chan struct{}), closeRelease: make(chan struct{}), closeReturned: make(chan struct{}), errorEntered: make(chan struct{}), errorRelease: make(chan struct{}), events: events}
	e := &shutdownEngine{listener: l, events: events, entered: make(chan struct{}), release: make(chan struct{}), returned: make(chan struct{})}
	e.err = errors.New("private engine failure")
	releaseError := releaseSignal(t, l.errorRelease)
	releaseListener := releaseSignal(t, l.closeRelease)
	releaseEngine := releaseSignal(t, e.release)
	s := New(validConfig(t), e, dialFunc(func(context.Context, string, string) (net.Conn, error) { return probe, nil }))
	results := make(chan error, 8)
	launched, received := 0, 0
	receive := func() error { err := waitResult(t, results); received++; return err }
	acceptDone := make(chan struct{})
	observerLaunched, observerJoined := false, false
	joinAccept := func() { awaitShutdown(t, acceptDone, "actual accept registration did not drain"); observerJoined = true }
	t.Cleanup(func() {
		releaseError(); releaseListener(); releaseEngine()
		_ = probe.Close(); _ = probePeer.Close()
		if launched == 0 { launched++; go func() { results <- s.Stop() }() }
		var observer <-chan struct{}
		if observerLaunched && !observerJoined { observer = acceptDone }
		timer := time.NewTimer(testWaitTimeout)
		defer timer.Stop()
		for received < launched || observer != nil {
			select {
			case <-results: received++
			case <-observer: observerJoined = true; observer = nil
			case <-timer.C:
				t.Errorf("cleanup incomplete: Stop results remaining=%d observer pending=%t", launched-received, observer != nil)
				return
			}
		}
	})
	if err := s.Start(context.Background()); err != nil { t.Fatal(err) }
	awaitShutdown(t, l.errorEntered, "actual Start-owned Accept did not enter")
	observerLaunched = true
	go func() { s.acceptWG.Wait(); close(acceptDone) }()
	// Elect the deliberate Stop owner before releasing the actual Accept error.
	launched++
	go func() { results <- s.Stop() }()
	awaitShutdown(t, l.closeEntered, "Stop did not acquire listener-first teardown")
	for launched < cap(results) { launched++; go func() { results <- s.Stop() }() }
	releaseListener()
	awaitShutdown(t, l.closeReturned, "Listener.Close did not return")
	// Close may return, but actual Accept and its registration remain held.
	select {
	case <-e.entered: t.Fatal("engine closed before Accept returned")
	case err := <-results: received++; t.Fatalf("Stop returned before Accept: %v", err)
	case <-time.After(20 * time.Millisecond):
	}
	select { case <-acceptDone: observerJoined = true; t.Fatal("Accept registration drained before release"); default: }
	releaseError()
	joinAccept()
	awaitShutdown(t, e.entered, "engine close did not follow actual accept drain")
	select {
	case err := <-results:
		received++
		t.Fatalf("Stop returned before Engine.Close completion: %v", err)
	default:
	}
	releaseEngine()
	awaitShutdown(t, e.returned, "Engine.Close did not return")
	for received < launched { if err := receive(); err != ErrEngine { t.Fatalf("Stop=%v want=%v", err, ErrEngine) } }
	if err := s.Stop(); err != ErrEngine { t.Fatalf("repeat Stop=%v want=%v", err, ErrEngine) }
	if got := strings.Join(events.snapshot(), ","); got != "listener-close,engine-close" { t.Fatalf("teardown=%s", got) }
}

func TestStopRejectsConnectionReturnedAfterListenerClose(t *testing.T) {
	events := &shutdownEvents{}
	probe, probePeer := net.Pipe()
	t.Cleanup(func() { _ = probe.Close(); _ = probePeer.Close() })
	tail, inbound := net.Pipe()
	t.Cleanup(func() { _ = tail.Close(); _ = inbound.Close() })
	l := &shutdownListener{conn: inbound, failure: make(chan struct{}), closed: make(chan struct{}), closeEntered: make(chan struct{}), closeRelease: make(chan struct{}), acceptEntered: make(chan struct{}), acceptRelease: make(chan struct{}), events: events}
	close(l.closeRelease)
	e := &shutdownEngine{listener: l, events: events, entered: make(chan struct{}), release: make(chan struct{})}
	close(e.release)
	var dialMu sync.Mutex
	dials := 0
	s := New(validConfig(t), e, dialFunc(func(context.Context, string, string) (net.Conn, error) {
		dialMu.Lock()
		defer dialMu.Unlock()
		dials++
		if dials != 1 {
			return nil, errors.New("post-stop dial")
		}
		return probe, nil
	}))
	release := releaseSignal(t, l.acceptRelease)
	results := make(chan error, 8)
	launched, received := 0, 0
	receive := func() error { err := waitResult(t, results); received++; return err }
	proxyDone := make(chan struct{})
	observerLaunched, observerJoined := false, false
	joinProxy := func() { awaitShutdown(t, proxyDone, "late-accept proxy observation did not settle"); observerJoined = true }
	t.Cleanup(func() {
		release()
		for _, c := range []net.Conn{probe, probePeer, tail, inbound} { _ = c.Close() }
		if launched == 0 { launched++; go func() { results <- s.Stop() }() }
		var observer <-chan struct{}
		if observerLaunched && !observerJoined { observer = proxyDone }
		timer := time.NewTimer(testWaitTimeout)
		defer timer.Stop()
		for received < launched || observer != nil {
			select {
			case <-results: received++
			case <-observer: observerJoined = true; observer = nil
			case <-timer.C:
				t.Errorf("cleanup incomplete: Stop results remaining=%d observer pending=%t", launched-received, observer != nil)
				return
			}
		}
	})
	if err := s.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	awaitShutdown(t, l.acceptEntered, "Accept did not begin")
	for launched < cap(results) { launched++; go func() { results <- s.Stop() }() }
	awaitShutdown(t, l.closeEntered, "Stop did not close listener first")
	select {
	case <-e.entered:
		t.Fatal("engine closed before pending Accept resolved")
	case <-time.After(20 * time.Millisecond):
	}
	release()
	for received < launched { if err := receive(); err != nil { t.Fatalf("Stop=%v", err) } }
	observerLaunched = true
	go func() { s.proxyWG.Wait(); close(proxyDone) }()
	joinProxy()
	dialMu.Lock()
	gotDials := dials
	dialMu.Unlock()
	if gotDials != 1 {
		t.Fatalf("post-stop connection admitted: dials=%d, want probe only", gotDials)
	}
	_ = tail.SetReadDeadline(time.Now().Add(time.Second))
	if _, err := tail.Read(make([]byte, 1)); !errors.Is(err, io.EOF) {
		t.Fatalf("late connection was not closed: %v", err)
	}
	if got := strings.Join(events.snapshot(), ","); got != "listener-close,engine-close" {
		t.Fatalf("teardown = %s", got)
	}
}

func TestShutdownOwnersDrainProxiesAndAgreeOnStopError(t *testing.T) {
	for _, owner := range []string{"stop", "listener"} {
		for _, proxy := range []string{"active", "inflight"} {
			for _, fail := range []bool{false, true} {
				t.Run(fmt.Sprintf("%s/%s/close-failure-%t", owner, proxy, fail), func(t *testing.T) {
					events := &shutdownEvents{}
					probe, probePeer := net.Pipe()
					t.Cleanup(func() { _ = probe.Close(); _ = probePeer.Close() })
					tail, inbound := net.Pipe()
					t.Cleanup(func() { _ = tail.Close(); _ = inbound.Close() })
					outbound, connector := net.Pipe()
					t.Cleanup(func() { _ = outbound.Close(); _ = connector.Close() })
					l := &shutdownListener{conn: inbound, failure: make(chan struct{}), closed: make(chan struct{}), closeEntered: make(chan struct{}), closeRelease: make(chan struct{}), closeReturned: make(chan struct{}), events: events}
					e := &shutdownEngine{listener: l, events: events, entered: make(chan struct{}), release: make(chan struct{}), returned: make(chan struct{})}
					if fail {
						e.err = errors.New("private engine failure")
					}
					dialEntered, dialRelease := make(chan struct{}), make(chan struct{})
					var dials int
					dial := dialFunc(func(context.Context, string, string) (net.Conn, error) {
						dials++ // probe is synchronous; exactly one proxy follows it
						if dials == 1 {
							return probe, nil
						}
						close(dialEntered)
						<-dialRelease
						return outbound, nil
					})
					s := New(validConfig(t), e, dial)
					releaseDial := releaseSignal(t, dialRelease)
					releaseListener := releaseSignal(t, l.closeRelease)
					releaseEngine := releaseSignal(t, e.release)
					results := make(chan error, 8)
					launched, received := 0, 0
					receive := func() error { err := waitResult(t, results); received++; return err }
					acceptDone := make(chan struct{})
					observerLaunched, observerJoined := false, false
					joinAccept := func() { awaitShutdown(t, acceptDone, "Stop returned with undrained accept loop"); observerJoined = true }
					t.Cleanup(func() {
						releaseListener(); releaseDial(); releaseEngine()
						for _, c := range []net.Conn{probe, probePeer, tail, inbound, outbound, connector} { _ = c.Close() }
						if launched == 0 { launched++; go func() { results <- s.Stop() }() }
						var observer <-chan struct{}
						if observerLaunched && !observerJoined { observer = acceptDone }
						timer := time.NewTimer(testWaitTimeout)
						defer timer.Stop()
						for received < launched || observer != nil {
							select {
							case <-results: received++
							case <-observer: observerJoined = true; observer = nil
							case <-timer.C:
								t.Errorf("cleanup incomplete: Stop results remaining=%d observer pending=%t", launched-received, observer != nil)
								return
							}
						}
					})
					if err := s.Start(context.Background()); err != nil {
						t.Fatal(err)
					}
					_ = probePeer.Close()
					awaitShutdown(t, dialEntered, "proxy did not enter Dial")
					if proxy == "active" {
						releaseDial()
						deadline := time.Now().Add(time.Second)
						for {
							s.mu.Lock()
							active := len(s.active)
							s.mu.Unlock()
							if active == 2 {
								break
							}
							if time.Now().After(deadline) {
								t.Fatal("proxy did not register active flows")
							}
							time.Sleep(time.Millisecond)
						}
					}
					if owner == "listener" {
						close(l.failure)
						awaitShutdown(t, l.closeEntered, "unexpected listener error did not own teardown")
					}
					for launched < cap(results) { launched++; go func() { results <- s.Stop() }() }
					awaitShutdown(t, l.closeEntered, "listener-first teardown did not begin")
					if owner == "stop" {
						close(l.failure)
					}
					releaseListener()
					awaitShutdown(t, l.closeReturned, "Listener.Close did not return")
					if proxy == "inflight" {
						select {
						case <-e.entered:
							t.Fatal("engine closed before in-flight proxy drain")
						case <-time.After(20 * time.Millisecond):
						}
						releaseDial()
					}
					awaitShutdown(t, e.entered, "engine close did not follow proxy drain")
					select {
					case err := <-results:
						received++
						t.Fatalf("Stop returned before engine completion: %v", err)
					default:
					}
					s.mu.Lock()
					active := len(s.active)
					s.mu.Unlock()
					if active != 0 {
						t.Fatalf("active connections after drain = %d", active)
					}
					for _, c := range []net.Conn{tail, connector} {
						_ = c.SetReadDeadline(time.Now().Add(time.Second))
						if _, err := c.Read(make([]byte, 1)); !errors.Is(err, io.EOF) {
							t.Fatalf("drained connection read = %v", err)
						}
					}
					releaseEngine()
					awaitShutdown(t, e.returned, "Engine.Close did not return")
					var want error
					if owner == "listener" {
						want = ErrListener
					} else if fail {
						want = ErrEngine
					}
					for received < launched { if err := receive(); err != want { t.Fatalf("Stop=%v want=%v", err, want) } }
					if err := s.Stop(); err != want {
						t.Fatalf("repeated Stop = %v, want %v", err, want)
					}
					if err := s.Start(context.Background()); !errors.Is(err, ErrListener) {
						t.Fatalf("post-stop Start = %v", err)
					}
					if got := strings.Join(events.snapshot(), ","); got != "listener-close,engine-close" {
						t.Fatalf("teardown = %s", got)
					}
					observerLaunched = true
					go func() { s.acceptWG.Wait(); close(acceptDone) }()
					joinAccept()
				})
			}
		}
	}
}

func TestShippingFailureMatrixUsesStableCategories(t *testing.T) {
	tests := []struct {
		name   string
		engine func(*[]string, net.Listener) *fakeEngine
		dialer func(net.Conn) Dialer
		want   error
	}{
		{"engine-start", func(e *[]string, l net.Listener) *fakeEngine {
			return &fakeEngine{startErr: errors.New("secret"), events: e}
		}, func(net.Conn) Dialer { return &net.Dialer{} }, ErrEngineStart},
		{"listen", func(e *[]string, l net.Listener) *fakeEngine {
			return &fakeEngine{receipt: RedemptionReceipt{true, true, true}, listenErr: errors.New("secret"), events: e}
		}, func(c net.Conn) Dialer {
			return dialFunc(func(context.Context, string, string) (net.Conn, error) { return c, nil })
		}, ErrListener},
		{"probe-close", func(e *[]string, l net.Listener) *fakeEngine {
			return &fakeEngine{receipt: RedemptionReceipt{true, true, true}, events: e}
		}, func(net.Conn) Dialer {
			return dialFunc(func(context.Context, string, string) (net.Conn, error) { return &errorCloseConn{}, nil })
		}, ErrConnector},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			cfg := validConfig(t)
			events := []string{}
			probeClient, probeServer := net.Pipe()
			defer probeServer.Close()
			err := New(cfg, tc.engine(&events, nil), tc.dialer(probeClient)).Start(context.Background())
			if !errors.Is(err, tc.want) || strings.Contains(err.Error(), "secret") {
				t.Fatalf("error = %v events=%v", err, events)
			}
			if contains(events, "listen") && tc.want != ErrListener {
				t.Fatalf("unexpected listener admission: %v", events)
			}
		})
	}
	for _, reason := range []string{"unclassified", "context_cancelled", "deadline_exceeded", "up_backend_error", "up_no_ip", "up_error_unclassified", "up_status_unavailable", "up_not_running", "peer_status_error", "peer_status_unavailable", "peer_not_running", "peer_wait_deadline", "expected_peer_missing", "SECRET_CANARY"} {
		name := reason
		if reason == "SECRET_CANARY" {
			name = "invalid"
		}
		for _, shape := range []string{"value", "pointer", "wrapped"} {
			t.Run("network/"+name+"/"+shape, func(t *testing.T) {
				cfg := validConfig(t)
				events := []string{}
				var failure error = NetworkJoinFailure{SubReason: reason}
				if shape == "pointer" {
					failure = &NetworkJoinFailure{SubReason: reason}
				}
				if shape == "wrapped" {
					failure = fmt.Errorf("TOKEN_CANARY: %w", failure)
				}
				svc := New(cfg, &fakeEngine{startErr: failure, events: &events}, &net.Dialer{})
				err := svc.Start(context.Background())
				want := reason
				if reason == "SECRET_CANARY" {
					want = "unclassified"
				}
				assertNetworkReason(t, err, want)
				_ = svc.Stop()
				if strings.Join(events, ",") != "start,engine-close" {
					t.Fatalf("ordering=%v", events)
				}
				if _, err := os.Stat(cfg.CredentialFile); err != nil {
					t.Fatal("failed network receipt consumed credential")
				}
				if _, err := os.Stat(filepath.Join(cfg.StateDir, consumedMarker)); !os.IsNotExist(err) {
					t.Fatal("failed network receipt committed")
				}
			})
		}
	}
	for _, wrapped := range []bool{false, true} {
		t.Run(fmt.Sprintf("bare/%t", wrapped), func(t *testing.T) {
			cfg := validConfig(t)
			events := []string{}
			var failure error = ErrNetworkJoin
			if wrapped {
				failure = fmt.Errorf("TOKEN_CANARY: %w", failure)
			}
			svc := New(cfg, &fakeEngine{startErr: failure, events: &events}, &net.Dialer{})
			assertNetworkReason(t, svc.Start(context.Background()), "unclassified")
			_ = svc.Stop()
			if strings.Join(events, ",") != "start,engine-close" {
				t.Fatal("close ordering changed")
			}
		})
	}
	t.Run("incomplete-peer-receipt", func(t *testing.T) {
		cfg := validConfig(t)
		events := []string{}
		svc := New(cfg, &fakeEngine{receipt: RedemptionReceipt{true, true, false}, events: &events}, &net.Dialer{})
		assertNetworkReason(t, svc.Start(context.Background()), "expected_peer_missing")
		_ = svc.Stop()
		if strings.Join(events, ",") != "start,engine-close" {
			t.Fatal("incomplete peer admitted")
		}
		if _, err := os.Stat(cfg.CredentialFile); err != nil {
			t.Fatal("incomplete peer consumed credential")
		}
	})

}

func TestAcceptedConnectionDialAndCopyFailuresCloseResources(t *testing.T) {
	for _, tc := range []struct {
		name       string
		proxyDial  func() (net.Conn, error)
		wantClosed <-chan struct{}
	}{
		{name: "dial", proxyDial: func() (net.Conn, error) { return nil, errors.New("dial secret") }},
		func() struct {
			name       string
			proxyDial  func() (net.Conn, error)
			wantClosed <-chan struct{}
		} {
			out := newFailureConn(false)
			return struct {
				name       string
				proxyDial  func() (net.Conn, error)
				wantClosed <-chan struct{}
			}{"copy", func() (net.Conn, error) { return out, nil }, out.closed}
		}(),
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := validConfig(t)
			events := []string{}
			in := newFailureConn(tc.name == "copy")
			l := &oneListener{conn: in, blockAfter: true, closed: make(chan struct{}), accepted: make(chan struct{}), events: &events}
			probeClient, probeServer := net.Pipe()
			defer probeServer.Close()
			dials := 0
			d := dialFunc(func(context.Context, string, string) (net.Conn, error) {
				dials++
				if dials == 1 {
					return probeClient, nil
				}
				return tc.proxyDial()
			})
			s := New(cfg, &fakeEngine{listener: l, receipt: RedemptionReceipt{true, true, true}, events: &events}, d)
			if err := s.Start(context.Background()); err != nil {
				t.Fatal(err)
			}
			select {
			case <-in.closed:
			case <-time.After(time.Second):
				t.Fatal("inbound connection was not closed")
			}
			if tc.wantClosed != nil {
				select {
				case <-tc.wantClosed:
				case <-time.After(time.Second):
					t.Fatal("outbound connection was not closed")
				}
			}
			if err := s.Stop(); err != nil {
				t.Fatal(err)
			}
		})
	}
}

func TestStopFailureWithPartialResourcesIsListenerFirstAndRedacted(t *testing.T) {
	cfg := validConfig(t)
	events := []string{}
	l := &oneListener{blockAfter: true, closed: make(chan struct{}), accepted: make(chan struct{}), events: &events, closeErr: errors.New("listener secret")}
	probeClient, probeServer := net.Pipe()
	defer probeServer.Close()
	e := &fakeEngine{listener: l, receipt: RedemptionReceipt{true, true, true}, closeErr: errors.New("engine secret"), events: &events}
	s := New(cfg, e, dialFunc(func(context.Context, string, string) (net.Conn, error) { return probeClient, nil }))
	if err := s.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	err := s.Stop()
	if !errors.Is(err, ErrEngine) || strings.Contains(err.Error(), "secret") {
		t.Fatalf("Stop error = %v", err)
	}
	if strings.Join(events[len(events)-2:], ",") != "listener-close,engine-close" {
		t.Fatalf("events = %v", events)
	}
}

type failureConn struct {
	failRead bool
	closed   chan struct{}
	once     sync.Once
}

func newFailureConn(failRead bool) *failureConn {
	return &failureConn{failRead: failRead, closed: make(chan struct{})}
}
func (c *failureConn) Read([]byte) (int, error) {
	if c.failRead {
		return 0, errors.New("copy secret")
	}
	<-c.closed
	return 0, io.EOF
}
func (*failureConn) Write(p []byte) (int, error)      { return len(p), nil }
func (c *failureConn) Close() error                   { c.once.Do(func() { close(c.closed) }); return nil }
func (*failureConn) LocalAddr() net.Addr              { return fakeAddr("local") }
func (*failureConn) RemoteAddr() net.Addr             { return fakeAddr("remote") }
func (*failureConn) SetDeadline(time.Time) error      { return nil }
func (*failureConn) SetReadDeadline(time.Time) error  { return nil }
func (*failureConn) SetWriteDeadline(time.Time) error { return nil }

type errorCloseConn struct{ net.Conn }

func (*errorCloseConn) Close() error { return errors.New("hostile secret") }

type dialFunc func(context.Context, string, string) (net.Conn, error)

func (f dialFunc) DialContext(c context.Context, n, a string) (net.Conn, error) { return f(c, n, a) }

type oneListener struct {
	conn       net.Conn
	acceptErr  error
	accepted   chan struct{}
	events     *[]string
	once       sync.Once
	closeErr   error
	blockAfter bool
	closed     chan struct{}
	closeOnce  sync.Once
}

func (l *oneListener) Accept() (net.Conn, error) {
	l.once.Do(func() { close(l.accepted) })
	if l.conn != nil {
		c := l.conn
		l.conn = nil
		return c, nil
	}
	if l.acceptErr != nil {
		return nil, l.acceptErr
	}
	if l.blockAfter {
		<-l.closed
		return nil, net.ErrClosed
	}
	select {}
}
func (l *oneListener) Close() error {
	if l.events != nil {
		*l.events = append(*l.events, "listener-close")
	}
	if l.closed != nil {
		l.closeOnce.Do(func() { close(l.closed) })
	}
	return l.closeErr
}
func (l *oneListener) Addr() net.Addr { return fakeAddr("") }

type fakeAddr string

func (a fakeAddr) Network() string { return "tcp" }
func (a fakeAddr) String() string  { return string(a) }
func contains(xs []string, s string) bool {
	for _, x := range xs {
		if x == s {
			return true
		}
	}
	return false
}
func count(xs []string, s string) int {
	n := 0
	for _, x := range xs {
		if x == s {
			n++
		}
	}
	return n
}

var _ = time.Second

// Connector cases use the shipping Engine/Dialer/Listener boundaries. Budgets
// use real context deadlines; no production clock, sleeper or retry seam.
type countedProbe struct {
	net.Conn
	closes   int
	closeErr error
	onClose  func()
}

func (p *countedProbe) Close() error {
	p.closes++
	if p.onClose != nil {
		p.onClose()
	}
	return p.closeErr
}

type timeoutLookingError struct{}

func (timeoutLookingError) Error() string   { return "dial timeout: connection refused" }
func (timeoutLookingError) Timeout() bool   { return true }
func (timeoutLookingError) Temporary() bool { return true }

func connectorFixture(t *testing.T, dial Dialer) (*Sidecar, *fakeEngine, *[]string) {
	t.Helper()
	events := []string{}
	listener := &oneListener{blockAfter: true, closed: make(chan struct{}), accepted: make(chan struct{}), events: &events}
	engine := &fakeEngine{listener: listener, receipt: RedemptionReceipt{true, true, true}, events: &events}
	return New(validConfig(t), engine, dial), engine, &events
}

func TestConnectorImmediateAndDelayedSuccessAdmitExactlyOnce(t *testing.T) {
	for _, first := range []string{"immediate", "refused", "beyond-old-cap", "attempt-timeout"} {
		t.Run(first, func(t *testing.T) {
			probes := []*countedProbe{}
			calls := 0
			var svc *Sidecar
			var key []byte
			var engineDeadline time.Time
			dial := dialFunc(func(ctx context.Context, network, address string) (net.Conn, error) {
				calls++
				if network != "tcp" || address != svc.cfg.ConnectorAddr {
					t.Fatal("probe target changed")
				}
				deadline, ok := ctx.Deadline()
				if !ok || deadline.After(time.Now().Add(time.Second)) || deadline.After(engineDeadline) {
					t.Fatal("dial budget escaped enclosing context")
				}
				for _, b := range key {
					if b != 0 {
						t.Fatal("engine credential buffer retained")
					}
				}
				if _, err := os.Stat(svc.cfg.CredentialFile); !os.IsNotExist(err) {
					t.Fatal("credential commit moved after connector")
				}
				if _, err := os.Stat(filepath.Join(svc.cfg.StateDir, consumedMarker)); err != nil {
					t.Fatal("missing pre-probe consumption commit")
				}
				probe := &countedProbe{}
				probes = append(probes, probe)
				if calls == 1 && first == "refused" || calls <= 10 && first == "beyond-old-cap" {
					return probe, fmt.Errorf("private: %w", syscall.ECONNREFUSED)
				}
				if calls == 1 && first == "attempt-timeout" {
					<-ctx.Done()
					return probe, fmt.Errorf("private: %w", ctx.Err())
				}
				return probe, nil
			})
			s, engine, events := connectorFixture(t, dial)
			svc = s
			entry := time.Now()
			engine.startFn = func(ctx context.Context, _ EngineConfig, credential []byte) (RedemptionReceipt, error) {
				key = credential
				engineDeadline, _ = ctx.Deadline()
				if engineDeadline.Before(entry.Add(75*time.Second)) || engineDeadline.After(time.Now().Add(75*time.Second)) {
					t.Fatal("total deadline is not anchored at Start entry")
				}
				return RedemptionReceipt{true, true, true}, nil
			}
			if err := svc.Start(context.Background()); err != nil {
				t.Fatal(err)
			}
			want := 2
			if first == "immediate" {
				want = 1
			}
			if first == "beyond-old-cap" {
				want = 11
				if elapsed := time.Since(entry); elapsed < 2*time.Second || elapsed >= connectorBudget {
					t.Fatalf("delayed acceptance escaped phase: %s", elapsed)
				}
			}
			if calls != want || count(*events, "start") != 1 || count(*events, "listen") != 1 {
				t.Fatalf("calls=%d events=%v", calls, *events)
			}
			for _, probe := range probes {
				if probe.closes != 1 {
					t.Fatalf("owned connection closes=%d", probe.closes)
				}
			}
			if err := svc.Stop(); err != nil {
				t.Fatal(err)
			}
			if count(*events, "engine-close") != 1 || count(*events, "listener-close") != 1 {
				t.Fatal(*events)
			}
		})
	}
}

func TestConnectorTerminalResultsNeverRetryOrListen(t *testing.T) {
	for _, outcome := range []string{"unclassified", "timeout-text-and-net-error", "deadline-identity-before-expiry", "nil-success", "success-close-failure", "refused-close-failure", "connection-with-terminal-error"} {
		t.Run(outcome, func(t *testing.T) {
			probe := &countedProbe{}
			calls := 0
			svc, _, events := connectorFixture(t, dialFunc(func(context.Context, string, string) (net.Conn, error) {
				calls++
				switch outcome {
				case "unclassified":
					return nil, errors.New("private failure")
				case "timeout-text-and-net-error":
					return nil, timeoutLookingError{}
				case "deadline-identity-before-expiry":
					return nil, context.DeadlineExceeded
				case "nil-success":
					return nil, nil
				case "success-close-failure":
					probe.closeErr = errors.New("private close")
					return probe, nil
				case "refused-close-failure":
					probe.closeErr = errors.New("private close")
					return probe, syscall.ECONNREFUSED
				default:
					return probe, errors.New("private failure")
				}
			}))
			if err := svc.Start(context.Background()); err != ErrConnector {
				t.Fatalf("error=%v", err)
			}
			if calls != 1 || contains(*events, "listen") {
				t.Fatalf("calls=%d events=%v", calls, *events)
			}
			wantCloses := 0
			if strings.Contains(outcome, "close-failure") || outcome == "connection-with-terminal-error" {
				wantCloses = 1
			}
			if probe.closes != wantCloses {
				t.Fatalf("closes=%d want=%d", probe.closes, wantCloses)
			}
			_ = svc.Stop()
			if count(*events, "engine-close") != 1 {
				t.Fatal(*events)
			}
		})
	}
}

func TestConnectorPersistentRefusalExhaustsPhaseDeadline(t *testing.T) {
	calls := 0
	probes := []*countedProbe{}
	var last time.Time
	svc, _, events := connectorFixture(t, dialFunc(func(context.Context, string, string) (net.Conn, error) {
		if calls > 0 && time.Since(last) < 200*time.Millisecond {
			t.Fatal("retry spacing shortened")
		}
		last = time.Now()
		calls++
		probe := &countedProbe{}
		probes = append(probes, probe)
		return probe, fmt.Errorf("private: %w", syscall.ECONNREFUSED)
	}))
	entry := time.Now()
	if svc.Start(context.Background()) != ErrConnector || contains(*events, "listen") {
		t.Fatalf("calls=%d events=%v", calls, *events)
	}
	if elapsed := time.Since(entry); elapsed < connectorBudget || elapsed > connectorBudget+2*time.Second || calls <= 9 {
		t.Fatalf("premature or unbounded refusal: calls=%d elapsed=%s", calls, elapsed)
	}
	for _, probe := range probes {
		if probe.closes != 1 {
			t.Fatal("connection+error was not closed exactly once")
		}
	}
	_ = svc.Stop()
	if count(*events, "start") != 1 || count(*events, "engine-close") != 1 {
		t.Fatal(*events)
	}
}

func TestConnectorPhaseClipsPerAttemptTimeoutAtTenSeconds(t *testing.T) {
	calls := 0
	var firstEntry, firstDeadline, lastDeadline time.Time
	svc, _, events := connectorFixture(t, dialFunc(func(ctx context.Context, _, _ string) (net.Conn, error) {
		calls++
		if calls == 1 {
			firstEntry = time.Now()
			firstDeadline, _ = ctx.Deadline()
		}
		lastDeadline, _ = ctx.Deadline()
		if lastDeadline.After(time.Now().Add(time.Second)) {
			t.Fatal("attempt exceeds one second")
		}
		<-ctx.Done()
		return nil, ctx.Err()
	}))
	entry := time.Now()
	if svc.Start(context.Background()) != ErrConnector || contains(*events, "listen") {
		t.Fatal(*events)
	}
	if calls != 9 || lastDeadline.After(firstEntry.Add(10*time.Second)) || !lastDeadline.Before(firstDeadline.Add(8*(time.Second+200*time.Millisecond))) {
		t.Fatalf("calls=%d first=%s last=%s", calls, firstDeadline, lastDeadline)
	}
	if elapsed := time.Since(entry); elapsed < 10*time.Second || elapsed > 12*time.Second {
		t.Fatalf("phase elapsed=%s", elapsed)
	}
	_ = svc.Stop()
}

func TestConnectorCallerCancellationRefusesBeforeDuringDialAndSpacing(t *testing.T) {
	for _, stage := range []string{"before", "dial", "spacing", "close"} {
		t.Run(stage, func(t *testing.T) {
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if stage == "before" {
				cancel()
			}
			calls := 0
			probe := &countedProbe{}
			if stage == "close" {
				probe.onClose = cancel
			}
			svc, _, events := connectorFixture(t, dialFunc(func(dialCtx context.Context, _, _ string) (net.Conn, error) {
				calls++
				switch stage {
				case "dial":
					cancel()
					<-dialCtx.Done()
					return probe, context.Canceled
				case "spacing":
					go func() { time.Sleep(50 * time.Millisecond); cancel() }()
					return probe, syscall.ECONNREFUSED
				default:
					return probe, nil
				}
			}))
			err := svc.Start(ctx)
			if stage == "before" {
				assertNetworkReason(t, err, "context_cancelled")
				if len(*events) != 0 || calls != 0 {
					t.Fatal("cancelled caller started work")
				}
				if _, err := os.Stat(svc.cfg.CredentialFile); err != nil {
					t.Fatal("cancelled entry consumed credential")
				}
				return
			}
			if err != ErrConnector || calls != 1 || probe.closes != 1 || contains(*events, "listen") {
				t.Fatalf("error=%v calls=%d closes=%d events=%v", err, calls, probe.closes, *events)
			}
			_ = svc.Stop()
			if count(*events, "engine-close") != 1 {
				t.Fatal(*events)
			}
		})
	}
}

func TestStartEngineUsesTotalAndEarlierCallerDeadlineWithoutRenewal(t *testing.T) {
	for _, budget := range []string{"total-75s", "earlier-parent"} {
		t.Run(budget, func(t *testing.T) {
			ctx := context.Background()
			var callerDeadline time.Time
			if budget == "earlier-parent" {
				var cancel context.CancelFunc
				ctx, cancel = context.WithTimeout(ctx, 30*time.Millisecond)
				defer cancel()
				callerDeadline, _ = ctx.Deadline()
			}
			calls := 0
			svc, engine, events := connectorFixture(t, dialFunc(func(context.Context, string, string) (net.Conn, error) { calls++; return nil, nil }))
			entry := time.Now()
			engine.startFn = func(startup context.Context, _ EngineConfig, _ []byte) (RedemptionReceipt, error) {
				deadline, ok := startup.Deadline()
				if !ok || deadline.After(time.Now().Add(75*time.Second)) {
					t.Fatal("engine escaped total acceptance")
				}
				if !callerDeadline.IsZero() && deadline != callerDeadline {
					t.Fatal("engine renewed parent deadline")
				}
				if callerDeadline.IsZero() && deadline.Before(entry.Add(75*time.Second)) {
					t.Fatal("wrong total deadline")
				}
				<-startup.Done() // Actual budget consumption, including the full75s case.
				return RedemptionReceipt{true, true, true}, nil
			}
			assertNetworkReason(t, svc.Start(ctx), "deadline_exceeded")
			if calls != 0 || contains(*events, "listen") {
				t.Fatal("expired engine budget renewed connector phase")
			}
			if _, err := os.Stat(svc.cfg.CredentialFile); err != nil {
				t.Fatal("late engine committed credential")
			}
			if _, err := os.Stat(filepath.Join(svc.cfg.StateDir, consumedMarker)); !os.IsNotExist(err) {
				t.Fatal("late engine created marker")
			}
			_ = svc.Stop()
			if count(*events, "start") != 1 || count(*events, "engine-close") != 1 {
				t.Fatal(*events)
			}
		})
	}
}

func TestConnectorLateDialOrCloseSuccessCannotAdmit(t *testing.T) {
	for _, stage := range []string{"dial", "close", "attempt-dial", "attempt-close"} {
		t.Run(stage, func(t *testing.T) {
			budget := 30 * time.Millisecond
			if strings.HasPrefix(stage, "attempt-") {
				budget = 3 * time.Second
			}
			ctx, cancel := context.WithTimeout(context.Background(), budget)
			defer cancel()
			calls := 0
			probe := &countedProbe{}
			if stage == "close" {
				probe.onClose = func() { <-ctx.Done() }
			}
			svc, _, events := connectorFixture(t, dialFunc(func(dialCtx context.Context, _, _ string) (net.Conn, error) {
				calls++
				deadline, _ := dialCtx.Deadline()
				parentDeadline, _ := ctx.Deadline()
				if !strings.HasPrefix(stage, "attempt-") && deadline != parentDeadline {
					t.Fatal("dial escaped remaining total")
				}
				if stage == "dial" || stage == "attempt-dial" {
					<-dialCtx.Done()
				}
				if stage == "attempt-close" {
					probe.onClose = func() { <-dialCtx.Done() }
				}
				return probe, nil // A producer can return a resource after cancellation.
			}))
			if svc.Start(ctx) != ErrConnector || calls != 1 || probe.closes != 1 || contains(*events, "listen") {
				t.Fatalf("calls=%d closes=%d events=%v", calls, probe.closes, *events)
			}
			_ = svc.Stop()
		})
	}
}

func TestLateListenerAndListenerWithErrorCloseBeforeEngine(t *testing.T) {
	for _, outcome := range []string{"late", "resource-with-error"} {
		t.Run(outcome, func(t *testing.T) {
			ctx, cancel := context.WithTimeout(context.Background(), 30*time.Millisecond)
			defer cancel()
			probe := &countedProbe{}
			svc, engine, events := connectorFixture(t, dialFunc(func(context.Context, string, string) (net.Conn, error) { return probe, nil }))
			listener := engine.listener.(*oneListener)
			if outcome == "late" {
				engine.listenEntered, engine.listenRelease = make(chan struct{}), make(chan struct{})
				go func() { <-ctx.Done(); close(engine.listenRelease) }()
			} else {
				engine.listenErr = errors.New("private listener failure")
			}
			if svc.Start(ctx) != ErrListener {
				t.Fatal("failed or late listener admitted")
			}
			select {
			case <-listener.accepted:
				t.Fatal("failed listener accepted")
			default:
			}
			_ = svc.Stop()
			if probe.closes != 1 || strings.Join(*events, ",") != "start,listen,listener-close,engine-close" {
				t.Fatal(*events)
			}
		})
	}
}

// A context whose timer delivery is pending still has a real elapsed deadline.
type pendingDeadlineContext struct {
	context.Context
	deadline time.Time
	done     <-chan struct{}
}

func (c pendingDeadlineContext) Deadline() (time.Time, bool) { return c.deadline, true }
func (c pendingDeadlineContext) Done() <-chan struct{}       { return c.done }

func TestStartupAbsoluteDeadlineRefusesPendingTimerDelivery(t *testing.T) {
	t.Run("elapsed-entry", func(t *testing.T) {
		calls := 0
		svc, engine, events := connectorFixture(t, dialFunc(func(context.Context, string, string) (net.Conn, error) {
			calls++
			return nil, nil
		}))
		credential, err := os.ReadFile(svc.cfg.CredentialFile)
		if err != nil {
			t.Fatal(err)
		}
		ctx := pendingDeadlineContext{Context: context.Background(), deadline: time.Now().Add(-time.Nanosecond), done: make(chan struct{})}
		if ctx.Err() != nil || time.Now().Before(ctx.deadline) {
			t.Fatal("fixture must have an elapsed deadline with Err still nil")
		}
		select {
		case <-ctx.Done():
			t.Fatal("fixture delivered cancellation before Start")
		default:
		}
		assertNetworkReason(t, svc.Start(ctx), "deadline_exceeded")
		if calls != 0 || len(*events) != 0 {
			t.Fatalf("elapsed entry performed work: calls=%d events=%v", calls, *events)
		}
		select {
		case <-engine.listener.(*oneListener).accepted:
			t.Fatal("elapsed entry started serving")
		default:
		}
		retained, err := os.ReadFile(svc.cfg.CredentialFile)
		if err != nil || string(retained) != string(credential) {
			t.Fatal("elapsed entry consumed or changed credential fixture")
		}
		if _, err := os.Stat(filepath.Join(svc.cfg.StateDir, consumedMarker)); !os.IsNotExist(err) {
			t.Fatal("elapsed entry created consumption marker")
		}
	})
	t.Run("late-listener", func(t *testing.T) {
		calls := 0
		probe := &countedProbe{}
		svc, engine, events := connectorFixture(t, dialFunc(func(context.Context, string, string) (net.Conn, error) {
			calls++
			return probe, nil
		}))
		listener := engine.listener.(*oneListener)
		engine.listenEntered, engine.listenRelease = make(chan struct{}), make(chan struct{})
		ctx := pendingDeadlineContext{Context: context.Background(), deadline: time.Now().Add(time.Second), done: make(chan struct{})}
		var startup context.Context
		engine.startFn = func(engineCtx context.Context, _ EngineConfig, _ []byte) (RedemptionReceipt, error) {
			startup = engineCtx
			return engine.receipt, nil
		}
		if ctx.Err() != nil || !time.Now().Before(ctx.deadline) {
			t.Fatal("caller must initially be live")
		}
		result := make(chan error, 1)
		go func() { result <- svc.Start(ctx) }()
		// Release the existing Listen block even if a fixture assertion fails.
		t.Cleanup(func() {
			select {
			case <-engine.listenRelease:
			default:
				close(engine.listenRelease)
			}
			_ = svc.Stop()
		})
		select {
		case <-engine.listenEntered:
		case err := <-result:
			t.Fatalf("Start refused before reaching Listen: %v", err)
		case <-time.After(2 * time.Second):
			t.Fatal("Start did not reach Listen")
		}
		if !time.Now().Before(ctx.deadline) {
			t.Fatal("fixture must reach Listen before the caller deadline")
		}
		if deadline, ok := startup.Deadline(); !ok || !deadline.Equal(ctx.deadline) {
			t.Fatal("startup did not retain the earlier caller deadline")
		}
		// Await the real absolute deadline, never the caller's pending Done.
		timer := time.NewTimer(time.Until(ctx.deadline))
		defer timer.Stop()
		<-timer.C
		if ctx.Err() != nil || startup.Err() != nil || time.Now().Before(ctx.deadline) {
			t.Fatal("listener release must be elapsed with Err still nil")
		}
		select {
		case <-ctx.Done():
			t.Fatal("fixture delivered cancellation before listener return")
		default:
		}
		select {
		case <-startup.Done():
			t.Fatal("fixture delivered startup cancellation before listener return")
		default:
		}
		close(engine.listenRelease)
		if err := <-result; err != ErrListener {
			t.Fatalf("late listener admitted: %v", err)
		}
		if ctx.Err() != nil || calls != 1 || probe.closes != 1 || strings.Join(*events, ",") != "start,listen,listener-close,engine-close" {
			t.Fatalf("calls=%d probe closes=%d events=%v", calls, probe.closes, *events)
		}
		select {
		case <-listener.accepted:
			t.Fatal("late listener started serving")
		default:
		}
		if err := svc.Stop(); err != nil {
			t.Fatal(err)
		}
		if probe.closes != 1 || strings.Join(*events, ",") != "start,listen,listener-close,engine-close" {
			t.Fatal("Stop duplicated late-listener teardown", *events)
		}
	})
}

func TestConnectorConsumedCallerBudgetCannotRenewRetrySpacing(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 150*time.Millisecond)
	defer cancel()
	calls := 0
	probe := &countedProbe{}
	svc, engine, events := connectorFixture(t, dialFunc(func(context.Context, string, string) (net.Conn, error) {
		calls++
		return probe, syscall.ECONNREFUSED
	}))
	engine.startFn = func(startup context.Context, _ EngineConfig, _ []byte) (RedemptionReceipt, error) {
		timer := time.NewTimer(100 * time.Millisecond)
		defer timer.Stop()
		select {
		case <-timer.C:
		case <-startup.Done():
			return RedemptionReceipt{}, startup.Err()
		}
		return RedemptionReceipt{true, true, true}, nil
	}
	if svc.Start(ctx) != ErrConnector || calls != 1 || probe.closes != 1 || contains(*events, "listen") {
		t.Fatalf("calls=%d events=%v", calls, *events)
	}
	if ctx.Err() != context.DeadlineExceeded {
		t.Fatal("retry did not preserve consumed caller deadline")
	}
	_ = svc.Stop()
	if count(*events, "start") != 1 || count(*events, "engine-close") != 1 {
		t.Fatal(*events)
	}
}
