# Packaged connection shell (slices 1a / 1b-LINUX)

From `web/`, use existing dependencies only:

```sh
npm run build:connection
npm run lint
npm run typecheck
./node_modules/.bin/vite preview --config connection/vite.config.ts --host 127.0.0.1 --port 4179 --strictPort
```

This independent TypeScript entry emits only `dist-connection/`. The runtime
SPA and daemon routes are unchanged. No connection lifecycle controls ship here.
Linux native integration and packaging are slice 1b-LINUX below; Mac integration
is pending. Lifecycle screens are slice 4. This shell reads status only.

`resource-manifest.json` is a literal version-1 object with `entry: "index.html"`
and a `resources` array of exact relative `path`, UTF-8/file `bytes` and SHA-256
`sha256`. It inventories HTML, JS, CSS, a local font and its license; it excludes
itself. The native loader must validate the manifest, pin the entry and serve
only those literal resources. No arbitrary path resolution is authorized.
The exact spec §2 CSP is embedded in HTML and must also be enforced natively.
`base: './'`, disabled module-preload support and `assetsInlineLimit: 0` avoid
absolute daemon paths, a preload network shim and embedded asset data.

## Import audit

`index.html` → `src/main.ts` → `bridge.ts`, `protocol.ts`, `strings.ts`, CSS.
`protocol.ts` imports only `protocol/native-connection-bridge.v1.json`.
There are no runtime imports from `web/src/`, React, App, router, auth/bootstrap,
API/data providers, analytics or the runtime feature-flag resolver. No network,
clipboard, credential, storage, navigation or generic execution API is used.

Build-only reuse: `vite.config.ts` reads eight semantic declarations from
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
advertised role; no role or capability inferred from platform alone. Native
withholds Linux saved-access capabilities until the Founder Q3/persistence slice.

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
Linux `client.status` returns a closed unavailable error (slice 3a is pending).
No status event source exists in this helper seam, so this slice emits no events;
`on` accepts the two fixed names and returns an idempotent unsubscribe. No invite,
credential, selection or mutation call is implemented.

Standalone invocation against an already-built/staged prefix (no daemon start):

```sh
/usr/bin/python3 -I /absolute/prefix/share/happyranch/desktop/connection_window.py --prefix /absolute/prefix --locale en
# --locale zh-CN exercises the Chinese view and unavailable notice.
/usr/bin/python3 -B packaging/linux/desktop/test_connection.py
/usr/bin/python3 -B packaging/linux/test_build_deb.py DebPackageTests -v
python -m py_compile packaging/linux/desktop/connection_contract.py packaging/linux/desktop/connection_window.py packaging/linux/desktop/happyranch_window.py
bash -n packaging/linux/build-payload.sh packaging/linux/build-deb.sh
```

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
