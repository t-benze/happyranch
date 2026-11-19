package main

// The archive never invokes main. Only this separate executable installs roots.
import (
	"bytes"
	"context"
	"crypto/x509"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"golang.org/x/sys/unix"
)

const homeTrustRefused = -20
const homeReapprovalRequired = -21
const homeControlURL = "https://" + homeControlName + ":8443"

type homeLaunch struct {
	Recovery       *homeAddressRecovery `json:"recovery,omitempty"`
	Version        int                  `json:"version"`
	CA             []byte               `json:"caDER"`
	DialIP         string               `json:"dialIP"`
	HomeID         string               `json:"homeID"`
	ControlPlaneID string               `json:"controlPlaneID"`
	Fingerprint    string               `json:"fingerprint"`
	ControlURL     string               `json:"controlURL"`
	Hostname       string               `json:"hostname"`
	StateDirectory string               `json:"stateDirectory"`
	JoinKey        string               `json:"joinKey"`
	SocketPath     string               `json:"socketPath"`
	Target         string               `json:"target"`
}

var errHomeTrust = errors.New("home_trust_refused")

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
	if !exactHomeObject(data, false) {
		return homeLaunch{}, errHomeTrust
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if dec.Decode(&cfg) != nil || dec.Decode(new(any)) != io.EOF {
		return homeLaunch{}, errHomeTrust
	}
	return cfg, nil
}

