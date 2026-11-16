//go:build linux

package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

func TestReadinessFlagsRejectLinux(t *testing.T) {
	t.Setenv("WATCHDOG_USEC", "")
	t.Setenv("NOTIFY_SOCKET", "")
	t.Setenv("CREDENTIALS_DIRECTORY", "")
	cfg := configForCommand(t)
	// This config validates, but without the flag gate the ordinary credential
	// path returns service exit 1 before contacting the encrypted engine.
	if err := os.Chmod(cfg.CredentialFile, 0o400); err != nil {
		t.Fatal(err)
	}
	data, err := json.Marshal(cfg)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	for _, flags := range [][]string{{"--readiness-file", filepath.Join(t.TempDir(), "ready")}, {"--watchdog-interval", "1s"}} {
		args := append([]string{"--config", path}, flags...)
		if code := run(context.Background(), args); code != 2 {
			t.Fatalf("Linux readiness flag admitted: %d", code)
		}
	}
}

func TestMainRejectsNullProfileBeforeStateOrCredential(t *testing.T) {
	// Reuse the existing subprocess-main pattern, entirely within test code.
	if path := os.Getenv("SIDECAR_NULL_PROFILE_CONFIG"); path != "" {
		os.Args = []string{"happyranch-tsnet-sidecar", "--config", path}
		main()
		return
	}
	t.Setenv("WATCHDOG_USEC", "")
	t.Setenv("NOTIFY_SOCKET", "")
	t.Setenv("CREDENTIALS_DIRECTORY", "")
	cfg := configForCommand(t)
	// Missing state makes an unfixed command exit 1 before any engine/network
	// activity; configuration refusal must instead exit 2 without touching input.
	cfg.StateDir = filepath.Join(t.TempDir(), "missing-state")
	data, err := json.Marshal(cfg)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, append([]byte(`{"Profile":null,`), data[1:]...), 0o600); err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestMainRejectsNullProfileBeforeStateOrCredential$")
	cmd.Env = append(os.Environ(), "SIDECAR_NULL_PROFILE_CONFIG="+path)
	output, err := cmd.CombinedOutput()
	var exitErr *exec.ExitError
	if !errors.As(err, &exitErr) || exitErr.ExitCode() != 2 {
		t.Fatalf("null Profile: process error %v, want configuration exit 2; output: %s", err, output)
	}
	if _, err := os.Stat(cfg.StateDir); !os.IsNotExist(err) {
		t.Fatalf("configuration refusal touched state: %v", err)
	}
	if data, err := os.ReadFile(cfg.CredentialFile); err != nil || string(data) != "synthetic-input" {
		t.Fatalf("configuration refusal changed credential: %v", err)
	}
}
