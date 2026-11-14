// Package main builds tsnet as a C-ABI archive for Swift embedding.
//
// Build: go build -buildmode=c-archive -o libtsnet.a .
//
// Exports a C-callable surface for in-process WireGuard via tailscale.com/tsnet:
//   - tsnet_init / tsnet_start / tsnet_close          — engine lifecycle
//   - tsnet_dial                                       — connectivity probe (establish+close)
//   - tsnet_conn_dial / tsnet_conn_read / tsnet_conn_write / tsnet_conn_close — duplex connections
//   - tsnet_local_addr / tsnet_last_error / tsnet_free_string
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
	"io"
	"net"
	"strings"
	"sync"
	"sync/atomic"
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
	errorMu   sync.Mutex

	// Connection handle table for duplex conns
	connMu     sync.Mutex
	connTable  = make(map[int32]net.Conn)
	nextConnID int32
)

//export tsnet_init
func tsnet_init(authKeyC *C.char, controlURL *C.char, hostnameC *C.char) C.int {
	opMu.Lock()
	defer opMu.Unlock()
	srvMu.Lock()
	defer srvMu.Unlock()

	if srv != nil || managed != nil {
		setLastError("tsnet already initialised")
		return -1
	}

	authKey := C.GoString(authKeyC)
	hostname := C.GoString(hostnameC)
	ctrl := C.GoString(controlURL)

	s := &tsnet.Server{
		AuthKey:    authKey,
		Hostname:   hostname,
		ControlURL: ctrl,
		Ephemeral:  true,
	}

	dialCtx, dialCxl = context.WithCancel(context.Background())
	srv = s
	setLastError("")
	return 0
}

//export tsnet_start
func tsnet_start() C.int {
	opMu.Lock()
	defer opMu.Unlock()
	srvMu.Lock()
	s := srv
	if managed != nil {
		srvMu.Unlock()
		return C.int(managedResult(managedBusy))
	}
	ctx := dialCtx
	srvMu.Unlock()

	if s == nil {
		setLastError("tsnet not initialised — call tsnet_init first")
		return -1
	}
	if ctx == nil {
		setLastError("tsnet context is nil")
		return -1
	}

	if err := s.Start(); err != nil {
		setLastError("tsnet.Start failed: " + err.Error())
		return -2
	}

	setLastError("")
	return 0
}

//export tsnet_dial
func tsnet_dial(networkC *C.char, addrC *C.char, timeoutMs C.int) C.int {
	srvMu.Lock()
	if managed != nil {
		srvMu.Unlock()
		return C.int(managedResult(managedUnsafe))
	}
	s := srv
	srvMu.Unlock()

	if s == nil {
		setLastError("tsnet not initialised")
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
		setLastError("tsnet.Dial(" + network + ", " + addr + ") failed: " + err.Error())
		return -2
	}
	conn.Close()

	setLastError("")
	return 0
}

// tsnet_conn_dial opens a duplex connection through the in-process WireGuard tunnel.
// Returns a non-negative connection handle on success, or -1 on failure.
// The caller must close the connection with tsnet_conn_close when done.
//
//export tsnet_conn_dial
func tsnet_conn_dial(networkC *C.char, addrC *C.char, timeoutMs C.int) C.int {
	srvMu.Lock()
	if managed != nil {
		srvMu.Unlock()
		return C.int(managedResult(managedUnsafe))
	}
	s := srv
	srvMu.Unlock()

	if s == nil {
		setLastError("tsnet not initialised")
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
		setLastError("tsnet.Dial(" + network + ", " + addr + ") failed: " + err.Error())
		return -1
	}

	connMu.Lock()
	id := atomic.AddInt32(&nextConnID, 1)
	connTable[id] = conn
	connMu.Unlock()

	setLastError("")
	return C.int(id)
}

// tsnet_conn_read reads up to bufSize bytes from the connection into the
// caller-provided buffer. Returns the number of bytes read (may be 0 on
// clean close), or -1 on error. Use tsnet_last_error() for details.
//
//export tsnet_conn_read
func tsnet_conn_read(connID C.int, buf *C.char, bufSize C.int) C.int {
	connMu.Lock()
	conn := connTable[int32(connID)]
	connMu.Unlock()

	if conn == nil {
		setLastError("invalid connection handle")
		return -1
	}

	// Build a Go slice backed by the C buffer — data is copied into it,
	// so no cgo pointer rules are violated.
	goBuf := (*[1 << 30]byte)(unsafe.Pointer(buf))[:int(bufSize):int(bufSize)]

	n, err := conn.Read(goBuf)
	if err != nil {
		if err == io.EOF {
			return 0
		}
		if _, ok := conn.(*managedConnection); ok {
			return C.int(managedResult(managedBackend))
		}
		setLastError("conn.Read failed: " + err.Error())
		return -1
	}

	return C.int(n)
}

