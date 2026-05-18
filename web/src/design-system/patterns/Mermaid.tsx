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
  mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'strict' });
}

export default function Mermaid({ source }: { source: string }): JSX.Element {
  const [svg, setSvg] = useState<string | null>(null);
  const [errored, setErrored] = useState(false);
  const rawId = useId();
  const id = `m_${rawId.replace(/:/g, '_')}`;
  useEffect(() => {
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

export const meta = {
  name: 'Mermaid',
  layer: 'pattern',
  import: '@/design-system/patterns/Mermaid',
  variants: {},
  consumes: ['components.code_block'],
  example: '<Mermaid source="flowchart LR; A-->B" />',
} as const;
