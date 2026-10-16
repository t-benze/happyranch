# Local upstream derivative

This package is compiled from the unmodified source at
[8ca9ba5ea52de03308fe8ced94f7b159a44d28ff](https://github.com/theKashey/react-remove-scroll-bar/tree/8ca9ba5ea52de03308fe8ced94f7b159a44d28ff).
That revision declares **2.3.7** and includes the original MIT LICENSE copied
byte-for-byte at this root and under `upstream/`. It does not authenticate the
notice provenance of the former registry 2.3.8 publication.

`upstream/` preserves the four source files, original manifests, tsconfig and
LICENSE. `provenance.json` records Git blob IDs, sizes and SHA256 authenticated
against the exact upstream GitHub tree. The separate root `package.json` is the
local derivative manifest, version `2.3.7+upstream.8ca9ba5ea52d`. It preserves
upstream dependency/peer ranges and root/constants entrypoints, removes upstream
development tooling and lifecycle scripts, and includes originals/provenance.
No registry code or downloaded upstream build toolchain enters this tarball.

From `web/`, after ordinary `npm ci`, explicitly run:

```sh
node scripts/prepare-scrollbar-package.mjs --check --installed
# Only when deliberately regenerating the committed archive:
node scripts/prepare-scrollbar-package.mjs --write
```

The script uses the existing locked TypeScript 5.9.3 and locked React types,
strict compilation, React JSX, importHelpers and esModuleInterop. It emits
CommonJS ES5 and ES module ES2015/ES2019 JS plus declarations, without source
maps (Vite maps attribute the actual installed compiled package files).
`skipLibCheck` skips external declaration diagnostics, not upstream source
checking. `npm pack --offline --ignore-scripts` packs a disposable directory; regeneration
compares the complete archive bytes, including normalized npm archive metadata.
No install-time compilation is needed. Preparation was verified with Node
24.19.0 / npm 11.17.0; different npm pack implementations must also satisfy the
byte comparison rather than silently accepting a changed archive.

The web direct file dependency and `$react-remove-scroll-bar` override select
this same regular installed package for every transitive consumer. The existing
release wrapper exports the checked-in tarball through git archive before npm
ci. Installed original notice bytes are available to the existing safe web
notice collector; no first-party alias or collector exception is used.

This source substitution preserves intended UI behavior. Package checks and
browser observations are scoped QA, not legal applicability, native acceptance
or complete emitted/license closure. All six candidate requirements remain
unresolved pending their actual evidence.
