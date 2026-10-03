package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	sidecar "happyranch/linux-tsnet-sidecar"
)

func TestConnectorHealthChild(t *testing.T) {
	if os.Getenv("HAPPYRANCH_TEST_HEALTH_CHILD") == "" {
		return
	}
	fd, _ := strconv.Atoi(os.Getenv("HAPPYRANCH_CHILD_HEALTH_FD"))
	out := os.NewFile(uintptr(fd), "health")
	generation := os.Getenv("HAPPYRANCH_CHILD_HEALTH_GENERATION")
	mode := os.Getenv("HAPPYRANCH_TEST_HEALTH_CHILD")
	sequence := uint64(1)
	state := "ready"
	if mode == "waiting" {
		state = "waiting"
	}
	for {
		_, _ = out.WriteString(healthRecord(generation, sequence, state))
		sequence++
		if state == "ready" {
			state = "healthy"
		}
		time.Sleep(2 * time.Millisecond)
	}
}

func runCompositeOrdering(t *testing.T, initiallyActive bool) []string {
	t.Helper()
	t.Setenv("HAPPYRANCH_TEST_HEALTH_CHILD", "healthy")
	var active atomic.Bool
	active.Store(initiallyActive)
	ctx, cancel := context.WithCancel(context.Background())
	n := &recordingNotifier{}
	done := make(chan int, 1)
	go func() {
		done <- superviseConnector(ctx, []string{os.Args[0], "-test.run=TestConnectorHealthChild"}, n, time.Second, 50*time.Millisecond, nil,
			func(context.Context) sidecarObservation {
				if active.Load() {
					return sidecarPresentHealthy
				}
				return sidecarAbsent
			},
			func(context.Context) bool { active.Store(false); return true })
	}()
	if !initiallyActive {
		time.Sleep(10 * time.Millisecond)
		active.Store(true)
	}
	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		n.mu.Lock()
		ready := false
		for _, call := range n.calls {
			if call == "READY=1" {
				ready = true
			}
		}
		n.mu.Unlock()
		if ready {
			break
		}
		time.Sleep(time.Millisecond)
	}
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("supervisor did not stop")
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	return append([]string(nil), n.calls...)
}

func TestCompositeReadinessAcceptsConnectorFirstAndSidecarFirst(t *testing.T) {
	for _, initiallyActive := range []bool{false, true} {
		calls := runCompositeOrdering(t, initiallyActive)
		ready := 0
		for _, call := range calls {
			if call == "READY=1" {
				ready++
			}
		}
		if ready != 1 {
			t.Fatalf("initiallyActive=%v READY count=%d calls=%v", initiallyActive, ready, calls)
		}
	}
}

func TestRepeatedWaitingCannotExtendStartupDeadline(t *testing.T) {
	t.Setenv("HAPPYRANCH_TEST_HEALTH_CHILD", "waiting")
	n := &recordingNotifier{}
	started := time.Now()
	code := superviseConnector(context.Background(), []string{os.Args[0], "-test.run=TestConnectorHealthChild"}, n,
		20*time.Millisecond, 50*time.Millisecond, nil, func(context.Context) sidecarObservation { return sidecarAbsent }, func(context.Context) bool { return true })
	if code != 1 || time.Since(started) > 300*time.Millisecond {
		t.Fatalf("deadline refreshed: code=%d elapsed=%s", code, time.Since(started))
	}
	for _, call := range n.calls {
		if call == "READY=1" || call == "WATCHDOG=1" {
			t.Fatalf("partial health notified: %v", n.calls)
		}
	}
}

const healthySystemdShowOutput = "ActiveState=active\nSubState=running\nResult=success\nMainPID=42\n"

// shellSingleQuote wraps a value for a POSIX shell single-quoted argument.
func shellSingleQuote(value string) string {
	return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'"
}

