package tsnethealth

import (
	"encoding/json"
	"errors"
	"math"
	"net/netip"
	"slices"
	"strings"
	"time"

	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tailcfg"
	"tailscale.com/types/key"
)

// Record is an immutable, closed v1 projection. Use json.Marshal to serialize it.
// The zero value is invalid; neither construction nor serialization exceeds 2 KiB.
type Record struct{ data record }

type record struct {
	V          int            `json:"v"`
	At         time.Time      `json:"at"`
	Trigger    string         `json:"trigger"`
	Backend    string         `json:"backend"`
	Control    string         `json:"control"`
	Health     []string       `json:"health"`
	UDP        bool           `json:"udp"`
	NATVaries  bool           `json:"nat_varies"`
	Portmap    string         `json:"portmap"`
	DERPHome   string         `json:"derp_home"`
	DERPMillis map[string]int `json:"derp_ms"`
	Peer       peerRecord     `json:"peer"`
}

type peerRecord struct {
	Name          string  `json:"name"`
	Online        bool    `json:"online"`
	Path          string  `json:"path"`
	Relay         string  `json:"relay"`
	RTTMillis     int64   `json:"rtt_ms"`
	HandshakeAgeS int64   `json:"handshake_age_s"`
	DirectKind    *string `json:"direct_kind"`
}

func (r Record) MarshalJSON() ([]byte, error) {
	if r.data.V != 1 {
		return nil, errors.New("invalid health record")
	}
	b, err := json.Marshal(r.data)
	if len(b) > 2048 {
		return nil, errors.New("health record exceeds 2 KiB")
	}
	return b, err
}

// BuildRecord selects exactly expected from status.Peer. ping must target one of
// that peer's TailscaleIPs. Inputs must not be mutated during this call.
// at is dated back by the cached probe age; no age/address/key enters the record.
// Unknown handshake age is zero. Oversize names/records are refused, not truncated.
func BuildRecord(at time.Time, trigger string, status *ipnstate.Status, expected key.NodePublic, ping *ipnstate.PingResult, dm *tailcfg.DERPMap, probe ProbeResult, age time.Duration) (Record, error) {
	if !slices.Contains([]string{"join", "periodic", "change", "on_demand"}, trigger) {
		return Record{}, errors.New("invalid health trigger")
	}
	r := record{V: 1, At: at.Add(-max(0, age)).UTC(), Trigger: trigger, Backend: "NoState", Control: "unreachable", Portmap: "unknown", Health: []string{}, DERPMillis: map[string]int{}, Peer: peerRecord{Path: "none"}}
	if dm != nil {
		for _, id := range dm.RegionIDs() {
			ms, ok := probe.DERPMillis[id]
			region := dm.Regions[id]
			if !ok || ms < 0 || region == nil || !validRegionCode(region.RegionCode) {
				continue
			}
			if _, duplicate := r.DERPMillis[region.RegionCode]; !duplicate {
				r.DERPMillis[region.RegionCode] = ms
			}
			if len(r.DERPMillis) == 8 {
				break
			}
		}
	}
	warnings := map[string]bool{}
	if len(r.DERPMillis) == 0 {
		warnings["derp-unreachable"] = true
	} else {
		r.UDP, r.NATVaries = probe.UDP, probe.NATVaries
		if !r.UDP {
			warnings["no-udp"] = true
		}
	}
	if status != nil {
		if slices.Contains([]string{"NoState", "NeedsLogin", "NeedsMachineAuth", "Stopped", "Starting", "Running"}, status.BackendState) {
			r.Backend = status.BackendState
		}
		for _, text := range status.Health {
			warnings[healthCode(text)] = true
		}
		if status.Self != nil && validRegionCode(status.Self.Relay) {
			r.DERPHome = status.Self.Relay
		}
		if peer := status.Peer[expected]; !expected.IsZero() && peer != nil {
			r.Peer.Name, r.Peer.Online = peer.HostName, peer.Online
			if validRegionCode(peer.Relay) {
				r.Peer.Relay = peer.Relay
			}
			if !peer.LastHandshake.IsZero() {
				r.Peer.HandshakeAgeS = int64(max(0, at.Sub(peer.LastHandshake)) / time.Second)
			}
			if ping != nil && ping.Err == "" && parseIP(ping.IP).IsValid() && slices.Contains(peer.TailscaleIPs, parseIP(ping.IP)) {
				switch {
				case ping.Endpoint != "":
					r.Peer.Path, r.Peer.DirectKind = "direct", directKind(ping.Endpoint)
				case ping.DERPRegionID != 0 || ping.DERPRegionCode != "":
					r.Peer.Path = "relay"
					if validRegionCode(ping.DERPRegionCode) {
						r.Peer.Relay = ping.DERPRegionCode
					} else if dm != nil && dm.Regions[ping.DERPRegionID] != nil && validRegionCode(dm.Regions[ping.DERPRegionID].RegionCode) {
						r.Peer.Relay = dm.Regions[ping.DERPRegionID].RegionCode
					}
				}
				if r.Peer.Path != "none" && ping.LatencySeconds >= 0 && ping.LatencySeconds < float64(math.MaxInt64)/1000 {
					r.Peer.RTTMillis = int64(ping.LatencySeconds * 1000)
				}
			}
		}
	}
	switch {
	case r.Backend == "NeedsLogin" || r.Backend == "NeedsMachineAuth":
		r.Control = "needs_login"
	case r.Backend == "Running" && !warnings["control-unreachable"]:
		r.Control = "connected"
	default:
		warnings["control-unreachable"] = true
	}
	for _, code := range []string{"control-unreachable", "derp-unreachable", "no-udp", "clock-skew", "key-expiry-soon", "other"} {
		if warnings[code] {
			r.Health = append(r.Health, code)
		}
	}
	result := Record{r}
	_, err := result.MarshalJSON()
	if err != nil {
		return Record{}, err
	}
	return result, nil
}

