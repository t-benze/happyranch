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
	credential                    []byte
	config                        EngineConfig
}

func (f *fakeEngine) Start(_ context.Context, config EngineConfig, credential []byte) (RedemptionReceipt, error) {
	*f.events = append(*f.events, "start")
	f.credential = append([]byte(nil), credential...)
	f.config = config
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
	return Config{StateDir: state, CredentialFile: cred, ControlURL: "https://headscale.private.example", RoleIdentity: "home-sidecar-123", ExpectedPeer: "mac-client-123", ListenAddr: ":443", ConnectorAddr: "127.0.0.1:9443", DERPPolicy: "private-only"}
}

// The persisted digest for validConfig's role and control URL, including the
// newline written by the enrollment path.
const validMarkerContent = "4ccf9915c9a0f9ec80c8f2d635003fa3be78185085a10a89e62f5264e8adff2f\n"

func consumedConfig(t *testing.T) Config {
	t.Helper()
	cfg := validConfig(t)
	if err := os.Remove(cfg.CredentialFile); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(cfg.StateDir, consumedMarker), []byte(validMarkerContent), 0o600); err != nil {
		t.Fatal(err)
	}
	return cfg
}

func TestSystemdCredentialLifecycle(t *testing.T) {
	for _, name := range []string{"enrollment", "enrollment_acl_mask", "restart_absent", "restart_directory_absent", "enrollment_directory_absent", "restart_ancestor_absent", "restart_ancestor_symlink", "restart_present", "invalid_mode", "wrong_name", "symlink"} {
		t.Run(name, func(t *testing.T) {
			cfg := validConfig(t)
			dir := filepath.Join(t.TempDir(), "credentials")
			absentDirectory := strings.HasSuffix(name, "directory_absent") || strings.HasPrefix(name, "restart_ancestor_")
			switch name {
			case "restart_ancestor_absent":
				dir = filepath.Join(dir, "unit")
			case "restart_ancestor_symlink":
				if err := os.Symlink(t.TempDir(), dir); err != nil {
					t.Fatal(err)
				}
				dir = filepath.Join(dir, "unit")
			default:
				if !absentDirectory {
					if err := os.Mkdir(dir, 0o700); err != nil {
						t.Fatal(err)
					}
				}
			}
			t.Setenv("CREDENTIALS_DIRECTORY", dir)
			cfg.CredentialFile = filepath.Join(dir, "enrollment-key")
			if name == "wrong_name" {
				cfg.CredentialFile = filepath.Join(dir, "other")
			}
			if name != "restart_absent" && !absentDirectory {
				mode := os.FileMode(0o400)
				if name == "enrollment_acl_mask" {
					mode = 0o440
				}
				if name == "invalid_mode" {
					mode = 0o600
				}
				if err := os.WriteFile(cfg.CredentialFile, []byte("synthetic-systemd-input\n"), mode); err != nil {
					t.Fatal(err)
				}
			}
			if name == "symlink" {
				if err := os.Remove(cfg.CredentialFile); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink("absent", cfg.CredentialFile); err != nil {
					t.Fatal(err)
				}
			}
			marker := filepath.Join(cfg.StateDir, consumedMarker)
			if strings.HasPrefix(name, "restart_") {
				if err := os.WriteFile(marker, []byte(validMarkerContent), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			before, _ := os.Lstat(marker)
			// An attempted unlink fails for this service-owned, non-writable directory.
			if !absentDirectory {
				if err := os.Chmod(dir, 0o500); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = os.Chmod(dir, 0o700) })
			}
			events := []string{}
			l := &oneListener{blockAfter: true, closed: make(chan struct{}), accepted: make(chan struct{}), events: &events}
			e := &fakeEngine{listener: l, receipt: RedemptionReceipt{true, true, true}, events: &events}
			client, server := net.Pipe()
			defer server.Close()
			defer client.Close()
			svc := New(cfg, e, dialFunc(func(context.Context, string, string) (net.Conn, error) {
				events = append(events, "probe")
				return client, nil
			}))
			t.Cleanup(func() { _ = svc.Stop() })
			err := svc.Start(context.Background())
			enroll := name == "enrollment" || name == "enrollment_acl_mask"
			restart := name == "restart_absent" || name == "restart_directory_absent"
			if !enroll && !restart {
				if !errors.Is(err, ErrCredential) || len(events) != 0 {
					t.Fatalf("unsafe credential admitted: err=%v events=%v", err, events)
				}
				if before == nil {
					if _, err := os.Lstat(marker); !errors.Is(err, syscall.ENOENT) {
						t.Fatalf("refusal created a marker: %v", err)
					}
				}
				return
			}
			if err != nil {
				t.Fatalf("systemd %s: %v", name, err)
			}
			want := "synthetic-systemd-input\n"
			if restart {
				want = ""
			}
			if string(e.credential) != want || strings.Join(events, ",") != "start,probe,listen" {
				t.Fatal("key delivery or startup gates changed")
			}
			content, err := os.ReadFile(marker)
			if err != nil || string(content) != validMarkerContent {
				t.Fatalf("consumption marker not committed: %v", err)
			}
			if enroll {
				content, err = os.ReadFile(cfg.CredentialFile)
				if err != nil || string(content) != want {
					t.Fatalf("systemd copy removed or changed: %v", err)
				}
			} else {
				after, err := os.Lstat(marker)
				if err != nil || !os.SameFile(before, after) || !before.ModTime().Equal(after.ModTime()) {
					t.Fatal("restart rewrote marker")
				}
				if _, err := os.Lstat(cfg.CredentialFile); !errors.Is(err, syscall.ENOENT) {
					t.Fatal("restart recreated credential")
				}
				if absentDirectory {
					if _, err := os.Lstat(dir); !errors.Is(err, syscall.ENOENT) {
						t.Fatalf("restart recreated credentials directory: %v", err)
					}
				}
			}
		})
	}
}