// Enforce exact key spelling and uniqueness as well as DisallowUnknownFields.
func exactHomeObject(data []byte, recovery bool) bool {
	allowed := []string{"version", "caDER", "dialIP", "homeID", "controlPlaneID", "fingerprint", "controlURL", "hostname", "stateDirectory", "joinKey", "socketPath", "target"}
	if recovery {
		allowed = []string{"previousDialIP", "stableNodeID", "bearer"}
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	token, err := dec.Token()
	if err != nil || token != json.Delim('{') {
		return false
	}
	seen := make(map[string]bool)
	for dec.More() {
		token, err := dec.Token()
		key, ok := token.(string)
		if err != nil || !ok || seen[key] {
			return false
		}
		seen[key] = true
		var raw json.RawMessage
		if dec.Decode(&raw) != nil || bytes.Equal(raw, []byte("null")) {
			return false
		}
		if key == "recovery" && !recovery {
			if !exactHomeObject(raw, true) {
				return false
			}
			continue
		}
		known := false
		for _, name := range allowed {
			if name == key {
				known = true
				break
			}
		}
		if !known {
			return false
		}
	}
	if token, err := dec.Token(); err != nil || token != json.Delim('}') || dec.Decode(new(any)) != io.EOF {
		return false
	}
	for _, name := range allowed {
		if !seen[name] {
			return false
		}
	}
	return true
}

func validHomeID(s string) bool {
	if len(s) == 0 || len(s) > 128 {
		return false
	}
	for _, c := range s {
		if !(c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_') {
			return false
		}
	}
	return true
}

func validateHomeLaunch(cfg homeLaunch) (*x509.CertPool, error) {
	// tlsdial caches this value during package init; Unsetenv cannot revoke it.
	if ambientOverride() || os.Getenv("SSLKEYLOGFILE") != "" || cfg.Version != 2 ||
		cfg.ControlURL != homeControlURL && cfg.ControlURL != homeControlURL+"/" ||
		!validHomeID(cfg.HomeID) || !validHomeID(cfg.ControlPlaneID) ||
		!validHostname(cfg.Hostname) || !filepath.IsAbs(cfg.StateDirectory) ||
		hasControl(cfg.SocketPath+cfg.Target) {
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
	if cfg.Recovery != nil {
		r := cfg.Recovery
		if cfg.JoinKey != "" || r.PreviousDialIP == cfg.DialIP ||
			r.StableNodeID == "" || len(r.StableNodeID) > 256 || hasControl(r.StableNodeID) ||
			len(r.Bearer) == 0 || len(r.Bearer) > 4096 || strings.IndexFunc(r.Bearer, func(c rune) bool { return c < 33 || c > 126 }) >= 0 {
			return nil, errHomeTrust
		}
		if _, _, err := admitHomeTransport(cfg.CA, cfg.Fingerprint, r.PreviousDialIP, time.Now()); err != nil {
			return nil, errHomeTrust
		}
	}
	roots, _, err := admitHomeTransport(cfg.CA, cfg.Fingerprint, cfg.DialIP, time.Now())
	if err != nil {
		return nil, errHomeTrust
	}
	return roots, nil
}

func helperMain() {
	// Suppress upstream diagnostics at their destination; never interpolate errors.
	log.SetOutput(io.Discard)
	if len(os.Args) == 2 && os.Args[1] == "--home-session-v1" {
		homeSessionMain(os.Stdin, os.Stdout)
		return
	}
	if len(os.Args) != 2 || os.Args[1] != "--home-bridge-v2" {
		os.Exit(2)
	}
	cfg, err := readHomeLaunch(os.Stdin)
	if err != nil {
		fmt.Fprintln(os.Stdout, homeTrustRefused)
		return
	}
	transport, err := prepareHomeProcess(cfg)
	if err != nil {
		fmt.Fprintln(os.Stdout, homeTrustRefused)
		return
	}
	defer transport.Close()
	code := initHomeProfile(cfg)
	cfg.JoinKey = ""
	if code != 0 {
		fmt.Fprintln(os.Stdout, code)
		return
	}
	defer tsnet_close()
	if transport.ctx.Err() != nil {
		return
	}
	// Parent pipe ownership is the lifetime capability. EOF also cancels Up.
	go func() { _, _ = io.Copy(io.Discard, os.Stdin); cancelManaged(); transport.Close() }()
	if code = managedUp(120000); code != 0 {
		fmt.Fprintln(os.Stdout, code)
		return
	}
	if cfg.Recovery != nil {
		ctx, cancel := context.WithTimeout(managed.ctx, 10*time.Second)
		transport := &http.Transport{MaxResponseHeaderBytes: 8192, DisableKeepAlives: true, DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			host, _, _ := net.SplitHostPort(cfg.Target)
			conn, code := managedDialPeer(ctx, host, 443)
			if code != 0 {
				return nil, errManagedState
			}
			return conn, nil
		}}
		client := &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
		err := completeHomeAddressRecovery(ctx, cfg, managed.selfNodeID, managed.profile, client)
		cancel()
		transport.CloseIdleConnections()
		cfg.Recovery.Bearer = ""
		if err != nil {
			fmt.Fprintln(os.Stdout, managedPeerChanged)
			return
		}
	}
	listener, err := homeListener(cfg.SocketPath)
	if err != nil {
		fmt.Fprintln(os.Stdout, managedStateError)
		return
	}
	defer listener.Close()
	if writeHomeReady(os.Stdout, managed.selfNodeID) != nil {
		return
	} // membership identity only, never product approval
	host, _, _ := net.SplitHostPort(cfg.Target)
	serveHomeRuntime(transport.ctx, listener, host)
}

// Closing the owned listener or process context seals admission and closes all
// local ends, which also unblocks and closes every relay's peer end.
func serveHomeRuntime(ctx context.Context, listener net.Listener, host string) {
	var mu sync.Mutex
	var wg sync.WaitGroup
	streams := make(map[net.Conn]struct{})
	closed := false
	closeOwned := func() {
		mu.Lock()
		defer mu.Unlock()
		closed = true
		_ = listener.Close()
		for c := range streams {
			_ = c.Close()
		}
	}
	stop := context.AfterFunc(ctx, closeOwned)
	defer func() { stop(); closeOwned(); cancelManaged(); wg.Wait() }()
	for {
		conn, err := listener.Accept()
		if err != nil {
			return
		}
		mu.Lock()
		if closed || len(streams) >= homeStreamLimit {
			mu.Unlock()
			_ = conn.Close()
			continue
		}
		streams[conn] = struct{}{}
		wg.Add(1)
		mu.Unlock()
		go func() {
			defer wg.Done()
			defer func() { mu.Lock(); delete(streams, conn); mu.Unlock() }()
			relayHome(ctx, conn, host)
		}()
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
func relayHome(parent context.Context, local net.Conn, host string) {
	defer local.Close()
	ctx, cancel := context.WithTimeout(parent, 10*time.Second)
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

// Report the actual Self StableID authenticated during managedUp, never an
// invite field or a connector-supplied identity. This private stdout frame is
// the Mac's saved-grant binding; it carries no credential.
func writeHomeReady(out io.Writer, node string) error {
	if node == "" || len(node) > 256 || strings.IndexFunc(node, func(c rune) bool { return c <= 32 || c >= 127 }) >= 0 {
		return errHomeTrust
	}
	_, err := fmt.Fprintf(out, "0 %s\n", node)
	return err
}
