package main

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
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
	"tailscale.com/envknob"
	"tailscale.com/net/dnscache"
	"tailscale.com/net/netmon"
	"tailscale.com/net/tsdial"
	"tailscale.com/net/tshttpproxy"
	"tailscale.com/tailcfg"
	"tailscale.com/types/key"
)

type homeTestCA struct {
	der  []byte
	cert *x509.Certificate
	key  *ecdsa.PrivateKey
}

func makeHomeCA(t *testing.T, isCA bool) homeTestCA {
	t.Helper()
	k, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal("synthetic key failed")
	}
	c := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Synthetic Home CA"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), BasicConstraintsValid: true, IsCA: isCA, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature}
	der, err := x509.CreateCertificate(rand.Reader, c, c, &k.PublicKey, k)
	if err != nil {
		t.Fatal("synthetic CA failed")
	}
	parsed, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal("synthetic CA parse failed")
	}
	return homeTestCA{der, parsed, k}
}
func homeFingerprint(der []byte) string { sum := sha256.Sum256(der); return hex.EncodeToString(sum[:]) }
func homeLeaf(t *testing.T, ca homeTestCA, scenario string) tls.Certificate {
	t.Helper()
	k, _ := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	leaf := &x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "Synthetic Home Leaf"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, DNSNames: []string{homeControlName}}
	switch scenario {
	case "expired":
		leaf.NotAfter = time.Now().Add(-time.Minute)
	case "wrong-san":
		leaf.DNSNames = []string{"wrong.invalid"}
	case "missing-san":
		leaf.DNSNames = nil
	case "ip-only":
		leaf.DNSNames = nil
		leaf.IPAddresses = []net.IP{net.ParseIP("127.0.0.1")}
	}
	der, err := x509.CreateCertificate(rand.Reader, leaf, ca.cert, &k.PublicKey, ca.key)
	if err != nil {
		t.Fatal("synthetic leaf failed")
	}
	return tls.Certificate{Certificate: [][]byte{der, ca.der}, PrivateKey: k}
}

type homeTLSProbe struct {
	CA                     []byte
	Fingerprint, URL, Kind string
}

