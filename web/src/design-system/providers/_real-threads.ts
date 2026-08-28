/**
 * Real (daemon-backed) implementation of `ThreadsApi`.
 *
 * Private to the providers folder — compositions never import this file.
 * They go through `@/hooks/threads.ts`, which reads `useData()`.
 *
 * The bodies here are the same TanStack Query hooks that previously lived in
 * `src/features/threads/hooks.ts`. The only change is that the `slug` is read
 * from `useRealOrgSlug()` (URL via react-router) instead of being passed as
 * an argument — that's how the public hook surface stays provider-agnostic.
 */
import type { InfiniteData } from '@tanstack/react-query';
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { useParams } from 'react-router-dom';
import { subscribeSSE, threads as threadsApi } from '@/lib/api';
import type {
  ThreadInboxEvent,
  ThreadMessage,
  ThreadMessagesPage,
  ThreadRecord,
  ThreadTailEvent,
} from '@/lib/api/types';
import type {
  ArchiveArgs,
  ComposeArgs,
  InviteArgs,
  MutationLike,
  QueryLike,
  RemoveParticipantArgs,
  RenameThreadArgs,
  ResumeArgs,
  SendFollowUpArgs,
  SetThreadPinArgs,
  ThreadsApi,
} from './DataContext';

/**
 * Read the active org slug from the URL.
 *
 * AppProvider mounts inside `<BrowserRouter>` so `useParams` resolves the
 * `:slug` segment from `/orgs/:slug/...`. Callers that hit a non-org route
 * get an empty string, which gates the dependent queries via `enabled`.
 */
function useRealOrgSlug(): string {
  const { slug } = useParams<{ slug: string }>();
  return slug ?? '';
}

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

function useThreadsList(
  params?: { status?: string; limit?: number },
): QueryLike<Awaited<ReturnType<typeof threadsApi.listThreads>>> {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['threads', slug, params],
    queryFn: () => threadsApi.listThreads(slug, params),
    enabled: !!slug,
  });
}

function useThread(threadId: string | undefined) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['thread', slug, threadId],
    queryFn: () => threadsApi.getThread(slug, threadId as string),
    enabled: !!slug && !!threadId,
  });
}

function useThreadMessages(threadId: string | undefined) {
  const slug = useRealOrgSlug();
  const q = useInfiniteQuery({
    queryKey: ['thread-messages', slug, threadId],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) =>
      threadsApi.listThreadMessages(slug, threadId as string, {
        since_seq: pageParam ?? 0,
      }),
    getNextPageParam: (last) => (last.has_more ? last.next_since_seq : undefined),
    enabled: !!slug && !!threadId,
  });
  return {
    data: q.data ? { pages: q.data.pages } : undefined,
    isLoading: q.isLoading,
    isError: q.isError,
    error: (q.error as Error | null) ?? null,
    fetchNextPage: () => q.fetchNextPage(),
    hasNextPage: !!q.hasNextPage,
    isFetchingNextPage: q.isFetchingNextPage,
  };
}

function useThreadTasks(threadId: string | undefined) {
  const slug = useRealOrgSlug();
  return useQuery({
    queryKey: ['thread-tasks', slug, threadId],
    queryFn: () => threadsApi.listThreadTasks(slug, threadId as string),
    enabled: !!slug && !!threadId,
  });
}

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------

function useThreadsInboxSSE(): void {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  useEffect(() => {
    if (!slug) return;
    const ctl = new AbortController();
    subscribeSSE<ThreadInboxEvent>(threadsApi.threadInboxEventsPath(slug), {
      signal: ctl.signal,
      onMessage: () => {
        qc.invalidateQueries({ queryKey: ['threads', slug] });
      },
    }).catch(() => {
      /* swallow — fetch-event-source already retries transient errors */
    });
    return () => ctl.abort();
  }, [slug, qc]);
}

