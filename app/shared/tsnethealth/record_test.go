package tsnethealth

import (
	"encoding/json"
	"fmt"
	"net/netip"
	"reflect"
	"strings"
	"testing"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tailcfg"
	"tailscale.com/types/key"
)

func TestRecordSentinels(t *testing.T) {
	node, other, disco := key.NewNode(), key.NewNode(), key.NewDisco()
	machine := key.NewMachine()
	machinePrivate, _ := machine.MarshalText()
	sentinels := []string{node.UntypedHexString(), node.Public().String(), other.UntypedHexString(), other.Public().String(), "discopriv:" + strings.Repeat("1", 64), disco.Public().String(), "198.51.100.23:4567", "203.0.113.43:9876", "100.81.2.3", "100.99.8.7", "other.magic.invalid", "https://login.secret.invalid/auth", "raw-health.secret.invalid", "derp.secret.invalid", "192.0.2.54"}
	sentinels = append(sentinels, string(machinePrivate), machine.Public().String())
	ip := netip.MustParseAddr(sentinels[8])
	status := &ipnstate.Status{BackendState: "Running", Version: sentinels[0], AuthURL: sentinels[11], TailscaleIPs: []netip.Addr{netip.MustParseAddr(sentinels[9])}, Health: append(append([]string{"warning https://" + sentinels[12]}, sentinels[:6]...), sentinels[15:]...), Self: &ipnstate.PeerStatus{PublicKey: other.Public(), Relay: "hk", Addrs: []string{sentinels[6]}}}
	status.Peer = map[key.NodePublic]*ipnstate.PeerStatus{
		node.Public():  {PublicKey: node.Public(), HostName: "expected-peer", DNSName: "expected.magic.invalid.", TailscaleIPs: []netip.Addr{ip}, Online: true, CurAddr: sentinels[6], Addrs: []string{sentinels[7]}, Relay: "hk"},
		other.Public(): {PublicKey: other.Public(), HostName: sentinels[10], DNSName: sentinels[10], TailscaleIPs: status.TailscaleIPs},
	}
	dm := &tailcfg.DERPMap{Regions: map[int]*tailcfg.DERPRegion{1: {RegionID: 1, RegionCode: "hk", Nodes: []*tailcfg.DERPNode{{HostName: sentinels[13], IPv4: sentinels[14]}}}}}
	ping := &ipnstate.PingResult{IP: ip.String(), NodeIP: sentinels[9], NodeName: sentinels[10], Endpoint: sentinels[7], PeerAPIURL: sentinels[11], LatencySeconds: .012}
	r, err := BuildRecord(time.Unix(1000, 0), "join", status, node.Public(), ping, dm, ProbeResult{DERPMillis: map[int]int{1: 38}, UDP: true}, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	b, err := json.Marshal(r)
	if err != nil || len(b) > 2048 {
		t.Fatalf("marshal size=%d err=%v", len(b), err)
	}
	for _, s := range append(sentinels, "expected.magic.invalid") {
		if strings.Contains(string(b), s) {
			t.Errorf("sentinel leaked: %q", s)
		}
	}
	var got map[string]any
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatal(err)
	}
	peer := got["peer"].(map[string]any)
	if len(got) != 12 || len(peer) != 7 || got["v"] != float64(1) || got["at"] != "1970-01-01T00:15:40Z" || got["portmap"] != "unknown" || got["derp_home"] != "hk" || !reflect.DeepEqual(got["derp_ms"], map[string]any{"hk": float64(38)}) || peer["name"] != "expected-peer" || peer["path"] != "direct" || peer["direct_kind"] != "public" || peer["rtt_ms"] != float64(12) {
		t.Fatalf("projection: %s", b)
	}
	status.Peer[node.Public()].HostName = "later-mutation"
	probeAfter, _ := json.Marshal(r)
	if string(probeAfter) != string(b) {
		t.Fatal("record retains mutable source")
	}
}

func TestRecordBounds(t *testing.T) {
	k := key.NewNode().Public()
	s := &ipnstate.Status{BackendState: "Running", Peer: map[key.NodePublic]*ipnstate.PeerStatus{k: {HostName: "peer"}}}
	dm, p := &tailcfg.DERPMap{Regions: map[int]*tailcfg.DERPRegion{}}, ProbeResult{DERPMillis: map[int]int{}, UDP: true}
	for id := 12; id > 0; id-- {
		dm.Regions[id] = &tailcfg.DERPRegion{RegionID: id, RegionCode: fmt.Sprintf("r%d", id)}
		p.DERPMillis[id] = id
	}
	t.Run("regions", func(t *testing.T) {
		r, err := BuildRecord(time.Time{}, "periodic", s, k, nil, dm, p, 0)
		if err != nil {
			t.Fatal(err)
		}
		if len(r.data.DERPMillis) != 8 {
			t.Fatalf("region cap: %v", r.data.DERPMillis)
		}
		for id := 1; id <= 8; id++ {
			if r.data.DERPMillis[fmt.Sprintf("r%d", id)] != id {
				t.Fatal("not first eight ascending IDs")
			}
		}
	})
	t.Run("health", func(t *testing.T) {
		for i := 0; i < 20; i++ {
			s.Health = append(s.Health, "control-unreachable", "derp-unreachable", "no-udp", "clock-skew", "key-expiry-soon", fmt.Sprint("unknown", i))
		}
		r, err := BuildRecord(time.Time{}, "change", s, k, nil, dm, p, 0)
		want := []string{"control-unreachable", "derp-unreachable", "no-udp", "clock-skew", "key-expiry-soon", "other"}
		if err != nil || !reflect.DeepEqual(r.data.Health, want) {
			t.Fatalf("health cap/vocabulary: %v (%v)", r.data.Health, err)
		}
	})
	t.Run("size", func(t *testing.T) {
		for _, name := range []string{strings.Repeat("n", 2048), strings.Repeat("<", 400), strings.Repeat("界", 700)} {
			s.Peer[k].HostName = name
			if _, err := BuildRecord(time.Time{}, "on_demand", s, k, nil, dm, p, 0); err == nil {
				t.Fatal("oversize record accepted")
			}
		}
		if _, err := json.Marshal(Record{}); err == nil {
			t.Fatal("zero record accepted")
		}
	})
}

