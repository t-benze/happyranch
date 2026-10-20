# PyInstaller custody input

This internal repack retains distribution/version `pyinstaller 6.21.0` and uses
wheel build tag `1happyranchcustody3`. It is not an upstream release. The source
changes include HappyRanch custody revision 3, dated 2026-10-20. A later
content revision requires a new build tag/path, identities, lock and review.

The original wheel is 1,052,350 bytes, SHA256
`327d132389f37912609e01be62810cf96b5aa95b613903e4b8692e0d12fb0eda`, retained
from TASK-11500's authenticated original. `original/COPYING.txt` is its exact
32,138-byte notice (SHA256
`dcf75fdb959db1e3b41c0f8505069d2ece781b5ec6b3d0a4d30975cfc6580245`). All original
notice members remain inside the original and all retained replacement wheels.
These identities make no new legal terms or applicability determination.

`custody.patch` changes only `PyInstaller/building/api.py` and
`PyInstaller/building/utils.py`. PYZ/EXE/COLLECT retain actual ordered selections,
generations, normalization edges, native return/copy results and EXE operations.
Revision 2 holds freshly admitted destination ancestry through descriptor-relative
snapshot creation, streaming, fsync and close. The opt-in data contract is closed; unsupported effective strip/UPX options
refuse before processing/cache use. Unopted callers retain upstream path returns.
Revision 3 additionally sorts `modules_toc` by module name only inside
`create_base_library_zip`, before either original Analysis snapshot. Equal module
content and optimization can therefore serialize independently of traversal order;
module membership, code-cache lookup, headers, marshaling and ZipInfo stay unchanged.
Both original lane observations and every contributor remain; missing inputs,
live drift and different bytes or kinds still refuse. This does not prove equal
cached code objects or identify build12's exact changed subject, which is unknown.
No loader, bootloader, compiler or configuration source changes. Bootloader
binaries are unchanged. Native operations are observed at their existing call
sites, without repeating transformations or adding process supervision.

Only those two code members and METADATA/WHEEL/RECORD differ. The other 561 of
566 member names, modes and decompressed byte sequences are unchanged. METADATA
adds the factual custody-revision header, WHEEL adds the build tag, and RECORD
contains actual replacement hashes/sizes. Original Name/Version/Requires-Dist,
extras, compatibility tag and entry points are preserved. ZIP wrapper metadata
is deliberately deterministic repackaging, not original compressed-byte identity.

Reproduce from an immutable export under owned, safe, physical ancestry, using
two distinct absent output directories:

```sh
python3 packaging/vendor/pyinstaller-6.21.0/prepare.py prepare --output-dir /owned/private/new-preparation-a
python3 packaging/vendor/pyinstaller-6.21.0/prepare.py prepare --output-dir /owned/private/new-preparation-b
cmp /owned/private/new-preparation-a/*.whl /owned/private/new-preparation-b/*.whl
python3 packaging/vendor/pyinstaller-6.21.0/prepare.py verify
```

Preparation needs only Python standard library and existing Git. It checks
original RECORD/inventory/notices, the exact two-path patch, resulting source
identities, the five-member allowlist, regenerated RECORD, replacement inventory
and expected wheel digest. Git applies only inside new private staging trees;
no download, build backend, package import/install, index upload or native tool
runs. The original, historical custody2 and active custody3 wheels and patch are
committed in full. Historical wheel bytes and receipts retain their original
identity. `provenance.json` pins actual inputs, preparer, per-member outputs and
wheel identities; it has no
self-hash. External review pins that manifest. See the maker's case record for
actual commands/exits and missing evidence, rather than inferring a run here.

Darwin selects this exact local wheel through `tool.uv.sources`; other platforms
retain registry 6.21.0. uv's actual lock includes the local SHA256. The app wrapper
checks the committed wheel before frozen sync. The freeze process checks loaded
source paths and selected installed member/RECORD claims against the wheel.
Installed RECORD is retained separately because installers can regenerate it.
No patch-after-install or runtime monkey patch supplies this API. Linux resolver
and portable parser observations establish no Darwin install/runtime behavior.

The active custody3 replacement is 2,827,007 bytes, SHA256
`2232ee6ad8b63f912fcfef8027b5327d2051e4a270cd838c3f25aeb4a263ee12`.
Two guarded preparations and verification establish source-repack reproduction
only. Darwin installation, both-lane ZIP equality, mismatch refusal and fresh
reader behavior remain UNEXECUTED/UNVERIFIED; app/archive/receipt and final
manifest-based notices remain NOT_REACHED. Source/static or repack evidence is
not native or package acceptance.
