package main

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"strings"
	"sync"
	"unicode/utf8"

	"tailscale.com/ipn"
	"tailscale.com/types/key"
)

const homeSessionStateLimit = 64 * 1024
const homeSessionLaunchLimit = 128 * 1024
const homeSessionIdentityMismatch = -22

var errHomeSessionIdentity = errors.New("home_session_identity_mismatch")

type homeSessionLaunch struct {
	homeLaunch
	SaveIntent   bool
	StableNodeID string
	NetworkState []byte
}

// Version 3 is a closed additive session-only variant. Mac's v2 stays closed.
func sessionObject(data []byte) (map[string]json.RawMessage, error) {
	if !utf8.Valid(data) {
		return nil, errHomeTrust
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	token, err := dec.Token()
	if err != nil || token != json.Delim('{') {
		return nil, errHomeTrust
	}
	fields := make(map[string]json.RawMessage)
	for dec.More() {
		token, err := dec.Token()
		name, ok := token.(string)
		if err != nil || !ok || fields[name] != nil {
			return nil, errHomeTrust
		}
		var raw json.RawMessage
		if dec.Decode(&raw) != nil || bytes.Equal(raw, []byte("null")) {
			return nil, errHomeTrust
		}
		fields[name] = raw
	}
	if token, err := dec.Token(); err != nil || token != json.Delim('}') || dec.Decode(new(any)) != io.EOF {
		return nil, errHomeTrust
	}
	return fields, nil
}

func readSavedSessionLaunch(data []byte, fields map[string]json.RawMessage) (homeSessionLaunch, error) {
	var wire struct {
		Version        int    `json:"version"`
		CA             []byte `json:"caDER"`
		DialIP         string `json:"dialIP"`
		HomeID         string `json:"homeID"`
		ControlPlaneID string `json:"controlPlaneID"`
		Fingerprint    string `json:"fingerprint"`
		ControlURL     string `json:"controlURL"`
		Hostname       string `json:"hostname"`
		JoinKey        string `json:"joinKey"`
		SocketPath     string `json:"socketPath"`
		Target         string `json:"target"`
		SaveIntent     bool   `json:"saveIntent"`
		StableNodeID   string `json:"stableNodeID"`
		NetworkState   []byte `json:"networkState"`
	}
	_, join := fields["joinKey"]
	_, restore := fields["networkState"]
	if join == restore || len(fields) != 13 {
		return homeSessionLaunch{}, errHomeTrust
	}
	for _, name := range []string{"version", "caDER", "dialIP", "homeID", "controlPlaneID", "fingerprint", "controlURL", "hostname", "socketPath", "target", "saveIntent", "stableNodeID"} {
		if fields[name] == nil {
			return homeSessionLaunch{}, errHomeTrust
		}
	}
	dec := json.NewDecoder(bytes.NewReader(data))
	dec.DisallowUnknownFields()
	if dec.Decode(&wire) != nil || wire.Version != 3 {
		return homeSessionLaunch{}, errHomeTrust
	}
	cfg := homeSessionLaunch{homeLaunch: homeLaunch{Version: 2, CA: wire.CA, DialIP: wire.DialIP, HomeID: wire.HomeID, ControlPlaneID: wire.ControlPlaneID, Fingerprint: wire.Fingerprint, ControlURL: wire.ControlURL, Hostname: wire.Hostname, StateDirectory: "/", JoinKey: wire.JoinKey, SocketPath: wire.SocketPath, Target: wire.Target}, SaveIntent: wire.SaveIntent, StableNodeID: wire.StableNodeID, NetworkState: wire.NetworkState}
	if _, err := validateHomeLaunch(cfg.homeLaunch); err != nil {
		return homeSessionLaunch{}, errHomeTrust
	}
	if join {
		if cfg.JoinKey == "" || cfg.StableNodeID != "" {
			return homeSessionLaunch{}, errHomeTrust
		}
	} else {
		if !cfg.SaveIntent {
			return homeSessionLaunch{}, errHomeTrust
		}
		if _, err := restoreHomeState(cfg.NetworkState, cfg.ControlPlaneID, cfg.StableNodeID); err != nil {
			return homeSessionLaunch{}, err
		}
	}
	return cfg, nil
}

type homeStateEnvelope struct {
	Version        int               `json:"version"`
	ControlPlaneID string            `json:"controlPlaneID"`
	StableNodeID   string            `json:"stableNodeID"`
	Store          map[string][]byte `json:"store"`
}

func restoreHomeState(data []byte, control, node string) (map[string][]byte, error) {
	if len(data) == 0 || len(data) > homeSessionStateLimit {
		return nil, errHomeTrust
	}
	fields, err := sessionObject(data)
	if len(data) > homeSessionStateLimit || err != nil || len(fields) != 4 {
		return nil, errHomeTrust
	}
	for _, name := range []string{"version", "controlPlaneID", "stableNodeID", "store"} {
		if fields[name] == nil {
			return nil, errHomeTrust
		}
	}
	var state homeStateEnvelope
	if json.Unmarshal(data, &state) != nil || state.Version != 1 || node == "" || len(node) > 256 || strings.IndexFunc(node, func(c rune) bool { return c <= 32 || c >= 127 }) >= 0 {
		return nil, errHomeTrust
	}
	if state.ControlPlaneID != control || state.StableNodeID != node {
		return nil, errHomeSessionIdentity
	}
	// Retain ALL store entries, including future tsnet auxiliary state. Require a
	// complete active profile and machine key so a partial blob cannot re-enroll.
	storeFields, err := sessionObject(fields["store"])
	if err != nil || len(storeFields) > 32 {
		return nil, errHomeTrust
	}
	var machine key.MachinePrivate
	if machine.UnmarshalText(state.Store[string(ipn.MachineKeyStateKey)]) != nil || machine.IsZero() {
		return nil, errHomeTrust
	}
	active := string(state.Store[string(ipn.CurrentProfileStateKey)])
	var profiles map[ipn.ProfileID]*ipn.LoginProfile
	if json.Unmarshal(state.Store[string(ipn.KnownProfilesStateKey)], &profiles) != nil {
		return nil, errHomeTrust
	}
	var prefs ipn.Prefs
	if active == "" || json.Unmarshal(state.Store[active], &prefs) != nil || prefs.Persist == nil || prefs.Persist.PrivateNodeKey.IsZero() || string(prefs.Persist.NodeID) != node || (prefs.ControlURL != homeControlURL && prefs.ControlURL != homeControlURL+"/") {
		return nil, errHomeTrust
	}
	found := false
	for _, profile := range profiles {
		if profile != nil && string(profile.Key) == active && string(profile.NodeID) == node && profile.ControlURL == prefs.ControlURL {
			found = true
		}
	}
	if !found {
		return nil, errHomeTrust
	}
	return state.Store, nil
}

// A bounded ipn.StateStore for explicitly save-intending sessions. Upstream's
// literal mem.Store is ephemeral-only; this owner exports credentials to the
// private parent instead of persisting a FileStore. No path is ever accepted.
type homeSessionStore struct {
	mu      sync.Mutex
	values  map[string][]byte
	changed chan struct{}
}

func (s *homeSessionStore) String() string { return "homeSessionStore(<redacted>)" }
func (s *homeSessionStore) ReadState(id ipn.StateKey) ([]byte, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	value, ok := s.values[string(id)]
	if !ok {
		return nil, ipn.ErrStateNotExist
	}
	return bytes.Clone(value), nil
}
func (s *homeSessionStore) WriteState(id ipn.StateKey, value []byte) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	// Check before retaining a write; errors never include keys or values.
	if len(id) > 256 || len(value) > homeSessionStateLimit/2 {
		return errHomeTrust
	}
	next := make(map[string][]byte, len(s.values)+1)
	for k, v := range s.values {
		next[k] = v
	}
	if value == nil {
		delete(next, string(id))
	} else {
		next[string(id)] = bytes.Clone(value)
	}
	raw, err := json.Marshal(next)
	if err != nil || len(next) > 32 || len(raw) > homeSessionStateLimit-1024 {
		return errHomeTrust
	}
	defer clear(raw)
	if bytes.Equal(s.values[string(id)], value) {
		return nil
	}
	s.values = next
	select {
	case s.changed <- struct{}{}:
	default:
	}
	return nil
}
func (s *homeSession) exportState(out io.Writer) error {
	s.state.mu.Lock()
	data, err := json.Marshal(homeStateEnvelope{Version: 1, ControlPlaneID: s.control, StableNodeID: s.engine.selfNodeID, Store: s.state.values})
	s.state.mu.Unlock()
	defer clear(data)
	if err != nil || len(data) > homeSessionStateLimit {
		return errHomeTrust
	}
	if _, err := restoreHomeState(data, s.control, s.engine.selfNodeID); err != nil {
		return errHomeTrust
	}
	if binary.Write(out, binary.BigEndian, uint32(len(data))) != nil {
		return errHomeTrust
	}
	if n, err := out.Write(data); err != nil || n != len(data) {
		return errHomeTrust
	}
	return nil
}
