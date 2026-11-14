//go:build darwin

package sidecar

import (
	"context"
	"errors"
	"net"
	"os"
	"path/filepath"
	"testing"
)

func TestDarwinIgnoresCredentialDirectory(t *testing.T) {
	for _, mode := range []os.FileMode{0o400, 0o600} {
		t.Run(mode.String(), func(t *testing.T) {
			cfg := validConfig(t)
			cfg.CredentialFile = filepath.Join(filepath.Dir(cfg.CredentialFile), "enrollment-key")
			if err := os.WriteFile(cfg.CredentialFile, []byte("synthetic-darwin-input"), mode); err != nil {
				t.Fatal(err)
			}
			t.Setenv("CREDENTIALS_DIRECTORY", filepath.Dir(cfg.CredentialFile))
			if systemdInput(cfg) {
				t.Fatal("Darwin selected systemd credential semantics")
			}
			events := []string{}
			receipt := RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}
			engine := &fakeEngine{events: &events, receipt: receipt, listenErr: ErrListener}
			svc := New(cfg, engine, &net.Dialer{})
			if mode == 0o400 {
				if err := svc.Start(context.Background()); !errors.Is(err, ErrCredential) || len(events) != 0 {
					t.Fatalf("read-only systemd copy admitted: %v %v", err, events)
				}
				return
			}
			// The existing one-use commit happens before the loopback probe/listener.
			_ = svc.Start(context.Background())
			if _, err := os.Lstat(cfg.CredentialFile); !errors.Is(err, os.ErrNotExist) {
				t.Fatalf("one-use input not deleted: %v", err)
			}
			if got, err := os.ReadFile(filepath.Join(cfg.StateDir, consumedMarker)); err != nil || string(got) != validMarkerContent {
				t.Fatalf("durable consumed marker: %v", err)
			}
			if got, err := consumeInput(cfg); err != nil || len(got) != 0 {
				t.Fatalf("empty-key restart: %v", err)
			}
		})
	}
}
