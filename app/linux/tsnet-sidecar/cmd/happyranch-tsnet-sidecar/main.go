package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"strconv"
	"sync"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/mdlayher/sdnotify"
	sidecar "happyranch/linux-tsnet-sidecar"
)

func main() { os.Exit(runSidecarMain()) }

func runSidecarMain() (exit int) {
	entry := time.Now()
	if len(os.Args) == 2 && os.Args[1] == "managed-package" {
		return sidecar.HandleLifecyclePackage(os.Stdin, os.Stdout)
	}
	if len(os.Args) == 2 && os.Args[1] == "managed-run-check" {
		return sidecar.HandleLifecycleGuard(os.Stdin, "run")
	}
	if len(os.Args) == 2 && os.Args[1] == "managed-stopped-check" {
		return sidecar.HandleLifecycleGuard(os.Stdin, "stopped")
	}
	if len(os.Args) == 2 && os.Args[1] == "managed-package-check" {
		return sidecar.HandleLifecycleGuard(os.Stdin, "package")
	}
	if len(os.Args) == 2 && os.Args[1] == "managed-worker-check" {
		return sidecar.CheckManagedWorker()
	}
	if len(os.Args) == 2 && os.Args[1] == "managed-lifecycle" {
		return sidecar.HandleLifecycleRequest(os.Stdin, os.Stdout)
	}
	if len(os.Args) > 1 && os.Args[1] == "supervise-connector" {
		return runConnectorSupervisor(os.Args[2:])
	}
	var terminalOnce sync.Once
	terminalDone := make(chan struct{})
	var terminalAt time.Time
	markTerminal := func() { terminalOnce.Do(func() { terminalAt = time.Now(); close(terminalDone) }) }
	ctx, cancelLifetime := context.WithCancel(context.Background())
	cancel := func() { markTerminal(); cancelLifetime() }
	defer cancelLifetime()
	var haltRequested, failureRequested atomic.Bool
	managedRun := false
	var earlier time.Time
	signals := make(chan os.Signal, 8)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGINT)
	signalStop, signalDone := make(chan struct{}), make(chan struct{})
	acceptSignal := func(sig os.Signal) {
		if sig == syscall.SIGTERM || sig == syscall.SIGINT {
			haltRequested.Store(true)
		}
		if sig == syscall.SIGUSR2 {
			failureRequested.Store(true)
		}
		cancel()
	}
	go func() {
		defer close(signalDone)
		for {
			select {
			case sig := <-signals:
				acceptSignal(sig)
			case <-signalStop:
				return
			}
		}
	}()
	var settleSignalsOnce sync.Once
	settleSignals := func() {
		settleSignalsOnce.Do(func() {
			signal.Stop(signals)
			close(signalStop)
			<-signalDone
			for {
				select {
				case sig := <-signals:
					acceptSignal(sig)
				default:
					return
				}
			}
		})
	}
	defer func() {
		settleSignals()
		if managedRun {
			if failureRequested.Load() || !earlier.IsZero() && !time.Now().Before(earlier) {
				exit = 78
			}
			select {
			case <-terminalDone:
				if !time.Now().Before(terminalAt.Add(30 * time.Second)) {
					exit = 78
				}
			default:
			}
		}
	}()
	configPath := flag.String("config", "", "absolute path to the manual-N5 sidecar JSON configuration")
	flag.Parse()
	if *configPath == "" {
		fmt.Fprintln(os.Stderr, "configuration_invalid")
		return 2
	}
	raw, err := os.ReadFile(*configPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "configuration_invalid")
		return 2
	}
	var cfg sidecar.Config
	if json.Unmarshal(raw, &cfg) != nil {
		fmt.Fprintln(os.Stderr, "configuration_invalid")
		return 2
	}
	credentialsDir := os.Getenv("CREDENTIALS_DIRECTORY")
	if filepath.IsAbs(credentialsDir) {
		cfg.CredentialFile = filepath.Join(filepath.Clean(credentialsDir), "enrollment.key")
	} else {
		// After successful one-use enrollment the privileged post-start helper
		// removes both the source and transient LoadCredential drop-in.  Restarts
		// therefore prove the durable marker against the confirmed-absent source.
		cfg.CredentialFile = "/etc/happyranch/enrollment.key"
	}
	if cfg.Validate() != nil {
		fmt.Fprintln(os.Stderr, "configuration_invalid")
		return 2
	}
	engine := sidecar.NewTSNetEngine()
	svc := sidecar.New(cfg, engine, &netDialer{})
	var guard *sidecar.ManagedSidecarGuard
	var watchdogDone chan struct{}
	var watchdogErr chan error
	stoppedComplete := false
	managedResult := 78
	// One object, one first terminal time, and one actual teardown owner.
	// Publication errors remain sticky but cannot skip physical Stop/joins.
	finishManaged := func(failed bool) int {
		if stoppedComplete {
			return managedResult
		}
		stoppedComplete = true
		cancel()
		<-terminalDone
		deadline := terminalAt.Add(30 * time.Second)
		if owned := guard.UnstartedDeadline(); !owned.IsZero() && owned.Before(deadline) {
			deadline = owned
		}
		if !earlier.IsZero() && earlier.Before(deadline) {
			deadline = earlier
		}
		cleanup, finish := context.WithDeadline(context.Background(), deadline)
		defer finish()
		failed = failed || failureRequested.Load()
		if guard.Terminal(cleanup, haltRequested.Load(), false, failed) != nil {
			failed = true
		}
		stopErr := stopTwice(svc, os.Stderr, os.Getpid(), deadline)
		if stopErr != nil {
			failed = true
		}
		watchdogJoined := watchdogDone == nil
		if watchdogDone != nil {
			select {
			case <-watchdogDone:
				watchdogJoined = true
			case <-cleanup.Done():
			}
		}
		closed := stopErr == nil && watchdogJoined && cleanup.Err() == nil && time.Now().Before(deadline)
		if !closed {
			failed = true
		}
		if watchdogErr != nil {
			select {
			case <-watchdogErr:
				failed = true
			default:
			}
		}
		settleSignals()
		if guard.Terminal(cleanup, haltRequested.Load(), closed, failed || failureRequested.Load()) != nil {
			failed = true
		}
		if !failed {
			// A nil writer result acknowledges I/O only. The guard may have
			// retained its own failure latch; never turn that into exit0.
			self, e := sidecar.CurrentLifecycleProcess()
			final, readErr := sidecar.OpenLifecycle(cleanup)
			if e != nil || readErr != nil {
				failed = true
			}
			if final != nil {
				if final.Sidecar.Binding == nil || *final.Sidecar.Binding != self || final.Sidecar.TerminalNS == nil ||
					final.Sidecar.Outcome != "closed" || final.Sidecar.CloseProof != "cooperative" || final.Sidecar.Failure != "none" ||
					haltRequested.Load() && !final.Sidecar.Halt {
					failed = true
				}
				final.Close()
			}
		}
		if failed || failureRequested.Load() || cleanup.Err() != nil || !time.Now().Before(deadline) {
			return 78
		}
		managedResult = 0
		return 0
	}
	defer func() {
		if guard != nil && !stoppedComplete {
			exit = finishManaged(true)
		}
		if guard != nil && !earlier.IsZero() && !time.Now().Before(earlier) {
			exit = 78
		}
		select {
		case <-terminalDone:
			if guard != nil && !time.Now().Before(terminalAt.Add(30*time.Second)) {
				exit = 78
			}
		default:
		}
	}()
	if cfg.StateDir == sidecar.ManagedSidecarDirectory {
		managedRun = true
		signal.Notify(signals, syscall.SIGUSR1, syscall.SIGUSR2)
		startupDeadline := entry.Add(75 * time.Second)
		if raw := os.Getenv("HAPPYRANCH_LIFECYCLE_DEADLINE_NS"); raw != "" {
			sampled := time.Now()
			now, e := sidecar.LifecycleNow()
			n, parseErr := strconv.ParseInt(raw, 10, 64)
			if e != nil || parseErr != nil || strconv.FormatInt(n, 10) != raw || n <= now {
				return 78
			}
			earlier = sampled.Add(time.Duration(n - now))
			if earlier.Before(startupDeadline) {
				startupDeadline = earlier
			}
			var cancelBound context.CancelFunc
			ctx, cancelBound = context.WithDeadline(ctx, earlier)
			defer cancelBound()
		}
		startup, cancelStartup := context.WithDeadline(ctx, startupDeadline)
		defer cancelStartup()
		var err error
		guard, err = sidecar.NewManagedSidecarGuard(startup, ctx, cancel)
		if err != nil {
			if guard != nil && ctx.Err() != nil && !guard.FailureObserved() {
				return finishManaged(false)
			}
			fmt.Fprintln(os.Stderr, "managed_lifecycle_refused")
			return 78
		}
		svc.SetManagedGuard(guard)
	}
	if err := svc.Start(ctx); err != nil {
		if guard != nil {
			markTerminal()
			if ctx.Err() != nil && !guard.FailureObserved() {
				return finishManaged(false)
			}
		}
		fmt.Fprintln(os.Stderr, diagnosticReceipt(err))
		return 1
	}
	if guard != nil {
		check, cancelCheck := context.WithTimeout(ctx, time.Second)
		err := guard.Check(check)
		cancelCheck()
		if err != nil {
			markTerminal()
			if ctx.Err() != nil && !guard.FailureObserved() {
				return finishManaged(false)
			}
			return 78
		}
	}
	notifier, err := sdnotify.New()
	publishReady := func() error {
		if err != nil {
			return err
		}
		if guard == nil {
			return publishSidecarReady(ctx, notifier)
		}
		check, finish := context.WithTimeout(ctx, time.Second)
		defer finish()
		return guard.Forward(check, func() error { return publishSidecarReady(check, notifier) })
	}
	if publishReady() != nil {
		if guard == nil {
			_ = svc.Stop()
		} else {
			markTerminal()
		}
		fmt.Fprintln(os.Stderr, "readiness_unavailable")
		return 1
	}
	watchdogErr = make(chan error, 1)
	watchdogDone = make(chan struct{})
	go func() {
		defer close(watchdogDone)
		watchdogLoop(ctx, cancel, notifier, 10*time.Second, watchdogErr, guard)
	}()
	<-ctx.Done()
	if guard != nil {
		failed := failureRequested.Load()
		select {
		case <-watchdogErr:
			failed = true
		default:
		}
		return finishManaged(failed)
	}
	// Nonmanaged behavior keeps its existing Stop and watchdog policy.
	<-watchdogDone
	if err := stopTwice(svc, os.Stderr, os.Getpid()); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	// Category-only lifecycle evidence for the packaged systemd integration.
	// Stop returns only after listener removal, active-flow drain, and the
	// idempotent engine close have completed.
	select {
	case <-watchdogErr:
		fmt.Fprintln(os.Stderr, "watchdog_unavailable")
		return 1
	default:
	}
	if failureRequested.Load() {
		return 78
	}
	return 0
}

