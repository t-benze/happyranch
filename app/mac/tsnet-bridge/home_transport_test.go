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
	"encoding/hex"
	"encoding/pem"
	"errors"
	"io"
	"math/big"
	"net"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

type transportTestCA struct {
	der  []byte
	cert *x509.Certificate
	key  *ecdsa.PrivateKey
}

func makeTransportCA(t *testing.T, isCA bool) transportTestCA {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal("synthetic CA key failed")
	}
	cert := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "Synthetic transport CA"},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour), BasicConstraintsValid: true,
		IsCA: isCA, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature}
	der, err := x509.CreateCertificate(rand.Reader, cert, cert, &key.PublicKey, key)
	if err != nil {
		t.Fatal("synthetic CA failed")
	}
	parsed, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal("synthetic CA parse failed")
	}
	return transportTestCA{der, parsed, key}
}
func transportFingerprint(der []byte) string {
	sum := sha256.Sum256(der)
	return hex.EncodeToString(sum[:])
}

func transportLeaf(t *testing.T, ca transportTestCA, scenario string) tls.Certificate {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal("synthetic key failed")
	}
	c := &x509.Certificate{SerialNumber: big.NewInt(9), NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(time.Hour),
		DNSNames: []string{homeControlName}, KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	switch scenario {
	case "wildcard":
		c.DNSNames = []string{"*.happyranch.invalid"}
	case "wrong-name":
		c.DNSNames = []string{"other.invalid"}
	case "no-san":
		c.DNSNames = nil
	case "ip-only":
		c.DNSNames = nil
		c.IPAddresses = []net.IP{net.ParseIP("127.0.0.1")}
	case "expired":
		c.NotAfter = time.Now().Add(-time.Minute)
	case "client-auth":
		c.ExtKeyUsage = []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth}
	}
	der, err := x509.CreateCertificate(rand.Reader, c, ca.cert, &key.PublicKey, ca.key)
	if err != nil {
		t.Fatal("synthetic leaf failed")
	}
	return tls.Certificate{Certificate: [][]byte{der, ca.der}, PrivateKey: key}
}

func transportCAAt(t *testing.T, notBefore, notAfter time.Time) transportTestCA {
	t.Helper()
	ca := makeTransportCA(t, true)
	c := *ca.cert
	c.NotBefore, c.NotAfter = notBefore, notAfter
	der, err := x509.CreateCertificate(rand.Reader, &c, &c, &ca.key.PublicKey, ca.key)
	if err != nil {
		t.Fatal("synthetic CA failed")
	}
	ca.der = der
	ca.cert, err = x509.ParseCertificate(der)
	if err != nil {
		t.Fatal("synthetic CA parse failed")
	}
	return ca
}

func TestHomeTransportAdmission(t *testing.T) {
	now := time.Now()
	ca, forged, nonCA := makeTransportCA(t, true), makeTransportCA(t, true), makeTransportCA(t, false)
	expired := transportCAAt(t, now.Add(-2*time.Hour), now.Add(-time.Hour))
	future := transportCAAt(t, now.Add(time.Hour), now.Add(2*time.Hour))
	for _, ip := range []string{"10.0.0.1", "172.16.0.1", "172.31.255.254", "192.168.1.40"} {
		t.Run("valid-"+ip, func(t *testing.T) {
			roots, endpoint, err := admitHomeTransport(ca.der, transportFingerprint(ca.der), ip, now)
			if err != nil || endpoint != ip+":8443" || len(roots.Subjects()) != 1 {
				t.Fatal("valid home admission or fixed 8443 mapping failed")
			}
		})
	}
	cases := []struct {
		name   string
		der    []byte
		fp, ip string
	}{
		{"fingerprint", ca.der, transportFingerprint(forged.der), "192.168.1.40"},
		{"uppercase-fingerprint", ca.der, strings.ToUpper(transportFingerprint(ca.der)), "192.168.1.40"},
		{"forged-ca", forged.der, transportFingerprint(ca.der), "192.168.1.40"},
		{"not-ca", nonCA.der, transportFingerprint(nonCA.der), "192.168.1.40"},
		{"expired-ca", expired.der, transportFingerprint(expired.der), "192.168.1.40"},
		{"future-ca", future.der, transportFingerprint(future.der), "192.168.1.40"},
		{"two-der", append(append([]byte{}, ca.der...), ca.der...), "", "192.168.1.40"},
		{"trailing-der", append(append([]byte{}, ca.der...), 0), "", "192.168.1.40"},
		{"pem", pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: ca.der}), "", "192.168.1.40"},
	}
	for _, ip := range []string{"127.0.0.1", "0.0.0.0", "8.8.8.8", "100.64.0.1", "169.254.1.1", "172.32.0.1", "::1", "fc00::1", "::ffff:192.168.1.40", "control.happyranch.invalid", "192.168.1.40:8443", "192.168.001.40"} {
		cases = append(cases, struct {
			name   string
			der    []byte
			fp, ip string
		}{"ip-" + ip, ca.der, transportFingerprint(ca.der), ip})
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if tc.fp == "" {
				tc.fp = transportFingerprint(tc.der)
			}
			h, err := newHomeTransport(tc.der, tc.fp, tc.ip)
			if h != nil {
				h.Close()
				t.Fatal("invalid profile created a transport")
			}
			if err != errHomeTransport {
				t.Fatal("invalid profile did not return fixed refusal")
			}
		})
	}
}

