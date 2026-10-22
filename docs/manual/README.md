# HappyRanch End-User Manual

Standalone Nextra 3 (Pages Router) docs site on Next.js 15 and React 18.
Content lives in `pages/`; screenshots that do not exist yet are
`![placeholder: ...](TODO)` markers rendered as "Screenshot coming" cards.

## Search

The search box is `components/ManualSearch.jsx`, set as the theme's
`search.component`. It is adapted from nextra-theme-docs 3.3.1's built-in
FlexSearch box (MIT, notice kept in the file) and keeps its index, matching,
markup and keyboard behavior. It differs in three ways:

- A query typed while the search index is still loading gets its results as
  soon as the index is ready, with no further typing. A query cleared while
  loading shows nothing.
- If the index fails to load, the box shows "Failed to load search index." and
  the next focus (or typing again) retries the load.
- No search runs while the index is absent.

It imports `flexsearch` and `@headlessui/react` directly, so both are declared
in `package.json` at exactly the versions nextra-theme-docs 3.3.1 already
resolves (0.7.43 and 2.2.10). Keep them in step with the theme when upgrading
Nextra.

## Build

Requires Node 24 and the committed lockfile.

```bash
npm ci
npm run build
```

`npm run dev` serves a local preview. Every page is prerendered at build
time; the site has no API routes, middleware, application-defined rewrites or
remote images. Nextra itself generates one `afterFiles` rewrite,
`/:path*/_meta` -> `/404`, which hides the `_meta` navigation files; it appears
in `.next/routes-manifest.json` and the standalone `server.js`.

## Relocatable package

`next.config.mjs` sets `output: 'standalone'`, so the build emits
`.next/standalone/` holding `server.js`, the prerendered pages and only the
runtime files Next.js traced. Build-time packages (Nextra's MDX/KaTeX/Mermaid
toolchain and their dependencies) are not part of it. Assemble the package
from a clean build:

```bash
cp -R .next/static .next/standalone/.next/static
tar -C .next -czf happyranch-manual.tar.gz standalone
```

There is no `public/` directory; if one is added, copy it to
`.next/standalone/public` as well.

### Platform

The standalone trace copies the native binaries that `npm ci` installed for
the build machine, so a package only runs on the OS, CPU architecture and
libc it was built on. Build on the same OS/architecture/libc as the intended
host; every other target needs its own build and its own run evidence.

- Upstream prerequisites of the bundled native packages
  (`@img/sharp-linux-x64`, `@img/sharp-libvips-linux-x64`): Linux, x64,
  glibc >= 2.28. No musl (Alpine) binaries are included. Node 24 is required
  by `engines`.
- Observed: the current candidate was built with Node 24.19.0 / npm 11.17.0
  on Linux x86_64 (glibc), and its relocated `server.js` was run on Node
  24.19.0 with glibc 2.43. The glibc 2.28 minimum, other glibc versions,
  musl, arm64, macOS and Windows were not tested.

## Static export (optional, for a static host)

The default build above is unchanged. For a static host such as Cloudflare
Pages (which cannot run `server.js`), build the optional static export and
assemble it from a clean checkout:

```bash
npm ci
npm run build:static-export          # HAPPYRANCH_BUILD_TARGET=static-export, writes out/
npm run assemble:static-export -- "$PWD" /absolute/new/package-dir
```

Any other `HAPPYRANCH_BUILD_TARGET` value fails the build. The assembler
requires that the last build was a successful export into this directory's
`out/`, a single build ID under `_next/static`, `index.html`, `404.html`
(without it Pages serves `/` for unknown paths) and the search index
`_next/static/chunks/nextra-data-en-US.json`. It refuses symlinks, hosting
files (`_headers`, `_redirects`, `_routes.json`, `_worker.js`, `functions`),
`.env*` and source maps, and files over the Pages limits. It writes `site/`
(the only directory to upload or serve) with a package-only `_headers`
(`X-Robots-Tag: noindex, nofollow`; nothing marked immutable, because the
search index keeps the same name across builds) and `package-manifest.json`
beside `site/`, never inside it.

The export has the same pages, navigation and search as the Node package.
Nextra's `/:path*/_meta` rewrite does not apply to a static export; the
`_meta` routes produce no HTML there, so they are unknown paths (404). The
pages load the three brand fonts from Google Fonts (`fonts.googleapis.com`,
`fonts.gstatic.com`) in both builds; a visitor's browser makes those requests
to Google directly. The navbar "Website" link targets `https://happyranch.ai`,
which serves the website only after its cutover.

Run the package:

```bash
tar -xzf happyranch-manual.tar.gz
cd standalone
HOSTNAME=127.0.0.1 PORT=3000 node server.js
```

`npm start` (`next start`) is for local previews only; it prints a warning
under `output: 'standalone'`. Use `node server.js` for the package.
