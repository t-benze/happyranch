package main

// TASK-8466: real command-seam Q1-Q8 and real-consumer H1-H7 regression
// coverage for the managed N3 composite readiness path.
//
// Every observation in this file is made through the real exported seam
// (systemdSidecarState / systemdStopSidecar) against a per-case fail-closed
// systemctl fixture on PATH that accepts only the exact canonical argv and
// never forwards to the host manager.  The health child is this test binary
// re-executed as a helper process, so the real child health FD, the real
// canonical generation/sequence records, and real process termination and
// reaping are exercised.

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

const (
	hr8466ShowArgv = "show happyranch-tsnet-sidecar.service --property=ActiveState --property=SubState --property=Result --property=MainPID"
	hr8466StopArgv = "stop --no-block happyranch-tsnet-sidecar.service"
)

// TestConnectorHealthFixtureChild is the controlled health child.  It writes
// real canonical generation/sequence records over the inherited health FD and
// exposes an entered/released barrier so the tests prove ordering rather than
// sleeping.  It is a no-op during an ordinary `go test` run.
func TestConnectorHealthFixtureChild(t *testing.T) {
	if os.Getenv("HR8466_CHILD") != "1" {
		return
	}
	fd, _ := strconv.Atoi(os.Getenv("HAPPYRANCH_CHILD_HEALTH_FD"))
	out := os.NewFile(uintptr(fd), "health")
	generation := os.Getenv("HAPPYRANCH_CHILD_HEALTH_GENERATION")
	events := os.Getenv("HR8466_EVENTS")
	barrier := os.Getenv("HR8466_CHILD_BARRIER")
	exitPath := os.Getenv("HR8466_CHILD_EXIT")
	plan := readHR8466Lines(os.Getenv("HR8466_CHILD_PLAN"))

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGTERM, syscall.SIGINT)
	go func() {
		<-sig
		appendHR8466Event(events, "term")
		os.Exit(0)
	}()

	for i, state := range plan {
		if barrier != "" {
			appendHR8466Event(events, fmt.Sprintf("waiting:%d", i))
			if !waitHR8466Line(barrier, strconv.Itoa(i), 20*time.Second) {
				appendHR8466Event(events, fmt.Sprintf("barrier-timeout:%d", i))
				os.Exit(3)
			}
		}
		sequence := uint64(i + 1)
		if state == "malformed" {
			_, _ = out.WriteString("{malformed\n")
		} else {
			_, _ = out.WriteString(healthRecord(generation, sequence, state))
		}
		appendHR8466Event(events, fmt.Sprintf("emitted:%d:%d:%s", i, sequence, state))
	}
	for {
		if exitPath != "" {
			if _, err := os.Stat(exitPath); err == nil {
				appendHR8466Event(events, "child-exit")
				os.Exit(0)
			}
		}
		time.Sleep(2 * time.Millisecond)
	}
}

// ---------------------------------------------------------------------------
// File/env helpers shared by the test process and the child helper.
// ---------------------------------------------------------------------------

func appendHR8466Event(path, line string) {
	appendHR8466File(path, line+"\n")
}

func appendHR8466File(path, text string) {
	if path == "" {
		return
	}
	file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		return
	}
	_, _ = file.WriteString(text)
	_ = file.Close()
}

func writeHR8466File(path, text string) {
	if path == "" {
		return
	}
	_ = os.WriteFile(path, []byte(text), 0600)
}

func readHR8466File(path string) string {
	raw, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(raw))
}

func readHR8466Lines(path string) []string {
	raw := readHR8466File(path)
	if raw == "" {
		return nil
	}
	return strings.Split(raw, "\n")
}

func hr8466Events(path string) []string {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var lines []string
	for _, line := range strings.Split(string(raw), "\n") {
		if line != "" {
			lines = append(lines, line)
		}
	}
	return lines
}

func waitHR8466Line(path, value string, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for {
		for _, line := range hr8466Events(path) {
			if line == value {
				return true
			}
		}
		if time.Now().After(deadline) {
			return false
		}
		time.Sleep(time.Millisecond)
	}
}

func waitHR8466EventPrefix(t *testing.T, path, prefix string, timeout time.Duration) []string {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for {
		events := hr8466Events(path)
		for _, line := range events {
			if strings.HasPrefix(line, prefix) {
				return events
			}
		}
		if time.Now().After(deadline) {
			t.Fatalf("event prefix %q not observed within %s; events=%v", prefix, timeout, events)
		}
		time.Sleep(time.Millisecond)
	}
}

// waitHR8466EventPrefixBool is the goroutine-safe form (no testing.T calls).
func waitHR8466EventPrefixBool(path, prefix string, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for {
		for _, line := range hr8466Events(path) {
			if strings.HasPrefix(line, prefix) {
				return true
			}
		}
		if time.Now().After(deadline) {
			return false
		}
		time.Sleep(time.Millisecond)
	}
}

