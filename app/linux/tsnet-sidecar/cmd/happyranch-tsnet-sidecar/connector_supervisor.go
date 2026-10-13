package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/mdlayher/sdnotify"
)

type childHealth struct {
	Generation string `json:"generation"`
	Sequence   uint64 `json:"sequence"`
	State      string `json:"state"`
	Version    int    `json:"version"`
}

type notifySender interface{ Notify(...string) error }

// connectorDiagnostics records local observations, never a global root cause.
// Values come only from fixed call sites and supervisor-owned state. One first
// decision and one copy of each consequence bound volume even on canceled loops.
type connectorDiagnostics struct {
	generation string
	sequence   uint64
	first      bool
	seen       map[string]bool
}

func (d *connectorDiagnostics) emit(phase, reason string) {
	key := phase + ":" + reason
	if d.seen == nil {
		d.seen = make(map[string]bool)
	}
	if d.seen[key] {
		return
	}
	d.seen[key] = true
	generation := d.generation
	if generation == "" {
		generation = "unavailable"
	}
	fmt.Fprintf(os.Stderr, "connector_supervisor phase=%s reason=%s generation=%s sequence=%d\n", phase, reason, generation, d.sequence)
}

func (d *connectorDiagnostics) decision(reason string) {
	if d.seen["first:"+reason] {
		return
	}
	if !d.first {
		d.first = true
		d.emit("first", reason)
	} else {
		d.emit("consequence", reason)
	}
}

func runConnectorSupervisor(argv []string) int {
	diagnostics := &connectorDiagnostics{}
	if len(argv) == 0 {
		diagnostics.decision("invalid_invocation")
		fmt.Fprintln(os.Stderr, "connector_supervisor_invalid")
		return 2
	}
	notifier, err := sdnotify.New()
	if err != nil {
		diagnostics.decision("notifier_setup_failed")
		fmt.Fprintln(os.Stderr, "readiness_unavailable")
		return 1
	}
	return superviseConnector(context.Background(), argv, notifier, 80*time.Second, 12*time.Second, nil, systemdSidecarState, systemdStopSidecar)
}

type sidecarObservation uint8

const (
	sidecarUnknown sidecarObservation = iota
	sidecarAbsent
	sidecarPresentUnhealthy
	sidecarPresentHealthy
)

type sidecarHealthProbe func(context.Context) sidecarObservation
type sidecarStop func(context.Context) bool

// systemdSidecarPropertyNames is the exact closed set of named properties the
// supervisor observes.  The query is keyed so a reordered, missing, duplicated,
// or extra record can never be mistaken for readback of a different property.
var systemdSidecarPropertyNames = []string{"ActiveState", "SubState", "Result", "MainPID"}

func systemdSidecarState(parent context.Context) sidecarObservation {
	ctx, cancel := context.WithTimeout(parent, time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "systemctl", "show", "happyranch-tsnet-sidecar.service",
		"--property=ActiveState", "--property=SubState", "--property=Result", "--property=MainPID")
	cmd.Env = withoutNotifySocket(os.Environ())
	result, err := cmd.Output()
	if err != nil {
		return sidecarUnknown
	}
	properties, ok := parseSystemdShowProperties(string(result))
	if !ok {
		return sidecarUnknown
	}
	activeState := properties["ActiveState"]
	subState := properties["SubState"]
	resultValue := properties["Result"]
	pid, pidErr := strconv.ParseInt(properties["MainPID"], 10, 32)
	if activeState == "inactive" && subState == "dead" && pidErr == nil && pid == 0 && resultValue != "" {
		return sidecarAbsent
	}
	if activeState == "active" && subState == "running" && resultValue == "success" && pidErr == nil && pid > 1 {
		return sidecarPresentHealthy
	}
	if pidErr == nil && (pid == 0 || pid > 1) && (activeState == "active" || activeState == "activating" || activeState == "deactivating" || activeState == "failed") && subState != "" && resultValue != "" {
		return sidecarPresentUnhealthy
	}
	return sidecarUnknown
}

