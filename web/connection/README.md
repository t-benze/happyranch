# Packaged connection shell (slices 1a / 1b-LINUX / 1b-MAC)

From `web/`, use existing dependencies only:

```sh
npm run build:connection
npm run lint
npm run typecheck
./node_modules/.bin/vite preview --config connection/vite.config.ts --host 127.0.0.1 --port 4179 --strictPort
```

This independent TypeScript entry emits only `dist-connection/`. The runtime
SPA and daemon routes are unchanged. Client lifecycle controls only request
closed native commands; native owns every effect.
Linux native integration and packaging are slice 1b-LINUX below; Mac client-only
integration and bundle assembly are slice 1b-MAC below. Client lifecycle screens are
slice 4a and Linux host lifecycle screens are slice 4b (both below).

`resource-manifest.json` is a literal version-1 object with `entry: "index.html"`
and a `resources` array of exact relative `path`, UTF-8/file `bytes` and SHA-256
`sha256`. It inventories HTML, JS, CSS, a local font and its license; it excludes
itself. The native loader must validate the manifest, pin the entry and serve
only those literal resources. No arbitrary path resolution is authorized.
The exact spec §2 CSP is embedded in HTML and must also be enforced natively.
`base: './'`, disabled module-preload support and `assetsInlineLimit: 0` avoid
absolute daemon paths, a preload network shim and embedded asset data.

## Import audit

`index.html` → `src/main.ts` → `bridge.ts`, `client.ts`, `host.ts`, `protocol.ts`, `strings.ts`, CSS.
`protocol.ts` imports only `protocol/native-connection-bridge.v1.json`.
There are no runtime imports from `web/src/`, React, App, router, auth/bootstrap,
API/data providers, analytics or the runtime feature-flag resolver. No network,
clipboard, credential, storage, navigation or generic execution API is used.

Build-only reuse: `vite.config.ts` reads seventeen literal semantic declarations from
`src/design-system/tokens/tokens.css` into an external CSS asset. It does not
import that stylesheet's Tailwind graph or other runtime UI. The local Hanken
Grotesk font comes from `public/fonts/`; the OFL is copied alongside the assets.
License metadata contains upstream URLs as text; executable HTML/CSS/JS has no
remote URL or resource. Browser UI rendering uses DOM text nodes, never HTML
interpolation. There are no inline styles/scripts or eval.

## Facade and schema

The native loader injects only `window.happyranchConnection.call(command, data)`
and `.on(eventName, handler)` into its admitted top-level document. Both payloads
and results are strings; failures reject/throw closed error JSON **strings**.
The app does not inject a native channel, infer authorization or create browser
transport fallbacks. Hello has a one-second UX deadline. Missing, malformed,
late or incompatible hello leaves the document empty/feature hidden. A compatible
hello with no usable status capability renders only unavailable/future-CLI copy.
A compatible fake facade can render a shell; §9 explicitly treats this as UX,
not native authority. There is no shipped fake-facade switch or URL flag.

The v1 definition is the language-neutral JSON schema under `protocol/`.
`commands` and `events` are lookup annotations for their closed payload schemas.
`maxUtf8Bytes` is a mandatory byte-length assertion (JSON Schema `maxLength`
alone counts characters). The `utc` format asserts a real UTC timestamp in
`YYYY-MM-DDTHH:mm:ss[.sss]Z` form. Labels/phrases are at most 256 UTF-8 bytes,
exclude controls/bidi overrides, and computer refs are 1–64 ASCII letters,
numbers, underscores or hyphens. Request IDs have the same alphabet/bound.
Duplicate JSON keys, unpaired surrogates, depth over 16, extra/missing keys,
unknown nested fields, noninteger sequence values and oversized payloads fail
closed. Requests are at most 4 KiB including the internal envelope; inbound
strings/envelopes are at most 32 KiB. Native must enforce these independently.

