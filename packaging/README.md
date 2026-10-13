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
bash app/mac/scripts/verify-release.sh licenses --app "/owned/HappyRanch.app" \
  --mapping "/owned/license-mapping.json"
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