// parseSystemdShowProperties reads `systemctl show` named-property output.
// Every line must be a known Key=Value record, each key exactly once, and all
// four keys must be present with a nonempty value.  A single trailing newline
// is the only tolerated framing; any garbage, blank line, unknown or empty
// key, duplicate key, control character, or MainPID value carrying a second
// `=` fails closed.
func parseSystemdShowProperties(stdout string) (map[string]string, bool) {
	if stdout == "" {
		return nil, false
	}
	trimmed := strings.TrimSuffix(stdout, "\n")
	if trimmed == "" {
		return nil, false
	}
	properties := make(map[string]string, len(systemdSidecarPropertyNames))
	for _, line := range strings.Split(trimmed, "\n") {
		if line == "" {
			return nil, false
		}
		for i := 0; i < len(line); i++ {
			if line[i] < 0x20 || line[i] == 0x7f {
				return nil, false
			}
		}
		separator := strings.IndexByte(line, '=')
		if separator < 0 {
			return nil, false
		}
		key, value := line[:separator], line[separator+1:]
		known := false
		for _, candidate := range systemdSidecarPropertyNames {
			if key == candidate {
				known = true
				break
			}
		}
		if !known {
			return nil, false
		}
		if _, duplicate := properties[key]; duplicate {
			return nil, false
		}
		if key == "MainPID" && strings.IndexByte(value, '=') >= 0 {
			return nil, false
		}
		properties[key] = value
	}
	if len(properties) != len(systemdSidecarPropertyNames) {
		return nil, false
	}
	for _, key := range systemdSidecarPropertyNames {
		if properties[key] == "" {
			return nil, false
		}
	}
	return properties, true
}

func systemdStopSidecar(parent context.Context) bool {
	ctx, cancel := context.WithTimeout(parent, 3*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, "systemctl", "stop", "--no-block", "happyranch-tsnet-sidecar.service")
	cmd.Env = withoutNotifySocket(os.Environ())
	return cmd.Run() == nil
}

func withoutNotifySocket(env []string) []string {
	clean := make([]string, 0, len(env))
	for _, item := range env {
		if !strings.HasPrefix(item, "NOTIFY_SOCKET=") {
			clean = append(clean, item)
		}
	}
	return clean
}

func removeSidecarAdmission(ctx context.Context, healthy sidecarHealthProbe, stop sidecarStop) bool {
	return removeSidecarAdmissionObserved(ctx, healthy, stop, nil)
}

func removeSidecarAdmissionObserved(ctx context.Context, healthy sidecarHealthProbe, stop sidecarStop, diagnostics *connectorDiagnostics) bool {
	observe := func(reason string) {
		if diagnostics != nil {
			diagnostics.emit("consequence", reason)
		}
	}
	state := healthy(ctx)
	if state == sidecarAbsent {
		return true
	}
	if state == sidecarUnknown {
		observe("admission_initial_unknown")
		return false
	}
	if !stop(ctx) {
		observe("admission_stop_request_failed")
		return false
	}
	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			observe("admission_cancelled")
			return false
		case <-deadline.C:
			observe("admission_expired")
			return false
		case <-ticker.C:
			state = healthy(ctx)
			if state == sidecarAbsent {
				return true
			}
			if state == sidecarUnknown {
				observe("admission_poll_unknown")
				return false
			}
		}
	}
}

// connectorHealthJoinBound bounds the owned health-reader scanner shutdown so a
// misbehaving reader can never defer supervisor return indefinitely.
const connectorHealthJoinBound = 2 * time.Second

