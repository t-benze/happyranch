//go:build linux || darwin

package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"time"

	"golang.org/x/sys/unix"
	sidecar "happyranch/linux-tsnet-sidecar"
)

// Compiled on Linux for filesystem/lifecycle tests; run selects this path only
// on Darwin. The Linux service continues to use serve and sd_notify unchanged.
const launchdWatchdogExit = 75

type readinessRecord struct {
	PID                 int      `json:"pid"`
	ProcessStartedAt    float64  `json:"process_started_at"`
	Ready               bool     `json:"ready"`
	LastHealthyWallTime *float64 `json:"last_healthy_wall_time"`
	Sequence            uint64   `json:"sequence"`
	Reason              string   `json:"reason"`
}

func validReadinessPath(path string) bool {
	return filepath.IsAbs(path) && path != "/" && path == filepath.Clean(path)
}

// Pin every literal directory, matching launchd_files.literal_directory. Only
// the writable leaf needs to be current-euid owned 0700; ancestors keep modes.
func readinessParent(path string) (int, error) {
	if !validReadinessPath(path) {
		return -1, sidecar.ErrConfiguration
	}
	flags := unix.O_RDONLY | unix.O_DIRECTORY | unix.O_NOFOLLOW | unix.O_CLOEXEC
	fd, err := unix.Open("/", flags, 0)
	if err != nil {
		return -1, err
	}
	for _, part := range strings.Split(strings.TrimPrefix(filepath.Dir(path), "/"), "/") {
		if part == "" {
			continue
		}
		if err := unix.Mkdirat(fd, part, 0o700); err != nil && err != unix.EEXIST {
			unix.Close(fd)
			return -1, err
		}
		child, err := unix.Openat(fd, part, flags, 0)
		unix.Close(fd)
		if err != nil {
			return -1, err
		}
		fd = child
	}
	var info unix.Stat_t
	if err := unix.Fstat(fd, &info); err != nil {
		unix.Close(fd)
		return -1, err
	}
	if info.Uid != uint32(os.Geteuid()) || info.Mode&0o7777 != 0o700 {
		unix.Close(fd)
		return -1, sidecar.ErrConfiguration
	}
	return fd, nil
}

func (r *readinessRecord) publish(path string) error {
	r.Sequence++
	parent, err := readinessParent(path)
	if err != nil {
		return err
	}
	defer unix.Close(parent)
	var nonce [16]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return err
	}
	temporary := "." + filepath.Base(path) + "." + hex.EncodeToString(nonce[:])
	fd, err := unix.Openat(parent, temporary, unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0o600)
	if err != nil {
		return err
	}
	defer unix.Unlinkat(parent, temporary, 0)
	f := os.NewFile(uintptr(fd), temporary)
	defer f.Close()
	if err := f.Chmod(0o600); err != nil {
		return err
	}
	// These six JSON fields and fixed reasons are the C3 connector format, read
	// unchanged by launchd_readiness._read_record. No provider text is persisted.
	if err := json.NewEncoder(f).Encode(r); err != nil {
		return err
	}
	if err := f.Sync(); err != nil {
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	return unix.Renameat(parent, temporary, parent, filepath.Base(path))
}

// A healthy tick is committed only AFTER publication. time.Since uses the
// monotonic component of origin; wall time in the record is observation only.
// Expiry never waits for the service, publisher, filesystem or lifecycle lock.
type launchdWatchdog struct {
	origin   time.Time
	lastPass atomic.Int64
	done     chan struct{}
	stopped  chan struct{}
}

func startLaunchdWatchdog(interval time.Duration) *launchdWatchdog {
	w := &launchdWatchdog{origin: time.Now(), done: make(chan struct{}), stopped: make(chan struct{})}
	go func() {
		defer close(w.stopped)
		for {
			remaining := interval - (time.Since(w.origin) - time.Duration(w.lastPass.Load()))
			if remaining <= 0 {
				os.Exit(launchdWatchdogExit)
			}
			timer := time.NewTimer(remaining)
			select {
			case <-w.done:
				timer.Stop()
				return
			case <-timer.C:
			}
		}
	}()
	return w
}

func (w *launchdWatchdog) healthy() { w.lastPass.Store(int64(time.Since(w.origin))) }
func (w *launchdWatchdog) stop()    { close(w.done); <-w.stopped }

func serveLaunchd(ctx context.Context, svc *sidecar.Sidecar, path string, interval time.Duration) int {
	if !validReadinessPath(path) || interval <= 0 {
		return 2
	}
	watchdog := startLaunchdWatchdog(interval)
	defer watchdog.stop()
	record := readinessRecord{PID: os.Getpid(), ProcessStartedAt: float64(time.Now().UnixNano()) / 1e9, Reason: "starting"}
	publish := func() bool {
		if err := record.publish(path); err != nil {
			fmt.Fprintln(os.Stderr, "sidecar: readiness publication failed")
			return false
		}
		return true
	}
	stop := func(code int, reason string) int {
		record.Ready = false
		record.Reason = reason
		if !publish() {
			code = 1
		}
		if err := svc.Stop(); err != nil {
			code = 1
		}
		return code
	}
	if !publish() {
		return 1
	}
	if err := svc.Start(ctx); err != nil {
		fmt.Fprintln(os.Stderr, err)
		if ctx.Err() != nil {
			return stop(0, "stopping")
		}
		return stop(1, "waiting-for-readiness")
	}
	healthy := func() bool {
		now := float64(time.Now().UnixNano()) / 1e9
		record.Ready = true
		record.LastHealthyWallTime = &now
		record.Reason = "healthy"
		if !publish() {
			return false
		}
		watchdog.healthy()
		return true
	}
	if ctx.Err() != nil {
		return stop(0, "stopping")
	}
	if !svc.Running() {
		return stop(1, "readiness-or-listener-lost")
	}
	if !healthy() {
		return stop(1, "readiness-or-listener-lost")
	}
	cadence := interval / 2
	if cadence > 100*time.Millisecond {
		cadence = 100 * time.Millisecond
	}
	if cadence <= 0 {
		cadence = time.Nanosecond
	}
	ticker := time.NewTicker(cadence)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return stop(0, "stopping")
		case <-ticker.C:
			if !svc.Running() {
				return stop(1, "readiness-or-listener-lost")
			}
			if !healthy() {
				return stop(1, "readiness-or-listener-lost")
			}
		}
	}
}
