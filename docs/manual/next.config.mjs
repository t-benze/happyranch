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
  reactStrictMode: true
})
