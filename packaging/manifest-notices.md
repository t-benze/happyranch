# Mac manifest-notice contract

Founder THR104 seq260/266 establishes resolved dependency-manifest notices;
seq369 removes asset/freezer/web evidence and the packaging supervisor from Mac
build and verification. No linker/emitted per-byte closure is claimed. Ordinary
PyInstaller/Swift/Go/Vite compilation and the bundled runtime/catalog remain.

The existing collector `app/mac/scripts/manifest_notices.py` owns direct inputs:

- `inputs` reads the same fourteen pinned original npm archives supplied by the
  workflow. Bounded member reads verify SHA512 (upstream Vite SHA256), package
  versions and all original license/NOTICE texts without extracting arbitrary
  archive paths. The license-less esbuild/Rollup arm64 packages use only their
  authenticated same-version/repository/MIT parent originals; other missing
  originals refuse. It records direct Go toolchain originals and paths.
- `python_snapshot`, called by the ordinary spec, reads installed metadata/RECORD,
  the actual selected EXE.python_lib from both lanes and native binary inputs.
  The selected library must agree and be present in both analyses. Authentic
  installed CPython/toolchain originals take precedence. The CPython original
  may be at the base-prefix root or the selected sysconfig stdlib beneath it;
  the latter must match the pinned original bytes. Vendor texts cannot satisfy
  it. The prior exact pinned offline CPython fallback remains strict and is used
  only when both installed locations are absent. Unknown native vendors receive no
  inherited license and refuse collection. No observer lifecycle/IPC is used.
- `collect` reads full resolved npm/uv/Go manifests, installed npm/Python metadata,
  source-map package membership where available, known resolved generated-web
  tooling, Go target dependencies/vendor manifest, and public/font/icon inputs.
  Production/mapped/generated-tool npm owners and every installed non-project
  Python distribution are conservatively included. Unselected marker/platform,
  build-only npm and Go modules without target dependencies have explicit reasons.
  No supplied component subset controls inventory membership.
  The first-party Go replacement exemption is `happyranch/tsnethealth`
  replaced by unversioned `../../shared/tsnethealth`, with both go-list Dir
  values resolving exactly to the exported `app/shared/tsnethealth` directory
  under the source root. It produces no third-party owner row. Versioned or
  near-miss replacements and symlink escapes remain refused. The only
  third-party association is the client bridge's exact `tailscale.com v1.78.0`
  replacement at `./third_party/tailscale.com`, bound to the pristine module h1,
  committed patch SHA-256 and verified prepared tree. It retains the upstream
  owner and original notice bytes. See the
  [pin and preparation procedure](../app/mac/tsnet-bridge/third_party/README.md).
  Every other replacement or cache-escaping module remains refused. The
  inventory retains the source root for independent owner rederivation.
- The Swift owner family reads the exact Sparkle binary-target URL/version and
  checksum from `app/mac/Package.swift`. SwiftPM authenticates the official ZIP;
  the collector includes `swift:Sparkle` and the complete 2.10.1 release LICENSE
  retained at `app/mac/third_party/Sparkle/LICENSE`, including its four external
  licences. Both source files enter the input digest inventory. This is a direct
  binary target, so no git dependency or `Package.resolved` is involved.

Every included owner needs its complete discovered original set. Multiple
versions/locations remain separate identities. Installed texts are preferred;
known generated tools also retain all authenticated original archive notices.
The prior exact Radix metadata/module/original fallback is retained, without
an emitted-byte closure claim. Go LICENSE/PATENTS, target toolchain vendor owners,
CPython library/stdlib, distribution-native components, font OFL and first-party
icon/assets are associated separately. Unknown native owners refuse with path;
no generic notice substitutes for a vendor license.

The app embeds `Contents/Resources/licenses/manifest-notices.json`,
`resolved-inventory.json`, associated original `.txt` files, a readable
`DEPENDENCIES.txt` index, first-party LICENSE and retained README. The unchanged
schema family `happyranch-manifest-notices@1` describes manifest associations;
`notice-data/manifest-notices.schema.json` documents structural shape. The stdlib
reader rederives identity/membership/selection, original associations and digests.
Safe bounded nonempty UTF8 regular originals and safe destinations are required.
Missing owners/texts, unknown runtime owners, second-original omission, corrupt
or empty bytes, source mismatch and unsafe/aliased paths refuse nonzero.

`verify-release.sh licenses` recomputes direct live inputs and compares the
embedded inventory. `archive-app.sh` repeats packaged notice checks and basic
bundle checks before ditto. The actual ZIP is checked against the app's members,
resource modes, all notice bytes, nonzero size, SHA256 and effective release
identity. Reconcile checks the notice receipt and ZIP identity against the
requested source/version/build/arch/run. These are ordinary archive integrity
checks, not emitted code/asset provenance or legal certification.

Basic app checks cover actual plist executable/package/identifier, semver
version/build, source IDs, minimumOS14 declaration, required resources/licenses/
README, executable presence and actual Mach-O arm64 headers. Native launch and
physical Mac14 compatibility are distinct and unproven by headers/declarations.
Signing, trust and public-release boundaries are unchanged.

The existing workflow keeps Build45/job95 limits, exact one-candidate success
selection and separate bounded failure/cancellation diagnostics. No workflow
interface or definition change is required. Custom helper deadline/receipt/IPC
and per-byte closure requirements are retired; ordinary process status and the
runner's timeout/cancellation remain. Failure diagnostics keep their 53-input,
108-output caps, four critical files and existing byte/metadata caps; absent
retired records are honestly unavailable.

Run finite portable checks outside CI:

```sh
python3 -I app/mac/tsnet-bridge/third_party/prepare_tailscale.py --go /absolute/path/to/go
python packaging/verify_manifest_notices.py --scratch /absolute/new/task-scratch
python packaging/verify_mac_run_layout.py --scratch /absolute/new/layout-scratch
```

The notice command exercises the real collector, live licenses reader, archive
shell and independent ZIP/reconcile readers. Inputs are synthetic resolved
manifests/metadata/native selection, authentic retained originals and preassembled
resources/plist/icon/arm64 headers; ditto alone is replaced by Python zipfile.
The layout command checks actual workflow selectors/reservation, build reservation
and shipping preflight. Test-authoring four answers and attributable evidence
belong in the existing maker handoff/PR, not a separate proof framework.

Native app compilation, runtime acceptance, native subprocess cancellation/
cleanup and physical install are NOT RUN by the maker. No native success follows
from fixture/static results. Python units remain RETIRED, integration INCLUDING
callback SKIPPED THR243seq42; no ad hoc Product E2E/Jenkins/native dispatch.
ROOT owns independent review/QA, guarded merge and actual native/archive evidence.
