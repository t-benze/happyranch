# Frozen Mac packaging

Run the full [Mac build contract](../app/mac/README.md) from clean reviewed
source.

The full hosted candidate workflow bootstraps native arm64 Go **1.23.12** using
`actions/setup-go@b7ad1dad31e06c5925ef5d2fc7ad053ef454303e` (v7.0.0), with
shared setup caching disabled and job-wide `GOTOOLCHAIN=local`. Before native
packaging it records PATH/resolved executable/version/GOHOSTOS/GOHOSTARCH and
refuses a version, Darwin/arm64 host or toolchain mismatch. Compilation retains
the fresh run's Go caches and `-mod=readonly`; module/source pins stay unchanged.
This follows the pinned Tailscale release's Go 1.23 line and runtime-dependent
gVisor graph, using its newest archived patch. Go 1.23 is out of upstream
maintenance; newer maintained compatibility is not established. See the
[source provenance and maintenance limit](../app/mac/README.md#internal-candidate-build).
The prior hosted missing-Go failure remains a failure; corrected native build,
archive/linkage and compatibility remain UNEXECUTED pending independent QA.

Freezer-only work is supported **only in a disposable checkout**:

```bash
bash packaging/build_daemon.sh \
  --dist-dir "/absolute/owned-parent/new dist" \
  --work-dir "/absolute/owned-parent/new work"
```

Both paths must be new, disjoint, outside source, with existing owned parents
and no symlink ancestors. `uv sync --group build --frozen` installs existing
build dependencies in that checkout; PyInstaller receives explicit dist/work
paths and the checked-in equivalent hook. Before any build tool runs, the
freezer allocates and validates a fresh `pyinstaller-cache.*` directory under
the admitted work path and exports it as `PYINSTALLER_CONFIG_DIR`, overriding
any inherited selection. Thus `--clean` operates only on that invocation's
cache in both standalone and full-app builds. Work and cache are retained with
diagnostics; the venue owner disposes of only these invocation allocations,
preserving external caches. Both daemon and CLI must be
executable. Separate daemon/CLI PYZ archives share the byte-checked union of
both native/data closures. Destination collisions refuse instead of dropping
CLI-only payloads. `analysis.json` records actual pure/binary/data rows and CLI
set differences, including empty differences. No missing-module bug is claimed
from source inference; no removed runtime modules or retired units are restored.

`license-mapping.json` and `notices/` retain original installed license texts,
versions, input inclusion and hashes. Go compilation dependencies and web source
maps add original module/package texts and inclusion mappings during the full
build. Build-input membership alone is not final linked/emitted license
completeness. Named missing/unresolved dispositions remain fatal to complete
release finalization; their partial records are useful diagnostic output.

Emitted web inputs are attributed within their innermost installed package
boundary, including scoped and nested packages. Anonymous subpath format
manifests inherit only that package's validated root name/version. Malformed,
missing, conflicting or unsafe metadata/input/notice paths produce
`LICENSE_UNRESOLVED` with source-map, input and metadata paths; they cannot
inherit an enclosing dependency or web-app identity. Only existing inputs
lexically and physically contained within `web/src` (the HTML entry/Vite alias
boundary) may skip dependency attribution; missing, external, symlink-escaping
or otherwise unclassified inputs retain per-input unresolved evidence without
reading external metadata/notices. Both source and `sourceRoot` must be
filesystem-encodable before inspection; rejected records preserve their
source-map, sourceRoot, source and lexical input provenance using escaped JSON.
Grouped inputs retain map/source/metadata hashes and original notice bytes.

All notice-named entries are checked before declaring installed originals
absent, including FIFOs and directories. Every installed source and existing
destination must be a bounded (at most 256 KiB), nonempty regular file with one
link and no symlinked/escaping path. All of an owner's notices and destinations
are validated before copying any; unsafe entries or conflicting destinations
produce `LICENSE_UNRESOLVED` without fallback. Safe installed bytes are retained
exactly, and identical safe destinations are left untouched. An earlier refused
emitted input prevents later inputs from admitting any notice for that owner.
Root and encountered nested package manifests use the same nonblocking,
no-follow bounded read for parsing and hashing; unsafe or malformed metadata
refuses promptly. Shared Go notice collection preserves installed bytes and
records unsafe notice inputs as unresolved.

Emitted dependency source hashing uses a separate 16 MiB per-file limit and
64 KiB streaming reads, with lexical/package and ancestor checks, no-follow,
nonblocking open, single-link regular-file validation and stable size/timestamps.
Empty regular JavaScript modules retain the SHA256 of empty bytes. The limit
provides over 13 times the maximum size (1,236,999 bytes) in the authenticated
1,043-source retained mapping; notice, manifest and pinned fallback limits stay
unchanged. Any source refusal persists for its owner across later inputs/maps.

The [notice index](THIRD_PARTY_NOTICES.md) documents the two byte-preserved
offline originals and exact pinned identities in `packaging/notice-data/`.
Installed originals take precedence. The CPython fallback requires matching
effective toolchain and three stdlib hashes in both Analysis lanes; the ten
Radix fallbacks require pinned metadata and every actual emitted source hash.
Unsafe paths, corrupt inputs and conflicts refuse retention. The web dependency
now installs a local derivative of upstream scroll-bar source
`8ca9ba5ea52de03308fe8ced94f7b159a44d28ff` (upstream version 2.3.7), including
its byte-preserved original MIT LICENSE. This replaces the registry 2.3.8 input;
it does not establish notice provenance for that former publication. See
[`web/vendor/react-remove-scroll-bar/README.md`](../web/vendor/react-remove-scroll-bar/README.md)
for source authentication, locked-tool compilation, reproducible tarball and
installed-byte checks. The ordinary node_modules package remains within this
collector's existing third-party attribution boundary. The checked-in tarball
and all preparation inputs survive the release wrapper's git archive export;
ordinary npm ci requires no local package lifecycle build.

All six unresolved labels remain, and strict license verification still blocks
ZIP/release finalization with `closure_complete=false`. Scoped non-native
package/browser checks do not establish final emitted closure, legal
applicability, actual native custody or Mac acceptance.

The Mac wrapper's private `asset_evidence.py` adds local asset observations,
without a new dependency, Vite change or rights policy. A complete bounded
Git/export census includes hidden/unreferenced public files, fonts and OFLs.
Public `index.html`/`assets` reservations and exact/case/NFC/prefix collisions
refuse. Every ordinary dist file is measured; unknown generated/other assets
remain `UNRESOLVED`. The actual app web tree must equal dist except every
existing basename `*.map` removal, each recorded explicitly. No historical
18/119-file count substitutes for a census.

At the existing notices stage, all three original font/copy relations and both
retention targets are validated before either notice is written. Three distinct
font component rows reference two exact original OFLs retained under
`Contents/Resources/licenses/assets/`. Both Newsreader owners share one notice
and are withheld together on a shared conflict. Safe identical existing notices
keep their inode/mtime; conflicts and incomplete stage files cannot be resumed.
No synthetic text or root Apache reassignment is allowed.

Seven direct PNG originals produce exactly ten iconset copies. Only the actual
Darwin/arm64 operation runs iconutil, once, at the selected absolute executable;
its output goes into newly reserved private `scratch/icon-output`, then an
exclusive app byte copy. Bounded real streams, argv/cwd, pre/post subjects,
process exit/signal/timing and quiescence are retained. Timeout/overflow
terminates and reaps the owned process without retry; failure preserves the
original tool exit separately from inspection errors and retains partial files.
Catchable HUP/INT/TERM is deferred through launch/handle registration and cleanup;
the first failure is retained. Setup precedes launch where possible, and each
owned pipe/selector is closed independently after bounded termination/reaping.
An unknown exit or failed cleanup never supplies a quiescent success.
The tool's own file API is not O_EXCL: private single-writer custody is required.
There is no hostile same-UID or executed-image attestation guarantee.

Fixed local diagnostics are `assets-{context,source,dist,app,icon,notices}.json`
and `iconutil.{stdout,stderr}`. Each successful stage returns its bounded token
directly to the wrapper; the context precedes source, dist and app, with icon
and notices referencing earlier bindings. Expectations are never recovered from
the candidate records. Standalone readers require independently retained caller
arguments/tokens and the original subjects. Missing native evidence refuses the
full reader; there is no partial-success reader. The unchanged mapping reader
still requires literal Boolean True and preserves all failed components. It
does **not** implement the future exact-six-result/sentinel identity contract.

Both producer and independent reader use descriptor-relative no-follow,
nonblocking, regular/single-link and stable-identity reads with complete stable
membership walks. Selected roots must have safe physical ancestry and owned
private output parents. Limits apply together: files16MiB; notices/manifest and
icon/notices records256KiB; owner128B; preflight16KiB; context64KiB;
toolchains/each stream1MiB; source/dist/app records8MiB each. Trees permit4096
entries including directories, depth16 and4MiB names. JSON is closed with
unique keys, depth16/100,000 nodes; per-invocation reads total512MiB and local
JSON32MiB. Process waits are60s with bounded termination/drain; tokens4KiB.
Overflow refuses, never a truncated completeness claim. No whole-mapping cap is
introduced. Failure bundles retain their existing allowlist/caps; new records
and original subjects remain local, and a summary upload cannot replace them.

Source delivery and portable fixtures do not establish actual PYNOTICES
append/False/sentinel/error/token behavior, wrapper-to-licenses integration,
full independent reader or native operation custody. Those remain native-only
UNEXECUTED/NOT_REACHED, alongside Mac14/WKWebView/install and future all-six
finalizer cases. Native execution is not a prerequisite for this bounded source
review; the parent retains actual changed-source/input/custodian disposition.
All six closures remain UNEXECUTED/UNMET, including ASSET_ATTRIBUTION.

Failure diagnostics include
`web-attribution.json`: first 100 rejected records, string fields capped at 512
characters and serialized records capped at 2048 bytes, with explicit
omitted-record/field truncation. Retained texts leave `closure_complete=false`
and release finalization blocked pending actual legal/native/asset reconciliation.

The failure bundle preserves complete small files (up to 262144 bytes) as
`.diagnostic` and larger complete files as `.diagnostic.gz`, with no JSON byte
tails. A fixed 50-input allowlist covers available Analysis/daemon and CLI PYZ
TOC descriptors, native/data union, Python/toolchain, Go inputs/members, link
map and component/notice provenance plus per-command CLI/catalog/trust results.
Inputs are capped at 8 MiB each and 32 MiB total; stored bulk is capped at
2 MiB each and 8 MiB total. Three independent critical files (build failure,
native failure and all eleven symbol observations) each have a 262144-byte
budget. At most 102 files and 128 KiB of metadata describe source hashes/sizes,
whole-file identity/gzip encoding, original/retained/omitted byte and known row
counts. Missing or uncountable inputs use null counts, never zero-loss claims;
malformed, unsafe or over-cap inputs get valid explicit loss records. The
`evidence-index.json` distinguishes available stage evidence from missing or
not-yet-produced inputs. Native summaries preserve per-export nm/member
observations separately from bulky raw nm and link-object data; bounded row or
field selection states its omissions. Only task-owned regular allowlisted
files are read through no-follow descriptors; symlinks and hardlinks refuse.
No app binary, private archive, recursive cache tree or environment dump is
uploaded. Diagnostic completeness does not establish release or legal closure.

## Read-only verifier

All commands use Python standard library and, for native inspection, actual
Mac `file/lipo/otool/nm/ar` tools. Trust inspection captures the installed spctl manual, codesign metadata and
nested/outer integrity exits plus actual policy-assessment diagnostics. Tool
failure/unresolved assessment is separate from observed exit3 rejection or
exit0 acceptance; these observations do not assert notarization/public trust.
Every native rpath is expanded in its component's loader/executable context
and resolved before dependency inspection, even if currently unused. Token
paths must remain within the bundle, including through symlinks. Absolute paths
must resolve within the bundle or `/usr/lib` or `/System/Library` themselves or
their children; spelling prefixes and unknown tokens refuse. Raw paths remain
in diagnostic evidence.
Native strong linkage requires exactly one final strong definition and one map
object per export. The complete origin must match the exact archive path plus
`(member)` or `[numeric-index](member)`, with that member uniquely present in
`ar -t`. Path metacharacters are literal; no basename, substring or prefix/suffix
fallback is used. The optional Apple index is syntax, not proven positional
identity. Duplicate archive names, object IDs or symbol associations refuse
while retaining all eleven observations and original origins. Duplicate member
names leave member hashes explicitly unproven rather than hashing ambiguous
`ar -p` output. Unique members retain their actual size/hash provenance.
No runtime imports, skill/guard/cleanup execution,
provider calls or signing occurs. Example commands:

```bash
bash app/mac/scripts/verify-release.sh resources --app "/owned/HappyRanch.app"
bash app/mac/scripts/verify-release.sh inventory --app "/owned/HappyRanch.app"
bash app/mac/scripts/verify-release.sh trust --app "/owned/HappyRanch.app"
bash app/mac/scripts/verify-release.sh native --app "/owned/HappyRanch.app" \
  --go-archive "/owned/run/scratch/source/app/mac/tsnet-bridge/libtsnet.a" \
  --link-map "/owned/run/diagnostics/swift-link.map"
bash app/mac/scripts/verify-release.sh catalog --app "/owned/HappyRanch.app" \
  --list-json "/owned/list.json" --validate-json "/owned/validate.json"
# All values below are independently retained invocation arguments/tokens,
# not discovered from the candidate. Each option occurs exactly once.
bash app/mac/scripts/verify-release.sh licenses \
  --source "$REPO" --exported-source "$OUTPUT/scratch/source" \
  --dist "$OUTPUT/scratch/source/web/dist" --app "$OUTPUT/scratch/HappyRanch.app" \
  --output-dir "$OUTPUT" --evidence-dir "$OUTPUT/diagnostics" \
  --iconset "$OUTPUT/scratch/AppIcon.iconset" --mapping "$OUTPUT/diagnostics/license-mapping.json" \
  --source-sha "$SOURCE_SHA" --source-tree "$SOURCE_TREE" --version "$VERSION" --build "$BUILD" \
  --arch arm64 --run-id "$RUN_ID" --asset-context-sha256 "$CONTEXT" \
  --toolchains-sha256 "$TOOLCHAINS" --iconutil-path "$ICONUTIL_PATH" --iconutil-sha256 "$ICONUTIL" \
  --asset-source-sha256 "$SOURCE_TOKEN" --asset-dist-sha256 "$DIST_TOKEN" \
  --asset-app-sha256 "$APP_TOKEN" --asset-icon-sha256 "$ICON_TOKEN" --asset-notices-sha256 "$NOTICES_TOKEN" \
  --freezer-context-sha256 "$FREEZER_CONTEXT" --freezer-inputs-sha256 "$FREEZER_INPUTS" \
  --freezer-output-sha256 "$FREEZER_OUTPUT" --freezer-copy-sha256 "$FREEZER_COPY"
```

Resource expectations are the accepted 29-member source baseline, with regular
file type, mode, size and hash. Catalog expectations compare every list field
and validation key, replacing only the relocated app prefix. Capture actual
frozen CLI `skills catalog list --json` and `validate --json` outside checkout,
without root/policy overrides; exit zero alone is insufficient. Ordinary-reader
execution stays explicitly UNEXECUTED.

After all prerequisites, `ditto -c -k --keepParent` creates
`HappyRanch-VERSION-BUILD-arm64.zip`. `archive --app APP --archive ZIP --run-id ID`
compares full ZIP member bytes/modes/targets against canonical app inventory and
emits exact inner ZIP size/hash/source/tree/version/build/CPU/run/filename.
`reconcile --archive ZIP --receipt JSON --version VERSION --build BUILD
--source-sha SHA --source-tree TREE --arch arm64 --run-id ID` independently
compares these identities and archived plist. Named mismatches retain false/null
availability. An Actions wrapper is distinct from this inner ZIP.

App and ZIP inventories use identical relative-path string ordering and compact
UTF8 JSON with sorted keys plus LF, with every
relative path/type/mode, file size/hash and symlink target, including hidden
signing resources. Transforming the app changes its inventory. A later custodian
must retain separate signed-app, notary submission ZIP, accepted UUID/log,
stapled-app/ticket and final ZIP identities; old unsigned receipts cannot be
relabelled as signed/final. `reconcile --transform-chain JSON` also compares supplied signing/submission/
notary/staple/final relations against actual inner ZIP bytes and inventory,
refusing stale/foreign stage bindings. Stage objects are `unsigned_app`,
`signed_app`, `submission_zip`, `notary`, `stapled_app`, `final_zip`; each carries
source_sha/source_tree/version/build. App stages carry inventory_sha256;
signed_app links unsigned_app_inventory_sha256 plus signature_verified/team_id/
certificate_fingerprint; submission_zip links signed_app_inventory_sha256 plus
size/sha256; notary carries Accepted status/uuid/log_sha256 and submission_size/
submission_sha256/signed_app_inventory_sha256; stapled_app links signed inventory
and notary_uuid plus validated; final_zip links stapled inventory plus measured
filename/run_id/size/sha256. The receipt role is final-inner-zip. This validates
supplied relationships only: it does not authenticate signatures or notary logs,
perform transformations, accept public availability or replace independent
custodian/install/download evidence. Parent website admission remains separate.

## Evidence limits

Manual/tag workflow success uploads only the exact ZIP and receipts. Failure
uploads a finite diagnostic allowlist, never an app or success-labelled ZIP.
All full local logs and scratch stay in the private run directory until the
venue owner disposes of it. No added secrets, permissions, signing automation,
PR/main build/test triggers or automatic release publication.

Source-only/synthetic packaging checks cannot prove native execution. Independent
QA still owns C01/C04–C08 native assertions, C09 frozen catalog, C10 actual notice
closure, C11 native archive, C12 real bundled daemon/CLI/web smoke, C13/C14 display
and process assertions, C15 minimum OS, and C16–C18 signing/public receipts as
separately authorized. R2 managed Start/quit bounds are 60s/15s; R3 identities
remain distinct. Q1–Q13 remain UNEXECUTED/deferred. General integration is
SKIPPED THR243seq42; Python units are RETIRED. Automated merge checks remain
lint/static only THR291seq69, with normal hooks, independent review/QA and
manager guarded merge. No self-review, QA verdict or release acceptance follows.

## Freezer operation custody

The full app wrapper opts into the private `freezer_evidence.py` contract. Before
the ordinary frozen sync it verifies the exact committed Darwin input described
in [vendor provenance](vendor/pyinstaller-6.21.0/README.md), captures source and
asset/toolchain bindings and reserves an exclusive response directory. Other
platforms keep registry PyInstaller 6.21.0. The shared `build_daemon.sh` command
is unchanged. Standalone freezer work has no app-custody token.

The spec retains each Analysis/PYZ/EXE generation before later lanes can replace
it. PYZ bootstrap selections and redirects, EXE raw inputs, both dependency
lists and every shared Analysis occurrence survive normalization. Equal copies
can share a physical destination while preserving distinct contributor edges;
bytes/kind conflicts, missing edges and physical aliases refuse. Ordered options
remain ordered. Generated equal bytes can refer to different retained generations;
a changed live generation cannot silently replace the retained source bytes.

The two patched upstream members observe native return/cache/outcome/copy and
bootloader/copy/PKG-append/final-EXE operations at their actual consuming sites.
Only effective strip=False/UPX=False is supported for custody. Pinned CONF UPX
availability is a literal Boolean, with the bounded force environment string
recorded separately. Constructors, pre-guts/assemble and processing entry/return
recheck admission. Cache hits need the genuine same-run compatible predecessor;
non-Mach-O and optional-architecture passthrough remain distinct outcomes.
Unopted upstream callers retain their existing return types and semantics.

Caller-held digests bind context → inputs → output → app-copy. Early fragments
have null ledger references. Records and snapshots are exclusive, failures
persist where writable, and a partial attempt cannot return a success token.
The wrapper surrounds its existing cp-R with complete two-sided censuses; only
the existing tracked-skills chmod policy may change modes. At `licenses`, the
independent reader requires all caller tokens, reopens originals and generations,
and independently parses bounded PKG/PYZ/base-ZIP membership. Code payloads stay
opaque; the finite marshal decoder accepts only TOC data forms, never code.

Freezer limits are additional to unchanged asset-helper limits: container512MiB,
native256MiB, ordinary source16MiB, notice/manifest256KiB; decoded member16MiB
(native256MiB), container expansion512MiB and ratio1000. Aggregate reads8GiB,
snapshots2GiB, records32MiB, logical members/operations16,384, tree8,192/depth24,
names8MiB, alias hops16. Each success record is at most8MiB, context64KiB,
response4KiB; the reused JSON parser's stricter depth16/100,000-node fence also
applies. Overflow refuses; these caps have not been calibrated by a native run.
Container snapshots are hashed in chunks on held stable descriptors; archive
parsing uses bounded ranges and expansion, without executing or extracting code.
Both application and upstream snapshot writers open the ordinal leaf relative to
freshly admitted destination descriptors, retaining ancestry checks until after
streaming, fsync and close. ZIP metadata admission precedes ZipFile allocation:
fixed EOCD/central/local headers, actual entry counts and cumulative names are
bounded first. The small pinned base-library format supports single-disk stored
or deflated entries; ZIP64, split archives, data descriptors and archive comments
refuse. Central extras/comments and local extras share an 8MiB per-archive cap;
extra fields must be structurally complete. Existing payload CRC, expansion,
duplicate, local-range and complete-coverage checks remain independent. Logical
member/name budgets are charged once in admission, cumulatively across archives.
The Darwin input is custody revision 2; historical revision 1 receipts describe
only their original input and head.

Technical matches cannot complete PYTHON_NATIVE or BOOTLOADER_EXCEPTION_MAPPING.
Compilation associations remain `declared_compile_origin`/`origin_unobserved`;
installer custody and terms applicability remain unresolved. All six sentinels,
literal closure False and ordinary failed rows remain. Portable source/parser
checks are distinct from actual Darwin configure/target/transform/cache/EXE/copy
and full wrapper/licenses integration, which remain N-only UNEXECUTED. No build
allocation, signing, release, public distribution or deployment follows.

## Generated web observations

The app wrapper requires `--web-tool-originals /absolute/admitted-directory`.
This is an inert, disjoint input directory with exactly fourteen original npm
archives named `<package-with-slash-as-underscore>.tgz`. Originals, installed
implementations and complete notices are authenticated; the original Vite
LICENSE.md includes its bundled terms. `web/vendor/vite/prepare.py --original
/path/vite.tgz --check` independently reproduces the deterministic local repack.
Only the reviewed CSS/shared asset sites and package version differ from
Vite 5.4.21. Ordinary callers retain the original Tailwind plugin.

After ordinary npm postinstall, the wrapper authenticates the selected esbuild
platform member and its optimized alias. Only the exact accounted two-link pair
can be separated by exclusive copy and replacement of the alias entry. Both
paths must subsequently remain independent, single-link, authenticated files.
No other overwrite, alternate binary or install-script suppression is allowed.

A fully bound packaging build selects `packaging-tailwind.ts` and explicit
PostCSS `plugins=[]`. Content comes from regular pinned Git blobs under web/
with extensions html/js/jsx/mjs/cjs/ts/tsx/mts/cts/vue/svelte/astro/md/mdx/json/svg,
excluding vendor/, node_modules/ and dist/. Tests/stories are included, in UTF8
path-byte order. The adapter passes guarded replacement-decoded content and
extensions to Scanner.scanFiles, preserves candidate Set order, invokes one
compiler.build and one whole node.optimize, and returns that result to Vite.
Imported CSS uses finite loaders; module/config loading, filesystem source
instructions, ambiguous/relative imported URLs and unsupported CSS profiles
refuse. This input selection needs matched ordinary-build CSS/UI comparison;
it is not asserted equivalent to automatic scanner discovery.

Private public-hook envelopes, HTML/core snapshots and original patched CSS
operations retain actual argument/result associations through genuine emission,
final bundle and write. CSS chunk, URL and minifier chains follow their recorded
enclosing render call across interleaved emission-queue waits. Unavailable core
internal references stay null. Each CSS finalization consumes exactly its own
pending minifier result once; disabled minification consumes none, and unused
minifier operations refuse even when their bytes match. Each asset-cache store
consumes a pending read
under the same enclosing hook; interleaved reads retain their separate parents.
Cache hits require a stored URL, and incomplete read/store histories refuse. Python
publish/read stages validate caller-held context/preparation/build/dist/app
bindings, original bytes and finite relation graphs. Exact stat integers never
round-trip through JavaScript. The final observer passes the actual successful
publish token to synchronous `complete-build`. That helper independently checks
the original caller/context/preparation and complete live build, exclusively
writes `web-completion.jsonl` under the held evidence directory, fsyncs/closes
and reads back, then echoes the SAME canonical 555-byte completion line. The TS
observer compares raw bytes to its original direct inputs and actual publish
token before acknowledging completion. Only actual npm lifecycle exit 0 enters
`accept-build`; a later postbuild failure defeats even a valid marker. Missing,
foreign, malformed, duplicate or unsafe completion leaves diagnostic evidence
and no accepted build. Existing attempts/markers are never deleted for retry. Generated/public collisions refuse after npm exits and before
app-copy acceptance, including equal-byte collisions. Existing asset census and
map removal remain the copy authority.

`web-notices.json` retains the canonical digest of every existing component row
and records appended candidate notice rows. It cannot clear an ordinary failure,
sentinel, or literal closure False. A fresh licenses command independently runs
the web reader before the existing ordinary/global license gate. Unsigned source,
portable/component/browser observations establish only their measured local
scope. They establish neither legal applicability nor actual Darwin/native
custody, any of the six full closures, or release availability.

Packaging compilation retains the ordinary Tailwind plugin's `AtProperty` and
`ColorMix` polyfills (the pinned compiler's combined value `3`); both independent
readers require that recorded option.
The existing custody observer records Rollup's public final `moduleParsed` code
for every module, including profiles with no ordinary handler. Publisher and
separate fresh reader require that callback and its final transform-chain match.

The private `web-private-plain@2` resource profile retains all null and non-null
observations: 262144 rows; 32768 each for modules, subjects, CSS operations,
snapshots, selected content and candidates; 262144 call/association memberships;
4096 outputs, 256 plugins, 1024 owners and 32 notices per owner. Input JSON is
96 MiB, the complete envelope 112 MiB with at most 16 MiB added, build lexical
nodes 12582912/depth32. Aggregate UTF8 name occurrences are limited to 8 MiB,
including repeated names; each metadata string remains limited to 4096 bytes.
Other record byte/node caps stay unchanged. Every body
capture, including repeated equal bytes, contributes to the independently
recomputed 1024 MiB producer-work limit; this is separate from filesystem I/O.
Bounded canonical emission avoids only a redundant producer self-parse; complete,
accept, seal and fresh verify independently parse/reopen their actual inputs.

The writer and every reopened build reader apply the same bounded canonical
input-size and actual envelope-minus-input addition check. An over-limit stored
registry is refused before relation validation, including during fresh verify.

Each web command owns one fixed 512 MiB regular / 192 MiB JSON budget and ONE
persistent 512/32 MiB asset submeter. Successive/nested native asset lanes share
that submeter; nested bounded calls keep the active view. Attempted charges stay
monotonic, including refused work and repeated reads/parses. `read_closed_json`
charges regular N / JSON N; web `Session.load` conservatively charges regular
2N / JSON N. Counters are read-only; concurrent census accounting is shared and
unchanged. Default asset and independent freezer limits remain unchanged. There
is no numeric override, inner reset, repeated-read exemption or whole-npm I/O
ceiling. Limits are conjunctions: path4096/component255/depth16 admits at most
4095 path bytes, and the closed completion body is exactly555 bytes, so neither
has a reachable valid4096-byte semantic positive. Capacity/headroom requires
actual scoped measurements; source/static success establishes none of it.


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
