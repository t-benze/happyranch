package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"
	"os/signal"
	"syscall"

	"github.com/mdlayher/sdnotify"
	sidecar "happyranch/linux-tsnet-sidecar"
)

func main() {
	configPath := flag.String("config", "", "absolute path to the manual-N5 sidecar JSON configuration")
	flag.Parse()
	if *configPath == "" { fmt.Fprintln(os.Stderr, "configuration_invalid"); os.Exit(2) }
	raw, err := os.ReadFile(*configPath)
	if err != nil { fmt.Fprintln(os.Stderr, "configuration_invalid"); os.Exit(2) }
	var cfg sidecar.Config
	if json.Unmarshal(raw, &cfg) != nil || cfg.Validate() != nil { fmt.Fprintln(os.Stderr, "configuration_invalid"); os.Exit(2) }
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer cancel()
	engine := sidecar.NewTSNetEngine()
	svc := sidecar.New(cfg, engine, &netDialer{})
	if err := svc.Start(ctx); err != nil { fmt.Fprintln(os.Stderr, err); os.Exit(1) }
	notifier, err := sdnotify.New()
	if err != nil || notifier.Notify("READY=1", "STATUS=sidecar listener ready") != nil {
		_ = svc.Stop(); fmt.Fprintln(os.Stderr, "readiness_unavailable"); os.Exit(1)
	}
	<-ctx.Done()
	if err := svc.Stop(); err != nil { fmt.Fprintln(os.Stderr, err); os.Exit(1) }
}

type netDialer struct{}
func (*netDialer) DialContext(ctx context.Context, network, address string) (net.Conn, error) {
	return (&net.Dialer{}).DialContext(ctx, network, address)
}
