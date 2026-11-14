// Package sidecar implements the HappyRanch-managed embedded-tailnet
// transport boundary. It transports opaque TCP bytes and has no HTTP or daemon
// credential knowledge.
package sidecar

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
)

var (
	ErrConfiguration = errors.New("sidecar: configuration invalid")
	ErrCredential    = errors.New("sidecar: credential unavailable")
	ErrEngine        = errors.New("sidecar: encrypted engine unavailable")
	ErrConnector     = errors.New("sidecar: connector unavailable")
	ErrListener      = errors.New("sidecar: listener unavailable")
)

const consumedMarker = "credential.consumed"

type Config struct {
	StateDir, CredentialFile, ControlURL, RoleIdentity  string
	ExpectedPeer, ListenAddr, ConnectorAddr, DERPPolicy string
	ExcludedDaemonPorts                                 []int `json:",omitempty"`
}

func (c Config) Validate() error {
	u, err := url.Parse(c.ControlURL)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || strings.EqualFold(u.Hostname(), "controlplane.tailscale.com") {
		return ErrConfiguration
	}
	if !strings.HasPrefix(c.RoleIdentity, "home-sidecar-") || len(c.RoleIdentity) <= len("home-sidecar-") || strings.TrimSpace(c.ExpectedPeer) == "" {
		return ErrConfiguration
	}
	if c.DERPPolicy != "private-only" {
		return ErrConfiguration
	}
	host, port, err := net.SplitHostPort(c.ConnectorAddr)
	if err != nil || host != "127.0.0.1" || port == "" || port == "8765" {
		return ErrConfiguration
	}
	for _, excluded := range c.ExcludedDaemonPorts {
		connectorPort, err := strconv.Atoi(port)
		if excluded < 1 || excluded > 65535 || err != nil || connectorPort == excluded {
			return ErrConfiguration
		}
	}
	if _, p, err := net.SplitHostPort(c.ListenAddr); err != nil || p == "" {
		return ErrConfiguration
	}
	if !filepath.IsAbs(c.StateDir) || !filepath.IsAbs(c.CredentialFile) || filepath.Clean(c.StateDir) == string(filepath.Separator) {
		return ErrConfiguration
	}
	return nil
}

type EngineConfig struct{ StateDir, ControlURL, RoleIdentity, ExpectedPeer string }
type RedemptionReceipt struct{ Redeemed, Durable, ExpectedPeerVisible bool }
type Engine interface {
	Start(context.Context, EngineConfig, []byte) (RedemptionReceipt, error)
	Listen(string) (net.Listener, error)
	Close() error
}
type Dialer interface {
	DialContext(context.Context, string, string) (net.Conn, error)
}

type Sidecar struct {
	cfg             Config
	engine          Engine
	dialer          Dialer
	mu              sync.Mutex
	listener        net.Listener
	active          map[net.Conn]struct{}
	starting        bool
	startDone       chan struct{}
	stopping        bool
	stopOnce        sync.Once
	engineCloseOnce sync.Once
	acceptWG        sync.WaitGroup
	proxyWG         sync.WaitGroup
	stopErr         error
	engineErr       error
}

func New(cfg Config, engine Engine, dialer Dialer) *Sidecar {
	return &Sidecar{cfg: cfg, engine: engine, dialer: dialer, active: make(map[net.Conn]struct{})}
}

func (s *Sidecar) Start(ctx context.Context) error {
	if err := s.cfg.Validate(); err != nil {
		return err
	}
	s.mu.Lock()
	if s.starting || s.listener != nil || s.stopping {
		s.mu.Unlock()
		return ErrListener
	}
	s.starting = true
	s.startDone = make(chan struct{})
	done := s.startDone
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		s.starting = false
		close(done)
		s.mu.Unlock()
	}()
	credential, err := consumeInput(s.cfg)
	if err != nil {
		return ErrCredential
	}
	receipt, err := s.engine.Start(ctx, EngineConfig{s.cfg.StateDir, s.cfg.ControlURL, s.cfg.RoleIdentity, s.cfg.ExpectedPeer}, credential)
	for i := range credential {
		credential[i] = 0
	}
	if err != nil || !receipt.Redeemed || !receipt.Durable || !receipt.ExpectedPeerVisible {
		s.closeEngine()
		return ErrCredential
	}
	// Empty input means an already-consumed enrollment. Its marker must stay
	// untouched while tsnet resumes the durable state in the same directory.
	if len(credential) != 0 {
		if err := commitConsumption(s.cfg); err != nil {
			s.closeEngine()
			return ErrCredential
		}
	}
	probe, err := s.dialer.DialContext(ctx, "tcp", s.cfg.ConnectorAddr)
	if err != nil || probe == nil || probe.Close() != nil {
		s.closeEngine()
		return ErrConnector
	}
	l, err := s.engine.Listen(s.cfg.ListenAddr)
	if err != nil || l == nil {
		s.closeEngine()
		return ErrListener
	}
	s.mu.Lock()
	if s.stopping {
		s.mu.Unlock()
		_ = l.Close()
		s.closeEngine()
		return ErrListener
	}
	s.listener = l
	s.acceptWG.Add(1)
	s.mu.Unlock()
	go s.acceptLoop(ctx, l)
	return nil
}

