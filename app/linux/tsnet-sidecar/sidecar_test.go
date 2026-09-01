package sidecar

import (
	"context"
	"errors"
	"io"
	"net"
	"os"
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
}

func (f *fakeEngine) Start(context.Context, EngineConfig, []byte) (RedemptionReceipt, error) {
	*f.events = append(*f.events, "start")
	return f.receipt, f.startErr
}
func (f *fakeEngine) Listen(string) (net.Listener, error) {
	*f.events = append(*f.events, "listen")
	return f.listener, f.listenErr
}
func (f *fakeEngine) Close() error { *f.events = append(*f.events, "engine-close"); return f.closeErr }

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
	return Config{StateDir: state, CredentialFile: cred, ControlURL: "https://headscale.private.example", RoleIdentity: "home-sidecar-123", ExpectedPeer: "mac-client-123", ListenAddr: ":443", ConnectorAddr: "127.0.0.1:9443", DERPPolicy: "private-only"}
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
	if !errors.Is(err, ErrCredential) || len(events) != 0 {
		t.Fatalf("err=%v events=%v", err, events)
	}
}

func TestRedemptionAndDeletionMustBeDurableBeforeListen(t *testing.T) {
	for _, receipt := range []RedemptionReceipt{{}, {Redeemed: true}, {Redeemed: true, Durable: true}} {
		cfg := validConfig(t)
		events := []string{}
		e := &fakeEngine{receipt: receipt, events: &events}
		if err := New(cfg, e, &net.Dialer{}).Start(context.Background()); !errors.Is(err, ErrCredential) {
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
	listener := &oneListener{conn: tailServer, acceptErr: net.ErrClosed, accepted: make(chan struct{}), events: &events}
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

type dialFunc func(context.Context, string, string) (net.Conn, error)

func (f dialFunc) DialContext(c context.Context, n, a string) (net.Conn, error) { return f(c, n, a) }

type oneListener struct {
	conn      net.Conn
	acceptErr error
	accepted  chan struct{}
	events    *[]string
	once      sync.Once
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
	select {}
}
func (l *oneListener) Close() error {
	if l.events != nil {
		*l.events = append(*l.events, "listener-close")
	}
	return nil
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
