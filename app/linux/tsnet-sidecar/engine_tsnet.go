package sidecar

import (
	"context"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"tailscale.com/logtail"
	"tailscale.com/tsnet"
)

// TSNetEngine is the production embedded userspace-tailnet adapter. Server.Start
// launches the backend; readiness requires the bounded Server.Up wait as well.
type TSNetEngine struct{ server *tsnet.Server }

func NewTSNetEngine() *TSNetEngine { return &TSNetEngine{} }
func (e *TSNetEngine) Start(ctx context.Context, c EngineConfig, credential []byte) (RedemptionReceipt, error) {
	// Pinned tsnet falls back to ambient auth keys when AuthKey is empty.
	// Refuse before constructing/starting it, in enrollment and restart alike.
	// Also exclude forced login and the system-policy environment spelling;
	// never mutate process-wide inputs or include their values in errors.
	for _, name := range []string{"TS_AUTHKEY", "TS_AUTH_KEY", "TSNET_FORCE_LOGIN", "TS_DEBUGSYSPOLICY_AUTH_KEY"} {
		if _, set := os.LookupEnv(name); set {
			return RedemptionReceipt{}, fmt.Errorf("sidecar: unset %s; ambient tsnet authentication is forbidden", name)
		}
	}
	e.server = newTSNetServer(c, credential)
	if err := clearTSNetLogSpool(c.StateDir); err != nil {
		return RedemptionReceipt{}, err
	}
	if err := e.server.Start(); err != nil {
		return RedemptionReceipt{}, ErrEngine
	}
	e.server.AuthKey = ""
	// Start only launches the backend. Joining can legitimately pass through
	// NeedsLogin/NeedsMachineAuth before Running; Up waits for that transition.
	upCtx, cancel := context.WithTimeout(ctx, 60*time.Second)
	defer cancel()
	status, err := e.server.Up(upCtx)
	if err != nil {
		return RedemptionReceipt{}, ErrEngine
	}
	visible := false
	for _, peer := range status.Peer {
		if peer.HostName == c.ExpectedPeer || strings.TrimSuffix(peer.DNSName, ".") == c.ExpectedPeer {
			visible = true
			break
		}
	}
	return RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: visible}, nil
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
	if e.server == nil {
		return nil, ErrEngine
	}
	return e.server.Listen("tcp", addr)
}
func (e *TSNetEngine) Close() error {
	if e.server == nil {
		return nil
	}
	return e.server.Close()
}

func (e *TSNetEngine) healthClient() (healthClient, error) {
	return e.server.LocalClient()
}
