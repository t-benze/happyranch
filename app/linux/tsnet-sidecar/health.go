package sidecar

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/netip"
	"os"
	"path/filepath"
	"strings"
	"time"

	"happyranch/tsnethealth"
	"tailscale.com/client/tailscale"
	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tailcfg"
	"tailscale.com/types/key"
)

type healthClient interface {
	Status(context.Context) (*ipnstate.Status, error)
	CurrentDERPMap(context.Context) (*tailcfg.DERPMap, error)
	PingWithOpts(context.Context, netip.Addr, tailcfg.PingType, tailscale.PingOpts) (*ipnstate.PingResult, error)
}

// Called under the lifecycle lock only after all startup gates succeeded.
func (s *Sidecar) startHealth(ctx context.Context) {
	provider, ok := s.engine.(interface{ healthClient() (healthClient, error) })
	if !ok {
		return
	}
	client, err := provider.healthClient()
	if err != nil {
		return
	}
	ctx, s.healthCancel = context.WithCancel(ctx)
	m := healthMonitor{client: client, expected: s.cfg.ExpectedPeer, out: os.Stderr, dir: os.Getenv("RUNTIME_DIRECTORY"), at: time.Now()}
	go func() {
		ticker := time.NewTicker(time.Second)
		defer ticker.Stop()
		m.run(ctx, ticker.C)
	}()
}

type peerPath struct {
	key         key.NodePublic
	addr, relay string
	online      bool
}
type healthState struct {
	Backend string
	Peer    struct {
		Online bool
		Path   string
	}
}
type healthMonitor struct {
	client        healthClient
	expected, dir string
	out           io.Writer
	at            time.Time
	runner        *tsnethealth.Runner
	dm            *tailcfg.DERPMap
	probe         tsnethealth.ProbeResult
	age           time.Duration
	measured      time.Time
	ping          *ipnstate.PingResult
	verified      peerPath
	previous      healthState
}

func (m *healthMonitor) run(ctx context.Context, ticks <-chan time.Time) {
	m.runner = tsnethealth.NewRunner(func() time.Time { return m.at })
	next := m.at.Add(5 * time.Minute)
	m.sample(ctx, "join")
	for {
		select {
		case <-ctx.Done():
			return
		case at := <-ticks:
			m.at = at
			trigger := "change"
			if !at.Before(next) {
				trigger, next = "periodic", at.Add(5*time.Minute)
			}
			m.sample(ctx, trigger)
		}
	}
}

func (m *healthMonitor) sample(ctx context.Context, trigger string) {
	// All LocalAPI calls are bounded and cancelled with the service.
	serviceCtx := ctx
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	status, err := m.client.Status(ctx)
	if err != nil {
		status = nil
	}
	var selected key.NodePublic
	var peer *ipnstate.PeerStatus
	if status != nil {
		for k, p := range status.Peer {
			if p != nil && (p.HostName == m.expected || strings.TrimSuffix(p.DNSName, ".") == m.expected) {
				// Ambiguous identities must not expose an arbitrary matching node.
				if peer != nil {
					selected, peer = key.NodePublic{}, nil
					break
				}
				selected, peer = k, p
			}
		}
	}
	path := peerPath{key: selected}
	if peer != nil {
		path.addr, path.relay, path.online = peer.CurAddr, peer.Relay, peer.Online
	}
	if trigger != "change" {
		m.dm, err = m.client.CurrentDERPMap(ctx)
		if err != nil {
			m.dm = nil
		}
		m.probe, m.age, _ = m.runner.Refresh(ctx, m.dm, trigger == "join")
		m.measured = m.at
		m.ping = nil
		if peer != nil && len(peer.TailscaleIPs) > 0 {
			m.ping, err = m.runner.Ping(ctx, func(ctx context.Context) (*ipnstate.PingResult, error) {
				return m.client.PingWithOpts(ctx, peer.TailscaleIPs[0], tailcfg.PingDisco, tailscale.PingOpts{})
			})
			if err != nil {
				m.ping = nil
			}
		}
		m.verified = path
	}
	ping := m.ping
	if path != m.verified || !path.online {
		ping = nil
	}
	record, err := tsnethealth.BuildRecord(m.at, trigger, status, selected, ping, m.dm, m.probe, m.age+m.at.Sub(m.measured))
	if err != nil || serviceCtx.Err() != nil {
		return
	}
	data, err := json.Marshal(record)
	if err != nil {
		return
	}
	var state healthState
	if json.Unmarshal(data, &state) != nil {
		return
	}
	if trigger == "change" && (state == m.previous || !m.runner.AllowChange()) {
		return
	}
	m.previous = state
	// No raw LocalAPI, probe, serialization or filesystem errors enter the journal.
	fmt.Fprintf(m.out, "tsnet-health %s\n", data)
	_ = writeHealthFile(m.dir, data)
}

func writeHealthFile(dir string, data []byte) error {
	if dir == "" {
		return nil
	}
	target := filepath.Join(dir, "health.json")
	regular := func() error {
		st, err := os.Lstat(target)
		if os.IsNotExist(err) {
			return nil
		}
		if err != nil {
			return err
		}
		if !st.Mode().IsRegular() {
			return errors.New("health target unavailable")
		}
		return nil
	}
	if err := regular(); err != nil {
		return err
	}
	f, err := os.CreateTemp(dir, ".health-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	defer f.Close()
	if err = f.Chmod(0644); err != nil {
		return err
	}
	if _, err = f.Write(data); err != nil {
		return err
	}
	if err = f.Sync(); err != nil {
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	if err = regular(); err != nil {
		return err
	}
	return os.Rename(f.Name(), target)
}
