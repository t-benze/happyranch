/**
 * Two-pane threads composition.
 *
 * Owns every TanStack Query hook + SSE subscription for this screen, plus
 * dialog state and routing. The visual pieces — InboxRow, ThreadHeader,
 * MessageBubble, Composer, EmptyState — are pure-prop patterns from
 * @/design-system/patterns/. The `?` HelpDrawer is owned globally by
 * `HelpDrawerHost` mounted in AppShell, not by this page.
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Tabs, TabsList, TabsTrigger } from '@/design-system/primitives/Tabs';
import { ThreadsLayout } from '@/design-system/layouts/ThreadsLayout';
import { Composer } from '@/design-system/patterns/Composer';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { InboxRow } from '@/design-system/patterns/InboxRow';
import { KbdChip } from '@/design-system/patterns/KbdChip';
import { MessageBubble, type MessageVariant } from '@/design-system/patterns/MessageBubble';
import { ThreadHeader } from '@/design-system/patterns/ThreadHeader';
import { ApiError } from '@/lib/api';
import type { ThreadMessage } from '@/lib/api/types';
import { useAgentsList } from '@/hooks/agents';
import { isGPrefixArmed } from '@/hooks/global-jump';
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
import { ResponderStatusStrip } from './ResponderStatusStrip';
import { describeError } from './strings';

const STATUS_TABS = ['open', 'archived', 'abandoned'] as const;
type StatusTab = (typeof STATUS_TABS)[number];

export function ThreadsPage(): JSX.Element {
  const routes = useThreadRoutes();
  const navigate = useNavigate();
  const { slug, thread_id: threadId } = useParams<{ slug: string; thread_id: string }>();
  const composerFocusRef = useRef<(() => void) | null>(null);

  // Inbox state
  const [status, setStatus] = useState<StatusTab>('open');
  const [filter, setFilter] = useState('');
  useThreadsInboxSSE();
  const agentsQuery = useAgentsList();
  const agents = useMemo(() => agentsQuery.data?.agents ?? [], [agentsQuery.data]);
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
      await sendFollowUp.mutateAsync({ body_markdown: markdown });
    } catch (err) {
      if (err instanceof ApiError) {
        setComposerError(describeError(err.code, `HTTP ${err.status}`));
      } else {
        setComposerError(String(err));
      }
      throw err;
    }
  };

  // Keyboard shortcuts: N / I / A / X / F / R. Limited to when no input is
  // focused. The `?` help trigger lives on the global `HelpDrawerHost`.
  // `isGPrefixArmed()` keeps `g i / g a / g d`-style chords from also
  // firing the bare-letter dialogs here.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || target?.isContentEditable) return;
      if (isGPrefixArmed()) return;
      if (e.key === 'n' || e.key === 'N') { e.preventDefault(); openNew(); }
      else if (threadId && (e.key === 'i' || e.key === 'I')) { e.preventDefault(); setShowInvite(true); }
      else if (threadId && (e.key === 'a' || e.key === 'A')) { e.preventDefault(); setShowArchive(true); }
      else if (threadId && (e.key === 'x' || e.key === 'X')) { e.preventDefault(); setShowAbandon(true); }
      else if (threadId && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); openForward(); }
      else if (threadId && (e.key === 'r' || e.key === 'R')) {
        e.preventDefault();
        composerFocusRef.current?.();
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, activeThread.data, activeMessagesQuery.data]);

  return (
    <>
      <ThreadsLayout
        inbox={(
      <aside className="border-border-default bg-surface-sunken flex h-full flex-col border-r">
        <header className="border-border-default border-b px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-overline text-text-muted tracking-wide uppercase">Inbox</h2>
            <Button
              size="sm"
              onClick={openNew}
              aria-label="New thread"
              title="New thread (N)"
            >
              + New
            </Button>
          </div>
          <Input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter…"
            className="text-caption mt-2 h-7 px-2 py-1"
            aria-label="Filter threads"
          />
          <Tabs
            className="mt-2"
            value={status}
            onValueChange={(v) => setStatus(v as StatusTab)}
            aria-label="Status filter"
          >
            <TabsList>
              {STATUS_TABS.map((s) => (
                <TabsTrigger key={s} value={s}>
                  {s}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        </header>
        <div className="flex-1 overflow-auto p-2">
          {threadsQuery.isLoading && (
            <p className="text-caption text-text-muted px-2 py-4">Loading…</p>
          )}
          {threadsQuery.isError && (
            <p className="text-caption text-feedback-danger px-2 py-4">
              Failed to load threads.
            </p>
          )}
          {!threadsQuery.isLoading && threads.length === 0 && (
            <p className="text-caption text-text-muted px-2 py-4">
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
        )}
        detail={threadId ? (
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
              agents={agents}
              threadId={threadId ?? ''}
              disabled={activeThread.data?.status !== 'open'}
              pending={sendFollowUp.isPending}
              errorMessage={composerError}
              helper="Sends as founder — all participants are notified."
              onSend={onSendFollowUp}
              registerFocus={(focus) => { composerFocusRef.current = focus; }}
            />
          }
          slug={slug}
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
      />

      <NewThreadDialog
        open={showNew}
        onClose={() => setShowNew(false)}
        prefill={newPrefill}
        onCreated={(newId) => navigate(routes.detail(newId))}
        agents={agents}
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
    </>
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
  /** Active org slug — used to build the cross-surface "View audit" link. */
  slug: string | undefined;
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
  slug,
}: DetailColumnProps): JSX.Element {
  if (loading) {
    return (
      <section className="text-text-muted flex h-full items-center justify-center">
        <p className="text-body">Loading…</p>
      </section>
    );
  }
  if (errored || !thread) {
    return (
      <section className="text-feedback-danger flex h-full items-center justify-center">
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
            {slug && thread.participants[0] && (
              <Link
                to={`/orgs/${slug}/audit?agent=${encodeURIComponent(thread.participants[0])}`}
                className="text-accent self-center text-xs hover:underline"
                title="View audit log for the lead participant"
              >
                Audit ↗
              </Link>
            )}
          </>
        }
      />
      <div className="flex-1 overflow-hidden">
        <MessageTranscript messages={messages} loading={messagesLoading} slug={slug} />
      </div>
      <footer className="border-border-default bg-surface-sunken border-t p-3">
        {composer}
      </footer>
    </section>
  );
}

