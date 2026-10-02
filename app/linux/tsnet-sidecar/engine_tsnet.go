package sidecar

import (
	"context"
	"net"
	"strings"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tsnet"
)

// The connector's independent startup timer is 80 seconds. The adapter uses
// that same upper envelope as a cap, but does not claim that the two clocks
// are identical or alter the connector's timer.
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

type tsnetLocalClient interface {
	Status(context.Context) (*ipnstate.Status, error)
}

type productionTSNetServer struct {
	server      *tsnet.Server
	localClient func() (tsnetLocalClient, error)
}

func (s productionTSNetServer) Start() error  { return s.server.Start() }
func (s productionTSNetServer) ClearAuthKey() { s.server.AuthKey = "" }
func (s productionTSNetServer) Up(ctx context.Context) (*ipnstate.Status, error) {
	return s.server.Up(ctx)
}
func (s productionTSNetServer) Status(ctx context.Context) (*ipnstate.Status, error) {
	localClient := s.localClient
	if localClient == nil {
		localClient = func() (tsnetLocalClient, error) { return s.server.LocalClient() }
	}
	client, err := localClient()
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
	server           tsnetServer
	newServer        func(EngineConfig, []byte) tsnetServer
	peerPollInterval time.Duration
	now              func() time.Time
}

func NewTSNetEngine() *TSNetEngine {
	return &TSNetEngine{
		newServer: func(c EngineConfig, credential []byte) tsnetServer {
			return productionTSNetServer{server: &tsnet.Server{Dir: c.StateDir, Hostname: c.RoleIdentity, ControlURL: c.ControlURL, AuthKey: string(credential), Ephemeral: false}}
		},
		peerPollInterval: 200 * time.Millisecond,
		now:              time.Now,
	}
}

func (e *TSNetEngine) Start(ctx context.Context, c EngineConfig, credential []byte) (RedemptionReceipt, error) {
	if ctx.Err() != nil {
		return RedemptionReceipt{}, ErrNetworkJoin
	}
	// Start is contextless upstream work, so it cannot be interrupted here.  The
	// deadline still starts at adapter entry: a late Start return must not mint a
	// fresh readiness/peer budget.
	readyCtx, cancel := context.WithTimeout(ctx, tsnetStartupBudget)
	defer cancel()
	if e.newServer == nil {
		return RedemptionReceipt{}, ErrEngineStart
	}
	e.server = e.newServer(c, credential)
	if e.server == nil {
		return RedemptionReceipt{}, ErrEngineStart
	}
	if err := e.server.Start(); err != nil {
		return RedemptionReceipt{}, ErrEngineStart
	}
	now := e.now
	if now == nil {
		now = time.Now
	}
	if ctx.Err() != nil {
		return RedemptionReceipt{}, ErrNetworkJoin
	}
	if deadline, ok := ctx.Deadline(); ok && !now().Before(deadline) {
		return RedemptionReceipt{}, ErrNetworkJoin
	}
	e.server.ClearAuthKey()
	if readyCtx.Err() != nil {
		return RedemptionReceipt{}, ErrNetworkJoin
	}

	status, err := e.server.Up(readyCtx)
	if err != nil || readyCtx.Err() != nil || status == nil || status.BackendState != "Running" {
		return RedemptionReceipt{}, ErrNetworkJoin
	}
	if expectedPeerVisible(status, c.ExpectedPeers) {
		return RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, nil
	}

	poll := e.peerPollInterval
	if poll <= 0 {
		poll = 200 * time.Millisecond
	}
	ticker := time.NewTicker(poll)
	defer ticker.Stop()
	for {
		select {
		case <-readyCtx.Done():
			return RedemptionReceipt{}, ErrNetworkJoin
		case <-ticker.C:
			status, err = e.server.Status(readyCtx)
			if err != nil || readyCtx.Err() != nil || status == nil || status.BackendState != "Running" {
				return RedemptionReceipt{}, ErrNetworkJoin
			}
			if expectedPeerVisible(status, c.ExpectedPeers) {
				return RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, nil
			}
		}
	}
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
