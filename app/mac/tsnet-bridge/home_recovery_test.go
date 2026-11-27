package main

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

const recoveryOldIP = "192.168.1.40"
const recoveryNewIP = "192.168.1.41"
const recoveryCanary = "SYNTHETIC-RECOVERY-BEARER-CANARY"

func recoveryFixture(t *testing.T, status int) (*managedProfile, homeLaunch, *http.Client, *atomic.Int32) {
	t.Helper()
	cfg := homeLaunchFixture(makeHomeCA(t, true).der)
	cfg.Hostname, cfg.StateDirectory = testHostname, profilePath(t)
	p, err := openBoundManagedProfile(cfg.StateDirectory, cfg.Hostname, homeControlURL, true, &cfg)
	if err != nil {
		t.Fatal("profile creation failed")
	}
	t.Cleanup(func() { p.close() })
	p.record.StableNodeID = "stable-synthetic-node"
	if os.WriteFile(filepath.Join(p.path, "tailscaled.state"), []byte("synthetic-persisted-node"), 0600) != nil || p.commitConsumed() != nil {
		t.Fatal("saved membership setup failed")
	}
	cfg.DialIP = recoveryNewIP
	cfg.Recovery = &homeAddressRecovery{PreviousDialIP: recoveryOldIP, StableNodeID: p.record.StableNodeID, Bearer: recoveryCanary}
	requests := new(atomic.Int32)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		if r.Method != "GET" || r.URL.Path != "/api/v1/health" || r.Host != cfg.Target || r.Header.Get("X-HappyRanch-Device-Credential") != recoveryCanary || r.Header.Get("Authorization") != "" {
			t.Error("saved-grant request escaped fixed route, target or authorization")
		}
		if status == 302 {
			w.Header().Set("Location", "/forbidden-redirect")
		}
		w.WriteHeader(status)
	}))
	t.Cleanup(server.Close)
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, "tcp", server.Listener.Addr().String())
	}}
	t.Cleanup(transport.CloseIdleConnections)
	client := &http.Client{Transport: transport, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	return p, cfg, client, requests
}
func reopenHome(t *testing.T, cfg homeLaunch) (*managedProfile, error) {
	t.Helper()
	return openBoundManagedProfile(cfg.StateDirectory, cfg.Hostname, homeControlURL, false, &cfg)
}
func recoveryBytes(p *managedProfile) ([]byte, []byte) {
	a, _ := p.read("profile.json")
	b, _ := p.read("consumed")
	return a, b
}

