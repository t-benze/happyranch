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
ambient framework or creates global/user bin links. Before sync, the actual
CPython 3.12.10 executable must match the separately validated concrete selection;
raw prefix/base prefix must agree within the private installation and raw
stdlib must be beneath that base. Sync and both no-dotenv runs share the
patch-bearing `--python cpython@3.12.10` request to retain fixed venv upgrade
semantics, with the concrete executable kept as the physical identity
expectation. They bind the absent absolute exported-source/.venv, retain the
frozen local Darwin PyInstaller wheel, and disable subsequent downloads.
Before writing the strict four-field toolchain receipt, the actual venv must
pass executable/version/platform/machine checks, raw prefix must equal that
absolute .venv and raw base prefix must equal the admitted concrete base.
Raw venv/base/stdlib directories pass the existing no-follow ancestry/FD,
owner/mode/device and identity rechecks within source/install/base boundaries
before resolved executable comparisons. Directory aliases refuse; permitted
executable leaf links retain the separate later executable_alias checks.
Earlier admission grants no later root/toolchain exemption or same-UID race
guarantee. The receipt describes only the actual venv interpreter's
executable/version/platform/machine.

Before any uv command (including the early version read), both entries refuse
inherited UV_* selection/source/mirror/catalog/mode controls. The only exceptions
are cache (replaced), offline/download policy (honored by refusal when downloads
are prohibited), progress, and UV_PYTHON only when absent or exactly the
literal `3.12` supplied by the pinned workflow caller. Empty, path, patch,
alternate and whitespace selectors refuse without exposing values. After the
entire inherited-environment check succeeds, each parent shell explicitly
unsets UV_PYTHON before any uv command or wrapper handoff. The literal is only
a compatibility input; the fixed private CPython request and absolute managed
executable control selection. Mismatched VIRTUAL_ENV and inherited PYTHON*
controls refuse without exposing values. The wrapper validates its private
temp/cache ancestry and mode before use; standalone freezer temp/cache/install
allocations receive the same custody predicates before effects. Discovered
uv.toml overrides and unexpected source-project uv configuration refuse before
install, without suppressing the committed source table. Every ancestor above
the source through `/` must have no pyproject.toml: unrelated, malformed,
oversized or unreadable entries, directories, all symlinks (including dangling)
and inspection errors refuse via pinned ancestry descriptors before freezer
dist/work reservations, install or sync. The source's own project retains its
exact frozen uv-settings check. At this freezer boundary the wrapper has
already reserved scratch, exported source and read the uv version; its existing
finish diagnostics apply. Early environment refusal occurs before that trap.
See the complete
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
selected `root` (or null). Original process roles are checked in order: `prefix`,
`base_prefix`, `stdlib`, `usr-bin`. At the later native stage the selected lipo
and install_name_tool immediate bin roots are each checked before deduplication,
with cap16 preserved before native snapshots and Analysis. An unsafe ancestor names that ancestor even
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

Native-tool selection, alias and snapshot refusals can additionally carry one
best-effort `NATIVE_TOOL_CONTEXT ` exception note with compact ASCII JSON (at
most 1024 bytes including the prefix). It names the literal tool role, operation
(`selection`, `executable_alias` or `native_snapshot`), selected and terminal
paths, and separate path truncation flags. Each path is null when unknown or at
most 192 serialized ASCII characters including quotes; truncation keeps whole
Unicode codepoints/escapes plus `...`. The terminal comes from the snapshot's
actual resolution, then from the actual opened subject at a pre-read refusal.
Earlier selection/alias/root/open failures leave unknown fields null.

Only a refusal at the regular-file pre-read admission checks supplies
`observation=opened_before_admission`, full octal `mode`, octal `type_bits`,
file `type`, `uid` and `nlink`, taken from that operation's existing same-descriptor
`fstat`. Before that observation, and for post-read instability, all descriptor
fields remain null. There is no diagnostic stat, reopen, payload read, native
command or changed tool selection. Formatting or note attachment failure can
leave the note absent; the same original exception, nonzero exit and descriptor
cleanup remain controlling. Existing `DIRECTORY_CONTEXT` notes are preserved.

