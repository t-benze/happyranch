# Packaged connection shell (slice 1a)

From `web/`, use existing dependencies only:

```sh
npm run build:connection
npm run lint
npm run typecheck
./node_modules/.bin/vite preview --config connection/vite.config.ts --host 127.0.0.1 --port 4179 --strictPort
```

This independent TypeScript entry emits only `dist-connection/`. The runtime
SPA, daemon routes, native loaders and packaging are unchanged. No connection
lifecycle controls ship here. Native integration and custom-scheme admission
are slice 1b; lifecycle screens are slice 4. This shell reads status only.

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
request/result correlation and JSON-string delivery. No native handler, platform
transport or authorization is implemented by this web-only slice.

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
