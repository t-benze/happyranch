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
	"io"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"os/exec"
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
	leaf := &x509.Certificate{SerialNumber: big.NewInt(2), Subject: pkix.Name{CommonName: "Synthetic Home Leaf"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, IPAddresses: []net.IP{net.ParseIP("127.0.0.1")}}
	switch scenario {
	case "expired":
		leaf.NotAfter = time.Now().Add(-time.Minute)
	case "wrong-san":
		leaf.IPAddresses = []net.IP{net.ParseIP("192.168.1.99")}
	case "missing-san":
		leaf.IPAddresses = nil
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
	if mode == "startup" || mode == "once" {
		os.Args = []string{"bridge", "--home-bridge-v1"}
		helperMain()
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
	endpoint, _ := url.Parse(p.URL)
	roots, err := homeRoots(p.CA, p.Fingerprint, endpoint.Hostname(), time.Now())
	if err != nil {
		os.Exit(34)
	}
	x509.SetFallbackRoots(roots)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if p.Kind == "derp" {
		client, err := derphttp.NewClient(key.NewNode(), p.URL, func(string, ...any) {}, netmon.NewStatic())
		if err != nil {
			os.Exit(35)
		}
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
		client, err := controlclient.NewDirect(controlclient.Options{ServerURL: p.URL, Dialer: dialer, Hostinfo: &tailcfg.Hostinfo{BackendLogID: "synthetic-test"}, GetMachinePrivateKey: func() (key.MachinePrivate, error) { return machine, nil }, Logf: func(string, ...any) {}})
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
	cfg := homeLaunch{CA: ca.der, Fingerprint: homeFingerprint(ca.der), ControlURL: "https://192.168.1.40:8443", Hostname: "synthetic-mac", StateDirectory: "/invalid-profile", Target: "100.64.0.1:443", SocketPath: "/invalid/socket"}
	for _, name := range []string{"empty", "multiple", "invalid", "not-ca", "fingerprint", "expired-ca", "environment", "secret-redaction", "http"} {
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
			case "environment":
				debug = "x509usefallbackroots=0"
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
	if got := runHomeChild(t, "once", homeFrame(t, cfg), "x509usefallbackroots=1"); got != "-16\nROOTS_ONCE\n" {
		t.Fatal("roots not installed exactly once before engine admission")
	}
}
func TestHomeBootstrapTLSAndNoiseBinding(t *testing.T) {
	for _, scenario := range []string{"valid", "forged-ca", "expired", "wrong-san", "missing-san", "wrong-noise-key"} {
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
	for _, scenario := range []string{"valid", "forged-ca", "expired", "wrong-san", "missing-san"} {
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