// hr8466Reply renders the exact systemctl stdout for a fixture state token
// (and a nonzero exit only for the "fail" token).
func hr8466Reply(state, order string) (string, int) {
	switch state {
	case "healthy":
		return hr8466HealthyReply(order), 0
	case "absent":
		return "ActiveState=inactive\nSubState=dead\nResult=success\nMainPID=0\n", 0
	case "unhealthy":
		return "ActiveState=failed\nSubState=failed\nResult=exit-code\nMainPID=42\n", 0
	case "exitcode":
		return "ActiveState=active\nSubState=running\nResult=exit-code\nMainPID=2\n", 0
	case "unknownstate":
		return "ActiveState=reloading\nSubState=start\nResult=success\nMainPID=2\n", 0
	case "piddiff":
		return "ActiveState=active\nSubState=running\nResult=success\nMainPID=-1\n", 0
	case "fail":
		return hr8466HealthyReply(order), 0
	case "garbage", "delay":
		return "garbage\n", 0
	}
	parts := strings.SplitN(state, "-", 2)
	if len(parts) != 2 {
		return "garbage\n", 0
	}
	kind, key := parts[0], parts[1]
	base := map[string]string{"ActiveState": "active", "SubState": "running", "Result": "success", "MainPID": "42"}
	names := []string{"ActiveState", "SubState", "Result", "MainPID"}
	switch kind {
	case "nosep":
		var out strings.Builder
		for _, name := range names {
			if name == key {
				out.WriteString(name + "\n")
			} else {
				out.WriteString(name + "=" + base[name] + "\n")
			}
		}
		return out.String(), 0
	case "missing":
		var out strings.Builder
		for _, name := range names {
			if name != key {
				out.WriteString(name + "=" + base[name] + "\n")
			}
		}
		return out.String(), 0
	case "empty":
		var out strings.Builder
		for _, name := range names {
			value := base[name]
			if name == key {
				value = ""
			}
			out.WriteString(name + "=" + value + "\n")
		}
		return out.String(), 0
	case "dupsame":
		var out strings.Builder
		for _, name := range names {
			out.WriteString(name + "=" + base[name] + "\n")
			if name == key {
				out.WriteString(name + "=" + base[name] + "\n")
			}
		}
		return out.String(), 0
	case "dupconflict":
		var out strings.Builder
		for _, name := range names {
			out.WriteString(name + "=" + base[name] + "\n")
			if name == key {
				out.WriteString(name + "=conflict\n")
			}
		}
		return out.String(), 0
	}
	return "garbage\n", 0
}

func hr8466HealthyReply(order string) string {
	base := map[string]string{"ActiveState": "active", "SubState": "running", "Result": "success", "MainPID": "42"}
	if order == "" {
		order = "ActiveState,SubState,Result,MainPID"
	}
	var out strings.Builder
	for _, name := range strings.Split(order, ",") {
		out.WriteString(name + "=" + base[name] + "\n")
	}
	return out.String()
}

// hr8466FixtureScript is the per-case fail-closed systemctl stand-in.  It
// accepts only the exact canonical show/stop argv, refuses to run when
// NOTIFY_SOCKET leaked, records every invocation, renders the reply bytes for
// the current state token, and can be told to hold the sidecar present.
const hr8466FixtureScript = `#!/bin/sh
if [ -n "$NOTIFY_SOCKET" ]; then echo notify_leak >> "$HR8466_LOG"; exit 9; fi
echo "$*" >> "$HR8466_LOG"
case "$1" in
  show)
    if [ "$*" != "show happyranch-tsnet-sidecar.service --property=ActiveState --property=SubState --property=Result --property=MainPID" ]; then echo bad_argv >> "$HR8466_EVENTS"; exit 96; fi
    echo $$ >> "$HR8466_ENTRY"
    echo "show:$(/bin/cat "$HR8466_STATE")" >> "$HR8466_EVENTS"
    if [ -f "$HR8466_DELAY" ]; then exec /bin/sleep 10; fi
    /bin/cat "$HR8466_REPLIES/$(/bin/cat "$HR8466_STATE")"
    if [ -f "$HR8466_FAIL" ]; then exit 2; fi
    exit 0 ;;
  stop)
    if [ "$*" != "stop --no-block happyranch-tsnet-sidecar.service" ]; then echo bad_argv >> "$HR8466_EVENTS"; exit 96; fi
    echo stop >> "$HR8466_EVENTS"
    if [ -f "$HR8466_FAILSTOP" ]; then echo stop-failed >> "$HR8466_EVENTS"; exit 1; fi
    echo stopped > "$HR8466_STOP"
    if [ ! -f "$HR8466_HOLD" ]; then echo absent > "$HR8466_STATE"; fi
    exit 0 ;;
  *) echo bad_argv >> "$HR8466_EVENTS"; exit 96 ;;
esac
`

// ---------------------------------------------------------------------------
// Per-case fixture harness.
// ---------------------------------------------------------------------------

type hr8466Fixture struct {
	t                                        *testing.T
	order                                    string
	state, events, entered, log, stop        string
	failStop, hold, plan, barrier, childExit string
	repliesDir, delay, fail                  string
}

func newHR8466Fixture(t *testing.T) *hr8466Fixture {
	t.Helper()
	dir := t.TempDir()
	replies := filepath.Join(dir, "replies")
	if err := os.Mkdir(replies, 0700); err != nil {
		t.Fatal(err)
	}
	f := &hr8466Fixture{
		t:          t,
		order:      "ActiveState,SubState,Result,MainPID",
		state:      filepath.Join(dir, "state"),
		events:     filepath.Join(dir, "events"),
		entered:    filepath.Join(dir, "entry"),
		log:        filepath.Join(dir, "log"),
		stop:       filepath.Join(dir, "stop"),
		failStop:   filepath.Join(dir, "fail-stop"),
		hold:       filepath.Join(dir, "hold"),
		plan:       filepath.Join(dir, "child-plan"),
		barrier:    filepath.Join(dir, "child-barrier"),
		childExit:  filepath.Join(dir, "child-exit"),
		repliesDir: replies,
		delay:      filepath.Join(dir, "delay"),
		fail:       filepath.Join(dir, "fail"),
	}
	if err := os.WriteFile(filepath.Join(dir, "systemctl"), []byte(hr8466FixtureScript), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)
	t.Setenv("HR8466_EVENTS", f.events)
	t.Setenv("HR8466_LOG", f.log)
	t.Setenv("HR8466_ENTRY", f.entered)
	t.Setenv("HR8466_STATE", f.state)
	t.Setenv("HR8466_REPLIES", f.repliesDir)
	t.Setenv("HR8466_DELAY", f.delay)
	t.Setenv("HR8466_FAIL", f.fail)
	t.Setenv("HR8466_STOP", f.stop)
	t.Setenv("HR8466_FAILSTOP", f.failStop)
	t.Setenv("HR8466_HOLD", f.hold)
	t.Setenv("HR8466_CHILD", "1")
	t.Setenv("HR8466_CHILD_PLAN", f.plan)
	t.Setenv("HR8466_CHILD_BARRIER", f.barrier)
	t.Setenv("HR8466_CHILD_EXIT", f.childExit)
	t.Setenv("NOTIFY_SOCKET", "/run/systemd/notify")
	// The stop handler rewrites the state token to "absent"; make sure the
	// matching reply exists before any stop can be observed.
	f.writeReply("absent")
	f.setState("healthy")
	return f
}