// tsnet_conn_write writes up to dataLen bytes from the caller-provided buffer
// to the connection. Returns the number of bytes written, or -1 on error.
//
//export tsnet_conn_write
func tsnet_conn_write(connID C.int, data *C.char, dataLen C.int) C.int {
	connMu.Lock()
	conn := connTable[int32(connID)]
	connMu.Unlock()

	if conn == nil {
		setLastError("invalid connection handle")
		return -1
	}

	goBuf := (*[1 << 30]byte)(unsafe.Pointer(data))[:int(dataLen):int(dataLen)]

	if c, ok := conn.(*managedConnection); ok {
		ctx, cancel := context.WithTimeout(c.owner.ctx, 30*time.Second)
		code := c.validate(ctx)
		cancel()
		if code != 0 {
			tsnet_conn_close(connID)
			return C.int(managedResult(code))
		}
	}
	n, err := conn.Write(goBuf)
	if err != nil {
		if _, ok := conn.(*managedConnection); ok {
			return C.int(managedResult(managedBackend))
		}
		setLastError("conn.Write failed: " + err.Error())
		return -1
	}

	return C.int(n)
}

// tsnet_conn_close closes and removes the connection handle.
// Safe to call on an invalid or already-closed handle.
//
//export tsnet_conn_close
func tsnet_conn_close(connID C.int) {
	connMu.Lock()
	conn := connTable[int32(connID)]
	delete(connTable, int32(connID))
	connMu.Unlock()

	if conn != nil {
		conn.Close()
	}
}

//export tsnet_local_addr
func tsnet_local_addr() *C.char {
	srvMu.Lock()
	if managed != nil && !managedActive(managed) {
		srvMu.Unlock()
		managedResult(managedUnavailable)
		return nil
	}
	s := srv
	ctx := dialCtx
	isManaged := managed != nil
	srvMu.Unlock()

	if s == nil {
		setLastError("tsnet not initialised")
		return nil
	}

	lc, err := s.LocalClient()
	if err != nil || lc == nil {
		if isManaged {
			managedResult(managedUnavailable)
		} else {
			if err != nil {
				setLastError("LocalClient not available: " + err.Error())
			} else {
				setLastError("LocalClient not available")
			}
		}
		return nil
	}

	st, err := lc.Status(ctx)
	if err != nil {
		if isManaged {
			managedResult(managedBackend)
		} else {
			setLastError("Status() failed: " + err.Error())
		}
		return nil
	}

	if st == nil || st.Self == nil {
		if isManaged {
			managedResult(managedUnavailable)
		} else {
			setLastError("no Tailscale IP assigned")
		}
		return nil
	}
	for _, ip := range st.Self.TailscaleIPs {
		return C.CString(ip.String())
	}

	setLastError("no Tailscale IP assigned")
	return nil
}

//export tsnet_close
func tsnet_close() {
	cancelManaged()
	closeAllConnections()
	opMu.Lock()
	defer opMu.Unlock()
	srvMu.Lock()
	m := managed
	srvMu.Unlock()
	if m != nil {
		if !m.closeServer() {
			managedResult(managedStateError)
			return
		}
		if err := m.profile.close(); err != nil {
			m.closeFailed = true
			managedResult(managedStateError)
			return
		}
		srvMu.Lock()
		managed = nil
		dialCtx = nil
		dialCxl = nil
		srvMu.Unlock()
		managedResult(0)
		return
	}
	srvMu.Lock()
	defer srvMu.Unlock()
	if dialCxl != nil {
		dialCxl()
	}
	if srv != nil {
		srv.Close()
		srv = nil
	}
	setLastError("")
}

//export tsnet_init_managed
func tsnet_init_managed(authKey, controlURL, hostname, stateDir *C.char) C.int {
	return C.int(managedInit(C.GoString(authKey), C.GoString(controlURL), C.GoString(hostname), C.GoString(stateDir)))
}

//export tsnet_up_managed
func tsnet_up_managed(timeoutMs C.int) C.int { return C.int(managedUp(int(timeoutMs))) }

//export tsnet_cancel
func tsnet_cancel() { cancelManaged() }

//export tsnet_conn_dial_managed
func tsnet_conn_dial_managed(host *C.char, port, timeoutMs C.int) C.int {
	srvMu.Lock()
	m := managed
	if !managedActive(m) {
		srvMu.Unlock()
		return C.int(managedResult(managedUnavailable))
	}
	parent := m.ctx
	srvMu.Unlock()
	timeout := 30 * time.Second
	if timeoutMs > 0 {
		timeout = time.Duration(timeoutMs) * time.Millisecond
	}
	ctx, cancel := context.WithTimeout(parent, timeout)
	defer cancel()
	conn, code := managedDialPeer(ctx, C.GoString(host), int(port))
	if code != 0 {
		return C.int(managedResult(code))
	}
	srvMu.Lock()
	defer srvMu.Unlock()
	if !managedActive(m) {
		conn.Close()
		return C.int(managedResult(managedCanceled))
	}
	connMu.Lock()
	id := atomic.AddInt32(&nextConnID, 1)
	connTable[id] = conn
	connMu.Unlock()
	managedResult(0)
	return C.int(id)
}

func setLastError(message string) { errorMu.Lock(); lastError = message; errorMu.Unlock() }

//export tsnet_last_error
func tsnet_last_error() *C.char {
	errorMu.Lock()
	defer errorMu.Unlock()
	return C.CString(lastError)
}

//export tsnet_free_string
func tsnet_free_string(s *C.char) {
	C.free(unsafe.Pointer(s))
}

// ---- helpers ----

func isConnRefused(err error) bool {
	if err == nil {
		return false
	}
	return strings.Contains(strings.ToLower(err.Error()), "connection refused")
}

var _ = unsafe.Sizeof(0)

func main() {
	panic("this is a C-ABI archive — do not run as an executable")
}
