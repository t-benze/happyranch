// HappyRanch brand mark — the leaf/sprig glyph lifted from the marketing
// mockup (currentColor so it picks up the brand green in both themes).
const Mark = () => (
  <svg
    className="hr-mark"
    viewBox="0 0 100 100"
    fill="none"
    width="24"
    height="24"
    aria-hidden="true"
  >
    <g transform="rotate(-7 50 44)">
      <path
        d="M50 26 C68 26 78 34 78 44 C78 54 66 60 50 60 C34 60 22 54 22 44 C22 34 32 26 50 26 Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="6.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </g>
    <ellipse
      cx="41"
      cy="59"
      rx="6.2"
      ry="5"
      fill="none"
      stroke="currentColor"
      strokeWidth="5"
    />
    <path
      d="M44 63 C50 78 70 82 80 71 C85 65 83 59 77 60"
      fill="none"
      stroke="currentColor"
      strokeWidth="6.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
)

export default {
  logo: (
    <span className="hr-brand">
      <Mark />
      <span className="hr-wordmark">
        Happy<b>Ranch</b> <span className="hr-wordmark-sub">Manual</span>
      </span>
    </span>
  ),
  // Brand green → HSL, split light/dark so links stay legible on the warm
  // paper (light) and the deep warm-charcoal (dark) surfaces.
  color: {
    hue: { light: 138, dark: 134 },
    saturation: { light: 34, dark: 34 },
    lightness: { light: 36, dark: 55 }
  },
  // Warm paper (#f7f5ef) light / warm charcoal (#191d18) dark — drives
  // --nextra-bg for the page, sidebar, and navbar surfaces.
  backgroundColor: {
    light: '247,245,239',
    dark: '25,29,24'
  },
  project: {
    link: 'https://github.com/t-benze/happyranch'
  },
  docsRepositoryBase: 'https://github.com/t-benze/happyranch/tree/main/docs/manual',
  footer: {
    content: (
      <span className="hr-footer">
        <span className="hr-footer-brand">
          <Mark />
          Happy<b>Ranch</b>
        </span>
        <span className="hr-footer-line">
          A local runtime for running a small organization of AI agents — under
          one human founder.
        </span>
      </span>
    )
  }
}