func (f *hr8466Fixture) writeReply(state string) {
	reply, _ := hr8466Reply(state, f.order)
	writeHR8466File(filepath.Join(f.repliesDir, state), reply)
}

func (f *hr8466Fixture) setState(state string) {
	f.writeReply(state)
	writeHR8466File(f.state, state)
	if state == "delay" {
		writeHR8466File(f.delay, "delay")
	} else {
		_ = os.Remove(f.delay)
	}
	if state == "fail" {
		writeHR8466File(f.fail, "fail")
	} else {
		_ = os.Remove(f.fail)
	}
}

func (f *hr8466Fixture) setOrder(order string) {
	f.order = order
}

func (f *hr8466Fixture) setPlan(states ...string) {
	writeHR8466File(f.plan, strings.Join(states, "\n"))
}

func (f *hr8466Fixture) holdPresent()        { writeHR8466File(f.hold, "hold") }
func (f *hr8466Fixture) requireStopFailure() { writeHR8466File(f.failStop, "fail") }
func (f *hr8466Fixture) releaseChild(i int)  { appendHR8466File(f.barrier, strconv.Itoa(i)+"\n") }

func (f *hr8466Fixture) releaseAll(count int) {
	for i := 0; i < count; i++ {
		f.releaseChild(i)
	}
}

func (f *hr8466Fixture) exitChild() { writeHR8466File(f.childExit, "exit") }

func (f *hr8466Fixture) waitChildWaiting(t *testing.T, i int) {
	t.Helper()
	waitHR8466EventPrefix(t, f.events, fmt.Sprintf("waiting:%d", i), 15*time.Second)
}

func (f *hr8466Fixture) waitChildEmitted(t *testing.T, i int) []string {
	t.Helper()
	return waitHR8466EventPrefix(t, f.events, fmt.Sprintf("emitted:%d:", i), 15*time.Second)
}

func (f *hr8466Fixture) waitStop(t *testing.T) []string {
	t.Helper()
	return waitHR8466EventPrefix(t, f.events, "stop", 10*time.Second)
}

func (f *hr8466Fixture) waitTerm(t *testing.T) []string {
	t.Helper()
	return waitHR8466EventPrefix(t, f.events, "term", 10*time.Second)
}

func hr8466EventCount(events []string, want string) int {
	count := 0
	for _, line := range events {
		if line == want {
			count++
		}
	}
	return count
}

func hr8466HasEvent(events []string, want string) bool {
	return hr8466EventCount(events, want) > 0
}

// assertHR8466NoFixtureLeak proves every real observation used the one
// authoritative named-property invocation: the fixture records a failing
// receipt if it is invoked with any other argv (including --value or a
// fallback form) or if NOTIFY_SOCKET leaked into the command environment.
func assertHR8466NoFixtureLeak(t *testing.T, f *hr8466Fixture) {
	t.Helper()
	for _, line := range hr8466Events(f.events) {
		if line == "notify_leak" || strings.HasPrefix(line, "bad_argv") {
			t.Fatalf("real consumer issued a non-canonical/leaking query: events=%v", hr8466Events(f.events))
		}
	}
	if log := readHR8466File(f.log); strings.Contains(log, "notify_leak") {
		t.Fatalf("NOTIFY_SOCKET leaked into a fixture command: %q", log)
	}
}

// ---------------------------------------------------------------------------
// Instrumented real-probe consumer harness.
// ---------------------------------------------------------------------------

type hr8466ProbeLog struct {
	mu         sync.Mutex
	results    []sidecarObservation
	background []bool
}

func (p *hr8466ProbeLog) probe(ctx context.Context) sidecarObservation {
	result := systemdSidecarState(ctx)
	p.mu.Lock()
	p.results = append(p.results, result)
	p.background = append(p.background, ctx.Done() == nil)
	p.mu.Unlock()
	return result
}

func (p *hr8466ProbeLog) count() int {
	p.mu.Lock()
	defer p.mu.Unlock()
	return len(p.results)
}

func (p *hr8466ProbeLog) sawDetachedContext() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	for _, detached := range p.background {
		if detached {
			return true
		}
	}
	return false
}

type hr8466Supervisor struct {
	t        *testing.T
	notifier *recordingNotifier
	probe    *hr8466ProbeLog
	stops    atomic.Int32
	cancel   context.CancelFunc
	done     chan int
	cmd      *exec.Cmd
	finished bool
	code     int
}