// Test-side process isolation only; production exports no test flag or hook.
func TestHomeProcessChild(t *testing.T) {
	mode := os.Getenv("HOME_BRIDGE_TEST_MODE")
	if mode == "" {
		return
	}
	log.SetOutput(io.Discard)
	if mode == "environment" {
		cfg, err := readHomeLaunch(os.Stdin)
		if err != nil {
			os.Exit(40)
		}
		h, err := prepareHomeProcess(cfg)
		if err != nil {
			os.Exit(41)
		}
		expectedProxy := "http://" + h.Addr().String()
		req, _ := http.NewRequest("GET", homeControlURL+"/key", nil)
		for _, proxyFunc := range []func(*http.Request) (*url.URL, error){http.ProxyFromEnvironment, tshttpproxy.ProxyFromEnvironment} {
			proxy, err := proxyFunc(req)
			if err != nil || proxy == nil || proxy.String() != expectedProxy {
				os.Exit(42)
			}
		}
		if os.Getenv("GODEBUG") != "x509usefallbackroots=1" {
			os.Exit(43)
		}
		for _, name := range []string{"http_proxy", "https_proxy", "ALL_PROXY", "NO_PROXY", "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR", "TS_DEBUG_TLS_DIAL"} {
			if _, ok := os.LookupEnv(name); ok {
				os.Exit(44)
			}
		}
		issuer, err := x509.ParseCertificate(h.LocalCA())
		if err != nil {
			os.Exit(61)
		}
		pool, err := x509.SystemCertPool()
		if err != nil || len(pool.Subjects()) != 1 || !bytes.Equal(pool.Subjects()[0], issuer.RawSubject) {
			os.Exit(62)
		}
		remote, err := x509.ParseCertificate(cfg.CA)
		if err != nil || bytes.Equal(remote.RawSubject, issuer.RawSubject) || len(h.upstreamTLS.RootCAs.Subjects()) != 1 || !bytes.Equal(h.upstreamTLS.RootCAs.Subjects()[0], remote.RawSubject) {
			os.Exit(63)
		}
		if envknob.RegisterBool("TS_DEBUG_TLS_DIAL")() {
			os.Exit(64)
		}
		ctx, cancel := context.WithTimeout(context.Background(), time.Second)
		defer cancel()
		ips, err := net.DefaultResolver.LookupHost(ctx, homeControlName)
		if err != nil || len(ips) != 1 || ips[0] != cfg.DialIP {
			os.Exit(45)
		}
		ip, _, _, err := dnscache.Get().LookupIP(ctx, homeControlName)
		if err != nil || ip.String() != cfg.DialIP {
			os.Exit(46)
		}
		if ips, err := net.DefaultResolver.LookupHost(ctx, "other.invalid"); err == nil || len(ips) != 0 {
			os.Exit(47)
		}
		if _, err := prepareHomeProcess(cfg); err == nil {
			os.Exit(48)
		}
		issuerDER := h.LocalCA()
		h.Close()
		if len(h.LocalCA()) != 0 || len(h.localTLS.Certificates) != 0 {
			os.Exit(49)
		}
		// Fresh processes must own fresh ports and issuers.
		fmt.Fprintln(os.Stdout, h.Addr().String(), homeFingerprint(issuerDER))
		os.Exit(0)
	}
	if mode == "startup" || mode == "once" {
		os.Args = []string{"bridge", "--home-bridge-v2"}
		helperMain()
		if mode == "startup" {
			installed := false
			func() { defer func() { installed = recover() != nil }(); x509.SetFallbackRoots(x509.NewCertPool()) }()
			if installed {
				os.Exit(65)
			} // rejected input must not have installed roots
		}
		if mode == "once" {
			pool, err := x509.SystemCertPool()
			if err != nil || len(pool.Subjects()) != 1 {
				os.Exit(31)
			}
			panicked := false
			func() { defer func() { panicked = recover() != nil }(); x509.SetFallbackRoots(x509.NewCertPool()) }()
			if !panicked {
				os.Exit(32)
			}
			io.WriteString(os.Stdout, "ROOTS_ONCE\n")
		}
		os.Exit(0)
	}
	var p homeTLSProbe
	if json.NewDecoder(os.Stdin).Decode(&p) != nil {
		os.Exit(33)
	}
	cfg := homeLaunchFixture(p.CA)
	cfg.Fingerprint = p.Fingerprint
	h, err := prepareHomeProcess(cfg)
	if err != nil {
		os.Exit(34)
	}
	defer h.Close()
	// Independent endpoint assertion precedes test-side isolation to owned fake.
	if h.upstream != "192.168.1.40:8443" {
		os.Exit(37)
	}
	h.upstream = strings.TrimPrefix(p.URL, "https://")
	h.upstream = strings.TrimSuffix(h.upstream, "/derp")
	if h.upstream == "" {
		os.Exit(38)
	}
	if strings.HasPrefix(p.Kind, "teardown-") {
		cfg.StateDirectory = profilePath(t)
		cfg.JoinKey = strings.Repeat("ab", 24)
		if initHomeProfile(cfg) != 0 {
			os.Exit(51)
		}
		owner := managed
		// The exported stream table must close too, independently of proxy TLS.
		owned, observer := net.Pipe()
		connMu.Lock()
		connTable[12345] = owned
		connMu.Unlock()
		defer observer.Close()
		raw, reader, status := connectTransport(t, h, homeControlName+":8443")
		if p.Kind == "teardown-verifier" {
			if !strings.Contains(status, " 502 ") {
				os.Exit(52)
			}
		} else {
			if !strings.Contains(status, " 200 ") {
				os.Exit(53)
			}
			c := localTransportTLS(t, h, raw, reader)
			if _, err := c.Write([]byte("live")); err != nil {
				os.Exit(54)
			}
			if _, err := io.ReadFull(c, make([]byte, 4)); err != nil {
				os.Exit(55)
			}
			if p.Kind == "teardown-listener" {
				h.listener.Close()
			} else {
				h.cancel()
			}
			_ = c.SetReadDeadline(time.Now().Add(time.Second))
			if _, err := c.Read(make([]byte, 1)); err == nil {
				os.Exit(56)
			}
			c.Close()
		}
		select {
		case <-owner.ctx.Done():
		case <-time.After(time.Second):
			os.Exit(57)
		}
		srvMu.Lock()
		admitted := owner.ready
		srvMu.Unlock()
		if admitted {
			os.Exit(58)
		}
		_ = observer.SetReadDeadline(time.Now().Add(time.Second))
		if _, err := observer.Read(make([]byte, 1)); err != io.EOF {
			os.Exit(59)
		}
		h.Close()
		if len(h.LocalCA()) != 0 || len(h.localTLS.Certificates) != 0 {
			os.Exit(60)
		}
		tsnet_close()
		io.WriteString(os.Stdout, "TEARDOWN\n")
		os.Exit(0)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if p.Kind == "derp" {
		client := derphttp.NewRegionClient(key.NewNode(), func(string, ...any) {}, netmon.NewStatic(), func() *tailcfg.DERPRegion {
			return &tailcfg.DERPRegion{RegionID: 1, Nodes: []*tailcfg.DERPNode{{Name: "synthetic-home", RegionID: 1, HostName: homeControlName, IPv4: cfg.DialIP, IPv6: "none", DERPPort: 8443, STUNPort: 3478}}}
		})
		defer client.Close()
		if client.Connect(ctx) != nil {
			io.WriteString(os.Stdout, "REFUSED\n")
		} else {
			io.WriteString(os.Stdout, "ACCEPTED\n")
		}
	} else {
		dialer := tsdial.NewDialer(netmon.NewStatic())
		defer dialer.Close()
		machine := key.NewMachine()
		client, err := controlclient.NewDirect(controlclient.Options{ServerURL: homeControlURL, Dialer: dialer, Hostinfo: &tailcfg.Hostinfo{BackendLogID: "synthetic-test"}, GetMachinePrivateKey: func() (key.MachinePrivate, error) { return machine, nil }, Logf: func(string, ...any) {}})
		if err != nil {
			os.Exit(36)
		}
		defer client.Close()
		_, _ = client.TryLogin(ctx, 0)
		io.WriteString(os.Stdout, "FINISHED\n")
	}
	os.Exit(0)
}
func runHomeChild(t *testing.T, mode string, payload []byte, debug string) string {
	t.Helper()
	cmd := exec.Command(os.Args[0], "-test.run=^TestHomeProcessChild$")
	cmd.Env = []string{"GODEBUG=" + debug, "HOME_BRIDGE_TEST_MODE=" + mode}
	cmd.Stdin = bytes.NewReader(payload)
	out, err := cmd.CombinedOutput()
	if err != nil {
		t.Fatalf("isolated home probe failed; output withheld; exit=%d", cmd.ProcessState.ExitCode())
	}
	for _, canary := range []string{"RAW-INVITE-CANARY", "JOIN-KEY-CANARY", "PAIR-SECRET-CANARY", "COMPLETION-HANDLE-CANARY", "BEARER-CANARY"} {
		if bytes.Contains(out, []byte(canary)) {
			t.Fatal("secret canary leaked")
		}
	}
	return string(out)
}
func homeFrame(t *testing.T, cfg homeLaunch) []byte {
	t.Helper()
	data, err := json.Marshal(cfg)
	if err != nil {
		t.Fatal("frame failed")
	}
	var out bytes.Buffer
	binary.Write(&out, binary.BigEndian, uint32(len(data)))
	out.Write(data)
	return out.Bytes()
}
func TestHomeStartupRefusalsAndRootsBeforeEngine(t *testing.T) {
	ca := makeHomeCA(t, true)
	cfg := homeLaunchFixture(ca.der)
	for _, name := range []string{"empty", "multiple", "invalid", "not-ca", "fingerprint", "expired-ca", "secret-redaction", "http", "ip-url", "v1", "public-dial-ip"} {
		t.Run(name, func(t *testing.T) {
			c := cfg
			debug := "x509usefallbackroots=1"
			switch name {
			case "empty":
				c.CA = nil
			case "multiple":
				c.CA = append(append([]byte{}, ca.der...), ca.der...)
			case "invalid":
				c.CA = []byte("invalid DER")
			case "not-ca":
				c.CA = makeHomeCA(t, false).der
				c.Fingerprint = homeFingerprint(c.CA)
			case "fingerprint":
				c.Fingerprint = strings.Repeat("0", 64)
			case "expired-ca":
				expired := *ca.cert
				expired.NotAfter = time.Now().Add(-time.Minute)
				c.CA, _ = x509.CreateCertificate(rand.Reader, &expired, ca.cert, &ca.key.PublicKey, ca.key)
				c.Fingerprint = homeFingerprint(c.CA)
			case "ip-url":
				c.ControlURL = "https://192.168.1.40:8443"
			case "v1":
				c.Version = 1
			case "public-dial-ip":
				c.DialIP = "8.8.8.8"
			case "secret-redaction":
				c.JoinKey = "JOIN-KEY-CANARY"
				c.CA = []byte("RAW-INVITE-CANARY PAIR-SECRET-CANARY COMPLETION-HANDLE-CANARY BEARER-CANARY")
			case "http":
				c.ControlURL = "http://192.168.1.40:8443"
			}
			if got := runHomeChild(t, "startup", homeFrame(t, c), debug); got != "-20\n" {
				t.Fatal("invalid home configuration reached engine admission")
			}
		})
	}
	// Valid SAN-less CA reaches profile admission; invalid path prevents networking.
	if got := runHomeChild(t, "once", homeFrame(t, cfg), "x509usefallbackroots=0,http2debug=2"); got != "-16\nROOTS_ONCE\n" {
		t.Fatal("roots not installed exactly once before engine admission")
	}
}
func TestHomeBootstrapTLSAndNoiseBinding(t *testing.T) {
	for _, scenario := range []string{"valid", "forged-ca", "expired", "wrong-san", "missing-san", "ip-only", "wrong-noise-key"} {
		t.Run(scenario, func(t *testing.T) {
			ca := makeHomeCA(t, true)
			signer := ca
			if scenario == "forged-ca" {
				signer = makeHomeCA(t, true)
			}
			noise := key.NewMachine()
			advertised := noise.Public()
			if scenario == "wrong-noise-key" {
				advertised = key.NewMachine().Public()
			}
			var keys, registered atomic.Int32
			h2 := &http2.Server{}
			server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/key" {
					keys.Add(1)
					json.NewEncoder(w).Encode(tailcfg.OverTLSPublicKeyResponse{PublicKey: advertised, LegacyPublicKey: noise.Public()})
					return
				}
				conn, err := controlhttpserver.AcceptHTTP(r.Context(), w, r, noise, nil)
				if err != nil {
					return
				}
				defer conn.Close()
				h2.ServeConn(conn, &http2.ServeConnOpts{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { registered.Add(1); io.WriteString(w, "{}") })})
			}))
			server.Config.ErrorLog = log.New(io.Discard, "", 0)
			server.TLS = &tls.Config{Certificates: []tls.Certificate{homeLeaf(t, signer, scenario)}}
			server.StartTLS()
			defer server.Close()
			payload, _ := json.Marshal(homeTLSProbe{ca.der, homeFingerprint(ca.der), server.URL, "bootstrap"})
			runHomeChild(t, "tls", payload, "x509usefallbackroots=1")
			if scenario == "valid" || scenario == "wrong-noise-key" {
				if keys.Load() == 0 {
					t.Fatal("pinned valid bootstrap refused")
				}
			} else if keys.Load() != 0 {
				t.Fatal("invalid leaf reached /key")
			}
			if scenario == "valid" {
				if registered.Load() == 0 {
					t.Fatal("authenticated Noise registration did not reach server")
				}
			} else if registered.Load() != 0 {
				t.Fatal("invalid TLS or Noise key reached registration")
			}
		})
	}
}
func TestHomeDERPTLSSeparateFromNoise(t *testing.T) {
	for _, scenario := range []string{"valid", "forged-ca", "expired", "wrong-san", "missing-san", "ip-only"} {
		t.Run(scenario, func(t *testing.T) {
			ca := makeHomeCA(t, true)
			signer := ca
			if scenario == "forged-ca" {
				signer = makeHomeCA(t, true)
			}
			relay := derp.NewServer(key.NewNode(), func(string, ...any) {})
			defer relay.Close()
			server := httptest.NewUnstartedServer(derphttp.Handler(relay))
			server.Config.ErrorLog = log.New(io.Discard, "", 0)
			server.TLS = &tls.Config{Certificates: []tls.Certificate{homeLeaf(t, signer, scenario)}}
			server.StartTLS()
			defer server.Close()
			payload, _ := json.Marshal(homeTLSProbe{ca.der, homeFingerprint(ca.der), server.URL + "/derp", "derp"})
			got := runHomeChild(t, "tls", payload, "x509usefallbackroots=1")
			want := "REFUSED\n"
			if scenario == "valid" {
				want = "ACCEPTED\n"
			}
			if got != want {
				t.Fatal("DERP TLS acceptance differs from pinned trust contract")
			}
		})
	}
}

