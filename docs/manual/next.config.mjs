import nextra from 'nextra'

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
  output: 'standalone',
  // Trace from this directory so a lockfile elsewhere in the repository
  // cannot move server.js below .next/standalone/<subpath>.
  outputFileTracingRoot: import.meta.dirname
})
