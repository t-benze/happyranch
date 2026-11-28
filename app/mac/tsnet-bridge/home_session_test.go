package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"regexp"
	"runtime"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"tailscale.com/derp"
	"tailscale.com/derp/derphttp"
	"tailscale.com/ipn/store/mem"
	"tailscale.com/tailcfg"
	"tailscale.com/tstest/integration/testcontrol"
	"tailscale.com/types/key"
)

func sessionFrame(t *testing.T, data []byte) []byte {
	t.Helper()
	var frame bytes.Buffer
	if binary.Write(&frame, binary.BigEndian, uint32(len(data))) != nil {
		t.Fatal("frame size failed")
	}
	frame.Write(data)
	return frame.Bytes()
}
func sessionPayload(t *testing.T, ca []byte) []byte {
	t.Helper()
	cfg := homeLaunchFixture(ca)
	cfg.JoinKey = strings.Repeat("ab", 24)
	data, _ := json.Marshal(cfg)
	var fields map[string]json.RawMessage
	if json.Unmarshal(data, &fields) != nil {
		t.Fatal("fixture failed")
	}
	delete(fields, "stateDirectory")
	data, _ = json.Marshal(fields)
	return data
}

func TestHomeSessionLaunchClosed(t *testing.T) {
	good := sessionPayload(t, makeHomeCA(t, true).der)
	for _, scenario := range []string{"valid", "stateDirectory", "recovery", "unknown", "duplicate", "alias", "null", "missing", "type", "version", "empty-join", "bad-pin", "trailing", "oversize", "truncated"} {
		t.Run(scenario, func(t *testing.T) {
			data := append([]byte(nil), good...)
			switch scenario {
			case "stateDirectory", "recovery", "unknown":
				data = append([]byte(`{"`+scenario+`":{},`), data[1:]...)
			case "duplicate":
				data = append([]byte(`{"version":2,`), data[1:]...)
			case "alias":
				data = bytes.Replace(data, []byte(`"version"`), []byte(`"Version"`), 1)
			case "null":
				data = bytes.Replace(data, []byte(`"version":2`), []byte(`"version":null`), 1)
			case "missing":
				data = bytes.Replace(data, []byte(`,"version":2`), nil, 1)
			case "type":
				data = bytes.Replace(data, []byte(`"version":2`), []byte(`"version":"2"`), 1)
			case "version":
				data = bytes.Replace(data, []byte(`"version":2`), []byte(`"version":1`), 1)
			case "empty-join":
				data = bytes.Replace(data, []byte(strings.Repeat("ab", 24)), nil, 1)
			case "bad-pin":
				data = bytes.Replace(data, []byte(`"fingerprint":"`), []byte(`"fingerprint":"0`), 1)
			case "trailing":
				data = append(data, []byte(`{}`)...)
			case "oversize":
				data = bytes.Repeat([]byte(" "), 16385)
			}
			frame := sessionFrame(t, data)
			if scenario == "truncated" {
				frame = frame[:len(frame)-1]
			}
			_, err := readHomeSessionLaunch(bytes.NewReader(frame))
			if (err == nil) != (scenario == "valid") {
				t.Fatal("session launch admission violated closed contract")
			}
		})
	}
}

func TestHomeSessionRuntimeDirectory(t *testing.T) {
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal("canonical runtime setup failed")
	}
	if os.Chmod(root, 0700) != nil {
		t.Fatal("private runtime setup failed")
	}
	alias := filepath.Join(t.TempDir(), "alias")
	if os.Symlink(root, alias) != nil {
		t.Fatal("alias setup failed")
	}
	file := filepath.Join(t.TempDir(), "file")
	if os.WriteFile(file, nil, 0600) != nil {
		t.Fatal("file setup failed")
	}
	for _, scenario := range []string{"valid", "unset", "relative", "symlink", "aliased-parent", "file", "loose-mode"} {
		t.Run(scenario, func(t *testing.T) {
			path := root
			switch scenario {
			case "unset":
				path = ""
			case "relative":
				path = "relative"
			case "symlink":
				path = alias
			case "aliased-parent":
				path = filepath.Join(alias, "child")
			case "file":
				path = file
			case "loose-mode":
				path = t.TempDir()
				if os.Chmod(path, 0755) != nil {
					t.Fatal("mode setup failed")
				}
			}
			t.Setenv("XDG_RUNTIME_DIR", path)
			dir, err := homeSessionScratch()
			if scenario != "valid" {
				if err == nil {
					os.RemoveAll(dir)
					t.Fatal("unsafe runtime admitted")
				}
				return
			}
			if err != nil || filepath.Dir(dir) != root {
				t.Fatal("private session scratch unavailable")
			}
			defer os.RemoveAll(dir)
			info, err := os.Lstat(dir)
			if err != nil || !info.IsDir() || info.Mode().Perm() != 0700 {
				t.Fatal("scratch not private")
			}
		})
	}
}