func TestHomeAddressRecoveryCommitAndReopen(t *testing.T) {
	p, cfg, client, requests := recoveryFixture(t, 200)
	original := p.record
	_, oldMarker := recoveryBytes(p)
	state, _ := p.read("tailscaled.state")
	if completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, p, client) != nil {
		t.Fatal("authorized recovery refused")
	}
	expected := original
	expected.DialIP, expected.PreviousDialIP = recoveryNewIP, recoveryOldIP
	if requests.Load() != 1 || p.record != expected {
		t.Fatal("recovery changed identity instead of dial metadata only")
	}
	_, marker := recoveryBytes(p)
	afterState, _ := p.read("tailscaled.state")
	if !bytes.Equal(marker, oldMarker) || !strings.HasPrefix(string(marker), "v2:") || !bytes.Equal(state, afterState) {
		t.Fatal("recovery replaced consumed authority or node state")
	}
	p.close()
	resumed := cfg
	resumed.Recovery = nil
	reopened, err := reopenHome(t, resumed)
	if err != nil {
		t.Fatal("new dial metadata cannot reopen")
	}
	reopened.close()
	old := resumed
	old.DialIP = recoveryOldIP
	if replay, err := reopenHome(t, old); err == nil {
		replay.close()
		t.Fatal("stale dial metadata replay accepted")
	}
	if fresh, err := openBoundManagedProfile(cfg.StateDirectory, cfg.Hostname, homeControlURL, true, &cfg); err == nil {
		fresh.close()
		t.Fatal("new key consumed in existing profile")
	}
	if os.WriteFile(filepath.Join(cfg.StateDirectory, "consumed"), []byte("v1:obsolete-marker\n"), 0600) != nil {
		t.Fatal("marker fixture failed")
	}
	if replay, err := reopenHome(t, resumed); err == nil {
		replay.close()
		t.Fatal("v1 consumed marker silently repaired")
	}
}
func TestHomeAddressRecoveryRefusals(t *testing.T) {
	for _, scenario := range []string{"different-node", "missing-grant", "denied-grant", "redirect", "missing-marker", "replayed-marker", "wrong-old-ip", "different-home", "different-control", "different-ca", "stale-pending", "protected-url-slash", "legacy-control-port"} {
		t.Run(scenario, func(t *testing.T) {
			status := 200
			if scenario == "denied-grant" {
				status = 403
			}
			if scenario == "redirect" {
				status = 302
			}
			p, cfg, client, requests := recoveryFixture(t, status)
			node := cfg.Recovery.StableNodeID
			switch scenario {
			case "different-node":
				node = "different-synthetic-node"
			case "missing-grant":
				cfg.Recovery.Bearer = ""
			case "missing-marker":
				os.Remove(filepath.Join(p.path, "consumed"))
			case "replayed-marker":
				os.WriteFile(filepath.Join(p.path, "consumed"), []byte("stale-consumed-marker\n"), 0600)
			case "wrong-old-ip":
				cfg.Recovery.PreviousDialIP = "192.168.1.99"
			case "different-home":
				cfg.HomeID = "different-home"
			case "different-control":
				cfg.ControlPlaneID = "different-control"
			case "different-ca":
				cfg.CA = makeHomeCA(t, true).der
				cfg.Fingerprint = homeFingerprint(cfg.CA)
			case "stale-pending":
				os.WriteFile(filepath.Join(p.path, "profile.json.pending"), []byte("unknown-writer"), 0600)
			case "legacy-control-port", "protected-url-slash":
				if scenario == "legacy-control-port" {
					p.record.ControlURL = "https://control.happyranch.invalid:8443"
				} else {
					p.record.ControlURL += "/"
				}
				if p.phase("joined") != nil {
					t.Fatal("fixture failed")
				}
			}
			beforeProfile, beforeMarker := recoveryBytes(p)
			if completeHomeAddressRecovery(context.Background(), cfg, node, p, client) == nil {
				t.Error("unauthorized metadata recovery accepted")
			}
			afterProfile, afterMarker := recoveryBytes(p)
			if !bytes.Equal(beforeProfile, afterProfile) || !bytes.Equal(beforeMarker, afterMarker) {
				t.Error("refusal rewrote protected binding or marker")
			}
			if scenario == "denied-grant" || scenario == "redirect" || scenario == "stale-pending" {
				if requests.Load() != 1 {
					t.Error("fixture did not reach saved-grant admission")
				}
			} else if requests.Load() != 0 {
				t.Error("local refusal contacted connector")
			}
		})
	}
}
func TestHomeAddressRecoveryRetryReceipt(t *testing.T) {
	p, cfg, client, requests := recoveryFixture(t, 200)
	if completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, p, client) != nil {
		t.Fatal("initial recovery failed")
	}
	profile, marker := recoveryBytes(p)
	if completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, p, client) != nil || requests.Load() != 2 {
		t.Fatal("exact retry failed to revalidate saved grant")
	}
	afterProfile, afterMarker := recoveryBytes(p)
	if !bytes.Equal(profile, afterProfile) || !bytes.Equal(marker, afterMarker) {
		t.Fatal("exact retry rewrote native receipt")
	}
	cfg.Recovery.PreviousDialIP = "192.168.1.99"
	if completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, p, client) == nil {
		t.Fatal("foreign retry receipt accepted")
	}
}
func TestHomeAddressRecoveryLaunchRefusals(t *testing.T) {
	cfg := homeLaunchFixture(makeHomeCA(t, true).der)
	cfg.DialIP = recoveryNewIP
	cfg.Recovery = &homeAddressRecovery{PreviousDialIP: recoveryOldIP, StableNodeID: "synthetic-node", Bearer: recoveryCanary}
	if _, err := validateHomeLaunch(cfg); err != nil {
		t.Fatal("valid recovery launch refused")
	}
	for _, scenario := range []string{"new-key", "public-old-ip", "missing-grant", "header-injection", "missing-node", "same-address", "ip-control-url"} {
		t.Run(scenario, func(t *testing.T) {
			changed := cfg
			recovery := *cfg.Recovery
			changed.Recovery = &recovery
			switch scenario {
			case "new-key":
				changed.JoinKey = strings.Repeat("ab", 24)
			case "public-old-ip":
				recovery.PreviousDialIP = "8.8.8.8"
			case "missing-grant":
				recovery.Bearer = ""
			case "header-injection":
				recovery.Bearer += "\r\nInjected: value"
			case "missing-node":
				recovery.StableNodeID = ""
			case "same-address":
				changed.DialIP = recoveryOldIP
			case "ip-control-url":
				changed.ControlURL = "https://192.168.1.41:8443/"
			}
			if _, err := validateHomeLaunch(changed); err == nil {
				t.Error("unsafe recovery launch accepted")
			}
		})
	}
}
func TestHomeAddressRecoveryRedaction(t *testing.T) {
	recovery := homeAddressRecovery{Bearer: recoveryCanary}
	if strings.Contains(fmt.Sprintf("%v %#v %v", recovery, recovery, errManagedState), recoveryCanary) {
		t.Fatal("recovery diagnostic leaked material")
	}
}
func TestHomeAddressRecoveryInterruptedCommitStaysClosed(t *testing.T) {
	p, cfg, client, _ := recoveryFixture(t, 200)
	oldProfile, oldMarker := recoveryBytes(p)
	if os.WriteFile(filepath.Join(p.path, "profile.json.pending"), []byte("unknown-writer"), 0600) != nil {
		t.Fatal("pending fixture failed")
	}
	if completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, p, client) == nil {
		t.Fatal("failed atomic commit reported success")
	}
	profile, marker := recoveryBytes(p)
	if !bytes.Equal(profile, oldProfile) || !bytes.Equal(marker, oldMarker) {
		t.Fatal("failed single-file transaction changed authority")
	}
	p.close()
	cfg.Recovery = nil
	if reopened, err := reopenHome(t, cfg); err == nil {
		reopened.close()
		t.Fatal("uncommitted address admitted after interruption")
	}
	cfg.DialIP = recoveryOldIP
	old, err := reopenHome(t, cfg)
	if err != nil {
		t.Fatal("interruption damaged old committed address")
	}
	old.close()
}
func TestHomeAddressRecoveryProfileAdmission(t *testing.T) {
	cleanEngine(t)
	p, cfg, client, _ := recoveryFixture(t, 200)
	if p.phase("submitting") != nil {
		t.Fatal("offline reconnect fixture failed")
	}
	before, _ := p.read("profile.json")
	p.close()
	if code := initHomeProfile(cfg); code != 0 {
		t.Fatalf("consumed offline profile refused: %d", code)
	}
	after, _ := managed.profile.read("profile.json")
	if managed.server.ControlURL != homeControlURL || !bytes.Equal(before, after) || managed.profile.record.DialIP != recoveryOldIP {
		t.Fatal("admission changed protected metadata before live validation")
	}
	if managed.profile.phase("joined") != nil {
		t.Fatal("fixture failed")
	}
	if completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, managed.profile, client) != nil {
		t.Fatal("recovery fixture failed")
	}
	tsnet_close()
	if code := initHomeProfile(cfg); code != 0 {
		t.Fatalf("native receipt could not recover Keychain save failure: %d", code)
	}
	tsnet_close()
	cfg.Recovery.StableNodeID = "different-node"
	if code := initHomeProfile(cfg); code == 0 {
		tsnet_close()
		t.Fatal("native retry accepted different-node receipt")
	}
}
func TestHomeAddressRecoveryRootSlashCompatibility(t *testing.T) {
	for _, savedSlash := range []string{"", "/"} {
		for _, newSlash := range []string{"", "/"} {
			t.Run(fmt.Sprintf("saved-slash=%t/refreshed-slash=%t", savedSlash != "", newSlash != ""), func(t *testing.T) {
				cleanEngine(t)
				p, cfg, client, _ := recoveryFixture(t, 200)
				_, marker := recoveryBytes(p)
				state, _ := p.read("tailscaled.state")
				p.close()
				saved := cfg
				saved.Recovery, saved.DialIP, saved.ControlURL = nil, recoveryOldIP, homeControlURL+savedSlash
				if code := initHomeProfile(saved); code != 0 {
					t.Fatalf("slash-bearing saved trust could not reopen: %d", code)
				}
				tsnet_close()
				cfg.ControlURL += newSlash
				if code := initHomeProfile(cfg); code != 0 {
					t.Fatalf("slash-bearing trust could not admit recovery: %d", code)
				}
				if managed.server.ControlURL != homeControlURL {
					t.Fatal("runtime URL was not canonical")
				}
				if completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, managed.profile, client) != nil {
					t.Fatal("slash-bearing trust could not recover")
				}
				if managed.profile.record.ControlURL != homeControlURL || managed.profile.record.DialIP != recoveryNewIP {
					t.Fatal("protected receipt is not canonical")
				}
				afterState, _ := managed.profile.read("tailscaled.state")
				_, afterMarker := recoveryBytes(managed.profile)
				if !bytes.Equal(afterState, state) || !bytes.Equal(afterMarker, marker) {
					t.Fatal("node or consumed identity changed")
				}
				tsnet_close()
				resumed := cfg
				resumed.Recovery = nil
				if code := initHomeProfile(resumed); code != 0 {
					t.Fatalf("shipping empty-key reopen failed: %d", code)
				}
				tsnet_close()
				if code := initHomeProfile(cfg); code != 0 {
					t.Fatalf("exact recovery receipt retry failed: %d", code)
				}
				if completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, managed.profile, client) != nil {
					t.Fatal("canonical receipt could not revalidate saved grant")
				}
				tsnet_close()
			})
		}
	}
}
func TestHomeAddressRecoveryRefusesV1(t *testing.T) {
	for _, version := range []int{0, 1} {
		t.Run(fmt.Sprint(version), func(t *testing.T) {
			cleanEngine(t)
			_, cfg, client, requests := recoveryFixture(t, 200)
			legacyPath := profilePath(t)
			legacy, err := openManagedProfile(legacyPath, testHostname, "https://192.168.1.40:8443", true)
			if err != nil {
				t.Fatal("legacy fixture failed")
			}
			defer legacy.close()
			if version == 1 {
				legacy.record.Version, legacy.record.StableNodeID, legacy.record.PreviousControlURL = 1, cfg.Recovery.StableNodeID, "https://192.168.1.39:8443"
			}
			if os.WriteFile(filepath.Join(legacyPath, "tailscaled.state"), []byte("legacy-node"), 0600) != nil || legacy.commitConsumed() != nil {
				t.Fatal("legacy fixture failed")
			}
			before, marker := recoveryBytes(legacy)
			if !errors.Is(completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, legacy, client), errHomeReapproval) {
				t.Error("v1 profile did not require reapproval")
			}
			after, afterMarker := recoveryBytes(legacy)
			if !bytes.Equal(before, after) || !bytes.Equal(marker, afterMarker) || requests.Load() != 0 {
				t.Error("v1 refusal changed protected files or contacted connector")
			}
			legacy.close()
			cfg.StateDirectory = legacyPath
			if code := initHomeProfile(cfg); code != homeReapprovalRequired {
				t.Errorf("saved v1 startup did not return reapproval required: %d", code)
			}
		})
	}
}
