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
