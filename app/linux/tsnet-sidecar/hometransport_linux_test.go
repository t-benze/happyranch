package sidecar

import (
	"bufio"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/netip"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"golang.org/x/net/http2"
	"tailscale.com/control/controlclient"
	"tailscale.com/control/controlhttp/controlhttpserver"
	"tailscale.com/derp"
	"tailscale.com/derp/derphttp"
	"tailscale.com/net/netmon"
	"tailscale.com/net/tsdial"
	"tailscale.com/tailcfg"
	"tailscale.com/types/key"
)

type testHomeCA struct {
	cert *x509.Certificate
	key  *ecdsa.PrivateKey
}

func makeHomeCA(t *testing.T, expired bool) testHomeCA {
	t.Helper()
	k, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	c := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "synthetic home"}, IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign, NotBefore: now.Add(-time.Hour), NotAfter: now.Add(time.Hour)}
	if expired {
		c.NotAfter = now.Add(-time.Minute)
	}
	der, err := x509.CreateCertificate(rand.Reader, c, c, &k.PublicKey, k)
	if err != nil {
		t.Fatal(err)
	}
	cert, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	return testHomeCA{cert, k}
}

func (ca testHomeCA) leaf(t *testing.T, profile string) tls.Certificate {
	t.Helper()
	k, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	c := &x509.Certificate{SerialNumber: big.NewInt(2), DNSNames: []string{homeName}, NotBefore: now.Add(-time.Hour), NotAfter: now.Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	switch profile {
	case "public-unrelated", "wrong-san":
		c.DNSNames = []string{"attacker.example.com"}
	case "public-wildcard":
		c.DNSNames = []string{"*.happyranch.invalid"}
	case "no-san":
		c.DNSNames = nil
	case "ip-only":
		c.DNSNames = nil
		c.IPAddresses = []net.IP{net.ParseIP("127.0.0.1")}
	case "expired-leaf":
		c.NotAfter = now.Add(-time.Minute)
	}
	der, err := x509.CreateCertificate(rand.Reader, c, ca.cert, &k.PublicKey, ca.key)
	if err != nil {
		t.Fatal(err)
	}
	return tls.Certificate{Certificate: [][]byte{der, ca.cert.Raw}, PrivateKey: k}
}

func homeFingerprint(c *x509.Certificate) string {
	d := sha256.Sum256(c.Raw)
	return hex.EncodeToString(d[:])
}

func TestHomeCAAdmission(t *testing.T) {
	for _, scenario := range []string{"valid", "missing", "symlink", "parent-symlink", "hardlink", "write-mode", "world-readable", "private-mode", "parent-write", "extra-ca", "extra-pem", "leading-text", "fingerprint", "nonempty-dir", "dir-symlink", "dir-write", "dir-private-mode", "expired-ca", "not-ca", "oversize"} {
		t.Run(scenario, func(t *testing.T) {
			dir := t.TempDir()
			if err := os.Chmod(dir, 0o700); err != nil {
				t.Fatal(err)
			}
			ca := makeHomeCA(t, scenario == "expired-ca")
			bundle := filepath.Join(dir, "ca.pem")
			empty := filepath.Join(dir, "empty")
			if err := os.Mkdir(empty, 0o755); err != nil {
				t.Fatal(err)
			}
			data := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: ca.cert.Raw})
			if scenario == "not-ca" {
				data = pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: ca.leaf(t, "valid").Certificate[0]})
			}
			if scenario == "extra-ca" {
				data = append(data, data...)
			}
			if scenario == "extra-pem" {
				data = append(data, pem.EncodeToMemory(&pem.Block{Type: "UNRELATED", Bytes: []byte("synthetic")})...)
			}
			if scenario == "leading-text" {
				data = append([]byte("ignored text\n"), data...)
			}
			if scenario == "oversize" {
				data = append(data, bytesOf(' ', 65<<10)...)
			}
			if err := os.WriteFile(bundle, data, 0o640); err != nil {
				t.Fatal(err)
			}
			fingerprint := homeFingerprint(ca.cert)
			switch scenario {
			case "missing":
				if err := os.Remove(bundle); err != nil {
					t.Fatal(err)
				}
			case "symlink":
				if err := os.Rename(bundle, bundle+".saved"); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(bundle+".saved", bundle); err != nil {
					t.Fatal(err)
				}
			case "parent-symlink":
				link := dir + "-link"
				if err := os.Symlink(dir, link); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { os.Remove(link) })
				bundle = filepath.Join(link, "ca.pem")
			case "hardlink":
				if err := os.Link(bundle, bundle+".linked"); err != nil {
					t.Fatal(err)
				}
			case "write-mode":
				if err := os.Chmod(bundle, 0o660); err != nil {
					t.Fatal(err)
				}
			case "world-readable":
				if err := os.Chmod(bundle, 0o644); err != nil {
					t.Fatal(err)
				}
			case "private-mode":
				if err := os.Chmod(bundle, 0o600); err != nil {
					t.Fatal(err)
				}
			case "parent-write":
				if err := os.Chmod(dir, 0o770); err != nil {
					t.Fatal(err)
				}
			case "fingerprint":
				fingerprint = strings.Repeat("0", 64)
			case "nonempty-dir":
				if err := os.WriteFile(filepath.Join(empty, "extra"), []byte("synthetic"), 0o600); err != nil {
					t.Fatal(err)
				}
			case "dir-symlink":
				if err := os.Remove(empty); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(dir, empty); err != nil {
					t.Fatal(err)
				}
			case "dir-private-mode":
				if err := os.Chmod(empty, 0o700); err != nil {
					t.Fatal(err)
				}
			case "dir-write":
				if err := os.Chmod(empty, 0o775); err != nil {
					t.Fatal(err)
				}
			}
			p := homeTrustPolicy{uid: uint32(os.Getuid()), caGID: uint32(os.Getgid()), directoryGID: uint32(os.Getgid())}
			admitted, err := p.admit(bundle, empty, fingerprint)
			if scenario == "valid" {
				if err != nil || admitted == nil {
					t.Fatal("safe single CA refused", err)
				}
			} else if err == nil {
				t.Fatal("unsafe trust admitted")
			}
		})
	}
}