// shellPrintfEscape renders a byte string as a `printf '%b'` argument so a
// fake systemctl can reproduce it exactly, including newlines and control
// bytes, using only the shell builtin.
func shellPrintfEscape(value string) string {
	var escaped strings.Builder
	for i := 0; i < len(value); i++ {
		character := value[i]
		switch {
		case character == '\\':
			escaped.WriteString(`\\`)
		case character == '\n':
			escaped.WriteString(`\n`)
		case character == '\t':
			escaped.WriteString(`\t`)
		case character == '\r':
			escaped.WriteString(`\r`)
		case character < 0x20 || character == 0x7f:
			fmt.Fprintf(&escaped, `\%03o`, character)
		default:
			escaped.WriteByte(character)
		}
	}
	return escaped.String()
}

// fakeSystemctlShellPrintf builds a `#!/bin/sh` script that emits exactly
// stdout on standard output.
func fakeSystemctlShellPrintf(stdout string) string {
	return "#!/bin/sh\nprintf '%b' " + shellSingleQuote(shellPrintfEscape(stdout)) + "\n"
}

// installFakeSystemctl writes a fake systemctl executable and puts only its
// directory on PATH, exactly like a real systemd host would resolve it.
func installFakeSystemctl(t *testing.T, script string) string {
	t.Helper()
	dir := t.TempDir()
	command := filepath.Join(dir, "systemctl")
	if err := os.WriteFile(command, []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)
	return command
}

// observeSidecarState runs one observation against a fake systemctl emitting
// exactly stdout.
func observeSidecarState(t *testing.T, stdout string) sidecarObservation {
	t.Helper()
	installFakeSystemctl(t, fakeSystemctlShellPrintf(stdout))
	return systemdSidecarState(context.Background())
}

func permutationsOf(values []string) [][]string {
	if len(values) <= 1 {
		return [][]string{append([]string(nil), values...)}
	}
	permutations := [][]string{}
	for i := range values {
		rest := make([]string, 0, len(values)-1)
		rest = append(rest, values[:i]...)
		rest = append(rest, values[i+1:]...)
		for _, tail := range permutationsOf(rest) {
			permutations = append(permutations, append([]string{values[i]}, tail...))
		}
	}
	return permutations
}

func TestSystemdSidecarHealthRequiresCompleteAuthoritativeState(t *testing.T) {
	if got := observeSidecarState(t, healthySystemdShowOutput); got != sidecarPresentHealthy {
		t.Fatalf("active sidecar observation = %d, want sidecarPresentHealthy", got)
	}
	if got := observeSidecarState(t, "ActiveState=activating\nSubState=start\nResult=success\nMainPID=42\n"); got != sidecarPresentUnhealthy {
		t.Fatalf("partial sidecar readiness observation = %d, want sidecarPresentUnhealthy", got)
	}
	if got := observeSidecarState(t, "garbage\n"); got != sidecarUnknown {
		t.Fatalf("malformed state observation = %d, want sidecarUnknown", got)
	}
}

func TestSystemdSidecarHealthAcceptsEveryHealthyPropertyOrder(t *testing.T) {
	lines := []string{
		"ActiveState=active",
		"SubState=running",
		"Result=success",
		"MainPID=42",
	}
	permutations := permutationsOf(lines)
	if len(permutations) != 24 {
		t.Fatalf("permutation count = %d, want 24", len(permutations))
	}
	for _, permutation := range permutations {
		stdout := strings.Join(permutation, "\n") + "\n"
		if got := observeSidecarState(t, stdout); got != sidecarPresentHealthy {
			t.Fatalf("permutation %v = %d, want sidecarPresentHealthy", permutation, got)
		}
	}
}

