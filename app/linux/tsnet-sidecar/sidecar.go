// Package sidecar implements the HappyRanch-managed Linux embedded-tailnet
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
	cfg      Config
	engine   Engine
	dialer   Dialer
	mu       sync.Mutex
	listener net.Listener
	active   map[net.Conn]struct{}
	stopping bool
	stopOnce sync.Once
	wg       sync.WaitGroup
	stopErr  error
}

func New(cfg Config, engine Engine, dialer Dialer) *Sidecar {
	return &Sidecar{cfg: cfg, engine: engine, dialer: dialer, active: make(map[net.Conn]struct{})}
}

func (s *Sidecar) Start(ctx context.Context) error {
	if err := s.cfg.Validate(); err != nil {
		return err
	}
	credential, err := consumeInput(s.cfg)
	if err != nil {
		return ErrCredential
	}
	receipt, err := s.engine.Start(ctx, EngineConfig{s.cfg.StateDir, s.cfg.ControlURL, s.cfg.RoleIdentity, s.cfg.ExpectedPeer}, credential)
	for i := range credential {
		credential[i] = 0
	}
	if err != nil || !receipt.Redeemed || !receipt.Durable || !receipt.ExpectedPeerVisible {
		_ = s.engine.Close()
		return ErrCredential
	}
	if err := commitConsumption(s.cfg); err != nil {
		_ = s.engine.Close()
		return ErrCredential
	}
	probe, err := s.dialer.DialContext(ctx, "tcp", s.cfg.ConnectorAddr)
	if err != nil || probe.Close() != nil {
		_ = s.engine.Close()
		return ErrConnector
	}
	l, err := s.engine.Listen(s.cfg.ListenAddr)
	if err != nil {
		_ = s.engine.Close()
		return ErrListener
	}
	s.mu.Lock()
	s.listener = l
	s.mu.Unlock()
	s.wg.Add(1)
	go s.acceptLoop(ctx, l)
	return nil
}

func consumeInput(c Config) ([]byte, error) {
	if err := noSymlinkPath(c.StateDir); err != nil {
		return nil, err
	}
	if err := noSymlinkPath(c.CredentialFile); err != nil {
		return nil, err
	}
	if err := requireOwnerDir(c.StateDir); err != nil {
		return nil, err
	}
	if _, err := os.Lstat(filepath.Join(c.StateDir, consumedMarker)); err == nil || !os.IsNotExist(err) {
		return nil, ErrCredential
	}
	st, err := os.Lstat(c.CredentialFile)
	if err != nil || st.Mode()&os.ModeSymlink != 0 || !st.Mode().IsRegular() || st.Mode().Perm() != 0o600 || !ownedByCurrentUser(st) {
		return nil, ErrCredential
	}
	b, err := os.ReadFile(c.CredentialFile)
	if err != nil || len(strings.TrimSpace(string(b))) == 0 {
		return nil, ErrCredential
	}
	return b, nil
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

func noSymlinkPath(path string) error {
	clean := filepath.Clean(path)
	for current := clean; current != string(filepath.Separator); current = filepath.Dir(current) {
		st, err := os.Lstat(current)
		if err != nil {
			return err
		}
		if st.Mode()&os.ModeSymlink != 0 {
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
	defer s.wg.Done()
	for {
		c, err := l.Accept()
		if err != nil {
			return
		}
		s.wg.Add(1)
		go s.proxy(ctx, c)
	}
}
func (s *Sidecar) proxy(ctx context.Context, inbound net.Conn) {
	defer s.wg.Done()
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
	s.stopOnce.Do(func() {
		s.mu.Lock()
		s.stopping = true
		l := s.listener
		s.listener = nil
		s.mu.Unlock()
		if l != nil {
			s.stopErr = l.Close()
		}
		s.mu.Lock()
		for c := range s.active {
			_ = c.Close()
		}
		s.mu.Unlock()
		s.wg.Wait()
		if err := s.engine.Close(); s.stopErr == nil {
			s.stopErr = err
		}
	})
	if s.stopErr != nil {
		return ErrEngine
	}
	return nil
}