func TestStartWithConsumedCredentialReusesDurableState(t *testing.T) {
	cfg := consumedConfig(t)
	marker := filepath.Join(cfg.StateDir, consumedMarker)
	before, err := os.Lstat(marker)
	if err != nil {
		t.Fatal(err)
	}
	events := []string{}
	l := &oneListener{blockAfter: true, closed: make(chan struct{}), accepted: make(chan struct{}), events: &events}
	e := &fakeEngine{listener: l, receipt: RedemptionReceipt{true, true, true}, events: &events}
	probeClient, probeServer := net.Pipe()
	defer probeServer.Close()
	s := New(cfg, e, dialFunc(func(context.Context, string, string) (net.Conn, error) {
		events = append(events, "probe")
		return probeClient, nil
	}))
	t.Cleanup(func() { _ = s.Stop() })
	if err := s.Start(context.Background()); err != nil {
		t.Fatalf("restart with consumed marker and no credential: %v", err)
	}
	if len(e.credential) != 0 || e.config != (EngineConfig{cfg.StateDir, cfg.ControlURL, cfg.RoleIdentity, cfg.ExpectedPeer}) {
		t.Fatal("restart did not reuse the configured state without an auth key")
	}
	if strings.Join(events, ",") != "start,probe,listen" {
		t.Fatalf("startup gates = %v", events)
	}
	after, err := os.Lstat(marker)
	if err != nil {
		t.Fatal(err)
	}
	content, err := os.ReadFile(marker)
	if err != nil || string(content) != validMarkerContent || !os.SameFile(before, after) || !before.ModTime().Equal(after.ModTime()) {
		t.Fatalf("consumed marker changed: %v", err)
	}
	if _, err := os.Lstat(cfg.CredentialFile); !errors.Is(err, syscall.ENOENT) {
		t.Fatalf("credential recreated: %v", err)
	}
}

