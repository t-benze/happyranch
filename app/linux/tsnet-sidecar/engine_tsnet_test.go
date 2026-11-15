package sidecar

import (
	"context"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"tailscale.com/logtail"
	"tailscale.com/logtail/filch"
)

func TestTSNetStartDisablesLogging(t *testing.T) {
	// Disable is irreversible and other engine tests start tsnet, so each mode
	// needs a fresh process to prove that Start itself disables uploads.
	const child = "HAPPYRANCH_TEST_TSNET_LOGGING"
	mode := os.Getenv(child)
	if mode == "" {
		for _, mode := range []string{"enrollment", "restart"} {
			t.Run(mode, func(t *testing.T) {
				ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
				defer cancel()
				cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestTSNetStartDisablesLogging$")
				cmd.Env = append(os.Environ(), child+"="+mode)
				out, err := cmd.CombinedOutput()
				if err != nil {
					t.Fatalf("isolated Start logging check: %v\n%s", err, out)
				}
				if strings.Contains(string(out), "synthetic-private-endpoint") {
					t.Fatal("tsnet logging callbacks emitted raw backend text")
				}
			})
		}
		return
	}
	unsetAmbientAuthentication(t)
	var credential []byte
	if mode == "enrollment" {
		credential = []byte("synthetic-explicit-input")
	}
	// Reach the production constructor through Start, then fail on an unusable
	// state path before any backend/network initialization.
	state := filepath.Join(t.TempDir(), "not-a-directory")
	if err := os.WriteFile(state, []byte("state-path-blocker"), 0o600); err != nil {
		t.Fatal(err)
	}
	engine := NewTSNetEngine()
	defer engine.Close()
	_, err := engine.Start(context.Background(), EngineConfig{StateDir: state}, credential)
	if !errors.Is(err, ErrEngine) || engine.server == nil {
		t.Fatalf("Start did not reach tsnet state-path failure: %v", err)
	}
	for name, logf := range map[string]func(string, ...any){"Logf": engine.server.Logf, "UserLogf": engine.server.UserLogf} {
		if logf == nil {
			t.Errorf("tsnet %s must be an explicit no-op", name)
			continue
		}
		logf("backend: %s", "synthetic-private-endpoint")
	}

	var uploads atomic.Int32
	upload := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		uploads.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	defer upload.Close()
	buffer := &observedLogtailBuffer{Buffer: logtail.NewMemoryBuffer(16)}
	// Unlike tsnet.startLogger (which skips Go tests), the real logtail logger
	// exercises v1.78.0's process-wide Disable gate before buffer.Write.
	logger := logtail.NewLogger(logtail.Config{
		BaseURL: upload.URL, HTTPC: upload.Client(), Stderr: io.Discard,
		Buffer: buffer, FlushDelayFn: func() time.Duration { return 0 },
	}, func(string, ...any) {})
	logger.Logf("synthetic-private-endpoint")
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := logger.Shutdown(ctx); err != nil {
		t.Fatal(err)
	}
	if got := buffer.writes.Load(); got != 0 {
		t.Errorf("logtail queued %d records for upload after Start, want 0", got)
	}
	if got := uploads.Load(); got != 0 {
		t.Errorf("logtail made %d HTTP uploads after Start, want 0", got)
	}
}

