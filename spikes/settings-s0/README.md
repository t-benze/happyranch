# Settings S0 — spike, do not merge

Throwaway custom-scheme observations for TASK-18075 / THR-387. None of these
files is imported by the product or packaging. Do not ship this handler: it
omits the production manifest, CSP, admission and target-generation policy.
The native bridge here only logs synthetic probe observations.

`probe.js` uses the repository's installed `@microsoft/fetch-event-source`.
It records POST/PUT JSON equality, complete 10 MiB multipart file equality,
five one-second SSE writes/JS receipt times, abort after two events (2.5 s
timeout), download response/blob, history and persistent storage. Native stream
logs include write/stop times. Exit 0 means the observer exited, **not** that all
requirements passed. Read the JSON records, errors, timeout and download files.
`omitMultipart=1` is a diagnostic isolation mode after a crashing full run;
it never changes or supplies a PASS for the upload result.
`serializeMultipart=1` separately measures a workaround: preserve the browser's
multipart encoding/Content-Type but send its ArrayBuffer as Uint8Array. This
buffers the upload in JS; it is not evidence for direct Blob/FormData support.
`directOnly=1` isolates direct attachment navigation from the blob download,
so multiple-download policy cannot explain a missing direct download.

Build the unmodified product bundle using repository commands:

```sh
cd web
npm ci
npm run build
cd ..
```

## Linux

Requires GTK3, PyGObject, WebKit2 4.1 >=2.40 and a display. On the S0 host:
system `/usr/bin/python3`, WebKitGTK 2.52.6; extracted Xvfb 21.1.22 under
task scratch, software GL, `GSETTINGS_BACKEND=memory`, X11 display `:175`.
Run Xvfb and the finite commands together through the authorized jobs skill;
retain/stop/reap only that job's display process. No WebKit sandbox flags.

```sh
GSETTINGS_BACKEND=memory GDK_BACKEND=x11 DISPLAY=:175 LIBGL_ALWAYS_SOFTWARE=1 /usr/bin/python3 -X faulthandler -B spikes/settings-s0/linux_probe.py --data /tmp/settings-s0-storage
# Fresh process, same persistent profile; /settings receives dist/index.html.
GSETTINGS_BACKEND=memory GDK_BACKEND=x11 DISPLAY=:175 LIBGL_ALWAYS_SOFTWARE=1 /usr/bin/python3 -B spikes/settings-s0/linux_probe.py --data /tmp/settings-s0-storage --spa --path /settings
```

Use `--spa --path /` for the built SPA, or
`--path '/probe/page?omitMultipart=1'` to isolate other requirements after the upload
crash. `--host other` uses the same data manager and another scheme host to
check origin isolation. Repeat the app-host invocation after graceful exit to
check persistence. The built SPA currently has no standalone `/settings`
dialog route; shell rendering is distinct from implementing the new dialog.

Only Linux's exact health and THR-387 tail GETs forward to the real loopback
daemon, default port 8765 (`--daemon-port` overrides). Bootstrap is read in
native memory for the tail GET. No real bearer is logged or passed to JS;
only SSE event names/lengths are logged. No other real daemon API is reachable
through this probe. No remote address or dial exists.

## Mac — standalone feasibility probe

The separate Swift package targets macOS 14+, matching the product minimum.
It depends only on system AppKit/WebKit/CryptoKit. It never contacts a daemon
or a remote host; the shared page's daemon cases explicitly return 503.
It records `httpBody` versus `httpBodyStream`, handles incremental `didReceive`
and `stop`, and provides WKDownloadDelegate callbacks. A dedicated nonzero UUID
selects a persistent WKWebsiteDataStore; the suite uses a fresh UUID per build,
shared across its restarts, leaving the product store alone.
All file destinations go beneath the explicit probe download directory.