Hello is exactly `{version,platform,roles,capabilities,observationLimits}`:
`version` is 1; `platform` is `linux|mac`; `roles` is a unique host/client list;
`capabilities` is a unique-by-command list of `{command,availability}`;
`availability` is `available|unavailable|unsupportedVersion`. Omitted commands
are unavailable. `observationLimits` is exactly `{host:[],client:[]}`, where
arrays contain snapshot field names whose observation is limited/unsupported.
Unknown/stale values still use the snapshot's explicit unknown/null semantics.
For both status replies and events, the shell presents runtime availability only
when the snapshot observation is `known` and that role's limits include neither
`runtime` nor `observation`. Stale observations show explicit stale status;
unavailable or limited runtime/freshness observations show unavailable status,
and an unknown runtime stays unknown. Freshness is native-owned; the web adds
no timestamp-based expiry rule.
Mac may advertise only client roles/commands. Available commands require their
advertised role; no role or capability inferred from platform alone. Linux
saved-access capabilities come from the slice 8b Secret Service owner.

`Connection` validates all facade strings, bounds reads to eight, serializes
ordinary calls that can change state (including NextDevice), and leaves the
cancel path reachable while ordinary mutations are pending. Native owns prompt
serialization, deadlines, attempt targeting, duplicate IDs, 64-mutation outcome
retention and safe-point cancellation. Only hello has a web timeout; the web
never mistakes a local timeout or transport loss for a confirmed cancellation.

Each role has a single native subscription shared by local listeners. It accepts
full validated snapshots with increasing positive sequence, treats malformed or
stale delivery as a closed failure, and unsubscribes idempotently. Native still
owns the one underlying per-view stream. Page teardown detaches listeners and
fences pending delivery; native must invalidate its generation on every §4
transition, including attempted navigation that JS cannot observe.

The internal request/reply/event codecs are module exports for later relay use,
not extra window APIs. They preserve fixed protocol/version, closed shapes,
request/result correlation and JSON-string delivery. The web entry supplies no native authorization; Linux owns the separate
transport and admission described below.

## Client screens (slice 4a)

`client.ts` renders the client role for Linux and Mac from closed native
projections only: `client.status`/`client.stateChanged` snapshots, action
results, closed errors and `client.health`. It shows idle/no saved, the saved
list (per-record Connect / Remove local access), validating, joining, pairing,
waiting for approval, saving, opening, connected, stopping and failed, with a
reachable Cancel in every in-flight stage. Connected is shown only when the
native snapshot says `connected`. After every action the view rereads
`client.savedComputers` and `client.status` independently; it never edits lists
optimistically. Discovery `busy` during an active attempt is expected, not a
failure; any other reread failure is shown beside the original action outcome
(same command, record and effect), never instead of it. Every async status read
and role load is bound to the role selection and a monotonic
native-observation revision: a reply overtaken by a native event, a newer read,
a completed action or a role change is ignored, so the newest native state wins.
`client.health` reads are not fenced by that shared revision: each read carries
its own role-selection and request marker, and a snapshot that leaves
`connected` invalidates any outstanding health reply. Native confirmations own Connect, Remove, Disconnect and Cancel;
dismissing one returns `effect:none` and the view says nothing changed.
Unknown effects (including transport loss after dispatch) show “couldn’t
confirm” with Check again, never “nothing changed”. A recovery `explicitRetry` or
`nativePermission` retries the same command with the same `computerRef`, only
while that record is still listed.

The view never renders invite bytes, fingerprints, bearers or the snapshot's
`comparisonPhrase`; the comparison stays in native windows. Health fields that
are `unknown`, unavailable or listed in `observationLimits.client` show Unknown;
an unreadable or limited saved list is shown as unreadable, not empty. The
“Secure access” step is done only for native `savedAccess: saved`; otherwise it
reads skipped (`none`), not saved (`notSaved`/`needsPermission`) or not
confirmed. Once `notSaved`, `needsPermission` or `unknown` is observed at opening
or connected, the save warning stays visible through saving/opening/connected
transitions until `saved` is observed, with Retry save while connected; the
persistent native-chrome banner remains native. Invite deadlines
show an absolute local time only in validating..waitingApproval, with no
countdown or expiry announcements. Commands whose capability is absent are
hidden, with one “update the app” note. A pending ordinary action marks other
actions `aria-disabled` (focus stays put); Cancel stays enabled.

