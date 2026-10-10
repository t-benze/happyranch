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
import type { InfiniteData, QueryClient } from '@tanstack/react-query';
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  skipToken,
} from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import { subscribeSSE, threads as threadsApi } from '@/lib/api';
import type {
  ThreadInboxEvent,
  ThreadMessage,
  ThreadMessagesPage,
  ThreadRecord,
  ThreadListPage,
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
  ThreadListQueryLike,
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

interface ThreadPageOwner {
  users: number;
  listeners: Set<() => void>;
  runners: Map<() => void, (kind: 'next' | 'refresh') => Promise<void>>;
  generation: number;
  controller: AbortController | null;
  operation: 'next' | 'refresh' | null;
  dirty: boolean;
  failure: 'next' | 'refresh' | null;
  error: Error | null;
}
const threadPageOwners = new WeakMap<QueryClient, Map<string, ThreadPageOwner>>();

/** Shared committed-page ownership for daemon and prototype transports.
 * Refresh pages stay private until the entire prior depth is replaced. */
export function useCommittedThreadPages(
  scope: string,
  queryKey: readonly unknown[],
  load: (cursor: string | null, signal: AbortSignal) => Promise<ThreadListPage>,
  pinKey: readonly unknown[],
): ThreadListQueryLike {
  const qc = useQueryClient();
  const identity = JSON.stringify(queryKey);
  const key = useMemo(() => JSON.parse(identity) as unknown[], [identity]);
  const pinIdentity = JSON.stringify(pinKey);
  const pinnedQueryKey = useMemo(() => JSON.parse(pinIdentity) as unknown[], [pinIdentity]);
  const currentIdentity = useRef(identity);
  currentIdentity.current = identity;
  const owner = useMemo(() => {
    let owners = threadPageOwners.get(qc);
    if (!owners) { owners = new Map(); threadPageOwners.set(qc, owners); }
    let existing = owners.get(identity);
    if (!existing) {
      existing = { users: 0, listeners: new Set(), runners: new Map(), generation: 0, controller: null,
        operation: null, dirty: false, failure: null, error: null };
      owners.set(identity, existing);
    }
    return existing;
  }, [qc, identity]);
  const [, render] = useState(0);
  const notify = useCallback(() => render((n) => n + 1), []);
  const notifyOwners = useCallback(() => owner.listeners.forEach((listener) => listener()), [owner]);
  const q = useQuery<InfiniteData<ThreadListPage, string | null>>({
    queryKey: key, queryFn: skipToken, enabled: false, retry: false,
  });
  const active = useCallback(() => currentIdentity.current === identity, [identity]);
  const paused = useCallback(() => !!qc.getQueryData<number>(pinnedQueryKey), [qc, pinnedQueryKey]);
  const run = useCallback(async (kind: 'next' | 'refresh') => {
    if (!scope || !active() || paused() || owner.failure) return;
    if (owner.operation) {
      if (kind !== 'refresh') return;
      if (owner.operation === 'refresh') {
        owner.dirty = true;
        return;
      }
      // Fence a pending continuation before aborting it; a late success cannot
      // resurrect rows or counts from the generation being replaced.
      owner.generation++;
      owner.controller?.abort();
      owner.operation = null;
      owner.controller = null;
    }
    const previous = qc.getQueryData<InfiniteData<ThreadListPage, string | null>>(key);
    const tail = previous?.pages.at(-1);
    if (kind === 'next' && !tail?.has_more) return;
    const ctl = new AbortController();
    const generation = ++owner.generation;
    owner.controller = ctl;
    owner.operation = kind;
    owner.error = null;
    owner.failure = null;
    notifyOwners();
    const owned = () => owner.users > 0 && generation === owner.generation && !ctl.signal.aborted;
    try {
      const staged: InfiniteData<ThreadListPage, string | null> = kind === 'next'
        ? { pages: [...(previous?.pages ?? [])], pageParams: [...(previous?.pageParams ?? [])] }
        : { pages: [], pageParams: [] };
      let cursor = kind === 'next' ? tail!.next_cursor : null;
      const depth = kind === 'refresh' ? Math.max(1, previous?.pages.length ?? 0) : staged.pages.length + 1;
      const seen = new Set(staged.pageParams.filter((p): p is string => p !== null));
      while (staged.pages.length < depth) {
        const page = await load(cursor, ctl.signal);
        if (!owned()) return;
        if (!Array.isArray(page.threads) || !page.totals || typeof page.has_more !== 'boolean'
            || (page.has_more ? typeof page.next_cursor !== 'string' || !page.next_cursor : page.next_cursor !== null)
            || (page.next_cursor !== null && (seen.has(page.next_cursor) || page.next_cursor === cursor))) {
          throw new Error('invalid_thread_page');
        }
        if (cursor !== null) seen.add(cursor);
        staged.pageParams.push(cursor);
        staged.pages.push(page);
        if (!page.has_more) break;
        cursor = page.next_cursor;
      }
      if (owned() && !paused() && !owner.dirty) qc.setQueryData(key, staged);
    } catch (error) {
      if (owned()) {
        owner.failure = kind;
        owner.error = error instanceof Error ? error : new Error(String(error));
      }
    } finally {
      if (owned()) {
        owner.operation = null;
        owner.controller = null;
        const dirty = owner.dirty;
        owner.dirty = false;
        notifyOwners();
        if (dirty && !owner.failure && !paused()) void owner.runners.values().next().value?.('refresh');
      }
    }
  }, [scope, owner, qc, key, load, notifyOwners, active, paused]);
  useEffect(() => {
    owner.users++;
    owner.listeners.add(notify);
    owner.runners.set(notify, run);
    if (scope && owner.users === 1 && !owner.operation) {
      owner.failure = null;
      void run('refresh');
    }
    const leader = () => owner.listeners.values().next().value === notify;
    const unsubscribe = qc.getQueryCache().subscribe((event) => {
      if (event.type !== 'updated' || !leader()) return;
      if (JSON.stringify(event.query.queryKey) === identity && event.action.type === 'invalidate') {
        void run('refresh');
      }
      if (JSON.stringify(event.query.queryKey) === pinIdentity) {
        if (paused()) {
          // Pin owns a generation fence before its optimistic cache writes.
          owner.generation++;
          owner.controller?.abort();
          owner.controller = null;
          owner.operation = null;
          owner.dirty = true;
          owner.failure = null;
          owner.error = null;
          notifyOwners();
        } else if (owner.dirty) {
          owner.dirty = false;
          void run('refresh');
        }
      }
    });
    const refresh = () => { if (leader()) void run('refresh'); };
    window.addEventListener('focus', refresh);
    window.addEventListener('online', refresh);
    return () => {
      unsubscribe();
      window.removeEventListener('focus', refresh);
      window.removeEventListener('online', refresh);
      owner.users--;
      owner.listeners.delete(notify);
      owner.runners.delete(notify);
      if (owner.users === 0) {
        owner.generation++;
        owner.controller?.abort();
        owner.controller = null;
        owner.operation = null;
        owner.dirty = false;
      }
    };
  }, [scope, identity, owner, qc, key, run, paused, pinIdentity, notify, notifyOwners]);
  return {
    data: q.data,
    isLoading: !q.data && !owner.error,
    isError: !!owner.error, error: owner.error,
    hasNextPage: !!q.data?.pages.at(-1)?.has_more,
    isFetchingNextPage: owner.operation === 'next',
    isRefreshing: owner.operation === 'refresh' && !!q.data,
    isStale: !!owner.error && !!q.data,
    fetchNextPage: () => run('next'),
    refresh: () => { owner.failure = null; return run('refresh'); },
    retry: () => {
      const kind = owner.failure ?? 'refresh';
      owner.failure = null;
      return run(kind);
    },
  };
}

function useThreadsInfiniteList(status?: 'open' | 'archived'): ThreadListQueryLike {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  const load = useCallback((cursor: string | null, signal: AbortSignal) =>
    threadsApi.listThreads(slug, { status, page_size: 50, cursor }, signal), [slug, status]);
  const pages = useCommittedThreadPages(slug, ['threads', slug, { status, page_size: 50 }], load, ['thread-list-pin', slug]);
  return {
    ...pages,
    retry: () => {
      // Preserve the list retry's org-wide cache reconciliation. The failed
      // owner stays latched during invalidation, then retries its own operation.
      void qc.invalidateQueries({ queryKey: ['threads', slug] });
      return pages.retry();
    },
  };
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
      onOpen: () => {
        qc.invalidateQueries({ queryKey: ['threads', slug] });
      },
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
  // A new-thread submission captures its destination org at first submit (see
  // NewThreadDialog). The destination rides beside the payload so a rerender
  // after an org switch cannot retarget the in-flight compose; it is stripped
  // before the request body is built. Mirrors useSendFollowUp.
  const destinationOf = (variables: ComposeArgs): { slug: string } | undefined =>
    (variables as ComposeArgs & { destination?: { slug: string } }).destination;
  return useMutation({
    mutationFn: (variables: ComposeArgs) => {
      const destination = destinationOf(variables);
      const { destination: _destination, ...body } = variables as ComposeArgs & {
        destination?: { slug: string };
      };
      return threadsApi.composeThread(destination?.slug ?? slug, body);
    },
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ['threads', destinationOf(variables)?.slug ?? slug] });
    },
  });
}

