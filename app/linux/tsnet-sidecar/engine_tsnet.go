package sidecar

import (
	"context"
	"errors"
	"net"
	"strings"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tsnet"
)

// Keep the adapter's private 80-second ceiling. Sidecar.Start supplies its
// enclosing 75-second acceptance context (or an earlier caller deadline), so
// this cap cannot renew or escape that budget. The connector supervisor's
// independent 80-second timer is unchanged; the clocks are not identical.
const tsnetStartupBudget = 80 * time.Second

// tsnetServer is intentionally small so the production adapter can be driven
// through Sidecar with controlled upstream readiness and peer observations.
// Server.Start itself is contextless upstream work; Up and Status are the
// interruptible waits and always receive the one derived context.
type tsnetServer interface {
	Start() error
	ClearAuthKey()
	Up(context.Context) (*ipnstate.Status, error)
	Status(context.Context) (*ipnstate.Status, error)
	Listen(string) (net.Listener, error)
	Close() error
}

type productionTSNetServer struct {
	server *tsnet.Server
}

func (s productionTSNetServer) Start() error  { return s.server.Start() }
func (s productionTSNetServer) ClearAuthKey() { s.server.AuthKey = "" }
func (s productionTSNetServer) Up(ctx context.Context) (*ipnstate.Status, error) {
	return s.server.Up(ctx)
}
func (s productionTSNetServer) Status(ctx context.Context) (*ipnstate.Status, error) {
	client, err := s.server.LocalClient()
	if err != nil {
		return nil, err
	}
	return client.Status(ctx)
}
func (s productionTSNetServer) Listen(addr string) (net.Listener, error) {
	return s.server.Listen("tcp", addr)
}
func (s productionTSNetServer) Close() error { return s.server.Close() }

// TSNetEngine is the production embedded userspace-tailnet adapter. Successful
// readiness plus a matching expected peer is the positive receipt boundary;
// Sidecar alone commits that receipt and starts connector/listener work.
type TSNetEngine struct {
	server    tsnetServer
	newServer func(EngineConfig, []byte) tsnetServer
}

func NewTSNetEngine() *TSNetEngine {
	return &TSNetEngine{
		newServer: func(c EngineConfig, credential []byte) tsnetServer {
			return productionTSNetServer{server: &tsnet.Server{Dir: c.StateDir, Hostname: c.RoleIdentity, ControlURL: c.ControlURL, AuthKey: string(credential), Ephemeral: false}}
		},
	}
}

