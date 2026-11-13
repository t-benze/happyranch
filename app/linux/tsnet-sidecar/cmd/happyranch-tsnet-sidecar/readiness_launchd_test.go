//go:build linux || darwin

package main

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	sidecar "happyranch/linux-tsnet-sidecar"
)

func readReadiness(t *testing.T, path string) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var record map[string]any
	if err := json.Unmarshal(raw, &record); err != nil {
		t.Fatalf("partial/invalid JSON: %v", err)
	}
	if len(record) != 6 {
		t.Fatalf("record fields: %v", record)
	}
	for _, key := range []string{"pid", "process_started_at", "ready", "last_healthy_wall_time", "sequence", "reason"} {
		if _, ok := record[key]; !ok {
			t.Fatalf("missing C3 field %s", key)
		}
	}
	st, err := os.Lstat(path)
	if err != nil || !st.Mode().IsRegular() || st.Mode().Perm() != 0o600 {
		t.Fatalf("unsafe record metadata: %v %v", st, err)
	}
	return record
}

func awaitReadiness(t *testing.T, path string, want func(map[string]any) bool) map[string]any {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if _, err := os.Lstat(path); err == nil {
			record := readReadiness(t, path)
			if want(record) {
				return record
			}
		}
		time.Sleep(time.Millisecond)
	}
	t.Fatal("readiness did not reach expected state")
	return nil
}

func TestLaunchdReadinessLifecycle(t *testing.T) {
	for _, scenario := range []string{"stop", "listener-loss", "startup-failure"} {
		t.Run(scenario, func(t *testing.T) {
			// Darwin must not consult inherited systemd watchdog/notification inputs.
			t.Setenv("WATCHDOG_USEC", "invalid")
			t.Setenv("NOTIFY_SOCKET", "/nonexistent-notify-socket")
			e := &commandEngine{release: make(chan struct{})}
			if scenario == "startup-failure" {
				e.startErr = sidecar.ErrEngine
			}
			svc := commandService(t, e)
			path := filepath.Join(t.TempDir(), "state", "sidecar.readiness.json")
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			result := make(chan int, 1)
			go func() { result <- serveLaunchd(ctx, svc, path, 2*time.Second) }()
			starting := awaitReadiness(t, path, func(r map[string]any) bool { return r["reason"] == "starting" })
			if starting["ready"] != false || starting["last_healthy_wall_time"] != nil || starting["sequence"] != float64(1) || starting["pid"] != float64(os.Getpid()) || starting["process_started_at"].(float64) <= 0 {
				t.Fatalf("pre-Up record: %v", starting)
			}
			close(e.release)
			if scenario == "startup-failure" {
				if code := resultCode(t, result); code != 1 {
					t.Fatalf("startup failure exit=%d", code)
				}
				stopped := readReadiness(t, path)
				if stopped["ready"] != false || stopped["reason"] != "waiting-for-readiness" || stopped["last_healthy_wall_time"] != nil {
					t.Fatalf("failed startup readiness=%v", stopped)
				}
				return
			}
			ready := awaitReadiness(t, path, func(r map[string]any) bool { return r["ready"] == true })
			if ready["reason"] != "healthy" || ready["last_healthy_wall_time"].(float64) < ready["process_started_at"].(float64) {
				t.Fatalf("healthy record=%v", ready)
			}
			refreshed := awaitReadiness(t, path, func(r map[string]any) bool { return r["sequence"].(float64) > ready["sequence"].(float64) })
			if refreshed["last_healthy_wall_time"].(float64) < ready["last_healthy_wall_time"].(float64) {
				t.Fatal("healthy time did not refresh")
			}
			wantCode, wantReason := 0, "stopping"
			if scenario == "listener-loss" {
				e.listener.Close()
				wantCode, wantReason = 1, "readiness-or-listener-lost"
			} else {
				cancel()
			}
			if code := resultCode(t, result); code != wantCode || svc.Running() || !e.closed.Load() {
				t.Fatalf("teardown exit=%d running=%v closed=%v", code, svc.Running(), e.closed.Load())
			}
			stopped := readReadiness(t, path)
			if stopped["ready"] != false || stopped["reason"] != wantReason || stopped["last_healthy_wall_time"] == nil || stopped["sequence"].(float64) <= refreshed["sequence"].(float64) {
				t.Fatalf("final record=%v", stopped)
			}
			entries, err := os.ReadDir(filepath.Dir(path))
			if err != nil || len(entries) != 1 {
				t.Fatalf("temporary publication residue: %v %v", entries, err)
			}
		})
	}
}

func TestLaunchdPublicationNoFollow(t *testing.T) {
	for _, scenario := range []string{"parent-symlink", "loose-parent", "leaf-symlink", "leaf-directory", "relative", "dotdot"} {
		t.Run(scenario, func(t *testing.T) {
			root := t.TempDir()
			parent := filepath.Join(root, "private")
			if err := os.Mkdir(parent, 0o700); err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(parent, "ready.json")
			target := filepath.Join(root, "target")
			if err := os.WriteFile(target, []byte("untouched"), 0o600); err != nil {
				t.Fatal(err)
			}
			wantError := true
			switch scenario {
			case "parent-symlink":
				alias := filepath.Join(root, "alias")
				if err := os.Symlink(parent, alias); err != nil {
					t.Fatal(err)
				}
				path = filepath.Join(alias, "ready.json")
			case "loose-parent":
				if err := os.Chmod(parent, 0o755); err != nil {
					t.Fatal(err)
				}
			case "leaf-symlink":
				if err := os.Symlink(target, path); err != nil {
					t.Fatal(err)
				}
				wantError = false
			case "leaf-directory":
				if err := os.Mkdir(path, 0o700); err != nil {
					t.Fatal(err)
				}
			case "relative":
				path = "relative/ready.json"
			case "dotdot":
				path = parent + "/../private/ready.json"
			}
			record := readinessRecord{PID: os.Getpid(), ProcessStartedAt: 1, Reason: "starting"}
			if err := record.publish(path); (err != nil) != wantError {
				t.Fatalf("publication err=%v wantError=%v", err, wantError)
			}
			raw, err := os.ReadFile(target)
			if err != nil || string(raw) != "untouched" {
				t.Fatalf("write followed leaf: %q %v", raw, err)
			}
			if !wantError {
				readReadiness(t, path)
			}
			entries, err := os.ReadDir(parent)
			if err != nil {
				t.Fatal(err)
			}
			for _, entry := range entries {
				if strings.HasPrefix(entry.Name(), ".") {
					t.Fatalf("failed publication left temp %s", entry.Name())
				}
			}
			if scenario == "leaf-directory" {
				st, err := os.Lstat(path)
				if err != nil || !st.IsDir() {
					t.Fatal("failed rename damaged prior destination")
				}
			}
		})
	}
}

