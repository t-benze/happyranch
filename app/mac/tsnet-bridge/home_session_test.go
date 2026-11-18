package main

import (
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
	root := t.TempDir()
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
	transport, err := prepareHomeProcess(cfg)
	if err != nil {
		s.Close()
		t.Fatal("transport refused")
	}
	// Existing transport tests use the same test-side endpoint isolation; the
	// production constructor's fixed RFC1918 target is checked independently.
	if transport.upstream != "192.168.1.40:8443" {
		t.Fatal("unexpected production endpoint")
	}
	transport.upstream = os.Getenv("HOME_SESSION_TEST_UPSTREAM")
	server := s.engine.server
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT, syscall.SIGHUP, syscall.SIGQUIT)
	defer stop()
	if mode != "custody" {
		cfg.SocketPath = os.Getenv("HOME_SESSION_TEST_SOCKET")
		if mode == "error" {
			cfg.SocketPath = "relative-refused"
		}
		code := s.serve(ctx, os.Stdin, os.Stdout, transport, cfg)
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
	region := &tailcfg.DERPRegion{RegionID: 1, RegionCode: "home", Nodes: []*tailcfg.DERPNode{{Name: "home", RegionID: 1, HostName: homeControlName, IPv4: "192.168.1.40", IPv6: "none", DERPPort: 8443, STUNPort: -1}}}
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
	for _, mode := range []string{"custody", "eof", "signal", "error", "eof-enrolling", "signal-enrolling"} {
		t.Run(mode, func(t *testing.T) {
			holdEnrollment.Store(strings.HasSuffix(mode, "-enrolling"))
			runtime := t.TempDir()
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
			if cmd.Start() != nil {
				t.Fatal("child failed to start")
			}
			if _, err := in.Write(sessionFrame(t, sessionPayload(t, ca.der))); err != nil {
				t.Fatal("launch delivery failed")
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
				for _, reason := range []string{"credential bytes reached scratch", "unexpected scratch file", "session scratch survived teardown", "serve left scratch", "session credentials are not memory-only"} {
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