Accessibility: one polite live region announces stage and action outcomes —
confirmed, failed or unknown — keyed by native ref and operation/outcome
identity (not every snapshot and not language switches). App-generated saved
names in retained outcomes/recovery re-render in the current locale; focus is restored to
the same control after a native re-render, or moved to the card heading when the
stage changes and focus was lost. Visible focus uses the shared outline. The
connection shell has no dark theme; light only.

## Host screens (slice 4b)

`host.ts` renders the Linux host role from the closed v1 host commands/events
only: `host.status`/`host.stateChanged` snapshots, `host.enable`, `host.turnOff`,
`host.createInvite`, `host.approveDevice`, `host.rejectDevice`, `host.nextDevice`
and `host.revokeDevice` results, and closed errors. Every command takes `{}`; the
web never supplies a device identity. States: off (Turn on), setting up, ready
(Add a device), ready with the runtime offline/restarting, invite active (local
expiry time, no countdown), last invite expired/used, pending device request
(Approve / Reject request the native system prompt), devices with access, the
helper-selected device (Select next device / Revoke selected device), paused,
failed (needs repair), and unknown. Unknown setup, an unavailable observation or
a limited `setup`/`observation` field shows “not confirmed” with Check again, never
Off. A null or limited device list is shown as unreadable, not empty. Devices
carry no ID and labels can repeat, so list rows have no actions or selection
marker; the selection is shown separately from the native `selected` record.

Turn off and Revoke first open an in-view confirmation (focus starts on Cancel;
Cancel returns focus to the opener), then native shows the system prompt. Native
owns the exact target and every effect. The view never renders invite bytes,
fingerprints or any `comparisonPhrase`; the invite is displayed and copied only
in the native invite window. Action outcomes are honest: an unknown effect shows
“couldn’t confirm” with Check again (never “nothing changed” and never full Off);
`effect: none` says nothing changed; Turn off and Revoke list host denial, owned
remote services/state and network cleanup separately, and “Remote access is off”
is the title only when the native snapshot says `off`. A successful Create invite
is reported as an active invite only when native reports `issued`. Recovery
(`explicitRetry`/`nativePermission`) retries the same command only while the
same pending request or selected device is still current; otherwise it says the
record changed. After every action the view rereads `host.status`; it never edits
lists optimistically. NextDevice's returned snapshot is applied under the same
fence. Status reads and role loads use the same role-selection and
native-observation revision fence as the client view, so stale replies never
override newer native state. Missing capabilities hide their control with one
update note; a plain browser still hides the feature. en/zh-CN, keyboard order,
focus restoration, `aria-disabled` while an action is pending and one polite live
region (keyed by phase, record and outcome identity, silent on language change)
match the client view.

v1 has no host command for: cancelling or replacing an issued invite (H09), so
Add a device is not offered while an invite is active and the view discloses that
closing the native window does not cancel it; replacing a recognized failed setup
(H04/X02); observing or refreshing a changed home address (H20); retrying policy
renewal (H19); or cancelling host setup from the web (setup is cancelled in the
system prompt). These render nothing fake and remain slice 2 / TASK-16779 work.

## Linux native loader (slice 1b-LINUX)

The native titlebar **Connection / 连接** button opens `connection_window.py`
independently of daemon startup, failure or runtime-page loading. The existing
remote-access card remains during development. No handler or user script is
added to the daemon-served runtime view.