func TestSystemdSidecarHealthRejectsDuplicatedProperties(t *testing.T) {
	order := []string{"ActiveState", "SubState", "Result", "MainPID"}
	healthy := map[string]string{
		"ActiveState": "active",
		"SubState":    "running",
		"Result":      "success",
		"MainPID":     "42",
	}
	for _, key := range order {
		for _, duplicate := range []string{healthy[key], "conflicting"} {
			var stdout strings.Builder
			for _, name := range order {
				stdout.WriteString(name + "=" + healthy[name] + "\n")
				if name == key {
					stdout.WriteString(name + "=" + duplicate + "\n")
				}
			}
			if got := observeSidecarState(t, stdout.String()); got != sidecarUnknown {
				t.Fatalf("duplicate %s=%q observation = %d, want sidecarUnknown", key, duplicate, got)
			}
		}
	}
}

func TestSystemdSidecarHealthRejectsMissingAndEmptyProperties(t *testing.T) {
	order := []string{"ActiveState", "SubState", "Result", "MainPID"}
	healthy := map[string]string{
		"ActiveState": "active",
		"SubState":    "running",
		"Result":      "success",
		"MainPID":     "42",
	}
	for _, key := range order {
		var missing strings.Builder
		for _, name := range order {
			if name != key {
				missing.WriteString(name + "=" + healthy[name] + "\n")
			}
		}
		if got := observeSidecarState(t, missing.String()); got != sidecarUnknown {
			t.Fatalf("missing %s observation = %d, want sidecarUnknown", key, got)
		}
		var empty strings.Builder
		for _, name := range order {
			value := healthy[name]
			if name == key {
				value = ""
			}
			empty.WriteString(name + "=" + value + "\n")
		}
		if got := observeSidecarState(t, empty.String()); got != sidecarUnknown {
			t.Fatalf("empty %s observation = %d, want sidecarUnknown", key, got)
		}
	}
}

func TestSystemdSidecarHealthRejectsMalformedRecords(t *testing.T) {
	cases := map[string]string{
		"line missing separator":   "ActiveState=active\nSubState=running\nResult=success\nMainPID\n",
		"empty key":                "ActiveState=active\nSubState=running\nResult=success\n=42\n",
		"misspelled key":           "ActiveState=active\nSubState=running\nResult=success\nMainPid=42\n",
		"extra unknown key":        "ActiveState=active\nSubState=running\nResult=success\nMainPID=42\nExtra=1\n",
		"interior blank line":      "ActiveState=active\nSubState=running\n\nResult=success\nMainPID=42\n",
		"old positional reply":     "active\nrunning\nsuccess\n42\n",
		"control byte in value":    "ActiveState=active\nSubState=running\nResult=success\nMainPID=4\x002\n",
		"second equals in MainPID": "ActiveState=active\nSubState=running\nResult=success\nMainPID=4=2\n",
	}
	for name, stdout := range cases {
		if got := observeSidecarState(t, stdout); got != sidecarUnknown {
			t.Fatalf("%s observation = %d, want sidecarUnknown", name, got)
		}
	}
}

func TestSystemdSidecarHealthMainPIDBoundaries(t *testing.T) {
	// Rule 3 makes active/running/success/0 sidecarPresentUnhealthy (MainPID 0
	// with an active state); this matches the truth table and Q6.  Every other
	// non-healthy PID spelling must be unknown, never truncated into a PID.
	unknown := []string{"1", "-1", "abc", " 42", "4.2", "9999999999999999999999"}
	for _, mainPID := range unknown {
		stdout := "ActiveState=active\nSubState=running\nResult=success\nMainPID=" + mainPID + "\n"
		if got := observeSidecarState(t, stdout); got != sidecarUnknown {
			t.Fatalf("MainPID=%q observation = %d, want sidecarUnknown", mainPID, got)
		}
	}
	if got := observeSidecarState(t, "ActiveState=active\nSubState=running\nResult=success\nMainPID=0\n"); got != sidecarPresentUnhealthy {
		t.Fatalf("MainPID=%q observation = %d, want sidecarPresentUnhealthy", "0", got)
	}
	for _, mainPID := range []string{"2", "42"} {
		stdout := "ActiveState=active\nSubState=running\nResult=success\nMainPID=" + mainPID + "\n"
		if got := observeSidecarState(t, stdout); got != sidecarPresentHealthy {
			t.Fatalf("MainPID=%q observation = %d, want sidecarPresentHealthy", mainPID, got)
		}
	}
}

