# HappyRanch Mac App (Phase 1 — Dev Shell Prototype A)

A native macOS WebView dev-shell for HappyRanch. Built in Swift using SwiftUI + WKWebView.

## Scope

**Prototype A — DEV-SHELL ONLY.** This is NOT a bundled/packaged app or Electron shell. It:
- Supervises the HappyRanch daemon using the existing repo checkout and launch path
- Opens `http://127.0.0.1:<port>/` in a WKWebView after a health probe succeeds
- Provides a diagnostics panel for troubleshooting
- **Does NOT** bundle Python, sign, notarize, or auto-update

## Build

Requires **Xcode 16+** (macOS 15+).

```bash
# Set Xcode path if needed
export DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer

# Build the app
cd app/mac
xcodebuild -scheme HappyRanchApp -sdk macosx build

# Run tests
swift test
```

## Run (unsigned, local only)

```bash
# From the happyranch repo root:
open app/mac/.build/debug/HappyRanchApp.app
```

Or via `swift run`:
```bash
cd app/mac
swift run HappyRanchApp
```

> **Note:** The app runs unsigned. macOS Gatekeeper may block it on first launch.
> Right-click → Open in Finder to bypass, or run `xattr -cr <app>`.

## Architecture

```
app/mac/
├── Package.swift
├── Sources/
│   ├── HappyRanchSupervisor/     # Testable business logic
│   │   ├── DaemonState.swift     # Lifecycle state enum
│   │   ├── DaemonSupervisor.swift # State machine + lifecycle
│   │   ├── PortReader.swift      # Read daemon.port
│   │   ├── HealthProbe.swift     # Health check endpoint
│   │   ├── DiagnosticsRedactor.swift  # Secret redaction
│   │   ├── DiagnosticsCollector.swift # Diagnostics bundle
│   │   ├── EnvironmentSanitizer.swift # Child-process env sanitization
│   │   └── RuntimeTransport.swift   # URL transport protocol (local/remote)
│   └── HappyRanchApp/            # GUI shell (SwiftUI + WKWebView)
│       ├── HappyRanchApp.swift
│       ├── ContentView.swift
│       └── DiagnosticsView.swift
├── Tests/
│   └── HappyRanchSupervisorTests/ # Unit tests for supervisor
└── README.md
```

## Daemon Lifecycle States

`notConfigured → stopped → starting → running → stopping → stopped`
                                       ↓          ↓
                                   unhealthy    crashed
                                       ↓
                                    running (recovery)

External daemon: `notConfigured → externalRunning` (no managed stop without confirmation)

## Verified Behaviors

- **Managed vs external:** App-launched daemon is managed; externally-started daemon is attached as `externalRunning`. External daemon stop is **disabled** in the Phase 1 UI (no confirmation dialog yet). The supervisor guard (unconfirmed external stop rejected) remains the source of truth.
- **Stale PID:** If a PID file exists but the referenced process is dead, state moves to `stalePid`.
- **Diagnostics redaction:** All bearer tokens (`daemon.token`), allow-rules, API secrets, and log/error strings are redacted at the `collect()` boundary — live display and export share ONE redaction guarantee.
- **Loopback-only:** Always binds to `127.0.0.1`. Never sets `HAPPYRANCH_DAEMON_BIND_HOST` to anything else.
- **Environment sanitization:** The daemon child Process receives a sanitized environment (PATH, HOME, HAPPYRANCH_DAEMON_HOME, and optionally HAPPYRANCH_WEB_DIST). No other HAPPYRANCH_* overrides, CORS/auth/debug vars, or secrets leak from the parent process.
- **Managed process lifecycle:** The app retains the Process handle for the managed daemon. On stop/quit, it sends SIGTERM (via `Process.terminate()`) and waits for exit with a bounded 5-second timeout; escalation to `crashed` if the process doesn't respond. External daemons are **never** terminated by stop/quit.
- **RuntimeTransport protocol:** Only `LocalLoopbackTransport` (constructs `http://127.0.0.1:<port>/`) is wired to the UI. `RemoteTransport` is an internal placeholder with no founder-facing controls.

## Founder-Run Manual GUI Acceptance

The following acceptance step **must be performed by a human with a macOS display** (not verifiable headlessly):

1. Build the app
2. Start from no daemon → verify WKWebView loads the HappyRanch UI after health probe
3. Kill the managed daemon → verify state transitions to `crashed` + diagnostics available
4. Start an external daemon first, then launch the app → verify it attaches as `externalRunning`
5. Export diagnostics bundle → verify no raw tokens or secrets are present
