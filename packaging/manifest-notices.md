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
  installed CPython/toolchain originals take precedence; the prior exact pinned
  offline CPython fallback remains strict. Unknown native vendors receive no
  inherited license and refuse collection. No observer lifecycle/IPC is used.
- `collect` reads full resolved npm/uv/Go manifests, installed npm/Python metadata,
  source-map package membership where available, known resolved generated-web
  tooling, Go target dependencies/vendor manifest, and public/font/icon inputs.
  Production/mapped/generated-tool npm owners and every installed non-project
  Python distribution are conservatively included. Unselected marker/platform,
  build-only npm and Go modules without target dependencies have explicit reasons.
  No supplied component subset controls inventory membership.

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
runner's timeout/cancellation remain. Failure diagnostics keep their 52-input,
106-output and byte/metadata caps; absent retired records are honestly unavailable.

Run finite portable checks outside CI:

```sh
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