func TestSystemdSidecarHealthTruthTableRepresentatives(t *testing.T) {
	cases := []struct {
		activeState string
		subState    string
		result      string
		mainPID     string
		want        sidecarObservation
	}{
		{"inactive", "dead", "success", "0", sidecarAbsent},
		{"inactive", "dead", "success", "2", sidecarUnknown},
		{"inactive", "running", "success", "0", sidecarUnknown},
		{"reloading", "start", "success", "2", sidecarUnknown},
		{"active", "start", "success", "0", sidecarPresentUnhealthy},
		{"active", "start", "success", "2", sidecarPresentUnhealthy},
		{"activating", "start", "success", "0", sidecarPresentUnhealthy},
		{"activating", "start", "success", "2", sidecarPresentUnhealthy},
		{"deactivating", "stop", "success", "0", sidecarPresentUnhealthy},
		{"deactivating", "stop", "success", "2", sidecarPresentUnhealthy},
		{"failed", "failed", "success", "0", sidecarPresentUnhealthy},
		{"failed", "failed", "success", "2", sidecarPresentUnhealthy},
		{"active", "running", "exit-code", "2", sidecarPresentUnhealthy},
	}
	for _, tc := range cases {
		stdout := "ActiveState=" + tc.activeState + "\nSubState=" + tc.subState + "\nResult=" + tc.result + "\nMainPID=" + tc.mainPID + "\n"
		if got := observeSidecarState(t, stdout); got != tc.want {
			t.Fatalf("ActiveState=%s SubState=%s Result=%s MainPID=%s observation = %d, want %d",
				tc.activeState, tc.subState, tc.result, tc.mainPID, got, tc.want)
		}
	}
}

func TestSystemdSidecarHealthFailsClosedOnProbeFailure(t *testing.T) {
	installFakeSystemctl(t, fakeSystemctlShellPrintf(healthySystemdShowOutput)+"exit 1\n")
	if got := systemdSidecarState(context.Background()); got != sidecarUnknown {
		t.Fatalf("nonzero exit with valid stdout observation = %d, want sidecarUnknown", got)
	}
	t.Setenv("PATH", t.TempDir())
	if got := systemdSidecarState(context.Background()); got != sidecarUnknown {
		t.Fatalf("unstartable systemctl observation = %d, want sidecarUnknown", got)
	}
	if got := observeSidecarState(t, ""); got != sidecarUnknown {
		t.Fatalf("empty stdout observation = %d, want sidecarUnknown", got)
	}
}

func TestSystemdSidecarHealthQueriesNamedPropertiesWithoutValueFlag(t *testing.T) {
	dir := t.TempDir()
	logPath := filepath.Join(dir, "argv.log")
	script := "#!/bin/sh\nprintf '%s\\n' \"$@\" >> " + shellSingleQuote(logPath) + "\n" +
		strings.TrimPrefix(fakeSystemctlShellPrintf(healthySystemdShowOutput), "#!/bin/sh\n")
	if err := os.WriteFile(filepath.Join(dir, "systemctl"), []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir)
	if got := systemdSidecarState(context.Background()); got != sidecarPresentHealthy {
		t.Fatalf("healthy named state = %d, want sidecarPresentHealthy", got)
	}
	raw, err := os.ReadFile(logPath)
	if err != nil {
		t.Fatal(err)
	}
	argv := strings.Split(strings.TrimSuffix(string(raw), "\n"), "\n")
	joined := strings.Join(argv, " ")
	if !strings.Contains(joined, "happyranch-tsnet-sidecar.service") {
		t.Fatalf("argv %v did not name the sidecar service", argv)
	}
	for _, property := range []string{"--property=ActiveState", "--property=SubState", "--property=Result", "--property=MainPID"} {
		if !strings.Contains(joined, property) {
			t.Fatalf("argv %v did not request %s", argv, property)
		}
	}
	if strings.Contains(joined, "--value") {
		t.Fatalf("argv %v requested the unreliable positional --value form", argv)
	}
}