func bytesOf(b byte, n int) []byte { return []byte(strings.Repeat(string(b), n)) }

func TestHomeEnvironmentAdmission(t *testing.T) {
	base := []string{"SSL_CERT_FILE=" + homeCAFile, "SSL_CERT_DIR=" + homeCADirectory}
	if err := admitHomeEnvironment(base); err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy", "SSLKEYLOGFILE", "GODEBUG", "TS_DEBUG_TLS_DIAL", "TS_DEBUG_USE_DERP_HTTP", "TS_DEBUG_DERP_WS_CLIENT", "TS_DEBUG_FAKE_PROXY_AUTH", "TS_FORCE_NOISE_443", "TS_USE_CONTROL_DIAL_PLAN", "TS_AUTHKEY", "TS_UNKNOWN_OVERRIDE", "SSL_CERT_FILE", "SSL_CERT_DIR"} {
		t.Run(name, func(t *testing.T) {
			env := append([]string{}, base...)
			if name == "SSL_CERT_FILE" {
				env[0] = name + "=synthetic-secret-canary"
			} else if name == "SSL_CERT_DIR" {
				env[1] = name + "=synthetic-secret-canary"
			} else {
				env = append(env, name+"=synthetic-secret-canary")
			}
			err := admitHomeEnvironment(env)
			if err == nil {
				t.Fatal("ambient override admitted")
			}
			if strings.Contains(err.Error(), "synthetic-secret-canary") {
				t.Fatal("override value leaked")
			}
		})
	}
	for _, env := range [][]string{nil, base[:1], base[1:]} {
		if admitHomeEnvironment(env) == nil {
			t.Fatal("missing fixed trust environment admitted")
		}
	}
}

func TestHomeFixedResolver(t *testing.T) {
	r := fixedHomeResolver(netip.MustParseAddr("192.168.4.5"))
	ctx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	ips, err := r.LookupHost(ctx, homeName)
	if err != nil || len(ips) != 1 || ips[0] != "192.168.4.5" {
		t.Fatal(ips, err)
	}
	if _, err := r.LookupHost(ctx, "unrelated.invalid"); err == nil {
		t.Fatal("unrelated DNS resolved")
	}
}

