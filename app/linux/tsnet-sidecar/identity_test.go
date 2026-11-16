//go:build linux

package sidecar

import (
	"context"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"tailscale.com/client/tailscale/apitype"
	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tailcfg"
)

type identityLookup struct {
	response *apitype.WhoIsResponse
	err      error
	remote   chan string
}

func (c *identityLookup) WhoIs(_ context.Context, remote string) (*apitype.WhoIsResponse, error) {
	if c.remote != nil {
		c.remote <- remote
	}
	return c.response, c.err
}

type identityEngine struct {
	*fakeEngine
	resolver *TSNetEngine
}

func (e *identityEngine) peerIdentity(ctx context.Context, remote string) (string, error) {
	return e.resolver.peerIdentity(ctx, remote)
}

// Existing production Dialer maps the fixed endpoint to fixture storage. Every
// connect/read/write/close is a real Unix socket, with no transport mock.
func unixFixture(t *testing.T) (net.Listener, Dialer) {
	t.Helper()
	dir, err := os.MkdirTemp("/tmp", "hrid-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	path := filepath.Join(dir, "ingress.sock")
	l, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Close() })
	return l, dialFunc(func(ctx context.Context, network, address string) (net.Conn, error) {
		if network != "unix" || address != "/run/happyranch-connector/ingress.sock" {
			return nil, errors.New("wrong endpoint")
		}
		return (&net.Dialer{Timeout: time.Second}).DialContext(ctx, network, path)
	})
}
func managedConfig(t *testing.T) Config {
	cfg := validConfig(t)
	cfg.Profile = "linux-managed-identity-v1"
	cfg.ExpectedPeer = ""
	cfg.ConnectorAddr = ""
	return cfg
}
func inboundListener(t *testing.T) net.Listener {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { l.Close() })
	return l
}
func managedEngine(t *testing.T, l net.Listener, lookup *identityLookup) *identityEngine {
	t.Helper()
	events := []string{}
	e := &identityEngine{&fakeEngine{events: &events, listener: l, receipt: RedemptionReceipt{true, true, false}}, &TSNetEngine{}}
	if lookup != nil {
		e.resolver.identityClient = lookup
	}
	return e
}
func TestIdentityRefusalsCloseBeforeLocalDial(t *testing.T) {
	for _, tc := range []struct {
		name   string
		lookup *identityLookup
		absent bool
	}{
		{"absent-client", nil, false}, {"failed-whois", &identityLookup{err: errors.New("private")}, false},
		{"nil-response", &identityLookup{}, false}, {"nil-node", &identityLookup{response: &apitype.WhoIsResponse{}}, false},
		{"empty-stable-id", &identityLookup{response: &apitype.WhoIsResponse{Node: &tailcfg.Node{Name: "hostname-not-identity"}}}, false},
		{"frame-build-oversize", &identityLookup{response: &apitype.WhoIsResponse{Node: &tailcfg.Node{StableID: tailcfg.StableNodeID(strings.Repeat("n", 1018))}}}, false},
		{"absent-resolver", nil, true}} {
		t.Run(tc.name, func(t *testing.T) {
			local, dialer := unixFixture(t)
			inbound := inboundListener(t)

			e := managedEngine(t, inbound, tc.lookup)
			var engine Engine = e
			if tc.absent {
				engine = e.fakeEngine
			}
			s := New(managedConfig(t), engine, dialer)
			t.Cleanup(func() { s.Stop() })
			if err := s.Start(context.Background()); err != nil {
				t.Fatal(err)
			}
			probe, err := local.Accept()
			if err != nil {
				t.Fatal(err)
			}
			probe.Close()
			c, err := net.Dial("tcp", inbound.Addr().String())
			if err != nil {
				t.Fatal(err)
			}
			defer c.Close()
			c.SetReadDeadline(time.Now().Add(2 * time.Second))
			n, err := c.Read(make([]byte, 1))
			if n != 0 || err != io.EOF {
				t.Fatalf("remote not closed: n=%d err=%v", n, err)
			}
			local.(*net.UnixListener).SetDeadline(time.Now().Add(50 * time.Millisecond))
			if extra, err := local.Accept(); err == nil {
				extra.Close()
				t.Fatal("refusal dialed the local socket")
			} else if n, ok := err.(net.Error); !ok || !n.Timeout() {
				t.Fatal(err)
			}
		})
	}
}
func TestIdentityOneRemoteOneUnixStream(t *testing.T) {
	local, dialer := unixFixture(t)
	inbound := inboundListener(t)
	lookup := &identityLookup{response: &apitype.WhoIsResponse{Node: &tailcfg.Node{StableID: "node-123"}}, remote: make(chan string, 2)}
	s := New(managedConfig(t), managedEngine(t, inbound, lookup), dialer)
	defer s.Stop()
	if err := s.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	probe, err := local.Accept()
	if err != nil {
		t.Fatal(err)
	}
	probe.Close()
	seen := make(chan string, 2)
	go func() {
		for range 2 {
			c, err := local.Accept()
			if err != nil {
				return
			}
			go func(c net.Conn) {
				defer c.Close()
				c.SetDeadline(time.Now().Add(2 * time.Second))
				b := make([]byte, 19)
				_, err := io.ReadFull(c, b)
				if err == nil {
					seen <- string(b)
					c.Write([]byte("ok"))
				}
			}(c)
		}
	}()
	for _, node := range []tailcfg.StableNodeID{"node-123", "node-456"} {
		lookup.response.Node.StableID = node
		c, err := net.Dial("tcp", inbound.Addr().String())
		if err != nil {
			t.Fatal(err)
		}
		c.SetDeadline(time.Now().Add(2 * time.Second))
		c.Write([]byte("HTTP"))
		b := make([]byte, 2)
		if _, err := io.ReadFull(c, b); err != nil || string(b) != "ok" {
			t.Fatalf("copy %q %v", b, err)
		}
		if got := <-lookup.remote; got != c.LocalAddr().String() {
			t.Fatalf("WhoIs remote=%s", got)
		}
		c.Close()
	}
	for _, node := range []string{"node-123", "node-456"} {
		select {
		case got := <-seen:
			if got != "HRID\x01\x00\x08"+node+"HTTP" {
				t.Fatalf("wire=%q", got)
			}
		case <-time.After(2 * time.Second):
			t.Fatal("pooled connection or missing frame")
		}
	}
	// Runtime connector loss refuses the next remote connection even though
	// this isolated test has no host systemd BindsTo owner to stop the sidecar.
	local.Close()
	c, err := net.Dial("tcp", inbound.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	c.SetReadDeadline(time.Now().Add(2 * time.Second))
	if n, err := c.Read(make([]byte, 1)); n != 0 || err != io.EOF {
		t.Fatalf("connector loss left remote open: n=%d err=%v", n, err)
	}

}
func TestIdentityProfileReadinessAndLegacyGate(t *testing.T) {
	cfg := managedConfig(t)
	if err := cfg.Validate(); err != nil {
		t.Fatal(err)
	}
	for name, mutation := range map[string]func(*Config){
		"unknown-profile":      func(c *Config) { c.Profile = "future-v2" },
		"caller-socket":        func(c *Config) { c.ConnectorAddr = "/tmp/chosen.sock" },
		"legacy-peer-required": func(c *Config) { c.Profile = ""; c.ConnectorAddr = "127.0.0.1:9443" },
	} {
		t.Run(name, func(t *testing.T) {
			bad := cfg
			mutation(&bad)
			if bad.Validate() == nil {
				t.Fatal("unsafe or legacy config accepted")
			}
		})
	}
	if !expectedPeerVisible(EngineConfig{Profile: LinuxIdentityProfile}, &ipnstate.Status{BackendState: "Running"}) {
		t.Fatal("empty-peer profile not ready")
	}
	if expectedPeerVisible(EngineConfig{ExpectedPeer: "legacy"}, &ipnstate.Status{BackendState: "Running"}) {
		t.Fatal("legacy peer gate lost")
	}
	local, dialer := unixFixture(t)
	for range 2 {
		events := []string{}
		e := &fakeEngine{events: &events, listener: inboundListener(t), receipt: RedemptionReceipt{true, true, false}}
		s := New(cfg, e, dialer)
		if err := s.Start(context.Background()); err != nil {
			t.Fatal(err)
		}
		c, err := local.Accept()
		if err != nil {
			t.Fatal(err)
		}
		c.Close()
		if !s.Running() {
			t.Fatal("empty-peer start/restart not READY")
		}
		if err := s.Stop(); err != nil {
			t.Fatal(err)
		}
	}
	local.Close()
	events := []string{}
	s := New(cfg, &fakeEngine{events: &events, receipt: RedemptionReceipt{true, true, false}}, dialer)
	if err := s.Start(context.Background()); !errors.Is(err, ErrConnector) {
		t.Fatalf("connector loss gate=%v", err)
	}
	s.Stop()
}
func TestIdentityFrameEncodingBound(t *testing.T) {
	for _, size := range []int{1, 1017} {
		b, err := identityFrame(strings.Repeat("n", size))
		if err != nil || len(b) > 1024 || string(b[:5]) != "HRID\x01" || int(binary.BigEndian.Uint16(b[5:7])) != size {
			t.Fatalf("frame size=%d %v", len(b), err)
		}
	}
	for name, id := range map[string]string{"empty": "", "blank": " ", "oversize": strings.Repeat("n", 1018), "control-character": "node\nforged", "invalid-utf8": "\xff"} {
		t.Run(name, func(t *testing.T) {
			if _, err := identityFrame(id); err == nil {
				t.Fatal("invalid identity encoded")
			}
		})
	}
}
