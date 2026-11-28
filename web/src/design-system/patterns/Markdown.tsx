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
import { ImageOff } from 'lucide-react';

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

// Only this Markdown instance's optional placeholder copy crosses the stable renderer.
const RemoteImageLabel = createContext('Remote image not loaded');
// True while rendering inside an authored link, so a placeholder never nests an <a> in an <a>.
const InsideLink = createContext(false);

/**
 * Privacy: an image loads only from this page's own origin (relative or
 * same scheme+host). Anything else (another host, protocol-relative
 * `//host/x`, unparsable) would make the visitor's browser contact a third
 * party, so it is not rendered as <img>. react-markdown's default
 * urlTransform already empties data:, blob: and other non-http(s) sources
 * before this runs, so they arrive as src="" (an <img> that fetches nothing);
 * the data:/blob: branch below is therefore not reached today.
 */
function isLoadableImageSrc(src: string, base: { href: string; protocol: string; host: string }): boolean {
  let url: URL;
  try {
    url = new URL(src, base.href);
  } catch {
    return false;
  }
  if (url.protocol === 'data:' || url.protocol === 'blob:') return true;
  return url.protocol === base.protocol && url.host === base.host;
}

function MarkdownLink(props: ComponentProps<'a'> & { node?: unknown }): JSX.Element {
  const { node: _node, children, ...rest } = props;
  return (
    <InsideLink.Provider value>
      <a {...rest}>{children}</a>
    </InsideLink.Provider>
  );
}

/** Phrasing content only: Markdown images render inside a <p> (and maybe an <a>). */
function MarkdownImage(props: ComponentProps<'img'> & { node?: unknown }): JSX.Element {
  const label = useContext(RemoteImageLabel);
  const insideLink = useContext(InsideLink);
  const { node: _node, ...rest } = props;
  const src = typeof rest.src === 'string' ? rest.src : '';
  if (!src || isLoadableImageSrc(src, window.location)) return <img {...rest} />;
  const alt = typeof rest.alt === 'string' ? rest.alt.trim() : '';
  return (
    <span className="gl-prose-remote-image" data-remote-image="blocked" title={rest.title}>
      <ImageOff aria-hidden="true" className="gl-prose-remote-image-icon" />
      <span className="gl-prose-remote-image-label">{label}</span>
      {alt && <span className="gl-prose-remote-image-alt">{alt}</span>}
      {insideLink
        ? <span className="gl-prose-remote-image-url">{src}</span>
        : <a className="gl-prose-remote-image-url" href={src} rel="noopener noreferrer">{src}</a>}
    </span>
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

export function Markdown({ body, threadMentions = false, mermaidLoadingLabel = 'Rendering diagram…', remoteImageLabel = 'Remote image not loaded' }: {
  body: string;
  threadMentions?: boolean;
  /** App-owned loading chrome only; authored Markdown stays verbatim. */
  mermaidLoadingLabel?: string;
  /** App-owned placeholder label for a blocked third-party image. */
  remoteImageLabel?: string;
}): JSX.Element {
  return (
    <div className="gl-prose">
      <MermaidLoadingLabel.Provider value={mermaidLoadingLabel}>
        <RemoteImageLabel.Provider value={remoteImageLabel}>
          <ReactMarkdown
            remarkPlugins={[remarkGfm, remarkBreaks, remarkOpeningLineMarkup]}
            rehypePlugins={[...(threadMentions ? [sourceMentions(body)] : []), [rehypeHighlight, { ignoreMissing: true, detect: true }]]}
            components={{ code: CodeOrMermaid, a: MarkdownLink, img: MarkdownImage }}
          >
            {body}
          </ReactMarkdown>
        </RemoteImageLabel.Provider>
      </MermaidLoadingLabel.Provider>
    </div>
  );
}
