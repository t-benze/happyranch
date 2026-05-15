import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ThreadMessage } from '@/lib/api/types';

interface Props {
  message: ThreadMessage;
}

function fmtTs(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

export function MessageBubble({ message: m }: Props): JSX.Element {
  if (m.kind === 'system') {
    return (
      <div
        className="mx-auto my-2 rounded-full border border-border-subtle bg-bg-subtle px-3 py-1 text-xs text-fg-muted"
        aria-label="system event"
      >
        <span className="font-mono">[{m.seq}]</span>{' '}
        <span>{describeSystem(m.system_payload)}</span>{' '}
        <span className="text-fg-subtle">· {fmtTs(m.created_at)}</span>
      </div>
    );
  }

  const isFounder = m.speaker === 'founder';
  const baseColor = isFounder
    ? 'border-accent/40 bg-accent/10'
    : 'border-border bg-bg-raised';

  return (
    <article
      className={`rounded-lg border p-3 ${
        m.kind === 'decline' ? 'border-tier-red/40 bg-tier-red/10' : baseColor
      }`}
    >
      <header className="mb-1 flex items-baseline gap-2 text-xs">
        <span className="font-semibold text-fg">{m.speaker}</span>
        {m.addressed_to && m.addressed_to.length > 0 && (
          <span className="text-fg-muted">→ {m.addressed_to.join(', ')}</span>
        )}
        <span className="ml-auto font-mono text-fg-subtle">
          #{m.seq} · {fmtTs(m.created_at)}
        </span>
      </header>
      {m.kind === 'decline' ? (
        <p className="text-sm text-tier-red">
          <strong>Declined:</strong> {m.decline_reason}
        </p>
      ) : (
        <div className="prose prose-invert prose-sm max-w-none text-fg">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>
            {m.body_markdown ?? ''}
          </ReactMarkdown>
        </div>
      )}
    </article>
  );
}

function describeSystem(payload: Record<string, unknown> | null): string {
  if (!payload) return 'system event';
  const ev = String(payload.event ?? '');
  switch (ev) {
    case 'invited':
      return `invited ${payload.agent}`;
    case 'extended':
      return `turn cap raised to ${payload.new_cap}`;
    case 'archive_requested':
      return 'archive requested';
    case 'archived':
      return 'archived';
    case 'abandoned':
      return `abandoned${payload.reason ? `: ${payload.reason}` : ''}`;
    default:
      return ev || JSON.stringify(payload);
  }
}