/**
 * Decide how a thread-tail SSE event affects the messages cache:
 * - 'append'     — a full ThreadMessage from replay (carries `body_markdown`)
 * - 'invalidate' — a seq-bearing preview or invocation-lifecycle event
 *   (`invocation_started` / `invocation_settled`): refetch the canonical
 *   messages so `responder_status` (queued/working/replied/…) updates live.
 *   The live "agent working on a reply" indicator depends on THIS branch
 *   firing for invocation events — keep seq-bearing non-message events routed
 *   here if you refactor the consumer.
 * - 'ignore'     — no seq (e.g. `decline_status` events published with seq=null)
 */
export function classifyTailEvent(
  ev: { seq?: number | null; body_markdown?: unknown },
): 'append' | 'invalidate' | 'ignore' {
  if (ev.seq == null) return 'ignore';
  if ('body_markdown' in ev) return 'append';
  return 'invalidate';
}

function useThreadTailSSE(threadId: string | undefined): void {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  const sinceSeqRef = useRef(0);

  useEffect(() => {
    if (!slug || !threadId) return;
    // Reset since_seq when threadId changes
    sinceSeqRef.current = 0;

    const ctl = new AbortController();
    const { path, query } = threadsApi.threadTailPath(slug, threadId, sinceSeqRef.current);

    subscribeSSE<ThreadTailEvent | ThreadMessage>(path, {
      signal: ctl.signal,
      query,
      onMessage: (ev) => {
        // Replay events are full ThreadMessage objects (kind ∈ {message,
        // decline, system}); live events are ThreadTailEvent previews or
        // invocation-lifecycle events. See classifyTailEvent.
        const action = classifyTailEvent(ev);
        if (action === 'ignore') return;
        sinceSeqRef.current = Math.max(sinceSeqRef.current, ev.seq as number);

        if (action === 'append') {
          // Full ThreadMessage from replay — append to cache (last page).
          qc.setQueryData<InfiniteData<ThreadMessagesPage>>(
            ['thread-messages', slug, threadId],
            (prev) => {
              const msg = ev as ThreadMessage;
              if (!prev || prev.pages.length === 0) {
                return {
                  pages: [{
                    messages: [msg],
                    has_more: false,
                    next_since_seq: msg.seq,
                    reply_delivery: [],
                  }],
                  pageParams: [0],
                };
              }
              // Check if already present across all pages
              for (const page of prev.pages) {
                if (page.messages.some((m) => m.seq === msg.seq)) return prev;
              }
              const lastPage = { ...prev.pages[prev.pages.length - 1] };
              lastPage.messages = [...lastPage.messages, msg].sort(
                (a, b) => a.seq - b.seq,
              );
              lastPage.next_since_seq = lastPage.messages[lastPage.messages.length - 1].seq;
              return {
                pages: [
                  ...prev.pages.slice(0, -1),
                  lastPage,
                ],
                pageParams: prev.pageParams,
              };
            },
          );
        } else {
          // Preview, invocation-lifecycle, or system event — invalidate to
          // refetch the canonical rows (responder_status lives in messages;
          // dispatched tasks live in thread-tasks; the GH-688 Phase 1
          // pair-level reply_delivery projection lives on BOTH the thread
          // detail and the messages page, so invalidate the detail query too
          // to keep the Reply delivery rail + tail live indicator fresh).
          qc.invalidateQueries({ queryKey: ['thread-messages', slug, threadId] });
          qc.invalidateQueries({ queryKey: ['thread-tasks', slug, threadId] });
          qc.invalidateQueries({ queryKey: ['thread', slug, threadId] });
        }
      },
    }).catch(() => {
      /* swallow */
    });
    return () => ctl.abort();
  }, [slug, threadId, qc]);
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

function useComposeThread(): MutationLike<
  ComposeArgs,
  Awaited<ReturnType<typeof threadsApi.composeThread>>
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ComposeArgs) => threadsApi.composeThread(slug, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['threads', slug] });
    },
  });
}