// Separate process isolates global fallback roots, tsnet and signal ownership.
// Reset only test-side flags so upstream enables its REAL production log spool.
func TestHomeSessionProcessChild(t *testing.T) {
	mode := os.Getenv("HOME_SESSION_TEST_MODE")
	if mode == "" {
		return
	}
	log.SetOutput(io.Discard)
	flag.CommandLine = flag.NewFlagSet("session-child", flag.ContinueOnError)
	cfg, err := readHomeSessionLaunch(os.Stdin)
	if err != nil {
		t.Fatal("session launch refused")
	}
	s, err := newHomeSession(cfg)
	if err != nil {
		t.Fatal("session allocation failed")
	}
	transport, err := prepareHomeProcess(cfg.homeLaunch)
	if err != nil {
		s.Close()
		t.Fatal("transport refused")
	}
	// Existing transport tests use the same test-side endpoint isolation; the
	// production constructor's fixed RFC1918 target is checked independently.
	if transport.upstream != "192.168.1.40:28443" {
		t.Fatal("unexpected production endpoint")
	}
	transport.upstream = os.Getenv("HOME_SESSION_TEST_UPSTREAM")
	server := s.engine.server
	if s.state != nil && server.Ephemeral {
		t.Fatal("save intent remained ephemeral")
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT, syscall.SIGHUP, syscall.SIGQUIT)
	defer stop()
	if mode == "save" {
		changed := make(chan os.Signal, 1)
		signal.Notify(changed, syscall.SIGUSR1)
		defer signal.Stop(changed)
		go func() {
			select {
			case <-changed:
				_ = s.state.WriteState("later-test-state", []byte("changed"))
			case <-ctx.Done():
			}
		}()
	}
	if mode != "custody" {
		if mode == "backpressure" {
			stopDiagnostics := observeHomeShutdown(ctx, s)
			defer stopDiagnostics()
			if s.state.WriteState("pipe-padding", bytes.Repeat([]byte("x"), 16*1024)) != nil {
				t.Fatal("state padding failed")
			}
		}
		cfg.SocketPath = os.Getenv("HOME_SESSION_TEST_SOCKET")
		if mode == "error" {
			cfg.SocketPath = "relative-refused"
		}
		code := s.serve(ctx, os.Stdin, os.Stdout, transport, cfg)
		if mode == "backpressure" {
			fmt.Fprintln(os.Stderr, "shutdown-stage serve-returned")
			if code != 0 {
				t.Fatal("cancelled frame did not exit cleanly")
			}
			if _, err := os.Lstat(s.dir); !os.IsNotExist(err) {
				t.Fatal("cancelled frame left scratch")
			}
			// The parent deliberately leaves stdout full. Do not let the test
			// runner's PASS line block after production cleanup has completed.
			fmt.Fprintln(os.Stderr, "shutdown-stage scratch-removed process-exiting")
			os.Exit(0)
		}
		fmt.Fprintln(os.Stdout, code)
		if mode == "error" && code != managedStateError {
			t.Fatal("listener error not observed")
		}
		if _, err := os.Lstat(s.dir); !os.IsNotExist(err) {
			t.Fatal("serve left scratch")
		}
		return
	}
	defer transport.Close()
	defer s.Close()
	timeout := time.AfterFunc(6*time.Second, cancelManaged)
	defer timeout.Stop()
	if code := s.up(); code != 0 {
		t.Fatalf("synthetic membership failed: %d", code)
	}
	store, ok := server.Store.(*mem.Store)
	if !ok || !server.Ephemeral {
		t.Fatal("session credentials are not memory-only")
	}
	raw, err := store.ExportToJSON()
	if err != nil {
		t.Fatal("state observation failed")
	}
	var state map[string][]byte
	if json.Unmarshal(raw, &state) != nil {
		t.Fatal("memory state decoding failed")
	}
	// Observe actual machine/node private keys generated by tsnet, not fixtures
	// written to a substitute store. Keep every observed value out of diagnostics.
	private := regexp.MustCompile(`privkey:[0-9a-f]{64}`)
	keys := make(map[string]bool)
	for _, value := range state {
		for _, key := range private.FindAll(value, -1) {
			keys[string(key)] = true
		}
	}
	if len(keys) < 2 {
		t.Fatal("did not observe both machine and node private state")
	}
	secrets := [][]byte{[]byte(cfg.JoinKey), []byte("BEARER-CANARY")}
	for key := range keys {
		secrets = append(secrets, []byte(key), []byte(strings.TrimPrefix(key, "privkey:")))
		decoded, _ := hex.DecodeString(strings.TrimPrefix(key, "privkey:"))
		secrets = append(secrets, decoded)
	}
	// Product bearer is owned by the future adapter, and transits this socket
	// only as application bytes. Send a canary through the actual local relay;
	// this synthetic control plane has no target peer, so it must fail closed.
	local, err := homeListener(os.Getenv("HOME_SESSION_TEST_SOCKET"))
	if err != nil {
		t.Fatal("local socket unavailable")
	}
	done := make(chan struct{})
	go func() { serveHomeRuntime(ctx, local, "100.64.0.1"); close(done) }()
	stream, err := net.Dial("unix", local.Addr().String())
	if err != nil {
		t.Fatal("local stream unavailable")
	}
	stream.SetDeadline(time.Now().Add(3 * time.Second))
	if _, err := stream.Write([]byte("BEARER-CANARY")); err != nil {
		t.Fatal("bearer canary delivery failed")
	}
	var refusal int32
	if binary.Read(stream, binary.BigEndian, &refusal) != nil || refusal == 0 {
		t.Fatal("missing peer was not refused")
	}
	stream.Close()
	local.Close()
	<-done
	check := func() {
		files := 0
		err := filepath.WalkDir(s.dir, func(path string, entry os.DirEntry, walkErr error) error {
			if walkErr != nil {
				return walkErr
			}
			if entry.IsDir() {
				return nil
			}
			files++
			data, err := os.ReadFile(path)
			if err != nil {
				return err
			}
			if entry.Name() != "tailscaled.log.conf" && entry.Name() != "tailscaled.log1.txt" && entry.Name() != "tailscaled.log2.txt" {
				t.Fatal("unexpected scratch file")
			}
			for _, secret := range secrets {
				if bytes.Contains(data, secret) {
					t.Fatal("credential bytes reached scratch")
				}
			}
			for _, marker := range []string{"privkey:", "PrivateNodeKey", "PrivateMachineKey", "BEARER-CANARY"} {
				if bytes.Contains(data, []byte(marker)) {
					t.Fatal("private state reached scratch")
				}
			}
			return nil
		})
		if err != nil || files == 0 {
			t.Fatal("real production log scratch not observed")
		}
	}
	check()
	cancelManaged()
	if !s.engine.closeServer() {
		t.Fatal("tsnet stop failed")
	}
	check() // final logger flush must not introduce credentials either
	if s.Close() != nil {
		t.Fatal("session teardown failed")
	}
	if _, err := os.Lstat(s.dir); !os.IsNotExist(err) {
		t.Fatal("session scratch survived teardown")
	}
	fmt.Fprintln(os.Stdout, "MEMORY_KEYS_LOG_SCAN_AND_TEARDOWN_OK")
}

