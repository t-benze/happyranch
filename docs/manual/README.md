# HappyRanch End-User Manual

Standalone Nextra 3 (Pages Router) docs site on Next.js 15 and React 18.
Content lives in `pages/`; screenshots that do not exist yet are
`![placeholder: ...](TODO)` markers rendered as "Screenshot coming" cards.

## Build

Requires Node 24 and the committed lockfile.

```bash
npm ci
npm run build
```

`npm run dev` serves a local preview. Every page is prerendered at build
time; the site has no API routes, middleware, rewrites or remote images.

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
`.next/standalone/public` as well. Run the package on any Linux x86_64 host
with Node 24 (the bundled `sharp`/`@img` binaries are platform-specific):

```bash
tar -xzf happyranch-manual.tar.gz
cd standalone
HOSTNAME=127.0.0.1 PORT=3000 node server.js
```

`npm start` (`next start`) is for local previews only; it prints a warning
under `output: 'standalone'`. Use `node server.js` for the package.
