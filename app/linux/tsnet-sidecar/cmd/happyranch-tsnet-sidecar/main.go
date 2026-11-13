package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"runtime"
	"strconv"
	"syscall"
	"time"

	"github.com/mdlayher/sdnotify"
	sidecar "happyranch/linux-tsnet-sidecar"
)

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	code := run(ctx, os.Args[1:])
	cancel()
	os.Exit(code)
}

func run(ctx context.Context, args []string) int {
	flags := flag.NewFlagSet("happyranch-tsnet-sidecar", flag.ContinueOnError)
	path := flags.String("config", "", "absolute path to sidecar JSON configuration")
	credential := flags.String("credential-file", "", "absolute path to enrollment input (never a key literal)")
	readiness := flags.String("readiness-file", "", "Darwin only: absolute path to the launchd readiness record")
	watchdog := flags.Duration("watchdog-interval", 30*time.Second, "Darwin only: maximum time without a completed healthy publication")
	if err := flags.Parse(args); err != nil {
		return 2
	}
	if flags.NArg() != 0 || !filepath.IsAbs(*path) {
		fmt.Fprintln(os.Stderr, "--config requires an absolute JSON path")
		return 2
	}
	if runtime.GOOS == "darwin" {
		if !validReadinessPath(*readiness) || *watchdog <= 0 {
			fmt.Fprintln(os.Stderr, "sidecar: invalid Darwin readiness options")
			return 2
		}
	} else {
		invalid := false
		flags.Visit(func(f *flag.Flag) {
			if f.Name == "readiness-file" || f.Name == "watchdog-interval" {
				invalid = true
			}
		})
		if invalid {
			fmt.Fprintln(os.Stderr, "sidecar: readiness options require Darwin")
			return 2
		}
	}
	cfg, err := loadConfig(*path, *credential)
	if err != nil {
		fmt.Fprintln(os.Stderr, "sidecar: configuration invalid")
		return 2
	}
	svc := sidecar.New(cfg, sidecar.NewTSNetEngine(), &net.Dialer{Timeout: 5 * time.Second})
	if runtime.GOOS == "darwin" {
		return serveLaunchd(ctx, svc, *readiness, *watchdog)
	}
	return serve(ctx, svc)
}

func loadConfig(path, credentialFile string) (sidecar.Config, error) {
	// Nonblocking/no-follow admission prevents a replaced FIFO or symlink from
	// turning a config read into an unbounded wait or an aliased state read.
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_NONBLOCK|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return sidecar.Config{}, err
	}
	f := os.NewFile(uintptr(fd), path)
	defer f.Close()
	st, err := f.Stat()
	if err != nil || !st.Mode().IsRegular() || st.Size() > 1<<20 {
		return sidecar.Config{}, sidecar.ErrConfiguration
	}
	var cfg sidecar.Config
	decoder := json.NewDecoder(io.LimitReader(f, (1<<20)+1))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&cfg); err != nil {
		return cfg, err
	}
	if err := decoder.Decode(new(any)); err != io.EOF {
		return cfg, sidecar.ErrConfiguration
	}
	if credentialFile != "" {
		cfg.CredentialFile = credentialFile
	}
	return cfg, cfg.Validate()
}

func watchdogInterval() (time.Duration, error) {
	if pid := os.Getenv("WATCHDOG_PID"); pid != "" && pid != strconv.Itoa(os.Getpid()) {
		return 0, nil
	}
	usec := os.Getenv("WATCHDOG_USEC")
	if usec == "" {
		return 0, nil
	}
	n, err := strconv.ParseUint(usec, 10, 64)
	if err != nil || n == 0 || n > uint64((1<<63-1)/time.Microsecond) {
		return 0, fmt.Errorf("sidecar: invalid WATCHDOG_USEC")
	}
	return time.Duration(n) * time.Microsecond / 2, nil
}

func serve(ctx context.Context, svc *sidecar.Sidecar) int {
	interval, err := watchdogInterval()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 2
	}
	var notifier *sdnotify.Notifier
	if os.Getenv(sdnotify.Socket) != "" {
		notifier, err = sdnotify.New()
		if err != nil {
			fmt.Fprintln(os.Stderr, "sidecar: notification socket unavailable")
			return 1
		}
	}
	defer notifier.Close()
	stop := func(code int) int {
		if err := notifier.Notify(sdnotify.Stopping); err != nil {
			code = 1
		}
		if err := svc.Stop(); err != nil {
			code = 1
		}
		return code
	}
	if err := svc.Start(ctx); err != nil {
		fmt.Fprintln(os.Stderr, err)
		if ctx.Err() != nil {
			return stop(0)
		}
		return stop(1)
	}
	if ctx.Err() != nil {
		return stop(0)
	}
	if !svc.Running() {
		return stop(1)
	}
	if err := notifier.Notify(sdnotify.Ready); err != nil {
		return stop(1)
	}
	var watchdog <-chan time.Time
	if interval > 0 {
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		watchdog = ticker.C
	}
	health := time.NewTicker(100 * time.Millisecond)
	defer health.Stop()
	for {
		select {
		case <-ctx.Done():
			return stop(0)
		case <-health.C:
			if !svc.Running() {
				fmt.Fprintln(os.Stderr, "sidecar: serving loop ended")
				return stop(1)
			}
		case <-watchdog:
			if !svc.Running() {
				return stop(1)
			}
			if err := notifier.Notify("WATCHDOG=1"); err != nil {
				return stop(1)
			}
		}
	}
}
