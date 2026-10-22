## Manifest-notice shipping gate (THR104 seq260/266)

Current shipping standard and finite verification: [manifest-notice contract](manifest-notices.md).

The current standard is resolved dependency-manifest notices plus separately
bundled fonts, icons and assets. `app/mac/scripts/manifest_notices.py` enumerates
all resolved npm, Go and Python identities, including explicit target/build-only
exclusions. Original texts, owner associations and independent inventory
membership are checked in the candidate and ZIP. It does not claim linker or
per-byte license closure. Source/custody, resources, architecture, trust and
release identity checks remain independent. The former six-closure standard is retired without claiming its proof.

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
and no symlink ancestors. Before uv writes, the freezer uses the existing
no-follow ancestry/FD checks (root/current-user ancestors, no group/other write,
uid-owned allocation roots) and exclusive directory reservation. It requires
an absent exported-source `.venv`, including no dangling symlink, and reserves
private mode0700 `python-install`, `uv-cache`, `tmp` and `pyinstaller-cache`
children under the new work root. Paths with spaces remain supported.

`uv python install --no-bin` requests **CPython 3.12.10** into the admitted
`UV_PYTHON_INSTALL_DIR`; no user/global executable link is installed. Discovery
is managed-only with automatic downloads disabled and must return a safe,
owned executable within that root. Before sync, its actual executable must
match that concrete selection and report CPython 3.12.10. Raw prefix and base
prefix must agree in the private installation, with raw stdlib beneath that
base; every raw root is admitted through the existing no-follow ancestry/FD,
owner/mode/device and identity rechecks before physical executable comparisons.
The admitted raw base prefix remains the expectation for the project venv.
Frozen sync and both run commands share explicit absolute project/directory,
patch-bearing `--python cpython@3.12.10` and managed-only/no-download arguments, with
`UV_PROJECT_ENVIRONMENT` fixed to exported-source/.venv. Both runs disable
dotenv loading. The patch-bearing request retains fixed venv upgrade semantics;
the separately validated concrete executable remains the physical identity
expectation. Before writing the four-field `python-toolchain.json`, the actual
venv must pass executable/version/platform/machine checks, raw prefix must equal
absolute exported-source/.venv, and raw base prefix must equal the admitted
base. Raw venv/base/stdlib roots are admitted within source/install/base,
respectively, before resolved executable equality is considered. Directory
aliases are refused; permitted executable leaf links retain the separate later
`executable_alias` checks. Earlier admission grants no later root/toolchain
exemption or same-UID race guarantee. The receipt still records only the actual
venv executable/version/platform/machine. The freeze run uses the same venv
`python -I` command via `-m PyInstaller`, avoiding console-script shebang aliases. The vendored Darwin wheel and frozen
lock are unchanged; sync/run keep project source configuration enabled.

Both build entries refuse inherited `UV_*` controls before their first uv
invocation, except `UV_CACHE_DIR` (replaced by an admitted allocation),
`UV_OFFLINE`, `UV_PYTHON_DOWNLOADS`, `UV_NO_PROGRESS`, and `UV_PYTHON` only
when absent or exactly the literal `3.12` emitted by the pinned workflow caller.
Present-empty, path, patch, alternate or whitespace selectors refuse without
printing values. After the entire inherited-environment check succeeds, each
parent shell explicitly unsets `UV_PYTHON` before any uv invocation or wrapper
handoff. This compatibility input grants no interpreter-selection authority;
the fixed private CPython request and absolute managed executable remain binding.
This closed rule covers project/install/bin/interpreter/config/directory
redirects, no-sources/package
modes, custom mirrors/catalogs and unknown future controls. Inherited `PYTHON*`
controls and a mismatched `VIRTUAL_ENV` also refuse, without printing values.
Offline or non-automatic Python-download settings refuse a fresh install;
no prohibition is converted into network permission. Discovered `uv.toml`
overrides (source/ancestors, home, XDG and system locations), unreadable
configuration and changes to the reviewed `[tool.uv.sources]` configuration
refuse before installation. Every ancestor above the source through `/` must
also have no `pyproject.toml`: any presence (unrelated, malformed, oversized,
unreadable, directory or symlink, including dangling) or inspection error
refuses using the pinned ancestry descriptors, before freezer dist/work
reservation, install or sync. Only the source's own project is admitted through
the exact frozen uv-settings check. This preserves local wheel selection
without
silently loading ambient user/system settings. Use a disposable build venue
without those overrides; the script never edits external configuration.