func TestHomeSessionScratchCustodyAndLifetime(t *testing.T) {
	ca := makeHomeCA(t, true)
	relay := derp.NewServer(key.NewNode(), func(string, ...any) {})
	defer relay.Close()
	region := &tailcfg.DERPRegion{RegionID: 1, RegionCode: "home", Nodes: []*tailcfg.DERPNode{{Name: "home", RegionID: 1, HostName: homeControlName, IPv4: "192.168.1.40", IPv6: "none", DERPPort: 28443, STUNPort: -1}}}
	control := &testcontrol.Server{Logf: func(string, ...any) {}, DERPMap: &tailcfg.DERPMap{Regions: map[int]*tailcfg.DERPRegion{1: region}}, RequireAuthKey: strings.Repeat("ab", 24)}
	var requests atomic.Int32
	var holdEnrollment atomic.Bool
	enrollment := make(chan struct{}, 1)
	upstream := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requests.Add(1)
		if holdEnrollment.Load() && r.URL.Path == "/key" {
			select {
			case enrollment <- struct{}{}:
			default:
			}
			<-r.Context().Done()
			return
		}
		if r.URL.Path == "/derp/latency-check" {
			w.WriteHeader(200)
			return
		}
		if r.URL.Path == "/derp" {
			derphttp.Handler(relay).ServeHTTP(w, r)
			return
		}
		control.ServeHTTP(w, r)
	}))
	control.HTTPTestServer = upstream
	upstream.Config.ErrorLog = log.New(io.Discard, "", 0)
	upstream.TLS = &tls.Config{Certificates: []tls.Certificate{homeLeaf(t, ca, "valid")}}
	upstream.StartTLS()
	defer upstream.Close()
	var saved []byte
	var savedNode string
	var savedCount int
	for _, mode := range []string{"custody", "save", "resume", "changed-identity", "eof", "signal", "error", "eof-enrolling", "signal-enrolling"} {
		t.Run(mode, func(t *testing.T) {
			holdEnrollment.Store(strings.HasSuffix(mode, "-enrolling"))
			// testcontrol requires a join key even on re-registration by default.
			// Allow its existing registered node to reconnect without a new key.
			control.RequireAuthKey = strings.Repeat("ab", 24)
			if mode == "resume" || mode == "changed-identity" {
				control.RequireAuthKey = ""
			}
			runtime, err := filepath.EvalSymlinks(t.TempDir())
			if err != nil {
				t.Fatal("canonical runtime setup failed")
			}
			os.Chmod(runtime, 0700)
			// Short private socket path stays below the Unix socket length limit.
			sockets, err := os.MkdirTemp("/tmp", "home-session-socket-")
			if err != nil {
				t.Fatal("socket directory failed")
			}
			defer os.RemoveAll(sockets)
			ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestHomeSessionProcessChild$")
			cmd.Env = []string{"HOME_SESSION_TEST_MODE=" + mode, "XDG_RUNTIME_DIR=" + runtime, "HOME_SESSION_TEST_UPSTREAM=" + strings.TrimPrefix(upstream.URL, "https://"), "HOME_SESSION_TEST_SOCKET=" + filepath.Join(sockets, "runtime.sock")}
			in, err := cmd.StdinPipe()
			if err != nil {
				t.Fatal("child input failed")
			}
			var out bytes.Buffer
			cmd.Stdout, cmd.Stderr = &out, &out
			var output io.ReadCloser
			if mode == "save" || mode == "resume" || mode == "changed-identity" {
				cmd.Stdout = nil
				output, err = cmd.StdoutPipe()
				if err != nil {
					t.Fatal("private output pipe failed")
				}
			}
			if cmd.Start() != nil {
				t.Fatal("child failed to start")
			}
			defer in.Close()
			defer func() {
				if cmd.ProcessState == nil {
					_ = cmd.Process.Kill()
					_ = cmd.Wait()
				}
			}()
			payload := sessionPayload(t, ca.der)
			if output != nil {
				var fields map[string]any
				json.Unmarshal(payload, &fields)
				fields["version"], fields["saveIntent"], fields["stableNodeID"] = 3, true, ""
				if mode != "save" {
					delete(fields, "joinKey")
					fields["networkState"], fields["stableNodeID"] = saved, savedNode
				}
				if mode == "changed-identity" {
					// Keep machine/node keys intact but supply another expected
					// identity consistently in all saved metadata. Live Self
					// still authenticates the original node and must refuse.
					var state homeStateEnvelope
					json.Unmarshal(saved, &state)
					state.StableNodeID = "expected-other-node"
					for name, value := range state.Store {
						state.Store[name] = bytes.ReplaceAll(value, []byte(savedNode), []byte(state.StableNodeID))
					}
					fields["networkState"], _ = json.Marshal(state)
					fields["stableNodeID"] = state.StableNodeID
				}
				payload, _ = json.Marshal(fields)
			}
			if _, err := in.Write(sessionFrame(t, payload)); err != nil {
				t.Fatal("launch delivery failed")
			}
			if output != nil {
				reader := bufio.NewReader(output)
				ready, err := reader.ReadString('\n')
				if mode == "changed-identity" {
					if err != nil || ready != "-22\n" {
						if regexp.MustCompile(`^-?[0-9]+\n$`).MatchString(ready) {
							t.Log("closed helper category", strings.TrimSpace(ready))
						}
						t.Fatal("live replacement identity was not distinctly refused")
					}
				} else {
					if err != nil || !strings.HasPrefix(ready, "0 ") {
						if regexp.MustCompile(`^-?[0-9]+\n$`).MatchString(ready) {
							t.Log("closed helper category", strings.TrimSpace(ready))
						}
						t.Fatal("saved session readiness unavailable")
					}
					node := strings.TrimSuffix(strings.TrimPrefix(ready, "0 "), "\n")
					var size uint32
					if binary.Read(reader, binary.BigEndian, &size) != nil || size == 0 || size > homeSessionStateLimit {
						t.Fatal("bounded state frame missing")
					}
					state := make([]byte, size)
					if _, err := io.ReadFull(reader, state); err != nil {
						t.Fatal("state frame truncated")
					}
					values, err := restoreHomeState(state, "synthetic-control", node)
					if err != nil {
						t.Fatal("export is not complete restorable state")
					}
					if mode == "save" {
						if cmd.Process.Signal(syscall.SIGUSR1) != nil {
							t.Fatal("later write trigger failed")
						}
						for attempt := 0; ; attempt++ {
							if attempt > 8 {
								t.Fatal("later store change was not exported")
							}
							if binary.Read(reader, binary.BigEndian, &size) != nil || size == 0 || size > homeSessionStateLimit {
								t.Fatal("later bounded frame missing")
							}
							state = make([]byte, size)
							if _, err := io.ReadFull(reader, state); err != nil {
								t.Fatal("later state truncated")
							}
							values, err = restoreHomeState(state, "synthetic-control", node)
							if err != nil {
								t.Fatal("later state incomplete")
							}
							if string(values["later-test-state"]) == "changed" {
								break
							}
						}
						saved, savedNode, savedCount = state, node, control.NumNodes()
					} else if node != savedNode || control.NumNodes() != savedCount {
						t.Fatal("resume replaced StableNodeID")
					}
					// Actual machine/node state must never reach scratch.
					filepath.WalkDir(runtime, func(path string, entry os.DirEntry, err error) error {
						if err != nil {
							t.Fatal("scratch observation failed")
						}
						if !entry.Type().IsRegular() {
							return nil
						}
						data, err := os.ReadFile(path)
						if err != nil {
							t.Fatal("scratch read failed")
						}
						if bytes.Contains(data, []byte("privkey:")) || bytes.Contains(data, values["_machinekey"]) {
							t.Fatal("saved credentials reached disk")
						}
						return nil
					})
				}
				in.Close()
				// Drain subsequent complete snapshots, retaining none in logs.
				go io.Copy(io.Discard, reader)
			}
			if strings.HasSuffix(mode, "-enrolling") {
				select {
				case <-enrollment:
				case <-time.After(8 * time.Second):
					cmd.Process.Kill()
					cmd.Wait()
					t.Fatal("enrollment did not start")
				}
				if mode == "eof-enrolling" {
					in.Close()
				} else {
					cmd.Process.Signal(syscall.SIGTERM)
				}
			}
			if mode == "eof" || mode == "signal" {
				deadline := time.Now().Add(10 * time.Second)
				for {
					if _, err := os.Lstat(filepath.Join(sockets, "runtime.sock")); err == nil {
						break
					}
					if time.Now().After(deadline) {
						cmd.Process.Kill()
						cmd.Wait()
						t.Fatal("session did not reach runtime listener; child output withheld")
					}
					time.Sleep(10 * time.Millisecond)
				}
				if mode == "eof" {
					in.Close()
				} else {
					cmd.Process.Signal(syscall.SIGTERM)
				}
			}
			if err := cmd.Wait(); err != nil {
				for _, reason := range []string{"synthetic membership failed", "missing peer was not refused", "local socket unavailable", "real production log scratch not observed", "credential bytes reached scratch", "unexpected scratch file", "session scratch survived teardown", "serve left scratch", "session credentials are not memory-only"} {
					if bytes.Contains(out.Bytes(), []byte(reason)) {
						t.Log(reason)
					}
				}
				t.Logf("synthetic control requests=%d", requests.Load())
				t.Fatalf("session child failed; output withheld; exit=%d", cmd.ProcessState.ExitCode())
			}
			in.Close()
			if mode == "custody" && !bytes.Contains(out.Bytes(), []byte("MEMORY_KEYS_LOG_SCAN_AND_TEARDOWN_OK")) {
				t.Fatal("custody receipt missing")
			}
			for _, secret := range []string{strings.Repeat("ab", 24), "BEARER-CANARY", "privkey:"} {
				if bytes.Contains(out.Bytes(), []byte(secret)) {
					t.Fatal("secret reached child output")
				}
			}
			entries, err := os.ReadDir(runtime)
			if err != nil || len(entries) != 0 {
				t.Fatal("session left runtime scratch")
			}
			t.Log("real tsnet session: teardown passed for", mode)
			if mode == "custody" {
				t.Log("production log scan: join key, generated machine/node private keys and socket bearer canary absent")
			}
		})
	}
}

