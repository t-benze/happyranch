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
	return superviseConnector(context.Background(), argv, notifier, 12*time.Second, nil)
}

func superviseConnector(parent context.Context, argv []string, notifier notifySender, staleAfter time.Duration, started chan<- *exec.Cmd) int {
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
	timer := time.NewTimer(staleAfter)
	defer timer.Stop()
	var sequence uint64
	ready := false
	stopping := false
	stopChild := func() {
		if !stopping {
			stopping = true
			_ = notifier.Notify("STOPPING=1", "STATUS=connector stopping")
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
			if !timer.Stop() {
				select { case <-timer.C: default: }
			}
			timer.Reset(staleAfter)
			switch record.State {
			case "waiting":
				if ready { stopChild() }
			case "ready":
				if ready || stopping || notifier.Notify("READY=1", "STATUS=connector healthy") != nil {
					stopChild()
				} else {
					ready = true
				}
			case "healthy":
				if !ready || stopping || notifier.Notify("WATCHDOG=1") != nil { stopChild() }
			case "stopping", "failed":
				stopChild()
			default:
				stopChild()
			}
		case err := <-waited:
			if !stopping { _ = notifier.Notify("STOPPING=1", "STATUS=connector exited") }
			if err == nil && stopping { return 0 }
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