The full-app wrapper validates its own private temp/uv-cache roots before its
early `uv --no-config --version` read. Ancestor-project admission occurs later
in the freezer: the wrapper has already reserved scratch, exported source and
read the uv version. Its existing finish diagnostics apply to that refusal;
early inherited-environment refusal precedes the wrapper's finish trap.
The freezer always selects its own
admitted work/tmp for standalone and full-app use, overriding inherited TMPDIR.
`PYINSTALLER_CONFIG_DIR` confines `--clean` to the fresh work/pyinstaller-cache.
Allocation, install, discovery, identity or sync failure stops nonzero without
ambient retry or later freeze/success. Work and caches remain with diagnostics;
the venue owner disposes only of exact invocation allocations after evidence
retention. No shared framework is chmodded, chowned, copied or repaired.
Both daemon and CLI must be
executable. Separate daemon/CLI PYZ archives share the byte-checked union of
both native/data closures. Destination collisions refuse instead of dropping
CLI-only payloads. `analysis.json` records actual pure/binary/data rows and CLI
set differences, including empty differences. No missing-module bug is claimed
from source inference; no removed runtime modules or retired units are restored.

This repair has source/static evidence only. Actual managed installation,
external-byte preservation, three-stage interpreter selection, new-distribution
originals/fallback eligibility/library owners, freeze/app/ZIP, launch and native
failure transitions remain **UNVERIFIED**, owned by engineering_manager. Risks
include repeat fail-closed selection, freeze or finalization refusal; an
uncontrolled external write remains a source defect requiring correction.
No new native run, helper/test harness or release acceptance follows.

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
effective toolchain and three stdlib hashes in both Analysis lanes; the twelve
Radix fallbacks require pinned metadata and every actual emitted source hash.
Unsafe paths, corrupt inputs and conflicts refuse retention. A fixed 3.12.10
request does not establish the old fallback's full build/platform/machine or
stdlib/Analysis hashes. Unsafe/dangling installed originals never become
absence fallback. Notice errors are caught by the spec as LICENSE_MISSING;
Python-library disagreement can fail at python_snapshot, while a matching
snapshot with a missing original (including a separate bundled library owner)
is refused by final collect. The wrapper preserves the original failure and
available=false rather than completing verification/archive/success. The web dependency
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

The former six unresolved labels and always-false closure flag are retired.
The manifest-notice gate allows a complete original-backed inventory to reach
ZIP creation. Scoped non-native checks do not establish legal certification,
actual native custody or Mac acceptance.

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
full reader; there is no partial-success reader. The mapping reader preserves
concrete failed components and independently verifies the manifest-notice
inventory. The former exact-six-result/sentinel contract is retired.

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

Source delivery and portable fixtures do not establish actual native PYNOTICES
append/token behavior, full wrapper/licenses execution or native custody.
Mac14/WKWebView/install remain UNEXECUTED. Native execution is not a source-review
prerequisite; the parent retains changed-source/input/custodian disposition.
No former six-closure claim is asserted.

Failure diagnostics include
`web-attribution.json`: first 100 rejected records, string fields capped at 512
characters and serialized records capped at 2048 bytes, with explicit
omitted-record/field truncation. Concrete unresolved owners/texts remain errors;
complete manifest inventory and retained originals pass the new shipping gate.