func TestConsumedMarkerRejectsCredentialWithoutReadingIt(t *testing.T) {
	for _, name := range []string{"regular", "fifo", "directory", "symlink", "dangling-symlink", "symlinked-parent", "missing-parent", "non-directory-parent"} {
		t.Run(name, func(t *testing.T) {
			cfg := consumedConfig(t)
			var err error
			switch name {
			case "regular", "non-directory-parent":
				err = os.WriteFile(cfg.CredentialFile, []byte("unused-enrollment-input"), 0o600)
				if err == nil {
					err = os.Chtimes(cfg.CredentialFile, time.Unix(1, 0), time.Unix(1, 0))
				}
				if name == "non-directory-parent" {
					cfg.CredentialFile = filepath.Join(cfg.CredentialFile, "child")
				}
			case "fifo":
				err = syscall.Mkfifo(cfg.CredentialFile, 0o600)
			case "directory":
				err = os.Mkdir(cfg.CredentialFile, 0o700)
			case "symlink":
				err = os.Symlink(filepath.Join(cfg.StateDir, consumedMarker), cfg.CredentialFile)
			case "dangling-symlink":
				err = os.Symlink(filepath.Join(cfg.StateDir, "absent"), cfg.CredentialFile)
			case "symlinked-parent":
				alias := filepath.Join(t.TempDir(), "alias")
				err = os.Symlink(filepath.Dir(cfg.CredentialFile), alias)
				cfg.CredentialFile = filepath.Join(alias, "credential")
			case "missing-parent":
				cfg.CredentialFile = filepath.Join(filepath.Dir(cfg.CredentialFile), "absent", "credential")
			}
			if err != nil {
				t.Fatal(err)
			}
			events := []string{}
			e := &fakeEngine{events: &events}
			if err := New(cfg, e, &net.Dialer{}).Start(context.Background()); !errors.Is(err, ErrCredential) || len(events) != 0 {
				t.Fatalf("err=%v events=%v", err, events)
			}
			if name == "regular" {
				st, err := os.Lstat(cfg.CredentialFile)
				if err != nil {
					t.Fatal(err)
				}
				if st.Sys().(*syscall.Stat_t).Atim.Sec != 1 {
					t.Fatal("present credential was read")
				}
			}
		})
	}
}

func TestRestartRejectsHostileConsumedMarker(t *testing.T) {
	for _, name := range []string{"symlink", "dangling-symlink", "directory", "fifo", "wrong-mode", "wrong-content", "wrong-role", "wrong-control"} {
		t.Run(name, func(t *testing.T) {
			cfg := consumedConfig(t)
			marker := filepath.Join(cfg.StateDir, consumedMarker)
			var err error
			switch name {
			case "symlink", "dangling-symlink", "directory", "fifo":
				if err := os.Remove(marker); err != nil {
					t.Fatal(err)
				}
				switch name {
				case "symlink", "dangling-symlink":
					target := filepath.Join(cfg.StateDir, "target")
					if name == "symlink" {
						if err := os.WriteFile(target, []byte(validMarkerContent), 0o600); err != nil {
							t.Fatal(err)
						}
					}
					err = os.Symlink(target, marker)
				case "directory":
					err = os.Mkdir(marker, 0o700)
				case "fifo":
					err = syscall.Mkfifo(marker, 0o600)
				}
			case "wrong-mode":
				err = os.Chmod(marker, 0o644)
			case "wrong-content":
				err = os.WriteFile(marker, []byte("untrusted-marker\n"), 0o600)
			case "wrong-role":
				cfg.RoleIdentity = "home-sidecar-other"
			case "wrong-control":
				cfg.ControlURL = "https://other.private.example"
			}
			if err != nil {
				t.Fatal(err)
			}
			events := []string{}
			if err := New(cfg, &fakeEngine{events: &events}, &net.Dialer{}).Start(context.Background()); !errors.Is(err, ErrCredential) || len(events) != 0 {
				t.Fatalf("err=%v events=%v", err, events)
			}
		})
	}
}

