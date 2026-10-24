// Package sidecar implements the HappyRanch-managed Linux embedded-tailnet
// transport boundary. It transports opaque TCP bytes and has no HTTP or daemon
// credential knowledge.
package sidecar

import (
	"bytes"
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
	"time"
)

var (
	ErrConfiguration   = errors.New("sidecar: configuration invalid")
	ErrCredentialInput = errors.New("sidecar: credential input unavailable")
	ErrEngineStart     = errors.New("sidecar: engine start unavailable")
	ErrNetworkJoin     = errors.New("sidecar: network join unavailable")
	ErrDurableCommit   = errors.New("sidecar: durable receipt unavailable")
	ErrEngine          = errors.New("sidecar: encrypted engine unavailable")
	ErrConnector       = errors.New("sidecar: connector unavailable")
	ErrListener        = errors.New("sidecar: listener unavailable")
)

// NetworkJoinFailure retains only finite stage evidence, never an upstream
// cause. Error and Unwrap preserve the existing public refusal contract.
type NetworkJoinFailure struct{ SubReason string }

func (e NetworkJoinFailure) Error() string { return ErrNetworkJoin.Error() }
func (e NetworkJoinFailure) Unwrap() error { return ErrNetworkJoin }
func (e NetworkJoinFailure) NetworkJoinSubReason() string {
	switch e.SubReason {
	case "context_cancelled", "deadline_exceeded", "up_backend_error", "up_no_ip",
		"up_error_unclassified", "up_status_unavailable", "up_not_running",
		"peer_status_error", "peer_status_unavailable", "peer_not_running",
		"peer_wait_deadline", "expected_peer_missing":
		return e.SubReason
	default:
		return "unclassified"
	}
}

// NetworkJoinSubReason sanitizes metadata at both production consumers.
func NetworkJoinSubReason(err error) string {
	var failure NetworkJoinFailure
	if errors.As(err, &failure) {
		return failure.NetworkJoinSubReason()
	}
	var pointer *NetworkJoinFailure
	if errors.As(err, &pointer) && pointer != nil {
		return pointer.NetworkJoinSubReason()
	}
	return "unclassified"
}

const consumedMarker = "credential.consumed"

const (
	startupBudget       = 75 * time.Second
	connectorBudget     = 10 * time.Second
	connectorDialBudget = time.Second
	connectorSpacing    = 200 * time.Millisecond
)

type Config struct {
	StateDir, CredentialFile, ControlURL, RoleIdentity string
	ListenAddr, ConnectorAddr, DERPPolicy              string
	ExpectedPeers                                      []string
}