func diagnosticReceipt(err error) string {
	category, phase := "unknown", "unknown"
	switch {
	case errors.Is(err, sidecar.ErrCredentialInput):
		category, phase = "credential_input", "input_acquisition"
	case errors.Is(err, sidecar.ErrEngineStart):
		category, phase = "engine_start", "engine_initialization"
	case errors.Is(err, sidecar.ErrNetworkJoin):
		category, phase = "network_join", "peer_establishment"
	case errors.Is(err, sidecar.ErrDurableCommit):
		category, phase = "durable_commit", "receipt_commit"
	case errors.Is(err, sidecar.ErrConnector):
		category, phase = "connector", "connector_probe"
	case errors.Is(err, sidecar.ErrListener):
		category, phase = "listener", "listener_admission"
	case errors.Is(err, sidecar.ErrConfiguration):
		category, phase = "configuration", "configuration_validation"
	}
	receipt := map[string]any{"category": category, "phase": phase, "actor": "tsnet-sidecar", "unit": "happyranch-tsnet-sidecar.service", "outcome": "failed", "terminal": true, "assertion": map[string]string{"status": "completed"}}
	if category == "network_join" {
		receipt["sub_reason"] = sidecar.NetworkJoinSubReason(err)
	}
	raw, _ := json.Marshal(receipt)
	return "diagnostic_receipt=" + string(raw)
}

