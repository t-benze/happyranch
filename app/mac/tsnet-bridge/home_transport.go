package main

// The dedicated helper owns Close and installs local roots/proxy routing before tsnet.
import (
	"bufio"
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
	"errors"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/netip"
	"strings"
	"sync"
	"time"
)

const (
	homeControlName  = "control.happyranch.invalid"
	homeHeaderLimit  = 8192
	homeStreamLimit  = 128
	homeSetupTimeout = 10 * time.Second
)

var errHomeTransport = errors.New("home_transport_refused")

// admitHomeTransport never consults system, platform, fallback or baked roots.
func admitHomeTransport(der []byte, fingerprint, dialIP string, now time.Time) (*x509.CertPool, string, error) {
	cert, err := x509.ParseCertificate(der) // rejects trailing bytes / multiple DERs
	sum := sha256.Sum256(der)
	ip, ipErr := netip.ParseAddr(dialIP)
	if err != nil || !cert.BasicConstraintsValid || !cert.IsCA ||
		now.Before(cert.NotBefore) || !now.Before(cert.NotAfter) ||
		hex.EncodeToString(sum[:]) != fingerprint || ipErr != nil || !ip.Is4() || !ip.IsPrivate() {
		return nil, "", errHomeTransport
	}
	roots := x509.NewCertPool()
	roots.AddCert(cert)
	return roots, net.JoinHostPort(ip.String(), "8443"), nil
}

// homeTransport holds one immutable admitted remote profile. Keys are generated
// in memory, never serialized. LocalCA returns only the public issuer DER.
type homeTransport struct {
	listener    net.Listener
	upstream    string
	upstreamTLS *tls.Config
	localTLS    *tls.Config
	localCA     []byte
	ctx         context.Context
	cancel      context.CancelFunc
	mu          sync.Mutex
	closed      bool
	streams     map[*homeTransportStream]struct{}
	wg          sync.WaitGroup
	closeOnce   sync.Once
}

type homeTransportStream struct{ local, remote net.Conn }

func newHomeTransport(der []byte, fingerprint, dialIP string) (*homeTransport, error) {
	roots, upstream, err := admitHomeTransport(der, fingerprint, dialIP, time.Now())
	if err != nil {
		return nil, err
	}
	listener, err := net.Listen("tcp4", "127.0.0.1:0")
	if err != nil {
		return nil, errHomeTransport
	}
	ca, leaf, err := ephemeralHomeIdentity(time.Now())
	if err != nil {
		_ = listener.Close()
		return nil, errHomeTransport
	}
	parsed, err := x509.ParseCertificate(leaf.Certificate[0])
	if err != nil {
		_ = listener.Close()
		return nil, errHomeTransport
	}
	// A new process is required after the memory-only issuer expires.
	ctx, cancel := context.WithDeadline(context.Background(), parsed.NotAfter)
	h := &homeTransport{listener: listener, upstream: upstream,
		upstreamTLS: &tls.Config{RootCAs: roots, ServerName: homeControlName, MinVersion: tls.VersionTLS12},
		localTLS:    &tls.Config{Certificates: []tls.Certificate{leaf}, MinVersion: tls.VersionTLS12},
		localCA:     ca, ctx: ctx, cancel: cancel, streams: make(map[*homeTransportStream]struct{})}
	h.wg.Add(1)
	go h.accept()
	go func() { <-ctx.Done(); _ = h.Close() }()
	return h, nil
}

func (h *homeTransport) Addr() net.Addr { return h.listener.Addr() }
func (h *homeTransport) LocalCA() []byte {
	h.mu.Lock()
	defer h.mu.Unlock()
	return append([]byte(nil), h.localCA...)
}
func (h *homeTransport) Done() <-chan struct{} { return h.ctx.Done() }

// Close cancels pending dials/handshakes and both sides of every owned stream,
// and waits until accept/relay handlers have relinquished ownership.
func (h *homeTransport) Close() error {
	h.closeOnce.Do(func() {
		h.mu.Lock()
		h.closed = true
		h.cancel()
		_ = h.listener.Close()
		for s := range h.streams {
			_ = s.local.Close()
			if s.remote != nil {
				_ = s.remote.Close()
			}
		}
		h.mu.Unlock()
		h.wg.Wait()
		h.mu.Lock()
		h.localTLS.Certificates = nil // relinquish private-key references after all TLS users exit
		clear(h.localCA)
		h.localCA = nil
		h.mu.Unlock()
	})
	return nil
}

func (h *homeTransport) accept() {
	defer h.wg.Done()
	for {
		conn, err := h.listener.Accept()
		if err != nil {
			// An unexpected listener failure cancels all existing admission/streams too.
			go h.Close()
			return
		}
		h.mu.Lock()
		if h.closed || len(h.streams) >= homeStreamLimit {
			h.mu.Unlock()
			_ = conn.Close()
			continue
		}
		s := &homeTransportStream{local: conn}
		h.streams[s] = struct{}{}
		h.wg.Add(1)
		h.mu.Unlock()
		go h.serve(s)
	}
}

