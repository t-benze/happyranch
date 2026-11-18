package sidecar

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"regexp"
	"unicode/utf8"

	"tailscale.com/ipn"
	"tailscale.com/ipn/ipnstate"
)

// HomeBinding is loaded once from protected config. Production writing is G2-E.
type HomeBinding struct {
	HomeID               string      `json:"home_id"`
	ControlPlaneID       string      `json:"control_plane_id"`
	ConnectorID          string      `json:"connector_id"`
	ActivationGeneration json.Number `json:"activation_generation"`
}

var bindingID = regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`)
var bindingGeneration = regexp.MustCompile(`^(0|[1-9][0-9]*)$`)

func (b HomeBinding) valid() bool {
	return bindingID.MatchString(b.HomeID) && bindingID.MatchString(b.ControlPlaneID) && bindingID.MatchString(b.ConnectorID) && bindingGeneration.MatchString(string(b.ActivationGeneration))
}
func (b *HomeBinding) UnmarshalJSON(data []byte) error {
	d := json.NewDecoder(bytes.NewReader(data))
	token, err := d.Token()
	if err != nil || token != json.Delim('{') {
		return ErrConfiguration
	}
	seen := map[string]bool{}
	for d.More() {
		token, err := d.Token()
		if err != nil {
			return ErrConfiguration
		}
		name, ok := token.(string)
		if !ok || seen[name] {
			return ErrConfiguration
		}
		seen[name] = true
		var dst any
		switch name {
		case "home_id":
			dst = &b.HomeID
		case "control_plane_id":
			dst = &b.ControlPlaneID
		case "connector_id":
			dst = &b.ConnectorID
		case "activation_generation":
			var raw json.RawMessage
			if d.Decode(&raw) != nil || !bindingGeneration.Match(raw) {
				return ErrConfiguration
			}
			b.ActivationGeneration = json.Number(raw)
			continue
		default:
			return ErrConfiguration
		}
		if d.Decode(dst) != nil {
			return ErrConfiguration
		}
	}
	if _, err := d.Token(); err != nil || len(seen) != 4 || !b.valid() {
		return ErrConfiguration
	}
	if d.Decode(new(any)) != io.EOF {
		return ErrConfiguration
	}
	return nil
}

type selfClient interface {
	Status(context.Context) (*ipnstate.Status, error)
	GetPrefs(context.Context) (*ipn.Prefs, error)
}
type selfObservation struct {
	Format           string      `json:"format"`
	StableID         string      `json:"stable_id"`
	TailnetAddresses []string    `json:"tailnet_addresses"`
	ControlURL       string      `json:"control_url"`
	NodeKeySHA256    string      `json:"node_key_sha256"`
	Binding          HomeBinding `json:"binding"`
}

func observeSelf(ctx context.Context, client selfClient, binding *HomeBinding) ([]byte, error) {
	if binding == nil || !binding.valid() {
		return nil, ErrEngine
	}
	status, err := client.Status(ctx)
	if err != nil || status == nil || status.BackendState != "Running" || status.Self == nil || status.Self.PublicKey.IsZero() {
		return nil, ErrEngine
	}
	stable := string(status.Self.ID)
	if stable == "" || len([]rune(stable)) > 256 || !utf8.ValidString(stable) {
		return nil, ErrEngine
	}
	for _, r := range stable {
		if r < 32 || r == 127 {
			return nil, ErrEngine
		}
	}
	prefs, err := client.GetPrefs(ctx)
	if err != nil || prefs == nil || prefs.ControlURL != homeURL || ctx.Err() != nil {
		return nil, ErrEngine
	}
	// Headscale v0.25.1 stores/returns NodePublic's full text, including nodekey:.
	digest := sha256.Sum256([]byte(status.Self.PublicKey.String()))
	addresses := []string{}
	if len(status.Self.TailscaleIPs) > 16 {
		return nil, ErrEngine
	}
	for _, ip := range status.Self.TailscaleIPs {
		if !ip.IsValid() || ip.Zone() != "" {
			return nil, ErrEngine
		}
		addresses = append(addresses, ip.String())
	}
	out, err := json.Marshal(selfObservation{"observe-self@1", stable, addresses, prefs.ControlURL, hex.EncodeToString(digest[:]), *binding})
	if err != nil || len(out) > 4096 {
		return nil, ErrEngine
	}
	return out, nil
}
