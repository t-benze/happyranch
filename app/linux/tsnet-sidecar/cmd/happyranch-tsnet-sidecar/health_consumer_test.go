package main

// TASK-8489: corrected TASK8466 real command-seam Q1-Q8 and real-consumer
// H1-H7 regression coverage for the managed N3 composite readiness path.
//
// Every observation in this file is made through the real exported seam
// (systemdSidecarState / systemdStopSidecar) against a per-case fail-closed
// systemctl fixture on PATH that accepts only the exact canonical argv and
// never forwards to the host manager.  The health child is this test binary
// re-executed as a helper process, so the real child health FD, the real
// canonical generation/sequence records, and real process termination and
// reaping are exercised.
//
// TASK8468 finding 5 corrections implemented here:
//  1. The supervisor stop closure forwards the context the production
//     consumer actually supplied (never the supervisor parent), the health
//     child asserts NOTIFY_SOCKET absence, the supervisor keeps sole
//     ownership of cmd.Wait, product waits fail (rather than kill and
//     return success), and test-owned teardown is a separate
//     TEST_TEARDOWN_BEGIN-delimited path.
//  2. Q6 is the exact accepted state/result/PID table and Q8 keeps the real
//     deadline/cancellation seams with a 2s normal entry barrier and proven
//     helper reaping.
//  3. H1/H2 negative assertions run behind a real probe gate (a completed
//     observation/consumer-progress boundary) instead of a bare probe
//     counter, and never-recovers bounds STOPPING by the original 250ms
//     deadline plus the accepted 2s scheduling allowance.
//  4. H3 includes a real entered query-deadline consumer case.
//  5. H5 covers initially healthy and unhealthy with completed
//     still-present/absence observations before any product child cleanup;
//     H6 finishes each negative observation before asserting no cleanup.
//  6. H7 cancels the context actually supplied to removeSidecarAdmission
//     only after an entered re-observation barrier and keeps the separate
//     production Background-context behavior.
//
// Bounds: ordinary barrier/join 2s, real query plus teardown 3s, real
// admission plus teardown 7s.

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
	"syscall"
	"testing"
	"time"
)

const (
	hr8466ShowArgv = "show happyranch-tsnet-sidecar.service --property=ActiveState --property=SubState --property=Result --property=MainPID"
	hr8466StopArgv = "stop --no-block happyranch-tsnet-sidecar.service"

	// hr8466NormalBarrier is the accepted ordinary barrier/join bound.
	hr8466NormalBarrier = 2 * time.Second
	// hr8466QueryBound is the accepted real-query plus teardown bound.
	hr8466QueryBound = 3 * time.Second
	// hr8466AdmissionBound is the accepted real-admission plus teardown bound.
	hr8466AdmissionBound = 7 * time.Second
	// hr8466FixtureOutputCap is the accepted finite fixture reply bound.
	hr8466FixtureOutputCap = 4 * 1024
)

