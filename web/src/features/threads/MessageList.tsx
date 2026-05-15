import { useEffect, useRef } from 'react';
import type { ThreadMessage } from '@/lib/api/types';
import { MessageBubble } from './MessageBubble';

interface Props {
  messages: ThreadMessage[];
  loading?: boolean;
}

export function MessageList({ messages, loading }: Props): JSX.Element {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Auto-scroll to bottom whenever message count changes.
    // Guarded so tests (jsdom — no scrollIntoView) don't blow up.
    if (typeof endRef.current?.scrollIntoView === 'function') {
      endRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [messages.length]);

  return (
    <div className="flex h-full flex-col gap-2 overflow-auto px-4 py-3">
      {loading && messages.length === 0 && (
        <p className="text-xs text-fg-muted">Loading messages…</p>
      )}
      {!loading && messages.length === 0 && (
        <p className="text-xs text-fg-muted">No messages yet.</p>
      )}
      {messages.map((m) => (
        <MessageBubble key={`${m.seq}-${m.speaker}-${m.kind}`} message={m} />
      ))}
      <div ref={endRef} />
    </div>
  );
}