func TestRestartRetainsStartupFailureGates(t *testing.T) {
	for _, tc := range []struct {
		name       string
		receipt    RedemptionReceipt
		startErr   error
		probeErr   error
		probeClose bool
		listenErr  error
		want       error
	}{
		{name: "engine-start", receipt: RedemptionReceipt{true, true, true}, startErr: errors.New("engine failed"), want: ErrCredential},
		{name: "not-redeemed", receipt: RedemptionReceipt{false, true, true}, want: ErrCredential},
		{name: "not-durable", receipt: RedemptionReceipt{true, false, true}, want: ErrCredential},
		{name: "peer-not-visible", receipt: RedemptionReceipt{true, true, false}, want: ErrCredential},
		{name: "probe", receipt: RedemptionReceipt{true, true, true}, probeErr: errors.New("probe failed"), want: ErrConnector},
		{name: "probe-close", receipt: RedemptionReceipt{true, true, true}, probeClose: true, want: ErrConnector},
		{name: "listen", receipt: RedemptionReceipt{true, true, true}, listenErr: errors.New("listen failed"), want: ErrListener},
	} {
		t.Run(tc.name, func(t *testing.T) {
			cfg := consumedConfig(t)
			events := []string{}
			l := &oneListener{blockAfter: true, closed: make(chan struct{}), accepted: make(chan struct{}), events: &events}
			e := &fakeEngine{listener: l, receipt: tc.receipt, startErr: tc.startErr, listenErr: tc.listenErr, events: &events}
			probeClient, probeServer := net.Pipe()
			defer probeClient.Close()
			defer probeServer.Close()
			s := New(cfg, e, dialFunc(func(context.Context, string, string) (net.Conn, error) {
				events = append(events, "probe")
				if tc.probeClose {
					return &errorCloseConn{}, nil
				}
				return probeClient, tc.probeErr
			}))
			t.Cleanup(func() { _ = s.Stop() })
			if err := s.Start(context.Background()); !errors.Is(err, tc.want) {
				t.Fatalf("err=%v, want %v", err, tc.want)
			}
			wantEvents := "start,engine-close"
			if tc.want == ErrConnector {
				wantEvents = "start,probe,engine-close"
			} else if tc.want == ErrListener {
				wantEvents = "start,probe,listen,engine-close"
			}
			if strings.Join(events, ",") != wantEvents || s.Running() || len(e.credential) != 0 {
				t.Fatalf("failed restart admission: events=%v running=%v", events, s.Running())
			}
			content, err := os.ReadFile(filepath.Join(cfg.StateDir, consumedMarker))
			if err != nil || string(content) != validMarkerContent {
				t.Fatalf("marker changed on failed restart: %v", err)
			}
		})
	}
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
	t.Setenv("CREDENTIALS_DIRECTORY", filepath.Dir(cfg.CredentialFile)+"-unrelated")
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
	if string(e.credential) != "secret-value\n" {
		t.Fatal("first enrollment did not pass the credential to the engine")
	}
	marker, err := os.ReadFile(filepath.Join(cfg.StateDir, consumedMarker))
	if err != nil || string(marker) != validMarkerContent {
		t.Fatalf("first enrollment did not persist the identity digest: %v", err)
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

func TestShippingFailureMatrixUsesStableCategories(t *testing.T) {
	tests := []struct {
		name   string
		engine func(*[]string, net.Listener) *fakeEngine
		dialer func(net.Conn) Dialer
		want   error
	}{
		{"engine-start", func(e *[]string, l net.Listener) *fakeEngine {
			return &fakeEngine{startErr: errors.New("secret"), events: e}
		}, func(net.Conn) Dialer { return &net.Dialer{} }, ErrCredential},
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
