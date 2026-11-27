package main

import (
	"context"
	"crypto/tls"
	"crypto/x509"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"golang.org/x/net/proxy"
	"tailscale.com/derp"
	"tailscale.com/derp/derphttp"
	"tailscale.com/ipn/store/mem"
	"tailscale.com/logtail"
	"tailscale.com/net/memnet"
	"tailscale.com/tailcfg"
	"tailscale.com/tsnet"
	"tailscale.com/tstest/integration/testcontrol"
	"tailscale.com/types/key"
	"tailscale.com/types/logger"
)

type peerCensusRow struct{ kind, address string }

// Join kernel socket tables to this process's descriptors. Read errors fail the
// test; unrelated sockets in the network namespace cannot mask a regression.
func peerCensus(t *testing.T) []peerCensusRow {
	t.Helper()
	entries, err := os.ReadDir("/proc/self/fd")
	if err != nil {
		t.Fatal(err)
	}
	owned := map[string]bool{}
	for _, entry := range entries {
		target, err := os.Readlink("/proc/self/fd/" + entry.Name())
		if os.IsNotExist(err) {
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		if strings.HasPrefix(target, "socket:[") {
			owned[strings.TrimSuffix(strings.TrimPrefix(target, "socket:["), "]")] = true
		}
	}
	var rows []peerCensusRow
	for _, kind := range []string{"tcp", "tcp6", "udp", "udp6", "unix"} {
		data, err := os.ReadFile("/proc/self/net/" + kind)
		if err != nil {
			t.Fatal(err)
		}
		for _, line := range strings.Split(string(data), "\n")[1:] {
			fields := strings.Fields(line)
			if kind == "unix" {
				if len(fields) >= 8 && owned[fields[6]] && strings.HasPrefix(fields[7], "/") {
					rows = append(rows, peerCensusRow{kind, fields[7]})
				}
			} else if len(fields) >= 10 && owned[fields[9]] && (!strings.HasPrefix(kind, "tcp") || fields[3] == "0A") {
				rows = append(rows, peerCensusRow{kind, fields[1]})
			}
		}
	}
	t.Logf("process-owned kernel sockets (UDP reported separately): %v", rows)
	return rows
}

func peerCensusListeners(rows []peerCensusRow) int {
	n := 0
	for _, row := range rows {
		if row.kind == "tcp" || row.kind == "tcp6" || row.kind == "unix" {
			n++
		}
	}
	return n
}

type peerCensusDialer struct {
	control, derp func(context.Context, string, string) (net.Conn, error)
}

func (d peerCensusDialer) Dial(network, address string) (net.Conn, error) {
	return d.DialContext(context.Background(), network, address)
}

func (d peerCensusDialer) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	if network != "tcp" && network != "tcp4" {
		return nil, fmt.Errorf("fixture refuses destination")
	}
	// DERP requests tcp4, while memnet.Listener.Dial accepts only tcp.
	// The exact fixture address already fixes IPv4; no kernel dial is involved.
	switch address {
	case "127.0.0.1:18080":
		return d.control(ctx, "tcp", address)
	case "127.0.0.1:18443":
		return d.derp(ctx, "tcp", address)
	default:
		return nil, fmt.Errorf("fixture refuses destination")
	}
}