function useSendFollowUp(threadId: string): MutationLike<
  SendFollowUpArgs,
  Awaited<ReturnType<typeof threadsApi.sendThreadFollowUp>>
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  // A submission captures its destination at first submit (see ThreadsPage).
  // The destination rides beside the payload so a rerender after a thread/org
  // switch cannot retarget an in-flight send to the new view; it is stripped
  // before the request body is built.
  type Destination = { slug: string; threadId: string };
  const destinationOf = (variables: SendFollowUpArgs): Destination | undefined =>
    (variables as SendFollowUpArgs & { destination?: Destination }).destination;
  return useMutation({
    mutationFn: (variables: SendFollowUpArgs) => {
      const destination = destinationOf(variables);
      const { destination: _destination, ...body } = variables as SendFollowUpArgs & {
        destination?: Destination;
      };
      return threadsApi.sendThreadFollowUp(
        destination?.slug ?? slug,
        destination?.threadId ?? threadId,
        body,
      );
    },
    onSuccess: (_data, variables) => {
      const destination = destinationOf(variables);
      const targetSlug = destination?.slug ?? slug;
      const targetThreadId = destination?.threadId ?? threadId;
      qc.invalidateQueries({ queryKey: ['thread-messages', targetSlug, targetThreadId] });
      // A follow-up message wakes every other participant — the pair-level
      // reply_delivery projection on the thread detail must refetch too.
      qc.invalidateQueries({ queryKey: ['thread', targetSlug, targetThreadId] });
      qc.invalidateQueries({ queryKey: ['threads', targetSlug] });
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
      qc.invalidateQueries({ queryKey: ['threads', slug] });
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
      qc.invalidateQueries({ queryKey: ['threads', slug] });
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
 * Client mirror of the server's OPEN-list ordering rule — used for loaded
 * list projection and optimistic cache reorder in useSetThreadPinned so the UI never
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
      const delta = numericThreadId(b.thread_id) - numericThreadId(a.thread_id);
      if (delta) return delta;
    }
    if (a.started_at !== b.started_at) return a.started_at > b.started_at ? -1 : 1;
    return a.thread_id === b.thread_id ? 0 : a.thread_id > b.thread_id ? -1 : 1;
  });
}