type stoppable interface {
	Stop() error
}

// stopTwice is the production re-entrant shutdown seam. Both calls target the
// same Sidecar; the process-scoped receipts distinguish the invocations.
func stopTwice(svc stoppable, output *os.File, runID int, deadlines ...time.Time) error {
	for invocation := 1; invocation <= 2; invocation++ {
		// Stop owns physical work even after expiry; lateness cannot manufacture
		// a join receipt or give Stop2 a new cleanup allowance.
		if err := svc.Stop(); err != nil {
			return err
		}
		if len(deadlines) > 0 && !time.Now().Before(deadlines[0]) {
			return sidecar.ErrLifecycle
		}
		_, err := fmt.Fprintf(output, "lifecycle_stop_complete run=%d invocation=%d\n", runID, invocation)
		if len(deadlines) > 0 && (err != nil || !time.Now().Before(deadlines[0])) {
			return sidecar.ErrLifecycle
		}
	}
	return nil
}

type systemdNotifier interface {
	Notify(...string) error
}

var _ systemdNotifier = (*sdnotify.Notifier)(nil)

// Start has admitted the listener; recheck the caller at the publication
// boundary so cancellation delivered during notifier setup cannot emit READY.
func publishSidecarReady(ctx context.Context, notifier systemdNotifier) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if deadline, ok := ctx.Deadline(); ok && !time.Now().Before(deadline) {
		return context.DeadlineExceeded
	}
	return notifier.Notify("READY=1", "STATUS=sidecar listener ready")
}

func watchdogLoop(ctx context.Context, cancel context.CancelFunc, notifier systemdNotifier, interval time.Duration, failed chan<- error, guards ...*sidecar.ManagedSidecarGuard) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			notify := func() error { return notifier.Notify("WATCHDOG=1") }
			var err error
			if len(guards) > 0 && guards[0] != nil {
				check, finish := context.WithTimeout(ctx, time.Second)
				err = guards[0].Forward(check, notify)
				finish()
			} else {
				err = notify()
			}
			if err != nil {
				if len(guards) > 0 && guards[0] != nil && ctx.Err() != nil && !guards[0].FailureObserved() {
					return
				}
				failed <- err
				cancel()
				return
			}
		}
	}
}

type netDialer struct{}

func (*netDialer) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	return (&net.Dialer{}).DialContext(ctx, network, address)
}
