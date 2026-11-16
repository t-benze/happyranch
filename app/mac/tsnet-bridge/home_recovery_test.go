package main

import (
	"context"
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

const recoveryOldURL = "https://192.168.1.40:8443"
const recoveryNewURL = "https://192.168.1.41:8443"
const recoveryCanary = "SYNTHETIC-RECOVERY-BEARER-CANARY"

func recoveryFixture(t *testing.T, status int) (*managedProfile, homeLaunch, *http.Client, *atomic.Int32) {
	t.Helper()
	p, err := openManagedProfile(profilePath(t), testHostname, recoveryOldURL, true)
	if err != nil {
		t.Fatal("profile creation failed")
	}
	t.Cleanup(func() { p.close() })
	if os.WriteFile(filepath.Join(p.path, "tailscaled.state"), []byte("synthetic-persisted-node"), 0600) != nil || p.commitConsumed() != nil {
		t.Fatal("saved membership setup failed")
	}
	cfg := homeLaunch{ControlURL: recoveryNewURL, Target: "100.64.0.1:443",
		Recovery: &homeAddressRecovery{PreviousControlURL: recoveryOldURL, StableNodeID: "stable-synthetic-node", Bearer: recoveryCanary}}
	requests := new(atomic.Int32)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		if r.Method != "GET" || r.URL.Path != "/api/v1/health" || r.Host != cfg.Target || r.Header.Get("X-HappyRanch-Device-Credential") != recoveryCanary || r.Header.Get("Authorization") != "" {
			t.Error("saved-grant request escaped its fixed route, target or authorization")
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

func TestHomeAddressRecoveryCommitAndReopen(t *testing.T) {
	p, cfg, client, requests := recoveryFixture(t, 200)
	oldMarker, _ := p.read("consumed")
	state, _ := p.read("tailscaled.state")
	if completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, p, client) != nil {
		t.Fatal("authorized recovery refused")
	}
	if requests.Load() != 1 || p.record.Version != 1 || p.record.ControlURL != recoveryNewURL || p.record.StableNodeID != cfg.Recovery.StableNodeID || p.record.PreviousControlURL != recoveryOldURL {
		t.Fatal("protected recovery receipt missing")
	}
	marker, _ := p.read("consumed")
	afterState, _ := p.read("tailscaled.state")
	if string(marker) == string(oldMarker) || !strings.HasPrefix(string(marker), "v1:") || string(state) != string(afterState) {
		t.Fatal("migration failed to bind new marker or replaced node state")
	}
	path := p.path
	p.close()
	reopened, err := openManagedProfile(path, testHostname, recoveryNewURL, false)
	if err != nil {
		t.Fatal("new protected binding cannot reopen")
	}
	reopened.close()
	if old, err := openManagedProfile(path, testHostname, recoveryOldURL, false); err == nil {
		old.close()
		t.Fatal("old URL replay accepted")
	}
	if fresh, err := openManagedProfile(path, testHostname, recoveryNewURL, true); err == nil {
		fresh.close()
		t.Fatal("new key consumed in existing profile")
	}
	// A replayed legacy marker is never repaired against the migrated profile.
	if os.WriteFile(filepath.Join(path, "consumed"), oldMarker, 0600) != nil {
		t.Fatal("marker fixture failed")
	}
	if replay, err := openManagedProfile(path, testHostname, recoveryNewURL, false); err == nil {
		replay.close()
		t.Fatal("legacy consumed marker replay accepted")
	}
}

func TestHomeAddressRecoveryRefusals(t *testing.T) {
	for _, scenario := range []string{"different-node", "missing-grant", "denied-grant", "redirect", "missing-marker", "replayed-marker", "wrong-old-url", "stale-pending"} {
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
			case "wrong-old-url":
				cfg.Recovery.PreviousControlURL = "https://192.168.1.99:8443"
			case "stale-pending":
				os.WriteFile(filepath.Join(p.path, "consumed.pending"), []byte("unknown-writer"), 0600)
			}
			beforeProfile, _ := p.read("profile.json")
			beforeMarker, _ := p.read("consumed")
			if completeHomeAddressRecovery(context.Background(), cfg, node, p, client) == nil {
				t.Error("unauthorized metadata recovery accepted")
			}
			afterProfile, _ := p.read("profile.json")
			afterMarker, _ := p.read("consumed")
			if string(beforeProfile) != string(afterProfile) || string(beforeMarker) != string(afterMarker) {
				t.Error("refusal rewrote protected binding or marker")
			}
			if scenario == "denied-grant" || scenario == "redirect" {
				if requests.Load() != 1 {
					t.Error("grant refusal did not reach connector admission")
				}
			}
			if scenario == "different-node" || scenario == "missing-grant" || scenario == "missing-marker" || scenario == "replayed-marker" || scenario == "wrong-old-url" {
				if requests.Load() != 0 {
					t.Error("local refusal contacted connector")
				}
			}
		})
	}
}

