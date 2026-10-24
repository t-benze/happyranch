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
	"sync"
	"sync/atomic"
	"time"

	"github.com/mdlayher/sdnotify"
	sidecar "happyranch/linux-tsnet-sidecar"
	"golang.org/x/sys/unix"
)

type childHealth struct {
	Generation string `json:"generation"`
	Sequence   uint64 `json:"sequence"`
	State      string `json:"state"`
	Version    int    `json:"version"`
	WorkerPID int `json:"worker_pid,omitempty"`
	WorkerStartTicks int64 `json:"worker_start_ticks,omitempty"`
    // Wrapper-local pipe receipt clocks, never wire fields or authority from
    // the child. Queued scanner delivery cannot renew the original lease.
    receivedAt time.Time
    receivedNS int64
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
    entry:=time.Now()
    notifier,err:=sdnotify.New()
    if err!=nil{return superviseManagedConnector(argv,nil,entry)}
    return superviseManagedConnector(argv,notifier,entry)
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
	// Preserve the physical terminator for v2 while keeping ScanLines' token
	// boundaries and buffering limits for the historical v1 decoder.
	scanner.Split(func(data []byte, atEOF bool) (int, []byte, error) {
		advance, token, err := bufio.ScanLines(data, atEOF)
		if token == nil {
			return advance, nil, err
		}
		return advance, data[:advance], err
	})
	for scanner.Scan() {
		var record childHealth
		frame := scanner.Bytes()
		_, line, _ := bufio.ScanLines(frame, true)
		if len(line) == 0 || json.Unmarshal(line, &record) != nil {
			failed <- errors.New("child_health_malformed")
			return
		}
		// Check the retained bytes, never a reconstructed newline. CRLF and
		// unterminated EOF cannot earn receipt clocks or stopping cooperation.
		if record.Version == 2 && (len(frame) != len(line)+1 || frame[len(frame)-1] != '\n' || len(frame) > 1024) {
			failed <- errors.New("child_health_malformed")
			return
		}
		canonical, _ := json.Marshal(record)
		if !bytes.Equal(line, canonical) {
			failed <- errors.New("child_health_noncanonical")
			return
		}
		var shape map[string]json.RawMessage
		if json.Unmarshal(line, &shape) != nil || (record.Version==1 && len(shape)!=4) || (record.Version==2 && len(shape)!=6) || (record.Version!=1 && record.Version!=2) {
			failed <- errors.New("child_health_malformed")
			return
		}
		for _, key := range []string{"version", "generation", "sequence", "state"} {
			if _, ok := shape[key]; !ok {
				failed <- errors.New("child_health_partial")
				return
			}
		}
		if record.Version==2 && (record.WorkerPID<2 || record.WorkerStartTicks<=0) {
			failed <- errors.New("child_health_malformed")
			return
		}
        if record.Version==2{
            record.receivedAt=time.Now()
            stamp,e:=sidecar.LifecycleNow();if e!=nil{failed<-sidecar.ErrLifecycle;return};record.receivedNS=stamp
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

// superviseManagedConnector owns exactly one launch, Wait, scanner and terminal
// boundary. PID1 owns all later scheduling; runtime retirement never StopUnits.
func superviseManagedConnector(argv []string, notifier notifySender, entry time.Time) (result int) {
    var intentional,signalFailure,commanded,unexpectedWait,readyObserved atomic.Bool
    var generation string
    var sequence uint64
    revision,rateCount:=int64(0),0
    var diagnosticGeneration *string
    events,bytesEmitted:=0,0;seen:=map[string]bool{};decisionEmitted:=false
    terminalKind:="cleanup_blocked"
    stopCertified:=false
    unchangedRateReader:=false
    var deferredTerminalFence func()bool
    var finalBound time.Time
    emit:=func(phase,reason,outcome string,code *int){
        key:=phase+":"+reason
        if seen[key] || events>=16 || phase!="terminal" && events>=15 || phase=="decision" && decisionEmitted{return}
        var out bytes.Buffer
        var rev *int64;if revision>0{rev=&revision}
        origin:="unknown";if intentional.Load() && reason!="publication_observed"{origin="intentional_request"}
        seq:=uint64(0);if diagnosticGeneration!=nil{seq=sequence}
        if sidecar.EmitLifecycleDiagnostic(&out,phase,reason,origin,outcome,code,rev,diagnosticGeneration,seq,rateCount)!=nil || bytesEmitted+out.Len()>8192{return}
        seen[key]=true;if phase=="decision"{decisionEmitted=true};events++;bytesEmitted+=out.Len()
        _,_=os.Stderr.Write(out.Bytes()) // best effort; never a durable failure/cleanup receipt
    }
    refuse:=func(err error)int{
        reason:=""
        switch sidecar.LifecycleFailureCause(err){case "io_failure":reason="state_io_failed";case "invalid_state":reason="state_invalid";case "identity_loss":reason="identity_lost"}
        if reason!=""{emit("decision",reason,"blocked",nil)}
        return 78 // unavailable proof supplies no invented typed cause
    }
    // Registered before all entry refusals. This is the LAST return observer;
    // resource/final-gate defers run first and cannot later contradict its exit.
    defer func(){
        if deferredTerminalFence!=nil && !deferredTerminalFence(){result=78}
        if signalFailure.Load() || !finalBound.IsZero() && !time.Now().Before(finalBound){result=78}
        if result==0 && !stopCertified{result=78}
        reason,outcome:="cleanup_blocked","blocked"
        if result==0{reason,outcome="intentional_stop","stopped"}else if result==1{
            reason,outcome="restart_requested","restart_requested"
            if terminalKind=="rate_deferred" || terminalKind=="rate_window_exhausted"{reason,outcome=terminalKind,"deferred"}
        }else{result=78}
        // Only the unchanged no-claim reader is quiet. Settled signals and
        // the original final fences above still turn any late refusal into78.
        if unchangedRateReader && result==1{return}
        emit("terminal",reason,outcome,&result)
    }()
    // An actual supplied earlier bound is a limit. Independently launched
    // PID1 units retain their own original activation/startup bounds.
    var earlier time.Time
    if raw:=os.Getenv("HAPPYRANCH_LIFECYCLE_DEADLINE_NS");raw!="" {
        sampled:=time.Now();now,e:=sidecar.LifecycleNow();n,parseErr:=strconv.ParseInt(raw,10,64)
        if e!=nil || parseErr!=nil || strconv.FormatInt(n,10)!=raw || n<=now {emit("decision","startup_expired","blocked",nil);return 78}
        earlier=sampled.Add(time.Duration(n-now))
    }
    clip:=func(t time.Time)time.Time{if !earlier.IsZero() && earlier.Before(t){return earlier};return t}
    startupDeadline:=clip(entry.Add(80*time.Second))
    startup,cancelStartup:=context.WithDeadline(context.Background(),startupDeadline);defer cancelStartup()
    // First observed terminal event owns its time, reason and kernel stamp.
    // Wait and signals can observe it during a blocking identity/state query.
    var terminalOnce sync.Once
    var terminalLatched atomic.Bool
    terminalDone:=make(chan struct{})
    var terminalAt time.Time
    var terminalNS int64
    var terminalReason string
    latch:=func(reason string){terminalOnce.Do(func(){
        terminalAt=time.Now();terminalReason=reason;terminalLatched.Store(true)
        stamp,e:=sidecar.LifecycleNow();elapsed:=time.Since(terminalAt)
        if e==nil && elapsed>=0 && int64(elapsed)<stamp{terminalNS=stamp-int64(elapsed)}
        close(terminalDone)
    })}
    terminalSeen:=func()bool{return terminalLatched.Load()}
    var finalOutput func(int)int
    signals:=make(chan os.Signal,8)
    signal.Notify(signals,syscall.SIGTERM,syscall.SIGINT,syscall.SIGUSR2)
    signalStop,signalDone:=make(chan struct{}),make(chan struct{})
    acceptSignal:=func(sig os.Signal){
        if sig==syscall.SIGTERM || sig==syscall.SIGINT{intentional.Store(true);latch("intentional_stop")}else{signalFailure.Store(true);latch("watchdog_failed")}
    }
    go func(){
        defer close(signalDone)
        startupDone:=startup.Done()
        var earlierTimer *time.Timer
        var earlierDone <-chan time.Time
        if !earlier.IsZero(){earlierTimer=time.NewTimer(time.Until(earlier));earlierDone=earlierTimer.C;defer earlierTimer.Stop()}
        for{select{
            case sig:=<-signals:acceptSignal(sig)
            case <-startupDone:if !readyObserved.Load(){latch("startup_expired")};startupDone=nil
            case <-earlierDone:latch("startup_expired");earlierDone=nil
            case <-signalStop:return
        }}
    }()
    var settleSignalsOnce sync.Once
    settleSignals:=func(){settleSignalsOnce.Do(func(){
        signal.Stop(signals);close(signalStop);<-signalDone
        for{select{case sig:=<-signals:acceptSignal(sig);default:return}}
    })}
    defer func(){
        settleSignals()
        if signalFailure.Load(){result=78}
        if terminalSeen(){bound:=clip(terminalAt.Add(30*time.Second));if finalBound.IsZero() || bound.Before(finalBound){finalBound=bound};if !time.Now().Before(finalBound){result=78}}
    }()
    stopped:=terminalSeen
    if stopped(){emit("decision","startup_expired","blocked",nil);return 78}
    if len(argv)!=5 || argv[0]!="/opt/happyranch/bin/happyranch-connector" || strings.Join(argv[1:]," ")!="run --managed --config /etc/happyranch/connector.json" {emit("decision","state_invalid","blocked",nil);return 78}
    if notifier==nil{emit("decision","watchdog_failed","blocked",nil);return 78}
    classificationDeadline:=clip(entry.Add(2*time.Second))
    classification,cancelClassification:=context.WithDeadline(startup,classificationDeadline)
    pair,probeErr:=sidecar.OpenLifecycle(classification)
    deferred:=false
    needsBootPreparation:=false
    if probeErr==nil{
        boot,e:=sidecar.LifecycleBoot();stamp,clockErr:=sidecar.LifecycleNow()
        needsBootPreparation=e==nil && (pair.Main.RuntimeBoot!=boot || pair.Sidecar.RuntimeBoot!=boot || pair.Main.Phase=="boot_prepared")
        if e==nil && clockErr==nil && pair.Main.RuntimeBoot==boot && pair.Sidecar.RuntimeBoot==boot && pair.Main.Clean && pair.Eligible(boot)==nil{
            count:=0;for _,spend:=range pair.Main.Rate.Spends{if stamp-spend<sidecar.ManagedWindowNS{count++}}
            rateCount=count
            deferred=count==3 || pair.Main.Rate.NotBeforeNS!=nil && stamp<*pair.Main.Rate.NotBeforeNS
        }
        pair.Close()
    }
    // Decide while startup can still continue. A Go return observer cannot
    // resume startup when rate eligibility changes during the final proof.
    // This bounded decision grants neither a spend nor an old cleanup receipt.
    classifyRate:=func(proofDeadline time.Time)(bool,error){
        if stopped() || !time.Now().Before(proofDeadline){return false,sidecar.ErrLifecycle}
        check,finish:=context.WithDeadline(context.Background(),proofDeadline);defer finish()
        if sidecar.RunLifecyclePreflight(check)!=nil{return false,sidecar.ErrLifecycle}
        self,e:=sidecar.CurrentLifecycleProcess();if e!=nil{return false,e}
        pin,e:=sidecar.PinLifecycleProcess(self);if e!=nil{return false,e};defer pin.Close()
        if e=sidecar.ValidateLifecyclePin(check,pin,"happyranch-connector.service",true);e!=nil{return false,e}
        current,e:=sidecar.OpenLifecycle(check);if e!=nil{return false,e};defer current.Close()
        boot,bErr:=sidecar.LifecycleBoot()
        if bErr!=nil || current.Eligible(boot)!=nil || !current.Main.Clean || !sidecar.LifecycleEmptyOwner(check,current,self){return false,sidecar.ErrLifecycle}
        // EmptyOwner repeats installed+loaded/fixed-unit+job/process authority.
        // Classify AFTER it and Refresh, within the selected ORIGINAL bound.
        if e=current.Refresh();e!=nil{return false,e}
        stamp,tErr:=sidecar.LifecycleNow()
        if tErr!=nil || current.Eligible(boot)!=nil || !current.Main.Clean || stopped() || check.Err()!=nil || !time.Now().Before(proofDeadline){return false,sidecar.ErrLifecycle}
        count:=0;for _,spend:=range current.Main.Rate.Spends{if stamp-spend<sidecar.ManagedWindowNS{count++}}
        rateCount=count
        stillDeferred:=count==3 || current.Main.Rate.NotBeforeNS!=nil && stamp<*current.Main.Rate.NotBeforeNS
        // An eligible read reserves neither admission nor its policy revision.
        // Bind diagnostics at actual claim, or this proven final defer only.
        if stillDeferred{revision=current.Main.Policy.Revision;terminalKind="rate_deferred";if count==3{terminalKind="rate_window_exhausted"}}
        return stillDeferred,nil
    }
    if deferred{
        // No child/engine/listener/READY/watchdog and no sleeping through debt.
        // PID1's existing RestartSec30 is the only later scheduler.
        stillDeferred,e:=classifyRate(classificationDeadline)
        if e!=nil{cancelClassification();return refuse(e)}
        if stillDeferred{
            cancelClassification();finalBound=classificationDeadline
            // Last return observer sees signals settled by the earlier defer.
            // Rate itself was decided before return, while startup could resume.
            deferredTerminalFence=func()bool{return !stopped() && time.Now().Before(classificationDeadline)}
            unchangedRateReader=true
            return 1
        }
        // Release the pair and re-prove a fresh claim/Spend under the remaining
        // ORIGINAL entry+80 startup. No permission survives this unlock.
    }
    cancelClassification()
    if probeErr!=nil{return refuse(probeErr)}
    // Repeated unchanged readers derive status from the ledger, without a
    // context or terminal transition. Real boot/admission work remains visible.
    emit("context","invocation_started","pending",nil)
    if e:=sidecar.RunLifecyclePreflight(startup);e!=nil{return refuse(e)};if stopped(){return 78}
    if e:=sidecar.PrepareLifecycleBoot(startup);e!=nil{emit("context","boot_refused","blocked",nil);return refuse(e)};if stopped(){return 78}
    emit("context","boot_validated","pending",nil)
    if needsBootPreparation{
        // The independent boot writers used this invocation's ORIGINAL80s
        // startup bound. Carried debt is no lost-claim race: no claim exists.
        // Re-prove complete current empty ownership before leaving PID1 its
        // sole RestartSec30 schedule. Do not mint a fresh reader2s allowance.
        stillDeferred,e:=classifyRate(startupDeadline)
        if e!=nil{return refuse(e)}
        if stillDeferred{
            finalBound=startupDeadline
            deferredTerminalFence=func()bool{return !stopped() && time.Now().Before(startupDeadline)}
            return 1
        }
    }
    self,err:=sidecar.CurrentLifecycleProcess();if err!=nil{return refuse(err)}
    selfPin,err:=sidecar.PinLifecycleProcess(self);if err!=nil{return refuse(err)};defer selfPin.Close()
    if e:=sidecar.ValidateLifecyclePin(startup,selfPin,"happyranch-connector.service",true);e!=nil{return refuse(e)}
    generation,err=sidecar.NewLifecycleID();if err!=nil{return refuse(err)}

    // All registrations and the fallback owner exist BEFORE a spend may become
    // visible. An unsuccessful Start owns pipes, but never invents a Wait.
    var boundSide,launcher,worker *sidecar.LifecyclePin
    var reader,writer *os.File
    var ownedChildren []*sidecar.LifecyclePin
    var cmd *exec.Cmd
    var waitErr,scannerErr,scannerCloseErr error
    waitDone,scannerDone:=make(chan struct{}),make(chan struct{})
    records:=make(chan childHealth)
    scannerStop:=make(chan struct{})
    var scannerStopOnce sync.Once
    started,scannerStarted,claimAttempted:=false,false,false
    retired:=false;retirementResult:=78
    var retiring atomic.Bool
    workerStopped,childTermSent,launcherTermSent:=false,false,false
    var childTermAt time.Time
    stickyFailure:=false
    var policy sidecar.LifecyclePolicy
    var runtimeBoot,transition string
    recovered:=false
    closeScanner:=func(){scannerStopOnce.Do(func(){close(scannerStop);if reader!=nil{scannerCloseErr=reader.Close()}})}
    // Both normal health and teardown consume this SAME generation/sequence.
    // Only an authenticated stopping record AFTER commanded TERM is a worker
    // cooperation witness; pidfd death or a successful launcher Wait is not.
    duplicateRecord:=func(record childHealth)bool{
        return record.Version==2 && record.Generation==generation && record.Sequence>0 && record.Sequence<=sequence && worker!=nil &&
            record.WorkerPID==worker.Identity.PID && record.WorkerStartTicks==worker.Identity.StartTicks &&
            (record.State=="waiting" || record.State=="ready" || record.State=="healthy")
    }
    acceptRecord:=func(record childHealth,ctx context.Context,teardown bool)bool{
        if ctx.Err()!=nil || record.Version!=2 || record.Generation!=generation || sequence==^uint64(0) || record.Sequence==0 || record.Sequence!=sequence+1 ||
            record.WorkerPID<2 || record.WorkerPID>2147483647 || record.WorkerStartTicks<=0 ||
            record.receivedAt.IsZero() || record.receivedNS<=0 || record.receivedNS>int64(1<<63-1)-sidecar.ManagedLeaseNS{return false}
        // Terminal states have no live positive role. In retirement only this
        // accepted worker's advancing post-command stopping can cooperate;
        // failed, first/stale stopping and pre-TERM stopping always refuse.
        switch record.State{case "waiting","ready","healthy":case "stopping":if !teardown || !childTermSent || childTermAt.IsZero() || !record.receivedAt.After(childTermAt){return false};default:return false}
        identity:=sidecar.LifecycleProcess{PID:record.WorkerPID,StartTicks:record.WorkerStartTicks,Invocation:self.Invocation}
        if worker==nil{
            // A signal can win before the first waiting/ready delivery. Only
            // a real positive record, before worker TERM, can pin that owner.
            if launcher==nil || childTermSent || (record.State!="waiting" && record.State!="ready"){return false}
            candidate,e:=sidecar.PinLifecycleProcess(identity)
            if e!=nil{return false}
            if sidecar.ValidateLifecycleWorker(ctx,candidate,launcher.Identity.PID)!=nil || ctx.Err()!=nil{candidate.Close();return false}
            worker=candidate
        }else if worker.Identity!=identity || !worker.Dead() && worker.Validate()!=nil{return false}
        // Recheck the original receipt after slow identity work for BOTH
        // stream roles. A live context cannot renew wall/kernel receipt time.
        stamp,e:=sidecar.LifecycleNow()
        if e!=nil || stamp<record.receivedNS || stamp-record.receivedNS>=sidecar.ManagedLeaseNS ||
            ctx.Err()!=nil || !time.Now().Before(record.receivedAt.Add(12*time.Second)){return false}
        if bound,ok:=ctx.Deadline();ok && !time.Now().Before(bound){return false}
        sequence=record.Sequence
        if record.State=="stopping"{workerStopped=true}
        return true
    }
    consumeWait:=func()bool{if !started{return false};select{case <-waitDone:return true;default:return false}}
    failureCause:="none"
    stoppedPolicyFollows:=func(admission,current sidecar.LifecyclePolicy)bool{
        return current.InstallationID==admission.InstallationID && current.Desired=="stopped" && current.HardBlock=="none" &&
            (current==admission || current.Revision>admission.Revision && current.OperationID!=admission.OperationID)
    }
    ownedInhibition:=func(p *sidecar.LifecyclePair,boot string,now int64)bool{
        return boot==runtimeBoot && p.CurrentInhibition(boot,now) && p.Main.BootTransitionID==transition &&
            p.Main.Wrapper!=nil && *p.Main.Wrapper==self && p.Main.Generation!=nil && *p.Main.Generation==generation &&
            stoppedPolicyFollows(policy,p.Main.Policy) &&
            launcher!=nil && p.Main.Launcher!=nil && *p.Main.Launcher==launcher.Identity &&
            (p.Main.Worker==nil || worker!=nil && *p.Main.Worker==worker.Identity) &&
            boundSide!=nil && p.Main.Sidecar!=nil && *p.Main.Sidecar==boundSide.Identity && p.Sidecar.Binding!=nil && *p.Sidecar.Binding==boundSide.Identity
    }
    retire:=func(reason string)int{
        // A later observed failure remains available even when an earlier
        // terminal event already owns T/reason. Only time/reason are first-wins.
        if reason=="state_io_failed" || reason=="state_invalid" || reason=="identity_lost" || reason=="health_invalid" || reason=="watchdog_failed"{stickyFailure=true}
        if failureCause=="none"{switch reason{case "state_io_failed":failureCause="io_failure";case "state_invalid","health_invalid":failureCause="invalid_state";case "identity_lost":failureCause="identity_loss"}}
        latch(reason);<-terminalDone
        if retired{return retirementResult};retired=true;retiring.Store(true)
        deadline:=clip(terminalAt.Add(30*time.Second))
        removalDeadline:=clip(terminalAt.Add(10*time.Second));if deadline.Before(removalDeadline){removalDeadline=deadline}
        childDeadline:=clip(terminalAt.Add(20*time.Second));if deadline.Before(childDeadline){childDeadline=deadline}
        cleanup,cancel:=context.WithDeadline(context.Background(),deadline);defer cancel()
        child,finishChild:=context.WithDeadline(cleanup,childDeadline);defer finishChild()
        childPhase:=func()bool{
            if child.Err()!=nil || !time.Now().Before(childDeadline){stickyFailure=true;return false}
            return true
        }
        // This owner must actually observe Wait before the original child
        // fence. Later channel availability supplies no historical timestamp.
        // Keep a timely observation across independent scanner/cleanup work.
        childWaitTimely:=false
        observeChildWait:=func()bool{
            if !time.Now().Before(deadline) || !consumeWait(){return false}
            if !childWaitTimely && childPhase(){childWaitTimely=true}
            return true
        }
        childRecords:=records
        retirementRecord:=func(record childHealth)bool{
            if cleanup.Err()!=nil || !time.Now().Before(deadline){return false}
            // SAME positive duplicates are inert in all stream consumers.
            // They supply no new receipt, sequence, lease or stopping proof.
            if duplicateRecord(record){return true}
            stamp,e:=sidecar.LifecycleNow()
            if e!=nil || record.receivedAt.IsZero() || record.receivedNS<=0 || record.receivedNS>stamp || stamp-record.receivedNS>=sidecar.ManagedLeaseNS{return false}
            bound:=record.receivedAt.Add(12*time.Second);if deadline.Before(bound){bound=deadline}
            if !time.Now().Before(bound){return false}
            observation,finish:=context.WithDeadline(cleanup,bound);defer finish()
            return acceptRecord(record,observation,true) && observation.Err()==nil && time.Now().Before(bound)
        }
        if terminalReason=="cleanup_blocked"{emit("cleanup","cleanup_blocked","blocked",nil)}else{emit("decision",terminalReason,"pending",nil)}
        if notifier.Notify("STOPPING=1","STATUS=connector stopping")!=nil{stickyFailure=true}
        if writer!=nil{if writer.Close()!=nil{stickyFailure=true};writer=nil}
        // No state/query/publication failure skips the physical Wait/scanner
        // disposition below. State failure and physical cleanup are separate.
        if terminalReason=="state_io_failed" || terminalReason=="state_invalid" || terminalReason=="identity_lost" || terminalReason=="health_invalid" || signalFailure.Load(){stickyFailure=true}
        observePair:=func(p *sidecar.LifecyclePair)bool{
            if p.Sidecar.Failure!="none" || p.Main.Block!="none" || p.Main.Policy.HardBlock!="none"{stickyFailure=true}
            stamp,clockErr:=sidecar.LifecycleNow()
            inhibited:=clockErr==nil && ownedInhibition(p,runtimeBoot,stamp)
            if inhibited{intentional.Store(true)}
            return p.Main.Wrapper!=nil && *p.Main.Wrapper==self && p.Main.Generation!=nil && *p.Main.Generation==generation &&
                p.Main.RuntimeBoot==runtimeBoot && p.Sidecar.RuntimeBoot==runtimeBoot && p.Main.BootTransitionID==transition && p.Sidecar.BootTransitionID==transition &&
                p.Main.Policy==p.Sidecar.Policy && p.Main.Policy.InstallationID==policy.InstallationID && p.Main.Policy.Revision>=policy.Revision &&
                (sidecar.LifecycleRunPolicyFollows(policy,p.Main.Policy) || inhibited) &&
                (launcher==nil || p.Main.Launcher!=nil && *p.Main.Launcher==launcher.Identity) &&
                (p.Main.Worker==nil || worker!=nil && *p.Main.Worker==worker.Identity) &&
                boundSide!=nil && p.Sidecar.Binding!=nil && *p.Sidecar.Binding==boundSide.Identity
        }
        rawStop,stopErr:=sidecar.LifecycleStopRequested(cleanup)
        if stopErr!=nil{stickyFailure=true}else if rawStop{intentional.Store(true)}
        p,e:=sidecar.OpenLifecycle(cleanup)
        if e!=nil{stickyFailure=true}else{
            if !observePair(p) || terminalNS<=0{stickyFailure=true}else{
                p.Main.Health=nil;p.Main.Phase="retiring";p.Main.Clean=false
                if worker!=nil{p.Main.Worker=&worker.Identity}
                if p.Main.Block=="none" && failureCause!="none"{p.Main.Block=failureCause}
                p.Main.Teardown=&sidecar.LifecycleTeardown{BeganNS:terminalNS,DeadlineNS:terminalNS+int64(30*time.Second)}
                if p.WriteMain()!=nil{stickyFailure=true}
            }
            p.Close()
        }
        // Withdraw only the held sidecar role. A failed publication does not
        // authorize a numeric-PID fallback or premature child signaling.
        removal,cancelRemoval:=context.WithDeadline(cleanup,removalDeadline)
        if boundSide!=nil && !boundSide.Dead(){
            if sidecar.ValidateLifecyclePin(removal,boundSide,"happyranch-tsnet-sidecar.service",true)!=nil || removal.Err()!=nil || !time.Now().Before(removalDeadline){stickyFailure=true}else{
                sig:=unix.SIGUSR1;if intentional.Load(){sig=unix.SIGTERM}
                if boundSide.Signal(removal,sig)!=nil{stickyFailure=true}
                if removal.Err()!=nil || !time.Now().Before(removalDeadline){stickyFailure=true}
            }
        }
        readPeer:=func(ctx context.Context)(map[string]string,bool){
            if boundSide==nil || ctx.Err()!=nil{return nil,false}
            props,e:=sidecar.LifecycleUnit(ctx,"happyranch-tsnet-sidecar.service")
            if e!=nil || ctx.Err()!=nil || props["InvocationID"]!=boundSide.Identity.Invocation{stickyFailure=true;return nil,false}
            code,e:=strconv.Atoi(props["ExecMainCode"]);status,se:=strconv.Atoi(props["ExecMainStatus"])
            if e!=nil || se!=nil || code<0 || code>3 || status<0 || status>255 || strconv.Itoa(code)!=props["ExecMainCode"] || strconv.Itoa(status)!=props["ExecMainStatus"]{stickyFailure=true;return nil,false}
            if props["ControlGroup"]!="" && props["ControlGroup"]!="/system.slice/happyranch-tsnet-sidecar.service"{stickyFailure=true;return nil,false}
            switch props["Result"]{
            case "success","signal":
            case "exit-code","core-dump":stickyFailure=true
            default:stickyFailure=true;return nil,false
            }
            if code==1 && status!=0{stickyFailure=true} // available78 cannot disappear on reread
            if boundSide.Dead() && code==0{stickyFailure=true;return nil,false}
            return props,true
        }
        peerAbsent:=func(ctx context.Context)bool{
            before,ok:=readPeer(ctx);if !ok{return false}
            if !sidecar.LifecycleAbsent(ctx,"happyranch-tsnet-sidecar.service",boundSide){return false}
            after,ok:=readPeer(ctx)
            return ok && before["InvocationID"]==after["InvocationID"] && after["MainPID"]=="0" && after["ControlPID"]=="0" &&
                after["Job"]=="" && (after["ActiveState"]=="inactive" && after["SubState"]=="dead" || after["ActiveState"]=="failed" && after["SubState"]=="failed") && ctx.Err()==nil
        }
        absent:=false
        for removal.Err()==nil && time.Now().Before(removalDeadline){
            if peerAbsent(removal) && removal.Err()==nil && time.Now().Before(removalDeadline){absent=true;break}
            select{
            case record,ok:=<-childRecords:
                if !ok{childRecords=nil}else if !retirementRecord(record){stickyFailure=true}
            case <-removal.Done():
            case <-time.After(20*time.Millisecond):
            }
        }
        cancelRemoval()
        if absent{emit("withdrawal","admission_absent","pending",nil)}else{stickyFailure=true;emit("withdrawal","admission_unknown","blocked",nil)}

        // If stop won the live select, authenticate the pending first worker
        // before constructing/signaling its cohort. No new terminal or receipt
        // allowance is created, and a first stopping record cannot own a worker.
        if started && worker==nil{
            firstTimer:=time.NewTimer(time.Until(childDeadline))
            for worker==nil && childRecords!=nil && !observeChildWait() && childPhase(){select{
            case record,ok:=<-childRecords:
                if !ok{childRecords=nil}else if !retirementRecord(record){stickyFailure=true}
            case <-waitDone:observeChildWait()
            case <-child.Done():
            case <-firstTimer.C:
            }}
            firstTimer.Stop()
        }
        // Retain the actual accepted owner in the SAME retiring publication,
        // including a first worker authenticated after the original stop latch.
        if worker==nil{stickyFailure=true}else if childPhase(){
            current,e:=sidecar.OpenLifecycle(child)
            if e!=nil{stickyFailure=true}else{
                if !observePair(current) || current.Main.Phase!="retiring" || current.Main.Teardown==nil ||
                    current.Main.Teardown.BeganNS!=terminalNS || current.Main.Teardown.DeadlineNS!=terminalNS+int64(30*time.Second){stickyFailure=true
                }else if current.Main.Worker==nil && childPhase(){
                    current.Main.Worker=&worker.Identity
                    if current.WriteMain()!=nil{stickyFailure=true}
                }
                current.Close()
            }
            childPhase() // publication/close can consume the original phase
        }
        observeChildWait()
        // Retain the bounded authenticated cohort through cleanup. A dead
        // launcher is distinguishable from an inaccessible/mismatched one;
        // unknown descendants are never adopted after ancestry is lost.
        owned:=[]*sidecar.LifecyclePin{}
        if launcher!=nil{owned=append(owned,launcher)}
        if worker!=nil{owned=append(owned,worker)}
        ownedChildren=owned
        censusOK:=false
        if started && launcher!=nil && childPhase(){
            next,e:=sidecar.LifecycleChildCensus(child,self,launcher,owned...)
            if e!=nil{stickyFailure=true}else{owned=next;ownedChildren=owned;censusOK=true}
        }else if started{stickyFailure=true}
        if started && launcher!=nil && launcher.Dead(){stickyFailure=true}
        if started && absent && censusOK && childPhase(){
            // The worker is signaled before the launcher. Its final health
            // record and actual exit must precede any clean certification.
            if worker==nil || worker.Dead() || !childPhase(){stickyFailure=true}else{
                // Keep the early Wait/command election: command-induced exit
                // may precede Signal's return. This latch is NOT cooperation.
                commanded.Store(true)
                if worker.Signal(child,unix.SIGTERM)!=nil{stickyFailure=true}else{
                    // A conservative upper boundary AFTER successful send.
                    // Earlier/equal receipts, including during Validate or
                    // send/return, are ambiguous and must remain sticky78.
                    childTermAt=time.Now();childTermSent=true
                }
                childPhase()
            }
            for _,pin:=range owned{
                if pin.Identity==launcher.Identity || worker!=nil && pin.Identity==worker.Identity || pin.Dead(){continue}
                if !childPhase(){break}
                if pin.Signal(child,unix.SIGTERM)!=nil{stickyFailure=true}
                childPhase()
            }
        }
        // Regardless of admission/state failure, observe the ONE actual Wait
        // and consume scanner delivery until the ORIGINAL remaining bound.
        childTimer:=time.NewTimer(time.Until(childDeadline))
        for started && !observeChildWait() && childPhase(){
            select{
            case record,ok:=<-childRecords:
                if !ok{childRecords=nil}else if !retirementRecord(record){stickyFailure=true}
            case <-waitDone:observeChildWait()
            case <-child.Done():
            case <-childTimer.C:
            case <-time.After(20*time.Millisecond):
                if censusOK{
                    next,e:=sidecar.LifecycleChildCensus(child,self,launcher,owned...)
                    if e!=nil{stickyFailure=true;censusOK=false}else{owned=next;ownedChildren=owned}
                }
                if absent && censusOK && childTermSent && worker!=nil && worker.Dead() && !launcher.Dead() && !launcherTermSent && childPhase(){
                    launcherTermSent=true
                    if launcher.Signal(child,unix.SIGTERM)!=nil{stickyFailure=true}
                    childPhase()
                }
            }
        }
        childTimer.Stop()
        if started && !childWaitTimely{stickyFailure=true} // late/unproved Wait cannot renew T+20 to D
        // Scanner gets one at-most2s join INSIDE D, never a fallback allowance.
        // Keep consuming the final stopping record before cancel/close so a
        // channel-blocked scanner can actually finish and its result is kept.
        scannerJoined:=!scannerStarted
        joinDeadline:=time.Now().Add(2*time.Second);if deadline.Before(joinDeadline){joinDeadline=deadline}
        if scannerStarted{
            joinTimer:=time.NewTimer(time.Until(joinDeadline))
            for !scannerJoined && time.Now().Before(joinDeadline){select{
                case record,ok:=<-childRecords:
                    if !ok{childRecords=nil}else if !retirementRecord(record){stickyFailure=true}
                case <-scannerDone:scannerJoined=true
                case <-joinTimer.C:
            }}
            joinTimer.Stop();closeScanner()
            if !scannerJoined || !time.Now().Before(joinDeadline){stickyFailure=true}
            if !scannerJoined && time.Now().Before(deadline){select{case <-scannerDone:scannerJoined=true;default:}}
            if scannerJoined && scannerErr!=nil{stickyFailure=true}
        }else{closeScanner()}
        if scannerCloseErr!=nil{stickyFailure=true}
        // A still-owned reaper retains its outcome channel; EOF/absence never
        // supplies a Wait. Observe remaining D even on every negative path.
        joined:=!started
        if started && time.Now().Before(deadline){joined=consumeWait()}
        // A missed scanner join stays sticky, but still observe BOTH owned
        // outcomes in remaining D, even when Wait was already available.
        pendingWait,pendingScanner:=waitDone,scannerDone
        if joined{pendingWait=nil};if scannerJoined{pendingScanner=nil}
        for (!joined || !scannerJoined) && cleanup.Err()==nil && time.Now().Before(deadline){select{
            case <-pendingWait:joined=time.Now().Before(deadline);pendingWait=nil
            case <-pendingScanner:
                scannerJoined=time.Now().Before(deadline);pendingScanner=nil
                if scannerJoined && scannerErr!=nil{stickyFailure=true}
            case <-cleanup.Done():
        }}
        if started && (!joined || waitErr!=nil || unexpectedWait.Load() || !workerStopped || worker==nil || !worker.Dead()){stickyFailure=true}
        childrenAbsent:=!started
        if started && censusOK{childrenAbsent=sidecar.LifecycleChildrenAbsent(cleanup,self,owned)}
        if !joined || !scannerJoined || !childrenAbsent || !time.Now().Before(deadline){stickyFailure=true;emit("cleanup","join_failed","blocked",nil)}
        if !started || !absent || !censusOK || !childTermSent || signalFailure.Load(){stickyFailure=true}
        if stickyFailure{emit("cleanup","cleanup_blocked","blocked",nil);return 78}
        // Fresh source/owner/policy/peer/failure checks, under the common lock,
        // surround the only eligible idle/backoff publication. Stop wins retry.
        if sidecar.RunLifecyclePreflight(cleanup)!=nil || !peerAbsent(cleanup) || !sidecar.LifecycleChildrenAbsent(cleanup,self,owned){return 78}
        p,e=sidecar.OpenLifecycle(cleanup);if e!=nil{return 78};defer p.Close()
        boot,bErr:=sidecar.LifecycleBoot();stamp,tErr:=sidecar.LifecycleNow()
        if bErr!=nil || tErr!=nil || boot!=runtimeBoot || !observePair(p) || stickyFailure || signalFailure.Load() || cleanup.Err()!=nil || !time.Now().Before(deadline){return 78}
        if p.RetireClean(stamp,self,generation)!=nil || cleanup.Err()!=nil || !time.Now().Before(deadline){return 78}
        if p.Refresh()!=nil || p.Sidecar.Failure!="none" || p.Main.Block!="none" || p.Main.Policy!=p.Sidecar.Policy || !p.Main.Clean || cleanup.Err()!=nil{return 78}
        // Final output runs after the owned signal observer is joined. It
        // reacquires the current idle publication, so an accepted stop before
        // that boundary wins over the staged retry result.
        end:=stamp;finalPolicy:=p.Main.Policy
        finalOutput=func(code int)int{
            if code==78{return 78}
            finalContext,finishFinal:=context.WithDeadline(context.Background(),deadline);defer finishFinal()
            final,e:=sidecar.OpenLifecycle(finalContext);if e!=nil{return 78};defer final.Close()
            if sidecar.ValidateLifecyclePin(finalContext,selfPin,"happyranch-connector.service",true)!=nil ||
                !peerAbsent(finalContext) || stickyFailure || !sidecar.LifecycleChildrenAbsent(finalContext,self,owned){return 78}
            if final.Main.RuntimeBoot!=runtimeBoot || final.Sidecar.RuntimeBoot!=runtimeBoot || !final.Main.Clean ||
                final.Main.Generation!=nil || final.Main.Rate.LastEndNS==nil || *final.Main.Rate.LastEndNS!=end ||
                final.Main.Policy!=final.Sidecar.Policy || final.Main.Policy.InstallationID!=policy.InstallationID || final.Main.Policy.Revision<finalPolicy.Revision ||
                !(sidecar.LifecycleRunPolicyFollows(finalPolicy,final.Main.Policy) || stoppedPolicyFollows(finalPolicy,final.Main.Policy)) ||
                final.Sidecar.Binding==nil || *final.Sidecar.Binding!=boundSide.Identity ||
                final.Main.Policy.HardBlock!="none" || final.Main.Block!="none" || final.Sidecar.Failure!="none" ||
                finalContext.Err()!=nil || !time.Now().Before(deadline){return 78}
            rawStop,stopErr:=sidecar.LifecycleStopRequested(finalContext);if stopErr!=nil{return 78};if rawStop{intentional.Store(true)}
            rateCount=len(final.Main.Rate.Spends)
            code=1
            if intentional.Load() || final.Main.Policy.Desired!="run" || final.Sidecar.Halt{
                intentional.Store(true);code=0;stopCertified=true
                // Run events describe the immutable admission policy. Only a
                // proven final stopped inhibition projects its newer revision.
                if final.Main.Policy.Desired!="run"{revision=final.Main.Policy.Revision}
            }
            if !time.Now().Before(deadline){return 78};return code
        }
        retirementResult=1;return 1
    }
    retireRecord:=func(err error)int{
        cause:=sidecar.LifecycleFailureCause(err)
        if failureCause=="none" && cause!="none"{failureCause=cause}
        stickyFailure=true
        reason:="cleanup_blocked";switch cause{case "io_failure":reason="state_io_failed";case "invalid_state":reason="state_invalid";case "identity_loss":reason="identity_lost"}
        return retire(reason)
    }
    defer func(){
        if claimAttempted && !retired{result=retire("state_invalid")}
        settleSignals()
        if signalFailure.Load(){result=78}
        if finalOutput!=nil{result=finalOutput(result)}
        if writer!=nil{_ = writer.Close()};closeScanner()
        for _,pin:=range ownedChildren{if pin!=launcher && pin!=worker{pin.Close()}}
        if launcher!=nil{launcher.Close()};if worker!=nil{worker.Close()};if boundSide!=nil{boundSide.Close()}
    }()
    claim,cancelClaim:=context.WithTimeout(startup,10*time.Second);defer cancelClaim()
    for{
        p,e:=sidecar.OpenLifecycle(claim);if e!=nil{return refuse(e)}
        if p.Sidecar.Outcome=="waiting" && p.Sidecar.Binding!=nil{
            boundSide,err=sidecar.PinLifecycleProcess(*p.Sidecar.Binding)
            if err!=nil{p.Close();return refuse(err)}
            if e:=sidecar.ValidateLifecyclePin(claim,boundSide,"happyranch-tsnet-sidecar.service",true);e!=nil{p.Close();return refuse(e)}
            now,e:=sidecar.LifecycleNow();if e!=nil{p.Close();return 78}
            if !sidecar.LifecycleEmptyOwner(claim,p,self) || stopped(){p.Close();return refuse(sidecar.ErrLifecycle)}
            policy=p.Main.Policy;runtimeBoot=p.Main.RuntimeBoot;transition=p.Main.BootTransitionID
            revision=policy.Revision;recovered=p.Main.Rate.LastEndNS!=nil
            claimAttempted=true
            err=p.Spend(now,self,generation);rateCount=len(p.Main.Rate.Spends);p.Close()
            if errors.Is(err,sidecar.ErrRateDeferred){
                // Startup/claim has begun; a lost claim race is not a prior
                // no-child deferred-reader success and gets no new2s window.
                claimAttempted=false;return 78
            }
            if err!=nil{return retireRecord(err)};diagnosticGeneration=&generation
            emit("context","publication_observed","pending",nil);break
        }
        p.Close();select{case <-terminalDone:return 78;case <-claim.Done():return 78;case <-time.After(20*time.Millisecond):}
    }
    if stopped(){return retire("intentional_stop")}
    reader,writer,err=os.Pipe();if err!=nil{stickyFailure=true;return retire("child_loss")}
    cmd=exec.Command(argv[0],argv[1:]...);cmd.Stdout,cmd.Stderr=os.Stdout,os.Stderr;cmd.ExtraFiles=[]*os.File{writer}
    env:=[]string{}
    for _,item:=range os.Environ(){if strings.HasPrefix(item,"NOTIFY_SOCKET=") || strings.HasPrefix(item,"HAPPYRANCH_CHILD_HEALTH_"){continue};env=append(env,item)}
    cmd.Env=append(env,"HAPPYRANCH_CHILD_HEALTH_FD=3","HAPPYRANCH_CHILD_HEALTH_GENERATION="+generation,"HAPPYRANCH_CHILD_HEALTH_VERSION=2")
    if stopped() || startup.Err()!=nil{return retire("startup_expired")}
    if cmd.Start()!=nil{return retire("child_loss")}
    started=true
    go func(){waitErr=cmd.Wait();if !commanded.Load(){unexpectedWait.Store(true)};latch("child_loss");close(waitDone)}()
    scannerStarted=true
    go func(){
        failed:=make(chan error,1)
        scanChildHealth(reader,records,failed,scannerStop)
        select{case scannerErr=<-failed:default:}
        select{case <-scannerStop:if errors.Is(scannerErr,os.ErrClosed){scannerErr=nil};default:}
        if !retiring.Load(){latch("health_invalid")}
        close(scannerDone)
    }()
    if writer.Close()!=nil{writer=nil;stickyFailure=true;return retire("child_loss")};writer=nil
    launcher,err=sidecar.IdentifyLifecycleProcess(cmd.Process.Pid,self.Invocation)
    if err!=nil{return retire("identity_lost")}
    if stopped(){return retire("child_loss")}
    launchPair,err:=sidecar.OpenLifecycle(startup);if err!=nil{return retireRecord(err)}
    if launchPair.Main.Wrapper==nil || *launchPair.Main.Wrapper!=self || launchPair.Main.Generation==nil || *launchPair.Main.Generation!=generation{launchPair.Close();return retire("state_invalid")}
    launchPair.Main.Launcher=&launcher.Identity
    err=launchPair.WriteMain();launchPair.Close();if err!=nil{return retireRecord(err)}
    var qualification sidecar.LifecycleQualification
    ready,childReady:=false,false
    resetPublished:=false
    var readyNS int64
    var freshness time.Time
    ticker:=time.NewTicker(time.Second);defer ticker.Stop()
    startupDone:=startup.Done()
    for{
        if stopped(){qualification.Observe(0,0,0,false);return retire("child_loss")}
        if !freshness.IsZero() && !time.Now().Before(freshness){qualification.Observe(0,0,0,false);return retire("freshness_expired")}
        select{
        case <-terminalDone:qualification.Observe(0,0,0,false);return retire("child_loss")
        case <-startupDone:
            if !ready{return retire("startup_expired")};startupDone=nil
        case <-ticker.C:
            if boundSide.Dead(){qualification.Observe(0,0,0,false);return retire("sidecar_loss")}
            if launcher.Dead() || worker!=nil && worker.Dead(){qualification.Observe(0,0,0,false);return retire("child_loss")}
            if !freshness.IsZero() && !time.Now().Before(freshness){qualification.Observe(0,0,0,false);return retire("freshness_expired")}
            if !ready && !time.Now().Before(startupDeadline){return retire("startup_expired")}
            if !earlier.IsZero() && !time.Now().Before(earlier){return retire("startup_expired")}
            if ready{
                // A current loss between distinct records clears local
                // accumulation immediately on observation. Ticks never add a
                // sequence, refresh a lease, publish reset or emit watchdog.
                bound:=clip(time.Now().Add(2*time.Second));if freshness.Before(bound){bound=freshness}
                check,finish:=context.WithDeadline(context.Background(),bound)
                current,e:=sidecar.OpenLifecycle(check)
                if e!=nil{finish();qualification.Observe(0,0,0,false);return retireRecord(e)}
                composite:=sidecar.LifecycleReady(check,current,false)
                if composite && !sidecar.LifecycleReady(check,current,true){qualification.Observe(0,0,0,false)}
                current.Close();late:=check.Err()!=nil || !time.Now().Before(bound);finish()
                if stopped() || late || !composite{qualification.Observe(0,0,0,false);return retire("sidecar_loss")}
            }
        case record,ok:=<-records:
            if !ok{qualification.Observe(0,0,0,false);return retire("health_invalid")}
            // Duplicates/old records of this pinned worker are inert. They
            // neither reset receipt time nor advance sequence/qualification.
            // Invalid version/generation/identity/state/range still refuses.
            if duplicateRecord(record){
                continue
            }
            // Admit every nonduplicate through the managed contract before
            // dispatch. Invalid terminals must survive cleanup as sticky78.
            acceptedAt,stamp:=record.receivedAt,record.receivedNS
            if acceptedAt.IsZero() || stamp<=0 || stamp>int64(1<<63-1)-sidecar.ManagedLeaseNS{qualification.Observe(0,0,0,false);return retire("health_invalid")}
            // No renewed startup context after composite READY. Live work is
            // clipped by the OLD lease and THIS receipt's12s, plus any real
            // earlier caller bound. Autonomous PID1 entry retains its own bound.
            observationDeadline:=clip(acceptedAt.Add(12*time.Second))
            if !ready && startupDeadline.Before(observationDeadline){observationDeadline=startupDeadline}
            if !freshness.IsZero() && freshness.Before(observationDeadline){observationDeadline=freshness}
            if !time.Now().Before(observationDeadline){qualification.Observe(0,0,0,false);return retire("health_invalid")}
            observation,cancelObservation:=context.WithDeadline(context.Background(),observationDeadline)
            if !acceptRecord(record,observation,false){cancelObservation();qualification.Observe(0,0,0,false);return retire("health_invalid")}
            if record.State=="waiting"{cancelObservation();if ready || childReady{return retire("health_invalid")};continue}
            if record.State=="ready" && childReady{cancelObservation();qualification.Observe(0,0,0,false);return retire("health_invalid")}
            if record.State=="ready"{childReady=true};if !childReady{cancelObservation();return retire("health_invalid")}
            p,e:=sidecar.OpenLifecycle(observation)
            if e!=nil{cancelObservation();qualification.Observe(0,0,0,false);return retireRecord(e)}
            boot,e:=sidecar.LifecycleBoot();currentNS,clockErr:=sidecar.LifecycleNow()
            if e!=nil || clockErr!=nil{p.Close();cancelObservation();qualification.Observe(0,0,0,false);return retireRecord(sidecar.ErrLifecycle)}
            eligibleErr:=p.Eligible(boot)
            if eligibleErr!=nil{
                // A's coherent inhibition may win before the manager signal.
                // Bind the actual current owner before classifying this as
                // intent. Unknown, typed failure, mismatched or late input still
                // takes the sticky error path. Neither branch proves cleanup.
                inhibited:=sidecar.LifecycleFailureCause(eligibleErr)=="none" && currentNS>=stamp && currentNS-stamp<sidecar.ManagedLeaseNS &&
                    ownedInhibition(p,boot,currentNS) && observation.Err()==nil && time.Now().Before(observationDeadline)
                p.Close();cancelObservation();qualification.Observe(0,0,0,false)
                if inhibited{intentional.Store(true);return retire("intentional_stop")}
                return retireRecord(eligibleErr)
            }
            if currentNS<stamp || currentNS-stamp>=sidecar.ManagedLeaseNS || !sidecar.LifecycleRunPolicyFollows(policy,p.Main.Policy) ||
                p.Main.RuntimeBoot!=runtimeBoot || p.Main.BootTransitionID!=transition || p.Main.Phase!="live" ||
                p.Main.Wrapper==nil || *p.Main.Wrapper!=self || p.Main.Generation==nil || *p.Main.Generation!=generation ||
                p.Sidecar.Binding==nil || *p.Sidecar.Binding!=boundSide.Identity || p.Main.Health!=nil && sequence<=p.Main.Health.Sequence{
                p.Close();cancelObservation();qualification.Observe(0,0,0,false);return retire("state_invalid")
            }
            identity:=worker.Identity;p.Main.Worker=&identity
            p.Main.Health=&sidecar.LifecycleHealth{Sequence:sequence,ValidUntilNS:stamp+sidecar.ManagedLeaseNS};p.Main.Sidecar=p.Sidecar.Binding
            if stopped() || observation.Err()!=nil || !time.Now().Before(observationDeadline){p.Close();cancelObservation();qualification.Observe(0,0,0,false);return retire("freshness_expired")}
            if e:=p.WriteMain();e!=nil{p.Close();cancelObservation();qualification.Observe(0,0,0,false);return retireRecord(e)}
            composite:=sidecar.LifecycleReady(observation,p,false)
            revision=policy.Revision;rateCount=len(p.Main.Rate.Spends);p.Close()
            if stopped(){cancelObservation();qualification.Observe(0,0,0,false);return retire("child_loss")}
            if observation.Err()!=nil || !time.Now().Before(observationDeadline){cancelObservation();qualification.Observe(0,0,0,false);return retire("freshness_expired")}
            // Both local wall-monotonic and persisted kernel-monotonic leases
            // stay anchored to pipe receipt, never to slow query completion.
            freshness=clip(acceptedAt.Add(12*time.Second))
            if !composite{qualification.Observe(stamp,readyNS,sequence,false);cancelObservation();if ready{return retire("sidecar_loss")};continue}
            message:="WATCHDOG=1";if !ready{message="READY=1"}
            if notifier.Notify(message,"STATUS=composite healthy")!=nil{cancelObservation();qualification.Observe(0,0,0,false);return retire("watchdog_failed")}
            qualifiedNS,qualifiedClockErr:=sidecar.LifecycleNow()
            if stopped() || observation.Err()!=nil || !time.Now().Before(observationDeadline) || qualifiedClockErr!=nil || qualifiedNS<stamp || qualifiedNS-stamp>=sidecar.ManagedLeaseNS{cancelObservation();qualification.Observe(0,0,0,false);return retire("freshness_expired")}
            if !ready && recovered{emit("readmission","fresh_listener_recovered","fresh_listener_recovered",nil)}
            // Anchor READY once at its actual acknowledgment. Later credited
            // observations use original receipt stamps, never proof completion.
            if !ready{readyNS=qualifiedNS}
            ready=true;readyObserved.Store(true)
            p,e=sidecar.OpenLifecycle(observation)
            if e!=nil{cancelObservation();qualification.Observe(0,0,0,false);return retireRecord(e)}
            qualified:=sidecar.LifecycleReady(observation,p,true)
            p.Close()
            if stopped() || observation.Err()!=nil || !time.Now().Before(observationDeadline){cancelObservation();qualification.Observe(0,0,0,false);return retire("freshness_expired")}
            // Startup/notify roles can establish READY, but cannot accumulate
            // running qualification until the strict active/job proof succeeds.
            if qualification.Observe(stamp,readyNS,sequence,qualified) && !resetPublished{
                p,e=sidecar.OpenLifecycle(observation)
                if e!=nil{cancelObservation();return retireRecord(e)}
                if stopped() || !sidecar.LifecycleRunPolicyFollows(policy,p.Main.Policy) || p.Main.RuntimeBoot!=runtimeBoot || p.Main.BootTransitionID!=transition ||
                    p.Main.Wrapper==nil || *p.Main.Wrapper!=self || p.Main.Generation==nil || *p.Main.Generation!=generation ||
                    p.Main.Health==nil || p.Main.Health.Sequence!=sequence || p.Main.Health.ValidUntilNS!=stamp+sidecar.ManagedLeaseNS{
                    p.Close();cancelObservation();qualification.Observe(0,0,0,false);return retire("state_invalid")
                }
                // Repeat the selected existing-observation predicate under the
                // SAME lock. A known current negative vetoes reset; an actual
                // sidecar loss not yet communicated can still clear all debt.
                if !sidecar.LifecycleReady(observation,p,true) || stopped(){p.Close();cancelObservation();qualification.Observe(0,0,0,false);return retire("sidecar_loss")}
                changed:=len(p.Main.Rate.Spends)!=0 || p.Main.Rate.LastEndNS!=nil || p.Main.Rate.NotBeforeNS!=nil
                if changed{
                    p.Main.Rate.Spends=[]int64{};p.Main.Rate.LastEndNS=nil;p.Main.Rate.NotBeforeNS=nil
                    // Final positive boundary after every slow proof, before
                    // any empty bytes become visible. No latch/time reservation.
                    if stopped() || observation.Err()!=nil || !time.Now().Before(observationDeadline){p.Close();cancelObservation();qualification.Observe(0,0,0,false);return retire("freshness_expired")}
                    if e:=p.WriteMain();e!=nil{p.Close();cancelObservation();return retireRecord(e)}
                }
                // A late/failed/changed final observation never claims the
                // reset acknowledgment, even if new bytes became visible.
                if stopped() || observation.Err()!=nil || !time.Now().Before(observationDeadline){p.Close();cancelObservation();return retire("freshness_expired")}
                if e=p.Refresh();e!=nil{p.Close();cancelObservation();return retireRecord(e)}
                if !sidecar.LifecycleRunPolicyFollows(policy,p.Main.Policy) || p.Main.RuntimeBoot!=runtimeBoot || p.Main.BootTransitionID!=transition ||
                    p.Main.Generation==nil || *p.Main.Generation!=generation || p.Main.Wrapper==nil || *p.Main.Wrapper!=self ||
                    p.Main.Health==nil || p.Main.Health.Sequence!=sequence || p.Main.Health.ValidUntilNS!=stamp+sidecar.ManagedLeaseNS ||
                    p.Sidecar.Binding==nil || *p.Sidecar.Binding!=boundSide.Identity || len(p.Main.Rate.Spends)!=0 ||
                    p.Main.Rate.LastEndNS!=nil || p.Main.Rate.NotBeforeNS!=nil || !sidecar.LifecycleReady(observation,p,true) || stopped(){
                    p.Close();cancelObservation();qualification.Observe(0,0,0,false);return retire("sidecar_loss")
                }
                if stopped() || observation.Err()!=nil || !time.Now().Before(observationDeadline){p.Close();cancelObservation();qualification.Observe(0,0,0,false);return retire("freshness_expired")}
                resetPublished=true;p.Close();rateCount=0
                if changed{emit("readmission","healthy_reset","pending",nil)}
            }
            cancelObservation()
        }
    }
}
