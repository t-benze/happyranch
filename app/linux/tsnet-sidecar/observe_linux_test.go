package sidecar

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/netip"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"golang.org/x/sys/unix"
	"tailscale.com/ipn"
	"tailscale.com/ipn/ipnstate"
	"tailscale.com/types/key"
)

type observationClient struct {
	status *ipnstate.Status
	prefs  *ipn.Prefs
	err    error
}

func (c observationClient) Status(context.Context) (*ipnstate.Status, error) { return c.status, c.err }
func (c observationClient) GetPrefs(context.Context) (*ipn.Prefs, error)     { return c.prefs, c.err }
func observationFixture() (observationClient, *HomeBinding) {
	var public key.NodePublic
	_ = public.UnmarshalText([]byte("nodekey:" + strings.Repeat("1", 64)))
	return observationClient{status: &ipnstate.Status{BackendState: "Running", Self: &ipnstate.PeerStatus{ID: "self-stable", PublicKey: public, TailscaleIPs: []netip.Addr{netip.MustParseAddr("100.64.0.10")}}}, prefs: &ipn.Prefs{ControlURL: homeURL}}, &HomeBinding{"home", "control", "connector", "7"}
}
func TestObservationReply(t *testing.T) {
	for _, scenario := range []string{"valid", "engine", "not-running", "no-self", "no-key", "no-prefs", "wrong-control", "no-binding", "oversize", "deadline"} {
		t.Run(scenario, func(t *testing.T) {
			client, binding := observationFixture()
			ctx := context.Background()
			switch scenario {
			case "engine":
				client.err = errors.New("synthetic-secret")
			case "not-running":
				client.status.BackendState = "Stopped"
			case "no-self":
				client.status.Self = nil
			case "no-key":
				client.status.Self.PublicKey = key.NodePublic{}
			case "no-prefs":
				client.prefs = nil
			case "wrong-control":
				client.prefs.ControlURL = "https://wrong.invalid"
			case "no-binding":
				binding = nil
			case "oversize":
				binding.ActivationGeneration = json.Number(strings.Repeat("9", 4096))
			case "deadline":
				var cancel context.CancelFunc
				ctx, cancel = context.WithCancel(ctx)
				cancel()
			}
			data, err := observeSelf(ctx, client, binding)
			if scenario != "valid" {
				if err == nil || len(data) != 0 {
					t.Fatal("unsafe observation returned")
				}
				return
			}
			var reply map[string]json.RawMessage
			if err != nil || json.Unmarshal(data, &reply) != nil || len(reply) != 6 || len(data) > 4096 {
				t.Fatal("reply shape")
			}
			for _, name := range []string{"format", "stable_id", "tailnet_addresses", "control_url", "node_key_sha256", "binding"} {
				if reply[name] == nil {
					t.Fatal("missing field", name)
				}
			}
			if !strings.Contains(string(data), `"node_key_sha256":"bf2f7365cba59a01d8c433001a0139fb8c5a3c9754793bc3f73f826ff4299e19"`) || strings.Contains(string(data), "nodekey:") {
				t.Fatal("digest/raw-key contract")
			}
		})
	}
}
func shortObservationPath(t *testing.T, path string) string {
	t.Helper()
	parent, err := os.Open(filepath.Dir(path))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { parent.Close() })
	return fmt.Sprintf("/proc/self/fd/%d/%s", parent.Fd(), filepath.Base(path))
}
func TestObservationSocket(t *testing.T) {
	old := unix.Umask(0077)
	defer unix.Umask(old)
	for _, scenario := range []string{"new", "stale", "file", "symlink", "hardlink", "parent-symlink", "parent-mode"} {
		t.Run(scenario, func(t *testing.T) {
			parent := t.TempDir()
			path := filepath.Join(parent, "observe.sock")
			switch scenario {
			case "stale", "hardlink":
				l, err := net.ListenUnix("unix", &net.UnixAddr{Name: shortObservationPath(t, path), Net: "unix"})
				if err != nil {
					t.Fatal(err)
				}
				l.SetUnlinkOnClose(false)
				_ = l.Close()
				if scenario == "hardlink" {
					if err := os.Link(path, path+".link"); err != nil {
						t.Fatal(err)
					}
				}
			case "file":
				_ = os.WriteFile(path, []byte("preserve"), 0600)
			case "symlink":
				_ = os.Symlink("missing", path)
			case "parent-symlink":
				alias := parent + "-alias"
				_ = os.Symlink(parent, alias)
				defer os.Remove(alias)
				path = filepath.Join(alias, "observe.sock")
			case "parent-mode":
				_ = os.Chmod(parent, 0777)
			}
			listener, err := listenObservation(path)
			if scenario != "new" && scenario != "stale" {
				if err == nil {
					listener.Close()
					t.Fatal("unsafe entry admitted")
				}
				if scenario != "parent-mode" {
					if _, err := os.Lstat(path); scenario != "parent-symlink" && err != nil {
						t.Fatal("entry removed")
					}
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			var st unix.Stat_t
			if unix.Lstat(path, &st) != nil || st.Mode&07777 != 0600 || st.Uid != uint32(os.Geteuid()) || st.Nlink != 1 {
				t.Fatal("socket custody")
			}
			client, binding := observationFixture()
			server := serveObservation(listener, func(ctx context.Context) ([]byte, error) { return observeSelf(ctx, client, binding) })
			c, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: shortObservationPath(t, path), Net: "unix"})
			if err != nil {
				t.Fatal(err)
			}
			_ = c.SetDeadline(time.Now().Add(time.Second))
			_, _ = io.WriteString(c, observeRequest)
			_ = c.CloseWrite()
			data, err := io.ReadAll(c)
			_ = c.Close()
			if err != nil || len(data) == 0 || strings.Contains(string(data), "nodekey:") {
				t.Fatal("socket observation")
			}
			_ = server.Close()
			if _, err := os.Lstat(path); !os.IsNotExist(err) {
				t.Fatal("orderly stop retained socket")
			}
		})
	}
}
func TestObservationRequestAndSize(t *testing.T) {
	old := unix.Umask(0077)
	defer unix.Umask(old)
	for _, scenario := range []string{"extra-request", "malformed", "oversize", "engine-failure", "deadline"} {
		t.Run(scenario, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "observe.sock")
			listener, err := listenObservation(path)
			if err != nil {
				t.Fatal(err)
			}
			server := serveObservation(listener, func(context.Context) ([]byte, error) {
				if scenario == "engine-failure" {
					return nil, ErrEngine
				}
				if scenario == "oversize" {
					return []byte(strings.Repeat("x", 4097)), nil
				}
				return []byte("{}"), nil
			})
			defer server.Close()
			c, err := net.DialUnix("unix", nil, &net.UnixAddr{Name: shortObservationPath(t, path), Net: "unix"})
			if err != nil {
				t.Fatal(err)
			}
			defer c.Close()
			_ = c.SetDeadline(time.Now().Add(6 * time.Second))
			request := observeRequest
			if scenario == "extra-request" {
				request += observeRequest
			}
			if scenario == "malformed" {
				request = "wrong\n"
			}
			if scenario != "deadline" {
				_, _ = io.WriteString(c, request)
				_ = c.CloseWrite()
			}
			start := time.Now()
			data, _ := io.ReadAll(c)
			if len(data) != 0 {
				t.Fatal("invalid request/reply exposed")
			}
			if scenario == "deadline" && (time.Since(start) < 4*time.Second || time.Since(start) > 6*time.Second) {
				t.Fatal("deadline not enforced")
			}
		})
	}
	// Inert/legacy engines never even inspect the fixed runtime path.
	if err := (&TSNetEngine{}).startHomeObserver(EngineConfig{}); err != nil {
		t.Fatal(err)
	}
}
