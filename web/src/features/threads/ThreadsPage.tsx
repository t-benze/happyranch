/**
 * ThreadsPage — list + detail reshape (§4.2, design-overhaul).
 *
 * LIST: each row shows LAST SPEAKER, STATUS PILL,
 * and a crescent-moon marker for dream-originated threads.
 *
 * DETAIL: turn cards; SYSTEM CARDS visually distinct from agent-turn
 * cards. In-thread agent-own "ran:" cards are OMITTED (P1 — D7 deferred).
 *
 * States: Loading (skeleton), Empty (calm), Error-with-retry (§2.5.5).
 * NO persistent unread/read-state (B.3 deferred — no markRead/unread).
 *
 * Composer: BROADCAST-ONLY ("Message the thread — all participants see it").
 */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { Button } from '@/design-system/primitives/Button';
import { Input } from '@/design-system/primitives/Input';
import { Tabs, TabsList, TabsTrigger } from '@/design-system/primitives/Tabs';
import { AgentChip } from '@/design-system/patterns/AgentChip';
import { Composer } from '@/design-system/patterns/Composer';
import { CrescentMoonBadge } from '@/design-system/patterns/CrescentMoonBadge';
import { EmptyState } from '@/design-system/patterns/EmptyState';
import { InboxRow } from '@/design-system/patterns/InboxRow';
import { MessageBubble, type MessageVariant } from '@/design-system/patterns/MessageBubble';
import { StatusBadge } from '@/design-system/patterns/StatusBadge';
import { ThreadHeader } from '@/design-system/patterns/ThreadHeader';
import { artifacts as artifactsApi, ApiError } from '@/lib/api';
import type { ThreadAttachment, ThreadAttachmentRef, ThreadMessage } from '@/lib/api/types';
import { attachmentContentType, safeArtifactName } from '@/lib/threadAttachments';
import type { PendingAttachment } from '@/design-system/patterns/Composer';
import { useAgentsList } from '@/hooks/agents';
import { isGPrefixArmed } from '@/hooks/global-jump';
import {
  useAbortReplies,
  useSendFollowUp,
  useThread,
  useThreadMessages,
  useThreadRoutes,
  useThreadTailSSE,
  useThreadTasks,
  useThreadsInboxSSE,
  useThreadsList,
} from '@/hooks/threads';
import { ArchiveDialog } from './ArchiveDialog';
import { InviteDialog } from './InviteDialog';
import { RemoveParticipantDialog } from './RemoveParticipantDialog';
import { NewThreadDialog } from '@/shared/threads/NewThreadDialog';
import { ResponderStatusStrip } from './ResponderStatusStrip';
import { ResumeButton } from './ResumeButton';
import { selectInFlightResponders } from './inFlightResponders';
import { describeError, THREADS_STRINGS as S } from './strings';
import { TypingBubble } from '@/design-system/patterns/TypingBubble';

/* ------------------------------------------------------------------ */
/*  helpers                                                            */
/* ------------------------------------------------------------------ */

function useNowMs(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [active]);
  return now;
}

// Segmented inbox filter (THREADS-02). 'done' = archived; 'all' = both, derived
// client-side by merging the two per-status fetches (the list payload carries no
// finer waiting/active split, so no other bucket is honestly derivable here).
type InboxBucket = 'all' | 'open' | 'done';
const INBOX_BUCKETS: InboxBucket[] = ['all', 'open', 'done'];
const BUCKET_LABEL: Record<InboxBucket, string> = { all: 'All', open: 'Open', done: 'Done' };

function threadStatusOrFallback(status: string): 'open' | 'archived' {
  if (status === 'open' || status === 'archived') return status;
  return 'open';
}

/**
 * Derive a display label for the last speaker.
 * Returns { name, role } for AgentChip, or null if the thread has no speaker yet.
 */
function lastSpeakerChip(speaker: string | null | undefined): { name: string; role: 'manager' | 'worker' | 'founder' } | null {
  if (!speaker) return null;
  if (speaker === 'founder') return { name: 'founder', role: 'founder' };
  if (speaker === 'system') return { name: 'system', role: 'worker' };
  return { name: speaker, role: 'worker' };
}