The failure bundle preserves complete small files (up to 262144 bytes) as
`.diagnostic` and larger complete files as `.diagnostic.gz`, with no JSON byte
tails. A fixed 50-input allowlist covers available Analysis/daemon and CLI PYZ
TOC descriptors, native/data union, Python/toolchain, Go inputs/members, link
map and component/notice provenance plus per-command CLI/catalog/trust results.
Inputs are capped at 8 MiB each and 32 MiB total; stored bulk is capped at
2 MiB each and 8 MiB total. Three independent critical files (build failure,
native failure and all eleven symbol observations) each have a 262144-byte
budget. At most 106 files and 128 KiB of metadata describe source hashes/sizes,
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
QA still owns C01/C04–C08 native assertions, C09 frozen catalog, C10 actual manifest
notices, C11 native archive, C12 real bundled daemon/CLI/web smoke, C13/C14 display
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
platforms keep registry PyInstaller 6.21.0. The shared `build_daemon.sh` command retains that custody handoff while
binding the private managed interpreter described above. Standalone freezer
work has no app-custody token.

Before `spec_begin` or either Analysis, the custody-only spec setup freezes the
fixed `/Library/Developer/CommandLineTools` developer selection. Only the resolver
child environment overrides `DEVELOPER_DIR`, preserving the original PATH and
other settings. Caller-selected `DEVELOPER_DIR` is not consulted and
`xcode-select` is not queried. Exactly two sequential
literal `/usr/bin/xcrun --sdk macosx --find` queries select `lipo` and
`install_name_tool`. No host selection/cache change, candidate execution, retry,
alternate installation, copied tool or PATH fallback is permitted.

Each configuration query has a 10-second deadline, streaming combined
stdout/stderr limit of 8192 bytes and stdout limit of 4096 path bytes plus one
optional LF. Both pipes are drained without unbounded capture; failures kill
and reap only the owned child, with bounded discard/descriptor cleanup. Exit,
timeout, output, UTF8 and path failures report bounded role/category errors,
without dumping stderr or environment. Paths must be single, nonempty, strict
UTF8 absolute spellings, without controls, colon, empty/dot/traversal components
or existing component/length violations. The developer string must have the exact
raw spelling `/Library/Developer/CommandLineTools`; each candidate must have the
exact raw spelling `/Library/Developer/CommandLineTools/usr/bin/<role>`.
Dispatchers, aliases, other SDK/toolchain/installation paths and alternative raw
spellings refuse; path shape alone establishes no safe filesystem attributes.

`spec_begin` validates the transient closed selection again at its native stage,
after the unchanged caller/context/platform, prefix/base-prefix/stdlib/usr-bin
root, installed-wheel, receipt and Python checks. It admits only each candidate's
immediate bin parent, checking each occurrence before deduplication through the
unchanged `RootHandle` ancestry/owner/mode/device/identity predicates. At most two
distinct roots are added, retaining cap16 before native snapshots and Analysis.
Neither `/Library`, the Developer/CLT tree, SDK nor `/` becomes a general
read root. Native snapshot order stays codesign, lipo, install_name_tool;
codesign remains literal `/usr/bin/codesign`. The two discovered leaves must be
physical executable files with no alias chain, and still pass all original
single-link/type/uid/mode/no-follow/nonblocking/device/size/budget/identity guards.
Unsafe ancestry and same-descriptor leaf refusals retain their original notes.

After all native snapshots succeed, ordered deduplicated candidate parents
(lipo then install_name_tool) precede the complete original PATH. Both
`shutil.which` results must equal the discovered paths before activation;
cross-shadowing, disappearance or fallback refuses before Analysis. Any
tentative PATH is restored on refusal. Only this PyInstaller process receives
the PATH, for both lanes through PYZ/EXE/shared COLLECT. Original vendor lipo and
install_name_tool PATH argv inherit that selection; the wheel remains unchanged.
The parent wrapper, later web/Go/Swift and frozen CLI inspection keep their own
environments, including the active Xcode and outer SDK selection. Standalone
custody-absent setup performs no discovery or PATH change and retains its ordinary
unobserved tool selection.

