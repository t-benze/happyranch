package main

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"testing"

	"tailscale.com/ipn"
	"tailscale.com/types/key"
	"tailscale.com/types/persist"
)

func savedStateFixture(t *testing.T) []byte {
	t.Helper()
	prefs := ipn.NewPrefs()
	prefs.ControlURL = homeControlURL
	prefs.Persist = &persist.Persist{PrivateNodeKey: key.NewNode(), NodeID: "saved-node"}
	machine, _ := key.NewMachine().MarshalText()
	profiles, _ := json.Marshal(map[ipn.ProfileID]*ipn.LoginProfile{"abcd": {ID: "abcd", Key: "profile-abcd", NodeID: "saved-node", ControlURL: homeControlURL}})
	state, _ := json.Marshal(homeStateEnvelope{Version: 1, ControlPlaneID: "synthetic-control", StableNodeID: "saved-node", Store: map[string][]byte{"_machinekey": machine, "_current-profile": []byte("profile-abcd"), "_profiles": profiles, "profile-abcd": prefs.ToBytes()}})
	return state
}

func TestHomeSessionSavedLaunch(t *testing.T) {
	good := sessionPayload(t, makeHomeCA(t, true).der)
	saved := savedStateFixture(t)
	for _, scenario := range []string{"join", "session-only", "restore", "both", "neither", "stateDirectory", "recovery", "duplicate", "alias", "null", "bad-version", "empty", "malformed", "state-version", "oversize", "control-mismatch", "node-mismatch", "incomplete", "missing-machine", "missing-node", "bad-control", "no-intent"} {
		t.Run(scenario, func(t *testing.T) {
			var fields map[string]any
			json.Unmarshal(good, &fields)
			fields["version"], fields["saveIntent"], fields["stableNodeID"] = 3, true, ""
			if scenario != "join" && scenario != "session-only" {
				delete(fields, "joinKey")
				fields["networkState"], fields["stableNodeID"] = saved, "saved-node"
			}
			switch scenario {
			case "session-only", "no-intent":
				fields["saveIntent"] = false
			case "both":
				fields["joinKey"] = "abcdef"
			case "neither":
				delete(fields, "networkState")
			case "stateDirectory", "recovery":
				fields[scenario] = "/"
			case "alias":
				fields["SaveIntent"] = true
				delete(fields, "saveIntent")
			case "null":
				fields["saveIntent"] = nil
			case "bad-version":
				fields["version"] = 4
			case "empty":
				fields["networkState"] = []byte{}
			case "malformed":
				fields["networkState"] = []byte("bad-state")
			case "state-version":
				fields["networkState"] = bytes.Replace(saved, []byte(`"version":1`), []byte(`"version":2`), 1)
			case "oversize":
				fields["networkState"] = bytes.Repeat([]byte("x"), homeSessionStateLimit+1)
			case "control-mismatch":
				fields["controlPlaneID"] = "another-control"
			case "node-mismatch":
				fields["stableNodeID"] = "another-node"
			case "incomplete", "missing-machine", "missing-node", "bad-control":
				var state homeStateEnvelope
				json.Unmarshal(saved, &state)
				switch scenario {
				case "incomplete":
					delete(state.Store, "_current-profile")
				case "missing-machine":
					delete(state.Store, "_machinekey")
				case "missing-node":
					state.Store["profile-abcd"] = []byte(`{}`)
				case "bad-control":
					state.Store["profile-abcd"] = bytes.Replace(state.Store["profile-abcd"], []byte(homeControlURL), []byte("https://untrusted.invalid"), 1)
				}
				fields["networkState"], _ = json.Marshal(state)
			}
			data, _ := json.Marshal(fields)
			if scenario == "duplicate" {
				data = append([]byte(`{"version":3,`), data[1:]...)
			}
			_, err := readHomeSessionLaunch(bytes.NewReader(sessionFrame(t, data)))
			want := scenario == "join" || scenario == "restore" || scenario == "session-only"
			if (err == nil) != want {
				t.Fatal("saved launch closed admission violated")
			}
		})
	}
}

func TestHomeSessionStateChanges(t *testing.T) {
	values, err := restoreHomeState(savedStateFixture(t), "synthetic-control", "saved-node")
	if err != nil {
		t.Fatal("fixture refused")
	}
	store := &homeSessionStore{values: values, changed: make(chan struct{}, 1)}
	s := &homeSession{state: store, control: "synthetic-control", engine: &managedEngine{selfNodeID: "saved-node"}}
	for _, value := range []string{"first", "later"} {
		if store.WriteState("auxiliary", []byte(value)) != nil {
			t.Fatal("state write refused")
		}
		select {
		case <-store.changed:
		default:
			t.Fatal("state change notification lost")
		}
		var frame bytes.Buffer
		if s.exportState(&frame) != nil {
			t.Fatal("export failed")
		}
		var size uint32
		binary.Read(&frame, binary.BigEndian, &size)
		restored, err := restoreHomeState(frame.Bytes(), "synthetic-control", "saved-node")
		if err != nil || size != uint32(frame.Len()) || string(restored["auxiliary"]) != value || len(restored) != 5 {
			t.Fatal("snapshot lost full or changed store state")
		}
	}
	if store.WriteState("too-large", make([]byte, homeSessionStateLimit)) == nil {
		t.Fatal("unbounded store write admitted")
	}
	if _, err := store.ReadState("too-large"); err != ipn.ErrStateNotExist {
		t.Fatal("failed write changed store")
	}
}
