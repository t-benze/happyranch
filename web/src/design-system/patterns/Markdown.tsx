/**
 * Markdown — render a markdown body inside `.gl-prose`.
 *
 * See spec §4.2 for the full pipeline rationale.
 */
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeHighlight from 'rehype-highlight';
import { Suspense, lazy, type ComponentProps } from 'react';

const Mermaid = lazy(() => import('./Mermaid'));

function CodeOrMermaid(props: ComponentProps<'code'>): JSX.Element {
  const { className, children, ...rest } = props;
  const lang = /language-(\w+)/.exec(className ?? '')?.[1];
  if (lang === 'mermaid') {
    const source = String(children ?? '').replace(/\n$/, '');
    return (
      <Suspense fallback={<pre className="gl-prose-mermaid-loading">Rendering diagram…</pre>}>
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

export function Markdown({ body }: { body: string }): JSX.Element {
  return (
    <div className="gl-prose">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[[rehypeHighlight, { ignoreMissing: true, detect: true }]]}
        components={{ code: CodeOrMermaid }}
      >
        {body}
      </ReactMarkdown>
    </div>
  );
}

export const meta = {
  name: 'Markdown',
  layer: 'pattern',
  import: '@/design-system/patterns/Markdown',
  variants: {},
  consumes: ['typography.body', 'components.code_block'],
  example: '<Markdown body="**hello**" />',
} as const;