func validRegionCode(s string) bool {
	if len(s) == 0 || len(s) > 16 || s[0] < 'a' || s[0] > 'z' {
		return false
	}
	for _, c := range s {
		if (c < 'a' || c > 'z') && (c < '0' || c > '9') && c != '-' {
			return false
		}
	}
	return true
}

func parseIP(s string) netip.Addr { a, _ := netip.ParseAddr(s); return a.Unmap() }

func directKind(endpoint string) *string {
	ap, err := netip.ParseAddrPort(endpoint)
	if err != nil {
		return nil
	}
	a, kind := ap.Addr().Unmap(), "public"
	switch {
	case a.Is6():
		kind = "ipv6"
	case netip.MustParsePrefix("100.64.0.0/10").Contains(a):
		kind = "cgnat"
	case a.IsPrivate() || a.IsLinkLocalUnicast():
		kind = "lan"
	}
	return &kind
}

// Status v1.78.0 exposes rendered strings, not structured Warnable codes.
// Matching only produces this fixed vocabulary; the source text is discarded.
func healthCode(text string) string {
	s := strings.ToLower(text)
	switch {
	case strings.HasPrefix(s, "unable to connect to the tailscale coordination server"), strings.HasPrefix(s, "tailscale hasn't received a network map from the coordination server"), s == "control-unreachable":
		return "control-unreachable"
	case strings.HasPrefix(s, "tailscale could not connect to") && strings.Contains(s, "relay server"), strings.HasPrefix(s, "tailscale hasn't heard from the home relay server"), strings.HasPrefix(s, "the relay server #"), s == "derp-unreachable":
		return "derp-unreachable"
	case strings.HasPrefix(s, "tailscale couldn't listen for incoming udp connections"), s == "no-udp":
		return "no-udp"
	case strings.HasPrefix(s, "clock skew"), s == "clock-skew":
		return "clock-skew"
	case strings.HasPrefix(s, "key expires"), s == "key-expiry-soon":
		return "key-expiry-soon"
	default:
		return "other"
	}
}