// The production constructor always admits RFC1918:8443. Tests replace only an
// instance's private endpoint, before dialing, with an owned loopback
// fake. There is no production dialer hook, test flag or alternative API.
func transportForTest(t *testing.T, ca transportTestCA, endpoint string) *homeTransport {
	t.Helper()
	h, err := newHomeTransport(ca.der, transportFingerprint(ca.der), "192.168.1.40")
	if err != nil {
		t.Fatal("transport creation failed")
	}
	t.Cleanup(func() { h.Close() })
	if h.upstream != "192.168.1.40:8443" {
		t.Fatal("constructor lost admitted literal endpoint")
	}
	h.mu.Lock()
	h.upstream = endpoint
	h.mu.Unlock()
	return h
}

type transportPeer struct {
	streamEnded             chan struct{}
	listener                net.Listener
	wg                      sync.WaitGroup
	dials, applicationBytes atomic.Int64
	handshakes              atomic.Int64
	mu                      sync.Mutex
	conns                   []net.Conn
}

func startTransportPeer(t *testing.T, leaf tls.Certificate, serve func(*tls.Conn, *transportPeer)) *transportPeer {
	t.Helper()
	l, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal("fake listener failed")
	}
	p := &transportPeer{listener: l, streamEnded: make(chan struct{}, 128)}
	p.wg.Add(1)
	go func() {
		defer p.wg.Done()
		for {
			raw, err := l.Accept()
			if err != nil {
				return
			}
			p.dials.Add(1)
			p.mu.Lock()
			p.conns = append(p.conns, raw)
			p.mu.Unlock()
			p.wg.Add(1)
			go func() {
				defer p.wg.Done()
				defer func() { p.streamEnded <- struct{}{} }()
				defer raw.Close()
				_ = raw.SetDeadline(time.Now().Add(5 * time.Second))
				c := tls.Server(raw, &tls.Config{Certificates: []tls.Certificate{leaf}, MinVersion: tls.VersionTLS12})
				if c.Handshake() != nil {
					return
				}
				p.handshakes.Add(1)
				serve(c, p)
			}()
		}
	}()
	t.Cleanup(p.close)
	return p
}
func (p *transportPeer) close() {
	_ = p.listener.Close()
	// Accept may be adding its final connection; first let its loop terminate.
	// Closing current streams is safe; setup/stream test deadlines bound late ones.
	p.mu.Lock()
	for _, c := range p.conns {
		_ = c.Close()
	}
	p.mu.Unlock()
	p.wg.Wait()
}
func echoTransport(c *tls.Conn, p *transportPeer) {
	data := make([]byte, 4096)
	for {
		n, err := c.Read(data)
		p.applicationBytes.Add(int64(n))
		if n > 0 {
			if _, writeErr := c.Write(data[:n]); writeErr != nil {
				return
			}
		}
		if err != nil {
			_ = c.CloseWrite()
			return
		}
	}
}
func connectTransport(t *testing.T, h *homeTransport, authority string) (net.Conn, error) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	conn, err := h.DialContext(ctx, "tcp", authority)
	if conn != nil {
		_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
	}
	return conn, err
}
func localTransportTLS(t *testing.T, h *homeTransport, raw net.Conn) *tls.Conn {
	t.Helper()
	ca, err := x509.ParseCertificate(h.LocalCA())
	if err != nil {
		t.Fatal("local issuer invalid")
	}
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	c := tls.Client(raw, &tls.Config{RootCAs: roots, ServerName: homeControlName, MinVersion: tls.VersionTLS12})
	if c.Handshake() != nil {
		t.Fatal("local TLS failed")
	}
	return c
}

