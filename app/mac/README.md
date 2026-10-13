# HappyRanch Mac app

The Swift app supports a development checkout and an internal bundled candidate
with frozen daemon/CLI, web assets and a Go tsnet archive. Bundled release
verification is still pending. Candidates are unsigned; no public download,
Gatekeeper acceptance, signing, notarization or automatic update is provided by
these scripts. Bundle identity stays `ai.happyranch.HappyRanchApp`.

## Development

Xcode 16 / Swift 6 and a macOS build host are required. From `app/mac`, use
`swift build` and `swift run HappyRanchApp`. Existing development weak tsnet
stubs remain available when no Go archive exists. Those stubs never satisfy
release verification. Retained native tests are separate from automated
lint/static merge checks.

## Internal candidate build

Use a clean, independently reviewed full commit and tree. Version/build 1.0.1,
arm64 and minimum 14.0 are candidate inputs, not compatibility acceptance.
Build prerequisites are native arm64 macOS, Xcode 16 / Swift 6, Go compatible
with the checked-in module graph (1.23.1 or newer), CGO/SDK, Python 3.12–3.14,
uv, Node 24 and npm. Dependencies stay locked. The script records effective
toolchains; missing tools or evidence fail explicitly.

```bash
sha="$(git rev-parse HEAD)"
tree="$(git rev-parse HEAD^{tree})"
bash app/mac/scripts/build-app.sh --version 1.0.1 --build 1.0.1 \
  --source-sha "$sha" --source-tree "$tree" --arch arm64 \
  --output-dir "/absolute/owned-parent/new candidate"
```

The output parent must already exist, be owned by the current user and have no
symlink ancestors. The leaf must be absent and outside the source and Desktop.
No output overwrite or implicit Desktop copy exists. Paths with spaces are
supported. Builds export the pinned tree to an invocation-owned disposable
checkout. Go/archive, Python environment, web, Swift, icon and caches stay there;
no existing debug/archive/dist output is reused. Failed stages retain scratch
and diagnostics, exit nonzero and produce no successful build receipt. The
venue owner disposes of only that exact run after retaining evidence.

Success requires a real release executable, all eleven strong tsnet exports
bound to fresh archive members, native component inspection, all 29 exact
resources, exact frozen catalog JSON, notice closure and ZIP reconciliation.
An unchanged linker may fail to extract Go members in the presence of weak
stubs. A named `TSNET_STRONG_LINK_UNPROVEN` returns the missing exports and map;
Package.swift is outside this correction's authority.

Notice collection retains original installed texts and actual Analysis/Go/web
input mappings. Native redistribution, embedded bootloader exception, emitted
web/static-Go closure and asset attribution remain unresolved until independent
artifact reconciliation. This currently prevents complete-release finalization;
a staged app and diagnostic inventories are partial evidence. See
[packaging instructions](../../packaging/README.md) and
[notice dispositions](../../packaging/THIRD_PARTY_NOTICES.md).

## Runtime prerequisites and support limits

Frozen help/startup/catalog should require no developer checkout, uv, Python,
Go or npm; actual Mac execution is pending. Provider operations separately
require externally installed, authorized claude/codex/opencode/pi CLIs or a
capability-registered custom profile with its admitted adapter and executable
binding. Repository operations require git. File-backed Python skill helpers
require an external compatible Python environment. The shipped skill-local
`uv --project` shim is development-only. Optional installed Tailscale CLI/socket
integration is distinct from linked tsnet; neither linkage nor packaging proves
remote/network operation.

Cleanup resources rely on Linux `/proc` process/namespace/cgroup observation;
Mac cleanup capability remains unverified and platform dependent. The verifier
hashes helper bytes and calculates the ordinary bundled root, with
`ordinary_reader_execution=UNEXECUTED`; it never imports runtime, constructs
Settings or executes guards/cleanup. Managed catalog success is separate.

## Independent native and GUI acceptance

The manual/tag-only workflow is the existing internal native venue. The maker
does not dispatch it. QA records one manager-authorized reviewed-head run and
its exact inputs/run ID before observing it; uncertainty never triggers a
competing build. A hosted macOS 15 result cannot prove macOS 14 support, GUI
acceptance or signing trust. Intel/universal support is not claimed.

Use an existing manager-named disposable real account/VM with a display and no
checkout/interpreter fallback. Initially no daemon, PID/port files, runtime
registry, app-support home or defaults exist. Relocate the app to a path with
spaces, open normally, select Home and press Start. Within 60 seconds record
the app-owned child PID/start identity, actual bundled executable/hash,
app-support cwd/home, bundled web/asset hashes, assigned loopback port and
visible SPA. External prestarted-daemon discovery cannot substitute. Normal
Quit must terminate that same child/bootloader lineage and its listener within
15 seconds, confirmed twice one second apart. Failure stays failure; any later
harness teardown is separately authorized and limited to proven owned identities.

For synthetic replacement, create an empty valid runtime using the bundled
CLI against that managed daemon, snapshot valid `home` role preference,
unrelated defaults, registry and inert data hashes, quit, replace only the
isolated app, then repeat Start/quit. Bundle/defaults identity and data survive;
current launch writes the real account's app-support HappyRanchDaemonHome.
Preservation of an arbitrary stale custom home is not promised.

A real minimum-OS launch, trust inspection and quarantined install remain
separate missing assertions. Signing needs an authorized custodian and exact
artifact authority. Preserve distinct unsigned-app, signed-app, submission ZIP,
notary UUID/log, stapled-app and final ZIP receipts. Parent TASK11196 owns public
admission and anonymous download verification; local matching never sets
`available=true` or an approved URL. No Gatekeeper-bypass installation procedure
is supplied. See accepted TASK11214 C01–C18 for the full independent contract.