func TestClientPeerAPIListeners(t *testing.T) {
	// tsnet and netns cache process globals. An isolated test process leaves other
	// bridge tests' proxy/root configuration untouched and owns the whole census.
	if os.Getenv("HR_PEERAPI_CENSUS_CHILD") != "1" {
		ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
		defer cancel()
		command := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestClientPeerAPIListeners$", "-test.v")
		for _, entry := range os.Environ() {
			key, _, _ := strings.Cut(entry, "=")
			if strings.HasPrefix(key, "TS_") {
				continue
			}
			switch strings.ToLower(key) {
			case "http_proxy", "https_proxy", "all_proxy", "no_proxy", "hr_peerapi_census_child", "godebug", "ssl_cert_file", "ssl_cert_dir", "sslkeylogfile":
				continue
			}
			command.Env = append(command.Env, entry)
		}
		// Control and DERP dials use only memory fixtures. Keep normal UDP
		// lifecycle: the upstream force-DERP debug mode leaks receive waits.
		command.Env = append(command.Env, "HR_PEERAPI_CENSUS_CHILD=1", "ALL_PROXY=hrpeerapi://memory", "GODEBUG=x509usefallbackroots=1")
		output, err := command.CombinedOutput()
		t.Logf("%s", output)
		if err != nil {
			t.Fatal(err)
		}
		return
	}
	log.SetOutput(io.Discard) // Synthetic node keys/control payloads stay private.
	logtail.Disable()
	if n := peerCensusListeners(peerCensus(t)); n != 0 {
		t.Fatalf("dirty census baseline: %d listeners", n)
	}
	// Positive controls cover both IP families and a filesystem Unix socket.
	// The short, private socket directory avoids AF_UNIX's path length limit.
	dir, err := os.MkdirTemp("/tmp", "hr-peerapi-")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	var controls []net.Listener
	for _, item := range [][2]string{{"tcp4", "127.0.0.1:0"}, {"tcp6", "[::1]:0"}, {"unix", filepath.Join(dir, "positive")}} {
		listener, err := net.Listen(item[0], item[1])
		if err != nil {
			t.Fatal(err)
		}
		defer listener.Close()
		controls = append(controls, listener)
	}
	if n := peerCensusListeners(peerCensus(t)); n != 3 {
		t.Fatalf("census missed positive controls: got %d, want 3", n)
	}
	for _, listener := range controls {
		if err := listener.Close(); err != nil {
			t.Fatal(err)
		}
	}
	if n := peerCensusListeners(peerCensus(t)); n != 0 {
		t.Fatalf("positive controls left %d listeners", n)
	}
	fixture := memnet.Listen("127.0.0.1:18080")
	defer fixture.Close()
	derpFixture := memnet.Listen("127.0.0.1:18443")
	defer derpFixture.Close()
	ca := makeTransportCA(t, true)
	roots := x509.NewCertPool()
	roots.AddCert(ca.cert)
	x509.SetFallbackRoots(roots)
	derper := derp.NewServer(key.NewNode(), logger.Discard)
	defer derper.Close()
	var derpRequests, derpUpgrades atomic.Int64
	derpHandler := derphttp.Handler(derper)
	derpHTTP := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		derpRequests.Add(1)
		if strings.EqualFold(r.Header.Get("Upgrade"), "DERP") {
			derpUpgrades.Add(1)
		}
		derpHandler.ServeHTTP(w, r)
	}), ErrorLog: log.New(io.Discard, "", 0)}
	defer func() { t.Logf("memory DERP requests=%d upgrades=%d", derpRequests.Load(), derpUpgrades.Load()) }()
	go derpHTTP.Serve(tls.NewListener(derpFixture, &tls.Config{Certificates: []tls.Certificate{transportLeaf(t, ca, "ip-only")}, MinVersion: tls.VersionTLS12}))
	defer derpHTTP.Close()
	proxy.RegisterDialerType("hrpeerapi", func(_ *url.URL, _ proxy.Dialer) (proxy.Dialer, error) {
		return peerCensusDialer{fixture.Dial, derpFixture.Dial}, nil
	})
	// Control/Noise and TLS DERP all use memory listeners. No fixture kernel
	// listeners are subtracted from the census and no external service is used.
	region := &tailcfg.DERPRegion{RegionID: 1, RegionCode: "mem", RegionName: "memory", Nodes: []*tailcfg.DERPNode{{Name: "mem1", RegionID: 1, HostName: "127.0.0.1", IPv4: "127.0.0.1", IPv6: "none", DERPPort: 18443, STUNPort: -1}}}
	control := &testcontrol.Server{ExplicitBaseURL: "http://127.0.0.1:18080", DERPMap: &tailcfg.DERPMap{Regions: map[int]*tailcfg.DERPRegion{1: region}}, Logf: logger.Discard}
	server := &http.Server{Handler: control, ErrorLog: log.New(io.Discard, "", 0)}
	go server.Serve(fixture)
	defer server.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 40*time.Second)
	defer cancel()
	nodes := make([]*tsnet.Server, 2)
	for i := range nodes {
		node := &tsnet.Server{Dir: t.TempDir(), Store: new(mem.Store), Ephemeral: true, ControlURL: control.ExplicitBaseURL, Hostname: fmt.Sprintf("peerapi-census-%d", i), Logf: logger.Discard, UserLogf: logger.Discard}
		nodes[i] = node
		defer node.Close()
		if _, err := node.Up(ctx); err != nil {
			t.Fatal(err)
		}
	}
	client, err := nodes[1].LocalClient()
	if err != nil {
		t.Fatal(err)
	}
	var peerURL string
	from, err := nodes[0].LocalClient()
	if err != nil {
		t.Fatal(err)
	}
	for ctx.Err() == nil {
		status, err := client.StatusWithoutPeers(ctx)
		if err != nil {
			t.Fatal(err)
		}
		fromStatus, err := from.Status(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if status.Self != nil && len(status.Self.PeerAPIURL) > 0 {
			// Running/Self alone precedes asynchronous map updates. Wait until
			// the caller has the target's actual advertised PeerAPI service.
			if peer := fromStatus.Peer[status.Self.PublicKey]; peer != nil && len(peer.PeerAPIURL) > 0 && peer.PeerAPIURL[0] == status.Self.PeerAPIURL[0] {
				peerURL = status.Self.PeerAPIURL[0]
				// Upstream's tsnet tests establish peer connectivity before TCP.
				// A successful encrypted ping is separate from the HTTP oracle.
				ping, err := from.Ping(ctx, status.TailscaleIPs[0], tailcfg.PingICMP)
				if err != nil || ping == nil || ping.Err != "" {
					t.Logf("memory DERP requests=%d upgrades=%d; ping request error=%t response missing=%t", derpRequests.Load(), derpUpgrades.Load(), err != nil, ping == nil)
					t.Fatal("memory tailnet ping failed before PeerAPI request")
				}
				t.Log("memory tailnet encrypted ping succeeded")
				break
			}
		}
		time.Sleep(20 * time.Millisecond)
	}
	if peerURL == "" {
		t.Fatal("PeerAPI never advertised")
	}
	beforeRequest := peerCensus(t)
	transport := &http.Transport{DialContext: nodes[0].Dial}
	defer transport.CloseIdleConnections()
	request, err := http.NewRequestWithContext(ctx, "GET", peerURL+"/", nil)
	if err != nil {
		t.Fatal(err)
	}
	response, err := (&http.Client{Transport: transport}).Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	body, err := io.ReadAll(io.LimitReader(response.Body, 65536))
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusOK || !strings.Contains(string(body), "<h1>Hello,") {
		t.Fatalf("in-tailnet PeerAPI control failed: status=%d", response.StatusCode)
	}
	t.Log("in-tailnet PeerAPI served HTTP 200 with its greeting")
	if derpUpgrades.Load() < 2 {
		t.Fatal("both nodes must use the memory DERP fixture")
	}
	if control.NumNodes() != 2 {
		t.Fatalf("control fixture has %d nodes, want 2", control.NumNodes())
	}
	if n := peerCensusListeners(beforeRequest); n != 0 {
		t.Errorf("PeerAPI kernel listener census: got %d TCP/filesystem Unix listeners, want zero", n)
	}
	if n := peerCensusListeners(peerCensus(t)); n != 0 {
		t.Errorf("PeerAPI after request: got %d TCP/filesystem Unix listeners, want zero", n)
	}
}
