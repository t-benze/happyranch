# HappyRanch Mac app

The Swift app supports a development checkout and an internal bundled candidate
with frozen daemon/CLI, web assets and a Go tsnet archive. Bundled release
verification is still pending. Candidates are unsigned; no public download,
Gatekeeper acceptance, signing, notarization or automatic update is provided by
these scripts. Bundle identity stays `ai.happyranch.HappyRanchApp`.

## Development

Xcode 16 / Swift 6 and a macOS build host are required. From `app/mac`, use
`swift build` and `swift run HappyRanchApp`. Existing development weak tsnet
stubs remain available when no Go archive exists. When the freshly built
archive exists, CTsnet exposes the unchanged header without compiling stubs;
Darwin `-force_load` selects that exact archive with separate path arguments,
including paths with spaces. Darwin `-u` roots each unchanged ABI export against
SwiftPM release dead stripping. All eleven exports still require independent
strong-definition and archive-member verification. Retained native tests are
separate from automated lint/static merge checks.

## Internal candidate build

Use a clean, independently reviewed full commit and tree. Version/build 1.0.1,
arm64 and minimum 14.0 are candidate inputs, not compatibility acceptance.
Build prerequisites are native arm64 macOS, Xcode 16 / Swift 6, Go compatible
with the checked-in module graph (1.23.1 or newer), CGO/SDK, Python 3.12–3.14,
uv, Node 24 and npm. Dependencies stay locked. The script records effective
toolchains; missing tools or evidence fail explicitly.

The web lockfile selects a checked-in local `react-remove-scroll-bar` derivative
from immutable upstream `8ca9ba5ea52de03308fe8ced94f7b159a44d28ff`, version
`2.3.7+upstream.8ca9ba5ea52d`, through a same-package file dependency/override.
The git archive source export includes its tarball, original source/LICENSE and
explicit [preparation/check script](../../web/vendor/react-remove-scroll-bar/README.md).
The build's ordinary npm ci installs regular package files and the original
notice without compiling this package or downloading upstream build tooling.
Existing Radix/react-remove-scroll consumers and notice attribution remain.
This is source preparation, not approval to replace the separately pinned
native-run source or consume an allocation. Six actual candidate closures
remain UNEXECUTED/UNMET; native build, macOS/WKWebView behavior, minimum OS,
install, trust and release acceptance remain separate.

The existing hosted workflow installs exact native arm64 Go **1.23.12** with
`actions/setup-go@b7ad1dad31e06c5925ef5d2fc7ad053ef454303e` (v7.0.0), with
shared setup caching disabled. Before packaging it logs PATH, the resolved Go
executable, version, GOHOSTOS/GOHOSTARCH and GOTOOLCHAIN, and refuses anything
other than that version on Darwin/arm64 with `GOTOOLCHAIN=local`. The latter
applies to the entire job, including initial queries, preventing automatic
toolchain substitution. Actual compilation still uses the invocation-owned
Go caches and `-mod=readonly` against the unchanged module graph.

This is a conservative bootstrap choice, not proof that any Go version above
the module's 1.23.1 directive is compatible. Tailscale v1.78.0's
[pinned upstream toolchain](https://github.com/tailscale/tailscale/blob/v1.78.0/go.toolchain.rev)
identifies [Go 1.23.3](https://github.com/tailscale/go/blob/96578f73d04e1a231fa2a495ad3fa97747785bc6/VERSION).
Its pinned gVisor dependency uses Go runtime/ABI internals, including the old
[map type layout](https://github.com/google/gvisor/blob/64c016c92987/pkg/sync/runtime_unsafe.go);
[Go 1.24 changed the default map implementation](https://go.dev/doc/go1.24).
The selected 1.23.12 is the newest archived patch in that upstream-used minor
line, [available for Darwin/arm64](https://go.dev/dl/). Go 1.23 is outside the
[upstream maintenance window](https://go.dev/doc/devel/release); a maintained
compatible toolchain for this unchanged graph has not been established.
No Go/Tailscale dependency update or native compatibility acceptance follows.
The earlier hosted run failed with `BUILD_TOOL_MISSING: go` before output
allocation; success with this corrected configuration remains UNEXECUTED
pending a separately authorized independent QA run.

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
The shared freezer overrides inherited `PYINSTALLER_CONFIG_DIR` with a fresh,
validated cache under this run's freeze work path before invoking build tools.
PyInstaller `--clean` therefore stays within the invocation's allocations,
including for standalone freezer builds; external caches remain untouched.

Success requires a real release executable, all eleven strong tsnet exports
bound to fresh archive members, native component inspection, all 29 exact
resources, exact frozen catalog JSON, notice closure and ZIP reconciliation.
Each export requires one final strong definition and one unambiguous map object
whose origin exactly matches the full archive path and a unique `ar -t` member.
Both `archive(member)` and Apple's `archive[3](member)` syntax are accepted;
the optional numeric index does not prove an archive ordinal. Foreign paths,
prefixes/suffixes, malformed origins and duplicate object/member/symbol
associations refuse; all eleven observations and raw origins remain evidence.
App and ZIP inventories share relative-path string ordering and UTF8 JSON
encoding, including hidden members and symlink targets. Native inspection
resolves every loader/executable-relative rpath in its component context and
requires bundle containment through symlinks, even for unused paths. Absolute
system exceptions use only `/usr/lib` and `/System/Library` roots and children;
unknown tokens, escaping paths and spelling prefixes refuse with raw diagnostics.
A named `TSNET_STRONG_LINK_UNPROVEN` still refuses release finalization. Small
independent error/all-eleven symbol receipts survive separately from bulky nm
and link-map evidence. Failed builds retain available Analysis/CLI PYZ/native
union, toolchain and component provenance through the fixed diagnostic allowlist
and whole-file raw/gzip bounds described in the packaging instructions. Missing,
malformed, unsafe and over-cap inputs carry explicit loss records; no byte-tail
JSON is called a complete mapping. Native linkage acceptance remains pending.

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