func TestHomeSameCARenewalAcrossRestart(t *testing.T) {
	ca := makeHomeCA(t, true)
	var previous []byte
	for generation := 0; generation < 2; generation++ {
		// New leaf/key and a restarted server/helper, with the SAME saved CA.
		leaf := homeLeaf(t, ca, "valid")
		if bytes.Equal(previous, leaf.Certificate[0]) {
			t.Fatal("renewal fixture reused the old leaf")
		}
		previous = leaf.Certificate[0]
		relay := derp.NewServer(key.NewNode(), func(string, ...any) {})
		server := httptest.NewUnstartedServer(derphttp.Handler(relay))
		server.Config.ErrorLog = log.New(io.Discard, "", 0)
		server.TLS = &tls.Config{Certificates: []tls.Certificate{leaf}}
		server.StartTLS()
		payload, err := json.Marshal(homeTLSProbe{ca.der, homeFingerprint(ca.der), server.URL + "/derp", "derp"})
		if err != nil {
			t.Fatal("synthetic probe serialization failed")
		}
		got := runHomeChild(t, "tls", payload, "x509usefallbackroots=1")
		server.Close()
		relay.Close()
		if got != "ACCEPTED\n" {
			t.Fatal("same saved CA refused renewed leaf after restart")
		}
	}
}