func startHR8466Supervisor(t *testing.T, startup, stale time.Duration) *hr8466Supervisor {
	t.Helper()
	h := &hr8466Supervisor{
		t:        t,
		notifier: &recordingNotifier{},
		probe:    &hr8466ProbeLog{},
		done:     make(chan int, 1),
	}
	ctx, cancel := context.WithCancel(context.Background())
	h.cancel = cancel
	started := make(chan *exec.Cmd, 1)
	argv := []string{os.Args[0], "-test.run=^TestConnectorHealthFixtureChild$"}
	go func() {
		h.done <- superviseConnector(ctx, argv, h.notifier, startup, stale, started, h.probe.probe, func(context.Context) bool {
			h.stops.Add(1)
			return systemdStopSidecar(ctx)
		})
	}()
	select {
	case h.cmd = <-started:
	case code := <-h.done:
		t.Fatalf("supervisor exited before starting its child: code=%d", code)
	case <-time.After(10 * time.Second):
		t.Fatal("supervisor never started its child")
	}
	return h
}

func (h *hr8466Supervisor) calls() []string {
	h.notifier.mu.Lock()
	defer h.notifier.mu.Unlock()
	return append([]string(nil), h.notifier.calls...)
}

func (h *hr8466Supervisor) count(value string) int {
	count := 0
	for _, call := range h.calls() {
		if call == value {
			count++
		}
	}
	return count
}

func (h *hr8466Supervisor) waitCount(value string, want int, timeout time.Duration) []string {
	h.t.Helper()
	deadline := time.Now().Add(timeout)
	for {
		calls := h.calls()
		count := 0
		for _, call := range calls {
			if call == value {
				count++
			}
		}
		if count >= want {
			return calls
		}
		if time.Now().After(deadline) {
			h.t.Fatalf("notification %q count=%d, want >=%d; calls=%v", value, count, want, calls)
		}
		time.Sleep(time.Millisecond)
	}
}

func (h *hr8466Supervisor) waitProbes(want int, timeout time.Duration) {
	h.t.Helper()
	deadline := time.Now().Add(timeout)
	for h.probe.count() < want {
		if time.Now().After(deadline) {
			h.t.Fatalf("probe count=%d, want >=%d; calls=%v", h.probe.count(), want, h.calls())
		}
		time.Sleep(time.Millisecond)
	}
}

func (h *hr8466Supervisor) waitDone(timeout time.Duration) int {
	h.t.Helper()
	if h.finished {
		return h.code
	}
	select {
	case h.code = <-h.done:
		h.finished = true
		return h.code
	case <-time.After(timeout):
		if h.cmd != nil && h.cmd.Process != nil {
			_ = h.cmd.Process.Kill()
		}
		select {
		case h.code = <-h.done:
			h.finished = true
			return h.code
		case <-time.After(10 * time.Second):
			h.t.Fatalf("supervisor did not terminate; calls=%v probes=%d", h.calls(), h.probe.count())
		}
	}
	return -1
}

func (h *hr8466Supervisor) teardown() {
	if h.finished {
		return
	}
	h.cancel()
	if h.cmd != nil && h.cmd.Process != nil {
		_ = h.cmd.Process.Kill()
	}
	_ = h.waitDone(10 * time.Second)
}

func firstHR8466Index(calls []string, value string) int {
	for i, call := range calls {
		if call == value {
			return i
		}
	}
	return -1
}

// ---------------------------------------------------------------------------
// Q. Command-seam named-query cases (real systemdSidecarState).
// ---------------------------------------------------------------------------

// Q6: the complete non-success-result / classification combination table.
func TestSystemdSidecarHealthResultAndStateCombinationTable(t *testing.T) {
	type row struct {
		name                                   string
		activeState, subState, result, mainPID string
		want                                   sidecarObservation
	}
	rows := []row{
		{"inactive-dead-success-pid0", "inactive", "dead", "success", "0", sidecarAbsent},
		{"inactive-dead-success-pid2", "inactive", "dead", "success", "2", sidecarUnknown},
		{"inactive-dead-exitcode-pid0", "inactive", "dead", "exit-code", "0", sidecarAbsent},
		{"inactive-dead-exitcode-pid2", "inactive", "dead", "exit-code", "2", sidecarUnknown},
		{"inactive-running-success-pid0", "inactive", "running", "success", "0", sidecarUnknown},
		{"inactive-running-exitcode-pid0", "inactive", "running", "exit-code", "0", sidecarUnknown},
		{"reloading-start-success-pid2", "reloading", "start", "success", "2", sidecarUnknown},
		{"active-start-success-pid0", "active", "start", "success", "0", sidecarPresentUnhealthy},
		{"active-start-exitcode-pid2", "active", "start", "exit-code", "2", sidecarPresentUnhealthy},
		{"activating-start-success-pid0", "activating", "start", "success", "0", sidecarPresentUnhealthy},
		{"activating-start-exitcode-pid2", "activating", "start", "exit-code", "2", sidecarPresentUnhealthy},
		{"deactivating-stop-success-pid0", "deactivating", "stop", "success", "0", sidecarPresentUnhealthy},
		{"deactivating-stop-exitcode-pid2", "deactivating", "stop", "exit-code", "2", sidecarPresentUnhealthy},
		{"failed-failed-success-pid0", "failed", "failed", "success", "0", sidecarPresentUnhealthy},
		{"failed-failed-exitcode-pid2", "failed", "failed", "exit-code", "2", sidecarPresentUnhealthy},
		{"active-running-exitcode-pid2", "active", "running", "exit-code", "2", sidecarPresentUnhealthy},
	}
	for _, tc := range rows {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			stdout := "ActiveState=" + tc.activeState + "\nSubState=" + tc.subState + "\nResult=" + tc.result + "\nMainPID=" + tc.mainPID + "\n"
			if got := observeSidecarState(t, stdout); got != tc.want {
				t.Fatalf("ActiveState=%s SubState=%s Result=%s MainPID=%s = %d, want %d",
					tc.activeState, tc.subState, tc.result, tc.mainPID, got, tc.want)
			}
		})
	}
}

