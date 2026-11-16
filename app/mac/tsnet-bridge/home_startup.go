package main

import (
	"context"
	"crypto/x509"
	"encoding/binary"
	"io"
	"net"
	"net/netip"
	"os"
	"strings"
	"sync"
	"time"

	"golang.org/x/net/dns/dnsmessage"
	"tailscale.com/envknob"
	"tailscale.com/net/dnscache"
)

var homeProcessMu sync.Mutex
var homeProcessStarted bool

// Only the dedicated executable calls this. No init function or archive export
// installs roots, proxies or resolvers in the embedding application's process.
func prepareHomeProcess(cfg homeLaunch) (*homeTransport, error) {
	homeProcessMu.Lock()
	defer homeProcessMu.Unlock()
	if homeProcessStarted {
		return nil, errHomeTrust
	}
	if _, err := validateHomeLaunch(cfg); err != nil {
		return nil, err
	}
	h, err := newHomeTransport(cfg.CA, cfg.Fingerprint, cfg.DialIP)
	if err != nil {
		return nil, errHomeTrust
	}
	success := false
	defer func() {
		if !success {
			_ = h.Close()
		}
	}()
	local, err := x509.ParseCertificate(h.LocalCA())
	if err != nil {
		return nil, errHomeTrust
	}
	roots := x509.NewCertPool()
	roots.AddCert(local) // ONLY the per-process issuer, never the remote home CA
	if os.Setenv("GODEBUG", "x509usefallbackroots=1") != nil {
		return nil, errHomeTrust
	}
	homeProcessStarted = true // global roots cannot be replaced, even after failure
	x509.SetFallbackRoots(roots)
	for _, entry := range os.Environ() {
		name, _, _ := strings.Cut(entry, "=")
		lower := strings.ToLower(name)
		switch lower {
		case "http_proxy", "https_proxy", "all_proxy", "no_proxy", "ssl_cert_file", "ssl_cert_dir", "sslkeylogfile", "gotraceback":
			if os.Unsetenv(name) != nil {
				return nil, errHomeTrust
			}
		default:
			if strings.HasPrefix(name, "TS_DEBUG") || strings.HasPrefix(name, "TS_TLS") {
				// Reset upstream's registered init-time debug caches as well.
				envknob.Setenv(name, "")
				if os.Unsetenv(name) != nil {
					return nil, errHomeTrust
				}
			}
		}
	}
	proxy := "http://" + h.Addr().String()
	if os.Setenv("HTTP_PROXY", proxy) != nil || os.Setenv("HTTPS_PROXY", proxy) != nil {
		return nil, errHomeTrust
	}
	resolver := fixedHomeResolver(h.ctx, netip.MustParseAddr(cfg.DialIP))
	net.DefaultResolver = resolver
	dnscache.Get().Forward = resolver
	dnscache.Get().LookupIPFallback = nil
	// This owner also closes tsnet admission and exported streams on failure.
	context.AfterFunc(h.ctx, cancelManaged)
	success = true
	return h, nil
}

// net.Resolver's production Dial API uses TCP framing for net.Pipe. No DNS
// socket, host configuration, external forwarder or mutable address map exists.
func fixedHomeResolver(lifetime context.Context, ip netip.Addr) *net.Resolver {
	return &net.Resolver{PreferGo: true, StrictErrors: true, Dial: func(ctx context.Context, _, _ string) (net.Conn, error) {
		if lifetime.Err() != nil || ctx.Err() != nil {
			return nil, errHomeTrust
		}
		client, server := net.Pipe()
		stop := context.AfterFunc(lifetime, func() { _ = client.Close(); _ = server.Close() })
		go func() {
			defer stop()
			defer server.Close()
			_ = server.SetDeadline(time.Now().Add(homeSetupTimeout))
			for {
				var size uint16
				if binary.Read(server, binary.BigEndian, &size) != nil || size == 0 || size > 4096 {
					return
				}
				raw := make([]byte, int(size))
				if _, err := io.ReadFull(server, raw); err != nil {
					return
				}
				var query dnsmessage.Message
				if query.Unpack(raw) != nil || query.Response || len(query.Questions) != 1 {
					return
				}
				q := query.Questions[0]
				response := dnsmessage.Message{Header: dnsmessage.Header{ID: query.ID, Response: true, Authoritative: true, RecursionDesired: query.RecursionDesired}, Questions: query.Questions}
				if q.Name.String() != homeControlName+"." || q.Class != dnsmessage.ClassINET {
					response.RCode = dnsmessage.RCodeNameError
				} else if q.Type == dnsmessage.TypeA {
					response.Answers = []dnsmessage.Resource{{Header: dnsmessage.ResourceHeader{Name: q.Name, Type: dnsmessage.TypeA, Class: dnsmessage.ClassINET, TTL: 60}, Body: &dnsmessage.AResource{A: ip.As4()}}}
				}
				data, err := response.Pack()
				if err != nil || binary.Write(server, binary.BigEndian, uint16(len(data))) != nil {
					return
				}
				if _, err := server.Write(data); err != nil {
					return
				}
			}
		}()
		return client, nil
	}}
}
