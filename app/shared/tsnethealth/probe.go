// Package tsnethealth measures network health without retaining addresses.
package tsnethealth

import (
	"context"
	"net"
	"net/netip"
	"sync"
	"time"

	"tailscale.com/net/stun"
	"tailscale.com/tailcfg"
)

// ProbeResult contains only measurements, never the mapped address or DERP hosts.
// Region IDs are translated to region codes by the health-record builder.
type ProbeResult struct {
	DERPMillis map[int]int
	UDP        bool
	NATVaries  bool
}

type destination struct {
	region int
	node   *tailcfg.DERPNode
}

// Probe sends at most eight STUN requests using one UDP socket and a single
// five-second deadline, including DNS. Only the caller's map supplies targets.
// It does not select a home DERP, detect captive portals, or probe port mappers.
// An empty result means DERP unreachable; callers must emit portmap "unknown".
// Callers must not mutate dm during the call. Partial results survive timeout.
func Probe(ctx context.Context, dm *tailcfg.DERPMap) ProbeResult {
	result := ProbeResult{DERPMillis: map[int]int{}}
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	var targets []destination
	if dm != nil {
		for _, id := range dm.RegionIDs() {
			region := dm.Regions[id]
			if region == nil {
				continue
			}
			for _, node := range region.Nodes {
				if node != nil && node.STUNPort != -1 {
					targets = append(targets, destination{id, node})
					break
				}
			}
			if len(targets) == 8 {
				break
			}
		}
	}
	if len(targets) == 0 || ctx.Err() != nil {
		return result
	}
	var config net.ListenConfig
	conn, err := config.ListenPacket(ctx, "udp", ":0")
	if err != nil {
		return result
	}
	defer conn.Close()
	stop := context.AfterFunc(ctx, func() { conn.Close() })
	defer stop()
	deadline, _ := ctx.Deadline()
	if conn.SetDeadline(deadline) != nil {
		return result
	}
	type pendingProbe struct {
		region int
		addr   netip.AddrPort
		sent   time.Time
	}
	// The receiver must keep draining replies while later map hosts resolve.
	// Only pending and sending are shared; measurements stay receiver-owned.
	var mu sync.Mutex
	pending := map[stun.TxID]pendingProbe{}
	sending := true
	sentDone := make(chan struct{})
	go func() {
		defer close(sentDone)
		for _, target := range targets {
			addr, err := nodeAddress(ctx, target.node)
			if err != nil || ctx.Err() != nil {
				continue
			}
			tx := stun.NewTxID()
			mu.Lock()
			pending[tx] = pendingProbe{target.region, addr, time.Now()}
			if _, err := conn.WriteTo(stun.Request(tx), net.UDPAddrFromAddrPort(addr)); err != nil {
				delete(pending, tx)
			}
			mu.Unlock()
		}
		mu.Lock()
		sending = false
		if len(pending) == 0 {
			// Wake a receiver waiting after the final reply or failed send.
			conn.SetReadDeadline(time.Now())
		}
		mu.Unlock()
	}()
	var firstMapped netip.AddrPort
	buffer := make([]byte, 2048)
	for {
		n, source, err := conn.ReadFrom(buffer)
		if err != nil {
			break
		}
		received := time.Now()
		tx, mapped, err := stun.ParseResponse(buffer[:n])
		mu.Lock()
		probe, ok := pending[tx]
		from, isUDP := source.(*net.UDPAddr)
		if err != nil || !mapped.IsValid() || !ok || !isUDP || from.AddrPort().Addr().Unmap() != probe.addr.Addr() || from.Port != int(probe.addr.Port()) {
			mu.Unlock()
			continue
		}
		delete(pending, tx)
		finished := !sending && len(pending) == 0
		mu.Unlock()
		result.DERPMillis[probe.region] = int(received.Sub(probe.sent).Milliseconds())
		result.UDP = true
		if firstMapped.IsValid() && firstMapped != mapped {
			result.NATVaries = true
		}
		firstMapped = mapped
		if finished {
			break
		}
	}
	cancel()
	<-sentDone
	return result
}

func nodeAddress(ctx context.Context, node *tailcfg.DERPNode) (netip.AddrPort, error) {
	port := node.STUNPort
	if port == 0 {
		port = 3478
	}
	if port < 1 || port > 65535 {
		return netip.AddrPort{}, &net.AddrError{Err: "invalid STUN port"}
	}
	addr, _ := netip.ParseAddr(node.IPv4)
	if !addr.Is4() {
		addr, _ = netip.ParseAddr(node.IPv6)
		if !addr.Is6() {
			addresses, err := net.DefaultResolver.LookupNetIP(ctx, "ip", node.HostName)
			if err != nil {
				return netip.AddrPort{}, err
			}
			if len(addresses) == 0 {
				return netip.AddrPort{}, &net.AddrError{Err: "no DERP addresses"}
			}
			addr = addresses[0]
		}
	}
	return netip.AddrPortFrom(addr.Unmap(), uint16(port)), nil
}
