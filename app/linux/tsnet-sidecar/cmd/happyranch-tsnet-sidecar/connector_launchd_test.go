//go:build linux || darwin

package main

import (
	"context"
	"net"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	sidecar "happyranch/linux-tsnet-sidecar"
)

func launchdCommandService(t *testing.T, e *commandEngine) (*sidecar.Sidecar, net.Listener) {
	t.Helper()
	connector, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { connector.Close() })
	go func() {
		for {
			conn, err := connector.Accept()
			if err != nil {
				return
			}
			conn.Close()
		}
	}()
	cfg := configForCommand(t)
	cfg.ConnectorAddr = connector.Addr().String()
	e.listener, err = net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { e.listener.Close() })
	svc := sidecar.New(cfg, e, &net.Dialer{})
	t.Cleanup(func() { svc.Stop() })
	return svc, connector
}

func TestLaunchdConnectorReachability(t *testing.T) {
	for _, scenario := range []string{"healthy", "connector-loss", "startup-absent"} {
		t.Run(scenario, func(t *testing.T) {
			e := &commandEngine{}
			svc, connector := launchdCommandService(t, e)
			if scenario == "startup-absent" {
				connector.Close()
			}
			path := filepath.Join(t.TempDir(), "state", "readiness.json")
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			result := make(chan int, 1)
			go func() { result <- serveLaunchd(ctx, svc, path, 8*time.Second, connector.Addr().String()) }()
			wantCode, wantReason := 1, "waiting-for-readiness"
			if scenario != "startup-absent" {
				first := awaitReadiness(t, path, func(r map[string]any) bool { return r["ready"] == true })
				if scenario == "healthy" {
					// Remain ready across two maximum connector-probe periods.
					time.Sleep(2200 * time.Millisecond)
					next := readReadiness(t, path)
					if next["ready"] != true || next["reason"] != "healthy" || next["sequence"].(float64) <= first["sequence"].(float64) {
						t.Fatalf("reachable connector lost readiness: %v", next)
					}
					wantCode, wantReason = 0, "stopping"
					cancel()
				} else {
					connector.Close()
					wantReason = "readiness-or-listener-lost"
				}
			}
			started := time.Now()
			select {
			case code := <-result:
				if code != wantCode || svc.Running() || !e.closed.Load() {
					t.Fatalf("exit=%d want=%d running=%v engineClosed=%v", code, wantCode, svc.Running(), e.closed.Load())
				}
			case <-time.After(1750 * time.Millisecond):
				// 1s cadence + 250ms dial + 500ms scheduling/publication margin.
				cancel()
				<-result
				t.Fatal("connector loss/startup did not exit within 1.25s + 500ms test margin")
			}
			stopped := readReadiness(t, path)
			if stopped["ready"] != false || stopped["reason"] != wantReason {
				t.Fatalf("final readiness=%v", stopped)
			}
			if scenario == "connector-loss" && stopped["last_healthy_wall_time"] == nil {
				t.Fatal("loss discarded prior healthy observation")
			}
			t.Logf("%s exit within %s; record=%v", scenario, time.Since(started), stopped)
			// Exercise the shipping C3 parser, with no copied schema or reader changes.
			readerCtx, readerCancel := context.WithTimeout(context.Background(), 5*time.Second)
			defer readerCancel()
			reader := exec.CommandContext(readerCtx, "python3", "-B", "-c", `
import sys
from pathlib import Path
from runtime.remote_access.launchd_readiness import _read_record
state, record = _read_record(Path(sys.argv[1]))
assert state == "valid", state
assert record["ready"] is False and record["reason"] == sys.argv[2], record
`, path, wantReason)
			reader.Dir = filepath.Join("..", "..", "..", "..", "..")
			if output, err := reader.CombinedOutput(); err != nil {
				t.Fatalf("unchanged Python reader rejected record: %v %s", err, output)
			}
		})
	}
}