// Each profile re-execs before pinned proxy/root caches can be initialized.
func TestHomeTransportMatrix(t *testing.T) {
	if profile := os.Getenv("HR_HOME_TEST_PROFILE"); profile != "" {
		runHomeTransportProfile(t, profile, os.Getenv("HR_HOME_TEST_PATH"))
		return
	}
	for _, profile := range []string{"valid", "renewed-leaf", "redirect", "public-matching", "public-wildcard", "public-unrelated", "wrong-san", "no-san", "ip-only", "forged-ca", "expired-ca", "expired-leaf", "wrong-noise", "outer-forged", "proxy-unavailable"} {
		for _, path := range []string{"bootstrap", "derp"} {
			if path == "derp" && (profile == "wrong-noise" || profile == "outer-forged") {
				continue
			}
			t.Run(profile+"/"+path, func(t *testing.T) {
				t.Parallel()
				cmd := exec.Command(os.Args[0], "-test.run=^TestHomeTransportMatrix$", "-test.v")
				for _, entry := range os.Environ() {
					name, _, _ := strings.Cut(entry, "=")
					if !strings.Contains(strings.ToUpper(name), "PROXY") && name != "GODEBUG" && name != "SSLKEYLOGFILE" && !strings.HasPrefix(name, "TS_") {
						cmd.Env = append(cmd.Env, entry)
					}
				}
				cmd.Env = append(cmd.Env, "HR_HOME_TEST_PROFILE="+profile, "HR_HOME_TEST_PATH="+path)
				out, err := cmd.CombinedOutput()
				if err != nil {
					t.Fatalf("profile failed: %v\n%s", err, out)
				}
				t.Log(strings.TrimSpace(string(out)))
			})
		}
	}
}

