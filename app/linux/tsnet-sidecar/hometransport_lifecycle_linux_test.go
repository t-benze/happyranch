package sidecar

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"golang.org/x/net/http2"
	"tailscale.com/control/controlhttp/controlhttpserver"
	"tailscale.com/derp"
	"tailscale.com/derp/derphttp"
	"tailscale.com/tailcfg"
	"tailscale.com/types/key"
	"tailscale.com/util/zstdframe"
)

func TestHomeStartupBeforeTSNet(t *testing.T) {
	for _, scenario := range []string{"ambient-proxy", "ambient-auth", "missing-fixed-trust"} {
		t.Run(scenario, func(t *testing.T) {
			unsetAmbientAuthentication(t)
			for _, name := range []string{"HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy", "GODEBUG", "SSLKEYLOGFILE"} {
				t.Setenv(name, "")
				os.Unsetenv(name)
			}
			t.Setenv("SSL_CERT_FILE", homeCAFile)
			t.Setenv("SSL_CERT_DIR", homeCADirectory)
			if scenario == "ambient-proxy" {
				t.Setenv("HTTPS_PROXY", "synthetic-secret-canary")
			}
			if scenario == "ambient-auth" {
				t.Setenv("TS_AUTHKEY", "synthetic-secret-canary")
			}
			if scenario == "missing-fixed-trust" {
				// No host path is created. An unavailable protected profile must
				// refuse in this unprivileged venue, never use the system pool.
				if _, err := os.Stat(homeCAFile); !os.IsNotExist(err) {
					t.Fatal("this fixture requires absent protected home trust")
				}
			}
			state := filepath.Join(t.TempDir(), "tsnet-must-not-create")
			e := NewTSNetEngine()
			defer e.Close()
			receipt, err := e.Start(context.Background(), EngineConfig{
				StateDir: state, ControlURL: homeURL, Profile: LinuxIdentityProfile,
				ControlTLS: &HomeControlTLS{DialIP: "192.168.1.2", CAFingerprint: strings.Repeat("a", 64)},
			}, []byte("synthetic-join-canary"))
			if !errors.Is(err, ErrConfiguration) || receipt != (RedemptionReceipt{}) {
				t.Fatalf("startup refusal class/receipt = %v, %+v", err, receipt)
			}
			if strings.Contains(err.Error(), "synthetic-") || e.server != nil || e.home != nil {
				t.Fatal("refused setup constructed tsnet/forwarder or disclosed input")
			}
			if _, err := os.Stat(state); !os.IsNotExist(err) {
				t.Fatal("refused startup reached tsnet state creation")
			}
		})
	}
}

func TestHomeSidecarPreservesConfigurationError(t *testing.T) {
	c := validConfig(t)
	c.Profile, c.ExpectedPeer, c.ConnectorAddr, c.ControlURL = LinuxIdentityProfile, "", "", homeURL
	c.ControlTLS = &HomeControlTLS{DialIP: "192.168.1.2", CAFingerprint: strings.Repeat("a", 64)}
	t.Setenv("HTTPS_PROXY", "synthetic-secret-canary")
	e := NewTSNetEngine()
	s := New(c, e, &net.Dialer{})
	defer s.Stop()
	if err := s.Start(context.Background()); !errors.Is(err, ErrConfiguration) {
		t.Fatalf("sidecar lost configuration classification: %v", err)
	}
	if s.Running() || e.server != nil {
		t.Fatal("failed setup opened sidecar admission")
	}
	if _, err := os.Stat(filepath.Join(c.StateDir, consumedMarker)); !os.IsNotExist(err) {
		t.Fatal("failed setup consumed enrollment authority")
	}
}

// All process-global root/proxy caches are initialized in a fresh child.
func cleanHomeChild(command *exec.Cmd) {
	for _, entry := range os.Environ() {
		name, _, _ := strings.Cut(entry, "=")
		if !strings.Contains(strings.ToUpper(name), "PROXY") && name != "GODEBUG" && name != "SSLKEYLOGFILE" && !strings.HasPrefix(name, "TS_") {
			command.Env = append(command.Env, entry)
		}
	}
}

