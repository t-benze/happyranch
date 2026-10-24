import nextra from 'nextra'

// Default (unset) is the relocatable Node package (standalone server.js).
// HAPPYRANCH_BUILD_TARGET=static-export selects the optional static export
// (out/) for a static host such as Cloudflare Pages; any other value fails.
const target = process.env.HAPPYRANCH_BUILD_TARGET ?? 'standalone'
if (target !== 'standalone' && target !== 'static-export') {
  throw new Error(
    `Invalid HAPPYRANCH_BUILD_TARGET "${target}": use standalone (default) or static-export`
  )
}
const staticExport = target === 'static-export'

const withNextra = nextra({
  theme: 'nextra-theme-docs',
  themeConfig: './theme.config.jsx',
  // v1 uses `![placeholder: ...](TODO)` screenshot markers (honesty fence).
  // Disable static-image optimization so these render as literal placeholders
  // instead of being resolved as module imports at build time.
  staticImage: false
})

export default withNextra({
  reactStrictMode: true,
  // Emit a self-contained server in .next/standalone with only the traced
  // runtime files, so the relocatable package does not ship the build-time
  // dependency tree (see README.md "Relocatable package").
  output: staticExport ? 'export' : 'standalone',
  // A static export has no image optimizer; the manual renders no optimized
  // images today (placeholders only), so this changes no output.
  ...(staticExport ? { images: { unoptimized: true } } : {}),
  // Trace from this directory so a lockfile elsewhere in the repository
  // cannot move server.js below .next/standalone/<subpath>.
  outputFileTracingRoot: import.meta.dirname
})
