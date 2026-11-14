package main

import (
	"golang.org/x/sys/unix"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

const testControl = "https://127.0.0.1:1"
const testHostname = "managed-native-test"
const testProfileID = "f2496a40-46a3-4b14-871f-2b0604ebc269"

func profilePath(t *testing.T) string {
	t.Helper()
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	return filepath.Join(root, "ManagedTailnet", testProfileID)
}
func cleanEngine(t *testing.T) {
	t.Helper()
	for _, name := range ambientKeys {
		old, ok := os.LookupEnv(name)
		os.Unsetenv(name)
		t.Cleanup(func() {
			if ok {
				os.Setenv(name, old)
			} else {
				os.Unsetenv(name)
			}
		})
	}
	tsnet_close()
	t.Cleanup(func() { tsnet_close() })
}
func TestManagedAmbientPresenceRefused(t *testing.T) {
	cleanEngine(t)
	for _, name := range ambientKeys {
		for _, value := range []string{"", "synthetic-override"} {
			t.Run(name+"/"+map[bool]string{true: "empty", false: "present"}[value == ""], func(t *testing.T) {
				t.Setenv(name, value)
				path := profilePath(t)
				if got := managedInit("synthetic-enrollment", testControl, testHostname, path); got != -10 {
					t.Fatalf("init=%d, want -10", got)
				}
				if _, err := os.Lstat(filepath.Dir(path)); !os.IsNotExist(err) {
					t.Fatalf("refused init touched state: %v", err)
				}
			})
		}
	}
}
func TestManagedInvalidConfiguration(t *testing.T) {
	cleanEngine(t)
	for _, tc := range []struct{ name, control, hostname string }{
		{"http", "http://127.0.0.1", testHostname}, {"default", "", testHostname},
		{"userinfo", "https://user@127.0.0.1", testHostname}, {"query", "https://127.0.0.1?a=b", testHostname},
		{"fragment", "https://127.0.0.1/#x", testHostname}, {"path", "https://127.0.0.1/path", testHostname},
		{"control", "https://127.0.0.1\n", testHostname}, {"hostname", testControl, "bad host"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if got := managedInit("synthetic-enrollment", tc.control, tc.hostname, profilePath(t)); got != -10 {
				t.Fatalf("init=%d, want -10", got)
			}
		})
	}
}
func TestManagedUnstartedCancelAndLegacyRefusal(t *testing.T) {
	cleanEngine(t)
	if got := tsnet_up_managed(1); got != -15 {
		t.Fatalf("uninitialized Up=%d", got)
	}
	if got := managedInit("synthetic-enrollment", testControl, testHostname, profilePath(t)); got != 0 {
		t.Fatalf("init=%d", got)
	}
	if got := managedInit("synthetic-enrollment", testControl, testHostname, profilePath(t)); got != -11 {
		t.Fatalf("duplicate init=%d", got)
	}
	// N7: native mode guard before Up. Running-engine/sink proof is separate.
	if got := tsnet_dial(nil, nil, 1); got != -17 {
		t.Errorf("legacy probe=%d", got)
	}
	if got := tsnet_conn_dial(nil, nil, 1); got != -17 {
		t.Errorf("legacy duplex=%d", got)
	}
	if got := tsnet_start(); got != -11 {
		t.Fatalf("legacy start=%d", got)
	}
	tsnet_cancel()
	if got := tsnet_up_managed(1); got != -12 {
		t.Fatalf("canceled Up=%d", got)
	}
	tsnet_close() // Unstarted config must be discarded without Server.Close.
	if got := managedInit("synthetic-enrollment", testControl, testHostname, profilePath(t)); got != 0 {
		t.Fatalf("reuse after close=%d", got)
	}
}
func TestManagedEnvironmentRecheckedBeforeUp(t *testing.T) {
	cleanEngine(t)
	path := profilePath(t)
	if got := managedInit("synthetic-enrollment", testControl, testHostname, path); got != 0 {
		t.Fatalf("init=%d", got)
	}
	t.Setenv("TS_AUTHKEY", "")
	if got := tsnet_up_managed(1); got != -10 {
		t.Fatalf("late override Up=%d", got)
	}
	b, err := os.ReadFile(filepath.Join(path, "profile.json"))
	if err != nil || !strings.Contains(string(b), `"phase":"draft"`) {
		t.Fatalf("submitted despite override: %v", err)
	}
}
func TestManagedStateRefusesUnsafeAndConflictingProfiles(t *testing.T) {
	cleanEngine(t)
	t.Run("missing-resume", func(t *testing.T) {
		if got := managedInit("", testControl, testHostname, profilePath(t)); got != -16 {
			t.Fatalf("resume=%d", got)
		}
	})
	for _, kind := range []string{"symlink-parent", "symlink-file", "loose-file", "hardlink", "nonempty", "lock"} {
		t.Run(kind, func(t *testing.T) {
			path := profilePath(t)
			parent := filepath.Dir(path)
			if err := os.MkdirAll(path, 0700); err != nil {
				t.Fatal(err)
			}
			switch kind {
			case "symlink-parent":
				if err := os.Remove(path); err != nil {
					t.Fatal(err)
				}
				if err := os.Remove(parent); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink(t.TempDir(), parent); err != nil {
					t.Fatal(err)
				}
			case "symlink-file":
				if err := os.Symlink("missing", filepath.Join(path, "state")); err != nil {
					t.Fatal(err)
				}
			case "loose-file":
				if err := os.WriteFile(filepath.Join(path, "state"), []byte("synthetic"), 0644); err != nil {
					t.Fatal(err)
				}
			case "hardlink":
				src := filepath.Join(t.TempDir(), "state")
				if err := os.WriteFile(src, []byte("synthetic"), 0600); err != nil {
					t.Fatal(err)
				}
				if err := os.Link(src, filepath.Join(path, "state")); err != nil {
					t.Fatal(err)
				}
			case "nonempty":
				if err := os.WriteFile(filepath.Join(path, "tailscaled.state"), []byte("synthetic"), 0600); err != nil {
					t.Fatal(err)
				}
			case "lock":
				f, err := os.OpenFile(filepath.Join(path, ".lock"), os.O_CREATE|os.O_RDWR, 0600)
				if err != nil {
					t.Fatal(err)
				}
				defer f.Close()
				if err := unix.Flock(int(f.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
					t.Fatal(err)
				}
			}
			if got := managedInit("synthetic-enrollment", testControl, testHostname, path); got != -16 {
				t.Fatalf("unsafe init=%d", got)
			}
		})
	}
}
func TestManagedSavedMembershipValidation(t *testing.T) {
	cleanEngine(t)
	path := profilePath(t)
	p, err := openManagedProfile(path, testHostname, testControl, true)
	if err != nil {
		t.Fatal(err)
	}
	if err = p.phase("submitting"); err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(path, "tailscaled.state"), []byte(`{"synthetic":"node-state"}`), 0600); err != nil {
		t.Fatal(err)
	}
	p.close()
	// Synthetic state proves admission/persistence only, never Running identity.
	if got := managedInit("", testControl, testHostname, path); got != 0 {
		t.Fatalf("explicit saved check=%d", got)
	}
	tsnet_close()
	p, err = openManagedProfile(path, testHostname, testControl, false)
	if err != nil {
		t.Fatal(err)
	}
	if err = p.commitConsumed(); err != nil {
		t.Fatal(err)
	}
	p.close()
	if got := managedInit("", testControl, testHostname, path); got != 0 {
		t.Fatalf("saved init=%d", got)
	}
	tsnet_close()
	if got := managedInit("fresh-key", testControl, testHostname, path); got != -16 {
		t.Fatalf("key replay=%d", got)
	}
	if got := managedInit("", testControl, "different-host", path); got != -16 {
		t.Fatalf("identity conflict=%d", got)
	}
	if err = os.WriteFile(filepath.Join(path, "consumed"), []byte("conflicting-marker\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if got := managedInit("", testControl, testHostname, path); got != -16 {
		t.Fatalf("marker conflict=%d", got)
	}
}

func TestManagedDeadlinePersistsSubmissionAndSettles(t *testing.T) {
	cleanEngine(t)
	path := profilePath(t)
	if got := managedInit("synthetic-enrollment", testControl, testHostname, path); got != 0 {
		t.Fatalf("init=%d", got)
	}
	if got := tsnet_up_managed(1); got != -13 {
		t.Fatalf("bounded unreachable Up=%d, want -13", got)
	}
	b, err := os.ReadFile(filepath.Join(path, "profile.json"))
	if err != nil || !strings.Contains(string(b), `"phase":"submitting"`) {
		t.Fatalf("missing submission phase: %v", err)
	}
	if _, err := os.Lstat(filepath.Join(path, "consumed")); !os.IsNotExist(err) {
		t.Fatalf("failed enrollment published consumed marker: %v", err)
	}
	if got := tsnet_up_managed(1); got != -11 {
		t.Fatalf("attempt replay=%d", got)
	}
	tsnet_close()
	if got := managedInit("synthetic-enrollment", testControl, testHostname, profilePath(t)); got != 0 {
		t.Fatalf("teardown did not release owner: %d", got)
	}
}

func TestManagedConcurrentCloseCancelsUp(t *testing.T) {
	cleanEngine(t)
	path := profilePath(t)
	if got := managedInit("synthetic-enrollment", testControl, testHostname, path); got != 0 {
		t.Fatalf("init=%d", got)
	}
	upDone := make(chan int, 1)
	go func() { upDone <- int(tsnet_up_managed(1000)) }()
	deadline := time.Now().Add(time.Second)
	for {
		b, _ := os.ReadFile(filepath.Join(path, "profile.json"))
		if strings.Contains(string(b), `"phase":"submitting"`) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("Up did not enter submitting")
		}
		time.Sleep(time.Millisecond)
	}
	tsnet_close() // Cancels first, then waits for the Up owner to settle Start.
	select {
	case got := <-upDone:
		if got != -12 {
			t.Fatalf("concurrent close Up=%d, want -12", got)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Up owner did not settle")
	}
	if got := managedInit("synthetic-enrollment", testControl, testHostname, profilePath(t)); got != 0 {
		t.Fatalf("reuse=%d", got)
	}
}

func TestManagedCloseBeforeInitPublicationCancelsUp(t *testing.T) {
	cleanEngine(t)
	// Hold the traffic-close boundary after Close's initial no-owner check.
	connMu.Lock()
	held := true
	closed := make(chan struct{})
	go func() { tsnet_close(); close(closed) }()
	defer func() {
		if held {
			connMu.Unlock()
		}
		select {
		case <-closed:
		case <-time.After(2 * time.Second):
			t.Error("Close did not settle")
		}
	}()
	deadline := time.Now().Add(time.Second)
	for {
		stack := make([]byte, 65536)
		n := runtime.Stack(stack, true)
		if strings.Contains(string(stack[:n]), "happyranch/tsnet-bridge.closeAllConnections") {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("Close did not reach the traffic-close boundary")
		}
		runtime.Gosched()
	}
	path := profilePath(t)
	if got := managedInit("synthetic-enrollment", testControl, testHostname, path); got != 0 {
		t.Fatalf("init=%d", got)
	}
	upDone := make(chan int, 1)
	started := time.Now()
	go func() { upDone <- int(tsnet_up_managed(250)) }()
	deadline = time.Now().Add(time.Second)
	for {
		b, _ := os.ReadFile(filepath.Join(path, "profile.json"))
		if strings.Contains(string(b), `"phase":"submitting"`) {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("Up did not enter submitting")
		}
		runtime.Gosched()
	}
	connMu.Unlock()
	held = false
	select {
	case got := <-upDone:
		t.Logf("Close preceded init publication; Up=%d; elapsed=%s", got, time.Since(started))
		if got != -12 {
			t.Fatalf("Close missed the selected engine cancellation: Up=%d, want -12", got)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Up owner did not settle")
	}
	select {
	case <-closed:
	case <-time.After(2 * time.Second):
		t.Fatal("Close did not release the owner")
	}
	if got := managedInit("synthetic-enrollment", testControl, testHostname, profilePath(t)); got != 0 {
		t.Fatalf("reuse=%d", got)
	}
}