func homeLaunchFixture(der []byte) homeLaunch {
	return homeLaunch{Version: 2, CA: der, Fingerprint: homeFingerprint(der),
		ControlURL: homeControlURL, DialIP: "192.168.1.40", HomeID: "synthetic-home", ControlPlaneID: "synthetic-control",
		Hostname: "synthetic-mac", StateDirectory: "/invalid-profile", Target: "100.64.0.1:443", SocketPath: "/invalid/socket"}
}

func TestHomeProcessEnvironmentResolverAndIsolation(t *testing.T) {
	ca := makeHomeCA(t, true)
	var previous []string
	for generation := 0; generation < 2; generation++ {
		cmd := exec.Command(os.Args[0], "-test.run=^TestHomeProcessChild$")
		cmd.Env = []string{"HOME_BRIDGE_TEST_MODE=environment", "GODEBUG=x509usefallbackroots=0,http2debug=2",
			"HTTP_PROXY=http://127.0.0.1:1", "HTTPS_PROXY=http://127.0.0.1:1", "http_proxy=http://127.0.0.1:2", "https_proxy=http://127.0.0.1:2",
			"ALL_PROXY=http://127.0.0.1:3", "all_proxy=http://127.0.0.1:3", "NO_PROXY=*", "no_proxy=*",
			"SSL_CERT_FILE=/invalid", "SSL_CERT_DIR=/invalid", "TS_DEBUG_TLS_DIAL=true"}
		cmd.Stdin = bytes.NewReader(homeFrame(t, homeLaunchFixture(ca.der)))
		out, err := cmd.CombinedOutput()
		if err != nil {
			t.Fatalf("process configuration failed; output withheld; exit=%d", cmd.ProcessState.ExitCode())
		}
		current := strings.Fields(string(out))
		if len(current) != 2 {
			t.Fatal("missing process isolation receipt")
		}
		if previous != nil && (previous[0] == current[0] || previous[1] == current[1]) {
			t.Fatal("restart reused local endpoint or issuer")
		}
		previous = current
	}
}