Existing native rows keep their keys, role order and `selection_observation`
relation, toolchain `origin_unobserved`, expanded root propagation and four
caller-held digests. Fresh accept/before-copy/copy/licenses readers re-admit the
recorded roots and reopen selected paths, alias identities and live/snapshot
subjects; they never rediscover via xcrun or their own PATH. Final
`verify-release.sh` PATH lipo inspection is separate and unchanged. Discovery
and intended invocation alignment attest neither an executed kernel image nor
publisher identity, and provide no same-UID replacement/restore guarantee.
Actual CLT ancestry/leaf suitability and command compatibility, query
bounds/cleanup/races/negative behavior, both-lane completion and fresh native
readers remain UNVERIFIED until the
root-owned shipping run. Successful source/static checks or discovery establish
no app/archive/receipt or native acceptance.

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
Unopted upstream callers retain their existing return types; the canonical
base-library producer ordering applies with or without custody.

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
(native256MiB), container expansion512MiB and ratio1000. Aggregate ordinary
reads8GiB, snapshots2GiB, logical members/operations16,384, tree8,192/depth24,
names8MiB and alias hops16 remain independent. Fixed internally selected inputs
and output profiles admit32MiB each with BOTH lexical/object1,000,000 nodes;
context stays64KiB and copy/default8MiB, with smaller site caps retained. All
retain depth16/string4096B/strict UTF8/duplicate/nonfinite/type and Boolean-field
checks. Object preflight precedes bounded incremental canonical JSON encoding;
sorted keys, separators, ASCII escaping and final newline stay unchanged, along
with all complete occurrences/order/IDs/lineage and digest-bound references.
One enclosing attempted helper JSON-I/O meter256MiB per command/spec Session
replaces records32MiB write/load-only accounting. Every write and mandatory
readback, load, full reference reread and standalone/nested fragment read is
charged before attempted work, without path discounts or nested accept/verify
reset; exhaustion is sticky and failure persistence remains best effort. Write
readbacks reserve the exact written length plus an EOF probe under an exact-length
ReadDomain within the fixed kind cap; a partial write does not refund reservations.
Fragment JSON reads also charge ordinary reads. Copy references stored output
bytes rather than reserializing under a default cap. Responses retain4KiB.
The current complete call graph bounds are224MiB/spec and232.125MiB/fresh verify
with copy, plus small records/EOF probes. These formulas are not measured native
totals or hard RSS/time bounds. Overflow still refuses; build13's rejected input
size/content remain UNKNOWN, and these finite capacities do not guarantee success.
The independent vendor COLLECT ledger profile is32MiB with lexical/object1m,
depth16/string4096 and original types/duplicates. Integer declared size, original
guarded observation, bounded cap+1 full-read size/actual consumed digest, ordered
collect arguments and stable-ledger reobservation all agree. Both reads and the
final stability reread charge its existing8GiB meter. Vendor default parse and
fragments stay8MiB each/32MiB per-run writes, with snapshots2GiB and members16384;
asset and vendor budgets remain separate. Unrelated freezer RECORD_CAP uses,
including marshal/PYZ TOC and ZIP metadata, stay8MiB.
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
The ZIP reader borrows the held snapshot mmap through a read-only seekable file
interface compatible with CPython3.12 ZipFile, supplying read/seek/tell/seekable
without a whole-container copy or path reopen. Reads cover ZipFile's admitted
metadata and bounded member chunks; seek returns the actual mapping position.
The interface closes before the enclosing snapshot context releases the mapping
and stable descriptor; it does not close either owner. Bytes inputs retain the
BytesIO reader, and metadata admission still precedes either reader's allocation.
The Darwin input is custody revision 5, build tag `1happyranchcustody5`, pinned
through the local wheel, provenance, lock, wrapper and reader contract. Historical
revision 1/2/3/4 wheels and receipts retain only their original input and head.
Revision 5 adds only the missing `api.py` module-global `hashlib` binding for
the existing COLLECT ledger digest; its reader and limits are unchanged. Build14
failed at this binding after both EXEs built, before app inspection. Custody5
build15 (`39518819733`, attempt1, input
`a9970abb52e3a444c7172674a1eca360a08f8c00`) completed both EXEs and COLLECT,
then failed in `finish_freeze` / `archive_observations` / `zip_members` when
CPython3.12.10's ZipFile member reader requested the mmap's missing `seekable`.
The secondary `FREEZER_FAILURE_PERSISTENCE:FileExistsError` remains diagnostic.
Native acceptance of the seekable-interface repair remains UNEXECUTED;
source/repack evidence and build15's earlier success do not establish a Mac/package pass.
Revision 3 sorts base-library modules by name at the original ZIP producer before
each original lane observation. It preserves module membership, cached-code
lookup, optimization, headers and deterministic ZipInfo. Equal content no longer
depends on traversal order; equal module tuples alone do not prove equal cached
code objects. Both original Analysis captures, PYZ/EXE lanes, shared contributor
occurrences and fresh readers remain. Exact-byte and kind collisions, missing
inputs and live generation drift still refuse; no captured ZIP is rewritten or
later generation substituted. Build12's exact changed subject and bytes remain
unknown. Guarded deterministic wheel reproduction validates source repack only;
changed-helper Darwin compatibility, actual equal lane ZIPs and mismatch/fresh-reader behavior
remain UNEXECUTED/UNVERIFIED. App/archive/receipt and final manifest-based notices
remain NOT_REACHED.