TASK-18408 / THR-387 seq94 permits at most three submissions to the existing
`happyranch-mac-native-probe` job with `SUITE=settings-scheme` and an exact pushed
`SOURCE_SHA`. This is a throwaway branch; no PR, merge or job config change.
The suite first records OS/architecture/Swift/SDK/launchctl facts and interprets
`mac/gui-preflight.swift` to observe NSApplication, WindowServer and a real
WKWebView HTML/JS round trip. No usable GUI ends the build cleanly with M1–M4
NOT RUN and forbids further attempts in that venue. Background/System alone
is not a failure if the actual WKWebView succeeds.

The suite then runs `swift build` with the supervisor suite's private HOME,
TMPDIR, SwiftPM and module-cache pattern. It retrieves only hash-pinned Node24
using the existing toolchain pins/transfer code, into the build scratch, then
runs `npm ci` and `npm run build` on a scratch copy of `web`. It does not install
host tools. Every invocation has a 50s external timeout (40s native timeout):

```sh
# After the suite's private scratch build, the executable takes these arguments.
"$PROBE" "$ASSET_ROOT" "$DOWNLOAD_ROOT/initial" probe
"$PROBE" "$ASSET_ROOT" "$DOWNLOAD_ROOT/restart" probe
"$PROBE" "$ASSET_ROOT" "$DOWNLOAD_ROOT/other" other
"$PROBE" "$ASSET_ROOT" "$DOWNLOAD_ROOT/root" /
"$PROBE" "$ASSET_ROOT" "$DOWNLOAD_ROOT/deep" /settings
"$PROBE" "$ASSET_ROOT" "$DOWNLOAD_ROOT/no-multipart" probe-no-multipart
"$PROBE" "$ASSET_ROOT" "$DOWNLOAD_ROOT/serialized" probe-serialized
"$PROBE" "$ASSET_ROOT" "$DOWNLOAD_ROOT/direct" probe-direct
```

The suite sets one fresh `SETTINGS_S0_STORE_UUID` per build, shared by all
invocations. Foundation's resolved home must match the private scratch home.
All caches/assets/downloads are under the build's TMPDIR, never `/tmp`.
`/settings` explicitly calls `WKWebView.reload()` after the first load; both
index responses and rendered DOM/assets must be observed. Serialized upload
and direct-download diagnostic modes preserve the original full-run result.
Question results in `evidence/probe.json` distinguish direct FormData from the
byte-serialized adaptation and blob from direct attachment. M3 records all five
native-to-JS wall-time differences, receipt intervals, abort-to-stop delay and
sends after stop; thresholds are stated in JSON. M4 compares first/restart/other
store values and verifies saved download size/SHA256. Observer exit alone
never supplies a verified question.

Linux-safe preparation commands (no Mac execution):

```sh
python3 -B scripts/mac_native/suite_settings_scheme.py --help
python3 -B scripts/mac_native/suite_settings_scheme.py --stub
python3 -B scripts/mac_native/probe.py --suite settings-scheme --output "$TMPDIR/linux-dispatch.json"
```

`--stub` exercises only the result parser and rejects absent evidence; it is
not a Mac result. The dispatcher reports Darwin-only skip on Linux. Python
syntax can also be checked via stdlib `ast.parse` without writing bytecode.
The suite imports only stdlib and the stdlib-only supervisor/toolchain modules;
it never imports HappyRanch runtime modules.

If full upload blocks later observations, run `probe-no-multipart` separately
and preserve the failure. Record macOS and WebKit versions (the app logs both),
OS/SDK/compiler, exit, native body count/hash, all five receipt times and native
stop delay, both downloaded files' contents, and first/second/other-origin
storage values. Repeat on the minimum supported macOS 14 and current macOS.
The second process must read `persistent-s0` for both stores; `other` must
initially read null. Five messages must arrive near their one-second writes,
before `didFinish`, and abort must trigger `stop` with no later native sends.
SPA modes load the real built bundle; `/settings` must still load index and
root-absolute assets. This transport probe does not implement the new dialog.

This is an observational tiny app, not a regression test suite. No production
seam, test flag, product edit, CI workflow or new dependency is introduced.
The Mac package cannot compile on the Linux maker host; only Jenkins evidence
can establish compilation or execution. Apple
API documentation supports handler/data-store availability; it does not prove
large Blob bodies, fetch buffering/cancellation or custom-origin persistence.