// Exercise the existing production Engine interface, including a blocked close;
// the watchdog must not depend on that interface returning.
type blockedCloseEngine struct {
	commandEngine
	closing chan struct{}
}

func (e *blockedCloseEngine) Close() error { <-e.closing; return nil }

func TestLaunchdWatchdogStalledPass(t *testing.T) {
	if scenario := os.Getenv("SIDECAR_WATCHDOG_CASE"); scenario != "" {
		e := &commandEngine{release: make(chan struct{})}
		svc := commandService(t, e)
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		path := os.Getenv("SIDECAR_WATCHDOG_RECORD")
		if scenario == "shutdown" {
			// Swap only the existing engine interface, never the watchdog or publisher.
			cfg := configForCommand(t)
			connector, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			defer connector.Close()
			cfg.ConnectorAddr = connector.Addr().String()
			blocked := &blockedCloseEngine{commandEngine: commandEngine{listener: e.listener}, closing: make(chan struct{})}
			svc = sidecar.New(cfg, blocked, &net.Dialer{})
			go func() { awaitReadiness(t, path, func(r map[string]any) bool { return r["ready"] == true }); cancel() }()
		}
		os.Exit(serveLaunchd(ctx, svc, path, 150*time.Millisecond))
	}
	for _, scenario := range []string{"startup", "shutdown"} {
		t.Run(scenario, func(t *testing.T) {
			// Give the publisher its own private state leaf; TempDir itself can
			// inherit a group-accessible mode from the test runner's umask.
			path := filepath.Join(t.TempDir(), "state", "record.json")
			ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestLaunchdWatchdogStalledPass$")
			cmd.Env = append(os.Environ(), "SIDECAR_WATCHDOG_CASE="+scenario, "SIDECAR_WATCHDOG_RECORD="+path)
			output, err := cmd.CombinedOutput()
			var exit *exec.ExitError
			if !errors.As(err, &exit) || exit.ExitCode() != 75 {
				t.Fatalf("stalled %s exit=%v output=%s", scenario, err, output)
			}
			record := readReadiness(t, path)
			reason := "starting"
			if scenario == "shutdown" {
				reason = "stopping"
			}
			if record["ready"] != false || record["reason"] != reason {
				t.Fatalf("stalled record=%v", record)
			}
		})
	}
}

func TestLaunchdHealthyPassRenewsDeadline(t *testing.T) {
	if os.Getenv("SIDECAR_HEALTHY_CHILD") != "1" {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestLaunchdHealthyPassRenewsDeadline$")
		cmd.Env = append(os.Environ(), "SIDECAR_HEALTHY_CHILD=1")
		if output, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("healthy publications failed to renew watchdog: %v %s", err, output)
		}
		return
	}
	e := &commandEngine{}
	svc := commandService(t, e)
	path := filepath.Join(t.TempDir(), "state", "readiness.json")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan int, 1)
	go func() { result <- serveLaunchd(ctx, svc, path, 400*time.Millisecond) }()
	first := awaitReadiness(t, path, func(r map[string]any) bool { return r["ready"] == true })
	// Multiple completed publications carry a healthy service past its initial
	// watchdog deadline. A missing healthy() tick exits the test subprocess too.
	time.Sleep(650 * time.Millisecond)
	next := readReadiness(t, path)
	if next["sequence"].(float64) <= first["sequence"].(float64) || next["ready"] != true {
		t.Fatalf("healthy service stalled: %v", next)
	}
	cancel()
	if code := resultCode(t, result); code != 0 {
		t.Fatalf("healthy exit=%d", code)
	}
}

func TestLaunchdPublicationFailureClosesService(t *testing.T) {
	e := &commandEngine{}
	svc := commandService(t, e)
	parent := filepath.Join(t.TempDir(), "state")
	path := filepath.Join(parent, "ready.json")
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan int, 1)
	go func() { result <- serveLaunchd(ctx, svc, path, 2*time.Second) }()
	awaitReadiness(t, path, func(r map[string]any) bool { return r["ready"] == true })
	// A real filesystem policy loss makes the next publication fail. The previous
	// complete record remains; process status must cease claiming readiness.
	if err := os.Chmod(parent, 0o755); err != nil {
		t.Fatal(err)
	}
	defer os.Chmod(parent, 0o700)
	if code := resultCode(t, result); code != 1 || svc.Running() || !e.closed.Load() {
		t.Fatalf("publication loss exit=%d running=%v closed=%v", code, svc.Running(), e.closed.Load())
	}
	readReadiness(t, path)
	entries, err := os.ReadDir(parent)
	if err != nil || len(entries) != 1 {
		t.Fatalf("publication loss damaged prior file or left temp: %v %v", entries, err)
	}
}
