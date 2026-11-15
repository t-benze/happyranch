package sidecar

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/netip"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"syscall"
	"testing"
	"time"

	"happyranch/tsnethealth"
	"tailscale.com/client/tailscale"
	"tailscale.com/ipn/ipnstate"
	"tailscale.com/tailcfg"
	"tailscale.com/types/key"
)

type fakeHealth struct {
	status       *ipnstate.Status
	statuses     []*ipnstate.Status
	calls, pings int
	block        chan struct{}
}

func (f *fakeHealth) Status(ctx context.Context) (*ipnstate.Status, error) {
	if f.block != nil {
		close(f.block)
		<-ctx.Done()
		return nil, errors.New("RAW-error-198.51.100.7")
	}
	s := f.status
	if f.calls < len(f.statuses) {
		s = f.statuses[f.calls]
	}
	f.calls++
	return s, nil
}
func (*fakeHealth) CurrentDERPMap(context.Context) (*tailcfg.DERPMap, error) { return nil, nil }
func (f *fakeHealth) PingWithOpts(ctx context.Context, ip netip.Addr, kind tailcfg.PingType, opts tailscale.PingOpts) (*ipnstate.PingResult, error) {
	f.pings++
	deadline, ok := ctx.Deadline()
	if !ok || time.Until(deadline) > 3*time.Second || kind != tailcfg.PingDisco || opts != (tailscale.PingOpts{}) {
		return nil, errors.New("wrong ping contract")
	}
	return &ipnstate.PingResult{IP: ip.String(), Endpoint: "198.51.100.7:443", LatencySeconds: 0.012}, nil
}
func healthStatus(online bool, addr, backend string) *ipnstate.Status {
	k := key.NewNode().Public()
	return &ipnstate.Status{BackendState: backend, AuthURL: "AUTH-URL-SENTINEL", Health: []string{"RAW-HEALTH-SENTINEL https://private.invalid"}, Peer: map[key.NodePublic]*ipnstate.PeerStatus{
		k:                      {HostName: "expected-mac", DNSName: "expected.tail.invalid.", Online: online, CurAddr: addr, TailscaleIPs: []netip.Addr{netip.MustParseAddr("100.64.0.2")}},
		key.NewNode().Public(): {HostName: "OTHER-PEER-SENTINEL", CurAddr: "203.0.113.9:42"},
	}}
}

type healthLines chan string

type timedHealthLines struct {
	healthLines
	at    *time.Time
	times []time.Time
}

func (w *timedHealthLines) Write(b []byte) (int, error) {
	w.times = append(w.times, *w.at)
	return w.healthLines.Write(b)
}

func (c healthLines) Write(b []byte) (int, error) { c <- string(b); return len(b), nil }
func takeHealth(t *testing.T, lines healthLines, trigger, path string) {
	t.Helper()
	select {
	case line := <-lines:
		var r struct {
			Trigger string
			Peer    struct{ Name, Path string }
		}
		if !strings.HasPrefix(line, "tsnet-health ") || strings.Count(line, "\n") != 1 || json.Unmarshal([]byte(strings.TrimPrefix(line, "tsnet-health ")), &r) != nil || r.Trigger != trigger || r.Peer.Path != path || r.Peer.Name != "expected-mac" {
			t.Fatalf("unexpected journal record: %s", line)
		}
	case <-time.After(time.Second):
		t.Fatal("missing health record")
	}
}

