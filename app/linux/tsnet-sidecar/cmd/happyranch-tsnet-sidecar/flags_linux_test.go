//go:build linux

package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
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

func TestRunRejectsNullProfileBeforeStateOrCredential(t *testing.T) {
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
	if code := run(context.Background(), []string{"--config", path}); code != 2 {
		t.Fatalf("null Profile: command exit %d, want configuration exit 2", code)
	}
	if _, err := os.Stat(cfg.StateDir); !os.IsNotExist(err) {
		t.Fatalf("configuration refusal touched state: %v", err)
	}
	if data, err := os.ReadFile(cfg.CredentialFile); err != nil || string(data) != "synthetic-input" {
		t.Fatalf("configuration refusal changed credential: %v", err)
	}
}
