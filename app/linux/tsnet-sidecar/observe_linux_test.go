package sidecar

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/netip"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"tailscale.com/ipn"
	"tailscale.com/ipn/ipnstate"
	"tailscale.com/types/key"
)

// Boundary contracts: real filesystem socket admission/cleanup; closed loaded
// binding; actual LocalAPI projection and bounded wire reply, no raw key.
// Regressions: chmod after listen, unlink foreign entries, drop binding admission,
// serialize PublicKey or return cached Self after engine failure. Existing health/
// identity tests own different endpoints and lack this wire/custody boundary.
// No exported test-only seam: same-package tests use the actual listener/handler.
func TestObservationListener(t *testing.T) {
	for _, scenario := range []string{"clean", "stale", "file", "symlink", "nlink", "parent-link", "parent-mode"} {
		t.Run(scenario, func(t *testing.T) {
			dir := t.TempDir()
			_ = os.Chmod(dir, 0700)
			path := filepath.Join(dir, "observe.sock")
			if scenario == "file" {
				_ = os.WriteFile(path, []byte("foreign"), 0600)
			}
			if scenario == "symlink" {
				_ = os.Symlink("missing", path)
			}
			if scenario == "stale" || scenario == "nlink" {
				l, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
				if err != nil {
					t.Fatal(err)
				}
				l.SetUnlinkOnClose(false)
				_ = l.Close()
				if scenario == "nlink" {
					if err := os.Link(path, path+".link"); err != nil {
						t.Fatal(err)
					}
				}
			}
			if scenario == "parent-mode" {
				_ = os.Chmod(dir, 0777)
			}
			if scenario == "parent-link" {
				_ = os.Symlink(dir, dir+"-alias")
				defer os.Remove(dir + "-alias")
				path = filepath.Join(dir+"-alias", "observe.sock")
			}
			l, err := newObservationListener(path)
			if scenario != "clean" && scenario != "stale" {
				if err == nil {
					l.Close()
					t.Fatal("unsafe entry admitted")
				}
				if scenario == "file" {
					if data, _ := os.ReadFile(path); string(data) != "foreign" {
						t.Fatal("foreign file changed")
					}
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			st, err := os.Lstat(path)
			if err != nil || st.Mode().Perm() != 0600 || st.Mode()&os.ModeSocket == 0 || !ownedByCurrentUser(st) {
				t.Fatal("socket custody")
			}
			_ = l.Close()
			if _, err := os.Lstat(path); !os.IsNotExist(err) {
				t.Fatal("socket retained after orderly close")
			}
		})
	}
}

func TestHomeBindingAdmission(t *testing.T) {
	valid := `{"home_id":"home","control_plane_id":"control","connector_id":"connector","activation_generation":7}`
	for _, data := range []string{valid, "null", `{}`, strings.Replace(valid, `"home"`, `"!"`, 1), strings.Replace(valid, ":7", ":true", 1), strings.Replace(valid, ":7", ":-1", 1), strings.Replace(valid, ":7", ":7.0", 1), strings.Replace(valid, ":7", `:"7"`, 1), valid[:len(valid)-1] + `,"extra":1}`, valid[:len(valid)-1] + `,"home_id":"home"}`} {
		for _, profile := range []string{"home", "identity", "legacy", "bad-tls", "bad-url"} {
			cfg := validConfig(t)
			if profile != "legacy" {
				cfg.Profile, cfg.ExpectedPeer, cfg.ConnectorAddr = LinuxIdentityProfile, "", ""
			}
			if profile != "identity" {
				cfg.ControlURL = homeURL
				cfg.ControlTLS = &HomeControlTLS{DialIP: "192.168.1.1", CAFingerprint: strings.Repeat("a", 64)}
			}
			if profile == "bad-tls" {
				cfg.ControlTLS.DialIP = "8.8.8.8"
			}
			if profile == "bad-url" {
				cfg.ControlURL += "/"
			}
			cfg.HomeBinding = bindingConfig(data)
			if (cfg.Validate() == nil) != (data == valid && profile == "home") {
				t.Fatalf("binding admission %s/%s", data, profile)
			}
		}
	}
	var cfg Config
	if json.Unmarshal([]byte(`{"home_binding":`+valid+`,"home_binding":`+valid+`}`), &cfg) == nil {
		t.Fatal("duplicate binding admitted")
	}
	legacy := validConfig(t)
	data, _ := json.Marshal(legacy)
	if bytes.Contains(data, []byte("home_binding")) || legacy.Validate() != nil {
		t.Fatal("legacy config changed")
	}
}

type observationClient struct {
	status *ipnstate.Status
	prefs  *ipn.Prefs
	fail   bool
}

func (c observationClient) Status(context.Context) (*ipnstate.Status, error) {
	if c.fail {
		return nil, ErrEngine
	}
	return c.status, nil
}
func (c observationClient) GetPrefs(context.Context) (*ipn.Prefs, error) { return c.prefs, nil }

func TestObservationReply(t *testing.T) {
	for _, scenario := range []string{"ok", "request", "two", "engine", "closed", "listener", "backend", "self", "prefs", "url", "binding", "oversize"} {
		t.Run(scenario, func(t *testing.T) {
			public := key.NewNode().Public()
			client := observationClient{status: &ipnstate.Status{BackendState: "Running", Self: &ipnstate.PeerStatus{ID: "node-7", PublicKey: public, TailscaleIPs: []netip.Addr{netip.MustParseAddr("100.64.0.10")}}}, prefs: &ipn.Prefs{ControlURL: homeURL}}
			engine := &TSNetEngine{selfClient: client}
			binding, _ := parseHomeBinding([]byte(`{"home_id":"home","control_plane_id":"control","connector_id":"connector","activation_generation":7}`))
			if scenario == "engine" {
				client.fail = true
				engine.selfClient = client
			}
			if scenario == "closed" {
				_ = engine.Close()
			}
			if scenario == "backend" {
				client.status.BackendState = "Stopped"
			}
			if scenario == "self" {
				client.status.Self = nil
			}
			if scenario == "prefs" {
				client.prefs = nil
				engine.selfClient = client
			}
			if scenario == "url" {
				client.prefs.ControlURL += "/"
			}
			if scenario == "binding" {
				binding = nil
			}
			if scenario == "oversize" {
				for i := 0; i < 400; i++ {
					client.status.Self.TailscaleIPs = append(client.status.Self.TailscaleIPs, netip.MustParseAddr("100.64.0.10"))
				}
			}
			dir := t.TempDir()
			_ = os.Chmod(dir, 0700)
			l, err := newObservationListener(filepath.Join(dir, "observe.sock"))
			if err != nil {
				t.Fatal(err)
			}
			defer l.Close()
			s := &Sidecar{engine: engine, listener: l, observationBinding: binding}
			if scenario == "listener" {
				s.listener = nil
			}
			done := make(chan struct{})
			go func() {
				defer close(done)
				c, err := l.Accept()
				if err == nil {
					s.serveObservation(context.Background(), c)
				}
			}()
			conn, err := net.Dial("unix", l.(*observationListener).path)
			if err != nil {
				t.Fatal(err)
			}
			defer conn.Close()
			_ = conn.SetDeadline(time.Now().Add(6 * time.Second))
			request := observeRequest
			if scenario == "request" {
				request = "other\n"
			}
			if scenario == "two" {
				request += observeRequest
			}
			_, _ = io.WriteString(conn, request)
			_ = conn.(*net.UnixConn).CloseWrite()
			data, _ := io.ReadAll(conn)
			<-done
			if scenario != "ok" {
				if len(data) != 0 {
					t.Fatal("unavailable observation replied")
				}
				return
			}
			var value map[string]json.RawMessage
			if json.Unmarshal(data, &value) != nil || len(value) != 6 || len(data) > 4096 {
				t.Fatal("reply shape/size")
			}
			var got selfObservation
			_ = json.Unmarshal(data, &got)
			if got.NodeKeySHA256 != fmt.Sprintf("%x", sha256.Sum256([]byte(public.String()))) || bytes.Contains(data, []byte(public.String())) || got.StableID != "node-7" || got.Binding.HomeID != "home" {
				t.Fatal("Self projection/raw key")
			}
		})
	}
}