`build-payload.sh` runs the existing connection build and copies its output,
manifest, protocol and native modules beside the desktop window. Payload and
Debian validation use the packager's trusted `connection_contract.py`, never
code from an input tarball. Both reject missing/extra files, wrong sizes/digests,
symlinks and nonregular assets. Runtime reads repeat no-follow descriptor walks
and byte/hash checks. Only manifest-listed literal URLs at
`happyranch-shell://connection/` are served; the manifest itself is not served.
The loader never reads `HAPPYRANCH_WEB_DIST`, a daemon URL or a caller asset path.

Each connection view owns an ephemeral WebContext, UserContentManager,
asset-only scheme, exact §2 CSP response header and manifest-only network filter.
Resource MIME types are set in both WebKit metadata and Content-Type headers,
so nosniff admits the packaged stylesheet without content sniffing.
Navigation, popups, reloads, redirects, load failure and process loss invalidate
the generation, unregister the handler and disconnect its dedicated bus caller.
Opening Connection again creates a fresh view; no automatic re-admission/replay.

Runtime admission checks API availability, the exact entry/origin, an empty frame
tree, named-world channel presence, page-world channel absence and structured
execution before constructing a fresh view with TOP_FRAME scripts. Isolation is
constructed by the CSP (including frame-src none and no inline/eval scripts),
manifest-only filter and native refusal of every subsequent navigation, including
subframes, about:blank and srcdoc. Each refused attempt tears down the bridge.
The isolated relay also observes embedded-frame insertion (including a frame
removed in the same task) and frame CSP violations: CSP may refuse a load before
native decide-policy runs. It stops relaying and sends rejected non-string input
on the existing channel, which can only revoke the current native generation;
it adds no command or frame/origin authorization claim.
The probe does not create its own forbidden frames or mistake a parent-window
read for child execution. TOP_FRAME and the top-level URI alone are insufficient.
Native message admission also requires the current manager, active generation
and pinned view URI; frame admission depends on this complete construction.

WebKitGTK 2.52.6 can enable the read-only bridge with this construction. Native
qualification must separately exercise child/parent access and non-pinned
navigation against the loaded view. Missing APIs or failed admission still
disable the **whole** bridge and show a bilingual native notice, explicitly
saying the connection CLI is not available yet. No dependency-floor increase,
file URL or network-command fallback is used. This slice does not establish
minimum-engine, CLI or full lifecycle acceptance.

Only an admitted fresh view receives the two-function facade and named-world
relay. Native independently validates duplicate-free JSON, closed command/args
schemas, 4 KiB requests, 32 KiB inbound replies, UTF-8 and depth limits. Hello
advertises Linux host and client roles, only `host.status` available, all mutations
unavailable, and limited observations. Status calls the existing helper `Status`
with zero inputs, no interactive authorization, on a dedicated D-Bus connection.
Only bounded public label/phrase fields are projected; fingerprints are omitted.
After HTML decoding, Linux host labels within 256 UTF-8 bytes are unchanged;
longer labels keep the longest whole-code-point prefix within 253 bytes plus `…`
(3 bytes), for devices, pending and selected. Only the public display projection
is truncated; stored labels and native identity are unchanged.
Linux `client.status` returns a closed unavailable error (slice 3a is pending).
No status event source exists in this helper seam, so this slice emits no events;
`on` accepts the two fixed names and returns an idempotent unsubscribe. No invite,
credential, selection or mutation call is implemented.

Standalone invocation against an already-built/staged prefix (no daemon start):

```sh
/usr/bin/python3 -I /absolute/prefix/share/happyranch/desktop/connection_window.py --prefix /absolute/prefix --locale en
# --locale zh-CN localizes native chrome; use Language for the web view.
/usr/bin/python3 -B packaging/linux/desktop/test_connection.py
/usr/bin/python3 -B packaging/linux/test_build_deb.py DebPackageTests -v
python -m py_compile packaging/linux/desktop/connection_contract.py packaging/linux/desktop/connection_window.py packaging/linux/desktop/happyranch_window.py
bash -n packaging/linux/build-payload.sh packaging/linux/build-deb.sh
```