/**
 * Role for a participant avatar chip. Participants are bare name strings, so
 * only the founder is distinguishable; every other agent renders as a worker —
 * mirrors lastSpeakerChip's honest, no-fabrication mapping (the dot is decor).
 */
function participantChipRole(name: string): 'worker' | 'founder' {
  return name === 'founder' ? 'founder' : 'worker';
}

/**
 * Aggregate the real produced artifacts across a thread's transcript:
 * every message's attachments, deduped by artifact_name (first occurrence
 * wins). Pure presentation over existing ThreadDetailResponse data — no fetch,
 * no fabrication. Guards `attachments` which older payloads may omit.
 */
function collectThreadArtifacts(messages: ThreadMessage[]): ThreadAttachment[] {
  const seen = new Map<string, ThreadAttachment>();
  for (const m of messages) {
    for (const a of m.attachments ?? []) {
      const key = a.thread_attachment_id ?? a.artifact_name;
      if (!seen.has(key)) seen.set(key, a);
    }
  }
  return [...seen.values()];
}

/* ------------------------------------------------------------------ */
/*  Loading skeleton                                                   */
/* ------------------------------------------------------------------ */

function InboxSkeleton(): JSX.Element {
  return (
    <div className="animate-pulse space-y-2 p-2">
      {[1, 2, 3, 4, 5].map((i) => (
        <div key={i} className="space-y-2 rounded-md px-3 py-2">
          <div className="flex items-center justify-between gap-2">
            <div className="bg-bg-raised h-4 w-48 rounded" />
            <div className="bg-bg-raised h-4 w-12 rounded-full" />
          </div>
          <div className="flex items-center gap-2">
            <div className="bg-bg-raised h-3 w-16 rounded" />
            <div className="bg-bg-raised h-3 w-20 rounded" />
            <div className="bg-bg-raised ml-auto h-3 w-12 rounded" />
          </div>
        </div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main page                                                          */
/* ------------------------------------------------------------------ */

export function ThreadsPage(): JSX.Element {
  const routes = useThreadRoutes();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { slug, thread_id: threadId } = useParams<{ slug: string; thread_id: string }>();
  const composerFocusRef = useRef<(() => void) | null>(null);

  // Inbox state — segmented status filter (THREADS-02).
  const [bucket, setBucket] = useState<InboxBucket>('open');
  const [filter, setFilter] = useState('');
  useThreadsInboxSSE();
  const agentsQuery = useAgentsList();
  const agents = useMemo(() => agentsQuery.data?.agents ?? [], [agentsQuery.data]);
  // Two real per-status fetches back BOTH the per-bucket counts and the list.
  // 'all' merges them client-side — no extra fetch, no new data field.
  const openQuery = useThreadsList({ status: 'open' });
  const archivedQuery = useThreadsList({ status: 'archived' });
  const openCount = openQuery.data?.threads?.length ?? 0;
  const archivedCount = archivedQuery.data?.threads?.length ?? 0;
  const counts: Record<InboxBucket, number> = {
    all: openCount + archivedCount,
    open: openCount,
    done: archivedCount,
  };
  // Org-wide dream-opened count for the header eyebrow (THREADS-04) — derived
  // across BOTH buckets from composed_from_dream_id, independent of the active
  // filter, so the count reflects the org rather than the current view.
  const dreamOpenedCount = useMemo(() => {
    const openThreads = openQuery.data?.threads ?? [];
    const archivedThreads = archivedQuery.data?.threads ?? [];
    return [...openThreads, ...archivedThreads].filter(
      (t) => t.composed_from_dream_id !== null,
    ).length;
  }, [openQuery.data, archivedQuery.data]);
  const bucketLoading =
    bucket === 'open'
      ? openQuery.isLoading
      : bucket === 'done'
        ? archivedQuery.isLoading
        : openQuery.isLoading || archivedQuery.isLoading;
  const bucketError =
    bucket === 'open'
      ? openQuery.isError
      : bucket === 'done'
        ? archivedQuery.isError
        : openQuery.isError || archivedQuery.isError;
  const threads = useMemo(() => {
    const openThreads = openQuery.data?.threads ?? [];
    const archivedThreads = archivedQuery.data?.threads ?? [];
    const base =
      bucket === 'open'
        ? openThreads
        : bucket === 'done'
          ? archivedThreads
          : [...openThreads, ...archivedThreads].sort((a, b) =>
              b.started_at.localeCompare(a.started_at),
            );
    if (!filter.trim()) return base;
    const needle = filter.toLowerCase();
    return base.filter(
      (t) =>
        t.subject.toLowerCase().includes(needle) ||
        t.thread_id.toLowerCase().includes(needle),
    );
  }, [bucket, openQuery.data, archivedQuery.data, filter]);

  // Active-thread data
  const activeThread = useThread(threadId);
  const activeMessagesQuery = useThreadMessages(threadId);
  useThreadTailSSE(threadId);
  const messages: ThreadMessage[] = useMemo(() => {
    if (activeMessagesQuery.data) return activeMessagesQuery.data.messages;
    return activeThread.data?.messages ?? [];
  }, [activeMessagesQuery.data, activeThread.data]);

  const anyWorking = useMemo(
    () =>
      messages.some((m) =>
        (m.responder_status ?? []).some((s) => s.status === 'working'),
      ),
    [messages],
  );
  const inFlight = useMemo(() => selectInFlightResponders(messages), [messages]);
  const nowMs = useNowMs(anyWorking);

  // Send mutation lives at the page level so the Composer pattern is pure.
  const sendFollowUp = useSendFollowUp(threadId ?? '');
  const abortReplies = useAbortReplies(threadId ?? '');
  const [composerError, setComposerError] = useState<string | null>(null);
  const [pendingAttachments, setPendingAttachments] = useState<PendingAttachment[]>([]);

  useEffect(() => {
    setPendingAttachments([]);
  }, [threadId]);

  // Dialog state
  const [showNew, setShowNew] = useState(false);
  const [newPrefill, setNewPrefill] = useState<
    | { subject?: string; recipients?: string[]; body?: string; forwarded_from_id?: string; forwarded_from_kind?: 'thread' }
    | undefined
  >(undefined);
  const [showInvite, setShowInvite] = useState(false);
  const [showArchive, setShowArchive] = useState(false);
  // Participant pending removal — drives the confirm dialog; null keeps it closed.
  const [removeTarget, setRemoveTarget] = useState<string | null>(null);
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

  const onSendFollowUp = async (markdown: string, attachments: PendingAttachment[]) => {
    if (!threadId || !slug) return;
    setComposerError(null);
    try {
      const refs: ThreadAttachmentRef[] = [];
      const generatedNames = new Map<string, number>();
      for (const pending of attachments) {
        let artifactName = safeArtifactName(threadId, pending.file);
        const count = (generatedNames.get(artifactName) ?? 0) + 1;
        generatedNames.set(artifactName, count);
        if (count > 1) {
          artifactName = safeArtifactName(threadId, pending.file, count);
        }
        const uploaded = await artifactsApi.uploadArtifact(slug, {
          file: pending.file,
          name: artifactName,
          agent: 'founder',
        });
        refs.push({
          artifact_name: uploaded.name,
          display_name: pending.file.name,
          content_type: attachmentContentType(pending.file),
        });
      }
      await sendFollowUp.mutateAsync({
        body_markdown: markdown.trim(),
        ...(refs.length ? { attachments: refs } : {}),
      });
      setPendingAttachments([]);
    } catch (err) {
      if (err instanceof ApiError) {
        setComposerError(describeError(err.code, `HTTP ${err.status}`));
      } else {
        setComposerError(String(err));
      }
      throw err;
    }
  };

  // Keyboard shortcuts
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === 'input' || tag === 'textarea' || target?.isContentEditable) return;
      if (isGPrefixArmed()) return;
      if (e.key === 'n' || e.key === 'N') { e.preventDefault(); openNew(); }
      else if (threadId && (e.key === 'i' || e.key === 'I')) { e.preventDefault(); setShowInvite(true); }
      else if (threadId && (e.key === 'a' || e.key === 'A')) { e.preventDefault(); setShowArchive(true); }
      else if (threadId && (e.key === 'f' || e.key === 'F')) { e.preventDefault(); openForward(); }
      else if (threadId && (e.key === 'r' || e.key === 'R') && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        composerFocusRef.current?.();
      }
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, activeThread.data, activeMessagesQuery.data]);

  // Inbox column — the full-width single-column conversation list shown on
  // /threads (no thread selected). When a thread IS selected the list column
  // collapses entirely (THREADDET-01 transcript-focus view): the detail column
  // takes the full width and the inbox is not rendered.
  const inbox = (
      <aside className="bg-surface-sunken flex h-full flex-col">
        <header className="border-border-default border-b px-3 py-3">
          <div className="flex items-start justify-between gap-2">
            {/* THREADS-04: uppercase eyebrow (org-wide thread + dream-opened
                counts) + Newsreader serif title, matching the a-threads
                Direction-A reference and the KB/Audit surfaces. */}
            <div className="min-w-0 flex-1">
              <p className="text-text-muted text-xs font-medium tracking-wide uppercase">
                {S.headerEyebrow(counts.all, dreamOpenedCount)}
              </p>
              <h1 className="font-display text-display text-text-primary mt-1 font-medium">
                {S.pageTitle}
              </h1>
            </div>
            <Button
              size="sm"
              onClick={openNew}
              aria-label="New thread"
              title="New thread (N)"
            >
              {S.newThread}
            </Button>
          </div>
          <Input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={S.filterPlaceholder}
            className="text-caption mt-2 h-7 px-2 py-1"
            aria-label="Filter threads"
          />
          <Tabs
            className="mt-2"
            value={bucket}
            onValueChange={(v) => setBucket(v as InboxBucket)}
          >
            <TabsList aria-label="Status filter">
              {INBOX_BUCKETS.map((b) => {
                const loading =
                  b === 'all'
                    ? openQuery.isLoading || archivedQuery.isLoading
                    : b === 'done'
                      ? archivedQuery.isLoading
                      : openQuery.isLoading;
                return (
                  <TabsTrigger key={b} value={b}>
                    {BUCKET_LABEL[b]}
                    <span className="text-text-muted ml-1 text-xs tabular-nums">
                      {loading ? '…' : counts[b]}
                    </span>
                  </TabsTrigger>
                );
              })}
            </TabsList>
          </Tabs>
        </header>
        <div className="flex-1 overflow-auto p-2">
          {/* Loading skeleton */}
          {bucketLoading && <InboxSkeleton />}

          {/* Error with retry — §2.5.5 */}
          {bucketError && (
            <div className="space-y-3 p-4 text-center">
              <p className="text-feedback-danger text-sm">{S.errorTitle}</p>
              <p className="text-text-muted text-xs">{S.errorBody}</p>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  queryClient.invalidateQueries({
                    queryKey: ['threads', slug],
                  })
                }
              >
                {S.retry}
              </Button>
            </div>
          )}

          {/* Empty — calm §2.5.5 */}
          {!bucketLoading && !bucketError && threads.length === 0 && (
            <EmptyState
              title={S.emptyTitle}
              body={
                <span>
                  {filter ? S.filterEmpty : S.emptyBody}
                </span>
              }
            />
          )}

          {/* Populated list */}
          {!bucketLoading && !bucketError && threads.length > 0 && (
            <div className="flex flex-col gap-1">
              {threads.map((t) => {
                const path = routes.detail(t.thread_id);
                const speaker = lastSpeakerChip(t.last_speaker);
                return (
                  <InboxRow
                    key={t.thread_id}
                    threadId={t.thread_id}
                    subject={t.subject}
                    lastSpeaker={speaker ?? undefined}
                    status={threadStatusOrFallback(t.status)}
                    needsYou={false}
                    active={t.thread_id === threadId}
                    fromDream={!!t.composed_from_dream_id}
                    meta={undefined}
                    href={path}
                    onSelect={() => navigate(path)}
                  />
                );
              })}
            </div>
          )}
        </div>
      </aside>
  );

  return (
    <>
      {threadId ? (
        // Transcript-focus view (THREADDET-01): the list column collapses and
        // the detail column (transcript + composer + right rail) takes the full
        // width. The back link returns to the single-column list.
        <DetailColumn
          loading={activeThread.isLoading}
          errored={activeThread.isError || !activeThread.data}
          thread={activeThread.data}
          messages={messages}
          messagesLoading={activeMessagesQuery.isLoading}
          nowMs={nowMs}
          backHref={routes.inbox()}
          onInvite={() => setShowInvite(true)}
          onArchive={() => setShowArchive(true)}
          onRemoveParticipant={setRemoveTarget}
          composer={
            <Composer
              agents={agents}
              threadId={threadId ?? ''}
              orgSlug={slug ?? ''}
              disabled={activeThread.data?.status !== 'open'}
              pending={sendFollowUp.isPending}
              errorMessage={composerError}
              helper={S.composerHelper}
              onSend={onSendFollowUp}
              attachments={pendingAttachments}
              onAttachmentsChange={setPendingAttachments}
              registerFocus={(focus) => { composerFocusRef.current = focus; }}
              hasInFlightResponders={inFlight.length > 0}
              isAborting={abortReplies.isPending}
              onAbortReplies={() => { abortReplies.mutateAsync().catch(() => {}); }}
            />
          }
          slug={slug}
          threadId={threadId}
        />
      ) : (
        inbox
      )}

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
            agents={agents}
          />
          <ArchiveDialog
            threadId={threadId}
            open={showArchive}
            onClose={() => setShowArchive(false)}
          />
          <RemoveParticipantDialog
            threadId={threadId}
            agentName={removeTarget}
            open={removeTarget !== null}
            onClose={() => setRemoveTarget(null)}
          />

        </>
      )}
    </>
  );
}