func TestHomeTransportEndToEnd(t *testing.T) {
	for _, authority := range []string{homeControlName + ":443", homeControlName + ":8443", "192.168.1.40:443", "192.168.1.40:8443"} {
		t.Run(authority, func(t *testing.T) {
			ca := makeTransportCA(t, true)
			leaf := transportLeaf(t, ca, "valid")
			p := startTransportPeer(t, leaf, echoTransport)
			h := transportForTest(t, ca, p.listener.Addr().String())
			raw, err := connectTransport(t, h, authority)
			if err != nil {
				t.Fatal("valid upstream refused")
			}
			c := localTransportTLS(t, h, raw)
			seen := c.ConnectionState().PeerCertificates
			if len(seen) != 1 || bytes.Equal(seen[0].Raw, leaf.Certificate[0]) || len(seen[0].DNSNames) != 1 || seen[0].DNSNames[0] != homeControlName || len(seen[0].IPAddresses) != 0 {
				t.Fatal("client did not see only the fixed-name ephemeral leaf")
			}
			if bytes.Equal(h.LocalCA(), ca.der) || len(c.ConnectionState().VerifiedChains) != 1 {
				t.Fatal("local and home trust collapsed")
			}
			// Arbitrary upgraded/Noise/DERP-shaped bytes travel simultaneously both ways.
			payload := append([]byte("GET /upgrade HTTP/1.1\r\nConnection: Upgrade\r\n\r\n"), bytes.Repeat([]byte{0, 255, 3, 10}, 32768)...)
			written := make(chan error, 1)
			go func() {
				_, err := c.Write(payload)
				if err == nil {
					err = c.CloseWrite()
				}
				written <- err
			}()
			got, err := io.ReadAll(c)
			if err != nil || !bytes.Equal(got, payload) || <-written != nil {
				t.Fatal("duplex/half-close relay changed bytes")
			}
			h.Close()
			p.close()
			if p.dials.Load() != 1 || p.handshakes.Load() != 1 || p.applicationBytes.Load() != int64(len(payload)) {
				t.Fatal("allowlisted authority did not use one verified admitted upstream")
			}
		})
	}
}

func TestHomeTransportLeafRefusals(t *testing.T) {
	for _, scenario := range []string{"public-matching", "public-wildcard", "public-unrelated", "wrong-name", "no-san", "ip-only", "forged-ca", "expired", "client-auth"} {
		t.Run(scenario, func(t *testing.T) {
			home, outside := makeTransportCA(t, true), makeTransportCA(t, true)
			signer, name := home, scenario
			switch scenario {
			case "public-matching":
				signer, name = outside, "valid"
			case "public-wildcard":
				signer, name = outside, "wildcard"
			case "public-unrelated":
				signer, name = outside, "wrong-name"
			case "forged-ca":
				signer, name = outside, "valid"
			}
			leaf := transportLeaf(t, signer, name)
			if strings.HasPrefix(scenario, "public-") {
				roots := x509.NewCertPool()
				roots.AddCert(outside.cert)
				cert, _ := x509.ParseCertificate(leaf.Certificate[0])
				ownName := homeControlName
				if scenario == "public-unrelated" {
					ownName = "other.invalid"
				}
				for _, dns := range []string{ownName, ""} {
					if _, err := cert.Verify(x509.VerifyOptions{Roots: roots, DNSName: dns}); err != nil {
						t.Fatal("public-equivalent fixture does not independently verify")
					}
				}
				homeRoots := x509.NewCertPool()
				homeRoots.AddCert(home.cert)
				if _, err := cert.Verify(x509.VerifyOptions{Roots: homeRoots}); err == nil {
					t.Fatal("public-equivalent fixture unexpectedly chains to home")
				}
				if scenario == "public-unrelated" && cert.VerifyHostname(homeControlName) == nil {
					t.Fatal("unrelated fixture matches home")
				}
			}
			p := startTransportPeer(t, leaf, echoTransport)
			h := transportForTest(t, home, p.listener.Addr().String())
			_, err := connectTransport(t, h, homeControlName+":8443")
			h.Close()
			p.close()
			if err == nil {
				t.Fatal("untrusted upstream returned a connection")
			}
			if p.dials.Load() != 1 || p.applicationBytes.Load() != 0 {
				t.Fatal("refusal did not reach upstream or leaked application bytes")
			}
		})
	}
}