// tsnet.startLogger skips Go tests. After the real production Start path, reopen
// its exact disk spool with the pinned filch/logtail implementations and a local
// collector; this observes historical egress independently of the cleanup code.
func TestTSNetStartClearsHistoricalLogging(t *testing.T) {
	const child = "HAPPYRANCH_TEST_TSNET_HISTORICAL_LOGGING"
	mode := os.Getenv(child)
	if mode == "" {
		for _, mode := range []string{"enrollment/log1", "enrollment/log2", "enrollment/both", "restart/log1", "restart/log2", "restart/both"} {
			t.Run(mode, func(t *testing.T) {
				ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
				defer cancel()
				cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestTSNetStartClearsHistoricalLogging$", "-test.v")
				cmd.Env = append(os.Environ(), child+"="+mode)
				out, err := cmd.CombinedOutput()
				if err != nil {
					t.Fatalf("isolated historical logging check: %v\n%s", err, out)
				}
				t.Logf("%s", out)
			})
		}
		return
	}
	unsetAmbientAuthentication(t)
	state := t.TempDir()
	// The deliberately invalid store makes real tsnet.Start stop before joining
	// or writing identity state. The cleanup must leave these unrelated bytes.
	preserved := map[string]string{
		"tailscaled.state":    "synthetic-invalid-durable-state\n",
		"tailscaled.log.conf": "synthetic-log-configuration\n",
		"unrelated.txt":       "unrelated-synthetic-data\n",
	}
	for name, content := range preserved {
		if err := os.WriteFile(filepath.Join(state, name), []byte(content), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	for i, name := range []string{"tailscaled.log1.txt", "tailscaled.log2.txt"} {
		if strings.HasSuffix(mode, "/both") || strings.HasSuffix(mode, "/log"+string(rune('1'+i))) {
			if err := os.WriteFile(filepath.Join(state, name), []byte("{\"text\":\"synthetic-historical-backend-record\"}\n"), 0o600); err != nil {
				t.Fatal(err)
			}
		}
	}
	var credential []byte
	if strings.HasPrefix(mode, "enrollment/") {
		credential = []byte("synthetic-explicit-input")
	}
	engine := NewTSNetEngine()
	_, err := engine.Start(context.Background(), EngineConfig{StateDir: state}, credential)
	if !errors.Is(err, ErrEngine) || engine.server == nil {
		t.Fatalf("Start did not reach real tsnet with the invalid store: %v", err)
	}
	if err := engine.Close(); err != nil {
		t.Fatal(err)
	}
	// Check before filch/logtail can drain anything: an upload followed by an
	// empty file is not evidence that startup cleared the historical records.
	for _, name := range []string{"tailscaled.log1.txt", "tailscaled.log2.txt"} {
		b, err := os.ReadFile(filepath.Join(state, name))
		if err != nil && !errors.Is(err, os.ErrNotExist) {
			t.Fatal(err)
		}
		if len(b) != 0 {
			t.Errorf("%s retained %d historical bytes after Start", name, len(b))
		}
	}
	for name, want := range preserved {
		if got, err := os.ReadFile(filepath.Join(state, name)); err != nil || string(got) != want {
			t.Errorf("Start changed %s: read error %v", name, err)
		}
	}
	var uploads atomic.Int32
	collector := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		uploads.Add(1)
		w.WriteHeader(http.StatusOK)
	}))
	defer collector.Close()
	buffer, err := filch.New(filepath.Join(state, "tailscaled"), filch.Options{})
	if err != nil {
		t.Fatal(err)
	}
	defer buffer.Close()
	logger := logtail.NewLogger(logtail.Config{
		BaseURL: collector.URL, HTTPC: collector.Client(), Stderr: io.Discard,
		Buffer: buffer, FlushDelayFn: func() time.Duration { return 0 },
	}, func(string, ...any) {})
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := logger.Shutdown(ctx); err != nil {
		t.Fatal(err)
	}
	if got := uploads.Load(); got != 0 {
		t.Errorf("historical spool made %d HTTP uploads after Start, want 0", got)
	}
	for _, name := range []string{"tailscaled.log1.txt", "tailscaled.log2.txt"} {
		if b, err := os.ReadFile(filepath.Join(state, name)); err != nil || len(b) != 0 {
			t.Errorf("spool %s not empty after logger shutdown: %v", name, err)
		}
	}
	t.Logf("%s: collector requests=%d; spool empty; state/config/unrelated bytes unchanged", mode, uploads.Load())
}

func TestTSNetStartRejectsUnsafeLogSpool(t *testing.T) {
	for _, name := range []string{"tailscaled.log1.txt", "tailscaled.log2.txt"} {
		for _, kind := range []string{"symlink", "dangling-symlink", "directory", "fifo"} {
			t.Run(name+"/"+kind, func(t *testing.T) {
				unsetAmbientAuthentication(t)
				state := t.TempDir()
				for _, name := range []string{"tailscaled.state", "tailscaled.log.conf"} {
					if err := os.WriteFile(filepath.Join(state, name), []byte("synthetic-preserved-state"), 0o600); err != nil {
						t.Fatal(err)
					}
				}
				path := filepath.Join(state, name)
				target := filepath.Join(t.TempDir(), "target")
				if err := os.WriteFile(target, []byte("synthetic-protected-target"), 0o600); err != nil {
					t.Fatal(err)
				}
				var err error
				switch kind {
				case "symlink":
					err = os.Symlink(target, path)
				case "dangling-symlink":
					err = os.Symlink(target+"-absent", path)
				case "directory":
					err = os.Mkdir(path, 0o700)
				case "fifo":
					err = syscall.Mkfifo(path, 0o600)
				}
				if err != nil {
					t.Fatal(err)
				}
				before, err := os.Lstat(path)
				if err != nil {
					t.Fatal(err)
				}
				engine := NewTSNetEngine()
				defer engine.Close()
				receipt, err := engine.Start(context.Background(), EngineConfig{StateDir: state}, nil)
				if !errors.Is(err, ErrEngine) || !strings.Contains(err.Error(), "log spool") || !strings.Contains(err.Error(), name) {
					t.Errorf("Start error = %v, want clear log spool refusal", err)
				}
				if receipt != (RedemptionReceipt{}) {
					t.Error("unsafe spool returned a redemption receipt")
				}
				if after, err := os.Lstat(path); err != nil || !os.SameFile(before, after) || before.Mode() != after.Mode() {
					t.Errorf("unsafe spool entry changed: %v", err)
				}
				if got, err := os.ReadFile(target); err != nil || string(got) != "synthetic-protected-target" {
					t.Errorf("symlink target changed: %v", err)
				}
				if _, err := os.Lstat(target + "-absent"); !errors.Is(err, os.ErrNotExist) {
					t.Errorf("dangling target created: %v", err)
				}
				for _, name := range []string{"tailscaled.state", "tailscaled.log.conf"} {
					if got, err := os.ReadFile(filepath.Join(state, name)); err != nil || string(got) != "synthetic-preserved-state" {
						t.Errorf("unsafe-spool refusal changed %s: %v", name, err)
					}
				}
			})
		}
	}
}

