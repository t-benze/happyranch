# HappyRanch Mac app

Current shipping standard and finite verification: [manifest-notice contract](../../packaging/manifest-notices.md).

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
with the checked-in module graph (1.23.1 or newer), CGO/SDK, bootstrap Python 3.12–3.14,
uv with Python install `--no-bin` support, Node 24 and npm. Dependencies stay locked. The script records effective
toolchains; missing tools or evidence fail explicitly.

The freezer requests **CPython 3.12.10** through uv into a newly reserved,
uid-owned private install root under freeze-work; it never falls back to the
ambient framework or creates global/user bin links. Sync and both no-dotenv
runs bind that base to the absent absolute exported-source/.venv, retain the
frozen local Darwin PyInstaller wheel, and disable subsequent downloads. The
strict four-field toolchain receipt describes the actual venv interpreter,
whose executable and base prefix must agree with the selected installation.

Before any uv command (including the early version read), both entries refuse
inherited UV_* selection/source/mirror/catalog/mode controls. The only exceptions
are cache (replaced), offline/download policy (honored by refusal when downloads
are prohibited) and progress. Mismatched VIRTUAL_ENV and inherited PYTHON*
controls refuse without exposing values. The wrapper validates its private
temp/cache ancestry and mode before use; standalone freezer temp/cache/install
allocations receive the same custody predicates before effects. Discovered
uv.toml overrides and unexpected project uv configuration refuse before install,
without suppressing the committed source table. See the complete
[freezer selection and allocation contract](../../packaging/README.md#frozen-mac-packaging).
No shared framework modes or external environments are repaired. Original
nonzero install/discovery/sync failures stop without ambient retry, with the
existing wrapper finish diagnostics and available=false retained.

This is a source repair, not an observed successful Python relocation. Managed
installer effects, unchanged external bytes, actual selection/failure transitions,
new-distribution notice originals/fallback eligibility/library ownership, native
app/archive/install/launch acceptance remain **UNVERIFIED**; engineering_manager
owns the risk of another fail-closed build/finalization refusal. Installed
original precedence, unsafe-original refusal, full fallback identity/hashes,
both EXE.python_lib checks, separate notice owners and final collector gate
are unchanged. No additional native build or distribution is authorized.

The web lockfile selects a checked-in local `react-remove-scroll-bar` derivative
from immutable upstream `8ca9ba5ea52de03308fe8ced94f7b159a44d28ff`, version
`2.3.7+upstream.8ca9ba5ea52d`, through a same-package file dependency/override.
The git archive source export includes its tarball, original source/LICENSE and
explicit [preparation/check script](../../web/vendor/react-remove-scroll-bar/README.md).
The build's ordinary npm ci installs regular package files and the original
notice without compiling this package or downloading upstream build tooling.
Existing Radix/react-remove-scroll consumers and notice attribution remain.
Native build allocation remains owned by the delivery root. Native build,
macOS/WKWebView behavior, minimum OS, installation, trust and release acceptance
remain separate from the manifest-notice shipping standard.

Web font source delivery retains unmodified Hanken Grotesk and Newsreader
variable TTFs plus their two full co-version original OFL texts under
`web/public/fonts/`. The pinned source/notice relations are in
[`font-assets.json`](../../packaging/notice-data/font-assets.json). The
existing Vite public-copy step emits all five files to `web/dist/fonts/`,
and existing app assembly copies that directory with the rest of dist to
`Contents/Resources/web/dist/`. Font bytes total 1,080,240 (+850,516 versus
the removed WOFF2 subsets), with another 8,796 bytes of notices before
transport compression. This source retention does not establish actual
app/archive custody, macOS 14 / WKWebView font loading or rasterization,
ICNS transformation evidence or native acceptance. Original notices are checked
under the current manifest standard; no whole-program closure is claimed.

The release wrapper now measures the complete selected Git/export public tree
(including hidden and unreferenced files), ordinary dist, and actual app copy.
Only existing basename `*.map` removals are exceptions. Unknown generated/other
files remain explicitly unattributed. Its private `scripts/asset_evidence.py`
retains the two unchanged OFLs at
`Contents/Resources/licenses/assets/{HankenGrotesk,Newsreader}-OFL.txt` and
returns three separate font component rows; both Newsreader rows share one
notice. Existing identical safe targets are preserved; conflicts persist as
failures. Installed-original Python/Radix precedence is unchanged.

The same helper prepares seven PNG originals into ten iconset members, then
runs the selected real iconutil once on Darwin/arm64 into new private
`scratch/icon-output/AppIcon.icns`. Exclusive publication to the app is a byte
copy, not another transform. Catchable interruption is deferred until the child
handle is owned; bounded cleanup preserves the first failure and actual exit
(or explicit unknown) and attempts each acquired resource independently.
Six one-shot local records and two bounded tool
streams bind explicit caller roots, candidate, run, tool and synchronous stage
tokens. The independent licenses reader reopens complete subjects before the
unchanged strict legacy license checks; it never reruns iconutil. See the
[packaging reader arguments and limits](../../packaging/README.md).

Portable synthetic-copy fixture results cover shipping helper operations only.
Actual native process capture, PYNOTICES append/token capture, full reader,
macOS14/WKWebView/install remain unexecuted. The former six sentinels and
literal False gate are retired by THR104seq260. Concrete failed original-text
rows still refuse. Source delivery grants no native execution, signing,
deployment or release authority.

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
  --web-tool-originals "/absolute/admitted-originals" \
  --output-dir "/absolute/owned-parent/new candidate"
```

The absolute originals directory must meet the [generated web input contract](#generated-web-custody-inputs-and-verification).

The output parent must already exist, be private (no group/other permission
bits), be owned by the current user and have no symlink ancestors. The leaf
must be absent and outside the source and Desktop.
No output overwrite or implicit Desktop copy exists. Paths with spaces are
supported. Builds export the pinned tree to an invocation-owned disposable
checkout. Go/archive, Python environment, web, Swift, icon and caches stay there;
no existing debug/archive/dist output is reused. Failed stages retain scratch
and diagnostics, exit nonzero and produce no successful build receipt. The
venue owner disposes of only that exact run after retaining evidence.

The hosted workflow atomically reserves a unique mode-0700 run directory under
`RUNNER_TEMP` using `mktemp -d`; it never changes the shared temp directory's
permissions. `web-originals` and the initially absent `output` are children of
that run directory. Step outputs carry these exact paths to preparation, build
and both artifact collectors. Success uploads only `output/HappyRanch-*.zip`,
`output/build-receipt.json` and `output/diagnostics/*.json`; failure uploads the
run's `setup-status.txt`, available `output/preflight.json` and
`output/diagnostics/bounded/*`. The status file reports the last entered setup
stage, not build success. If allocation fails before the root is published,
artifact collection is skipped and the workflow log is the diagnostic evidence;
no fallback glob scans shared temp. No candidate receipt is invented for early
setup or native-tool failures.

Portable layout verification (no compilation or native acceptance):
`python packaging/verify_mac_run_layout.py --scratch /absolute/new-fixture-root`.
It executes the workflow reservation shell and real preflight/bindings boundary,
including existing-destination, nonprivate-parent and symlink refusal. It also
exercises real directory/subject guards and the freezer process-root admission
helper with disposable fixtures, including role order, repeated equal paths,
first refusal, descriptor cleanup and uncaught Python stderr propagation.
Synthetic wrong-owner/formatter boundaries are separate from real filesystem
observations. Native build and hosted upload execution remain separately
allocated observations.

Directory guard refusals retain their original category and error text. Python
tracebacks can additionally show one `DIRECTORY_CONTEXT ` note containing compact
ASCII JSON: category, full held-descriptor octal `mode`, `uid`, the actual `owned`
predicate, process-root `role` (or null), truncation flags, offending `cursor` and
selected `root` (or null). Process roles are checked in order: `prefix`,
`base_prefix`, `stdlib`, `usr-bin`. An unsafe ancestor names that ancestor even
when the selected root is a safe child. The note is at most 768 ASCII bytes;
each path is at most 192 serialized characters including JSON quotes. Long paths
keep a whole-codepoint prefix plus `...` and an explicit truncation flag. The
note uses the already held stat observation; it performs no diagnostic stat or
permission repair. Ancillary formatting failures preserve the original refusal
and descriptor cleanup, but can leave the diagnostic absent.

These notes are traceback/stderr evidence only. Existing subject, `Session.fail`
and attribution strings do not persist them; whole-file collector caps can also
omit a log. Portable subprocess checks do not establish PyInstaller/spec,
`build.log` or archived diagnostic propagation. No native cause, Python relocation
fix, successful build, app or launch acceptance follows from the diagnostic.
Every root still requires the original ancestry, ownership, mode, no-follow and
identity checks, including repeated equal process roots before admission deduplication.

The shared freezer overrides inherited `PYINSTALLER_CONFIG_DIR` with a fresh,
validated cache under this run's freeze work path before invoking build tools.
PyInstaller `--clean` therefore stays within the invocation's allocations,
including for standalone freezer builds; external caches remain untouched.

Success requires a real release executable, all eleven strong tsnet exports
bound to fresh archive members, native component inspection, all 29 exact
resources, exact frozen catalog JSON, manifest notices and ZIP reconciliation.
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
input mappings. The shipping gate now checks resolved manifest membership,
explicit selections, original texts and separate asset owners. A complete
inventory can archive; concrete missing vendor originals still refuse with the
owner/path. This is not a per-byte/linker closure or legal-certification claim. See
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

The freezer operation observer uses the original-backed PyInstaller input and
bounded independent archive reader described in
[the freezer contract](../../packaging/README.md#freezer-operation-custody).
Darwin selects custody revision 2 with descriptor-relative snapshot writes and
ZIP metadata budgets enforced before allocation. Historical revision 1 evidence
remains attributed to its original input. The existing daemon freeze and app cp-R
execute once; observers retain their
actual generations and results. Preserve the full local scratch tree for the
independent reader. Removing intermediate subjects makes custody unavailable.

The strict `licenses` invocation additionally requires each of these original
caller-held tokens exactly once:

```sh
--freezer-context-sha256 "$FREEZER_CONTEXT" \
--freezer-inputs-sha256 "$FREEZER_INPUTS" \
--freezer-output-sha256 "$FREEZER_OUTPUT" \
--freezer-copy-sha256 "$FREEZER_COPY"
```

These tokens cannot be discovered from candidate diagnostics. The ordinary
wrapper retains them synchronously and supplies them alongside unchanged asset
expectations. Missing/foreign/partial observations refuse. Selected technical
relations and portable fixtures do not establish native/full-reader execution.
The fifth native allocation and exact-source review remain root-owned; the
manifest standard replaces the former six-closure prerequisite.

### Generated web custody inputs and verification

`build-app.sh` now additionally requires `--web-tool-originals
/absolute/admitted-directory`, containing exactly the fourteen lock-authenticated
original tarballs described in packaging/README.md. The directory must be
separate from source/output. Preserve it, the invocation-owned npm tree, scratch
and diagnostics until independent verification completes. The native workflow prepares this same pinned input directory; dispatch inputs
and allocation controls are unchanged. This source change is not an execution grant.

The wrapper performs normal npm ci in a subshell with umask 022 so authentic
package executables retain mode 0755. The parent mask remains 077 and the
previously created output, scratch and source directories remain private.
It then performs exact esbuild optimized-alias preparation,
then one ordinary npm build with a bound packaging Tailwind adapter and the
original-backed Vite CSS observation patch. The content policy includes pinned
web tests/stories and needs actual matched baseline/candidate CSS/UI evidence.
Plain split ES CSS with esbuild minification is the admitted profile; unsupported
imports/modules/source directives/URL forms refuse instead of silently extending
it. The prepared P/A binary paths are both revalidated throughout custody.
Both web readers require each asset-cache store to consume a read under the
same enclosing hook, preserving interleaved read parents. Hits require a stored
URL; incomplete read/store histories refuse.
Each CSS finalization consumes one pending minifier result under its own hook,
or none when disabled. Reused, duplicate and unconsumed minifier results refuse.

The final observer uses the fixed `web-completion.jsonl` leaf, never an extra
inherited FD. Python completes only the actual publish token after independent
full validation, exclusive write/fsync/close/readback, then echoes its exact
555-byte canonical line. TS admits only raw equality to the original direct
caller/context/preparation and actual published token. The wrapper independently
requires actual npm exit0 before accept/seal; later postbuild failure wins.
Existing markers/attempts remain evidence and cannot be deleted for retry.

The completion token and direct caller-held `WEB_CALLER`,
`WEB_CONTEXT`, `WEB_PREPARATION`, `WEB_BUILD`, `WEB_DIST` and `WEB_NOTICES` values
must survive from the genuine wrapper, alongside all existing asset/freezer
expectations. Standalone `verify-release.sh licenses` additionally requires
`--web-tool-originals "$WEB_TOOL_ORIGINALS" --web-caller "$WEB_CALLER" --web-context-sha256 "$WEB_CONTEXT"
--web-preparation-sha256 "$WEB_PREPARATION" --web-build-sha256 "$WEB_BUILD"
--web-dist-sha256 "$WEB_DIST" --web-notices-sha256 "$WEB_NOTICES"`.
Never discover expectations from candidate receipts. Retained web originals
append rows while preserving the complete pre-addition mapping prefix. Source
and portable web observations are distinct from a real Mac wrapper/licenses run.
The former six-closure requirement is retired, without asserting that claim.
Concrete failed rows and incomplete manifest membership prevent archiving.
Release=null, available=false and approved_url=null remain unchanged.

Packaging compilation preserves the ordinary Tailwind `AtProperty` and
`ColorMix` polyfills, with the same recorded option checked by both readers.
The existing custody observer also captures each public final `moduleParsed`
callback; publisher and fresh reader require its code to match the module chain.
Generated web evidence uses the fixed `web-private-plain@2` profile documented
in packaging/README.md: lossless262144 observations, other named domains32768,
96 MiB input /112 MiB envelope (+16 MiB),12582912 build nodes/depth32,
8 MiB aggregate UTF8 name occurrences (including repeats), unchanged4096-byte
individual metadata strings,
1024 MiB represented body work, unchanged512 MiB guarded regular reads and
web-only192 MiB aggregate JSON per command. Native asset lanes retain one
persistent512/32 MiB submeter through the whole command; reentry and nested
bounded calls never replenish it. Every repeated parse/read counts. Default
asset/freezer limits and shared census semantics stay unchanged. Portable
measurements are separate from actual Mac execution and release acceptance.

Both build serialization and every independent stored-build read enforce the
same input/envelope addition limits; a stored registry cannot bypass the 16 MiB
addition bound through complete, accept, seal or fresh verify.

The actual single Tailwind compile now has a helper-owned loader lifecycle in
`web-css-loads/`: unique invocation and original transform-call binding, input
size/hash/options, ordered per-compile stylesheet ordinals and opaque read
receipts written before callback return, then a successful end from that same
awaited call. Repeats retain distinct tokens; zero-import compiles still have
begin/end. Interleaved invocations keep their own sequence. The helper derives
the complete directory inventory and exclusively creates `seal.json` before
validating mutable operations. Publisher and separate fresh full verifier both
reopen that inventory, every event and its original source descriptor, and
require a complete bijection with compile operations and ordered imports.
Partial imports, equal-byte subject substitutions, owner/order swaps, duplicate
imports and whole missing compiles refuse. Unknown leaves, gaps, open compiles,
extra reads and post-seal writes refuse; failed prefixes are diagnostic only.

Additional sublimits are 32768 lifecycle/read files total, 256 KiB per event,
8 MiB aggregate event JSON and 8 MiB terminal seal. Bounded enumeration stops
before excess entries are retained. All existing read/JSON/build/name/body and
transport caps still apply; event writes/readbacks, parsing and producer
transport are charged. These limits supply no measured-fit or host-RSS claim.
The selected optimizer profile passes no map and refuses an unexpected non-null
returned map before handing code to Vite; both readers require null optimizer
input/result maps. Vite's later CSS map is a separate relation. Map-bearing P
graphs are unsupported-profile negatives, not production-positive evidence.
Each CSS emission consumes its original emission observation once and matches
its resolved output filename, including when separate emissions have equal bytes.
This is local custody under the reviewed helper/producer model, not authenticated
compiler execution or resistance to invisible same-UID forgery/restore.

The packaging preparation checks the authenticated oxide, Rollup and lightningcss
loaders before Vite or static config imports execute. It refuses earlier local
and universal bindings, WASI/local fallbacks, nested package shadows and tool
overrides, and probes Node 24 platform, architecture and importer resolutions
without loading native modules. Preparation and context retain selected members,
locked originals, actual ancestor/global package lookup locations and the complete
excluded file relation: exact stems plus `.js`, `.json` and `.node` candidates,
even for requests already ending in `.node`. Existing excluded directories refuse
without reading their package entry metadata. Rollup's exact `existsSync` branch
retains its loader-specific reachability. Non-loading Node probes corroborate
excluded-request nonresolution; errors other than `MODULE_NOT_FOUND` refuse.
Both publication and a fresh reopened reader
revalidate that relation and the actual build process's resolved paths and loaded
CJS native cache members. Linux/glibc/x64 observations remain portable evidence;
Darwin/arm64 execution remains separately required. These local custody checks
provide no same-UID race or authenticated-execution guarantee.

The embedded notice collector enters the existing 192 MiB JSON web budget before
parsing the caller and constructing its Session. Nested native asset work retains
the persistent 32 MiB asset submeter; neither budget is reset or enlarged. Notice
tokens leave the wrapper only after the complete mapping is successfully written.