function useSendFollowUp(threadId: string): MutationLike<
  SendFollowUpArgs,
  Awaited<ReturnType<typeof threadsApi.sendThreadFollowUp>>
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SendFollowUpArgs) =>
      threadsApi.sendThreadFollowUp(slug, threadId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thread-messages', slug, threadId] });
      // A follow-up message wakes every other participant — the pair-level
      // reply_delivery projection on the thread detail must refetch too.
      qc.invalidateQueries({ queryKey: ['thread', slug, threadId] });
      qc.invalidateQueries({ queryKey: ['threads', slug] });
    },
  });
}

function useInviteAgent(threadId: string): MutationLike<
  InviteArgs,
  Awaited<ReturnType<typeof threadsApi.inviteToThread>>
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: InviteArgs) =>
      threadsApi.inviteToThread(slug, threadId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thread', slug, threadId] });
    },
  });
}

function useRemoveParticipant(threadId: string): MutationLike<
  RemoveParticipantArgs,
  Awaited<ReturnType<typeof threadsApi.removeParticipantFromThread>>
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RemoveParticipantArgs) =>
      threadsApi.removeParticipantFromThread(slug, threadId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thread', slug, threadId] });
    },
  });
}

function useArchiveThread(threadId: string): MutationLike<
  ArchiveArgs,
  Awaited<ReturnType<typeof threadsApi.archiveThread>>
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ArchiveArgs) =>
      threadsApi.archiveThread(slug, threadId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thread', slug, threadId] });
      qc.invalidateQueries({ queryKey: ['threads', slug] });
    },
  });
}

function useResumeThread(threadId: string): MutationLike<
  ResumeArgs,
  Awaited<ReturnType<typeof threadsApi.resumeThread>>
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => threadsApi.resumeThread(slug, threadId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thread', slug, threadId] });
      qc.invalidateQueries({ queryKey: ['threads', slug] });
    },
  });
}

function useAbortReplies(threadId: string): MutationLike<
  void,
  Awaited<ReturnType<typeof threadsApi.abortReplies>>
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => threadsApi.abortReplies(slug, threadId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thread-messages', slug, threadId] });
      qc.invalidateQueries({ queryKey: ['thread', slug, threadId] });
      qc.invalidateQueries({ queryKey: ['threads', slug] });
    },
  });
}

function useRenameThread(threadId: string): MutationLike<
  RenameThreadArgs,
  Awaited<ReturnType<typeof threadsApi.renameThread>>
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: RenameThreadArgs) =>
      threadsApi.renameThread(slug, threadId, body),
    onSuccess: (data) => {
      // Patch the detail cache in place so the header shows the saved title
      // immediately; the list refetches so rows + pinned ranking stay fresh.
      qc.setQueryData<Awaited<ReturnType<typeof threadsApi.getThread>>>(
        ['thread', slug, threadId],
        (prev) => (prev ? { ...prev, subject: data.subject } : prev),
      );
      qc.invalidateQueries({ queryKey: ['threads', slug] });
    },
  });
}

/**
 * Numeric suffix of a THR-NNN thread id — mirrors the server's
 * `CAST(SUBSTR(t.id, 5) AS INTEGER)` open-list pin-rank key
 * (runtime/infrastructure/database.py::list_threads). THR-10 → 10,
 * THR-2 → 2, THR-003 → 3.
 */
export function numericThreadId(threadId: string): number {
  const n = Number.parseInt(threadId.slice(4), 10);
  return Number.isFinite(n) ? n : 0;
}

/**
 * Client mirror of the server's OPEN-list ordering rule — used ONLY for the
 * optimistic open-list cache reorder in useSetThreadPinned so the UI never
 * diverges from the server contract between click and refetch:
 *
 *   1. pinned threads first;
 *   2. pinned ordered by immutable NUMERIC thread id DESC (THR-10 above
 *      THR-2 — never lexicographic subject/display text, never activity);
 *   3. unpinned in the established ordinary `started_at DESC` order.
 *
 * Archived/status-less/all cached views are NOT passed here — pin has zero
 * presentation effect there, so they keep their ordinary order untouched.
 * Pure: returns a new array, never mutates the input.
 */
export function reorderOpenThreads<
  T extends Pick<ThreadRecord, 'thread_id' | 'pinned' | 'started_at'>,
