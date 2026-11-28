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
 * Shape metadata (`A@{ … }`) is YAML that mermaid parses only after rewriting
 * the source: it removes `%%` directives and comments, normalizes HTML
 * attribute quotes after `<`, and drops the `;` ending a `style…#…;` run. Each
 * rewrite can splice or re-delimit text, so the guard does not try to locate
 * metadata blocks. Metadata needs a literal `@` (no rewrite inserts one). A
 * source containing `@` is refused when it also contains, anywhere: `%%`, `<`,
 * `#` (the inputs of those rewrites), a backslash (YAML escapes can spell a
 * key), or the letters `img` in any case.
 */
const SHAPE_DATA_HAZARD = /%%|<|#|\\|img/i;
const hasUnsafeShapeData = (source: string): boolean =>
  source.includes('@') && SHAPE_DATA_HAZARD.test(source);

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
