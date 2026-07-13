// Package main builds tsnet as a C-ABI archive for Swift embedding.
//
// This is a THROWAWAY spike (THR-097) — do NOT merge to main.
// Build: go build -buildmode=c-archive -o libtsnet.a .
//
// Exports a minimal C-callable surface:
//   - tsnet_init(authKey)      → initialise tsnet.Server with an auth key
//   - tsnet_dial(net, addr)    → Dial TCP/UDP through the tsnet userspace WireGuard tunnel
//   - tsnet_close()            → shutdown the tsnet.Server
//   - tsnet_last_error()       → retrieve the last error string
//
// WireGuard runs entirely in-process. No system tunnel interface is created,
// no NetworkExtension entitlement is required, and no VPN configuration
// profile is installed.
package main

/*
#include <stdlib.h>
*/
import "C"

import (
	"context"
	"strings"
	"sync"
	"time"
	"unsafe"

	"tailscale.com/tsnet"
)

var (
	srv       *tsnet.Server
	srvMu     sync.Mutex
	dialCtx   context.Context
	dialCxl   context.CancelFunc
	lastError string
)

//export tsnet_init
func tsnet_init(authKeyC *C.char, controlURL *C.char, hostnameC *C.char) C.int {
	srvMu.Lock()
	defer srvMu.Unlock()

	if srv != nil {
		lastError = "tsnet already initialised"
		return -1
	}

	authKey := C.GoString(authKeyC)
	hostname := C.GoString(hostnameC)
	ctrl := C.GoString(controlURL)

	s := &tsnet.Server{
		AuthKey:    authKey,
		Hostname:   hostname,
		ControlURL: ctrl,
		// Ephemeral: true means no persistent state is written to disk.
		// For a production ship, this would become a configurable path.
		Ephemeral: true,
	}

	dialCtx, dialCxl = context.WithCancel(context.Background())
	srv = s
	lastError = ""
	return 0
}

// tsnet_start brings the tsnet.Server online.
// On macOS this does NOT create a utun interface or install a VPN profile.
// All networking happens in-process via userspace WireGuard.
//export tsnet_start
func tsnet_start() C.int {
	srvMu.Lock()
	s := srv
	ctx := dialCtx
	srvMu.Unlock()

	if s == nil {
		lastError = "tsnet not initialised — call tsnet_init first"
		return -1
	}
	if ctx == nil {
		lastError = "tsnet context is nil"
		return -1
	}

	// Start blocks until the server is connected to the control plane
	// and has a working WireGuard tunnel.
	// NOTE: tsnet v1.78.0 Start() takes no context.
	if err := s.Start(); err != nil {
		lastError = "tsnet.Start failed: " + err.Error()
		return -2
	}

	lastError = ""
	return 0
}

//export tsnet_dial
func tsnet_dial(networkC *C.char, addrC *C.char, timeoutMs C.int) C.int {
	srvMu.Lock()
	s := srv
	srvMu.Unlock()

	if s == nil {
		lastError = "tsnet not initialised"
		return -1
	}

	network := C.GoString(networkC)
	addr := C.GoString(addrC)

	timeout := time.Duration(timeoutMs) * time.Millisecond
	if timeout <= 0 {
		timeout = 30 * time.Second
	}

	ctx, cancel := context.WithTimeout(dialCtx, timeout)
	defer cancel()

	conn, err := s.Dial(ctx, network, addr)
	if err != nil {
		// Surface whether this is a tsnet/DERP/WG error vs a generic dial failure
		lastError = "tsnet.Dial(" + network + ", " + addr + ") failed: " + err.Error()
		return -2
	}
	conn.Close()

	lastError = ""
	return 0
}

//export tsnet_local_addr
func tsnet_local_addr() *C.char {
	srvMu.Lock()
	s := srv
	srvMu.Unlock()

	if s == nil {
		lastError = "tsnet not initialised"
		return nil
	}

	// LocalClient may return nil before Start() completes.
	lc, err := s.LocalClient()
	if err != nil || lc == nil {
		lastError = "LocalClient not available: " + err.Error()
		return nil
	}

	st, err := lc.Status(dialCtx)
	if err != nil {
		lastError = "Status() failed: " + err.Error()
		return nil
	}

	// Return the first Tailscale IP (100.x.y.z) of this node.
	for _, ip := range st.Self.TailscaleIPs {
		return C.CString(ip.String())
	}

	lastError = "no Tailscale IP assigned"
	return nil
}

//export tsnet_close
func tsnet_close() {
	srvMu.Lock()
	defer srvMu.Unlock()

	if dialCxl != nil {
		dialCxl()
	}
	if srv != nil {
		srv.Close()
		srv = nil
	}
	lastError = ""
}

//export tsnet_last_error
func tsnet_last_error() *C.char {
	// Use an unsafe pointer trick to return a C string that the caller
	// must copy immediately — the underlying Go memory may move.
	// For a spike harness this is acceptable; production would use a
	// fixed-size buffer passed from the caller.
	return C.CString(lastError)
}

// freeCString frees a C string allocated by Go. Callers must free every
// non-nil string returned by the tsnet_* API.
//export tsnet_free_string
func tsnet_free_string(s *C.char) {
	C.free(unsafe.Pointer(s))
}

// ---- helper for testing (not exported, used by Go tests only) ----

func isConnRefused(err error) bool {
	if err == nil {
		return false
	}
	// On macOS, connection refused is "connection refused"
	return strings.Contains(strings.ToLower(err.Error()), "connection refused")
}

// compile-time check that we're building for C-ABI
var _ = unsafe.Sizeof(0)

func main() {
	// Unused — buildmode=c-archive produces a static library, not an executable.
	// Silence the "no exported functions" warning by exporting at least one symbol.
	panic("this is a C-ABI archive — do not run as an executable")
}