type refusedForwarder struct{ calls atomic.Int64 }

func (d *refusedForwarder) Dial(string, string) (net.Conn, error) {
	d.calls.Add(1)
	return nil, errors.New("forwarding dialer must never run")
}

func TestHomeTransportDestinationRefusals(t *testing.T) {
	ca := makeTransportCA(t, true)
	p := startTransportPeer(t, transportLeaf(t, ca, "valid"), echoTransport)
	h := transportForTest(t, ca, p.listener.Addr().String())
	for _, tc := range [][2]string{
		{"tcp", "attacker.invalid:8443"}, {"tcp", "192.168.1.41:8443"},
		{"tcp", homeControlName + ":80"}, {"tcp", homeControlName + ":444"},
		{"tcp", "127.0.0.1:443"}, {"tcp", "[::1]:443"},
		{"tcp", "user@" + homeControlName + ":443"}, {"tcp", homeControlName},
		{"udp", homeControlName + ":443"}, {"tcp6", homeControlName + ":443"},
	} {
		t.Run(tc[0]+"/"+tc[1], func(t *testing.T) {
			conn, err := h.DialContext(context.Background(), tc[0], tc[1])
			if conn != nil || err != errHomeTransport {
				t.Fatal("unadmitted destination returned a connection")
			}
		})
	}
	if p.dials.Load() != 0 || h.ctx.Err() != nil {
		t.Fatal("refused target dialed upstream or poisoned HTTPS admission")
	}
}

func TestHomeTransportHandshakeBeforeReturn(t *testing.T) {
	ca := makeTransportCA(t, true)
	leaf := transportLeaf(t, ca, "valid")
	l, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		t.Fatal("fake listener failed")
	}
	defer l.Close()
	accepted, release, finished := make(chan struct{}), make(chan struct{}), make(chan struct{})
	var handshake atomic.Bool
	go func() {
		defer close(finished)
		raw, err := l.Accept()
		if err != nil {
			return
		}
		defer raw.Close()
		close(accepted)
		<-release
		_ = raw.SetDeadline(time.Now().Add(time.Second))
		c := tls.Server(raw, &tls.Config{Certificates: []tls.Certificate{leaf}})
		if c.Handshake() == nil {
			handshake.Store(true)
			_, _ = io.Copy(io.Discard, c)
		}
	}()
	h := transportForTest(t, ca, l.Addr().String())
	type dialResult struct {
		conn net.Conn
		err  error
	}
	result := make(chan dialResult, 1)
	go func() { conn, err := connectTransport(t, h, homeControlName+":8443"); result <- dialResult{conn, err} }()
	select {
	case <-accepted:
	case <-time.After(time.Second):
		close(release)
		t.Fatal("upstream not dialed")
	}
	early := false
	select {
	case <-result:
		early = true
	case <-time.After(50 * time.Millisecond):
	}
	close(release)
	if early {
		h.Close()
		<-finished
		t.Fatal("connection preceded upstream handshake")
	}
	r := <-result
	if r.err != nil || r.conn == nil {
		t.Fatal("verified upstream did not return a connection")
	}
	defer r.conn.Close()
	// Server observes its Finished before receiving the client's later close.
	h.Close()
	<-finished
	if !handshake.Load() {
		t.Fatal("connection without a completed upstream handshake")
	}
}

func TestHomeTransportHalfCloseResponse(t *testing.T) {
	ca := makeTransportCA(t, true)
	requestEOF, release := make(chan struct{}), make(chan struct{})
	p := startTransportPeer(t, transportLeaf(t, ca, "valid"), func(c *tls.Conn, p *transportPeer) {
		data, err := io.ReadAll(c)
		p.applicationBytes.Add(int64(len(data)))
		close(requestEOF)
		<-release
		if err == nil {
			_, _ = c.Write([]byte("response-after-request-eof"))
			_ = c.CloseWrite()
		}
	})
	h := transportForTest(t, ca, p.listener.Addr().String())
	raw, err := connectTransport(t, h, homeControlName+":8443")
	if err != nil {
		close(release)
		t.Fatal("owned dial failed")
	}
	c := localTransportTLS(t, h, raw)
	_, _ = c.Write([]byte("request"))
	if c.CloseWrite() != nil {
		close(release)
		t.Fatal("local half-close failed")
	}
	type result struct {
		data []byte
		err  error
	}
	response := make(chan result, 1)
	go func() { data, err := io.ReadAll(c); response <- result{data, err} }()
	select {
	case <-requestEOF:
	case <-time.After(time.Second):
		close(release)
		t.Fatal("upstream did not observe request EOF")
	}
	// Hold the final response past request EOF: closing the whole upstream at
	// that EOF must be detectable before the server is allowed to respond.
	var early *result
	select {
	case r := <-response:
		early = &r
	case <-time.After(50 * time.Millisecond):
	}
	close(release)
	if early != nil {
		t.Fatal("request half-close prematurely closed response direction")
	}
	r := <-response
	if r.err != nil || string(r.data) != "response-after-request-eof" {
		t.Fatal("request half-close lost final response")
	}
}