func TestHomeAddressRecoveryRetryReceipt(t *testing.T) {
	p, cfg, client, requests := recoveryFixture(t, 200)
	if completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, p, client) != nil {
		t.Fatal("initial recovery failed")
	}
	profile, _ := p.read("profile.json")
	marker, _ := p.read("consumed")
	if completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, p, client) != nil || requests.Load() != 2 {
		t.Fatal("exact retry failed to revalidate saved grant")
	}
	afterProfile, _ := p.read("profile.json")
	afterMarker, _ := p.read("consumed")
	if string(profile) != string(afterProfile) || string(marker) != string(afterMarker) {
		t.Fatal("exact retry rewrote native receipt")
	}
	cfg.Recovery.PreviousControlURL = "https://192.168.1.99:8443"
	if completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, p, client) == nil {
		t.Fatal("foreign retry receipt accepted")
	}
}

func TestHomeAddressRecoveryLaunchRefusals(t *testing.T) {
	t.Setenv("GODEBUG", "x509usefallbackroots=1")
	for _, k := range ambientKeys {
		t.Setenv(k, "")
		os.Unsetenv(k)
	}
	ca := makeHomeCA(t, true)
	cfg := homeLaunch{CA: ca.der, Fingerprint: homeFingerprint(ca.der), ControlURL: recoveryNewURL,
		Hostname: testHostname, StateDirectory: "/synthetic-profile", Target: "100.64.0.1:443",
		Recovery: &homeAddressRecovery{PreviousControlURL: recoveryOldURL, StableNodeID: "synthetic-node", Bearer: recoveryCanary}}
	if _, err := validateHomeLaunch(cfg); err != nil {
		t.Fatal("valid recovery launch refused")
	}
	for _, scenario := range []string{"new-key", "http-old-url", "missing-grant", "header-injection", "missing-node"} {
		t.Run(scenario, func(t *testing.T) {
			changed := cfg
			recovery := *cfg.Recovery
			changed.Recovery = &recovery
			switch scenario {
			case "new-key":
				changed.JoinKey = strings.Repeat("ab", 24)
			case "http-old-url":
				recovery.PreviousControlURL = "http://192.168.1.40:8443"
			case "missing-grant":
				recovery.Bearer = ""
			case "header-injection":
				recovery.Bearer += "\r\nInjected: value"
			case "missing-node":
				recovery.StableNodeID = ""
			}
			if _, err := validateHomeLaunch(changed); err == nil {
				t.Error("unsafe recovery launch accepted")
			}
		})
	}
}

func TestHomeAddressRecoveryRedaction(t *testing.T) {
	recovery := homeAddressRecovery{Bearer: recoveryCanary}
	display := fmt.Sprintf("%v %#v %v", recovery, recovery, errManagedState)
	if strings.Contains(display, recoveryCanary) {
		t.Fatal("recovery diagnostic leaked material")
	}
}

func TestHomeAddressRecoveryInterruptedCommitStaysClosed(t *testing.T) {
	p, cfg, client, _ := recoveryFixture(t, 200)
	oldProfile, _ := p.read("profile.json")
	oldMarker, _ := p.read("consumed")
	// The existing exclusive-pending-file contract simulates a failed second
	// write after the new consumed marker is durably committed.
	if os.WriteFile(filepath.Join(p.path, "profile.json.pending"), []byte("unknown-writer"), 0600) != nil {
		t.Fatal("pending fixture failed")
	}
	if completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, p, client) == nil {
		t.Fatal("partial commit reported success")
	}
	profile, _ := p.read("profile.json")
	marker, _ := p.read("consumed")
	if string(profile) != string(oldProfile) || string(marker) == string(oldMarker) {
		t.Fatal("fixture did not reach the interrupted two-file commit")
	}
	path := p.path
	p.close()
	for _, control := range []string{recoveryOldURL, recoveryNewURL} {
		if reopened, err := openManagedProfile(path, testHostname, control, false); err == nil {
			reopened.close()
			t.Fatal("interrupted migration was silently repaired")
		}
	}
}

func TestHomeAddressRecoveryProfileAdmission(t *testing.T) {
	cleanEngine(t)
	p, cfg, client, _ := recoveryFixture(t, 200)
	cfg.StateDirectory = p.path
	cfg.Hostname = testHostname
	if p.phase("submitting") != nil {
		t.Fatal("offline reconnect fixture failed")
	}
	p.close()
	if code := initHomeProfile(cfg); code != 0 {
		t.Fatalf("consumed offline profile refused: %d", code)
	}
	if managed.server.ControlURL != recoveryNewURL || managed.profile.record.ControlURL != recoveryOldURL {
		t.Fatal("native admission rewrote protected URL before live validation")
	}
	tsnet_close()
	p, err := openManagedProfile(cfg.StateDirectory, testHostname, recoveryOldURL, false)
	if err != nil {
		t.Fatal("old binding lost before validation")
	}
	if p.phase("joined") != nil {
		t.Fatal("fixture failed")
	}
	if completeHomeAddressRecovery(context.Background(), cfg, cfg.Recovery.StableNodeID, p, client) != nil {
		t.Fatal("migration fixture failed")
	}
	p.close()
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