>(threads: T[]): T[] {
  return [...threads].sort((a, b) => {
    const aPinned = a.pinned ? 0 : 1;
    const bPinned = b.pinned ? 0 : 1;
    if (aPinned !== bPinned) return aPinned - bPinned;
    if (aPinned === 0) {
      return numericThreadId(b.thread_id) - numericThreadId(a.thread_id);
    }
    return b.started_at.localeCompare(a.started_at);
  });
}

function useSetThreadPinned(threadId: string): MutationLike<
  SetThreadPinArgs,
  Awaited<ReturnType<typeof threadsApi.setThreadPinned>>
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: SetThreadPinArgs) =>
      threadsApi.setThreadPinned(slug, threadId, body),
    // Optimistic pin/unpin: flip the flag in every cached list + the detail
    // row before the write lands; roll back to the snapshot on failure.
    onMutate: async (body) => {
      await qc.cancelQueries({ queryKey: ['threads', slug] });
      await qc.cancelQueries({ queryKey: ['thread', slug, threadId] });
      const prevLists = qc.getQueriesData<{ threads: ThreadRecord[] }>({
        queryKey: ['threads', slug],
      });
      const prevDetail = qc.getQueryData<Awaited<ReturnType<typeof threadsApi.getThread>>>(
        ['thread', slug, threadId],
      );
      for (const [key, data] of prevLists) {
        if (!data) continue;
        // TASK-5987 (PR #758 fix-forward): after flipping the flag, cached
        // OPEN lists are reordered immediately to the exact server rule
        // (reorderOpenThreads) so pinning THR-10 while THR-2 is pinned
        // renders THR-10 above THR-2 BEFORE the response/refetch, and
        // unpinning re-inserts the row into ordinary started_at-desc order.
        // Only `params.status === 'open'` variants qualify: archived and
        // status-less/all cached views keep their ordinary order and no pin
        // presentation, exactly like the server. The mutation/audit/persistence
        // wire behavior is unchanged.
        const params = key[2] as { status?: string } | undefined;
        const isOpenList = params?.status === 'open';
        const threads = data.threads.map((t) =>
          t.thread_id === threadId
            ? { ...t, pinned: body.pinned, pinned_at: body.pinned ? t.pinned_at ?? new Date().toISOString() : null } : t,
        );
        qc.setQueryData<{ threads: ThreadRecord[] }>(key, {
          threads: isOpenList ? reorderOpenThreads(threads) : threads,
        });
      }
      if (prevDetail) {
        qc.setQueryData<Awaited<ReturnType<typeof threadsApi.getThread>>>(
          ['thread', slug, threadId],
          { ...prevDetail, pinned: body.pinned, pinned_at: body.pinned ? prevDetail.pinned_at ?? new Date().toISOString() : null },
        );
      }
      return { prevLists, prevDetail };
    },
    onError: (_err, _vars, ctx) => {
      // Roll back every optimistic write.
      if (!ctx) return;
      for (const [key, data] of ctx.prevLists) {
        qc.setQueryData<{ threads: ThreadRecord[] }>(key, data);
      }
      if (ctx.prevDetail) {
        qc.setQueryData<Awaited<ReturnType<typeof threadsApi.getThread>>>(
          ['thread', slug, threadId],
          ctx.prevDetail,
        );
      }
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['threads', slug] });
      qc.invalidateQueries({ queryKey: ['thread', slug, threadId] });
    },
  });
}

// ---------------------------------------------------------------------------
// Exposed surface
// ---------------------------------------------------------------------------

export const realThreadsApi: ThreadsApi = {
  useThreadsList,
  useThread,
  useThreadMessages,
  useThreadTasks,
  useThreadsInboxSSE,
  useThreadTailSSE,
  useComposeThread,
  useSendFollowUp,
  useInviteAgent,
  useRemoveParticipant,
  useArchiveThread,
  useResumeThread,
  useAbortReplies,
  useRenameThread,
  useSetThreadPinned,
};
