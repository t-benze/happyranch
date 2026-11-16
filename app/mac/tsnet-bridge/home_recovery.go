package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/netip"
	"strings"
)

var errHomeReapproval = errors.New("home_reapproval_required")

// Private pipe metadata, never part of the invite schema or process arguments.
type homeAddressRecovery struct {
	PreviousDialIP string `json:"previousDialIP"`
	StableNodeID   string `json:"stableNodeID"`
	Bearer         string `json:"bearer"`
}

func (homeAddressRecovery) String() string     { return "homeAddressRecovery(<redacted>)" }
func (r homeAddressRecovery) GoString() string { return r.String() }

// Preserve F2: only validated launch representations normalize; protected bytes
// are never repaired and the optional slash cannot change the profile identity.
func canonicalHomeLaunch(cfg homeLaunch) homeLaunch {
	cfg.ControlURL = strings.TrimSuffix(cfg.ControlURL, "/")
	return cfg
}
func validHomeDialIP(s string) bool {
	ip, err := netip.ParseAddr(s)
	return err == nil && ip.Is4() && ip.IsPrivate() && ip.String() == s
}
func matchesHomeProfile(r profileRecord, cfg homeLaunch) bool {
	cfg = canonicalHomeLaunch(cfg)
	if r.Version != 2 || r.ControlURL != homeControlURL || cfg.ControlURL != homeControlURL ||
		r.HomeID != cfg.HomeID || r.ControlPlaneID != cfg.ControlPlaneID || r.CAFingerprint != cfg.Fingerprint ||
		r.PreviousControlURL != "" || !validHomeDialIP(r.DialIP) || r.PreviousDialIP != "" && !validHomeDialIP(r.PreviousDialIP) {
		return false
	}
	if cfg.Recovery == nil {
		return r.DialIP == cfg.DialIP
	}
	old := cfg.Recovery.PreviousDialIP
	return r.StableNodeID == cfg.Recovery.StableNodeID &&
		(r.DialIP == old || r.DialIP == cfg.DialIP && r.PreviousDialIP == old)
}

func sameHomeRecord(p *managedProfile) bool {
	raw, err := p.read("profile.json")
	if err != nil {
		return false
	}
	expected, err := json.Marshal(p.record)
	return err == nil && bytes.Equal(raw, append(expected, '\n'))
}

// The ordinary connector route validates the saved grant and WhoIs pin. Only
// after that live proof may one atomic, fsynced profile replacement change dial
// metadata. Canonical identity, consumed marker and tailscaled.state stay exact.
func completeHomeAddressRecovery(ctx context.Context, cfg homeLaunch, node string, p *managedProfile, client *http.Client) error {
	cfg = canonicalHomeLaunch(cfg)
	r := cfg.Recovery
	if p.record.Version != 2 {
		return errHomeReapproval
	}
	if _, err := validateHomeLaunch(cfg); err != nil {
		return errManagedState
	}
	if r == nil || cfg.JoinKey != "" || node == "" || node != r.StableNodeID ||
		p.record.StableNodeID != node || !matchesHomeProfile(p.record, cfg) ||
		p.record.Phase != "joined" || p.validate() != nil || !sameHomeRecord(p) {
		return errManagedState
	}
	marker, err := p.read("consumed")
	if err != nil || string(marker) != p.marker() {
		return errManagedState
	}
	req, err := http.NewRequestWithContext(ctx, "GET", "http://"+cfg.Target+"/api/v1/health", nil)
	if err != nil {
		return errManagedState
	}
	req.Header.Set("X-HappyRanch-Device-Credential", r.Bearer)
	response, err := client.Do(req)
	if err != nil {
		return errManagedState
	}
	response.Body.Close() // Never log or persist response/error bodies.
	if response.StatusCode != http.StatusOK || ctx.Err() != nil {
		return errManagedState
	}
	if p.validate() != nil || !sameHomeRecord(p) {
		return errManagedState
	}
	marker, err = p.read("consumed")
	if err != nil || string(marker) != p.marker() {
		return errManagedState
	}
	if p.record.DialIP == cfg.DialIP {
		return nil
	} // exact committed receipt; grant rechecked
	record := p.record
	record.DialIP, record.PreviousDialIP = cfg.DialIP, r.PreviousDialIP
	data, err := json.Marshal(record)
	if err != nil {
		return errManagedState
	}
	if p.atomic("profile.json", append(data, '\n')) != nil {
		return errManagedState
	}
	p.record = record
	return nil
}

// Existing profiles must already be v2 with the exact protected home/CA tuple.
// A v1 URL or marker is never converted, even with a valid saved grant.
func initHomeProfile(cfg homeLaunch) int {
	if _, err := validateHomeLaunch(cfg); err != nil {
		return homeTrustRefused
	}
	cfg = canonicalHomeLaunch(cfg)
	code := initManagedProfile(cfg.JoinKey, cfg.ControlURL, cfg.Hostname, cfg.StateDirectory, &cfg)
	if code == 0 && cfg.Recovery != nil {
		p := managed.profile
		marker, err := p.read("consumed")
		if err != nil || string(marker) != p.marker() || (p.record.Phase != "joined" && p.record.Phase != "submitting") {
			tsnet_close()
			return managedStateError
		}
	}
	return code
}
