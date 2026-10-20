# PyInstaller custody input

This internal repack retains distribution/version `pyinstaller 6.21.0` and uses
wheel build tag `1happyranchcustody4`. It is not an upstream release. The source
changes include HappyRanch custody revision 4, dated 2026-10-21. A later
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
Revision 4 additionally admits the complete COLLECT input ledger up to 32MiB,
with a fixed predecode lexical fence of 1,000,000 nodes and the original
1,000,000-object-node/depth16/string4096/type/duplicate guards. The ledger's
integer declared size, guarded subject digest, bounded full consumed bytes and
digest, ordered actual `collect_arguments` and final stable observation must all
agree. Both ledger reads and final stability reread charge the existing vendor
8GiB read meter. Default parsing and each written fragment stay 8MiB; aggregate
vendor fragment writes stay 32MiB. This is separate from the helper's fixed
inputs/output 32MiB, lexical/object 1,000,000-node profiles and its enclosing
attempted JSON-I/O 256MiB meter. No evidence occurrence is omitted or deduplicated.
Context stays64KiB, copy/default8MiB; depth16/string4096/strict UTF8 and Boolean
field restrictions remain. Object preflight and incremental canonical encoding
precede exclusive creation. Every helper write/readback/load/reference and
standalone/nested fragment read is charged without nested reset/path discounts;
ordinary reads8GiB/snapshots2GiB/members16384/names8MiB stay independent. The
helper's copy record references the actual stored digest-bound output bytes.
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
runs. The original, historical custody2/custody3 and active custody4 wheels and patch are
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

The active custody4 replacement is 2,828,479 bytes, SHA256
`1fa23b6af3cc9a99569ddd6758f1d92ccbe423cc03bee20c1543d2fcec5001c4`.
Two guarded preparations and verification establish source-repack reproduction
only. These are finite capacities, not a measurement of build13 (exact rejected size
and contents UNKNOWN), guaranteed native headroom, or hard RSS/time limits. The
224MiB/spec and 232.125MiB/fresh-reader formulas plus small records describe the
current call graph only; every actual extra pass still charges and may refuse.
Darwin installation, both-lane ZIP equality, mismatch refusal and fresh
reader behavior remain UNEXECUTED/UNVERIFIED; app/archive/receipt and final
manifest-based notices remain NOT_REACHED. Source/static or repack evidence is
not native or package acceptance.
