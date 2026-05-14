/**
 * TanStack Query hooks for the threads feature.
 *
 * Query-key convention:
 *   ["threads", slug]                         — inbox list
 *   ["thread", slug, threadId]                — single thread metadata + initial msgs
 *   ["thread-messages", slug, threadId]       — message list (advances from tail SSE)
 *
 * SSE hooks subscribe in useEffect and invalidate / append on each event.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { subscribeSSE, threads as threadsApi } from '@/lib/api';
import type {
  ThreadDetailResponse,
  ThreadInboxEvent,
  ThreadMessage,
  ThreadRecord,
  ThreadTailEvent,
} from '@/lib/api/types';

// ---------------------------------------------------------------------------
// Reads
// ---------------------------------------------------------------------------

export function useThreadsList(
  slug: string,
  params?: { status?: string; limit?: number },
): UseQueryResult<{ threads: ThreadRecord[] }> {
  return useQuery({
    queryKey: ['threads', slug, params],
    queryFn: () => threadsApi.listThreads(slug, params),
    enabled: !!slug,
  });
}

export function useThread(
  slug: string,
  threadId: string | undefined,
): UseQueryResult<ThreadDetailResponse> {
  return useQuery({
    queryKey: ['thread', slug, threadId],
    queryFn: () => threadsApi.getThread(slug, threadId as string),
    enabled: !!slug && !!threadId,
  });
}

export function useThreadMessages(
  slug: string,
  threadId: string | undefined,
): UseQueryResult<{ messages: ThreadMessage[] }> {
  return useQuery({
    queryKey: ['thread-messages', slug, threadId],
    queryFn: () => threadsApi.listThreadMessages(slug, threadId as string),
    enabled: !!slug && !!threadId,
  });
}

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------

export function useThreadsInboxSSE(slug: string): void {
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

export function useThreadTailSSE(
  slug: string,
  threadId: string | undefined,
): void {
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
        // The first batch of events (replay) is full ThreadMessage objects
        // (kind ∈ {message, decline, system}). Subsequent live events are
        // ThreadTailEvent previews. Both carry seq.
        if (ev.seq == null) return;
        sinceSeqRef.current = Math.max(sinceSeqRef.current, ev.seq);

        if ('body_markdown' in ev || 'addressed_to' in ev) {
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
          // Preview from live channel — invalidate to fetch the canonical row.
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
// Mutations (write-path, used in Phase 9 features)
// ---------------------------------------------------------------------------

export function useComposeThread(slug: string): UseMutationResult<
  Awaited<ReturnType<typeof threadsApi.composeThread>>,
  Error,
  Parameters<typeof threadsApi.composeThread>[1]
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) => threadsApi.composeThread(slug, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['threads', slug] });
    },
  });
}

export function useSendFollowUp(
  slug: string,
  threadId: string,
): UseMutationResult<
  Awaited<ReturnType<typeof threadsApi.sendThreadFollowUp>>,
  Error,
  Parameters<typeof threadsApi.sendThreadFollowUp>[2]
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body) => threadsApi.sendThreadFollowUp(slug, threadId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thread-messages', slug, threadId] });
      qc.invalidateQueries({ queryKey: ['threads', slug] });
    },
  });
}

export function useInviteAgent(slug: string, threadId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof threadsApi.inviteToThread>[2]) =>
      threadsApi.inviteToThread(slug, threadId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thread', slug, threadId] });
    },
  });
}

export function useArchiveThread(slug: string, threadId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof threadsApi.archiveThread>[2]) =>
      threadsApi.archiveThread(slug, threadId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thread', slug, threadId] });
      qc.invalidateQueries({ queryKey: ['threads', slug] });
    },
  });
}

export function useAbandonThread(slug: string, threadId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof threadsApi.abandonThread>[2]) =>
      threadsApi.abandonThread(slug, threadId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thread', slug, threadId] });
      qc.invalidateQueries({ queryKey: ['threads', slug] });
    },
  });
}

export function useExtendCap(slug: string, threadId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Parameters<typeof threadsApi.extendThreadCap>[2]) =>
      threadsApi.extendThreadCap(slug, threadId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['thread', slug, threadId] });
    },
  });
}
