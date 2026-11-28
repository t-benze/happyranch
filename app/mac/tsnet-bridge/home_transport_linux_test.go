package main

import (
	"context"
	"net"
	"os"
	"os/exec"
	"testing"
	"time"
)

// The same test runs unchanged on main: constructor admission itself must not
// leave no helper TCP listener. No fixture listener is subtracted.
func TestHomeTransportListenerCensus(t *testing.T) {
	if os.Getenv("HR_HOME_CENSUS_CHILD") != "1" {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		cmd := exec.CommandContext(ctx, os.Args[0], "-test.run=^TestHomeTransportListenerCensus$", "-test.v")
		cmd.Env = append(os.Environ(), "HR_HOME_CENSUS_CHILD=1")
		output, err := cmd.CombinedOutput()
		t.Logf("%s", output)
		if err != nil {
			t.Fatal(err)
		}
		return
	}
	if n := peerCensusListeners(peerCensus(t)); n != 0 {
		t.Fatalf("dirty census baseline: %d", n)
	}
	var controls []net.Listener
	for _, spec := range [][2]string{{"tcp4", "127.0.0.1:0"}, {"tcp6", "[::1]:0"}} {
		l, err := net.Listen(spec[0], spec[1])
		if err != nil {
			t.Fatal(err)
		}
		defer l.Close()
		controls = append(controls, l)
	}
	if n := peerCensusListeners(peerCensus(t)); n != 2 {
		t.Fatalf("census missed synthetic TCP controls: %d", n)
	}
	for _, l := range controls {
		if err := l.Close(); err != nil {
			t.Fatal(err)
		}
	}
	if n := peerCensusListeners(peerCensus(t)); n != 0 {
		t.Fatalf("controls left listeners: %d", n)
	}
	ca := makeTransportCA(t, true)
	h, err := newHomeTransport(ca.der, transportFingerprint(ca.der), "192.168.1.40")
	if err != nil {
		t.Fatal(err)
	}
	defer h.Close()
	if n := peerCensusListeners(peerCensus(t)); n != 0 {
		t.Errorf("helper CONNECT TCP listener present: %d; want zero", n)
	}
	h.Close()
	if n := peerCensusListeners(peerCensus(t)); n != 0 {
		t.Errorf("transport teardown left listeners: %d", n)
	}
}
