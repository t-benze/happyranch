package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"encoding/binary"
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"golang.org/x/sys/unix"
	"tailscale.com/derp"
	"tailscale.com/derp/derphttp"
	"tailscale.com/tailcfg"
	"tailscale.com/tstest/integration/testcontrol"
	"tailscale.com/types/key"
)

func TestHomeSessionBackpressuredCancel(t *testing.T) {
	ca := makeHomeCA(t, true)
	relay := derp.NewServer(key.NewNode(), func(string, ...any) {})
	defer relay.Close()
	region := &tailcfg.DERPRegion{RegionID: 1, RegionCode: "home", Nodes: []*tailcfg.DERPNode{{Name: "home", RegionID: 1, HostName: homeControlName, IPv4: "192.168.1.40", IPv6: "none", DERPPort: 8443, STUNPort: -1}}}
	control := &testcontrol.Server{Logf: func(string, ...any) {}, DERPMap: &tailcfg.DERPMap{Regions: map[int]*tailcfg.DERPRegion{1: region}}, RequireAuthKey: strings.Repeat("ab", 24)}
	upstream := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/derp/latency-check" {
			w.WriteHeader(200)
		} else if r.URL.Path == "/derp" {
			derphttp.Handler(relay).ServeHTTP(w, r)
		} else {
			control.ServeHTTP(w, r)
		}
	}))
	control.HTTPTestServer = upstream
	upstream.Config.ErrorLog = log.New(io.Discard, "", 0)
	upstream.TLS = &tls.Config{Certificates: []tls.Certificate{homeLeaf(t, ca, "valid")}}
	upstream.StartTLS()
	defer upstream.Close()
	for _, mode := range []string{"eof", "signal"} {
		t.Run(mode, func(t *testing.T) {
			runtime, err := os.MkdirTemp("/tmp", "home-session-blocked-")
			if err != nil {
				t.Fatal("runtime setup failed")
			}
			defer os.RemoveAll(runtime)
			ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			defer cancel()
			cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestHomeSessionProcessChild$")
			cmd.Env = []string{"HOME_SESSION_TEST_MODE=backpressure", "XDG_RUNTIME_DIR=" + runtime,
				"HOME_SESSION_TEST_UPSTREAM=" + strings.TrimPrefix(upstream.URL, "https://"),
				"HOME_SESSION_TEST_SOCKET=" + filepath.Join(runtime, "runtime.sock")}
			in, err := cmd.StdinPipe()
			if err != nil {
				t.Fatal("child input failed")
			}
			defer in.Close()
			output, err := cmd.StdoutPipe()
			if err != nil {
				t.Fatal("child output failed")
			}
			defer output.Close()
			fd := output.(*os.File).Fd()
			capacity, err := unix.FcntlInt(fd, unix.F_SETPIPE_SZ, 4096)
			if err != nil {
				t.Fatal("pipe capacity setup failed")
			}
			var diagnostics bytes.Buffer
			cmd.Stderr = &diagnostics
			if cmd.Start() != nil {
				t.Fatal("child start failed")
			}
			defer func() {
				if cmd.ProcessState == nil {
					_ = cmd.Process.Kill()
					_ = cmd.Wait()
				}
			}()
			var fields map[string]any
			json.Unmarshal(sessionPayload(t, ca.der), &fields)
			fields["version"], fields["saveIntent"], fields["stableNodeID"] = 3, true, ""
			payload, _ := json.Marshal(fields)
			if _, err := in.Write(sessionFrame(t, payload)); err != nil {
				t.Fatal("launch delivery failed")
			}
			reader := bufio.NewReader(output)
			ready, err := reader.ReadString('\n')
			if err != nil || !strings.HasPrefix(ready, "0 ") {
				t.Fatal("membership readiness missing")
			}
			var size uint32
			if binary.Read(reader, binary.BigEndian, &size) != nil || int(size) <= capacity+reader.Buffered() {
				t.Fatal("snapshot cannot saturate pipe")
			}
			// Stop reading mid-frame, and observe a completely full kernel pipe.
			deadline := time.Now().Add(2 * time.Second)
			for {
				queued, err := unix.IoctlGetInt(int(fd), unix.TIOCINQ)
				if err != nil {
					t.Fatal("pipe observation failed")
				}
				if queued == capacity {
					break
				}
				if time.Now().After(deadline) {
					t.Fatal("snapshot did not saturate pipe")
				}
				time.Sleep(time.Millisecond)
			}
			cancelAt := time.Now()
			if mode == "eof" {
				in.Close()
			} else if cmd.Process.Signal(syscall.SIGTERM) != nil {
				t.Fatal("cancellation signal failed")
			}
			done := make(chan error, 1)
			go func() { done <- cmd.Wait() }()
			deadlineTimer := time.NewTimer(2 * time.Second)
			defer deadlineTimer.Stop()
			probe := time.NewTimer(time.Second)
			defer probe.Stop()
		wait:
			for {
				select {
				case err := <-done:
					if err != nil {
						t.Fatalf("cancelled frame failed after %s; %s", time.Since(cancelAt), diagnostics.String())
					}
					break wait
				case <-probe.C:
					_ = cmd.Process.Signal(syscall.SIGUSR2) // test-side stage probe only
				case <-deadlineTimer.C:
					_ = cmd.Process.Kill()
					<-done
					t.Fatalf("cancelled frame blocked cleanup until forced kill after %s; %s", time.Since(cancelAt), diagnostics.String())
				}
			}
			t.Logf("process-exit after %s; %s", time.Since(cancelAt), diagnostics.String())
			entries, err := os.ReadDir(runtime)
			if err != nil || len(entries) != 0 {
				t.Fatal("cancelled frame left runtime scratch")
			}
		})
	}
}
