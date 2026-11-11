package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	sidecar "happyranch/linux-tsnet-sidecar"
)

// Use the shipping Sidecar with its existing Engine interface; no command-only
// lifecycle interface or injection flag is needed to observe sd_notify.
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

func notificationSocket(t *testing.T) net.PacketConn {
	t.Helper()
	// Keep the Unix socket path below sockaddr_un's limit, independent of the
	// test runner's long task-worktree TMPDIR.
	dir, err := os.MkdirTemp("/tmp", "notify-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	path := filepath.Join(dir, "socket")
	conn, err := net.ListenPacket("unixgram", path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close() })
	t.Setenv("NOTIFY_SOCKET", path)
	t.Setenv("WATCHDOG_PID", strconv.Itoa(os.Getpid()))
	return conn
}

func readNotification(t *testing.T, conn net.PacketConn) string {
	t.Helper()
	if err := conn.SetReadDeadline(time.Now().Add(3 * time.Second)); err != nil {
		t.Fatal(err)
	}
	var b [256]byte
	n, _, err := conn.ReadFrom(b[:])
	if err != nil {
		t.Fatal(err)
	}
	return string(b[:n])
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

func TestServeReadyWatchdogAndStop(t *testing.T) {
	conn := notificationSocket(t)
	t.Setenv("WATCHDOG_USEC", "100000")
	e := &commandEngine{release: make(chan struct{})}
	svc := commandService(t, e)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	result := make(chan int, 1)
	go func() { result <- serve(ctx, svc) }()
	conn.SetReadDeadline(time.Now().Add(100 * time.Millisecond))
	var b [256]byte
	if _, _, err := conn.ReadFrom(b[:]); err == nil {
		t.Fatal("notification published before engine startup completed")
	} else if timeout, ok := err.(net.Error); !ok || !timeout.Timeout() {
		t.Fatal(err)
	}
	close(e.release)
	if got := readNotification(t, conn); got != "READY=1" {
		t.Fatalf("first notification = %q", got)
	}
	start := time.Now()
	for range 2 {
		if got := readNotification(t, conn); got != "WATCHDOG=1" {
			t.Fatalf("healthy notification = %q", got)
		}
	}
	// Two ticks should arrive at roughly WATCHDOG_USEC, allowing scheduler
	// jitter while still rejecting the old fixed ten-second cadence.
	if elapsed := time.Since(start); elapsed < 70*time.Millisecond || elapsed > time.Second {
		t.Fatalf("watchdog cadence = %s", elapsed)
	}
	cancel()
	if code := resultCode(t, result); code != 0 || !e.closed.Load() || svc.Running() {
		t.Fatalf("stop: code=%d engineClosed=%v running=%v", code, e.closed.Load(), svc.Running())
	}
	if got := readNotification(t, conn); got != "STOPPING=1" {
		t.Fatalf("shutdown notification = %q", got)
	}
}

func TestServeFailureExitsWithoutRearming(t *testing.T) {
	for _, startup := range []bool{true, false} {
		name := "listener"
		if startup {
			name = "startup"
		}
		t.Run(name, func(t *testing.T) {
			conn := notificationSocket(t)
			t.Setenv("WATCHDOG_USEC", "")
			e := &commandEngine{}
			if startup {
				e.startErr = sidecar.ErrEngine
			}
			svc := commandService(t, e)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			result := make(chan int, 1)
			go func() { result <- serve(ctx, svc) }()
			if !startup {
				if got := readNotification(t, conn); got != "READY=1" {
					t.Fatalf("ready notification = %q", got)
				}
				e.listener.Close() // real accept-loop failure in the shipping Sidecar
			}
			if code := resultCode(t, result); code == 0 || !e.closed.Load() || svc.Running() {
				t.Fatalf("failure: code=%d engineClosed=%v running=%v", code, e.closed.Load(), svc.Running())
			}
			if got := readNotification(t, conn); got != "STOPPING=1" {
				t.Fatalf("failure notification = %q", got)
			}
		})
	}
}

func TestWatchdogEnvironment(t *testing.T) {
	for _, tc := range []struct {
		name, usec, pid string
		want            time.Duration
		invalid         bool
	}{
		{"disabled", "", "", 0, false},
		{"half", "30000000", "", 15 * time.Second, false},
		{"minimum", "1", "", 500 * time.Nanosecond, false},
		{"other-process", "30000000", "-1", 0, false},
		{"malformed", "invalid", "", 0, true},
		{"zero", "0", "", 0, true},
		{"overflow", "18446744073709551615", "", 0, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("WATCHDOG_USEC", tc.usec)
			t.Setenv("WATCHDOG_PID", tc.pid)
			got, err := watchdogInterval()
			if (err != nil) != tc.invalid || got != tc.want {
				t.Fatalf("interval=%s err=%v, want %s invalid=%v", got, err, tc.want, tc.invalid)
			}
		})
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

func TestMainSIGTERMDuringStartup(t *testing.T) {
	if config := os.Getenv("SIDECAR_SIGNAL_CONFIG"); config != "" {
		os.Args = []string{"happyranch-tsnet-sidecar", "--config", config}
		main()
		return
	}
	t.Setenv("TS_NO_LOGS_NO_SUPPORT", "true")
	conn := notificationSocket(t)
	t.Setenv("WATCHDOG_USEC", "")
	cfg := configForCommand(t)
	// A local TCP endpoint accepts TLS attempts but never completes the join.
	// No real control plane, enrollment or systemd service is contacted.
	control, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer control.Close()
	cfg.ControlURL = "https://" + control.Addr().String()
	data, err := json.Marshal(cfg)
	if err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	cmd := exec.Command(os.Args[0], "-test.run=^TestMainSIGTERMDuringStartup$")
	cmd.Env = append(os.Environ(), "SIDECAR_SIGNAL_CONFIG="+path)
	stderr, err := cmd.StderrPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer cmd.Process.Kill()
	started := make(chan struct{})
	readDone := make(chan struct{})
	go func() {
		defer close(readDone)
		scanner := bufio.NewScanner(stderr)
		for scanner.Scan() {
			if strings.Contains(scanner.Text(), "LocalBackend state is NeedsLogin") {
				select {
				case <-started:
				default:
					close(started)
				}
			}
		}
	}()
	select {
	case <-started:
	case <-time.After(5 * time.Second):
		cmd.Process.Kill()
		cmd.Wait()
		t.Fatal("real main did not reach engine startup")
	}
	if err := cmd.Process.Signal(syscall.SIGTERM); err != nil {
		t.Fatal(err)
	}
	finished := make(chan error, 1)
	go func() { <-readDone; finished <- cmd.Wait() }()
	select {
	case err := <-finished:
		if err != nil {
			t.Fatalf("SIGTERM exit: %v", err)
		}
	// tsnet.Close allows up to five seconds for its own best-effort flush.
	// Leave margin for cleanup while staying below the unit's 35s stop limit.
	case <-time.After(10 * time.Second):
		cmd.Process.Kill()
		<-finished
		t.Fatal("SIGTERM did not stop the foreground process")
	}
	// The real tsnet backend also publishes STATUS updates. They do not grant
	// readiness: READY/WATCHDOG before STOPPING must still fail this case.
	for range 16 {
		got := readNotification(t, conn)
		if got == "STOPPING=1" {
			return
		}
		if !strings.HasPrefix(got, "STATUS=") {
			t.Fatalf("startup SIGTERM notification = %q", got)
		}
	}
	t.Fatal("startup SIGTERM never published STOPPING")
}