## Mac native loader (slice 1b-MAC)

The native **Connection / 连接** menu opens a fresh separate `ConnectionWindow`
while the runtime is loaded, starting, disconnected or failed. Existing native
connection screens stay unchanged during development. The runtime WKWebView
receives no handler, facade, forwarder or reference to this view.

`app/mac/scripts/build-app.sh` runs `npm run build:connection` in its existing
isolated producer, stages the complete output outside the exported web source
census, and copies that output to
`HappyRanch.app/Contents/Resources/Connection/` before sealing. It reuses the
trusted source-side Linux `Assets` validator for exact inventory, sizes, hashes
and no-symlink checks. Manifest-listed files, including the font/license, are
preserved; `Resources/web/dist` stays separate. SwiftPM resources are unnecessary.
The native scheme handler repeats no-follow reads and SHA-256 checks and serves
only literal manifest URLs, with correct MIME and the exact §2 CSP header.

The view has its own WKWebViewConfiguration, non-persistent WKWebsiteDataStore
and WKUserContentController. Only the named `happyranch.connection.v1` world has
a WithReply handler; main-frame document-start scripts expose exactly call/on in
the page world. Every native request checks view/controller identity, main frame,
frame request, exact canonical security origin and generation. Initial hello
probes structured execution, exact JS origin/entry, an empty frame tree and page
world channel absence before capabilities dispatch. Native validates the closed
v1 command/args envelope independently, including duplicates, Unicode, depth 16,
4 KiB requests and 32 KiB replies. No data is interpolated into native JS source.

Only hello and client.status are implemented. Hello advertises Mac client only,
with no host role/command. Status hand-projects existing in-memory client phases;
it performs no discovery, Keychain read, health/network request or native action.
Only HomeInviteModel's verified UI-load phase yields connected/available; older
join/health signals stay opening/unknown. Saved access, route, observation time,
computers/ref, deadline and comparison phrase remain unknown/null and are listed
in observationLimits. No credential, trust record, invite, fingerprint, comparison
phrase or raw exception is serialized. All other schema commands are unavailable.
There is no clean unified model-event source in this slice: no events are wired
or advertised; on accepts the fixed names and returns idempotent unsubscribe.
Future event delivery requires structured callAsyncJavaScript arguments in the
admitted main frame/named world with a generation recheck.

Native navigation, redirects, popups, load failures, process loss, role/lane
changes and window close revoke admission and unregister the handler. Reopening
creates a fresh view. Failed prerequisites show bilingual native unavailable
copy with no action controls, explicitly saying the planned connection CLI is
not available yet. No file URL, remote origin or network-command fallback exists.

Focused native contract tests use the existing Swift venue on macOS:

```sh
swift test --package-path app/mac --filter ConnectionContractTests
```

Linux cannot compile AppKit/WebKit. The existing unsigned build-mac-app workflow
may supply compile/package evidence only under a slice-specific dispatch budget;
it does not execute these tests or GUI checks. Native custom-origin/CSP, frame
and popup refusal, daemon-state entry, process teardown and bilingual Mac
screenshots remain NOT RUN until exercised. Lifecycle/CLI/cutover and Mac client
qualification remain pending; no acceptance claim follows from source delivery.

## Verification boundaries

Use lint/static lanes and direct code/browser inspection with synthetic facades;
no retired unit suite, Product E2E run or new CI trigger. Check absent/incompatible
facades, Linux roles, Mac client-only, offline/unknown status, unavailable
capabilities, strict payload bounds, closed failures, event sequencing/unsubscribe,
and teardown. For the shell, inspect en/zh-CN at 390×844 and 1440×900; switching
language preserves mounted controls/focus and makes no native calls. No editable
invitation/credential fields exist. Screenshot facades do not qualify native
admission, prompts, custom-scheme CSP/origin/frame isolation, packaging, CLI or
lifecycle acceptance; those remain NOT RUN until their owning slices.
