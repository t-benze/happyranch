// Branded, honesty-fenced stand-in for a not-yet-captured screenshot.
//
// The manual marks pending screenshots in prose with a plain image marker:
//   ![placeholder: <what the shot will show>](TODO)
// Nextra compiles those to <img src="TODO">, which browsers render as a broken
// glyph. theme.config.jsx's `components.img` detects the marker and renders this
// card instead. It is deliberately NOT a fake screenshot: the "Screenshot
// coming" badge keeps the honesty fence, while the caption echoes the marker
// text so the reader knows exactly what the real shot will show. When a real
// image lands (the deferred screenshots-in-content slice), the marker's `(TODO)`
// is swapped for an image path — the alt no longer starts with "placeholder:",
// so this stand-in is bypassed and the real image renders.
export function ScreenshotPlaceholder({ caption }) {
  return (
    <figure
      className="hr-shot"
      role="img"
      aria-label={`Screenshot placeholder — ${caption}`}
    >
      <div className="hr-shot-chrome" aria-hidden="true">
        <span className="hr-shot-dot" />
        <span className="hr-shot-dot" />
        <span className="hr-shot-dot" />
      </div>
      <div className="hr-shot-body">
        <svg
          className="hr-shot-icon"
          viewBox="0 0 24 24"
          width="28"
          height="28"
          fill="none"
          aria-hidden="true"
        >
          <rect
            x="3"
            y="5"
            width="18"
            height="14"
            rx="2.5"
            stroke="currentColor"
            strokeWidth="1.6"
          />
          <circle
            cx="8.5"
            cy="10"
            r="1.8"
            stroke="currentColor"
            strokeWidth="1.6"
          />
          <path
            d="M4 17.5l4.5-4 3.5 2.8 3-2.4 6 4.6"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <span className="hr-shot-badge">Screenshot coming</span>
        {caption ? (
          <figcaption className="hr-shot-caption">{caption}</figcaption>
        ) : null}
      </div>
    </figure>
  )
}
