package sidecar

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/netip"
	"strings"
	"sync"
	"syscall"
	"time"
)

const homeName = "control.happyranch.invalid"
const homeURL = "https://control.happyranch.invalid:28443"
const homeCAFile = "/etc/happyranch/control-ca.pem"
const homeCADirectory = "/etc/happyranch/empty-ca-directory"

var errHomeTransport = fmt.Errorf("%w: home transport unavailable", ErrConfiguration)

// HomeControlTLS binds the protected home configuration to its upstream identity.
// The installer, not the desktop, supplies this record. Keys are never config.
type HomeControlTLS struct {
	DialIP        string `json:"dial_ip"`
	CAFingerprint string `json:"ca_sha256"`
}

func (c HomeControlTLS) valid() bool {
	ip, err := netip.ParseAddr(c.DialIP)
	return err == nil && ip.Is4() && ip.IsPrivate() && len(c.CAFingerprint) == 64 && strings.Trim(c.CAFingerprint, "0123456789abcdef") == ""
}

// homeForwarder owns both ends of every admitted stream, including handshakes.
// Each instance holds its ephemeral listener and issuer for its entire lifetime.
type homeForwarder struct {
	listener   net.Listener
	upstream   string
	roots      *x509.CertPool
	leaf       tls.Certificate
	localRoots *x509.CertPool
	mu         sync.Mutex
	closed     bool
	streams    map[net.Conn]struct{}
	workers    sync.WaitGroup
	closeOnce  sync.Once
	done       chan struct{}
}

func newHomeForwarder(upstream string, ca *x509.Certificate) (*homeForwarder, error) {
	localCA, leaf, err := ephemeralHomeIssuer()
	if err != nil {
		return nil, errHomeTransport
	}
	l, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, errHomeTransport
	}
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	localRoots := x509.NewCertPool()
	localRoots.AddCert(localCA)
	f := &homeForwarder{listener: l, upstream: upstream, roots: roots, leaf: leaf, localRoots: localRoots, streams: make(map[net.Conn]struct{}), done: make(chan struct{})}
	f.workers.Add(1)
	go f.serve()
	return f, nil
}

func ephemeralHomeIssuer() (*x509.Certificate, tls.Certificate, error) {
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, tls.Certificate{}, err
	}
	serial := func() *big.Int { n, _ := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128)); return n }
	now := time.Now()
	caTemplate := &x509.Certificate{SerialNumber: serial(), Subject: pkix.Name{CommonName: "HappyRanch process-local issuer"}, NotBefore: now.Add(-time.Minute), NotAfter: now.AddDate(10, 0, 0), IsCA: true, BasicConstraintsValid: true, KeyUsage: x509.KeyUsageCertSign}
	caDER, err := x509.CreateCertificate(rand.Reader, caTemplate, caTemplate, &caKey.PublicKey, caKey)
	if err != nil {
		return nil, tls.Certificate{}, err
	}
	ca, err := x509.ParseCertificate(caDER)
	if err != nil {
		return nil, tls.Certificate{}, err
	}
	leafKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, tls.Certificate{}, err
	}
	leafTemplate := &x509.Certificate{SerialNumber: serial(), DNSNames: []string{homeName}, NotBefore: now.Add(-time.Minute), NotAfter: ca.NotAfter, KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, err := x509.CreateCertificate(rand.Reader, leafTemplate, ca, &leafKey.PublicKey, caKey)
	if err != nil {
		return nil, tls.Certificate{}, err
	}
	return ca, tls.Certificate{Certificate: [][]byte{der}, PrivateKey: leafKey}, nil
}

func (f *homeForwarder) proxyURL() string { return "http://" + f.listener.Addr().String() }

func (f *homeForwarder) own(c net.Conn) bool {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.closed || len(f.streams) >= 128 {
		_ = c.Close()
		return false
	}
	f.streams[c] = struct{}{}
	return true
}

func (f *homeForwarder) release(c net.Conn) {
	_ = c.Close()
	f.mu.Lock()
	delete(f.streams, c)
	f.mu.Unlock()
}