func TestHomeLaunchFrameClosed(t *testing.T) {
	cfg := homeLaunchFixture(makeHomeCA(t, true).der)
	good, _ := json.Marshal(cfg)
	for _, scenario := range []string{"valid", "old-ca-field", "extra-trust", "duplicate", "case-alias", "trailing", "oversize", "truncated", "nested-trust"} {
		t.Run(scenario, func(t *testing.T) {
			data := append([]byte(nil), good...)
			switch scenario {
			case "old-ca-field":
				data = bytes.Replace(data, []byte(`"caDER"`), []byte(`"ca"`), 1)
			case "extra-trust":
				data = append(data[:len(data)-1], []byte(`,"roots":[]}`)...)
			case "duplicate":
				data = append(data[:len(data)-1], []byte(`,"version":2}`)...)
			case "case-alias":
				data = bytes.Replace(data, []byte(`"dialIP"`), []byte(`"DialIP"`), 1)
			case "trailing":
				data = append(data, []byte(`{}`)...)
			case "oversize":
				data = make([]byte, 16385)
			case "nested-trust":
				data = append(data[:len(data)-1], []byte(`,"recovery":{"previousDialIP":"192.168.1.39","stableNodeID":"synthetic","bearer":"synthetic","roots":[]}}`)...)
			}
			var frame bytes.Buffer
			binary.Write(&frame, binary.BigEndian, uint32(len(data)))
			frame.Write(data)
			raw := frame.Bytes()
			if scenario == "truncated" {
				raw = raw[:len(raw)-1]
			}
			_, err := readHomeLaunch(bytes.NewReader(raw))
			if (err == nil) != (scenario == "valid") {
				t.Error("launch schema/framing admission mismatch")
			}
		})
	}
}

