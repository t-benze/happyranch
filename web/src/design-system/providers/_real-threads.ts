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
import {
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
  ThreadTailEvent,
} from '@/lib/api/types';
import type {
  ArchiveArgs,
  ComposeArgs,
  ExtendArgs,
  InviteArgs,
  MutationLike,
  QueryLike,
  ResumeArgs,
  SendFollowUpArgs,
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
  return useQuery({
    queryKey: ['thread-messages', slug, threadId],
    queryFn: () => threadsApi.listThreadMessages(slug, threadId as string),
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
          // Full ThreadMessage from replay — append to cache.
          qc.setQueryData<{ messages: ThreadMessage[] }>(
            ['thread-messages', slug, threadId],
            (prev) => {
              if (!prev) return { messages: [ev as ThreadMessage] };
              const have = new Set(prev.messages.map((m) => m.seq));
              if (have.has((ev as ThreadMessage).seq)) return prev;
              return {
                messages: [...prev.messages, ev as ThreadMessage].sort(
                  (a, b) => a.seq - b.seq,
                ),
              };
            },
          );
        } else {
          // Preview or invocation-lifecycle event — invalidate to refetch the
          // canonical rows (responder_status, incl. queued/working, lives there).
          qc.invalidateQueries({ queryKey: ['thread-messages', slug, threadId] });
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

function useExtendCap(threadId: string): MutationLike<
  ExtendArgs,
  Awaited<ReturnType<typeof threadsApi.extendThreadCap>>
> {
  const slug = useRealOrgSlug();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ExtendArgs) =>
      threadsApi.extendThreadCap(slug, threadId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thread', slug, threadId] });
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

// ---------------------------------------------------------------------------
// Exposed surface
// ---------------------------------------------------------------------------

export const realThreadsApi: ThreadsApi = {
  useThreadsList,
  useThread,
  useThreadMessages,
  useThreadsInboxSSE,
  useThreadTailSSE,
  useComposeThread,
  useSendFollowUp,
  useInviteAgent,
  useArchiveThread,
  useResumeThread,
  useExtendCap,
  useAbortReplies,
};