func TestHomeTransportClose(t *testing.T) {
	for _, phase := range []string{"local-handshake", "relay"} {
		t.Run(phase, func(t *testing.T) {
			ca := makeTransportCA(t, true)
			p := startTransportPeer(t, transportLeaf(t, ca, "valid"), echoTransport)
			h := transportForTest(t, ca, p.listener.Addr().String())
			raw, err := connectTransport(t, h, homeControlName+":8443")
			if err != nil {
				t.Fatal("owned dial failed")
			}
			var c net.Conn = raw
			if phase == "relay" {
				c = localTransportTLS(t, h, raw)
				_, _ = c.Write([]byte("live"))
				if _, err := io.ReadFull(c, make([]byte, 4)); err != nil {
					t.Fatal("relay was not live")
				}
			}
			defer c.Close()
			closed := make(chan struct{})
			go func() { h.Close(); close(closed) }()
			select {
			case <-closed:
			case <-time.After(time.Second):
				t.Fatal("Close did not promptly interrupt owned streams")
			}
			h.Close()
			_ = c.SetReadDeadline(time.Now().Add(time.Second))
			if _, err := c.Read(make([]byte, 1)); err == nil {
				t.Fatal("owned stream survived Close")
			} else if timeout, ok := err.(net.Error); ok && timeout.Timeout() {
				t.Fatal("Close left client blocked")
			}
			if conn, err := h.Dial("tcp", homeControlName+":443"); conn != nil || err == nil {
				t.Fatal("closed transport admitted a dial")
			}
			h.mu.Lock()
			active := len(h.streams)
			h.mu.Unlock()
			if active != 0 {
				t.Fatal("Close returned with owned streams")
			}
			select {
			case <-p.streamEnded:
			case <-time.After(time.Second):
				t.Fatal("Close left upstream stream alive")
			}
			p.close()
		})
	}
}

func TestHomeTransportSetupCancellation(t *testing.T) {
	for _, cause := range []string{"caller", "owner"} {
		t.Run(cause, func(t *testing.T) {
			ca := makeTransportCA(t, true)
			l, err := net.Listen("tcp4", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			defer l.Close()
			accepted := make(chan net.Conn, 1)
			go func() {
				raw, err := l.Accept()
				if err == nil {
					accepted <- raw
				}
			}()
			h := transportForTest(t, ca, l.Addr().String())
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			result := make(chan error, 1)
			go func() {
				c, err := h.DialContext(ctx, "tcp", homeControlName+":8443")
				if c != nil {
					c.Close()
				}
				result <- err
			}()
			var raw net.Conn
			select {
			case raw = <-accepted:
			case <-time.After(time.Second):
				t.Fatal("outer dial not reached")
			}
			defer raw.Close()
			if cause == "caller" {
				cancel()
			} else {
				h.Close()
			}
			select {
			case err := <-result:
				if err == nil {
					t.Fatal("cancelled setup returned a connection")
				}
			case <-time.After(time.Second):
				t.Fatal("setup outlived cancellation")
			}
			h.mu.Lock()
			active := len(h.streams)
			h.mu.Unlock()
			if active != 0 {
				t.Fatal("cancelled setup retained an owned stream")
			}
			if cause == "caller" {
				if h.ctx.Err() != nil {
					t.Fatal("speculative caller cancellation killed the home owner")
				}
				peer := startTransportPeer(t, transportLeaf(t, ca, "valid"), echoTransport)
				h.upstream = peer.listener.Addr().String()
				c, err := h.Dial("tcp", homeControlName+":443")
				if err != nil {
					t.Fatal("caller cancellation poisoned subsequent admitted dial")
				}
				c.Close()
			}
		})
	}
}