func TestAdmissionRemovalWaitsForSidecarBeforeConnectorCleanup(t *testing.T) {
	active := true
	events := []string{}
	healthy := func(context.Context) sidecarObservation {
		events = append(events, "probe")
		if active {
			return sidecarPresentHealthy
		}
		return sidecarAbsent
	}
	stop := func(context.Context) bool { events = append(events, "sidecar-stop"); active = false; return true }
	if !removeSidecarAdmission(context.Background(), healthy, stop) {
		t.Fatal("admission removal failed")
	}
	if len(events) < 3 || events[0] != "probe" || events[1] != "sidecar-stop" || events[2] != "probe" {
		t.Fatalf("unexpected ordering: %v", events)
	}
}

func TestAdmissionRemovalIsIdempotentWhenSidecarAlreadyStopped(t *testing.T) {
	called := false
	if !removeSidecarAdmission(context.Background(), func(context.Context) sidecarObservation { return sidecarAbsent }, func(context.Context) bool { called = true; return true }) {
		t.Fatal("already removed admission was rejected")
	}
	if called {
		t.Fatal("stopped sidecar was signalled twice")
	}
}

func TestAdmissionRemovalUnknownFailsClosedWithoutCleanup(t *testing.T) {
	called := false
	if removeSidecarAdmission(context.Background(), func(context.Context) sidecarObservation { return sidecarUnknown }, func(context.Context) bool { called = true; return true }) {
		t.Fatal("unknown state claimed admission removed")
	}
	if called {
		t.Fatal("unknown state triggered stop")
	}
}

