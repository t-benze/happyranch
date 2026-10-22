# HappyRanch End-User Manual

Standalone Nextra 3 (Pages Router) docs site on Next.js 15 and React 18.
Content lives in `pages/`; screenshots that do not exist yet are
`![placeholder: ...](TODO)` markers rendered as "Screenshot coming" cards.

## Search

The search box is `components/ManualSearch.jsx`, set as the theme's
`search.component`. It is adapted from nextra-theme-docs 3.3.1's built-in
FlexSearch box (MIT, notice kept in the file) and keeps its index, matching,
markup and keyboard behavior. It differs in these ways:

- A query typed while the search index is still loading gets its results as
  soon as the index is ready, with no further typing. A query cleared while
  loading shows nothing.
- If the index fails to load, the box shows "Failed to load search index." and
  the next focus (or typing again) retries the load.
- No search runs while the index is absent.
- Choosing a result, by pointer or keyboard, moves focus to the selected
  heading (or the page's h1) once that page is showing, so the next Tab starts
  in the article. Upstream left focus in the search input, which in the closed
  mobile menu is off screen. A selected heading is scrolled to below the
  navbar; `styles/globals.css` keeps Nextra's navbar scroll padding while focus
  is in the article. Focus does not move if the navigation fails, another
  navigation replaces it, or focus has already gone elsewhere.
- A plain click on a result starts one navigation, not two (Headless UI
  already navigates on mousedown). Ctrl/Cmd/Shift/Alt-click keep the browser's
  link behavior.

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
`out/` (`.next/export-detail.json`, without `exportPathMap` or
`trailingSlash`). It derives the expected export from that build, not from
`out/`: one static HTML page per `.next/server/pages-manifest.json` page (the
`getStaticProps` routes must all be the Nextra `_meta` 404s), each carrying
the build's `.next/BUILD_ID` and no other, plus every `.next/static` file
under a single build ID. `index.html`, `404.html` (without it Pages serves `/`
for unknown paths) and the search index
`_next/static/chunks/nextra-data-en-US.json` are required. `out/` must match
that inventory and those bytes exactly, so a stale page from another build, a
changed resource, a missing page or an extra file such as `server.js` is
refused. It also refuses hosting files (`_headers`, `_redirects`,
`_routes.json`, `_worker.js`, `functions`), secret/workspace names anywhere
under `out/` or `.next/static` (`.env*`, `.npmrc`, `.netrc`, `.git`, `.ssh`,
`.aws`, `.claude`, `.happyranch`, `*.pem`, `*.key`, `*.p12`, `*.pfx`), source
maps, a `public/` directory and files over the Pages limits. Every existing
ancestor of the manual directory, `out/`, `.next` and its metadata files, and
of the destination must be a real directory, not a symlink; this is a literal
path check, not protection against a concurrent rename. The destination must
be new and outside the manual directory. It writes `site/` (the only
directory to upload or serve) with a package-only `_headers`
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
