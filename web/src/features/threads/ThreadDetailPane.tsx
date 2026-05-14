import { useMemo } from 'react';
import { useOrgSlug } from '@/lib/orgSlug';
import type { ThreadMessage } from '@/lib/api/types';
import { MessageList } from './MessageList';
import { ThreadHeader } from './ThreadHeader';
import {
  useThread,
  useThreadMessages,
  useThreadTailSSE,
} from './hooks';

interface Props {
  threadId: string;
  onInvite: () => void;
  onArchive: () => void;
  onAbandon: () => void;
  onExtend: () => void;
  footer: React.ReactNode;
}

export function ThreadDetailPane({
  threadId,
  onInvite,
  onArchive,
  onAbandon,
  onExtend,
  footer,
}: Props): JSX.Element {
  const slug = useOrgSlug();
  const threadQuery = useThread(slug, threadId);
  const messagesQuery = useThreadMessages(slug, threadId);

  // Subscribe to tail SSE while this pane is mounted.
  useThreadTailSSE(slug, threadId);

  const messages: ThreadMessage[] = useMemo(() => {
    // Prefer the live `thread-messages` cache (updated by SSE). Fall back to
    // the initial messages embedded in /threads/{id}.
    if (messagesQuery.data) return messagesQuery.data.messages;
    return threadQuery.data?.messages ?? [];
  }, [messagesQuery.data, threadQuery.data]);

  if (threadQuery.isLoading) {
    return (
      <section className="flex h-full items-center justify-center text-fg-muted">
        <p className="text-sm">Loading…</p>
      </section>
    );
  }
  if (threadQuery.isError || !threadQuery.data) {
    return (
      <section className="flex h-full items-center justify-center text-tier-red">
        <p className="text-sm">Failed to load thread.</p>
      </section>
    );
  }

  return (
    <section className="flex h-full flex-col">
      <ThreadHeader
        thread={threadQuery.data}
        onInvite={onInvite}
        onArchive={onArchive}
        onAbandon={onAbandon}
        onExtend={onExtend}
      />
      <div className="flex-1 overflow-hidden">
        <MessageList messages={messages} loading={messagesQuery.isLoading} />
      </div>
      <footer className="border-t border-border bg-bg-subtle p-3">{footer}</footer>
    </section>
  );
}
