package main

// The archive never invokes main. Only this separate executable installs roots.
import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/x509"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/netip"
	"net/url"
	"os"
	"path/filepath"
	"time"

	"golang.org/x/sys/unix"
)

const homeTrustRefused = -20

type homeLaunch struct {
	CA             []byte `json:"ca"`
	Fingerprint    string `json:"fingerprint"`
	ControlURL     string `json:"controlURL"`
	Hostname       string `json:"hostname"`
	StateDirectory string `json:"stateDirectory"`
	JoinKey        string `json:"joinKey"`
	SocketPath     string `json:"socketPath"`
	Target         string `json:"target"`
}

var errHomeTrust = errors.New("home_trust_refused")

func homeRoots(der []byte, fingerprint, host string, now time.Time) (*x509.CertPool, error) {
	cert, err := x509.ParseCertificate(der) // exactly ONE DER certificate, no trailing bytes
	sum := sha256.Sum256(der)
	if err != nil || !cert.BasicConstraintsValid || !cert.IsCA || now.Before(cert.NotBefore) || !now.Before(cert.NotAfter) || hex.EncodeToString(sum[:]) != fingerprint {
		return nil, errHomeTrust
	}
	pool := x509.NewCertPool()
	// tlsdial v1.78 uses ConnectionState.ServerName, which is empty for
	// an IP-literal SNI. Enforce the invite URL IP on every chain rooted here.
	pool.AddCertWithConstraint(cert, func(chain []*x509.Certificate) error {
		if len(chain) == 0 || chain[0].VerifyHostname(host) != nil {
			return errHomeTrust
		}
		return nil
	})
	return pool, nil
}

func readHomeLaunch(in io.Reader) (homeLaunch, error) {
	var size uint32
	var cfg homeLaunch
	if binary.Read(in, binary.BigEndian, &size) != nil || size == 0 || size > 16384 {
		return cfg, errHomeTrust
	}
	data := make([]byte, size)
	if _, err := io.ReadFull(in, data); err != nil {
		return cfg, errHomeTrust
	}
	defer clear(data)
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if dec.Decode(&cfg) != nil || dec.Decode(new(any)) != io.EOF {
		return homeLaunch{}, errHomeTrust
	}
	return cfg, nil
}

func validateHomeLaunch(cfg homeLaunch) (*x509.CertPool, error) {
	if os.Getenv("GODEBUG") != "x509usefallbackroots=1" || ambientOverride() {
		return nil, errHomeTrust
	}
	u, err := url.Parse(cfg.ControlURL)
	if err != nil || u.Scheme != "https" || u.Port() != "8443" || u.User != nil || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return nil, errHomeTrust
	}
	ip, err := netip.ParseAddr(u.Hostname())
	if err != nil || !ip.Is4() || !ip.IsPrivate() || cfg.ControlURL != "https://"+ip.String()+":8443" && cfg.ControlURL != "https://"+ip.String()+":8443/" {
		return nil, errHomeTrust
	}
	if !validHostname(cfg.Hostname) || !filepath.IsAbs(cfg.StateDirectory) || hasControl(cfg.SocketPath+cfg.Target) {
		return nil, errHomeTrust
	}
	host, port, err := net.SplitHostPort(cfg.Target)
	if err != nil || host == "" || port != "443" {
		return nil, errHomeTrust
	}
	if cfg.JoinKey != "" {
		key, err := hex.DecodeString(cfg.JoinKey)
		if err != nil || len(key) != 24 || hex.EncodeToString(key) != cfg.JoinKey {
			return nil, errHomeTrust
		}
	}
	return homeRoots(cfg.CA, cfg.Fingerprint, ip.String(), time.Now())
}

func helperMain() {
	// Suppress upstream diagnostics at their destination; never interpolate errors.
	log.SetOutput(io.Discard)
	if len(os.Args) != 2 || os.Args[1] != "--home-bridge-v1" {
		os.Exit(2)
	}
	cfg, err := readHomeLaunch(os.Stdin)
	if err != nil {
		fmt.Fprintln(os.Stdout, homeTrustRefused)
		return
	}
	roots, err := validateHomeLaunch(cfg)
	if err != nil {
		fmt.Fprintln(os.Stdout, homeTrustRefused)
		return
	}
	// Exactly once, before even constructing the tsnet server. Never in the app.
	x509.SetFallbackRoots(roots)
	code := managedInit(cfg.JoinKey, cfg.ControlURL, cfg.Hostname, cfg.StateDirectory)
	cfg.JoinKey = ""
	if code != 0 {
		fmt.Fprintln(os.Stdout, code)
		return
	}
	defer tsnet_close()
	// Parent pipe ownership is the lifetime capability. EOF also cancels Up.
	done := make(chan struct{})
	go func() { _, _ = io.Copy(io.Discard, os.Stdin); cancelManaged(); close(done) }()
	if code = managedUp(120000); code != 0 {
		fmt.Fprintln(os.Stdout, code)
		return
	}
	listener, err := homeListener(cfg.SocketPath)
	if err != nil {
		fmt.Fprintln(os.Stdout, managedStateError)
		return
	}
	defer listener.Close()
	go func() { <-done; listener.Close() }()
	fmt.Fprintln(os.Stdout, 0) // membership readiness only, never product approval
	host, _, _ := net.SplitHostPort(cfg.Target)
	for {
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		go relayHome(conn, host)
	}
}

func homeListener(path string) (net.Listener, error) {
	if !filepath.IsAbs(path) || filepath.Clean(path) != path || len(path) > 100 {
		return nil, errHomeTrust
	}
	var st unix.Stat_t
	if unix.Lstat(filepath.Dir(path), &st) != nil || st.Mode&unix.S_IFMT != unix.S_IFDIR || st.Uid != uint32(os.Geteuid()) || st.Mode&07777 != 0700 {
		return nil, errHomeTrust
	}
	if _, err := os.Lstat(path); !errors.Is(err, os.ErrNotExist) {
		return nil, errHomeTrust
	}
	listener, err := net.Listen("unix", path)
	if err != nil {
		return nil, errHomeTrust
	}
	if os.Chmod(path, 0600) != nil {
		listener.Close()
		return nil, errHomeTrust
	}
	return listener, nil
}

// Same peer-only pin boundary as the archive; no arbitrary dial IPC operation.
func relayHome(local net.Conn, host string) {
	defer local.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	remote, code := managedDialPeer(ctx, host, 443)
	cancel()
	if remote != nil {
		defer remote.Close()
	}
	if binary.Write(local, binary.BigEndian, int32(code)) != nil || code != 0 {
		return
	}
	finished := make(chan struct{}, 1)
	go func() {
		defer func() { finished <- struct{}{}; remote.Close(); local.Close() }()
		buf := make([]byte, 65536)
		for {
			n, err := local.Read(buf)
			if n > 0 {
				peer := remote.(*managedConnection)
				ctx, cancel := context.WithTimeout(peer.owner.ctx, 10*time.Second)
				code := peer.validate(ctx)
				cancel()
				if code != 0 {
					return
				}
				if _, e := io.Copy(remote, bytes.NewReader(buf[:n])); e != nil {
					return
				}
			}
			if err != nil {
				return
			}
		}
	}()
	_, _ = io.Copy(local, remote)
	remote.Close()
	local.Close()
	<-finished
}