// Q7: complete stdout with a nonzero status, truncation, blank framing and an
// unstartable command all fail closed.
func TestSystemdSidecarHealthRejectsFailedAndTruncatedOutputs(t *testing.T) {
	installFakeSystemctl(t, fakeSystemctlShellPrintf(healthySystemdShowOutput)+"exit 2\n")
	if got := systemdSidecarState(context.Background()); got != sidecarUnknown {
		t.Fatalf("nonzero exit with complete stdout = %d, want sidecarUnknown", got)
	}
	for name, stdout := range map[string]string{
		"truncated-three-keys": "ActiveState=active\nSubState=running\nResult=success\n",
		"empty":                "",
		"bare-newline":         "\n",
		"double-trailing":      healthySystemdShowOutput + "\n",
		"interior-blank":       "ActiveState=active\n\nSubState=running\nResult=success\nMainPID=42\n",
	} {
		if got := observeSidecarState(t, stdout); got != sidecarUnknown {
			t.Fatalf("%s = %d, want sidecarUnknown", name, got)
		}
	}
	t.Setenv("PATH", t.TempDir())
	if got := systemdSidecarState(context.Background()); got != sidecarUnknown {
		t.Fatalf("unstartable systemctl = %d, want sidecarUnknown", got)
	}
}

// Q8: the real one-second query deadline, an earlier parent deadline, an
// already-cancelled parent, and in-flight cancellation after the helper has
// actually entered.  Every path must return unknown and terminate/reap the
// started helper.
func TestSystemdSidecarStateDeadlinesAndCancellation(t *testing.T) {
	for _, mode := range []string{"one-second", "earlier-parent", "already-cancelled", "in-flight"} {
		mode := mode
		t.Run(mode, func(t *testing.T) {
			f := newHR8466Fixture(t)
			f.setState("delay")
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if mode == "earlier-parent" {
				var stop context.CancelFunc
				ctx, stop = context.WithTimeout(ctx, 100*time.Millisecond)
				defer stop()
			}
			if mode == "already-cancelled" {
				cancel()
			}
			start := time.Now()
			done := make(chan sidecarObservation, 1)
			go func() { done <- systemdSidecarState(ctx) }()
			if mode == "in-flight" {
				deadline := time.Now().Add(5 * time.Second)
				for len(hr8466Events(f.entered)) < 1 {
					if time.Now().After(deadline) {
						t.Fatal("fixture helper never entered")
					}
					time.Sleep(time.Millisecond)
				}
				cancel()
			}
			select {
			case got := <-done:
				if got != sidecarUnknown {
					t.Fatalf("%s = %d, want sidecarUnknown", mode, got)
				}
			case <-time.After(3 * time.Second):
				t.Fatal("query did not terminate within its bound")
			}
			elapsed := time.Since(start)
			switch mode {
			case "one-second":
				if elapsed < 900*time.Millisecond || elapsed > 2500*time.Millisecond {
					t.Fatalf("one-second deadline elapsed=%s", elapsed)
				}
			case "earlier-parent":
				if elapsed > 900*time.Millisecond {
					t.Fatalf("earlier parent deadline elapsed=%s", elapsed)
				}
			}
			entered := hr8466Events(f.entered)
			if len(entered) > 0 {
				pid, err := strconv.Atoi(strings.Fields(entered[0])[0])
				if err != nil {
					t.Fatalf("bad entered pid %q", entered[0])
				}
				if err := syscall.Kill(pid, 0); err != syscall.ESRCH {
					t.Fatalf("helper pid %d not reaped: err=%v", pid, err)
				}
			} else if mode != "already-cancelled" {
				t.Fatal("missing helper entry receipt for a started query")
			}
			t.Logf("%s returned unknown in %s", mode, elapsed)
		})
	}
}

// ---------------------------------------------------------------------------
// H. Real consumers.
// ---------------------------------------------------------------------------

// H1: all 24 healthy property permutations through the real supervisor for
// both startup orderings.  READY must never precede complete child+sidecar
// health, must occur exactly once, and WATCHDOG only follows a subsequent
// complete healthy pair.
func TestConsumerCompositeReadinessAllHealthyPermutations(t *testing.T) {
	permutations := permutationsOf([]string{"ActiveState=active", "SubState=running", "Result=success", "MainPID=42"})
	if len(permutations) != 24 {
		t.Fatalf("permutation count = %d, want 24", len(permutations))
	}
	orders := make([]string, 0, len(permutations))
	for _, permutation := range permutations {
		keys := make([]string, 0, 4)
		for _, line := range permutation {
			keys = append(keys, strings.SplitN(line, "=", 2)[0])
		}
		orders = append(orders, strings.Join(keys, ","))
	}
	for _, ordering := range []string{"sidecar-first", "connector-first"} {
		for index, order := range orders {
			ordering, order, index := ordering, order, index
			t.Run(fmt.Sprintf("%s/perm%02d", ordering, index), func(t *testing.T) {
				runHR8466CompositePermutation(t, ordering, order)
			})
		}
	}
}