func (h *homeTransport) serve(s *homeTransportStream) {
	defer h.wg.Done()
	defer func() {
		h.mu.Lock()
		delete(h.streams, s)
		h.mu.Unlock()
		_ = s.local.Close()
		if s.remote != nil {
			_ = s.remote.Close()
		}
	}()
	deadline := time.Now().Add(homeSetupTimeout)
	_ = s.local.SetDeadline(deadline)
	reader := bufio.NewReaderSize(s.local, homeHeaderLimit+1)
	if !readHomeConnect(reader) {
		_, _ = io.WriteString(s.local, "HTTP/1.1 400 Bad Request\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
		return
	}
	ctx, cancel := context.WithDeadline(h.ctx, deadline)
	defer cancel()
	raw, err := (&net.Dialer{}).DialContext(ctx, "tcp4", h.upstream)
	if err != nil {
		homeConnectFailure(s.local)
		h.cancel() // terminal upstream/verifier failure; never retry directly
		return
	}
	h.mu.Lock()
	if h.closed {
		h.mu.Unlock()
		_ = raw.Close()
		return
	}
	s.remote = raw // track before handshake; Close can interrupt any setup phase
	h.mu.Unlock()
	_ = raw.SetDeadline(deadline)
	upstream := tls.Client(raw, h.upstreamTLS)
	if upstream.HandshakeContext(ctx) != nil {
		homeConnectFailure(s.local)
		h.cancel() // terminal upstream/verifier failure; never retry directly
		return
	}
	// No remote application byte or local success precedes upstream verification.
	if _, err := io.WriteString(s.local, "HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil {
		return
	}
	local := tls.Server(&homeBufferedConn{Conn: s.local, reader: reader}, h.localTLS)
	if local.HandshakeContext(ctx) != nil {
		return
	}
	_ = local.SetDeadline(time.Time{})
	_ = upstream.SetDeadline(time.Time{})
	relayHomeTLS(local, upstream)
}

func homeConnectFailure(conn net.Conn) {
	_, _ = io.WriteString(conn, "HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
}

// Read only bounded headers. Preserve buffered TLS bytes after CONNECT, and
// reject folded/control headers, bodies and every non-allowlisted request form.
func readHomeConnect(reader *bufio.Reader) bool {
	var header []byte
	for {
		line, err := reader.ReadSlice('\n')
		if err != nil || len(header)+len(line) > homeHeaderLimit || !bytes.HasSuffix(line, []byte("\r\n")) {
			return false
		}
		if len(header) > 0 && len(line) > 2 && (line[0] == ' ' || line[0] == '\t') {
			return false
		}
		for _, b := range line[:len(line)-2] {
			if b < 32 && b != '\t' || b == 127 {
				return false
			}
		}
		header = append(header, line...)
		if len(line) == 2 {
			break
		}
	}
	req, err := http.ReadRequest(bufio.NewReader(bytes.NewReader(header)))
	if err != nil {
		return false
	}
	defer req.Body.Close()
	if req.Method != "CONNECT" || req.Proto != "HTTP/1.1" && req.Proto != "HTTP/1.0" ||
		req.RequestURI != homeControlName+":8443" && req.RequestURI != homeControlName+":443" ||
		req.ContentLength != 0 || len(req.TransferEncoding) != 0 {
		return false
	}
	// ReadRequest uses the CONNECT target as Host. Also validate an explicit Host
	// header independently, rather than silently ignoring contradictory input.
	for _, line := range strings.Split(string(header), "\r\n")[1:] {
		key, value, ok := strings.Cut(line, ":")
		if ok && strings.EqualFold(key, "Host") && strings.TrimSpace(value) != req.RequestURI {
			return false
		}
	}
	return true
}

type homeBufferedConn struct {
	net.Conn
	reader *bufio.Reader
}

func (c *homeBufferedConn) Read(p []byte) (int, error) { return c.reader.Read(p) }

func relayHomeTLS(local, remote *tls.Conn) {
	done := make(chan struct{}, 2)
	copyHalf := func(dst, src *tls.Conn) {
		_, err := io.Copy(dst, src)
		if err != nil || dst.CloseWrite() != nil {
			_ = local.Close()
			_ = remote.Close()
		}
		done <- struct{}{}
	}
	go copyHalf(remote, local)
	go copyHalf(local, remote)
	<-done
	<-done
}

func ephemeralHomeIdentity(now time.Time) ([]byte, tls.Certificate, error) {
	caKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, tls.Certificate{}, err
	}
	leafKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, tls.Certificate{}, err
	}
	serial := func() (*big.Int, error) { return rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128)) }
	caSerial, err := serial()
	if err != nil {
		return nil, tls.Certificate{}, err
	}
	leafSerial, err := serial()
	if err != nil {
		return nil, tls.Certificate{}, err
	}
	ca := &x509.Certificate{SerialNumber: caSerial, Subject: pkix.Name{CommonName: "HappyRanch ephemeral local issuer"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(24 * time.Hour), BasicConstraintsValid: true,
		IsCA: true, KeyUsage: x509.KeyUsageCertSign}
	caDER, err := x509.CreateCertificate(rand.Reader, ca, ca, &caKey.PublicKey, caKey)
	if err != nil {
		return nil, tls.Certificate{}, err
	}
	leaf := &x509.Certificate{SerialNumber: leafSerial, DNSNames: []string{homeControlName},
		NotBefore: ca.NotBefore, NotAfter: ca.NotAfter, KeyUsage: x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	leafDER, err := x509.CreateCertificate(rand.Reader, leaf, ca, &leafKey.PublicKey, caKey)
	if err != nil {
		return nil, tls.Certificate{}, err
	}
	return caDER, tls.Certificate{Certificate: [][]byte{leafDER}, PrivateKey: leafKey}, nil
}