interface TranscriptProps {
  messages: ThreadMessage[];
  loading: boolean;
  /** Active org slug — used to build cross-surface task links in system messages. */
  slug?: string;
}

function MessageTranscript({ messages, loading, slug }: TranscriptProps): JSX.Element {
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
        <div key={`${m.seq}-${m.speaker}-${m.kind}`}>
          <MessageBubble
            variant={messageVariant(m)}
            seq={m.seq}
            speaker={m.kind === 'system' ? undefined : m.speaker}
            speakerRole={m.speaker === 'founder' ? 'founder' : 'worker'}
            timestamp={m.created_at}
            body={m.body_markdown}
            declineReason={m.decline_reason}
            systemDescription={m.kind === 'system' ? describeSystem(m.system_payload, slug) : undefined}
          />
          {m.kind === 'message' && (
            <ResponderStatusStrip statuses={m.responder_status ?? []} />
          )}
        </div>
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

function describeSystem(payload: Record<string, unknown> | null, slug?: string): React.ReactNode {
  if (!payload) return 'system event';
  // Real API payloads use kind_tag; mock/legacy payloads use event.
  const tag = String(payload.kind_tag ?? payload.event ?? '');
  switch (tag) {
    case 'invited':
      return `invited ${payload.agent}`;
    case 'participant_added':
      return `added ${payload.agent_name}`;
    case 'extended':
      return `turn cap raised to ${payload.new_cap}`;
    case 'turn_cap_extended':
      return `turn cap raised to ${payload.new_cap}`;
    case 'archive_requested':
      return 'archive requested';
    case 'archived':
      return 'archived';
    case 'abandoned':
      return `abandoned${payload.reason ? `: ${payload.reason}` : ''}`;
    case 'task_dispatched': {
      const taskId = String(payload.task_id ?? '');
      const taskLink = slug && taskId
        ? <Link to={`/orgs/${slug}/tasks/${taskId}`} className="underline">{taskId}</Link>
        : taskId;
      return <>dispatched {taskLink}</>;
    }
    case 'task_completed': {
      const taskId = String(payload.task_id ?? '');
      const taskLink = slug && taskId
        ? <Link to={`/orgs/${slug}/tasks/${taskId}`} className="underline">{taskId}</Link>
        : taskId;
      const summary = payload.final_output_summary
        ? String(payload.final_output_summary).slice(0, 240)
        : null;
      return (
        <>
          task {taskLink} completed{summary ? ` · ${summary}` : ''}
        </>
      );
    }
    case 'task_failed': {
      const taskId = String(payload.task_id ?? '');
      const taskLink = slug && taskId
        ? <Link to={`/orgs/${slug}/tasks/${taskId}`} className="underline">{taskId}</Link>
        : taskId;
      const chainLength = typeof payload.revisit_chain_length === 'number' ? payload.revisit_chain_length : 1;
      const revisitSuffix = chainLength > 1
        ? ` · after ${chainLength - 1} ${chainLength - 1 === 1 ? 'revisit' : 'revisits'}`
        : '';
      const cancelledSuffix = payload.cancelled ? ' · founder-cancelled' : '';
      return (
        <>
          task {taskLink} failed{cancelledSuffix}{revisitSuffix}
        </>
      );
    }
    default:
      return tag || JSON.stringify(payload);
  }
}