func TestStructuredChildHealthAcceptsExactRecords(t *testing.T) {
	records := make(chan childHealth, 2)
	failed := make(chan error, 1)
	scanChildHealth(bytes.NewBufferString(healthRecord("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 1, "ready")), records, failed, nil)
	select {
	case err := <-failed:
		t.Fatal(err)
	default:
	}
	record := <-records
	if record.Version != 1 || record.Sequence != 1 || record.State != "ready" {
		t.Fatalf("unexpected record: %#v", record)
	}
}

func TestStructuredChildHealthRejectsMalformedPartialAndUnknownShape(t *testing.T) {
	for _, raw := range []string{
		"not-json\n",
		`{"version":1,"generation":"a","sequence":1}` + "\n",
		`{"version":1,"generation":"a","sequence":1,"state":"ready","extra":true}` + "\n",
	} {
		records := make(chan childHealth, 2)
		failed := make(chan error, 1)
		scanChildHealth(bytes.NewBufferString(raw), records, failed, nil)
		select {
		case <-failed:
		default:
			t.Fatalf("accepted malformed record %q", raw)
		}
	}
}

// R2 direct seam: a decoded record whose delivery is blocked on the
// unbuffered records channel must abort when the supervisor cancels the owned
// scanner, close the records channel, and report no protocol failure.
func TestStructuredChildHealthAbortsPendingDeliveryOnStop(t *testing.T) {
	records := make(chan childHealth)
	failed := make(chan error, 1)
	stop := make(chan struct{})
	done := make(chan struct{})
	go func() {
		defer close(done)
		scanChildHealth(bytes.NewBufferString(healthRecord("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 1, "ready")), records, failed, stop)
	}()
	waitHR8466BlockedScanner(t, 2*time.Second)
	close(stop)
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("cancellation did not unblock the pending scanChildHealth delivery")
	}
	if _, ok := <-records; ok {
		t.Fatal("scanChildHealth did not close the records channel on cancellation")
	}
	select {
	case err := <-failed:
		t.Fatalf("cancellation reported a protocol failure: %v", err)
	default:
	}
}

type countingStopper struct{ calls int }

func (s *countingStopper) Stop() error {
	s.calls++
	return nil
}

func TestStopTwiceUsesSameProductionInstanceAndReceiptsEachInvocation(t *testing.T) {
	stopper := &countingStopper{}
	file, err := os.CreateTemp(t.TempDir(), "receipt")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if err := stopTwice(stopper, file, 4242); err != nil {
		t.Fatal(err)
	}
	if stopper.calls != 2 {
		t.Fatalf("Stop calls = %d, want 2", stopper.calls)
	}
	if err := file.Sync(); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(file.Name())
	if err != nil {
		t.Fatal(err)
	}
	want := "lifecycle_stop_complete run=4242 invocation=1\nlifecycle_stop_complete run=4242 invocation=2\n"
	if string(raw) != want {
		t.Fatalf("receipt = %q, want %q", raw, want)
	}
}

func TestDiagnosticReceiptHasStableRedactedCategories(t *testing.T) {
	for _, tc := range []struct {
		err             error
		category, phase string
	}{
		{fmt.Errorf("wrapped: %w", sidecar.ErrCredentialInput), "credential_input", "input_acquisition"},
		{sidecar.ErrEngineStart, "engine_start", "engine_initialization"},
		{sidecar.ErrNetworkJoin, "network_join", "peer_establishment"},
		{sidecar.ErrDurableCommit, "durable_commit", "receipt_commit"},
		{errors.New("provider token=/secret/path"), "unknown", "unknown"},
	} {
		raw := strings.TrimPrefix(diagnosticReceipt(tc.err), "diagnostic_receipt=")
		var got map[string]any
		if err := json.Unmarshal([]byte(raw), &got); err != nil {
			t.Fatal(err)
		}
		if got["category"] != tc.category || got["phase"] != tc.phase {
			t.Fatalf("receipt=%v", got)
		}
		if strings.Contains(raw, "token") || strings.Contains(raw, "/secret") || strings.Contains(raw, "provider") {
			t.Fatalf("secret-bearing receipt %q", raw)
		}
	}
}

type recordingNotifier struct {
	mu    sync.Mutex
	calls []string
	err   error
}

func (n *recordingNotifier) Notify(states ...string) error {
	n.mu.Lock()
	defer n.mu.Unlock()
	n.calls = append(n.calls, states...)
	return n.err
}

func TestWatchdogLoopReportsHealthyProcessAndStops(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	n := &recordingNotifier{}
	done := make(chan struct{})
	failed := make(chan error, 1)
	go func() {
		watchdogLoop(ctx, cancel, n, time.Millisecond, failed)
		close(done)
	}()
	time.Sleep(5 * time.Millisecond)
	cancel()
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("watchdog did not stop")
	}
	n.mu.Lock()
	defer n.mu.Unlock()
	if len(n.calls) == 0 {
		t.Fatal("healthy process emitted no watchdog notification")
	}
	for _, call := range n.calls {
		if call != "WATCHDOG=1" {
			t.Fatalf("unexpected notification %q", call)
		}
	}
}

func TestWatchdogFailureCancelsService(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	n := &recordingNotifier{err: errors.New("notify failed")}
	failed := make(chan error, 1)
	watchdogLoop(ctx, cancel, n, time.Millisecond, failed)
	select {
	case <-ctx.Done():
	default:
		t.Fatal("notify failure did not cancel service")
	}
	if <-failed == nil {
		t.Fatal("notify failure was not reported")
	}
}

func TestWithoutNotifySocketPreventsHelperNotification(t *testing.T) {
	env := withoutNotifySocket([]string{"PATH=/bin", "NOTIFY_SOCKET=/run/systemd/notify", "OTHER=value"})
	if got := strings.Join(env, "\n"); got != "PATH=/bin\nOTHER=value" {
		t.Fatalf("helper environment retained notification authority: %q", got)
	}
}

func writeFixtureSystemctl(t *testing.T, dir, statePath, logPath, failStopPath string) {
	t.Helper()
	script := `#!/bin/sh
if [ -n "$NOTIFY_SOCKET" ]; then echo notify_leak >> ` + logPath + `; exit 9; fi
echo "$@" >> ` + logPath + `
case "$1" in
  show)
    read -r state < ` + statePath + `
    case "$state" in
      absent) printf 'ActiveState=inactive\nSubState=dead\nResult=success\nMainPID=0\n' ;;
      healthy) printf 'ActiveState=active\nSubState=running\nResult=success\nMainPID=42\n' ;;
      unhealthy) printf 'ActiveState=failed\nSubState=failed\nResult=exit-code\nMainPID=42\n' ;;
    esac ;;
  stop)
    if [ -f ` + failStopPath + ` ]; then exit 1; fi
    echo absent > ` + statePath + ` ;;
esac
`
	if err := os.WriteFile(filepath.Join(dir, "systemctl"), []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
}

func readFixtureLog(t *testing.T, path string) string {
	t.Helper()
	raw, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return string(raw)
}

// TestAdmissionRemovalUsesRealNamedQueryAndStop wires the real named-property
// probe and real stop through the real admission-removal consumer, using only
// a per-case fail-closed systemctl fixture on PATH (never host forwarding).
//
// This is the command-level admission case.  The real supervisor-level
// consumer assertions (child TERM/cleanup ordering after observed absence,
// notification suppression, the real 5s timeout and the detached admission
// context) live in health_consumer_test.go so a seam name is never mistaken
// for executed coverage.
func TestAdmissionRemovalUsesRealNamedQueryAndStop(t *testing.T) {
	dir := t.TempDir()
	statePath := filepath.Join(dir, "state")
	logPath := filepath.Join(dir, "log")
	failStopPath := filepath.Join(dir, "fail-stop")
	writeFixtureSystemctl(t, dir, statePath, logPath, failStopPath)
	t.Setenv("PATH", dir)
	t.Setenv("NOTIFY_SOCKET", "/run/systemd/notify")

	// H4: confirmed absence is admitted with no stop and no child cleanup.
	if err := os.WriteFile(statePath, []byte("absent\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if !removeSidecarAdmission(context.Background(), systemdSidecarState, systemdStopSidecar) {
		t.Fatal("confirmed absence was not admitted")
	}
	log := readFixtureLog(t, logPath)
	if strings.Contains(log, "stop") {
		t.Fatalf("confirmed absence issued a stop: %q", log)
	}
	if strings.Contains(log, "notify_leak") {
		t.Fatalf("NOTIFY_SOCKET leaked into the fixture command: %q", log)
	}

	// H5: present -> exactly one successful stop -> observed absence.
	if err := os.WriteFile(statePath, []byte("healthy\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(logPath, nil, 0600); err != nil {
		t.Fatal(err)
	}
	if !removeSidecarAdmission(context.Background(), systemdSidecarState, systemdStopSidecar) {
		t.Fatal("healthy present sidecar was not removed")
	}
	log = readFixtureLog(t, logPath)
	if strings.Count(log, "stop") != 1 {
		t.Fatalf("expected exactly one stop, log=%q", log)
	}

	// H6: a stop failure fails closed and never claims absence.
	if err := os.WriteFile(statePath, []byte("healthy\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(failStopPath, []byte("1"), 0600); err != nil {
		t.Fatal(err)
	}
	if removeSidecarAdmission(context.Background(), systemdSidecarState, systemdStopSidecar) {
		t.Fatal("stop failure was reported as removed admission")
	}
}
