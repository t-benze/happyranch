/**
 * Mermaid — render a Mermaid source string as inline SVG.
 *
 * Lazy-imported from Markdown.tsx so the ~150 KB library only loads when
 * a message body actually contains a ```mermaid block. On render error,
 * falls back to the raw source inside a styled <pre> so a malformed
 * diagram never blanks the surrounding bubble.
 *
 * Default-export so the lazy() wrapper in Markdown.tsx works as
 * intended.
 */
import { useEffect, useId, useState } from 'react';
import mermaid from 'mermaid';

let initialized = false;
function ensureInitialized(): void {
  if (initialized) return;
  initialized = true;
  mermaid.initialize({
    startOnLoad: false,
    theme: 'dark',
    securityLevel: 'strict',
    // Privacy: label HTML may not embed anything the browser would fetch
    // (mermaid measures labels in the live document). The `style` tag stays
    // forbidden as in mermaid's default; a `style` attribute could carry url().
    dompurifyConfig: {
      FORBID_TAGS: ['style', 'img', 'image', 'picture', 'source', 'video', 'audio', 'track', 'iframe', 'object', 'embed', 'input', 'link'],
      FORBID_ATTR: ['style', 'background', 'poster', 'srcset'],
    },
  });
}

/**
 * Privacy: some diagram sources make the browser fetch a URL before or
 * outside the label sanitizer: a flowchart image shape (`A@{ img: "…" }`,
 * loaded by mermaid via `new Image()`), and authored CSS (`themeCSS`,
 * `style`/`classDef` values) using a fetching CSS function, `@import` or a
 * CSS escape that could spell one. Such a diagram is not rendered; the
 * raw-source fallback is shown instead.
 */
const EXTERNAL_RESOURCE = [
  /\bthemeCSS\b/i,
  /(?:url|image-set|image|cross-fade|src)\(/i,
  /@import|@font-face/i,
  /\\[0-9a-f]/i,
];

/**
 * Shape metadata (`@{ … }`) is parsed by mermaid as YAML, where escapes
 * (`"i\u006dg"`, `"\x69mg"`, an escaped line break) can spell the `img` key
 * without those letters appearing in the source. Fail closed instead of
 * enumerating spellings: refuse any metadata block that contains a backslash
 * or the letters `img` in any case. Without a backslash YAML cannot produce
 * those letters unless they appear literally in the same block (anchors and
 * aliases repeat literal text; folding only inserts spaces). A block ends at
 * the first `}` outside double quotes, as in mermaid's lexer, or at the end
 * of the source when unterminated. Every `@{` is checked as a possible start,
 * so one inside a label string cannot hide the real block that follows.
 */
function hasUnsafeShapeData(source: string): boolean {
  let start = source.indexOf('@{');
  while (start !== -1) {
    let end = start + 2;
    let quoted = false;
    while (end < source.length && (quoted || source[end] !== '}')) {
      if (source[end] === '"') quoted = !quoted;
      end += 1;
    }
    const block = source.slice(start + 2, end);
    if (block.includes('\\') || /img/i.test(block)) return true;
    start = source.indexOf('@{', start + 1);
  }
  return false;
}

export default function Mermaid({ source }: { source: string }): JSX.Element {
  const [svg, setSvg] = useState<string | null>(null);
  const [errored, setErrored] = useState(false);
  const rawId = useId();
  const id = `m_${rawId.replace(/:/g, '_')}`;
  useEffect(() => {
    if (hasUnsafeShapeData(source) || EXTERNAL_RESOURCE.some((pattern) => pattern.test(source))) {
      setErrored(true);
      return;
    }
    ensureInitialized();
    let cancelled = false;
    mermaid
      .render(id, source)
      .then(
        (r) => { if (!cancelled) setSvg(r.svg); },
        () => { if (!cancelled) setErrored(true); },
      );
    return () => { cancelled = true; };
  }, [source, id]);
  if (errored) {
    return <pre className="gl-prose-mermaid-failed">{source}</pre>;
  }
  return (
    <div
      className="gl-prose-mermaid"
      // eslint-disable-next-line react/no-danger -- mermaid output is trusted (securityLevel: 'strict' upstream)
      dangerouslySetInnerHTML={{ __html: svg ?? '' }}
    />
  );
}