func TestHomeTSNetLifecycle(t *testing.T) {
	if scenario := os.Getenv("HR_HOME_LIFECYCLE"); scenario != "" {
		runHomeTSNetLifecycle(t, scenario)
		return
	}
	for _, scenario := range []string{"cancel", "shutdown", "listener-failure", "verifier-failure", "local-tls-failure"} {
		t.Run(scenario, func(t *testing.T) {
			t.Parallel()
			ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestHomeTSNetLifecycle$", "-test.v")
			cleanHomeChild(cmd)
			cmd.Env = append(cmd.Env, "HR_HOME_LIFECYCLE="+scenario)
			if out, err := cmd.CombinedOutput(); err != nil {
				t.Fatalf("real tsnet lifecycle failed: %v\n%s", err, out)
			}
		})
	}
}

func runHomeTSNetLifecycle(t *testing.T, scenario string) {
	fixture := newHomeTSNetFixture(t)
	f, err := newHomeForwarder(fixture.up.Listener.Addr().String(), fixture.ca.cert)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if err := installHomeProcess(f, netip.MustParseAddr("127.0.0.2")); err != nil {
		t.Fatal(err)
	}
	// Protected admission is tested separately. Compose the unchanged real
	// tsnet server and shipping engine lifecycle with temporary synthetic trust;
	// no alternate protected path or production injection hook is introduced.
	cfg := EngineConfig{StateDir: t.TempDir(), ControlURL: homeURL, RoleIdentity: "home-sidecar-lifecycle", Profile: LinuxIdentityProfile}
	e := &TSNetEngine{home: f, server: newTSNetServer(cfg, []byte("synthetic-join-canary"))}
	defer e.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if err := e.server.Start(); err != nil {
		t.Fatal(err)
	}
	go e.watchHomeTransport(ctx)
	upCtx, stop := context.WithTimeout(ctx, 8*time.Second)
	defer stop()
	status, err := e.server.Up(upCtx)
	if err != nil || status == nil || status.BackendState != "Running" {
		t.Fatalf("real tsnet did not reach Running: %v", err)
	}
	l, err := e.Listen(":443")
	if err != nil {
		t.Fatal(err)
	}
	accepted := make(chan error, 1)
	go func() { _, err := l.Accept(); accepted <- err }()
	deadline := time.Now().Add(3 * time.Second)
	for fixture.relays.Load() == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if fixture.keys.Load() == 0 || fixture.registrations.Load() == 0 || fixture.canaries.Load() != 1 || fixture.maps.Load() == 0 || fixture.relays.Load() == 0 {
		t.Fatalf("missing real engine receipts: key=%d register=%d canary=%d map=%d derp=%d", fixture.keys.Load(), fixture.registrations.Load(), fixture.canaries.Load(), fixture.maps.Load(), fixture.relays.Load())
	}
	switch scenario {
	case "cancel":
		cancel()
	case "shutdown":
		e.Close()
	case "listener-failure":
		f.listener.Close()
	case "local-tls-failure":
		c, err := net.Dial("tcp", f.listener.Addr().String())
		if err != nil {
			t.Fatal(err)
		}
		c.SetDeadline(time.Now().Add(time.Second))
		fmt.Fprintf(c, "CONNECT %s:8443 HTTP/1.1\r\nHost: %s:8443\r\n\r\n", homeName, homeName)
		if response, err := http.ReadResponse(bufio.NewReader(c), nil); err != nil || response.StatusCode != 200 {
			t.Fatal("valid CONNECT failed before local handshake", err)
		}
		io.WriteString(c, "invalid TLS record")
		c.Close()
	case "verifier-failure":
		fixture.fail.Store(true)
		c, err := net.Dial("tcp", f.listener.Addr().String())
		if err != nil {
			t.Fatal(err)
		}
		c.SetDeadline(time.Now().Add(time.Second))
		fmt.Fprintf(c, "CONNECT %s:8443 HTTP/1.1\r\nHost: %s:8443\r\n\r\n", homeName, homeName)
		if _, err := http.ReadResponse(bufio.NewReader(c), nil); err == nil {
			t.Fatal("forged certificate got CONNECT 200")
		}
		c.Close()
	}
	select {
	case err := <-accepted:
		if err == nil {
			t.Fatal("teardown accepted a connection")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("home failure did not close real tailnet admission")
	}
	if l, err := e.Listen(":444"); err == nil {
		l.Close()
		t.Fatal("closed home engine reopened tailnet admission")
	}
	if err := e.Close(); err != nil {
		t.Fatal(err)
	}
	if f.leaf.PrivateKey != nil || len(f.streams) != 0 {
		t.Fatal("home teardown retained key or stream ownership")
	}
}

type homeTSNetFixture struct {
	ca                                                testHomeCA
	up                                                *httptest.Server
	fail                                              atomic.Bool
	keys, registrations, canaries, maps, relays, hits atomic.Int32
	mu                                                sync.Mutex
	nodeKeys                                          map[string][]key.NodePublic
}

func newHomeTSNetFixture(t *testing.T) *homeTSNetFixture {
	ca := makeHomeCA(t, false)
	leaf := ca.leaf(t, "valid")
	forged := makeHomeCA(t, false).leaf(t, "valid")
	fixture := &homeTSNetFixture{ca: ca, nodeKeys: make(map[string][]key.NodePublic)}
	noise := key.NewMachine()
	ds := derp.NewServer(key.NewNode(), func(string, ...any) {})
	t.Cleanup(func() { ds.Close() })
	dh := derphttp.Handler(ds)
	region := &tailcfg.DERPRegion{RegionID: 1, RegionCode: "home", Nodes: []*tailcfg.DERPNode{{Name: "home", RegionID: 1, HostName: homeName, IPv4: "127.0.0.2", IPv6: "none", DERPPort: 8443, STUNPort: -1}}}
	h2 := &http2.Server{}
	control := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/machine/register":
			var request tailcfg.RegisterRequest
			if json.NewDecoder(r.Body).Decode(&request) != nil {
				http.Error(w, "invalid", 400)
				return
			}
			fixture.registrations.Add(1)
			if request.Auth != nil && request.Auth.AuthKey == "synthetic-join-canary" {
				fixture.canaries.Add(1)
			}
			json.NewEncoder(w).Encode(tailcfg.RegisterResponse{MachineAuthorized: true, User: tailcfg.User{ID: 1, LoginName: "synthetic@example.invalid"}, Login: tailcfg.Login{ID: 1, LoginName: "synthetic@example.invalid"}})
		case "/machine/map":
			var request tailcfg.MapRequest
			if json.NewDecoder(r.Body).Decode(&request) != nil {
				http.Error(w, "invalid", 400)
				return
			}
			fixture.maps.Add(1)
			fixture.mu.Lock()
			fixture.nodeKeys[request.Hostinfo.Hostname] = append(fixture.nodeKeys[request.Hostinfo.Hostname], request.NodeKey)
			fixture.mu.Unlock()
			response := tailcfg.MapResponse{Node: &tailcfg.Node{
				ID: 1, StableID: "synthetic-stable-node", User: 1, Name: "home-sidecar.test.", Key: request.NodeKey,
				Addresses: []netip.Prefix{netip.MustParsePrefix("100.64.0.1/32")}, AllowedIPs: []netip.Prefix{netip.MustParsePrefix("100.64.0.1/32")},
				MachineAuthorized: true, DERP: "127.3.3.40:1",
			}, DERPMap: &tailcfg.DERPMap{Regions: map[int]*tailcfg.DERPRegion{1: region}}, Domain: "test", UserProfiles: []tailcfg.UserProfile{{ID: 1, LoginName: "synthetic@example.invalid"}}}
			data, _ := json.Marshal(response)
			data = zstdframe.AppendEncode(nil, data)
			var prefix [4]byte
			binary.LittleEndian.PutUint32(prefix[:], uint32(len(data)))
			w.Write(append(prefix[:], data...))
			w.(http.Flusher).Flush()
			if request.Stream {
				<-r.Context().Done()
			}
		default:
			http.NotFound(w, r)
		}
	})
	up := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		fixture.hits.Add(1)
		switch r.URL.Path {
		case "/key":
			fixture.keys.Add(1)
			json.NewEncoder(w).Encode(tailcfg.OverTLSPublicKeyResponse{PublicKey: noise.Public()})
		case "/ts2021":
			conn, err := controlhttpserver.AcceptHTTP(r.Context(), w, r, noise, nil)
			if err != nil {
				return
			}
			defer conn.Close()
			h2.ServeConn(conn, &http2.ServeConnOpts{BaseConfig: &http.Server{Handler: control}})
		case "/derp":
			fixture.relays.Add(1)
			dh.ServeHTTP(w, r)
		default:
			http.NotFound(w, r)
		}
	}))
	up.TLS = &tls.Config{GetCertificate: func(*tls.ClientHelloInfo) (*tls.Certificate, error) {
		if fixture.fail.Load() {
			return &forged, nil
		}
		return &leaf, nil
	}}
	up.Config.ErrorLog = log.New(io.Discard, "", 0)
	up.StartTLS()
	fixture.up = up
	t.Cleanup(up.Close)
	return fixture
}