func TestHealthCadenceAndChanges(t *testing.T) {
	at := time.Date(2026, 11, 15, 0, 0, 0, 0, time.UTC)
	up := healthStatus(true, "198.51.100.7:443", "Running")
	// Preserve node identity while changing local status snapshots.
	down := *up
	down.Peer = map[key.NodePublic]*ipnstate.PeerStatus{}
	for k, p := range up.Peer {
		q := *p
		if q.HostName == "expected-mac" {
			q.Online = false
		}
		down.Peer[k] = &q
	}
	rerouted := *up
	rerouted.Peer = map[key.NodePublic]*ipnstate.PeerStatus{}
	for k, p := range up.Peer {
		q := *p
		if q.HostName == "expected-mac" {
			q.CurAddr = ""
		}
		rerouted.Peer[k] = &q
	}
	backend := rerouted
	backend.BackendState = "Stopped"
	f := &fakeHealth{status: &backend, statuses: []*ipnstate.Status{up, up, &down, up, up, &rerouted, &backend, &backend, &backend}}
	lines := make(healthLines, 16)
	ticks := make(chan time.Time)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	writer := &timedHealthLines{healthLines: lines}
	m := healthMonitor{client: f, expected: "expected.tail.invalid", out: writer, at: at}
	writer.at = &m.at
	done := make(chan struct{})
	go func() { m.run(ctx, ticks); close(done) }()
	takeHealth(t, lines, "join", "direct")
	ticks <- at.Add(time.Second)
	ticks <- at.Add(2 * time.Second)
	takeHealth(t, lines, "change", "none")
	ticks <- at.Add(3 * time.Second)
	ticks <- at.Add(32 * time.Second)
	takeHealth(t, lines, "change", "direct")
	ticks <- at.Add(62 * time.Second)
	takeHealth(t, lines, "change", "none")
	ticks <- at.Add(92 * time.Second)
	takeHealth(t, lines, "change", "none")
	ticks <- at.Add(5*time.Minute - time.Second)
	ticks <- at.Add(5 * time.Minute)
	takeHealth(t, lines, "periodic", "direct")
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("loop did not stop")
	}
	wantTimes := []time.Duration{0, 2 * time.Second, 32 * time.Second, 62 * time.Second, 92 * time.Second, 5 * time.Minute}
	if len(writer.times) != len(wantTimes) {
		t.Fatalf("emissions=%v", writer.times)
	}
	for i, d := range wantTimes {
		if writer.times[i] != at.Add(d) {
			t.Fatalf("record %d emitted at %v, want %v", i, writer.times[i], at.Add(d))
		}
	}
	if len(lines) != 0 || f.pings != 2 || f.calls != 9 {
		t.Fatalf("extra record/ping or wrong cadence: lines=%d pings=%d calls=%d", len(lines), f.pings, f.calls)
	}
}

func TestHealthJournalPrivacyAndMissingPeer(t *testing.T) {
	for _, expected := range []string{"expected-mac", "missing-mac"} {
		t.Run(expected, func(t *testing.T) {
			f := &fakeHealth{status: healthStatus(true, "198.51.100.7:443", "Running")}
			runtimeDir := t.TempDir()
			t.Setenv("RUNTIME_DIRECTORY", runtimeDir)
			if expected == "missing-mac" {
				t.Setenv("RUNTIME_DIRECTORY", "")
			}
			var out bytes.Buffer
			m := healthMonitor{client: f, expected: expected, dir: os.Getenv("RUNTIME_DIRECTORY"), out: &out, at: time.Now()}
			m.runner = tsnethealth.NewRunner(func() time.Time { return m.at })
			m.sample(context.Background(), "join")
			line := out.String()
			raw := strings.TrimSuffix(strings.TrimPrefix(line, "tsnet-health "), "\n")
			var record map[string]any
			if json.Unmarshal([]byte(raw), &record) != nil || len(raw) > 2048 || strings.ContainsAny(raw, "\n\r") {
				t.Fatalf("invalid compact record: %s", line)
			}
			compact := new(bytes.Buffer)
			if json.Compact(compact, []byte(raw)) != nil || compact.String() != raw {
				t.Fatal("noncompact journal")
			}
			sentinels := []string{"AUTH-URL-SENTINEL", "RAW-HEALTH-SENTINEL", "private.invalid", "OTHER-PEER-SENTINEL", "198.51.100.7", "203.0.113.9", "100.64.0.2"}
			for k := range f.status.Peer {
				sentinels = append(sentinels, k.String())
			}
			for _, s := range sentinels {
				if strings.Contains(line, s) {
					t.Fatalf("leaked sentinel %s", s)
				}
			}
			file, err := os.ReadFile(filepath.Join(runtimeDir, "health.json"))
			if expected == "expected-mac" && (err != nil || string(file) != raw) {
				t.Fatal("file does not match journal record")
			}
			if expected == "missing-mac" && !os.IsNotExist(err) {
				t.Fatal("unset runtime directory created a file")
			}
			peer := record["peer"].(map[string]any)
			if expected == "missing-mac" && (peer["name"] != "" || peer["online"] != false || peer["path"] != "none" || f.pings != 0) {
				t.Fatalf("missing-peer placeholder: %v", peer)
			}
		})
	}
}

