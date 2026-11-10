package sidecar

import (
	"context"
	"net"
	"strings"
	"time"

	"tailscale.com/tsnet"
)

// TSNetEngine is the production embedded userspace-tailnet adapter. Server.Start
// launches the backend; readiness requires the bounded Server.Up wait as well.
type TSNetEngine struct{ server *tsnet.Server }

func NewTSNetEngine() *TSNetEngine { return &TSNetEngine{} }
func (e *TSNetEngine) Start(ctx context.Context, c EngineConfig, credential []byte) (RedemptionReceipt, error) {
	e.server = &tsnet.Server{Dir: c.StateDir, Hostname: c.RoleIdentity, ControlURL: c.ControlURL, AuthKey: string(credential), Ephemeral: false}
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
