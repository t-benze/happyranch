/**
 * Two-pane threads composition.
 *
 * Owns every TanStack Query hook + SSE subscription for this screen, plus
 * dialog state and routing. The visual pieces — InboxRow, ThreadHeader,
 * MessageBubble, Composer, HelpSheet, EmptyState — are pure-prop patterns
 * from @/design-system/patterns/.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { Composer } from '@/design-system/patterns/Composer';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { HelpSheet } from '@/design-system/patterns/HelpSheet';
import { InboxRow } from '@/design-system/patterns/InboxRow';
import { KbdChip } from '@/design-system/patterns/KbdChip';
import { MessageBubble, type MessageVariant } from '@/design-system/patterns/MessageBubble';
import { ThreadHeader } from '@/design-system/patterns/ThreadHeader';
import { ApiError } from '@/lib/api';
import type { ThreadMessage } from '@/lib/api/types';
import {
  useSendFollowUp,
  useThread,
  useThreadMessages,
  useThreadRoutes,
  useThreadTailSSE,
  useThreadsInboxSSE,
  useThreadsList,
} from '@/hooks/threads';
import { AbandonDialog } from './AbandonDialog';
import { ArchiveDialog } from './ArchiveDialog';
import { ExtendDialog } from './ExtendDialog';
import { InviteDialog } from './InviteDialog';
import { NewThreadDialog } from './NewThreadDialog';
import { describeError } from './strings';
import { THREADS_SHORTCUTS, THREADS_SHORTCUTS_FOOTNOTE } from './threads-shortcuts';

const STATUS_TABS = ['open', 'archived', 'abandoned'] as const;
type StatusTab = (typeof STATUS_TABS)[number];

export function ThreadsPage(): JSX.Element {
  const routes = useThreadRoutes();
  const navigate = useNavigate();
  const { thread_id: threadId } = useParams<{ thread_id: string }>();
  const composerFocusRef = useRef<(() => void) | null>(null);

  // Inbox state
  const [status, setStatus] = useState<StatusTab>('open');
  const [filter, setFilter] = useState('');
  useThreadsInboxSSE();
  const threadsQuery = useThreadsList({ status });
  const threads = useMemo(() => {
    const all = threadsQuery.data?.threads ?? [];
    if (!filter.trim()) return all;
    const needle = filter.toLowerCase();
    return all.filter(
      (t) =>
        t.subject.toLowerCase().includes(needle) ||
        t.thread_id.toLowerCase().includes(needle),
    );
  }, [threadsQuery.data, filter]);

  // Active-thread data
  const activeThread = useThread(threadId);
  const activeMessagesQuery = useThreadMessages(threadId);
  useThreadTailSSE(threadId);
  const messages: ThreadMessage[] = useMemo(() => {
    if (activeMessagesQuery.data) return activeMessagesQuery.data.messages;
    return activeThread.data?.messages ?? [];
  }, [activeMessagesQuery.data, activeThread.data]);

  // Send mutation lives at the page level so the Composer pattern is pure.
  const sendFollowUp = useSendFollowUp(threadId ?? '');
  const [composerError, setComposerError] = useState<string | null>(null);

  // Dialog state
  const [showNew, setShowNew] = useState(false);
  const [newPrefill, setNewPrefill] = useState<
    | { subject?: string; recipients?: string[]; body?: string; forwarded_from_id?: string; forwarded_from_kind?: 'thread' | 'talk' }
    | undefined
  >(undefined);
  const [showInvite, setShowInvite] = useState(false);
  const [showArchive, setShowArchive] = useState(false);
  const [showAbandon, setShowAbandon] = useState(false);
  const [showExtend, setShowExtend] = useState(false);
  const [showHelp, setShowHelp] = useState(false);

  const openNew = () => {
    setNewPrefill(undefined);
    setShowNew(true);
  };

  const openForward = () => {
    if (!threadId || !activeThread.data) return;
    const lastFounderMsg = messages
      .filter((m) => m.kind === 'message' && m.body_markdown)
      .at(-1);
    const quoted = lastFounderMsg
      ? `> from ${threadId} by ${lastFounderMsg.speaker}\n>\n` +
        lastFounderMsg.body_markdown!
          .split('\n')
          .map((l) => `> ${l}`)
          .join('\n')
      : `> from ${threadId}`;
    setNewPrefill({
      subject: `Fwd: ${activeThread.data.subject}`,
      body: `${quoted}\n\n`,
      forwarded_from_id: threadId,
      forwarded_from_kind: 'thread',
    });
    setShowNew(true);
  };

  const onSendFollowUp = async (markdown: string) => {
    if (!threadId) return;
    setComposerError(null);
    try {
      await sendFollowUp.mutateAsync({ body_markdown: markdown, addressed_to: ['@all'] });
    } catch (err) {
      if (err instanceof ApiError) {
        setComposerError(describeError(err.code, `HTTP ${err.status}`));
      } else {
        setComposerError(String(err));
      }
      // Re-throw so the Composer pattern keeps the draft for retry.
      throw err;
    }
  };

  // Keyboard shortcuts: N / I / A / X / F / R / ?. Limited to when no input is focused.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || target?.isContentEditable) return;
      if (e.key === 'n' || e.key === 'N') { e.preventDefault(); openNew(); }
      else if (threadId && (e.key === 'i' || e.key === 'I')) { e.preventDefault(); setShowInvite(true); }
      else if (threadId && (e.key === 'a' || e.key === 'A')) { e.preventDefault(); setShowArchive(true); }
      else if (threadId && (e.key === 'x' || e.key === 'X')) { e.preventDefault(); setShowAbandon(true); }
      else if (threadId && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); openForward(); }
      else if (threadId && (e.key === 'r' || e.key === 'R')) {
        e.preventDefault();
        composerFocusRef.current?.();
      }
      else if (e.key === '?') { e.preventDefault(); setShowHelp(true); }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, activeThread.data, activeMessagesQuery.data]);

  return (
    <div className="grid h-full grid-cols-[320px_1fr] grid-rows-[minmax(0,1fr)]">
      {/* Inbox column */}
      <aside className="flex h-full flex-col border-r border-border-default bg-surface-sunken">
        <header className="border-b border-border-default px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-overline uppercase tracking-wide text-text-muted">Inbox</h2>
            <Button
              size="sm"
              onClick={openNew}
              aria-label="New thread"
              title="New thread (N)"
            >
              + New
            </Button>
          </div>
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter…"
            className="mt-2 w-full rounded-md border border-border-default bg-surface-raised px-2 py-1 text-caption text-text-primary placeholder:text-text-muted focus:border-accent-default focus:outline-none"
            aria-label="Filter threads"
          />
          <div className="mt-2 flex gap-1">
            {STATUS_TABS.map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setStatus(s)}
                className={`rounded-md px-2 py-0.5 text-caption transition-colors ${
                  status === s
                    ? 'bg-surface-raised text-text-primary'
                    : 'text-text-muted hover:text-text-primary'
                }`}
              >
                {s}
              </button>
            ))}
          </div>
        </header>
        <div className="flex-1 overflow-auto p-2">
          {threadsQuery.isLoading && (
            <p className="px-2 py-4 text-caption text-text-muted">Loading…</p>
          )}
          {threadsQuery.isError && (
            <p className="px-2 py-4 text-caption text-feedback-danger">
              Failed to load threads.
            </p>
          )}
          {!threadsQuery.isLoading && threads.length === 0 && (
            <p className="px-2 py-4 text-caption text-text-muted">
              {filter
                ? 'No threads match the filter.'
                : 'No threads yet. Press N to compose.'}
            </p>
          )}
          <div className="flex flex-col gap-1">
            {threads.map((t) => {
              const path = routes.detail(t.thread_id);
              return (
                <InboxRow
                  key={t.thread_id}
                  threadId={t.thread_id}
                  subject={t.subject}
                  status={threadStatusOrFallback(t.status)}
                  needsYou={false}
                  active={t.thread_id === threadId}
                  meta={`${t.turns_used}/${t.turn_cap} turns`}
                  href={path}
                  onSelect={() => navigate(path)}
                />
              );
            })}
          </div>
        </div>
      </aside>

      {/* Detail column */}
      {threadId ? (
        <DetailColumn
          loading={activeThread.isLoading}
          errored={activeThread.isError || !activeThread.data}
          thread={activeThread.data}
          messages={messages}
          messagesLoading={activeMessagesQuery.isLoading}
          onInvite={() => setShowInvite(true)}
          onArchive={() => setShowArchive(true)}
          onAbandon={() => setShowAbandon(true)}
          onExtend={() => setShowExtend(true)}
          composer={
            <Composer
              disabled={activeThread.data?.status !== 'open'}
              pending={sendFollowUp.isPending}
              errorMessage={composerError}
              helper="Sends as founder; @all by default."
              onSend={onSendFollowUp}
              registerFocus={(focus) => { composerFocusRef.current = focus; }}
            />
          }
        />
      ) : (
        <EmptyState
          title="Select a thread"
          body={
            <span className="inline-flex flex-wrap items-center justify-center gap-1">
              Select a thread from the inbox, or press
              <KbdChip keys={['N']} />
              to compose.
            </span>
          }
        />
      )}

      <NewThreadDialog
        open={showNew}
        onClose={() => setShowNew(false)}
        prefill={newPrefill}
        onCreated={(newId) => navigate(routes.detail(newId))}
      />
      <HelpSheet
        open={showHelp}
        onClose={() => setShowHelp(false)}
        shortcuts={THREADS_SHORTCUTS}
        footnote={THREADS_SHORTCUTS_FOOTNOTE}
      />
      {threadId && (
        <>
          <InviteDialog
            threadId={threadId}
            open={showInvite}
            onClose={() => setShowInvite(false)}
          />
          <ArchiveDialog
            threadId={threadId}
            open={showArchive}
            onClose={() => setShowArchive(false)}
          />
          <AbandonDialog
            threadId={threadId}
            open={showAbandon}
            onClose={() => setShowAbandon(false)}
          />
          <ExtendDialog
            threadId={threadId}
            currentCap={activeThread.data?.turn_cap ?? 500}
            open={showExtend}
            onClose={() => setShowExtend(false)}
          />
        </>
      )}
    </div>
  );
}

