// tsnet_shim.h — Clean C header wrapping the cgo-generated libtsnet.h
//
// THROWAWAY spike (THR-097).  Do NOT merge to main.
//
// This shim provides a minimal, well-typed C surface that Swift can call
// via a module map.  All tsnet_* functions return 0 on success, non-zero
// on failure.  Error details are available via tsnet_last_error().
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

// tsnet_init initialises the tsnet engine with the given auth key.
// controlURL may be NULL to use the default (https://controlplane.tailscale.com).
// hostname may be NULL for auto-generated.
// Returns 0 on success, -1 if already initialised.
int tsnet_init(const char *authKey, const char *controlURL, const char *hostname);

// tsnet_start brings the tsnet engine online.
// Blocks until connected to the control plane and WireGuard tunnel established.
// Returns 0 on success, -2 on connection failure.
int tsnet_start(void);

// tsnet_dial attempts a TCP connection through the in-process WireGuard tunnel.
// network: "tcp" or "tcp4" or "tcp6"
// addr: "host:port" (host may be a Tailscale 100.x.y.z address or MagicDNS name)
// timeoutMs: timeout in milliseconds (0 = 30s default)
// Returns 0 on success (the connection was established and closed),
// -1 if not initialised, -2 on dial failure.
int tsnet_dial(const char *network, const char *addr, int timeoutMs);

// tsnet_local_addr returns the Tailscale IP (100.x.y.z) of this node.
// Caller must free with tsnet_free_string.  Returns NULL on failure.
char *tsnet_local_addr(void);

// tsnet_close shuts down the tsnet engine.
void tsnet_close(void);

// tsnet_last_error returns a human-readable description of the last error.
// Caller must free with tsnet_free_string.
char *tsnet_last_error(void);

// tsnet_free_string frees a string returned by tsnet_* functions.
void tsnet_free_string(char *s);

#ifdef __cplusplus
}
#endif

#endif // TSNET_SHIM_H