type homeProcessReport struct {
	Proxy, Issuer, ResolvedIP string
	UsedEnrollment            bool
}

func TestHomeProcessRestartAndIsolation(t *testing.T) {
	if os.Getenv("HR_HOME_RESTART_CHILD") == "1" {
		runHomeRestartChild(t)
		return
	}
	fixture := newHomeTSNetFixture(t)
	configs := make([]Config, 2)
	for i := range configs {
		c := validConfig(t)
		c.ControlURL, c.Profile, c.ExpectedPeer, c.ConnectorAddr = homeURL, LinuxIdentityProfile, "", ""
		c.RoleIdentity = fmt.Sprintf("home-sidecar-isolated-%d", i)
		c.ControlTLS = &HomeControlTLS{DialIP: "192.168.4.5", CAFingerprint: homeFingerprint(fixture.ca.cert)}
		if err := os.WriteFile(c.CredentialFile, []byte("synthetic-join-canary"), 0o600); err != nil {
			t.Fatal(err)
		}
		configs[i] = c
	}
	type child struct {
		cmd    *exec.Cmd
		input  io.WriteCloser
		output *bytes.Buffer
		report string
	}
	launch := func(c Config, upstream, ip string) child {
		t.Helper()
		dir := t.TempDir()
		configPath, report := filepath.Join(dir, "sidecar.json"), filepath.Join(dir, "report.json")
		data, err := json.Marshal(c)
		if err != nil || os.WriteFile(configPath, data, 0o600) != nil {
			t.Fatal("write synthetic config")
		}
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
		t.Cleanup(cancel)
		cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestHomeProcessRestartAndIsolation$", "-test.v")
		cleanHomeChild(cmd)
		cmd.Env = append(cmd.Env, "HR_HOME_RESTART_CHILD=1", "HR_HOME_CONFIG="+configPath,
			"HR_HOME_REPORT="+report, "HR_HOME_UPSTREAM="+upstream, "HR_HOME_RESOLVER_IP="+ip,
			"HR_HOME_CA="+base64.StdEncoding.EncodeToString(fixture.ca.cert.Raw))
		input, err := cmd.StdinPipe()
		if err != nil {
			t.Fatal(err)
		}
		out := new(bytes.Buffer)
		cmd.Stdout, cmd.Stderr = out, out
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { input.Close(); cmd.Wait() })
		return child{cmd, input, out, report}
	}
	read := func(c child) homeProcessReport {
		t.Helper()
		deadline := time.Now().Add(10 * time.Second)
		for time.Now().Before(deadline) {
			data, err := os.ReadFile(c.report)
			if err == nil {
				var report homeProcessReport
				if json.Unmarshal(data, &report) == nil {
					return report
				}
			}
			time.Sleep(10 * time.Millisecond)
		}
		c.input.Close()
		c.cmd.Wait()
		t.Fatalf("child did not publish readiness: %s", c.output.String())
		return homeProcessReport{}
	}
	finish := func(c child) {
		t.Helper()
		c.input.Close()
		if err := c.cmd.Wait(); err != nil {
			t.Fatalf("dedicated process failed: %v\n%s", err, c.output.String())
		}
	}
	first := launch(configs[0], fixture.up.Listener.Addr().String(), "127.0.0.2")
	second := launch(configs[1], fixture.up.Listener.Addr().String(), "127.0.0.2")
	a, b := read(first), read(second)
	if a.Proxy == b.Proxy || a.Issuer == b.Issuer || !a.UsedEnrollment || !b.UsedEnrollment {
		t.Fatal("simultaneous processes shared listener/issuer or missed enrollment")
	}
	finish(first)
	finish(second)
	markerPath := filepath.Join(configs[0].StateDir, consumedMarker)
	marker, err := os.ReadFile(markerPath)
	if err != nil {
		t.Fatal(err)
	}
	// Renew under the same CA and move the upstream listener. Old process
	// caches/connections must not survive this restart or rewrite node identity.
	var refreshedHits atomic.Int32
	refreshed := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		refreshedHits.Add(1)
		fixture.up.Config.Handler.ServeHTTP(w, r)
	}))
	refreshed.TLS = &tls.Config{Certificates: []tls.Certificate{fixture.ca.leaf(t, "valid")}}
	refreshed.Config.ErrorLog = log.New(io.Discard, "", 0)
	refreshed.StartTLS()
	defer refreshed.Close()
	configs[0].ControlTLS.DialIP = "192.168.4.6"
	oldHits := fixture.hits.Load()
	restart := launch(configs[0], refreshed.Listener.Addr().String(), "127.0.0.3")
	r := read(restart)
	finish(restart)
	if r.UsedEnrollment || r.Issuer == a.Issuer || r.ResolvedIP != "127.0.0.3" {
		t.Fatal("restart reused enrollment, local issuer or stale resolver")
	}
	if refreshedHits.Load() == 0 || fixture.hits.Load()-refreshedHits.Load() != oldHits || fixture.canaries.Load() != 2 {
		t.Fatal("restart did not use renewed endpoint or reused a join key")
	}
	after, err := os.ReadFile(markerPath)
	if err != nil || !bytes.Equal(marker, after) {
		t.Fatal("restart changed canonical consumed authority")
	}
	fixture.mu.Lock()
	defer fixture.mu.Unlock()
	observed := fixture.nodeKeys[configs[0].RoleIdentity]
	if len(observed) < 2 {
		t.Fatal("missing first/restarted real node map requests")
	}
	for _, node := range observed {
		if node != observed[0] {
			t.Fatal("restart changed durable node identity")
		}
	}
}