func TestRecordProjection(t *testing.T) {
	k := key.NewNode().Public()
	ip := netip.MustParseAddr("100.64.0.1")
	at := time.Unix(1000, 0)
	s := &ipnstate.Status{BackendState: "Running", Peer: map[key.NodePublic]*ipnstate.PeerStatus{k: {HostName: "peer", TailscaleIPs: []netip.Addr{ip}, CurAddr: "192.168.0.1:80", LastHandshake: at.Add(-40 * time.Second)}}}
	for _, tc := range []struct{ endpoint, code, err, path, kind string }{
		{"10.1.2.3:80", "hk", "", "direct", "lan"}, {"172.16.1.2:80", "", "", "direct", "lan"}, {"169.254.1.2:80", "", "", "direct", "lan"},
		{"100.64.0.1:80", "", "", "direct", "cgnat"}, {"100.128.0.1:80", "", "", "direct", "public"}, {"[fd00::1]:80", "", "", "direct", "ipv6"},
		{"203.0.113.1:80", "", "", "direct", "public"}, {"invalid", "", "", "direct", ""}, {"", "hk", "", "relay", ""},
		{"203.0.113.1:80", "hk", "failed", "none", ""}, {"", "", "", "none", ""},
	} {
		t.Run(tc.endpoint+tc.code+tc.err, func(t *testing.T) {
			ping := &ipnstate.PingResult{IP: ip.String(), Endpoint: tc.endpoint, DERPRegionCode: tc.code, Err: tc.err}
			r, err := BuildRecord(at, "periodic", s, k, ping, nil, ProbeResult{}, 0)
			if err != nil {
				t.Fatal(err)
			}
			kind := ""
			if r.data.Peer.DirectKind != nil {
				kind = *r.data.Peer.DirectKind
			}
			if r.data.Peer.Path != tc.path || kind != tc.kind || r.data.Peer.HandshakeAgeS != 40 || len(r.data.DERPMillis) != 0 || !reflect.DeepEqual(r.data.Health, []string{"derp-unreachable"}) {
				t.Fatalf("projection: %+v", r.data)
			}
		})
	}
	for _, state := range []string{"NeedsLogin", "NeedsMachineAuth", "Starting", "unexpected private backend"} {
		s.BackendState = state
		r, err := BuildRecord(at, "join", s, k, nil, nil, ProbeResult{}, 0)
		if err != nil || r.data.Peer.Path != "none" || r.data.Control == "connected" || strings.Contains(r.data.Backend, "private") {
			t.Fatalf("state projection: %+v (%v)", r.data, err)
		}
	}
	s.BackendState, s.Health = "Running", []string{"Unable to connect to the Tailscale coordination server at https://secret.invalid"}
	dm := &tailcfg.DERPMap{Regions: map[int]*tailcfg.DERPRegion{1: {RegionCode: "hk"}, 2: {RegionCode: "derp.secret.invalid"}}}
	r, err := BuildRecord(at, "periodic", s, k, &ipnstate.PingResult{IP: ip.String(), DERPRegionID: 1}, dm, ProbeResult{DERPMillis: map[int]int{1: 5, 2: 6}}, 0)
	if err != nil || r.data.Control != "unreachable" || r.data.Peer.Path != "relay" || r.data.Peer.Relay != "hk" || !reflect.DeepEqual(r.data.Health, []string{"control-unreachable", "no-udp"}) || len(r.data.DERPMillis) != 1 {
		t.Fatalf("relay/control/UDP: %+v (%v)", r.data, err)
	}
	r, _ = BuildRecord(at, "join", s, k, &ipnstate.PingResult{IP: "100.64.0.2", Endpoint: "203.0.113.1:80"}, nil, ProbeResult{}, 0)
	if r.data.Peer.Path != "none" {
		t.Fatal("unrelated peer ping used")
	}
	r, _ = BuildRecord(at, "join", s, key.NodePublic{}, nil, nil, ProbeResult{}, 0)
	if r.data.Peer.Name != "" {
		t.Fatal("missing identity selected a peer")
	}
	if _, err := BuildRecord(at, "raw trigger", nil, k, nil, nil, ProbeResult{}, 0); err == nil {
		t.Fatal("open trigger vocabulary")
	}
}
