package main

// The dedicated helper owns Close and installs local roots/proxy routing before tsnet.
import (
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
	"net/netip"
	"sync"
	"time"
)

const (
	homeControlName  = "control.happyranch.invalid"
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
	dialIP      string
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
	ca, leaf, err := ephemeralHomeIdentity(time.Now())
	if err != nil {
		return nil, errHomeTransport
	}
	parsed, err := x509.ParseCertificate(leaf.Certificate[0])
	if err != nil {
		return nil, errHomeTransport
	}
	// A new process is required after the memory-only issuer expires.
	ctx, cancel := context.WithDeadline(context.Background(), parsed.NotAfter)
	h := &homeTransport{dialIP: dialIP, upstream: upstream,
		upstreamTLS: &tls.Config{RootCAs: roots, ServerName: homeControlName, MinVersion: tls.VersionTLS12},
		localTLS:    &tls.Config{Certificates: []tls.Certificate{leaf}, MinVersion: tls.VersionTLS12},
		localCA:     ca, ctx: ctx, cancel: cancel, streams: make(map[*homeTransportStream]struct{})}
	go func() { <-ctx.Done(); _ = h.Close() }()
	return h, nil
}

func (h *homeTransport) LocalCA() []byte {
	h.mu.Lock()
	defer h.mu.Unlock()
	return append([]byte(nil), h.localCA...)
}
func (h *homeTransport) Done() <-chan struct{} { return h.ctx.Done() }

// Close cancels pending dials/handshakes and both sides of every owned stream,
// and waits until dial/relay handlers have relinquished ownership.
func (h *homeTransport) Close() error {
	h.closeOnce.Do(func() {
		h.mu.Lock()
		h.closed = true
		h.cancel()
		for s := range h.streams {
			if s.local != nil {
				_ = s.local.Close()
			}
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

// Dial and DialContext are the x/net proxy and netns interfaces. The supplied
// proxy forwarding dialer is never used: only the admitted outer endpoint can dial.
func (h *homeTransport) Dial(network, address string) (net.Conn, error) {
	return h.DialContext(context.Background(), network, address)
}

func (h *homeTransport) DialContext(caller context.Context, network, address string) (net.Conn, error) {
	host, port, err := net.SplitHostPort(address)
	if err != nil || (network != "tcp" && network != "tcp4") ||
		(host != homeControlName && host != h.dialIP) || (port != "443" && port != "8443") {
		return nil, errHomeTransport
	}
	ctx, cancel := context.WithTimeout(caller, homeSetupTimeout)
	defer cancel()
	stop := context.AfterFunc(h.ctx, cancel)
	defer stop()
	h.mu.Lock()
	if h.closed || h.ctx.Err() != nil || caller.Err() != nil || len(h.streams) >= homeStreamLimit {
		h.mu.Unlock()
		return nil, errHomeTransport
	}
	s := &homeTransportStream{}
	h.streams[s] = struct{}{}
	h.wg.Add(1) // Close cannot finish while an admitted dial is still setting up.
	h.mu.Unlock()
	transferred := false
	defer func() {
		if !transferred {
			h.release(s)
		}
	}()
	raw, err := (&net.Dialer{}).DialContext(ctx, "tcp4", h.upstream)
	if err != nil {
		if caller.Err() == nil {
			h.cancel()
		}
		return nil, errHomeTransport
	}
	h.mu.Lock()
	if h.closed || h.ctx.Err() != nil {
		h.mu.Unlock()
		_ = raw.Close()
		return nil, errHomeTransport
	}
	s.remote = raw // Close owns the outer handshake as well as active relays.
	h.mu.Unlock()
	deadline, _ := ctx.Deadline()
	_ = raw.SetDeadline(deadline)
	upstream := tls.Client(raw, h.upstreamTLS)
	if upstream.HandshakeContext(ctx) != nil {
		if caller.Err() == nil {
			h.cancel() // verification failure is terminal; never retry directly
		}
		return nil, errHomeTransport
	}
	_ = upstream.SetDeadline(time.Time{})
	client, server := net.Pipe()
	h.mu.Lock()
	if h.closed || h.ctx.Err() != nil || caller.Err() != nil {
		h.mu.Unlock()
		_ = client.Close()
		_ = server.Close()
		return nil, errHomeTransport
	}
	s.local = server
	h.mu.Unlock()
	transferred = true
	go h.serve(s, upstream, deadline)
	// No connection or application byte precedes the pinned outer verification.
	return client, nil
}

func (h *homeTransport) release(s *homeTransportStream) {
	h.mu.Lock()
	delete(h.streams, s)
	h.mu.Unlock()
	if s.local != nil {
		_ = s.local.Close()
	}
	if s.remote != nil {
		_ = s.remote.Close()
	}
	h.wg.Done()
}

func (h *homeTransport) serve(s *homeTransportStream, upstream *tls.Conn, deadline time.Time) {
	defer h.release(s)
	local := tls.Server(s.local, h.localTLS)
	_ = local.SetDeadline(deadline)
	if local.HandshakeContext(h.ctx) != nil {
		return // a cancelled speculative client closes only its own stream
	}
	_ = local.SetDeadline(time.Time{})
	relayHomeTLS(local, upstream)
}

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