func runHR8466CompositePermutation(t *testing.T, ordering, order string) {
	t.Helper()
	f := newHR8466Fixture(t)
	f.setOrder(order)
	states := []string{"healthy", "healthy", "healthy"}
	if ordering == "connector-first" {
		states = []string{"unhealthy", "healthy", "healthy"}
	}
	f.setPlan("ready", "healthy", "healthy")
	h := startHR8466Supervisor(t, 5*time.Second, 5*time.Second)
	defer h.teardown()

	for i := 0; i < len(states); i++ {
		f.waitChildWaiting(t, i)
		if i == 0 {
			if got := h.count("READY=1"); got != 0 {
				t.Fatalf("READY before any complete sidecar health: %d", got)
			}
		}
		f.setState(states[i])
		f.releaseChild(i)
		f.waitChildEmitted(t, i)
		h.waitProbes(i+1, 5*time.Second)
		switch {
		case ordering == "connector-first" && i == 0:
			if got := h.count("READY=1"); got != 0 {
				t.Fatalf("early READY with an unhealthy sidecar: calls=%v", h.calls())
			}
			if got := h.count("WATCHDOG=1"); got != 0 {
				t.Fatalf("early WATCHDOG with an unhealthy sidecar: calls=%v", h.calls())
			}
		case ordering == "connector-first" && i == 1, ordering == "sidecar-first" && i == 0:
			h.waitCount("READY=1", 1, 5*time.Second)
		}
		if (ordering == "sidecar-first" && i == 1) || (ordering == "connector-first" && i == 2) {
			h.waitCount("WATCHDOG=1", 1, 5*time.Second)
		}
	}
	if got := h.count("READY=1"); got != 1 {
		t.Fatalf("READY count = %d, want 1; calls=%v", got, h.calls())
	}
	calls := h.calls()
	ready := firstHR8466Index(calls, "READY=1")
	watchdog := firstHR8466Index(calls, "WATCHDOG=1")
	if ready < 0 || watchdog < 0 {
		t.Fatalf("missing READY/WATCHDOG; calls=%v", calls)
	}
	if ready > watchdog {
		t.Fatalf("WATCHDOG preceded READY; calls=%v", calls)
	}
	events := hr8466Events(f.events)
	if !hr8466HasEvent(events, "show:"+states[0]) {
		t.Fatalf("fixture did not observe the expected initial state; events=%v", events)
	}
	assertHR8466NoFixtureLeak(t, f)
}

// H2: an initial malformed/absent/unhealthy/error observation must not emit
// READY or WATCHDOG, and recovery must happen on a subsequent complete healthy
// child record without refreshing the absolute startup deadline.
func TestConsumerInitialBadObservationDoesNotEarlyReady(t *testing.T) {
	families := map[string]string{
		"absent":        "absent",
		"unhealthy":     "unhealthy",
		"malformed":     "garbage",
		"duplicate":     "dupsame-Result",
		"missing":       "missing-MainPID",
		"empty":         "empty-ActiveState",
		"error-exit":    "fail",
		"unknown-state": "unknownstate",
	}
	for name, bad := range families {
		name, bad := name, bad
		t.Run(name, func(t *testing.T) {
			f := newHR8466Fixture(t)
			f.setPlan("ready", "healthy", "healthy")
			h := startHR8466Supervisor(t, 5*time.Second, 5*time.Second)
			defer h.teardown()

			f.waitChildWaiting(t, 0)
			f.setState(bad)
			f.releaseChild(0)
			f.waitChildEmitted(t, 0)
			h.waitProbes(1, 5*time.Second)
			if got := h.count("READY=1"); got != 0 {
				t.Fatalf("early READY on %s: calls=%v", name, h.calls())
			}
			if got := h.count("WATCHDOG=1"); got != 0 {
				t.Fatalf("early WATCHDOG on %s: calls=%v", name, h.calls())
			}

			f.waitChildWaiting(t, 1)
			f.setState("healthy")
			f.releaseChild(1)
			f.waitChildEmitted(t, 1)
			h.waitProbes(2, 5*time.Second)
			h.waitCount("READY=1", 1, 5*time.Second)

			f.waitChildWaiting(t, 2)
			f.setState("healthy")
			f.releaseChild(2)
			f.waitChildEmitted(t, 2)
			h.waitProbes(3, 5*time.Second)
			h.waitCount("WATCHDOG=1", 1, 5*time.Second)
			assertHR8466NoFixtureLeak(t, f)
		})
	}
}

// H2 never-recovers: repeated records while the sidecar stays bad must not
// refresh the original absolute deadline and must never publish READY.
func TestConsumerNeverRecoversHonorsOriginalDeadline(t *testing.T) {
	f := newHR8466Fixture(t)
	f.setPlan("ready", "healthy", "healthy", "healthy", "healthy", "healthy")
	f.releaseAll(6)
	f.setState("unhealthy")
	start := time.Now()
	h := startHR8466Supervisor(t, 250*time.Millisecond, 10*time.Second)
	defer h.teardown()

	h.waitCount("STOPPING=1", 1, 5*time.Second)
	if got := h.count("READY=1"); got != 0 {
		t.Fatalf("READY while the sidecar never became healthy: calls=%v", h.calls())
	}
	if got := h.count("WATCHDOG=1"); got != 0 {
		t.Fatalf("WATCHDOG before READY: calls=%v", h.calls())
	}
	// Admission is now polling; allow the stop to complete so the supervisor
	// terminates instead of spinning.
	f.setState("absent")
	code := h.waitDone(10 * time.Second)
	elapsed := time.Since(start)
	if elapsed < 200*time.Millisecond || elapsed > 5*time.Second {
		t.Fatalf("absolute startup deadline not honored: elapsed=%s code=%d", elapsed, code)
	}
}

