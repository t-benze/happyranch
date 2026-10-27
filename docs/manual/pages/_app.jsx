import 'nextra-theme-docs/style.css'
import '../styles/globals.css'

export default function App({ Component, pageProps, router }) {
  // Next.js's own 404 page has no navigation to skip and no page h1.
  const error = router.pathname === '/_error' || router.pathname === '/404'
  return (
    <>
      {/* First focusable element on every manual page, server-rendered so it
          works without JavaScript. The target is the page's h1
          (theme.config.jsx components.h1). */}
      {!error && (
        <a className="hr-skip" href="#hr-content">
          Skip to content
        </a>
      )}
      <Component {...pageProps} />
    </>
  )
}