function threadStatusOrFallback(status: string): 'open' | 'archiving' | 'archived' | 'abandoned' {
  if (status === 'open' || status === 'archiving' || status === 'archived' || status === 'abandoned') return status;
  return 'open';
}

interface DetailColumnProps {
  loading: boolean;
  errored: boolean;
  thread:
    | {
        thread_id: string;
        subject: string;
        status: string;
        participants: string[];
        turns_used: number;
        turn_cap: number;
        summary: string | null;
      }
    | undefined;
  messages: ThreadMessage[];
  messagesLoading: boolean;
  onInvite: () => void;
  onArchive: () => void;
  onAbandon: () => void;
  onExtend: () => void;
  composer: JSX.Element;
}

function DetailColumn({
  loading,
  errored,
  thread,
  messages,
  messagesLoading,
  onInvite,
  onArchive,
  onAbandon,
  onExtend,
  composer,
}: DetailColumnProps): JSX.Element {
  if (loading) {
    return (
      <section className="flex h-full items-center justify-center text-text-muted">
        <p className="text-body">Loading…</p>
      </section>
    );
  }
  if (errored || !thread) {
    return (
      <section className="flex h-full items-center justify-center text-feedback-danger">
        <p className="text-body">Failed to load thread.</p>
      </section>
    );
  }
  const open = thread.status === 'open';
  return (
    <section className="flex h-full flex-col">
      <ThreadHeader
        threadId={thread.thread_id}
        subject={thread.subject}
        status={threadStatusOrFallback(thread.status)}
        participants={thread.participants}
        turnsUsed={thread.turns_used}
        turnCap={thread.turn_cap}
        archiveSummary={thread.summary}
        actions={
          <>
            <Button variant="ghost" size="sm" onClick={onInvite} disabled={!open} title="Invite (I)">Invite</Button>
            <Button variant="ghost" size="sm" onClick={onExtend} disabled={!open} title="Extend turn cap">Extend</Button>
            <Button variant="ghost" size="sm" onClick={onArchive} disabled={!open} title="Archive (A)">Archive</Button>
            <Button variant="ghost" size="sm" onClick={onAbandon} disabled={!open} title="Abandon (X)">Abandon</Button>
          </>
        }
      />
      <div className="flex-1 overflow-hidden">
        <MessageTranscript messages={messages} loading={messagesLoading} />
      </div>
      <footer className="border-t border-border-default bg-surface-sunken p-3">
        {composer}
      </footer>
    </section>
  );
}