// TestConnectorHealthFixtureChild is the controlled health child.  It writes
// real canonical generation/sequence records over the inherited health FD and
// exposes an entered/released barrier so the tests prove ordering rather than
// sleeping.  It is a no-op during an ordinary `go test` run.
func TestConnectorHealthFixtureChild(t *testing.T) {
	if os.Getenv("HR8466_CHILD") != "1" {
		return
	}
	events := os.Getenv("HR8466_EVENTS")
	// The production supervisor removes NOTIFY_SOCKET from the child
	// environment; the real child must never regain notification authority.
	if os.Getenv("NOTIFY_SOCKET") != "" {
		appendHR8466Event(events, "notify_leak")
		os.Exit(5)
	}
	appendHR8466Event(events, "notify_absent")

	fd, _ := strconv.Atoi(os.Getenv("HAPPYRANCH_CHILD_HEALTH_FD"))
	out := os.NewFile(uintptr(fd), "health")
	generation := os.Getenv("HAPPYRANCH_CHILD_HEALTH_GENERATION")
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
			if !waitHR8466Line(barrier, strconv.Itoa(i), hr8466NormalBarrier) {
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
// NOTIFY_SOCKET leaked, records every invocation plus an exact-start and
// exact-completion receipt for each observation, renders the reply bytes for
// the current state token, and can be told to hold the sidecar present or to
// delay the observation beyond the real query deadline.
const hr8466FixtureScript = `#!/bin/sh
if [ -n "$NOTIFY_SOCKET" ]; then echo notify_leak >> "$HR8466_LOG"; exit 9; fi
echo "$*" >> "$HR8466_LOG"
case "$1" in
  show)
    if [ "$*" != "show happyranch-tsnet-sidecar.service --property=ActiveState --property=SubState --property=Result --property=MainPID" ]; then echo bad_argv >> "$HR8466_EVENTS"; exit 96; fi
    echo $$ >> "$HR8466_ENTRY"
    state=$(/bin/cat "$HR8466_STATE")
    echo "show:$state" >> "$HR8466_EVENTS"
    if [ -f "$HR8466_DELAY" ]; then exec /bin/sleep 10; fi
    /bin/cat "$HR8466_REPLIES/$state"
    echo "show-done:$state" >> "$HR8466_EVENTS"
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
	teardownOnce                             sync.Once
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
	if len(reply) > hr8466FixtureOutputCap {
		f.t.Fatalf("fixture reply for %q is %d bytes, exceeding the accepted %d-byte cap", state, len(reply), hr8466FixtureOutputCap)
	}
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

// beginTeardown records the explicit test-owned teardown boundary before any
// test-owned exit/TERM/kill action so those actions can never be mistaken for
// product child cleanup.
func (f *hr8466Fixture) beginTeardown() {
	f.teardownOnce.Do(func() { appendHR8466Event(f.events, "TEST_TEARDOWN_BEGIN") })
}

// productEvents returns only the events recorded before the test-owned
// teardown boundary.  Product cleanup assertions must use this view.
func (f *hr8466Fixture) productEvents() []string {
	var out []string
	for _, line := range hr8466Events(f.events) {
		if line == "TEST_TEARDOWN_BEGIN" {
			break
		}
		out = append(out, line)
	}
	return out
}

// exitChild is a test-owned action: it records the teardown boundary first and
// then asks the fixture child to exit so a supervisor blocked on a negative
// admission can settle.
func (f *hr8466Fixture) exitChild() {
	f.beginTeardown()
	writeHR8466File(f.childExit, "exit")
}

func (f *hr8466Fixture) waitEventPrefix(t *testing.T, prefix string, timeout time.Duration) []string {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for {
		events := f.productEvents()
		for _, line := range events {
			if strings.HasPrefix(line, prefix) {
				return events
			}
		}
		if time.Now().After(deadline) {
			t.Fatalf("event prefix %q not observed within %s; events=%v", prefix, timeout, f.productEvents())
		}
		time.Sleep(time.Millisecond)
	}
}

func (f *hr8466Fixture) waitEvent(t *testing.T, exact string, timeout time.Duration) []string {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for {
		events := f.productEvents()
		for _, line := range events {
			if line == exact {
				return events
			}
		}
		if time.Now().After(deadline) {
			t.Fatalf("event %q not observed within %s; events=%v", exact, timeout, f.productEvents())
		}
		time.Sleep(time.Millisecond)
	}
}

// waitShowComplete waits until the fixture has completed `want` observations of
// the named state (the reply bytes were emitted), which is the real
// observation-completion boundary.
func (f *hr8466Fixture) waitShowComplete(t *testing.T, state string, want int, timeout time.Duration) []string {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for {
		events := f.productEvents()
		count := 0
		for _, line := range events {
			if line == "show-done:"+state {
				count++
			}
		}
		if count >= want {
			return events
		}
		if time.Now().After(deadline) {
			t.Fatalf("completed observation %q count=%d, want >=%d; events=%v", "show-done:"+state, count, want, events)
		}
		time.Sleep(time.Millisecond)
	}
}

func (f *hr8466Fixture) waitChildWaiting(t *testing.T, i int) {
	t.Helper()
	f.waitEventPrefix(t, fmt.Sprintf("waiting:%d", i), hr8466NormalBarrier)
}

func (f *hr8466Fixture) waitChildEmitted(t *testing.T, i int) []string {
	t.Helper()
	return f.waitEventPrefix(t, fmt.Sprintf("emitted:%d:", i), hr8466NormalBarrier)
}

func (f *hr8466Fixture) waitStop(t *testing.T) []string {
	t.Helper()
	return f.waitEventPrefix(t, "stop", hr8466NormalBarrier)
}

func (f *hr8466Fixture) waitTerm(t *testing.T) []string {
	t.Helper()
	return f.waitEventPrefix(t, "term", hr8466NormalBarrier)
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
// The health child separately records whether it inherited NOTIFY_SOCKET.
func assertHR8466NoFixtureLeak(t *testing.T, f *hr8466Fixture) {
	t.Helper()
	for _, line := range f.productEvents() {
		if line == "notify_leak" || strings.HasPrefix(line, "bad_argv") {
			t.Fatalf("real consumer issued a non-canonical/leaking query: events=%v", f.productEvents())
		}
	}
	if log := readHR8466File(f.log); strings.Contains(log, "notify_leak") {
		t.Fatalf("NOTIFY_SOCKET leaked into a fixture command: %q", log)
	}
}

// ---------------------------------------------------------------------------
// Instrumented real-probe consumer harness.
// ---------------------------------------------------------------------------

// hr8466ProbeLog wraps the real systemdSidecarState with an optional gate: when
// armed, the very next real observation blocks after its query completes and
// before its result is returned to the supervisor.  The test can therefore
// assert negative READY/WATCHDOG properties only after the prior record was
// fully consumed and before the gated record is acted on.
type hr8466ProbeLog struct {
	mu         sync.Mutex
	results    []sidecarObservation
	background []bool
	durations  []time.Duration
	armGate    bool
	gateIn     chan int
	gateOut    chan struct{}
}

func newHR8466ProbeLog() *hr8466ProbeLog {
	return &hr8466ProbeLog{gateIn: make(chan int, 1), gateOut: make(chan struct{})}
}

func (p *hr8466ProbeLog) armNext() {
	p.mu.Lock()
	p.armGate = true
	p.mu.Unlock()
}

func (p *hr8466ProbeLog) probe(ctx context.Context) sidecarObservation {
	start := time.Now()
	result := systemdSidecarState(ctx)
	elapsed := time.Since(start)
	p.mu.Lock()
	p.results = append(p.results, result)
	p.background = append(p.background, ctx.Done() == nil)
	p.durations = append(p.durations, elapsed)
	index := len(p.results)
	gate := p.armGate
	p.armGate = false
	p.mu.Unlock()
	if gate {
		p.gateIn <- index
		<-p.gateOut
	}
	return result
}

// lastDuration returns the wall-clock duration and result of the most recently
// completed real observation.  The tests use it to prove the real query
// deadline actually elapsed without racing an admission re-observation.
func (p *hr8466ProbeLog) lastDuration() (time.Duration, sidecarObservation, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if len(p.durations) == 0 {
		return 0, sidecarUnknown, false
	}
	last := len(p.durations) - 1
	return p.durations[last], p.results[last], true
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
	fixture  *hr8466Fixture
	notifier *recordingNotifier
	probe    *hr8466ProbeLog
	cancel   context.CancelFunc
	done     chan int
	cmd      *exec.Cmd
	finished bool
	code     int
}

// startHR8466Supervisor starts the real supervisor.  The stop callback forwards
// the context production actually supplied (the real admission seam uses
// context.Background()); the harness never substitutes the supervisor parent.
func startHR8466Supervisor(t *testing.T, f *hr8466Fixture, startup, stale time.Duration) *hr8466Supervisor {
	t.Helper()
	h := &hr8466Supervisor{
		t:        t,
		fixture:  f,
		notifier: &recordingNotifier{},
		probe:    newHR8466ProbeLog(),
		done:     make(chan int, 1),
	}
	ctx, cancel := context.WithCancel(context.Background())
	h.cancel = cancel
	// The supervisor owns cmd.Wait; the test only ever reaps by waiting for the
	// supervisor result.  Cleanup is registered before the first wait so a
	// failing assertion still tears the process tree down.
	t.Cleanup(h.verifyReaped)
	t.Cleanup(h.teardown)

	started := make(chan *exec.Cmd, 1)
	argv := []string{os.Args[0], "-test.run=^TestConnectorHealthFixtureChild$"}
	go func() {
		h.done <- superviseConnector(ctx, argv, h.notifier, startup, stale, started, h.probe.probe, func(stopCtx context.Context) bool {
			return systemdStopSidecar(stopCtx)
		})
	}()
	select {
	case h.cmd = <-started:
	case code := <-h.done:
		t.Fatalf("supervisor exited before starting its child: code=%d", code)
	case <-time.After(hr8466NormalBarrier):
		t.Fatal("supervisor never started its child")
	}
	h.waitChildHealthBoundary()
	return h
}

// waitChildHealthBoundary proves the real child started and asserted that it
// did not inherit NOTIFY_SOCKET; a leak fails the test immediately.
func (h *hr8466Supervisor) waitChildHealthBoundary() {
	h.t.Helper()
	deadline := time.Now().Add(hr8466NormalBarrier)
	for {
		for _, line := range hr8466Events(h.fixture.events) {
			if line == "notify_leak" {
				h.t.Fatalf("health child inherited NOTIFY_SOCKET: events=%v", hr8466Events(h.fixture.events))
			}
			if line == "notify_absent" {
				return
			}
		}
		if time.Now().After(deadline) {
			h.t.Fatalf("health child did not assert NOTIFY_SOCKET absence within %s", hr8466NormalBarrier)
		}
		time.Sleep(time.Millisecond)
	}
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

func (h *hr8466Supervisor) armProbeGate() { h.probe.armNext() }

func (h *hr8466Supervisor) waitProbeGateEntered(timeout time.Duration) int {
	h.t.Helper()
	select {
	case index := <-h.probe.gateIn:
		return index
	case <-time.After(timeout):
		h.t.Fatalf("probe gate not entered within %s; probes=%d calls=%v", timeout, h.probe.count(), h.calls())
		return -1
	}
}

func (h *hr8466Supervisor) releaseProbeGate() {
	h.t.Helper()
	select {
	case h.probe.gateOut <- struct{}{}:
	case <-time.After(hr8466NormalBarrier):
		h.t.Fatal("probe gate release was not consumed")
	}
}

// waitDone is the product wait: it fails the assertion when the supervisor does
// not settle within the bound.  Test-owned termination lives in teardown.
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
		h.t.Fatalf("supervisor did not terminate within %s; calls=%v probes=%d", timeout, h.calls(), h.probe.count())
		return -1
	}
}

// teardown is the separate guaranteed test-owned path: it records
// TEST_TEARDOWN_BEGIN before any kill, cancels the supervisor, and waits for
// the supervisor's own cmd.Wait result within the admission+teardown bound.
func (h *hr8466Supervisor) teardown() {
	if h.finished {
		return
	}
	h.fixture.beginTeardown()
	h.cancel()
	if h.cmd != nil && h.cmd.Process != nil {
		_ = h.cmd.Process.Kill()
	}
	select {
	case h.code = <-h.done:
		h.finished = true
	case <-time.After(hr8466AdmissionBound):
		// The product waitDone already fails the test when termination is
		// required; teardown must never itself report success.
	}
}

// verifyReaped checks (non-fatally, after teardown) that the supervisor-owned
// child was reaped by its single Wait owner.
func (h *hr8466Supervisor) verifyReaped() {
	if h.cmd == nil || h.cmd.Process == nil {
		return
	}
	if err := syscall.Kill(h.cmd.Process.Pid, 0); err != syscall.ESRCH {
		h.t.Errorf("health child pid %d not reaped after supervisor-owned Wait: err=%v", h.cmd.Process.Pid, err)
	}
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

// Q6: the exact accepted non-success-result / classification combination table.
func TestSystemdSidecarHealthResultAndStateCombinationTable(t *testing.T) {
	type row struct {
		name                                   string
		activeState, subState, result, mainPID string
		want                                   sidecarObservation
	}
	rows := []row{
		// inactive/dead x success/exit-code x PID0/PID2.
		{"inactive-dead-success-pid0", "inactive", "dead", "success", "0", sidecarAbsent},
		{"inactive-dead-success-pid2", "inactive", "dead", "success", "2", sidecarUnknown},
		{"inactive-dead-exitcode-pid0", "inactive", "dead", "exit-code", "0", sidecarAbsent},
		{"inactive-dead-exitcode-pid2", "inactive", "dead", "exit-code", "2", sidecarUnknown},
		// inactive with a running substate and an unsupported ActiveState.
		{"inactive-running-success-pid0", "inactive", "running", "success", "0", sidecarUnknown},
		{"inactive-running-exitcode-pid0", "inactive", "running", "exit-code", "0", sidecarUnknown},
		{"unsupported-active-state-pid2", "reloading", "start", "success", "2", sidecarUnknown},
		// All four ActiveStates x PID0/PID2 with SubState=start / Result=exit-code.
		{"active-start-exitcode-pid0", "active", "start", "exit-code", "0", sidecarPresentUnhealthy},
		{"active-start-exitcode-pid2", "active", "start", "exit-code", "2", sidecarPresentUnhealthy},
		{"activating-start-exitcode-pid0", "activating", "start", "exit-code", "0", sidecarPresentUnhealthy},
		{"activating-start-exitcode-pid2", "activating", "start", "exit-code", "2", sidecarPresentUnhealthy},
		{"deactivating-start-exitcode-pid0", "deactivating", "start", "exit-code", "0", sidecarPresentUnhealthy},
		{"deactivating-start-exitcode-pid2", "deactivating", "start", "exit-code", "2", sidecarPresentUnhealthy},
		{"failed-start-exitcode-pid0", "failed", "start", "exit-code", "0", sidecarPresentUnhealthy},
		{"failed-start-exitcode-pid2", "failed", "start", "exit-code", "2", sidecarPresentUnhealthy},
		// Running active with a non-success result is still unhealthy.
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
// actually entered.  Every path must return unknown, terminate within the
// real-query plus teardown bound, and reap the started helper identity.
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
				deadline := time.Now().Add(hr8466NormalBarrier)
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
			case <-time.After(hr8466QueryBound):
				t.Fatal("query did not terminate within its real-query plus teardown bound")
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
				fields := strings.Fields(entered[0])
				if len(fields) == 0 {
					t.Fatalf("empty entered pid receipt %q", entered[0])
				}
				pid, err := strconv.Atoi(fields[0])
				if err != nil || pid <= 0 {
					t.Fatalf("bad entered pid %q (err=%v)", entered[0], err)
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
// complete healthy pair.  Negative assertions run while the next observation
// is gated, proving the prior record was completely consumed.
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
	h := startHR8466Supervisor(t, f, hr8466NormalBarrier, hr8466NormalBarrier)
	defer h.teardown()

	// Record 0: no READY before any sidecar observation at all.
	f.waitChildWaiting(t, 0)
	if got := h.count("READY=1"); got != 0 {
		t.Fatalf("READY before any complete sidecar health: %d", got)
	}
	f.setState(states[0])
	f.releaseChild(0)
	f.waitChildEmitted(t, 0)
	h.waitProbes(1, hr8466NormalBarrier)

	// Record 1: gate the observation so the negative assertion runs after
	// record 0 was fully consumed and before record 1 is acted on.
	h.armProbeGate()
	f.waitChildWaiting(t, 1)
	f.setState(states[1])
	f.releaseChild(1)
	f.waitChildEmitted(t, 1)
	h.waitProbeGateEntered(hr8466NormalBarrier)
	switch ordering {
	case "connector-first":
		if got := h.count("READY=1"); got != 0 {
			t.Fatalf("early READY with an unhealthy sidecar: calls=%v", h.calls())
		}
		if got := h.count("WATCHDOG=1"); got != 0 {
			t.Fatalf("early WATCHDOG with an unhealthy sidecar: calls=%v", h.calls())
		}
	case "sidecar-first":
		h.waitCount("READY=1", 1, hr8466NormalBarrier)
		if got := h.count("WATCHDOG=1"); got != 0 {
			t.Fatalf("WATCHDOG before a subsequent complete healthy pair: calls=%v", h.calls())
		}
	}
	h.releaseProbeGate()
	if ordering == "connector-first" {
		h.waitCount("READY=1", 1, hr8466NormalBarrier)
	} else {
		h.waitCount("WATCHDOG=1", 1, hr8466NormalBarrier)
	}

	// Record 2: gate again to prove no extra READY/WATCHDOG before the pair.
	h.armProbeGate()
	f.waitChildWaiting(t, 2)
	f.setState("healthy")
	f.releaseChild(2)
	f.waitChildEmitted(t, 2)
	h.waitProbeGateEntered(hr8466NormalBarrier)
	if got := h.count("READY=1"); got != 1 {
		t.Fatalf("READY count before the third pair = %d, want 1; calls=%v", got, h.calls())
	}
	switch ordering {
	case "connector-first":
		if got := h.count("WATCHDOG=1"); got != 0 {
			t.Fatalf("WATCHDOG before the third complete pair: calls=%v", h.calls())
		}
	case "sidecar-first":
		if got := h.count("WATCHDOG=1"); got != 1 {
			t.Fatalf("WATCHDOG count before the third pair = %d, want 1; calls=%v", got, h.calls())
		}
	}
	h.releaseProbeGate()
	h.waitCount("WATCHDOG=1", 1, hr8466NormalBarrier)

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
	if !hr8466HasEvent(f.productEvents(), "show-done:"+states[0]) {
		t.Fatalf("fixture did not complete the expected initial observation; events=%v", f.productEvents())
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
			h := startHR8466Supervisor(t, f, hr8466NormalBarrier, hr8466NormalBarrier)
			defer h.teardown()

			f.waitChildWaiting(t, 0)
			f.setState(bad)
			f.releaseChild(0)
			f.waitChildEmitted(t, 0)
			h.waitProbes(1, hr8466NormalBarrier)

			// Gate the recovery observation: the negative assertion runs only
			// after the bad record was completely consumed.
			h.armProbeGate()
			f.waitChildWaiting(t, 1)
			f.setState("healthy")
			f.releaseChild(1)
			f.waitChildEmitted(t, 1)
			h.waitProbeGateEntered(hr8466NormalBarrier)
			if got := h.count("READY=1"); got != 0 {
				t.Fatalf("early READY on %s: calls=%v", name, h.calls())
			}
			if got := h.count("WATCHDOG=1"); got != 0 {
				t.Fatalf("early WATCHDOG on %s: calls=%v", name, h.calls())
			}
			h.releaseProbeGate()
			h.waitCount("READY=1", 1, hr8466NormalBarrier)

			f.waitChildWaiting(t, 2)
			f.setState("healthy")
			f.releaseChild(2)
			f.waitChildEmitted(t, 2)
			h.waitProbes(3, hr8466NormalBarrier)
			h.waitCount("WATCHDOG=1", 1, hr8466NormalBarrier)
			assertHR8466NoFixtureLeak(t, f)
		})
	}
}

// H2 never-recovers: repeated records released across the startup interval
// must not refresh the original absolute deadline.  STOPPING must occur at the
// original 250ms deadline within the accepted 2s scheduling allowance.
func TestConsumerNeverRecoversHonorsOriginalDeadline(t *testing.T) {
	f := newHR8466Fixture(t)
	f.setPlan("ready", "healthy", "healthy", "healthy", "healthy", "healthy")
	f.setState("unhealthy")
	start := time.Now()
	h := startHR8466Supervisor(t, f, 250*time.Millisecond, 10*time.Second)
	defer h.teardown()

	// Release repeated records over the startup interval from a helper
	// goroutine (no testing.T calls) so the deadline measurement is not
	// serialized behind the child barriers.  The goroutine is joined below.
	released := make(chan struct{})
	go func() {
		defer close(released)
		for i := 0; i < 6; i++ {
			if !waitHR8466Line(f.events, fmt.Sprintf("waiting:%d", i), hr8466NormalBarrier) {
				return
			}
			f.releaseChild(i)
			time.Sleep(40 * time.Millisecond)
		}
	}()

	h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
	elapsed := time.Since(start)
	if elapsed < 200*time.Millisecond {
		t.Fatalf("startup deadline fired before its 250ms bound: %s", elapsed)
	}
	if elapsed > 250*time.Millisecond+hr8466NormalBarrier {
		t.Fatalf("STOPPING delayed beyond the original 250ms deadline plus the %s scheduling allowance: %s", hr8466NormalBarrier, elapsed)
	}
	if got := h.count("READY=1"); got != 0 {
		t.Fatalf("READY while the sidecar never became healthy: calls=%v", h.calls())
	}
	if got := h.count("WATCHDOG=1"); got != 0 {
		t.Fatalf("WATCHDOG before READY: calls=%v", h.calls())
	}
	if code := h.waitDone(hr8466AdmissionBound); code != 0 {
		t.Fatalf("never-recovers admission exit code=%d, want 0", code)
	}
	select {
	case <-released:
	case <-time.After(hr8466NormalBarrier):
		t.Fatal("release helper did not join within the normal barrier bound")
	}
	assertHR8466NoFixtureLeak(t, f)
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
			h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
			defer h.teardown()

			f.waitChildWaiting(t, 0)
			f.setState("healthy")
			f.releaseChild(0)
			f.waitChildEmitted(t, 0)
			h.waitProbes(1, hr8466NormalBarrier)
			h.waitCount("READY=1", 1, hr8466NormalBarrier)

			f.waitChildWaiting(t, 1)
			f.setState("healthy")
			f.releaseChild(1)
			f.waitChildEmitted(t, 1)
			h.waitProbes(2, hr8466NormalBarrier)
			h.waitCount("WATCHDOG=1", 1, hr8466NormalBarrier)

			f.waitChildWaiting(t, 2)
			f.setState(bad)
			f.releaseChild(2)
			f.waitChildEmitted(t, 2)
			// STOPPING proves the bad observation was consumed by the real
			// consumer; no further WATCHDOG may follow it.
			h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
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

// H3 query-deadline representative: after READY/WATCHDOG a real observation
// enters the delayed fixture, completes at the real one-second deadline with
// unknown, and drives STOPPING with no further WATCHDOG before test teardown.
func TestConsumerQueryDeadlineAfterReadySuppressesWatchdog(t *testing.T) {
	f := newHR8466Fixture(t)
	f.setPlan("ready", "healthy", "healthy")
	h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
	defer h.teardown()

	f.waitChildWaiting(t, 0)
	f.setState("healthy")
	f.releaseChild(0)
	f.waitChildEmitted(t, 0)
	h.waitProbes(1, hr8466NormalBarrier)
	h.waitCount("READY=1", 1, hr8466NormalBarrier)

	f.waitChildWaiting(t, 1)
	f.setState("healthy")
	f.releaseChild(1)
	f.waitChildEmitted(t, 1)
	h.waitProbes(2, hr8466NormalBarrier)
	h.waitCount("WATCHDOG=1", 1, hr8466NormalBarrier)

	// The third observation enters the real delayed query.  STOPPING only
	// follows a completed (timed-out) observation, so the recorded probe
	// duration is the real query-deadline evidence; this is robust whether or
	// not the admission re-observation has already started.
	f.waitChildWaiting(t, 2)
	f.setState("delay")
	f.releaseChild(2)
	f.waitChildEmitted(t, 2)
	h.waitCount("STOPPING=1", 1, hr8466QueryBound)
	duration, result, ok := h.probe.lastDuration()
	if !ok || result != sidecarUnknown {
		t.Fatalf("query-deadline observation result=%d ok=%v, want sidecarUnknown", result, ok)
	}
	if duration < 900*time.Millisecond || duration > hr8466QueryBound {
		t.Fatalf("query-deadline consumer observation duration=%s, want the real 1s deadline within %s", duration, hr8466QueryBound)
	}
	if got := h.count("WATCHDOG=1"); got != 1 {
		t.Fatalf("WATCHDOG after a timed-out observation = %d, want 1; calls=%v", got, h.calls())
	}
	if got := h.count("READY=1"); got != 1 {
		t.Fatalf("READY after a timed-out observation = %d, want 1; calls=%v", got, h.calls())
	}
	assertHR8466NoFixtureLeak(t, f)
}

// H3 cancellation representative: cancel the query actually in flight after it
// has entered, and confirm no further WATCHDOG and no continued readiness.
func TestConsumerCancelledObservationAfterReadySuppressesWatchdog(t *testing.T) {
	f := newHR8466Fixture(t)
	f.setPlan("ready", "healthy", "healthy")
	h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
	defer h.teardown()

	f.waitChildWaiting(t, 0)
	f.setState("healthy")
	f.releaseChild(0)
	f.waitChildEmitted(t, 0)
	h.waitProbes(1, hr8466NormalBarrier)
	h.waitCount("READY=1", 1, hr8466NormalBarrier)

	f.waitChildWaiting(t, 1)
	f.setState("healthy")
	f.releaseChild(1)
	f.waitChildEmitted(t, 1)
	h.waitProbes(2, hr8466NormalBarrier)
	h.waitCount("WATCHDOG=1", 1, hr8466NormalBarrier)

	f.waitChildWaiting(t, 2)
	f.setState("delay")
	f.releaseChild(2)
	f.waitChildEmitted(t, 2)
	// The third show invocation is the in-flight one for record 2.
	deadline := time.Now().Add(hr8466NormalBarrier)
	for len(hr8466Events(f.entered)) < 3 {
		if time.Now().After(deadline) {
			t.Fatal("in-flight query never entered")
		}
		time.Sleep(time.Millisecond)
	}
	h.cancel()
	h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
	if got := h.count("WATCHDOG=1"); got != 1 {
		t.Fatalf("continued WATCHDOG after cancelled observation: %d", got)
	}
	// Let the detached (Background) admission observe the real absence so the
	// supervisor terminates instead of retrying the still-present sidecar.
	f.setState("absent")
	if code := h.waitDone(hr8466AdmissionBound); code != 0 && code != 1 {
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
		h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
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
			if line == "show-done:absent" {
				showAbsent = i
			}
		}
		if showAbsent < 0 || term < showAbsent {
			t.Fatalf("child cleanup preceded the completed observed absence: events=%v", events)
		}
		if code := h.waitDone(hr8466AdmissionBound); code != 0 {
			t.Fatalf("absent admission exit code=%d, want 0", code)
		}
		assertHR8466NoFixtureLeak(t, f)
	})

	for _, initial := range []string{"healthy", "unhealthy"} {
		initial := initial
		t.Run("stop-then-still-present-then-absent-"+initial, func(t *testing.T) {
			f := newHR8466Fixture(t)
			f.setPlan("stopping")
			f.setState(initial)
			f.holdPresent()
			h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
			defer h.teardown()

			f.waitChildWaiting(t, 0)
			f.releaseChild(0)
			events := f.waitStop(t)
			stopIndex := firstHR8466Index(events, "stop")
			// A completed still-present re-observation must follow the stop.
			deadline := time.Now().Add(hr8466NormalBarrier)
			for {
				events = f.productEvents()
				completedAfterStop := 0
				for i := stopIndex + 1; i < len(events); i++ {
					if events[i] == "show-done:"+initial {
						completedAfterStop++
					}
				}
				if completedAfterStop >= 1 {
					break
				}
				if time.Now().After(deadline) {
					t.Fatalf("no completed still-present re-observation after stop: events=%v", events)
				}
				time.Sleep(time.Millisecond)
			}
			if hr8466HasEvent(events, "term") {
				t.Fatalf("child was cleaned up while the sidecar was still present: events=%v", events)
			}
			if got := hr8466EventCount(events, "stop"); got != 1 {
				t.Fatalf("stop count = %d within the admission attempt, want 1; events=%v", got, events)
			}

			// Release absence only now; a completed absence observation must
			// precede the product child TERM.
			f.setState("absent")
			events = f.waitTerm(t)
			term := firstHR8466Index(events, "term")
			showAbsent := -1
			for i, line := range events {
				if line == "show-done:absent" {
					showAbsent = i
				}
			}
			if showAbsent < 0 || showAbsent > term {
				t.Fatalf("product TERM preceded a completed absence observation: events=%v", events)
			}
			if got := hr8466EventCount(events, "stop"); got != 1 {
				t.Fatalf("stop count = %d after absence, want 1; events=%v", got, events)
			}
			if code := h.waitDone(hr8466AdmissionBound); code != 0 {
				t.Fatalf("clean admission exit code=%d, want 0", code)
			}
			assertHR8466NoFixtureLeak(t, f)
		})
	}
}

// H6: stop failure, an initially unknown query, and present -> stop -> unknown
// all deny admission and never perform product child cleanup.  Each negative
// observation must complete before the no-cleanup assertion; the initially
// unknown query issues no stop.
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
			h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
			defer h.teardown()

			f.waitChildWaiting(t, 0)
			f.releaseChild(0)
			switch {
			case tc.fail:
				f.waitEvent(t, "stop", hr8466NormalBarrier)
				f.waitEvent(t, "stop-failed", hr8466NormalBarrier)
			case tc.after != "":
				f.waitEvent(t, "stop", hr8466NormalBarrier)
				f.setState(tc.after)
				f.waitShowComplete(t, tc.after, 1, hr8466NormalBarrier)
			default:
				f.waitShowComplete(t, tc.initial, 1, hr8466NormalBarrier)
			}
			// The negative observation is complete; now assert no product
			// child cleanup, before the explicit test teardown boundary.
			events := f.productEvents()
			if hr8466HasEvent(events, "term") {
				t.Fatalf("negative admission performed product child cleanup: events=%v", events)
			}
			if tc.name == "initially-unknown" && hr8466HasEvent(events, "stop") {
				t.Fatalf("unknown query issued a stop: events=%v", events)
			}
			f.exitChild()
			if code := h.waitDone(hr8466AdmissionBound); code != 1 {
				t.Fatalf("negative admission exit code=%d, want 1", code)
			}
			if hr8466HasEvent(f.productEvents(), "term") {
				t.Fatalf("negative admission terminated the product child: events=%v", f.productEvents())
			}
			assertHR8466NoFixtureLeak(t, f)
		})
	}
}

// H7: the real five-second admission timeout, supplied-context cancellation of
// an entered re-observation, the supervisor's detached (Background) admission
// context, and parent cancellation that must not substitute for it at the stop
// seam.
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
		if elapsed < 4500*time.Millisecond || elapsed > hr8466AdmissionBound {
			t.Fatalf("admission timeout elapsed=%s, want the real 5s within the %s admission+teardown bound", elapsed, hr8466AdmissionBound)
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
		// Cancel only after a re-observation has actually entered, never on the
		// stop event.
		go func() {
			if !waitHR8466EventPrefixBool(f.events, "stop", hr8466NormalBarrier) {
				return
			}
			deadline := time.Now().Add(hr8466NormalBarrier)
			for len(hr8466Events(f.entered)) < 2 && time.Now().Before(deadline) {
				time.Sleep(time.Millisecond)
			}
			cancel()
		}()
		start := time.Now()
		if removeSidecarAdmission(ctx, systemdSidecarState, systemdStopSidecar) {
			t.Fatal("cancelled re-observation falsely claimed absent")
		}
		if elapsed := time.Since(start); elapsed > hr8466NormalBarrier {
			t.Fatalf("cancellation not honored during the entered re-observation: %s", elapsed)
		}
		for _, line := range hr8466Events(f.entered) {
			fields := strings.Fields(line)
			if len(fields) == 0 {
				t.Fatalf("empty entered pid receipt %q", line)
			}
			pid, err := strconv.Atoi(fields[0])
			if err != nil || pid <= 0 {
				t.Fatalf("bad entered pid %q (err=%v)", line, err)
			}
			if err := syscall.Kill(pid, 0); err != syscall.ESRCH {
				t.Fatalf("re-observation helper pid %d not reaped: err=%v", pid, err)
			}
		}
		assertHR8466NoFixtureLeak(t, f)
	})

	t.Run("supervisor-uses-detached-context", func(t *testing.T) {
		f := newHR8466Fixture(t)
		f.setPlan("ready", "healthy")
		f.setState("healthy")
		h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
		defer h.teardown()
		f.waitChildWaiting(t, 0)
		f.releaseChild(0)
		f.waitChildEmitted(t, 0)
		h.waitCount("READY=1", 1, hr8466NormalBarrier)
		h.cancel()
		deadline := time.Now().Add(hr8466NormalBarrier)
		for !h.probe.sawDetachedContext() && time.Now().Before(deadline) {
			time.Sleep(time.Millisecond)
		}
		if !h.probe.sawDetachedContext() {
			t.Fatal("supervisor admission did not use a detached context")
		}
		assertHR8466NoFixtureLeak(t, f)
	})

	t.Run("parent-cancel-uses-background-stop", func(t *testing.T) {
		f := newHR8466Fixture(t)
		f.setPlan()
		f.setState("healthy")
		h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
		defer h.teardown()

		// Cancel the supervisor parent while the sidecar is present.  The stop
		// must still be performed through the production Background admission
		// context; with the parent context substituted there is no stop.
		h.cancel()
		f.waitStop(t)
		events := f.waitTerm(t)
		term := firstHR8466Index(events, "term")
		showAbsent := -1
		for i, line := range events {
			if line == "show-done:absent" {
				showAbsent = i
			}
		}
		if showAbsent < 0 || showAbsent > term {
			t.Fatalf("Background admission did not observe absence before the product TERM: events=%v", events)
		}
		if code := h.waitDone(hr8466AdmissionBound); code != 0 {
			t.Fatalf("parent-cancel admission exit code=%d, want 0", code)
		}
		assertHR8466NoFixtureLeak(t, f)
	})
}