func runHomeTransportProfile(t *testing.T, profile, path string) {
	home := makeHomeCA(t, profile == "expired-ca")
	issuer := home
	public := makeHomeCA(t, false)
	if strings.HasPrefix(profile, "public-") {
		issuer = public
	}
	if profile == "forged-ca" {
		issuer = makeHomeCA(t, false)
	}
	leaf := issuer.leaf(t, profile)
	renewed := issuer.leaf(t, "valid")
	valid := profile == "valid" || profile == "renewed-leaf"
	if strings.HasPrefix(profile, "public-") {
		pool := x509.NewCertPool()
		pool.AddCert(public.cert)
		cert, _ := x509.ParseCertificate(leaf.Certificate[0])
		for _, name := range []string{cert.DNSNames[0], ""} {
			if profile == "public-wildcard" && name != "" {
				name = homeName
			}
			if _, err := cert.Verify(x509.VerifyOptions{Roots: pool, DNSName: name}); err != nil {
				t.Fatal("public equivalent precondition", err)
			}
		}
	}
	var keyHits, noiseAttempts, registrations, canaries atomic.Int32
	var rotateLeaf atomic.Bool
	noise := key.NewMachine()
	machine := key.NewMachine()
	advertised := noise.Public()
	if profile == "wrong-noise" {
		advertised = key.NewMachine().Public()
	}
	ds := derp.NewServer(key.NewNode(), func(string, ...any) {})
	defer ds.Close()
	derpHandler := derphttp.Handler(ds)
	h2 := &http2.Server{}
	register := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/machine/register" {
			http.NotFound(w, r)
			return
		}
		registrations.Add(1)
		var req tailcfg.RegisterRequest
		if json.NewDecoder(r.Body).Decode(&req) != nil {
			http.Error(w, "invalid", 400)
			return
		}
		if req.Auth != nil && req.Auth.AuthKey == "synthetic-join-canary" {
			canaries.Add(1)
		}
		_ = json.NewEncoder(w).Encode(tailcfg.RegisterResponse{MachineAuthorized: true})
	})
	forged := makeHomeCA(t, false).leaf(t, "valid")
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/key":
			keyHits.Add(1)
			if profile == "redirect" {
				http.Redirect(w, r, "https://unrelated.invalid:28443/key", http.StatusFound)
				return
			}
			_ = json.NewEncoder(w).Encode(tailcfg.OverTLSPublicKeyResponse{PublicKey: advertised})
		case "/ts2021":
			noiseAttempts.Add(1)
			conn, err := controlhttpserver.AcceptHTTP(r.Context(), w, r, noise, nil)
			if err != nil {
				return
			}
			defer conn.Close()
			h2.ServeConn(conn, &http2.ServeConnOpts{BaseConfig: &http.Server{Handler: register}})
		case "/derp":
			if profile == "redirect" {
				http.Redirect(w, r, "https://unrelated.invalid:28443/derp", http.StatusFound)
				return
			}
			derpHandler.ServeHTTP(w, r)
		default:
			http.NotFound(w, r)
		}
	}))
	server.Config.ErrorLog = log.New(io.Discard, "", 0)
	server.TLS = &tls.Config{GetCertificate: func(*tls.ClientHelloInfo) (*tls.Certificate, error) {
		if profile == "outer-forged" && keyHits.Load() > 0 {
			return &forged, nil
		}
		if profile == "renewed-leaf" && (keyHits.Load() > 0 || rotateLeaf.Load()) {
			return &renewed, nil
		}
		return &leaf, nil
	}}
	server.StartTLS()
	defer server.Close()
	f, err := newHomeForwarder(server.Listener.Addr().String(), home.cert)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	// Model a public-equivalent issuer in the local client pool only. The
	// production constructor contains exactly the ephemeral issuer.
	f.localRoots.AddCert(public.cert)
	trapIP := netip.AddrFrom4([4]byte{127, byte(os.Getpid() >> 16), byte(os.Getpid() >> 8), byte(os.Getpid())})
	trapListener, err := net.Listen("tcp4", net.JoinHostPort(trapIP.String(), "28443"))
	if err != nil {
		t.Fatal(err)
	}
	var directDials atomic.Int32
	trap := httptest.NewUnstartedServer(http.NotFoundHandler())
	trap.Listener.Close()
	trap.Listener = trapListener
	trap.TLS = &tls.Config{Certificates: []tls.Certificate{leaf}}
	trap.Config.ErrorLog = log.New(io.Discard, "", 0)
	trap.Config.ConnState = func(_ net.Conn, state http.ConnState) {
		if state == http.StateNew {
			directDials.Add(1)
		}
	}
	trap.StartTLS()
	defer trap.Close()
	defer func() {
		if directDials.Load() != 0 {
			t.Errorf("direct fallback reached independent TCP decoy: %d", directDials.Load())
		}
	}()
	if err := installHomeProcess(f, trapIP); err != nil {
		t.Fatal(err)
	}
	if profile == "proxy-unavailable" {
		f.Close()
	}
	if path == "bootstrap" {
		dialer := tsdial.NewDialer(netmon.NewStatic())
		defer dialer.Close()
		client, err := controlclient.NewDirect(controlclient.Options{ServerURL: homeURL, Hostinfo: &tailcfg.Hostinfo{BackendLogID: "synthetic-backend"}, AuthKey: "synthetic-join-canary", GetMachinePrivateKey: func() (key.MachinePrivate, error) { return machine, nil }, Dialer: dialer, Logf: func(string, ...any) {}})
		if err != nil {
			t.Fatal(err)
		}
		defer client.Close()
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		_, loginErr := client.TryLogin(ctx, controlclient.LoginDefault)
		cancel()
		if valid {
			if loginErr != nil || keyHits.Load() != 1 || registrations.Load() != 1 || canaries.Load() != 1 {
				t.Fatalf("valid login err=%v key=%d registration=%d canary=%d", loginErr, keyHits.Load(), registrations.Load(), canaries.Load())
			}
		} else {
			if profile == "wrong-noise" && noiseAttempts.Load() == 0 {
				t.Fatal("wrong Noise key never reached the Noise handshake")
			}
			wantKey := int32(0)
			if profile == "wrong-noise" || profile == "outer-forged" || profile == "redirect" {
				wantKey = 1
			}
			if loginErr == nil || keyHits.Load() != wantKey || registrations.Load() != 0 || canaries.Load() != 0 {
				t.Fatalf("refusal violated err=%v key=%d registration=%d canary=%d", loginErr, keyHits.Load(), registrations.Load(), canaries.Load())
			}
		}
		t.Logf("profile=%s /key=%d registration=%d canary=%d", profile, keyHits.Load(), registrations.Load(), canaries.Load())
		return
	}

	region := &tailcfg.DERPRegion{RegionID: 1, Nodes: []*tailcfg.DERPNode{{Name: "home", RegionID: 1, HostName: homeName, IPv4: trapIP.String(), IPv6: "none", DERPPort: 28443, STUNPort: -1}}}
	dc := derphttp.NewRegionClient(key.NewNode(), func(string, ...any) {}, netmon.NewStatic(), func() *tailcfg.DERPRegion { return region })
	defer dc.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	derpErr := dc.Connect(ctx)
	cancel()
	if profile == "renewed-leaf" && derpErr == nil {
		dc.Close()
		rotateLeaf.Store(true)
		dc = derphttp.NewRegionClient(key.NewNode(), func(string, ...any) {}, netmon.NewStatic(), func() *tailcfg.DERPRegion { return region })
		defer dc.Close()
		ctx, cancel = context.WithTimeout(context.Background(), 3*time.Second)
		derpErr = dc.Connect(ctx)
		cancel()
	}
	if (derpErr == nil) != (valid || profile == "wrong-noise") {
		t.Fatalf("DERP outcome profile=%s err=%v", profile, derpErr)
	}
	if valid {
		up, _ := x509.ParseCertificate(leaf.Certificate[0])
		local, _ := x509.ParseCertificate(f.leaf.Certificate[0])
		_, verifyErr := local.Verify(x509.VerifyOptions{Roots: f.localRoots, DNSName: homeName})
		if homeFingerprint(up) == homeFingerprint(local) || verifyErr != nil {
			t.Fatal("local leaf did not re-present ephemeral issuer")
		}
	}
	t.Logf("profile=%s /key=%d registration=%d canary=%d DERP=%t", profile, keyHits.Load(), registrations.Load(), canaries.Load(), derpErr == nil)
}

