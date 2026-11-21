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

The original three authorized submissions are spent (builds 49–51). Of the two
additional THR-387 seq101 runs, build 52 spent one: all fourteen invocations
stopped at `nsapplication-run` before the launch callback, while GUI preflight
succeeded. S0-MAC-2b repairs startup with **zero builds in this source leg**.
One conditional Mac run remains, only after an independent code_reviewer
APPROVE of the exact new repair SHA. No PR, merge or job configuration change
belongs to this spike.

The suite first records OS/architecture/Swift/SDK/launchctl facts and interprets
`mac/gui-preflight.swift` to observe NSApplication, WindowServer and a real
WKWebView HTML/JS round trip. No usable GUI ends the build cleanly with M1–M4
NOT RUN and forbids further attempts in that venue. Background/System alone
is not a failure if the actual WKWebView succeeds.

Both Swift entry points keep synchronous AppKit startup inside
`MainActor.assumeIsolated`, which checks the main executor before accessing the
actor-isolated probe. Compilation errors leave GUI availability unknown.

The executable now mirrors the successful preflight: `.prohibited` activation
policy, a retained hidden window, and synchronous `Probe.start()` before
`NSApplication.run()`. `applicationDidFinishLaunching` only logs its arrival;
no check waits for it. Startup heartbeats bracket probe/directory/application
creation, activation-policy selection (including its return value), delegate
assignment, window creation/attachment and the direct `probe-starting` /
`probe-started` call. `check-started` still marks the selected operation before
its load request. A scheduled `main-queue-ready` marker distinguishes queue
progress from a later stall inside `run()`. There is no second setup attempt:
it could not recover a blocked main thread and would risk duplicate loads.
These source changes do not establish native success or an AppKit root cause.

The suite then runs `swift build` with the supervisor suite's private HOME,
TMPDIR, SwiftPM and module-cache pattern. It retrieves only hash-pinned Node24
using the existing toolchain pins/transfer code, into the build scratch, then
runs `npm ci` and `npm run build` on a scratch copy of `web`, with its imported
`protocol/native-connection-bridge.v1.json` at the same relative path. It does
not install host tools. The Mac-specific `mac/probe.js` runs only the selected
check; it does not await storage, uploads or unrelated daemon cases first.
The shared Linux page is unchanged. Fourteen separate invocations run in order:
M1 history, root SPA, deep SPA/reload; M4 first/restart/other-origin storage;
M2 POST, PUT; M3 stream, abort; M4 blob download, direct download;
M2 FormData upload, serialized upload. Each writes a separate receipt even if
an earlier check hangs. `CASES` in the suite is the invocation manifest.

Each invocation receives an event file in the build's private scratch and uses
a **25s inner watchdog / 30s outer timeout**. The watchdog is scheduled on a
background queue before creating NSApplication, so main-thread hangs cannot
starve it. Success emits `check-finished` / `orderly_exit` and calls `exit(0)`;
it does not wait on AppKit termination negotiation. A watchdog emits
`watchdog_fired` with its last phase and exits 124. Timeout sends TERM to the
process group, drains for 1s, sends KILL to the group, drains up to 1s and reaps
for up to 1s. A timed-out invocation and its affected question are
`timeout_partial`, with all available evidence retained. Exit 0 alone still
cannot verify a question. A question with a timed-out alternative remains
`timeout_partial`, with successful sub-observations preserved separately.

Build 51 receipts: GUI HTML/JS round trip 2.938s, release Swift build 5.225s,
npm ci 58.792s, SPA build 12.929s, then eight probes at 50.012–50.014s;
whole build 518.383s. There is no successful native M-check timing yet. The 25s
watchdog is a diagnostic bound: about 8x the observed GUI startup, covering the
5s stream, 2.5s abort fallback, 7s SPA render/reload wait and 4s download settle
(the checks run independently). The outer 30s allows 5s watchdog/exit margin.
Fourteen worst-case invocations plus TERM/drain/reap use <=462s. The unchanged
1200s suite deadline clips preparation/commands; the existing 90min Jenkins
limit and log caps stay unchanged. These bounds are not evidence that all
checks complete within 25s on Mac.

Example after the private build (same store UUID for persistence invocations):

```sh
"$PROBE" "$ASSET_ROOT" "$DOWNLOAD_ROOT/initial" probe storage "$TMPDIR/events-initial.jsonl" 25
"$PROBE" "$ASSET_ROOT" "$DOWNLOAD_ROOT/restart" probe storage "$TMPDIR/events-restart.jsonl" 25
"$PROBE" "$ASSET_ROOT" "$DOWNLOAD_ROOT/other" other storage "$TMPDIR/events-other.jsonl" 25
"$PROBE" "$ASSET_ROOT" "$DOWNLOAD_ROOT/stream" probe stream "$TMPDIR/events-stream.jsonl" 25
```

Events are JSONL written with FileHandle write+synchronize after **every line**;
stdout/stderr are unbuffered and explicitly flushed. Heartbeats cover process
start, AppKit/store/WebView setup, handler registration, load request, handler
start/body-read/body-finish/stop, all implemented navigation callbacks, JS
messages, check start/finish and exit. Handler records include path, method,
body bytes and SHA256. A body stream's size/hash is null at entry until read;
`body-read-start` then `handler-body` exposes a blocking read honestly.
Native events are bounded at 8192 bytes/line and 256KiB/file (overflow exits 70).
Python hashes/counts full stdout/stderr while retaining <=1024-byte head and tail
per stream, plus <=24KiB of parsed events per invocation and the last event.
Counts/digests and truncation flags survive timeout; malformed/torn JSONL lines
are counted. Evidence is embedded in `probe.json` before scratch cleanup; the
section is capped at 768KiB when indented (below the existing 1MiB console cap). Raw scratch files are not separate archived artifacts.

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
# TMPDIR must name an existing private non-/tmp runtime task-tmp directory.
python3 -B spikes/settings-s0/mac/verify_timeout.py
# Optional pre-fix proof (expected exit 1, lost partial stdout):
python3 -B spikes/settings-s0/mac/verify_timeout.py --before-source "$OLD_SUITE_SOURCE"
node --check spikes/settings-s0/mac/probe.js
python3 -B scripts/mac_native/suite_settings_scheme.py --help
python3 -B scripts/mac_native/suite_settings_scheme.py --stub
python3 -B scripts/mac_native/probe.py --suite settings-scheme --output "$TMPDIR/linux-dispatch.json"
```

`--stub` exercises only the result parser and rejects absent evidence; it is
not a Mac result. The dispatcher reports Darwin-only skip on Linux. Python
syntax can also be checked via stdlib `ast.parse` without writing bytecode.
The suite imports only stdlib and the stdlib-only supervisor/toolchain modules;
it never imports HappyRanch runtime modules.

A full upload hang cannot block the separately launched route/stream/storage
checks. Preserve all invocation receipts. Record macOS and WebKit versions (the app logs both),
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

The Linux simulation starts real synthetic subprocesses: TERM-cooperative and
TERM-ignoring/group descendants, watchdog exit, success after timeout, and large
streams/events. It checks independently expected bytes/digests, signals, last
event, truncation and per-question isolation. Its optional pre-fix proof compiles
the old suite's actual nested command function. It does not test AppKit/WebKit
or compile Swift; those remain NOT RUN on Linux.