// Test-side diagnostics emit stage/function names only, never arguments or state.
// Sampling is requested only if cancellation has already taken one second,
// so ordinary enrollment and shutdown scheduling remain unperturbed.
func observeHomeShutdown(ctx context.Context, s *homeSession) func() {
	done, stopped := make(chan struct{}), make(chan struct{})
	probe := make(chan os.Signal, 1)
	signal.Notify(probe, syscall.SIGUSR2)
	start := time.Now()
	handled := context.AfterFunc(ctx, func() {
		fmt.Fprintf(os.Stderr, "shutdown-stage signal-handled elapsed=%s\n", time.Since(start).Round(time.Millisecond))
	})
	cancelled := context.AfterFunc(s.engine.ctx, func() {
		fmt.Fprintf(os.Stderr, "shutdown-stage engine-cancelled elapsed=%s\n", time.Since(start).Round(time.Millisecond))
	})
	go func() {
		defer close(stopped)
		select {
		case <-done:
			return
		case <-probe:
		}
		stage := "signal-pending"
		if ctx.Err() != nil {
			stage = "signal-handled"
		}
		if s.engine.ctx.Err() != nil {
			stage += " engine-cancelled"
		}
		stack := make([]byte, 256*1024)
		frames := string(stack[:runtime.Stack(stack, true)])
		for _, item := range []struct{ frame, label string }{
			{"(*homeSession).exportState(", "snapshot-writer"},
			{"(*homeTransport).Close(", "transport-close"},
			{"tailscale.com/tsnet.(*Server).Close(", "tsnet-close"},
			{"net/http.(*Server).Shutdown(", "http-shutdown"},
			{"tailscale.com/ipn/ipnlocal.(*LocalBackend).Shutdown(", "backend-shutdown"},
			{"tailscale.com/wgengine.(*userspaceEngine).Close(", "engine-close"},
			{"os.RemoveAll(", "scratch-removal"},
		} {
			if strings.Contains(frames, item.frame) {
				stage += " " + item.label
			}
		}
		fmt.Fprintf(os.Stderr, "shutdown-stage delayed elapsed=%s %s\n", time.Since(start).Round(time.Millisecond), stage)
		// Only cleanup and control-worker function names are retained.
		// Omit argument values, paths and all unrelated goroutines.
		for _, goroutine := range strings.Split(frames, "\n\n") {
			if !strings.Contains(goroutine, "tailscale.com/tsnet.(*Server).Close(") &&
				!strings.Contains(goroutine, "tailscale.com/control/controlclient.(*Auto).authRoutine(") &&
				!strings.Contains(goroutine, "tailscale.com/control/controlclient.(*Auto).mapRoutine(") &&
				!strings.Contains(goroutine, "tailscale.com/control/controlclient.(*Auto).updateRoutine(") &&
				!strings.Contains(goroutine, "tailscale.com/control/controlclient.(*NoiseClient).dial(") {
				continue
			}
			count := 0
			lines := strings.Split(goroutine, "\n")
			for index, line := range lines {
				if strings.HasPrefix(line, "\t") || strings.HasPrefix(line, "goroutine ") {
					continue
				}
				if i := strings.LastIndex(line, "("); i >= 0 && count < 20 {
					location := ""
					if index+1 < len(lines) {
						fields := strings.Fields(lines[index+1])
						if len(fields) > 0 {
							location = filepath.Base(fields[0])
						}
					}
					fmt.Fprintln(os.Stderr, "shutdown-function", line[:i], location)
					count++
				}
			}
		}
	}()
	return func() { handled(); cancelled(); signal.Stop(probe); close(done); <-stopped }
}
