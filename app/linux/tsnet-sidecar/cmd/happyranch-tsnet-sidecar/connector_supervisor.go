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

func runConnectorSupervisor(argv []string) int {
	if len(argv) == 0 {
		fmt.Fprintln(os.Stderr, "connector_supervisor_invalid")
		return 2
	}
	notifier, err := sdnotify.New()
	if err != nil {
		fmt.Fprintln(os.Stderr, "readiness_unavailable")
		return 1
	}
	return superviseConnector(context.Background(), argv, notifier, 80*time.Second, 12*time.Second, nil, systemdSidecarHealthy, systemdStopSidecar)
}

type sidecarHealthProbe func(context.Context) bool
type sidecarStop func(context.Context) bool

func systemdSidecarHealthy(parent context.Context) bool {
	ctx, cancel := context.WithTimeout(parent, time.Second)
	defer cancel()
	result, err := exec.CommandContext(ctx, "systemctl", "show", "happyranch-tsnet-sidecar.service", "--property=ActiveState", "--value").Output()
	return err == nil && string(result) == "active\n"
}

func systemdStopSidecar(parent context.Context) bool {
	ctx, cancel := context.WithTimeout(parent, time.Second)
	defer cancel()
	result, err := exec.CommandContext(ctx, "systemctl", "show", "happyranch-tsnet-sidecar.service", "--property=MainPID", "--value").Output()
	if err != nil {
		return false
	}
	pidText := strings.TrimSpace(string(result))
	pid, err := strconv.Atoi(pidText)
	if err != nil || pid <= 1 || strconv.Itoa(pid) != pidText {
		return false
	}
	return syscall.Kill(pid, syscall.SIGTERM) == nil
}

func removeSidecarAdmission(ctx context.Context, healthy sidecarHealthProbe, stop sidecarStop) bool {
	if !healthy(ctx) {
		return true
	}
	if !stop(ctx) {
		return false
	}
	deadline := time.NewTimer(5 * time.Second)
	defer deadline.Stop()
	ticker := time.NewTicker(20 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return false
		case <-deadline.C:
			return false
		case <-ticker.C:
			if !healthy(ctx) {
				return true
			}
		}
	}
}

func superviseConnector(parent context.Context, argv []string, notifier notifySender, startupDeadline, staleAfter time.Duration, started chan<- *exec.Cmd, sidecarHealthy sidecarHealthProbe, stopSidecar sidecarStop) int {
	generationBytes := make([]byte, 16)
	if _, err := rand.Read(generationBytes); err != nil {
		return 1
	}
	generation := hex.EncodeToString(generationBytes)
	reader, writer, err := os.Pipe()
	if err != nil {
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
	go scanChildHealth(reader, records, protocolErr)
	waited := make(chan error, 1)
	go func() { waited <- cmd.Wait() }()
	// The initial deadline is intentionally absolute.  Waiting or partial
	// progress must never buy another startup window.
	timer := time.NewTimer(startupDeadline)
	defer timer.Stop()
	var sequence uint64
	childReady := false
	ready := false
	stopping := false
	stopChild := func() {
		if !stopping {
			stopping = true
			_ = notifier.Notify("STOPPING=1", "STATUS=connector stopping")
			// The sidecar owns external admission.  Signal its MainPID and wait
			// for systemd to observe it inactive before beginning connector
			// child cleanup.  Both services retain their own MainPID ownership.
			_ = removeSidecarAdmission(context.Background(), sidecarHealthy, stopSidecar)
			_ = cmd.Process.Signal(syscall.SIGTERM)
		}
	}
	for {
		select {
		case <-ctx.Done():
			stopChild()
		case <-timer.C:
			stopChild()
		case err := <-protocolErr:
			if err != nil {
				stopChild()
			}
		case record, ok := <-records:
			if !ok {
				records = nil
				continue
			}
			if record.Version != 1 || record.Generation != generation || record.Sequence != sequence+1 {
				stopChild()
				continue
			}
			sequence = record.Sequence
			switch record.State {
			case "waiting":
				if ready {
					stopChild()
				}
			case "ready":
				if childReady || ready || stopping {
					stopChild()
				} else {
					childReady = true
					if sidecarHealthy(ctx) {
						if notifier.Notify("READY=1", "STATUS=composite healthy") != nil {
							stopChild()
							continue
						}
						ready = true
						if !timer.Stop() {
							select {
							case <-timer.C:
							default:
							}
						}
						timer.Reset(staleAfter)
					}
				}
			case "healthy":
				if !childReady || stopping {
					stopChild()
				} else if !ready && !sidecarHealthy(ctx) {
					// Connector-first startup: retain the original absolute
					// deadline while the independently starting sidecar finishes.
					continue
				} else if !ready {
					if notifier.Notify("READY=1", "STATUS=composite healthy") != nil {
						stopChild()
						continue
					}
					ready = true
					if !timer.Stop() {
						select {
						case <-timer.C:
						default:
						}
					}
					timer.Reset(staleAfter)
				} else if notifier.Notify("WATCHDOG=1") != nil {
					stopChild()
				} else {
					if !timer.Stop() {
						select {
						case <-timer.C:
						default:
						}
					}
					timer.Reset(staleAfter)
				}
			case "stopping", "failed":
				stopChild()
			default:
				stopChild()
			}
		case err := <-waited:
			admissionRemoved := removeSidecarAdmission(context.Background(), sidecarHealthy, stopSidecar)
			if !stopping {
				_ = notifier.Notify("STOPPING=1", "STATUS=connector exited")
			}
			if err == nil && stopping && admissionRemoved {
				return 0
			}
			return 1
		}
	}
}

func scanChildHealth(reader io.Reader, records chan<- childHealth, failed chan<- error) {
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
		records <- record
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
