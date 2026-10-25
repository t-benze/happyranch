# Packaging notice index and unresolved dispositions

This index supplements the original texts bundled by `manifest_notices.py`.
THR104seq260/266 selects dependency-manifest notices, not per-byte/linker closure
or legal certification. `licenses/manifest-notices.json` names every resolved
owner, selection and original/destination digest; `resolved-inventory.json`
retains the independently enumerated inventory. Complete originals are copied
under `licenses/manifest/OWNER_HASH/TEXT_HASH.txt`. Existing source/custody
observations and their notice copies are retained too.

| Layer | Membership and original text association |
| --- | --- |
| First-party Swift/C/Python/resources | Source tree and original root LICENSE; all 29 resource checks remain |
| Python distributions | Every uv.lock identity; installed frozen-environment superset has its own metadata/RECORD original texts, unselected marker/group/version rows are explicit |
| Python runtime/stdlib | Effective interpreter original or exact authenticated CPython fallback; independent native vendor files cannot inherit that text |
| PyInstaller | Exact installed distribution and its complete original COPYING/redistribution-exception terms |
| Go modules/runtime/vendor | Resolved go-list module graph and target package selection; module originals, GOROOT LICENSE/PATENTS and selected toolchain vendor modules.txt owners |
| npm/generated web | Full package-lock identity/location inventory; production and emitted owners retain originals, installed build-only and absent optional packages carry explicit exclusions; existing generated-tool reader retains its originals |
| Native vendor binaries | Concrete Analysis inputs outside distribution RECORD/Python core are named individually; missing original association refuses by path |
| Fonts/icons/public assets | Separate census, exact font revision and co-version OFL association; first-party assets use the original source LICENSE; source/app/archive bytes remain checked |

The self-hosted web fonts retain four unmodified upstream originals:
Hanken Grotesk at `marcologous/hanken-grotesk@e6e3c4df55acfe44333c587e3d97ae3c44b7dce5`,
Newsreader normal/italic at
`productiontype/NewsReader@1ece6a8bfe5db1a2b90c76cc1fe5d3b2eed5dcf3`
and Baloo 2 at `yanone/Baloo2-Variable@da523dfa21aa0e376253d61c21e39146dc55702a`.
Their full co-version original OFL texts are retained verbatim as
`web/public/fonts/HankenGrotesk-OFL.txt`, `Newsreader-OFL.txt` and
`Baloo2-OFL.txt`.
[`notice-data/font-assets.json`](notice-data/font-assets.json) records the
source URLs, commit-to-tree-to-blob identities and font-to-notice relations.
The Hanken Google Fonts import title says 3.012; its body and original font
say 3.013. Both facts are preserved in that record.

Vite's existing public-copy step emits these four TTFs and three notices into
`web/dist/fonts/`; the existing app assembly copies that dist into
`Contents/Resources/web/dist/`. Source retention and a web-copy observation
do not prove actual app/archive custody, native loading or complete asset
attribution. The manifest standard preserves these separate font identities
and original texts. Actual ICNS/native evidence remains independently required;
the former six-closure claim is not asserted.

A named `LICENSE_MISSING:<component>:<path>` means an identified original text or
retained copy is absent. `LICENSE_UNRESOLVED:<component>:<reason>` means shipped
inclusion/version/provenance/obligations remain unproved. The machine mapping
retains component and source fields separately. Missing/unresolved entries
prevent the verifier from emitting complete-license or successful-release
inventory. Logs, actual found texts and partial mappings survive; return exact
remaining component/text/destination evidence to the current delivery root. Do not
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

Emitted dependency source hashing uses a separate 16 MiB per-file limit and
64 KiB streaming reads, with lexical/package and ancestor checks, no-follow,
nonblocking open, single-link regular-file validation and stable size/timestamps.
Empty regular JavaScript modules retain the SHA256 of empty bytes. The limit
provides over 13 times the maximum size (1,236,999 bytes) in the authenticated
1,043-source retained mapping; notice, manifest and pinned fallback limits stay
unchanged. Any source refusal persists for its owner across later inputs/maps.

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
That historical 2.3.8 notice gap does not apply to the original-backed 2.3.7
derivative now selected by the lock. Source retention does not establish native
acceptance. The former six labels/False predicate are retired; incomplete
manifest membership or concrete missing originals still block ZIP creation.

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

External claude/codex/opencode/pi/custom providers, git, helper Python,
development uv and optional installed Tailscale are operation prerequisites;
being mentioned here does not mean their external installations are shipped.
Linux app notice inventories and historical Mac binaries do not establish this
candidate's closure. Public availability, signing and legal disposition remain
with the parent's authorized owners.