func superviseConnector(parent context.Context, argv []string, notifier notifySender, startupDeadline, staleAfter time.Duration, started chan<- *exec.Cmd, sidecarHealthy sidecarHealthProbe, stopSidecar sidecarStop) int {
	diagnostics := &connectorDiagnostics{}
	generationBytes := make([]byte, 16)
	if _, err := rand.Read(generationBytes); err != nil {
		diagnostics.decision("generation_setup_failed")
		return 1
	}
	generation := hex.EncodeToString(generationBytes)
	diagnostics.generation = generation
	diagnostics.emit("context", "invocation_started")
	reader, writer, err := os.Pipe()
	if err != nil {
		diagnostics.decision("health_pipe_setup_failed")
		return 1
	}
	defer reader.Close()
	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	cmd.ExtraFiles = []*os.File{writer}
	childEnv := make([]string, 0, len(os.Environ())+3)
	for _, item := range os.Environ() {
		if strings.HasPrefix(item, "NOTIFY_SOCKET=") ||
			strings.HasPrefix(item, "HAPPYRANCH_CHILD_HEALTH_FD=") ||
			strings.HasPrefix(item, "HAPPYRANCH_CHILD_HEALTH_GENERATION=") {
			continue
		}
		childEnv = append(childEnv, item)
	}
	cmd.Env = append(childEnv,
		"HAPPYRANCH_CHILD_HEALTH_FD=3",
		"HAPPYRANCH_CHILD_HEALTH_GENERATION="+generation,
	)
	if err := cmd.Start(); err != nil {
		diagnostics.decision("child_start_failed")
		writer.Close()
		return 1
	}
	writer.Close()
	if started != nil {
		started <- cmd
	}

	ctx, stopSignals := signal.NotifyContext(parent, syscall.SIGTERM, syscall.SIGINT)
	defer stopSignals()
	records := make(chan childHealth)
	protocolErr := make(chan error, 1)
	// scannerStop and scannerDone make the reader scanner an explicitly owned
	// helper rather than an abandoned goroutine.  A scanner that has decoded a
	// valid record can block forever on the unbuffered delivery, and closing
	// the read end alone cannot unblock a channel send, so the scanner selects
	// on scannerStop and the supervisor joins scannerDone within a bounded
	// lifecycle on every return path.
	scannerStop := make(chan struct{})
	scannerDone := make(chan struct{})
	go func() {
		defer close(scannerDone)
		scanChildHealth(reader, records, protocolErr, scannerStop)
	}()
	defer func() {
		close(scannerStop)
		_ = reader.Close()
		select {
		case <-scannerDone:
		case <-time.After(connectorHealthJoinBound):
			// Fail loudly rather than silently abandoning an owned helper.
			diagnostics.emit("consequence", "scanner_join_timeout")
			fmt.Fprintln(os.Stderr, "connector_health_scanner_join_timeout")
		}
	}()
	waited := make(chan error, 1)
	go func() { waited <- cmd.Wait() }()
	// The initial deadline is intentionally absolute.  It is anchored once and
	// never refreshed by waiting, partial progress, or a health observation
	// that only completes after it.
	startupDeadlineAt := time.Now().Add(startupDeadline)
	timer := time.NewTimer(startupDeadline)
	defer timer.Stop()
	var sequence uint64
	childReady := false
	ready := false
	// terminal latches the readiness decision independently of permission to
	// signal or reap the child.  Expiry, failure, cancellation or any stop
	// decision permanently ends READY/WATCHDOG publication, including while
	// the sidecar is still present or its admission state stays unknown and
	// the child therefore cannot yet be cleaned up.
	terminal := false
	// stopping records confirmed sidecar-absence admission and the connector
	// child TERM.  It gates child cleanup only and never revives readiness.
	stopping := false
	// stoppingNotified is the single at-most-once latch for the terminal
	// STOPPING notification.  It is deliberately independent of ``stopping``
	// (child cleanup permission): a refused admission stop must not let a later
	// spontaneous child exit emit a second STOPPING.
	stoppingNotified := false
	notifyStopping := func(status string) {
		if stoppingNotified {
			return
		}
		stoppingNotified = true
		if notifier.Notify("STOPPING=1", status) != nil {
			diagnostics.emit("consequence", "stopping_notify_failed")
		}
	}
	stopChild := func(reason string) {
		diagnostics.decision(reason)
		terminal = true
		notifyStopping("STATUS=connector stopping")
		if stopping {
			return
		}
		// The sidecar owns external admission.  Signal its MainPID and wait
		// for systemd to observe it inactive before beginning connector
		// child cleanup.  Both services retain their own MainPID ownership.
		if !removeSidecarAdmissionObserved(context.Background(), sidecarHealthy, stopSidecar, diagnostics) {
			diagnostics.emit("consequence", "admission_removal_refused")
			return
		}
		stopping = true
		if cmd.Process.Signal(syscall.SIGTERM) != nil {
			diagnostics.emit("consequence", "child_term_failed")
		}
	}
	// observePreReady runs one pre-READY health observation bounded by the
	// original absolute startup deadline.  A healthy result that completes
	// after that deadline is never accepted, so a late query completion can
	// never refresh or extend the startup window.  Cancellation is rechecked
	// after the query returns: a completed-but-late delivery that races the
	// supervisor context cancellation must never publish a positive receipt.
	observePreReady := func() (sidecarObservation, bool) {
		if !ready && !time.Now().Before(startupDeadlineAt) {
			return sidecarUnknown, false
		}
		observationCtx, cancelObservation := context.WithDeadline(ctx, startupDeadlineAt)
		defer cancelObservation()
		observation := sidecarHealthy(observationCtx)
		// Observation classification is emitted by the caller after deadline checks.
		if ctx.Err() != nil {
			return observation, false
		}
		if !ready && !time.Now().Before(startupDeadlineAt) {
			return observation, false
		}
		return observation, true
	}
	// Each accepted pair owns an absolute freshness deadline. Anchor it at
	// publication, so query/result/notify delivery cannot restart elapsed time.
	var freshnessDeadlineAt time.Time
	deadlineReason := func() string {
		if ctx.Err() != nil {
			return "context_cancelled"
		}
		if ready {
			return "freshness_expired"
		}
		return "startup_expired"
	}
	observeSidecar := func(observation sidecarObservation) {
		switch observation {
		case sidecarUnknown:
			diagnostics.emit("observation", "sidecar_unknown")
		case sidecarAbsent:
			diagnostics.emit("observation", "sidecar_absent")
		case sidecarPresentUnhealthy:
			diagnostics.emit("observation", "sidecar_unhealthy")
		}
	}
	resetStale := func(publishedAt time.Time) {
		freshnessDeadlineAt = publishedAt.Add(staleAfter)
		if !timer.Stop() {
			select {
			case <-timer.C:
			default:
			}
		}
		timer.Reset(time.Until(freshnessDeadlineAt))
	}
	// finishChildExit handles the child's single owned Wait exactly once.  It
	// is shared by the priority check and the blocking select so the terminal
	// child-exit path can never be starved by a cancellation wakeup.
	finishChildExit := func(err error) int {
		if err != nil {
			diagnostics.decision("child_exit_error")
		} else if stopping {
			diagnostics.decision("child_exit_after_stop")
		} else {
			diagnostics.decision("child_exit_unexpected_clean")
		}
		admissionRemoved := removeSidecarAdmissionObserved(context.Background(), sidecarHealthy, stopSidecar, diagnostics)
		if !admissionRemoved {
			diagnostics.emit("consequence", "admission_removal_refused")
		}
		// The terminal STOPPING notification shares the single at-most-once
		// latch with stopChild.  ``stopping`` only records child-cleanup
		// permission, so a refused admission stop followed by a spontaneous
		// child exit must not emit a second STOPPING.
		terminal = true
		notifyStopping("STATUS=connector exited")
		if err == nil && stopping && admissionRemoved {
			return 0
		}
		return 1
	}
	// fenceChildExit reconciles a child that has already completed its single
	// owned Wait before any positive notification is published.  A health
	// observation can be in flight when the child exits, so the outer-loop
	// priority check cannot observe that exit until the observation returns; a
	// stale completed healthy result must therefore never publish READY or
	// WATCHDOG.  The fence first re-reads the one owned Wait result; because the
	// kernel can reap the exited child before the Wait goroutine has delivered
	// that result on ``waited``, it also probes the authoritative reaped state
	// so a positive boundary can never follow an already-reaped child.  Once the
	// process is gone the owned Wait result is imminent, so the fence blocks on
	// it to recover the terminal exit classification.
	fenceChildExit := func() (int, bool) {
		select {
		case err := <-waited:
			return finishChildExit(err), true
		default:
		}
		if err := cmd.Process.Signal(syscall.Signal(0)); err != nil &&
			(errors.Is(err, os.ErrProcessDone) || errors.Is(err, syscall.ESRCH)) {
			return finishChildExit(<-waited), true
		}
		return 0, false
	}
	for {
		// A completed child Wait is the terminal event for this supervisor.
		// Give it priority over another cancellation wakeup: when the sidecar
		// admission state stays unknown, a canceled supervisor can otherwise
		// re-run the bounded admission probe on every select pass and delay
		// observing its own already-exited child past the accepted
		// admission+teardown bound.  The probe is still retried while the
		// child is alive, so confirmed absence before child TERM is preserved.
		select {
		case err := <-waited:
			return finishChildExit(err)
		default:
		}
		select {
		case <-ctx.Done():
			stopChild("context_cancelled")
		case <-timer.C:
			stopChild(deadlineReason())
		case err := <-protocolErr:
			if err != nil {
				reason := "health_read_failed"
				switch err.Error() {
				case "child_health_malformed":
					reason = "health_malformed"
				case "child_health_noncanonical":
					reason = "health_noncanonical"
				case "child_health_partial":
					reason = "health_partial"
				}
				stopChild(reason)
			}
		case record, ok := <-records:
			if !ok {
				diagnostics.emit("observation", "health_pipe_closed")
				records = nil
				continue
			}
			if record.Version != 1 || record.Generation != generation || record.Sequence != sequence+1 {
				reason := "health_sequence_rejected"
				if record.Version != 1 {
					reason = "health_version_rejected"
				} else if record.Generation != generation {
					reason = "health_generation_rejected"
				}
				stopChild(reason)
				continue
			}
			sequence = record.Sequence
			diagnostics.sequence = sequence
			switch record.State {
			case "waiting":
				if ready {
					stopChild("readiness_regressed")
				}
			case "ready":
				if terminal || childReady || ready || stopping {
					stopChild("ready_state_rejected")
				} else {
					childReady = true
					observation, withinStartup := observePreReady()
					if !withinStartup {
						stopChild(deadlineReason())
						continue
					}
					observeSidecar(observation)
					if observation == sidecarPresentHealthy {
						if ctx.Err() != nil {
							stopChild(deadlineReason())
							continue
						}
						if code, exited := fenceChildExit(); exited {
							return code
						}
						publishedAt := time.Now()
						if ctx.Err() != nil || !publishedAt.Before(startupDeadlineAt) {
							stopChild(deadlineReason())
							continue
						}
						if notifier.Notify("READY=1", "STATUS=composite healthy") != nil {
							stopChild("ready_notify_failed")
							continue
						}
						ready = true
						resetStale(publishedAt)
					}
				}
			case "healthy":
				if terminal || !childReady || stopping {
					stopChild("healthy_state_rejected")
				} else if !ready {
					// Pre-READY composite gate.  Whichever service started
					// first, the observation is bounded by and re-checked
					// against the original absolute startup deadline.
					observation, withinStartup := observePreReady()
					if !withinStartup {
						stopChild(deadlineReason())
						continue
					}
					observeSidecar(observation)
					if observation != sidecarPresentHealthy {
						// Connector-first startup: retain the original absolute
						// deadline while the independently starting sidecar
						// finishes.
						continue
					}
					if ctx.Err() != nil {
						stopChild(deadlineReason())
						continue
					}
					if code, exited := fenceChildExit(); exited {
						return code
					}
					publishedAt := time.Now()
					if ctx.Err() != nil || !publishedAt.Before(startupDeadlineAt) {
						stopChild(deadlineReason())
						continue
					}
					if notifier.Notify("READY=1", "STATUS=composite healthy") != nil {
						stopChild("ready_notify_failed")
						continue
					}
					ready = true
					resetStale(publishedAt)
				} else {
					// The query cannot borrow time beyond current freshness.
					// A pending timer event is not proof that its deadline
					// remains open: query/result delivery may win the select.
					if !time.Now().Before(freshnessDeadlineAt) {
						stopChild(deadlineReason())
						continue
					}
					observationCtx, cancelObservation := context.WithDeadline(ctx, freshnessDeadlineAt)
					observation := sidecarHealthy(observationCtx)
					cancelObservation()
					switch {
					case ctx.Err() != nil || !time.Now().Before(freshnessDeadlineAt):
						stopChild(deadlineReason())
					case observation != sidecarPresentHealthy:
						reason := "sidecar_unknown"
						if observation == sidecarAbsent {
							reason = "sidecar_absent"
						} else if observation == sidecarPresentUnhealthy {
							reason = "sidecar_unhealthy"
						}
						stopChild(reason)
					default:
						if code, exited := fenceChildExit(); exited {
							return code
						}
						// Recheck at publication after the child-exit fence;
						// no expired result can emit or renew WATCHDOG.
						publishedAt := time.Now()
						if ctx.Err() != nil || !publishedAt.Before(freshnessDeadlineAt) {
							stopChild(deadlineReason())
							continue
						}
						if notifier.Notify("WATCHDOG=1") != nil {
							stopChild("watchdog_notify_failed")
						} else {
							resetStale(publishedAt)
						}
					}
				}
			case "stopping", "failed":
				reason := "child_reported_stopping"
				if record.State == "failed" {
					reason = "child_reported_failed"
				}
				stopChild(reason)
			default:
				stopChild("health_state_unknown")
			}
		case err := <-waited:
			return finishChildExit(err)
		}
	}
}