/* ------------------------------------------------------------------ */
/*  Detail column                                                      */
/* ------------------------------------------------------------------ */

interface DetailColumnProps {
  loading: boolean;
  errored: boolean;
  threadId: string | undefined;
  thread:
    | {
        thread_id: string;
        subject: string;
        status: string;
        started_at: string;
        participants: string[];
        summary: string | null;
        composed_from_dream_id?: string | null;
      }
    | undefined;
  messages: ThreadMessage[];
  messagesLoading: boolean;
  nowMs: number;
  /** Back-link target — returns to the single-column thread list (THREADDET-01). */
  backHref: string;
  onInvite: () => void;
  onArchive: () => void;
  /** Open the confirm-remove dialog for the given participant. */
  onRemoveParticipant: (name: string) => void;
  composer: JSX.Element;
  slug: string | undefined;
}

function DetailColumn({
  loading,
  errored,
  threadId,
  thread,
  messages,
  messagesLoading,
  nowMs,
  backHref,
  onInvite,
  onArchive,
  onRemoveParticipant,
  composer,
  slug,
}: DetailColumnProps): JSX.Element {
  const queryClient = useQueryClient();
  // Real produced artifacts aggregated from the transcript (THREADDET-02).
  // Computed before the early returns so the hook order stays stable.
  const artifacts = useMemo(() => collectThreadArtifacts(messages), [messages]);
  // Tasks dispatched from this thread (THR-061). Called before the early
  // returns so the hook order stays stable; self-gates on threadId.
  const threadTasks = useThreadTasks(threadId);
  // Back affordance — the list column is collapsed in this view, so the link
  // back to the single-column thread list must stay reachable in every state.
  const backNav = (
    <div className="bg-surface-sunken px-4 pt-3">
      <Link
        to={backHref}
        className="text-text-muted hover:text-text-primary text-xs transition-colors"
      >
        ‹ All threads
      </Link>
    </div>
  );

  // Loading skeleton
  if (loading) {
    return (
      <section className="flex h-full flex-col">
        {backNav}
        <div className="border-border-subtle animate-pulse space-y-2 border-b px-4 py-3">
          <div className="bg-bg-raised h-5 w-64 rounded" />
          <div className="bg-bg-raised h-3 w-48 rounded" />
        </div>
        <div className="flex flex-1 items-center justify-center">
          <p className="text-text-muted text-body">{S.loadingMessages}</p>
        </div>
      </section>
    );
  }

  // Error with retry — §2.5.5
  if (errored || !thread) {
    return (
      <section className="flex h-full flex-col">
        {backNav}
        <div className="flex flex-1 flex-col items-center justify-center space-y-3 p-4">
          <p className="text-feedback-danger text-body">{S.detailError}</p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              queryClient.invalidateQueries({
                queryKey: ['thread', slug, threadId],
              });
              queryClient.invalidateQueries({
                queryKey: ['thread-messages', slug, threadId],
              });
            }}
          >
            {S.retry}
          </Button>
        </div>
      </section>
    );
  }

  const open = thread.status === 'open';
  const isDreamOriginated = !!thread.composed_from_dream_id;
  const statusPillCls =
    thread.status === 'open'
      ? 'bg-accent-soft text-accent-text'
      : 'bg-surface-sunken border border-border-default text-text-muted';

  return (
    <section className="flex h-full flex-col">
      {backNav}
      <ThreadHeader
        threadId={thread.thread_id}
        subject={thread.subject}
        status={threadStatusOrFallback(thread.status)}
        participants={thread.participants}
        archiveSummary={thread.summary}
        dreamOriginated={isDreamOriginated}
        actions={
          <>
            <Button variant="ghost" size="sm" onClick={onInvite} disabled={!open} title="Invite (I)">Invite</Button>
            <Button variant="ghost" size="sm" onClick={onArchive} disabled={!open} title="Archive (A)">Archive</Button>
            {thread.status === 'archived' && <ResumeButton threadId={thread.thread_id} />}
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
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex-1 overflow-auto">
            <ThreadDetailTranscript
              messages={messages}
              loading={messagesLoading}
              slug={slug}
              threadId={threadId}
              nowMs={nowMs}
            />
          </div>
          <footer className="border-border-default bg-surface-sunken border-t p-3">
            {composer}
          </footer>
        </div>
        {/* Properties rail — 244px wide, Direction-A Pasture. Structured as
            Participants (avatars) · properties · Artifacts (THREADDET-02). */}
        <aside
          aria-label="Thread properties"
          className="border-border-default bg-surface-sunken w-rail flex shrink-0 flex-col gap-3 overflow-auto border-l p-4"
        >
          {/* Participants — avatar chips (AgentChip idiom, role-colored dot) */}
          <div>
            <h3 className="text-text-muted mb-1 text-xs font-semibold tracking-wider uppercase">Participants</h3>
            {thread.participants.length > 0 ? (
              <ul className="space-y-1">
                {thread.participants.map((p) => (
                  <li key={p} className="group flex items-center justify-between gap-2">
                    <AgentChip name={p} role={participantChipRole(p)} />
                    {open && (
                      <button
                        type="button"
                        aria-label={`Remove ${p}`}
                        title={`Remove ${p}`}
                        onClick={() => onRemoveParticipant(p)}
                        className="text-text-muted hover:text-feedback-danger shrink-0 rounded px-1 text-xs leading-none opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
                      >
                        ✕
                      </button>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-text-muted text-xs">none</p>
            )}
          </div>

          {/* Tasks from this thread — read-only list of dispatched tasks,
              newest-first (server-ordered; do NOT re-sort). THR-061. */}
          <div>
            <h3 className="text-text-muted mb-1 text-xs font-semibold tracking-wider uppercase">Tasks from this thread</h3>
            {threadTasks.isLoading ? (
              <p className="text-text-muted text-xs">Loading…</p>
            ) : threadTasks.isError ? (
              <p className="text-feedback-danger text-xs">Couldn’t load tasks</p>
            ) : threadTasks.data && threadTasks.data.length > 0 ? (
              // Founder ruling (THR-061 seq79): the thread-tasks rail shows
              // STATUS-PILL + ID — overriding the earlier msg51 id-only note.
              // Two elements only (real t.status + linked id); no invented
              // fields (honesty fence). Pill + id wrap so the narrow rail never
              // overflows on long status labels.
              <ul className="space-y-1.5">
                {threadTasks.data.map((t) => (
                  <li key={t.id} className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <StatusBadge status={t.status} />
                    {slug ? (
                      <Link
                        to={`/orgs/${slug}/tasks/${t.id}`}
                        className="text-accent font-mono text-xs hover:underline"
                      >
                        {t.id}
                      </Link>
                    ) : (
                      <span className="text-text-secondary font-mono text-xs">{t.id}</span>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-text-muted text-xs">No tasks dispatched from this thread yet</p>
            )}
          </div>

          {/* Status */}
          <div>
            <h3 className="text-text-muted mb-1 text-xs font-semibold tracking-wider uppercase">Status</h3>
            <span
              className={`inline-flex items-center rounded-full px-2 py-px text-xs leading-relaxed font-semibold ${statusPillCls}`}
            >
              {thread.status === 'open' ? 'active' : 'archived'}
            </span>
          </div>

          {/* Opened */}
          <div>
            <h3 className="text-text-muted mb-1 text-xs font-semibold tracking-wider uppercase">Opened</h3>
            <p className="text-text-secondary text-xs">
              {new Date(thread.started_at).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' })}
            </p>
          </div>

          {/* Artifacts — real attachments produced across the transcript,
              deduped by artifact_name; rendered only when some exist. Neutral
              text entries (the transcript bubbles carry the download links). */}
          {artifacts.length > 0 && (
            <div>
              <h3 className="text-text-muted mb-1 text-xs font-semibold tracking-wider uppercase">Artifacts</h3>
              <ul className="space-y-1">
                {artifacts.map((a) => (
                  <li
                    key={a.thread_attachment_id ?? a.artifact_name}
                    className="text-text-secondary block truncate text-xs"
                    title={a.display_name}
                  >
                    {a.display_name}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Thread ID */}
          <div>
            <h3 className="text-text-muted mb-1 text-xs font-semibold tracking-wider uppercase">ID</h3>
            <p className="text-text-secondary font-mono text-xs">{thread.thread_id}</p>
          </div>

          {/* Dream marker */}
          {isDreamOriginated && (
            <div>
              <h3 className="text-text-muted mb-1 text-xs font-semibold tracking-wider uppercase">Origin</h3>
              <span className="bg-accent-soft text-accent-text inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold">
                <CrescentMoonBadge className="h-3 w-3" />
                dream
              </span>
            </div>
          )}

          {/* Archive summary */}
          {thread.summary && (
            <div>
              <h3 className="text-text-muted mb-1 text-xs font-semibold tracking-wider uppercase">Summary</h3>
              <p className="text-text-secondary text-xs leading-relaxed">{thread.summary}</p>
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Thread detail transcript — turn cards                              */
/* ------------------------------------------------------------------ */

interface TranscriptProps {
  messages: ThreadMessage[];
  loading: boolean;
  slug?: string;
  threadId?: string;
  nowMs?: number;
}

function ThreadDetailTranscript({ messages, loading, slug, threadId, nowMs }: TranscriptProps): JSX.Element {
  const endRef = useRef<HTMLDivElement>(null);

  // Agents mid-reply (working) or waiting to reply (queued)
  const inFlight = useMemo(() => selectInFlightResponders(messages), [messages]);
  const inFlightKey = inFlight.map((s) => `${s.agent_name}:${s.status}`).join(',');

  useEffect(() => {
    if (typeof endRef.current?.scrollIntoView === 'function') {
      endRef.current.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }
  }, [messages.length, inFlightKey]);

  // Error with retry for messages
  if (loading && messages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-text-muted text-caption">{S.loadingMessages}</p>
      </div>
    );
  }

  if (!loading && messages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-text-muted text-caption">{S.noMessages}</p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col gap-2 overflow-auto px-4 py-3">
      {messages.map((m) => {
        const variant = messageVariant(m);
        return (
          <div key={`${m.seq}-${m.speaker}-${m.kind}`}>
            {/* System cards — visually distinct */}
            {variant === 'system' ? (
              <SystemCard
                seq={m.seq}
                timestamp={m.created_at}
                systemPayload={m.system_payload}
                slug={slug}
              />
            ) : (
              <MessageBubble
                variant={variant}
                seq={m.seq}
                speaker={m.speaker}
                speakerRole={m.speaker === 'founder' ? 'founder' : 'worker'}
                timestamp={m.created_at}
                body={m.body_markdown}
                declineReason={m.decline_reason}
                attachments={m.attachments}
                onAttachmentDownload={slug && threadId ? (attachment) => {
                  if (attachment.thread_attachment_id) {
                    artifactsApi.downloadThreadAttachment(slug, threadId, attachment.thread_attachment_id, attachment.display_name);
                  } else {
                    artifactsApi.downloadArtifact(slug, attachment.artifact_name);
                  }
                } : undefined}
              />
            )}
            {m.kind === 'message' && (
              <ResponderStatusStrip statuses={m.responder_status ?? []} nowMs={nowMs} />
            )}
          </div>
        );
      })}
      {inFlight.map((s) => (
        <TypingBubble
          key={`typing-${s.agent_name}`}
          agentName={s.agent_name}
          status={s.status as 'queued' | 'working'}
          startedAt={s.started_at}
          nowMs={nowMs}
        />
      ))}
      <div ref={endRef} />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  System card — visually distinct from agent-turn cards              */
/* ------------------------------------------------------------------ */

interface SystemCardProps {
  seq: number;
  timestamp: string;
  systemPayload: Record<string, unknown> | null;
  slug?: string;
}

function SystemCard({ seq, timestamp, systemPayload, slug }: SystemCardProps): JSX.Element {
  const description = describeSystem(systemPayload, slug);
  return (
    <div className="bg-surface-sunken border-border-subtle flex items-center gap-2 rounded-md border px-2 py-1.5">
      <span className="text-caption text-text-muted font-mono">{seq}</span>
      <span className="text-caption bg-bg-raised text-text-muted rounded-full px-1.5 py-0.5 font-medium uppercase">
        {S.systemEventLabel}
      </span>
      <span className="text-text-secondary flex-1 text-xs">{description}</span>
      <time
        dateTime={timestamp}
        className="text-caption text-text-disabled shrink-0"
      >
        {new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
      </time>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Message variant logic (excludes ran: cards — D7 deferred)          */
/* ------------------------------------------------------------------ */

function messageVariant(m: ThreadMessage): MessageVariant {
  // Omit in-thread agent-own ran: cards (D7 deferred, P1)
  // These would be system messages with kind_tag matching ran: command patterns
  if (m.kind === 'system') return 'system';
  if (m.kind === 'decline') return 'decline';
  if (m.speaker === 'founder') return 'founder';
  return 'worker';
}

/* ------------------------------------------------------------------ */
/*  System event descriptions                                          */
/* ------------------------------------------------------------------ */

function describeSystem(payload: Record<string, unknown> | null, slug?: string): React.ReactNode {
  if (!payload) return 'system event';
  const tag = String(payload.kind_tag ?? payload.event ?? '');
  switch (tag) {
    case 'invited':
      return `invited ${payload.agent}`;
    case 'participant_added':
      return `added ${payload.agent_name}`;
    case 'participant_removed':
      return `removed ${payload.agent_name}`;
    case 'archive_requested':
      return 'archive requested';
    case 'archived':
      return 'archived';
    case 'resumed':
      return 'resumed';
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
      const cancelledSuffix = payload.cancelled ? ' · founder-cancelled' : '';
      const revisitTaskId = payload.revisit_task_id ? String(payload.revisit_task_id) : null;
      const chainLength = typeof payload.revisit_chain_length === 'number' ? payload.revisit_chain_length : 1;
      let revisitSuffix: React.ReactNode = null;
      if (revisitTaskId) {
        const successorLink = slug
          ? <Link to={`/orgs/${slug}/tasks/${revisitTaskId}`} className="underline">{revisitTaskId}</Link>
          : revisitTaskId;
        revisitSuffix = <> · revisiting as {successorLink}</>;
      } else if (chainLength > 1) {
        revisitSuffix = ' · no further revisits';
      }
      return (
        <>
          task {taskLink} failed{cancelledSuffix}{revisitSuffix}
        </>
      );
    }
    case 'task_escalated': {
      const taskId = String(payload.task_id ?? '');
      const taskLink = slug && taskId
        ? <Link to={`/orgs/${slug}/tasks/${taskId}`} className="underline">{taskId}</Link>
        : taskId;
      const reason = payload.reason ? String(payload.reason).slice(0, 240) : null;
      return (
        <>
          task {taskLink} escalated{reason ? ` · ${reason}` : ''}
        </>
      );
    }
    default:
      return tag || JSON.stringify(payload);
  }
}
