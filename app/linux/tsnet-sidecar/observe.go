package sidecar

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"unicode/utf8"

	"tailscale.com/ipn"
	"tailscale.com/ipn/ipnstate"
)

const observeSocket = "/run/happyranch-tsnet-sidecar/observe.sock"
const observeRequest = "observe-self@1\n"

// Every occurrence is admitted, so a duplicate cannot hide an invalid binding.
type bindingConfig json.RawMessage

func (b *bindingConfig) UnmarshalJSON(data []byte) error {
	if len(*b) != 0 {
		return ErrConfiguration
	}
	if _, err := parseHomeBinding(data); err != nil {
		return err
	}
	*b = append((*b)[:0], data...)
	return nil
}
func (b bindingConfig) MarshalJSON() ([]byte, error) { return b, nil }

type homeBinding struct {
	HomeID         string      `json:"home_id"`
	ControlPlaneID string      `json:"control_plane_id"`
	ConnectorID    string      `json:"connector_id"`
	Generation     json.Number `json:"activation_generation"`
}

func parseHomeBinding(data []byte) (*homeBinding, error) {
	if len(data) == 0 {
		return nil, nil
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	token, err := decoder.Token()
	if err != nil || token != json.Delim('{') {
		return nil, ErrConfiguration
	}
	fields := map[string]json.RawMessage{}
	for decoder.More() {
		key, err := decoder.Token()
		name, ok := key.(string)
		if err != nil || !ok || fields[name] != nil {
			return nil, ErrConfiguration
		}
		var value json.RawMessage
		if decoder.Decode(&value) != nil {
			return nil, ErrConfiguration
		}
		fields[name] = value
	}
	if _, err = decoder.Token(); err != nil || len(fields) != 4 {
		return nil, ErrConfiguration
	}
	if decoder.Decode(new(any)) != io.EOF {
		return nil, ErrConfiguration
	}
	for _, name := range []string{"home_id", "control_plane_id", "connector_id", "activation_generation"} {
		if fields[name] == nil {
			return nil, ErrConfiguration
		}
	}
	var binding homeBinding
	strict := json.NewDecoder(bytes.NewReader(data))
	strict.DisallowUnknownFields()
	if strict.Decode(&binding) != nil {
		return nil, ErrConfiguration
	}
	for _, value := range []string{binding.HomeID, binding.ControlPlaneID, binding.ConnectorID} {
		if !regexp.MustCompile(`^[A-Za-z0-9_-]{1,128}$`).MatchString(value) {
			return nil, ErrConfiguration
		}
	}
	// json.Number retains B1's unbounded nonnegative integer grammar exactly.
	if !regexp.MustCompile(`^(0|[1-9][0-9]*)$`).Match(fields["activation_generation"]) {
		return nil, ErrConfiguration
	}
	return &binding, nil
}

type selfClient interface {
	Status(context.Context) (*ipnstate.Status, error)
	GetPrefs(context.Context) (*ipn.Prefs, error)
}
type selfObservation struct {
	Format        string       `json:"format"`
	StableID      string       `json:"stable_id"`
	Addresses     []string     `json:"tailnet_addresses"`
	ControlURL    string       `json:"control_url"`
	NodeKeySHA256 string       `json:"node_key_sha256"`
	Binding       *homeBinding `json:"binding"`
}

func (e *TSNetEngine) observeSelf(ctx context.Context) (selfObservation, error) {
	e.admissionMu.Lock()
	closed := e.closed
	e.admissionMu.Unlock()
	if closed || e.selfClient == nil {
		return selfObservation{}, ErrEngine
	}
	status, err := e.selfClient.Status(ctx)
	if err != nil || status == nil || status.BackendState != "Running" || status.Self == nil || status.Self.PublicKey.IsZero() {
		return selfObservation{}, ErrEngine
	}
	prefs, err := e.selfClient.GetPrefs(ctx)
	if err != nil || prefs == nil || prefs.ControlURL != homeURL {
		return selfObservation{}, ErrEngine
	}
	self := status.Self
	value := selfObservation{Format: "observe-self@1", StableID: string(self.ID), ControlURL: prefs.ControlURL, Addresses: []string{}}
	for _, addr := range self.TailscaleIPs {
		value.Addresses = append(value.Addresses, addr.String())
	}
	value.NodeKeySHA256 = fmt.Sprintf("%x", sha256.Sum256([]byte(self.PublicKey.String())))
	e.admissionMu.Lock()
	defer e.admissionMu.Unlock()
	if e.closed || ctx.Err() != nil {
		return selfObservation{}, ErrEngine
	}
	if e.home != nil {
		select {
		case <-e.home.done:
			return selfObservation{}, ErrEngine
		default:
		}
	}
	return value, nil
}

func observationBytes(value selfObservation) ([]byte, error) {
	if value.Format != "observe-self@1" || value.Binding == nil || value.ControlURL != homeURL ||
		value.StableID == "" || len([]rune(value.StableID)) > 256 || !utf8.ValidString(value.StableID) {
		return nil, ErrEngine
	}
	for _, ch := range value.StableID {
		if ch < 32 || ch == 127 {
			return nil, ErrEngine
		}
	}
	data, err := json.Marshal(value)
	if err != nil || len(data) > 4096 {
		return nil, ErrEngine
	}
	return data, nil
}