func runHomeRestartChild(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("HR_HOME_CONFIG"))
	var cfg Config
	if err != nil || json.Unmarshal(data, &cfg) != nil || cfg.Validate() != nil {
		t.Fatal("invalid synthetic child config")
	}
	der, err := base64.StdEncoding.DecodeString(os.Getenv("HR_HOME_CA"))
	if err != nil {
		t.Fatal(err)
	}
	ca, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	f, err := newHomeForwarder(os.Getenv("HR_HOME_UPSTREAM"), ca)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	ip := netip.MustParseAddr(os.Getenv("HR_HOME_RESOLVER_IP"))
	if err := installHomeProcess(f, ip); err != nil {
		t.Fatal(err)
	}
	credential, err := consumeInput(cfg)
	if err != nil {
		t.Fatal(err)
	}
	c := EngineConfig{StateDir: cfg.StateDir, ControlURL: cfg.ControlURL, RoleIdentity: cfg.RoleIdentity, Profile: cfg.Profile}
	e := &TSNetEngine{home: f, server: newTSNetServer(c, credential)}
	defer e.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if _, err := e.server.Up(ctx); err != nil {
		t.Fatal("real process restart did not reach Running", err)
	}
	if len(credential) != 0 {
		if err := commitConsumption(cfg); err != nil {
			t.Fatal(err)
		}
	}
	resolved, err := net.DefaultResolver.LookupHost(ctx, homeName)
	if err != nil || len(resolved) != 1 || resolved[0] != ip.String() {
		t.Fatal("process retained stale DNS mapping")
	}
	leaf, err := x509.ParseCertificate(f.leaf.Certificate[0])
	if err != nil {
		t.Fatal(err)
	}
	report, _ := json.Marshal(homeProcessReport{f.proxyURL(), homeFingerprint(leaf), resolved[0], len(credential) != 0})
	if err := os.WriteFile(os.Getenv("HR_HOME_REPORT"), report, 0o600); err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, os.Stdin)
}
