package sidecar

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/x509"
	"encoding/binary"
	"encoding/hex"
	"encoding/pem"
	"io"
	"net"
	"net/netip"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.org/x/net/dns/dnsmessage"
	"golang.org/x/sys/unix"
	"tailscale.com/net/dnscache"
)

// Ownership is private and fixed by the production constructor. Tests may use
// their own UID/GID for synthetic temporary roots; there is no runtime flag.
type homeTrustPolicy struct{ uid, caGID, directoryGID uint32 }

func (p homeTrustPolicy) safeDirectory(st *unix.Stat_t) bool {
	return st.Mode&unix.S_IFMT == unix.S_IFDIR && st.Uid == p.uid && st.Gid == p.directoryGID && st.Mode&0o022 == 0
}

func sameHomeMetadata(a, b unix.Stat_t) bool {
	return a.Dev == b.Dev && a.Ino == b.Ino && a.Mode == b.Mode && a.Nlink == b.Nlink && a.Uid == b.Uid && a.Gid == b.Gid && a.Size == b.Size && a.Mtim == b.Mtim && a.Ctim == b.Ctim
}

// Every path component is opened relative to the previous held descriptor.
// Ownership/mode admission applies to the containing directory. Production also
// admits / and /etc independently before calling this for the fixed paths.
func (p homeTrustPolicy) openParent(path string) (*os.File, string, error) {
	if !filepath.IsAbs(path) || filepath.Clean(path) != path {
		return nil, "", errHomeTransport
	}
	parts := strings.Split(strings.TrimPrefix(path, "/"), "/")
	fd, err := unix.Open("/", unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, "", errHomeTransport
	}
	for _, part := range parts[:len(parts)-1] {
		next, err := unix.Openat(fd, part, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
		unix.Close(fd)
		if err != nil {
			return nil, "", errHomeTransport
		}
		fd = next
	}
	var st unix.Stat_t
	if unix.Fstat(fd, &st) != nil || !p.safeDirectory(&st) {
		unix.Close(fd)
		return nil, "", errHomeTransport
	}
	return os.NewFile(uintptr(fd), filepath.Dir(path)), parts[len(parts)-1], nil
}

func (p homeTrustPolicy) admit(bundle, directory, fingerprint string) (*x509.Certificate, error) {
	parent, name, err := p.openParent(bundle)
	if err != nil {
		return nil, err
	}
	defer parent.Close()
	fd, err := unix.Openat(int(parent.Fd()), name, unix.O_RDONLY|unix.O_NOFOLLOW|unix.O_NONBLOCK|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, errHomeTransport
	}
	f := os.NewFile(uintptr(fd), name)
	defer f.Close()
	var before, after, named unix.Stat_t
	if unix.Fstat(fd, &before) != nil || before.Mode&unix.S_IFMT != unix.S_IFREG || before.Nlink != 1 || before.Uid != p.uid || before.Gid != p.caGID || before.Mode&0o7777 != 0o640 || before.Size < 1 || before.Size > 64<<10 {
		return nil, errHomeTransport
	}
	b, err := io.ReadAll(io.LimitReader(f, (64<<10)+1))
	if err != nil || len(b) > 64<<10 || unix.Fstat(fd, &after) != nil || !sameHomeMetadata(before, after) || unix.Fstatat(int(parent.Fd()), name, &named, unix.AT_SYMLINK_NOFOLLOW) != nil || !sameHomeMetadata(named, after) {
		return nil, errHomeTransport
	}
	// pem.Decode skips arbitrary leading text: explicitly prohibit that.
	b = bytes.TrimSpace(b)
	if !bytes.HasPrefix(b, []byte("-----BEGIN CERTIFICATE-----")) || bytes.Count(b, []byte("-----BEGIN ")) != 1 {
		return nil, errHomeTransport
	}
	block, rest := pem.Decode(b)
	if block == nil || block.Type != "CERTIFICATE" || len(block.Headers) != 0 || len(bytes.TrimSpace(rest)) != 0 {
		return nil, errHomeTransport
	}
	ca, err := x509.ParseCertificate(block.Bytes)
	if err != nil || !ca.BasicConstraintsValid || !ca.IsCA || ca.KeyUsage&x509.KeyUsageCertSign == 0 || time.Now().Before(ca.NotBefore) || !time.Now().Before(ca.NotAfter) {
		return nil, errHomeTransport
	}
	digest := sha256.Sum256(ca.Raw)
	if hex.EncodeToString(digest[:]) != fingerprint {
		return nil, errHomeTransport
	}
	dp, dn, err := p.openParent(directory)
	if err != nil {
		return nil, err
	}
	defer dp.Close()
	dfd, err := unix.Openat(int(dp.Fd()), dn, unix.O_RDONLY|unix.O_DIRECTORY|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, errHomeTransport
	}
	d := os.NewFile(uintptr(dfd), dn)
	defer d.Close()
	var ds, dsAfter, dsNamed unix.Stat_t
	if unix.Fstat(dfd, &ds) != nil || !p.safeDirectory(&ds) || ds.Mode&0o7777 != 0o755 {
		return nil, errHomeTransport
	}
	entries, err := d.ReadDir(1)
	if err != io.EOF || len(entries) != 0 || unix.Fstat(dfd, &dsAfter) != nil || !sameHomeMetadata(ds, dsAfter) || unix.Fstatat(int(dp.Fd()), dn, &dsNamed, unix.AT_SYMLINK_NOFOLLOW) != nil || !sameHomeMetadata(dsNamed, dsAfter) {
		return nil, errHomeTransport
	}
	return ca, nil
}

func admitHomeEnvironment(env []string) error {
	file, directory := false, false
	for _, entry := range env {
		name, value, _ := strings.Cut(entry, "=")
		switch name {
		case "SSL_CERT_FILE":
			if file || value != homeCAFile {
				return errHomeTransport
			}
			file = true
		case "SSL_CERT_DIR":
			if directory || value != homeCADirectory {
				return errHomeTransport
			}
			directory = true
		case "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy", "SSLKEYLOGFILE", "GODEBUG":
			return errHomeTransport
		default:
			if strings.HasPrefix(name, "TS_") {
				return errHomeTransport
			}
		}
	}
	if !file || !directory {
		return errHomeTransport
	}
	return nil
}

var homeProcessMu sync.Mutex
var homeProcessUsed bool

func prepareHomeTransport(c EngineConfig) (*homeForwarder, error) {
	if c.ControlTLS == nil {
		if strings.Contains(c.ControlURL, homeName) {
			return nil, errHomeTransport
		}
		return nil, nil
	}
	homeProcessMu.Lock()
	defer homeProcessMu.Unlock()
	if homeProcessUsed || !c.ControlTLS.valid() || c.ControlURL != homeURL || c.Profile != LinuxIdentityProfile {
		return nil, errHomeTransport
	}
	if admitHomeEnvironment(os.Environ()) != nil {
		return nil, errHomeTransport
	}
	group, err := user.LookupGroup("happyranch")
	if err != nil {
		return nil, errHomeTransport
	}
	gid, err := strconv.ParseUint(group.Gid, 10, 32)
	if err != nil {
		return nil, errHomeTransport
	}
	p := homeTrustPolicy{uid: 0, caGID: uint32(gid), directoryGID: 0}
	// Admit all fixed ancestors, including / and /etc, with the same rule.
	for _, path := range []string{"/etc", "/etc/happyranch"} {
		fd, _, err := p.openParent(path)
		if err != nil {
			return nil, errHomeTransport
		}
		fd.Close()
	}
	ca, err := p.admit(homeCAFile, homeCADirectory, c.ControlTLS.CAFingerprint)
	if err != nil {
		return nil, err
	}
	f, err := newHomeForwarder(net.JoinHostPort(c.ControlTLS.DialIP, "8443"), ca)
	if err != nil {
		return nil, err
	}
	// The process cannot safely retry global root/proxy setup in place.
	homeProcessUsed = true
	if err := installHomeProcess(f, netip.MustParseAddr(c.ControlTLS.DialIP)); err != nil {
		f.Close()
		return nil, err
	}
	return f, nil
}

// Called once before any tsnet/client construction. The only fallback root is
// the process-local issuer; home roots are used exclusively by the forwarder.
func installHomeProcess(f *homeForwarder, ip netip.Addr) error {
	if os.Setenv("GODEBUG", "x509usefallbackroots=1") != nil {
		return errHomeTransport
	}
	x509.SetFallbackRoots(f.localRoots)
	for _, name := range []string{"HTTP_PROXY", "HTTPS_PROXY"} {
		if os.Setenv(name, f.proxyURL()) != nil {
			return errHomeTransport
		}
	}
	resolver := fixedHomeResolver(ip)
	net.DefaultResolver = resolver
	dnscache.Get().Forward = resolver
	dnscache.Get().LookupIPFallback = nil
	dnscache.Get().UseLastGood = false
	dnscache.Get().SingleHost = homeName
	dnscache.Get().SingleHostStaticResult = []netip.Addr{ip}
	return nil
}

// net.Pipe gives the Go resolver DNS-over-TCP framing without opening a socket.
// Other names return NXDOMAIN; there is no host-DNS or public fallback.
func fixedHomeResolver(ip netip.Addr) *net.Resolver {
	return &net.Resolver{PreferGo: true, StrictErrors: true, Dial: func(ctx context.Context, _, _ string) (net.Conn, error) {
		client, server := net.Pipe()
		if deadline, ok := ctx.Deadline(); ok {
			_ = client.SetDeadline(deadline)
			_ = server.SetDeadline(deadline)
		}
		go func() {
			defer server.Close()
			var prefix [2]byte
			if _, err := io.ReadFull(server, prefix[:]); err != nil {
				return
			}
			length := binary.BigEndian.Uint16(prefix[:])
			if length > 4096 {
				return
			}
			wire := make([]byte, length)
			if _, err := io.ReadFull(server, wire); err != nil {
				return
			}
			var query dnsmessage.Message
			if query.Unpack(wire) != nil || len(query.Questions) != 1 {
				return
			}
			q := query.Questions[0]
			response := dnsmessage.Message{Header: dnsmessage.Header{ID: query.ID, Response: true, Authoritative: true, RecursionDesired: true, RecursionAvailable: true}, Questions: query.Questions}
			if q.Name.String() != homeName+"." {
				response.RCode = dnsmessage.RCodeNameError
			} else if q.Type == dnsmessage.TypeA && q.Class == dnsmessage.ClassINET {
				response.Answers = []dnsmessage.Resource{{Header: dnsmessage.ResourceHeader{Name: q.Name, Type: dnsmessage.TypeA, Class: dnsmessage.ClassINET, TTL: 60}, Body: &dnsmessage.AResource{A: ip.As4()}}}
			}
			answer, err := response.Pack()
			if err != nil {
				return
			}
			binary.BigEndian.PutUint16(prefix[:], uint16(len(answer)))
			_, _ = server.Write(append(prefix[:], answer...))
		}()
		return client, nil
	}}
}
