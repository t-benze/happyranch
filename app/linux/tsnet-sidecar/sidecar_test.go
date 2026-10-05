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
}

func (f *fakeEngine) Start(context.Context, EngineConfig, []byte) (RedemptionReceipt, error) {
	*f.events = append(*f.events, "start")
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
	for _, name := range []string{"missing", "symlink", "loose", "empty", "replay"} {
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
			case "replay":
				os.WriteFile(filepath.Join(cfg.StateDir, consumedMarker), []byte("1"), 0o600)
			}
			e := &fakeEngine{receipt: RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, events: &events}
			s := New(cfg, e, &net.Dialer{})
			err := s.Start(context.Background())
			if err == nil || strings.Contains(err.Error(), "secret") || contains(events, "listen") {
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
	dial := dialFunc(func(context.Context, string, string) (net.Conn, error) {
		c := dials[0]
		dials = dials[1:]
		return c, nil
	})
	s := New(cfg, e, dial)
	if err := s.Start(context.Background()); err != nil {
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

func TestSystemdStagedCredentialIsReadOnceAndNeverUnlinked(t *testing.T) {
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
	closeOnce                                   sync.Once
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
	select {
	case <-l.failure:
	case <-l.closed:
	}
	return nil, errors.New("private listener failure")
}
func (l *shutdownListener) Close() error {
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
	err              error
}

func (e *shutdownEngine) Start(context.Context, EngineConfig, []byte) (RedemptionReceipt, error) {
	return RedemptionReceipt{true, true, true}, nil
}
func (e *shutdownEngine) Listen(string) (net.Listener, error) { return e.listener, nil }
func (e *shutdownEngine) Close() error {
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
	// Drive the real shutdown entry with an accept error observed before Stop
	// election and delivered afterward. This isolates the stale caller at the
	// exact method boundary without a scheduler hook in acceptLoop.
	events := &shutdownEvents{}
	l := &shutdownListener{failure: make(chan struct{}), closed: make(chan struct{}), closeEntered: make(chan struct{}), closeRelease: make(chan struct{}), events: events}
	close(l.failure)
	if _, err := l.Accept(); err == nil {
		t.Fatal("unexpected listener error was not observed")
	}
	e := &shutdownEngine{listener: l, events: events, entered: make(chan struct{}), release: make(chan struct{})}
	close(e.release)
	s := New(validConfig(t), e, &net.Dialer{})
	s.listener = l
	s.acceptWG.Add(1)
	resume, acceptDone := make(chan struct{}), make(chan struct{})
	go func() {
		defer close(acceptDone)
		defer s.acceptWG.Done()
		<-resume
		s.shutdown(ErrListener, true)
	}()
	results := make(chan error, 8)
	for i := 0; i < cap(results); i++ {
		go func() { results <- s.Stop() }()
	}
	awaitShutdown(t, l.closeEntered, "Stop did not acquire listener-first teardown")
	var releaseOnce sync.Once
	t.Cleanup(func() { releaseOnce.Do(func() { close(l.closeRelease) }) })
	close(resume)
	awaitShutdown(t, acceptDone, "accept-error teardown blocked the accept drain while Stop owns teardown")
	releaseOnce.Do(func() { close(l.closeRelease) })
	for i := 0; i < cap(results); i++ {
		select {
		case err := <-results:
			if err != nil {
				t.Fatalf("Stop error = %v, want nil", err)
			}
		case <-time.After(time.Second):
			t.Fatal("Stop did not complete after accept drain")
		}
	}
	if got := strings.Join(events.snapshot(), ","); got != "listener-close,engine-close" {
		t.Fatalf("teardown = %s", got)
	}
	if err := s.Stop(); err != nil {
		t.Fatalf("repeated Stop = %v", err)
	}
}

func TestStopJoinsAcceptOwnerAfterTeardown(t *testing.T) {
	events := &shutdownEvents{}
	l := &shutdownListener{failure: make(chan struct{}), closed: make(chan struct{}), closeEntered: make(chan struct{}), closeRelease: make(chan struct{}), events: events}
	close(l.closeRelease)
	e := &shutdownEngine{listener: l, events: events, entered: make(chan struct{}), release: make(chan struct{})}
	close(e.release)
	s := New(validConfig(t), e, &net.Dialer{})
	s.listener = l
	s.acceptWG.Add(1)
	teardownDone, releaseAccept, acceptDone := make(chan struct{}), make(chan struct{}), make(chan struct{})
	go func() {
		defer close(acceptDone)
		defer s.acceptWG.Done()
		s.shutdown(ErrListener, true)
		close(teardownDone)
		<-releaseAccept // hold the real caller's registration after teardown
	}()
	var releaseOnce sync.Once
	release := func() { releaseOnce.Do(func() { close(releaseAccept) }) }
	t.Cleanup(release)
	awaitShutdown(t, teardownDone, "accept-owned teardown did not complete")
	results := make(chan error, 8)
	for i := 0; i < cap(results); i++ {
		go func() { results <- s.Stop() }()
	}
	select {
	case err := <-results:
		t.Fatalf("Stop returned before accept-owner registration drained: %v", err)
	case <-time.After(20 * time.Millisecond):
	}
	release()
	awaitShutdown(t, acceptDone, "accept owner did not drain")
	for i := 0; i < cap(results); i++ {
		select {
		case err := <-results:
			if err != ErrListener {
				t.Fatalf("Stop = %v, want ErrListener", err)
			}
		case <-time.After(time.Second):
			t.Fatal("Stop did not join accept owner")
		}
	}
	if got := strings.Join(events.snapshot(), ","); got != "listener-close,engine-close" {
		t.Fatalf("teardown = %s", got)
	}
}

func TestStopRejectsConnectionReturnedAfterListenerClose(t *testing.T) {
	events := &shutdownEvents{}
	probe, probePeer := net.Pipe()
	tail, inbound := net.Pipe()
	for _, c := range []net.Conn{probe, probePeer, tail, inbound} {
		defer c.Close()
	}
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
	if err := s.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	awaitShutdown(t, l.acceptEntered, "Accept did not begin")
	var releaseOnce sync.Once
	release := func() { releaseOnce.Do(func() { close(l.acceptRelease) }) }
	t.Cleanup(release)
	results := make(chan error, 8)
	for i := 0; i < cap(results); i++ {
		go func() { results <- s.Stop() }()
	}
	awaitShutdown(t, l.closeEntered, "Stop did not close listener first")
	select {
	case <-e.entered:
		t.Fatal("engine closed before pending Accept resolved")
	case <-time.After(20 * time.Millisecond):
	}
	release()
	for i := 0; i < cap(results); i++ {
		select {
		case err := <-results:
			if err != nil {
				t.Fatalf("Stop = %v", err)
			}
		case <-time.After(time.Second):
			t.Fatal("Stop did not drain late Accept")
		}
	}
	proxyDone := make(chan struct{})
	go func() { s.proxyWG.Wait(); close(proxyDone) }()
	awaitShutdown(t, proxyDone, "late-accept proxy observation did not settle")
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
					tail, inbound := net.Pipe()
					outbound, connector := net.Pipe()
					for _, c := range []net.Conn{probe, probePeer, tail, inbound, outbound, connector} {
						defer c.Close()
					}
					l := &shutdownListener{conn: inbound, failure: make(chan struct{}), closed: make(chan struct{}), closeEntered: make(chan struct{}), closeRelease: make(chan struct{}), events: events}
					e := &shutdownEngine{listener: l, events: events, entered: make(chan struct{}), release: make(chan struct{})}
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
					if err := s.Start(context.Background()); err != nil {
						t.Fatal(err)
					}
					_ = probePeer.Close()
					awaitShutdown(t, dialEntered, "proxy did not enter Dial")
					var dialOnce, listenerOnce, engineOnce sync.Once
					releaseDial := func() { dialOnce.Do(func() { close(dialRelease) }) }
					releaseListener := func() { listenerOnce.Do(func() { close(l.closeRelease) }) }
					releaseEngine := func() { engineOnce.Do(func() { close(e.release) }) }
					t.Cleanup(func() { releaseDial(); releaseListener(); releaseEngine() })
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
					results := make(chan error, 8)
					if owner == "listener" {
						close(l.failure)
						awaitShutdown(t, l.closeEntered, "unexpected listener error did not own teardown")
					}
					for i := 0; i < cap(results); i++ {
						go func() { results <- s.Stop() }()
					}
					awaitShutdown(t, l.closeEntered, "listener-first teardown did not begin")
					if owner == "stop" {
						close(l.failure)
					}
					releaseListener()
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
					var want error
					if owner == "listener" {
						want = ErrListener
					} else if fail {
						want = ErrEngine
					}
					for i := 0; i < cap(results); i++ {
						select {
						case err := <-results:
							if err != want {
								t.Fatalf("Stop = %v, want %v", err, want)
							}
						case <-time.After(time.Second):
							t.Fatal("concurrent Stop did not complete")
						}
					}
					if err := s.Stop(); err != want {
						t.Fatalf("repeated Stop = %v, want %v", err, want)
					}
					if err := s.Start(context.Background()); !errors.Is(err, ErrListener) {
						t.Fatalf("post-stop Start = %v", err)
					}
					if got := strings.Join(events.snapshot(), ","); got != "listener-close,engine-close" {
						t.Fatalf("teardown = %s", got)
					}
					acceptDone := make(chan struct{})
					go func() { s.acceptWG.Wait(); close(acceptDone) }()
					awaitShutdown(t, acceptDone, "Stop returned with undrained accept loop")
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
