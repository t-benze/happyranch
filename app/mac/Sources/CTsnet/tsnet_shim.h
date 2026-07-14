// tsnet_shim.h — Clean C header for the tsnet C-ABI archive (libtsnet.a).
//
// All tsnet_* functions return 0 on success, non-zero on failure.
// Error details are available via tsnet_last_error().
//
// WireGuard runs entirely in-process via the go:tailscale.com/tsnet engine.
// No system tunnel (utun) interface is created, no NetworkExtension
// entitlement is required, and no VPN configuration profile is installed.

#ifndef TSNET_SHIM_H
#define TSNET_SHIM_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>

// ---- Engine lifecycle ----

// tsnet_init initialises the tsnet engine with the given auth key.
// controlURL may be NULL to use the default (https://controlplane.tailscale.com).
// hostname may be NULL for auto-generated.
// Returns 0 on success, -1 if already initialised.
int tsnet_init(const char *authKey, const char *controlURL, const char *hostname);

// tsnet_start brings the tsnet engine online.
// Blocks until connected to the control plane and WireGuard tunnel established.
// Returns 0 on success, -2 on connection failure.
int tsnet_start(void);

// tsnet_close shuts down the tsnet engine and all active connections.
void tsnet_close(void);

// ---- Connectivity probe ----

// tsnet_dial attempts a TCP connection through the in-process WireGuard tunnel
// then immediately closes it.  Use as a connectivity probe.
// network: "tcp", "tcp4", or "tcp6"
// addr: "host:port" (Tailscale 100.x.y.z address or MagicDNS name)
// timeoutMs: timeout in milliseconds (0 = 30s default)
// Returns 0 on success (connection established and closed),
// -1 if not initialised, -2 on dial failure.
int tsnet_dial(const char *network, const char *addr, int timeoutMs);

// ---- Duplex connection (conn handle table) ----

// tsnet_conn_dial opens a duplex TCP connection through the tsnet tunnel
// and returns a non-negative connection handle, or -1 on failure.
// The caller must close the connection with tsnet_conn_close.
int tsnet_conn_dial(const char *network, const char *addr, int timeoutMs);

// tsnet_conn_read reads up to bufSize bytes from the connection into buf.
// Returns the number of bytes read (0 = clean close by peer), or -1 on error.
int tsnet_conn_read(int connID, char *buf, int bufSize);

// tsnet_conn_write writes up to dataLen bytes from data to the connection.
// Returns the number of bytes written, or -1 on error.
int tsnet_conn_write(int connID, const char *data, int dataLen);

// tsnet_conn_close closes the connection and removes its handle.
// Safe to call on an invalid or already-closed handle.
void tsnet_conn_close(int connID);

// ---- Queries ----

// tsnet_local_addr returns the Tailscale IP (100.x.y.z) of this node.
// Caller must free with tsnet_free_string.  Returns NULL on failure.
char *tsnet_local_addr(void);

// tsnet_last_error returns a human-readable description of the last error.
// Caller must free with tsnet_free_string.
char *tsnet_last_error(void);

// tsnet_free_string frees a string returned by tsnet_* functions.
void tsnet_free_string(char *s);

#ifdef __cplusplus
}
#endif

#endif // TSNET_SHIM_H