interface TranscriptProps {
  messages: ThreadMessage[];
  loading: boolean;
}

function MessageTranscript({ messages, loading }: TranscriptProps): JSX.Element {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (typeof endRef.current?.scrollIntoView === 'function') {
      endRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [messages.length]);

  return (
    <div className="flex h-full flex-col gap-2 overflow-auto px-4 py-3">
      {loading && messages.length === 0 && (
        <p className="text-caption text-text-muted">Loading messages…</p>
      )}
      {!loading && messages.length === 0 && (
        <p className="text-caption text-text-muted">No messages yet.</p>
      )}
      {messages.map((m) => (
        <MessageBubble
          key={`${m.seq}-${m.speaker}-${m.kind}`}
          variant={messageVariant(m)}
          seq={m.seq}
          speaker={m.kind === 'system' ? undefined : m.speaker}
          speakerRole={m.speaker === 'founder' ? 'founder' : 'worker'}
          addressedTo={m.addressed_to ?? undefined}
          timestamp={m.created_at}
          body={m.body_markdown}
          declineReason={m.decline_reason}
          systemDescription={m.kind === 'system' ? describeSystem(m.system_payload) : undefined}
        />
      ))}
      <div ref={endRef} />
    </div>
  );
}

function messageVariant(m: ThreadMessage): MessageVariant {
  if (m.kind === 'system') return 'system';
  if (m.kind === 'decline') return 'decline';
  if (m.speaker === 'founder') return 'founder';
  return 'worker';
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