Native notes are stderr/traceback evidence only. `str(error)` and `Session.fail`
do not persist notes in failure records, and successful records, schemas, roots
and tokens carry no diagnostic context. Existing tee/build.log and allowlisted
failure collectors can carry the note, but whole-file caps can omit logs: the
wrapper collector permits 8 MiB per input/32 MiB total read and 2 MiB per stored
file/8 MiB total stored. Actual hosted log/artifact propagation, runtime bounds,
descriptor/race/negative behavior and final native consumers remain unverified.
This diagnostic establishes no tool execution identity, successful build or app
acceptance. Literal codesign and original vendor PATH argv remain; the custody
selection described below aligns the two affected PATH roles.

For full-app custody builds, spec setup now discovers real lipo/install_name_tool
paths before `spec_begin` and either Analysis using the fixed developer directory
`/Library/Developer/CommandLineTools`. Only the resolver child environment
overrides `DEVELOPER_DIR`, retaining the original PATH and other settings.
Caller-selected `DEVELOPER_DIR` is not consulted; `xcode-select` is not queried.
Exactly two sequential `/usr/bin/xcrun --sdk macosx --find` queries select the
two roles. Each query has a 10-second deadline, streaming combined8192-byte
stdout/stderr cap and4096-byte
path plus optional LF limit, drains both pipes and settles only its owned child.
Strict UTF8/single-path/control/colon/component/length validation precedes use;
spaces are allowed. No host selection/cache mutation, probe execution, retry,
alternate installation or copied tool is used. Discovery failures report bounded
role/category errors with descriptor facts unknown, never wholesale stderr/env.

The transient selection is validated again in `spec_begin` after the original
caller/context/platform/root/wheel/receipt/Python sequence. Candidates must have
the exact raw spelling `/Library/Developer/CommandLineTools/usr/bin/<role>`;
the developer string must be exactly `/Library/Developer/CommandLineTools`.
Dispatchers, aliases, other SDK/toolchain/installations and alternative raw
spellings refuse. Only those exact bin roots are admitted; `/Library`,
Developer/CLT/SDK trees and `/` gain no general read permission. Raw ancestry
and all existing single-link/type/uid/mode/no-follow/nonblocking/device/cap and
stable-identity safeguards remain controlling. Returned leaf aliases and
nonexecutables refuse; no unsafe path or attributes are assumed. Native row and
snapshot order stays codesign,lipo,install_name_tool, with literal
`/usr/bin/codesign`. Original same-descriptor refusal notes remain authoritative.

After successful snapshots, candidate parents (lipo then install_name_tool,
deduplicated) precede the complete original PATH. Both `shutil.which` results
must equal the discovered paths before activation, so cross-shadow/disappearance/
fallback refuses before Analysis and tentative PATH is restored on refusal.
Only this PyInstaller process receives the PATH for both lanes and the whole
PYZ/EXE/COLLECT lifetime. The original vendor PATH argv for the two affected
roles inherits this selection; no wheel edit is involved. The parent shell,
active Xcode/outer SDK selection, later web/Go/Swift and frozen CLI inspection
environments are unchanged; custody-absent standalone freezes perform no
discovery/PATH change.