func TestHomeProcessTeardown(t *testing.T) {
	for _, cause := range []string{"listener", "cancel", "verifier"} {
		t.Run(cause, func(t *testing.T) {
			ca := makeTransportCA(t, true)
			signer := ca
			if cause == "verifier" {
				signer = makeTransportCA(t, true)
			}
			peer := startTransportPeer(t, transportLeaf(t, signer, "valid"), echoTransport)
			defer peer.close()
			payload, _ := json.Marshal(homeTLSProbe{ca.der, transportFingerprint(ca.der), "https://" + peer.listener.Addr().String(), "teardown-" + cause})
			if got := runHomeChild(t, "tls", payload, ""); got != "TEARDOWN\n" {
				t.Fatal("process failure did not tear down admission and owned streams")
			}
		})
	}
}

func TestHomeCachedKeylogRefusal(t *testing.T) {
	path := filepath.Join(t.TempDir(), "must-not-exist")
	cmd := exec.Command(os.Args[0], "-test.run=^TestHomeProcessChild$")
	cmd.Env = []string{"HOME_BRIDGE_TEST_MODE=startup", "SSLKEYLOGFILE=" + path}
	cmd.Stdin = bytes.NewReader(homeFrame(t, homeLaunchFixture(makeHomeCA(t, true).der)))
	out, err := cmd.CombinedOutput()
	if err != nil || string(out) != "-20\n" {
		t.Fatal("cached keylog override reached root/engine admission")
	}
	if _, err := os.Lstat(path); !os.IsNotExist(err) {
		t.Fatal("keylog override created a file")
	}
}

// The private readiness response carries native Self identity, not product
// approval or any invite/credential. Invalid identity cannot create a frame.
func TestHomeReadyNativeIdentityFrame(t *testing.T) {
	var out bytes.Buffer
	if err := writeHomeReady(&out, "native-stable-node"); err != nil || out.String() != "0 native-stable-node\n" {
		t.Fatal("native readiness identity frame mismatch")
	}
	for _, node := range []string{"", "node with space", "node\nforged", "node\x00", strings.Repeat("n", 257), "nöde"} {
		out.Reset()
		if err := writeHomeReady(&out, node); err == nil || out.Len() != 0 {
			t.Fatal("invalid identity produced readiness bytes")
		}
	}
}