func TestHomeVerifierFailureClosesStreams(t *testing.T) {
	ca := makeHomeCA(t, false)
	leaf := ca.leaf(t, "valid")
	forged := makeHomeCA(t, false).leaf(t, "valid")
	var fail atomic.Bool
	up := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(200)
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	up.TLS = &tls.Config{GetCertificate: func(*tls.ClientHelloInfo) (*tls.Certificate, error) {
		if fail.Load() {
			return &forged, nil
		}
		return &leaf, nil
	}}
	up.Config.ErrorLog = log.New(io.Discard, "", 0)
	up.StartTLS()
	defer up.Close()
	f, err := newHomeForwarder(up.Listener.Addr().String(), ca.cert)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	connect := func() net.Conn {
		c, err := net.Dial("tcp", f.listener.Addr().String())
		if err != nil {
			t.Fatal(err)
		}
		c.SetDeadline(time.Now().Add(2 * time.Second))
		fmt.Fprintf(c, "CONNECT %s:28443 HTTP/1.1\r\nHost: %s:28443\r\n\r\n", homeName, homeName)
		return c
	}
	first := connect()
	defer first.Close()
	if res, err := http.ReadResponse(bufio.NewReader(first), nil); err != nil || res.StatusCode != 200 {
		t.Fatal(res, err)
	}
	secure := tls.Client(first, &tls.Config{ServerName: homeName, RootCAs: f.localRoots})
	if err := secure.Handshake(); err != nil {
		t.Fatal(err)
	}
	fmt.Fprintf(secure, "GET /hold HTTP/1.1\r\nHost: %s\r\n\r\n", homeName)
	reader := bufio.NewReader(secure)
	if res, err := http.ReadResponse(reader, nil); err != nil || res.StatusCode != 200 {
		t.Fatal(res, err)
	}
	fail.Store(true)
	second := connect()
	defer second.Close()
	if res, err := http.ReadResponse(bufio.NewReader(second), nil); err == nil {
		t.Fatal("unverified upstream got CONNECT response", res.StatusCode)
	}
	select {
	case <-f.done:
	case <-time.After(time.Second):
		t.Fatal("verifier failure did not close owned admission/streams")
	}
	if _, err := reader.ReadByte(); err == nil {
		t.Fatal("existing stream survived verifier failure")
	}
	f.mu.Lock()
	count := len(f.streams)
	f.mu.Unlock()
	if count != 0 {
		t.Fatal("stream residue", count)
	}
}