// H3: after READY and WATCHDOG, every failing/non-healthy observation family
// suppresses any further WATCHDOG and stops composite readiness.  The bad
// observation must be the driver, not the absolute timer (staleAfter is
// deliberately much larger than the test window).
func TestConsumerBadObservationAfterReadySuppressesWatchdog(t *testing.T) {
	badStates := []string{
		"dupsame-ActiveState", "dupsame-SubState", "dupsame-Result", "dupsame-MainPID",
		"dupconflict-ActiveState", "dupconflict-SubState", "dupconflict-Result", "dupconflict-MainPID",
		"missing-ActiveState", "missing-SubState", "missing-Result", "missing-MainPID",
		"empty-ActiveState", "empty-SubState", "empty-Result", "empty-MainPID",
		"nosep-MainPID", "garbage", "unknownstate", "piddiff", "exitcode", "fail",
	}
	for _, bad := range badStates {
		bad := bad
		t.Run(strings.ReplaceAll(bad, "-", "_"), func(t *testing.T) {
			f := newHR8466Fixture(t)
			f.setPlan("ready", "healthy", "healthy")
			h := startHR8466Supervisor(t, 10*time.Second, 10*time.Second)
			defer h.teardown()

			f.waitChildWaiting(t, 0)
			f.setState("healthy")
			f.releaseChild(0)
			f.waitChildEmitted(t, 0)
			h.waitProbes(1, 5*time.Second)
			h.waitCount("READY=1", 1, 5*time.Second)

			f.waitChildWaiting(t, 1)
			f.setState("healthy")
			f.releaseChild(1)
			f.waitChildEmitted(t, 1)
			h.waitProbes(2, 5*time.Second)
			h.waitCount("WATCHDOG=1", 1, 5*time.Second)

			f.waitChildWaiting(t, 2)
			f.setState(bad)
			f.releaseChild(2)
			f.waitChildEmitted(t, 2)
			// The bad observation must produce a stop decision and must not
			// produce any further WATCHDOG.
			h.waitCount("STOPPING=1", 1, 5*time.Second)
			deadline := time.Now().Add(300 * time.Millisecond)
			for time.Now().Before(deadline) {
				if got := h.count("WATCHDOG=1"); got != 1 {
					t.Fatalf("continued WATCHDOG on %s: count=%d calls=%v", bad, got, h.calls())
				}
				time.Sleep(2 * time.Millisecond)
			}
			if got := h.count("READY=1"); got != 1 {
				t.Fatalf("READY count changed on %s: %d", bad, got)
			}
			assertHR8466NoFixtureLeak(t, f)
		})
	}
}

// H3 cancellation representative: cancel the query actually in flight after it
// has entered, and confirm no further WATCHDOG and no continued readiness.
func TestConsumerCancelledObservationAfterReadySuppressesWatchdog(t *testing.T) {
	f := newHR8466Fixture(t)
	f.setPlan("ready", "healthy", "healthy")
	h := startHR8466Supervisor(t, 10*time.Second, 10*time.Second)
	defer h.teardown()

	f.waitChildWaiting(t, 0)
	f.setState("healthy")
	f.releaseChild(0)
	f.waitChildEmitted(t, 0)
	h.waitProbes(1, 5*time.Second)
	h.waitCount("READY=1", 1, 5*time.Second)

	f.waitChildWaiting(t, 1)
	f.setState("healthy")
	f.releaseChild(1)
	f.waitChildEmitted(t, 1)
	h.waitProbes(2, 5*time.Second)
	h.waitCount("WATCHDOG=1", 1, 5*time.Second)

	f.waitChildWaiting(t, 2)
	f.setState("delay")
	f.releaseChild(2)
	f.waitChildEmitted(t, 2)
	// The third show invocation is the in-flight one for record 2; the two
	// earlier records already recorded their own entries.
	deadline := time.Now().Add(5 * time.Second)
	for len(hr8466Events(f.entered)) < 3 {
		if time.Now().After(deadline) {
			t.Fatal("in-flight query never entered")
		}
		time.Sleep(time.Millisecond)
	}
	h.cancel()
	h.waitCount("STOPPING=1", 1, 5*time.Second)
	if got := h.count("WATCHDOG=1"); got != 1 {
		t.Fatalf("continued WATCHDOG after cancelled observation: %d", got)
	}
	// Let the detached (Background) admission observe the real absence so the
	// supervisor terminates instead of retrying the still-present sidecar.
	f.setState("absent")
	if code := h.waitDone(10 * time.Second); code != 0 && code != 1 {
		t.Fatalf("supervisor exit code=%d", code)
	}
	if got := h.count("WATCHDOG=1"); got != 1 {
		t.Fatalf("continued WATCHDOG after cancelled observation: %d", got)
	}
	assertHR8466NoFixtureLeak(t, f)
}

// H4/H5: the supervisor's real child cleanup is ordered after observed sidecar
// absence; a still-present sidecar keeps the child alive and a single stop is
// issued within the attempt.
func TestConsumerAdmissionOrderingBeforeChildCleanup(t *testing.T) {
	t.Run("initial-absent-no-stop", func(t *testing.T) {
		f := newHR8466Fixture(t)
		f.setPlan("stopping")
		f.setState("absent")
		h := startHR8466Supervisor(t, 10*time.Second, 10*time.Second)
		defer h.teardown()

		f.waitChildWaiting(t, 0)
		f.releaseChild(0)
		events := f.waitTerm(t)
		if hr8466HasEvent(events, "stop") {
			t.Fatalf("already-absent sidecar was stopped: events=%v", events)
		}
		term := firstHR8466Index(events, "term")
		showAbsent := -1
		for i, line := range events {
			if line == "show:absent" {
				showAbsent = i
			}
		}
		if showAbsent < 0 || term < showAbsent {
			t.Fatalf("child cleanup preceded observed absence: events=%v", events)
		}
		if code := h.waitDone(5 * time.Second); code != 0 {
			t.Fatalf("absent admission exit code=%d, want 0", code)
		}
		assertHR8466NoFixtureLeak(t, f)
	})

	t.Run("stop-then-still-present-then-absent", func(t *testing.T) {
		f := newHR8466Fixture(t)
		f.setPlan("stopping")
		f.setState("healthy")
		f.holdPresent()
		h := startHR8466Supervisor(t, 10*time.Second, 10*time.Second)
		defer h.teardown()

		f.waitChildWaiting(t, 0)
		f.releaseChild(0)
		events := f.waitStop(t)
		if hr8466HasEvent(events, "term") {
			t.Fatalf("child was cleaned up while the sidecar was still present: events=%v", events)
		}
		if got := hr8466EventCount(events, "stop"); got != 1 {
			t.Fatalf("stop count = %d within the attempt, want 1; events=%v", got, events)
		}
		f.setState("absent")
		events = f.waitTerm(t)
		if got := hr8466EventCount(events, "stop"); got != 1 {
			t.Fatalf("stop count = %d after absence, want 1; events=%v", got, events)
		}
		if code := h.waitDone(5 * time.Second); code != 0 {
			t.Fatalf("clean admission exit code=%d, want 0", code)
		}
		assertHR8466NoFixtureLeak(t, f)
	})
}

