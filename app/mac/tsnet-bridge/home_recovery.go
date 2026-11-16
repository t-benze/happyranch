package main

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"
)

// Private pipe metadata, never part of the invite schema or process arguments.
type homeAddressRecovery struct {
	PreviousControlURL string `json:"previousControlURL"`
	StableNodeID       string `json:"stableNodeID"`
	Bearer             string `json:"bearer"`
}

func (homeAddressRecovery) String() string     { return "homeAddressRecovery(<redacted>)" }
func (r homeAddressRecovery) GoString() string { return r.String() }

// managedInit stores a root URL without its optional slash. Normalize only
// that already-validated representation; never repair protected profile bytes.
func canonicalHomeLaunch(cfg homeLaunch) homeLaunch {
	cfg.ControlURL = strings.TrimSuffix(cfg.ControlURL, "/")
	if cfg.Recovery != nil {
		recovery := *cfg.Recovery
		recovery.PreviousControlURL = strings.TrimSuffix(recovery.PreviousControlURL, "/")
		cfg.Recovery = &recovery
	}
	return cfg
}

// The connector's ordinary admission checks the current saved grant AND WhoIs
// pin. Network membership or a matching hostname cannot authorize this write.
func completeHomeAddressRecovery(ctx context.Context, cfg homeLaunch, node string, p *managedProfile, client *http.Client) error {
	cfg = canonicalHomeLaunch(cfg)
	r := cfg.Recovery
	if r == nil || cfg.JoinKey != "" || node == "" || node != r.StableNodeID || r.Bearer == "" ||
		p.record.Phase != "joined" || (p.record.Version == 1 && p.record.StableNodeID != node) || p.validate() != nil {
		return errManagedState
	}
	marker, err := p.read("consumed")
	if err != nil || string(marker) != p.marker() {
		return errManagedState
	}
	retry := p.record.Version == 1 && p.record.ControlURL == cfg.ControlURL &&
		p.record.PreviousControlURL == r.PreviousControlURL && p.record.StableNodeID == node
	if !retry && p.record.ControlURL != r.PreviousControlURL {
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
	if retry {
		return nil
	}
	if p.validate() != nil {
		return errManagedState
	}
	// Recheck the old marker immediately before replacing either protected file.
	marker, err = p.read("consumed")
	if err != nil || string(marker) != p.marker() {
		return errManagedState
	}
	record := p.record
	p.record.Version = 1
	p.record.PreviousControlURL = r.PreviousControlURL
	p.record.ControlURL = cfg.ControlURL
	p.record.StableNodeID = node
	data, err := json.Marshal(p.record)
	if err != nil {
		p.record = record
		return errManagedState
	}
	// A crash between these fsynced writes leaves a mismatched pair and refuses
	// reopen. It never repairs/replays a marker or grants new authorization.
	if err = p.atomic("consumed", []byte(p.marker())); err != nil {
		p.record = record
		return errManagedState
	}
	if err = p.atomic("profile.json", append(data, '\n')); err != nil {
		return errManagedState
	}
	return nil
}

// Admit only existing same-profile membership; defer protected URL writes until
// live node/grant checks. A submitting phase with a valid consumed marker is a
// previously joined profile whose last reconnect was interrupted/offline.
func initHomeProfile(cfg homeLaunch) int {
	cfg = canonicalHomeLaunch(cfg)
	if cfg.Recovery != nil && cfg.JoinKey != "" {
		return managedInvalid
	}
	control := cfg.ControlURL
	if cfg.Recovery != nil {
		control = cfg.Recovery.PreviousControlURL
	}
	code := managedInit(cfg.JoinKey, control, cfg.Hostname, cfg.StateDirectory)
	if code != 0 && cfg.Recovery != nil {
		// A previous successful native commit may precede a failed Keychain save.
		// This is accepted only with the exact protected old/new/node receipt.
		code = managedInit("", cfg.ControlURL, cfg.Hostname, cfg.StateDirectory)
		if code == 0 && (managed.profile.record.Version != 1 ||
			managed.profile.record.PreviousControlURL != cfg.Recovery.PreviousControlURL ||
			managed.profile.record.StableNodeID != cfg.Recovery.StableNodeID) {
			tsnet_close()
			code = managedStateError
		}
	}
	if code == 0 && cfg.Recovery != nil {
		p := managed.profile
		marker, err := p.read("consumed")
		if err != nil || string(marker) != p.marker() || (p.record.Phase != "joined" && p.record.Phase != "submitting") {
			tsnet_close()
			code = managedStateError
		} else {
			// Select the explicit refreshed endpoint without changing protected
			// metadata until both same-node and saved-grant checks succeed.
			managed.server.ControlURL = cfg.ControlURL
		}
	}
	return code
}