type observedLogtailBuffer struct {
	logtail.Buffer
	writes atomic.Int32
}

func (b *observedLogtailBuffer) Write(p []byte) (int, error) {
	b.writes.Add(1)
	return b.Buffer.Write(p)
}

func TestTSNetStartWaitsForUpUntilContextDeadline(t *testing.T) {
	unsetAmbientAuthentication(t)
	t.Setenv("TS_NO_LOGS_NO_SUPPORT", "true")
	// A local, deliberately unavailable control server leaves the real engine
	// joining. Start must wait for Up, rather than reject its transient state.
	control := httptest.NewTLSServer(http.NotFoundHandler())
	defer control.Close()
	engine := NewTSNetEngine()
	defer engine.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_, err := engine.Start(ctx, EngineConfig{
		StateDir: t.TempDir(), ControlURL: control.URL,
		RoleIdentity: "home-sidecar-wait", ExpectedPeer: "missing-peer",
	}, nil)
	if !errors.Is(err, ErrEngine) {
		t.Fatalf("Start error = %v, want engine unavailable", err)
	}
	if !errors.Is(ctx.Err(), context.DeadlineExceeded) {
		t.Fatal("Start rejected a joining engine before the readiness deadline")
	}
}

// These are process inputs, so isolate them without adding a production hook.
func unsetAmbientAuthentication(t *testing.T) {
	t.Helper()
	for _, name := range []string{"TS_AUTHKEY", "TS_AUTH_KEY", "TSNET_FORCE_LOGIN", "TS_DEBUGSYSPOLICY_AUTH_KEY"} {
		t.Setenv(name, "") // arrange restoration of the original environment
		if err := os.Unsetenv(name); err != nil {
			t.Fatal(err)
		}
	}
}

func TestTSNetStartRejectsAmbientAuthentication(t *testing.T) {
	for _, name := range []string{"TS_AUTHKEY", "TS_AUTH_KEY", "TSNET_FORCE_LOGIN", "TS_DEBUGSYSPOLICY_AUTH_KEY"} {
		for _, value := range []struct{ name, value string }{
			{"nonempty", "synthetic-ambient-input"}, {"empty", ""},
		} {
			for _, mode := range []struct {
				name       string
				credential []byte
			}{
				{"restart", nil}, {"enrollment", []byte("synthetic-explicit-input")},
			} {
				t.Run(name+"/"+value.name+"/"+mode.name, func(t *testing.T) {
					unsetAmbientAuthentication(t)
					ambient := value.value
					if name == "TSNET_FORCE_LOGIN" && ambient != "" {
						ambient = "true"
					}
					t.Setenv(name, ambient)
					// If the guard regresses, real tsnet.Start fails at MkdirAll
					// before initializing its backend or doing any network I/O.
					state := filepath.Join(t.TempDir(), "not-a-directory")
					if err := os.WriteFile(state, []byte("state-path-blocker"), 0o600); err != nil {
						t.Fatal(err)
					}
					engine := NewTSNetEngine()
					defer engine.Close()
					receipt, err := engine.Start(context.Background(), EngineConfig{StateDir: state}, mode.credential)
					want := "sidecar: unset " + name + "; ambient tsnet authentication is forbidden"
					if err == nil || err.Error() != want {
						t.Errorf("Start error = %v, want %q", err, want)
					}
					if receipt != (RedemptionReceipt{}) {
						t.Error("refused startup returned a redemption receipt")
					}
					if engine.server != nil {
						t.Error("ambient input reached tsnet server construction/start")
					}
					if got, ok := os.LookupEnv(name); !ok || got != ambient {
						t.Error("Start mutated the process environment")
					}
				})
			}
		}
	}
}

func TestTSNetStartWithoutAmbientAuthenticationReachesTSNet(t *testing.T) {
	for _, mode := range []struct {
		name       string
		credential []byte
	}{
		{"restart", nil}, {"enrollment", []byte("synthetic-explicit-input")},
	} {
		t.Run(mode.name, func(t *testing.T) {
			unsetAmbientAuthentication(t)
			state := filepath.Join(t.TempDir(), "not-a-directory")
			if err := os.WriteFile(state, []byte("state-path-blocker"), 0o600); err != nil {
				t.Fatal(err)
			}
			engine := NewTSNetEngine()
			defer engine.Close()
			_, err := engine.Start(context.Background(), EngineConfig{StateDir: state}, mode.credential)
			if !errors.Is(err, ErrEngine) {
				t.Fatalf("real tsnet state-path failure = %v, want engine unavailable", err)
			}
			if engine.server == nil {
				t.Fatal("startup did not reach real tsnet")
			}
			if engine.server.Dir != state || engine.server.AuthKey != string(mode.credential) {
				t.Fatal("tsnet did not receive the explicit state path and credential")
			}
		})
	}
}