func (e *TSNetEngine) Start(ctx context.Context, c EngineConfig, credential []byte) (RedemptionReceipt, error) {
	if err := startupContextError(ctx); err != nil {
		return RedemptionReceipt{}, terminalNetworkFailure(err)
	}
	// Start is contextless upstream work, so it cannot be interrupted here.  The
	// deadline still starts at adapter entry: a late Start return must not mint a
	// fresh readiness/peer budget.
	readyCtx, cancel := context.WithTimeout(ctx, tsnetStartupBudget)
	defer cancel()
	if err := startupContextError(readyCtx); err != nil {
		return RedemptionReceipt{}, terminalNetworkFailure(err)
	}
	if e.newServer == nil {
		return RedemptionReceipt{}, ErrEngineStart
	}
	e.server = e.newServer(c, credential)
	if e.server == nil {
		return RedemptionReceipt{}, ErrEngineStart
	}
	// Allocation may itself consume the original bound. Refuse Start while
	// releasing the allocated server's credential reference on this path too.
	if err := startupContextError(readyCtx); err != nil {
		e.server.ClearAuthKey()
		return RedemptionReceipt{}, terminalNetworkFailure(err)
	}
	startErr := e.server.Start()
	// Release the adapter's string reference on every returned Start result,
	// including failures and late initialization; this admits no further work.
	e.server.ClearAuthKey()
	if startErr != nil {
		return RedemptionReceipt{}, ErrEngineStart
	}
	if err := startupContextError(readyCtx); err != nil {
		return RedemptionReceipt{}, terminalNetworkFailure(err)
	}

	status, err := e.server.Up(readyCtx)
	if terminal := startupContextError(readyCtx); terminal != nil {
		return RedemptionReceipt{}, terminalNetworkFailure(terminal)
	}
	if err != nil || status == nil || status.BackendState != "Running" {
		return RedemptionReceipt{}, readinessNetworkFailure(readyCtx, err, status, false)
	}
	if expectedPeerVisible(status, c.ExpectedPeers) {
		if err := startupContextError(readyCtx); err != nil {
			return RedemptionReceipt{}, terminalNetworkFailure(err)
		}
		return RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, nil
	}

	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-readyCtx.Done():
			if errors.Is(readyCtx.Err(), context.DeadlineExceeded) {
				return RedemptionReceipt{}, NetworkJoinFailure{SubReason: "peer_wait_deadline"}
			}
			return RedemptionReceipt{}, terminalNetworkFailure(readyCtx.Err())
		case <-ticker.C:
			if err := startupContextError(readyCtx); err != nil {
				return RedemptionReceipt{}, terminalNetworkFailure(err)
			}
			status, err = e.server.Status(readyCtx)
			if terminal := startupContextError(readyCtx); terminal != nil {
				return RedemptionReceipt{}, terminalNetworkFailure(terminal)
			}
			if err != nil || status == nil || status.BackendState != "Running" {
				return RedemptionReceipt{}, readinessNetworkFailure(readyCtx, err, status, true)
			}
			if expectedPeerVisible(status, c.ExpectedPeers) {
				if err := startupContextError(readyCtx); err != nil {
					return RedemptionReceipt{}, terminalNetworkFailure(err)
				}
				return RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, nil
			}
		}
	}
}

func terminalNetworkFailure(err error) error {
	if errors.Is(err, context.Canceled) {
		return NetworkJoinFailure{SubReason: "context_cancelled"}
	}
	return NetworkJoinFailure{SubReason: "deadline_exceeded"}
}

func readinessNetworkFailure(ctx context.Context, err error, status *ipnstate.Status, peer bool) error {
	if terminal := startupContextError(ctx); terminal != nil {
		return terminalNetworkFailure(terminal)
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return terminalNetworkFailure(err)
	}
	reason := "up_error_unclassified"
	if peer {
		reason = "peer_status_error"
	}
	if err != nil {
		// Only these two producers are distinguishable in pinned tsnet v1.78.0.
		if !peer {
			if strings.HasPrefix(err.Error(), "tsnet.Up: backend: ") {
				reason = "up_backend_error"
			}
			if err.Error() == "tsnet.Up: running, but no ip" {
				reason = "up_no_ip"
			}
		}
	} else if status == nil {
		reason = "up_status_unavailable"
		if peer {
			reason = "peer_status_unavailable"
		}
	} else {
		reason = "up_not_running"
		if peer {
			reason = "peer_not_running"
		}
	}
	if terminal := startupContextError(ctx); terminal != nil {
		return terminalNetworkFailure(terminal)
	}
	return NetworkJoinFailure{SubReason: reason}
}

func expectedPeerVisible(status *ipnstate.Status, expectedPeers []string) bool {
	if status == nil {
		return false
	}
	for _, peer := range status.Peer {
		if peer == nil {
			continue
		}
		for _, expected := range expectedPeers {
			if peer.HostName == expected || strings.TrimSuffix(peer.DNSName, ".") == expected {
				return true
			}
		}
	}
	return false
}

func (e *TSNetEngine) Listen(addr string) (net.Listener, error) {
	if e.server == nil {
		return nil, ErrEngine
	}
	return e.server.Listen(addr)
}
func (e *TSNetEngine) Close() error {
	if e.server == nil {
		return nil
	}
	return e.server.Close()
}