// H6: stop failure, an initially unknown query, and present -> stop -> unknown
// all deny admission and never perform product child cleanup.
func TestConsumerNegativeAdmissionNeverCleansUpChild(t *testing.T) {
	cases := []struct {
		name    string
		initial string
		after   string
		hold    bool
		fail    bool
	}{
		{"stop-failure", "healthy", "", false, true},
		{"initially-unknown", "garbage", "", false, false},
		{"stop-then-unknown", "healthy", "garbage", true, false},
	}
	for _, tc := range cases {
		tc := tc
		t.Run(tc.name, func(t *testing.T) {
			f := newHR8466Fixture(t)
			f.setPlan("stopping")
			f.setState(tc.initial)
			if tc.hold {
				f.holdPresent()
			}
			if tc.fail {
				f.requireStopFailure()
			}
			h := startHR8466Supervisor(t, 10*time.Second, 10*time.Second)
			defer h.teardown()

			f.waitChildWaiting(t, 0)
			f.releaseChild(0)
			if tc.after != "" {
				f.waitStop(t)
				deadline := time.Now().Add(2 * time.Second)
				for h.probe.count() < 2 && time.Now().Before(deadline) {
					time.Sleep(time.Millisecond)
				}
				f.setState(tc.after)
			} else {
				deadline := time.Now().Add(2 * time.Second)
				for h.probe.count() < 1 && time.Now().Before(deadline) {
					time.Sleep(time.Millisecond)
				}
			}
			if hr8466HasEvent(hr8466Events(f.events), "term") {
				t.Fatalf("negative admission performed product child cleanup: events=%v", hr8466Events(f.events))
			}
			f.exitChild()
			if code := h.waitDone(5 * time.Second); code != 1 {
				t.Fatalf("negative admission exit code=%d, want 1", code)
			}
			if hr8466HasEvent(hr8466Events(f.events), "term") {
				t.Fatalf("negative admission terminated the product child: events=%v", hr8466Events(f.events))
			}
			if tc.name == "initially-unknown" && hr8466HasEvent(hr8466Events(f.events), "stop") {
				t.Fatalf("unknown query issued a stop: events=%v", hr8466Events(f.events))
			}
			assertHR8466NoFixtureLeak(t, f)
		})
	}
}

// H7: the real five-second admission timeout, supplied-context cancellation of
// the re-observation, and the supervisor's detached (Background) admission
// context.
func TestConsumerAdmissionTimeoutAndDetachedContext(t *testing.T) {
	t.Run("five-second-timeout", func(t *testing.T) {
		f := newHR8466Fixture(t)
		f.setState("healthy")
		f.holdPresent()
		start := time.Now()
		if removeSidecarAdmission(context.Background(), systemdSidecarState, systemdStopSidecar) {
			t.Fatal("persistent present sidecar falsely claimed absent")
		}
		elapsed := time.Since(start)
		if elapsed < 4500*time.Millisecond || elapsed > 7500*time.Millisecond {
			t.Fatalf("admission timeout elapsed=%s, want ~5s", elapsed)
		}
		if !hr8466HasEvent(hr8466Events(f.events), "stop") {
			t.Fatalf("admission timeout issued no stop: events=%v", hr8466Events(f.events))
		}
		assertHR8466NoFixtureLeak(t, f)
	})

	t.Run("supplied-context-cancelled", func(t *testing.T) {
		f := newHR8466Fixture(t)
		f.setState("healthy")
		f.holdPresent()
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		go func() {
			_ = waitHR8466EventPrefixBool(f.events, "stop", 2*time.Second)
			cancel()
		}()
		start := time.Now()
		if removeSidecarAdmission(ctx, systemdSidecarState, systemdStopSidecar) {
			t.Fatal("cancelled re-observation falsely claimed absent")
		}
		if elapsed := time.Since(start); elapsed > 2*time.Second {
			t.Fatalf("cancellation not honored during re-observation: %s", elapsed)
		}
		assertHR8466NoFixtureLeak(t, f)
	})

	t.Run("supervisor-uses-detached-context", func(t *testing.T) {
		f := newHR8466Fixture(t)
		f.setPlan("ready", "healthy")
		f.setState("healthy")
		h := startHR8466Supervisor(t, 10*time.Second, 10*time.Second)
		defer h.teardown()
		f.waitChildWaiting(t, 0)
		f.releaseChild(0)
		f.waitChildEmitted(t, 0)
		h.waitCount("READY=1", 1, 5*time.Second)
		h.cancel()
		deadline := time.Now().Add(5 * time.Second)
		for !h.probe.sawDetachedContext() && time.Now().Before(deadline) {
			time.Sleep(time.Millisecond)
		}
		if !h.probe.sawDetachedContext() {
			t.Fatal("supervisor admission did not use a detached context")
		}
		assertHR8466NoFixtureLeak(t, f)
	})
}
