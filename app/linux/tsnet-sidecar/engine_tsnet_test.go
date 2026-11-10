package sidecar

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestTSNetStartWaitsForUpUntilContextDeadline(t *testing.T) {
	// A local, deliberately unavailable control server leaves the real engine
	// joining. Start must wait for Up, rather than reject its transient state.
	control := httptest.NewTLSServer(http.NotFoundHandler())
	defer control.Close()
	engine := NewTSNetEngine()
	defer engine.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_, err := engine.Start(ctx, EngineConfig{
		StateDir: t.TempDir(), ControlURL: control.URL,
		RoleIdentity: "home-sidecar-wait", ExpectedPeer: "missing-peer",
	}, nil)
	if !errors.Is(err, ErrEngine) {
		t.Fatalf("Start error = %v, want engine unavailable", err)
	}
	if !errors.Is(ctx.Err(), context.DeadlineExceeded) {
		t.Fatal("Start rejected a joining engine before the readiness deadline")
	}
}
