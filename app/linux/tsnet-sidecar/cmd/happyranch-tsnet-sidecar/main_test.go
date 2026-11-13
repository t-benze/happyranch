package main

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"os"
	"path/filepath"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	sidecar "happyranch/linux-tsnet-sidecar"
)

// Use the shipping Sidecar with its existing Engine interface; no command-only
// lifecycle interface or injection flag is needed for lifecycle observations.
type commandEngine struct {
	listener net.Listener
	release  chan struct{}
	startErr error
	closed   atomic.Bool
}

func (e *commandEngine) Start(ctx context.Context, _ sidecar.EngineConfig, _ []byte) (sidecar.RedemptionReceipt, error) {
	if e.release != nil {
		select {
		case <-e.release:
		case <-ctx.Done():
			return sidecar.RedemptionReceipt{}, ctx.Err()
		}
	}
	return sidecar.RedemptionReceipt{Redeemed: true, Durable: true, ExpectedPeerVisible: true}, e.startErr
}

func (e *commandEngine) Listen(string) (net.Listener, error) { return e.listener, nil }
func (e *commandEngine) Close() error {
	e.closed.Store(true)
	return nil
}

func configForCommand(t *testing.T) sidecar.Config {
	t.Helper()
	state := filepath.Join(t.TempDir(), "state")
	if err := os.Mkdir(state, 0o700); err != nil {
		t.Fatal(err)
	}
	credential := filepath.Join(filepath.Dir(state), "input")
	if err := os.WriteFile(credential, []byte("synthetic-input"), 0o600); err != nil {
		t.Fatal(err)
	}
	return sidecar.Config{
		StateDir: state, CredentialFile: credential, ControlURL: "https://control.invalid",
		RoleIdentity: "home-sidecar-command", ExpectedPeer: "peer", ListenAddr: ":443",
		ConnectorAddr: "127.0.0.1:9443", DERPPolicy: "private-only",
	}
}

func commandService(t *testing.T, e *commandEngine) *sidecar.Sidecar {
	t.Helper()
	connector, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { connector.Close() })
	cfg := configForCommand(t)
	cfg.ConnectorAddr = connector.Addr().String()
	e.listener, err = net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { e.listener.Close() })
	svc := sidecar.New(cfg, e, &net.Dialer{})
	t.Cleanup(func() { svc.Stop() })
	return svc
}

func resultCode(t *testing.T, result <-chan int) int {
	t.Helper()
	select {
	case code := <-result:
		return code
	case <-time.After(3 * time.Second):
		t.Fatal("foreground service did not exit")
		return -1
	}
}

func TestLoadConfigAndRefuseUnsafeFiles(t *testing.T) {
	cfg := configForCommand(t)
	data, err := json.Marshal(cfg)
	if err != nil {
		t.Fatal(err)
	}
	for _, name := range []string{"valid", "unknown", "trailing", "invalid", "symlink", "fifo", "directory", "oversized"} {
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "config")
			content := data
			switch name {
			case "unknown":
				content = append(append([]byte(nil), data[:len(data)-1]...), []byte(",\"Unknown\":true}")...)
			case "trailing":
				content = append(append([]byte(nil), data...), []byte(" {}")...)
			case "invalid":
				content = []byte("{}")
			case "oversized":
				content = make([]byte, (1<<20)+1)
			}
			switch name {
			case "symlink":
				err = os.Symlink(cfg.CredentialFile, path)
			case "fifo":
				err = syscall.Mkfifo(path, 0o600)
			case "directory":
				err = os.Mkdir(path, 0o700)
			default:
				err = os.WriteFile(path, content, 0o600)
			}
			if err != nil {
				t.Fatal(err)
			}
			got, err := loadConfig(path, "")
			if name == "valid" {
				if err != nil || got != cfg {
					t.Fatalf("config=%v err=%v", got, err)
				}
			} else if err == nil {
				t.Fatal("unsafe configuration accepted")
			}
		})
	}
	if _, err := loadConfig(filepath.Join(t.TempDir(), "missing"), ""); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("missing config: %v", err)
	}
}

func TestLoadConfigCredentialOverride(t *testing.T) {
	cfg := configForCommand(t)
	cfg.CredentialFile = ""
	data, err := json.Marshal(cfg)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(t.TempDir(), "credentials", "enrollment-key")
	got, err := loadConfig(path, want)
	cfg.CredentialFile = want
	if err != nil || got != cfg {
		t.Fatalf("managed credential path override not applied: %v", err)
	}
	if _, err := loadConfig(path, "relative"); err == nil {
		t.Fatal("relative override accepted")
	}
}

func TestRunRejectsInvalidArguments(t *testing.T) {
	for _, args := range [][]string{nil, {"--config", "relative"}, {"--config", "/missing", "extra"}, {"--unknown"}, {"--config", "/missing"}} {
		if code := run(context.Background(), args); code != 2 {
			t.Fatalf("run(%v)=%d, want configuration exit 2", args, code)
		}
	}
}
