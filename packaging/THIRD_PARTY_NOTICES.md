# Packaging notice index and unresolved dispositions

This index is not a substitute for original license texts or legal acceptance.
An actual build maps shipped code/resources to source/version/digest, Analysis
input or archive-member/chunk inclusion, original notice path/hash, retained app
notice path/hash and disposition. Dependency/lock lists nominate inputs; they
do not prove complete shipping obligations. Build-only tooling requires actual
exclusion evidence before `BUILD_ONLY_NOT_SHIPPED` can be recorded.

| Layer | Original texts and retained location | Current disposition |
| --- | --- | --- |
| First-party Swift/C/Python/resources | Exact root Apache 2.0 LICENSE → Resources/licenses/LICENSE; SHA256 cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30 | Build checks exact resource bytes; preserve per-file attribution |
| Python distributions selected by daemon/CLI Analysis | Actual installed LICENSE/NOTICE/COPYING/copyright files → licenses/python/NAME/VERSION; RECORD inputs and hashes retained | Missing text is LICENSE_MISSING; actual native/stdlib inclusion still requires reconciliation |
| Python runtime/stdlib | Effective interpreter LICENSE.txt, or exact authenticated offline CPython fallback → licenses/python/Python-LICENSE.txt | Missing text is LICENSE_MISSING; embedded native vendor obligations unresolved |
| PyInstaller bootloader/support | Effective distribution original license and redistribution-exception texts → licenses/python/PyInstaller/VERSION | LICENSE_UNRESOLVED:BOOTLOADER_EXCEPTION_MAPPING until actual bootloader/source/exception mapping is reconciled |
| Go runtime and compiled dependencies | GOROOT original texts and real readonly go-list dependency module LICENSE/NOTICE → licenses/go | LICENSE_UNRESOLVED:GO_LINKED_GRAPH until fresh archive-member/final link inclusion is reconciled |
| Generated web assets | Emitted source-map package input graph, original installed package notices → licenses/web | LICENSE_UNRESOLVED:WEB_EMITTED_CLOSURE; source maps alone do not resolve minifier/static helpers or all assets |
| Native extensions/dylibs/frameworks | Recursive actual native closure, vendor texts; system frameworks explicitly host references | LICENSE_UNRESOLVED:PYTHON_NATIVE and NATIVE_REDISTRIBUTION |
| Generated icon and source resources | Original source attribution and build receipts; preserve all 29 accepted resources | LICENSE_UNRESOLVED:ASSET_ATTRIBUTION where ownership/attribution is not established |

A named `LICENSE_MISSING:<component>:<path>` means an identified original text or
retained copy is absent. `LICENSE_UNRESOLVED:<component>:<reason>` means shipped
inclusion/version/provenance/obligations remain unproved. The machine mapping
retains component and source fields separately. Missing/unresolved entries
prevent the verifier from emitting complete-license or successful-release
inventory. Logs, actual found texts and partial mappings survive; return exact
remaining component/text/destination evidence to TASK11199/TASK11196. Do not
remove/replace dependencies or treat an index/dependency list as compliance.

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

Offline notice inputs live in `packaging/notice-data/identity-map.json` with two
unchanged originals: CPython LICENSE (13936 bytes, SHA256
`3b2f81fe21d181c499c59a256c8e1968455d6689d269aa85373bfb6af41da3bf`)
at revision `0cc81280367df838c4b199f8f0378837165071c2`, and Radix LICENSE
(1063 bytes, SHA256
`0e80a2d229d2fd4fc7e8636142ec5d0ff0bc031f14c15b682e2ac01dfd5b5138`)
at revision `fcef0668a5c827e5a4baac405474d75680f9a4eb`.
Installed originals retain precedence. When effective CPython `LICENSE.txt` is
absent, the fallback requires the exact recorded implementation/toolchain,
matching current toolchain evidence, and threading/contextlib/string source
hashes in the actual stdlib and both daemon/CLI Analysis lanes. A version label
alone cannot admit retention. A missing or mismatched identity remains
`LICENSE_MISSING` with its reason.

Only the ten explicit Radix package/version entries may use the offline notice
when package-local originals are absent. Each actual emitted input must match
the pinned package metadata and source hash; unknown/changed inputs or added
subpath manifests refuse fallback. Published stable versions differ from the
upstream `-rc.9` metadata: official registry gitHead and exact published
source-map contents authenticate that revision. The map preserves both
identities, archive integrity and source bindings. No network request, install
hook, dependency/version change or generic notice template is involved.
Symlinked/escaping paths, corrupt offline originals or conflicting destination
bytes refuse retention. Mapping rows preserve original/retained hashes,
destinations and provenance. `react-remove-scroll-bar` 2.3.8 remains missing;
its tarball lacks a full notice and its recorded upstream revision is unresolved.
All six named unresolved labels and `closure_complete=false` remain. Source
retention does not establish current native binary custody, legal acceptance
or complete emitted closure; strict license verification still blocks ZIP and
release finalization.

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

External claude/codex/opencode/pi/custom providers, git, helper Python,
development uv and optional installed Tailscale are operation prerequisites;
being mentioned here does not mean their external installations are shipped.
Linux app notice inventories and historical Mac binaries do not establish this
candidate's closure. Public availability, signing and legal disposition remain
with the parent's authorized owners.