func consumeInput(c Config) ([]byte, error) {
	if err := noSymlinkPath(c.StateDir, false); err != nil {
		return nil, err
	}
	if err := noSymlinkPath(c.CredentialFile, systemdInput(c)); err != nil {
		return nil, err
	}
	if err := requireOwnerDir(c.StateDir); err != nil {
		return nil, err
	}
	marker := filepath.Join(c.StateDir, consumedMarker)
	st, err := os.Lstat(marker)
	if err == nil {
		if !st.Mode().IsRegular() || st.Mode().Perm() != 0o600 || !ownedByCurrentUser(st) {
			return nil, ErrCredential
		}
		// Recheck the opened inode, and never follow a link or block on a FIFO
		// if the final entry changes between Lstat and open.
		f, err := os.OpenFile(marker, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
		if err != nil {
			return nil, ErrCredential
		}
		defer f.Close()
		opened, err := f.Stat()
		if err != nil || !opened.Mode().IsRegular() || opened.Mode().Perm() != 0o600 || !ownedByCurrentUser(opened) || !os.SameFile(st, opened) {
			return nil, ErrCredential
		}
		digest := sha256.Sum256([]byte(c.RoleIdentity + "\x00" + c.ControlURL))
		want := fmt.Sprintf("%x\n", digest)
		content, err := io.ReadAll(io.LimitReader(f, int64(len(want)+1)))
		if err != nil || string(content) != want {
			return nil, ErrCredential
		}
		if _, err := os.Lstat(c.CredentialFile); !errors.Is(err, syscall.ENOENT) {
			return nil, ErrCredential
		}
		return nil, nil
	}
	if !errors.Is(err, syscall.ENOENT) {
		return nil, ErrCredential
	}
	st, err = os.Lstat(c.CredentialFile)
	if err != nil || !validInputFile(c, st) {
		return nil, ErrCredential
	}
	f, err := os.OpenFile(c.CredentialFile, os.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK, 0)
	if err != nil {
		return nil, ErrCredential
	}
	defer f.Close()
	opened, err := f.Stat()
	if err != nil || !validInputFile(c, opened) || !os.SameFile(st, opened) {
		return nil, ErrCredential
	}
	b, err := io.ReadAll(f)
	if err != nil || len(strings.TrimSpace(string(b))) == 0 {
		return nil, ErrCredential
	}
	return b, nil
}

// Only the exact managed credential name in systemd's absolute directory gets
// read-only credential semantics. The environment contains a path, never a key.
func systemdInput(c Config) bool {
	// launchd has no credential directory; Darwin always uses one-use files.
	if runtime.GOOS == "darwin" {
		return false
	}
	dir := os.Getenv("CREDENTIALS_DIRECTORY")
	return filepath.IsAbs(dir) && filepath.Clean(dir) == dir && dir != "/" && c.CredentialFile == filepath.Join(dir, "enrollment-key")
}

func validInputFile(c Config, st os.FileInfo) bool {
	if !st.Mode().IsRegular() {
		return false
	}
	if !systemdInput(c) {
		return st.Mode().Perm() == 0o600 && ownedByCurrentUser(st)
	}
	// systemd writes 0400 copies, granting the service read access via ownership
	// or an ACL on a root-owned inode (whose read mask appears as 0440).
	// Never accept a writable or world-readable credential copy.
	owner, ok := st.Sys().(*syscall.Stat_t)
	mode := st.Mode().Perm()
	return ok && (mode == 0o400 || mode == 0o440) && owner.Nlink == 1 && (owner.Uid == 0 || ownedByCurrentUser(st))
}

func requireOwnerDir(path string) error {
	st, err := os.Lstat(path)
	if err != nil || st.Mode()&os.ModeSymlink != 0 || !st.IsDir() || st.Mode().Perm() != 0o700 || !ownedByCurrentUser(st) {
		return ErrConfiguration
	}
	return nil
}

func ownedByCurrentUser(st os.FileInfo) bool {
	sys, ok := st.Sys().(*syscall.Stat_t)
	return ok && sys.Uid == uint32(os.Geteuid())
}

func noSymlinkPath(path string, allowMissingParent bool) error {
	clean := filepath.Clean(path)
	for current := clean; current != string(filepath.Separator); current = filepath.Dir(current) {
		st, err := os.Lstat(current)
		// systemd can remove an empty per-unit credential directory. Only the
		// exact managed input may also lack that immediate parent; every higher
		// ancestor must still exist as a directory without symlinks.
		if errors.Is(err, syscall.ENOENT) && (current == clean || (allowMissingParent && current == filepath.Dir(clean))) {
			continue
		}
		if err != nil {
			return err
		}
		if st.Mode()&os.ModeSymlink != 0 || (current != clean && !st.IsDir()) {
			return ErrConfiguration
		}
	}
	return nil
}

func commitConsumption(c Config) error {
	digest := sha256.Sum256([]byte(c.RoleIdentity + "\x00" + c.ControlURL))
	marker := filepath.Join(c.StateDir, consumedMarker)
	f, err := os.OpenFile(marker, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	ok := false
	defer func() {
		if !ok {
			_ = f.Close()
		}
	}()
	if _, err = fmt.Fprintf(f, "%x\n", digest); err != nil {
		return err
	}
	if err = f.Sync(); err != nil {
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	if err = syncDir(c.StateDir); err != nil {
		return err
	}
	// systemd owns the read-only copy until service teardown. The installer
	// removes its root-owned source after both READY services pass drift checks.
	if systemdInput(c) {
		ok = true
		return nil
	}
	if err = os.Remove(c.CredentialFile); err != nil {
		return err
	}
	if err = syncDir(filepath.Dir(c.CredentialFile)); err != nil {
		return err
	}
	ok = true
	return nil
}
func syncDir(path string) error {
	f, err := os.Open(path)
	if err != nil {
		return err
	}
	defer f.Close()
	return f.Sync()
}

func (s *Sidecar) acceptLoop(ctx context.Context, l net.Listener) {
	defer s.acceptWG.Done()
	for {
		c, err := l.Accept()
		if err != nil {
			s.mu.Lock()
			deliberate := s.stopping
			s.mu.Unlock()
			if !deliberate {
				s.shutdown(ErrListener, true)
			}
			return
		}
		s.mu.Lock()
		if s.stopping {
			s.mu.Unlock()
			_ = c.Close()
			return
		}
		s.proxyWG.Add(1)
		s.mu.Unlock()
		go s.proxy(ctx, c)
	}
}
func (s *Sidecar) proxy(ctx context.Context, inbound net.Conn) {
	defer s.proxyWG.Done()
	outbound, err := s.dialer.DialContext(ctx, "tcp", s.cfg.ConnectorAddr)
	if err != nil {
		_ = inbound.Close()
		return
	}
	s.mu.Lock()
	if s.stopping {
		s.mu.Unlock()
		_ = inbound.Close()
		_ = outbound.Close()
		return
	}
	s.active[inbound] = struct{}{}
	s.active[outbound] = struct{}{}
	s.mu.Unlock()
	defer func() {
		_ = inbound.Close()
		_ = outbound.Close()
		s.mu.Lock()
		delete(s.active, inbound)
		delete(s.active, outbound)
		s.mu.Unlock()
	}()
	done := make(chan struct{}, 2)
	go func() {
		_, _ = io.Copy(outbound, inbound)
		if c, ok := outbound.(interface{ CloseWrite() error }); ok {
			_ = c.CloseWrite()
		}
		done <- struct{}{}
	}()
	go func() {
		_, _ = io.Copy(inbound, outbound)
		if c, ok := inbound.(interface{ CloseWrite() error }); ok {
			_ = c.CloseWrite()
		}
		done <- struct{}{}
	}()
	<-done
}

func (s *Sidecar) Stop() error {
	s.shutdown(nil, false)
	s.mu.Lock()
	err := s.stopErr
	s.mu.Unlock()
	return err
}

func (s *Sidecar) closeEngine() {
	s.engineCloseOnce.Do(func() { s.engineErr = s.engine.Close() })
}

// shutdown is the single listener-first teardown path. acceptCaller avoids
// waiting on the accept goroutine that is currently executing this method.
func (s *Sidecar) shutdown(cause error, acceptCaller bool) {
	s.stopOnce.Do(func() {
		s.mu.Lock()
		s.stopping = true
		startDone := s.startDone
		starting := s.starting
		s.mu.Unlock()
		if starting {
			<-startDone
		}
		s.mu.Lock()
		l := s.listener
		s.listener = nil
		s.mu.Unlock()
		var teardownFailed bool
		if l != nil {
			teardownFailed = l.Close() != nil
		}
		s.mu.Lock()
		for c := range s.active {
			_ = c.Close()
		}
		s.mu.Unlock()
		s.proxyWG.Wait()
		if !acceptCaller {
			s.acceptWG.Wait()
		}
		s.closeEngine()
		teardownFailed = teardownFailed || s.engineErr != nil
		s.mu.Lock()
		if cause != nil {
			s.stopErr = cause
		} else if teardownFailed {
			s.stopErr = ErrEngine
		}
		s.mu.Unlock()
	})
}