Compilation associations remain `declared_compile_origin`/`origin_unobserved`.
Technical matches do not certify legal applicability. Manifest-selected
PyInstaller originals include its original exception terms; unknown concrete
native vendors require their own original association. Former six sentinels
and literal closure False are retired; concrete failed rows remain. Portable source/parser
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

Only ordinary npm ci runs in a subshell with umask 022, preserving authentic
0755 package executables. The parent mask stays 077 on success or failure;
output, scratch and source directories are created privately before installation.
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
and records appended candidate notice rows. It cannot clear a concrete failure.
A fresh licenses command independently runs the web reader before the original
text checks and manifest membership validator. Unsigned source,
portable/component/browser observations establish only their measured local
scope. They establish neither legal applicability nor actual Darwin/native
custody or release availability; former six full closures are not claimed.

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

Packaging helper invocations use the finite deadlines and held-direct-child
lifecycle in `app/mac/scripts/packaging_supervisor.py`; see the named-helper
table and ownership limits in [the Mac guide](../app/mac/README.md#named-helper-deadlines-and-owned-cleanup).
Token/fail operations are30s, small web operations120s, web prepare/begin300s,
and the enumerated larger helpers/notices600s. Existing per-tool60s bounds stay;
verifier modes are300s except licenses600s, and frozen diagnostics have one600s
outer. Internal failure persistence is30s clipped to the original clock only
when an actual supervising owner is present. PyInstaller imports/freezer body,
npm producers, compilers and archive compression remain workflow-only.

The outer cleanup allowance is one5s window; Node's D+10 KILL fallback and
Bash's same-module guardian/D+12 observation slots are independent fallbacks.
Only the actual parent with a positively unreaped handle can signal its group;
registration never transfers kill authority, and no signaling follows reap.
Bash preserves stdin/heredocs and admits only successful complete replies.
Finish preserves the original failure/signal while attempting secondary
persistence; tee finalization is10s after writers close, with the original log
and stdout sinks. Deadline124, cleanup-only125 and actual failures/signals stay
distinct. Parent/control loss and escapes retain UNKNOWN cleanup. Neither these
allowances nor the750s ordinary/780s fallback single-helper-plus-finish totals
are total-build/hard OS guarantees or native acceptance. Preflight may defer
parent traps for300s+5s; a late one-shot marker never overrides failed status.
No validation cap, diagnostic grant, notice/source selection or complete-build
context/reopen accounting changes, and no native stall/cap-fix claim follows.
