package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	"github.com/mdlayher/sdnotify"
	sidecar "happyranch/linux-tsnet-sidecar"
)

func main() {
	configPath := flag.String("config", "", "absolute path to the manual-N5 sidecar JSON configuration")
	flag.Parse()
	if *configPath == "" {
		fmt.Fprintln(os.Stderr, "configuration_invalid")
		os.Exit(2)
	}
	raw, err := os.ReadFile(*configPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, "configuration_invalid")
		os.Exit(2)
	}
	var cfg sidecar.Config
	if json.Unmarshal(raw, &cfg) != nil {
		fmt.Fprintln(os.Stderr, "configuration_invalid")
		os.Exit(2)
	}
	credentialsDir := os.Getenv("CREDENTIALS_DIRECTORY")
	if !filepath.IsAbs(credentialsDir) {
		fmt.Fprintln(os.Stderr, "credential_unavailable")
		os.Exit(2)
	}
	cfg.CredentialFile = filepath.Join(filepath.Clean(credentialsDir), "enrollment.key")
	if cfg.Validate() != nil {
		fmt.Fprintln(os.Stderr, "configuration_invalid")
		os.Exit(2)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer cancel()
	engine := sidecar.NewTSNetEngine()
	svc := sidecar.New(cfg, engine, &netDialer{})
	if err := svc.Start(ctx); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	notifier, err := sdnotify.New()
	if err != nil || notifier.Notify("READY=1", "STATUS=sidecar listener ready") != nil {
		_ = svc.Stop()
		fmt.Fprintln(os.Stderr, "readiness_unavailable")
		os.Exit(1)
	}
	watchdogErr := make(chan error, 1)
	go watchdogLoop(ctx, cancel, notifier, 10*time.Second, watchdogErr)
	<-ctx.Done()
	if err := svc.Stop(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	// Category-only lifecycle evidence for the packaged systemd integration.
	// Stop returns only after listener removal, active-flow drain, and the
	// idempotent engine close have completed.
	fmt.Fprintln(os.Stderr, "lifecycle_stop_complete")
	select {
	case <-watchdogErr:
		fmt.Fprintln(os.Stderr, "watchdog_unavailable")
		os.Exit(1)
	default:
	}
}

type systemdNotifier interface {
	Notify(...string) error
}

var _ systemdNotifier = (*sdnotify.Notifier)(nil)

func watchdogLoop(ctx context.Context, cancel context.CancelFunc, notifier systemdNotifier, interval time.Duration, failed chan<- error) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if err := notifier.Notify("WATCHDOG=1"); err != nil {
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