func TestHealthCancellation(t *testing.T) {
	f := &fakeHealth{block: make(chan struct{})}
	ctx, cancel := context.WithCancel(context.Background())
	var out bytes.Buffer
	m := healthMonitor{client: f, expected: "expected-mac", out: &out, at: time.Now()}
	done := make(chan struct{})
	go func() { m.run(ctx, make(chan time.Time)); close(done) }()
	<-f.block
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("blocked LocalAPI was not cancelled")
	}
	if out.Len() != 0 {
		t.Fatal("cancelled sample emitted data")
	}
}

func TestHealthFile(t *testing.T) {
	dir := t.TempDir()
	target := filepath.Join(dir, "health.json")
	original := syscall.Umask(0077)
	defer syscall.Umask(original)
	if err := writeHealthFile(dir, []byte(`{"v":1}`)); err != nil {
		t.Fatal(err)
	}
	old, err := os.Open(target)
	if err != nil {
		t.Fatal(err)
	}
	defer old.Close()
	if err := writeHealthFile(dir, []byte(`{"v":2}`)); err != nil {
		t.Fatal(err)
	}
	oldBytes := make([]byte, 7)
	if _, err := old.Read(oldBytes); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(target)
	if err != nil {
		t.Fatal(err)
	}
	st, err := os.Stat(target)
	if err != nil {
		t.Fatal(err)
	}
	if string(oldBytes) != `{"v":1}` || string(data) != `{"v":2}` || st.Mode().Perm() != 0644 {
		t.Fatal("non-atomic replacement or umask changed mode")
	}
	entries, err := os.ReadDir(dir)
	if err != nil || len(entries) != 1 {
		t.Fatal("temporary file left behind")
	}
	if _, err := os.Lstat("health.json"); !os.IsNotExist(err) {
		t.Fatal("journal-only precondition: local health.json exists")
	}
	if err := writeHealthFile("", []byte("ignored")); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Lstat("health.json"); !os.IsNotExist(err) {
		t.Fatal("empty runtime directory created a file")
	}
	for _, kind := range []string{"symlink", "dangling-symlink", "directory", "fifo"} {
		t.Run(kind, func(t *testing.T) {
			d := t.TempDir()
			p := filepath.Join(d, "health.json")
			var err error
			switch kind {
			case "symlink":
				err = os.Symlink(target, p)
			case "dangling-symlink":
				err = os.Symlink(filepath.Join(d, "missing"), p)
			case "directory":
				err = os.Mkdir(p, 0700)
			case "fifo":
				err = syscall.Mkfifo(p, 0600)
			}
			if err != nil {
				t.Fatal(err)
			}
			if writeHealthFile(d, []byte("overwrite")) == nil {
				t.Fatal("unsafe target accepted")
			}
			data, err := os.ReadFile(target)
			if err != nil || string(data) != `{"v":2}` {
				t.Fatal("target followed or changed")
			}
		})
	}
}

type healthEngine struct {
	*fakeEngine
	client healthClient
}

func (e *healthEngine) healthClient() (healthClient, error) { return e.client, nil }

func TestHealthStartupAndStop(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("Linux-only monitor wiring")
	}
	t.Setenv("RUNTIME_DIRECTORY", "")
	cfg := validConfig(t)
	events := []string{}
	listener := &oneListener{blockAfter: true, closed: make(chan struct{}), accepted: make(chan struct{}), events: &events}
	f := &fakeHealth{block: make(chan struct{})}
	e := &healthEngine{fakeEngine: &fakeEngine{listener: listener, receipt: RedemptionReceipt{true, true, true}, events: &events}, client: f}
	a, b := net.Pipe()
	defer b.Close()
	svc := New(cfg, e, dialFunc(func(context.Context, string, string) (net.Conn, error) { return a, nil }))
	t.Cleanup(func() { _ = svc.Stop() })
	if err := svc.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	select {
	case <-f.block:
	case <-time.After(time.Second):
		t.Fatal("successful startup did not launch health")
	}
	stopped := make(chan struct{})
	go func() { _ = svc.Stop(); close(stopped) }()
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("health delayed transport shutdown")
	}
	if _, err := os.Stat(filepath.Join(cfg.StateDir, "health.json")); !os.IsNotExist(err) {
		t.Fatal("unset runtime directory wrote state file")
	}
}
