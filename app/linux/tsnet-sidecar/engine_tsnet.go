package sidecar

import (
	"context"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"

	"tailscale.com/client/tailscale/apitype"
	"tailscale.com/ipn/ipnstate"
	"tailscale.com/logtail"
	"tailscale.com/tsnet"
)

// TSNetEngine is the production embedded userspace-tailnet adapter. Server.Start
// launches the backend; readiness requires the bounded Server.Up wait as well.
type whoIsClient interface {
	WhoIs(context.Context, string) (*apitype.WhoIsResponse, error)
}

type TSNetEngine struct {
	server         *tsnet.Server
	identityClient whoIsClient
	selfClient     selfClient
	home           *homeForwarder
	admissionMu    sync.Mutex
	closed         bool
	closeOnce      sync.Once
	closeErr       error
}

func NewTSNetEngine() *TSNetEngine { return &TSNetEngine{} }
func (e *TSNetEngine) Start(ctx context.Context, c EngineConfig, credential []byte) (RedemptionReceipt, error) {
	// Pinned tsnet falls back to ambient auth keys when AuthKey is empty.
	// Refuse before constructing/starting it, in enrollment and restart alike.
	// Also exclude forced login and the system-policy environment spelling;
	// never mutate process-wide inputs or include their values in errors.
	for _, name := range []string{"TS_AUTHKEY", "TS_AUTH_KEY", "TSNET_FORCE_LOGIN", "TS_DEBUGSYSPOLICY_AUTH_KEY"} {
		if _, set := os.LookupEnv(name); set {
			if c.ControlTLS != nil || c.ControlURL == homeURL {
				return RedemptionReceipt{}, errHomeTransport
			}
			return RedemptionReceipt{}, fmt.Errorf("sidecar: unset %s; ambient tsnet authentication is forbidden", name)
		}
	}
	var err error
	e.home, err = prepareHomeTransport(c)
	if err != nil {
		return RedemptionReceipt{}, err
	}
	e.server = newTSNetServer(c, credential)
	if err := clearTSNetLogSpool(c.StateDir); err != nil {
		_ = e.Close()
		return RedemptionReceipt{}, err
	}
	if err := e.server.Start(); err != nil {
		_ = e.Close()
		return RedemptionReceipt{}, ErrEngine
	}
	if e.home != nil {
		go e.watchHomeTransport(ctx)
	}
	e.server.AuthKey = ""
	// Start only launches the backend. Joining can legitimately pass through
	// NeedsLogin/NeedsMachineAuth before Running; Up waits for that transition.
	upCtx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	status, err := e.server.Up(upCtx)
	if err != nil {
		_ = e.Close()
		return RedemptionReceipt{}, ErrEngine
	}
	if c.Profile == LinuxIdentityProfile {
		client, err := e.server.LocalClient()
		if err != nil {
			_ = e.Close()
			return RedemptionReceipt{}, ErrEngine
		}
		e.identityClient = client
		e.selfClient = client
	}
	return RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: expectedPeerVisible(c, status)}, nil
}

func (e *TSNetEngine) watchHomeTransport(ctx context.Context) {
	select {
	case <-ctx.Done():
	case <-e.home.done:
	}
	_ = e.Close()
}

func expectedPeerVisible(c EngineConfig, status *ipnstate.Status) bool {
	if c.Profile == LinuxIdentityProfile {
		return true
	}
	if status == nil {
		return false
	}
	for _, peer := range status.Peer {
		if peer != nil && (peer.HostName == c.ExpectedPeer || strings.TrimSuffix(peer.DNSName, ".") == c.ExpectedPeer) {
			return true
		}
	}
	return false
}

type peerMetadata struct {
	stableID string
	label    string // Display only, from the SAME WhoIs observation as stableID.
}

func (e *TSNetEngine) peerIdentity(ctx context.Context, remote string) (peerMetadata, error) {
	if e.identityClient == nil {
		return peerMetadata{}, ErrEngine
	}
	response, err := e.identityClient.WhoIs(ctx, remote)
	if err != nil || response == nil || response.Node == nil || response.Node.StableID == "" {
		return peerMetadata{}, ErrEngine
	}
	label := ""
	if response.Node.Hostinfo.Valid() {
		label = response.Node.Hostinfo.Hostname()
	}
	if label == "" {
		label = response.Node.ComputedName
	}
	return peerMetadata{string(response.Node.StableID), label}, nil
}

func newTSNetServer(c EngineConfig, credential []byte) *tsnet.Server {
	// Disable upload independently of the local log callbacks, on every start.
	// Match the Mac bridge: suppress new backend records and local log output.
	logtail.Disable()
	return &tsnet.Server{
		Dir: c.StateDir, Hostname: c.RoleIdentity, ControlURL: c.ControlURL,
		AuthKey: string(credential), Ephemeral: false,
		Logf: func(string, ...any) {}, UserLogf: func(string, ...any) {},
	}
}

func clearTSNetLogSpool(stateDir string) error {
	// Disable blocks new writes, but pinned logtail still uploads old filch
	// records. Remove only these disposable buffers before tsnet can open them.
	for _, name := range []string{"tailscaled.log1.txt", "tailscaled.log2.txt"} {
		path := filepath.Join(stateDir, name)
		info, err := os.Lstat(path)
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			return fmt.Errorf("%w: inspect log spool %s: %w", ErrEngine, name, err)
		}
		if !info.Mode().IsRegular() {
			return fmt.Errorf("%w: log spool %s must be a regular file (symlinks forbidden)", ErrEngine, name)
		}
		// Unlink never follows a replaced symlink or removes a directory, and
		// leaves other hard links' contents (including identity state) intact.
		if err := syscall.Unlink(path); err != nil {
			return fmt.Errorf("%w: remove log spool %s: %w", ErrEngine, name, err)
		}
	}
	return nil
}

func (e *TSNetEngine) Listen(addr string) (net.Listener, error) {
	e.admissionMu.Lock()
	defer e.admissionMu.Unlock()
	// Pinned Server.Listen can create a listener even after Server.Close.
	// Serialize home admission with teardown rather than reopening a dead engine.
	if e.home != nil {
		select {
		case <-e.home.done:
			return nil, ErrEngine
		default:
		}
		if e.closed {
			return nil, ErrEngine
		}
	}
	if e.server == nil {
		return nil, ErrEngine
	}
	return e.server.Listen("tcp", addr)
}
func (e *TSNetEngine) Close() error {
	e.closeOnce.Do(func() {
		e.admissionMu.Lock()
		defer e.admissionMu.Unlock()
		e.closed = true
		if e.home != nil {
			_ = e.home.Close()
		}
		if e.server != nil {
			e.closeErr = e.server.Close()
		}
	})
	return e.closeErr
}

func (e *TSNetEngine) healthClient() (healthClient, error) {
	return e.server.LocalClient()
}
