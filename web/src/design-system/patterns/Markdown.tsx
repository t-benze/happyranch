/**
 * Markdown — render a markdown body inside `.gl-prose`.
 *
 * See spec §4.2 for the full pipeline rationale.
 */
import ReactMarkdown from 'react-markdown';
import { scanMentions } from '@/lib/threadMentions';
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

interface SourceNode {
  type: string;
  value?: string;
  tagName?: string;
  children?: SourceNode[];
  properties?: Record<string, unknown>;
  position?: { start: { offset?: number }; end: { offset?: number } };
}

/** Syntax only: decorate proven raw intervals, never decoded text or identities. */
function sourceMentions(body: string) {
  const ranges = scanMentions(body).filter(range => range.query);
  return () => (tree: SourceNode) => {
    const visit = (parent: SourceNode) => {
      if (parent.tagName === 'code' || parent.tagName === 'pre' || !parent.children) return;
      parent.children = parent.children.flatMap(child => {
        if (child.type !== 'text') { visit(child); return [child]; }
        const start = child.position?.start.offset;
        const end = child.position?.end.offset;
        if (start === undefined || end === undefined || body.slice(start, end) !== child.value) return [child];
        const selected = ranges.filter(range => range.tokenStart >= start && range.end <= end);
        if (!selected.length) return [child];
        const result: SourceNode[] = [];
        let cursor = start;
        for (const range of selected) {
          if (cursor < range.tokenStart) result.push({ type: 'text', value: body.slice(cursor, range.tokenStart) });
          result.push({ type: 'element', tagName: 'span', properties: { className: ['font-medium', 'text-accent-default'], 'data-thread-mention': 'syntax' },
            children: [{ type: 'text', value: body.slice(range.tokenStart, range.end) }] });
          cursor = range.end;
        }
        if (cursor < end) result.push({ type: 'text', value: body.slice(cursor, end) });
        return result;
      });
    };
    visit(tree);
  };
}

export function Markdown({ body, threadMentions = false, mermaidLoadingLabel = 'Rendering diagram…' }: {
  body: string;
  threadMentions?: boolean;
  /** App-owned loading chrome only; authored Markdown stays verbatim. */
  mermaidLoadingLabel?: string;
}): JSX.Element {
  return (
    <div className="gl-prose">
      <MermaidLoadingLabel.Provider value={mermaidLoadingLabel}>
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkBreaks]}
          rehypePlugins={[...(threadMentions ? [sourceMentions(body)] : []), [rehypeHighlight, { ignoreMissing: true, detect: true }]]}
          components={{ code: CodeOrMermaid }}
        >
          {body}
        </ReactMarkdown>
      </MermaidLoadingLabel.Provider>
    </div>
  );
}