function useSetThreadPinned(threadId: string): MutationLike<
  SetThreadPinArgs,
  Awaited<ReturnType<typeof threadsApi.setThreadPinned>>
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  type ListCache = { threads: ThreadRecord[] } | InfiniteData<ThreadListPage, string | null>;
  const mutation = useMutation({
    mutationFn: (variables: { body: SetThreadPinArgs; slug: string; threadId: string }) =>
      threadsApi.setThreadPinned(variables.slug, variables.threadId, variables.body),
    onMutate: async ({ body, slug, threadId }) => {
      const pinKey = ['thread-list-pin', slug];
      qc.setQueryData<number>(pinKey, (n) => (n ?? 0) + 1);
      await qc.cancelQueries({ queryKey: ['threads', slug] });
      await qc.cancelQueries({ queryKey: ['thread', slug, threadId] });
      const prevLists = qc.getQueriesData<ListCache>({ queryKey: ['threads', slug] });
      const detailKey = ['thread', slug, threadId];
      const prevDetail = qc.getQueryData<Awaited<ReturnType<typeof threadsApi.getThread>>>(detailKey);
      const writes = prevLists.map(([key, data]) => {
        if (!data) return { key, before: data, after: data };
        const params = key[2] as { status?: string } | undefined;
        const flip = (t: ThreadRecord) => t.thread_id === threadId
          ? { ...t, pinned: body.pinned, pinned_at: body.pinned ? t.pinned_at ?? new Date().toISOString() : null } : t;
        let after: ListCache;
        if ('pages' in data) {
          const rows = data.pages.flatMap((page) => page.threads).map(flip);
          const ordered = params?.status === 'open' ? reorderOpenThreads(rows) : rows;
          let offset = 0;
          after = { ...data, pages: data.pages.map((page) => {
            const threads = ordered.slice(offset, offset + page.threads.length);
            offset += page.threads.length;
            return { ...page, threads };
          }) };
        } else {
          const rows = data.threads.map(flip);
          after = { ...data, threads: params?.status === 'open' ? reorderOpenThreads(rows) : rows };
        }
        const applied = qc.setQueryData(key, after);
        return { key, before: data, after: applied };
      });
      const nextDetail = prevDetail ? { ...prevDetail, pinned: body.pinned, pinned_at: body.pinned ? prevDetail.pinned_at ?? new Date().toISOString() : null } : undefined;
      const appliedDetail = nextDetail ? qc.setQueryData(detailKey, nextDetail) : undefined;
      return { writes, prevDetail, nextDetail: appliedDetail, detailKey, pinKey, slug };
    },
    onError: (_err, _vars, ctx) => {
      if (!ctx) return;
      for (const { key, before, after } of ctx.writes) {
        if (qc.getQueryData(key) === after) qc.setQueryData(key, before);
      }
      if (ctx.prevDetail && qc.getQueryData(ctx.detailKey) === ctx.nextDetail) qc.setQueryData(ctx.detailKey, ctx.prevDetail);
    },
    onSettled: (_data, _error, _vars, ctx) => {
      if (!ctx) return;
      qc.invalidateQueries({ queryKey: ['threads', ctx.slug] });
      qc.invalidateQueries({ queryKey: ctx.detailKey });
      qc.setQueryData<number>(ctx.pinKey, (n) => Math.max(0, (n ?? 1) - 1));
    },
  });
  return { mutateAsync: (body) => mutation.mutateAsync({ body, slug, threadId }), isPending: mutation.isPending };
}

// ---------------------------------------------------------------------------
// Exposed surface
// ---------------------------------------------------------------------------

export const realThreadsApi: ThreadsApi = {
  useThreadsInfiniteList,
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
