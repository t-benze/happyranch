// tsnet_stub.c — Weak-symbol stubs for the tsnet C-ABI.
//
// These weak-symbol implementations satisfy the tsnet_* references during
// `swift build` / `swift test` on a clean checkout that has no Go toolchain
// and no pre-built libtsnet.a.
//
// When the real Go-built libtsnet.a IS present, the linker flag (applied
// conditionally in Package.swift) causes the real strong symbols to override
// these weak stubs.  When it is absent, these weak symbols resolve the
// references and all tsnet calls return safe error values:
//   - Lifecycle: tsnet_init/tsnet_start return -1 (not initialised).
//   - Connections: tsnet_conn_dial/tsnet_dial return -1.
//   - I/O: tsnet_conn_read/tsnet_conn_write return -1.
//   - Queries: tsnet_local_addr/tsnet_last_error return NULL.
//
// This keeps the DEFAULT build self-contained — no manual pre-step required.

#include <stdint.h>

__attribute__((weak))
int tsnet_init(const char *authKey, const char *controlURL, const char *hostname) { return -1; }

__attribute__((weak))
int tsnet_start(void) { return -1; }

__attribute__((weak))
void tsnet_close(void) {}

__attribute__((weak))
int tsnet_dial(const char *network, const char *addr, int timeoutMs) { return -1; }

__attribute__((weak))
int tsnet_conn_dial(const char *network, const char *addr, int timeoutMs) { return -1; }

__attribute__((weak))
int tsnet_conn_read(int connID, char *buf, int bufSize) { return -1; }

__attribute__((weak))
int tsnet_conn_write(int connID, const char *data, int dataLen) { return -1; }

__attribute__((weak))
void tsnet_conn_close(int connID) {}

__attribute__((weak))
char *tsnet_local_addr(void) { return 0; }

__attribute__((weak))
char *tsnet_last_error(void) { return 0; }

__attribute__((weak))
void tsnet_free_string(char *s) {}