func (c Config) Validate() error {
	u, err := url.Parse(c.ControlURL)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || strings.EqualFold(u.Hostname(), "controlplane.tailscale.com") {
		return ErrConfiguration
	}
	if !strings.HasPrefix(c.RoleIdentity, "home-sidecar-") || len(c.RoleIdentity) <= len("home-sidecar-") || len(c.ExpectedPeers) == 0 {
		return ErrConfiguration
	}
	seen := map[string]bool{}
	for _, peer := range c.ExpectedPeers {
		peer = strings.TrimSpace(peer)
		if peer == "" || seen[peer] {
			return ErrConfiguration
		}
		seen[peer] = true
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

type EngineConfig struct {
	StateDir, ControlURL, RoleIdentity string
	ExpectedPeers                      []string
}
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
	managed         *ManagedSidecarGuard
	monitorStop     chan struct{}
	monitorWG       sync.WaitGroup
	cfg             Config
	engine          Engine
	dialer          Dialer
	mu              sync.Mutex
	listener        net.Listener
	active          map[net.Conn]struct{}
	starting        bool
	startDone       chan struct{}
	stopping        bool
	stopDone        chan struct{}
	engineCloseOnce sync.Once
	acceptWG        sync.WaitGroup
	proxyWG         sync.WaitGroup
	stopErr         error
	engineErr       error
}

func New(cfg Config, engine Engine, dialer Dialer) *Sidecar {
	return &Sidecar{cfg: cfg, engine: engine, dialer: dialer, active: make(map[net.Conn]struct{}), monitorStop: make(chan struct{})}
}

func (s *Sidecar) Start(ctx context.Context) error {
	// This deadline begins at entry, including credential and engine work.
	// Serving retains ctx; cancelling startup must not cancel accepted proxies.
	deadline := time.Now().Add(startupBudget)
    if s.managed != nil && s.managed.startupDeadline.Before(deadline) {deadline=s.managed.startupDeadline}
    startup, cancel := context.WithDeadline(ctx, deadline)
	defer cancel()
	if err := startupContextError(startup); err != nil {
		return terminalNetworkFailure(err)
	}
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
	if err := startupContextError(startup); err != nil {
		return terminalNetworkFailure(err)
	}
	credential, err := consumeInput(s.cfg)
	if err != nil {
		return ErrCredentialInput
	}
	if err := startupContextError(startup); err != nil {
		clear(credential)
		return terminalNetworkFailure(err)
	}
	if s.managed != nil && ManagedIdentityPreflight(s.cfg) != nil {
		clear(credential)
		return ErrCredentialInput
	}
	receipt, err := s.engine.Start(startup, EngineConfig{s.cfg.StateDir, s.cfg.ControlURL, s.cfg.RoleIdentity, s.cfg.ExpectedPeers}, credential)
	for i := range credential {
		credential[i] = 0
	}
	if err != nil {
		s.closeEngine()
		if errors.Is(err, ErrNetworkJoin) {
			return NetworkJoinFailure{SubReason: NetworkJoinSubReason(err)}
		}
		return ErrEngineStart
	}
	if err := startupContextError(startup); err != nil {
		s.closeEngine()
		return terminalNetworkFailure(err)
	}
	if !receipt.Redeemed || !receipt.Durable {
		s.closeEngine()
		return ErrDurableCommit
	}
	if !receipt.ExpectedPeerVisible {
		s.closeEngine()
		return NetworkJoinFailure{SubReason: "expected_peer_missing"}
	}
	if err := startupContextError(startup); err != nil {
		s.closeEngine()
		return terminalNetworkFailure(err)
	}
	if len(credential) != 0 {
		if err := commitConsumption(s.cfg); err != nil {
			s.closeEngine()
			return ErrDurableCommit
		}
	}
	if s.probeConnector(startup) != nil {
		s.closeEngine()
		return ErrConnector
	}
	if startupContextError(startup) != nil {
		s.closeEngine()
		return ErrConnector
	}
	var l net.Listener
	if s.managed != nil {
		err = s.managed.Admission(startup, func() error {
			if startupContextError(startup) != nil { return ErrListener }
			l, err = s.engine.Listen(s.cfg.ListenAddr)
			return err
		})
	} else {
		l, err = s.engine.Listen(s.cfg.ListenAddr)
	}
	if err != nil || l == nil {
		if l != nil {
			_ = l.Close()
		}
		s.closeEngine()
		return ErrListener
	}
	s.mu.Lock()
	if s.stopping || startupContextError(startup) != nil {
		s.mu.Unlock()
		_ = l.Close()
		s.closeEngine()
		return ErrListener
	}
	s.listener = l
	s.acceptWG.Add(1)
	s.mu.Unlock()
	go s.acceptLoop(ctx, l)
	if s.managed != nil {
		s.monitorWG.Add(1)
		go func() {
			defer s.monitorWG.Done()
			ticker := time.NewTicker(time.Second)
			defer ticker.Stop()
			for {
				select {
				case <-ctx.Done():
                    var cause error
                    if s.managed.failed.Load(){cause=ErrListener}
                    go s.shutdown(cause);return
				case <-s.monitorStop: return
				case <-ticker.C:
					s.mu.Lock(); stopping := s.stopping; s.mu.Unlock()
					if stopping { return }
					check, cancelCheck := context.WithTimeout(ctx,time.Second)
					engine, ok := s.engine.(*TSNetEngine)
					engineHealthy := ok && engine.managedHealthy(check,s.cfg)
                    // A known local negative vetoes admission BEFORE Check can
                    // wait for the common lock. Record/signal delivery to the
                    // wrapper remains a separate bounded observation.
                    if !engineHealthy { s.managed.observe(check,ErrLifecycle) }
                    guardErr:=s.managed.Check(check)
                    healthy:=engineHealthy && guardErr==nil
					cancelCheck()
                    if ctx.Err()!=nil{
                        var cause error
                        if s.managed.failed.Load(){cause=ErrListener}
                        go s.shutdown(cause);return
                    }
					if !healthy { s.managed.observe(check,ErrLifecycle); go s.shutdown(ErrListener); return }
				}
			}
		}()
	}
	return nil
}

// Timer delivery can lag behind an absolute deadline. Positive admission and
// retry decisions must check the clock as well as context cancellation.
func startupContextError(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if deadline, ok := ctx.Deadline(); ok && !time.Now().Before(deadline) {
		return context.DeadlineExceeded
	}
	return nil
}

func (s *Sidecar) probeConnector(startup context.Context) error {
	phase, cancel := context.WithTimeout(startup, connectorBudget)
	defer cancel()
	// Immediate refusals must not consume an arbitrary attempt allocation.
	// The fixed phase/overall deadline and cancellable spacing bound retries.
	for {
		if startupContextError(phase) != nil {
			return ErrConnector
		}
		dialCtx, cancelDial := context.WithTimeout(phase, connectorDialBudget)
		if startupContextError(dialCtx) != nil {
			cancelDial()
			return ErrConnector
		}
		probe, err := s.dialer.DialContext(dialCtx, "tcp", s.cfg.ConnectorAddr)
		dialErr := startupContextError(dialCtx)
		// Own every returned resource, even when DialContext also returns an
		// error. Close exactly once before any success or retry decision.
		var closeErr error
		if probe != nil {
			closeErr = probe.Close()
		}
		afterCloseErr := startupContextError(dialCtx)
		cancelDial()
		if closeErr != nil || startupContextError(phase) != nil {
			return ErrConnector
		}
		if err == nil {
			if probe == nil || dialErr != nil || afterCloseErr != nil {
				return ErrConnector
			}
			return nil
		}
		// A timeout identity is eligible only after the actual per-attempt
		// deadline. Text and generic net.Error implementations are insufficient.
		attemptTimeout := errors.Is(dialErr, context.DeadlineExceeded) && errors.Is(err, context.DeadlineExceeded)
		if !errors.Is(err, syscall.ECONNREFUSED) && !attemptTimeout {
			return ErrConnector
		}
		timer := time.NewTimer(connectorSpacing)
		select {
		case <-phase.Done():
			timer.Stop()
			return ErrConnector
		case <-timer.C:
		}
		if startupContextError(phase) != nil {
			return ErrConnector
		}
	}
}

func consumeInput(c Config) ([]byte, error) {
	if err := noSymlinkPath(c.StateDir); err != nil {
		return nil, err
	}
	if err := requireOwnerDir(c.StateDir); err != nil {
		return nil, err
	}
	_, markerErr := os.Lstat(filepath.Join(c.StateDir, consumedMarker))
	_, credentialErr := os.Lstat(c.CredentialFile)
	if markerErr == nil {
		if credentialErr == nil || !os.IsNotExist(credentialErr) {
			return nil, ErrCredentialInput
		}
		return nil, nil
	}
	if !os.IsNotExist(markerErr) {
		return nil, ErrCredentialInput
	}
	if err := noSymlinkPath(c.CredentialFile); err != nil {
		return nil, err
	}
	st, err := os.Lstat(c.CredentialFile)
	systemdCredential := isSystemdCredential(c.CredentialFile)
	if err != nil || st.Mode()&os.ModeSymlink != 0 || !st.Mode().IsRegular() ||
		(!systemdCredential && (st.Mode().Perm() != 0o600 || !ownedByCurrentUser(st))) ||
		(systemdCredential && st.Mode().Perm()&0o022 != 0) {
		return nil, ErrCredentialInput
	}
	b, err := os.ReadFile(c.CredentialFile)
	// TrimSpace returns a view. Always clear the entire owned read buffer,
	// including partial reads and the bytes outside that view on refusal.
	defer clear(b)
	if err != nil {
		return nil, ErrCredentialInput
	}
	trimmed := bytes.TrimSpace(b)
	if len(trimmed) == 0 {
		return nil, ErrCredentialInput
	}
	// Start owns and clears this independent copy after every engine result.
	return bytes.Clone(trimmed), nil
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
	if !isSystemdCredential(c.CredentialFile) {
		if err = os.Remove(c.CredentialFile); err != nil {
			return err
		}
		if err = syncDir(filepath.Dir(c.CredentialFile)); err != nil {
			return err
		}
	}
	ok = true
	return nil
}

func isSystemdCredential(path string) bool {
	dir := os.Getenv("CREDENTIALS_DIRECTORY")
	return filepath.IsAbs(dir) && filepath.Clean(path) == filepath.Join(filepath.Clean(dir), "enrollment.key")
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
                if s.managed!=nil{
                    s.managed.observe(ctx,ErrListener)
                }
				go s.shutdown(ErrListener)
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
	// Own every returned socket and any membership before registration can
	// succeed and then fail Forward's post-action authority proof.
	defer func() {
		_ = inbound.Close()
		if outbound != nil {
			_ = outbound.Close()
		}
		s.mu.Lock()
		delete(s.active, inbound)
		delete(s.active, outbound)
		s.mu.Unlock()
	}()
	if err != nil || outbound == nil {
		return
	}
	register := func() error {
		s.mu.Lock()
		defer s.mu.Unlock()
		if s.stopping {
			return ErrListener
		}
		s.active[inbound] = struct{}{}
		s.active[outbound] = struct{}{}
		return nil
	}
	if s.managed != nil {
		check, cancel := context.WithTimeout(ctx, time.Second)
		err = s.managed.Forward(check, register)
		cancel()
	} else {
		err = register()
	}
	if err != nil {
		return
	}
	done := make(chan bool, 2)
	copyFlow := func(dst, src net.Conn) {
		_, err := io.Copy(dst, src)
		orderly := false
		if err == nil {
			if c, ok := dst.(interface{ CloseWrite() error }); ok {
				orderly = c.CloseWrite() == nil
			}
		}
		done <- orderly
	}
	go copyFlow(outbound, inbound)
	go copyFlow(inbound, outbound)
	for i := 0; i < 2; i++ {
		if !<-done {
			// A copy error or failed/unsupported half-close terminates both
			// flows. Orderly EOF instead lets the reverse response drain.
			_ = inbound.Close()
			_ = outbound.Close()
		}
	}
	// Stop closes both registered sockets independently of copy completion;
	// proxy ownership ends only after both copies have reported completion.
}

func (s *Sidecar) Stop() error {
	s.shutdown(nil)
	// Every caller observes the actual accept-loop registration drained.
	s.acceptWG.Wait()
	s.mu.Lock()
	err := s.stopErr
	s.mu.Unlock()
	return err
}

func (s *Sidecar) closeEngine() {
	s.engineCloseOnce.Do(func() { s.engineErr = s.engine.Close() })
}

// shutdown is the single listener-first teardown owner. The accept loop hands
// failure to an external owner and releases acceptWG before that owner joins.
// Every external caller converges on stopDone after both work joins and Close.
func (s *Sidecar) shutdown(cause error) {
	s.mu.Lock()
	if s.stopping {
		done := s.stopDone
		s.mu.Unlock()
		<-done
		return
	}
	s.stopping = true
	close(s.monitorStop)
	s.stopDone = make(chan struct{})
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
	s.acceptWG.Wait()
	s.monitorWG.Wait()
	s.closeEngine()
	teardownFailed = teardownFailed || s.engineErr != nil
	s.mu.Lock()
	if cause != nil {
		s.stopErr = cause
	} else if teardownFailed {
		s.stopErr = ErrEngine
	}
	close(s.stopDone)
	s.mu.Unlock()
}
