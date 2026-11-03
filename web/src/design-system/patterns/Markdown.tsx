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
  lang?: string | null;
  meta?: string | null;
  tagName?: string;
  children?: SourceNode[];
  properties?: Record<string, unknown>;
  position?: { start: { offset?: number }; end: { offset?: number } };
}

/**
 * Local recovery convention: the parser accepts an info string starting with
 * `<` as lang/meta, but here it is treated as markup typed on the opening line
 * (```<a href…>…</a>). Show that raw source line, minus fence and leading
 * whitespace (trailing spaces/tabs kept), as the first code line. Text, never HTML.
 */
function remarkOpeningLineMarkup() {
  return (tree: SourceNode, file: { value: unknown }) => {
    const source = String(file.value);
    const visit = (node: SourceNode) => {
      if (node.type === 'code' && node.lang?.startsWith('<')) {
        const start = node.position?.start.offset;
        const line = start === undefined ? undefined : source.slice(start).split(/\r\n|\r|\n/, 1)[0];
        const info = line?.replace(/^[ \t]*(`{3,}|~{3,})[ \t]*/, '') ?? [node.lang, node.meta].filter(Boolean).join(' ');
        node.value = node.value ? `${info}\n${node.value}` : info;
        node.lang = null;
        node.meta = null;
      }
      node.children?.forEach(visit);
    };
    visit(tree);
  };
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
          remarkPlugins={[remarkGfm, remarkBreaks, remarkOpeningLineMarkup]}
          rehypePlugins={[...(threadMentions ? [sourceMentions(body)] : []), [rehypeHighlight, { ignoreMissing: true, detect: true }]]}
          components={{ code: CodeOrMermaid }}
        >
          {body}
        </ReactMarkdown>
      </MermaidLoadingLabel.Provider>
    </div>
  );
}