Native row shapes, `selection_observation`, `origin_unobserved`, root propagation
and four caller-held digests stay unchanged. Fresh accept/before-copy/copy/licenses
readers reopen recorded roots/selected paths/aliases/live and snapshot subjects,
never their own PATH or a new xcrun result. Final verify-release PATH lipo
inspection is a separate unchanged consumer. This observes intended selection,
not an executed image or publisher, and cannot detect invisible same-UID
replacement/restore. Actual CLT ancestry/leaf suitability, command compatibility
and discovery, bounded cleanup/FD/race/negative behavior, both-lane completion
and fresh native consumers remain
UNEXECUTED/UNVERIFIED; source/static success establishes no app/archive/receipt,
native or release acceptance. The full
[freezer selection contract](../../packaging/README.md#freezer-operation-custody)
retains original-backed notices and all independent package checks.

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
Darwin selects custody revision 5 (`1happyranchcustody5`) with descriptor-relative
snapshot writes and ZIP metadata budgets enforced before allocation. Revision 5
adds the missing `api.py` module-global `hashlib` import for the existing COLLECT
ledger digest, preserving its reader and limits. Build14 failed there after both
EXEs built; app inspection was not reached. Custody5 build15
(`39518819733`, attempt1, input `a9970abb52e3a444c7172674a1eca360a08f8c00`)
completed both EXEs and COLLECT, then failed in the base-library ZIP observer:
CPython3.12.10's ZipFile member reader requires `seekable()`, absent on mmap.
The observer now borrows the held snapshot mapping through a seekable read
interface, without a whole-container copy or path reopen. Metadata admission
still precedes ZipFile allocation; closing the interface leaves mapping and
descriptor ownership with the enclosing snapshot context. Native confirmation
of this helper repair remains UNEXECUTED; build15 is not a package pass. The original
base-library producer sorts modules by name before each original lane capture,
with or without custody. It retains membership, cached-code selection,
optimization and ZIP member bytes.
Both original Analysis observations and all contributor edges remain, with exact
byte/kind collision and live-drift refusal unchanged. Equal module tuples do not
prove equal cached code, and build12's exact changed subject remains unknown.
Fixed operation-selected freezer inputs/output profiles are32MiB each with both
lexical/object1,000,000-node admission; context64KiB and copy/default8MiB/smaller
site caps remain. All retain strict UTF8/duplicates/nonfinite/types/Boolean-field
checks, depth16 and string4096B. Object preflight and bounded incremental encoding
preserve full canonical records, every occurrence/order/ID and lineage. All fresh
accept/before-copy/copy/verify/licenses readers use matching caps, reopen/hash full
bytes and preserve the four-digest DAG; copy references actual stored output.
One enclosing attempted JSON-I/O256MiB meter per helper command/spec Session
charges every write/readback/load/reference and standalone/nested fragment read,
including both parse and retained-byte reads, without path discounts/nested reset.
Readback reserves written length plus an EOF probe before creation under an
exact-length domain within the kind cap; partial IO does not refund reservations.
Exhaustion stays sticky; original failure/nonzero/partial diagnostics remain.
Ordinary reads8GiB/snapshots2GiB/members16384/names8MiB are independent; fragment
JSON also charges ordinary reads. Vendor COLLECT independently reads the entire
32MiB ledger with declared integer size/full consumed digest/lexical1m and original
object1m/depth16/string4096 guards, ordered arguments and stable observation.
Those reads charge its8GiB meter; vendor fragments/default parse remain8MiB,
fragment writes32MiB/run. Unrelated marshal/PYZ/ZIP and asset budgets stay unchanged.
The224MiB/spec and232.125MiB/fresh-reader formulas plus small records/EOF probes
bound the current call graph, not native totals or hard RSS/time. Build13's exact
rejected size/content remain UNKNOWN; finite capacities do not guarantee native
success. Original schema/notice/guard/default/occurrence semantics remain.
The new wheel/provenance/lock/wrapper/reader pins agree; historical revision 1/2/3
evidence and retained wheels keep their original identities. Deterministic
source-repack reproduction and build15's EXE/COLLECT success do not establish
changed-helper Darwin compatibility, complete lane equality or fresh-reader
behavior, which remain UNEXECUTED/UNVERIFIED.
App/archive/receipt and final manifest notices remain NOT_REACHED; original-backed
notice precedence, unsafe-original refusal, exact missing-original fallback and
both-lane interpreter/RECORD/native ownership obligations are unchanged.
The existing daemon freeze and app cp-R execute once; observers retain their
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

### Bounded web packaging progress

The existing packaging custody observer emits content-free progress directly to
stderr with synchronous writes before each actual parent helper spawn and after
its return or failure. Selected `renderStart`, `renderChunk`, `generateBundle`,
`writeBundle` and `closeBundle` wrappers mark observation, original-hook completion
and settlement; `renderChunk` selects the first and every 32nd call per plugin.
The observer's own final write/close, measurement and serialization are marked,
with helper markers covering publish/completion. Fixed event/boundary/stage names,
numeric ordinal/call/plugin numbers and monotonic elapsed milliseconds identify
these boundaries. Caller JSON, tokens, payloads, source/module/chunk names, paths,
environment values and error text/stacks are never included in these diagnostics.
Load/transform/moduleParsed calls have no progress logging.

One custody instance shares the original 65536-byte total diagnostic budget,
including the existing single suppression notice. At construction it charges a
1024-byte terminal complete-build grant, even if never spent. Ordinary progress
and each 3072-byte snapshot grant use the remainder while retaining the notice.
Unused or partially written grants are never refunded, reset or replenished.
If a snapshot grant cannot fit, the existing notice suppresses ordinary progress
and snapshot diagnostics; the upfront terminal grant survives. Unavailable stderr
retains the original three-pipe helper fallback. Short or failed writes affect
only diagnostics. The previous 47546-byte reservation through call 22 (14 snapshot
grants plus parent records and notice) becomes 48570 bytes including the terminal
grant, still within the same 65536 bytes. Later suppression may occur earlier.

A reserved snapshot maps fixed child fd3 directly to the parent's existing stderr,
so its lines remain visible while the original synchronous spawn is blocked.
Numeric private CLI metadata grants the helper its call number and byte allowance;
there is no caller-selected descriptor or destination. The child writes at most
24 lines of at most 128 bytes each, including newlines, within its granted bytes.
Missing/invalid metadata or unavailable fd3 disables child diagnostics; short or
failed writes disable further child lines without retry. Borrowed descriptors
are never closed. fd0/1/2, captured protocol stdout/stderr, CSS transport and
original helper result/error handling are unchanged.

Fixed `[packaging-snapshot]` records pair `start`/`end` for `bindings`, `assets`,
`toolchain`, `source` (Git tree and helper pins), `stdin`, `mutable`, `context`,
`create` (snapshot creation/readback), and `reply`. They carry only the numeric
parent helper call number and fixed phase/event names. Operations and guarded
reads remain in their original order. The `assets` phase follows the selected
venue: a portable end does not exercise native asset context/source validation.
A missing end leaves that phase's outcome UNKNOWN; even a reply end does not
prove child exit or native/package acceptance.
There is no retained stream queue, timer, monitor or deadline guarantee for OS
stderr I/O. A start without its matching end identifies only a selected boundary;
it does not identify an exact inner cause, and suppression, I/O failure or
cancellation can also omit an end. Hook promise identity/lifetime/order, helper
capture/transport limits, native source validation and build acceptance stay as
before. Ordinary builds retain the existing null packaging-custody path.

Native build17 (`39571904074`, attempt 1, source `22eea903`, tree `5fa018a`;
workflow definition `be36c7a`) completed both EXEs and COLLECT. Its 43 immediate
parent helper records show calls 1–21 returning, then snapshot call 22 starting
at `2026-10-21T08:52:24.4387860Z` without an end/failure before cancellation at
14:48:45Z after six hours. Node's synchronous helper spawn had not returned;
Python admission, asset/source validation, Git, stdin, snapshot work and child
closure were not distinguished. Precise cause remains UNKNOWN. Reserving for
all 14 snapshots through that call costs 43008 bytes, leaving room for the
observed parent records and the suppression allowance within 65536 bytes.
The fd3 phases are optional diagnostics for the next root-owned changed-source
native confirmation, not a causal or performance fix. Portable TASK-12265/12271
success is a baseline only; its 788662-byte blob 14 is not known to equal native
input, and an earlier portable blob was larger. JOB5031 exit 0 is collection
only. Native artifacts were absent; archive/receipt/final metadata, manifest
notices/hash/ZIP and install/launch acceptance remain unverified.

Native build18 (`39630410187`, attempt 1, checkout `49a30dfe`, workflow definition
`be36c7a`) completed both EXEs, COLLECT and the Vite body, then complete-build
refused `AGGREGATE_READ_CAP_EXCEEDED: web-evidence-v1`. Call 22 returned all nine
snapshot phase pairs; progress was suppressed at call 28. The failed native
read/phase, enclosing counters and record closure remain UNKNOWN. Native used
Node 24.20.0/npm 11.19.0/CPython 3.12.10. Collection JOB5055 exit 0 supplies no
package acceptance and is not a successful build.

Portable original-helper JOB5058 exit 0 measured accept-build in 49.6917 seconds:
regular 527830544/536870912 (9040368 bytes headroom), JSON 79744940, sticky failure
null. Each of its two full context passes charged 174139703 regular bytes;
the build load charged 136247034 regular and 68123517 JSON bytes. Linux used
Node 24.19.0/npm 11.17.0/Python 3.14.4. This is portable accept evidence, not
native complete-build reproduction or grounds to remove either context pass.

The upfront terminal grant uses the same optional borrowed fd3 transport with
checked integer call/grant metadata. It permits one `[packaging-complete]` line
of at most 1024 bytes, including newline, on actual failure or success while the
SAME original command budget is active. Fixed phase/operation vocabulary separates
admission, prefix context, checked-build record load, validation context and
subsequent validation, completion write and reply. A failed subject import also
reports its ordinal and declared size. Regular/JSON counters come from the
existing enclosing and asset-lane read-only views after attempted charging;
`-1` means an unavailable lane. These counters may exceed a refused cap. No paths,
subject/caller bodies, environment, secrets or exception text are emitted.
Local annotations do not change read/charge/validation order, both full context
loads or context/plan prefix import timing. The terminal failure line precedes
outer failure handling and preserves the original exception/status and sticky
refusal; it cannot become a receipt. Missing/invalid metadata, unavailable fd3,
short/failed output or a line exceeding its grant disable only diagnostics,
without retry or alternate output. Failure/refusal paths remain source-audited
and UNEXECUTED here. Native attribution and cap resolution remain UNVERIFIED;
portable success cannot establish native/package/archive/install/launch acceptance.

Native build16 (`39521557858`, attempt 1, source `a17cf29`) completed both EXEs and
COLLECT, then logged Vite's 4695 modules before silence and six-hour cancellation;
artifacts were absent and cause remains UNKNOWN. The unchanged-source portable
JOB5026 at `d723ad06` completed the actual npm producer with exit 0 in 111.3826 s
and accept-build with exit 0 in 16.7244 s on Linux Node 24.19.0/npm 11.17.0.
Its retained helper timing included 153 snapshots (19.4324 s total), publish-build
(29.1517 s) and complete-build (23.6445 s); these externally traced intervals
include startup/validation/tracing overhead. Native-only asset context/source
validation was unexercised. This evidence does not establish native root cause,
a performance fix, Darwin timing or package/launch acceptance.

### Named helper deadlines and owned cleanup

`packaging_supervisor.py` is the packaging-local standard-library owner used by
Node custody, the Bash build adapter, archive manifest verification and the
release verifier. Its finite operation table refuses unknown labels. There is
no timeout override or operation retry. Clocks begin before child launch and
include initialization, transport and private control handshakes.

| Named work | Seconds |
| --- | ---: |
| Asset/freezer/web token-field; freezer/web fail; internal failure persistence | 30 |
| Web read-input, compile-begin, compile-end, snapshot | 120 |
| Web prepare/begin | 300 |
| Other enumerated asset/freezer/web stages, embedded notices, manifest collect/verify | 600 |
| Verifier preflight/native/resources/catalog/trust/inventory/archive/reconcile | 300 |
| Verifier licenses; frozen-entrypoint diagnostic outer | 600 |
| Existing capture and verifier inspection tools; failure diagnostic collector | 60 |
| Build log finalization after finish/EOF | 10 |

Internal failure persistence is additionally clipped to the original remaining
operation clock; an unsupervised PyInstaller import has no external30s timer.
The verifier's direct `ar -p` is specifically outer-contained by native300s;
trust observations retain their existing60s arguments within trust300s. The
seven frozen diagnostic calls retain60s each within the600s outer. These are
not per-tool registered descendant-cleanup claims. Manifest validation bodies,
notice texts, input guards, complete-build context passes/live reopens and all
original read/JSON/diagnostic caps remain unchanged.

The actual parent may signal its child group only while holding the positively
unreaped direct-child handle. It disarms before every poll/reap and rearms only
on a confirmed still-running result. Exceptional construction, ECHILD and
ambiguous control never rearm. Open output pipes keep an exited leader pinned
until cleanup, including resistant pipe holders. EOF with a live leader follows
the separate disarm/poll/rearm path. Private registration informs settlement;
it gives no other parent numeric kill authority. No post-reap kill or global
process scan occurs. Group absence, EOF and actual status are observations, not
proof of global descendant quiescence; parent loss/escape can leave cleanup
UNKNOWN and must never be called complete containment.

The relay retains pending input/output across retryable nonblocking readiness
loss, including stdout/stderr/fd3 sinks that alias one pipe; partial writes
advance only the bytes delivered. These retries consume the original clock.
Secondary and terminal diagnostics make only a best-effort nonblocking attempt
within the existing cleanup window; unavailable diagnostics cannot replace the
computed status or delay return by waiting for a sink. Borrowed stdio flags are
restored after their last use. CLI error diagnostics likewise never wait.

One shared5s outer cleanup allowance covers TERM (at most2s grace), held-child
KILL, drain and reap/closure. Asset capture retains its stricter60s/0.25s/1s/1s
schedule. Node has an independent D+10s SIGKILL fallback; failure is sticky and
never admits a partial reply. The Bash3.2 adapter preserves original stdin and
heredocs through asynchronous launch, assigns a token only after accepted status
and bounded reply, and retains command-substitution trailing-LF behavior. Its
same-module guardian owns the worker; D+10 fallback and D+12 timed observation
slots never give Bash bare-PID kill authority or an unconditional wait.

Finish retains the original npm/helper/signal status while attempting secondary
freezer30s, web30s and diagnostic60s persistence independently. Tee retains the
original build.log and stdout sinks; after writers close, its owner allows7s
drain,1s TERM and remaining2s KILL/drain/reap within10s (shell observes at most12
slots). Primary deadline124, cleanup-only125, actual child signals and existing
nonzero failures remain distinct. Secondary failure cannot replace a primary.
One600s helper plus ordinary cleanup/finish/logger allowance is750s, or780s
with guardian/caller fallbacks; neither is a total-build or hard OS walltime.

Compilers, npm producers, PyInstaller itself, compression, tiny scalar/environment
checks and file operations remain workflow-only. This helper correction grants
no native run or workflow acceptance. Build preflight uses a synchronous
substitution: parent traps can be deferred until its300s+5s return. A late signal
can leave an already-written one-shot marker; failed transport/status still
withholds acceptance. Uninterruptible kernel I/O and scheduler/parent loss can
exceed observed bounds. Actual Darwin Bash finish/logger, native freezer and
archive/notices/hash/ZIP acceptance require their real native venue; portable
relay checks do not establish those outcomes. Native18's aggregate cap and the
native17/19 unknown stall causes are not resolved by these deadlines.
