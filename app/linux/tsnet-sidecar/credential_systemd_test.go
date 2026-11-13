//go:build linux

package sidecar

import (
	"context"
	"errors"
	"net"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

func TestSystemdCredentialLifecycle(t *testing.T) {
	for _, name := range []string{"enrollment", "enrollment_acl_mask", "restart_absent", "restart_directory_absent", "enrollment_directory_absent", "restart_ancestor_absent", "restart_ancestor_symlink", "restart_present", "invalid_mode", "wrong_name", "symlink"} {
		t.Run(name, func(t *testing.T) {
			cfg := validConfig(t)
			dir := filepath.Join(t.TempDir(), "credentials")
			absentDirectory := strings.HasSuffix(name, "directory_absent") || strings.HasPrefix(name, "restart_ancestor_")
			switch name {
			case "restart_ancestor_absent":
				dir = filepath.Join(dir, "unit")
			case "restart_ancestor_symlink":
				if err := os.Symlink(t.TempDir(), dir); err != nil {
					t.Fatal(err)
				}
				dir = filepath.Join(dir, "unit")
			default:
				if !absentDirectory {
					if err := os.Mkdir(dir, 0o700); err != nil {
						t.Fatal(err)
					}
				}
			}
			t.Setenv("CREDENTIALS_DIRECTORY", dir)
			cfg.CredentialFile = filepath.Join(dir, "enrollment-key")
			if name == "wrong_name" {
				cfg.CredentialFile = filepath.Join(dir, "other")
			}
			if name != "restart_absent" && !absentDirectory {
				mode := os.FileMode(0o400)
				if name == "enrollment_acl_mask" {
					mode = 0o440
				}
				if name == "invalid_mode" {
					mode = 0o600
				}
				if err := os.WriteFile(cfg.CredentialFile, []byte("synthetic-systemd-input\n"), mode); err != nil {
					t.Fatal(err)
				}
			}
			if name == "symlink" {
				if err := os.Remove(cfg.CredentialFile); err != nil {
					t.Fatal(err)
				}
				if err := os.Symlink("absent", cfg.CredentialFile); err != nil {
					t.Fatal(err)
				}
			}
			marker := filepath.Join(cfg.StateDir, consumedMarker)
			if strings.HasPrefix(name, "restart_") {
				if err := os.WriteFile(marker, []byte(validMarkerContent), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			before, _ := os.Lstat(marker)
			// An attempted unlink fails for this service-owned, non-writable directory.
			if !absentDirectory {
				if err := os.Chmod(dir, 0o500); err != nil {
					t.Fatal(err)
				}
				t.Cleanup(func() { _ = os.Chmod(dir, 0o700) })
			}
			events := []string{}
			l := &oneListener{blockAfter: true, closed: make(chan struct{}), accepted: make(chan struct{}), events: &events}
			e := &fakeEngine{listener: l, receipt: RedemptionReceipt{true, true, true}, events: &events}
			client, server := net.Pipe()
			defer server.Close()
			defer client.Close()
			svc := New(cfg, e, dialFunc(func(context.Context, string, string) (net.Conn, error) {
				events = append(events, "probe")
				return client, nil
			}))
			t.Cleanup(func() { _ = svc.Stop() })
			err := svc.Start(context.Background())
			enroll := name == "enrollment" || name == "enrollment_acl_mask"
			restart := name == "restart_absent" || name == "restart_directory_absent"
			if !enroll && !restart {
				if !errors.Is(err, ErrCredential) || len(events) != 0 {
					t.Fatalf("unsafe credential admitted: err=%v events=%v", err, events)
				}
				if before == nil {
					if _, err := os.Lstat(marker); !errors.Is(err, syscall.ENOENT) {
						t.Fatalf("refusal created a marker: %v", err)
					}
				}
				return
			}
			if err != nil {
				t.Fatalf("systemd %s: %v", name, err)
			}
			want := "synthetic-systemd-input\n"
			if restart {
				want = ""
			}
			if string(e.credential) != want || strings.Join(events, ",") != "start,probe,listen" {
				t.Fatal("key delivery or startup gates changed")
			}
			content, err := os.ReadFile(marker)
			if err != nil || string(content) != validMarkerContent {
				t.Fatalf("consumption marker not committed: %v", err)
			}
			if enroll {
				content, err = os.ReadFile(cfg.CredentialFile)
				if err != nil || string(content) != want {
					t.Fatalf("systemd copy removed or changed: %v", err)
				}
			} else {
				after, err := os.Lstat(marker)
				if err != nil || !os.SameFile(before, after) || !before.ModTime().Equal(after.ModTime()) {
					t.Fatal("restart rewrote marker")
				}
				if _, err := os.Lstat(cfg.CredentialFile); !errors.Is(err, syscall.ENOENT) {
					t.Fatal("restart recreated credential")
				}
				if absentDirectory {
					if _, err := os.Lstat(dir); !errors.Is(err, syscall.ENOENT) {
						t.Fatalf("restart recreated credentials directory: %v", err)
					}
				}
			}
		})
	}
}
