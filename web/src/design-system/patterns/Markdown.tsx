/**
 * Markdown — render a markdown body inside `.gl-prose`.
 *
 * See spec §4.2 for the full pipeline rationale.
 */
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkBreaks from 'remark-breaks';
import rehypeHighlight from 'rehype-highlight';
import { Suspense, lazy, createContext, useContext, type ComponentProps } from 'react';

const Mermaid = lazy(() => import('./Mermaid'));
// Only this Markdown instance's optional chrome copy crosses the stable renderer.
const MermaidLoadingLabel = createContext('Rendering diagram…');

function CodeOrMermaid(props: ComponentProps<'code'>): JSX.Element {
  const loadingLabel = useContext(MermaidLoadingLabel);
  const { className, children, ...rest } = props;
  const lang = /language-(\w+)/.exec(className ?? '')?.[1];
  if (lang === 'mermaid') {
    const source = String(children ?? '').replace(/\n$/, '');
    return (
      <Suspense fallback={<pre className="gl-prose-mermaid-loading">{loadingLabel}</pre>}>
        <Mermaid source={source} />
      </Suspense>
    );
  }
  return (
    <code className={className} {...rest}>
      {children}
    </code>
  );
}

export function Markdown({ body, mermaidLoadingLabel = 'Rendering diagram…' }: {
  body: string;
  /** App-owned loading chrome only; authored Markdown stays verbatim. */
  mermaidLoadingLabel?: string;
}): JSX.Element {
  return (
    <div className="gl-prose">
      <MermaidLoadingLabel.Provider value={mermaidLoadingLabel}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkBreaks]}
          rehypePlugins={[[rehypeHighlight, { ignoreMissing: true, detect: true }]]}
          components={{ code: CodeOrMermaid }}
        >
          {body}
        </ReactMarkdown>
      </MermaidLoadingLabel.Provider>
    </div>
  );
}
