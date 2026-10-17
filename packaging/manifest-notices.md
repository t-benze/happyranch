# Mac manifest-notice contract

Founder THR104 seq260/266 replaces the former six-closure prerequisite with
resolved dependency-manifest license notices. No linker implementation study,
per-byte attribution, legal certification or former closure result is asserted.

The collector `app/mac/scripts/manifest_notices.py` runs after the real freezer,
Go and web assembly. Its two package files live in `Contents/Resources/licenses`:

- `resolved-inventory.json`: full npm lock plus installed metadata/emitted owner
  selection; full resolved Go modules and target dependency packages; full uv
  lock plus installed distribution metadata/RECORD census; concrete Python/Go
  runtime and Go vendor owners; independent public/font/icon source census;
  original associations and source-manifest digests.
- `manifest-notices.json`: schema `happyranch-manifest-notices@1`, the inventory
  SHA256 and ordered component rows. Every row carries identity, version or
  source revision, manifest/source relationship, explicit inclusion/exclusion,
  and original/destination SHA256 associations. The maintained structural schema
  is `notice-data/manifest-notices.schema.json`; the stdlib reader additionally
  checks semantic membership, source relationships and actual bytes.

Membership is recomputed without using the supplied component rows. Python's
spec snapshot is cross-checked against a fresh installed metadata/RECORD census.
All installed non-project Python distributions are conservatively included,
including build tools; unselected uv marker/group/version alternatives remain
explicit. npm production and emitted packages are included; installed build-only
and absent optional platform packages remain explicit. The existing generated-web
reader separately requires participating tool originals. Every resolved Go
module is listed; only target go-list dependencies need notices. GOROOT original
LICENSE/PATENTS and selected `src/vendor/modules.txt` owners are separate.
All 293 installed production npm owners have original texts in the maker census;
twelve missing-package-text Radix owners use exact authenticated recoveries.
The two additional rect/use-rect identities match registry integrity/gitHead,
installed metadata/module bytes and published-source-map/upstream source bytes
at the same original LICENSE revision; no nearby-version substitute is used.
Multiple versions/locations retain separate rows, even when original text bytes
are shared. All discovered applicable original texts remain associated; a
nonempty one-text subset cannot replace the measured original set.

Fonts retain the pinned authentic revision and co-version OFL. Public bytes are
checked in the candidate and ZIP. The icon source census uses the first-party
original LICENSE and the existing asset reader still checks actual iconutil
transformation and app publication. Original source/asset/freezer/web custody
readers stay independent; no new observer profile or toolchain is introduced.
The Python snapshot binds the canonical library selected by the existing
PyInstaller `EXE.python_lib` in both daemon and CLI to the same hashed BINARY
input in each Analysis. That exact input (including standalone
`PREFIX/lib/libpython3.12.dylib` or a selected framework library) shares the
validated CPython original; no filename/prefix-only interpreter exemption exists.
Distribution RECORD ownership remains independent and stdlib `lib-dynload`
extensions retain their existing handling. Selected-library disagreement or
missing Analysis membership refuses. The resolved runtime inventory records the
selected library path/digest; original-text and native custody checks remain.
Concrete native files outside distribution RECORD/Python core are named in the
runtime inventory. Without an original-text association they fail with their
actual source path; they do not inherit another owner's generic license.

Texts must be nonempty UTF8, bounded regular readable single-link files with
safe ancestry. Empty/unreadable/missing originals, unsafe paths, source/destination
conflicts, digest/identity mismatches and missing owners refuse with remediation.
Candidate verification reopens live resolved inputs and original associations.
`archive-app.sh`, called by `build-app.sh`, repeats the packaged inventory check
immediately before the existing ditto command. The unchanged whole-ZIP member,
resource/mode, version/build, architecture and source identity checks still run.
The inner receipt now includes `manifest_notices` (schema, inventory/manifest
SHA256, owner/included counts); reconcile recomputes it from actual archived bytes.
Signing/trust, installation and public-release boundaries are unchanged.
Failure diagnostics add only the manifest and Python distribution inventories
to the fixed allowlist (52 input/106 output files); all byte/metadata budgets
remain unchanged and oversized evidence is explicitly reported incomplete.

All 29 resource members remain required. Eleven historical skill-resource
size/hash/blob pins are updated to their actual originals on fresh main7812caa6;
no resource source or mode requirement is removed. The existing native workflow
now supplies PR1101's required web-original directory: a new disjoint directory
with the same fourteen existing pinned npm tarballs, bounded acquisition and
lock SHA512/original Vite SHA256 verification. Workflow dispatch inputs, native
Apple tools and allocation controls are unchanged. Source delivery is no run.

## Finite manual verification

Run outside CI with Python3.12+ and a new task-owned scratch directory:

```sh
python packaging/verify_manifest_notices.py --scratch /absolute/new/task-scratch
```

It uses authentic retained PyInstaller/CPython/scrollbar/font/Go/UUID originals;
Go and UUID provenance is retained under `fixtures/manifest-notices`. The source
manifests, installed metadata/RECORD, EXE.python_lib selections, standalone
prefix and Analysis native bytes/rows, and candidate assembly are synthetic.
No actual selected native toolchain/sysconfig observation is claimed. The real
python_snapshot now produces the inventory consumed by the collector. Only ditto
is replaced at the shipping archive transition, by Python zipfile; all collector,
notice/resource/archive/reconcile code is real. No native compilation, full
asset/freezer/web custody run, daemon, retired unit suite or Product E2E runs.
General integration is SKIPPED THR243seq42.

| Requirement | Setup/action at production seam | Observable result | Level/environment |
| --- | --- | --- | --- |
| 1. Complete inventory archives | Real collector receives resolved npm/Go/Python and separate asset inputs with authentic originals; invoke shipping archive shell | Candidate contains texts, real ZIP and receipt produced; independent roundtrip succeeds | Portable preassembled candidate; ditto substituted |
| R1. Selected interpreter ownership | Real python_snapshot receives synthetic freezer-selected standalone library plus stdlib/RECORD inputs and real retained CPython original; collector/archive consume its output; add misleading vendor libpython/Python paths | One licensed Python owner, actual ZIP contains the original; vendors remain separate empty-original owners, collector and archive refuse | Portable synthetic freezer facts; no native toolchain execution |
| 2. Independent owner/text completeness | Remove each manifest-family/asset owner, required retained notice, or second Go PATENTS association | Nonzero, named owner/path, no ZIP creation | Disposable candidate only |
| 3. Fail closed on unsafe/mismatched text | Empty/whitespace/unreadable/changed text, identity mismatch, traversal and symlink | Nonzero, no outside mutation or ZIP | Disposable source/candidate only |
| 4. Preserve versions/selections | Two actual scrollbar source/derivative versions sharing original license, shared font OFL, optional/build-only exclusions | Separate identities and deterministic selections survive | Synthetic resolved inventory, authentic originals |
| 5. Archive/identity integrity | Complete roundtrip; remove/change archived text; change expected version/build/arch/tree | Complete passes, tampering and unrelated identity mismatches fail | Actual ZIP and shipping readers |

These four-answer rows describe requirement, setup/action, observable outcome,
and verification level. Evidence is the script's per-command argv/exit/output
JSON, not the table alone. No protected deliberate-defect or repetition proof,
native success, install compatibility, signing or public availability follows.
Independent full review and scoped QA must bind the final PR head; the delivery
root retains guarded merge and the reserved native allocation/result follow-up.
