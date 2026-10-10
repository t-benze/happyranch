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
	"runtime"
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
			released, exiting := waitHR8466LineOrExit(barrier, strconv.Itoa(i), exitPath, hr8466NormalBarrier)
			if exiting {
				appendHR8466Event(events, "child-exit")
				os.Exit(0)
			}
			if !released {
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

// waitHR8466LineOrStop is the goroutine-safe, stop-aware form of
// waitHR8466Line.  A helper that drives the real child through barrier releases
// must stop waiting as soon as the supervisor has terminated: once the single
// Wait has reaped the child, a later “waiting:<i>“ line is physically
// impossible, so waiting a full barrier for it can lose a race with the
// caller's identical join bound.  The line is still returned when it appears.
func waitHR8466LineOrStop(path, value string, stop <-chan struct{}, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for {
		for _, line := range hr8466Events(path) {
			if line == value {
				return true
			}
		}
		select {
		case <-stop:
			return false
		default:
		}
		if time.Now().After(deadline) {
			return false
		}
		time.Sleep(time.Millisecond)
	}
}

// waitHR8466LineOrExit is the child-side barrier wait.  It also observes the
// test-owned exit file so a real child can exit spontaneously while it is
// parked at any barrier, which is what the R1/R2 child-exit regressions need.
// It returns (released, exiting); an exit request wins over a pending release
// so the two signals can never both be consumed.
func waitHR8466LineOrExit(barrier, value, exitPath string, timeout time.Duration) (bool, bool) {
	deadline := time.Now().Add(timeout)
	for {
		if exitPath != "" {
			if _, err := os.Stat(exitPath); err == nil {
				return false, true
			}
		}
		for _, line := range hr8466Events(barrier) {
			if line == value {
				return true, false
			}
		}
		if time.Now().After(deadline) {
			return false, false
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

// holdFirstUnhealthyShow rewrites the fixture systemctl stand-in so the first
// nonhealthy show stays alive after its completion receipt until the test
// releases it.  The receipt (“show-done“) is written before the shell exits
// and therefore before the production parser/cmd.Output boundary returns, so a
// test that arms its gate on that receipt can capture the wrong observation.
// This reproduces the reviewer's delayed-completion counterexample.
func (f *hr8466Fixture) holdFirstUnhealthyShow() {
	f.t.Helper()
	script := strings.Replace(hr8466FixtureScript,
		`echo "show-done:$state" >> "$HR8466_EVENTS"`,
		`echo "show-done:$state" >> "$HR8466_EVENTS"
    if [ "$state" = unhealthy ]; then while [ ! -f "$HR8466_STATE.release" ]; do /bin/sleep 0.001; done; fi`,
		1)
	if err := os.WriteFile(filepath.Join(filepath.Dir(f.state), "systemctl"), []byte(script), 0700); err != nil {
		f.t.Fatal(err)
	}
}

func (f *hr8466Fixture) releaseHeldShow() { writeHR8466File(f.state+".release", "release") }

// hr8466ScannerGoroutineStack reports whether any live goroutine is inside
// scanChildHealth.  The production helper is joined on supervisor return, so a
// surviving goroutine is the exact owned-helper leak this harness forbids.
func hr8466ScannerGoroutineStack() bool {
	buffer := make([]byte, 1<<20)
	count := runtime.Stack(buffer, true)
	return strings.Contains(string(buffer[:count]), "scanChildHealth(")
}

// hr8466BlockedScannerStack reports whether the owned scanner has decoded a
// record and is waiting on its cancellation-aware delivery select, which is the
// exact pending-delivery state the supervisor must unblock and join.
func hr8466BlockedScannerStack() bool {
	buffer := make([]byte, 1<<20)
	count := runtime.Stack(buffer, true)
	for _, goroutine := range strings.Split(string(buffer[:count]), "\n\n") {
		if !strings.Contains(goroutine, "scanChildHealth(") {
			continue
		}
		if strings.Contains(goroutine, "[select]") || strings.Contains(goroutine, "[chan send]") {
			return true
		}
	}
	return false
}

func waitHR8466BlockedScanner(t *testing.T, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for !hr8466BlockedScannerStack() {
		if time.Now().After(deadline) {
			t.Fatal("the owned health scanner never reached a pending record delivery")
		}
		time.Sleep(time.Millisecond)
	}
}

func assertHR8466ScannerJoined(t *testing.T, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for hr8466ScannerGoroutineStack() {
		if time.Now().After(deadline) {
			buffer := make([]byte, 1<<20)
			count := runtime.Stack(buffer, true)
			t.Fatalf("the owned health scanner was not joined before supervisor return:\n%s", string(buffer[:count]))
		}
		time.Sleep(time.Millisecond)
	}
}

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
	mu          sync.Mutex
	results     []sidecarObservation
	background  []bool
	durations   []time.Duration
	armGate     bool
	gateIn      chan int
	gateOut     chan struct{}
	gateRelease *sync.Once
}

func newHR8466ProbeLog() *hr8466ProbeLog {
	return &hr8466ProbeLog{
		gateIn:      make(chan int, 1),
		gateOut:     make(chan struct{}, 1),
		gateRelease: &sync.Once{},
	}
}

func (p *hr8466ProbeLog) armNext() {
	p.mu.Lock()
	// A fresh once per armed gate keeps release idempotent and safe against a
	// double release from both the test and failure-path teardown.
	p.gateRelease = &sync.Once{}
	p.armGate = true
	p.mu.Unlock()
}

// releaseGate is the idempotent, failure-safe release for an armed or entered
// query gate.  It never blocks and never panics on a repeated release, so
// teardown can always unblock a stranded supervisor even after an assertion
// abort.
func (p *hr8466ProbeLog) releaseGate() {
	p.mu.Lock()
	once := p.gateRelease
	p.mu.Unlock()
	if once == nil {
		return
	}
	once.Do(func() { p.gateOut <- struct{}{} })
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

// resultAt returns the recorded duration and result of the observation at the
// given zero-based position, so a test can reason about the pre-READY health
// query independently of later admission re-observations.
func (p *hr8466ProbeLog) resultAt(index int) (time.Duration, sidecarObservation, bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if index < 0 || index >= len(p.durations) {
		return 0, sidecarUnknown, false
	}
	return p.durations[index], p.results[index], true
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
	// Idempotent: an explicit release and a failure-path teardown release of
	// the same armed gate must not panic or block.
	h.probe.releaseGate()
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
// TEST_TEARDOWN_BEGIN before any kill, releases any armed/entered query gate so
// a failure-path assertion cannot strand the supervisor, cancels it, and joins
// the supervisor's own cmd.Wait result within the admission+teardown bound.  A
// failed join is reported; a child ESRCH alone is never treated as a join.
func (h *hr8466Supervisor) teardown() {
	if h.finished {
		return
	}
	h.fixture.beginTeardown()
	// Failure-safe, idempotent gate release: an entered completed-query gate
	// leaves the supervisor blocked in the health query and cannot be unblocked
	// by cancellation alone.
	h.probe.releaseGate()
	h.cancel()
	if h.cmd != nil && h.cmd.Process != nil {
		_ = h.cmd.Process.Kill()
	}
	select {
	case h.code = <-h.done:
		h.finished = true
	case <-time.After(hr8466AdmissionBound):
		// The product waitDone already fails the test when termination is
		// required; teardown must never itself report success.  Report the
		// failed join explicitly rather than returning silently.
		h.t.Errorf("teardown returned after %s without joining the supervisor; calls=%v probes=%d",
			hr8466AdmissionBound, h.calls(), h.probe.count())
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
	// Once the supervisor has terminated the child is reaped, so no further
	// waiting:<i> line can arrive; stopRelease lets the helper join promptly
	// instead of waiting a full barrier for a line that cannot appear (which
	// races the identical join bound below).
	released := make(chan struct{})
	stopRelease := make(chan struct{})
	go func() {
		defer close(released)
		for i := 0; i < 6; i++ {
			if !waitHR8466LineOrStop(f.events, fmt.Sprintf("waiting:%d", i), stopRelease, hr8466NormalBarrier) {
				return
			}
			f.releaseChild(i)
			select {
			case <-time.After(40 * time.Millisecond):
			case <-stopRelease:
				return
			}
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
	close(stopRelease)
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

// ---------------------------------------------------------------------------
// seq264 terminal-readiness latch and absolute startup-deadline cases.
//
// The supervisor latches a terminal readiness decision independently of
// permission to signal or reap the child: expiry, failure, cancellation or a
// stop decision permanently ends READY/WATCHDOG even while sidecar admission
// stays unknown and the child therefore cannot be cleaned up.
// ---------------------------------------------------------------------------

// assertHR8466NoReadyWatchdog holds a bounded window in which the terminal
// latch must keep the notification counts frozen at the given values.
func assertHR8466NoReadyWatchdog(t *testing.T, h *hr8466Supervisor, ready, watchdog int, window time.Duration) {
	t.Helper()
	deadline := time.Now().Add(window)
	for time.Now().Before(deadline) {
		if got := h.count("READY=1"); got != ready {
			t.Fatalf("READY count=%d want %d; calls=%v", got, ready, h.calls())
		}
		if got := h.count("WATCHDOG=1"); got != watchdog {
			t.Fatalf("WATCHDOG count=%d want %d; calls=%v", got, watchdog, h.calls())
		}
		time.Sleep(2 * time.Millisecond)
	}
}

// assertHR8466TermAfterAbsence requires any product child TERM to follow a
// completed confirmed-absence observation; readiness is never permission to
// clean up the child.
func assertHR8466TermAfterAbsence(t *testing.T, events []string) {
	t.Helper()
	term := firstHR8466Index(events, "term")
	if term < 0 {
		return
	}
	absent := firstHR8466Index(events, "show-done:absent")
	if absent < 0 || absent > term {
		t.Fatalf("product child TERM without a preceding completed absence observation: events=%v", events)
	}
}

// assertHR8466HelpersReaped proves every recorded real query helper is gone.
func assertHR8466HelpersReaped(t *testing.T, f *hr8466Fixture) {
	t.Helper()
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
			t.Fatalf("query helper pid %d not reaped: err=%v", pid, err)
		}
	}
}

// Case 1: an expired absolute startup deadline plus refused admission must not
// let a later valid record with healthy sidecar data publish READY/WATCHDOG,
// and terminal readiness must never become permission to clean up the child
// while sidecar absence is unconfirmed.
func TestConsumerExpiredStartupRefusedAdmissionRejectsLateReady(t *testing.T) {
	t.Run("unknown-admission", func(t *testing.T) {
		f := newHR8466Fixture(t)
		f.setPlan("ready", "healthy")
		f.setState("garbage")
		h := startHR8466Supervisor(t, f, 250*time.Millisecond, 10*time.Second)
		defer h.teardown()

		f.waitChildWaiting(t, 0)
		f.releaseChild(0)
		f.waitChildEmitted(t, 0)
		h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
		// The expired-deadline admission observation completes as unknown.
		h.waitProbes(2, hr8466NormalBarrier)
		if got := h.count("READY=1"); got != 0 {
			t.Fatalf("READY before the late record: calls=%v", h.calls())
		}
		if hr8466HasEvent(f.productEvents(), "term") {
			t.Fatalf("child cleanup while admission was unknown: events=%v", f.productEvents())
		}

		// Late valid child record plus healthy sidecar data.
		f.waitChildWaiting(t, 1)
		f.setState("healthy")
		f.releaseChild(1)
		f.waitChildEmitted(t, 1)
		assertHR8466NoReadyWatchdog(t, h, 0, 0, 300*time.Millisecond)
		events := f.waitEvent(t, "show-done:absent", hr8466NormalBarrier)
		assertHR8466TermAfterAbsence(t, events)
		assertHR8466NoFixtureLeak(t, f)
	})
	t.Run("stop-failure", func(t *testing.T) {
		f := newHR8466Fixture(t)
		f.setPlan("ready", "healthy")
		f.setState("unhealthy")
		f.requireStopFailure()
		h := startHR8466Supervisor(t, f, 250*time.Millisecond, 10*time.Second)
		defer h.teardown()

		f.waitChildWaiting(t, 0)
		f.releaseChild(0)
		f.waitChildEmitted(t, 0)
		h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
		f.waitEvent(t, "stop", hr8466NormalBarrier)
		f.waitEvent(t, "stop-failed", hr8466NormalBarrier)
		if got := h.count("READY=1"); got != 0 {
			t.Fatalf("READY before the late record: calls=%v", h.calls())
		}

		f.waitChildWaiting(t, 1)
		f.setState("healthy")
		f.releaseChild(1)
		f.waitChildEmitted(t, 1)
		assertHR8466NoReadyWatchdog(t, h, 0, 0, 300*time.Millisecond)
		deadline := time.Now().Add(hr8466NormalBarrier)
		for hr8466EventCount(f.productEvents(), "stop-failed") < 2 {
			if time.Now().After(deadline) {
				t.Fatalf("late record did not trigger a completed refused admission: events=%v", f.productEvents())
			}
			time.Sleep(time.Millisecond)
		}
		if hr8466HasEvent(f.productEvents(), "term") {
			t.Fatalf("child cleanup while admission was refused: events=%v", f.productEvents())
		}
		assertHR8466NoFixtureLeak(t, f)
	})
}

// Case 2: after a real READY and WATCHDOG, a failed composite health with
// refused admission latches STOPPING permanently.  A later valid record with
// healthy sidecar data must never resume READY/WATCHDOG, and the still-present
// sidecar must keep withholding child cleanup until absence is confirmed.
func TestConsumerStoppingTerminalPreventsLateReadyAndWatchdog(t *testing.T) {
	f := newHR8466Fixture(t)
	f.setPlan("ready", "healthy", "healthy", "healthy")
	h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
	defer h.teardown()

	f.waitChildWaiting(t, 0)
	f.setState("healthy")
	f.releaseChild(0)
	f.waitChildEmitted(t, 0)
	h.waitCount("READY=1", 1, hr8466NormalBarrier)

	f.waitChildWaiting(t, 1)
	f.setState("healthy")
	f.releaseChild(1)
	f.waitChildEmitted(t, 1)
	h.waitCount("WATCHDOG=1", 1, hr8466NormalBarrier)

	// Failed composite health with admission refusal (unknown observation).
	f.waitChildWaiting(t, 2)
	f.setState("garbage")
	f.releaseChild(2)
	f.waitChildEmitted(t, 2)
	h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
	f.waitShowComplete(t, "garbage", 2, hr8466NormalBarrier)
	if hr8466HasEvent(f.productEvents(), "term") {
		t.Fatalf("child cleanup while admission was unknown: events=%v", f.productEvents())
	}
	before := h.count("WATCHDOG=1")

	// Late valid record with healthy sidecar data must not revive readiness.
	f.waitChildWaiting(t, 3)
	f.setState("healthy")
	f.releaseChild(3)
	f.waitChildEmitted(t, 3)
	assertHR8466NoReadyWatchdog(t, h, 1, before, 300*time.Millisecond)
	events := f.waitEvent(t, "show-done:absent", hr8466NormalBarrier)
	if got := h.count("READY=1"); got != 1 {
		t.Fatalf("READY resumed after STOPPING: count=%d calls=%v", got, h.calls())
	}
	if got := h.count("WATCHDOG=1"); got != before {
		t.Fatalf("WATCHDOG resumed after STOPPING: count=%d want=%d calls=%v", got, before, h.calls())
	}
	assertHR8466TermAfterAbsence(t, events)
	if code := h.waitDone(hr8466AdmissionBound); code != 0 {
		t.Fatalf("supervisor exit code=%d, want 0", code)
	}
	assertHR8466NoFixtureLeak(t, f)
}

// Case 3: a pre-READY health observation that enters before the original
// absolute startup deadline but completes after it must never publish READY,
// must not refresh the deadline, and must stay bounded; the query helper must
// be reaped.  Both pre-READY query paths (initial ready record and later
// healthy record) are exercised.
func TestConsumerHealthQuerySpanningStartupDeadlineCannotReady(t *testing.T) {
	const crossingStartup = 1500 * time.Millisecond
	const crossingEntryBound = time.Second
	const boundedStartup = 600 * time.Millisecond

	t.Run("ready-record-path", func(t *testing.T) {
		f := newHR8466Fixture(t)
		f.setPlan("ready")
		f.setState("healthy")
		start := time.Now()
		h := startHR8466Supervisor(t, f, crossingStartup, 10*time.Second)
		defer h.teardown()

		h.armProbeGate()
		f.waitChildWaiting(t, 0)
		f.releaseChild(0)
		f.waitChildEmitted(t, 0)
		index := h.waitProbeGateEntered(hr8466NormalBarrier)
		duration, result, ok := h.probe.resultAt(index - 1)
		if !ok || result != sidecarPresentHealthy {
			t.Fatalf("gated pre-READY observation result=%d ok=%v, want healthy", result, ok)
		}
		if elapsed := time.Since(start); elapsed >= crossingStartup || elapsed >= crossingEntryBound {
			t.Fatalf("gated pre-READY observation entered after its deadline: %s", elapsed)
		}
		if duration > crossingEntryBound {
			t.Fatalf("gated pre-READY observation duration=%s", duration)
		}
		// Release the healthy result only after the original deadline.
		time.Sleep(time.Until(start.Add(crossingStartup)) + 50*time.Millisecond)
		h.releaseProbeGate()
		assertHR8466NoReadyWatchdog(t, h, 0, 0, 250*time.Millisecond)
		h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
		assertHR8466NoReadyWatchdog(t, h, 0, 0, 250*time.Millisecond)
		assertHR8466HelpersReaped(t, f)
		assertHR8466NoFixtureLeak(t, f)
	})

	t.Run("healthy-record-path", func(t *testing.T) {
		f := newHR8466Fixture(t)
		f.setPlan("ready", "healthy")
		f.setState("absent")
		start := time.Now()
		h := startHR8466Supervisor(t, f, crossingStartup, 10*time.Second)
		defer h.teardown()

		f.waitChildWaiting(t, 0)
		f.releaseChild(0)
		f.waitChildEmitted(t, 0)
		h.waitProbes(1, hr8466NormalBarrier) // ready-record observation: absent, no READY

		h.armProbeGate()
		f.waitChildWaiting(t, 1)
		f.setState("healthy")
		f.releaseChild(1)
		f.waitChildEmitted(t, 1)
		index := h.waitProbeGateEntered(hr8466NormalBarrier)
		duration, result, ok := h.probe.resultAt(index - 1)
		if !ok || result != sidecarPresentHealthy {
			t.Fatalf("gated pre-READY observation result=%d ok=%v, want healthy", result, ok)
		}
		if elapsed := time.Since(start); elapsed >= crossingStartup || elapsed >= crossingEntryBound {
			t.Fatalf("gated pre-READY observation entered after its deadline: %s", elapsed)
		}
		if duration > crossingEntryBound {
			t.Fatalf("gated pre-READY observation duration=%s", duration)
		}
		time.Sleep(time.Until(start.Add(crossingStartup)) + 50*time.Millisecond)
		h.releaseProbeGate()
		assertHR8466NoReadyWatchdog(t, h, 0, 0, 250*time.Millisecond)
		h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
		assertHR8466NoReadyWatchdog(t, h, 0, 0, 250*time.Millisecond)
		assertHR8466HelpersReaped(t, f)
		assertHR8466NoFixtureLeak(t, f)
	})

	t.Run("real-query-deadline", func(t *testing.T) {
		f := newHR8466Fixture(t)
		f.setPlan("ready")
		f.setState("delay")
		h := startHR8466Supervisor(t, f, boundedStartup, 10*time.Second)
		defer h.teardown()

		f.waitChildWaiting(t, 0)
		f.releaseChild(0)
		f.waitChildEmitted(t, 0)
		h.waitProbes(1, hr8466NormalBarrier)
		duration, result, ok := h.probe.resultAt(0)
		if !ok || result != sidecarUnknown {
			t.Fatalf("startup-bounded query result=%d ok=%v, want unknown", result, ok)
		}
		if duration < boundedStartup-150*time.Millisecond || duration >= 900*time.Millisecond {
			t.Fatalf("startup-bounded query duration=%s, want the original deadline within the default 1s query bound", duration)
		}
		entered := hr8466Events(f.entered)
		if len(entered) == 0 {
			t.Fatal("startup-bounded query helper never entered")
		}
		fields := strings.Fields(entered[0])
		if len(fields) == 0 {
			t.Fatalf("empty entered pid receipt %q", entered[0])
		}
		pid, err := strconv.Atoi(fields[0])
		if err != nil || pid <= 0 {
			t.Fatalf("bad entered pid %q (err=%v)", entered[0], err)
		}
		if err := syscall.Kill(pid, 0); err != syscall.ESRCH {
			t.Fatalf("startup-bounded query helper pid %d not reaped: err=%v", pid, err)
		}
		h.waitCount("STOPPING=1", 1, hr8466AdmissionBound)
		assertHR8466NoReadyWatchdog(t, h, 0, 0, 200*time.Millisecond)
		assertHR8466NoFixtureLeak(t, f)
	})
}

// Case 4: explicit child failed/stopping and parent cancellation are terminal;
// later good records cannot revive notifications.  Valid immediate/delayed
// composite readiness within the budget and post-READY watchdog refresh are
// preserved as successful controls.
func TestConsumerTerminalChildStatesBlockLateNotifications(t *testing.T) {
	for _, terminalState := range []string{"failed", "stopping"} {
		terminalState := terminalState
		for _, lateHealthy := range []bool{false, true} {
			name := "child-" + terminalState
			if lateHealthy {
				name += "-late-healthy-stop-failed"
			}
			t.Run(name, func(t *testing.T) {
				f := newHR8466Fixture(t)
				if lateHealthy {
					f.requireStopFailure()
				}
				f.setPlan("ready", "healthy", terminalState, "healthy", "healthy")
				f.setState("healthy")
				h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
				defer h.teardown()

				f.waitChildWaiting(t, 0)
				f.releaseChild(0)
				f.waitChildEmitted(t, 0)
				h.waitCount("READY=1", 1, hr8466NormalBarrier)
				f.waitChildWaiting(t, 1)
				f.setState("healthy")
				f.releaseChild(1)
				f.waitChildEmitted(t, 1)
				h.waitCount("WATCHDOG=1", 1, hr8466NormalBarrier)

				f.setState("garbage")
				f.waitChildWaiting(t, 2)
				f.releaseChild(2)
				f.waitChildEmitted(t, 2)
				h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
				f.waitShowComplete(t, "garbage", 1, hr8466NormalBarrier)
				if hr8466HasEvent(f.productEvents(), "term") {
					t.Fatalf("terminal child state cleaned up while admission was unknown: events=%v", f.productEvents())
				}
				beforeReady := h.count("READY=1")
				beforeWatchdog := h.count("WATCHDOG=1")
				healthyBefore := hr8466EventCount(f.productEvents(), "show-done:healthy")
				if lateHealthy {
					// Healthy late data makes positive publication eligible; failed
					// stop separately withholds child cleanup after terminal readiness.
					f.setState("healthy")
				}

				f.waitChildWaiting(t, 3)
				f.releaseChild(3)
				f.waitChildEmitted(t, 3)
				if lateHealthy {
					assertHR8466NoReadyWatchdog(t, h, 1, 1, 250*time.Millisecond)
					f.waitShowComplete(t, "healthy", healthyBefore+1, hr8466NormalBarrier)
					f.waitEvent(t, "stop-failed", hr8466NormalBarrier)
				} else {
					f.waitShowComplete(t, "garbage", 2, hr8466NormalBarrier)
				}
				f.waitChildWaiting(t, 4)
				f.releaseChild(4)
				f.waitChildEmitted(t, 4)
				if lateHealthy {
					assertHR8466NoReadyWatchdog(t, h, 1, 1, 250*time.Millisecond)
					f.waitShowComplete(t, "healthy", healthyBefore+2, hr8466NormalBarrier)
					f.waitEvent(t, "stop-failed", hr8466NormalBarrier)
				} else {
					f.waitShowComplete(t, "garbage", 3, hr8466NormalBarrier)
				}

				if got := h.count("READY=1"); got != beforeReady {
					t.Fatalf("READY after child %s: count=%d want=%d calls=%v", terminalState, got, beforeReady, h.calls())
				}
				if got := h.count("WATCHDOG=1"); got != beforeWatchdog {
					t.Fatalf("WATCHDOG after child %s: count=%d want=%d calls=%v", terminalState, got, beforeWatchdog, h.calls())
				}
				if hr8466HasEvent(f.productEvents(), "term") {
					t.Fatalf("child cleanup while admission was unknown: events=%v", f.productEvents())
				}
				assertHR8466NoFixtureLeak(t, f)
			})
		}
	}

	t.Run("parent-cancel", func(t *testing.T) {
		f := newHR8466Fixture(t)
		f.setPlan("ready", "healthy", "healthy")
		f.setState("healthy")
		h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
		defer h.teardown()

		f.waitChildWaiting(t, 0)
		f.releaseChild(0)
		f.waitChildEmitted(t, 0)
		h.waitCount("READY=1", 1, hr8466NormalBarrier)
		f.waitChildWaiting(t, 1)
		f.setState("healthy")
		f.releaseChild(1)
		f.waitChildEmitted(t, 1)
		h.waitCount("WATCHDOG=1", 1, hr8466NormalBarrier)

		f.setState("garbage")
		h.cancel()
		h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
		before := h.count("WATCHDOG=1")

		f.waitChildWaiting(t, 2)
		f.releaseChild(2)
		f.waitChildEmitted(t, 2)
		f.waitShowComplete(t, "garbage", 2, hr8466NormalBarrier)
		assertHR8466NoReadyWatchdog(t, h, 1, before, 250*time.Millisecond)
		if hr8466HasEvent(f.productEvents(), "term") {
			t.Fatalf("child cleanup while admission was unknown: events=%v", f.productEvents())
		}
		assertHR8466NoFixtureLeak(t, f)
	})

	t.Run("delayed-composite-readiness-and-watchdog-control", func(t *testing.T) {
		f := newHR8466Fixture(t)
		f.setPlan("ready", "healthy", "healthy")
		f.setState("unhealthy")
		h := startHR8466Supervisor(t, f, hr8466NormalBarrier, hr8466NormalBarrier)
		defer h.teardown()

		f.waitChildWaiting(t, 0)
		f.releaseChild(0)
		f.waitChildEmitted(t, 0)
		h.waitProbes(1, hr8466NormalBarrier)
		if got := h.count("READY=1"); got != 0 {
			t.Fatalf("early READY with an unhealthy composite: calls=%v", h.calls())
		}

		f.waitChildWaiting(t, 1)
		f.setState("healthy")
		f.releaseChild(1)
		f.waitChildEmitted(t, 1)
		h.waitCount("READY=1", 1, hr8466NormalBarrier)

		f.waitChildWaiting(t, 2)
		f.setState("healthy")
		f.releaseChild(2)
		f.waitChildEmitted(t, 2)
		h.waitCount("WATCHDOG=1", 1, hr8466NormalBarrier)
		if got := h.count("READY=1"); got != 1 {
			t.Fatalf("READY count=%d want 1; calls=%v", got, h.calls())
		}
		assertHR8466NoFixtureLeak(t, f)
	})
}

// Case 5: terminal readiness and child-cleanup permission are separately
// asserted.  STOPPING latches readiness immediately while a still-present
// sidecar withholds child cleanup; only a completed confirmed-absence
// observation then permits the product TERM.  No admission retry policy is
// introduced.
func TestConsumerReadinessTerminalIndependentOfChildCleanupPermission(t *testing.T) {
	f := newHR8466Fixture(t)
	f.setPlan("ready", "failed")
	f.setState("healthy")
	f.holdPresent()
	h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
	defer h.teardown()

	f.waitChildWaiting(t, 0)
	f.releaseChild(0)
	f.waitChildEmitted(t, 0)
	h.waitCount("READY=1", 1, hr8466NormalBarrier)

	f.waitChildWaiting(t, 1)
	f.releaseChild(1)
	f.waitChildEmitted(t, 1)
	// Readiness is terminal immediately even though the sidecar stays present.
	h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
	events := f.waitStop(t)
	stopIndex := firstHR8466Index(events, "stop")
	deadline := time.Now().Add(hr8466NormalBarrier)
	for {
		events = f.productEvents()
		completed := 0
		for i := stopIndex + 1; i < len(events); i++ {
			if events[i] == "show-done:healthy" {
				completed++
			}
		}
		if completed >= 1 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("no completed still-present re-observation after stop: events=%v", events)
		}
		time.Sleep(time.Millisecond)
	}
	if hr8466HasEvent(events, "term") {
		t.Fatalf("child cleanup before confirmed sidecar absence: events=%v", events)
	}
	if got := h.count("READY=1"); got != 1 {
		t.Fatalf("READY count=%d want 1; calls=%v", got, h.calls())
	}
	if got := h.count("WATCHDOG=1"); got != 0 {
		t.Fatalf("WATCHDOG count=%d want 0; calls=%v", got, h.calls())
	}

	// Confirmed absence now permits exactly the ordered child TERM.
	f.setState("absent")
	events = f.waitTerm(t)
	assertHR8466TermAfterAbsence(t, events)
	if code := h.waitDone(hr8466AdmissionBound); code != 0 {
		t.Fatalf("admission exit code=%d, want 0", code)
	}
	assertHR8466NoFixtureLeak(t, f)
}

// Case 6: cancellation that races the delivery of an already-completed healthy
// query must never publish a positive receipt.  The maintained real child-FD
// and real-query gate holds delivery of the completed observation, cancels the
// actual supervisor context, and only then releases it.  All three positive
// publication boundaries are covered: the initial ready-record query, the
// later healthy-record pre-READY gate, and the post-READY watchdog query.
func TestConsumerCancellationAfterCompletedHealthyQuerySuppressesPositive(
	t *testing.T,
) {
	for _, mode := range []string{"ready", "later", "watchdog"} {
		t.Run(mode, func(t *testing.T) {
			f := newHR8466Fixture(t)
			f.setPlan("ready", "healthy")
			// The "later" path establishes child-ready while the first
			// completed real observation is nonhealthy, so READY is withheld
			// and the later healthy record exercises the second pre-READY
			// publication boundary.
			if mode == "later" {
				f.setState("unhealthy")
			} else {
				f.setState("healthy")
			}
			h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
			defer h.teardown()

			expected := 1
			switch mode {
			case "later":
				f.waitChildWaiting(t, 0)
				f.releaseChild(0)
				// The real initial query must have COMPLETED and been
				// classified nonhealthy before the later gate is armed.  A
				// fixture ``show-done`` receipt is emitted before the shell
				// exits and before the production parser/``cmd.Output``
				// returns, so waiting on that receipt could arm the gate for
				// the still-unparsed initial unhealthy observation.
				h.waitProbes(1, hr8466NormalBarrier)
				if _, result, ok := h.probe.resultAt(0); !ok || result != sidecarPresentUnhealthy {
					t.Fatalf("first real observation result=%v, want present-unhealthy before arming the later gate", result)
				}
				if got := h.count("READY=1"); got != 0 {
					t.Fatalf("nonhealthy first observation published READY: %v", h.calls())
				}
				f.setState("healthy")
				expected = 2
			case "watchdog":
				f.waitChildWaiting(t, 0)
				f.releaseChild(0)
				h.waitCount("READY=1", 1, hr8466NormalBarrier)
				expected = 2
			}
			h.armProbeGate()
			f.waitChildWaiting(t, expected-1)
			f.releaseChild(expected - 1)
			index := h.waitProbeGateEntered(hr8466NormalBarrier)
			// The gate must have captured the intended completed healthy
			// observation, not an earlier unhealthy one.
			if index != expected {
				t.Fatalf("cancellation gate captured observation %d, want the completed later healthy observation %d; calls=%v", index, expected, h.calls())
			}
			if _, result, ok := h.probe.resultAt(index - 1); !ok || result != sidecarPresentHealthy {
				t.Fatalf("gated observation %d result=%v, want present-healthy before cancellation", index, result)
			}
			// The healthy query has completed; cancellation precedes delivery.
			h.cancel()
			h.releaseProbeGate()
			h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
			if mode != "watchdog" && h.count("READY=1") != 0 {
				t.Fatalf("READY published after cancellation: %v", h.calls())
			}
			if mode == "watchdog" && h.count("WATCHDOG=1") != 0 {
				t.Fatalf("WATCHDOG published after cancellation: %v", h.calls())
			}
			// The terminal decision is latched: exactly one STOPPING and no
			// late positive receipt once the gated result is delivered.
			if got := h.count("STOPPING=1"); got != 1 {
				t.Fatalf("terminal STOPPING count=%d, want 1; calls=%v", got, h.calls())
			}
			if code := h.waitDone(hr8466AdmissionBound); code != 0 {
				t.Fatalf("cancellation exit code=%d, want 0", code)
			}
			assertHR8466NoFixtureLeak(t, f)
		})
	}
}

// R1: a real child that exits while a completed healthy observation is still
// held must never receive a positive notification at ANY of the three
// publication boundaries.  The production single Wait reaps the child; the
// stale observation is released only after ESRCH, so the boundary fence must
// observe the completed child before it can publish READY or WATCHDOG.
func TestConsumerChildExitWhileCompletedHealthyObservationHeld(t *testing.T) {
	for _, mode := range []string{"ready", "later", "watchdog"} {
		t.Run(mode, func(t *testing.T) {
			f := newHR8466Fixture(t)
			f.setPlan("ready", "healthy", "healthy")
			if mode == "later" {
				f.setState("unhealthy")
			} else {
				f.setState("healthy")
			}
			h := startHR8466Supervisor(t, f, 20*time.Second, 20*time.Second)
			defer h.teardown()

			indexRecord := 0
			initialReady := 0
			if mode != "ready" {
				f.waitChildWaiting(t, 0)
				f.releaseChild(0)
				if mode == "watchdog" {
					h.waitCount("READY=1", 1, hr8466NormalBarrier)
					initialReady = 1
				} else {
					h.waitProbes(1, hr8466NormalBarrier)
					if _, result, ok := h.probe.resultAt(0); !ok || result != sidecarPresentUnhealthy {
						t.Fatalf("first real observation result=%v, want present-unhealthy", result)
					}
					if got := h.count("READY=1"); got != 0 {
						t.Fatalf("nonhealthy first observation published READY: %v", h.calls())
					}
				}
				f.setState("healthy")
				indexRecord = 1
			}
			h.armProbeGate()
			f.waitChildWaiting(t, indexRecord)
			f.releaseChild(indexRecord)
			index := h.waitProbeGateEntered(hr8466NormalBarrier)
			if _, result, ok := h.probe.resultAt(index - 1); !ok || result != sidecarPresentHealthy {
				t.Fatalf("gated observation index=%d result=%v, want a completed healthy query", index, result)
			}
			// The completed healthy result is held.  The real child now exits
			// and the supervisor's single owned Wait reaps it.
			writeHR8466File(f.childExit, "exit")
			deadline := time.Now().Add(hr8466NormalBarrier)
			for syscall.Kill(h.cmd.Process.Pid, 0) != syscall.ESRCH {
				if time.Now().After(deadline) {
					t.Fatal("the production Wait owner did not reap the exited health child")
				}
				time.Sleep(time.Millisecond)
			}
			h.releaseProbeGate()
			code := h.waitDone(hr8466AdmissionBound)
			if got := h.count("READY=1"); got != initialReady {
				t.Errorf("READY emitted after the health child was already reaped: count=%d want=%d calls=%v", got, initialReady, h.calls())
			}
			if got := h.count("WATCHDOG=1"); got != 0 {
				t.Errorf("WATCHDOG emitted after the health child was already reaped: count=%d calls=%v", got, h.calls())
			}
			if got := h.count("STOPPING=1"); got != 1 {
				t.Errorf("terminal STOPPING count=%d, want exactly 1; calls=%v", got, h.calls())
			}
			if code != 1 {
				t.Errorf("spontaneous child-exit classification=%d, want 1", code)
			}
			assertHR8466NoFixtureLeak(t, f)
		})
	}
}

// R2: the supervisor owns the health scanner and must join it within a bounded
// lifecycle even when the scanner has decoded a valid record and is blocked on
// its unbuffered delivery while the child exits.  A child-ESRCH or supervisor
// join check alone does not prove the owned helper completed.
func TestConsumerScannerJoinedOnChildExitWithPendingRecord(t *testing.T) {
	f := newHR8466Fixture(t)
	f.setPlan("ready", "healthy", "healthy")
	f.setState("healthy")
	h := startHR8466Supervisor(t, f, 20*time.Second, 20*time.Second)
	defer h.teardown()

	h.armProbeGate()
	f.waitChildWaiting(t, 0)
	f.releaseChild(0)
	index := h.waitProbeGateEntered(hr8466NormalBarrier)
	if _, result, ok := h.probe.resultAt(index - 1); !ok || result != sidecarPresentHealthy {
		t.Fatalf("gated observation index=%d result=%v, want a completed healthy query", index, result)
	}
	// Let the real child emit and the scanner decode the next valid record.
	f.waitChildWaiting(t, 1)
	f.releaseChild(1)
	f.waitChildEmitted(t, 1)
	// The scanner is now blocked delivering that decoded record.
	waitHR8466BlockedScanner(t, hr8466NormalBarrier)

	// The real child exits while the pending delivery is still blocked.
	writeHR8466File(f.childExit, "exit")
	deadline := time.Now().Add(hr8466NormalBarrier)
	for syscall.Kill(h.cmd.Process.Pid, 0) != syscall.ESRCH {
		if time.Now().After(deadline) {
			t.Fatal("the production Wait owner did not reap the exited health child")
		}
		time.Sleep(time.Millisecond)
	}
	h.releaseProbeGate()
	h.waitDone(hr8466AdmissionBound)
	// The owned helper must have completed: no scanner goroutine may remain,
	// let alone one blocked on a channel send.
	assertHR8466ScannerJoined(t, hr8466NormalBarrier)
	assertHR8466NoFixtureLeak(t, f)
}

// R3 counterexample: the reviewer delayed the initial unhealthy shell after its
// completion receipt.  Arming on that receipt would capture the still-unparsed
// initial unhealthy observation; waiting for the real probe result and
// asserting the expected later index/classification is the corrected proof.
func TestConsumerLaterCancellationRequiresCompletedFirstProbe(t *testing.T) {
	f := newHR8466Fixture(t)
	f.holdFirstUnhealthyShow()
	f.setPlan("ready", "healthy")
	f.setState("unhealthy")
	h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
	defer h.teardown()

	f.waitChildWaiting(t, 0)
	f.releaseChild(0)
	// The shell completion receipt lands before the production parser returns.
	f.waitShowComplete(t, "unhealthy", 1, hr8466NormalBarrier)
	f.releaseHeldShow()
	// Only now has the real first observation completed and been classified.
	h.waitProbes(1, hr8466NormalBarrier)
	if _, result, ok := h.probe.resultAt(0); !ok || result != sidecarPresentUnhealthy {
		t.Fatalf("first real observation result=%v, want present-unhealthy", result)
	}
	if got := h.count("READY=1"); got != 0 {
		t.Fatalf("nonhealthy first observation published READY: %v", h.calls())
	}
	f.setState("healthy")
	h.armProbeGate()
	f.waitChildWaiting(t, 1)
	f.releaseChild(1)
	index := h.waitProbeGateEntered(hr8466NormalBarrier)
	if index != 2 {
		t.Fatalf("receipt-only arming captured observation %d, want the completed later healthy observation 2; calls=%v", index, h.calls())
	}
	if _, result, ok := h.probe.resultAt(index - 1); !ok || result != sidecarPresentHealthy {
		t.Fatalf("gated observation %d result=%v, want present-healthy before cancellation", index, result)
	}
	h.cancel()
	h.releaseProbeGate()
	h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
	if h.count("READY=1") != 0 || h.count("WATCHDOG=1") != 0 {
		t.Fatalf("positive notification published after cancellation: %v", h.calls())
	}
	if got := h.count("STOPPING=1"); got != 1 {
		t.Fatalf("terminal STOPPING count=%d, want 1; calls=%v", got, h.calls())
	}
	if code := h.waitDone(hr8466AdmissionBound); code != 0 {
		t.Fatalf("cancellation exit code=%d, want 0", code)
	}
	assertHR8466NoFixtureLeak(t, f)
}

// Case 7: the at-most-once terminal STOPPING latch is independent of child
// cleanup permission.  A refused admission stop followed by a spontaneous
// child exit emits exactly one STOPPING and keeps the failed exit
// classification, without granting product child TERM permission.
func TestConsumerRefusedStopThenSpontaneousExitNotifiesStoppingOnce(t *testing.T) {
	f := newHR8466Fixture(t)
	f.setPlan("ready", "failed")
	f.setState("healthy")
	f.requireStopFailure()
	h := startHR8466Supervisor(t, f, 10*time.Second, 10*time.Second)
	defer h.teardown()

	f.waitChildWaiting(t, 0)
	f.releaseChild(0)
	h.waitCount("READY=1", 1, hr8466NormalBarrier)
	f.waitChildWaiting(t, 1)
	f.releaseChild(1)
	h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
	f.waitEvent(t, "stop-failed", hr8466NormalBarrier)
	// No product TERM was permitted; the child exits spontaneously.
	if events := f.productEvents(); hr8466HasEvent(events, "term") {
		t.Fatalf("child cleanup attempted after a refused stop: events=%v", events)
	}
	f.exitChild()
	if code := h.waitDone(hr8466AdmissionBound); code != 1 {
		t.Fatalf("refused cleanup changed exit classification: %d", code)
	}
	if got := h.count("STOPPING=1"); got != 1 {
		t.Fatalf("terminal refusal then spontaneous child exit emitted STOPPING %d times: %v",
			got, h.calls())
	}
	assertHR8466NoFixtureLeak(t, f)
}

// Case 8: a failure-path assertion that aborts while the completed-query gate
// is entered must still be torn down to a bounded, joined state.  Teardown
// releases the entered gate idempotently, joins the supervisor and its single
// Wait owner within the accepted bound, and a repeated release/teardown must
// not block or panic.  A child ESRCH check alone is not treated as proof that
// the supervisor completed.
func TestConsumerFailurePathTeardownReleasesEnteredGateAndJoins(t *testing.T) {
	f := newHR8466Fixture(t)
	f.setPlan("ready")
	f.setState("healthy")
	h := startHR8466Supervisor(t, f, 20*time.Second, 20*time.Second)
	h.armProbeGate()
	f.waitChildWaiting(t, 0)
	f.releaseChild(0)
	h.waitProbeGateEntered(hr8466NormalBarrier)

	// This is the equivalent of a Fatal assertion aborting before the explicit
	// releaseProbeGate call.
	h.teardown()
	if !h.finished {
		t.Fatalf("failure-path teardown did not join the supervisor within %s", hr8466AdmissionBound)
	}
	// The supervisor's one owned Wait reaped the child; the product assertion
	// boundary is untouched because teardown only records TEST_TEARDOWN_BEGIN.
	h.verifyReaped()
	if events := f.productEvents(); hr8466HasEvent(events, "TEST_TEARDOWN_BEGIN") {
		t.Fatalf("teardown boundary leaked into product-observable events: %v", events)
	}
	// Idempotent: a repeated explicit release plus the registered cleanup's
	// second teardown must not block or panic on the consumed gate.
	h.probe.releaseGate()
	h.releaseProbeGate()
	h.teardown()
	if !h.finished {
		t.Fatal("repeated teardown lost the joined supervisor state")
	}
	assertHR8466NoFixtureLeak(t, f)
}

func TestReviewExpiredPostReadyObservationCannotRefreshWatchdog(t *testing.T) {
	f := newHR8466Fixture(t)
	f.setPlan("ready", "healthy")
	f.setState("healthy")
	const stale = 400 * time.Millisecond
	h := startHR8466Supervisor(t, f, 10*time.Second, stale)
	defer h.teardown()
	f.waitChildWaiting(t, 0)
	f.releaseChild(0)
	h.waitCount("READY=1", 1, hr8466NormalBarrier)
	h.armProbeGate()
	f.waitChildWaiting(t, 1)
	f.releaseChild(1)
	index := h.waitProbeGateEntered(hr8466NormalBarrier)
	if index != 2 {
		t.Fatalf("unexpected observation: %d", index)
	}
	if _, result, ok := h.probe.resultAt(index - 1); !ok || result != sidecarPresentHealthy {
		t.Fatalf("real observation did not complete healthy before delayed delivery")
	}
	held := time.Now()
	time.Sleep(stale + 50*time.Millisecond)
	if elapsed := time.Since(held); elapsed < stale {
		t.Fatalf("completed observation released before freshness elapsed: %s", elapsed)
	}
	h.releaseProbeGate()
	h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
	if got := h.count("WATCHDOG=1"); got != 0 {
		t.Errorf("expired post-READY observation refreshed WATCHDOG: got %d, want 0; calls=%v", got, h.calls())
	}
	if code := h.waitDone(hr8466AdmissionBound); code != 0 {
		t.Fatalf("expired-health ordered cleanup exit=%d, want0", code)
	}
	if got := h.count("READY=1"); got != 1 {
		t.Errorf("READY=%d want1", got)
	}
	if got := h.count("STOPPING=1"); got != 1 {
		t.Errorf("STOPPING=%d want1", got)
	}
	assertHR8466TermAfterAbsence(t, f.productEvents())
	assertHR8466NoFixtureLeak(t, f)
}

// The real query must inherit the remaining post-READY freshness window,
// rather than using its independent one-second cap to extend stale liveness.
func TestConsumerPostReadyQueryBoundedByRemainingFreshness(t *testing.T) {
	f := newHR8466Fixture(t)
	f.setPlan("ready", "healthy")
	f.setState("healthy")
	const stale = 400 * time.Millisecond
	h := startHR8466Supervisor(t, f, 10*time.Second, stale)
	defer h.teardown()
	f.waitChildWaiting(t, 0)
	f.releaseChild(0)
	h.waitCount("READY=1", 1, hr8466NormalBarrier)
	h.armProbeGate()
	f.waitChildWaiting(t, 1)
	f.setState("delay")
	f.releaseChild(1)
	f.waitEvent(t, "show:delay", hr8466NormalBarrier)
	index := h.waitProbeGateEntered(hr8466QueryBound)
	if index != 2 {
		t.Fatalf("unexpected in-flight observation: %d", index)
	}
	duration, result, ok := h.probe.resultAt(1)
	if !ok || result != sidecarUnknown {
		t.Fatalf("crossing query result=%d ok=%v, want unknown", result, ok)
	}
	if duration > 800*time.Millisecond {
		t.Errorf("post-READY query exceeded remaining freshness: duration=%s, want <800ms for400ms window", duration)
	}
	entries := hr8466Events(f.entered)
	if len(entries) != 2 {
		t.Fatalf("query entry count=%d, want2", len(entries))
	}
	pid, err := strconv.Atoi(entries[1])
	if err != nil || pid <= 0 {
		t.Fatalf("invalid query PID receipt: %q", entries[1])
	}
	if err := syscall.Kill(pid, 0); err != syscall.ESRCH {
		t.Fatalf("expired query PID%d not reaped: %v", pid, err)
	}
	// Cleanup observes absence only after the timed-out query was recorded.
	f.setState("absent")
	h.releaseProbeGate()
	h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
	if got := h.count("WATCHDOG=1"); got != 0 {
		t.Errorf("crossing query emitted WATCHDOG: got%d want0", got)
	}
	if code := h.waitDone(hr8466AdmissionBound); code != 0 {
		t.Fatalf("ordered cleanup exit=%d want0", code)
	}
	if got := h.count("STOPPING=1"); got != 1 {
		t.Errorf("STOPPING count=%d want1", got)
	}
	assertHR8466TermAfterAbsence(t, f.productEvents())
	assertHR8466NoFixtureLeak(t, f)
}

// A timely complete pair renews the existing configured window. Silence then
// expires relative to that legitimate renewal, not relative to initial READY.
func TestConsumerTimelyPostReadyPairRenewsThenMissingHealthExpires(t *testing.T) {
	f := newHR8466Fixture(t)
	f.setPlan("ready", "healthy")
	f.setState("healthy")
	const stale = 800 * time.Millisecond
	h := startHR8466Supervisor(t, f, 10*time.Second, stale)
	defer h.teardown()
	f.waitChildWaiting(t, 0)
	f.releaseChild(0)
	h.waitCount("READY=1", 1, hr8466NormalBarrier)
	time.Sleep(stale / 2)
	f.waitChildWaiting(t, 1)
	f.releaseChild(1)
	h.waitCount("WATCHDOG=1", 1, hr8466NormalBarrier)
	renewed := time.Now()
	time.Sleep(stale/2 + 50*time.Millisecond)
	if got := h.count("STOPPING=1"); got != 0 {
		t.Fatalf("timely pair failed to renew: STOPPING=%d want0; calls=%v", got, h.calls())
	}
	h.waitCount("STOPPING=1", 1, stale+hr8466NormalBarrier)
	if elapsed := time.Since(renewed); elapsed < stale-50*time.Millisecond || elapsed > stale+hr8466NormalBarrier {
		t.Errorf("missing health expiry elapsed=%s, want latest800ms window within scheduling bound", elapsed)
	}
	if code := h.waitDone(hr8466AdmissionBound); code != 0 {
		t.Fatalf("missing-health cleanup exit=%d want0", code)
	}
	if got := h.count("READY=1"); got != 1 {
		t.Errorf("READY=%d want1", got)
	}
	if got := h.count("WATCHDOG=1"); got != 1 {
		t.Errorf("WATCHDOG=%d want1", got)
	}
	if got := h.count("STOPPING=1"); got != 1 {
		t.Errorf("STOPPING=%d want1", got)
	}
	assertHR8466TermAfterAbsence(t, f.productEvents())
	assertHR8466NoFixtureLeak(t, f)
}

// Expiry is terminal even while admission removal is unknown or refused.
// Later healthy records cannot revive it or authorize child cleanup.
func TestConsumerExpiredPostReadyDeliveryRetainsTerminalAdmissionFence(t *testing.T) {
	for _, admission := range []string{"refused", "unknown"} {
		for _, ending := range []string{"absent", "child-exit"} {
			t.Run(admission+"/"+ending, func(t *testing.T) {
				f := newHR8466Fixture(t)
				f.setPlan("ready", "healthy", "healthy", "healthy", "healthy")
				f.setState("healthy")
				f.requireStopFailure()
				const stale = 400 * time.Millisecond
				h := startHR8466Supervisor(t, f, 10*time.Second, stale)
				defer h.teardown()
				f.waitChildWaiting(t, 0)
				f.releaseChild(0)
				h.waitCount("READY=1", 1, hr8466NormalBarrier)
				h.armProbeGate()
				f.waitChildWaiting(t, 1)
				f.releaseChild(1)
				index := h.waitProbeGateEntered(hr8466NormalBarrier)
				if index != 2 {
					t.Fatalf("unexpected expiry observation: %d", index)
				}
				if _, result, ok := h.probe.resultAt(1); !ok || result != sidecarPresentHealthy {
					t.Fatal("held query did not complete healthy")
				}
				held := time.Now()
				time.Sleep(stale + 50*time.Millisecond)
				if time.Since(held) < stale {
					t.Fatal("freshness boundary not crossed")
				}
				if admission == "unknown" {
					f.setState("unknownstate")
				}
				h.releaseProbeGate()
				h.waitCount("STOPPING=1", 1, hr8466NormalBarrier)
				if admission == "refused" {
					f.waitEvent(t, "stop-failed", hr8466NormalBarrier)
				} else {
					f.waitEvent(t, "show-done:unknownstate", hr8466NormalBarrier)
				}
				// show-done is before cmd.Output/parser return, so it cannot
				// safely arm a gate for the next observation. Keep the actual
				// completed-result receipts instead. Two later records and two
				// healthy admission completions prove at least one later record
				// was consumed even if the elapsed timer also won a select.
				probesBefore := h.probe.count()
				failuresBefore := hr8466EventCount(f.productEvents(), "stop-failed")
				f.setState("healthy")
				for _, record := range []int{2, 3} {
					f.waitChildWaiting(t, record)
					f.releaseChild(record)
					f.waitChildEmitted(t, record)
				}
				consumedBy := time.Now().Add(hr8466NormalBarrier)
				for {
					healthy := 0
					for i := probesBefore; i < h.probe.count(); i++ {
						if _, result, ok := h.probe.resultAt(i); ok && result == sidecarPresentHealthy {
							healthy++
						}
					}
					failures := hr8466EventCount(f.productEvents(), "stop-failed")
					if healthy >= 2 && failures >= failuresBefore+2 {
						break
					}
					if time.Now().After(consumedBy) {
						t.Fatalf("later healthy records did not complete refused admission: healthy=%d want>=2, failures=%d want>=%d", healthy, failures, failuresBefore+2)
					}
					time.Sleep(time.Millisecond)
				}
				if got := h.count("WATCHDOG=1"); got != 0 {
					t.Errorf("expired delivery or later health revived WATCHDOG: got%d want0; calls=%v", got, h.calls())
				}
				if got := h.count("READY=1"); got != 1 {
					t.Errorf("terminal READY=%d want1", got)
				}
				if events := f.productEvents(); hr8466HasEvent(events, "term") {
					t.Fatalf("child TERM before admission absence: %v", events)
				}
				if ending == "absent" {
					f.setState("absent")
					f.waitChildWaiting(t, 4)
					f.releaseChild(4)
					if code := h.waitDone(hr8466AdmissionBound); code != 0 {
						t.Fatalf("confirmed-absence cleanup exit=%d want0", code)
					}
					assertHR8466TermAfterAbsence(t, f.productEvents())
				} else {
					f.exitChild()
					if code := h.waitDone(hr8466AdmissionBound); code != 1 {
						t.Fatalf("refused cleanup/spontaneous exit=%d want1", code)
					}
					if events := f.productEvents(); hr8466HasEvent(events, "term") {
						t.Fatalf("spontaneous exit manufactured cleanup permission: %v", events)
					}
				}
				if got := h.count("WATCHDOG=1"); got != 0 {
					t.Errorf("terminal final WATCHDOG=%d want0", got)
				}
				if got := h.count("READY=1"); got != 1 {
					t.Errorf("terminal final READY=%d want1", got)
				}
				if got := h.count("STOPPING=1"); got != 1 {
					t.Errorf("terminal STOPPING=%d want1; calls=%v", got, h.calls())
				}
				assertHR8466NoFixtureLeak(t, f)
			})
		}
	}
}