// scanChildHealth is the supervisor-owned health reader.  It decodes only
// canonical versioned records and delivers them on records.  Delivery is
// cancellation-aware: stop lets the supervisor's bounded teardown unblock a
// decoded record that is waiting on the unbuffered channel, so the helper is
// always joined instead of being abandoned after its reader is closed.
func scanChildHealth(reader io.Reader, records chan<- childHealth, failed chan<- error, stop <-chan struct{}) {
	defer close(records)
	scanner := bufio.NewScanner(reader)
	scanner.Buffer(make([]byte, 256), 4096)
	for scanner.Scan() {
		var record childHealth
		line := scanner.Bytes()
		if len(line) == 0 || json.Unmarshal(line, &record) != nil {
			failed <- errors.New("child_health_malformed")
			return
		}
		canonical, _ := json.Marshal(record)
		if !bytes.Equal(line, canonical) {
			failed <- errors.New("child_health_noncanonical")
			return
		}
		var shape map[string]json.RawMessage
		if json.Unmarshal(line, &shape) != nil || len(shape) != 4 {
			failed <- errors.New("child_health_malformed")
			return
		}
		for _, key := range []string{"version", "generation", "sequence", "state"} {
			if _, ok := shape[key]; !ok {
				failed <- errors.New("child_health_partial")
				return
			}
		}
		select {
		case records <- record:
		case <-stop:
			return
		}
	}
	if err := scanner.Err(); err != nil {
		failed <- fmt.Errorf("child_health_read: %w", err)
	}
}

func healthRecord(generation string, sequence uint64, state string) string {
	record := childHealth{Version: 1, Generation: generation, Sequence: sequence, State: state}
	raw, _ := json.Marshal(record)
	return string(raw) + "\n"
}