func TestHomeConfigAdmission(t *testing.T) {
	for _, scenario := range []string{"valid", "missing-trust", "public-ip", "ipv6", "loopback", "bad-fingerprint", "wrong-url", "old-home-port", "legacy-profile"} {
		t.Run(scenario, func(t *testing.T) {
			c := validConfig(t)
			c.Profile = LinuxIdentityProfile
			c.ExpectedPeer = ""
			c.ConnectorAddr = ""
			c.ControlURL = homeURL
			c.ControlTLS = &HomeControlTLS{DialIP: "192.168.1.2", CAFingerprint: strings.Repeat("a", 64)}
			switch scenario {
			case "missing-trust":
				c.ControlTLS = nil
			case "public-ip":
				c.ControlTLS.DialIP = "8.8.8.8"
			case "ipv6":
				c.ControlTLS.DialIP = "fd00::1"
			case "loopback":
				c.ControlTLS.DialIP = "127.0.0.1"
			case "bad-fingerprint":
				c.ControlTLS.CAFingerprint = "unexpected"
			case "wrong-url":
				c.ControlURL = "https://attacker.invalid:28443"
			case "old-home-port":
				c.ControlURL = "https://control.happyranch.invalid:8443"
			case "legacy-profile":
				c.Profile = ""
			}
			if (c.Validate() == nil) != (scenario == "valid") {
				t.Fatal("home config admission differs", scenario)
			}
		})
	}
}

func TestHomeConnectAdmissionAndClose(t *testing.T) {
	ca := makeHomeCA(t, false)
	var upstreamDials atomic.Int32
	up := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { io.WriteString(w, "ok") }))
	up.TLS = &tls.Config{Certificates: []tls.Certificate{ca.leaf(t, "valid")}}
	up.Config.ErrorLog = log.New(io.Discard, "", 0)
	up.Config.ConnState = func(_ net.Conn, s http.ConnState) {
		if s == http.StateNew {
			upstreamDials.Add(1)
		}
	}
	up.StartTLS()
	defer up.Close()
	f, err := newHomeForwarder(up.Listener.Addr().String(), ca.cert)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	for _, request := range []string{"GET http://" + homeName + "/ts2021 HTTP/1.1\r\nHost: " + homeName + "\r\n\r\n", "CONNECT attacker.invalid:443 HTTP/1.1\r\nHost: attacker.invalid:443\r\n\r\n", "CONNECT " + homeName + ":28443 HTTP/1.1\r\nBad Header: x\r\n\r\n", strings.Repeat("X", 8192)} {
		c, err := net.Dial("tcp", f.listener.Addr().String())
		if err != nil {
			t.Fatal(err)
		}
		c.SetDeadline(time.Now().Add(time.Second))
		io.WriteString(c, request)
		if _, err := bufio.NewReader(c).ReadByte(); err == nil {
			t.Fatal("invalid CONNECT accepted")
		}
		c.Close()
	}
	if upstreamDials.Load() != 0 {
		t.Fatal("invalid target caused upstream dial")
	}
	c, err := net.Dial("tcp", f.listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	fmt.Fprintf(c, "CONNECT %s:443 HTTP/1.1\r\nHost: %s:443\r\n\r\n", homeName, homeName)
	res, err := http.ReadResponse(bufio.NewReader(c), nil)
	if err != nil || res.StatusCode != 200 {
		t.Fatal(res, err)
	}
	// Close while local TLS handshake is pending; both raw sockets must close.
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
	c.SetReadDeadline(time.Now().Add(time.Second))
	if _, err := c.Read(make([]byte, 1)); err == nil {
		t.Fatal("owned stream survived Close")
	}
	c.Close()
	if _, err := net.DialTimeout("tcp", f.listener.Addr().String(), time.Second); err == nil {
		t.Fatal("listener survived Close")
	}
	f.mu.Lock()
	count := len(f.streams)
	f.mu.Unlock()
	if count != 0 {
		t.Fatal("owned stream residue", count)
	}
}