func (f *homeForwarder) serve() {
	defer f.workers.Done()
	for {
		c, err := f.listener.Accept()
		if err != nil {
			go f.Close()
			return
		}
		if !f.own(c) {
			continue
		}
		f.workers.Add(1)
		go func() { defer f.workers.Done(); defer f.release(c); f.forward(c) }()
	}
}

// CONNECT is deliberately parsed as a small closed protocol. Read bytewise to
// avoid consuming any pipelined TLS bytes, and never parse/log a tunneled body.
func readHomeConnect(c net.Conn) error {
	b := make([]byte, 0, 1024)
	for len(b) < 8192 {
		var one [1]byte
		_, err := io.ReadFull(c, one[:])
		if err != nil {
			return errHomeTransport
		}
		b = append(b, one[0])
		if len(b) >= 4 && string(b[len(b)-4:]) == "\r\n\r\n" {
			break
		}
	}
	if !strings.HasSuffix(string(b), "\r\n\r\n") {
		return errHomeTransport
	}
	lines := strings.Split(string(b), "\r\n")
	parts := strings.Split(lines[0], " ")
	if len(parts) != 3 || parts[0] != "CONNECT" || (parts[1] != homeName+":28443" && parts[1] != homeName+":443") || parts[2] != "HTTP/1.1" {
		return errHomeTransport
	}
	seenHost := false
	for _, line := range lines[1 : len(lines)-2] {
		name, value, ok := strings.Cut(line, ":")
		if !ok || name == "" {
			return errHomeTransport
		}
		for _, ch := range name {
			if !(ch >= 'a' && ch <= 'z' || ch >= 'A' && ch <= 'Z' || ch == '-') {
				return errHomeTransport
			}
		}
		for _, ch := range value {
			if ch < 32 && ch != '\t' || ch == 127 {
				return errHomeTransport
			}
		}
		switch strings.ToLower(name) {
		case "host":
			if seenHost || strings.TrimSpace(value) != parts[1] {
				return errHomeTransport
			}
			seenHost = true
		case "user-agent", "proxy-connection", "connection":
		default:
			return errHomeTransport
		}
	}
	if !seenHost {
		return errHomeTransport
	}
	return nil
}

func (f *homeForwarder) forward(local net.Conn) {
	_ = local.SetDeadline(time.Now().Add(10 * time.Second))
	if readHomeConnect(local) != nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	// Literal upstream address; no DNS, proxy, redirect or public root path.
	raw, err := (&net.Dialer{}).DialContext(ctx, "tcp4", f.upstream)
	if err != nil {
		go f.Close()
		return
	}
	if !f.own(raw) {
		return
	}
	defer f.release(raw)
	up := tls.Client(raw, &tls.Config{ServerName: homeName, RootCAs: f.roots, MinVersion: tls.VersionTLS12})
	if err := up.HandshakeContext(ctx); err != nil {
		go f.Close()
		return
	}
	if _, err := io.WriteString(local, "HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil {
		return
	}
	down := tls.Server(local, &tls.Config{Certificates: []tls.Certificate{f.leaf}, MinVersion: tls.VersionTLS12})
	if err := down.HandshakeContext(ctx); err != nil {
		// Pinned controlhttp races attempts; magicsock also cancels DERP
		// attempts on rebind. EOF or a TCP reset ends only that local stream;
		// malformed TLS or another handshake failure seals the transport.
		if !errors.Is(err, io.EOF) && !errors.Is(err, io.ErrUnexpectedEOF) && !errors.Is(err, net.ErrClosed) && !errors.Is(err, syscall.ECONNRESET) {
			go f.Close()
		}
		return
	}
	_ = local.SetDeadline(time.Time{})
	done := make(chan struct{})
	go func() { _, _ = io.Copy(up, down); _ = raw.Close(); _ = local.Close(); close(done) }()
	_, _ = io.Copy(down, up)
	_ = raw.Close()
	_ = local.Close()
	<-done
}

func (f *homeForwarder) Close() error {
	f.closeOnce.Do(func() {
		f.mu.Lock()
		f.closed = true
		_ = f.listener.Close()
		for c := range f.streams {
			_ = c.Close()
		}
		f.mu.Unlock()
		f.workers.Wait()
		f.leaf = tls.Certificate{}
		close(f.done)
	})
	return nil
}
