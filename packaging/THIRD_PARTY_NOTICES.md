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
| Python runtime/stdlib | Effective interpreter LICENSE.txt → licenses/python/Python-LICENSE.txt | Missing text is LICENSE_MISSING; embedded native vendor obligations unresolved |
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
Failure diagnostics include
`web-attribution.json`: first 100 rejected records, string fields capped at 512
characters and serialized records capped at 2048 bytes, with explicit
omitted-record/field truncation; the failure bundle keeps
its existing 262144-byte per-file bound. No metadata contents or cache tree are
uploaded. Retained texts leave `closure_complete=false` and release finalization
blocked pending actual legal/native/asset reconciliation.

External claude/codex/opencode/pi/custom providers, git, helper Python,
development uv and optional installed Tailscale are operation prerequisites;
being mentioned here does not mean their external installations are shipped.
Linux app notice inventories and historical Mac binaries do not establish this
candidate's closure. Public availability, signing and legal disposition remain
with the parent's authorized owners.
